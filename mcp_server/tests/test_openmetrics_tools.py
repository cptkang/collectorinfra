"""openmetrics_tools.py 테스트 (plans/92 O2 · §4.3 · §7.1 · §4.8.2 [v3]).

exporter 직접 스크레이프 도구의 안전 통제(허용목록 밖 HTTP 0회·리다이렉트 거부·크기 상한·
timeout·GET·Accept), federate URL 조립, 반환 계약(PromQL instant와 같은 vector 모양), 타깃 신원,
카탈로그, ``prom_*`` URL 미설정 힌트, 등록 게이트와 ``tools/list`` 스냅샷을 고정한다.
HTTP는 httpx.MockTransport로 대신한다. 실 mock-exporter 통합은 ``RUN_DOCKER_IT=1`` 옵트인이다.
"""

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

try:
    import httpx
    from mcp_server.config import (
        AppServerConfig,
        OpenMetricsConfig,
        OpenMetricsTarget,
        PrometheusConfig,
    )
    from mcp_server.server import create_server

    from mcp_server import openmetrics as om
    from mcp_server import openmetrics_tools as omt
    from mcp_server import promql_tools as pq

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp/httpx 패키지가 설치되지 않음")

_REPO = Path(__file__).resolve().parents[2]
_MOCK_DIR = _REPO / "testdata" / "prometheus" / "mock_exporter"
_OM_DIR = Path(__file__).resolve().parents[1] / "testdata" / "openmetrics"
_TEXT_CT = "text/plain; version=0.0.4"
_OM_CT = "application/openmetrics-text; version=1.0.0; charset=utf-8"
_HOST = "web-01"
_URL = "http://exporter.test:9100/metrics"
_MOCK_VALUES = {
    ("mock_cpu_usage_percent", "user"): "97.5",
    ("mock_cpu_usage_percent", "system"): "1.5",
    ("mock_memory_used_bytes", None): "8589934592",
    ("mock_oom_kills_total", None): "3",
}


def _mock_text() -> str:
    return (_MOCK_DIR / "metrics.txt").read_text(encoding="utf-8")


def _mock_om_text() -> str:
    return (_MOCK_DIR / "metrics_om.txt").read_text(encoding="utf-8")


def _cfg(targets: list | None = None, **kw: Any) -> "OpenMetricsConfig":
    if targets is None:
        targets = [OpenMetricsTarget(hostname=_HOST, url=_URL)]
    return OpenMetricsConfig(targets=targets, **kw)


def _handler(body: Any = "", content_type: str = _TEXT_CT, status: int = 200,
             headers: dict | None = None, captured: list | None = None,
             raise_exc: Exception | None = None):
    """MockTransport 핸들러 — 요청 캡처·응답/예외 주입."""

    def handler(request):
        if captured is not None:
            captured.append(request)
        if raise_exc is not None:
            raise raise_exc
        content = body.encode("utf-8") if isinstance(body, str) else body
        return httpx.Response(
            status, content=content, headers={"content-type": content_type, **(headers or {})}
        )

    return handler


async def _call(fn, handler, *, client_kwargs: dict | None = None, **kwargs: Any) -> dict:
    """mock client를 주입해 도구 코어를 호출하고 JSON을 풀어 돌려준다."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), **(client_kwargs or {})
    ) as client:
        return json.loads(await fn(client=client, **kwargs))


async def _instant(handler, *, cfg=None, hostname=_HOST, **kwargs: Any) -> dict:
    return await _call(
        omt.run_om_metric_instant, handler,
        cfg=cfg or _cfg(), hostname=hostname, **kwargs,
    )


def _values(result: dict) -> dict:
    return {
        (r["metric"]["__name__"], r["metric"].get("mode")): r["value"][1]
        for r in result["data"]["result"]
    }


# =====================================================================
# HTTP 전 검증 — 허용목록 밖·타깃 0건·인자 오류는 HTTP 0회 (I-1)
# =====================================================================


async def test_targets_unset_returns_error_without_http():
    captured: list = []
    d = await _instant(_handler(captured=captured), cfg=_cfg(targets=[]), metric="x")
    assert d == {"error": "OpenMetrics 스크레이프 타깃 미설정"}
    assert captured == []


async def test_unregistered_hostname_returns_error_without_http():
    captured: list = []
    d = await _instant(_handler(captured=captured), hostname="other-host", metric="x")
    assert d == {"error": "스크레이프 타깃 미등록: other-host"}
    assert captured == []


async def test_empty_hostname_rejected_without_http():
    captured: list = []
    d = await _instant(_handler(captured=captured), hostname="  ", metric="x")
    assert d == {"error": "hostname이 비어 있음"}
    assert captured == []


async def test_filter_required_without_http():
    captured: list = []
    d = await _instant(_handler(captured=captured))
    assert "metric 또는 prefix" in d["error"]
    assert captured == []


@pytest.mark.parametrize("kwargs", [{"metric": 'up{job="x"}'}, {"metric": "rate(x[5m])"},
                                    {"prefix": "a b"}, {"prefix": ""}])
async def test_invalid_metric_or_prefix_rejected_without_http(kwargs):
    captured: list = []
    d = await _instant(_handler(captured=captured), **kwargs)
    assert "bare 메트릭 이름" in d["error"]
    assert captured == []


async def test_max_series_below_one_rejected_without_http():
    captured: list = []
    d = await _instant(_handler(captured=captured), metric="x", max_series=0)
    assert "max_series" in d["error"]
    assert captured == []


async def test_hostname_is_stripped_before_lookup():
    d = await _instant(
        _handler(_mock_text()), hostname=f"  {_HOST} ", metric="mock_oom_kills_total"
    )
    assert d["target"] == _HOST
    assert d["data"]["result"][0]["metric"]["nodename"] == _HOST


# =====================================================================
# HTTP 통제 — GET·Accept·리다이렉트·비200·크기 상한·timeout (I-2)
# =====================================================================


async def test_request_is_get_with_prometheus_accept_to_registered_url():
    captured: list = []
    await _instant(_handler(_mock_text(), captured=captured), metric="mock_oom_kills_total")
    (req,) = captured
    assert req.method == "GET"
    assert str(req.url) == _URL
    assert req.headers["accept"] == om.PROM_SCRAPE_ACCEPT


async def test_accept_and_timeout_forced_even_with_client_defaults():
    captured: list = []
    await _instant(
        _handler(_mock_text(), captured=captured),
        cfg=_cfg(scrape_timeout=7),
        metric="mock_oom_kills_total",
        client_kwargs={"headers": {"Accept": "text/html"}, "timeout": 99.0},
    )
    (req,) = captured
    assert req.headers["accept"] == om.PROM_SCRAPE_ACCEPT
    assert req.extensions["timeout"]["read"] == 7.0
    assert req.extensions["timeout"]["connect"] == 7.0


async def test_redirect_rejected_and_not_followed():
    captured: list = []
    d = await _instant(
        _handler(status=302, headers={"location": "http://elsewhere.test/metrics"},
                 captured=captured),
        metric="x",
        client_kwargs={"follow_redirects": True},
    )
    assert "리다이렉트 거부" in d["error"]
    assert "302" in d["error"]
    assert len(captured) == 1  # 따라가지 않았다


async def test_non_200_returns_error():
    d = await _instant(_handler("down", status=503), metric="x")
    assert d["error"] == "exporter 비200 응답: status=503"


async def test_content_length_over_limit_rejected():
    d = await _instant(_handler("x" * 200), cfg=_cfg(max_body_bytes=64), metric="x")
    assert "응답 크기 상한 초과" in d["error"]
    assert "Content-Length=200" in d["error"]


async def test_stream_accumulation_over_limit_stops_early():
    consumed: list[int] = []

    async def chunks():
        for i in range(3):
            consumed.append(i)
            yield b"#" * 64

    def handler(request):
        return httpx.Response(200, content=chunks(), headers={"content-type": _TEXT_CT})

    d = await _instant(handler, cfg=_cfg(max_body_bytes=100), metric="x")
    assert "응답 크기 상한 초과" in d["error"]
    assert "Content-Length" not in d["error"]  # 선언 길이 없이 누적 검사로 잡았다
    assert consumed == [0, 1]  # 상한을 넘긴 즉시 중단 — 세 번째 청크는 읽지 않는다


async def test_body_exactly_at_limit_is_accepted():
    body = "mock_x 1\n"
    d = await _instant(_handler(body), cfg=_cfg(max_body_bytes=len(body)), metric="mock_x")
    assert d["data"]["result"][0]["value"][1] == "1"


async def test_timeout_error_not_propagated():
    d = await _instant(_handler(raise_exc=httpx.ReadTimeout("timed out")), metric="x")
    assert d["error"] == "스크레이프 실패(ReadTimeout): timed out"


async def test_connect_error_not_propagated():
    d = await _instant(_handler(raise_exc=httpx.ConnectError("refused")), metric="x")
    assert d["error"].startswith("스크레이프 실패(ConnectError)")


async def test_invalid_utf8_body_returns_error():
    d = await _instant(_handler(b"mock_x 1\n\xff\xfe\n"), metric="mock_x")
    assert "UTF-8 디코딩 실패" in d["error"]


async def test_make_scrape_client_enforces_settings():
    client = omt.make_scrape_client(_cfg(scrape_timeout=12))
    try:
        assert client.timeout.read == 12.0
        assert client.timeout.connect == 12.0
        assert client.follow_redirects is False
        assert client.headers["accept"] == om.PROM_SCRAPE_ACCEPT
    finally:
        await client.aclose()


# =====================================================================
# federate 겸용 (§4.3 (c))
# =====================================================================


def test_build_scrape_request_exporter_uses_url_verbatim():
    target = OpenMetricsTarget(hostname=_HOST, url="http://exporter.test:9100/metrics-om?x=1")
    assert omt.build_scrape_request(target) == (
        "http://exporter.test:9100/metrics-om?x=1", None, "/metrics",
    )


async def test_federate_url_and_match_escaped():
    host = 'web"01\\x'
    captured: list = []
    federated = (
        '# TYPE mock_oom_kills_total counter\n'
        f'mock_oom_kills_total{{instance="e:80",job="mock",nodename="{_HOST}"}} 3 1700000000000\n'
    )
    cfg = _cfg(targets=[OpenMetricsTarget(hostname=host, url="http://prom.test:9090/",
                                          kind="federate")])
    d = await _instant(_handler(federated, captured=captured), cfg=cfg, hostname=host,
                       metric="mock_oom_kills_total")
    (req,) = captured
    assert req.url.path == "/federate"
    assert req.url.host == "prom.test"
    assert req.url.params.get_list("match[]") == ['{nodename="web\\"01\\\\x"}']
    assert d["endpoint"] == "/federate"
    (row,) = d["data"]["result"]
    # federate 응답의 nodename은 Prometheus honor_labels=false처럼 exported_nodename으로 밀린다
    assert row["metric"]["exported_nodename"] == _HOST
    assert row["metric"]["nodename"] == host
    assert row["value"] == [1700000000.0, "3"]


# =====================================================================
# 반환 계약 — PromQL instant와 같은 모양 (§4.3 (d))
# =====================================================================


async def test_instant_return_contract_0_0_4():
    before = time.time()
    d = await _instant(_handler(_mock_text()), metric="mock_memory_used_bytes")
    assert d["source_kind"] == "openmetrics"
    assert d["data"]["resultType"] == "vector"
    (row,) = d["data"]["result"]
    assert row["metric"] == {"__name__": "mock_memory_used_bytes", "nodename": _HOST}
    assert isinstance(row["value"][0], float)
    assert row["value"][1] == "8589934592"
    assert d["query"] == f'mock_memory_used_bytes{{nodename="{_HOST}"}}'
    assert d["endpoint"] == "/metrics"
    assert d["result_count"] == 1
    assert d["content_type"] == _TEXT_CT
    assert d["target"] == _HOST
    assert d["truncated"] is False
    assert d["series_total"] == 1
    assert d["types"] == {"mock_memory_used_bytes": "gauge"}
    assert d["target_identity"] == "unknown"
    assert "uname_nodename" not in d
    assert "queried_at" in d
    observed = datetime.fromisoformat(d["observed_at"])
    assert observed.tzinfo is not None
    assert before - 1 <= observed.timestamp() <= time.time() + 1
    assert row["value"][0] == pytest.approx(observed.timestamp())


@pytest.mark.parametrize(
    ("text_fn", "content_type"), [(_mock_text, _TEXT_CT), (_mock_om_text, _OM_CT)]
)
async def test_mock_fixed_values_same_for_both_formats(text_fn, content_type):
    d = await _instant(_handler(text_fn(), content_type=content_type), prefix="mock_")
    assert _values(d) == _MOCK_VALUES
    assert d["content_type"] == content_type
    assert d["types"]["mock_oom_kills_total"] == "counter"


async def test_om_counter_family_name_matches_total_sample():
    d = await _instant(_handler(_mock_om_text(), content_type=_OM_CT), metric="mock_oom_kills")
    assert [r["metric"]["__name__"] for r in d["data"]["result"]] == ["mock_oom_kills_total"]
    assert d["types"] == {"mock_oom_kills_total": "counter"}


async def test_missing_eof_returns_parse_error():
    text = (_OM_DIR / "truncated_no_eof.txt").read_text(encoding="utf-8")
    d = await _instant(_handler(text, content_type=_OM_CT), prefix="app_")
    assert "EOF" in d["error"]
    assert "OpenMetrics 1.0 파싱 실패" in d["error"]


async def test_truncation_flag_and_series_total():
    d = await _instant(_handler(_mock_text()), prefix="mock_", max_series=2)
    assert d["truncated"] is True
    assert d["series_total"] == 4
    assert d["result_count"] == 2
    assert len(d["data"]["result"]) == 2


async def test_max_series_capped_by_server_limit():
    d = await _instant(_handler(_mock_text()), cfg=_cfg(max_series=1), prefix="mock_",
                       max_series=50)
    assert d["result_count"] == 1
    assert d["truncated"] is True


async def test_max_series_defaults_to_server_limit():
    d = await _instant(_handler(_mock_text()), cfg=_cfg(max_series=3), prefix="mock_")
    assert d["result_count"] == 3
    assert d["series_total"] == 4


async def test_prefix_query_description():
    d = await _instant(_handler(_mock_text()), prefix="mock_cpu")
    assert d["query"] == f'{{__name__=~"mock_cpu.*",nodename="{_HOST}"}}'
    assert d["result_count"] == 2


@pytest.mark.parametrize(
    ("hostname", "os_hostname", "expected"),
    [
        ("host-a01", "", "match"),            # server_name = OS 호스트명(FQDN 정규화)
        ("svc-alias-01", "host-a01", "match"),  # server_name ≠ OS 호스트명 — os_hostname 기준
        ("svc-alias-01", "host-b02", "mismatch"),  # 허용목록 오등록
        ("svc-alias-01", "", "unverified"),
    ],
)
async def test_target_identity_from_node_uname_info(hostname, os_hostname, expected):
    text = (_OM_DIR / "om_1_0_full.txt").read_text(encoding="utf-8")
    cfg = _cfg(targets=[OpenMetricsTarget(hostname=hostname, url=_URL, os_hostname=os_hostname)])
    d = await _instant(_handler(text, content_type=_OM_CT), cfg=cfg, hostname=hostname,
                       metric="app_mystery")
    assert d["target_identity"] == expected
    assert d["uname_nodename"] == "host-a01.example.internal"


async def test_audit_log_on_success_and_error(caplog):
    with caplog.at_level(logging.INFO, logger="mcp_server.openmetrics_tools"):
        await _instant(_handler(_mock_text()), prefix="mock_", max_series=2)
        await _instant(_handler("down", status=500), metric="x")
    text = caplog.text
    assert f"openmetrics audit: tool=om_metric_instant target={_HOST}" in text
    assert "families=3 series=2 truncated=True" in text
    assert "error=exporter 비200 응답: status=500" in text


# =====================================================================
# 카탈로그
# =====================================================================


async def test_catalog_rows_and_fields():
    d = await _call(omt.run_om_metric_catalog, _handler(_mock_om_text(), content_type=_OM_CT),
                    cfg=_cfg(), hostname=_HOST)
    assert d["source_kind"] == "openmetrics"
    assert d["families"] == 3
    assert d["result_count"] == 3
    assert d["query"] == f'{{nodename="{_HOST}"}}'
    assert d["target"] == _HOST
    assert d["target_identity"] == "unknown"
    assert "observed_at" in d
    assert d["data"] == [
        {"name": "mock_cpu_usage_percent", "type": "gauge", "unit": "",
         "help": "결정적 단언용 CPU 사용률(고정값 — CPU 급증 시나리오 재현)", "series": 2},
        {"name": "mock_memory_used_bytes", "type": "gauge", "unit": "bytes",
         "help": "결정적 단언용 메모리 사용량(고정값)", "series": 1},
        {"name": "mock_oom_kills", "type": "counter", "unit": "",
         "help": "결정적 단언용 OOM 카운터(고정값 — severity_judge 시그니처 대응)", "series": 1},
    ]


async def test_catalog_prefix_filter_and_invalid_prefix():
    d = await _call(omt.run_om_metric_catalog, _handler(_mock_text()), cfg=_cfg(),
                    hostname=_HOST, prefix="mock_cpu")
    assert [r["name"] for r in d["data"]] == ["mock_cpu_usage_percent"]
    assert d["families"] == 3
    captured: list = []
    bad = await _call(omt.run_om_metric_catalog, _handler(captured=captured), cfg=_cfg(),
                      hostname=_HOST, prefix="bad prefix")
    assert "bare 메트릭 이름" in bad["error"]
    assert captured == []


async def test_catalog_unregistered_without_http():
    captured: list = []
    d = await _call(omt.run_om_metric_catalog, _handler(captured=captured), cfg=_cfg(),
                    hostname="other-host")
    assert d == {"error": "스크레이프 타깃 미등록: other-host"}
    assert captured == []


# =====================================================================
# prom_* URL 미설정 힌트 (§4.8.2 [v3] ②)
# =====================================================================


class _CaptureMCP:
    """@mcp.tool() 등록 함수를 이름→함수로 포획하는 최소 스텁."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _ctx(**config_kwargs: Any) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={"config": AppServerConfig(**config_kwargs)}
        )
    )


def _prom_tools(openmetrics_hint: bool) -> dict:
    capture = _CaptureMCP()
    pq.register_promql_tools(capture, openmetrics_hint=openmetrics_hint)
    return capture.tools


async def test_url_unset_constant_matches_prom_get():
    out = await pq._prom_get(PrometheusConfig(), "/api/v1/query", {}, "t", "q")
    assert out == pq._URL_UNSET_ERROR


async def test_hint_off_is_byte_identical_to_legacy():
    tools = _prom_tools(openmetrics_hint=False)
    ctx = _ctx(prometheus=PrometheusConfig(url=""))
    legacy = await pq.run_metric_instant(PrometheusConfig(url=""), _HOST, "node_load1")
    got = await tools["prom_metric_instant"](hostname=_HOST, metric="node_load1", ctx=ctx)
    assert got == legacy
    legacy_range = await pq.run_metric_range(PrometheusConfig(url=""), _HOST, "node_load1",
                                             "1h", "60s")
    got_range = await tools["prom_metric_range"](hostname=_HOST, metric="node_load1", ctx=ctx)
    assert got_range == legacy_range
    assert "hint" not in json.loads(legacy)


@pytest.mark.parametrize("tool", ["prom_metric_instant", "prom_metric_range"])
async def test_hint_on_adds_openmetrics_tool_name(tool):
    tools = _prom_tools(openmetrics_hint=True)
    out = await tools[tool](hostname=_HOST, metric="node_load1",
                            ctx=_ctx(prometheus=PrometheusConfig(url="")))
    assert json.loads(out) == {
        "error": "PROMETHEUS_URL 미설정 — PromQL 조회 불가",
        "hint": "om_metric_instant",
    }


async def test_hint_on_leaves_other_errors_untouched():
    tools = _prom_tools(openmetrics_hint=True)
    out = await tools["prom_metric_instant"](hostname=" ", metric="node_load1",
                                             ctx=_ctx(prometheus=PrometheusConfig(url="")))
    assert json.loads(out) == {"error": "hostname이 비어 있음"}


# =====================================================================
# 등록 게이트 · tools/list 스냅샷 (I-4 · I-6)
# =====================================================================


def test_register_expose_false_registers_nothing():
    capture = _CaptureMCP()
    omt.register_openmetrics_tools(capture, expose=False)
    assert capture.tools == {}


def test_register_expose_true_registers_two_tools():
    capture = _CaptureMCP()
    omt.register_openmetrics_tools(capture, expose=True)
    assert set(capture.tools) == {"om_metric_instant", "om_metric_catalog"}


async def test_registered_tools_read_config_from_ctx():
    capture = _CaptureMCP()
    omt.register_openmetrics_tools(capture, expose=True)
    ctx = _ctx(openmetrics=_cfg())
    d = json.loads(await capture.tools["om_metric_instant"](hostname="other-host", metric="x",
                                                            ctx=ctx))
    assert d == {"error": "스크레이프 타깃 미등록: other-host"}
    empty_ctx = _ctx(openmetrics=_cfg(targets=[]))
    d = json.loads(await capture.tools["om_metric_catalog"](hostname=_HOST, ctx=empty_ctx))
    assert d == {"error": "OpenMetrics 스크레이프 타깃 미설정"}


async def _tool_dump(config: "AppServerConfig") -> dict[str, dict]:
    return {t.name: t.model_dump() for t in await create_server(config).list_tools()}


async def test_create_server_default_has_no_om_tools():
    names = set(await _tool_dump(AppServerConfig()))
    assert not any(n.startswith("om_") for n in names)
    assert {"prom_metric_instant", "prom_metric_range"} <= names


async def test_tools_list_snapshot_unchanged_when_om_on():
    """OM을 켜면 om_* 2종만 더해지고 기존 도구의 스키마·설명은 그대로다(I-6)."""
    off = await _tool_dump(AppServerConfig())
    on = await _tool_dump(
        AppServerConfig(openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True))
    )
    assert set(on) - set(off) == {"om_metric_instant", "om_metric_catalog"}
    assert set(off) <= set(on)
    for name in off:
        assert on[name] == off[name], name
    for name in ("prom_metric_instant", "prom_metric_range"):
        assert on[name]["inputSchema"] == off[name]["inputSchema"]
        assert on[name]["description"] == off[name]["description"]


async def test_om_tool_input_schema():
    on = await _tool_dump(
        AppServerConfig(openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True))
    )
    instant = on["om_metric_instant"]["inputSchema"]
    assert instant["required"] == ["hostname"]
    assert set(instant["properties"]) == {"hostname", "metric", "prefix", "max_series"}
    catalog = on["om_metric_catalog"]["inputSchema"]
    assert catalog["required"] == ["hostname"]
    assert set(catalog["properties"]) == {"hostname", "prefix"}
    assert "현재값" in on["om_metric_instant"]["description"]


# =====================================================================
# Docker mock-exporter 통합 (옵트인 RUN_DOCKER_IT=1)
# =====================================================================


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason="Docker mock-exporter 픽스처 미기동 — RUN_DOCKER_IT=1로 옵트인 시에만 실행",
)
class TestDockerMockExporter:
    """mock-exporter(:9102)의 0.0.4(/metrics)·1.0(/metrics-om)을 실 HTTP로 읽어 고정값 대조."""

    @pytest.mark.parametrize(
        ("path", "media_type"),
        [("/metrics", "text/plain"), ("/metrics-om", "application/openmetrics-text")],
    )
    async def test_mock_exporter_fixed_values(self, path, media_type):
        base = os.environ.get("DOCKER_IT_MOCK_EXPORTER_URL", "http://localhost:9102")
        host = os.environ.get("DOCKER_IT_PROM_NODENAME", "svr-web-01")
        cfg = OpenMetricsConfig(targets=[OpenMetricsTarget(hostname=host, url=base + path)])
        d = json.loads(await omt.run_om_metric_instant(cfg, host, prefix="mock_"))
        assert "error" not in d, f"mock-exporter 미도달: {d}"
        assert d["content_type"].lower().startswith(media_type)
        values = _values(d)
        assert values[("mock_cpu_usage_percent", "user")] == "97.5"
        assert values[("mock_memory_used_bytes", None)] == "8589934592"
        assert values[("mock_oom_kills_total", None)] == "3"
