"""SQL 방언 유틸 — 엔진 판정·행 제한 절·리터럴 이스케이프의 단일 출처 (Plan 69 P2).

방언 분기 판정식이 3형태(`== "db2"` / `.lower() == "db2"` / `"db2" in eng`)로 흩어져
소비처마다 다르게 판정하던 것을 단일화한다. 레지스트리(`config/db_registry.yaml`)의
engine 실값은 "db2"/"postgresql" 정확 문자열이므로 정규화 후 등호 비교로 전 소비처
동작이 동일하다(계획서 v2 실측 — 값 전수는 테스트가 단언).

계층: utils — DB-agnostic(D-088). 특정 DB 스키마 지식 금지, 엔진 방언 문법만 담는다.
"""

from __future__ import annotations


def is_db2(engine: str | None) -> bool:
    """DB2 엔진 여부 — 방언 분기의 유일한 판정식."""
    return (engine or "").strip().lower() == "db2"


def row_limit_clause(engine: str | None, limit: int) -> str:
    """행 제한 절(개행·세미콜론 없이) — DB2는 FETCH FIRST, 그 외 LIMIT."""
    if is_db2(engine):
        return f"FETCH FIRST {limit} ROWS ONLY"
    return f"LIMIT {limit}"


def sql_literal(value: object) -> str:
    """필터 값을 SQL 리터럴로 만든다(문자열은 따옴표 이스케이프, 리스트는 IN 목록).

    조립 지점 단일화 — 컴파일러·조립기가 emit하는 리터럴의 이스케이프는 전부 여기를
    지난다(polestar assembler에서 이동, 동작 불변).
    """
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return "(" + ", ".join(sql_literal(v) for v in value) + ")"
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"
