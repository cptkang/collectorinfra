"""EAV 속성 순위 정렬 회귀 (plans/116 §10.3 — 「메모리 큰 상위 3대」가 8GB 서버를 낸 결함).

EAV 값은 문자열이라 `ORDER BY "TotalSize" DESC`는 '8.0 GB' > '64.0 GB' 순이 된다.
결정적 조립(시맨틱 컴파일러)이 숫자·크기 속성 정렬을 값 크기 순 식으로 거는지, 그 식이 실제로
크기 순을 내는지(sqlite로 식 평가 — 같은 함수 어휘), 식별 칼럼 없이 속성만 순위 매기던 SELECT에
서버명이 들어가는지를 본다. 실제 DB·LLM 없이 통과해야 한다.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.db_adapters.polestar.assembler import eav_sort_expr
from src.nodes.semantic_compiler import SMQ, check_coverage, compile_smq, load_semantic_model


@pytest.fixture()
def model():
    return load_semantic_model("polestar")


def _compile(smq_dict: dict, model: dict, **kwargs) -> str:
    smq = SMQ.from_dict(smq_dict)
    cov = check_coverage(smq, model)
    assert cov.covered, f"커버리지 밖: {cov.reason}"
    return compile_smq(smq, "polestar", model, **kwargs)


def _sorted_desc(values: list[str], attribute: str) -> list[str]:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(v,) for v in values])
    expr = eav_sort_expr("v", attribute)
    assert expr is not None
    return [r[0] for r in conn.execute(f"SELECT v FROM t ORDER BY {expr} DESC")]


def test_size_sort_expr_orders_by_capacity_across_units():
    """'N.N GB' 값을 용량 순으로 정렬한다 — 문자열 정렬이면 '8.0 GB'가 1위였다."""
    values = ["8.0 GB", "64.0 GB", "16.0 GB", "62.1 GB", "4.0 GB"]
    ordered = _sorted_desc(values, "TotalSize")
    assert ordered == ["64.0 GB", "62.1 GB", "16.0 GB", "8.0 GB", "4.0 GB"]


def test_size_sort_expr_keeps_unitless_value_as_is():
    """무단위 값은 원값 그대로(D-199 ①) — 샌드박스 시드의 무단위 MB '65536'은 65536으로
    취급돼 맨 위에 온다. 시드 모양 문제(D-199 ③)라 정상으로 둔다."""
    ordered = _sorted_desc(["64.0 GB", "65536", "2 TB"], "TotalSize")
    assert ordered == ["65536", "2 TB", "64.0 GB"]


def test_size_sort_expr_handles_tb_and_mb_suffix():
    ordered = _sorted_desc(["900 MB", "1.5 TB", "14.9 GB"], "TotalSize")
    assert ordered == ["1.5 TB", "14.9 GB", "900 MB"]


def test_numeric_sort_expr_orders_core_counts():
    ordered = _sorted_desc(["8.0", "16", "32.0", "4", "16.0"], "LOGICALCORE")
    assert ordered[0] == "32.0" and ordered[-1] == "4"


def test_string_attribute_has_no_sort_expr():
    assert eav_sort_expr("v", "OSType") is None


def test_ranked_size_dimension_sorts_by_capacity_and_names_servers(model):
    """「메모리 사이즈가 큰 순서로 상위 3대」 SMQ(2026-09-23 MLX 재현 형태)."""
    sql = _compile(
        {"pattern": "A", "dimensions": ["TotalSize"],
         "order_by": {"field": "TotalSize", "direction": "desc"}, "limit": 3},
        model, user_query="메모리 사이즈가 큰 순서로 상위 3대 서버를 보여줘",
    )
    assert 'ORDER BY "TotalSize"' not in sql
    order_clause = sql[sql.index("ORDER BY"):]
    assert "LIKE '%GB%'" in order_clause and "DESC NULLS LAST" in order_clause
    # 식별 칼럼 보정 — 용량 칼럼만 있으면 어느 서버인지 알 수 없다.
    assert 'AS "name"' in sql
    assert sql.rstrip().endswith("LIMIT 3;")


def test_ranked_numeric_dimension_casts(model):
    sql = _compile(
        {"pattern": "A", "dimensions": ["name", "LOGICALCORE"],
         "order_by": {"field": "LOGICALCORE", "direction": "desc"}, "limit": 5},
        model,
    )
    assert (
        "ORDER BY MAX(CASE WHEN c.resource_type='server.Cpus' AND cc.name='LOGICALCORE' "
        "THEN CAST(NULLIF(TRIM(cc.stringvalue_short), '') AS NUMERIC) END) DESC NULLS LAST"
    ) in sql


def test_string_dimension_order_unchanged(model):
    sql = _compile(
        {"pattern": "A", "dimensions": ["name", "hostname"],
         "order_by": {"field": "hostname", "direction": "asc"}},
        model,
    )
    assert 'ORDER BY "hostname" ASC NULLS LAST' in sql


def test_ranked_size_sql_passes_polestar_validators(model):
    from src.db_adapters import get_adapter

    sql = _compile(
        {"pattern": "A", "dimensions": ["TotalSize"],
         "order_by": {"field": "TotalSize", "direction": "desc"}, "limit": 3},
        model,
    )
    adapter = get_adapter("polestar", {"polestar"})
    errors = [e for check in adapter.validator_checks() for e in check(sql)]
    assert errors == []
