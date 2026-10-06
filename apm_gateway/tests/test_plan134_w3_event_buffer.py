"""plans/134 W3 — 조회용 이벤트 버퍼(계약 A-7 · N-11).

- 확정 구간 = 폴러 조회 `[커서, 끝]`의 `[커서, 끝 − 겹침 60초]`(가상 시계로 주기를 돌려 잰다).
- 실패·백오프 = 확정 안 함 · 시각 없는 이벤트를 받은 조회 = 확정 안 함 · 중복은 멱등 키로 지운다.
- 보관 분·최대 건수 밀어내기와 확정 구간 축소 · 빈 키 sweep.
- **알람 발행 불변**: 버퍼 있음/없음 두 폴러의 XADD 페이로드·멱등 키·커서가 바이트 같다(버퍼 기록이
  예외를 내도 발행은 같다).
- 설정 키(`APM_EVENT_BUFFER_MINUTES`·`APM_EVENT_BUFFER_MAX_EVENTS`) · `gateway_health` 버퍼 상태.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest
from apm_gateway.application.event_buffer import EventBuffer, buffer_row, row_key
from apm_gateway.application.poller import OVERLAP_MS, EventPoller
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from conftest import NOW_MS, NOW_S
from fleet_fake import Clock, FakeFleet, event
from test_poller import FakeRedis

from apm_gateway.config import load_config

T0 = NOW_MS
HOST = "apm.test"


def rec(time_ms, instance_id: int = 1, *, level: str = "warning", txid: str = "", **over):
    """이벤트 레코드(어댑터 중립 모양)."""
    base = {
        "time_ms": time_ms,
        "level": level,
        "event_type": "ERROR_X",
        "event_kind": "error",
        "value": 1.0,
        "message": "m user=kim@example.com",
        "instance_id": instance_id,
        "instance_name": f"i{instance_id}",
        "domain_id": None,
        "domain_name": "",
        "application": "/a?x=1",
        "txid": txid,
        "instance_oid": 5,
    }
    base.update(over)
    return base


def _poller(fake: FakeFleet, clock: Clock, redis: FakeRedis, buffer: EventBuffer | None, **env):
    cfg = load_config(fake.env(APM_EVENT_POLLER_ENABLED="true", **env))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return EventPoller(sources, cfg, redis, clock=clock, event_buffer=buffer), cfg, sources


def _fake_with_events() -> FakeFleet:
    fake = FakeFleet(2, hosts=(HOST,))
    for d in fake.domains:
        for j, offset in enumerate(range(-25, 400, 37)):
            level = ("FATAL", "WARNING", "NORMAL", "RECOVERY")[j % 4]  # normal은 발행 최소 레벨 밑
            fake.add_event(HOST, event(d, d * 100 + j % 2, T0 + offset * 1000, level=level,
                                       txid=str(j)))
    return fake


# ── 확정 구간 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirmed_span_is_cursor_to_end_minus_overlap():
    fake = _fake_with_events()
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    poller, _cfg, _ = _poller(fake, clock, FakeRedis(), buffer)
    await poller.poll_once()  # 첫 주기 [T0−30초, T0] → 확정 [T0−30초, T0−60초] = 빈 구간
    assert buffer.gaps("default", 1, T0 - 30_000, T0) == [(T0 - 30_000, T0)]
    assert buffer.status()["domains_confirmed"] == 0 and buffer.status()["events"] > 0
    for k in range(1, 13):
        clock.t = NOW_S + 30 * k
        await poller.poll_once()
    end = T0 + 30_000 * 12 - OVERLAP_MS  # 마지막 주기 끝 − 겹침
    for d in fake.domains:
        assert buffer.gaps("default", d, T0 - 30_000, end) == []
        assert buffer.gaps("default", d, T0 - 30_001, end + 1) == [
            (T0 - 30_001, T0 - 30_001),
            (end + 1, end + 1),
        ]
    status = buffer.status()
    assert status["domains_confirmed"] == 2 and status["retention_minutes"] == 60
    # 겹친 재조회로 같은 이벤트를 여러 번 받았어도 한 번만 둔다(받은 구간 [T0−30초, T0+360초])
    expected = {
        (d, int(e["time"]))
        for (_h, d), events in fake.events.items()
        for e in events
        if T0 - 30_000 <= int(e["time"]) <= T0 + 360_000
    }
    assert status["events"] == len(expected)
    rows = buffer.rows("default", 1, T0 - 30_000, T0 + 360_000)
    assert len(rows) == len({row_key(r) for r in rows})
    assert rows and all("<email>" in r["message"] for r in rows)  # 이벤트 조회 행과 같은 마스킹


@pytest.mark.asyncio
async def test_failed_poll_confirms_nothing():
    fake = _fake_with_events()
    fake.event_fail = {(HOST, 2)}
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    poller, _cfg, _ = _poller(fake, clock, FakeRedis(), buffer)
    for k in range(6):
        clock.t = NOW_S + 30 * k
        await poller.poll_once()
    assert poller.status()["domains"]["default:2"]["state"] == "unavailable"  # 백오프
    assert buffer.gaps("default", 2, T0, T0 + 60_000) == [(T0, T0 + 60_000)]
    assert buffer.rows("default", 2, T0 - 60_000, T0 + 400_000) == []
    assert buffer.gaps("default", 1, T0, T0 + 60_000) == []


def test_timeless_event_blocks_confirmation_and_is_not_stored():
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    buffer.record("s", 1, T0 - 120_000, T0 - 60_000, [rec(T0 - 90_000), rec(None)])
    assert buffer.gaps("s", 1, T0 - 120_000, T0 - 60_000) == [(T0 - 120_000, T0 - 60_000)]
    assert [r["time_ms"] for r in buffer.rows("s", 1, 0, T0)] == [T0 - 90_000]
    buffer.record("s", 1, T0 - 120_000, T0 - 60_000, [rec(T0 - 90_000)])  # 다음 조회는 확정
    assert buffer.gaps("s", 1, T0 - 120_000, T0 - 60_000) == []
    assert buffer.status()["events"] == 1  # 같은 이벤트는 한 번만


def test_spans_merge_and_gaps_are_closed_intervals():
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock)
    buffer.record("s", 1, T0 - 100_000, T0 - 80_000, [])
    buffer.record("s", 1, T0 - 79_999, T0 - 60_000, [])  # 붙은 구간(1ms 차)
    buffer.record("s", 1, T0 - 40_000, T0 - 30_000, [])
    assert buffer.gaps("s", 1, T0 - 100_000, T0 - 30_000) == [(T0 - 59_999, T0 - 40_001)]
    assert buffer.gaps("s", 2, T0 - 10, T0) == [(T0 - 10, T0)]  # 모르는 키 = 전부 미확인
    buffer.record("s", 1, T0 - 60_000, T0 - 40_000, [])
    assert buffer.gaps("s", 1, T0 - 100_000, T0 - 30_000) == []


# ── 보관 · 최대 건수 · sweep ─────────────────────────────────────


def test_retention_drops_old_events_and_shrinks_span():
    clock = Clock(NOW_S)
    buffer = EventBuffer(retention_minutes=1, clock=clock)
    buffer.record("s", 1, T0 - 50_000, T0 - 10_000, [rec(T0 - 45_000), rec(T0 - 15_000, 2)])
    assert buffer.gaps("s", 1, T0 - 50_000, T0 - 10_000) == []
    clock.t = NOW_S + 20  # 보관 경계 = T0+20초 − 60초 = T0−40초
    buffer.sweep()
    horizon = T0 + 20_000 - 60_000
    assert [r["time_ms"] for r in buffer.rows("s", 1, 0, T0)] == [T0 - 15_000]
    assert buffer.gaps("s", 1, T0 - 50_000, T0 - 10_000) == [(T0 - 50_000, horizon - 1)]
    clock.t = NOW_S + 120
    assert buffer.status()["domains"] == 0  # 이벤트·확정 구간이 모두 빈 키는 지운다(sweep)


def test_sweep_is_also_done_on_record_without_query():
    clock = Clock(NOW_S)
    buffer = EventBuffer(retention_minutes=1, clock=clock)
    buffer.record("s", 1, T0 - 50_000, T0 - 10_000, [rec(T0 - 45_000)])
    clock.t = NOW_S + 600  # 그 도메인이 다시 폴링되지 않아도
    buffer.record("s", 2, T0 + 590_000, T0 + 595_000, [])
    assert ("s", 1) not in buffer._domains


def test_max_events_evicts_oldest_globally_and_pulls_span_start():
    clock = Clock(NOW_S)
    buffer = EventBuffer(max_events=5, clock=clock)
    a = [rec(T0 - 50_000 + i * 1000, txid=str(i)) for i in range(4)]
    buffer.record("s", 1, T0 - 60_000, T0 - 20_000, a)
    b = [rec(T0 - 30_000 + i * 1000, txid=str(i)) for i in range(3)]
    buffer.record("s", 2, T0 - 40_000, T0 - 20_000, b)
    status = buffer.status()
    assert status["events"] == 5 and status["evicted_total"] == 2
    # 도메인 1의 가장 오래된 2건(T0−50초·T0−49초)을 밀어냈다 → 그 시각까지는 확정이 아니다
    assert [r["time_ms"] for r in buffer.rows("s", 1, 0, T0)] == [T0 - 48_000, T0 - 47_000]
    assert buffer.gaps("s", 1, T0 - 60_000, T0 - 20_000) == [(T0 - 60_000, T0 - 49_000)]
    assert buffer.gaps("s", 2, T0 - 40_000, T0 - 20_000) == []
    assert len(buffer.rows("s", 2, 0, T0)) == 3


def test_invalid_limits_are_rejected():
    with pytest.raises(ValueError):
        EventBuffer(retention_minutes=0)
    with pytest.raises(ValueError):
        EventBuffer(max_events=0)


def test_buffer_row_matches_event_tool_row_and_key_rule():
    row = buffer_row("s", 7, rec(T0, txid="77"))
    assert row == ApmTools._event_row({**rec(T0, txid="77"), "source_id": "s", "domain_id": 7})
    assert row["domain_id"] == 7 and row["message"] == "m user=<email>"
    from apm_gateway.domain.events import idempotency_key

    assert row_key(row) == idempotency_key("s", 7, 1, "ERROR_X", T0, "77")


# ── 알람 발행 불변 ─────────────────────────────────────────────


async def _run_polls(fake: FakeFleet, make_buffer, polls: int = 15):
    clock = Clock(NOW_S)
    buffer = make_buffer(clock)
    redis = FakeRedis()
    poller, _cfg, _ = _poller(fake, clock, redis, buffer)
    for k in range(polls):
        clock.t = NOW_S + 30 * k
        await poller.poll_once()
    return redis, poller


@pytest.mark.asyncio
async def test_alarm_publishing_is_byte_identical_with_and_without_buffer(caplog):
    with_buffer, p1 = await _run_polls(_fake_with_events(), lambda c: EventBuffer(clock=c))
    without, p2 = await _run_polls(_fake_with_events(), lambda c: None)
    assert with_buffer.stream and json.dumps(with_buffer.stream) == json.dumps(without.stream)
    assert with_buffer.kv == without.kv  # 멱등 키 · 커서
    assert (p1.published_total, p1.duplicates_total) == (p2.published_total, p2.duplicates_total)
    levels = {json.loads(r["data"])["apm"]["level"] for r in with_buffer.stream}
    assert "normal" not in levels  # 최소 레벨 필터는 그대로(버퍼에는 normal도 있다)
    assert p1.event_buffer is not None
    stored = p1.event_buffer.rows("default", 1, 0, T0 + 10_000_000)
    assert "normal" in {r["level"] for r in stored}

    class Broken(EventBuffer):
        def record(self, *args, **kwargs):
            raise RuntimeError("buffer bug")

    caplog.set_level(logging.ERROR, logger="apm_gateway.application.poller")
    broken, _ = await _run_polls(_fake_with_events(), lambda c: Broken(clock=c))
    assert json.dumps(broken.stream) == json.dumps(without.stream) and broken.kv == without.kv
    assert "조회용 이벤트 버퍼 기록 실패" in caplog.text


@pytest.mark.asyncio
async def test_gateway_health_shows_buffer_status():
    fake = _fake_with_events()
    clock = Clock(NOW_S)
    buffer = EventBuffer(clock=clock, retention_minutes=30, max_events=1000)
    poller, cfg, sources = _poller(fake, clock, FakeRedis(), buffer)
    await poller.poll_once()
    tools = ApmTools(sources, cfg, clock=clock, poller_status=poller.status)
    health = await tools.gateway_health()
    assert health["poller"]["event_buffer"] == {
        "enabled": True,
        "events": buffer.status()["events"],
        "domains": 2,
        "domains_confirmed": 0,
        "retention_minutes": 30,
        "max_events": 1000,
        "evicted_total": 0,
    }
    plain = EventPoller(sources, cfg, FakeRedis(), clock=clock)
    assert "event_buffer" not in plain.status()  # 버퍼가 없으면 종전 상태 모양 그대로


# ── 설정 ─────────────────────────────────────────────────────


def test_buffer_settings_defaults_and_overrides():
    cfg = load_config({})
    assert (cfg.poller.buffer_minutes, cfg.poller.buffer_max_events) == (60, 200_000)
    cfg = load_config({"APM_EVENT_BUFFER_MINUTES": "15", "APM_EVENT_BUFFER_MAX_EVENTS": "5000"})
    assert (cfg.poller.buffer_minutes, cfg.poller.buffer_max_events) == (15, 5000)
    for key in ("APM_EVENT_BUFFER_MINUTES", "APM_EVENT_BUFFER_MAX_EVENTS"):
        with pytest.raises(ValueError):
            load_config({key: "0"})


def test_env_example_documents_buffer_keys():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    assert "APM_EVENT_BUFFER_MINUTES=60" in text and "APM_EVENT_BUFFER_MAX_EVENTS=200000" in text
