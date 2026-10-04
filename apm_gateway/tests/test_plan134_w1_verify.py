"""plans/134 W1 게이트웨이(N-1~N-4) 검증자 적대적 테스트.

구현 테스트(`test_w1_coverage.py`)와 별개다.

확인하는 것(SPEC-apm-question-coverage §0.4 · §2.1 · §4.3 · §5 W1 행 · COV 표 B·C W1 행):
  - 상한이 정말 없는가: 인스턴스 2,000 · 이벤트 10,000(기본 `n` = 전부) · 60분 X-View(app_health
    시 단위 보충 포함) · 호스트당 인스턴스 12 · 3일 이벤트 창(종전 24시간 절단 없음).
  - `n` 0·음수·문자열 · `top_k` 0 · `full`과 `n` 동시.
  - `level_mode=exact` + 대소문자 · `error_type` 접두 변형 · `record=error`.
  - 식별자 가림(`user_id`·`client_id`) — 행 · 프로파일 상세 · 결과 파일(스풀 청크) 어디에도
    원값 없음.
  - 텍스트 마스킹(W1 새 칸).
  - 시 단위 합계는 받은 **전** 애플리케이션 행(종전 `max_row=20` 누락 교정).

결함 재현은 `xfail(strict=True)`다(고치면 XPASS로 실패해 표지를 걷게 된다). 외부 네트워크 0 —
`httpx.MockTransport`와 127.0.0.1 목 Open API만 쓴다.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from apm_gateway.application.jobs import FILE_ONLY_KEY, JobManager
from apm_gateway.application.spool import Spool
from apm_gateway.interface.server import create_server
from conftest import DOMAIN, NOW_MS, make_tools, synthetic_fixtures, write_fixtures

from apm_gateway.config import JobConfig

HOST = "was-host01"
RAW_USER = "honggildong77"
RAW_CLIENT = "client-secret-777"


def _handler(
    *,
    instances: int = 2,
    events: int = 0,
    tx_per_minute: int = 0,
    app_rows: int = 0,
    errors: list[dict] | None = None,
    calls: list[tuple[str, dict]] | None = None,
    message_tail: str = "",
):
    """스펙 5.6.4 필드명 합성 응답 — 도메인 1개 · 호스트 1개(인스턴스 `instances`개)."""
    inst_rows = [
        {"instanceId": 3000 + i, "name": f"was_{i:02d}", "hostName": HOST,
         "instanceOid": 70_000 + i, "description": f"담당 lee{i}@example.com"}
        for i in range(instances)
    ]
    event_rows = [
        {"domainId": DOMAIN, "domainName": "d", "instanceId": 3000 + (i % instances),
         "instanceName": f"was_{i % instances:02d}", "errorType": "ERROR_OUTOFMEMORY",
         "metricsName": "", "eventLevel": ("FATAL", "Warning", "normal")[i % 3],
         "message": f"ev {i} call 010-9876-5432{message_tail}", "value": i,
         "time": str(NOW_MS - 10_000_000 + i * 100), "txid": str(i),
         "applicationName": f"/a?u=x{i}", "instanceOid": 70_000}
        for i in range(events)
    ]
    app_status = [
        {"name": f"/app/{i}?u=kim{i}", "calls": 10, "failures": 1, "badResponses": 0,
         "responseTime": 100.0 + i, "maxResponseTime": 1000 + i,
         "totalResponseTime": (100 + i) * 10}
        for i in range(app_rows)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        q = dict(request.url.params)
        if calls is not None:
            calls.append((path, q))
        if path == "/api/domain":
            return httpx.Response(200, json={"result": [
                {"domainId": DOMAIN, "name": "d", "description": "도메인 park@example.com"}]})
        if path == "/api/instance":
            return httpx.Response(200, json={"result": inst_rows})
        if path == "/api/realtime/instance":
            rows = [{"instanceId": r["instanceId"], "domainId": DOMAIN, "heapUsed": 1,
                     "heapCommitted": 2, "visitDay": 5,
                     "instanceDescription": "설명 choi@example.com 900101-1234567",
                     "instanceOid": r["instanceOid"]} for r in inst_rows]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/transaction/time":
            start = int(q["start_time"])
            rows = [
                {"domainId": DOMAIN, "instanceId": 3000 + (k % instances),
                 "txid": f"{start}-{k}", "responseTime": (start // 60_000 * 13 + k) % 9_000,
                 "endTime": str(start + k), "applicationName": "/x?card=1111",
                 "userId": RAW_USER, "clientId": RAW_CLIENT, "clientIp": "10.20.30.40",
                 "guid": f"g-{start}-{k}", "instanceOid": 70_000}
                for k in range(tx_per_minute)
            ]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/status/application":
            return httpx.Response(200, json={"result": app_status})
        if path == "/api/dbsearch/event":
            rows = event_rows
            if q.get("level"):
                rows = [r for r in rows if r["eventLevel"].upper() == q["level"].upper()]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/dbsearch/error":
            rows = list(errors or [])
            if q.get("error_type"):
                rows = [r for r in rows if r["errorType"].upper() == q["error_type"].upper()]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/dbmetrics/instance":
            return httpx.Response(200, json={"result": [{"time": str(NOW_MS), "value": 1}]})
        if path == "/api/activeService/list":
            rows = [{"domainId": DOMAIN, "instanceId": r["instanceId"], "txid": str(5000 + i),
                     "elapseTime": 1000 + i, "statusMessage": "lock by kim@example.com",
                     "application": "/p?card=4444", "alias": "/p?card=4444",
                     "sessionId": 10 + i, "threadHash": 20 + i, "runningMode": "SQL",
                     "runningFullText": "select 1 from t where id = 42"}
                    for i, r in enumerate(inst_rows)]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/transaction/txid":
            return httpx.Response(200, json={"result": [
                {"txid": "1", "domainId": DOMAIN, "userId": RAW_USER, "clientId": RAW_CLIENT,
                 "instanceOid": 70_000}]})
        if path == "/api/transaction/profile.txt":
            return httpx.Response(200, text="START\nEND", headers={"content-type": "text/plain"})
        if path == "/api/transaction/sql":
            return httpx.Response(200, json={"result": []})
        return httpx.Response(404)

    return handler


def _tools(handler):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    return tools


def _mcp(tmp_path: Path, handler):
    spool = tmp_path / "spool"
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler),
                          extra={"APM_SPOOL_DIR": str(spool)})
    jobs = JobManager(Spool(spool), JobConfig(spool_dir=spool), envelope=tools.ok,
                      error_envelope=tools.err)
    return create_server(tools, jobs=jobs), jobs, spool


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


async def _chunks(mcp, out: dict) -> list[dict]:
    rows: list[dict] = []
    for chunk in out["artifact"]["chunks"]:
        rows += _json(await mcp.call_tool(
            "apm_job_read", {"job_id": out["artifact"]["job_id"], "chunk": chunk["index"]}))["rows"]
    return rows


# ── 상한 없음 (N-4 · D-296 ④) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_2000_instances_listing_is_whole_and_ordered(tmp_path):
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=2000))
    out = _json(await mcp.call_tool("apm_instance_map", {}))
    assert out["total_row_count"] == 2000 and out["row_count"] == 500
    assert sum(c["rows"] for c in out["artifact"]["chunks"]) == 2000
    rows = await _chunks(mcp, out)
    assert [r["instance_id"] for r in rows] == [3000 + i for i in range(2000)]
    assert out["artifact"]["file_only_columns"] == ["instance_oid"]
    assert all("instance_oid" not in r for r in out["rows"])
    assert all("lee" not in r["description"] for r in rows), "결과 파일도 마스킹본"
    await jobs.aclose()


@pytest.mark.asyncio
async def test_10000_events_default_n_is_all_and_3_day_window_not_cut(tmp_path):
    calls: list[tuple[str, dict]] = []
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=1, events=10_000, calls=calls))
    out = _json(await mcp.call_tool("apm_events", {"hostname": HOST,
                                                   "lookback_minutes": 3 * 1440}))
    assert out["total_row_count"] == 10_000, "n 미지정 = 전부(종전 50건 상한 없음)"
    assert out["window"]["minutes"] == 3 * 1440, "종전 24시간 절단 없음"
    (q,) = [q for p, q in calls if p == "/api/dbsearch/event"]
    assert int(q["end_time"]) - int(q["start_time"]) == 3 * 1440 * 60_000
    rows = await _chunks(mcp, out)
    assert len(rows) == 10_000
    times = [r["time_ms"] for r in rows]
    assert times == sorted(times, reverse=True)
    assert all("010-9876-5432" not in r["message"] for r in rows), "결과 파일 메시지도 마스킹본"
    await jobs.aclose()


@pytest.mark.asyncio
async def test_60_minute_xview_app_health_and_hourly_whole():
    calls: list[tuple[str, dict]] = []
    tools = _tools(_handler(instances=2, tx_per_minute=40, app_rows=30, calls=calls))
    out = await tools.apm_app_health(HOST, lookback_minutes=60)
    xview = [q for p, q in calls if p == "/api/transaction/time"]
    assert len(xview) == 60, "1분 조각을 창 전체만큼(종전 10분 상한 없음)"
    starts = sorted(int(q["start_time"]) for q in xview)
    assert starts[-1] - starts[0] == 59 * 60_000
    assert sum(r["window"]["calls"] for r in out["rows"]) == 60 * 40
    hourly = out["hourly"]
    assert hourly["application_count"] == 30 and hourly["calls"] == 300, "전 애플리케이션 합계"
    assert len(hourly["top_applications"]) == 5
    assert all("kim" not in a["application"] for a in hourly["top_applications"])
    assert all("max_row" not in q for p, q in calls if p == "/api/status/application")


@pytest.mark.asyncio
async def test_12_instances_on_one_host_are_all_queried():
    calls: list[tuple[str, dict]] = []
    tools = _tools(_handler(instances=12, tx_per_minute=12, calls=calls))
    health = await tools.apm_app_health(HOST)
    assert len(health["rows"]) == 12
    assert not any("상한" in x or "개만" in x for x in health["limits"])
    rt = await tools.apm_runtime_health(HOST, lookback_minutes=30)
    assert sum(1 for p, _ in calls if p == "/api/dbmetrics/instance") == 12 * 3
    assert all("trend" in r for r in rt["rows"])
    act = await tools.apm_active_services(HOST, full=True)
    assert {r["instance_id"] for r in act["rows"]} == {3000 + i for i in range(12)}
    slow = await tools.apm_slow_transactions(HOST, lookback_minutes=2, full=True)
    (ids,) = {q["instance_id"] for p, q in calls if p == "/api/transaction/time"}
    assert ids == ",".join(str(3000 + i) for i in range(12))
    assert slow["row_count"] == 2 * 12


# ── n · full · top_k ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("apm_slow_transactions", {"n": 0}),
    ("apm_slow_transactions", {"n": -3}),
    ("apm_active_services", {"n": 0}),
    ("apm_events", {"n": -1}),
    ("apm_transaction_profile", {"domain_id": DOMAIN, "txid": "1", "time_ms": NOW_MS,
                                 "top_k": 0}),
])
async def test_n_below_one_is_invalid_argument(tmp_path, tool, args):
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=1, tx_per_minute=3))
    out = _json(await mcp.call_tool(tool, {"hostname": HOST, **args}))
    assert out["error"] == "invalid_argument"
    await jobs.aclose()


@pytest.mark.asyncio
async def test_n_string_is_rejected_by_schema_not_silently_defaulted(tmp_path):
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=1, tx_per_minute=3))
    try:
        out = _json(await mcp.call_tool("apm_slow_transactions", {"hostname": HOST, "n": "abc"}))
    except Exception as e:  # noqa: BLE001 — FastMCP 스키마 검증 오류(전송에서는 isError)
        assert "n" in str(e)
    else:
        assert out.get("error") == "invalid_argument", out
    numeric = _json(await mcp.call_tool("apm_slow_transactions",
                                        {"hostname": HOST, "n": "2", "lookback_minutes": 3}))
    assert numeric["row_count"] == 2
    await jobs.aclose()


@pytest.mark.asyncio
async def test_huge_n_and_full_with_n_return_every_row():
    tools = _tools(_handler(instances=2, tx_per_minute=30, events=40))
    big = await tools.apm_slow_transactions(HOST, lookback_minutes=3, n=10**9)
    assert big["row_count"] == 90
    both = await tools.apm_slow_transactions(HOST, lookback_minutes=3, n=3, full=True)
    ranked = [r["response_time_ms"] for r in both["rows"]]
    assert both["row_count"] == 90 and ranked == sorted(ranked, reverse=True), "full이 n을 이긴다"
    ev = await tools.apm_events(HOST, n=4, full=True)
    assert ev["row_count"] == 40
    ev4 = await tools.apm_events(HOST, n=4)
    assert ev4["row_count"] == 4 and [r["time_ms"] for r in ev4["rows"]] == [
        r["time_ms"] for r in ev["rows"][:4]]
    act = await tools.apm_active_services(HOST, n=1, full=True)
    assert act["row_count"] == 2


# ── level_mode · error_type · record (N-3) ───────────────────────────────


@pytest.mark.asyncio
async def test_level_exact_is_case_insensitive_and_min_is_floor():
    calls: list[tuple[str, dict]] = []
    tools = _tools(_handler(instances=1, events=30, calls=calls))
    exact = await tools.apm_events(HOST, level="FATAL", level_mode="EXACT")
    assert {r["level"] for r in exact["rows"]} == {"fatal"} and exact["row_count"] == 10
    assert [q for p, q in calls if p == "/api/dbsearch/event"][-1]["level"] == "FATAL"
    warn_exact = await tools.apm_events(HOST, level="Warning", level_mode="exact")
    assert {r["level"] for r in warn_exact["rows"]} == {"warning"}
    floor = await tools.apm_events(HOST, level="warning", level_mode="Min")
    assert {r["level"] for r in floor["rows"]} == {"fatal", "warning"}
    assert "level" not in [q for p, q in calls if p == "/api/dbsearch/event"][-1]


_ERRORS = [
    {"domainId": DOMAIN, "instanceId": 3000, "instanceName": "was_00",
     "errorType": "SQL_EXCEPTION", "message": "ORA-1 mail kim@example.com 010-1111-2222",
     "time": str(NOW_MS - 5_000 - i), "txid": str(i), "applicationName": "/q?id=77",
     "profileIndex": i}
    for i in range(3)
]


@pytest.mark.asyncio
async def test_record_error_rows_are_masked_and_newest_first():
    tools = _tools(_handler(instances=1, errors=_ERRORS))
    out = await tools.apm_events(HOST, record="ERROR", error_type="sql_exception")
    assert out["record"] == "error" and out["row_count"] == 3
    assert [r["time_ms"] for r in out["rows"]] == sorted(
        (r["time_ms"] for r in out["rows"]), reverse=True)
    blob = json.dumps(out, ensure_ascii=False)
    assert "kim@example.com" not in blob and "010-1111-2222" not in blob and "id=77" not in blob
    assert out["errors_by_type"] == [{"error_type": "SQL_EXCEPTION", "count": 3}]


@pytest.mark.asyncio
async def test_error_type_invalid_forms_are_rejected():
    from apm_gateway.domain.errors import ApmError

    tools = _tools(_handler(instances=1))
    for bad in ("SQL-EXCEPTION", "SQL EXCEPTION", "x" * 65, "OOM;DROP"):
        with pytest.raises(ApmError) as exc:
            await tools.apm_events(HOST, error_type=bad)
        assert exc.value.code == "invalid_argument", bad


@pytest.mark.asyncio
async def test_error_type_prefix_variant_still_finds_error_records(mock_server_factory, tmp_path):
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", synthetic_fixtures()),
                                  "connected")
    tools, _ = make_tools(base)
    plain = await tools.apm_events("was-host01", record="error", error_type="SQL_EXCEPTION")
    assert plain["row_count"] == 1
    prefixed = await tools.apm_events("was-host01", record="error",
                                      error_type="ERROR_SQL_EXCEPTION")
    assert prefixed["row_count"] == plain["row_count"]


@pytest.mark.asyncio
async def test_error_type_prefix_on_events_matches_both_spellings(mock_server_factory, tmp_path):
    """이벤트 쪽은 접두 무시가 실제로 성립한다(OUTOFMEMORY ↔ ERROR_OUTOFMEMORY)."""
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", synthetic_fixtures()),
                                  "connected")
    tools, _ = make_tools(base)
    a = await tools.apm_events("was-host01", error_type="outofmemory")
    b = await tools.apm_events("was-host01", error_type="ERROR_OUTOFMEMORY")
    assert a["row_count"] == b["row_count"] == 1


# ── 식별자 가림 · 텍스트 마스킹 (SPEC §4.3) ─────────────────────────────────


@pytest.mark.asyncio
async def test_identifiers_are_masked_in_rows_profile_and_spool_files(tmp_path):
    mcp, jobs, spool = _mcp(tmp_path, _handler(instances=1, tx_per_minute=200))
    out = _json(await mcp.call_tool("apm_slow_transactions",
                                    {"hostname": HOST, "lookback_minutes": 5, "full": True}))
    assert out["total_row_count"] == 1000 and "artifact" in out
    row = out["rows"][0]
    assert row["user_id"] == "h***" and row["client_id"] == "c***"
    assert row["client_ip"] == "10.20.*.*" and "card=<v>" in row["application"]
    prof = _json(await mcp.call_tool("apm_transaction_profile", {
        "hostname": HOST, "domain_id": DOMAIN, "txid": "1", "time_ms": NOW_MS}))
    assert prof["rows"][0]["transaction"]["user_id"] == "h***"
    chunk_rows = await _chunks(mcp, out)
    assert len(chunk_rows) == 1000
    on_disk = "".join(p.read_text(encoding="utf-8", errors="replace")
                      for p in spool.rglob("*") if p.is_file())
    for raw in (RAW_USER, RAW_CLIENT, "10.20.30.40", "card=1111"):
        assert raw not in json.dumps(out) and raw not in json.dumps(prof), raw
        assert raw not in json.dumps(chunk_rows) and raw not in on_disk, raw
    await jobs.aclose()


def test_mask_identifier_edges():
    from apm_gateway.application.masking import mask_identifier

    assert mask_identifier("") == "" and mask_identifier(None) == ""
    assert mask_identifier("ab") == "***" and mask_identifier("홍길동") == "홍***"
    assert mask_identifier(12345) == "1***"


@pytest.mark.asyncio
async def test_new_free_text_fields_are_masked():
    tools = _tools(_handler(instances=2))
    inst = await tools.apm_instance_map(HOST)
    health = await tools.apm_app_health(HOST)
    act = await tools.apm_active_services(HOST)
    blob = json.dumps([inst, health, act], ensure_ascii=False)
    for raw in ("lee0@example.com", "park@example.com", "choi@example.com", "900101-1234567",
                "kim@example.com", "card=4444", "id = 42"):
        assert raw not in blob, raw
    assert {r["active_ref"]["session_id"] for r in act["rows"]} == {10, 11}, (
        "정수 세션 ID는 보존(F-15 입력)")


@pytest.mark.asyncio
async def test_profile_transaction_instance_oid_is_file_only(tmp_path):
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=1))
    prof = _json(await mcp.call_tool("apm_transaction_profile", {
        "hostname": HOST, "domain_id": DOMAIN, "txid": "1", "time_ms": NOW_MS}))
    await jobs.aclose()
    assert "instance_oid" not in (prof["rows"][0]["transaction"] or {})


@pytest.mark.asyncio
async def test_file_only_key_never_leaks_through_mcp(tmp_path):
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=3, tx_per_minute=5, events=6))
    for tool, args in (("apm_slow_transactions", {"lookback_minutes": 2}),
                       ("apm_events", {}), ("apm_active_services", {}),
                       ("apm_app_health", {})):
        out = _json(await mcp.call_tool(tool, {"hostname": HOST, **args}))
        assert FILE_ONLY_KEY not in out, tool
        assert all("instance_oid" not in r for r in out["rows"]), tool
    await jobs.aclose()



@pytest.mark.asyncio
async def test_long_free_text_is_whole_in_result_file(tmp_path):
    tail = " | stack " + "at com.example.Foo.bar(Foo.java:42) " * 40
    mcp, jobs, _ = _mcp(tmp_path, _handler(instances=1, events=600, message_tail=tail))
    out = _json(await mcp.call_tool("apm_events", {"hostname": HOST}))
    rows = await _chunks(mcp, out)
    await jobs.aclose()
    assert len(rows) == 600
    assert max(len(r["message"]) for r in rows) > 300, "결과 파일에는 마스킹된 전문"
