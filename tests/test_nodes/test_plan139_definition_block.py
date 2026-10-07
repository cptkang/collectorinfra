"""「테이블 용도」 블록 — 선별 테이블로만 SQL 생성 (plans/139 W5 · D-308 ⑤ · D-066).

여기서 고정하는 것:
- 공용 빌더 `build_table_purpose_block` — 좁힌 스키마 중 정의가 있는 테이블만 이름순으로
  「- 테이블: manages (주의: notes)」. 대표 컬럼·연결·성격·영역은 싣지 않는다. 정의가 없으면
  빈 문자열.
  중괄호·코드 펜스가 든 항목은 빼고 WARNING.
- 단일 `_build_system_prompt`·멀티 `_build_multi_system_prompt`의 **같은 자리**(스키마 텍스트 바로
  앞)에 **같은 내용**
- 정의 없는 DB는 바이트 불변 — 블록 외 차이 0 · 운영 프로필(폴스타 4종·test_db)은 빈 블록
  (itam은 plans/140 W3 직접 커밋 정의 보유)
- 예산 사다리 1·2단(재료·표본 제거) 뒤에도 블록 유지(단일·멀티)
- 표본 수집 대상 = 선별 테이블(단일·멀티) · 설명 재료도 선별 테이블 것만 렌더
- 선별 밖 테이블을 참조한 SQL → 「존재하지 않는 테이블 참조」 → 재생성(재선별 없음 · plans/139 §7)

시드: `testdata/itam_bench/closed/{itam_schema.json,table_definitions.yaml}`(G-7 — 실제 모양).
LLM·DB는 전부 가짜다(D-127 — 실 호출 없음).
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
import re
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from langgraph.graph import END, START, StateGraph

from src.config import AppConfig, Text2SQLConfig
from src.domain.table_definitions import (
    PROFILE_KEY,
    parse_import_document,
    validate_table_definitions,
)
from src.nodes.prompt_blocks import (
    TABLE_PURPOSE_HEADER,
    PromptBudgetExceeded,
    build_table_purpose_block,
)
from src.nodes.table_selection import definitions_of, narrow_schema_dict
from src.state import AgentState, create_initial_state

graph = importlib.import_module("src.graph")
mdb = importlib.import_module("src.nodes.multi_db_executor")
qg = importlib.import_module("src.nodes.query_generator")
qv = importlib.import_module("src.nodes.query_validator")
sa = importlib.import_module("src.nodes.schema_analyzer")

_ROOT = Path(__file__).resolve().parents[2]
_SEED_DIR = _ROOT / "testdata" / "itam_bench" / "closed"
_PROFILE_DIR = _ROOT / "config" / "db_profiles"

#: 시드 선별 — 주의(notes)가 있는 테이블 1개 포함 · 스키마 순서는 선별 순서
_PICKED = ("tcdmsif92", "tcdmsif94", "tcdmsif72")
_SAMPLE_VALUE = "sample-vm-name-0001"
_SELECTION_MARKER = "## 테이블 성격 안내"


# ──────────────────────────────────────────────
# 시드(G-7) · 가짜 LLM · 설정
# ──────────────────────────────────────────────


@pytest.fixture(scope="module")
def seed() -> tuple[dict, dict, list[str]]:
    """(스키마, 검증한 정의, 조회 대상 목록) — 테스트마다 deepcopy해서 쓴다."""
    raw = json.loads((_SEED_DIR / "itam_schema.json").read_text(encoding="utf-8"))
    schema = raw["schema"]
    doc = yaml.safe_load((_SEED_DIR / "table_definitions.yaml").read_text(encoding="utf-8"))
    columns = {t: [c["name"] for c in info["columns"]] for t, info in schema["tables"].items()}
    defs, errors = validate_table_definitions(parse_import_document(doc), columns)
    assert errors == {}
    allowed = [
        t for t, entry in doc["tables"].items()
        if not (isinstance(entry, dict) and entry.get("allowed") is False)
    ]
    return schema, defs, allowed


def _narrowed(seed: tuple, picked: tuple[str, ...] = _PICKED, *, with_defs: bool = True) -> dict:
    """시드 스키마를 선별 테이블로 좁히고 구조 메타(정의 포함 여부 선택)를 붙인다."""
    schema, defs, _allowed = seed
    narrowed = copy.deepcopy(narrow_schema_dict(schema, list(picked)))
    meta: dict[str, Any] = {"source": "manual"}
    if with_defs:
        meta[PROFILE_KEY] = copy.deepcopy(defs)
    return {**narrowed, "_structure_meta": meta}


def _with_samples(schema_info: dict) -> dict:
    for name, data in schema_info["tables"].items():
        first = data["columns"][0]["name"]
        data["sample_data"] = [{first: f"{_SAMPLE_VALUE}-{name}"}]
    return schema_info


def _desc_for(schema_info: dict, text: str) -> dict[str, str]:
    """선별 테이블마다 첫 컬럼에 설명 하나."""
    return {
        f"{name}.{data['columns'][0]['name']}": f"{text}-{name}"
        for name, data in schema_info["tables"].items()
    }


class _FakeLLM:
    """메시지 전체를 기록하고 정해 둔 응답을 차례로 돌려준다(마지막 응답은 반복)."""

    def __init__(self, *responses: str) -> None:
        self.calls: list[list[str]] = []
        self._responses = list(responses)

    async def ainvoke(self, messages: Any, **_kw: Any) -> Any:
        self.calls.append([m.content for m in messages])
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        return SimpleNamespace(content=response)


def _app_cfg() -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(schema_table_select_max=8)
    return cfg


def _single_prompt(schema_info: dict, **kwargs: Any) -> str:
    return qg._build_system_prompt(
        schema_info=schema_info, default_limit=1000, active_db_id="app_db",
        polestar_db_ids=set(), active_db_engine="mysql", **kwargs,
    )


async def _multi_prompt(schema_info: dict, app_config: Any = None) -> str:
    return await mdb._build_multi_system_prompt(
        schema_info, {"original_query": "가상 서버 IP 목록"}, "가상 서버 IP 목록", 1000,
        "mysql", "app_db", app_config,
    )


# ──────────────────────────────────────────────
# 공용 빌더
# ──────────────────────────────────────────────


class TestBuilder:
    def test_seed_block_lines(self, seed):
        _schema, defs, _allowed = seed
        block = build_table_purpose_block(_narrowed(seed))
        lines = block.rstrip("\n").splitlines()
        assert lines[0] == TABLE_PURPOSE_HEADER
        expected = []
        for table in sorted(_PICKED):
            line = f"- {table}: {defs[table]['manages']}"
            if defs[table].get("notes"):
                line += f" (주의: {defs[table]['notes']})"
            expected.append(line)
        assert lines[1:] == expected, "선별 테이블만 이름순 · manages + (주의: notes)"
        assert "(주의: " in block, "시드 선별에 주의가 있는 테이블이 있다"
        assert block.endswith("\n\n"), "스키마 텍스트와 빈 줄로 구분"

    def test_only_selected_definitions(self, seed):
        _schema, defs, _allowed = seed
        block = build_table_purpose_block(_narrowed(seed))
        others = [t for t in defs if t not in _PICKED]
        assert len(others) == len(defs) - len(_PICKED)
        for table in others:
            assert f"- {table}:" not in block, f"선별 밖 정의 미포함: {table}"

    def test_selection_only_fields_not_included(self, seed):
        _schema, defs, _allowed = seed
        block = build_table_purpose_block(_narrowed(seed))
        assert "대표 컬럼" not in block and "연결:" not in block
        assert "[현행]" not in block and "[매핑]" not in block, "성격(kind)은 선별용"
        assert "가상화서버참조ID" not in block, "key_columns·related는 싣지 않는다"
        assert "가상화" not in block.replace("가상 서버", ""), "업무 영역(group)도 싣지 않는다"

    @pytest.mark.parametrize("schema_info", [
        None,
        {},
        {"tables": {}},
        {"tables": {"t1": {}}},
        {"tables": {"t1": {}}, "_structure_meta": {"source": "manual"}},
        {"tables": {"t1": {}}, "_structure_meta": {PROFILE_KEY: {}}},
        {"tables": {"t1": {}}, "_structure_meta": {PROFILE_KEY: {"t2": {"manages": "다른 것"}}}},
        {"tables": {"t1": {}}, "_structure_meta": {PROFILE_KEY: {"t1": {"manages": "  "}}}},
        {"tables": {"t1": {}}, "_structure_meta": {PROFILE_KEY: {"t1": "문자열 항목"}}},
    ])
    def test_empty_when_no_usable_definition(self, schema_info):
        assert build_table_purpose_block(schema_info) == ""

    @pytest.mark.parametrize("meta", [["not", "a", "mapping"], {PROFILE_KEY: ["t1"]}])
    def test_reads_same_place_as_definitions_of(self, meta):
        """정의 위치는 선별 모듈의 `definitions_of`와 같다(모양이 어긋나면 둘 다 정의 없음)."""
        schema_info = {"tables": {"t1": {}}, "_structure_meta": meta}
        assert definitions_of(schema_info) is None
        assert build_table_purpose_block(schema_info) == ""

    def test_bare_name_match_uses_schema_key(self):
        block = build_table_purpose_block({
            "tables": {"ASSET.SERVERS": {}, "asset.apps": {}},
            "_structure_meta": {PROFILE_KEY: {
                "servers": {"manages": "서버 원장"}, "APPS": {"manages": "업무 목록"},
            }},
        })
        assert _items(block) == ["- ASSET.SERVERS: 서버 원장", "- asset.apps: 업무 목록"]

    def test_multiline_text_folded(self):
        block = build_table_purpose_block({
            "tables": {"t1": {}},
            "_structure_meta": {PROFILE_KEY: {
                "t1": {"manages": "서버\n- 가짜 줄", "notes": "  두 칸   공백 "},
            }},
        })
        assert _items(block) == ["- t1: 서버 - 가짜 줄 (주의: 두 칸 공백)"]

    def test_braces_and_fences_skipped_with_warning(self, caplog):
        schema_info = {
            "tables": {"t_brace": {}, "t_fence": {}, "t_ok": {}},
            "_structure_meta": {PROFILE_KEY: {
                "t_brace": {"manages": "값 {user_query} 주입"},
                "t_fence": {"manages": "정상", "notes": "```sql DROP```"},
                "t_ok": {"manages": "정상 항목"},
            }},
        }
        with caplog.at_level(logging.WARNING, logger="src.nodes.prompt_blocks"):
            block = build_table_purpose_block(schema_info)
        assert _items(block) == ["- t_ok: 정상 항목"]
        logs = [r.getMessage() for r in caplog.records if "[테이블용도]" in r.getMessage()]
        assert len(logs) == 1 and "t_brace" in logs[0] and "t_fence" in logs[0]

    def test_all_unsafe_gives_empty(self, caplog):
        schema_info = {
            "tables": {"t1": {}},
            "_structure_meta": {PROFILE_KEY: {"t1": {"manages": "}"}}},
        }
        with caplog.at_level(logging.WARNING, logger="src.nodes.prompt_blocks"):
            assert build_table_purpose_block(schema_info) == ""
        assert "[테이블용도]" in caplog.text


# ──────────────────────────────────────────────
# 같은 자리 · 같은 내용 · 정의 없는 DB 바이트 불변
# ──────────────────────────────────────────────


class TestSamePlace:
    async def test_single_and_multi_same_block_before_schema(self, seed):
        schema_info = _narrowed(seed)
        block = build_table_purpose_block(schema_info)
        assert block
        single = _single_prompt(schema_info)
        multi = await _multi_prompt(schema_info)
        for prompt in (single, multi):
            assert prompt.count(TABLE_PURPOSE_HEADER) == 1
            # 템플릿의 스키마 자리 바로 다음이 블록이고, 블록 바로 다음이 첫 선별 테이블 스키마다
            assert f"## DB 스키마\n\n{block}### {_PICKED[0]}\n" in prompt

    async def test_only_difference_is_the_block(self, seed):
        with_defs = _narrowed(seed)
        without = _narrowed(seed, with_defs=False)
        block = build_table_purpose_block(with_defs)
        assert build_table_purpose_block(without) == ""
        single_with, single_without = _single_prompt(with_defs), _single_prompt(without)
        multi_with, multi_without = await _multi_prompt(with_defs), await _multi_prompt(without)
        assert single_with.replace(block, "", 1) == single_without
        assert multi_with.replace(block, "", 1) == multi_without
        assert TABLE_PURPOSE_HEADER not in single_without + multi_without

    @pytest.mark.parametrize(
        "profile_path",
        # itam은 외부망 빌더가 정의를 직접 커밋한다(plans/140 W3 · D-311 ③) — 블록이 있는 쪽이 정상
        sorted(p for p in _PROFILE_DIR.glob("*.yaml") if p.stem != "itam"),
        ids=lambda p: p.stem,
    )
    def test_runtime_profiles_have_no_block(self, profile_path):
        """G-1 — 정의가 승인되지 않은 운영·로컬 프로필은 빈 블록(프롬프트 바이트 불변)."""
        profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        tables = {str(t): {} for t in profile.get("allowed_tables") or ["any_table"]}
        assert build_table_purpose_block({"tables": tables, "_structure_meta": profile}) == "", (
            f"{profile_path.name}에 테이블 정의가 생기면 이 DB의 생성 프롬프트가 바뀐다(G-1 재확인)"
        )


# ──────────────────────────────────────────────
# 예산 사다리 1·2단 뒤에도 블록 유지
# ──────────────────────────────────────────────


def _mock_cfg(budget: int) -> MagicMock:
    """검증 대상 필드만 명시한 설정 대역(.env 누수 차단)."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.query.max_retry_count = 3
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.text2sql.prompt_token_budget = budget
    cfg.get_polestar_db_ids.return_value = set()
    return cfg


def _gen_state(schema_info: dict, *, descriptions: bool) -> AgentState:
    state = create_initial_state(user_query="가상 서버 IP 목록")
    state["schema_info"] = schema_info
    state["relevant_tables"] = list(schema_info["tables"])
    state["parsed_requirements"] = {
        "query_targets": ["가상 서버"], "filter_conditions": [], "time_range": None,
        "output_format": "text", "aggregation": None, "limit": None,
        "original_query": "가상 서버 IP 목록",
    }
    if descriptions:
        state["column_descriptions"] = _desc_for(schema_info, "설명재료")
    return state


async def _generate(state: AgentState, budget: int) -> tuple[Any, dict]:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="```sql\nSELECT 1 FROM tcdmsif92 LIMIT 10;\n```",
    ))
    out = await qg.query_generator(state, llm=llm, app_config=_mock_cfg(budget))
    return llm, out


def _sent_system(llm: Any) -> str:
    return llm.ainvoke.await_args.args[0][0].content


class TestLadderKeepsBlock:
    async def test_single_stage2_samples_removed_block_kept(self, seed):
        block = build_table_purpose_block(_narrowed(seed))
        _probe_llm, probe = await _generate(
            _gen_state(_with_samples(_narrowed(seed)), descriptions=True), 1,
        )
        assert probe["prompt_budget"]["stage"] == "exceeded"
        floor = probe["prompt_budget"]["estimated_tokens"]  # 재료·표본 없는 크기(블록 포함)
        llm, out = await _generate(
            _gen_state(_with_samples(_narrowed(seed)), descriptions=True), floor,
        )
        assert out["prompt_budget"]["stage"] == "samples"
        system = _sent_system(llm)
        assert _SAMPLE_VALUE not in system and "설명재료" not in system
        assert f"## DB 스키마\n\n{block}### " in system, "2단 뒤에도 같은 자리에 블록"

    async def test_single_stage1_materials_removed_block_kept(self, seed):
        block = build_table_purpose_block(_narrowed(seed))
        _probe_llm, probe = await _generate(_gen_state(_narrowed(seed), descriptions=True), 1)
        floor = probe["prompt_budget"]["estimated_tokens"]
        llm, out = await _generate(_gen_state(_narrowed(seed), descriptions=True), floor)
        assert out["prompt_budget"]["stage"] == "materials"
        system = _sent_system(llm)
        assert "설명재료" not in system
        assert f"## DB 스키마\n\n{block}### " in system

    async def test_single_within_budget_includes_block_in_estimate(self, seed):
        llm, out = await _generate(_gen_state(_narrowed(seed), descriptions=True), 10**9)
        assert out["prompt_budget"]["stage"] == "within"
        system = _sent_system(llm)
        assert TABLE_PURPOSE_HEADER in system and "설명재료" in system

    async def test_multi_stage1_and_stage2_keep_block(self, seed):
        block = build_table_purpose_block(_narrowed(seed))
        materials = {
            "column_descriptions": _desc_for(_narrowed(seed), "설명재료"),
            "column_synonyms": {}, "resource_type_synonyms": {}, "eav_name_synonyms": {},
        }

        def cfg(budget: int) -> SimpleNamespace:
            return SimpleNamespace(text2sql=SimpleNamespace(prompt_token_budget=budget))

        load_materials = AsyncMock(return_value=materials)
        with patch.object(mdb, "_load_schema_prompt_materials", load_materials), \
             patch.object(mdb, "_select_query_history_examples", AsyncMock(return_value=None)):
            with pytest.raises(PromptBudgetExceeded) as exc:
                await _multi_prompt(_with_samples(_narrowed(seed)), cfg(1))
            floor = int(re.search(r"추정 (\d+)", str(exc.value)).group(1))
            stage2 = await _multi_prompt(_with_samples(_narrowed(seed)), cfg(floor))
            stage1 = await _multi_prompt(_narrowed(seed), cfg(floor))
        assert _SAMPLE_VALUE not in stage2 and "설명재료" not in stage2
        assert "설명재료" not in stage1
        for prompt in (stage1, stage2):
            assert f"## DB 스키마\n\n{block}### " in prompt


# ──────────────────────────────────────────────
# 표본·설명 재료 = 선별 테이블
# ──────────────────────────────────────────────


def _seed_profile(seed: tuple) -> dict:
    _schema, defs, allowed = seed
    return {
        "source": "manual", "allowed_tables": list(allowed),
        PROFILE_KEY: copy.deepcopy(defs),
    }


def _cache_mgr(schema: dict) -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_schema_or_fetch.return_value = (schema, True, "메모리", {}, {})
    mgr.get_db_description.return_value = "자산 관리 DB"
    mgr.get_synonyms.return_value = {}
    mgr.redis_available = False
    return mgr


def _sample_client() -> AsyncMock:
    client = AsyncMock()
    client.get_sample_data = AsyncMock(return_value=[{"c": _SAMPLE_VALUE}])
    return client


def _sampled_tables(client: AsyncMock) -> list[str]:
    return [c.args[0] for c in client.get_sample_data.await_args_list]


def _items(block: str) -> list[str]:
    """블록의 항목 줄(머리말·끝 빈 줄 제외)."""
    return block.rstrip("\n").splitlines()[1:]


def _picks(*names: str) -> str:
    return json.dumps({"tables": list(names)}, ensure_ascii=False)


class TestMaterialsFollowSelection:
    async def test_single_samples_only_selected(self, seed):
        schema, _defs, _allowed = seed
        client = _sample_client()

        @asynccontextmanager
        async def _ctx(_client):
            yield client

        state = dict(create_initial_state(user_query="가상 서버 IP 목록"))
        state["active_db_id"] = "app_db"
        state["parsed_requirements"] = {
            "query_targets": ["가상 서버"], "original_query": "가상 서버 IP 목록",
        }
        llm = _FakeLLM(_picks(*_PICKED))
        with patch.object(sa, "get_db_client", return_value=_ctx(None)), \
             patch.object(sa, "get_cache_manager",
                          return_value=_cache_mgr(copy.deepcopy(schema))), \
             patch.object(sa, "_load_manual_profile", return_value=_seed_profile(seed)):
            out = await sa.schema_analyzer(state, llm=llm, app_config=_app_cfg())
        assert out["table_selection"]["app_db"]["selected"] == list(_PICKED)
        assert sorted(_sampled_tables(client)) == sorted(_PICKED), "표본 수집 = 선별 테이블"
        assert list(out["schema_info"]["tables"]) == list(_PICKED)

        # 설명 재료는 DB 전체 사전이어도 렌더는 선별 테이블 것만(좁힌 스키마 기준 조회)
        outsider = "tcdmsif89"
        descs = _desc_for(out["schema_info"], "선별설명")
        first = schema["tables"][outsider]["columns"][0]["name"]
        descs[f"{outsider}.{first}"] = "선별밖설명"
        prompt = _single_prompt(out["schema_info"], column_descriptions=descs)
        assert "선별설명" in prompt and "선별밖설명" not in prompt
        assert f"### {outsider}\n" not in prompt

    async def test_multi_samples_only_selected(self, seed):
        schema, _defs, _allowed = seed
        client = _sample_client()
        sink: dict = {}
        with patch("src.schema_cache.cache_manager.get_cache_manager",
                   return_value=_cache_mgr(copy.deepcopy(schema))), \
             patch.object(sa, "_load_manual_profile", return_value=_seed_profile(seed)):
            out = await mdb._analyze_schema(
                client, {"original_query": "가상 서버 IP 목록"}, db_id="app_db",
                app_config=_app_cfg(), sub_query_context="가상 서버 IP 목록",
                llm=_FakeLLM(_picks(*_PICKED)), selection_sink=sink,
            )
        assert sink["app_db"]["selected"] == list(_PICKED)
        assert sorted(_sampled_tables(client)) == sorted(_PICKED), "표본 백필 = 선별 테이블"
        assert list(out["tables"]) == list(_PICKED)
        assert out["_structure_meta"][PROFILE_KEY], "좁힌 사본에도 정의가 실린다"
        assert TABLE_PURPOSE_HEADER in await _multi_prompt(out)


# ──────────────────────────────────────────────
# 선별 밖 테이블 참조 → 검증 실패 → 재생성(재선별 없음)
# ──────────────────────────────────────────────

_TABLES = {
    "app.alpha": ["a_id", "host_name"],
    "app.beta": ["b_id", "sw_name"],
    "app.gamma": ["g_id", "note"],
}
_DEFS = {
    "alpha": {"kind": "현행", "manages": "서버 원장 — 호스트명", "origin": "import"},
    "beta": {"kind": "현행", "manages": "설치 소프트웨어", "origin": "import"},
    "gamma": {"kind": "로그", "manages": "작업 기록", "origin": "import"},
}
_BAD_SQL = "SELECT sw_name FROM app.beta LIMIT 10"
_GOOD_SQL = "SELECT host_name FROM app.alpha LIMIT 10"


class TestOutOfSelectionRegenerates:
    async def test_validation_fails_then_regenerates_without_reselection(self):
        schema = {
            "tables": {
                t: {"columns": [{"name": c, "type": "varchar"} for c in cols]}
                for t, cols in _TABLES.items()
            },
            "relationships": [],
        }
        profile = {
            "source": "manual", "allowed_tables": ["alpha", "beta", "gamma"],
            PROFILE_KEY: copy.deepcopy(_DEFS),
        }
        llm = _FakeLLM(
            _picks("alpha"), f"```sql\n{_BAD_SQL}\n```", f"```sql\n{_GOOD_SQL}\n```",
        )
        cfg = _app_cfg()
        executed: list[str] = []

        async def _executor(state):
            executed.append(state.get("generated_sql"))
            return {"error_message": None, "query_results": []}

        g = StateGraph(AgentState)
        g.add_node("schema_analyzer", partial(sa.schema_analyzer, llm=llm, app_config=cfg))
        g.add_node("query_generator", partial(qg.query_generator, llm=llm, app_config=cfg))
        g.add_node("query_validator", partial(qv.query_validator, app_config=cfg))
        g.add_node("query_executor", _executor)
        g.add_node("error_response", graph._error_response_node)
        g.add_edge(START, "schema_analyzer")
        g.add_edge("schema_analyzer", "query_generator")
        g.add_edge("query_generator", "query_validator")
        g.add_conditional_edges(
            "query_validator", partial(graph.route_after_validation, max_retry=3),
            {"query_executor": "query_executor", "query_generator": "query_generator",
             "error_response": "error_response"},
        )
        g.add_edge("query_executor", END)
        g.add_edge("error_response", END)
        app = g.compile()

        @asynccontextmanager
        async def _ctx(_client):
            yield _sample_client()

        state = dict(create_initial_state(user_query="서버 호스트명 목록"))
        state["active_db_id"] = "app_db"
        state["parsed_requirements"] = {
            "query_targets": ["서버"], "original_query": "서버 호스트명 목록",
        }
        with patch.object(sa, "get_db_client", return_value=_ctx(None)), \
             patch.object(sa, "get_cache_manager", return_value=_cache_mgr(schema)), \
             patch.object(sa, "_load_manual_profile", return_value=profile):
            out = await app.ainvoke(state)

        selection_calls = [c for c in llm.calls if _SELECTION_MARKER in c[0]]
        sql_calls = [c for c in llm.calls if _SELECTION_MARKER not in c[0]]
        assert len(selection_calls) == 1, "재선별 없음 — 선별 호출은 처음 1회뿐"
        assert len(sql_calls) == 2, "선별 밖 참조는 재생성 1회로 돌아온다"
        for system, *_rest in sql_calls:
            assert "- app.alpha: 서버 원장 — 호스트명" in system
            assert "- app.beta:" not in system and "### app.beta" not in system
        assert "존재하지 않는 테이블 참조" in "\n".join(sql_calls[1]), (
            "재생성 프롬프트에 검증 사유가 실린다"
        )
        assert executed == [_GOOD_SQL]
        assert out["retry_count"] == 1
        assert out["table_selection"]["app_db"]["selected"] == ["app.alpha"]
