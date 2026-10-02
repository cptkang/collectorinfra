"""결정 단계 근거 기록 `stage_evidence` 테스트 (plans/112 S6 · G-1 · G-2).

판정은 그대로 두고 "왜"의 구체 근거만 레코드에 더한다. 그래서 세 가지를 겨눈다:
    1. 도메인 — 결정 지점이 이미 아는 근거(비알람 마커·침묵 규칙·의존성 모드·주석 출처·매트릭스
       조정)를 `NotificationDecision.evidence`에 싣는다. 판정 비트 동일은 `test_decision_snapshot`.
    2. 워커 — 탐지 함수 반환형(bool)은 그대로, 근거는 `_last_<단계>_evidence`로 옆에 남기고
       탐지가 True였던 단계만 그래프 입력 `detection_evidence`로 싣는다.
    3. 배선 — `AlarmState`에 선언돼 게이트 노드까지 도달하고, 게이트는 **결정 단계의 근거만**
       기록한다(다른 단계 탐지값 미기록). 빈 근거면 키가 없다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from langgraph.graph import END, StateGraph

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.notification_policy import (
    STAGE_FLAPPING,
    STAGE_INHIBITION,
    STAGE_MATRIX,
    STAGE_SELF_HEAL,
    STAGE_STORM,
    TIER_DASHBOARD,
    TIER_SUPPRESS,
    NotificationDecision,
    _silence_evidence,
    decide_notification,
    is_operational_alarm,
    non_alarm_markers,
)
from noise_gate.domain.silence import SilenceRule
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.orchestration.alarm_graph import AlarmState
from noise_gate.tests.test_notification_policy import IMP_MAP, make_config, make_ctx, make_event

NOW = datetime(2026, 9, 22, 1, 0, 0, tzinfo=UTC)


def _cfg(**kwargs) -> SimpleNamespace:
    return make_config(importance_value_map=IMP_MAP, **kwargs)


def _decide(event, ctx, config, **kw) -> NotificationDecision:
    return decide_notification(event, None, None, ctx, config, now=NOW, **kw)


# ─── 1. 도메인 근거 ──────────────────────────────────────────────────────


class TestDomainEvidence:
    def test_default_evidence_is_empty_and_not_shared(self):
        # 맨 뒤 기본값 필드 — 직접 만든 결정은 빈 dict이고, 인스턴스끼리 공유하지 않는다.
        a = NotificationDecision(tier="page", reason="r", priority=1, signals={})
        b = NotificationDecision(tier="page", reason="r", priority=1, signals={})
        a.evidence["x"] = 1
        assert b.evidence == {}

    def test_non_alarm_records_matched_markers(self):
        event = make_event(severity=1, alarm_name="계정 생성 승인 요청 — 승인 바랍니다")
        d = _decide(event, make_ctx(), _cfg(non_alarm_filter_enabled=True))
        assert d.tier == TIER_SUPPRESS
        assert d.evidence == {"markers": ["승인", "요청", "바랍니다"]}  # 등장 순서·중복 제거

    @pytest.mark.parametrize("alarm_name,condition_log", [
        ("계정 생성 승인 요청", ""),
        ("작업 안내", "공지 사항 문의"),
        ("CPU 사용률 임계 초과", "승인 요청"),   # 알람 마커가 있어 운영 알람
        ("정기 점검", ""),                      # 어떤 마커도 없음
    ])
    def test_markers_agree_with_is_operational_alarm(self, alarm_name, condition_log):
        # 같은 정규식·같은 텍스트 조합 — 비운영으로 판정된 이벤트는 반드시 마커가 뽑힌다.
        event = make_event(severity=1, alarm_name=alarm_name, condition_log=condition_log)
        if not is_operational_alarm(event):
            assert non_alarm_markers(event)

    def test_silence_records_rule_snapshot(self):
        rule = SilenceRule(
            id="slc_1", db_id="", server_name="cop0-*", alarm_name="", resource_name="",
            max_severity=2, reason="월간 배포 점검", created_by="adm",
            created_at=NOW - timedelta(hours=1), expires_at=NOW + timedelta(hours=2),
        )
        d = _decide(make_event(severity=2), make_ctx(), _cfg(), silence_rules=[rule])
        assert d.evidence == {
            "rule_id": "slc_1",
            "matcher_summary": "server_name=cop0-* · sev<=2",
            "expires_at": (NOW + timedelta(hours=2)).isoformat(),
            "created_by": "adm",
        }

    def test_silence_evidence_never_raises_on_duck_typed_rule(self):
        # 근거 추출이 판정을 깨면 안 된다 — 속성이 모자란 규칙도 예외 없이 통과한다.
        assert _silence_evidence(SimpleNamespace(id="x")) == {
            "rule_id": "x", "matcher_summary": "", "expires_at": "", "created_by": "",
        }

    def test_dependency_multi_hop_root_notified(self):
        ctx = make_ctx()
        ctx.update(cascaded=True, root_notified=True, root_resource_name="core-sw-01")
        d = _decide(make_event(severity=2), ctx, _cfg(dependency_suppression=True))
        assert d.tier == TIER_SUPPRESS
        assert d.evidence == {
            "mode": "multi_hop", "root_notified": True, "root_resource_name": "core-sw-01",
        }

    def test_dependency_multi_hop_root_not_notified_without_name(self):
        ctx = make_ctx()
        ctx.update(cascaded=True, root_notified=False)
        d = _decide(make_event(severity=2), ctx, _cfg(dependency_suppression=True))
        assert d.tier == TIER_DASHBOARD
        assert d.evidence == {"mode": "multi_hop", "root_notified": False}

    def test_dependency_one_hop(self):
        ctx = make_ctx()
        ctx["parent_avail_status"] = 2
        d = _decide(make_event(severity=2), ctx, _cfg(dependency_suppression=True))
        assert d.evidence == {"mode": "one_hop"}  # 1홉은 통보 여부를 모른다 — 키를 두지 않는다

    @pytest.mark.parametrize("kw,ctx_extra,expected", [
        ({"annotation": {"planned_work": True, "resolution": True}}, {}, ["resolution"]),
        ({"annotation": {"planned_work": True}, "correlated": True}, {}, ["correlation"]),
        ({"annotation": {"planned_work": True}}, {"change_nearby": True}, ["change_nearby"]),
        (
            {"annotation": {"planned_work": True, "resolution": True}, "correlated": True},
            {"change_nearby": True},
            ["resolution", "correlation", "change_nearby"],
        ),
    ])
    def test_annotation_records_corroboration_sources(self, kw, ctx_extra, expected):
        ctx = make_ctx()
        ctx.update(ctx_extra)
        d = _decide(make_event(severity=2), ctx, _cfg(annotation_planned_suppress=True), **kw)
        assert d.tier == TIER_DASHBOARD
        assert d.evidence == {"corroborated_by": expected}

    def test_matrix_records_base_tier_and_adjustments(self):
        ctx = make_ctx(importance_id="MID", noti_policy="notify")
        analysis = SimpleNamespace(
            pattern_type="", is_routine=True, llm_actionability=None, ai_message_severity=None
        )
        d = decide_notification(make_event(severity=2), None, analysis, ctx, _cfg(), now=NOW)
        assert d.stage == STAGE_MATRIX
        assert d.evidence == {
            "base_tier": "ticket",
            "promote": ["폴스타 통보 정책(notify)"],
            "demote": ["일상 반복 패턴(is_routine)"],
        }

    def test_matrix_without_adjustment(self):
        d = _decide(make_event(severity=1), make_ctx(importance_id="MID"), _cfg())
        assert d.evidence == {"base_tier": "dashboard", "promote": [], "demote": []}

    @pytest.mark.parametrize("event_kw,ctx,cfg_kw,kw", [
        ({"severity": 3}, "ok", {}, {}),                                          # severity3
        ({"severity": 0}, "ok", {}, {}),                                          # resolved
        ({"severity": 0}, "ok", {}, {"self_heal": True}),                         # self_heal
        ({"severity": 2}, None, {}, {}),                                          # 수집 실패
        ({"severity": 2}, "maint", {}, {}),                                       # maintenance
        ({"severity": 2}, "ok", {"inhibition_enabled": True}, {"inhibited": True}),
        ({"severity": 2}, "ok", {"flapping_enabled": True}, {"flapping": True}),
        ({"severity": 2}, "ok", {"storm_grouping_enabled": True}, {"storm": True}),
        ({"severity": 2}, "ok", {"cross_host_correlation_enabled": True}, {"correlated": True}),
    ])
    def test_stages_without_domain_evidence(self, event_kw, ctx, cfg_kw, kw):
        # 도메인이 근거를 모르는 단계는 빈 dict다(워커 탐지 근거·correlation_meta가 따로 있다).
        noise_ctx = {"ok": make_ctx(), "maint": make_ctx(maintenance=True), None: None}[ctx]
        d = _decide(make_event(**event_kw), noise_ctx, _cfg(**cfg_kw), **kw)
        assert d.evidence == {}


# ─── 2. 워커 탐지 근거 ───────────────────────────────────────────────────


def _worker(**gate) -> AlarmWorker:
    base = dict(
        enable_noise_gate=True,
        repeat_interval_seconds=14400,
        suppress_max_severity=2,
        self_heal_window_seconds=300,
        inhibition_window_seconds=300,
        storm_threshold=1,
        storm_window_seconds=300,
        flap_high_threshold=50.0,
        flap_low_threshold=25.0,
    )
    base.update(gate)
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(**base),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    return AlarmWorker(cfg)


def _ev(severity: int, alarm_name: str = "CPU 사용률 임계 초과", server: str = "srv-1"):
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1", server_name=server,
        alarm_name=alarm_name, resource_name="r1", hostname="h1",
    )


class TestWorkerDetectionEvidence:
    def test_inhibition_evidence_and_reset(self):
        w = _worker()
        assert w._detect_inhibition(_ev(2, "상위 알람"), now=1000.0) is False
        assert w._last_inhibition_evidence is None
        assert w._detect_inhibition(_ev(1, "하위 알람"), now=1030.0) is True
        assert w._last_inhibition_evidence == {
            "inhibitor": "상위 알람", "inhibitor_severity": 2,
            "age_seconds": 30.0, "window_seconds": 300,
        }
        # 다음 호출이 False면 직전 근거가 남지 않는다(이전 알람 값 누수 금지).
        assert w._detect_inhibition(_ev(0, "하위 알람"), now=1031.0) is False
        assert w._last_inhibition_evidence is None

    def test_storm_evidence_and_reset(self):
        w = _worker(storm_threshold=2)
        results = [w._detect_storm(_ev(2), now=1000.0) for _ in range(3)]
        assert results == [False, False, True]
        assert w._last_storm_evidence == {
            "window_count": 3, "threshold": 2, "window_seconds": 300,
        }
        assert w._detect_storm(_ev(0), now=1001.0) is False
        assert w._last_storm_evidence is None

    def test_flapping_evidence_and_reset(self):
        w = _worker()
        results = [
            w._detect_flapping("fp1", _ev(sev), now=1000.0 + i)
            for i, sev in enumerate([2, 0, 2, 0, 2, 0])
        ]
        assert results[-1] is True
        ev = w._last_flapping_evidence
        assert ev is not None and ev["high"] == 50.0 and ev["low"] == 25.0
        assert ev["samples"] == 6 and ev["flap_percent"] > 50.0
        # 다른 지문의 첫 상태는 플래핑이 아니다 — 앞 지문의 근거가 새지 않는다.
        assert w._detect_flapping("fp2", _ev(2), now=2000.0) is False
        assert w._last_flapping_evidence is None

    def test_self_heal_evidence_and_reset(self):
        w = _worker()
        assert w._update_firing_registry(_ev(1), "fp1", 1000.0) is False
        assert w._update_firing_registry(_ev(0), "fp1", 1184.25) is True
        assert w._last_self_heal_evidence == {"heal_seconds": 184.25, "fired_severity": 1}
        assert w._update_firing_registry(_ev(0), "fp1", 1200.0) is False  # 1회성 매칭
        assert w._last_self_heal_evidence is None

    def test_detection_return_types_are_unchanged(self):
        # 직접 호출하는 기존 테스트 보호 — 반환형은 여전히 bool이다(튜플로 바뀌지 않았다).
        w = _worker()
        values = [
            w._detect_inhibition(_ev(2), now=1.0),
            w._detect_storm(_ev(2), now=1.0),
            w._detect_flapping("fp", _ev(2), now=1.0),
            w._update_firing_registry(_ev(1), "fp", 1.0),
        ]
        assert all(type(v) is bool for v in values)


class _FakeRedis:
    async def xack(self, *args, **kwargs):
        return 1


class _FakeGraph:
    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _payload(severity: int, alarm_id: str, alarm_name: str) -> dict:
    body = {
        "severity": severity, "alarmId": alarm_id, "serverName": "srv-x",
        "alarmName": alarm_name, "resourceName": "r1", "dbId": "db1",
    }
    return {b"data": json.dumps(body).encode("utf-8")}


class TestWorkerGraphInput:
    async def test_only_detected_stages_are_forwarded(self):
        w = _worker(inhibition_enabled=True, storm_grouping_enabled=True, storm_threshold=1)
        graph = _FakeGraph()
        w._graph = graph
        await w._process(_FakeRedis(), "s", "g", b"1-1", _payload(2, "A-1", "CPU"), {})
        await w._process(_FakeRedis(), "s", "g", b"1-2", _payload(1, "A-2", "디스크"), {})
        await w._process(_FakeRedis(), "s", "g", b"1-3", _payload(0, "A-3", "디스크"), {})

        first, second, third = (s["detection_evidence"] for s in graph.states)
        assert first is None  # 아무 탐지도 True가 아니면 None
        assert set(second) == {STAGE_INHIBITION, STAGE_STORM}
        assert second[STAGE_INHIBITION]["inhibitor"] == "CPU"
        assert second[STAGE_STORM]["window_count"] == 2
        # 해소는 인히비션·스톰 비대상 — 직전 알람의 근거가 따라오지 않고 자가복구만 실린다.
        assert set(third) == {STAGE_SELF_HEAL}
        assert third[STAGE_SELF_HEAL]["fired_severity"] == 1

    async def test_flags_off_forward_nothing(self):
        w = _worker()
        graph = _FakeGraph()
        w._graph = graph
        await w._process(_FakeRedis(), "s", "g", b"1-1", _payload(2, "A-1", "CPU"), {})
        assert graph.states[0]["detection_evidence"] is None


# ─── 3. 게이트 병합 · AlarmState 도달 ─────────────────────────────────────


def _gate_cfg(**kwargs) -> SimpleNamespace:
    base = dict(enable_noise_gate=True, suppress_max_severity=2, importance_value_map=IMP_MAP,
                resolved_to_dashboard=False)
    base.update(kwargs)
    return SimpleNamespace(**base)


def _analysis() -> SimpleNamespace:
    return SimpleNamespace(
        pattern_type="", is_routine=None, llm_actionability=None, ai_message_severity=None
    )


def _gate_state(event, **extra) -> dict:
    state = {
        "alarm_event": event,
        "analysis_result": _analysis(),
        "history_stats": None,
        "noise_context": make_ctx(),
        "error": None,
    }
    state.update(extra)
    return state


def _records(store: DecisionStore) -> list[dict]:
    return [json.loads(ln) for ln in store.path.read_text(encoding="utf-8").splitlines()]


def _run_config(store: DecisionStore, **gate) -> dict:
    return {"configurable": {
        "app_config": SimpleNamespace(noise_gate=_gate_cfg(**gate)), "decision_store": store,
    }}


_BOTH = {
    STAGE_INHIBITION: {"inhibitor": "CPU", "inhibitor_severity": 2,
                       "age_seconds": 3.0, "window_seconds": 300},
    STAGE_STORM: {"window_count": 9, "threshold": 5, "window_seconds": 300},
}


class TestGateMerge:
    async def test_only_deciding_stage_evidence_is_recorded(self, tmp_path):
        # 인히비션이 먼저 결정했다 — 같은 알람에서 탐지된 스톰 창은 이 판단의 근거가 아니다.
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        state = _gate_state(make_event(severity=1), inhibited=True, storm=True,
                            detection_evidence=_BOTH)
        await notification_gate_node(
            state, _run_config(store, inhibition_enabled=True, storm_grouping_enabled=True)
        )
        rec = _records(store)[0]
        assert rec["stage"] == STAGE_INHIBITION
        assert rec["stage_evidence"] == _BOTH[STAGE_INHIBITION]

    async def test_matrix_decision_ignores_detection_of_other_stages(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        state = _gate_state(make_event(severity=1), detection_evidence=_BOTH)
        await notification_gate_node(state, _run_config(store))
        rec = _records(store)[0]
        assert rec["stage"] == STAGE_MATRIX
        assert rec["stage_evidence"] == {"base_tier": "ticket", "promote": [], "demote": []}

    async def test_detection_evidence_fills_worker_only_stage(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        flap = {"flap_percent": 62.5, "high": 50.0, "low": 25.0, "samples": 12}
        state = _gate_state(make_event(severity=2), flapping=True,
                            detection_evidence={STAGE_FLAPPING: flap})
        await notification_gate_node(state, _run_config(store, flapping_enabled=True))
        assert _records(store)[0]["stage_evidence"] == flap

    async def test_empty_evidence_has_no_key_but_identity_fields_are_recorded(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        event = make_event(severity=3, condition_log="x" * 250)
        await notification_gate_node(_gate_state(event), _run_config(store))
        rec = _records(store)[0]
        assert "stage_evidence" not in rec  # 심각도3 단락은 근거가 없다
        assert rec["db_id"] == event.db_id
        assert rec["resource_name"] == event.resource_name
        assert rec["condition_log"] == "x" * 200  # 200자 상한

    async def test_blank_identity_fields_are_not_recorded(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        event = make_event(severity=3, db_id="", resource_name="", condition_log="")
        await notification_gate_node(_gate_state(event), _run_config(store))
        rec = _records(store)[0]
        assert not {"db_id", "resource_name", "condition_log", "stage_evidence"} & set(rec)


class TestAlarmStateCarriesDetectionEvidence:
    """`detection_evidence`가 그래프를 거쳐 게이트까지 도달한다(AlarmState 선언 누락 시 실패)."""

    async def test_gate_node_receives_evidence_through_graph(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        builder = StateGraph(AlarmState)
        builder.add_node("notification_gate", notification_gate_node)
        builder.set_entry_point("notification_gate")
        builder.add_edge("notification_gate", END)
        graph = builder.compile()

        await graph.ainvoke(
            _gate_state(make_event(severity=2), storm=True, detection_evidence=_BOTH),
            config=_run_config(store, storm_grouping_enabled=True),
        )
        rec = _records(store)[0]
        assert rec["stage"] == STAGE_STORM
        assert rec["stage_evidence"] == _BOTH[STAGE_STORM]

    async def test_undeclared_keys_really_are_dropped(self):
        # 위 테스트가 공허하지 않음을 보인다 — AlarmState에 없는 입력 키는 노드에 오지 않는다.
        seen: dict = {}

        async def _probe(state, config=None):  # noqa: ANN001, ANN202
            seen["declared"] = state.get("detection_evidence")
            seen["undeclared"] = state.get("undeclared_probe_key")
            return {}

        builder = StateGraph(AlarmState)
        builder.add_node("probe", _probe)
        builder.set_entry_point("probe")
        builder.add_edge("probe", END)
        await builder.compile().ainvoke(
            {"alarm_event": None, "detection_evidence": {"x": {}}, "undeclared_probe_key": 1}
        )
        assert seen == {"declared": {"x": {}}, "undeclared": None}
