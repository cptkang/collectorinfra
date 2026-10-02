"""DB 테이블 스키마 한정 헬퍼 (D-057).

테이블 참조 접두사(`schema.`)를 domain_config의 `db_schema`를 단일 출처로 삼아
결정적으로 결정한다. LLM SQL 생성 경로(`multi_db_executor`)와 고정 SQL 경로
(`polestar_hostname_resolver`)가 동일한 규칙을 공유하여, 특정 엔진(예: DB2 b0)만
스키마 한정이 누락되는 회귀(Known Mistakes 2026-06-30, D-053)를 막는다.

규칙:
    - `db_schema`가 설정돼 있으면 `"<schema>."` 접두사를 반환한다(예: PostgreSQL polestar → "polestar.").
    - 비어 있으면 `""`(무스키마) — 연결 계정의 CURRENT SCHEMA로 해소된다.

계층: routing (domain_config와 동일 계층 참조).
"""

from __future__ import annotations

from src.routing.domain_config import get_domain_by_id


def get_schema_prefix(db_id: str) -> str:
    """db_id에 대응하는 테이블 참조 접두사(`"schema."` 또는 `""`)를 반환한다.

    Args:
        db_id: DB 식별자

    Returns:
        `"<schema>."` 또는 빈 문자열(무스키마)
    """
    domain = get_domain_by_id(db_id)
    schema = (domain.db_schema if domain else "") or ""
    return f"{schema}." if schema else ""


def qualify_table(db_id: str, table: str) -> str:
    """테이블명에 db_id별 스키마 접두사를 붙여 반환한다.

    Args:
        db_id: DB 식별자
        table: 무스키마 테이블명 (예: "cmm_resource")

    Returns:
        스키마 한정 테이블 참조 (예: "polestar.cmm_resource") 또는 원본(무스키마)
    """
    return f"{get_schema_prefix(db_id)}{table}"
