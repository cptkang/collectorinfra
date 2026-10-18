"""plans/184 M3 — deepagents(1단) 조립 불가 경고가 실제 확정 단 이름을 찍는다.

종전 경고는 2단 플래그가 켜져 있어도 「semantic_router 경로로 폴백」이라 써 운영 로그 판독을
오도했다. 확정은 `resolve_ladder_tier`(docs/21_orchestration_ladder.md) — 경고는 그 결과를 쓴다.
"""

from __future__ import annotations

import logging

import pytest

import src.graph as graph_module
from src.graph import build_graph
from tests.test_orchestration.test_deep_agent_wiring import _build_config, _node_names


def _warnings(caplog) -> str:
    return " ".join(r.getMessage() for r in caplog.records
                    if r.name == graph_module.logger.name and r.levelno == logging.WARNING)


@pytest.mark.parametrize(("intent", "semantic", "tier"), [
    (True, True, "intent_orchestration"),
    (False, True, "semantic_router"),
    (False, False, "legacy"),
], ids=["tier2", "tier3", "tier4"])
def test_package_missing_warning_names_resolved_tier(monkeypatch, caplog, intent, semantic,
                                                     tier) -> None:
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: False)
    cfg = _build_config(package=True, semantic=semantic)
    cfg.enable_intent_orchestration = intent
    with caplog.at_level(logging.WARNING, logger=graph_module.logger.name):
        compiled = build_graph(cfg)
    text = _warnings(caplog)
    assert f"조립 불가 → {tier} 경로로 폴백합니다" in text
    assert "degraded_reason=package_missing" in text
    assert "deep_agent" not in _node_names(compiled)


def test_no_package_missing_warning_when_buildable(monkeypatch, caplog) -> None:
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: True)
    cfg = _build_config(package=True, semantic=True)
    with caplog.at_level(logging.WARNING, logger=graph_module.logger.name):
        build_graph(cfg)
    assert "조립 불가" not in _warnings(caplog)
