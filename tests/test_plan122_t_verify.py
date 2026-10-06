"""plans/122 트랙 T(시간 표현 정규화 · D-306) 독립 검증 — 실제 `build_graph()` 종단 회귀.

기존 트랙 T 단위 테스트는 대부분 완전한 state(`time_resolution` 포함)를 노드·함수에 직접 넣는다.
이 파일은 **실제 그래프**(체크포인터 포함)를 가짜 LLM·가짜 DB로 돌려, 값이 LangGraph 채널을
지나 각 소비처에 닿는지(D-186 실수 유형)를 본다.

고정하는 계약:
  ① 종단 대칭 — 사다리 legacy·3단(semantic_router)·2단(intent_orchestration) × 단일·멀티 DB에서
     state `time_resolution` · LLM SQL 생성 프롬프트의 기간 블록 리터럴 · 실행 SQL 리터럴이
     같은 `[start, end)`다. 알람 결정적 조립(`alarm_deterministic`)의 `ctime` 리터럴도 같다
  ② 플래그 off — state `time_resolution` 없음 · 시스템 기간 블록 없음
  ③ 기간 되묻기 — 실제 그래프를 실제 라우트(비스트림·스트림)에 꽂으면 status/`clarification`
     (kind `time_period`)으로 끝나고 SQL 생성·실행이 0회다
  ④ 요청 스코프 — 후속 턴은 직전 턴 해석을 물려받지 않고 자기 원문으로 다시 해석한다
  ⑤ verify-122t가 찾은 결함 재현(D1~D5·D8) — 2026-10-06 교정 뒤 일반 회귀 테스트로 전환

기준 시각 2026-09-29(화) 10:00 KST(`src.nodes.input_parser._now` 고정). 실 LLM·DB·네트워크 0.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
DB_SINGLE = ["polestar"]
DB_MULTI = ["polestar_cm_gp", "polestar_cm_yd"]

# ──────────────────────────────────────────────
# 최소 폴스타 스키마(검증기 컬럼 존재 검사를 통과할 만큼)
# ──────────────────────────────────────────────


def _cols(*names: str) -> list[dict[str, Any]]:
    return [{"name": n, "type": "character varying", "nullable": True} for n in names]


def _schema() -> dict[str, Any]:
    stat = _cols("resource_id", "definition_name", "stat_date", "avg_val", "max_val", "min_val")
    return {
        "tables": {
            "polestar.cmm_resource": {"columns": _cols(
                "id", "name", "hostname", "ipaddress", "dtime", "resource_type",
                "platform_resource_id", "service_resource_id", "resource_conf_id",
            )},
            "polestar.core_config_prop": {"columns": _cols(
                "configuration_id", "name", "stringvalue_short",
            )},
            "polestar.cmm_metric_stat_h": {"columns": list(stat)},
            "polestar.cmm_metric_stat_d": {"columns": list(stat)},
            "polestar.cmm_metric_stat_m": {"columns": list(stat)},
            "polestar.cmm_alarm": {"columns": _cols(
                "id", "ctime", "alarmseverity", "resource_id", "definition_id",
                "conditionlogtext", "currentalarmstatus", "ackuserid",
            )},
            "polestar.cmm_alarm_def": {"columns": _cols("id", "name")},
            "polestar.cmm_alarm_active": {"columns": _cols(
                "alarm_id", "ctime", "alarmseverity", "resource_id", "definition_id",
            )},
        },
        "relationships": [],
    }


# ──────────────────────────────────────────────
# 가짜 LLM — 프롬프트 종류별 응답(순종 LLM: 기간 블록의 조건을 그대로 쓴다)
# ──────────────────────────────────────────────

_PERIOD_HEAD = "## 기간 조건 (시스템"


def _block(human: str) -> str:
    return next((p for p in human.split("\n\n") if p.startswith(_PERIOD_HEAD)), "")


def _sql_for(human: str) -> str:
    m = re.search(r'"original_query":\s*"([^"]*)"', human)
    query = m.group(1) if m else ""
    blk = _block(human)
    table = re.search(r"`(cmm_metric_stat_[hdm])`", blk)
    cond = re.search(r"`(s\.stat_date [^`]+)`", blk)
    alarm = re.search(r"`(a\.ctime [^`]+)`", blk)
    if "알람" in query:
        where = "r.dtime IS NULL" + (f" AND {alarm.group(1)}" if alarm else "")
        return (
            "```sql\nSELECT a.id, a.ctime, r.hostname FROM polestar.cmm_alarm a "
            f"JOIN polestar.cmm_resource r ON r.id = a.resource_id WHERE {where} "
            "ORDER BY a.ctime DESC LIMIT 100\n```"
        )
    if not re.search(r"CPU|메모리|사용률|평균", query):
        return (
            "```sql\nSELECT r.hostname FROM polestar.cmm_resource r "
            "WHERE r.dtime IS NULL LIMIT 1000\n```"
        )
    tbl = table.group(1) if table else "cmm_metric_stat_h"
    parts = ["r.dtime IS NULL", "s.definition_name = 'Utilization'"]
    if cond:
        parts.append(cond.group(1))
    return (
        "```sql\nSELECT r.hostname, AVG(s.avg_val) AS avg_cpu FROM polestar.cmm_resource r "
        f"JOIN polestar.{tbl} s ON s.resource_id = r.id WHERE {' AND '.join(parts)} "
        "GROUP BY r.hostname ORDER BY avg_cpu DESC LIMIT 10\n```"
    )


class _FakeLLM:
    """프롬프트 종류(입력 파싱·SQL 생성·기타)만 가려 고정 응답을 낸다. 호출을 기록한다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []
        self.query = ""

    def _respond(self, messages: Any) -> AIMessage:
        from src.prompts.input_parser import INPUT_PARSER_SYSTEM_PROMPT

        if isinstance(messages, str):
            messages = [HumanMessage(content=messages)]
        system = "\n".join(
            str(m.content) for m in messages if type(m).__name__ == "SystemMessage"
        )
        human = "\n".join(
            str(m.content) for m in messages if type(m).__name__ == "HumanMessage"
        )
        if system.startswith(INPUT_PARSER_SYSTEM_PROMPT[:60]):
            kind = "input_parser"
            content = json.dumps({
                "query_targets": ["알람"] if "알람" in self.query else ["서버", "CPU"],
                "filter_conditions": [], "time_range": None, "output_format": "text",
                "aggregation": None, "limit": None,
            }, ensure_ascii=False)
        elif "## 파싱된 요구사항" in human:
            kind, content = "sql", _sql_for(human)
        else:
            kind, content = "other", "조회 결과를 정리했습니다."
        self.calls.append((kind, system, human))
        return AIMessage(content=content)

    async def ainvoke(self, messages: Any, *a: Any, **k: Any) -> AIMessage:
        return self._respond(messages)

    def invoke(self, messages: Any, *a: Any, **k: Any) -> AIMessage:
        return self._respond(messages)

    async def astream(self, messages: Any, *a: Any, **k: Any):  # noqa: ANN201
        yield self._respond(messages)

    def with_config(self, *a: Any, **k: Any) -> _FakeLLM:
        return self

    def bind(self, *a: Any, **k: Any) -> _FakeLLM:
        return self

    def sql_humans(self) -> list[str]:
        return [h for kind, _s, h in self.calls if kind == "sql"]


class _Client:
    def __init__(self, db_id: str | None, sink: list[tuple[str | None, str]]) -> None:
        self.db_id, self.sink = db_id, sink

    async def execute_sql(self, sql: str):  # noqa: ANN201
        from src.dbhub.models import QueryResult

        self.sink.append((self.db_id, sql))
        rows = [{"hostname": "webdb01", "avg_cpu": 81.5}]
        return QueryResult(columns=list(rows[0]), rows=rows, row_count=len(rows))

    async def get_sample_data(self, table: str, limit: int = 3) -> list:
        return []


class _Cache:
    redis_available = False

    async def get_schema_or_fetch(self, client: Any, db_id: str):  # noqa: ANN201
        return _schema(), True, "메모리", {"polestar.cmm_resource.hostname": "호스트명"}, {}

    async def get_applied_structure_meta(self, db_id: str) -> None:
        return None

    async def get_synonyms(self, db_id: str) -> dict:
        return {}

    def __getattr__(self, name: str):  # noqa: ANN204
        async def _noop(*a: Any, **k: Any) -> None:
            return None

        return _noop


class _Env:
    def __init__(self, compiled: Any, llm: _FakeLLM, executed: list, visits: list) -> None:
        self.compiled, self.llm, self.executed, self.visits = compiled, llm, executed, visits

    async def run(self, query: str, thread: str, *, followup: bool = False,
                  extra: dict | None = None, active_db: str | None = None) -> dict:
        from src.state import create_followup_input, create_initial_state

        self.llm.calls.clear()
        self.llm.query = query
        self.executed.clear()
        self.visits.clear()
        inp = (create_followup_input(user_query=query) if followup
               else create_initial_state(user_query=query, thread_id=thread))
        if active_db and not followup:
            inp["active_db_id"] = active_db
        inp.update(extra or {})
        return await self.compiled.ainvoke(inp, config={"configurable": {"thread_id": thread}})


def _intent(query: str) -> str:
    return "alarm_query" if "알람" in query else "data_query"


@pytest.fixture
def build_env(monkeypatch: pytest.MonkeyPatch):
    """실제 그래프 빌더 — 시간 해석과 무관한 노드(라우터·계획·재계획)만 대역으로 바꾼다."""

    def _build(tier: str, db_ids: list[str], *, flag: bool = True,
               alarm_det: bool = False) -> tuple[_Env, Any]:
        from langgraph.checkpoint.memory import MemorySaver

        import src.db
        import src.graph as graph_module
        import src.llm
        import src.schema_cache.cache_manager as cm
        from src.config import AppConfig
        from src.routing.db_registry import DBRegistry

        llm, executed, visits = _FakeLLM(), [], []

        @asynccontextmanager
        async def _db(config: Any = None, *, db_id: str | None = None, **kw: Any):
            yield _Client(db_id, executed)

        @asynccontextmanager
        async def _reg_client(self: Any, db_id: str):
            yield _Client(db_id, executed)

        cache = _Cache()
        for name, mod in list(sys.modules.items()):
            if not name.startswith("src") or mod is None:
                continue
            if name != "src.llm" and getattr(mod, "create_llm", None) is not None:
                monkeypatch.setattr(mod, "create_llm", lambda *a, **k: llm)
            if name != "src.db" and getattr(mod, "get_db_client", None) is not None:
                monkeypatch.setattr(mod, "get_db_client", _db)
            if name != "src.schema_cache.cache_manager" and getattr(
                mod, "get_cache_manager", None
            ) is not None:
                monkeypatch.setattr(mod, "get_cache_manager", lambda *a, **k: cache)
        monkeypatch.setattr(src.llm, "create_llm", lambda *a, **k: llm)
        monkeypatch.setattr(src.db, "get_db_client", _db)
        monkeypatch.setattr(cm, "get_cache_manager", lambda *a, **k: cache)
        monkeypatch.setattr(DBRegistry, "get_client", _reg_client)
        monkeypatch.setattr(importlib.import_module("src.nodes.input_parser"), "_now", lambda: NOW)

        async def _router(state: Any, **kw: Any) -> dict:
            q = state["user_query"]
            targets = [{"db_id": d, "relevance_score": 0.9, "sub_query_context": q}
                       for d in db_ids]
            return {"target_databases": targets, "is_multi_db": len(targets) > 1,
                    "active_db_id": targets[0]["db_id"], "user_specified_db": None,
                    "routing_intent": _intent(q), "current_node": "semantic_router"}

        async def _planner(state: Any, **kw: Any) -> dict:
            q = state["user_query"]
            return {"task_plan": [{
                "task_id": "t1", "agent": _intent(q), "sub_query": q, "depends_on": [],
                "input_from": [], "order": 1, "status": "pending", "db_ids": list(db_ids),
            }], "is_composite": False, "current_node": "intent_planner"}

        async def _replanner(state: Any, **kw: Any) -> dict:
            return {"needs_replan": False, "current_node": "replanner"}

        real_fm = graph_module.field_mapper

        async def _field_mapper(state: Any, **kw: Any) -> dict:
            visits.append("field_mapper")
            return await real_fm(state, **kw)

        monkeypatch.setattr(
            graph_module, "select_orchestration_backend", lambda c: "semantic_router"
        )
        monkeypatch.setattr(graph_module, "semantic_router", _router)
        monkeypatch.setattr(graph_module, "intent_planner", _planner)
        monkeypatch.setattr(graph_module, "replanner", _replanner)
        monkeypatch.setattr(graph_module, "field_mapper", _field_mapper)

        cfg = AppConfig()
        cfg.enable_deepagents_package = False
        cfg.enable_sql_approval = False
        cfg.tier3_plan_loop_enabled = False
        cfg.cross_system_probe_enabled = False
        cfg.enable_intent_orchestration = tier == "t2"
        cfg.enable_semantic_routing = tier in ("t2", "t3")
        cfg.structured_output_backend = "none"
        cfg.multi_db.active_db_ids_csv = "polestar,polestar_cm_gp,polestar_cm_yd"
        cfg.polestar_db_ids = "polestar_b0,polestar_cm_gp,polestar_cm_yd,polestar"
        cfg.query.time_resolution_enabled = flag
        cfg.query.max_retry_count = 3
        cfg.observability.trace_enabled = False
        cfg.composite.sequential_fallback_tiers_enabled = False
        t2s = cfg.text2sql
        t2s.alarm_deterministic = alarm_det
        t2s.semantic_compose = False
        t2s.multi_candidate = False
        t2s.stepwise_derivation = False
        t2s.query_history_fewshot = False
        t2s.multi_full_validation = False
        t2s.generic_llm_mapping = False
        compiled = graph_module.build_graph(cfg, checkpointer=MemorySaver())
        return _Env(compiled, llm, executed, visits), cfg

    return _build


TIER_DBS = [("legacy", DB_SINGLE), ("t3", DB_SINGLE), ("t3", DB_MULTI),
            ("t2", DB_SINGLE), ("t2", DB_MULTI)]
TIER_IDS = ["legacy-single", "t3-single", "t3-multi", "t2-single", "t2-multi"]

#: 질의 → (metric 입도, 통계 lo, 통계 hi, 알람 ctime 시작, 알람 ctime 끝)
EXPECTED = {
    "최근 30일 CPU 사용률 상위 5대": ("day", "20260830", "20260929",
                             "2026-08-30 00:00:00", "2026-09-29 00:00:00"),
    "어제 알람 목록": ("day", "20260928", "20260929",
                 "2026-09-28 00:00:00", "2026-09-29 00:00:00"),
    "이번 달 CPU 평균": ("day", "20260901", "20260929",
                   "2026-09-01 00:00:00", "2026-09-29 10:00:00"),
    "CPU 사용률 상위 10대": ("month", "202608", "202609", None, None),
}

_STAT_RE = re.compile(r"s\.stat_date >= '(\d+)' AND s\.stat_date < '(\d+)'")
_TS_RE = re.compile(r"ctime >= TIMESTAMP '([^']+)' AND \w+\.ctime < TIMESTAMP '([^']+)'")


def _tr(state: dict) -> QueryTime:
    qt = QueryTime.from_state(state.get("time_resolution"))
    assert qt is not None, state.get("time_resolution")
    return qt


# ──────────────────────────────────────────────
# ① 종단 대칭
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("tier", "db_ids"), TIER_DBS, ids=TIER_IDS)
@pytest.mark.parametrize("query", list(EXPECTED))
async def test_period_literals_same_in_state_prompt_and_executed_sql(
    build_env, tier, db_ids, query,
):
    if tier == "legacy" and "알람" in query:
        pytest.skip("legacy는 라우팅 의도가 없어 알람 템플릿·허용 테이블을 쓰지 않는다(종전 동일)")
    env, _ = build_env(tier, db_ids)
    out = await env.run(query, f"sym-{tier}-{len(db_ids)}",
                        active_db=db_ids[0] if tier == "legacy" else None)
    grain, lo, hi, ts0, ts1 = EXPECTED[query]
    qt = _tr(out)
    assert qt.anchor_at == NOW and qt.metric is not None and qt.metric.grain == grain

    humans = env.llm.sql_humans()
    assert humans, "LLM SQL 생성이 불리지 않았다"
    blk = _block(humans[0])
    assert _STAT_RE.search(blk).groups() == (lo, hi)  # type: ignore[union-attr]
    if ts0 is not None:
        assert _TS_RE.search(blk.replace("a.ctime < ", "a.ctime < ")).groups() == (ts0, ts1)  # type: ignore[union-attr]

    assert len(env.executed) == len(db_ids)
    for _db, sql in env.executed:
        if "알람" in query:
            assert _TS_RE.search(sql).groups() == (ts0, ts1)  # type: ignore[union-attr]
        else:
            assert _STAT_RE.search(sql).groups() == (lo, hi)  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tier", "db_ids"), TIER_DBS[1:], ids=TIER_IDS[1:])
async def test_deterministic_alarm_literal_matches_prompt_block(build_env, tier, db_ids):
    """알람 결정적 조립(`alarm_deterministic` on)의 ctime 리터럴 = 프롬프트 블록 알람 줄.

    LLM SQL 생성은 0회다.
    """
    env, _ = build_env(tier, db_ids, alarm_det=True)
    out = await env.run("어제 알람 목록", f"det-{tier}-{len(db_ids)}")
    assert env.llm.sql_humans() == []
    assert len(env.executed) == len(db_ids)
    from src.db_adapters.polestar.time_period import build_period_block

    blk = build_period_block(_tr(out))
    expect = _TS_RE.search(blk).groups()  # type: ignore[union-attr]
    assert expect == EXPECTED["어제 알람 목록"][3:]
    for _db, sql in env.executed:
        assert _TS_RE.search(sql).groups() == expect  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tier", "db_ids"), TIER_DBS, ids=TIER_IDS)
async def test_present_query_gets_no_period_block(build_env, tier, db_ids):
    env, _ = build_env(tier, db_ids)
    out = await env.run("현재 CPU 사용률", f"now-{tier}-{len(db_ids)}",
                        active_db=db_ids[0] if tier == "legacy" else None)
    qt = _tr(out)
    assert qt.present and not qt.uses_default_period
    assert all(_block(h) == "" for h in env.llm.sql_humans())
    assert "[조회 기간]" not in (out.get("final_response") or "")


# ──────────────────────────────────────────────
# ② 플래그 off
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("tier", "db_ids"), TIER_DBS, ids=TIER_IDS)
async def test_flag_off_no_resolution_no_system_block(build_env, tier, db_ids):
    env, _ = build_env(tier, db_ids, flag=False)
    out = await env.run("최근 30일 CPU 사용률 상위 5대", f"off-{tier}-{len(db_ids)}",
                        active_db=db_ids[0] if tier == "legacy" else None)
    assert out.get("time_resolution") is None
    assert all(_PERIOD_HEAD not in h for h in env.llm.sql_humans())
    assert "[조회 기간]" not in (out.get("final_response") or "")


# ──────────────────────────────────────────────
# ③ 기간 되묻기 — 실제 그래프 + 실제 라우트
# ──────────────────────────────────────────────


def _client(env: _Env, cfg: Any):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes

    cfg.server.sse_progress_events = True
    app = FastAPI()
    app.state.config = cfg
    app.state.graph = env.compiled
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _post(client: Any, env: _Env, route: str, query: str, thread_id: str | None = None) -> dict:
    env.llm.calls.clear()
    env.llm.query = query
    env.executed.clear()
    body: dict[str, Any] = {"query": query}
    if thread_id:
        body["thread_id"] = thread_id
    if route == "text":
        r = client.post("/api/v1/query", json=body)
        assert r.status_code == 200, r.text
        return r.json()
    r = client.post("/api/v1/query/stream", json=body)
    assert r.status_code == 200, r.text
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, [e.get("type") for e in events]
    return done[0]


@pytest.mark.parametrize("tier", ["t3", "t2"])
@pytest.mark.parametrize("route", ["text", "stream"])
@pytest.mark.parametrize(("query", "reason"), [
    ("13월 CPU 사용률", "invalid_date"),
    ("2027년 3월 알람", "future_explicit"),
    ("내년 3월 CPU", "future_relative"),
    ("3일 전 CPU", "unresolved"),
])
def test_clarify_turn_ends_before_field_mapper_via_routes(build_env, tier, route, query, reason):
    env, cfg = build_env(tier, DB_SINGLE)
    out = _post(_client(env, cfg), env, route, query)
    assert out["clarification"]["kind"] == "time_period"
    assert out["clarification"]["reason"] == reason
    assert out["time_resolution"]["clarify"] == reason
    assert out["response"].startswith("[조회 기간]")
    if route == "text":
        assert out["status"] == "clarification"
    assert env.visits == [] and env.llm.sql_humans() == [] and env.executed == []


@pytest.mark.asyncio
async def test_slot_resolves_ago_expression_through_graph(build_env, monkeypatch):
    """「3일 전」 — 규칙 미매칭이어도 LLM 슬롯이 맞으면 조회한다(되묻지 않는다)."""
    env, _ = build_env("t3", DB_SINGLE)
    slot = {"relation": "ago", "n": 3, "unit": "day", "completeness": None, "anchor": None,
            "start": None, "end": None, "display_grain": "none", "span": "3일 전"}
    orig = env.llm._respond

    def _with_slot(messages: Any) -> AIMessage:
        msg = orig(messages)
        if env.llm.calls[-1][0] == "input_parser":
            data = json.loads(str(msg.content))
            data["time_expr"] = slot
            return AIMessage(content=json.dumps(data, ensure_ascii=False))
        return msg

    monkeypatch.setattr(env.llm, "_respond", _with_slot)
    out = await env.run("3일 전 CPU", "slot-ago")
    qt = _tr(out)
    assert qt.clarify is None and qt.slot_status == "accepted"
    assert _STAT_RE.search(env.executed[0][1]).groups() == ("20260926", "20260927")  # type: ignore[union-attr]


# ──────────────────────────────────────────────
# ④ 요청 스코프 · 멀티턴
# ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("tier", ["legacy", "t3", "t2"])
async def test_followup_turn_reresolves_and_does_not_inherit(build_env, tier):
    env, _ = build_env(tier, DB_SINGLE)
    th = f"mt-{tier}"
    first = await env.run("최근 7일 CPU 상위 5대", th,
                          active_db="polestar" if tier == "legacy" else None)
    assert _STAT_RE.search(env.executed[0][1]).groups() == ("20260922", "20260929")  # type: ignore[union-attr]
    assert _tr(first).metric.grain == "day"  # type: ignore[union-attr]

    second = await env.run("그 서버들 메모리", th, followup=True)
    qt = _tr(second)
    assert qt.metric is not None and qt.metric.source == "default"
    # 직전 턴 SQL은 「이전 대화」 맥락으로 실릴 수 있다 — 이번 턴 기간 블록만 본다
    assert all("20260922" not in _block(h) for h in env.llm.sql_humans())
    assert _STAT_RE.search(env.executed[0][1]).groups() == ("202608", "202609")  # type: ignore[union-attr]

    third = await env.run("13월 CPU 사용률", th, followup=True)
    assert _tr(third).clarify == "invalid_date" and env.executed == []

    fourth = await env.run("어제 CPU 사용률", th, followup=True)
    assert _tr(fourth).clarify is None
    assert _STAT_RE.search(env.executed[0][1]).groups() == ("20260928", "20260929")  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_zone_answer_reuse_turn_reresolves_at_its_own_anchor(build_env, monkeypatch):
    """존 역질문 답변 턴(파싱 재사용) — 답변 턴의 기준 시각으로 다시 해석한다."""
    env, _ = build_env("t3", DB_SINGLE)
    await env.run("최근 7일 CPU 상위 5대", "zone-reuse")
    snap = env.compiled.get_state({"configurable": {"thread_id": "zone-reuse"}}).values
    parsed = dict(snap["parsed_requirements"])
    later = datetime(2026, 10, 1, 9, 0, tzinfo=KST)
    monkeypatch.setattr(importlib.import_module("src.nodes.input_parser"), "_now", lambda: later)
    out = await env.run("최근 7일 CPU 상위 5대", "zone-reuse", followup=True, extra={
        "reuse_parsed_requirements": parsed, "selected_db_ids": ["polestar"],
    })
    assert _tr(out).anchor_at == later
    assert _STAT_RE.search(env.executed[0][1]).groups() == ("20260924", "20261001")  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["서버 목록", "OS가 리눅스인 서버", "webdb01 서버 사양"])
async def test_no_period_notice_for_period_free_query(build_env, query):
    env, _ = build_env("t3", DB_SINGLE)
    out = await env.run(query, f"nonperiod-{query}")
    assert "[조회 기간]" not in (out.get("final_response") or "")


# ──────────────────────────────────────────────
# ⑤ 결함 재현 · verify-122t 2026-10-06 — 교정 뒤 일반 회귀 테스트(xfail 표지 제거)
# ──────────────────────────────────────────────


# verify-122t D1(교정됨 — lead-122): _TRACE_RE가 「시간」→「시」로 되짚어
# 수량 비교를 기간 흔적으로 봤다
@pytest.mark.parametrize("query", ["1시간 이상 지속된 알람", "3시간 넘게 지속된 알람",
                                   "2주일 이상 재부팅 안 한 서버", "2대 서버 3시스템 목록"])
def test_defect_duration_comparison_is_not_unresolved(query):
    assert resolve_query_time(query, NOW).clarify is None


# verify-122t D2(교정됨 — lead-122): 만료·마감 필터·빈도를 기간 흔적으로 되물었다(카탈로그 FS-12)
@pytest.mark.parametrize("query", [
    "HW 지원 종료가 6개월 안 남은 서버들의 WAS 인스턴스 목록 보여줘",
    "보증 만료가 3개월 이내인 서버",
    "30일 후 만료 서버",
    "하루 2시간씩 점검하는 서버",
])
def test_defect_deadline_filter_is_not_unresolved(query):
    assert resolve_query_time(query, NOW).clarify is None


# verify-122t D3(교정됨 — lead-122): LLM 슬롯 relation=next를 받아 미래 구간을 조회했다
def test_defect_llm_next_slot_is_future_clarify():
    slot = {"relation": "next", "n": 6, "unit": "month", "completeness": None, "anchor": None,
            "start": None, "end": None, "display_grain": "none", "span": "향후 6개월"}
    qt = resolve_query_time("향후 6개월 CPU 추이", NOW, slot=slot)
    assert qt.clarify == "future_relative"


def test_future_slot_on_filter_phrase_is_dropped_not_clarified():
    """필터 표현(흔적 아님)에 슬롯이 미래 구간을 내면 슬롯만 버리고 기본값으로 간다(D2·D3 교차)."""
    slot = {"relation": "next", "n": 30, "unit": "day", "completeness": None, "anchor": None,
            "start": None, "end": None, "display_grain": "none", "span": "30일 후"}
    qt = resolve_query_time("30일 후 만료 서버 목록", NOW, slot=slot)
    assert qt.clarify is None and qt.slot_status == "rejected"
    assert qt.slot_reason == "future_period"
    assert qt.metric is not None and qt.metric.source == "default"


@pytest.mark.parametrize("query", ["최근 5분 CPU 사용률", "최근 10분 알람"])
def test_minutes_are_clarified_not_defaulted(query):
    """verify-122t D8(교정됨): 「최근 N분」은 기간 미지정 기본값으로 조용히 가지 않고 되묻는다."""
    assert resolve_query_time(query, NOW).clarify == "unresolved"


# verify-122t D4(교정됨 — impl-notice): 「당월 1일부터 어제까지」 각주가
# 어제·최근 N시간·지난주에도 붙었다
@pytest.mark.parametrize("query", ["어제 CPU 사용률", "최근 3시간 CPU 사용률", "지난주 CPU 평균"])
def test_defect_partial_month_note_only_for_month_to_date(query):
    og = importlib.import_module("src.nodes.output_generator")
    state = {"user_query": query, "query_results": [{"cpu_avg": 41.5}],
             "time_resolution": resolve_query_time(query, NOW).to_state()}
    assert "당월 1일부터 어제까지" not in og._append_current_month_partial_note("응답", state)


# verify-122t D5(교정됨 — impl-orch): 후속 되묻기 턴 비스트림 응답에
# 직전 턴 executed_sql·row_count가 실렸다
def test_defect_clarify_followup_turn_has_no_stale_sql(build_env):
    env, cfg = build_env("t3", DB_SINGLE)
    client = _client(env, cfg)
    _post(client, env, "text", "지난달 CPU 상위 5대", thread_id="stale")
    out = _post(client, env, "text", "13월 CPU 사용률", thread_id="stale")
    assert out["status"] == "clarification"
    assert not out.get("executed_sql") and out.get("row_count") in (0, None)
