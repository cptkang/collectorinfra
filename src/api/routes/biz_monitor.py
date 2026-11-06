"""업무 현황 보기 API — 요약 · 상세 · heartbeat (plans/215 §4.2 · §4.3 · §5.2 ①② · §9 · D-368).

`NOISE_BIZ_SERVICE_ENABLED`·`NOISE_BIZ_MONITOR_ENABLED`(`biz_monitor_active`)가 켜진 채 기동했을
때만 등록한다(꺼져 있으면 라우트가 없어 404). 집계·조립은
`noise_gate.application.biz_monitor_alarms`가 하고, 여기는 인가·존 범위·M3 키 읽기·마스킹만
맡는다(D-139 — 본체는 배선 최소).

- 인가: 로그인 사용자(`require_user` — JWT `type` 클레임 검증 · 미로그인 401). 관리 동작은 없다.
- 존 필터: 알람 존(`alarm_zones_for_user`) 밖 구성원은 이름·값 없이 건수만 · 보이는 구성원이
  없는 서비스는 목록에서 빼고 `hidden_service_count`로만 센다(상세는 404 — 존재도 드러내지 않는다).
- 사용자 경로의 자유 문장(서비스 설명·담당 조직)은 관제 사용자 경로와 같은 값 패턴 마스킹
  (D-245 부기).
- M3 최신값(`bizmon:svc:{id}`)·수집 상태(`bizmon:status`)는 지표 수집 플래그가 켜졌을 때만 읽는다.
- heartbeat는 `bizmon:viewers` `SET EX 120`(쓰기는 API 프로세스 · M3 수집기가 소비) — 지표 수집이
  꺼져 있으면 기록하지 않는다(소비자가 없다).
- (W8) 상세 「개요」 — 구성 맵(`/{id}/topology` · 210 연계 사본 `app.state.apm_host_map_cache`)과
  24시간 추이(`/{id}/trend` · 상태 전이 `app.state.biz_service_repo` + 시계열 `bizmon:ts:{id}`).
  둘 다 상세와 같은 존 필터를 거친 구성원 위에서
  `noise_gate.application.biz_monitor_overview`가 조립한다.
- (W8b) 상세 「이벤트」 — 통합 이벤트 타임라인(`/{id}/events` · 결정 이력 · 상태 전이 ·
  조사 저장소 · 옵트인 변경 이력). 수집·묶음은 `noise_gate.application.biz_monitor_events`가 하고
  여기는 상세와 같은 존 필터 · 억제 포함 규칙(운영자만) · 사용자 경로 마스킹만 건다.
- (W8c) 상세 「지표」 — 구성원별 지표 추이(`/{id}/metrics` · `NOISE_BIZ_MONITOR_HISTORY_ENABLED` ·
  꺼져 있으면 404 · 조회기 생성 0). 조회·메모·상한은 `noise_gate.application.biz_monitor_history`가
  하고 여기는 상세와 같은 존 필터 · `members` 형식 검사 · 구성 맵과 같은 210 연계 선만 건다.
- (W9 · W11) 상세 「진단」 — D1 진단 스냅샷(`/{id}/diagnosis`) · D3 서비스 심층 조사
  접수(`POST /{id}/investigations`) · 진단 이력(`GET /{id}/investigations`).
  `NOISE_BIZ_DIAGNOSIS_ENABLED`(`biz_diagnosis_active`)가 꺼져 있으면 셋 다 404(진단기 생성 0).
  판독·즉석 조회는 `noise_gate.application.biz_diagnosis`, 접수(인가·상한·같은 서비스
  재사용)·이력은 `noise_gate.application.biz_service_investigation`이 하고, 여기는 존 범위·
  요청자 정보·상한 임계구역·감사 콜백(기존 알람 조사 경로 `src.api.routes.alarm`의 것을
  그대로)만 넘긴다. 접수는 요청 본문을 받지 않는다(질문은 코드 템플릿 — 사용자 입력 0).
- 관측 시스템(MCP·DB·게이트웨이) 호출 0 · LLM 0 — 예외는 변경 이력
  (`NOISE_BIZ_MONITOR_CHANGES_ENABLED` · 기본 off)과 지표 보기(`NOISE_BIZ_MONITOR_HISTORY_ENABLED` ·
  기본 off) · 진단(`NOISE_BIZ_DIAGNOSIS_ENABLED` · 기본 off — M3 최신값이 없을 때 즉석 1회) 셋으로,
  켜졌을 때만 레지스트리 실행기(`SQLGuard` · readonly)로 존별 고정 SELECT · APM 게이트웨이를 읽는다.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.dependencies import alarm_zones_for_user, is_admin, require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/biz-services/monitor")

_STATE_ATTR = "biz_monitor_alarms"
_EVENTS_ATTR = "biz_monitor_events"
_HISTORY_ATTR = "biz_monitor_history"
_DIAGNOSER_ATTR = "biz_diagnoser"
#: `members` 쉼표 목록의 구성원 id 한 개.
_MEMBER_ID = re.compile(r"^\d{1,19}$")
#: 사용자 경로에서 값 패턴 마스킹을 거는 서비스 자유 문장 칸.
_FREE_TEXT_FIELDS = ("description", "owner_org")


def _metrics_active(request: Request) -> bool:
    ng = getattr(request.app.state.config, "noise_gate", None)
    return getattr(ng, "biz_monitor_metrics_active", False) is True


def _decision_store(ng: Any) -> Any:
    """결정 이력 저장소(경로가 없으면 None) — 집계기·이벤트 타임라인이 같은 설정으로 만든다."""
    from noise_gate.infrastructure.decision_store import DecisionStore

    path = ng.decision_store_path
    return DecisionStore(
        path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    ) if path else None


def _aggregator(request: Request) -> Any:
    """프로세스 안 집계기 1개(첫 요청에 만든다 · 10초 메모를 보드 전체가 나눠 쓴다)."""
    state = request.app.state
    agg = getattr(state, _STATE_ATTR, None)
    if agg is not None:
        return agg
    from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator

    ng = state.config.noise_gate
    agg = BizAlarmAggregator(
        cache=getattr(state, "biz_service_cache", None),
        incident_store=getattr(state, "incident_store", None),
        decision_store=_decision_store(ng),
        window_minutes=int(getattr(ng, "biz_monitor_alarm_window_minutes", 60)),
    )
    setattr(state, _STATE_ATTR, agg)
    return agg


def _timeline(request: Request, agg: Any) -> Any:
    """프로세스 안 이벤트 타임라인 수집기 1개(첫 요청에 만든다 · 10초 메모).

    변경 이력 읽기는 `biz_monitor_changes_active`일 때만 주입한다(꺼져 있으면 `change_feed` 호출 0).
    """
    state = request.app.state
    timeline = getattr(state, _EVENTS_ATTR, None)
    if timeline is not None:
        return timeline
    from noise_gate.application.biz_monitor_events import (
        BizEventTimeline,
        change_reader_from_executor,
    )

    ng = state.config.noise_gate
    reader = engine_of = None
    if ng.biz_monitor_changes_active is True:
        from noise_gate.infrastructure.apm_host_map_sql_scanners import (
            db_engine_of,
            registry_executor,
        )

        reader = change_reader_from_executor(registry_executor(state.config))
        engine_of = db_engine_of
    investigations = None
    path = ng.investigation_store_path
    if path:
        from noise_gate.infrastructure.investigation_store import InvestigationStore

        investigations = InvestigationStore(
            path, int(ng.investigation_store_max_lines))
    timeline = BizEventTimeline(
        cache=agg.cache,
        decision_store=_decision_store(ng),
        investigation_store=investigations,
        repo=getattr(state, "biz_service_repo", None),
        change_reader=reader,
        engine_of=engine_of,
        change_lookback_minutes=int(ng.biz_monitor_change_lookback_minutes),
    )
    setattr(state, _EVENTS_ATTR, timeline)
    return timeline


def _zone_scope(request: Request, user: dict[str, Any]) -> frozenset[str] | None:
    """요청자 알람 존 범위 — 전 존이면 None. 빈 집합은 거부가 아니라 「전부 건수만」이다."""
    from src.routing.zones import all_zones

    zones = alarm_zones_for_user(user, request.app.state.config.auth.enabled)
    if zones >= set(all_zones()):
        return None
    return frozenset(zones)


def _is_operator(request: Request, user: dict[str, Any]) -> bool:
    return bool(not request.app.state.config.auth.enabled) or is_admin(user)


def _mask_fn(request: Request) -> Callable[[str], str]:
    """관제 사용자 경로와 같은 값 패턴 마스킹(값 전체가 비밀값 꼴이면 가린다)."""
    from src.security.data_masker import DataMasker

    mask = request.app.state.config.security.mask_pattern

    def _mask(text: str) -> str:
        for pattern in DataMasker.SENSITIVE_VALUE_PATTERNS:
            if pattern.match(text):
                return str(mask)
        return text

    return _mask


async def _monitor_inputs(
    request: Request, agg: Any, service_ids: list[int]
) -> tuple[dict[int, Any], dict[str, Any]]:
    """M3 최신값(감시 서비스만) · 수집 상태 — 지표 수집이 꺼져 있으면 Redis를 읽지 않는다."""
    from noise_gate.application.biz_monitor_alarms import read_metrics_status, read_monitor_values

    active = _metrics_active(request)
    redis = getattr(agg.cache, "redis", None)
    status = await read_metrics_status(redis, active=active)
    if not active:
        return {}, status
    return await read_monitor_values(redis, service_ids), status


def _monitored_ids(snapshot: Any) -> list[int]:
    return sorted(sid for sid, svc in snapshot.services.items() if svc.get("monitored") is True)


@router.get(
    "",
    summary="업무 현황 요약",
    description=(
        "업무 서비스 목록 타일과 상단 요약 띠 데이터입니다(M2 알람 현황 + M3 최신값).<br/>"
        "존 권한 밖 구성원은 이름·값 없이 건수만 셉니다. 관측 시스템을 조회하지 않습니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_board(
    request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """요약 — 서비스 타일 + 요약 띠 + 「서비스 미상」 건수(트리 그룹 추정은 표시만)."""
    from noise_gate.application.biz_monitor_alarms import build_board

    scope = _zone_scope(request, current_user)
    agg = _aggregator(request)
    snapshot = await agg.snapshot()
    values, status = await _monitor_inputs(request, agg, _monitored_ids(snapshot))
    return build_board(
        snapshot, scope=scope, monitor_values=values, metrics_status=status,
        metrics_active=_metrics_active(request), thresholds=agg.thresholds(),
    )


@router.get(
    "/{service_id}",
    summary="업무 현황 상세",
    description=(
        "서비스 머리(상태·근거·알람 현황)와 구성원 상태 표 데이터입니다.<br/>"
        "없는 서비스·존 권한 밖 서비스는 404입니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_detail(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """상세 — 머리 + 구성원 상태 표(권한 밖 구성원은 `hidden_member_count`로만)."""
    body = await _detail_body(request, current_user, service_id)
    if not _is_operator(request, current_user):
        mask = _mask_fn(request)
        head = body["service"]
        for name in _FREE_TEXT_FIELDS:
            if isinstance(head.get(name), str):
                head[name] = mask(head[name])
    return body


async def _detail_body(
    request: Request, current_user: dict[str, Any], service_id: int
) -> dict[str, Any]:
    """존 필터를 건 상세 본문(없음·전부 권한 밖이면 404) — 상세·구성 맵·추이가 같이 쓴다."""
    from noise_gate.application.biz_monitor_alarms import build_detail

    scope = _zone_scope(request, current_user)
    agg = _aggregator(request)
    snapshot = await agg.snapshot()
    ids = [service_id] if service_id in _monitored_ids(snapshot) else []
    values, status = await _monitor_inputs(request, agg, ids)
    body = build_detail(
        snapshot, service_id, scope=scope, monitor=values.get(service_id),
        metrics_status=status, metrics_active=_metrics_active(request),
        thresholds=agg.thresholds(),
    )
    if body is None:
        raise HTTPException(status_code=404, detail="업무 서비스를 찾을 수 없습니다.")
    return body


@router.get(
    "/{service_id}/topology",
    summary="업무 현황 구성 맵",
    description=(
        "계층 레인(웹 → WAS → DB → 배치 → 기타 → 미지정)별 구성원과 "
        "APM 인스턴스 ↔ 서버 연계 선입니다.<br/>"
        "추정 선은 자동 연계, 끊김은 대상이 사라진 연계입니다. 존 권한 밖 구성원은 건수만 셉니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_topology(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """구성 맵 — 상세 구성원 + 210 연계 사본(소스당 HMGET 1회 · 없으면 선 없음 + 사유)."""
    from noise_gate.application.biz_monitor_overview import build_topology, read_link_records

    detail = await _detail_body(request, current_user, service_id)
    records, reason = await read_link_records(
        getattr(request.app.state, "apm_host_map_cache", None), detail["members"]
    )
    return build_topology(detail, records, links_reason=reason)


@router.get(
    "/{service_id}/trend",
    summary="업무 현황 24시간 추이",
    description=(
        "24시간 상태 띠(상태 전이 기록)와 스파크라인 점(지표 시계열)입니다.<br/>"
        "감시 꺼짐·지표 수집 꺼짐이면 점 없이 사유를, "
        "존 권한 밖 구성원이 있으면 둘 다 사유만 돌려줍니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_trend(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """추이 — 상태 띠 + 시계열 점(서비스 전체 기준이라 권한 밖 구성원이 있으면 사유만)."""
    from noise_gate.application.biz_monitor_overview import build_trend

    detail = await _detail_body(request, current_user, service_id)
    state = request.app.state
    return await build_trend(
        detail,
        repo=getattr(state, "biz_service_repo", None),
        redis=getattr(getattr(state, "biz_service_cache", None), "redis", None),
        metrics_active=_metrics_active(request),
        restricted=_zone_scope(request, current_user) is not None,
        now=time.time(),
    )


@router.get(
    "/{service_id}/events",
    summary="업무 현황 통합 이벤트 타임라인",
    description=(
        "알람(폴스타·제니퍼 · 노이즈 판정 전달/억제/강등) · 서비스 상태 전이 · 조사 시작/완료 · "
        "(옵트인) 변경 이력을 시각 순으로 합칩니다.<br/>"
        "같은 분·같은 구성원·같은 알람은 1행(`count`)으로 묶고 행은 최근 500건까지입니다. "
        "억제 판정 행은 관리자가 `include_suppressed=true`를 줄 때만 싣습니다. "
        "존 권한 밖 구성원 행은 건수(`hidden_row_count`)만 셉니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_events(
    service_id: int,
    request: Request,
    window: str = Query(default="1h", description="기간 1h·6h·24h"),
    sources: str | None = Query(
        default=None, max_length=200,
        description=(
            "소스(쉼표 복수값 · polestar_alarm·apm_event·state_change·investigation·change)"
        ),
    ),
    include_suppressed: bool = Query(default=False, description="억제 판정 포함(관리자만 유효)"),
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """타임라인 — 상세와 같은 존 필터 위에서 소스별 행을 합친다(없음·권한 밖 서비스는 404)."""
    from noise_gate.application.biz_monitor_events import (
        build_events,
        parse_sources,
        window_seconds,
    )

    try:
        seconds = window_seconds(window)
        wanted = parse_sources(sources)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    detail = await _detail_body(request, current_user, service_id)
    agg = _aggregator(request)
    service = (await agg.snapshot()).services.get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="업무 서비스를 찾을 수 없습니다.")
    timeline = _timeline(request, agg)
    events = await timeline.collect(service, window=seconds)
    operator = _is_operator(request, current_user)
    return build_events(
        events,
        service_id=service_id,
        window=window,
        visible_members=detail["members"],
        hidden_member_count=int(detail.get("hidden_member_count") or 0),
        scope=_zone_scope(request, current_user),
        sources=wanted,
        include_suppressed=include_suppressed,
        operator=operator,
        user_sub=str(current_user.get("sub") or ""),
        mask=None if operator else _mask_fn(request),
        change_lookback_minutes=timeline.change_lookback_minutes,
    )


def _history(request: Request) -> Any:
    """프로세스 안 지표 보기 조회기 1개(첫 요청에 만든다 · 플래그 off면 None)."""
    state = request.app.state
    history = getattr(state, _HISTORY_ATTR, None)
    if history is not None:
        return history
    from noise_gate.application.biz_monitor_history import build_biz_metrics_history

    history = build_biz_metrics_history(
        state.config,
        redis=getattr(getattr(state, "biz_service_cache", None), "redis", None),
    )
    if history is not None:
        setattr(state, _HISTORY_ATTR, history)
    return history


def _member_ids(raw: str | None, limit: int) -> list[int] | None:
    """`members` 쉼표 목록 → 구성원 id(비면 None = 자동 선택) — 형식·개수 밖이면 400."""
    if raw is None or not raw.strip():
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts or any(not _MEMBER_ID.match(p) for p in parts):
        raise HTTPException(status_code=400, detail="members는 쉼표로 나눈 구성원 id여야 합니다.")
    ids = list(dict.fromkeys(int(p) for p in parts))
    if len(ids) > limit:
        raise HTTPException(status_code=400, detail=f"members는 {limit}개까지입니다.")
    return ids


@router.get(
    "/{service_id}/metrics",
    summary="업무 현황 구성원별 지표 추이",
    description=(
        "상세 「지표」 보기 — 구성원별 SMS(폴스타 시간·일 통계)·APM(제니퍼 시계열) 추이를 "
        "탭을 열 때 원천에서 1회 읽습니다(60초 메모 · 저장 0).<br/>"
        "`members`를 비우면 상태 나쁜 순 → 최근 1시간 알람 수 → 이름 순으로 상한(20)까지 고릅니다. "
        "그 서비스의 구성원이 아니거나 볼 수 없는 id는 400 · 동시 조회 상한을 넘으면 429 · "
        "시간 예산(20초)을 넘은 소스는 빼고 사유와 함께 부분 결과를 돌려줍니다. "
        "자동 선택에서 존 권한 밖 구성원은 선 없이 건수만 셉니다. 기능이 꺼져 있으면 404입니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_metrics(
    service_id: int,
    request: Request,
    window: str = Query(default="24h", max_length=8, description="기간 1h·6h·24h·7d"),
    members: str | None = Query(
        default=None, max_length=512, description="조회 구성원 id(쉼표 · 상한까지 · 비우면 자동)"
    ),
    compare: str | None = Query(
        default=None, max_length=16, description="yesterday면 어제 같은 시각을 겹친다(7d 제외)"
    ),
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """지표 추이 — 상세와 같은 존 필터 위에서 조회기를 부른다(플래그 off면 404 · 관측 호출 0)."""
    from noise_gate.application.biz_monitor_history import (
        BUSY_DETAIL,
        COMPARE_YESTERDAY,
        HistoryBusy,
        history_active,
    )

    if not history_active(request.app.state.config):
        raise HTTPException(status_code=404, detail="지표 보기가 꺼져 있습니다.")
    history = _history(request)
    if history is None:
        raise HTTPException(status_code=404, detail="지표 보기가 꺼져 있습니다.")
    if compare not in (None, "", COMPARE_YESTERDAY):
        raise HTTPException(status_code=400, detail="compare는 yesterday만 받습니다.")
    ids = _member_ids(members, history.max_members)
    detail = await _detail_body(request, current_user, service_id)
    agg = _aggregator(request)
    service = (await agg.snapshot()).services.get(service_id)
    if service is None:
        raise HTTPException(status_code=404, detail="업무 서비스를 찾을 수 없습니다.")
    from noise_gate.application.biz_monitor_overview import build_topology, read_link_records

    records, reason = await read_link_records(
        getattr(request.app.state, "apm_host_map_cache", None), detail["members"]
    )
    links = build_topology(detail, records, links_reason=reason).get("edges") or []
    try:
        body: dict[str, Any] = await history.query(
            service_id=service_id,
            service=service,
            visible=detail["members"],
            hidden_member_count=int(detail.get("hidden_member_count") or 0),
            window=window,
            member_ids=ids,
            compare=compare == COMPARE_YESTERDAY,
            user=str(current_user.get("sub") or ""),
            links=links,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HistoryBusy as exc:
        raise HTTPException(status_code=429, detail=BUSY_DETAIL) from exc
    return body


def _diagnosis_error(status_code: int, code: str, reason: str, **extra: Any) -> HTTPException:
    """진단·접수 라우트의 거부 — `detail = {code, reason[, …extra]}`(알람 조사 라우트와 같다)."""
    return HTTPException(status_code=status_code, detail={"code": code, "reason": reason, **extra})


def _require_diagnosis(request: Request) -> None:
    """진단 플래그(`biz_diagnosis_active`)가 꺼져 있으면 404(진단기·관측 호출 0)."""
    ng = getattr(request.app.state.config, "noise_gate", None)
    if getattr(ng, "biz_diagnosis_active", False) is not True:
        raise _diagnosis_error(404, "biz_diagnosis_disabled", "서비스 진단이 꺼져 있습니다.")


def _diagnoser(request: Request) -> Any:
    """프로세스 안 D1 진단기 1개(첫 요청에 만든다 · 서비스 메모 60초·즉석 동시 1을 나눠 쓴다).

    플래그가 꺼져 있으면 None(게이트웨이 클라이언트·SQL 실행기 생성 0 · 보관도 하지 않는다).
    """
    state = request.app.state
    diag = getattr(state, _DIAGNOSER_ATTR, None)
    if diag is not None:
        return diag
    from noise_gate.application.biz_diagnosis import build_biz_diagnoser
    from src.api.routes.alarm import _investigation_store

    diag = build_biz_diagnoser(
        state.config,
        aggregator=_aggregator(request),
        map_cache=getattr(state, "apm_host_map_cache", None),
        investigation_store=_investigation_store(state.config),
    )
    if diag is not None:
        setattr(state, _DIAGNOSER_ATTR, diag)
    return diag


async def _diagnose(
    request: Request, current_user: dict[str, Any], service_id: int
) -> dict[str, Any]:
    """요청자 존 범위의 D1 결과(없음·전부 권한 밖·진단기 없음이면 404)."""
    diag = _diagnoser(request)
    if diag is None:
        raise _diagnosis_error(404, "biz_diagnosis_disabled", "서비스 진단이 꺼져 있습니다.")
    body: dict[str, Any] | None = await diag.diagnose(
        service_id, scope=_zone_scope(request, current_user)
    )
    if body is None:
        raise _diagnosis_error(404, "service_not_found", "업무 서비스를 찾을 수 없습니다.")
    return body


@router.get(
    "/{service_id}/diagnosis",
    summary="업무 서비스 진단 스냅샷(D1)",
    description=(
        "서비스 구성원의 이상 신호를 결정적 규칙(R1~R6)으로 판독합니다 — "
        "결과는 「가설 · 참고용」입니다.<br/>최초 이상 순 타임라인 · 대표 서버(서비스 심층 조사 "
        "대상) · 결손(연계 없음·지표 없음·권한 밖 N)을 함께 돌려줍니다. M3 최신값이 없으면 "
        "즉석 1회 조회하고 서비스당 60초 메모합니다. 존 권한 밖 구성원은 이름·값 없이 건수만 "
        "셉니다. 기능이 꺼져 있으면 404입니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_diagnosis(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """D1 — 플래그 off면 404 · 없음·권한 밖 서비스는 404."""
    _require_diagnosis(request)
    return await _diagnose(request, current_user, service_id)


@router.post(
    "/{service_id}/investigations",
    summary="업무 서비스 심층 조사 접수(D3)",
    description=(
        "D1이 고른 대표 서버 기준으로 서비스 심층 조사를 접수합니다(요청 본문 없음 · "
        "질문은 코드 템플릿).<br/>대표 서버에 호스트 조사 인가를 적용하고(거부 403 + 감사), "
        "비교 구성원은 요청자 존 안만 싣습니다. 같은 서비스 조사가 진행 중이면 기존 조사 id를 "
        "돌려줍니다(`reused`). 사용자별 상한(진행 1·시간당 N)은 알람 다시 분석·추가 조사와 "
        "공유합니다 — 시간당 상한 429면 `detail.retry_after_seconds`를 더합니다. "
        "대표 서버가 없으면 409입니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_investigate(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """D3 접수 → `{investigation_id, status, reused, service_id, representative, …}`."""
    from noise_gate.application.biz_service_investigation import (
        ENTRY_POINT,
        BizRequester,
        submit_biz_service_investigation,
    )
    from noise_gate.infrastructure.sre_agent_client import build_sre_agent_client
    from src.api.routes.alarm import (
        _investigation_store,
        _is_alarm_operator,
        _limit_extra,
        _refresh_investigation,
        _user_request_slot,
    )

    _require_diagnosis(request)
    config = request.app.state.config
    ng = config.noise_gate
    d1 = await _diagnose(request, current_user, service_id)
    cache = getattr(request.app.state, "biz_service_cache", None)
    service = await cache.get_service(service_id) if cache is not None else None
    store = _investigation_store(config)
    if store is None:
        raise _diagnosis_error(503, "store_unconfigured", "조사 저장소 경로가 설정되지 않았습니다.")
    sub = str(current_user.get("sub") or "")

    async def _audit(*, outcome: str, **fields: Any) -> None:
        from src.security import audit_logger

        await audit_logger.log_investigation(
            entry_point=ENTRY_POINT, user_id=sub or None, backend="sre_agent", outcome=outcome,
            **fields,
        )

    result = await submit_biz_service_investigation(
        cfg=config,
        service=service,
        d1=d1,
        requester=BizRequester(
            sub=sub,
            role=current_user.get("role"),
            allowed_db_ids=current_user.get("allowed_db_ids"),
            zones=_zone_scope(request, current_user),
            is_admin=_is_alarm_operator(config, current_user),
        ),
        store=store,
        client=build_sre_agent_client(ng),
        limit_slot=lambda: _user_request_slot(store, ng, sub),
        audit=_audit,
        refresh=_refresh_investigation,
    )
    if not result.ok:
        detail = dict(result.body)
        detail.update(_limit_extra(store, ng, sub, str(detail.get("code") or "")))
        raise HTTPException(status_code=result.http_status, detail=detail)
    return result.body


@router.get(
    "/{service_id}/investigations",
    summary="업무 서비스 진단 이력",
    description=(
        "그 서비스의 서비스 심층 조사 최근 10건(최신 먼저)입니다.<br/>"
        "관리자는 전부(운영자 뷰), 그 밖은 자기가 요청한 조사만(사용자 뷰 · 마스킹) 봅니다. "
        "없음·존 권한 밖 서비스는 404 · 기능이 꺼져 있으면 404입니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_investigations(
    service_id: int, request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """진단 이력 → `{service_id, items, limit, reason}`(저장소 미설정이면 빈 목록 + 사유)."""
    from noise_gate.application.biz_service_investigation import (
        HISTORY_LIMIT,
        BizRequester,
        list_biz_service_investigations,
    )
    from src.api.routes.alarm import (
        _investigation_mask_fn,
        _investigation_store,
        _is_alarm_operator,
    )

    _require_diagnosis(request)
    await _detail_body(request, current_user, service_id)  # 없음·권한 밖 서비스는 404
    config = request.app.state.config
    store = _investigation_store(config)
    if store is None:
        return {"service_id": service_id, "items": [], "limit": HISTORY_LIMIT,
                "reason": "조사 저장소 경로가 설정되지 않았습니다."}
    requester = BizRequester(
        sub=str(current_user.get("sub") or ""),
        role=current_user.get("role"),
        is_admin=_is_alarm_operator(config, current_user),
    )
    items = list_biz_service_investigations(
        store, service_id, requester, mask_fn=_investigation_mask_fn(config), limit=HISTORY_LIMIT,
    )
    return {"service_id": service_id, "items": items, "limit": HISTORY_LIMIT, "reason": ""}


@router.post(
    "/heartbeat",
    summary="업무 현황 보는 중 알림",
    description=(
        "보드가 열려 있음을 알립니다(`bizmon:viewers` 120초).<br/>"
        "지표 수집이 꺼져 있으면 기록하지 않고 `recorded=false`를 돌려줍니다."
    ),
    tags=["biz-monitor"],
)
async def biz_monitor_heartbeat(
    request: Request, current_user: dict[str, Any] = Depends(require_user)
) -> dict[str, Any]:
    """M3 수요 키 기록 — Redis가 없거나 실패하면 503(침묵 금지)."""
    from noise_gate.application.biz_monitor_alarms import VIEWERS_TTL_SECONDS, touch_viewers

    if not _metrics_active(request):
        return {"recorded": False, "ttl_seconds": 0, "reason": "지표 수집 꺼짐"}
    redis = getattr(getattr(request.app.state, "biz_service_cache", None), "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="업무 서비스 Redis 연결이 없습니다.")
    try:
        await touch_viewers(redis)
    except Exception as exc:  # noqa: BLE001 — 기록 실패는 503으로 알린다
        logger.warning("업무 현황 heartbeat 기록 실패: %s", exc)
        raise HTTPException(status_code=503, detail="보는 중 기록에 실패했습니다.") from exc
    return {"recorded": True, "ttl_seconds": VIEWERS_TTL_SECONDS, "reason": ""}
