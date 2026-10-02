"""침묵(Silence) 규칙 테스트 (Plan 54 모듈 4 — `noise-silence`).

이 계획에서 **유일하게 게이트 판정을 바꾸는** 모듈이므로 안전 가드를 직접 겨눈다:
심각도3 불가침 · 전체 침묵 금지 · 심각도 상한 · 만료·해제 · 기본 off 시 비트 동일.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from noise_gate.domain.notification_policy import (
    STAGE_SILENCE,
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)
from noise_gate.domain.silence import (
    SilenceRule,
    is_active,
    is_blank_matcher,
    match_rules,
    matches,
)
from noise_gate.infrastructure.silence_store import SilenceStore

from noise_gate.tests.test_notification_policy import (
    IMP_MAP,
    make_config,
    make_ctx,
    make_event,
)

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)


def make_rule(**kwargs) -> SilenceRule:
    """테스트용 침묵 규칙(기본: 서버 글롭 · 심각도 2 이하 · 2시간 뒤 만료)."""
    defaults = dict(
        id="slc_test",
        db_id="",
        server_name="cop0-*",
        alarm_name="",
        resource_name="",
        max_severity=2,
        reason="배포 점검",
        created_by="op",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=2),
        revoked_at=None,
    )
    defaults.update(kwargs)
    return SilenceRule(**defaults)


def _cfg(**kwargs):
    return make_config(importance_value_map=IMP_MAP, **kwargs)


class TestMatcher:
    def test_glob_matches_server_name(self):
        assert matches(make_rule(), make_event(severity=2), effective_severity=2)

    def test_glob_miss(self):
        event = make_event(severity=2, server_name="other-host")
        assert not matches(make_rule(), event, effective_severity=2)

    def test_empty_field_means_any(self):
        rule = make_rule(server_name="", alarm_name="CPU*")
        assert matches(rule, make_event(severity=2), effective_severity=2)

    def test_all_fields_must_match(self):
        rule = make_rule(server_name="cop0-*", alarm_name="메모리*")
        # 서버는 맞지만 알람명이 어긋나면 걸리지 않는다(AND 결합).
        assert not matches(rule, make_event(severity=2), effective_severity=2)

    def test_falls_back_to_hostname_when_server_name_missing(self):
        event = make_event(severity=2, server_name="", hostname="cop0-fallback")
        assert matches(make_rule(), event, effective_severity=2)

    def test_severity_above_rule_cap_is_not_silenced(self):
        assert not matches(make_rule(max_severity=1), make_event(severity=2), effective_severity=2)

    def test_blank_matcher_never_matches(self):
        # 전체 침묵 방지 — 저장소에 억지로 넣어도 도메인이 막는다.
        rule = make_rule(server_name="", alarm_name="", db_id="", resource_name="")
        assert is_blank_matcher(rule)
        assert not matches(rule, make_event(severity=2), effective_severity=2)

    def test_matching_is_case_sensitive(self):
        # 글롭은 대소문자를 구분한다 — 의도보다 넓게 걸리는 사고를 막는다.
        event = make_event(severity=2, server_name="COP0-AISAPD02")
        assert not matches(make_rule(), event, effective_severity=2)


class TestActivation:
    def test_active_before_expiry(self):
        assert is_active(make_rule(), NOW + timedelta(hours=1))

    def test_inactive_after_expiry(self):
        assert not is_active(make_rule(), NOW + timedelta(hours=3))

    def test_inactive_after_revoke(self):
        rule = make_rule(revoked_at=NOW + timedelta(minutes=10))
        assert not is_active(rule, NOW + timedelta(minutes=20))
        assert is_active(rule, NOW + timedelta(minutes=5))

    def test_naive_datetime_is_treated_as_utc(self):
        # 비교 시 예외(naive vs aware)로 게이트가 죽지 않아야 한다.
        rule = make_rule(expires_at=datetime(2026, 9, 3, 14, 0, 0))
        assert is_active(rule, NOW)


class TestMatchRules:
    def test_first_registered_rule_wins(self):
        first = make_rule(id="slc_first", reason="첫 규칙")
        second = make_rule(id="slc_second", reason="둘째 규칙")
        picked = match_rules(
            [first, second], make_event(severity=2), effective_severity=2, now=NOW
        )
        assert picked.id == "slc_first"

    def test_expired_rules_are_skipped(self):
        expired = make_rule(id="slc_old", expires_at=NOW - timedelta(hours=1))
        live = make_rule(id="slc_live")
        picked = match_rules(
            [expired, live], make_event(severity=2), effective_severity=2, now=NOW
        )
        assert picked.id == "slc_live"

    def test_no_rules_returns_none(self):
        assert match_rules([], make_event(severity=2), effective_severity=2, now=NOW) is None
        assert match_rules(None, make_event(severity=2), effective_severity=2, now=NOW) is None


class TestGateIntegration:
    def test_no_rules_means_bit_identical_decision(self):
        # 기본 off(규칙 미주입)면 판정이 종전과 같아야 한다.
        event = make_event(severity=2)
        base = decide_notification(event, None, None, make_ctx(), _cfg())
        with_empty = decide_notification(
            event, None, None, make_ctx(), _cfg(), silence_rules=[]
        )
        assert (base.tier, base.reason, base.priority, base.signals) == (
            with_empty.tier, with_empty.reason, with_empty.priority, with_empty.signals
        )

    def test_matched_rule_suppresses_with_stage_and_id(self):
        d = decide_notification(
            make_event(severity=2),
            None, None, make_ctx(), _cfg(),
            silence_rules=[make_rule(id="slc_abc", reason="월간 배포 점검")],
            now=NOW,
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_SILENCE
        assert "slc_abc" in d.reason and "월간 배포 점검" in d.reason

    def test_severity3_is_never_silenced(self):
        # 규칙이 심각도 3까지 허용해도 게이트의 단락이 앞서므로 통보된다.
        d = decide_notification(
            make_event(severity=3),
            None, None, make_ctx(), _cfg(),
            silence_rules=[make_rule(max_severity=3)],
            now=NOW,
        )
        assert d.tier == TIER_PAGE
        assert d.stage != STAGE_SILENCE

    def test_maintenance_wins_over_silence(self):
        # 시스템이 알려준 사실(유지보수)이 운영자 의도(침묵)보다 앞선 사유로 남는다.
        d = decide_notification(
            make_event(severity=2),
            None, None, make_ctx(maintenance=True), _cfg(),
            silence_rules=[make_rule()],
            now=NOW,
        )
        assert d.stage == "maintenance"

    def test_silence_precedes_dependency_suppression(self):
        ctx = make_ctx()
        ctx["parent_avail_status"] = 2
        d = decide_notification(
            make_event(severity=2),
            None, None, ctx, _cfg(dependency_suppression=True),
            silence_rules=[make_rule()],
            now=NOW,
        )
        assert d.stage == STAGE_SILENCE

    def test_unmatched_rule_does_not_change_decision(self):
        event = make_event(severity=2, server_name="unrelated")
        base = decide_notification(event, None, None, make_ctx(), _cfg())
        d = decide_notification(
            event, None, None, make_ctx(), _cfg(), silence_rules=[make_rule()], now=NOW
        )
        assert (d.tier, d.reason, d.stage) == (base.tier, base.reason, base.stage)


class TestSilenceStore:
    @pytest.fixture()
    def store(self, tmp_path):
        return SilenceStore(str(tmp_path / "silences.jsonl"))

    def test_create_then_list(self, store):
        rule = store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(hours=1), now=NOW,
        )
        active = store.list_rules(now=NOW)
        assert [r.id for r in active] == [rule.id]
        assert active[0].created_by == "kim"

    def test_revoke_hides_rule_but_keeps_history(self, store):
        rule = store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(hours=1), now=NOW,
        )
        assert store.revoke(rule.id, revoked_by="lee", now=NOW + timedelta(minutes=5))
        assert store.active_rules(now=NOW + timedelta(minutes=10)) == []
        history = store.list_rules(include_inactive=True, now=NOW + timedelta(minutes=10))
        assert len(history) == 1 and history[0].revoked_at is not None

    def test_revoke_is_append_only(self, store):
        rule = store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(hours=1), now=NOW,
        )
        store.revoke(rule.id, revoked_by="lee")
        lines = store.path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # create + revoke, 원 레코드는 고쳐지지 않는다

    def test_revoke_unknown_or_twice_returns_false(self, store):
        assert store.revoke("nope", revoked_by="x") is False
        rule = store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(hours=1), now=NOW,
        )
        assert store.revoke(rule.id, revoked_by="x") is True
        assert store.revoke(rule.id, revoked_by="x") is False

    def test_expired_rules_drop_out_of_active(self, store):
        store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(minutes=30), now=NOW,
        )
        assert len(store.active_rules(now=NOW)) == 1
        assert store.active_rules(now=NOW + timedelta(hours=1)) == []

    def test_corrupt_lines_are_skipped(self, store):
        store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(hours=1), now=NOW,
        )
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write("{not json}\n")
        assert len(store.list_rules(now=NOW)) == 1

    def test_disabled_store_is_inert(self, tmp_path):
        store = SilenceStore(str(tmp_path / "s.jsonl"), enabled=False)
        store.create(
            server_name="WEB-*", max_severity=2, reason="배포",
            created_by="kim", expires_at=NOW + timedelta(hours=1), now=NOW,
        )
        assert store.list_rules() == []
        assert not store.path.exists()

    def test_missing_file_returns_empty(self, store):
        assert store.list_rules() == []
        assert store.active_rules() == []
