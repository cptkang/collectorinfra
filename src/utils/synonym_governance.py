"""동일 용어 다중 컬럼 매핑 충돌 우선순위 규칙 (Plan 61 트랙 B / E5-3 / D-075).

한 사용자 용어가 여러 컬럼의 유사어로 등록되어 충돌할 때, 결정적 우선순위로
후보를 정렬하고 최상위 1건을 선택한다.

**계층: utils** — 순수 함수만 제공하며 Redis·config·상태에 의존하지 않는다.
로드된 메타 dict를 인자로 받으며, Redis I/O는 infrastructure(schema_cache)에서
수행한다(redis import 금지).

우선순위(결정적, 우선 → 후순위):
  ① source == "operator"     운영자 등록 최우선
  ② usage_count 높은 순       실사용 빈도
  ③ confidence 높은 순        등록 신뢰도
  ④ column 사전순(오름차순)   안정적 tie-break

메타가 없으면 usage_count=0, confidence=0.0으로 간주한다. source가 없으면 "llm".
후보(candidate) dict 형태(모든 키 선택적):
    {"column": str, "source": str, "usage_count": int, "confidence": float, ...}
"""

from __future__ import annotations


def _sort_key(candidate: dict) -> tuple:
    """충돌 우선순위 정렬 키를 생성한다.

    파이썬 오름차순 정렬에서 우선순위가 높을수록 앞에 오도록,
    내림차순 항목(operator·usage_count·confidence)은 음수화하고
    tie-break 컬럼명은 오름차순 그대로 둔다.
    """
    source = str(candidate.get("source") or "llm")
    is_operator = 1 if source == "operator" else 0
    usage_count = int(candidate.get("usage_count") or 0)
    confidence = float(candidate.get("confidence") or 0.0)
    column = str(candidate.get("column") or "")
    return (-is_operator, -usage_count, -confidence, column)


def rank_synonym_candidates(candidates: list[dict]) -> list[dict]:
    """충돌 후보를 결정적 우선순위로 정렬하여 반환한다(최상위 우선).

    Args:
        candidates: 후보 dict 목록. 각 dict는 column/source/usage_count/
            confidence 키를 가질 수 있다(모두 선택적, 메타 없으면 0으로 간주).

    Returns:
        우선순위 내림차순으로 정렬된 새 리스트(원본 불변).
    """
    return sorted(candidates or [], key=_sort_key)


def resolve_synonym_conflict(candidates: list[dict]) -> dict:
    """충돌 후보 중 우선순위 최상위 1건을 반환한다.

    Args:
        candidates: 후보 dict 목록.

    Returns:
        최상위 후보 dict. 후보가 없으면 빈 dict.
    """
    ranked = rank_synonym_candidates(candidates)
    return ranked[0] if ranked else {}
