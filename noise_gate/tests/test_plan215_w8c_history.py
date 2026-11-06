"""plans/215 W8c — 「지표」 보기 이력 조회(수용 34~37 · SMS 이력 고정 SELECT · APM series ·
60초 메모 · 동시 상한 · 예산 부분 결과 · 보간 0 · APM 임계선 0).

가짜 SQL 실행기(`SQLGuard` 단언 · SELECT 기록) · 가짜 게이트웨이(호출 기록) · 가짜 Redis로 본다.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta
from typing import Any

import pytest
from src.security.sql_guard import SQLGuard
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application import biz_monitor_history as h
from noise_gate.application.biz_monitor_collector import REASON_SMS_UNSET
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain.biz_service_keys import (
    KIND_APM_BUSINESS,
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_business_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_model import BizServiceConfig, SmsThreshold
from noise_gate.infrastructure import biz_service_sql as q
from noise_gate.infrastructure.apm_host_map_sql_scanners import ScanSqlError

WALL0 = 1_800_000_000.0
SOURCES3 = {"cpu": ("server.Cpus", "Utilization"), "memory": ("server.Memory", "Utilization"),
            "filesystem": ("server.FileSystems", "Utilization")}
GP, YD = "polestar_cm_gp", "polestar_cm_yd"
_STAT = re.compile(r"(cmm_metric_stat_[hd]) s")


# ── SQL 빌더 ────────────────────────────────────────────────────────────
def test_history_sql_24h_hour_stat_row_cap_1440_and_dialects() -> None:
    pg = q.build_sms_history_sql(GP, "postgresql", range(1, 21), SOURCES3, grain="hour",
                                 since_key="2026110500", until_key="2026110523")
    assert "FROM polestar.cmm_metric_stat_h s" in pg and "cmm_metric_stat_d" not in pg
    assert "s.stat_date BETWEEN '2026110500' AND '2026110523'" in pg
    assert "s.avg_val BETWEEN 0 AND 1000" in pg and "MAX(s.avg_val) AS avg_val" in pg
    assert "GROUP BY r.platform_resource_id, r.resource_type, s.definition_name, s.stat_date" in pg
    assert pg.rstrip().endswith("LIMIT 1440")  # 24점 × 20대 × 3지표
    db2 = q.build_sms_history_sql("polestar_b0", "db2", range(1, 21), SOURCES3, grain="hour",
                                  since_key="2026110500", until_key="2026110523")
    assert "FROM POLESTAR.cmm_metric_stat_h s" in db2
    assert db2.rstrip().endswith("FETCH FIRST 1440 ROWS ONLY") and "LIMIT" not in db2
    for sql in (pg, db2):
        ok, reason = SQLGuard().is_safe_select(sql)
        assert ok, reason


def test_history_sql_7d_day_stat_row_cap_420() -> None:
    sql = q.build_sms_history_sql(GP, "postgresql", range(1, 21), SOURCES3, grain="day",
                                  since_key="20261030", until_key="20261105")
    assert "FROM polestar.cmm_metric_stat_d s" in sql and "cmm_metric_stat_h" not in sql
    assert sql.rstrip().endswith("LIMIT 420")  # 7점 × 20대 × 3지표
    assert SQLGuard().is_safe_select(sql)[0]


@pytest.mark.parametrize(
    ("grain", "since", "until"),
    [
        ("hour", "2026110400", "2026110500"),  # 25점 > 24
        ("day", "20261029", "20261105"),  # 8점 > 7
        ("hour", "2026110510", "2026110509"),  # 거꾸로
        ("hour", "2026110500' OR '1'='1", "2026110523"),
        ("day", "2026110500", "2026110523"),  # 일 통계에 시간 키
        ("week", "20261030", "20261105"),
    ],
)
def test_history_sql_key_rules(grain: str, since: str, until: str) -> None:
    with pytest.raises(ScanSqlError):
        q.build_sms_history_sql(GP, "postgresql", [1], SOURCES3, grain=grain, since_key=since,
                                until_key=until)


@pytest.mark.parametrize("ids", [[], list(range(1, 22)), ["1 OR 1=1"]])
def test_history_sql_server_rules(ids: list[Any]) -> None:
    with pytest.raises(ScanSqlError):
        q.build_sms_history_sql(GP, "postgresql", ids, SOURCES3, grain="hour",
                                since_key="2026110500", until_key="2026110523")


async def test_read_sms_history_drops_unknown_pairs_and_null_values() -> None:
    async def execute(db_id: str, sql: str) -> list[dict[str, Any]]:
        return [
            {"server_id": 1, "resource_type": "server.Cpus", "definition_name": "Utilization",
             "stat_date": "2026110510", "avg_val": 12.5},
            {"server_id": 1, "resource_type": "server.Cpus", "definition_name": "Utilization",
             "stat_date": "2026110511", "avg_val": None},
            {"server_id": 1, "resource_type": "server.Other", "definition_name": "x",
             "stat_date": "2026110511", "avg_val": 3},
            {"server_id": 1, "resource_type": "server.Cpus", "definition_name": "Utilization",
             "stat_date": "bad", "avg_val": 3},
        ]

    rows = await q.read_sms_history(execute, GP, "postgresql", [1], SOURCES3, grain="hour",
                                    since_key="2026110510", until_key="2026110511")
    assert rows == [q.SmsHistoryRow("1", "cpu", "2026110510", 12.5)]


# ── 가짜 ────────────────────────────────────────────────────────────────
class _Exec:
    """IN 목록·키 범위를 읽어 서버마다 cpu 값을 돌려준다(키 하나는 비운다 — 보간 0 확인)."""

    def __init__(self, *, block: asyncio.Event | None = None, fail: bool = False) -> None:
        self.calls: list[tuple[str, str]] = []
        self.block = block
        self.fail = fail

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        ok, reason = SQLGuard().is_safe_select(sql)
        assert ok, reason
        self.calls.append((db_id, sql))
        if self.block is not None:
            await self.block.wait()
        if self.fail:
            raise RuntimeError("db down")
        ids = [int(x) for x in re.search(r"IN \(([\d, ]+)\)", sql).group(1).split(", ")]  # type: ignore[union-attr]
        since, until = re.search(r"BETWEEN '(\d+)' AND '(\d+)'", sql).groups()  # type: ignore[union-attr]
        hourly = len(since) == 10
        fmt = "%Y%m%d%H" if hourly else "%Y%m%d"
        step = timedelta(hours=1) if hourly else timedelta(days=1)
        keys = []
        cur = datetime.strptime(since, fmt)
        while cur <= datetime.strptime(until, fmt):
            keys.append(cur.strftime(fmt))
            cur += step
        rows = []
        for rid in ids:
            for i, key in enumerate(keys):
                if i == 1:
                    continue  # 값 없는 칸
                rows.append({"server_id": rid, "resource_type": "server.Cpus",
                             "definition_name": "Utilization", "stat_date": key,
                             "avg_val": 10.0 + rid})
        return rows


class _Gateway:
    def __init__(self, *, fail: bool = False, timeout: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail
        self.timeout = timeout

    async def call_tool(self, tool: str, args: dict[str, Any], *,
                        timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((tool, args))
        if self.timeout:
            raise TimeoutError("게이트웨이 호출 타임아웃")
        if self.fail:
            return {"error": {"code": "upstream_failed"}, "reason": "제니퍼 응답 없음"}
        t0 = 1_799_990_000_000
        if args.get("scope") == "business":
            return {"rows": [
                {"domain_id": args["domain_id"], "business_id": 7, "metric": "service_time",
                 "time_ms": t0, "value": 120.0},
                {"domain_id": args["domain_id"], "business_id": 8, "metric": "service_time",
                 "time_ms": t0, "value": 999.0},
            ]}
        rows = []
        for i, target in enumerate(args.get("targets") or []):
            rows += [
                {"target_index": i, "domain_id": 10, "instance_name": target["instance_name"],
                 "metric": "service_time", "time_ms": t0, "value": 200.0},
                {"target_index": i, "domain_id": 10, "metric": "service_time",
                 "time_ms": t0 + 60_000, "value": None},
                {"target_index": i, "domain_id": 10, "metric": "service_count",
                 "time_ms": t0, "value": 50.0},
            ]
        return {"rows": rows, "batch": [{"index": i, "status": "ok"}
                                        for i, _ in enumerate(args.get("targets") or [])]}


def _settings(*, sources: dict[str, tuple[str, str]] | None = None,
              thresholds: dict[str, SmsThreshold] | None = None) -> BizServiceSettings:
    return BizServiceSettings(
        BizServiceConfig(sms_thresholds=thresholds or {}), frozenset(), (),
        {"cpu": SOURCES3["cpu"]} if sources is None else sources)


def _sms(mid: int, rid: int, *, db_id: str = GP, state: str = "no_signal", alarms: int = 0,
         name: str | None = None, layer: str = "web") -> dict[str, Any]:
    return {"id": mid, "kind": KIND_SMS_SERVER, "member_key": sms_member_key(db_id, rid),
            "db_id": db_id, "resource_id": str(rid), "name": name or f"srv-{mid:02d}",
            "layer": layer, "zone": "gongjon", "state": state,
            "alarms": {"recent_total": alarms, "open_incidents": 0}}


def _inst(mid: int, name: str, source: str = "s1") -> dict[str, Any]:
    return {"id": mid, "kind": KIND_APM_INSTANCE, "member_key": f"{source}|10|instance|{name}",
            "source_id": source, "domain_id": "10", "instance_name": name, "name": name,
            "layer": "was", "state": "no_signal", "alarms": {}}


def _history(*, gateway: Any = None, execute: Any = None, redis: Any = None,
             settings: BizServiceSettings | None = None, **kw: Any) -> h.BizMetricsHistory:
    return h.BizMetricsHistory(
        redis=FakeAsyncRedis() if redis is None else redis,
        gateway=gateway, execute=execute,
        settings_loader=lambda: settings or _settings(),
        engine_of=lambda db_id: "db2" if db_id == "polestar_b0" else "postgresql",
        wall=lambda: WALL0, **kw,
    )


async def _query(hist: h.BizMetricsHistory, visible: list[dict[str, Any]], *,
                 window: str = "24h", members: list[int] | None = None, compare: bool = False,
                 user: str = "u", service_members: list[dict[str, Any]] | None = None,
                 ) -> dict[str, Any]:
    return await hist.query(
        service_id=1, service={"members": service_members or visible}, visible=visible,
        hidden_member_count=0, window=window, member_ids=members, compare=compare, user=user,
    )


# ── 수용 34 ─────────────────────────────────────────────────────────────
async def test_acceptance_34_selection_20_of_35_one_select_per_zone_and_apm_one_call() -> None:
    members = [_sms(i, 100 + i, db_id=GP if i % 2 else YD) for i in range(1, 31)]
    members[29]["state"] = "risk"          # id 30 — 상태 나쁜 순 1위
    members[4]["alarms"]["recent_total"] = 9  # id 5 — 알람 많은 순
    members += [dict(_inst(40 + i, f"was-{i}"), state="caution") for i in range(5)]
    ex, gw = _Exec(), _Gateway()
    body = await _query(_history(gateway=gw, execute=ex), members)
    queried = [m["id"] for m in body["members"] if m["queried"]]
    assert len(queried) == 20 and body["selection"] == {
        "mode": "auto", "queried": 20, "members": 35, "max": 20}
    order = [m["id"] for m in body["members"]]
    assert order[:7] == [30, 40, 41, 42, 43, 44, 5]  # 상태 → 알람 수 → 이름
    assert order[:20] == [m for m in order if m in queried]
    assert sorted(db for db, _ in ex.calls) == [GP, YD]  # 존별 SELECT 1회
    for _, sql in ex.calls:
        assert _STAT.search(sql).group(1) == "cmm_metric_stat_h"  # type: ignore[union-attr]
        assert int(re.search(r"LIMIT (\d+)", sql).group(1)) <= 1440  # type: ignore[union-attr]
    assert [t for t, _ in gw.calls] == ["apm_metrics"]  # 소스별 1호출
    args = gw.calls[0][1]
    assert args["mode"] == "series" and args["scope"] == "instance"
    assert args["owner"] == "biz_history" and args["interval_minute"] == 15
    assert args["lookback_minutes"] == 1440 and len(args["targets"]) == 5
    assert any("조회 상한 20대" in n for n in body["notices"])
    assert body["sms"]["state"] == "ok" and body["apm"]["state"] == "ok"
    assert body["sms"]["through"].endswith("시 통계까지")


async def test_acceptance_34_7d_day_stat_and_apm_24h_only() -> None:
    ex, gw = _Exec(), _Gateway()
    body = await _query(_history(gateway=gw, execute=ex), [_sms(1, 101), _inst(2, "was-a")],
                        window="7d")
    assert len(ex.calls) == 1
    assert _STAT.search(ex.calls[0][1]).group(1) == "cmm_metric_stat_d"  # type: ignore[union-attr]
    assert gw.calls == []
    assert body["apm"]["state"] == "unsupported" and "APM은 24시간까지" in body["apm"]["reason"]
    assert body["sms"]["table"] == "day" and body["sms"]["through"].endswith("일 통계까지")
    with pytest.raises(ValueError):
        await _query(_history(gateway=gw, execute=ex), [_sms(1, 101)], window="7d", compare=True)


async def test_acceptance_34_memo_60s_same_request_zero_source_calls() -> None:
    redis = FakeAsyncRedis()
    ex, gw = _Exec(), _Gateway()
    hist = _history(gateway=gw, execute=ex, redis=redis)
    members = [_sms(1, 101), _inst(2, "was-a")]
    first = await _query(hist, members)
    second = await _query(hist, members, user="v")  # 다른 사람이 같은 화면
    assert first["memo"] is False and second["memo"] is True
    assert len(ex.calls) == 1 and len(gw.calls) == 1  # 원천 조회 1회
    assert second["series"] == first["series"]
    keys = [k for k in redis._data if k.startswith("bizmon:hist:")]
    assert len(keys) == 1 and redis._expire_at[keys[0]] > 0  # EX 60
    await _query(hist, members, window="6h")
    assert len(ex.calls) == 2  # 기간이 다르면 다른 메모


# ── 수용 35 ─────────────────────────────────────────────────────────────
async def test_acceptance_35_foreign_and_hidden_member_same_400_auto_hidden_no_line() -> None:
    visible = [_sms(1, 101)]
    service = visible + [_sms(2, 102)]  # 2번은 존 권한 밖(보이지 않음)
    ex = _Exec()
    hist = _history(execute=ex)
    errors = []
    for ids in ([1, 999], [1, 2]):  # 구성원 아님 · 권한 밖 — 같은 오류(숨김 여부가 드러나지 않게)
        with pytest.raises(ValueError) as exc:
            await _query(hist, visible, members=ids, service_members=service)
        errors.append(str(exc.value))
    assert errors[0] == errors[1] == "members에 이 서비스의 구성원이 아닌 id가 있습니다."
    assert ex.calls == []
    body = await _query(hist, visible, service_members=service)  # 자동 선택
    assert {s["member_id"] for s in body["series"]} == {1}  # 권한 밖 구성원은 선 없음
    assert "IN (101)" in ex.calls[0][1]
    with pytest.raises(ValueError):
        await _query(hist, visible, members=list(range(1, 22)), service_members=service)


# ── 수용 36 ─────────────────────────────────────────────────────────────
async def test_acceptance_36_user_in_flight_and_global_concurrency_429() -> None:
    gate = asyncio.Event()
    ex = _Exec(block=gate)
    hist = _history(execute=ex, max_concurrency=1)
    first = asyncio.create_task(_query(hist, [_sms(1, 101)]))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    with pytest.raises(h.HistoryBusy):  # 같은 사용자 진행 중 1
        await _query(hist, [_sms(1, 101)], window="6h")
    with pytest.raises(h.HistoryBusy):  # 전체 동시 1
        await _query(hist, [_sms(1, 101)], window="6h", user="other")
    gate.set()
    body = await first
    assert body["sms"]["state"] == "ok"
    assert h.BUSY_DETAIL == "다른 지표 조회가 진행 중입니다"


async def test_acceptance_36_budget_exceeded_sources_excluded_with_reason() -> None:
    ticks = iter([0.0])
    ex, gw = _Exec(), _Gateway()
    hist = _history(gateway=gw, execute=ex, monotonic=lambda: next(ticks, 100.0))
    body = await _query(hist, [_sms(1, 101), _inst(2, "was-a")])
    assert ex.calls == [] and gw.calls == []
    assert body["partial"] is True
    assert "예산 20초 초과로 제외" in body["sms"]["reason"]
    assert "예산 20초 초과로 제외" in body["apm"]["reason"]


async def test_acceptance_36_gateway_timeout_partial_sms_kept_not_memoized() -> None:
    redis = FakeAsyncRedis()
    ex, gw = _Exec(), _Gateway(timeout=True)
    hist = _history(gateway=gw, execute=ex, redis=redis)
    body = await _query(hist, [_sms(1, 101), _inst(2, "was-a")])
    assert body["partial"] is True and body["apm"]["state"] == "failed"
    assert "조회 실패" in body["apm"]["reason"] and body["sms"]["state"] == "ok"
    assert [s["source"] for s in body["series"]] == ["sms"]
    assert not [k for k in redis._data if k.startswith("bizmon:hist:")]  # 실패 결과는 메모 안 함
    await _query(hist, [_sms(1, 101), _inst(2, "was-a")])
    assert len(gw.calls) == 2  # [다시 시도]가 다시 읽는다


# ── 수용 37 · 지표 원천 · 비교 ──────────────────────────────────────────
async def test_acceptance_37_sms_threshold_only_no_apm_threshold_no_interpolation() -> None:
    ex, gw = _Exec(), _Gateway()
    plain = await _query(_history(gateway=gw, execute=ex), [_sms(1, 101), _inst(2, "was-a")])
    assert plain["thresholds"] == {}
    set_ = await _query(
        _history(gateway=_Gateway(), execute=_Exec(),
                 settings=_settings(thresholds={"cpu": SmsThreshold(80.0, 90.0)})),
        [_sms(1, 101), _inst(2, "was-a")])
    assert set_["thresholds"] == {"cpu": {"warn": 80.0, "crit": 90.0}}  # SMS만 · APM 임계 0
    sms = [s for s in plain["series"] if s["source"] == "sms"][0]
    assert len(sms["points"]) == 23 and sms["step_seconds"] == 3600  # 24칸 중 1칸 비움 → 점 23
    assert all(v is not None for _, v in sms["points"])
    apm = {s["metric"]: s for s in plain["series"] if s["source"] == "apm"}
    assert list(apm) == ["service_time", "service_count"]
    assert apm["service_time"]["label"] == "응답시간" and len(apm["service_time"]["points"]) == 1


async def test_sms_source_unset_zero_select_with_reason() -> None:
    ex = _Exec()
    body = await _query(_history(execute=ex, settings=_settings(sources={})), [_sms(1, 101)])
    assert ex.calls == []
    assert body["sms"]["state"] == "unset" and "지표 원천 미설정" in body["sms"]["reason"]
    assert body["sms"]["reason"] == REASON_SMS_UNSET and body["series"] == []


async def test_compare_yesterday_second_query_shifted_one_day() -> None:
    ex, gw = _Exec(), _Gateway()
    body = await _query(_history(gateway=gw, execute=ex), [_sms(1, 101), _inst(2, "was-a")],
                        window="6h", compare=True)
    assert len(ex.calls) == 2 and len(gw.calls) == 2
    assert "reference_time" not in gw.calls[0][1] and "reference_time" in gw.calls[1][1]
    today = [s for s in body["series"] if s["source"] == "sms" and not s["yesterday"]][0]
    yday = [s for s in body["series"] if s["source"] == "sms" and s["yesterday"]][0]
    assert today["points"] == yday["points"]  # 하루 뒤로 옮겨 같은 시간축
    assert body["compare"] is True


async def test_business_member_one_call_per_domain_and_domain_kind_excluded() -> None:
    gw = _Gateway()
    biz = {"id": 3, "kind": KIND_APM_BUSINESS, "member_key": apm_business_member_key("s1", 10, 7),
           "name": "대출", "layer": "was", "state": "no_signal", "alarms": {}}
    dom = {"id": 4, "kind": KIND_APM_DOMAIN, "member_key": "s1|10", "name": "dom",
           "state": "no_signal", "alarms": {}}
    body = await _query(_history(gateway=gw), [biz, dom])
    assert len(gw.calls) == 1
    args = gw.calls[0][1]
    assert args["scope"] == "business" and args["domain_id"] == 10 and "targets" not in args
    assert [s["points"][0][1] for s in body["series"]] == [120.0]  # 구성원 업무 id만
    assert any("지표 보기 대상 아님" in n for n in body["notices"])
    assert [m["id"] for m in body["members"] if m["queried"]] == [3]


async def test_sms_failure_reason_partial() -> None:
    body = await _query(_history(execute=_Exec(fail=True)), [_sms(1, 101)])
    assert body["sms"]["state"] == "failed" and "조회 실패" in body["sms"]["reason"]
    assert body["partial"] is True


def test_flag_off_builds_nothing() -> None:
    from types import SimpleNamespace

    cfg = SimpleNamespace(noise_gate=SimpleNamespace(biz_monitor_history_active=False))
    assert h.build_biz_metrics_history(cfg) is None
    assert h.history_active(SimpleNamespace()) is False
    with pytest.raises(ValueError):
        h.window_spec("2h")
