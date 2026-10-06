"""정의 기반 LLM 테이블 선별 — 단일·멀티 공용 (plans/138 W4 · D-305 G-1~G-3).

여기서 고정하는 것:
- 후보 = 스키마 ∩ `allowed_tables` · 이름순 프롬프트 · 정의 없는 후보는 앞쪽 컬럼 10개
- 선별 3개 → relevant 3개(강제 보충·EAV 보충·유사어 보완 없음) · Q-5 미적용
- 후보 밖·없는 이름 제거 · 다리 테이블(승인 관계 우선 → 정의 related) · 상한 K(LLM 순서 · 다리가 뒤)
- 실패(예외 · FabriX 오류 응답 · 엉뚱한 이름) → 어휘 대체 → 0개면 SQL 생성 LLM 0회로 안내 종결
  (단일 그래프 · 2단 단일 루프 · 멀티)
- 알람 의도·정의 없는 DB는 종전 경로 그대로 · 같은 입력이면 단일·멀티 같은 선별
- 상태 `table_selection` = ``{db_id: {mode, source, candidates, selected, bridged}}`` · 요청 스코프
- 멀티 토큰 한도 중단의 종결 사유 = `backend_limit`(W1 잔여)

시드: `testdata/itam_bench/closed/{itam_schema.json,table_definitions.yaml}`(G-7 — 실제 모양).
LLM·DB는 전부 가짜다(D-127 — 실 호출 없음).
"""

from __future__ import annotations

import copy
import importlib
import json
import logging
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
from src.domain.table_definitions import parse_import_document, validate_table_definitions
from src.nodes import table_selection as ts
from src.state import AgentState, create_followup_input, create_initial_state

graph = importlib.import_module("src.graph")
mdb = importlib.import_module("src.nodes.multi_db_executor")
qg = importlib.import_module("src.nodes.query_generator")
qv = importlib.import_module("src.nodes.query_validator")
sa = importlib.import_module("src.nodes.schema_analyzer")
sub = importlib.import_module("src.orchestration.subagents")
replanner = importlib.import_module("src.orchestration.replanner")

_ROOT = Path(__file__).resolve().parents[2]
_SEED_DIR = _ROOT / "testdata" / "itam_bench" / "closed"

FABRIX_TEXT = (
    "An exception occurred in GptOssAdapter.llm_call: Input tokens must be <= 95232. "
    "Given: 96858. Please reduce the length of the messages. Error occurred from orchestrator"
)


# ──────────────────────────────────────────────
# 가짜 LLM · 설정 · 합성 스키마
# ──────────────────────────────────────────────


class _FakeLLM:
    """프롬프트를 기록하고 정해 둔 응답(또는 예외)을 차례로 돌려준다(마지막 응답은 반복)."""

    def __init__(self, *responses: Any) -> None:
        self.prompts: list[str] = []
        self._responses = list(responses)

    async def ainvoke(self, messages: Any, **_kw: Any) -> Any:
        self.prompts.append(messages[0].content)
        response = self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=response)


def _cfg(k: int = 8, *, skip: bool = False) -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(
        schema_table_select_max=k, schema_table_select_skip_enabled=skip,
    )
    return cfg


def _ns_cfg(k: int = 8) -> SimpleNamespace:
    return SimpleNamespace(text2sql=SimpleNamespace(schema_table_select_max=k))


_MISC_COLUMNS = [f"c{i:02d}" for i in range(12)]

#: 합성 스키마 — 정의 4개(alpha·beta·gamma·delta) + 정의 없는 misc + 허용 밖 hidden
_TABLES: dict[str, list[str]] = {
    "app.alpha": ["a_id", "host_name", "ip_addr"],
    "app.beta": ["b_id", "sw_name", "sw_ver"],
    "app.gamma": ["a_id", "b_id"],
    "app.delta": ["a_id", "b_id", "note"],
    "app.misc": _MISC_COLUMNS,
    "app.hidden": ["h_id"],
}

_DEFS: dict[str, dict[str, Any]] = {
    "alpha": {
        "group": "서버", "kind": "현행", "manages": "서버 원장 — 호스트명·IP",
        "key_columns": ["host_name", "ip_addr"], "related": {"delta": "a_id"},
        "origin": "import",
    },
    "beta": {
        "group": "소프트웨어", "kind": "현행", "manages": "설치 소프트웨어 — 이름·버전",
        "key_columns": ["sw_name"], "related": {"delta": "b_id"}, "origin": "import",
    },
    "gamma": {
        "group": "서버", "kind": "매핑", "manages": "서버와 소프트웨어 연결", "origin": "import",
    },
    "delta": {"group": "서버", "kind": "수집이력", "manages": "연결 이력", "origin": "import"},
}

_APPROVED_GAMMA = [
    {"from": "alpha.a_id", "to": "gamma.a_id"},
    {"from": "gamma.b_id", "to": "beta.b_id"},
]


def _schema_dict(tables: dict[str, list[str]] = _TABLES, relationships: Any = ()) -> dict:
    return {
        "tables": {
            t: {"columns": [{"name": c, "type": "varchar"} for c in cols]}
            for t, cols in tables.items()
        },
        "relationships": list(relationships),
    }


def _profile(*, relationships: Any = None, defs: Any = None) -> dict:
    prof: dict[str, Any] = {
        "source": "manual",
        "allowed_tables": ["alpha", "beta", "gamma", "delta", "misc"],
        "table_definitions": copy.deepcopy(_DEFS if defs is None else defs),
    }
    if relationships is not None:
        prof["relationships"] = list(relationships)
    return prof


async def _select(llm: Any, *, profile: dict | None = None, k: int = 8,
                  question: str = "서버 소프트웨어 목록", sub_ctx: str | None = None,
                  relationships: Any = ()) -> ts.TableSelection:
    return await ts.select_tables(
        llm=llm, question=question, sub_query_context=sub_ctx, db_id="app_db",
        db_description="자산 관리 DB", schema_tables=_schema_dict()["tables"],
        profile=profile or _profile(), app_config=_ns_cfg(k), relationships=list(relationships),
    )


def _picks(*names: str) -> str:
    return json.dumps({"tables": list(names)}, ensure_ascii=False)


# ──────────────────────────────────────────────
# 시드(G-7) — 실제 모양
# ──────────────────────────────────────────────


def _seed() -> tuple[dict, dict, str]:
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
    profile = {"source": "manual", "allowed_tables": allowed, "table_definitions": defs}
    return schema, profile, raw.get("_db_description") or ""


class TestSeedShape:
    async def test_three_picked_three_selected_no_supplement(self):
        schema, profile, desc = _seed()
        llm = _FakeLLM(_picks("tcdmsif92", "tcdmsif94", "tcdmsif89"))
        sel = await ts.select_tables(
            llm=llm, question="가상 서버별 IP 주소 목록", sub_query_context=None, db_id="itam",
            db_description=desc, schema_tables=schema["tables"], profile=profile,
            app_config=_ns_cfg(), relationships=schema["relationships"],
        )
        assert sel.source == "llm"
        assert sel.candidates == 99, "허용 목록 밖 9개는 후보가 아니다"
        assert list(sel.selected) == ["tcdmsif92", "tcdmsif94", "tcdmsif89"]
        assert sel.bridged == ()

    async def test_related_bridge_on_seed(self):
        schema, profile, desc = _seed()
        llm = _FakeLLM(_picks("tcdmsif94", "tcdmsif89"))
        sel = await ts.select_tables(
            llm=llm, question="가상 호스트별 VM IP", sub_query_context=None, db_id="itam",
            db_description=desc, schema_tables=schema["tables"], profile=profile,
            app_config=_ns_cfg(), relationships=schema["relationships"],
        )
        assert list(sel.selected) == ["tcdmsif94", "tcdmsif89", "tcdmsif92"]
        assert sel.bridged == ("tcdmsif92",)

    async def test_lexical_fallback_on_seed(self, caplog):
        schema, profile, desc = _seed()
        llm = _FakeLLM(RuntimeError("connection reset"))
        with caplog.at_level(logging.WARNING, logger="src.nodes.table_selection"):
            sel = await ts.select_tables(
                llm=llm, question="가상 서버 IP 주소 목록", sub_query_context=None, db_id="itam",
                db_description=desc, schema_tables=schema["tables"], profile=profile,
                app_config=_ns_cfg(), relationships=schema["relationships"],
            )
        assert sel.source == "lexical"
        assert 0 < len(sel.selected) <= 8
        assert sel.selected[0] == "tcdmsif94"
        assert "[테이블선별] 폴백" in caplog.text

    async def test_prompt_lists_candidates_by_name(self):
        schema, profile, desc = _seed()
        llm = _FakeLLM(_picks("tcdmsif72"))
        await ts.select_tables(
            llm=llm, question="서버 목록", sub_query_context=None, db_id="itam",
            db_description=desc, schema_tables=schema["tables"], profile=profile,
            app_config=_ns_cfg(), relationships=schema["relationships"],
        )
        lines = [ln for ln in llm.prompts[0].splitlines() if ln.startswith("- tcdms")]
        names = [ln[2:].split(" ", 1)[0] for ln in lines]
        assert len(names) == 99 and names == sorted(names)
        assert desc in llm.prompts[0]


# ──────────────────────────────────────────────
# 공용 선별 — 결정적 후처리
# ──────────────────────────────────────────────


class TestSelectTables:
    async def test_out_of_candidates_and_unknown_removed(self):
        sel = await _select(_FakeLLM(_picks("app.alpha", "hidden", "nonexistent", "BETA")))
        assert sel.source == "llm"
        assert sel.candidates == 5
        # 맨 이름 비교 · 스키마 원래 키로 돌려준다
        assert list(sel.selected) == ["app.alpha", "app.beta", "app.delta"]
        assert sel.bridged == ("app.delta",)

    async def test_comma_list_accepted(self):
        sel = await _select(_FakeLLM("app.alpha, app.beta\n- misc"))
        assert list(sel.selected)[:3] == ["app.alpha", "app.beta", "app.misc"]

    async def test_bridge_prefers_approved_relationship(self):
        sel = await _select(
            _FakeLLM(_picks("alpha", "beta")), profile=_profile(relationships=_APPROVED_GAMMA),
        )
        assert sel.bridged == ("app.gamma",), "승인 관계가 있으면 related(delta)를 쓰지 않는다"
        assert list(sel.selected) == ["app.alpha", "app.beta", "app.gamma"]

    async def test_bridge_from_declared_schema_relationship(self):
        sel = await _select(_FakeLLM(_picks("alpha", "beta")), relationships=[
            {"from": "app.alpha.a_id", "to": "app.gamma.a_id"},
            {"from": "app.gamma.b_id", "to": "app.beta.b_id"},
        ])
        assert sel.bridged == ("app.gamma",)

    async def test_bridge_falls_back_to_related_without_approved_path(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.nodes.table_selection"):
            sel = await _select(_FakeLLM(_picks("alpha", "beta")))
        assert sel.bridged == ("app.delta",)
        assert "미검증" in caplog.text, "related 근거를 로그로 구분한다"

    async def test_no_bridge_when_directly_connected_or_via_selected(self):
        direct = await _select(
            _FakeLLM(_picks("alpha", "beta")),
            profile=_profile(relationships=[{"from": "alpha.a_id", "to": "beta.b_id"}]),
        )
        assert direct.bridged == ()
        via_selected = await _select(_FakeLLM(_picks("alpha", "beta", "delta")))
        assert via_selected.bridged == ()

    async def test_cap_truncates_in_llm_order_and_bridges_last(self, caplog):
        with caplog.at_level(logging.INFO, logger="src.nodes.table_selection"):
            sel = await _select(_FakeLLM(_picks("misc", "beta", "alpha", "gamma")), k=2)
        assert list(sel.selected) == ["app.misc", "app.beta"]
        assert "상한 2개 초과" in caplog.text
        no_room = await _select(_FakeLLM(_picks("alpha", "beta")), k=2)
        assert list(no_room.selected) == ["app.alpha", "app.beta"] and no_room.bridged == ()
        room = await _select(_FakeLLM(_picks("alpha", "beta")), k=3)
        assert room.bridged == ("app.delta",)

    @pytest.mark.parametrize("response", [
        RuntimeError("boom"),
        RuntimeError(FABRIX_TEXT),
        FABRIX_TEXT,
        _picks("nope", "hidden"),
        "모르겠습니다",
    ])
    async def test_failure_goes_lexical(self, response, caplog):
        with caplog.at_level(logging.WARNING, logger="src.nodes.table_selection"):
            sel = await _select(_FakeLLM(response), question="서버 호스트명 목록")
        assert sel.source == "lexical"
        assert sel.selected[0] == "app.alpha"
        assert sel.bridged == ()
        assert "[테이블선별] 폴백" in caplog.text
        assert "orchestrator" not in caplog.text, "백엔드 원문은 로그 사유에 싣지 않는다"

    async def test_lexical_zero_is_none(self):
        sel = await _select(_FakeLLM(RuntimeError("x")), question="zzqq")
        assert (sel.source, sel.selected, sel.candidates) == ("none", (), 5)

    async def test_no_candidates_is_none_without_llm(self):
        llm = _FakeLLM(_picks("alpha"))
        prof = _profile()
        prof["allowed_tables"] = ["not_there"]
        sel = await _select(llm, profile=prof)
        assert sel.source == "none" and sel.candidates == 0
        assert llm.prompts == []

    async def test_prompt_lines(self):
        llm = _FakeLLM(_picks("alpha"))
        await _select(llm)
        prompt = llm.prompts[0]
        assert (
            "- app.alpha [현행] 서버 원장 — 호스트명·IP | 대표 컬럼: host_name, ip_addr"
            " | 연결: delta"
        ) in prompt
        assert f"- app.misc [{', '.join(_MISC_COLUMNS[:10])}]" in prompt
        assert "c10" not in prompt, "정의 없는 후보는 앞쪽 컬럼 10개만"
        assert "app.hidden" not in prompt
        assert "최대 8개" in prompt and "수집적재" in prompt
        assert prompt.rstrip().endswith('{"tables": ["테이블 이름"]}')

    async def test_sub_query_context_appended_only_when_different(self):
        same = _FakeLLM(_picks("alpha"))
        await _select(same, question="서버 목록", sub_ctx="서버 목록")
        plain = _FakeLLM(_picks("alpha"))
        await _select(plain, question="서버 목록", sub_ctx=None)
        assert same.prompts == plain.prompts
        other = _FakeLLM(_picks("alpha"))
        await _select(other, question="서버 목록", sub_ctx="김포 서버만")
        assert "(이 DB가 맡은 조회: 김포 서버만)" in other.prompts[0]


class TestGuidanceAndHelpers:
    def test_guidance_examples_by_group_count(self):
        text = ts.selection_none_guidance(_DEFS)
        assert text == (
            "질문에 맞는 조회 대상 테이블을 찾지 못했습니다 — 무엇을 찾는지 알려 주세요"
            "(예: 서버, 소프트웨어)"
        )

    def test_guidance_without_groups(self):
        assert ts.selection_none_guidance({"t": {"manages": "x"}}) == ts.SELECTION_NONE_MESSAGE
        assert ts.selection_none_guidance(None) == ts.SELECTION_NONE_MESSAGE

    def test_trigger(self):
        assert ts.uses_definition_selection(_profile(), None)
        assert not ts.uses_definition_selection(_profile(), "alarm_query")
        assert not ts.uses_definition_selection({"source": "manual"}, None)
        assert not ts.uses_definition_selection(None, None)

    def test_select_max_from_config_and_default(self):
        assert ts.select_max(_cfg(5)) == 5
        assert ts.select_max(MagicMock()) == ts.DEFAULT_SELECT_MAX
        assert Text2SQLConfig().schema_table_select_max == 8

    def test_narrow_is_shallow_copy(self):
        schema = _schema_dict(relationships=[
            {"from": "app.alpha.a_id", "to": "app.gamma.a_id"},
        ])
        narrowed = ts.narrow_schema_dict(schema, ["app.gamma", "app.alpha"])
        assert list(narrowed["tables"]) == ["app.gamma", "app.alpha"]
        assert len(schema["tables"]) == len(_TABLES), "원본(캐시 공유 객체)은 그대로"


# ──────────────────────────────────────────────
# 단일 경로 — schema_analyzer
# ──────────────────────────────────────────────


def _single_state(question: str, *, intent: str | None = None, db_id: str = "app_db") -> dict:
    state = dict(create_initial_state(user_query=question))
    state["active_db_id"] = db_id
    state["routing_intent"] = intent
    state["parsed_requirements"] = {"query_targets": ["서버"], "original_query": question}
    return state


def _cache_mgr(schema: dict, description: str | None = "자산 관리 DB") -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_schema_or_fetch.return_value = (schema, True, "메모리", {"app.alpha.a_id": "ID"}, {})
    mgr.get_db_description.return_value = description
    mgr.get_synonyms.return_value = {}
    mgr.redis_available = False
    return mgr


async def _run_single(state: dict, *, llm: Any, profile: dict | None, cfg: AppConfig,
                      schema: dict | None = None) -> dict:
    @asynccontextmanager
    async def _ctx(client):
        yield client

    mgr = _cache_mgr(copy.deepcopy(schema or _schema_dict()))
    with patch.object(sa, "get_db_client", return_value=_ctx(AsyncMock())), \
         patch.object(sa, "get_cache_manager", return_value=mgr), \
         patch.object(sa, "_load_manual_profile", return_value=copy.deepcopy(profile)):
        return await sa.schema_analyzer(state, llm=llm, app_config=cfg)


class TestSingleSchemaAnalyzer:
    async def test_selection_is_relevant_without_supplements(self):
        llm = _FakeLLM(_picks("alpha", "beta", "misc"))
        syn = AsyncMock(return_value={"hidden"})
        eav = MagicMock(side_effect=AssertionError("EAV 보충은 정의 모드에서 건너뛴다"))
        with patch.object(sa, "_query_synonym_tables", syn), \
             patch.object(sa, "_supplement_eav_tables", eav):
            out = await _run_single(
                _single_state("서버 소프트웨어 목록"), llm=llm,
                profile=_profile(relationships=[{"from": "alpha.a_id", "to": "beta.b_id"}]),
                cfg=_cfg(skip=True),
            )
        assert out["relevant_tables"] == ["app.alpha", "app.beta", "app.misc"], "강제 보충 없음"
        assert list(out["schema_info"]["tables"]) == ["app.alpha", "app.beta", "app.misc"]
        assert out["table_selection"] == {"app_db": {
            "mode": "definitions", "source": "llm", "candidates": 5,
            "selected": ["app.alpha", "app.beta", "app.misc"], "bridged": [],
        }}
        assert len(llm.prompts) == 1 and "## 테이블 성격 안내" in llm.prompts[0]
        syn.assert_not_awaited()  # Q-5 생략·유사어 동적 보완 미적용
        assert out["error_message"] is None

    async def test_existing_selection_entries_are_kept(self):
        state = _single_state("서버 목록")
        state["table_selection"] = {"other_db": {"source": "llm"}}
        out = await _run_single(
            state, llm=_FakeLLM(_picks("alpha")), profile=_profile(), cfg=_cfg(),
        )
        assert set(out["table_selection"]) == {"other_db", "app_db"}

    async def test_alarm_intent_keeps_legacy_path(self):
        llm = _FakeLLM("app.alpha, app.beta")
        out = await _run_single(
            _single_state("알람 목록", intent="alarm_query"), llm=llm, profile=_profile(),
            cfg=_cfg(),
        )
        assert "table_selection" not in out
        assert "## 테이블 성격 안내" not in llm.prompts[0]

    async def test_profile_without_definitions_keeps_legacy_path(self):
        prof = _profile()
        del prof["table_definitions"]
        llm = _FakeLLM("app.alpha")
        out = await _run_single(_single_state("서버 목록"), llm=llm, profile=prof, cfg=_cfg())
        assert "table_selection" not in out
        # 종전 경로 — 허용 목록 강제 보충이 그대로 일어난다
        assert set(out["relevant_tables"]) == {
            "app.alpha", "app.beta", "app.gamma", "app.delta", "app.misc",
        }

    async def test_none_selection_gives_empty_relevant(self):
        out = await _run_single(
            _single_state("zzqq"), llm=_FakeLLM(RuntimeError("x")), profile=_profile(),
            cfg=_cfg(),
        )
        assert out["relevant_tables"] == []
        assert out["table_selection"]["app_db"]["source"] == "none"
        assert out["schema_info"]["_structure_meta"]["table_definitions"]


# ──────────────────────────────────────────────
# 0개 안내 종결 — 단일 그래프 · 2단 단일 루프 · 멀티
# ──────────────────────────────────────────────

_GUIDANCE = ts.selection_none_guidance(_DEFS)


def _none_state() -> AgentState:
    state = create_initial_state(user_query="zzqq")
    state["active_db_id"] = "app_db"
    state["parsed_requirements"] = {"query_targets": [], "original_query": "zzqq"}
    state["schema_info"] = {
        "tables": {}, "relationships": [],
        "_structure_meta": {"source": "manual", "table_definitions": copy.deepcopy(_DEFS)},
    }
    state["table_selection"] = {"app_db": ts.TableSelection("definitions", "none", 5).as_state()}
    return state


class TestSingleGraphTermination:
    async def test_schema_to_guidance_without_sql_llm(self):
        llm = _FakeLLM(RuntimeError("selection down"))
        cfg = _cfg()
        executed: list[Any] = []

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
        async def _ctx(client):
            yield client

        state = _single_state("zzqq")
        with patch.object(sa, "get_db_client", return_value=_ctx(AsyncMock())), \
             patch.object(sa, "get_cache_manager", return_value=_cache_mgr(_schema_dict())), \
             patch.object(sa, "_load_manual_profile", return_value=_profile()):
            out = await app.ainvoke(state)
        assert len(llm.prompts) == 1, "선별 호출 1회뿐 — SQL 생성 LLM 0회"
        assert executed == []
        assert out["final_response"] == _GUIDANCE
        assert out["validation_result"]["selection_none"] is True
        assert out["retry_count"] == 0

    def test_route_functions_stop_immediately(self):
        state = _none_state()
        state["validation_result"] = {
            "passed": False, "reason": _GUIDANCE, "auto_fixed_sql": None, "non_sql": False,
            "selection_none": True,
        }
        state["retry_count"] = 0
        assert graph.route_after_validation(state) == "error_response"
        assert graph.route_after_validation_with_approval(state) == "error_response"

    async def test_generator_and_validator_short_circuit(self):
        llm = _FakeLLM("SELECT 1")
        state = _none_state()
        gen = await qg.query_generator(state, llm=llm, app_config=MagicMock())
        assert gen["generated_sql"] == "" and llm.prompts == []
        state.update(gen)
        val = await qv.query_validator(state, app_config=MagicMock())
        assert val["validation_result"]["passed"] is False
        assert val["validation_result"]["selection_none"] is True
        assert val["error_message"] == _GUIDANCE
        assert qv.selection_none_hit(val["validation_result"])

    async def test_other_db_selection_does_not_stop(self):
        state = _none_state()
        state["active_db_id"] = "plain_db"
        assert not ts.selection_none_active(state)


def _sub_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        query=SimpleNamespace(max_retry_count=3, default_limit=1000),
        get_polestar_db_ids=lambda: set(),
        server=SimpleNamespace(answer_reserve_sec=0.0),
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: ["app_db"], zone_group_exclusive=True,
        ),
        polestar_rest=SimpleNamespace(realtime_usage_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        text2sql=SimpleNamespace(multi_full_validation=False, alarm_deterministic=False),
    )


def _patch_tier2(monkeypatch) -> dict[str, int]:
    calls = {"exec": 0}
    none_state = _none_state()

    async def _schema_node(state, llm=None, app_config=None):
        key = state.get("active_db_id") or "_default"
        return {
            "schema_info": none_state["schema_info"],
            "relevant_tables": [],
            "table_selection": {key: ts.TableSelection("definitions", "none", 5).as_state()},
        }

    async def _exec(state, app_config=None):
        calls["exec"] += 1
        return {"error_message": None, "query_results": []}

    async def _organize(state, llm=None, app_config=None):
        return {"organized_data": {"rows": [], "summary": ""}, "error_message": None}

    monkeypatch.setattr(sub, "schema_analyzer", _schema_node)
    monkeypatch.setattr(sub, "query_executor", _exec)
    monkeypatch.setattr(sub, "result_organizer", _organize)
    return calls


def _isolated() -> dict:
    return {
        "user_query": "zzqq", "original_user_query": "zzqq", "active_db_id": "app_db",
        "parsed_requirements": {"original_query": "zzqq", "query_targets": []},
        "conversation_context": None, "allowed_db_ids": None, "user_role": None,
        "selected_db_ids": None, "zone_selection_db_ids": None,
        "realtime_usage_intent": False, "zone_clarification_allowed": False,
        "retry_count": 0, "error_message": None,
        "validation_result": {"passed": False, "reason": "", "auto_fixed_sql": None},
        "organized_data": {"rows": [], "summary": ""}, "query_results": [],
        "generated_sql": "", "request_deadline": None,
    }


class TestTier2SingleLoop:
    async def test_selection_none_stops_with_guidance(self, monkeypatch):
        calls = _patch_tier2(monkeypatch)
        llm = _FakeLLM("SELECT 1")
        out = await sub._run_single_db_pipeline(_isolated(), llm, _sub_cfg())
        assert llm.prompts == [], "SQL 생성 LLM 0회"
        assert calls["exec"] == 0
        assert out["regen_stop"] == {"reason": "selection_none", "detail": _GUIDANCE}
        assert out["regen_stop_response"] == _GUIDANCE
        assert out["table_selection"]["app_db"]["source"] == "none"

    async def test_task_result_carries_guidance(self, monkeypatch):
        _patch_tier2(monkeypatch)
        task = {"task_id": "t1", "agent": "data_query", "sub_query": "zzqq",
                "db_ids": ["app_db"]}
        res = await sub.run_data_query_pipeline(
            task, _isolated(), llm=_FakeLLM("SELECT 1"), app_config=_sub_cfg(),
        )
        assert res["final_response"] == _GUIDANCE
        assert res["regen_stop"]["reason"] == "selection_none"

    def test_replanner_notice_wording(self):
        text = replanner._REGEN_STOP_REASON_TEXT["selection_none"]
        notice = replanner._regen_stop_notice([
            ({"sub_query": "zzqq"}, {"reason": "selection_none", "detail": _GUIDANCE}),
        ])
        assert text in notice
        assert "대신" not in notice


# ──────────────────────────────────────────────
# 멀티 경로
# ──────────────────────────────────────────────


async def _run_multi_analyze(*, llm: Any, profile: dict | None, question: str,
                             sub_ctx: str = "", intent: str | None = None, k: int = 8,
                             sink: dict | None = None, cached: dict | None = None) -> dict:
    mgr = _cache_mgr(cached if cached is not None else _schema_dict())
    client = AsyncMock()
    client.get_sample_data.return_value = []
    with patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr), \
         patch.object(sa, "_load_manual_profile", return_value=copy.deepcopy(profile)):
        return await mdb._analyze_schema(
            client, {"original_query": question}, db_id="app_db", app_config=_cfg(k),
            sub_query_context=sub_ctx, routing_intent=intent, llm=llm, selection_sink=sink,
        )


class TestMultiAnalyzeSchema:
    async def test_narrows_to_selection_without_mutating_cache(self):
        cached = _schema_dict()
        sink: dict = {}
        out = await _run_multi_analyze(
            llm=_FakeLLM(_picks("alpha", "beta")), profile=_profile(), question="서버 소프트웨어",
            sink=sink, cached=cached,
        )
        assert list(out["tables"]) == ["app.alpha", "app.beta", "app.delta"]
        assert sink == {"app_db": {
            "mode": "definitions", "source": "llm", "candidates": 5,
            "selected": ["app.alpha", "app.beta", "app.delta"], "bridged": ["app.delta"],
        }}
        assert len(cached["tables"]) == len(_TABLES), "캐시 공유 객체는 그대로"
        assert out["_structure_meta"]["table_definitions"]

    async def test_without_definitions_uses_gate(self):
        prof = _profile()
        del prof["table_definitions"]
        llm = _FakeLLM(_picks("alpha"))
        sink: dict = {}
        out = await _run_multi_analyze(llm=llm, profile=prof, question="서버", sink=sink)
        assert llm.prompts == [] and sink == {}
        assert "app.alpha" in out["tables"]

    async def test_alarm_intent_uses_gate(self):
        llm = _FakeLLM(_picks("alpha"))
        sink: dict = {}
        await _run_multi_analyze(llm=llm, profile=_profile(), question="알람", intent="alarm_query",
                                 sink=sink)
        assert llm.prompts == [] and sink == {}

    async def test_same_input_same_selection_as_single(self):
        single_llm = _FakeLLM(_picks("beta", "alpha", "misc"))
        single = await _run_single(
            _single_state("서버 소프트웨어 목록"), llm=single_llm, profile=_profile(), cfg=_cfg(),
        )
        multi_llm = _FakeLLM(_picks("beta", "alpha", "misc"))
        sink: dict = {}
        multi = await _run_multi_analyze(
            llm=multi_llm, profile=_profile(), question="서버 소프트웨어 목록",
            sub_ctx="서버 소프트웨어 목록", sink=sink,
        )
        assert single_llm.prompts == multi_llm.prompts
        assert single["table_selection"]["app_db"] == sink["app_db"]
        assert list(single["schema_info"]["tables"]) == list(multi["tables"])


class _Registry:
    def __init__(self, cfg: Any) -> None:
        pass

    def is_registered(self, db_id: str) -> bool:
        return True

    def get_client(self, db_id: str) -> Any:
        @asynccontextmanager
        async def _ctx():
            client = AsyncMock()
            client.execute_sql.return_value = SimpleNamespace(
                rows=[{"hostname": "a"}], row_count=1,
            )
            yield client

        return _ctx()


class TestMultiExecutor:
    async def test_none_db_fails_with_guidance_and_others_run(self, monkeypatch):
        generated: list[str] = []

        async def _schema(client, parsed, *, db_id, app_config, selection_sink=None, llm=None,
                          **_kw):
            if db_id == "defs_db":
                selection_sink[db_id] = ts.TableSelection("definitions", "none", 5).as_state()
                return {"tables": {}, "relationships": [],
                        "_structure_meta": {"table_definitions": copy.deepcopy(_DEFS)}}
            return {"tables": {"servers": {"columns": [{"name": "hostname", "type": "text"}]}},
                    "_structure_meta": {"source": "manual"}}

        async def _generate(llm, parsed, schema_info, sub_context, limit, **kwargs):
            generated.append(kwargs.get("db_id"))
            return "SELECT hostname FROM servers"

        monkeypatch.setattr(mdb, "DBRegistry", _Registry)
        monkeypatch.setattr(mdb, "_analyze_schema", _schema)
        monkeypatch.setattr(mdb, "_generate_sql", _generate)
        monkeypatch.setattr(mdb, "_validate_sql_simple", lambda *_a, **_k: None)
        monkeypatch.setattr(mdb, "get_domain_by_id", lambda _d: None)
        monkeypatch.setattr(mdb, "log_query_execution", AsyncMock())

        state = create_initial_state(user_query="서버 목록")
        state["target_databases"] = [{"db_id": "defs_db"}, {"db_id": "plain_db"}]
        out = await mdb.multi_db_executor(
            state, llm=AsyncMock(),
            app_config=SimpleNamespace(
                query=SimpleNamespace(default_limit=100),
                text2sql=SimpleNamespace(multi_full_validation=False),
            ),
        )
        assert generated == ["plain_db"], "선별 0개 DB는 SQL을 만들지 않는다"
        assert out["db_errors"]["defs_db"] == _GUIDANCE
        assert out["regen_stops"]["defs_db"] == {"reason": "selection_none", "detail": _GUIDANCE}
        assert out["table_selection"] == {"defs_db": {
            "mode": "definitions", "source": "none", "candidates": 5, "selected": [], "bridged": [],
        }}
        assert "plain_db" in out["db_results"]

    async def test_token_limit_stop_reason_is_backend_limit(self, monkeypatch):
        calls: list[int] = []

        async def fake_generate(llm, parsed, schema_info, sub_context, limit, **kwargs):
            calls.append(1)
            return FABRIX_TEXT

        monkeypatch.setattr(mdb, "_generate_sql", fake_generate)
        run = SimpleNamespace(
            llm=AsyncMock(), parsed_requirements={}, effective_limit=100,
            unmapped_fields=None,
            app_config=SimpleNamespace(
                query=SimpleNamespace(default_limit=100),
                text2sql=SimpleNamespace(multi_full_validation=False, prompt_token_budget=0),
            ),
            mc_candidates=[], mc_derivations=[], prior_block=None, prior_scope=None,
            value_index=None, form_context="", form_fill_out=None, form_intent=False,
            mapping_sources=None, form_fill_answers=None,
            state={"user_query": "자산 목록"}, regen_stops={},
        )
        _sql, error = await mdb._generate_validated_sql(
            run, AsyncMock(), {"tables": {}}, "ctx", {}, db_engine="postgresql", db_id="gp",
        )
        assert len(calls) == 1
        assert run.regen_stops["gp"]["reason"] == "backend_limit"
        assert run.regen_stops["gp"]["detail"] == error


# ──────────────────────────────────────────────
# 요청 스코프 상태
# ──────────────────────────────────────────────


class TestRequestScope:
    def test_initialized_in_both_state_builders(self):
        assert create_initial_state(user_query="q")["table_selection"] is None
        assert create_followup_input("q")["table_selection"] is None
