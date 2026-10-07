"""plans/144 독립 검증 2회차 — Wave A(D-320 · §4.4) · W3 step 7.6 · W4 사후 승격 · 메모리 가드.

구현자 테스트(test_plan144_*.py)와 겹치지 않는 축만 둔다.

    A. step 7.6 성질 — 격자 전수: 상한 비활성 모드는 판정 6-튜플 불변 · 상한은 티어를 올리지 않음 ·
       applied ⇔ stage==cross_source · 심각도3 PAGE
    B. 시계 혼용 — 창은 발생 시각, idle 종료는 도착 시각
    C. 역방향 규칙에 enforce: true를 달아도 폴스타 증상은 강등되지 않는다(로더 강제 link → 정책)
    D. 사후 승격 — 원 판정 객체 무수정
    E. 메모리 가드 — Episode.cause_ids 상한(결함 재현 · xfail strict)
"""

from __future__ import annotations

import dataclasses
import itertools
from types import SimpleNamespace

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.domain.cross_source import (
    ACTION_DEMOTE,
    ACTION_LINK,
    MISSING_WINDOW,
    SOURCE_JENNIFER,
    SOURCE_POLESTAR,
    EpisodeAlarm,
    EpisodeTracker,
)
from noise_gate.domain.notification_policy import (
    _TIER_RANK,
    STAGE_CROSS_SOURCE,
    STAGE_MATRIX,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
)
from noise_gate.infrastructure.cross_source_rules import parse_rules
from noise_gate.tests.test_notification_policy import IMP_MAP, make_config, make_ctx, make_event

T0 = 1_791_330_000.0


def _cs(mode: str, action: str, enforce: bool) -> dict:
    return {
        "episode_id": "ep-v2", "mode": mode, "host_key": "h", "rule_id": "r",
        "cause_alarm_id": "c", "lag_seconds": 1.0, "action": action, "enforce": enforce,
        "missing": [], "applied": False,
    }


_CAP = _cs("enforce", ACTION_DEMOTE, True)
_NEUTRAL = [
    _cs(m, a, e)
    for m in ("off", "shadow", "annotate", "enforce")
    for a in (ACTION_DEMOTE, ACTION_LINK)
    for e in (True, False)
    if not (m == "enforce" and a == ACTION_DEMOTE and e is True)
]

_APP = {"fatal_events": 1, "event_types": ["E"], "was_signals": [], "source": "s"}


def _grid():
    for sev, imp, noti, routine, llm, change, app, annot, storm in itertools.product(
        (1, 2, 3), ("HIGH", "MID", "LOW", None), (None, "notify", "suppress"), (None, True, False),
        (None, "actionable", "noise"), (False, True), (None, _APP),
        (None, {"planned_work": True, "resolution": True}), (False, True),
    ):
        cfg = make_config(
            importance_value_map=IMP_MAP, enable_llm_actionability=True,
            annotation_planned_suppress=True,
        )
        ctx = make_ctx(importance_id=imp, noti_policy=noti)
        if change:
            ctx["change_nearby"] = True
        if app:
            ctx["app_impact"] = app
        analysis = SimpleNamespace(
            pattern_type="", is_routine=routine, llm_actionability=llm, ai_message_severity=None,
        )
        yield make_event(severity=sev), analysis, ctx, cfg, dict(annotation=annot, storm=storm)


def _verdict(d: NotificationDecision) -> tuple:
    return (d.tier, d.reason, d.priority, d.signals, d.fingerprint, d.stage)


# ═════════════════════════════════════════════════════════════════════════════
# A. step 7.6 성질(격자 전수)
# ═════════════════════════════════════════════════════════════════════════════


def test_non_cap_modes_leave_verdict_unchanged_on_grid():
    """off·shadow·annotate·link·규칙 enforce false — 판정 6-튜플이 cross_source=None과 같다."""
    n = 0
    for ev, an, ctx, cfg, kw in _grid():
        base = _verdict(decide_notification(ev, None, an, ctx, cfg, **kw))
        for cs in _NEUTRAL:
            d = decide_notification(ev, None, an, ctx, cfg, cross_source=cs, **kw)
            assert _verdict(d) == base, (cs, base)
            assert d.evidence["cross_source"]["applied"] is False
            assert d.evidence["episode_id"] == "ep-v2"
            n += 1
    assert n > 10000


def test_cap_never_raises_and_applied_matches_stage_on_grid():
    """상한은 티어를 올리지 않고, 실제로 낮췄을 때만 stage=cross_source·applied=True다."""
    for ev, an, ctx, cfg, kw in _grid():
        base = decide_notification(ev, None, an, ctx, cfg, **kw)
        d = decide_notification(ev, None, an, ctx, cfg, cross_source=_CAP, **kw)
        assert _TIER_RANK[d.tier] <= _TIER_RANK[base.tier]
        assert d.evidence["cross_source"]["applied"] is (d.stage == STAGE_CROSS_SOURCE)
        if ev.severity == 3:
            assert d.tier == TIER_PAGE and d.stage != STAGE_CROSS_SOURCE
        if d.stage == STAGE_CROSS_SOURCE:
            assert d.tier == TIER_DASHBOARD
            assert _TIER_RANK[d.evidence["capped_from"]] > _TIER_RANK[TIER_DASHBOARD]
        elif base.stage == STAGE_MATRIX:
            # 매트릭스 결과가 DASHBOARD 이하였으면 판정은 그대로다.
            assert _verdict(d) == _verdict(base)


def test_cap_overrides_app_impact_promotion_on_symptom():
    """증상(정책 계층 기준) 판정에서 9.5 앱 영향 승격 PAGE도 상한이 DASHBOARD로 낮춘다.

    제니퍼 증상에는 게이트가 app_impact를 넣지 않으므로(폴스타 전용) 운영 경로에선 닿지 않지만,
    상한이 9.5 뒤 결과를 본다는 순서를 고정한다(폴스타 증상은 로더가 link로 막는다 — C 참조).
    """
    cfg = make_config(importance_value_map=IMP_MAP)
    ctx = make_ctx(importance_id="MID")
    ctx["app_impact"] = _APP
    base = decide_notification(make_event(severity=2), None, None, ctx, cfg)
    assert base.tier == TIER_PAGE and "앱 영향 승격" in base.reason
    d = decide_notification(make_event(severity=2), None, None, ctx, cfg, cross_source=_CAP)
    assert d.tier == TIER_DASHBOARD and d.evidence["capped_from"] == TIER_PAGE


# ═════════════════════════════════════════════════════════════════════════════
# B. 시계 혼용 — 창은 발생 시각, idle은 도착 시각
# ═════════════════════════════════════════════════════════════════════════════

_RULES_YAML = {
    "version": 1,
    "rules": [{
        "id": "fw", "enabled": True, "enforce": True,
        "cause": {"source": "polestar", "kinds": ["cpu"]},
        "effect": {"source": "jennifer", "was_kinds": ["was_service_queuing"]},
        "equal": ["zone", "host_key"],
        "window": {"cause_before_seconds": 600, "cause_after_seconds": 60},
        "action": "demote_effect",
    }],
}


def _p(alarm_id: str, at: float) -> EpisodeAlarm:
    return EpisodeAlarm(
        alarm_id=alarm_id, source=SOURCE_POLESTAR, zone="gongjon", host_key="xsapp01",
        host_key_strength="strong", db_id="polestar_cm_gp", occurred_at=at, severity=2,
        kind="cpu", fingerprint=f"fp-{alarm_id}",
    )


def _j(alarm_id: str, at: float, *, was=("was_service_queuing",)) -> EpisodeAlarm:
    return EpisodeAlarm(
        alarm_id=alarm_id, source=SOURCE_JENNIFER, zone="gongjon", host_key="xsapp01",
        host_key_strength="strong", db_id="jennifer_common", occurred_at=at, severity=2,
        was_kinds=tuple(was), fingerprint=f"fp-{alarm_id}",
    )


def test_window_uses_occurred_at_not_arrival():
    tr = EpisodeTracker(parse_rules(_RULES_YAML), mode="enforce", idle_seconds=900)
    # 원인은 발생 직후가 아니라 590초 늦게 도착(폴링 지연) — 창은 발생 시각 기준이라 충족.
    tr.observe(_p("P-1", T0), now=T0 + 590)
    tr.record_tier(tr.episode_for("gongjon", "xsapp01").id, "P-1", TIER_TICKET)
    sig = tr.observe(_j("J-1", T0 + 600), now=T0 + 600)
    assert sig["action"] == ACTION_DEMOTE and sig["lag_seconds"] == 600

    # 도착은 즉시지만 발생 시각이 창(600초) 밖이면 link.
    tr2 = EpisodeTracker(parse_rules(_RULES_YAML), mode="enforce", idle_seconds=900)
    tr2.observe(_p("P-2", T0), now=T0 + 600)
    tr2.record_tier(tr2.episode_for("gongjon", "xsapp01").id, "P-2", TIER_TICKET)
    sig2 = tr2.observe(_j("J-2", T0 + 601), now=T0 + 601)
    assert sig2["action"] == ACTION_LINK and MISSING_WINDOW in sig2["missing"]


def test_idle_close_uses_arrival_clock():
    tr = EpisodeTracker(parse_rules(_RULES_YAML), mode="enforce", idle_seconds=900)
    tr.observe(_p("P-1", T0), now=T0)
    # 발생 시각은 창 안이지만 도착이 idle(900초)을 넘겨 왔다 — 사건은 이미 닫혀 새 사건이 된다.
    sig = tr.observe(_j("J-1", T0 + 30), now=T0 + 901)
    assert "action" not in sig  # 원인 후보 없음(새 사건)
    assert len(tr) == 1


# ═════════════════════════════════════════════════════════════════════════════
# C. 역방향 enforce: true — 폴스타 증상은 강등되지 않는다
# ═════════════════════════════════════════════════════════════════════════════


def test_reverse_rule_with_enforce_true_never_caps_polestar_symptom():
    rules = parse_rules({
        "version": 1,
        "rules": [{
            "id": "rev", "enabled": True, "enforce": True,
            "cause": {"source": "jennifer", "was_kinds": ["*"]},
            "effect": {"source": "polestar", "kinds": ["process"]},
            "equal": ["zone", "host_key"],
            "window": {"cause_before_seconds": 600, "cause_after_seconds": 60},
            "action": "demote_effect",
        }],
    })
    assert rules[0].action == "link" and rules[0].enforce is True
    tr = EpisodeTracker(rules, mode="enforce", idle_seconds=900)
    tr.observe(_j("J-1", T0), now=T0)
    tr.record_tier(tr.episode_for("gongjon", "xsapp01").id, "J-1", TIER_PAGE)
    proc = dataclasses.replace(_p("P-1", T0 + 30), kind="process")
    sig = tr.observe(proc, now=T0 + 30)
    assert sig["action"] == ACTION_LINK and sig["missing"] == []
    cfg = make_config(importance_value_map=IMP_MAP)
    ctx = make_ctx(importance_id="HIGH")
    base = decide_notification(make_event(severity=2), None, None, ctx, cfg)
    d = decide_notification(make_event(severity=2), None, None, ctx, cfg, cross_source=sig)
    assert base.tier == TIER_PAGE
    assert _verdict(d) == _verdict(base) and d.evidence["cross_source"]["applied"] is False


# ═════════════════════════════════════════════════════════════════════════════
# D. 사후 승격 — 원 판정 객체 무수정
# ═════════════════════════════════════════════════════════════════════════════


async def test_send_late_promotion_does_not_mutate_original_decision(monkeypatch):
    async def _no_workb(*_a, **_k):
        return None

    monkeypatch.setattr(notifier_mod, "_send_workb_late_promotion", _no_workb)

    class _Pub:
        def __init__(self):
            self.payloads = []

        async def publish(self, payload):
            self.payloads.append(payload)

    ev = make_event(severity=2)
    result = AlarmAnalysisResult(
        alarm_event=ev, severity_label="경고", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=["workb"],
    )
    original = decide_notification(
        ev, None, None, make_ctx(importance_id="MID"), make_config(importance_value_map=IMP_MAP),
    )
    assert original.tier in (TIER_DASHBOARD, TIER_TICKET)
    before = dataclasses.asdict(original)
    pub = _Pub()
    sent = await notifier_mod.send_late_promotion(
        result, original, {"episode_id": "ep-1", "reason": "사후 승격: x"},
        SimpleNamespace(workb=SimpleNamespace()), pub,
    )
    assert sent == {"incident": True, "workb": True}
    assert dataclasses.asdict(original) == before
    assert "late_promotion" not in original.evidence
    assert pub.payloads[0]["late_promotion"]["episode_id"] == "ep-1"


# ═════════════════════════════════════════════════════════════════════════════
# E. 메모리 가드 — Episode.cause_ids
# ═════════════════════════════════════════════════════════════════════════════


def test_cause_ids_bounded_by_member_cap():
    tr = EpisodeTracker(parse_rules(_RULES_YAML), mode="enforce", idle_seconds=900, max_members=4)
    for i in range(50):
        t = T0 + i * 60
        tr.observe(_p(f"P-{i}", t), now=t)
        ep = tr.episode_for("gongjon", "xsapp01")
        tr.record_tier(ep.id, f"P-{i}", TIER_TICKET)
        tr.observe(_j(f"J-{i}", t + 1), now=t + 1)
    ep = tr.episode_for("gongjon", "xsapp01")
    assert len(ep.members) <= 4
    assert len(ep.cause_ids) <= 4
