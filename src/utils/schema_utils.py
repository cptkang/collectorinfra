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


def form_signature(template_structure: dict | None) -> str | None:
    """양식 시그니처를 계산한다 (Plan 68 §2.4, D-118 확인 이력 키).

    **헤더 필드명 집합만** 사용한다 — 데이터 행·파일명·시트명 불포함(값 선입력·파일명
    변경에 불변). 정규화: NFC + 공백/개행 전부 제거 + 소문자화 → 정렬 집합 해시.
    "IP 주소"와 "IP주소", 띄어쓰기 교정본이 같은 시그니처가 된다.

    Returns:
        16자리 sha256 hex 접두 또는 None(헤더 없음 — 이력 비대상).
    """
    import hashlib

    fields: set[str] = set()
    for sheet in (template_structure or {}).get("sheets", []):
        for header in sheet.get("headers", []) or []:
            if header is None:
                continue
            norm = normalize_field_name(str(header)).replace(" ", "").lower()
            if norm:
                fields.add(norm)
    if not fields:
        return None
    joined = "\x1f".join(sorted(fields))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]
