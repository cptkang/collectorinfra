"""업무 서비스 관리자 동작 — 구성원 키 조립 · 후보 수락 · APM 대조 · 게이트웨이 업무 매핑 사본.

plans/215 §3.6 · §3.7 · §5.1 ②③ · D-368.

관리 API(`src/api/routes/admin_biz_service.py`)가 부른다(API 프로세스 — D-049 PG 기록자). 라우트가
두꺼워지지 않게 여러 저장소 호출을 묶는 판단만 여기 둔다. 인가·입력 형식·PII 검사·감사는 라우트 몫.

- **구성원 키**: 관리자 입력(종류별 칸) → 정규 키(`biz_service_keys`)와 표시 칸. SMS 표시 칸은 210
  「현재 스냅샷」(`sms_inventory`)에 그 서버가 있으면 스냅샷 값을 쓴다(관측 DB 조회 0).
- **후보 수락**: member = 그 서비스 구성원(`origin=manual`·`confirmed`·`evidence.aux`에 근거 보존 —
  수용 30) · merge = 병합(후보의 다른 서비스 → 후보 서비스) · apm_unlinked = 수락 불가(210 관리
  탭에서 매핑을 만든다 — Q12). 거절은 저장소 상태 변경만이라 라우트가 직접 한다.
- **APM 대조**(수용 18): 서비스 표시명으로 게이트웨이 `apm_instance_map(business=…)`를 1회 부른다
  (`owner=biz_admin`). 해석에만 있는 인스턴스는 후보(`kind=member` · 근거 `gateway_business`)로만
  남기고 구성원은 바꾸지 않는다. 응답은 차이(해석에만 · 구성원에만 · 일치).
- **게이트웨이 업무 매핑 사본**(Q17): 확정 APM 인스턴스 구성원 → `business_map` YAML 텍스트
  (미리보기·복사용). 게이트웨이 파일을 쓰지 않는다(D-274 ⑤).

계층: application. LLM 호출 0.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from noise_gate.application.apm_host_map_scan import TABLE_SMS
from noise_gate.application.biz_service import BizServiceError, registry_zone
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, SCOPE_WORKLOAD, match_key_for
from noise_gate.domain.biz_service_keys import (
    KIND_APM_BUSINESS,
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_business_member_key,
    apm_domain_member_key,
    apm_instance_member_key,
    parse_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import text

logger = logging.getLogger(__name__)

#: 게이트웨이 호출 주체 · 도구 · 후보 근거.
GATEWAY_OWNER = "biz_admin"
APM_INSTANCE_MAP_TOOL = "apm_instance_map"
BASIS_GATEWAY_BUSINESS = "gateway_business"
#: 게이트웨이 업무명 인자 상한(게이트웨이 `QUERY_MAX`와 같다).
BUSINESS_MAX = 200
#: APM 대조 1회 — 게이트웨이 대기·호출 상한(초).
COMPARE_WAIT_SECONDS = 20.0
COMPARE_CALL_TIMEOUT = 30.0
#: APM 대조가 한 번에 남기는 후보 상한(저장소 일괄 상한과 같다).
COMPARE_MAX_CANDIDATES = 200
_LIVE_JOB_STATES = frozenset({"queued", "running"})


class BizServiceAdminError(BizServiceError):
    """관리자 동작 거부 — `code`(응답 사유 코드) · `status`(HTTP 상태)."""

    def __init__(self, code: str, message: str, *, status: int = 400, **detail: Any) -> None:
        """사유 코드·사람이 읽을 사유·HTTP 상태·상세 칸을 싣는다."""
        super().__init__(message, **detail)
        self.code = code
        self.status = status


class _GatewayCaller(Protocol):
    async def call_tool(self, tool_name: str, arguments: dict[str, Any], *,
                        timeout: float | None = None) -> dict[str, Any]: ...


class _SnapshotRows(Protocol):
    async def current_snapshot_run(self, table: str) -> str | None: ...

    async def snapshot_rows(self, table: str) -> list[dict[str, Any]]: ...


class _AdminStore(Protocol):
    async def get_candidate(self, candidate_id: int) -> dict[str, Any] | None: ...

    async def accept_member_candidate(self, candidate_id: int, *,
                                      display: Mapping[str, Any] | None = None,
                                      by: str = "") -> dict[str, Any]: ...

    async def merge_services(self, from_service_id: int, to_service_id: int, *,
                             from_version: int | None = None, to_version: int | None = None,
                             by: str = "", candidate_id: int | None = None) -> dict[str, Any]: ...

    async def insert_member_candidates(self, service_id: int, service_key: str,
                                       items: Any, *, run_id: str | None = None
                                       ) -> dict[str, int]: ...


def _invalid_member(message: str) -> BizServiceAdminError:
    return BizServiceAdminError("invalid_member", message)


# ── 구성원 키 ────────────────────────────────────────────────────────────
def build_member(
    kind: str,
    *,
    db_id: str = "",
    resource_id: str = "",
    source_id: str = "",
    domain_id: str = "",
    scope: str = SCOPE_INSTANCE,
    instance_name: str = "",
    business_id: str = "",
) -> tuple[str, dict[str, Any]]:
    """관리자 입력 → (정규 구성원 키, 키에서 나오는 표시 칸).

    Raises:
        BizServiceAdminError: `invalid_member` — 종류 밖 · 키 조각 누락 · 인스턴스 이름으로 키를
            못 만든다(워크로드는 파드 이름 모양이어야 한다)
    """
    key: str | None
    display: dict[str, Any]
    if kind == KIND_SMS_SERVER:
        key = sms_member_key(db_id, resource_id)
        display = {"db_id": db_id, "resource_id": resource_id}
    elif kind == KIND_APM_INSTANCE:
        if scope not in (SCOPE_INSTANCE, SCOPE_WORKLOAD):
            raise _invalid_member("APM 인스턴스 범위는 instance·workload만 됩니다.")
        key = apm_instance_member_key(source_id, domain_id, scope,
                                      match_key_for(scope, instance_name))
        display = {"source_id": source_id, "domain_id": domain_id,
                   "instance_name": instance_name.strip()}
    elif kind == KIND_APM_DOMAIN:
        key = apm_domain_member_key(source_id, domain_id)
        display = {"source_id": source_id, "domain_id": domain_id}
    elif kind == KIND_APM_BUSINESS:
        key = apm_business_member_key(source_id, domain_id, business_id)
        display = {"source_id": source_id, "domain_id": domain_id}
    else:
        raise _invalid_member(
            "구성원 종류는 sms_server·apm_instance·apm_domain·apm_business만 됩니다.")
    if key is None:
        raise _invalid_member(
            "구성원 키를 만들 수 없는 입력입니다(필수 칸 누락 · 인스턴스 이름 모양).")
    return key, display


class SnapshotDisplay:
    """구성원 표시 칸 — SMS는 210 현재 스냅샷 행(요청마다 1회 읽기) · 존은 레지스트리.

    210 저장소가 없거나(플래그 off) 스냅샷이 없으면 키에서 나오는 칸만 쓴다(`source` = `key`).
    """

    def __init__(self, map_repo: _SnapshotRows | None, *,
                 zone_of: Callable[[str], str] = registry_zone) -> None:
        """210 저장소(없으면 None)와 존 해석 함수를 받는다."""
        self._maps = map_repo
        self._zone_of = zone_of
        self._sms: dict[tuple[str, str], dict[str, Any]] | None = None

    async def _sms_index(self) -> dict[tuple[str, str], dict[str, Any]]:
        if self._sms is None:
            self._sms = {}
            if self._maps is not None and await self._maps.current_snapshot_run(TABLE_SMS):
                for r in await self._maps.snapshot_rows(TABLE_SMS):
                    ref = (text(r.get("db_id")), text(r.get("resource_id")))
                    if all(ref):
                        self._sms[ref] = r
        return self._sms

    async def sms(self, db_id: str, resource_id: str) -> tuple[dict[str, Any], str]:
        """SMS 서버 표시 칸과 출처(`snapshot` · `key`)."""
        out: dict[str, Any] = {"db_id": db_id, "resource_id": resource_id,
                               "zone": self._zone_of(db_id) or None}
        row = (await self._sms_index()).get((db_id, resource_id))
        if row is None:
            return out, "key"
        out.update({c: text(row.get(c)) or None for c in ("server_name", "hostname", "ip")})
        return out, "snapshot"

    async def for_member(self, kind: str, member_key: str,
                         evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """후보 구성원 → 표시 칸(SMS는 스냅샷 · APM은 후보 근거의 `display` 또는 키 조각)."""
        parts = parse_member_key(kind, member_key) or ()
        if kind == KIND_SMS_SERVER and len(parts) == 2:
            shown, _ = await self.sms(parts[0], parts[1])
            return shown
        raw = (evidence or {}).get("display")
        if isinstance(raw, Mapping) and raw:
            return dict(raw)
        if kind == KIND_APM_INSTANCE and len(parts) == 4:
            return {"source_id": parts[0], "domain_id": parts[1], "instance_name": parts[3]}
        if len(parts) >= 2:
            return {"source_id": parts[0], "domain_id": parts[1]}
        return {}


# ── 후보 수락 ────────────────────────────────────────────────────────────
async def accept_candidate(
    repo: _AdminStore, candidate_id: int, *, display: SnapshotDisplay, by: str
) -> dict[str, Any]:
    """후보 1건 수락 → `{kind, …저장소 결과}`.

    - member: 그 서비스 구성원으로(다른 서비스에 있으면 옮긴다 · 한 트랜잭션)
    - merge: 후보의 다른 서비스(`other_service_id`)를 후보 서비스(`service_id`)로 병합
    - apm_unlinked: 수락 불가(`candidate_not_acceptable` — 「APM·SMS 연계」 탭에서 매핑을 만든다)

    Raises:
        BizServiceAdminError: `not_found`(404) · `candidate_closed`(409) ·
            `candidate_not_acceptable`(400) · `candidate_needs_service`(400)
        그 밖 저장소 예외(version·중복·LookupError)는 그대로 올린다.
    """
    cand = await repo.get_candidate(candidate_id)
    if cand is None:
        raise BizServiceAdminError("not_found", "후보를 찾지 못했습니다.", status=404)
    if cand.get("status") != "open":
        raise BizServiceAdminError("candidate_closed", "이미 수락·거절한 후보입니다.", status=409,
                                   status_now=cand.get("status"))
    kind = text(cand.get("kind"))
    if kind == "member":
        if cand.get("service_id") is None:
            raise BizServiceAdminError(
                "candidate_needs_service",
                "아직 없는 서비스로의 후보입니다 — 서비스를 먼저 만든 뒤 구성원으로 추가하세요.")
        evidence = cand.get("evidence")
        shown = await display.for_member(text(cand.get("member_kind")),
                                         text(cand.get("member_key")),
                                         evidence if isinstance(evidence, Mapping) else None)
        out = await repo.accept_member_candidate(candidate_id, display=shown, by=by)
        return {"kind": kind, **out}
    if kind == "merge":
        dst, src = cand.get("service_id"), cand.get("other_service_id")
        if dst is None or src is None:
            raise BizServiceAdminError("candidate_needs_service",
                                       "병합 후보의 두 서비스 중 하나가 없습니다.")
        out = await repo.merge_services(int(src), int(dst), by=by, candidate_id=candidate_id)
        return {"kind": kind, "from_service_id": int(src), "to_service_id": int(dst), **out}
    raise BizServiceAdminError(
        "candidate_not_acceptable",
        "「같은 도메인 · 미연계」 APM 인스턴스는 여기서 수락하지 않습니다 — "
        "「APM·SMS 연계」 관리 탭에서 매핑을 만드세요.")


# ── APM 대조 ─────────────────────────────────────────────────────────────
def _members(service: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    return [m for m in service.get("members") or []
            if isinstance(m, Mapping) and m.get("kind") == kind and m.get("state") == "active"]


def _gateway_rows(env: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """게이트웨이 봉투 → (행, 상태) — 오류·대기는 예외."""
    err = env.get("error")
    if err:
        code = str(err.get("code") or "gateway_error") if isinstance(err, Mapping) else str(err)
        raise BizServiceAdminError("gateway_error", f"게이트웨이가 오류를 돌려줬습니다({code}).",
                                   status=502, gateway_code=code)
    job = env.get("job")
    if isinstance(job, Mapping) and job.get("state") in _LIVE_JOB_STATES:
        raise BizServiceAdminError(
            "gateway_pending",
            "게이트웨이 해석이 아직 끝나지 않았습니다 — 잠시 뒤 다시 대조하세요.",
            status=409)
    rows = [dict(r) for r in env.get("rows") or [] if isinstance(r, Mapping)]
    total = env.get("total_row_count")
    partial = env.get("partial") is True or (
        isinstance(total, int) and not isinstance(total, bool) and total > len(rows))
    return rows, {"status": "partial" if partial else "ok", "rows": len(rows),
                  "total_row_count": total if isinstance(total, int) else None,
                  "partial": partial}


async def apm_compare(
    repo: _AdminStore, caller: _GatewayCaller | None, service: Mapping[str, Any], *, by: str
) -> dict[str, Any]:
    """서비스 표시명으로 게이트웨이 업무명 해석 1회 → 확정 APM 구성원과의 차이 + 후보 적재.

    구성원은 바꾸지 않는다. 해석에만 있는 인스턴스(구성원 인스턴스·워크로드·도메인 어느 것에도
    걸리지 않는 행)만 후보(`member` · 근거 `gateway_business`)로 남긴다(거절한 같은 후보는 다시
    내지 않는다). 결과가 일부만 왔으면(`partial`) 「구성원에만」은 참고용이다.

    Raises:
        BizServiceAdminError: `business_too_long`(400) · `gateway_unavailable`(503) ·
            `gateway_unreachable`(502) · `gateway_error`(502) · `gateway_pending`(409)
    """
    business = text(service.get("display_name"))
    if not business or len(business) > BUSINESS_MAX:
        raise BizServiceAdminError(
            "business_too_long",
            f"서비스 표시명이 비었거나 {BUSINESS_MAX}자를 넘어 업무명으로 해석할 수 없습니다.")
    if caller is None:
        raise BizServiceAdminError(
            "gateway_unavailable",
            "APM 게이트웨이 주소가 설정되지 않았습니다(MCP_SOURCE_ENDPOINTS 「apm」).",
            status=503)
    try:
        env = await caller.call_tool(
            APM_INSTANCE_MAP_TOOL,
            {"business": business, "owner": GATEWAY_OWNER, "wait_seconds": COMPARE_WAIT_SECONDS},
            timeout=COMPARE_CALL_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 — 통신 실패는 사유 코드로(예외 문자열은 로그만)
        logger.warning("업무 서비스 APM 대조 — 게이트웨이 호출 실패: %s", type(exc).__name__)
        raise BizServiceAdminError("gateway_unreachable",
                                   f"게이트웨이 호출에 실패했습니다({type(exc).__name__}).",
                                   status=502) from None
    rows, gateway = _gateway_rows(env if isinstance(env, Mapping) else {})
    inst = {text(m.get("member_key")): m for m in _members(service, KIND_APM_INSTANCE)}
    doms = {text(m.get("member_key")): m for m in _members(service, KIND_APM_DOMAIN)}
    seen: set[str] = set()
    hit_keys: set[str] = set()
    matched: list[dict[str, Any]] = []
    only_gw: list[dict[str, Any]] = []
    for r in rows:
        src, dom, name = text(r.get("source_id")), text(r.get("domain_id")), \
            text(r.get("instance_name"))
        key = apm_instance_member_key(src, dom, SCOPE_INSTANCE, match_key_for(SCOPE_INSTANCE, name))
        if key is None or key in seen:
            continue
        seen.add(key)
        wl = apm_instance_member_key(src, dom, SCOPE_WORKLOAD, match_key_for(SCOPE_WORKLOAD, name))
        dk = apm_domain_member_key(src, dom)
        row = {"source_id": src, "domain_id": dom, "domain_name": text(r.get("domain_name")),
               "instance_name": name, "member_key": key}
        hit = next((k for k in (key, wl) if k and k in inst), None) or (dk if dk in doms else None)
        if hit is None:
            only_gw.append(row)
        else:
            hit_keys.add(hit)
            matched.append({**row, "matched_by": hit})
    only_members = [
        {"member_id": m.get("id"), "kind": m.get("kind"), "member_key": k,
         "instance_name": m.get("instance_name"), "domain_name": m.get("domain_name"),
         "confidence": m.get("confidence")}
        for k, m in sorted({**inst, **doms}.items()) if k not in hit_keys
    ]
    items = [
        {"member_kind": KIND_APM_INSTANCE, "member_key": r["member_key"],
         "signals": [{"basis": BASIS_GATEWAY_BUSINESS, "bundle_key": business, "anchors": 0,
                      "purity": None}],
         "display": {"source_id": r["source_id"], "domain_id": r["domain_id"],
                     "domain_name": r["domain_name"] or None,
                     "instance_name": r["instance_name"] or None}}
        for r in only_gw[:COMPARE_MAX_CANDIDATES]
    ]
    counts = {"inserted": 0, "existing": 0}
    if items:
        counts = await repo.insert_member_candidates(int(service.get("id") or 0),
                                                     text(service.get("service_key")), items)
    logger.info("업무 서비스 APM 대조: service=%s by=%s 해석 %d · 일치 %d · 해석에만 %d · "
                "구성원에만 %d",
                service.get("id"), by, len(seen), len(matched), len(only_gw), len(only_members))
    return {
        "business": business, "gateway": gateway, "matched": matched,
        "only_in_gateway": only_gw, "only_in_members": only_members,
        "candidates": {**counts, "truncated": max(0, len(only_gw) - COMPARE_MAX_CANDIDATES)},
    }


# ── 게이트웨이 업무 매핑 사본 ────────────────────────────────────────────
def gateway_business_map(service: Mapping[str, Any]) -> dict[str, Any]:
    """확정 APM 인스턴스 구성원 → 게이트웨이 `business_map` 항목·YAML 텍스트(쓰기 없음).

    소스마다 항목 1개(`{business, aliases, instances, source_id}`). 게이트웨이는 인스턴스 이름을
    정확 일치로 보므로 instance 범위 확정 구성원만 넣고, 워크로드·도메인·제니퍼 업무·추정 구성원은
    `skipped`에 사유와 함께 둔다.
    """
    import yaml  # type: ignore[import-untyped]

    business = text(service.get("display_name"))
    by_source: dict[str, set[str]] = defaultdict(set)
    skipped: list[dict[str, Any]] = []
    for m in service.get("members") or []:
        if not isinstance(m, Mapping) or m.get("state") != "active" \
                or m.get("kind") == KIND_SMS_SERVER:
            continue
        key, kind = text(m.get("member_key")), text(m.get("kind"))
        parts = parse_member_key(kind, key)
        if kind != KIND_APM_INSTANCE or parts is None:
            reason = "not_instance"
        elif parts[2] != SCOPE_INSTANCE:
            reason = "workload_scope"
        elif m.get("confidence") != "confirmed":
            reason = "not_confirmed"
        else:
            by_source[parts[0]].add(text(m.get("instance_name")) or parts[3])
            continue
        skipped.append({"member_id": m.get("id"), "kind": kind, "member_key": key,
                        "reason": reason})
    entries = [{"business": business, "aliases": [], "instances": sorted(names),
                "source_id": src} for src, names in sorted(by_source.items())]
    body = yaml.safe_dump({"business_map": entries}, allow_unicode=True, sort_keys=False,
                          default_flow_style=False) if entries else ""
    return {"yaml": body, "entries": entries,
            "instance_count": sum(len(e["instances"]) for e in entries), "skipped": skipped}


__all__ = [
    "APM_INSTANCE_MAP_TOOL",
    "BASIS_GATEWAY_BUSINESS",
    "BUSINESS_MAX",
    "GATEWAY_OWNER",
    "BizServiceAdminError",
    "SnapshotDisplay",
    "accept_candidate",
    "apm_compare",
    "build_member",
    "gateway_business_map",
]
