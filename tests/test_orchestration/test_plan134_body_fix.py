"""plans/134 본체 W5·W6·W7 결함 수정(body-fix · 2026-10-06) 회귀 테스트.

재현 원본은 `test_plan134_w567_body_verify.py`(V-1~V-7)다. 여기서는 처분의 경계를 고정한다.

  1. V-1: 같은 계획 「목록 → 참조」(느린 트랜잭션 → 프로파일 · 실행 중 서비스 → 요청 상세)는 서버
     키 병합(D-100)으로 접지 않고 단계별로 답한다 — 서버당 1행 목록도 참조 단계가 목록 행을
     지우지 않는다. 서버당 1행인 APM 결과·폴스타 결과의 병합은 종전대로다.
  2. V-2: 분해가 고른 보기는 분해 조건만 쓴다(합의·재시도 성공 분기 모두) — 재시도 조건은 재시도가
     새로 더한 보기에만. 선택 프롬프트에 예문 값 금지 한 줄 + 사용자 원문.
  3. V-3: GUID 결정적 줄에 조회 구간 · 기본 창 고지(게이트웨이 문구) · 「호출 관계가 아님」.
  4. V-4: 좁힘 서버마다 부른 GUID 추적은 (소스, 도메인, txid) 중복 제거 + GUID 줄 1개.
  5. V-5: `depends_on`만 건 선행 APM task 행도 참조 후보 원천이다(다른 처리기는 아니다).
  6. V-7: 전 행 null 강등(C-06)은 APM 처리기 결과에 적용하지 않는다 — SQL 결과는 종전대로.
  7. 참조 보기만 고른 task에는 순차 게이트의 「대상을 한정」 경과 노트를 싣지 않는다.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이 · 실 LLM 0).
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration import subagents as sa
from tests.test_orchestration.test_plan134_w567_body_verify import (
    T0,
    _active,
    _apm_task,
    _app_config,
    _cfg,
    _env,
    _fresh,
    _Gateway,
    _isolated,
    _real,
    _run,
    _SelectLLM,
    _two_tier_turn,
    _tx,
)

ao = importlib.import_module("src.orchestration.agent_orchestrator")
ra = importlib.import_module("src.orchestration.result_aggregator")
#: 목 Open API ↔ 게이트웨이 실프로세스(모듈 범위 픽스처 — 검증 파일 것을 그대로 쓴다)
live_gateway = importlib.import_module(
    "tests.test_orchestration.test_plan134_w567_body_verify").real_gateway


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict | None = None) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _tables(answer: str) -> list[list[dict[str, str]]]:
    """최종 답 본문의 마크다운 표들 → 표마다 행 dict 목록."""
    tables: list[list[dict[str, str]]] = []
    block: list[str] = []
    for line in [*answer.splitlines(), ""]:
        if line.startswith("|"):
            block.append(line)
            continue
        if block:
            header = [c.strip() for c in block[0].strip("|").split("|")]
            tables.append([dict(zip(header, (c.strip() for c in row.strip("|").split("|"))))
                           for row in block[2:]])
            block = []
    return tables


def _bare(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "hostname"}


# ── 1. V-1 목록 → 참조는 단계별 답 ─────────────────────────────────────────────

_PROFILE_103 = {"source_id": "default", "domain_id": 1000, "txid": "103", "time_ms": T0 + 3500,
                "transaction": {"txid": "103"}, "profile_excerpt": "STEP 103"}
_DETAIL_801 = {"source_id": "default", "domain_id": 1000, "txid": "-801", "sql": "select 801"}


@pytest.mark.parametrize("views,tools,list_rows,ref_row,key,listed", [
    (("apm.slow_tx", "apm.profile"), ("apm_slow_transactions", "apm_transaction_profile"),
     [_bare(_tx(i)) for i in (2, 3)], _PROFILE_103, "profile_excerpt", ["102", "103"]),
    (("apm.active", "apm.active_detail"), ("apm_active_services", "apm_active_detail"),
     [_bare(_active(i)) for i in (0, 1)], _DETAIL_801, "sql", ["800", "-801"]),
], ids=["slow_then_profile", "active_then_detail"])
async def test_list_then_reference_answers_by_steps_without_folding_rows(
        gateway, views, tools, list_rows, ref_row, key, listed) -> None:
    (list_view, ref_view), (list_tool, ref_tool) = views, tools
    gateway({list_tool: _env(list_tool, list_rows), ref_tool: _env(ref_tool, [ref_row])})
    q = "web01 목록 보고 두 번째 상세도"
    state = await _two_tier_turn(_fresh(q, "th-fix-v1"), q, [
        _apm_task([list_view], tid="t1"),
        _apm_task([ref_view], tid="t2", view_args={ref_view: {"ref": 2}}, input_from=["t1"],
                  depends_on=["t1"], order=2)], _app_config(), hosts=("web01",))
    first, second = _tables(state["final_response"])
    assert [r["txid"] for r in first] == listed, "목록 표의 행이 접히지 않는다"
    assert key not in first[0], "목록 행에 다른 거래의 상세가 붙지 않는다"
    assert [(r["txid"], r[key]) for r in second] == [(ref_row["txid"], ref_row[key])]
    rows = state["query_results"]
    assert [r["txid"] for r in rows] == [*listed, ref_row["txid"]]
    assert all(r.get(key) is None for r in rows[:2])


async def test_one_row_per_server_list_keeps_every_server_row(gateway) -> None:
    """서버당 1행 목록(web01·web02) → 2번째 프로파일: 병합 좁히기가 web01 행을 지우지 않는다."""
    by_host = {"web01": [_bare(_tx(1, "web01"))], "web02": [_bare(_tx(2, "web02"))]}
    profile = {**_PROFILE_103, "txid": "102", "transaction": {"txid": "102"}}
    gateway({"apm_slow_transactions": lambda a: _env("apm_slow_transactions",
                                                     by_host[a["hostname"]]),
             "apm_transaction_profile": _env("apm_transaction_profile", [profile])})
    q = "web01 web02 느린 트랜잭션 보고 두 번째 프로파일도"
    state = await _two_tier_turn(_fresh(q, "th-fix-v1b"), q, [
        _apm_task(["apm.slow_tx"], tid="t1"),
        _apm_task(["apm.profile"], tid="t2", view_args={"apm.profile": {"ref": 2}},
                  input_from=["t1"], depends_on=["t1"], order=2)], _app_config(),
        hosts=("web01", "web02"))
    first, second = _tables(state["final_response"])
    assert [(r["hostname"], r["txid"]) for r in first] == [("web01", "101"), ("web02", "102")]
    assert [r["txid"] for r in second] == ["102"]


def _merge(tasks: list[tuple[str, str, list[str], list[dict]]]) -> list[dict] | None:
    ordered = [{"task_id": tid, "agent": agent, "views": views, "order": i}
               for i, (tid, agent, views, _) in enumerate(tasks)]
    results = {tid: {"query_results": rows, "organized_data": {"rows": rows}}
               for tid, _, _, rows in tasks}
    return ra._merge_task_results_by_identity(ordered, results)


def test_server_level_merges_are_unchanged() -> None:
    """서버당 1행 APM 결과 + 폴스타 결과 병합(F-14 계열)과 폴스타 다건 병합은 종전대로다."""
    apm = [{"hostname": "web01", "tps": 3}, {"hostname": "web02", "tps": 4}]
    sql = [{"hostname": "web01", "cpu": 10}, {"hostname": "web02", "cpu": 20}]
    merged = _merge([("t1", "data_query", [], sql), ("t2", "apm_query", ["apm.app_health"], apm)])
    assert merged is not None
    assert [(r["hostname"], r["cpu"], r["tps"]) for r in merged] == [
        ("web01", 10, 3), ("web02", 20, 4)]
    alarms = [{"hostname": "web01", "alarm": "a"}, {"hostname": "web01", "alarm": "b"}]
    assert _merge([("t1", "alarm_query", [], alarms),
                   ("t2", "apm_query", ["apm.app_health"], apm[:1])]) is not None, (
        "폴스타 서버당 다건은 종전 병합(대표 유지) 그대로")


def test_apm_rows_per_transaction_are_not_merged() -> None:
    sql = [{"hostname": "web01", "cpu": 10}]
    many = [{"hostname": "web01", "txid": "1"}, {"hostname": "web01", "txid": "2"}]
    assert _merge([("t1", "data_query", [], sql[:1]),
                   ("t2", "apm_query", ["apm.slow_tx"], many)]) is None


# ── 2. V-2 분해가 고른 보기는 분해 조건만 ───────────────────────────────────────

async def test_retry_that_covers_the_areas_does_not_fill_the_planned_view_condition(
        gateway) -> None:
    """재시도 성공 분기(재시도 보기가 영역을 덮음)도 분해가 고른 보기의 조건을 덮지 않는다."""
    gw = gateway()
    retry = _SelectLLM({"views": ["apm.process", "apm.instances"],
                        "view_args": {"apm.process": {"process_id": 12345}}})
    iso = _isolated("web01", query="web01 프로세스 ID로 인스턴스 찾아줘")
    res = await _run(["apm.process"], iso, areas=["was_instance"], llm=retry)
    assert res["apm_query"]["selection"]["result"] == "retried"
    assert not gw.named("apm_config"), "말하지 않은 PID로 조회하지 않는다"
    assert gw.named("apm_instance_map"), "재시도가 새로 더한 보기는 조회한다"
    notices = [d["text"] for d in res.get("disclosures") or []
               if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert any("프로세스 ID" in t for t in notices), notices


def test_selected_args_keep_planned_bundles_and_take_retry_only_for_new_views() -> None:
    planned = {"apm.process": {"process_id": 7}, aq.FLAT_VIEW_ARGS_KEY: {"level": "fatal"}}
    retried = {"apm.process": {"process_id": 12345}, "apm.events": {"level": "warning"},
               aq.FLAT_VIEW_ARGS_KEY: {"n": 3}}
    out = aq._selected_args(["apm.process", "apm.events"], planned, retried, ["apm.process"])
    assert out == {"apm.process": {"process_id": 7}, "apm.events": {"level": "warning"},
                   aq.FLAT_VIEW_ARGS_KEY: {"level": "fatal"}}
    fresh = aq._selected_args(["apm.events"], {}, retried, [])
    assert fresh == {"apm.events": {"level": "warning"}, aq.FLAT_VIEW_ARGS_KEY: {"n": 3}}, (
        "분해가 고른 보기가 없으면 재시도 묶음(평면 포함) 그대로")


async def test_selection_prompt_forbids_example_values_and_carries_the_original(gateway) -> None:
    gateway()
    llm = _SelectLLM({"views": []})
    task = {"task_id": "t1", "agent": "apm_query", "views": ["apm.process"],
            "areas": ["was_instance"], "sub_query": "web01 PID로 인스턴스", "input_from": []}
    iso = _isolated("web01", query="web01 프로세스 ID로 WAS 인스턴스 찾아줘")
    await aq.run_apm_query(task, iso, llm=llm, app_config=_cfg())
    ((system, human),) = llm.messages
    assert "「예」의 값은 형식을 보여 주는 예일 뿐이다" in system.content
    assert human.content == ("web01 PID로 인스턴스\n\n사용자 원문: web01 프로세스 ID로 WAS 인스턴스"
                             " 찾아줘")


# ── 3. V-3 GUID 조회 구간 고지 ───────────────────────────────────────────────

_WINDOW = {"start": "2026-10-02T09:55:00+09:00", "end": "2026-10-02T10:05:00+09:00",
           "minutes": 10}
_TOPOLOGY = "[한계] GUID가 같은 거래 묶음이다 — 호출 관계(토폴로지)를 뜻하지 않는다"


def _trace_env(rows: list[dict], limits: list[str], **summary: Any) -> dict:
    return _env("apm_transaction_trace", rows, window=_WINDOW, limits=limits, summary={
        "guid": "g-1", "transactions": len(rows), "domains_queried": 2,
        "domains_with_hits": len(rows), "domains_failed": 0, **summary})


_TRACE_ROW = {"source_id": "default", "domain_id": 1000, "txid": "1", "guid": "g-1",
              "instance_name": "a", "trace_order": 1}


@pytest.mark.parametrize("limits,tail", [
    ([_TOPOLOGY, "[한계] 기간 미지정 — ±5분(앞 결과 시각 기준)"],
     "(기간 미지정 — ±5분(앞 결과 시각 기준))"),
    ([_TOPOLOGY], ""),
], ids=["default_window", "user_period"])
async def test_trace_line_carries_window_default_notice_and_call_relation_disclaimer(
        gateway, limits, tail) -> None:
    gateway({"apm_transaction_trace": _trace_env([_TRACE_ROW], limits, instances=["a"])})
    res = await _run(["apm.trace"], _isolated(), view_args={"apm.trace": {"guid": "g-1"}})
    (line,) = [x for x in res["answer_lines"] if x.startswith("GUID g-1")]
    span = "조회 구간 2026-10-02T09:55:00+09:00 ~ 2026-10-02T10:05:00+09:00"
    assert f" · {span}{tail} · 같은 GUID일 뿐 호출 관계가 아님 · 인스턴스 a" in line, line
    assert ("기간 미지정" in line) is bool(tail)


async def test_zero_hit_trace_answer_says_where_it_looked(gateway) -> None:
    limits = [_TOPOLOGY, "[한계] 기간 미지정 — 최근 60분",
              "[한계] 구간 안에서 GUID 거래를 찾지 못했다(구간 …)"]
    gateway({"apm_transaction_trace": _trace_env([], limits)})
    q = "GUID g-1 연계 거래"
    state = await _two_tier_turn(_fresh(q, "th-fix-v3"), q, [_apm_task(
        ["apm.trace"], view_args={"apm.trace": {"guid": "g-1"}})], _app_config())
    answer = state["final_response"]
    span = "조회 구간 2026-10-02T09:55:00+09:00 ~ 2026-10-02T10:05:00+09:00"
    assert f"{span}(기간 미지정 — 최근 60분)" in answer
    assert "같은 GUID일 뿐 호출 관계가 아님" in answer


async def test_real_gateway_zero_hit_trace_line_names_the_default_window(live_gateway,
                                                                         monkeypatch) -> None:
    """게이트웨이 실코드의 기본 창 `[한계]` 문구가 결정적 줄에 닿는다(문구가 바뀌면 깨진다)."""
    url, _, _, _ = live_gateway
    monkeypatch.setattr(aq, "_SESSION_FACTORY", None)
    res = await _real(["apm.trace"], _isolated(), url, {"apm.trace": {"guid": "no-such-guid"}})
    assert res["source_status"][0]["status"] == "empty", res["source_status"]
    (line,) = [x for x in res["answer_lines"] if x.startswith("GUID no-such-guid: 거래 0건")]
    assert "· 조회 구간 " in line and "(기간 미지정 — 최근 60분) · " in line, line
    assert line.endswith("같은 GUID일 뿐 호출 관계가 아님"), line


# ── 4. V-4 좁힘 서버마다 부른 GUID 추적 ──────────────────────────────────────

def _resolution(*pairs: tuple[str, int]) -> dict:
    return {"matched": True, "confidence": "exact", "instances": [11],
            "instance_refs": [{"source_id": s, "domain_id": d, "instance_id": 11}
                              for s, d in pairs]}


@pytest.mark.parametrize("domains,line_head,rows", [
    ({"web01": 1000, "web02": 1000}, "GUID g-1: 거래 1건 · 도메인 1곳(조회 1곳 · 실패 0곳) · ", 1),
    ({"web01": 1000, "web02": 2000}, "GUID g-1: 거래 2건 · 도메인 2곳(조회 2곳 · 실패 0곳) · ", 2),
], ids=["same_domain", "two_domains"])
async def test_trace_over_hosts_is_one_deduplicated_result(gateway, domains, line_head,
                                                           rows) -> None:
    def reply(args: dict) -> dict:
        domain = domains[args["hostname"]]
        row = {**_TRACE_ROW, "domain_id": domain, "txid": str(domain),
               "instance_name": f"i{domain}", "start_time_ms": T0 + domain}
        return _env("apm_transaction_trace", [row], window=_WINDOW, limits=[_TOPOLOGY],
                    instance_resolution=_resolution(("default", domain)), summary={
                        "guid": "g-1", "transactions": 1, "domains_queried": 1,
                        "domains_with_hits": 1, "domains_failed": 0,
                        "first_start_ms": T0 + domain, "last_end_ms": T0 + domain + 10,
                        "instances": [f"i{domain}"]})

    gw = gateway({"apm_transaction_trace": reply})
    res = await _run(["apm.trace"], _isolated("web01", "web02"),
                     view_args={"apm.trace": {"guid": "g-1"}})
    assert len(gw.named("apm_transaction_trace")) == 2
    assert len(res["query_results"]) == rows
    (line,) = [x for x in res["answer_lines"] if x.startswith("GUID g-1")]
    assert line.startswith(line_head), line
    traces = [a for a in res["apm_query"]["aggregates"] if a["view"] == "apm.trace"]
    assert len(traces) == 1 and traces[0]["hostname"] is None
    assert traces[0]["scope"].endswith("web01 · web02")
    if rows == 2:
        assert line.endswith("인스턴스 i1000, i2000")


# ── 5. V-5 depends_on만 건 선행 APM task ─────────────────────────────────────

def test_depends_on_apm_task_rows_are_reference_candidates() -> None:
    prior = {"t0": {"query_results": [{"hostname": "web01", "cpu": 1}]},
             "t1": {"query_results": [_tx(7)]}}
    plan = [{"task_id": "t0", "agent": "data_query"}, {"task_id": "t1", "agent": "apm_query"},
            {"task_id": "t2", "agent": "apm_query"}]
    task = {"task_id": "t2", "agent": "apm_query", "sub_query": "q", "views": ["apm.profile"],
            "depends_on": ["t0", "t1"], "input_from": []}
    iso = sa._make_isolated_input(task, {"user_query": "q", "task_plan": plan}, prior)
    assert list(iso["prior_result_rows"]) == ["t1"], "같은 처리기 선행 task만"
    assert "prior_rows" not in iso, "대상 좁힘(input_from)은 종전 그대로"
    data = sa._make_isolated_input({**task, "agent": "data_query"},
                                   {"user_query": "q", "task_plan": plan}, prior)
    assert "prior_result_rows" not in data


@pytest.mark.parametrize("input_rows,expected", [
    ([_tx(8, host="api02")], "108"),               # input_from 행이 있으면 그 행(종전 그대로)
    ([{"hostname": "web01", "cpu": 1}], "102"),     # 참조 칸이 없으면 depends_on APM task 행
], ids=["input_from_first", "depends_on_fallback"])
async def test_reference_candidates_fall_back_from_input_from_to_depends_on(
        gateway, input_rows, expected) -> None:
    gw = gateway({"apm_transaction_profile": _env("apm_transaction_profile", [{"txid": "x"}])})
    task = {"task_id": "t2", "agent": "apm_query", "views": ["apm.profile"], "sub_query": "q",
            "view_args": {"apm.profile": {"ref": 2 if expected == "102" else 1}},
            "input_from": ["t0"], "depends_on": ["t0", "t1"]}
    iso = _isolated(prior_result_rows={"t0": input_rows, "t1": [_tx(1), _tx(2)]})
    await aq.run_apm_query(task, iso, llm=None, app_config=_cfg())
    assert gw.named("apm_transaction_profile")[0]["txid"] == expected


# ── 6. V-7 C-06은 SQL 결과에만 ───────────────────────────────────────────────

_NULL_ROWS = [{"user_id": "o***", "email": None, "phone_number": None, "group": "admin"}]


@pytest.mark.parametrize("agent,downgraded", [("apm_query", False), ("data_query", True)])
async def test_all_null_downgrade_applies_to_sql_results_only(agent, downgraded) -> None:
    res = {"organized_data": {"summary": "s", "rows": _NULL_ROWS, "column_mapping": None,
                              "resolved_mapping": None, "is_sufficient": True,
                              "sheet_mappings": None},
           "query_results": _NULL_ROWS}
    task = {"task_id": "t1", "agent": agent, "sub_query": "계정 op01", "order": 1}
    state = {"user_query": "계정 op01", "parsed_requirements": {}, "task_plan": [task]}
    out = await ra._finalize_task(task, res, state, FakeListChatModel(responses=["요약."] * 3),
                                  _app_config(), stream_user_response=False)
    assert ("목록 표시를 생략" in out["text"]) is downgraded, out["text"]
    if not downgraded:
        assert "admin" in out["text"]


# ── 7. 참조 보기 task의 경과 노트 ────────────────────────────────────────────

@pytest.mark.parametrize("views,prior_rows,noted", [
    (["apm.profile"], [_tx(1)], False),
    (["apm.app_health"], [_tx(1)], True),
    (["apm.profile", "apm.app_health"], [_tx(1)], True),
    (["apm.profile"], [], True),
], ids=["reference_only", "targeted_view", "mixed", "gate_skip"])
def test_dependency_trace_note_is_left_out_for_reference_views(views, prior_rows,
                                                                noted) -> None:
    task = {"task_id": "t2", "agent": "apm_query", "views": views, "input_from": ["t1"],
            "depends_on": ["t1"]}
    notes: list[dict] = []
    ao._gate_level([task], {"t1": {"query_results": prior_rows}}, gate_on=True, notes=notes)
    assert bool(notes) is noted, notes
