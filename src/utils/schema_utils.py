"""스키마 관련 유틸리티 함수.

_structure_meta 딕셔너리에서 메타데이터를 추출하는 순수 함수들.
application 계층(nodes/)에서 공용으로 사용한다.
"""

from __future__ import annotations

import json
import re
import unicodedata

# 샘플 프리뷰 상한 — DB2 CLOB성 설정값(수 MB 단일 라인)이 무제한으로 프롬프트에
# 직렬화되면 PII 스크럽(라인×규칙 정규식)이 이벤트 루프를 수 시간 동기 점유한다
# (2026-08-04 py-spy 실측: b0 408테이블 샘플 → scrub_pii active+gil 고정, 서버 전체
# 동결). 값·테이블 단위로 절단해 스크럽·프롬프트 비용을 결정적으로 bound한다.
SAMPLE_VALUE_MAX_CHARS = 200
SAMPLE_PREVIEW_MAX_CHARS = 2000


def safe_sample_preview(samples: list, max_rows: int = 3) -> str:
    """샘플 행들을 크기 상한이 보장된 JSON 프리뷰 텍스트로 직렬화한다.

    긴 문자열 값은 SAMPLE_VALUE_MAX_CHARS로, 전체 프리뷰는
    SAMPLE_PREVIEW_MAX_CHARS로 절단한다(절단 시 표식을 남겨 침묵 축소를 피한다).

    Args:
        samples: 샘플 행 목록(dict 행 권장, 그 외 타입은 그대로 직렬화)
        max_rows: 직렬화할 최대 행 수

    Returns:
        프롬프트 삽입용 JSON 프리뷰 문자열
    """
    def _cap(value):  # noqa: ANN001 — JSON 값(str/num/bool/None/중첩)
        if isinstance(value, str) and len(value) > SAMPLE_VALUE_MAX_CHARS:
            return value[:SAMPLE_VALUE_MAX_CHARS] + "…(절단)"
        return value

    rows = []
    for row in samples[:max_rows]:
        if isinstance(row, dict):
            rows.append({k: _cap(v) for k, v in row.items()})
        else:
            rows.append(_cap(row))
    try:
        preview = json.dumps(rows, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        preview = str(rows)
    if len(preview) > SAMPLE_PREVIEW_MAX_CHARS:
        preview = preview[:SAMPLE_PREVIEW_MAX_CHARS] + "\n…(샘플 절단)"
    return preview


def cap_sample_rows(samples: list, max_rows: int = 5) -> list:
    """샘플 행 목록을 행 수·값 길이 상한으로 절단해 반환한다.

    safe_sample_preview가 프롬프트 직렬화 직전의 절단이라면, 이 함수는 샘플을
    state에 **부착하는 시점**의 절단이다. DB2 CLOB성 수 MB 값이 상태·체크포인트·
    후단 PII 스크럽 비용을 무상한으로 키우는 것을 원천 차단한다(2026-08-04 b0
    동결 계열 — 부착 시점 미절단이 남은 유입구였음).

    Args:
        samples: 샘플 행 목록(dict 행 권장)
        max_rows: 유지할 최대 행 수

    Returns:
        값 단위 절단이 적용된 샘플 행 목록 (원본 비변경)
    """
    def _cap(value):  # noqa: ANN001 — JSON 값(str/num/bool/None/중첩)
        if isinstance(value, str) and len(value) > SAMPLE_VALUE_MAX_CHARS:
            return value[:SAMPLE_VALUE_MAX_CHARS] + "…(절단)"
        return value

    capped: list = []
    for row in samples[:max_rows]:
        if isinstance(row, dict):
            capped.append({k: _cap(v) for k, v in row.items()})
        else:
            capped.append(_cap(row))
    return capped


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
    """양식 시그니처를 계산한다 (Plan 73 §2.4, D-151 확인 이력 키).

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
