"""plans/215 W7 — M3 실시간 지표 수집기(수용 10 · §4.3 · §4.5).

가짜 게이트웨이(호출 기록) · 가짜 SQL 실행기(`SQLGuard` 단언 + 호출 기록) · 가짜 Redis(문자열·ZSET·
잠금 Lua 2종)와 가짜 사본으로 본다 — 보는 사람 없음 → 호출 0 · 인스턴스 상한 · 소스당 1호출 ·
같은 정각 통계 → 지표 행 조회 0 · 실패 상태와 마지막 성공 · 백오프 ×2·최대 10분 · 예산 초과 사유 ·
지표 원천 미설정 · 리더 잠금 · 상태 전이 · 시계열 상한 · W6 읽기와 왕복.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from src.security.sql_guard import SQLGuard

from noise_gate.application import biz_monitor_collector as c
from noise_gate.application.biz_monitor_alarms import (
    FACT_INCIDENT,
    MONITOR_STATUS_KEY,
    VIEWERS_KEY,
    AlarmFact,
    AlarmSnapshot,
    read_metrics_status,
    read_monitor_values,
)
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, match_key_for
from noise_gate.domain.biz_service_keys import (
    KIND_APM_BUSINESS,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_business_member_key,
    apm_instance_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import BizServiceConfig, SmsThreshold
from noise_gate.domain.biz_service_state import STATE_NO_SIGNAL, STATE_RISK, judge_service_state
from noise_gate.infrastructure.worker_lock import WorkerSingletonLock

WALL0 = 1_800_000_000.0
SOURCES = {"cpu": ("server.Cpus", "Utilization"), "memory": ("server.Memory", "Utilization")}


# ── 가짜 ────────────────────────────────────────────────────────────────
class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def mono(self) -> float:
        return self.t

    def wall(self) -> float:
        return WALL0 + self.t


class _Redis:
    """문자열(SET NX EX · GET · EXISTS) · ZSET · 잠금 Lua(연장·해제)만."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.z: dict[str, dict[str, float]] = {}

    async def get(self, key: str) -> str | None:
        return self.kv.get(key)

    async def set(self, key: str, value: Any, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.kv:
            return False
        self.kv[key] = str(value)
        if ex is not None:
            self.ttl[key] = int(ex)
        return True

    async def exists(self, key: str) -> int:
        return int(key in self.kv or key in self.z)

    async def eval(self, script: str, numkeys: int, key: str, owner: str, *args: Any) -> int:
        if self.kv.get(key) != owner:
            return 0
        if "'del'" in script:
            del self.kv[key]
        return 1

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttl[key] = int(seconds)
        return True

    async def zadd(self, key: str, mapping: dict[str, float]) -> int:
        self.z.setdefault(key, {}).update(mapping)
        return len(mapping)

    def _ordered(self, key: str) -> list[str]:
        return [m for m, _ in sorted(self.z.get(key, {}).items(), key=lambda kv: kv[1])]

    @staticmethod
    def _span(n: int, start: int, stop: int) -> range:
        start = start + n if start < 0 else start
        stop = stop + n if stop < 0 else stop
        return range(max(0, start), min(n - 1, stop) + 1)

    async def zrange(self, key: str, start: int, stop: int) -> list[str]:
        items = self._ordered(key)
        return [items[i] for i in self._span(len(items), start, stop)]

    async def zremrangebyrank(self, key: str, start: int, stop: int) -> int:
        items = self._ordered(key)
        gone = [items[i] for i in self._span(len(items), start, stop)]
        for m in gone:
            del self.z[key][m]
        return len(gone)

    async def zremrangebyscore(self, key: str, lo: Any, hi: float) -> int:
        gone = [m for m, s in self.z.get(key, {}).items() if s <= hi]
        for m in gone:
            del self.z[key][m]
        return len(gone)


class _Cache:
    def __init__(self, redis: _Redis, services: list[dict[str, Any]]) -> None:
        self.redis = redis
        self.services = {int(s["id"]): s for s in services}

    async def list_service_ids(self) -> list[int]:
        return sorted(self.services)

    async def get_service(self, sid: int) -> dict[str, Any] | None:
        return self.services.get(sid)


class _Gateway:
    def __init__(self, handler: Any = None) -> None:
        self.calls: list[tuple[float, str, dict[str, Any], float | None]] = []
        self.handler = handler or _health_ok
        self.clock: _Clock | None = None

    async def call_tool(self, tool: str, args: dict[str, Any], *,
                        timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((self.clock.t if self.clock else 0.0, tool, args, timeout))
        result: dict[str, Any] = self.handler(tool, args)
        return result


class _Exec:
    def __init__(self, stat: str | None = "2026110514", clock: _Clock | None = None,
                 advance: float = 0.0) -> None:
        self.calls: list[tuple[str, str]] = []
        self.stat = stat
        self.clock = clock
        self.advance = advance
        self.guard = SQLGuard()

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        ok, reason = self.guard.is_safe_select(sql)
        assert ok and sql.lstrip().startswith("SELECT"), reason
        self.calls.append((db_id, sql))
        if self.clock is not None:
            self.clock.t += self.advance
        if "MAX(s.stat_date)" in sql:
            return [{"stat_date": self.stat}]
        ids = [int(x) for x in sql.split("IN (")[1].split(")")[0].split(", ")]
        return [{"server_id": i, "resource_type": "server.Cpus", "definition_name": "Utilization",
                 "avg_val": 50.0 + i % 10} for i in ids]


class _Repo:
    def __init__(self) -> None:
        self.logs: list[tuple[int, str | None, str, dict[str, Any]]] = []

    async def add_state_log(self, sid: int, *, from_state: str | None, to_state: str,
                            reason: dict[str, Any] | None = None) -> dict[str, Any]:
        self.logs.append((sid, from_state, to_state, dict(reason or {})))
        return {}


def _inst(source: str, domain: str, name: str) -> dict[str, Any]:
    return {
        "kind": KIND_APM_INSTANCE, "source_id": source, "domain_id": domain,
        "instance_name": name,
        "member_key": apm_instance_member_key(source, domain, SCOPE_INSTANCE,
                                              match_key_for(SCOPE_INSTANCE, name)),
    }


def _sms(db_id: str, rid: int) -> dict[str, Any]:
    return {"kind": KIND_SMS_SERVER, "db_id": db_id, "resource_id": str(rid),
            "member_key": sms_member_key(db_id, rid)}


def _svc(sid: int, members: list[dict[str, Any]], monitored: bool = True) -> dict[str, Any]:
    return {"id": sid, "monitored": monitored, "members": members}


def _health_ok(tool: str, args: dict[str, Any], signal_index: int | None = None) -> dict[str, Any]:
    """targets 항목마다 같은 도메인 행 1개(+ 원하면 신호) — 실 배치 봉투 모양."""
    if tool == c.APM_BUSINESS_TOOL:
        return {"rows": [{"source_id": "s1", "domain_id": 7, "business_id": 31, "tps": 4.0,
                          "response_time_avg_ms": 90.0, "active_services": 1}]}
    rows, batch = [], []
    for i, t in enumerate(args["targets"]):
        domain = t["instance_name"].split("-")[1]
        rows.append({"source_id": t["source_id"], "instance_id": 100 + i, "domain_id": int(domain),
                     "instance_name": t["instance_name"], "response_time_avg_ms": 120.0,
                     "tps": 3.5, "active_services": 2, "target_index": i,
                     "window": {"calls": 40, "errors": 1, "error_rate": 0.025}})
        sigs = []
        if signal_index == i:
            sigs = [{"kind": "was_service_queuing", "level": "CRITICAL", "instance_id": 100 + i,
                     "source_id": t["source_id"]}]
        batch.append({"index": i, "status": "ok", "row_count": 1, "was_signals": sigs})
    return {"rows": rows, "batch": batch, "row_count": len(rows), "total_row_count": len(rows)}


def _settings(**sms: Any) -> BizServiceSettings:
    thresholds = {"cpu": SmsThreshold(80.0, 90.0)}
    return BizServiceSettings(BizServiceConfig(sms_thresholds=thresholds), frozenset(), (),
                              sms.get("sources", {}))


def _collector(
    services: list[dict[str, Any]],
    *,
    gateway: Any = None,
    execute: Any = None,
    settings: BizServiceSettings | None = None,
    redis: _Redis | None = None,
    clock: _Clock | None = None,
    owner: str = "me",
    viewers: bool = True,
    **kw: Any,
) -> tuple[c.BizMonitorCollector, _Redis, _Clock]:
    redis = redis or _Redis()
    clock = clock or _Clock()
    if viewers:
        redis.kv[VIEWERS_KEY] = "x"
    if isinstance(gateway, _Gateway):
        gateway.clock = clock
    col = c.BizMonitorCollector(
        redis=redis, cache=_Cache(redis, services), gateway=gateway, execute=execute,
        settings=settings or _settings(), engine_of=lambda db: "db2" if db == "polestar_b0"
        else "postgresql",
        lock=WorkerSingletonLock(redis, key=c.LEADER_KEY, owner=owner),
        monotonic=clock.mono, wall=clock.wall, **kw,
    )
    return col, redis, clock


def _value(redis: _Redis, sid: int) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(redis.kv[f"bizmon:svc:{sid}"])
    return value


def _status(redis: _Redis) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(redis.kv[MONITOR_STATUS_KEY])
    return value


# ── 수요 · 대상 ─────────────────────────────────────────────────────────
async def test_no_viewers_means_zero_calls() -> None:
    gw, ex = _Gateway(), _Exec()
    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7"), _sms("polestar_cm_gp", 5)])],
                               gateway=gw, execute=ex, viewers=False,
                               settings=_settings(sources=SOURCES))
    try:
        assert await col.run_once() == c.RUN_IDLE
    finally:
        await col.aclose()
    assert gw.calls == [] and ex.calls == []
    assert not any(k.startswith("bizmon:svc:") for k in redis.kv)
    assert MONITOR_STATUS_KEY not in redis.kv


async def test_unmonitored_services_are_not_collected() -> None:
    gw = _Gateway()
    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7")], monitored=False)], gateway=gw)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert gw.calls == [] and "bizmon:svc:1" not in redis.kv


async def test_max_services_in_id_order_with_reason() -> None:
    gw = _Gateway()
    services = [_svc(i, [_inst("s1", "7", f"i{i}-7")]) for i in (5, 3, 9)]
    col, redis, _ = _collector(services, gateway=gw, max_services=2)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert {"bizmon:svc:3", "bizmon:svc:5"} <= set(redis.kv) and "bizmon:svc:9" not in redis.kv
    assert any("감시 서비스 상한 2 초과 — 1개" in r for r in _status(redis)["reasons"])


async def test_apm_120_targets_cap_100_one_call_per_source() -> None:
    gw = _Gateway()
    s1 = [_inst("s1", "7", f"a{i:03d}-7") for i in range(70)]
    s2 = [_inst("s2", "8", f"b{i:03d}-8") for i in range(50)]
    col, redis, _ = _collector([_svc(1, s1), _svc(2, s2)], gateway=gw)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert [(tool, len(a["targets"])) for _, tool, a, _ in gw.calls] == [
        (c.APM_HEALTH_TOOL, 70), (c.APM_HEALTH_TOOL, 30)]
    for _, _, args, timeout in gw.calls:
        assert args["owner"] == "biz_monitor" and args["lookback_minutes"] == 5
        assert "xview_confirm_over" not in args and "reference_time" not in args
        assert 0 < args["wait_seconds"] < timeout <= c.APM_BUDGET_SECONDS
    assert [{t["source_id"] for t in a["targets"]} for _, _, a, _ in gw.calls] == [{"s1"}, {"s2"}]
    v2 = _value(redis, 2)
    assert any("일부만 감시 — APM 인스턴스 상한 100 초과로 20개 제외" in f
               for f in v2["fetch_failures"])
    assert len({x["member_key"] for x in v2["apm_values"]}) == 30
    assert not any("일부만" in f for f in _value(redis, 1)["fetch_failures"])
    status = _status(redis)
    assert status["state"] == "partial"
    assert any("일부만 감시 — APM 인스턴스 상한 100 초과(제외 20개)" in r
               for r in status["reasons"])


async def test_values_signals_round_trip_with_w6_reader_and_transition() -> None:
    gw = _Gateway(lambda tool, args: _health_ok(tool, args, signal_index=1))
    repo = _Repo()
    members = [_inst("s1", "7", "a-7"), _inst("s1", "7", "b-7")]
    col, redis, _ = _collector([_svc(1, members)], gateway=gw, repo=repo)
    try:
        assert await col.run_once() == c.RUN_COLLECTED
    finally:
        await col.aclose()
    got = (await read_monitor_values(redis, [1]))[1]
    assert got.in_window is True and got.fetch_failures == ()
    assert [(s.member_key, s.signal, s.level) for s in got.apm_signals] == [
        (members[1]["member_key"], "was_service_queuing", "CRITICAL")]
    names = {(v.member_key, v.name): v.value for v in got.apm_values}
    assert names[(members[0]["member_key"], "response_time_avg_ms")] == 120.0
    assert names[(members[0]["member_key"], "error_rate")] == 0.025
    assert redis.ttl["bizmon:svc:1"] == 180  # 3 × APM 주기
    j = judge_service_state(monitored=True, apm_signals=got.apm_signals,
                            apm_values=got.apm_values)
    assert j.state == STATE_RISK
    assert [(sid, f, t) for sid, f, t, _ in repo.logs] == [(1, None, STATE_RISK)]
    assert repo.logs[0][3]["evidence"][0]["rule"] == "apm_signal"
    status = await read_metrics_status(redis, active=True)
    assert status["state"] == "collecting" and status["last_success_at"]


async def test_instance_found_only_in_other_domain_is_a_failure() -> None:
    def handler(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        env = _health_ok(tool, args)
        for r in env["rows"]:
            r["domain_id"] = 99
        return env

    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=_Gateway(handler))
    try:
        await col.run_once()
    finally:
        await col.aclose()
    v = _value(redis, 1)
    assert v["apm_values"] == [] and v["in_window"] is False
    assert any("APM 인스턴스 미발견 — a-7(도메인 7)" in f for f in v["fetch_failures"])


async def test_business_member_one_call_per_domain_without_names() -> None:
    gw = _Gateway()
    biz = {"kind": KIND_APM_BUSINESS, "member_key": apm_business_member_key("s1", "7", "31")}
    biz2 = {"kind": KIND_APM_BUSINESS, "member_key": apm_business_member_key("s1", "7", "32")}
    col, redis, _ = _collector([_svc(1, [biz, biz2])], gateway=gw)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert [(tool, a["mode"], a["domain_id"], a["source_ids"], "business" in a)
            for _, tool, a, _ in gw.calls] == [(c.APM_BUSINESS_TOOL, "current", 7, ["s1"], False)]
    v = _value(redis, 1)
    assert {x["name"]: x["value"] for x in v["apm_values"]}["tps"] == 4.0
    assert any("업무 32 현재값 없음" in f for f in v["fetch_failures"])


async def test_gateway_missing_reason_and_no_calls() -> None:
    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=None)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert c.REASON_GATEWAY_UNSET in _value(redis, 1)["fetch_failures"]
    assert _status(redis)["state"] == "failed"


# ── 실패 · 백오프 · 예산 ────────────────────────────────────────────────
async def test_gateway_failure_status_failed_keeps_last_success() -> None:
    mode = {"fail": False}

    def handler(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        if mode["fail"]:
            raise RuntimeError("게이트웨이 연결 끊김")
        return _health_ok(tool, args)

    col, redis, clock = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=_Gateway(handler))
    try:
        await col.run_once()
        first = _status(redis)["last_success_at"]
        mode["fail"] = True
        clock.t += 60
        await col.run_once()
    finally:
        await col.aclose()
    status = await read_metrics_status(redis, active=True)
    assert status["state"] == "failed" and status["label"] == "조회 실패"
    assert status["last_success_at"] == first is not None
    assert any("APM s1 조회 실패 — RuntimeError: 게이트웨이 연결 끊김" in r
               for r in status["reasons"])
    v = _value(redis, 1)
    assert v["in_window"] is False and v["apm_values"] == []


async def test_error_envelope_counts_as_failure() -> None:
    gw = _Gateway(lambda tool, args: {"error": "source_unavailable", "reason": "뷰서버 응답 없음"})
    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=gw)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert any("source_unavailable: 뷰서버 응답 없음" in f
               for f in _value(redis, 1)["fetch_failures"])


async def test_backoff_doubles_up_to_ten_minutes() -> None:
    def handler(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError("게이트웨이 호출 타임아웃")

    gw = _Gateway(handler)
    col, redis, clock = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=gw)
    start = clock.t
    try:
        for _ in range(36):  # 60초 주기 × 36 = 2,100초
            await col.run_once()
            clock.t += 60
    finally:
        await col.aclose()
    assert [t - start for t, *_ in gw.calls] == [0, 120, 360, 840, 1440, 2040]
    assert any("백오프 중" in f and "마지막 성공 없음" in f
               for f in _value(redis, 1)["fetch_failures"])


async def test_budget_too_small_skips_without_touching_backoff() -> None:
    gw = _Gateway()
    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=gw, apm_budget=2.5)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert gw.calls == []
    assert any("APM 시간 예산 2.5초 초과 · 이번 주기 건너뜀" in f
               for f in _value(redis, 1)["fetch_failures"])
    assert col._backoff == {}  # 예산 건너뜀은 실패가 아니다
    assert _status(redis)["state"] == "partial"


async def test_unfinished_gateway_job_is_budget_skip_and_keeps_failure_count() -> None:
    state = {"n": 0}

    def handler(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("끊김")
        return {"job": {"job_id": "j1", "state": "running"}}

    col, redis, clock = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=_Gateway(handler))
    try:
        await col.run_once()  # 실패 1회 → 120초 백오프
        clock.t += 120
        await col.run_once()  # 작업 미완 → 예산 건너뜀
    finally:
        await col.aclose()
    assert col._backoff["apm:s1"].failures == 1
    assert any("게이트웨이 작업이 APM 시간 예산 안에 끝나지 않음" in f
               for f in _value(redis, 1)["fetch_failures"])


# ── SMS ─────────────────────────────────────────────────────────────────
async def test_sms_source_unset_means_zero_select_with_reason() -> None:
    ex = _Exec()
    col, redis, _ = _collector([_svc(1, [_sms("polestar_cm_gp", 5)])], gateway=_Gateway(),
                               execute=ex, settings=_settings(sources={}))
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert ex.calls == []
    assert c.REASON_SMS_UNSET in _value(redis, 1)["fetch_failures"]
    status = await read_metrics_status(redis, active=True)
    assert c.REASON_SMS_UNSET in status["reasons"] and status["state"] == "partial"


async def test_same_stat_date_skips_metric_rows() -> None:
    clock = _Clock()
    ex = _Exec(clock=clock)
    col, redis, _ = _collector([_svc(1, [_sms("polestar_cm_gp", 5), _sms("polestar_cm_gp", 6)])],
                               execute=ex, settings=_settings(sources=SOURCES), clock=clock)
    try:
        await col.run_once()
        assert len(ex.calls) == 2  # 확인 1 + 지표 행 1
        clock.t += 60
        await col.run_once()  # SMS 확인 주기 전 — 조회 0
        assert len(ex.calls) == 2
        clock.t += 600
        await col.run_once()  # 확인만 · 같은 정각 → 지표 행 생략
    finally:
        await col.aclose()
    assert len(ex.calls) == 3 and "MAX(s.stat_date)" in ex.calls[2][1]
    v = (await read_monitor_values(redis, [1]))[1]
    assert v.sms_stat_time == "2026-11-05 14:00"
    assert {(s.member_key, s.metric): s.value for s in v.sms_metrics} == {
        ("polestar_cm_gp|5", "cpu"): 55.0, ("polestar_cm_gp|5", "memory"): None,
        ("polestar_cm_gp|6", "cpu"): 56.0, ("polestar_cm_gp|6", "memory"): None,
    }


async def test_new_stat_date_reads_rows_again() -> None:
    clock = _Clock()
    ex = _Exec(clock=clock)
    col, _, _ = _collector([_svc(1, [_sms("polestar_cm_gp", 5)])], execute=ex,
                           settings=_settings(sources=SOURCES), clock=clock)
    try:
        await col.run_once()
        ex.stat = "2026110515"
        clock.t += 600
        await col.run_once()
    finally:
        await col.aclose()
    assert ["GROUP BY" in sql for _, sql in ex.calls] == [False, True, False, True]


async def test_sms_chunks_of_500_and_dialect_per_zone() -> None:
    ex = _Exec()
    members = [_sms("polestar_b0", i) for i in range(1, 602)] + [_sms("polestar_cm_gp", 9)]
    col, _, _ = _collector([_svc(1, members)], execute=ex, settings=_settings(sources=SOURCES))
    try:
        await col.run_once()
    finally:
        await col.aclose()
    checks = [(db, sql) for db, sql in ex.calls if "MAX(s.stat_date)" in sql]
    assert [db for db, _ in checks] == ["polestar_b0", "polestar_b0", "polestar_cm_gp"]
    for db, sql in ex.calls:
        n = len(sql.split("IN (")[1].split(")")[0].split(", "))
        assert n <= 500
        if db == "polestar_b0":
            assert "POLESTAR.cmm_metric_stat_h" in sql and "LIMIT" not in sql
        else:
            assert "polestar.cmm_metric_stat_h" in sql and "FETCH FIRST" not in sql
    rows_b0 = [sql for db, sql in ex.calls if db == "polestar_b0" and "GROUP BY" in sql]
    assert rows_b0[0].rstrip().endswith("FETCH FIRST 1000 ROWS ONLY")  # 500 서버 × 지표 2


async def test_sms_budget_exceeded_skips_rest_with_reason() -> None:
    clock = _Clock()
    ex = _Exec(clock=clock, advance=59.0)
    members = [_sms("polestar_b0", 1), _sms("polestar_cm_gp", 2)]
    col, redis, _ = _collector([_svc(1, members)], execute=ex,
                               settings=_settings(sources=SOURCES), clock=clock)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert [db for db, _ in ex.calls] == ["polestar_b0"]  # 확인 1건에 예산 대부분 소진
    reasons = _status(redis)["reasons"]
    assert any("SMS polestar_b0 — SMS 시간 예산 60초 초과" in r for r in reasons)
    assert any("SMS polestar_cm_gp — SMS 시간 예산 60초 초과" in r for r in reasons)
    assert all(bo.failures == 0 for bo in col._backoff.values())  # 예산 건너뜀은 실패가 아니다


async def test_sms_failure_backs_off_and_is_shown() -> None:
    class _Bad(_Exec):
        async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
            self.calls.append((db_id, sql))
            raise LookupError("활성 DB가 아닙니다")

    ex = _Bad()
    col, redis, clock = _collector([_svc(1, [_sms("polestar_cm_gp", 5)])], execute=ex,
                                   settings=_settings(sources=SOURCES), sms_interval=300)
    try:
        await col.run_once()
        clock.t += 300
        await col.run_once()  # 백오프 ×2 = 600초 — 이번 확인은 건너뜀
        assert any("SMS polestar_cm_gp 조회 실패 · 연속 1회 — 백오프 중" in f
                   for f in _value(redis, 1)["fetch_failures"])
        clock.t += 300
        await col.run_once()
    finally:
        await col.aclose()
    assert len(ex.calls) == 2
    v = _value(redis, 1)
    assert any("SMS polestar_cm_gp 조회 실패 — LookupError" in f for f in v["fetch_failures"])
    assert _status(redis)["state"] == "failed"


# ── 잠금 · 전이 · 시계열 ────────────────────────────────────────────────
async def test_two_collectors_only_leader_collects() -> None:
    redis = _Redis()
    gw_a, gw_b = _Gateway(), _Gateway()
    services = [_svc(1, [_inst("s1", "7", "a-7")])]
    a, _, _ = _collector(services, gateway=gw_a, redis=redis, owner="host-a:1")
    b, _, _ = _collector(services, gateway=gw_b, redis=redis, owner="host-b:2")
    try:
        assert await a.run_once() == c.RUN_COLLECTED
        assert await b.run_once() == c.RUN_NOT_LEADER
        assert redis.kv[c.LEADER_KEY] == "host-a:1"
    finally:
        await b.aclose()
        assert redis.kv[c.LEADER_KEY] == "host-a:1"  # 남의 잠금은 지우지 않는다
        await a.aclose()
    assert len(gw_a.calls) == 1 and gw_b.calls == []
    assert c.LEADER_KEY not in redis.kv


async def test_state_transitions_logged_only_on_change_and_survive_restart() -> None:
    sig = {"on": True}
    gw = _Gateway(lambda tool, args: _health_ok(tool, args, 0 if sig["on"] else None))
    repo = _Repo()
    services = [_svc(1, [_inst("s1", "7", "a-7")])]
    col, redis, clock = _collector(services, gateway=gw, repo=repo)
    try:
        await col.run_once()
        clock.t += 60
        await col.run_once()  # 그대로 위험 — 기록 없음
        sig["on"] = False
        clock.t += 60
        await col.run_once()  # 위험 → 이상 신호 없음
    finally:
        await col.aclose()
    assert [(f, t) for _, f, t, _ in repo.logs] == [
        (None, STATE_RISK), (STATE_RISK, STATE_NO_SIGNAL)]
    # 새 프로세스(리더 교대) — 시계열 마지막 점에서 직전 상태를 잇는다
    col2, _, _ = _collector(services, gateway=gw, repo=repo, redis=redis, clock=clock)
    try:
        clock.t += 60
        await col2.run_once()
    finally:
        await col2.aclose()
    assert len(repo.logs) == 2


async def test_incident_from_alarm_snapshot_drives_state() -> None:
    member = _inst("s1", "7", "a-7")

    async def snap() -> AlarmSnapshot:
        fact = AlarmFact(kind=FACT_INCIDENT, alarm_id="x", db_id="polestar_cm_gp", severity=3,
                         tier="page", at=None, service_id=1, member_key=member["member_key"],
                         incident_id="inc-1")
        return AlarmSnapshot(computed_at=0.0, window_minutes=60, facts=(fact,))

    repo = _Repo()
    col, _, _ = _collector([_svc(1, [member])], gateway=_Gateway(), repo=repo, alarm_snapshot=snap)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert repo.logs[0][2] == STATE_RISK
    assert repo.logs[0][3]["evidence"][0]["rule"] == "incident_critical"


async def test_alarm_snapshot_failure_skips_transition_with_reason() -> None:
    async def snap() -> AlarmSnapshot:
        raise RuntimeError("사건 저장소 끊김")

    repo = _Repo()
    col, redis, _ = _collector([_svc(1, [_inst("s1", "7", "a-7")])], gateway=_Gateway(),
                               repo=repo, alarm_snapshot=snap)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert repo.logs == [] and c.REASON_SNAPSHOT_FAILED in _status(redis)["reasons"]
    point = json.loads((await redis.zrange("bizmon:ts:1", -1, -1))[0])
    assert point["state"] is None


async def test_timeseries_capped_at_1440_points_and_24h() -> None:
    col, redis, clock = _collector([_svc(1, [])], gateway=_Gateway())
    try:
        for _ in range(1500):
            await col.run_once()
            clock.t += 60
        assert len(redis.z["bizmon:ts:1"]) == c.TS_MAX_POINTS
        assert redis.ttl["bizmon:ts:1"] == 86400
        clock.t += 2 * 86400
        await col.run_once()
    finally:
        await col.aclose()
    assert len(redis.z["bizmon:ts:1"]) == 1
    point = json.loads(next(iter(redis.z["bizmon:ts:1"])))
    assert set(point) == {"at", "state", "sms_max", "apm_max"}


async def test_sms_thresholds_used_for_state() -> None:
    clock = _Clock()
    ex = _Exec(clock=clock)
    settings = replace(_settings(sources={"cpu": SOURCES["cpu"]}),
                       config=BizServiceConfig(sms_thresholds={"cpu": SmsThreshold(50.0, 55.0)}))
    repo = _Repo()
    col, _, _ = _collector([_svc(1, [_sms("polestar_cm_gp", 5)])], execute=ex, settings=settings,
                           clock=clock, repo=repo)
    try:
        await col.run_once()
    finally:
        await col.aclose()
    assert repo.logs[0][2] == STATE_RISK and repo.logs[0][3]["evidence"][0]["rule"] == "sms_crit"
