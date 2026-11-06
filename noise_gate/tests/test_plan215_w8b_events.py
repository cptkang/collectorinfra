"""plans/215 W8b — 통합 이벤트 타임라인 수집·조립(`biz_monitor_events`).

가짜 사본·결정 이력·조사 저장소·상태 전이 저장소·변경 이력 읽기기로 본다(망·LLM 0).
수용 25: 같은 분 같은 구성원 같은 알람 4건 → 1행 ×4 · 사용자 기본 억제 없음 ·
변경 읽기기 없음(플래그 off) → 변경 칩 없음.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application.biz_monitor_events import (
    MAX_ROWS,
    SOURCES,
    BizEventTimeline,
    build_events,
    change_reader_from_executor,
    parse_sources,
    window_seconds,
)
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, match_key_for
from noise_gate.domain.biz_service_keys import apm_instance_member_key

T = 1_800_000_000.0  # 고정 벽시계(epoch 초)
ZA, ZB = "gongjon", "bankjon"
APM_KEY = apm_instance_member_key(
    "s1", "10", SCOPE_INSTANCE, match_key_for(SCOPE_INSTANCE, "was-a")
)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _sms(mid: int, db: str, rid: str, host: str, zone: str) -> dict[str, Any]:
    return {"id": mid, "kind": "sms_server", "member_key": f"{db}|{rid}", "db_id": db,
            "resource_id": rid, "hostname": host, "server_name": host, "zone": zone,
            "name": host}


def _service() -> dict[str, Any]:
    return {
        "id": 1, "display_name": "여신",
        "members": [
            _sms(11, "pg", "101", "loan-01", ZA),
            _sms(12, "db2x", "102", "loan-02", ZB),
            {"id": 21, "kind": "apm_instance", "member_key": APM_KEY, "source_id": "s1",
             "domain_id": "10", "instance_name": "was-a", "zone": ZA, "name": "was-a"},
        ],
    }


class _Cache:
    """귀속 대역 — 호스트 · APM 인스턴스만."""

    def __init__(self) -> None:
        self.calls = 0

    async def attribute(self, *, polestar_link_targets: Any = None, apm: Any = None,
                        hostname: str | None = None, db_id: str | None = None) -> Any:
        self.calls += 1
        if apm is not None and apm[2] == "was-a":
            return SimpleNamespace(service_id=1, basis="apm_instance")
        if hostname in {"loan-01", "loan-02"}:
            return SimpleNamespace(service_id=1, basis="host")
        if hostname == "LOAN-01.":  # 같은 호스트 키지만 사본이 다른 서비스로 귀속한 경우
            return SimpleNamespace(service_id=2, basis="apm_domain")
        return None


class _Decisions:
    enabled = True

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.calls = 0

    def iter_recent_decisions(self, window_seconds: int) -> Any:
        self.calls += 1
        return iter(self.records)


class _Investigations:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def list_requests(self, *, kinds: Any = ()) -> list[dict[str, Any]]:
        return [r for r in self.records if r.get("kind") in kinds]


class _Repo:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls = 0

    async def list_state_log(self, service_id: int, *, since: Any = None,
                             limit: int = 50) -> list[dict[str, Any]]:
        self.calls += 1
        return list(self.rows)


def _dec(alarm_id: str, at: float, *, host: str = "loan-01", tier: str = "page", sev: int = 3,
         fp: str = "fp-cpu", name: str = "CPU 높음", db_id: str = "pg",
         apm: dict[str, Any] | None = None) -> dict[str, Any]:
    rec: dict[str, Any] = {"ts": _iso(at), "alarm_id": alarm_id, "tier": tier,
                           "signals": {"severity": sev}, "db_id": db_id, "hostname": host,
                           "fingerprint": fp, "alarm_name": name}
    if apm is not None:
        rec["apm_ref"] = apm
    return rec


def _timeline(records: list[dict[str, Any]], *, investigations: list[dict[str, Any]] | None = None,
              state_rows: list[dict[str, Any]] | None = None, reader: Any = None,
              engine: dict[str, str] | None = None, clock: list[float] | None = None) -> Any:
    mono = clock if clock is not None else [0.0]
    return BizEventTimeline(
        cache=_Cache(),
        decision_store=_Decisions(records),
        investigation_store=_Investigations(investigations or []),
        repo=_Repo(state_rows or []),
        change_reader=reader,
        engine_of=(lambda db: (engine or {}).get(db, "postgresql")),
        change_lookback_minutes=60,
        monotonic=lambda: mono[0],
        wall=lambda: T,
    )


def _build(events: Any, *, operator: bool = False, include: bool = False,
           scope: Any = None, hidden: int = 0, members: list[dict[str, Any]] | None = None,
           sources: Any = None, mask: Any = None, user_sub: str = "u") -> dict[str, Any]:
    return build_events(
        events, service_id=1, window="1h",
        visible_members=members if members is not None else _service()["members"],
        hidden_member_count=hidden, scope=scope,
        sources=sources if sources is not None else frozenset(SOURCES),
        include_suppressed=include, operator=operator, user_sub=user_sub, mask=mask,
        change_lookback_minutes=60,
    )


def _collect(tl: Any, window: int = 3600) -> Any:
    return asyncio.run(tl.collect(_service(), window=window))


# ─── 입력 검증 ────────────────────────────────────────────────────────────


def test_window_and_sources_parsing() -> None:
    assert window_seconds("1h") == 3600 and window_seconds("24h") == 86400
    with pytest.raises(ValueError):
        window_seconds("2h")
    assert parse_sources(None) == frozenset(SOURCES)
    assert parse_sources("change, investigation") == {"change", "investigation"}
    with pytest.raises(ValueError):
        parse_sources("polestar_alarm,zzz")


# ─── 소스별 행 ────────────────────────────────────────────────────────────


def test_each_source_becomes_rows() -> None:
    async def reader(db: str, ids: list[str], window: int, ref: int) -> Any:
        return [SimpleNamespace(resource_id="101", resource_type="server", change_type="patch",
                                description="커널 패치", event_time=int(T - 600))], ""

    records = [
        _dec("a1", T - 120),
        _dec("j1", T - 300, host="", db_id="jennifer_s1", fp="fp-apm", name="응답 지연",
             apm={"source_id": "s1", "domain_id": "10", "instance_name": "was-a"}),
        _dec("x1", T - 60, host="LOAN-01."),  # 겹치지만 다른 서비스로 귀속 — 빠진다
        _dec("y1", T - 60, host="stranger"),  # 구성원 키와 안 겹친다 — 귀속 조회도 안 건다
    ]
    investigations = [
        {"investigation_id": "inv-1", "kind": "alarm", "alarm_ids": ["a1"], "status": "done",
         "submitted_at": _iso(T - 100), "ts": _iso(T - 30)},
        {"investigation_id": "inv-2", "kind": "alarm", "alarm_ids": ["zz"], "status": "running",
         "submitted_at": _iso(T - 100), "ts": _iso(T - 30)},  # 구성원 아님 — 빠진다
    ]
    state_rows = [{"at": _iso(T - 200), "from_state": "no_signal", "to_state": "risk"}]
    tl = _timeline(records, investigations=investigations, state_rows=state_rows, reader=reader)
    events = _collect(tl)
    assert tl._cache.calls == 3  # 겹치는 기록(a1 · j1 · x1)만 귀속 조회
    body = _build(events, operator=True)
    by_source: dict[str, list[dict[str, Any]]] = {}
    for row in body["rows"]:
        by_source.setdefault(row["source"], []).append(row)
    assert [r["alarm_id"] for r in by_source["polestar_alarm"]] == ["a1"]
    pol = by_source["polestar_alarm"][0]
    assert (pol["member_name"], pol["summary"], pol["severity"], pol["handling"],
            pol["handling_label"], pol["count"]) == ("loan-01", "CPU 높음", "critical",
                                                     "delivered", "전달", 1)
    apm = by_source["apm_event"][0]
    got = (apm["alarm_id"], apm["member_key"], apm["source_label"])
    assert got == ("j1", APM_KEY, "제니퍼 이벤트")
    assert [r["summary"] for r in by_source["state_change"]] == ["이상 신호 없음 → 위험"]
    assert [r["summary"] for r in by_source["investigation"]] == ["조사 완료 · 알람 조사",
                                                                   "조사 시작 · 알람 조사"]
    assert {r["investigation_id"] for r in by_source["investigation"]} == {"inv-1"}
    change = by_source["change"][0]
    assert change["change"] == {"change_type": "patch", "resource_type": "server",
                                "description": "커널 패치"}
    assert change["pinned"] is True  # 이상 시작(T-200) 전 60분 안
    assert body["rows"][0]["source"] == "change"  # 「장애 직전 변경」은 머리에 고정
    assert body["anomaly_start"] == {"at": _iso(T - 200), "basis": "state_log"}
    assert "상관 ≠ 인과" in body["pinned_note"]
    assert all(v["available"] for v in body["sources_available"].values())
    assert set(body) >= {"generated_at", "service_id", "window", "sources_available", "rows",
                         "truncated", "hidden_member_count", "hidden_row_count", "notices"}


def test_same_minute_member_alarm_groups_into_one_row_x4() -> None:
    base = T - (T % 60) - 120  # 분 경계 안쪽
    records = [_dec(f"a{i}", base + i * 5) for i in range(4)]
    records.append(_dec("b1", base + 61))  # 다음 분 — 따로
    records.append(_dec("c1", base + 10, fp="fp-mem", name="메모리"))  # 다른 알람 — 따로
    body = _build(_collect(_timeline(records)))
    rows = [r for r in body["rows"] if r["source"] == "polestar_alarm"]
    counts = sorted((r["summary"], r["count"]) for r in rows)
    assert counts == [("CPU 높음", 1), ("CPU 높음", 4), ("메모리", 1)]
    grouped = next(r for r in rows if r["count"] == 4)
    assert grouped["alarm_id"] == "a3"  # 최신 알람으로 서랍을 연다


def test_suppressed_only_for_operator_with_include() -> None:
    records = [_dec("s1", T - 60, tier="suppress", sev=1), _dec("d1", T - 90, tier="dashboard")]
    events = _collect(_timeline(records))

    def handlings(body: dict[str, Any]) -> list[str]:
        return sorted(r["handling"] for r in body["rows"])

    assert handlings(_build(events)) == ["demoted"]  # 사용자 기본 — 억제 없음
    assert handlings(_build(events, include=True)) == ["demoted"]  # 사용자는 켜도 없음
    assert handlings(_build(events, operator=True)) == ["demoted"]
    with_sup = _build(events, operator=True, include=True)
    assert handlings(with_sup) == ["demoted", "suppressed"] and with_sup["include_suppressed"]


def test_rows_capped_at_500_newest_first() -> None:
    records = [_dec(f"a{i}", T - i * 61, fp=f"fp{i}") for i in range(MAX_ROWS + 20)]
    body = _build(_collect(_timeline(records), window=86400))
    assert len(body["rows"]) == MAX_ROWS and body["truncated"] is True
    assert body["total_rows"] == MAX_ROWS + 20
    assert body["rows"][0]["alarm_id"] == "a0"


def test_change_reader_absent_means_no_change_source() -> None:
    body = _build(_collect(_timeline([_dec("a1", T - 60)])))
    assert body["sources_available"]["change"] == {"label": "변경 이력", "available": False,
                                                   "reason": "변경 이력 꺼짐"}
    assert not [r for r in body["rows"] if r["source"] == "change"]
    assert not any("변경" in n for n in body["notices"])


def test_db2_zone_change_unsupported_and_not_queried() -> None:
    called: list[str] = []

    async def reader(db: str, ids: list[str], window: int, ref: int) -> Any:
        called.append(db)
        assert ids == ["101"] and window == 3600 + 3600 and ref == int(T)
        return [], ""

    tl = _timeline([], reader=reader, engine={"db2x": "db2"})
    body = _build(_collect(tl))
    assert called == ["pg"]  # DB2 존은 SELECT 0
    assert "변경 이력 미지원 — db2x(db2)" in body["notices"]


def test_change_reader_failure_is_a_notice() -> None:
    async def failing(db_id: str, sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("down")

    reader = change_reader_from_executor(failing)
    rows, reason = asyncio.run(reader("pg", ["101"], 3600, int(T)))
    assert rows == [] and reason == "변경 이력 조회 실패(pg)"

    seen: list[str] = []

    async def ok(db_id: str, sql: str) -> list[dict[str, Any]]:
        from src.security.sql_guard import SQLGuard

        seen.append(sql)
        assert SQLGuard().is_safe_select(sql)[0]
        return [{"resource_id": 101, "resource_type": "server", "lifecycle_type": "patch",
                 "description": "d", "event_time": int(T)}]

    rows, reason = asyncio.run(change_reader_from_executor(ok)("pg", ["101"], 3600, int(T)))
    assert reason == "" and [r.resource_id for r in rows] == ["101"]
    assert "resource_id IN ('101')" in seen[0]


def test_masking_applies_to_summary_and_change_description() -> None:
    async def reader(db: str, ids: list[str], window: int, ref: int) -> Any:
        return [SimpleNamespace(resource_id="101", resource_type="server", change_type="patch",
                                description="AKIAIOSFODNN7EXAMPLE", event_time=int(T - 60))], ""

    events = _collect(_timeline([_dec("a1", T - 60, name="AKIAIOSFODNN7EXAMPLE")], reader=reader))

    def mask(text: str) -> str:
        return "***" if text.startswith("AKIA") else text

    body = _build(events, mask=mask)
    text = str(body)
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert {r["summary"] for r in body["rows"] if r["source"] == "polestar_alarm"} == {"***"}
    assert [r["change"]["description"] for r in body["rows"] if r["source"] == "change"] == ["***"]


def test_zone_hidden_member_rows_counted_and_state_hidden() -> None:
    records = [_dec("a1", T - 60), _dec("a2", T - 70, host="loan-02", fp="fp2")]
    state_rows = [{"at": _iso(T - 200), "from_state": "no_signal", "to_state": "caution"}]
    events = _collect(_timeline(records, state_rows=state_rows))
    visible = [m for m in _service()["members"] if m["zone"] == ZA]
    body = _build(events, scope=frozenset({ZA}), hidden=1, members=visible)
    assert [r["alarm_id"] for r in body["rows"] if "alarm_id" in r] == ["a1"]
    assert body["hidden_row_count"] == 1
    assert not [r for r in body["rows"] if r["source"] == "state_change"]
    assert any("서비스 상태 전이를 보이지 않습니다" in n for n in body["notices"])
    assert "loan-02" not in str(body["rows"])


def test_user_request_investigation_owner_only() -> None:
    investigations = [
        {"investigation_id": "inv-9", "kind": "diagnosis", "alarm_ids": [], "status": "running",
         "owner_sub": "owner", "subject": {"hostname": "loan-01"},
         "submitted_at": _iso(T - 100), "ts": _iso(T - 100)},
    ]
    events = _collect(_timeline([], investigations=investigations))
    assert not _build(events, user_sub="u")["rows"]
    assert [r["investigation_id"] for r in _build(events, user_sub="owner")["rows"]] == ["inv-9"]
    rows = _build(events, operator=True)["rows"]
    assert [r["summary"] for r in rows] == ["조사 시작 · 추가 조사"]


def test_memo_10s_and_change_memo_60s() -> None:
    reads: list[str] = []

    async def reader(db: str, ids: list[str], window: int, ref: int) -> Any:
        reads.append(db)
        return [], ""

    clock = [0.0]
    tl = _timeline([_dec("a1", T - 60)], reader=reader, clock=clock, engine={"db2x": "db2"})
    _collect(tl)
    _collect(tl)
    assert tl._decisions.calls == 1 and reads == ["pg"]
    clock[0] = 11.0
    _collect(tl)
    assert tl._decisions.calls == 2 and reads == ["pg"]  # 변경 이력은 60초 메모
    clock[0] = 61.0
    _collect(tl)
    assert reads == ["pg", "pg"]


def test_source_filter_and_first_delivered_alarm_anchor() -> None:
    async def reader(db: str, ids: list[str], window: int, ref: int) -> Any:
        return [SimpleNamespace(resource_id="101", resource_type="", change_type="deploy",
                                description="", event_time=int(T - 1000))], ""

    events = _collect(_timeline([_dec("a1", T - 300)], reader=reader))
    assert events.anomaly_start == {"at": _iso(T - 300), "basis": "first_delivered_alarm"}
    body = _build(events, sources=frozenset({"change"}))
    assert [r["source"] for r in body["rows"]] == ["change"] and body["rows"][0]["pinned"] is True
