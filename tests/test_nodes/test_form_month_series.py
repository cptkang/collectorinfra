"""금감원 취합자료 월 시리즈(M~M+5) 폼필 파이프라인 테스트 (plans/67 Phase 3, D-113/D-114/D-115).

인식기(순수 함수) → 단일/멀티 SQL 경로 대칭 배선 → writer 채움 → 응답 사유 노출까지
결정적 경로 전체를 고정한다. LLM은 어디에도 개입하지 않아야 한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

from src.db_adapters.polestar.assembler import (
    apply_capacity_scope_rule,
    find_vendor_model_concat,
    recognize_month_series,
)

_FORMS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "forms"

_TODAY = date(2026, 7, 28)  # 결정적 기준일 — M+5(마지막 완결 월)=202606

_AVG = "월중평균사용률(최근 6개월간)"
_PEAK = "월중 Peak시 사용률(최근 6개월간)"


def _cpu_form_mapping() -> dict:
    mapping: dict = {
        "구분|분류": None,
        "제조사(모델명)": "EAV:Model",
        "호스트명": "cmm_resource.hostname",
        "처리능력|(TPMC)": None,
        "비고": None,
    }
    for k in range(6):
        sub = "M" if k == 0 else f"M+{k}"
        mapping[f"{_AVG}|{sub}"] = None
        mapping[f"{_PEAK}|{sub}"] = None
    return mapping


_EAV_META = {
    "_structure_meta": {
        "patterns": [
            {
                "type": "eav",
                "entity_table": "cmm_resource",
                "config_table": "core_config_prop",
                "attribute_column": "name",
                "value_column": "stringvalue_short",
                "direct_join": {
                    "entity_column": "resource_conf_id",
                    "config_column": "configuration_id",
                },
                "known_attributes": [
                    {"name": "Vendor", "description": "서버 제조사 [resource_type: server.Server]"},
                    {"name": "Model", "description": "모델명 [resource_type: server.Server]"},
                    # 실 프로필과 동일한 대문자 충돌(server.Cpus의 MODEL/VENDOR) 재현 —
                    # eav_attr_resource_types의 upper 키는 후자로 덮인다
                    {"name": "MODEL", "description": "CPU 모델 [resource_type: server.Cpus]"},
                    {"name": "VENDOR", "description": "CPU 제조사 [resource_type: server.Cpus]"},
                    {"name": "TotalSize", "description": "메모리 용량 [resource_type: server.Memory]"},
                ],
            }
        ]
    }
}


class TestRecognizeMonthSeries:
    """월 시리즈 인식기(D-113) — 구조 패턴 기반, 기관명·시트제목 하드코딩 없음."""

    def test_cpu_form_relative_months(self):
        ms = recognize_month_series(
            _cpu_form_mapping(),
            context_text="16. IT장비 및 통신회선 사용현황 나. 주요 업무 CPU 사용현황",
            user_query="양식 채워줘",
            today=_TODAY,
        )
        assert ms is not None
        assert ms.resource_type == "server.Cpus"
        assert len(ms.measures) == 12
        # 기본 앵커: M+5 = 실행일 기준 지난달(202606) → M=202601 (Q3 확정)
        assert ms.anchor == ("202601", "202606")
        assert ms.month_by_field[f"{_AVG}|M"] == "202601"
        assert ms.month_by_field[f"{_PEAK}|M+5"] == "202606"
        # alias는 복합 필드명 그대로(행 키=양식 헤더 아키텍처), 평균→avg_val, peak→max_val
        by_alias = {m[0]: m for m in ms.measures}
        assert by_alias[f"{_AVG}|M+2"][1:] == ("server.Cpus", "avg_val", "202603")
        assert by_alias[f"{_PEAK}|M+2"][1:] == ("server.Cpus", "max_val", "202603")

    def test_user_specified_anchor_month_is_end(self):
        """사용자가 월을 명시하면 그 월이 M+max(끝 월)가 된다(Q3: 기준월=M+5)."""
        ms = recognize_month_series(
            _cpu_form_mapping(),
            context_text="CPU 사용현황",
            user_query="2026년 3월 기준으로 채워줘",
            today=_TODAY,
        )
        assert ms.anchor == ("202510", "202603")
        assert ms.month_by_field[f"{_AVG}|M+5"] == "202603"

    def test_memory_context_noun(self):
        """'주기억장치'(관용 표현)로 메모리 리소스를 판정한다."""
        ms = recognize_month_series(
            _cpu_form_mapping(),
            context_text="다. 주요 업무 주기억장치 사용현황",
            today=_TODAY,
        )
        assert ms.resource_type == "server.Memory"

    def test_no_resource_noun_falls_back(self):
        """리소스 명사가 어디에도 없으면 판정 불가 → None(기존 경로 폴백)."""
        assert recognize_month_series(
            _cpu_form_mapping(), context_text="사용현황", user_query="양식 채워줘",
            today=_TODAY,
        ) is None

    def test_absolute_months_without_year(self):
        """'1월'~'3월' 절대 표기(연도 미상)는 마지막 완결 월 이하 최근 발생으로 보정."""
        mapping = {f"{_AVG}|{m}월": None for m in (1, 2, 3)}
        ms = recognize_month_series(mapping, context_text="CPU", today=_TODAY)
        assert ms.anchor == ("202601", "202603")

    def test_absolute_future_month_wraps_to_previous_year(self):
        """실행일(7월) 기준 '12월'은 아직 안 온 달 → 작년 12월로 보정."""
        mapping = {f"{_AVG}|12월": None, f"{_AVG}|1월": None}
        ms = recognize_month_series(mapping, context_text="CPU", today=_TODAY)
        assert ms.month_by_field[f"{_AVG}|12월"] == "202512"
        assert ms.month_by_field[f"{_AVG}|1월"] == "202601"

    def test_mixed_relative_absolute_rejected(self):
        mapping = {f"{_AVG}|M": None, f"{_AVG}|3월": None}
        assert recognize_month_series(mapping, context_text="CPU", today=_TODAY) is None

    def test_mapped_fields_not_consumed(self):
        """이미 정상 매핑된 필드는 인식 대상이 아니다(cmm_metric_stat 오매핑은 대상)."""
        mapping = {
            f"{_AVG}|M": "cmm_metric_stat_m.avg_val",  # LLM 오매핑 → 인식 대상
            f"{_AVG}|M+1": None,
        }
        ms = recognize_month_series(mapping, context_text="CPU", today=_TODAY)
        assert ms is not None and len(ms.measures) == 2

    def test_overlong_alias_rejected(self):
        """PG 식별자 63바이트 초과 필드명은 잘려 월 서픽스가 소실되므로 양식 전체 폴백."""
        long_group = "월중평균사용률" * 4  # 84바이트
        mapping = {f"{long_group}|M": None, f"{long_group}|M+1": None}
        assert recognize_month_series(mapping, context_text="CPU", today=_TODAY) is None

    def test_non_month_form_ignored(self):
        """서버 목록 양식(월 서브헤더 없음)은 발동하지 않는다."""
        mapping = {
            "서버위치|설치장소 (주센터, 재해복구센터 등)": None,
            "접근통제 및 추가인증|적용 솔루션명": None,
            "서버명": "cmm_resource.name",
        }
        assert recognize_month_series(mapping, context_text="18. DB 및 서버 운영현황", today=_TODAY) is None


class TestCapacityScopeRule:
    """'처리능력|(GB)' 요청 스코프 규칙(Q1/D-115) — 유사어 등록 없이 3중 문맥으로만."""

    _ATTR_RT = {"TOTALSIZE": "server.Memory", "MODEL": "server.Server"}

    def test_gb_capacity_mapped_for_memory_form(self):
        updates = apply_capacity_scope_rule(
            {"처리능력|(GB)": None, "비고": None}, self._ATTR_RT, "server.Memory"
        )
        assert updates == {"처리능력|(GB)": "EAV:TotalSize"}

    def test_tpmc_forced_blank(self):
        """CPU 양식의 (TPMC)는 오염 매핑이 있어도 강제 None(공란 보장) — 라이브 4차 실측:
        field_mapper 캐시가 '처리능력'을 TotalSize로 매핑해 TPMC에 메모리 용량이 채워짐."""
        updates = apply_capacity_scope_rule(
            {"처리능력|(TPMC)": "EAV:TotalSize"}, self._ATTR_RT, "server.Cpus"
        )
        assert updates == {"처리능력|(TPMC)": None}

    def test_requires_profile_attribute(self):
        """프로필에 TotalSize가 없으면 용량 매핑 대신 강제 공란(프로필 게이트)."""
        assert apply_capacity_scope_rule(
            {"처리능력|(GB)": None}, {}, "server.Memory"
        ) == {"처리능력|(GB)": None}


class TestVendorModelConcat:
    """'제조사(모델명)' Vendor+Model 결합 규칙(D-115) — 라이브 실측 반쪽 매핑 교정."""

    def test_rule_picks_server_scoped_exact_case_names(self):
        """server.Cpus의 MODEL/VENDOR(대문자 충돌)가 아니라 server.Server의
        Vendor/Model 정확한 이름을 고른다."""
        rules = find_vendor_model_concat(_EAV_META, {"제조사(모델명)": "EAV:Model"})
        assert rules == [("제조사(모델명)", "Vendor", "Model")]

    def test_rule_requires_both_attrs(self):
        meta = {"_structure_meta": {"patterns": [{
            "type": "eav",
            "known_attributes": [
                {"name": "Vendor", "description": "제조사 [resource_type: server.Server]"},
            ],
        }]}}
        assert find_vendor_model_concat(meta, {"제조사(모델명)": None}) == []

    def test_single_path_sql_has_concat_alias_once(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = {
            "column_mapping": _cpu_form_mapping(),
            "schema_info": _EAV_META,
            "template_structure": {"sheets": [{"title_text": "나. 주요 업무 CPU 사용현황", "name": "Sheet1"}]},
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준 CPU 양식 채워줘")
        sql = result["sql"]
        # Vendor(Model) 결합 라인 — alias는 정확히 1회(단독 매핑과 중복 없음)
        assert sql.count('AS "제조사(모델명)"') == 1
        assert "cc.name='Vendor'" in sql
        assert "NULLIF(COALESCE(" in sql
        assert "'(' ||" in sql


class TestSinglePathWiring:
    """단일 경로(query_generator._try_build_form_fill_pivot_sql) 배선."""

    def _state(self, mapping: dict, title: str) -> dict:
        return {
            "column_mapping": mapping,
            "schema_info": _EAV_META,
            "template_structure": {"sheets": [{"title_text": title, "name": "Sheet1"}]},
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }

    def test_cpu_form_builds_month_pivot_sql(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = self._state(_cpu_form_mapping(), "나. 주요 업무 CPU 사용현황")
        result = _try_build_form_fill_pivot_sql(
            state, 100_000, "2026년 6월 기준 CPU 양식 채워줘"
        )
        assert result is not None
        sql = result["sql"]
        # 월별 CASE 피벗 + alias=복합 필드명
        assert "s.stat_date='202603'" in sql
        assert f'AS "{_AVG}|M+2"' in sql
        assert f'AS "{_PEAK}|M+5"' in sql
        # 기존 필드도 통합 피벗에 포함
        assert 'AS "호스트명"' in sql
        assert "cc.name='Model'" in sql
        # 서버당 1행 계약 유지
        assert "GROUP BY COALESCE(c.platform_resource_id, c.id)" in sql
        anchor = result["month_anchor"]
        assert anchor["start"] == "202601" and anchor["end"] == "202606"
        assert len(anchor["fields"]) == 12
        # 월 필드는 state 매핑 강제 None(writer 필드명 조회 — N:1 역매핑 동일값 방지)
        updates = result["mapping_updates"]
        assert updates[f"{_AVG}|M+2"] is None
        assert updates[f"{_PEAK}|M+5"] is None
        # 비고 규칙(사용자 확정): SQL은 등록명, writer 매핑은 None
        assert 'THEN c.name END) AS "비고"' in sql
        assert updates["비고"] is None

    def test_memory_form_applies_capacity_rule(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        mapping = _cpu_form_mapping()
        del mapping["처리능력|(TPMC)"]
        mapping["처리능력|(GB)"] = None
        state = self._state(mapping, "다. 주요 업무 주기억장치 사용현황")
        result = _try_build_form_fill_pivot_sql(
            state, 100_000, "2026년 6월 기준 메모리 양식 채워줘"
        )
        assert result is not None
        assert result["mapping_updates"]["처리능력|(GB)"] == "EAV:TotalSize"
        assert result["mapping_updates"][f"{_AVG}|M"] is None
        sql = result["sql"]
        assert "cc.name='TotalSize'" in sql
        assert "c.resource_type='server.Memory' AND s.definition_name" in sql

    def test_non_form_query_unchanged(self):
        """월 시리즈도 자식 EAV도 없으면 기존과 동일하게 None(LLM 경로 유지)."""
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        state = self._state({"서버명": "cmm_resource.name"}, "서버 목록")
        assert _try_build_form_fill_pivot_sql(state, 1000, "서버 목록 조회") is None


class TestMultiPathWiring:
    """멀티 경로(multi_db_executor._generate_sql) 대칭 배선 — 단일 경로와 동일 SQL 형태."""

    async def test_month_series_deterministic_sql(self):
        from src.nodes.multi_db_executor import _generate_sql

        schema_info = {
            "tables": {"cmm_resource": {"columns": [{"name": "hostname", "type": "text"}]}},
            **_EAV_META,
        }
        month_fields = [f"{_AVG}|{'M' if k == 0 else f'M+{k}'}" for k in range(6)]
        month_fields += [f"{_PEAK}|{'M' if k == 0 else f'M+{k}'}" for k in range(6)]
        form_fill_out: dict = {}
        sql = await _generate_sql(
            llm=None,  # 결정적 경로 — LLM에 도달하면 안 된다
            parsed_requirements={"original_query": "2026년 6월 기준 CPU 양식 채워줘"},
            schema_info=schema_info,
            sub_query_context="CPU 양식 채우기",
            default_limit=100_000,
            column_mapping={"호스트명": "cmm_resource.hostname"},
            db_engine="postgresql",
            db_id="",
            unmapped_fields=month_fields,
            app_config=SimpleNamespace(
                text2sql=SimpleNamespace(semantic_compose=False, generic_llm_mapping=False),
                get_polestar_db_ids=lambda: set(),
            ),
            form_context_text="나. 주요 업무 CPU 사용현황",
            form_fill_out=form_fill_out,
        )
        assert "s.stat_date='202601'" in sql and "s.stat_date='202606'" in sql
        assert f'AS "{_AVG}|M"' in sql
        assert f'AS "{_PEAK}|M+5"' in sql
        assert 'AS "호스트명"' in sql
        assert form_fill_out["month_anchor"]["end"] == "202606"
        assert len(form_fill_out["month_anchor"]["fields"]) == 12

    async def test_fires_with_empty_db_mapping(self):
        """per-DB 매핑이 비어도(라이브 실측 FIX-1) 월 인식이 발동하고 식별 컬럼을 주입한다."""
        from src.nodes.multi_db_executor import _generate_sql

        month_fields = [f"{_AVG}|{'M' if k == 0 else f'M+{k}'}" for k in range(6)]
        form_fill_out: dict = {}
        sql = await _generate_sql(
            llm=None,
            parsed_requirements={"original_query": "2026년 6월 기준 CPU 양식 채워줘"},
            schema_info={"tables": {"cmm_resource": {"columns": []}}, **_EAV_META},
            sub_query_context="CPU 양식 채우기",
            default_limit=100_000,
            column_mapping={},  # per-DB 매핑 공백
            db_engine="postgresql",
            db_id="",
            unmapped_fields=month_fields,
            app_config=SimpleNamespace(
                text2sql=SimpleNamespace(semantic_compose=False, generic_llm_mapping=False),
                get_polestar_db_ids=lambda: set(),
            ),
            form_context_text="나. 주요 업무 CPU 사용현황",
            form_fill_out=form_fill_out,
        )
        assert f'AS "{_AVG}|M+3"' in sql
        # 식별 컬럼 결정적 주입(양식 헤더와 무충돌 라틴 alias)
        assert 'AS "server_name"' in sql and 'AS "hostname"' in sql
        assert form_fill_out["month_anchor"]["start"] == "202601"


class TestWriterFillsMonthColumns:
    """실물 CPU 양식 픽스처에 월 칼럼이 채워지는지(행 키=복합 필드명 아키텍처) e2e."""

    def test_fill_real_cpu_form(self):
        import openpyxl
        import io

        from src.document.excel_parser import parse_excel_template
        from src.document.excel_writer import fill_excel_template

        file_data = (_FORMS_DIR / "CPU_양식.xlsx").read_bytes()
        template = parse_excel_template(file_data)
        mapping = _cpu_form_mapping()
        row = {
            "호스트명": "web-01",
            "제조사(모델명)": "Dell R740",
            f"{_AVG}|M": 11.5,
            f"{_AVG}|M+5": 16.5,
            f"{_PEAK}|M": 91.1,
            f"{_PEAK}|M+5": 96.6,
        }
        stats: dict[str, int] = {}
        out_bytes, filled = fill_excel_template(
            file_data, template, mapping, [row], fill_stats=stats
        )
        assert filled >= 6
        # 필드별 실제 채움 통계(D-114 판정 근거)
        assert stats["호스트명"] == 1
        assert stats[f"{_AVG}|M"] == 1
        assert stats["처리능력|(TPMC)"] == 0
        assert stats["비고"] == 0

        ws = openpyxl.load_workbook(io.BytesIO(out_bytes)).worksheets[0]
        assert ws.cell(row=7, column=3).value == "web-01"   # C7 호스트명
        assert ws.cell(row=7, column=5).value == 11.5       # E7 평균 M
        assert ws.cell(row=7, column=10).value == 16.5      # J7 평균 M+5
        assert ws.cell(row=7, column=11).value == 91.1      # K7 Peak M
        assert ws.cell(row=7, column=16).value == 96.6      # P7 Peak M+5
        assert ws.cell(row=7, column=4).value is None       # D7 TPMC — 공란 유지

    def test_db2_string_values_converted_numeric(self):
        """DB2 경로의 문자열 통계값("6.51")도 월 칼럼에서 숫자로 변환된다(numeric hint)."""
        import openpyxl
        import io

        from src.document.excel_parser import parse_excel_template
        from src.document.excel_writer import fill_excel_template

        file_data = (_FORMS_DIR / "CPU_양식.xlsx").read_bytes()
        template = parse_excel_template(file_data)
        row = {f"{_AVG}|M+1": "6.51"}
        out_bytes, _ = fill_excel_template(
            file_data, template, _cpu_form_mapping(), [row]
        )
        ws = openpyxl.load_workbook(io.BytesIO(out_bytes)).worksheets[0]
        assert ws.cell(row=7, column=6).value == 6.51


class TestFormFillNotes:
    """응답의 기준월 명시(§2.4) + 미작성 사유(D-114)."""

    def test_notes_appended(self):
        from src.nodes.output_generator import _append_form_fill_notes

        state = {
            "form_month_anchor": {
                "start": "202601", "end": "202606",
                "resource_type": "server.Cpus",
                "fields": [f"{_AVG}|M", f"{_AVG}|M+1"],
            },
            "column_mapping": {
                "호스트명": "cmm_resource.hostname",
                "구분|분류": None,
                "처리능력|(TPMC)": None,
                f"{_AVG}|M": None,     # 월 시리즈 — 미작성 사유에서 제외돼야 함
                f"{_AVG}|M+1": None,
            },
        }
        out = _append_form_fill_notes("본문", state)
        assert "2026년 1월" in out and "2026년 6월" in out
        assert "미작성 항목" in out
        assert "구분 > 분류" in out
        assert "처리능력 > (TPMC)" in out
        assert f"{_AVG} > M" not in out  # 월 필드는 채움 대상 — 사유 목록에 없어야 함

    def test_no_anchor_no_change(self):
        from src.nodes.output_generator import _append_form_fill_notes

        assert _append_form_fill_notes("본문", {"column_mapping": {"a": "t.c"}}) == "본문"

    def test_fill_stats_overrides_mapping_based_judgment(self):
        """실제로 채워진 칼럼(매핑 None이어도)은 미작성 목록에 오르지 않는다(라이브 실측 교정)."""
        from src.nodes.output_generator import _append_form_fill_notes

        state = {
            "form_month_anchor": {"start": "202601", "end": "202606", "fields": [f"{_AVG}|M"]},
            "column_mapping": {"제조사(모델명)": None, "처리능력|(TPMC)": None},
        }
        fill_stats = {"제조사(모델명)": 20, "처리능력|(TPMC)": 0, f"{_AVG}|M": 20, "비고": 0}
        out = _append_form_fill_notes("본문", state, fill_stats=fill_stats)
        unfilled_block = out.split("[미작성 항목]")[1]
        assert "처리능력 > (TPMC)" in unfilled_block
        assert "비고" in unfilled_block
        assert "제조사(모델명)" not in unfilled_block  # 채워졌으므로 제외
        assert "2026년 1월부터 2026년 6월까지" in out

    def test_all_month_columns_zero_warns_sql_check(self):
        """월 칼럼이 전부 0건이면 D-050 취지의 생성 SQL 확인 안내를 낸다."""
        from src.nodes.output_generator import _append_form_fill_notes

        fields = [f"{_AVG}|M", f"{_AVG}|M+1"]
        state = {
            "form_month_anchor": {"start": "202601", "end": "202606", "fields": fields},
            "column_mapping": {},
        }
        out = _append_form_fill_notes(
            "본문", state, fill_stats={fields[0]: 0, fields[1]: 0, "호스트명": 10}
        )
        assert "[확인 필요]" in out and "생성 SQL" in out

    def test_parser_title_text_contains_form_context(self):
        """파서가 추출한 title_text에 리소스 판정 문맥(CPU)이 담긴다."""
        from src.document.excel_parser import parse_excel_template

        sheet = parse_excel_template((_FORMS_DIR / "CPU_양식.xlsx").read_bytes())["sheets"][0]
        assert "CPU" in sheet["title_text"]
        assert sheet["header_block_rows"] == [5, 6]


class TestHallucinatedColumnGuard:
    """환각 매핑 칼럼의 결정적 피벗 유입 차단(FIX-5 — 라이브 c.category 실측)."""

    _SCHEMA_TABLES = {
        "tables": {
            "cmm_resource": {
                "columns": [
                    {"name": "name", "type": "text"},
                    {"name": "hostname", "type": "text"},
                    {"name": "id", "type": "bigint"},
                ],
            },
            "no_column_info": {"columns": []},
        }
    }

    def test_helper_drops_missing_column_only(self):
        from src.utils.query_gen_common import drop_entries_missing_columns

        entries = [
            ("호스트명", "cmm_resource.hostname"),        # 존재 → 유지
            ("구분|분류", "cmm_resource.category"),        # 부재 → 제외
            ("기타", "no_column_info.whatever"),           # 칼럼 정보 없음 → 유지(오탐 방지)
            ("미지테이블", "unknown_table.col"),           # 테이블 미지 → 유지
        ]
        kept, dropped = drop_entries_missing_columns(entries, self._SCHEMA_TABLES)
        assert ("호스트명", "cmm_resource.hostname") in kept
        assert ("기타", "no_column_info.whatever") in kept
        assert ("미지테이블", "unknown_table.col") in kept
        assert dropped == [("구분|분류", "cmm_resource.category")]

    def test_single_path_excludes_hallucinated_column(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        mapping = _cpu_form_mapping()
        mapping["구분|분류"] = "cmm_resource.category"  # 환각 매핑
        state = {
            "column_mapping": mapping,
            "schema_info": {**self._SCHEMA_TABLES, **_EAV_META},
            "template_structure": {"sheets": [{"title_text": "CPU 사용현황", "name": "Sheet1"}]},
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준")
        sql = result["sql"]
        assert "category" not in sql
        assert 'AS "호스트명"' in sql

    async def test_multi_path_excludes_hallucinated_column(self):
        from src.nodes.multi_db_executor import _generate_sql

        month_fields = [f"{_AVG}|{'M' if k == 0 else f'M+{k}'}" for k in range(6)]
        sql = await _generate_sql(
            llm=None,
            parsed_requirements={"original_query": "2026년 6월 기준 CPU 양식 채워줘"},
            schema_info={**self._SCHEMA_TABLES, **_EAV_META},
            sub_query_context="CPU 양식 채우기",
            default_limit=100_000,
            column_mapping={"구분|분류": "cmm_resource.category", "호스트명": "cmm_resource.hostname"},
            db_engine="postgresql",
            db_id="",
            unmapped_fields=month_fields,
            app_config=SimpleNamespace(
                text2sql=SimpleNamespace(semantic_compose=False, generic_llm_mapping=False),
                get_polestar_db_ids=lambda: set(),
            ),
            form_context_text="나. 주요 업무 CPU 사용현황",
            form_fill_out={},
        )
        assert "category" not in sql
        assert 'AS "호스트명"' in sql


class TestInsufficiencyLoopSuppression:
    """결정적 월 피벗 결과가 부족 판정 재시도(LLM 재생성)로 덮이는 회귀 방지(FIX-6)."""

    async def test_month_anchor_suppresses_retry(self, monkeypatch):
        import importlib

        ro = importlib.import_module("src.nodes.result_organizer")

        async def _always_insufficient(*args, **kwargs):
            return False

        monkeypatch.setattr(ro, "_check_data_sufficiency", _always_insufficient)
        state = {
            "query_results": [{"호스트명": "web-01", f"{_AVG}|M": 11.1}],
            "parsed_requirements": {"output_format": "xlsx"},
            "template_structure": None,
            "column_mapping": {"호스트명": "cmm_resource.hostname"},
            "retry_count": 0,
            "form_month_anchor": {"start": "202601", "end": "202606", "fields": [f"{_AVG}|M"]},
        }
        result = await ro.result_organizer(state)
        assert result.get("error_message") != "data_insufficient"
        assert result["organized_data"]["is_sufficient"] is not False

    async def test_without_anchor_retry_still_requested(self, monkeypatch):
        import importlib

        ro = importlib.import_module("src.nodes.result_organizer")

        async def _always_insufficient(*args, **kwargs):
            return False

        monkeypatch.setattr(ro, "_check_data_sufficiency", _always_insufficient)
        state = {
            "query_results": [{"호스트명": "web-01"}],
            "parsed_requirements": {"output_format": "xlsx"},
            "template_structure": None,
            "column_mapping": None,
            "retry_count": 0,
        }
        result = await ro.result_organizer(state)
        assert result.get("error_message") == "data_insufficient"


class TestWriterMappingForceNull:
    """월 필드 N:1 오염 매핑의 강제 None 갱신(라이브 3차 실측 — Excel 동일값 복제 방지)."""

    def test_polluted_metric_mapping_forced_to_none(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        mapping = _cpu_form_mapping()
        # field_mapper 오염 재현: 월 필드 전부가 같은 metric 칼럼으로 매핑됨
        for k in range(6):
            sub = "M" if k == 0 else f"M+{k}"
            mapping[f"{_AVG}|{sub}"] = "cmm_metric_stat_m.avg_val"
            mapping[f"{_PEAK}|{sub}"] = "cmm_metric_stat_m.max_val"
        state = {
            "column_mapping": mapping,
            "schema_info": _EAV_META,
            "template_structure": {"sheets": [{"title_text": "CPU 사용현황", "name": "Sheet1"}]},
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준")
        assert result is not None
        # 인식은 발동(월별 리터럴 SQL) + 오염 매핑은 전부 None으로 갱신
        assert "s.stat_date='202603'" in result["sql"]
        for k in range(6):
            sub = "M" if k == 0 else f"M+{k}"
            assert result["mapping_updates"][f"{_AVG}|{sub}"] is None
            assert result["mapping_updates"][f"{_PEAK}|{sub}"] is None


class TestResponseTableMarkdownSafety:
    """복합 필드명 '|'의 Markdown 표 충돌 방지 — 표시용 키 결정적 치환."""

    def test_pipe_keys_replaced_in_prompt(self):
        from src.nodes.output_generator import _build_response_prompt

        rows = [{f"{_AVG}|M+2": 11.5, "처리능력|(TPMC)": None, "호스트명": "web-01"}]
        prompt = _build_response_prompt("질의", "요약", rows)
        assert f"{_AVG} > M+2" in prompt
        assert "처리능력 > (TPMC)" in prompt
        assert f"{_AVG}|M+2" not in prompt  # 원본 '|' 키는 프롬프트에 없어야 함


class TestWriterStrictFieldLookup:
    """필드명 폴백 열의 엄격 조회(FIX-11) — NULL 월 키 누락 시 이웃 월 복제 방지."""

    def test_missing_month_key_stays_blank(self):
        """6월 생성 서버(행에 M+5 키만 존재): M~M+4는 공란, M+5만 채워져야 한다."""
        import io
        import openpyxl

        from src.document.excel_parser import parse_excel_template
        from src.document.excel_writer import fill_excel_template

        file_data = (_FORMS_DIR / "CPU_양식.xlsx").read_bytes()
        template = parse_excel_template(file_data)
        # NULL 칼럼이 직렬화에서 누락된 행 재현 — M+5만 존재
        row = {"호스트명": "new-06", f"{_AVG}|M+5": 7.7}
        out_bytes, _ = fill_excel_template(file_data, template, _cpu_form_mapping(), [row])
        ws = openpyxl.load_workbook(io.BytesIO(out_bytes)).worksheets[0]
        assert ws.cell(row=7, column=5).value is None    # E7 = 평균 M — 복제 금지
        assert ws.cell(row=7, column=9).value is None    # I7 = 평균 M+4 — 복제 금지
        assert ws.cell(row=7, column=10).value == 7.7    # J7 = 평균 M+5

    def test_db2_lowercase_latin_key_still_matches(self):
        """DB2 라틴 소문자화('…|m+1') 행 키는 정규화 동등으로 흡수한다(엄격 조회 범위 내)."""
        import io
        import openpyxl

        from src.document.excel_parser import parse_excel_template
        from src.document.excel_writer import fill_excel_template

        file_data = (_FORMS_DIR / "CPU_양식.xlsx").read_bytes()
        template = parse_excel_template(file_data)
        row = {f"{_AVG}|m+1".replace("M", "m"): 6.51}  # 라틴만 소문자화된 키
        out_bytes, _ = fill_excel_template(file_data, template, _cpu_form_mapping(), [row])
        ws = openpyxl.load_workbook(io.BytesIO(out_bytes)).worksheets[0]
        assert ws.cell(row=7, column=6).value == 6.51    # F7 = 평균 M+1


class TestUnverifiableSchemaWhitelist:
    """스키마에 칼럼 목록이 없을 때 entity 안전 화이트리스트(FIX-13 — gp+yd category 차단)."""

    def test_category_dropped_hostname_kept(self):
        from src.db_adapters.polestar.assembler import filter_pivot_regular_entries

        entries = [
            ("호스트명", "cmm_resource.hostname"),   # 화이트리스트 → 유지
            ("비고", "cmm_resource.name"),           # 화이트리스트 → 유지
            ("구분|분류", "cmm_resource.category"),   # 화이트리스트 밖 → 제외
        ]
        kept, dropped = filter_pivot_regular_entries(entries, {}, "cmm_resource")
        assert ("호스트명", "cmm_resource.hostname") in kept
        assert ("비고", "cmm_resource.name") in kept
        assert dropped == [("구분|분류", "cmm_resource.category")]

    def test_single_path_blocks_category_without_schema_columns(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        mapping = _cpu_form_mapping()
        mapping["구분|분류"] = "cmm_resource.category"
        state = {
            "column_mapping": mapping,
            "schema_info": _EAV_META,  # tables 정보 없음 — 검증 불가 상황
            "template_structure": {"sheets": [{"title_text": "CPU 사용현황", "name": "Sheet1"}]},
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준")
        assert "category" not in result["sql"]
        assert 'AS "호스트명"' in result["sql"]


class TestLlmFallbackMonthBlock:
    """LLM 폴백 경로의 월 리터럴 강제(라이브 5차 실측 — CURRENT_DATE 역방향 월 뒤집힘 차단)."""

    def test_block_contains_literal_months(self):
        from src.db_adapters.polestar.assembler import build_month_series_block

        ms = recognize_month_series(
            _cpu_form_mapping(), context_text="CPU 사용현황",
            user_query="2026년 6월 기준", today=_TODAY,
        )
        block = build_month_series_block(ms)
        assert f'"{_AVG}|M" ← s.stat_date = \'202601\'' in block
        assert f'"{_PEAK}|M+5" ← s.stat_date = \'202606\'' in block
        assert "CURRENT_DATE 동적 계산 금지" in block
        assert build_month_series_block(None) == ""

    async def test_multi_retry_prompt_gets_month_block(self):
        """멀티 재생성(error_context) 턴: 결정적 조립은 스킵하되 프롬프트에 월 리터럴 강제."""
        from src.nodes.multi_db_executor import _generate_sql

        class _FakeLLM:
            def __init__(self):
                self.captured = None

            async def ainvoke(self, messages):
                self.captured = messages
                return SimpleNamespace(content="SELECT 1;")

        llm = _FakeLLM()
        month_fields = [f"{_AVG}|{'M' if k == 0 else f'M+{k}'}" for k in range(6)]
        await _generate_sql(
            llm=llm,
            parsed_requirements={"original_query": "2026년 6월 기준 CPU 양식 채워줘"},
            schema_info={"tables": {"cmm_resource": {"columns": [{"name": "hostname", "type": "text"}]}}, **_EAV_META},
            sub_query_context="CPU 양식 채우기",
            default_limit=100_000,
            error_context="SQL 검증 실패: something",
            column_mapping={"호스트명": "cmm_resource.hostname"},
            db_engine="postgresql",
            db_id="",
            unmapped_fields=month_fields,
            app_config=SimpleNamespace(
                text2sql=SimpleNamespace(
                    semantic_compose=False, generic_llm_mapping=False,
                    multi_candidate=False, complexity_gate=False,
                ),
                query=SimpleNamespace(default_limit=1000),
                get_polestar_db_ids=lambda: set(),
            ),
            form_context_text="나. 주요 업무 CPU 사용현황",
            form_fill_out={},
        )
        human = llm.captured[-1].content
        assert "월별 칼럼 매핑 강제" in human
        assert "s.stat_date = '202601'" in human


class TestForeignTableRegularEntryDropped:
    """entity 외 테이블의 유효 매핑이 c.* 로 재작성되는 결함 차단 (라이브 6차 실측 확정 FIX-15).

    category는 cmm_resource_type에 **실존**하는 칼럼이라 부재-칼럼 검증(FIX-5)·화이트리스트
    (FIX-13)를 전부 통과했고, 조립기가 테이블명을 떼고 c.(entity)에 붙여 5회 연속
    `column c.category does not exist`를 만들었다.
    """

    def test_existing_column_on_other_table_dropped(self):
        from src.db_adapters.polestar.assembler import filter_pivot_regular_entries

        schema = {"tables": {
            "polestar.cmm_resource_type": {"columns": [{"name": "category", "type": "text"}]},
            "polestar.cmm_resource": {"columns": [{"name": "hostname", "type": "text"},
                                                   {"name": "name", "type": "text"}]},
        }}
        entries = [
            ("구분|분류", "cmm_resource_type.category"),  # 타 테이블 실존 칼럼 → 제외
            ("호스트명", "cmm_resource.hostname"),        # entity 칼럼 → 유지
        ]
        kept, dropped = filter_pivot_regular_entries(entries, schema, "cmm_resource")
        assert kept == [("호스트명", "cmm_resource.hostname")]
        assert ("구분|분류", "cmm_resource_type.category") in dropped

    def test_single_path_excludes_foreign_table_column(self):
        from src.nodes.query_generator import _try_build_form_fill_pivot_sql

        mapping = _cpu_form_mapping()
        mapping["구분|분류"] = "cmm_resource_type.category"
        state = {
            "column_mapping": mapping,
            "schema_info": {
                "tables": {"polestar.cmm_resource_type": {"columns": [{"name": "category", "type": "text"}]}},
                **_EAV_META,
            },
            "template_structure": {"sheets": [{"title_text": "CPU 사용현황", "name": "Sheet1"}]},
            "active_db_engine": "postgresql",
            "active_db_id": "",
        }
        result = _try_build_form_fill_pivot_sql(state, 100_000, "2026년 6월 기준")
        assert "category" not in result["sql"]
        assert 'AS "호스트명"' in result["sql"]
