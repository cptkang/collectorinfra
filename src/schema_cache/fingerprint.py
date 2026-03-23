"""DB 스키마 fingerprint 생성 모듈.

information_schema에서 가벼운 메타데이터만 조회하여
스키마 변경 여부를 판단할 수 있는 해시(fingerprint)를 생성한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# fingerprint 조회용 SQL (information_schema 기반)
FINGERPRINT_SQL = """
SELECT
    table_name,
    COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
GROUP BY table_name
ORDER BY table_name
"""


def compute_fingerprint(rows: list[dict[str, Any]]) -> str:
    """fingerprint 쿼리 결과로부터 해시를 생성한다.

    테이블명 + 컬럼 수 조합을 정렬된 JSON으로 직렬화한 뒤
    SHA-256 해시를 생성한다.

    Args:
        rows: fingerprint 쿼리 결과 행 목록
              (각 행은 table_name, column_count 필드를 가짐)

    Returns:
        SHA-256 해시 문자열 (hex)
    """
    # 정규화: 테이블명으로 정렬, 일관된 키 순서
    normalized = sorted(
        [
            {
                "table_name": row.get("table_name", ""),
                "column_count": row.get("column_count", 0),
            }
            for row in rows
        ],
        key=lambda r: r["table_name"],
    )

    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_fingerprint_from_schema_dict(schema_dict: dict) -> str:
    """스키마 딕셔너리에서 fingerprint를 역산한다.

    캐시 저장 시 fingerprint를 생성하기 위해 사용한다.
    실제 DB 조회 없이 기존 스키마 정보로부터 해시를 만든다.

    Args:
        schema_dict: 스키마 딕셔너리 (tables 키 포함)

    Returns:
        SHA-256 해시 문자열 (hex)
    """
    tables = schema_dict.get("tables", {})
    rows = [
        {
            "table_name": table_name,
            "column_count": len(table_data.get("columns", [])),
        }
        for table_name, table_data in tables.items()
    ]
    return compute_fingerprint(rows)
