"""스키마 관련 유틸리티 함수.

_structure_meta 딕셔너리에서 메타데이터를 추출하는 순수 함수들.
application 계층(nodes/)에서 공용으로 사용한다.
"""

from __future__ import annotations

import re
import unicodedata


def build_excluded_join_map(schema_info: dict) -> dict[tuple[str, str], str]:
    """_structure_meta의 excluded_join_columns에서 금지 컬럼 매핑을 구축한다.

    Args:
        schema_info: 스키마 정보 딕셔너리

    Returns:
        {(table_lower, column_lower): reason} 매핑.
        예: {("cmm_resource", "resource_conf_id"): "NULL"}
    """
    result: dict[tuple[str, str], str] = {}
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return result
    for pattern in structure_meta.get("patterns", []):
        for excl in pattern.get("excluded_join_columns", []):
            table = excl.get("table", "").lower()
            column = excl.get("column", "").lower()
            reason = excl.get("reason", "NULL")
            if table and column:
                result[(table, column)] = reason
    return result


def normalize_field_name(name: str) -> str:
    """필드명을 정규화한다.

    1. Unicode NFC 정규화
    2. 줄바꿈/탭을 공백으로 치환
    3. 연속 공백을 단일 공백으로 축소
    4. 앞뒤 공백 제거
    """
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = re.sub(r" {2,}", " ", name)
    return name.strip()
