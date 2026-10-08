"""MariaDB 백틱 식별자 참조 실존 검사 (plans/146 W1 · G-1 (b) · D-003 · D-297 · D-066).

내부망 3회차 ITAM-108·110 첫 시도는 `` FROM `tcdmsif80` AS t `` + `` t.`활성화여부` ``처럼 백틱으로
감싼 SQL이 검증기의 테이블·컬럼 실존 검사를 통째로 건너뛰어 DB 오류 1054로 실행까지 갔다.

여기서 고정하는 것:
- 백틱 테이블·별칭·한정(`` t.`c` ``·`` `t`.`c` ``·`` `tbl`.`c` ``)·비한정(단일 테이블) 컬럼을 읽는다
- 실존 판정은 선별 스키마가 아니라 조회 대상 전체 카탈로그(`CATALOG_COLUMNS_KEY`)로 한다 —
  선별 밖이어도 실존하면 통과(104·117 모양), 없는 컬럼은 그 테이블 실제 컬럼 목록을 실은 오류
- 조회 대상 밖 테이블은 「존재하지 않는 테이블 참조」
- PostgreSQL·DB2는 종전 판정·문구 그대로(카탈로그 키를 읽지 않는다)
- 한글 토큰 검사(D-297)가 잡은 이름은 참조 검사가 다시 오류로 내지 않는다
- 단일(`query_validator`·`schema_analyzer`)·멀티(`_validate_sql` 간이·full · `_analyze_schema`) 대칭

시드: `testdata/itam_bench/closed/{itam_schema.json,table_definitions.yaml}`(실제 모양).
LLM·DB는 전부 가짜다(D-127).
"""

from __future__ import annotations

import copy
import importlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from src.api.routes.db_structure import asset_sql_checker
from src.config import AppConfig, Text2SQLConfig
from src.domain.table_definitions import (
    PROFILE_KEY,
    parse_import_document,
    validate_table_definitions,
)
from src.nodes.query_validator import query_validator
from src.nodes.table_selection import catalog_columns, narrow_schema_dict
from src.sql_validation import (
    CATALOG_COLUMN_PREVIEW,
    CATALOG_COLUMNS_KEY,
    HANGUL_TOKEN_ERROR_PREFIX,
    _validate_columns,
    check_catalog_references,
    validate_sql,
)
from src.state import create_initial_state
from src.tools.validation import validate_sql_draft

mdb = importlib.import_module("src.nodes.multi_db_executor")
sa = importlib.import_module("src.nodes.schema_analyzer")

_ROOT = Path(__file__).resolve().parents[2]
_SEED_DIR = _ROOT / "testdata" / "itam_bench" / "closed"

#: 3회차 108·110 턴의 선별(프롬프트에 컬럼이 실린 테이블) 모양 — `tcdmsif80`은 선별 밖
_PICKED = ("tcdmsif72", "tcdmsif41")
_TABLE_MISSING = "존재하지 않는 테이블 참조"


@pytest.fixture(scope="module")
def seed() -> tuple[dict, dict, list[str]]:
    """(스키마, 검증한 정의, 조회 대상 목록) — 테스트마다 deepcopy해서 쓴다."""
    raw = json.loads((_SEED_DIR / "itam_schema.json").read_text(encoding="utf-8"))
    schema = raw["schema"]
    doc = yaml.safe_load((_SEED_DIR / "table_definitions.yaml").read_text(encoding="utf-8"))
    columns = {t: [c["name"] for c in info["columns"]] for t, info in schema["tables"].items()}
    defs, errors = validate_table_definitions(parse_import_document(doc), columns)
    assert errors == {}
    allowed = [
        t for t, entry in doc["tables"].items()
        if not (isinstance(entry, dict) and entry.get("allowed") is False)
    ]
    return schema, defs, allowed


def _profile(seed: tuple) -> dict:
    _schema, defs, allowed = seed
    return {"source": "manual", "allowed_tables": list(allowed), PROFILE_KEY: copy.deepcopy(defs)}


def _selected(seed: tuple, picked: tuple[str, ...] = _PICKED, *, catalog: bool = True) -> dict:
    """선별로 좁힌 스키마 — `catalog`면 조회 대상 전체 카탈로그를 싣는다(질의 경로 모양)."""
    schema = copy.deepcopy(seed[0])
    narrowed = narrow_schema_dict(schema, list(picked))
    if catalog:
        narrowed[CATALOG_COLUMNS_KEY] = catalog_columns(schema["tables"], _profile(seed))
    return narrowed


def _columns_of(seed: tuple, table: str) -> list[str]:
    return [c["name"] for c in seed[0]["tables"][table]["columns"]]


def _errors(sql: str, schema_info: dict, *, engine: str = "mariadb") -> list[str]:
    return validate_sql(
        sql, schema_info, db_engine=engine, allow_hangul_identifiers=True,
    ).errors


# 3회차 실측 모양(108 · 110 첫 시도) — `tcdmsif80`에 없는 컬럼
_SQL_108 = (
    "SELECT t.`서버호스트명`, t.`활성화여부` FROM `tcdmsif80` AS t "
    "WHERE t.`활성화여부` = 'Y' LIMIT 100"
)
_SQL_110 = (
    "SELECT t.`서버호스트명`, t.`취득년월일` FROM `tcdmsif80` AS t "
    "WHERE t.`자산상태구분명` = '사용' LIMIT 100"
)
# 104·117 모양 — 선별 밖이지만 실존하는 허용 테이블·컬럼
_SQL_104 = (
    "SELECT a.`어플리케이션명`, COUNT(*) AS `서버수` FROM `tcdmsgt82` AS a "
    "GROUP BY a.`어플리케이션명` ORDER BY `서버수` DESC LIMIT 10"
)
_SQL_117 = (
    "SELECT s.`시리얼번호`, s.`업무명`, s.`장비할당량` FROM `tcdmsif52` s "
    "WHERE s.`업무명` LIKE '%통합인증%' LIMIT 100"
)


# ──────────────────────────────────────────────
# F1 재현 — 백틱 SQL이 검사를 건너뛰지 않는다
# ──────────────────────────────────────────────


class TestBacktickExtraction:
    def test_reproduction_no_longer_silent(self, seed):
        """카탈로그가 없으면(선별 스키마만) 종전 폴스타와 같은 「존재하지 않는 테이블」이다."""
        errors = _errors(_SQL_108, _selected(seed, catalog=False))
        assert errors and errors[0].startswith(_TABLE_MISSING)
        assert "tcdmsif80" in errors[0]

    def test_unquoted_equivalent_same_verdict(self, seed):
        unquoted = _SQL_108.replace("`", "")
        quoted = _errors(_SQL_108, _selected(seed))
        assert quoted == _errors(unquoted, _selected(seed))
        assert len(quoted) == 1 and "'활성화여부'" in quoted[0]

    @pytest.mark.parametrize("sql", [
        "SELECT t.`활성화여부` FROM `tcdmsif80` AS t LIMIT 10",
        "SELECT `t`.`활성화여부` FROM `tcdmsif80` `t` LIMIT 10",
        "SELECT t.활성화여부 FROM `tcdmsif80` t LIMIT 10",
        "SELECT `tcdmsif80`.`활성화여부` FROM `tcdmsif80` LIMIT 10",
        "SELECT x.`활성화여부` FROM `itam`.`tcdmsif80` AS x LIMIT 10",
        "SELECT a.`서버호스트명` FROM `tcdmsif72` AS a "
        "JOIN `tcdmsif80` AS b ON a.`서버호스트명` = b.`서버호스트명` "
        "WHERE b.`활성화여부` = 'Y' LIMIT 10",
        "SELECT a.`서버호스트명` FROM `tcdmsif72` a "
        "LEFT JOIN `tcdmsif80` b ON a.`서버호스트명` = b.`서버호스트명` "
        "WHERE b.`활성화여부` = 'Y' LIMIT 10",
    ])
    def test_qualified_forms(self, seed, sql):
        errors = _errors(sql, _selected(seed))
        assert len(errors) == 1, errors
        assert errors[0].startswith("테이블 'tcdmsif80'에 컬럼 '활성화여부'이 존재하지 않습니다.")

    def test_alias_case_insensitive_and_column_case(self, seed):
        sql = "SELECT T.`IP주소내용`, t.`ip주소내용` FROM `tcdmsif80` AS t LIMIT 10"
        assert _errors(sql, _selected(seed)) == []

    def test_cte_and_derived_not_tables(self, seed):
        sql = (
            "WITH `srv` AS (SELECT `서버호스트명` FROM `tcdmsif80`) "
            "SELECT s.`서버호스트명` FROM `srv` s LIMIT 10"
        )
        assert _errors(sql, _selected(seed)) == []


# ──────────────────────────────────────────────
# 비한정 백틱 컬럼(단일 테이블)
# ──────────────────────────────────────────────


class TestUnqualifiedColumns:
    def test_missing_unqualified_column(self, seed):
        errors = _errors("SELECT `활성화여부` FROM `tcdmsif80` LIMIT 10", _selected(seed))
        assert len(errors) == 1 and "'활성화여부'" in errors[0]

    def test_aliased_single_table_also_checked(self, seed):
        sql = "SELECT `서버호스트명` FROM `tcdmsif80` AS t WHERE `활성화여부` = 'Y' LIMIT 10"
        errors = _errors(sql, _selected(seed))
        assert len(errors) == 1 and "'활성화여부'" in errors[0]

    @pytest.mark.parametrize("sql", [
        # 결과 별칭(AS 백틱 · AS 맨 이름 · 암묵 별칭)을 ORDER BY·HAVING에서 참조
        "SELECT `운영체제타입내용` AS os_type, COUNT(*) AS `건수` FROM `tcdmsif72` "
        "GROUP BY `운영체제타입내용` ORDER BY `건수` DESC LIMIT 10",
        "SELECT `운영체제타입내용`, COUNT(*) AS cnt FROM `tcdmsif72` "
        "GROUP BY `운영체제타입내용` HAVING `cnt` > 1 LIMIT 10",
        "SELECT `운영체제타입내용` `구분`, COUNT(*) `건수` FROM `tcdmsif72` "
        "GROUP BY `구분` ORDER BY `건수` DESC LIMIT 10",
        "SELECT CASE WHEN `활성화여부` = 'Y' THEN 1 ELSE 0 END `활성` FROM `tcdmsif72` "
        "ORDER BY `활성` LIMIT 10",
        # 함수 인자·연산자 뒤
        "SELECT COUNT(DISTINCT `서버호스트명`), SUM(`CPU개수` * 2) FROM `tcdmsif72` LIMIT 10",
        # 문자열 리터럴·큰따옴표 별칭 안의 백틱은 식별자가 아니다
        "SELECT `서버호스트명` AS \"`없는칸`\" FROM `tcdmsif72` "
        "WHERE `비고내용` = '`없는칸`' LIMIT 10",
    ])
    def test_valid_unqualified_pass(self, seed, sql):
        assert _errors(sql, _selected(seed)) == []

    def test_multi_table_unqualified_checked_against_union(self, seed):
        """참조 테이블이 둘 이상이면 비한정 컬럼은 그중 어디에든 있어야 한다(교정 1 · M1)."""
        sql = (
            "SELECT `없는칸`, `활성화여부`, `취득금액` FROM `tcdmsif72` a JOIN `tcdmsif80` b "
            "ON a.`서버호스트명` = b.`서버호스트명` LIMIT 10"
        )
        errors = _errors(sql, _selected(seed))
        assert errors == [
            "컬럼 '없는칸'이 참조 테이블(tcdmsif72, tcdmsif80) 어디에도 존재하지 않습니다 - "
            "실제 컬럼명으로 고치고 테이블 별칭으로 한정해 쓰세요(예: t.`컬럼`)."
        ]

    def test_derived_table_skips_unqualified(self, seed):
        sql = (
            "SELECT `n` FROM (SELECT COUNT(*) AS `n` FROM `tcdmsif72`) AS d LIMIT 10"
        )
        assert _errors(sql, _selected(seed)) == []


# ──────────────────────────────────────────────
# G-1 (b) — 조회 대상 전체 카탈로그
# ──────────────────────────────────────────────


class TestCatalogScope:
    @pytest.mark.parametrize("sql", [_SQL_104, _SQL_117])
    def test_out_of_selection_existing_passes(self, seed, sql):
        """104·117 모양 — 맞게 쓴 턴이 재시도로 가지 않는다."""
        assert _errors(sql, _selected(seed)) == []

    @pytest.mark.parametrize("sql, missing", [
        (_SQL_108, "활성화여부"), (_SQL_110, "자산상태구분명"),
    ])
    def test_out_of_selection_missing_column_lists_columns(self, seed, sql, missing):
        """108·110 모양 — 실행 전 오류 + 그 테이블 실제 컬럼 목록(재생성 1회에 고치게)."""
        errors = _errors(sql, _selected(seed))
        assert len(errors) == 1
        message = errors[0]
        columns = _columns_of(seed, "tcdmsif80")
        assert f"테이블 'tcdmsif80'에 컬럼 '{missing}'이 존재하지 않습니다." in message
        assert f"'tcdmsif80'의 실제 컬럼({len(columns)}개): " in message
        assert ", ".join(columns) in message, "상한 안이면 전 컬럼을 순서대로 싣는다"
        assert "취득년월일" in message and missing not in message.split("실제 컬럼")[1]

    def test_missing_columns_grouped_per_table(self, seed):
        sql = "SELECT t.`없는1`, t.`없는2`, t.`없는1` FROM `tcdmsif80` t LIMIT 10"
        errors = _errors(sql, _selected(seed))
        assert len(errors) == 1
        assert "컬럼 '없는1', '없는2'이 존재하지 않습니다." in errors[0]

    def test_column_list_capped(self):
        columns = [f"c{i:03d}" for i in range(CATALOG_COLUMN_PREVIEW + 7)]
        schema = {"tables": {"t1": {"columns": [{"name": "k"}]}},
                  CATALOG_COLUMNS_KEY: {"wide": columns}}
        errors = check_catalog_references(
            "SELECT w.`zz` FROM `wide` w", schema, db_engine="mariadb",
        )
        assert len(errors) == 1
        assert f"실제 컬럼({len(columns)}개)" in errors[0]
        assert columns[CATALOG_COLUMN_PREVIEW - 1] in errors[0]
        assert columns[CATALOG_COLUMN_PREVIEW] not in errors[0]
        assert errors[0].count(", c") == CATALOG_COLUMN_PREVIEW - 1
        assert " 외 7개 - " in errors[0]
        assert "—" not in errors[0], "ASCII 구두점(cp949 콘솔)"

    def test_outside_lookup_targets_still_rejected(self, seed):
        """조회 대상(허용 목록) 밖 테이블은 스키마에 있어도 종전대로 오류다."""
        outsider = "tcdmsam60"
        assert outsider in seed[0]["tables"], "시드 스키마에 있는 허용 제외 테이블"
        sql = f"SELECT x.`{_columns_of(seed, outsider)[0]}` FROM `{outsider}` x LIMIT 10"
        errors = _errors(sql, _selected(seed))
        assert errors == [f"{_TABLE_MISSING}: {outsider}"]

    def test_unknown_table_suppresses_column_errors(self, seed):
        sql = "SELECT a.`없는칸` FROM `tcdmsif72` a JOIN `nope` b ON a.`x` = b.`y` LIMIT 10"
        assert _errors(sql, _selected(seed)) == [f"{_TABLE_MISSING}: nope"]

    def test_selected_columns_win_over_catalog(self):
        schema = {"tables": {"db.t1": {"columns": [{"name": "a"}]}},
                  CATALOG_COLUMNS_KEY: {"t1": ["a", "b"]}}
        errors = check_catalog_references("SELECT `b` FROM `t1`", schema, db_engine="mariadb")
        assert len(errors) == 1 and "'b'" in errors[0]


# ──────────────────────────────────────────────
# 폴스타(PostgreSQL·DB2) 비영향
# ──────────────────────────────────────────────

_PG_SCHEMA = {
    "tables": {
        "polestar.cmm_resource": {"columns": [{"name": "id"}, {"name": "hostname"}]},
    },
}


class TestOtherEnginesUnchanged:
    @pytest.mark.parametrize("engine", ["postgresql", "db2", None, ""])
    def test_reference_check_is_noop(self, seed, engine):
        assert check_catalog_references(_SQL_108, _selected(seed), db_engine=engine) == []

    @pytest.mark.parametrize("engine", ["postgresql", "db2"])
    def test_backtick_sql_still_unchecked(self, seed, engine):
        """백틱은 이 엔진들의 식별자 인용이 아니다 — 종전처럼 테이블 추출이 비어 검사가 없다."""
        errors = validate_sql(_SQL_108, _selected(seed), db_engine=engine).errors
        assert not any(_TABLE_MISSING in e or "존재하지 않습니다" in e for e in errors)

    @pytest.mark.parametrize("engine", ["postgresql", "db2"])
    def test_catalog_key_ignored(self, engine):
        """카탈로그 키가 있어도 선별 밖 테이블은 종전 문구의 오류다(G-1 (b)는 MariaDB만)."""
        schema = {**_PG_SCHEMA, CATALOG_COLUMNS_KEY: {"polestar.cmm_alarm": ["alarm_id"]}}
        sql = "SELECT a.alarm_id FROM polestar.cmm_alarm a WHERE a.dtime IS NULL LIMIT 10"
        assert validate_sql(sql, schema, db_engine=engine).errors == (
            validate_sql(sql, _PG_SCHEMA, db_engine=engine).errors
        ) == [f"{_TABLE_MISSING}: polestar.cmm_alarm"]

    def test_column_error_text_unchanged(self):
        sql = "SELECT r.nope FROM polestar.cmm_resource r WHERE r.dtime IS NULL LIMIT 10"
        errors = validate_sql(sql, _PG_SCHEMA, db_engine="postgresql").errors
        assert errors == _validate_columns(sql, _PG_SCHEMA, {"polestar.cmm_resource"})
        assert errors == [
            "테이블 'polestar.cmm_resource'에 컬럼 'nope'이 존재하지 않습니다.",
            "테이블 'polestar.cmm_resource'에 컬럼 'dtime'이 존재하지 않습니다.",
        ]


# ──────────────────────────────────────────────
# D-297 한글 토큰 검사와 중복 오류 없음
# ──────────────────────────────────────────────


class TestNoDuplicateWithHangulGuard:
    def test_bare_hangul_flagged_once(self, seed):
        """백틱 없이 쓴 없는 한글 컬럼 — 한글 토큰 오류 하나만(참조 검사가 다시 내지 않는다)."""
        sql = "SELECT t.없는칸 FROM `tcdmsif80` t LIMIT 10"
        errors = _errors(sql, _selected(seed))
        assert len(errors) == 1 and errors[0].startswith(HANGUL_TOKEN_ERROR_PREFIX)

    def test_bare_hangul_out_of_selection_existing_passes(self, seed):
        """선별 밖 테이블의 실존 한글 컬럼을 백틱 없이 써도 자연어 조각으로 거부하지 않는다."""
        sql = "SELECT t.취득금액, t.경과년수 FROM tcdmsif80 t LIMIT 10"
        assert _errors(sql, _selected(seed)) == []
        # 카탈로그가 없으면(종전) 선별 스키마에 없는 한글이라 거부된다 — 통과는 카탈로그 덕분
        errors = _errors(sql, _selected(seed, catalog=False))
        assert any(e.startswith(HANGUL_TOKEN_ERROR_PREFIX) for e in errors)
        assert f"{_TABLE_MISSING}: tcdmsif80" in errors

    def test_policy_off_hangul_not_duplicated(self, seed):
        sql = "SELECT t.`서버호스트명`, t.없는칸 FROM `tcdmsif80` t LIMIT 10"
        errors = validate_sql(sql, _selected(seed), db_engine="mariadb").errors
        assert len(errors) == 1 and errors[0].startswith(HANGUL_TOKEN_ERROR_PREFIX)

    def test_backticked_missing_reported_by_reference_check_only(self, seed):
        errors = _errors("SELECT t.`없는칸` FROM `tcdmsif80` t LIMIT 10", _selected(seed))
        assert len(errors) == 1 and not errors[0].startswith(HANGUL_TOKEN_ERROR_PREFIX)


# ──────────────────────────────────────────────
# 경로 대칭(D-066) — 검증 소비처
# ──────────────────────────────────────────────


def _multi_cfg(full: bool) -> SimpleNamespace:
    return SimpleNamespace(
        text2sql=SimpleNamespace(multi_full_validation=full),
        query=SimpleNamespace(default_limit=100),
        get_polestar_db_ids=lambda: set(),
    )


class TestValidationConsumers:
    def test_multi_simple(self, seed):
        schema = _selected(seed)
        assert mdb._validate_sql_simple(
            _SQL_104, schema, db_engine="mariadb", allow_hangul_identifiers=True,
        ) is None
        error = mdb._validate_sql_simple(
            _SQL_108, schema, db_engine="mariadb", allow_hangul_identifiers=True,
        )
        assert error and "'활성화여부'" in error and "실제 컬럼" in error
        # 간이 검증은 폴스타 엔진에 테이블 실존 검사를 하지 않는다(종전 그대로)
        assert mdb._validate_sql_simple(
            "SELECT a.x FROM polestar.nope a WHERE a.dtime IS NULL LIMIT 10",
            _PG_SCHEMA, db_engine="postgresql",
        ) is None

    @pytest.mark.parametrize("full", [False, True])
    def test_multi_validate(self, seed, full):
        schema = _selected(seed)
        error, _ = mdb._validate_sql(
            _SQL_117, schema, db_id="itam", db_engine="mariadb", app_config=_multi_cfg(full),
        )
        assert error is None
        error, _ = mdb._validate_sql(
            _SQL_110, schema, db_id="itam", db_engine="mariadb", app_config=_multi_cfg(full),
        )
        assert error and "'자산상태구분명'" in error and "실제 컬럼" in error

    async def test_single_node(self, seed):
        base = {"user_query": "서버 목록", "active_db_id": "itam", "active_db_engine": "mariadb"}
        ok = await query_validator(
            {**base, "generated_sql": _SQL_104, "schema_info": _selected(seed)},
            app_config=AppConfig(),
        )
        assert ok["validation_result"]["passed"] is True
        bad = await query_validator(
            {**base, "generated_sql": _SQL_108, "schema_info": _selected(seed)},
            app_config=AppConfig(),
        )
        assert bad["validation_result"]["passed"] is False
        assert "'활성화여부'" in bad["error_message"] and "실제 컬럼" in bad["error_message"]

    def test_tool_and_asset_checker(self, seed):
        schema = _selected(seed)
        assert validate_sql_draft(_SQL_104, schema, db_engine="mariadb", db_id="itam")["valid"]
        draft = validate_sql_draft(_SQL_108, schema, db_engine="mariadb", db_id="itam")
        assert not draft["valid"] and "'활성화여부'" in draft["errors"][0]
        # 자산 검증기는 스냅샷 전체 스키마를 받는다 — 같은 코어라 백틱 SQL도 검사된다
        full = copy.deepcopy(seed[0])
        assert asset_sql_checker(_SQL_104, full, "mariadb", "itam") == []
        asset_errors = asset_sql_checker(_SQL_108, full, "mariadb", "itam")
        assert any("'활성화여부'" in e for e in asset_errors)


# ──────────────────────────────────────────────
# 경로 대칭(D-066) — 카탈로그 부착
# ──────────────────────────────────────────────


class _FakeLLM:
    """정해 둔 응답을 돌려준다(선별 LLM)."""

    def __init__(self, response: str) -> None:
        self._response = response

    async def ainvoke(self, _messages: Any, **_kw: Any) -> Any:
        return SimpleNamespace(content=self._response)


def _cache_mgr(schema: dict) -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_schema_or_fetch.return_value = (schema, True, "메모리", {}, {})
    mgr.get_db_description.return_value = "자산 관리 DB"
    mgr.get_synonyms.return_value = {}
    mgr.redis_available = False
    return mgr


def _sample_client() -> AsyncMock:
    client = AsyncMock()
    client.get_sample_data = AsyncMock(return_value=[])
    return client


def _app_cfg() -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(schema_table_select_max=8)
    return cfg


def _picks(*names: str) -> str:
    return json.dumps({"tables": list(names)}, ensure_ascii=False)


class TestCatalogAttachment:
    def test_catalog_scope_is_lookup_targets(self, seed):
        schema, _defs, allowed = seed
        catalog = catalog_columns(schema["tables"], _profile(seed))
        assert set(catalog) == set(allowed) & set(schema["tables"])
        assert "tcdmsam60" not in catalog
        assert catalog["tcdmsif80"] == _columns_of(seed, "tcdmsif80")

    def test_catalog_without_allowed_tables_is_whole_schema(self, seed):
        schema = seed[0]
        assert set(catalog_columns(schema["tables"], {})) == set(schema["tables"])

    async def test_single_and_multi_attach_same_catalog(self, seed):
        schema = seed[0]

        @asynccontextmanager
        async def _ctx(_client):
            yield _sample_client()

        state = dict(create_initial_state(user_query="서버 목록"))
        state["active_db_id"] = "app_db"
        state["active_db_engine"] = "mariadb"  # 1·2단 subagents가 채우는 값
        state["parsed_requirements"] = {"query_targets": ["서버"], "original_query": "서버 목록"}
        mgr = _cache_mgr(copy.deepcopy(schema))
        with patch.object(sa, "get_db_client", return_value=_ctx(None)), \
             patch.object(sa, "get_cache_manager", return_value=mgr), \
             patch.object(sa, "_load_manual_profile", return_value=_profile(seed)):
            single = await sa.schema_analyzer(
                state, llm=_FakeLLM(_picks(*_PICKED)), app_config=_app_cfg(),
            )
        with patch("src.schema_cache.cache_manager.get_cache_manager",
                   return_value=_cache_mgr(copy.deepcopy(schema))), \
             patch.object(sa, "_load_manual_profile", return_value=_profile(seed)):
            multi = await mdb._analyze_schema(
                _sample_client(), {"original_query": "서버 목록"}, db_id="app_db",
                app_config=_app_cfg(), sub_query_context="서버 목록",
                llm=_FakeLLM(_picks(*_PICKED)), selection_sink={},
            )
        expected = catalog_columns(schema["tables"], _profile(seed))
        for out in (single["schema_info"], multi):
            assert list(out["tables"]) == list(_PICKED), "선별 자체는 바뀌지 않는다(G-2 (c))"
            assert out[CATALOG_COLUMNS_KEY] == expected
            assert _errors(_SQL_104, out) == []
            assert "실제 컬럼" in _errors(_SQL_108, out)[0]
        assert "tcdmsif80" in schema["tables"] and CATALOG_COLUMNS_KEY not in schema, (
            "캐시 공유 객체는 바꾸지 않는다"
        )

    @pytest.mark.parametrize("engine", [None, "postgresql"])
    async def test_single_skips_catalog_when_validator_engine_not_backtick(self, seed, engine):
        """교정 1 L2 — 3·4단 그래프(엔진 미설정 → 검증기 폴백)는 카탈로그를 싣지 않는다."""
        schema = seed[0]

        @asynccontextmanager
        async def _ctx(_client):
            yield _sample_client()

        state = dict(create_initial_state(user_query="서버 목록"))
        state["active_db_id"] = "app_db"
        state["active_db_engine"] = engine
        state["parsed_requirements"] = {"query_targets": ["서버"], "original_query": "서버 목록"}
        mgr = _cache_mgr(copy.deepcopy(schema))
        with patch.object(sa, "get_db_client", return_value=_ctx(None)), \
             patch.object(sa, "get_cache_manager", return_value=mgr), \
             patch.object(sa, "_load_manual_profile", return_value=_profile(seed)):
            out = await sa.schema_analyzer(
                state, llm=_FakeLLM(_picks(*_PICKED)), app_config=_app_cfg(),
            )
        assert list(out["schema_info"]["tables"]) == list(_PICKED)
        assert CATALOG_COLUMNS_KEY not in out["schema_info"]

    async def test_no_definitions_no_catalog(self, seed):
        """정의 기반 선별이 아닌 DB(폴스타 등)는 스키마 딕셔너리 모양이 종전 그대로다."""
        schema = seed[0]
        mgr = _cache_mgr(copy.deepcopy(schema))
        mgr.get_applied_structure_meta.return_value = None
        with patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr), \
             patch.object(sa, "_load_manual_profile", return_value=None):
            multi = await mdb._analyze_schema(
                _sample_client(), {"original_query": "서버 목록"}, db_id="app_db",
                app_config=_app_cfg(), sub_query_context="서버 목록",
                llm=_FakeLLM(_picks(*_PICKED)), selection_sink={},
            )
        assert CATALOG_COLUMNS_KEY not in multi


# ──────────────────────────────────────────────
# 독립 검증(verifier · plans/146 W1) — 실제 ITAM SQL 모양 · 누출 · 실제 조회 대상 · 잔여 결함 고정
# ──────────────────────────────────────────────

_ORACLE_DIR = _ROOT / "testdata" / "scenarios" / "oracles"
_REAL_PROFILE = _ROOT / "config" / "db_profiles" / "itam.yaml"


def _closed_oracles() -> list[Path]:
    """폐쇄망 스키마 모양 오라클(ITAM-1NN · ITAM-146-P*).

    샌드박스 ITAM-0x·1x는 스키마가 달라 뺀다.
    """
    import re as _re

    return sorted(
        p for p in _ORACLE_DIR.glob("ITAM-*.mariadb.sql")
        if _re.match(r"ITAM-(1\d\d|146-P\w+)\.mariadb\.sql$", p.name)
    )


def _oracle_sql(path: Path) -> str:
    import re as _re

    return _re.sub(r":\w+", "CURDATE()", path.read_text(encoding="utf-8"))


def _k2_items() -> list[dict]:
    doc = yaml.safe_load((_SEED_DIR / "knowledge" / "examples.yaml").read_text(encoding="utf-8"))
    return list(doc["items"])


def _real_profile() -> dict:
    return yaml.safe_load(_REAL_PROFILE.read_text(encoding="utf-8"))


class TestVerifierRealShapes:
    """실제 ITAM SQL(K2 예시 · 신규 오라클) — 선별(72·41) 밖이어도 단일·멀티에서 통과."""

    def test_oracle_inventory(self):
        assert len(_closed_oracles()) >= 15 and len(_k2_items()) == 3

    @pytest.mark.parametrize("path", _closed_oracles(), ids=lambda p: p.name)
    def test_oracles_pass(self, seed, path):
        sql = _oracle_sql(path)
        schema = _selected(seed)
        assert _errors(sql, schema) == []
        assert mdb._validate_sql_simple(
            sql, schema, db_engine="mariadb", allow_hangul_identifiers=True,
        ) is None

    @pytest.mark.parametrize("item", _k2_items(), ids=lambda i: i["id"])
    def test_k2_examples_pass(self, seed, item):
        schema = _selected(seed)
        assert _errors(item["sql"], schema) == []
        assert mdb._validate_sql_simple(
            item["sql"], schema, db_engine="mariadb", allow_hangul_identifiers=True,
        ) is None

    @pytest.mark.parametrize("sql", [
        "SELECT t.`서버호스트명` AS `호스트`, COUNT(*) AS `건수` FROM `tcdmsif80` t "
        "GROUP BY `호스트` ORDER BY `건수` DESC LIMIT 10",
        "SELECT COUNT(*) `건수` FROM `tcdmsif80` ORDER BY `건수` LIMIT 10",
        "SELECT (`취득금액` * 2) `두배` FROM `tcdmsif80` ORDER BY `두배` LIMIT 10",
        "SELECT CASE WHEN `취득금액` > 0 THEN 1 ELSE 0 END `플래그` FROM `tcdmsif80` "
        "ORDER BY `플래그` LIMIT 10",
        "SELECT `서버호스트명` AS host FROM `tcdmsif80` ORDER BY `host` LIMIT 10",
        "SELECT `서버호스트명`, ROW_NUMBER() OVER (PARTITION BY `IP주소내용` "
        "ORDER BY `서버호스트명`) AS rn FROM `tcdmsif80` LIMIT 10",
        "SELECT GROUP_CONCAT(DISTINCT `IP주소내용` SEPARATOR ',') AS ips FROM `tcdmsif80` LIMIT 10",
        "SELECT CAST(`취득금액` AS DECIMAL(18,2)) AS amt FROM `tcdmsif80` LIMIT 10",
        "SELECT `서버호스트명` FROM `tcdmsif80` WHERE `유지보수계약종료년월일` < "
        "DATE_FORMAT(DATE_ADD(CURDATE(), INTERVAL 3 MONTH), '%Y%m%d') LIMIT 10",
        "SELECT `서버호스트명` FROM `tcdmsif80` WHERE `서버호스트명` IN "
        "(SELECT `서버호스트명` FROM `tcdmsif72`) LIMIT 10",
        "SELECT a.`서버호스트명` FROM `tcdmsif80` a WHERE EXISTS (SELECT 1 FROM `tcdmsif72` b "
        "WHERE b.`서버호스트명` = a.`서버호스트명`) LIMIT 10",
        "SELECT `x`.`서버호스트명` FROM (SELECT `서버호스트명` FROM `tcdmsif80`) AS `x` LIMIT 10",
        "-- `활성화여부` 칸은 없음\nSELECT `서버호스트명` FROM `tcdmsif80` LIMIT 10",
        "SELECT `tcdmsif80`.`서버호스트명` FROM `itam`.`tcdmsif80` LIMIT 10",
        "SELECT `t`.* FROM `tcdmsif80` `t` LIMIT 1",
        "SELECT `서버호스트명` AS `호스트 명` FROM `tcdmsif80` ORDER BY `호스트 명` LIMIT 10",
    ])
    def test_valid_shapes_not_rejected(self, seed, sql):
        assert _errors(sql, _selected(seed)) == []


class TestVerifierNoLeak:
    """카탈로그 키는 검증 전용 — 프롬프트·벤치 trace 모양에 실리지 않는다(토큰 예산)."""

    def test_prompt_renders_identical(self, seed):
        from src.nodes.query_generator import _format_schema_for_prompt

        with_catalog = _selected(seed)
        without = _selected(seed, catalog=False)
        assert mdb._format_schema(with_catalog) == mdb._format_schema(without)
        assert _format_schema_for_prompt(with_catalog) == _format_schema_for_prompt(without)
        assert "tcdmsif80" not in mdb._format_schema(with_catalog)

    def test_bench_trace_shape_identical(self, seed):
        from scripts.itam_bench._serve import _schema_shape

        assert _schema_shape(_selected(seed), None) == _schema_shape(
            _selected(seed, catalog=False), None,
        )


class TestVerifierRealScope:
    """실제 프로필(`config/db_profiles/itam.yaml`) 조회 대상 — 기본 제외 tcdmsif81은 밖."""

    def _schema(self, seed: tuple) -> dict:
        schema = copy.deepcopy(seed[0])
        narrowed = narrow_schema_dict(schema, list(_PICKED))
        narrowed[CATALOG_COLUMNS_KEY] = catalog_columns(schema["tables"], _real_profile())
        return narrowed

    def test_excluded_table_rejected_without_column_list(self, seed):
        schema = self._schema(seed)
        assert "tcdmsif81" not in schema[CATALOG_COLUMNS_KEY]
        errors = _errors("SELECT a.`계정명` FROM `tcdmsif81` a LIMIT 1", schema)
        assert errors == [f"{_TABLE_MISSING}: tcdmsif81"]
        assert not any("계정비밀번호" in e for e in errors)

    def test_error_carries_names_not_values(self, seed):
        sql = (
            "SELECT t.`서버호스트명` FROM `tcdmsif80` AS t "
            "WHERE t.`활성화여부` = 'SECRET-VALUE-9' LIMIT 10"
        )
        errors = _errors(sql, self._schema(seed))
        assert errors and "'활성화여부'" in errors[0]
        assert not any("SECRET" in e for e in errors)


# ── 잔여 결함 고정(strict xfail — 고치면 XPASS로 실패해 표지를 걷어 내게 한다) ──


class TestVerifierDefectsFixed:
    """교정 1에서 고친 결함(H1 (a)~(d) · M1) — strict xfail에서 일반 테스트로 옮겼다."""

    def test_literal_with_alias_dot(self, seed):
        """H1 (a) 문자열 리터럴 안 `별칭.단어`를 컬럼 참조로 읽지 않는다."""
        sql = (
            "SELECT t.`서버호스트명` FROM `tcdmsif80` t "
            "WHERE t.`IP주소내용` LIKE 't.abc%' LIMIT 10"
        )
        assert _errors(sql, _selected(seed)) == []

    def test_literal_then_implicit_backtick_alias(self, seed):
        """H1 (b) 리터럴 뒤 AS 없는 백틱 별칭을 컬럼 자리로 보지 않는다."""
        sql = "SELECT `서버호스트명`, '-' `비고` FROM `tcdmsif80` LIMIT 10"
        assert _errors(sql, _selected(seed)) == []

    def test_implicit_plain_alias_backtick_ref(self, seed):
        """H1 (c) AS 없는 무백틱 별칭의 백틱 재참조를 컬럼으로 보지 않는다."""
        sql = (
            "SELECT `IP주소내용`, COUNT(*) cnt FROM `tcdmsif80` "
            "GROUP BY `IP주소내용` HAVING `cnt` > 1 LIMIT 10"
        )
        assert _errors(sql, _selected(seed)) == []

    def test_comma_join_derived_table(self, seed):
        """H1 (d) 콤마 조인 파생 테이블도 파생 소스로 본다."""
        sql = (
            "SELECT `서버호스트명`, `mx` FROM `tcdmsif80` a, "
            "(SELECT MAX(`취득금액`) mx FROM `tcdmsif80`) b LIMIT 10"
        )
        assert _errors(sql, _selected(seed)) == []

    def test_unquoted_unqualified_missing_column(self, seed):
        """M1 무백틱 비한정 한글 컬럼도 단일 테이블과 대조한다(F1 모양의 무백틱 변형)."""
        sql = "SELECT 서버호스트명, 활성화여부 FROM tcdmsif80 LIMIT 10"
        errors = _errors(sql, _selected(seed, ("tcdmsif41",)))
        assert len(errors) == 1
        assert errors[0].startswith("테이블 'tcdmsif80'에 컬럼 '활성화여부'이 존재하지 않습니다.")

    @pytest.mark.parametrize("sql", [
        # 무백틱 한글 결과 별칭(AS)을 ORDER BY에서 참조 — AS 없는 한글 암묵 별칭은 종전부터
        # D-297이 거부한다(이번 변경과 무관)
        "SELECT 운영체제타입내용 AS 구분, COUNT(*) AS 건수 FROM tcdmsif72 "
        "GROUP BY 운영체제타입내용 ORDER BY 건수 DESC LIMIT 10",
        "SELECT '-' AS 비고란, 서버호스트명 FROM tcdmsif72 ORDER BY 비고란 LIMIT 10",
        "SELECT 운영체제타입내용, COUNT(*) cnt FROM tcdmsif72 "
        "GROUP BY 운영체제타입내용 ORDER BY cnt DESC LIMIT 10",
        # 하위 질의(참조 테이블 둘) — 각 컬럼이 참조 테이블 어딘가에 있다
        "SELECT 서버호스트명 FROM tcdmsif72 WHERE 물품고유번호 IN "
        "(SELECT 물품고유번호 FROM tcdmsif41 WHERE 자산상태구분명 = '사용') LIMIT 10",
    ])
    def test_unquoted_hangul_valid_shapes_pass(self, seed, sql):
        assert _errors(sql, _selected(seed)) == []

    def test_unquoted_multi_table_missing_column(self, seed):
        """M1 — 참조 테이블이 여럿이어도 어디에도 없는 무백틱 한글 컬럼은 거부한다(기준선 이상)."""
        sql = (
            "SELECT 서버호스트명 FROM tcdmsif80 WHERE 물품고유번호 IN "
            "(SELECT 물품고유번호 FROM tcdmsif43 WHERE 활성화여부 = 'Y') LIMIT 10"
        )
        errors = _errors(sql, _selected(seed, ("tcdmsif41",)))
        assert len(errors) == 1 and "'활성화여부'이 참조 테이블" in errors[0]


class TestVerifierKnownDefects:

    @pytest.mark.xfail(strict=True, reason="잔여 ① — 3·4단 그래프는 엔진 폴백으로 검사 미발동")
    async def test_graph_path_engine_fallback(self, seed):
        state = {
            "user_query": "서버 목록", "active_db_id": "itam",
            "generated_sql": _SQL_108, "schema_info": _selected(seed),
        }
        out = await query_validator(state, app_config=AppConfig())
        assert out["validation_result"]["passed"] is False


# ──────────────────────────────────────────────
# 재검증(verifier · 교정 1) — 여러 테이블 비한정 대조 · L2 엔진 결합
# ──────────────────────────────────────────────


class TestVerifierRecheckMultiTable:
    """참조 테이블이 여럿일 때 비한정 컬럼을 합집합과 대조해도 정상 SQL이 거절되지 않는다."""

    @pytest.mark.parametrize("sql", [
        # 상관 하위 질의 · EXISTS · NOT EXISTS · IN 하위 질의(백틱 · 무백틱 한글)
        "SELECT a.`서버호스트명` FROM `tcdmsif80` a WHERE EXISTS (SELECT 1 FROM `tcdmsif72` b "
        "WHERE `활성화여부` = 'Y' AND b.`서버호스트명` = a.`서버호스트명`) LIMIT 10",
        "SELECT a.서버호스트명 FROM tcdmsif80 a WHERE EXISTS (SELECT 1 FROM tcdmsif72 b "
        "WHERE 활성화여부 = 'Y' AND b.서버호스트명 = a.서버호스트명) LIMIT 10",
        "SELECT 서버호스트명 FROM tcdmsif80 a WHERE NOT EXISTS (SELECT 1 FROM tcdmsif72 b "
        "WHERE b.서버호스트명 = a.서버호스트명) LIMIT 10",
        "SELECT 서버호스트명, 취득금액 FROM tcdmsif80 WHERE 서버호스트명 IN "
        "(SELECT 서버호스트명 FROM tcdmsif72 WHERE 활성화여부 = 'Y') LIMIT 10",
        "SELECT a.`서버호스트명`, (SELECT COUNT(*) FROM `tcdmsif72` b WHERE b.`서버호스트명` = "
        "a.`서버호스트명`) AS `건수` FROM `tcdmsif80` a ORDER BY `건수` DESC LIMIT 10",
        # JOIN … USING · NATURAL JOIN · SELECT * · a.*
        "SELECT `서버호스트명`, a.`취득금액`, b.`활성화여부` FROM `tcdmsif80` a JOIN `tcdmsif72` b "
        "USING (`서버호스트명`, `IP주소내용`) LIMIT 10",
        "SELECT DISTINCT 서버호스트명 FROM tcdmsif80 a "
        "JOIN tcdmsif72 b USING (서버호스트명) LIMIT 10",
        "SELECT * FROM `tcdmsif80` NATURAL JOIN `tcdmsif72` LIMIT 10",
        "SELECT a.*, b.`활성화여부` FROM `tcdmsif80` a LEFT JOIN `tcdmsif72` b "
        "ON a.`서버호스트명` = b.`서버호스트명` LIMIT 10",
        # 비한정 컬럼이 서로 다른 조인 테이블에 있다
        "SELECT 취득금액, 활성화여부 FROM tcdmsif80 a JOIN tcdmsif72 b "
        "ON a.서버호스트명 = b.서버호스트명 LIMIT 10",
        "SELECT a.`서버호스트명`, `물품명` FROM `tcdmsif80` a JOIN `tcdmsif72` b "
        "ON a.`서버호스트명` = b.`서버호스트명` LEFT JOIN `tcdmsif41` c "
        "ON c.`물품분류번호` = a.`물품분류번호` LIMIT 10",
        # 결과 별칭 재참조(백틱 · 한글 · 암묵 별칭 · 큰따옴표)
        "SELECT b.운영체제타입내용 AS 운영체제, COUNT(*) "
        "AS 서버수 FROM tcdmsif80 a JOIN tcdmsif72 b "
        "ON a.서버호스트명 = b.서버호스트명 GROUP BY 운영체제 ORDER BY 서버수 DESC LIMIT 10",
        "SELECT b.`운영체제타입내용` os, COUNT(*) n FROM `tcdmsif80` a JOIN `tcdmsif72` b "
        "ON a.`서버호스트명` = b.`서버호스트명` GROUP BY `os` ORDER BY `n` LIMIT 10",
        "SELECT CASE WHEN b.활성화여부 = 'Y' THEN "
        "'활성' ELSE '비활성' END AS 상태, COUNT(*) AS 건수 "
        "FROM tcdmsif80 a JOIN tcdmsif72 b ON "
        "a.서버호스트명 = b.서버호스트명 GROUP BY 상태 LIMIT 10",
        # 한글 테이블 별칭 · 소문자 키워드 · 줄바꿈 · 리터럴 이스케이프 · 윈도
        "SELECT 서버.서버호스트명 FROM tcdmsif80 서버 JOIN tcdmsif72 원장 "
        "ON 서버.서버호스트명 = 원장.서버호스트명 LIMIT 10",
        "select 서버호스트명 from tcdmsif80 where "
        "취득금액 is not null order by 취득금액 desc limit 10",
        "SELECT\n  서버호스트명,\n  취득금액\nFROM tcdmsif80\nWHERE 취득금액 > 0\nLIMIT 10",
        "SELECT 서버호스트명 FROM tcdmsif80 WHERE 담당부점명 = 'O''Reilly 팀' LIMIT 10",
        "SELECT 서버호스트명, RANK() OVER (PARTITION BY 담당부점명 ORDER BY 취득금액 DESC) AS 순위 "
        "FROM tcdmsif80 LIMIT 10",
        "SELECT 담당부점명, GROUP_CONCAT(서버호스트명 "
        "ORDER BY 서버호스트명 SEPARATOR ', ') AS 서버들 "
        "FROM tcdmsif80 GROUP BY 담당부점명 LIMIT 10",
    ])
    def test_multi_table_valid_shapes_pass(self, seed, sql):
        schema = _selected(seed)
        assert _errors(sql, schema) == []
        assert mdb._validate_sql_simple(
            sql, schema, db_engine="mariadb", allow_hangul_identifiers=True,
        ) is None

    @pytest.mark.parametrize("sql", [
        "SELECT a.`서버호스트명`, b.`활성화여부` FROM `tcdmsif80` a, `tcdmsif72` b "
        "WHERE a.`서버호스트명` = b.`서버호스트명` LIMIT 10",
        "SELECT a.서버호스트명, 활성화여부 FROM tcdmsif80 a, tcdmsif72 b "
        "WHERE a.서버호스트명 = b.서버호스트명 LIMIT 10",
    ])
    def test_comma_join_with_aliases(self, seed, sql):
        """교정 2 H2 — 별칭 붙은 콤마 조인의 둘째 테이블도 참조 테이블로 읽는다."""
        assert _errors(sql, _selected(seed)) == []

    def test_comma_join_second_table_checked(self, seed):
        """교정 2 H2 — 콤마 조인 둘째 테이블도 실존·컬럼 검사를 받는다."""
        missing = (
            "SELECT a.`서버호스트명`, b.`없는칸` FROM `tcdmsif80` a, `tcdmsif72` b "
            "WHERE a.`서버호스트명` = b.`서버호스트명` LIMIT 10"
        )
        errors = _errors(missing, _selected(seed))
        assert len(errors) == 1
        assert errors[0].startswith("테이블 'tcdmsif72'에 컬럼 '없는칸'이 존재하지 않습니다.")
        outsider = (
            "SELECT a.`서버호스트명` FROM `tcdmsif80` a, `tcdmsam60` b "
            "WHERE a.`서버호스트명` = b.`x` LIMIT 10"
        )
        assert _errors(outsider, _selected(seed)) == [f"{_TABLE_MISSING}: tcdmsam60"]


class TestVerifierRecheckEngineCoupling:
    def test_registry_engine_is_backtick_for_itam(self):
        """L2 결합 — 2단 subagents는 레지스트리 엔진을 `active_db_engine`에 싣고, 단일 경로는
        그 값이 백틱 엔진일 때만 카탈로그를 싣는다. 레지스트리가 바뀌면 G-1 (b)가 조용히 꺼진다."""
        from src.routing.domain_config import get_domain_by_id
        from src.sql_validation import _uses_backtick_quotes

        domain = get_domain_by_id("itam")
        assert domain is not None and _uses_backtick_quotes(domain.db_engine)
