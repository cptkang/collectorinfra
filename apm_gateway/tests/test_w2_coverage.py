"""plans/134 W2 게이트웨이 — N-5~N-8 · COV 표 A·B·C의 W2 행 · SPEC-apm-question-coverage §2.4·§5 W2.

테스트 이름 끝의 주석이 COV 테스트 ID다(`T-COV-<경로>-allow`·`-arg-<인자>`·`-fields` ·
`T-SCH-<스키마>-fields` · `T-MASK-<필드>`). 인자 전달은 목 Open API 서버의 `/__mock/hits`
(`query` = 실제 쿼리 키·값)로, 조각·재정렬·캐시는 MockTransport로 본다. `/api/metrics`는 녹화본
(로컬 5.7.0.1) 실모양이고 나머지는 스펙 5.6.4·v2 매뉴얼 필드명 기반 합성이다(실응답은 W10).
외부 네트워크 0.
"""

from __future__ import annotations

import json
import time
import urllib.request

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import (
    Endpoint,
    NotAllowedError,
    _template_re,
    build_path,
    check_request,
    match_template,
)
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.masking import mask_text
from apm_gateway.application.spool import Spool
from apm_gateway.domain.errors import API_ERROR, INVALID_ARGUMENT, ApmError
from apm_gateway.interface.server import create_server
from conftest import (
    DOMAIN,
    NOW_MS,
    NOW_S,
    make_tools,
    recorded_body,
    synthetic_fixtures,
    synthetic_handler,
)

from apm_gateway.config import JobConfig

HOUR = 3_600_000


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


def _queries(base: str, template: str) -> list[dict]:
    return [h["query"] for h in _hits(base) if h["template"] == template]


@pytest.fixture
def synth(mock_server_factory, synthetic_dir):
    base, _ = mock_server_factory(synthetic_dir, "connected")
    tools, _ = make_tools(base)
    return base, tools


def _mock_tools(handler, **kw):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler), **kw)
    return tools


# ── N-8 허용목록 · §2.4 경로 변수 형식 ─────────────────────────


@pytest.mark.parametrize(
    ("path", "optional"),
    [
        (
            "/api/status/application",
            {
                "instance_id": "1",
                "sort_by_metrics": "calls",
                "max_row": "5",
                "application_name": "x",
            },
        ),
        ("/api/status/sql", {"instance_id": "1", "sort_by_metrics": "calls", "max_row": "5"}),
        (
            "/api/status/external_call",
            {"instance_id": "1", "sort_by_metrics": "calls", "max_row": "5"},
        ),
    ],
)
def test_status_optional_keys_allowed(path, optional):
    # T-COV-STAT-APP-allow · T-COV-STAT-SQL-allow · T-COV-STAT-EXT-allow
    base = {"domain_id": "1", "start_time": "1", "end_time": "2"}
    assert check_request("GET", path, {**base, **optional}).template == path
    with pytest.raises(NotAllowedError):
        check_request("POST", path, base)
    with pytest.raises(NotAllowedError):
        check_request("GET", path, {**base, "token": "x"})


def test_application_name_only_on_application_status():
    base = {"domain_id": "1", "start_time": "1", "end_time": "2", "application_name": "x"}
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/status/sql", base)


def test_path_var_formats_are_declared_and_enforced():  # SPEC-coverage §2.4
    assert build_path("/api-v2/deploy/{domainId}", {"domainId": 1000}) == "/api-v2/deploy/1000"
    for bad in ("1000a", "-1", "1 0", ""):
        with pytest.raises(NotAllowedError):
            build_path("/api-v2/deploy/{domainId}", {"domainId": bad})
    with pytest.raises(NotAllowedError):
        build_path("/api-v2/deploy/{domainId}", {"other": 1})
    assert match_template("/api-v2/deploy/12x") is None
    enum = _template_re(Endpoint("/x/{t}", path_vars=(("t", "enum:domain|instance"),)))
    assert enum.match("/x/domain") and enum.match("/x/instance") and not enum.match("/x/domainx")
    token = _template_re(Endpoint("/e/{k}", path_vars=(("k", "token"),)))
    assert token.match("/e/ERROR_OUTOFMEMORY") and not token.match("/e/error")
    with pytest.raises(ValueError):  # 선언 없는 변수 — 기동 시점에 실패한다
        _template_re(Endpoint("/x/{t}"))


# ── N-5 apm_status_stats ─────────────────────────────────────


@pytest.mark.asyncio
async def test_status_application_args_fields_and_masking(synth):
    # T-COV-STAT-APP-arg-{domain_id·instance_id·start_time·end_time·sort_by_metrics·max_row·
    # application_name} · T-SCH-ApplicationStatus-fields · T-MASK-name
    base, tools = synth
    out = await tools.apm_status_stats(
        "application", "was-host01", sort_by="calls", n=5, application_name="/order"
    )
    assert _queries(base, "/api/status/application")[-1] == {
        "domain_id": str(DOMAIN),
        "start_time": str(NOW_MS - HOUR),
        "end_time": str(NOW_MS),
        "sort_by_metrics": "calls",
        "max_row": "5",
        "application_name": "/order",
        "instance_id": "1001,1002",
    }
    row = out["rows"][0]
    assert len([k for k in row if k not in ("source_id", "domain_id")]) == 25
    assert row["application"] == "/order/list?user=<v>"
    assert (row["total_response_ms"], row["calls"]) == (270_000, 300)  # 원자료 칸(W6 가중 평균)
    assert out["kind"] == "application" and out["summary"]["response_time_avg_ms"] == 900.0
    assert out["summary"]["failure_rate"] == 12 / 300
    assert any("시 경계로 맞췄다" in x for x in out["limits"])
    assert any("정렬 기준 calls" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_status_window_widens_to_hour_boundaries(synth):
    base, tools = synth
    out = await tools.apm_status_stats(
        "sql", "was-host01", reference_time="2026-09-29T10:23:00+09:00", lookback_minutes=30
    )
    query = _queries(base, "/api/status/sql")[-1]
    assert (int(query["start_time"]), int(query["end_time"])) == (NOW_MS - HOUR, NOW_MS + HOUR)
    note = next(x for x in out["limits"] if "시 경계" in x)
    assert "요청 2026-09-29T09:53+09:00~2026-09-29T10:23+09:00" in note
    assert "조회 2026-09-29T09:00+09:00~2026-09-29T11:00+09:00" in note
    assert out["hour_start"].startswith("2026-09-29T09:00") and out["hour_end"].startswith(
        "2026-09-29T11:00"
    )


@pytest.mark.asyncio
async def test_status_sql_and_external_fields_masked(synth):
    # T-SCH-SqlAndExternalCallStatus-fields · T-MASK-name · T-COV-STAT-SQL-arg-* ·
    # T-COV-STAT-EXT-arg-*
    base, tools = synth
    sql = await tools.apm_status_stats("sql", "was-host01")
    fields = {
        "name",
        "calls",
        "failures",
        "bad_responses",
        "response_time_avg_ms",
        "max_response_time_ms",
        "total_response_ms",
    }
    assert set(sql["rows"][0]) == fields | {"source_id", "domain_id"}
    assert sql["rows"][0]["name"] == "select * from orders where id = ? and name = ?"
    assert "'A-1'" not in sql["rows"][1]["name"] and "qty = ?" in sql["rows"][1]["name"]
    assert _queries(base, "/api/status/sql")[-1] == {
        "domain_id": str(DOMAIN),
        "start_time": str(NOW_MS - HOUR),
        "end_time": str(NOW_MS),
        "max_row": "10",  # n 기본 10
        "instance_id": "1001,1002",
    }
    ext = await tools.apm_status_stats("external_call", "was-host01", sort_by="maxResponseTime")
    assert ext["rows"][0]["name"] == "http://pay.example/api/charge?card=<v>&user=<v>"
    assert _queries(base, "/api/status/external_call")[-1]["sort_by_metrics"] == "maxResponseTime"
    summary = sql["summary"]  # Σ총 응답시간 ÷ Σ호출 = 85000 / 550
    assert summary["calls"] == 550 and summary["response_time_avg_ms"] == 85_000 / 550
    assert summary["failures"] == 9 and summary["max_response_time_ms"] == 3001


@pytest.mark.asyncio
async def test_status_full_omits_max_row(synth):  # COV E-16
    base, tools = synth
    out = await tools.apm_status_stats("sql", "was-host01", full=True)
    assert "max_row" not in _queries(base, "/api/status/sql")[-1]
    assert out["row_count"] == 2 and any("서버 기본 행 수" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_status_argument_validation(synth):
    _, tools = synth
    for kwargs in (
        {"kind": "cpu"},
        {"kind": "sql", "application_name": "/x"},
        {"kind": "sql", "sort_by": "calls; drop"},
        {"kind": "sql", "n": 0},
    ):
        with pytest.raises(ApmError) as exc:
            await tools.apm_status_stats(hostname="was-host01", **kwargs)
        assert exc.value.code == INVALID_ARGUMENT, kwargs


@pytest.mark.asyncio
async def test_status_server_rejection_reason_is_passed_through():  # COV E-05
    rejected = httpx.Response(500, json={"exception": {"message": "Unknown sort metric: fooBar"}})
    tools = _mock_tools(synthetic_handler(override={"/api/status/sql": rejected}))
    with pytest.raises(ApmError) as exc:
        await tools.apm_status_stats("sql", "was-host01", sort_by="fooBar")
    assert exc.value.code == API_ERROR and "Unknown sort metric: fooBar" in exc.value.reason


def _two_domain_handler(rows_by_domain: dict[int, list[dict]], calls: list[httpx.Request]):
    """도메인 2개에 같은 hostname 인스턴스 1개씩 — (소스, 도메인) 묶음 2개."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path, params = request.url.path, request.url.params
        if path == "/api/domain":
            return httpx.Response(
                200,
                json={"result": [{"domainId": d, "name": f"d{d}"} for d in rows_by_domain]},
            )
        if path == "/api/instance":
            d = int(params["domain_id"])
            inst = {"instanceId": d + 1, "name": f"was_{d}", "hostName": "was-host01"}
            return httpx.Response(200, json={"result": [inst]})
        if path.startswith("/api/status/"):
            rows = rows_by_domain[int(params["domain_id"])]
            if "max_row" in params:
                rows = rows[: int(params["max_row"])]
            return httpx.Response(200, json={"result": rows})
        return httpx.Response(404)

    return handler


def _sql_row(name: str, calls: int, avg: float) -> dict:
    return {
        "name": name,
        "calls": calls,
        "failures": 0,
        "badResponses": 0,
        "responseTime": avg,
        "maxResponseTime": int(avg * 2),
        "totalResponseTime": int(calls * avg),
    }


@pytest.mark.asyncio
async def test_status_merges_groups_and_takes_global_top_n():
    calls: list[httpx.Request] = []
    rows = {
        1000: [_sql_row("select a", 90, 10.0), _sql_row("select b", 40, 300.0)],
        2000: [_sql_row("select c", 70, 20.0), _sql_row("select d", 60, 50.0)],
    }
    tools = _mock_tools(_two_domain_handler(rows, calls))
    out = await tools.apm_status_stats("sql", "was-host01", n=2)
    asked = [r for r in calls if r.url.path == "/api/status/sql"]
    assert [r.url.params["max_row"] for r in asked] == ["2", "2"]  # 도메인별 max_row = n
    assert [(r["name"], r["domain_id"]) for r in out["rows"]] == [
        ("select a", 1000),
        ("select c", 2000),
    ]  # 전역 재정렬(기본 정렬 = 호출 수) 뒤 상위 2
    assert out["summary"]["calls"] == 160 and any("상위 2행의 합계" in x for x in out["limits"])
    by_avg = await tools.apm_status_stats(
        "sql", "was-host01", n=2, sort_by="averageResponseTime"
    )
    assert [r["name"] for r in by_avg["rows"]] == ["select b", "select d"]
    unmapped = await tools.apm_status_stats("sql", "was-host01", n=2, sort_by="slowCount")
    # 대응 못 하는 정렬 기준 — 묶음별 상위 n을 모두 남긴다(W2V-G3)
    assert any("전역 순위 아님 — 묶음별 상위 2" in x for x in unmapped["limits"])
    assert {r["domain_id"] for r in unmapped["rows"]} == {1000, 2000}


@pytest.mark.asyncio
async def test_status_partial_group_failure_keeps_reason():
    calls: list[httpx.Request] = []
    rows = {1000: [_sql_row("select a", 9, 1.0)], 2000: []}
    inner = _two_domain_handler(rows, calls)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/status/sql" and request.url.params["domain_id"] == "2000":
            return httpx.Response(500, json={"exception": {"message": "bad max_row"}})
        return inner(request)

    out = await _mock_tools(handler).apm_status_stats("sql", "was-host01")
    assert out["partial"] is True and out["row_count"] == 1
    assert any("도메인 2000" in x and "bad max_row" in x for x in out["limits"])


# ── N-6 apm_metrics · apm_runtime_health 지표 ────────────────────


@pytest.mark.asyncio
async def test_metric_catalog_rows_from_recorded_shape(synth):
    # T-COV-METRICS-allow · T-COV-METRICS-fields · T-SCH-Metrics-fields · T-SCH-MetricsSet-fields
    base, tools = synth
    body = recorded_body("GET_api_metrics__ok.json")["result"]
    out = await tools.apm_metrics("catalog")
    assert out["row_count"] == sum(len(v) for v in body.values()) == 169
    assert {r["scope"] for r in out["rows"]} == {
        "domain",
        "instance",
        "business",
        "application",
        "sql",
        "external_call",
    }
    assert set(out["rows"][0]) == {"source_id", "scope", "metric"}
    sql = await tools.apm_metrics("catalog", scope="sql")
    assert [r["metric"] for r in sql["rows"]] == body["sql"]
    assert len([h for h in _hits(base) if h["template"] == "/api/metrics"]) == 1  # TTL 캐시
    assert out["sources"] == [{"source_id": "default", "status": "ok", "reason": ""}]


@pytest.mark.asyncio
async def test_metric_catalog_ttl_and_change_detection():
    now = [NOW_S]
    catalog = {"result": {"instance": ["heap_used", "proc_cpu"], "sql": ["count"]}}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/metrics":
            seen.append("metrics")
            return httpx.Response(200, json=catalog)
        return httpx.Response(404)

    tools = _mock_tools(
        handler, clock=lambda: now[0], extra={"APM_METRIC_CATALOG_TTL_SECONDS": "100"}
    )
    first = await tools.apm_metrics("catalog")
    now[0] += 50
    await tools.apm_metrics("catalog")
    assert len(seen) == 1 and not any("변경 감지" in x for x in first["limits"])
    catalog["result"]["instance"] = ["heap_used", "proc_cpu", "thread_current"]
    now[0] += 60
    changed = await tools.apm_metrics("catalog")
    assert len(seen) == 2 and changed["row_count"] == 4
    note = next(x for x in changed["limits"] if "변경 감지" in x)
    assert "추가 1" in note and "삭제 0" in note
    now[0] += 101
    same = await tools.apm_metrics("catalog")
    assert len(seen) == 3 and not any("변경 감지" in x for x in same["limits"])


@pytest.mark.asyncio
async def test_metric_catalog_wrong_shape_is_error_not_empty():  # COV E-18
    tools = _mock_tools(
        lambda r: httpx.Response(200, json={"result": [{"instance": ["heap_used"]}]})
    )
    with pytest.raises(ApmError) as exc:
        await tools.apm_metrics("catalog")
    assert exc.value.code == API_ERROR


@pytest.mark.asyncio
async def test_metric_series_long_rows_and_args(synth):
    # T-COV-DBM-INSTANCE-arg-metrics · T-COV-DBM-INSTANCE-arg-interval_minute
    base, tools = synth
    out = await tools.apm_metrics(
        "series",
        hostname="was-host01",
        metrics=["heap_used", "proc_cpu"],
        interval_minute=10,
        lookback_minutes=30,
    )
    asked = _queries(base, "/api/dbmetrics/instance")
    assert sorted((q["instance_id"], q["metrics"]) for q in asked) == [
        ("1001", "heap_used"),
        ("1001", "proc_cpu"),
        ("1002", "heap_used"),
        ("1002", "proc_cpu"),
    ]
    assert {q["interval_minute"] for q in asked} == {"10"}
    assert {q["start_time"] for q in asked} == {str(NOW_MS - 30 * 60_000)}
    assert out["row_count"] == 4 * 6 and out["interval_minute"] == 10
    assert set(out["rows"][0]) == {
        "source_id",
        "domain_id",
        "instance_id",
        "instance_name",
        "metric",
        "time_ms",
        "value",
    }
    assert any("interval_minute 허용값" in x for x in out["limits"])  # COV E-06


@pytest.mark.asyncio
async def test_metric_series_unknown_metric_has_candidates(synth):
    _, tools = synth
    with pytest.raises(ApmError) as exc:
        await tools.apm_metrics("series", hostname="was-host01", metrics=["heap_usd"])
    assert exc.value.code == INVALID_ARGUMENT
    candidates = exc.value.reason.split("후보: ")[1].split(")")[0].split(", ")
    assert "heap_used" in candidates and 1 <= len(candidates) <= 3


@pytest.mark.asyncio
async def test_metric_argument_validation(synth):
    _, tools = synth
    cases = [
        ({"mode": "graph"}, ""),
        ({"mode": "catalog", "scope": "cpu"}, ""),
        # plans/134 W3·W4가 domain·business 시계열을 열었다 — 대상은 hostname이 아니다
        (
            {"mode": "series", "scope": "domain", "metrics": ["service_count"], "hostname": "h"},
            "service·domain_id",
        ),
        (
            {"mode": "series", "scope": "business", "metrics": ["service_count"], "instance_id": 1},
            "business_id+domain_id",
        ),
        (
            {"mode": "series", "hostname": "was-host01", "metrics": ["heap_used"], "domain_id": 1},
            "scope domain·business 시계열에서만",
        ),
        ({"mode": "series", "scope": "sql", "metrics": ["count"]}, "apm_status_stats"),
        ({"mode": "series", "hostname": "was-host01"}, "metrics"),
        ({"mode": "series", "hostname": "was-host01", "metrics": ["a b"]}, ""),
        (
            {
                "mode": "series",
                "hostname": "was-host01",
                "metrics": ["heap_used"],
                "interval_minute": 0,
            },
            "interval_minute",
        ),
    ]
    for kwargs, needle in cases:
        with pytest.raises(ApmError) as exc:
            await tools.apm_metrics(**kwargs)
        assert exc.value.code == INVALID_ARGUMENT and needle in exc.value.reason, kwargs


@pytest.mark.asyncio
async def test_runtime_health_metrics_and_interval(synth):
    base, tools = synth
    out = await tools.apm_runtime_health(
        "was-host01", lookback_minutes=30, metrics=["proc_cpu", "heap_used_mb"], interval_minute=10
    )
    row = next(r for r in out["rows"] if r["instance_id"] == 1001)
    assert set(row["trend"]) == {"process_cpu_pct", "heap_used_mb"}  # 중립 이름이 있으면 중립 이름
    asked = _queries(base, "/api/dbmetrics/instance")
    assert {q["metrics"] for q in asked} == {"proc_cpu", "heap_used"}
    assert {q["interval_minute"] for q in asked} == {"10"}
    # 카탈로그에 없는 지표는 빼고 조회 — 전부 모르면 기본 3종 추세(현재값 유지 · W2V-B2)
    mixed = await tools.apm_runtime_health(
        "was-host01", lookback_minutes=30, metrics=["proc_cpu", "no_such_metric"]
    )
    row = next(r for r in mixed["rows"] if r["instance_id"] == 1001)
    assert set(row["trend"]) == {"process_cpu_pct"} and row["heap_used_mb"] == 930.0
    assert any("no_such_metric(후보" in x and "빼고 조회" in x for x in mixed["limits"])
    assert mixed.get("partial") is not True
    none = await tools.apm_runtime_health("was-host01", metrics=["no_such_metric"])
    row = next(r for r in none["rows"] if r["instance_id"] == 1001)
    assert set(row["trend"]) == {"heap_used_mb", "heap_committed_mb", "gc_time_usage_pct"}
    assert any("기본 추세 지표" in x for x in none["limits"])


@pytest.mark.asyncio
async def test_runtime_health_default_trend_is_unchanged(synth):
    base, tools = synth
    out = await tools.apm_runtime_health("was-host01", lookback_minutes=30)
    asked = _queries(base, "/api/dbmetrics/instance")
    assert sorted({q["metrics"] for q in asked}) == ["gc_time_usage", "heap_committed", "heap_used"]
    assert {q["interval_minute"] for q in asked} == {"5"}
    # 기본 3종은 카탈로그를 읽지 않는다
    assert not [h for h in _hits(base) if h["template"] == "/api/metrics"]
    row = next(r for r in out["rows"] if r["instance_id"] == 1001)
    assert set(row["trend"]) == {"heap_used_mb", "heap_committed_mb", "gc_time_usage_pct"}


@pytest.mark.asyncio
async def test_runtime_health_metrics_without_window_use_default_lookback(synth):
    _, tools = synth
    out = await tools.apm_runtime_health("was-host01", metrics=["proc_cpu"])
    assert out["window"]["minutes"] == 30 and all("trend" in r for r in out["rows"])


# ── N-7 apm_source_changes ───────────────────────────────────


@pytest.mark.asyncio
async def test_source_changes_default_24h_via_mock(synth):
    # T-COV-DEPLOY-allow · T-COV-DEPLOY-arg-{domainId·startTime·endTime} · T-COV-DEPLOY-fields
    base, tools = synth
    out = await tools.apm_source_changes("was-host01")
    hits = [h for h in _hits(base) if h["template"] == "/api-v2/deploy/{domainId}"]
    assert [h["path"] for h in hits] == [f"/api-v2/deploy/{DOMAIN}"]
    assert hits[0]["query"] == {"startTime": str(NOW_MS - 24 * HOUR), "endTime": str(NOW_MS)}
    assert out["rows"] == [
        {
            "source_id": "default",
            "domain_id": DOMAIN,
            "instance_id": 1001,
            "instance_name": "was01_a",
            "change_detected_ms": NOW_MS - HOUR,
            "change_detected_at": "2026-09-29T09:00:00+09:00",
        },
        {
            "source_id": "default",
            "domain_id": DOMAIN,
            "instance_id": 1002,
            "instance_name": "was01_b",
            "change_detected_ms": NOW_MS - 2 * HOUR,
            "change_detected_at": "2026-09-29T08:00:00+09:00",
        },
    ]  # 다른 호스트(1003)는 거르고 원시 시각(epoch ms)을 그대로 둔다
    assert any("배포 확정 아님" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_source_changes_25h_chunks_and_dedupe():
    windows: list[tuple[int, int]] = []
    inner = synthetic_handler()
    start = NOW_MS - 60 * HOUR
    boundary = start + 25 * HOUR  # 두 조각에 모두 걸리는 시각

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api-v2/deploy/"):
            lo, hi = int(request.url.params["startTime"]), int(request.url.params["endTime"])
            windows.append((lo, hi))
            items = [
                {"collectTime": t, "instanceId": 1001}
                for t in (boundary, start + HOUR, NOW_MS - HOUR)
                if lo <= t <= hi
            ]
            return httpx.Response(200, json=items)
        return inner(request)

    tools = _mock_tools(_sync(handler))
    out = await tools.apm_source_changes("was-host01", lookback_minutes=60 * 60)
    assert windows == [
        (start, start + 25 * HOUR),
        (start + 25 * HOUR, start + 50 * HOUR),
        (start + 50 * HOUR, NOW_MS),
    ]  # 25시간 이하 조각 · 이어 붙음
    assert [r["change_detected_ms"] for r in out["rows"]] == [
        NOW_MS - HOUR,
        boundary,
        start + HOUR,
    ]  # 경계 시각은 한 번만(겹침 제거)


@pytest.mark.asyncio
async def test_source_changes_non_array_is_error_not_empty():  # COV E-18
    inner = synthetic_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api-v2/deploy/"):
            return httpx.Response(200, json={"result": []})
        return inner(request)

    with pytest.raises(ApmError) as exc:
        await _mock_tools(_sync(handler)).apm_source_changes("was-host01")
    assert exc.value.code == API_ERROR and "배열이 아니" in exc.value.reason


def _sync(handler):
    """동기·비동기 응답이 섞인 핸들러를 MockTransport용 비동기 핸들러로 맞춘다."""

    async def run(request: httpx.Request) -> httpx.Response:
        resp = handler(request)
        return await resp if not isinstance(resp, httpx.Response) else resp

    return run


# ── 행 텍스트 전문 (팀 리드 결정 — 300자 절단 제거) ──────────────


@pytest.mark.asyncio
async def test_row_text_is_full_masked_text():
    fx = synthetic_fixtures()
    long_message = "x" * 5000 + " user=kim@example.com tail"
    for f in fx:
        if f["request"]["template"] == "/api/dbsearch/event":
            f["response"]["body_json"]["result"][0]["message"] = long_message
        if f["request"]["template"] == "/api/activeService/list":
            for r in f["response"]["body_json"]["result"]:
                r["runningFullText"] = "select 1 " + "y" * 3000 + " where id = 42"
    tools = _mock_tools(synthetic_handler(fx))
    ev = await tools.apm_events("was-host01")
    message = ev["rows"][0]["message"]
    assert len(message) > 5000 and message.endswith("user=<email> tail")
    assert "kim@example.com" not in message
    active = await tools.apm_active_services("was-host01")
    text = active["rows"][0]["running_text"]
    assert len(text) > 3000 and text.endswith("where id = ?")
    reason = tools.err("apm_events", API_ERROR, "r" * 2000)["reason"]
    assert len(reason) == 400  # 오류 사유는 종전대로 자른다


@pytest.mark.parametrize(
    "text",
    [
        "?a" * 500_000,
        ";a" * 500_000,
        "a@" * 500_000,
        "select '" + "a" * 1_000_000,
        "select " + "1" * 1_000_000 + "x",
        ("a=b:c@d/e'f\"g<h>-p?x;y" * 50_000)[:1_000_000],
    ],
)
def test_full_text_masking_is_linear(text):
    started = time.perf_counter()
    mask_text(text, limit=None)
    assert time.perf_counter() - started < 1.0


# ── MCP 표면 ──────────────────────────────────────────────


def _mcp(tmp_path):
    spool = tmp_path / "spool"
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        extra={"APM_SPOOL_DIR": str(spool)},
    )
    jobs = JobManager(
        Spool(spool), JobConfig(spool_dir=spool), envelope=tools.ok, error_envelope=tools.err
    )
    return create_server(tools, jobs=jobs), jobs


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


@pytest.mark.asyncio
async def test_w2_tools_through_mcp(tmp_path):
    mcp, jobs = _mcp(tmp_path)
    schemas = {t.name: t.inputSchema["properties"] for t in await mcp.list_tools()}
    assert {"kind", "sort_by", "n", "full", "application_name", "source_ids"} <= set(
        schemas["apm_status_stats"]
    )
    assert {"mode", "scope", "metrics", "interval_minute", "source_ids"} <= set(
        schemas["apm_metrics"]
    )
    assert {"metrics", "interval_minute"} <= set(schemas["apm_runtime_health"])
    stats = _json(
        await mcp.call_tool("apm_status_stats", {"kind": "sql", "hostname": "was-host01"})
    )
    assert stats["tool"] == "apm_status_stats" and stats["row_count"] == 2
    catalog = _json(await mcp.call_tool("apm_metrics", {"mode": "catalog", "scope": "sql"}))
    assert catalog["row_count"] == 7
    changes = _json(await mcp.call_tool("apm_source_changes", {"hostname": "was-host01"}))
    assert changes["row_count"] == 3  # 이 핸들러는 구간으로 거르지 않는다 — 다른 호스트만 거른다
    bad = _json(await mcp.call_tool("apm_status_stats", {"kind": "cpu", "hostname": "x"}))
    assert bad["error"] == INVALID_ARGUMENT
    await jobs.aclose()
