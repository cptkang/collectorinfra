"""프로필 규칙·코드값 블록을 선별 테이블로 좁힘 (plans/151 W2 · F2 · G-2 (a) · D-066).

여기서 고정하는 것:
- `build_profile_rules_block(meta, tables)` — 주어진 테이블(맨 이름 · 대소문자 무시 · 스키마 한정자
  무시)의 규칙 줄·코드값 키만 싣는다. 코드값은 키 순서와 무관하게 실리고(61번째 이후 키 포함) 60칸
  상한은 좁힌 뒤에 적용한다. 백틱 컬럼 참조로 테이블을 읽지 못한 규칙 줄·점 없는 키는 남는다.
- `tables=None`은 종전 그대로(전량 · 키 순서 앞 60칸).
- `profile_rule_tables` — 선별 출처가 `llm`·`lexical`일 때만 `selected`(다리 포함), 그 밖은 None.
- 단일(`_build_fallback_prompts`)·멀티(`_generate_validated_sql`) 호출부가 상태의 선별 결과에서 같은
  테이블을 넘기고, 같은 입력이면 두 프롬프트에 같은 블록이 실린다.

LLM·DB는 쓰지 않는다(D-127).
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.config import AppConfig
from src.nodes.prompt_blocks import build_profile_rules_block, profile_rule_tables

mdb = importlib.import_module("src.nodes.multi_db_executor")
qg = importlib.import_module("src.nodes.query_generator")

#: 키 순서상 앞을 채우는 다른 테이블 코드값 칸 수(종전 상한 60을 넘긴다)
_FILLER = 70
_SELECTED = ["tcdmsif72", "tcdmsif80"]

_RULE_OTHER = "`tcdmsam01.사용여부`는 플래그 값 'Y', 'N' 중 하나다."
_RULE_72 = "`tcdmsif72.등록일자`는 'YYYYMMDD' 형식의 문자열 날짜다."
_RULE_MULTI = "현행 원장의 활성 여부 칸: `tcdmsif51.활성화여부`, `tcdmsif72.활성화여부`."
_RULE_NO_REF = "조회는 조회 대상으로 등록된 테이블만 쓴다."
_RULE_TABLE_ONLY = "수집 적재 테이블은 조회에 고르지 않는다: `tcdmsam05`, `tcdmsif53`."
_RULE_OTHER_MULTI = "`tcdmsam02.상태`·`tcdmsif51.상태`는 대소문자 표기가 섞여 있다."


def _meta() -> dict[str, Any]:
    """앞 70칸이 다른 테이블(`tcdmsam*`)이고 선별 테이블 칸이 뒤에 오는 구조 메타."""
    code_values: dict[str, Any] = {
        f"tcdmsam{i:02d}.상태구분": ["A", "B"] for i in range(1, _FILLER + 1)
    }
    code_values["tcdmsif72.활성화여부"] = ["1", "0"]
    code_values["tcdmsif80.자산상태구분"] = ["01", "02"]
    code_values["tcdmsif92.활성화여부"] = ["1", "0"]
    code_values["점없는키"] = ["X"]
    return {
        "query_rules": [
            _RULE_OTHER, _RULE_72, _RULE_MULTI, _RULE_NO_REF, _RULE_TABLE_ONLY, _RULE_OTHER_MULTI,
        ],
        "code_values": code_values,
        "code_labels": {
            "tcdmsif72.활성화여부": {"1": "활성", "0": "비활성"},
            "tcdmsif80.자산상태구분": {"01": "운영"},
        },
    }


def _rules_section(block: str) -> str:
    return block.split("### DB 쿼리 규칙\n", 1)[1].split("\n\n### 코드값", 1)[0]


def _codes_section(block: str) -> str:
    return block.split("### 코드값 (컬럼: 값=의미)\n", 1)[1]


# ──────────────────────────────────────────────
# 공용 빌더
# ──────────────────────────────────────────────


class TestNarrowedBlock:
    def test_selected_codes_load_regardless_of_key_order(self):
        """선별 테이블 칸은 키 순서 61번째 이후라도 실린다 — 종전(None)은 앞 60칸에서 끊긴다."""
        meta = _meta()
        assert list(meta["code_values"]).index("tcdmsif72.활성화여부") >= 60
        narrowed = _codes_section(build_profile_rules_block(meta, _SELECTED))
        assert "  - tcdmsif72.활성화여부: 1=활성, 0=비활성\n" in narrowed
        assert "  - tcdmsif80.자산상태구분: 01=운영, 02\n" in narrowed
        assert "tcdmsam" not in narrowed and "tcdmsif92" not in narrowed

        full = _codes_section(build_profile_rules_block(meta))
        assert "tcdmsif72" not in full and "tcdmsif80" not in full

    def test_other_table_rules_drop_unreadable_rules_stay(self):
        rules = _rules_section(build_profile_rules_block(_meta(), _SELECTED))
        assert rules == "".join(
            f"  - {r}\n" for r in (_RULE_72, _RULE_MULTI, _RULE_NO_REF, _RULE_TABLE_ONLY)
        )

    def test_dotless_code_key_stays(self):
        """테이블을 읽지 못한 키(점 없음)는 싣는다 — 침묵 누락 방지."""
        assert "  - 점없는키: X\n" in build_profile_rules_block(_meta(), _SELECTED)

    def test_bare_name_case_and_schema_qualifier(self):
        """선별 이름의 스키마 한정자·대소문자, 키·참조의 스키마 한정자를 모두 맨 이름으로 맞춘다."""
        meta = {
            "query_rules": ["`INST1.TCDMSIF72.등록일자`는 문자열 날짜다.", _RULE_OTHER],
            "code_values": {"inst1.TcdmsIF72.활성화여부": ["1"], "tcdmsam01.상태구분": ["A"]},
        }
        block = build_profile_rules_block(meta, ["INST1.TCDMSIF72"])
        assert "`INST1.TCDMSIF72.등록일자`" in block and "tcdmsam01" not in block
        assert "  - inst1.TcdmsIF72.활성화여부: 1\n" in block

    def test_cap_applies_after_narrowing(self):
        meta = {"code_values": {f"tcdmsif72.c{i:03d}": ["1"] for i in range(80)}}
        meta["code_values"] = {"tcdmsam01.x": ["A"], **meta["code_values"]}
        codes = _codes_section(build_profile_rules_block(meta, ["tcdmsif72"]))
        lines = codes.splitlines()
        assert len(lines) == 60
        assert lines[0] == "  - tcdmsif72.c000: 1" and lines[-1] == "  - tcdmsif72.c059: 1"

    def test_none_is_byte_identical_to_prior_behavior(self):
        """None — 규칙 전량 · 키 순서 앞 60칸(종전 구현의 출력 그대로)."""
        meta = _meta()
        block = build_profile_rules_block(meta)
        assert block == build_profile_rules_block(meta, None)
        expected = "\n\n### DB 쿼리 규칙\n" + "".join(
            f"  - {r}\n" for r in meta["query_rules"]
        ) + "\n\n### 코드값 (컬럼: 값=의미)\n" + "".join(
            f"  - tcdmsam{i:02d}.상태구분: A, B\n" for i in range(1, 61)
        )
        assert block == expected

    def test_empty_selection_keeps_only_unreadable(self):
        """선별이 비어도 테이블을 읽지 못한 줄·키는 남는다(빈 목록은 None과 다르다)."""
        block = build_profile_rules_block(_meta(), [])
        assert _rules_section(block) == f"  - {_RULE_NO_REF}\n  - {_RULE_TABLE_ONLY}\n"
        assert _codes_section(block) == "  - 점없는키: X\n"

    def test_no_assets_is_empty(self):
        assert build_profile_rules_block({}, _SELECTED) == ""
        assert build_profile_rules_block(None, _SELECTED) == ""


class TestProfileRuleTables:
    @pytest.mark.parametrize("source", ["llm", "lexical"])
    def test_definition_selection_returns_selected_with_bridges(self, source):
        selection = {
            "mode": "definitions", "source": source, "candidates": 10,
            "selected": ["tcdmsif72", "tcdmsif46"], "bridged": ["tcdmsif46"],
        }
        assert profile_rule_tables(selection) == ["tcdmsif72", "tcdmsif46"]

    @pytest.mark.parametrize("selection", [
        None,
        {},
        {"source": "none", "selected": []},
        {"source": "other", "selected": ["tcdmsif72"]},
        {"source": "llm"},
        "llm",
    ])
    def test_otherwise_none(self, selection):
        assert profile_rule_tables(selection) is None


# ──────────────────────────────────────────────
# 호출부 — 단일/멀티 대칭
# ──────────────────────────────────────────────


class _CapturedError(Exception):
    """프롬프트 조립 함수에 넘어간 인자를 들고 빠져나온다."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        super().__init__("captured")
        self.kwargs = kwargs


def _schema_info() -> dict[str, Any]:
    columns = [{"name": "활성화여부", "type": "char(1)"}]
    return {
        "tables": {t: {"columns": list(columns)} for t in _SELECTED},
        "_structure_meta": _meta(),
    }


def _selection(source: str) -> dict[str, Any]:
    return {
        "mode": "definitions", "source": source, "candidates": 90,
        "selected": list(_SELECTED), "bridged": [],
    }


async def _single_rule_tables(monkeypatch, selections: dict | None) -> Any:
    """단일 경로가 `_build_system_prompt`에 넘기는 `rule_tables`."""
    def _capture(**kwargs: Any) -> str:
        raise _CapturedError(kwargs)

    monkeypatch.setattr(qg, "_select_query_history_examples", AsyncMock(return_value=None))
    monkeypatch.setattr(qg, "_build_system_prompt", _capture)
    state: dict[str, Any] = {"active_db_id": "app_db", "schema_info": _schema_info()}
    if selections is not None:
        state["table_selection"] = selections
    ctx = SimpleNamespace(
        app_config=AppConfig(), is_retry=False, user_query="서버 목록",
        limit_value=1000, adapter_db_ids=set(),
    )
    with pytest.raises(_CapturedError) as info:
        await qg._build_fallback_prompts(state, ctx)
    return info.value.kwargs["rule_tables"]


async def _multi_rule_tables(monkeypatch, selections: dict | None) -> Any:
    """멀티 경로가 `_generate_sql`에 넘기는 `rule_tables`."""
    async def _capture(*_args: Any, **kwargs: Any) -> str:
        raise _CapturedError(kwargs)

    monkeypatch.setattr(mdb, "_deterministic_alarm_sql_or_none", lambda *a, **k: None)
    monkeypatch.setattr(mdb, "_prior_for_db", lambda *a, **k: (None, None))
    monkeypatch.setattr(mdb, "_generate_sql", _capture)
    run = SimpleNamespace(
        table_selections=selections, llm=None, parsed_requirements={}, effective_limit=1000,
        unmapped_fields=[], app_config=None, mc_candidates=[], mc_derivations=[],
        value_index=None, form_context="", form_fill_out={}, form_intent=False,
        mapping_sources={}, form_fill_answers={}, state={}, template_assembly={},
    )
    with pytest.raises(_CapturedError) as info:
        await mdb._generate_validated_sql(
            run, None, _schema_info(), "서버 목록", {}, db_engine="mysql", db_id="app_db",
        )
    return info.value.kwargs["rule_tables"]


class TestCallSites:
    @pytest.mark.parametrize("source", ["llm", "lexical"])
    async def test_both_paths_pass_selected_tables(self, monkeypatch, source):
        selections = {"app_db": _selection(source)}
        assert await _single_rule_tables(monkeypatch, selections) == _SELECTED
        assert await _multi_rule_tables(monkeypatch, selections) == _SELECTED

    @pytest.mark.parametrize("selections", [
        None,
        {},
        {"other_db": _selection("llm")},
        {"app_db": {**_selection("none"), "selected": []}},
    ])
    async def test_both_paths_pass_none_without_definition_selection(
        self, monkeypatch, selections,
    ):
        assert await _single_rule_tables(monkeypatch, selections) is None
        assert await _multi_rule_tables(monkeypatch, selections) is None

    async def test_same_input_same_block_in_both_prompts(self):
        """같은 스키마·선별이면 단일·멀티 시스템 프롬프트에 같은 좁힌 블록이 실린다."""
        tables = profile_rule_tables(_selection("llm"))
        narrowed = build_profile_rules_block(_meta(), tables)
        full = build_profile_rules_block(_meta())
        single = qg._build_system_prompt(
            schema_info=_schema_info(), default_limit=1000, active_db_id="app_db",
            polestar_db_ids=set(), active_db_engine="mysql", rule_tables=tables,
        )
        multi = await mdb._build_multi_system_prompt(
            _schema_info(), {"original_query": "서버 목록"}, "서버 목록", 1000,
            "mysql", "app_db", None, rule_tables=tables,
        )
        for prompt in (single, multi):
            assert narrowed in prompt and full not in prompt
            assert "tcdmsif72.활성화여부: 1=활성, 0=비활성" in prompt

    async def test_none_prompts_carry_full_block(self):
        full = build_profile_rules_block(_meta())
        single = qg._build_system_prompt(
            schema_info=_schema_info(), default_limit=1000, active_db_id="app_db",
            polestar_db_ids=set(), active_db_engine="mysql",
        )
        multi = await mdb._build_multi_system_prompt(
            _schema_info(), {"original_query": "서버 목록"}, "서버 목록", 1000,
            "mysql", "app_db", None,
        )
        assert full in single and full in multi
