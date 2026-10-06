"""`apm_*` 도구 계약 — 목 Open API 서버 상대 (plans/87 §6 J1·J2 수용 기준 · [v3.3] `/__mock/hits`
단언).

- 합성 픽스처(`spec-synthetic` · conftest 참고)로 도구 경로 전체를 돈다 — 반환
  계약·정합·축약·마스킹·판정·오류.
- 녹화본(`local-docker` — 라이선스 없음)으로 "도메인 0건 → source_unavailable"을 확인한다(빈 결과를
  정상으로 보지 않는다).
- 끝마다 목 서버 접근 기록에서 **허용목록 밖 호출 0회 · 쿼리 token 0회**를 단언하고, 토큰이
  반환·로그에 0회임을 본다.
"""

from __future__ import annotations

import json
import logging
import urllib.request

import pytest
from conftest import NOW_MS, RECORDED, TOKEN, make_tools

REQUIRED_OK_KEYS = {"rows", "row_count", "queried_at", "source_kind", "source", "tool", "limits"}


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


def _assert_clean(base: str, *payloads: dict) -> None:
    hits = _hits(base)
    assert hits, "목 서버 호출이 없었다"
    assert [h for h in hits if not h["allowlisted"]] == []
    assert [h for h in hits if h["query_token"]] == []
    for p in payloads:
        assert TOKEN not in json.dumps(p, ensure_ascii=False)


@pytest.fixture
def synth(mock_server_factory, synthetic_dir):
    base, state = mock_server_factory(synthetic_dir, "connected")
    tools, _cfg = make_tools(base)
    yield base, tools, state


def _ok(payload: dict, tool: str) -> dict:
    assert "error" not in payload, payload
    assert REQUIRED_OK_KEYS <= set(payload)
    assert (
        payload["source_kind"] == "apm_api"
        and payload["source"] == "jennifer"
        and payload["tool"] == tool
    )
    assert payload["row_count"] == len(payload["rows"])
    return payload


def _kinds(payload: dict) -> set[str]:
    return {s["kind"] for s in payload.get("was_signals", [])}


# ── 정합 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_instance_map_by_hostname_normalizes_fqdn_and_case(synth):
    base, tools, _ = synth
    out = _ok(await tools.apm_instance_map("was-host01"), "apm_instance_map")
    assert [r["instance_id"] for r in out["rows"]] == [1001, 1002]
    assert {r["match_confidence"] for r in out["rows"]} == {"high"}
    assert out["instance_resolution"] == {
        "matched": True,
        "confidence": "high",
        "reason": "host_name",
        "instances": [1001, 1002],
        "instance_refs": [
            {"source_id": "default", "domain_id": 1000, "instance_id": 1001},
            {"source_id": "default", "domain_id": 1000, "instance_id": 1002},
        ],
    }
    assert out["sources"] == [{"source_id": "default", "status": "ok", "reason": ""}]
    assert "port" not in out["rows"][0] or out["rows"][0]["port"] is None
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_instance_map_listing_reverse_resolves(synth):
    base, tools, _ = synth
    out = _ok(await tools.apm_instance_map(), "apm_instance_map")
    by_id = {r["instance_id"]: r for r in out["rows"]}
    assert by_id[1001]["hostname"] == "was-host01.example.local"
    assert by_id[1003]["hostname"] == "" and by_id[1003]["match_confidence"] == "none"


@pytest.mark.asyncio
async def test_prefix_rule_is_medium_with_limit(synth):
    _, tools, _ = synth
    out = _ok(await tools.apm_instance_map("api02"), "apm_instance_map")
    assert out["instance_resolution"]["confidence"] == "medium"
    assert any("정합 신뢰도 medium" in x for x in out["limits"])


@pytest.mark.asyncio
async def test_unresolved_hostname_is_error_not_empty(synth):
    from apm_gateway.domain.errors import ApmError

    base, tools, _ = synth
    with pytest.raises(ApmError) as exc:
        await tools.apm_app_health("nohost")
    assert exc.value.code == "instance_unresolved"
    _assert_clean(base)


# ── 도구 8종 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_app_health_current_and_window(synth):
    base, tools, _ = synth
    cur = _ok(await tools.apm_app_health("was-host01"), "apm_app_health")
    row = next(r for r in cur["rows"] if r["instance_id"] == 1001)
    assert row["response_time_avg_ms"] == 850.0 and row["tps"] == 42.5 and row["reject_rate"] == 2.0
    assert "window" not in cur and "was_service_queuing" in _kinds(cur)
    win = _ok(await tools.apm_app_health("was-host01", lookback_minutes=5), "apm_app_health")
    w = next(r for r in win["rows"] if r["instance_id"] == 1001)["window"]
    assert w["calls"] == 6 and w["errors"] == 1 and w["response_time_p95_ms"] == 4000
    assert win["window"]["minutes"] == 5
    _assert_clean(base, cur, win)


@pytest.mark.asyncio
async def test_app_health_past_reference_and_long_window(synth):
    base, tools, _ = synth
    out = _ok(
        await tools.apm_app_health(
            "was-host01", reference_time="2026-09-29T09:50:00", lookback_minutes=30
        ),
        "apm_app_health",
    )
    assert any("과거 기준시각" in x for x in out["limits"])
    # plans/134 W1 N-4 — X-View 10분 상한 제거: 창 30분 전체를 1분 조각 30개로 부른다
    assert not any("> 10분" in x for x in out["limits"])
    assert out["window"]["minutes"] == 30
    xview = [h for h in _hits(base) if h["template"] == "/api/transaction/time"]
    assert len(xview) == 30
    assert (
        out["hourly"]["calls"] == 300
        and "user=<v>" in out["hourly"]["top_applications"][0]["application"]
    )
    assert out["hourly"]["application_count"] == 1
    assert any("top_applications는 평균 응답시간 상위" in x for x in out["limits"])
    assert "response_time_avg_ms" not in out["rows"][0]
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_xview_split_into_one_minute_calls(synth):
    base, tools, state = synth
    await tools.apm_app_health("was-host01", lookback_minutes=3)
    tx_calls = [h for h in _hits(base) if h["template"] == "/api/transaction/time"]
    assert len(tx_calls) == 3  # 도메인 1 × 1분 창 3개


@pytest.mark.asyncio
async def test_runtime_health_signals_and_trend(synth):
    base, tools, _ = synth
    out = _ok(
        await tools.apm_runtime_health("was-host01", lookback_minutes=30), "apm_runtime_health"
    )
    row = next(r for r in out["rows"] if r["instance_id"] == 1001)
    assert row["heap_usage_ratio"] == pytest.approx(0.93)
    assert set(row["trend"]) == {"heap_used_mb", "heap_committed_mb", "gc_time_usage_pct"}
    assert {"was_heap_pressure", "was_gc_stall"} <= _kinds(out)
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_resource_pool(synth):
    base, tools, _ = synth
    out = _ok(await tools.apm_resource_pool("was-host01"), "apm_resource_pool")
    row = next(r for r in out["rows"] if r["instance_id"] == 1001)
    assert row["db_pool_usage_ratio"] == pytest.approx(0.95)
    assert row["active_by_running_mode"] == {"SQL": 4}
    assert {"was_db_pool_exhaustion", "was_thread_pool_exhaustion"} <= _kinds(out)
    assert any("스레드 풀 상한" in x for x in out["limits"])
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_slow_transactions_top_n_masking_profile_ref(synth):
    base, tools, _ = synth
    out = _ok(
        await tools.apm_slow_transactions("was-host01", lookback_minutes=5, n=3),
        "apm_slow_transactions",
    )
    assert [r["response_time_ms"] for r in out["rows"]] == [4000, 3900, 3800]
    assert all("kim" not in r["application"] for r in out["rows"])
    assert out["rows"][0]["profile_ref"] == {
        "source_id": "default",
        "domain_id": 1000,
        "txid": "9000",
        "time_ms": NOW_MS - 30_000,
    }
    assert "was_slow_sql" in _kinds(out) and out["summary"]["sql_fetch_share"] > 0.6
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_slow_transactions_rejects_bad_n(synth):
    """plans/134 W1(SPEC §2.1) — `n`은 1 이상이면 상한 없음(종전 ≤20 폐지) · 0 이하는 거부."""
    from apm_gateway.domain.errors import ApmError

    _, tools, _ = synth
    with pytest.raises(ApmError) as exc:
        await tools.apm_slow_transactions("was-host01", n=0)
    assert exc.value.code == "invalid_argument"
    out = await tools.apm_slow_transactions("was-host01", lookback_minutes=5, n=50)
    assert out["row_count"] == 6  # 원천 6건 전부(50 상한 아님)


@pytest.mark.asyncio
async def test_active_services_masking(synth):
    base, tools, _ = synth
    out = _ok(await tools.apm_active_services("was-host01"), "apm_active_services")
    row = out["rows"][0]
    assert "kim@example.com" not in row["running_text"] and "42" not in row["running_text"]
    assert row["client_ip"] == "192.168.*.*"
    assert "card=<v>" in row["application"]
    assert out["summary"]["total"] == 4
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_events_signals_masking_and_level(synth):
    base, tools, _ = synth
    out = _ok(await tools.apm_events("was-host01"), "apm_events")
    ev = out["rows"][0]
    assert (
        ev["event_type"] == "ERROR_OUTOFMEMORY"
        and ev["level"] == "fatal"
        and ev["event_kind"] == "error"
    )
    assert "kim@example.com" not in ev["message"]
    assert ev["profile_ref"]["txid"] == "9000"
    sig = out["was_signals"][0]
    assert (sig["kind"], sig["level"]) == ("was_heap_pressure", "CRITICAL")
    assert out["errors_by_type"] == [{"error_type": "SQL_EXCEPTION", "count": 1}]
    assert out["window"]["minutes"] == 30
    fatal = _ok(await tools.apm_events("was-host01", level="fatal"), "apm_events")
    assert fatal["row_count"] == 1
    _assert_clean(base, out, fatal)


@pytest.mark.asyncio
async def test_transaction_profile_contract(synth):
    from apm_gateway.domain.errors import ApmError

    base, tools, _ = synth
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_profile("was-host01")
    assert exc.value.code == "invalid_argument"
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_profile("was-host01", 2000, "9000", NOW_MS)
    assert exc.value.code == "profile_ref_mismatch"
    out = _ok(
        await tools.apm_transaction_profile(
            "was-host01", 1000, "9000", NOW_MS, investigation_id="inv-1"
        ),
        "apm_transaction_profile",
    )
    row = out["rows"][0]
    assert "kim" not in row["profile_excerpt"] and "= 42" not in row["profile_excerpt"]
    assert row["sqls"] == ["select * from orders where id = ?"]
    assert row["transaction"]["txid"] == "9000"
    _assert_clean(base, out)


@pytest.mark.asyncio
async def test_profile_budget_per_investigation(synth):
    from apm_gateway.domain.errors import ApmError

    _, tools, _ = synth
    for _ in range(5):
        await tools.apm_transaction_profile(
            "was-host01", 1000, "9000", NOW_MS, investigation_id="inv-b"
        )
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_profile(
            "was-host01", 1000, "9000", NOW_MS, investigation_id="inv-b"
        )
    assert exc.value.code == "rate_limited"
    # 다른 조사는 영향 없음
    await tools.apm_transaction_profile(
        "was-host01", 1000, "9000", NOW_MS, investigation_id="inv-c"
    )


@pytest.mark.asyncio
async def test_gateway_health_ok(synth):
    base, tools, _ = synth
    out = _ok(await tools.gateway_health(), "gateway_health")
    body = out["rows"][0]
    assert body["status"] == "ok" and body["jennifer_reachable"] and body["domain_count"] == 1
    # 16 + plans/134 W5 `/api/transaction/guid` 1(W7 경로는 별도 — 병합 시 합산)
    assert body["allowlist_size"] == 41  # 16 + W5 guid 1 + W7 19(plans/134) + 업무 1(130)
    # + W3·W4 서비스·업무 4(plans/134)
    assert out["poller"] == {"enabled": False}


# ── 라이선스 없는 녹화본 · 미접속 · 미설정 ────────────────────


@pytest.mark.asyncio
async def test_recorded_license_less_domain_zero_is_source_unavailable(mock_server_factory):
    from apm_gateway.domain.errors import ApmError

    base, _ = mock_server_factory(RECORDED, "fixtures")
    tools, _ = make_tools(base)
    with pytest.raises(ApmError) as exc:
        await tools.apm_app_health("sample-was-01")
    assert exc.value.code == "source_unavailable" and "도메인 0건" in exc.value.reason
    health = await tools.gateway_health()
    assert health["rows"][0]["status"] == "degraded" and health["rows"][0]["domain_count"] == 0
    _assert_clean(base, health)


@pytest.mark.asyncio
async def test_disconnected_domain_is_source_unavailable(mock_server_factory, synthetic_dir):
    from apm_gateway.domain.errors import ApmError

    base, _ = mock_server_factory(synthetic_dir, "disconnected")
    tools, _ = make_tools(base)
    with pytest.raises(ApmError) as exc:
        await tools.apm_instance_map("was-host01")
    assert exc.value.code == "source_unavailable" and "Domain is not connected" in exc.value.reason
    _assert_clean(base)


@pytest.mark.asyncio
async def test_not_configured():
    from apm_gateway.domain.errors import ApmError

    tools, _ = make_tools("")
    with pytest.raises(ApmError) as exc:
        await tools.apm_app_health("was-host01")
    assert exc.value.code == "not_configured"


@pytest.mark.asyncio
async def test_token_never_in_logs(synth, caplog):
    caplog.set_level(logging.DEBUG)
    base, tools, _ = synth
    await tools.apm_app_health("was-host01", lookback_minutes=2)
    await tools.apm_events("was-host01")
    assert TOKEN not in caplog.text
