"""월 구조 양식 필드 판정 — 사용률 월 시리즈 헤더의 구조 패턴 (plans/120 F-1 · D-146).

2단 병합 헤더 결합(D-145)이 만든 복합 필드명 "그룹라벨|서브"에서
"사용률+집계어 그룹 | M+k(또는 절대월) 서브" **구조 패턴**만 판정한다.
기관명·시트제목·칼럼순서 하드코딩 금지(과적합 가드 — plans/72 §8 R3의 경계 지표).

스키마 리터럴이 없어 공용 계층(utils)에 둔다. 소비처가 같은 규칙을 import한다(사본 금지):
- 문서 계층 필드 매퍼 — 월 구조 필드는 매핑하지 않는다(plans/120 F-1)
- DB 어댑터 월 시리즈 인식기 — 집계 종류를 자기 값 컬럼 표로 옮긴다
- 출력 노드 — 월 구조 필드 미채움 고지(plans/120 F-6)
- 유사어 등록 차단(plans/120 F-4)
"""

from __future__ import annotations

import re

#: 월 그룹 집계어 — (집계 종류, 표면어). peak 판정을 평균보다 먼저 한다
#: ("Peak시 사용률"류에 '평균'이 공존할 일은 없으나 순서 명시).
MONTH_GROUP_AGG_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("peak", ("peak", "피크", "최고", "최대")),
    ("avg", ("평균", "avg")),
)

_REL_MONTH_RE = re.compile(r"^m(?:\s*\+\s*(\d{1,2}))?$", re.IGNORECASE)
_ABS_YM_RE = re.compile(r"^(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*월?$")
_ABS_M_ONLY_RE = re.compile(r"^(\d{1,2})\s*월$")


def parse_month_sub(sub: str) -> tuple[str, int | str] | None:
    """서브 헤더를 ('rel', k) 또는 ('abs', 'YYYYMM'|'MM')로 해석한다(아니면 None)."""
    s = sub.strip()
    m = _REL_MONTH_RE.match(s)
    if m:
        return ("rel", int(m.group(1) or 0))
    m = _ABS_YM_RE.match(s)
    if m:
        month = int(m.group(2))
        if 1 <= month <= 12:
            return ("abs", f"{int(m.group(1))}{month:02d}")
        return None
    m = _ABS_M_ONLY_RE.match(s)
    if m:
        month = int(m.group(1))
        if 1 <= month <= 12:
            return ("abs", f"{month:02d}")  # 연도 미상 — 앵커 해석 시 보정
        return None
    return None


def month_group_agg(group: str) -> str | None:
    """그룹 라벨이 '사용률'과 집계어를 함께 가지면 집계 종류('peak'|'avg'), 아니면 None."""
    low = (group or "").lower()
    if "사용률" not in low:
        return None
    return next(
        (kind for kind, terms in MONTH_GROUP_AGG_TERMS if any(t in low for t in terms)),
        None,
    )


def parse_month_structure_field(field_name: str) -> tuple[str, tuple[str, int | str]] | None:
    """복합 필드명을 (집계 종류, 서브 해석)으로 해석한다(월 구조 필드가 아니면 None).

    예: "월중평균사용률(최근 6개월간)|M+2" → ("avg", ("rel", 2)),
        "월중 Peak시 사용률|2026.03" → ("peak", ("abs", "202603")).
    """
    name = field_name or ""
    if "|" not in name:
        return None
    group, _, sub = name.rpartition("|")
    agg = month_group_agg(group)
    if agg is None:
        return None
    sub_parsed = parse_month_sub(sub)
    if sub_parsed is None:
        return None
    return agg, sub_parsed


def is_month_structure_field(field_name: str) -> bool:
    """월 구조 필드(사용률+집계어 그룹 | M+k 또는 절대월 서브)인지 판정한다.

    필드명에 리소스 명사(CPU·메모리 등)가 없어도 구조만으로 판정한다 — 명사는 양식
    제목·질의에 있다(plans/120 §2.4: 명사 없는 월 필드가 매핑 스킵 규칙을 빠져나갔다).
    """
    return parse_month_structure_field(field_name) is not None
