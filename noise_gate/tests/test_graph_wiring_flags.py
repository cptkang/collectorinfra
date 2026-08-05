"""build_alarm_graph 플래그 조합별 배선 통합 테스트 (Plan 52 §8.1).

history_enabled × enable_noise_gate 4조합에 대해 컴파일된 그래프의 노드 집합·엣지·
entry point 를 실측 검증한다. 핵심: 게이트 활성 시 enricher 는 noise_context 수집원이므로
history_enabled 와 무관하게 강제 포함되어야 한다(§8.1 표).
"""

from __future__ import annotations

from types import SimpleNamespace

from noise_gate.orchestration.alarm_graph import build_alarm_graph


def make_config(history_enabled: bool, gate_enabled: bool) -> SimpleNamespace:
    """경량 설정(SimpleNamespace) — alarm.history_enabled / noise_gate.enable_noise_gate 토글."""
    return SimpleNamespace(
        alarm=SimpleNamespace(history_enabled=history_enabled),
        noise_gate=SimpleNamespace(enable_noise_gate=gate_enabled),
    )


def nodes_of(g) -> set[str]:
    """컴파일된 그래프의 노드 이름 집합(시작/종료 가상 노드 포함)."""
    return set(g.get_graph().nodes.keys())


def edges_of(g) -> set[tuple[str, str]]:
    """컴파일된 그래프의 (source, target) 엣지 집합."""
    return {(e.source, e.target) for e in g.get_graph().edges}


def entry_target(g) -> str:
    """__start__ 에서 연결된 진입 노드(entry point)를 반환한다."""
    for src, tgt in edges_of(g):
        if src == "__start__":
            return tgt
    raise AssertionError("__start__ 엣지가 없습니다")


class TestWiringFF:
    """history=F, gate=F — 현행 2노드(analyzer→notifier), 무변경."""

    def test_nodes_and_entry(self):
        g = build_alarm_graph(make_config(False, False))
        nodes = nodes_of(g)
        assert "alarm_analyzer" in nodes
        assert "alarm_notifier" in nodes
        assert "alarm_context_enricher" not in nodes
        assert "notification_gate" not in nodes
        assert entry_target(g) == "alarm_analyzer"

    def test_edges(self):
        g = build_alarm_graph(make_config(False, False))
        edges = edges_of(g)
        assert ("alarm_analyzer", "alarm_notifier") in edges
        assert ("alarm_notifier", "__end__") in edges


class TestWiringTF:
    """history=T, gate=F — enricher 포함, gate 없음(현행 3노드)."""

    def test_nodes_and_entry(self):
        g = build_alarm_graph(make_config(True, False))
        nodes = nodes_of(g)
        assert "alarm_context_enricher" in nodes
        assert "notification_gate" not in nodes
        assert entry_target(g) == "alarm_context_enricher"

    def test_edges(self):
        g = build_alarm_graph(make_config(True, False))
        edges = edges_of(g)
        assert ("alarm_context_enricher", "alarm_analyzer") in edges
        assert ("alarm_analyzer", "alarm_notifier") in edges


class TestWiringFT:
    """history=F, gate=T — enricher 강제 포함 + notification_gate 포함.

    배선: enricher → analyzer → notification_gate → notifier (§8.1 핵심).
    """

    def test_enricher_forced_and_gate_present(self):
        g = build_alarm_graph(make_config(False, True))
        nodes = nodes_of(g)
        # history=F 이지만 게이트 활성이므로 enricher 가 강제 포함되어야 한다
        assert "alarm_context_enricher" in nodes
        assert "notification_gate" in nodes
        assert entry_target(g) == "alarm_context_enricher"

    def test_full_chain_edges(self):
        g = build_alarm_graph(make_config(False, True))
        edges = edges_of(g)
        assert ("alarm_context_enricher", "alarm_analyzer") in edges
        assert ("alarm_analyzer", "notification_gate") in edges
        assert ("notification_gate", "alarm_notifier") in edges
        assert ("alarm_notifier", "__end__") in edges
        # 게이트 활성 시 analyzer 가 notifier 로 직접 가지 않는다(게이트 경유)
        assert ("alarm_analyzer", "alarm_notifier") not in edges


class TestWiringTT:
    """history=T, gate=T — enricher + gate 모두 포함."""

    def test_nodes_and_chain(self):
        g = build_alarm_graph(make_config(True, True))
        nodes = nodes_of(g)
        assert "alarm_context_enricher" in nodes
        assert "notification_gate" in nodes
        assert entry_target(g) == "alarm_context_enricher"
        edges = edges_of(g)
        assert ("alarm_context_enricher", "alarm_analyzer") in edges
        assert ("alarm_analyzer", "notification_gate") in edges
        assert ("notification_gate", "alarm_notifier") in edges


def test_none_config_defaults_history_on_gate_off():
    """config=None → history 기본 True · gate 비활성(기존 동작 보존)."""
    g = build_alarm_graph(None)
    nodes = nodes_of(g)
    assert "alarm_context_enricher" in nodes  # history 기본 True
    assert "notification_gate" not in nodes   # gate 기본 off
