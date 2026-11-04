"""plans/210 교정 라운드 — 직접 조회 예외 사유 · ITAM 재시도 시각 · 스트림 메시지 검증 · 저장 연결
`previous_link`(플래그 on).

락 원자화(`test_plan210_w5_scan`)·`set_targets_gone` 낙관적 잠금(`test_plan210_w1_repository`)은 각
기존 파일에 둔다.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest

from noise_gate.application import apm_host_map_consumer as cons
from noise_gate.application import apm_host_map_lookup as lk
from noise_gate.application import host_candidates as hc
from noise_gate.application.apm_host_map_scan import handle_itam_request
from noise_gate.domain import apm_host_map as hm
from noise_gate.domain.host_link import NODE_UNCHECKED
from noise_gate.infrastructure import apm_host_map_cache as cm
from noise_gate.infrastructure.host_link_store import HostLinkStore

from .plan210_w5_fakes import NOW, FakeCache, FakeGateway, FakeRepo, apm_row
from .test_plan210_w3_consumer import _Repo, _StreamRedis
from .test_plan210_w3_fast_path import (
    NODE_IP,
    POD,
    SOURCES,
    _apm,
    _attach,
    _cache_with,
    _cfg,
    _event,
    _FakeProbe,
    _Redis,
    _row,
)


def _boom(app_config: Any) -> list[str]:
    raise RuntimeError("registry load failed")


@pytest.fixture
def probe(monkeypatch: pytest.MonkeyPatch) -> _FakeProbe:
    fake = _FakeProbe()
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (fake, None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", lambda app_config: list(SOURCES))
    return fake


# ── 1. 3'·5 직접 조회 예외 — 올리지 않고 사유를 남긴다 ─────────────────
async def test_node_live_error_marks_node_unchecked_and_logs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (_FakeProbe(), None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    caplog.set_level(logging.INFO, logger=lk.__name__)
    link = (await _attach(_cfg(), _event(), redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved" and link["pod"] is True
    assert link["reason"] == hm.REASON_PENDING  # 사유 코드는 기존 그대로
    assert link["node_address_status"] == NODE_UNCHECKED
    warn = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("직접 조회 실패" in m and "alarm_id=a1" in m and "step=3'" in m
               and "error=RuntimeError" in m for m in warn)


async def test_node_live_error_then_workload_hit_still_links(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3'이 실패해도 다음 순(4 — workload)으로 진행한다."""
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (_FakeProbe(), None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    redis = _Redis()
    await _cache_with(redis, _row("workload", "bfm-kbland-admin-price", rid=31,
                                  servers=("W-01",)))
    link = (await _attach(_cfg(), _event(), redis=redis))["polestar_link"]
    assert link["status"] == "linked" and link["map_id"] == 31


async def test_vm_live_error_logs_step_five(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(hc, "_default_probes", lambda app_config: (_FakeProbe(), None))
    monkeypatch.setattr(hc, "node_lookup_db_ids", _boom)
    caplog.set_level(logging.INFO, logger=lk.__name__)
    apm = {"source_id": "jen", "domain_id": 7, "instance_name": "WAS01", "pod": False}
    event = _event(apm, resource_name="WAS01", hostname="was01", ip_address="192.0.2.41")
    link = (await _attach(_cfg(), event, redis=_Redis()))["polestar_link"]
    assert link["status"] == "unresolved" and "pod" not in link
    warn = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("직접 조회 실패" in m and "step=5" in m for m in warn)


# ── 2. ITAM 요청 — `next_retry_at` ─────────────────────────────────────
def _itam_repo() -> FakeRepo:
    repo = FakeRepo(extra_columns={"itam_snapshot_server": ("clue_text",)})
    repo.set_snapshot("itam_snapshot_app", "r0", [{"app_code": "STL", "app_name": "결제정산"}])
    repo.set_snapshot("itam_snapshot_server", "r0", [
        {"hostname": "nohost", "ips": ["10.2.0.99"], "active": None, "clue_text": ""}])
    repo.set_snapshot("sms_inventory", "r0", [
        {"db_id": "zone_b", "resource_id": "4", "server_name": "stlbat01",
         "hostname": "stlbat01", "ip": "10.2.0.9", "nic_ips": []}])
    return repo


def _gateway() -> FakeGateway:
    return FakeGateway({"apm_instance_map": lambda args: {"rows": [
        apm_row("s1", "30", "b1", "settle-batch", domain_name="결제정산")]}})


async def test_itam_request_before_retry_returns_retry_wait() -> None:
    repo, cache, gateway = _itam_repo(), FakeCache(), _gateway()
    retry = NOW + timedelta(hours=3)
    repo.seed("s1", "30", "domain", "", state="failed", basis="itam",
              next_retry_at=retry.isoformat())
    msg = hm.itam_request_message(source_id="s1", domain_id="30", domain_name="결제정산")
    out = await handle_itam_request(msg, repo=repo, cache=cache, gateway_client=gateway, now=NOW)
    assert out == {"status": "retry_wait", "next_retry_at": retry.isoformat()}
    assert gateway.calls == [] and cache.put_rows == []


@pytest.mark.parametrize("retry", [NOW - timedelta(minutes=1), None])
async def test_itam_request_after_retry_or_without_row_proceeds(retry: Any) -> None:
    repo, cache, gateway = _itam_repo(), FakeCache(), _gateway()
    if retry is not None:
        repo.seed("s1", "30", "domain", "", state="failed", basis="itam",
                  next_retry_at=retry.isoformat())
    msg = hm.itam_request_message(source_id="s1", domain_id="30", domain_name="결제정산")
    out = await handle_itam_request(msg, repo=repo, cache=cache, gateway_client=gateway, now=NOW)
    assert out["status"] == "ok" and len(gateway.calls) == 1


async def test_itam_request_retry_wait_is_acked_by_consumer() -> None:
    """재시도 대기도 소비자가 ack한다(다시 읽지 않는다)."""
    repo, gateway = _itam_repo(), _gateway()
    repo.seed("s1", "30", "domain", "", state="ambiguous", basis="itam",
              next_retry_at=(NOW + timedelta(days=3650)).isoformat())
    redis = _StreamRedis()
    consumer = cons.ApmHostMapConsumer(
        _Repo(), FakeCache(), redis=redis, consumer="t",  # type: ignore[arg-type]
        allowed_db_ids=lambda: list(SOURCES),
    )
    results: list[dict[str, Any]] = []

    async def _handler(msg: dict[str, Any]) -> None:
        results.append(await handle_itam_request(msg, repo=repo, cache=FakeCache(),
                                                 gateway_client=gateway))

    consumer.set_itam_handler(_handler)
    await consumer.ensure_group()
    await cm.publish_request(redis, hm.itam_request_message(source_id="s1", domain_id="30"))
    assert await consumer.consume_once() == 1
    assert results[0]["status"] == "retry_wait" and len(redis.acked) == 1
    assert gateway.calls == []


# ── 3. 스트림 메시지 검증 ──────────────────────────────────────────────
def _consumer(redis: _StreamRedis, repo: _Repo, **kw: Any) -> cons.ApmHostMapConsumer:
    kw.setdefault("allowed_db_ids", lambda: list(SOURCES))
    return cons.ApmHostMapConsumer(
        repo, FakeCache(), redis=redis, consumer="t", **kw  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(("source_id", "domain_id"), [
    ("", "7"), ("jen", ""), ("a:b", "7"), ("jen", "7|x"), ("je|n", "7"),
])
async def test_learn_with_bad_key_is_rejected_without_write(
    source_id: str, domain_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo, allowed_db_ids=_boom)  # 키 검사가 먼저다
    await consumer.ensure_group()
    await cm.publish_request(redis, {
        "type": hm.MSG_LEARN, "source_id": source_id, "domain_id": domain_id,
        "instance_name": POD, "basis": hm.BASIS_NODE_ADDRESS,
        "targets": [hm.make_target(db_id="polestar_cm_gp", server_name="N")],
    })
    caplog.set_level(logging.WARNING, logger=cons.__name__)
    assert await consumer.consume_once() == 1
    assert repo.rows == {} and repo.get_calls == [] and len(redis.acked) == 1
    assert any("매핑 키 규칙 밖" in r.getMessage() for r in caplog.records)


async def test_learn_with_foreign_db_id_is_rejected_without_write(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    await cm.publish_request(redis, hm.learn_message(
        source_id="jen", domain_id="7", instance_name=POD, basis=hm.BASIS_NODE_ADDRESS,
        targets=[hm.make_target(db_id="polestar_cm_gp", server_name="N"),
                 hm.make_target(db_id="evil_db", server_name="X")],
    ))
    caplog.set_level(logging.WARNING, logger=cons.__name__)
    assert await consumer.consume_once() == 1
    assert repo.rows == {} and len(redis.acked) == 1
    assert any("조회 소스 밖 db_id" in r.getMessage() and "evil_db" in r.getMessage()
               for r in caplog.records)


async def test_learn_with_allowed_db_ids_is_written() -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.ensure_group()
    await cm.publish_request(redis, hm.learn_message(
        source_id="jen", domain_id="7", instance_name=POD, basis=hm.BASIS_NODE_ADDRESS,
        targets=[hm.make_target(db_id="polestar_cm_gp", server_name="N")],
    ))
    assert await consumer.consume_once() == 1
    assert ("jen", "7", "instance", POD) in repo.rows


def test_default_allowed_db_ids_reads_node_lookup_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기본 허용 목록은 API 프로세스 설정(`load_config`)의 노드 주소 조회 소스다."""
    import src.config as config_mod

    sentinel = object()
    seen: list[Any] = []
    monkeypatch.setattr(config_mod, "load_config", lambda: sentinel)
    monkeypatch.setattr(hc, "node_lookup_db_ids",
                        lambda cfg: seen.append(cfg) or ["polestar_b0"])
    assert cons._default_allowed_db_ids() == ["polestar_b0"] and seen == [sentinel]


async def test_touch_ids_are_capped(caplog: pytest.LogCaptureFixture) -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    caplog.set_level(logging.WARNING, logger=cons.__name__)
    await consumer.handle({"type": hm.MSG_TOUCH, "source_id": "jen",
                           "ids": list(range(1, cons.MAX_TOUCH_IDS + 51))})
    await consumer.flush_touches(force=True)
    assert repo.touched == [list(range(1, cons.MAX_TOUCH_IDS + 1))]
    assert any("상한 초과" in r.getMessage() for r in caplog.records)


async def test_touch_ids_not_a_list_are_ignored() -> None:
    redis, repo = _StreamRedis(), _Repo()
    consumer = _consumer(redis, repo)
    await consumer.handle({"type": hm.MSG_TOUCH, "source_id": "jen", "ids": "123"})
    assert await consumer.flush_touches(force=True) == 0 and repo.touched == []


# ── 6. 플래그 on — 저장 확정 연결과 다르면 `previous_link` ──────────────
async def _save_link(redis: _Redis, server: str = "SAVED-01") -> None:
    await HostLinkStore(redis).save(
        "jen", POD, kind="link", confirmed=True, by="op", by_role="operator",
        db_id="polestar_b0", server_name=server, polestar_hostname=server.lower(),
    )


def _node(name: str, ip: str) -> list[dict[str, Any]]:
    return [{"name": name, "hostname": name.lower(), "ipaddress": ip}]


async def test_flag_on_node_link_differs_from_stored_adds_previous_link(
    probe: _FakeProbe,
) -> None:
    probe.table["polestar_cm_gp"] = _node("NODE-01", NODE_IP)
    redis = _Redis()
    await _save_link(redis)
    link = (await _attach(_cfg(host_link=True), _event(), redis=redis))["polestar_link"]
    assert link["status"] == "linked" and link["server_name"] == "NODE-01"
    assert link["previous_link"] == {"db_id": "polestar_b0", "server_name": "SAVED-01"}


async def test_flag_on_map_hit_differs_from_stored_has_no_previous_link(
    probe: _FakeProbe,
) -> None:
    """매핑 hit는 매핑이 정본 — 「노드 주소 우선」 문장이 맞지 않아 `previous_link`를 안 단다."""
    redis = _Redis()
    await _save_link(redis)
    await _cache_with(redis, _row("instance", POD, rid=41, origin="manual",
                                  servers=("ADMIN-01",)))
    link = (await _attach(_cfg(host_link=True), _event(), redis=redis))["polestar_link"]
    assert link["map_id"] == 41
    assert "previous_link" not in link


async def test_flag_on_same_server_or_stored_itself_has_no_previous_link(
    probe: _FakeProbe,
) -> None:
    redis = _Redis()
    await _save_link(redis, server="NODE-01")
    probe.table["polestar_b0"] = _node("NODE-01", NODE_IP)
    link = (await _attach(_cfg(host_link=True), _event(), redis=redis))["polestar_link"]
    assert link["server_name"] == "NODE-01" and "previous_link" not in link
    # 6순(저장 연결 자체)은 비교 대상이 자기 자신이다
    event = _event(_apm(host_address="", host_address_status="not_found"))
    link = (await _attach(_cfg(host_link=True), event, redis=redis))["polestar_link"]
    assert link["server_name"] == "NODE-01" and "previous_link" not in link


async def test_flag_on_host_link_off_never_reads_stored(probe: _FakeProbe) -> None:
    probe.table["polestar_cm_gp"] = _node("NODE-01", NODE_IP)
    redis = _Redis()
    await _save_link(redis)
    link = (await _attach(_cfg(host_link=False), _event(), redis=redis))["polestar_link"]
    assert link["server_name"] == "NODE-01" and "previous_link" not in link
