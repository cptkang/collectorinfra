"""plans/120 F-1·F-1b·F-6 — 월 구조 필드 판정·매핑 스킵·인식기 견고화·미채움 고지.

벤치 run `20260923-140539` H-10 사슬(§2.4): 양식 월 사용률 열은 필드명에 CPU·메모리 명사가
없어 매핑 스킵 규칙을 빠져나갔고, 오염 유사어에 정확 매칭돼 월 시리즈 인식기가 16/16
미발동했다. LLM 0 · DB 0 · 기준일 2026-09-23 고정.
"""

from __future__ import annotations

import pytest

from src.utils.month_structure import (
    is_month_structure_field,
    month_group_agg,
    parse_month_structure_field,
    parse_month_sub,
)

_AVG = "월중평균사용률(최근 6개월간)"
_PEAK = "월중 Peak시 사용률(최근 6개월간)"


class TestIsMonthStructureField:
    """utils 공용 판정 — 명사 없이 구조만으로 판정한다(F-1 · F-4 소비처 공유)."""

    @pytest.mark.parametrize(
        "field",
        [
            f"{_AVG}|M", f"{_AVG}|M+5", f"{_PEAK}|M", f"{_PEAK}|M+3",
            f"{_AVG}|m + 2", "CPU 평균 사용률|2026.03", "메모리 최고 사용률|3월",
            "피크 사용률|2025년 12월", "avg 사용률|M+1",
        ],
    )
    def test_month_structure_true(self, field):
        assert is_month_structure_field(field) is True

    @pytest.mark.parametrize(
        "field",
        [
            _AVG,                          # 서브 없음(그룹명만)
            "월중 사용률|M",                 # 집계어 없음
            "월중평균|M",                    # '사용률' 없음
            f"{_AVG}|합계",                  # 서브가 월 아님
            f"{_AVG}|13월",                  # 월 범위 밖
            "처리능력|(TPMC)", "구분|분류", "비고", "", "CPU 평균",
        ],
    )
    def test_month_structure_false(self, field):
        assert is_month_structure_field(field) is False

    def test_parse_returns_agg_kind_and_sub(self):
        assert parse_month_structure_field(f"{_AVG}|M+2") == ("avg", ("rel", 2))
        assert parse_month_structure_field(f"{_PEAK}|M") == ("peak", ("rel", 0))
        assert parse_month_structure_field("CPU 최대 사용률|2026-03") == ("peak", ("abs", "202603"))

    def test_peak_precedes_avg(self):
        assert month_group_agg("최대 평균 사용률") == "peak"

    def test_parse_month_sub_abs_month_only(self):
        assert parse_month_sub("3월") == ("abs", "03")
        assert parse_month_sub("0월") is None


# ── F-1 · F-1b 공통 픽스처(테스트 전용 스키마 리터럴 — 공용 계층에 두지 않는다) ──────────
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from src.db_adapters.polestar.assembler import recognize_month_series  # noqa: E402

_FORMS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "forms"
_TODAY = date(2026, 9, 23)  # run 20260923 기준일 — 마지막 완결 월 202608
#: 폐쇄망 run 로그의 서브 테이블 정확 매칭 대상(§2.4) — 오염 유사어가 가리키던 비지표 컬럼
_POLLUTED_COL = "MON_HW_20260806.MEM_RATIO"
_POLLUTED_DB = "polestar_b0"
#: 실행 DB(gp) 허용 테이블 — 스키마 분석 결과 `tables` 키 모양
_GP_TABLES = {
    "cmm_resource": {"columns": [{"name": "name"}, {"name": "hostname"}]},
    "core_config_prop": {"columns": [{"name": "name"}, {"name": "stringvalue_short"}]},
    "cmm_metric_stat_m": {"columns": [{"name": "avg_val"}, {"name": "max_val"}]},
}
_EAV_PATTERN = {
    "type": "eav",
    "entity_table": "cmm_resource",
    "config_table": "core_config_prop",
    "attribute_column": "name",
    "value_column": "stringvalue_short",
    "direct_join": {"entity_column": "resource_conf_id", "config_column": "configuration_id"},
    "known_attributes": [
        {"name": "Model", "description": "모델명 [resource_type: server.Server]"},
        {"name": "TotalSize", "description": "메모리 용량 [resource_type: server.Memory]"},
    ],
}


def _parse_form(name: str) -> dict:
    """저장소 양식 픽스처를 **실제 파서**로 읽는다(mock shape 금지 — Known Mistakes)."""
    from src.document.excel_parser import parse_excel_template

    return parse_excel_template((_FORMS_DIR / name).read_bytes())


def _month_fields(field_names: list[str]) -> list[str]:
    return [f for f in field_names if is_month_structure_field(f)]


def _polluted_synonyms(field_names: list[str]) -> dict[str, dict[str, list[str]]]:
    """오염 사전 — 복합 월 필드명이 비지표 컬럼의 유사어로 그대로 들어 있다(§2.4 ②)."""
    return {_POLLUTED_DB: {_POLLUTED_COL: _month_fields(field_names)}}


def _structure_cache_manager() -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_structure_meta_or_profile = AsyncMock(return_value={"patterns": [_EAV_PATTERN]})
    return mgr


def _empty_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content="{}")
    return llm


class TestF1MetricUsageSkip:
    """F-1 — 명사 없는 월 구조 필드도 매핑 스킵 대상이다."""

    @pytest.mark.parametrize("form", ["CPU_양식.xlsx", "메모리_양식.xlsx"])
    def test_fixture_month_fields_all_hit_skip(self, form):
        from src.document.field_mapper import _is_metric_usage_field, extract_field_names

        names = extract_field_names(_parse_form(form))
        months = _month_fields(names)
        assert len(months) == 12  # 월중평균·Peak × M~M+5
        # 종전 0/12 적중(§2.4 재현 표) → 12/12
        assert [f for f in months if _is_metric_usage_field(f)] == months
        # 월 구조가 아닌 필드는 스킵 대상이 아니다(정상 매핑 유지)
        others = [f for f in names if f not in months]
        assert not [f for f in others if _is_metric_usage_field(f)]

    @pytest.mark.parametrize(
        "field,expected",
        [
            ("CPU 평균", True), ("메모리 사용률", True), ("디스크 사용률", True),
            ("메모리 용량", False), ("CPU 코어 수", False), ("서버 이름", False),
            ("CPU 평균 사용률|M+1", True), ("처리능력|(TPMC)", False),
        ],
    )
    def test_noun_field_judgment_unchanged(self, field, expected):
        """필드명에 명사가 있는 기존 판정은 비트 동일."""
        from src.document.field_mapper import _is_metric_usage_field

        assert _is_metric_usage_field(field) is expected

    @pytest.mark.parametrize(
        "form,query,rt,anchor,source",
        [
            ("CPU_양식.xlsx", "채워줘", "server.Cpus", ("202603", "202608"), "default"),
            (
                "CPU_양식.xlsx", "1월부터 3월까지 채워줘", "server.Cpus",
                ("202510", "202603"), "query",
            ),
            (
                "메모리_양식.xlsx", "2026년 6월 기준 메모리 양식 채워줘", "server.Memory",
                ("202601", "202606"), "query",
            ),
        ],
    )
    async def test_polluted_synonyms_do_not_block_month_series(
        self, form, query, rt, anchor, source
    ):
        """오염 사전이 있어도 field_mapper → 인식기가 발동한다(§2.4 재현 표의 기대값)."""
        from src.document.field_mapper import extract_field_names, perform_3step_mapping
        from src.utils.query_gen_common import template_context_text

        template = _parse_form(form)
        names = extract_field_names(template)
        result, _ = await perform_3step_mapping(
            llm=_empty_llm(),
            field_names=names,
            field_mapping_hints=[],
            all_db_synonyms=_polluted_synonyms(names),
            all_db_descriptions={},
            priority_db_ids=[_POLLUTED_DB, "polestar_cm_yd"],
            eav_name_synonyms={},
            cache_manager=_structure_cache_manager(),
            active_db_ids=[_POLLUTED_DB, "polestar_cm_gp", "polestar_cm_yd"],
            global_synonyms={},
        )
        months = _month_fields(names)
        assert {f: result.column_mapping[f] for f in months} == {f: None for f in months}
        assert _POLLUTED_COL not in str(result.db_column_mapping)

        ms = recognize_month_series(
            result.column_mapping,
            context_text=template_context_text(template),
            user_query=query,
            today=_TODAY,
        )
        assert ms is not None
        assert ms.resource_type == rt
        assert ms.anchor == anchor
        assert ms.anchor_source == source
        assert sorted(ms.fields) == sorted(months)

    async def test_non_eav_schema_keeps_legacy_mapping(self):
        """EAV 피벗 선언이 없는 스키마에서는 종전대로 스킵하지 않는다(월 구조 필드 포함)."""
        from src.document.field_mapper import perform_3step_mapping

        field = "월중평균사용률(최근 6개월간)|M"
        result, _ = await perform_3step_mapping(
            llm=_empty_llm(),
            field_names=[field],
            field_mapping_hints=[],
            all_db_synonyms={"metrics_db": {"usage_monthly.avg_pct": [field]}},
            all_db_descriptions={},
            priority_db_ids=["metrics_db"],
            eav_name_synonyms={},
            active_db_ids=["metrics_db"],
            global_synonyms={},
        )
        assert result.column_mapping[field] == "usage_monthly.avg_pct"


class TestF1bRecognizerRobustness:
    """F-1b — 허용 테이블 밖 매핑은 미매핑으로 보고, 미발동 사유를 나눠 남긴다."""

    def _polluted_mapping(self, form: str) -> tuple[dict, str]:
        from src.document.field_mapper import extract_field_names
        from src.utils.query_gen_common import template_context_text

        template = _parse_form(form)
        names = extract_field_names(template)
        mapping: dict = {f: None for f in names}
        mapping.update({f: _POLLUTED_COL for f in _month_fields(names)})
        mapping["호스트명"] = "cmm_resource.hostname"
        return mapping, template_context_text(template)

    def test_polluted_mapping_without_allowed_tables_is_none(self, caplog):
        """팀 리드 재현: 비지표 컬럼 매핑이면 None — 사유는 '비지표 컬럼 매핑'이다(오보 금지)."""
        mapping, ctx = self._polluted_mapping("CPU_양식.xlsx")
        with caplog.at_level("INFO", logger="src.db_adapters.polestar.assembler"):
            assert recognize_month_series(mapping, context_text=ctx, today=_TODAY) is None
        text = caplog.text
        assert "월 구조 필드 12건이 비지표 컬럼 매핑이라 인식 제외" in text
        assert "패턴 불충족" not in text

    def test_polluted_mapping_outside_allowed_tables_fires(self, caplog):
        mapping, ctx = self._polluted_mapping("CPU_양식.xlsx")
        with caplog.at_level("INFO", logger="src.db_adapters.polestar.assembler"):
            ms = recognize_month_series(
                mapping, context_text=ctx, user_query="채워줘", today=_TODAY,
                allowed_tables=_GP_TABLES.keys(),
            )
        assert ms is not None
        assert ms.anchor == ("202603", "202608")
        assert len(ms.fields) == 12
        assert "허용 테이블 밖 컬럼에 매핑된 월 구조 필드 12건은 미매핑으로 간주" in caplog.text

    def test_schema_qualified_allowed_table_names(self):
        """스키마 한정 테이블 키(`polestar.cmm_resource`)도 맨 이름으로 대조한다."""
        mapping = {"월중평균사용률(최근 6개월간)|M": "polestar.cmm_resource.description"}
        ms = recognize_month_series(
            mapping, context_text="CPU", today=_TODAY,
            allowed_tables=["polestar.cmm_resource"],
        )
        assert ms is None  # 허용 테이블 안 비지표 컬럼 — 종전대로 인식 제외

    def test_mapping_inside_allowed_tables_still_excluded(self, caplog):
        """허용 테이블 안의 비지표 컬럼 매핑은 종전대로 제외(판정 범위는 테이블 밖만)."""
        mapping = {
            f"월중평균사용률(최근 6개월간)|M+{k}": "cmm_resource.description" for k in range(1, 3)
        }
        mapping["월중평균사용률(최근 6개월간)|M"] = None
        with caplog.at_level("INFO", logger="src.db_adapters.polestar.assembler"):
            ms = recognize_month_series(
                mapping, context_text="CPU", today=_TODAY, allowed_tables=_GP_TABLES.keys(),
            )
        assert ms is not None and ms.fields == ["월중평균사용률(최근 6개월간)|M"]
        assert "월 구조 필드 2건이 비지표 컬럼 매핑이라 인식 제외" in caplog.text
        assert "미발동" not in caplog.text

    def test_pattern_unmet_reason_separate(self, caplog):
        mapping = {"월중 사용률(최근 6개월간)|M": None, "월중평균사용률|합계": None}
        with caplog.at_level("INFO", logger="src.db_adapters.polestar.assembler"):
            assert recognize_month_series(mapping, context_text="CPU", today=_TODAY) is None
        assert "집계어/서브(M+k·절대월) 패턴 불충족" in caplog.text
        assert "비지표 컬럼" not in caplog.text

    def test_eav_mapping_not_judged_by_table(self):
        mapping = {"월중평균사용률(최근 6개월간)|M": "EAV:TotalSize"}
        assert recognize_month_series(
            mapping, context_text="CPU", today=_TODAY, allowed_tables=_GP_TABLES.keys(),
        ) is None

    def test_agg_kind_value_column_table_covers_utils_kinds(self):
        """어댑터 값 컬럼 표는 utils 집계 종류를 빠짐없이 덮는다(규칙 사본 대신 종류 키 대응)."""
        from src.db_adapters.polestar.assembler import _MONTH_AGG_VAL_COL
        from src.utils.month_structure import MONTH_GROUP_AGG_TERMS

        assert set(_MONTH_AGG_VAL_COL) == {k for k, _t in MONTH_GROUP_AGG_TERMS}


class TestF1bPathSymmetry:
    """F-1b — 단일·멀티 두 경로가 허용 테이블을 넘겨 오염 매핑에서도 발동한다(대칭 배선)."""

    def _cpu_mapping(self) -> dict:
        from src.document.field_mapper import extract_field_names

        names = extract_field_names(_parse_form("CPU_양식.xlsx"))
        mapping: dict = {f: None for f in names}
        mapping.update({f: _POLLUTED_COL for f in _month_fields(names)})
        mapping["호스트명"] = "cmm_resource.hostname"
        return mapping

    def test_single_path_fires_on_polluted_mapping(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = {
            "column_mapping": self._cpu_mapping(),
            "schema_info": {
                "tables": _GP_TABLES, "_structure_meta": {"patterns": [_EAV_PATTERN]},
            },
            "template_structure": _parse_form("CPU_양식.xlsx"),
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(
            state, 100_000, "2026년 6월 기준 CPU 양식 채워줘"
        )
        assert result is not None and result["month_anchor"] is not None
        assert result["month_anchor"]["end"] == "202606"
        sql = result["sql"]
        assert "MON_HW" not in sql
        # 월 alias는 정확히 1회(오염 매핑이 직접 컬럼으로 중복 SELECT되지 않는다)
        assert sql.count('AS "월중평균사용률(최근 6개월간)|M+2"') == 1
        assert sql.count('AS "월중 Peak시 사용률(최근 6개월간)|M+5"') == 1

    async def test_multi_path_fires_on_polluted_mapping(self):
        from src.nodes.multi_db_executor import _generate_sql

        form_fill_out: dict = {}
        sql = await _generate_sql(
            llm=None,  # 결정적 경로 — LLM에 도달하면 안 된다
            parsed_requirements={"original_query": "2026년 6월 기준 CPU 양식 채워줘"},
            schema_info={
                "tables": _GP_TABLES, "_structure_meta": {"patterns": [_EAV_PATTERN]},
            },
            sub_query_context="CPU 양식 채우기",
            default_limit=100_000,
            column_mapping={k: v for k, v in self._cpu_mapping().items() if v},
            db_engine="postgresql",
            db_id="polestar_cm_gp",
            unmapped_fields=[k for k, v in self._cpu_mapping().items() if not v],
            app_config=SimpleNamespace(
                text2sql=SimpleNamespace(semantic_compose=False, generic_llm_mapping=False),
                get_polestar_db_ids=lambda: set(),
            ),
            form_context_text="나. 주요 업무 CPU 사용현황",
            form_fill_out=form_fill_out,
        )
        assert form_fill_out["month_anchor"]["end"] == "202606"
        assert len(form_fill_out["month_anchor"]["fields"]) == 12
        assert "MON_HW" not in sql
        assert sql.count('AS "월중평균사용률(최근 6개월간)|M+2"') == 1


class TestI07StructureFieldProtection:
    """I-07 t2 — 답변 키가 그룹명(`월중평균사용률(최근 6개월간)`)이고 양식 필드는 `…|M`~`…|M+5`다.

    F-1 뒤 월 필드가 구조 필드로 인식돼야 보호가 발화한다(§4.3 I-07 주석).
    """

    _GROUP_ANSWER = {"월중평균사용률(최근 6개월간)": {"action": "literal", "value": "99"}}
    _REASON = "구조 채움 필드는 답변으로 변경할 수 없습니다"

    async def _f1_mapping(self) -> dict:
        from src.document.field_mapper import extract_field_names, perform_3step_mapping

        names = extract_field_names(_parse_form("CPU_양식.xlsx"))
        result, _ = await perform_3step_mapping(
            llm=_empty_llm(), field_names=names, field_mapping_hints=[],
            all_db_synonyms=_polluted_synonyms(names), all_db_descriptions={},
            priority_db_ids=[_POLLUTED_DB], eav_name_synonyms={},
            cache_manager=_structure_cache_manager(), active_db_ids=[_POLLUTED_DB],
            global_synonyms={},
        )
        return dict(result.column_mapping)

    async def test_single_path_group_key_answer_protected(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = {
            "column_mapping": await self._f1_mapping(),
            "schema_info": {"tables": _GP_TABLES, "_structure_meta": {"patterns": [_EAV_PATTERN]}},
            "template_structure": _parse_form("CPU_양식.xlsx"),
            "form_fill_answers": self._GROUP_ANSWER,
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준으로 채워줘")
        assert result is not None and result["month_anchor"] is not None
        ov = result["overrides"]["월중평균사용률(최근 6개월간)"]
        assert ov["applied"] is False and self._REASON in ov["reason"]
        assert "월중평균사용률(최근 6개월간)" not in result["literals"]

    async def test_multi_path_group_key_answer_protected(self):
        from src.nodes.multi_db_executor import _generate_sql

        mapping = await self._f1_mapping()
        form_fill_out: dict = {}
        await _generate_sql(
            llm=None,
            parsed_requirements={"original_query": "2026년 6월 기준으로 채워줘"},
            schema_info={"tables": _GP_TABLES, "_structure_meta": {"patterns": [_EAV_PATTERN]}},
            sub_query_context="CPU 양식 채우기",
            default_limit=100_000,
            column_mapping={k: v for k, v in mapping.items() if v},
            db_engine="postgresql",
            db_id="polestar_cm_gp",
            unmapped_fields=[k for k, v in mapping.items() if not v],
            app_config=SimpleNamespace(
                text2sql=SimpleNamespace(semantic_compose=False, generic_llm_mapping=False),
                get_polestar_db_ids=lambda: set(),
            ),
            form_context_text="나. 주요 업무 CPU 사용현황",
            form_fill_out=form_fill_out,
            form_intent=True,
            form_fill_answers=self._GROUP_ANSWER,
        )
        ov = form_fill_out["overrides"]["월중평균사용률(최근 6개월간)"]
        assert ov["applied"] is False and self._REASON in ov["reason"]
        assert "월중평균사용률(최근 6개월간)" not in form_fill_out.get("literals", {})

    def test_response_note_shows_protection_reason(self):
        from src.db_adapters.polestar.assembler import resolve_form_fill_answers
        from src.nodes.output_generator import _append_form_fill_notes

        protected = {f"월중평균사용률(최근 6개월간)|{s}" for s in ("M", "M+1", "M+5")}
        overrides, _m, literals = resolve_form_fill_answers(
            self._GROUP_ANSWER, {"tables": _GP_TABLES}, _EAV_PATTERN, protected_fields=protected,
        )
        assert not literals
        out = _append_form_fill_notes("본문", {"form_fill_overrides": overrides})
        assert self._REASON in out


class TestF6MonthFieldNotice:
    """F-6 — 인식기 미발동(앵커 없음)에서도 월 구조 열은 역질문하지 않고 [확인 필요]로 알린다."""

    _FILLED = {"구분|분류": "서버", "제조사(모델명)": "Dell R740", "호스트명": "web-01",
               "처리능력|(TPMC)": "1000", "비고": "웹"}

    def _state(self, row: dict) -> tuple[dict, list[str]]:
        from src.document.field_mapper import extract_field_names
        from src.state import create_initial_state

        data = (_FORMS_DIR / "CPU_양식.xlsx").read_bytes()
        from src.document.excel_parser import parse_excel_template

        template = parse_excel_template(data)
        names = extract_field_names(template)
        state = create_initial_state(user_query="양식 채워줘")
        state["file_type"] = "xlsx"
        state["uploaded_file"] = data
        state["template_structure"] = template
        state["column_mapping"] = {f: None for f in names}
        state["form_month_anchor"] = None  # 인식기 미발동(명사 없음 모의)
        state["organized_data"] = {
            "summary": "서버 1건", "rows": [row], "column_mapping": None, "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"], "output_format": "xlsx",
            "original_query": "양식 채워줘", "filter_conditions": [],
        }
        return state, names

    @staticmethod
    def _capturing_llm(captured: list) -> AsyncMock:
        from langchain_core.messages import AIMessageChunk

        llm = AsyncMock()

        async def _astream(messages, config=None):
            captured.append(messages)
            yield AIMessageChunk(content="양식의 서버 정보를 채웠습니다.")

        llm.astream = _astream
        return llm

    async def test_no_anchor_month_fields_not_asked_and_warned(self):
        from src.nodes.output_generator import output_generator

        state, _names = self._state(dict(self._FILLED))
        captured: list = []
        result = await output_generator(
            state, llm=self._capturing_llm(captured), app_config=MagicMock()
        )
        text = result["final_response"]
        # 월 필드 역질문 0 — 나머지 열이 다 채워져 역질문 자체가 없다
        assert result.get("form_fill_clarification") is None
        assert result.get("pending_form_fill") is None
        assert text.count("[확인 필요]") == 1
        assert "월별 사용률 양식으로 인식하지 못해" in text
        assert "[미작성 항목]" not in text  # 월 열을 "수집 항목 없음"으로 오보하지 않는다
        # 요약 LLM 입력(모의 캡처)에 채운 열 목록이 실린다(서술 가드)
        assert len(captured) == 1
        prompt = captured[0][-1].content
        assert "## 양식 채움 결과" in prompt
        filled_line = next(ln for ln in prompt.splitlines() if ln.startswith("- 채운 열:"))
        empty_line = next(ln for ln in prompt.splitlines() if ln.startswith("- 비운 열:"))
        assert "호스트명" in filled_line and "월중" not in filled_line
        assert "월중평균사용률(최근 6개월간) > M+5" in empty_line

    async def test_clarification_turn_excludes_month_fields(self):
        """다른 열이 비면 역질문은 나가되 월 열은 후보에서 빠진다 — 요약 LLM 0회(119 N-1 ④)."""
        from src.nodes.output_generator import output_generator

        row = dict(self._FILLED)
        row.pop("비고")
        state, _names = self._state(row)
        captured: list = []
        result = await output_generator(
            state, llm=self._capturing_llm(captured), app_config=MagicMock()
        )
        clar = result["form_fill_clarification"]
        assert [f["name"] for f in clar["fields"]] == ["비고"]
        assert result["pending_form_fill"]["unresolved"] == ["비고"]
        assert captured == []  # 역질문 턴은 요약 LLM을 부르지 않는다
        assert result["final_response"].count("[확인 필요]") == 1

    def test_anchor_present_keeps_existing_sql_check_wording(self):
        from src.nodes.output_generator import _append_form_fill_notes

        fields = ["월중평균사용률(최근 6개월간)|M", "월중평균사용률(최근 6개월간)|M+1"]
        state = {"form_month_anchor": {"start": "202601", "end": "202606", "fields": fields}}
        out = _append_form_fill_notes(
            "본문", state, fill_stats={fields[0]: 0, fields[1]: 0, "호스트명": 10}
        )
        assert out.count("[확인 필요]") == 1 and "생성 SQL" in out
        assert "인식하지 못해" not in out

    def test_docx_without_fill_stats_unchanged(self):
        """채움 통계가 없는 경로(docx)는 앵커만 본다 — 종전 매핑 기준 판정과 비트 동일."""
        from src.nodes.output_generator import _form_month_fields

        state = {"form_month_anchor": None}
        assert _form_month_fields(state, None) == set()
        assert _form_month_fields(
            {"form_month_anchor": {"fields": ["a|M"]}}, None
        ) == {"a|M"}

    def test_prompt_block_absent_without_stats(self):
        from src.nodes.output_generator import _build_response_prompt

        rows = [{"호스트명": "web-01"}]
        assert "## 양식 채움 결과" not in _build_response_prompt("q", "1건", rows)
        prompt = _build_response_prompt(
            "q", "1건", rows, form_fill_stats={"호스트명": 1, "a|M": 0}
        )
        assert "- 채운 열: 호스트명" in prompt and "- 비운 열: a > M" in prompt


class TestF7CapacityRuleGateEvidence:
    """F-7 조사 근거 — D-152 ① 용량 스코프 규칙은 월 시리즈 인식 뒤에만 돈다.

    run 로그: `[동의어] EAV 유사어 매칭 확정: '처리능력|(TPMC)' -> EAV:TotalSize
    (단어='처리능력|(TPMC)', db=polestar_b0)` 28회 — 복합 필드명이 EAV 유사어로 들어 있는 오염이다.
    인식기가 16/16 미발동이라 `apply_capacity_scope_rule`(단일·멀티 모두 `if month_series:` 안)이
    돌지 않아 TPMC 칸에 메모리 용량이 들어갔다. F-1로 인식이 살아나면 규칙이 TPMC를 공란으로 막는다.
    """

    async def _mapping(self, month_polluted: bool) -> dict:
        from src.document.field_mapper import extract_field_names, perform_3step_mapping

        names = extract_field_names(_parse_form("CPU_양식.xlsx"))
        result, _ = await perform_3step_mapping(
            llm=_empty_llm(), field_names=names, field_mapping_hints=[],
            all_db_synonyms=_polluted_synonyms(names), all_db_descriptions={},
            priority_db_ids=[_POLLUTED_DB],
            eav_name_synonyms={"TotalSize": ["처리능력|(TPMC)"]},
            cache_manager=_structure_cache_manager(), active_db_ids=[_POLLUTED_DB],
            global_synonyms={},
        )
        mapping = dict(result.column_mapping)
        assert mapping["처리능력|(TPMC)"] == "EAV:TotalSize"  # 오염 EAV 유사어 재현
        if month_polluted:  # F-1 이전 상태 모의 — 월 필드가 비지표 컬럼에 매핑돼 있다
            mapping.update({f: _POLLUTED_COL for f in _month_fields(list(mapping))})
        return mapping

    def _state(self, mapping: dict, *, tables: bool) -> dict:
        schema: dict = {"_structure_meta": {"patterns": [_EAV_PATTERN]}}
        if tables:
            schema["tables"] = _GP_TABLES
        return {
            "column_mapping": mapping, "schema_info": schema,
            "template_structure": _parse_form("CPU_양식.xlsx"),
            "active_db_engine": "postgresql", "active_db_id": "",
        }

    async def test_after_f1_tpmc_forced_blank(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = self._state(await self._mapping(month_polluted=False), tables=True)
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준 CPU 양식 채워줘")
        assert result["month_anchor"] is not None
        assert result["mapping_updates"]["처리능력|(TPMC)"] is None
        assert 'AS "처리능력|(TPMC)"' not in result["sql"]

    async def test_without_month_series_rule_is_skipped(self):
        """인식기 미발동(= 이 run) 재현: 규칙이 돌지 않아 TPMC ← TotalSize가 SQL에 남는다."""
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = self._state(await self._mapping(month_polluted=True), tables=False)
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준 CPU 양식 채워줘")
        assert result["month_anchor"] is None
        assert "cc.name='TotalSize' THEN cc.stringvalue_short END) AS \"처리능력|(TPMC)\"" in (
            result["sql"]
        )
