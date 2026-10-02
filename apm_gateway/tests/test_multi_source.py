"""다중 제니퍼 소스 — plans/87 J8 §0.13 (5) 수용 기준 · D-287.

목 Open API 서버 **2개**가 같은 `domain_id`(1000)·`instance_id`(1001·1002)·hostname(was-host01)을
갖는 충돌 경우를 만든다. 소스 B의 실시간 응답시간·트랜잭션 id는 일부러 다르게 두어 값이 섞이지
않았는지 본다. 끝마다 `/__mock/hits`로 소스별 호출·Bearer 지문을 대조한다(토큰 값은 기록되지
않는다).
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import urllib.request

import httpx
import pytest
from conftest import (
    NOW_MS,
    NOW_S,
    RECORDED,
    TOKEN,
    synthetic_fixtures,
    write_fixtures,
)
from mock_openapi import bearer_fingerprint, make_server

TOKEN_B = "tok-OTHER-77c1"


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


def _reset(*bases: str) -> None:
    for base in bases:
        req = urllib.request.Request(f"{base}/__mock/reset", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=5).read()


def _closed_port_url() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def _fixtures_b() -> list[dict]:
    """소스 B — 같은 도메인·인스턴스 id·hostname, 다른 응답시간·트랜잭션 id."""
    out = []
    for fx in synthetic_fixtures():
        fx = json.loads(json.dumps(fx))
        body = fx["response"].get("body_json")
        tpl = fx["request"]["template"]
        if tpl == "/api/realtime/instance":
            for rec in body["result"]:
                rec["responseTime"] = 111.0
        if tpl in ("/api/transaction/time", "/api/transaction/txid"):
            for rec in body["result"]:
                rec["txid"] = str(int(rec["txid"]) - 2000)
        out.append(fx)
    return out


def _env(url_a: str, url_b: str, **extra: str) -> dict[str, str]:
    return {
        "JENNIFER_SOURCES": '["bank", "common"]',
        "JENNIFER_BANK_API_URL": url_a,
        "JENNIFER_BANK_API_TOKEN": TOKEN,
        "JENNIFER_COMMON_API_URL": url_b,
        "JENNIFER_COMMON_API_TOKEN": TOKEN_B,
        "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        **extra,
    }


def _tools(env: dict[str, str], *, clock=lambda: NOW_S, policy_dir=None, transport=None, mono=None):
    import time

    from apm_gateway.application.sources import build_source_set
    from apm_gateway.application.tools import ApmTools

    from apm_gateway.config import POLICY_DIR, load_config

    cfg = load_config(env, policy_dir=policy_dir or POLICY_DIR)
    sources = build_source_set(cfg, transport=transport, clock=mono or time.monotonic)
    return ApmTools(sources, cfg, clock=clock), cfg


def _start_two(mock_server_factory, tmp_path, *, events: bool = True):
    def pick(fixtures: list[dict]) -> list[dict]:
        if events:
            return fixtures
        return [fx for fx in fixtures if fx["request"]["template"] != "/api/dbsearch/event"]

    base_a, _ = mock_server_factory(write_fixtures(tmp_path / "a", pick(synthetic_fixtures())))
    # 소스 B는 다른 토큰으로 띄운다(conftest 팩토리는 TOKEN 고정이라 직접 띄운다).
    server, _ = make_server(
        write_fixtures(tmp_path / "b", pick(_fixtures_b())), TOKEN_B, "connected"
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_b = f"http://127.0.0.1:{server.server_address[1]}"
    tools, _cfg = _tools(_env(base_a, base_b))
    return base_a, base_b, tools, server


@pytest.fixture
def two(mock_server_factory, tmp_path):
    base_a, base_b, tools, server = _start_two(mock_server_factory, tmp_path)
    yield base_a, base_b, tools
    server.shutdown()
    server.server_close()


@pytest.fixture
def two_without_events(mock_server_factory, tmp_path):
    base_a, base_b, tools, server = _start_two(mock_server_factory, tmp_path, events=False)
    yield base_a, base_b, tools
    server.shutdown()
    server.server_close()


def _assert_tokens_isolated(base_a: str, base_b: str) -> None:
    fp_a, fp_b = bearer_fingerprint(TOKEN), bearer_fingerprint(TOKEN_B)
    hits_a, hits_b = _hits(base_a), _hits(base_b)
    assert [h for h in hits_a if h["bearer_fp"] != fp_a] == []
    assert [h for h in hits_b if h["bearer_fp"] != fp_b] == []
    assert [h for h in hits_a + hits_b if h["status"] == 401] == []
    assert [h for h in hits_a + hits_b if not h["allowlisted"] or h["query_token"]] == []


# ── 정합 · 식별자 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_ids_in_two_sources_are_kept_apart(two):
    base_a, base_b, tools = two
    out = await tools.apm_instance_map("was-host01")
    assert [(r["source_id"], r["instance_id"]) for r in out["rows"]] == [
        ("bank", 1001),
        ("bank", 1002),
        ("common", 1001),
        ("common", 1002),
    ]
    assert out["instance_resolution"]["instance_refs"] == [
        {"source_id": s, "domain_id": 1000, "instance_id": i}
        for s in ("bank", "common")
        for i in (1001, 1002)
    ]
    assert out["sources"] == [
        {"source_id": "bank", "status": "ok", "reason": ""},
        {"source_id": "common", "status": "ok", "reason": ""},
    ]
    assert out["source_kind"] == "apm_api" and out["source"] == "jennifer"  # 인식 키 불변
    health = await tools.apm_app_health("was-host01")
    rt = {(r["source_id"], r["instance_id"]): r["response_time_avg_ms"] for r in health["rows"]}
    assert rt[("bank", 1001)] == 850.0 and rt[("common", 1001)] == 111.0  # 값이 섞이지 않는다
    assert {s["source_id"] for s in health["was_signals"]} <= {"bank", "common"}
    _assert_tokens_isolated(base_a, base_b)


@pytest.mark.asyncio
async def test_source_ids_narrow_calls_to_that_server(two):
    base_a, base_b, tools = two
    await tools.apm_instance_map("was-host01")  # 인벤토리 캐시
    _reset(base_a, base_b)
    out = await tools.apm_app_health("was-host01", source_ids=["common"], lookback_minutes=2)
    assert {r["source_id"] for r in out["rows"]} == {"common"}
    assert out["sources"] == [{"source_id": "common", "status": "ok", "reason": ""}]
    assert _hits(base_a) == [] and len(_hits(base_b)) >= 3  # 실시간 1 + 1분 창 2
    _assert_tokens_isolated(base_a, base_b)


@pytest.mark.asyncio
async def test_unknown_source_ids_is_invalid_argument_with_list(two):
    from apm_gateway.domain.errors import ApmError

    base_a, base_b, tools = two
    _reset(base_a, base_b)
    with pytest.raises(ApmError) as exc:
        await tools.apm_events("was-host01", source_ids=["zzz"])
    assert exc.value.code == "invalid_argument" and "['bank', 'common']" in exc.value.reason
    assert _hits(base_a) == [] and _hits(base_b) == []


@pytest.mark.asyncio
async def test_profile_ref_goes_only_to_its_source(two):
    from apm_gateway.domain.errors import ApmError

    base_a, base_b, tools = two
    slow = await tools.apm_slow_transactions("was-host01", lookback_minutes=5, n=20)
    refs = [r["profile_ref"] for r in slow["rows"] if r["source_id"] == "common"]
    assert refs and refs[0]["source_id"] == "common" and refs[0]["txid"] == "7000"
    _reset(base_a, base_b)
    out = await tools.apm_transaction_profile("was-host01", **refs[0], investigation_id="inv-j8")
    assert out["rows"][0]["source_id"] == "common" and out["rows"][0]["txid"] == "7000"
    assert _hits(base_a) == []  # 다른 소스 호출 0
    assert {h["template"] for h in _hits(base_b)} >= {
        "/api/transaction/txid",
        "/api/transaction/profile.txt",
        "/api/transaction/sql",
    }
    ref = {k: v for k, v in refs[0].items() if k != "source_id"}
    with pytest.raises(ApmError) as exc:  # 소스 2개 이상이면 source_id 필수
        await tools.apm_transaction_profile("was-host01", **ref)
    assert exc.value.code == "invalid_argument" and "source_id" in exc.value.reason
    with pytest.raises(ApmError) as exc:
        await tools.apm_transaction_profile("was-host01", 2000, "7000", NOW_MS, source_id="bank")
    assert exc.value.code == "profile_ref_mismatch"
    _assert_tokens_isolated(base_a, base_b)


@pytest.mark.asyncio
async def test_rejected_input_is_zero_http_per_source(two):
    from apm_gateway.adapters.jennifer.allowlist import NotAllowedError

    base_a, base_b, tools = two
    _reset(base_a, base_b)
    for src in tools.sources:
        for path, query in (
            ("/api/auth/userlist", {}),
            ("/api/domain", {"token": "x"}),
            ("/api/instance", {}),
        ):
            with pytest.raises(NotAllowedError):
                await src.api.client.get_json(path, query)
    assert _hits(base_a) == [] and _hits(base_b) == []


# ── 부분 실패 · 전부 실패 · 짧은 캐시(F-3) ──────────────────


@pytest.mark.asyncio
async def test_one_source_down_returns_rest_with_limit(mock_server_factory, synthetic_dir):
    base_a, _ = mock_server_factory(synthetic_dir, "connected")
    tools, _ = _tools(_env(base_a, _closed_port_url()))
    out = await tools.apm_app_health("was-host01")
    assert {r["source_id"] for r in out["rows"]} == {"bank"}
    assert any("APM 소스 common 조회 불가(source_unavailable)" in x for x in out["limits"])
    status = {s["source_id"]: s for s in out["sources"]}
    assert status["bank"]["status"] == "ok" and status["common"]["status"] == "unavailable"
    assert status["common"]["reason"].startswith("source_unavailable")
    health = await tools.gateway_health()
    assert health["status"] == "degraded"
    assert [(r["source_id"], r["status"]) for r in health["rows"]] == [
        ("bank", "ok"),
        ("common", "degraded"),
    ]


@pytest.mark.asyncio
async def test_all_sources_down_or_empty_is_source_unavailable(mock_server_factory):
    from apm_gateway.domain.errors import ApmError

    tools, _ = _tools(_env(_closed_port_url(), _closed_port_url()))
    with pytest.raises(ApmError) as exc:
        await tools.apm_instance_map("was-host01")
    assert exc.value.code == "source_unavailable" and "모든 APM 소스 조회 불가" in exc.value.reason
    base_a, _ = mock_server_factory(RECORDED, "fixtures")  # 라이선스 없음 — 도메인 0건
    base_b, _ = mock_server_factory(RECORDED, "fixtures")
    tools, _ = _tools({**_env(base_a, base_b), "JENNIFER_COMMON_API_TOKEN": TOKEN})
    with pytest.raises(ApmError) as exc:
        await tools.apm_events("sample-was-01")
    assert exc.value.code == "source_unavailable" and "도메인 0건" in exc.value.reason


@pytest.mark.asyncio
async def test_failed_or_empty_source_is_requeried_after_30s():
    """F-3 — 빈 인벤토리·실패 인벤토리는 30초만 캐시한다(정상 인벤토리는 600초)."""
    from apm_gateway.domain.errors import ApmError

    state = {"bank": "empty", "common": "down"}

    def handler(request: httpx.Request) -> httpx.Response:
        src = "bank" if request.url.host == "bank.test" else "common"
        path = request.url.path
        if state[src] == "down":
            return httpx.Response(500, json={"exception": {"message": "boom"}})
        if path == "/api/domain":
            body = [] if state[src] == "empty" else [{"domainId": 1000, "name": "d"}]
            return httpx.Response(200, json={"result": body})
        if path == "/api/instance":
            return httpx.Response(
                200, json={"result": [{"instanceId": 1, "name": "i", "hostName": "h1"}]}
            )
        return httpx.Response(404)

    mono = [100.0]
    tools, _ = _tools(
        _env("http://bank.test", "http://common.test"),
        transport=httpx.MockTransport(handler),
        mono=lambda: mono[0],
    )
    with pytest.raises(ApmError):
        await tools.apm_instance_map("h1")
    calls = tools.sources.calls_total
    state.update(bank="ok", common="ok")
    mono[0] += 10  # 30초 안 — 다시 부르지 않는다
    with pytest.raises(ApmError):
        await tools.apm_instance_map("h1")
    assert tools.sources.calls_total == calls
    mono[0] += 21  # 30초 경과 — 다시 조회해 회복
    out = await tools.apm_instance_map("h1")
    assert {r["source_id"] for r in out["rows"]} == {"bank", "common"}
    calls = tools.sources.calls_total
    mono[0] += 60  # 정상 인벤토리는 600초 캐시
    await tools.apm_instance_map("h1")
    assert tools.sources.calls_total == calls


# ── 정합 규칙(M-8) · 감사(M-9) · 도구 표면 ──────────────────


@pytest.mark.asyncio
async def test_override_source_id_and_per_source_rules(two, tmp_path):
    base_a, base_b, _ = two
    policy = tmp_path / "policy"
    policy.mkdir()
    (policy / "instance_map.yaml").write_text(
        "version: 1\n"
        "match_rules: [{kind: host_name}, {kind: exact}, {kind: prefix}]\n"
        "per_source:\n"
        "  common: {match_rules: [{kind: host_name}, {kind: exact}]}\n"
        "overrides:\n"
        "  - {source_id: bank, instance_name: api02_main, domain_id: 1000, hostname: api02}\n",
        encoding="utf-8",
    )
    tools, _ = _tools(_env(base_a, base_b), policy_dir=policy)
    out = await tools.apm_instance_map("api02")
    assert [(r["source_id"], r["match_reason"]) for r in out["rows"]] == [("bank", "override")]
    assert {s["source_id"]: s["status"] for s in out["sources"]} == {
        "bank": "ok",
        "common": "no_match",  # 전역 prefix 규칙이면 medium 거짓 정합이 났을 자리
    }
    assert out["instance_resolution"]["confidence"] == "high"


@pytest.mark.asyncio
async def test_audit_line_carries_called_sources(two, caplog):
    from apm_gateway.interface.server import create_server

    base_a, base_b, tools = two
    mcp = create_server(tools)
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    await mcp.call_tool("apm_instance_map", {"hostname": "was-host01", "source_ids": ["bank"]})
    line = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"][-1]
    assert "sources=bank:" in line and "common" not in line
    await mcp.call_tool("apm_resource_pool", {"hostname": "was-host01"})
    line = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"][-1]
    assert "sources=bank:" in line and ",common:" in line
    assert TOKEN not in caplog.text and TOKEN_B not in caplog.text


@pytest.mark.asyncio
async def test_tool_schemas_add_source_arguments_only(two):
    from apm_gateway.interface.server import create_server

    _, _, tools = two
    schemas = {t.name: t.inputSchema["properties"] for t in await create_server(tools).list_tools()}
    for name, props in schemas.items():
        if name == "apm_transaction_profile":
            assert "source_id" in props and "source_ids" not in props
        elif name.startswith("apm_"):
            assert "source_ids" in props, name
    assert schemas["gateway_health"] == {}


# ── 폴러 — 커서·멱등 키·dbId ─────────────────────────────────


@pytest.mark.asyncio
async def test_poller_keeps_same_value_events_of_two_sources(two_without_events):
    from apm_gateway.application.poller import EventPoller
    from test_poller import FakeRedis, _event, _inject

    base_a, base_b, tools = two_without_events
    for base in (base_a, base_b):
        _inject(base, [_event()])  # 두 서버에 값이 똑같은 이벤트
    cfg = tools.cfg
    cfg.poller.enabled = True
    redis = FakeRedis()
    poller = EventPoller(tools.sources, cfg, redis, clock=lambda: NOW_S)
    assert await poller.poll_once() == 2
    payloads = sorted((json.loads(r["data"]) for r in redis.stream), key=lambda p: p["dbId"])
    assert [p["dbId"] for p in payloads] == ["jennifer_bank", "jennifer_common"]
    assert [p["source"] for p in payloads] == ["jennifer", "jennifer"]
    assert [p["apm"]["source_id"] for p in payloads] == ["bank", "common"]
    assert payloads[0]["resourceAncestry"] == "JENNIFER > bank > demo-domain > was01_a"
    assert payloads[0]["alarmId"] != payloads[1]["alarmId"]
    assert payloads[0]["apm"]["was_signals"][0]["source_id"] == "bank"
    assert {k for k in redis.kv if ":cursor:" in k} == {
        "apm_gateway:poller:cursor:bank:1000",
        "apm_gateway:poller:cursor:common:1000",
    }
    assert set(poller.status()["domains"]) == {"bank:1000", "common:1000"}
    assert await poller.poll_once() == 0  # 경계 재조회 — 멱등 키로 차단
    restarted = EventPoller(tools.sources, cfg, redis, clock=lambda: NOW_S + 5)
    assert await restarted.poll_once() == 0
    assert len(redis.stream) == 2
    _assert_tokens_isolated(base_a, base_b)


@pytest.mark.asyncio
async def test_poller_one_source_down_does_not_stop_other(mock_server_factory, tmp_path):
    from apm_gateway.application.poller import EventPoller
    from test_poller import FakeRedis, _event, _inject, _no_event_fixtures

    base_a, _ = mock_server_factory(
        write_fixtures(tmp_path / "a", _no_event_fixtures()), "connected"
    )
    _inject(base_a, [_event()])
    tools, cfg = _tools(_env(base_a, _closed_port_url()))
    redis = FakeRedis()
    poller = EventPoller(tools.sources, cfg, redis, clock=lambda: NOW_S)
    assert await poller.poll_once() == 1
    assert json.loads(redis.stream[0]["data"])["dbId"] == "jennifer_bank"


@pytest.mark.asyncio
async def test_single_setting_keeps_v4_identifiers(mock_server_factory, tmp_path):
    from apm_gateway.application.poller import EventPoller
    from apm_gateway.application.sources import build_source_set
    from conftest import make_cfg
    from test_poller import FakeRedis, _event, _inject, _no_event_fixtures

    base, _ = mock_server_factory(write_fixtures(tmp_path / "s", _no_event_fixtures()), "connected")
    _inject(base, [_event()])
    cfg = make_cfg(base, extra={"APM_EVENT_POLLER_ENABLED": "true"})
    assert [s.source_id for s in cfg.sources] == ["default"]
    redis = FakeRedis()
    assert (
        await EventPoller(build_source_set(cfg), cfg, redis, clock=lambda: NOW_S).poll_once() == 1
    )
    p = json.loads(redis.stream[0]["data"])
    assert p["dbId"] == "jennifer" and p["resourceAncestry"] == "JENNIFER > demo-domain > was01_a"
    assert p["apm"]["source_id"] == "default"
