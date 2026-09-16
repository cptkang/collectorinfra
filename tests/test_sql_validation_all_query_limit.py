"""전체 조회 질의의 행 상한 자동 보정 (plans/98 CU-2).

종전 `validate_sql`은 "모든/전체" 질의에서 행 제한 절 자동 추가를 **통째로 생략**했다.
반면 같은 판정(`has_all_scope_keyword`)을 쓰는 `resolve_query_limit`은 `_ALL_QUERY_LIMIT`
(10,000)으로 **상향**한다 — 두 경로가 정반대였고, LLM이 LIMIT을 빼고 생성하면 아무도
되돌려 놓지 않아 무제한 실행됐다(J-03 실측 1,668행).

여기서 고정하는 계약:
1. 전체 조회 질의도 행 제한 절이 없으면 **반드시** 보정된다 — 생략 없음.
2. 보정 값은 `resolve_query_limit`과 **같은 상수**(`_ALL_QUERY_LIMIT`)다 — 새 상수 금지.
3. 방언 분기는 `_add_limit_clause`가 그대로 처리한다 — PostgreSQL `LIMIT n` /
   DB2 `FETCH FIRST n ROWS ONLY` **양쪽을 실제로 단언**한다.
4. 상향분은 경고 문구로 구별돼 드러난다(침묵 보정 금지).
"""

import pytest

from src.sql_validation import validate_sql
from src.utils.query_gen_common import _ALL_QUERY_LIMIT, resolve_query_limit

DEFAULT_LIMIT = 1000

_SQL_NO_LIMIT = "SELECT r.id, r.name FROM cmm_resource r WHERE r.dtime IS NULL"


@pytest.fixture
def schema_info() -> dict:
    """행 제한 검사만 태우기 위한 최소 스키마."""
    return {
        "tables": {
            "cmm_resource": {
                "columns": [
                    {"name": "id", "type": "integer"},
                    {"name": "name", "type": "varchar(255)"},
                    {"name": "dtime", "type": "timestamp"},
                ],
            },
        },
    }


class TestAllQueryLimitRaised:
    """전체 조회 질의 = 생략이 아니라 상향."""

    def test_postgresql_all_query_gets_ceiling_limit(self, schema_info):
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="postgresql",
            user_query="모든 서버 조회",
            default_limit=DEFAULT_LIMIT,
        )
        assert outcome.auto_fixed_sql is not None, "전체 조회 질의도 보정을 생략하면 안 된다"
        assert f"LIMIT {_ALL_QUERY_LIMIT};" in outcome.auto_fixed_sql
        assert "FETCH FIRST" not in outcome.auto_fixed_sql
        assert f"LIMIT {DEFAULT_LIMIT};" not in outcome.auto_fixed_sql

    def test_db2_all_query_gets_ceiling_fetch_first(self, schema_info):
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="db2",
            user_query="전체 서버 조회",
            default_limit=DEFAULT_LIMIT,
        )
        assert outcome.auto_fixed_sql is not None
        assert f"FETCH FIRST {_ALL_QUERY_LIMIT} ROWS ONLY;" in outcome.auto_fixed_sql
        assert "LIMIT" not in outcome.auto_fixed_sql

    def test_all_query_warning_states_the_raise(self, schema_info):
        """상향은 경고로 구별돼 드러난다 — 기본 상한 보정과 같은 문구면 안 된다."""
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="postgresql",
            user_query="모든 서버 조회",
            default_limit=DEFAULT_LIMIT,
        )
        raised = [w for w in outcome.warnings if "상향" in w]
        assert raised, f"상향 사실을 알리는 경고가 없다: {outcome.warnings}"
        assert str(_ALL_QUERY_LIMIT) in raised[0]
        assert str(DEFAULT_LIMIT) in raised[0]


class TestDefaultLimitUnchanged:
    """일반 질의 경로는 종전 그대로(회귀 방어)."""

    def test_postgresql_default_limit(self, schema_info):
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="postgresql",
            user_query="김포 서버 목록 보여줘",
            default_limit=DEFAULT_LIMIT,
        )
        assert f"LIMIT {DEFAULT_LIMIT};" in (outcome.auto_fixed_sql or "")
        assert not [w for w in outcome.warnings if "상향" in w]

    def test_db2_default_limit(self, schema_info):
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="db2",
            user_query="김포 서버 목록 보여줘",
            default_limit=DEFAULT_LIMIT,
        )
        assert f"FETCH FIRST {DEFAULT_LIMIT} ROWS ONLY;" in (outcome.auto_fixed_sql or "")

    @pytest.mark.parametrize(
        ("sql", "engine"),
        [
            (f"{_SQL_NO_LIMIT} LIMIT 50", "postgresql"),
            (f"{_SQL_NO_LIMIT} FETCH FIRST 50 ROWS ONLY", "db2"),
        ],
    )
    def test_existing_row_limit_is_not_touched(self, schema_info, sql, engine):
        """이미 행 제한이 있으면 전체 조회 질의여도 보정하지 않는다."""
        outcome = validate_sql(
            sql,
            schema_info,
            db_engine=engine,
            user_query="모든 서버 조회",
            default_limit=DEFAULT_LIMIT,
        )
        assert outcome.auto_fixed_sql is None

    def test_derivative_form_is_not_treated_as_all_query(self, schema_info):
        """"전체적으로 …"는 전체 조회가 아니다(Plan 67 R3-(iii) 경계 유지)."""
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="postgresql",
            user_query="전체적으로 CPU 높은 서버",
            default_limit=DEFAULT_LIMIT,
        )
        assert f"LIMIT {DEFAULT_LIMIT};" in (outcome.auto_fixed_sql or "")


class TestCeilingSharedWithResolver:
    """검증기와 `resolve_query_limit`이 같은 상수를 쓴다(새 상수 신설 금지)."""

    @pytest.mark.parametrize(
        "user_query", ["모든 서버 조회", "전체 서버", "서버별 CPU 사용률", "김포 서버 목록"]
    )
    def test_applied_ceiling_matches_resolver(self, schema_info, user_query):
        expected = resolve_query_limit(user_query, DEFAULT_LIMIT)
        outcome = validate_sql(
            _SQL_NO_LIMIT,
            schema_info,
            db_engine="postgresql",
            user_query=user_query,
            default_limit=DEFAULT_LIMIT,
        )
        assert f"LIMIT {expected};" in (outcome.auto_fixed_sql or "")

    def test_validator_reuses_shared_constant(self):
        import src.sql_validation as core

        assert core._ALL_QUERY_LIMIT == _ALL_QUERY_LIMIT
