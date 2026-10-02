"""plans/132 W0·W1 — 데이터 소스 명시 인식·유사어·소스 불가 처분 (D-293).

실측(plans/132 §3)에서 드러난 결함을 재현하고 고정한다 — LLM 0회:

1. 유사어 정본 — 레지스트리 `solutions[].aliases`(비DB) · 단독 DB `aliases` · 위치·제품 표면어와
   겹치지 않음
2. 입력 파서 결정적 보강 — 원문의 소스 이름·유사어를 `target_db_hints`에 더한다(D-065 방식)
3. 계획 출구 고정 — 지목한 비DB 시스템이 활성이면 그 처리기로, 지목 소스가 전부 비활성이면
   안내만(G-1)
4. SQL 처리기 가드 — 1단·재계획 후속이 지목 소스를 SQL로 대신 답하지 않는다(G-1 · G-5)
5. 재계획 소스 불가 종결 — 제니퍼 0건 뒤 폴스타 대체 task를 만들지 않는다(G-4 · 실측 4/4 재현)
6. APM 기본 보기 — 대상 없는 질의는 인스턴스 목록(G-3 · 실측 views 비움 4/4)
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.nodes.input_parser import _ensure_source_hints
from src.orchestration import apm_query as aq
from src.orchestration import conditional_agents as ca
from src.orchestration import subagents
from src.orchestration.replanner import _drop_terminal_substitutes, replanner
from src.routing.registry import get_registry
from src.routing.source_hints import (
    KIND_DB,
    KIND_NON_DB,
    is_mention_active,
    resolve_source_mentions,
    source_notice_text,
)
from src.state import create_initial_state

# ── 공용 ───────────────────────────────────────────────────────────────────────


def _cfg(active_db_ids: list[str] | None = None) -> SimpleNamespace:
    ids = ["polestar", "itam"] if active_db_ids is None else active_db_ids
    return SimpleNamespace(multi_db=SimpleNamespace(get_active_db_ids=lambda: list(ids)))


@pytest.fixture
def systems(monkeypatch):
    """활성 비DB 시스템을 테스트마다 정한다(실제 엔드포인트·문서 설정과 무관)."""
    state: dict[str, dict[str, str]] = {"value": {}}
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _cfg: dict(state["value"]))

    def set_active(**pairs: str) -> None:
        state["value"] = dict(pairs)

    return set_active


def _task(tid: str, agent: str, sub_query: str, **extra: Any) -> dict[str, Any]:
    return {"task_id": tid, "agent": agent, "sub_query": sub_query, "depends_on": [],
            "input_from": [], "order": int(tid[1:]), "status": "pending", **extra}


# ── 1. 유사어 정본 ─────────────────────────────────────────────────────────────


def test_source_aliases_are_registry_canon() -> None:
    reg = get_registry()
    assert "제니퍼" in reg.solution_aliases("apm") and "APM" in reg.solution_aliases("apm")
    assert "프로메테우스" in reg.solution_aliases("prometheus")
    assert not reg.views_of("prometheus") and not reg.capability_owners("metric_series")
    terms = reg.source_alias_terms()
    assert "제니퍼" in terms and "자산관리" in terms and "전산관리매뉴얼" in terms
    # 위치·환경·제품 표면어는 위치어 보강(D-065)의 몫 — 겹치지 않는다
    reserved = set(reg.location_signal_terms()) | set(reg.product_terms())
    assert not reserved & set(terms)


def test_no_alias_points_to_two_systems() -> None:
    """같은 유사어가 두 시스템을 가리키면 고정이 흔들린다 — 정본 무결성."""
    owners: dict[str, set[str]] = {}
    for term in get_registry().source_alias_terms():
        for mention in resolve_source_mentions([term]):
            owners.setdefault(term.lower(), set()).add(mention.system)
    assert {t: s for t, s in owners.items() if len(s) > 1} == {}


def test_source_alias_breaks_db_succession() -> None:
    """「제니퍼」를 쓴 턴은 직전 폴스타 턴의 DB를 이어받지 않는다(S-9 ④)."""
    assert "제니퍼" in get_registry().new_db_signal_terms()


# ── 2. 입력 파서 결정적 보강 ───────────────────────────────────────────────────


@pytest.mark.parametrize(("query", "expected"), [
    ("제니퍼의 인스턴스 리스트를 보여줘", "제니퍼"),
    ("jennifer 인스턴스 목록", "JENNIFER"),
    ("APM에서 응답시간 보여줘", "APM"),
    ("프로메테우스에서 서버 CPU 사용률 보여줘", "프로메테우스"),
    ("자산관리 담당자 목록", "자산관리"),
])
def test_source_alias_in_query_is_added_to_hints(query: str, expected: str) -> None:
    parsed = _ensure_source_hints({"target_db_hints": []}, query)
    assert expected in parsed["target_db_hints"]


def test_source_hint_not_duplicated_and_latin_word_boundary() -> None:
    parsed = _ensure_source_hints({"target_db_hints": ["Jennifer"]}, "Jennifer 인스턴스")
    assert parsed["target_db_hints"] == ["Jennifer"], "이미 있는 이름은 다시 더하지 않는다"
    for query in ("서버 CPU 사용률 상위 10", "APMS 계정 목록", "김포 운영 서버"):
        assert _ensure_source_hints({"target_db_hints": []}, query)["target_db_hints"] == []


# ── 소스 해소 ─────────────────────────────────────────────────────────────────


def test_resolve_mentions_by_system() -> None:
    by_hint = {h: resolve_source_mentions([h]) for h in
               ("제니퍼", "APM 서버", "ITAM/itam", "김포", "web01", "전산관리매뉴얼")}
    assert [(m.system, m.kind) for m in by_hint["제니퍼"]] == [("apm", KIND_NON_DB)]
    assert [m.system for m in by_hint["APM 서버"]] == ["apm"]
    assert [(m.system, m.kind) for m in by_hint["ITAM/itam"]] == [("itam", KIND_DB)]
    assert [m.system for m in by_hint["김포"]] == ["polestar"]
    assert by_hint["web01"] == [], "서버명은 소스 지목이 아니다"
    assert [m.system for m in by_hint["전산관리매뉴얼"]] == ["doc"]


def test_activity_is_per_system() -> None:
    [gimpo] = resolve_source_mentions(["김포"])
    # 김포 DB가 비활성이어도 폴스타 시스템에 활성 DB가 있으면 활성(존 사이 처분은 위치 규칙)
    assert is_mention_active(gimpo, active_db_ids=["polestar"], active_non_db=())
    [itam] = resolve_source_mentions(["ITAM"])
    assert not is_mention_active(itam, active_db_ids=["polestar"], active_non_db=())
    [apm] = resolve_source_mentions(["제니퍼"])
    assert is_mention_active(apm, active_db_ids=[], active_non_db={"apm"})
    assert not is_mention_active(apm, active_db_ids=["polestar"], active_non_db=())


def test_notice_uses_user_words_only() -> None:
    text = source_notice_text(["제니퍼"])
    assert "「제니퍼」" in text and "대신 답하지 않았습니다" in text
    assert "APM · WAS" not in text, "레지스트리 표시명 비노출(D-264)"


# ── 3. 계획 출구 고정 ─────────────────────────────────────────────────────────


def test_named_active_system_pins_handler(systems) -> None:
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "제니퍼 인스턴스 리스트 조회")]
    assert ca.apply_explicit_sources(tasks, ["제니퍼"], _cfg()) == ["t1"]
    assert tasks[0]["agent"] == "apm_query" and tasks[0]["views"] == []


def test_named_inactive_system_is_notice_only(systems) -> None:
    """실측 C2-1 — 제니퍼 미연결이면 폴스타 SQL 대신 안내만(G-1 「안내만」)."""
    systems()
    tasks = [_task("t1", "data_query", "제니퍼 인스턴스 리스트 조회")]
    ca.apply_explicit_sources(tasks, ["제니퍼"], _cfg())
    assert tasks[0]["agent"] == "general_inference"
    assert "「제니퍼」" in tasks[0]["direct_response"]
    assert tasks[0][ca.SOURCE_NOTICE_KEY] == ["apm"]


def test_unregistered_like_prometheus_is_notice_only(systems) -> None:
    """실측 B-5 — 「프로메테우스에서」가 폴스타 CPU로 대체되지 않는다."""
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "프로메테우스에서 서버 CPU 사용률 조회")]
    ca.apply_explicit_sources(tasks, ["프로메테우스"], _cfg())
    assert tasks[0]["agent"] == "general_inference"
    assert "「프로메테우스」" in tasks[0]["direct_response"]


def test_composite_plan_scopes_by_task_text(systems) -> None:
    """복합 계획은 task 질의에 이름이 남은 지목만 본다 — 폴스타 단계는 그대로 간다(M-03 모양)."""
    systems()
    tasks = [
        _task("t1", "alarm_query", "폴스타에서 활성 심각 알람 서버 조회"),
        _task("t2", "data_query", "선행 결과 서버들의 CPU 추이를 프로메테우스에서 조회",
              depends_on=["t1"], input_from=["t1"]),
    ]
    assert ca.apply_explicit_sources(tasks, ["폴스타", "프로메테우스"], _cfg()) == ["t2"]
    assert tasks[0]["agent"] == "alarm_query"
    assert tasks[1]["agent"] == "general_inference" and tasks[1]["depends_on"] == ["t1"]


def test_untouched_cases(systems) -> None:
    systems(apm="apm_query")
    concept = [_task("t1", "general_inference", "제니퍼가 뭐야")]
    mixed = [_task("t1", "data_query", "ITAM 담당자와 제니퍼 인스턴스")]
    zone = [_task("t1", "data_query", "김포 서버 CPU 사용률 상위 10")]
    planned = [_task("t1", "data_query", "제니퍼 인스턴스", db_ids=["polestar"])]
    assert ca.apply_explicit_sources(concept, ["제니퍼"], _cfg()) == [], "개념 설명은 그대로"
    assert ca.apply_explicit_sources(mixed, ["ITAM", "제니퍼"], _cfg()) == [], \
        "활성 DB 지목이 섞이면 그대로"
    assert ca.apply_explicit_sources(zone, ["김포"], _cfg()) == [], \
        "같은 시스템 존 사이는 위치 규칙 몫"
    assert ca.apply_explicit_sources(planned, ["제니퍼"], _cfg()) == [], \
        "계획이 DB를 고정한 task 제외"
    assert ca.apply_explicit_sources([_task("t1", "data_query", "서버 목록")], [], _cfg()) == []


def test_named_inactive_db_system_is_notice_only(systems) -> None:
    """운영 모양 — 자산관리가 활성 목록에 없으면 폴스타로 대신 답하지 않는다."""
    systems()
    tasks = [_task("t1", "data_query", "ITAM에서 자산 담당자 목록 조회")]
    ca.apply_explicit_sources(tasks, ["ITAM"], _cfg(["polestar"]))
    assert tasks[0]["agent"] == "general_inference" and "「ITAM」" in tasks[0]["direct_response"]


# ── 4. SQL 처리기 가드(1단 · 재계획 후속) ──────────────────────────────────────


def _isolated(hints: list[str]) -> dict[str, Any]:
    return {"parsed_requirements": {"target_db_hints": hints}, "allowed_db_ids": None}


@pytest.mark.asyncio
async def test_sql_handler_does_not_answer_named_inactive_source(systems) -> None:
    """실측 A-1 모양 — SQL 처리기로 온 「제니퍼」 질의를 분류 LLM 없이 안내로 끝낸다."""
    systems()
    boom = AsyncMock(side_effect=AssertionError("분류·SQL 생성 LLM을 부르면 안 된다"))
    llm = SimpleNamespace(ainvoke=boom)
    res = await subagents.run_data_query_pipeline(
        {"task_id": "t1", "agent": "data_query",
         "sub_query": "SELECT * FROM instances WHERE owner = '제니퍼'"},
        _isolated(["제니퍼"]), llm=llm, app_config=_cfg())
    assert res["degraded_reason"] == subagents.REASON_SOURCE_INACTIVE
    assert "「제니퍼」" in res["final_response"] and "error" not in res
    boom.assert_not_called()


def test_sql_handler_guard_variants(systems) -> None:
    systems(apm="apm_query")
    active = subagents._explicit_source_notice(
        "제니퍼 인스턴스 목록", _isolated(["제니퍼"]), _cfg())
    assert active is not None and active["degraded_reason"] == subagents.REASON_SOURCE_UNSUPPORTED
    assert "이 조회 경로에서 처리하지 않아" in active["final_response"]
    assert subagents._explicit_source_notice(
        "ITAM 자산 담당자", _isolated(["ITAM"]), _cfg()) is None, "활성 DB 지목은 종전 경로"
    assert subagents._explicit_source_notice(
        "선행 결과 서버들의 CPU", _isolated(["제니퍼"]), _cfg()) is None, \
        "이름이 없는 task는 대상 밖"


def test_mixed_turn_note_includes_inactive_non_db() -> None:
    notes = subagents._source_unavailable_notes(
        _isolated(["폴스타", "프로메테우스"]), ["polestar"], active_non_db=())
    assert [n["detail"] for n in notes] == [
        "요청하신 「프로메테우스」 데이터 소스는 현재 활성화되어 있지 않아 조회하지 않았습니다."]


# ── 5. 재계획 소스 불가 종결 ─────────────────────────────────────────────────


def _state(tasks: list[dict[str, Any]], results: dict[str, Any]) -> dict[str, Any]:
    state = create_initial_state(user_query="제니퍼의 인스턴스 리스트를 보여줘")
    state["task_plan"] = tasks
    state["task_results"] = results
    return state


@pytest.mark.asyncio
async def test_replanner_stops_after_apm_source_failure() -> None:
    """실측 B-1·B-2 재현 — 제니퍼 0건(`apm_not_queried`) 뒤 평가 LLM·대체 task 없음."""
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=AssertionError("평가 LLM 금지")))
    tasks = [_task("t1", "apm_query", "제니퍼 WAS 인스턴스 목록 조회", status="failed")]
    results = {"t1": {"error": "제니퍼을(를) 조회하지 않았습니다 — source_unavailable",
                      "degraded_reason": "apm_not_queried", "final_response": "…"}}
    out = await replanner(_state(tasks, results), llm=llm, app_config=SimpleNamespace(max_replan=3))
    assert out["needs_replan"] is False and "task_plan" not in out


@pytest.mark.asyncio
async def test_replanner_stops_when_rest_succeeded() -> None:
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=AssertionError("평가 LLM 금지")))
    tasks = [
        _task("t1", "general_inference", "프로메테우스 CPU", status="completed",
              direct_response="안내", **{ca.SOURCE_NOTICE_KEY: ["prometheus"]}),
        _task("t2", "data_query", "폴스타 심각 알람 서버", status="completed"),
    ]
    results = {"t1": {"final_response": "안내"},
               "t2": {"query_results": [{"hostname": "web01"}],
                      "organized_data": {"rows": [{"hostname": "web01"}], "is_sufficient": True}}}
    out = await replanner(_state(tasks, results), llm=llm, app_config=SimpleNamespace(max_replan=3))
    assert out["needs_replan"] is False


def test_followups_replacing_terminal_tasks_are_dropped() -> None:
    new = [{"task_id": "t3", "agent": "data_query", "sub_query": "대체", "supersedes": ["t1"]},
           {"task_id": "t4", "agent": "data_query", "sub_query": "의존", "input_from": ["t1"]},
           {"task_id": "t5", "agent": "alarm_query", "sub_query": "별개", "supersedes": []}]
    assert [t["task_id"] for t in _drop_terminal_substitutes(new, {"t1"})] == ["t5"]


# ── 6. APM 기본 보기 ──────────────────────────────────────────────────────────


def test_default_view_is_instance_list_without_targets() -> None:
    no_target = {"parsed_requirements": {"filter_conditions": []}}
    assert aq.default_views(no_target, 10) == [aq.INSTANCES_VIEW]
    named = {"parsed_requirements": {"filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}]}}
    assert aq.default_views(named, 10) == [aq.DEFAULT_VIEW]
    # 직전 턴 대상만 있으면 목록(대상 없는 목록 질문이 직전 서버로 좁혀지지 않는다)
    previous = {**no_target, "conversation_context": {
        "previous_entities": [{"field": "hostname", "value": "web09"}]}}
    assert aq.default_views(previous, 10) == [aq.INSTANCES_VIEW]
