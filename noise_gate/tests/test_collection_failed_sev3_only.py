"""plans/162 W2 R4 — 수집 실패 PAGE 「심각도3만」(D-330 ③) + 수집 실패율 메타경보 단위 테스트.

- 플래그 off(기본): step 5 보수 PAGE 그대로 · evidence 무가산(비트 동일)
- 플래그 on: sev3 → step 3 PAGE · sev2 → 매트릭스(보통) TICKET · sev1 → DASHBOARD ·
  침묵 등 뒤 단계 정상 적용 · evidence 표지
- 플래그 off + page_review_mode=shadow: 판정 불변 + would_tier evidence
- meta_alerts collection_failed_ratio: 임계 0이면 미평가 · 초과 시 경보 · 표지·원 단계도 센다
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from noise_gate.domain.notification_policy import (
    EVIDENCE_COLLECTION_FAILED,
    EVIDENCE_COLLECTION_FAILED_SHADOW,
    STAGE_COLLECTION_FAILED,
    STAGE_MATRIX,
    STAGE_NON_ALARM,
    STAGE_RESOLVED,
    STAGE_SEVERITY3,
    STAGE_SILENCE,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
)
from noise_gate.domain.silence import SilenceRule
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_notification_policy import (
    IMP_MAP,
    make_config,
    make_ctx,
    make_event,
)

NOW = datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC)

# 수집 실패 두 형태 — noise_ctx 부재 · 출처 unavailable
FAILED_CTXS = [None, make_ctx(source="unavailable")]


def _cfg(**kwargs):
    return make_config(importance_value_map=IMP_MAP, **kwargs)


def _key(d: NotificationDecision) -> tuple:
    return (d.tier, d.reason, d.priority, d.signals, d.fingerprint, d.stage, d.evidence)


class TestFlagOff:
    """기본 off — 현행 step 5 보수 PAGE 그대로."""

    @pytest.mark.parametrize("ctx", FAILED_CTXS)
    @pytest.mark.parametrize("sev", [1, 2])
    def test_conservative_page_unchanged(self, ctx, sev):
        d = decide_notification(make_event(severity=sev), None, None, ctx, _cfg())
        assert d.tier == TIER_PAGE
        assert d.stage == STAGE_COLLECTION_FAILED
        assert d.evidence == {}

    def test_explicit_false_and_mode_off_equal_default(self):
        event = make_event(severity=2)
        base = decide_notification(event, None, None, None, _cfg())
        explicit = decide_notification(
            event, None, None, None,
            _cfg(collection_failed_page_sev3_only=False, page_review_mode="off"),
        )
        assert _key(base) == _key(explicit)

    def test_enforce_mode_adds_no_shadow_evidence(self):
        d = decide_notification(
            make_event(severity=2), None, None, None, _cfg(page_review_mode="enforce")
        )
        assert d.tier == TIER_PAGE and d.evidence == {}


class TestFlagOn:
    """on — 심각도3만 PAGE, 그 밖은 중요도 보통으로 뒤 단계."""

    @pytest.mark.parametrize("ctx", FAILED_CTXS)
    def test_sev3_pages_at_step3_without_marker(self, ctx):
        # step 5에 도달하지 않은 판정(off여도 step 3에서 끝남)에는 표지를 싣지 않는다.
        d = decide_notification(
            make_event(severity=3), None, None, ctx,
            _cfg(collection_failed_page_sev3_only=True),
        )
        assert d.tier == TIER_PAGE
        assert d.stage == STAGE_SEVERITY3
        assert EVIDENCE_COLLECTION_FAILED not in d.evidence

    def test_clear_has_no_marker(self):
        d = decide_notification(
            make_event(severity=0), None, None, None,
            _cfg(collection_failed_page_sev3_only=True),
        )
        assert d.stage == STAGE_RESOLVED
        assert EVIDENCE_COLLECTION_FAILED not in d.evidence

    def test_non_alarm_has_no_marker(self):
        d = decide_notification(
            make_event(severity=2, alarm_name="작업 승인 요청 바랍니다"), None, None, None,
            _cfg(collection_failed_page_sev3_only=True, non_alarm_filter_enabled=True),
        )
        assert d.stage == STAGE_NON_ALARM
        assert EVIDENCE_COLLECTION_FAILED not in d.evidence

    @pytest.mark.parametrize("ctx", FAILED_CTXS)
    def test_sev2_goes_to_matrix_ticket(self, ctx):
        d = decide_notification(
            make_event(severity=2), None, None, ctx,
            _cfg(collection_failed_page_sev3_only=True),
        )
        assert d.tier == TIER_TICKET
        assert d.stage == STAGE_MATRIX
        assert d.signals["importance"] == "보통"
        assert "중요도보통" in d.reason
        assert d.evidence[EVIDENCE_COLLECTION_FAILED] is True

    @pytest.mark.parametrize("ctx", FAILED_CTXS)
    def test_sev1_goes_to_matrix_dashboard(self, ctx):
        d = decide_notification(
            make_event(severity=1), None, None, ctx,
            _cfg(collection_failed_page_sev3_only=True),
        )
        assert d.tier == TIER_DASHBOARD
        assert d.stage == STAGE_MATRIX
        assert d.evidence[EVIDENCE_COLLECTION_FAILED] is True

    def test_silence_rule_applies_after_step5(self):
        rule = SilenceRule(
            id="slc_cf", db_id="", server_name="cop0-*", alarm_name="", resource_name="",
            max_severity=2, reason="점검", created_by="op",
            created_at=NOW, expires_at=NOW + timedelta(hours=2),
        )
        d = decide_notification(
            make_event(severity=2), None, None, None,
            _cfg(collection_failed_page_sev3_only=True),
            silence_rules=[rule], now=NOW,
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_SILENCE
        assert d.evidence[EVIDENCE_COLLECTION_FAILED] is True

    def test_storm_applies_after_step5(self):
        d = decide_notification(
            make_event(severity=2), None, None, None,
            _cfg(collection_failed_page_sev3_only=True, storm_grouping_enabled=True),
            storm=True,
        )
        assert d.tier == TIER_SUPPRESS and d.evidence[EVIDENCE_COLLECTION_FAILED] is True

    def test_collected_ctx_is_unmarked(self):
        # 수집 성공 알람은 플래그 on이어도 판정·근거가 off와 같다.
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="HIGH")
        off = decide_notification(event, None, None, ctx, _cfg())
        on = decide_notification(
            event, None, None, ctx, _cfg(collection_failed_page_sev3_only=True)
        )
        assert _key(off) == _key(on)
        assert EVIDENCE_COLLECTION_FAILED not in on.evidence


class TestShadow:
    """off + page_review_mode=shadow — 판정 불변, 「했을 티어」만 근거로."""

    @pytest.mark.parametrize(("sev", "would"), [(2, TIER_TICKET), (1, TIER_DASHBOARD)])
    def test_records_would_tier(self, sev, would):
        event = make_event(severity=sev)
        base = decide_notification(event, None, None, None, _cfg())
        d = decide_notification(event, None, None, None, _cfg(page_review_mode="shadow"))
        assert (d.tier, d.reason, d.priority, d.signals, d.stage) == (
            base.tier, base.reason, base.priority, base.signals, base.stage
        )
        assert d.evidence == {
            EVIDENCE_COLLECTION_FAILED_SHADOW: {"mode": "shadow", "would_tier": would}
        }
        assert EVIDENCE_COLLECTION_FAILED not in d.evidence

    def test_would_tier_reflects_later_stages(self):
        rule = SilenceRule(
            id="slc_cf", db_id="", server_name="cop0-*", alarm_name="", resource_name="",
            max_severity=2, reason="점검", created_by="op",
            created_at=NOW, expires_at=NOW + timedelta(hours=2),
        )
        d = decide_notification(
            make_event(severity=2), None, None, None, _cfg(page_review_mode="shadow"),
            silence_rules=[rule], now=NOW,
        )
        assert d.tier == TIER_PAGE
        assert d.evidence[EVIDENCE_COLLECTION_FAILED_SHADOW]["would_tier"] == TIER_SUPPRESS

    def test_flag_on_shadow_has_no_shadow_block(self):
        d = decide_notification(
            make_event(severity=2), None, None, None,
            _cfg(collection_failed_page_sev3_only=True, page_review_mode="shadow"),
        )
        assert d.tier == TIER_TICKET
        assert EVIDENCE_COLLECTION_FAILED_SHADOW not in d.evidence


# ── 메타경보 ─────────────────────────────────────────────────────────────


def _write(tmp_path, records: list[dict]) -> DecisionStore:
    path = tmp_path / "decisions.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return DecisionStore(str(path))


def _rec(**over) -> dict:
    rec = {
        "ts": datetime.now(UTC).isoformat(),
        "tier": "ticket",
        "stage": "matrix",
        "reason": "매트릭스",
        "fingerprint": "fp",
    }
    rec.update(over)
    return rec


def _meta(store: DecisionStore, threshold: float) -> list[dict]:
    return store.meta_alerts(
        window_seconds=3600,
        suppress_ratio_threshold=0.9,
        min_events=1,
        collection_failed_ratio_threshold=threshold,
    )


class TestCollectionFailedMetaAlert:
    def test_threshold_zero_is_off(self, tmp_path):
        store = _write(tmp_path, [_rec(tier="page", stage="collection_failed")] * 4)
        default = store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1
        )
        assert default == _meta(store, 0.0)
        assert not any(a["type"] == "collection_failed_ratio" for a in default)

    def test_over_threshold_alerts(self, tmp_path):
        store = _write(tmp_path, [
            _rec(tier="page", stage="collection_failed"),
            # 플래그 on — 단계가 matrix로 바뀐 수집 실패 건도 표지로 센다
            _rec(stage_evidence={"collection_failed": True}),
            # 재판정 강등 — 원 단계가 step 5
            _rec(stage="page_review", stage_evidence={"original_stage": "collection_failed"}),
            # 구 레코드(stage 없음) — 사유 접두로 판정
            {k: v for k, v in _rec(tier="page", reason="신호 수집 실패 — 보수적 PAGE").items()
             if k != "stage"},
            _rec(),
            _rec(),
            # 부속 레코드·창 밖 레코드는 세지 않는다
            {"type": "resolution", "ts": datetime.now(UTC).isoformat()},
            _rec(
                stage="collection_failed",
                ts=(datetime.now(UTC) - timedelta(hours=3)).isoformat(),
            ),
        ])
        alerts = [a for a in _meta(store, 0.5) if a["type"] == "collection_failed_ratio"]
        assert alerts == [{
            "type": "collection_failed_ratio",
            "value": 4 / 6,
            "threshold": 0.5,
            "window_seconds": 3600,
            "count": 4,
            "total": 6,
        }]

    def test_under_threshold_no_alert(self, tmp_path):
        store = _write(tmp_path, [_rec(stage="collection_failed"), _rec(), _rec(), _rec()])
        assert not any(a["type"] == "collection_failed_ratio" for a in _meta(store, 0.25))

    def test_empty_store_no_alert(self, tmp_path):
        store = DecisionStore(str(tmp_path / "absent.jsonl"))
        assert not any(a["type"] == "collection_failed_ratio" for a in _meta(store, 0.1))

    def test_recorded_flag_on_decision_is_counted(self, tmp_path):
        # 도메인 판정 → 저장소 기록 → 메타경보까지 실제 경로로 센다.
        store = DecisionStore(str(tmp_path / "decisions.jsonl"))
        cfg = _cfg(collection_failed_page_sev3_only=True)
        failed = decide_notification(make_event(severity=2), None, None, None, cfg)
        ok = decide_notification(make_event(severity=2), None, None, make_ctx(), cfg)
        store.record(failed, stage_evidence=failed.evidence or None)
        store.record(ok, stage_evidence=ok.evidence or None)
        alerts = [a for a in _meta(store, 0.4) if a["type"] == "collection_failed_ratio"]
        assert len(alerts) == 1 and alerts[0]["count"] == 1 and alerts[0]["total"] == 2

    @pytest.mark.parametrize("ctx", FAILED_CTXS)
    def test_off_and_on_count_same_population(self, tmp_path, ctx):
        # 같은 입력 묶음(sev0~3 · 비알람 · 수집 성공 섞음)을 off·on으로 각각 기록하면
        # 수집 실패율 count·total이 같다 — 분자가 플래그 상태에 따라 달라지지 않는다.
        events = [
            (make_event(severity=3), ctx),
            (make_event(severity=2), ctx),
            (make_event(severity=1), ctx),
            (make_event(severity=0), ctx),
            (make_event(severity=2, alarm_name="작업 승인 요청 바랍니다"), ctx),
            (make_event(severity=2), make_ctx()),
            (make_event(severity=3), make_ctx()),
        ]
        counts = []
        for name, flag in (("off", False), ("on", True)):
            store = DecisionStore(str(tmp_path / f"{name}.jsonl"))
            cfg = _cfg(collection_failed_page_sev3_only=flag, non_alarm_filter_enabled=True)
            for event, c in events:
                d = decide_notification(event, None, None, c, cfg)
                store.record(d, stage_evidence=d.evidence or None)
            alerts = [a for a in _meta(store, 0.01) if a["type"] == "collection_failed_ratio"]
            assert len(alerts) == 1
            counts.append((alerts[0]["count"], alerts[0]["total"]))
        assert counts[0] == counts[1] == (2, 7)
