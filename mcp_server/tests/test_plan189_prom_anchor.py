"""plans/189 운영 Prometheus 라벨 앵커 테스트 (D-119 ③ 부기 · W1~W4 · W6 통합).

- W2: 폴스타 앵커 해석 SQL(PG·DB2 방언 · 값 이스케이프)과 ``resolve_polestar_anchor``
  (1행·0행·2행·전부 오류·일부 오류·호출 오류).
- W3: ``anchor_mode=polestar_host`` 사다리 3단 성립 · IP 공백 시 1·2단 생략 · 「모호」 ·
  관제 미등록 · 존 차단(HTTP·DB 0회) · 폴스타 0행(HTTP 0회) · 조회 오류(hostname_unverified ·
  anchor_unresolved) · 라벨 값 이스케이프(``=~`` 미사용) · nodename 모드 동일성(source 무시) ·
  도구 배선(source 인자 · ctx의 pool_manager).
- W4: OM 확장 시그니처의 ``source``(db_id) 해석 · ``ip_label`` 커버리지 · 교차 검증 up 재사용 ·
  ``label_mismatch`` 문구 라벨.
- W1: 앵커 설정 검증(무효 anchor_mode·라벨 이름) · ``PROMETHEUS_SOURCES`` env.
- W6: 운영 규약 Docker 픽스처 실 HTTP(``RUN_DOCKER_IT=1`` 옵트인).

HTTP는 ``httpx.MockTransport``, DB는 가짜 풀이다(실 Prometheus·DB 불필요).
"""

import json
import logging
import os
from datetime import datetime
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
        _apply_env_overrides,
        validate_prometheus_anchor,
    )

    from mcp_server import metric_source as ms
    from mcp_server import polestar_tools as pt
    from mcp_server import promql_tools as pq

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp/httpx 패키지가 설치되지 않음")

_URL = "http://prom.test:9090"
_OM_URL = "http://exporter.test:9100/metrics"
_GP = "polestar_cm_gp"
_YD = "polestar_cm_yd"
_B0 = "polestar_b0"
_METRIC = "node_load1"


# =====================================================================
# 테스트 유틸 — 가짜 풀 · Prometheus mock
# =====================================================================


class _FakePool:
    """DBPoolManager 대역 — source별 엔진과 조회 결과(행 목록 또는 예외)를 주입한다."""

    def __init__(self, engines: dict[str, str], rows: dict[str, Any] | None = None) -> None:
        self.engines = engines
        self.rows = rows or {}
        self.calls: list[tuple[str, str]] = []

    def is_source_active(self, name: str) -> bool:
        return name in self.engines

    def get_source_type(self, name: str) -> str:
        return self.engines[name]

    def get_active_sources(self) -> list[str]:
        return list(self.engines)

    async def execute(self, name: str, sql: str) -> list[dict[str, Any]]:
        self.calls.append((name, sql))
        value = self.rows.get(name, [])
        if isinstance(value, Exception):
            raise value
        return list(value)


def _row(name: str, hostname: str, ip: str, *, upper: bool = False) -> dict[str, Any]:
    row = {"resource_id": 1, "name": name, "hostname": hostname, "ipaddress": ip}
    return {k.upper(): v for k, v in row.items()} if upper else row


def _pool(rows: dict[str, Any] | None = None) -> "_FakePool":
    return _FakePool({_GP: "postgresql", _YD: "postgresql", _B0: "db2"}, rows)


def _up(instance: str, value: str = "1", **labels: str) -> dict[str, Any]:
    return {"metric": {"__name__": "up", "instance": instance, **labels},
            "value": [1700000000.0, value]}


def _sample(instance: str, value: str = "0.5") -> dict[str, Any]:
    return {"metric": {"__name__": _METRIC, "instance": instance},
            "value": [1700000000.0, value]}


class _Prom:
    """Prometheus MockTransport — 쿼리 문자열별 결과 · 라벨 값 목록."""

    def __init__(self, series: dict[str, list] | None = None,
                 labels: dict[str, list[str]] | None = None) -> None:
        self.series = series or {}
        self.labels = labels or {}
        self.requests: list[httpx.Request] = []

    def queries(self) -> list[str]:
        out = []
        for r in self.requests:
            q = parse_qs(urlparse(str(r.url)).query).get("query")
            out.append(q[0] if q else urlparse(str(r.url)).path)
        return out

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = urlparse(str(request.url))
        if url.path.startswith("/api/v1/label/"):
            label = url.path.split("/")[4]
            return httpx.Response(200, json={"status": "success",
                                             "data": self.labels.get(label, [])})
        query = parse_qs(url.query).get("query", [""])[0]
        kind = "matrix" if url.path.endswith("/query_range") else "vector"
        return httpx.Response(200, json={"status": "success", "data": {
            "resultType": kind, "result": list(self.series.get(query, []))}})


def _cfg(**kw: Any) -> "PrometheusConfig":
    base: dict[str, Any] = {"url": _URL, "anchor_mode": "polestar_host", "sources": [_GP, _YD]}
    base.update(kw)
    return PrometheusConfig(**base)


def _client(prom: "_Prom") -> "httpx.AsyncClient":
    return httpx.AsyncClient(base_url=_URL, transport=httpx.MockTransport(prom))


async def _instant(prom: "_Prom", pool: Any, hostname: str = "web01", *,
                   cfg: Any = None, source: str | None = None) -> dict:
    async with _client(prom) as c:
        out = await pq.run_metric_instant(cfg or _cfg(), hostname, _METRIC, client=c,
                                          pool=pool, source=source)
    return json.loads(out)


_H1 = 'up{hostname="web01",node_ip="10.0.0.1"}'
_H2 = 'up{node_ip="10.0.0.1"}'
_H3 = 'up{hostname="web01"}'


# =====================================================================
# W2 — 폴스타 앵커 해석 SQL · resolve
# =====================================================================


def test_anchor_sql_pg_dialect():
    sql = pt.build_anchor_lookup_sql(False, "web01")
    assert sql.startswith("SELECT\n")
    assert "FROM polestar.cmm_resource CR\n" in sql
    assert "CR.RESOURCE_TYPE = 'server.Server'" in sql
    assert "CR.DTIME IS NULL" in sql
    assert "(CR.NAME = 'web01' OR CR.HOSTNAME = 'web01')" in sql
    assert sql.rstrip().endswith("LIMIT 10")
    assert "FETCH FIRST" not in sql


def test_anchor_sql_db2_dialect():
    sql = pt.build_anchor_lookup_sql(True, "web01", limit=5)
    assert "FROM POLESTAR.cmm_resource CR\n" in sql
    assert sql.rstrip().endswith("FETCH FIRST 5 ROWS ONLY")
    assert "LIMIT" not in sql


def test_anchor_sql_escapes_value_and_is_select_only():
    sql = pt.build_anchor_lookup_sql(False, "o'neil")
    assert "CR.NAME = 'o''neil' OR CR.HOSTNAME = 'o''neil'" in sql
    first = sql.lstrip().split(None, 1)[0].upper()
    assert first == "SELECT"
    for word in ("INSERT", "UPDATE", "DELETE", "DROP"):
        assert word not in sql.upper()
    with pytest.raises(ValueError):
        pt.build_anchor_lookup_sql(False, "a\\b")


async def test_resolve_one_row_normalizes_db2_upper_columns():
    pool = _pool({_GP: [], _YD: [_row("vm-a", "web01", "10.0.0.1", upper=True)]})
    res = await pt.resolve_polestar_anchor(pool, " web01 ", sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_RESOLVED
    assert res.anchor == {"source": _YD, "name": "vm-a", "hostname": "web01",
                          "ipaddress": "10.0.0.1"}
    assert res.warning is None
    assert [c[0] for c in pool.calls] == [_GP, _YD]


async def test_resolve_zero_rows_is_not_found():
    res = await pt.resolve_polestar_anchor(_pool(), "web01", sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_NOT_FOUND


async def test_resolve_two_rows_is_ambiguous_without_merge():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")],
                  _YD: [_row("vm-b", "web01", "10.0.0.2")]})
    res = await pt.resolve_polestar_anchor(pool, "web01", sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_AMBIGUOUS
    assert [(c["source"], c["name"]) for c in res.candidates] == [(_GP, "vm-a"), (_YD, "vm-b")]
    assert res.anchor is None


async def test_resolve_all_errors_is_lookup_error():
    pool = _pool({_GP: RuntimeError("down"), _YD: RuntimeError("down")})
    res = await pt.resolve_polestar_anchor(pool, "web01", sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_LOOKUP_ERROR
    assert res.failed_sources == [_GP, _YD]
    assert _GP in (res.warning or "")


async def test_resolve_partial_error_with_row_resolves_with_warning():
    pool = _pool({_GP: RuntimeError("down"), _YD: [_row("vm-a", "web01", "10.0.0.1")]})
    res = await pt.resolve_polestar_anchor(pool, "web01", sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_RESOLVED
    assert res.failed_sources == [_GP]
    assert _GP in (res.warning or "")


async def test_resolve_partial_error_without_rows_is_lookup_error():
    pool = _pool({_GP: RuntimeError("down"), _YD: []})
    res = await pt.resolve_polestar_anchor(pool, "web01", sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_LOOKUP_ERROR


async def test_resolve_explicit_source_only_queries_that_source():
    pool = _pool({_YD: [_row("vm-a", "web01", "10.0.0.1")]})
    res = await pt.resolve_polestar_anchor(pool, "web01", source=_YD, sources=[_GP, _YD])
    assert res.status == pt.ANCHOR_RESOLVED
    assert [c[0] for c in pool.calls] == [_YD]


async def test_resolve_explicit_source_unknown_or_not_polestar_raises():
    pool = _FakePool({_GP: "postgresql", "itam": "mariadb"})
    with pytest.raises(ValueError):
        await pt.resolve_polestar_anchor(pool, "web01", source="nope")
    with pytest.raises(ValueError):
        await pt.resolve_polestar_anchor(pool, "web01", source="itam")
    assert pool.calls == []


# =====================================================================
# W3 — 사다리 조립 (순수 함수)
# =====================================================================


def test_anchor_ladder_three_steps_and_ip_blank_skips():
    cfg = _cfg()
    assert pq.build_anchor_ladder(cfg, "web01", "10.0.0.1") == [
        ("hostname+node_ip", '{hostname="web01",node_ip="10.0.0.1"}'),
        ("node_ip", '{node_ip="10.0.0.1"}'),
        ("hostname", '{hostname="web01"}'),
    ]
    assert pq.build_anchor_ladder(cfg, "web01", " ") == [("hostname", '{hostname="web01"}')]
    assert pq.build_anchor_ladder(cfg, "", "10.0.0.1") == [("node_ip", '{node_ip="10.0.0.1"}')]


def test_anchor_ladder_uses_configured_label_names():
    cfg = _cfg(host_label="host", ip_label="addr")
    steps = pq.build_anchor_ladder(cfg, "web01", "10.0.0.1")
    assert steps[0][1] == '{host="web01",addr="10.0.0.1"}'
    assert steps[0][0] == "hostname+node_ip"  # matched_by 어휘는 고정


def test_label_matcher_escapes_quotes_and_backslashes_without_regex():
    m = pq.build_label_matcher([("hostname", 'a"b\\c\nd')])
    assert m == '{hostname="a\\"b\\\\cd"}'
    assert "=~" not in m


# =====================================================================
# W3 — 사다리 판정 · 반환 계약
# =====================================================================


async def test_step1_hostname_and_ip_established():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")],
                  'node_load1{hostname="web01",node_ip="10.0.0.1"}': [_sample("10.0.0.1:9100")]})
    got = await _instant(prom, pool)
    assert prom.queries() == [_H1, 'node_load1{hostname="web01",node_ip="10.0.0.1"}']
    assert got["matched_by"] == "hostname+node_ip"
    assert got["tried"] == [_H1]
    assert got["anchor"] == {"source": _GP, "name": "vm-a", "hostname": "web01",
                             "ipaddress": "10.0.0.1"}
    assert got["query"] == 'node_load1{hostname="web01",node_ip="10.0.0.1"}'
    assert got["result_count"] == 1
    assert "reason" not in got and "hint" not in got and "error" not in got


async def test_step2_ip_only_when_hostname_stale():
    """VM 교체로 폴스타 hostname이 Prometheus와 다르면 2단(IP)에서 성립한다."""
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H2: [_up("10.0.0.1:9100")],
                  'node_load1{node_ip="10.0.0.1"}': [_sample("10.0.0.1:9100")]})
    got = await _instant(prom, pool, "vm-a")
    assert prom.queries() == [_H1, _H2, 'node_load1{node_ip="10.0.0.1"}']
    assert got["matched_by"] == "node_ip"
    assert got["tried"] == [_H1, _H2]


async def test_step3_hostname_when_ip_blank_skips_steps_1_2():
    pool = _pool({_GP: [_row("vm-a", "web01", "")]})
    prom = _Prom({_H3: [_up("10.0.0.1:9100")],
                  'node_load1{hostname="web01"}': [_sample("10.0.0.1:9100")]})
    got = await _instant(prom, pool)
    assert prom.queries() == [_H3, 'node_load1{hostname="web01"}']
    assert got["matched_by"] == "hostname"
    assert got["tried"] == [_H3]


async def test_step3_fallback_after_steps_1_2_empty():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H3: [_up("10.0.0.9:9100")],
                  'node_load1{hostname="web01"}': [_sample("10.0.0.9:9100")]})
    got = await _instant(prom, pool)
    assert got["matched_by"] == "hostname"
    assert len(prom.requests) == 4  # up 3회 + 본 조회 1회 (최대)


async def test_prometheus_ambiguous_on_hostname_group_returns_without_merge():
    pool = _pool({_GP: [_row("vm-a", "phys01", "")]})
    prom = _Prom({'up{hostname="phys01"}': [_up("10.0.0.1:9100"), _up("10.0.0.2:9100")]})
    got = await _instant(prom, pool, "phys01")
    assert got["reason"] == pq.REASON_PROMETHEUS_AMBIGUOUS
    assert got["hint"] == pq._REASON_HINTS[pq.REASON_PROMETHEUS_AMBIGUOUS]
    assert got["instances"] == ["10.0.0.1:9100", "10.0.0.2:9100"]
    assert got["result_count"] == 0 and got["data"]["result"] == []
    assert len(prom.requests) == 1  # 본 조회 없음


async def test_ambiguous_at_step1_stops_immediately():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("a:1"), _up("b:1")]})
    got = await _instant(prom, pool)
    assert got["reason"] == pq.REASON_PROMETHEUS_AMBIGUOUS
    assert prom.queries() == [_H1]


async def test_not_monitored_when_all_steps_empty():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom()
    got = await _instant(prom, pool)
    assert "error" not in got
    assert got["reason"] == pq.REASON_NOT_MONITORED
    assert got["hint"] == "Prometheus 미수집 서버다. 폴스타 지표(polestar_metric_trend)로 확인하라"
    assert got["tried"] == [_H1, _H2, _H3]
    assert got["matched_by"] is None
    assert got["anchor"]["name"] == "vm-a"
    assert got["source_kind"] == "prometheus" and got["result_count"] == 0
    assert len(prom.requests) == 3


async def test_unsupported_zone_blocks_before_db_and_http():
    pool = _pool({_B0: [_row("vm-b0", "web01", "10.0.0.1")]})
    prom = _Prom()
    got = await _instant(prom, pool, source=_B0)
    assert got["reason"] == pq.REASON_UNSUPPORTED_ZONE
    assert prom.requests == [] and pool.calls == []
    assert got["tried"] == [] and got["anchor"] is None


async def test_empty_sources_means_no_zone_restriction():
    pool = _pool({_B0: [_row("vm-b0", "web01", "")]})
    prom = _Prom({_H3: [_up("x:1")]})
    got = await _instant(prom, pool, cfg=_cfg(sources=[]), source=_B0)
    assert got["matched_by"] == "hostname"


async def test_polestar_not_found_skips_prometheus():
    prom = _Prom()
    got = await _instant(prom, _pool())
    assert got["reason"] == pq.REASON_POLESTAR_NOT_FOUND
    assert got["hint"] == pq._REASON_HINTS[pq.REASON_POLESTAR_NOT_FOUND]
    assert "source를 지정했다면 빼고 다시 불러라" in got["hint"]
    assert "알람 dbId" in got["hint"] and "polestar_metric_trend" in got["hint"]
    assert prom.requests == []


async def test_polestar_ambiguous_returns_candidates():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")],
                  _YD: [_row("vm-b", "web01", "10.0.0.2")]})
    prom = _Prom()
    got = await _instant(prom, pool)
    assert got["reason"] == pq.REASON_POLESTAR_AMBIGUOUS
    assert got["candidates"] == [{"source": _GP, "name": "vm-a"},
                                 {"source": _YD, "name": "vm-b"}]
    assert prom.requests == []


async def test_lookup_error_tries_hostname_only_unverified():
    pool = _pool({_GP: RuntimeError("db down"), _YD: RuntimeError("db down")})
    prom = _Prom({_H3: [_up("x:1")], 'node_load1{hostname="web01"}': [_sample("x:1")]})
    got = await _instant(prom, pool)
    assert prom.queries() == [_H3, 'node_load1{hostname="web01"}']
    assert got["matched_by"] == pq.MATCHED_BY_HOST_UNVERIFIED
    assert got["anchor"] is None
    assert got["warning"].startswith(pq.LOOKUP_ERROR_WARNING)
    assert _GP in got["warning"]
    assert "reason" not in got


async def test_lookup_error_zero_is_anchor_unresolved_not_not_monitored():
    pool = _pool({_GP: RuntimeError("db down"), _YD: RuntimeError("db down")})
    prom = _Prom()
    got = await _instant(prom, pool)
    assert got["reason"] == pq.REASON_ANCHOR_UNRESOLVED
    assert got["matched_by"] == pq.MATCHED_BY_HOST_UNVERIFIED
    assert got["tried"] == [_H3]


async def test_lookup_error_keeps_ambiguity_check():
    pool = _pool({_GP: RuntimeError("db down"), _YD: RuntimeError("db down")})
    prom = _Prom({_H3: [_up("a:1"), _up("b:1")]})
    got = await _instant(prom, pool)
    assert got["reason"] == pq.REASON_PROMETHEUS_AMBIGUOUS


async def test_partial_lookup_failure_carries_warning():
    pool = _pool({_GP: RuntimeError("db down"), _YD: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")]})
    got = await _instant(prom, pool)
    assert got["matched_by"] == "hostname+node_ip"
    assert _GP in got["warning"]
    assert pq.LOOKUP_ERROR_WARNING not in got["warning"]


async def test_unknown_source_is_call_error():
    prom = _Prom()
    got = await _instant(prom, _pool(), cfg=_cfg(sources=[]), source="nope")
    assert set(got) == {"error"}
    assert prom.requests == []


async def test_backslash_hostname_is_call_error_not_lookup_error():
    prom = _Prom()
    got = await _instant(prom, _pool(), "a\\b")
    assert set(got) == {"error"}
    assert prom.requests == []


async def test_anchor_values_escaped_in_selectors():
    pool = _pool({_GP: [_row("vm-a", 'we"b', "10.0.0.1")]})
    prom = _Prom()
    await _instant(prom, pool)
    queries = prom.queries()
    assert queries[0] == 'up{hostname="we\\"b",node_ip="10.0.0.1"}'
    assert all("=~" not in q for q in queries)


async def test_blank_anchor_row_established_by_input_hostname():
    """폴스타 행에 OS 호스트명·IP가 비면 입력값 단독 3단 — 성립하면 hostname_unverified · anchor는 그 행."""
    pool = _pool({_GP: [_row("vm-a", "", "")]})
    prom = _Prom({'up{hostname="vm-a"}': [_up("x:1")]})
    got = await _instant(prom, pool, "vm-a")
    assert got["matched_by"] == pq.MATCHED_BY_HOST_UNVERIFIED
    assert got["anchor"]["name"] == "vm-a"
    assert got["warning"] == pq.BLANK_ANCHOR_WARNING
    assert prom.queries() == ['up{hostname="vm-a"}', 'node_load1{hostname="vm-a"}']


def test_anchor_unresolved_hint_covers_both_causes():
    hint = pq._REASON_HINTS[pq.REASON_ANCHOR_UNRESOLVED]
    assert "서버 신원(OS 호스트명·IP)" in hint and "관제 미등록으로 단정하지 말라" in hint


async def test_anchor_mode_rejects_overlong_hostname_and_source():
    pool = _pool()
    prom = _Prom()
    long_host = await _instant(prom, pool, "h" * 256)
    long_src = await _instant(prom, pool, source="s" * 65, cfg=_cfg(sources=[]))
    assert set(long_host) == {"error"} and set(long_src) == {"error"}
    assert prom.requests == [] and pool.calls == []
    ok = await _instant(prom, pool, "h" * 255)  # 경계값은 통과(0행 → polestar_not_found)
    assert ok["reason"] == pq.REASON_POLESTAR_NOT_FOUND


async def test_nodename_mode_has_no_length_limit():
    prom = _Prom()
    got = await _instant(prom, None, "h" * 300, cfg=PrometheusConfig(url=_URL))
    assert "error" not in got
    assert prom.queries() == [f'node_load1{{nodename="{"h" * 300}"}}']


async def test_anchor_logs_quote_and_truncate_hostname(caplog):
    """앵커 경로 로그는 hostname을 repr 인용하고 128자로 자른다(개행 주입·폭주 방지)."""
    caplog.set_level(logging.INFO)
    pool = _pool({_GP: RuntimeError("down"), _YD: RuntimeError("down")})
    await _instant(_Prom(), pool, "a" * 200)
    await _instant(_Prom(), _pool(), "bad" + chr(10) + "line")
    # 대상: 폴스타 해석 경고 · 미조회 감사 줄(셀렉터 감사는 라벨 이스케이프가 개행을 지운다)
    messages = [r.getMessage() for r in caplog.records
                if r.name == "mcp_server.polestar_tools" or "(미조회)" in r.getMessage()]
    assert any("앵커 해석 실패" in m for m in messages)
    assert not any("a" * 129 in m for m in messages)
    assert any("hostname='bad\\nline'" in m for m in messages)
    assert not any(chr(10) in m for m in messages)


async def test_missing_pool_in_anchor_mode_is_error():
    got = await _instant(_Prom(), None)
    assert "error" in got


async def test_url_unset_in_anchor_mode_matches_legacy_error_string():
    out = await pq.run_metric_instant(_cfg(url=""), "web01", _METRIC, pool=_pool())
    assert out == pq._URL_UNSET_ERROR


async def test_prometheus_http_error_on_probe_is_error():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})

    def handler(request):
        return httpx.Response(500, json={"status": "error"})

    async with httpx.AsyncClient(base_url=_URL, transport=httpx.MockTransport(handler)) as c:
        got = json.loads(await pq.run_metric_instant(_cfg(), "web01", _METRIC, client=c,
                                                     pool=pool))
    assert set(got) == {"error"}


async def test_range_anchor_mode_probes_at_reference_time_and_queries_range():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H2: [_up("10.0.0.1:9100")]})
    async with _client(prom) as c:
        got = json.loads(await pq.run_metric_range(
            _cfg(), "web01", _METRIC, "1h", "60s", client=c, pool=pool,
            reference_time="2026-10-20T00:00:00+00:00",
        ))
    end = f"{datetime.fromisoformat('2026-10-20T00:00:00+00:00').timestamp():.3f}"
    probe_params = parse_qs(urlparse(str(prom.requests[0].url)).query)
    assert probe_params["time"] == [end]
    main = prom.requests[-1]
    assert urlparse(str(main.url)).path == "/api/v1/query_range"
    params = parse_qs(urlparse(str(main.url)).query)
    assert params["query"] == ['node_load1{node_ip="10.0.0.1"}']
    assert params["end"] == [end] and params["step"] == ["60"]
    assert got["matched_by"] == "node_ip"
    assert got["window"] == "1h" and got["step"] == "60s"
    assert got["endpoint"] == "/api/v1/query_range"


async def test_range_not_monitored_reply_is_matrix_shape():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    async with _client(_Prom()) as c:
        got = json.loads(await pq.run_metric_range(_cfg(), "web01", _METRIC, "1h", "60s",
                                                   client=c, pool=pool))
    assert got["reason"] == pq.REASON_NOT_MONITORED
    assert got["data"] == {"resultType": "matrix", "result": []}


async def test_audit_log_carries_reason_and_matched_by(caplog):
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    caplog.set_level(logging.INFO, logger="mcp_server.promql_tools")
    await _instant(_Prom({_H1: [_up("10.0.0.1:9100")]}), pool)
    await _instant(_Prom(), pool)
    text = caplog.text
    assert "matched_by=hostname+node_ip" in text
    assert "reason=prometheus_not_monitored" in text


# =====================================================================
# W3 — nodename 모드 동일성 · 도구 배선
# =====================================================================


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _ctx(config: Any, pool: Any = None) -> SimpleNamespace:
    lifespan: dict[str, Any] = {"config": config}
    if pool is not None:
        lifespan["pool_manager"] = pool
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context=lifespan))


@pytest.mark.parametrize("tool", ["prom_metric_instant", "prom_metric_range"])
async def test_nodename_mode_ignores_source(monkeypatch, tool):
    prom = _Prom({'node_load1{nodename="web01"}': [_sample("x:1")]})
    monkeypatch.setattr(pq, "_now_iso", lambda: "2026-10-20T00:00:00+00:00")
    monkeypatch.setattr(pq, "make_client", lambda cfg: httpx.AsyncClient(
        base_url=cfg.url, transport=httpx.MockTransport(prom)))
    monkeypatch.setattr(pq.time, "time", lambda: 1760918400.0)
    capture = _CaptureMCP()
    pq.register_promql_tools(capture)
    pool = _pool()
    ctx = _ctx(AppServerConfig(prometheus=PrometheusConfig(url=_URL)), pool)
    plain = await capture.tools[tool](hostname="web01", metric=_METRIC, ctx=ctx)
    plain_reqs = [str(r.url) for r in prom.requests]
    prom.requests.clear()
    with_src = await capture.tools[tool](hostname="web01", metric=_METRIC, source=_B0, ctx=ctx)
    assert with_src == plain
    assert [str(r.url) for r in prom.requests] == plain_reqs
    assert pool.calls == []
    assert "matched_by" not in json.loads(plain)


@pytest.mark.parametrize("tool", ["prom_metric_instant", "prom_metric_range"])
async def test_registered_tools_use_ctx_pool_and_source(monkeypatch, tool):
    pool = _pool({_YD: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")]})
    monkeypatch.setattr(pq, "make_client", lambda cfg: httpx.AsyncClient(
        base_url=cfg.url, transport=httpx.MockTransport(prom)))
    capture = _CaptureMCP()
    pq.register_promql_tools(capture)
    ctx = _ctx(AppServerConfig(prometheus=_cfg()), pool)
    got = json.loads(await capture.tools[tool](hostname="web01", metric=_METRIC, source=_YD,
                                               ctx=ctx))
    assert got["matched_by"] == "hostname+node_ip"
    assert [c[0] for c in pool.calls] == [_YD]


# =====================================================================
# W4 — OM 확장 시그니처 · 커버리지 · 교차 검증
# =====================================================================


def _om_cfg(**kw: Any) -> "OpenMetricsConfig":
    return OpenMetricsConfig(expose_openmetrics_tools=True,
                             targets=[OpenMetricsTarget(hostname="web01", url=_OM_URL)], **kw)


class _Exporter:
    def __init__(self, value: str = "0.5") -> None:
        self.text = f"# HELP {_METRIC} load.\n# TYPE {_METRIC} gauge\n{_METRIC} {value}\n"
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, content=self.text.encode(),
                              headers={"content-type": "text/plain; version=0.0.4"})


@pytest.fixture(autouse=True)
def _fresh_coverage():
    ms.reset_coverage_cache()
    yield
    ms.reset_coverage_cache()


async def _with_source(prom: "_Prom", pool: Any, source: str, *, om_cfg: Any = None,
                       exp: Any = None, cfg: Any = None, cross: bool = False) -> dict:
    exp = exp or _Exporter()
    async with _client(prom) as pc, httpx.AsyncClient(transport=httpx.MockTransport(exp)) as oc:
        out = await ms.run_metric_instant_with_source(
            cfg or _cfg(), om_cfg or _om_cfg(), "web01", _METRIC, source, cross,
            prom_client=pc, om_client=oc, pool=pool,
        )
    return json.loads(out)


async def test_om_signature_source_db_id_is_polestar_source_in_anchor_mode():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")], _YD: [_row("x", "web01", "1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")]})
    got = await _with_source(prom, pool, _GP)  # 정책 off → 종전 호출 경로 + 앵커
    assert got["matched_by"] == "hostname+node_ip"
    assert [c[0] for c in pool.calls] == [_GP]
    assert "fallback_reason" not in got


async def test_om_signature_metric_source_values_still_work_in_anchor_mode():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    got = await _with_source(_Prom({_H1: [_up("10.0.0.1:9100")]}), pool, "prometheus")
    assert got["matched_by"] == "hostname+node_ip"
    assert got["fallback_reason"] is None


async def test_om_signature_unknown_source_still_rejected_in_nodename_mode():
    got = await _with_source(_Prom(), None, _GP, cfg=PrometheusConfig(url=_URL))
    assert "source는" in got["error"]


async def test_om_signature_zone_blocked_in_anchor_mode():
    pool = _pool()
    prom = _Prom()
    got = await _with_source(prom, pool, _B0)
    assert got["reason"] == pq.REASON_UNSUPPORTED_ZONE
    assert prom.requests == [] and pool.calls == []


async def test_coverage_uses_ip_label_in_anchor_mode():
    prom = _Prom(labels={"node_ip": ["10.0.0.1"], "nodename": ["web01"]})
    async with _client(prom) as c:
        cov = await ms.prometheus_coverage(_cfg(), 60, client=c)
    assert cov == frozenset({"10.0.0.1"})
    assert [urlparse(str(r.url)).path for r in prom.requests] == ["/api/v1/label/node_ip/values"]


async def test_coverage_nodename_mode_unchanged():
    prom = _Prom(labels={"node_ip": ["10.0.0.1"], "nodename": ["web01"]})
    async with _client(prom) as c:
        cov = await ms.prometheus_coverage(PrometheusConfig(url=_URL), 60, client=c)
    assert cov == frozenset({"web01"})
    assert [urlparse(str(r.url)).path for r in prom.requests] == ["/api/v1/label/nodename/values"]


async def test_auto_not_covered_by_ip_falls_to_exporter_with_single_db_lookup():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom(labels={"node_ip": ["10.9.9.9"]})
    got = await _with_source(prom, pool, "auto", om_cfg=_om_cfg(fallback_policy="on_empty"))
    assert got["fallback_reason"] == ms.FALLBACK_HOST_NOT_COVERED
    assert got["source_kind"] == "openmetrics"
    assert len(pool.calls) == 2  # 대상 source 2개 · 해석 1회


async def test_auto_covered_by_ip_uses_prometheus_and_reuses_resolution():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")],
                  'node_load1{hostname="web01",node_ip="10.0.0.1"}': [_sample("10.0.0.1:9100")]},
                 labels={"node_ip": ["10.0.0.1"]})
    got = await _with_source(prom, pool, "auto", om_cfg=_om_cfg(fallback_policy="on_empty"))
    assert got["source_kind"] == "prometheus"
    assert got["matched_by"] == "hostname+node_ip"
    assert got["fallback_reason"] is None
    assert len(pool.calls) == 2  # 해석 1회(두 source) — 본 조회가 다시 묻지 않는다


async def test_catalog_availability_uses_anchor_ip():
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom(labels={"node_ip": ["10.0.0.1"]})
    async with _client(prom) as pc, \
            httpx.AsyncClient(transport=httpx.MockTransport(_Exporter())) as oc:
        got = json.loads(await ms.run_catalog_with_sources(
            _cfg(), _om_cfg(), "web01", prom_client=pc, om_client=oc, pool=pool))
    assert got["prometheus_coverage"] == "covered"
    assert got["sources_available"] == ["prometheus", "exporter"]


async def test_catalog_availability_unknown_when_anchor_unresolved():
    prom = _Prom(labels={"node_ip": ["10.0.0.1"]})
    async with _client(prom) as pc, \
            httpx.AsyncClient(transport=httpx.MockTransport(_Exporter())) as oc:
        got = json.loads(await ms.run_catalog_with_sources(
            _cfg(), _om_cfg(), "web01", prom_client=pc, om_client=oc, pool=_pool()))
    assert got["prometheus_coverage"] == "unknown"
    assert prom.requests == []


async def test_cross_check_anchor_mode_not_monitored_is_inconclusive():
    """사다리 전 단 0건(reason 있음)은 up 근거로 판정하지 않는다 — 판정 보류 · 추가 HTTP 없음."""
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom(labels={"hostname": ["WEB01.corp"]})
    got = await _with_source(prom, pool, "auto", cross=True)
    cc = got["cross_check"]
    assert cc["verdict"] is None
    assert cc["inconclusive_reason"] == pq.REASON_NOT_MONITORED
    queries = prom.queries()
    assert not any(q.startswith("up{nodename") for q in queries)
    assert queries == [_H1, _H2, _H3]
    assert cc["round_trips"] == len(prom.requests) + 1  # + exporter


async def test_cross_check_anchor_mode_label_in_diagnosis_when_metric_absent():
    """사다리는 성립했는데 본 조회만 0건이면 up 재사용 + host_label 후보로 label_mismatch."""
    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom({_H1: [_up("10.0.0.1:9100")]}, labels={"hostname": ["WEB01.corp"]})
    got = await _with_source(prom, pool, "auto", cross=True)
    cc = got["cross_check"]
    assert cc["verdict"] == ms.VERDICT_LABEL_MISMATCH
    assert cc["diagnosis"] == "스크레이프 설정 hostname 정규화 필요 — 후보: WEB01.corp"
    assert prom.queries() == [_H1, 'node_load1{hostname="web01",node_ip="10.0.0.1"}',
                              "/api/v1/label/hostname/values"]
    assert cc["round_trips"] == 4  # up 1 + 본 조회 1 + 커버리지 1 + exporter 1


async def test_om_fallback_tried_carries_prometheus_reason():
    """강등 뒤 exporter도 실패하면 tried의 Prometheus 항목에 앵커 reason이 함께 실린다."""

    class _DownExporter(_Exporter):
        def __call__(self, request):
            self.requests.append(request)
            return httpx.Response(500, content=b"down")

    pool = _pool({_GP: [_row("vm-a", "web01", "10.0.0.1")]})
    prom = _Prom(labels={"node_ip": ["10.0.0.1"]})
    got = await _with_source(prom, pool, "auto", om_cfg=_om_cfg(fallback_policy="on_empty"),
                             exp=_DownExporter())
    assert got["error"] == "조회 가능한 소스 없음"
    assert got["tried"][0] == {"source": "prometheus", "outcome": "empty", "error": None,
                               "reason": pq.REASON_NOT_MONITORED}


def test_label_mismatch_diagnosis_default_label_is_nodename():
    text = ms.diagnosis_for(ms.VERDICT_LABEL_MISMATCH, {"candidates": ["a"]})
    assert text == "스크레이프 설정 nodename 정규화 필요 — 후보: a"


# =====================================================================
# W1 — 설정 검증 · env
# =====================================================================


def test_validate_anchor_rejects_invalid_mode_and_label():
    with pytest.raises(ValueError):
        validate_prometheus_anchor(PrometheusConfig(anchor_mode="regex"))
    with pytest.raises(ValueError):
        validate_prometheus_anchor(PrometheusConfig(host_label='host"}'))
    with pytest.raises(ValueError):
        validate_prometheus_anchor(PrometheusConfig(ip_label="node-ip"))
    cfg = PrometheusConfig(anchor_mode=" Polestar_Host ", sources=[" a ", ""])
    validate_prometheus_anchor(cfg)
    assert cfg.anchor_mode == "polestar_host" and cfg.sources == ["a"]


def test_label_name_check_is_fullmatch():
    """라벨 이름 검증은 끝 개행 등 꼬리 문자도 거부한다(fullmatch)."""
    import mcp_server.config as mc

    assert mc._PROM_LABEL_NAME_RE.fullmatch("hostname" + chr(10)) is None
    assert mc._PROM_LABEL_NAME_RE.fullmatch("node_ip") is not None


def test_toml_sources_must_be_array(tmp_path):
    from mcp_server.config import _load_toml

    bad = tmp_path / "bad.toml"
    bad.write_text('[prometheus]' + chr(10) + 'sources = "polestar_cm_gp"' + chr(10),
                   encoding="utf-8")
    with pytest.raises(ValueError):
        _load_toml(bad)
    good = tmp_path / "good.toml"
    good.write_text('[prometheus]' + chr(10) + 'sources = ["polestar_cm_gp"]' + chr(10),
                    encoding="utf-8")
    assert _load_toml(good).prometheus.sources == [_GP]


def test_default_anchor_config_is_nodename():
    cfg = PrometheusConfig()
    assert (cfg.anchor_mode, cfg.host_label, cfg.ip_label, cfg.instance_label, cfg.sources) == (
        "nodename", "hostname", "node_ip", "instance", [])


@pytest.mark.parametrize("raw", ['["polestar_cm_gp","polestar_cm_yd"]',
                                 "polestar_cm_gp, polestar_cm_yd"])
def test_env_prometheus_sources(monkeypatch, raw):
    monkeypatch.setenv("PROMETHEUS_SOURCES", raw)
    monkeypatch.setenv("PROMETHEUS_ANCHOR_MODE", "polestar_host")
    config = AppServerConfig()
    _apply_env_overrides(config)
    assert config.prometheus.sources == [_GP, _YD]
    assert config.prometheus.anchor_mode == "polestar_host"


def test_env_prometheus_sources_rejects_non_array_json(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_SOURCES", '["a"')
    with pytest.raises(ValueError):
        _apply_env_overrides(AppServerConfig())


def test_env_invalid_anchor_mode_rejected(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_ANCHOR_MODE", "fuzzy")
    with pytest.raises(ValueError):
        _apply_env_overrides(AppServerConfig())


# =====================================================================
# W6 — 운영 규약 Docker 픽스처 실 HTTP (옵트인 RUN_DOCKER_IT=1)
# =====================================================================
#
# testdata/prometheus/prometheus.yml의 job ``ops_node``(hostname·node_ip·instance·resources 규약)를
# 실 Prometheus로 조회해 사다리 1·2·3단과 「모호」 판정을 단언한다. 폴스타 쪽은 가짜 풀이다
# (픽스처 PG에는 운영 규약 행이 없다). 값은 픽스처 결정값이다.


def _it_prom_url() -> str:
    return os.environ.get("DOCKER_IT_PROM_URL", "http://localhost:9190")


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason="Docker Prometheus 픽스처 미기동 — RUN_DOCKER_IT=1로 옵트인 시에만 실행(plans/189 W6).",
)
class TestDockerOperationalLabels:
    """운영 규약 타깃 라벨(job ops_node) 대상 사다리 실 HTTP."""

    async def _call(self, row: dict[str, Any]) -> dict:
        cfg = PrometheusConfig(url=_it_prom_url(), anchor_mode="polestar_host",
                               sources=[_GP], query_timeout=10)
        pool = _FakePool({_GP: "postgresql"}, {_GP: [row]})
        return json.loads(await pq.run_metric_instant(cfg, row["name"], "mock_oom_kills_total",
                                                      pool=pool))

    async def test_step1_regular_server(self):
        got = await self._call(_row("svr-ops-01", "ops-01", "10.10.0.1"))
        assert got["matched_by"] == "hostname+node_ip", got
        assert got["data"]["result"][0]["value"][1] == "3"

    async def test_stale_resources_still_matches_by_os_labels(self):
        # resources(VM name)가 낡아도 앵커는 OS 라벨이라 폴스타 등록명으로 1단에서 성립한다.
        got = await self._call(_row("svr-ops-02", "ops-02", "10.10.0.2"))
        assert got["matched_by"] == "hostname+node_ip", got

    async def test_step2_ip_when_hostname_differs(self):
        # 폴스타 hostname이 Prometheus와 다르면(교체 전 값) IP만으로 2단에서 성립한다.
        got = await self._call(_row("svr-ops-02", "ops-02-old", "10.10.0.2"))
        assert got["matched_by"] == "node_ip", got

    async def test_step3_hostname_when_ip_blank(self):
        got = await self._call(_row("svr-ops-01", "ops-01", ""))
        assert got["matched_by"] == "hostname", got

    async def test_ambiguous_physical_hostname_group(self):
        got = await self._call(_row("svr-ops-phys", "ops-phys", ""))
        assert got["reason"] == pq.REASON_PROMETHEUS_AMBIGUOUS, got
        assert got["instances"] == ["10.10.0.3:9100", "10.10.0.4:9100"]

    async def test_not_monitored(self):
        got = await self._call(_row("svr-ops-none", "ops-none", "10.10.9.9"))
        assert got["reason"] == pq.REASON_NOT_MONITORED, got
