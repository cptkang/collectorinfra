"""APM·SMS 연계 매핑 — 키·조회 판정·되적재 병합·스캔 대조·VM 일치·힌트·포트 (plans/210 · D-362).

제니퍼 인스턴스(파드·VM)를 관측 DB 등록 서버(SMS 대상)에 잇는 매핑의 **순수 규칙**만 둔다
(LLM 0 · I/O 0). PG 정본·Redis 사본은 infrastructure 구현이 포트(아래 ABC)를 채운다.

매핑 키는 `(source_id, domain_id, scope, match_key)`다(§3.2).

| scope | match_key |
|---|---|
| `instance` | `normalize_hostname(인스턴스 이름)` — 파드명(생애 동안 노드 고정) · VM |
| `workload` | `k8s_workload_name(인스턴스 이름)` — 파드 재기동 뒤에도 같은 키 |
| `domain` | `""` — 도메인 기본값 |

조회 판정(§3.1 — 1·2·4순, 3·3'·5순은 호출부가 직접 조회로 끼운다):
1. 관리자 행(manual · verified · disabled) — instance → workload → domain. disabled = `absent`.
2. 자동 instance 행.
4. 자동 workload → domain 행(`MapDecision.deferred` — 호출부가 노드 주소 직접 조회 뒤에 쓴다).
`ambiguous`·`failed` 행은 쓰지 않고 사유만 남긴다. `target_gone`·`kind != "sms"` 대상은 뺀다.

쓰기 규칙(§3.4): 자동 결과는 `origin=auto AND verified=false AND state<>'disabled'` 행만 갱신한다.
관리자 행(manual·verified)은 결과가 달라도 `evidence.scan_suggests`만 남긴다.

계층: domain — stdlib + `src.domain.host_names`(stdlib 순수 · D-139 부기)만 쓴다. 관측 DB·ITAM의
테이블·칼럼 이름은 여기 두지 않는다(D-088 — overfit_check 스캔 대상).
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.domain.host_names import (
    canonical_host_key,
    host_keys_match,
    k8s_workload_name,
    normalize_hostname,
)

# ── 상수 ────────────────────────────────────────────────────────────────
SCOPE_INSTANCE = "instance"
SCOPE_WORKLOAD = "workload"
SCOPE_DOMAIN = "domain"
#: 조회 순서(좁은 키 먼저).
SCOPES: tuple[str, ...] = (SCOPE_INSTANCE, SCOPE_WORKLOAD, SCOPE_DOMAIN)

ORIGIN_AUTO = "auto"
ORIGIN_MANUAL = "manual"
ORIGINS: tuple[str, ...] = (ORIGIN_AUTO, ORIGIN_MANUAL)

BASIS_NODE_ADDRESS = "node_address"
BASIS_VM_EXACT = "vm_exact"
BASIS_ITAM = "itam"
BASIS_MANUAL = "manual"
BASES: tuple[str, ...] = (BASIS_NODE_ADDRESS, BASIS_VM_EXACT, BASIS_ITAM, BASIS_MANUAL)

STATE_ACTIVE = "active"
STATE_AMBIGUOUS = "ambiguous"
STATE_FAILED = "failed"
STATE_DISABLED = "disabled"
STATES: tuple[str, ...] = (STATE_ACTIVE, STATE_AMBIGUOUS, STATE_FAILED, STATE_DISABLED)

#: 미확정 사유(`meta.hints.polestar_link.reason` · §3.7).
REASON_PENDING = "apm_map_pending"
REASON_FAILED = "apm_map_failed"
REASON_NO_INVENTORY = "apm_map_no_inventory"
REASON_AMBIGUOUS = "apm_map_ambiguous"

#: 대상 종류 — `dpm`은 자리만(§3.9 · 소비자는 `sms`만 쓴다).
KIND_SMS = "sms"
KIND_DPM = "dpm"

#: 힌트 `basis` 값(매핑 hit).
HINT_BASIS = "apm_map"
#: 「대체됨」 표지 — 같은 도메인을 노드 주소·VM 일치로 이은 뒤 남은 자동·미확인 domain 행
#: (failed·ambiguous)의 evidence 키(plans/211 §9.3 E). 표지가 있으면 「검토 필요」·7순 사유에서
#: 빠진다. 사본 값에는 이 키가 있을 때만 `superseded: true`를 싣는다.
EVIDENCE_SUPERSEDED = "superseded"
#: 표지를 달 수 있는 domain 행 state.
SUPERSEDABLE_STATES: tuple[str, ...] = (STATE_FAILED, STATE_AMBIGUOUS)
#: 한 매핑·한 조사의 대상 상한 기본값(`NOISE_APM_HOST_MAP_MAX_TARGETS`).
DEFAULT_MAX_TARGETS = 10

#: 자동 instance 행 정리 — 마지막 사용·관측 뒤 일수(파드명은 계속 바뀌어 쌓인다).
AUTO_INSTANCE_IDLE_DAYS = 30
#: 자동 행 정리 — 마지막 관측 뒤 일수(APM 인스턴스·도메인이 사라진 행).
AUTO_UNSEEN_DAYS = 180
#: 스캔 실행 기록 보존 일수.
SCAN_RUN_RETENTION_DAYS = 90

#: 스캔 실행 상태.
SCAN_RUNNING = "running"
SCAN_OK = "ok"
SCAN_PARTIAL = "partial"
SCAN_FAILED = "failed"
SCAN_ABORTED = "aborted"
SCAN_STATUSES: tuple[str, ...] = (SCAN_RUNNING, SCAN_OK, SCAN_PARTIAL, SCAN_FAILED, SCAN_ABORTED)

#: 조회 판정 단계(`MapDecision.stage`).
STAGE_MANUAL = "manual"            # 1순 — 관리자 행 사용
STAGE_AUTO_INSTANCE = "auto_instance"  # 2순 — 자동 instance 행 사용
STAGE_ABSENT = "absent"            # 1순 — 관리자 「연계 없음」(disabled)
STAGE_MISS = "miss"                # 1·2순 불성립(4순 후보는 `deferred`)

#: 자동 쓰기 판정(`MapWrite.action`).
WRITE_INSERT = "insert"    # 키에 행 없음 → 자동 신규
WRITE_UPDATE = "update"    # 자동·미확인 · 결과 변경 → 갱신 + previous_targets + 변경됨
WRITE_SAME = "same"        # 자동·미확인 · 결과 동일 → updated_at만
WRITE_SUGGEST = "suggest"  # 관리자 행 · 결과 다름 → scan_suggests만
WRITE_SKIP = "skip"        # disabled · 관리자 행 결과 동일 → 불변

#: 정리 사유(`purge_reason`).
PURGE_INSTANCE_IDLE = "instance_idle"
PURGE_UNSEEN = "unseen"

#: 되적재 스트림 메시지 종류(`apmhostmap:req` · 소비자는 API 프로세스).
MSG_LEARN = "learn"
MSG_TOUCH = "touch"
MSG_ITAM_REQUEST = "itam_request"
MESSAGE_TYPES: tuple[str, ...] = (MSG_LEARN, MSG_TOUCH, MSG_ITAM_REQUEST)

#: Redis 사본 필드 구분자 — `{scope}|{domain_id}|{match_key}`.
FIELD_SEP = "|"
#: 소스 id로 쓸 수 없는 예약어 — Redis 사본 키 접미사(색인·스트림·inflight)와 겹친다.
RESERVED_SOURCE_IDS: frozenset[str] = frozenset({"smsip", "smshost", "req", "inflight"})

_BASIS_LABELS: dict[str, str] = {
    BASIS_NODE_ADDRESS: "노드 주소 정확 일치",
    BASIS_VM_EXACT: "VM 인스턴스 IP·호스트명 정확 일치",
    BASIS_ITAM: "ITAM 업무 추정",
    BASIS_MANUAL: "관리자 지정",
}


# ── 예외 ────────────────────────────────────────────────────────────────
class ApmHostMapVersionError(Exception):
    """낙관적 동시성 — 요청 `version`이 현재 행과 다르다(다른 쓰기가 먼저 갱신했다)."""

    def __init__(self, map_id: int, current_version: int) -> None:
        """대상 id와 현재 version을 싣는다."""
        super().__init__(f"apm_host_map id={map_id} version 불일치(현재 {current_version})")
        self.map_id = map_id
        self.current_version = current_version


class ApmHostMapExistsError(Exception):
    """수동 추가 — 같은 키(source·domain·scope·match_key)의 행이 이미 있다."""

    def __init__(self, existing_id: int) -> None:
        """기존 행 id를 싣는다."""
        super().__init__(f"apm_host_map 같은 키 행이 이미 있음(id={existing_id})")
        self.existing_id = existing_id


# ── 키 ──────────────────────────────────────────────────────────────────
def _text(value: Any) -> str:
    return str(value or "").strip()


def instance_match_key(instance_name: str | None) -> str:
    """instance scope 키 — 정규화 인스턴스 이름(소문자 · FQDN 첫 레이블)."""
    return normalize_hostname(instance_name)


def workload_match_key(instance_name: str | None) -> str | None:
    """workload scope 키 — K8s 워크로드 이름. 파드 모양이 아니면(VM 등) None."""
    return k8s_workload_name(instance_name)


def match_key_for(scope: str, instance_name: str | None) -> str | None:
    """scope별 match_key(만들 수 없으면 None · domain은 늘 `""`)."""
    if scope == SCOPE_INSTANCE:
        return instance_match_key(instance_name) or None
    if scope == SCOPE_WORKLOAD:
        return workload_match_key(instance_name)
    if scope == SCOPE_DOMAIN:
        return ""
    return None


def valid_key(source_id: str, domain_id: str, scope: str, match_key: str) -> bool:
    """매핑 키로 쓸 수 있는가 — scope가 알려진 값, source·domain이 비지 않고 구분자(`|`·`:`)가 없다.
    source는 예약어(`RESERVED_SOURCE_IDS`)가 아니다.

    instance·workload의 match_key는 비면 안 된다(domain만 `""`).
    """
    source, domain, key = _text(source_id), _text(domain_id), str(match_key or "")
    if scope not in SCOPES or not source or not domain:
        return False
    if ":" in source or FIELD_SEP in source or FIELD_SEP in domain or FIELD_SEP in key:
        return False
    if source in RESERVED_SOURCE_IDS:
        return False
    if scope == SCOPE_DOMAIN:
        return key == ""
    return bool(key)


def cache_field(scope: str, domain_id: str, match_key: str) -> str:
    """Redis 사본 해시 필드 `{scope}|{domain_id}|{match_key}`.

    Raises:
        ValueError: 알 수 없는 scope · 빈 domain_id · 구분자 `|`가 든 부분
    """
    domain, key = _text(domain_id), str(match_key or "")
    if scope not in SCOPES or not domain or FIELD_SEP in domain or FIELD_SEP in key:
        raise ValueError("매핑 사본 필드로 쓸 수 없는 scope·domain_id·match_key")
    return f"{scope}{FIELD_SEP}{domain}{FIELD_SEP}{key}"


def lookup_fields(domain_id: str, instance_name: str | None) -> dict[str, str]:
    """조회(HMGET 1회)할 필드 `{scope: 필드}` — 만들 수 없는 scope는 뺀다(VM은 workload 없음)."""
    out: dict[str, str] = {}
    for scope in SCOPES:
        key = match_key_for(scope, instance_name)
        if key is None:
            continue
        try:
            out[scope] = cache_field(scope, domain_id, key)
        except ValueError:
            continue
    return out


# ── 대상 ────────────────────────────────────────────────────────────────
def make_target(
    *,
    db_id: str,
    server_name: str,
    hostname: str = "",
    ip: str = "",
    resource_id: str = "",
    kind: str = KIND_SMS,
    target_gone: bool = False,
) -> dict[str, Any]:
    """매핑 대상 1건(§3.3 `targets` 원소)."""
    return {
        "kind": _text(kind) or KIND_SMS,
        "db_id": _text(db_id),
        "server_name": _text(server_name),
        "hostname": _text(hostname),
        "ip": _text(ip),
        "resource_id": _text(resource_id),
        "target_gone": bool(target_gone),
    }


def target_identity(target: Mapping[str, Any]) -> tuple[str, str, str]:
    """대상 동일성 키 `(kind, db_id, 서버명 소문자)`."""
    return (
        _text(target.get("kind")) or KIND_SMS,
        _text(target.get("db_id")),
        _text(target.get("server_name")).lower(),
    )


def _as_targets(raw: Any) -> list[Mapping[str, Any]]:
    if not isinstance(raw, (list, tuple)):
        return []
    return [t for t in raw if isinstance(t, Mapping)]


def normalize_targets(
    raw: Any, max_targets: int | None = DEFAULT_MAX_TARGETS
) -> list[dict[str, Any]]:
    """저장용 대상 목록 — 서버명 빈 원소 제거 · 동일성 중복 제거(앞 우선) · 상한(None = 무상한)."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for t in _as_targets(raw):
        ident = target_identity(t)
        if not ident[1] or not ident[2] or ident in seen:
            continue
        seen.add(ident)
        out.append(make_target(
            db_id=t.get("db_id") or "", server_name=t.get("server_name") or "",
            hostname=t.get("hostname") or "", ip=t.get("ip") or "",
            resource_id=t.get("resource_id") or "", kind=t.get("kind") or KIND_SMS,
            target_gone=bool(t.get("target_gone")),
        ))
        if max_targets is not None and len(out) >= max(1, int(max_targets)):
            break
    return out


def usable_targets(raw: Any, max_targets: int = DEFAULT_MAX_TARGETS) -> list[dict[str, Any]]:
    """진단에 쓰는 대상 — `kind == "sms"` · `target_gone` 아님 · 서버 식별 있음 · 상한."""
    candidates = [
        t for t in _as_targets(raw)
        if _text(t.get("kind")) == KIND_SMS and not t.get("target_gone")
    ]
    return normalize_targets(candidates, max_targets)


def same_targets(left: Any, right: Any) -> bool:
    """두 대상 목록이 동일성 집합으로 같은가(순서·부가 칸 무시)."""
    return {target_identity(t) for t in _as_targets(left)} == {
        target_identity(t) for t in _as_targets(right)
    }


def mark_targets_gone(
    targets: Any,
    present: Collection[tuple[str, str]],
    scanned_db_ids: Collection[str],
) -> list[dict[str, Any]] | None:
    """스캔 결과로 `target_gone`을 다시 매긴다 → 바뀌었으면 새 목록, 아니면 None.

    Args:
        targets: 행의 대상 목록
        present: 스캔에서 본 서버 `(db_id, 서버명 소문자)` 집합
        scanned_db_ids: 이번 스캔이 끝까지 읽은 db_id — 여기 없는(실패·미확인) db의 대상은 손대지
            않는다(읽지 못한 것을 「사라짐」으로 판정하지 않는다)
    """
    scanned = {_text(d) for d in scanned_db_ids}
    changed = False
    out: list[dict[str, Any]] = []
    for t in _as_targets(targets):
        new = dict(t)
        db_id = _text(t.get("db_id"))
        if _text(t.get("kind")) == KIND_SMS and db_id in scanned:
            gone = (db_id, _text(t.get("server_name")).lower()) not in present
            if bool(t.get("target_gone")) != gone:
                new["target_gone"] = gone
                changed = True
        out.append(new)
    return out if changed else None


# ── 조회 판정 (§3.1 1·2·4순) ───────────────────────────────────────────
def is_admin_record(record: Mapping[str, Any] | None) -> bool:
    """관리자 행인가 — manual · verified · disabled(「연계 없음」은 관리자만 정한다)."""
    if not isinstance(record, Mapping):
        return False
    return (
        record.get("origin") == ORIGIN_MANUAL
        or record.get("verified") is True
        or record.get("state") == STATE_DISABLED
    )


@dataclass(frozen=True)
class MapPick:
    """판정에 쓰인 매핑 행 1건과 그 진단 대상."""

    scope: str
    record: Mapping[str, Any]
    targets: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MapDecision:
    """사본 조회 결과의 판정.

    - `stage`: `STAGE_MANUAL`(1순) · `STAGE_AUTO_INSTANCE`(2순) · `STAGE_ABSENT`(disabled) ·
      `STAGE_MISS`(1·2순 불성립)
    - `pick`: 성립한 행(`STAGE_ABSENT`면 disabled 행 · `STAGE_MISS`면 None)
    - `deferred`: 4순 후보(자동 workload → domain) — `STAGE_MISS`일 때만. 호출부는 3·3'순(노드
      주소) 직접 조회가 불성립할 때 쓴다
    - `reason`: 쓰지 못한 행이 있었으면 첫 사유(`REASON_AMBIGUOUS`·`REASON_FAILED` — 대상이 전부
      사라진 활성 행도 `REASON_FAILED`). 없으면 None. 「대체됨」 표지(사본 값 `superseded`)가 있는
      행은 사유를 남기지 않는다(plans/211 §9.3 E)
    """

    stage: str
    pick: MapPick | None = None
    deferred: MapPick | None = None
    reason: str | None = None


def _state_reason(state: Any) -> str:
    return REASON_AMBIGUOUS if state == STATE_AMBIGUOUS else REASON_FAILED


def decide_from_map(
    records: Mapping[str, Mapping[str, Any] | None],
    *,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> MapDecision:
    """사본 조회 결과(`{scope: 레코드}` — 없는 scope는 빠지거나 None) → 판정(§3.1 1·2·4순)."""
    reason: str | None = None

    def _usable(scope: str) -> MapPick | None:
        nonlocal reason
        rec = records.get(scope)
        if not isinstance(rec, Mapping):
            return None
        if rec.get("state") != STATE_ACTIVE:
            if rec.get(EVIDENCE_SUPERSEDED) is not True:  # 대체된 행은 사유로 남기지 않는다
                reason = reason or _state_reason(rec.get("state"))
            return None
        targets = usable_targets(rec.get("targets"), max_targets)
        if not targets:
            reason = reason or REASON_FAILED
            return None
        return MapPick(scope=scope, record=rec, targets=tuple(targets))

    for scope in SCOPES:
        rec = records.get(scope)
        if not is_admin_record(rec):
            continue
        assert isinstance(rec, Mapping)
        if rec.get("state") == STATE_DISABLED:
            return MapDecision(stage=STAGE_ABSENT, pick=MapPick(scope, rec, ()), reason=reason)
        pick = _usable(scope)
        if pick is not None:
            return MapDecision(stage=STAGE_MANUAL, pick=pick, reason=reason)

    if not is_admin_record(records.get(SCOPE_INSTANCE)):
        pick = _usable(SCOPE_INSTANCE)
        if pick is not None:
            return MapDecision(stage=STAGE_AUTO_INSTANCE, pick=pick, reason=reason)

    deferred: MapPick | None = None
    for scope in (SCOPE_WORKLOAD, SCOPE_DOMAIN):
        if is_admin_record(records.get(scope)):
            continue
        deferred = _usable(scope)
        if deferred is not None:
            break
    return MapDecision(stage=STAGE_MISS, deferred=deferred, reason=reason)


# ── 자동 쓰기 규칙 (되적재 · 스캔 대조 — §3.4 · §3.5) ─────────────────
@dataclass(frozen=True)
class AutoProposal:
    """자동 결과 1건(되적재 또는 스캔 일괄 생성)이 한 키에 제안하는 내용."""

    domain_id: str
    scope: str
    match_key: str
    basis: str
    state: str
    targets: tuple[dict[str, Any], ...]
    evidence: Mapping[str, Any] = field(default_factory=dict)
    domain_name: str = ""
    instance_example: str = ""


@dataclass(frozen=True)
class MapWrite:
    """자동 제안을 기존 행에 적용한 판정 — 저장소가 그대로 쓴다.

    `targets`·`state`·`basis`·`evidence`는 쓰기 뒤 행의 값이다(`WRITE_SKIP`·`WRITE_SAME`이면
    기존 값 · `WRITE_SUGGEST`면 evidence만 바뀐다).
    """

    action: str
    targets: tuple[dict[str, Any], ...]
    state: str
    basis: str
    evidence: dict[str, Any]


def _same_result(existing: Mapping[str, Any], proposal: AutoProposal) -> bool:
    return existing.get("state") == proposal.state and same_targets(
        existing.get("targets"), proposal.targets
    )


def _same_suggestion(evidence: Mapping[str, Any], proposal: AutoProposal) -> bool:
    """관리자 행에 이미 같은 제안(basis·state·대상 동일성 집합)이 있는가.

    같으면 다시 쓰지 않는다 — 연계 요청·스캔마다 `at`만 바꿔 `version`을 올리면 관리자 수정이
    `version_conflict`로 반복 거절된다(D-362 ③ · plans/211 W6).
    """
    prior = evidence.get("scan_suggests")
    return (
        isinstance(prior, Mapping)
        and prior.get("basis") == proposal.basis
        and prior.get("state") == proposal.state
        and same_targets(prior.get("targets"), proposal.targets)
    )


def _evidence(existing: Mapping[str, Any]) -> dict[str, Any]:
    raw = existing.get("evidence")
    return dict(raw) if isinstance(raw, Mapping) else {}


def plan_auto_write(
    existing: Mapping[str, Any] | None,
    proposal: AutoProposal,
    *,
    now_iso: str,
) -> MapWrite:
    """자동 제안 × 기존 행 → 쓰기 판정(§3.5 「일괄 생성」 표 · 되적재도 같은 규칙).

    | 기존 행 | 판정 |
    |---|---|
    | 없음 | `insert`(origin auto · 미확인) |
    | disabled | `skip` |
    | manual·verified · 결과 동일 / 다름 | `skip` / `suggest`(evidence.scan_suggests만) |
    | auto·미확인 · 결과 동일 / 다름 | `same`(updated_at만) / `update`(+previous_targets · 변경됨) |

    「결과」는 state와 대상 동일성 집합이다. 관리자 행에 같은 제안이 이미 있으면 `skip`이다.
    """
    proposed_targets = tuple(proposal.targets)
    if existing is None:
        return MapWrite(
            action=WRITE_INSERT, targets=proposed_targets, state=proposal.state,
            basis=proposal.basis, evidence=dict(proposal.evidence),
        )
    old_targets = tuple(dict(t) for t in _as_targets(existing.get("targets")))
    old_state = str(existing.get("state") or "")
    old_basis = str(existing.get("basis") or "")
    old_evidence = _evidence(existing)
    if old_state == STATE_DISABLED:
        return MapWrite(WRITE_SKIP, old_targets, old_state, old_basis, old_evidence)
    if is_admin_record(existing):
        if _same_result(existing, proposal) or _same_suggestion(old_evidence, proposal):
            return MapWrite(WRITE_SKIP, old_targets, old_state, old_basis, old_evidence)
        suggested = {
            **old_evidence,
            "scan_suggests": {
                "basis": proposal.basis,
                "state": proposal.state,
                "targets": [dict(t) for t in proposed_targets],
                "at": now_iso,
            },
        }
        return MapWrite(WRITE_SUGGEST, old_targets, old_state, old_basis, suggested)
    if _same_result(existing, proposal):
        return MapWrite(WRITE_SAME, old_targets, old_state, old_basis, old_evidence)
    updated = {
        **dict(proposal.evidence),
        "previous_targets": [dict(t) for t in old_targets],
        "changed": True,
        "changed_at": now_iso,
    }
    return MapWrite(WRITE_UPDATE, proposed_targets, proposal.state, proposal.basis, updated)


def next_retry_at(state: str, now: datetime, retry_hours: int) -> datetime | None:
    """`failed`·`ambiguous` 행의 다음 재시도 시각(그 밖은 None)."""
    if state in (STATE_FAILED, STATE_AMBIGUOUS):
        return now + timedelta(hours=max(0, int(retry_hours)))
    return None


def learn_proposals(
    *,
    domain_id: str,
    instance_name: str,
    targets: Sequence[Mapping[str, Any]],
    basis: str,
    existing_workload: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    domain_name: str = "",
    state: str = STATE_ACTIVE,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> list[AutoProposal]:
    """직접 조회 결과(§3.1 3·3'·5순) → instance 행 + workload 행 제안(되적재).

    - instance 행: 이번 결과 대상 그대로(상한).
    - workload 행(파드 모양일 때만): 기존 workload 대상 ∪ 이번 대상 — 이번 대상을 뒤에 두고 상한을
      넘으면 **오래된 쪽(앞)부터** 버린다(새로 확인한 노드를 남긴다). 기존 행이 관리자 행이어도
      합집합을 제안한다(쓰기 규칙이 `scan_suggests`로만 남긴다).
    instance 키를 만들 수 없으면 빈 목록이다.
    """
    cap = max(1, int(max_targets))
    learned = normalize_targets(list(targets), cap)
    inst_key = instance_match_key(instance_name)
    if not inst_key or not learned:
        return []
    ev = dict(evidence or {})
    out = [AutoProposal(
        domain_id=_text(domain_id), scope=SCOPE_INSTANCE, match_key=inst_key, basis=basis,
        state=state, targets=tuple(learned), evidence=ev, domain_name=domain_name,
        instance_example=_text(instance_name),
    )]
    wl_key = workload_match_key(instance_name)
    if wl_key:
        learned_ids = {target_identity(t) for t in learned}
        prior = [
            t for t in normalize_targets((existing_workload or {}).get("targets"), None)
            if target_identity(t) not in learned_ids
        ]
        merged = (prior + learned)[-cap:]
        out.append(AutoProposal(
            domain_id=_text(domain_id), scope=SCOPE_WORKLOAD, match_key=wl_key, basis=basis,
            state=state, targets=tuple(merged), evidence=ev, domain_name=domain_name,
            instance_example=_text(instance_name),
        ))
    return out


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def purge_reason(record: Mapping[str, Any], now: datetime) -> str | None:
    """정리 대상인가 → 사유(`PURGE_INSTANCE_IDLE`·`PURGE_UNSEEN`) 또는 None.

    자동·미확인 행만 정리한다(관리자 행은 유지).
    - instance 행: 마지막 사용·관측·생성 중 가장 늦은 시각이 `AUTO_INSTANCE_IDLE_DAYS`일 전
    - 모든 scope: 마지막 관측(없으면 생성)이 `AUTO_UNSEEN_DAYS`일 전
    """
    if is_admin_record(record) or record.get("origin") != ORIGIN_AUTO:
        return None
    created = _parse_time(record.get("created_at"))
    seen = _parse_time(record.get("last_seen_at")) or created
    if record.get("scope") == SCOPE_INSTANCE:
        stamps = [
            t for t in (_parse_time(record.get("last_used_at")), seen, created) if t is not None
        ]
        if stamps and max(stamps) < now - timedelta(days=AUTO_INSTANCE_IDLE_DAYS):
            return PURGE_INSTANCE_IDLE
    if seen is not None and seen < now - timedelta(days=AUTO_UNSEEN_DAYS):
        return PURGE_UNSEEN
    return None


# ── VM 정확 일치 · SMS 색인 (§3.4 vm_exact · §3.3 색인) ─────────────────
@dataclass(frozen=True)
class VmMatch:
    """VM 인스턴스 `ip`·`host_name` → SMS 색인 대조 결과.

    `status`: `linked`(서로 다른 서버 정확히 1대) · `ambiguous`(2대 이상 — 행은 `ambiguous`) ·
    `none`(0대). `targets`는 찾은 서버 전부다(ambiguous면 후보 · 진단에는 쓰지 않는다).
    """

    status: str
    targets: tuple[dict[str, Any], ...] = ()

    @property
    def state(self) -> str | None:
        """매핑 행 state(linked → active · ambiguous → ambiguous · none → None)."""
        return {"linked": STATE_ACTIVE, "ambiguous": STATE_AMBIGUOUS}.get(self.status)


def vm_exact_match(
    ip_servers: Iterable[Mapping[str, Any]] | None,
    host_servers: Iterable[Mapping[str, Any]] | None,
    *,
    max_targets: int = DEFAULT_MAX_TARGETS,
) -> VmMatch:
    """IP 색인 값 ∪ hostname 색인 값의 서로 다른 서버 수로 판정한다(1대일 때만 성립)."""
    found = normalize_targets(
        [*(ip_servers or ()), *(host_servers or ())], max(int(max_targets), 2)
    )
    if not found:
        return VmMatch("none")
    if len(found) == 1:
        return VmMatch("linked", (found[0],))
    return VmMatch("ambiguous", tuple(found))


def _valid_ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(_text(value)))
    except ValueError:
        return None


def sms_host_key(value: Any, *, canonical: bool = False) -> str:
    """SMS hostname 색인 키.

    `canonical` 거짓(현행)이면 `normalize_hostname`, 참(`HOST_CANONICAL_MATCH_ENABLED` · plans/211
    §9.3 F)이면 `canonical_host_key`의 첫 레이블(업무명 꼬리·`-`·`_` 제거 · IP면 정규형 전체)이다.
    빈 문자열이면 키가 아니다.
    """
    if not canonical:
        return normalize_hostname(value)
    key = canonical_host_key(_text(value))
    if not key or _valid_ip(key):
        return key
    return key.split(".", 1)[0]


def sms_host_servers(
    by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    hostname: Any,
    *,
    canonical: bool = False,
) -> list[Mapping[str, Any]]:
    """hostname 색인 조회 → 서버 목록(없으면 빈 목록).

    `canonical` 참이면 같은 첫 레이블 버킷에서 `host_keys_match(질의, 서버 hostname 또는 서버명)`인
    서버만 남긴다(§3.2 — 양쪽 모두 FQDN이면 전체 일치).
    """
    key = sms_host_key(hostname, canonical=canonical)
    bucket = list(by_host.get(key) or ()) if key else []
    if not canonical:
        return bucket
    query = _text(hostname)
    return [
        s for s in bucket
        if host_keys_match(query, s.get("hostname")) or host_keys_match(query, s.get("server_name"))
    ]


def build_sms_index(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonical: bool = False,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """SMS 스냅샷 행 → `(ip 색인, hostname 색인)` — 값은 서버 목록(같은 키의 2대 이상도 싣는다).

    행: `{db_id, server_name, hostname, ip, nic_ips, resource_id}`. IP 키는 대표 IP + NIC IP(형식
    검증 통과분), hostname 키는 hostname·서버명의 `sms_host_key`(`canonical` 참이면 정규형 첫
    레이블 — 조회는 `sms_host_servers`)다.
    """
    by_ip: dict[str, list[dict[str, Any]]] = {}
    by_host: dict[str, list[dict[str, Any]]] = {}

    def _add(index: dict[str, list[dict[str, Any]]], key: str, target: dict[str, Any]) -> None:
        bucket = index.setdefault(key, [])
        if target_identity(target) not in {target_identity(t) for t in bucket}:
            bucket.append(target)

    for row in rows:
        if not _text(row.get("db_id")) or not _text(row.get("server_name")):
            continue
        target = make_target(
            db_id=row.get("db_id") or "", server_name=row.get("server_name") or "",
            hostname=row.get("hostname") or "", ip=row.get("ip") or "",
            resource_id=row.get("resource_id") or "",
        )
        nic = row.get("nic_ips")
        ips = [row.get("ip"), *(nic if isinstance(nic, (list, tuple)) else ())]
        for raw_ip in ips:
            ip = _valid_ip(raw_ip)
            if ip:
                _add(by_ip, ip, target)
        for raw_name in (row.get("hostname"), row.get("server_name")):
            name = sms_host_key(raw_name, canonical=canonical)
            if name:
                _add(by_host, name, target)
    return by_ip, by_host


# ── 힌트 (§3.7 `meta.hints.polestar_link` 가산 키) ─────────────────────
def evidence_text(origin: str, basis: str, *, verified: bool = False) -> str:
    """근거 문장 — 자동은 「자동 · 추정」을 늘 표기한다(I-1)."""
    label = _BASIS_LABELS.get(basis, basis or "근거 미상")
    if origin == ORIGIN_MANUAL:
        return f"APM↔SMS 연계 — 관리자 등록({label})"
    if verified:
        return f"APM↔SMS 연계 — 자동 · 관리자 확인({label})"
    return f"APM↔SMS 연계 — 자동 · 추정({label})"


def cache_value(row: Mapping[str, Any]) -> dict[str, Any]:
    """PG 행 → Redis 사본 값.

    `{id, origin, basis, state, verified, targets, evidence_text, version}` + evidence에 「대체됨」
    표지가 있을 때만 `superseded: true`(plans/211 §9.3 E — 표지 없는 행은 현행 값 그대로).
    """
    origin = str(row.get("origin") or "")
    basis = str(row.get("basis") or "")
    verified = row.get("verified") is True
    value: dict[str, Any] = {
        "id": int(row.get("id") or 0),
        "origin": origin,
        "basis": basis,
        "state": str(row.get("state") or ""),
        "verified": verified,
        "targets": [dict(t) for t in _as_targets(row.get("targets"))],
        "evidence_text": evidence_text(origin, basis, verified=verified),
        "version": int(row.get("version") or 0),
    }
    if is_superseded(row):
        value[EVIDENCE_SUPERSEDED] = True
    return value


def is_superseded(row: Mapping[str, Any] | None) -> bool:
    """PG 행의 evidence에 「대체됨」 표지가 있는가."""
    raw = (row or {}).get("evidence")
    return isinstance(raw, Mapping) and bool(raw.get(EVIDENCE_SUPERSEDED))


def linked_hint(pick: MapPick, *, at: str) -> dict[str, Any]:
    """매핑 hit → `polestar_link`(linked). 대상이 2대 이상이면 `targets`(≤상한)를 싣는다.

    최상위 `db_id`·`server_name`은 첫 대상이다(하위 호환).
    """
    rec = pick.record
    first = pick.targets[0]
    origin = str(rec.get("origin") or "")
    basis = str(rec.get("basis") or "")
    hint: dict[str, Any] = {
        "status": "linked",
        "db_id": first["db_id"],
        "server_name": first["server_name"],
        "hostname": first.get("hostname", ""),
        "basis": HINT_BASIS,
        "at": at,
        "map_origin": origin,
        "map_scope": pick.scope,
        "map_id": int(rec.get("id") or 0),
        "map_basis": basis,
        "resource_id": first.get("resource_id", ""),
        "evidence_text": str(
            rec.get("evidence_text")
            or evidence_text(origin, basis, verified=rec.get("verified") is True)
        ),
    }
    if len(pick.targets) > 1:
        hint["targets"] = [
            {
                "db_id": t["db_id"],
                "server_name": t["server_name"],
                "hostname": t.get("hostname", ""),
                "resource_id": t.get("resource_id", ""),
            }
            for t in pick.targets
        ]
    return hint


def absent_hint() -> dict[str, Any]:
    """disabled 매핑(관리자 「연계 없음」) → `polestar_link`(absent)."""
    return {"status": "absent", "basis": HINT_BASIS}


def unresolved_hint(reason: str, *, node_address_status: str | None = None) -> dict[str, Any]:
    """미확정 → `polestar_link`(unresolved).

    `no_config_fallback`으로 조사 쪽 설정 매핑 대체를 막는다.
    """
    hint: dict[str, Any] = {"status": "unresolved", "reason": reason, "no_config_fallback": True}
    if node_address_status:
        hint["node_address_status"] = node_address_status
    return hint


# ── 되적재 스트림 메시지 (`apmhostmap:req`) ─────────────────────────────
def learn_message(
    *,
    source_id: str,
    domain_id: str,
    instance_name: str,
    basis: str,
    targets: Sequence[Mapping[str, Any]],
    domain_name: str = "",
    state: str = STATE_ACTIVE,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """직접 조회 결과 되적재 요청(워커 → API 소비자)."""
    return {
        "type": MSG_LEARN,
        "source_id": _text(source_id),
        "domain_id": _text(domain_id),
        "domain_name": _text(domain_name),
        "instance_name": _text(instance_name),
        "basis": basis,
        "state": state,
        "targets": [dict(t) for t in targets],
        "evidence": dict(evidence or {}),
    }


def touch_message(*, source_id: str, map_ids: Sequence[int]) -> dict[str, Any]:
    """매핑 hit 사용 시각 갱신 요청(`last_used_at` — 워커는 PG에 쓰지 않는다)."""
    return {"type": MSG_TOUCH, "source_id": _text(source_id), "ids": [int(i) for i in map_ids]}


def itam_request_message(
    *, source_id: str, domain_id: str, domain_name: str = ""
) -> dict[str, Any]:
    """알람 miss 시 그 도메인의 ITAM 경유 생성 요청(비동기 · 다음 알람부터 4순)."""
    return {
        "type": MSG_ITAM_REQUEST,
        "source_id": _text(source_id),
        "domain_id": _text(domain_id),
        "domain_name": _text(domain_name),
    }


# ── 포트 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MapLookup:
    """사본 조회 결과 — `records`는 있는 scope만 · `error`가 있으면 miss로 다룬다(오연결 없음)."""

    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class IndexLookup:
    """SMS 색인 조회 결과 — `servers`(0대 이상) · `error`가 있으면 miss로 다룬다."""

    servers: tuple[dict[str, Any], ...] = ()
    error: str | None = None


class ApmHostMapRepository(ABC):
    """연계 매핑 PG 정본 저장소 포트 — 매핑·스캔 실행 기록·스냅샷.

    기록자는 API 프로세스뿐이다(D-049). 관리 동작(추가·수정·확인·삭제)의 오류는 그대로 올리고,
    묶음 동작(자동 반영 여러 건)은 행별로 격리한다.
    """

    @abstractmethod
    async def list_maps(
        self,
        *,
        source_id: str | None = None,
        state: str | None = None,
        origin: str | None = None,
        basis: str | None = None,
        query: str | None = None,
        review_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """목록(최근 갱신 먼저)과 전체 건수. `review_only`는 「검토 필요」(ambiguous·failed·변경됨·
        target_gone·scan_suggests)만."""

    @abstractmethod
    async def get(self, map_id: int) -> dict[str, Any] | None:
        """단건(없으면 None)."""

    @abstractmethod
    async def get_by_key(
        self, source_id: str, domain_id: str, scope: str, match_key: str
    ) -> dict[str, Any] | None:
        """키로 단건(없으면 None)."""

    @abstractmethod
    async def all_rows(self) -> list[dict[str, Any]]:
        """전체 행(사본 재구성용)."""

    @abstractmethod
    async def create_manual(
        self,
        *,
        source_id: str,
        domain_id: str,
        scope: str,
        match_key: str,
        targets: Sequence[Mapping[str, Any]],
        state: str = STATE_ACTIVE,
        domain_name: str = "",
        instance_example: str = "",
        note: str = "",
        by: str = "",
    ) -> dict[str, Any]:
        """수동 추가(origin manual · basis manual).

        Raises:
            ApmHostMapExistsError: 같은 키 행이 이미 있다
            ValueError: 키·state가 규칙 밖
        """

    @abstractmethod
    async def update(
        self,
        map_id: int,
        *,
        version: int,
        targets: Sequence[Mapping[str, Any]] | None = None,
        state: str | None = None,
        note: str | None = None,
        by: str = "",
    ) -> dict[str, Any] | None:
        """관리자 수정 — origin을 manual로 바꾼다(자동이 다시 덮지 않는다). 없으면 None.

        Raises:
            ApmHostMapVersionError: version 불일치
        """

    @abstractmethod
    async def verify(self, map_id: int, *, version: int, by: str = "") -> dict[str, Any] | None:
        """자동 결과 확인(verified=true · 이후 자동 갱신 금지). 없으면 None.

        Raises:
            ApmHostMapVersionError: version 불일치
        """

    @abstractmethod
    async def delete(self, map_id: int, *, version: int) -> dict[str, Any] | None:
        """삭제 → 지운 행(없으면 None).

        Raises:
            ApmHostMapVersionError: version 불일치
        """

    @abstractmethod
    async def apply_auto(
        self,
        source_id: str,
        proposal: AutoProposal,
        *,
        now: datetime | None = None,
        retry_hours: int = 24,
    ) -> tuple[str, dict[str, Any] | None]:
        """자동 제안 1건을 쓰기 규칙(`plan_auto_write`)대로 한 트랜잭션에 반영 → `(action, 행)`."""

    @abstractmethod
    async def apply_auto_many(
        self,
        source_id: str,
        proposals: Sequence[AutoProposal],
        *,
        now: datetime | None = None,
        retry_hours: int = 24,
    ) -> list[tuple[str, dict[str, Any] | None]]:
        """여러 건 — 행별 개별 트랜잭션 · 실패 행은 `("error", None)`."""

    @abstractmethod
    async def set_domain_superseded(
        self, source_id: str, domain_id: str, marker: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """도메인 기본값 행의 「대체됨」 표지를 단다(`marker`)·뗀다(None) → 바뀐 행 또는 None.

        자동·미확인이고 state가 `SUPERSEDABLE_STATES`인 domain 행만 바꾼다(관리자 행 불변).
        """

    @abstractmethod
    async def touch(self, map_ids: Sequence[int], *, at: datetime | None = None) -> int:
        """`last_used_at` 묶음 갱신 → 갱신 행 수."""

    @abstractmethod
    async def set_targets_gone(
        self, present: Collection[tuple[str, str]], scanned_db_ids: Collection[str]
    ) -> int:
        """스캔 결과로 전 행의 `target_gone`을 다시 매긴다(`mark_targets_gone`) → 바뀐 행 수."""

    @abstractmethod
    async def cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        """정리 — 자동 instance 30일 · 자동 180일 미관측 · 실행 기록 90일 → 삭제 건수."""

    @abstractmethod
    async def start_scan_run(
        self, *, requested_by: str, run_id: str | None = None
    ) -> dict[str, Any]:
        """스캔 실행 기록 생성(status running)."""

    @abstractmethod
    async def update_scan_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        scanners: Mapping[str, Any] | None = None,
        link_summary: Mapping[str, Any] | None = None,
        finished: bool = False,
    ) -> dict[str, Any] | None:
        """실행 기록 갱신(준 칸만) → 갱신 행(없으면 None)."""

    @abstractmethod
    async def get_scan_run(self, run_id: str) -> dict[str, Any] | None:
        """실행 기록 단건."""

    @abstractmethod
    async def list_scan_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """실행 기록 목록(최근 먼저)."""

    @abstractmethod
    async def abort_running_scan_runs(self) -> int:
        """기동 시 남은 `running` 기록을 `aborted`로 → 바꾼 건수."""

    @abstractmethod
    async def load_snapshot(
        self, table: str, run_id: str, rows: Sequence[Mapping[str, Any]]
    ) -> int:
        """스냅샷 행 적재(한 트랜잭션 · 같은 run 재적재는 덮는다) → 적재 행 수."""

    @abstractmethod
    async def swap_snapshot(self, table: str, run_id: str) -> None:
        """「현재 스냅샷」 포인터를 이 run으로 바꾸고 이전 run 행을 지운다(성공 스캐너만)."""

    @abstractmethod
    async def current_snapshot_run(self, table: str) -> str | None:
        """현재 스냅샷 run_id(없으면 None)."""

    @abstractmethod
    async def snapshot_rows(
        self,
        table: str,
        *,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """현재 스냅샷 행(검색어 · 페이지 선택)."""


class ApmHostMapCache(ABC):
    """연계 매핑 Redis 읽기 사본·SMS 색인 포트.

    읽기(`lookup`·`sms_by_ip`·`sms_by_host`)는 워커·API 어디서나, 쓰기는 API 프로세스만 한다.
    Redis 오류는 삼키지 않고 결과의 `error`로 돌려준다(호출부는 miss로 다룬다).
    """

    @abstractmethod
    async def lookup(self, source_id: str, domain_id: str, instance_name: str) -> MapLookup:
        """instance·workload·domain 필드를 한 번(HMGET)에 읽는다."""

    @abstractmethod
    async def sms_by_ip(self, ip: str) -> IndexLookup:
        """SMS IP 색인 1회 조회."""

    @abstractmethod
    async def sms_by_host(self, hostname: str) -> IndexLookup:
        """SMS hostname 색인 1회 조회(정규화 hostname)."""

    @abstractmethod
    async def put(self, row: Mapping[str, Any]) -> bool:
        """행 1건을 사본에 반영(쓰기마다) → 성공 여부."""

    @abstractmethod
    async def remove(self, row: Mapping[str, Any]) -> bool:
        """행 1건을 사본에서 뺀다 → 성공 여부."""

    @abstractmethod
    async def rebuild(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """사본 전체 재구성 → 적재 필드 수."""

    @abstractmethod
    async def rebuild_sms_index(
        self,
        by_ip: Mapping[str, Sequence[Mapping[str, Any]]],
        by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> int:
        """SMS 색인 전체 재구성 → 적재 키 수."""


__all__ = [
    "AUTO_INSTANCE_IDLE_DAYS",
    "AUTO_UNSEEN_DAYS",
    "BASES",
    "BASIS_ITAM",
    "BASIS_MANUAL",
    "BASIS_NODE_ADDRESS",
    "BASIS_VM_EXACT",
    "DEFAULT_MAX_TARGETS",
    "EVIDENCE_SUPERSEDED",
    "FIELD_SEP",
    "HINT_BASIS",
    "KIND_DPM",
    "KIND_SMS",
    "MESSAGE_TYPES",
    "MSG_ITAM_REQUEST",
    "MSG_LEARN",
    "MSG_TOUCH",
    "ORIGINS",
    "ORIGIN_AUTO",
    "ORIGIN_MANUAL",
    "PURGE_INSTANCE_IDLE",
    "PURGE_UNSEEN",
    "REASON_AMBIGUOUS",
    "REASON_FAILED",
    "REASON_NO_INVENTORY",
    "REASON_PENDING",
    "SCAN_ABORTED",
    "SCAN_FAILED",
    "SCAN_OK",
    "SCAN_PARTIAL",
    "SCAN_RUNNING",
    "SCAN_RUN_RETENTION_DAYS",
    "SCAN_STATUSES",
    "SCOPES",
    "SCOPE_DOMAIN",
    "SCOPE_INSTANCE",
    "SCOPE_WORKLOAD",
    "STAGE_ABSENT",
    "STAGE_AUTO_INSTANCE",
    "STAGE_MANUAL",
    "STAGE_MISS",
    "STATES",
    "STATE_ACTIVE",
    "STATE_AMBIGUOUS",
    "STATE_DISABLED",
    "STATE_FAILED",
    "SUPERSEDABLE_STATES",
    "WRITE_INSERT",
    "WRITE_SAME",
    "WRITE_SKIP",
    "WRITE_SUGGEST",
    "WRITE_UPDATE",
    "ApmHostMapCache",
    "ApmHostMapExistsError",
    "ApmHostMapRepository",
    "ApmHostMapVersionError",
    "AutoProposal",
    "IndexLookup",
    "MapDecision",
    "MapLookup",
    "MapPick",
    "MapWrite",
    "VmMatch",
    "absent_hint",
    "build_sms_index",
    "cache_field",
    "cache_value",
    "decide_from_map",
    "evidence_text",
    "instance_match_key",
    "is_admin_record",
    "is_superseded",
    "itam_request_message",
    "learn_message",
    "learn_proposals",
    "linked_hint",
    "lookup_fields",
    "make_target",
    "mark_targets_gone",
    "match_key_for",
    "next_retry_at",
    "normalize_targets",
    "plan_auto_write",
    "purge_reason",
    "same_targets",
    "sms_host_key",
    "sms_host_servers",
    "target_identity",
    "touch_message",
    "unresolved_hint",
    "usable_targets",
    "valid_key",
    "vm_exact_match",
    "workload_match_key",
]
