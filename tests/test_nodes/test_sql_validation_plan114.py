"""plans/114 P-1·P-6 — 행 제한 자동 부착의 다중문 결함 · 상수 SELECT 침묵 오답.

근거 run `20260922-112010`(폐쇄망 `general-1` 구간):

- **P-1** C-13: 생성 SQL이 `… BETWEEN 0 AND 1000;       -- 상한 게이트 (오탐 방지)`로 끝나자
  `_add_limit_clause`의 `rstrip(";")`가 세미콜론을 못 떼 `\\nLIMIT 10000;`이 **두 번째 문**이
  됐다. MCP 서버가 `읽기 전용 위반: 다중 SQL 문 감지 (2개)`로 실행을 거부했다.
- **P-6** D-03: 생성기가 `SELECT 0 AS alarm_count LIMIT 10000;`(FROM 없음)을 냈고 검증을
  통과해 사용자는 "알람 0건"을 받았다.

단일 경로(`validate_sql`)와 멀티 DB 경로(`_validate_sql_simple`·`_auto_limit_or_none`)
양쪽을 같이 고정한다(D-066 경로 대칭).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import sqlparse

from src.nodes.multi_db_executor import _auto_limit_or_none, _validate_sql_simple
from src.nodes.query_validator import query_validator
from src.sql_validation import (
    TABLELESS_SELECT_ERROR,
    _add_limit_clause,
    is_tableless_select,
    strip_sql_comments,
    validate_sql,
)
from src.state import create_initial_state

#: C-13 실측 SQL 그대로(감사 로그 `query_executed` · 행 제한 절 부착 전 형태).
C13_SQL = (
    "-- 전체 서버의 지난달 평균 CPU 사용률 (Utilization) 단일 값 조회\n"
    "SELECT\n"
    "    ROUND(AVG(s.avg_val)::numeric, 2) AS avg_cpu_utilization\n"
    "FROM polestar.cmm_resource r\n"
    "JOIN polestar.cmm_metric_stat_m s\n"
    "  ON r.id = s.resource_id\n"
    "WHERE r.resource_type = 'server.Cpus'\n"
    "  AND s.definition_name = 'Utilization'\n"
    "  AND s.stat_date = '202608'               -- 지난달(2026-08) 고정 월\n"
    "  AND r.dtime IS NULL                     -- 삭제되지 않은 리소스\n"
    "  AND s.avg_val BETWEEN 0 AND 1000;       -- 상한 게이트 (오탐 방지)"
)


def _statements(sql: str) -> int:
    """MCP 서버 `validate_readonly`와 같은 판정 — `get_type()`이 있는 문의 수."""
    return len([s for s in sqlparse.parse(sql) if s.get_type() is not None])


def _one_statement(sql: str) -> bool:
    return _statements(sql) == 1 and len([s for s in sqlparse.split(sql) if s.strip()]) == 1


# --- P-1 --------------------------------------------------------------------


class TestAddLimitClauseSingleStatement:
    def test_c13_semicolon_then_line_comment(self) -> None:
        out = _add_limit_clause(C13_SQL, 10000, "postgresql")
        assert _one_statement(out), out
        assert out.endswith("BETWEEN 0 AND 1000\nLIMIT 10000;")
        # 중간 주석은 건드리지 않는다 — 끝의 주석만 걷어낸다
        assert "-- 지난달(2026-08) 고정 월" in out
        assert "-- 삭제되지 않은 리소스" in out
        assert "상한 게이트" not in out

    def test_plain_trailing_semicolon(self) -> None:
        assert _add_limit_clause("SELECT a FROM t;", 100) == "SELECT a FROM t\nLIMIT 100;"

    def test_trailing_block_comment(self) -> None:
        out = _add_limit_clause("SELECT a FROM t; /* 끝 */", 100)
        assert _one_statement(out)
        assert out == "SELECT a FROM t\nLIMIT 100;"

    def test_repeated_semicolons_and_comments(self) -> None:
        out = _add_limit_clause("SELECT a FROM t ; -- a\n /* b */ ;\n -- c  \n", 100)
        assert _one_statement(out)
        assert out == "SELECT a FROM t\nLIMIT 100;"

    def test_double_dash_inside_literal_is_kept(self) -> None:
        sql = "SELECT a FROM t WHERE c = 'a--b'"
        out = _add_limit_clause(sql, 100)
        assert out == "SELECT a FROM t WHERE c = 'a--b'\nLIMIT 100;"
        assert _one_statement(out)

    def test_literal_with_dash_then_trailing_comment(self) -> None:
        out = _add_limit_clause("SELECT a FROM t WHERE c = 'x--y'; -- 주석", 100)
        assert out == "SELECT a FROM t WHERE c = 'x--y'\nLIMIT 100;"

    def test_db2_fetch_first(self) -> None:
        out = _add_limit_clause("SELECT a FROM POLESTAR.T;  -- 주석", 100, "db2")
        assert out == "SELECT a FROM POLESTAR.T\nFETCH FIRST 100 ROWS ONLY;"
        assert _one_statement(out)

    def test_validate_sql_auto_fix_is_one_statement(self) -> None:
        schema = {"tables": {"polestar.cmm_resource": {"columns": []},
                             "polestar.cmm_metric_stat_m": {"columns": []}}}
        outcome = validate_sql(C13_SQL, schema, db_engine="postgresql", default_limit=1000)
        assert outcome.auto_fixed_sql is not None
        assert _one_statement(outcome.auto_fixed_sql)

    def test_multi_db_path_uses_same_fix(self) -> None:
        """멀티 DB 경로도 같은 함수로 붙인다 — 한쪽만 고치는 비대칭 금지."""
        out = _auto_limit_or_none(C13_SQL, "postgresql", "", None)
        assert out is not None and _one_statement(out)
        out_db2 = _auto_limit_or_none(
            C13_SQL.replace("::numeric", ""), "db2", "", None)
        assert out_db2 is not None and _one_statement(out_db2)
        assert out_db2.endswith("FETCH FIRST 1000 ROWS ONLY;")


# --- P-6 --------------------------------------------------------------------


class TestTablelessSelect:
    @pytest.mark.parametrize("sql", [
        "SELECT 0 AS alarm_count LIMIT 10000;",          # D-03 실측
        "-- 알람 데이터는 스키마에 없으므로 0 반환\nSELECT 0 AS alarm_count;",
        "SELECT 1",
        "SELECT EXTRACT(YEAR FROM CURRENT_DATE) AS y",  # 함수 인자 FROM은 FROM 절이 아니다
        "SELECT 'from t' AS a",                          # 리터럴 안 FROM
        "-- FROM cmm_resource\nSELECT 1",                # 주석 안 FROM
    ])
    def test_rejected(self, sql: str) -> None:
        assert is_tableless_select(sql)

    @pytest.mark.parametrize("sql", [
        "SELECT hostname FROM servers",
        "SELECT * FROM (VALUES (1), (2)) v(x)",           # 범위 밖(좁게 못 박는다)
        "WITH x AS (SELECT hostname FROM servers) SELECT * FROM x",
        "WITH x AS (SELECT 1 AS n) SELECT * FROM x",      # CTE만 읽는 형태도 범위 밖
        'SELECT * FROM "POLESTAR"."CMM_RESOURCE"',        # 따옴표 식별자 테이블
        "SELECT CURRENT DATE FROM SYSIBM.SYSDUMMY1",
    ])
    def test_not_rejected(self, sql: str) -> None:
        assert not is_tableless_select(sql)

    def test_validate_sql_error(self, sample_schema_info) -> None:
        outcome = validate_sql("SELECT 0 AS alarm_count LIMIT 10000;", sample_schema_info)
        assert TABLELESS_SELECT_ERROR in outcome.errors
        assert not outcome.passed

    def test_validate_sql_passes_normal_select(self, sample_schema_info) -> None:
        outcome = validate_sql("SELECT hostname FROM servers LIMIT 10", sample_schema_info)
        assert TABLELESS_SELECT_ERROR not in outcome.errors

    def test_message_is_ascii_punctuation(self) -> None:
        """평가 하네스 cp949 콘솔 출력 대비 — em-dash 등 비ASCII 구두점 금지."""
        assert all(ord(ch) < 128 or "가" <= ch <= "힣" for ch in TABLELESS_SELECT_ERROR)

    @pytest.mark.asyncio
    async def test_node_routes_to_ordinary_retry_not_prose(self, sample_schema_info) -> None:
        """SQL이므로 산문 조기 종결(`non_sql`)이 아니라 일반 재시도 경로다."""
        state = create_initial_state(user_query="2026년 7월 심각 알람 건수")
        state["schema_info"] = sample_schema_info
        state["generated_sql"] = "SELECT 0 AS alarm_count LIMIT 10000;"
        with patch("src.nodes.query_validator.load_config") as mock_config:
            mock_config.return_value.query.default_limit = 1000
            result = await query_validator(state)
        assert result["validation_result"]["passed"] is False
        assert result["validation_result"]["non_sql"] is False
        assert "상수 SELECT" in result["validation_result"]["reason"]

    def test_multi_db_simple_validation_symmetric(self, sample_schema_info) -> None:
        assert _validate_sql_simple(
            "SELECT 0 AS alarm_count LIMIT 10000;", sample_schema_info
        ) == TABLELESS_SELECT_ERROR
        assert _validate_sql_simple(
            "SELECT hostname FROM servers LIMIT 10", sample_schema_info
        ) is None


# --- M-6 공용 함수 ------------------------------------------------------------


def test_strip_sql_comments_keeps_literals() -> None:
    sql = "-- 여의도 서버\nSELECT * FROM t WHERE loc = '여의도' /* x */ AND c = 'a--b' -- tail"
    body = strip_sql_comments(sql)
    assert "여의도 서버" not in body and "tail" not in body and "/*" not in body
    assert "'여의도'" in body and "'a--b'" in body
