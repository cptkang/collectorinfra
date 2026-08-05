"""decision_store.record_resolution + aggregate auto_recovery_mttr 테스트 (D-049).

- record_resolution: type="resolution" JSONL append
- aggregate: auto_recovery_mttr_seconds = resolution duration 평균(없으면 None)
- 기존 by_tier/total 불변(resolution 줄 제외) — 회귀
- 워커 _update_firing_registry가 self-heal 매칭 시 _last_self_heal_duration 설정
"""

from __future__ import annotations

from types import SimpleNamespace

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_PAGE,
    TIER_SUPPRESS,
)
from noise_gate.infrastructure.decision_store import DecisionStore


def _decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="r", priority=300, signals={"severity": 2}, fingerprint="fp",
    )


# ─── record_resolution + aggregate ───────────────────────────────────────────

def test_record_resolution_appends_and_aggregate_averages(tmp_path):
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    store.record_resolution(fingerprint="fp1", duration_seconds=120.0)
    store.record_resolution(fingerprint="fp2", duration_seconds=180.0)

    agg = store.aggregate()
    assert agg["auto_recovery_mttr_seconds"] == 150.0   # (120+180)/2
    # resolution 레코드는 발송 판단(tier) 집계에 포함되지 않음
    assert agg["total"] == 0
    assert agg["by_tier"] == {}


def test_aggregate_auto_recovery_none_when_no_resolution(tmp_path):
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    store.record(_decision(TIER_PAGE))
    agg = store.aggregate()
    assert agg["auto_recovery_mttr_seconds"] is None
    assert agg["total"] == 1


def test_aggregate_tier_counts_unchanged_with_resolution_lines(tmp_path):
    # 결정 + resolution 혼재 — by_tier/total은 결정만(resolution 제외, 회귀)
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path))
    store.record(_decision(TIER_PAGE))
    store.record_resolution(fingerprint="fp", duration_seconds=90.0)
    store.record(_decision(TIER_SUPPRESS))
    store.record_resolution(fingerprint="fp2", duration_seconds=110.0)

    agg = store.aggregate()
    assert agg["total"] == 2                       # 결정 2건만
    assert agg["page_count"] == 1
    assert agg["suppress_count"] == 1
    assert agg["auto_recovery_mttr_seconds"] == 100.0   # (90+110)/2


def test_record_resolution_noop_when_disabled(tmp_path):
    path = tmp_path / "d.jsonl"
    store = DecisionStore(str(path), enabled=False)
    store.record_resolution(fingerprint="fp", duration_seconds=100.0)
    assert not path.exists()                       # no-op


# ─── 워커 _update_firing_registry duration 설정 ──────────────────────────────

def _heal_worker() -> AlarmWorker:
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(
        suppress_max_severity=2, self_heal_window_seconds=300))
    return AlarmWorker(cfg)


def _firing(severity: int) -> SimpleNamespace:
    return SimpleNamespace(is_clear=False, severity=severity)


def _clear() -> SimpleNamespace:
    return SimpleNamespace(is_clear=True, severity=0)


def test_self_heal_match_sets_last_duration():
    w = _heal_worker()
    fp = "fp-d"
    w._update_firing_registry(_firing(2), fp, now=1000.0)
    healed = w._update_firing_registry(_clear(), fp, now=1075.0)   # 75s 후 해소
    assert healed is True
    assert w._last_self_heal_duration == 75.0


def test_no_match_leaves_duration_none():
    w = _heal_worker()
    healed = w._update_firing_registry(_clear(), "fp-unknown", now=1000.0)
    assert healed is False
    assert w._last_self_heal_duration is None
