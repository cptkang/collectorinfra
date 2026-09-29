"""plans/119 재계획기 — N-2(CU-4) · Q-3 · T-2 · T-6.

  N-2 (D-251 ⑤ · 플래그 없음) — 전 task 성공 · 조회 행 ≥1 · 미완 표지 없음이면 LLM 평가 없이 종료.
      0건·부분 실패·순차 의존(D-203) 경로는 종전과 **같은 입력**으로 LLM 평가를 탄다.
  Q-3 (D-063 확장) — 내부 루프가 재생성을 멈춘(`regen_stop`) 조회를 같은 담당·같은 대상 DB·같은
      식별 리터럴로 다시 시키는 후속을 제거하고, 제거로 끝나면 마지막 사유를 응답에 싣는다.
  T-2 (D-267 ⑥ · 플래그 없음) — 재계획기의 "남은 시간"은 조회 마감(처리 마감 − 서술 예약) 기준이고,
      오케스트레이터는 조회 마감이 지난 재진입 바퀴에서 새 후속을 시작하지 않는다.
  T-6 (플래그 기본 off) — 켜면 평가 입력 말미에 예산 블록이 붙고, 끄면 입력이 바이트 동일하다.

LLM·DB 0 — LLM 은 모의 객체다.
"""

from __future__ import annotations

import importlib
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from src.orchestration.replanner import (
    _all_tasks_succeeded,
    _build_eval_context,
    _deadline_stop_notice,
    _filter_regen_stopped,
    replanner,
)
from src.orchestration.result_aggregator import _apply_incomplete_notice
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec
from src.prompts.replanner import REPLANNER_SYSTEM_TEMPLATE
from src.state import create_initial_state

# 패키지 __init__ 이 동명 함수를 재노출해 submodule 이름을 가린다 → importlib 로 실제 모듈
_ao = importlib.import_module("src.orchestration.agent_orchestrator")
_rp = importlib.import_module("src.orchestration.replanner")

_NO_FOLLOWUP = {"needs_followup": False, "reason": "충분", "new_tasks": []}


def _llm(payload: dict[str, Any]) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    return llm


def _task(tid: str = "t1", agent: str = "data_query", sub_query: str = "김포 서버 CPU 상위 10건",
          **over: Any) -> dict[str, Any]:
    task = {"task_id": tid, "agent": agent, "sub_query": sub_query, "depends_on": [],
            "input_from": [], "order": int(tid[1:]), "status": "completed"}
    task.update(over)
    return task


def _rows(n: int = 2) -> dict[str, Any]:
    return {"query_results": [{"hostname": f"web-0{i}", "cpu": 90 + i} for i in range(n)],
            "organized_data": {"rows": [{"hostname": f"web-0{i}"} for i in range(n)],
                               "is_sufficient": True},
            "source": [{"db_id": "polestar_cm_gp"}], "target_db_ids": ["polestar_cm_gp"]}


def _state(tasks: list[dict[str, Any]], results: dict[str, dict[str, Any]], *,
           user_query: str = "김포 서버 CPU 상위 10건", **over: Any) -> dict[str, Any]:
    state = create_initial_state(user_query=user_query)
    state["task_plan"] = tasks
    state["task_results"] = results
    state.update(over)
    return state


def _messages(llm: AsyncMock) -> list[Any]:
    return llm.ainvoke.await_args.args[0]


def _pin(cfg: Any, *, reserve: float = 0, budget_prompt: bool = False) -> Any:
    """`.env` 누수 없이 검증 대상 필드를 명시한다(CLAUDE.md Known Mistakes)."""
    cfg.server.answer_reserve_sec = reserve
    cfg.replan_budget_prompt_enabled = budget_prompt
    return cfg


# ── N-2 결정적 성공 종료 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_n2_success_path_makes_no_llm_call(mock_config) -> None:
    """성공 경로는 LLM 호출 0회로 끝난다."""
    llm = _llm({"needs_followup": True, "reason": "x",
                "new_tasks": [{"agent": "alarm_query", "sub_query": "알람"}]})
    state = _state([_task()], {"t1": _rows()})

    out = await replanner(state, llm=llm, app_config=_pin(mock_config))

    llm.ainvoke.assert_not_awaited()
    assert out == {"needs_replan": False, "replan_history": [], "current_node": "replanner"}


@pytest.mark.asyncio
async def test_n2_mixed_plan_with_rows_and_text_skips_llm(mock_config) -> None:
    """혼합 계획도 행이 있고 텍스트 task가 성공이면 LLM 없이 끝난다."""
    llm = _llm(_NO_FOLLOWUP)
    tasks = [_task(), _task("t2", agent="general_inference", sub_query="CPU 사용률 개념")]
    results = {"t1": _rows(), "t2": {"final_response": "CPU 사용률은 …"}}

    out = await replanner(_state(tasks, results), llm=llm, app_config=_pin(mock_config))

    llm.ainvoke.assert_not_awaited()
    assert out["needs_replan"] is False


@pytest.mark.asyncio
async def test_n2_informational_notes_are_not_incomplete(mock_config) -> None:
    """안내성 경과 노트는 미완 표지가 아니다."""
    llm = _llm(_NO_FOLLOWUP)
    res = {**_rows(), "dependency_notes": [{"kind": "ownership", "detail": "소유 교정"}]}
    await replanner(_state([_task()], {"t1": res}), llm=llm, app_config=_pin(mock_config))
    llm.ainvoke.assert_not_awaited()


def _case(name: str, *, task: dict[str, Any] | None = None, res: dict[str, Any] | None = None,
          extra: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
          **state_over: Any) -> Any:
    tasks = [task or _task()]
    results = {"t1": res if res is not None else _rows()}
    for t, r in extra or []:
        tasks.append(t)
        results[t["task_id"]] = r
    return pytest.param(tasks, results, state_over, id=name)


# 0건·부분 실패·순차 의존·미완 표지 — 전부 종전대로 LLM 평가(같은 입력)
_LLM_PATH_CASES = [
    _case("0건", res={**_rows(), "query_results": [], "organized_data": {"rows": []}}),
    _case("실패", res={"error": "SQL 검증 실패"}),
    _case("재생성_중단", res={"error": "검증 실패",
                            "regen_stop": {"reason": "non_sql", "detail": "x"}}),
    _case("멀티DB_일부_실패", res={**_rows(), "db_errors": {"polestar_cm_yd": "timeout"}}),
    _case("DB별_미조회", res={**_rows(), "skipped_dbs": ["polestar_cm_yd"]}),
    _case("상태_failed", task=_task(status="failed")),
    _case("게이트_미실행", res={"error": "선행 0건", "skipped": True,
                              "skip_reason": "prior_empty"}),
    _case("충족도_미달_결과", res={**_rows(), "organized_data": {"rows": [{"a": 1}],
                                                             "is_sufficient": False}}),
    _case("조회_담당인데_행_모양_없음", res={"final_response": "텍스트", "source": []}),
    _case("텍스트만_있는_계획", task=_task(agent="general_inference"),
          res={"final_response": "안내"}),
    _case("순차_의존_input_from",
          extra=[(_task("t2", sub_query="그 서버들의 메모리", input_from=["t1"], depends_on=["t1"]),
                  _rows())]),
    _case("순서_의존_depends_on", extra=[(_task("t2", depends_on=["t1"]), _rows())]),
    _case("순차_표지_원질의", user_query="CPU 높은 서버를 찾아 그 서버들의 메모리도 보여줘"),
    _case("충족도_미달_상태", sufficiency_shortfalls=[{"task_id": "t1"}]),
    _case("순차_경과_노트_게이트", dependency_notes=[{"kind": "gate", "detail": "선행 0건"}]),
    _case("1단_미실행_안내", orchestration_incomplete_notice="일부 작업 미실행"),
    _case("일부_권한_거부", extra=[(_task("t2"), {"final_response": "권한 없음",
                                                  "routing_intent": "access_denied",
                                                  "source": []})]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tasks", "results", "state_over"), _LLM_PATH_CASES)
async def test_n2_non_success_paths_keep_llm_with_same_input(
    mock_config, tasks, results, state_over,
) -> None:
    """비성공 경로는 종전과 같은 입력으로 LLM이 평가한다."""
    llm = _llm(_NO_FOLLOWUP)
    state = _state(tasks, results, **state_over)

    await replanner(state, llm=llm, app_config=_pin(mock_config))

    llm.ainvoke.assert_awaited_once()
    # 비트 동일 — 시스템 템플릿 + 종전 평가 컨텍스트 그대로(예산 블록 없음 · KBGenAI 아님)
    assert _messages(llm) == [
        SystemMessage(content=REPLANNER_SYSTEM_TEMPLATE),
        HumanMessage(content=_build_eval_context(
            state["user_query"], state["task_plan"], state["task_results"])),
    ]


def test_n2_access_denied_or_empty_plan_is_not_success() -> None:
    """판정은 권한 거부 결과를 성공으로 보지 않는다."""
    from src.orchestration.db_access import access_denied_result

    assert _all_tasks_succeeded(_state([_task()], {"t1": _rows()}))
    assert not _all_tasks_succeeded(_state([_task()], {"t1": access_denied_result()}))
    assert not _all_tasks_succeeded(_state([], {}))


# ── Q-3 재생성 위임 후속 제거 ────────────────────────────────────────────────


_STOP = {"reason": "validation_budget", "detail": "허용 목록 밖 테이블 참조: core_config_prop"}


def _failed(tid: str = "t1", sub_query: str = "김포 서버 CPU 상위 10건", stop: Any = _STOP,
            agent: str = "data_query") -> tuple[dict[str, Any], dict[str, Any]]:
    res: dict[str, Any] = {"error": "SQL 검증 실패", "source": [{"db_id": "polestar_cm_gp"}],
                           "target_db_ids": ["polestar_cm_gp"]}
    if stop is not None:
        res["regen_stop"] = stop
    return _task(tid, agent=agent, sub_query=sub_query, status="failed"), res


def _new(sub_query: str = "김포 서버 CPU 상위 10건 올바른 SELECT로 재조회", **over: Any) -> dict:
    task = {"task_id": "t2", "agent": "data_query", "sub_query": sub_query,
            "depends_on": [], "input_from": [], "supersedes": ["t1"], "status": "pending"}
    task.update(over)
    return task


def test_q3_blocks_same_family_followup_with_reason() -> None:
    """같은 담당 같은 DB 같은 계열 후속을 막고 사유를 싣는다."""
    prior, res = _failed()

    kept, notice = _filter_regen_stopped([_new()], [prior], {"t1": res})

    assert kept == []
    assert "「김포 서버 CPU 상위 10건」 조회는 SQL을 재시도 한도까지 다시 만들었지만" in notice
    assert "마지막 사유: 허용 목록 밖 테이블 참조: core_config_prop" in notice


@pytest.mark.parametrize(("reason", "phrase"), [
    ("non_sql", "SQL 대신 설명문이 생성됐습니다"),
    ("deadline", "응답 시간 상한이 가까워 SQL 재생성을 멈췄습니다"),
    ("unknown_reason", "SQL 재생성이 멈췄습니다"),
])
def test_q3_user_message_per_reason(reason, phrase) -> None:
    """사유별 사용자 문구."""
    prior, res = _failed(stop={"reason": reason, "detail": "  여러   줄\n설명  "})
    _, notice = _filter_regen_stopped([_new()], [prior], {"t1": res})
    assert phrase in notice and "마지막 사유: 여러 줄 설명" in notice


def test_q3_same_entity_literal_is_same_family() -> None:
    """식별 리터럴이 같으면 같은 계열이다."""
    prior, res = _failed(sub_query="sbhdbo53 서버 CPU 조회")
    kept, _ = _filter_regen_stopped([_new("SBHDBO53-01 CPU 재조회")], [prior], {"t1": res})
    assert kept == []


# 선행 「김포 web-01 서버 CPU 상위 10건」과 **한 축만** 다른 후속들
_SAME_Q = "김포 web-01 서버 CPU 상위 10건 재조회"


@pytest.mark.parametrize("new", [
    _new(_SAME_Q, agent="alarm_query"),                                  # 다른 담당
    _new("여의도 web-01 서버 CPU 상위 10건 재조회"),                      # 다른 DB 지목
    _new("은행존 김포 web-01 서버 CPU 상위 10건 재조회"),                 # DB 지목이 늘었다
    _new("김포 web-02 서버 CPU 상위 10건 재조회"),                        # 다른 식별 대상
    _new(_SAME_Q, input_from=["t3"], depends_on=["t3"], supersedes=[]),  # 다른 선행 결과 보강
    _new(_SAME_Q, db_ids=["polestar_cm_yd"]),                            # 선행이 안 본 DB 고정
], ids=["다른_담당", "다른_DB", "DB_지목_추가", "다른_대상", "다른_선행_보강", "다른_DB_고정"])
def test_q3_keeps_different_queries(new) -> None:
    """다른 조회는 보존한다."""
    prior, res = _failed(sub_query="김포 web-01 서버 CPU 상위 10건")
    assert _filter_regen_stopped([_new(_SAME_Q)], [prior], {"t1": res})[0] == [], "대조군은 막힌다"
    kept, notice = _filter_regen_stopped([new], [prior], {"t1": res})
    assert kept == [new] and notice is None


def test_q3_explicit_supersedes_is_same_query() -> None:
    """대체를 명시하면 입력 의존이 있어도 같은 조회다."""
    prior, res = _failed()
    new = _new(input_from=["t1"], depends_on=["t1"], supersedes=["t1"])
    kept, notice = _filter_regen_stopped([new], [prior], {"t1": res})
    assert kept == [] and notice


def test_q3_failure_without_regen_stop_is_untouched() -> None:
    """재생성 중단 표지가 없는 실패는 대상이 아니다."""
    prior, res = _failed(stop=None)
    assert _filter_regen_stopped([_new()], [prior], {"t1": res}) == ([_new()], None)


def test_q3_registry_failure_keeps_followups() -> None:
    """레지스트리를 못 읽으면 보존한다."""
    prior, res = _failed()
    with patch.object(_rp, "get_registry", side_effect=RuntimeError("no registry")):
        assert _filter_regen_stopped([_new()], [prior], {"t1": res}) == ([_new()], None)


@pytest.mark.asyncio
async def test_q3_replanner_stops_with_reason(mock_config) -> None:
    """replanner가 재위임을 막고 사유로 끝낸다."""
    prior, res = _failed()
    llm = _llm({"needs_followup": True,
                "reason": ("이전 data_query 작업이 SQL 검증 오류로 실패 — "
                           "올바른 SELECT 문으로 재조회 필요"),
                "new_tasks": [{"agent": "data_query", "sub_query": "김포 서버 CPU 상위 10건 재조회",
                               "supersedes": ["t1"]}]})

    out = await replanner(_state([prior], {"t1": res}), llm=llm, app_config=_pin(mock_config))

    llm.ainvoke.assert_awaited_once()          # Q-3 는 평가 뒤 결정적 필터다(평가 전 판정 미채택)
    assert out["needs_replan"] is False and "task_plan" not in out
    assert "검증을 통과하지 못했습니다" in out["replan_stop_notice"]
    final = _apply_incomplete_notice({"final_response": "본문"}, out)["final_response"]
    assert final.startswith("본문\n\n---\n「김포 서버 CPU 상위 10건」 조회는")


@pytest.mark.asyncio
async def test_q3_partial_block_continues_with_rest(mock_config) -> None:
    """일부만 막히면 나머지 후속으로 계속한다."""
    prior, res = _failed()
    llm = _llm({"needs_followup": True, "reason": "알람 경로로 재조회",
                "new_tasks": [
                    {"agent": "data_query", "sub_query": "김포 서버 CPU 상위 10건",
                     "supersedes": ["t1"]},
                    {"agent": "alarm_query", "sub_query": "김포 서버 CPU 임계 알람",
                     "supersedes": ["t1"]},
                ]})

    out = await replanner(_state([prior], {"t1": res}), llm=llm, app_config=_pin(mock_config))

    assert out["needs_replan"] is True and "replan_stop_notice" not in out
    assert [t["agent"] for t in out["task_plan"][1:]] == ["alarm_query"]


# ── T-2 조회 마감(서술 예약) ─────────────────────────────────────────────────


def test_t2_remaining_is_measured_to_retrieval_deadline() -> None:
    """남은 시간은 서술 예약을 뺀 조회 마감 기준이다."""
    state = {"request_deadline": 100.0, "orchestrator_round_sec": 30.0}
    # 처리 마감까지 40초 ≥ 30초지만 서술 예약 15초를 빼면 25초 < 30초 — 멈춘다
    assert _deadline_stop_notice(state, now=60.0) is None
    notice = _deadline_stop_notice(state, now=60.0, reserve_sec=15)
    assert "답변 작성 몫 15초를 뺀 남은 시간 25초 < 직전 조회 한 바퀴 30초" in notice
    assert _deadline_stop_notice(state, now=55.0, reserve_sec=15) is None   # 30 = 30 → 계속


def test_t2_passed_retrieval_deadline_stops() -> None:
    """조회 마감이 지났으면 바퀴 소요와 무관하게 멈춘다."""
    state = {"request_deadline": 100.0, "orchestrator_round_sec": 0.0}
    assert _deadline_stop_notice(state, now=95.0, reserve_sec=5) is not None


def test_t2_zero_reserve_keeps_p1_message() -> None:
    """예약 0이면 종전 P-1 문구와 같다."""
    state = {"request_deadline": 100.0, "orchestrator_round_sec": 60.0}
    assert _deadline_stop_notice(state, now=80.0) == (
        "응답 시간 상한이 가까워 추가 조회가 필요한지 더 판단하지 않고 지금까지의 결과로 "
        "답했습니다(남은 시간 20초 < 직전 조회 한 바퀴 60초)."
    )


@pytest.mark.parametrize("state", [
    {}, {"request_deadline": None, "orchestrator_round_sec": 5.0},
    {"request_deadline": 1.0, "orchestrator_round_sec": None},
])
def test_t2_no_deadline_fields_means_no_judgement(state) -> None:
    """마감 칸이 없으면 판정하지 않는다."""
    assert _deadline_stop_notice(state, now=1e9, reserve_sec=15) is None


@pytest.mark.asyncio
async def test_t2_replanner_uses_configured_reserve(mock_config) -> None:
    """replanner는 설정의 서술 예약으로 판정한다."""
    llm = _llm(_NO_FOLLOWUP)
    prior, res = _failed(stop=None)
    state = _state([prior], {"t1": res}, request_deadline=time.monotonic() + 40,
                   orchestrator_round_sec=30.0)

    out = await replanner(state, llm=llm, app_config=_pin(mock_config, reserve=15))

    llm.ainvoke.assert_not_awaited()
    assert "답변 작성 몫 15초를 뺀 남은 시간" in out["replan_stop_notice"]


@pytest.mark.asyncio
async def test_t2_replanner_keeps_orchestrator_notice(mock_config) -> None:
    """오케스트레이터가 남긴 미시작 사유를 덮지 않는다."""
    prior, res = _failed(stop=None)
    state = _state([prior], {"t1": res}, request_deadline=time.monotonic() - 1,
                   orchestrator_round_sec=0.001, replan_stop_notice="추가 조회 1건 미실행")
    out = await replanner(state, llm=_llm(_NO_FOLLOWUP), app_config=_pin(mock_config, reserve=15))
    assert out["replan_stop_notice"] == "추가 조회 1건 미실행"


def _registry_with(handlers: dict[str, AsyncMock]) -> dict[str, SubAgentSpec]:
    return {name: SubAgentSpec(SUBAGENT_REGISTRY[name].name, SUBAGENT_REGISTRY[name].description,
                               handler, fallback=SUBAGENT_REGISTRY[name].fallback)
            for name, handler in handlers.items()}


def _reentry_plan() -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    t1 = _task(status="completed")
    t2 = _task("t2", sub_query="김포 서버 CPU 상위 10건 재조회", status="pending",
               supersedes=["t1"])
    return [t1, t2], {"t1": {"query_results": [], "source": [{"db_id": "polestar_cm_gp"}]}}


@pytest.mark.asyncio
async def test_t2_reentry_after_deadline_starts_nothing(mock_config) -> None:
    """조회 마감이 지난 재진입 바퀴는 후속을 시작하지 않는다."""
    handler = AsyncMock(return_value=_rows())
    tasks, results = _reentry_plan()
    state = _state(tasks, results, request_deadline=time.monotonic() + 10)   # 예약 15 > 10

    with patch.dict(SUBAGENT_REGISTRY, _registry_with({"data_query": handler})):
        out = await _ao.agent_orchestrator(state, llm=AsyncMock(),
                                           app_config=_pin(mock_config, reserve=15))

    handler.assert_not_awaited()
    assert [t["task_id"] for t in out["task_plan"]] == ["t1"]        # 미시작 후속은 계획에서 뺀다
    assert out["task_results"] == results                             # 이미 얻은 결과 보존
    notice = out["replan_stop_notice"]
    assert "추가 조회 1건(「김포 서버 CPU 상위 10건 재조회」)은 실행하지 않고" in notice
    assert "답변 작성 몫 15초를 남긴 조회 마감 경과" in notice


@pytest.mark.asyncio
@pytest.mark.parametrize("deadline_offset", [None, 60.0])
async def test_t2_reentry_runs_without_or_before_deadline(mock_config, deadline_offset) -> None:
    """마감이 없거나 여유면 후속을 실행한다."""
    handler = AsyncMock(return_value=_rows())
    tasks, results = _reentry_plan()
    over = ({} if deadline_offset is None
            else {"request_deadline": time.monotonic() + deadline_offset})

    with patch.dict(SUBAGENT_REGISTRY, _registry_with({"data_query": handler})):
        out = await _ao.agent_orchestrator(_state(tasks, results, **over), llm=AsyncMock(),
                                           app_config=_pin(mock_config, reserve=15))

    handler.assert_awaited_once()
    assert "replan_stop_notice" not in out and len(out["task_plan"]) == 2


@pytest.mark.asyncio
async def test_t2_first_round_is_never_gated(mock_config) -> None:
    """새 계획(전부 pending)은 대상이 아니다 — 멈추면 답할 데이터가 하나도 없다."""
    handler = AsyncMock(return_value=_rows())
    state = _state([_task(status="pending")], {}, request_deadline=time.monotonic() - 5)

    with patch.dict(SUBAGENT_REGISTRY, _registry_with({"data_query": handler})):
        out = await _ao.agent_orchestrator(state, llm=AsyncMock(),
                                           app_config=_pin(mock_config, reserve=15))

    handler.assert_awaited_once()
    assert "replan_stop_notice" not in out


@pytest.mark.asyncio
async def test_t2_deadline_between_levels_stops_later_levels(mock_config) -> None:
    """레벨 사이에 마감이 지나면 다음 레벨부터 시작하지 않는다."""
    handler = AsyncMock(return_value=_rows())
    t1 = _task(status="completed")
    t2 = _task("t2", sub_query="김포 서버 메모리", status="pending")
    t3 = _task("t3", sub_query="그 서버들의 알람", status="pending", depends_on=["t2"])
    passed = iter([False, True])

    with patch.dict(SUBAGENT_REGISTRY, _registry_with({"data_query": handler})), \
            patch.object(_ao, "_retrieval_deadline_passed", side_effect=lambda *_: next(passed)):
        out = await _ao.agent_orchestrator(_state([t1, t2, t3], {"t1": _rows()}), llm=AsyncMock(),
                                           app_config=_pin(mock_config, reserve=15))

    assert handler.await_count == 1
    assert [t["task_id"] for t in out["task_plan"]] == ["t1", "t2"]
    assert "「그 서버들의 알람」" in out["replan_stop_notice"]


# ── T-6 예산 인지 재계획 프롬프트(기본 off) ──────────────────────────────────


def _llm_path_state(**over: Any) -> dict[str, Any]:
    return _state([_task()], {"t1": {**_rows(), "db_errors": {"polestar_cm_yd": "timeout"}}},
                  replan_count=1, **over)


@pytest.mark.asyncio
async def test_t6_off_keeps_input_byte_identical(mock_config) -> None:
    """off면 평가 입력이 바이트 동일하다."""
    llm = _llm(_NO_FOLLOWUP)
    state = _llm_path_state(request_deadline=time.monotonic() + 100, orchestrator_round_sec=5.0)

    await replanner(state, llm=llm, app_config=_pin(mock_config, reserve=15))

    assert _messages(llm)[1].content == _build_eval_context(
        state["user_query"], state["task_plan"], state["task_results"])


def test_t6_default_is_off() -> None:
    """기본값은 off다."""
    from src.config import AppConfig

    assert AppConfig.model_fields["replan_budget_prompt_enabled"].default is False


@pytest.mark.asyncio
async def test_t6_on_appends_budget_block(mock_config) -> None:
    """on이면 평가 입력 말미에 예산 블록이 붙는다."""
    llm = _llm(_NO_FOLLOWUP)
    state = _llm_path_state(request_deadline=time.monotonic() + 100.4, orchestrator_round_sec=5.0)

    await replanner(state, llm=llm, app_config=_pin(mock_config, reserve=15, budget_prompt=True))

    system, human = _messages(llm)
    assert system.content == REPLANNER_SYSTEM_TEMPLATE                # 시스템 접두 불변(KV 캐시)
    base = _build_eval_context(state["user_query"], state["task_plan"], state["task_results"])
    assert human.content.startswith(base + "\n\n## 남은 예산 (시간·재계획)\n")
    assert ("- 조회 마감까지 남은 시간: 약 85초 (답변 작성에 쓸 15초는 이미 뺐습니다)"
            in human.content)
    assert "- 남은 재계획 횟수: 2회 (최대 3회)" in human.content
    assert "지금까지의 결과로 종결할 수 있습니다" in human.content


@pytest.mark.asyncio
async def test_t6_on_without_deadline_omits_time_line(mock_config) -> None:
    """on이어도 마감이 없으면 시간 줄은 뺀다."""
    llm = _llm(_NO_FOLLOWUP)
    await replanner(_llm_path_state(), llm=llm,
                    app_config=_pin(mock_config, budget_prompt=True))
    human = _messages(llm)[1].content
    assert "남은 재계획 횟수: 2회" in human and "조회 마감까지" not in human
