"""워커 min_severity 게이트 역할분리 + 자가복구 상관 통합 테스트 (Plan 52 §4.8 / §3.7).

게이트 활성 시:
    - severity 0(해소)·3 은 절대 드롭되지 않고 그래프까지 도달한다.
    - 1 <= severity < min_severity 만 게이트 이전에 드롭된다(매트릭스 하단 행 도달 보장).
    - 자가복구: 발생(severity ≤ suppress_max) 후 self_heal_window 내 해소 → self_heal=True,
      창 밖/발생 없음 → False.

seam: 워커에는 이미 깔끔한 헬퍼가 존재한다.
    - 자가복구 상관: AlarmWorker._update_firing_registry (동기, 직접 호출)
    - min_severity 분기: AlarmWorker._process 를 fake redis + fake graph 로 구동하여
      "어떤 severity 가 graph.ainvoke 까지 도달/드롭되는지" 단언(억지 리팩터 불필요).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from noise_gate.application.alarm_worker import AlarmWorker


# ─────────────────────────────────────────────────────────────
# Part A: 자가복구 상관 (_update_firing_registry) — 동기 헬퍼 직접 검증
# ─────────────────────────────────────────────────────────────
def _heal_worker() -> AlarmWorker:
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(
        suppress_max_severity=2, self_heal_window_seconds=300))
    return AlarmWorker(cfg)


def _firing(severity: int) -> SimpleNamespace:
    return SimpleNamespace(is_clear=False, severity=severity)


def _clear() -> SimpleNamespace:
    return SimpleNamespace(is_clear=True, severity=0)


class TestSelfHealCorrelation:
    def test_firing_then_clear_within_window_heals(self):
        w = _heal_worker()
        fp = "fp-1"
        assert w._update_firing_registry(_firing(2), fp, now=1000.0) is False  # 발생 기록
        assert w._update_firing_registry(_clear(), fp, now=1100.0) is True     # 100s 후 해소 → 매칭

    def test_clear_outside_window_does_not_heal(self):
        w = _heal_worker()
        fp = "fp-2"
        w._update_firing_registry(_firing(2), fp, now=1000.0)
        # 400s 후 해소 → 창(300s) 밖 → 매칭 아님
        assert w._update_firing_registry(_clear(), fp, now=1400.0) is False

    def test_clear_without_firing_does_not_heal(self):
        w = _heal_worker()
        assert w._update_firing_registry(_clear(), "fp-unknown", now=1000.0) is False

    def test_severity3_firing_not_registered(self):
        # 심각도3 발생은 자가복구 후보에서 제외 → 해소가 와도 매칭 없음(발생 PAGE 보존)
        w = _heal_worker()
        fp = "fp-3"
        w._update_firing_registry(_firing(3), fp, now=1000.0)
        assert w._update_firing_registry(_clear(), fp, now=1050.0) is False

    def test_severity_above_suppress_max_not_registered(self):
        # suppress_max=2 이므로 severity>2 발생은 자가복구 등록 안 됨
        w = _heal_worker()
        fp = "fp-4"
        w._update_firing_registry(_firing(3), fp, now=1000.0)
        assert fp not in w._firing_registry


# ─────────────────────────────────────────────────────────────
# Part B: min_severity 게이트 분기 — _process 를 fake redis/graph 로 구동
# ─────────────────────────────────────────────────────────────
class _FakeRedis:
    async def xack(self, *args, **kwargs):
        return 1


class _FakeGraph:
    """ainvoke 로 전달된 state 를 캡처한다(어떤 severity 가 그래프에 도달했는지)."""

    def __init__(self) -> None:
        self.states: list[dict] = []

    async def ainvoke(self, state, config=None):
        self.states.append(state)
        return state


def _gate_worker(min_severity: int) -> tuple[AlarmWorker, _FakeGraph]:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            enable_noise_gate=True,
            repeat_interval_seconds=14400,
            suppress_max_severity=2,
            self_heal_window_seconds=300,
        ),
        alarm=SimpleNamespace(min_severity=min_severity, dedup_ttl_seconds=300),
    )
    worker = AlarmWorker(cfg)
    graph = _FakeGraph()
    worker._graph = graph
    return worker, graph


def _payload(severity: int, alarm_id: str, **extra) -> dict:
    base = {"severity": severity, "alarmId": alarm_id}
    base.update(extra)
    return {b"data": json.dumps(base).encode("utf-8")}


async def _process(worker: AlarmWorker, fields: dict) -> None:
    await worker._process(_FakeRedis(), "stream", "group", b"1-1", fields, {})


class TestMinSeverityGateDrop:
    async def test_severity_below_min_is_dropped(self):
        # min=2, sev=1 → 1 <= 1 < 2 → 드롭(그래프 미도달)
        worker, graph = _gate_worker(min_severity=2)
        await _process(worker, _payload(1, "A-1"))
        assert graph.states == []

    async def test_severity_zero_reaches_graph(self):
        # min=2, sev=0(해소) → 절대 드롭 금지(자가복구 상관 위해 전달)
        worker, graph = _gate_worker(min_severity=2)
        await _process(worker, _payload(0, "A-2"))
        assert len(graph.states) == 1
        assert graph.states[0]["alarm_event"].severity == 0

    async def test_severity_three_reaches_graph(self):
        # min=2, sev=3 → 절대 드롭 금지
        worker, graph = _gate_worker(min_severity=2)
        await _process(worker, _payload(3, "A-3"))
        assert len(graph.states) == 1
        assert graph.states[0]["alarm_event"].severity == 3

    async def test_lowering_min_to_1_lets_severity1_reach_gate(self):
        # min=1 → 1 <= 1 < 1 (거짓) → sev1 도달(매트릭스 주의 행 활성, §4.8 ⓑ)
        worker, graph = _gate_worker(min_severity=1)
        await _process(worker, _payload(1, "A-4"))
        assert len(graph.states) == 1
        assert graph.states[0]["alarm_event"].severity == 1


class TestSelfHealHandoffToGraph:
    async def test_firing_then_clear_sets_self_heal_on_graph_state(self):
        # 발생(sev2) → 즉시 해소(sev0, 동일 핑거프린트) → 두번째 invoke state.self_heal=True
        worker, graph = _gate_worker(min_severity=2)
        common = dict(serverName="srv-x", alarmName="CPU", resourceName="r1", dbId="db1")
        await _process(worker, _payload(2, "F-1", **common))   # 발생
        await _process(worker, _payload(0, "C-1", **common))   # 해소(같은 핑거프린트)
        assert len(graph.states) == 2
        assert graph.states[0]["self_heal"] is False
        assert graph.states[1]["self_heal"] is True
