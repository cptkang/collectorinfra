"""Plan 52 Phase E2 회귀 가드 — 모든 E2 플래그 off면 E1과 동일(회귀 0).

게이트는 켜져 있되(E1 활성) E2 4기능 플래그가 모두 off일 때:
  A. 정책: inhibited/flapping/storm 인자·parent_avail_status가 채워져 있어도 억제 단계가
     평가되지 않아 결과(tier/reason/priority)가 E1 매트릭스와 동일.
  B. 그래프: build_alarm_graph가 E2 플래그를 읽지 않으므로 E2 전용 노드가 없고 노드집합 무변경.
  C. 워커: E2 플래그가 없거나 off면 _detect_* 가 호출되지 않아 그래프 상태의
     inhibited/flapping/storm 이 모두 False로 유지.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.domain.notification_policy import (
    TIER_PAGE,
    decide_notification,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph


# ─────────────────────────────────────────────────────────────
# A. 정책 — E2 인자가 있어도 플래그 off면 E1 결과와 동일
# ─────────────────────────────────────────────────────────────
def _event(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def _e1_config() -> SimpleNamespace:
    # E1 기본 — 모든 E2 플래그 미지정(getattr 기본 False). 높음 매핑으로 PAGE 유도.
    return SimpleNamespace(
        suppress_max_severity=2,
        importance_value_map={"HIGH": "높음"},
        resolved_to_dashboard=False,
    )


def _ctx(parent=None) -> dict:
    return {
        "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
        "parent_avail_status": parent, "source": "polestar_db",
    }


class TestPolicyE2OffEqualsE1:
    def test_e2_args_ignored_when_flags_off(self):
        cfg = _e1_config()
        ev = _event(2)
        baseline = decide_notification(ev, None, None, _ctx(None), cfg)
        # E2 신호를 전부 켜서 전달해도 플래그 off → 결과 동일
        with_e2 = decide_notification(
            ev, None, None, _ctx(parent=1), cfg,
            self_heal=False, inhibited=True, flapping=True, storm=True,
        )
        assert with_e2.tier == baseline.tier == TIER_PAGE
        assert with_e2.reason == baseline.reason
        assert with_e2.priority == baseline.priority

    def test_parent_abnormal_not_suppressed_without_flag(self):
        # dependency_suppression 플래그 부재 → parent 비정상이어도 매트릭스(PAGE)
        d = decide_notification(_event(2), None, None, _ctx(parent=1), _e1_config())
        assert d.tier == TIER_PAGE


# ─────────────────────────────────────────────────────────────
# B. 그래프 — E2 플래그가 노드집합을 바꾸지 않음
# ─────────────────────────────────────────────────────────────
def _gcfg(**ng) -> SimpleNamespace:
    base = dict(enable_noise_gate=True)
    base.update(ng)
    return SimpleNamespace(
        alarm=SimpleNamespace(history_enabled=True),
        noise_gate=SimpleNamespace(**base),
    )


class TestGraphNodesUnchangedByE2:
    def test_node_set_identical_e2_on_vs_off(self):
        nodes_off = set(build_alarm_graph(_gcfg()).get_graph().nodes.keys())
        nodes_on = set(build_alarm_graph(_gcfg(
            dependency_suppression=True, inhibition_enabled=True,
            flapping_enabled=True, storm_grouping_enabled=True,
        )).get_graph().nodes.keys())
        assert nodes_off == nodes_on
        # E2 전용 노드는 존재하지 않는다(전부 worker/policy 계층)
        assert nodes_on == {
            "__start__", "__end__", "alarm_context_enricher",
            "alarm_analyzer", "notification_gate", "alarm_notifier",
        }


# ─────────────────────────────────────────────────────────────
# C. 워커 — E2 플래그 off면 _detect_* 미호출, 상태 플래그 False 유지
# ─────────────────────────────────────────────────────────────
class _FakeRedis:
    async def xack(self, *args, **kwargs):
        return 1


class _FakeGraph:
    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _worker_e2_off() -> tuple[AlarmWorker, _FakeGraph]:
    # E2 플래그를 명시하지 않음 → 워커 _process의 getattr(..., False)로 detection 스킵
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
        ),
        alarm=SimpleNamespace(min_severity=1, dedup_ttl_seconds=300),
    )
    worker = AlarmWorker(cfg)
    graph = _FakeGraph()
    worker._graph = graph
    return worker, graph


def _payload(severity: int, alarm_id: str, **extra) -> dict:
    base = {"severity": severity, "alarmId": alarm_id}
    base.update(extra)
    return {b"data": json.dumps(base).encode("utf-8")}


class TestWorkerE2FlagsOff:
    async def test_detect_methods_not_invoked_state_flags_false(self):
        worker, graph = _worker_e2_off()
        await worker._process(
            _FakeRedis(), "stream", "group", b"1-1",
            _payload(2, "A-1", serverName="srv-x", alarmName="CPU",
                     resourceName="r1", dbId="db1"),
            {},
        )
        assert len(graph.states) == 1
        st = graph.states[0]
        assert st["inhibited"] is False
        assert st["flapping"] is False
        assert st["storm"] is False
        # E2 detection 상태 dict가 시드되지 않았는지(빈 상태) 확인
        assert worker._active_firings == {}
        assert worker._flap_states == {}
        assert worker._storm_window == {}
