"""column_mapping -> SQL alias 해석 유틸리티.

field_mapper가 생성한 column_mapping 값(예: "cmm_resource.hostname", "EAV:OSType")을
SQL 쿼리 결과의 실제 키(예: "cmm_resource_hostname", "os_type")로 해석하는 규칙 기반 매칭.

이 모듈은 순수 규칙 기반이며 LLM 의존성이 없다.
계층: utils (config/utils)
"""

from __future__ import annotations

import re
from typing import Any, Optional


def _is_close_match(a: str, b: str, max_distance: int = 1) -> bool:
    """두 문자열이 편집 거리 1 이내인지 확인한다 (오타 대응).

    단순 삽입/삭제/치환 1회 차이만 허용한다.
    빈 문자열이나 길이 차이가 2 이상인 경우는 즉시 False.
    """
    if abs(len(a) - len(b)) > max_distance:
        return False
    if a == b:
        return True

    # 간단한 편집 거리 1 체크 (O(n) 시간)
    len_a, len_b = len(a), len(b)

    if len_a == len_b:
        # 치환 1회: 정확히 1곳만 다른지 확인
        diffs = sum(1 for ca, cb in zip(a, b) if ca != cb)
        return diffs <= max_distance

    # 삽입/삭제 1회: 긴 쪽에서 1개 빼면 짧은 쪽과 같은지
    shorter, longer = (a, b) if len_a < len_b else (b, a)
    i = j = diffs = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] != longer[j]:
            diffs += 1
            if diffs > max_distance:
                return False
            j += 1  # 긴 쪽에서 1개 건너뛰기
        else:
            i += 1
            j += 1
    return True


def camel_to_snake(name: str) -> str:
    """CamelCase를 snake_case로 변환한다.

    Examples:
        >>> camel_to_snake("OSType")
        'os_type'
        >>> camel_to_snake("SerialNumber")
        'serial_number'
        >>> camel_to_snake("already_snake")
        'already_snake'
    """
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def resolve_column_key(mapped_col: str, result_keys: set[str]) -> str | None:
    """매핑된 컬럼명을 실제 결과 키로 해석한다.

    5단계 매칭 순서:
    1. 정확 매칭
    2. "table.column" -> "column" 부분 매칭
    3. "EAV:" 접두사 제거 후 매칭
    4. 대소문자 무시 매칭 (EAV 접두사 고려)
    5. CamelCase<->snake_case 변환 + 언더스코어 제거 비교

    Args:
        mapped_col: 매핑된 컬럼명 (예: "cmm_resource.hostname", "EAV:OSType")
        result_keys: SQL 결과의 컬럼명 집합

    Returns:
        매칭된 실제 result key, 또는 None (매칭 실패)
    """
    if not mapped_col or not result_keys:
        return None

    # 1. 정확 매칭
    if mapped_col in result_keys:
        return mapped_col

    # 2. "table.column" -> "column" 매칭
    if "." in mapped_col:
        col_only = mapped_col.split(".", 1)[-1]
        if col_only in result_keys:
            return col_only

    # 3. "EAV:" 접두사 제거 후 정확 매칭
    if mapped_col.startswith("EAV:"):
        attr_name = mapped_col[4:]
        if attr_name in result_keys:
            return attr_name

    # 4. 대소문자 무시 매칭 (EAV 접두사 고려)
    effective = mapped_col[4:] if mapped_col.startswith("EAV:") else mapped_col
    effective_lower = effective.lower()
    # "table.column" -> "column" 부분도 시도
    col_only_lower = (
        effective.split(".", 1)[1].lower() if "." in effective else effective_lower
    )

    for rk in result_keys:
        rk_lower = rk.lower()
        if rk_lower == effective_lower or rk_lower == col_only_lower:
            return rk

    # 4.5 "table.column" -> "table_column" 점을 언더스코어로 대체하여 매칭
    if "." in effective:
        dot_to_underscore = effective.replace(".", "_")
        if dot_to_underscore in result_keys:
            return dot_to_underscore
        dot_lower = dot_to_underscore.lower()
        for rk in result_keys:
            if rk.lower() == dot_lower:
                return rk

    # 5. CamelCase <-> snake_case 변환 + 언더스코어 제거 비교
    effective_snake = camel_to_snake(effective)
    # "table.column" 형식이면 table_column 형태의 snake도 생성
    if "." in effective:
        col_part = effective.split(".", 1)[1]
        effective_snake_col = camel_to_snake(col_part)
    else:
        effective_snake_col = effective_snake

    effective_no_underscore = effective.lower().replace("_", "")

    for rk in result_keys:
        rk_snake = camel_to_snake(rk)
        # CamelCase->snake_case 비교
        if effective_snake == rk_snake or effective_snake_col == rk_snake:
            return rk
        # 언더스코어 제거 비교 (serialnumber == serial_number)
        if effective_no_underscore == rk.lower().replace("_", ""):
            return rk

    # 6. 오타 대응: snake_case 변환 후 편집 거리 1 이내 매칭
    for rk in result_keys:
        rk_snake = camel_to_snake(rk)
        rk_no_underscore = rk.lower().replace("_", "")
        # snake_case 비교에서 편집 거리 1 이내
        if _is_close_match(effective_snake, rk_snake):
            return rk
        # 언더스코어 제거 후 편집 거리 1 이내
        if _is_close_match(effective_no_underscore, rk_no_underscore):
            return rk

    return None


def build_resolved_mapping(
    column_mapping: dict[str, str | None],
    result_keys: set[str],
) -> tuple[dict[str, str | None], list[str]]:
    """column_mapping을 실제 결과 키로 해석한다.

    Args:
        column_mapping: {field: db_column 또는 None}
        result_keys: SQL 결과의 실제 키 집합

    Returns:
        (resolved_mapping, unresolved_fields)
        - resolved_mapping: {field: 실제_result_key 또는 None}
        - unresolved_fields: 규칙 기반으로 해석 실패한 field명 목록
          (column_mapping에서 None이 아닌 매핑이지만 result_keys에서 찾지 못한 필드)
    """
    resolved: dict[str, str | None] = {}
    unresolved: list[str] = []

    for field, db_col in column_mapping.items():
        if db_col is None:
            # DB 매핑 없는 필드 (auto-numbering 등)
            resolved[field] = None
            continue

        matched_key = resolve_column_key(db_col, result_keys)
        if matched_key is not None:
            resolved[field] = matched_key
        else:
            # 규칙으로 해석 실패 -> unresolved
            resolved[field] = db_col  # 원본 값 유지 (Layer 3 폴백용)
            unresolved.append(field)

    return resolved, unresolved
