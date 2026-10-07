"""plans/134 W6 독립 검증(verify-134-w6 · 2026-10-07) — 게이트웨이 기간 순위 적대 입력(MCP 경계).

새 인자 경로(`apm_fleet` 순위 + `reference_time`·`lookback_minutes`)가 망가진 값을 HTTP 전에
`invalid_argument`로 거절하는지(시 단위 통계 호출 0) · enum 밖 지표·정렬은 스키마에서 막히는지 ·
거대 n은 인스턴스 수로 묶이는지 본다. 결함 재현은 `xfail(strict=True, reason="V6-n …")`이다.
합성 Open API만 쓴다(외부 네트워크 0 · 실 제니퍼 0 · 스풀은 tmp).
"""

from __future__ import annotations

import pytest
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.spool import Spool
from apm_gateway.domain.errors import INVALID_ARGUMENT
from apm_gateway.interface.server import audit_job_finished, create_server
from test_plan134_w6_period_ranking import GW_ROOT, PeriodFake, _json, _tools

from apm_gateway.config import JobConfig


@pytest.fixture
async def mcp_fake(tmp_path, monkeypatch):
    monkeypatch.setenv("APM_SPOOL_DIR", str(tmp_path / "spool"))
    fake = PeriodFake(2, hosts=("apm.test",))
    tools = _tools(fake, tmp_path)
    jcfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(Spool(jcfg.spool_dir), jcfg, envelope=tools.ok, error_envelope=tools.err,
                      rate_per_sec=0.0, on_finish=audit_job_finished)
    mcp = create_server(tools, jobs=jobs)
    try:
        yield mcp, fake
    finally:
        await jobs.aclose()
    assert not (GW_ROOT / "var").exists()


async def _call(mcp, fake, **args) -> dict:
    fake.calls.clear()
    return _json(await mcp.call_tool("apm_fleet", {"mode": "ranking", "metric": "calls", **args}))


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"lookback_minutes": 0},
    {"lookback_minutes": -5},
    {"lookback_minutes": 10**12},
    {"reference_time": "어제", "lookback_minutes": 60},
    {"reference_time": "", "lookback_minutes": 60},
    {"reference_time": "0001-01-01T00:30:00+09:00", "lookback_minutes": 60},
    {"n": -1, "lookback_minutes": 60},
    {"n": 0, "lookback_minutes": 60},
], ids=["lb0", "lb-neg", "lb-huge", "ref-korean", "ref-empty", "ref-year1", "n-neg", "n0"])
async def test_bad_period_arguments_are_refused_before_any_stats_call(mcp_fake, args):
    mcp, fake = mcp_fake
    out = await _call(mcp, fake, **args)
    assert out["error"] == INVALID_ARGUMENT, out
    assert fake.status_calls() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("args", [
    {"metric": "calls; DROP TABLE x", "lookback_minutes": 60},
    {"order": "sideways", "lookback_minutes": 60},
], ids=["metric", "order"])
async def test_enum_outside_values_stop_at_the_schema(mcp_fake, args):
    mcp, fake = mcp_fake
    fake.calls.clear()
    with pytest.raises(Exception, match="validation error"):
        await mcp.call_tool("apm_fleet", {"mode": "ranking", **args})
    assert fake.calls == []


@pytest.mark.asyncio
async def test_huge_n_is_bounded_by_the_instances(mcp_fake):
    mcp, fake = mcp_fake
    out = await _call(mcp, fake, n=10**9, lookback_minutes=60)
    instances = len({(h, int(q["instance_id"])) for h, q in fake.status_calls()})
    assert out["row_count"] <= instances == len(fake.status_calls())


@pytest.mark.asyncio
async def test_v6_7_far_future_reference_time_is_an_argument_error(mcp_fake):
    mcp, fake = mcp_fake
    out = await _call(mcp, fake, reference_time="9999-12-31T23:30:00+09:00", lookback_minutes=60)
    assert out["error"] == INVALID_ARGUMENT, out
