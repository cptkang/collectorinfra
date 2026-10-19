"""plans/164 W7-A · G-9 (a) — 인스턴스 시계열(`apm_metrics` series · scope instance) 기본 지표.

- 지표 미지정 = 기본 지표(`service_time`·`service_count` — instance 카탈로그 녹화본에 있는 이름 ·
  `tps` 식별자는 없다)로 (인스턴스 × 지표)마다 1호출 + `[한계]` 고지(침묵 폴백 아님).
- 전부 모름 = 기본 지표 + 미해결 고지 + partial(도메인·업무 시계열·런타임 추세와 같은 계열).
- 일부 모름은 종전 그대로(빼고 조회 · partial).

외부 네트워크 0 — `httpx.MockTransport` 합성 픽스처 · 가상 시계.
"""

from __future__ import annotations

import json

import pytest
from apm_gateway.adapters.jennifer.api import JenniferApi
from conftest import recorded_body, synthetic_handler
from test_plan134_w3_targets import _counting, _server

SERIES_PATH = "/api/dbmetrics/instance"


def _series_calls(seen: list[str]) -> int:
    return sum(1 for s in seen if s.endswith(SERIES_PATH))


def test_instance_defaults_exist_in_recorded_catalog():
    """기본 지표 이름이 instance 카탈로그 녹화본(로컬 5.7.0.1)에 실제로 있다 — `tps`는 없다."""
    body = recorded_body("GET_api_metrics__ok.json")
    instance = set(body["result"]["instance"])
    defaults = JenniferApi.default_series_metrics("instance")
    assert defaults == ["service_time", "service_count"]
    assert set(defaults) <= instance
    assert "tps" not in instance


@pytest.mark.asyncio
async def test_instance_series_without_metrics_uses_defaults(tmp_path):
    seen: list[str] = []
    _mcp, core, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    await jobs.aclose()
    out = await core.apm_metrics(
        "series", "instance", hostname="was-host01", interval_minute=60, lookback_minutes=600
    )
    instances = {r["instance_id"] for r in out["rows"]}
    assert instances  # 합성 픽스처 was-host01 = 인스턴스 여럿
    # (인스턴스 × 기본 지표 2개)마다 1호출 — 1분 창 분할 없음
    assert _series_calls(seen) == 2 * len(instances)
    assert {r["metric"] for r in out["rows"]} == {"service_time", "service_count"}
    assert out["interval_minute"] == 60 and out["scope"] == "instance"
    assert "[한계] 지표 미지정 — 기본 지표(response_time_avg_ms, service_count)로 조회" in (
        out["limits"]
    )
    assert "partial" not in out and "_unresolved" not in out


@pytest.mark.asyncio
async def test_instance_series_all_unknown_falls_back_with_notice(tmp_path):
    seen: list[str] = []
    _mcp, core, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    await jobs.aclose()
    out = await core.apm_metrics("series", None, hostname="was-host01", metrics=["tps"])
    assert "error" not in out
    assert {r["metric"] for r in out["rows"]} == {"service_time", "service_count"}
    (note,) = out["_unresolved"]
    assert note == (
        "요청한 지표 tps를 지표 목록에서 찾지 못해 기본 지표(response_time_avg_ms, service_count)로"
        " 조회했습니다"
    )
    assert out["partial"] is True
    assert any("카탈로그에 없는 지표는 빼고 조회했다: tps(후보: " in x for x in out["limits"])


@pytest.mark.asyncio
async def test_instance_series_named_metrics_unchanged(tmp_path):
    seen: list[str] = []
    _mcp, core, jobs = _server(tmp_path, _counting(synthetic_handler(), seen))
    await jobs.aclose()
    out = await core.apm_metrics(
        "series", "instance", hostname="was-host01", metrics=["response_time_avg_ms"]
    )
    assert {r["metric"] for r in out["rows"]} == {"service_time"}
    assert not any("기본 지표" in x for x in out["limits"])
    assert "partial" not in out


@pytest.mark.asyncio
async def test_mcp_instance_series_without_metrics(tmp_path):
    """MCP 경계(본체가 보내는 모양 — metrics 없음 · owner·thread_id)에서도 오류가 아니다."""
    mcp, _core, jobs = _server(tmp_path, synthetic_handler())
    try:
        result = await mcp.call_tool(
            "apm_metrics",
            {
                "mode": "series",
                "scope": "instance",
                "hostname": "was-host01",
                "interval_minute": 60,
                "lookback_minutes": 120,
                "owner": "o",
                "thread_id": "th-1",
            },
        )
    finally:
        await jobs.aclose()
    blocks = result[0] if isinstance(result, tuple) else result
    out = json.loads(blocks[0].text)
    assert "error" not in out, out
    assert {r["metric"] for r in out["rows"]} == {"service_time", "service_count"}
