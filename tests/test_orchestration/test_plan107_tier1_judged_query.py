"""plans/107 v2.6 · plans/111 — 1단 격리 입력의 원문 기준 판정 입력.

1단(deep_agent)은 ambient 키에 user_query가 없어(plans/103 N-1) `_make_isolated_input`이 빈
문자열로 LIMIT·실시간 의도를 판정했다 — LIMIT이 기본값에 고정되고 실시간 의도가 항상 꺼졌다.
원문이 없을 때만 task 질의로 판정한다. 2단은 원문이 있어 종전과 같아야 한다.
"""

from __future__ import annotations

from src.config import load_config
from src.orchestration.deep_agent import _extract_ambient_state
from src.orchestration.subagents import _make_isolated_input
from src.utils.query_gen_common import _ALL_QUERY_LIMIT


def _task(sub_query: str) -> dict:
    return {"task_id": "t1", "agent": "data_query", "sub_query": sub_query, "depends_on": []}


def _tier1(original: str, sub_query: str) -> dict:
    ambient = _extract_ambient_state({"user_query": original, "thread_id": "t"})
    assert "user_query" not in ambient  # 전제 — 1단 ambient에는 원문이 없다
    return _make_isolated_input(_task(sub_query), ambient, prior={})


def test_tier1_all_scope_sub_query_lifts_limit():
    iso = _tier1("은행존 모든 서버 목록 보여줘", "은행존 모든 서버 목록 조회")
    assert iso["resolved_limit"] == _ALL_QUERY_LIMIT


def test_tier1_plain_sub_query_keeps_default_limit():
    iso = _tier1("은행존 서버 목록 보여줘", "은행존 서버 목록 조회")
    assert iso["resolved_limit"] == load_config().query.default_limit


def test_tier1_top_n_sub_query_uses_n():
    iso = _tier1("CPU 사용률 상위 5대 서버", "CPU 사용률 상위 5대 서버 조회")
    assert iso["resolved_limit"] == 5


def test_tier1_realtime_intent_from_sub_query():
    iso = _tier1("지금 CPU 사용률 알려줘", "지금 CPU 사용률 조회")
    assert iso["realtime_usage_intent"] is True


def test_tier2_still_judges_on_original():
    """2단은 전체 상태에 원문이 있다 — task 질의가 "모든"을 잃어도 원문 기준이다(종전과 동일)."""
    state = {"user_query": "은행존 모든 서버 목록 보여줘", "thread_id": "t"}
    iso = _make_isolated_input(_task("은행존 서버 목록 조회"), state, prior={})
    assert iso["resolved_limit"] == _ALL_QUERY_LIMIT


def test_promoted_limit_still_wins():
    state = {"user_query": "서버 목록", "resolved_limit": 42, "thread_id": "t"}
    iso = _make_isolated_input(_task("모든 서버 목록"), state, prior={})
    assert iso["resolved_limit"] == 42
