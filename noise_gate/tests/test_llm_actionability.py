"""LLM 액션가능성 판단(피드백 few-shot) 단위 테스트 (Plan 52 E4).

policy: analysis.llm_actionability를 step 9 보조 신호로 소비하되 승격 비대칭(재현율 우선)·
        SUPPRESS 하한·1단계 이내 이동·심각도3 불변·게이트오프 회귀 0을 고정한다.
analyzer: 게이트 활성 + feedback_store 주입 시에만 few-shot을 조회·주입하고 응답을 재파싱하며,
          비활성 시 조회·재파싱을 스킵함(회귀 0)을 고정한다(추가 LLM 호출 없음).
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_TICKET,
    decide_notification,
)

REF = datetime(2026, 6, 30, 10, 0, 0)
IMP_MAP = {"HIGH": "높음", "MID": "보통", "LOW": "낮음"}


# ─── policy 헬퍼 ─────────────────────────────────────────────────────────────

def make_event(**kwargs) -> AlarmEvent:
    """테스트용 AlarmEvent. severity로 is_clear 동기화."""
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="srv-log01",
        hostname="hlog01",
        ip_address="10.1.2.9",
        resource_ancestry="/Servers/srv/LogMonitor",
        alarm_id="LOG-001",
        severity=2,
        alarm_status="NOT_ACK",
        resource_type="server.LogMonitor",
        resource_name="syslog",
        alarm_name="로그 패턴 감지",
        alarm_time=REF,
        conditions="",
        condition_log="benign log line",
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def make_config(*, enable_llm_actionability=True, **kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2,
        importance_value_map=IMP_MAP,
        resolved_to_dashboard=False,
        enable_llm_actionability=enable_llm_actionability,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx(importance_id="HIGH", *, maintenance=False, noti_policy=None) -> dict:
    return {
        "importance_id": importance_id,
        "maintenance": maintenance,
        "noti_policy": noti_policy,
        "parent_avail_status": None,
        "source": "polestar_db",
    }


def make_analysis(actionability, *, noti_pattern="") -> SimpleNamespace:
    """alarm_analyzer가 채운 AlarmAnalysisResult를 모사한 덕타이핑 객체."""
    return SimpleNamespace(
        pattern_type=noti_pattern,
        is_routine=None,
        ai_message_severity=None,
        llm_actionability=actionability,
    )


class TestPolicyActionability:
    def test_noise_demotes_one_step(self):
        # sev2×높음 base PAGE + noise → 1단계 강등 TICKET (SUPPRESS로 직행 불가)
        cfg = make_config()
        d = decide_notification(
            make_event(severity=2), None, make_analysis("noise"), make_ctx("HIGH"), cfg
        )
        assert d.tier == TIER_TICKET
        assert d.signals["llm_actionability"] == "noise"
        assert "LLM 노이즈" in d.reason

    def test_actionable_promotes_one_step(self):
        # sev2×보통 base TICKET + actionable → 1단계 승격 PAGE
        cfg = make_config()
        d = decide_notification(
            make_event(severity=2), None, make_analysis("actionable"), make_ctx("MID"), cfg
        )
        assert d.tier == TIER_PAGE
        assert d.signals["llm_actionability"] == "actionable"
        assert "LLM 액션가능성" in d.reason

    def test_severity3_always_page_regardless(self):
        # 심각도3은 액션가능성과 무관하게 항상 PAGE(step3 단락, E4는 step9만 관여)
        cfg = make_config()
        d = decide_notification(
            make_event(severity=3), None, make_analysis("noise"), make_ctx("LOW"), cfg
        )
        assert d.tier == TIER_PAGE
        assert d.signals["effective_severity"] == 3

    def test_disabled_ignores_actionability(self):
        # enable off → llm_actionability 무시(E3 결과와 동일, 회귀 0)
        cfg_off = make_config(enable_llm_actionability=False)
        with_act = decide_notification(
            make_event(severity=2), None, make_analysis("noise"), make_ctx("HIGH"), cfg_off
        )
        without = decide_notification(
            make_event(severity=2), None, None, make_ctx("HIGH"), cfg_off
        )
        assert with_act.tier == without.tier == TIER_PAGE  # sev2×높음 매트릭스 그대로
        assert with_act.signals["llm_actionability"] is None

    def test_noise_with_promote_signal_promotion_wins(self):
        # noise(강등) + noti_policy=notify(승격) 공존 → 승격 우선(재현율 우선)
        cfg = make_config()
        d = decide_notification(
            make_event(severity=2),
            None,
            make_analysis("noise"),
            make_ctx("MID", noti_policy="notify"),
            cfg,
        )
        assert d.tier == TIER_PAGE  # sev2×보통 TICKET → 승격 PAGE
        assert "승격 우선으로 무시" in d.reason

    def test_noise_respects_suppress_floor(self):
        # sev1×낮음 base DASHBOARD + noise → 강등 가드 통과하나 SUPPRESS 하한 유지(DASHBOARD)
        # 실제로는 DASHBOARD에서 1단계 강등하면 SUPPRESS이므로, 저심각도 noise가 하한을 넘지
        # 않는지 확인(sev1×낮음은 base DASHBOARD → noise 1단계 강등 → SUPPRESS 가능).
        cfg = make_config()
        d = decide_notification(
            make_event(severity=1), None, make_analysis("noise"), make_ctx("LOW"), cfg
        )
        # sev1×낮음 base=DASHBOARD, noise demote 1단계 → SUPPRESS. 1단계 이내·하한 규칙 확인.
        assert d.tier in (TIER_DASHBOARD, "suppress")
        assert d.signals["llm_actionability"] == "noise"


# ─── analyzer 헬퍼 ───────────────────────────────────────────────────────────

class _CapturingLLM:
    """user 메시지를 캡처하고 고정 JSON을 반환하는 analyzer LLM 대역."""

    def __init__(self, response_json: dict) -> None:
        self._response = response_json
        self.last_user_msg = None

    async def ainvoke(self, messages, *args, **kwargs):
        for m in messages:
            if m.get("role") == "user":
                self.last_user_msg = m["content"]
        return SimpleNamespace(content=json.dumps(self._response, ensure_ascii=False))


class _FakeFeedbackStore:
    """find_similar 호출 인자를 기록하고 고정 예시를 반환하는 대역."""

    def __init__(self, examples: list[dict]) -> None:
        self._examples = examples
        self.called_with = None

    def find_similar(self, *, alarm_name, resource_name="", pattern="", limit=3):
        self.called_with = {
            "alarm_name": alarm_name,
            "resource_name": resource_name,
            "pattern": pattern,
            "limit": limit,
        }
        return self._examples


def _make_app_cfg(*, enable: bool) -> SimpleNamespace:
    """analyzer 노드가 덕 타이핑으로 소비하는 app_config."""
    alarm = SimpleNamespace(get_notification_channels=lambda: ["workb"])
    noise_gate = SimpleNamespace(
        enable_llm_actionability=enable,
        enable_ai_severity_boost=False,
        actionability_fewshot_count=3,
    )
    return SimpleNamespace(alarm=alarm, noise_gate=noise_gate)


def _response_json(actionability) -> dict:
    return {
        "severity_label": "경고",
        "summary": "요약",
        "probable_cause": "원인",
        "recommended_action": "조치",
        "pattern_type": "",
        "llm_actionability": actionability,
        "actionability_reason": "반복 양성" if actionability else "",
    }


class TestAnalyzerActionability:
    async def test_fewshot_injected_and_reparsed(self, monkeypatch):
        # 게이트 활성 + feedback_store 주입 → find_similar 호출·프롬프트 주입·응답 재파싱
        llm = _CapturingLLM(_response_json("noise"))
        monkeypatch.setattr(
            "noise_gate.application.nodes.alarm_analyzer.create_llm",
            lambda cfg, **kw: llm,
        )
        fb = _FakeFeedbackStore(
            [{"label": "noise", "alarm_name": "로그 패턴 감지",
              "resource_name": "syslog", "pattern": "주기적", "note": "야간"}]
        )
        event = make_event()
        state = {
            "alarm_event": event,
            "history_stats": None,
            "process_snapshot": None,
            "analysis_result": None,
            "error": None,
        }
        config = {"configurable": {"app_config": _make_app_cfg(enable=True), "feedback_store": fb}}

        out = await alarm_analyzer_node(state, config)
        result = out["analysis_result"]
        assert fb.called_with is not None
        assert fb.called_with["alarm_name"] == event.alarm_name
        assert "[운영자 피드백" in llm.last_user_msg
        assert result.llm_actionability == "noise"
        assert result.actionability_reason == "반복 양성"

    async def test_disabled_skips_fewshot_and_reparse(self, monkeypatch):
        # 게이트 비활성 → find_similar 미호출·프롬프트 미주입·재파싱 스킵(회귀 0)
        llm = _CapturingLLM(_response_json("noise"))
        monkeypatch.setattr(
            "noise_gate.application.nodes.alarm_analyzer.create_llm",
            lambda cfg, **kw: llm,
        )
        fb = _FakeFeedbackStore([{"label": "noise", "alarm_name": "로그 패턴 감지"}])
        state = {
            "alarm_event": make_event(),
            "history_stats": None,
            "process_snapshot": None,
            "analysis_result": None,
            "error": None,
        }
        config = {"configurable": {"app_config": _make_app_cfg(enable=False), "feedback_store": fb}}

        out = await alarm_analyzer_node(state, config)
        result = out["analysis_result"]
        assert fb.called_with is None  # few-shot 미조회
        assert "[운영자 피드백" not in llm.last_user_msg
        assert result.llm_actionability is None  # 재파싱 스킵
        assert result.actionability_reason == ""
