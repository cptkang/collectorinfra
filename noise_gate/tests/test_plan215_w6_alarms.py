"""plans/215 W6 — M2 업무 서비스 알람 집계 · 현황 보기 조립.

사본은 실 `RedisBizServiceCache`(W2)에 가짜 Redis(`FakeAsyncRedis` + `hmget`·`sadd` 큐잉)를
붙여 쓰고,
열린 사건·결정 이력은 호출을 세는 가짜 저장소다. 귀속 순서 · 조회 추가 0 · 10초 메모 · 존 필터 ·
「서비스 미상 · 트리 그룹 추정」 집계 불포함 · §14 M2·§4.4 벡터 · 「정상」 어휘 0을 본다.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis, FakePipeline

from noise_gate.application import biz_monitor_alarms as m
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, match_key_for
from noise_gate.domain.biz_service_keys import apm_domain_member_key, apm_instance_member_key
from noise_gate.domain.biz_service_model import SmsThreshold
from noise_gate.infrastructure.biz_service_cache import RedisBizServiceCache

ZA, ZB = "zone_a", "zone_b"
INST = "inst-a"
INST_KEY = apm_instance_member_key(
    "src1", "10", SCOPE_INSTANCE, match_key_for(SCOPE_INSTANCE, INST)
)
DOM_KEY = apm_domain_member_key("src1", "20")
NOW = datetime.now(UTC)


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


class _Decisions:
    enabled = True

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls: list[int] = []

    def iter_recent_decisions(self, window_seconds: int) -> Any:
        self.calls.append(window_seconds)
        return iter(self.records)


class _Incidents:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls = 0

    async def list_open(self, *, limit: int = 100) -> list[dict[str, Any]]:
        self.calls += 1
        return self.rows[:limit]


def _sms(mid: int, db: str, rid: str, host: str, zone: str = ZA) -> dict[str, Any]:
    return {"id": mid, "kind": "sms_server", "member_key": f"{db}|{rid}", "db_id": db,
            "resource_id": rid, "hostname": host, "server_name": host, "zone": zone}


def _svc(sid: int, name: str, members: list[dict[str, Any]], *, tier: int | None = 2,
         key: str = "", monitored: bool = False) -> dict[str, Any]:
    return {"id": sid, "service_key": key or f"svc{sid}", "display_name": name, "tier": tier,
            "state": "active", "monitored": monitored, "verified": False, "members": members}


def _services() -> list[dict[str, Any]]:
    return [
        _svc(1, "여신", [_sms(11, "pg", "101", "loan-01")], tier=1),
        _svc(2, "수신", [{"id": 21, "kind": "apm_instance", "member_key": INST_KEY,
                          "source_id": "src1", "domain_id": "10", "instance_name": INST,
                          "zone": ZA}]),
        _svc(3, "카드", [{"id": 31, "kind": "apm_domain", "member_key": DOM_KEY,
                          "source_id": "src1", "domain_id": "20", "zone": ZA}]),
        _svc(4, "인터넷뱅킹", [_sms(41, "pg", "401", "IBKWEB01")], key="tree:인터넷뱅킹"),
    ]


def _ts(minutes_ago: int = 1) -> str:
    return datetime.fromtimestamp(NOW.timestamp() - minutes_ago * 60, UTC).isoformat()


def _rec(alarm_id: str, *, tier: str = "page", sev: Any = 2, **extra: Any) -> dict[str, Any]:
    return {"ts": _ts(), "alarm_id": alarm_id, "tier": tier, "signals": {"severity": sev},
            "db_id": "pg", **extra}


LINK = {"status": "linked", "db_id": "pg", "resource_id": "101"}
APM_INST = {"source_id": "src1", "domain_id": "10", "instance_name": INST}
APM_DOM = {"source_id": "src1", "domain_id": "20", "instance_name": "no-such"}


async def _cache(services: list[dict[str, Any]] | None = None) -> RedisBizServiceCache:
    cache = RedisBizServiceCache(_Redis())
    await cache.sync_from_repo(_Repo(services if services is not None else _services()))
    return cache


def _settings(thresholds: dict[str, SmsThreshold] | None = None) -> Any:
    return SimpleNamespace(config=SimpleNamespace(sms_thresholds=thresholds or {}),
                           generic_terms=frozenset({"servers"}))


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _agg(cache: Any, records: list[dict[str, Any]], incidents: list[dict[str, Any]] | None = None,
         *, thresholds: dict[str, SmsThreshold] | None = None,
         clock: _Clock | None = None) -> tuple[m.BizAlarmAggregator, _Decisions, _Incidents]:
    dec, inc = _Decisions(records), _Incidents(incidents or [])
    agg = m.BizAlarmAggregator(
        cache=cache, incident_store=inc, decision_store=dec, window_minutes=60,
        monotonic=clock or _Clock(), settings_loader=lambda: _settings(thresholds),
    )
    return agg, dec, inc


def _by_alarm(snap: m.AlarmSnapshot) -> dict[str, m.AlarmFact]:
    return {f.alarm_id: f for f in snap.facts if f.kind == m.FACT_RECENT}


def _board(snap: m.AlarmSnapshot, scope: Any = None, **kw: Any) -> dict[str, Any]:
    args: dict[str, Any] = {"monitor_values": {}, "metrics_status": {"state": "off"},
                            "metrics_active": False, "thresholds": {}}
    args.update(kw)
    return m.build_board(snap, scope=scope, **args)


# ── 귀속 순서 ───────────────────────────────────────────────────────────
def test_attribution_order_first_match_wins() -> None:
    """폴스타 연계 → APM 인스턴스 → APM 도메인 → 호스트 → 미상(§4.2 · 첫 성립에서 멈춤)."""
    records = [
        _rec("a1", polestar_link=LINK, apm_ref=APM_INST, hostname="IBKWEB01"),
        _rec("a2", apm_ref=APM_INST, hostname="IBKWEB01"),
        _rec("a3", apm_ref=APM_DOM, hostname="IBKWEB01"),
        _rec("a4", hostname="IBKWEB01"),
        _rec("a5", hostname="nobody-99"),
    ]

    async def run() -> m.AlarmSnapshot:
        agg, _, _ = _agg(await _cache(), records)
        return await agg.snapshot()

    facts = _by_alarm(asyncio.run(run()))
    got = {k: (f.service_id, f.basis) for k, f in facts.items()}
    assert got == {
        "a1": (1, "polestar_link"),
        "a2": (2, "apm_instance"),
        "a3": (3, "apm_domain"),
        "a4": (4, "host"),
        "a5": (None, ""),
    }
    assert facts["a1"].member_key == "pg|101"
    assert facts["a2"].member_key == INST_KEY
    assert facts["a3"].member_key == DOM_KEY
    assert facts["a4"].member_key == "pg|401"


def test_m2_vector_host_index_single_server() -> None:
    """§14 M2 — `polestar_link` 없음 · 호스트 `IBKWEB01` → 색인 1대 → 인터넷뱅킹."""

    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), [_rec("x", hostname="ibkweb01")])
        return _board(await agg.snapshot())

    body = asyncio.run(run())
    item = next(s for s in body["services"] if s["id"] == 4)
    assert item["display_name"] == "인터넷뱅킹"
    assert item["alarms"]["recent_total"] == 1
    assert body["unattributed"]["recent_alarms"] == 0


def test_sms_db_alarm_host_attributes_only_within_same_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SMS DB 알람(레지스트리 등록 db_id)은 같은 db_id 구성원으로만 호스트 귀속 — 다른 DB의 같은
    이름 서버 알람은 미상이다(같은 존이어도). APM 기록은 db_id를 보지 않는다(현행 1대 규칙)."""
    monkeypatch.setattr(m, "sms_db_of", lambda db_id: db_id if db_id in {"pg", "pg2"} else "")
    records = [
        _rec("same", hostname="IBKWEB01"),
        _rec("other", hostname="IBKWEB01", db_id="pg2"),
        _rec("apm", hostname="IBKWEB01", db_id="jennifer_x"),
    ]

    async def run() -> m.AlarmSnapshot:
        agg, _, _ = _agg(await _cache(), records)
        return await agg.snapshot()

    facts = _by_alarm(asyncio.run(run()))
    got = {k: (f.service_id, f.basis, f.member_key) for k, f in facts.items()}
    assert got == {
        "same": (4, "host", "pg|401"),
        "other": (None, "", ""),
        "apm": (4, "host", "pg|401"),
    }


def test_identity_carries_sms_db_only_for_non_apm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "sms_db_of", lambda db_id: db_id)
    assert m._identity({"db_id": "pg", "hostname": "h"})[3] == "pg"
    assert m._identity({"db_id": "pg", "hostname": "h", "apm_ref": APM_INST})[3] == ""
    assert m._identity({"db_id": "jennifer_gp", "hostname": "h"})[3] == ""


def test_member_of_host_never_picks_among_two_in_same_db() -> None:
    """호스트 근거 구성원 — 그 DB 안에서 맞는 서버가 2대면 고르지 않는다(D-224 ④)."""
    svc = _svc(9, "x", [_sms(1, "pg", "1", "app-01"), _sms(2, "pg", "2", "app_01"),
                        _sms(3, "pg2", "3", "app01")])
    assert m._member_of(svc, "host", ((), None, "APP01", "pg")) is None
    hit = m._member_of(svc, "host", ((), None, "APP01", "pg2"))
    assert hit is not None and hit["member_key"] == "pg2|3"
    assert m._member_of(svc, "host", ((), None, "APP01", "pg3")) is None


def test_two_services_link_is_unknown() -> None:
    """연계 대상이 서로 다른 서비스 2개에 걸리면 고르지 않는다(D-224 ④) — 미상."""
    link = {**LINK, "targets": [{"db_id": "pg", "resource_id": "401"}]}

    async def run() -> m.AlarmSnapshot:
        agg, _, _ = _agg(await _cache(), [_rec("z", polestar_link=link)])
        return await agg.snapshot()

    assert _by_alarm(asyncio.run(run()))["z"].service_id is None


def test_incident_joins_decision_record_and_falls_back_to_server_name() -> None:
    """열린 사건은 같은 alarm_id 결정 기록으로 귀속 · 없으면 사건 server_name을 호스트로."""
    incidents = [
        {"id": 7, "alarm_id": "a1", "db_id": "pg", "server_name": "?", "severity": 3,
         "tier": "page", "created_at": _ts(5)},
        {"id": 8, "alarm_id": "old", "db_id": "pg", "server_name": "IBKWEB01", "severity": 1,
         "tier": "ticket", "created_at": _ts(500)},
    ]

    async def run() -> m.AlarmSnapshot:
        agg, _, _ = _agg(await _cache(), [_rec("a1", polestar_link=LINK)], incidents)
        return await agg.snapshot()

    inc = {f.incident_id: f for f in asyncio.run(run()).facts if f.kind == m.FACT_INCIDENT}
    assert (inc["7"].service_id, inc["7"].severity) == (1, 3)
    assert (inc["8"].service_id, inc["8"].basis) == (4, "host")


# ── 조회 추가 0 · 메모 ─────────────────────────────────────────────────
def test_memo_ten_seconds_no_store_calls() -> None:
    """10초 안 재요청은 저장소·사본 호출 0 · 동시 요청도 1회 · 10초 뒤 다시 계산."""
    clock = _Clock()

    async def run() -> tuple[_Decisions, _Incidents, list[int]]:
        cache = await _cache()
        attribute_calls: list[int] = []
        original = cache.attribute

        async def counted(**kw: Any) -> Any:
            attribute_calls.append(1)
            return await original(**kw)

        cache.attribute = counted  # type: ignore[method-assign]
        agg, dec, inc = _agg(cache, [_rec("a4", hostname="IBKWEB01")], clock=clock)
        await asyncio.gather(agg.snapshot(), agg.snapshot(), agg.snapshot())
        assert (len(dec.calls), inc.calls, len(attribute_calls)) == (1, 1, 1)
        clock.t += 9.9
        await agg.snapshot()
        assert (len(dec.calls), inc.calls, len(attribute_calls)) == (1, 1, 1)
        clock.t += 0.2
        await agg.snapshot()
        return dec, inc, attribute_calls

    dec, inc, calls = asyncio.run(run())
    assert (len(dec.calls), inc.calls, len(calls)) == (2, 2, 2)
    assert dec.calls[0] == 60 * 60   # 창 60분


def test_attribution_deduplicated_per_identity() -> None:
    """같은 식별 입력은 한 번만 귀속을 묻는다(사본 왕복 최소)."""

    async def run() -> int:
        cache = await _cache()
        calls: list[int] = []
        original = cache.attribute

        async def counted(**kw: Any) -> Any:
            calls.append(1)
            return await original(**kw)

        cache.attribute = counted  # type: ignore[method-assign]
        agg, _, _ = _agg(cache, [_rec(f"r{i}", hostname="IBKWEB01") for i in range(5)])
        await agg.snapshot()
        return len(calls)

    assert asyncio.run(run()) == 1


def test_module_has_no_observation_clients() -> None:
    """관측 호출 0 — MCP·게이트웨이·HTTP 클라이언트를 import하지 않는다(정적 단언)."""
    src = Path(m.__file__).read_text(encoding="utf-8")
    imports = "\n".join(line for line in src.splitlines() if re.match(r"\s*(from|import) ", line))
    for banned in ("mcp", "httpx", "apm_gateway", "gateway_client", "dbhub", "asyncpg"):
        assert banned not in imports, banned


# ── 서비스 미상 · 트리 그룹 추정 ─────────────────────────────────────────
def test_unknown_tree_group_estimate_is_display_only() -> None:
    rec = _rec("u1", hostname="svr-x", server_name="svr-x",
               resource_ancestry="/Servers/인터넷뱅킹/svr-x/Cpus")

    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), [rec])
        return _board(await agg.snapshot())

    body = asyncio.run(run())
    assert body["unattributed"]["recent_alarms"] == 1
    assert body["unattributed"]["tree_estimates"] == [
        {"service_id": 4, "display_name": "인터넷뱅킹", "recent_alarms": 1}
    ]
    item = next(s for s in body["services"] if s["id"] == 4)
    assert item["alarms"]["recent_total"] == 0   # 집계에 넣지 않는다


def test_tree_estimate_needs_server_segment() -> None:
    rec = _rec("u2", hostname="svr-y", resource_ancestry="/Servers/인터넷뱅킹/other/Cpus")

    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), [rec])
        return _board(await agg.snapshot())

    assert asyncio.run(run())["unattributed"]["tree_estimates"] == []


# ── 집계 칸 ─────────────────────────────────────────────────────────────
def test_counts_by_severity_and_handling() -> None:
    records = [
        _rec("t1", tier="page", sev=3, hostname="loan-01"),
        _rec("t2", tier="ticket", sev=2, hostname="loan-01"),
        _rec("t3", tier="suppress", sev=1, hostname="loan-01"),
        _rec("t4", tier="dashboard", sev="주의", hostname="loan-01"),
        _rec("t5", tier="page", sev="??", hostname="loan-01"),
    ]

    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), records)
        return _board(await agg.snapshot())

    body = asyncio.run(run())
    alarms = next(s for s in body["services"] if s["id"] == 1)["alarms"]
    assert alarms["recent_total"] == 5
    assert alarms["recent_by_severity"] == {"critical": 1, "warning": 2, "attention": 2,
                                            "clear": 0}
    assert alarms["recent_by_handling"] == {"delivered": 3, "suppressed": 1, "demoted": 1,
                                            "other": 0}
    assert alarms["last_alarm_at"] is not None
    assert any("심각도 미상 1건" in n for n in body["notices"])


def test_board_top_level_and_service_keys() -> None:
    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), [])
        return _board(await agg.snapshot())

    body = asyncio.run(run())
    assert set(body) == {"generated_at", "window_minutes", "summary", "metrics_status",
                         "services", "hidden_service_count", "unattributed", "notices"}
    assert set(body["summary"]) == {"state_counts", "service_count", "open_incidents"}
    assert set(body["services"][0]) == {
        "id", "display_name", "tier", "monitored", "verified", "zones", "member_count",
        "hidden_member_count", "state", "state_label", "reasons", "alarms", "metrics",
    }


def test_sort_worst_state_then_tier_then_name() -> None:
    incidents = [
        {"id": 1, "alarm_id": "i1", "db_id": "pg", "server_name": "IBKWEB01", "severity": 3,
         "created_at": _ts()},
        {"id": 2, "alarm_id": "i2", "db_id": "pg", "server_name": "loan-01", "severity": 1,
         "created_at": _ts()},
    ]

    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), [], incidents)
        return _board(await agg.snapshot())

    body = asyncio.run(run())
    # 위험 → 주의 → 판정 불가(등급·이름)
    assert [s["id"] for s in body["services"]] == [4, 1, 2, 3]
    assert body["summary"]["state_counts"] == {"risk": 1, "caution": 1, "no_signal": 0,
                                               "unknown": 2}
    assert body["summary"]["open_incidents"] == 2


# ── 존 필터 ─────────────────────────────────────────────────────────────
def test_zone_scope_hides_members_and_services() -> None:
    services = [
        _svc(1, "여신", [_sms(11, "pg", "101", "loan-01", ZA),
                         _sms(12, "pg", "102", "loan-02", ZB)]),
        _svc(2, "비밀", [_sms(21, "pg", "201", "secret-01", ZB)]),
    ]
    records = [_rec("r1", hostname="loan-01"), _rec("r2", hostname="loan-02"),
               _rec("r3", hostname="secret-01")]

    async def run() -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
        agg, _, _ = _agg(await _cache(services), records)
        snap = await agg.snapshot()
        kw: dict[str, Any] = {"monitor": None, "metrics_status": {}, "metrics_active": False,
                              "thresholds": {}}
        return (_board(snap, scope=frozenset({ZA})),
                m.build_detail(snap, 1, scope=frozenset({ZA}), **kw),
                m.build_detail(snap, 2, scope=frozenset({ZA}), **kw))

    board, detail, hidden = asyncio.run(run())
    assert [s["id"] for s in board["services"]] == [1]
    assert board["hidden_service_count"] == 1
    assert "비밀" not in json.dumps(board, ensure_ascii=False)
    item = board["services"][0]
    assert (item["member_count"], item["hidden_member_count"]) == (1, 1)
    assert item["alarms"]["recent_total"] == 1   # 권한 밖 구성원 알람은 세지 않는다
    assert detail is not None and hidden is None
    assert [r["name"] for r in detail["members"]] == ["loan-01"]
    assert detail["hidden_member_count"] == 1
    assert "loan-02" not in json.dumps(detail, ensure_ascii=False)


def test_empty_scope_counts_only() -> None:
    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(), [])
        return _board(await agg.snapshot(), scope=frozenset())

    body = asyncio.run(run())
    assert body["services"] == [] and body["hidden_service_count"] == 4


# ── 상태 판정(§4.4) ─────────────────────────────────────────────────────
def _monitor(**kw: Any) -> m.MonitorValue:
    raw = {"updated_at": _ts(), "sms_stat_time": "2026-11-05T10:00:00", **kw}
    value = m.parse_monitor_value(json.dumps(raw))
    assert value is not None
    return value


def _detail(snap: m.AlarmSnapshot, sid: int, monitor: m.MonitorValue | None,
            thresholds: dict[str, SmsThreshold], active: bool = True) -> dict[str, Any]:
    body = m.build_detail(snap, sid, scope=None, monitor=monitor, metrics_status={},
                          metrics_active=active, thresholds=thresholds)
    assert body is not None
    return body


def test_state_vector_sms_crit_beats_major_incident() -> None:
    """§14 §4.4 — APM 신호 없음 · 열린 사건 major 1 · SMS CPU 95(crit 90) → 위험(근거: SMS CPU)."""
    services = [_svc(1, "여신", [_sms(11, "pg", "101", "loan-01")], monitored=True)]
    incidents = [{"id": 5, "alarm_id": "i", "db_id": "pg", "server_name": "loan-01",
                  "severity": "major", "created_at": _ts()}]
    mon = _monitor(sms_metrics=[{"member_key": "pg|101", "metric": "cpu", "value": 95}])

    async def run() -> m.AlarmSnapshot:
        agg, _, _ = _agg(await _cache(services), [], incidents)
        return await agg.snapshot()

    body = _detail(asyncio.run(run()), 1, mon, {"cpu": SmsThreshold(warn=80, crit=90)})
    head = body["service"]
    assert (head["state"], head["state_label"]) == ("risk", "위험")
    first = head["evidence"][0]
    assert (first["rule"], first["metric"], first["value"], first["threshold"]) == (
        "sms_crit", "cpu", 95.0, 90)
    assert first["member_name"] == "loan-01"
    assert {e["rule"] for e in head["evidence"]} == {"sms_crit", "incident_open"}
    row = body["members"][0]
    assert (row["state"], row["sms"]) == ("risk", {"cpu": 95.0})


def test_state_rows() -> None:
    """§4.4 표 전 행 + 임계 미설정 · 감시 꺼짐 · 지표 수집 꺼짐 사유."""
    services = [_svc(1, "여신", [_sms(11, "pg", "101", "loan-01")], monitored=True),
                _svc(2, "수신", [_sms(21, "pg", "201", "dep-01")], monitored=False)]

    async def run() -> m.AlarmSnapshot:
        agg, _, _ = _agg(await _cache(services), [])
        return await agg.snapshot()

    snap = asyncio.run(run())
    th = {"cpu": SmsThreshold(warn=80, crit=90)}
    cpu = lambda v: _monitor(sms_metrics=[{"member_key": "pg|101", "metric": "cpu", "value": v}])  # noqa: E731
    assert _detail(snap, 1, cpu(85), th)["service"]["state"] == "caution"
    assert _detail(snap, 1, cpu(50), th)["service"]["state"] == "no_signal"
    apm = _monitor(apm_signals=[{"member_key": "pg|101", "signal": "was_error_burst",
                                 "level": "CRITICAL"}])
    assert _detail(snap, 1, apm, th)["service"]["state"] == "risk"
    failed = _monitor(fetch_failures=["sms"], sms_metrics=[
        {"member_key": "pg|101", "metric": "cpu", "value": 50}])
    head = _detail(snap, 1, failed, th)["service"]
    assert head["state"] == "unknown" and "조회 실패 — sms" in head["reasons"]
    no_th = _detail(snap, 1, cpu(99), {})["service"]
    assert no_th["sms_notes"] == {"cpu": "판정 불가 · 임계 미설정"}
    missing = _detail(snap, 1, None, th)["service"]
    assert missing["state"] == "unknown" and missing["reasons"][0] == "지표 수집 결과 없음"
    off = _detail(snap, 2, None, th, active=False)["service"]
    assert off["state"] == "unknown"
    assert off["reasons"][:2] == ["지표 수집 꺼짐", "감시 꺼짐"]
    assert off["metrics"] is None


def test_metrics_view_worst_member_values() -> None:
    services = [_svc(1, "여신", [_sms(11, "pg", "101", "a"), _sms(12, "pg", "102", "b")],
                     monitored=True)]
    mon = _monitor(
        sms_metrics=[{"member_key": "pg|101", "metric": "cpu", "value": 30},
                     {"member_key": "pg|102", "metric": "cpu", "value": 70}],
        apm_values=[{"member_key": "pg|101", "name": "response_time_ms", "value": 120}],
    )

    async def run() -> dict[str, Any]:
        agg, _, _ = _agg(await _cache(services), [])
        return _board(await agg.snapshot(), monitor_values={1: mon}, metrics_active=True)

    metrics = asyncio.run(run())["services"][0]["metrics"]
    assert metrics["sms_max"] == {"cpu": 70.0}
    assert metrics["apm_max"] == {"response_time_ms": 120.0}
    assert metrics["sms_stat_time"] == "2026-11-05T10:00:00"


# ── M3 키 읽기 · heartbeat ───────────────────────────────────────────────
class _CountingRedis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self.gets: list[str] = []

    async def get(self, name: str) -> str | None:
        self.gets.append(name)
        return await super().get(name)


def test_metrics_status_states() -> None:
    async def run() -> list[dict[str, Any]]:
        r = _CountingRedis()
        off = await m.read_metrics_status(r, active=False)
        assert r.gets == []   # 플래그 off면 읽지 않는다
        missing = await m.read_metrics_status(r, active=True)
        await r.set(m.MONITOR_STATUS_KEY, json.dumps(
            {"state": "partial", "last_success_at": "2026-11-05T10:00:00+00:00",
             "reasons": ["APM 상한 100 — 일부만 감시"]}))
        partial = await m.read_metrics_status(r, active=True)
        return [off, missing, partial]

    off, missing, partial = asyncio.run(run())
    assert (off["state"], off["label"]) == ("off", "꺼짐")
    assert off["reasons"] == ["지표 수집 꺼짐 — 알람 현황만"]
    assert missing["state"] == "off" and missing["reasons"]
    assert partial == {"state": "partial", "label": "일부만",
                       "last_success_at": "2026-11-05T10:00:00+00:00",
                       "reasons": ["APM 상한 100 — 일부만 감시"]}


def test_read_monitor_values_skips_missing_and_corrupt() -> None:
    async def run() -> dict[int, m.MonitorValue]:
        r = FakeAsyncRedis()
        await r.set("bizmon:svc:1", json.dumps({"sms_metrics": [
            {"member_key": "pg|101", "metric": "CPU", "value": "91.5"}]}))
        await r.set("bizmon:svc:2", "{broken")
        return await m.read_monitor_values(r, [1, 2, 3])

    values = asyncio.run(run())
    assert set(values) == {1}
    assert values[1].sms_metrics[0].metric == "cpu" and values[1].sms_metrics[0].value == 91.5


def test_touch_viewers_ex_120() -> None:
    async def run() -> int:
        r = FakeAsyncRedis()
        await m.touch_viewers(r)
        assert await r.get(m.VIEWERS_KEY)
        return await r.ttl(m.VIEWERS_KEY)

    assert 0 < asyncio.run(run()) <= 120


def test_no_cache_and_stores_off_are_notices() -> None:
    async def run() -> dict[str, Any]:
        agg = m.BizAlarmAggregator(cache=None, incident_store=None, decision_store=None,
                                   window_minutes=60, settings_loader=_settings)
        return _board(await agg.snapshot())

    body = asyncio.run(run())
    assert body["services"] == []
    assert len(body["notices"]) == 3


@pytest.mark.parametrize("label", list(m.METRICS_LABELS.values()) + list(m.SEVERITY_KEYS.values()))
def test_no_normal_vocabulary(label: str) -> None:
    assert "정상" not in label
