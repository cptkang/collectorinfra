"""단계적 도출 진입 분기·4경로 대칭 주입 테스트 (Plan 67 Phase S2 / D-128, D-066).

실 LLM 호출 없음(D-127) — 결정적 목만 사용한다.

검증 축:
    1. 플래그 OFF — 1방 SMQ 프롬프트가 **바이트 동일**(sha256 대조)하고 루프에 진입하지 않는다.
       app_config를 안 넘긴 기존 호출 형태도 동일하다.
    2. 플래그 ON — 루프 산출 SMQ가 기존 검증 경로(normalize_smq → check_coverage → compile_smq)를
       그대로 통과하고, 관측 레코드에 커버리지 판정이 기록된다.
    3. 미해결·형식 오류 — 구조화 사유를 담은 CoverageResult로 폴백한다(침묵 폴백 금지).
    4. 4경로 대칭 — 그래프 단일 / orchestration 인라인 / 멀티 DB / deepagents 도구 **각 실제
       호출 체인**이 루프를 발동시킨다.
"""

from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from src.nodes import column_deriver as cd
from src.nodes import multi_db_executor as mdb
from src.nodes.query_generator import query_generator
from src.nodes.semantic_compiler import compile_from_nl, load_semantic_model, render_catalog
from src.orchestration import subagents as sa
from src.prompts.semantic_compiler import (
    SEMANTIC_SMQ_SYSTEM_TEMPLATE,
    SEMANTIC_SMQ_USER_TEMPLATE,
)

_DB_ID = "polestar_cm_gp"
_QUERY = "서버별 OS종류와 벤더를 조회해줘"

_ONE_SHOT_SMQ = json.dumps({
    "pattern": "A", "resource_types": ["server.Server"],
    "dimensions": ["OSType", "Vendor"],
}, ensure_ascii=False)

_DECOMPOSED = json.dumps({"fields": [{"field": "OS종류", "role_hint": "dimension"}]})
_FINAL_SMQ = json.dumps({
    "smq": {"pattern": "A", "resource_types": ["server.Server"],
            "dimensions": ["OSType", "Vendor"]},
    "fields": [{"field": "OS종류", "role": "dimension", "selection": "OSType",
                "evidence": "search_catalog", "confidence": 0.95}],
    "unresolved": [],
}, ensure_ascii=False)

_DERIVED_RECORD = {
    "path": "", "db_id": "", "smq": {"pattern": "A", "dimensions": ["OSType"]},
    "fields": [], "unresolved": [], "rounds": 1, "tool_calls": 1, "llm_calls": 2,
    "elapsed_ms": 1.0, "stopped_reason": cd.STOP_COMPLETED, "covered": None,
}


def _cfg(*, stepwise: bool) -> MagicMock:
    """검증 대상 플래그만 명시한 설정 대역(.env 누수 차단)."""
    cfg = MagicMock()
    cfg.query.default_limit = 100
    cfg.get_polestar_db_ids.return_value = {_DB_ID}
    cfg.multi_db.get_active_db_ids.return_value = {_DB_ID}
    cfg.orchestrator.max_tool_result_tokens = 1000     # deepagents 도구 결과 상한(경로 D)
    cfg.synonym.value_retrieval = False
    cfg.synonym.fuzzy_match = False
    cfg.synonym.match_confidence_min = 0.85
    cfg.text2sql.semantic_compose = True
    cfg.text2sql.semantic_fallback = "llm"
    cfg.text2sql.fallback_confidence_min = 0.0
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.complexity_gate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.text2sql.query_history_fewshot = False
    cfg.text2sql.stepwise_derivation = stepwise
    cfg.text2sql.stepwise_max_rounds = 4
    cfg.text2sql.stepwise_max_tool_calls = 8
    cfg.text2sql.stepwise_timeout_seconds = 10.0
    # MagicMock 속성은 자동 생성되어 truthy하다 — 상위어 확장(N4)이 이 경로에서 조용히
    # 켜지지 않도록 명시 OFF로 못 박는다.
    cfg.text2sql.hypernym_ambiguity = False
    return cfg


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _messages_sha(messages: list) -> str:
    """메시지 목록(타입 + 본문)의 sha256 — 프롬프트 바이트 동일성 대조용."""
    joined = "\n\x00\n".join(
        f"{type(m).__name__}:{getattr(m, 'content', '')}" for m in messages
    )
    return _sha(joined)


class _CapturingLLM:
    """1방 경로 목 — 호출된 메시지를 기록하고 고정 SMQ를 돌려준다."""

    def __init__(self, content: str = _ONE_SHOT_SMQ):
        self._content = content
        self.calls: list[list] = []

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        return AIMessage(content=self._content)


class _LoopLLM:
    """루프 경로 목 — [분해, 최종 SMQ] 순서로 응답한다(도구 미사용 시퀀스)."""

    def __init__(self, final: str = _FINAL_SMQ):
        self._responses = [AIMessage(content=_DECOMPOSED), AIMessage(content=final)]
        self.calls: list[list] = []

    def bind_tools(self, tools):
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        assert self._responses, "목 응답 소진"
        return self._responses.pop(0)


# ──────────────────────────────────────────────
# 1. 플래그 OFF — 회귀 0
# ──────────────────────────────────────────────

async def test_flag_off_prompt_is_byte_identical(monkeypatch):
    """플래그 OFF면 1방 프롬프트가 템플릿 렌더 결과와 바이트 동일하고 루프에 진입하지 않는다."""
    async def _must_not_run(*args, **kwargs):
        raise AssertionError("플래그 OFF인데 도출 루프가 실행됐다")

    monkeypatch.setattr(cd, "derive_smq", _must_not_run)

    model = load_semantic_model(_DB_ID)
    expected = [
        SEMANTIC_SMQ_SYSTEM_TEMPLATE.format(catalog=render_catalog(model)),
        SEMANTIC_SMQ_USER_TEMPLATE.format(user_query=_QUERY),
    ]

    llm = _CapturingLLM()
    sql, smq, cov = await compile_from_nl(
        llm, _QUERY, _DB_ID, app_config=_cfg(stepwise=False),
    )

    assert sql and sql.strip().upper().startswith("SELECT")
    assert smq.pattern == "A" and cov.covered
    sent = llm.calls[0]
    assert [type(m).__name__ for m in sent] == ["SystemMessage", "HumanMessage"]
    assert _sha(sent[0].content) == _sha(expected[0])
    assert _sha(sent[1].content) == _sha(expected[1])


async def test_flag_off_matches_legacy_call_signature():
    """app_config를 넘기지 않는 기존 호출 형태와 프롬프트·산출물이 동일하다."""
    legacy_llm = _CapturingLLM()
    legacy_sql, _, _ = await compile_from_nl(legacy_llm, _QUERY, _DB_ID)

    off_llm = _CapturingLLM()
    off_sql, _, _ = await compile_from_nl(
        off_llm, _QUERY, _DB_ID, app_config=_cfg(stepwise=False),
    )

    assert _messages_sha(legacy_llm.calls[0]) == _messages_sha(off_llm.calls[0])
    assert legacy_sql == off_sql


async def test_flag_off_leaves_state_field_empty():
    """플래그 OFF면 query_generator가 smq_derivation을 None으로 둔다(요청 스코프 자기정리)."""
    result = await query_generator(_single_state(), llm=_CapturingLLM(), app_config=_cfg(stepwise=False))
    assert result["smq_derivation"] is None


# ──────────────────────────────────────────────
# 2. 플래그 ON — 기존 결정적 경로 통과
# ──────────────────────────────────────────────

async def test_flag_on_uses_loop_and_compiles_deterministically():
    """루프 산출 SMQ가 커버리지 판정·결정적 컴파일을 그대로 통과한다."""
    llm = _LoopLLM()
    sink: list[dict] = []
    sql, smq, cov = await compile_from_nl(
        llm, _QUERY, _DB_ID,
        app_config=_cfg(stepwise=True),
        stepwise_deps=cd.StepwiseDeps(path="single"),
        derivation_sink=sink,
    )

    assert sql and sql.strip().upper().startswith("SELECT")
    assert smq.pattern == "A" and set(smq.dimensions) == {"OSType", "Vendor"}
    assert cov.covered
    assert len(sink) == 1
    assert sink[0]["stopped_reason"] == cd.STOP_COMPLETED
    assert sink[0]["covered"] is True
    assert sink[0]["path"] == "single"
    # 1방 프롬프트(카탈로그 전문)는 쓰이지 않았다.
    assert all(
        "[카탈로그]" not in getattr(m, "content", "")
        for call in llm.calls for m in call
    )


async def test_loop_output_passes_normalize_smq():
    """루프 산출물도 기존 교정(normalize_smq)을 통과한다(새 검증 경로 신설 금지 — D-067)."""
    final = json.dumps({
        "smq": {"pattern": "A", "dimensions": ["PHYSICALCORE"]},
        "fields": [], "unresolved": [],
    })
    sink: list[dict] = []
    _sql, smq, _cov = await compile_from_nl(
        _LoopLLM(final), "CPU 용량 조회해줘", _DB_ID,
        app_config=_cfg(stepwise=True), derivation_sink=sink,
    )
    # '물리' 명시가 없으면 LOGICALCORE로 치환된다(기존 결정적 교정).
    assert [str(d).upper() for d in smq.dimensions] == ["LOGICALCORE"]


async def test_coverage_outside_is_stamped_in_record():
    """커버리지 밖이면 레코드에 covered=False가 남고 폴백 사유가 실린다."""
    final = json.dumps({
        "smq": {"pattern": "A", "dimensions": ["OSParameter"]},
        "fields": [], "unresolved": [],
    })
    sink: list[dict] = []
    sql, smq, cov = await compile_from_nl(
        _LoopLLM(final), "OS파라미터 조회", _DB_ID,
        app_config=_cfg(stepwise=True), derivation_sink=sink,
    )
    assert sql is None and smq is not None and not cov.covered
    assert sink[0]["covered"] is False


# ──────────────────────────────────────────────
# 3. 미해결·형식 오류 → 구조화 사유 폴백
# ──────────────────────────────────────────────

async def test_unresolved_field_falls_back_with_structured_reason():
    """미해결 필드가 있으면 사유를 담아 폴백한다(침묵 폴백 금지)."""
    final = json.dumps({
        "smq": {"pattern": "A", "dimensions": ["OSType"]},
        "fields": [],
        "unresolved": [{"field": "유사 사양", "reason": "카탈로그 미대응"}],
    }, ensure_ascii=False)
    sink: list[dict] = []
    sql, smq, cov = await compile_from_nl(
        _LoopLLM(final), _QUERY, _DB_ID,
        app_config=_cfg(stepwise=True), derivation_sink=sink,
    )

    assert sql is None and smq is None
    assert not cov.covered
    assert "단계적 도출 미완" in cov.reason
    assert "유사 사양" in cov.reason and "카탈로그 미대응" in cov.reason
    assert sink[0]["unresolved"]


async def test_malformed_smq_falls_back_with_reason():
    """SMQ 스키마 불일치도 사유와 함께 폴백한다."""
    final = json.dumps({
        "smq": {"pattern": "Z", "dimensions": []}, "fields": [], "unresolved": [],
    })
    sink: list[dict] = []
    sql, _smq, cov = await compile_from_nl(
        _LoopLLM(final), _QUERY, _DB_ID,
        app_config=_cfg(stepwise=True), derivation_sink=sink,
    )

    assert sql is None and not cov.covered
    assert "형식 오류" in cov.reason
    assert sink[0]["stopped_reason"] == "schema_error"


async def test_timeout_falls_back_with_reason():
    """루프 타임아웃도 사유와 함께 폴백한다(예외 전파 없음)."""
    import asyncio

    class _SlowLLM(_LoopLLM):
        async def ainvoke(self, messages):
            await asyncio.sleep(0.05)
            return await super().ainvoke(messages)

    cfg = _cfg(stepwise=True)
    cfg.text2sql.stepwise_timeout_seconds = 0.01
    sink: list[dict] = []
    sql, smq, cov = await compile_from_nl(
        _SlowLLM(), _QUERY, _DB_ID, app_config=cfg, derivation_sink=sink,
    )

    assert sql is None and smq is None
    assert "타임아웃" in cov.reason
    assert sink[0]["stopped_reason"] == cd.STOP_TIMEOUT


# ──────────────────────────────────────────────
# 4. 4경로 대칭 주입 (D-066)
# ──────────────────────────────────────────────

def _single_state() -> dict:
    return {
        "user_query": _QUERY,
        "schema_info": {"tables": {"cmm_resource": {"columns": []}}},
        "parsed_requirements": {"original_query": _QUERY, "query_targets": ["os"]},
        "active_db_id": _DB_ID,
        "active_db_engine": "postgresql",
        "column_descriptions": {},
        "column_synonyms": {"cmm_resource.name": ["서버명"]},
        "retry_count": 0,
    }


@pytest.fixture
def derive_recorder(monkeypatch):
    """derive_smq 호출을 기록하는 결정적 대역(경로 라벨·db_id·도구 재료 관측)."""
    calls: list[dict] = []

    async def _fake_derive(llm, user_query, db_id, model, *, deps=None, limits=None):
        calls.append({
            "path": getattr(deps, "path", None),
            "db_id": db_id,
            "user_query": user_query,
            "has_synonyms": bool(getattr(deps, "synonyms", None)),
            "max_rounds": getattr(limits, "max_rounds", None),
            "timeout": getattr(limits, "timeout_seconds", None),
        })
        return {**_DERIVED_RECORD, "path": getattr(deps, "path", ""), "db_id": db_id}

    monkeypatch.setattr(cd, "derive_smq", _fake_derive)
    return calls


@pytest.fixture
def stub_cache_manager(monkeypatch):
    """멀티 DB 경로가 읽는 유사어 캐시를 대역화한다(Redis 접근 차단)."""
    from src.schema_cache import cache_manager as cm

    class _Mgr:
        async def get_synonyms(self, db_id):
            return {"cmm_resource.name": ["서버명"]}

    monkeypatch.setattr(cm, "get_cache_manager", lambda cfg: _Mgr())


@pytest.fixture
def stub_pipeline_nodes(monkeypatch):
    """orchestration 경로의 주변 노드를 대역화한다(schema/validate/execute/organize)."""
    async def _schema(state, **kwargs):
        return {"schema_info": state.get("schema_info") or {"tables": {}}}

    async def _validate(state, **kwargs):
        return {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None}}

    async def _execute(state, **kwargs):
        return {"query_results": [{"OSType": "Linux"}], "error_message": None}

    async def _organize(state, **kwargs):
        return {"organized_data": {"summary": "", "rows": state.get("query_results") or []}}

    monkeypatch.setattr(sa, "schema_analyzer", _schema)
    monkeypatch.setattr(sa, "query_validator", _validate)
    monkeypatch.setattr(sa, "query_executor", _execute)
    monkeypatch.setattr(sa, "result_organizer", _organize)


async def test_path_a_graph_single_db(derive_recorder):
    """경로 A(그래프 단일 DB): query_generator가 루프를 발동한다."""
    result = await query_generator(
        _single_state(), llm=_CapturingLLM(), app_config=_cfg(stepwise=True),
    )

    assert [c["path"] for c in derive_recorder] == ["single"]
    assert derive_recorder[0]["db_id"] == _DB_ID
    assert derive_recorder[0]["has_synonyms"] is True     # state 유사어가 주입됐다
    assert derive_recorder[0]["max_rounds"] == 4          # 설정 상한이 전달됐다
    assert result["generated_sql"].strip().upper().startswith("SELECT")
    assert result["smq_derivation"] and result["smq_derivation"][0]["path"] == "single"


async def test_path_b_orchestration_inline(derive_recorder, stub_pipeline_nodes):
    """경로 B(orchestration 인라인): _run_single_db_pipeline도 같은 함수를 지난다."""
    state = await sa._run_single_db_pipeline(
        _single_state(), _CapturingLLM(), _cfg(stepwise=True),
    )

    assert [c["path"] for c in derive_recorder] == ["single"]
    assert state["generated_sql"].strip().upper().startswith("SELECT")
    assert state["smq_derivation"][0]["stopped_reason"] == cd.STOP_COMPLETED


async def test_path_c_multi_db_executor(derive_recorder, stub_cache_manager):
    """경로 C(멀티 DB): _generate_sql이 multi_db 라벨로 루프를 발동한다."""
    sink: list[dict] = []
    sql = await mdb._generate_sql(
        _CapturingLLM(), {"original_query": _QUERY},
        {"tables": {"cmm_resource": {"columns": []}}},
        _QUERY, 100, db_id=_DB_ID, app_config=_cfg(stepwise=True),
        derivation_sink=sink,
    )

    assert [c["path"] for c in derive_recorder] == ["multi_db"]
    assert derive_recorder[0]["has_synonyms"] is True     # 캐시 유사어가 주입됐다(대칭)
    assert sql.strip().upper().startswith("SELECT")
    assert sink and sink[0]["path"] == "multi_db"


async def test_path_d_deepagents_tool(derive_recorder, stub_pipeline_nodes, monkeypatch):
    """경로 D(deepagents 도구): 도구 → subagent handler → 단일 파이프라인까지 발동한다."""
    from src.orchestration import deepagents_tools as dt

    async def _classify(llm, sub_query, app_config):
        return [{
            "db_id": _DB_ID, "relevance_score": 1.0,
            "sub_query_context": sub_query, "user_specified": False,
            "reason": "테스트 고정",
        }]

    monkeypatch.setattr(sa, "classify_dbs", _classify)

    out = await dt._run_subagent_tool(
        "data_query", _QUERY,
        worker_llm=_CapturingLLM(), app_config=_cfg(stepwise=True),
        ambient_state={"user_query": _QUERY},
    )

    assert [c["path"] for c in derive_recorder] == ["single"]
    assert derive_recorder[0]["db_id"] == _DB_ID
    assert isinstance(out, str)


async def test_all_four_paths_are_symmetric(
    derive_recorder, stub_pipeline_nodes, stub_cache_manager, monkeypatch
):
    """4경로를 연달아 실행해 전부 발동함을 한 번에 단언한다(비대칭 회귀 차단)."""
    from src.orchestration import deepagents_tools as dt

    async def _classify(llm, sub_query, app_config):
        return [{
            "db_id": _DB_ID, "relevance_score": 1.0, "sub_query_context": sub_query,
            "user_specified": False, "reason": "테스트 고정",
        }]

    monkeypatch.setattr(sa, "classify_dbs", _classify)
    cfg = _cfg(stepwise=True)

    await query_generator(_single_state(), llm=_CapturingLLM(), app_config=cfg)
    await sa._run_single_db_pipeline(_single_state(), _CapturingLLM(), cfg)
    await mdb._generate_sql(
        _CapturingLLM(), {"original_query": _QUERY},
        {"tables": {}}, _QUERY, 100, db_id=_DB_ID, app_config=cfg,
    )
    await dt._run_subagent_tool(
        "data_query", _QUERY, worker_llm=_CapturingLLM(), app_config=cfg,
        ambient_state={"user_query": _QUERY},
    )

    assert [c["path"] for c in derive_recorder] == [
        "single", "single", "multi_db", "single",
    ]


async def test_flag_off_blocks_all_four_paths(
    stub_pipeline_nodes, stub_cache_manager, monkeypatch
):
    """플래그 OFF면 4경로 어디서도 루프가 발동하지 않는다."""
    from src.orchestration import deepagents_tools as dt

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("플래그 OFF인데 도출 루프가 실행됐다")

    async def _classify(llm, sub_query, app_config):
        return [{
            "db_id": _DB_ID, "relevance_score": 1.0, "sub_query_context": sub_query,
            "user_specified": False, "reason": "테스트 고정",
        }]

    monkeypatch.setattr(cd, "derive_smq", _must_not_run)
    monkeypatch.setattr(sa, "classify_dbs", _classify)
    cfg = _cfg(stepwise=False)

    await query_generator(_single_state(), llm=_CapturingLLM(), app_config=cfg)
    await sa._run_single_db_pipeline(_single_state(), _CapturingLLM(), cfg)
    await mdb._generate_sql(
        _CapturingLLM(), {"original_query": _QUERY},
        {"tables": {}}, _QUERY, 100, db_id=_DB_ID, app_config=cfg,
    )
    await dt._run_subagent_tool(
        "data_query", _QUERY, worker_llm=_CapturingLLM(), app_config=cfg,
        ambient_state={"user_query": _QUERY},
    )


# ──────────────────────────────────────────────
# 5. N2 few-shot 대칭 (D-133 — S2 공유 헬퍼 편승분)
# ──────────────────────────────────────────────

async def test_multi_db_path_uses_history_fewshot(monkeypatch):
    """멀티 DB 경로도 이력 few-shot을 주입한다(단일 경로와 같은 공용 헬퍼)."""
    from src.schema_cache import query_history as qh

    async def _hit(db_id, user_query, *, top_k, min_score, store=None):
        assert db_id == _DB_ID
        return [{"query": "이력 질의", "sql": "SELECT hostname FROM cmm_resource", "score": 0.9}]

    monkeypatch.setattr(qh, "search_query_history", _hit)

    cfg = _cfg(stepwise=False)
    cfg.text2sql.semantic_compose = False       # 폴백(LLM 1방) 프롬프트 경로로 진입
    cfg.text2sql.query_history_fewshot = True
    cfg.text2sql.query_history_top_k = 2
    cfg.text2sql.query_history_min_score = 0.35

    llm = _CapturingLLM("SELECT hostname FROM cmm_resource")
    await mdb._generate_sql(
        llm, {"original_query": _QUERY},
        {"tables": {"cmm_resource": {"columns": [{"name": "hostname", "type": "varchar"}]}},
         "_structure_meta": {"query_guide": "가이드", "patterns": [],
                             "query_examples": [{"question": "프로필 고정 예시",
                                                 "sql": "SELECT 1"}]}},
        _QUERY, 100, db_id=_DB_ID, app_config=cfg,
    )

    system_prompt = llm.calls[0][0].content
    assert "이력 질의" in system_prompt
    assert "프로필 고정 예시" not in system_prompt


async def test_multi_db_path_keeps_fixed_fewshot_when_flag_off(monkeypatch):
    """플래그 OFF면 멀티 DB 경로도 고정 few-shot 그대로다(회귀 0)."""
    from src.schema_cache import query_history as qh

    async def _fail(*args, **kwargs):
        raise AssertionError("플래그 OFF인데 이력 검색이 호출됐다")

    monkeypatch.setattr(qh, "search_query_history", _fail)

    cfg = _cfg(stepwise=False)
    cfg.text2sql.semantic_compose = False
    cfg.text2sql.query_history_fewshot = False

    llm = _CapturingLLM("SELECT hostname FROM cmm_resource")
    await mdb._generate_sql(
        llm, {"original_query": _QUERY},
        {"tables": {"cmm_resource": {"columns": [{"name": "hostname", "type": "varchar"}]}},
         "_structure_meta": {"query_guide": "가이드", "patterns": [],
                             "query_examples": [{"question": "프로필 고정 예시",
                                                 "sql": "SELECT 1"}]}},
        _QUERY, 100, db_id=_DB_ID, app_config=cfg,
    )

    assert "프로필 고정 예시" in llm.calls[0][0].content
