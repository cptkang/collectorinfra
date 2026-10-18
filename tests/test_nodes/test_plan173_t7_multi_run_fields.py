"""plans/173 T7 특성화 — `_MultiRun` 선언 분리(입력/누적) 전후 동일성.

기대값은 착수 시점(1ad7c161) `_MultiRun`의 현재 값이다 — 필드 이름 집합·기본값·
키워드 생성·기본값 컨테이너의 인스턴스 독립성.
"""

from __future__ import annotations

import dataclasses

from src.nodes import multi_db_executor as mdb

_NO_DEFAULT = (
    "state", "llm", "app_config", "registry", "parsed_requirements", "effective_limit",
    "unmapped_fields", "prior_block", "prior_scope", "value_index", "db_results",
    "db_schemas", "db_errors", "all_attempts", "mc_candidates", "mc_derivations",
    "sql_by_schema", "validation_failed", "form_context", "form_intent", "mapping_sources",
    "form_fill_answers", "form_fill_out",
)
_DEFAULTS = {
    "prior_scope_by_db": None,
    "skipped_dbs": [],
    "dependency_notes": [],
    "db_sqls": {},
    "regen_stops": {},
    "table_selections": {},
    "gen_elapsed": {},
    "template_assembly": {},
    "query_time": None,
}


def _kwargs() -> dict:
    return {name: object() for name in _NO_DEFAULT}


def test_field_names_are_unchanged() -> None:
    names = {f.name for f in dataclasses.fields(mdb._MultiRun)}
    assert names == set(_NO_DEFAULT) | set(_DEFAULTS)
    assert len(names) == 32


def test_keyword_construction_keeps_values_and_defaults() -> None:
    kw = _kwargs()
    run = mdb._MultiRun(**kw)
    for name, value in kw.items():
        assert getattr(run, name) is value
    for name, default in _DEFAULTS.items():
        assert getattr(run, name) == default


def test_default_containers_are_per_instance() -> None:
    a = mdb._MultiRun(**_kwargs())
    b = mdb._MultiRun(**_kwargs())
    for name, default in _DEFAULTS.items():
        if default is not None:
            assert getattr(a, name) is not getattr(b, name)


def test_fields_without_default_are_required() -> None:
    kw = _kwargs()
    kw.pop("form_fill_out")
    try:
        mdb._MultiRun(**kw)
    except TypeError:
        return
    raise AssertionError("form_fill_out 누락이 TypeError를 내지 않았다")


def test_attributes_stay_assignable() -> None:
    """입력 필드도 대입 가능하다(frozen 아님) — 대역 테스트가 평면 속성을 바꿔 끼운다."""
    run = mdb._MultiRun(**{**_kwargs(), "db_results": {}})
    run.state = {"user_query": "q"}
    run.db_results["gp"] = []
    assert run.state == {"user_query": "q"}
    assert run.db_results == {"gp": []}
