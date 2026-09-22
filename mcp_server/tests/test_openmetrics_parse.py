"""openmetrics.py 테스트 — 노출 형식 파서·정규화 (plans/92 O1 · §4.2 · I-8).

골든 픽스처 3종(``mcp_server/testdata/openmetrics/``)으로 OpenMetrics 1.0 strict·0.0.4 관대 파싱,
instant vector 정규화(``nodename`` 주입·``exported_nodename`` 이동·``_created`` 제외·필터·절단),
값 표기, 카탈로그, 타깃 신원 판정을 고정한다. 이 모듈은 ``mcp``를 임포트하지 않으므로
``mcp`` 부재로 skip되지 않는다(패턴 동일성 1건만 ``mcp``가 필요하다).
"""

import math
from pathlib import Path

import pytest

from mcp_server import openmetrics as om

_FIXTURES = Path(__file__).resolve().parents[1] / "testdata" / "openmetrics"
_OM_CT = "application/openmetrics-text; version=1.0.0; charset=utf-8"
_TEXT_CT = "text/plain; version=0.0.4"
_HOST = "web-app-01"
_SCRAPED_AT = 1700000100.0


def _read(name: str) -> str:
    return (_FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def om_families():
    return om.parse_exposition(_read("om_1_0_full.txt"), _OM_CT)


@pytest.fixture(scope="module")
def text_families():
    return om.parse_exposition(_read("text_0_0_4.txt"), _TEXT_CT)


def _vector(families, **kwargs):
    kwargs.setdefault("hostname", _HOST)
    kwargs.setdefault("scraped_at", _SCRAPED_AT)
    kwargs.setdefault("max_series", 200)
    return om.to_instant_vector(families, **kwargs)


def _series(result, name):
    return [r for r in result.data["result"] if r["metric"]["__name__"] == name]


# =====================================================================
# 파싱 — 형식 선택·전 타입·오류
# =====================================================================


def test_om_full_parses_all_types(om_families):
    types = {f.name: f.type for f in om_families}
    assert types == {
        "app_temperature_celsius": "gauge",
        "app_requests": "counter",
        "app_build": "info",
        "app_feature_state": "stateset",
        "app_latency_seconds": "histogram",
        "app_rpc_seconds": "summary",
        "app_mystery": "unknown",
        "app_job_created": "gauge",
        "app_labeled": "gauge",
        "node_uname_info": "gauge",
    }
    units = {f.name: f.unit for f in om_families}
    assert units["app_temperature_celsius"] == "celsius"
    assert units["app_latency_seconds"] == "seconds"


def test_om_missing_eof_raises_parse_error():
    with pytest.raises(om.ExpositionParseError) as excinfo:
        om.parse_exposition(_read("truncated_no_eof.txt"), _OM_CT)
    assert "EOF" in str(excinfo.value)
    assert "OpenMetrics 1.0" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ValueError)
    assert isinstance(excinfo.value, ValueError)  # 호출부가 ValueError로도 잡을 수 있다


def test_text_0_0_4_garbage_wrapped_with_reason():
    with pytest.raises(om.ExpositionParseError) as excinfo:
        om.parse_exposition('x{a="1"} notanumber\n', _TEXT_CT)
    assert "Prometheus text 0.0.4" in str(excinfo.value)
    assert "notanumber" in str(excinfo.value)
    assert excinfo.value.__cause__ is not None


@pytest.mark.parametrize(
    "content_type",
    [
        "application/openmetrics-text; version=1.0.0; charset=utf-8",
        "Application/OpenMetrics-Text;version=1.0.0",
        "APPLICATION/OPENMETRICS-TEXT",
        "  application/openmetrics-text ; version=0.0.1",
    ],
)
def test_openmetrics_content_type_case_and_params_select_strict(content_type):
    with pytest.raises(om.ExpositionParseError):
        om.parse_exposition(_read("truncated_no_eof.txt"), content_type)


@pytest.mark.parametrize(
    "content_type",
    ["text/plain; version=0.0.4", "text/plain", "", "application/openmetrics-textual"],
)
def test_other_content_types_use_lax_0_0_4(content_type):
    # 0.0.4 관대 파서는 # EOF를 요구하지 않는다
    families = om.parse_exposition(_read("truncated_no_eof.txt"), content_type)
    samples = {s.name: s.value for f in families for s in f.samples}
    assert samples == {"app_temperature_celsius": 21.5, "app_requests_total": 1027}


def test_crlf_normalized_for_openmetrics_strict():
    crlf = _read("om_1_0_full.txt").replace("\n", "\r\n")
    assert "\r\n" in crlf
    families = om.parse_exposition(crlf, _OM_CT)
    assert len(families) == 10
    temps = _series(_vector(families, metric="app_temperature_celsius"), "app_temperature_celsius")
    assert [r["value"][1] for r in temps] == ["21.5", "23.25"]


def test_crlf_normalized_for_text_0_0_4():
    crlf = _read("text_0_0_4.txt").replace("\n", "\r\n")
    result = _vector(om.parse_exposition(crlf, _TEXT_CT), metric="mock_cpu_usage_percent")
    assert [r["value"][1] for r in result.data["result"]] == ["97.5", "1.5"]


# =====================================================================
# 0.0.4 — mock 고정값·타임스탬프·이름 보존
# =====================================================================


def test_text_0_0_4_mock_fixed_values(text_families):
    result = _vector(text_families, prefix="mock_")
    values = {
        (r["metric"]["__name__"], r["metric"].get("mode")): r["value"][1]
        for r in result.data["result"]
    }
    assert values[("mock_cpu_usage_percent", "user")] == "97.5"
    assert values[("mock_cpu_usage_percent", "system")] == "1.5"
    assert values[("mock_memory_used_bytes", None)] == "8589934592"
    assert values[("mock_oom_kills_total", None)] == "3"


def test_text_0_0_4_ms_timestamp_converted_to_seconds(text_families):
    result = _vector(text_families, metric="mock_boot_time_seconds")
    (boot,) = _series(result, "mock_boot_time_seconds")
    assert boot["value"] == [1700000000.0, "1699000000"]
    assert isinstance(boot["value"][0], float)


def test_text_0_0_4_counter_without_total_keeps_exposed_name(text_families):
    result = _vector(text_families, prefix="mock_restarts")
    assert [r["metric"]["__name__"] for r in result.data["result"]] == ["mock_restarts"]
    assert result.types == {"mock_restarts": "counter"}
    # _total로 선언된 counter는 그대로 둔다
    oom = _vector(text_families, metric="mock_oom_kills_total")
    assert [r["metric"]["__name__"] for r in oom.data["result"]] == ["mock_oom_kills_total"]


def test_text_0_0_4_quoted_counter_without_total_keeps_name():
    families = om.parse_exposition('# TYPE "my.count" counter\n{"my.count", a="1"} 4\n', _TEXT_CT)
    assert [s.name for f in families for s in f.samples] == ["my.count"]


def test_text_0_0_4_untyped_sample_is_unknown(text_families):
    result = _vector(text_families, metric="mock_untyped_probe")
    assert result.types == {"mock_untyped_probe": "unknown"}
    assert result.data["result"][0]["metric"] == {
        "__name__": "mock_untyped_probe", "kind": "probe", "nodename": _HOST,
    }


def test_text_0_0_4_utf8_quoted_name_passes():
    families = om.parse_exposition('{"my.metric", env="dev"} 2\n', _TEXT_CT)
    result = _vector(families, prefix="my")
    assert result.data["result"] == [
        {"metric": {"__name__": "my.metric", "env": "dev", "nodename": _HOST},
         "value": [_SCRAPED_AT, "2"]},
    ]


# =====================================================================
# instant vector — 모양·_created·info/stateset·nodename
# =====================================================================


def test_instant_vector_shape_and_default_timestamp(om_families):
    result = _vector(om_families, metric="app_mystery")
    assert result.data == {
        "resultType": "vector",
        "result": [
            {"metric": {"__name__": "app_mystery", "nodename": _HOST},
             "value": [_SCRAPED_AT, "42"]},
        ],
    }
    assert result.truncated is False
    assert result.series_total == 1


def test_created_samples_excluded_for_counter_histogram_summary(om_families):
    names = [r["metric"]["__name__"] for r in _vector(om_families, prefix="app_").data["result"]]
    assert "app_requests_created" not in names
    assert "app_latency_seconds_created" not in names
    assert "app_rpc_seconds_created" not in names


def test_gauge_named_with_created_suffix_is_kept(om_families):
    result = _vector(om_families, metric="app_job_created")
    assert [r["value"][1] for r in result.data["result"]] == ["1699000000"]
    assert result.types == {"app_job_created": "gauge"}


def test_info_and_stateset_values_pass_through(om_families):
    info = _vector(om_families, metric="app_build")
    assert info.data["result"][0]["metric"]["__name__"] == "app_build_info"
    assert info.data["result"][0]["value"][1] == "1"
    assert info.types == {"app_build_info": "info"}

    state = _vector(om_families, metric="app_feature_state")
    got = {r["metric"]["app_feature_state"]: r["value"][1] for r in state.data["result"]}
    assert got == {"enabled": "1", "disabled": "0"}
    assert state.types == {"app_feature_state": "stateset"}


def test_histogram_family_name_returns_bucket_sum_count(om_families):
    result = _vector(om_families, metric="app_latency_seconds")
    names = [r["metric"]["__name__"] for r in result.data["result"]]
    assert names == ["app_latency_seconds_bucket"] * 3 + [
        "app_latency_seconds_count", "app_latency_seconds_sum",
    ]
    assert result.types == {
        "app_latency_seconds_bucket": "histogram",
        "app_latency_seconds_count": "histogram",
        "app_latency_seconds_sum": "histogram",
    }
    buckets = [r["metric"]["le"] for r in _series(result, "app_latency_seconds_bucket")]
    assert buckets == ["0.1", "1.0", "+Inf"]


def test_summary_quantiles_kept(om_families):
    result = _vector(om_families, metric="app_rpc_seconds")
    quantiles = [r["metric"]["quantile"] for r in _series(result, "app_rpc_seconds")]
    assert quantiles == ["0.5", "0.9"]
    assert result.series_total == 4  # quantile 2 + _count + _sum (_created 제외)


def test_metric_exact_sample_name_and_counter_family_name(om_families):
    exact = _vector(om_families, metric="app_latency_seconds_bucket")
    assert exact.series_total == 3
    counter = _vector(om_families, metric="app_requests")
    assert [r["metric"]["__name__"] for r in counter.data["result"]] == ["app_requests_total"] * 2
    assert [r["value"][1] for r in counter.data["result"]] == ["1027", "3"]
    assert counter.types == {"app_requests_total": "counter"}


def test_nodename_injected_on_every_series(om_families):
    result = _vector(om_families, prefix="app_")
    assert result.data["result"]
    for row in result.data["result"]:
        assert row["metric"]["nodename"] == _HOST
        assert list(row["metric"])[0] == "__name__"
        assert list(row["metric"])[-1] == "nodename"


def test_existing_nodename_moved_to_exported_nodename(om_families):
    first, second = _series(_vector(om_families, metric="app_labeled"), "app_labeled")
    assert first["metric"] == {
        "__name__": "app_labeled", "exported_nodename": "inner-node", "nodename": _HOST,
    }
    # exported_nodename이 이미 차 있으면 접두를 한 번 더 붙인다(Prometheus honor_labels=false)
    assert second["metric"] == {
        "__name__": "app_labeled",
        "exported_nodename": "relayed",
        "exported_exported_nodename": "inner-node",
        "nodename": _HOST,
    }
    assert second["value"] == [1700000001.25, "8"]


def test_exported_nodename_nested_chain_and_empty_slot():
    text = (
        "# TYPE t gauge\n"
        't{nodename="a",exported_nodename="b",exported_exported_nodename="c"} 1\n'
        't{nodename="d",exported_nodename=""} 2\n'
        't{nodename=""} 3\n'
    )
    rows = _vector(om.parse_exposition(text, _TEXT_CT), metric="t").data["result"]
    assert rows[0]["metric"] == {
        "__name__": "t",
        "exported_nodename": "b",
        "exported_exported_nodename": "c",
        "exported_exported_exported_nodename": "a",
        "nodename": _HOST,
    }
    # 빈 값은 없는 라벨로 본다 — 빈 exported_nodename 자리에 옮긴다
    assert rows[1]["metric"] == {"__name__": "t", "exported_nodename": "d", "nodename": _HOST}
    # 빈 nodename은 충돌이 아니다 — 옮기지 않고 주입값으로 채운다
    assert rows[2]["metric"] == {"__name__": "t", "nodename": _HOST}


def test_uname_nodename_found_regardless_of_filter(om_families):
    result = _vector(om_families, metric="app_mystery")
    assert result.uname_nodename == "host-a01.example.internal"
    (uname,) = _series(_vector(om_families, metric="node_uname_info"), "node_uname_info")
    assert uname["metric"]["exported_nodename"] == "host-a01.example.internal"
    assert uname["metric"]["nodename"] == _HOST


def test_uname_nodename_none_when_absent(text_families):
    assert _vector(text_families, prefix="mock_").uname_nodename is None


# =====================================================================
# 필터 검증·절단
# =====================================================================


def test_truncation_flag_and_series_total(om_families):
    result = _vector(om_families, prefix="app_", max_series=3)
    assert result.truncated is True
    assert result.series_total == 20  # app_* 비-_created 샘플 전부
    rows = result.data["result"]
    assert len(rows) == 3
    assert [r["metric"]["__name__"] for r in rows] == [
        "app_temperature_celsius", "app_temperature_celsius", "app_requests_total",
    ]
    assert result.types == {"app_temperature_celsius": "gauge", "app_requests_total": "counter"}


def test_no_truncation_at_exact_limit(om_families):
    result = _vector(om_families, metric="app_latency_seconds", max_series=5)
    assert result.truncated is False
    assert result.series_total == 5
    assert len(result.data["result"]) == 5


def test_metric_and_prefix_combine_as_and(om_families):
    result = _vector(om_families, metric="app_latency_seconds", prefix="app_latency_seconds_s")
    assert [r["metric"]["__name__"] for r in result.data["result"]] == ["app_latency_seconds_sum"]


def test_filter_required(om_families):
    with pytest.raises(ValueError, match="metric 또는 prefix"):
        _vector(om_families)


@pytest.mark.parametrize("arg", ["metric", "prefix"])
@pytest.mark.parametrize(
    "bad", ['up{job="x"}', "rate(x[5m])", "", "   ", "my.metric", "1abc", "a b"]
)
def test_invalid_metric_or_prefix_rejected(om_families, arg, bad):
    with pytest.raises(ValueError, match="bare 메트릭 이름"):
        _vector(om_families, **{arg: bad})


def test_max_series_must_be_positive(om_families):
    with pytest.raises(ValueError, match="max_series"):
        _vector(om_families, metric="app_mystery", max_series=0)


def test_metric_name_pattern_matches_promql_tools():
    pytest.importorskip("mcp")
    from mcp_server import promql_tools

    assert om._METRIC_NAME_RE.pattern == promql_tools._METRIC_NAME_RE.pattern
    assert om._METRIC_NAME_RE.flags == promql_tools._METRIC_NAME_RE.flags


# =====================================================================
# 값 표기 · 카탈로그 · 호스트명 · 신원 · 임포트
# =====================================================================


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (math.nan, "NaN"),
        (math.inf, "+Inf"),
        (-math.inf, "-Inf"),
        (8589934592.0, "8589934592"),
        (8589934592, "8589934592"),
        (3, "3"),
        (97.5, "97.5"),
        (1.5, "1.5"),
        (0.1, "0.1"),
        (1e-05, "0.00001"),
        (0.0, "0"),
        (-2.25, "-2.25"),
        (1e16, "10000000000000000"),
        (float(2**60), "1152921504606847000"),  # Go FormatFloat(-1) 최단 자릿수와 같다
    ],
)
def test_format_sample_value(value, expected):
    assert om.format_sample_value(value) == expected


def test_to_catalog_rows_sorted_with_unit_help_series(om_families):
    rows = om.to_catalog(om_families)
    assert [r["name"] for r in rows] == sorted(r["name"] for r in rows)
    by_name = {r["name"]: r for r in rows}
    assert by_name["app_temperature_celsius"] == {
        "name": "app_temperature_celsius", "type": "gauge", "unit": "celsius",
        "help": "Current temperature.", "series": 2,
    }
    assert by_name["app_requests"]["series"] == 2  # _created 2건 제외
    assert by_name["app_latency_seconds"]["series"] == 5
    assert by_name["app_rpc_seconds"]["series"] == 4
    assert by_name["app_job_created"]["series"] == 1
    assert by_name["app_build"]["type"] == "info"
    assert by_name["app_feature_state"]["type"] == "stateset"
    assert by_name["app_mystery"]["type"] == "unknown"


def test_to_catalog_prefix_filter(om_families, text_families):
    assert [r["name"] for r in om.to_catalog(om_families, prefix="node_")] == ["node_uname_info"]
    # 샘플명 접두로도 잡는다 — 0.0.4 counter 패밀리명은 _total이 떨어진 이름이다
    rows = om.to_catalog(text_families, prefix="mock_oom_kills_total")
    assert [(r["name"], r["type"], r["series"]) for r in rows] == [("mock_oom_kills", "counter", 1)]
    with pytest.raises(ValueError):
        om.to_catalog(om_families, prefix="bad prefix")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("host-a01", "host-a01"),
        ("HOST-A01.example.internal", "host-a01"),
        ("  Host-A01.Example.Internal  ", "host-a01"),
        ("host-a01.", "host-a01"),
        ("", ""),
    ],
)
def test_normalize_hostname(raw, expected):
    assert om.normalize_hostname(raw) == expected


@pytest.mark.parametrize(
    ("uname", "server_name", "os_hostname", "expected"),
    [
        (None, "host-a01", "", "unknown"),
        ("", "host-a01", "host-a01", "unknown"),
        # os_hostname 없음 — server_name과 대조
        ("host-a01.example.internal", "HOST-A01", "", "match"),
        # server_name ≠ OS hostname(D-046) — os_hostname이 없으면 확인 불가
        ("host-a01.example.internal", "svc-alias-01", "", "unverified"),
        # server_name ≠ OS hostname이어도 os_hostname이 맞으면 match
        ("host-a01.example.internal", "svc-alias-01", "host-a01", "match"),
        # os_hostname이 있는데 다르다 — 허용목록 오등록(server_name 일치 여부와 무관)
        ("host-a01.example.internal", "host-a01", "host-b02", "mismatch"),
    ],
)
def test_target_identity(uname, server_name, os_hostname, expected):
    assert om.target_identity(uname, server_name=server_name, os_hostname=os_hostname) == expected


def test_target_identity_from_fixture(om_families):
    uname = _vector(om_families, metric="app_mystery").uname_nodename
    assert om.target_identity(uname, server_name="host-a01") == "match"


def test_prometheus_client_resolves_to_pip_package_not_noise_gate():
    import prometheus_client

    parts = Path(prometheus_client.__file__).resolve().parts
    assert {"site-packages", "dist-packages"} & set(parts)
    assert "noise_gate" not in parts
    assert hasattr(prometheus_client, "generate_latest")


def test_prom_scrape_accept_matches_prometheus_header():
    assert om.PROM_SCRAPE_ACCEPT == (
        "application/openmetrics-text;version=1.0.0,"
        "application/openmetrics-text;version=0.0.1;q=0.75,"
        "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"
    )
