"""생성된 SQL에서 실제 사용된 유사어 사전 항목을 역조회한다.

LLM이 SQL을 생성한 뒤, SQL에 등장한 EAV 속성명/RESOURCE_TYPE 리터럴과
컬럼 참조를 유사어 사전 key와 대조하여 "LLM이 결정한 매핑"을 추출한다.
처리 현황 UI에 유사어 검색 과정을 표시하는 용도로 사용된다.
"""

from __future__ import annotations

import re

# 처리 현황 페이로드 비대화 방지를 위한 유사어 표시 상한
_MAX_SYNONYMS_PER_KEY = 10

# 처리 현황 페이로드 비대화 방지를 위한 매핑 항목 수 상한
_MAX_MAPPINGS = 15


def extract_synonym_usage(
    sql: str,
    *,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    query_targets: list[str] | None = None,
    attribute_columns: list[str] | None = None,
) -> dict:
    """SQL에 사용된 유사어 사전 항목과 사전 미등록 리터럴을 추출한다.

    일반 컬럼 매핑은 bare 컬럼명 기준으로 중복 제거되며,
    사용자 용어(query_targets)와 매칭된 항목만 포함된다.
    전체 매핑은 최대 _MAX_MAPPINGS건으로 제한된다.

    Args:
        sql: 생성된 SQL 쿼리
        column_synonyms: {table.column: [synonym, ...]} 매핑
        resource_type_synonyms: {resource_type값: [한국어 표현, ...]} 매핑
        eav_name_synonyms: {EAV 속성명: [한국어 표현, ...]} 매핑
        query_targets: 사용자 질의에서 파싱된 조회 대상 표현 목록
        attribute_columns: EAV 속성명 컬럼 목록 (기본 ["NAME"])

    Returns:
        {
            "mappings": [
                {"type": "eav_name"|"resource_type"|"column",
                 "key": str, "synonyms": [str], "matched_user_terms": [str]},
            ],
            "unregistered": [
                {"type": "eav_name"|"resource_type", "literal": str},
            ],
        }
    """
    column_synonyms = column_synonyms or {}
    resource_type_synonyms = resource_type_synonyms or {}
    eav_name_synonyms = eav_name_synonyms or {}
    query_targets = query_targets or []
    attribute_columns = attribute_columns or ["NAME"]

    mappings: list[dict] = []

    if sql:
        # 1. EAV 속성명: 따옴표로 감싼 리터럴로 등장
        for key, syns in eav_name_synonyms.items():
            if re.search(rf"'{re.escape(key)}'", sql, re.IGNORECASE):
                mappings.append(_build_mapping("eav_name", key, syns, query_targets))

        # 2. RESOURCE_TYPE 값: 따옴표로 감싼 리터럴로 등장
        for key, syns in resource_type_synonyms.items():
            if re.search(rf"'{re.escape(key)}'", sql, re.IGNORECASE):
                mappings.append(
                    _build_mapping("resource_type", key, syns, query_targets)
                )

        # 3. 일반 컬럼: 컬럼명이 단어 경계로 등장 (리터럴 외 영역)
        # column_synonyms는 DB 전체 테이블×컬럼을 담아 수백 키 규모일 수 있고,
        # name/id 같은 공통 컬럼명은 여러 테이블 키에 중복 존재한다.
        # → bare 컬럼명 기준으로 그룹화해 1건으로 합치고,
        #   사용자 용어와 연결된 매핑만 포함하여 출력 폭주를 방지한다.
        sql_without_literals = re.sub(r"'[^']*'", "''", sql)
        grouped: dict[str, dict] = {}
        for key, syns in column_synonyms.items():
            if not syns:
                continue
            col = key.rsplit(".", 1)[-1].lower()
            entry = grouped.setdefault(col, {"keys": [], "synonyms": []})
            entry["keys"].append(key)
            for s in syns:
                if s not in entry["synonyms"]:
                    entry["synonyms"].append(s)
        for col, entry in grouped.items():
            if not re.search(
                rf"\b{re.escape(col)}\b", sql_without_literals, re.IGNORECASE
            ):
                continue
            keys = entry["keys"]
            display_key = keys[0] if len(keys) == 1 else col
            mapping = _build_mapping(
                "column", display_key, entry["synonyms"], query_targets
            )
            if mapping["matched_user_terms"]:
                mappings.append(mapping)

    mappings = mappings[:_MAX_MAPPINGS]

    # 4. 사전 미등록 리터럴 (LLM이 직접 추론한 속성명/리소스 타입)
    unregistered: list[dict] = []
    if sql:
        eav_keys = {k.lower() for k in eav_name_synonyms}
        for lit in _extract_column_literals(sql, attribute_columns):
            if lit.strip("%").lower() not in eav_keys:
                unregistered.append({"type": "eav_name", "literal": lit})

        rt_keys = {k.lower() for k in resource_type_synonyms}
        for lit in _extract_column_literals(sql, ["RESOURCE_TYPE"]):
            if lit.strip("%").lower() not in rt_keys:
                unregistered.append({"type": "resource_type", "literal": lit})

    return {"mappings": mappings, "unregistered": _dedupe(unregistered)}


def _build_mapping(
    mapping_type: str,
    key: str,
    synonyms: list[str],
    query_targets: list[str],
) -> dict:
    """매핑 항목 딕셔너리를 생성한다."""
    return {
        "type": mapping_type,
        "key": key,
        "synonyms": synonyms[:_MAX_SYNONYMS_PER_KEY],
        "matched_user_terms": _match_user_terms(query_targets, key, synonyms),
    }


def _match_user_terms(
    query_targets: list[str],
    key: str,
    synonyms: list[str],
) -> list[str]:
    """사용자 질의 표현 중 key 또는 유사어와 매칭되는 것을 찾는다.

    정규화(공백 제거, 소문자) 후 동일하거나 한쪽이 다른쪽을 포함하면 매칭으로 본다.
    """
    candidates = [_normalize(c) for c in [key, *synonyms] if c]
    candidates = [c for c in candidates if len(c) >= 2]

    matched: list[str] = []
    for term in query_targets:
        t = _normalize(term)
        if len(t) < 2:
            continue
        for c in candidates:
            if c == t or c in t or t in c:
                matched.append(term)
                break
    return matched


def _extract_column_literals(sql: str, columns: list[str]) -> list[str]:
    """SQL에서 지정 컬럼의 비교 대상 문자열 리터럴을 추출한다.

    `col = 'X'`, `col LIKE 'X'`, `col IN ('A', 'B')` 패턴을 지원한다.
    """
    literals: list[str] = []
    for col in columns:
        if not col:
            continue
        pattern_eq = rf"\b{re.escape(col)}\s*(?:=|LIKE)\s*'([^']+)'"
        for m in re.finditer(pattern_eq, sql, re.IGNORECASE):
            literals.append(m.group(1))
        pattern_in = rf"\b{re.escape(col)}\s+IN\s*\(([^)]*)\)"
        for m in re.finditer(pattern_in, sql, re.IGNORECASE):
            literals.extend(re.findall(r"'([^']*)'", m.group(1)))
    return literals


def _normalize(s: str) -> str:
    """비교용 정규화: 공백 제거 + 소문자화."""
    return re.sub(r"\s+", "", s).lower()


def _dedupe(items: list[dict]) -> list[dict]:
    """(type, literal) 기준으로 순서를 유지하며 중복을 제거한다."""
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    for item in items:
        ident = (item["type"], item["literal"].lower())
        if ident not in seen:
            seen.add(ident)
            result.append(item)
    return result
