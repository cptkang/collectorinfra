"""plans/112 독립 검증 — 워커 → AlarmState 그래프 → 게이트 노드 → 저장소 연결 (verifier 추가분).

구현 쪽 테스트는 워커 쪽(`_FakeGraph`가 입력만 받음)과 게이트 쪽(상태를 직접 주입)을 따로 본다.
여기서는 **워커 `_process`가 만든 실제 입력 상태**가 `AlarmState` 그래프를 지나 게이트 노드에 닿고,
결정 단계의 근거·식별 필드만 JSONL에 남는지를 한 번에 본다. noise_context는 LLM·DB 없이
고정값을 주는 대역 노드가 채운다(enricher·analyzer 자리). 실 LLM 0.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langgraph.graph import END, StateGraph

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.notification_policy import (
    STAGE_INHIBITION,
    STAGE_MATRIX,
    STAGE_SELF_HEAL,
    STAGE_SEVERITY3,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.orchestration.alarm_graph import AlarmState
from noise_gate.tests.test_notification_policy import IMP_MAP, make_ctx


class _FakeRedis:
    async def xack(self, *args, **kwargs):
        return 1


async def _context_stub(state, config=None):  # noqa: ANN001, ANN202 — enricher·analyzer 자리
    return {
        "noise_context": make_ctx(),
        "analysis_result": SimpleNamespace(
            pattern_type="", is_routine=None, llm_actionability=None, ai_message_severity=None
        ),
    }


def _graph():
    builder = StateGraph(AlarmState)
    builder.add_node("ctx", _context_stub)
    builder.add_node("notification_gate", notification_gate_node)
    builder.set_entry_point("ctx")
    builder.add_edge("ctx", "notification_gate")
    builder.add_edge("notification_gate", END)
    return builder.compile()


def _worker(store: DecisionStore) -> AlarmWorker:
    ng = SimpleNamespace(
        enable_noise_gate=True, repeat_interval_seconds=14400, suppress_max_severity=2,
        self_heal_window_seconds=300, inhibition_window_seconds=300, inhibition_enabled=True,
        storm_grouping_enabled=True, storm_threshold=1, storm_window_seconds=300,
        flap_high_threshold=50.0, flap_low_threshold=25.0, importance_value_map=IMP_MAP,
        resolved_to_dashboard=False,
    )
    alarm = SimpleNamespace(min_severity=1, dedup_ttl_seconds=300)
    cfg = SimpleNamespace(noise_gate=ng, alarm=alarm)
    worker = AlarmWorker(cfg)
    worker._graph = _graph()
    worker._decision_store = store
    return worker


def _payload(severity: int, alarm_id: str, alarm_name: str, condition_log: str = "") -> dict:
    body = {
        "severity": severity, "alarmId": alarm_id, "serverName": "srv-x", "alarmName": alarm_name,
        "resourceName": "r1", "dbId": "db1", "conditionLog": condition_log,
    }
    return {b"data": json.dumps(body).encode("utf-8")}


def _records(store: DecisionStore) -> list[dict]:
    return [
        rec for rec in (json.loads(x) for x in store.path.read_text(encoding="utf-8").splitlines())
        if not rec.get("type")
    ]


async def test_worker_detection_evidence_reaches_the_record(tmp_path):
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    worker = _worker(store)
    await worker._process(_FakeRedis(), "s", "g", b"1-1", _payload(2, "A-1", "CPU"), {})
    # 두 번째: 인히비션·스톰 둘 다 탐지 — 결정은 인히비션(앞 단계)이므로 스톰 창은 기록되지 않는다.
    await worker._process(_FakeRedis(), "s", "g", b"1-2",
                          _payload(1, "A-2", "디스크", condition_log="D" * 260), {})
    # 세 번째: 같은 알람 해소 — 자가복구 소요가 근거로 남는다.
    await worker._process(_FakeRedis(), "s", "g", b"1-3", _payload(0, "A-3", "디스크"), {})

    first, second, third = _records(store)
    assert first["stage"] == STAGE_MATRIX  # 첫 발생은 탐지 없음 → 매트릭스(도메인 근거만)
    assert set(first["stage_evidence"]) == {"base_tier", "promote", "demote"}

    assert second["stage"] == STAGE_INHIBITION
    assert second["stage_evidence"]["inhibitor"] == "CPU"
    assert "window_count" not in second["stage_evidence"]  # 다른 단계(스톰) 탐지값 미기록
    assert second["db_id"] == "db1" and second["resource_name"] == "r1"
    assert second["condition_log"] == "D" * 200

    assert third["stage"] == STAGE_SELF_HEAL
    assert third["stage_evidence"]["fired_severity"] == 1
    assert "condition_log" not in third  # 빈 값이면 키 없음


async def test_severity3_record_has_identity_but_no_evidence(tmp_path):
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    worker = _worker(store)
    await worker._process(_FakeRedis(), "s", "g", b"1-1", _payload(3, "A-1", "CPU", "99%"), {})
    (rec,) = _records(store)
    assert rec["stage"] == STAGE_SEVERITY3
    assert "stage_evidence" not in rec
    assert rec["condition_log"] == "99%"
    assert len(rec["signals"]) == 16  # 동결 스키마 16키 무변경
