"""plans/134 W2 게이트웨이 통합 검증(검증자) — N-5~N-8 경계·형 위반·드문 모양.

구현 테스트(`test_w2_coverage.py`·`test_w2_fix_regressions.py`)가 덮지 않은 적대 입력을 본다.
  - `apm_source_changes`: 25시간 정확히 = 1조각 · 1ms 넘김 = 2조각(각 ≤25h) ·
    48시간 = 25+23 · 같은 변경의 경계 겹침 제거 · 다른 도메인 같은 시각 보존 ·
    맨 배열 아닌 모양 = 오류 · 조각 일부 실패
  - `apm_status_stats`: 전부 실패(같은 코드 / 섞인 코드) · 일부 실패 · 정렬 기준 주입 ·
    묶음 2개 + 대응 못 하는 정렬 기준 · kind 대소문자 · application_name 다른 kind ·
    시 경계 정확히 · 이름 마스킹
  - `apm_metrics`: 녹화본 169개 = 6군 · 카탈로그 군 단위 모양 위반 · 대문자 scope ·
    변경 감지 추가·삭제 · 두 소스 중 하나만 카탈로그 실패 · 한 소스 카탈로그에만 없는 지표
  - 허용목록 경로 변수: 유니코드 숫자·불리언 거부 · 템플릿 끝 줄바꿈(기존 정규식 `$`)
  - `apm_events` `error_type` 접두만 있는 값
결함 재현은 `xfail(strict=True)`다(고쳐지면 XPASS 로 실패해 표지를 걷게 한다). 외부 네트워크 0.
"""

from __future__ import annotations

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import (
    ALLOWED,
    NotAllowedError,
    build_path,
    check_request,
    match_template,
)
from apm_gateway.application.tools import Window
from apm_gateway.domain.errors import API_ERROR, INVALID_ARGUMENT, SOURCE_UNAVAILABLE, ApmError
from conftest import NOW_MS, NOW_S, TOKEN, make_tools, recorded_body, synthetic_handler

HOUR = 3_600_000
CHUNK = 25 * HOUR


def _tools(handler, **kw):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler), **kw)
    return tools


def _deploy_windows(seen: list[tuple[int, int]], items=None, fail_at: int | None = None):
    """`/api-v2/deploy/*`는 조각 구간을 기록하고 맨 배열을 돌려준다. 나머지는 합성 픽스처."""
    inner = synthetic_handler()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api-v2/deploy/"):
            lo, hi = int(request.url.params["startTime"]), int(request.url.params["endTime"])
            seen.append((lo, hi))
            if fail_at is not None and len(seen) == fail_at:
                return httpx.Response(500, json={"exception": {"message": "chunk boom"}})
            body = items(lo, hi) if callable(items) else [
                {"collectTime": hi - 1000, "instanceId": 1001}]
            return httpx.Response(200, json=body)
        return inner(request)

    return handler


def _fixed_window(tools, start: int, end: int) -> None:
    tools.window = lambda *a, **k: Window(start, end, False, "Asia/Seoul")


# ── N-7 소스 변경 — 25시간 조각 경계 ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_changes_exactly_25h_is_one_call():
    seen: list[tuple[int, int]] = []
    tools = _tools(_deploy_windows(seen))
    await tools.apm_source_changes("was-host01", lookback_minutes=25 * 60)
    assert seen == [(NOW_MS - CHUNK, NOW_MS)]


@pytest.mark.asyncio
async def test_changes_25h_plus_1ms_is_two_chunks_each_within_25h():
    seen: list[tuple[int, int]] = []
    tools = _tools(_deploy_windows(seen))
    start = NOW_MS - CHUNK - 1
    _fixed_window(tools, start, NOW_MS)
    out = await tools.apm_source_changes("was-host01")
    assert seen == [(start, start + CHUNK), (start + CHUNK, NOW_MS)]
    assert all(hi - lo <= CHUNK for lo, hi in seen)
    assert out.get("partial") is not True


@pytest.mark.asyncio
async def test_changes_48h_is_25h_then_23h():
    seen: list[tuple[int, int]] = []
    tools = _tools(_deploy_windows(seen))
    await tools.apm_source_changes("was-host01", lookback_minutes=48 * 60)
    assert [(hi - lo) / HOUR for lo, hi in seen] == [25.0, 23.0]
    assert seen[0][1] == seen[1][0], "조각은 경계에서 맞닿는다(겹침은 결과에서 지운다)"


@pytest.mark.asyncio
async def test_changes_boundary_duplicate_is_removed_and_order_is_recent_first():
    seen: list[tuple[int, int]] = []
    boundary = NOW_MS - 23 * HOUR  # 48시간 창의 조각 경계

    def items(lo, hi):
        rows = [{"collectTime": boundary, "instanceId": 1001},   # 두 조각에 모두 걸린다
                {"collectTime": boundary, "instanceId": 1002}]   # 같은 시각 · 다른 인스턴스
        if hi == NOW_MS:
            rows.append({"collectTime": NOW_MS - 1, "instanceId": 1001})
        return rows

    tools = _tools(_deploy_windows(seen, items))
    out = await tools.apm_source_changes("was-host01", lookback_minutes=48 * 60)
    assert [(r["instance_id"], r["change_detected_ms"]) for r in out["rows"]] == [
        (1001, NOW_MS - 1), (1001, boundary), (1002, boundary)]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"result": []},               # v1 봉투(빈 목록) — v2 맨 배열이 아님
    {"result": [{"collectTime": 1, "instanceId": 1001}]},
    {},
    None,
    "deploy",
    7,
], ids=["v1_empty", "v1_rows", "empty_object", "null", "string", "number"])
async def test_changes_non_array_body_is_an_error_not_zero_rows(body):
    inner = synthetic_handler()

    def handler(request):
        if request.url.path.startswith("/api-v2/deploy/"):
            return httpx.Response(200, json=body)
        return inner(request)

    with pytest.raises(ApmError) as exc:
        await _tools(handler).apm_source_changes("was-host01")
    assert exc.value.code == API_ERROR and "배열" in exc.value.reason


@pytest.mark.asyncio
async def test_changes_array_of_non_objects_is_not_silently_empty():
    inner = synthetic_handler()

    def handler(request):
        if request.url.path.startswith("/api-v2/deploy/"):
            return httpx.Response(200, json=[1, "x", None])
        return inner(request)

    try:
        out = await _tools(handler).apm_source_changes("was-host01")
    except ApmError as e:
        assert e.code == API_ERROR
        return
    assert out.get("partial") is True or any("모양" in x for x in out["limits"]), out["limits"]


@pytest.mark.asyncio
async def test_changes_second_chunk_failure_keeps_first_chunk_rows_as_partial():
    seen: list[tuple[int, int]] = []
    tools = _tools(_deploy_windows(seen, fail_at=2))
    out = await tools.apm_source_changes("was-host01", lookback_minutes=48 * 60)
    assert out["partial"] is True and out["row_count"] >= 1
    assert any("chunk boom" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_changes_detection_limit_is_always_present_even_with_zero_rows():
    seen: list[tuple[int, int]] = []
    tools = _tools(_deploy_windows(seen, items=lambda lo, hi: []))
    out = await tools.apm_source_changes("was-host01")
    assert out["row_count"] == 0 and any("배포 확정 아님" in x for x in out["limits"])


# ── N-5 시 단위 통계 ─────────────────────────────────────────────────────────

def _row(name: str, calls: int, avg: float, failures: int = 1) -> dict:
    return {"name": name, "calls": calls, "failures": failures, "badResponses": 0,
            "responseTime": avg, "maxResponseTime": int(avg * 2),
            "totalResponseTime": int(calls * avg)}


def _two_domains(rows: dict[int, list[dict]], calls: list[httpx.Request], fail=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path, params = request.url.path, request.url.params
        if path == "/api/domain":
            return httpx.Response(200, json={"result": [{"domainId": d, "name": f"d{d}"}
                                                        for d in rows]})
        if path == "/api/instance":
            d = int(params["domain_id"])
            return httpx.Response(200, json={"result": [
                {"instanceId": d + 1, "name": f"was_{d}", "hostName": "was-host01"}]})
        if path.startswith("/api/status/"):
            d = int(params["domain_id"])
            if fail and d in fail:
                if fail[d] == "timeout":
                    raise httpx.ReadTimeout("slow")
                return httpx.Response(500, json={"exception": {"message": fail[d]}})
            got = rows[d]
            if "max_row" in params:
                got = got[: int(params["max_row"])]
            return httpx.Response(200, json={"result": got})
        return httpx.Response(404)

    return handler


_ROWS = {1000: [_row("select a1", 90, 1.0), _row("select a2", 80, 1.0)],
         2000: [_row("select b1", 99, 1.0), _row("select b2", 70, 1.0)]}


@pytest.mark.asyncio
async def test_status_all_groups_fail_same_code_keeps_server_reasons():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls, fail={1000: "Unknown sort metric: x",
                                                    2000: "Unknown sort metric: x"}))
    with pytest.raises(ApmError) as exc:
        await tools.apm_status_stats("sql", "was-host01", sort_by="x")
    assert exc.value.code == API_ERROR
    assert exc.value.reason.count("Unknown sort metric: x") == 2


@pytest.mark.asyncio
async def test_status_all_groups_fail_mixed_codes_is_source_unavailable():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls, fail={1000: "bad", 2000: "timeout"}))
    with pytest.raises(ApmError) as exc:
        await tools.apm_status_stats("sql", "was-host01")
    assert exc.value.code == SOURCE_UNAVAILABLE and "bad" in exc.value.reason


@pytest.mark.asyncio
async def test_status_one_group_rejects_sort_by_other_answers_partial():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls, fail={2000: "Unknown sort metric: errCount"}))
    out = await tools.apm_status_stats("sql", "was-host01", sort_by="errCount")
    assert out["partial"] is True and {r["domain_id"] for r in out["rows"]} == {1000}
    assert any("Unknown sort metric: errCount" in x for x in out["limits"])


@pytest.mark.asyncio
@pytest.mark.parametrize("sort_by", ["calls&max_row=1", "calls max_row", "1calls", "_calls",
                                     "calls;", "x" * 129, "콜수"])
async def test_status_sort_by_injection_is_rejected_before_http(sort_by):
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls))
    with pytest.raises(ApmError) as exc:
        await tools.apm_status_stats("sql", "was-host01", sort_by=sort_by)
    assert exc.value.code == INVALID_ARGUMENT and calls == [], "HTTP 0회"


@pytest.mark.asyncio
async def test_status_application_name_on_other_kind_is_rejected_before_http():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls))
    for kind in ("sql", "external_call", "EXTERNAL_CALL"):
        with pytest.raises(ApmError):
            await tools.apm_status_stats(kind, "was-host01", application_name="/order")
    assert calls == []
    out = await tools.apm_status_stats(" SQL ", "was-host01", application_name="   ")
    assert out["kind"] == "sql", "공백 이름은 미지정 · kind 는 대소문자·공백 무시"


@pytest.mark.asyncio
async def test_status_full_two_groups_keeps_every_row_and_omits_max_row():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls))
    out = await tools.apm_status_stats("sql", "was-host01", full=True, sort_by="errCount")
    assert out["row_count"] == 4 and all(
        "max_row" not in r.url.params for r in calls if r.url.path == "/api/status/sql")
    assert not any("summary는 표시한" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_status_unmapped_sort_does_not_hide_the_second_group():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains(_ROWS, calls))
    out = await tools.apm_status_stats("sql", "was-host01", n=2, sort_by="errCount")
    assert {r["domain_id"] for r in out["rows"]} == {1000, 2000}


@pytest.mark.asyncio
async def test_status_hour_window_exact_boundary_is_not_widened():
    calls: list[httpx.Request] = []
    tools = _tools(_two_domains({1000: _ROWS[1000]}, calls))
    out = await tools.apm_status_stats(
        "sql", "was-host01", reference_time="2026-09-29T10:00:00+09:00", lookback_minutes=60)
    q = next(r for r in calls if r.url.path == "/api/status/sql").url.params
    assert int(q["end_time"]) % HOUR == 0 and int(q["end_time"]) - int(q["start_time"]) == HOUR
    note = next(x for x in out["limits"] if "시 경계" in x)
    assert "요청" not in note, "넓히지 않았으면 요청→조회 문구가 없다"
    out2 = await tools.apm_status_stats(
        "sql", "was-host01", reference_time="2026-09-29T10:00:01+09:00", lookback_minutes=1)
    q2 = [r for r in calls if r.url.path == "/api/status/sql"][-1].url.params
    assert int(q2["end_time"]) - int(q2["start_time"]) == 2 * HOUR, "1분 구간도 두 정시 칸"
    assert any("요청" in x and "조회" in x for x in out2["limits"])


@pytest.mark.asyncio
async def test_status_summary_zero_calls_has_no_rates():
    calls: list[httpx.Request] = []
    rows = {1000: [{"name": "select 1", "calls": 0, "failures": 0, "badResponses": 0,
                    "responseTime": None, "maxResponseTime": None, "totalResponseTime": None}]}
    out = await _tools(_two_domains(rows, calls)).apm_status_stats("sql", "was-host01")
    s = out["summary"]
    assert (s["calls"], s["failure_rate"], s["response_time_avg_ms"], s["max_response_time_ms"]) \
        == (0, None, None, None)
    assert any("가중" in x for x in out["limits"]), "총 응답시간 칸이 없으면 그 사실을 알린다"


@pytest.mark.asyncio
async def test_status_names_mask_literals_query_values_and_url_credentials():
    inner = synthetic_handler()
    long_sql = "select * from t where a = 'x' and " + " and ".join(
        f"c{i} = {i}" for i in range(800))

    def handler(request):
        p = request.url.path
        if p == "/api/status/sql":
            return httpx.Response(200, json={"result": [_row(long_sql, 1, 1.0),
                                                        _row("select 'it''s', 42 from"
                                                             " dual", 1, 1)]})
        if p == "/api/status/external_call":
            return httpx.Response(200, json={"result": [
                _row("http://admin:S3cretPw@pay.example/api?card=1234&u=kim", 1, 1.0)]})
        if p == "/api/status/application":
            return httpx.Response(200, json={"result": [
                _row("/order?user=kim&token=abc", 1, 1.0)]})
        return inner(request)

    tools = _tools(handler)
    sql = await tools.apm_status_stats("sql", "was-host01")
    first = sql["rows"][0]["name"]
    assert "'x'" not in first and len(first) > 5000, "전문 · 리터럴 가림"
    assert sql["rows"][1]["name"] == "select ?, ? from dual"
    ext = await tools.apm_status_stats("external_call", "was-host01")
    assert "S3cretPw" not in ext["rows"][0]["name"] and "1234" not in ext["rows"][0]["name"]
    app = await tools.apm_status_stats("application", "was-host01")
    assert app["rows"][0]["application"] == "/order?user=<v>&token=<v>"


# ── N-6 지표 카탈로그 ─────────────────────────────────────────────────────────

def _catalog_handler(body, *, seen: list | None = None, fail: bool = False):
    inner = synthetic_handler()

    def handler(request):
        if request.url.path == "/api/metrics":
            if seen is not None:
                seen.append(1)
            if fail:
                return httpx.Response(500, json={"exception": {"message": "catalog down"}})
            return httpx.Response(200, json=body() if callable(body) else body)
        return inner(request)

    return handler


@pytest.mark.asyncio
async def test_catalog_recorded_shape_is_169_rows_in_6_scopes_with_case_insensitive_scope():
    body = recorded_body("GET_api_metrics__ok.json")
    tools = _tools(_catalog_handler(body))
    out = await tools.apm_metrics("catalog")
    counts: dict[str, int] = {}
    for r in out["rows"]:
        counts[r["scope"]] = counts.get(r["scope"], 0) + 1
    assert counts == {"domain": 41, "instance": 60, "business": 29, "application": 25,
                      "sql": 7, "external_call": 7}
    ext = await tools.apm_metrics("CATALOG", scope=" External_Call ")
    assert ext["row_count"] == 7 and {r["scope"] for r in ext["rows"]} == {"external_call"}
    with pytest.raises(ApmError) as exc:
        await tools.apm_metrics("catalog", scope="externalCall")  # 벤더 표기는 받지 않는다
    assert exc.value.code == INVALID_ARGUMENT


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"result": {}}, {"result": None}, {"result": "instance"}, {"result": {"instance": "heap"}},
    {"metrics": {"instance": ["heap_used"]}}, [], None,
], ids=["empty_obj", "null", "string", "group_string_only", "no_result", "array", "none"])
async def test_catalog_shape_violation_is_an_error(body):
    with pytest.raises(ApmError) as exc:
        await _tools(_catalog_handler(body)).apm_metrics("catalog")
    assert exc.value.code == API_ERROR


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"result": {"instance": [1, 2]}},
    {"result": {"instance": "heap_used", "sql": ["count"]}},
    {"result": {"instance": None, "domain": ["service_count"]}},
], ids=["non_string_items", "one_group_not_list", "one_group_null"])
async def test_catalog_partial_shape_violation_is_not_silent(body):
    try:
        out = await _tools(_catalog_handler(body)).apm_metrics("catalog")
    except ApmError as e:
        assert e.code == API_ERROR
        return
    assert out.get("partial") is True or any("모양" in x for x in out["limits"]), out["limits"]


@pytest.mark.asyncio
async def test_catalog_change_detection_counts_additions_and_removals():
    now = [NOW_S]
    current = {"result": {"instance": ["a", "b", "c"], "sql": ["count"]}}
    seen: list[int] = []
    tools = _tools(_catalog_handler(lambda: current, seen=seen), clock=lambda: now[0],
                   extra={"APM_METRIC_CATALOG_TTL_SECONDS": "10"})
    await tools.apm_metrics("catalog")
    current = {"result": {"instance": ["a", "d"], "sql": ["count"], "domain": ["e"]}}
    now[0] += 11
    out = await tools.apm_metrics("catalog")
    note = next(x for x in out["limits"] if "변경 감지" in x)
    assert "추가 2" in note and "삭제 2" in note and len(seen) == 2
    now[0] += 5  # 캐시 수명 안 — 다시 읽지 않는다(변경 고지는 그 수명 동안 남는다)
    again = await tools.apm_metrics("catalog")
    assert len(seen) == 2 and any("변경 감지" in x for x in again["limits"])


def _two_sources_env(**extra: str) -> dict[str, str]:
    return {"JENNIFER_SOURCES": '["bank", "common"]',
            "JENNIFER_BANK_API_URL": "http://bank.test", "JENNIFER_BANK_API_TOKEN": TOKEN,
            "JENNIFER_COMMON_API_URL": "http://common.test",
            "JENNIFER_COMMON_API_TOKEN": TOKEN + "-b",
            "JENNIFER_RATE_LIMIT_PER_SEC": "0", **extra}


def _two_source_tools(handler):
    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    from apm_gateway.config import load_config

    cfg = load_config(_two_sources_env())
    return ApmTools(build_source_set(cfg, transport=httpx.MockTransport(handler)), cfg,
                    clock=lambda: NOW_S)


def _two_source_handler(catalogs: dict[str, dict | None], metric_calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        src = "bank" if request.url.host == "bank.test" else "common"
        path, params = request.url.path, request.url.params
        if path == "/api/metrics":
            body = catalogs[src]
            if body is None:
                return httpx.Response(500, json={"exception": {"message": f"{src} catalog down"}})
            return httpx.Response(200, json=body)
        if path == "/api/domain":
            return httpx.Response(200, json={"result": [{"domainId": 1000, "name": "d"}]})
        if path == "/api/instance":
            iid = 1 if src == "bank" else 2
            return httpx.Response(200, json={"result": [
                {"instanceId": iid, "name": f"{src}_i", "hostName": "h1"}]})
        if path == "/api/dbmetrics/instance":
            metric_calls.append((src, params["metrics"]))
            return httpx.Response(200, json={"result": [{"time": NOW_MS, "value": 1.0}]})
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
async def test_catalog_two_sources_one_down_is_partial_with_status():
    tools = _two_source_tools(_two_source_handler(
        {"bank": {"result": {"instance": ["heap_used"]}}, "common": None}, []))
    out = await tools.apm_metrics("catalog")
    assert out["partial"] is True and out["row_count"] == 1
    assert {s["source_id"]: s["status"] for s in out["sources"]} == {"bank": "ok",
                                                                     "common": "unavailable"}
    tools2 = _two_source_tools(_two_source_handler({"bank": None, "common": None}, []))
    with pytest.raises(ApmError) as exc:
        await tools2.apm_metrics("catalog")
    assert exc.value.code == API_ERROR


@pytest.mark.asyncio
async def test_series_metric_missing_in_one_source_skips_only_that_source():
    metric_calls: list = []
    tools = _two_source_tools(_two_source_handler(
        {"bank": {"result": {"instance": ["socket_count"]}},
         "common": {"result": {"instance": ["heap_used"]}}}, metric_calls))
    out = await tools.apm_metrics("series", hostname="h1", metrics=["socket_count"],
                                  lookback_minutes=10)
    assert metric_calls == [("bank", "socket_count")]
    assert any("socket_count" in x and "생략" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_series_unvalidated_when_catalog_down_is_flagged_partial():
    metric_calls: list = []
    tools = _two_source_tools(_two_source_handler({"bank": None, "common": None}, metric_calls))
    out = await tools.apm_metrics("series", hostname="h1", metrics=["socket_count"],
                                  lookback_minutes=10)
    assert sorted(metric_calls) == [("bank", "socket_count"), ("common", "socket_count")]
    assert out["partial"] is True and any("검증하지 못하고" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_runtime_interval_only_uses_default_metrics_with_default_window():
    seen: list[dict] = []
    inner = synthetic_handler()

    def handler(request):
        if request.url.path == "/api/dbmetrics/instance":
            seen.append(dict(request.url.params))
        if request.url.path == "/api/metrics":
            raise AssertionError("기본 지표는 카탈로그를 읽지 않는다")
        return inner(request)

    out = await _tools(handler).apm_runtime_health("was-host01", interval_minute=15)
    assert {q["interval_minute"] for q in seen} == {"15"}
    assert {q["metrics"] for q in seen} == {"heap_used", "heap_committed", "gc_time_usage"}
    assert out["window"]["minutes"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -5, "abc", 2.5, True, "0"])
async def test_interval_minute_non_positive_or_non_int_is_rejected(bad):
    with pytest.raises(ApmError) as exc:
        await _tools(synthetic_handler()).apm_runtime_health(
            "was-host01", lookback_minutes=10, interval_minute=bad)
    assert exc.value.code == INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_superscript_interval_is_invalid_argument_not_value_error():
    with pytest.raises(ApmError) as exc:
        await _tools(synthetic_handler()).apm_runtime_health(
            "was-host01", lookback_minutes=10, interval_minute="²")
    assert exc.value.code == INVALID_ARGUMENT


@pytest.mark.asyncio
async def test_metrics_argument_must_be_a_list_and_dedupes():
    seen: list[str] = []
    inner = synthetic_handler()

    def handler(request):
        if request.url.path == "/api/dbmetrics/instance":
            seen.append(request.url.params["metrics"])
        return inner(request)

    tools = _tools(handler)
    with pytest.raises(ApmError):
        await tools.apm_metrics("series", hostname="was-host01", metrics="heap_used")
    await tools.apm_metrics("series", hostname="was-host01", metrics=["heap_used", "heap_used",
                                                                      "heap_used_mb"],
                            lookback_minutes=10)
    assert sorted(seen) == ["heap_used", "heap_used"], "같은 식별자는 인스턴스당 1회"


# ── N-8 허용목록 · §2.4 경로 변수 ───────────────────────────────────────────────

@pytest.mark.parametrize("value", ["١٢", "²", "１２", True, "12 ", " 12", "0x10", "1e3"])
def test_path_var_int_rejects_unicode_digits_and_non_ints(value):
    with pytest.raises(NotAllowedError):
        build_path("/api-v2/deploy/{domainId}", {"domainId": value})


def test_status_keys_are_exact_per_kind_and_v2_query_keys_are_exact():
    assert ALLOWED["/api/status/sql"].query_keys == ALLOWED["/api/status/external_call"].query_keys
    assert "application_name" in ALLOWED["/api/status/application"].query_keys
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api-v2/deploy/1000",
                      {"startTime": 1, "endTime": 2, "domain_id": 1000})
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api-v2/deploy/1000", {"startTime": 1})
    for bad in ("/api-v2/deploy/1000/", "/api-v2/deploy/", "/api-v2/deploy/1000.xml",
                "/api-v2/deploy/../1000", "/api-v2/deploy/1000?x=1"):
        assert match_template(bad) is None, bad


def test_template_match_with_trailing_newline_is_preexisting_and_unreachable():
    """기존 템플릿 정규식(`^…$`)은 끝 줄바꿈을 받는다(54e1597 부터 · W2 신규 아님).

    경로는 코드 템플릿 + 형식 검사한 경로 변수로만 만들어져 외부 입력이 줄바꿈을 넣을 수 없다 —
    기록용(결함 아님). `build_path`는 줄바꿈 값을 거부한다.
    """
    assert match_template("/api/domain\n") == "/api/domain"
    with pytest.raises(NotAllowedError):
        build_path("/api-v2/deploy/{domainId}", {"domainId": "1000\n"})


# ── apm_events error_type 접두만 있는 값 ─────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("etype", ["ERROR_", "warning_"])
async def test_prefix_only_error_type_is_invalid(etype):
    with pytest.raises(ApmError) as exc:
        await _tools(synthetic_handler()).apm_events("was-host01", error_type=etype,
                                                      record="error")
    assert exc.value.code == INVALID_ARGUMENT
