"""output_generator 노드 테스트.

자연어 응답 생성, 빈 결과 처리, 파일 출력 분기를 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessageChunk

from src.nodes.output_generator import (
    _build_response_prompt,
    _generate_empty_result_response,
    output_generator,
)
from src.state import create_initial_state


def _make_streaming_llm(text: str) -> AsyncMock:
    """astream()으로 주어진 텍스트를 흘리는 모의 LLM을 만든다.

    output_generator는 토큰 단위 SSE 스트리밍(D-009)을 위해 llm.astream()을
    사용하므로, 테스트 모의 LLM도 async iterator를 반환해야 한다.
    """
    mock_llm = AsyncMock()

    async def _astream(messages, config=None):
        yield AIMessageChunk(content=text)

    mock_llm.astream = _astream
    return mock_llm


class TestGenerateEmptyResultResponse:
    """빈 결과 응답 생성 검증."""

    def test_basic_empty_response(self):
        """기본 빈 결과 응답을 생성한다."""
        parsed = {"query_targets": ["서버", "CPU"], "filter_conditions": []}
        response = _generate_empty_result_response(parsed)
        assert "서버" in response
        assert "CPU" in response
        assert "데이터가 없습니다" in response

    def test_empty_with_filters_suggests_alternatives(self):
        """필터가 있는 빈 결과는 대안을 제안한다."""
        parsed = {
            "query_targets": ["서버"],
            "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 99}],
        }
        response = _generate_empty_result_response(parsed)
        assert "완화" in response

    def test_empty_with_time_range_suggests_expansion(self):
        """시간 범위가 있는 빈 결과는 시간 범위 확대를 제안한다."""
        parsed = {
            "query_targets": ["서버"],
            "filter_conditions": [{"field": "usage_pct", "op": ">=", "value": 99}],
            "time_range": {"start": "2026-03-15", "end": "2026-03-15"},
        }
        response = _generate_empty_result_response(parsed)
        assert "시간 범위" in response


class TestBuildResponsePrompt:
    """응답 생성 프롬프트 구성 검증."""

    def test_includes_all_sections(self):
        """프롬프트에 질의·요약·결과 행이 포함된다."""
        prompt = _build_response_prompt(
            original_query="서버 목록",
            summary="3건 조회",
            rows=[{"hostname": "web-01"}],
        )
        assert "서버 목록" in prompt
        assert "3건 조회" in prompt
        assert "web-01" in prompt

    def test_sql_is_not_exposed_to_the_response_prompt(self):
        """★ 응답 프롬프트에 SQL을 싣지 않는다(커밋 014ed90 "응답에서 SQL 제외", 2026-06-09).

        그 커밋이 `sql` 인자와 프롬프트 섹션을 지웠는데 이 테스트 파일은 갱신되지 않아
        `sql=`을 넘기며 2개월 넘게 TypeError로 실패하고 있었다. 계약을 **강화하는 방향**으로
        되살린다 — 인자를 지우는 데 그치지 않고 SQL이 새어 들어오면 잡히게 한다.
        """
        import inspect

        assert "sql" not in inspect.signature(_build_response_prompt).parameters

        prompt = _build_response_prompt(
            original_query="서버 목록", summary="3건", rows=[{"hostname": "web-01"}],
        )
        assert "SELECT" not in prompt.upper()

    def test_truncates_large_result(self):
        """결과가 20건을 초과하면 상위 20건만 표시한다."""
        rows = [{"id": i} for i in range(50)]
        prompt = _build_response_prompt(
            original_query="test",
            summary="50건",
            rows=rows,
        )
        assert "상위 20건" in prompt


class TestNumericSummary:
    """`## 수치 요약` 블록 — 전체 rows 기준 결정적 통계 (C-10~C-12)."""

    def test_stats_computed_over_full_rows_not_preview(self):
        """20행 미리보기가 아니라 전체 50행 기준으로 최대·평균이 계산돼야 한다."""
        rows = [{"host": f"h{i}", "cpu_avg": float(i)} for i in range(50)]
        prompt = _build_response_prompt(original_query="q", summary="50건", rows=rows)
        assert "## 수치 요약" in prompt
        assert "전체 50건" in prompt
        assert "최대 49.0" in prompt  # 미리보기(상위 20행)만 보면 19.0
        assert "평균 24.5" in prompt

    def test_identifier_and_string_columns_excluded(self):
        """식별자성('리소스 ID')·문자열 칼럼은 통계에서 제외된다."""
        rows = [
            {"리소스 ID": 10, "hostname": "a", "value": 1.0},
            {"리소스 ID": 20, "hostname": "b", "value": 2.0},
        ]
        prompt = _build_response_prompt(original_query="q", summary="2건", rows=rows)
        assert "- value: 최소 1.0 · 최대 2.0 · 평균 1.5" in prompt
        assert "- 리소스 ID" not in prompt
        assert "- hostname" not in prompt

    def test_null_count_reported(self):
        """null이 섞인 칼럼은 null 건수를 명시한다(C-03 판독성)."""
        rows = [{"v": 1.0}, {"v": None}, {"v": 3.0}]
        prompt = _build_response_prompt(original_query="q", summary="3건", rows=rows)
        assert "(null 1건)" in prompt

    def test_no_numeric_columns_no_block(self):
        """숫자 칼럼이 없으면 블록 자체가 없다 — 종전 프롬프트와 동일."""
        rows = [{"hostname": "a", "os": "Linux"}]
        prompt = _build_response_prompt(original_query="q", summary="1건", rows=rows)
        assert "## 수치 요약" not in prompt

    def test_unit_rule_present_with_block(self):
        """단위 임의 부여 금지 규칙(C-10)이 블록에 실린다 — pct 별칭 허점까지 명시.

        재실측(2026-09-07): "칼럼명에 단위가 명시된 경우 허용" 문구를 LLM이 'pct' 접미를
        단위 명시로 해석해 개월 수에 %를 붙였다 — 개수 값은 칼럼명이 pct여도 % 금지로 강화.
        """
        rows = [{"months_over_40pct": 28}]
        prompt = _build_response_prompt(original_query="q", summary="1건", rows=rows)
        assert "임의로 붙이지 마세요" in prompt
        assert "pct/percent가 들어 있어도 %를 붙이지 말고" in prompt
        assert "months_over_40pct" in prompt

    def test_string_numbers_not_coerced(self):
        """문자열 숫자('4.0' — EAV 원값)는 강제 변환하지 않고 블록에서 제외한다."""
        rows = [{"core": "4.0"}, {"core": "8.0"}]
        prompt = _build_response_prompt(original_query="q", summary="2건", rows=rows)
        assert "## 수치 요약" not in prompt


class TestAllNullDegrade:
    """전 행 null 강등(C-06) — 판정과 결정적 안내 응답."""

    def _rows_all_null(self):
        return [
            {"hostname": "h1", "month": None, "cpu_avg": None},
            {"hostname": "h2", "month": None, "cpu_avg": None},
        ]

    def test_detects_all_null_value_columns(self):
        from src.nodes.output_generator import _all_null_value_columns

        assert _all_null_value_columns(self._rows_all_null()) == ["month", "cpu_avg"]

    def test_partial_values_not_degraded(self):
        """지표가 한 값이라도 채워졌으면 미발동(C-03 부분 null 보호)."""
        from src.nodes.output_generator import _all_null_value_columns

        rows = [
            {"hostname": "h1", "cpu_avg": 12.5, "month": None},
            {"hostname": "h2", "cpu_avg": None, "month": None},
        ]
        assert _all_null_value_columns(rows) is None

    def test_minor_null_column_not_degraded(self):
        """전체 칼럼의 절반 미만인 부수적 공란(빈 비고)은 미발동."""
        from src.nodes.output_generator import _all_null_value_columns

        rows = [{"hostname": "h1", "os": "Linux", "vendor": "HP", "비고": None}]
        assert _all_null_value_columns(rows) is None

    def test_empty_or_non_dict_rows_not_degraded(self):
        from src.nodes.output_generator import _all_null_value_columns

        assert _all_null_value_columns([]) is None
        assert _all_null_value_columns([("a", 1)]) is None

    def test_identifier_heavy_multi_merge_rows_degraded(self):
        """멀티 병합 표(식별 칼럼 다수)도 지표 2칼럼 전 행 null이면 발동한다.

        재실측(2026-09-07 C-06 CM): 존·서버명·호스트명 등 식별 칼럼이 많은 병합 표에서
        절반 기준이 미달해 미발동 — null 칼럼 ≥2 기준을 합집합으로 추가한 회귀 고정.
        """
        from src.nodes.output_generator import _all_null_value_columns

        rows = [
            {"존": "공동존 김포", "서버명": "s1", "hostname": "h1",
             "cpu_avg_per_server": None, "cpu_avg_overall": None},
            {"존": "공동존 여의도", "서버명": "s2", "hostname": "h2",
             "cpu_avg_per_server": None, "cpu_avg_overall": None},
        ]
        assert _all_null_value_columns(rows) == [
            "cpu_avg_per_server", "cpu_avg_overall"
        ]

    def test_single_metric_half_rule_still_degrades(self):
        """단일 지표 결과([hostname, cpu_avg])는 절반 기준으로 발동을 유지한다."""
        from src.nodes.output_generator import _all_null_value_columns

        rows = [{"hostname": "h1", "cpu_avg": None}, {"hostname": "h2", "cpu_avg": None}]
        assert _all_null_value_columns(rows) == ["cpu_avg"]

    @pytest.mark.asyncio
    async def test_xlsx_format_not_degraded(self):
        """폼필(xlsx) 동반 텍스트는 강등하지 않는다 — H-06 의도적 공란 보호."""
        state = create_initial_state(user_query="양식 채워줘")
        # 양식 첨부 턴(file_type) — 양식 없는 xlsx 요청은 텍스트 경로로 간다(plans/116 §10.3)
        state["file_type"] = "xlsx"
        state["organized_data"] = {
            "summary": "요약",
            "rows": [
                {"hostname": "h1", "TPMC": None, "도입일자": None},
                {"hostname": "h2", "TPMC": None, "도입일자": None},
            ],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "xlsx",
            "original_query": "양식 채워줘",
            "filter_conditions": [],
        }

        mock_llm = _make_streaming_llm("텍스트 응답")
        result = await output_generator(state, llm=mock_llm, app_config=MagicMock())

        assert "전 행 null이어서" not in result["final_response"]

    @pytest.mark.asyncio
    async def test_node_returns_deterministic_notice(self):
        """노드 레벨: LLM 미호출로 안내 응답을 돌려주고 목록 표를 내지 않는다."""
        state = create_initial_state(user_query="이번 달 CPU 사용률")
        state["organized_data"] = {
            "summary": "요약",
            "rows": self._rows_all_null(),
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["CPU"],
            "output_format": "text",
            "original_query": "이번 달 CPU 사용률",
            "filter_conditions": [],
        }

        result = await output_generator(state, app_config=MagicMock())

        text = result["final_response"]
        assert "2건 수행되었으나" in text
        assert "전 행 null" in text
        assert "CSV" in text
        assert "h1" not in text  # 무의미한 서버 목록 미표시
        # "이번 달" 질의 → 조회 기간에 진행 중인 달 포함 → 직전월 안내(C-06)
        assert "직전월" in text

    @pytest.mark.asyncio
    async def test_past_period_no_current_month_notice(self):
        """과거 월 질의는 강등되더라도 직전월 안내가 붙지 않는다."""
        state = create_initial_state(user_query="2025년 1월 CPU 사용률")
        state["organized_data"] = {
            "summary": "요약",
            "rows": self._rows_all_null(),
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["CPU"],
            "output_format": "text",
            "original_query": "2025년 1월 CPU 사용률",
            "filter_conditions": [],
        }

        result = await output_generator(state, app_config=MagicMock())

        assert "전 행 null" in result["final_response"]
        assert "직전월" not in result["final_response"]


class TestOutputGeneratorNode:
    """output_generator 노드 전체 동작 검증."""

    @pytest.mark.asyncio
    async def test_text_output(self):
        """텍스트 응답을 생성한다."""
        state = create_initial_state(user_query="서버 목록")
        state["organized_data"] = {
            "summary": "3건 조회",
            "rows": [{"hostname": "web-01"}],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "text",
            "original_query": "서버 목록",
        }
        state["generated_sql"] = "SELECT * FROM servers LIMIT 10"

        mock_llm = _make_streaming_llm(
            "서버 목록 조회 결과입니다. 총 1건의 서버가 있습니다."
        )

        result = await output_generator(state, llm=mock_llm, app_config=MagicMock())

        assert result["final_response"] != ""
        assert result["output_file"] is None
        assert result["current_node"] == "output_generator"
        assert result["error_message"] is None

    @pytest.mark.asyncio
    async def test_empty_results_response(self):
        """빈 결과에 대한 응답을 생성한다."""
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "text",
            "filter_conditions": [],
        }
        state["generated_sql"] = "SELECT * FROM servers WHERE 1=0"

        result = await output_generator(state, app_config=MagicMock())

        assert "데이터가 없습니다" in result["final_response"]

    @pytest.mark.asyncio
    async def test_xlsx_output_without_attached_template(self):
        """양식을 첨부하지 않은 Excel 요청 — 첨부하지 않은 양식을 "채우지 못했다"고 하지 않는다.

        plans/116 §10.3 「전체 서버 목록을 엑셀로 만들어줘」: 파일 산출은 첨부 양식 채우기뿐이라
        파일 없이 텍스트 응답 + CSV 안내가 실제 동작이다.
        """
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "3건",
            "rows": [{"hostname": "web-01"}],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "xlsx",
            "original_query": "서버 목록 엑셀로",
        }
        state["generated_sql"] = "SELECT * FROM servers LIMIT 10"

        mock_llm = _make_streaming_llm("텍스트 응답")

        result = await output_generator(state, llm=mock_llm, app_config=MagicMock())

        text = result["final_response"]
        assert "채우지 못했습니다" not in text
        assert "state에서 누락" not in text
        assert text.startswith("양식 파일이 첨부되지 않아 Excel 파일은 만들지 않았습니다.")
        assert "CSV 다운로드" in text
        assert "텍스트 응답" in text
        assert result["output_file"] is None

    @pytest.mark.asyncio
    async def test_attached_template_lost_keeps_failure_reason(self):
        """양식을 첨부했는데(file_type) 양식이 전파되지 않은 경우는 D-059 사유를 그대로 노출한다."""
        state = create_initial_state(user_query="test")
        state["file_type"] = "xlsx"
        state["organized_data"] = {
            "summary": "3건",
            "rows": [{"hostname": "web-01"}],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "xlsx",
            "original_query": "첨부한 양식에 서버 목록",
        }

        mock_llm = _make_streaming_llm("텍스트 응답")

        result = await output_generator(state, llm=mock_llm, app_config=MagicMock())

        assert "채우지 못했습니다" in result["final_response"]
        assert "사유:" in result["final_response"]
        assert result["output_file"] is None

    @pytest.mark.asyncio
    async def test_docx_without_template_no_rows_omits_csv(self):
        """행이 없으면 CSV 안내를 붙이지 않는다(CSV 다운로드가 404다)."""
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "", "rows": [], "column_mapping": None, "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "docx",
            "original_query": "서버 목록 워드로",
            "filter_conditions": [],
        }

        result = await output_generator(state, app_config=MagicMock())

        text = result["final_response"]
        assert text.startswith("양식 파일이 첨부되지 않아 Word 파일은 만들지 않았습니다.")
        assert "CSV" not in text.split("\n\n")[0]

    @pytest.mark.asyncio
    async def test_unsupported_format(self):
        """지원하지 않는 출력 형식에 대한 안내를 반환한다."""
        state = create_initial_state(user_query="test")
        state["organized_data"] = {
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "is_sufficient": True,
        }
        state["parsed_requirements"] = {
            "query_targets": ["서버"],
            "output_format": "pdf",
        }
        state["generated_sql"] = ""

        result = await output_generator(state, app_config=MagicMock())

        assert "지원하지 않는" in result["final_response"]


# === 존 커버리지 각주 (2026-09-02 폐쇄망 실측 — 존 결과 침묵 증발 교정) ===


class TestZoneCoverageNotes:
    """`_append_zone_coverage_notes` — 부분 실패·0행 존 명시 (침묵 강등 금지)."""

    def test_partial_failure_rendered(self):
        from src.nodes.output_generator import _append_zone_coverage_notes

        state = {
            "db_errors": {"polestar_b0": "DB2 connection refused"},
            "db_result_summary": {},
        }
        out = _append_zone_coverage_notes("알람 114건입니다.", state)
        assert "[일부 존 조회 실패]" in out
        assert "polestar_b0" in out
        assert out.startswith("알람 114건입니다.")

    def test_zero_row_zone_rendered_when_others_have_rows(self):
        from src.nodes.output_generator import _append_zone_coverage_notes

        state = {
            "db_errors": {},
            "db_result_summary": {
                "polestar_b0": {"row_count": 1174},
                "polestar_cm_gp": {"row_count": 0},
                "polestar_cm_yd": {"row_count": 116},
            },
        }
        out = _append_zone_coverage_notes("결과입니다.", state)
        assert "[존별 결과]" in out
        assert "polestar_cm_gp 0건" in out
        assert "polestar_b0 1,174건" in out

    def test_all_zones_have_rows_renders_counts(self):
        """정상 조회 턴(전 존 1행 이상)에도 존별 건수 1줄을 싣는다(plans/113 G-4 (가)).

        종전에는 일부 존 0행일 때만 표기했다 — 종합 결과를 존 기준으로 읽을 근거가 없었다.
        표시명은 요약의 `display_name`(레지스트리)이고, 없으면 db_id다.
        """
        from src.nodes.output_generator import _append_zone_coverage_notes

        state = {
            "db_errors": {},
            "db_result_summary": {
                "polestar_cm_gp": {"row_count": 114, "display_name": "김포 표시명"},
                "polestar_cm_yd": {"row_count": 116},
            },
        }
        assert _append_zone_coverage_notes("응답", state) == (
            "응답\n\n**[존별 결과]** 김포 표시명 114건 · polestar_cm_yd 116건"
        )

    def test_single_db_noop(self):
        """단일 DB 조회는 바이트 무변경 — 기존 응답 회귀 0."""
        from src.nodes.output_generator import _append_zone_coverage_notes

        assert _append_zone_coverage_notes("응답", {}) == "응답"
        assert _append_zone_coverage_notes(
            "응답", {"db_result_summary": {"polestar_cm_gp": {"row_count": 0}}}
        ) == "응답"

    def test_all_zones_zero_no_note(self):
        """전 존 0행은 기존 0건 안내가 담당 — 각주 중복 금지."""
        from src.nodes.output_generator import _append_zone_coverage_notes

        state = {
            "db_result_summary": {
                "polestar_cm_gp": {"row_count": 0},
                "polestar_cm_yd": {"row_count": 0},
            },
        }
        assert _append_zone_coverage_notes("조건에 해당하는 데이터가 없습니다.", state) == (
            "조건에 해당하는 데이터가 없습니다."
        )


class TestCurrentMonthPartialNote:
    """`_append_current_month_partial_note` — 진행월 stat_d 집계 기준 결정적 각주."""

    def _state(self, **over):
        base = {
            "user_query": "이번 달 서버별 CPU 사용률 보여줘",
            "routing_intent": "data_query",
            "query_results": [{"hostname": "h1", "cpu_avg": 12.5}],
            "parsed_requirements": {},
        }
        base.update(over)
        return base

    def test_current_month_metric_query_gets_note(self):
        from src.nodes.output_generator import _append_current_month_partial_note

        out = _append_current_month_partial_note("응답", self._state())
        assert "진행 중인 달" in out
        assert "당월 1일부터 어제까지" in out

    def test_past_month_query_noop(self):
        from src.nodes.output_generator import _append_current_month_partial_note

        state = self._state(user_query="지난달 서버별 CPU 사용률 보여줘")
        assert _append_current_month_partial_note("응답", state) == "응답"

    def test_alarm_query_noop(self):
        """알람은 진행월 실데이터가 정상 — 각주 오부착 금지."""
        from src.nodes.output_generator import _append_current_month_partial_note

        state = self._state(
            user_query="이번 달 CPU 임계값 초과 알람 통계",
            routing_intent="alarm_query",
        )
        assert _append_current_month_partial_note("응답", state) == "응답"

    def test_no_metric_term_noop(self):
        from src.nodes.output_generator import _append_current_month_partial_note

        state = self._state(user_query="이번 달 등록된 서버 목록")
        assert _append_current_month_partial_note("응답", state) == "응답"

    def test_all_null_rows_noop(self):
        """숫자 값이 없으면(전 행 null 강등 케이스) 자체 안내가 담당 — 이중 각주 금지."""
        from src.nodes.output_generator import _append_current_month_partial_note

        state = self._state(query_results=[{"hostname": "h1", "cpu_avg": None}])
        assert _append_current_month_partial_note("응답", state) == "응답"


class TestUnavailableMetricNotes:
    """`_append_unavailable_metric_notes` — 미수집 지표 결정적 안내 (C-02 2차 실측)."""

    def test_b0_disk_io_query_gets_note(self):
        from src.nodes.output_generator import _append_unavailable_metric_notes

        state = {
            "user_query": "지난 3개월 서버별 CPU, 메모리, 파일시스템, 디스크 IO 통계 조회",
            "active_db_id": "polestar_b0",
        }
        out = _append_unavailable_metric_notes("응답", state)
        assert out.startswith("응답")
        assert "[안내]" in out
        assert "디스크 IO 통계를 수집하지 않아" in out

    def test_b0_without_disk_terms_noop(self):
        from src.nodes.output_generator import _append_unavailable_metric_notes

        state = {"user_query": "서버별 CPU 통계", "active_db_id": "polestar_b0"}
        assert _append_unavailable_metric_notes("응답", state) == "응답"

    def test_gongjon_disk_io_noop(self):
        """공동존은 디스크 IO를 수집하므로 안내가 붙지 않는다."""
        from src.nodes.output_generator import _append_unavailable_metric_notes

        state = {"user_query": "디스크 IO 통계", "active_db_id": "polestar_cm_gp"}
        assert _append_unavailable_metric_notes("응답", state) == "응답"

    def test_db_result_summary_signal_also_detected(self):
        """멀티/오케스트레이션 경로 신호(db_result_summary)로도 대상 DB를 인식한다."""
        from src.nodes.output_generator import _append_unavailable_metric_notes

        state = {
            "user_query": "디스크 IO 보여줘",
            "db_result_summary": {"polestar_b0": {"row_count": 10}},
        }
        assert "디스크 IO 통계를 수집하지 않아" in _append_unavailable_metric_notes(
            "응답", state
        )


class TestLimitTruncationNote:
    """`_append_limit_truncation_note` — LIMIT 도달 절단 결정적 명시 (K-09)."""

    def test_at_limit_appends_note(self):
        from src.nodes.output_generator import _append_limit_truncation_note

        state = {"resolved_limit": 3, "query_results": [{}, {}, {}]}
        out = _append_limit_truncation_note("응답", state)
        assert "[안내] 결과가 조회 상한(LIMIT 3)" in out
        assert "절단" in out

    def test_below_limit_noop(self):
        from src.nodes.output_generator import _append_limit_truncation_note

        state = {"resolved_limit": 10, "query_results": [{}, {}]}
        assert _append_limit_truncation_note("응답", state) == "응답"

    def test_no_resolved_limit_noop(self):
        from src.nodes.output_generator import _append_limit_truncation_note

        assert _append_limit_truncation_note("응답", {}) == "응답"
        assert _append_limit_truncation_note(
            "응답", {"resolved_limit": None, "query_results": [{}]}
        ) == "응답"


class TestAlarmHeadline:
    """`_prepend_alarm_headline` — 결정적 헤드라인 (LLM 서술 환각 대응, 2.5차 F2)."""

    def _cfg(self, on=True):
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.text2sql.alarm_deterministic = on
        return cfg

    def test_active_headline_with_zone_counts(self):
        from src.nodes.output_generator import _prepend_alarm_headline

        state = {
            "routing_intent": "alarm_query",
            "user_query": "현재 활성 상태인 심각(severity 3) 알람 목록",
            "db_result_summary": {
                "polestar_b0": {"row_count": 1173},
                "polestar_cm_gp": {"row_count": 113},
            },
        }
        out = _prepend_alarm_headline("요약", state, self._cfg())
        head = out.splitlines()[0]
        assert "[알람 조회]" in head and "활성" in head
        assert "총 1,286건" in head and "polestar_b0 1,173건" in head
        assert out.endswith("요약")

    def test_history_headline_shows_period(self):
        from src.nodes.output_generator import _prepend_alarm_headline

        state = {
            "routing_intent": "alarm_query",
            "user_query": "2026년 1월부터 3월까지 심각 알람 이력",
            "db_result_summary": {"polestar_cm_gp": {"row_count": 403}},
        }
        head = _prepend_alarm_headline("요약", state, self._cfg()).splitlines()[0]
        assert "이력" in head and "202601~202603" in head

    def test_flag_off_noop(self):
        from src.nodes.output_generator import _prepend_alarm_headline

        state = {"routing_intent": "alarm_query", "user_query": "현재 활성 심각 알람"}
        assert _prepend_alarm_headline("요약", state, self._cfg(on=False)) == "요약"

    def test_non_alarm_or_unrecognized_noop(self):
        from src.nodes.output_generator import _prepend_alarm_headline

        assert _prepend_alarm_headline(
            "요약", {"routing_intent": "data_query", "user_query": "현재 활성 심각 알람"},
            self._cfg(),
        ) == "요약"
        assert _prepend_alarm_headline(
            "요약", {"routing_intent": "alarm_query", "user_query": "서버 목록"},
            self._cfg(),
        ) == "요약"

    def test_empty_results_noop(self):
        from src.nodes.output_generator import _prepend_alarm_headline

        state = {
            "routing_intent": "alarm_query",
            "user_query": "현재 활성 심각 알람",
            "query_results": [],
        }
        assert _prepend_alarm_headline("0건 안내", state, self._cfg()) == "0건 안내"


# --- CU-8 · LIMIT 도달 절단 고지 (P-4 부수 · 2026-09-16) ---------------------
#
# 고지 기능 자체는 있었지만 `resolved_limit` 승격에만 의존해 평범한 조회에서는
# 발화하지 못했다(라우트가 폼필·존 재선택 턴에만 싣는다). 멀티 DB는 병합 총행수를
# DB 하나치 상한과 비교해 잘린 존을 지목하지 못했고 거짓 경고도 냈다.

from src.nodes.output_generator import (  # noqa: E402
    _append_limit_truncation_note,
    _applied_row_limit,
)


def _attempt(sql: str) -> dict:
    return {"sql": sql, "success": True, "error": None, "row_count": 0, "execution_time_ms": 1.0}


def test_승격이_없어도_실행SQL에서_상한을_읽는다() -> None:
    """평범한 조회는 resolved_limit이 None이다 — 그 값에만 기대면 절단이 조용히 지나간다."""
    state = {"query_attempts": [_attempt("SELECT 1 FROM cmm_resource LIMIT 1000")]}

    assert _applied_row_limit(state) == 1000


def test_DB2_FETCH_FIRST_도_읽는다() -> None:
    state = {"query_attempts": [_attempt("SELECT 1 FROM POLESTAR.CMM_RESOURCE FETCH FIRST 500 ROWS ONLY")]}

    assert _applied_row_limit(state) == 500


def test_승격된_상한이_있으면_그것을_쓴다() -> None:
    """폼필·존 재선택 턴의 기존 동작 보존."""
    state = {"resolved_limit": 10000, "query_attempts": [_attempt("... LIMIT 20")]}

    assert _applied_row_limit(state) == 10000


def test_단일_조회가_상한에_도달하면_고지한다() -> None:
    state = {
        "query_attempts": [_attempt("SELECT 1 FROM cmm_resource LIMIT 3")],
        "query_results": [{"a": 1}, {"a": 2}, {"a": 3}],
    }

    out = _append_limit_truncation_note("본문", state)

    assert "[안내]" in out and "LIMIT 3" in out


def test_멀티DB는_잘린_존만_지목한다() -> None:
    """P-4 실측 그대로 — 10,000 / 2,813 / 10,000 중 잘린 것은 둘이다."""
    state = {
        "query_attempts": [_attempt("SELECT 1 FROM cmm_alarm LIMIT 10000")],
        "db_result_summary": {
            "polestar_b0": {"display_name": "은행존", "row_count": 10000},
            "polestar_cm_gp": {"display_name": "공동존 김포", "row_count": 2813},
            "polestar_cm_yd": {"display_name": "공동존 여의도", "row_count": 10000},
        },
    }

    out = _append_limit_truncation_note("본문", state)

    assert "은행존" in out and "공동존 여의도" in out
    assert "공동존 김포" not in out, "잘리지 않은 존을 지목하면 오독을 만든다"


def test_존별로는_미달인데_합계가_넘으면_고지하지_않는다() -> None:
    """3-DB × 4,000행 = 12,000 ≥ 10,000 — 종전 비교식이 만들던 거짓 경고."""
    state = {
        "query_attempts": [_attempt("SELECT 1 FROM cmm_resource LIMIT 10000")],
        "query_results": [{"a": i} for i in range(12000)],
        "db_result_summary": {
            "polestar_b0": {"display_name": "은행존", "row_count": 4000},
            "polestar_cm_gp": {"display_name": "공동존 김포", "row_count": 4000},
            "polestar_cm_yd": {"display_name": "공동존 여의도", "row_count": 4000},
        },
    }

    assert _append_limit_truncation_note("본문", state) == "본문"


def test_상한을_못_읽으면_no_op() -> None:
    state = {"query_attempts": [_attempt("SELECT 1 FROM cmm_resource")], "query_results": [{"a": 1}]}

    assert _append_limit_truncation_note("본문", state) == "본문"
