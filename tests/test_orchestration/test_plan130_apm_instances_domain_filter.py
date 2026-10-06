"""`apm.instances` 보기의 `domain_id` 조건 — 「도메인 아이디가 1130인 인스턴스 리스트」 오답 교정.

종전에는 보기에 도메인 조건이 선언되지 않아 계획 LLM이 낸 `domain_id`가 「이 보기에 없는 조건」으로
버려지고 전 도메인 목록이 답변 LLM에 넘어갔다. 이제 조건이 검증을 거쳐 게이트웨이 도구
`apm_instance_map(domain_id=…)`에 실린다(거르기는 게이트웨이 몫).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.orchestration import apm_query as aq


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(dbhub=dbhub,
                           composite=SimpleNamespace(max_targets=10, fanout_concurrency=2,
                                                     audit_enabled=False))


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        row = {"source_id": "default", "domain_id": 1130, "instance_id": 2001,
               "instance_name": "was_1130_a", "hostname": "", "match_confidence": "none"}
        reply = {"rows": [row], "row_count": 1, "queried_at": "2026-10-06T10:00:00+09:00",
                 "source_kind": "apm_api", "source": "jennifer", "tool": name, "limits": []}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)


@pytest.fixture
def gateway(monkeypatch) -> _Gateway:
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    return gw


async def _run(view_args: dict | None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": ["apm.instances"],
                            "sub_query": "도메인 아이디가 1130인 인스턴스 리스트를 보여줘"}
    if view_args is not None:
        task["view_args"] = view_args
    isolated = {"parsed_requirements": {"filter_conditions": [], "time_range": None,
                                        "limit": None},
                "conversation_context": {}, "thread_id": "th-1", "user_id": "alice"}
    return await aq.run_apm_query(task, isolated, llm=None, app_config=_cfg(),
                                  now=datetime(2026, 10, 6, 10, 0, 0))


def test_instances_view_declares_domain_id_condition() -> None:
    view = {v.id: v for v in aq.apm_views()}["apm.instances"]
    assert [(a.name, a.type, a.min) for a in view.args] == [("domain_id", "int", 0)]
    assert view.first_hop and view.window == "none", "목록 보기 성격은 그대로"
    assert "`domain_id` = 정수 0 이상" in aq.render_view_rows()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1130, "1130"])
async def test_domain_id_reaches_gateway_tool(gateway, value) -> None:
    res = await _run({"apm.instances": {"domain_id": value}})
    ((name, args),) = gateway.calls
    assert name == "apm_instance_map" and args["domain_id"] == 1130
    assert not res.get("disclosures"), "조건을 버렸다는 고지가 없어야 한다"


@pytest.mark.asyncio
async def test_without_domain_id_lists_all(gateway) -> None:
    await _run(None)
    ((_, args),) = gateway.calls
    assert "domain_id" not in args


@pytest.mark.asyncio
async def test_invalid_domain_id_is_dropped_with_notice(gateway) -> None:
    res = await _run({"apm.instances": {"domain_id": "결제"}})
    ((_, args),) = gateway.calls
    assert "domain_id" not in args
    (item,) = res["disclosures"]
    assert item["kind"] == "apm_unresolved_condition" and "domain_id" in item["text"]
