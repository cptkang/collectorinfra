"""plans/164 W7-A · G-9 (a) — 인스턴스 추세 보기(`apm.instance_trend`) 라우팅 교정(본체 쪽).

- 레지스트리: 새 보기가 `apm_metrics` instance 시계열(`fixed_args`)·hostname 필수·기간 지정 가능으로
  선언되고, `apm.app_health` 라벨은 「현재·구간 합계」로 좁혀졌다.
- 처리기: 새 보기를 별도 배선 없이 처리한다 — 지표를 말하지 않은 task는 `metrics` 없이(기본 지표는
  게이트웨이 · D-274) `mode=series · scope=instance · interval_minute=60`으로 대상마다 1호출.
- 벤치 E1 정답 계획(`plan_from_expect`)이 새 시나리오에서 이 보기·인자를 낸다.

실 게이트웨이·실 LLM 0 — 모의 게이트웨이 세션.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import driver as dv
from scripts.apm_bench import scenarios as sc
from scripts.apm_bench.fake_llm import plan_from_expect
from src.config import DBHubConfig
from src.domain.query_time import resolve_query_time
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 10, 17, 15, 0, 0)
VIEW = "apm.instance_trend"
HOURS = 15  # 오늘 0시 ~ 15시


def _views() -> dict[str, Any]:
    return {v.id: v for v in aq.apm_views()}


# ── 레지스트리 ──────────────────────────────────────────────────────────────────


def test_instance_trend_view_is_declared() -> None:
    view = _views()[VIEW]
    assert view.tool == "apm_metrics"
    assert view.fixed_args == {"mode": "series", "scope": "instance"}
    assert view.required_input == "hostname" and view.window == "range" and view.target == ""
    assert "추세" in view.label and "시간별" in view.label
    args = {a.name: a for a in view.args}
    assert set(args) == {"metrics", "interval_minute"}
    assert args["metrics"].type == "str_list" and args["metrics"].catalog == "instance"
    assert args["metrics"].default is None and not args["metrics"].required
    assert "미지정 = 기본 지표" in args["metrics"].label
    assert args["interval_minute"].type == "int" and args["interval_minute"].min == 1
    assert any("interval_minute=60" in e for e in view.examples)
    assert all("metrics=" not in e for e in view.examples)  # 카탈로그 밖 이름을 가르치지 않는다


def test_app_health_label_narrowed_to_current_and_window_totals() -> None:
    view = _views()["apm.app_health"]
    assert view.label.endswith("현재·구간 합계")
    assert "추세" not in view.label and "시간별" not in view.label
    assert view.tool == "apm_app_health"  # 도구·창은 종전 그대로


# ── 처리기 ──────────────────────────────────────────────────────────────────────


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-17T15:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


def _hourly(args: dict) -> dict:
    """게이트웨이 instance 시계열 모양 — 기본 지표 2개 × 시간별 점."""
    start = int(datetime(2026, 10, 17).timestamp() * 1000)
    rows = [{"source_id": "default", "domain_id": 1000, "instance_id": 1001,
             "instance_name": "was01_a", "metric": m, "time_ms": start + h * 3_600_000,
             "value": float(h)}
            for m in ("service_time", "service_count") for h in range(HOURS)]
    return _env("apm_metrics", rows, mode="series", scope="instance",
                interval_minute=args.get("interval_minute"),
                limits=["[한계] 지표 미지정 — 기본 지표(response_time_avg_ms, service_count)"
                        "로 조회"])


class _Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        if name == "apm_metrics":
            return _hourly(args)
        return _env(name, [{"tool_row": name, "hostname": args.get("hostname")}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    return gw


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _isolated(query: str, *hosts: str) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts], "time_range": None, "limit": None},
            "time_resolution": resolve_query_time(query, NOW).to_state(),
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice",
            "original_user_query": query, "user_query": query}


async def _run(task: dict, isolated: dict) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "sub_query": "q",
                                   "input_from": [], **task},
                                  isolated, llm=None, app_config=_cfg(), now=NOW)


@pytest.mark.asyncio
async def test_instance_trend_sends_instance_series_without_metrics(gateway) -> None:
    query = "web01 오늘 시간당 응답시간"
    res = await _run({"views": [VIEW], "view_args": {VIEW: {"interval_minute": 60}}},
                     _isolated(query, "web01"))
    assert not res.get("error"), res.get("final_response")
    (sent,) = gateway.named("apm_metrics")
    assert sent["mode"] == "series" and sent["scope"] == "instance"
    assert sent["interval_minute"] == 60 and sent["hostname"] == "web01"
    assert "metrics" not in sent  # 기본 지표는 게이트웨이가 정한다(D-274)
    assert sent.get("lookback_minutes") == HOURS * 60  # 오늘 0시부터(조회 범위를 줄이지 않는다)
    assert [n for n, _ in gateway.calls if not n.startswith("apm_job_")] == ["apm_metrics"]
    assert not gateway.named("apm_app_health")  # 종전 1분 분할 경로로 가지 않는다


@pytest.mark.asyncio
async def test_instance_trend_named_metrics_pass_through(gateway) -> None:
    await _run({"views": [VIEW],
                "view_args": {VIEW: {"metrics": ["response_time_avg_ms"], "interval_minute": 60}}},
               _isolated("web01 오늘 시간당 응답시간", "web01"))
    (sent,) = gateway.named("apm_metrics")
    assert sent["metrics"] == ["response_time_avg_ms"]


@pytest.mark.asyncio
async def test_app_health_question_unchanged(gateway) -> None:
    """「web01 응답시간 p95」·「지금 응답시간」 계획(app_health)은 종전 도구 그대로."""
    for query in ("web01 응답시간 p95", "web01 지금 응답시간"):
        gateway.calls.clear()
        await _run({"views": ["apm.app_health"]}, _isolated(query, "web01"))
        assert [n for n, _ in gateway.calls if not n.startswith("apm_job_")] == ["apm_app_health"]


# ── 벤치 E1 정답 계획 ────────────────────────────────────────────────────────────


def test_bench_scenarios_plan_instance_trend() -> None:
    scenarios = {s.id: s for s in sc.load_scenarios()}
    registry = sc.load_registry_info()
    holders = dv.load_placeholders(cli.PLACEHOLDERS_E1)
    hourly = scenarios["APM-INSTTREND-hourly"].turns[0]
    assert hourly.expect.views == (VIEW,) and hourly.expect.window == "hourly:today"
    assert hourly.expect.args == {
        "metrics": {"absent_or_contains": ["service_time", "response_time_avg_ms"]},
        "interval_minute": 60,
    } and hourly.expect.target == "host:1"
    plan = plan_from_expect(hourly.expect, hourly.prompt, mapping=holders.values,
                            views=registry.views, any_values=holders.any_values)
    (task,) = plan["tasks"]
    assert task["views"] == [VIEW] and task["view_args"] == {VIEW: {"interval_minute": 60}}
    assert task["targets"] == []  # hostname은 targets에 넣지 않는다(입력 파싱이 싣는다)
    named = scenarios["APM-INSTTREND-metrics"].turns[0]
    assert named.expect.args == {"metrics": "*", "interval_minute": 60}
