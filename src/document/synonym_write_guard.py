"""유사어 자동 등록 쓰기 가드 — 오염 자기강화 루프를 쓰기 지점에서 막는다 (plans/120 F-4).

양식 LLM 매핑을 유사어로 자동 등록하면 한 번 굳은 오매핑이 다음 run에서 정확 매칭으로 되살아난다
(Known Mistakes — 출력 교정만으로는 부족하고 등록 지점에서 결정적으로 막아야 한다). 등록하지 않는
네 유형을 판정한다.

- 월 구조 필드명 — 사용률 월 시리즈 헤더는 컬럼 하나로 매핑되는 필드가 아니다(plans/120 F-1)
- EAV 속성으로 가는 「그룹|서브」 복합 필드명 — 양식 헤더 결합(D-145) 산물이라 그 양식 문맥에서만
  성립한다(D-148 과적합 경계 지표 ③ "전역 유사어에 양식 어휘 등록"). 일반 컬럼 대상은 월 구조
  필드만 막는다(이번 범위 — plans/120 F-4 ⓓ)
- EAV 피벗 DB에서 구조 선언 밖 테이블 — 결정적 피벗이 어차피 버리는 매핑이다
- 날짜 접미사 스냅샷 테이블(테이블명 끝 ``_`` + 8자리 날짜) — 특정 시점 사본이다

판정은 `synonym_registration_block_reason` 하나다. 양식 매퍼의 자동 등록과 오염 진단·정리 도구
(`scripts/synonym_seeds.py`의 ``audit``·``prune``)가 같은 함수를 쓴다(사본 금지). 사용자 명시 경로
(매핑 보고서 피드백·등록 명령)는 D-228 ③에 따라 이 가드를 거치지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from src.utils.month_structure import is_month_structure_field

#: 월 구조 필드명(사용률+집계어 그룹 | M+k·절대월 서브)
BLOCK_MONTH_STRUCTURE_FIELD = "month_structure_field"
#: EAV 속성 대상의 「그룹|서브」 복합 필드명
BLOCK_EAV_COMPOSITE_FIELD = "eav_composite_field_name"
#: 날짜 접미사 스냅샷 테이블
BLOCK_DATE_SUFFIX_TABLE = "date_suffix_snapshot_table"
#: EAV 피벗 DB의 구조 선언 밖 테이블
BLOCK_OUTSIDE_DECLARED_TABLES = "outside_declared_tables"

#: 사유 코드의 사람용 표기(로그·진단 도구 출력)
BLOCK_REASON_LABELS: dict[str, str] = {
    BLOCK_MONTH_STRUCTURE_FIELD: "월 구조 필드명",
    BLOCK_EAV_COMPOSITE_FIELD: "EAV 속성으로 가는 복합 필드명(그룹|서브)",
    BLOCK_DATE_SUFFIX_TABLE: "날짜 접미사 스냅샷 테이블",
    BLOCK_OUTSIDE_DECLARED_TABLES: "EAV 피벗 DB의 구조 선언 밖 테이블",
}

_DATE_SUFFIX_RE = re.compile(r"_(\d{8})$")


def _bare_table(name: Any) -> str:
    """테이블명에서 스키마 접두를 떼고 소문자로 만든다."""
    return str(name).strip().rsplit(".", 1)[-1].lower()


def column_table(column: str) -> str | None:
    """매핑 대상 컬럼의 테이블명(소문자 · 스키마 접두 없음)을 돌려준다.

    ``[schema.]table.column`` 형식만 테이블을 가진다. ``EAV:`` 속성·bare 컬럼명(전역 사전 키)은
    테이블이 없어 None이다. 테이블 추출 규칙은 유사어 매칭 Pass 1·2와 같다(끝에서 두 번째 조각).
    """
    if not column or column.startswith("EAV:"):
        return None
    parts = column.split(".")
    if len(parts) < 2:
        return None
    return parts[-2].strip().lower() or None


def is_date_suffix_snapshot_table(table: str) -> bool:
    """테이블명이 ``_`` + 실제 날짜 8자리(YYYYMMDD)로 끝나는지 판정한다."""
    m = _DATE_SUFFIX_RE.search((table or "").strip())
    if not m:
        return False
    try:
        datetime.strptime(m.group(1), "%Y%m%d")
    except ValueError:
        return False
    return True


def eav_declared_tables(structure_meta: Mapping[str, Any] | None) -> frozenset[str] | None:
    """EAV 피벗 DB의 구조 선언 테이블 집합(소문자 · 스키마 접두 없음)을 돌려준다.

    선언 테이블은 ``allowed_tables`` ∪ ``alarm_allowed_tables`` ∪ 패턴이 가리키는 테이블
    (``entity_table``·``config_table``·``table``)이다. EAV 패턴(``patterns[].type == "eav"``)이
    없거나 ``allowed_tables`` 선언이 없으면 None — 판정 근거가 없으니 호출부는 제한하지 않는다
    (종전 동작).

    Args:
        structure_meta: 한 DB의 구조 선언(수동 프로필 또는 승인 적용본)

    Returns:
        선언 테이블 집합 또는 None(제한 근거 없음)
    """
    if not isinstance(structure_meta, Mapping):
        return None
    raw_patterns = structure_meta.get("patterns")
    patterns = [
        p for p in (raw_patterns if isinstance(raw_patterns, (list, tuple)) else [])
        if isinstance(p, Mapping)
    ]
    if not any(p.get("type") == "eav" for p in patterns):
        return None
    allowed = structure_meta.get("allowed_tables")
    if not isinstance(allowed, (list, tuple)) or not allowed:
        return None
    tables = {_bare_table(t) for t in allowed}
    alarm = structure_meta.get("alarm_allowed_tables")
    if isinstance(alarm, (list, tuple)):
        tables.update(_bare_table(t) for t in alarm)
    for p in patterns:
        for key in ("entity_table", "config_table", "table"):
            if p.get(key):
                tables.add(_bare_table(p[key]))
    tables.discard("")
    return frozenset(tables)


def synonym_registration_block_reason(
    field: str,
    column: str,
    *,
    structure_meta: Mapping[str, Any] | None = None,
) -> str | None:
    """유사어로 등록하면 안 되는 매핑이면 사유 코드를, 등록해도 되면 None을 돌려준다.

    판정 순서는 월 구조 필드명 → EAV 속성 대상 복합 필드명 → 날짜 접미사 스냅샷 테이블 → EAV 피벗
    DB의 구조 선언 밖 테이블이다. ``EAV:`` 속성 대상은 앞의 두 규칙, bare 컬럼명은 첫 규칙만 받는다.

    Args:
        field: 등록할 단어(양식 필드명)
        column: 대상 ``[schema.]table.column`` · ``EAV:속성`` · bare 컬럼명
        structure_meta: 대상 DB의 구조 선언(없으면 구조 선언 규칙은 판정하지 않는다)

    Returns:
        `BLOCK_*` 사유 코드 또는 None
    """
    if is_month_structure_field(field):
        return BLOCK_MONTH_STRUCTURE_FIELD
    if column.startswith("EAV:") and "|" in (field or ""):
        return BLOCK_EAV_COMPOSITE_FIELD
    table = column_table(column)
    if table is None:
        return None
    if is_date_suffix_snapshot_table(table):
        return BLOCK_DATE_SUFFIX_TABLE
    declared = eav_declared_tables(structure_meta)
    if declared is not None and table not in declared:
        return BLOCK_OUTSIDE_DECLARED_TABLES
    return None
