"""그래프 위상 스냅샷 — 사다리 단 × 3단 옵트인 플래그 매트릭스 (plans/166 W0 · 스멜 O3).

plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지(갱신이
필요하면 리팩터가 아니다). 기대값은 「맞는 값」이 아니라 「지금 값」이다(D-337 ⑤).

`build_graph(cfg).get_graph()`의 노드 집합·엣지 집합(source, target, conditional)·
`interrupt_before`·확정 사다리 단과 사유를 조합별로 정렬해 JSON으로 고정한다.
`get_graph()`는 START에서 닿지 않는 노드의 엣지를 그리지 않는다(실측 — 1·2단 빌드의 단일 DB 꼬리
엣지가 빠진다). 그래서 빌더에 **선언된** 고정 엣지(`declared_edges`)와 조건부 분기의 경로 표
(`declared_branches` — 분기 함수 이름은 빼고 source별 (키, 대상)만)를 함께 고정한다. O3 리팩터
(`resolve_ladder_tier` 선행 1회 · 단별 `_wire_*` 분할)가 배선을 한 줄도 바꾸지 않았는지의
판정 기준이다.

사다리 축(앞 단이 성립하면 하위 단 플래그는 무시된다 — 운영 `.env`처럼 상위 단 플래그도 켜 둔다):

| 키 | enable_deepagents_package | enable_intent_orchestration | enable_semantic_routing |
|---|---|---|---|
| tier1 | True (+ 백엔드 "deep_agent" 고정) | True | True |
| tier2 | False | True | True |
| tier3 | False | False | True |
| tier4 | False | False | False |

1단은 `select_orchestration_backend`를 "deep_agent"로 고정한다 — 실제 판정은 오케스트레이터
`/v1/models` health check(네트워크)라 테스트에서 돌리지 않는다. deepagents 조립
(`_deep_agent_buildable`)은 실제로 수행하며, 이 환경에서는 조립에 성공해 1단으로 확정된다
(스냅샷의 `ladder` 칸이 그 근거다 — 조립이 실패하는 환경이면 `package_missing` 폴백으로
기록돼 여기서 실패한다).

옵션 축 — `build_graph()`에서 `config.` 플래그로 **노드 등록**을 바꾸는 옵트인 분기를 전수로 골랐다:

- `approval_on`  — `enable_sql_approval`: `approval_gate` 노드 + 검증 뒤 분기 교체 +
  interrupt_before
  (전 단 공통이지만 3단 단일 DB 꼬리 배선을 바꾼다)
- `fault_dx_on`  — `noise_gate.fault_diagnosis_enabled`: 3단 `fault_diagnosis` 노드·라우팅 대상
- `plan_loop_on` — `tier3_plan_loop_enabled`: 3단 계획 루프 7노드(plan…finalize) · 순차 러너 미등록
- `seq_on`       — `composite.sequential_fallback_tiers_enabled`: 3·4단 `sequential_runner`
  (코드 기본값이 True다 — 위임은 「4종」이었으나 노드 등록을 바꾸는 옵트인 분기가 실측 5종이라
  계획 루프와의 상호 배제(plan_loop_tier면 미등록)까지 덮으려고 함께 넣었다)
- `probe_on`     — `cross_system_probe_enabled`: 3단 `entity_locator` 노드 + 라우터 뒤 경유 배선

조합: 1·2·4단은 `base`(5종 모두 off) · 각 옵션 단독 on 5종 · `all_on`(5종 모두 on) = 7종씩 21건,
3단은 옵션 5종의 전곱 2^5 = 32건(부분 조합 덮음) — 합계 53건. 키 이름은 공집합 `base`,
단독은 옵션명, 5종 전부는 `all_on`, 그 밖의 부분 조합은 `_OPTIONS` 순서로 `+` 연결
(예: `tier3|approval_on+plan_loop_on`).
노드 등록에 영향이 없는 `observability.trace_enabled`(TracedGraph 프록시)는 False로 고정한다.

`.env` 누수 차단: `.env`에 사다리 1·2·3단 플래그가 모두 true이므로 위 플래그 8종과
워커/오케스트레이터 LLM 설정을 전부 명시한다(pydantic-settings의 env_file은 os.environ에
주입되지 않는다).
LLM은 생성만 하고 호출하지 않는다(네트워크 없음).

스냅샷 (재)생성: `UPDATE_PLAN162_SNAPSHOT=1 python -m pytest <이 파일>`
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
from src.config import AppConfig, LLMConfig, OrchestratorConfig

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent.parent / "snapshots" / "plan166_w0" / "graph_topology.json"
)

#: 사다리 단 → (deepagents, intent_orchestration, semantic_routing)
_TIERS: dict[str, tuple[bool, bool, bool]] = {
    "tier1": (True, True, True),
    "tier2": (False, True, True),
    "tier3": (False, False, True),
    "tier4": (False, False, False),
}

_OPTIONS: tuple[str, ...] = ("approval_on", "fault_dx_on", "plan_loop_on", "seq_on", "probe_on")

_COMBOS: tuple[str, ...] = ("base", *_OPTIONS, "all_on")


def _combo_name(on: tuple[str, ...]) -> str:
    """켜진 옵션 집합 → 조합 이름(공집합 `base` · 전부 `all_on` · 그 밖은 `+` 연결)."""
    if not on:
        return "base"
    if len(on) == len(_OPTIONS):
        return "all_on"
    return "+".join(on)


def _tier3_combos() -> list[str]:
    """3단 전용 — 옵션 5종의 전곱 2^5 조합 이름(`_OPTIONS` 순서 유지)."""
    return [
        _combo_name(tuple(o for i, o in enumerate(_OPTIONS) if mask >> i & 1))
        for mask in range(1 << len(_OPTIONS))
    ]


def _combo_keys() -> list[str]:
    """사람이 읽는 조합 키 목록(`tier3|approval_on` 형식)."""
    return [
        f"{tier}|{combo}"
        for tier in _TIERS
        for combo in (_tier3_combos() if tier == "tier3" else _COMBOS)
    ]


def _build_config(tier: str, combo: str) -> AppConfig:
    """조합 키에 맞춰 사다리·옵션 플래그를 전부 명시한 테스트 config를 만든다."""
    deep, intent, semantic = _TIERS[tier]
    if combo == "all_on":
        on = set(_OPTIONS)
    elif combo == "base":
        on = set()
    else:
        on = set(combo.split("+"))
    assert on <= set(_OPTIONS), combo
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        orchestrator=OrchestratorConfig(
            provider="vllm",
            base_url="http://127.0.0.1:9/v1",
            model="Qwen3.5-9B",
        ),
    )
    cfg.worker_provider_override = None
    cfg.enable_deepagents_package = deep
    cfg.enable_intent_orchestration = intent
    cfg.enable_semantic_routing = semantic
    cfg.enable_sql_approval = "approval_on" in on
    cfg.noise_gate.fault_diagnosis_enabled = "fault_dx_on" in on
    cfg.tier3_plan_loop_enabled = "plan_loop_on" in on
    cfg.composite.sequential_fallback_tiers_enabled = "seq_on" in on
    cfg.cross_system_probe_enabled = "probe_on" in on
    cfg.observability.trace_enabled = False
    return cfg


def _topology(monkeypatch: pytest.MonkeyPatch, key: str) -> dict[str, Any]:
    """조합 1건의 그래프를 빌드해 정렬된 위상 dict를 돌려준다."""
    tier, combo = key.split("|")
    cfg = _build_config(tier, combo)
    if tier == "tier1":
        # 오케스트레이터 health check(네트워크) 대신 가용 판정을 고정한다.
        monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
    else:
        monkeypatch.setattr(
            graph_module, "select_orchestration_backend", lambda c: "semantic_router"
        )
    ladder: list[tuple[str, str]] = []
    monkeypatch.setattr(
        graph_module,
        "record_ladder_resolution",
        lambda t, r, **_: ladder.append((t.value, r)),
    )
    compiled = graph_module.build_graph(cfg, checkpointer=InMemorySaver())
    drawable = compiled.get_graph()
    edges = sorted({(e.source, e.target, bool(e.conditional)) for e in drawable.edges})
    builder = compiled.builder
    declared_branches = {
        source: sorted(
            {(str(k), str(v)) for spec in branches.values() for k, v in (spec.ends or {}).items()}
        )
        for source, branches in builder.branches.items()
    }
    assert len(ladder) == 1, ladder
    return {
        "ladder": list(ladder[0]),
        "nodes": sorted(drawable.nodes.keys()),
        "edges": [list(e) for e in edges],
        "declared_edges": [list(e) for e in sorted(builder.edges)],
        "declared_branches": {k: [list(p) for p in v] for k, v in declared_branches.items()},
        "interrupt_before": sorted(compiled.interrupt_before_nodes or []),
    }


def _collect(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """전 조합의 위상을 모은다(조합마다 monkeypatch를 새로 건다)."""
    result: dict[str, Any] = {}
    for key in _combo_keys():
        with monkeypatch.context() as mp:
            result[key] = _topology(mp, key)
    return result


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def test_graph_topology_matches_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """사다리 4단 × 옵션 조합(3단 32 · 나머지 7씩)의 노드·엣지 집합이 스냅샷과 같다."""
    current = _collect(monkeypatch)

    if os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(_dump(current), encoding="utf-8")
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert set(current) == set(expected), (
        f"조합 집합이 달라졌다 (신규: {sorted(set(current) - set(expected))}, "
        f"소실: {sorted(set(expected) - set(current))})"
    )
    changed = sorted(k for k in current if current[k] != expected[k])
    assert not changed, f"배선이 바뀐 조합: {changed} — 리팩터라면 배선은 불변이어야 한다"


def test_ladder_tier_per_row_is_as_labelled(monkeypatch: pytest.MonkeyPatch) -> None:
    """조합 키의 단 이름과 확정 사다리 단이 일치한다(1단 조립 폴백이 없음을 명시 고정)."""
    expected_tier = {
        "tier1": "deep_agent",
        "tier2": "intent_orchestration",
        "tier3": "semantic_router",
        "tier4": "legacy",
    }
    for tier in _TIERS:
        with monkeypatch.context() as mp:
            got = _topology(mp, f"{tier}|base")["ladder"][0]
        assert got == expected_tier[tier], (tier, got)
