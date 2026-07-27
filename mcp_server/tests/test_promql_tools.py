"""promql_tools.py 테스트 (Plan 66 Wave 2-B′ · sre-agent/06 §3 · D-119).

서버측 nodename 결정적 조립(고수준 도구가 만드는 PromQL 문자열)·timeout 강제·원시
옵트인 게이팅·반환/오류 계약·감사 로깅을 실 Prometheus 없이 단위로 고정한다. HTTP
계층은 httpx.MockTransport로 mock 한다. 실 Prometheus HTTP 실측(Docker 픽스처, §8.1)은
별도(RUN_DOCKER_IT 옵트인)로 gate 한다.
"""

import asyncio
import json
import logging
import os

import pytest

try:
    import httpx

    from mcp_server import promql_tools as pq
    from mcp_server.config import (
        AppServerConfig,
        PrometheusConfig,
        _apply_env_overrides,
        _load_toml,
    )
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp/httpx 패키지가 설치되지 않음")


# =====================================================================
# 테스트 유틸 (mock HTTP)
# =====================================================================

_SUCCESS_VECTOR = {
    "status": "success",
    "data": {
        "resultType": "vector",
        "result": [
            {"metric": {"__name__": "node_load1", "nodename": "web-01"},
             "value": [1700000000.0, "1.23"]},
        ],
    },
}

_SUCCESS_MATRIX = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {"metric": {"nodename": "web-01"},
             "values": [[1700000000.0, "1.0"], [1700000060.0, "2.0"]]},
        ],
    },
}


def _handler(response_json, status=200, captured=None, raise_exc=None):
    """MockTransport용 핸들러를 만든다(요청 캡처·응답/예외 주입)."""

    def handler(request):
        if captured is not None:
            captured.append(request)
        if raise_exc is not None:
            raise raise_exc
        return httpx.Response(status, json=response_json)

    return handler


def _run_with_mock(cfg, handler, call):
    """mock client를 만들어 call(client) 코루틴을 실행하고 결과 문자열을 반환한다."""

    async def _do():
        client = httpx.AsyncClient(
            base_url=cfg.url or "http://prom.test",
            transport=httpx.MockTransport(handler),
        )
        try:
            return await call(client)
        finally:
            await client.aclose()

    return asyncio.run(_do())


def _cfg(**kw):
    """기본 url이 채워진 PrometheusConfig를 만든다."""
    kw.setdefault("url", "http://prom.test")
    return PrometheusConfig(**kw)


# =====================================================================
# 설정 로딩 (config.py — PrometheusConfig)
# =====================================================================


class TestPrometheusConfig:
    """[prometheus] TOML 파싱·환경변수 오버라이드·기본값 테스트."""

    def test_default_hidden_and_values(self):
        """기본값은 url 빈 문자열·timeout 30·원시 비노출이다."""
        c = PrometheusConfig()
        assert c.url == ""
        assert c.auth_header == ""
        assert c.query_timeout == 30
        assert c.expose_raw_promql is False

    def test_app_config_has_prometheus(self):
        """AppServerConfig가 prometheus 필드를 기본 생성한다."""
        assert isinstance(AppServerConfig().prometheus, PrometheusConfig)

    def test_toml_parse(self, tmp_path):
        """[prometheus] 섹션을 파싱한다."""
        toml = """
[server]
name = "prom-test"

[prometheus]
url = "http://prom.local:9090"
auth_header = "Bearer tok"
query_timeout = 20
expose_raw_promql = true
"""
        f = tmp_path / "config.toml"
        f.write_text(toml)
        config = _load_toml(f)
        assert config.prometheus.url == "http://prom.local:9090"
        assert config.prometheus.auth_header == "Bearer tok"
        assert config.prometheus.query_timeout == 20
        assert config.prometheus.expose_raw_promql is True

    def test_toml_defaults_when_absent(self, tmp_path):
        """[prometheus] 섹션이 없으면 기본값을 유지한다."""
        f = tmp_path / "config.toml"
        f.write_text("[server]\nname = \"x\"\n")
        config = _load_toml(f)
        assert config.prometheus.url == ""
        assert config.prometheus.expose_raw_promql is False

    def test_env_override_url_auth_timeout(self, monkeypatch):
        """PROMETHEUS_URL/AUTH_HEADER/QUERY_TIMEOUT 환경변수로 오버라이드한다."""
        monkeypatch.setenv("PROMETHEUS_URL", "http://env-prom:9090")
        monkeypatch.setenv("PROMETHEUS_AUTH_HEADER", "Bearer env")
        monkeypatch.setenv("PROMETHEUS_QUERY_TIMEOUT", "45")
        config = AppServerConfig(prometheus=PrometheusConfig())
        _apply_env_overrides(config)
        assert config.prometheus.url == "http://env-prom:9090"
        assert config.prometheus.auth_header == "Bearer env"
        assert config.prometheus.query_timeout == 45

    def test_env_override_expose_raw(self, monkeypatch):
        """EXPOSE_RAW_PROMQL 환경변수로 원시 노출을 옵트인한다."""
        monkeypatch.setenv("EXPOSE_RAW_PROMQL", "true")
        config = AppServerConfig(prometheus=PrometheusConfig(expose_raw_promql=False))
        _apply_env_overrides(config)
        assert config.prometheus.expose_raw_promql is True


# =====================================================================
# 서버측 nodename 결정적 조립 (§5-0)
# =====================================================================


class TestNodenameMatcher:
    """build_nodename_matcher 조립·이스케이프 테스트."""

    def test_basic(self):
        """hostname에서 {nodename="host"} 셀렉터를 조립한다."""
        assert pq.build_nodename_matcher("web-01") == '{nodename="web-01"}'

    def test_escapes_quote_and_backslash(self):
        """따옴표·백슬래시를 이스케이프한다(라벨 값 인젝션 방지)."""
        assert pq.build_nodename_matcher('a"b') == '{nodename="a\\"b"}'
        assert pq.build_nodename_matcher("a\\b") == '{nodename="a\\\\b"}'

    def test_strips_newline(self):
        """개행을 제거한다."""
        assert pq.build_nodename_matcher("a\nb") == '{nodename="ab"}'


class TestHighLevelSelector:
    """build_high_level_selector 조립·bare 메트릭 강제 테스트."""

    def test_assembles_metric_and_nodename(self):
        """metric{nodename="host"} 형태로 조립한다(서버측 결정적 조립)."""
        sel = pq.build_high_level_selector("node_cpu_seconds_total", "web-01")
        assert sel == 'node_cpu_seconds_total{nodename="web-01"}'

    def test_allows_recording_rule_colon(self):
        """레코딩 룰 이름(: 포함)도 bare 메트릭으로 허용한다."""
        sel = pq.build_high_level_selector("job:util:rate5m", "web-01")
        assert sel == 'job:util:rate5m{nodename="web-01"}'

    @pytest.mark.parametrize(
        "bad",
        [
            "rate(node_cpu[5m])",       # 함수 표현식
            'node_cpu{mode="idle"}',    # 라벨 셀렉터 포함
            "node_cpu + 1",             # 산술
            "a b",                      # 공백
            "",                         # 빈 값
        ],
    )
    def test_rejects_non_bare_metric(self, bad):
        """bare 메트릭 이름이 아니면 ValueError(임의 PromQL 차단)."""
        with pytest.raises(ValueError, match="bare 메트릭 이름만 허용"):
            pq.build_high_level_selector(bad, "web-01")


class TestParseDuration:
    """parse_duration_seconds 테스트."""

    @pytest.mark.parametrize(
        "text,secs",
        [("30s", 30), ("15m", 900), ("1h", 3600), ("7d", 604800), ("2w", 1209600)],
    )
    def test_valid(self, text, secs):
        """지원 단위(s/m/h/d/w)를 초로 변환한다."""
        assert pq.parse_duration_seconds(text) == secs

    @pytest.mark.parametrize("bad", ["", "abc", "10", "5x", "-3m", "0s"])
    def test_invalid(self, bad):
        """형식 오류·0 이하는 ValueError."""
        with pytest.raises(ValueError):
            pq.parse_duration_seconds(bad)


# =====================================================================
# 반환/오류 계약 (§3)
# =====================================================================


class TestReturnContract:
    """_ok / _err / _result_count 반환 계약 테스트."""

    def test_ok_shape(self):
        """정상 반환은 data/queried_at/source_kind를 포함한다."""
        out = json.loads(pq._ok({"result": [1, 2]}, "q", "/api/v1/query"))
        assert out["data"] == {"result": [1, 2]}
        assert out["source_kind"] == "prometheus"
        assert out["query"] == "q"
        assert out["endpoint"] == "/api/v1/query"
        assert out["result_count"] == 2
        assert "queried_at" in out

    def test_err_shape(self):
        """오류 반환은 {error} 형태."""
        assert json.loads(pq._err("boom")) == {"error": "boom"}

    def test_result_count_variants(self):
        """result_count는 vector result·list·dict를 최선 추정한다."""
        assert pq._result_count({"result": [1, 2, 3]}) == 3
        assert pq._result_count(["a", "b"]) == 2
        assert pq._result_count({"m1": [], "m2": []}) == 2
        assert pq._result_count(None) == 0


# =====================================================================
# timeout 서버 강제 (§3)
# =====================================================================


class TestMakeClientTimeout:
    """make_client가 설정 timeout·인증 헤더를 강제하는지 테스트."""

    def test_timeout_enforced(self):
        """클라이언트 timeout이 설정 query_timeout으로 강제된다(소비자 우회 불가)."""
        client = pq.make_client(_cfg(query_timeout=17))
        try:
            assert client.timeout.read == 17.0
            assert client.timeout.connect == 17.0
            assert client.timeout.write == 17.0
            assert client.timeout.pool == 17.0
        finally:
            asyncio.run(client.aclose())

    def test_auth_header_attached(self):
        """auth_header가 있으면 Authorization 헤더로 붙인다."""
        client = pq.make_client(_cfg(auth_header="Bearer secret"))
        try:
            assert client.headers["authorization"] == "Bearer secret"
        finally:
            asyncio.run(client.aclose())

    def test_no_auth_header_when_unset(self):
        """auth_header가 없으면 Authorization 헤더를 붙이지 않는다."""
        client = pq.make_client(_cfg())
        try:
            assert "authorization" not in client.headers
        finally:
            asyncio.run(client.aclose())


# =====================================================================
# 고수준 도구 HTTP — 서버측 nodename 조립이 실제 PromQL로 나가는지
# =====================================================================


class TestHighLevelHttp:
    """run_metric_instant / run_metric_range 서버측 조립·엔드포인트 테스트."""

    def test_instant_assembles_nodename_query(self):
        """instant 도구가 {nodename=...} 셀렉터를 /api/v1/query에 보낸다."""
        cfg = _cfg()
        captured = []
        out = _run_with_mock(
            cfg, _handler(_SUCCESS_VECTOR, captured=captured),
            lambda c: pq.run_metric_instant(cfg, "web-01", "node_load1", client=c),
        )
        req = captured[0]
        assert req.url.path == "/api/v1/query"
        assert req.url.params["query"] == 'node_load1{nodename="web-01"}'
        data = json.loads(out)
        assert data["source_kind"] == "prometheus"
        assert data["result_count"] == 1

    def test_range_assembles_nodename_and_window(self):
        """range 도구가 셀렉터·start/end·step(초)을 /api/v1/query_range에 보낸다."""
        cfg = _cfg()
        captured = []
        out = _run_with_mock(
            cfg, _handler(_SUCCESS_MATRIX, captured=captured),
            lambda c: pq.run_metric_range(
                cfg, "web-01", "node_cpu_seconds_total", "5m", "30s", client=c
            ),
        )
        req = captured[0]
        assert req.url.path == "/api/v1/query_range"
        assert req.url.params["query"] == 'node_cpu_seconds_total{nodename="web-01"}'
        # step="30s" → 30초, window="5m" → 300초 창(end-start)
        assert req.url.params["step"] == "30"
        start = float(req.url.params["start"])
        end = float(req.url.params["end"])
        assert round(end - start) == 300
        assert json.loads(out)["source_kind"] == "prometheus"

    def test_empty_hostname_rejected(self):
        """hostname이 비면 HTTP 없이 오류를 반환한다."""
        out = asyncio.run(pq.run_metric_instant(_cfg(), "  ", "node_load1"))
        assert json.loads(out)["error"] == "hostname이 비어 있음"

    def test_non_bare_metric_rejected(self):
        """임의 PromQL metric은 HTTP 없이 오류를 반환한다(고수준 차단)."""
        out = asyncio.run(
            pq.run_metric_instant(_cfg(), "web-01", "rate(node_cpu[5m])")
        )
        assert "bare 메트릭 이름만 허용" in json.loads(out)["error"]

    def test_bad_window_rejected(self):
        """잘못된 window duration은 오류를 반환한다."""
        out = asyncio.run(
            pq.run_metric_range(_cfg(), "web-01", "node_load1", "5x", "30s")
        )
        assert "duration" in json.loads(out)["error"]


# =====================================================================
# 원시 패스스루 HTTP
# =====================================================================


class TestRawHttp:
    """원시 도구 코어의 엔드포인트·파라미터 패스스루 테스트."""

    def test_raw_query_passthrough(self):
        """원시 instant는 임의 PromQL을 그대로 /api/v1/query에 보낸다."""
        cfg = _cfg()
        captured = []
        _run_with_mock(
            cfg, _handler(_SUCCESS_VECTOR, captured=captured),
            lambda c: pq.run_raw_query(cfg, "sum(rate(node_cpu[5m]))", client=c),
        )
        req = captured[0]
        assert req.url.path == "/api/v1/query"
        assert req.url.params["query"] == "sum(rate(node_cpu[5m]))"

    def test_raw_query_range_passthrough(self):
        """원시 range는 start/end/step을 그대로 패스스루한다."""
        cfg = _cfg()
        captured = []
        _run_with_mock(
            cfg, _handler(_SUCCESS_MATRIX, captured=captured),
            lambda c: pq.run_raw_query_range(
                cfg, "up", "100", "200", "15s", client=c
            ),
        )
        req = captured[0]
        assert req.url.path == "/api/v1/query_range"
        assert req.url.params["start"] == "100"
        assert req.url.params["end"] == "200"
        assert req.url.params["step"] == "15s"

    def test_raw_labels(self):
        """원시 labels는 /api/v1/labels로 간다."""
        cfg = _cfg()
        captured = []
        _run_with_mock(
            cfg, _handler({"status": "success", "data": ["__name__", "nodename"]},
                          captured=captured),
            lambda c: pq.run_raw_labels(cfg, client=c),
        )
        assert captured[0].url.path == "/api/v1/labels"

    def test_raw_metadata_with_metric(self):
        """원시 metadata는 metric 파라미터를 붙여 /api/v1/metadata로 간다."""
        cfg = _cfg()
        captured = []
        _run_with_mock(
            cfg, _handler({"status": "success", "data": {"node_load1": []}},
                          captured=captured),
            lambda c: pq.run_raw_metadata(cfg, "node_load1", client=c),
        )
        req = captured[0]
        assert req.url.path == "/api/v1/metadata"
        assert req.url.params["metric"] == "node_load1"

    def test_raw_series_matchers(self):
        """원시 series는 match[] 반복 파라미터로 /api/v1/series에 보낸다."""
        cfg = _cfg()
        captured = []
        _run_with_mock(
            cfg, _handler({"status": "success", "data": [{"__name__": "up"}]},
                          captured=captured),
            lambda c: pq.run_raw_series(cfg, ["up", "node_load1"], client=c),
        )
        req = captured[0]
        assert req.url.path == "/api/v1/series"
        assert req.url.params.get_list("match[]") == ["up", "node_load1"]

    def test_raw_series_empty_rejected(self):
        """빈 match는 HTTP 없이 오류를 반환한다."""
        out = asyncio.run(pq.run_raw_series(_cfg(), "", client=None))
        assert json.loads(out)["error"] == "match 셀렉터가 비어 있음"


# =====================================================================
# 오류 계약 (예외 비전파)
# =====================================================================


class TestErrorContract:
    """비200·Prometheus 오류·예외·URL 미설정을 {error}로 변환하는지 테스트."""

    def test_non_200_returns_error(self):
        """비200 응답은 {error}로 변환한다."""
        cfg = _cfg()
        out = _run_with_mock(
            cfg, _handler({}, status=500),
            lambda c: pq.run_metric_instant(cfg, "web-01", "node_load1", client=c),
        )
        assert "status=500" in json.loads(out)["error"]

    def test_prometheus_error_status(self):
        """status=error 응답은 error 메시지를 담아 {error}로 변환한다."""
        cfg = _cfg()
        out = _run_with_mock(
            cfg, _handler({"status": "error", "error": "parse error"}),
            lambda c: pq.run_metric_instant(cfg, "web-01", "node_load1", client=c),
        )
        assert "parse error" in json.loads(out)["error"]

    def test_exception_not_propagated(self):
        """HTTP 예외가 전파되지 않고 {error}로 변환된다."""
        cfg = _cfg()
        out = _run_with_mock(
            cfg, _handler(None, raise_exc=httpx.ConnectError("refused")),
            lambda c: pq.run_metric_instant(cfg, "web-01", "node_load1", client=c),
        )
        assert "error" in json.loads(out)

    def test_url_unset_returns_error_without_http(self):
        """PROMETHEUS_URL 미설정 시 HTTP 없이 명시적 오류를 반환한다(침묵 폴백 금지)."""
        out = asyncio.run(
            pq.run_metric_instant(PrometheusConfig(url=""), "web-01", "node_load1")
        )
        assert "PROMETHEUS_URL 미설정" in json.loads(out)["error"]


# =====================================================================
# 감사 로깅 (§6-5 — 도구명·쿼리·소요·행수)
# =====================================================================


class TestAuditLogging:
    """감사 로그가 도구명·쿼리·소요·행수/오류를 남기는지 테스트."""

    def test_audit_on_success(self, caplog):
        """정상 호출은 도구명·쿼리·소요·행수를 INFO로 감사 로깅한다."""
        cfg = _cfg()
        with caplog.at_level(logging.INFO, logger="mcp_server.promql_tools"):
            _run_with_mock(
                cfg, _handler(_SUCCESS_VECTOR),
                lambda c: pq.run_metric_instant(cfg, "web-01", "node_load1", client=c),
            )
        assert "promql audit" in caplog.text
        assert "tool=prom_metric_instant" in caplog.text
        assert 'node_load1{nodename="web-01"}' in caplog.text
        assert "rows=1" in caplog.text
        assert "elapsed_ms=" in caplog.text

    def test_audit_on_error(self, caplog):
        """오류 호출은 error를 WARNING으로 감사 로깅한다."""
        cfg = _cfg()
        with caplog.at_level(logging.INFO, logger="mcp_server.promql_tools"):
            _run_with_mock(
                cfg, _handler({}, status=503),
                lambda c: pq.run_metric_instant(cfg, "web-01", "node_load1", client=c),
            )
        assert "promql audit" in caplog.text
        assert "error=HTTP 503" in caplog.text


# =====================================================================
# 원시 옵트인 게이팅 (등록 시점)
# =====================================================================


class _FakeMCP:
    """@mcp.tool() 등록을 기록하는 최소 스텁."""

    def __init__(self):
        self.registered: list[str] = []

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.registered.append(fn.__name__)
            return fn

        return deco


class TestRawOptInGating:
    """expose_raw_promql에 따른 도구 노출 게이팅 테스트."""

    def test_raw_hidden_by_default(self):
        """기본(False)은 고수준 2종만 등록하고 원시는 미노출이다."""
        m = _FakeMCP()
        pq.register_promql_tools(m, expose_raw_promql=False)
        assert set(m.registered) == {"prom_metric_instant", "prom_metric_range"}

    def test_raw_exposed_on_opt_in(self):
        """옵트인(True)은 고수준 2종 + 원시 5종을 등록한다."""
        m = _FakeMCP()
        pq.register_promql_tools(m, expose_raw_promql=True)
        assert set(m.registered) == {
            "prom_metric_instant",
            "prom_metric_range",
            "prom_query",
            "prom_query_range",
            "prom_labels",
            "prom_metadata",
            "prom_series",
        }


# =====================================================================
# Docker Prometheus 통합 (§8.1 — 옵트인, 미기동 시 skip·사유 명시)
# =====================================================================


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason=(
        "Docker Prometheus 픽스처 미기동 — RUN_DOCKER_IT=1로 옵트인 시에만 실행"
        "(§8.1 prometheus+node_exporter+mock_exporter, nodename=PG 픽스처 server_name). "
        "픽스처 생성·A/B 품질 게이트는 R-B Docker 부분으로 보류."
    ),
)
class TestDockerPrometheusIntegration:
    """로컬 Docker Prometheus 픽스처 대상 실 HTTP end-to-end(옵트인)."""

    def test_placeholder(self):
        """픽스처 기동 후 실 PromQL 조회·값 단언 지점(현재 미기동으로 기본 skip)."""
        pytest.skip("Docker Prometheus 픽스처 기동 필요 — R-D 실 연동 검증에서 다룸")
