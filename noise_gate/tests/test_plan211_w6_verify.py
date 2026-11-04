"""plans/211 W6 독립 검증(verifier) — 이음매·결정 위배·예산 경합.

가짜 입력 금지(docs/18 2026-11-03 plans/210 팀 리드 항목): 생성기(연계 요청·알람 3''순)의
실제 출력을 실제 소비자(Redis 사본 `RedisApmHostMapCache` → `resolve_map_link` · 되적재 소비자
`ApmHostMapConsumer`)에 그대로 넣는다. 게이트웨이·폴스타 프로브만 가짜다.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from noise_gate.application import apm_host_map_consumer as cons
from noise_gate.application import apm_host_map_lookup as lk
from noise_gate.application.apm_host_map_scan import handle_link_request
from noise_gate.domain import apm_host_map as hm
from noise_gate.infrastructure import apm_gateway_client as gw
from noise_gate.infrastructure import apm_host_map_cache as cm
from noise_gate.tests.test_plan210_w3_fast_path import (
    NODE_IP,
    POD,
    SOURCES,
    WORKLOAD,
    _apm,
    _cfg,
    _event,
    _FakeProbe,
    _Redis,
)

from .plan210_w5_fakes import NOW, FakeCache, FakeGateway, FakeRepo, apm_row, node_row

# test_plan211_w6_alarm 의 `probe` 픽스처(폴스타 프로브 가짜)를 그대로 쓴다
from .test_plan211_w6_alarm import _FakeGateway, _node_result, probe  # noqa: F401

INSTANCE_ID = "4242"
VM = "batch01"
HINTS = {"source_id": "jen", "domain_id": 7}
SMS = [{"db_id": "polestar_cm_gp", "resource_id": "9", "server_name": "NODE-01",
        "hostname": "node-01", "ip": NODE_IP, "nic_ips": []}]


def _link_repo() -> FakeRepo:
    repo = FakeRepo()
    repo.set_snapshot("sms_inventory", "r0", SMS)
    return repo


def _link_gateway(*instances: dict[str, Any]) -> FakeGateway:
    return FakeGateway({
        "apm_instance_map": lambda args: {"rows": list(instances)},
        "apm_node_addresses": lambda args: {
            "rows": [node_row("jen", "7", INSTANCE_ID, NODE_IP)],
            "node_address_sources": [{"source_id": "jen", "status": "ok"}],
        },
    })


def _vm_event() -> Any:
    """같은 도메인의 다른 인스턴스(VM · IP·호스트명 없음 · instance_id 없음 → 3''·5순 대상 아님)."""
    return _event(_apm(instance_name=VM, pod=False, host_address=None,
                       host_address_status=None),
                  server_name=VM, hostname="", resource_name=VM)


# ── 이음매 ① — 연계 요청이 실제로 쓴 행 → 실 사본 → 실 `resolve_map_link` ─────────────
async def test_seam_link_rows_through_real_cache_to_alarm_lookup(probe: _FakeProbe) -> None:  # noqa: F811
    """A의 쓰기 → `cache_value` → 실 `decide_from_map`(사본 HMGET): 2순 hit · 같은 도메인 다른
    인스턴스는 7순 사유 `apm_map_pending`(대체된 failed domain 행은 `apm_map_failed`가 아님)."""
    redis = _Redis()
    cache = cm.RedisApmHostMapCache(redis)
    repo = _link_repo()
    old = repo.seed("jen", "7", "domain", "", state="failed", basis="itam",
                    evidence={"reason": "no_sms_match"}, version=1,
                    next_retry_at=(NOW + timedelta(hours=20)).isoformat())
    assert await cache.put(old)  # 기동 동기화로 사본에 이미 있던 failed domain 행
    gateway = _link_gateway(apm_row("jen", "7", INSTANCE_ID, POD, domain_name="KB"),
                            apm_row("jen", "7", "5000", VM, domain_name="KB"))
    msg = hm.itam_request_message(source_id="jen", domain_id="7", domain_name="KB")

    # 대조 — 연계 요청 전: 같은 VM 인스턴스는 7순 apm_map_failed
    before = await lk.resolve_map_link(_cfg(), _vm_event(), HINTS, cache=cache, redis=_Redis())
    assert before["status"] == "unresolved" and before["reason"] == hm.REASON_FAILED

    out = await handle_link_request(msg, repo=repo, cache=cache, gateway_client=gateway, now=NOW)
    assert out["status"] == "ok" and out["linked"] == 1
    assert out["tried"]["itam"] == "skipped:linked"

    # 다음 알람 — 이은 파드는 2순 hit(실시간 조회 0)
    hit = await lk.resolve_map_link(_cfg(), _event(), HINTS, cache=cache, redis=_Redis())
    assert hit["status"] == "linked" and hit["basis"] == hm.HINT_BASIS
    assert hit["map_scope"] == "instance" and hit["server_name"] == "NODE-01"
    assert probe.calls == []

    # 같은 도메인 다른 인스턴스 — 7순 사유가 apm_map_failed가 아니라 apm_map_pending
    after = await lk.resolve_map_link(_cfg(), _vm_event(), HINTS, cache=cache, redis=_Redis())
    assert after["status"] == "unresolved" and after["reason"] == hm.REASON_PENDING
    # 정본 domain 행은 지우지 않고 표지만(원래 근거 유지 · version 1회 증가)
    domain = repo.row("jen", "7", "domain", "")
    assert domain is not None and domain["state"] == "failed"
    assert domain["evidence"]["reason"] == "no_sms_match"
    assert domain["evidence"]["superseded"]["bases"] == ["node_address"]
    assert domain["version"] == 2


# ── 이음매 ② — 알람 3''순 learn → 실 소비자(valid_key·db_id 검증) → 다음 알람 2순 ─────────
async def test_seam_alarm_learn_is_accepted_by_consumer_and_hits_next(
    probe: _FakeProbe,  # noqa: F811
) -> None:
    redis = _Redis()
    cache = cm.RedisApmHostMapCache(redis)
    await cache.rebuild_sms_index(
        {NODE_IP: [hm.make_target(db_id="polestar_b0", server_name="NODE-B", resource_id="9")]},
        {},
    )
    fake = _FakeGateway(_node_result())
    event = _event(_apm(host_address=None, host_address_status="not_found",
                        instance_id=int(INSTANCE_ID)))
    first = await lk.resolve_map_link(_cfg(), event, HINTS, cache=cache, redis=redis,
                                      gateway_client=fake)
    assert first["status"] == "linked" and len(fake.calls) == 1
    [learn] = [m for m in redis.requests() if m["type"] == hm.MSG_LEARN]

    repo = FakeRepo()
    consumer = cons.ApmHostMapConsumer(
        repo, cache, redis=redis, consumer="verify",  # type: ignore[arg-type]
        allowed_db_ids=lambda: list(SOURCES),
    )
    await consumer.handle(learn)
    inst = repo.row("jen", "7", "instance", hm.instance_match_key(POD))
    assert inst and (inst["basis"], inst["state"]) == ("node_address", "active")
    assert inst["evidence"]["node_address_from"] == lk.NODE_LOOKUP_STEP
    workload = repo.row("jen", "7", "workload", WORKLOAD)
    assert workload and workload["state"] == "active"

    # 소비자가 사본에 반영 → 같은 파드 다음 알람은 2순 hit · 게이트웨이 재호출 0
    again = await lk.resolve_map_link(_cfg(), event, HINTS, cache=cache, redis=_Redis(),
                                      gateway_client=fake)
    assert again["status"] == "linked" and again["map_scope"] == "instance"
    assert len(fake.calls) == 1

    # 조회 소스 밖 db_id면 같은 learn이 쓰기 0으로 거부된다(D-362 부기 1 ⑭)
    repo2 = FakeRepo()
    strict = cons.ApmHostMapConsumer(
        repo2, FakeCache(), redis=redis, consumer="verify2",  # type: ignore[arg-type]
        allowed_db_ids=lambda: ["polestar_cm_gp"],
    )
    await strict.handle(learn)
    assert repo2.rows == {}


# ── 결정 위배 — 직접 조회 결과 2대 이상은 고르지 않는다(D-224 ④) ─────────────────────
async def test_live_ambiguous_results_are_not_linked_and_itam_still_runs() -> None:
    """N3 직접 조회가 `ambiguous`, V 직접 조회가 2대면 active로 잇지 않고 I(ITAM) 단계로 간다."""

    async def _node(ip: str) -> dict[str, Any]:
        return {"status": "ambiguous", "reason": "multiple_servers"}

    async def _vm(ip: str, host: str) -> Any:
        both = tuple(hm.make_target(db_id="polestar_cm_gp", server_name=n, ip=ip)
                     for n in ("vm-a", "vm-b"))
        return hm.VmMatch("ambiguous", both), []

    repo = FakeRepo()  # SMS 스냅샷 없음 → N3·V 직접 조회로
    gateway = FakeGateway({
        "apm_instance_map": lambda args: {"rows": [
            apm_row("jen", "7", INSTANCE_ID, POD, domain_name="KB"),
            apm_row("jen", "7", "5000", VM, ip="10.7.0.1", domain_name="KB")]},
        "apm_node_addresses": lambda args: {"rows": [node_row("jen", "7", INSTANCE_ID, NODE_IP)]},
    })
    msg = hm.itam_request_message(source_id="jen", domain_id="7")
    out = await handle_link_request(msg, repo=repo, cache=FakeCache(), gateway_client=gateway,
                                    node_link=_node, vm_link=_vm, now=NOW)
    assert out["linked"] == 0
    assert repo.row("jen", "7", "instance", hm.instance_match_key(POD)) is None
    vm_row = repo.row("jen", "7", "instance", VM)
    assert vm_row is not None and vm_row["state"] == "ambiguous"  # 고르지 않음
    assert out["tried"]["itam"] == "skipped:no_inventory"  # 이은 것이 없어 I 단계까지 갔다


# ── 예산 경합 — 3''순 wait_seconds 대 클라이언트 호출 상한(docs/18 W9 · D-362 부기 3 ⑲) ──────
async def test_alarm_wait_seconds_is_capped_by_client_call_timeout(
    probe: _FakeProbe, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    client = gw.ApmGatewayClient("http://127.0.0.1:1/sse")
    seen: list[dict[str, Any]] = []

    async def _call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append(arguments)
        return _node_result()

    monkeypatch.setattr(client, "_call", _call)
    event = _event(_apm(host_address=None, host_address_status="not_found",
                        instance_id=int(INSTANCE_ID)))
    await lk.resolve_map_link(_cfg(budget=10.0), event, HINTS, cache=None, redis=_Redis(),
                              gateway_client=client)
    [args] = seen
    assert args["wait_seconds"] <= client._call_timeout - lk.NODE_LOOKUP_CONNECT_MARGIN


# ── 관리자 행 반복 쓰기 — 알람 miss마다 다른 인스턴스 때문에 관리자 행이 다시 쓰인다 ───────
async def test_repeated_link_requests_do_not_rewrite_admin_row() -> None:
    repo = _link_repo()
    manual = repo.seed("jen", "7", "workload", WORKLOAD, origin="manual", basis="manual",
                       state="active", version=1,
                       targets=[hm.make_target(db_id="polestar_cm_gp", server_name="OTHER-01")])
    gateway = _link_gateway(apm_row("jen", "7", INSTANCE_ID, POD, domain_name="KB"),
                            apm_row("jen", "7", "5000", VM, domain_name="KB"))
    msg = hm.itam_request_message(source_id="jen", domain_id="7")
    await handle_link_request(msg, repo=repo, cache=FakeCache(), gateway_client=gateway, now=NOW)
    first = dict(manual["evidence"])
    out = await handle_link_request(msg, repo=repo, cache=FakeCache(), gateway_client=gateway,
                                    now=NOW + timedelta(minutes=10))
    actions = {(r["scope"], r["match_key"]): r["action"] for r in out["rows"]}
    assert actions.get(("workload", WORKLOAD)) != hm.WRITE_SUGGEST
    assert manual["evidence"] == first
