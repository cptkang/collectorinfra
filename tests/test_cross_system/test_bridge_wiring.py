"""키 브리지 3단 기준 배선 (plans/102 X-4 · §3.5 ④ · D-224).

3단 `semantic_router`의 순차 러너는 2단 부품(`agent_orchestrator` → `subagents` → `query_generator`/
`multi_db_executor`)을 함수로 재사용한다. 이 파일은 그 경로의 네 호출 지점을 고정한다.

- **플래그 off = 비트 동일**: 게이트 호출 인자 · 주입 행 shape · 스코프 블록 문자열 ·
  판정(verdict) · 사후 대조가 종전과 같다
  (§1.2 자산→폴스타 체인은 여전히 `prior_no_identity`로 막힌다).
- **플래그 on**: §1.2 실행 재현이 뒤집힌다 — 자산관리 선행 행이 게이트를 통과하고,
  **3단 순차 러너 경유**로 폴스타 후속 task가 실행되며,
  후속 SQL 프롬프트의 대상 블록이 `LOWER(hostname) IN (...)`으로 렌더되고,
  결과는 매칭 등급으로 판정돼 노트 1건이 남는다.

mock LLM·mock handler만 쓴다(D-127 — 실 LLM·DB 0).
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.nodes.key_bridge as kb
from src.orchestration.agent_orchestrator import _gate_level, agent_orchestrator
from src.orchestration.sequential_runner import sequential_runner
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec, _extract_identity_rows
from src.state import create_initial_state
from src.utils.prior_dependency import (
    NOTE_BRIDGE,
    REASON_PRIOR_NO_IDENTITY,
    assess_prior_dependency,
)
from src.utils.query_gen_common import build_prior_rows_block

ao_mod = importlib.import_module("src.orchestration.agent_orchestrator")
sa_mod = importlib.import_module("src.orchestration.subagents")
sr_mod = importlib.import_module("src.orchestration.sequential_runner")
qg_mod = importlib.import_module("src.nodes.query_generator")
mde_mod = importlib.import_module("src.nodes.multi_db_executor")

ITAM_ROW = {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1", "hWSportEndYmd": "20261201"}
QUERY = "HW 지원 종료가 6개월 안 남은 서버를 찾아 그 서버들의 현재 알람"
LEGACY_NO_IDENTITY_DETAIL = (
    "선행 작업(t1) 결과에 서버 식별 컬럼이 없어 대상을 확정할 수 없습니다 (행 1건)."
)


def _cfg(mock_config, *, bridge: bool):
    """.env 누수 차단 — 검증 대상 플래그를 전부 명시한다(CLAUDE.md Known Mistakes)."""
    mock_config.cross_system_key_bridge_enabled = bridge
    mock_config.composite.sequential_gate_enabled = True
    mock_config.composite.scope_postcheck_enabled = True
    mock_config.composite.prior_scope_by_db_enabled = False
    mock_config.composite.prior_targets_enabled = False
    return mock_config


@pytest.fixture
def patched_config(monkeypatch, mock_config):
    """`_extract_identity_rows`·`_make_isolated_input`은 전역 `load_config()`를 읽는다.

    같은 설정을 주입한다.
    """

    def _apply(bridge: bool):
        cfg = _cfg(mock_config, bridge=bridge)
        monkeypatch.setattr(sa_mod, "load_config", lambda: cfg)
        return cfg

    return _apply


def _chain() -> list[dict]:
    return [
        {
            "task_id": "t1",
            "agent": "data_query",
            "sub_query": "HW 지원 종료 6개월 내 서버",
            "depends_on": [],
            "input_from": [],
            "order": 1,
            "status": "pending",
        },
        {
            "task_id": "t2",
            "agent": "alarm_query",
            "sub_query": "선행 결과 서버들의 현재 알람",
            "depends_on": ["t1"],
            "input_from": ["t1"],
            "order": 2,
            "status": "pending",
        },
    ]


def _registry(handler) -> dict:
    out = {}
    for name in ("data_query", "alarm_query"):
        base = SUBAGENT_REGISTRY[name]
        out[name] = SubAgentSpec(base.name, base.description, handler, fallback=base.fallback)
    return out


def _qg_config(bridge: bool) -> MagicMock:
    """query_generator를 LLM 폴백 경로로만 태우는 최소 설정 대역."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.text2sql.semantic_compose = False
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.synonym.value_retrieval = False
    cfg.get_polestar_db_ids.return_value = None
    cfg.cross_system_key_bridge_enabled = bridge
    return cfg


async def _render_followup_prompt(isolated: dict, *, bridge: bool, db_id: str = "polestar") -> str:
    """후속 task가 실제 `query_generator`로 SQL 프롬프트를 만들 때의 사용자 프롬프트(LLM은 mock)."""
    state = create_initial_state(user_query=isolated.get("user_query", ""))
    state.update(
        {
            "parsed_requirements": {"original_query": isolated.get("user_query", "")},
            "schema_info": {
                "tables": {"cmm_alarm": {"columns": [{"name": "severity", "type": "varchar"}]}}
            },
            "prior_rows": isolated.get("prior_rows"),
            "active_db_id": db_id,
            "active_db_engine": "postgresql",
        }
    )
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content="```sql\nSELECT 1 FROM cmm_alarm LIMIT 10;\n```")
    await qg_mod.query_generator(state, llm=llm, app_config=_qg_config(bridge))
    return llm.ainvoke.call_args[0][0][-1].content


# ──────────────────────────────────────────────
# 플래그 off — 비트 동일
# ──────────────────────────────────────────────


def test_off_gate_calls_assess_exactly_as_before(monkeypatch):
    calls: list[dict] = []

    def spy(task, prior, **kwargs):
        calls.append(kwargs)
        return assess_prior_dependency(task, prior, **kwargs)

    monkeypatch.setattr(ao_mod, "assess_prior_dependency", spy)
    monkeypatch.setattr(
        ao_mod, "resolve_gate_identity", MagicMock(side_effect=AssertionError("off에서 호출 금지"))
    )
    results = {"t1": {"query_results": [ITAM_ROW]}}
    verdicts: dict = {}
    level = [dict(_chain()[1])]
    runnable = _gate_level(level, results, gate_on=True, notes=[], verdicts=verdicts)
    assert calls == [{}]
    assert runnable == [] and verdicts["t2"].reason == REASON_PRIOR_NO_IDENTITY
    assert verdicts["t2"].detail == LEGACY_NO_IDENTITY_DETAIL


@pytest.mark.parametrize(
    "rows, expected",
    [
        ([ITAM_ROW], [ITAM_ROW]),  # 이름 판정 식별 컬럼 없음 → 전 컬럼 유지(행수 상한만)
        (
            [{"hostname": "a", "cpu": 1, "_source_db": "polestar_cm_gp"}],
            [{"hostname": "a"}],
        ),  # 출처 태그 버림(by_db off)
        ([{"server_name": "SV-1", "severity": "critical"}], [{"server_name": "SV-1"}]),
    ],
)
def test_off_identity_rows_shape_unchanged(patched_config, rows, expected):
    patched_config(False)
    assert _extract_identity_rows(rows) == expected


@pytest.mark.asyncio
async def test_off_query_generator_block_is_legacy_block(sample_state):
    prior_rows = {"t1": [{"hostname": "svr-web-01"}, {"hostname": "svr-web-02"}]}
    human = await _render_followup_prompt(
        {"user_query": "선행 결과 서버들의 현재 알람", "prior_rows": prior_rows}, bridge=False
    )
    assert build_prior_rows_block(prior_rows) in human
    assert "LOWER(hostname)" not in human


def test_off_multi_db_prior_for_db_returns_run_values():
    run = SimpleNamespace(
        app_config=SimpleNamespace(cross_system_key_bridge_enabled=False),
        prior_scope_by_db=None,
        prior_block="BLOCK",
        prior_scope=("hostname", ["a"]),
        state={"prior_rows": {"t1": [ITAM_ROW]}},
    )
    block, scope = mde_mod._prior_for_db(run, "polestar")
    assert block is run.prior_block and scope is run.prior_scope


@pytest.mark.asyncio
async def test_off_asset_to_polestar_chain_still_blocked(patched_config):
    """현행 재현(§1.2) — off면 자산→폴스타 후속이 `prior_no_identity`로 막힌다."""
    cfg = patched_config(False)
    calls: list[str] = []

    async def handler(task, isolated, **kw):
        calls.append(task["task_id"])
        return {"query_results": [ITAM_ROW], "target_db_ids": ["itam"]}

    state = create_initial_state(user_query=QUERY)
    state["task_plan"] = _chain()
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(state, llm=AsyncMock(), app_config=cfg)
    assert calls == ["t1"] and out["task_plan"][1]["status"] == "skipped"
    assert out["task_results"]["t2"]["skip_reason"] == REASON_PRIOR_NO_IDENTITY
    assert not [n for n in out.get("dependency_notes") or [] if n.get("kind") == NOTE_BRIDGE]


# ──────────────────────────────────────────────
# 플래그 on — 3단 순차 러너 경유
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_tier3_sequential_runner_runs_asset_to_polestar_chain(patched_config):
    """★ §1.2 실행 재현 뒤집힘 — 3단 순차 러너 경유로 자산→폴스타 후속이 실행된다.

    대상 블록은 코드가 확정한 컬럼으로 렌더된다.
    """
    cfg = patched_config(True)
    captured: dict = {}

    async def handler(task, isolated, **kw):
        if task["task_id"] == "t1":
            return {
                "query_results": [ITAM_ROW],
                "target_db_ids": ["itam"],
                "generated_sql": "SELECT ...",
            }
        captured["prior_rows"] = isolated.get("prior_rows")
        captured["prompt"] = await _render_followup_prompt(isolated, bridge=True)
        return {
            "query_results": [
                {"hostname": "SVR-WEB-01", "severity": "critical"},
                {"hostname": "svr-db-99", "severity": "major"},  # 스코프 밖 — 판정에서 제외
            ],
            "target_db_ids": ["polestar"],
        }

    async def fake_aggregator(state, *, llm, app_config, synthesize):
        captured["aggregated"] = state
        return {"final_response": "답"}

    decomposed = {"tasks": _chain(), "clarification_needed": None}
    with (
        patch.object(sr_mod, "_llm_decompose", AsyncMock(return_value=decomposed)),
        patch.object(sr_mod, "result_aggregator", AsyncMock(side_effect=fake_aggregator)),
        patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))),
    ):
        out = await sequential_runner(
            create_initial_state(user_query=QUERY), llm=AsyncMock(), app_config=cfg
        )

    # 게이트 통과 → 후속 실행
    assert [t["status"] for t in out["task_plan"]] == ["completed", "completed"]
    # 주입 행: 값 판정 키 컬럼만(선별 조건 컬럼은 버린다)
    assert captured["prior_rows"] == {"t1": [{"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1"}]}
    # 대상 블록: 폴스타 매니페스트 컬럼을 코드가 확정 · 위임 문구 없음
    prompt = captured["prompt"]
    assert "LOWER(hostname) IN ('svr-web-01')" in prompt and "`cmm_resource.hostname`" in prompt
    assert "동등한 식별 컬럼" not in prompt
    # 판정·보고: link 1, 스코프 밖 행 제거, 노트 1건
    t2 = out["task_results"]["t2"]
    assert [r["hostname"] for r in t2["query_results"]] == ["SVR-WEB-01"]
    bridge_notes = [n for n in t2["dependency_notes"] if n["kind"] == NOTE_BRIDGE]
    assert len(bridge_notes) == 1
    assert bridge_notes[0]["counts"] == {
        "total": 1,
        "link": 1,
        "possible": 0,
        "non_link": 0,
        "ambiguous": 0,
    }
    assert bridge_notes[0]["coverage"] == 1.0
    # 사후 대조(D-203 postcheck)는 키 브리지 task에 중복 적용되지 않는다
    assert not [n for n in t2["dependency_notes"] if n["kind"] == "postcheck"]
    # 게이트 경과(trace)는 종전 채널 그대로 — 값 판정 키로 기록
    trace = [n for n in captured["aggregated"]["dependency_notes"] if n.get("kind") == "trace"]
    assert (
        trace and trace[0]["scope_col"] == "sevrHostName" and trace[0]["sample"] == ["svr-web-01"]
    )


@pytest.mark.asyncio
async def test_on_no_value_key_reason_names_types(patched_config):
    cfg = patched_config(True)

    async def handler(task, isolated, **kw):
        return {"query_results": [{"cnt": 3, "ratio": 0.5}], "target_db_ids": ["itam"]}

    state = create_initial_state(user_query=QUERY)
    state["task_plan"] = _chain()
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(state, llm=AsyncMock(), app_config=cfg)
    res = out["task_results"]["t2"]
    assert res["skip_reason"] == REASON_PRIOR_NO_IDENTITY
    assert (
        "값으로 찾은 키 타입: 없음" in res["error"] and "받는 키 타입: hostname, ip" in res["error"]
    )


def test_on_identity_rows_keep_value_keys_and_source_tag(patched_config):
    patched_config(True)
    rows = [
        {**ITAM_ROW, "_source_db": "itam"},
        {"sevrHostName": "SVR-WEB-02", "iPCtnt": "10.0.1.2", "_source_db": "itam"},
    ]
    assert _extract_identity_rows(rows) == [
        {"sevrHostName": "svr-web-01", "iPCtnt": "10.0.1.1", "_source_db": "itam"},
        {"sevrHostName": "SVR-WEB-02", "iPCtnt": "10.0.1.2", "_source_db": "itam"},
    ]


def test_on_multi_db_prior_for_db_builds_block_per_target_db(monkeypatch):
    """멀티 DB 경로 — DB마다 매니페스트 컬럼으로 블록을 다시 만든다(run 단위 블록 재사용 금지)."""
    from pathlib import Path

    from src.schema_cache.entity_key_manifest import load_entity_key_manifest, load_manifest_file

    local = load_manifest_file(
        Path(__file__).resolve().parents[2] / "testdata/itam/entity_keys.local.yaml", "itam"
    )
    monkeypatch.setattr(
        kb,
        "load_entity_key_manifest",
        lambda db_id, **k: local if db_id == "itam" else load_entity_key_manifest(db_id, **k),
    )
    monkeypatch.setattr(mde_mod, "is_scrub_samples_enabled", lambda: False)
    run = SimpleNamespace(
        app_config=SimpleNamespace(cross_system_key_bridge_enabled=True),
        prior_scope_by_db=None,
        prior_block="LEGACY",
        prior_scope=None,
        state={"prior_rows": {"t1": [{"hostname": "svr-web-03.synth.example"}]}},
    )
    polestar_block, scope = mde_mod._prior_for_db(run, "polestar_cm_gp")
    itam_block, _ = mde_mod._prior_for_db(run, "itam")
    assert "LOWER(hostname) IN ('svr-web-03.synth.example', 'svr-web-03')" in polestar_block
    assert "LOWER(sevrHostName) IN ('svr-web-03.synth.example', 'svr-web-03')" in itam_block
    assert scope is None


@pytest.mark.asyncio
async def test_on_polestar_only_chain_keeps_running(patched_config):
    """값 판정은 폴스타 단독 체인에도 적용된다 — 게이트·주입·판정이 끊기지 않는지(회귀 경계)."""
    cfg = patched_config(True)

    async def handler(task, isolated, **kw):
        if task["task_id"] == "t1":
            return {
                "query_results": [
                    {"hostname": "sv1", "name": "sv1 (주문)"},
                    {"hostname": "sv2", "name": "sv2"},
                ],
                "target_db_ids": ["polestar_cm_gp"],
            }
        assert isolated["prior_rows"] == {
            "t1": [{"hostname": "sv1", "name": "sv1 (주문)"}, {"hostname": "sv2", "name": "sv2"}]
        }
        return {
            "query_results": [{"hostname": "sv1", "cpu": 1}, {"hostname": "sv2", "cpu": 2}],
            "target_db_ids": ["polestar_cm_gp"],
        }

    state = create_initial_state(user_query=QUERY)
    state["task_plan"] = _chain()
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(state, llm=AsyncMock(), app_config=cfg)
    assert [t["status"] for t in out["task_plan"]] == ["completed", "completed"]
    note = [n for n in out["task_results"]["t2"]["dependency_notes"] if n["kind"] == NOTE_BRIDGE][0]
    assert note["counts"]["link"] == 2 and len(out["task_results"]["t2"]["query_results"]) == 2
