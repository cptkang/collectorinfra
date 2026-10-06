"""DB별 한글 식별자 허용 정책 회귀 테스트 (plans/137 · D-104 개정).

한글 물리 컬럼을 쓰는 DB(레지스트리 `allow_hangul_identifiers: true`)는 SQL 구조 영역의 한글을
**스키마에 실재하는 이름·SQL 안에서 선언한 별칭일 때만** 통과시킨다 — LLM이 남긴 자연어 조각
(D-104 원 증상)은 계속 거부한다. 허용 off DB는 종전 판정·문구와 비트 동일하다(G-4).

MariaDB는 백틱을 식별자 인용으로 인정하고(W2), 기본 sql_mode(ANSI_QUOTES 없음 — 운영 실측
2026-10-06)에서 큰따옴표로 감싼 컬럼명은 문자열 리터럴이 되는 침묵 오답이라 거부한다(W3).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from src.api.routes.db_structure import asset_sql_checker
from src.config import AppConfig
from src.nodes.multi_db_executor import _validate_sql, _validate_sql_simple
from src.nodes.query_validator import query_validator
from src.routing.registry import (
    get_registry,
    hangul_identifiers_allowed,
    parse_registry,
)
from src.schema_cache.db_registration_service import (
    SERVER_VARIABLE_SQL,
    DBRegistrationService,
    _hangul_column_count,
)
from src.sql_validation import (
    DOUBLE_QUOTED_IDENTIFIER_ERROR_PREFIX,
    HANGUL_SCHEMA_MISSING_WARNING,
    HANGUL_TOKEN_ERROR_PREFIX,
    check_double_quoted_identifiers,
    check_hangul_tokens,
    find_bare_hangul_tokens,
    validate_sql,
)
from src.tools.validation import validate_sql_draft
from tests.test_schema_cache.test_plan104_service_fixtures import (
    FakeTable,
    make_env,
    make_registry,
)

# 가상 한글 컬럼 스키마 — 테이블명은 영문(운영 ITAM 형태 · G-5), 컬럼명은 한글
_TABLE = "TAB01"
_SCHEMA: dict[str, Any] = {
    "tables": {
        _TABLE: {"columns": [
            {"name": "자산번호"}, {"name": "자산명"}, {"name": "취득_금액2"}, {"name": "자산상태"},
        ]},
    },
}
_LEGACY_MESSAGE_TAIL = (
    "따옴표 안 별칭/문자열 리터럴 외의 한글은 모두 제거하고 완전한 SQL로 다시 작성하세요."
)


def _ok(sql: str, *, engine: str = "mariadb", allow: bool = True) -> bool:
    return validate_sql(
        sql, _SCHEMA, db_engine=engine, allow_hangul_identifiers=allow,
    ).passed


# ──────────────────────────────────────────────
# W1 레지스트리
# ──────────────────────────────────────────────


class TestRegistryField:
    def test_default_false_and_explicit_true(self):
        registry = parse_registry({"databases": [
            {"db_id": "db_off", "engine": "postgresql"},
            {"db_id": "db_on", "engine": "mariadb", "allow_hangul_identifiers": True},
        ]})
        assert registry.get("db_off").allow_hangul_identifiers is False
        assert registry.get("db_on").allow_hangul_identifiers is True

    def test_runtime_registry_values(self):
        """정본 레지스트리: ITAM만 켜고 폴스타 계열은 미선언(= 현행)이다."""
        registry = get_registry()
        assert registry.get("itam").allow_hangul_identifiers is True
        for entry in registry.databases:
            if entry.family == "polestar":
                assert entry.allow_hangul_identifiers is False, entry.db_id

    @pytest.mark.parametrize("db_id", [None, "", "no_such_db", "polestar_cm_gp"])
    def test_resolver_closed_by_default(self, db_id):
        assert hangul_identifiers_allowed(db_id) is False

    def test_resolver_itam(self):
        assert hangul_identifiers_allowed("itam") is True


# ──────────────────────────────────────────────
# 허용 off DB — 현행 비트 동일(G-4)
# ──────────────────────────────────────────────


class TestOffUnchanged:
    def test_tokens_and_message_identical(self):
        sql = f"SELECT 자산명, 취득_금액2 FROM {_TABLE} LIMIT 10"
        assert find_bare_hangul_tokens(sql) == ["자산명", "취득", "금액"]
        errors, warnings = check_hangul_tokens(sql, _SCHEMA, db_engine="postgresql")
        assert warnings == []
        assert errors == [f"{HANGUL_TOKEN_ERROR_PREFIX}: 금액, 자산명, 취득 - {_LEGACY_MESSAGE_TAIL}"]

    def test_postgresql_backtick_still_rejected(self):
        """백틱 인정은 MariaDB·MySQL만이다 — PostgreSQL 판정은 종전 그대로."""
        assert not _ok(f"SELECT `자산명` FROM {_TABLE} LIMIT 10", engine="postgresql", allow=False)

    def test_postgresql_double_quote_not_flagged(self):
        """PostgreSQL에서 큰따옴표는 정상 식별자 인용이라 W3 가드가 걸리지 않는다."""
        assert check_double_quoted_identifiers(
            f'SELECT "자산명" FROM {_TABLE}', _SCHEMA, db_engine="postgresql",
        ) == []


# ──────────────────────────────────────────────
# W2 · W4 허용 on DB
# ──────────────────────────────────────────────


class TestAllowedDB:
    @pytest.mark.parametrize("sql", [
        f"SELECT 자산명, 취득_금액2 FROM {_TABLE} WHERE 자산상태 = '운영' LIMIT 10",
        f"SELECT `자산명`, `취득_금액2` FROM {_TABLE} LIMIT 10",
        f"SELECT t.자산명 AS 자산이름 FROM {_TABLE} t ORDER BY 자산이름 LIMIT 10",
        f"SELECT 자산명 FROM {_TABLE} 자산 WHERE 자산.자산상태 = 'x' LIMIT 10",
        f'SELECT 자산명 AS "자산 이름" FROM {_TABLE} LIMIT 10',
    ])
    def test_real_identifiers_and_declared_aliases_pass(self, sql):
        assert _ok(sql)

    @pytest.mark.parametrize("sql, token", [
        (f"SELECT 자산명 FROM {_TABLE} WHERE 해당 자산상태 = '운영' LIMIT 10", "해당"),
        (f"SELECT 자산명을 FROM {_TABLE} LIMIT 10", "자산명을"),
        (f"SELECT * FROM {_TABLE} WHERE 자산상태 = 현재 LIMIT 10", "현재"),
    ])
    def test_natural_language_residue_rejected(self, sql, token):
        outcome = validate_sql(sql, _SCHEMA, db_engine="mariadb", allow_hangul_identifiers=True)
        assert not outcome.passed
        message = next(e for e in outcome.errors if e.startswith(HANGUL_TOKEN_ERROR_PREFIX))
        assert token in message and "스키마에 없는 한글" in message and "백틱" in message

    def test_nfc_normalization(self):
        import unicodedata

        decomposed = unicodedata.normalize("NFD", "자산명")
        assert _ok(f"SELECT {decomposed} FROM {_TABLE} LIMIT 10")

    def test_missing_schema_skips_with_warning(self):
        outcome = validate_sql(
            f"SELECT 자산명 FROM {_TABLE} LIMIT 10", {},
            db_engine="mariadb", allow_hangul_identifiers=True,
        )
        assert outcome.passed
        assert HANGUL_SCHEMA_MISSING_WARNING in outcome.warnings

    def test_snapshot_column_mapping_shape(self):
        """스냅샷(`columns`가 사전)에서 만든 스키마도 대조 근거가 된다."""
        schema = {"tables": {_TABLE: {"columns": {"자산명": {"type": "varchar"}}}}}
        errors, _ = check_hangul_tokens(
            f"SELECT 자산명 FROM {_TABLE}", schema, db_engine="mariadb",
            allow_hangul_identifiers=True,
        )
        assert errors == []


# ──────────────────────────────────────────────
# W3 MariaDB 큰따옴표 식별자
# ──────────────────────────────────────────────


class TestDoubleQuotedIdentifier:
    @pytest.mark.parametrize("sql", [
        f'SELECT "자산명" FROM {_TABLE} LIMIT 10',
        f"SELECT * FROM {_TABLE} WHERE \"자산상태\" = '운영' LIMIT 10",
    ])
    def test_quoted_column_rejected(self, sql):
        outcome = validate_sql(sql, _SCHEMA, db_engine="mariadb", allow_hangul_identifiers=True)
        assert any(e.startswith(DOUBLE_QUOTED_IDENTIFIER_ERROR_PREFIX) for e in outcome.errors)

    def test_rejected_regardless_of_policy(self):
        """영문 컬럼도 같은 침묵 오답이라 정책과 무관하게 MariaDB 전체에 건다."""
        schema = {"tables": {_TABLE: {"columns": [{"name": "hostName"}]}}}
        assert check_double_quoted_identifiers(
            f'SELECT "hostName" FROM {_TABLE}', schema, db_engine="mariadb",
        )

    @pytest.mark.parametrize("sql", [
        f'SELECT 자산명 AS "자산명" FROM {_TABLE} LIMIT 10',
        f'SELECT 자산명 as  "자산명" FROM {_TABLE} LIMIT 10',
        f"SELECT 자산명 FROM {_TABLE} WHERE 자산명 = '\"자산명\"' LIMIT 10",
        f'SELECT 자산명 FROM {_TABLE} -- "자산명"\nLIMIT 10',
    ])
    def test_alias_literal_and_comment_allowed(self, sql):
        assert check_double_quoted_identifiers(sql, _SCHEMA, db_engine="mariadb") == []

    def test_unknown_quoted_text_not_flagged(self):
        """스키마 이름이 아닌 큰따옴표 내용은 판단하지 않는다(문자열 의도일 수 있다)."""
        assert check_double_quoted_identifiers(
            f'SELECT 자산명 FROM {_TABLE} WHERE 자산상태 = "운영"', _SCHEMA, db_engine="mariadb",
        ) == []


# ──────────────────────────────────────────────
# W5 경로 대칭 (D-066)
# ──────────────────────────────────────────────

_GOOD = f"SELECT 자산명 FROM {_TABLE} WHERE 자산상태 = '운영' LIMIT 10"
_BAD = f"SELECT 자산명 FROM {_TABLE} WHERE 해당 자산상태 = '운영' LIMIT 10"
_QUOTED = f'SELECT "자산명" FROM {_TABLE} LIMIT 10'


def _multi_cfg(full: bool) -> SimpleNamespace:
    return SimpleNamespace(
        text2sql=SimpleNamespace(multi_full_validation=full),
        query=SimpleNamespace(default_limit=100),
        get_polestar_db_ids=lambda: set(),
    )


class TestPathSymmetry:
    def test_multi_simple(self):
        assert _validate_sql_simple(
            _GOOD, _SCHEMA, db_engine="mariadb", allow_hangul_identifiers=True,
        ) is None
        assert _validate_sql_simple(
            _BAD, _SCHEMA, db_engine="mariadb", allow_hangul_identifiers=True,
        ).startswith(HANGUL_TOKEN_ERROR_PREFIX)
        assert _validate_sql_simple(
            _QUOTED, _SCHEMA, db_engine="mariadb", allow_hangul_identifiers=True,
        ).startswith(DOUBLE_QUOTED_IDENTIFIER_ERROR_PREFIX)
        # 정책 미전달 = 현행(한글 컬럼도 거부)
        assert _validate_sql_simple(_GOOD, _SCHEMA, db_engine="mariadb") is not None

    @pytest.mark.parametrize("full", [False, True])
    def test_multi_validate_resolves_registry(self, full):
        error, _ = _validate_sql(
            _GOOD, _SCHEMA, db_id="itam", db_engine="mariadb", app_config=_multi_cfg(full),
        )
        assert error is None
        error, _ = _validate_sql(
            _BAD, _SCHEMA, db_id="itam", db_engine="mariadb", app_config=_multi_cfg(full),
        )
        assert error and HANGUL_TOKEN_ERROR_PREFIX in error
        error, _ = _validate_sql(
            _GOOD, _SCHEMA, db_id="other_db", db_engine="mariadb", app_config=_multi_cfg(full),
        )
        assert error and HANGUL_TOKEN_ERROR_PREFIX in error

    @pytest.mark.parametrize("db_id, passed", [("itam", True), ("polestar_cm_gp", False)])
    async def test_single_node(self, db_id, passed):
        state = {
            "generated_sql": _GOOD, "schema_info": _SCHEMA, "user_query": "자산 목록",
            "active_db_id": db_id, "db_engine": "mariadb",
        }
        result = await query_validator(state, app_config=AppConfig())
        assert result["validation_result"]["passed"] is passed

    def test_tool(self):
        assert validate_sql_draft(_GOOD, _SCHEMA, db_engine="mariadb", db_id="itam")["valid"]
        assert not validate_sql_draft(_GOOD, _SCHEMA, db_engine="mariadb", db_id="x")["valid"]

    def test_asset_checker(self):
        assert asset_sql_checker(_GOOD, _SCHEMA, "mariadb", "itam") == []
        assert asset_sql_checker(_GOOD, _SCHEMA, "mariadb") != []


# ──────────────────────────────────────────────
# W8 설정 조각 자동 제안
# ──────────────────────────────────────────────


class TestSnippetSuggestion:
    def test_count_helper(self):
        record = {"snapshot": {"tables": {
            "A": {"columns": {"자산명": {}, "id": {}}},
            "B": {"columns": {"취득_금액2": {}}},
        }}}
        assert _hangul_column_count(record) == 2
        assert _hangul_column_count(None) == 0
        assert _hangul_column_count({"snapshot": {"tables": {"A": {"columns": {"id": {}}}}}}) == 0

    @pytest.mark.parametrize("hangul, expected", [(True, True), (False, None)])
    async def test_snippet_field(self, tmp_path, monkeypatch, hangul, expected):
        source = "acme_src"
        columns = (
            [("자산번호", "varchar", False, True), ("자산명", "varchar", True, False)] if hangul
            else [("asset_no", "varchar", False, True), ("asset_nm", "varchar", True, False)]
        )
        env = make_env(tmp_path, monkeypatch)
        env.session.add_source(source, "mariadb", {"TAB01": FakeTable(columns)})
        env.session.sql_rows = _sql_rows
        service = DBRegistrationService(
            env.config, env.mgr, **{**env.service_kwargs(), "registry_getter": make_registry}
        )
        await service.run_register(
            source, steps=["probe", "schema"], tables=None, by="admin", ctx=env.ctx,
        )

        snippet = await service.config_snippets(source)

        item = yaml.safe_load(snippet["registry_yaml"])["databases"][0]
        assert item.get("allow_hangul_identifiers") is expected
        assert parse_registry({"databases": [item]}).get(source).allow_hangul_identifiers is bool(
            expected
        )
        assert any("한글 컬럼이 2개" in n for n in snippet["notes"]) is hangul


def _sql_rows(source: str, sql: str) -> list[dict[str, Any]]:
    if sql == SERVER_VARIABLE_SQL["mariadb"]:
        return [{"version": "11.4.0-MariaDB", "sql_mode": "STRICT_TRANS_TABLES",
                 "lower_case_table_names": 0, "collation_server": "utf8mb4_general_ci"}]
    raise RuntimeError(f"예상하지 못한 SQL: {sql}")
