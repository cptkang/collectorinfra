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


# ──────────────────────────────────────────────
# W9 NULLS LAST 엔진 분기 (plans/137 §8.2 · D-305)
# ──────────────────────────────────────────────

_RANKING = "SELECT a, COUNT(*) AS cnt FROM t GROUP BY a ORDER BY cnt DESC LIMIT 5"


class TestNullsLastEngineBranch:
    @pytest.mark.parametrize("engine", ["mariadb", "MySQL", " mariadb "])
    def test_no_nulls_ordering_engines_unchanged(self, engine):
        from src.db_adapters.polestar.validators import ensure_ranking_nulls_last

        assert ensure_ranking_nulls_last(_RANKING, db_engine=engine) == _RANKING

    @pytest.mark.parametrize("engine", [None, "postgresql", "db2"])
    def test_polestar_engines_keep_fix(self, engine):
        from src.db_adapters.polestar.validators import ensure_ranking_nulls_last

        assert "cnt DESC NULLS LAST" in ensure_ranking_nulls_last(_RANKING, db_engine=engine)

    def test_query_generator_resolves_engine_from_registry(self):
        """그래프 경로처럼 state에 엔진이 없어도 레지스트리(`active_db_id`)에서 찾는다."""
        from src.nodes.query_generator import _dialect_engine

        assert _dialect_engine({"active_db_engine": "db2"}) == "db2"
        assert _dialect_engine({"active_db_engine": None, "active_db_id": "itam"}) == "mariadb"
        assert _dialect_engine({}) is None

    def test_all_call_sites_pass_engine(self):
        """호출부 3곳이 모두 엔진을 넘긴다 — 하나라도 빠지면 MariaDB 1064가 재발한다."""
        import importlib
        import inspect

        # `src.nodes` 패키지가 노드 함수 `query_generator`를 재노출해 `import … as`는 함수를 준다
        qg = inspect.getsource(importlib.import_module("src.nodes.query_generator"))
        mde = inspect.getsource(importlib.import_module("src.nodes.multi_db_executor"))

        assert qg.count("ensure_ranking_nulls_last(") == 1
        assert qg.count("ensure_ranking_nulls_last(sql, db_engine=_dialect_engine(state))") == 1
        assert mde.count("ensure_ranking_nulls_last(") == 2
        assert mde.count("), db_engine=db_engine)") == 1
        assert mde.count("ensure_ranking_nulls_last(sql, db_engine=db_engine)") == 1


# ──────────────────────────────────────────────
# W10 테이블 선택 요약 — 앞 15개 밖 겹침 컬럼 (plans/137 §8.3 · G-7 ⓑ)
# ──────────────────────────────────────────────


def _schema_info_obj():
    from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo

    def table(name: str, cols: list[str]) -> TableInfo:
        return TableInfo(name=name, schema_name="", columns=[
            ColumnInfo(name=c, data_type="varchar", nullable=True, is_primary_key=False,
                       is_foreign_key=False, references=None, comment=None)
            for c in cols
        ])

    schema = SchemaInfo()
    filler = [f"c{i:02d}" for i in range(44)]
    schema.tables["TAB80"] = table(
        "TAB80",
        filler + ["유지보수계약시작년월일", "유지보수계약종료년월일", "자산분류구분명"]
        + [f"d{i}" for i in range(20)],
    )
    schema.tables["TAB72"] = table("TAB72", ["시스템등록처리일시"] + [f"x{i}" for i in range(30)])
    schema.tables["TAB01"] = table("TAB01", ["유지보수계약명"])  # 15개 이하 — 이미 보인다
    return schema


class _CaptureLLM:
    def __init__(self, answer: str = "TAB80") -> None:
        self.prompts: list[str] = []
        self.answer = answer

    async def ainvoke(self, messages):
        self.prompts.append(messages[0].content)
        return SimpleNamespace(content=self.answer)


class TestSelectionSummaryMatchedColumns:
    def test_matched_hidden_columns_listed(self):
        from src.nodes.schema_analyzer import _query_matched_columns_text

        text = _query_matched_columns_text(
            _schema_info_obj(), "ITAM에서 유지보수계약 종료일이 올해 안에 끝나는 자산 보여줘",
        )
        assert "- TAB80: 유지보수계약시작년월일, 유지보수계약종료년월일" in text
        assert "TAB01" not in text and "TAB72" not in text  # 이미 보인 컬럼 · 겹침 없음

    def test_tail_particle_stripped(self):
        from src.nodes.schema_analyzer import _query_matched_columns_text

        text = _query_matched_columns_text(_schema_info_obj(), "자산분류별 자산 수")
        assert "- TAB80: 자산분류구분명" in text

    def test_no_match_is_empty(self):
        from src.nodes.schema_analyzer import _query_matched_columns_text

        assert _query_matched_columns_text(_schema_info_obj(), "server list 보여줘") == ""
        # 2자 낱말(「자산」)만으로는 겹침을 만들지 않는다(잡음 차단)
        assert _query_matched_columns_text(_schema_info_obj(), "자산 목록") == ""

    def test_per_table_cap(self):
        from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo
        from src.nodes.schema_analyzer import _MATCH_COLUMNS_PER_TABLE, _query_matched_columns_text

        cols = [f"f{i:02d}" for i in range(15)] + [f"유지보수항목{i:02d}" for i in range(30)]
        schema = SchemaInfo()
        schema.tables["W"] = TableInfo(name="W", schema_name="", columns=[
            ColumnInfo(name=c, data_type="varchar", nullable=True, is_primary_key=False,
                       is_foreign_key=False, references=None, comment=None) for c in cols
        ])
        line = _query_matched_columns_text(schema, "유지보수항목").splitlines()[-1]
        assert line.count(",") + 1 == _MATCH_COLUMNS_PER_TABLE

    async def test_prompt_prefix_unchanged_and_section_after(self):
        from src.nodes.schema_analyzer import _llm_select_relevant_tables

        query = "ITAM에서 유지보수계약 종료일이 올해 안에 끝나는 자산 보여줘"
        off, on = _CaptureLLM(), _CaptureLLM()
        await _llm_select_relevant_tables(off, _schema_info_obj(), ["자산"], query)
        await _llm_select_relevant_tables(
            on, _schema_info_obj(), ["자산"], query, match_columns=True,
        )
        before, after = off.prompts[0], on.prompts[0]
        assert "질의 단어와 이름이 겹치는 컬럼" not in before
        marker = "\n\n질의 단어와 이름이 겹치는 컬럼"
        head = after[: after.index(marker)]
        assert before.startswith(head)  # 테이블 목록 접두 불변(KV 캐시)
        assert after.replace(after[after.index(marker): after.index("\n\n사용자 질의")], "") == before

    async def test_policy_off_db_prompt_byte_identical(self):
        """G-7 ⓑ — 허용 off DB(match_columns=False)는 종전 프롬프트 그대로."""
        from src.nodes.schema_analyzer import _llm_select_relevant_tables

        a, b = _CaptureLLM(), _CaptureLLM()
        await _llm_select_relevant_tables(a, _schema_info_obj(), ["자산"], "유지보수계약 종료일")
        await _llm_select_relevant_tables(
            b, _schema_info_obj(), ["자산"], "유지보수계약 종료일", match_columns=False,
        )
        assert a.prompts == b.prompts
        assert "외 " in a.prompts[0]  # 15개 절단 표기는 종전 그대로

    def test_caller_gates_by_registry(self):
        import inspect

        import src.nodes.schema_analyzer as sa

        assert "match_columns=hangul_identifiers_allowed(db_id)" in inspect.getsource(sa)


# ──────────────────────────────────────────────
# W13 집계 결과 표의 NULL 묶음 기준 「(값 없음)」 (plans/137 §9 · D-306)
# ──────────────────────────────────────────────

_GROUP_SQL = (
    "SELECT t.`자산분류구분` AS asset_class_code, t.`자산분류구분명` AS asset_class_name, "
    "COUNT(*) AS cnt FROM TAB80 t GROUP BY t.`자산분류구분`, t.`자산분류구분명` "
    "ORDER BY cnt DESC LIMIT 1000"
)
_GROUP_ROWS = [
    {"asset_class_code": "31", "asset_class_name": "기계장치", "cnt": 2386},
    {"asset_class_code": None, "asset_class_name": None, "cnt": 1419},
    {"asset_class_code": "54", "asset_class_name": "부외자산", "cnt": 38},
]


class TestNullGroupLabel:
    def test_registry_field(self):
        registry = parse_registry({"databases": [
            {"db_id": "db_off"},
            {"db_id": "db_on", "label_null_group_keys": True},
        ]})
        assert registry.get("db_off").label_null_group_keys is False
        assert registry.get("db_on").label_null_group_keys is True
        assert get_registry().get("itam").label_null_group_keys is True
        for entry in get_registry().databases:
            if entry.family == "polestar":
                assert entry.label_null_group_keys is False, entry.db_id

    @pytest.mark.parametrize("sql, expected", [
        (_GROUP_SQL, {"asset_class_code", "asset_class_name"}),
        ("SELECT 담당부점명, SUM(취득금액) FROM TAB80 GROUP BY 담당부점명", {"담당부점명"}),
        ("SELECT t.a 부점, COUNT(*) n FROM T t GROUP BY t.a", {"부점"}),
        ("SELECT a, b FROM t", None),  # GROUP BY 없음
        ("WITH x AS (SELECT a, COUNT(*) c FROM t GROUP BY a) SELECT * FROM x", None),
        ("SELECT * FROM t GROUP BY a", None),
        ("SELECT a FROM t GROUP BY a", None),  # 집계 항목 없음
        ("SELECT a, COUNT(*) FROM (SELECT a FROM t) s GROUP BY a", {"a"}),
    ])
    def test_group_key_columns(self, sql, expected):
        from src.nodes.output_generator import group_key_columns

        got = group_key_columns(sql)
        assert (set(got) if got is not None else None) == expected

    def test_table_labels_only_null_group_keys(self):
        from src.nodes.output_generator import (
            NULL_GROUP_LABEL,
            _render_result_table,
            group_key_columns,
        )

        table = _render_result_table(
            _GROUP_ROWS, ranked=False, null_label_keys=group_key_columns(_GROUP_SQL),
        )
        assert f"| {NULL_GROUP_LABEL} | {NULL_GROUP_LABEL} | 1419 |" in table
        assert "| 31 | 기계장치 | 2386 |" in table
        assert _GROUP_ROWS[1]["asset_class_code"] is None  # 원본 행 불변(CSV·후속 질의)

    def test_no_label_when_aggregate_also_null(self):
        from src.nodes.output_generator import _label_null_group_keys

        row = {"k": None, "cnt": None}
        assert _label_null_group_keys(row, frozenset({"k"})) is row

    def test_default_table_unchanged(self):
        from src.nodes.output_generator import _render_result_table

        assert "|  |  | 1419 |" in _render_result_table(_GROUP_ROWS, ranked=False)

    @pytest.mark.parametrize("state, on", [
        ({"active_db_id": "itam", "generated_sql": _GROUP_SQL}, True),
        ({"active_db_id": "polestar_cm_gp", "generated_sql": _GROUP_SQL}, False),
        ({"active_db_id": "itam", "generated_sql": _GROUP_SQL, "is_multi_db": True}, False),
        ({"active_db_id": "itam", "generated_sql": "SELECT a FROM t"}, False),
        ({"generated_sql": _GROUP_SQL}, False),
    ])
    def test_gate(self, state, on):
        from src.nodes.output_generator import _null_group_key_columns

        assert (_null_group_key_columns(state) is not None) is on

    def test_text_response_wires_gate(self):
        import importlib
        import inspect

        src_text = inspect.getsource(importlib.import_module("src.nodes.output_generator"))
        assert "null_label_keys=_null_group_key_columns(state)" in src_text
