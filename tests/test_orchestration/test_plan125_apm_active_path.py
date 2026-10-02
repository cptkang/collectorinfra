"""plans/125 — APM 엔드포인트를 설정한 배포의 2단 경로 끝까지(D-285 부기).

사용자 *"향후 apm 이 설정되면 정상적으로 처리하라"*.

분해(`_llm_decompose` — 대역 LLM이 JSON 계획을 낸다) → 오케스트레이터(`apm_query` 실제 처리기 ·
폴스타 `data_query`는 대역 처리기) → 재계획기 → 집계기(2단 배선 그대로 `synthesize=True` ·
`composite_answer="steps"`) → 최종 응답. 게이트웨이는 모의 MCP 세션이다(실 게이트웨이·LLM 0).

세 경우:
  1. 권한 있는 사용자(NULL · `["apm"]` · 관리자) — 제니퍼 결과가 응답에 실린다.
  2. 권한 없는 사용자 — 게이트웨이를 열지 않고 소스 이름 없는 거부 문구가 실리며, 같은 질의의
     폴스타 부분은 정상 답이 나온다.
  3. 엔드포인트 미설정(기본 배포) — 분해 어휘에 `apm_query`가 없다(목록 밖 이름은 종전 폴백).
     바이트 불변은 `test_plan125_apm_query`(분해 프롬프트·구조화 스키마)가 고정한다.
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.orchestration import apm_query as aq
from src.orchestration import subagents
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.replanner import replanner
from src.orchestration.result_aggregator import result_aggregator
from src.routing.db_authz import SOURCE_ACCESS_DENIED_MESSAGE
from src.routing.registry import get_registry
from src.state import create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")

QUERY = "web01 서버 CPU 사용률과 WAS 응답시간 알려줘"
PLAN = {"tasks": [
    {"task_id": "t1", "agent": "data_query", "sub_query": "web01 서버 CPU 사용률"},
    {"task_id": "t2", "agent": "apm_query", "sub_query": "web01 WAS 응답시간",
     "views": ["apm.app_health"]},
]}
POLESTAR_ROWS = [{"hostname": "web01", "cpu_usage_pct": 42.5}]
APM_ROW = {"instance_id": 11, "instance_name": "web01-was1", "tps": 3.5, "avg_response_ms": 812}


def _config(*, apm: bool) -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(
            server_url="http://localhost:9099/sse", source_name="infra_db",
            source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if apm else {},
        ),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


class _Gateway:
    """모의 게이트웨이 세션 — 연 횟수와 부른 도구를 센다."""

    def __init__(self) -> None:
        self.opened = 0
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        env = {"rows": [APM_ROW], "row_count": 1, "queried_at": "2026-09-30T10:00:00+09:00",
               "source_kind": "apm_api", "source": "apm", "tool": name, "limits": [],
               "instance_resolution": {"matched": True, "confidence": "high",
                                       "reason": "hostName", "instances": [11]},
               "window": {"start": "x", "end": "y", "minutes": 10}}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)


async def _polestar_handler(task, isolated, *, llm, app_config):
    """폴스타 SQL 조회 대역 — 이 테스트의 관심은 APM 경로와 집계다."""
    return {
        "organized_data": {"summary": f"{len(POLESTAR_ROWS)}건", "rows": POLESTAR_ROWS,
                           "column_mapping": None, "resolved_mapping": None,
                           "is_sufficient": True, "sheet_mappings": None},
        "query_results": POLESTAR_ROWS,
        "target_db_ids": ["polestar_cm_gp"],
    }


@pytest.fixture
def gateway(monkeypatch) -> _Gateway:
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    spec = subagents.SUBAGENT_REGISTRY["data_query"]
    monkeypatch.setitem(subagents.SUBAGENT_REGISTRY, "data_query",
                        replace(spec, handler=_polestar_handler))
    return gw


def _llm(*responses: str) -> FakeListChatModel:
    return FakeListChatModel(responses=list(responses))


async def _turn(config: AppConfig, **user) -> dict:
    """분해 → 실행 → 재계획 판정 → 집계 한 턴."""
    plan = await ip._llm_decompose(_llm(json.dumps(PLAN, ensure_ascii=False)), QUERY, config)
    state = create_initial_state(user_query=QUERY, thread_id="th-1", **user)
    # 입력 파서 산출(그래프에서는 분해 앞 노드) — 대상 서버 식별자
    parsed = {"query_targets": ["서버", "CPU"], "filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}], "original_query": QUERY}
    state.update({"parsed_requirements": parsed, "task_plan": plan["tasks"],
                  "task_results": {}, "replan_count": 0})
    state.update(await agent_orchestrator(state, llm=_llm("unused"), app_config=config))
    no_followup = json.dumps({"needs_followup": False, "reason": "충분", "new_tasks": []})
    state.update(await replanner(state, llm=_llm(no_followup), app_config=config))
    out = await result_aggregator(state, llm=_llm("요약입니다."), app_config=config,
                                  synthesize=True, composite_answer="steps")
    return {"plan": plan, "state": state, "out": out}


# ─── 1. 권한 있는 사용자 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("allowed", "role"), [(None, "user"), (["apm"], "user"), ([], "admin")])
async def test_authorized_user_gets_apm_rows_in_the_answer(gateway, allowed, role) -> None:
    turn = await _turn(_config(apm=True), user_role=role, allowed_sources=allowed)

    agents = [t["agent"] for t in turn["plan"]["tasks"]]
    assert agents == ["data_query", "apm_query"], "APM 활성이면 분해 어휘에 apm_query가 있다"
    assert turn["plan"]["tasks"][1]["views"] == ["apm.app_health"]
    assert gateway.opened == 1 and [c[0] for c in gateway.calls] == ["apm_app_health"]
    assert gateway.calls[0][1]["hostname"] == "web01"

    answer = turn["out"]["final_response"]
    assert "web01-was1" in answer and "812" in answer, "제니퍼 행이 응답 표에 실린다"
    assert "42.5" in answer, "폴스타 행도 함께 실린다"
    assert SOURCE_ACCESS_DENIED_MESSAGE not in answer


# ─── 2. 권한 없는 사용자 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthorized_user_gets_denial_and_polestar_answer(gateway) -> None:
    turn = await _turn(_config(apm=True), user_role="user", allowed_sources=[])

    assert gateway.opened == 0, "권한 밖이면 게이트웨이에 연결하지 않는다"
    answer = turn["out"]["final_response"]
    assert SOURCE_ACCESS_DENIED_MESSAGE in answer
    assert "42.5" in answer, "같은 질의의 폴스타 부분은 정상 답이다"
    label = get_registry().system_label("apm")
    for leak in (label, "제니퍼", "APM", "web01-was1"):
        assert leak not in answer, f"권한 밖 소스를 드러냈다: {leak}"
    assert turn["state"]["task_results"]["t2"].get("routing_intent") == "access_denied"


# ─── 3. 엔드포인트 미설정(기본 배포) ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_deployment_has_no_apm_path(gateway) -> None:
    config = _config(apm=False)
    assert subagents.resolve_subagent("apm_query", config) is None
    assert "apm_query" not in ip._planner_system_prompt(config)
    turn = await _turn(config, user_role="user", allowed_sources=None)
    assert gateway.opened == 0
    assert "apm_query" not in [t["agent"] for t in turn["plan"]["tasks"]], \
        "비활성 배포에서 목록 밖 담당 이름은 종전 폴백 규칙을 따른다"
