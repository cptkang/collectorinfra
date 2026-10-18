"""plans/173 W1 T4 특성화 — `validate_sql`·`_validate_forbidden_joins` 현재 값 골든.

Extract Function(N5) 전에 반환 전 필드(오류·경고 목록의 순서와 문구 · 자동 보정 SQL ·
감사 신호 · 백엔드 오류 판정)와 전체 조회 상향 로그를 고정한다. 기대값은 「맞는 값」이 아니라
1ad7c161 시점의 「현재 값」이다 — 리팩터 뒤 이 값이 바뀌면 리팩터가 아니다.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

import pytest

import src.sql_validation as core
from src.sql_validation import _validate_forbidden_joins, validate_sql


def _schema() -> dict:
    """EAV 패턴(직접 조인 금지·제외 조인 컬럼·브릿지 선언)을 가진 일반화 스키마."""
    return {
        "tables": {
            "app.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "bigint"},
                    {"name": "hostname", "type": "varchar"},
                    {"name": "conf_id", "type": "bigint"},
                    {"name": "vendor_id", "type": "bigint"},
                    {"name": "dtime", "type": "timestamp"},
                ],
                "row_count_estimate": 500000,
            },
            "app.cfg_prop": {
                "columns": [
                    {"name": "id", "type": "bigint"},
                    {"name": "cfg_id", "type": "bigint"},
                    {"name": "name", "type": "varchar"},
                    {"name": "sval", "type": "varchar"},
                ]
            },
            "app.vendor": {
                "columns": [
                    {"name": "id", "type": "bigint"},
                    {"name": "label", "type": "varchar"},
                ]
            },
        },
        "_structure_meta": {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "app.cmm_resource",
                    "config_table": "app.cfg_prop",
                    "attribute_column": "name",
                    "value_column": "sval",
                    "direct_join": {"entity_column": "conf_id", "config_column": "cfg_id"},
                    "value_joins": [
                        {
                            "eav_attribute": "Hostname",
                            "eav_value_column": "sval",
                            "entity_column": "hostname",
                        }
                    ],
                    "excluded_join_columns": [
                        {"table": "app.cmm_resource", "column": "conf_id", "reason": "NULL 다수"},
                        {"table": "cmm_resource", "column": "vendor_id"},
                        {"table": "", "column": "x"},
                        {"table": "cmm_resource", "column": ""},
                    ],
                }
            ]
        },
    }


_OK_WHERE = "WHERE r.dtime IS NULL"

#: (이름, sql, kwargs) — 검사 단계마다 하나 이상 걸리게 고른다.
VALIDATE_CASES: list[tuple[str, str, dict[str, Any]]] = [
    ("backend_error", "Error from orchestrator: Input tokens must be <= 95232. Given: 96858", {}),
    ("empty", "", {}),
    ("non_select", "DELETE FROM app.cmm_resource WHERE dtime IS NULL", {}),
    ("tableless", "SELECT 1", {}),
    ("forbidden_injection", "SELECT id FROM app.vendor; DROP TABLE app.vendor -- x", {}),
    (
        "union_injection",
        "SELECT id FROM app.vendor WHERE 1=1 OR 'a'='a' UNION SELECT id FROM app.vendor",
        {},
    ),
    ("hangul_token", "SELECT 해당 id FROM app.vendor LIMIT 5", {}),
    (
        "hangul_allowed",
        'SELECT label AS "이름", 이름 FROM app.vendor LIMIT 5',
        {"allow_hangul_identifiers": True},
    ),
    ("mariadb_double_quote", 'SELECT "label" FROM vendor LIMIT 5', {"db_engine": "mariadb"}),
    ("mariadb_backtick_unknown", "SELECT `nope` FROM `vendor` LIMIT 5", {"db_engine": "mariadb"}),
    (
        "mariadb_backtick_ok",
        "SELECT `label` FROM `vendor` WHERE id = 1 LIMIT 5",
        {"db_engine": "mariadb"},
    ),
    ("missing_dtime", "SELECT r.hostname FROM app.cmm_resource r LIMIT 5", {}),
    ("unknown_table", "SELECT x FROM app.nowhere LIMIT 5", {}),
    ("unknown_column", "SELECT v.nope FROM app.vendor v WHERE v.id = 1 LIMIT 5", {}),
    ("bare_name_fallback", "SELECT label FROM vendor WHERE id = 1 LIMIT 5", {}),
    (
        "cte_excluded",
        "WITH t AS (SELECT id FROM app.vendor) SELECT id FROM t WHERE id = 1 LIMIT 5",
        {},
    ),
    (
        "forbidden_join_direct",
        "SELECT r.hostname FROM app.cmm_resource r JOIN app.cfg_prop p ON r.id = p.cfg_id "
        f"{_OK_WHERE} LIMIT 5",
        {},
    ),
    (
        "excluded_join_cols",
        "SELECT r.hostname FROM app.cmm_resource r JOIN app.cfg_prop p ON p.cfg_id = r.conf_id "
        f"JOIN app.vendor v ON r.vendor_id = v.id {_OK_WHERE} LIMIT 5",
        {},
    ),
    (
        "left_join_demotion",
        "SELECT r.hostname FROM app.cmm_resource r LEFT JOIN app.vendor v ON v.id = r.vendor_id "
        f"{_OK_WHERE} AND v.label = 'x' LIMIT 5",
        {},
    ),
    ("no_limit_pg", "SELECT label FROM app.vendor WHERE id = 1", {}),
    ("no_limit_db2", "SELECT label FROM app.vendor WHERE id = 1", {"db_engine": "db2"}),
    (
        "no_limit_all_pg",
        "SELECT label FROM app.vendor WHERE id = 1",
        {"user_query": "모든 벤더 보여줘", "default_limit": 50},
    ),
    (
        "no_limit_all_db2",
        "SELECT label FROM app.vendor WHERE id = 1",
        {"db_engine": "db2", "user_query": "전체 벤더 목록"},
    ),
    (
        "db2_fetch_first",
        "SELECT label FROM app.vendor WHERE id = 1 FETCH FIRST 5 ROWS ONLY",
        {"db_engine": "db2"},
    ),
    ("select_star_large", "SELECT * FROM app.cmm_resource r LIMIT 5", {}),
    (
        "adapter_checks",
        "SELECT label FROM app.vendor WHERE id = 1 LIMIT 5",
        {"adapter_checks": (lambda s: ["A1"], lambda s: [], lambda s: ["B1", "B2"])},
    ),
]


def _dump(outcome: core.SQLValidationOutcome) -> dict:
    return dataclasses.asdict(outcome)


#: 1ad7c161 현재 값 스냅샷(이 파일들을 갱신하는 변경은 리팩터가 아니다).
_SNAP_DIR = Path(__file__).resolve().parents[1] / "snapshots" / "plan173_t4"


def _load(name: str) -> Any:
    return json.loads((_SNAP_DIR / f"{name}.json").read_text(encoding="utf-8"))


VALIDATE_GOLDEN: dict[str, dict] = _load("validate_sql")


@pytest.mark.parametrize("name,sql,kwargs", VALIDATE_CASES, ids=[c[0] for c in VALIDATE_CASES])
def test_validate_sql_golden(name: str, sql: str, kwargs: dict, caplog) -> None:
    """각 단계의 오류·경고 순서·문구·자동 보정·감사 신호가 현재 값과 같다."""
    with caplog.at_level(logging.INFO, logger=core.logger.name):
        outcome = validate_sql(sql, _schema(), **kwargs)
    got = json.loads(json.dumps(_dump(outcome)))
    got["_logs"] = [r.getMessage() for r in caplog.records if r.name == core.logger.name]
    assert got == VALIDATE_GOLDEN[name]


def test_validate_sql_parse_exception(monkeypatch) -> None:
    """sqlparse 예외는 파싱 에러 하나만 내고 즉시 반환한다."""

    def _boom(_sql: str) -> Any:
        raise ValueError("boom")

    monkeypatch.setattr(core.sqlparse, "parse", _boom)
    outcome = validate_sql("SELECT 1", _schema())
    assert _dump(outcome) == {
        "errors": ["SQL 파싱 에러: boom"],
        "warnings": [],
        "auto_fixed_sql": None,
        "forbidden_keywords": [],
        "injection_count": 0,
        "backend_error": None,
    }


def _fj_schema(**over: Any) -> dict:
    schema = _schema()
    schema["_structure_meta"]["patterns"][0].update(over)
    return schema


_FJ_SQL = {
    "direct": "SELECT 1 FROM app.cmm_resource r JOIN app.cfg_prop p ON r.id = p.cfg_id WHERE 1=1",
    "direct_rev": "SELECT 1 FROM app.cmm_resource r JOIN app.cfg_prop p ON p.CFG_ID = r.ID LIMIT 3",
    "excluded_both": (
        "SELECT 1 FROM app.cmm_resource r JOIN app.cfg_prop p ON r.conf_id = p.cfg_id "
        "LEFT JOIN app.cfg_prop q ON q.cfg_id = r.conf_id AND q.name = 'a' "
        "JOIN app.vendor v ON v.id = r.vendor_id INNER JOIN app.vendor w ON r.vendor_id = w.id "
        "ORDER BY 1"
    ),
    "unaliased": (
        "SELECT 1 FROM app.cmm_resource JOIN app.cfg_prop "
        "ON cmm_resource.id = cfg_prop.cfg_id FETCH FIRST 1 ROWS ONLY"
    ),
    "no_on": "SELECT 1 FROM app.cmm_resource r, app.cfg_prop p WHERE r.id = p.cfg_id",
}

#: (이름, sql 키, 스키마 변형)
FJ_CASES: list[tuple[str, str, Any]] = [
    ("no_meta", "direct", lambda: {"tables": {}}),
    ("no_eav", "direct", lambda: {"_structure_meta": {"patterns": [{"type": "fk"}]}}),
    ("direct", "direct", _schema),
    ("direct_rev", "direct_rev", _schema),
    ("excluded_both", "excluded_both", _schema),
    ("unaliased", "unaliased", _schema),
    ("no_on", "no_on", _schema),
    ("no_config_column", "direct", lambda: _fj_schema(direct_join={})),
    ("no_bridge_hint", "direct", lambda: _fj_schema(value_joins=[])),
    ("no_entity", "direct", lambda: _fj_schema(entity_table="")),
    ("no_excluded", "excluded_both", lambda: _fj_schema(excluded_join_columns=[])),
]

FJ_GOLDEN: dict[str, list[str]] = _load("forbidden_joins")


@pytest.mark.parametrize("name,key,schema_fn", FJ_CASES, ids=[c[0] for c in FJ_CASES])
def test_validate_forbidden_joins_golden(name: str, key: str, schema_fn: Any) -> None:
    """금지 조인 패턴 1·2·3(정·역방향)의 오류 순서·문구가 현재 값과 같다."""
    assert _validate_forbidden_joins(_FJ_SQL[key], schema_fn()) == FJ_GOLDEN[name]
