"""plans/125 A-3·A-5·B-1 — 제니퍼 1급 처리기 `apm_query` · 분해 `views[]` 슬롯 · 알람 교정 비간섭.

고정하는 계약:
  1. **비활성 = 바이트 불변**: 엔드포인트가 없으면(설정 대역 MagicMock 포함) 처리기 미등록 · 분해
     프롬프트·구조화 스키마 불변 · 목록 밖 담당 처리 종전 그대로.
  2. 활성이면 분해 프롬프트는 **삽입만**(담당 한 줄 + 보기 절) · `views`는 닫힌 어휘로 정제된다.
  3. 처리기는 LLM 0 — 보기 → 도구 고정 표 · 대상(hostname) · 첫 홉 삽입(`apm.instances`) ·
     창 자르기·창 밖 조회 안 함 · 실패 사유 · 세션 재사용 · thread_id 전파 · 폴스타로 대신하지
     않음.
  4. B-1: 알람 결정적 교정은 `apm_query`(WAS 이벤트)를 건드리지 않는다(분해·재계획).
게이트웨이는 모의 MCP 세션(봉투는 `apm_gateway` 도구 계약 모양 — SPEC-apm-gateway §3)이다.
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.config import DBHubConfig
from src.orchestration import apm_query as aq
from src.orchestration.investigation_audit import _apm_query_fields
from src.orchestration.replanner import _validate_replanned_tasks
from src.orchestration.schemas import DecomposedPlan, views_plan_model
from src.orchestration.subagents import SUBAGENT_REGISTRY, resolve_subagent

# 패키지 `__init__`이 같은 이름의 함수를 다시 내보내 `from … import 모듈`이 함수를 가리킨다
ip = importlib.import_module("src.orchestration.intent_planner")

NOW = datetime(2026, 9, 30, 10, 0, 0)
BASE_AGENTS = {"data_query", "process_query", "alarm_query", "cache_management",
               "synonym_registration", "general_inference", "host_inspect"}


def _cfg(active: bool = True, **composite) -> SimpleNamespace:
    dbhub = DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if active else {},
                        source_tokens='{"apm": "gw-token"}')
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=composite.get("max_targets", 10),
                                  fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra) -> dict:
    """게이트웨이 정상 봉투(`ApmTools.ok`) 모양."""
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-09-30T10:00:00+09:00",
            "source_kind": "apm_api", "source": "apm", "tool": tool, "limits": [], **extra}


class _Gateway:
    """모의 게이트웨이 세션 — 도구별 응답(호스트별 가능)과 호출 기록."""

    def __init__(self, replies: dict):
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            self.headers = headers
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        reply = self.replies[name]
        if callable(reply):
            reply = reply(arguments)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _isolated(filters=None, time_range=None, **extra) -> dict:
    return {"parsed_requirements": {"filter_conditions": filters or [], "time_range": time_range},
            "conversation_context": {}, "thread_id": "th-1", **extra}


def _host(value: str) -> dict:
    return {"field": "hostname", "op": "=", "value": value}


# ── 1. 비활성 = 바이트 불변 ──────────────────────────────────────────────────

def test_inactive_registers_nothing_and_keeps_prompt_bytes() -> None:
    # 고정 처리기 목록은 그대로다(처리기 계약 테스트 보호)
    assert set(SUBAGENT_REGISTRY) == BASE_AGENTS
    for cfg in (_cfg(active=False), MagicMock()):
        assert aq.active_extra_subagents(cfg) == {}
        assert resolve_subagent("apm_query", cfg) is None
        assert ip._nonsql_agents(cfg) == ()
    inactive = ip._planner_system_prompt(_cfg(active=False))
    assert "apm_query" not in inactive and "apm." not in inactive


def test_active_prompt_is_insertion_only() -> None:
    inactive = ip._planner_system_prompt(_cfg(active=False))
    active = ip._planner_system_prompt(_cfg(active=True))
    agent_line = aq.render_agent_line()
    assert agent_line in active and "## WAS·미들웨어(APM) 조회" in active
    section_start = active.index("## WAS·미들웨어(APM) 조회")
    section_end = active.index("## 작업 분해 규칙\n")
    stripped = (active[:section_start] + active[section_end:]).replace("\n" + agent_line, "", 1)
    assert stripped == inactive, "기존 줄은 한 글자도 바뀌지 않는다"
    assert "`apm.events`" in active and "`apm.app_health`" in active


def test_views_schema_only_when_active() -> None:
    model = views_plan_model(DecomposedPlan, ("apm_query",))
    plan = model.model_validate({"tasks": [
        {"task_id": "t1", "agent": "apm_query", "sub_query": "x", "views": ["apm.events"]}]})
    assert plan.tasks[0].views == ["apm.events"]
    assert "views" not in DecomposedPlan.model_json_schema()["$defs"]["TaskSpec"]["properties"]
    with pytest.raises(ValueError):
        DecomposedPlan.model_validate({"tasks": [
            {"task_id": "t1", "agent": "apm_query", "sub_query": "x"}]})


class _JsonLLM:
    def __init__(self, payload: dict):
        self.payload = payload

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


@pytest.mark.asyncio
async def test_json_decompose_keeps_apm_task_only_when_active() -> None:
    payload = {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "sub_query": "web01 WAS 이벤트",
         "views": ["apm.events", "bogus.view", "apm.events"]},
        {"task_id": "t2", "agent": "data_query", "sub_query": "web01 CPU", "views": ["apm.pool"]},
    ]}
    active = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=True))
    t1, t2 = active["tasks"]
    assert t1["agent"] == "apm_query" and t1["views"] == ["apm.events"], "닫힌 어휘 · 중복 제거"
    assert "views" not in t2, "다른 담당의 views 는 떼어 낸다"

    inactive = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=False))
    assert inactive["tasks"][0]["agent"] == "general_inference", "비활성은 종전 폴백 그대로"
    assert "views" not in inactive["tasks"][0] and "views" not in inactive["tasks"][1]


# ── 4. B-1 알람 교정 비간섭 ──────────────────────────────────────────────────

def test_alarm_coercion_leaves_apm_events_alone() -> None:
    tasks = [{"task_id": "t1", "agent": "apm_query", "sub_query": "제니퍼 fatal 이벤트",
              "views": ["apm.events"]},
             {"task_id": "t2", "agent": "data_query", "sub_query": "최근 이벤트 발생 서버"}]
    coerced = ip._coerce_alarm_intent([dict(t) for t in tasks])
    assert coerced[0]["agent"] == "apm_query", "WAS 이벤트는 폴스타 알람으로 뒤집지 않는다"
    assert coerced[1]["agent"] == "alarm_query", "폴스타 알람 교정은 현행 그대로"
    kept, _notes = _validate_replanned_tasks([dict(tasks[0])], [], {}, _cfg(active=True))
    assert kept[0]["agent"] == "apm_query", "재계획 경로도 같다(활성일 때 어휘 안)"


# ── 3. 처리기 ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_named_hosts_fan_out_with_one_session(gateway) -> None:
    gw = gateway({"apm_app_health": lambda a: _env(
        "apm_app_health", [{"instance_id": 11, "tps": 3.5}],
        instance_resolution={"matched": True, "confidence": "high", "reason": "hostName",
                             "instances": [11]},
        window={"start": "x", "end": "y", "minutes": 10})})
    res = await aq.run_apm_query(
        {"task_id": "t1", "agent": "apm_query", "views": []},
        _isolated([_host("web01"), _host("web02")]), llm=None, app_config=_cfg(), now=NOW)
    assert gw.opened == 1 and gw.headers == {"Authorization": "Bearer gw-token"}
    assert [c[0] for c in gw.calls] == ["apm_app_health", "apm_app_health"], "기본 보기"
    assert {c[1]["hostname"] for c in gw.calls} == {"web01", "web02"}
    assert all(c[1]["thread_id"] == "th-1" for c in gw.calls), "thread_id 전파(감사 연결)"
    rows = res["organized_data"]["rows"]
    assert [r["hostname"] for r in rows] == ["web01", "web02"] and rows[0]["tps"] == 3.5
    meta = res["apm_query"]
    assert meta["provenance"][0]["confidence"] == "high"
    assert res["source_status"][0]["status"] == "ok"
    assert "대상 2대 · 2행" in res["organized_data"]["summary"]


@pytest.mark.asyncio
async def test_no_target_inserts_instances_first_hop_and_caps(gateway) -> None:
    inventory = [{"instance_id": i, "hostname": f"was{i:02d}", "match_confidence": "high"}
                 for i in range(1, 6)] + [{"instance_id": 99, "hostname": None,
                                            "match_confidence": None}]
    gw = gateway({"apm_instance_map": _env("apm_instance_map", inventory),
                  "apm_app_health": lambda a: _env("apm_app_health", [{"tps": 1}])})
    res = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query",
                                  "views": ["apm.app_health"]},
                                 _isolated(), llm=None, app_config=_cfg(max_targets=3), now=NOW)
    assert gw.calls[0] == ("apm_instance_map", {"thread_id": "th-1"}), "첫 홉 삽입(LLM 0)"
    assert [c[1]["hostname"] for c in gw.calls[1:]] == ["was01", "was02", "was03"]
    step = res["apm_query"]["inserted_steps"][0]
    assert step == {"view": "apm.instances", "reason": step["reason"], "hosts": 5, "truncated": 2}
    assert "조회한 범위 안의 결과" in res["organized_data"]["summary"]


@pytest.mark.asyncio
async def test_out_of_window_is_not_queried_and_not_substituted(gateway) -> None:
    gw = gateway({"apm_app_health": _env("apm_app_health", [])})
    res = await aq.run_apm_query(
        {"task_id": "t1", "agent": "apm_query", "views": ["apm.app_health"]},
        _isolated([_host("web01")], {"start": "2026-09-20", "end": "2026-09-26"}),
        llm=None, app_config=_cfg(), now=NOW)
    assert gw.calls == [], "창 밖은 조회하지 않는다"
    assert res["degraded_reason"] == "apm_not_queried"
    assert "조회 창" in res["final_response"] and res["source_status"][0]["status"] == "not_queried"


def test_window_trim_and_current_value_views() -> None:
    views = {v.id: v for v in aq.apm_views()}
    today = aq.plan_window(views["apm.app_health"], {"start": "2026-09-30", "end": "2026-09-30"},
                           NOW)
    assert today.mode == "window" and today.args == {"reference_time": None,
                                                    "lookback_minutes": 10}
    assert "마지막 10분만 조회" in today.note
    short = aq.plan_window(views["apm.events"],
                           {"start": "2026-09-30 09:00", "end": "2026-09-30 09:30"}, NOW)
    assert short.args == {"reference_time": "2026-09-30T09:30:00", "lookback_minutes": 30}
    assert aq.plan_window(views["apm.pool"], None, NOW).mode == "current"
    assert "현재값 기준" in aq.plan_window(views["apm.pool"],
                                          {"start": "2026-09-30", "end": "2026-09-30"}, NOW).note


@pytest.mark.asyncio
async def test_partial_failure_and_gateway_error_envelope(gateway) -> None:
    def health(args):
        if args["hostname"] == "web09":
            return {"error": "instance_unresolved", "reason": "hostName 불일치", "tool": "x"}
        return _env("apm_app_health", [{"tps": 2}])

    gateway({"apm_app_health": health})
    res = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": []},
                                 _isolated([_host("web01"), _host("web09")]),
                                 llm=None, app_config=_cfg(), now=NOW)
    assert res["source_status"][0]["status"] == "partial"
    assert "web09(instance_unresolved" in res["organized_data"]["summary"]
    fields = _apm_query_fields(res)
    assert fields["outcome"] == "partial" and fields["commands"] == [
        "apm_app_health(hostname=web01)"]


@pytest.mark.asyncio
async def test_unreachable_gateway_and_missing_endpoint(gateway, monkeypatch) -> None:
    @asynccontextmanager
    async def refused(url, headers):
        raise ConnectionRefusedError("x")
        yield  # pragma: no cover

    monkeypatch.setattr(aq, "_SESSION_FACTORY", refused)
    res = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query"},
                                 _isolated([_host("web01")]), llm=None, app_config=_cfg(),
                                 now=NOW)
    assert res["degraded_reason"] == "source_unavailable"
    assert "다른 소스의 값으로 대신 답하지 않았습니다" in res["final_response"]
    off = await aq.run_apm_query({"task_id": "t1", "agent": "apm_query"},
                                 _isolated([_host("web01")]), llm=None,
                                 app_config=_cfg(active=False), now=NOW)
    assert off["source_status"][0]["reason"] == "엔드포인트 미설정"


def test_active_dispatch_resolves_the_conditional_handler() -> None:
    spec = resolve_subagent("apm_query", _cfg(active=True))
    assert spec is aq.APM_QUERY_SPEC and spec.backend == "mcp"
    assert spec.key_facets_out == ("hostname", "apm_instance_id", "apm_domain_id")
    assert resolve_subagent("data_query", _cfg(active=True)) is SUBAGENT_REGISTRY["data_query"]
