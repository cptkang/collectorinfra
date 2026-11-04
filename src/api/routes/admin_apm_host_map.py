"""관리자 「APM·SMS 연계」 API — 매핑 목록·수동 추가·수정·확인·확인 취소·다시 찾기·삭제 ·
사전 스캔 · 인벤토리 (plans/210 §3.10 · D-362).

`NOISE_APM_HOST_MAP_ENABLED`가 켜진 채 기동했을 때만 등록한다(꺼져 있으면 라우트가 없어 404 — 현행과
같다). 저장소(`app.state.apm_host_map_repo`)·사본(`app.state.apm_host_map_cache`)은 기동 단계가
auth 풀이 있을 때만 둔다 — 없으면 503 `apm_host_map_unavailable`.

- 인가: `require_admin_user` 뒤에서 role 클레임을 한 번 더 명시 검증한다(`admin_host_links` 관례 ·
  서명 검증만으로 끝내지 않는다). 일반 사용자는 장애 진단 화면에서 근거만 본다(I-5).
- 감사: 전 동작(추가·수정·확인·확인 취소·다시 찾기 적용·삭제·스캔 시작)을 `log_admin_event`로
  before/after와 함께 남긴다(거부·실패도 outcome과 함께). 응답에 `audit_logged`를
  싣는다(침묵 금지).
- 쓰기 뒤 Redis 사본을 행 단위로 맞춘다(`put`·`remove` — 실패는 주기 대조가 메운다).
- 수동 추가·대상 변경은 대상마다 `locate_selected_server`로 폴스타 실재를 확인한다(시스템 전 존).
- 다시 찾기(`/{id}/preview`·`/{id}/resolve`)는 현재 스냅샷으로 자동 제안을 만들어 보여 주고,
  `apply`일 때만 자동 반영 규칙(`apply_auto`)으로 저장한다 — 관리자 행(manual·verified·
  disabled)은 미리보기만 · 자동 생성 플래그 off면 적용 거부. 관리자별 분당 10회(Redis 고정
  창 · 오류 시 프로세스 내 폴백).
- 모든 SQL은 저장소의 파라미터 바인딩 고정 SQL뿐이다(관측 DB 조회는 실재 확인의 기존 readonly 경로).
"""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.api.admin_audit import log_admin_event
from src.api.dependencies import is_admin, require_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/apm-host-map")

#: 다시 찾기(미리보기·적용) 관리자별 호출 상한 — 고정 창(초)·횟수.
_RATE_WINDOW_SECONDS = 60
_RESOLVE_RATE_LIMIT = 10
_RATE_KEY_PREFIX = "apmhostmaprate:"
#: 본문 대상 수 하드 상한(설정 상한 `NOISE_APM_HOST_MAP_MAX_TARGETS`은 핸들러가 따로 본다).
_TARGETS_HARD_MAX = 50
#: 인벤토리 종류 → 스냅샷 표.
_INVENTORY_TABLES: dict[str, str] = {
    "apm": "apm_inventory",
    "sms": "sms_inventory",
    "itam": "itam_snapshot_server",
}
_DB_ID_PATTERN = r"^[A-Za-z0-9_]*$"
#: APM 소스 id — 게이트웨이 설정 규칙(`apm_gateway.domain.sources` 소문자 슬러그)과 같다.
_SOURCE_ID_PATTERN = r"^[a-z][a-z0-9_]{0,15}$"
#: 제니퍼 도메인 id(sid) — 음이 아닌 정수 문자열.
_DOMAIN_ID_PATTERN = r"^\d{1,19}$"
_RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


# ── 오류 · 인가 ─────────────────────────────────────────────────────────
def _error(status_code: int, code: str, reason: str, **extra: Any) -> HTTPException:
    """구조화 오류 `{code, reason, …}`."""
    return HTTPException(status_code=status_code, detail={"code": code, "reason": reason, **extra})


def _store_failed(action: str, exc: BaseException) -> HTTPException:
    return _error(
        503,
        "apm_host_map_store_failed",
        f"연계 저장소(PG) {action}에 실패했습니다({type(exc).__name__}). 잠시 뒤 다시 시도하세요.",
    )


def _assert_admin(request: Request, admin: dict[str, Any]) -> None:
    """role 클레임 명시 검증 — 개발 모드(인증 꺼짐)만 예외."""
    if request.app.state.config.auth.enabled and not is_admin(admin):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


def _repo(request: Request) -> Any:
    repo = getattr(request.app.state, "apm_host_map_repo", None)
    if repo is None:
        raise _error(
            503,
            "apm_host_map_unavailable",
            "연계 저장소(PG)를 쓸 수 없습니다 — 내부 PG(auth 풀) 없이 기동했거나 "
            "기동 단계가 실패했습니다.",
        )
    return repo


def _cache(request: Request) -> Any:
    return getattr(request.app.state, "apm_host_map_cache", None)


def _ng(request: Request) -> Any:
    return getattr(request.app.state.config, "noise_gate", None)


def _auto_enabled(request: Request) -> bool:
    return getattr(_ng(request), "apm_host_map_auto_enabled", False) is True


def _max_targets(request: Request) -> int:
    from noise_gate.domain.apm_host_map import DEFAULT_MAX_TARGETS

    try:
        return max(1, int(getattr(_ng(request), "apm_host_map_max_targets", DEFAULT_MAX_TARGETS)))
    except (TypeError, ValueError):
        return DEFAULT_MAX_TARGETS


def _retry_hours(request: Request) -> int:
    try:
        return int(getattr(_ng(request), "apm_host_map_retry_hours", 24))
    except (TypeError, ValueError):
        return 24


# ── 감사 ────────────────────────────────────────────────────────────────
def _audit_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """감사 before/after 스냅샷 — 키·상태·대상 동일성·메모·version."""
    if not row:
        return None
    return {
        "id": row.get("id"),
        "source_id": row.get("source_id"),
        "domain_id": row.get("domain_id"),
        "scope": row.get("scope"),
        "match_key": row.get("match_key"),
        "origin": row.get("origin"),
        "basis": row.get("basis"),
        "state": row.get("state"),
        "verified": row.get("verified"),
        "targets": [
            {"db_id": t.get("db_id"), "server_name": t.get("server_name")}
            for t in (row.get("targets") or [])
            if isinstance(t, Mapping)
        ],
        "note": row.get("note"),
        "version": row.get("version"),
    }


async def _audit(
    request: Request,
    admin: dict[str, Any],
    action: str,
    outcome: str,
    *,
    before: Any = None,
    after: Any = None,
    **fields: Any,
) -> bool:
    """감사 1건(`ADMIN_ACTION` · `{action, outcome, before, after, …}`) → 기록 성공 여부."""
    from src.domain.audit import AuditEvent

    try:
        return await log_admin_event(
            request,
            admin.get("sub"),
            AuditEvent.ADMIN_ACTION.value,
            {"action": action, "outcome": outcome, "before": before, "after": after, **fields},
        )
    except Exception:  # noqa: BLE001 — 감사 실패가 응답을 막지 않는다(사유는 로그 · 응답 audit_logged)
        logger.warning("APM·SMS 연계 관리 감사 기록 실패: %s", action, exc_info=True)
        return False


async def _audit_rejected(
    request: Request, admin: dict[str, Any], action: str, exc: HTTPException, **fields: Any
) -> None:
    """검증 거부(4xx → `denied`)·확인 실패(5xx → `failed`) 감사 — `reason`은 응답 오류 코드."""
    detail = exc.detail if isinstance(exc.detail, Mapping) else {}
    await _audit(
        request,
        admin,
        action,
        "denied" if exc.status_code < 500 else "failed",
        reason=str(detail.get("code") or exc.status_code),
        **fields,
    )


# ── 본문 ────────────────────────────────────────────────────────────────
class TargetIn(BaseModel):
    """대상 서버 — `db_id`(비우면 전 존에서 찾는다) + 서버명 또는 hostname."""

    db_id: str = Field("", max_length=64, pattern=_DB_ID_PATTERN)
    server_name: str = Field("", max_length=255)
    hostname: str = Field("", max_length=255)


class MapCreate(BaseModel):
    """수동 추가 — 키(소스·도메인·범위·인스턴스 이름) + 대상 + 상태(active·disabled) + 메모."""

    source_id: str = Field(..., min_length=1, max_length=64, pattern=_SOURCE_ID_PATTERN)
    domain_id: str = Field(..., min_length=1, max_length=64, pattern=_DOMAIN_ID_PATTERN)
    scope: Literal["instance", "workload", "domain"]
    instance_name: str = Field(
        "", max_length=255, description="instance·workload 범위의 인스턴스 이름"
    )
    domain_name: str = Field("", max_length=255)
    targets: list[TargetIn] = Field(default_factory=list, max_length=_TARGETS_HARD_MAX)
    state: Literal["active", "disabled"] = "active"
    note: str = Field("", max_length=1000)


class MapUpdate(BaseModel):
    """수정 — 준 칸만 바꾼다. `version`은 목록에서 본 값(다르면 409 `version_conflict`)."""

    version: int = Field(..., ge=1)
    targets: list[TargetIn] | None = Field(None, max_length=_TARGETS_HARD_MAX)
    state: Literal["active", "disabled"] | None = None
    note: str | None = Field(None, max_length=1000)


class VersionBody(BaseModel):
    """확인·삭제 본문."""

    version: int = Field(..., ge=1)


class ResolveBody(BaseModel):
    """다시 찾기 — `apply=true`일 때만 저장한다(`version` 필수)."""

    apply: bool = False
    version: int | None = Field(None, ge=1)


# ── 공통 헬퍼 ───────────────────────────────────────────────────────────
async def _get_or_404(repo: Any, map_id: int) -> dict[str, Any]:
    try:
        row = await repo.get(map_id)
    except Exception as exc:  # noqa: BLE001 — DB 오류는 사유와 함께 503
        logger.warning("APM·SMS 연계 매핑 조회 실패: id=%s", map_id, exc_info=True)
        raise _store_failed("조회", exc) from None
    if row is None:
        raise _error(404, "not_found", "연계 매핑을 찾지 못했습니다.")
    return dict(row)


def _invalid_key() -> HTTPException:
    return _error(
        400,
        "invalid_key",
        "매핑 키로 쓸 수 없는 소스·도메인·범위·인스턴스 이름입니다"
        "(워크로드 범위는 파드 이름이 필요합니다).",
    )


def _version_conflict(current: int) -> HTTPException:
    return _error(
        409,
        "version_conflict",
        "목록을 본 뒤 다른 변경이 먼저 저장됐습니다. 목록을 새로 고친 뒤 다시 시도하세요.",
        current_version=current,
    )


def _review_reasons(row: Mapping[str, Any]) -> list[str]:
    """「검토 필요」 사유 — ambiguous·failed · 변경됨 · scan_suggests · target_gone.

    저장소의 `review_only` 조건과 같다.
    """
    out: list[str] = []
    if row.get("state") in ("ambiguous", "failed"):
        out.append(str(row.get("state")))
    raw = row.get("evidence")
    evidence: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    if evidence.get("changed"):
        out.append("changed")
    if evidence.get("scan_suggests"):
        out.append("scan_suggests")
    if any(isinstance(t, Mapping) and t.get("target_gone") for t in row.get("targets") or []):
        out.append("target_gone")
    return out


def _view(row: Mapping[str, Any]) -> dict[str, Any]:
    """응답 행 — 저장소 행 + `review_reasons` · `evidence_text`(진단 근거 문장과 같은 규칙)."""
    from noise_gate.domain.apm_host_map import evidence_text

    out = dict(row)
    out["review_reasons"] = _review_reasons(row)
    out["evidence_text"] = evidence_text(
        str(row.get("origin") or ""),
        str(row.get("basis") or ""),
        verified=row.get("verified") is True,
    )
    return out


async def _cache_put(request: Request, row: Mapping[str, Any] | None) -> None:
    cache = _cache(request)
    if cache is not None and row:
        await cache.put(row)


async def _locate_targets(
    request: Request,
    targets: Sequence[TargetIn],
    *,
    keep: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """대상마다 폴스타 실재 확인 → 저장용 대상 목록.

    `keep`(현재 행 대상)과 같은 대상은 다시 묻지 않는다.

    Raises:
        HTTPException: 형식 400 · 존 밖 400 · 없음 400 · 동명 여럿 409 · 확인 실패 502
    """
    from noise_gate.application import host_candidates as hc
    from noise_gate.domain.apm_host_map import make_target, target_identity
    from noise_gate.domain.host_link import valid_server_text

    config = request.app.state.config
    kept = {target_identity(t): dict(t) for t in keep if isinstance(t, Mapping)}
    zones: list[str] | None = None
    out: list[dict[str, Any]] = []
    for t in targets:
        db_id = t.db_id.strip()
        server_name = t.server_name.strip()
        hostname = t.hostname.strip()
        if not (server_name or hostname):
            raise _error(400, "invalid_target", "대상마다 서버명 또는 hostname이 필요합니다.")
        if not all(valid_server_text(v) for v in (server_name, hostname) if v):
            raise _error(
                400,
                "invalid_target",
                "서버명·hostname에는 영문·숫자와 . _ - 만 쓸 수 있습니다.",
            )
        same = kept.get(target_identity({"db_id": db_id, "server_name": server_name}))
        if db_id and server_name and same is not None:
            out.append(same)
            continue
        if zones is None:
            zones = list(hc.node_lookup_db_ids(config))
        if db_id and db_id not in zones:
            raise _error(400, "invalid_target_db", f"폴스타 활성 소스가 아닙니다: {db_id}")
        try:
            located = await hc.locate_selected_server(
                config,
                db_ids=[db_id] if db_id else zones,
                server_name=server_name or None,
                hostname=hostname or None,
            )
        except ValueError:
            raise _error(
                400,
                "invalid_target",
                "서버명·hostname에는 영문·숫자와 . _ - 만 쓸 수 있습니다.",
            ) from None
        except Exception as exc:  # noqa: BLE001 — 확인 실패는 「확인하지 못함」으로 거부
            logger.warning("연계 대상 실재 확인 실패: db_id=%s", db_id, exc_info=True)
            raise _error(
                502,
                "verify_failed",
                f"서버 존재를 확인하지 못했습니다({type(exc).__name__}).",
            ) from None
        name = server_name or hostname
        if located.get("ambiguous"):
            raise _error(
                409,
                "server_ambiguous",
                f"같은 이름의 폴스타 서버가 여러 대(또는 여러 소스)에 있습니다: {name} — "
                "소스를 지정하세요.",
            )
        row = located.get("row")
        if row is None:
            unchecked = [str(u.get("db_id")) for u in located.get("unchecked") or []]
            if unchecked:
                raise _error(
                    502,
                    "verify_failed",
                    "일부 소스를 확인하지 못해 서버가 없다고 단정할 수 없습니다"
                    f"({', '.join(unchecked)}).",
                )
            raise _error(400, "server_not_found", f"폴스타에서 서버를 찾지 못했습니다: {name}")
        out.append(
            make_target(
                db_id=str(located.get("db_id") or db_id),
                server_name=str(row.get("server_name") or ""),
                hostname=str(row.get("hostname") or ""),
                ip=str(row.get("ipaddress") or ""),
            )
        )
    return out


def _check_targets(request: Request, state: str, targets: Sequence[Any]) -> None:
    if len(targets) > _max_targets(request):
        raise _error(
            400,
            "too_many_targets",
            f"대상은 {_max_targets(request)}대까지입니다(NOISE_APM_HOST_MAP_MAX_TARGETS).",
        )
    if state == "active" and not targets:
        raise _error(
            400, "targets_required", "사용(active) 매핑에는 대상 서버가 1대 이상 필요합니다."
        )


# ── 호출 상한 ───────────────────────────────────────────────────────────
async def _rate_retry(request: Request, sub: str) -> int | None:
    """다시 찾기 관리자별 고정 창 상한 — 넘으면 남은 초, 아니면 None(이번 호출을 센다).

    사본의 Redis로 프로세스끼리 공유한다(`apmhostmaprate:resolve:{sub}:{창 번호}`). Redis가
    없거나 오류면     프로세스 내 카운터로 센다(상한을 열어 두지 않는다).
    """
    redis = getattr(_cache(request), "redis", None)
    if redis is not None:
        try:
            now = time.time()
            window_no = int(now // _RATE_WINDOW_SECONDS)
            key = f"{_RATE_KEY_PREFIX}resolve:{sub}:{window_no}"
            async with redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, _RATE_WINDOW_SECONDS + 1)
                count, _ = await pipe.execute()
            if int(count) <= _RESOLVE_RATE_LIMIT:
                return None
            return max(1, math.ceil((window_no + 1) * _RATE_WINDOW_SECONDS - now))
        except Exception:  # noqa: BLE001 — Redis 오류는 프로세스 내 카운터로 폴백(열지 않는다)
            logger.warning(
                "연계 다시 찾기 호출 상한 Redis 카운터 실패 — 프로세스 내로 셈", exc_info=True
            )
    return _rate_retry_local(request, sub)


def _rate_retry_local(request: Request, sub: str) -> int | None:
    now = time.monotonic()
    buckets: dict[str, deque[float]] | None = getattr(request.app.state, "apm_host_map_rate", None)
    if buckets is None:
        buckets = {}
        request.app.state.apm_host_map_rate = buckets
    for key in list(buckets):
        stamps = buckets[key]
        while stamps and now - stamps[0] >= _RATE_WINDOW_SECONDS:
            stamps.popleft()
        if not stamps:
            del buckets[key]
    stamps = buckets.setdefault(sub, deque())
    if len(stamps) >= _RESOLVE_RATE_LIMIT:
        return max(1, int(_RATE_WINDOW_SECONDS - (now - stamps[0])) + 1)
    stamps.append(now)
    return None


# ── 다시 찾기 제안 ──────────────────────────────────────────────────────
async def _proposal_for(
    repo: Any, row: Mapping[str, Any], *, max_targets: int
) -> tuple[Any, str | None]:
    """행의 키로 현재 스냅샷 기준 자동 제안 → `(AutoProposal | None, 사유)`.

    instance·workload = 노드 주소·VM 일치(`plan_scan_proposals` · ITAM 제외) · domain = ITAM
    사슬. 사유:
    `apm_map_no_inventory`(스냅샷 없음) · `not_in_inventory`(인벤토리에 그 인스턴스·도메인 없음) ·
    `no_match`(현재 스냅샷으로 잇지 못함) · `itam_config_invalid`.
    """
    from noise_gate.domain.apm_host_map import (
        REASON_NO_INVENTORY,
        SCOPE_DOMAIN,
        build_sms_index,
        match_key_for,
    )
    from noise_gate.domain.apm_host_map_itam import (
        apps_from_rows,
        itam_proposal,
        resolve_itam_chain,
        servers_from_rows,
    )
    from noise_gate.domain.apm_host_map_scan import plan_scan_proposals

    source_id = str(row.get("source_id") or "")
    domain_id = str(row.get("domain_id") or "")
    scope = str(row.get("scope") or "")
    match_key = str(row.get("match_key") or "")
    needed = ["apm_inventory", "sms_inventory"]
    if scope == SCOPE_DOMAIN:
        needed += ["itam_snapshot_app", "itam_snapshot_server"]
    for table in needed:
        if await repo.current_snapshot_run(table) is None:
            return None, REASON_NO_INVENTORY
    domain_rows = [
        r
        for r in await repo.snapshot_rows("apm_inventory", query=domain_id)
        if str(r.get("source_id") or "") == source_id and str(r.get("domain_id") or "") == domain_id
    ]
    if scope != SCOPE_DOMAIN:
        domain_rows_in_scope = [
            r for r in domain_rows if match_key_for(scope, r.get("instance_name")) == match_key
        ]
        if not domain_rows_in_scope:
            return None, "not_in_inventory"
    elif not domain_rows:
        return None, "not_in_inventory"
    by_ip, by_host = build_sms_index(await repo.snapshot_rows("sms_inventory"))
    if scope == SCOPE_DOMAIN:
        from noise_gate.infrastructure.apm_host_map_sql_scanners import (
            ScanSqlError,
            load_itam_lookup,
        )

        try:
            itam_cfg = load_itam_lookup()
        except ScanSqlError:
            return None, "itam_config_invalid"
        domain_name = next(
            (str(r.get("domain_name") or "") for r in domain_rows if r.get("domain_name")),
            str(row.get("domain_name") or ""),
        )
        description = next(
            (
                str(r.get("domain_description") or "")
                for r in domain_rows
                if r.get("domain_description")
            ),
            "",
        )
        result = resolve_itam_chain(
            domain_name=domain_name,
            domain_description=description,
            apps=apps_from_rows(await repo.snapshot_rows("itam_snapshot_app")),
            servers=servers_from_rows(await repo.snapshot_rows("itam_snapshot_server")),
            by_ip=by_ip,
            by_host=by_host,
            generic_terms=itam_cfg.generic_terms,
            max_servers=itam_cfg.max_servers,
            max_targets=max_targets,
        )
        return itam_proposal(
            domain_id=domain_id,
            domain_name=domain_name,
            result=result,
            instance_example=str(domain_rows[0].get("instance_name") or ""),
            extra_evidence={"trigger": "admin_resolve"},
        ), None
    plan = plan_scan_proposals(
        domain_rows,
        by_ip,
        by_host,
        use_node_address=True,
        itam=None,
        max_targets=max_targets,
        evidence={"trigger": "admin_resolve"},
    )
    for proposal in plan.get(source_id, []):
        if proposal.scope == scope and proposal.match_key == match_key:
            return proposal, None
    return None, "no_match"


def _proposal_view(proposal: Any) -> dict[str, Any] | None:
    if proposal is None:
        return None
    return {
        "scope": proposal.scope,
        "match_key": proposal.match_key,
        "basis": proposal.basis,
        "state": proposal.state,
        "targets": [dict(t) for t in proposal.targets],
        "evidence": dict(proposal.evidence),
    }


# ── 매핑 ────────────────────────────────────────────────────────────────
@router.get(
    "",
    summary="APM·SMS 연계 매핑 목록(관리자)",
    description=(
        "소스·상태·출처·근거·검색어 필터와 페이지. `review=true`면 「검토 필요」 행만 반환합니다."
    ),
    tags=["admin"],
)
async def list_maps(
    request: Request,
    source_id: str | None = Query(None, max_length=64),
    state: Literal["active", "ambiguous", "failed", "disabled"] | None = None,
    origin: Literal["auto", "manual"] | None = None,
    basis: Literal["node_address", "vm_exact", "itam", "manual"] | None = None,
    q: str | None = Query(None, max_length=200),
    review: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{rows[], total, limit, offset, max_targets, auto_enabled, scan_available}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    try:
        rows, total = await repo.list_maps(
            source_id=source_id or None,
            state=state,
            origin=origin,
            basis=basis,
            query=q,
            review_only=review,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 매핑 목록 조회 실패", exc_info=True)
        raise _store_failed("조회", exc) from None
    return {
        "rows": [_view(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "max_targets": _max_targets(request),
        "auto_enabled": _auto_enabled(request),
        "scan_available": getattr(request.app.state, "apm_host_map_scan_service", None) is not None,
    }


@router.post(
    "",
    status_code=201,
    summary="APM·SMS 연계 수동 추가(관리자)",
    description=(
        "키(소스·도메인·범위·인스턴스 이름)에 대상 서버를 지정합니다. "
        "대상마다 폴스타 실재를 확인하며, 같은 키가 있으면 409 `exists`입니다. "
        "`state=disabled`는 「연계 없음」(폴스타 조회 생략)입니다."
    ),
    tags=["admin"],
)
async def create_map(
    body: MapCreate, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{map, audit_logged}`."""
    from noise_gate.domain.apm_host_map import ApmHostMapExistsError, match_key_for, valid_key

    _assert_admin(request, admin)
    repo = _repo(request)
    match_key = match_key_for(body.scope, body.instance_name)
    key = {
        "source_id": body.source_id,
        "domain_id": body.domain_id,
        "scope": body.scope,
        "match_key": match_key,
    }
    try:
        if match_key is None or not valid_key(
            body.source_id, body.domain_id, body.scope, match_key
        ):
            raise _invalid_key()
        _check_targets(request, body.state, body.targets)
        targets = await _locate_targets(request, body.targets)
    except HTTPException as exc:
        await _audit_rejected(request, admin, "apm_host_map_create", exc, after=key)
        raise
    try:
        row = await repo.create_manual(
            **key,
            targets=targets,
            state=body.state,
            domain_name=body.domain_name,
            instance_example=body.instance_name.strip(),
            note=body.note,
            by=str(admin.get("sub") or ""),
        )
    except ApmHostMapExistsError as e:
        await _audit(
            request,
            admin,
            "apm_host_map_create",
            "denied",
            after=key,
            reason="exists",
            existing_id=e.existing_id,
        )
        raise _error(
            409,
            "exists",
            "같은 키의 매핑이 이미 있습니다 — 그 행을 수정하세요.",
            existing_id=e.existing_id,
        ) from None
    except ValueError as e:
        logger.info("APM·SMS 연계 수동 추가 키 거부(%s)", type(e).__name__)
        await _audit(
            request, admin, "apm_host_map_create", "denied", after=key, reason="invalid_key"
        )
        raise _invalid_key() from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 수동 추가 실패", exc_info=True)
        await _audit(
            request, admin, "apm_host_map_create", "failed", after=key, reason=type(exc).__name__
        )
        raise _store_failed("저장", exc) from None
    await _cache_put(request, row)
    logged = await _audit(request, admin, "apm_host_map_create", "ok", after=_audit_view(row))
    return {"map": _view(row), "audit_logged": logged}


@router.put(
    "/{map_id}",
    summary="APM·SMS 연계 수정(관리자)",
    description=(
        "대상·상태(active·disabled)·메모를 바꿉니다. "
        "자동 행을 수정하면 관리자 행(origin manual)이 되어 자동 결과가 덮지 않습니다. "
        "새 대상은 폴스타 실재를 확인합니다. version이 다르면 409 `version_conflict`."
    ),
    tags=["admin"],
)
async def update_map(
    map_id: int,
    body: MapUpdate,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{map, audit_logged}`."""
    from noise_gate.domain.apm_host_map import ApmHostMapVersionError

    _assert_admin(request, admin)
    repo = _repo(request)
    before = await _get_or_404(repo, map_id)
    if int(before.get("version") or 0) != body.version:
        await _audit(
            request,
            admin,
            "apm_host_map_update",
            "denied",
            before=_audit_view(before),
            reason="version_conflict",
        )
        raise _version_conflict(int(before.get("version") or 0))
    state = body.state or str(before.get("state") or "")
    targets: list[dict[str, Any]] | None = None
    try:
        if body.targets is not None:
            _check_targets(request, state, body.targets)
            targets = await _locate_targets(
                request, body.targets, keep=before.get("targets") or []
            )
        elif body.state == "active" and not (before.get("targets") or []):
            _check_targets(request, "active", [])
    except HTTPException as exc:
        await _audit_rejected(
            request, admin, "apm_host_map_update", exc, before=_audit_view(before)
        )
        raise
    try:
        row = await repo.update(
            map_id,
            version=body.version,
            targets=targets,
            state=body.state,
            note=body.note,
            by=str(admin.get("sub") or ""),
        )
    except ApmHostMapVersionError as e:
        await _audit(
            request,
            admin,
            "apm_host_map_update",
            "denied",
            before=_audit_view(before),
            reason="version_conflict",
        )
        raise _version_conflict(e.current_version) from None
    except ValueError as e:
        logger.info("APM·SMS 연계 수정 상태 거부: id=%s(%s)", map_id, type(e).__name__)
        await _audit(
            request,
            admin,
            "apm_host_map_update",
            "denied",
            before=_audit_view(before),
            reason="invalid_state",
        )
        raise _error(
            400, "invalid_state", "관리자 수정 상태는 사용(active)·연계 없음(disabled)만 됩니다."
        ) from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 수정 실패: id=%s", map_id, exc_info=True)
        await _audit(
            request,
            admin,
            "apm_host_map_update",
            "failed",
            before=_audit_view(before),
            reason=type(exc).__name__,
        )
        raise _store_failed("저장", exc) from None
    if row is None:
        raise _error(404, "not_found", "연계 매핑을 찾지 못했습니다.")
    await _cache_put(request, row)
    logged = await _audit(
        request,
        admin,
        "apm_host_map_update",
        "ok",
        before=_audit_view(before),
        after=_audit_view(row),
    )
    return {"map": _view(row), "audit_logged": logged}


@router.post(
    "/{map_id}/verify",
    summary="APM·SMS 연계 자동 결과 확인(관리자)",
    description="자동 행을 관리자 확인(verified)으로 올립니다 — 이후 자동 결과가 덮지 않습니다.",
    tags=["admin"],
)
async def verify_map(
    map_id: int,
    body: VersionBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{map, audit_logged}`."""
    from noise_gate.domain.apm_host_map import ORIGIN_AUTO, ApmHostMapVersionError

    _assert_admin(request, admin)
    repo = _repo(request)
    before = await _get_or_404(repo, map_id)
    if before.get("origin") != ORIGIN_AUTO:
        raise _error(
            400, "verify_auto_only", "확인은 자동 행에만 씁니다(관리자 행은 이미 확정입니다)."
        )
    try:
        row = await repo.verify(map_id, version=body.version, by=str(admin.get("sub") or ""))
    except ApmHostMapVersionError as e:
        await _audit(
            request,
            admin,
            "apm_host_map_verify",
            "denied",
            before=_audit_view(before),
            reason="version_conflict",
        )
        raise _version_conflict(e.current_version) from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 확인 실패: id=%s", map_id, exc_info=True)
        await _audit(
            request,
            admin,
            "apm_host_map_verify",
            "failed",
            before=_audit_view(before),
            reason=type(exc).__name__,
        )
        raise _store_failed("저장", exc) from None
    if row is None:
        raise _error(404, "not_found", "연계 매핑을 찾지 못했습니다.")
    await _cache_put(request, row)
    logged = await _audit(
        request,
        admin,
        "apm_host_map_verify",
        "ok",
        before=_audit_view(before),
        after=_audit_view(row),
    )
    return {"map": _view(row), "audit_logged": logged}


@router.post(
    "/{map_id}/unverify",
    summary="APM·SMS 연계 확인 취소(관리자)",
    description=(
        "관리자 확인(verified)한 자동 행을 미확인 자동 행으로 되돌립니다 — 이후 자동 결과가 "
        "다시 갱신하고 정리 대상이 됩니다. 대상·상태·근거 기록은 그대로입니다. "
        "확인한 자동 행이 아니면 400 `unverify_verified_auto_only`."
    ),
    tags=["admin"],
)
async def unverify_map(
    map_id: int,
    body: VersionBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{map, audit_logged}`."""
    from noise_gate.domain.apm_host_map import ORIGIN_AUTO, ApmHostMapVersionError

    _assert_admin(request, admin)
    repo = _repo(request)
    before = await _get_or_404(repo, map_id)
    if before.get("origin") != ORIGIN_AUTO or before.get("verified") is not True:
        exc = _error(
            400,
            "unverify_verified_auto_only",
            "확인 취소는 관리자 확인한 자동 행에만 씁니다"
            "(관리자 등록 행은 수정·삭제로, 미확인 자동 행은 그대로 둡니다).",
        )
        await _audit_rejected(
            request, admin, "apm_host_map_unverify", exc, before=_audit_view(before)
        )
        raise exc
    try:
        row = await repo.unverify(map_id, version=body.version, by=str(admin.get("sub") or ""))
    except ApmHostMapVersionError as e:
        await _audit(
            request,
            admin,
            "apm_host_map_unverify",
            "denied",
            before=_audit_view(before),
            reason="version_conflict",
        )
        raise _version_conflict(e.current_version) from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 확인 취소 실패: id=%s", map_id, exc_info=True)
        await _audit(
            request,
            admin,
            "apm_host_map_unverify",
            "failed",
            before=_audit_view(before),
            reason=type(exc).__name__,
        )
        raise _store_failed("저장", exc) from None
    if row is None:
        raise _error(404, "not_found", "연계 매핑을 찾지 못했습니다.")
    await _cache_put(request, row)
    logged = await _audit(
        request,
        admin,
        "apm_host_map_unverify",
        "ok",
        before=_audit_view(before),
        after=_audit_view(row),
    )
    return {"map": _view(row), "audit_logged": logged}


async def _resolve(
    map_id: int, body: ResolveBody, request: Request, admin: dict[str, Any]
) -> dict[str, Any]:
    """다시 찾기 본체(미리보기·적용 공용)."""
    from noise_gate.domain.apm_host_map import is_admin_record, plan_auto_write

    _assert_admin(request, admin)
    repo = _repo(request)
    retry = await _rate_retry(request, str(admin.get("sub") or ""))
    if retry is not None:
        raise _error(
            429,
            "apm_host_map_rate_limited",
            f"다시 찾기가 너무 잦습니다. {retry}초 뒤에 다시 시도하세요.",
            retry_after_seconds=retry,
        )
    before = await _get_or_404(repo, map_id)
    try:
        proposal, reason = await _proposal_for(repo, before, max_targets=_max_targets(request))
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 다시 찾기 실패: id=%s", map_id, exc_info=True)
        raise _store_failed("스냅샷 조회", exc) from None
    now_iso = datetime.now(UTC).isoformat()
    action = plan_auto_write(before, proposal, now_iso=now_iso).action if proposal else None
    preview_only = is_admin_record(before)
    out: dict[str, Any] = {
        "map": _view(before),
        "proposal": _proposal_view(proposal),
        "reason": reason,
        "action": action,
        "preview_only": preview_only,
        "applied": False,
    }
    if not body.apply:
        return out
    if body.version is None or int(before.get("version") or 0) != body.version:
        raise _version_conflict(int(before.get("version") or 0))
    if preview_only:
        raise _error(
            409,
            "preview_only",
            "관리자 행(관리자 등록·확인·연계 없음)은 다시 찾기 결과를 미리보기로만 "
            "보여 줍니다 — 수정하세요.",
        )
    if not _auto_enabled(request):
        raise _error(
            409,
            "apm_host_map_auto_disabled",
            "자동 생성(NOISE_APM_HOST_MAP_AUTO_ENABLED)이 꺼져 있어 적용할 수 없습니다.",
        )
    if proposal is None:
        raise _error(409, "no_proposal", f"적용할 제안이 없습니다({reason}).")
    try:
        written, row = await repo.apply_auto(
            str(before.get("source_id") or ""),
            proposal,
            retry_hours=_retry_hours(request),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 다시 찾기 적용 실패: id=%s", map_id, exc_info=True)
        await _audit(
            request,
            admin,
            "apm_host_map_resolve",
            "failed",
            before=_audit_view(before),
            reason=type(exc).__name__,
        )
        raise _store_failed("저장", exc) from None
    await _cache_put(request, row)
    out["audit_logged"] = await _audit(
        request,
        admin,
        "apm_host_map_resolve",
        "ok",
        before=_audit_view(before),
        after=_audit_view(row),
        write=written,
    )
    out.update(map=_view(row) if row else out["map"], action=written, applied=True)
    return out


@router.post(
    "/{map_id}/preview",
    summary="APM·SMS 연계 다시 찾기 미리보기(관리자)",
    description=(
        "현재 스냅샷으로 자동 제안을 만들어 보여 줍니다(저장하지 않음 · 관리자별 분당 10회)."
    ),
    tags=["admin"],
)
async def preview_map(
    map_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{map, proposal, reason, action, preview_only, applied:false}`."""
    return await _resolve(map_id, ResolveBody(apply=False, version=None), request, admin)


@router.post(
    "/{map_id}/resolve",
    summary="APM·SMS 연계 다시 찾기(관리자)",
    description=(
        "미리보기와 같은 제안을 만들고 `apply=true`일 때만 자동 반영 규칙으로 저장합니다. "
        "관리자 행은 "
        "미리보기만(409 `preview_only`) · 자동 생성 플래그 off면 409 `apm_host_map_auto_disabled`."
    ),
    tags=["admin"],
)
async def resolve_map(
    map_id: int,
    body: ResolveBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{map, proposal, reason, action, preview_only, applied[, audit_logged]}`."""
    return await _resolve(map_id, body, request, admin)


@router.delete(
    "/{map_id}",
    summary="APM·SMS 연계 삭제(관리자)",
    description="매핑 1건을 지웁니다(사본에서도 뺍니다). version이 다르면 409 `version_conflict`.",
    tags=["admin"],
)
async def delete_map(
    map_id: int,
    body: VersionBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{deleted, audit_logged}`."""
    from noise_gate.domain.apm_host_map import ApmHostMapVersionError

    _assert_admin(request, admin)
    repo = _repo(request)
    before = await _get_or_404(repo, map_id)
    try:
        row = await repo.delete(map_id, version=body.version)
    except ApmHostMapVersionError as e:
        await _audit(
            request,
            admin,
            "apm_host_map_delete",
            "denied",
            before=_audit_view(before),
            reason="version_conflict",
        )
        raise _version_conflict(e.current_version) from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 삭제 실패: id=%s", map_id, exc_info=True)
        await _audit(
            request,
            admin,
            "apm_host_map_delete",
            "failed",
            before=_audit_view(before),
            reason=type(exc).__name__,
        )
        raise _store_failed("삭제", exc) from None
    if row is None:
        raise _error(404, "not_found", "연계 매핑을 찾지 못했습니다.")
    cache = _cache(request)
    if cache is not None:
        await cache.remove(row)
    logged = await _audit(
        request, admin, "apm_host_map_delete", "ok", before=_audit_view(before), after=None
    )
    return {"deleted": _view(row), "audit_logged": logged}


# ── 스캔 ────────────────────────────────────────────────────────────────
@router.post(
    "/scan",
    status_code=202,
    summary="APM·SMS 연계 지금 스캔(관리자)",
    description=(
        "사전 스캔을 백그라운드로 시작하고 `run_id`를 돌려줍니다. 실행 중이면 409 `scan_running`, "
        "자동 생성 플래그 off면 409 `apm_host_map_auto_disabled`입니다."
    ),
    tags=["admin"],
)
async def start_scan(
    request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{run_id, status: running, audit_logged}`."""
    from noise_gate.application.apm_host_map_scan import (
        REASON_AUTO_DISABLED,
        ScanDisabledError,
        ScanRunningError,
    )

    _assert_admin(request, admin)
    repo = _repo(request)
    service = getattr(request.app.state, "apm_host_map_scan_service", None)
    action = "apm_host_map_scan_start"
    before: dict[str, Any] | None = None
    try:
        last = await repo.list_scan_runs(limit=1)
        if last:
            before = {"run_id": last[0].get("run_id"), "status": last[0].get("status")}
    except Exception:  # noqa: BLE001 — 직전 기록은 감사 before일 뿐이다
        logger.warning("APM·SMS 연계 스캔 — 직전 실행 기록 조회 실패", exc_info=True)
    if service is None:
        if not _auto_enabled(request):
            await _audit(
                request, admin, action, "denied", before=before, reason=REASON_AUTO_DISABLED
            )
            raise _error(
                409,
                REASON_AUTO_DISABLED,
                "자동 생성(NOISE_APM_HOST_MAP_AUTO_ENABLED)이 꺼져 있어 스캔할 수 없습니다.",
            )
        await _audit(request, admin, action, "failed", before=before, reason="scan_unavailable")
        raise _error(
            503, "scan_unavailable", "스캔 서비스를 조립하지 못했습니다 — 기동 로그를 확인하세요."
        )
    try:
        run_id = await service.start_scan(requested_by=str(admin.get("sub") or ""))
    except ScanRunningError as e:
        await _audit(
            request,
            admin,
            action,
            "denied",
            before=before,
            reason="scan_running",
            running_run_id=e.run_id,
        )
        raise _error(
            409,
            "scan_running",
            "다른 스캔이 실행 중입니다 — 끝난 뒤 다시 시도하세요.",
            run_id=e.run_id,
        ) from None
    except ScanDisabledError as e:
        logger.info("APM·SMS 연계 스캔 거부(%s)", type(e).__name__)
        await _audit(
            request, admin, action, "denied", before=before, reason=REASON_AUTO_DISABLED
        )
        raise _error(
            409,
            REASON_AUTO_DISABLED,
            "자동 생성(NOISE_APM_HOST_MAP_AUTO_ENABLED)이 꺼져 있어 스캔할 수 없습니다.",
        ) from None
    except Exception as exc:  # noqa: BLE001 — 락(Redis)·실행 기록(PG) 오류
        logger.warning("APM·SMS 연계 스캔 시작 실패", exc_info=True)
        await _audit(request, admin, action, "failed", before=before, reason=type(exc).__name__)
        raise _error(
            503, "scan_start_failed", f"스캔을 시작하지 못했습니다({type(exc).__name__})."
        ) from None
    logged = await _audit(
        request, admin, action, "ok", before=before, after={"run_id": run_id, "status": "running"}
    )
    return {"run_id": run_id, "status": "running", "audit_logged": logged}


@router.get(
    "/scan/runs",
    summary="APM·SMS 연계 스캔 이력(관리자)",
    tags=["admin"],
)
async def list_scan_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{runs[], scan_available, auto_enabled}` — 최근 먼저."""
    _assert_admin(request, admin)
    repo = _repo(request)
    try:
        runs = await repo.list_scan_runs(limit=limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 스캔 이력 조회 실패", exc_info=True)
        raise _store_failed("조회", exc) from None
    return {
        "runs": runs,
        "scan_available": getattr(request.app.state, "apm_host_map_scan_service", None) is not None,
        "auto_enabled": _auto_enabled(request),
    }


@router.get(
    "/scan/runs/{run_id}",
    summary="APM·SMS 연계 스캔 실행 기록(관리자)",
    description="진행 폴링용 — 스캐너별 결과(`scanners`)와 연계 요약(`link_summary`).",
    tags=["admin"],
)
async def get_scan_run(
    request: Request,
    run_id: str,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{run}` — 없으면 404."""
    import re

    _assert_admin(request, admin)
    if not re.match(_RUN_ID_PATTERN, run_id):
        raise _error(400, "invalid_run_id", "실행 id 형식이 아닙니다.")
    repo = _repo(request)
    try:
        run = await repo.get_scan_run(run_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 스캔 실행 기록 조회 실패: %s", run_id, exc_info=True)
        raise _store_failed("조회", exc) from None
    if run is None:
        raise _error(404, "not_found", "스캔 실행 기록을 찾지 못했습니다.")
    return {"run": run}


# ── 인벤토리 ────────────────────────────────────────────────────────────
def _unmapped_filter(kind: str, maps: Sequence[Mapping[str, Any]]) -> Any:
    """「매핑 없음만」 판정 함수 — apm: 그 인스턴스의 instance·workload·domain 키가 모두 없음 ·
    sms: 어느 매핑의 대상도 아님 · itam: hostname이 어느 매핑 대상의 서버명·hostname도 아님."""
    from noise_gate.domain.apm_host_map import SCOPES, match_key_for, target_identity
    from src.domain.host_names import normalize_hostname

    if kind == "apm":
        keys = {
            (
                str(m.get("source_id") or ""),
                str(m.get("domain_id") or ""),
                str(m.get("scope") or ""),
                str(m.get("match_key") or ""),
            )
            for m in maps
        }

        def _apm(row: Mapping[str, Any]) -> bool:
            base = (str(row.get("source_id") or ""), str(row.get("domain_id") or ""))
            for scope in SCOPES:
                key = match_key_for(scope, row.get("instance_name"))
                if key is not None and (*base, scope, key) in keys:
                    return False
            return True

        return _apm
    targets = [t for m in maps for t in (m.get("targets") or []) if isinstance(t, Mapping)]
    if kind == "sms":
        idents = {target_identity(t) for t in targets}
        return lambda row: (
            target_identity({"db_id": row.get("db_id"), "server_name": row.get("server_name")})
            not in idents
        )
    names = {
        normalize_hostname(str(t.get(k) or ""))
        for t in targets
        for k in ("server_name", "hostname")
    }
    names.discard("")
    return lambda row: normalize_hostname(str(row.get("hostname") or "")) not in names


@router.get(
    "/inventory/{kind}",
    summary="APM·SMS 연계 스냅샷 검색(관리자)",
    description=(
        "사전 스캔의 현재 스냅샷(apm = APM 인벤토리 · sms = 폴스타 서버 · "
        "itam = ITAM 서버 원장)을 검색합니다. "
        "`unmapped=true`면 매핑이 없는 행만 — 수동 추가 선택 목록입니다."
    ),
    tags=["admin"],
)
async def inventory(
    request: Request,
    kind: Literal["apm", "sms", "itam"],
    q: str | None = Query(None, max_length=200),
    unmapped: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{kind, run_id, rows[], has_more, limit, offset}`.

    스냅샷이 없으면 `run_id: null` · 빈 목록.
    """
    _assert_admin(request, admin)
    repo = _repo(request)
    table = _INVENTORY_TABLES[kind]
    try:
        run_id = await repo.current_snapshot_run(table)
        if run_id is None:
            rows: list[dict[str, Any]] = []
            has_more = False
        elif unmapped:
            keep = _unmapped_filter(kind, await repo.all_rows())
            matched = [r for r in await repo.snapshot_rows(table, query=q) if keep(r)]
            rows = matched[offset : offset + limit]
            has_more = len(matched) > offset + limit
        else:
            fetched = await repo.snapshot_rows(table, query=q, limit=limit + 1, offset=offset)
            rows = fetched[:limit]
            has_more = len(fetched) > limit
    except Exception as exc:  # noqa: BLE001
        logger.warning("APM·SMS 연계 인벤토리 조회 실패: %s", kind, exc_info=True)
        raise _store_failed("조회", exc) from None
    return {
        "kind": kind,
        "run_id": run_id,
        "rows": rows,
        "has_more": has_more,
        "limit": limit,
        "offset": offset,
    }


__all__ = ["router"]
