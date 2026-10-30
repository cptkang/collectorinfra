"""담당 업무 라우트 (plans/201 W1 · D-354).

`DUTY_AREA_ENABLED` 켠 기동에서만 앱 조립(`create_app`)이 이 라우터를 등록한다 — 끄면 경로가 없어
404다(현행과 같다).

- `GET /auth/duty-areas` — 가입 화면용 영역 메타(무인증 · 어휘 공개값만). 읽기 전용이다 —
  본인이 담당 업무를 바꾸는 경로는 없다(D-354 부기 4 ① — 가입 시 선택 + 관리자 변경만).
- `GET /admin/duty-areas/status` — 관리자 「담당 업무 현황」 카드: 지정률 + 매핑 점검.
  매핑 점검은 노이즈 결정 기록(`/noise/summary`와 같은 저장소)의 최근 7일 판정 레코드에서
  **볼 때마다** 센다 — 새 파일·메모리 계수기 없음.

응답의 `sections`는 점검 항목 목록이다 — 뒤 Wave가 제니퍼 `event_type_norm` 점검·알려진 미매핑
(`known_unmapped`) 분리를 항목으로 더한다(W1은 `resource_type` 항목 하나).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from src.api.dependencies import require_admin_user
from src.api.duty_area_support import (
    duty_alarm_focus_enabled,
    duty_area_vocabulary,
    effective_duty_areas,
)
from src.api.routes.noise_dashboard import _decision_store
from src.domain.duty_areas import unmapped_apm_event_types, unmapped_resource_types

logger = logging.getLogger(__name__)
router = APIRouter()

#: 매핑 점검 창(일).
MAPPING_WINDOW_DAYS = 7
#: 미매핑 상위 개수.
MAPPING_TOP_N = 5
#: 미매핑 조치 안내(관리자 카드에 그대로 보인다).
RESOURCE_TYPE_ACTION_HINT = "config/duty_areas.yaml의 resource_types에 추가하세요"
#: (plans/201 W3) 제니퍼 이벤트 유형 점검 — 판정 레코드의 정규화 유형 위치(중첩 점 경로).
APM_EVENT_TYPE_FIELD = "stage_evidence.apm_policy.event_type_norm"
#: (plans/201 W3) 유형 미매핑 조치 안내.
APM_EVENT_TYPE_ACTION_HINT = (
    "config/duty_areas.yaml cause_hints의 apm_event_types(원인 영역이 정해지면) "
    "또는 known_unmapped(유형만으로 못 가르면)에 추가하세요"
)


@router.get("/auth/duty-areas")
async def duty_area_options_public() -> dict[str, Any]:
    """가입 화면이 그릴 담당 업무 영역 메타 `[{code, label}]`(무인증 · 읽기 전용)."""
    return {"options": duty_area_vocabulary().options()}


async def _assignment_summary(request: Request) -> dict[str, Any] | None:
    """사용자 담당 지정률. 사용자 저장소가 없으면 None."""
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        return None
    vocab = duty_area_vocabulary()
    users = await user_repo.list_all()
    by_area = {code: 0 for code in vocab.codes()}
    assigned = 0
    for user in users:
        areas = effective_duty_areas(user.user_id, user.duty_areas)
        if not areas:
            continue
        assigned += 1
        for code in areas:
            by_area[code] = by_area.get(code, 0) + 1
    return {
        "total_users": len(users),
        "assigned_users": assigned,
        "unassigned_users": len(users) - assigned,
        "by_area": [
            {"code": area.code, "label": area.label, "users": by_area.get(area.code, 0)}
            for area in vocab.areas
        ],
    }


async def _resource_type_section(request: Request) -> dict[str, Any]:
    """매핑 점검 — 최근 7일 판정 레코드의 미매핑 `resource_type`.

    결정 기록 전량 스캔은 동기 파일 I/O라 스레드에서 돈다(이벤트 루프 정지 방지). 레코드를
    쌓지 않고 값별 건수만 센다(`DecisionStore.count_recent_values`).
    """
    store = _decision_store(request)
    counts, empty = await asyncio.to_thread(
        store.count_recent_values, MAPPING_WINDOW_DAYS * 86400, "resource_type"
    )
    result = unmapped_resource_types(
        duty_area_vocabulary(), counts, empty_count=empty, top_n=MAPPING_TOP_N
    )
    return {
        "key": "resource_type",
        "title": "알람 자원 유형",
        "action_hint": RESOURCE_TYPE_ACTION_HINT,
        **result,
    }


async def _apm_event_type_section(request: Request) -> dict[str, Any]:
    """매핑 점검 — 최근 7일 판정 레코드의 제니퍼 이벤트 유형 중 단서 미매핑(plans/201 W3).

    `cause_hints.known_unmapped`(알려진 미매핑)는 `known_unmapped`로 접고 배지에서 뺀다.
    대조는 정규화 규칙(`normalize_apm_event_type`)이다. 문턱(최소 건수)은 두지 않는다.
    """
    store = _decision_store(request)
    counts, empty = await asyncio.to_thread(
        store.count_recent_values, MAPPING_WINDOW_DAYS * 86400, APM_EVENT_TYPE_FIELD
    )
    result = unmapped_apm_event_types(
        duty_area_vocabulary(), counts, empty_count=empty, top_n=MAPPING_TOP_N
    )
    return {
        "key": "apm_event_type",
        "title": "제니퍼 이벤트 유형",
        "action_hint": APM_EVENT_TYPE_ACTION_HINT,
        **result,
    }


@router.get("/admin/duty-areas/status")
async def duty_area_status(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
    mapping: bool = Query(True, description="false면 매핑 점검을 건너뛰고 지정률만 돌려준다"),
) -> dict[str, Any]:
    """관리자 「담당 업무 현황」 — 영역 메타 · 지정률 · 매핑 점검(볼 때마다 계산).

    `badge_count`는 탭 배지 숫자다 — 점검 항목들의 미매핑 유형 **가짓수** 합(0이면 배지 없음 ·
    W3 계약). `mapping=false`(담당 체크 변경 직후 지정률 갱신용)면 결정 기록을 읽지 않고
    `sections`·`badge_count`를 null로 둔다 — 화면은 직전 점검 결과를 그대로 쓴다.
    """
    body: dict[str, Any] = {
        "options": duty_area_vocabulary().options(),
        "window_days": MAPPING_WINDOW_DAYS,
        "assignment": await _assignment_summary(request),
        "sections": None,
        "badge_count": None,
    }
    if mapping:
        sections = [await _resource_type_section(request)]
        # (plans/201 W3) 알람 화면 담당 강조가 켜진 기동에서만 제니퍼 유형 항목을 더한다(끄면 W1 응답 동일)
        if duty_alarm_focus_enabled(request):
            sections.append(await _apm_event_type_section(request))
        body["sections"] = sections
        body["badge_count"] = sum(int(s.get("unmapped_distinct") or 0) for s in sections)
    return body
