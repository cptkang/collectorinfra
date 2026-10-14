"""plans/162 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능,
단계 3 이후 갱신 금지(갱신이 필요하면 리팩터가 아니다).

SSE 진행 표시 화이트리스트 `_STREAM_KNOWN_NODES`(`src/api/routes/query.py`)와
`build_graph()`가 실제로 등록하는 노드 이름의 관계를 고정한다(스멜 A6).
둘은 손으로 맞춘 두 목록이라 한쪽만 바뀌면 진행 이벤트가 소리 없이 빠진다.

플래그 매트릭스(사다리 1·2·3·4단 + 3단·4단 옵트인 노드)로 빌드한 그래프의 노드 합집합 U에 대해
- ``known_minus_graph`` = `_STREAM_KNOWN_NODES - U` (그래프에 없는 진행 노드명)
- ``graph_minus_known`` = `U - _STREAM_KNOWN_NODES`
  (진행 요약 대상 밖 노드 · `__start__`/`__end__` 제외)
를 「지금 값」으로 적는다. 1단(`deep_agent`)은 오케스트레이터 가용·deepagents 조립이 환경에 달려
빌드 시 판정 함수 둘을 대역으로 바꾼다(노드 등록만 본다 — 실행하지 않는다).

스냅샷 갱신::

    UPDATE_PLAN162_SNAPSHOT=1 python -m pytest \
        tests/test_api/test_plan162_w0_stream_node_names.py

실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

SNAPSHOT_PATH = (
    Path(__file__).resolve().parents[1] / "snapshots" / "plan162_w0" / "stream_node_names.json"
)
UPDATE_ENV = "UPDATE_PLAN162_SNAPSHOT"
PSEUDO_NODES = {"__start__", "__end__"}

#: 빌드 매트릭스 — 이름: 켜는 플래그. 나머지 플래그는 전부 off로 명시한다(`.env` 누수 차단).
MATRIX: dict[str, dict[str, bool]] = {
    "tier1_deep_agent": {"deepagents": True, "intent": True, "semantic": True},
    "tier2": {"intent": True},
    "tier2_with_semantic_flag": {"intent": True, "semantic": True, "approval": True},
    "tier3_options": {"semantic": True, "fault": True, "sequential": True, "probe": True,
                      "approval": True},
    "tier3_plan_loop": {"semantic": True, "plan_loop": True},
    "tier4": {"sequential": True, "approval": True},
}


def _config(flags: dict[str, bool]) -> Any:
    from src.config import AppConfig, LLMConfig

    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )
    cfg.enable_deepagents_package = flags.get("deepagents", False)
    cfg.enable_intent_orchestration = flags.get("intent", False)
    cfg.enable_semantic_routing = flags.get("semantic", False)
    cfg.enable_sql_approval = flags.get("approval", False)
    cfg.tier3_plan_loop_enabled = flags.get("plan_loop", False)
    cfg.cross_system_probe_enabled = flags.get("probe", False)
    cfg.worker_provider_override = None
    cfg.noise_gate.fault_diagnosis_enabled = flags.get("fault", False)
    cfg.composite.sequential_fallback_tiers_enabled = flags.get("sequential", False)
    cfg.observability.trace_enabled = False
    return cfg


def _node_names(flags: dict[str, bool], monkeypatch) -> set[str]:
    from src import graph as graph_mod
    from src.observability import ladder

    # build_graph는 사다리 확정을 프로세스 전역에 기록한다 — 테스트 뒤 되돌린다
    monkeypatch.setattr(ladder, "_resolution", ladder._resolution)
    if flags.get("deepagents"):
        monkeypatch.setattr(graph_mod, "select_orchestration_backend", lambda config: "deep_agent")
        monkeypatch.setattr(graph_mod, "_deep_agent_buildable", lambda config, llm: True)
    compiled = graph_mod.build_graph(_config(flags))
    return set(compiled.get_graph().nodes) - PSEUDO_NODES


def _current(monkeypatch) -> dict[str, Any]:
    from src.api.routes.query import _STREAM_KNOWN_NODES

    per_build: dict[str, list[str]] = {}
    union: set[str] = set()
    for name, flags in MATRIX.items():
        with monkeypatch.context() as mp:
            nodes = _node_names(flags, mp)
        per_build[name] = sorted(nodes)
        union |= nodes
    known = set(_STREAM_KNOWN_NODES)
    return {
        "known_nodes": sorted(known),
        "known_count": len(known),
        "graph_nodes_per_build": per_build,
        "known_minus_graph": sorted(known - union),
        "graph_minus_known": sorted(union - known),
    }


def test_stream_known_nodes_vs_graph_nodes_snapshot(monkeypatch) -> None:
    current = _current(monkeypatch)

    if os.environ.get(UPDATE_ENV) == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — {UPDATE_ENV}=1로 최초 채록하라")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    # 핵심 관계 두 집합을 먼저 본다 — 실패 메시지가 원인을 바로 가리키게
    assert current["known_minus_graph"] == expected["known_minus_graph"], (
        "그래프에 없는 진행 노드명이 바뀌었다")
    assert current["graph_minus_known"] == expected["graph_minus_known"], (
        "진행 요약 대상 밖 노드가 바뀌었다")
    assert current == expected
