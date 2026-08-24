"""그래프 빌드 분기 테스트 (Plan 48 §9).

검증 대상:
- 플래그 off 시 orchestration 노드 미등록·기존 경로 유지 (test_backward_compat_flag_off)
- 플래그 on 시 3노드 등록·orchestration 경로 (test_orchestration_flag_on)
"""

import os

import pytest

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.graph import build_graph

ORCH_NODES = {"intent_planner", "agent_orchestrator", "result_aggregator"}


def _build_config(*, orchestration: bool, semantic: bool) -> AppConfig:
    """오케스트레이션/시멘틱 플래그를 명시 설정한 테스트 config를 만든다.

    model_post_init가 환경변수를 읽으므로, 생성 전 관련 env를 제거하고
    생성 후 플래그를 명시적으로 덮어쓴다(테스트 환경 오염 방지).
    """
    os.environ.pop("ENABLE_INTENT_ORCHESTRATION", None)
    os.environ.pop("ENABLE_SEMANTIC_ROUTING", None)
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db", mcp_call_timeout=60),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(sensitive_columns=["password"], mask_pattern="***"),
        server=ServerConfig(host="0.0.0.0", port=8000),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )
    cfg.enable_intent_orchestration = orchestration
    cfg.enable_semantic_routing = semantic
    # 트랙 B(deepagents 실제 패키지)는 이 테스트 범위 밖 — .env의 ENABLE_DEEPAGENTS_PACKAGE
    # 누수로 deep_agent 경로가 선택되면 orchestration/semantic 노드가 미등록되어 오탐한다.
    # 명시적으로 비활성화하여 누수를 차단한다(D-037 Decision 2 패턴).
    cfg.enable_deepagents_package = False
    return cfg


def _node_names(compiled) -> set[str]:
    """컴파일된 그래프의 노드 이름 집합을 반환한다."""
    return set(compiled.get_graph().nodes.keys())


def test_backward_compat_flag_off():
    """플래그 off(기본) → orchestration 3노드 미등록, 기존 경로(schema_analyzer) 유지."""
    cfg = _build_config(orchestration=False, semantic=False)
    compiled = build_graph(cfg)
    names = _node_names(compiled)

    # orchestration 노드 미등록
    assert ORCH_NODES.isdisjoint(names), f"플래그 off인데 orchestration 노드 등록됨: {ORCH_NODES & names}"
    # 기존 단일 DB 경로 유지
    assert "schema_analyzer" in names
    assert "query_generator" in names
    assert "field_mapper" in names


def test_backward_compat_semantic_routing_path():
    """플래그 off + semantic_routing on → semantic_router 경로, orchestration 미등록."""
    cfg = _build_config(orchestration=False, semantic=True)
    compiled = build_graph(cfg)
    names = _node_names(compiled)

    assert ORCH_NODES.isdisjoint(names)
    assert "semantic_router" in names


def test_orchestration_flag_on():
    """플래그 on → orchestration 3노드 등록 + 컴파일 성공."""
    cfg = _build_config(orchestration=True, semantic=False)
    compiled = build_graph(cfg)
    names = _node_names(compiled)

    assert ORCH_NODES.issubset(names), f"orchestration 노드 누락: {ORCH_NODES - names}"
    # 기존 단일 DB 노드도 그래프에 공존 (subagent가 함수로 재사용하므로 노드 등록은 유지)
    assert "field_mapper" in names
