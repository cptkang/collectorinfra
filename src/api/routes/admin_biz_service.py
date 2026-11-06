"""관리자 「업무 서비스 정의」 API — 목록·상세·수동 생성·수정·구성원·병합·분할·후보·계산·APM 대조
(plans/215 §3.6 · §3.7 · §5.1 ②③ · §6 · §9 · D-368).

`NOISE_BIZ_SERVICE_ENABLED`(`biz_service_active`)가 켜진 채 기동했을 때만 등록한다
(꺼져 있으면 라우트가 없어 404). 저장소(`app.state.biz_service_repo`)는 기동 단계가 auth 풀이
있을 때만 둔다 — 없으면 503 `biz_service_unavailable`. 계산(`app.state.biz_service_compute`)은
210 사전 스캔 저장소가 있을 때만 조립된다 — 없으면 미리보기·적용은 409 `scan_disabled`(Q5).

- 인가: `require_admin_user` 뒤에서 role 클레임을 한 번 더 명시 검증한다
  (서명 검증만으로 끝내지 않는다).
- 감사: 전 변경 동작을 `log_admin_event`로 before/after와 함께 남긴다(거부 `denied` ·
  실패 `failed` · 사유는 오류 코드 — 예외 문자열을 싣지 않는다). 일괄 후보 동작은 행마다
  1줄 + 묶음 id(`batch_id`). 응답에 `audit_logged`를 싣는다(침묵 금지).
- 편집은 서비스 `version` 낙관적 잠금 — 불일치는 409 `version_conflict`(덮어쓰기 경로 없음).
- 입력 검증(§6): `db_id` 레지스트리 등록값 · `resource_id`·`domain_id` `^\\d{1,19}$` ·
  `source_id` `^[a-z][a-z0-9_]{0,15}$` · tier 1~3|null · layer web/was/db/batch/etc|null ·
  설명 ≤500 · 담당 부서 ≤60 · 담당 부서·설명이 PII 패턴(`pii_filter` 규칙 · `DataMasker`
  값 패턴)에 걸리면 400 `pii_rejected`.
- 관리자 수동 동작(추가·옮김·병합·분할·후보 수락)은 `origin=manual` · 확인은
  `confirmed`/`verified` — 재계산이 덮지 않는다(§3.6).
- 구성원이나 서비스가 바뀐 뒤 Redis 사본을 전체 재구성한다 — 실패해도 응답은 성공이고
  `cache_ok=false` + `cache_reason`을 싣는다(다음 편집·적용이 다시 만든다).
- (W5) P1 편집 보조: 스냅샷 검색(`/inventory/sms`·`/inventory/apm` — q 2~64자 · ≤50 · 210 저장소의
  바인드 LIKE) · 미소속 서버(`/unassigned`) · 일괄 지정(`/bulk-assign` — 행마다 감사 + 묶음 id) ·
  계층 일괄(`PATCH /{id}/members/layer`) · 변경 이력(`/{id}/history` — 감사 저장소 읽기).
  고정 경로 GET은 `/{service_id}`보다 먼저 등록한다.
- (W5b) CSV 가져오기(`POST /import/preview` multipart `file` → 행별 판정 · `POST /import/apply
  {preview_id}` → 한 트랜잭션 · 행마다 감사 1줄 + 묶음 id). 판정·계획은
  `noise_gate.application.biz_service_import` · 미리보기 계획은 이 프로세스 메모리(TTL)에 둔다.
  본문은 인가 뒤에 직접 파싱한다 — `Content-Length`가 상한을 넘으면 읽기 전에, 헤더가 없거나
  chunked면 수신 중 누적이 상한을 넘는 순간 413 `import_too_large`.
- 감시 대상(`monitored`) 꺼짐→켜짐(생성·수정·토글)은 켜진 서비스가 이미
  `NOISE_BIZ_MONITOR_MAX_SERVICES` 이상이면 409 `monitor_cap_reached`(동시 요청 경합의 최종
  방어선은 수집기의 상한).
- SQL은 저장소의 파라미터 바인딩 고정 SQL뿐이다. 관측 DB 조회 0 · LLM 0. APM 대조만 게이트웨이 MCP를
  1회 부른다(`owner=biz_admin` · D-274 — 토큰은 `NOISE_APM_MCP_TOKEN`).
"""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.api.admin_audit import log_admin_event
from src.api.dependencies import is_admin, require_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/biz-services")

T = TypeVar("T")

_DB_ID_PATTERN = r"^[A-Za-z0-9_]{0,64}$"
_RESOURCE_ID_PATTERN = r"^(\d{1,19})?$"
_SOURCE_ID_PATTERN = r"^([a-z][a-z0-9_]{0,15})?$"
_DOMAIN_ID_PATTERN = r"^(\d{1,19})?$"
_RUN_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_MAX_BULK = 200
#: (W5) 스냅샷 검색 — 검색어 길이 · 결과 상한.
_SEARCH_MIN = 2
_SEARCH_MAX = 64
_INVENTORY_LIMIT = 50
#: (W5) 변경 이력 — 감사 기록을 거슬러 읽는 상한.
_HISTORY_SCAN = 1000
_Layer = Literal["web", "was", "db", "batch", "etc"]
_Tier = int
#: 오류 코드 → HTTP 상태(계산 서비스 `BizServiceError.code`).
_COMPUTE_STATUS = {
    "biz_service_disabled": 409,
    "biz_service_running": 409,
    "scan_running": 409,
    "snapshot_changed": 409,
    "preview_not_found": 404,
}


# ── 오류 · 인가 ─────────────────────────────────────────────────────────
def _error(status_code: int, code: str, reason: str, **extra: Any) -> HTTPException:
    """구조화 오류 `{code, reason, …}`."""
    return HTTPException(status_code=status_code, detail={"code": code, "reason": reason, **extra})


def _assert_admin(request: Request, admin: dict[str, Any]) -> None:
    """role 클레임 명시 검증 — 개발 모드(인증 꺼짐)만 예외."""
    if request.app.state.config.auth.enabled and not is_admin(admin):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


def _repo(request: Request) -> Any:
    repo = getattr(request.app.state, "biz_service_repo", None)
    if repo is None:
        raise _error(
            503,
            "biz_service_unavailable",
            "업무 서비스 저장소(PG)를 쓸 수 없습니다 — 내부 PG(auth 풀) 없이 기동했거나 "
            "기동 단계가 실패했습니다.",
        )
    return repo


def _compute(request: Request) -> Any:
    compute = getattr(request.app.state, "biz_service_compute", None)
    if compute is None:
        raise _error(409, "scan_disabled", _compute_reason(request) or "계산을 쓸 수 없습니다.")
    return compute


def _compute_reason(request: Request) -> str | None:
    """계산 불가 사유(가능하면 None)."""
    state = request.app.state
    if getattr(state, "biz_service_compute", None) is not None:
        return None
    if getattr(state, "apm_host_map_repo", None) is None:
        return ("APM·SMS 연계(사전 스캔)가 꺼져 있어 계산할 수 없습니다 — 스냅샷이 모집단입니다"
                "(NOISE_APM_HOST_MAP_ENABLED).")
    return "계산 서비스 조립에 실패했습니다 — 기동 로그를 확인하세요."


def _by(admin: Mapping[str, Any]) -> str:
    return str(admin.get("sub") or "")


def _version_conflict(current: int) -> HTTPException:
    return _error(
        409,
        "version_conflict",
        "화면을 연 뒤 다른 변경이 먼저 저장됐습니다. 새로 고친 뒤 다시 시도하세요.",
        current_version=current,
    )


def _http_error(exc: BaseException) -> HTTPException:
    """저장소·계산·관리 예외 → 구조화 HTTP 오류(예외 문자열은 응답·감사에 싣지 않는다)."""
    from noise_gate.application.biz_service import BizServiceError
    from noise_gate.application.biz_service_admin import BizServiceAdminError
    from noise_gate.infrastructure.biz_service_repository import (
        BizServiceCandidateNotOpen,
        BizServiceExistsError,
        BizServiceMemberExistsError,
        BizServiceVersionConflict,
    )

    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, BizServiceVersionConflict):
        return _version_conflict(exc.current_version)
    if isinstance(exc, BizServiceExistsError):
        return _error(409, "exists", "같은 서비스 키가 이미 있습니다.", existing_id=exc.existing_id)
    if isinstance(exc, BizServiceMemberExistsError):
        return _error(
            409,
            "member_exists",
            "이미 다른 서비스의 구성원입니다 — 「다른 서비스로」 옮기기를 쓰세요.",
            member_id=exc.member_id,
            service_id=exc.service_id,
        )
    if isinstance(exc, BizServiceCandidateNotOpen):
        return _error(409, "candidate_closed", "이미 수락·거절한 후보입니다.",
                      status_now=exc.status)
    if isinstance(exc, BizServiceAdminError):
        return _error(exc.status, exc.code, str(exc), **exc.detail)
    if isinstance(exc, BizServiceError):
        return _error(_COMPUTE_STATUS.get(exc.code, 409), exc.code, str(exc), **exc.detail)
    if isinstance(exc, LookupError):
        return _error(404, "not_found", "대상(서비스·구성원·후보)을 찾지 못했습니다.")
    if isinstance(exc, ValueError):
        logger.info("업무 서비스 관리 입력 거부: %s", exc)
        return _error(400, "invalid_request",
                      "입력 값이 규칙에 맞지 않습니다(대상 0건 · 상한 · 값 범위).")
    logger.warning("업무 서비스 관리 저장소 실패", exc_info=exc)
    return _error(
        503,
        "biz_service_store_failed",
        f"업무 서비스 저장소(PG) 처리에 실패했습니다({type(exc).__name__}). "
        "잠시 뒤 다시 시도하세요.",
    )


# ── 감사 ────────────────────────────────────────────────────────────────
def _service_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """감사 before/after — 서비스 칸(구성원은 활성 수만)."""
    if not row:
        return None
    out = {k: row.get(k) for k in (
        "id", "service_key", "display_name", "tier", "description", "owner_org", "origin",
        "state", "verified", "monitored", "version",
    )}
    members = row.get("members")
    if isinstance(members, list):
        out["active_members"] = sum(1 for m in members if m.get("state") == "active")
    return out


def _member_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {k: row.get(k) for k in (
        "id", "service_id", "kind", "member_key", "origin", "basis", "confidence", "state",
        "layer",
    )}


def _candidate_view(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {k: row.get(k) for k in (
        "id", "kind", "service_id", "other_service_id", "member_kind", "member_key", "status",
    )}


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
    except Exception:  # noqa: BLE001 — 감사 실패가 응답을 막지 않는다(사유는 로그 · audit_logged)
        logger.warning("업무 서비스 관리 감사 기록 실패: %s", action, exc_info=True)
        return False


async def _audit_rejected(
    request: Request, admin: dict[str, Any], action: str, exc: HTTPException, **fields: Any
) -> None:
    """거부(4xx → `denied`)·실패(5xx → `failed`) 감사 — `reason`은 응답 오류 코드."""
    detail = exc.detail if isinstance(exc.detail, Mapping) else {}
    await _audit(
        request,
        admin,
        action,
        "denied" if exc.status_code < 500 else "failed",
        reason=str(detail.get("code") or exc.status_code),
        **fields,
    )


async def _guard(
    request: Request,
    admin: dict[str, Any],
    action: str,
    call: Callable[[], Awaitable[T]],
    **fields: Any,
) -> T:
    """동작 1회 — 예외는 구조화 오류로 바꾸고 거부·실패 감사를 남긴 뒤 올린다."""
    try:
        return await call()
    except Exception as exc:  # noqa: BLE001 — 아래에서 구조화 오류로 바꿔 다시 올린다
        http = _http_error(exc)
        await _audit_rejected(request, admin, action, http, **fields)
        raise http from None


async def _sync_cache(request: Request) -> dict[str, Any]:
    """사본 전체 재구성 → `{cache_ok, cache_reason}`(실패해도 올리지 않는다 — PG는 이미 커밋)."""
    cache = getattr(request.app.state, "biz_service_cache", None)
    if cache is None:
        return {"cache_ok": False,
                "cache_reason": "사본(Redis)이 연결되지 않았습니다 — 기동 로그를 확인하세요."}
    try:
        await cache.sync_from_repo(_repo(request))
    except Exception as exc:  # noqa: BLE001 — 다음 편집·적용·기동이 다시 만든다
        logger.warning("업무 서비스 사본 재구성 실패: %s", type(exc).__name__, exc_info=True)
        return {"cache_ok": False,
                "cache_reason": f"사본 재구성에 실패했습니다({type(exc).__name__}) — "
                                "다음 편집·적용 때 다시 만듭니다."}
    return {"cache_ok": True, "cache_reason": None}


# ── 입력 검증 ───────────────────────────────────────────────────────────
def _pii_rules(value: str | None) -> list[str]:
    """PII 패턴 이름(걸린 규칙만 · 값은 싣지 않는다) — `pii_filter` 규칙 + `DataMasker` 값 패턴."""
    if not value:
        return []
    from src.security.data_masker import DataMasker
    from src.security.pii_filter import scan_pii

    rules = sorted({m.name for m in scan_pii(value, max_per_rule=1, unmask=False)})
    stripped = value.strip()
    if any(p.match(stripped) for p in DataMasker.SENSITIVE_VALUE_PATTERNS):
        rules.append("비밀값 패턴")
    return rules


def _check_pii(fields: Mapping[str, str | None]) -> None:
    """담당 부서·설명 PII 검사 — 걸리면 400 `pii_rejected`(칸 이름·규칙 이름만)."""
    hits = [{"field": k, "rules": r} for k, v in fields.items() if (r := _pii_rules(v))]
    if hits:
        raise _error(
            400,
            "pii_rejected",
            "담당 부서·설명에 개인정보로 보이는 값이 있어 저장하지 않았습니다"
            "(개인 이름·연락처는 넣지 않습니다).",
            fields=hits,
        )


def _known_db_ids() -> set[str]:
    """DB 레지스트리 등록 db_id — 못 읽으면 503(fail-closed)."""
    from src.routing.registry import get_registry

    try:
        return set(get_registry().db_ids())
    except Exception as exc:  # noqa: BLE001 — 레지스트리를 못 읽으면 받지 않는다(fail-closed)
        logger.warning("업무 서비스 — DB 레지스트리 읽기 실패: %s", type(exc).__name__)
        raise _error(503, "registry_unavailable", "DB 레지스트리를 읽지 못했습니다.") from None


def _check_db_id(db_id: str) -> None:
    if db_id not in _known_db_ids():
        raise _error(400, "unknown_db_id", "등록되지 않은 db_id입니다.")


# ── 본문 ────────────────────────────────────────────────────────────────
class ServiceCreate(BaseModel):
    """수동 생성 — 서비스 키·표시명 + 선택 칸."""

    service_key: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=255)
    tier: _Tier | None = Field(None, ge=1, le=3)
    description: str | None = Field(None, max_length=500)
    owner_org: str | None = Field(None, max_length=60)
    note: str | None = Field(None, max_length=1000)
    monitored: bool = False


class ServiceUpdate(BaseModel):
    """수정 — 준 칸만 바꾼다(null은 비운다). `version`은 화면에서 본 값."""

    version: int = Field(..., ge=1)
    display_name: str | None = Field(None, min_length=1, max_length=255)
    tier: _Tier | None = Field(None, ge=1, le=3)
    description: str | None = Field(None, max_length=500)
    owner_org: str | None = Field(None, max_length=60)
    note: str | None = Field(None, max_length=1000)
    state: Literal["active", "disabled"] | None = None
    verified: bool | None = None
    monitored: bool | None = None


class MonitoredBody(BaseModel):
    version: int = Field(..., ge=1)
    monitored: bool


class MemberAdd(BaseModel):
    """구성원 수동 추가 — 종류별 키 칸 + (스냅샷에 없을 때 쓰는) 표시 칸 + 계층."""

    version: int = Field(..., ge=1)
    kind: Literal["sms_server", "apm_instance", "apm_domain", "apm_business"]
    db_id: str = Field("", pattern=_DB_ID_PATTERN)
    resource_id: str = Field("", pattern=_RESOURCE_ID_PATTERN)
    source_id: str = Field("", pattern=_SOURCE_ID_PATTERN)
    domain_id: str = Field("", pattern=_DOMAIN_ID_PATTERN)
    scope: Literal["instance", "workload"] = "instance"
    instance_name: str = Field("", max_length=255)
    business_id: str = Field("", pattern=_DOMAIN_ID_PATTERN)
    domain_name: str = Field("", max_length=255)
    server_name: str = Field("", max_length=255)
    hostname: str = Field("", max_length=255)
    ip: str = Field("", max_length=64)
    layer: _Layer | None = None


class MembersBody(BaseModel):
    version: int = Field(..., ge=1)
    member_ids: list[int] = Field(..., min_length=1, max_length=_MAX_BULK)


class MembersStateBody(MembersBody):
    state: Literal["active", "excluded"]


class MembersConfirmBody(MembersBody):
    confirmed: bool


class MembersMoveBody(MembersBody):
    to_service_id: int = Field(..., ge=1)
    to_version: int = Field(..., ge=1)


class MergeBody(BaseModel):
    """병합 — 이 서비스(A)를 `into_service_id`(B)로."""

    version: int = Field(..., ge=1)
    into_service_id: int = Field(..., ge=1)
    into_version: int = Field(..., ge=1)


class SplitBody(MembersBody):
    """분할 — 선택 구성원을 새 수동 서비스로."""

    service_key: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=255)
    tier: _Tier | None = Field(None, ge=1, le=3)
    description: str | None = Field(None, max_length=500)
    owner_org: str | None = Field(None, max_length=60)


class CandidatesBulk(BaseModel):
    action: Literal["accept", "reject"]
    candidate_ids: list[int] = Field(..., min_length=1, max_length=_MAX_BULK)


class ApplyBody(BaseModel):
    preview_run_id: str = Field(..., pattern=_RUN_ID_PATTERN)


# ── 공통 헬퍼 ───────────────────────────────────────────────────────────
async def _get_service(request: Request, service_id: int) -> dict[str, Any]:
    try:
        row = await _repo(request).get_service(service_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    if row is None:
        raise _error(404, "not_found", "업무 서비스를 찾지 못했습니다.")
    return dict(row)


def _check_version(before: Mapping[str, Any], version: int) -> None:
    current = int(before.get("version") or 0)
    if current != int(version):
        raise _version_conflict(current)


#: 감시 대상 상한 기본값 — `NOISE_BIZ_MONITOR_MAX_SERVICES`(설정 기본과 같다).
_MONITOR_CAP_DEFAULT = 20


def _monitor_cap(request: Request) -> int:
    ng = getattr(request.app.state.config, "noise_gate", None)
    return max(1, int(getattr(ng, "biz_monitor_max_services", _MONITOR_CAP_DEFAULT)))


async def _check_monitor_cap(request: Request) -> None:
    """감시 대상 꺼짐→켜짐 전 상한 검사 — 켜진 수가 이미 상한 이상이면 409 `monitor_cap_reached`.

    켜진 수는 상태와 무관하게 `monitored=true` 전체로 센다(비활성 서비스도 다시 켜면 곧바로
    수집 대상이 되므로). 검사와 쓰기가 한 트랜잭션이 아니어서 동시 요청 두 건이 함께 통과할 수
    있다 — 최종 방어선은 수집기의 상한(넘친 서비스는 「감시 서비스 상한 초과」 사유로 건너뜀)이다.
    """
    cap = _monitor_cap(request)
    _rows, on = await _repo(request).list_services(monitored=True, limit=1, offset=0)
    if int(on) >= cap:
        raise _error(
            409,
            "monitor_cap_reached",
            f"감시 대상 상한 {cap}개에 도달했습니다 — 다른 서비스의 감시를 끈 뒤 다시 켜세요",
            max_services=cap,
        )


async def _prepared(
    request: Request,
    admin: dict[str, Any],
    action: str,
    service_id: int,
    version: int | None,
    checks: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """대상 서비스 읽기 + version·입력 검사 — 거부는 감사 후 올린다."""
    before = await _get_service(request, service_id)
    try:
        if version is not None:
            _check_version(before, version)
        if checks is not None:
            checks()
    except HTTPException as exc:
        await _audit_rejected(request, admin, action, exc, before=_service_view(before))
        raise
    return before


# ── 조회 ────────────────────────────────────────────────────────────────
@router.get(
    "",
    summary="업무 서비스 목록(관리자)",
    description=(
        "검색(표시명·서비스 키·구성원 서버명·hostname)·존·등급·상태·감시 대상 필터와 "
        "페이지(≤100)."
    ),
    tags=["admin"],
)
async def list_services(
    request: Request,
    q: str | None = Query(None, max_length=200),
    zone: str | None = Query(None, max_length=64),
    tier: int | None = Query(None, ge=1, le=3),
    state: Literal["active", "ambiguous", "disabled"] | None = None,
    monitored: bool | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{rows[], total, limit, offset, compute_available, compute_reason}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    try:
        rows, total = await repo.list_services(
            zone=zone or None, tier=tier, state=state, monitored=monitored, query=q,
            limit=limit, offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    reason = _compute_reason(request)
    return {"rows": rows, "total": total, "limit": limit, "offset": offset,
            "compute_available": reason is None, "compute_reason": reason}


@router.get(
    "/candidates",
    summary="업무 서비스 후보 목록(관리자)",
    description="상태(기본 open)·종류·서비스 필터와 페이지(≤200).",
    tags=["admin"],
)
async def list_candidates(
    request: Request,
    status: Literal["open", "accepted", "rejected"] | None = "open",
    kind: Literal["member", "merge", "apm_unlinked"] | None = None,
    service_id: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=_MAX_BULK),
    offset: int = Query(0, ge=0),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{rows[], total, limit, offset}`."""
    _assert_admin(request, admin)
    try:
        rows, total = await _repo(request).list_candidates(
            status=status, kind=kind, service_id=service_id, limit=limit, offset=offset
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    return {"rows": rows, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/runs",
    summary="업무 서비스 계산 실행 기록(관리자)",
    description="최근 순 실행 기록(미리보기·적용 · summary 포함 — 추이용 · ≤100).",
    tags=["admin"],
)
async def list_runs(
    request: Request,
    mode: Literal["preview", "apply"] | None = None,
    limit: int = Query(20, ge=1, le=100),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{runs[]}`."""
    _assert_admin(request, admin)
    try:
        runs = await _repo(request).list_runs(mode=mode, limit=limit)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    return {"runs": runs}


@router.get(
    "/analysis",
    summary="업무 서비스 연계 분석 지표(관리자)",
    description="최근 완료한 적용 실행의 §3.4 지표(전체·서비스별·직전 대비 변화). 없으면 사유.",
    tags=["admin"],
)
async def analysis(
    request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{run_id, finished_at, preview_run_id, summary, reason}`."""
    _assert_admin(request, admin)
    try:
        runs = await _repo(request).list_runs(mode="apply", limit=20)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    for run in runs:
        summary = run.get("summary")
        if run.get("status") == "done" and isinstance(summary, Mapping):
            return {"run_id": run.get("run_id"), "finished_at": run.get("finished_at"),
                    "preview_run_id": summary.get("preview_run_id"), "summary": summary,
                    "reason": None}
    return {"run_id": None, "finished_at": None, "preview_run_id": None, "summary": None,
            "reason": "완료한 적용 실행이 없습니다 — 미리보기 뒤 적용하세요."}


# ── (W5) 스냅샷 검색 · 미소속 서버 (P1 편집 보조 · `/{service_id}`보다 먼저 등록) ──────
def _map_repo(request: Request) -> Any:
    maps = getattr(request.app.state, "apm_host_map_repo", None)
    if maps is None:
        raise _error(409, "scan_disabled",
                     "APM·SMS 연계(사전 스캔)가 꺼져 있어 스냅샷이 없습니다"
                     "(NOISE_APM_HOST_MAP_ENABLED).")
    return maps


def _search_text(q: str) -> str:
    text = q.strip()
    if len(text) < _SEARCH_MIN:
        raise _error(400, "invalid_request", f"검색어는 {_SEARCH_MIN}자 이상이어야 합니다.")
    return text


async def _snapshot_search(
    request: Request, table: str, q: str
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """현재 스냅샷 검색(부분 일치 · LIKE 이스케이프 · 바인드 — 210 저장소)
    → `(행, 잘림, run_id)`."""
    maps = _map_repo(request)
    text = _search_text(q)
    try:
        run_id = await maps.current_snapshot_run(table)
        if run_id is None:
            return [], False, None
        rows = await maps.snapshot_rows(table, query=text, limit=_INVENTORY_LIMIT + 1)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    return rows[:_INVENTORY_LIMIT], len(rows) > _INVENTORY_LIMIT, run_id


_NO_SNAPSHOT = "사전 스캔 스냅샷이 없습니다 — 「APM·SMS 연계」 탭에서 스캔을 먼저 실행하세요."


@router.get(
    "/inventory/sms",
    summary="스냅샷 SMS 서버 검색(관리자)",
    description=(
        "210 현재 스냅샷(`sms_inventory`)에서 서버명·hostname·IP·db_id 부분 일치(q 2~64자 · "
        "결과 ≤50). 관측 DB를 조회하지 않습니다. 이미 서비스 구성원이면 그 소속(`owner`)을 "
        "싣습니다."
    ),
    tags=["admin"],
)
async def inventory_sms(
    request: Request,
    q: str = Query(..., min_length=_SEARCH_MIN, max_length=_SEARCH_MAX),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{rows[], truncated, limit, snapshot_run_id, reason}`."""
    from noise_gate.application.apm_host_map_scan import TABLE_SMS
    from noise_gate.application.biz_service import registry_zone
    from noise_gate.application.biz_service_view import sms_search_keys, sms_search_rows
    from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER

    _assert_admin(request, admin)
    repo = _repo(request)
    rows, truncated, run_id = await _snapshot_search(request, TABLE_SMS, q)
    try:
        owners = await repo.member_owners(KIND_SMS_SERVER, sms_search_keys(rows))
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    return {"rows": sms_search_rows(rows, owners, registry_zone), "truncated": truncated,
            "limit": _INVENTORY_LIMIT, "snapshot_run_id": run_id,
            "reason": None if run_id else _NO_SNAPSHOT}


@router.get(
    "/inventory/apm",
    summary="스냅샷 APM 인스턴스 검색(관리자)",
    description=(
        "210 현재 스냅샷(`apm_inventory`)에서 도메인·인스턴스·IP·호스트 부분 일치(q 2~64자 · "
        "결과 ≤50). 게이트웨이를 부르지 않습니다. 이미 서비스 구성원이면 그 소속을 싣습니다."
    ),
    tags=["admin"],
)
async def inventory_apm(
    request: Request,
    q: str = Query(..., min_length=_SEARCH_MIN, max_length=_SEARCH_MAX),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{rows[], truncated, limit, snapshot_run_id, reason}`."""
    from noise_gate.application.apm_host_map_scan import TABLE_APM
    from noise_gate.application.biz_service_view import apm_instance_key, apm_search_rows
    from noise_gate.domain.biz_service_keys import KIND_APM_INSTANCE

    _assert_admin(request, admin)
    repo = _repo(request)
    rows, truncated, run_id = await _snapshot_search(request, TABLE_APM, q)
    keys = [k for r in rows if (k := apm_instance_key(r))]
    try:
        owners = await repo.member_owners(KIND_APM_INSTANCE, keys)
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    return {"rows": apm_search_rows(rows, owners), "truncated": truncated,
            "limit": _INVENTORY_LIMIT, "snapshot_run_id": run_id,
            "reason": None if run_id else _NO_SNAPSHOT}


@functools.lru_cache(maxsize=1)
def _prefix_config() -> Any:
    """S6 접두 코드 설정(미소속 서버 묶음용 · 프로세스당 1회 읽기)."""
    from noise_gate.application.biz_service_config import load_biz_service_config

    return load_biz_service_config().config.host_prefix


@router.get(
    "/unassigned",
    summary="미소속 서버(관리자 · 검토함)",
    description=(
        "현재 스냅샷에 있는데 어느 서비스의 활성 구성원도 아닌 서버를 존·접두 코드로 묶어 "
        "돌려줍니다(서버 ≤500 · 넘으면 `truncated`). 관측 DB를 조회하지 않습니다."
    ),
    tags=["admin"],
)
async def unassigned(
    request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{groups[{zone, prefix_code, count, servers[]}], total, truncated, limit,
    snapshot_run_id, reason}`."""
    from noise_gate.application.apm_host_map_scan import TABLE_SMS
    from noise_gate.application.biz_service import registry_zone
    from noise_gate.application.biz_service_view import unassigned_groups
    from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER

    _assert_admin(request, admin)
    repo = _repo(request)
    maps = _map_repo(request)
    try:
        run_id = await maps.current_snapshot_run(TABLE_SMS)
        rows = await maps.snapshot_rows(TABLE_SMS) if run_id else []
        owners = await repo.member_owners(KIND_SMS_SERVER) if run_id else {}
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    try:
        prefix = _prefix_config()
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 접두 코드 없이 존으로만 묶는다
        logger.warning("업무 서비스 설정 읽기 실패 — 미소속 서버를 존으로만 묶습니다",
                       exc_info=True)
        prefix = None
    out = unassigned_groups(rows, owners, registry_zone, prefix)
    return {**out, "snapshot_run_id": run_id, "reason": None if run_id else _NO_SNAPSHOT}


# ── 계산 ────────────────────────────────────────────────────────────────
@router.post(
    "/preview",
    summary="업무 서비스 계산 미리보기(관리자)",
    description=(
        "현재 스냅샷 + 연계 매핑으로 계산해 저장 상태와의 차이를 돌려줍니다"
        "(저장은 실행 기록·보강 속성만). 사전 스캔이 꺼져 있으면 409 `scan_disabled` · "
        "실행 중이면 409 `biz_service_running`·`scan_running`."
    ),
    tags=["admin"],
)
async def preview(
    request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """계산 서비스 `preview` 결과 + `audit_logged`."""
    _assert_admin(request, admin)
    action = "biz_service_preview"

    async def _run() -> dict[str, Any]:
        return dict(await _compute(request).preview(_by(admin)))

    out = await _guard(request, admin, action, _run)
    out["audit_logged"] = await _audit(
        request, admin, action, "ok",
        after={"run_id": out.get("run_id"), "total_changes": out.get("total_changes"),
               "scan_run_id": out.get("scan_run_id")},
    )
    return out


@router.post(
    "/apply",
    summary="업무 서비스 계산 적용(관리자)",
    description=(
        "미리보기 결과를 적용합니다. 미리보기 뒤 스냅샷·연계 매핑이 바뀌었으면 "
        "409 `snapshot_changed`, "
        "미리보기가 없으면 404 `preview_not_found`."
    ),
    tags=["admin"],
)
async def apply(
    body: ApplyBody, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """계산 서비스 `apply` 결과 + `audit_logged`."""
    _assert_admin(request, admin)
    action = "biz_service_apply"

    async def _run() -> dict[str, Any]:
        return dict(await _compute(request).apply(body.preview_run_id, _by(admin)))

    out = await _guard(request, admin, action, _run, before={"preview_run_id": body.preview_run_id})
    out["audit_logged"] = await _audit(
        request, admin, action, "ok",
        before={"preview_run_id": body.preview_run_id},
        after={"run_id": out.get("run_id"), "applied": out.get("applied"),
               "cache": out.get("cache")},
    )
    return out


# ── 후보 ────────────────────────────────────────────────────────────────
async def _accept_one(
    request: Request, admin: dict[str, Any], candidate_id: int, display: Any, **fields: Any
) -> dict[str, Any]:
    from noise_gate.application.biz_service_admin import accept_candidate

    repo = _repo(request)
    action = "biz_service_candidate_accept"
    before = await _guard(request, admin, action, lambda: repo.get_candidate(candidate_id),
                          candidate_id=candidate_id, **fields)

    async def _run() -> dict[str, Any]:
        return await accept_candidate(repo, candidate_id, display=display, by=_by(admin))

    result = await _guard(request, admin, action, _run, before=_candidate_view(before),
                          candidate_id=candidate_id, **fields)
    after: dict[str, Any] = {"candidate": _candidate_view(result.get("candidate")),
                             "kind": result.get("kind")}
    if result.get("member") is not None:
        after["member"] = _member_view(result["member"])
        after["moved_from"] = result.get("moved_from")
    if result.get("kind") == "merge":
        after.update(from_service_id=result.get("from_service_id"),
                     to_service_id=result.get("to_service_id"), moved=result.get("moved"))
    result["audit_logged"] = await _audit(request, admin, action, "ok",
                                          before=_candidate_view(before), after=after, **fields)
    return result


async def _reject_one(
    request: Request, admin: dict[str, Any], candidate_id: int, **fields: Any
) -> dict[str, Any]:
    repo = _repo(request)
    action = "biz_service_candidate_reject"

    async def _run() -> dict[str, Any]:
        # 열린 후보만 거절한다(수락한 후보를 거절로 바꾸지 않는다 — 구성원은 이미 들어갔다).
        current = await repo.get_candidate(candidate_id)
        if current is None:
            raise _error(404, "not_found", "후보를 찾지 못했습니다.")
        rows = []
        if current.get("status") == "open":
            rows = await repo.set_candidates_status([candidate_id], status="rejected",
                                                    by=_by(admin))
        if rows:
            return dict(rows[0])
        raise _error(409, "candidate_closed", "이미 수락·거절한 후보입니다.",
                     status_now=current.get("status"))

    row = await _guard(request, admin, action, _run, candidate_id=candidate_id, **fields)
    logged = await _audit(request, admin, action, "ok", after=_candidate_view(row), **fields)
    return {"candidate": row, "audit_logged": logged}


def _display(request: Request) -> Any:
    from noise_gate.application.biz_service_admin import SnapshotDisplay

    return SnapshotDisplay(getattr(request.app.state, "apm_host_map_repo", None))


@router.post(
    "/candidates/bulk",
    summary="업무 서비스 후보 일괄 수락·거절(관리자)",
    description=(
        "후보 ≤200건을 하나씩 처리합니다(행마다 결과 · 감사 1줄 + 묶음 id). "
        "수락: member = 구성원 추가(origin manual · 확인) · merge = 병합 · "
        "apm_unlinked = 수락 불가."
    ),
    tags=["admin"],
)
async def candidates_bulk(
    body: CandidatesBulk, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{batch_id, action, results[{candidate_id, outcome, code, reason}], counts,
    cache_ok, cache_reason}`."""
    _assert_admin(request, admin)
    _repo(request)
    batch_id = uuid.uuid4().hex
    display = _display(request)
    results: list[dict[str, Any]] = []
    for cid in dict.fromkeys(body.candidate_ids):
        try:
            if body.action == "accept":
                await _accept_one(request, admin, cid, display, batch_id=batch_id)
            else:
                await _reject_one(request, admin, cid, batch_id=batch_id)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, Mapping) else {}
            results.append({
                "candidate_id": cid, "outcome": "denied" if exc.status_code < 500 else "failed",
                "code": detail.get("code"), "reason": detail.get("reason"),
            })
            continue
        results.append({"candidate_id": cid, "outcome": "ok", "code": None, "reason": None})
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in ("ok", "denied", "failed")}
    cache = await _sync_cache(request) if body.action == "accept" and counts["ok"] else \
        {"cache_ok": True, "cache_reason": None}
    return {"batch_id": batch_id, "action": body.action, "results": results, "counts": counts,
            **cache}


@router.post(
    "/candidates/{candidate_id}/accept",
    summary="업무 서비스 후보 수락(관리자)",
    description=(
        "member = 그 서비스 구성원(origin manual · confirmed · 근거 보존) · "
        "merge = 병합(다른 서비스 → 후보 서비스) · "
        "apm_unlinked = 400 `candidate_not_acceptable`(「APM·SMS 연계」 탭에서 매핑)."
    ),
    tags=["admin"],
)
async def accept_candidate(
    candidate_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{result, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    result = await _accept_one(request, admin, candidate_id, _display(request))
    logged = result.pop("audit_logged")
    return {"result": result, **await _sync_cache(request), "audit_logged": logged}


@router.post(
    "/candidates/{candidate_id}/reject",
    summary="업무 서비스 후보 거절(관리자)",
    description="거절은 기억합니다 — 같은 후보를 재계산이 다시 내지 않습니다.",
    tags=["admin"],
)
async def reject_candidate(
    candidate_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{candidate, audit_logged}`."""
    _assert_admin(request, admin)
    return await _reject_one(request, admin, candidate_id)


# ── 서비스 생성 · 상세 · 수정 ──────────────────────────────────────────
@router.post(
    "",
    status_code=201,
    summary="업무 서비스 수동 생성(관리자)",
    description=(
        "origin manual · 활성. 같은 서비스 키가 있으면 409 `exists` · "
        "담당 부서·설명 PII는 400."
    ),
    tags=["admin"],
)
async def create_service(
    body: ServiceCreate, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    action = "biz_service_create"
    after = {"service_key": body.service_key, "display_name": body.display_name}
    try:
        _check_pii({"owner_org": body.owner_org, "description": body.description})
    except HTTPException as exc:
        await _audit_rejected(request, admin, action, exc, after=after)
        raise

    async def _run() -> dict[str, Any]:
        if body.monitored:
            await _check_monitor_cap(request)
        return dict(await repo.create_service(**body.model_dump(), by=_by(admin)))

    row = await _guard(request, admin, action, _run, after=after)
    cache = await _sync_cache(request)
    logged = await _audit(request, admin, action, "ok", after=_service_view(row))
    return {"service": row, **cache, "audit_logged": logged}


@router.get(
    "/{service_id}",
    summary="업무 서비스 상세(관리자)",
    description="서비스 + 구성원 전체 + 이 서비스의 열린 후보(≤200).",
    tags=["admin"],
)
async def get_service(
    service_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{service, candidates[], compute_available, compute_reason}`."""
    _assert_admin(request, admin)
    row = await _get_service(request, service_id)
    try:
        cands, _total = await _repo(request).list_candidates(
            status="open", service_id=service_id, limit=_MAX_BULK
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    reason = _compute_reason(request)
    return {"service": row, "candidates": cands, "compute_available": reason is None,
            "compute_reason": reason}


async def _update(
    request: Request,
    admin: dict[str, Any],
    action: str,
    service_id: int,
    version: int,
    changes: dict[str, Any],
) -> dict[str, Any]:
    repo = _repo(request)

    def _checks() -> None:
        if not changes:
            raise _error(400, "invalid_request", "바꿀 칸이 없습니다.")
        _check_pii({k: changes.get(k) for k in ("owner_org", "description") if k in changes})

    before = await _prepared(request, admin, action, service_id, version, _checks)
    turning_on = changes.get("monitored") is True and before.get("monitored") is not True

    async def _run() -> dict[str, Any]:
        if turning_on:
            await _check_monitor_cap(request)
        row = await repo.update_service(service_id, version=version, changes=changes,
                                        by=_by(admin))
        if row is None:
            raise _error(404, "not_found", "업무 서비스를 찾지 못했습니다.")
        return dict(row)

    row = await _guard(request, admin, action, _run, before=_service_view(before))
    cache = await _sync_cache(request)
    logged = await _audit(request, admin, action, "ok", before=_service_view(before),
                          after=_service_view(row))
    return {"service": row, **cache, "audit_logged": logged}


@router.patch(
    "/{service_id}",
    summary="업무 서비스 수정(관리자)",
    description=(
        "표시명·등급·설명·담당 부서·메모·상태(active·disabled)·확인(verified)·감시 대상 중 준 칸만 "
        "바꿉니다. version이 다르면 409 `version_conflict`."
    ),
    tags=["admin"],
)
async def update_service(
    service_id: int,
    body: ServiceUpdate,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "version"}
    for flag in ("display_name", "state", "verified", "monitored"):
        if flag in changes and changes[flag] is None:
            del changes[flag]  # 비울 수 없는 칸의 null은 「안 바꿈」
    return await _update(request, admin, "biz_service_update", service_id, body.version, changes)


@router.post(
    "/{service_id}/monitored",
    summary="업무 서비스 감시 대상 켜기·끄기(관리자)",
    description=(
        "M3 상시 수집 대상 여부(`monitored`). version이 다르면 409 `version_conflict` · "
        "켜진 서비스가 이미 상한(`NOISE_BIZ_MONITOR_MAX_SERVICES`) 이상이면 409 "
        "`monitor_cap_reached`."
    ),
    tags=["admin"],
)
async def set_monitored(
    service_id: int,
    body: MonitoredBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    return await _update(request, admin, "biz_service_monitored", service_id, body.version,
                         {"monitored": body.monitored})


# ── 구성원 ──────────────────────────────────────────────────────────────
@router.post(
    "/{service_id}/members",
    status_code=201,
    summary="업무 서비스 구성원 수동 추가(관리자)",
    description=(
        "SMS 서버(db_id·resource_id) · APM 인스턴스(source_id·domain_id·scope·instance_name) · "
        "APM 도메인 · 제니퍼 업무(business_id). origin manual · confirmed. "
        "이미 다른 서비스면 409 `member_exists`."
    ),
    tags=["admin"],
)
async def add_member(
    service_id: int,
    body: MemberAdd,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{member, display_source, service, cache_ok, cache_reason, audit_logged}`."""
    from noise_gate.application.biz_service_admin import build_member

    _assert_admin(request, admin)
    repo = _repo(request)
    action = "biz_service_member_add"
    built: dict[str, Any] = {}

    def _checks() -> None:
        if body.kind == "sms_server":
            _check_db_id(body.db_id)
        try:
            built["key"], built["display"] = build_member(
                body.kind, db_id=body.db_id, resource_id=body.resource_id,
                source_id=body.source_id, domain_id=body.domain_id, scope=body.scope,
                instance_name=body.instance_name, business_id=body.business_id,
            )
        except Exception as exc:  # noqa: BLE001 — BizServiceAdminError → 400
            raise _http_error(exc) from None

    before = await _prepared(request, admin, action, service_id, body.version, _checks)
    display: dict[str, Any] = dict(built["display"])
    source = "key"
    if body.kind == "sms_server":
        snap, source = await _display(request).sms(body.db_id, body.resource_id)
        if source != "snapshot":
            snap.update({k: getattr(body, k) or None for k in ("server_name", "hostname", "ip")})
            source = "request"
        display.update(snap)
    elif body.domain_name:
        display["domain_name"] = body.domain_name

    async def _run() -> dict[str, Any]:
        return dict(await repo.add_member(
            service_id, kind=body.kind, member_key=built["key"], display=display,
            layer=body.layer, version=body.version, by=_by(admin),
        ))

    row = await _guard(request, admin, action, _run, before=_service_view(before),
                       member_key=built["key"])
    service = await _get_service(request, service_id)
    cache = await _sync_cache(request)
    logged = await _audit(request, admin, action, "ok", before=_service_view(before),
                          after=_member_view(row))
    return {"member": row, "display_source": source, "service": service, **cache,
            "audit_logged": logged}


async def _members_op(
    request: Request,
    admin: dict[str, Any],
    action: str,
    service_id: int,
    version: int,
    call: Callable[[], Awaitable[int]],
    **fields: Any,
) -> dict[str, Any]:
    before = await _prepared(request, admin, action, service_id, version)
    changed = await _guard(request, admin, action, call, before=_service_view(before), **fields)
    service = await _get_service(request, service_id)
    cache = await _sync_cache(request)
    logged = await _audit(request, admin, action, "ok", before=_service_view(before),
                          after=_service_view(service), changed=changed, **fields)
    return {"changed": changed, "service": service, **cache, "audit_logged": logged}


@router.post(
    "/{service_id}/members/state",
    summary="업무 서비스 구성원 제외·제외 취소(관리자)",
    description="state=excluded(제외 — 재계산이 되돌리지 않는다)·active(제외 취소) · ≤200건.",
    tags=["admin"],
)
async def set_members_state(
    service_id: int,
    body: MembersStateBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{changed, service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    return await _members_op(
        request, admin, "biz_service_member_state", service_id, body.version,
        lambda: repo.set_members_state(service_id, body.member_ids, state=body.state,
                                       version=body.version, by=_by(admin)),
        member_ids=body.member_ids, state=body.state,
    )


@router.post(
    "/{service_id}/members/confirm",
    summary="업무 서비스 구성원 확인·확인 취소(관리자)",
    description="confirmed=true(확인 — confirmed)·false(확인 취소 — inferred) · ≤200건.",
    tags=["admin"],
)
async def set_members_confirmed(
    service_id: int,
    body: MembersConfirmBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{changed, service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    return await _members_op(
        request, admin, "biz_service_member_confirm", service_id, body.version,
        lambda: repo.set_members_confirmed(service_id, body.member_ids,
                                           confirmed=body.confirmed, version=body.version,
                                           by=_by(admin)),
        member_ids=body.member_ids, confirmed=body.confirmed,
    )


@router.post(
    "/{service_id}/members/move",
    summary="업무 서비스 구성원 다른 서비스로 옮기기(관리자)",
    description=(
        "옮긴 구성원은 origin manual(재계산이 되돌리지 않는다) · 두 서비스 version 모두 확인."
    ),
    tags=["admin"],
)
async def move_members(
    service_id: int,
    body: MembersMoveBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{changed, service, to_service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    out = await _members_op(
        request, admin, "biz_service_member_move", service_id, body.version,
        lambda: repo.move_members(body.member_ids, from_service_id=service_id,
                                  to_service_id=body.to_service_id, from_version=body.version,
                                  to_version=body.to_version, by=_by(admin)),
        member_ids=body.member_ids, to_service_id=body.to_service_id,
    )
    out["to_service"] = await _get_service(request, body.to_service_id)
    return out


# ── 병합 · 분할 ─────────────────────────────────────────────────────────
@router.post(
    "/{service_id}/merge",
    summary="업무 서비스 병합(관리자)",
    description=(
        "이 서비스(A)의 구성원 전부를 `into_service_id`(B)로 옮기고 A를 비활성으로 둡니다"
        "(근거에 병합 기록 · "
        "한 트랜잭션). 두 서비스 version이 다르면 409 `version_conflict`."
    ),
    tags=["admin"],
)
async def merge_service(
    service_id: int,
    body: MergeBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{moved, from_version, to_version, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    action = "biz_service_merge"
    before = await _prepared(request, admin, action, service_id, body.version)

    async def _run() -> dict[str, Any]:
        return dict(await repo.merge_services(
            service_id, body.into_service_id, from_version=body.version,
            to_version=body.into_version, by=_by(admin),
        ))

    out = await _guard(request, admin, action, _run, before=_service_view(before),
                       into_service_id=body.into_service_id)
    cache = await _sync_cache(request)
    logged = await _audit(
        request, admin, action, "ok", before=_service_view(before),
        after={"from_service_id": service_id, "to_service_id": body.into_service_id,
               "moved": out.get("moved"), "from_version": out.get("from_version"),
               "to_version": out.get("to_version")},
    )
    return {"moved": out.get("moved"), "from_version": out.get("from_version"),
            "to_version": out.get("to_version"), **cache, "audit_logged": logged}


@router.post(
    "/{service_id}/split",
    status_code=201,
    summary="업무 서비스 분할(관리자)",
    description=(
        "선택 구성원을 새 수동 서비스로 옮깁니다(한 트랜잭션). "
        "새 서비스 키가 있으면 409 `exists`."
    ),
    tags=["admin"],
)
async def split_service(
    service_id: int,
    body: SplitBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{service, moved, from_version, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    action = "biz_service_split"
    before = await _prepared(
        request, admin, action, service_id, body.version,
        lambda: _check_pii({"owner_org": body.owner_org, "description": body.description}),
    )

    async def _run() -> dict[str, Any]:
        return dict(await repo.split_service(
            service_id, body.member_ids, version=body.version, service_key=body.service_key,
            display_name=body.display_name, tier=body.tier, description=body.description,
            owner_org=body.owner_org, by=_by(admin),
        ))

    out = await _guard(request, admin, action, _run, before=_service_view(before),
                       member_ids=body.member_ids)
    cache = await _sync_cache(request)
    logged = await _audit(request, admin, action, "ok", before=_service_view(before),
                          after={**(_service_view(out.get("service")) or {}),
                                 "moved": out.get("moved")}, member_ids=body.member_ids)
    return {"service": out.get("service"), "moved": out.get("moved"),
            "from_version": out.get("from_version"), **cache, "audit_logged": logged}


# ── APM 대조 · 게이트웨이 업무 매핑 사본 ────────────────────────────────
def _gateway_caller(config: Any) -> Any:
    """게이트웨이 호출기(MCP 경계 · D-274) — 주소(`MCP_SOURCE_ENDPOINTS["apm"]`)가 없으면 None."""
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_scanners import GatewayToolCaller

    client = build_apm_gateway_client(config)
    return GatewayToolCaller(client) if client is not None else None


@router.post(
    "/{service_id}/apm-compare",
    summary="업무 서비스 APM 대조(관리자)",
    description=(
        "서비스 표시명으로 게이트웨이 업무명 해석을 1회 부르고 APM 구성원과의 차이"
        "(해석에만 · 구성원에만)를 돌려줍니다. 해석에만 있는 인스턴스는 "
        "후보(근거 gateway_business)로만 남기고 구성원은 바꾸지 않습니다."
    ),
    tags=["admin"],
)
async def apm_compare(
    service_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{business, gateway, matched[], only_in_gateway[], only_in_members[], candidates,
    audit_logged}`."""
    from noise_gate.application.biz_service_admin import apm_compare as _compare

    _assert_admin(request, admin)
    repo = _repo(request)
    action = "biz_service_apm_compare"
    before = await _get_service(request, service_id)

    async def _run() -> dict[str, Any]:
        caller = _gateway_caller(request.app.state.config)
        return await _compare(repo, caller, before, by=_by(admin))

    out = await _guard(request, admin, action, _run, before=_service_view(before))
    out["audit_logged"] = await _audit(
        request, admin, action, "ok", before=_service_view(before),
        after={"matched": len(out["matched"]), "only_in_gateway": len(out["only_in_gateway"]),
               "only_in_members": len(out["only_in_members"]), "candidates": out["candidates"],
               "gateway_status": out["gateway"].get("status")},
    )
    return out


@router.get(
    "/{service_id}/gateway-business-map",
    summary="게이트웨이 업무 매핑 사본(관리자)",
    description=(
        "확정 APM 인스턴스 구성원 → 게이트웨이 `business_map` YAML 텍스트(미리보기·복사용). "
        "본체는 게이트웨이 파일을 쓰지 않습니다 — 붙여 넣을지는 관리자가 정합니다."
    ),
    tags=["admin"],
)
async def gateway_business_map(
    service_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{service_id, yaml, entries[], instance_count, skipped[]}`."""
    from noise_gate.application.biz_service_admin import gateway_business_map as _yaml

    _assert_admin(request, admin)
    row = await _get_service(request, service_id)
    return {"service_id": service_id, **_yaml(row)}


# ── (W5) 일괄 지정 · 계층 일괄 · 변경 이력 ──────────────────────────────
class BulkServer(BaseModel):
    db_id: str = Field(..., min_length=1, max_length=64, pattern=_DB_ID_PATTERN)
    resource_id: str = Field(..., min_length=1, max_length=19, pattern=_RESOURCE_ID_PATTERN)


class BulkAssignBody(BaseModel):
    """미소속 서버 → 서비스(다른 서비스 소속이면 옮긴다)."""

    service_id: int = Field(..., ge=1)
    version: int = Field(..., ge=1)
    servers: list[BulkServer] = Field(..., min_length=1, max_length=_MAX_BULK)


class MembersLayerBody(MembersBody):
    layer: _Layer | None


@router.post(
    "/bulk-assign",
    summary="서버 일괄 지정(관리자 · 검토함)",
    description=(
        "SMS 서버 ≤200대를 한 서비스 구성원으로(한 트랜잭션 · origin manual · confirmed). 다른 "
        "서비스 소속이면 옮깁니다. 행마다 감사 1줄 + 묶음 id. version이 다르면 409 "
        "`version_conflict`."
    ),
    tags=["admin"],
)
async def bulk_assign(
    body: BulkAssignBody, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{batch_id, service_id, results[{db_id, resource_id, member_id, outcome, moved_from}],
    counts{added, moved, kept}, service, cache_ok, cache_reason, audit_logged}`."""
    from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, sms_member_key

    _assert_admin(request, admin)
    repo = _repo(request)
    action = "biz_service_bulk_assign"
    batch_id = uuid.uuid4().hex
    servers = list(dict.fromkeys((s.db_id, s.resource_id) for s in body.servers))

    def _checks() -> None:
        for db_id in sorted({d for d, _ in servers}):
            _check_db_id(db_id)

    before = await _prepared(request, admin, action, body.service_id, body.version, _checks)
    display = _display(request)
    items: list[tuple[str, str, dict[str, Any]]] = []
    for db_id, rid in servers:
        shown, _source = await display.sms(db_id, rid)
        items.append((KIND_SMS_SERVER, str(sms_member_key(db_id, rid)), shown))

    async def _run() -> list[dict[str, Any]]:
        return list(await repo.assign_members(body.service_id, items, version=body.version,
                                              by=_by(admin)))

    rows = await _guard(request, admin, action, _run, before=_service_view(before),
                        batch_id=batch_id, service_id=body.service_id)
    results: list[dict[str, Any]] = []
    logged = True
    for row in rows:
        member = row.get("member") or {}
        moved_from = row.get("moved_from")
        logged = await _audit(
            request, admin, action, "ok",
            before={"service_id": moved_from} if moved_from is not None else None,
            after=_member_view(member), batch_id=batch_id, service_id=body.service_id,
            assign=row.get("outcome"),
        ) and logged
        results.append({"db_id": member.get("db_id"), "resource_id": member.get("resource_id"),
                        "member_id": member.get("id"), "outcome": row.get("outcome"),
                        "moved_from": moved_from})
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in ("added", "moved", "kept")}
    service = await _get_service(request, body.service_id)
    cache = await _sync_cache(request)
    return {"batch_id": batch_id, "service_id": body.service_id, "results": results,
            "counts": counts, "service": service, **cache, "audit_logged": logged}


@router.patch(
    "/{service_id}/members/layer",
    summary="업무 서비스 구성원 계층 일괄 지정(관리자)",
    description=(
        "선택 구성원 ≤200건의 계층(web·was·db·batch·etc · null = 미지정)을 바꿉니다"
        "(layer_origin manual — 재계산이 덮지 않는다). version이 다르면 409 `version_conflict`."
    ),
    tags=["admin"],
)
async def set_members_layer(
    service_id: int,
    body: MembersLayerBody,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{changed, service, cache_ok, cache_reason, audit_logged}`."""
    _assert_admin(request, admin)
    repo = _repo(request)
    return await _members_op(
        request, admin, "biz_service_member_layer", service_id, body.version,
        lambda: repo.set_members_layer(service_id, body.member_ids, layer=body.layer,
                                       version=body.version, by=_by(admin)),
        member_ids=body.member_ids, layer=body.layer,
    )


@router.get(
    "/{service_id}/history",
    summary="업무 서비스 변경 이력(관리자)",
    description=(
        "이 서비스의 관리 동작 감사 기록(before/after) 최근 50건. 감사 저장소(DB)가 없으면 사유를 "
        "돌려줍니다. 최근 감사 기록 1,000건 안에서 찾습니다(`scanned`)."
    ),
    tags=["admin"],
)
async def service_history(
    service_id: int,
    request: Request,
    limit: int = Query(50, ge=1, le=50),
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{service_id, rows[{at, user_id, action, outcome, reason, batch_id, before, after}],
    scanned, reason}`."""
    from noise_gate.application.biz_service_view import history_rows
    from src.domain.audit import AuditEvent

    _assert_admin(request, admin)
    await _get_service(request, service_id)
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo is None or not hasattr(audit_repo, "query_logs_paginated"):
        return {"service_id": service_id, "rows": [], "scanned": 0,
                "reason": "감사 저장소(DB)가 없어 변경 이력을 읽을 수 없습니다"
                          "(감사 로그는 파일에만 남습니다)."}
    try:
        logs, _total = await audit_repo.query_logs_paginated(
            event_type=AuditEvent.ADMIN_ACTION.value, keyword="biz_service", page=1,
            page_size=_HISTORY_SCAN,
        )
    except Exception as exc:  # noqa: BLE001
        raise _http_error(exc) from None
    return {"service_id": service_id, "rows": history_rows(logs, service_id, limit=limit),
            "scanned": len(logs), "reason": None}


# ── (W5b) CSV 가져오기 ──────────────────────────────────────────────────
class ImportApplyBody(BaseModel):
    preview_id: str = Field(..., pattern=_RUN_ID_PATTERN)


def _import_store(request: Request) -> Any:
    """미리보기 계획 보관소(앱마다 1개 · 처음 쓸 때 만든다)."""
    from noise_gate.application.biz_service_import import ImportPreviewStore

    store = getattr(request.app.state, "biz_service_import_store", None)
    if store is None:
        store = ImportPreviewStore()
        request.app.state.biz_service_import_store = store
    return store


#: (W5b) 요청 본문 상한 — 파일 상한 + multipart 머리·경계 여유분. 이 값을 넘으면 본문을 다 받기
#: 전에 413으로 끊는다(파일 자체의 1MB 판정은 그 뒤 `parse_csv`가 한다).
_IMPORT_MULTIPART_SLACK = 64 * 1024


def _import_body_limit() -> int:
    from noise_gate.application.biz_service_import import MAX_BYTES

    return MAX_BYTES + _IMPORT_MULTIPART_SLACK


def _check_content_length(request: Request, limit: int) -> None:
    """`Content-Length`가 상한을 넘으면 본문을 읽기 전에 413(헤더가 없거나 숫자가 아니면 통과 —
    수신 중 계수가 막는다)."""
    from noise_gate.application.biz_service_import import too_large_error

    raw = request.headers.get("content-length")
    if raw is not None and raw.strip().isdigit() and int(raw) > limit:
        raise too_large_error()


async def _capped_stream(request: Request, limit: int) -> AsyncGenerator[bytes, None]:
    """요청 본문 스트림 — 누적 바이트가 상한을 넘는 순간 413(헤더 없음·chunked·거짓 길이도)."""
    from noise_gate.application.biz_service_import import too_large_error

    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise too_large_error()
        yield chunk


async def _read_upload(request: Request) -> bytes:
    """multipart `file` 1개를 상한 안에서 받아 바이트로(최대 `MAX_BYTES + 1` — 판정은 `parse_csv`).

    FastAPI `UploadFile` 인자는 인가·핸들러보다 먼저 본문 전체를 파싱하므로 쓰지 않고, 인가 뒤에
    여기서 직접 파싱한다.
    """
    from starlette.datastructures import UploadFile as StarletteUploadFile
    from starlette.formparsers import MultiPartException, MultiPartParser

    from noise_gate.application.biz_service_import import MAX_BYTES

    limit = _import_body_limit()
    _check_content_length(request, limit)
    content_type = request.headers.get("content-type") or ""
    if not content_type.lower().startswith("multipart/form-data"):
        raise _error(400, "invalid_request", "CSV 파일을 multipart `file` 칸으로 보내세요.")
    parser = MultiPartParser(request.headers, _capped_stream(request, limit),
                             max_files=1, max_fields=10)
    try:
        form = await parser.parse()
    except MultiPartException:
        raise _error(
            400, "invalid_request",
            "multipart 본문을 읽지 못했습니다 — CSV 파일 1개만 `file` 칸으로 보내세요.",
        ) from None
    try:
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile):
            raise _error(400, "invalid_request", "CSV 파일을 multipart `file` 칸으로 보내세요.")
        return await upload.read(MAX_BYTES + 1)
    finally:
        await form.close()


@router.post(
    "/import/preview",
    summary="CSV 가져오기 미리보기(관리자)",
    description=(
        "multipart `file`(UTF-8 CSV · BOM 허용 · ≤1MB · 데이터 행 ≤5,000 · 머리 행 = 내보내기 "
        "템플릿). 행마다 현재 스냅샷과 대조해 추가·옮김·이미 소속·스냅샷에 없음·고르지 않음·"
        "등록되지 않은 DB·파일 안 충돌 등을 돌려줍니다(쓰기 0). 적용은 `preview_id`로. "
        "본문이 상한(1MB + multipart 여유분)을 넘으면 다 받기 전에 413 `import_too_large`."
    ),
    tags=["admin"],
    openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
        "schema": {"type": "object", "required": ["file"],
                   "properties": {"file": {"type": "string", "format": "binary"}}},
    }}}},
)
async def import_preview(
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{preview_id, expires_in, snapshot_run_id, rows[], counts{}, services[], moves[], creates,
    applicable, labels{}, audit_logged}`.

    본문은 인가(`require_admin_user` + role 재검증) 뒤에만 받는다 — `_read_upload`.
    """
    from noise_gate.application.biz_service import registry_zone
    from noise_gate.application.biz_service_import import build_preview, parse_csv

    _assert_admin(request, admin)
    action = "biz_service_import_preview"

    async def _run() -> Any:
        repo, maps = _repo(request), _map_repo(request)
        rows = parse_csv(await _read_upload(request))
        return await build_preview(rows, repo=repo, maps=maps, known_db_ids=_known_db_ids(),
                                   pii_rules=_pii_rules, zone_of=registry_zone)

    plan = await _guard(request, admin, action, _run)
    store = _import_store(request)
    preview_id = store.put(_by(admin), plan)
    logged = await _audit(request, admin, action, "ok",
                          after={"preview_id": preview_id, "rows": len(plan.view["rows"]),
                                 "counts": plan.view["counts"], "creates": len(plan.creates),
                                 "snapshot_run_id": plan.snapshot_run_id})
    return {"preview_id": preview_id, "expires_in": store.ttl_seconds, **plan.view,
            "audit_logged": logged}


@router.post(
    "/import/apply",
    summary="CSV 가져오기 적용(관리자)",
    description=(
        "미리보기 계획을 한 트랜잭션으로 저장합니다(origin manual · confirmed). 미리보기 뒤 "
        "스냅샷이 바뀌면 409 `snapshot_changed`, 관계 서비스가 바뀌면 409 `version_conflict`, "
        "미리보기가 없거나 만료되면 404 `preview_not_found`. 행마다 감사 1줄 + 묶음 id."
    ),
    tags=["admin"],
)
async def import_apply(
    body: ImportApplyBody, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{batch_id, preview_id, created[{service_id, service_key, display_name}],
    results[{line, service_key, service_id, db_id, resource_id, member_id, outcome, moved_from}],
    counts{added, moved, kept}, cache_ok, cache_reason, audit_logged}`."""
    from noise_gate.application.biz_service_import import apply_import

    _assert_admin(request, admin)
    action = "biz_service_import"
    batch_id = uuid.uuid4().hex
    store = _import_store(request)

    async def _run() -> dict[str, Any]:
        repo, maps = _repo(request), _map_repo(request)
        plan = store.get(body.preview_id, _by(admin))
        return dict(await apply_import(plan, repo=repo, maps=maps, by=_by(admin)))

    out = await _guard(request, admin, action, _run, batch_id=batch_id,
                       before={"preview_id": body.preview_id})
    store.drop(body.preview_id)
    logged = True
    created = []
    for row in out.get("created") or []:
        logged = await _audit(request, admin, action, "ok", after=_service_view(row),
                              batch_id=batch_id, service_id=row.get("id"),
                              assign="service_created") and logged
        created.append({"service_id": row.get("id"), "service_key": row.get("service_key"),
                        "display_name": row.get("display_name")})
    results: list[dict[str, Any]] = []
    for row in out.get("members") or []:
        member = row.get("member") or {}
        moved_from = row.get("moved_from")
        logged = await _audit(
            request, admin, action, "ok",
            before={"service_id": moved_from} if moved_from is not None else None,
            after=_member_view(member), batch_id=batch_id, service_id=member.get("service_id"),
            assign=row.get("outcome"), line=row.get("line"),
        ) and logged
        results.append({"line": row.get("line"), "service_key": row.get("service_key"),
                        "service_id": member.get("service_id"), "db_id": member.get("db_id"),
                        "resource_id": member.get("resource_id"), "member_id": member.get("id"),
                        "outcome": row.get("outcome"), "moved_from": moved_from})
    counts = {k: sum(1 for r in results if r["outcome"] == k) for k in ("added", "moved", "kept")}
    cache = await _sync_cache(request)
    return {"batch_id": batch_id, "preview_id": body.preview_id, "created": created,
            "results": results, "counts": counts, **cache, "audit_logged": logged}
