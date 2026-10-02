"""본체 자기 관측 `GET /api/v1/metrics` 테스트 (plans/92 트랙 B-1 · O4).

`noise_gate/tests/test_metrics_endpoint.py`(JSON 운영 지표 `/api/v1/alarm/metrics`)와 다른
엔드포인트다. 이쪽은 Prometheus 노출 형식이다.

- 플래그 off: 라우트 부재(404) + 계측 미들웨어 미추가 = 현행과 같다.
- 플래그 on: Accept 협상 2형식 · 정적 Bearer(무토큰·오토큰 401) · 토큰 미설정 503(fail-closed).
- RED 라벨: 경로 템플릿만(원 경로 값 금지) · 라벨 이름 = {route, method, status} ·
  미매칭 `unmatched`.

앱은 `create_app(config)`로 만들고 lifespan은 띄우지 않는다(`TestClient`를 with 없이 쓴다).
레지스트리는 프로세스 전역이라 값은 전·후 차이로만 단언한다.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from pydantic import SecretStr

from src.api.middleware.metrics_middleware import MetricsMiddleware
from src.api.routes.metrics import TOKEN_MISSING_DETAIL
from src.api.server import _metrics_endpoint_enabled, create_app
from src.config import ObservabilityConfig

_TOKEN = "scrape-token-o4"
_AUTH = {"Authorization": f"Bearer {_TOKEN}"}
_OM_ACCEPT = "application/openmetrics-text; version=1.0.0"


def _app(mock_config, *, enabled: bool, token: str = _TOKEN) -> FastAPI:
    """검증 대상 필드를 명시한 설정으로 앱을 만든다(`.env` 누수 차단)."""
    mock_config.observability = ObservabilityConfig(
        metrics_endpoint_enabled=enabled,
        metrics_bearer_token=SecretStr(token),
    )
    return create_app(mock_config)


def _requests_total(route: str, method: str, status: int) -> float:
    value = REGISTRY.get_sample_value(
        "http_requests_total", {"route": route, "method": method, "status": str(status)}
    )
    return value or 0.0


def _duration_count(route: str, method: str) -> float:
    value = REGISTRY.get_sample_value(
        "http_request_duration_seconds_count", {"route": route, "method": method}
    )
    return value or 0.0


# --- 플래그 off = 현행과 같다 ---


def test_flag_off_route_absent_and_middleware_not_added(mock_config):
    app = _app(mock_config, enabled=False)

    assert MetricsMiddleware not in [m.cls for m in app.user_middleware]
    assert TestClient(app).get("/api/v1/metrics", headers=_AUTH).status_code == 404


def test_flag_off_by_default():
    assert ObservabilityConfig.model_fields["metrics_endpoint_enabled"].default is False
    assert ObservabilityConfig.model_fields["metrics_bearer_token"].default.get_secret_value() == ""


def test_magicmock_config_does_not_open_endpoint():
    """테스트가 넘기는 MagicMock 설정의 참값 속성이 노출면을 열지 않는다(정확히 True만 켠다)."""
    assert _metrics_endpoint_enabled(MagicMock()) is False


# --- 플래그 on: 협상·인증 ---


def test_openmetrics_accept_returns_om_1_0_with_eof(mock_config):
    client = TestClient(_app(mock_config, enabled=True))
    response = client.get("/api/v1/metrics", headers={**_AUTH, "Accept": _OM_ACCEPT})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/openmetrics-text; version=1.0.0"
    )
    assert response.text.endswith("# EOF\n")


@pytest.mark.parametrize("accept", ["*/*", ""])
def test_default_accept_returns_text_0_0_4(mock_config, accept):
    client = TestClient(_app(mock_config, enabled=True))
    response = client.get("/api/v1/metrics", headers={**_AUTH, "Accept": accept})

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
    assert "# EOF" not in response.text


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong-token"},
    {"Authorization": _TOKEN},                # 스킴 누락
    {"Authorization": f"Basic {_TOKEN}"},     # 다른 스킴
])
def test_rejects_missing_or_wrong_token(mock_config, headers):
    client = TestClient(_app(mock_config, enabled=True))
    response = client.get("/api/v1/metrics", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "http_requests_total" not in response.text


def test_empty_token_fails_closed_with_503(mock_config, caplog):
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        client = TestClient(_app(mock_config, enabled=True, token=""))

    for headers in ({}, {"Authorization": "Bearer "}):
        response = client.get("/api/v1/metrics", headers=headers)
        assert response.status_code == 503
        assert response.json() == {"detail": TOKEN_MISSING_DETAIL}
    assert "OBS_METRICS_BEARER_TOKEN 미설정" in TOKEN_MISSING_DETAIL
    warnings = [r for r in caplog.records if "OBS_METRICS_BEARER_TOKEN" in r.getMessage()]
    assert len(warnings) == 1


def test_endpoint_not_in_openapi_and_distinct_from_alarm_metrics(mock_config):
    paths = _app(mock_config, enabled=True).openapi()["paths"]

    assert "/api/v1/metrics" not in paths
    assert "/api/v1/alarm/metrics" in paths


def test_building_app_twice_does_not_reregister_metrics(mock_config):
    """모듈 최상위 정의라 앱을 거듭 조립해도 레지스트리 중복 등록 오류가 없다."""
    first = TestClient(_app(mock_config, enabled=True))
    second = TestClient(_app(mock_config, enabled=True))

    assert first.get("/api/v1/metrics", headers=_AUTH).status_code == 200
    assert second.get("/api/v1/metrics", headers=_AUTH).status_code == 200


# --- RED 라벨 통제 ---


def test_path_param_route_is_labelled_by_template_not_raw_path(mock_config):
    client = TestClient(_app(mock_config, enabled=True))
    template = "/api/v1/threads/{thread_id}"
    raw_id = "thread-raw-9f3c"

    probe = client.get(f"/api/v1/threads/{raw_id}")
    before = _requests_total(template, "GET", probe.status_code)
    before_count = _duration_count(template, "GET")
    client.get(f"/api/v1/threads/{raw_id}")

    assert _requests_total(template, "GET", probe.status_code) == before + 1
    assert _duration_count(template, "GET") == before_count + 1
    body = client.get("/api/v1/metrics", headers=_AUTH).text
    assert raw_id not in body


def test_unmatched_path_is_labelled_unmatched(mock_config):
    client = TestClient(_app(mock_config, enabled=True))
    before = _requests_total("unmatched", "GET", 404)

    assert client.get("/no-such-path-o4/secret-segment").status_code == 404
    assert _requests_total("unmatched", "GET", 404) == before + 1
    assert "secret-segment" not in client.get("/api/v1/metrics", headers=_AUTH).text


def _requests_total_for_method(method: str) -> float:
    return sum(
        sample.value
        for family in REGISTRY.collect() if family.name == "http_requests"
        for sample in family.samples
        if sample.name == "http_requests_total" and sample.labels["method"] == method
    )


def test_nonstandard_method_is_folded_to_other(mock_config):
    """메서드 문자열은 클라이언트가 임의로 보낼 수 있다 — 표준 밖은 `other` 하나로 묶는다."""
    client = TestClient(_app(mock_config, enabled=True))
    before = _requests_total_for_method("other")

    client.request("BREW", "/api/v1/health")

    assert _requests_total_for_method("other") == before + 1
    assert 'method="BREW"' not in client.get("/api/v1/metrics", headers=_AUTH).text


def test_label_names_are_exactly_route_method_status(mock_config):
    client = TestClient(_app(mock_config, enabled=True))
    client.get("/api/v1/metrics", headers=_AUTH)

    families = {m.name: m for m in REGISTRY.collect()}
    counter_labels = {
        frozenset(s.labels) for s in families["http_requests"].samples
        if s.name == "http_requests_total"
    }
    histogram_labels = {
        frozenset(set(s.labels) - {"le"}) for s in families["http_request_duration_seconds"].samples
    }
    assert counter_labels == {frozenset({"route", "method", "status"})}
    assert histogram_labels == {frozenset({"route", "method"})}


# --- 미들웨어 단위: 예외·스트리밍 ---


def _mini_app() -> FastAPI:
    app = FastAPI()

    @app.get("/o4-boom/{item_id}")
    async def boom(item_id: str) -> dict:
        raise RuntimeError("의도된 실패")

    @app.get("/o4-stream/{item_id}")
    async def stream(item_id: str) -> StreamingResponse:
        async def chunks():
            yield b"a"
            yield b"b"

        return StreamingResponse(chunks(), status_code=206)

    app.add_middleware(MetricsMiddleware)
    return app


def test_exception_is_recorded_as_500_and_reraised():
    client = TestClient(_mini_app(), raise_server_exceptions=False)
    before = _requests_total("/o4-boom/{item_id}", "GET", 500)

    assert client.get("/o4-boom/x1").status_code == 500
    assert _requests_total("/o4-boom/{item_id}", "GET", 500) == before + 1

    with pytest.raises(RuntimeError):
        TestClient(_mini_app()).get("/o4-boom/x2")


def test_streaming_status_taken_from_response_start():
    client = TestClient(_mini_app())
    before = _requests_total("/o4-stream/{item_id}", "GET", 206)

    response = client.get("/o4-stream/s1")

    assert response.status_code == 206 and response.content == b"ab"
    assert _requests_total("/o4-stream/{item_id}", "GET", 206) == before + 1
