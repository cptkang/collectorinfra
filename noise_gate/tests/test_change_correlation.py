"""변경 상관 domain 단위 테스트 + 게이트 step9 승격 (Plan 60 E5 · D-081 초안).

- `overlay_changes`(domain·순수): 영향범위(resource_id) 매칭·타임라인 오버레이·피드
  부재(빈 changes)→빈 후보·결정적 정렬(event_time 내림차순, 동시각 tie-break)을 합성
  ChangeEvent로 검증한다. domain은 stdlib만 의존하므로 인프라 없이 순수 테스트 가능하다.
- 게이트 step9(`decide_notification`): change_nearby→promote 경로(**억제 아님·승격만**),
  off→무변경, 심각도3 단락 유지, signals 스키마 미훼손(change_nearby는 signals에 없음).
"""

from __future__ import annotations

from types import SimpleNamespace

from noise_gate.domain.change_correlation import ChangeCandidate, overlay_changes
from noise_gate.domain.notification_policy import (
    TIER_PAGE,
    TIER_TICKET,
    decide_notification,
)


def _chg(resource_id, event_time, *, change_type="deploy", description="d"):
    """합성 ChangeEvent(덕 타이핑 — resource_id/change_type/description/event_time)."""
    return SimpleNamespace(
        resource_id=resource_id,
        resource_type="server.Server",
        change_type=change_type,
        description=description,
        event_time=event_time,
    )


# ─────────────────────────────────────────────────────────────
# overlay_changes (domain·순수·결정적)
# ─────────────────────────────────────────────────────────────
class TestOverlayTimeline:
    def test_only_changes_in_window(self):
        # 창 [900, 1000]. 창 안(950)만 후보, 창 이전(899)·알람 이후(1001)는 배제.
        changes = [_chg("R1", 950), _chg("R1", 899), _chg("R1", 1001)]
        out = overlay_changes((900, 1000), changes, affected_resource_ids={"R1"})
        assert [c.event_time for c in out] == [950]

    def test_boundaries_inclusive(self):
        # 창 경계(start·end)는 포함.
        changes = [_chg("R1", 900), _chg("R1", 1000)]
        out = overlay_changes((900, 1000), changes, affected_resource_ids={"R1"})
        assert {c.event_time for c in out} == {900, 1000}

    def test_proximity_seconds(self):
        # 근접도 = end(알람 시각) − event_time (작을수록 근접).
        out = overlay_changes((900, 1000), [_chg("R1", 980)], affected_resource_ids={"R1"})
        assert out[0].proximity_seconds == 20

    def test_empty_changes_empty(self):
        # 피드 부재(빈 changes) → 빈 후보(graceful).
        assert overlay_changes((900, 1000), [], affected_resource_ids={"R1"}) == []
        assert overlay_changes((900, 1000), None, affected_resource_ids={"R1"}) == []

    def test_unparseable_event_time_skipped(self):
        changes = [_chg("R1", None), _chg("R1", "not-int"), _chg("R1", 950)]
        out = overlay_changes((900, 1000), changes, affected_resource_ids={"R1"})
        assert [c.event_time for c in out] == [950]


class TestOverlayResourceMatch:
    def test_scope_filters_to_affected(self):
        # 영향범위 {R1}만 매칭 — R2 변경은 배제.
        changes = [_chg("R1", 950), _chg("R2", 960)]
        out = overlay_changes((900, 1000), changes, affected_resource_ids={"R1"})
        assert [c.resource_id for c in out] == ["R1"]

    def test_empty_scope_is_time_only(self):
        # affected 미지정(None/빈) → 리소스 필터 없이 창 내 전 변경 오버레이(graceful).
        changes = [_chg("R1", 950), _chg("R2", 960)]
        out_none = overlay_changes((900, 1000), changes, affected_resource_ids=None)
        out_empty = overlay_changes((900, 1000), changes, affected_resource_ids=set())
        assert {c.resource_id for c in out_none} == {"R1", "R2"}
        assert {c.resource_id for c in out_empty} == {"R1", "R2"}

    def test_int_resource_id_normalized(self):
        # DB가 bigint resource_id를 줘도 문자열 정규화로 스코프 매칭.
        out = overlay_changes((900, 1000), [_chg(10, 950)], affected_resource_ids={"10"})
        assert out[0].resource_id == "10"


class TestOverlayDeterministicSort:
    def test_event_time_descending(self):
        changes = [_chg("R1", 910), _chg("R1", 990), _chg("R1", 950)]
        out = overlay_changes((900, 1000), changes, affected_resource_ids={"R1"})
        assert [c.event_time for c in out] == [990, 950, 910]  # 최신 우선

    def test_tie_break_resource_then_type(self):
        # 동시각 970 — resource_id 오름차순 우선, 같으면 change_type 오름차순.
        changes = [
            _chg("R1", 970, change_type="deploy"),
            _chg("R1", 970, change_type="config"),
            _chg("R0", 970, change_type="deploy"),
        ]
        out = overlay_changes((900, 1000), changes, affected_resource_ids={"R0", "R1"})
        assert [(c.resource_id, c.change_type) for c in out] == [
            ("R0", "deploy"),
            ("R1", "config"),
            ("R1", "deploy"),
        ]

    def test_returns_change_candidate_instances(self):
        out = overlay_changes((900, 1000), [_chg("R1", 950)], affected_resource_ids={"R1"})
        assert isinstance(out[0], ChangeCandidate)


# ─────────────────────────────────────────────────────────────
# 게이트 step9 — change_nearby → promote (억제 아님·승격만)
# ─────────────────────────────────────────────────────────────
def _event(severity=2):
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def _cfg(**over):
    base = dict(
        suppress_max_severity=2,
        importance_value_map={"MID": "보통", "HIGH": "높음", "LOW": "낮음"},
        resolved_to_dashboard=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(importance="MID", *, change_nearby=None, **extra):
    ctx = {
        "importance_id": importance, "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "change_nearby": change_nearby, "source": "polestar_db",
    }
    ctx.update(extra)
    return ctx


class TestGateChangePromote:
    def test_change_nearby_promotes_one_step(self):
        # sev2×보통 = TICKET → 변경 근접 승격 → PAGE(1단계).
        d = decide_notification(_event(2), None, None, _ctx("MID", change_nearby=True), _cfg())
        assert d.tier == TIER_PAGE
        assert "변경 근접(원인성)" in d.reason

    def test_off_no_change(self):
        # change_nearby None/False → 승격 없음(매트릭스 그대로 TICKET).
        base = decide_notification(_event(2), None, None, _ctx("MID", change_nearby=None), _cfg())
        false_ = decide_notification(_event(2), None, None, _ctx("MID", change_nearby=False), _cfg())
        assert base.tier == TIER_TICKET
        assert false_.tier == TIER_TICKET
        assert "변경 근접" not in base.reason

    def test_promote_not_suppress_wins_over_demote(self):
        # 억제 아님·승격 우선: change_nearby(promote) + is_routine(demote) 충돌 → 승격 우선.
        analysis = SimpleNamespace(is_routine=True, pattern_type="")
        d = decide_notification(
            _event(2), None, analysis, _ctx("MID", change_nearby=True), _cfg()
        )
        assert d.tier == TIER_PAGE  # SUPPRESS/강등 아님
        assert "변경 근접(원인성)" in d.reason

    def test_severity3_short_circuits(self):
        # 심각도3은 change_nearby=True여도 step3에서 PAGE 단락(변경 승격 미도달).
        d = decide_notification(_event(3), None, None, _ctx("MID", change_nearby=True), _cfg())
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason
        assert "변경 근접" not in d.reason

    def test_change_nearby_not_in_signals(self):
        # signals 동결 스키마 미훼손 — change_nearby는 signals에 넣지 않는다(§7.2).
        d = decide_notification(_event(2), None, None, _ctx("MID", change_nearby=True), _cfg())
        assert "change_nearby" not in d.signals
