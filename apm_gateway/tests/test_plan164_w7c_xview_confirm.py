"""긴 X-View 사전 확인 — `apm_app_health(xview_confirm_over=…)`
(plans/164 W7-C · G-10 (a) · 수용 10).

- 예상 X-View 호출(1분 조각 × (소스, 도메인) · `targets`면 전 대상 합)이 기준을 넘으면
  실시간·X-View·시 단위 통계를 부르지 않고(대상 해석용 인스턴스 명단만)
  `status = confirm_required` 견적을 낸다.
- 기준 이하·인자 없음은 종전 봉투와 같다(시각 칸 제외).
- 견적 `basis` — 소스 호출 소요 표본이 있으면 `measured`(호출 × 소요), 없으면 `rate_only`.

외부 네트워크 0 — `httpx.MockTransport`만 쓴다.
"""

from __future__ import annotations

import json

import httpx
import pytest
from apm_gateway.adapters.jennifer.api import XVIEW_PATH
from apm_gateway.application.jobs import BASIS_MEASURED, BASIS_RATE_ONLY, JobManager
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import CONFIRM_REQUIRED, ApmTools
from apm_gateway.domain.errors import INVALID_ARGUMENT
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import NOW_S, TOKEN, synthetic_handler

from apm_gateway.config import JobConfig, load_config

HOST = "was-host01"
RATE = 5.0
# 데이터 경로 — 사전 확인에서는 하나도 부르지 않는다(인스턴스 명단·도메인만 허용)
DATA_PATHS = ("/api/realtime/instance", "/api/transaction/time", "/api/status/application")


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _server(tmp_path, seen: list[str]):
    inner = synthetic_handler()

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
        rate_per_sec=RATE, on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), tools, jobs


def _data_calls(seen: list[str]) -> list[str]:
    return [p for p in seen if p in DATA_PATHS]


def _without_clock(env: dict) -> dict:
    return {k: v for k, v in env.items() if k != "queried_at"}


@pytest.mark.asyncio
async def test_over_threshold_returns_estimate_without_data_calls(tmp_path):
    seen: list[str] = []
    mcp, tools, jobs = _server(tmp_path, seen)
    try:
        out = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 120, "xview_confirm_over": 60}))
    finally:
        await jobs.aclose()
    assert out["status"] == CONFIRM_REQUIRED and "error" not in out
    assert out["rows"] == [] and out["row_count"] == 0
    assert out["expected_xview_calls"] == 120  # 120분 × (소스, 도메인) 1
    assert out["expected_api_calls"] == 122  # + 실시간 1 + 시 단위 통계 1
    assert out["xview_confirm_over"] == 60 and out["instance_count"] == 2
    assert out["window"]["minutes"] == 120  # 범위를 줄이지 않는다
    # 대상 해석(인스턴스 명단 적재) 표본은 X-View 경로 군이 아니다 — 지연 미반영(rate_only)
    assert out["estimate"] == {"api_calls": 122, "seconds": round(122 / RATE, 1),
                               "basis": BASIS_RATE_ONLY}
    assert any("사전 확인" in x for x in out["limits"])
    assert _data_calls(seen) == [], "실시간·X-View·시 단위 통계를 부르지 않는다"
    assert "/api/instance" in seen  # 대상 해석(인스턴스 명단)만


@pytest.mark.asyncio
async def test_estimate_basis_rate_only_without_samples(tmp_path):
    """표본 0(작업 밖 — 호출 소요를 재지 않는다) — 속도 간격만 본 값 · 지연 미반영."""
    seen: list[str] = []
    _, tools, jobs = _server(tmp_path, seen)
    try:
        out = await tools.app_health_preflight(
            [(HOST, None, None, None)], None, 120, 60, jobs.estimate_calls)
    finally:
        await jobs.aclose()
    assert out is not None and out["status"] == CONFIRM_REQUIRED
    assert out["estimate"] == {"api_calls": 122, "seconds": round(122 / RATE, 1),
                               "basis": BASIS_RATE_ONLY}
    assert _data_calls(seen) == []


@pytest.mark.asyncio
async def test_estimate_basis_measured_uses_call_seconds(tmp_path):
    seen: list[str] = []
    mcp, tools, jobs = _server(tmp_path, seen)
    args = {"hostname": HOST, "lookback_minutes": 120, "xview_confirm_over": 60}
    try:
        await mcp.call_tool("apm_app_health", args)  # 인스턴스 명단 캐시 적재
        jobs.observe_call(tools.sources.ids[0], 12.0, group=XVIEW_PATH)  # X-View 실측 12초/호출
        out = _json(await mcp.call_tool("apm_app_health", args))
    finally:
        await jobs.aclose()
    assert out["estimate"] == {"api_calls": 122, "seconds": 122 * 12.0, "basis": BASIS_MEASURED}
    assert _data_calls(seen) == []


@pytest.mark.asyncio
async def test_under_threshold_and_no_argument_are_unchanged(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, seen)
    try:
        plain = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 30}))
        plain_calls = list(seen)
        seen.clear()
        gated = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 30, "xview_confirm_over": 60}))
    finally:
        await jobs.aclose()
    assert "status" not in plain and "status" not in gated
    assert _without_clock(gated) == _without_clock(plain)
    assert _data_calls(seen) == _data_calls(plain_calls)
    assert seen.count("/api/transaction/time") == 30


@pytest.mark.asyncio
async def test_current_only_never_asks(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, seen)
    try:
        out = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "xview_confirm_over": 0}))
    finally:
        await jobs.aclose()
    assert "status" not in out and "error" not in out
    assert "/api/realtime/instance" in seen


@pytest.mark.asyncio
async def test_targets_are_summed_before_any_data_call(tmp_path):
    """대상 2개 × 40분 = 80회 > 60 — 대상 하나씩이면 기준 이하라도 전 대상 합으로 묻는다."""
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, seen)
    try:
        out = _json(await mcp.call_tool("apm_app_health", {
            "targets": [{"hostname": HOST}, {"instance_name": "api02_main"}],
            "lookback_minutes": 40, "xview_confirm_over": 60}))
    finally:
        await jobs.aclose()
    assert out["status"] == CONFIRM_REQUIRED
    assert out["expected_xview_calls"] == 80 and out["instance_count"] == 3
    assert _data_calls(seen) == []


@pytest.mark.asyncio
async def test_negative_threshold_is_invalid(tmp_path):
    seen: list[str] = []
    mcp, _, jobs = _server(tmp_path, seen)
    try:
        out = _json(await mcp.call_tool("apm_app_health", {
            "hostname": HOST, "lookback_minutes": 120, "xview_confirm_over": -1}))
    finally:
        await jobs.aclose()
    assert out["error"] == INVALID_ARGUMENT
    assert seen == []
