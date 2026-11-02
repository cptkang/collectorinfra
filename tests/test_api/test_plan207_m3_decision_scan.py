"""plans/207 트랙 C M-3 (a) — 억제기 상태 집계의 「최근 구간만 읽기」.

설정 `NOISE_DECISION_STORE_RECENT_SCAN_ENABLED`.

- off(기본): `aggregate`·`meta_alerts`·`health_snapshot`은 종전 전량 순회 그대로.
- on: 창을 준 집계는 파일 끝에서 거꾸로 읽다가 창 하한보다 여유(10분) 넘게 앞선 결정
  레코드에서 멈춘다 — 결과는 off와 같다(같은 시각이 여럿일 때 「최근 결정 시각」 고르기
  규칙 포함). 창 없는 집계는 전량.
- on이면 창 밖 줄은 거의 읽지 않는다(읽은 줄 수 ≈ 창 안 + 여유 구간) · 중단 신호는 그대로 동작한다.
- 알려진 차이: 멈춘 지점보다 앞의 시각 없는 레코드는 on에서 세지 않는다(off는 위치와 무관하게 셌다).
- 배선: 두 라우트의 저장소가 설정값을 받는다 · 쓰기 주체(워커·그래프 주입)가 회전 설정을 받는다.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.infrastructure import decision_store as ds
from noise_gate.infrastructure.decision_store import DecisionScanStoppedError, DecisionStore
from src.api.dependencies import require_admin_user
from src.api.routes import alarm as alarm_routes
from src.api.routes import noise_dashboard as noise_routes
from src.api.settings_catalog import SECTION_BY_KEY
from src.config.alarm import NoiseGateConfig

ADMIN = {"sub": "adm", "role": "admin"}
NEW_KEYS = (
    "NOISE_DECISION_STORE_RECENT_SCAN_ENABLED",
    "NOISE_DECISION_STORE_ROTATE_MAX_BYTES",
    "NOISE_DECISION_STORE_ROTATE_DAILY",
)


def _ts(seconds_ago: float, *, kst: bool = False) -> str:
    when = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    if kst:
        from datetime import timezone

        when = when.astimezone(timezone(timedelta(hours=9)))
    return when.isoformat()


def _history(seed: int, *, days: float = 3.0, step: float = 30.0) -> list[object]:
    """며칠치 시간순 기록 — 결정·해소·재발·수집 실패·깨진 줄·빈 줄·소폭 순서 어긋남이 섞인다."""
    rng = random.Random(seed)
    out: list[object] = []
    ago = days * 86400
    tiers = ("page", "ticket", "dashboard", "suppress")
    while ago > 5:
        jitter = rng.uniform(-60, 60)  # 워커 동시 처리 수준의 어긋남(여유 10분 안)
        roll = rng.random()
        if roll < 0.05:
            out.append({"type": "resolution", "duration_seconds": rng.randint(1, 900),
                        "ts": _ts(ago + jitter)})
        elif roll < 0.10:
            out.append({"type": "recurrence", "count": 3, "ts": _ts(ago + jitter)})
        elif roll < 0.12:
            out.append("{broken")
        elif roll < 0.13:
            out.append("")
        else:
            rec: dict[str, Any] = {"ts": _ts(ago + jitter, kst=rng.random() < 0.3),
                                   "tier": rng.choice(tiers)}
            if rng.random() < 0.1:
                rec["stage"] = "collection_failed"
            out.append(rec)
        ago -= step
    return out


def _write(path: Path, lines: list[object]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in lines:
            fh.write((item if isinstance(item, str) else json.dumps(item)) + "\n")


def _drop_age(agg: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in agg.items() if k != "last_event_age_seconds"}


@pytest.mark.parametrize("seed", [1, 2, 3])
@pytest.mark.parametrize("window", [86400, 3600, 300])
def test_recent_scan_equals_full_scan(tmp_path: Path, seed: int, window: int) -> None:
    path = tmp_path / "d.jsonl"
    _write(path, _history(seed))
    off = DecisionStore(str(path))
    on = DecisionStore(str(path), recent_scan=True)
    a_off, a_on = off.aggregate(window_seconds=window), on.aggregate(window_seconds=window)
    assert a_off["total"] > 0
    assert _drop_age(a_on) == _drop_age(a_off)
    assert a_on["last_event_age_seconds"] == pytest.approx(a_off["last_event_age_seconds"], abs=1.0)
    kwargs = dict(window_seconds=window, suppress_ratio_threshold=0.2, min_events=10_000,
                  collection_failed_ratio_threshold=0.05)
    assert on.meta_alerts(**kwargs) == off.meta_alerts(**kwargs)
    s_on, s_off = on.health_snapshot(**kwargs), off.health_snapshot(**kwargs)
    assert (_drop_age(s_on[0]), s_on[1]) == (_drop_age(s_off[0]), s_off[1])


def test_latest_event_tie_picks_earlier_line_like_full_scan(tmp_path: Path) -> None:
    same = datetime.now(UTC) - timedelta(seconds=30)
    first = same.isoformat()
    second = same.astimezone(__import__("datetime").timezone(timedelta(hours=9))).isoformat()
    assert first != second
    path = tmp_path / "d.jsonl"
    _write(path, [{"ts": _ts(100), "tier": "page"}, {"ts": first, "tier": "page"},
                  {"ts": second, "tier": "ticket"}, {"ts": _ts(200), "tier": "page"}])
    off = DecisionStore(str(path)).aggregate(window_seconds=3600)
    on = DecisionStore(str(path), recent_scan=True).aggregate(window_seconds=3600)
    assert off["last_event_ts"] == on["last_event_ts"] == first


def test_window_none_stays_full_scan(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    _write(path, _history(7, days=1.0))
    off = DecisionStore(str(path)).aggregate(window_seconds=None)
    on = DecisionStore(str(path), recent_scan=True).aggregate(window_seconds=None)
    assert _drop_age(on) == _drop_age(off)


def test_recent_scan_reads_only_window_tail(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "d.jsonl"
    history = _history(5, days=3.0, step=30.0)  # 약 8,600줄 · 1시간 창 ≈ 120줄
    _write(path, history)
    real = ds._reverse_lines_of
    seen = {"n": 0}

    def _counting(fh):  # noqa: ANN001, ANN202
        for part in real(fh):
            seen["n"] += 1
            yield part

    monkeypatch.setattr(ds, "_reverse_lines_of", _counting)
    store = DecisionStore(str(path), recent_scan=True)
    agg = store.aggregate(window_seconds=3600)
    # 창 1시간 + 여유 10분 + 어긋남 1분 ≈ 142줄 남짓 — 전체의 2~3%
    assert 100 < seen["n"] < 200 < len(history)
    assert agg["total"] > 0

    seen["n"] = 0
    DecisionStore(str(path)).aggregate(window_seconds=3600)  # off는 거꾸로 읽지 않는다
    assert seen["n"] == 0


def test_recent_scan_stop_signal_raises(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    _write(path, [{"ts": _ts(i), "tier": "page"} for i in range(3000, 0, -1)])
    store = DecisionStore(str(path), recent_scan=True)
    with pytest.raises(DecisionScanStoppedError):
        store.health_snapshot(window_seconds=86400, suppress_ratio_threshold=0.5, min_events=1,
                              should_stop=lambda: True)


def test_known_difference_tsless_record_beyond_stop_point(tmp_path: Path) -> None:
    """시각 없는 레코드: off는 위치와 무관하게 창 안으로 세고, on은 멈춘 지점 앞이면 세지 않는다."""
    path = tmp_path / "d.jsonl"
    _write(path, [{"tier": "dashboard"},                       # 시각 없음 — 맨 앞
                  {"ts": _ts(86400 * 2), "tier": "page"},      # 창·여유 밖 — 여기서 멈춘다
                  {"ts": _ts(60), "tier": "ticket"},
                  {"tier": "suppress"}])                       # 시각 없음 — 창 안 구간
    off = DecisionStore(str(path)).aggregate(window_seconds=3600)
    on = DecisionStore(str(path), recent_scan=True).aggregate(window_seconds=3600)
    assert off["by_tier"] == {"dashboard": 1, "ticket": 1, "suppress": 1}
    assert on["by_tier"] == {"ticket": 1, "suppress": 1}


def test_recent_scan_crosses_rotated_fragments(tmp_path: Path) -> None:
    lines = _history(11, days=0.5, step=20.0)
    plain = tmp_path / "plain.jsonl"
    _write(plain, lines)
    rotated = DecisionStore(str(tmp_path / "rot.jsonl"), rotate_max_bytes=4000, recent_scan=True)
    for item in lines:
        rotated._append_line(item if isinstance(item, str) else json.dumps(item))
    assert len(ds.decision_fragments(rotated.path)) > 10
    for window in (3600, 7200):
        assert _drop_age(rotated.aggregate(window_seconds=window)) == _drop_age(
            DecisionStore(str(plain)).aggregate(window_seconds=window)
        )


# ── 설정 · 배선 ───────────────────────────────────────────────────────────────


def test_config_defaults_off(monkeypatch) -> None:
    for key in NEW_KEYS:
        monkeypatch.delenv(key, raising=False)
    cfg = NoiseGateConfig(_env_file=None)
    assert cfg.decision_store_recent_scan_enabled is False
    assert cfg.decision_store_rotate_max_bytes == 0
    assert cfg.decision_store_rotate_daily is False
    monkeypatch.setenv("NOISE_DECISION_STORE_RECENT_SCAN_ENABLED", "true")
    monkeypatch.setenv("NOISE_DECISION_STORE_ROTATE_MAX_BYTES", "104857600")
    monkeypatch.setenv("NOISE_DECISION_STORE_ROTATE_DAILY", "true")
    cfg = NoiseGateConfig(_env_file=None)
    assert (cfg.decision_store_recent_scan_enabled, cfg.decision_store_rotate_max_bytes,
            cfg.decision_store_rotate_daily) == (True, 104857600, True)


def test_new_keys_are_sectioned_with_decision_store_keys() -> None:
    for key in NEW_KEYS:
        assert SECTION_BY_KEY[key] == SECTION_BY_KEY["NOISE_DECISION_STORE_MAX_LINES"]


def _ng(tmp_path: Path, **over: Any) -> SimpleNamespace:
    ng = dict(
        enable_noise_gate=True,
        decision_store_path=str(tmp_path / "decisions.jsonl"),
        decision_store_enabled=True,
        decision_store_max_lines=20000,
        meta_alert_window_seconds=3600,
        meta_alert_suppress_ratio=0.3,
        meta_alert_min_events=1,
        meta_alert_collection_failed_ratio=0.0,
        investigation_store_path="",
    )
    ng.update(over)
    return SimpleNamespace(**ng)


@pytest.mark.parametrize("flag", [False, True])
def test_routes_build_store_with_recent_scan_flag(tmp_path: Path, monkeypatch, flag: bool) -> None:
    _write(tmp_path / "decisions.jsonl", _history(3, days=1.0))
    made: list[DecisionStore] = []
    real_init = DecisionStore.__init__

    def _spy(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        real_init(self, *args, **kwargs)
        made.append(self)

    monkeypatch.setattr(DecisionStore, "__init__", _spy)
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        security=SimpleNamespace(mask_pattern="***"),
        noise_gate=_ng(tmp_path, decision_store_recent_scan_enabled=flag),
    )
    app = FastAPI()
    app.include_router(noise_routes.router, prefix="/api/v1")
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_admin_user] = lambda: ADMIN
    client = TestClient(app)
    health = client.get("/api/v1/admin/noise/health")
    metrics = client.get("/api/v1/alarm/metrics")
    assert health.status_code == 200 and metrics.status_code == 200
    assert made and {s.recent_scan for s in made} == {flag}
    assert health.json()["total"] == metrics.json()["total"] > 0


def test_writers_receive_rotation_settings(tmp_path: Path) -> None:
    from noise_gate.application.worker_wiring import build_decision_store

    config = SimpleNamespace(
        noise_gate=_ng(tmp_path, decision_store_rotate_max_bytes=1234,
                       decision_store_rotate_daily=True)
    )
    store = build_decision_store(config)
    assert store is not None
    assert (store.rotate_max_bytes, store.rotate_daily, store.recent_scan) == (1234, True, False)

    off = build_decision_store(SimpleNamespace(noise_gate=_ng(tmp_path)))
    assert off is not None and (off.rotate_max_bytes, off.rotate_daily) == (0, False)
