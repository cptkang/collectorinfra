"""metric_source.py 테스트 (plans/92 O2b · §4.8.3~§4.8.5 [v3] · I-6 · I-7).

- 사다리 매트릭스: (URL 설정 × 커버리지 포함 × PromQL 결과 {성공·빈·오류} × 타깃 {있음·없음} ×
  정책 3종) 72조합을 MockTransport 2개(Prometheus·exporter)로 고정하고 ``fallback_reason``을
  단언한다.
- 바이트 동일: ``source`` 미지정 + 정책 off는 종전 도구와 요청·응답이 같고, OM off·URL 미설정이면
  ``tools/list``가 종전과 같다(분기 등록).
- 교차 검증 6판정 각 1건 이상 + 판정 보류 사유 + 왕복 ≤ 4.
- Docker 동시 읽기 ``consistent``는 ``RUN_DOCKER_IT=1`` 옵트인이다.
"""

import json
import os
import time
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

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

    from mcp_server import metric_source as ms
    from mcp_server import openmetrics_tools as omt
    from mcp_server import promql_tools as pq

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp/httpx 패키지가 설치되지 않음")

_HOST = "web-01"
_PROM_URL = "http://prom.test:9090"
_OM_URL = "http://exporter.test:9100/metrics"
_METRIC = "node_load1"
_TEXT_CT = "text/plain; version=0.0.4"
_FIXED_ISO = "2026-09-23T00:00:00+00:00"


def _exporter_text(value: str = "0.5", *, counter: bool = False) -> str:
    kind = "counter" if counter else "gauge"
    return f"# HELP {_METRIC} load.\n# TYPE {_METRIC} {kind}\n{_METRIC} {value}\n"


def _series(value: str = "0.5", *, name: str = _METRIC, **labels: str) -> dict:
    metric = {"__name__": name, "nodename": _HOST, "job": "node", "instance": "vm:9100", **labels}
    return {"metric": metric, "value": [time.time(), value]}


def _ts_series(sample_time: float) -> dict:
    """``timestamp(<selector>)`` 결과 — 이름이 빠지고 값이 샘플 시각이다."""
    return {"metric": {"nodename": _HOST, "job": "node", "instance": "vm:9100"},
            "value": [time.time(), str(sample_time)]}


def _up(value: str, job: str = "node") -> dict:
    return {"metric": {"__name__": "up", "nodename": _HOST, "job": job, "instance": f"{job}:1"},
            "value": [time.time(), value]}


class _Prom:
    """Prometheus MockTransport — 쿼리 종류별 응답. 값이 ``"error"``면 HTTP 500."""

    def __init__(self, instant: Any = (), *, up: Any = (), ts: Any = (),
                 coverage: Any = (_HOST,)) -> None:
        self.instant, self.up, self.ts, self.coverage = instant, up, ts, coverage
        self.requests: list[httpx.Request] = []

    def kinds(self) -> list[str]:
        return [self._kind(r) for r in self.requests]

    @staticmethod
    def _kind(request: httpx.Request) -> str:
        url = urlparse(str(request.url))
        if url.path.endswith("/label/nodename/values"):
            return "coverage"
        query = parse_qs(url.query).get("query", [""])[0]
        if query.startswith("timestamp("):
            return "ts"
        if query.startswith("up{"):
            return "up"
        return "instant"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        kind = self._kind(request)
        body = {"instant": self.instant, "up": self.up, "ts": self.ts,
                "coverage": self.coverage}[kind]
        if body == "error":
            return httpx.Response(500, json={"status": "error"})
        data = list(body) if kind == "coverage" else {"resultType": "vector", "result": list(body)}
        return httpx.Response(200, json={"status": "success", "data": data})


class _Exporter:
    def __init__(self, text: str | None = None, status: int = 200) -> None:
        self.text = _exporter_text() if text is None else text
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, content=self.text.encode(),
                              headers={"content-type": _TEXT_CT})


def _prom_cfg(url: str = _PROM_URL) -> "PrometheusConfig":
    return PrometheusConfig(url=url)


def _om_cfg(*, target: bool = True, **kw: Any) -> "OpenMetricsConfig":
    targets = [OpenMetricsTarget(hostname=_HOST, url=_OM_URL)] if target else []
    return OpenMetricsConfig(expose_openmetrics_tools=True, targets=targets, **kw)


def _clients(prom: _Prom, exp: _Exporter) -> tuple["httpx.AsyncClient", "httpx.AsyncClient"]:
    return (httpx.AsyncClient(base_url=_PROM_URL, transport=httpx.MockTransport(prom)),
            httpx.AsyncClient(transport=httpx.MockTransport(exp)))


async def _run(prom: _Prom, exp: _Exporter, *, prom_cfg=None, om_cfg=None, **kw: Any) -> dict:
    pc, oc = _clients(prom, exp)
    async with pc, oc:
        out = await ms.run_metric_instant_with_source(
            prom_cfg or _prom_cfg(), om_cfg or _om_cfg(fallback_policy="on_empty"),
            _HOST, _METRIC, prom_client=pc, om_client=oc, **kw,
        )
    return json.loads(out)


async def _cross(prom: _Prom, exp: _Exporter, *, om_cfg=None, now: float | None = None) -> dict:
    pc, oc = _clients(prom, exp)
    async with pc, oc:
        return await ms.run_cross_check(
            _prom_cfg(), om_cfg or _om_cfg(), _HOST, _METRIC,
            prom_client=pc, om_client=oc, now=now,
        )


@pytest.fixture(autouse=True)
def _fresh_coverage():
    ms.reset_coverage_cache()
    yield
    ms.reset_coverage_cache()


# =====================================================================
# 사다리 매트릭스 (R1·R2·R4·R5) — 72조합
# =====================================================================

_PROM_RESULTS = {"ok": [_series()], "empty": [], "error": "error"}


def _oracle(url: bool, covered: bool, outcome: str, target: bool, policy: str):
    """§4.8.3 표를 테스트 쪽에서 독립적으로 옮긴 기대값 ``(결과 소스, fallback_reason)``."""
    if policy == "off":
        return "legacy", None
    if not url:
        return ("openmetrics" if target else "none"), "prometheus_unavailable"
    if target and not covered:
        return "openmetrics", "host_not_covered"
    if outcome == "ok":
        return "prometheus", None
    if outcome == "empty":
        if policy == "on_empty" and target:
            return "openmetrics", "prometheus_empty"
        return "prometheus", None
    return ("openmetrics" if target else "none"), "prometheus_error"


@pytest.mark.parametrize("policy", ["off", "on_unavailable", "on_empty"])
@pytest.mark.parametrize("target", [True, False], ids=["target", "no_target"])
@pytest.mark.parametrize("outcome", ["ok", "empty", "error"])
@pytest.mark.parametrize("covered", [True, False], ids=["covered", "not_covered"])
@pytest.mark.parametrize("url", [True, False], ids=["url", "no_url"])
async def test_ladder_matrix(url, covered, outcome, target, policy):
    prom = _Prom(_PROM_RESULTS[outcome], coverage=(_HOST,) if covered else ("other",))
    exp = _Exporter()
    prom_cfg = _prom_cfg(_PROM_URL if url else "")
    om_cfg = _om_cfg(target=target, fallback_policy=policy)
    got = await _run(prom, exp, prom_cfg=prom_cfg, om_cfg=om_cfg)
    kind, reason = _oracle(url, covered, outcome, target, policy)

    if kind == "legacy":
        assert "fallback_reason" not in got  # 종전 계약 그대로(I-6)
    else:
        assert got["fallback_reason"] == reason
    if kind in ("openmetrics", "none"):  # 전환·강등 경로 — 사유 필수
        assert got["fallback_reason"] is not None
    if kind == "openmetrics":
        assert got["source_kind"] == "openmetrics"
        assert "error" not in got
    elif kind == "prometheus":
        assert got["source_kind"] == "prometheus"
    elif kind == "none":
        assert got["error"] == "조회 가능한 소스 없음"
        assert [t["source"] for t in got["tried"]] == ["prometheus", "exporter"]
    # R5 — 데이터 왕복은 호출당 최대 2회(PromQL 1 + exporter 1), 타깃이 없으면 exporter 0회.
    assert prom.kinds().count("instant") <= 1
    assert len(exp.requests) <= (1 if target else 0)
    assert len(exp.requests) + prom.kinds().count("instant") <= 2


_SWITCH_CASES = [
    # (URL, 커버리지, PromQL 결과, 정책, 기대 사유)
    # — auto에서 소스 전환·강등이 실제로 일어나는 전 경로
    (False, (_HOST,), [_series()], "on_unavailable", "prometheus_unavailable"),
    (False, (_HOST,), [_series()], "on_empty", "prometheus_unavailable"),
    (True, ("other",), [_series()], "on_unavailable", "host_not_covered"),
    (True, ("other",), [_series()], "on_empty", "host_not_covered"),
    (True, (_HOST,), "error", "on_unavailable", "prometheus_error"),
    (True, (_HOST,), "error", "on_empty", "prometheus_error"),
    (True, (_HOST,), [], "on_empty", "prometheus_empty"),
]


@pytest.mark.parametrize("url,coverage,prom_body,policy,reason", _SWITCH_CASES)
@pytest.mark.parametrize("exporter_ok", [True, False], ids=["exporter_ok", "exporter_fail"])
async def test_every_switch_path_carries_fallback_reason(
    caplog, url, coverage, prom_body, policy, reason, exporter_ok
):
    """★ 강등·소스 전환이 일어나는 모든 경로는 성공이든 R4 오류든 fallback_reason이 non-null이다.

    감사 로그에도 같은 사유가 남는다(§4.8.3 — 침묵 폴백 금지).
    """
    caplog.set_level("INFO", logger="mcp_server.metric_source")
    exp = _Exporter(status=200 if exporter_ok else 503)
    got = await _run(_Prom(prom_body, coverage=coverage), exp,
                     prom_cfg=_prom_cfg(_PROM_URL if url else ""),
                     om_cfg=_om_cfg(fallback_policy=policy))
    assert got["fallback_reason"] == reason
    assert len(exp.requests) == 1  # 실제로 exporter로 전환했다
    if exporter_ok:
        assert got["source_kind"] == "openmetrics"
    else:
        assert got["error"] == "조회 가능한 소스 없음"
    assert any(f"fallback_reason={reason}" in r.getMessage() for r in caplog.records)


async def test_prometheus_error_fallback_carries_detail():
    got = await _run(_Prom("error"), _Exporter())
    assert got["fallback_reason"] == "prometheus_error"
    assert "status=500" in got["fallback_detail"]


async def test_no_source_when_exporter_also_fails():
    got = await _run(_Prom("error"), _Exporter(status=503))
    assert got["error"] == "조회 가능한 소스 없음"
    assert got["fallback_reason"] == "prometheus_error"
    assert "status=503" in got["tried"][1]["error"]


async def test_coverage_not_looked_up_without_target():
    prom = _Prom([_series()])
    await _run(prom, _Exporter(), om_cfg=_om_cfg(target=False, fallback_policy="on_empty"))
    assert "coverage" not in prom.kinds()


async def test_coverage_cached_within_ttl_and_failure_not_cached():
    prom = _Prom([_series()])
    await _run(prom, _Exporter())
    await _run(prom, _Exporter())
    assert prom.kinds().count("coverage") == 1
    ms.reset_coverage_cache()
    failing = _Prom([_series()], coverage="error")
    await _run(failing, _Exporter())
    await _run(failing, _Exporter())
    assert failing.kinds().count("coverage") == 2  # 실패는 캐시하지 않는다(미상 → PromQL 시도)


@pytest.mark.parametrize("source,kind", [("prometheus", "prometheus"), ("exporter", "openmetrics")])
async def test_explicit_source_skips_ladder(source, kind):
    prom, exp = _Prom([]), _Exporter()
    got = await _run(prom, exp, source=source)
    assert got["source_kind"] == kind
    assert got["fallback_reason"] is None
    assert "coverage" not in prom.kinds()
    assert (len(exp.requests), len(prom.requests)) == ((0, 1) if source == "prometheus" else (1, 0))


async def test_invalid_source_and_metric_rejected_without_http():
    prom, exp = _Prom(), _Exporter()
    assert "source는" in (await _run(prom, exp, source="both"))["error"]
    pc, oc = _clients(prom, exp)
    async with pc, oc:
        out = json.loads(await ms.run_metric_instant_with_source(
            _prom_cfg(), _om_cfg(fallback_policy="on_empty"), _HOST, 'x{a="b"}',
            prom_client=pc, om_client=oc,
        ))
    assert "bare 메트릭 이름" in out["error"]
    assert prom.requests == [] and exp.requests == []


def test_pure_rules():
    assert ms.is_legacy_call("off", "auto", False)
    assert not ms.is_legacy_call("off", "auto", True)
    assert not ms.is_legacy_call("off", "exporter", False)
    assert not ms.is_legacy_call("on_empty", "auto", False)
    assert ms.fallback_reason_for("on_unavailable", "empty") is None
    assert ms.fallback_reason_for("off", "error") is None
    assert ms.plan_auto_source(prometheus_configured=True, has_target=True, coverage=None,
                               hostname=_HOST) == ("prometheus", None)
    assert ms.label_candidates({"WEB-01.corp", "web-01", "web-02"}, _HOST) == ["WEB-01.corp"]
    assert ms.availability(prometheus_configured=True, has_target=True, coverage=frozenset(),
                           hostname=_HOST) == {"sources_available": ["exporter"],
                                               "prometheus_coverage": "not_covered"}


# =====================================================================
# I-6 — source 미지정 + 정책 off = 종전 도구와 요청·응답 바이트 동일 · tools/list 분기 등록
# =====================================================================


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _ctx(config: "AppServerConfig") -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"config": config}))


@pytest.mark.parametrize("prom_body", [[_series()], [], "error"], ids=["ok", "empty", "error"])
@pytest.mark.parametrize("url", [_PROM_URL, ""], ids=["url", "no_url"])
async def test_policy_off_is_byte_identical_to_legacy_tool(monkeypatch, prom_body, url):
    """★ 확장 도구를 source 미지정으로 부르면 종전 도구와 요청·응답이 바이트 동일이다."""
    prom = _Prom(prom_body)
    monkeypatch.setattr(pq, "_now_iso", lambda: _FIXED_ISO)
    monkeypatch.setattr(pq, "make_client", lambda cfg: httpx.AsyncClient(
        base_url=cfg.url, transport=httpx.MockTransport(prom)))
    legacy, ladder = _CaptureMCP(), _CaptureMCP()
    pq.register_promql_tools(legacy, openmetrics_hint=True)
    ms.register_source_ladder_tools(ladder)
    ctx = _ctx(AppServerConfig(prometheus=_prom_cfg(url), openmetrics=_om_cfg()))

    old = await legacy.tools["prom_metric_instant"](hostname=_HOST, metric=_METRIC, ctx=ctx)
    old_reqs = [(r.method, str(r.url), dict(r.headers)) for r in prom.requests]
    prom.requests.clear()
    new = await ladder.tools["prom_metric_instant"](hostname=_HOST, metric=_METRIC, ctx=ctx)
    new_reqs = [(r.method, str(r.url), dict(r.headers)) for r in prom.requests]
    assert new == old
    assert new_reqs == old_reqs


async def _tool_dump(config: "AppServerConfig") -> dict[str, dict]:
    return {t.name: t.model_dump() for t in await create_server(config).list_tools()}


@pytest.mark.parametrize("config", [
    AppServerConfig(prometheus=PrometheusConfig(url=_PROM_URL)),
    AppServerConfig(prometheus=PrometheusConfig(url=_PROM_URL),
                    openmetrics=OpenMetricsConfig(fallback_policy="on_empty")),
    AppServerConfig(openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True,
                                                  fallback_policy="on_empty")),
], ids=["om_off_url", "om_off_policy_on", "om_on_no_url"])
async def test_tools_list_unchanged_unless_om_on_and_url(config):
    """OM off거나 URL 미설정이면 prom_* 스키마·설명이 기본 설정과 같다(분기 등록 — I-6)."""
    base = await _tool_dump(AppServerConfig(prometheus=config.prometheus))
    got = await _tool_dump(config)
    for name in base:
        assert got[name] == base[name], name


async def test_tools_list_extended_when_om_on_and_url():
    off = await _tool_dump(AppServerConfig(
        prometheus=PrometheusConfig(url=_PROM_URL),
        openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True, fallback_policy="off"),
    ))
    on_url = await _tool_dump(AppServerConfig(
        prometheus=PrometheusConfig(url=_PROM_URL),
        openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True),
    ))
    no_url = await _tool_dump(AppServerConfig(
        openmetrics=OpenMetricsConfig(expose_openmetrics_tools=True)))
    assert off == on_url  # 스키마는 정책이 아니라 OM on + URL로만 갈린다
    assert set(on_url) == set(no_url)
    changed = {n for n in on_url if on_url[n] != no_url[n]}
    assert changed == {"prom_metric_instant", "om_metric_catalog"}
    schema = on_url["prom_metric_instant"]["inputSchema"]
    assert set(schema["properties"]) == {"hostname", "metric", "source", "cross_check"}
    assert schema["required"] == ["hostname", "metric"]
    assert schema["properties"]["source"]["default"] == "auto"
    assert schema["properties"]["cross_check"]["default"] is False
    assert on_url["om_metric_catalog"]["inputSchema"] == no_url["om_metric_catalog"]["inputSchema"]


async def test_catalog_carries_sources_available():
    pc, oc = _clients(_Prom(coverage=("other",)), _Exporter())
    async with pc, oc:
        got = json.loads(await ms.run_catalog_with_sources(
            _prom_cfg(), _om_cfg(), _HOST, prom_client=pc, om_client=oc))
    assert got["sources_available"] == ["exporter"]
    assert got["prometheus_coverage"] == "not_covered"
    assert got["data"][0]["name"] == _METRIC


# =====================================================================
# 교차 검증 — 6판정 각 1건 이상 · 판정 보류 · 왕복 ≤ 4
# =====================================================================


async def test_cross_check_consistent():
    prom = _Prom([_series("0.5")], ts=[_ts_series(time.time() - 1)])
    got = await _cross(prom, _Exporter())
    cc = got["cross_check"]
    assert cc["verdict"] == "consistent"
    assert cc["compared_series"] == 1
    assert cc["round_trips"] == 3 == len(prom.requests) + 1
    assert got["prometheus"]["source_kind"] == "prometheus"
    assert got["openmetrics"]["source_kind"] == "openmetrics"
    assert got["fallback_reason"] is None


async def test_cross_check_value_drift():
    prom = _Prom([_series("0.9")], ts=[_ts_series(time.time() - 1)])
    cc = (await _cross(prom, _Exporter(_exporter_text("0.5"))))["cross_check"]
    assert cc["verdict"] == "value_drift"
    drift = cc["evidence"]["drift_series"][0]
    assert (drift["prometheus"]["value"], drift["openmetrics"]["value"]) == (0.9, 0.5)
    assert "판단 유보" in cc["diagnosis"]


async def test_cross_check_counter_is_not_drift():
    prom = _Prom([_series("900")], ts=[_ts_series(time.time() - 1)])
    cc = (await _cross(prom, _Exporter(_exporter_text("5", counter=True))))["cross_check"]
    assert cc["verdict"] in ("consistent", None)
    assert "drift_series" not in cc["evidence"]


async def test_cross_check_within_tolerance_is_consistent():
    prom = _Prom([_series("0.51")], ts=[_ts_series(time.time() - 1)])
    cc = (await _cross(prom, _Exporter(), om_cfg=_om_cfg(cross_check_tolerance=0.05)))
    assert cc["cross_check"]["verdict"] == "consistent"


async def test_cross_check_stale_uses_timestamp_query():
    now = time.time()
    prom = _Prom([_series("0.5")], ts=[_ts_series(now - 100)])
    cc = (await _cross(prom, _Exporter(), om_cfg=_om_cfg(scrape_interval_hint=15), now=now))
    cc = cc["cross_check"]
    assert cc["verdict"] == "stale"
    assert cc["evidence"]["stale_series"][0]["age_seconds"] == pytest.approx(100, abs=0.01)
    assert "ts" in prom.kinds() and "up" not in prom.kinds()


async def test_cross_check_scrape_down():
    prom = _Prom([], up=[_up("1", "mock"), _up("0", "node")])
    cc = (await _cross(prom, _Exporter()))["cross_check"]
    assert cc["verdict"] == "scrape_down"
    assert cc["evidence"]["down_targets"] == [{"job": "node", "instance": "node:1"}]
    assert "coverage" not in prom.kinds()
    assert cc["round_trips"] == 3


async def test_cross_check_label_mismatch():
    prom = _Prom([], up=[], coverage=("WEB-01.corp.example", "db-01"))
    cc = (await _cross(prom, _Exporter()))["cross_check"]
    assert cc["verdict"] == "label_mismatch"
    assert cc["evidence"]["candidates"] == ["WEB-01.corp.example"]
    assert "WEB-01.corp.example" in cc["diagnosis"]
    assert cc["round_trips"] == 4 == len(prom.requests) + 1  # 상한(I-7 [v3])


async def test_cross_check_not_scraped():
    prom = _Prom([], up=[], coverage=("db-01",))
    cc = (await _cross(prom, _Exporter()))["cross_check"]
    assert cc["verdict"] == "not_scraped"
    assert cc["evidence"]["up_series"] == 0


async def test_cross_check_coverage_cached_counts_no_round_trip():
    prom = _Prom([], up=[], coverage=("db-01",))
    await _cross(prom, _Exporter())
    cc = (await _cross(prom, _Exporter()))["cross_check"]
    assert cc["round_trips"] == 3


@pytest.mark.parametrize("prom_body,exp_status,reason", [
    ("error", 200, "prometheus_error"),
    ([_series()], 500, "exporter_error"),
])
async def test_cross_check_inconclusive_on_source_error(prom_body, exp_status, reason):
    prom = _Prom(prom_body)
    cc = (await _cross(prom, _Exporter(status=exp_status)))["cross_check"]
    assert cc["verdict"] is None
    assert cc["inconclusive_reason"] == reason
    assert cc["diagnosis"] is None
    assert cc["round_trips"] == 2


async def test_cross_check_no_common_series():
    prom = _Prom([_series("0.5", name="other_metric")], ts=[_ts_series(time.time())])
    cc = (await _cross(prom, _Exporter()))["cross_check"]
    assert cc["verdict"] is None
    assert cc["inconclusive_reason"] == "no_common_series"


async def test_cross_check_preconditions_without_http():
    prom, exp = _Prom(), _Exporter()
    pc, oc = _clients(prom, exp)
    async with pc, oc:
        no_url = await ms.run_cross_check(PrometheusConfig(), _om_cfg(), _HOST, _METRIC,
                                          prom_client=pc, om_client=oc)
        no_target = await ms.run_cross_check(_prom_cfg(), _om_cfg(target=False), _HOST, _METRIC,
                                             prom_client=pc, om_client=oc)
    assert "PROMETHEUS_URL 미설정" in no_url["error"]
    assert "타깃 미등록" in no_target["error"]
    assert prom.requests == [] and exp.requests == []


async def test_cross_check_via_ladder_entry_ignores_source():
    prom = _Prom([_series("0.5")], ts=[_ts_series(time.time() - 1)])
    got = await _run(prom, _Exporter(), source="exporter", cross_check=True,
                     om_cfg=_om_cfg(fallback_policy="off"))
    assert got["cross_check"]["verdict"] == "consistent"


def test_judge_verdict_vocabulary_is_six():
    assert set(ms.VERDICTS) == {"label_mismatch", "not_scraped", "stale", "scrape_down",
                                "value_drift", "consistent"}
    assert ms.diagnosis_for(None, {}) is None


def test_judge_marks_missing_auxiliary_queries():
    got = ms.judge_cross_check(prom_series=[], om_series=[_series()], om_types={}, now=0.0,
                               tolerance=0.05, interval=15, up_series=None)
    assert got["evidence"]["up_unavailable"] is True
    got = ms.judge_cross_check(prom_series=[_series()], om_series=[_series()], om_types={},
                               now=0.0, tolerance=0.05, interval=15, ts_series=None)
    assert got["evidence"]["timestamp_unavailable"] is True


# =====================================================================
# 운영 CLI scripts/prom_om_cross_check.py
# =====================================================================


def _load_cli():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "prom_om_cross_check.py"
    spec = importlib.util.spec_from_file_location("prom_om_cross_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_cli_cross_check_hosts(monkeypatch):
    cli = _load_cli()
    prom = _Prom([_series("0.5")], ts=[_ts_series(time.time() - 1)])
    exp = _Exporter()
    monkeypatch.setattr(pq, "make_client", lambda cfg: httpx.AsyncClient(
        base_url=cfg.url, transport=httpx.MockTransport(prom)))
    monkeypatch.setattr(omt, "make_scrape_client", lambda cfg: httpx.AsyncClient(
        transport=httpx.MockTransport(exp)))
    config = AppServerConfig(prometheus=_prom_cfg(), openmetrics=_om_cfg())
    rows = await cli.cross_check_hosts(config, [_HOST, "ghost"], _METRIC)
    assert rows[0]["verdict"] == "consistent"
    assert rows[1]["verdict"] is None and "타깃 미등록" in rows[1]["error"]
    assert "보류" in cli._format_row(rows[1])


def test_cli_exits_2_without_prometheus_url(monkeypatch, tmp_path, capsys):
    cli = _load_cli()
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    cfg = tmp_path / "config.toml"
    cfg.write_text('[prometheus]\nurl = ""\n', encoding="utf-8")
    assert cli.main(["--metric", _METRIC, "--config", str(cfg)]) == 2
    assert "PROMETHEUS_URL 미설정" in capsys.readouterr().err


# =====================================================================
# Docker 동시 읽기 (옵트인 RUN_DOCKER_IT=1) — 픽스처 Prometheus(9190) + mock-exporter(9102)
# =====================================================================


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason="Docker Prometheus·mock-exporter 픽스처 미기동 — RUN_DOCKER_IT=1로 옵트인 시에만 실행",
)
async def test_docker_fixture_cross_check_consistent():
    """픽스처 두 소스를 동시에 읽으면 mock 고정값이 같아 ``consistent``다."""
    prom_url = os.environ.get("DOCKER_IT_PROM_URL", "http://localhost:9190")
    mock_url = os.environ.get("DOCKER_IT_MOCK_EXPORTER_URL", "http://localhost:9102")
    host = "svr-web-01"
    om_cfg = OpenMetricsConfig(
        expose_openmetrics_tools=True,
        targets=[OpenMetricsTarget(hostname=host, url=f"{mock_url}/metrics")],
    )
    got = await ms.run_cross_check(PrometheusConfig(url=prom_url), om_cfg, host,
                                   "mock_cpu_usage_percent")
    assert got["cross_check"]["verdict"] == "consistent", got["cross_check"]
    assert got["cross_check"]["compared_series"] >= 2
