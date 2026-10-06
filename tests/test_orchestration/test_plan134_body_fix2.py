"""plans/134 본체 코드 리뷰 결함 R-1·R-2 수정(body-fix 2차 · 2026-10-06) 회귀 테스트.

  1. R-1: 앞 결과가 표 여러 개(복합 계획의 task별 표)였으면 「N번째」는 표 안 번호다. 요청한 참조
     종류의 후보가 표 둘 이상에 걸쳐 있으면 추측하지 않고 표(보기 라벨)마다 후보를 들어 되묻는다
     (같은 계획의 선행 task 여럿도 같다). 표가 하나면 종전 번호 그대로다. 표 경계는 집계기가 그
     턴의 `conversation_context`에만 남긴다 — 행·화면 표·CSV 칸은 늘지 않고, 다음 턴이 지나면
     경계가 남지 않으며, 폴스타 `previous_entities` 경로는 그대로다.
  2. R-2(V-8): 계획 LLM이 낸 `ref`가 형식 밖(「5번째」·0·-1)이면 후보가 하나여도 되묻는다 — `ref`를
     내지 않았을 때만 하나뿐인 후보를 쓴다.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이 · 실 LLM 0).
"""

from __future__ import annotations

import importlib

import pytest

from src.domain import disclosure as disc
from src.domain.result_refs import RESULT_TABLES_KEY, TABLE_LABELS_KEY, extract_table_refs
from src.orchestration import apm_query as aq
from src.state import create_followup_input
from tests.test_orchestration.test_plan134_w567_body_verify import (
    _active,
    _apm_task,
    _app_config,
    _ctx,
    _env,
    _error_row,
    _fresh,
    _Gateway,
    _isolated,
    _merge,
    _next_turn,
    _run,
    _two_tier_turn,
    _tx,
)

cr = importlib.import_module("src.nodes.context_resolver")

_SLOW = "느린 트랜잭션(시간 분해)"
_EVENTS = "WAS 이벤트(fatal·warning 등)"


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict | None = None) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _bare(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "hostname"}


_PROFILE = _env("apm_transaction_profile", [{"txid": "x", "profile_excerpt": "STEP"}])


async def _slow_and_events_turn(gw: _Gateway, *,
                                second: tuple[str, str, list[dict]] | None = None,
                                thread: str = "th-r1") -> dict:
    """1턴 — 같은 질의에 느린 트랜잭션 표(t1)와 WAS 이벤트 표(t2) — 둘 다 `profile_ref`가 있다."""
    events = [_bare(_error_row(i)) for i in (1, 2)]
    view, tool, rows = second or ("apm.events", "apm_events", events)
    gw.replies.update({"apm_slow_transactions": _env("apm_slow_transactions",
                                                     [_bare(_tx(i)) for i in (1, 2)]),
                       tool: _env(tool, rows), "apm_transaction_profile": _PROFILE})
    q = "web01 느린 트랜잭션과 다른 목록"
    return await _two_tier_turn(_fresh(q, thread), q, [
        _apm_task(["apm.slow_tx"], tid="t1"), _apm_task([view], tid="t2", order=2)],
        _app_config(), hosts=("web01",))


def _table_headers(answer: str) -> list[list[str]]:
    return [[c.strip() for c in line.strip("|").split("|")]
            for i, line in enumerate(answer.splitlines())
            if line.startswith("|") and not answer.splitlines()[i - 1].startswith("|")]


# ── 1. R-1 표가 여럿이면 표 안 번호 · 걸치면 되묻기 ──────────────────────────────

async def test_reference_spanning_two_previous_tables_asks_which_table(gateway) -> None:
    """리뷰 재현: 「느린 트랜잭션과 WAS 이벤트」 → 「두 번째 이벤트 프로파일」 ref=2가 두 번째 느린
    트랜잭션(txid 102)을 경고 없이 조회했다."""
    gw = gateway()
    state = await _slow_and_events_turn(gw)
    state = await _next_turn(state, "두 번째 이벤트 프로파일")
    refs = state["conversation_context"]["previous_result_refs"]
    assert [e["table"] for e in refs["profile_ref"]] == [0, 0, 1, 1]
    assert refs[TABLE_LABELS_KEY] == [_SLOW, _EVENTS]
    out = await _two_tier_turn(state, "두 번째 이벤트 프로파일", [_apm_task(
        ["apm.profile"], view_args={"apm.profile": {"ref": 2}})], _app_config())
    assert not gw.named("apm_transaction_profile"), "표를 넘어 세어 엉뚱한 행을 조회하지 않는다"
    answer = out["final_response"]
    assert "후보가 표 2개에 걸쳐 있어" in answer and "원하는 목록만 다시 조회한 뒤" in answer
    assert f"「{_SLOW}」 1번 「" in answer and f"「{_EVENTS}」 1번 「" in answer
    assert "txid 502" in answer and "2번 「" in answer, "표마다 표 안 번호로 후보를 든다"
    kinds = {d["kind"] for d in out.get("disclosures") or []}
    assert disc.APM_UNRESOLVED_CONDITION in kinds and disc.KIND_TABLE[
        disc.APM_UNRESOLVED_CONDITION].mandatory


async def test_table_boundary_adds_no_column_to_rows_screen_or_csv(gateway) -> None:
    gw = gateway()
    state = await _slow_and_events_turn(gw)
    tables = state["conversation_context"][RESULT_TABLES_KEY]
    assert tables == [{"label": _SLOW, "rows": 2}, {"label": _EVENTS, "rows": 2}]
    rows = state["query_results"]
    assert len(rows) == 4 and not any({"table", RESULT_TABLES_KEY} & set(r) for r in rows)
    headers = _table_headers(state["final_response"])
    assert len(headers) == 2
    assert headers[0] == list(rows[0]) and headers[1] == list(rows[2]), "화면 표 칸 = 행 칸 그대로"
    assert "table" not in {c for h in headers for c in h}


async def test_one_table_with_the_reference_kind_keeps_its_own_numbers(gateway) -> None:
    """표가 둘이어도 그 종류의 칸이 있는 표가 하나면 그 표 안 번호다(실행 중 서비스 표는
    `active_ref`만 있다)."""
    gw = gateway()
    state = await _slow_and_events_turn(gw, second=(
        "apm.active", "apm_active_services", [_bare(_active(i)) for i in (0, 1)]))
    state = await _next_turn(state, "두 번째 트랜잭션 프로파일")
    await _two_tier_turn(state, "두 번째 트랜잭션 프로파일", [_apm_task(
        ["apm.profile"], view_args={"apm.profile": {"ref": 2}})], _app_config())
    assert gw.named("apm_transaction_profile")[-1]["txid"] == "102"


async def test_single_table_turn_writes_no_boundary_and_numbers_as_before(gateway) -> None:
    gw = gateway({"apm_slow_transactions": _env("apm_slow_transactions",
                                                [_bare(_tx(i)) for i in (1, 2)]),
                  "apm_transaction_profile": _PROFILE})
    q = "web01 느린 트랜잭션"
    state = await _two_tier_turn(_fresh(q, "th-r1-single"), q, [_apm_task(["apm.slow_tx"])],
                                 _app_config(), hosts=("web01",))
    assert RESULT_TABLES_KEY not in (state.get("conversation_context") or {})
    state = await _next_turn(state, "두 번째 트랜잭션 프로파일")
    refs = state["conversation_context"]["previous_result_refs"]
    assert TABLE_LABELS_KEY not in refs and all("table" not in e for e in refs["profile_ref"])
    await _two_tier_turn(state, "두 번째 트랜잭션 프로파일", [_apm_task(
        ["apm.profile"], view_args={"apm.profile": {"ref": 2}})], _app_config())
    assert gw.named("apm_transaction_profile")[-1]["txid"] == "102"


async def test_boundary_does_not_outlive_the_next_turn(gateway) -> None:
    """경계는 그 턴의 집계기만 남긴다 — 다음 턴 맥락(`context_resolver`)이 옮기지 않는다."""
    gw = gateway()
    state = await _slow_and_events_turn(gw)
    state = await _next_turn(state, "web01 느린 트랜잭션만")
    assert RESULT_TABLES_KEY not in state["conversation_context"]
    gw.replies["apm_slow_transactions"] = _env("apm_slow_transactions",
                                               [_bare(_tx(i)) for i in (7, 8, 9, 4)])
    state = await _two_tier_turn(state, "web01 느린 트랜잭션만", [_apm_task(["apm.slow_tx"])],
                                 _app_config(), hosts=("web01",))
    state = await _next_turn(state, "세 번째 트랜잭션 프로파일")
    refs = state["conversation_context"]["previous_result_refs"]
    assert TABLE_LABELS_KEY not in refs, "앞 턴(2+2행) 경계가 이번 4행에 붙지 않는다"
    assert [e["value"]["txid"] for e in refs["profile_ref"]] == ["107", "108", "109", "104"]


async def test_polestar_previous_entities_are_unchanged_by_the_boundary(gateway) -> None:
    gw = gateway()
    state = _merge(await _slow_and_events_turn(gw), create_followup_input("그 서버 CPU"))
    assert RESULT_TABLES_KEY in (state.get("conversation_context") or {}), "경계가 있는 턴"
    with_boundary = await cr.context_resolver(state)
    without = await cr.context_resolver({**state, "conversation_context": {
        k: v for k, v in state["conversation_context"].items() if k != RESULT_TABLES_KEY}})
    a, b = with_boundary["conversation_context"], without["conversation_context"]
    assert {k: v for k, v in a.items() if k != "previous_result_refs"} == {
        k: v for k, v in b.items() if k != "previous_result_refs"}
    assert a["previous_entities"] == b["previous_entities"] and a["previous_entities"]


async def test_same_plan_prior_tasks_spanning_tables_ask_back(gateway) -> None:
    gw = gateway({"apm_transaction_profile": _PROFILE})
    task = {"task_id": "t3", "agent": "apm_query", "views": ["apm.profile"], "sub_query": "q",
            "view_args": {"apm.profile": {"ref": 1}}, "input_from": ["t1", "t2"]}
    iso = _isolated(prior_result_rows={"t1": [_tx(1)], "t2": [_error_row(1)]},
                    prior_result_tables={"t1": {"views": ["apm.slow_tx"]},
                                         "t2": {"views": ["apm.events"]}})
    res = await aq.run_apm_query(task, iso, llm=None, app_config=_app_config())
    assert not gw.named("apm_transaction_profile")
    assert res["degraded_reason"] == "apm_unresolved_condition"
    assert f"「{_SLOW}」 1번 「" in res["final_response"]
    assert f"「{_EVENTS}」 1번 「" in res["final_response"]


def test_extract_table_refs_falls_back_to_one_table_when_the_boundary_does_not_fit() -> None:
    rows = [_tx(1), _tx(2), _error_row(3)]
    whole, labels = extract_table_refs(rows, [{"label": "a", "rows": 2}, {"label": "b", "rows": 2}])
    assert labels == [] and all("table" not in e for e in whole["profile_ref"]), "행 수가 다르면"
    split, labels = extract_table_refs(rows, [{"label": "a", "rows": 2}, {"label": "b", "rows": 1}])
    assert labels == ["a", "b"] and [e["table"] for e in split["profile_ref"]] == [0, 0, 1]
    one, labels = extract_table_refs(rows, [{"label": "a", "rows": 3}])
    assert labels == [] and len(one["profile_ref"]) == 3


# ── 2. R-2 낸 순번이 형식 밖이면 후보가 하나여도 되묻기 ─────────────────────────

@pytest.mark.parametrize("view,ctx_rows,bad", [
    ("apm.active_detail", [_active(0)], "첫번째"),
    ("apm.trace", [_tx(1)], 0),
], ids=["active_detail_text", "trace_zero"])
async def test_unreadable_ref_asks_back_for_every_reference_view(gateway, view, ctx_rows,
                                                                bad) -> None:
    gw = gateway()
    res = await _run([view], _isolated(ctx=_ctx(ctx_rows)), view_args={view: {"ref": bad}})
    assert gw.calls == [] and res["degraded_reason"] == "apm_unresolved_condition"
    assert "1건 중 몇 번째인지 정하지 못해" in res["final_response"]


async def test_no_ref_with_one_candidate_still_uses_that_row(gateway) -> None:
    gw = gateway({"apm_transaction_profile": _PROFILE})
    await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1)])))
    assert gw.named("apm_transaction_profile")[0]["txid"] == "101"


async def test_typed_guid_wins_over_an_unreadable_ref(gateway) -> None:
    gw = gateway()
    await _run(["apm.trace"], _isolated(ctx=_ctx([_tx(1)])),
               view_args={"apm.trace": {"guid": "g-typed", "ref": "두번째"}})
    assert gw.named("apm_transaction_trace")[0]["guid"] == "g-typed"
