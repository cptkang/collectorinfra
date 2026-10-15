"""plans/162 W5 · D-336 독립 검증 — 구현자 테스트(test_apm_sev3_policy.py)의 빈 곳 보강.

- AI 상향으로 실효 심각도 3이 된 제니퍼 sev2도 정책 표를 본다(D-336 ① 「실효 심각도 3」)
- source=apm_policy인데 apm_policy 근거가 None이면 policy_row None으로 TICKET(예외 없음)
- 크로스소스 enforce 강등 신호가 있어도 sev3 TICKET은 그대로(단계 severity3 · applied False)
- 해소 이벤트(심각도 0)는 플래그와 무관
- page_review(D-330)는 TICKET을 건드리지 않는다
- 게이트 노드 종단: 워커가 실제로 만든 apm_policy + 실제 정책 표 fetch → TICKET · 감사 근거 키
- 화면(node): TICKET sev3 · 폴스타 sev3 · 옛 레코드에서 사유 요약·처리 안내가 깨지지 않는다
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from noise_gate.domain.notification_policy import (
    EVIDENCE_APM_SEV3_POLICY,
    NOISE_SOURCE_APM_POLICY,
    STAGE_SEVERITY3,
    TIER_PAGE,
    TIER_TICKET,
    decide_notification,
)
from noise_gate.domain.page_review import review_page
from noise_gate.tests.test_notification_policy import IMP_MAP, make_config, make_ctx, make_event
from noise_gate.tests.test_plan144_apm_policy import (
    _apm_event,
    _apm_policy,
    _feed,
    _provider,
    _record,
    _worker,
)

_LEGACY_REASON = "심각도3 — 항상 통보(억제 단계 미경유)"


def _cfg(**kw):
    return make_config(importance_value_map=IMP_MAP, **kw)


def _apm_ctx(noti):
    return make_ctx(importance_id="보통", noti_policy=noti, source=NOISE_SOURCE_APM_POLICY)


# ─── AI 상향 ────────────────────────────────────────────────────────────────


class TestAiBoostToSev3:
    @pytest.mark.parametrize(
        ("noti", "on_tier"), [(None, TIER_TICKET), ("notify", TIER_PAGE)]
    )
    def test_boosted_sev2_follows_policy_when_on(self, noti, on_tier):
        analysis = SimpleNamespace(ai_message_severity=3)
        event = make_event(severity=2)
        on = decide_notification(
            event, None, analysis, _apm_ctx(noti),
            _cfg(enable_ai_severity_boost=True, apm_sev3_policy_enabled=True),
        )
        off = decide_notification(
            event, None, analysis, _apm_ctx(noti), _cfg(enable_ai_severity_boost=True)
        )
        assert on.signals["effective_severity"] == 3
        assert on.tier == on_tier and on.stage == STAGE_SEVERITY3
        assert off.tier == TIER_PAGE and off.reason == _LEGACY_REASON

    def test_boost_disabled_sev2_is_not_severity3(self):
        analysis = SimpleNamespace(ai_message_severity=3)
        d = decide_notification(
            make_event(severity=2), None, analysis, _apm_ctx(None),
            _cfg(apm_sev3_policy_enabled=True),
        )
        assert d.stage != STAGE_SEVERITY3
        assert EVIDENCE_APM_SEV3_POLICY not in d.evidence


# ─── 근거 None 처리 · 해소 · 크로스소스 ─────────────────────────────────────


def test_policy_source_without_apm_policy_evidence_is_ticket_with_none_row():
    d = decide_notification(
        make_event(severity=3), None, None, _apm_ctx(None),
        _cfg(apm_sev3_policy_enabled=True), apm_policy=None,
    )
    assert d.tier == TIER_TICKET
    assert d.evidence[EVIDENCE_APM_SEV3_POLICY] == {"notify": "ticket", "policy_row": None}
    assert "apm_policy" not in d.evidence


def test_clear_event_unaffected_by_flag():
    ev = make_event(severity=0)
    on = decide_notification(ev, None, None, _apm_ctx(None), _cfg(apm_sev3_policy_enabled=True))
    off = decide_notification(ev, None, None, _apm_ctx(None), _cfg())
    assert (on.tier, on.reason, on.stage, on.evidence) == (
        off.tier, off.reason, off.stage, off.evidence,
    )


@pytest.mark.parametrize("noti", [None, "notify"])
def test_cross_source_enforce_demote_does_not_touch_sev3(noti):
    cs = {
        "mode": "enforce", "action": "demote", "enforce": True, "episode_id": "ep-1",
        "rule_id": "r1", "cause_alarm_id": "P-1", "missing": [],
    }
    d = decide_notification(
        make_event(severity=3), None, None, _apm_ctx(noti),
        _cfg(apm_sev3_policy_enabled=True), cross_source=cs,
    )
    assert d.stage == STAGE_SEVERITY3
    assert d.tier == (TIER_PAGE if noti else TIER_TICKET)
    assert d.evidence["cross_source"]["applied"] is False
    assert d.evidence["episode_id"] == "ep-1"


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_page_review_leaves_sev3_ticket_untouched(mode):
    d = decide_notification(
        make_event(severity=3), None, None, _apm_ctx(None), _cfg(apm_sev3_policy_enabled=True)
    )
    assert d.tier == TIER_TICKET
    out = review_page(d, {"now": 0.0}, SimpleNamespace(page_review_mode=mode))
    assert out is d


# ─── 게이트 노드 종단(워커가 실제로 만든 apm_policy) ─────────────────────────


async def test_gate_node_end_to_end_ticket_and_audit_evidence():
    from noise_gate.application.nodes.notification_gate import notification_gate_node

    w, g = _worker(apm_on=True)
    await _feed(w, _record("FAKE_UNLISTED_EVENT_TYPE", severity=3, alarm_id="s3-1"))
    await _feed(w, _record("ERROR_OUTOFMEMORY", severity=3, alarm_id="s3-2"))
    assert len(g.states) == 2
    provider = _provider()

    recorded: list[dict] = []

    class _Store:
        def record(self, decision, **kw):
            recorded.append({"decision": decision, **kw})

    gate_cfg = _cfg(
        enable_noise_gate=True, apm_sev3_policy_enabled=True, page_review_mode="enforce",
        app_impact_enabled=True,
    )
    cfg = {"configurable": {"app_config": SimpleNamespace(noise_gate=gate_cfg),
                            "decision_store": _Store()}}
    tiers = []
    for state in g.states:
        st = {
            **state,
            "analysis_result": SimpleNamespace(error=None),
            "noise_context": provider.fetch(state["alarm_event"]),
            "page_review_signals": {"now": 0.0},
        }
        out = await notification_gate_node(st, cfg)
        tiers.append(out["notification_decision"].tier)
    assert tiers == [TIER_TICKET, TIER_PAGE]
    ev0 = recorded[0]["stage_evidence"]
    assert ev0[EVIDENCE_APM_SEV3_POLICY] == {"notify": "ticket", "policy_row": "default"}
    assert ev0["apm_policy"]["policy_row"] == "default"
    assert recorded[1]["stage_evidence"][EVIDENCE_APM_SEV3_POLICY] == {
        "notify": "page", "policy_row": "OUTOFMEMORY",
    }
    # 근거 키는 정책 티어와 행 키뿐 — 메시지·호스트 등 원문 값이 새지 않는다.
    assert set(ev0[EVIDENCE_APM_SEV3_POLICY]) == {"notify", "policy_row"}


def test_policy_row_key_comes_from_worker_match():
    provider = _provider()
    ev = _apm_event("FAKE_UNLISTED_EVENT_TYPE", severity=3)
    assert _apm_policy(ev, provider)["policy_row"] == "default"


# ─── 화면(node) — 사유 요약 · 처리 안내 ────────────────────────────────────


def _run_node(cases):
    from tests.test_api.test_plan153_w1_console_explain import _run_node as run

    return run(cases)


def test_console_summary_and_handling_for_sev3_variants():
    out = _run_node({
        "ticket": {"stage": "severity3", "stage_label": "심각도3 단락", "tier": "ticket",
                   "stage_evidence": {EVIDENCE_APM_SEV3_POLICY: {"notify": "ticket",
                                                                 "policy_row": "default"}}},
        "ticket_none_row": {"stage": "severity3", "tier": "ticket",
                            "stage_evidence": {EVIDENCE_APM_SEV3_POLICY: {"notify": "ticket",
                                                                          "policy_row": None}}},
        "polestar": {"stage": "severity3", "tier": "page", "stage_evidence": {}},
        "old": {"stage": "severity3", "tier": "page"},
    })
    for key, row in out.items():
        for text in (row["summary"], row["handling"], row["short"]):
            assert text and not re.search(r"[{}\[\]]|undefined|null", text), (key, text)
    assert "제니퍼 유형 정책 적용 행: default" in out["ticket"]["summary"]
    assert "TICKET" in out["ticket"]["summary"]
    assert out["ticket"]["handling"].startswith("대기열에서 근무 시간 안에 확인하세요")
    assert out["ticket"]["shift"] == 0
    for key in ("ticket_none_row", "polestar", "old"):
        assert "제니퍼" not in out[key]["summary"], key
    assert out["polestar"]["handling"].startswith("즉시 확인 대상입니다")
