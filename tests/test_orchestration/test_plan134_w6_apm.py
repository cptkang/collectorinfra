"""plans/134 W6(독립분 · A-2 · F-09) 본체 — 소스 변경 감지 전후 비교 보기 `apm.change_impact`.

고정하는 계약(팀 리드 계약 §0 · §2.5 · §4.1 · §4.3):
  1. 보기 = `apm_change_impact` · range(변경 탐색 구간) · 대상 hostname(필수 — 종전 필수 대상
     규칙) · 조건 `width_minutes`·`n`·`full` · 고정 고지 `apm_change_detection`.
  2. 기간 미지정이면 파서 기간을 쓰지 않는다(게이트웨이 기본 24시간) · 기간을 말하면 그대로 넘기고
     「하루 넘게 지난 기간」 규칙(D-283 ④ · W6 M-7 전까지)은 그대로 막는다.
  3. 결정적 줄: 변경마다 「{인스턴스} 변경 감지 {시각} — 오류율 a → b(±%p) · 평균 응답 a → b(±%) ·
     오류 기록 a → b · 호출 a → b」(값이 없으면 N/A — 0으로 세지 않는다) → `**판정·집계**` 블록.
게이트웨이는 모의 MCP 세션이다(봉투 모양은 계약 §2.5 — 게이트웨이 실코드는 병합 뒤 대조).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration.result_aggregator import _with_answer_lines

NOW = datetime(2026, 10, 2, 10, 0, 0)


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False),
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

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.replies[name]))],
                               isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _change(**over: Any) -> dict:
    row = {"source_id": "default", "domain_id": 1000, "instance_id": 11,
           "instance_name": "web01-was1", "change_detected_ms": 1759363200000,
           "change_detected_at": "2026-10-02T09:00:00+09:00", "width_minutes": 60,
           "before": {"start_ms": 1, "end_ms": 2, "calls": 1000, "tx_errors": 10,
                      "error_rate": 0.01, "avg_response_ms": 200.0, "p95_response_ms": 800,
                      "max_response_ms": 2000, "error_records": 4},
           "after": {"start_ms": 2, "end_ms": 3, "calls": 1200, "tx_errors": 36,
                     "error_rate": 0.03, "avg_response_ms": 250.0, "p95_response_ms": 900,
                     "max_response_ms": 2500, "error_records": 9},
           "delta": {"calls": {"abs": 200, "pct": 20.0}, "tx_errors": {"abs": 26, "pct": 260.0},
                     "error_rate": {"abs": 2.0, "pct": 200.0},
                     "avg_response_ms": {"abs": 50.0, "pct": 25.0},
                     "p95_response_ms": {"abs": 100, "pct": 12.5},
                     "error_records": {"abs": 5, "pct": 125.0}}}
    row.update(over)
    return row


def _isolated(*hosts: str, time_range: dict | None = None) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": time_range, "limit": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice"}


async def _run(*hosts: str, view_args: dict | None = None,
               time_range: dict | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query",
                            "views": ["apm.change_impact"], "sub_query": "질의"}
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, _isolated(*hosts, time_range=time_range), llm=None,
                                  app_config=_cfg(), now=NOW)


def test_change_impact_view_row() -> None:
    view = {v.id: v for v in aq.apm_views()}["apm.change_impact"]
    assert (view.tool, view.window, view.required_input, view.target, view.capability,
            view.notices) == ("apm_change_impact", "range", "hostname", "",
                              "was_change_detection", ("apm_change_detection",))
    assert [(a.name, a.type) for a in view.args] == [
        ("width_minutes", "int"), ("n", "int"), ("full", "bool")]


@pytest.mark.asyncio
async def test_conditions_period_and_detection_notice(gateway) -> None:
    gw = gateway({"apm_change_impact": _env("apm_change_impact", [_change()])})
    res = await _run("web01", view_args={"apm.change_impact": {"width_minutes": 30, "n": 2}},
                     time_range={"start": "2026-10-01 12:00", "end": "2026-10-02 09:00"})
    (args,) = gw.named("apm_change_impact")
    assert (args["hostname"], args["width_minutes"], args["n"], args["lookback_minutes"]) == (
        "web01", 30, 2, 21 * 60)
    kinds = [d["kind"] for d in res["disclosures"]]
    assert kinds == [disc.APM_CHANGE_DETECTION]


@pytest.mark.asyncio
async def test_no_period_leaves_the_gateway_default(gateway) -> None:
    gw = gateway({"apm_change_impact": _env("apm_change_impact", [])})
    await _run("web01")
    (args,) = gw.named("apm_change_impact")
    assert "reference_time" not in args and "lookback_minutes" not in args, \
        "기간 미지정 = 변경 탐색 기본 24시간(게이트웨이)"


@pytest.mark.asyncio
async def test_period_older_than_a_day_is_still_refused(gateway) -> None:
    gw = gateway({"apm_change_impact": _env("apm_change_impact", [])})
    res = await _run("web01", time_range={"start": "2026-09-28 00:00", "end": "2026-09-29 00:00"})
    assert gw.calls == [] and res["degraded_reason"] == "apm_not_queried"
    assert aq.OUT_OF_WINDOW_NOTE in res["final_response"]


@pytest.mark.asyncio
async def test_change_lines_reach_the_deterministic_block(gateway) -> None:
    na = _change(instance_name="web01-was2", change_detected_at=None,
                 after={"calls": None, "error_rate": None, "avg_response_ms": None,
                        "error_records": 9},
                 delta={"calls": None, "error_rate": {"abs": None, "pct": None},
                        "avg_response_ms": {"abs": None, "pct": None},
                        "error_records": {"abs": 5, "pct": 125.0}})
    gateway({"apm_change_impact": _env("apm_change_impact", [_change(), na])})
    res = await _run("web01")
    lines = res["answer_lines"]
    assert lines[0] == ("web01-was1 변경 감지 2026-10-02T09:00:00+09:00 — "
                        "오류율 1.0% → 3.0%(+2.0%p) · 평균 응답 200ms → 250ms(+25.0%) · "
                        "오류 기록 4 → 9 · 호출 1,000 → 1,200")
    assert lines[1].startswith("web01-was2 변경 감지 ")
    assert ("오류율 1.0% → N/A(N/A) · 평균 응답 200ms → N/A(N/A) · 오류 기록 4 → 9 · "
            "호출 1,000 → N/A" in lines[1]), "값이 없으면 N/A(0으로 세지 않는다)"
    block = _with_answer_lines("본문", lines)
    assert block.startswith("본문\n\n**판정·집계**\n- web01-was1 변경 감지")
