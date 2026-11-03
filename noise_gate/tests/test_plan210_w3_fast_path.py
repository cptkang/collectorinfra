"""plans/210 W3 §3.1 — 진단 고속 경로 조회 순서(`_attach_polestar_link` → `resolve_map_link`).

DB·Redis·게이트웨이는 띄우지 않는다 — 프로브는 대역 함수(호출 기록), Redis는
`tests.mocks.async_redis.FakeAsyncRedis`에 `hmget`·`xadd`만 더한 것, 사본은 실제
`RedisApmHostMapCache`를 그 위에 쓴다.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import time
from types import SimpleNamespace
from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application import apm_host_map_lookup as lk
from noise_gate.application import host_candidates as hc
from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.domain import apm_host_map as hm
from noise_gate.infrastructure import apm_host_map_cache as cm
from noise_gate.infrastructure.host_link_store import HostLinkStore

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6-x7k2p"
WORKLOAD = "bfm-kbland-admin-price"
NODE_IP = "198.51.100.7"  # 문서용 대역(RFC 5737)
SOURCES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


class _Redis(FakeAsyncRedis):
    """사본 조회(HMGET)·스트림 발행(XADD)을 더한 대역."""

    def __init__(self) -> None:
        super().__init__()
        self.streams: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.hmget_calls = 0

    async def hmget(self, name: str, keys: list[str], *args: str) -> list[str | None]:
        self.hmget_calls += 1
        h = self._hash(name) or {}
        return [h.get(k) for k in [*keys, *args]]

    async def xadd(self, name: str, fields: dict[str, Any], **kw: Any) -> str:
        entries = self.streams.setdefault(name, [])
        msg_id = f"{len(entries) + 1}-0"
        entries.append((msg_id, dict(fields)))
        return msg_id

    def requests(self) -> list[dict[str, Any]]:
        out = []
        for _, fields in self.streams.get(cm.REQUEST_STREAM, []):
            decoded = cm.decode_request(fields)
            assert decoded is not None
            out.append(decoded)
        return out


class _FakeProbe:
    """소스별 응답(행 목록 · 예외)을 돌려주고 호출을 기록한다. `delay`로 지연을 넣는다."""

    def __init__(self, table: dict[str, Any] | None = None, delay: float = 0.0) -> None:
        self.table = table or {}
        self.delay = delay
        self.calls: list[tuple[str, list[str], list[str], list[str]]] = []

    async def __call__(
        self, db_id: str, names: list[str], prefixes: list[str], ips: list[str]
    ) -> list[dict[str, Any]]:
        self.calls.append((db_id, names, prefixes, ips))
        if self.delay:
            await asyncio.sleep(self.delay)
        value = self.table.get(db_id, [])
        if isinstance(value, BaseException):
            raise value
        return list(value)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (fake, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


def _cfg(*, on: bool = True, auto: bool = True, host_link: bool = False,
         budget: float = 5.0, max_targets: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        noise_gate=SimpleNamespace(
            host_link_enabled=host_link, apm_host_map_enabled=on,
            apm_host_map_auto_enabled=auto, apm_host_map_max_targets=max_targets,
            apm_host_map_live_budget_seconds=budget,
        ),
        composite=SimpleNamespace(availability_precheck_enabled=False),
    )


def _apm(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_id": "jen", "domain_id": 7, "domain_name": "KB", "instance_name": POD,
        "pod": True, "host_address": NODE_IP, "host_address_status": "ok",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _event(apm: dict[str, Any] | None = None, **over: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        db_id="jennifer_jen", server_name=POD, hostname=POD, ip_address="", alarm_id="a1",
        severity=3, alarm_name="SQL_TOOMANY_FETCH", resource_type="apm.Instance",
        resource_name=POD, alarm_time=None, conditions="", condition_log="",
        raw_payload={"apm": apm if apm is not None else _apm()},
    )
    base.update(over)
    return SimpleNamespace(**base)


def _payload() -> dict[str, Any]:
    return {"meta": {"hints": {"solution": "apm", "source_id": "jen", "domain_id": 7}}}


async def _attach(cfg: Any, event: Any, **kw: Any) -> dict[str, Any]:
    payload = _payload()
    await trig._attach_polestar_link(cfg, event, payload, **kw)
    return payload["meta"]["hints"]


def _row(scope: str, key: str, *, rid: int, origin: str = "auto", state: str = "active",
         servers: tuple[str, ...] = ("NODE-01",), verified: bool = False) -> dict[str, Any]:
    return {
        "id": rid, "source_id": "jen", "domain_id": "7", "scope": scope, "match_key": key,
        "origin": origin, "basis": "node_address", "state": state, "verified": verified,
        "targets": [
            hm.make_target(db_id="polestar_cm_gp", server_name=s, hostname=s.lower(),
                           resource_id=f"r-{s}")
            for s in servers
        ],
        "version": 1,
    }


async def _cache_with(redis: _Redis, *rows: dict[str, Any]) -> cm.RedisApmHostMapCache:
    cache = cm.RedisApmHostMapCache(redis)
    for row in rows:
        assert await cache.put(row)
    return cache


def _lookup_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("apm_map_lookup")]


# ── 1·2순: 매핑 hit = 실시간 조회 0회 ───────────────────────────────────
async def test_instance_hit_makes_no_live_calls(probe: _FakeProbe,
                                                caplog: pytest.LogCaptureFixture) -> None:
    redis = _Redis()
    await _cache_with(redis, _row("instance", POD, rid=11))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    hints = await _attach(_cfg(), _event(), redis=redis)
    link = hints["polestar_link"]
    assert link["status"] == "linked" and link["basis"] == hm.HINT_BASIS
    assert link["map_scope"] == "instance" and link["map_id"] == 11
    assert link["server_name"] == "NODE-01" and link["resource_id"] == "r-NODE-01"
    assert probe.calls == []  # 폴스타 프로브 0회
    assert redis.hmget_calls == 1  # 사본 HMGET 1회
    assert [m["type"] for m in redis.requests()] == ["touch"]  # ITAM 요청 0 · 사용 시각만
    assert redis.requests()[0]["ids"] == [11]
    lines = _lookup_lines(caplog)
    assert len(lines) == 1
    assert "order=2 scope=instance result=hit" in lines[0] and "source=jen" in lines[0]


async def test_manual_workload_beats_auto_instance(probe: _FakeProbe) -> None:
    redis = _Redis()
    await _cache_with(
        redis, _row("instance", POD, rid=1),
        _row("workload", WORKLOAD, rid=2, origin="manual", servers=("ADMIN-01",)),
    )
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["map_origin"] == "manual" and link["server_name"] == "ADMIN-01"
    assert probe.calls == []


async def test_hit_is_much_faster_than_live(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = _FakeProbe({"polestar_cm_gp": [{"name": "NODE-01", "hostname": "node-01",
                                           "ipaddress": NODE_IP}]}, delay=0.05)
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (slow, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    hit_redis = _Redis()
    await _cache_with(hit_redis, _row("instance", POD, rid=1))
    t0 = time.perf_counter()
    await _attach(_cfg(), _event(), redis=hit_redis)
    hit_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    live = (await _attach(_cfg(), _event(), redis=_Redis()))["polestar_link"]
    live_s = time.perf_counter() - t0
    assert live["basis"] == "node_address" and len(slow.calls) == 3
    assert hit_s * 5 < live_s


# ── 3·3'순: 노드 주소 ──────────────────────────────────────────────────
async def test_node_address_live_links_and_publishes_learn(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    redis = _Redis()
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link == {
        "status": "linked", "db_id": "polestar_cm_gp", "server_name": "NODE-01",
        "hostname": "node-01", "basis": "node_address", "host_address": NODE_IP,
        "at": link["at"],
    }
    [learn] = redis.requests()
    assert learn["type"] == "learn" and learn["instance_name"] == POD
    assert learn["basis"] == hm.BASIS_NODE_ADDRESS and learn["domain_id"] == "7"
    assert learn["targets"][0]["server_name"] == "NODE-01"
    assert learn["targets"][0]["ip"] == NODE_IP
    assert "order=3' scope=- result=live" in _lookup_lines(caplog)[0]


async def test_node_address_index_hit_skips_probe(probe: _FakeProbe,
                                                  caplog: pytest.LogCaptureFixture) -> None:
    redis = _Redis()
    cache = cm.RedisApmHostMapCache(redis)
    await cache.rebuild_sms_index(
        {NODE_IP: [hm.make_target(db_id="polestar_b0", server_name="NODE-B", resource_id="9")]},
        {},
    )
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["server_name"] == "NODE-B" and link["resource_id"] == "9"
    assert probe.calls == []
    assert "order=3 scope=- result=index" in _lookup_lines(caplog)[0]


async def test_stale_node_address_is_not_used(probe: _FakeProbe) -> None:
    """계획 이탈 — D-355 ⑦: `stale`은 자동 연결하지 않는다(`NODE_ADDRESS_LINK_STATUSES`)."""
    assert lk.NODE_ADDRESS_LINK_STATUSES == frozenset({"ok"})
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    link = (await _attach(_cfg(), _event(_apm(host_address_status="stale")),
                          redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved" and probe.calls == []


async def test_node_address_beats_auto_workload(probe: _FakeProbe) -> None:
    """3순은 2순과 4순 사이 — 새 파드명은 자기 노드 주소가 워크로드 집합보다 앞선다."""
    probe.table["polestar_cm_gp"] = [{"name": "NODE-02", "hostname": "node-02",
                                      "ipaddress": NODE_IP}]
    redis = _Redis()
    await _cache_with(redis, _row("workload", WORKLOAD, rid=5, servers=("NODE-01",)))
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["server_name"] == "NODE-02" and link["basis"] == "node_address"


# ── 4순: 자동 workload → 대상 전부 ──────────────────────────────────────
async def test_workload_mapping_with_three_targets(probe: _FakeProbe,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    redis = _Redis()
    await _cache_with(redis, _row("workload", WORKLOAD, rid=5,
                                  servers=("NODE-01", "NODE-02", "NODE-03")))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(), event, redis=redis))["polestar_link"]
    assert link["basis"] == hm.HINT_BASIS and len(link["targets"]) == 3
    assert link["db_id"] == "polestar_cm_gp" and link["server_name"] == "NODE-01"
    assert probe.calls == []
    assert "order=4 scope=workload result=hit" in _lookup_lines(caplog)[0]


# ── disabled · failed · ambiguous ──────────────────────────────────────
async def test_disabled_mapping_is_absent(probe: _FakeProbe) -> None:
    redis = _Redis()
    await _cache_with(redis, _row("domain", "", rid=3, origin="manual", state="disabled"))
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link == {"status": "absent", "basis": hm.HINT_BASIS}
    assert probe.calls == [] and redis.requests() == []


@pytest.mark.parametrize(("state", "reason"), [
    ("failed", hm.REASON_FAILED), ("ambiguous", hm.REASON_AMBIGUOUS),
])
async def test_unusable_row_reason(probe: _FakeProbe, state: str, reason: str) -> None:
    redis = _Redis()
    await _cache_with(redis, _row("workload", WORKLOAD, rid=4, state=state))
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(), event, redis=redis))["polestar_link"]
    assert link["status"] == "unresolved" and link["reason"] == reason
    assert link["no_config_fallback"] is True


# ── 7순: 미확정 사유 · ITAM 요청 ────────────────────────────────────────
async def test_miss_auto_on_pending_and_one_itam_request(probe: _FakeProbe,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    redis = _Redis()
    caplog.set_level(logging.INFO, logger=lk.__name__)
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(), event, redis=redis))["polestar_link"]
    # 파드면 `pod: true`를 함께 싣는다 — 화면이 서버 선택 UI를 열지 않는다(D-355 ④)
    assert link == {"status": "unresolved", "reason": hm.REASON_PENDING,
                    "no_config_fallback": True, "pod": True}
    assert [m["type"] for m in redis.requests()] == ["itam_request"]
    assert redis.requests()[0]["domain_id"] == "7"
    # 같은 도메인 두 번째 miss — inflight가 막아 다시 요청하지 않는다
    await _attach(_cfg(), event, redis=redis)
    assert [m["type"] for m in redis.requests()] == ["itam_request"]
    assert "order=7 scope=- result=miss" in _lookup_lines(caplog)[0]


async def test_miss_auto_off_no_inventory_and_no_publish(probe: _FakeProbe) -> None:
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    redis = _Redis()
    # auto off — 직접 조회 연결은 쓰되 되적재·touch·ITAM 발행은 없다
    link = (await _attach(_cfg(auto=False), _event(), redis=redis))["polestar_link"]
    assert link["status"] == "linked" and redis.requests() == []
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(auto=False), event, redis=redis))["polestar_link"]
    assert link["reason"] == hm.REASON_NO_INVENTORY and redis.requests() == []


async def test_stored_link_is_order_six(probe: _FakeProbe) -> None:
    redis = _Redis()
    store = HostLinkStore(redis)
    await store.save("jen", POD, kind="link", confirmed=True, by="op", by_role="operator",
                     db_id="polestar_b0", server_name="SAVED-01", polestar_hostname="saved-01")
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(host_link=True), event, redis=redis))["polestar_link"]
    assert link["status"] == "linked" and link["server_name"] == "SAVED-01"
    # host_link off면 6순을 보지 않는다
    link = (await _attach(_cfg(host_link=False), event, redis=redis))["polestar_link"]
    assert link["status"] == "unresolved"


# ── 노드 주소 미설정 사유 ───────────────────────────────────────────────
@pytest.mark.parametrize(("apm_over", "expected"), [
    ({"host_address": None, "host_address_status": None}, "missing"),
    ({"host_address": "", "host_address_status": "disabled"}, "disabled"),
    ({"host_address": "", "host_address_status": "login_blocked"}, "login_blocked"),
    ({"host_address": "", "host_address_status": "login_failed"}, "login_failed"),
    ({"host_address": "", "host_address_status": "error"}, "error"),
])
async def test_node_address_status_reason(probe: _FakeProbe, apm_over: dict[str, Any],
                                          expected: str) -> None:
    link = (await _attach(_cfg(), _event(_apm(**apm_over)), redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved" and link["node_address_status"] == expected


async def test_node_address_status_rides_linked_hint(probe: _FakeProbe) -> None:
    redis = _Redis()
    await _cache_with(redis, _row("instance", POD, rid=1))
    event = _event(_apm(host_address=None, host_address_status=None))
    link = (await _attach(_cfg(), event, redis=redis))["polestar_link"]
    assert link["status"] == "linked" and link["node_address_status"] == "missing"
    # ok·not_found는 사유를 싣지 않는다
    for status in ("ok", "not_found"):
        link = (await _attach(_cfg(), _event(_apm(host_address_status=status)),
                              redis=redis))["polestar_link"]
        assert "node_address_status" not in link


# ── 5순: VM 정확 일치 ───────────────────────────────────────────────────
def _vm_event(ip: str = "192.0.2.20", host: str = "was01") -> SimpleNamespace:
    apm = {"source_id": "jen", "domain_id": 7, "instance_name": "WAS01", "pod": False}
    return _event(apm, ip_address=ip, hostname=host, resource_name="WAS01")


async def test_vm_index_then_live_exact_match(probe: _FakeProbe) -> None:
    redis = _Redis()
    cache = cm.RedisApmHostMapCache(redis)
    await cache.rebuild_sms_index(
        {"192.0.2.20": [hm.make_target(db_id="polestar_b0", server_name="WAS01")]}, {},
    )
    link = (await _attach(_cfg(), _vm_event(), redis=redis))["polestar_link"]
    assert link["basis"] == hm.HINT_BASIS and link["map_basis"] == hm.BASIS_VM_EXACT
    assert link["server_name"] == "WAS01" and probe.calls == []
    learn = redis.requests()[0]
    assert learn["type"] == "learn" and learn["basis"] == hm.BASIS_VM_EXACT

    # 색인 miss → probe_hosts 정확 일치(이름 넓힘 없음 · IP 행 일치)
    probe.table["polestar_cm_gp"] = [{"name": "WAS02", "hostname": "was02.corp",
                                      "ipaddress": "192.0.2.21"}]
    link = (await _attach(_cfg(), _vm_event("192.0.2.21", "was02"),
                          redis=_Redis()))["polestar_link"]
    assert link["server_name"] == "WAS02" and len(probe.calls) == 3
    assert all(c[2] == [] for c in probe.calls)  # 접두 LIKE 없음


async def test_vm_two_servers_is_ambiguous(probe: _FakeProbe) -> None:
    probe.table["polestar_b0"] = [{"name": "A", "hostname": "a", "ipaddress": "192.0.2.30"}]
    probe.table["polestar_cm_gp"] = [{"name": "B", "hostname": "b", "ipaddress": "192.0.2.30"}]
    redis = _Redis()
    link = (await _attach(_cfg(), _vm_event("192.0.2.30", ""), redis=redis))["polestar_link"]
    assert link["reason"] == hm.REASON_AMBIGUOUS
    learn = [m for m in redis.requests() if m["type"] == "learn"][0]
    assert learn["state"] == hm.STATE_AMBIGUOUS and len(learn["targets"]) == 2


async def test_pod_does_not_take_vm_step(probe: _FakeProbe) -> None:
    event = _event(_apm(host_address="", host_address_status="not_found"),
                   ip_address="10.0.0.9")
    await _attach(_cfg(), event, redis=_Redis())
    assert probe.calls == []


# ── 직접 조회 예산 ──────────────────────────────────────────────────────
async def test_live_budget_cuts_slow_probe(monkeypatch: pytest.MonkeyPatch,
                                           caplog: pytest.LogCaptureFixture) -> None:
    slow = _FakeProbe({"polestar_cm_gp": [{"name": "N", "hostname": "n",
                                           "ipaddress": NODE_IP}]}, delay=0.5)
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (slow, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    t0 = time.perf_counter()
    link = (await _attach(_cfg(budget=0.05), _event(), redis=_Redis()))["polestar_link"]
    assert time.perf_counter() - t0 < 0.4
    assert link["status"] == "unresolved"
    assert any("예산 초과" in r.getMessage() for r in caplog.records)


# ── 사본 없음 · 오류 ────────────────────────────────────────────────────
async def test_cache_error_falls_through_to_live(probe: _FakeProbe) -> None:
    class _Broken(_Redis):
        async def hmget(self, name: str, keys: list[str], *args: str) -> list[str | None]:
            raise ConnectionError("down")

        async def hget(self, name: str, key: str) -> str | None:
            raise ConnectionError("down")

    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    link = (await _attach(_cfg(), _event(), redis=_Broken()))["polestar_link"]
    assert link["status"] == "linked" and link["basis"] == "node_address"


async def test_explicit_map_cache_is_used_without_redis(probe: _FakeProbe) -> None:
    redis = _Redis()
    cache = await _cache_with(redis, _row("instance", POD, rid=8))
    link = (await _attach(_cfg(), _event(), map_cache=cache))["polestar_link"]
    assert link["map_id"] == 8
    # 발행은 사본의 연결로 한다(API 프로세스 — redis 인자 없음)
    assert [m["type"] for m in redis.requests()] == ["touch"]


# ── 플래그 off — 비트 동일 ──────────────────────────────────────────────
@pytest.mark.parametrize("host_link", [False, True])
@pytest.mark.parametrize("status", ["ok", "disabled", None])
async def test_flag_off_payload_bit_identical(probe: _FakeProbe, host_link: bool,
                                              status: str | None) -> None:
    """off(미설정 포함) = 종전 `_attach_polestar_link`와 같은 힌트·같은 프로브 호출."""
    probe.table["polestar_cm_gp"] = [{"name": "NODE-01", "hostname": "node-01",
                                      "ipaddress": NODE_IP}]
    event = _event(_apm(host_address_status=status))
    legacy_cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(host_link_enabled=host_link),
        composite=SimpleNamespace(availability_precheck_enabled=False),
    )
    redis = _Redis()
    await _cache_with(redis, _row("instance", POD, rid=1))  # 매핑이 있어도 쓰지 않는다
    a = await _attach(legacy_cfg, copy.deepcopy(event), redis=redis)
    calls_a = list(probe.calls)
    probe.calls.clear()
    b = await _attach(_cfg(on=False, host_link=host_link), copy.deepcopy(event), redis=redis)
    for hints in (a, b):
        hints.get("polestar_link", {}).pop("at", None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert calls_a == probe.calls
    assert redis.requests() == [] and redis.hmget_calls == 0
    assert a.get("polestar_link", {}).get("basis") != hm.HINT_BASIS


async def test_non_apm_payload_untouched(probe: _FakeProbe) -> None:
    payload: dict[str, Any] = {"meta": {}}
    await trig._attach_polestar_link(_cfg(), _event(), payload, redis=_Redis())
    assert payload == {"meta": {}}
