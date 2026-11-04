"""plans/208 트랙 C M-1·M-2 — 관제 메타모니터링 집계의 루프 밖 실행 · 공유 캐시 · 단일 비행.

- 플래그 off(`app.state.noise_aggregate_cache` 없음): 두 라우트가 종전 `aggregate` + `meta_alerts`
  경로 그대로다(새 경로 미호출 · 응답 키 동일).
- `DecisionStore.health_snapshot`은 한 번 순회로 `aggregate`·`meta_alerts`와 같은 값을 낸다.
- on: 스레드에서 계산 · 같은 키 동시 호출은 계산 1회 · TTL 만료 뒤 재계산 · 상한 시간 초과는 중단
  신호 + 503 사유 · 실패는 캐시하지 않고 503 사유 · 두 라우트가 같은 키면 결과를 나눠 쓴다.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noise_gate.infrastructure.decision_store import DecisionScanStoppedError, DecisionStore
from src.api.dependencies import require_admin_user
from src.api.noise_aggregate_cache import NoiseAggregateCache, build_noise_aggregate_cache
from src.api.routes import alarm as alarm_routes
from src.api.routes import noise_dashboard as noise_routes
from src.config.alarm import NoiseGateConfig

ADMIN = {"sub": "adm", "role": "admin"}


def _iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _write(path, lines: list[object]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in lines:
            fh.write((item if isinstance(item, str) else json.dumps(item)) + "\n")


def _mixed_records() -> list[object]:
    """창 안·밖 결정 · 해소 레코드 · 수집 실패 표지 · 깨진 줄 · ts 없음이 섞인 기록."""
    return [
        {"ts": _iso(7200), "tier": "page", "alarm_id": "old"},  # 창 밖
        {"ts": _iso(100), "tier": "suppress", "stage": "flapping"},
        {"ts": _iso(90), "tier": "suppress", "stage": "collection_failed"},
        {"ts": _iso(80), "tier": "ticket", "stage_evidence": {"collection_failed": True}},
        {"ts": _iso(70), "tier": "page", "stage": "severity3"},
        {"ts": _iso(60), "type": "resolution", "duration_seconds": 120},
        {"ts": _iso(7200), "type": "resolution", "duration_seconds": 999},  # 창 밖
        {"ts": _iso(50), "type": "recurrence", "fingerprint": "fp"},
        "{not json",
        "",
        {"tier": "dashboard"},  # ts 없음 — 창 안으로 센다
    ]


def _config(tmp_path, **noise_over) -> SimpleNamespace:
    ng = dict(
        enable_noise_gate=True,
        decision_store_path=str(tmp_path / "decisions.jsonl"),
        decision_store_enabled=True,
        decision_store_max_lines=20000,
        meta_alert_window_seconds=3600,
        meta_alert_suppress_ratio=0.3,
        meta_alert_min_events=1,
        meta_alert_collection_failed_ratio=0.2,
        investigation_store_path="",
    )
    ng.update(noise_over)
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=SimpleNamespace(**ng),
    )


def _client(config, cache: NoiseAggregateCache | None) -> TestClient:
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    if cache is not None:
        app.state.noise_aggregate_cache = cache
    app.dependency_overrides[require_admin_user] = lambda: ADMIN
    return TestClient(app)


def _drop_age(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "last_event_age_seconds"}


# ─── 저장소: 한 번 순회 = 두 호출과 같은 값 ─────────────────────────────


@pytest.mark.parametrize("cf_threshold", [0.0, 0.2])
def test_health_snapshot_equals_aggregate_plus_meta_alerts(tmp_path, cf_threshold):
    path = tmp_path / "decisions.jsonl"
    _write(path, _mixed_records())
    store = DecisionStore(str(path))
    kwargs = dict(
        window_seconds=3600,
        suppress_ratio_threshold=0.3,
        min_events=10,
        collection_failed_ratio_threshold=cf_threshold,
    )
    agg_old = store.aggregate(window_seconds=3600)
    alerts_old = store.meta_alerts(**kwargs)
    agg_new, alerts_new = store.health_snapshot(**kwargs)

    assert _drop_age(agg_new) == _drop_age(agg_old)
    assert agg_new["last_event_age_seconds"] == pytest.approx(
        agg_old["last_event_age_seconds"], abs=1.0
    )
    assert alerts_new == alerts_old
    # 픽스처가 세 경보 분기를 실제로 밟는지(빈 목록끼리 같다는 공허한 통과 방지)
    types = {a["type"] for a in alerts_new}
    assert {"high_suppress_ratio", "no_events"} <= types
    assert ("collection_failed_ratio" in types) is (cf_threshold > 0)
    assert agg_new["total"] == 5 and agg_new["auto_recovery_mttr_seconds"] == 120.0


def test_health_snapshot_missing_or_disabled_is_empty(tmp_path):
    store = DecisionStore(str(tmp_path / "none.jsonl"))
    agg, alerts = store.health_snapshot(
        window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1,
        collection_failed_ratio_threshold=0.5,
    )
    assert agg == DecisionStore._empty_aggregate()
    assert alerts == store.meta_alerts(
        window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1,
        collection_failed_ratio_threshold=0.5,
    )


def test_health_snapshot_stops_on_signal(tmp_path):
    path = tmp_path / "decisions.jsonl"
    _write(path, [{"ts": _iso(10), "tier": "page"}] * 1000)
    store = DecisionStore(str(path))
    with pytest.raises(DecisionScanStoppedError):
        store.health_snapshot(
            window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1,
            should_stop=lambda: True,
        )


# ─── 라우트: 플래그 off = 종전 경로 ───────────────────────────────────


def test_off_routes_use_legacy_calls_only(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    _write(path, _mixed_records())
    calls: list[str] = []
    orig_agg, orig_meta = DecisionStore.aggregate, DecisionStore.meta_alerts

    def _agg(self, **kw):
        calls.append("aggregate")
        return orig_agg(self, **kw)

    def _meta(self, **kw):
        calls.append("meta_alerts")
        return orig_meta(self, **kw)

    def _snap(self, **kw):  # pragma: no cover — 호출되면 실패
        raise AssertionError("off 경로가 health_snapshot을 불렀다")

    monkeypatch.setattr(DecisionStore, "aggregate", _agg)
    monkeypatch.setattr(DecisionStore, "meta_alerts", _meta)
    monkeypatch.setattr(DecisionStore, "health_snapshot", _snap)
    client = _client(_config(tmp_path), None)

    health = client.get("/api/v1/admin/noise/health")
    metrics = client.get("/api/v1/alarm/metrics")
    assert health.status_code == 200 and metrics.status_code == 200
    # 종전 순서 그대로: aggregate → meta_alerts(안에서 aggregate 재호출)
    assert calls == ["aggregate", "meta_alerts", "aggregate"] * 2


def test_on_response_matches_off_response(tmp_path):
    path = tmp_path / "decisions.jsonl"
    _write(path, _mixed_records())
    config = _config(tmp_path)
    off = _client(config, None)
    on = _client(config, NoiseAggregateCache(ttl_seconds=15, timeout_seconds=5))
    for url in ("/api/v1/admin/noise/health", "/api/v1/alarm/metrics"):
        a, b = off.get(url).json(), on.get(url).json()
        assert set(a) == set(b)
        assert _drop_age(a) == _drop_age(b)
        assert a["last_event_age_seconds"] == pytest.approx(b["last_event_age_seconds"], abs=1.0)


def test_build_cache_only_when_flag_exactly_true():
    assert build_noise_aggregate_cache(None) is None
    assert build_noise_aggregate_cache(MagicMock()) is None
    assert build_noise_aggregate_cache(SimpleNamespace(aggregate_offload_enabled=False)) is None
    cache = build_noise_aggregate_cache(SimpleNamespace(
        aggregate_offload_enabled=True,
        aggregate_cache_seconds=15.0,
        aggregate_timeout_seconds=30.0,
    ))
    assert isinstance(cache, NoiseAggregateCache)
    assert (cache.ttl_seconds, cache.timeout_seconds) == (15.0, 30.0)


def test_config_defaults_keep_current_behavior():
    fields = NoiseGateConfig.model_fields
    assert fields["aggregate_offload_enabled"].default is False
    assert fields["aggregate_timeout_seconds"].default == 30.0
    assert fields["aggregate_cache_seconds"].default == 15.0
    assert fields["alarm_bus_queue_max"].default == 0


# ─── 공유 캐시 · 단일 비행 · 상한 시간 ──────────────────────────────


class _FakeStore:
    """`health_snapshot` 호출 수·스레드·중단 신호를 기록하는 가짜 저장소."""

    def __init__(self, path, *, delay: float = 0.0, fail: Exception | None = None,
                 until_stopped: bool = False) -> None:
        self.path = path
        self.enabled = True
        self.delay = delay
        self.fail = fail
        self.until_stopped = until_stopped
        self.calls = 0
        self.threads: list[int] = []
        self.stopped = threading.Event()

    def health_snapshot(self, *, window_seconds, suppress_ratio_threshold, min_events,
                        collection_failed_ratio_threshold, should_stop):
        self.calls += 1
        self.threads.append(threading.get_ident())
        if self.until_stopped:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if should_stop():
                    self.stopped.set()
                    raise DecisionScanStoppedError()
                time.sleep(0.005)
            raise AssertionError("중단 신호가 오지 않았다")
        if self.delay:
            time.sleep(self.delay)
        if self.fail is not None:
            raise self.fail
        agg = dict(DecisionStore._empty_aggregate(), total=self.calls,
                   last_event_age_seconds=1.0)
        return agg, [{"type": "no_events", "total": self.calls}]


def _health(cache: NoiseAggregateCache, store, **over):
    kw = dict(window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1,
              collection_failed_ratio_threshold=0.0)
    kw.update(over)
    return cache.health(store, **kw)


def test_on_runs_off_loop_thread(tmp_path):
    store = _FakeStore(str(tmp_path / "d.jsonl"))
    cache = NoiseAggregateCache(ttl_seconds=15, timeout_seconds=5)

    async def _main():
        await _health(cache, store)
        return threading.get_ident()

    loop_thread = asyncio.run(_main())
    assert store.calls == 1 and store.threads[0] != loop_thread


def test_single_flight_concurrent_calls_compute_once(tmp_path):
    store = _FakeStore(str(tmp_path / "d.jsonl"), delay=0.2)
    cache = NoiseAggregateCache(ttl_seconds=15, timeout_seconds=5)

    async def _main():
        return await asyncio.gather(*[_health(cache, store) for _ in range(5)])

    results = asyncio.run(_main())
    assert store.calls == 1
    assert all(r[0]["total"] == 1 for r in results)
    # 사본을 준다 — 한 요청이 고쳐도 다른 요청·캐시는 그대로
    results[0][1].append({"type": "x"})
    assert results[1][1] == [{"type": "no_events", "total": 1}]


def test_single_flight_with_zero_ttl_still_merges_concurrent(tmp_path):
    store = _FakeStore(str(tmp_path / "d.jsonl"), delay=0.1)
    cache = NoiseAggregateCache(ttl_seconds=0, timeout_seconds=5)

    async def _main():
        await asyncio.gather(*[_health(cache, store) for _ in range(3)])
        await _health(cache, store)

    asyncio.run(_main())
    assert store.calls == 2  # 동시 3건 → 1회 · 그 뒤 1건 → 캐시 없이 1회


def test_cache_ttl_expiry_and_key_separation(tmp_path):
    store = _FakeStore(str(tmp_path / "d.jsonl"))
    cache = NoiseAggregateCache(ttl_seconds=0.2, timeout_seconds=5)

    async def _main():
        await _health(cache, store)
        await _health(cache, store)
        assert store.calls == 1
        await _health(cache, store, min_events=5)  # 결과를 바꾸는 인자가 다르면 다른 키
        assert store.calls == 2
        await asyncio.sleep(0.25)
        await _health(cache, store)
        assert store.calls == 3

    asyncio.run(_main())


def test_cached_age_advances_with_elapsed_time(tmp_path):
    store = _FakeStore(str(tmp_path / "d.jsonl"))
    cache = NoiseAggregateCache(ttl_seconds=15, timeout_seconds=5)

    async def _main():
        first, _ = await _health(cache, store)
        await asyncio.sleep(0.1)
        second, _ = await _health(cache, store)
        return first, second

    first, second = asyncio.run(_main())
    assert store.calls == 1
    assert second["last_event_age_seconds"] >= first["last_event_age_seconds"] + 0.09


def test_timeout_sets_stop_signal_and_returns_503(tmp_path, caplog):
    store = _FakeStore(str(tmp_path / "d.jsonl"), until_stopped=True)
    cache = NoiseAggregateCache(ttl_seconds=15, timeout_seconds=0.1)

    async def _main():
        with pytest.raises(HTTPException) as exc_info:
            await _health(cache, store)
        return exc_info.value

    with caplog.at_level("WARNING", logger="src.api.noise_aggregate_cache"):
        err = asyncio.run(_main())
    assert err.status_code == 503 and "상한 시간" in str(err.detail)
    assert store.stopped.wait(2), "순회 스레드가 중단 신호를 받지 못했다"
    assert any("시간 초과" in r.getMessage() for r in caplog.records)


def test_failure_is_503_with_reason_and_not_cached(tmp_path):
    store = _FakeStore(str(tmp_path / "d.jsonl"), fail=ValueError("boom"))
    cache = NoiseAggregateCache(ttl_seconds=15, timeout_seconds=5)

    async def _main():
        for _ in range(2):
            with pytest.raises(HTTPException) as exc_info:
                await _health(cache, store)
            assert exc_info.value.status_code == 503
            assert "ValueError" in str(exc_info.value.detail)

    asyncio.run(_main())
    assert store.calls == 2  # 실패는 캐시하지 않는다


def test_routes_share_one_computation_when_keys_match(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    _write(path, _mixed_records())
    counter = {"n": 0}
    orig = DecisionStore.health_snapshot

    def _spy(self, **kw):
        counter["n"] += 1
        return orig(self, **kw)

    monkeypatch.setattr(DecisionStore, "health_snapshot", _spy)
    client = _client(_config(tmp_path), NoiseAggregateCache(ttl_seconds=15, timeout_seconds=5))
    assert client.get("/api/v1/admin/noise/health").status_code == 200
    assert client.get("/api/v1/alarm/metrics").status_code == 200
    assert client.get("/api/v1/admin/noise/health").status_code == 200
    assert counter["n"] == 1


def test_route_timeout_surfaces_503_detail(tmp_path, monkeypatch):
    path = tmp_path / "decisions.jsonl"
    _write(path, _mixed_records())

    def _slow(self, **kw):
        stop = kw["should_stop"]
        while not stop():
            time.sleep(0.005)
        raise DecisionScanStoppedError()

    monkeypatch.setattr(DecisionStore, "health_snapshot", _slow)
    client = _client(_config(tmp_path), NoiseAggregateCache(ttl_seconds=15, timeout_seconds=0.1))
    for url in ("/api/v1/admin/noise/health", "/api/v1/alarm/metrics"):
        resp = client.get(url)
        assert resp.status_code == 503
        assert "NOISE_AGGREGATE_TIMEOUT_SECONDS" in resp.json()["detail"]
