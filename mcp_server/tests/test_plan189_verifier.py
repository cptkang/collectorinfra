"""plans/189 독립 검증(verifier) — 구현 테스트가 덮지 않은 경계·결함 후보를 고정한다.

- 클라이언트 수명(owns_client): 사다리 up + 본 조회가 한 클라이언트를 쓰고 끝에 1회 닫는다.
- range 최대 HTTP = up 3 + 본 조회 1.
- source 미지정 은행존 서버: sources 밖 source는 조회 대상이 아니라 polestar_not_found(존 차단 아님).
- 설정 sources 중 풀에 없는 source는 대상에서 빠질 뿐 실패가 아니다 — 0행은 polestar_not_found(교정).
- 폴스타 행의 hostname·IP가 모두 비면 입력값 단독 3단만 시도 · hostname_unverified(이탈 ④ 교정).
- up 성립 후 본 조회 0건은 reason 없는 0건이다.
- nodename 모드 anchor_pool은 컨텍스트를 읽지 않는다.
- 교차 검증: 사다리가 「모호」·차단으로 끝난 경우는 판정 보류(inconclusive_reason=응답 reason · 교정).
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    import httpx

    from mcp_server import metric_source as ms
    from mcp_server import promql_tools as pq
    from mcp_server.config import PrometheusConfig
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_plan189_prom_anchor import (
        _B0, _GP, _H1, _H2, _H3, _METRIC, _YD, _FakePool, _Prom, _client, _cfg,
        _instant, _pool, _row, _sample, _up, _with_source,
    )

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp/httpx 패키지가 설치되지 않음")


@pytest.fixture(autouse=True)
def _fresh_coverage():
    ms.reset_coverage_cache()
    yield
    ms.reset_coverage_cache()


class _TrackedClient(httpx.AsyncClient):
    instances: list["_TrackedClient"] = []

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.closed_n = 0
        _TrackedClient.instances.append(self)

    async def aclose(self) -> None:
        self.closed_n += 1
        await super().aclose()


async def test_owned_client_single_instance_closed_once(monkeypatch):
    _TrackedClient.instances = []
    prom = _Prom({_H3: [_up("x:1")], f'{_METRIC}{{hostname="web01"}}': [_sample("x:1")]})
    monkeypatch.setattr(pq, "make_client", lambda cfg: _TrackedClient(
        base_url=cfg.url, transport=httpx.MockTransport(prom)))
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    got = json.loads(await pq.run_metric_instant(_cfg(), "web01", _METRIC, pool=pool))
    assert got["matched_by"] == "hostname"
    assert len(prom.requests) == 4
    assert len(_TrackedClient.instances) == 1
    assert _TrackedClient.instances[0].closed_n == 1


async def test_owned_client_not_created_when_no_http(monkeypatch):
    _TrackedClient.instances = []
    monkeypatch.setattr(pq, "make_client", lambda cfg: _TrackedClient(base_url=cfg.url))
    got = json.loads(await pq.run_metric_instant(_cfg(), "web01", _METRIC, pool=_pool()))
    assert got["reason"] == pq.REASON_POLESTAR_NOT_FOUND
    assert _TrackedClient.instances == []


async def test_range_max_http_is_three_up_plus_one():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H3: [_up("x:1")]})
    async with _client(prom) as c:
        got = json.loads(await pq.run_metric_range(_cfg(), "web01", _METRIC, "1h", "60s",
                                                   client=c, pool=pool))
    assert prom.queries()[:3] == [_H1, _H2, _H3]
    assert len(prom.requests) == 4
    assert got["matched_by"] == "hostname"
    # reference_time 없으면 probe에 time 파라미터를 싣지 않는다(종전 instant와 같은 now 판정)
    assert "time=" not in str(prom.requests[0].url)


async def test_bank_zone_without_source_is_polestar_not_found_not_zone_block():
    """source 미지정이면 sources 밖(은행존) source는 조회하지 않는다 → polestar_not_found.

    존 차단(prometheus_unsupported_zone)은 source를 지정했을 때만 난다. hint는 「source를 지정했다면
    빼고 다시」라 은행존 서버에는 맞지 않는다(보고 지적).
    """
    pool = _pool({_B0: [_row("b0-web", "b0-web", "10.1.0.1")]})
    prom = _Prom()
    got = await _instant(prom, pool, "b0-web")
    assert got["reason"] == pq.REASON_POLESTAR_NOT_FOUND
    assert [c[0] for c in pool.calls] == [_GP, _YD]
    assert prom.requests == []


async def test_configured_but_inactive_source_is_excluded_not_failed():
    """설정 sources의 source가 풀에 없으면 대상에서 뺄 뿐이다 — 0행은 polestar_not_found(HTTP 0회)."""
    pool = _FakePool({_GP: "postgresql"}, {_GP: []})  # _YD 미등록
    prom = _Prom({_H3: [_up("x:1")]})
    got = await _instant(prom, pool)
    assert got["reason"] == pq.REASON_POLESTAR_NOT_FOUND
    assert "warning" not in got
    assert [c[0] for c in pool.calls] == [_GP]
    assert prom.requests == []


async def test_anchor_row_with_blank_hostname_and_ip_degrades_to_unverified():
    pool = _pool({_GP: [_row("vm-a", "", "")]})
    prom = _Prom()
    got = await _instant(prom, pool, "vm-a")
    assert got["reason"] == pq.REASON_ANCHOR_UNRESOLVED
    assert got["matched_by"] == pq.MATCHED_BY_HOST_UNVERIFIED
    assert got["tried"] == ['up{hostname="vm-a"}']
    assert got["anchor"]["name"] == "vm-a"
    assert got["warning"] == pq.BLANK_ANCHOR_WARNING


async def test_up_established_but_metric_absent_is_reasonless_zero():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")]})
    got = await _instant(prom, pool)
    assert got["matched_by"] == "hostname+node_ip"
    assert got["result_count"] == 0
    assert "reason" not in got and "hint" not in got


def test_anchor_pool_nodename_mode_does_not_touch_ctx():
    ctx = SimpleNamespace()  # request_context 없음 — 읽으면 AttributeError
    assert pq.anchor_pool(ctx, PrometheusConfig(url="http://x")) is None


async def test_zone_blocked_without_pool_still_returns_reason():
    got = await _instant(_Prom(), None, source=_B0)
    assert got["reason"] == pq.REASON_UNSUPPORTED_ZONE


async def test_cross_check_ambiguous_probe_is_inconclusive_not_scrape_down():
    """「모호」로 끝난 사다리의 up 시리즈(여러 서버)는 교차 검증 근거로 쓰지 않는다 — 판정 보류.

    한 instance가 up=0이어도 요청 서버가 아닐 수 있어 scrape_down을 내지 않는다(교정).
    """
    pool = _pool({_GP: [_row("vm-a", "phys01", "")]})
    prom = _Prom({'up{hostname="phys01"}': [_up("10.0.0.1:9100", "1"),
                                            _up("10.0.0.2:9100", "0")]})
    got = await _with_source(prom, pool, "auto", cross=True)
    assert got["prometheus"]["reason"] == pq.REASON_PROMETHEUS_AMBIGUOUS
    assert got["cross_check"]["verdict"] is None
    assert got["cross_check"]["inconclusive_reason"] == pq.REASON_PROMETHEUS_AMBIGUOUS


async def test_cross_check_zone_blocked_is_inconclusive():
    """존 차단 응답(HTTP 0회)은 exporter에 데이터가 있어도 not_scraped가 아니라 판정 보류다(교정)."""
    prom = _Prom()
    got = await _with_source(prom, _pool(), _B0, cross=True)
    assert got["prometheus"]["reason"] == pq.REASON_UNSUPPORTED_ZONE
    assert got["cross_check"]["verdict"] is None
    assert got["cross_check"]["inconclusive_reason"] == pq.REASON_UNSUPPORTED_ZONE
    assert got["cross_check"]["diagnosis"] is None
    assert prom.requests == []
