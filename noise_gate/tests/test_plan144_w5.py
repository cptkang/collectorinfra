"""plans/144 W5 — 통보문 사건 묶음 표시 · 사건 피드백 저장(few-shot 비혼입).

    A. episode_lines 순수 함수 — 모드별(annotate·enforce만) · 줄 구성
    B. worKB 본문 — off·shadow·미소속은 비트 동일, annotate·enforce는 「사건 묶음」 블록
    C. notifier 노드 — PAGE 쪽지 · 비PAGE SSE · incident open 페이로드 (없으면 키 없음)
    D. 워커 — episode_summary(annotate·enforce만 · 선언 필수)
    E. 피드백 저장소 — 사건 라벨 기록 · few-shot 후보·유효/노이즈 집계 제외 · 철회
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import (
    _incident_open_payload,
    _tier_sse_payload,
    alarm_notifier_node,
    build_workb_body,
    episode_lines,
)
from noise_gate.domain.notification_policy import (
    STAGE_CROSS_SOURCE,
    STAGE_MATRIX,
    TIER_DASHBOARD,
    TIER_PAGE,
    NotificationDecision,
)
from noise_gate.infrastructure.feedback_store import (
    EPISODE_FEEDBACK_LABELS,
    FeedbackStore,
)
from noise_gate.orchestration.alarm_graph import AlarmState
from noise_gate.tests.test_notifier_tier_routing import make_result
from noise_gate.tests.test_plan144_cross_source import _polestar_rec
from noise_gate.tests.test_plan144_w4 import _feed, _jrec, _w4_worker, clock, sent  # noqa: F401

EP = "ep-gongjon-xsapp01-1"


def _cs(mode: str, **extra) -> dict:
    return {"mode": mode, "host_key": "xsapp01", **extra}


def _decision(tier: str = TIER_PAGE, *, mode: str | None = "annotate", stage: str = STAGE_MATRIX,
              episode_id: str | None = EP, **cs_extra) -> NotificationDecision:
    evidence: dict = {}
    if episode_id:
        evidence["episode_id"] = episode_id
    if mode is not None:
        evidence["cross_source"] = _cs(mode, applied=stage == STAGE_CROSS_SOURCE, **cs_extra)
    return NotificationDecision(
        tier=tier, reason="테스트", priority=300, signals={"severity": 2},
        fingerprint="fp", stage=stage, evidence=evidence,
    )


SUMMARY = {"episode_id": EP, "member_count": 3}


# ═════════════════════════════════════════════════════════════════════════════
# A. episode_lines
# ═════════════════════════════════════════════════════════════════════════════


class TestEpisodeLines:
    @pytest.mark.parametrize("mode", ["off", "shadow"])
    def test_off_and_shadow_make_no_lines(self, mode):
        assert episode_lines(_decision(mode=mode), SUMMARY) == []

    def test_gate_off_or_no_episode_make_no_lines(self):
        assert episode_lines(None, SUMMARY) == []
        assert episode_lines(_decision(episode_id=None), SUMMARY) == []
        assert episode_lines(_decision(mode=None), SUMMARY) == []

    @pytest.mark.parametrize("mode", ["annotate", "enforce"])
    def test_membership_line_with_count(self, mode):
        assert episode_lines(_decision(mode=mode), SUMMARY) == [
            f"사건 {EP} · xsapp01 · 같은 사건 3건"
        ]

    def test_count_only_when_summary_matches_episode(self):
        other = {"episode_id": "ep-other", "member_count": 9}
        assert episode_lines(_decision(), other) == [f"사건 {EP} · xsapp01"]
        assert episode_lines(_decision(), None) == [f"사건 {EP} · xsapp01"]

    def test_applied_demotion_line(self):
        d = _decision(TIER_DASHBOARD, mode="enforce", stage=STAGE_CROSS_SOURCE,
                      cause_alarm_id="P-1", rule_id="cpu_saturation_to_was_queue", action="demote")
        assert episode_lines(d, SUMMARY)[1] == (
            "원인 P-1 (cpu_saturation_to_was_queue) 아래 묶음 — 화면 표시로 내림"
        )

    def test_not_applied_demote_has_no_demotion_line(self):
        # annotate의 「했을 강등」은 applied=False — 내림 문구를 붙이지 않는다.
        d = _decision(mode="annotate", cause_alarm_id="P-1", rule_id="r", action="demote")
        assert len(episode_lines(d, SUMMARY)) == 1

    def test_related_effects_line(self):
        d = _decision(mode="enforce", related_effects=["J-1", "J-2"])
        assert episode_lines(d, SUMMARY)[-1] == "연관 제니퍼 이벤트 2건(이미 통보됨)"


# ═════════════════════════════════════════════════════════════════════════════
# B. worKB 본문
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkbBody:
    def test_none_and_empty_are_bit_identical(self):
        base = build_workb_body(make_result())
        assert build_workb_body(make_result(), episode_lines=None) == base
        assert build_workb_body(make_result(), episode_lines=[]) == base

    def test_block_appended_and_escaped(self):
        lines = ["사건 a<b>", "연관 제니퍼 이벤트 1건(이미 통보됨)"]
        body = build_workb_body(make_result(), episode_lines=lines)
        assert "<b>사건 묶음</b><br>사건 a&lt;b&gt;<br>연관 제니퍼 이벤트 1건(이미 통보됨)" in body


# ═════════════════════════════════════════════════════════════════════════════
# C. notifier 노드
# ═════════════════════════════════════════════════════════════════════════════


def _capture_workb(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def fake_workb(cfg, result, snap=None, **kwargs):
        calls.append(kwargs)

    async def fake_webhook(cfg, result, snap=None):
        pass

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)
    return calls


def _config(**configurable) -> dict:
    app = SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace())
    return {"configurable": {"app_config": app, **configurable}}


class _Bus:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def publish(self, payload: dict) -> None:
        self.payloads.append(payload)


class TestNotifierNode:
    @pytest.mark.parametrize("mode", ["off", "shadow"])
    async def test_page_off_shadow_pass_no_episode_kwarg(self, monkeypatch, mode):
        calls = _capture_workb(monkeypatch)
        state = {"analysis_result": make_result(), "notification_decision": _decision(mode=mode),
                 "episode_summary": SUMMARY}
        await alarm_notifier_node(state, _config())
        assert calls and "episode_lines" not in calls[0]

    @pytest.mark.parametrize("mode", ["annotate", "enforce"])
    async def test_page_annotate_enforce_pass_lines(self, monkeypatch, mode):
        calls = _capture_workb(monkeypatch)
        state = {"analysis_result": make_result(),
                 "notification_decision": _decision(mode=mode, related_effects=["J-1"]),
                 "episode_summary": SUMMARY}
        await alarm_notifier_node(state, _config())
        assert calls[0]["episode_lines"] == [
            f"사건 {EP} · xsapp01 · 같은 사건 3건", "연관 제니퍼 이벤트 1건(이미 통보됨)",
        ]

    async def test_page_incident_open_carries_lines(self, monkeypatch):
        _capture_workb(monkeypatch)
        bus = _Bus()
        state = {"analysis_result": make_result(),
                 "notification_decision": _decision(mode="annotate"),
                 "episode_summary": SUMMARY}
        await alarm_notifier_node(state, _config(incident_publisher=bus))
        assert bus.payloads[0]["episode_lines"] == [f"사건 {EP} · xsapp01 · 같은 사건 3건"]

    async def test_dashboard_demoted_sse_carries_lines(self, monkeypatch):
        calls = _capture_workb(monkeypatch)
        bus = _Bus()
        d = _decision(TIER_DASHBOARD, mode="enforce", stage=STAGE_CROSS_SOURCE,
                      cause_alarm_id="P-1", rule_id="r1", action="demote")
        state = {"analysis_result": make_result(), "notification_decision": d,
                 "episode_summary": SUMMARY}
        await alarm_notifier_node(state, _config(alarm_bus=bus))
        assert calls == []  # DASHBOARD는 발송하지 않는다
        assert bus.payloads[0]["episode_lines"][1] == "원인 P-1 (r1) 아래 묶음 — 화면 표시로 내림"

    @pytest.mark.parametrize("mode", ["off", "shadow"])
    async def test_dashboard_off_shadow_payload_bit_identical(self, monkeypatch, mode):
        _capture_workb(monkeypatch)
        bus = _Bus()
        result = make_result()
        d = _decision(TIER_DASHBOARD, mode=mode)
        await alarm_notifier_node(
            {"analysis_result": result, "notification_decision": d, "episode_summary": SUMMARY},
            _config(alarm_bus=bus),
        )
        assert bus.payloads == [_tier_sse_payload(result, d)]
        assert "episode_lines" not in bus.payloads[0]

    def test_payload_builders_without_lines_have_no_key(self):
        result, d = make_result(), _decision(mode="enforce")
        assert "episode_lines" not in _tier_sse_payload(result, d)
        assert "episode_lines" not in _incident_open_payload(result, d)


# ═════════════════════════════════════════════════════════════════════════════
# D. 워커 episode_summary
# ═════════════════════════════════════════════════════════════════════════════


class TestWorkerEpisodeSummary:
    def test_state_declares_episode_summary(self):
        # LangGraph는 TypedDict에 없는 입력 키를 노드에 넘기지 않는다.
        assert "episode_summary" in AlarmState.__annotations__

    @pytest.mark.parametrize("mode", ["annotate", "enforce"])
    async def test_annotate_enforce_carry_summary(self, clock, sent, mode):  # noqa: F811
        w, g = _w4_worker(mode, late=False)
        await _feed(w, _jrec("J-1", at="20261007090000"))
        clock.t += 60
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        first, second = g.states[0]["episode_summary"], g.states[1]["episode_summary"]
        assert first["member_count"] == 1 and second["member_count"] == 2
        episode_id = g.states[1]["cross_source"]["episode_id"]
        assert first["episode_id"] == second["episode_id"] == episode_id

    @pytest.mark.parametrize("mode", ["off", "shadow"])
    async def test_off_shadow_have_no_key(self, clock, mode):  # noqa: F811
        w, g = _w4_worker(mode, late=False)
        await _feed(w, _jrec("J-1", at="20261007090000"))
        await _feed(w, _polestar_rec("P-1", at="20261007090100"), 2)
        assert all("episode_summary" not in s for s in g.states)


# ═════════════════════════════════════════════════════════════════════════════
# E. 피드백 저장소 — 사건 라벨
# ═════════════════════════════════════════════════════════════════════════════

TS = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


def _store(tmp_path) -> FeedbackStore:
    return FeedbackStore(str(tmp_path / "fb.jsonl"))


class TestEpisodeFeedbackStore:
    def test_labels(self):
        assert EPISODE_FEEDBACK_LABELS == ("episode_split", "demotion_needed")

    def test_record_shape(self, tmp_path):
        store = _store(tmp_path)
        assert store.record_episode_feedback(
            label="demotion_needed", episode_id=EP, alarm_id="J-1", alarm_name="ERROR_X",
            db_id="jennifer_common", tier="dashboard", stage="cross_source", applied=True,
            labeled_by="op1", ts=TS,
        )
        rec = json.loads(store.path.read_text(encoding="utf-8").strip())
        assert rec["label"] == "demotion_needed" and rec["episode_id"] == EP
        assert rec["alarm_id"] == "J-1" and rec["applied"] is True and rec["labeled_by"] == "op1"

    @pytest.mark.parametrize("label,episode_id", [("noise", EP), ("valid", EP), ("bogus", EP),
                                                  ("episode_split", "")])
    def test_invalid_not_recorded(self, tmp_path, label, episode_id):
        store = _store(tmp_path)
        assert store.record_episode_feedback(label=label, episode_id=episode_id) is False
        assert not store.path.exists()

    def test_disabled_store_is_noop(self, tmp_path):
        store = FeedbackStore(str(tmp_path / "fb.jsonl"), enabled=False)
        assert store.record_episode_feedback(label="episode_split", episode_id=EP) is False

    def test_excluded_from_few_shot_and_valid_noise_summary(self, tmp_path):
        store = _store(tmp_path)
        store.record_feedback(label="noise", alarm_name="ERROR_X", ts=TS)
        for label in EPISODE_FEEDBACK_LABELS:
            store.record_episode_feedback(label=label, episode_id=EP, alarm_name="ERROR_X")
        similar = store.find_similar(alarm_name="ERROR_X", limit=10)
        assert [r["label"] for r in similar] == ["noise"]
        summary = store.summarize()
        assert summary[0]["noise"] == 1 and summary[0]["valid"] == 0 and len(summary) == 1
        assert store.summarize_episode_feedback() == {"episode_split": 1, "demotion_needed": 1}

    def test_retract_and_zone_filter(self, tmp_path):
        store = _store(tmp_path)
        store.record_episode_feedback(label="episode_split", episode_id=EP, db_id="a", ts=TS)
        store.record_episode_feedback(label="episode_split", episode_id=EP, db_id="b")
        store.record_retract(target_ts=TS.isoformat())
        assert store.summarize_episode_feedback() == {"episode_split": 1, "demotion_needed": 0}
        only_a = store.summarize_episode_feedback(db_id_filter=lambda db: db == "a")
        assert only_a["episode_split"] == 0  # a의 라벨은 철회됐다
