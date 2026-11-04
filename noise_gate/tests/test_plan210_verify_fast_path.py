"""plans/210 독립 검증 — 진단 고속 경로 조회 순서 경계·예외·예산·재시도 (verifier).

기존 W3 테스트(`test_plan210_w3_fast_path`)의 대역을 재사용한다. 결함을 고정했던
`xfail(strict=True)` 3건(3'·5 직접 조회 예외 · `next_retry_at`)은 교정 라운드에서 고쳐
표지를 걷었다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from noise_gate.application import apm_host_map_lookup as lk
from noise_gate.application import host_candidates as hc
from noise_gate.application.apm_host_map_scan import handle_itam_request
from noise_gate.domain import apm_host_map as hm
from noise_gate.domain.apm_host_map import itam_request_message
from noise_gate.infrastructure import apm_host_map_cache as cm

from .plan210_w5_fakes import FakeCache, FakeGateway, FakeRepo, apm_row
from .test_plan210_w3_fast_path import (
    NODE_IP,
    POD,
    SOURCES,
    WORKLOAD,
    _apm,
    _attach,
    _cache_with,
    _cfg,
    _event,
    _FakeProbe,
    _lookup_lines,
    _Redis,
    _row,
)


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (fake, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


def _node(name: str, ip: str) -> list[dict[str, Any]]:
    return [{"name": name, "hostname": name.lower(), "ipaddress": ip}]


# ── 조회 순서 경계 (§3.1) ───────────────────────────────────────────────
async def test_manual_domain_row_beats_payload_node_address(probe: _FakeProbe) -> None:
    """1순(관리자 domain 행)이 3순(페이로드 노드 주소)보다 앞선다 — 프로브 0회."""
    probe.table["polestar_cm_gp"] = _node("NODE-LIVE", NODE_IP)
    redis = _Redis()
    await _cache_with(redis, _row("domain", "", rid=21, origin="manual", servers=("ADMIN-D",)))
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["server_name"] == "ADMIN-D" and link["map_scope"] == "domain"
    assert probe.calls == []


async def test_verified_auto_workload_beats_payload_node_address(probe: _FakeProbe) -> None:
    """관리자가 확인한 자동 workload 행은 1순(관리자 행)이다 — 3순보다 앞선다."""
    probe.table["polestar_cm_gp"] = _node("NODE-LIVE", NODE_IP)
    redis = _Redis()
    await _cache_with(redis, _row("workload", WORKLOAD, rid=22, verified=True,
                                  servers=("V-01", "V-02")))
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["map_id"] == 22 and len(link["targets"]) == 2
    assert probe.calls == []


async def test_index_two_servers_skips_live_and_falls_to_workload(
    probe: _FakeProbe, caplog: pytest.LogCaptureFixture
) -> None:
    """3순 색인이 2대면 불성립 — 직접 조회(3')도 하지 않고 4순으로 간다."""
    redis = _Redis()
    cache = cm.RedisApmHostMapCache(redis)
    await cache.rebuild_sms_index({NODE_IP: [
        hm.make_target(db_id="polestar_b0", server_name="A"),
        hm.make_target(db_id="polestar_cm_gp", server_name="B"),
    ]}, {})
    await cache.put(_row("workload", WORKLOAD, rid=23, servers=("W-01",)))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["map_id"] == 23 and probe.calls == []
    assert "order=4 scope=workload result=hit" in _lookup_lines(caplog)[0]


async def test_manual_row_with_only_gone_or_dpm_targets_is_not_used(probe: _FakeProbe) -> None:
    """관리자 행이라도 대상이 전부 `target_gone`·`kind != sms`면 쓰지 않고 다음 순(3')으로."""
    probe.table["polestar_cm_gp"] = _node("NODE-LIVE", NODE_IP)
    row = _row("instance", POD, rid=24, origin="manual")
    row["targets"] = [
        {**row["targets"][0], "target_gone": True},
        hm.make_target(db_id="dpm1", server_name="DB-X", kind=hm.KIND_DPM),
    ]
    redis = _Redis()
    await _cache_with(redis, row)
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["status"] == "linked" and link["server_name"] == "NODE-LIVE"
    assert link["basis"] == "node_address"


async def test_hit_with_auto_off_publishes_nothing(probe: _FakeProbe) -> None:
    """auto off — 매핑 hit여도 touch를 발행하지 않는다(되적재 경로 전체 off)."""
    redis = _Redis()
    await _cache_with(redis, _row("instance", POD, rid=25))
    link = (await _attach(_cfg(auto=False), _event(), redis=redis))["polestar_link"]
    assert link["map_id"] == 25 and redis.requests() == []


async def test_absent_publishes_nothing_and_skips_itam(probe: _FakeProbe) -> None:
    redis = _Redis()
    await _cache_with(redis, _row("instance", POD, rid=26, origin="manual", state="disabled"))
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link == {"status": "absent", "basis": hm.HINT_BASIS}
    assert redis.requests() == [] and probe.calls == []


async def test_publish_failure_does_not_break_link(probe: _FakeProbe) -> None:
    """되적재 XADD 실패(Redis 오류)는 이번 판정을 막지 않는다 — 연결은 그대로."""
    class _NoXadd(_Redis):
        async def xadd(self, name: str, fields: dict[str, Any], **kw: Any) -> str:
            raise ConnectionError("down")

    probe.table["polestar_cm_gp"] = _node("NODE-01", NODE_IP)
    link = (await _attach(_cfg(), _event(), redis=_NoXadd()))["polestar_link"]
    assert link["status"] == "linked" and link["server_name"] == "NODE-01"


async def test_inflight_redis_error_skips_itam_request(probe: _FakeProbe) -> None:
    class _NoSet(_Redis):
        async def set(self, *a: Any, **kw: Any) -> Any:
            raise ConnectionError("down")

    redis = _NoSet()
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(), event, redis=redis))["polestar_link"]
    assert link["status"] == "unresolved" and redis.requests() == []


# ── 직접 조회 예산 — 3'·5 합계 ───────────────────────────────────────────
async def test_budget_is_shared_between_node_live_and_vm_live(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """VM(파드 아님)에 노드 주소가 ok로 실려 오면 3'과 5가 모두 직접 조회 후보다 — 3'이 예산을
    다 쓰면 5는 「예산 소진」으로 생략한다(합계 예산)."""
    slow = _FakeProbe(delay=0.2)
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (slow, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    caplog.set_level(logging.INFO, logger=lk.__name__)
    apm = {"source_id": "jen", "domain_id": 7, "instance_name": "WAS01", "pod": False,
           "host_address": NODE_IP, "host_address_status": "ok", "ip": "192.0.2.40"}
    event = _event(apm, resource_name="WAS01", hostname="was01", ip_address="192.0.2.40")
    link = (await _attach(_cfg(budget=0.1), event, redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved"
    assert len(slow.calls) == 1  # 3'의 첫 소스에서 끊기고 5는 부르지 않는다
    messages = [r.getMessage() for r in caplog.records]
    assert any("예산 초과" in m and "step=3'" in m for m in messages)
    assert any("예산 소진" in m and "step=5" in m for m in messages)


# ── 예외 — 종전 경로는 삼키고 사유로 남겼다 ─────────────────────────────
async def test_node_live_source_resolution_error_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(app_config: Any) -> list[str]:
        raise RuntimeError("registry load failed")

    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (_FakeProbe(), None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    link = (await _attach(_cfg(), _event(), redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved"


async def test_flag_off_node_live_source_resolution_error_is_absorbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대조군 — 플래그 off(종전 경로)는 같은 예외를 삼키고 `node_unchecked`로 남긴다."""
    def _boom(app_config: Any) -> list[str]:
        raise RuntimeError("registry load failed")

    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (_FakeProbe(), None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    link = (await _attach(_cfg(on=False), _event(), redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved" and link["pod"] is True


async def test_vm_live_source_resolution_error_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(app_config: Any) -> list[str]:
        raise RuntimeError("registry load failed")

    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (_FakeProbe(), None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    apm = {"source_id": "jen", "domain_id": 7, "instance_name": "WAS01", "pod": False}
    event = _event(apm, resource_name="WAS01", hostname="was01", ip_address="192.0.2.41")
    link = (await _attach(_cfg(), event, redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved"


# ── `NOISE_APM_HOST_MAP_RETRY_HOURS` — ITAM 요청이 `next_retry_at`을 읽는다 ──────
def _itam_repo() -> FakeRepo:
    repo = FakeRepo(extra_columns={"itam_snapshot_server": ("clue_text",)})
    repo.set_snapshot("itam_snapshot_app", "r0", [{"app_code": "STL", "app_name": "결제정산"}])
    repo.set_snapshot("itam_snapshot_server", "r0", [
        {"hostname": "nohost", "ips": ["10.2.0.99"], "active": None, "clue_text": ""}])
    repo.set_snapshot("sms_inventory", "r0", [
        {"db_id": "zone_b", "resource_id": "4", "server_name": "stlbat01",
         "hostname": "stlbat01", "ip": "10.2.0.9", "nic_ips": []}])
    return repo


async def test_itam_request_respects_next_retry_at() -> None:
    from datetime import timedelta

    from .plan210_w5_fakes import NOW

    repo, cache = _itam_repo(), FakeCache()
    repo.seed("s1", "30", "domain", "", state="failed", basis="itam",
              next_retry_at=(NOW + timedelta(hours=20)).isoformat())
    gateway = FakeGateway({"apm_instance_map": lambda args: {"rows": [
        apm_row("s1", "30", "b1", "settle-batch", domain_name="결제정산")]}})
    msg = itam_request_message(source_id="s1", domain_id="30", domain_name="결제정산")
    await handle_itam_request(msg, repo=repo, cache=cache, gateway_client=gateway, now=NOW)
    assert gateway.calls == []


def test_next_retry_at_has_reader() -> None:
    """정적 대조 — `next_retry_at`을 조건으로 읽는 곳은 ITAM 요청 처리(`apm_host_map_scan`)다.
    사본 값(`cache_value`)에는 싣지 않는다(워커 조회와 무관)."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    readers = []
    for rel in ("application/apm_host_map_lookup.py", "application/apm_host_map_consumer.py",
                "application/apm_host_map_scan.py", "infrastructure/apm_host_map_cache.py"):
        text = (root / rel).read_text(encoding="utf-8")
        if "next_retry_at" in text:
            readers.append(rel)
    assert readers == ["application/apm_host_map_scan.py"]
    assert "next_retry_at" not in hm.cache_value({"id": 1, "next_retry_at": "x"})


# ── 소비자 — 독 메시지·ack ──────────────────────────────────────────────
async def test_learn_with_invalid_key_is_acked_and_logged(caplog: pytest.LogCaptureFixture
                                                           ) -> None:
    """빈 domain_id learn은 저장소가 거부한다 — 행별 격리 후 ack(루프를 막지 않는다)."""
    from noise_gate.application.apm_host_map_consumer import ApmHostMapConsumer

    from .test_plan210_w3_consumer import _StreamRedis

    class _Repo:
        def __init__(self) -> None:
            self.applied: list[Any] = []

        async def get_by_key(self, *a: Any) -> None:
            return None

        async def apply_auto_many(self, source_id: str, proposals: Any, **kw: Any) -> Any:
            self.applied.append(list(proposals))
            return [("error", None) for _ in proposals]

    redis = _StreamRedis()
    await cm.publish_request(redis, hm.learn_message(
        source_id="jen", domain_id="", instance_name=POD, basis="node_address",
        targets=[hm.make_target(db_id="polestar_cm_gp", server_name="N1")],
    ))
    cache = FakeCache()
    consumer = ApmHostMapConsumer(_Repo(), cache, redis=redis, consumer="t")  # type: ignore[arg-type]
    await consumer.ensure_group()
    n = await consumer.consume_once()
    assert n == 1 and cache.put_rows == [] and len(redis.acked) == 1
    assert await consumer.consume_once() == 0  # ack됐으므로 다시 오지 않는다
    await asyncio.sleep(0)
