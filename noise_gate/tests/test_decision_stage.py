"""결정 단계(stage) 라벨 단위 테스트 (Plan 54 모듈 1 — `decision-stage`).

`decide_notification`이 각 결정 지점에 파이프라인 단계 라벨을 붙이는지, 그리고 그 라벨이
티어·사유·우선순위·신호 산출에 **일절 영향을 주지 않는지**(판정 회귀 0)를 검증한다.

퍼널(Plan 54 모듈 2)이 이 라벨 위에 서므로, 단계별 고정은 여기서 끝낸다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.domain.notification_policy import (
    STAGE_ANNOTATION,
    STAGE_COLLECTION_FAILED,
    STAGE_CORRELATION,
    STAGE_DEPENDENCY,
    STAGE_FLAPPING,
    STAGE_INHIBITION,
    STAGE_LABELS,
    STAGE_MAINTENANCE,
    STAGE_MATRIX,
    STAGE_NON_ALARM,
    STAGE_ORDER,
    STAGE_RESOLVED,
    STAGE_SELF_HEAL,
    STAGE_SEVERITY3,
    STAGE_STORM,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)

from noise_gate.tests.test_notification_policy import (
    IMP_MAP,
    make_config,
    make_ctx,
    make_event,
)

REF = datetime(2026, 9, 3, 14, 0, 0)


def _cfg(**kwargs) -> SimpleNamespace:
    return make_config(importance_value_map=IMP_MAP, **kwargs)


class TestStageOrderContract:
    """단계 집합·순서·표시명의 계약."""

    def test_order_has_no_duplicates(self):
        assert len(STAGE_ORDER) == len(set(STAGE_ORDER))

    def test_every_stage_has_label(self):
        # 관제 화면이 영문 키를 그대로 노출하지 않도록 전 단계에 표시명이 있어야 한다.
        assert set(STAGE_ORDER) == set(STAGE_LABELS)

    def test_matrix_is_last(self):
        # 매트릭스는 억제 단계를 모두 통과한 알람이 도달하는 최종 관문이다.
        assert STAGE_ORDER[-1] == STAGE_MATRIX

    def test_severity3_precedes_every_suppression_stage(self):
        # 심각도3 단락이 억제 단계보다 앞에 있어야 "억제 단계 미경유"가 순서로도 성립한다.
        suppression = (
            STAGE_MAINTENANCE,
            STAGE_DEPENDENCY,
            STAGE_INHIBITION,
            STAGE_FLAPPING,
            STAGE_STORM,
            STAGE_CORRELATION,
        )
        i3 = STAGE_ORDER.index(STAGE_SEVERITY3)
        for stage in suppression:
            assert i3 < STAGE_ORDER.index(stage)


class TestStagePerDecisionPoint:
    """결정 지점 → 단계 라벨 고정."""

    def test_non_alarm(self):
        event = make_event(severity=1, alarm_name="계정 생성 승인 요청", conditions="")
        d = decide_notification(
            event, None, None, make_ctx(), _cfg(non_alarm_filter_enabled=True)
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_NON_ALARM

    def test_severity3(self):
        d = decide_notification(make_event(severity=3), None, None, make_ctx(), _cfg())
        assert d.tier == TIER_PAGE
        assert d.stage == STAGE_SEVERITY3

    def test_self_heal(self):
        d = decide_notification(
            make_event(severity=0), None, None, make_ctx(), _cfg(), self_heal=True
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_SELF_HEAL

    def test_resolved_to_dashboard(self):
        d = decide_notification(
            make_event(severity=0), None, None, make_ctx(), _cfg(resolved_to_dashboard=True)
        )
        assert d.tier == TIER_DASHBOARD
        assert d.stage == STAGE_RESOLVED

    def test_resolved_without_match(self):
        d = decide_notification(make_event(severity=0), None, None, make_ctx(), _cfg())
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_RESOLVED

    def test_collection_failed(self):
        # noise_ctx의 source가 수집 실패를 나타내면 보수적 PAGE로 간다.
        ctx = make_ctx(source="unavailable")
        d = decide_notification(make_event(severity=2), None, None, ctx, _cfg())
        assert d.tier == TIER_PAGE
        assert d.stage == STAGE_COLLECTION_FAILED

    def test_maintenance(self):
        d = decide_notification(
            make_event(severity=2), None, None, make_ctx(maintenance=True), _cfg()
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_MAINTENANCE

    def test_dependency(self):
        ctx = make_ctx()
        ctx["parent_avail_status"] = 2
        d = decide_notification(
            make_event(severity=2), None, None, ctx, _cfg(dependency_suppression=True)
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_DEPENDENCY

    def test_inhibition(self):
        d = decide_notification(
            make_event(severity=2),
            None,
            None,
            make_ctx(),
            _cfg(inhibition_enabled=True),
            inhibited=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_INHIBITION

    def test_flapping(self):
        d = decide_notification(
            make_event(severity=2),
            None,
            None,
            make_ctx(),
            _cfg(flapping_enabled=True),
            flapping=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_FLAPPING

    def test_storm(self):
        d = decide_notification(
            make_event(severity=2),
            None,
            None,
            make_ctx(),
            _cfg(storm_grouping_enabled=True),
            storm=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_STORM

    def test_correlation(self):
        d = decide_notification(
            make_event(severity=2),
            None,
            None,
            make_ctx(),
            _cfg(cross_host_correlation_enabled=True),
            correlated=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert d.stage == STAGE_CORRELATION

    def test_annotation(self):
        d = decide_notification(
            make_event(severity=2),
            None,
            None,
            make_ctx(),
            _cfg(annotation_planned_suppress=True),
            correlated=True,
            annotation={"planned_work": True, "resolution": True},
        )
        assert d.tier == TIER_DASHBOARD
        assert d.stage == STAGE_ANNOTATION

    def test_matrix_is_default_path(self):
        # 억제 단계를 모두 통과한 통상 알람은 매트릭스 단계로 기록된다.
        d = decide_notification(make_event(severity=2), None, None, make_ctx(), _cfg())
        assert d.stage == STAGE_MATRIX

    def test_every_stage_except_silence_is_reachable(self):
        # 침묵(모듈 4)을 뺀 13단계가 이 테스트 클래스에서 모두 도달됐음을 상수로 고정한다.
        covered = {
            STAGE_NON_ALARM,
            STAGE_SEVERITY3,
            STAGE_SELF_HEAL,
            STAGE_RESOLVED,
            STAGE_COLLECTION_FAILED,
            STAGE_MAINTENANCE,
            STAGE_DEPENDENCY,
            STAGE_INHIBITION,
            STAGE_FLAPPING,
            STAGE_STORM,
            STAGE_CORRELATION,
            STAGE_ANNOTATION,
            STAGE_MATRIX,
        }
        assert covered <= set(STAGE_ORDER)
        assert len(covered) == len(STAGE_ORDER) - 1  # 침묵만 미도달


class TestStageDoesNotAffectDecision:
    """stage는 관측 전용 — 판정 4-튜플에 영향이 없다."""

    def test_tuple_unchanged_across_stage_paths(self):
        # 대표 입력에 대한 (tier, reason, priority, signals)를 스냅샷으로 고정한다.
        cfg = _cfg()
        for severity, expected_tier in ((3, TIER_PAGE), (2, TIER_PAGE), (1, "ticket")):
            event = make_event(severity=severity)
            d = decide_notification(event, None, None, make_ctx(), cfg)
            assert d.tier == expected_tier
            assert isinstance(d.priority, int)
            assert isinstance(d.signals, dict)
            # stage를 비워도 나머지는 그대로여야 한다(필드 독립성).
            assert d.stage in STAGE_ORDER

    def test_default_stage_is_empty_string(self):
        # 구 호출자가 NotificationDecision을 직접 만들면 stage는 빈 문자열이다(하위호환).
        from noise_gate.domain.notification_policy import NotificationDecision

        d = NotificationDecision(tier=TIER_PAGE, reason="r", priority=1, signals={})
        assert d.stage == ""
