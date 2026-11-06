"""plans/215 잔여 ⑥⑨ — M3 존별 SMS 정각 통계 시각 · D1 메모 존별 판독(존 필터 뒤 구성원).

- ⑥ M3 수집기가 `sms_stat_time_by_db`를 적고 D1이 구성원 존의 시각으로 창을 판정한다 · 그 칸이
  없는 옛 형태 값이면 서비스 단위 시각 폴백 + 결손 `service_stat_time`.
- ⑨ D1 메모 `bizdiag:d1:{id}`는 존마다 그 존 구성원만으로 판독한 `by_zone` · D2는 알람 존 항목만
  싣고(존 미상·허용 밖·항목 없음·옛 형태면 미적재) 다른 존 이름·키를 싣지 않는다.

가짜는 W7(수집기)·W9(조립)·W10(D2) 테스트의 것을 그대로 쓴다.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from noise_gate.application import biz_investigation_context as bic
from noise_gate.application.biz_monitor_alarms import MONITOR_SVC_PREFIX
from noise_gate.domain.biz_diagnosis import (
    GAP_SERVICE_STAT_TIME,
    DiagMember,
    DiagMetrics,
    diagnose_service,
)
from noise_gate.domain.biz_service_keys import KIND_SMS_SERVER
from noise_gate.domain.biz_service_model import SmsThreshold
from noise_gate.domain.biz_service_state import SmsMetricFact
from noise_gate.tests import test_plan215_w7_collector as w7
from noise_gate.tests import test_plan215_w9_diagnosis_assembly as w9
from noise_gate.tests import test_plan215_w10_biz_context as w10

GP, B0, YD = "polestar_cm_gp", "polestar_b0", "polestar_cm_yd"


def _hour(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:00", time.localtime(epoch))


def _m3(*, by_db: dict[str, str] | None, stat: str, cpu: dict[str, float]) -> str:
    value: dict[str, Any] = {
        "updated_at": "2027-01-15T08:00:00+00:00", "in_window": True, "sms_stat_time": stat,
        "apm_signals": [], "apm_values": [],
        "sms_metrics": [{"member_key": k, "metric": "cpu", "value": v} for k, v in cpu.items()],
    }
    if by_db is not None:
        value["sms_stat_time_by_db"] = by_db
    return json.dumps(value)


# ── ⑥ 수집기: 존별 시각 기록 ──────────────────────────────────────────────
async def test_collector_writes_stat_time_per_zone() -> None:
    class _ZoneExec(w7._Exec):
        async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
            self.stat = {"polestar_b0": "2026110511"}.get(db_id, "2026110514")
            return await super().__call__(db_id, sql)

    members = [w7._sms(GP, 5), w7._sms(B0, 6)]
    col, redis, _ = w7._collector([w7._svc(1, members), w7._svc(2, [w7._inst("s1", "7", "a-7")])],
                                  execute=_ZoneExec(),
                                  settings=w7._settings(sources=w7.SOURCES))
    try:
        await col.run_once()
    finally:
        await col.aclose()
    v = w7._value(redis, 1)
    assert v["sms_stat_time"] == "2026-11-05 14:00"  # 서비스 단위(존별 최댓값) — 종전 그대로
    assert v["sms_stat_time_by_db"] == {B0: "2026-11-05 11:00", GP: "2026-11-05 14:00"}
    assert w7._value(redis, 2)["sms_stat_time_by_db"] == {}  # SMS 구성원 없음 — 빈 칸(새 형태)


# ── ⑥ D1: M3 존별 시각으로 창 판정 ───────────────────────────────────────
async def test_m3_stat_time_per_zone_window() -> None:
    """존 GP 최신 · 존 YD 3시간 전 — YD 구성원만 「창 안 정각 통계 없음」(서비스 시각 오판 없음)."""
    yd = w9._sms("ydwas01", 7, db_id=YD)
    diag, redis, _ = w9._diag([w9.SRV, yd], monitored=True, metrics_active=True,
                              gateway=w9._Gateway(), execute=w9._Exec())
    redis.kv[f"{MONITOR_SVC_PREFIX}1"] = _m3(
        by_db={GP: _hour(w9.WALL0), YD: _hour(w9.WALL0 - 3 * 3600)}, stat=_hour(w9.WALL0),
        cpu={w9.SRV["member_key"]: 95.0, yd["member_key"]: 95.0})
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["sources"]["metrics"] == "m3"
    assert out["sources"]["calls"] == {"apm": 0, "sms": 0}
    assert [m["member_key"] for m in out["anomalous_members"]] == [w9.SRV["member_key"]]
    assert out["verdict"] == "R6"
    gap = next(g for g in out["gaps"] if g["code"] == "no_metric")
    assert gap["member_keys"] == [yd["member_key"]] and "창 안 정각 통계 없음" in gap["label"]
    assert GAP_SERVICE_STAT_TIME not in [g["code"] for g in out["gaps"]]


async def test_m3_zone_missing_from_by_db_is_not_service_time() -> None:
    """새 형태인데 그 존 시각이 없으면 서비스 시각으로 물러서지 않는다(그 구성원은 판정 불가)."""
    yd = w9._sms("ydwas01", 7, db_id=YD)
    diag, redis, _ = w9._diag([w9.SRV, yd], monitored=True, metrics_active=True,
                              gateway=w9._Gateway(), execute=w9._Exec())
    redis.kv[f"{MONITOR_SVC_PREFIX}1"] = _m3(
        by_db={GP: _hour(w9.WALL0)}, stat=_hour(w9.WALL0),
        cpu={w9.SRV["member_key"]: 95.0, yd["member_key"]: 95.0})
    out = await diag.diagnose(1, scope=None)
    assert out is not None
    assert [m["member_key"] for m in out["anomalous_members"]] == [w9.SRV["member_key"]]
    assert GAP_SERVICE_STAT_TIME not in [g["code"] for g in out["gaps"]]


async def test_m3_old_form_falls_back_to_service_time_with_gap() -> None:
    yd = w9._sms("ydwas01", 7, db_id=YD)
    diag, redis, _ = w9._diag([w9.SRV, yd], monitored=True, metrics_active=True,
                              gateway=w9._Gateway(), execute=w9._Exec())
    redis.kv[f"{MONITOR_SVC_PREFIX}1"] = _m3(
        by_db=None, stat=_hour(w9.WALL0),
        cpu={w9.SRV["member_key"]: 95.0, yd["member_key"]: 95.0})
    out = await diag.diagnose(1, scope=None)
    assert out is not None
    # 종전 동작(서비스 단위 시각을 모든 구성원에) — 둘 다 이상
    assert sorted(m["member_key"] for m in out["anomalous_members"]) == sorted(
        [w9.SRV["member_key"], yd["member_key"]])
    gap = next(g for g in out["gaps"] if g["code"] == GAP_SERVICE_STAT_TIME)
    assert gap["member_keys"] == sorted([w9.SRV["member_key"], yd["member_key"]])
    assert "서비스 단위" in gap["label"]
    assert not [s for s in w9._strings(out) if "정상" in s]


def test_domain_service_stat_gap_only_when_fallback_used() -> None:
    srv = DiagMember(KIND_SMS_SERVER, "gp|1", "s1", "was", "gp", "1")
    now = 1_800_000_000.0
    th = {"cpu": SmsThreshold(80.0, 90.0)}
    sms = (SmsMetricFact(srv.member_key, "cpu", 40.0),)
    own = diagnose_service(members=[srv], metrics=DiagMetrics(
        sms_metrics=sms, sms_stats={srv.member_key: (now, "12시 통계")}, sms_stat_at=now),
        thresholds=th, now=now, window_minutes=15)
    assert own["gaps"] == []
    fallback = diagnose_service(members=[srv], metrics=DiagMetrics(
        sms_metrics=sms, sms_stat_at=now), thresholds=th, now=now, window_minutes=15)
    assert [g["code"] for g in fallback["gaps"]] == [GAP_SERVICE_STAT_TIME]
    no_metric = diagnose_service(members=[srv], metrics=DiagMetrics(sms_stat_at=now),
                                 thresholds=th, now=now, window_minutes=15)
    assert GAP_SERVICE_STAT_TIME not in [g["code"] for g in no_metric["gaps"]]


# ── ⑨ D1 메모: 존별 판독 ────────────────────────────────────────────────
async def test_d1_memo_by_zone_matches_user_view_per_zone() -> None:
    """전 존이면 2대 중 2대 → R2 · 존마다 1대 → R6 — 메모는 존별 · 사용자 화면도 존 필터 뒤."""
    bank = w9._sms("bank01", 900, db_id=B0, zone="bankjon")
    facts = [w9._cpu_alarm(w9.SRV), w9._cpu_alarm(bank, alarm_id="b-1", zone="bankjon")]
    diag, redis, _ = w9._diag([w9.SRV, bank], facts=facts, gateway=w9._Gateway(),
                              execute=w9._Exec())
    full = await diag.diagnose(1, scope=None)
    assert full is not None and full["verdict"] == "R2"
    memo = json.loads(redis.kv[f"{bic.D1_MEMO_PREFIX}1"])
    assert set(memo) == {"by_zone", "computed_at"}
    assert memo["by_zone"] == {
        "bankjon": {"verdict": "R6", "anomalous_members": [
            {"kind": KIND_SMS_SERVER, "member_key": bank["member_key"]}]},
        "gongjon": {"verdict": "R6", "anomalous_members": [
            {"kind": KIND_SMS_SERVER, "member_key": w9.SRV["member_key"]}]},
    }
    for zone in ("gongjon", "bankjon"):
        view = await diag.diagnose(1, scope=frozenset({zone}))
        assert view is not None and view["verdict"] == memo["by_zone"][zone]["verdict"]
    assert all("bank01" not in s and "ibkwas01" not in s for s in w9._strings(memo))


# ── ⑨ D2: 알람 존 항목만 ────────────────────────────────────────────────
@pytest.fixture
def zones(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    table = dict(w10.ZONES)
    monkeypatch.setattr(bic, "_db_zone", lambda db_id: table.get(db_id, ""))
    monkeypatch.setattr(bic, "_apm_source_zone", lambda source_id: "gongjon")
    return table


def _two_zone_service() -> dict[str, Any]:
    return {"id": 7, "service_key": "ibk", "display_name": "인터넷뱅킹", "state": "active",
            "members": [w10._sms(1, "ibkweb01"), w10._sms(2, "ibkweb02"),
                        w10._sms(9, "banksvr09", db=B0, zone="bankjon")]}


_MEMO = {
    "by_zone": {
        "gongjon": {"verdict": "R6", "anomalous_members": [
            {"kind": KIND_SMS_SERVER, "member_key": f"{GP}|2"}]},
        "bankjon": {"verdict": "R2", "anomalous_members": [
            {"kind": KIND_SMS_SERVER, "member_key": f"{B0}|9"}]},
    },
    "computed_at": "2026-11-05T10:00:00+00:00",
}


async def _attach(memo: Any, *, manual: bool = False,
                  requester_zones: Any = None) -> dict[str, Any]:
    cache, redis = await w10._cache([_two_zone_service()])
    if memo is not None:
        await redis.set("bizdiag:d1:7", memo if isinstance(memo, str) else json.dumps(memo))
    payload: dict[str, Any] = {"meta": {}}
    await bic.attach_biz_service(w10._cfg(), w10._event(), payload, cache=cache, manual=manual,
                                 requester_zones=requester_zones)
    biz: dict[str, Any] = payload["meta"]["biz_service"]
    return biz


async def test_d2_loads_only_alarm_zone_verdict(zones: dict[str, str]) -> None:
    biz = await _attach(_MEMO)
    assert biz["d1_verdict"] == "R6"  # 알람 존(gongjon) 판독 — 다른 존(R2) 아님
    assert [m["hostname"] for m in biz["sms_members"]] == ["ibkweb02"]  # 알람 대상 제외 · 같은 존만
    dumped = json.dumps(biz, ensure_ascii=False)
    assert "banksvr09" not in dumped and f"{B0}|9" not in dumped and "bankjon" not in dumped


async def test_d2_unknown_alarm_zone_loads_no_verdict(zones: dict[str, str]) -> None:
    zones[GP] = ""
    biz = await _attach(_MEMO)
    assert "d1_verdict" not in biz and biz["sms_members"] == []


async def test_d2_zone_entry_missing_or_old_form_loads_no_verdict(zones: dict[str, str]) -> None:
    only_bank = {"by_zone": {"bankjon": _MEMO["by_zone"]["bankjon"]}, "computed_at": "x"}
    assert "d1_verdict" not in await _attach(only_bank)
    old = {"verdict": "R2", "anomalous_members": [], "computed_at": "x"}
    assert "d1_verdict" not in await _attach(old)
    assert "d1_verdict" not in await _attach("{broken")


async def test_d2_manual_path_requires_alarm_zone_in_requester_zones(
        zones: dict[str, str]) -> None:
    assert (await _attach(_MEMO, manual=True, requester_zones=None))["d1_verdict"] == "R6"
    allowed = await _attach(_MEMO, manual=True, requester_zones=frozenset({"gongjon"}))
    assert allowed["d1_verdict"] == "R6"
    outside = await _attach(_MEMO, manual=True, requester_zones=frozenset({"bankjon"}))
    assert "d1_verdict" not in outside
