"""plans/134 W1 게이트웨이 — COV 표 B·C의 W1 행 · SPEC-apm-question-coverage §2.1·§4.3·§5 W1 행.

테스트 이름 끝의 주석이 COV 테스트 ID다(`T-SCH-<스키마>-fields` · `T-MASK-<필드>` ·
`T-COV-<경로>-arg-<인자>` · SPEC §10 ③ 대량). 인자 전달은 목 Open API 서버의 `/__mock/hits`
(`query` = 실제 쿼리 키·값)로, 대량은 MockTransport 합성 응답으로 본다. 합성 픽스처는 스펙 5.6.4
필드명 기반이다(실응답 모양은 W10). 외부 네트워크 0.
"""

from __future__ import annotations

import json
import urllib.request

import httpx
import pytest
from apm_gateway.application.jobs import FILE_ONLY_KEY, TEXT_PARTS_KEY, JobManager
from apm_gateway.application.spool import Spool
from apm_gateway.interface.server import create_server
from conftest import DOMAIN, NOW_MS, make_tools, synthetic_fixtures, write_fixtures

from apm_gateway.config import JobConfig


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


# ── N-1 필드 보존 · 마스킹 (COV 표 C W1) ──────────────────────


@pytest.mark.asyncio
async def test_instance_fields_and_masking(synth):  # T-SCH-Instance-fields · T-MASK-description
    _, tools = synth
    out = await tools.apm_instance_map("was-host01")
    row = out["rows"][0]
    assert row["config_file_path"] == "/opt/jennifer/agent/was01_a.conf"
    assert "kim@example.com" not in row["description"] and "주문 WAS" in row["description"]
    assert "park@example.com" not in row["domain_description"] and row["domain_description"]
    assert row["instance_oid"] == 50_000 and out[FILE_ONLY_KEY] == ["instance_oid"]
    listing = await tools.apm_instance_map()
    assert all("description" in r and "hostname" in r for r in listing["rows"])


@pytest.mark.asyncio
async def test_realtime_fields_in_app_and_runtime_health(synth):
    # T-SCH-RealtimeInstanceData-fields · T-MASK-instanceDescription
    _, tools = synth
    app = await tools.apm_app_health("was-host01")
    row = next(r for r in app["rows"] if r["instance_id"] == 1001)
    assert (row["visit_day"], row["visit_hour"], row["hit_day"], row["hit_hour"]) == (
        1200,
        80,
        45_000,
        3_100,
    )
    assert [row[f"active_range_count_{i}"] for i in range(4)] == [20, 8, 5, 4]
    assert row["service_rate_by_range"] == {"0": 0.7, "1": 0.2, "2": 0.08, "3": 0.02}
    assert "lee@example.com" not in row["instance_description"]
    assert "1234-5678" not in row["instance_description"] and row["instance_oid"] == 50_000
    assert any("단위·「하루」 경계" in x for x in app["limits"])  # COV E-07
    rt = await tools.apm_runtime_health("was-host01")
    row = next(r for r in rt["rows"] if r["instance_id"] == 1001)
    assert (row["thread_daemon"], row["thread_started"], row["socket_count"]) == (150, 4000, 90)
    assert (row["file_count"], row["collection_count"]) == (230, 17)


@pytest.mark.asyncio
async def test_realtime_asks_only_target_instances(synth):  # T-COV-RT-INSTANCE-arg-instance_id
    base, tools = synth
    await tools.apm_app_health("was-host01")
    assert _queries(base, "/api/realtime/instance")[-1] == {
        "domain_id": str(DOMAIN),
        "instance_id": "1001,1002",
    }


@pytest.mark.asyncio
async def test_active_service_fields_and_ref(synth):
    # T-SCH-ActiveServiceData-fields · T-MASK-alias · T-MASK-statusMessage
    _, tools = synth
    out = await tools.apm_active_services("was-host01")
    row = out["rows"][0]
    assert row["session_id"] == 31_000 + 3 and row["thread_hash"] == 99_003
    assert row["active_ref"] == {
        "source_id": "default",
        "domain_id": DOMAIN,
        "txid": "8003",
        "session_id": 31_003,
        "thread_hash": 99_003,
    }
    assert (row["cpu_ms"], row["sql_count"], row["fetch_count"], row["running_ms"]) == (
        1_203,
        21,
        4,
        640_000,
    )
    assert (row["running_hash"], row["running_sherpa_oracle_instance"]) == (77_003, "ORA1")
    assert row["running_sherpa_oracle_seq"] == 3 and row["instance_name"] == "was01_a"
    assert row["domain_name"] == "demo-domain" and row["business_names"] == ["결제"]
    assert "card=<v>" in row["application_alias"] and "1234" not in row["application_alias"]
    assert "kim@example.com" not in row["status_message"]
    assert row["instance_oid"] == 50_000


@pytest.mark.asyncio
async def test_transaction_fields_and_identifier_masking(synth):
    # T-SCH-TransactionData-fields(guid · W1 확장 필드) · SPEC §4.3 식별자 가림
    _, tools = synth
    out = await tools.apm_slow_transactions("was-host01", lookback_minutes=5)
    row = out["rows"][0]
    assert row["guid"] == "guid-0000" and row["client_ip"] == "192.168.*.*"
    assert row["user_id"] == "k***" and row["client_id"] == "c***"
    assert "kimcs01" not in json.dumps(out) and "client-kim" not in json.dumps(out)
    assert (row["sql_count"], row["fetch_count"], row["external_call_count"]) == (12, 3, 1)
    assert row["start_time_ms"] == NOW_MS - 34_000 and row["collect_time_ms"] == NOW_MS - 29_000
    assert (row["frontend_ms"], row["is_async"], row["link_root"], row["has_stacktrace"]) == (
        150,
        False,
        True,
        True,
    )
    assert row["business_ids"] == [7, 9] and row["business_names"] == ["주문", "결제"]
    assert row["instance_name"] == "was01_a" and row["domain_name"] == "demo-domain"
    profile = await tools.apm_transaction_profile("was-host01", DOMAIN, "9000", NOW_MS)
    detail = profile["rows"][0]["transaction"]
    assert detail["user_id"] == "k***" and detail["client_ip"] == "192.168.*.*"


@pytest.mark.asyncio
async def test_event_fields(synth):  # T-SCH-EventData-fields
    _, tools = synth
    out = await tools.apm_events("was-host01")
    row = out["rows"][0]
    assert row["domain_name"] == "demo-domain" and row["domain_id"] == DOMAIN
    assert row["instance_oid"] == 50_000 and out["record"] == "event"


@pytest.mark.asyncio
async def test_error_record_rows(synth):
    # T-SCH-ErrorData-fields · T-MASK-applicationName · T-MASK-message
    _, tools = synth
    out = await tools.apm_events("was-host01", record="error")
    assert out["record"] == "error" and out["row_count"] == 1
    row = out["rows"][0]
    assert row["error_type"] == "SQL_EXCEPTION" and row["value"] == 1.0
    assert (row["domain_id"], row["domain_name"], row["instance_name"]) == (
        DOMAIN,
        "demo-domain",
        "was01_a",
    )
    assert row["profile_index"] == 14 and row["txid"] == "9000"
    assert row["profile_ref"] == {
        "source_id": "default",
        "domain_id": DOMAIN,
        "txid": "9000",
        "time_ms": NOW_MS - 60_000,
    }
    assert "user=<v>" in row["application"] and "kim@example.com" not in row["message"]


@pytest.mark.asyncio
async def test_application_status_all_fields_in_hourly(synth):
    # T-SCH-ApplicationStatus-fields(25필드 — W1에 당겨 보존) · N-2 합계 · max_row 미지정
    base, tools = synth
    out = await tools.apm_app_health("was-host01", lookback_minutes=30)
    top = out["hourly"]["top_applications"][0]
    for key in (
        "response_time_stddev_ms",
        "cpu_ms_per_tx",
        "sql_ms_per_tx",
        "fetch_ms_per_tx",
        "external_ms_per_tx",
        "sqls",
        "sqls_per_tx",
        "fetches",
        "fetches_per_tx",
        "external_calls",
        "external_calls_per_tx",
        "frontend_measurements",
        "frontend_ms",
        "network_ms",
        "total_response_ms",
        "total_cpu_ms",
        "total_sql_ms",
        "total_fetch_ms",
        "total_external_ms",
    ):
        assert top[key] is not None, key
    assert out["hourly"]["response_time_avg_ms"] == pytest.approx(900.0)  # 270000 / 300
    assert all("max_row" not in q for q in _queries(base, "/api/status/application"))


# ── N-3 이벤트 인자 (COV 표 B W1) ─────────────────────────────


@pytest.mark.asyncio
async def test_level_exact_goes_to_api_and_min_stays_local(synth):  # T-COV-EVENT-arg-level
    base, tools = synth
    out = await tools.apm_events("was-host01", level="fatal", level_mode="exact")
    assert out["row_count"] == 1 and any("정확 일치" in x for x in out["limits"])
    assert _queries(base, "/api/dbsearch/event")[-1]["level"] == "FATAL"
    warn = await tools.apm_events("was-host01", level="warning", level_mode="exact")
    assert warn["row_count"] == 0  # FATAL은 warning 정확 일치가 아니다
    await tools.apm_events("was-host01", level="warning")  # min(기본) — API에 level 없음
    assert "level" not in _queries(base, "/api/dbsearch/event")[-1]


@pytest.mark.asyncio
async def test_error_type_goes_to_api_and_filters_events(synth):  # T-COV-ERROR-arg-error_type
    base, tools = synth
    out = await tools.apm_events("was-host01", error_type="sql_exception")
    assert _queries(base, "/api/dbsearch/error")[-1]["error_type"] == "SQL_EXCEPTION"
    assert out["row_count"] == 0  # 이벤트(OUTOFMEMORY)는 같은 유형이 아니다
    assert out["errors_by_type"] == [{"error_type": "SQL_EXCEPTION", "count": 1}]
    oom = await tools.apm_events("was-host01", error_type="OUTOFMEMORY")
    assert oom["row_count"] == 1 and oom["errors_by_type"] == []  # 접두 ERROR_ 무시


@pytest.mark.asyncio
async def test_event_option_validation(synth):
    from apm_gateway.domain.errors import ApmError

    _, tools = synth
    for kwargs in (
        {"level_mode": "exact"},
        {"level_mode": "max", "level": "fatal"},
        {"record": "rows"},
        {"error_type": "SQL EXCEPTION"},
        {"n": 0},
    ):
        with pytest.raises(ApmError) as exc:
            await tools.apm_events("was-host01", **kwargs)
        assert exc.value.code == "invalid_argument", kwargs


# ── N-4 상한 제거 · 대량 (SPEC §10 ③) ────────────────────────


def _bulk_handler(
    *,
    instances: int = 3,
    host: str = "was-host01",
    events: int = 0,
    tx_per_minute: int = 0,
    error_types: int = 0,
    sqls: int = 1,
    profile_lines: int = 3,
    calls: list[str] | None = None,
):
    """대량 합성 응답(스펙 필드명) — 도메인 1개."""
    inst_rows = [
        {"instanceId": 2000 + i, "name": f"{host}_{i}", "hostName": host, "instanceOid": i}
        for i in range(instances)
    ]
    event_rows = [
        {
            "domainId": DOMAIN,
            "domainName": "d",
            "instanceId": 2000 + (i % instances),
            "instanceName": f"{host}_{i % instances}",
            "errorType": "ERROR_X" if i % 2 else "",
            "metricsName": "" if i % 2 else "TPS",
            "eventLevel": "WARNING",
            "message": f"event {i}",
            "value": i,
            "time": str(NOW_MS - 1_000_000 + i * 10),
            "txid": "",
        }
        for i in range(events)
    ]
    error_rows = [
        {
            "domainId": DOMAIN,
            "instanceId": 2000,
            "errorType": f"TYPE_{i:02d}",
            "message": "m",
            "time": str(NOW_MS - 1_000),
            "txid": "",
        }
        for i in range(error_types)
        for _ in range(i + 1)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        q = request.url.params
        if calls is not None:
            calls.append(path)
        if path == "/api/domain":
            return httpx.Response(200, json={"result": [{"domainId": DOMAIN, "name": "d"}]})
        if path == "/api/instance":
            return httpx.Response(200, json={"result": inst_rows})
        if path == "/api/realtime/instance":
            rows = [{"instanceId": r["instanceId"], "heapUsed": 1, "heapCommitted": 2}
                    for r in inst_rows]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/transaction/time":
            start = int(q["start_time"])
            rows = [
                {
                    "domainId": DOMAIN,
                    "instanceId": 2000,
                    "txid": f"{start}-{k}",
                    "responseTime": (start // 1000 + k * 7) % 10_000,
                    "endTime": str(start + k),
                    "applicationName": "/x",
                }
                for k in range(tx_per_minute)
            ]
            return httpx.Response(200, json={"result": rows})
        if path == "/api/dbsearch/event":
            return httpx.Response(200, json={"result": event_rows})
        if path == "/api/dbsearch/error":
            return httpx.Response(200, json={"result": error_rows})
        if path == "/api/dbmetrics/instance":
            return httpx.Response(200, json={"result": [{"time": str(NOW_MS), "value": 1}]})
        if path == "/api/transaction/sql":
            return httpx.Response(
                200, json={"result": [{"sql": f"select {i} from t"} for i in range(sqls)]}
            )
        if path == "/api/transaction/profile.txt":
            text = "\n".join(f"line {i} user=kim@example.com" for i in range(profile_lines))
            return httpx.Response(200, text=text, headers={"content-type": "text/plain"})
        if path == "/api/transaction/txid":
            return httpx.Response(200, json={"result": [{"txid": "1", "domainId": DOMAIN}]})
        return httpx.Response(404)

    return handler


def _mcp(tmp_path, handler):
    spool = tmp_path / "spool"
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(handler),
        extra={"APM_SPOOL_DIR": str(spool)},
    )
    jobs = JobManager(
        Spool(spool),
        JobConfig(spool_dir=spool),
        envelope=tools.ok,
        error_envelope=tools.err,
    )
    return create_server(tools, jobs=jobs), tools, jobs


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


async def _all_rows(mcp, out: dict) -> list[dict]:
    rows: list[dict] = []
    for chunk in out["artifact"]["chunks"]:
        read = _json(
            await mcp.call_tool(
                "apm_job_read", {"job_id": out["artifact"]["job_id"], "chunk": chunk["index"]}
            )
        )
        rows += read["rows"]
    return rows


@pytest.mark.asyncio
async def test_bulk_1200_instances_listing_is_whole(tmp_path):  # SPEC §10 ③ 인스턴스 1,200
    mcp, _, jobs = _mcp(tmp_path, _bulk_handler(instances=1200))
    out = _json(await mcp.call_tool("apm_instance_map", {}))
    assert out["total_row_count"] == 1200 and out["row_count"] == 500  # 종전 200 상한 제거
    assert sum(c["rows"] for c in out["artifact"]["chunks"]) == 1200
    rows = await _all_rows(mcp, out)
    assert [r["instance_id"] for r in rows] == [2000 + i for i in range(1200)]  # 순서 보존
    assert all("instance_oid" in r for r in rows)  # 결과 파일에만
    assert all("instance_oid" not in r for r in out["rows"])  # 화면 행에는 없다
    assert not any("200개만" in x for x in out["limits"])
    await jobs.aclose()


@pytest.mark.asyncio
async def test_bulk_5000_events_full_is_whole_and_ordered(tmp_path):  # SPEC §10 ③ 이벤트 5,000
    mcp, _, jobs = _mcp(tmp_path, _bulk_handler(instances=1, events=5000))
    out = _json(
        await mcp.call_tool(
            "apm_events", {"hostname": "was-host01", "lookback_minutes": 60, "full": True}
        )
    )
    assert out["total_row_count"] == 5000  # 종전 50건 상한 제거
    rows = await _all_rows(mcp, out)
    times = [r["time_ms"] for r in rows]
    assert len(rows) == 5000 and times == sorted(times, reverse=True)
    assert rows[:500] == [
        {**r, "instance_oid": rows[i]["instance_oid"]} for i, r in enumerate(out["rows"])
    ]
    recent = _json(
        await mcp.call_tool(
            "apm_events", {"hostname": "was-host01", "lookback_minutes": 60, "n": 7}
        )
    )
    assert recent["row_count"] == 7 and [r["time_ms"] for r in recent["rows"]] == times[:7]
    await jobs.aclose()


@pytest.mark.asyncio
async def test_bulk_xview_60_minutes_all_chunks(tmp_path):  # SPEC §10 ③ X-View 60분
    calls: list[str] = []
    mcp, _, jobs = _mcp(tmp_path, _bulk_handler(instances=1, tx_per_minute=50, calls=calls))
    out = _json(
        await mcp.call_tool(
            "apm_slow_transactions",
            {"hostname": "was-host01", "lookback_minutes": 60, "full": True},
        )
    )
    assert calls.count("/api/transaction/time") == 60  # 종전 10분 상한 제거 — 1분 조각 전부
    assert out["total_row_count"] == 3000 and out["window"]["minutes"] == 60
    rows = await _all_rows(mcp, out)
    times = [r["response_time_ms"] for r in rows]
    assert len(rows) == 3000 and times == sorted(times, reverse=True)
    top = _json(
        await mcp.call_tool(
            "apm_slow_transactions", {"hostname": "was-host01", "lookback_minutes": 5, "n": 25}
        )
    )
    assert top["row_count"] == 25  # 종전 n ≤ 20 폐지
    await jobs.aclose()


@pytest.mark.asyncio
async def test_no_instance_trend_sql_or_type_caps(tmp_path):
    # 호스트당 5 · 추세 2 · SQL top_k 20 · 오류 유형 상위 10 상한 제거(N-4 · N-3)
    calls: list[str] = []
    handler = _bulk_handler(instances=8, error_types=12, sqls=30, calls=calls)
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    health = await tools.apm_app_health("was-host01")
    assert len(health["rows"]) == 8 and not any("개만 조회" in x for x in health["limits"])
    calls.clear()
    rt = await tools.apm_runtime_health("was-host01", lookback_minutes=30)
    assert calls.count("/api/dbmetrics/instance") == 8 * 3
    assert all("trend" in r for r in rt["rows"])
    ev = await tools.apm_events("was-host01")
    assert len(ev["errors_by_type"]) == 12 and ev["errors_by_type"][0]["count"] == 12
    prof = await tools.apm_transaction_profile("was-host01", DOMAIN, "1", NOW_MS)
    assert len(prof["rows"][0]["sqls"]) == 30
    capped = await tools.apm_transaction_profile(
        "was-host01", DOMAIN, "1", NOW_MS, top_k=25, investigation_id="t"
    )
    assert len(capped["rows"][0]["sqls"]) == 25


@pytest.mark.asyncio
async def test_profile_full_text_goes_to_artifact(tmp_path):
    # SPEC §5 apm_transaction_profile — 발췌(앞 60줄)는 화면용 · 전문은 artifact.text_parts
    mcp, tools, jobs = _mcp(tmp_path, _bulk_handler(instances=1, profile_lines=100))
    direct = await tools.apm_transaction_profile("was-host01", DOMAIN, "1", NOW_MS)
    row = direct["rows"][0]
    assert row["profile_truncated"] is True and row["profile_excerpt"].count("\n") == 59
    full = direct[TEXT_PARTS_KEY]["profile"]
    assert full.count("\n") == 99 and "kim@example.com" not in full
    out = _json(
        await mcp.call_tool(
            "apm_transaction_profile",
            {"hostname": "was-host01", "domain_id": DOMAIN, "txid": "1", "time_ms": NOW_MS},
        )
    )
    assert out["artifact"]["text_parts"][0]["name"] == "profile"
    text = _json(
        await mcp.call_tool(
            "apm_job_read", {"job_id": out["artifact"]["job_id"], "part": "profile"}
        )
    )["text"]
    assert text == full and TEXT_PARTS_KEY not in out  # 예약 키는 반환 봉투에 남지 않는다
    await jobs.aclose()


@pytest.mark.asyncio
async def test_short_profile_has_no_text_part(tmp_path):
    _, tools, jobs = _mcp(tmp_path, _bulk_handler(instances=1, profile_lines=5))
    out = await tools.apm_transaction_profile("was-host01", DOMAIN, "1", NOW_MS)
    assert out["rows"][0]["profile_truncated"] is False and TEXT_PARTS_KEY not in out
    await jobs.aclose()


def test_error_type_is_allowed_on_error_search_only():  # T-COV-ERROR-allow
    from apm_gateway.adapters.jennifer.allowlist import NotAllowedError, check_request

    base = {"domain_id": "1", "start_time": "1", "end_time": "2", "error_type": "X"}
    assert check_request("GET", "/api/dbsearch/error", base).template == "/api/dbsearch/error"
    with pytest.raises(NotAllowedError):
        check_request("GET", "/api/dbsearch/event", base)


@pytest.mark.asyncio
async def test_mock_server_filters_error_type(mock_server_factory, tmp_path):
    fixtures = synthetic_fixtures()
    for fx in fixtures:
        if fx["request"]["template"] == "/api/dbsearch/error":
            fx["response"]["body_json"]["result"].append(
                {**fx["response"]["body_json"]["result"][0], "errorType": "OUTOFMEMORY"}
            )
    base, _ = mock_server_factory(write_fixtures(tmp_path / "fx", fixtures), "connected")
    tools, _ = make_tools(base)
    everything = await tools.apm_events("was-host01")
    assert {e["error_type"] for e in everything["errors_by_type"]} == {
        "SQL_EXCEPTION",
        "OUTOFMEMORY",
    }
    one = await tools.apm_events("was-host01", record="error", error_type="OUTOFMEMORY")
    assert [r["error_type"] for r in one["rows"]] == ["OUTOFMEMORY"]
