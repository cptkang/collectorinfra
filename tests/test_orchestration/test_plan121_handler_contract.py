"""plans/121 TP-2.1a — 처리기 계약 필드(§4.4 · G-22 · D-272 ③ 묶음 C).

검증 항목
- 레지스트리 7종 모두 계약 필드를 채웠고 값이 어휘 안에 있다 · `side_effects`는 전부 none(D-003)
- 실측값 고정: 소스 종류 · 결과 형태 · 필수 입력 · 바인드 상한(현행 코드 상수·분기에서 확인한 값)
- `purpose`는 분해 프롬프트 현행 줄 그대로다(TP-2.1b 렌더가 분해 목록을 바이트 그대로 재현하도록)
- 소비처 0 — `description`과 1단 도구 목록(이름·설명)이 클린 기준선(2e635a9)과 해시가 같다
- 필드 기본값: 계약을 적지 않은 테스트 대역(`SubAgentSpec(name, desc, handler)`)은 미선언(None)

LLM·DB·네트워크 0.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.orchestration import deepagents_tools, subagents
from src.orchestration.host_inspect import HOST_INSPECT_AGENT
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec
from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE
from src.prompts.orchestrator import ORCHESTRATOR_INSTRUCTIONS

#: 클린 기준선 사본(HEAD 2e635a9 · 묶음 A·B 이전)에서 잰 값 — TP-2.1a는 이 둘을 바꾸지 않는다.
#: 1단 도구 목록 `[[도구 이름, 설명], ...]`(`deepagents_tools.build_tools` 순서) JSON의 sha256.
_BASELINE_TOOLS_SHA256 = "53deec871258eafc35cd35c84b80ebf2ffa8b6f6018814e54575b483ec278b50"
#: 레지스트리 `{agent: description}` JSON의 sha256.
_BASELINE_DESCRIPTIONS_SHA256 = "70762523335a2ad1bc3b1f278dfeb5737fc87432d13ce4265b7e4198ab16fd0d"

_BACKENDS = {"sql", "mcp", "rest", "none"}
_OUTPUT_TYPES = {"rows", "entity_set", "scalar", "text", "file"}
_SLOTS = {"entity_set", "db_set", "time_window", "scalar"}
_FILTERS = {"zone", "time", "host"}
_FACETS = {"hostname", "server_name", "ip", "fqdn", "asset_key"}

_DECOMPOSITION_AGENTS = (
    "data_query", "process_query", "alarm_query",
    "cache_management", "synonym_registration", "general_inference",
)


def _sha256(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def test_registry_has_seven_handlers() -> None:
    assert set(SUBAGENT_REGISTRY) == {*_DECOMPOSITION_AGENTS, HOST_INSPECT_AGENT}


@pytest.mark.parametrize("name", sorted(SUBAGENT_REGISTRY))
def test_contract_fields_filled_within_vocabulary(name: str) -> None:
    spec = SUBAGENT_REGISTRY[name]
    assert spec.purpose.strip()
    assert spec.backend in _BACKENDS
    assert spec.output_type in _OUTPUT_TYPES
    assert spec.side_effects == "none"  # D-003 — 관측 데이터 소스 읽기 전용
    assert set(spec.input_slots) <= _SLOTS
    assert set(spec.required_inputs) <= set(spec.input_slots)
    assert set(spec.self_filters) <= _FILTERS
    assert set(spec.key_facets_out) <= _FACETS
    for bound in (spec.max_bind_values, spec.bind_block):
        assert bound is None or bound >= 1
    if spec.max_bind_values is not None and spec.bind_block is not None:
        assert spec.bind_block <= spec.max_bind_values


def test_measured_values() -> None:
    """현행 코드에서 확인한 값(추정 아님) — 바뀌면 코드 쪽 근거부터 다시 잰다."""
    got = {
        name: (s.backend, s.output_type, s.required_inputs, s.max_bind_values, s.bind_block)
        for name, s in SUBAGENT_REGISTRY.items()
    }
    prior_cap = subagents._MAX_PRIOR_ROWS
    assert got == {
        "data_query": ("sql", "rows", (), prior_cap, None),
        "alarm_query": ("sql", "rows", (), prior_cap, None),
        "process_query": ("rest", "rows", ("entity_set",), None, 1),
        HOST_INSPECT_AGENT: ("mcp", "rows", ("entity_set",), 1, 1),
        "cache_management": ("sql", "text", (), None, None),
        "synonym_registration": ("none", "text", (), None, None),
        "general_inference": ("none", "text", (), None, None),
    }
    assert prior_cap == 100
    # 미측정 칸은 비워 둔다(추정 금지) — 지연 등급·고정 타임아웃·동시 호출 상한
    for spec in SUBAGENT_REGISTRY.values():
        assert (spec.latency_class, spec.timeout_sec, spec.concurrency) == (None, None, None)


@pytest.mark.parametrize("name", _DECOMPOSITION_AGENTS)
def test_purpose_is_current_decomposition_line(name: str) -> None:
    line = f"- **{name}**: {SUBAGENT_REGISTRY[name].purpose}\n"
    assert line in INTENT_PLANNER_SYSTEM_TEMPLATE


def test_host_inspect_purpose_is_shared_prefix_of_existing_texts() -> None:
    spec = SUBAGENT_REGISTRY[HOST_INSPECT_AGENT]
    assert spec.description.startswith(spec.purpose)
    assert f"- inspect_host: {spec.purpose}" in ORCHESTRATOR_INSTRUCTIONS


def test_tier1_tool_list_unchanged_from_clean_baseline() -> None:
    tools = deepagents_tools.build_tools(MagicMock(), MagicMock())
    assert _sha256([[t.name, t.description] for t in tools]) == _BASELINE_TOOLS_SHA256


def test_registry_descriptions_unchanged_from_clean_baseline() -> None:
    descriptions = {name: spec.description for name, spec in SUBAGENT_REGISTRY.items()}
    assert _sha256(descriptions) == _BASELINE_DESCRIPTIONS_SHA256


def test_positional_test_doubles_keep_working_with_undeclared_contract() -> None:
    async def _handler(
        task: dict[str, Any], isolated: dict[str, Any], *, llm: Any, app_config: Any
    ) -> dict[str, Any]:
        return {}

    spec = SubAgentSpec("data_query", "DB", _handler)
    assert (spec.purpose, spec.backend, spec.output_type) == ("", None, None)
    assert spec.input_slots == spec.required_inputs == spec.key_facets_out == ()
    assert spec.side_effects == "none"
