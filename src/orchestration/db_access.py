"""1·2단 task 핸들러의 사용자별 DB 조회 인가 (D-232 · plans/116 §10.3 결함 ②).

3단은 `graph.py`가 `semantic_router`를 `authorized_router`로 감싸 라우터 노드 경계에서 거른다.
1·2단(`deep_agent`·`intent_orchestration`)에는 라우터가 없고 조회 대상 DB를 task 핸들러
(`run_data_query_pipeline`·`run_process_query`·`run_host_inspect`)가 각자 정한다 — 그래서
같은 판정을 **핸들러가 대상을 확정한 지점**에 건다. 규칙은 새로 만들지 않고 3단과 같은 함수
(`src/routing/db_authz`)를 부른다(단일 출처).

- `None`·관리자 = 제한 없음 · `[]` = 조회 불가 · 목록 = 그 안의 DB만
- 거른 결과 대상이 없으면 조회하지 않고 3단과 같은 의도·문구로 끝낸다(침묵 강등 금지)

3단 순차 러너(`sequential_runner`)·계획 루프의 비데이터 task도 같은 핸들러를 부르므로 함께 적용된다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.routing.db_authz import (
    ACCESS_DENIED_INTENT,
    ACCESS_DENIED_MESSAGE,
    filter_router_result,
    is_db_access_denied,
)


def denied_for_all(isolated: Mapping[str, Any]) -> bool:
    """조회 가능 DB가 하나도 없는 사용자인가(빈 목록 + 관리자 아님)."""
    return is_db_access_denied(isolated.get("allowed_db_ids"), isolated.get("user_role"))


def authorize_targets(
    targets: Sequence[Mapping[str, Any]], isolated: Mapping[str, Any]
) -> list[dict[str, Any]] | None:
    """대상 DB 목록에서 인가된 것만 남긴다 — 3단 라우터 결과 필터와 같은 함수다.

    Returns:
        인가된 대상 목록(순서 보존). 하나도 남지 않으면 None(거부).
    """
    out = filter_router_result(
        {"target_databases": [dict(t) for t in targets]},
        isolated.get("allowed_db_ids"),
        isolated.get("user_role"),
    )
    if out.get("routing_intent") == ACCESS_DENIED_INTENT:
        return None
    return list(out.get("target_databases") or [])


def access_denied_result(message: str = ACCESS_DENIED_MESSAGE) -> dict[str, Any]:
    """조회를 거부한 task 결과 — 3단 `access_denied` 종결과 같은 의도·문구.

    텍스트 결과라 집계기가 문구를 그대로 쓴다(LLM 0). `target_db_ids`를 남기지 않아 거부된
    DB가 다음 턴 승계(`previous_db_ids`)로 새지 않는다(존 역질문 반환과 같은 사유).
    관측 소스 거부(plans/125 A-7)는 소스 이름을 밝히지 않는 문구를 넘긴다.
    """
    return {
        "final_response": message,
        "routing_intent": ACCESS_DENIED_INTENT,
        "source": [],
    }


def is_access_denied_result(result: object) -> bool:
    """`access_denied_result()`로 끝난 task 결과인가."""
    return isinstance(result, Mapping) and result.get("routing_intent") == ACCESS_DENIED_INTENT
