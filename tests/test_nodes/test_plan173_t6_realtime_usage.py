"""plans/173 T6 특성화 — `realtime_usage_lookup` 현행 동작 고정(Extract Function 전 안전망).

기대값은 「맞는 값」이 아니라 1ad7c161 시점의 「현재 값」이다. 반환 dict 전체(키 순서 포함)·
외부 호출 순서(DB 목록 → measurement → 감사)·로그 문구와 순서를 스냅샷으로 고정한다.
외부 I/O는 전부 가짜 — 네트워크·DB 미사용. 시각은 모듈 전역 `time`을 고정 주입한다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.clients.polestar_measurement import MeasurementResult, MeasurementRow
from src.config import PolestarRestConfig

_SNAP = Path(__file__).resolve().parents[1] / "snapshots" / "plan173_t6" / "realtime_usage.json"

_NOW_S = 1_800_000_000.0
_FRESH_MS = int(_NOW_S * 1000) - 60_000          # 1분 전 — 임계(15분) 안
_STALE_MS = int(_NOW_S * 1000) - 3_600_000       # 1시간 전 — 임계 초과

_SERVERS: dict[str, list[dict[str, Any]]] = {
    "zone_noids": [
        {"id": None, "name": "n1", "hostname": "n1"},
        {"id": "abc", "name": "n2", "hostname": "n2"},
    ],
    "polestar_b0": [
        {"ID": 11, "NAME": "srv01-a", "HOSTNAME": "h11", "AVAIL_STATUS": 0},
        {"id": 12, "name": "srv01-a", "hostname": "h12", "avail_status": "x"},
        {"id": "bad", "name": "srv01-bad", "hostname": "hb"},
        {"id": 13, "name": "other", "hostname": "srv01-host", "avail_status": 1},
        {"id": 14, "name": "zzz", "hostname": "nomatch", "avail_status": 0},
        {"id": 15, "name": "srv01-c", "hostname": "h15", "avail_status": 0},
        {"id": 16, "name": "srv01-d", "hostname": "h16", "avail_status": None},
    ],
    "polestar_cm_gp": [
        {"id": 21, "name": "gp1", "hostname": "gp1", "avail_status": 0},
    ],
    "polestar_cm_yd": [
        {"id": 31, "name": "yd1", "hostname": "yd1", "avail_status": 0},
        {"id": 32, "name": "yd2", "hostname": "yd2", "avail_status": 0},
    ],
}


def _row(rid: int, avg: float | None, ts: int | None) -> MeasurementRow:
    return MeasurementRow(rid, f"r{rid}", avg, avg, avg, ts, None)


def _fetch(db_id: str, ids: list[int], metric: str) -> MeasurementResult | None:
    if db_id == "polestar_b0" and metric == "cpu":
        return MeasurementResult(
            rows={
                11: _row(11, 41.236, _FRESH_MS),
                12: _row(12, 77.5, _STALE_MS),
                13: _row(13, None, _FRESH_MS),
                15: _row(15, 9.999, _FRESH_MS),
            },
            failed_chunks=1, total_chunks=3, failed_ids=frozenset({16}),
        )
    if db_id == "polestar_b0" and metric == "memory":
        return MeasurementResult(
            rows={11: _row(11, 55.0, _FRESH_MS - 1000), 12: _row(12, 12.0, None)},
            failed_chunks=0, total_chunks=3,
        )
    if db_id == "polestar_cm_gp":
        if metric == "memory":
            return None
        return MeasurementResult(
            rows={21: _row(21, 1.0, _FRESH_MS)}, failed_chunks=0, total_chunks=1,
        )
    if db_id == "polestar_cm_yd":
        return MeasurementResult(
            rows={31: _row(31, 5.0, None), 32: _row(32, 6.0, _FRESH_MS)},
            failed_chunks=0, total_chunks=1,
        )
    return None


def _patch(monkeypatch, mod, events: list, fetch=_fetch, servers=_SERVERS):
    """모듈 전역 seam을 가짜로 바꾼다.

    대상: DBRegistry · PolestarMeasurementClient · log_query_execution · time.
    """

    class _FakeDB:
        def __init__(self, db_id: str) -> None:
            self._db_id = db_id

        async def execute_sql(self, sql: str):
            events.append(["execute_sql", self._db_id, sql])
            return SimpleNamespace(rows=servers[self._db_id])

    class _FakeRegistry:
        def __init__(self, _cfg_arg) -> None:
            events.append(["registry_init"])

        def get_client(self, db_id: str):
            events.append(["get_client", db_id])

            class _Ctx:
                async def __aenter__(self):
                    if db_id == "zone_fail":
                        raise RuntimeError("boom")
                    return _FakeDB(db_id)

                async def __aexit__(self, *a):
                    return False

            return _Ctx()

    class _FakeClient:
        def __init__(self, _cfg_arg) -> None:
            events.append(["client_init"])

        async def fetch_zone(self, db_id, ids, metric):
            events.append(["fetch_zone", db_id, list(ids), metric])
            return fetch(db_id, ids, metric)

    async def _audit(**kw):
        events.append(["audit", kw])
        if kw.get("source_name") == "polestar_b0":
            raise RuntimeError("audit down")

    monkeypatch.setattr(mod, "DBRegistry", _FakeRegistry)
    monkeypatch.setattr(mod, "PolestarMeasurementClient", _FakeClient)
    monkeypatch.setattr(mod, "log_query_execution", _audit)
    monkeypatch.setattr(mod, "time", SimpleNamespace(time=lambda: _NOW_S))


def _cfg(csv: str) -> SimpleNamespace:
    return SimpleNamespace(polestar_rest=PolestarRestConfig(
        realtime_usage_enabled=True,
        base_urls_csv=csv,
        measurement_timeout_seconds=1,
        measurement_chunk_size=2,
        stale_after_minutes=15,
    ))


_CSV_ALL = (
    "zone_fail=http://f,zone_noids=http://n,polestar_b0=http://b,"
    "polestar_cm_gp=http://g,polestar_cm_yd=http://y"
)
_DB_IDS = [
    "zone_nourl", "zone_fail", "zone_noids", "polestar_b0", "polestar_cm_gp", "polestar_cm_yd",
]


async def _run_full(monkeypatch, caplog) -> dict:
    import importlib

    mod = importlib.import_module("src.nodes.realtime_usage")
    events: list = []
    _patch(monkeypatch, mod, events)
    caplog.set_level(logging.DEBUG, logger="src.nodes.realtime_usage")
    out = await mod.realtime_usage_lookup(
        _DB_IDS, "srv01 서버 지금 CPU 메모리 사용률", _cfg(_CSV_ALL),
        user_id="u1", thread_id="t1",
    )
    logs = [
        [r.levelname, r.getMessage()] for r in caplog.records
        if r.name == "src.nodes.realtime_usage"
    ]
    return {"result": out, "events": events, "logs": logs}


async def _run_all_fail(monkeypatch, caplog) -> dict:
    import importlib

    mod = importlib.import_module("src.nodes.realtime_usage")
    events: list = []
    _patch(monkeypatch, mod, events)
    caplog.set_level(logging.DEBUG, logger="src.nodes.realtime_usage")
    out = await mod.realtime_usage_lookup(
        ["zone_nourl", "zone_fail", "zone_noids", "polestar_cm_gp"], "지금 메모리", _cfg(_CSV_ALL),
    )
    logs = [
        [r.levelname, r.getMessage()] for r in caplog.records
        if r.name == "src.nodes.realtime_usage"
    ]
    return {"result": out, "events": events, "logs": logs}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=repr)


def _expected(key: str) -> str:
    return _dump(json.loads(_SNAP.read_text(encoding="utf-8"))[key])


@pytest.mark.asyncio
async def test_full_scenario_matches_snapshot(monkeypatch, caplog):
    """존 6개(미설정·목록 실패·대상 없음·지목 필터·지표 실패·수집 시각 없음) 전 분기 고정."""
    got = await _run_full(monkeypatch, caplog)
    assert _dump(got) == _expected("full")


@pytest.mark.asyncio
async def test_all_zone_failure_matches_snapshot(monkeypatch, caplog):
    """성공 존이 없으면 None + 폴백 경고 1줄(notes 포함)."""
    got = await _run_all_fail(monkeypatch, caplog)
    assert _dump(got) == _expected("all_fail")
