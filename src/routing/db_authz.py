"""사용자별 DB 조회 인가 (plans/104 C-4 · D-232).

`User.allowed_db_ids`는 D-026 때 만들어졌지만 **질의 경로가 한 번도 강제하지 않았다** —
스코프 칩·범위 선택·호스트 탐색만 좁히고 라우터는 활성 DB 전체로 갔다(Plan 41 미구현).
이 모듈이 그 강제의 단일 지점이다.

규약(세 값의 의미를 여기서 못 박는다):
- `None`  = 전체 허용(기존 사용자·개발 모드 익명 사용자·seed admin의 기본값 — 종전 동작 보존)
- `[]`    = **조회 가능 DB 없음**(신규 가입자 기본값이 될 수 있다 — 안전 실패)
- 목록    = 그 목록과 활성 DB의 교집합만

역할 예외: `admin`은 목록과 무관하게 전체를 본다 — 알림 존 RBAC에서 관리자를 전 존으로 보는
D-082와 같은 기준이다. 관리 화면에서 권한을 부여·회수하는 주체가 스스로 잠기지 않게 한다.

계층: infrastructure(`src/routing`). 라우터 노드를 감싸는 얇은 래퍼만 두고, 판정은 순수 함수다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

ADMIN_ROLE = "admin"

#: 라우터가 DB를 고르지 않는 의도 — 인가 필터를 적용하지 않고 그대로 통과시킨다.
_NON_DATA_INTENTS: frozenset[str] = frozenset({
    "cache_management",
    "synonym_registration",
    "general_inference",
    "fault_diagnosis",
    "zone_clarification",
})

#: 인가된 DB가 하나도 없을 때의 라우팅 의도 — `route_after_semantic_router`가 END로 보낸다.
ACCESS_DENIED_INTENT = "access_denied"

ACCESS_DENIED_MESSAGE = (
    "조회할 수 있는 DB가 없습니다. 관리자에게 DB 접근 권한을 요청하세요 "
    "(관리자 페이지 「사용자 관리」 탭에서 부여합니다)."
)


def parse_allowed_db_ids(raw: str | None) -> list[str]:
    """`AUTH_DEFAULT_ALLOWED_DB_IDS` 문자열을 db_id 목록으로 파싱한다(쉼표 구분).

    빈 문자열·None이면 **빈 목록**이다 — "아무 DB도 열지 않는다"가 이 설정의 기본값이고,
    전체 허용(`None`)과 구분된다.
    """
    if not raw:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def is_admin(role: str | None) -> bool:
    """관리자 역할인지(전체 허용 예외 — D-082 대칭)."""
    return (role or "").strip().lower() == ADMIN_ROLE


def authorized_db_ids(
    active_db_ids: Sequence[str],
    allowed_db_ids: Sequence[str] | None,
    role: str | None = None,
) -> list[str]:
    """이 사용자가 조회할 수 있는 활성 DB 목록을 돌려준다(결정적 · LLM 0).

    Args:
        active_db_ids: 활성 DB 목록(`ACTIVE_DB_IDS`)
        allowed_db_ids: 사용자 허용 목록 — `None`이면 전체 허용, `[]`면 없음
        role: 사용자 역할 — `admin`이면 목록과 무관하게 전체

    Returns:
        인가된 db_id 목록(활성 순서 보존)
    """
    active = [db_id for db_id in active_db_ids if db_id]
    if allowed_db_ids is None or is_admin(role):
        return active
    allowed = {db_id for db_id in allowed_db_ids if db_id}
    return [db_id for db_id in active if db_id in allowed]


def is_db_access_denied(
    allowed_db_ids: Sequence[str] | None, role: str | None = None
) -> bool:
    """조회 가능 DB가 하나도 없는 사용자인지(빈 목록 + 관리자 아님)."""
    if allowed_db_ids is None or is_admin(role):
        return False
    return not [db_id for db_id in allowed_db_ids if db_id]


SELECTION_DENIED_MESSAGE = (
    "선택하신 DB는 조회 권한이 없습니다. 관리자에게 권한을 요청하거나 선택을 바꿔 주세요."
)


def filter_selected_db_ids(
    selected_db_ids: Sequence[str] | None,
    allowed_db_ids: Sequence[str] | None,
    role: str | None = None,
) -> tuple[list[str] | None, list[str]]:
    """요청 본문의 DB 선택(`selected_db_ids`)에 인가를 적용한다(plans/104 C-4 · D-232).

    선택 값은 **외부 입력**이라 라우터 반환값 필터(`filter_router_result`)로는 막히지 않는다 —
    그대로 상태에 실리면 존 선택 재개 턴의 task 고정(`subagents`)이 그 DB로 조회를 확정한다.
    요청 경계에서 같은 규칙으로 거른다.

    Args:
        selected_db_ids: 요청이 지정한 DB 목록(없으면 None)
        allowed_db_ids: 사용자 허용 목록(`None`=전체 · `[]`=없음)
        role: 사용자 역할(`admin`이면 전체)

    Returns:
        `(인가된 선택, 제외된 db_id 목록)` — 선택이 없으면 `(None, [])`,
        전부 비인가면 `([], 제외 목록)`
    """
    selected = [db_id for db_id in (selected_db_ids or []) if db_id]
    if not selected:
        return None, []
    if allowed_db_ids is None or is_admin(role):
        return list(selected), []
    allowed = {db_id for db_id in allowed_db_ids if db_id}
    kept = [db_id for db_id in selected if db_id in allowed]
    dropped = [db_id for db_id in selected if db_id not in allowed]
    if dropped:
        logger.info(
            "인가되지 않은 DB 선택 제외: %s (남은 선택=%s · 역할=%s)", dropped, kept, role
        )
    return kept, dropped


def filter_router_result(
    result: Mapping[str, Any],
    allowed_db_ids: Sequence[str] | None,
    role: str | None = None,
) -> dict[str, Any]:
    """라우터 산출물에서 인가되지 않은 대상 DB를 제거한다.

    DB를 고르지 않는 의도(캐시 관리·유사어·일반 추론·장애 진단·존 역질문)는 그대로 둔다.
    인가된 대상이 하나도 남지 않으면 사유를 담아 `access_denied`로 종결한다 — 빈 대상으로
    파이프라인을 계속 보내면 "데이터 없음"처럼 보여 권한 문제가 가려진다(침묵 강등 금지).
    """
    out = dict(result)
    if allowed_db_ids is None or is_admin(role):
        return out
    if out.get("routing_intent") in _NON_DATA_INTENTS:
        return out

    targets = [t for t in (out.get("target_databases") or []) if isinstance(t, dict)]
    if not targets:
        return out

    allowed = {db_id for db_id in allowed_db_ids if db_id}
    kept = [t for t in targets if t.get("db_id") in allowed]
    dropped = [t.get("db_id") for t in targets if t.get("db_id") not in allowed]

    if not kept:
        logger.info(
            "DB 조회 인가 거부: 대상=%s, 허용=%s (역할=%s)", dropped, sorted(allowed), role
        )
        return {
            **out,
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": ACCESS_DENIED_INTENT,
            "final_response": ACCESS_DENIED_MESSAGE,
        }

    if dropped:
        logger.info(
            "인가되지 않은 대상 DB 제외: %s (남은 대상=%s)",
            dropped, [t.get("db_id") for t in kept],
        )
    out["target_databases"] = kept
    out["is_multi_db"] = len(kept) > 1
    out["active_db_id"] = kept[0].get("db_id")
    if out.get("user_specified_db") not in allowed:
        out["user_specified_db"] = kept[0].get("db_id") if len(kept) == 1 else None
    return out


async def authorized_router(
    state: Mapping[str, Any],
    *,
    inner: Callable[..., Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """라우터 노드를 감싸 **모든 반환 경로**에 사용자별 DB 인가를 적용한다.

    라우터 본체는 반환 지점이 여러 곳이라 그 안에서 거르면 빠뜨리기 쉽다. 노드 경계에서
    한 번만 거른다 — 단일 DB(`active_db_id`)·멀티 DB(`target_databases`) 양쪽이 이 결과에서
    파생되므로 하위 경로가 자동으로 대칭이 된다.

    Args:
        state: 에이전트 상태(`allowed_db_ids`·`user_role`)
        inner: 실제 라우터 노드(부분 적용된 `semantic_router`)
    """
    result = await inner(state)
    return filter_router_result(
        result, state.get("allowed_db_ids"), state.get("user_role")
    )
