"""담당 업무 라우트 공용 헬퍼 (plans/201 W1 · D-354).

가입(`/auth/register`)·본인 조회(`/auth/me`)·관리자 사용자 수정·목록이 같은 규칙으로 담당 업무를
다루도록 판정을 한곳에 둔다.

- 플래그(`DUTY_AREA_ENABLED`)는 **기동 시 1회** 해석해 `app.state.duty_area_enabled`에 둔다
  (`create_app`). 설정 리로드가 `app.state.config`를 바꿔도 이 값은 그대로다(재시작 반영).
- 어휘 밖 코드는 **422**다 — 조용히 버리지 않는다.
- 저장돼 있으나 어휘에서 빠진(폐지) 코드는 **읽을 때 무시 + 경고 1줄**이다.

담당 업무는 권한이 아니다 — 인가 의존성(`src/api/dependencies.py`)은 이 모듈을 읽지 않는다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from fastapi import HTTPException, Request

from src.domain.duty_areas import DutyAreaVocabulary, UnknownDutyAreaError
from src.infrastructure.duty_area_vocabulary import load_duty_area_vocabulary

logger = logging.getLogger(__name__)


def duty_area_enabled(request: Request) -> bool:
    """이 기동에서 담당 업무 기능이 켜졌는가(기동 시 1회 해석한 값 · 없으면 off)."""
    return bool(getattr(request.app.state, "duty_area_enabled", False))


def duty_area_vocabulary() -> DutyAreaVocabulary:
    """정본 어휘(프로세스당 1회 읽음)."""
    return load_duty_area_vocabulary()


def duty_alarm_focus_enabled(request: Request) -> bool:
    """이 기동에서 알람 화면 담당 강조가 켜졌는가(plans/201 W3 · 기동 시 1회 해석 · 없으면 off).

    유효값은 `DUTY_AREA_ENABLED` AND `DUTY_ALARM_FOCUS_ENABLED`다(`create_app`이 둘을 함께 본다).
    """
    return bool(getattr(request.app.state, "duty_alarm_focus_enabled", False))


def duty_alarm_focus_meta() -> dict[str, Any]:
    """`/auth/me` `duty_alarm_focus` 메타(plans/201 W3) — 화면은 어휘 사본 없이 이 값만 읽는다.

    - `areas`: 영역별 `resource_types`(코드값·표시 이름 · 대조는 대소문자 무시) — 발생 영역 판정용.
    - `cause_hints`: 단서 표(`apm_event_types`(정규화 유형 키)·`process_kinds`(kind 키)) — 단서 판정용.

    추천 질의 우선순위는 따로 싣지 않는다 — 추천의 `axis`(자원 유형 코드값)를 위 `resource_types`로
    영역에 대응시켜 재정렬한다(새 표를 두지 않는다). 공개 메타(`GET /auth/duty-areas`)에는 싣지 않는다.
    """
    vocab = duty_area_vocabulary()
    return {
        "areas": [
            {"code": area.code, "resource_types": list(area.resource_types)} for area in vocab.areas
        ],
        "cause_hints": vocab.cause_hints.to_meta(),
    }


def duty_welcome_hints_enabled(request: Request) -> bool:
    """이 기동에서 첫 화면 담당별 예시 질의가 켜졌는가(plans/201 W2 · 기동 시 1회 해석 · 없으면 off).

    유효값은 `DUTY_AREA_ENABLED` AND `DUTY_WELCOME_HINTS_ENABLED`다(`create_app`이 둘을 함께 본다).
    """
    return bool(getattr(request.app.state, "duty_welcome_hints_enabled", False))


#: 첫 화면 예시 질의 상한 — 영역당 · 전체(plans/201 §4.1).
WELCOME_QUERIES_PER_AREA = 3
WELCOME_QUERIES_TOTAL = 6


def duty_welcome_queries(areas: list[str] | None) -> list[dict[str, str]]:
    """`/auth/me` `duty_welcome_queries` — 사용자 담당 영역의 첫 화면 예시 질의(plans/201 W2).

    어휘 선언 순서로 영역마다 최대 3개 · 전체 최대 6개 `[{area, area_label, label, query}]`. 검증 근거
    (`verified_by`) 없는 문구는 로더가 이미 뺐다. 미지정·문구 없음은 빈 목록 — 화면은 현행 버튼을 유지한다.
    """
    return duty_area_vocabulary().select_welcome_queries(
        areas, per_area=WELCOME_QUERIES_PER_AREA, total=WELCOME_QUERIES_TOTAL
    )


def welcome_guide_enabled(request: Request) -> bool:
    """이 기동에서 첫 화면 상황 안내가 켜졌는가(plans/201 W2 P2 · 담당 업무와 독립 · 기동 시 1회 · 없으면 off)."""
    return bool(getattr(request.app.state, "welcome_guide_enabled", False))


#: 요청 `duty_areas` 목록 길이 상한 · 원소 길이 상한(무인증 가입 경로 남용 방지).
MAX_DUTY_AREAS = 32
MAX_DUTY_AREA_CODE_LENGTH = 64
#: 422 detail에 되돌려 보이는 어휘 밖 값 개수(나머지는 총 개수로만 알린다).
_UNKNOWN_ECHO_LIMIT = 5


def validate_duty_areas(values: Iterable[str] | None) -> list[str] | None:
    """요청 값을 검증·정규화한다 — 어휘 밖이면 422(중복 제거 · 선언 순서 · 빈 목록 = None).

    목록 길이(32)·원소 길이(64) 상한을 넘어도 422다. 상한은 pydantic 제약이 아니라 여기서
    본다 — 검증 오류 응답이 입력 전체를 되돌려 싣지 않게 하기 위해서다.
    """
    if values is None:
        return None
    items = list(values)
    if len(items) > MAX_DUTY_AREAS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"담당 업무는 최대 {MAX_DUTY_AREAS}개까지 지정할 수 있습니다"
                f"(받은 개수 {len(items)})."
            ),
        )
    if any(len(str(v)) > MAX_DUTY_AREA_CODE_LENGTH for v in items):
        raise HTTPException(
            status_code=422,
            detail=f"담당 업무 코드는 {MAX_DUTY_AREA_CODE_LENGTH}자 이하여야 합니다.",
        )
    try:
        return duty_area_vocabulary().normalize_codes(items)
    except UnknownDutyAreaError as exc:
        shown = ", ".join(exc.unknown[:_UNKNOWN_ECHO_LIMIT])
        more = len(exc.unknown) - _UNKNOWN_ECHO_LIMIT
        suffix = f" 외 {more}개(총 {len(exc.unknown)}개)" if more > 0 else ""
        raise HTTPException(
            status_code=422,
            detail="알 수 없는 담당 업무입니다: " + shown + suffix,
        ) from exc


def effective_duty_areas(user_id: str, stored: Iterable[str] | None) -> list[str] | None:
    """저장 값에서 폐지 코드를 걸러 읽는다(폐지 코드가 있으면 경고 1줄 · 남은 게 없으면 None)."""
    kept, dropped = duty_area_vocabulary().split_known(stored)
    if dropped:
        logger.warning(
            "담당 업무 폐지 코드 무시(user=%s): %s — config/duty_areas.yaml에 없음",
            user_id, ", ".join(dropped),
        )
    return kept or None


def duty_area_options() -> list[dict[str, str]]:
    """화면용 영역 메타 `[{code, label}]`(선언 순서)."""
    return duty_area_vocabulary().options()


def duty_response_fields(request: Request, user: Any) -> dict[str, Any]:
    """사용자 응답에 더할 담당 업무 필드. 플래그 off면 빈 dict(응답 키 부재 · 현행 동일)."""
    if not duty_area_enabled(request):
        return {}
    return {"duty_areas": effective_duty_areas(user.user_id, user.duty_areas)}
