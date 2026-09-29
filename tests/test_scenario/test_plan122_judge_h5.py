"""이미 관측되는 값에 붙는 단언 (plans/122 H-5).

`sql_executed` · `node_path_must_not` · `status_any` · `form_memory_panel` · `stream` ·
`dependency_notes_contains` — 키마다 합격·불합격 쌍과 **관측하지 못하면 보류**(불합격 아님)를
고정한다.
노트 종류 어휘(`catalog.DEPENDENCY_NOTE_KINDS`)는 제품 `NOTE_*` 와 같아야 한다.
전부 무과금 순수 함수다.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.scenario.assertions import Observation, Verdict, evaluate_turn
from scripts.scenario.catalog import DEPENDENCY_NOTE_KINDS, Group, Scenario, Turn


def _judge(expect: dict[str, Any], obs: Observation, *, run_mock: bool = False,
           **scenario: Any) -> Verdict:
    sc = Scenario(id="T-01", group="T", plans=[122], title="t",
                  turns=[Turn(send={"query": "q"}, expect=expect)], **scenario)
    return evaluate_turn(sc, 1, sc.turns[0], obs, Group(id="T", name="t", latency_target_ms=1),
                         mock=run_mock)


def _obs(**kwargs: Any) -> Observation:
    base: dict[str, Any] = dict(status="completed", http_status=200, response="결과")
    base.update(kwargs)
    return Observation(**base)


def _summary(path: str = "llm_decompose") -> dict[str, Any]:
    return {"plan_path": path, "task_count": 1, "tasks": [{"id": "t1", "agent": "data_query"}],
            "replan_count": 0}


# --- sql_executed -----------------------------------------------------------------

@pytest.mark.parametrize("wanted, obs_kwargs, func", [
    (True, {"executed_sqls": ["SELECT 1"]}, "pass"),
    (False, {}, "pass"),                                     # 실 모드 완료 · SQL 0건 = 관측
    (True, {}, "fail"),
    (False, {"executed_sql": "SELECT 1"}, "fail"),
    (False, {"status": "clarification"}, "manual"),          # 역질문 - 조회 전
    (True, {"clarification": {"kind": "zone_select"}}, "manual"),
    (False, {"row_count": 5}, "manual"),                     # 행은 있는데 SQL 못 봄
])
def test_sql_executed(wanted: bool, obs_kwargs: dict[str, Any], func: str) -> None:
    verdict = _judge({"sql_executed": wanted}, _obs(**obs_kwargs))
    assert verdict.func == func, (verdict.failures, verdict.manual_notes)
    if func == "manual":
        assert verdict.manual_sources == ["unobservable"]
    if func == "fail":
        assert [f.key for f in verdict.failures] == ["sql_executed"]


def test_sql_executed_mock_without_sql_is_held() -> None:
    """모의 실행(mock 블록 있음)은 SQL 수집기가 없다 - SQL 0건은 보류다."""
    verdict = _judge({"sql_executed": False}, _obs(), run_mock=True,
                     mock={"turns": [{"response": "r"}]})
    assert verdict.func == "manual"
    assert "모의 실행" in verdict.manual_notes[0]


def test_sql_executed_mock_without_block_is_skipped() -> None:
    """mock 블록 없는 모의 실행은 내용 단언을 적용하지 않는다(종전 규칙)."""
    verdict = _judge({"sql_executed": True}, _obs(), run_mock=True)
    assert verdict.func == "manual" and "sql_executed" in verdict.manual_notes[0]


# --- node_path_must_not · status_any · form_memory_panel --------------------------

def test_node_path_must_not() -> None:
    expect = {"node_path_must_not": ["query_generator"]}
    assert _judge(expect, _obs(node_path=["input_parser", "synonym_registrar"])).func == "pass"
    verdict = _judge(expect, _obs(node_path=["input_parser", "query_generator"]))
    assert verdict.func == "fail"
    assert verdict.failures[0].key == "node_path_must_not"
    held = _judge(expect, _obs(node_path=[]))
    assert held.func == "manual" and held.manual_sources == ["unobservable"]


def test_status_any() -> None:
    expect = {"status_any": ["clarification", "completed"]}
    assert _judge(expect, _obs(status="clarification")).func == "pass"
    assert _judge(expect, _obs(status="completed")).func == "pass"
    verdict = _judge(expect, _obs(status="error"))
    assert verdict.func == "fail"
    assert verdict.failures[0].actual == "error"


@pytest.mark.parametrize("wanted, panel, func", [
    ("present", {"fields": [{"name": "담당자"}]}, "pass"),
    ("present", None, "fail"),
    ("present", {}, "fail"),
    ("absent", None, "pass"),
    ("absent", {"fields": []}, "fail"),
])
def test_form_memory_panel(wanted: str, panel: Any, func: str) -> None:
    assert _judge({"form_memory_panel": wanted}, _obs(form_memory_panel=panel)).func == func


# --- stream ----------------------------------------------------------------------

def test_stream_bounds() -> None:
    expect = {"stream": {"ttft_ms": {"max": 8000}, "max_event_gap_ms": {"max": 15000}}}
    assert _judge(expect, _obs(ttft_ms=1200.0, max_event_gap_ms=9000.0)).func == "pass"
    verdict = _judge(expect, _obs(ttft_ms=9000.0, max_event_gap_ms=16000.0))
    assert verdict.func == "fail"
    assert [f.key for f in verdict.failures] == [
        "stream.max_event_gap_ms.max", "stream.ttft_ms.max",
    ]


def test_stream_missing_value_is_held() -> None:
    """토큰 없이 done 만 온 턴은 ttft_ms 가 None - 0 으로 보지 않고 보류한다."""
    verdict = _judge({"stream": {"ttft_ms": {"max": 8000}}}, _obs(ttft_ms=None))
    assert verdict.func == "manual"
    assert verdict.manual_sources == ["unobservable"]
    assert "stream.ttft_ms.max=8000" in verdict.manual_notes[0]


# --- dependency_notes_contains ----------------------------------------------------

def test_dependency_notes_contains() -> None:
    expect = {"dependency_notes_contains": ["gate"]}
    notes = [{"kind": "gate", "task_id": "t2", "reason": "prior_empty"}, {"kind": "trace"}]
    assert _judge(expect, _obs(plan_summary=_summary(), dependency_notes=notes)).func == "pass"
    verdict = _judge(expect, _obs(plan_summary=_summary(), dependency_notes=[{"kind": "trace"}]))
    assert verdict.func == "fail"
    assert verdict.failures[0].expected == "gate" and verdict.failures[0].actual == ["trace"]
    # 계획 요약이 있고 노트가 없으면(서버가 키를 싣지 않음 = 노트 0건) 불합격이다.
    assert _judge(expect, _obs(plan_summary=_summary())).func == "fail"


@pytest.mark.parametrize("summary", [None, _summary("pre_gate")])
def test_dependency_notes_without_plan_are_held(summary: dict[str, Any] | None) -> None:
    """계획 요약이 없거나(1·3단 · 옛 서버 · 모의) 사전 게이트로 끝나면 보류다."""
    verdict = _judge({"dependency_notes_contains": ["gate"]}, _obs(plan_summary=summary))
    assert verdict.func == "manual"
    assert verdict.manual_sources == ["unobservable"]


def test_note_kind_vocabulary_matches_product() -> None:
    """하네스 사본이 제품 노트 종류(`NOTE_*`)보다 낡으면 새 종류를 단언할 수 없다."""
    from src.utils import prior_dependency

    product = {
        value for name, value in vars(prior_dependency).items()
        if name.startswith("NOTE_") and isinstance(value, str)
    }
    assert DEPENDENCY_NOTE_KINDS == product


def test_real_server_notes_field_is_judged() -> None:
    """서버가 스트림 done 에 싣는 노트 필드(`_dependency_notes_field`)의 모양을 판정기가 읽는다."""
    from src.api.routes.query import _dependency_notes_field
    from src.utils.prior_dependency import NOTE_GATE

    payload = _dependency_notes_field({"dependency_notes": [{"kind": NOTE_GATE, "task_id": "t2"}]})
    verdict = _judge({"dependency_notes_contains": ["gate"]},
                     _obs(plan_summary=_summary(), dependency_notes=payload["dependency_notes"]))
    assert verdict.func == "pass"
    assert _dependency_notes_field({}) == {}      # 노트가 없으면 키가 없다 → 러너는 빈 목록


# --- 선언하지 않은 턴은 바이트 불변 ------------------------------------------------

def test_undeclared_keys_leave_verdict_unchanged() -> None:
    """새 키가 없으면 관측값(노트·패널·지연)이 무엇이든 판정이 같다."""
    plain = _judge({"status": "completed"}, _obs())
    loaded = _judge({"status": "completed"}, _obs(
        plan_summary=_summary(), dependency_notes=[{"kind": "gate"}], ttft_ms=99999.0,
        form_memory_panel={"fields": []}, node_path=["query_generator"],
    ))
    assert (plain.func, plain.failures, plain.manual_notes) == (
        loaded.func, loaded.failures, loaded.manual_notes)
