"""D-201 대칭 — "이번 달" 결정적 경로가 검증기와 같은 판정을 쓴다 + 실패 사유 보존 (P-13).

2026-09-17 로컬 MLX 재현(C-06 "이번 달 CPU 사용률", 사다리 2단):
①시맨틱 컴파일러가 `cmm_metric_stat_m ... stat_date='<당월>'`을 조립 → 검증기(D-201)가 반려
②LLM 재시도는 폴백 기간 블록("월간 통계 강제 · 일별 대체 금지")을 따라 같은 SQL 반복 → 예산 소진
③`run_data_query_pipeline`이 결과 정리 뒤 실패 사유를 잃어 task가 "0건 완료"가 되고, 응답이
  "데이터가 없습니다"로 나갔다(폐쇄망 P-13 "SQL 없음 · 오류 없음 · 답변"과 같은 모양).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from src.db_adapters.polestar.validators import check_current_month_stat_table
from src.nodes.semantic_compiler import SMQ, check_coverage, compile_smq, load_semantic_model
from src.orchestration import subagents
from src.utils.query_gen_common import (
    build_stat_month_block,
    current_month_daily_range,
    resolve_stat_month_range,
)

_GP = "polestar_cm_gp"


class TestCurrentMonthDailyRange:
    def test_current_month_maps_to_first_day_through_yesterday(self):
        assert current_month_daily_range("202609", date(2026, 9, 17)) == ("20260901", "20260916")
        assert current_month_daily_range(("202609", "202609"), date(2026, 9, 17)) == (
            "20260901",
            "20260916",
        )

    def test_first_day_of_month_gives_empty_range(self):
        """매월 1일은 어제가 전월이라 범위가 비고 0행 — D-201이 정한 정답이다(h로 강하 안 함)."""
        start, end = current_month_daily_range("202610", date(2026, 10, 1))
        assert (start, end) == ("20261001", "20260930")
        assert start > end

    @pytest.mark.parametrize(
        "stat_month", [None, "", "202608", ("202607", "202609"), ("202609", "202610")]
    )
    def test_other_periods_are_not_current_month(self, stat_month):
        assert current_month_daily_range(stat_month, date(2026, 9, 17)) is None

    @pytest.mark.parametrize(
        "query",
        [
            "이번 달 CPU 사용률",
            "당월 메모리 사용률 평균",
            "지난달 CPU 사용률",
            "최근 3개월 CPU 사용률",
            "CPU 사용률",
        ],
    )
    def test_same_verdict_as_validator(self, query):
        """검증기가 반려하는 질의 = 결정적 경로가 일간으로 바꾸는 질의 (드리프트 방지)."""
        rejects_monthly = bool(
            check_current_month_stat_table("SELECT 1 FROM cmm_metric_stat_m", query)
        )
        switches_daily = current_month_daily_range(resolve_stat_month_range(query)) is not None
        assert rejects_monthly == switches_daily


class TestStatMonthBlock:
    def test_current_month_block_points_to_daily_range(self):
        block = build_stat_month_block(("202609", "202609"), today=date(2026, 9, 17))
        assert "s.stat_date BETWEEN '20260901' AND '20260916'" in block
        assert "사용하지 마세요" in block
        # 종전 블록의 월간 강제·일별 대체 금지 문구가 남으면 검증기와 다시 부딪친다.
        assert "s.stat_date = '202609'" not in block
        assert "일별(_d) 테이블로 대체하지 마세요" not in block

    def test_completed_month_block_unchanged(self):
        block = build_stat_month_block(("202608", "202608"), today=date(2026, 9, 17))
        assert "s.stat_date = '202608'" in block
        assert "시간별(_h)/일별(_d) 테이블로 대체하지 마세요." in block


class TestSemanticCompilerCurrentMonth:
    @staticmethod
    def _compile(stat_month):
        model = load_semantic_model(_GP)
        smq = SMQ.from_dict(
            {
                "pattern": "B",
                "global_aggregate": True,
                "time_grain": "month",
                "measures": [
                    {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"}
                ],
            }
        )
        assert check_coverage(smq, model).covered
        return compile_smq(smq, _GP, model, stat_month=stat_month), model

    def test_current_month_compiles_to_daily_table(self):
        today = date.today()
        sql, model = self._compile(today.strftime("%Y%m"))
        start, end = current_month_daily_range(today.strftime("%Y%m"))
        day_table = model["pattern_b"]["metric_tables"]["day"]
        month_table = model["pattern_b"]["metric_tables"]["month"]
        assert f"{day_table} s" in sql
        assert month_table not in sql
        assert f"s.stat_date BETWEEN '{start}' AND '{end}'" in sql
        # 컴파일 결과가 검증기를 통과해야 재시도 루프에 들어가지 않는다.
        assert check_current_month_stat_table(sql, "이번 달 CPU 사용률") == []

    def test_completed_month_still_uses_monthly_table(self):
        sql, model = self._compile("202606")
        assert f"{model['pattern_b']['metric_tables']['month']} s" in sql
        assert "s.stat_date = '202606'" in sql


class TestPipelineKeepsFailureReason:
    _TASK = {
        "task_id": "t1",
        "agent": "data_query",
        "sub_query": "이번 달 CPU 사용률",
        "depends_on": [],
        "input_from": [],
        "order": 1,
    }
    _SINGLE = [
        {
            "db_id": "polestar",
            "relevance_score": 1.0,
            "sub_query_context": "이번 달 CPU 사용률",
            "user_specified": False,
            "reason": "단일",
        }
    ]

    @pytest.mark.asyncio
    async def test_validation_exhaustion_survives_result_organizer(self, mock_config):
        failed = {"query_results": [], "error_message": "SQL 검증 실패: 월간 통계 반려"}
        with (
            patch.object(subagents, "classify_dbs", new=AsyncMock(return_value=self._SINGLE)),
            patch.object(subagents, "_run_single_db_pipeline", new=AsyncMock(return_value=failed)),
            patch.object(
                subagents,
                "result_organizer",
                new=AsyncMock(return_value={"organized_data": {"rows": []}, "error_message": None}),
            ),
        ):
            result = await subagents.run_data_query_pipeline(
                self._TASK,
                {"user_query": "이번 달 CPU 사용률", "parsed_requirements": {}},
                llm=AsyncMock(),
                app_config=mock_config,
            )
        assert result["error"] == "SQL 검증 실패: 월간 통계 반려"

    @pytest.mark.asyncio
    async def test_all_db_failure_survives_result_organizer(self, mock_config):
        multi = [{**self._SINGLE[0]}, {**self._SINGLE[0], "db_id": "polestar2"}]
        with (
            patch.object(subagents, "classify_dbs", new=AsyncMock(return_value=multi)),
            patch.object(
                subagents,
                "multi_db_executor",
                new=AsyncMock(return_value={"error_message": "모든 DB 쿼리가 실패했습니다."}),
            ),
            patch.object(
                subagents,
                "result_merger",
                new=AsyncMock(
                    return_value={
                        "query_results": [],
                        "error_message": "모든 DB 쿼리가 실패했습니다.",
                    }
                ),
            ),
            patch.object(
                subagents,
                "result_organizer",
                new=AsyncMock(return_value={"organized_data": {"rows": []}, "error_message": None}),
            ),
        ):
            result = await subagents.run_data_query_pipeline(
                self._TASK,
                {"user_query": "이번 달 CPU 사용률", "parsed_requirements": {}},
                llm=AsyncMock(),
                app_config=mock_config,
            )
        assert result["error"] == "모든 DB 쿼리가 실패했습니다."

    @pytest.mark.asyncio
    async def test_success_has_no_error(self, mock_config):
        ok = {"query_results": [{"hostname": "web-01"}], "error_message": None}
        with (
            patch.object(subagents, "classify_dbs", new=AsyncMock(return_value=self._SINGLE)),
            patch.object(subagents, "_run_single_db_pipeline", new=AsyncMock(return_value=ok)),
            patch.object(
                subagents,
                "result_organizer",
                new=AsyncMock(
                    return_value={
                        "organized_data": {"rows": [{"hostname": "web-01"}]},
                        "error_message": None,
                    }
                ),
            ),
        ):
            result = await subagents.run_data_query_pipeline(
                self._TASK,
                {"user_query": "서버 조회", "parsed_requirements": {}},
                llm=AsyncMock(),
                app_config=mock_config,
            )
        assert "error" not in result


class TestIdentityFilterDropGuard:
    """결정적 컴파일이 파서가 잡은 서버 지목을 빠뜨리면 폐기하고 폴백한다.

    2026-09-17 로컬 G-01 턴1 "cocm-hdkapp01 서버의 OS 확인": 파서 filter_conditions에는
    hostname이 있었는데 SMQ에 필터가 없어 54대 전체를 반환했다(조용한 오답).
    """

    _HOST = [{"field": "hostname", "op": "=", "value": "cocm-hdkapp01"}]

    class _FakeLLM:
        """서버 필터를 빠뜨린 SMQ를 낸다(재현 형태)."""

        async def ainvoke(self, messages):
            from types import SimpleNamespace

            return SimpleNamespace(
                content=(
                    '{"pattern": "A", "resource_types": ["server.Server"], '
                    '"dimensions": ["OSType"]}'
                )
            )

    def test_dropped_values_detected(self):
        from src.nodes.semantic_compiler import _dropped_identity_filters

        sql = "SELECT ... HAVING MAX(c.hostname) = 'COCM-HDKAPP01'"
        assert _dropped_identity_filters(sql, self._HOST) == []
        assert _dropped_identity_filters("SELECT 1", self._HOST) == ["cocm-hdkapp01"]
        assert _dropped_identity_filters(
            "SELECT 1", [{"field": "hostname", "op": "in", "value": ["a01", "b02"]}]
        ) == ["a01", "b02"]
        # 식별 필드가 아니면 보지 않는다(속성·기간 조건은 다른 가드 소관).
        assert _dropped_identity_filters("SELECT 1", [{"field": "OSType", "value": "Linux"}]) == []
        assert _dropped_identity_filters("SELECT 1", None) == []

    @pytest.mark.asyncio
    async def test_compile_discarded_when_host_filter_missing(self):
        from src.nodes.semantic_compiler import compile_from_nl

        sql, _smq, cov = await compile_from_nl(
            self._FakeLLM(),
            "cocm-hdkapp01 서버의 OS 확인",
            _GP,
            parsed_filters=self._HOST,
        )
        assert sql is None
        assert cov is not None and not cov.covered and "cocm-hdkapp01" in cov.reason

    @pytest.mark.asyncio
    async def test_without_parsed_filters_behaviour_unchanged(self):
        from src.nodes.semantic_compiler import compile_from_nl

        sql, _smq, cov = await compile_from_nl(self._FakeLLM(), "서버 OS 목록", _GP)
        assert sql and cov.covered

    @pytest.mark.asyncio
    async def test_prior_scope_replaces_identity_filters(self):
        """선행 스코프가 있으면 식별 필터는 스코프 HAVING이 대신한다 — 폐기하지 않는다."""
        from src.nodes.semantic_compiler import compile_from_nl

        sql, _smq, cov = await compile_from_nl(
            self._FakeLLM(),
            "그 서버들의 OS",
            _GP,
            parsed_filters=self._HOST,
            server_scope=("hostname", ["web-01"]),
        )
        assert sql and cov.covered and "web-01" in sql
