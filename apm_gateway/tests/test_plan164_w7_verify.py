"""plans/164 W7 독립 검증(verifier) — 게이트웨이 쪽.

- 인자 없는 `apm_app_health`(sre_agent 등 다른 호출자)는 긴 창에서도 종전처럼 전수 조회한다.
- 사전 확인이 작업으로 승격돼도 데이터 호출 0 — 결과는 끝난 작업의 `result_meta`에 실린다.
- 호출 소요 이동 평균: NaN·무한·음수 표본은 버린다 · 0초는 받는다 · α=0.3.
- 견적 경계: 속도 0 · 표본 0 · 남은 호출 음수.
- 교정 확인: 표본이 인스턴스 명단 적재 1건뿐이면 `basis = rate_only`(X-View 경로 군 표본 없음).

외부 네트워크 0 — `httpx.MockTransport`만.
"""

from __future__ import annotations

import asyncio
import json
import math

import httpx
import pytest
from apm_gateway.application.jobs import BASIS_MEASURED, BASIS_RATE_ONLY, JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import CONFIRM_REQUIRED, ApmTools
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import NOW_S, TOKEN, synthetic_handler

from apm_gateway.config import JobConfig, load_config

HOST = "was-host01"
RATE = 5.0
DATA_PATHS = ("/api/realtime/instance", "/api/transaction/time", "/api/status/application")


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _server(tmp_path, seen: list[str], *, gate=None, rate: float = RATE):
    inner = synthetic_handler(gate=gate, gate_paths=("/api/instance",) if gate else ())

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return await inner(request)

    cfg = load_config({
        "JENNIFER_API_URL": "http://apm.test",
        "JENNIFER_API_TOKEN": TOKEN,
        "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        "APM_SPOOL_DIR": str(tmp_path / "spool"),
    })
    tools = ApmTools(
        build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg, clock=lambda: NOW_S
    )
    jcfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(
        Spool(jcfg.spool_dir), jcfg, envelope=tools.ok, error_envelope=tools.err,
        rate_per_sec=rate, on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), tools, jobs


# ── 인자 없는 호출 = 종전 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_argument_long_window_collects_everything(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, seen)
    try:
        out = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 120}))
    finally:
        await jobs.aclose()
    assert "status" not in out and "expected_xview_calls" not in out
    assert seen.count("/api/transaction/time") == 120  # 범위 축소 0(D-296 ④)
    assert not any("사전 확인" in x for x in out.get("limits", []))


@pytest.mark.asyncio
async def test_tool_schema_keeps_new_argument_optional(tmp_path):
    mcp, _, jobs = _server(tmp_path, [])
    await jobs.aclose()
    tool = next(t for t in await mcp.list_tools() if t.name == "apm_app_health")
    schema = tool.inputSchema
    assert "xview_confirm_over" in schema["properties"]
    assert "xview_confirm_over" not in schema.get("required", [])


# ── 승격된 사전 확인 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_promoted_preflight_makes_no_data_calls(tmp_path):
    """인스턴스 명단 적재가 wait_seconds보다 길면 작업으로 승격된다 — 그래도 데이터 호출 0 ·
    견적은 끝난 작업의 `result_meta`에만 있다(최상위 `status` 없음 — 본체 판정 주의)."""
    seen: list[str] = []
    gate = asyncio.Event()
    mcp, _, jobs = _server(tmp_path, seen, gate=gate)
    try:
        first = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 120, "xview_confirm_over": 60,
            "wait_seconds": 0, "owner": "o"}))
        assert first["job"]["state"] == "running" and "status" not in first
        gate.set()
        for _ in range(100):
            st = _json(await mcp.call_tool("apm_job_status", {
                "job_id": first["job"]["job_id"], "owner": "o"}))
            if st["job"]["state"] != "running":
                break
            await asyncio.sleep(0.01)
    finally:
        await jobs.aclose()
    assert st["job"]["state"] == "completed"
    assert "status" not in st and st["result_meta"]["status"] == CONFIRM_REQUIRED
    assert st["result_meta"]["expected_xview_calls"] == 120
    assert [p for p in seen if p in DATA_PATHS] == []


# ── 이동 평균 ───────────────────────────────────────────────────────────────


def test_moving_average_rejects_invalid_samples(tmp_path):
    _, _, jobs = _server(tmp_path, [])
    for bad in (math.nan, math.inf, -math.inf, -0.5):
        jobs.observe_call("s", bad)
    assert jobs.call_seconds("s") is None
    jobs.observe_call("s", 0.0)
    assert jobs.call_seconds("s") == 0.0
    jobs.observe_call("s", 10.0)
    assert jobs.call_seconds("s") == pytest.approx(3.0)  # 0 + 0.3 × 10
    jobs.observe_call("s", math.nan)
    assert jobs.call_seconds("s") == pytest.approx(3.0)
    assert jobs.call_seconds("other") is None and jobs.call_seconds() == pytest.approx(3.0)


def test_estimate_calls_boundaries(tmp_path):
    _, _, zero_rate = _server(tmp_path / "a", [], rate=0.0)
    assert zero_rate.estimate_calls(100) == {"api_calls": 100, "seconds": None, "basis": None}
    zero_rate.observe_call("s", 2.0)
    assert zero_rate.estimate_calls(100, ["s"]) == {
        "api_calls": 100, "seconds": 200.0, "basis": BASIS_MEASURED}
    _, _, jobs = _server(tmp_path / "b", [])
    assert jobs.estimate_calls(0) == {"api_calls": None, "seconds": None, "basis": None}
    assert jobs.estimate_calls(10, ["unknown"]) == {
        "api_calls": 10, "seconds": 2.0, "basis": BASIS_RATE_ONLY}


# ── 결함 고정 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_preflight_without_xview_samples_is_rate_only(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, seen)
    try:
        out = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 120, "xview_confirm_over": 60}))
    finally:
        await jobs.aclose()
    assert out["status"] == CONFIRM_REQUIRED
    assert out["estimate"]["basis"] == BASIS_RATE_ONLY
