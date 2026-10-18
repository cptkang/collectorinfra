"""plans/162 W1 — 마지막 관문 「PAGE 필요성 재판정」(page_review · D-330).

    A. 도메인 `review_page` — 모드(off/shadow/enforce) · 규칙 R1~R3 · 보존 ①~⑤ · 입력 TICKET 이하
    B. `PageRegistry` — 해소 닫힘 · reopen 유예 · sweep · 키 상한 · shadow 「했을 결과」 갱신
    C. 재기동 복원 — 판정 JSONL 꼬리 · 실패 시 빈 상태 + 경고
    D. 게이트 노드 배선 — 신호 없으면 미호출 · enforce 기록(원 단계 탐지 근거 유지)
    E. 워커 배선 — off면 state 키 없음 · shadow면 신호·레지스트리 갱신
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from src.config import NoiseGateConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes import notification_gate as gate_mod
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.notification_policy import (
    STAGE_MATRIX,
    STAGE_PAGE_REVIEW,
    STAGE_RESOLVED,
    STAGE_SELF_HEAL,
    STAGE_SEVERITY3,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
    NotificationDecision,
    _priority,
    decide_notification,
    stage_from_reason,
)
from noise_gate.domain.page_review import (
    EVIDENCE_KEY,
    KEEP_ALWAYS_PAGE,
    KEEP_APP_IMPACT,
    KEEP_REMINDER,
    KEEP_SEVERITY_RISE,
    RULE_FANOUT,
    RULE_OPEN_REPEAT,
    RULE_SAME_HOST,
    PageRegistry,
    fanout_key_of,
    host_key_of,
    review_page,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_notification_policy import IMP_MAP, make_ctx, make_event
from noise_gate.tests.test_plan144_cross_source import _Clock, _polestar_rec, _Redis

T0 = 1_791_330_000.0
DB = "polestar_cm_gp"


def _cfg(mode: str = "enforce", **kw) -> SimpleNamespace:
    base = dict(
        page_review_mode=mode,
        page_review_host_window_minutes=30,
        page_review_fanout_window_minutes=10,
        page_review_fanout_page_max=1,
        page_review_reminder_hours=24,
        page_review_reopen_quiet_minutes=30,
        page_review_always_page_globs=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _page(*, eff: int = 2, fp: str = "fp-a", stage: str = STAGE_MATRIX,
          evidence: dict | None = None, tier: str = TIER_PAGE) -> NotificationDecision:
    return NotificationDecision(
        tier=tier,
        reason="매트릭스(심각도2×중요도높음) → page → 최종 page",
        priority=_priority(tier, eff, "높음"),
        signals={"severity": eff, "effective_severity": eff, "importance": "높음"},
        fingerprint=fp,
        stage=stage,
        evidence=dict(evidence or {"base_tier": "page"}),
    )


def _resolved(fp: str = "fp-a", stage: str = STAGE_RESOLVED) -> NotificationDecision:
    return NotificationDecision(
        tier="suppress", reason="독립 해소 — 매칭 발생 없음, 감사 기록만", priority=0,
        signals={"severity": 0, "effective_severity": 0, "importance": "보통"},
        fingerprint=fp, stage=stage,
    )


def _registry(**kw) -> PageRegistry:
    return PageRegistry(**kw)


def _keys(host: str = "web01", name: str = "CPU 사용률") -> tuple:
    return host_key_of(DB, host), fanout_key_of(DB, name)


def _step(reg: PageRegistry, cfg, decision: NotificationDecision, *, now: float,
          host: str = "web01", name: str = "CPU 사용률",
          alarm_id: str = "") -> NotificationDecision:
    """신호 → 재판정 → 관측을 워커·게이트 순서대로 한 번 돈다."""
    hk, fk = _keys(host, name)
    signals = reg.signals_for(decision.fingerprint, hk, fk, now)
    out = review_page(decision, signals, cfg)
    reg.observe(out, fingerprint=decision.fingerprint, host_key=hk, fanout_key=fk,
                alarm_id=alarm_id or f"A-{int(now)}", now=now)
    return out


def _block(d: NotificationDecision) -> dict:
    return d.evidence[EVIDENCE_KEY]


# ═════════════════════════════════════════════════════════════════════════════
# A. 도메인 review_page
# ═════════════════════════════════════════════════════════════════════════════


class TestModes:
    def test_off_returns_same_object(self):
        reg = _registry()
        _step(reg, _cfg("enforce"), _page(), now=T0)
        d = _page()
        signals = reg.signals_for(d.fingerprint, *_keys(), T0 + 60)
        assert review_page(d, signals, _cfg("off")) is d

    def test_missing_mode_or_signals_returns_same_object(self):
        d = _page()
        assert review_page(d, None, _cfg("enforce")) is d
        assert review_page(d, {}, _cfg("enforce")) is d
        assert review_page(d, {"now": T0}, SimpleNamespace()) is d  # 모드 미지정 = off

    @pytest.mark.parametrize("tier", [TIER_TICKET, TIER_DASHBOARD, "suppress"])
    def test_non_page_input_is_untouched(self, tier):
        reg = _registry()
        _step(reg, _cfg(), _page(), now=T0)
        d = _page(tier=tier)
        signals = reg.signals_for(d.fingerprint, *_keys(), T0 + 60)
        assert review_page(d, signals, _cfg("enforce")) is d

    def test_new_incident_first_page_kept_with_block(self):
        reg = _registry()
        d = _page()
        out = _step(reg, _cfg("enforce"), d, now=T0)
        assert out.tier == TIER_PAGE and out.stage == STAGE_MATRIX and out.reason == d.reason
        assert _block(out) == {"mode": "enforce", "rule": None, "would_demote": False, "keep": None}
        assert d.evidence == {"base_tier": "page"}  # 원본 근거는 건드리지 않는다

    def test_shadow_keeps_verdict_and_adds_block_only(self):
        reg = _registry()
        _step(reg, _cfg("shadow"), _page(), now=T0)
        d = _page()
        out = _step(reg, _cfg("shadow"), d, now=T0 + 600)
        assert (out.tier, out.reason, out.priority, out.stage, out.signals, out.fingerprint) == (
            d.tier, d.reason, d.priority, d.stage, d.signals, d.fingerprint
        )
        block = _block(out)
        assert block["mode"] == "shadow" and block["rule"] == RULE_OPEN_REPEAT
        assert block["would_demote"] is True and block["keep"] is None
        assert "original_stage" not in out.evidence and "original_reason" not in out.evidence


class TestRules:
    def test_r1_open_repeat_demotes_to_ticket(self):
        reg = _registry()
        _step(reg, _cfg(), _page(), now=T0, alarm_id="A-1")
        d = _page()
        out = _step(reg, _cfg(), d, now=T0 + 600, alarm_id="A-2")
        first = datetime.fromtimestamp(T0).strftime("%H:%M")
        assert out.tier == TIER_TICKET and out.stage == STAGE_PAGE_REVIEW
        assert out.reason == f"PAGE 재판정 — 진행 중 사건 반복(최초 PAGE {first})"
        assert out.priority == _priority(TIER_TICKET, 2, "높음")
        assert out.signals is d.signals and out.fingerprint == d.fingerprint  # §8.2 동결
        assert out.evidence["original_stage"] == STAGE_MATRIX
        assert out.evidence["original_reason"] == d.reason
        assert out.evidence["base_tier"] == "page"
        block = _block(out)
        assert block["rule"] == RULE_OPEN_REPEAT and block["would_demote"] is True
        assert block["page_alarm_id"] == "A-1" and block["repeat_count"] == 0
        assert block["first_page_ts"].startswith(datetime.fromtimestamp(T0).strftime("%Y-%m-%dT"))
        # 사유 접두로도 단계가 역추정된다(구 레코드 폴백 경로)
        assert stage_from_reason(out.reason) == STAGE_PAGE_REVIEW

    def test_r1_reopen_within_quiet_is_same_incident(self):
        reg = _registry()
        _step(reg, _cfg(), _page(), now=T0)
        reg.observe(_resolved(), fingerprint="fp-a", host_key=_keys()[0], fanout_key=_keys()[1],
                    alarm_id="R-1", now=T0 + 600)
        out = _step(reg, _cfg(), _page(), now=T0 + 600 + 10 * 60)
        assert out.tier == TIER_TICKET and _block(out)["rule"] == RULE_OPEN_REPEAT
        # 재발로 사건이 다시 열렸다 — 유예가 지난 뒤의 반복도 같은 사건이다
        again = _step(reg, _cfg(), _page(), now=T0 + 600 + 50 * 60)
        assert again.tier == TIER_TICKET

    def test_r1_after_quiet_is_new_incident(self):
        reg = _registry()
        _step(reg, _cfg(), _page(), now=T0)
        reg.observe(_resolved(), fingerprint="fp-a", host_key=_keys()[0], fanout_key=_keys()[1],
                    alarm_id="R-1", now=T0 + 600)
        out = _step(reg, _cfg(), _page(), now=T0 + 600 + 31 * 60)
        assert out.tier == TIER_PAGE and _block(out)["rule"] is None

    def test_r2_same_host_other_incident(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-host"), now=T0, alarm_id="H-1")
        out = _step(reg, _cfg(), _page(fp="fp-b"), now=T0 + 20 * 60, name="메모리 사용률")
        assert out.tier == TIER_TICKET
        assert out.reason == "PAGE 재판정 — 동일 호스트 사건 진행 중"
        block = _block(out)
        assert block["rule"] == RULE_SAME_HOST and block["host_page_alarm_id"] == "H-1"
        assert "host_page_ts" in block

    def test_r2_requires_window_and_open_and_not_higher(self):
        cfg = _cfg()
        # 창 밖
        reg = _registry()
        _step(reg, cfg, _page(fp="fp-host"), now=T0)
        assert _step(reg, cfg, _page(fp="fp-b"), now=T0 + 31 * 60, name="X").tier == TIER_PAGE
        # 해소됨
        reg = _registry()
        _step(reg, cfg, _page(fp="fp-host"), now=T0)
        reg.observe(_resolved("fp-host"), fingerprint="fp-host", host_key=_keys()[0],
                    fanout_key=_keys()[1], alarm_id="R", now=T0 + 60)
        assert _step(reg, cfg, _page(fp="fp-b"), now=T0 + 120, name="X").tier == TIER_PAGE
        # 이번 심각도가 더 높다 — R2 불성립
        reg = _registry()
        _step(reg, cfg, _page(fp="fp-host", eff=2), now=T0)
        out = _step(reg, cfg, _page(fp="fp-b", eff=3, stage=STAGE_SEVERITY3), now=T0 + 60, name="X")
        assert out.tier == TIER_PAGE and _block(out)["rule"] is None

    def test_r2_ignores_empty_host(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-host"), now=T0, host="")
        out = _step(reg, _cfg(), _page(fp="fp-b"), now=T0 + 60, host="", name="X")
        assert out.tier == TIER_PAGE

    def test_r3_fanout_representative_only(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-h1"), now=T0, host="h1", name="디스크", alarm_id="F-1")
        out = _step(reg, _cfg(), _page(fp="fp-h2"), now=T0 + 60, host="h2", name="디스크")
        assert out.tier == TIER_TICKET
        assert out.reason == "PAGE 재판정 — 동시 발생 2대 중 대표 외"
        block = _block(out)
        assert block["rule"] == RULE_FANOUT and block["representative_alarm_id"] == "F-1"
        assert block["fanout_hosts"] == 2
        third = _step(reg, _cfg(), _page(fp="fp-h3"), now=T0 + 120, host="h3", name="디스크")
        assert third.reason == "PAGE 재판정 — 동시 발생 3대 중 대표 외"
        # 창 밖이면 새 사건
        late = _step(reg, _cfg(), _page(fp="fp-h4"), now=T0 + 11 * 60, host="h4", name="디스크")
        assert late.tier == TIER_PAGE

    def test_r3_page_max_and_representative_host(self):
        cfg = _cfg(page_review_fanout_page_max=2)
        reg = _registry()
        _step(reg, cfg, _page(fp="fp-h1"), now=T0, host="h1", name="디스크")
        assert _step(reg, cfg, _page(fp="fp-h2"), now=T0 + 30, host="h2", name="디스크").tier == (
            TIER_PAGE
        )
        assert _step(reg, cfg, _page(fp="fp-h3"), now=T0 + 60, host="h3", name="디스크").tier == (
            TIER_TICKET
        )
        # 대표 호스트 자신의 다른 자원 알람은 R3 대상이 아니다(같은 호스트는 R2가 본다)
        out = review_page(
            _page(fp="fp-h1-b"),
            reg.signals_for("fp-h1-b", *_keys("h1", "디스크"), T0 + 90),
            cfg,
        )
        assert _block(out)["rule"] != RULE_FANOUT

    def test_rules_are_ordered_first_hit_wins(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-a"), now=T0, host="h1", name="디스크")
        _step(reg, _cfg(), _page(fp="fp-x"), now=T0 + 10, host="h1", name="메모리")
        # 같은 지문 반복 — R1·R2 모두 성립하지만 R1이 먼저다
        out = _step(reg, _cfg(), _page(fp="fp-a"), now=T0 + 60, host="h1", name="디스크")
        assert _block(out)["rule"] == RULE_OPEN_REPEAT


class TestKeepRules:
    def test_keep2_severity_rise(self):
        reg = _registry()
        _step(reg, _cfg(), _page(eff=2), now=T0)
        out = _step(reg, _cfg(), _page(eff=3, stage=STAGE_SEVERITY3), now=T0 + 60)
        assert out.tier == TIER_PAGE and out.stage == STAGE_SEVERITY3
        assert _block(out)["rule"] == RULE_OPEN_REPEAT and _block(out)["keep"] == KEEP_SEVERITY_RISE
        # 올라간 심각도가 대표 심각도가 된다 — 같은 심각도 반복은 다시 내린다
        assert _step(reg, _cfg(), _page(eff=3, stage=STAGE_SEVERITY3), now=T0 + 120).tier == (
            TIER_TICKET
        )

    def test_page_alarm_id_stays_first_after_severity_rise(self):
        """심각도 상승 PAGE 뒤에도 page_alarm_id는 최초 PAGE id(first_page_ts와 같은 기준)."""
        reg = _registry()
        _step(reg, _cfg(), _page(eff=2), now=T0, alarm_id="A-1")
        _step(reg, _cfg(), _page(eff=3, stage=STAGE_SEVERITY3), now=T0 + 60, alarm_id="A-2")
        out = _step(reg, _cfg(), _page(eff=3, stage=STAGE_SEVERITY3), now=T0 + 120,
                    alarm_id="A-3")
        block = _block(out)
        assert block["page_alarm_id"] == "A-1" and block["last_page_alarm_id"] == "A-2"
        assert block["first_page_ts"] == datetime.fromtimestamp(T0).astimezone().isoformat(
            timespec="seconds"
        )

    def test_same_host_links_latest_page_and_counts_repeat(self):
        """R2는 그 사건의 최신 PAGE(id·시각 같은 기준)를 가리키고 그 대표의 반복 수를 올린다."""
        reg = _registry()
        _step(reg, _cfg(), _page(eff=2, fp="fp-host"), now=T0, alarm_id="H-1")
        _step(reg, _cfg(), _page(eff=3, fp="fp-host", stage=STAGE_SEVERITY3), now=T0 + 60,
              alarm_id="H-2")
        out = _step(reg, _cfg(), _page(eff=2, fp="fp-other"), now=T0 + 120, alarm_id="O-1")
        assert _block(out)["rule"] == RULE_SAME_HOST
        assert _block(out)["host_page_alarm_id"] == "H-2"
        sig = reg.signals_for("fp-host", *_keys(), T0 + 180)
        assert sig["fingerprint_page"]["alarm_id"] == "H-1"
        assert sig["fingerprint_page"]["repeat_count"] == 1

    def test_keep3_app_impact(self):
        reg = _registry()
        _step(reg, _cfg(), _page(), now=T0)
        out = _step(reg, _cfg(), _page(evidence={"app_impact_fatal_events": 2}), now=T0 + 60)
        assert out.tier == TIER_PAGE and _block(out)["keep"] == KEEP_APP_IMPACT

    def test_keep4_reminder_after_hours(self):
        reg = _registry()
        _step(reg, _cfg(), _page(), now=T0)
        out = _step(reg, _cfg(), _page(), now=T0 + 25 * 3600)
        block = _block(out)
        assert out.tier == TIER_PAGE and block["rule"] is None
        assert block["keep"] == KEEP_REMINDER and block["unresolved_hours"] == 25
        # 리마인더 PAGE가 마지막 대표 PAGE가 된다 — 직후 반복은 다시 내린다
        assert _step(reg, _cfg(), _page(), now=T0 + 25 * 3600 + 60).tier == TIER_TICKET

    def test_keep4_reminder_overrides_other_rule(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-a"), now=T0, name="A")
        _step(reg, _cfg(), _page(fp="fp-x"), now=T0 + 25 * 3600 - 60, name="X")
        out = _step(reg, _cfg(), _page(fp="fp-a"), now=T0 + 25 * 3600, name="A")
        assert _block(out)["rule"] == RULE_SAME_HOST and _block(out)["keep"] == KEEP_REMINDER
        assert out.tier == TIER_PAGE

    def test_keep5_always_page_glob(self):
        cfg = _cfg(page_review_always_page_globs=["*Down*", "디스크*"])
        reg = _registry()
        _step(reg, cfg, _page(), now=T0, name="Ping Down")
        out = _step(reg, cfg, _page(), now=T0 + 60, name="Ping Down")
        assert out.tier == TIER_PAGE and _block(out)["keep"] == KEEP_ALWAYS_PAGE
        other = _registry()
        _step(other, cfg, _page(), now=T0, name="Ping 지연")
        assert _step(other, cfg, _page(), now=T0 + 60, name="Ping 지연").tier == TIER_TICKET

    def test_config_csv_property_feeds_globs(self):
        cfg = NoiseGateConfig(
            _env_file=None, page_review_mode="enforce",
            page_review_always_page_csv=" *Down* , ,디스크*",
        )
        assert cfg.page_review_always_page_globs == ["*Down*", "디스크*"]
        reg = _registry()
        _step(reg, cfg, _page(), now=T0, name="디스크 사용률")
        out = _step(reg, cfg, _page(), now=T0 + 60, name="디스크 사용률")
        assert _block(out)["keep"] == KEEP_ALWAYS_PAGE

    def test_config_defaults_are_off(self, monkeypatch):
        import os

        for key in [k for k in os.environ if k.startswith("NOISE_PAGE_REVIEW_")]:
            monkeypatch.delenv(key)
        cfg = NoiseGateConfig(_env_file=None)
        assert cfg.page_review_mode == "off"
        assert (
            cfg.page_review_host_window_minutes, cfg.page_review_fanout_window_minutes,
            cfg.page_review_fanout_page_max, cfg.page_review_reminder_hours,
            cfg.page_review_reopen_quiet_minutes, cfg.page_review_always_page_csv,
        ) == (30, 10, 1, 24, 30, "")
        d = _page()
        assert review_page(d, {"now": T0, "host": "x"}, cfg) is d


# ═════════════════════════════════════════════════════════════════════════════
# B. 레지스트리
# ═════════════════════════════════════════════════════════════════════════════


class TestRegistry:
    def test_resolution_closes_incident(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-a"), now=T0)
        for stage in (STAGE_RESOLVED, STAGE_SELF_HEAL):
            reg.observe(_resolved("fp-a", stage), fingerprint="fp-a", host_key=_keys()[0],
                        fanout_key=_keys()[1], alarm_id="R", now=T0 + 60)
        sig = reg.signals_for("fp-a", *_keys(), T0 + 120)
        assert sig["fingerprint_page"]["resolved_ts"] == T0 + 60
        assert reg.signals_for("fp-b", *_keys(), T0 + 120)["host_pages"] == []

    def test_sev3_resolution_by_signals_without_resolution_stage(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-a", eff=3, stage=STAGE_SEVERITY3), now=T0)
        clear = dataclasses.replace(_resolved("fp-a"), stage="non_alarm")  # 심각도 0이면 해소
        reg.observe(clear, fingerprint="fp-a", host_key=_keys()[0], fanout_key=_keys()[1],
                    alarm_id="R", now=T0 + 60)
        assert reg.signals_for("fp-a", *_keys(), T0 + 61)["fingerprint_page"]["resolved_ts"] == (
            T0 + 60
        )

    def test_sweep_drops_stale_entries(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-r"), now=T0, host="h1", name="N")
        reg.observe(_resolved("fp-r"), fingerprint="fp-r", host_key=_keys("h1", "N")[0],
                    fanout_key=_keys("h1", "N")[1], alarm_id="R", now=T0 + 60)
        _step(reg, _cfg(), _page(fp="fp-o"), now=T0, host="h2", name="M")
        assert reg.signals_for("fp-z", *_keys("h2", "M"), T0 + 1)["fanout_members"]
        # 해소 뒤 유예(30분) 경과 · 동시발생 창(10분) 경과 · 호스트 창(30분) 경과
        sig = reg.signals_for("fp-z", *_keys("h2", "M"), T0 + 3600)
        assert reg.signals_for("fp-r", *_keys("h1", "N"), T0 + 3600)["fingerprint_page"] is None
        assert sig["fanout_members"] == [] and sig["host_pages"] == []
        assert len(reg) == 1  # 미해소 fp-o는 보존 시간 안이라 남는다
        # 미해소도 보존 시간(2 × max(리마인더, 24h))을 넘기면 정리된다
        reg.signals_for("fp-z", *_keys("h2", "M"), T0 + 49 * 3600)
        assert len(reg) == 0

    def test_key_cap_evicts_oldest_with_warning(self):
        evicted: list[tuple[str, int]] = []
        reg = _registry(max_keys=3, on_evict=lambda what, n: evicted.append((what, n)))
        for i in range(5):
            _step(reg, _cfg(), _page(fp=f"fp-{i}"), now=T0 + i, host=f"h{i}", name=f"N{i}")
        assert len(reg) == 3
        assert reg.signals_for("fp-0", *_keys("h0", "N0"), T0 + 10)["fingerprint_page"] is None
        assert reg.signals_for("fp-4", *_keys("h4", "N4"), T0 + 10)["fingerprint_page"]
        assert ("지문", 1) in evicted and ("동시발생 키", 1) in evicted

    def test_demotion_counts_repeat_on_representative(self):
        reg = _registry()
        _step(reg, _cfg(), _page(fp="fp-a"), now=T0, host="h1", name="디스크", alarm_id="P-1")
        _step(reg, _cfg(), _page(fp="fp-a"), now=T0 + 60, host="h1", name="디스크")
        _step(reg, _cfg(), _page(fp="fp-b"), now=T0 + 70, host="h1", name="메모리")  # R2
        _step(reg, _cfg(), _page(fp="fp-c"), now=T0 + 80, host="h2", name="디스크")  # R3
        sig = reg.signals_for("fp-a", *_keys("h1", "디스크"), T0 + 90)
        assert sig["fingerprint_page"]["repeat_count"] == 3
        assert sig["fingerprint_page"]["alarm_id"] == "P-1"  # 대표는 바뀌지 않는다

    def test_shadow_page_count_equals_enforce(self):
        sequence = [
            ("fp-a", "h1", "디스크", 0), ("fp-a", "h1", "디스크", 60),
            ("fp-b", "h1", "메모리", 120), ("fp-c", "h2", "디스크", 180),
            ("fp-d", "h3", "CPU", 240), ("fp-a", "h1", "디스크", 300),
        ]

        def _run(mode: str) -> list[bool]:
            reg = _registry()
            would_page = []
            for fp, host, name, dt in sequence:
                out = _step(reg, _cfg(mode), _page(fp=fp), now=T0 + dt, host=host, name=name)
                would_page.append(not _block(out)["would_demote"])
                if mode == "shadow":
                    assert out.tier == TIER_PAGE
            return would_page

        assert _run("shadow") == _run("enforce") == [True, False, False, False, True, False]


# ═════════════════════════════════════════════════════════════════════════════
# C. 재기동 복원
# ═════════════════════════════════════════════════════════════════════════════


def _record(store: DecisionStore, decision: NotificationDecision, *, at: float, host: str,
            name: str, alarm_id: str) -> None:
    store.record(
        decision, alarm_id=alarm_id, alarm_name=name, server_name=host,
        ts=datetime.fromtimestamp(at, tz=UTC), db_id=DB,
        stage_evidence=decision.evidence or None,
    )


class TestRestore:
    def test_restore_from_decision_jsonl_tail(self, tmp_path):
        import time as _time

        now = _time.time()
        store = DecisionStore(str(tmp_path / "decisions.jsonl"))
        cfg = _cfg("shadow")
        _record(store, _page(fp="fp-a"), at=now - 3600, host="WEB01.corp", name="디스크",
                alarm_id="P-1")
        shadow = review_page(
            _page(fp="fp-a"),
            {"now": now - 1800, "alarm_name": "디스크", "host": "web01",
             "fingerprint_page": {"first_page_ts": now - 3600, "last_page_ts": now - 3600,
                                  "severity": 2, "alarm_id": "P-1", "resolved_ts": None,
                                  "repeat_count": 0},
             "host_pages": [], "fanout_members": []},
            cfg,
        )
        assert _block(shadow)["would_demote"] is True
        _record(store, shadow, at=now - 1800, host="WEB01.corp", name="디스크", alarm_id="P-2")
        _record(store, _page(fp="fp-b"), at=now - 1700, host="web02", name="메모리",
                alarm_id="P-3")
        _record(store, _resolved("fp-b"), at=now - 1600, host="web02", name="메모리",
                alarm_id="R-1")
        store.record_resolution(fingerprint="fp-b", duration_seconds=10.0)  # type 레코드는 무시
        with store.path.open("a", encoding="utf-8") as fh:
            fh.write("{깨진 줄\n")

        reg = _registry()
        applied = reg.restore(store.recent_decisions(PageRegistry.restore_window_seconds(24)), now)
        assert applied == 4
        sig = reg.signals_for("fp-a", host_key_of(DB, "WEB01.corp"), fanout_key_of(DB, "디스크"),
                              now)
        assert sig["fingerprint_page"]["alarm_id"] == "P-1"
        assert sig["fingerprint_page"]["repeat_count"] == 1  # shadow 「했을 강등」은 반복 수만
        assert reg.signals_for("fp-b", *_keys("web02", "메모리"), now)["fingerprint_page"][
            "resolved_ts"
        ] is not None
        # 복원된 사건의 반복은 재기동 직후에도 PAGE가 아니다(H2)
        assert review_page(_page(fp="fp-a"), sig, _cfg("enforce")).tier == TIER_TICKET

    def test_restore_skips_outside_window(self):
        reg = _registry()
        old = datetime.fromtimestamp(T0 - 60 * 3600, tz=UTC).isoformat()
        rec = {"ts": old, "tier": "page", "fingerprint": "fp-a", "signals": {"severity": 2},
               "db_id": DB, "server_name": "h1", "alarm_name": "N", "alarm_id": "P"}
        bad = {**rec, "ts": "not-a-time"}
        assert reg.restore([rec, bad, "x"], T0) == 0  # type: ignore[list-item]
        assert len(reg) == 0

    def test_worker_restore_failure_starts_empty_with_warning(self, caplog):
        w = AlarmWorker(_worker_cfg("enforce"))
        w._page_registry = w._build_page_registry()

        class _Boom:
            def recent_decisions(self, window_seconds):
                raise OSError("디스크 오류")

        w._decision_store = _Boom()
        with caplog.at_level(logging.WARNING, logger=worker_mod.__name__):
            w._restore_page_registry(T0)
        assert w._page_registry is not None and len(w._page_registry) == 0
        assert any("PAGE 재판정 복원 실패" in r.getMessage() for r in caplog.records)

    def test_restore_window_is_time_not_line_count(self, tmp_path):
        """복원은 max_lines 줄 상한과 무관하다 — 부속 레코드가 줄을 먹어도 창 안 PAGE가 남는다."""
        import time as _time

        now = _time.time()
        store = DecisionStore(str(tmp_path / "d.jsonl"), max_lines=5)
        _record(store, _page(fp="fp-a"), at=now - 600, host="h1", name="N", alarm_id="P-1")
        for i in range(5):
            store.record_recurrence(fingerprint="fp-a", count=i + 1, alarm_id=f"R-{i}")
        records = store.recent_decisions(PageRegistry.restore_window_seconds(24))
        assert [r["alarm_id"] for r in records] == ["P-1"]
        assert store._tail_records(None) == []  # 관제 조회(꼬리 줄 상한)는 기존 그대로

    def test_worker_restore_skips_disabled_store_with_warning(self, tmp_path, caplog):
        w = AlarmWorker(_worker_cfg("enforce"))
        w._page_registry = w._build_page_registry()
        w._decision_store = DecisionStore(str(tmp_path / "d.jsonl"), enabled=False)
        with caplog.at_level(logging.WARNING, logger=worker_mod.__name__):
            w._restore_page_registry(T0)
        assert w._page_registry is not None and len(w._page_registry) == 0
        assert any(
            "PAGE 재판정 복원 생략 — 판정 기록 비활성" in r.getMessage() for r in caplog.records
        )

    def test_worker_restore_reads_store_tail(self, tmp_path):
        import time as _time

        now = _time.time()
        w = AlarmWorker(_worker_cfg("enforce"))
        w._page_registry = w._build_page_registry()
        w._decision_store = DecisionStore(str(tmp_path / "d.jsonl"))
        _record(w._decision_store, _page(fp="fp-a"), at=now - 600, host="h1", name="N",
                alarm_id="P-1")
        w._restore_page_registry(now)
        assert len(w._page_registry) == 1


# ═════════════════════════════════════════════════════════════════════════════
# D. 게이트 노드
# ═════════════════════════════════════════════════════════════════════════════


def _gate_cfg(mode: str = "enforce") -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(
        enable_noise_gate=True, suppress_max_severity=2, importance_value_map=IMP_MAP,
        resolved_to_dashboard=False, **vars(_cfg(mode)),
    ))


def _gate_state(event, **extra) -> dict:
    return {
        "alarm_event": event, "history_stats": None, "analysis_result": SimpleNamespace(error=None),
        "error": None, "noise_context": make_ctx(importance_id="HIGH"), **extra,
    }


def _signals_with_open_page(event, now: float) -> dict:
    reg = _registry()
    first = decide_notification(event, None, None, make_ctx(importance_id="HIGH"),
                                SimpleNamespace(suppress_max_severity=2,
                                                importance_value_map=IMP_MAP,
                                                resolved_to_dashboard=False))
    hk = host_key_of(event.db_id, event.server_name or event.hostname)
    fk = fanout_key_of(event.db_id, event.alarm_name)
    reg.observe(first, fingerprint=first.fingerprint, host_key=hk, fanout_key=fk,
                alarm_id="P-0", now=now - 600)
    return dict(reg.signals_for(first.fingerprint, hk, fk, now))


class TestGateNode:
    async def test_no_signals_does_not_call_review(self, monkeypatch, tmp_path):
        calls: list = []
        monkeypatch.setattr(gate_mod, "review_page", lambda *a: calls.append(a) or a[0])
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        out = await notification_gate_node(
            _gate_state(make_event(severity=2)),
            {"configurable": {"app_config": _gate_cfg("enforce"), "decision_store": store}},
        )
        assert calls == [] and out["notification_decision"].tier == TIER_PAGE
        rec = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
        assert "page_review" not in (rec.get("stage_evidence") or {})

    async def test_mode_off_does_not_call_review(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(gate_mod, "review_page", lambda *a: calls.append(a) or a[0])
        event = make_event(severity=2)
        await notification_gate_node(
            _gate_state(event, page_review_signals=_signals_with_open_page(event, T0)),
            {"configurable": {"app_config": _gate_cfg("off")}},
        )
        assert calls == []

    async def test_enforce_records_demotion_with_original_stage_evidence(self, tmp_path):
        event = make_event(severity=2)
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        out = await notification_gate_node(
            _gate_state(
                event,
                page_review_signals=_signals_with_open_page(event, T0),
                detection_evidence={STAGE_MATRIX: {"probe": 1}, "storm": {"window_count": 9}},
            ),
            {"configurable": {"app_config": _gate_cfg("enforce"), "decision_store": store}},
        )
        d = out["notification_decision"]
        assert d.tier == TIER_TICKET and d.stage == STAGE_PAGE_REVIEW
        rec = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
        assert rec["tier"] == TIER_TICKET and rec["stage"] == STAGE_PAGE_REVIEW
        ev = rec["stage_evidence"]
        assert ev["page_review"]["rule"] == RULE_OPEN_REPEAT
        assert ev["page_review"]["page_alarm_id"] == "P-0"
        assert ev["original_stage"] == STAGE_MATRIX and ev["probe"] == 1  # 원 단계 탐지 근거
        assert "window_count" not in ev
        assert set(rec["signals"]) == set(decide_notification(
            event, None, None, make_ctx(importance_id="HIGH"),
            SimpleNamespace(suppress_max_severity=2, importance_value_map=IMP_MAP,
                            resolved_to_dashboard=False),
        ).signals)

    async def test_shadow_records_block_only(self, tmp_path):
        event = make_event(severity=2)
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        out = await notification_gate_node(
            _gate_state(event, page_review_signals=_signals_with_open_page(event, T0)),
            {"configurable": {"app_config": _gate_cfg("shadow"), "decision_store": store}},
        )
        assert out["notification_decision"].tier == TIER_PAGE
        rec = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
        assert rec["stage"] == STAGE_MATRIX and rec["tier"] == TIER_PAGE
        assert rec["stage_evidence"]["page_review"]["would_demote"] is True


# ═════════════════════════════════════════════════════════════════════════════
# E. 워커
# ═════════════════════════════════════════════════════════════════════════════


def _worker_cfg(mode: str) -> SimpleNamespace:
    return SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True, repeat_interval_seconds=60, suppress_max_severity=2,
            dedup_severity_rise_bypass=False,
            self_heal_window_seconds=300, **vars(_cfg(mode)),
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )


class _ReviewGraph:
    """게이트 노드처럼 정책 판정 뒤 재판정만 하는 가짜 그래프."""

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        decision = decide_notification(
            state["alarm_event"], None, None,
            {"importance_id": "3", "maintenance": False, "noti_policy": None, "source": "fx"},
            SimpleNamespace(suppress_max_severity=2, importance_value_map={"3": "높음"},
                            resolved_to_dashboard=False),
            self_heal=state["self_heal"],
        )
        signals = state.get("page_review_signals")
        if signals:
            decision = review_page(decision, signals, self.cfg.noise_gate)
        return {**state, "notification_decision": decision}


async def _feed(w: AlarmWorker, rec: dict, n: int) -> None:
    fields = {b"data": json.dumps(rec, ensure_ascii=False).encode("utf-8")}
    await w._process(_Redis(), "alarm:raw", "g", f"{n}-1".encode(), fields, {})


@pytest.fixture
def clock(monkeypatch) -> _Clock:
    c = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", c)
    return c


class TestWorker:
    async def test_off_has_no_state_key_and_no_registry(self, clock):
        cfg = _worker_cfg("off")
        w = AlarmWorker(cfg)
        w._graph = g = _ReviewGraph(cfg)
        w._page_registry = w._build_page_registry()
        assert w._page_registry is None
        await _feed(w, _polestar_rec("A-1"), 1)
        assert g.states and "page_review_signals" not in g.states[0]

    async def test_shadow_feeds_signals_and_updates_registry(self, clock):
        cfg = _worker_cfg("shadow")
        w = AlarmWorker(cfg)
        w._graph = g = _ReviewGraph(cfg)
        w._page_registry = w._build_page_registry()
        await _feed(w, _polestar_rec("A-1", severity=2), 1)
        assert g.states[0]["page_review_signals"]["fingerprint_page"] is None
        clock.t += 120  # 지문 dedup 간격(60초) 뒤 재통보
        await _feed(w, _polestar_rec("A-2", severity=2), 2)
        fp_page = g.states[1]["page_review_signals"]["fingerprint_page"]
        assert fp_page["alarm_id"] == "A-1"
        # 해소는 신호 없이 그래프로 가고, 그래프 뒤 사건을 닫는다
        clock.t += 60
        await _feed(w, _polestar_rec("A-3", severity=0), 3)
        assert "page_review_signals" not in g.states[2]
        clock.t += 60
        await _feed(w, _polestar_rec("A-4", severity=2), 4)
        assert g.states[3]["page_review_signals"]["fingerprint_page"]["resolved_ts"] is not None
