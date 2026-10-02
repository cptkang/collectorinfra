"""계획 구조 단언 `plan` (plans/121 TP-0.3) — 복합군 `manual_review` 를 기계 판정으로 옮긴다.

고정하는 계약:
  1. 판정기는 2단 계획 요약(`done.plan_summary`)만 보고 결정적으로 판정한다(LLM 0).
  2. 요약이 없거나(1·3단 · 옛 서버 · 모의 실행) `plan_path=pre_gate` 면 **불합격이 아니라 보류**다.
  3. `plan` 이 없는 턴은 판정이 바이트 불변이다(요약이 있든 없든).
  4. 로더가 틀린 `plan` 선언을 실행 전에 거부한다. 담당 어휘는 2단 레지스트리와 같다.
  5. 모의 서버는 바꾸지 않는다 — `mock:` 블록의 `plan_summary` 가 그대로 done 에 실린다.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario.assertions import Observation, Verdict, evaluate_turn
from scripts.scenario.catalog import (
    PLAN_AGENTS,
    Catalog,
    CatalogError,
    Group,
    Scenario,
    Turn,
    load_catalog,
)
from tests.test_scenario.conftest import GOOD_GROUP, write


def _scenario(expect: dict[str, Any], **kwargs: Any) -> Scenario:
    base: dict[str, Any] = dict(
        id="T-01", group="T", plans=[121], title="t",
        turns=[Turn(send={"query": "q"}, expect=expect)],
    )
    base.update(kwargs)
    return Scenario(**base)


def _group() -> Group:
    return Group(id="T", name="t", latency_target_ms=30000)


def _eval(expect: dict[str, Any], summary: dict[str, Any] | None, *, mock: bool = False,
          scenario_mock: dict[str, Any] | None = None) -> Verdict:
    scenario = _scenario(expect, mock=scenario_mock)
    obs = Observation(status="completed", plan_summary=summary, executed_sql="SELECT 1")
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, _group(), mock=mock)


def _task(tid: str, agent: str, *, input_from: list[str] | None = None,
          depends_on: list[str] | None = None, status: str = "completed") -> dict[str, Any]:
    return {
        "id": tid, "agent": agent, "status": status,
        "depends_on": list(depends_on if depends_on is not None else (input_from or [])),
        "input_from": list(input_from or []),
        "db_ids": [], "db_origin": None,
    }


def _summary(
    *tasks: dict[str, Any], path: str = "llm_decompose", replans: int = 0,
) -> dict[str, Any]:
    """서버 `_plan_summary_field` 와 같은 모양(코드·개수만)."""
    return {
        "plan_path": path, "task_count": len(tasks), "tasks": list(tasks),
        "replan_count": replans, "agent_fallback": 0, "note_kinds": {},
    }


#: 알람 선별 → 지표 조회(D-086 · 예시 3-1)의 정답 모양.
ALARM_THEN_METRIC = _summary(
    _task("t1", "alarm_query"), _task("t2", "data_query", input_from=["t1"]),
)


# --- 1. 결정적 판정 -------------------------------------------------------

def test_matching_structure_passes() -> None:
    """기대 구조와 같으면 합격이다."""
    verdict = _eval(
        {"plan": {"min_tasks": 2, "agents": ["alarm_query", "data_query"],
                  "edges": [["alarm_query", "data_query"]]}},
        ALARM_THEN_METRIC,
    )
    assert verdict.func == "pass"
    assert verdict.failures == [] and verdict.manual_notes == []


def test_undecomposed_plan_fails_min_tasks() -> None:
    """존 재진입이 분해를 건너뛴 단일 task(F-3 · N-1)가 여기서 잡힌다."""
    summary = _summary(_task("t1", "data_query"), path="zone_reentry")
    verdict = _eval({"plan": {"min_tasks": 2, "edges": [["alarm_query", "data_query"]]}}, summary)
    assert verdict.func == "fail"
    keys = [f.key for f in verdict.failures]
    assert keys == ["plan.min_tasks", "plan.edges"]
    assert verdict.failures[0].actual == {"task_count": 1, "agents": ["data_query"]}


def test_max_tasks_exceeded_fails() -> None:
    """max_tasks 초과는 불합격이다."""
    summary = _summary(_task("t1", "data_query"), _task("t2", "data_query"),
                       _task("t3", "data_query"))
    verdict = _eval({"plan": {"max_tasks": 2}}, summary)
    assert [f.key for f in verdict.failures] == ["plan.max_tasks"]


def test_agents_is_multiset_and_order_free() -> None:
    """agents 는 개수까지 세고 순서는 보지 않는다."""
    two = _summary(_task("t1", "data_query"), _task("t2", "cache_management"))
    assert _eval({"plan": {"agents": ["cache_management", "data_query"]}}, two).func == "pass"

    verdict = _eval({"plan": {"agents": ["data_query", "data_query"]}}, two)
    assert verdict.func == "fail"
    assert verdict.failures[0].key == "plan.agents"
    assert verdict.failures[0].actual == ["data_query", "cache_management"]


def test_edges_are_input_from_not_depends_on() -> None:
    """예시 2(캐시 → 조회)처럼 순서만 있는 계획은 데이터 간선이 아니다."""
    order_only = _summary(
        _task("t1", "alarm_query"), _task("t2", "data_query", depends_on=["t1"]),
    )
    verdict = _eval({"plan": {"edges": [["alarm_query", "data_query"]]}}, order_only)
    assert verdict.func == "fail"
    assert verdict.failures[0].key == "plan.edges"
    assert verdict.failures[0].actual == []


def test_reversed_edge_fails() -> None:
    """간선 방향이 반대면 불합격이다."""
    reversed_plan = _summary(
        _task("t1", "data_query"), _task("t2", "alarm_query", input_from=["t1"]),
    )
    verdict = _eval({"plan": {"edges": [["alarm_query", "data_query"]]}}, reversed_plan)
    assert verdict.failures[0].actual == [["data_query", "alarm_query"]]


def test_star_matches_any_agent() -> None:
    """R1-05 — OS 파라미터 task 는 알람·프로세스 중 어느 결과를 받아도 된다."""
    chain = _summary(
        _task("t1", "alarm_query"),
        _task("t2", "process_query", input_from=["t1"]),
        _task("t3", "data_query", input_from=["t2"]),
    )
    spec = {"edges": [["alarm_query", "process_query"], ["*", "data_query"]]}
    assert _eval({"plan": spec}, chain).func == "pass"

    unscoped = _summary(
        _task("t1", "alarm_query"),
        _task("t2", "process_query", input_from=["t1"]),
        _task("t3", "data_query"),
    )
    verdict = _eval({"plan": spec}, unscoped)
    assert [f.expected for f in verdict.failures] == [["*", "data_query"]]


def test_dangling_input_from_is_not_an_edge() -> None:
    """계획에 없는 task 를 가리키는 input_from 은 간선이 아니다."""
    dangling = _summary(_task("t2", "data_query", input_from=["t9"]))
    verdict = _eval({"plan": {"edges": [["*", "data_query"]]}}, dangling)
    assert verdict.func == "fail"


def test_plan_path_matches_allowed_codes() -> None:
    """plan_path 는 허용 코드(하나 또는 목록)와 대조한다."""
    restored = _summary(_task("t1", "alarm_query"),
                        _task("t2", "data_query", input_from=["t1"]),
                        path="zone_reentry_restored")
    allowed = {"plan": {"plan_path": ["llm_decompose", "zone_reentry_restored"]}}
    assert _eval(allowed, restored).func == "pass"
    assert _eval({"plan": {"plan_path": "zone_reentry_restored"}}, restored).func == "pass"

    verdict = _eval({"plan": {"plan_path": "llm_decompose"}}, restored)
    assert verdict.failures[0].key == "plan.plan_path"
    assert verdict.failures[0].actual == "zone_reentry_restored"


def test_replan_max_caps_replan_count() -> None:
    """replan_max 는 재계획 횟수 상한이다."""
    replanned = _summary(_task("t1", "data_query"), replans=2)
    assert _eval({"plan": {"replan_max": 2}}, replanned).func == "pass"
    verdict = _eval({"plan": {"replan_max": 1}}, replanned)
    assert [f.key for f in verdict.failures] == ["plan.replan_max"]


def test_replanned_tasks_count_toward_lower_bounds() -> None:
    """요약은 이번 턴의 최종 계획이다 — 첫 분해만 보려면 replan_max: 0 을 함께 쓴다."""
    late = _summary(_task("t1", "data_query"), _task("t2", "data_query"), replans=1)
    assert _eval({"plan": {"min_tasks": 2}}, late).func == "pass"
    assert _eval({"plan": {"min_tasks": 2, "replan_max": 0}}, late).func == "fail"


def test_real_server_summary_shape_is_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """서버가 실제로 만드는 요약(`_plan_summary_field`·`_pre_gate_plan_summary`)을 판정기가 읽는다.

    키 이름(`id`·`agent`·`input_from`·`replan_count`)이 바뀌면 간선 단언이 조용히 전건 불합격이
    된다 — 사본 픽스처가 아니라 서버 함수의 출력으로 고정한다.
    """
    import src.observability.ladder as ladder
    from src.api.routes.query import _plan_summary_field, _pre_gate_plan_summary
    from src.state import create_initial_state

    state = dict(create_initial_state("q"))
    state.update({
        "plan_path": "llm_decompose",
        "task_plan": [
            {"task_id": "t2", "agent": "data_query", "order": 2, "status": "completed",
             "depends_on": ["t1"], "input_from": ["t1"]},
            {"task_id": "t1", "agent": "alarm_query", "order": 1, "status": "completed",
             "depends_on": [], "input_from": []},
        ],
        "task_results": {},
        "replan_count": 0,
    })
    spec = {"min_tasks": 2, "max_tasks": 2, "agents": ["alarm_query", "data_query"],
            "edges": [["alarm_query", "data_query"]], "plan_path": "llm_decompose",
            "replan_max": 0}
    summary = _plan_summary_field(state)["plan_summary"]
    assert _eval({"plan": spec}, summary).func == "pass"

    monkeypatch.setattr(ladder, "current_ladder", lambda: {"tier": "intent_orchestration"})
    pre_gate = _pre_gate_plan_summary()["plan_summary"]
    assert _eval({"plan": spec}, pre_gate).func == "manual"


# --- 2. 관측하지 못하면 보류 ----------------------------------------------

@pytest.mark.parametrize("summary", [None, {"plan_path": "pre_gate", "task_count": 0}])
def test_missing_summary_or_pre_gate_is_held(summary: dict[str, Any] | None) -> None:
    """요약이 없거나 사전 게이트로 끝난 턴은 불합격이 아니라 보류다."""
    spec = {"min_tasks": 2, "edges": [["alarm_query", "data_query"]]}
    verdict = _eval({"status": "completed", "plan": spec}, summary)
    assert verdict.func == "manual"
    assert verdict.failures == []
    (note,) = verdict.manual_notes
    assert "확인하지 못했다" in note
    # 1·3단 arm 에서 사람이 대신 볼 기대 구조가 사유에 실린다.
    assert '"edges": [["alarm_query", "data_query"]]' in note
    assert '"min_tasks": 2' in note


def test_mock_run_without_mock_block_holds_plan() -> None:
    """모의 실행은 mock 블록이 없으면 plan 을 적용하지 않는다(canned 응답 — 배관만 증명)."""
    verdict = _eval({"plan": {"min_tasks": 2}}, None, mock=True)
    assert verdict.func == "manual"
    assert "plan" in verdict.manual_notes[0] and "모의 실행" in verdict.manual_notes[0]


def test_mock_run_with_summary_in_mock_block_is_judged() -> None:
    """모의 실행도 mock 블록이 요약을 주면 판정한다."""
    verdict = _eval({"plan": {"min_tasks": 2}}, ALARM_THEN_METRIC, mock=True,
                    scenario_mock={"turns": [{"plan_summary": ALARM_THEN_METRIC}]})
    assert verdict.func == "pass"


def test_mock_server_passes_plan_summary_through() -> None:
    """모의 서버 무변경 근거 — `_payload_for` 가 `mock:` 턴 정의를 done 본문에 덮어쓴다."""
    from scripts.scenario.client import _apply_done
    from scripts.scenario.mockserver import _payload_for

    scenario = _scenario({}, mock={"turns": [{"plan_summary": ALARM_THEN_METRIC}]})
    obs = Observation()
    _apply_done(obs, _payload_for(scenario, 1, "q"))
    assert obs.plan_summary == ALARM_THEN_METRIC

    canned = Observation()
    _apply_done(canned, _payload_for(_scenario({}), 1, "q"))
    assert canned.plan_summary is None  # canned 응답은 요약이 없다 → 판정기는 보류


# --- 3. plan 없는 턴은 바이트 불변 ----------------------------------------

def test_turns_without_plan_are_byte_identical() -> None:
    """정본 카탈로그의 `plan` 없는 턴 전부 — 요약이 실려도 판정·사유가 한 글자도 안 바뀐다."""
    catalog = load_catalog()
    compared = 0
    for scenario in catalog.scenarios:
        group = catalog.groups[scenario.group]
        for index, turn in enumerate(scenario.turns, start=1):
            if "plan" in turn.expect:
                continue
            verdicts = [
                asdict(evaluate_turn(
                    scenario, index, turn,
                    Observation(status="completed", row_count=3, executed_sql="SELECT 1",
                                response="결과", plan_summary=summary),
                    group,
                ))
                for summary in (None, ALARM_THEN_METRIC)
            ]
            assert verdicts[0] == verdicts[1], (scenario.id, index)
            compared += 1
    assert compared >= 200  # 정본 카탈로그 259턴 중 plan 없는 252턴(2026-09-29)


# --- 4. 로더 검증 ---------------------------------------------------------

def _load_with(scenario_dir: Path, profiles_path: Path, expect: str) -> Catalog:
    write(scenario_dir / "t.yaml", GOOD_GROUP.replace(
        "        expect: {status: completed}", f"        expect: {expect}"))
    return load_catalog(scenario_dir=scenario_dir, profiles_path=profiles_path)


def test_valid_plan_loads(scenario_dir: Path, profiles_path: Path) -> None:
    """하위 키 여섯을 모두 쓴 올바른 plan 은 로드된다."""
    catalog = _load_with(
        scenario_dir, profiles_path,
        '{plan: {min_tasks: 2, max_tasks: 3, agents: [alarm_query, data_query], '
        'edges: [[alarm_query, data_query], ["*", data_query]], '
        'plan_path: [llm_decompose], replan_max: 0}}',
    )
    assert catalog.scenarios[0].turns[0].expect["plan"]["min_tasks"] == 2


@pytest.mark.parametrize("expect, needle", [
    ("{plan: {min_task: 2}}", "정의 밖 키"),
    ("{plan: {}}", "비지 않은 매핑"),
    ("{plan: [data_query]}", "비지 않은 매핑"),
    ("{plan: {min_tasks: 0}}", "min_tasks"),
    ("{plan: {min_tasks: true}}", "min_tasks"),
    ("{plan: {replan_max: -1}}", "replan_max"),
    ("{plan: {agents: [alarm_querry]}}", "agents"),
    ("{plan: {agents: ['*']}}", "agents"),
    ("{plan: {edges: [[alarm_query]]}}", "edges"),
    ("{plan: {edges: [alarm_query, data_query]}}", "edges"),
    ("{plan: {edges: [[alarm_query, [data_query]]]}}", "edges"),
    ("{plan: {plan_path: []}}", "plan_path"),
    ("{plan: {min_tasks: 3, max_tasks: 2}}", "min_tasks(3)"),
    ("{plan: {agents: [data_query, data_query, alarm_query], max_tasks: 2}}", "성립할 수 없다"),
])
def test_invalid_plan_rejected_at_load(
    scenario_dir: Path, profiles_path: Path, expect: str, needle: str,
) -> None:
    """틀린 plan 은 실행 전(로드 시점)에 사유와 함께 거부된다."""
    with pytest.raises(CatalogError) as exc:
        _load_with(scenario_dir, profiles_path, expect)
    assert any("plan" in error and needle in error for error in exc.value.errors), exc.value.errors


def test_agent_vocabulary_matches_tier2_registry() -> None:
    """하네스 사본(catalog.PLAN_AGENTS)이 레지스트리보다 낡으면 새 담당을 단언할 수 없다."""
    from src.orchestration.apm_query import APM_QUERY_AGENT
    from src.orchestration.subagents import SUBAGENT_REGISTRY

    # 조건부 처리기(plans/125 A-3 — 소스가 활성일 때만 등록)도 단언 어휘다(4소스 골드 FS군).
    assert PLAN_AGENTS == frozenset(SUBAGENT_REGISTRY) | {APM_QUERY_AGENT}


# --- 5. 정본 카탈로그 ------------------------------------------------------

def _composite_turns() -> list[tuple[Scenario, Turn]]:
    catalog = load_catalog()
    return [(s, t) for s in catalog.scenarios if s.group in ("E", "R1") for t in s.turns]


def test_converted_turns_drop_manual_review() -> None:
    """`manual_review` 가 남으면 구조 단언이 통과해도 합격이 아니라 manual 이다(§3.4)."""
    planned = [(s.id, t) for s, t in _composite_turns() if "plan" in t.expect]
    # R1-03 의 `plan: {min_tasks: 2}` 는 plans/123 CT-3(123·G-7 (a) · D-280 ⑦)이 A-12형 기대와 같은
    # 변경으로 뺐다 - 원문이 앞단 존 게이트를 발동해 답 턴이 단일 task 로 확정되는 경로라 구조적으로
    # 불합격이었다(123 §2.6). 키 소유는 그대로 `plan` = 121 이다.
    assert {sid for sid, _ in planned} == {
        "E-01", "E-02", "E-03", "R1-01", "R1-05", "R1-08",
    }
    assert all("manual_review" not in turn.expect for _, turn in planned)


def test_composite_machine_judged_ratio_floor() -> None:
    """TP-0.3 착수 전 2/18 → 10/18. 다시 수동 검토로 되돌아가는 것을 막는다."""
    turns = _composite_turns()
    judged = [t for _, t in turns
              if not t.expect.get("manual_review") and set(t.expect) - {"manual_review"}]
    assert len(judged) / len(turns) >= 0.55, f"{len(judged)}/{len(turns)}"
