"""알람 노이즈 캔슬링 관제 API (Plan 54 모듈 5 — `/admin/noise/*` · `/noise/*`).

억제는 곧 "보여주지 않음"이므로, 이 라우트는 그 반대로 **억제 내역을 가장 잘 보여주는 곳**이다.
집계(퍼널·추이·상위 억제)·조회(결정 추적)·메타모니터링·침묵 관리·정책 열람을 제공한다.

경로는 둘이고 핸들러는 하나다(D-245):
    - `/admin/noise/*` — 운영자. 읽기 전량 + 메타모니터링 + 침묵 관리 + 정책 + 실시간 스트림.
    - `/noise/*` — 로그인 사용자. **읽기 5종만**(집계 3 · 결정 목록 · 결정 추적). 침묵·정책·
      스트림은 걸지 않는다 — 운영 통제와 설정값은 운영자에게 남는다.

원칙:
    - **읽기 우선**: 변경은 침묵 생성/해제 둘뿐이며 전부 감사에 남는다.
    - **정책은 읽기 전용**: 쓰기 경로는 `/admin/settings` 하나다(검증·dry-run·백업·감사·리로드가
      거기 있다). 두 번째 쓰기 경로를 내면 안전 가드와 감사가 갈라진다(Plan 54 G-3).
    - **안전 가드는 서버가 강제**: 전체 침묵 금지·심각도 상한·만료 필수를 여기서 막는다.
      클라이언트 입력은 신뢰하지 않는다.
    - 게이트 off·저장소 부재에도 200과 빈 집계를 준다 — 관제 화면이 깨지지 않아야 한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.api.dependencies import require_admin_user, require_user
from src.security.audit_logger import log_silence_change

logger = logging.getLogger(__name__)
router = APIRouter()
# 읽기 전용 집계·조회 — 운영자 경로(`/admin/noise/*`)와 사용자 경로(`/noise/*`) 양쪽에
# **같은 핸들러**를 건다(D-245). 인가는 라우터 레벨 의존성이 갈라 붙이므로 핸들러는
# 권한을 모른다 — 두 벌로 복제하면 한쪽만 고쳐지는 비대칭이 생긴다.
read_router = APIRouter()

# 조회 창 — 닫힌 집합만 받는다(임의 초 입력은 스캔 비용을 예측 불가하게 만든다).
_RANGES: dict[str, int] = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}
# 시계열 버킷 — 창과 조합이 과하면 막대가 수천 개가 되므로 역시 닫힌 집합.
_BUCKETS: dict[str, int] = {"5m": 300, "1h": 3600, "2h": 7200, "6h": 21600, "1d": 86400}

# 판단 결과 4티어 — 목록의 `tier` 쉼표 복수값은 이 닫힌 집합 밖이면 400이다(plans/112 S2).
_TIERS: tuple[str, ...] = ("page", "ticket", "dashboard", "suppress")

# 노이즈 게이트 설정의 env 접두 — `NoiseGateConfig`(`src/config.py`)의 `env_prefix`와 같아야 한다.
# 정책 딥링크 키와 단계 활성 키가 **이 한 곳**에서 만들어진다(plans/112 F-1 — 종전
# `NOISE_GATE_` 접두는 설정 카탈로그에 없는 키를 가리켰다).
_NOISE_ENV_PREFIX = "NOISE_"


def _env_key(field_name: str) -> str:
    """노이즈 게이트 설정 필드명 → 설정 편집기가 쓰는 env 키."""
    return _NOISE_ENV_PREFIX + field_name.upper()


# 정책 화면이 설정 편집기로 딥링크할 때 쓰는 필드 — 값은 읽기만 하고 변경은 저기서 한다.
# 최종 목록 `_POLICY_FIELDS`는 아래에서 단계 활성 필드와 합쳐 만든다(순서 보존 · 추가만).
_BASE_POLICY_FIELDS: tuple[str, ...] = (
    "enable_noise_gate",
    "suppress_max_severity",
    "enable_ai_severity_boost",
    "ai_severity_escalate_only",
    "dependency_suppression",
    "inhibition_enabled",
    "flapping_enabled",
    "storm_grouping_enabled",
    "cross_host_correlation_enabled",
    "enable_llm_actionability",
    "silence_enabled",
    "meta_alert_suppress_ratio",
    "meta_alert_window_seconds",
    "meta_alert_min_events",
)

# 단계 → 그 단계를 켜는 설정 필드(plans/112 S4). 여기 없는 단계는 게이트가 켜져 있으면 항상
# 평가된다. env 키는 config 관심사라 도메인이 아니라 이 계층에 둔다.
_STAGE_ENABLE_FIELDS: dict[str, str] = {
    "non_alarm": "non_alarm_filter_enabled",
    "silence": "silence_enabled",
    "dependency": "dependency_suppression",
    "inhibition": "inhibition_enabled",
    "flapping": "flapping_enabled",
    "storm": "storm_grouping_enabled",
    "correlation": "cross_host_correlation_enabled",
    "annotation": "annotation_planned_suppress",
}

# 정책 탭 = 기존 정책 필드 ∪ 단계 활성 필드(plans/112 m-5). 단계 설명이 "켜는 설정"으로 인용하는
# 키가 정책 탭에 빠지지 않게 **표 하나에서 파생**한다 — 기존 항목의 순서는 그대로, 없던 것만 뒤에.
_POLICY_FIELDS: tuple[str, ...] = _BASE_POLICY_FIELDS + tuple(
    field for field in dict.fromkeys(_STAGE_ENABLE_FIELDS.values())
    if field not in _BASE_POLICY_FIELDS
)

# 운영자 경로로 들어온 요청 표시(`request.state`). 표시가 없으면 사용자 뷰다 — fail-closed.
_OPERATOR_PATH_FLAG = "noise_operator_path"


# ─── 요청/응답 모델 ──────────────────────────────────────────────────────


class FunnelStage(BaseModel):
    """퍼널 단계 1칸."""

    stage: str = Field(description="단계 키")
    label: str = Field(description="화면 표시명")
    residual: int = Field(description="이 단계에 도달한 건수")
    terminated: int = Field(description="이 단계에서 결정이 확정된 건수")
    cut: int = Field(description="그중 통보되지 않은 건수(SUPPRESS·DASHBOARD)")
    description: str = Field(default="", description="단계 설명(판정 코드 옆 도메인 정본)")
    enabled: bool = Field(default=True, description="현재 설정에서 이 단계가 평가되는지")
    enable_key: str | None = Field(
        default=None, description="이 단계를 켜는 설정 env 키(항상 평가되는 단계는 null)"
    )


class NoiseSummaryResponse(BaseModel):
    """KPI + 퍼널."""

    raw: int
    tiers: dict[str, int]
    suppress_ratio: float
    actionable_ratio: float
    stages: list[FunnelStage]
    range: str
    gate_enabled: bool


class TimeseriesPoint(BaseModel):
    """버킷 1칸의 티어 분포."""

    bucket_ts: str
    page: int
    ticket: int
    dashboard: int
    suppress: int


class TimeseriesResponse(BaseModel):
    points: list[TimeseriesPoint]
    range: str
    bucket: str
    excluded_no_ts: int = Field(
        default=0,
        description="창 안 결정 중 시각으로 구간을 정할 수 없어 막대에서 뺀 건수(KPI에는 포함)",
    )


class TopSuppressedItem(BaseModel):
    alarm_name: str
    stage: str
    label: str
    count: int


class TopSuppressedResponse(BaseModel):
    items: list[TopSuppressedItem]
    range: str


class NoiseHealthResponse(BaseModel):
    """억제기 메타모니터링(모니터를 모니터링)."""

    healthy: bool = Field(description="메타경보가 하나도 없으면 True")
    alerts: list[dict] = Field(description="억제율 초과·무수신 등 메타경보")
    suppress_ratio: float
    suppress_ratio_threshold: float
    total: int
    last_event_ts: Optional[str] = None
    last_event_age_seconds: Optional[float] = None
    window_seconds: int
    gate_enabled: bool


class DecisionsResponse(BaseModel):
    items: list[dict]
    total: int
    page: int
    size: int
    facets: dict[str, dict[str, int]] = Field(
        default_factory=dict,
        description=(
            "분포 칩용 건수 — tiers는 tier 필터만 뺀 집합(4키 항상), "
            "stages는 stage 필터만 뺀 집합(0보다 큰 키만)"
        ),
    )


class SilenceCreateRequest(BaseModel):
    """침묵 규칙 생성 요청 — 매처는 글롭이며 빈 문자열은 '무조건 일치'다."""

    db_id: str = ""
    server_name: str = ""
    alarm_name: str = ""
    resource_name: str = ""
    max_severity: int = Field(default=2, ge=0, description="침묵 허용 심각도 상한")
    reason: str = Field(description="침묵 사유(감사·화면 표시)")
    duration_seconds: int = Field(gt=0, description="지속 시간(초) — 만료는 필수다")


class SilenceItem(BaseModel):
    id: str
    db_id: str
    server_name: str
    alarm_name: str
    resource_name: str
    max_severity: int
    reason: str
    created_by: str
    created_at: str
    expires_at: str
    revoked_at: Optional[str] = None
    matcher_summary: str
    active: bool


class SilencesResponse(BaseModel):
    items: list[SilenceItem]
    enabled: bool


class SilenceCreateResponse(BaseModel):
    rule: SilenceItem


class SilenceRevokeResponse(BaseModel):
    revoked: bool


class PolicySetting(BaseModel):
    """정책 항목 1건 — 값은 읽기만 하고 변경은 설정 화면에서 한다."""

    key: str
    env_key: str
    value: Any
    locked: bool = Field(default=False, description="안전 고정 항목(변경해도 억제 강화 불가)")


class PolicyResponse(BaseModel):
    matrix: list[dict] = Field(description="심각도×중요도 기본 티어(심각도3 행은 잠금)")
    settings: list[PolicySetting]
    editor_path: str = Field(description="설정 편집 화면 경로(딥링크)")


# ─── 내부 헬퍼 ──────────────────────────────────────────────────────────


def _gate_cfg(request: Request):  # noqa: ANN201
    """노이즈 게이트 설정 그룹을 돌려준다."""
    return request.app.state.config.noise_gate


def _decision_store(request: Request):  # noqa: ANN201
    """결정 저장소를 만든다(설정만 읽으므로 요청마다 생성해도 저렴하다)."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    ng = _gate_cfg(request)
    return DecisionStore(
        ng.decision_store_path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    )


def _silence_store(request: Request):  # noqa: ANN201
    """침묵 저장소를 만든다(플래그와 무관하게 조회는 허용 — 화면이 목록을 보여야 한다)."""
    from noise_gate.infrastructure.silence_store import SilenceStore

    ng = _gate_cfg(request)
    return SilenceStore(getattr(ng, "silence_store_path", "logs/alarm_silences.jsonl"), True)


def _resolve_range(value: str) -> int:
    """조회 창 문자열을 초로 바꾼다(닫힌 집합 밖이면 400)."""
    seconds = _RANGES.get(value)
    if seconds is None:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 조회 범위입니다: {value} (가능: {', '.join(_RANGES)})",
        )
    return seconds


def _resolve_bucket(value: str) -> int:
    """버킷 문자열을 초로 바꾼다(닫힌 집합 밖이면 400)."""
    seconds = _BUCKETS.get(value)
    if seconds is None:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 버킷입니다: {value} (가능: {', '.join(_BUCKETS)})",
        )
    return seconds


def _mask_fn(request: Request):  # noqa: ANN201
    """표시 전 문자열 마스킹 함수를 만든다(값 패턴 기반 — 컬럼명을 모르는 자유 텍스트용).

    결정 사유·신호에 토큰·키가 섞여 들어올 수 있으므로 화면에 내보내기 전에 가린다.
    """
    from src.security.data_masker import DataMasker

    mask = request.app.state.config.security.mask_pattern

    def _mask(text: str) -> str:
        for pattern in DataMasker.SENSITIVE_VALUE_PATTERNS:
            if pattern.match(text):
                return mask
        return text

    return _mask


def _mark_operator_path(request: Request) -> None:
    """운영자 경로(`/admin/noise/*`)로 들어온 요청임을 표시한다(라우터 레벨 의존성).

    읽기 핸들러는 두 경로가 공유하므로(D-245) 경로를 모른다. 운영자 전용 필드(G-2 (c) —
    `condition_log`)를 가를 때 이 표시만 본다 — 표시가 없으면 사용자 뷰(fail-closed)라,
    새 경로를 걸면서 이 의존성을 빠뜨려도 실측값이 새지 않는다.
    """
    setattr(request.state, _OPERATOR_PATH_FLAG, True)


def _is_operator_path(request: Request) -> bool:
    """요청이 운영자 경로로 들어왔는지(`_mark_operator_path` 표시 여부)."""
    return bool(getattr(request.state, _OPERATOR_PATH_FLAG, False))


def _parse_tiers(value: str | None) -> frozenset[str] | None:
    """`tier` 쉼표 복수값을 집합으로 바꾼다(빈 값·None = 필터 없음, 닫힌 집합 밖이면 400).

    종전에는 임의 문자열이 조용히 0건이 됐다 — 오타를 "해당 없음"으로 보이게 하지 않는다.
    """
    if value is None:
        return None
    tokens = [token.strip() for token in value.split(",")]
    tokens = [token for token in tokens if token]
    if not tokens:
        return None
    unknown = [token for token in tokens if token not in _TIERS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"지원하지 않는 티어입니다: {', '.join(unknown)} (가능: {', '.join(_TIERS)})",
        )
    return frozenset(tokens)


def _parse_instant(value: str | None, name: str) -> datetime | None:
    """ISO-8601 시각을 datetime으로 바꾼다(빈 값 = 미지정, 시간대 없으면 UTC, 실패는 400)."""
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"{name} 시각 형식이 올바르지 않습니다(ISO-8601): {value}",
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _stage_meta(stage: str, ng: Any) -> dict[str, Any]:
    """퍼널 단계 1칸의 설명·활성 여부·켜는 설정 키를 만든다 (plans/112 S4).

    게이트가 꺼지면 전 단계가 평가되지 않는다. 게이트가 켜지면 `_STAGE_ENABLE_FIELDS`의
    8단계만 설정값을 따르고, 나머지(와 `unknown`)는 항상 평가된다.
    """
    from noise_gate.domain.notification_policy import STAGE_DESCRIPTIONS

    field_name = _STAGE_ENABLE_FIELDS.get(stage)
    gate_on = bool(ng.enable_noise_gate)
    if field_name is None:
        enabled = gate_on
    else:
        enabled = gate_on and bool(getattr(ng, field_name, False))
    return {
        "description": STAGE_DESCRIPTIONS.get(stage, ""),
        "enabled": enabled,
        "enable_key": _env_key(field_name) if field_name else None,
    }


def _actor(user: dict) -> str:
    """감사에 남길 행위자 식별자."""
    return str(user.get("sub") or user.get("name") or user.get("username") or "unknown")


def _to_item(rule, now: datetime) -> SilenceItem:  # noqa: ANN001
    """SilenceRule을 응답 모델로 바꾼다."""
    from noise_gate.domain.silence import is_active

    data = rule.to_dict()
    return SilenceItem(
        **data, matcher_summary=rule.matcher_summary, active=is_active(rule, now)
    )


# ─── 집계 (읽기) ────────────────────────────────────────────────────────


@read_router.get(
    "/summary",
    response_model=NoiseSummaryResponse,
    summary="노이즈 캔슬링 KPI + 퍼널",
    description=(
        "티어별 건수·억제율·액션가능 비율과 **단계별 억제량(퍼널)**을 반환합니다.<br/>"
        "모든 결정은 정확히 한 단계에서 종결되므로 단계별 종결 수의 합은 수신 건수와 같습니다.<br/>"
        "단계마다 설명(`description`)·현재 설정의 평가 여부(`enabled`)·켜는 설정 키"
        "(`enable_key`)를 함께 줍니다."
    ),
    tags=["noise-console"],
)
async def noise_summary(
    request: Request,
    range: str = Query(default="24h", description="조회 범위(1h·24h·7d·30d)"),
) -> NoiseSummaryResponse:
    """결정 감사에서 KPI와 퍼널을 집계해 돌려준다."""
    window = _resolve_range(range)
    funnel = _decision_store(request).funnel(window_seconds=window)
    ng = _gate_cfg(request)
    return NoiseSummaryResponse(
        raw=funnel["raw"],
        tiers=funnel["tiers"],
        suppress_ratio=funnel["suppress_ratio"],
        actionable_ratio=funnel["actionable_ratio"],
        stages=[FunnelStage(**s, **_stage_meta(s["stage"], ng)) for s in funnel["stages"]],
        range=range,
        gate_enabled=bool(ng.enable_noise_gate),
    )


@read_router.get(
    "/timeseries",
    response_model=TimeseriesResponse,
    summary="티어 분포 추이",
    description=(
        "버킷별 티어 분포를 반환합니다. 버킷 경계는 고정 격자이며, 기본은 UTC 격자입니다.<br/>"
        "`tz_offset_minutes`를 주면 그 로컬 시각 기준으로 정렬합니다(예: 540이면 2시간 구간이 "
        "KST 짝수 시에 시작). `bucket_ts`는 어느 경우든 UTC 표기입니다."
    ),
    tags=["noise-console"],
)
async def noise_timeseries(
    request: Request,
    range: str = Query(default="24h"),
    bucket: str = Query(default="2h"),
    tz_offset_minutes: int | None = Query(
        default=None,
        ge=-720,
        le=840,
        description="로컬 시각의 UTC 대비 오프셋(분) — 브라우저 `-getTimezoneOffset()`",
    ),
) -> TimeseriesResponse:
    """버킷별 티어 분포를 돌려준다(빈 버킷도 0으로 채운다)."""
    window = _resolve_range(range)
    bucket_seconds = _resolve_bucket(bucket)
    report = _decision_store(request).timeseries_report(
        window_seconds=window,
        bucket_seconds=bucket_seconds,
        tz_offset_minutes=tz_offset_minutes,
    )
    return TimeseriesResponse(
        points=[TimeseriesPoint(**p) for p in report["points"]],
        range=range,
        bucket=bucket,
        excluded_no_ts=report["excluded_no_ts"],
    )


@read_router.get(
    "/top-suppressed",
    response_model=TopSuppressedResponse,
    summary="상위 억제 알람 유형",
    description="억제된 알람을 (알람명 × 단계)로 묶어 상위 항목을 반환합니다.",
    tags=["noise-console"],
)
async def noise_top_suppressed(
    request: Request,
    range: str = Query(default="24h"),
    limit: int = Query(default=10, ge=1, le=50),
) -> TopSuppressedResponse:
    """무엇이 캔슬되고 있는지를 사람이 읽는 형태로 돌려준다."""
    window = _resolve_range(range)
    items = _decision_store(request).top_suppressed(window_seconds=window, limit=limit)
    return TopSuppressedResponse(
        items=[TopSuppressedItem(**i) for i in items], range=range
    )


@router.get(
    "/admin/noise/health",
    response_model=NoiseHealthResponse,
    summary="억제기 메타모니터링",
    description=(
        "억제율 임계 초과·이벤트 무수신 워치독을 반환합니다.<br/>"
        "억제기가 과도하게 억제하거나 아예 멈춘 상태를 운영자가 즉시 알아채기 위한 신호입니다."
    ),
    tags=["noise-console"],
)
async def noise_health(
    request: Request,
    _admin: dict = Depends(require_admin_user),
) -> NoiseHealthResponse:
    """메타경보와 억제율·최근 수신 경과를 돌려준다."""
    ng = _gate_cfg(request)
    store = _decision_store(request)
    window = int(getattr(ng, "meta_alert_window_seconds", 3600))
    threshold = float(getattr(ng, "meta_alert_suppress_ratio", 0.9))
    min_events = int(getattr(ng, "meta_alert_min_events", 1))

    agg = store.aggregate(window_seconds=window)
    alerts = store.meta_alerts(
        window_seconds=window,
        suppress_ratio_threshold=threshold,
        min_events=min_events,
    )
    return NoiseHealthResponse(
        healthy=not alerts,
        alerts=alerts,
        suppress_ratio=agg["suppress_ratio"],
        suppress_ratio_threshold=threshold,
        total=agg["total"],
        last_event_ts=agg["last_event_ts"],
        last_event_age_seconds=agg["last_event_age_seconds"],
        window_seconds=window,
        gate_enabled=bool(ng.enable_noise_gate),
    )


# ─── 결정 조회 (설명가능성) ─────────────────────────────────────────────


@read_router.get(
    "/decisions",
    response_model=DecisionsResponse,
    summary="발송 판단 목록",
    description=(
        "티어·단계·검색어로 거른 결정 목록을 최신순으로 반환합니다.<br/>"
        "PAGE 티어는 즉시 통보 경로라 실시간 스트림에 실리지 않으므로, 피드의 완전성은 이 조회가 맡습니다."
        "<br/>`tier`는 쉼표 복수값(`page,ticket`)을 받고, `facets`는 분포 칩용 건수입니다. "
        "`related=true`면 상관·자가복구 행에 대표/원 발생 판단을 붙입니다."
    ),
    tags=["noise-console"],
)
async def noise_decisions(
    request: Request,
    range: str = Query(default="24h"),
    tier: Optional[str] = Query(
        default=None, description="티어(쉼표 복수값 · page·ticket·dashboard·suppress)"
    ),
    stage: Optional[str] = Query(default=None),
    q: Optional[str] = Query(default=None, description="알람명·서버명·사유 부분일치"),
    alarm_name: str | None = Query(default=None, description="알람명 정확 일치"),
    since: str | None = Query(default=None, description="구간 시작(ISO-8601, 포함)"),
    until: str | None = Query(default=None, description="구간 끝(ISO-8601, 제외)"),
    related: bool = Query(default=False, description="대표/원 발생 판단 해석"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> DecisionsResponse:
    """결정 감사 목록을 필터·페이지로 돌려준다."""
    window = _resolve_range(range)
    result = _decision_store(request).list_decisions(
        window_seconds=window,
        tier=_parse_tiers(tier),
        stage=stage,
        q=q,
        alarm_name=alarm_name,
        since=_parse_instant(since, "since"),
        until=_parse_instant(until, "until"),
        related=related,
        page=page,
        size=size,
        mask_fn=_mask_fn(request),
        operator_view=_is_operator_path(request),
    )
    return DecisionsResponse(**result)


@read_router.get(
    "/decisions/{alarm_id}",
    summary="발송 판단 추적(단일)",
    description=(
        "알람 1건의 가장 최근 결정을 파이프라인 단계·신호 스냅샷과 함께 반환합니다.<br/>"
        "결정 단계 앞은 통과, 뒤는 단락(평가되지 않음)입니다."
    ),
    tags=["noise-console"],
)
async def noise_decision_trace(
    alarm_id: str,
    request: Request,
) -> dict:
    """결정 1건 + 단계 타임라인을 돌려준다(없으면 404)."""
    from noise_gate.domain.notification_policy import STAGE_LABELS, STAGE_ORDER

    decision = _decision_store(request).get_decision(
        alarm_id, mask_fn=_mask_fn(request), operator_view=_is_operator_path(request)
    )
    if decision is None:
        raise HTTPException(status_code=404, detail="해당 알람의 발송 판단이 없습니다.")

    decided_stage = decision.get("stage", "")
    try:
        decided_index = STAGE_ORDER.index(decided_stage)
    except ValueError:
        decided_index = -1  # unknown 단계 — 어느 지점도 강조하지 않는다

    timeline = []
    for index, stage in enumerate(STAGE_ORDER):
        if decided_index < 0:
            status = "unknown"
        elif index < decided_index:
            status = "passed"
        elif index == decided_index:
            status = "decided"
        else:
            status = "short_circuited"
        timeline.append({
            "stage": stage,
            "label": STAGE_LABELS.get(stage, stage),
            "status": status,
        })
    return {"decision": decision, "timeline": timeline}


@router.get(
    "/admin/noise/stream",
    summary="관제용 실시간 결정 스트림 (SSE)",
    description=(
        "게이트 결정을 실시간으로 흘려보냅니다(운영자 전용).<br/>"
        "PAGE는 즉시 통보 경로라 이 스트림에 실리지 않습니다 — 목록 조회로 보완하십시오."
    ),
    tags=["noise-console"],
)
async def noise_stream(
    request: Request,
    token: Optional[str] = Query(default=None, description="내부망 폴백 토큰"),
) -> StreamingResponse:
    """알람 버스를 구독해 결정 이벤트를 SSE로 전달한다(존 필터 없음 — 관제 전 범위).

    브라우저 EventSource는 Authorization 헤더를 실을 수 없으므로 쿠키를 우선 보고
    쿼리 토큰으로 폴백한다(`/alarm/notifications/stream` 전례). **운영자 판정은 여기서
    따로 한다** — 이 스트림에는 전 존의 억제 내역이 흐르므로 사용자 토큰으로는 열 수 없다.
    """
    from src.api.dependencies import resolve_stream_user
    from src.domain.user import UserRole

    config = request.app.state.config
    user = await resolve_stream_user(request, token)
    if config.auth.enabled:
        if user is None:
            raise HTTPException(status_code=401, detail="인증이 필요합니다.")
        if user.get("role") != UserRole.ADMIN.value:
            raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")

    bus = request.app.state.alarm_bus
    queue = bus.subscribe()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield 'data: {"type":"ping"}\n\n'
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ─── 침묵 관리 (쓰기 — 감사 대상) ───────────────────────────────────────


@router.get(
    "/admin/noise/silences",
    response_model=SilencesResponse,
    summary="침묵 규칙 목록",
    tags=["noise-console"],
)
async def list_silences(
    request: Request,
    include_inactive: bool = Query(default=False, description="만료·해제분 포함"),
    _admin: dict = Depends(require_admin_user),
) -> SilencesResponse:
    """침묵 규칙을 등록 순으로 돌려준다."""
    now = datetime.now(timezone.utc)
    rules = _silence_store(request).list_rules(include_inactive=include_inactive, now=now)
    return SilencesResponse(
        items=[_to_item(rule, now) for rule in rules],
        enabled=bool(getattr(_gate_cfg(request), "silence_enabled", False)),
    )


@router.post(
    "/admin/noise/silences",
    response_model=SilenceCreateResponse,
    summary="침묵 규칙 생성",
    description=(
        "안전 가드를 서버에서 강제합니다: 전 매처 공백 금지(전체 침묵) · "
        "심각도 상한(억제 허용 상한 이하) · 만료 필수(최대 존속 기간 이내)."
    ),
    tags=["noise-console"],
)
async def create_silence(
    body: SilenceCreateRequest,
    request: Request,
    admin: dict = Depends(require_admin_user),
) -> SilenceCreateResponse:
    """침묵 규칙을 검증 후 적재하고 감사에 남긴다."""
    ng = _gate_cfg(request)
    matcher_fields = (body.db_id, body.server_name, body.alarm_name, body.resource_name)
    if not any(str(f or "").strip() for f in matcher_fields):
        raise HTTPException(
            status_code=400,
            detail="매처를 하나 이상 지정해야 합니다 — 전 필드가 비면 모든 알람이 억제됩니다.",
        )

    suppress_cap = int(getattr(ng, "suppress_max_severity", 2))
    if body.max_severity > suppress_cap:
        raise HTTPException(
            status_code=400,
            detail=(
                f"심각도 상한은 {suppress_cap} 이하여야 합니다 — "
                "그 위 심각도는 어떤 규칙으로도 억제되지 않습니다."
            ),
        )

    max_duration = int(getattr(ng, "silence_max_duration_seconds", 604800))
    if body.duration_seconds > max_duration:
        raise HTTPException(
            status_code=400,
            detail=f"지속 시간은 {max_duration}초 이하여야 합니다(만료 없는 침묵 금지).",
        )
    if not body.reason.strip():
        raise HTTPException(status_code=400, detail="침묵 사유는 필수입니다(감사 기록).")

    now = datetime.now(timezone.utc)
    rule = _silence_store(request).create(
        db_id=body.db_id,
        server_name=body.server_name,
        alarm_name=body.alarm_name,
        resource_name=body.resource_name,
        max_severity=body.max_severity,
        reason=body.reason,
        created_by=_actor(admin),
        expires_at=now + timedelta(seconds=body.duration_seconds),
        now=now,
    )
    await log_silence_change(
        action="create",
        rule_id=rule.id,
        actor=_actor(admin),
        detail=rule.to_dict(),
    )
    logger.info("침묵 규칙 생성: id=%s by=%s %s", rule.id, _actor(admin), rule.matcher_summary)
    return SilenceCreateResponse(rule=_to_item(rule, now))


@router.delete(
    "/admin/noise/silences/{rule_id}",
    response_model=SilenceRevokeResponse,
    summary="침묵 규칙 해제",
    description="원 레코드를 지우지 않고 해제 이력을 덧붙입니다(감사 무결).",
    tags=["noise-console"],
)
async def revoke_silence(
    rule_id: str,
    request: Request,
    admin: dict = Depends(require_admin_user),
) -> SilenceRevokeResponse:
    """침묵 규칙을 해제하고 감사에 남긴다(없거나 이미 해제면 404)."""
    store = _silence_store(request)
    before = next(
        (r for r in store.list_rules(include_inactive=True) if r.id == rule_id), None
    )
    if not store.revoke(rule_id, revoked_by=_actor(admin)):
        raise HTTPException(status_code=404, detail="해제할 침묵 규칙이 없습니다.")
    await log_silence_change(
        action="revoke",
        rule_id=rule_id,
        actor=_actor(admin),
        detail=before.to_dict() if before is not None else {},
    )
    logger.info("침묵 규칙 해제: id=%s by=%s", rule_id, _actor(admin))
    return SilenceRevokeResponse(revoked=True)


# ─── 정책 (읽기 전용) ───────────────────────────────────────────────────


@router.get(
    "/admin/noise/policy",
    response_model=PolicyResponse,
    summary="게이트 정책 열람",
    description=(
        "심각도×중요도 매트릭스와 억제 관련 설정의 **현재 값**을 반환합니다(읽기 전용).<br/>"
        "변경은 설정 화면에서 합니다 — 검증·백업·감사·리로드가 그 경로에만 있습니다."
    ),
    tags=["noise-console"],
)
async def noise_policy(
    request: Request,
    _admin: dict = Depends(require_admin_user),
) -> PolicyResponse:
    """매트릭스와 정책 설정값을 딥링크 키와 함께 돌려준다."""
    from noise_gate.domain.notification_policy import _matrix_tier

    ng = _gate_cfg(request)
    matrix = [
        {
            "severity": severity,
            "locked": severity == 3,  # 심각도3은 매트릭스를 경유하지 않고 항상 PAGE
            "cells": {
                importance: ("page" if severity == 3 else _matrix_tier(severity, importance))
                for importance in ("높음", "보통", "낮음")
            },
        }
        for severity in (3, 2, 1)
    ]
    settings = [
        PolicySetting(
            key=key,
            env_key=_env_key(key),
            value=getattr(ng, key, None),
            # 상향 전용 잠금은 안전 고정 — 화면에서 끌 수 있는 것처럼 보이면 안 된다.
            locked=(key == "ai_severity_escalate_only"),
        )
        for key in _POLICY_FIELDS
    ]
    return PolicyResponse(
        matrix=matrix, settings=settings, editor_path="/static/admin/dashboard.html"
    )


# ─── 경로 등록 — 같은 읽기 핸들러를 두 인가 아래에 건다 (D-245) ──────────


# 운영자 경로: 종전 그대로. 개별 핸들러에 있던 `Depends(require_admin_user)`를
# 라우터 레벨로 옮긴 것뿐이라 외부 URL·응답·인가 판정은 비트 동일하다.
# (plans/112 G-2 (c)) `_mark_operator_path`는 인가가 아니라 **뷰 모드 표시**다 — 운영자 전용
# 필드(`condition_log`)는 이 표시가 있을 때만 응답에 실린다(사용자 경로에는 걸지 않는다).
router.include_router(
    read_router,
    prefix="/admin/noise",
    dependencies=[Depends(require_admin_user), Depends(_mark_operator_path)],
)
# 사용자 경로: **읽기 전용 5종만**. 침묵(쓰기)·정책(설정값 열람)·실시간 스트림은
# 여기에 걸지 않는다 — 운영 통제와 설정값은 운영자에게 남고, 전 존 억제 내역이 흐르는
# 스트림의 인가는 D-196 ⑤ 그대로 관리자 전용이다(사용자 화면은 주기 재동기화로 채운다).
router.include_router(
    read_router,
    prefix="/noise",
    dependencies=[Depends(require_user)],
)
