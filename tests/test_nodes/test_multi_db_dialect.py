"""멀티 경로 SQL 방언 그물 테스트 (D-176 · plans/82 §1.1 · SPEC-multi-dialect-guard).

배경(실측): 단일 DB 경로는 `sql_validation`이 행 제한 절을 엔진별로 자동 보정하는데
(`_add_limit_clause` — DB2는 FETCH FIRST), 멀티 DB 경로의 `_validate_sql_simple`에는
그 그물이 없다. 그래서 DB2 대상에 `LIMIT`이 나오면 **검증을 통과하고 실행 시점에
SQL0104N으로 죽으며 재생성 기회가 없다**(`graph.py:543` 무조건 전진).
사용자 보고 증상 "동시에 조회시 쿼리가 db종류에 맞게 작성이 안 된다"의 실제 지점이다.

전부 mock — LLM·네트워크·DB 미사용(D-127).
"""

from __future__ import annotations

import pytest


class TestRowLimitDialect:
    """`_check_row_limit_dialect` 순수 판정 — 오탐(위양성)을 만들지 않는 것이 핵심."""

    def _call(self, sql: str, engine: str):
        from src.nodes.multi_db_executor import _check_row_limit_dialect

        return _check_row_limit_dialect(sql, engine)

    def test_db2_with_limit_rejected(self):
        """DB2 대상에 LIMIT — 거부하고 FETCH FIRST를 안내한다."""
        err = self._call("SELECT hostname FROM polestar.cmm_resource LIMIT 100", "db2")
        assert err is not None
        assert "FETCH FIRST" in err

    def test_db2_with_fetch_first_passes(self):
        err = self._call(
            "SELECT hostname FROM POLESTAR.CMM_RESOURCE FETCH FIRST 100 ROWS ONLY", "db2"
        )
        assert err is None

    def test_postgresql_with_limit_passes(self):
        err = self._call("SELECT hostname FROM polestar.cmm_resource LIMIT 100", "postgresql")
        assert err is None

    def test_postgresql_with_fetch_first_passes(self):
        """PostgreSQL도 표준 FETCH FIRST를 지원한다 — 오탐 금지."""
        err = self._call(
            "SELECT hostname FROM polestar.cmm_resource FETCH FIRST 100 ROWS ONLY",
            "postgresql",
        )
        assert err is None

    @pytest.mark.parametrize("engine", ["db2", "postgresql"])
    def test_no_row_limit_clause_passes(self, engine):
        """행 제한 절 부재는 이 그물의 관심사가 아니다 — 기존 동작 보존."""
        assert self._call("SELECT hostname FROM cmm_resource", engine) is None

    def test_subquery_limit_not_flagged_for_db2(self):
        """서브쿼리 내부 LIMIT은 최상위 판정 대상이 아니다.

        최상위에 FETCH FIRST가 있으면 통과해야 한다 — `_strip_parenthesized` 재사용 근거.
        """
        sql = (
            "SELECT * FROM (SELECT hostname FROM t ORDER BY 1 LIMIT 10) x "
            "FETCH FIRST 5 ROWS ONLY"
        )
        assert self._call(sql, "db2") is None

    def test_string_literal_limit_not_flagged(self):
        """따옴표 안의 limit 단어에 오탐하지 않는다."""
        assert self._call("SELECT 'no limit 5 here' AS c FROM t", "db2") is None

    @pytest.mark.parametrize("engine", ["", None, "oracle", "mysql"])
    def test_unknown_engine_passes(self, engine):
        """미지·미설정 엔진은 새 실패를 만들지 않는다."""
        assert self._call("SELECT 1 FROM t LIMIT 10", engine) is None

    def test_lowercase_limit_rejected_for_db2(self):
        err = self._call("select hostname from t limit 100", "db2")
        assert err is not None


class TestValidateSqlSimpleDialect:
    """`_validate_sql_simple`이 방언 판정을 수행하되 기존 검사를 보존한다."""

    def _validate(self, sql: str, **kw):
        from src.nodes.multi_db_executor import _validate_sql_simple

        return _validate_sql_simple(sql, {"tables": {}}, **kw)

    def test_db2_limit_rejected_through_simple_validator(self):
        err = self._validate("SELECT a FROM t LIMIT 10", db_engine="db2")
        assert err is not None and "FETCH FIRST" in err

    def test_engine_omitted_keeps_current_behaviour(self):
        """db_engine 미전달 시 현행 동작 — DB2 판정이 발동하지 않는다."""
        assert self._validate("SELECT a FROM t LIMIT 10") is None

    def test_existing_checks_preserved(self):
        """기존 검사 항목은 하나도 사라지지 않는다."""
        assert "빈 SQL" in (self._validate("", db_engine="db2") or "")
        assert "SELECT" in (self._validate("DELETE FROM t", db_engine="db2") or "")
        # 금지 키워드는 SELECT 판정보다 뒤에 있으나 어느 쪽이든 거부되어야 한다
        assert self._validate("SELECT a FROM t; DROP TABLE t", db_engine="postgresql")

    def test_dialect_check_runs_after_select_check(self):
        """비-SELECT는 방언 사유가 아니라 원래 사유로 거부된다(원인 정확 노출)."""
        err = self._validate("UPDATE t SET a=1 LIMIT 1", db_engine="db2")
        assert err is not None
        assert "FETCH FIRST" not in err


class TestSyntaxErrorClassification:
    """실행 오류 중 **재생성으로 고쳐질 수 있는 것**만 골라낸다 (T3 · D-176)."""

    def _call(self, msg: str) -> bool:
        from src.nodes.multi_db_executor import _is_regenerable_exec_error

        return _is_regenerable_exec_error(Exception(msg))

    @pytest.mark.parametrize("msg", [
        'SQL0104N  An unexpected token "LIMIT" was found.',
        'ERROR: syntax error at or near "LIMIT"',
        'SQL0204N  "SDQ000.CMM_RESOURCE" is an undefined name.',
        'ERROR: column "hostnam" does not exist',
        'ERROR: relation "cmm_resourse" does not exist',
    ])
    def test_syntax_errors_are_regenerable(self, msg):
        assert self._call(msg) is True

    @pytest.mark.parametrize("msg", [
        "connection refused",
        "Read timed out",
        "MCP call timeout after 60s",
        "SSL handshake failed",
        "",
    ])
    def test_infrastructure_errors_are_not_regenerable(self, msg):
        """연결·타임아웃은 재생성해도 같다 — 토큰을 태우지 않는다."""
        assert self._call(msg) is False


class TestExecRegeneration:
    """`_run_single_target` 실행 오류 후 재생성 1회 (T3)."""

    @staticmethod
    def _run(exec_side_effects, gen_sqls):
        """execute_sql 부작용 목록과 생성 SQL 목록으로 _run_single_target을 돌린다."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        import src.nodes.multi_db_executor as mod

        client = MagicMock()
        client.execute_sql = AsyncMock(side_effect=exec_side_effects)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)

        run = MagicMock()
        run.registry.is_registered.return_value = True
        run.registry.get_client.return_value = ctx
        run.state = {"user_query": "q"}
        run.db_results, run.db_errors, run.db_schemas = {}, {}, {}
        run.all_attempts, run.validation_failed = [], {}
        run.sql_by_schema = {}
        run.parsed_requirements = {}
        run.app_config = MagicMock()

        gen = list(gen_sqls)
        with patch.object(mod, "_analyze_schema", AsyncMock(return_value={"tables": {"t": {}}})), \
             patch.object(mod, "_generate_validated_sql",
                          AsyncMock(side_effect=[(s, None) for s in gen])), \
             patch.object(mod, "_record_success", AsyncMock()) as rec_ok, \
             patch.object(mod, "_record_failure", AsyncMock()) as rec_fail, \
             patch.object(mod, "log_query_execution", AsyncMock()), \
             patch.object(mod, "get_domain_by_id",
                          MagicMock(return_value=MagicMock(db_engine="db2", db_schema="POLESTAR"))):
            asyncio.run(mod._run_single_target({"db_id": "polestar_b0"}, run))
        return run, client, rec_ok, rec_fail

    def test_syntax_error_triggers_one_regeneration(self):
        """문법 오류 → 재생성 1회 → 성공."""
        ok = MagicMock_result()
        run, client, rec_ok, rec_fail = self._run(
            [Exception('SQL0104N  An unexpected token "LIMIT" was found.'), ok],
            ["SELECT a FROM t LIMIT 10", "SELECT a FROM t FETCH FIRST 10 ROWS ONLY"],
        )
        assert client.execute_sql.await_count == 2
        assert rec_ok.await_count == 1
        assert rec_fail.await_count == 0

    def test_infra_error_does_not_regenerate(self):
        """연결 오류는 재생성하지 않는다 — 기존 실패 경로 그대로."""
        run, client, rec_ok, rec_fail = self._run(
            [Exception("connection refused")], ["SELECT a FROM t"],
        )
        assert client.execute_sql.await_count == 1
        assert rec_fail.await_count == 1

    def test_regeneration_failure_records_both(self):
        """재생성도 실패하면 원 에러와 재생성 사실이 함께 남는다(침묵 금지)."""
        run, client, rec_ok, rec_fail = self._run(
            [Exception('ERROR: syntax error at or near "LIMIT"'),
             Exception('ERROR: syntax error at or near "FETCH"')],
            ["SELECT a FROM t LIMIT 10", "SELECT a FROM t FETCH 10"],
        )
        assert client.execute_sql.await_count == 2
        assert rec_fail.await_count == 1
        assert "재생성" in (run.db_errors.get("polestar_b0") or "")


def MagicMock_result():
    """execute_sql 성공 반환값 스텁."""
    from unittest.mock import MagicMock

    r = MagicMock()
    r.rows, r.row_count = [{"a": 1}], 1
    return r
