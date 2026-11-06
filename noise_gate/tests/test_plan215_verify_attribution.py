"""plans/215 독립 검증 — 귀속(`attribute`)·D2 대상 제외의 경계 (verifier 작성).

구현 테스트(`test_plan215_w2_cache`·`test_plan215_w10_biz_context`)가 다루지 않은 두 경계를 본다.

1. `bizsvc:idx:host`는 db_id를 보지 않는다 — 구성원은 `polestar_cm_gp`에만 있는데 같은 호스트
   이름의 `polestar_cm_yd` SMS 알람이 그 서비스로 귀속된다(M2 집계·D2 맥락 오귀속 · D-224 ④ 취지).
2. APM 알람의 `polestar_link`가 `resource_id` 없이 linked(D-348 확정 연결 `previous_confirmed`)이면
   D2 `sms_members`에서 연계 대상 서버 자신이 빠지지 않는다(수용 15 「대상 제외」).

둘 다 검증 당시 재현된 결함이었다(`xfail(strict=True)`) — 교정 뒤 마크를 지웠다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis, FakePipeline

from noise_gate.application import biz_investigation_context as bic
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, match_key_for
from noise_gate.domain.biz_service_keys import apm_instance_member_key
from noise_gate.infrastructure.biz_service_cache import RedisBizServiceCache

ZONES = {"polestar_cm_gp": "gongjon", "polestar_cm_yd": "gongjon", "jennifer_gp": "gongjon"}


class _Pipe(FakePipeline):
    _QUEUEABLE = FakePipeline._QUEUEABLE | {"sadd"}


class _Redis(FakeAsyncRedis):
    async def hmget(self, name: str, keys: list[str], *args: str) -> list[str | None]:
        h = self._hash(name) or {}
        return [h.get(k) for k in [*keys, *args]]

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> _Pipe:
        return _Pipe(self)


class _Repo:
    def __init__(self, services: list[dict[str, Any]]) -> None:
        self.services = services

    async def active_services_with_members(self) -> list[dict[str, Any]]:
        return self.services


@pytest.fixture(autouse=True)
def _zones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bic, "_db_zone", lambda db_id: ZONES.get(db_id, ""))
    monkeypatch.setattr(bic, "_apm_source_zone", lambda source_id: "gongjon")


def _sms(rid: int, host: str, *, db: str = "polestar_cm_gp") -> dict[str, Any]:
    return {"kind": "sms_server", "member_key": f"{db}|{rid}", "db_id": db,
            "resource_id": str(rid), "server_name": host.upper(), "hostname": host,
            "zone": "gongjon", "prefix_code": "ibk", "confidence": "confirmed"}


def _inst(name: str, *, src: str = "gp", dom: str = "7") -> dict[str, Any]:
    key = apm_instance_member_key(src, dom, SCOPE_INSTANCE, match_key_for(SCOPE_INSTANCE, name))
    return {"kind": "apm_instance", "member_key": key, "source_id": src, "domain_id": dom,
            "instance_name": name, "confidence": "confirmed", "zone": "gongjon"}


def _service() -> dict[str, Any]:
    return {"id": 7, "service_key": "ibk", "display_name": "인터넷뱅킹", "state": "active",
            "members": [_sms(1, "ibkweb01"), _sms(2, "ibkwas01"), _sms(3, "ibkdb01"),
                        _inst("ibk-was-7f9c-x2k1p")]}


async def _cache() -> RedisBizServiceCache:
    cache = RedisBizServiceCache(_Redis())
    await cache.sync_from_repo(_Repo([_service()]))
    return cache


def _cfg() -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(noise_gate=SimpleNamespace(
        biz_investigation_context_active=True, biz_investigation_max_sms=10,
        biz_investigation_max_apm=8,
    ))


def _event(host: str, db_id: str, **kw: Any) -> AlarmEvent:
    base: dict[str, Any] = dict(
        db_id=db_id, server_name=host.upper(), hostname=host, ip_address="",
        resource_ancestry="", alarm_id="a-1", severity=3, alarm_status="NOT_ACK",
        resource_type="server.Server", resource_name=host, alarm_name="CPU 사용률",
        alarm_time=datetime(2026, 11, 5, 10, 0, 0), conditions="cpu>90", condition_log="95",
    )
    base.update(kw)
    return AlarmEvent(**base)


async def test_host_index_same_db_still_attributes() -> None:
    """기준선 — 같은 db_id·같은 호스트 이름은 host 단계로 귀속된다(수용 9 · §14 M2 벡터)."""
    cache = await _cache()
    hit = await cache.attribute(hostname="IBKWEB01")
    assert hit is not None and hit.service_id == 7 and hit.basis == "host"


async def test_sms_alarm_from_other_db_same_hostname_not_attributed() -> None:
    cache = await _cache()
    payload: dict[str, Any] = {"meta": {}}
    await bic.attach_biz_service(
        _cfg(), _event("ibkweb01", "polestar_cm_yd"), payload, cache=cache)
    assert "biz_service" not in payload["meta"]


async def test_apm_alarm_link_without_resource_id_excludes_linked_server() -> None:
    cache = await _cache()
    pod = "ibk-was-7f9c-x2k1p"
    payload: dict[str, Any] = {"meta": {"hints": {
        "solution": "apm", "domain_id": "7",
        "polestar_link": {"status": "linked", "db_id": "polestar_cm_gp",
                          "server_name": "IBKWAS01", "hostname": "ibkwas01",
                          "basis": "previous_confirmed"},
    }}}
    event = _event(pod, "jennifer_gp", resource_type="apm.instance",
                   server_name=pod, alarm_name="WAS 오류")
    await bic.attach_biz_service(_cfg(), event, payload, cache=cache)
    biz = payload["meta"].get("biz_service")
    assert biz is not None, "APM 인스턴스 구성원으로는 귀속되어야 한다(전제)"
    assert "ibkwas01" not in {m["hostname"] for m in biz["sms_members"]}
