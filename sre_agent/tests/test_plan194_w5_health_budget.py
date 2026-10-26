"""plans/194 W5 · C5 — `sre_health` 부하 게이지에 시간당 조사 상한(`hourly_budget`)을 싣는다.

관리자 「조사 설정」 요약(본체 `GET /admin/investigation/summary`)이 이 값을 읽는다. 실 LLM·MCP 0.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from sre_agent.application.investigation_jobs import JobStore
from sre_agent.interface import mcp_service
from tests.test_plan194_w3_load_limits import dispatcher, make_settings, ok_result


@pytest.mark.parametrize("budget", [None, 20])
def test_load_snapshot_carries_hourly_budget(budget) -> None:  # noqa: ANN001
    d = dispatcher(lambda job: ok_result(), make_settings(investigation_hourly_budget=budget))
    assert d.load_snapshot()["hourly_budget"] == budget


def test_sre_health_carries_hourly_budget(tmp_path) -> None:  # noqa: ANN001
    settings = make_settings(investigation_hourly_budget=7)
    store = JobStore(settings, executor=dispatcher(lambda job: ok_result(), settings),
                     audit_path=tmp_path / "a.jsonl")
    svc = mcp_service.create_service(settings, job_store=store)
    res = asyncio.run(svc.call_tool("sre_health", {}))
    content = res[0] if isinstance(res, tuple) else res
    health = json.loads(content[0].text)
    assert health["hourly_budget"] == 7 and health["running"] == 0
