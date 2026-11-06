"""plans/215 W9 — D1 진단 조립(수용 14 · M3 캐시 → 관측 호출 0 · 즉석 소스당 1·존당 1 · 메모 60초 ·
전역 동시 1 · 예산 「일부만」 · 존 필터 · D2 메모 계약 · 플래그 off).

가짜 M2 집계기 · 가짜 Redis(문자열·해시) · 가짜 게이트웨이(호출 기록) · 가짜 SQL 실행기(`SQLGuard`
단언)로 본다.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

from src.security.sql_guard import SQLGuard

from noise_gate.application import biz_diagnosis as bd
from noise_gate.application.biz_investigation_context import D1_MEMO_PREFIX
from noise_gate.application.biz_monitor_alarms import (
    FACT_RECENT,
    MONITOR_SVC_PREFIX,
    AlarmFact,
    AlarmSnapshot,
)
from noise_gate.application.biz_monitor_collector import APM_HEALTH_TOOL, REASON_SMS_UNSET
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, cache_field, match_key_for
from noise_gate.domain.biz_service_context import _d1_parts
from noise_gate.domain.biz_service_keys import (
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_instance_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import BizServiceConfig, SmsThreshold
from noise_gate.infrastructure.apm_host_map_cache import map_key

WALL0 = 1_800_000_000.0
SOURCES = {"cpu": ("server.Cpus", "Utilization")}
GP, YD = "polestar_cm_gp", "polestar_cm_yd"


# ── 가짜 ────────────────────────────────────────────────────────────────
class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return WALL0


class _Redis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.h: dict[str, dict[str, str]] = {}
        self.gets: list[str] = []

    async def get(self, key: str) -> str | None:
        self.gets.append(key)
        return self.kv.get(key)

    async def set(self, key: str, value: Any, *, ex: int | None = None) -> bool:
        self.kv[key] = str(value)
        if ex is not None:
            self.ttl[key] = int(ex)
        return True

    async def hmget(self, key: str, fields: list[str]) -> list[str | None]:
        return [self.h.get(key, {}).get(f) for f in fields]


class _Agg:
    def __init__(self, redis: _Redis, services: dict[int, dict[str, Any]],
                 facts: list[AlarmFact]) -> None:
        self.cache = SimpleNamespace(redis=redis)
        self.services = services
        self.facts = facts
        self.calls = 0

    async def snapshot(self) -> AlarmSnapshot:
        self.calls += 1
        return AlarmSnapshot(WALL0 - 5, 60, self.services, tuple(self.facts), ())


class _Gateway:
    def __init__(self, signal_names: set[str] | None = None, *, fail: bool = False,
                 delay: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.signal_names = signal_names or set()
        self.fail = fail
        self.delay = delay

    async def call_tool(self, tool: str, args: dict[str, Any], *,
                        timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((tool, args, timeout))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise TimeoutError("게이트웨이 시간 초과")
        rows, batch = [], []
        for i, t in enumerate(args["targets"]):
            rows.append({"source_id": t["source_id"], "instance_id": 100 + i, "domain_id": 7,
                         "instance_name": t["instance_name"], "target_index": i})
            sigs = [{"kind": "was_error_burst", "level": "CRITICAL", "instance_id": 100 + i}] \
                if t["instance_name"] in self.signal_names else []
            batch.append({"index": i, "status": "ok", "was_signals": sigs})
        return {"rows": rows, "batch": batch}


class _Exec:
    def __init__(self, cpu: float = 40.0, stats: dict[str, float] | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.cpu = cpu
        self.guard = SQLGuard()
        self.stat = time.strftime("%Y%m%d%H", time.localtime(WALL0))
        self.stats = {db: time.strftime("%Y%m%d%H", time.localtime(at))
                      for db, at in (stats or {}).items()}

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        ok, reason = self.guard.is_safe_select(sql)
        assert ok and sql.lstrip().startswith("SELECT"), reason
        self.calls.append((db_id, sql))
        if "MAX(s.stat_date)" in sql:
            return [{"stat_date": self.stats.get(db_id, self.stat)}]
        ids = [int(x) for x in sql.split("IN (")[1].split(")")[0].split(", ")]
        return [{"server_id": i, "resource_type": "server.Cpus", "definition_name": "Utilization",
                 "avg_val": self.cpu} for i in ids]


class _InvStore:
    def get_by_alarm(self, alarm_id: str) -> dict[str, Any] | None:
        if alarm_id != "cpu-1":
            return None
        return {"investigation_id": "inv-9", "status": "done", "ts": "2027-01-15T08:00:00+00:00",
                "briefing": {"engine_agreement": {"playbook": {"layer": "os"}}}}


def _inst(name: str, source: str = "s1", zone: str = "gongjon") -> dict[str, Any]:
    return {"id": hash(name) % 1000, "kind": KIND_APM_INSTANCE, "source_id": source,
            "domain_id": "7", "instance_name": name, "zone": zone, "layer": "was",
            "member_key": apm_instance_member_key(source, "7", SCOPE_INSTANCE,
                                                  match_key_for(SCOPE_INSTANCE, name))}


def _sms(name: str, rid: int, db_id: str = GP, zone: str = "gongjon") -> dict[str, Any]:
    return {"id": rid, "kind": KIND_SMS_SERVER, "db_id": db_id, "resource_id": str(rid),
            "server_name": name, "zone": zone, "layer": "was",
            "member_key": sms_member_key(db_id, rid)}


INST = _inst("ibk-was-01")
SRV = _sms("ibkwas01", 101)


def _svc(members: list[dict[str, Any]], monitored: bool = False) -> dict[int, dict[str, Any]]:
    return {1: {"id": 1, "display_name": "인터넷뱅킹", "monitored": monitored,
                "members": members}}


def _cpu_alarm(member: dict[str, Any], *, at: float = WALL0 - 120, alarm_id: str = "cpu-1",
               zone: str = "gongjon") -> AlarmFact:
    return AlarmFact(FACT_RECENT, alarm_id, GP, 2, "page", at, 1, "polestar_link",
                     member["member_key"], zone)


def _link(redis: _Redis, inst: dict[str, Any], *targets: dict[str, Any]) -> None:
    fld = cache_field(SCOPE_INSTANCE, inst["domain_id"],
                      match_key_for(SCOPE_INSTANCE, inst["instance_name"]))
    redis.h.setdefault(map_key(inst["source_id"]), {})[fld] = json.dumps({
        "state": "active",
        "targets": [{"kind": "sms", "db_id": t["db_id"], "resource_id": t["resource_id"]}
                    for t in targets],
    })


def _settings(*, sources: dict[str, tuple[str, str]] | None = None) -> BizServiceSettings:
    return BizServiceSettings(BizServiceConfig(sms_thresholds={"cpu": SmsThreshold(80.0, 90.0)}),
                              frozenset(), (), SOURCES if sources is None else sources)


def _diag(
    members: list[dict[str, Any]],
    *,
    facts: list[AlarmFact] | None = None,
    monitored: bool = False,
    metrics_active: bool = False,
    gateway: Any = None,
    execute: Any = None,
    settings: BizServiceSettings | None = None,
    clock: _Clock | None = None,
    **kw: Any,
) -> tuple[bd.BizDiagnoser, _Redis, _Agg]:
    redis = _Redis()
    agg = _Agg(redis, _svc(members, monitored), facts or [])
    clock = clock or _Clock()
    diag = bd.BizDiagnoser(
        aggregator=agg, redis=redis, gateway=gateway, execute=execute,
        settings_loader=lambda: settings or _settings(),
        map_cache=SimpleNamespace(redis=redis), metrics_active=metrics_active,
        monotonic=clock.mono, wall=clock.wall, **kw,
    )
    return diag, redis, agg


def _m3_value(*, signals: list[str], cpu: float | None = None) -> str:
    stat = time.strftime("%Y-%m-%d %H:00", time.localtime(WALL0))
    return json.dumps({
        "updated_at": "2027-01-15T08:00:00+00:00",
        "in_window": True,
        "sms_stat_time": stat,
        "sms_stat_time_by_db": {GP: stat},
        "apm_signals": [{"member_key": INST["member_key"], "signal": s, "level": "CRITICAL"}
                        for s in signals],
        "apm_values": [],
        "sms_metrics": [] if cpu is None else [
            {"member_key": SRV["member_key"], "metric": "cpu", "value": cpu}],
    })


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


# ── 수용 14 ─────────────────────────────────────────────────────────────
async def test_acceptance_14_m3_cache_zero_calls_r3_r4_r5() -> None:
    gw, ex = _Gateway(), _Exec()
    # R3 — APM 이상(M3) + 이어진 서버 같은 창 CPU 알람
    diag, redis, _ = _diag([INST, SRV], facts=[_cpu_alarm(SRV)], monitored=True,
                           metrics_active=True, gateway=gw, execute=ex,
                           investigation_store=_InvStore())
    _link(redis, INST, SRV)
    redis.kv[f"{MONITOR_SVC_PREFIX}1"] = _m3_value(signals=["was_error_burst"])
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["verdict"] == "R3"
    assert out["representative"]["member_key"] == SRV["member_key"]
    assert out["sources"]["metrics"] == bd.SOURCE_M3
    assert out["sources"]["calls"] == {"apm": 0, "sms": 0}
    assert gw.calls == [] and ex.calls == []
    srv_row = next(r for r in out["anomalous_members"] if r["kind"] == KIND_SMS_SERVER)
    assert srv_row["investigation"] == {"investigation_id": "inv-9", "status": "done",
                                        "at": "2027-01-15T08:00:00+00:00", "cause_layer": "os"}
    texts = _strings(out)

    # R4 — 이어진 서버 이상 신호 없음(임계 미만 · 창 안 정각 통계)
    diag, redis, _ = _diag([INST, SRV], monitored=True, metrics_active=True, gateway=gw,
                           execute=ex)
    _link(redis, INST, SRV)
    redis.kv[f"{MONITOR_SVC_PREFIX}1"] = _m3_value(signals=["was_error_burst"], cpu=30.0)
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["verdict"] == "R4"
    assert out["representative"]["member_key"] == SRV["member_key"]
    texts += _strings(out)

    # R5 — 연계 없음
    diag, redis, _ = _diag([INST, SRV], monitored=True, metrics_active=True, gateway=gw,
                           execute=ex)
    redis.kv[f"{MONITOR_SVC_PREFIX}1"] = _m3_value(signals=["was_error_burst"], cpu=30.0)
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["verdict"] == "R5"
    assert out["representative"] is None
    assert [g["code"] for g in out["gaps"]] == ["no_link"]
    texts += _strings(out)

    assert gw.calls == [] and ex.calls == []
    assert not [t for t in texts if "정상" in t]


# ── 즉석 조회 ───────────────────────────────────────────────────────────
async def test_adhoc_one_call_per_source_and_zone() -> None:
    members = [_inst("ibk-was-01"), _inst("ibk-was-02"), _inst("card-was-01", source="s2"),
               SRV, _sms("ibkwas02", 102), _sms("ydwas01", 7, db_id=YD)]
    gw, ex = _Gateway({"ibk-was-01"}), _Exec()
    diag, redis, _ = _diag(members, gateway=gw, execute=ex)
    _link(redis, members[0], SRV)
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["sources"]["metrics"] == bd.SOURCE_ADHOC
    assert sorted(len(a["targets"]) for _, a, _ in gw.calls) == [1, 2]  # 소스당 1호출
    assert {t for _, a, _ in gw.calls for t in (a["owner"],)} == {"biz_diagnosis"}
    assert {tool for tool, _, _ in gw.calls} == {APM_HEALTH_TOOL}
    by_zone: dict[str, int] = {}
    for db_id, _sql in ex.calls:
        by_zone[db_id] = by_zone.get(db_id, 0) + 1
    assert by_zone == {GP: 2, YD: 2}  # 존마다 최신 정각 확인 1 + 지표 행 1
    assert out["sources"]["calls"] == {"apm": 2, "sms": 4}
    assert out["verdict"] == "R4"  # 이어진 서버 CPU 40 < 80 · 창 안 정각 통계
    assert out["partial"] is False


async def test_adhoc_stat_time_per_zone() -> None:
    """존 A 최신 · 존 B 3시간 전 — 존 B 구성원만 「지표 없음」(F12 · 구성원별 창 판정)."""
    yd = _sms("ydwas01", 7, db_id=YD)
    ex = _Exec(cpu=95.0, stats={GP: WALL0, YD: WALL0 - 3 * 3600})
    diag, _, _ = _diag([SRV, yd], gateway=_Gateway(), execute=ex)
    out = await diag.diagnose(1, scope=None)
    assert out is not None
    assert [m["member_key"] for m in out["anomalous_members"]] == [SRV["member_key"]]
    gap = next(g for g in out["gaps"] if g["code"] == "no_metric")
    assert gap["member_keys"] == [yd["member_key"]] and "창 안 정각 통계 없음" in gap["label"]
    # 결정성 — 구성원 순서를 뒤집어도 같은 판독
    diag2, _, _ = _diag([yd, SRV], gateway=_Gateway(),
                        execute=_Exec(cpu=95.0, stats={GP: WALL0, YD: WALL0 - 3 * 3600}))
    out2 = await diag2.diagnose(1, scope=None)
    assert out2 is not None
    assert {k: out2[k] for k in ("verdict", "anomalous_members", "gaps", "representative")} == \
        {k: out[k] for k in ("verdict", "anomalous_members", "gaps", "representative")}


async def test_adhoc_apm_target_cap_partial() -> None:
    members = [_inst(f"ibk-was-0{i}") for i in range(4)]
    gw = _Gateway()
    diag, _, _ = _diag(members, gateway=gw, execute=_Exec(), max_apm_targets=3)
    out = await diag.diagnose(1, scope=None)
    assert out is not None
    assert len(gw.calls) == 1 and len(gw.calls[0][1]["targets"]) == 3
    assert out["partial"] is True
    assert any(n.startswith("일부만") and "상한 3" in n for n in out["notices"])


async def test_adhoc_failure_and_budget_are_partial_with_reason() -> None:
    gw = _Gateway(fail=True)
    diag, _, _ = _diag([INST], gateway=gw, execute=_Exec())
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["partial"] is True
    assert any("APM s1 조회 실패" in n for n in out["notices"])
    # 예산이 최소 호출 시간보다 작으면 호출하지 않고 사유
    gw = _Gateway()
    ex = _Exec()
    diag, _, _ = _diag([INST, SRV], gateway=gw, execute=ex, budget_seconds=1.0)
    out = await diag.diagnose(1, scope=None)
    assert out is not None and gw.calls == [] and ex.calls == []
    assert out["partial"] is True
    assert any("예산 1초 초과" in n for n in out["notices"])


async def test_sms_source_unset_no_select_and_r5() -> None:
    gw, ex = _Gateway({"ibk-was-01"}), _Exec()
    diag, redis, _ = _diag([INST, SRV], gateway=gw, execute=ex, settings=_settings(sources={}))
    _link(redis, INST, SRV)
    out = await diag.diagnose(1, scope=None)
    assert out is not None and ex.calls == []
    assert REASON_SMS_UNSET in out["notices"]
    assert out["verdict"] == "R5" and "지표 없음" in out["verdict_text"]


async def test_m3_missing_falls_back_to_adhoc_once() -> None:
    gw = _Gateway()
    diag, _, _ = _diag([INST], monitored=True, metrics_active=True, gateway=gw, execute=_Exec())
    out = await diag.diagnose(1, scope=None)
    assert out is not None and out["sources"]["metrics"] == bd.SOURCE_ADHOC
    assert len(gw.calls) == 1 and bd.REASON_M3_MISSING in out["notices"]


# ── 메모 · 동시 ─────────────────────────────────────────────────────────
async def test_memo_60s_and_concurrent_single_flight() -> None:
    clock = _Clock()
    gw = _Gateway(delay=0.01)
    diag, _, agg = _diag([INST], gateway=gw, execute=_Exec(), clock=clock)
    outs = await asyncio.gather(*(diag.diagnose(1, scope=None) for _ in range(3)))
    assert len(gw.calls) == 1 and agg.calls == 1
    assert outs[0] == outs[1] == outs[2]
    clock.t += 59
    await diag.diagnose(1, scope=None)
    assert len(gw.calls) == 1
    clock.t += 2
    await diag.diagnose(1, scope=None)
    assert len(gw.calls) == 2 and agg.calls == 2
    assert diag._svc_locks == {}  # 계산이 끝나면 잠금을 남기지 않는다


async def test_global_adhoc_concurrency_one() -> None:
    redis = _Redis()
    services = {1: {"id": 1, "members": [_inst("ibk-was-01")]},
                2: {"id": 2, "members": [_inst("ibk-was-02")]}}
    agg = _Agg(redis, services, [])
    active = 0
    peak = 0

    class _Slow(_Gateway):
        async def call_tool(self, tool: str, args: dict[str, Any], *,
                            timeout: float | None = None) -> dict[str, Any]:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            return await super().call_tool(tool, args, timeout=timeout)

    diag = bd.BizDiagnoser(aggregator=agg, redis=redis, gateway=_Slow(), execute=None,
                           settings_loader=_settings)
    await asyncio.gather(diag.diagnose(1, scope=None), diag.diagnose(2, scope=None))
    assert peak == 1


# ── 존 필터 ─────────────────────────────────────────────────────────────
async def test_zone_filter_counts_only_and_hidden_service() -> None:
    bank = _sms("bank01", 900, db_id="polestar_b0", zone="bankjon")
    facts = [_cpu_alarm(bank, alarm_id="b-1", zone="bankjon"), _cpu_alarm(SRV)]
    diag, _, _ = _diag([SRV, bank, _sms("x", 103)], facts=facts, gateway=_Gateway(),
                       execute=_Exec())
    out = await diag.diagnose(1, scope=frozenset({"gongjon"}))
    assert out is not None
    assert [m["member_key"] for m in out["anomalous_members"]] == [SRV["member_key"]]
    hidden = next(g for g in out["gaps"] if g["code"] == "out_of_scope")
    assert hidden["count"] == 1 and hidden["member_keys"] == []
    assert "bank01" not in " ".join(_strings(out))
    full = await diag.diagnose(1, scope=None)
    assert full is not None and full["verdict"] == "R2"  # 전 존이면 3대 중 2대
    assert await diag.diagnose(1, scope=frozenset({"other"})) is None
    assert await diag.diagnose(2, scope=None) is None


# ── D2 메모 계약 ────────────────────────────────────────────────────────
async def test_d1_memo_matches_w10_reader() -> None:
    bank = _sms("bank01", 900, db_id="polestar_b0", zone="bankjon")
    facts = [_cpu_alarm(SRV), _cpu_alarm(bank, alarm_id="b-1", zone="bankjon")]
    diag, redis, _ = _diag([INST, SRV, bank], facts=facts, gateway=_Gateway({"ibk-was-01"}),
                           execute=_Exec())
    _link(redis, INST, SRV)
    await diag.diagnose(1, scope=frozenset({"gongjon"}))
    key = f"{D1_MEMO_PREFIX}1"
    assert redis.ttl[key] == 60
    memo = json.loads(redis.kv[key])
    assert set(memo) == {"by_zone", "computed_at"}
    assert set(memo["by_zone"]) == {"bankjon", "gongjon"}
    verdict, anomalous = _d1_parts(memo["by_zone"]["gongjon"])
    assert verdict == "R3"  # 그 존 구성원만으로 판독(APM 이상 + 이어진 서버 CPU 알람)
    assert anomalous == {(KIND_SMS_SERVER, SRV["member_key"]),
                         (KIND_APM_INSTANCE, INST["member_key"])}
    verdict, anomalous = _d1_parts(memo["by_zone"]["bankjon"])
    assert verdict == "R6" and anomalous == {(KIND_SMS_SERVER, bank["member_key"])}


# ── 플래그 · 출력 모양 ──────────────────────────────────────────────────
def test_flag_off_builds_nothing() -> None:
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(biz_diagnosis_active=False))
    assert bd.build_biz_diagnoser(cfg, aggregator=object()) is None
    assert bd.diagnosis_active(SimpleNamespace(noise_gate=SimpleNamespace(
        biz_diagnosis_active="yes"))) is False


async def test_output_keys() -> None:
    diag, _, _ = _diag([SRV], gateway=_Gateway(), execute=_Exec())
    out = await diag.diagnose(1, scope=None)
    assert out is not None
    assert set(out) == {
        "service_id", "display_name", "verdict", "verdict_text", "verdict_guidance",
        "hypothesis_label", "correlation_note", "anomalous_members", "timeline",
        "representative", "representative_reason", "gaps", "computed_at", "window_minutes",
        "sources", "partial", "notices",
    }
    assert set(out["sources"]) == {"m2", "metrics", "metrics_at", "calls"}
    assert out["verdict"] == "R1" and out["hypothesis_label"] == "가설 · 참고용"
