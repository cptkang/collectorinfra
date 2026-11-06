"""plans/215 W10 — D2 알람 조사 페이로드 `meta.biz_service` (§4.8 · §13 수용 15 · §14 D2 벡터).

Redis는 `tests.mocks.async_redis.FakeAsyncRedis`에 `hmget`·`sadd`(파이프라인 큐잉)만 덧붙인 하위
클래스다(공용 페이크는 고치지 않는다). 존 해석은 레지스트리 대신 대역 함수로 고정한다.
세 경로(트리거 노드 · 사건 종합 · 수동 분석)의 플래그 off 바이트 동일 · 상한·`truncated` · 존 필터 ·
고르는 순서 · `polestar_link` 불변 · Redis 실패 키 없음을 본다.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis, FakePipeline

from noise_gate.application import biz_investigation_context as bic
from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, match_key_for
from noise_gate.domain.biz_service_context import BizTarget, build_biz_service_context
from noise_gate.domain.biz_service_keys import apm_instance_member_key
from noise_gate.domain.notification_policy import NotificationDecision
from noise_gate.infrastructure.biz_service_cache import RedisBizServiceCache

ZONES = {"polestar_cm_gp": "gongjon", "polestar_b0": "bankjon", "jennifer_gp": "gongjon"}


class _Pipe(FakePipeline):
    _QUEUEABLE = FakePipeline._QUEUEABLE | {"sadd"}


class _Redis(FakeAsyncRedis):
    async def hmget(self, name: str, keys: list[str], *args: str) -> list[str | None]:
        h = self._hash(name) or {}
        return [h.get(k) for k in [*keys, *args]]

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> _Pipe:
        return _Pipe(self)


class _BrokenRedis(_Redis):
    async def hmget(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis down")

    async def hget(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis down")

    async def get(self, *a: Any, **k: Any) -> Any:
        raise ConnectionError("redis down")


class _Repo:
    def __init__(self, services: list[dict[str, Any]]) -> None:
        self.services = services

    async def active_services_with_members(self) -> list[dict[str, Any]]:
        return self.services


@pytest.fixture(autouse=True)
def _zones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bic, "_db_zone", lambda db_id: ZONES.get(db_id, ""))
    monkeypatch.setattr(bic, "_apm_source_zone", lambda source_id: "gongjon")


def _sms(rid: int, host: str, *, db: str = "polestar_cm_gp", zone: str = "gongjon",
         prefix: str = "", conf: str = "confirmed") -> dict[str, Any]:
    return {"kind": "sms_server", "member_key": f"{db}|{rid}", "db_id": db,
            "resource_id": str(rid), "server_name": host.upper(), "hostname": host,
            "zone": zone, "prefix_code": prefix, "confidence": conf}


def _inst(name: str, *, src: str = "gp", dom: str = "7", conf: str = "confirmed",
          zone: str = "") -> dict[str, Any]:
    key = apm_instance_member_key(src, dom, SCOPE_INSTANCE, match_key_for(SCOPE_INSTANCE, name))
    return {"kind": "apm_instance", "member_key": key, "source_id": src, "domain_id": dom,
            "instance_name": name, "confidence": conf, "zone": zone}


def _service(n_sms: int = 15, n_apm: int = 12) -> dict[str, Any]:
    """서비스 S — `ibkweb01`(대상 후보) 포함 SMS n대 · APM 인스턴스 n개."""
    sms = [_sms(1, "ibkweb01", prefix="ibk")] + [
        _sms(100 + i, f"ibksvr{i:02d}", prefix="ibk" if i % 2 else "etc")
        for i in range(1, n_sms)
    ]
    apm = [_inst(f"ibk-was-{i:02d}") for i in range(1, n_apm + 1)]
    return {"id": 7, "service_key": "ibk", "display_name": "인터넷뱅킹", "state": "active",
            "members": sms + apm}


async def _cache(
    services: list[dict[str, Any]] | None = None,
) -> tuple[RedisBizServiceCache, _Redis]:
    redis = _Redis()
    cache = RedisBizServiceCache(redis)
    await cache.sync_from_repo(_Repo([_service()] if services is None else services))
    return cache, redis


def _cfg(on: bool = True, **gate: Any) -> SimpleNamespace:
    ng = dict(
        biz_investigation_context_active=on, biz_investigation_max_sms=10,
        biz_investigation_max_apm=8, investigation_trigger_enabled=True,
        investigation_trigger_min_tier="PAGE", investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
    )
    ng.update(gate)
    return SimpleNamespace(
        noise_gate=SimpleNamespace(**ng),
        host_authz=SimpleNamespace(mode="admin_only"),
        composite=SimpleNamespace(prior_targets_enabled=False, availability_precheck_enabled=False),
    )


def _event(host: str = "ibkweb01", db_id: str = "polestar_cm_gp") -> AlarmEvent:
    return AlarmEvent(
        db_id=db_id, server_name=host.upper(), hostname=host, ip_address="",
        resource_ancestry="", alarm_id="a-1", severity=3, alarm_status="NOT_ACK",
        resource_type="server.Server", resource_name=host, alarm_name="CPU 사용률",
        alarm_time=datetime(2026, 11, 5, 10, 0, 0), conditions="cpu>90", condition_log="95",
    )


def _decision() -> NotificationDecision:
    return NotificationDecision(
        tier="page", reason="r", priority=3, signals={}, fingerprint="fp-1", stage="matrix",
    )


class _Client:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(copy.deepcopy(payload))
        return {"investigation_id": f"inv-{len(self.payloads)}", "status": "accepted"}

    async def poll(self, inv_id: str) -> dict[str, Any]:
        return {"status": "done", "briefing": {"summary": "b"}, "verdict": None}


def _dump(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


# ── 순수 빌더(§14 D2 벡터) ─────────────────────────────────────────────────
def _build(service: dict[str, Any], **kw: Any) -> dict[str, Any] | None:
    target = kw.pop("target", BizTarget(hostname="ibkweb01", db_id="polestar_cm_gp"))
    opts: dict[str, Any] = {"allowed_zones": None, "max_sms": 10, "max_apm": 8, **kw}
    return build_biz_service_context(service, target, **opts)


def test_vector_caps_and_truncated() -> None:
    """`ibkweb01` 알람 · SMS 15대 · APM 12개 → sms 10(대상 제외) · apm 8 · truncated."""
    out = _build(_service())
    assert out is not None
    assert out["service_id"] == 7 and out["display_name"] == "인터넷뱅킹"
    assert out["confidence"] == "confirmed"
    assert len(out["sms_members"]) == 10 and len(out["apm_members"]) == 8
    assert out["truncated"] is True
    assert "ibkweb01" not in {m["hostname"] for m in out["sms_members"]}
    assert "d1_verdict" not in out
    assert set(out) == {"service_id", "display_name", "confidence", "sms_members",
                        "apm_members", "truncated"}
    assert set(out["sms_members"][0]) == {"db_id", "server_name", "hostname", "confidence"}
    assert set(out["apm_members"][0]) == {"source_id", "domain_id", "instance_name",
                                          "confidence"}


def test_under_caps_not_truncated() -> None:
    out = _build(_service(n_sms=4, n_apm=3))
    assert out is not None and out["truncated"] is False
    assert len(out["sms_members"]) == 3 and len(out["apm_members"]) == 3


def test_vector_zone_filter_excludes_other_zone() -> None:
    """존 A 사용자 수동 조사 · 구성원 일부 존 B → 존 B 구성원 제외(건수도 싣지 않음)."""
    svc = _service(n_sms=1, n_apm=0)
    svc["members"] += [_sms(201, "ibkgp01"), _sms(202, "ibkb001", db="polestar_b0", zone="bankjon")]
    out = _build(svc, allowed_zones={"gongjon"})
    assert out is not None
    assert [m["hostname"] for m in out["sms_members"]] == ["ibkgp01"]
    assert "bankjon" not in _dump(out) and "ibkb001" not in _dump(out)


def test_order_d1_then_prefix_then_ids() -> None:
    svc = {"id": 3, "display_name": "S", "members": [
        _sms(1, "ibkweb01", prefix="ibk"),
        _sms(30, "etc30", prefix="etc"), _sms(9, "etc09", prefix="etc"),
        _sms(20, "ibk20", prefix="ibk"), _sms(10, "ibk10", prefix="ibk"),
        _inst("b-was", dom="8"), _inst("a-was", dom="8"), _inst("z-was", dom="7"),
    ]}
    d1 = {"verdict": "R3", "anomalous_members": [
        {"kind": "sms_server", "member_key": "polestar_cm_gp|30"},
        {"kind": "apm_instance", "member_key": _inst("b-was", dom="8")["member_key"]},
    ], "computed_at": "2026-11-05T10:00:00"}
    out = _build(svc, d1=d1)
    assert out is not None and out["d1_verdict"] == "R3"
    # D1 이상(30) → 같은 접두 코드(10 · 20 — 수치 순) → 나머지(9)
    assert [m["hostname"] for m in out["sms_members"]] == ["etc30", "ibk10", "ibk20", "etc09"]
    # APM: D1 이상(b-was) → 나머지 (source, domain, instance) 순(도메인 7 → 8)
    assert [m["instance_name"] for m in out["apm_members"]] == ["b-was", "z-was", "a-was"]


def test_apm_target_same_domain_first_and_self_excluded() -> None:
    svc = {"id": 4, "display_name": "S", "members": [
        _inst("ibk-was-01", dom="7"), _inst("a-was", dom="9"), _inst("c-was", dom="7"),
        _sms(5, "ibkwas01", conf="inferred"),
    ]}
    target = BizTarget(sms_keys=frozenset({"polestar_cm_gp|5"}), hostname="ibkwas01",
                       apm=("gp", "7", "ibk-was-01"))
    out = _build(svc, target=target)
    assert out is not None
    assert [m["instance_name"] for m in out["apm_members"]] == ["c-was", "a-was"]
    assert out["sms_members"] == []  # 210 연계 대상(알람 서버) 자신은 뺀다
    assert out["confidence"] == "confirmed"  # 대상 APM 구성원이 확정


def test_inferred_target_and_invalid_d1_ignored() -> None:
    svc = _service(n_sms=3, n_apm=0)
    svc["members"][0]["confidence"] = "inferred"
    svc["members"][2]["confidence"] = "inferred"
    out = _build(svc, d1={"verdict": "정상", "anomalous_members": "x"})
    assert out is not None and out["confidence"] == "inferred"
    assert "d1_verdict" not in out
    assert [m["confidence"] for m in out["sms_members"]] == ["confirmed", "inferred"]


def test_missing_service_id_is_none() -> None:
    assert _build({"display_name": "x", "members": []}) is None


# ── 트리거 노드(자동 경로) ─────────────────────────────────────────────────
async def _node_payload(cfg: SimpleNamespace, redis: Any, event: AlarmEvent) -> dict[str, Any]:
    client = _Client()
    state = {"alarm_event": event, "notification_decision": _decision()}
    await trig.investigation_trigger_node(state, {"configurable": {
        "app_config": cfg, "sre_agent_client": client, "history_redis": redis,
    }})
    assert len(client.payloads) == 1
    return client.payloads[0]


async def test_trigger_node_attaches_member_context_in_alarm_zone() -> None:
    svc = _service(n_sms=3, n_apm=2)
    svc["members"].append(_sms(300, "ibkb0x", db="polestar_b0", zone="bankjon"))
    _, redis = await _cache([svc])
    payload = await _node_payload(_cfg(), redis, _event())
    assert payload["contract_version"] == "1"
    biz = payload["meta"]["biz_service"]
    assert biz["service_id"] == 7 and biz["truncated"] is False
    assert [m["hostname"] for m in biz["sms_members"]] == ["ibksvr01", "ibksvr02"]
    assert "ibkb0x" not in _dump(biz)  # 알람 존(공동존) 밖 구성원은 싣지 않는다
    assert len(biz["apm_members"]) == 2


async def test_trigger_node_bit_identical_off_nonmember_and_no_redis() -> None:
    _, redis = await _cache()
    base = _dump(await _node_payload(_cfg(on=False), None, _event()))
    assert _dump(await _node_payload(_cfg(on=False), redis, _event())) == base
    # 서비스 미소속 서버 알람 → 키 없음(종전과 동일)
    other = _dump(await _node_payload(_cfg(on=False), None, _event("lonely01")))
    assert _dump(await _node_payload(_cfg(), redis, _event("lonely01"))) == other
    # Redis 없음 · Redis 실패 → 키 없음
    assert _dump(await _node_payload(_cfg(), None, _event())) == base
    assert _dump(await _node_payload(_cfg(), _BrokenRedis(), _event())) == base


# ── 사건 종합 ──────────────────────────────────────────────────────────────
async def _episode_payload(cfg: SimpleNamespace, redis: Any) -> dict[str, Any]:
    client = _Client()
    out = await trig.submit_episode_investigation(
        client, _event(), _decision(), {"episode_id": "ep-1", "members": []}, ["a-1"],
        cfg=cfg, redis=redis,
    )
    assert out["status"] == "submitted"
    return client.payloads[0]


async def test_episode_attaches_and_off_bit_identical() -> None:
    _, redis = await _cache()
    off = await _episode_payload(_cfg(on=False), redis)
    on = await _episode_payload(_cfg(), redis)
    assert "biz_service" not in off["meta"]
    assert len(on["meta"]["biz_service"]["sms_members"]) == 10
    stripped = {**on, "meta": {k: v for k, v in on["meta"].items() if k != "biz_service"}}
    assert _dump(stripped) == _dump(off)  # 가산 키만 다르다


# ── 수동 분석 ──────────────────────────────────────────────────────────────
async def _manual(cfg: SimpleNamespace, **kw: Any) -> dict[str, Any]:
    decision = SimpleNamespace(tier="page", reason="r", fingerprint="fp", signals={})
    return await trig.build_manual_payload(
        cfg, _event(), decision, fingerprint="manual:a-1:x", investigation_kind="manual", **kw
    )


async def test_manual_requester_zone_filter_and_bit_identical() -> None:
    svc = _service(n_sms=2, n_apm=0)
    svc["members"].append(_sms(300, "ibkb0x", db="polestar_b0", zone="bankjon"))
    cache, _ = await _cache([svc])
    base = _dump(await _manual(_cfg()))
    assert _dump(await _manual(_cfg(on=False), biz_service_cache=cache)) == base
    assert _dump(await _manual(_cfg())) == base  # 사본 미주입(플래그 off 기동) → 키 없음
    zone_a = (await _manual(_cfg(), biz_service_cache=cache, biz_zones={"gongjon"}))["meta"]
    assert [m["hostname"] for m in zone_a["biz_service"]["sms_members"]] == ["ibksvr01"]
    every = (await _manual(_cfg(), biz_service_cache=cache, biz_zones=None))["meta"]
    assert [m["hostname"] for m in every["biz_service"]["sms_members"]] == ["ibksvr01", "ibkb0x"]


# ── APM 알람 · polestar_link 불변 ──────────────────────────────────────────
async def test_apm_alarm_polestar_link_untouched_and_targets_excluded() -> None:
    svc = {"id": 9, "display_name": "인터넷뱅킹", "state": "active", "members": [
        _sms(1, "ibkwas01"), _sms(2, "ibkwas02"), _inst("ibk-was-01"), _inst("ibk-was-02"),
    ]}
    cache, _ = await _cache([svc])
    link = {"status": "linked", "db_id": "polestar_cm_gp", "server_name": "IBKWAS01",
            "hostname": "ibkwas01", "basis": "apm_map", "resource_id": "1"}
    payload = {"contract_version": "1", "meta": {"hints": {
        "solution": "apm", "source_id": "gp", "domain_id": "7", "polestar_link": link,
    }}}
    before = _dump(payload["meta"]["hints"])
    event = AlarmEvent(
        db_id="jennifer_gp", server_name="ibkwas01", hostname="ibkwas01", ip_address="",
        resource_ancestry="", alarm_id="j-1", severity=3, alarm_status="", alarm_name="was",
        resource_type="apm.Instance", resource_name="ibk-was-01",
        alarm_time=datetime(2026, 11, 5), conditions="", condition_log="",
        raw_payload={"apm": {"source_id": "gp", "domain_id": "7", "instance_name": "ibk-was-01"}},
    )
    await bic.attach_biz_service(_cfg(), event, payload, cache=cache)
    assert _dump(payload["meta"]["hints"]) == before  # polestar_link 바이트 불변(별도 키)
    biz = payload["meta"]["biz_service"]
    assert [m["hostname"] for m in biz["sms_members"]] == ["ibkwas02"]
    assert [m["instance_name"] for m in biz["apm_members"]] == ["ibk-was-02"]


async def test_d1_memo_read_orders_and_sets_verdict() -> None:
    cache, redis = await _cache([_service(n_sms=4, n_apm=0)])
    await redis.set("bizdiag:d1:7", json.dumps({
        "by_zone": {"gongjon": {"verdict": "R2", "anomalous_members": [
            {"kind": "sms_server", "member_key": "polestar_cm_gp|103"}]}},
        "computed_at": "2026-11-05T10:00:00",
    }))
    payload: dict[str, Any] = {"meta": {}}
    await bic.attach_biz_service(_cfg(), _event(), payload, cache=cache)
    biz = payload["meta"]["biz_service"]
    assert biz["d1_verdict"] == "R2"
    assert biz["sms_members"][0]["hostname"] == "ibksvr03"
    await redis.set("bizdiag:d1:7", "{broken")
    payload = {"meta": {}}
    await bic.attach_biz_service(_cfg(), _event(), payload, cache=cache)
    assert "d1_verdict" not in payload["meta"]["biz_service"]


# ── 교정(검증·보안 감사 지적) ──────────────────────────────────────────────
async def test_auto_path_unknown_alarm_zone_is_fail_closed() -> None:
    """자동 경로에서 알람 존을 못 풀면 구성원 0건 — 존 미상 구성원(zone "")도 싣지 않는다.

    서비스 식별(`service_id`·`display_name`)만 남는다(수동 경로 허용 존 0개와 같은 모양).
    """
    svc = {"id": 7, "display_name": "인터넷뱅킹", "state": "active", "members": [
        _sms(1, "ibkweb01", db="polestar", zone=""), _sms(2, "ibkweb02", db="polestar", zone=""),
        _inst("ibk-was-01", zone=""),
    ]}
    cache, _ = await _cache([svc])
    payload: dict[str, Any] = {"meta": {}}
    await bic.attach_biz_service(_cfg(), _event(db_id="polestar"), payload, cache=cache)
    biz = payload["meta"]["biz_service"]
    assert (biz["service_id"], biz["display_name"]) == (7, "인터넷뱅킹")
    assert biz["sms_members"] == [] and biz["apm_members"] == []
    assert biz["truncated"] is False
    assert "ibkweb02" not in _dump(biz)


def test_link_hosts_exclude_target_by_db_and_host() -> None:
    """자원 id 없는 연계 대상도 같은 db_id·같은 정규 호스트 키 구성원이면 대상으로 뺀다."""
    svc = _service(n_sms=3, n_apm=0)
    svc["members"].append(_sms(400, "ibksvr01", db="polestar_b0", zone="gongjon"))
    target = BizTarget(link_hosts=frozenset({("polestar_cm_gp", "IBK_SVR01")}))
    out = _build(svc, target=target)
    assert out is not None
    got = [(m["db_id"], m["hostname"]) for m in out["sms_members"]]
    assert ("polestar_cm_gp", "ibksvr01") not in got
    assert ("polestar_b0", "ibksvr01") in got  # 다른 DB 같은 이름은 대상이 아니다


def test_sms_db_of_registry_and_apm() -> None:
    assert bic.sms_db_of("polestar_cm_gp") == "polestar_cm_gp"
    assert bic.sms_db_of("jennifer_gp") == ""
    assert bic.sms_db_of("not_registered_db") == ""
    assert bic.sms_db_of("") == "" and bic.sms_db_of(None) == ""


def test_sms_db_of_registry_failure_is_db_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.routing import registry

    def boom() -> Any:
        raise RuntimeError("registry down")

    monkeypatch.setattr(registry, "get_registry", boom)
    assert bic.sms_db_of("anything") == "anything"
    assert bic.sms_db_of("jennifer_gp") == ""
