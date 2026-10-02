"""침묵 규칙이 **그래프를 거쳐** 게이트에 닿는지 (plans/112 · D-247 · D-196 ③④ 결함 수정).

D-196은 워커가 활성 규칙을 그래프 입력 `silence_rules`로 싣고 게이트가 매칭하도록 만들었지만,
`AlarmState`에 그 키를 선언하지 않았다. LangGraph는 TypedDict에 없는 입력 키를 노드에 넘기지
않으므로 게이트는 늘 `None`을 받았고, `silence_enabled=true`여도 침묵 단계가 평가되지 않았다.
기존 `test_silence.py`는 `decide_notification`을 직접 불러 이 층을 밟지 않았다.

여기서는 세 가지를 본다:
    1. 도달 — `AlarmState` 그래프에 넣은 `silence_rules`가 게이트 노드까지 온다(선언을 빼면 실패).
    2. 종단 — 워커 `_process` → 그래프 → 게이트 → 저장소에서 규칙이 걸리면 `stage=silence`다.
       심각도3은 규칙이 걸려도 PAGE다(D-035 불가침).
    3. 기본 off — 저장소가 없으면 워커가 빈 목록을 넘겨 판정이 종전과 같다.
실 LLM 0 — enricher·analyzer 자리는 고정값 대역 노드다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langgraph.graph import END, StateGraph

from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.notification_policy import (
    STAGE_MATRIX,
    STAGE_SEVERITY3,
    STAGE_SILENCE,
    TIER_PAGE,
    TIER_SUPPRESS,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.silence_store import SilenceStore
from noise_gate.orchestration.alarm_graph import AlarmState
from noise_gate.tests.test_notification_policy import make_event
from noise_gate.tests.test_plan112_verify import _FakeRedis, _payload, _records, _worker
from noise_gate.tests.test_silence import make_rule
from noise_gate.tests.test_stage_evidence import _gate_state, _run_config


def _future_rule(**kwargs):  # noqa: ANN202 — 게이트는 실제 현재 시각으로 만료를 본다
    now = datetime.now(UTC)
    return make_rule(created_at=now, expires_at=now + timedelta(hours=2), **kwargs)


class TestAlarmStateCarriesSilenceRules:
    """`silence_rules`가 그래프를 거쳐 게이트까지 도달한다(AlarmState 선언 누락 시 실패)."""

    async def test_gate_node_receives_rules_through_graph(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        builder = StateGraph(AlarmState)
        builder.add_node("notification_gate", notification_gate_node)
        builder.set_entry_point("notification_gate")
        builder.add_edge("notification_gate", END)

        rule = _future_rule(id="slc_graph", server_name="cop0-*")
        await builder.compile().ainvoke(
            _gate_state(make_event(severity=2), silence_rules=[rule]),
            config=_run_config(store, silence_enabled=True),
        )
        (rec,) = _records(store)
        assert rec["stage"] == STAGE_SILENCE
        assert rec["tier"] == TIER_SUPPRESS
        assert rec["reason"].startswith("침묵 규칙(slc_graph)")
        assert rec["stage_evidence"]["rule_id"] == "slc_graph"


class TestWorkerSilenceEndToEnd:
    """워커 → 그래프 → 게이트 → 저장소 — 침묵 on 구성에서 의도 동작이 처음 발효된다."""

    @staticmethod
    def _silenced_worker(tmp_path, store: DecisionStore):  # noqa: ANN205
        worker = _worker(store)
        worker._config.noise_gate.silence_enabled = True
        silences = SilenceStore(str(tmp_path / "silences.jsonl"), True)
        now = datetime.now(UTC)
        silences.create(
            db_id="", server_name="srv-*", alarm_name="", resource_name="", max_severity=2,
            reason="월간 배포 점검", created_by="op", expires_at=now + timedelta(hours=2), now=now,
        )
        worker._silence_store = silences
        return worker

    async def test_matching_rule_decides_silence(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        worker = self._silenced_worker(tmp_path, store)
        await worker._process(_FakeRedis(), "s", "g", b"1-1", _payload(2, "A-1", "CPU"), {})

        (rec,) = _records(store)
        assert rec["stage"] == STAGE_SILENCE
        assert rec["tier"] == TIER_SUPPRESS
        assert "월간 배포 점검" in rec["reason"]
        assert rec["stage_evidence"]["matcher_summary"]

    async def test_severity3_is_never_silenced(self, tmp_path):
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        worker = self._silenced_worker(tmp_path, store)
        await worker._process(_FakeRedis(), "s", "g", b"1-1", _payload(3, "A-1", "CPU"), {})

        (rec,) = _records(store)
        assert rec["stage"] == STAGE_SEVERITY3
        assert rec["tier"] == TIER_PAGE

    async def test_silence_off_leaves_decisions_unchanged(self, tmp_path):
        # 기본(off) — 저장소가 없으면 워커가 빈 목록을 넘긴다. 같은 알람이 매트릭스로 간다.
        store = DecisionStore(str(tmp_path / "d.jsonl"))
        worker = _worker(store)
        assert worker._silence_store is None
        await worker._process(_FakeRedis(), "s", "g", b"1-1", _payload(2, "A-1", "CPU"), {})

        (rec,) = _records(store)
        assert rec["stage"] == STAGE_MATRIX
