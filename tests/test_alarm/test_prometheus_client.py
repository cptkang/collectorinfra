"""PrometheusClient 단위 테스트 (httpx 모킹).

Plan 60 E3 baseline / Plan 64 §4.5 — node_exporter 메트릭을 Prometheus HTTP Query API로
read-only 조회. 고정 대상: 읽기전용(D-003)·graceful None·db_id 매핑·PromQL 인코딩·
스칼라/시계열 파싱. httpx 모킹은 폴스타 프로세스 API 테스트(_FakeAsyncClient)와 동일 패턴.
"""

from __future__ import annotations

import pytest

import src.alarm.infrastructure.prometheus_client as mod
from src.alarm.infrastructure.prometheus_client import PrometheusClient
from src.config import AlarmConfig


# ─── httpx 모킹 대역 (프로세스 API 테스트와 동일 패턴) ──────────────────────────

class _FakeResp:
    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._raise = raise_exc

    def json(self):
        if self._raise:
            raise self._raise
        return self._payload


class _FakeAsyncClient:
    """httpx.AsyncClient 대역 — get URL 기록 + 지정 응답/예외 반환."""

    last_url = None
    next_resp = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        _FakeAsyncClient.last_url = url
        resp = _FakeAsyncClient.next_resp
        if isinstance(resp, Exception):
            raise resp
        return resp


def _cfg():
    # 검증 대상 필드를 명시해 .env 누수 차단 (Known Mistakes: 테스트 config 필드 명시)
    return AlarmConfig(
        prometheus_enabled=True,
        prometheus_base_urls_csv="polestar_cm_gp=http://prom-gp:9090",
        prometheus_timeout_seconds=3,
    )


def _vector(val):
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {"hostname": "web-01"}, "value": [1000.0, str(val)]}],
        },
    }


def _matrix(vals):
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"hostname": "web-01"},
                    "values": [[1000.0 + i * 60, str(v)] for i, v in enumerate(vals)],
                }
            ],
        },
    }


class TestPrometheusClient:
    def _client(self):
        return PrometheusClient(_cfg())

    async def test_unmapped_db_id_returns_none(self):
        # base_url 미매핑 → 조회 자체 skip (graceful)
        assert await self._client().query("polestar_cm_yd", "node_load1") is None

    async def test_empty_promql_returns_none(self):
        assert await self._client().query("polestar_cm_gp", "") is None

    async def test_query_scalar_ok(self, monkeypatch):
        _FakeAsyncClient.next_resp = _FakeResp(payload=_vector(0.42))
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        out = await self._client().query_scalar(
            "polestar_cm_gp", 'node_load1{hostname="web-01"}'
        )
        assert out == pytest.approx(0.42)
        assert "/api/v1/query?" in _FakeAsyncClient.last_url  # 올바른 read-only 엔드포인트

    async def test_promql_url_encoded(self, monkeypatch):
        _FakeAsyncClient.next_resp = _FakeResp(payload=_vector(1))
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        await self._client().query("polestar_cm_gp", 'node_load1{hostname="web-01"}')
        # 중괄호·따옴표가 raw로 들어가지 않음(인젝션 방지)
        assert '{hostname="web-01"}' not in _FakeAsyncClient.last_url
        assert "%7B" in _FakeAsyncClient.last_url  # '{' 인코딩됨

    async def test_query_series_ok(self, monkeypatch):
        _FakeAsyncClient.next_resp = _FakeResp(payload=_matrix([1.0, 2.0, 3.0]))
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        out = await self._client().query_series(
            "polestar_cm_gp",
            "avg_over_time(node_load1[5m])",
            start=1000.0,
            end=1180.0,
            step="60s",
        )
        assert out == [1.0, 2.0, 3.0]
        assert "/api/v1/query_range?" in _FakeAsyncClient.last_url
        assert "start=1000.0" in _FakeAsyncClient.last_url
        assert "step=60s" in _FakeAsyncClient.last_url

    async def test_non_200_returns_none(self, monkeypatch):
        _FakeAsyncClient.next_resp = _FakeResp(status_code=503)
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        assert await self._client().query_scalar("polestar_cm_gp", "node_load1") is None

    async def test_status_not_success_returns_none(self, monkeypatch):
        _FakeAsyncClient.next_resp = _FakeResp(payload={"status": "error", "errorType": "bad"})
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        assert await self._client().query("polestar_cm_gp", "node_load1") is None

    async def test_network_error_returns_none(self, monkeypatch):
        _FakeAsyncClient.next_resp = ConnectionError("refused")
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        assert await self._client().query_scalar("polestar_cm_gp", "node_load1") is None

    async def test_empty_result_scalar_none(self, monkeypatch):
        _FakeAsyncClient.next_resp = _FakeResp(
            payload={"status": "success", "data": {"result": []}}
        )
        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeAsyncClient)
        assert await self._client().query_scalar("polestar_cm_gp", "node_load1") is None

    async def test_read_only_no_write_methods(self):
        # 읽기전용 불변(D-003) — 쓰기/remote-write 메서드가 존재하지 않음
        assert not hasattr(PrometheusClient, "write")
        assert not hasattr(PrometheusClient, "remote_write")
        assert not hasattr(PrometheusClient, "post")
