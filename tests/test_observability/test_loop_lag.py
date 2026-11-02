"""plans/207 A0-b — 이벤트 루프 정지 계측(`src/observability/loop_lag.py`) · 본체 옵트인 배선 ·
프로브."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import ObservabilityConfig
from src.observability.loop_lag import (
    LoopLagSampler,
    format_summary,
    percentile,
    run_loop_lag_monitor,
)

_REPO = Path(__file__).resolve().parents[2]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "_test_investigation_live_probe", _REPO / "scripts" / "investigation_live_probe.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # 모듈 안 dataclass가 자기 모듈을 찾는다
    spec.loader.exec_module(module)
    return module


# --- 샘플러 ---


def test_percentile_nearest_rank():
    assert percentile([], 99.0) == 0.0
    assert percentile([5.0], 99.0) == 5.0
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 99.0) == 99.0
    assert percentile(values, 50.0) == 50.0


def test_observe_counts_max_p99_and_threshold():
    stalls: list[float] = []
    sampler = LoopLagSampler(threshold_ms=100.0, on_stall=stalls.append)
    for lag in [1.0] * 98 + [150.0, 400.0]:
        sampler.observe(lag)
    summary = sampler.summary()
    assert summary.samples == 100
    assert summary.max_ms == 400.0
    assert summary.p99_ms == 150.0
    assert summary.over_threshold == 2
    assert stalls == [150.0, 400.0]
    assert "max=400.0ms" in format_summary(summary)


def test_summary_reset_starts_new_window():
    sampler = LoopLagSampler(threshold_ms=10.0)
    sampler.observe(50.0)
    first = sampler.summary(reset=True)
    assert (first.samples, first.max_ms, first.over_threshold) == (1, 50.0, 1)
    second = sampler.summary()
    assert (second.samples, second.max_ms, second.over_threshold) == (0, 0.0, 0)


def test_window_samples_are_bounded():
    sampler = LoopLagSampler(max_samples=5)
    for _ in range(20):
        sampler.observe(1.0)
    assert sampler.summary().samples == 5


def test_negative_lag_is_clamped_to_zero():
    sampler = LoopLagSampler()
    sampler.observe(-3.0)
    assert sampler.summary().max_ms == 0.0


async def test_run_detects_injected_stall():
    """루프를 동기로 막으면 그 시간만큼 정지가 잡힌다."""
    sampler = LoopLagSampler(interval=0.01, threshold_ms=100.0)
    task = asyncio.create_task(sampler.run())
    await asyncio.sleep(0.05)
    time.sleep(0.25)  # 인위 정지 — 이벤트 루프를 250ms 막는다
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    summary = sampler.summary()
    assert summary.over_threshold >= 1
    assert summary.max_ms >= 200.0
    assert summary.p99_ms >= 200.0  # 샘플 수가 적어 정지 1건이 p99에 든다


async def test_monitor_logs_warning_and_summary(caplog):
    caplog.set_level(logging.INFO, logger="src.observability.loop_lag")
    task = asyncio.create_task(
        run_loop_lag_monitor(threshold_ms=50.0, report_seconds=1.0, interval=0.01)
    )
    await asyncio.sleep(0.05)
    time.sleep(0.12)
    await asyncio.sleep(1.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if "정지 요약" in r.getMessage()]
    assert warnings and "이벤트 루프 정지" in warnings[0].getMessage()
    assert infos


# --- 설정 · 본체 배선 ---


def test_config_defaults_off():
    fields = ObservabilityConfig.model_fields
    assert fields["loop_lag_monitor_enabled"].default is False
    assert fields["loop_lag_threshold_ms"].default == 100
    assert fields["loop_lag_report_seconds"].default == 60


def test_env_keys_resolve(monkeypatch):
    monkeypatch.setenv("OBS_LOOP_LAG_MONITOR_ENABLED", "true")
    monkeypatch.setenv("OBS_LOOP_LAG_THRESHOLD_MS", "250")
    cfg = ObservabilityConfig(_env_file=None)
    assert cfg.loop_lag_monitor_enabled is True
    assert cfg.loop_lag_threshold_ms == 250


def _cfg(enabled: object) -> SimpleNamespace:
    return SimpleNamespace(observability=SimpleNamespace(
        loop_lag_monitor_enabled=enabled, loop_lag_threshold_ms=100, loop_lag_report_seconds=60,
    ))


@pytest.mark.parametrize("enabled", [False, "true", 1])
async def test_flag_off_creates_no_task(monkeypatch, enabled):
    """정확히 True가 아니면 태스크를 만들지 않는다(기본 off = 비트 동일)."""
    from src.api import server

    created: list[object] = []
    real = asyncio.create_task
    monkeypatch.setattr(asyncio, "create_task", lambda *a, **k: created.append(a) or real(*a, **k))
    assert server._start_loop_lag_monitor(_cfg(enabled)) is None
    assert created == []


async def test_flag_on_starts_and_stops_task():
    from src.api import server

    task = server._start_loop_lag_monitor(_cfg(True))
    assert isinstance(task, asyncio.Task)
    assert task.get_name() == "loop-lag-monitor"
    await asyncio.sleep(0.12)
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done()
    # 감시 태스크가 띄운 샘플러도 함께 끝난다(남은 태스크 없음)
    await asyncio.sleep(0)
    leftovers = [t for t in asyncio.all_tasks() if t.get_name() == "loop-lag-sampler"]
    assert all(t.done() for t in leftovers)


def test_lifespan_wires_start_and_cancel():
    """lifespan이 시작 헬퍼를 부르고 종료 때 취소한다(배선 확인)."""
    import inspect

    from src.api import server

    source = inspect.getsource(server.lifespan)
    assert "loop_lag_task = _start_loop_lag_monitor(config)" in source
    assert "loop_lag_task.cancel()" in source


# --- 프로브 스크립트 ---


async def test_probe_short_run_restores_fetch_poll():
    from src.api import investigation_poll

    probe = _load_probe()
    original = investigation_poll.fetch_poll
    rows = await probe.run_probe(
        [1, 2], duration=0.6, subscribers=1, operator_subscribers=1, poll_seconds=0.2,
        initial_events=20, events_per_poll=5, latency_ms=1.0,
    )
    assert investigation_poll.fetch_poll is original
    assert [r["k"] for r in rows] == [1, 2]
    for row in rows:
        assert row["polls"] >= row["k"]
        assert row["frames"] > 0
        assert row["samples"] > 0
        assert row["max_ms"] >= row["p99_ms"] >= 0.0
    table = probe.format_table(rows, 100.0)
    assert "최대 정지 ms" in table and table.count("\n") == 3


async def test_fake_service_ring_buffer_and_cursor():
    probe = _load_probe()
    service = probe.FakeInvestigationService(
        initial_events=400, events_per_poll=10, excerpt_chars=64, latency_ms=0.0
    )
    first, reason = await service.fetch_poll(None, "inv-1", activity_since=0)
    assert reason is None
    events = first["activity"]["events"]
    assert len(events) == probe.RING_EVENTS and events[-1]["seq"] == 400
    assert first["activity"]["truncated"] is True
    second, _ = await service.fetch_poll(None, "inv-1", activity_since=400)
    assert [e["seq"] for e in second["activity"]["events"]] == list(range(401, 411))
