"""보류 사유의 출처 칸 (plans/122 J-3) — `Verdict.manual_sources`.

고정하는 계약:
  1. 평가기의 보류 지점마다 출처가 `MANUAL_SOURCES` 어휘로 붙는다(지점별 쌍).
  2. 출처는 첫 등장 순서 · 중복 없음. 사유 문구(`manual_notes`)는 종전과 바이트 동일하다.
  3. 러너 환경 불일치 보류(`runner._hold_for_env`)는 `env_mismatch` 로 가려진다 — 원 문구가 있으면
     `catalog` 도 함께 남는다. 러너 문구가 바뀌면 여기서 깨진다(`ENV_MISMATCH_NOTE_PREFIX`).
  4. 무효(T-c) 턴은 출처도 비운다.
전부 무과금 순수 함수다(LLM·DB·서버 0).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import REPO_ROOT
from scripts.scenario.assertions import (
    ENV_MISMATCH_NOTE_PREFIX,
    INVALID_VERDICT,
    MANUAL_SOURCES,
    Observation,
    Verdict,
    _Holds,
    evaluate_turn,
    primary_manual_source,
)
from scripts.scenario.catalog import Group, Scenario, Turn
from scripts.scenario.runner import _hold_for_env

GROUP = Group(id="T", name="t", latency_target_ms=1)


def _scenario(expect: dict[str, Any], **kwargs: Any) -> Scenario:
    base: dict[str, Any] = dict(
        id="T-01", group="T", plans=[122], title="t",
        turns=[Turn(send={"query": "q"}, expect=expect)],
    )
    base.update(kwargs)
    return Scenario(**base)


def _judge(expect: dict[str, Any], obs: Observation, *, run_mock: bool = False,
           **scenario: Any) -> Verdict:
    """`run_mock` = 모의 실행 판정.

    나머지 인자는 `Scenario` 칸(`mock` 블록 · `response_modes` 등)이다.
    """
    sc = _scenario(expect, **scenario)
    group = Group(id="T", name="t", latency_target_ms=30000)
    return evaluate_turn(sc, 1, sc.turns[0], obs, group, mock=run_mock)


def _done(**kwargs: Any) -> Observation:
    base: dict[str, Any] = dict(status="completed", http_status=200, response="결과")
    base.update(kwargs)
    return Observation(**base)


# --- 1. 보류 지점별 출처 --------------------------------------------------------

CASES: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any], str]] = [
    # (이름, expect, Observation 인자, 판정 인자, 기대 출처)
    ("모의 실행 보류", {"status": "completed"}, {}, {"run_mock": True}, "unobservable"),
    ("카탈로그 manual_review", {"manual_review": "눈으로 볼 것"}, {}, {}, "catalog"),
    ("intent 미관측", {"intent": "data_query"}, {}, {}, "unobservable"),
    ("row_count 팬아웃", {"row_count": {"min": 1}},
     {"row_count": 6, "row_counts_by_db": {"a": 3, "b": 3}}, {}, "fanout"),
    ("row_count_per_db 미관측", {"row_count_per_db": {"min": 1}}, {}, {}, "unobservable"),
    ("sql_must_match 역질문", {"sql_must_match": ["x"]}, {"status": "clarification"}, {},
     "unobservable"),
    ("sql_must_match 모의(mock 블록)", {"sql_must_match": ["x"]}, {},
     {"run_mock": True, "mock": {"turns": [{"response": "r"}]}}, "unobservable"),
    ("sql_must_not_match 행만", {"sql_must_not_match": ["x"]}, {"row_count": 3}, {},
     "unobservable"),
    ("period_covers SQL 없음", {"period_covers": {"from": "2026-07-01", "to": "2026-08-01"}},
     {}, {}, "unobservable"),
    ("file docx 는 칼럼 검증 밖", {"file": {"columns": ["a"]}},
     {"artifacts": ["/nope/out.docx"]}, {}, "unobservable"),
    ("rewrite 레코드 없음", {"rewrite": {"gate": "pass_through"}}, {}, {}, "unobservable"),
    ("rewrite 검증 결과 없음", {"rewrite": {"slots_preserved": True}},
     {"rewrite_traces": [{"gate": {"needed": False}, "verify": {}}]}, {}, "unobservable"),
    ("plan 요약 없음", {"plan": {"min_tasks": 1}}, {}, {}, "unobservable"),
    ("plan 사전 게이트", {"plan": {"min_tasks": 1}},
     {"plan_summary": {"plan_path": "pre_gate", "task_count": 0}}, {}, "unobservable"),
    ("plan 재계획 수 없음", {"plan": {"replan_max": 0}},
     {"plan_summary": {"plan_path": "llm_decompose", "task_count": 1,
                       "tasks": [{"id": "t1", "agent": "data_query"}]}}, {}, "unobservable"),
    ("llm_calls 미관측", {"llm_calls": {"max": 3}}, {}, {}, "unobservable"),
    ("retries 미관측", {"retries": {"max": 3}}, {}, {}, "unobservable"),
    ("retries 하한", {"retries": {"max": 3}}, {"retries": 1, "retries_partial": True}, {},
     "unobservable"),
    ("gold_sql", {"gold_sql": "SELECT 1"}, {}, {}, "unobservable"),
    ("등급 정책 미확정", {"status": "completed"}, {}, {"response_modes": ["refuse"]}, "policy"),
]


@pytest.mark.parametrize("name, expect, obs_kwargs, judge_kwargs, source", CASES,
                         ids=[case[0] for case in CASES])
def test_each_hold_point_carries_its_source(
    name: str, expect: dict[str, Any], obs_kwargs: dict[str, Any],
    judge_kwargs: dict[str, Any], source: str,
) -> None:
    """보류 지점 1곳 = 사유 1건 = 출처 1개."""
    verdict = _judge(expect, _done(**obs_kwargs), **judge_kwargs)
    assert verdict.func == "manual", (verdict.func, verdict.failures)
    assert len(verdict.manual_notes) == 1, verdict.manual_notes
    assert verdict.manual_sources == [source]


def test_broken_xlsx_is_unobservable(tmp_path: Path) -> None:
    """산출물을 열지 못한 것은 불합격이 아니라 관측 불가 보류다."""
    broken = tmp_path / "out.xlsx"
    broken.write_bytes(b"not a zip")
    verdict = _judge({"file": {"columns": ["a"]}}, _done(artifacts=[str(broken)], has_file=True))
    assert verdict.manual_sources == ["unobservable"]
    assert verdict.manual_notes[0].startswith("xlsx 열기 실패")


# --- 2. 순서 · 중복 · 비움 -----------------------------------------------------

def test_sources_keep_first_appearance_order_without_duplicates() -> None:
    """출처는 사유가 쌓인 순서대로, 같은 출처는 한 번만."""
    verdict = _judge(
        {"manual_review": "m", "row_count": {"min": 1}, "llm_calls": {"max": 1},
         "retries": {"max": 1}},
        _done(row_count=6, row_counts_by_db={"a": 3, "b": 3}),
        response_modes=["refuse"],
    )
    assert verdict.manual_sources == ["catalog", "fanout", "unobservable", "policy"]
    assert len(verdict.manual_notes) == 5          # 사유는 5건, 출처는 4종(unobservable 2건 → 1)
    assert primary_manual_source(verdict.manual_sources) == "policy"


def test_no_hold_means_no_source() -> None:
    """보류가 없으면 출처도 없다."""
    verdict = _judge({"status": "completed"}, _done())
    assert verdict.func == "pass"
    assert verdict.manual_sources == [] and verdict.manual_notes == []


def test_invalid_turn_clears_sources() -> None:
    """무효(러너 인증 실패) 턴은 사유와 함께 출처도 비운다."""
    verdict = _judge({"manual_review": "m"}, _done(http_status=401, status="error",
                                                   error="http 401 unauthorized"))
    assert verdict.func == INVALID_VERDICT
    assert verdict.manual_sources == [] and verdict.manual_notes == []


def test_unknown_source_is_rejected() -> None:
    """출처 어휘 밖 값은 쌓이지 않는다 — 리포트 분류가 조용히 새지 않게."""
    holds = _Holds()
    with pytest.raises(ValueError):
        holds.add("사유", "unknown_source")
    with pytest.raises(ValueError):
        holds.add("사유")
    assert holds.notes == [] and not holds


# --- 3. 러너 환경 불일치 보류 --------------------------------------------------

def test_env_mismatch_hold_without_catalog_review() -> None:
    """원 문구가 없는 턴의 환경 불일치 보류는 `env_mismatch` 하나다."""
    scenario = _scenario({"status": "completed", "row_count": {"min": 1}}, env="sandbox")
    held = _hold_for_env(scenario.turns[0], scenario, "closed")
    assert held.expect["manual_review"].startswith(ENV_MISMATCH_NOTE_PREFIX)
    verdict = evaluate_turn(scenario, 1, held, _done(), GROUP)
    assert verdict.func == "manual"
    assert verdict.manual_sources == ["env_mismatch"]
    assert primary_manual_source(verdict.manual_sources) == "env_mismatch"


def test_env_mismatch_hold_with_catalog_review_keeps_both() -> None:
    """원 문구가 있으면 러너가 `원문 / 환경 불일치 …` 로 잇는다 — 두 출처가 다 남는다."""
    scenario = _scenario({"manual_review": "원문 확인", "status": "completed"}, env="sandbox")
    held = _hold_for_env(scenario.turns[0], scenario, "closed")
    verdict = evaluate_turn(scenario, 1, held, _done(), GROUP)
    assert verdict.manual_notes == [held.expect["manual_review"]]      # 문구는 한 건 그대로
    assert verdict.manual_sources == ["catalog", "env_mismatch"]
    assert primary_manual_source(verdict.manual_sources) == "env_mismatch"


# --- 4. 지점 누락 방지 ---------------------------------------------------------

def test_every_hold_point_goes_through_holds() -> None:
    """평가기에 출처 없는 보류(`manual.append`)가 다시 생기지 않는다.

    `manual.add` 는 출처를 강제한다.
    """
    source = REPO_ROOT / "scripts" / "scenario" / "assertions.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    appends = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append" and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "manual"
    ]
    assert not appends, f"출처 없는 보류 지점: 줄 {appends}"
    adds = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add" and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "manual"
    ]
    literal_sources = {
        arg.value for node in adds for arg in node.args[1:] if isinstance(arg, ast.Constant)
    }
    assert literal_sources <= set(MANUAL_SOURCES), literal_sources
    assert len(adds) >= 20
