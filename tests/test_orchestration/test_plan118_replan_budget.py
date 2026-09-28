"""plans/118 P-1·P-2 — 2단 재계획기의 시간 예산 인식과 전 DB 연속 0건 재조회 중단.

run `20260922-162132` 시도 2 구간(180초 상한 191턴)에서 `replanner ≥ 2` 23턴의 wall p50 이
180.1초였고, 180초 타임아웃 16턴 중 11턴이 이 루프였다(118 §2.3).

  P-1 (G-2 · 플래그 없음) — 남은 시간 < 직전 `agent_orchestrator` 한 바퀴면 LLM 평가 없이 종료하고
      사유를 응답에 싣는다. 마감이 없는 상태(CLI·옛 체크포인트)는 종전 동작이다.
  P-2 (G-3 · D-063 개정) — 직전 두 바퀴가 모두 대상 DB 전부 0건이고 새 후속이 같은 엔티티면
      후속을 제거하고 결정적 사유(조회한 DB 목록)로 끝낸다. 첫 0건 뒤 재조회는 그대로다.

LLM·DB 0 — LLM 은 모의 객체다.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.replanner import (
    _deadline_stop_notice,
    _filter_repeated_empty,
    _rounds,
    replanner,
)
from src.orchestration.result_aggregator import _apply_incomplete_notice
from src.state import create_followup_input, create_initial_state


def _llm(payload: dict) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    return llm


_FOLLOWUP = {"needs_followup": True, "reason": "추가 확인",
             "new_tasks": [{"agent": "alarm_query", "sub_query": "그 서버들의 알람 이력",
                            "depends_on": ["t1"], "input_from": ["t1"]}]}


def _state(**over):
    state = create_initial_state(user_query="김포 서버 CPU 상위 10건")
    state["task_plan"] = [{"task_id": "t1", "agent": "data_query", "sub_query": "CPU 상위",
                           "depends_on": [], "input_from": [], "order": 1,
                           "status": "completed"}]
    # 부분 실패(`db_errors`) — 전 task 성공이면 plans/119 N-2(D-251 ⑤)가 LLM 평가·시간 판정 전에
    # 끝내므로, P-1 판정을 재려면 LLM 평가 경로에 남는 상태여야 한다.
    state["task_results"] = {"t1": {"query_results": [{"hostname": "web-01", "cpu": 91}],
                                    "source": [{"db_id": "polestar"}],
                                    "db_errors": {"polestar_cm_yd": "timeout"}}}
    state.update(over)
    return state


# ── P-1 시간 예산 ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_마감_20초_전_직전_바퀴_60초면_LLM_없이_종료하고_사유를_싣는다(mock_config) -> None:
    llm = _llm(_FOLLOWUP)
    state = _state(request_deadline=time.monotonic() + 20, orchestrator_round_sec=60.0)

    out = await replanner(state, llm=llm, app_config=mock_config)

    llm.ainvoke.assert_not_awaited()
    assert out["needs_replan"] is False
    assert "응답 시간 상한" in out["replan_stop_notice"]
    assert "직전 조회 한 바퀴 60초" in out["replan_stop_notice"]


@pytest.mark.asyncio
async def test_마감이_여유면_종전_동작이다(mock_config) -> None:
    llm = _llm(_FOLLOWUP)
    state = _state(request_deadline=time.monotonic() + 170, orchestrator_round_sec=60.0)

    out = await replanner(state, llm=llm, app_config=mock_config)

    llm.ainvoke.assert_awaited_once()
    assert out["needs_replan"] is True and "replan_stop_notice" not in out


@pytest.mark.asyncio
@pytest.mark.parametrize("over", [
    {},                                                     # CLI — 마감 칸이 비어 있다
    {"request_deadline": None, "orchestrator_round_sec": 60.0},
    {"request_deadline": 1.0, "orchestrator_round_sec": None},   # 3단 계획 루프 — 바퀴 계측 없음
])
async def test_마감이나_바퀴_소요가_없으면_종전_동작이다(mock_config, over) -> None:
    llm = _llm(_FOLLOWUP)
    out = await replanner(_state(**over), llm=llm, app_config=mock_config)

    llm.ainvoke.assert_awaited_once()
    assert out["needs_replan"] is True


def test_남은_시간이_바퀴와_같으면_계속한다() -> None:
    state = {"request_deadline": 100.0, "orchestrator_round_sec": 30.0}
    assert _deadline_stop_notice(state, now=70.0) is None      # 남은 30 = 바퀴 30
    assert _deadline_stop_notice(state, now=70.1) is not None


@pytest.mark.asyncio
async def test_오케스트레이터가_한_바퀴_소요를_남긴다(mock_config, monkeypatch) -> None:
    state = create_initial_state(user_query="q")
    state["task_plan"] = []
    out = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)
    assert isinstance(out["orchestrator_round_sec"], float) and out["orchestrator_round_sec"] >= 0


def test_마감_키는_요청_스코프로_매_턴_초기화된다() -> None:
    """체크포인터는 델타만 병합한다 — 두 생성기 모두 세 키를 비운다(CLAUDE.md Known Mistakes)."""
    delta = create_followup_input("다음 질의")
    fresh = create_initial_state(user_query="q")
    for key in ("request_deadline", "orchestrator_round_sec", "replan_stop_notice"):
        assert key in delta and delta[key] is None
        assert key in fresh and fresh[key] is None


def test_집계기가_중단_사유를_응답_말미에_싣는다() -> None:
    out = _apply_incomplete_notice({"final_response": "본문"},
                                   {"replan_stop_notice": "시간 상한 사유"})
    assert out["final_response"] == "본문\n\n---\n시간 상한 사유"
    both = _apply_incomplete_notice(
        {"final_response": "본문"},
        {"orchestration_incomplete_notice": "미실행 안내", "replan_stop_notice": "중단 사유"})
    assert both["final_response"].endswith("미실행 안내\n\n중단 사유")
    assert _apply_incomplete_notice({"final_response": "본문"}, {})["final_response"] == "본문"


def test_라우트가_마감을_싣는다() -> None:
    """진입점 4곳(텍스트·스트림·파일·파일 스트림)이 같은 헬퍼로 마감을 싣는다(D-066 대칭)."""
    from pathlib import Path

    from src.api.routes import query as q

    before = time.monotonic()
    assert q._request_deadline(120) >= before + 120
    assert q._request_deadline(0) is None
    source = Path(q.__file__).read_text(encoding="utf-8")
    assert source.count('["request_deadline"] = _request_deadline(') == 4


# ── P-2 전 DB 연속 0건 ───────────────────────────────────────────────────────


_DBS = [{"db_id": "polestar_b0"}, {"db_id": "polestar_cm_gp"}, {"db_id": "polestar_cm_yd"}]


def _plan_b05(*, second_round_rows: int = 0, single_db: bool = False):
    """B-05 로그 형태 — `sbhdbo53` 를 3개 DB에서 두 바퀴 연속 0건."""
    source = [_DBS[0]] if single_db else _DBS
    plan = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "sbhdbo53 서버 CPU 조회",
         "order": 1, "status": "completed", "depends_on": []},
        {"task_id": "t2", "agent": "data_query",
         "sub_query": "sbhdbo53 와일드카드·유사명 재조회", "order": 2, "status": "completed",
         "depends_on": []},
    ]
    results = {
        "t1": {"query_results": [], "source": source},
        "t2": {"query_results": [{"hostname": "x"}] * second_round_rows, "source": source},
    }
    history = [{"count": 1, "reason": "0건", "added": 1}]
    return plan, results, history


_THIRD = [{"task_id": "t3", "agent": "data_query", "sub_query": "SBHDBO53 존재 여부 확인",
           "depends_on": [], "supersedes": []}]


@pytest.mark.parametrize("single_db", [False, True])
def test_두_바퀴_연속_전_DB_0건이면_같은_엔티티_세번째를_막는다(single_db) -> None:
    plan, results, history = _plan_b05(single_db=single_db)

    kept, notice = _filter_repeated_empty(_THIRD, plan, results, history)

    assert kept == []
    assert "`sbhdbo53`" in notice and "찾지 못했습니다" in notice
    expected = "polestar_b0" if single_db else "polestar_b0, polestar_cm_gp, polestar_cm_yd"
    assert f"(조회한 DB: {expected})" in notice


def test_첫_0건_뒤_재조회는_그대로_둔다() -> None:
    """D-063 의 「0건 → 재조회」 허용은 1회로 좁힐 뿐 없애지 않는다."""
    plan, results, _ = _plan_b05()
    first_round_only = plan[:1]
    kept, notice = _filter_repeated_empty(_THIRD, first_round_only, results, [])
    assert kept == _THIRD and notice is None


def test_두번째_바퀴가_행을_찾았으면_막지_않는다() -> None:
    plan, results, history = _plan_b05(second_round_rows=2)
    kept, notice = _filter_repeated_empty(_THIRD, plan, results, history)
    assert kept == _THIRD and notice is None


@pytest.mark.parametrize("new_query", ["김포 전체 서버 목록 조회", "web-77 서버 CPU 조회"])
def test_다른_엔티티나_리터럴_없는_후속은_보존한다(new_query) -> None:
    plan, results, history = _plan_b05()
    new = [{**_THIRD[0], "sub_query": new_query}]
    kept, notice = _filter_repeated_empty(new, plan, results, history)
    assert kept == new and notice is None


def test_오류나_미조회_DB가_있으면_전_DB_0건이_아니다() -> None:
    plan, results, history = _plan_b05()
    results["t2"] = {**results["t2"], "skipped_dbs": ["polestar_cm_yd"]}
    assert _filter_repeated_empty(_THIRD, plan, results, history) == (_THIRD, None)
    results["t2"] = {"error": "timeout", "source": _DBS}
    assert _filter_repeated_empty(_THIRD, plan, results, history) == (_THIRD, None)


def test_바퀴는_재계획_이력의_추가_개수로_복원한다() -> None:
    plan = [{"task_id": f"t{i}", "order": i} for i in range(1, 6)]
    rounds = _rounds(plan, [{"added": 1}, {"added": 2}])
    assert [[t["task_id"] for t in r] for r in rounds] == [["t1", "t2"], ["t3"], ["t4", "t5"]]
    assert len(_rounds(plan, [{"added": 9}])) == 1, "개수가 안 맞으면 한 바퀴로 본다"


@pytest.mark.asyncio
async def test_replanner_가_세번째_바퀴를_막고_사유로_끝낸다(mock_config) -> None:
    plan, results, history = _plan_b05()
    llm = _llm({"needs_followup": True, "reason": "존재 여부 확인 필요",
                "new_tasks": [{"agent": "data_query", "sub_query": "sbhdbo53 존재 여부 확인"}]})
    state = create_initial_state(user_query="sbhdbo53 CPU 알려줘")
    state.update(task_plan=plan, task_results=results, replan_count=1, replan_history=history)

    out = await replanner(state, llm=llm, app_config=mock_config)

    assert out["needs_replan"] is False
    assert "task_plan" not in out
    assert "조회한 DB: polestar_b0, polestar_cm_gp, polestar_cm_yd" in out["replan_stop_notice"]
