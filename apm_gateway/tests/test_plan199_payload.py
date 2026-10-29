"""plans/199 W2 — `alarm:raw` 노드 주소 칸 · `apm_instance_map` `host_address` 칸 (D-352 ①⑦).

Open API는 합성 픽스처(`synthetic_handler`), 웹 목록은 대역(`FakeLister`)으로 대신한다 — 실제
제니퍼·외부 네트워크 없음. 플래그 off 페이로드는 계획 시작 SHA(60b4992b)에서 같은 입력으로 받은
직렬화 문자열을 스냅샷으로 고정한다(비트 동일).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from apm_gateway.application import node_address as na
from apm_gateway.application.node_address import NodeAddressService, build_node_address
from apm_gateway.application.poller import EventPoller
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from conftest import NOW_S, make_cfg, synthetic_handler
from test_poller import FakeRedis

JMX_PW = "JMX-SECRET-pw-9"
# 계획 시작 SHA 60b4992b 폴러가 같은 합성 입력으로 XADD한 `data` 문자열(그대로 — 비트 동일 기준)
SNAPSHOT_OFF = (
    '{"dbId": "jennifer", "source": "jennifer", "serverName": "was-host01.example.local",'
    ' "hostname": "was-host01.example.local", "ipAddress": "10.0.0.11", "resourceAncestry":'
    ' "JENNIFER > demo-domain > was01_a", "alarmId": "jennifer:96f55a7a9b30f4ba", "severity": 3,'
    ' "alarmStatus": "", "resourceType": "apm.Instance", "resourceName": "was01_a", "alarmName":'
    ' "ERROR_OUTOFMEMORY", "alarmTime": "20260929095800", "conditions": "JENNIFER EVENT fatal —'
    ' ERROR_OUTOFMEMORY", "conditionLog": "java.lang.OutOfMemoryError: Java heap space'
    ' user=<email> (value=1)", "apm": {"source": "jennifer", "source_id": "default", "domain_id":'
    ' 1000, "domain_name": "demo-domain", "instance_id": 1001, "instance_name": "was01_a",'
    ' "event_type": "ERROR_OUTOFMEMORY", "event_type_norm": "OUTOFMEMORY", "event_kind": "error",'
    ' "level": "fatal", "value": 1.0, "txid": "9000", "time_ms": 1790643480000, "application":'
    ' "/order/list?user=<v>", "match_confidence": "high", "match_reason": "host_name",'
    ' "was_signals": [{"kind": "was_heap_pressure", "level": "CRITICAL", "category": "strong",'
    ' "label": "힙 메모리 압박", "evidence": "이벤트 ERROR_OUTOFMEMORY", "instance_id": 1001,'
    ' "source_tool": "event_poller", "source_id": "default"}], "idempotency_key":'
    ' "96f55a7a9b30f4bab8989ce737912400a2edaa217e7d19e04be3afadd8f27318"}}'
)
NODE_KEYS = {"host_address", "host_address_status", "pod"}


class FakeLister:
    """웹 세션 대역 — 허용 칸 파싱이 끝난 행을 돌려준다(원본 JMX 칸은 파서가 이미 버렸다)."""

    def __init__(self, rows: dict[int, list[dict]], *, source_id: str = "default") -> None:
        self.source_id = source_id
        self.rows = rows
        self.calls: list[int] = []
        self.fail = False

    @property
    def configured(self) -> bool:
        return True

    async def fetch_list(self, sid: int) -> list[dict]:
        self.calls.append(sid)
        if self.fail:
            raise na.WebSessionError("가짜 실패")
        return self.rows.get(sid, [])

    async def aclose(self) -> None:
        return None


def _rows(ip: str, host: str) -> dict[int, list[dict]]:
    from apm_gateway.adapters.jennifer.web_fields import parse_upgrade_list

    raw = [
        {
            "instId": 1001,
            "sid": 1000,
            "hostName": "was01_a",
            "ip": ip,
            "hostAddress": host,
            "status": 3,
            "statusMsg": "",
            "lastLiveTime": 0,
            "jmx": {"passwordOrEmpty": JMX_PW},
        }
    ]
    return {1000: parse_upgrade_list(raw)}


def _transport() -> httpx.MockTransport:
    return httpx.MockTransport(synthetic_handler())


def _poller(redis: FakeRedis, node_address=None, extra: dict | None = None) -> EventPoller:
    cfg = make_cfg(
        "http://apm.test", extra={"APM_EVENT_POLLER_ENABLED": "true", **(extra or {})}
    )
    sources = build_source_set(cfg, transport=_transport())
    if node_address is None:
        return EventPoller(sources, cfg, redis, clock=lambda: NOW_S)
    return EventPoller(sources, cfg, redis, clock=lambda: NOW_S, node_address=node_address)


def _service(lister: FakeLister) -> NodeAddressService:
    return NodeAddressService({lister.source_id: lister}, clock=lambda: NOW_S)


# ── 플래그 off = 비트 동일 ──────────────────────────────────


async def test_flag_off_payload_is_bit_identical_snapshot():
    cfg = make_cfg("http://apm.test", extra={"APM_EVENT_POLLER_ENABLED": "true"})
    assert cfg.node_address.enabled is False and build_node_address(cfg) is None
    redis = FakeRedis()
    poller = _poller(redis)
    assert await poller.poll_once() == 1
    assert [rec["data"] for rec in redis.stream] == [SNAPSHOT_OFF]
    assert "node_address" not in poller.status()


# ── 플래그 on — 가산 칸 ─────────────────────────────────────


async def test_pod_payload_adds_three_keys_and_keeps_existing(caplog):
    caplog.set_level(logging.DEBUG)
    lister = FakeLister(_rows("10.139.0.5", "10.136.1.7"))
    redis = FakeRedis()
    poller = _poller(redis, _service(lister))
    assert await poller.poll_once() == 1
    data = redis.stream[0]["data"]
    payload = json.loads(data)
    assert {k: payload["apm"][k] for k in NODE_KEYS} == {
        "host_address": "10.136.1.7",
        "host_address_status": "ok",
        "pod": True,
    }
    # 기존 키·값은 그대로(가산만) — 노드 칸을 빼면 스냅샷과 같다
    for key in NODE_KEYS:
        del payload["apm"][key]
    assert payload == json.loads(SNAPSHOT_OFF)
    assert payload["ipAddress"] == "10.0.0.11"  # 인벤토리 IP는 판정에 쓰지 않는다
    assert JMX_PW not in data and "jmx" not in data
    assert JMX_PW not in caplog.text
    assert lister.calls == [1000]
    assert poller.status()["node_address"]["fetch_total"] == 1


async def test_vm_payload_pod_false():
    lister = FakeLister(_rows("10.0.0.11", "10.0.0.11"))
    redis = FakeRedis()
    await _poller(redis, _service(lister)).poll_once()
    apm = json.loads(redis.stream[0]["data"])["apm"]
    assert (apm["host_address"], apm["host_address_status"], apm["pod"]) == (
        "10.0.0.11",
        "ok",
        False,
    )


async def test_failure_without_memory_has_no_pod_key():
    lister = FakeLister({})
    lister.fail = True
    redis = FakeRedis()
    await _poller(redis, _service(lister)).poll_once()
    apm = json.loads(redis.stream[0]["data"])["apm"]
    assert apm["host_address"] == "" and apm["host_address_status"] == "error"
    assert "pod" not in apm


async def test_duplicate_event_is_not_looked_up():
    lister = FakeLister(_rows("10.139.0.5", "10.136.1.7"))
    service = _service(lister)
    seen: list[tuple] = []
    original = service.lookup

    async def counting(*args):
        seen.append(args)
        return await original(*args)

    service.lookup = counting  # type: ignore[method-assign]
    redis = FakeRedis()
    poller = _poller(redis, service)
    assert await poller.poll_once() == 1
    assert await poller.poll_once() == 0  # 멱등 키로 중복 — 조회 없음
    assert len(seen) == 1 and poller.duplicates_total == 1


async def test_slow_lookup_publishes_error_within_bound(monkeypatch):
    import asyncio

    class Slow(FakeLister):
        async def fetch_list(self, sid: int) -> list[dict]:
            await asyncio.sleep(5)
            return []

    monkeypatch.setattr(na, "LOOKUP_TIMEOUT_SECONDS", 0.05)
    redis = FakeRedis()
    assert await _poller(redis, _service(Slow({}))).poll_once() == 1
    apm = json.loads(redis.stream[0]["data"])["apm"]
    assert apm["host_address_status"] == "error" and "pod" not in apm


async def test_source_without_web_account_is_disabled():
    cfg = make_cfg(
        "http://apm.test",
        extra={"APM_EVENT_POLLER_ENABLED": "true", "JENNIFER_NODE_ADDRESS_ENABLED": "true"},
    )
    service = build_node_address(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(599)))
    assert service is not None
    redis = FakeRedis()
    await _poller(redis, service).poll_once()
    apm = json.loads(redis.stream[0]["data"])["apm"]
    assert {k: apm[k] for k in ("host_address", "host_address_status")} == {
        "host_address": "",
        "host_address_status": "disabled",
    }
    assert "pod" not in apm


# ── apm_instance_map ────────────────────────────────────────


def _tools(node_address=None) -> ApmTools:
    cfg = make_cfg("http://apm.test")
    return ApmTools(
        build_source_set(cfg, transport=_transport()),
        cfg,
        clock=lambda: NOW_S,
        node_address=node_address,
    )


async def test_instance_map_off_has_no_host_address_column():
    out = await _tools().apm_instance_map(domain_id=1000)
    assert out["rows"] and all("host_address" not in row for row in out["rows"])


@pytest.mark.parametrize(
    "kwargs", [{"domain_id": 1000}, {"hostname": "was-host01.example.local"}, {"query": "was01"}]
)
async def test_instance_map_on_adds_host_address(kwargs):
    lister = FakeLister(_rows("10.139.0.5", "10.136.1.7"))
    out = await _tools(_service(lister)).apm_instance_map(**kwargs)
    rows = out["rows"]
    assert rows and all("host_address" in row for row in rows)
    by_id = {row["instance_id"]: row["host_address"] for row in rows}
    assert by_id.get(1001) == "10.136.1.7"
    assert all(v == "" for k, v in by_id.items() if k != 1001)
    text = json.dumps(out, ensure_ascii=False)
    assert JMX_PW not in text and "jmx" not in text


async def test_instance_map_over_domain_limit_reads_cache_only(monkeypatch):
    """결과 도메인 수가 상한을 넘으면 새로 조회하지 않고 캐시만 읽는다(전 도메인 조회 방지)."""
    from apm_gateway.application import instance_tools

    monkeypatch.setattr(instance_tools, "MCP_FETCH_DOMAIN_LIMIT", 0)
    lister = FakeLister(_rows("10.139.0.5", "10.136.1.7"))
    service = _service(lister)
    tools = _tools(service)
    cold = await tools.apm_instance_map(domain_id=1000)
    assert lister.calls == [] and all(r["host_address"] == "" for r in cold["rows"])
    await service.lookup("default", 1000, 1001)  # 알람 경로가 채운 캐시
    warm = await tools.apm_instance_map(domain_id=1000)
    assert lister.calls == [1000]
    assert {r["instance_id"]: r["host_address"] for r in warm["rows"]}[1001] == "10.136.1.7"
