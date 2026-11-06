"""업무 서비스 심층 조사 D3 — 접수·진단 이력 (plans/215 §4.9 · D-368).

보드 서비스 상세의 [서비스 심층 조사]가 부르는 접수 로직이다. 라우트(interface)는 로그인·플래그·
서비스 사본·D1 결과·요청자 정보를 모아 넘기고, 응답 상태·본문·감사를 이 함수의 결과대로 낸다.

- **대표 서버**: D1이 고른 대표 1대(`representative`) — 없으면 `no_representative`로 거부
  (D1 사유 노출).
- **인가**: `authorize_host_investigation`(D-335 ⑤)을 대표 서버에 적용한다(실행 경계 — 여기서 판정).
- **같은 서비스 진행 중**(서비스 키 `biz:{service_id}`)이면 새로 접수하지 않고 기존 조사 id를
  돌려준다 — 요청자가 열 수 있는 조사(소유자·관리자)만. 남의 조사면 id 없이 거부한다
  (존재 노출 금지).
- **상한**: 사용자별 진행 1·시간당 N(추가 조사·다시 분석과 공유 · D-335 ⑥ · D-347 ②)은 호출부가
  주입하는 임계구역(`limit_slot`)이 판정한다. 전역 시간당 예산은 조사 서비스가 1건으로 센다.
- **질문은 코드 템플릿**(사용자 자유 텍스트 0 · D-335 ④). `reference_time`=D1 최초 이상 시각 ·
  `lookback_minutes`=D1 창 · `prior_findings`=D1 판정 요약(서술 없음) · `context.biz_service`=D2와
  같은 모양(`build_biz_service_context`) — 비교 구성원은 요청자 존 안만 싣고 존 밖은 건수만 남긴다.
- **저장**: 본체 `InvestigationStore` `kind=diagnosis` + `subject{type: "biz_service", …}`(D-335 ⑦).

LLM 호출 0 · DB 접근 0. 감사 기록·상한 임계구역·미종결 재조회는 호출부 주입(콜백 · 계층 규칙).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from src.domain.host_authz import Principal, authorize_host_investigation
from src.domain.investigation_followup import HYPOTHESIS_NOTICE
from src.domain.investigation_reject_reasons import reject_display_text

from noise_gate.application.biz_investigation_context import member_zone
from noise_gate.domain.biz_service_context import BizTarget, build_biz_service_context
from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER, sms_member_key
from noise_gate.infrastructure.investigation_store import (
    ACTIVE_STATUSES,
    KIND_DIAGNOSIS,
    InvestigationStore,
    investigation_view,
)

logger = logging.getLogger(__name__)

#: 저장 레코드의 `origin`·`source`와 감사 진입점.
ORIGIN_BIZ_SERVICE = "biz_service"
ENTRY_POINT = "biz_service"
#: 저장 레코드 `subject.type`.
SUBJECT_TYPE = "biz_service"
#: `prior_findings.source`(D1 판정 요약).
PRIOR_SOURCE = "biz_d1"
#: 진단 이력 기본 건수(보드 상세 「진단 이력」 · §4.9).
HISTORY_LIMIT = 10

# ── D1 결과에서 읽는 키(W9 `noise_gate.domain.biz_diagnosis.diagnose_service` 출력) ─────────
#: 대표 `{kind, member_key, name, db_id, resource_id, rule, rule_label}` | None(→ 사유)
D1_REPRESENTATIVE = "representative"
D1_REPRESENTATIVE_REASON = "representative_reason"
D1_VERDICT = "verdict"
#: 타임라인 원소 `{member_key, kind, name, layer, at, basis, label, time_basis}` — `at` 최솟값이
#: 최초 이상 시각이다.
D1_TIMELINE = "timeline"
D1_TIMELINE_AT = "at"
#: 이상 구성원 `{kind, member_key, …, first_at, …}`(타임라인이 비었을 때 `first_at`으로 대신 읽는다)
D1_ANOMALOUS = "anomalous_members"
D1_ANOMALOUS_AT = "first_at"
#: 결손 `{code: no_link|link_outside|no_metric|out_of_scope, label, count, member_keys}`
D1_GAPS = "gaps"

# ── 거부 사유 코드(라우트 `detail.code`) ─────────────────────────────────────────────
REASON_DISABLED = "biz_diagnosis_disabled"
REASON_SERVICE_NOT_FOUND = "service_not_found"
REASON_NO_REPRESENTATIVE = "no_representative"
REASON_AUTHZ_DENIED = "authz_denied"
REASON_SERVICE_IN_FLIGHT = "service_investigation_in_flight"
REASON_SERVICE_UNCONFIGURED = "service_unconfigured"
REASON_SERVICE_UNREACHABLE = "service_unreachable"
REASON_SUBMIT_FAILED = "submit_failed"
#: 상한 임계구역의 「요청자 식별 불가」 코드
#: (`investigation_request_limit.REASON_OWNER_UNKNOWN`과 같은 값).
_OWNER_UNKNOWN = "owner_unknown"

#: 감사 outcome 값(`src.security.audit_logger.INVESTIGATION_*`와 같은 문자열).
AUDIT_OK = "ok"
AUDIT_FAILED = "failed"
AUDIT_DENIED = "denied"

#: 질문 템플릿(코드 고정 · 사용자 자유 텍스트 0 — D-335 ④).
#: 값은 서비스 id·대표 서버 이름·창(분)뿐이다.
QUESTION_TEMPLATE = (
    "업무 서비스 심층 조사(서비스 id {service_id}): 대표 서버 {target} 기준 "
    "최근 {window}분 구간에서 "
    "같은 업무 서비스 구성원(조사 맥락의 비교 구성원)과 비교해 이상 원인 가설을 정리해 주세요."
)

#: 존 밖 구성원 건수를 세는 계산의 상한(사실상 무제한 — 건수만 쓴다).
_COUNT_CAP = 1_000_000
#: `prior_findings` 토큰 형식(sre_agent 화이트리스트와 같은 규칙).
_TOKEN_RE = re.compile(r"^[0-9A-Za-z가-힣_.\-/]{1,48}$")
_D1_VERDICT_RE = re.compile(r"^R[1-6]$")

#: 감사 콜백 — `await audit(outcome=…, targets=…, authz=…, degraded=…, **extra)`.
AuditFn = Callable[..., Awaitable[None]]
#: 사용자별 상한 임계구역 — 블록 안에서 `(code, reason)`(거부) 또는 None(통과)을 내준다.
LimitSlotFn = Callable[[], AbstractAsyncContextManager["tuple[str, str] | None"]]
#: 미종결 레코드 재조회 — `(store, rec, ng) → (최신 레코드, 실패 사유|None)`.
RefreshFn = Callable[[Any, dict[str, Any], Any], Awaitable["tuple[dict[str, Any], str | None]"]]


@dataclass(frozen=True)
class BizRequester:
    """접수 요청자.

    Attributes:
        sub: 사용자 식별자(JWT `sub` — 저장 `owner_sub`)
        role: 역할 클레임(인가 판정 재료)
        allowed_db_ids: 조회 인가 DB 목록(None = 제한 없음 — 기존 규약)
        zones: 알람 존 권한(`alarm_zones_for_user`) — 비교 구성원 필터(None = 거르지 않음)
        is_admin: 관리자(운영자 뷰) 여부 — 남의 진행 중 조사 id·이력 전체를 볼 수 있다
    """

    sub: str
    role: str | None
    allowed_db_ids: list[str] | None = None
    zones: frozenset[str] | None = None
    is_admin: bool = False


@dataclass(frozen=True)
class BizInvestigationResult:
    """접수 결과 — `http_status`가 200이면 `body`가 응답, 아니면 `detail{code, reason…}`."""

    http_status: int
    body: dict[str, Any]

    @property
    def ok(self) -> bool:
        """정상 응답(새 접수·기존 조사 재사용·조사 서비스 거부 기록) 여부."""
        return self.http_status == 200


def diagnosis_active(cfg: Any) -> bool:
    """D3 플래그 유효 여부 — 정확히 True일 때만(`MagicMock` 설정 오판 방지)."""
    ng = getattr(cfg, "noise_gate", None)
    return getattr(ng, "biz_diagnosis_active", False) is True


def service_investigation_key(service_id: int) -> str:
    """서비스 조사 키 `biz:{service_id}`(같은 서비스 진행 중 판정 · 저장 `subject.service_key`)."""
    return f"biz:{int(service_id)}"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _service_member(
    service: Mapping[str, Any] | None, member_key: str, db_id: str, resource_id: str
) -> Mapping[str, Any] | None:
    """서비스 사본에서 대표 SMS 구성원 행 — `member_key`, 없으면 `(db_id, resource_id)`로 찾는다."""
    rows = [
        m for m in ((service or {}).get("members") or ())
        if isinstance(m, Mapping) and _text(m.get("kind")) == KIND_SMS_SERVER
    ]
    for m in rows:
        if member_key and _text(m.get("member_key")) == member_key:
            return m
    for m in rows:
        if db_id and resource_id and (_text(m.get("db_id")), _text(m.get("resource_id"))) == (
            db_id, resource_id,
        ):
            return m
    return None


def representative_of(
    d1: Mapping[str, Any] | None, service: Mapping[str, Any] | None = None
) -> tuple[dict[str, str] | None, str]:
    """D1 대표 → (대표 서버 `{db_id, resource_id, server_name, hostname}` | None, 없음 사유).

    D1 대표는 이름(`name`)만 싣고 `server_name`·`hostname`은 없다 — 서비스 사본의 구성원 행에서
    찾는다(못 찾으면 `name`을 server_name으로). db_id와 이름이 없으면 D1 사유(없으면 기본 문구).
    """
    raw = d1.get(D1_REPRESENTATIVE) if isinstance(d1, Mapping) else None
    if isinstance(raw, Mapping):
        db_id, rid = _text(raw.get("db_id")), _text(raw.get("resource_id"))
        member = _service_member(service, _text(raw.get("member_key")), db_id, rid) or {}
        rep = {
            "db_id": db_id or _text(member.get("db_id")),
            "resource_id": rid or _text(member.get("resource_id")),
            "server_name": _text(member.get("server_name")) or _text(raw.get("name")),
            "hostname": _text(member.get("hostname")),
        }
        if rep["db_id"] and (rep["hostname"] or rep["server_name"]):
            return rep, ""
        return None, "D1 대표 서버의 식별자(db_id·서버 이름)를 서비스 구성원에서 찾지 못했습니다."
    reason = _text(d1.get(D1_REPRESENTATIVE_REASON)) if isinstance(d1, Mapping) else ""
    return None, reason or "D1 진단이 대표 서버를 고르지 못했습니다."


def _iso(value: Any) -> str | None:
    """ISO 8601 문자열·epoch 초 → ISO 8601(초 해상도). 못 읽으면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), UTC).isoformat(timespec="seconds")
        except (OverflowError, OSError, ValueError):
            return None
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).isoformat(timespec="seconds")
    except ValueError:
        return None


def first_anomaly_time(d1: Mapping[str, Any] | None) -> str | None:
    """D1 최초 이상 시각 — 타임라인 `at`의 최솟값, 타임라인이 비면 이상 구성원 `first_at` 최솟값."""
    if not isinstance(d1, Mapping):
        return None
    pairs = [(it, D1_TIMELINE_AT) for it in d1.get(D1_TIMELINE) or ()]
    if not pairs:
        pairs = [(it, D1_ANOMALOUS_AT) for it in d1.get(D1_ANOMALOUS) or ()]
    times: list[tuple[datetime, str]] = []
    for item, key_name in pairs:
        if not isinstance(item, Mapping):
            continue
        at = _iso(item.get(key_name))
        if at:
            parsed = datetime.fromisoformat(at)
            key = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            times.append((key, at))
    return min(times)[1] if times else None


def build_prior_findings(
    d1: Mapping[str, Any] | None,
    *,
    service_id: int,
    reference_time: str | None,
    lookback_minutes: int,
    compare_sms: int,
    compare_apm: int,
    excluded: int,
) -> dict[str, Any]:
    """D1 판정 요약 → `prior_findings`(sre_agent 화이트리스트 형식 · LLM 서술 0).

    판정 코드(R#)·이상 구성원 건수(SMS·APM)·비교 구성원 건수·존 밖 제외 건수·결손 코드만 싣는다.
    """
    pf: dict[str, Any] = {"version": 1, "source": PRIOR_SOURCE}
    if reference_time:
        pf["window"] = {"reference_time": reference_time, "lookback_minutes": lookback_minutes}
    verdict = _text(d1.get(D1_VERDICT)) if isinstance(d1, Mapping) else ""
    if _D1_VERDICT_RE.match(verdict):
        pf["verdict"] = {"level": verdict}
    anomalous = [
        m for m in (d1.get(D1_ANOMALOUS) or () if isinstance(d1, Mapping) else ())
        if isinstance(m, Mapping)
    ]
    readings: dict[str, Any] = {
        "service_id": int(service_id),
        "anomalous_members": len(anomalous),
        "anomalous_sms": sum(1 for m in anomalous if _text(m.get("kind")) == KIND_SMS_SERVER),
        "anomalous_apm": sum(1 for m in anomalous if _text(m.get("kind")) != KIND_SMS_SERVER),
        "compare_sms": compare_sms,
        "compare_apm": compare_apm,
        "excluded_out_of_zone": excluded,
    }
    for gap in (d1.get(D1_GAPS) or () if isinstance(d1, Mapping) else ()):
        code = _text(gap.get("code")) if isinstance(gap, Mapping) else _text(gap)
        key = f"gap_{code}"
        if not code or not _TOKEN_RE.match(key):
            continue
        count = gap.get("count") if isinstance(gap, Mapping) else None
        ok_count = isinstance(count, int) and not isinstance(count, bool)
        readings[key] = count if ok_count else True
    pf["readings"] = readings
    return pf


def build_question(service_id: int, target: str, window: int) -> str:
    """코드 템플릿 질문(사용자 입력 0)."""
    return QUESTION_TEMPLATE.format(service_id=int(service_id), target=target, window=int(window))


def _context_value(
    service: Mapping[str, Any],
    rep: Mapping[str, str],
    d1: Mapping[str, Any] | None,
    zones: Collection[str] | None,
    max_sms: int,
    max_apm: int,
) -> tuple[dict[str, Any] | None, int]:
    """`context.biz_service`(D2와 같은 모양) + 요청자 존 밖이라 뺀 비교 구성원 건수."""
    key = sms_member_key(rep["db_id"], rep["resource_id"])
    target = BizTarget(
        sms_keys=frozenset({key}) if key else frozenset(),
        hostname=rep["hostname"] or rep["server_name"],
        db_id=rep["db_id"],
    )

    def _build(allowed: Collection[str] | None, n_sms: int, n_apm: int) -> dict[str, Any] | None:
        return build_biz_service_context(
            service, target, allowed_zones=allowed, max_sms=n_sms, max_apm=n_apm,
            d1=d1, member_zone=member_zone,
        )

    value = _build(zones, max_sms, max_apm)
    if value is None or zones is None:
        return value, 0
    every = _build(None, _COUNT_CAP, _COUNT_CAP) or {}
    inside = _build(zones, _COUNT_CAP, _COUNT_CAP) or {}

    def _n(v: Mapping[str, Any]) -> int:
        return len(v.get("sms_members") or ()) + len(v.get("apm_members") or ())

    return value, max(0, _n(every) - _n(inside))


def _is_service_record(rec: Mapping[str, Any], service_id: int) -> bool:
    subject = rec.get("subject")
    return (
        isinstance(subject, Mapping)
        and subject.get("type") == SUBJECT_TYPE
        and _text(subject.get("service_id")) == str(int(service_id))
    )


async def _in_flight(
    store: InvestigationStore,
    service_id: int,
    ng: Any,
    refresh: RefreshFn | None,
) -> dict[str, Any] | None:
    """같은 서비스의 진행 중 조사(가장 최근 · 재조회 뒤에도 미종결인 것만)."""
    for rec in reversed(store.list_diagnoses()):
        if not _is_service_record(rec, service_id) or rec.get("status") not in ACTIVE_STATUSES:
            continue
        fresh = rec
        if refresh is not None:
            try:
                fresh, _ = await refresh(store, rec, ng)
            except Exception:  # noqa: BLE001 — 재조회 실패는 저장 상태로 판정한다(진행 중 유지)
                logger.warning("서비스 조사 재조회 실패: inv=%s", rec.get("investigation_id"),
                               exc_info=True)
        if fresh.get("status") in ACTIVE_STATUSES:
            return fresh
    return None


async def _audit(audit: AuditFn | None, outcome: str, **fields: Any) -> None:
    """감사 1건(실패해도 응답을 막지 않는다 — 사유는 로그)."""
    if audit is None:
        return
    try:
        await audit(outcome=outcome, **fields)
    except Exception:  # noqa: BLE001
        logger.warning("서비스 심층 조사 감사 기록 실패", exc_info=True)


#: 서비스별 접수 잠금 — `service_id → [잠금, 대기·보유 수]`. 수가 0이 되면 지운다(무한 증가 방지).
_SERVICE_LOCKS: dict[int, list[Any]] = {}


@asynccontextmanager
async def _service_lock(service_id: int) -> AsyncIterator[None]:
    """서비스 하나의 접수 임계구역(프로세스 안 · 이벤트 루프 단일 스레드 전제)."""
    entry = _SERVICE_LOCKS.setdefault(int(service_id), [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] <= 0 and _SERVICE_LOCKS.get(int(service_id)) is entry:
            _SERVICE_LOCKS.pop(int(service_id), None)


def _deny(status: int, code: str, reason: str, **extra: Any) -> BizInvestigationResult:
    return BizInvestigationResult(status, {"code": code, "reason": reason, **extra})


async def submit_biz_service_investigation(
    *,
    cfg: Any,
    service: Mapping[str, Any] | None,
    d1: Mapping[str, Any] | None,
    requester: BizRequester,
    store: InvestigationStore,
    client: Any,
    limit_slot: LimitSlotFn,
    audit: AuditFn | None = None,
    refresh: RefreshFn | None = None,
) -> BizInvestigationResult:
    """서비스 심층 조사 1건을 접수한다(§4.9 · 거부는 사유 코드와 함께 — 침묵 금지).

    Args:
        cfg: AppConfig(덕 타이핑) — `noise_gate.*`(창·구성원 상한·호출 타임아웃)·`host_authz.mode`
        service: 서비스 사본(`bizsvc:svc:{id}` — `id`·`display_name`·`members[]`) · None이면 404
        d1: D1 결과(대표·판정·최초 이상 시각·이상 구성원·결손)
        requester: 요청자(sub·역할·DB 권한·존 권한·관리자 여부)
        store: 본체 조사 저장소
        client: `SreAgentClient`(None = 조사 서비스 URL 미설정)
        limit_slot: 사용자별 상한 임계구역 팩토리(블록 안에서 접수·기록까지 끝낸다)
        audit: 감사 콜백(기존 조사 감사 경로 — `log_investigation` 래퍼)
        refresh: 미종결 레코드 재조회(같은 서비스 진행 중 판정 전 갱신 · 없으면 저장 상태 그대로)

    Returns:
        200 = `{investigation_id, status, reused, service_id, representative, excluded_members,
        truncated, hypothesis_notice[, reason, reason_text]}` · 그 밖 = `{code, reason}`.
    """
    if not diagnosis_active(cfg):
        return _deny(404, REASON_DISABLED, "서비스 심층 조사가 꺼져 있습니다.")
    try:
        service_id = int(service.get("id")) if service else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        service_id = None
    if service is None or service_id is None:
        return _deny(404, REASON_SERVICE_NOT_FOUND, "업무 서비스 사본을 찾지 못했습니다.")
    ng = cfg.noise_gate
    sub = requester.sub

    rep, rep_reason = representative_of(d1, service)
    if rep is None:
        await _audit(audit, AUDIT_DENIED, targets=None, authz=None,
                     degraded=[{"reason": REASON_NO_REPRESENTATIVE, "detail": rep_reason}],
                     service_id=service_id)
        return _deny(409, REASON_NO_REPRESENTATIVE, rep_reason)
    label = rep["hostname"] or rep["server_name"]
    targets = [{"server_name": rep["server_name"], "hostname": rep["hostname"],
                "db_id": rep["db_id"]}]

    decision = authorize_host_investigation(
        mode=getattr(getattr(cfg, "host_authz", None), "mode", None),
        principal=Principal(role=requester.role, user_id=sub or None,
                            allowed_db_ids=requester.allowed_db_ids, entry_point=ENTRY_POINT),
        hostname=label,
        db_id=rep["db_id"],
    )
    authz = decision.as_audit()

    async def _denied(status: int, code: str, reason: str, outcome: str = AUDIT_DENIED,
                      **extra: Any) -> BizInvestigationResult:
        await _audit(audit, outcome, targets=targets, authz=authz,
                     degraded=[{"reason": code, "detail": reason}], service_id=service_id)
        return _deny(status, code, reason, **extra)

    if not decision.allowed:
        return await _denied(403, REASON_AUTHZ_DENIED,
                             f"서비스 심층 조사를 요청할 권한이 없습니다(사유: {decision.reason}).")

    # 「같은 서비스 진행 중 확인 → 제출 → 저장」을 서비스별 잠금 한 구간으로 — 서로 다른 사용자의
    # 동시 접수가 둘 다 새 조사를 내지 않게 한다(프로세스 안 한정 · 다중 워커 간은 잔여).
    async def _admit() -> BizInvestigationResult:
        rep_view = {k: rep[k] for k in ("db_id", "server_name", "hostname")}
        existing = await _in_flight(store, service_id, ng, refresh)
        if existing is not None:
            if requester.is_admin or (sub and existing.get("owner_sub") == sub):
                # (보안 L1) 기존 조사 재사용도 감사 1줄을 남긴다.
                await _audit(audit, AUDIT_OK, targets=targets, authz=authz, degraded=None,
                             investigation_id=existing.get("investigation_id"),
                             service_id=service_id, reused=True)
                return BizInvestigationResult(200, {
                    "investigation_id": existing.get("investigation_id"),
                    "status": existing.get("status"),
                    "reused": True,
                    "service_id": service_id,
                    "representative": rep_view,
                    "hypothesis_notice": HYPOTHESIS_NOTICE,
                })
            return await _denied(
                409, REASON_SERVICE_IN_FLIGHT,
                "같은 업무 서비스를 다른 사용자가 조사 중입니다 — 끝난 뒤 다시 요청하세요.",
            )

        if client is None:
            return await _denied(503, REASON_SERVICE_UNCONFIGURED,
                                 "조사 서비스 URL이 설정되지 않았습니다.", AUDIT_FAILED)
        unreachable = await client.unreachable_reason()
        if unreachable:
            return await _denied(503, REASON_SERVICE_UNREACHABLE,
                                 f"조사 서비스에 연결할 수 없습니다: {unreachable}", AUDIT_FAILED)

        window = int(getattr(ng, "biz_diagnosis_window_minutes", 15))
        async with limit_slot() as limited:
            if limited is not None:
                code, limit_reason = limited
                return await _denied(403 if code == _OWNER_UNKNOWN else 429, code, limit_reason)

            biz, excluded = _context_value(
                service, rep, d1, requester.zones,
                int(getattr(ng, "biz_investigation_max_sms", 10)),
                int(getattr(ng, "biz_investigation_max_apm", 8)),
            )
            reference_time = first_anomaly_time(d1)
            prior = build_prior_findings(
                d1, service_id=service_id, reference_time=reference_time, lookback_minutes=window,
                compare_sms=len((biz or {}).get("sms_members") or ()),
                compare_apm=len((biz or {}).get("apm_members") or ()),
                excluded=excluded,
            )
            question = build_question(service_id, label, window)
            timeout = float(getattr(ng, "investigation_mcp_call_timeout_seconds", 10.0))

            async def _submit() -> dict[str, Any]:
                await client.connect()
                try:
                    sent: dict[str, Any] = await client.diagnose(
                        question,
                        server_name=rep["server_name"] or None,
                        hostname=rep["hostname"] or None,
                        db_id=rep["db_id"],
                        reference_time=reference_time,
                        lookback_minutes=window,
                        prior_findings=prior,
                        context={"biz_service": biz} if biz else None,
                    )
                finally:
                    await client.disconnect()
                return sent

            try:
                res = await asyncio.wait_for(_submit(), timeout=timeout)
            except TimeoutError:
                return await _denied(502, REASON_SUBMIT_FAILED,
                                     f"조사 서비스 접수 타임아웃({timeout:.0f}s 초과)",
                                     AUDIT_FAILED)
            except Exception as exc:  # noqa: BLE001 — 접수 실패는 사유와 함께 거부한다(침묵 금지)
                logger.warning("서비스 심층 조사 접수 실패: service=%s", service_id, exc_info=True)
                return await _denied(502, REASON_SUBMIT_FAILED,
                                     f"조사 서비스 접수 실패: {type(exc).__name__}", AUDIT_FAILED)
            new_id = _text(res.get("investigation_id"))
            if not new_id:
                return await _denied(502, REASON_SUBMIT_FAILED,
                                     "조사 서비스 응답에 조사 id가 없습니다.", AUDIT_FAILED)
            rejected = _text(res.get("status")) == "rejected"
            reason = _text(res.get("reason")) or None
            verdict = _text(res.get("verdict")) or None
            subject: dict[str, Any] = {
                "type": SUBJECT_TYPE,
                "service_id": service_id,
                "display_name": _text(service.get("display_name")),
                "service_key": service_investigation_key(service_id),
                **{k: v for k, v in rep_view.items() if v},
                "lookback_minutes": window,
            }
            if reference_time:
                subject["reference_time"] = reference_time
            if excluded:
                subject["excluded_members"] = excluded
            store.record(
                investigation_id=new_id,
                alarm_ids=[],
                kind=KIND_DIAGNOSIS,
                status="rejected" if rejected else "submitted",
                source=ORIGIN_BIZ_SERVICE,
                reason=reason if rejected else None,
                owner_sub=sub or None,
                origin=ORIGIN_BIZ_SERVICE,
                subject=subject,
            )
        await _audit(
            audit, AUDIT_DENIED if rejected else AUDIT_OK, targets=targets, authz=authz,
            degraded=[{"reason": "service_rejected", "detail": reason}] if rejected else None,
            investigation_id=new_id, service_id=service_id,
        )
        out: dict[str, Any] = {
            "investigation_id": new_id,
            "status": "rejected" if rejected else "submitted",
            "reused": False,
            "service_id": service_id,
            "representative": rep_view,
            "excluded_members": excluded,
            "truncated": bool((biz or {}).get("truncated")),
            "hypothesis_notice": HYPOTHESIS_NOTICE,
        }
        if rejected:
            if reason:
                out["reason"] = reason
            out["reason_text"] = reject_display_text(reason, verdict)
        return BizInvestigationResult(200, out)

    async with _service_lock(service_id):
        return await _admit()


def list_biz_service_investigations(
    store: InvestigationStore,
    service_id: int,
    requester: BizRequester,
    *,
    mask_fn: Callable[[str], str] | None = None,
    limit: int = HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """그 서비스의 진단 이력(최근 `limit`건 · 최신 먼저) — 소유자는 자기 것만, 관리자는 전부.

    항목은 조사 화면 요약(`investigation_view` — 관리자는 운영자 뷰, 그 밖은 `mask_fn` 사용자 뷰) +
    `submitted_at`이다. 요청자 `sub`가 비고 관리자가 아니면 빈 목록(fail-closed).
    """
    if not requester.is_admin and not requester.sub:
        return []
    owner = None if requester.is_admin else requester.sub
    recs = [r for r in store.list_diagnoses(owner_sub=owner) if _is_service_record(r, service_id)]
    recs.sort(key=lambda r: _text(r.get("submitted_at")), reverse=True)
    return [
        investigation_view(
            rec, mask_fn=None if requester.is_admin else mask_fn, operator_view=requester.is_admin,
        ) | {"submitted_at": rec.get("submitted_at")}
        for rec in recs[: max(0, int(limit))]
    ]


__all__ = [
    "BizInvestigationResult",
    "BizRequester",
    "D1_ANOMALOUS",
    "D1_ANOMALOUS_AT",
    "D1_GAPS",
    "D1_REPRESENTATIVE",
    "D1_REPRESENTATIVE_REASON",
    "D1_TIMELINE",
    "D1_TIMELINE_AT",
    "D1_VERDICT",
    "ENTRY_POINT",
    "HISTORY_LIMIT",
    "ORIGIN_BIZ_SERVICE",
    "QUESTION_TEMPLATE",
    "REASON_AUTHZ_DENIED",
    "REASON_DISABLED",
    "REASON_NO_REPRESENTATIVE",
    "REASON_SERVICE_IN_FLIGHT",
    "REASON_SERVICE_NOT_FOUND",
    "REASON_SERVICE_UNCONFIGURED",
    "REASON_SERVICE_UNREACHABLE",
    "REASON_SUBMIT_FAILED",
    "SUBJECT_TYPE",
    "build_prior_findings",
    "build_question",
    "diagnosis_active",
    "first_anomaly_time",
    "list_biz_service_investigations",
    "representative_of",
    "service_investigation_key",
    "submit_biz_service_investigation",
]
