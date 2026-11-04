"""plans/211 W6 축 D — 알람 3''순(게이트웨이 노드 주소 직접 조회 · §9.3 D · §9.4 7·9).

게이트웨이는 띄우지 않는다 — `resolve_map_link(gateway_client=…)`에 가짜 클라이언트를 넣는다.
폴스타 프로브·Redis 대역·이벤트 헬퍼는 plans/210 W3 테스트의 것을 그대로 쓴다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application import apm_host_map_lookup as lk
from noise_gate.application import host_candidates as hc
from noise_gate.domain import apm_host_map as hm
from noise_gate.infrastructure import apm_gateway_client as gw
from noise_gate.infrastructure import apm_host_map_cache as cm
from noise_gate.tests.test_plan210_w3_fast_path import (
    NODE_IP,
    POD,
    SOURCES,
    WORKLOAD,
    _apm,
    _cache_with,
    _cfg,
    _event,
    _FakeProbe,
    _lookup_lines,
    _Redis,
    _row,
)

INSTANCE_ID = 4242
HINTS = {"source_id": "jen", "domain_id": 7}


class _FakeGateway:
    """`apm_node_addresses` 대역 — 호출 인자를 기록하고 정한 반환·예외·지연을 낸다."""

    #: 호출 상한 없음 — 3''순 `wait_seconds`는 남은 예산만으로 정한다
    call_timeout: float | None = None

    def __init__(self, result: Any = None, *, delay: float = 0.0) -> None:
        self.result = result
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        #: 그 밖 도구(작업 상태 조회 등) 접근 기록 — 3''순은 0이어야 한다
        self.other: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        self.other.append(name)

        async def _noop(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return {}

        return _noop

    async def apm_node_addresses(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _node_result(status: str = "ok", address: str = NODE_IP, **row: Any) -> dict[str, Any]:
    base = {"source_id": "jen", "domain_id": 7, "instance_id": INSTANCE_ID,
            "instance_name": POD, "host_address": address, "pod": True, "status": status}
    base.update(row)
    return {"rows": [base], "row_count": 1, "node_address_sources": []}


def _pod_event(**apm_over: Any) -> SimpleNamespace:
    """노드 주소 `not_found`(폴러 조회 실패)인 파드 알람 — `instance_id` 있음."""
    over: dict[str, Any] = {"host_address": None, "host_address_status": "not_found",
                            "instance_id": INSTANCE_ID}
    over.update(apm_over)
    return _event(_apm(**over))


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (fake, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """기본 클라이언트 생성 횟수 기록 — 3''순 대상이 아니면 0이어야 한다."""
    calls: list[Any] = []

    def _build(cfg: Any) -> None:
        calls.append(cfg)
        return None

    monkeypatch.setattr(gw, "build_apm_gateway_client", _build)
    return calls


async def _resolve(cfg: Any, event: Any, **kw: Any) -> dict[str, Any]:
    return await lk.resolve_map_link(cfg, event, HINTS, **kw)


async def _index_cache(redis: _Redis) -> cm.RedisApmHostMapCache:
    cache = cm.RedisApmHostMapCache(redis)
    await cache.rebuild_sms_index(
        {NODE_IP: [hm.make_target(db_id="polestar_b0", server_name="NODE-B", resource_id="9")]},
        {},
    )
    return cache


# ── 수용 7: not_found 파드 → 도구 1회 → 3순·3'순 ───────────────────────
async def test_not_found_pod_links_via_index_after_gateway(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    redis = _Redis()
    cache = await _index_cache(redis)
    fake = _FakeGateway(_node_result())
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), cache=cache, redis=redis, gateway_client=fake)
    [call] = fake.calls
    assert call.pop("wait_seconds") <= 5.0 - lk.NODE_LOOKUP_CONNECT_MARGIN
    assert call == {"instance_ids": [INSTANCE_ID], "source_ids": ["jen"], "domain_ids": [7]}
    assert link["status"] == "linked" and link["basis"] == "node_address"
    assert link["server_name"] == "NODE-B" and link["host_address"] == NODE_IP
    assert "node_address_status" not in link  # 지금 확인한 주소 — 페이로드 사유를 싣지 않는다
    assert probe.calls == []
    [learn] = redis.requests()
    assert learn["type"] == "learn" and learn["basis"] == hm.BASIS_NODE_ADDRESS
    assert learn["evidence"] == {"step": "3", "host_address": NODE_IP,
                                 "node_address_from": lk.NODE_LOOKUP_STEP}
    assert "order=3 scope=- result=index" in _lookup_lines(caplog)[0]


async def test_not_found_pod_links_via_live_probe_after_gateway(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    redis = _Redis()
    fake = _FakeGateway(_node_result())
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), cache=cm.RedisApmHostMapCache(redis),
                          redis=redis, gateway_client=fake)
    assert len(fake.calls) == 1
    assert link["status"] == "linked" and link["server_name"] == "NODE-01"
    assert len(probe.calls) == len(SOURCES)  # 색인 miss → 3' 직접 조회(가짜 프로브)
    assert all(NODE_IP in ips for _, _, _, ips in probe.calls)
    [learn] = redis.requests()
    assert learn["evidence"]["step"] == "3'"
    assert learn["evidence"]["node_address_from"] == lk.NODE_LOOKUP_STEP
    assert "order=3' scope=- result=live" in _lookup_lines(caplog)[0]


async def test_missing_payload_address_also_asks_gateway(probe: _FakeProbe) -> None:
    """페이로드에 노드 주소 키가 아예 없어도(폴러 조회기 없음 = `missing`) 묻는다."""
    redis = _Redis()
    cache = await _index_cache(redis)
    fake = _FakeGateway(_node_result())
    event = _event(_apm(host_address=None, host_address_status=None, instance_id=str(INSTANCE_ID)))
    link = await _resolve(_cfg(), event, cache=cache, redis=redis, gateway_client=fake)
    assert fake.calls[0]["instance_ids"] == [INSTANCE_ID]  # 문자열 id도 정수로 읽는다
    assert link["status"] == "linked" and "node_address_status" not in link


async def test_workload_key_counts_as_pod_without_pod_flag(probe: _FakeProbe) -> None:
    redis = _Redis()
    fake = _FakeGateway(_node_result())
    await _resolve(_cfg(), _pod_event(pod=None), cache=await _index_cache(redis), redis=redis,
                   gateway_client=fake)
    assert hm.workload_match_key(POD) == WORKLOAD and len(fake.calls) == 1


async def test_budget_exceeded_goes_to_next_order(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    """예산 초과 → 현행 예산 로그 후 4순(auto workload) hit · 노드 주소 단계 없음."""
    redis = _Redis()
    cache = await _cache_with(redis, _row("workload", WORKLOAD, rid=5, servers=("ADMIN-01",)))
    fake = _FakeGateway(_node_result(), delay=1.0)
    caplog.set_level(logging.INFO, logger=lk.__name__)
    t0 = time.perf_counter()
    link = await _resolve(_cfg(budget=0.05), _pod_event(), cache=cache, redis=redis,
                          gateway_client=fake)
    assert time.perf_counter() - t0 < 0.5
    assert link["map_scope"] == "workload" and link["server_name"] == "ADMIN-01"
    assert probe.calls == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("예산 초과" in m and "step=3''" in m for m in messages)
    assert any("3'') 불성립" in m and "reason=budget" in m for m in messages)
    assert "node_address_status" not in link  # 시간 초과는 node_unchecked가 아니다(3'과 같음)
    assert "order=4 scope=workload result=hit" in _lookup_lines(caplog)[0]


async def test_shared_budget_upper_bound_not_raised(probe: _FakeProbe) -> None:
    """3''이 쓴 시간만큼 3' 예산이 준다 — 합계 상한은 늘지 않는다(§9.6 Q1)."""
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    probe.delay = 1.0
    redis = _Redis()
    fake = _FakeGateway(_node_result(), delay=0.1)
    t0 = time.perf_counter()
    link = await _resolve(_cfg(budget=0.2), _pod_event(), cache=cm.RedisApmHostMapCache(redis),
                          redis=redis, gateway_client=fake)
    elapsed = time.perf_counter() - t0
    assert len(fake.calls) == 1 and probe.calls  # 3'은 남은 예산으로 시도했다
    assert link["status"] == "unresolved"
    assert elapsed < 0.6  # 3'' 0.1 + 3' 남은 0.1 (3' 단독 예산 0.2를 다시 받지 않는다)


async def test_unknown_tool_logs_disabled_and_continues(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FakeGateway(gw.ApmGatewayClientError(
        "게이트웨이 도구 오류(apm_node_addresses): Unknown tool: apm_node_addresses"
    ))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), redis=_Redis(), gateway_client=fake)
    assert len(fake.calls) == 1
    assert link["status"] == "unresolved" and link["reason"] == hm.REASON_PENDING
    assert "node_address_status" not in link  # 미등록은 예외가 아니다(새 힌트 어휘 없음)
    assert any(f"reason={lk.REASON_NODE_ADDRESS_DISABLED}" in r.getMessage()
               for r in caplog.records)


async def test_exception_is_absorbed_as_node_unchecked(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FakeGateway(RuntimeError("boom"))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), redis=_Redis(), gateway_client=fake)
    assert link["status"] == "unresolved" and link["node_address_status"] == "node_unchecked"
    assert any("직접 조회 실패" in r.getMessage() and "step=3''" in r.getMessage()
               for r in caplog.records)


async def test_gateway_error_does_not_override_missing_reason(probe: _FakeProbe) -> None:
    """페이로드 사유 `missing`은 예외여도 그대로(3'과 같은 규칙 — 비어 있을 때만 node_unchecked)."""
    fake = _FakeGateway(gw.ApmGatewayClientError("게이트웨이 호출 타임아웃"))
    event = _event(_apm(host_address=None, host_address_status=None, instance_id=INSTANCE_ID))
    link = await _resolve(_cfg(), event, redis=_Redis(), gateway_client=fake)
    assert link["node_address_status"] == "missing"


@pytest.mark.parametrize(
    ("result", "reason"),
    [
        (_node_result("budget_exceeded", ""), "status:budget_exceeded"),
        (_node_result("not_found", ""), "status:not_found"),
        (_node_result("stale"), "status:stale"),  # D-355 ⑦ — stale 자동 연결 금지
        (_node_result("ok", "not-an-ip"), "invalid_address"),
        (_node_result(instance_id=1), "no_row"),
        ({"rows": [], "job": {"job_id": "j1", "status": "running"}}, "job_handle"),
        ({"error": {"code": "not_configured", "message": "x"}}, "gateway_error:not_configured"),
    ],
)
async def test_unusable_gateway_answer_goes_next(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture, result: dict[str, Any], reason: str
) -> None:
    redis = _Redis()
    fake = _FakeGateway(result)
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), cache=await _index_cache(redis), redis=redis,
                          gateway_client=fake)
    assert len(fake.calls) == 1
    assert link["status"] == "unresolved" and probe.calls == []
    assert any(f"reason={reason}" in r.getMessage() for r in caplog.records)


# ── wait_seconds — 게이트웨이 작업 승격으로 진행 중 웹 조회를 끊지 않는다 ──
async def test_wait_seconds_is_remaining_budget_minus_margin(probe: _FakeProbe) -> None:
    redis = _Redis()
    fake = _FakeGateway(_node_result())
    await _resolve(_cfg(budget=3.0), _pod_event(), cache=await _index_cache(redis), redis=redis,
                   gateway_client=fake)
    wait = fake.calls[0]["wait_seconds"]
    assert 0.0 < wait <= 3.0 - lk.NODE_LOOKUP_CONNECT_MARGIN
    assert wait > 3.0 - lk.NODE_LOOKUP_CONNECT_MARGIN - 0.5  # 호출 직전 남은 예산 기준


async def test_wait_seconds_floor_is_zero(probe: _FakeProbe) -> None:
    redis = _Redis()
    fake = _FakeGateway(_node_result())
    link = await _resolve(_cfg(budget=lk.NODE_LOOKUP_CONNECT_MARGIN / 2), _pod_event(),
                          cache=await _index_cache(redis), redis=redis, gateway_client=fake)
    assert fake.calls[0]["wait_seconds"] == 0.0
    assert link["status"] == "linked"  # 인라인으로 답하면 그대로 쓴다


async def test_job_handle_goes_next_without_reading_job(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    """승격된 작업은 읽지 않는다(`apm_job_status` 0) — 4순으로 진행."""
    redis = _Redis()
    cache = await _cache_with(redis, _row("workload", WORKLOAD, rid=5, servers=("ADMIN-01",)))
    fake = _FakeGateway({"job": {"job_id": "j1", "status": "running"}, "rows": []})
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), cache=cache, redis=redis, gateway_client=fake)
    assert len(fake.calls) == 1 and fake.other == []
    assert link["map_scope"] == "workload" and probe.calls == []
    assert any("reason=job_handle" in r.getMessage() for r in caplog.records)


async def test_unconfigured_gateway_is_logged_and_skipped(
    probe: _FakeProbe, built: list[Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = await _resolve(_cfg(), _pod_event(), redis=_Redis())
    assert len(built) == 1  # 3''순 대상일 때만 만든다
    assert link["status"] == "unresolved"
    assert any("reason=gateway_not_configured" in r.getMessage() for r in caplog.records)


# ── 수용 9: 호출 0 = 현행 비트 동일 ────────────────────────────────────
async def test_auto_off_makes_no_gateway_call(probe: _FakeProbe, built: list[Any]) -> None:
    fake = _FakeGateway(_node_result())
    redis = _Redis()
    link = await _resolve(_cfg(auto=False), _pod_event(), cache=await _index_cache(redis),
                          redis=redis, gateway_client=fake)
    assert fake.calls == [] and built == []
    assert link["status"] == "unresolved" and link["reason"] == hm.REASON_NO_INVENTORY


async def test_payload_ok_makes_no_gateway_call(probe: _FakeProbe, built: list[Any]) -> None:
    redis = _Redis()
    fake = _FakeGateway(_node_result(address="203.0.113.9"))
    link = await _resolve(_cfg(), _event(_apm(instance_id=INSTANCE_ID)),
                          cache=await _index_cache(redis), redis=redis, gateway_client=fake)
    assert fake.calls == [] and built == []
    assert link["server_name"] == "NODE-B" and link["host_address"] == NODE_IP
    [learn] = redis.requests()
    assert "node_address_from" not in learn["evidence"]  # 페이로드 주소 — 종전 evidence 그대로


async def test_vm_alarm_makes_no_gateway_call(probe: _FakeProbe, built: list[Any]) -> None:
    fake = _FakeGateway(_node_result())
    event = _event(_apm(instance_name="was01", pod=False, host_address=None,
                        host_address_status="not_found", instance_id=INSTANCE_ID),
                   resource_name="was01", ip_address="192.0.2.10")
    await _resolve(_cfg(), event, redis=_Redis(), gateway_client=fake)
    assert hm.workload_match_key("was01") is None
    assert fake.calls == [] and built == []


async def test_no_instance_id_makes_no_gateway_call(probe: _FakeProbe, built: list[Any]) -> None:
    fake = _FakeGateway(_node_result())
    for bad in (None, True, "abc", -1):
        await _resolve(_cfg(), _pod_event(instance_id=bad), redis=_Redis(), gateway_client=fake)
    assert fake.calls == [] and built == []


# ── 클라이언트 `apm_node_addresses` ──────────────────────────────────────
async def test_client_node_addresses_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gw.ApmGatewayClient("http://127.0.0.1:1/sse")
    seen: list[tuple[str, dict[str, Any]]] = []

    async def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append((tool, arguments))
        return {"rows": []}

    monkeypatch.setattr(client, "_call", _call)
    assert await client.apm_node_addresses(instance_ids=[3]) == {"rows": []}
    await client.apm_node_addresses(instance_ids=[3], source_ids=["jen"], domain_ids=[7])
    await client.apm_node_addresses(instance_ids=[3], wait_seconds=2.5)
    assert seen == [
        (gw.APM_NODE_ADDRESSES_TOOL, {"instance_ids": [3]}),  # wait_seconds None = 싣지 않음
        (gw.APM_NODE_ADDRESSES_TOOL, {"instance_ids": [3], "source_ids": ["jen"],
                                      "domain_ids": [7]}),
        (gw.APM_NODE_ADDRESSES_TOOL, {"instance_ids": [3], "wait_seconds": 2.5}),
    ]


async def test_client_node_addresses_error_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    client = gw.ApmGatewayClient("http://127.0.0.1:1/sse", call_timeout=0.05)

    async def _unknown(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise gw.ApmGatewayClientError(f"게이트웨이 도구 오류({tool}): Unknown tool: {tool}")

    monkeypatch.setattr(client, "_call", _unknown)
    with pytest.raises(gw.ApmGatewayClientError, match="Unknown tool"):
        await client.apm_node_addresses(instance_ids=[3])

    async def _slow(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        await asyncio.sleep(1.0)
        return {}

    monkeypatch.setattr(client, "_call", _slow)
    with pytest.raises(gw.ApmGatewayClientError, match="타임아웃"):
        await client.apm_node_addresses(instance_ids=[3])

    async def _broken(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise OSError("refused")

    monkeypatch.setattr(client, "_call", _broken)
    with pytest.raises(gw.ApmGatewayClientError, match="호출 실패"):
        await client.apm_node_addresses(instance_ids=[3])
