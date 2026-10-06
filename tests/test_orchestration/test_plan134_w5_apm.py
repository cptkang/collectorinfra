"""plans/134 W5 본체 — 앞 결과 행 참조(M-6) · 트랜잭션 프로파일 · GUID 연계 거래.

고정하는 계약(팀 리드 계약 §4.1·§4.2·§4.3 · SPEC-apm-question-coverage §6.2 W5 행 · §7.6):
  1. 레지스트리: `apm.profile`(`apm_transaction_profile` · none · 참조 `profile_ref`) ·
     `apm.trace`(`apm_transaction_trace` · range · 참조 `guid` 또는 사용자가 적은 `guid`).
  2. 후보 = 같은 계획의 선행 task 행(`prior_result_rows`) → 없으면 직전 턴 결과
     (`conversation_context.previous_result_refs`). 번호는 그 참조 칸이 **있는** 행 사이의
     표시 순서다.
  3. 선택: `ref`(계획 LLM 값) 범위·종류 검증 · `ref` 없음 + 후보 1 = 그 행 · 여럿 = 되묻기
     (≤3 라벨 · 의무 고지) · 없음 = 조회하지 않고 사유(다른 보기로 대신하지 않는다 · 인스턴스
     목록 첫 홉도 없음).
  4. 행의 참조 칸을 **그대로** 도구에 넘긴다(+ 행 hostname — 게이트웨이 정합 재검사) · `ref`는
     넘기지 않는다. 인가는 이번 턴 권한으로 다시 본다(`is_source_allowed`).
  5. GUID 추적 창: 사용자 기간 > 참조 행 시각(`around_ms`) > 게이트웨이 기본. hostname 좁힘은
     이번 턴 사용자가 말한 서버(단일 task 계획)만 — 선행 task 행·복합 계획 파서 식별자로 좁히지
     않는다.
  6. 결정적 줄: 「GUID …: 거래 N건 · 도메인 …」이 `answer_lines`(→ `**판정·집계**`)에 실린다.
  7. 2턴 종단: 1턴(오케스트레이터 → 집계기 · 2단 배선) 결과가 체크포인터 델타 병합(후속 턴 입력이
     `query_results`를 비우지 않는다) → `context_resolver` → 2턴 처리기로 실제 모양 그대로 이어진다.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이·실 LLM 0). 도구 인자 이름은 계약 §2.2·§2.3 기준이다.
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.domain import disclosure as disc
from src.domain.result_refs import extract_result_refs
from src.orchestration import apm_query as aq
from src.orchestration import investigation_audit as ia
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.result_aggregator import result_aggregator
from src.state import create_followup_input, create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")
cr = importlib.import_module("src.nodes.context_resolver")
sa = importlib.import_module("src.orchestration.subagents")

NOW = datetime(2026, 10, 2, 10, 0, 0)
T0 = 1759366000000


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-02T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Gateway:
    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = self.replies[name]
        if callable(reply):
            reply = reply(arguments)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _tx(i: int, host: str = "web01", **extra: Any) -> dict:
    """느린 트랜잭션 행(게이트웨이 `_tx_fields` + `profile_ref` 모양 · `_collect`가 hostname을
    더한 뒤)."""
    end = T0 + i * 1000 + 500
    row = {"source_id": "default", "domain_id": 1000, "instance_id": 11,
           "instance_name": f"{host}-was1", "application": f"/order/{i}", "txid": str(100 + i),
           "guid": f"g-{i}", "response_time_ms": 1000 * i, "start_time_ms": T0 + i * 1000,
           "end_time_ms": end, "hostname": host,
           "profile_ref": {"source_id": "default", "domain_id": 1000, "txid": str(100 + i),
                           "time_ms": end}}
    row.update(extra)
    return row


def _ctx(rows: list[dict], *, turn: int = 1, turn_count: int = 2) -> dict:
    """직전 턴 결과로 만든 대화 맥락(`context_resolver`와 같은 함수로 추린다)."""
    return {"turn_count": turn_count,
            "previous_result_refs": {"turn": turn, **extract_result_refs(rows)}}


def _isolated(*hosts: str, ctx: dict | None = None, time_range: dict | None = None,
              **extra: Any) -> dict:
    out = {"parsed_requirements": {
               "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
               "time_range": time_range, "limit": None},
           "conversation_context": ctx or {}, "thread_id": "th-1", "user_id": "alice"}
    out.update(extra)
    return out


async def _run(views: list[str], isolated: dict, *, view_args: dict | None = None,
               input_from: list[str] | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": "질의", "input_from": input_from or []}
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, isolated, llm=None, app_config=_cfg(), now=NOW)


_PROFILE_ROW = {"source_id": "default", "domain_id": 1000, "txid": "102", "time_ms": T0 + 2500,
                "transaction": {"guid": "g-2"}, "profile_excerpt": "step", "sqls": ["select ?"]}


def _profile_gateway(gateway) -> Any:
    return gateway({"apm_transaction_profile": _env("apm_transaction_profile", [_PROFILE_ROW]),
                    "apm_instance_map": _env("apm_instance_map", [])})


# ── 1. 레지스트리 ─────────────────────────────────────────────────────────────

def test_w5_views_follow_the_contract_rows() -> None:
    views = {v.id: v for v in aq.apm_views()}
    profile, trace = views["apm.profile"], views["apm.trace"]
    assert (profile.tool, profile.window, profile.target, profile.reference,
            profile.required_input, profile.capability) == (
        "apm_transaction_profile", "none", "reference", "profile_ref", "", "was_transaction")
    assert [(a.name, a.type, a.min) for a in profile.args] == [
        ("ref", "int", 1), ("top_k", "int", 1), ("include_param_key", "bool", None)]
    assert (trace.tool, trace.window, trace.target, trace.reference) == (
        "apm_transaction_trace", "range", "reference", "guid")
    assert [(a.name, a.type) for a in trace.args] == [("guid", "opaque"), ("ref", "int")]
    assert profile.examples and trace.examples


def test_result_refs_keep_display_order_and_empty_slots() -> None:
    rows = [{"hostname": "x", "cpu": 1},                      # 참조 칸 없음(폴스타 행) — 자리 없음
            _tx(1), _tx(2, profile_ref=None), _tx(3)]
    refs = extract_result_refs(rows)
    assert [e["value"]["txid"] if e["value"] else None for e in refs["profile_ref"]] == [
        "101", None, "103"], "값 없는 행도 자리를 지켜 번호가 표와 같다"
    assert [e["value"] for e in refs["guid"]] == ["g-1", "g-2", "g-3"]
    assert "active_ref" not in refs
    assert extract_result_refs([{"hostname": "a"}]) == {}
    assert extract_result_refs(None) == {}


def test_make_isolated_input_passes_full_prior_rows_only_to_apm_query() -> None:
    """실측: 식별 키만 남긴 `prior_rows`에는 참조 칸이 없다 → APM 처리기에는 원 행을 따로 싣는다."""
    prior = {"t1": {"query_results": [_tx(1)]}}
    apm = sa._make_isolated_input({"task_id": "t2", "agent": "apm_query", "sub_query": "q",
                                   "input_from": ["t1"]}, {"user_query": "q"}, prior)
    assert "profile_ref" not in apm["prior_rows"]["t1"][0], "식별 키만(종전 그대로)"
    assert apm["prior_result_rows"]["t1"][0]["profile_ref"]["txid"] == "101"
    data = sa._make_isolated_input({"task_id": "t2", "agent": "data_query", "sub_query": "q",
                                    "input_from": ["t1"]}, {"user_query": "q"}, prior)
    assert "prior_result_rows" not in data, "SQL 처리기 입력은 종전과 같다"


# ── 2. 참조 선택 ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_profile_by_ref_passes_the_row_reference_as_is(gateway) -> None:
    gw = _profile_gateway(gateway)
    rows = [_tx(1), _tx(2, profile_ref={"source_id": "default", "domain_id": 1000, "txid": "102",
                                        "time_ms": T0 + 2500, "profile_no": 4}), _tx(3)]
    res = await _run(["apm.profile"], _isolated(ctx=_ctx(rows)),
                     view_args={"apm.profile": {"ref": 2, "top_k": 5, "include_param_key": True}})
    (args,) = gw.named("apm_transaction_profile")
    for key, value in {"hostname": "web01", "source_id": "default", "domain_id": 1000,
                       "txid": "102", "time_ms": T0 + 2500, "profile_no": 4, "top_k": 5,
                       "include_param_key": True, "owner": "user:alice"}.items():
        assert args[key] == value, key
    assert "ref" not in args and "investigation_id" not in args
    assert gw.named("apm_instance_map") == [], "참조 보기는 인스턴스 목록 첫 홉을 끼우지 않는다"
    (prov,) = res["apm_query"]["provenance"]
    assert prov["reference"] == {"kind": "profile_ref", "origin": "직전 답의 결과", "n": 2,
                                 "fields": ["domain_id", "profile_no", "source_id", "time_ms",
                                            "txid"]}
    command = ia._apm_command(prov)
    assert command.startswith("apm_transaction_profile(hostname=web01, ")
    assert "txid=102" in command and "top_k=5" in command, "감사 commands에 어느 거래인지"
    assert any("직전 답의 결과 2번째" in n for n in res["apm_query"]["notes"])
    assert res["query_results"][0]["hostname"] == "web01"


@pytest.mark.asyncio
async def test_single_candidate_without_ref_is_that_row(gateway) -> None:
    gw = _profile_gateway(gateway)
    await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(7)])))
    (args,) = gw.named("apm_transaction_profile")
    assert args["txid"] == "107"


@pytest.mark.asyncio
async def test_many_candidates_without_ref_ask_back(gateway) -> None:
    gw = _profile_gateway(gateway)
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(i) for i in range(1, 6)])))
    assert gw.calls == [] and gw.opened == 0, "되묻기만 남으면 게이트웨이를 열지 않는다"
    assert res["degraded_reason"] == "apm_unresolved_condition"
    text = res["final_response"]
    assert "후보가 5건이라" in text and "몇 번째인지" in text
    assert "1번 「web01 · /order/1" in text and "3번 「" in text and "외 2건" in text
    assert "4번 「" not in text, "라벨은 ≤3개"
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_UNRESOLVED_CONDITION and disc.KIND_TABLE[item["kind"]].mandatory
    assert res["apm_query"]["unresolved"] == [
        {"view": "apm.profile", "conditions": ["reference"], "queried": False}]


@pytest.mark.asyncio
async def test_ask_back_does_not_depend_on_the_gateway(monkeypatch) -> None:
    @asynccontextmanager
    async def down(url, headers):
        raise aq.SourceMcpError("게이트웨이 내려감")
        yield  # pragma: no cover

    monkeypatch.setattr(aq, "_SESSION_FACTORY", down)
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1), _tx(2)])))
    assert res["degraded_reason"] == "apm_unresolved_condition", \
        "연결 실패가 되묻기를 가리지 않는다"


@pytest.mark.asyncio
@pytest.mark.parametrize(("ref", "phrase"), [
    (4, "3건이라 4번째가 없어"),                 # 범위 밖
    (2, "2번째 행에는 프로파일 참조가 없어"),       # 그 행에는 참조 값이 없다(종류 불일치)
], ids=["out_of_range", "kind_mismatch"])
async def test_bad_ref_asks_back_without_querying(gateway, ref, phrase) -> None:
    gw = _profile_gateway(gateway)
    rows = [_tx(1), _tx(2, profile_ref=None), _tx(3)]
    res = await _run(["apm.profile"], _isolated(ctx=_ctx(rows)),
                     view_args={"apm.profile": {"ref": ref}})
    assert gw.calls == []
    assert phrase in res["final_response"]
    assert "1번 「" in res["final_response"] and "3번 「" in res["final_response"]


@pytest.mark.asyncio
async def test_unreadable_ref_is_folded_into_the_single_notice(gateway) -> None:
    """9B가 순번을 문자열로 내도(「두번째」) 「빼고 조회」와 되묻기가 함께 나가지 않는다."""
    gw = _profile_gateway(gateway)
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1), _tx(2)])),
                     view_args={"apm.profile": {"ref": "두번째"}})
    assert gw.calls == []
    (item,) = res["disclosures"]
    assert "몇 번째인지" in item["text"]
    assert "해석하지 못한 조건(ref='두번째')은 쓰지 않았습니다" in item["text"]
    assert "빼고 조회했습니다" not in item["text"]
    gw2 = _profile_gateway(gateway)
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1)])),
                     view_args={"apm.profile": {"ref": "두번째"}})
    # plans/134 R-2(V-8 · 의도된 갱신) — 낸 순번이 형식 밖이면 후보가 하나여도 되묻는다
    assert gw2.calls == [], "형식 밖 순번을 버리고 하나뿐인 행으로 대신하지 않는다"
    (item,) = res["disclosures"]
    assert "몇 번째인지" in item["text"]
    assert item["text"].endswith("해석하지 못한 조건(ref='두번째')은 쓰지 않았습니다.")


@pytest.mark.asyncio
async def test_no_candidate_is_not_substituted(gateway) -> None:
    gw = gateway({"apm_transaction_profile": _env("apm_transaction_profile", []),
                  "apm_instance_map": _env("apm_instance_map", [{"hostname": "web01"}]),
                  "apm_app_health": _env("apm_app_health", [])})
    res = await _run(["apm.profile"], _isolated("web01"))  # 대상 서버는 있어도 참조 행은 없다
    assert gw.calls == [], "다른 보기·첫 홉으로 대신하지 않는다"
    assert "앞 결과에 프로파일을 볼 트랜잭션이 없어 조회하지 않았습니다" in res["final_response"]
    assert res["degraded_reason"] == "apm_unresolved_condition"


@pytest.mark.asyncio
async def test_row_without_hostname_cannot_be_profiled(gateway) -> None:
    gw = _profile_gateway(gateway)
    row = _tx(1)
    row.pop("hostname")
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([row])))
    assert gw.calls == [] and "서버(hostname) 정보가 없어" in res["final_response"]


@pytest.mark.asyncio
async def test_prior_task_rows_come_before_the_previous_turn(gateway) -> None:
    gw = _profile_gateway(gateway)
    iso = _isolated(ctx=_ctx([_tx(1), _tx(2)]),
                    prior_result_rows={"t0": [_tx(8, host="api02"), _tx(9, host="api02")]})
    await _run(["apm.profile"], iso, view_args={"apm.profile": {"ref": 2}}, input_from=["t0"])
    (args,) = gw.named("apm_transaction_profile")
    assert (args["txid"], args["hostname"]) == ("109", "api02"), "같은 계획의 선행 task 행"
    gw2 = _profile_gateway(gateway)
    iso2 = _isolated(ctx=_ctx([_tx(1), _tx(2)]),
                     prior_result_rows={"t0": [{"hostname": "api02", "cpu": 3}]})
    await _run(["apm.profile"], iso2, view_args={"apm.profile": {"ref": 2}}, input_from=["t0"])
    assert gw2.named("apm_transaction_profile")[0]["txid"] == "102", \
        "선행 행에 참조 칸이 없으면 직전 턴 결과"


@pytest.mark.asyncio
async def test_older_turn_reference_is_labelled(gateway) -> None:
    _profile_gateway(gateway)
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1)], turn=1, turn_count=3)))
    assert any("2턴 전 답의 결과 1번째" in n for n in res["apm_query"]["notes"])


@pytest.mark.asyncio
async def test_reference_is_reauthorized_with_this_turns_rights(gateway) -> None:
    gw = _profile_gateway(gateway)
    res = await _run(["apm.profile"], _isolated(ctx=_ctx([_tx(1)]), user_role="user",
                                                allowed_sources=[]))
    assert gw.opened == 0 and res.get("routing_intent") == "access_denied"


# ── 3. GUID 연계 거래 ────────────────────────────────────────────────────────────

def _trace_gateway(gateway, **summary: Any) -> Any:
    env = _env("apm_transaction_trace", [], summary={"guid": "g", "transactions": 0, **summary})
    return gateway({"apm_transaction_trace": env, "apm_instance_map": _env("apm_instance_map", [])})


@pytest.mark.asyncio
async def test_trace_by_typed_guid_searches_everywhere_by_default(gateway) -> None:
    gw = _trace_gateway(gateway)
    res = await _run(["apm.trace"], _isolated(), view_args={"apm.trace": {"guid": " 0a1b2c "}})
    (args,) = gw.named("apm_transaction_trace")
    assert args["guid"] == "0a1b2c"
    for absent in ("hostname", "around_ms", "reference_time", "lookback_minutes", "source_ids",
                   "ref"):
        assert absent not in args, absent
    assert any("GUID 0a1b2c(직접 지정)" in n for n in res["apm_query"]["notes"])
    assert gw.named("apm_instance_map") == []


@pytest.mark.asyncio
async def test_trace_guid_with_whitespace_is_rejected_and_falls_to_reference(gateway) -> None:
    gw = _trace_gateway(gateway)
    res = await _run(["apm.trace"], _isolated(ctx=_ctx([_tx(4)])),
                     view_args={"apm.trace": {"guid": "a b"}})
    assert gw.named("apm_transaction_trace")[0]["guid"] == "g-4", "무효 GUID 는 버리고 고지 → 참조"
    kinds = [d["kind"] for d in res["disclosures"]]
    assert disc.APM_UNRESOLVED_CONDITION in kinds


@pytest.mark.asyncio
async def test_trace_window_priority(gateway) -> None:
    gw = _trace_gateway(gateway)
    await _run(["apm.trace"], _isolated(ctx=_ctx([_tx(1), _tx(2)])),
               view_args={"apm.trace": {"ref": 2}})
    args = gw.named("apm_transaction_trace")[-1]
    assert args["guid"] == "g-2" and args["around_ms"] == T0 + 2000, "참조 행 시각 ±5분(게이트웨이)"
    assert "lookback_minutes" not in args
    await _run(["apm.trace"], _isolated(ctx=_ctx([_tx(1), _tx(2)]), time_range={
        "start": "2026-10-02 08:00", "end": "2026-10-02 09:00"}),
        view_args={"apm.trace": {"ref": 2}})
    args = gw.named("apm_transaction_trace")[-1]
    assert args["lookback_minutes"] == 60 and "around_ms" not in args, "사용자 기간이 앞선다"


@pytest.mark.asyncio
async def test_trace_narrows_only_by_hosts_the_user_named(gateway) -> None:
    gw = _trace_gateway(gateway)
    await _run(["apm.trace"], _isolated("web01", "web02"),
               view_args={"apm.trace": {"guid": "g-9"}})
    assert [a.get("hostname") for a in gw.named("apm_transaction_trace")] == ["web01", "web02"]
    gw2 = _trace_gateway(gateway)
    await _run(["apm.trace"], _isolated("web01", is_composite=True),
               view_args={"apm.trace": {"guid": "g-9"}})
    assert "hostname" not in gw2.named("apm_transaction_trace")[0], \
        "복합 계획의 파서 식별자는 다른 task 몫일 수 있다"
    gw3 = _trace_gateway(gateway)
    await _run(["apm.trace"], _isolated(prior_result_rows={"t0": [_tx(3)]},
                                        prior_rows={"t0": [{"hostname": "web01"}]}),
               view_args={"apm.trace": {"ref": 1}}, input_from=["t0"])
    (args,) = gw3.named("apm_transaction_trace")
    assert args["guid"] == "g-3" and "hostname" not in args, "선행 행은 참조 원천 — 좁힘 대상 아님"


@pytest.mark.asyncio
async def test_trace_line_goes_into_the_deterministic_block(gateway) -> None:
    _trace_gateway(gateway, guid="g-2", transactions=3, domains_queried=5, domains_with_hits=2,
                   domains_failed=1, first_start_ms=T0, last_end_ms=T0 + 1500,
                   instances=["web01-was1", "api02-was1"])
    res = await _run(["apm.trace"], _isolated(), view_args={"apm.trace": {"guid": "g-2"}})
    (line,) = [x for x in res["answer_lines"] if x.startswith("GUID")]
    assert line.startswith("GUID g-2: 거래 3건 · 도메인 2곳(조회 5곳 · 실패 1곳) · ")
    assert " ~ " in line and line.endswith("· 인스턴스 web01-was1, api02-was1")
    assert "판정·집계: GUID g-2" in res["organized_data"]["summary"]


# ── 4. 2턴 종단(2단 배선 · 체크포인터 델타 병합 모사 · context_resolver 실함수) ───────────

def _app_config() -> AppConfig:
    return AppConfig(
        _env_file=None, llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


def _merge(state: dict, update: dict) -> dict:
    """LangGraph 채널 병합 모사 — `messages`만 누적(add_messages) · 나머지는 마지막 값."""
    out = {**state, **{k: v for k, v in update.items() if k != "messages"}}
    out["messages"] = list(state.get("messages") or []) + list(update.get("messages") or [])
    return out


async def _turn(state: dict, query: str, plan: dict, config: AppConfig,
                hosts: tuple[str, ...] = ()) -> dict:
    llm = FakeListChatModel(responses=[json.dumps(plan, ensure_ascii=False)])
    decomposed = await ip._llm_decompose(llm, query, config)
    state = _merge(state, {"parsed_requirements": {
        "filter_conditions": [{"field": "hostname", "op": "=", "value": h} for h in hosts],
        "original_query": query}, "task_plan": decomposed["tasks"]})
    state = _merge(state, await agent_orchestrator(
        state, llm=FakeListChatModel(responses=["unused"]), app_config=config))
    out = await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 4),
                                  app_config=config, synthesize=True, composite_answer="steps")
    return _merge(state, out)


async def _next_turn(state: dict, query: str) -> dict:
    """후속 턴 입력(라우트 `create_followup_input` — 요청 스코프만 비운다) → context_resolver."""
    state = _merge(state, create_followup_input(query))
    return _merge(state, await cr.context_resolver(state))


@pytest.mark.asyncio
async def test_two_turn_reference_survives_the_real_state_shape(gateway) -> None:
    slow = [{k: v for k, v in _tx(i).items() if k != "hostname"} for i in (1, 2, 3)]
    gw = gateway({"apm_slow_transactions": _env("apm_slow_transactions", slow),
                  "apm_transaction_profile": _env("apm_transaction_profile", [_PROFILE_ROW])})
    config = _app_config()
    q1, q2, q3 = "web01 느린 트랜잭션", "두 번째 트랜잭션 프로파일 보여줘", "세 번째도"
    state: dict = create_initial_state(user_query=q1, thread_id="th-9", user_id="alice",
                                       user_role="user", allowed_sources=None)
    state = await _turn(state, q1, {"tasks": [{"task_id": "t1", "agent": "apm_query",
                                               "sub_query": q1, "views": ["apm.slow_tx"]}]},
                        config, hosts=("web01",))
    assert len(state["query_results"]) == 3, "집계기가 행을 top-level 로 올린다"

    state = await _next_turn(state, q2)
    refs = state["conversation_context"]["previous_result_refs"]
    assert refs["turn"] == 1 and len(refs["profile_ref"]) == 3, "후속 입력이 결과를 비우지 않는다"
    state = await _turn(state, q2, {"tasks": [{"task_id": "t1", "agent": "apm_query",
                                               "sub_query": q2, "views": ["apm.profile"],
                                               "view_args": {"apm.profile": {"ref": 2}}}]},
                        config)
    (args,) = gw.named("apm_transaction_profile")
    assert (args["txid"], args["hostname"], args["time_ms"]) == ("102", "web01", T0 + 2500)

    state = await _next_turn(state, q3)
    refs = state["conversation_context"]["previous_result_refs"]
    assert refs["turn"] == 1, "프로파일 상세 행에는 참조 칸이 없어 앞 목록이 이어진다"
    await _turn(state, q3, {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": q3,
                                       "views": ["apm.profile"],
                                       "view_args": {"apm.profile": {"ref": 3}}}]}, config)
    assert gw.named("apm_transaction_profile")[-1]["txid"] == "103"


@pytest.mark.asyncio
async def test_first_turn_has_no_reference_context() -> None:
    state = create_initial_state(user_query="두 번째 프로파일", thread_id="new")
    out = await cr.context_resolver(state)
    assert out["conversation_context"] is None, "다른 스레드의 참조는 구조적으로 닿지 않는다"


def test_new_reference_rows_replace_the_whole_list() -> None:
    """다른 종류의 참조 행이 새로 나오면 이전 목록을 통째로 갈아 끼운다(섞여 엉뚱한 행 선택
    방지)."""
    active = [{"source_id": "default", "txid": "-5", "hostname": "web01",
               "active_ref": {"source_id": "default", "domain_id": 1000, "txid": "-5",
                              "session_id": 7, "thread_hash": 9}}]
    prior_ctx = {"previous_result_refs": {"turn": 1, **extract_result_refs([_tx(1)])}}
    refs = cr._previous_result_refs(active, prior_ctx, 3)
    assert set(refs) == {"turn", "active_ref"} and refs["turn"] == 2
    assert cr._previous_result_refs([{"hostname": "a"}], prior_ctx, 3) == prior_ctx[
        "previous_result_refs"], "참조 칸이 없는 결과는 앞 목록을 잇는다"


# ── 5. 분해 프롬프트 — 활성 렌더만 ───────────────────────────────────────────────

def test_reference_guidance_renders_only_when_active() -> None:
    active = ip._planner_system_prompt(_cfg())
    inactive_cfg = _cfg()
    inactive_cfg.dbhub = DBHubConfig(_env_file=None, source_endpoints={})
    inactive = ip._planner_system_prompt(inactive_cfg)
    for token in ("`apm.profile`", "`apm.trace`", "`ref`(1부터)", "| was_transaction |",
                  "앞 결과의 행을 가리킴(ref)"):
        assert token in active, token
        assert token not in inactive, token
    assert "`ref` = 정수 1 이상" in active
