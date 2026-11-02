"""plans/207 트랙 C(M-1·M-2·M-4) 독립 검증 — verifier 작성.

- 기준 SHA(afc5626c)의 `DecisionStore`를 임시 모듈로 로드해 off 경로(`aggregate`·`meta_alerts`)와
  on 경로(`health_snapshot`)의 결과·예외를 같은 입력으로 대조한다.
- `NoiseAggregateCache`: 키 분리 · 사본 · 단일 비행 취소 내성 · 실패 비캐시 · 실 순회 스레드 중단 ·
  503 사유 누설.
- `AlarmNotificationBus` 상한 동작과 버림 요약 로그의 누락(xfail 로 결함 기록).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from noise_gate.infrastructure import notification_bus as bus_mod
from noise_gate.infrastructure.decision_store import DecisionScanStoppedError, DecisionStore
from noise_gate.infrastructure.notification_bus import AlarmNotificationBus
from src.api.noise_aggregate_cache import NoiseAggregateCache

BASE_SHA = "afc5626c"
ROOT = Path(__file__).resolve().parents[2]


# ─── 기준선 모듈 ────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def baseline_cls():
    try:
        src = subprocess.check_output(
            ["git", "show", f"{BASE_SHA}:noise_gate/infrastructure/decision_store.py"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("기준 SHA를 읽을 수 없다(git 부재·얕은 클론)")
    name = "_verify_c_baseline_decision_store"
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        exec(compile(src, f"<{BASE_SHA}:decision_store.py>", "exec"), mod.__dict__)
        yield mod.DecisionStore
    finally:
        sys.modules.pop(name, None)


def _iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _write(path: Path, lines: list[object]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in lines:
            fh.write((item if isinstance(item, str) else json.dumps(item)) + "\n")


WINDOW = 3600


def _rich_records() -> list[object]:
    return [
        {"ts": _iso(WINDOW + 5), "tier": "page"},  # 창 경계 바로 밖
        {"ts": _iso(WINDOW - 5), "tier": "suppress"},  # 창 경계 바로 안
        {"ts": _iso(100), "tier": "suppress", "stage": "collection_failed"},
        {"ts": _iso(95), "tier": "suppress", "stage_evidence": {"collection_failed": True}},
        {"ts": _iso(94), "tier": "ticket",
         "stage_evidence": {"original_stage": "collection_failed"}},
        {"ts": _iso(93), "tier": "ticket", "stage_evidence": "not-a-dict"},
        {"ts": _iso(90), "tier": "dashboard", "type": ""},  # 빈 type = 결정 레코드
        {"ts": _iso(80), "tier": "page"},
        {"ts": _iso(70)},  # tier 없음 → "" 버킷
        {"ts": "garbage-ts", "tier": "suppress"},  # 파싱 실패 → 창 안
        {"tier": "suppress"},  # ts 없음 → 창 안
        {"ts": _iso(60), "type": "resolution", "duration_seconds": 30},
        {"ts": _iso(60), "type": "resolution", "duration_seconds": "x"},
        {"ts": _iso(WINDOW + 100), "type": "resolution", "duration_seconds": 999},
        {"ts": _iso(50), "type": "recurrence", "fingerprint": "fp"},
        {"ts": _iso(WINDOW + 50), "tier": "suppress", "stage": "collection_failed"},  # 창 밖 실패
        "{broken",
        "",
        "   ",
    ]


def _outcome(fn):
    try:
        return ("ok", fn())
    except Exception as exc:  # noqa: BLE001
        return ("err", type(exc).__name__)


def _strip_age(value):
    if isinstance(value, dict):
        return {k: v for k, v in value.items() if k != "last_event_age_seconds"}
    if isinstance(value, tuple) and len(value) == 2 and value[0] == "ok":
        return ("ok", _strip_age(value[1]))
    return value


def _cases(tmp_path: Path) -> dict[str, tuple[str, bool]]:
    """이름 → (경로, enabled)."""
    rich = tmp_path / "rich.jsonl"
    _write(rich, _rich_records())
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    only_typed = tmp_path / "typed.jsonl"
    _write(only_typed, [{"ts": _iso(10), "type": "resolution", "duration_seconds": 5}])
    adir = tmp_path / "adir"
    adir.mkdir()
    return {
        "rich": (str(rich), True),
        "rich_disabled": (str(rich), False),
        "empty": (str(empty), True),
        "missing": (str(tmp_path / "nope.jsonl"), True),
        "only_typed": (str(only_typed), True),
        "directory_oserror": (str(adir), True),
    }


ALERT_KW = [
    dict(suppress_ratio_threshold=0.3, min_events=1, collection_failed_ratio_threshold=0.0),
    dict(suppress_ratio_threshold=0.3, min_events=50, collection_failed_ratio_threshold=0.1),
    dict(suppress_ratio_threshold=0.99, min_events=0, collection_failed_ratio_threshold=0.9),
    dict(suppress_ratio_threshold=0.0, min_events=1, collection_failed_ratio_threshold=0.0001),
]


# ─── 1. off 경로 = 기준 SHA 비트 동일 · 2. on = off ───────────────────────


@pytest.mark.parametrize("kw", ALERT_KW)
def test_off_and_on_match_baseline_across_fixtures(tmp_path, baseline_cls, kw):
    for name, (path, enabled) in _cases(tmp_path).items():
        old = baseline_cls(path, enabled)
        new = DecisionStore(path, enabled)
        for window in (None, WINDOW):
            o = _outcome(lambda: old.aggregate(window_seconds=window))
            n = _outcome(lambda: new.aggregate(window_seconds=window))
            assert _strip_age(n) == _strip_age(o), (name, window)
        o_alerts = _outcome(lambda: old.meta_alerts(window_seconds=WINDOW, **kw))
        n_alerts = _outcome(lambda: new.meta_alerts(window_seconds=WINDOW, **kw))
        assert n_alerts == o_alerts, name
        o_agg = old.aggregate(window_seconds=WINDOW)
        snap = new.health_snapshot(window_seconds=WINDOW, **kw)
        assert _strip_age(snap[0]) == _strip_age(o_agg), name
        assert snap[1] == o_alerts[1], name
        if o_agg["last_event_age_seconds"] is not None:
            assert snap[0]["last_event_age_seconds"] == pytest.approx(
                o_agg["last_event_age_seconds"], abs=2.0
            )


def test_rich_fixture_exercises_every_alert_branch(tmp_path, baseline_cls):
    path = _cases(tmp_path)["rich"][0]
    alerts = baseline_cls(path).meta_alerts(window_seconds=WINDOW, **ALERT_KW[1])
    assert {a["type"] for a in alerts} == {
        "high_suppress_ratio",
        "no_events",
        "collection_failed_ratio",
    }
    cf = next(a for a in alerts if a["type"] == "collection_failed_ratio")
    assert (cf["count"], cf["total"]) == (3, 10)  # 창 밖 실패·비결정 레코드 제외


@pytest.mark.parametrize(
    "bad_line",
    ["[1, 2]", "42", "null", '"text"'],
    ids=["list", "int", "null", "str"],
)
@pytest.mark.parametrize("window", [None, WINDOW])
def test_non_dict_json_line_exception_parity(tmp_path, baseline_cls, bad_line, window):
    """손상 줄 중 JSON이지만 객체가 아닌 줄 — 기준선과 같은 예외(회귀 아님 · 사전 결함 기록)."""
    path = tmp_path / "d.jsonl"
    _write(path, [{"ts": _iso(10), "tier": "page"}, bad_line])
    old = baseline_cls(str(path))
    new = DecisionStore(str(path))
    o = _outcome(lambda: old.aggregate(window_seconds=window))
    assert _outcome(lambda: new.aggregate(window_seconds=window)) == o
    if window is not None:
        kw = ALERT_KW[1]
        o_m = _outcome(lambda: old.meta_alerts(window_seconds=window, **kw))
        assert _outcome(lambda: new.meta_alerts(window_seconds=window, **kw)) == o_m
        snap = _outcome(lambda: new.health_snapshot(window_seconds=window, **kw))
        assert snap[0] == o_m[0] and (snap[0] == "ok" or snap[1] == o_m[1])
    # 사전 결함 기록: JSON 비객체 한 줄이 집계 전체를 깨뜨린다(기준선에서도 동일)
    assert o == ("err", "AttributeError")


def test_mixed_naive_aware_ts_and_bad_utf8_parity(tmp_path, baseline_cls):
    naive = tmp_path / "naive.jsonl"
    _write(naive, [
        {"ts": _iso(10), "tier": "page"},
        {"ts": datetime.now().replace(microsecond=0).isoformat(), "tier": "page"},
    ])
    badutf = tmp_path / "bad.jsonl"
    badutf.write_bytes(b'{"tier":"page"}\n\xff\xfe\n')
    for path in (naive, badutf):
        old = baseline_cls(str(path))
        new = DecisionStore(str(path))
        kw = ALERT_KW[1]
        o = _outcome(lambda: old.meta_alerts(window_seconds=WINDOW, **kw))
        assert _outcome(lambda: new.meta_alerts(window_seconds=WINDOW, **kw)) == o
        s = _outcome(lambda: new.health_snapshot(window_seconds=WINDOW, **kw))
        assert s[0] == o[0]


def test_aggregate_without_stop_never_counts_lines(tmp_path, monkeypatch):
    """should_stop 없는 off 경로는 중단 확인을 하지 않는다(무거운 파일에서도 예외 없음)."""
    path = tmp_path / "d.jsonl"
    _write(path, [{"ts": _iso(1), "tier": "page"}] * 600)
    assert DecisionStore(str(path)).aggregate(window_seconds=WINDOW)["total"] == 600


# ─── 3. 캐시 ────────────────────────────────────────────────────────────────


def _h(cache, store, **over):
    kw = dict(
        window_seconds=WINDOW,
        suppress_ratio_threshold=0.3,
        min_events=1,
        collection_failed_ratio_threshold=0.1,
    )
    kw.update(over)
    return cache.health(store, **kw)


def test_cache_keys_isolate_path_enabled_and_thresholds(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write(a, [{"ts": _iso(5), "tier": "page"}])
    _write(b, [{"ts": _iso(5), "tier": "suppress"}] * 3)
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=5)

    async def _main():
        ra, rb = await asyncio.gather(
            _h(cache, DecisionStore(str(a))), _h(cache, DecisionStore(str(b)))
        )
        rdis = await _h(cache, DecisionStore(str(a), enabled=False))
        r_hi = await _h(cache, DecisionStore(str(b)), suppress_ratio_threshold=1.0)
        r_min = await _h(cache, DecisionStore(str(a)), min_events=5)
        r_win = await _h(cache, DecisionStore(str(a)), window_seconds=1)
        return ra, rb, rdis, r_hi, r_min, r_win

    ra, rb, rdis, r_hi, r_min, r_win = asyncio.run(_main())
    assert ra[0]["total"] == 1 and rb[0]["total"] == 3
    assert rdis[0]["total"] == 0  # 같은 파일이라도 비활성 키는 따로
    assert any(x["type"] == "high_suppress_ratio" for x in rb[1])
    assert r_hi[1] == []  # 다른 임계 → 다른 경보
    assert any(x["type"] == "no_events" for x in r_min[1])
    assert r_win[0]["total"] == 0  # 창 1초 — ts 5초 전은 밖


def test_cache_symlink_shares_realpath_key(tmp_path, monkeypatch):
    real = tmp_path / "real.jsonl"
    _write(real, [{"ts": _iso(5), "tier": "page"}])
    link = tmp_path / "link.jsonl"
    os.symlink(real, link)
    calls = {"n": 0}
    orig = DecisionStore.health_snapshot

    def _spy(self, **kw):
        calls["n"] += 1
        return orig(self, **kw)

    monkeypatch.setattr(DecisionStore, "health_snapshot", _spy)
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=5)

    async def _main():
        await _h(cache, DecisionStore(str(real)))
        await _h(cache, DecisionStore(str(link)))

    asyncio.run(_main())
    assert calls["n"] == 1


def test_cache_returns_independent_copies(tmp_path):
    p = tmp_path / "d.jsonl"
    _write(p, [{"ts": _iso(5), "tier": "suppress"}] * 2)
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=5)

    async def _main():
        agg, alerts = await _h(cache, DecisionStore(str(p)))
        agg["by_tier"]["suppress"] = 999
        agg["total"] = -1
        alerts[0]["type"] = "tampered"
        alerts.append({"type": "extra"})
        return await _h(cache, DecisionStore(str(p)))

    agg2, alerts2 = asyncio.run(_main())
    assert agg2["by_tier"] == {"suppress": 2} and agg2["total"] == 2
    assert [a["type"] for a in alerts2] == ["high_suppress_ratio"]


class _SlowStore(DecisionStore):
    """실 순회 루프를 쓰되 줄마다 늦춘다(중단 신호 · 단일 비행 검증용)."""

    per_line = 0.0005

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = 0
        self.outcomes: list[str] = []
        self.done = threading.Event()

    def _within_window(self, rec, cutoff_ts):  # type: ignore[override]
        time.sleep(self.per_line)
        return DecisionStore._within_window(rec, cutoff_ts)

    def health_snapshot(self, **kw):  # type: ignore[override]
        self.calls += 1
        try:
            result = super().health_snapshot(**kw)
            self.outcomes.append("ok")
            return result
        except BaseException as exc:
            self.outcomes.append(type(exc).__name__)
            raise
        finally:
            self.done.set()


def test_cancelled_waiter_does_not_kill_shared_compute(tmp_path):
    p = tmp_path / "d.jsonl"
    _write(p, [{"ts": _iso(5), "tier": "page"}] * 200)  # 약 0.1초
    store = _SlowStore(str(p))
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=10)

    async def _main():
        w1 = asyncio.create_task(_h(cache, store))
        w2 = asyncio.create_task(_h(cache, store))
        await asyncio.sleep(0.02)
        w1.cancel()
        r2 = await w2
        with pytest.raises(asyncio.CancelledError):
            await w1
        r3 = await _h(cache, store)  # 캐시 적중
        return r2, r3

    r2, r3 = asyncio.run(_main())
    assert r2[0]["total"] == 200 and r3[0]["total"] == 200
    assert store.calls == 1 and store.outcomes == ["ok"]
    assert cache._inflight == {}


def test_all_waiters_cancelled_still_completes_and_caches(tmp_path):
    p = tmp_path / "d.jsonl"
    _write(p, [{"ts": _iso(5), "tier": "page"}] * 200)
    store = _SlowStore(str(p))
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=10)

    async def _main():
        w = asyncio.create_task(_h(cache, store))
        await asyncio.sleep(0.02)
        w.cancel()
        with pytest.raises(asyncio.CancelledError):
            await w
        await asyncio.to_thread(store.done.wait, 5)
        await asyncio.sleep(0.01)
        return await _h(cache, store)

    agg, _ = asyncio.run(_main())
    assert agg["total"] == 200 and store.calls == 1
    assert cache._inflight == {}


def test_timeout_really_stops_real_scan_thread(tmp_path):
    """실 `_aggregate_scan` 루프가 중단 신호로 멈추는지.

    asyncio.run 종료(기본 실행기 join)가 빠르다.
    """
    p = tmp_path / "d.jsonl"
    _write(p, [{"ts": _iso(5), "tier": "page"}] * 20000)  # 다 돌면 약 10초 이상
    store = _SlowStore(str(p))
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=0.05)
    threads_before = threading.active_count()

    async def _main():
        with pytest.raises(HTTPException) as ei:
            await _h(cache, store)
        return ei.value

    t0 = time.monotonic()
    err = asyncio.run(_main())  # 종료 시 기본 실행기 스레드를 join 한다
    elapsed = time.monotonic() - t0
    assert err.status_code == 503
    assert store.done.wait(2)
    assert store.outcomes == ["DecisionScanStoppedError"]
    assert elapsed < 3.0, f"순회 스레드가 멈추지 않았다({elapsed:.1f}s)"
    assert threading.active_count() <= threads_before
    assert cache._inflight == {} and cache._entries == {}


def test_timeout_then_next_request_recomputes(tmp_path):
    p = tmp_path / "d.jsonl"
    _write(p, [{"ts": _iso(5), "tier": "page"}] * 2000)
    store = _SlowStore(str(p))
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=0.05)

    async def _main():
        for _ in range(2):
            with pytest.raises(HTTPException):
                await _h(cache, store)
            await asyncio.to_thread(store.done.wait, 3)
            store.done.clear()

    asyncio.run(_main())
    assert store.calls == 2  # 시간 초과는 캐시하지 않는다


class _LeakyStore:
    def __init__(self, path: str, exc: Exception):
        self.path = path
        self.enabled = True
        self._exc = exc

    def health_snapshot(self, **kw):
        raise self._exc


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("/srv/secret/decisions.jsonl password=hunter2"),
        PermissionError(13, "Permission denied", "/srv/secret/decisions.jsonl"),
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "token=abc /srv/secret"),
    ],
    ids=["runtime", "permission", "unicode"],
)
def test_503_detail_has_no_path_or_secret(tmp_path, exc):
    store = _LeakyStore(str(tmp_path / "SECRETDIR" / "decisions.jsonl"), exc)
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=5)

    async def _main():
        with pytest.raises(HTTPException) as ei:
            await _h(cache, store)
        return ei.value

    err = asyncio.run(_main())
    assert err.status_code == 503
    detail = str(err.detail)
    assert type(exc).__name__ in detail
    for leak in ("SECRETDIR", "/srv/secret", "hunter2", "token=abc", str(tmp_path)):
        assert leak not in detail


def test_timeout_detail_has_no_path(tmp_path):
    store = _SlowStore(str(tmp_path / "SECRETDIR.jsonl"))
    _write(Path(store.path), [{"ts": _iso(5), "tier": "page"}] * 5000)
    cache = NoiseAggregateCache(ttl_seconds=60, timeout_seconds=0.05)

    async def _main():
        with pytest.raises(HTTPException) as ei:
            await _h(cache, store)
        return ei.value

    err = asyncio.run(_main())
    assert "SECRETDIR" not in str(err.detail) and str(tmp_path) not in str(err.detail)


def test_expired_entries_swept_on_next_success(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    _write(a, [{"ts": _iso(5), "tier": "page"}])
    _write(b, [{"ts": _iso(5), "tier": "page"}])
    cache = NoiseAggregateCache(ttl_seconds=0.05, timeout_seconds=5)

    async def _main():
        await _h(cache, DecisionStore(str(a)))
        assert len(cache._entries) == 1
        await asyncio.sleep(0.08)
        await _h(cache, DecisionStore(str(b)))

    asyncio.run(_main())
    assert len(cache._entries) == 1  # a는 만료돼 지워졌다


def test_route_responses_have_no_role_dependent_fields():
    """두 라우트가 캐시에서 쓰는 값은 (agg, alerts)뿐이고 사용자·권한 인자가 키에 없다 —
    공유 결과는 off 경로에서도 권한과 무관하게 계산되던 값이다(정적 확인)."""
    import inspect

    sig = inspect.signature(NoiseAggregateCache.health)
    assert set(sig.parameters) == {
        "self",
        "store",
        "window_seconds",
        "suppress_ratio_threshold",
        "min_events",
        "collection_failed_ratio_threshold",
    }


# ─── 5. 버스 ────────────────────────────────────────────────────────────────


def test_bus_bounded_publish_never_blocks_and_keeps_newest():
    bus = AlarmNotificationBus(queue_max=3)

    async def _main():
        slow = bus.subscribe()
        fast = bus.subscribe()
        got_fast = []
        for i in range(50):
            await asyncio.wait_for(bus.publish({"i": i}), timeout=0.5)
            got_fast.append(fast.get_nowait()["i"])
        return slow, got_fast

    slow, got_fast = asyncio.run(_main())
    assert got_fast == list(range(50))
    assert [slow.get_nowait()["i"] for _ in range(slow.qsize())] == [47, 48, 49]
    assert bus.dropped_count(slow) == 47


def test_bus_zero_unbounded_identical_queue_type():
    bus = AlarmNotificationBus()

    async def _main():
        q = bus.subscribe()
        for i in range(5000):
            await bus.publish({"i": i})
        return q

    q = asyncio.run(_main())
    assert q.maxsize == 0 and q.qsize() == 5000 and bus.dropped_count(q) == 0


def test_bus_unsubscribe_idempotent_and_stops_delivery():
    bus = AlarmNotificationBus(queue_max=2)

    async def _main():
        q = bus.subscribe()
        for i in range(5):
            await bus.publish({"i": i})
        assert bus.dropped_count(q) == 3
        bus.unsubscribe(q)
        bus.unsubscribe(q)
        await bus.publish({"i": 99})
        return q

    q = asyncio.run(_main())
    assert bus.dropped_count(q) == 0
    assert [q.get_nowait()["i"] for _ in range(q.qsize())] == [3, 4]


def _summary_counts(records) -> list[int]:
    out = []
    for r in records:
        if "버림 요약" in r.getMessage():
            out.append(int(r.args[0]))
    return out


def test_bus_summary_does_not_lose_other_subscribers_drops(caplog, monkeypatch):
    clock = {"t": 0.0}
    monkeypatch.setattr(bus_mod, "time", SimpleNamespace(monotonic=lambda: clock["t"]))
    bus = AlarmNotificationBus(queue_max=1)

    async def _main():
        bus.subscribe()  # A — 끝까지 읽지 않는다
        await bus.publish({"i": 0})
        non_first = 0
        for t in (0, 10, 20, 30, 40):  # A 첫 버림(t=0) + 4건
            clock["t"] = t
            await bus.publish({"t": t})
        non_first += 4
        bus.subscribe()  # B
        clock["t"] = 50
        await bus.publish({"t": 50})  # A 버림 · B 첫 적재
        non_first += 1
        clock["t"] = 55
        await bus.publish({"t": 55})  # A 버림 · B 첫 버림(경고 · 카운터 리셋)
        non_first += 1
        clock["t"] = 100
        await bus.publish({"t": 100})  # A·B 버림
        non_first += 2
        clock["t"] = 116
        await bus.publish({"t": 116})  # A 버림 → 요약 · B 버림
        non_first += 1  # 요약 시점까지 A의 버림
        return non_first

    with caplog.at_level(logging.WARNING, logger=bus_mod.__name__):
        expected = asyncio.run(_main())
    assert sum(_summary_counts(caplog.records)) >= expected


def test_bus_queue_max_env_parses_to_int(monkeypatch):
    from src.config.alarm import NoiseGateConfig

    for key in (
        "NOISE_AGGREGATE_OFFLOAD_ENABLED",
        "NOISE_AGGREGATE_TIMEOUT_SECONDS",
        "NOISE_AGGREGATE_CACHE_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("NOISE_ALARM_BUS_QUEUE_MAX", "1000")
    cfg = NoiseGateConfig(_env_file=None)
    assert type(cfg.alarm_bus_queue_max) is int and cfg.alarm_bus_queue_max == 1000
    monkeypatch.delenv("NOISE_ALARM_BUS_QUEUE_MAX")
    cfg0 = NoiseGateConfig(_env_file=None)
    assert cfg0.alarm_bus_queue_max == 0
    assert cfg0.aggregate_offload_enabled is False
    assert cfg0.aggregate_timeout_seconds == 30.0 and cfg0.aggregate_cache_seconds == 15.0


def test_scan_stopped_error_not_raised_without_signal(tmp_path):
    p = tmp_path / "d.jsonl"
    _write(p, [{"ts": _iso(1), "tier": "page"}] * 300)
    store = DecisionStore(str(p))
    agg, _ = store.health_snapshot(
        window_seconds=WINDOW, suppress_ratio_threshold=0.9, min_events=1,
        should_stop=lambda: False,
    )
    assert agg["total"] == 300
    with pytest.raises(DecisionScanStoppedError):
        store.health_snapshot(
            window_seconds=WINDOW, suppress_ratio_threshold=0.9, min_events=1,
            should_stop=lambda: True,
        )
