"""plans/144 W3 — 폴스타×제니퍼 크로스소스 사건(episode) 상관.

A. 결합 판정 순수 함수(조건 5개 + 모호·해소 — 하나씩 빠질 때 link)
B. 사건 추적기(도착 순서 3가지 · gp/yd 모호 · idle/전부 해소 종료 · 상한 정리 · 대표 · id)
C. 규칙 표 적재(출하 표 · 역방향 강제 link · 비활성 행 · 실패 → None)
D. 정책 step 7.6(모드별 · 티어 상승 없음 · 심각도3 · 앞 단계 억제 우선)
E. 워커 종단(off 비트 동일 · 그래프 반환 티어 기록 · enforce DASHBOARD · 증상 먼저 → 연관 증상)
F. 리플레이 하네스 shadow 지표 · G. incident open 페이로드 episode_id · H. 감사 레코드
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_notifier import _incident_open_payload
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmAnalysisResult
from noise_gate.domain.cross_source import (
    ACTION_DEMOTE,
    ACTION_LINK,
    MISSING_CAUSE_RESOLVED,
    MISSING_CAUSE_TIER,
    MISSING_DB_AMBIGUOUS,
    MISSING_HOST_KEY,
    MISSING_HOST_KEY_STRENGTH,
    MISSING_SEVERITY,
    MISSING_WINDOW,
    MISSING_ZONE,
    RULE_ACTION_DEMOTE,
    RULE_ACTION_LINK,
    SOURCE_JENNIFER,
    SOURCE_POLESTAR,
    CrossSourceRule,
    EpisodeAlarm,
    EpisodeMember,
    EpisodeTracker,
    SideSpec,
    episode_id_for,
    evaluate_link,
)
from noise_gate.domain.notification_policy import (
    STAGE_CROSS_SOURCE,
    STAGE_DESCRIPTIONS,
    STAGE_LABELS,
    STAGE_MAINTENANCE,
    STAGE_MATRIX,
    STAGE_SEVERITY3,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
    stage_from_reason,
)
from noise_gate.infrastructure.cross_source_rules import load_rules, parse_rules
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.scripts import replay_noise as rn
from noise_gate.tests.test_notification_policy import IMP_MAP, make_config, make_ctx, make_event
from noise_gate.tests.test_stage_evidence import _gate_state, _records, _run_config

REPO = Path(__file__).resolve().parents[2]
SHIPPED_RULES = REPO / "config" / "cross_source_rules.yaml"
FIXTURE = REPO / "noise_gate" / "testdata" / "cross_source" / "mock_events.jsonl"

T0 = 1_791_330_000.0  # 임의 기준 epoch(초)


# ═════════════════════════════════════════════════════════════════════════════
# 공통 픽스처
# ═════════════════════════════════════════════════════════════════════════════


def _rule(**over) -> CrossSourceRule:
    base = dict(
        id="r1",
        cause=SideSpec(source=SOURCE_POLESTAR, kinds=frozenset({"cpu", "memory"})),
        effect=SideSpec(source=SOURCE_JENNIFER, was_kinds=frozenset({"was_service_queuing"})),
        cause_before_seconds=600,
        cause_after_seconds=60,
        action=RULE_ACTION_DEMOTE,
        enforce=False,
    )
    base.update(over)
    return CrossSourceRule(**base)


def _cause(**over) -> EpisodeAlarm:
    base = dict(
        alarm_id="P-1", source=SOURCE_POLESTAR, zone="gongjon", host_key="xsapp01",
        host_key_strength="strong", db_id="polestar_cm_gp", occurred_at=T0, severity=2,
        kind="cpu", alarm_name="CPU 사용률", fingerprint="fp-cpu",
    )
    base.update(over)
    return EpisodeAlarm(**base)


def _effect(**over) -> EpisodeAlarm:
    base = dict(
        alarm_id="J-1", source=SOURCE_JENNIFER, zone="gongjon", host_key="xsapp01",
        host_key_strength="strong", db_id="jennifer_common", occurred_at=T0 + 95, severity=2,
        was_kinds=("was_service_queuing",), alarm_name="SERVICE_QUEUING", fingerprint="fp-q",
    )
    base.update(over)
    return EpisodeAlarm(**base)


def _member(alarm: EpisodeAlarm | None = None, *, tier: str | None = TIER_TICKET,
            resolved: bool = False) -> EpisodeMember:
    return EpisodeMember(alarm=alarm or _cause(), arrived_at=T0, tier=tier, resolved=resolved)


def _tracker(*rules: CrossSourceRule, **kw) -> EpisodeTracker:
    kw.setdefault("mode", "shadow")
    kw.setdefault("idle_seconds", 900)
    return EpisodeTracker(rules or (_rule(),), **kw)


# ═════════════════════════════════════════════════════════════════════════════
# A. 결합 판정 순수 함수
# ═════════════════════════════════════════════════════════════════════════════


class TestEvaluateLink:
    def test_all_conditions_met_is_demote(self):
        link = evaluate_link(_rule(), _member(), _effect())
        assert link is not None
        assert link.action == ACTION_DEMOTE and link.missing == ()
        assert link.lag_seconds == 95.0 and link.cause_alarm_id == "P-1"
        assert link.host_key == "xsapp01" and link.rule_id == "r1" and link.enforce is False

    @pytest.mark.parametrize(
        ("cause_over", "effect_over", "member_over", "ambiguous", "label"),
        [
            # ① 같은 존
            ({}, {"zone": "bankjon"}, {}, False, MISSING_ZONE),
            ({"zone": ""}, {"zone": ""}, {}, False, MISSING_ZONE),
            # ② 같은 host_key · 양쪽 strong · 모호 아님
            ({}, {"host_key": "other"}, {}, False, MISSING_HOST_KEY),
            ({}, {"host_key_strength": "weak"}, {}, False, MISSING_HOST_KEY_STRENGTH),
            ({"host_key_strength": "none"}, {}, {}, False, MISSING_HOST_KEY_STRENGTH),
            ({}, {}, {}, True, MISSING_DB_AMBIGUOUS),
            # ③ 창 — 원인이 before 창보다 이르거나 after 창보다 늦게 발생
            ({}, {"occurred_at": T0 + 601}, {}, False, MISSING_WINDOW),
            ({"occurred_at": T0 + 95 + 61}, {}, {}, False, MISSING_WINDOW),
            # ④ 원인 통보(page·ticket) · 미해소
            ({}, {}, {"tier": TIER_DASHBOARD}, False, MISSING_CAUSE_TIER),
            ({}, {}, {"tier": None}, False, MISSING_CAUSE_TIER),
            ({}, {}, {"resolved": True}, False, MISSING_CAUSE_RESOLVED),
            # ⑤ 증상 심각도 < 3
            ({}, {"severity": 3}, {}, False, MISSING_SEVERITY),
        ],
    )
    def test_each_missing_condition_falls_back_to_link(
        self, cause_over, effect_over, member_over, ambiguous, label
    ):
        member = _member(_cause(**cause_over), **member_over)
        link = evaluate_link(_rule(), member, _effect(**effect_over), db_ambiguous=ambiguous)
        assert link is not None
        assert link.action == ACTION_LINK
        assert link.missing == (label,)

    def test_window_edges_are_inclusive(self):
        assert evaluate_link(_rule(), _member(), _effect(occurred_at=T0 + 600)).action == "demote"
        late_cause = _member(_cause(occurred_at=T0 + 95 + 60))
        assert evaluate_link(_rule(), late_cause, _effect()).action == ACTION_DEMOTE

    def test_rule_action_link_never_demotes(self):
        link = evaluate_link(_rule(action=RULE_ACTION_LINK), _member(), _effect())
        assert link is not None and link.action == ACTION_LINK and link.missing == ()

    def test_direction_mismatch_is_not_a_candidate(self):
        assert evaluate_link(_rule(), _member(_cause(kind="disk")), _effect()) is None
        assert evaluate_link(_rule(), _member(), _effect(was_kinds=("was_slow_sql",))) is None
        # 소스가 뒤바뀐 쌍(증상을 원인 자리에)
        assert evaluate_link(_rule(), _member(_effect()), _effect()) is None

    def test_wildcard_and_alarm_names(self):
        rule = _rule(
            cause=SideSpec(source=SOURCE_POLESTAR, alarm_names=frozenset({"서버 다운"})),
            effect=SideSpec(source=SOURCE_JENNIFER, was_kinds=frozenset({"*"})),
        )
        cause = _member(_cause(kind="", alarm_name="서버 다운"))
        assert evaluate_link(rule, cause, _effect(was_kinds=())).action == ACTION_DEMOTE


# ═════════════════════════════════════════════════════════════════════════════
# B. 사건 추적기
# ═════════════════════════════════════════════════════════════════════════════


class TestTrackerArrivalOrder:
    def test_cause_first_then_effect_is_demote(self):
        t = _tracker()
        s1 = t.observe(_cause(), T0)
        assert s1 is not None and "rule_id" not in s1  # 원인은 증상 판정 없음
        t.record_tier(s1["episode_id"], "P-1", TIER_TICKET)
        s2 = t.observe(_effect(), T0 + 100)
        assert s2["episode_id"] == s1["episode_id"]
        assert s2["action"] == ACTION_DEMOTE and s2["missing"] == []
        assert s2["applied"] is False and s2["mode"] == "shadow"
        assert s2["cause_alarm_id"] == "P-1" and s2["rule_id"] == "r1"

    def test_effect_first_then_cause_lists_notified_effect(self):
        t = _tracker()
        s1 = t.observe(_effect(occurred_at=T0), T0)
        assert "rule_id" not in s1  # 원인 미도착 — 묶을 근거 없음(붙잡지 않음)
        t.record_tier(s1["episode_id"], "J-1", TIER_PAGE)
        s2 = t.observe(_cause(occurred_at=T0 - 30), T0 + 40)
        assert s2["related_effects"] == ["J-1"]
        ep = t.get(s2["episode_id"])
        assert ep is not None and ep.member("J-1").tier == TIER_PAGE  # 증상 불변(철회 없음)
        assert ep.representative_id == "P-1"

    def test_effect_first_not_notified_is_not_listed(self):
        t = _tracker()
        s1 = t.observe(_effect(), T0)
        t.record_tier(s1["episode_id"], "J-1", TIER_DASHBOARD)
        s2 = t.observe(_cause(occurred_at=T0 + 50), T0 + 60)
        assert "related_effects" not in s2

    def test_cause_occurred_late_within_after_window(self):
        # 증상 발생 T0+95, 원인 발생 T0+125(30초 뒤) — 원인이 먼저 도착·판정됐다.
        t = _tracker()
        s1 = t.observe(_cause(occurred_at=T0 + 125), T0)
        t.record_tier(s1["episode_id"], "P-1", TIER_PAGE)
        s2 = t.observe(_effect(), T0 + 200)
        assert s2["action"] == ACTION_DEMOTE and s2["lag_seconds"] == -30.0

    def test_cause_occurred_after_window_is_link(self):
        t = _tracker()
        s1 = t.observe(_cause(occurred_at=T0 + 95 + 61), T0)
        t.record_tier(s1["episode_id"], "P-1", TIER_PAGE)
        s2 = t.observe(_effect(), T0 + 200)
        assert s2["action"] == ACTION_LINK and s2["missing"] == [MISSING_WINDOW]


class TestTrackerAmbiguityAndScope:
    def test_gp_yd_mix_in_same_episode_is_link(self):
        t = _tracker()
        s1 = t.observe(_cause(), T0)
        t.record_tier(s1["episode_id"], "P-1", TIER_TICKET)
        t.observe(_cause(alarm_id="P-2", db_id="polestar_cm_yd", kind="disk"), T0 + 5)
        s3 = t.observe(_effect(), T0 + 100)
        assert s3["action"] == ACTION_LINK and s3["missing"] == [MISSING_DB_AMBIGUOUS]

    def test_different_zone_is_separate_episode(self):
        t = _tracker()
        s1 = t.observe(_cause(), T0)
        t.record_tier(s1["episode_id"], "P-1", TIER_TICKET)
        s2 = t.observe(_effect(zone="bankjon"), T0 + 100)
        assert s2["episode_id"] != s1["episode_id"] and "rule_id" not in s2
        assert len(t) == 2

    def test_weak_match_is_link(self):
        t = _tracker()
        s1 = t.observe(_cause(), T0)
        t.record_tier(s1["episode_id"], "P-1", TIER_TICKET)
        s2 = t.observe(_effect(host_key_strength="weak"), T0 + 100)
        assert s2["action"] == ACTION_LINK and s2["missing"] == [MISSING_HOST_KEY_STRENGTH]

    def test_empty_host_key_is_not_tracked(self):
        t = _tracker()
        assert t.observe(_effect(host_key="", host_key_strength="none"), T0) is None
        assert len(t) == 0

    def test_prefers_demote_candidate_over_link(self):
        t = _tracker()
        old = t.observe(_cause(alarm_id="P-old", occurred_at=T0 - 700), T0)  # 창 밖
        t.record_tier(old["episode_id"], "P-old", TIER_PAGE)
        new = t.observe(_cause(alarm_id="P-new", kind="memory"), T0 + 1)
        t.record_tier(new["episode_id"], "P-new", TIER_TICKET)
        s = t.observe(_effect(), T0 + 100)
        assert s["action"] == ACTION_DEMOTE and s["cause_alarm_id"] == "P-new"

    def test_episode_id_is_deterministic(self):
        t = _tracker()
        s = t.observe(_cause(), T0)
        assert s["episode_id"] == episode_id_for("gongjon", "xsapp01", T0)
        assert s["episode_id"].startswith("ep-")
        assert t.episode_for("gongjon", "xsapp01").id == s["episode_id"]


class TestTrackerLifecycle:
    def test_idle_close(self):
        t = _tracker(idle_seconds=900)
        s = t.observe(_cause(), T0)
        assert t.sweep(T0 + 900) == 0
        assert t.sweep(T0 + 901) == 1
        assert len(t) == 0 and t.get(s["episode_id"]) is None

    def test_idle_sweep_runs_on_observe(self):
        t = _tracker(idle_seconds=10)
        t.observe(_cause(), T0)
        t.observe(_cause(alarm_id="X", host_key="other"), T0 + 11)
        assert [ep.host_key for ep in t.episodes()] == ["other"]

    def test_all_resolved_closes(self):
        t = _tracker()
        s = t.observe(_cause(), T0)
        t.observe(_effect(), T0 + 100)
        clear_cpu = _cause(alarm_id="P-9", severity=0)
        assert t.resolve(clear_cpu, T0 + 200) == s["episode_id"]
        assert len(t) == 1  # 증상 미해소 — 사건 유지
        clear_q = _effect(alarm_id="J-9", severity=0)
        assert t.resolve(clear_q, T0 + 300) == s["episode_id"]
        assert len(t) == 0

    def test_resolved_cause_does_not_demote(self):
        t = _tracker()
        s = t.observe(_cause(), T0)
        t.record_tier(s["episode_id"], "P-1", TIER_PAGE)
        t.observe(_cause(alarm_id="P-x", kind="memory", fingerprint="fp-mem"), T0 + 1)
        t.resolve(_cause(alarm_id="P-9", severity=0), T0 + 50)
        s2 = t.observe(_effect(), T0 + 100)
        assert s2["action"] == ACTION_LINK
        assert MISSING_CAUSE_RESOLVED in s2["missing"]

    def test_resolve_unknown_is_noop(self):
        t = _tracker()
        assert t.resolve(_cause(severity=0), T0) is None
        t.observe(_cause(), T0)
        assert t.resolve(_cause(severity=0, fingerprint="other"), T0 + 1) is None

    def test_episode_cap_evicts_oldest_and_reports(self):
        evicted: list[tuple[str, int]] = []
        t = _tracker(max_episodes=2, on_evict=lambda what, n: evicted.append((what, n)))
        t.observe(_cause(host_key="h1"), T0)
        t.observe(_cause(host_key="h2"), T0 + 1)
        t.observe(_cause(host_key="h3"), T0 + 2)
        assert [ep.host_key for ep in t.episodes()] == ["h2", "h3"]
        assert evicted == [("episodes", 1)]

    def test_member_cap(self):
        evicted: list[tuple[str, int]] = []
        t = _tracker(max_members=2, on_evict=lambda what, n: evicted.append((what, n)))
        for i in range(3):
            t.observe(_cause(alarm_id=f"P-{i}"), T0 + i)
        ep = t.episode_for("gongjon", "xsapp01")
        assert [m.alarm.alarm_id for m in ep.members] == ["P-1", "P-2"]
        assert evicted == [("members", 1)]

    def test_representative_is_first_until_a_cause_is_used(self):
        t = _tracker()
        s = t.observe(_effect(occurred_at=T0), T0)
        assert t.get(s["episode_id"]).representative_id == "J-1"
        t.record_tier(s["episode_id"], "J-1", TIER_PAGE)
        t.observe(_cause(occurred_at=T0 - 10), T0 + 5)
        assert t.get(s["episode_id"]).representative_id == "P-1"
        assert t.get(s["episode_id"]).to_dict()["members"][0]["alarm_id"] == "J-1"


# ═════════════════════════════════════════════════════════════════════════════
# C. 규칙 표 적재
# ═════════════════════════════════════════════════════════════════════════════


def _table(**rule_over) -> dict:
    rule = {
        "id": "r1",
        "cause": {"source": "polestar", "kinds": ["cpu"]},
        "effect": {"source": "jennifer", "was_kinds": ["was_service_queuing"]},
        "equal": ["zone", "host_key"],
        "window": {"cause_before_seconds": 600, "cause_after_seconds": 60},
        "action": "demote_effect",
    }
    rule.update(rule_over)
    return {"version": 1, "rules": [rule]}


class TestRuleLoader:
    def test_shipped_table(self):
        rules = load_rules(SHIPPED_RULES)
        assert rules is not None
        by_id = {r.id: r for r in rules}
        # Q-5 ① 비활성 행은 적재되지 않는다
        assert set(by_id) == {"host_resource_to_was_latency", "was_oom_to_process_down"}
        fwd = by_id["host_resource_to_was_latency"]
        assert fwd.action == RULE_ACTION_DEMOTE and fwd.enforce is False
        assert fwd.cause.kinds == {"cpu", "memory"}
        assert fwd.cause_before_seconds == 600 and fwd.cause_after_seconds == 60
        assert by_id["was_oom_to_process_down"].action == RULE_ACTION_LINK
        # G-7 초기 행은 전부 enforce false
        assert all(r.enforce is False for r in rules)

    def test_relative_path_resolves_from_repo_root(self):
        assert load_rules("config/cross_source_rules.yaml") is not None

    def test_reverse_demote_is_forced_to_link_with_warning(self, caplog):
        data = _table(
            cause={"source": "jennifer", "was_kinds": ["was_heap_pressure"]},
            effect={"source": "polestar", "kinds": ["process"]},
        )
        with caplog.at_level(logging.WARNING):
            rules = parse_rules(data)
        assert rules[0].action == RULE_ACTION_LINK
        assert any("G-3" in r.getMessage() for r in caplog.records)

    def test_disabled_row_is_skipped(self):
        assert parse_rules(_table(enabled=False)) == ()

    def test_enforce_column(self):
        assert parse_rules(_table(enforce=True))[0].enforce is True

    def test_empty_table(self):
        assert parse_rules({"version": 1, "rules": []}) == ()

    @pytest.mark.parametrize(
        "over",
        [
            {"cause": {"source": "polestar", "kinds": ["cpux"]}},
            {"effect": {"source": "jennifer", "was_kinds": ["service_queuing"]}},
            {"cause": {"source": "polestar"}},
            {"cause": {"source": "polestar", "was_kinds": ["was_gc_stall"]}},
            {"effect": {"source": "polestar", "kinds": ["cpu"]}},
            {"equal": ["host_key"]},
            {"action": "suppress"},
            {"window": {"cause_before_seconds": -1, "cause_after_seconds": 0}},
            {"unknown": 1},
        ],
    )
    def test_invalid_row_fails_whole_table(self, tmp_path: Path, caplog, over):
        path = tmp_path / "rules.yaml"
        path.write_text(json.dumps(_table(**over)), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            assert load_rules(path) is None
        assert sum("적재 실패" in r.getMessage() for r in caplog.records) == 1

    def test_duplicate_id_fails(self, tmp_path: Path):
        data = _table()
        data["rules"].append(dict(data["rules"][0]))
        path = tmp_path / "rules.yaml"
        path.write_text(json.dumps(data), encoding="utf-8")
        assert load_rules(path) is None

    @pytest.mark.parametrize("body", [None, "rules: [\n  - id: x\n    cause: {"])
    def test_missing_or_broken_file_is_none(self, tmp_path: Path, body):
        path = tmp_path / "rules.yaml"
        if body is not None:
            path.write_text(body, encoding="utf-8")
        assert load_rules(path) is None


# ═════════════════════════════════════════════════════════════════════════════
# D. 정책 step 7.6
# ═════════════════════════════════════════════════════════════════════════════


def _cs(**over) -> dict:
    base = dict(
        episode_id="ep-1", mode="enforce", host_key="xsapp01", rule_id="r1",
        cause_alarm_id="P-1", lag_seconds=95.0, action=ACTION_DEMOTE, enforce=True, missing=[],
        applied=False,
    )
    base.update(over)
    return base


def _policy(severity=2, ctx=None, cross_source=None, **cfg):
    return decide_notification(
        make_event(severity=severity), None, None, ctx if ctx is not None else make_ctx(),
        make_config(importance_value_map=IMP_MAP, **cfg), cross_source=cross_source,
    )


def _same(a: NotificationDecision, b: NotificationDecision) -> bool:
    return (a.tier, a.reason, a.priority, a.signals, a.fingerprint, a.stage) == (
        b.tier, b.reason, b.priority, b.signals, b.fingerprint, b.stage
    )


class TestPolicyStage:
    def test_none_is_bit_identical(self):
        a = decide_notification(make_event(severity=2), None, None, make_ctx(),
                                make_config(importance_value_map=IMP_MAP))
        b = _policy(cross_source=None)
        assert _same(a, b) and a.evidence == b.evidence
        assert "cross_source" not in b.evidence and "episode_id" not in b.evidence

    @pytest.mark.parametrize(
        "cs",
        [
            _cs(mode="shadow"),
            _cs(mode="annotate"),
            _cs(enforce=False),
            _cs(action=ACTION_LINK, missing=[MISSING_WINDOW]),
            # 신호에 결합 판정이 없음(원인 미도착 — 사건 소속만)
            {"episode_id": "ep-1", "mode": "enforce", "host_key": "xsapp01"},
        ],
    )
    def test_decision_unchanged_but_evidence_recorded(self, cs):
        base = _policy()
        d = _policy(cross_source=cs)
        assert _same(base, d) and d.tier == TIER_PAGE and d.stage == STAGE_MATRIX
        assert d.evidence["episode_id"] == "ep-1"
        assert d.evidence["cross_source"]["applied"] is False
        assert "episode_id" not in d.evidence["cross_source"]
        assert d.evidence["cross_source"]["mode"] == cs["mode"]

    def test_enforce_caps_page_to_dashboard(self):
        d = _policy(cross_source=_cs())
        assert d.tier == TIER_DASHBOARD and d.stage == STAGE_CROSS_SOURCE
        assert d.reason.startswith("크로스소스 사건 상관")
        for token in ("r1", "P-1", "xsapp01", "95.0"):
            assert token in d.reason
        assert stage_from_reason(d.reason) == STAGE_CROSS_SOURCE
        assert d.evidence["cross_source"]["applied"] is True
        assert d.evidence["capped_from"] == TIER_PAGE
        assert d.evidence["episode_id"] == "ep-1"

    def test_enforce_caps_ticket(self):
        d = _policy(ctx=make_ctx(importance_id="MID"), cross_source=_cs())
        assert d.tier == TIER_DASHBOARD and d.evidence["capped_from"] == TIER_TICKET

    def test_no_raise_when_matrix_is_suppress(self):
        ctx = make_ctx(importance_id="LOW", noti_policy="suppress")
        base = _policy(severity=1, ctx=ctx)
        d = _policy(severity=1, ctx=ctx, cross_source=_cs())
        assert base.tier == TIER_SUPPRESS
        assert d.tier == TIER_SUPPRESS and d.stage == STAGE_MATRIX
        assert d.evidence["cross_source"]["applied"] is False

    def test_no_change_when_matrix_is_dashboard(self):
        ctx = make_ctx(importance_id="MID")
        d = _policy(severity=1, ctx=ctx, cross_source=_cs())
        assert d.tier == TIER_DASHBOARD and d.stage == STAGE_MATRIX

    def test_severity3_unchanged(self):
        d = _policy(severity=3, cross_source=_cs())
        assert d.tier == TIER_PAGE and d.stage == STAGE_SEVERITY3
        assert d.evidence["cross_source"]["applied"] is False

    def test_earlier_suppress_wins(self):
        d = _policy(ctx=make_ctx(maintenance=True), cross_source=_cs())
        assert d.tier == TIER_SUPPRESS and d.stage == STAGE_MAINTENANCE

    def test_collection_failed_jennifer_stays_page(self):
        # apm_noise_policy_enabled off — 제니퍼 증상은 step 5에서 끝난다(이 Wave에서 그대로 둔다).
        d = _policy(ctx={"source": "unavailable"}, cross_source=_cs())
        assert d.tier == TIER_PAGE and d.stage == "collection_failed"

    def test_stage_label_and_description(self):
        assert STAGE_LABELS[STAGE_CROSS_SOURCE] == "크로스소스 사건 상관"
        assert STAGE_DESCRIPTIONS[STAGE_CROSS_SOURCE].strip()


# ═════════════════════════════════════════════════════════════════════════════
# E. 워커 종단
# ═════════════════════════════════════════════════════════════════════════════

_POLICY_CFG = SimpleNamespace(
    suppress_max_severity=2, importance_value_map={"2": "보통"}, resolved_to_dashboard=False
)
_POLESTAR_CTX = {"importance_id": "2", "maintenance": False, "noti_policy": None,
                 "source": "fixture"}
_APM_CTX = {"importance_id": "높음", "maintenance": None, "noti_policy": "notify",
            "source": "apm_policy"}


class _Redis:
    async def xack(self, *a, **k):
        return 1

    async def xadd(self, *a, **k):
        return b"1-0"

    async def get(self, key):
        return None

    async def set(self, *a, **k):
        return True


class _PolicyGraph:
    """게이트 노드 대신 정책 순수 함수를 부르고 최종 판정을 반환값에 싣는 가짜 그래프."""

    def __init__(self) -> None:
        self.states: list[dict] = []
        self.decisions: list[NotificationDecision | None] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        ev = state["alarm_event"]
        decision = decide_notification(
            ev, None, None, _APM_CTX if is_apm_source(ev) else _POLESTAR_CTX, _POLICY_CFG,
            self_heal=state["self_heal"], cross_source=state.get("cross_source"),
        )
        self.decisions.append(decision)
        return {**state, "notification_decision": decision}


class _Clock:
    def __init__(self, t: float = 2_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


def _polestar_rec(alarm_id: str, *, severity: int = 2, at: str = "20261007090000",
                  host: str = "xsapp01", name: str = "CPU 사용률",
                  db_id: str = "polestar_cm_gp") -> dict:
    return {
        "dbId": db_id, "serverName": host, "hostname": host, "ipAddress": "",
        "resourceAncestry": "", "alarmId": alarm_id, "severity": severity,
        "alarmStatus": "NOT_ACK", "resourceType": "server.Server", "resourceName": "cpu0",
        "alarmName": name, "alarmTime": at, "conditions": ">90", "conditionLog": "95",
    }


def _jennifer_rec(alarm_id: str, *, severity: int = 2, at: str = "20261007090135",
                  host: str = "xsapp01", reason: str = "host_name",
                  kind: str = "was_service_queuing") -> dict:
    return {
        "dbId": "jennifer_common", "source": "jennifer", "serverName": host, "hostname": host,
        "ipAddress": "", "resourceAncestry": "JENNIFER > 주문 > was01", "alarmId": alarm_id,
        "severity": severity, "alarmStatus": "", "resourceType": "apm.Instance",
        "resourceName": f"{host}_was01", "alarmName": "SERVICE_QUEUING", "alarmTime": at,
        "conditions": "", "conditionLog": "",
        "apm": {"source": "jennifer", "source_id": "common", "match_reason": reason,
                "event_type": "SERVICE_QUEUING", "event_type_norm": "SERVICE_QUEUING",
                "event_kind": "error", "was_signals": [{"kind": kind}]},
    }


def _write_rules(tmp_path: Path, *, enforce: bool) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(json.dumps(_table(
        cause={"source": "polestar", "kinds": ["cpu", "memory"]}, enforce=enforce,
    )), encoding="utf-8")
    return path


def _worker(mode: str, rules_path: Path | str = SHIPPED_RULES, *, gate: bool = True):
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=gate, repeat_interval_seconds=14400, suppress_max_severity=2,
            self_heal_window_seconds=300, cross_source_mode=mode,
            cross_source_rules_path=str(rules_path), episode_idle_seconds=900,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    w = AlarmWorker(cfg)
    g = _PolicyGraph()
    w._graph = g
    w._episodes = w._build_episode_tracker()
    return w, g


async def _feed(w: AlarmWorker, rec: dict, n: int = 1) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


class TestWorker:
    async def test_off_has_no_tracker_and_no_state_key(self, clock):
        w, g = _worker("off")
        assert w._episodes is None
        await _feed(w, _polestar_rec("P-1"))
        await _feed(w, _jennifer_rec("J-1"), 2)
        assert all("cross_source" not in s for s in g.states)
        assert g.decisions[1].tier == TIER_PAGE
        assert "cross_source" not in g.decisions[1].evidence

    def test_tracker_needs_gate_and_rules(self, tmp_path: Path, caplog):
        assert _worker("shadow", gate=False)[0]._episodes is None
        with caplog.at_level(logging.WARNING):
            assert _worker("shadow", tmp_path / "nope.yaml")[0]._episodes is None
        assert any("적재 실패" in r.getMessage() for r in caplog.records)
        tracker = _worker("shadow")[0]._episodes
        assert tracker is not None and tracker.mode == "shadow" and tracker.idle_seconds == 900

    async def test_shadow_records_graph_tier_and_keeps_decision(self, clock):
        w, g = _worker("shadow")
        await _feed(w, _polestar_rec("P-1"))
        clock.t += 95
        await _feed(w, _jennifer_rec("J-1"), 2)
        cs = g.states[1]["cross_source"]
        assert cs["action"] == ACTION_DEMOTE and cs["rule_id"] == "host_resource_to_was_latency"
        assert cs["cause_alarm_id"] == "P-1" and cs["lag_seconds"] == 95.0
        assert cs["mode"] == "shadow" and cs["enforce"] is False
        # 그래프 반환값의 최종 티어가 원인 멤버에 기록됐다(조건 ④ 재료)
        ep = w._episodes.get(cs["episode_id"])
        assert ep.member("P-1").tier == TIER_TICKET
        assert ep.member("J-1").tier == TIER_PAGE  # shadow — 판정 불변
        assert g.decisions[1].evidence["cross_source"]["applied"] is False
        assert g.decisions[1].evidence["episode_id"] == cs["episode_id"]

    async def test_enforce_rule_enforce_caps_to_dashboard(self, clock, tmp_path: Path):
        w, g = _worker("enforce", _write_rules(tmp_path, enforce=True))
        await _feed(w, _polestar_rec("P-1"))
        clock.t += 95
        await _feed(w, _jennifer_rec("J-1"), 2)
        d = g.decisions[1]
        assert d.tier == TIER_DASHBOARD and d.stage == STAGE_CROSS_SOURCE
        assert w._episodes.get(d.evidence["episode_id"]).member("J-1").tier == TIER_DASHBOARD

    async def test_enforce_rule_not_enforced_is_unchanged(self, clock, tmp_path: Path):
        w, g = _worker("enforce", _write_rules(tmp_path, enforce=False))
        await _feed(w, _polestar_rec("P-1"))
        await _feed(w, _jennifer_rec("J-1"), 2)
        assert g.decisions[1].tier == TIER_PAGE and g.decisions[1].stage == STAGE_MATRIX

    async def test_effect_first_then_cause_lists_related(self, clock):
        w, g = _worker("shadow")
        await _feed(w, _jennifer_rec("J-1", at="20261007090135"))
        clock.t += 30
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        assert "rule_id" not in g.states[0]["cross_source"]
        assert g.states[1]["cross_source"]["related_effects"] == ["J-1"]
        assert g.decisions[0].tier == TIER_PAGE  # 먼저 온 증상은 그대로

    async def test_weak_match_links_only(self, clock):
        w, g = _worker("shadow")
        await _feed(w, _polestar_rec("P-1"))
        await _feed(w, _jennifer_rec("J-1", reason="regex"), 2)
        cs = g.states[1]["cross_source"]
        assert cs["action"] == ACTION_LINK and cs["missing"] == [MISSING_HOST_KEY_STRENGTH]

    async def test_clear_resolves_member(self, clock):
        w, g = _worker("shadow")
        await _feed(w, _polestar_rec("P-1"))
        clock.t += 10
        await _feed(w, _polestar_rec("P-2", severity=0), 2)
        assert "cross_source" in g.states[1] and g.states[1]["cross_source"] is None
        assert len(w._episodes) == 0  # 소속 전부 해소 → 종료

    async def test_idle_close_by_worker_clock(self, clock):
        w, _ = _worker("shadow")
        await _feed(w, _polestar_rec("P-1"))
        clock.t += 901
        await _feed(w, _polestar_rec("P-2", host="other", name="메모리 사용률"), 2)
        assert [ep.host_key for ep in w._episodes.episodes()] == ["other"]


# ═════════════════════════════════════════════════════════════════════════════
# F. 리플레이 하네스
# ═════════════════════════════════════════════════════════════════════════════


class TestReplay:
    def test_off_summary_shape_unchanged(self):
        rows, skipped = rn.replay(rn.load_jsonl(str(FIXTURE)))
        assert "cross_source" not in rn.summarize(rows, skipped)
        assert all("cross_source" not in r for r in rows)

    def test_shadow_has_would_demote(self):
        lines = rn.load_jsonl(str(FIXTURE))
        base_rows, _ = rn.replay(lines)
        rows, skipped = rn.replay(rn.load_jsonl(str(FIXTURE)), tracker=rn.build_tracker("shadow"))
        summary = rn.summarize(rows, skipped)["cross_source"]
        assert summary["would_demote"] >= 1
        assert summary["applied"] == 0 and summary["episodes"] >= 1
        assert summary["by_rule"].get("host_resource_to_was_latency", 0) >= 1
        # shadow — 판정 불변
        assert [(r["tier"], r["stage"]) for r in rows] == [
            (r["tier"], r["stage"]) for r in base_rows
        ]

    def test_enforce_with_apm_provider_applies(self, tmp_path: Path):
        def provider(event, recorded):
            return _APM_CTX if is_apm_source(event) else rn.current_noise_ctx(event, recorded)

        tracker = rn.build_tracker("enforce", str(_write_rules(tmp_path, enforce=True)))
        rows, _ = rn.replay(rn.load_jsonl(str(FIXTURE)), provider=provider, tracker=tracker)
        summary = rn.summarize(rows)["cross_source"]
        assert summary["applied"] == summary["would_demote"] >= 1

    def test_cli_option(self, tmp_path: Path):
        out = tmp_path / "s.json"
        assert rn.main([str(FIXTURE), "--cross-source-mode", "shadow", "--out", str(out)]) == 0
        assert json.loads(out.read_text(encoding="utf-8"))["cross_source"]["would_demote"] >= 1


# ═════════════════════════════════════════════════════════════════════════════
# G. incident open 페이로드
# ═════════════════════════════════════════════════════════════════════════════


class TestIncidentPayload:
    def _result(self) -> AlarmAnalysisResult:
        return AlarmAnalysisResult(
            alarm_event=make_event(severity=2, alarm_time=datetime(2026, 10, 7, 9, 0)),
            severity_label="경고", summary="s", probable_cause="c", recommended_action="a",
            notification_channels=["workb"],
        )

    def _decision(self, evidence: dict) -> NotificationDecision:
        return NotificationDecision(tier=TIER_PAGE, reason="r", priority=3, signals={},
                                    fingerprint="fp", evidence=evidence)

    def test_episode_id_when_present(self):
        payload = _incident_open_payload(self._result(), self._decision({"episode_id": "ep-7"}))
        assert payload["episode_id"] == "ep-7"

    def test_no_key_without_episode(self):
        assert "episode_id" not in _incident_open_payload(self._result(), self._decision({}))


# ═════════════════════════════════════════════════════════════════════════════
# H. 감사 레코드(decision_store) — 결정 단계와 무관하게 사건 근거가 남는다
# ═════════════════════════════════════════════════════════════════════════════


class TestDecisionStoreRecord:
    async def _record(self, tmp_path: Path, cs: dict | None) -> dict:
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        state = _gate_state(make_event(severity=2), **({"cross_source": cs} if cs else {}))
        await notification_gate_node(state, _run_config(store))
        return _records(store)[0]

    async def test_enforce_record(self, tmp_path: Path):
        rec = await self._record(tmp_path, _cs())
        assert rec["tier"] == TIER_DASHBOARD and rec["stage"] == STAGE_CROSS_SOURCE
        assert rec["stage_evidence"]["episode_id"] == "ep-1"
        assert rec["stage_evidence"]["cross_source"]["applied"] is True
        assert rec["stage_evidence"]["cross_source"]["rule_id"] == "r1"
        assert rec["stage_evidence"]["capped_from"] == TIER_PAGE

    async def test_shadow_record_on_matrix_stage(self, tmp_path: Path):
        rec = await self._record(tmp_path, _cs(mode="shadow"))
        assert rec["stage"] == STAGE_MATRIX
        assert rec["stage_evidence"]["episode_id"] == "ep-1"
        assert rec["stage_evidence"]["cross_source"]["applied"] is False

    async def test_off_record_has_no_cross_source(self, tmp_path: Path):
        rec = await self._record(tmp_path, None)
        assert "cross_source" not in (rec.get("stage_evidence") or {})
