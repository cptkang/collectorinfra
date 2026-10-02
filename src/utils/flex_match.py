"""유연(퍼지) 문자열 매칭 공유 유틸 (Plan 61 트랙 B / E5-1 / D-075).

폐쇄망 제약으로 외부 패키지(rapidfuzz·jamo)를 반입하지 않고 **표준 라이브러리만**
사용한다:
- ``unicodedata``: NFC 정규화 + NFD 한글 자모 분해
- 순수 파이썬 Levenshtein 편집거리

계단식 매칭(각 단계 신뢰도 0.0~1.0, 최댓값 반환):
  (1) 정확 동등(정규화 후)              → 1.0   (최우선, 무변경 경로 보존)
  (2) 공백/언더스코어/하이픈 제거 후 동등 → 0.97
  (3) 부분어(토큰) 포함                  → 0.85~0.95 (길이비 가중)
  (4) 한글 자모 분해 편집거리 근사        → 0.0~1.0

**빈문자열·1글자 가드**: 정규화 후 길이가 2 미만이면 정확 동등만 인정한다
(부분어/편집거리 근사가 1글자에서 과잉 매칭되는 것을 차단 — §4-B 실측상 기존
``_synonym_match``에는 이 가드가 없어 부분어 도입 시 신설이 필요했다).

이 모듈은 **순수 함수만** 제공하며 상태·설정에 의존하지 않는다(계층: utils).
플래그·임계값은 호출부(config 접근 가능 계층)에서 주입한다.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable, Optional

# 부분어 포함 매칭 신뢰도 범위(길이비로 [BASE, MAX] 사이 보간)
_CONTAINMENT_BASE = 0.85
_CONTAINMENT_MAX = 0.95
# 구분자 제거 후 동등 신뢰도
_SEP_EQUAL_SCORE = 0.97
# 유연 매칭을 허용하는 최소 정규화 길이(가드)
_MIN_FUZZY_LEN = 2


def _normalize(s: str) -> str:
    """NFC 정규화 + 공백 정리 + 소문자화."""
    if not s:
        return ""
    s = unicodedata.normalize("NFC", s)
    # 연속 공백·개행·탭을 단일 공백으로 축약 후 소문자
    return " ".join(s.split()).lower()


def _strip_sep(s: str) -> str:
    """공백·언더스코어·하이픈을 제거한다(표기 변형 흡수)."""
    return s.replace(" ", "").replace("_", "").replace("-", "")


def _decompose_jamo(s: str) -> str:
    """한글 음절을 NFD로 자모 분해한다(비한글 문자는 영향 없음).

    NFD는 한글 완성형 음절을 결합형 자모(초성·중성·종성)로 분해하여
    "메모리"↔"메모으리"류 자모 단위 변형을 편집거리로 흡수할 수 있게 한다.
    """
    return unicodedata.normalize("NFD", s)


def _levenshtein(a: str, b: str) -> int:
    """순수 파이썬 Levenshtein 편집거리(O(len(a)*len(b)) 공간 최적화)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def _edit_similarity(a: str, b: str) -> float:
    """한글 자모 분해 후 편집거리 기반 유사도(0.0~1.0)."""
    ja = _decompose_jamo(a)
    jb = _decompose_jamo(b)
    if not ja or not jb:
        return 0.0
    maxlen = max(len(ja), len(jb))
    if maxlen == 0:
        return 0.0
    dist = _levenshtein(ja, jb)
    return 1.0 - (dist / maxlen)


def flex_match_score(a: str, b: str) -> float:
    """두 문자열의 유연 매칭 신뢰도(0.0~1.0)를 반환한다.

    계단식 단계 중 가장 높은 신뢰도를 반환한다. 빈문자열이거나 정규화 후
    어느 한 쪽이 2글자 미만이면 정확 동등(1.0) 외에는 매칭을 인정하지 않는다.

    Args:
        a: 비교 문자열 1
        b: 비교 문자열 2

    Returns:
        0.0(무매칭) ~ 1.0(완전 동등) 사이의 신뢰도 점수
    """
    na = _normalize(a)
    nb = _normalize(b)
    if not na or not nb:
        return 0.0

    # (1) 정확 동등 — 최우선(무변경 경로)
    if na == nb:
        return 1.0

    # 가드: 정규화 후 어느 한 쪽이 2글자 미만이면 근사 매칭 금지(오매칭 차단)
    if len(na) < _MIN_FUZZY_LEN or len(nb) < _MIN_FUZZY_LEN:
        return 0.0

    # (2) 구분자 제거 동등
    sa = _strip_sep(na)
    sb = _strip_sep(nb)
    if sa and sb and sa == sb:
        return _SEP_EQUAL_SCORE

    # (3) 부분어 포함(짧은 쪽이 긴 쪽에 포함) — 길이비로 신뢰도 가중
    containment = 0.0
    if sa and sb:
        short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
        if short and short in long:
            ratio = len(short) / len(long)
            containment = _CONTAINMENT_BASE + (_CONTAINMENT_MAX - _CONTAINMENT_BASE) * ratio

    # (4) 자모 편집거리 근사
    edit = _edit_similarity(sa, sb)

    return max(containment, edit)


def best_flex_match(
    term: str,
    candidates: Iterable[str],
    min_score: float,
) -> tuple[Optional[str], float]:
    """candidates 중 term과 가장 잘 맞는 후보와 신뢰도를 반환한다.

    Args:
        term: 기준 문자열(질의어·필드명 등)
        candidates: 비교 대상 후보 문자열들
        min_score: 확정 매칭으로 인정할 최소 신뢰도

    Returns:
        (best_candidate, best_score) 튜플.
        - best_score >= min_score이면 best_candidate는 확정 후보 문자열.
        - 미만이면 best_candidate는 None(확정 아님)이고 best_score는 최고 근사치
          (호출부가 "후보 제시" 여부를 판단할 수 있도록 점수는 항상 반환).
    """
    best_cand: Optional[str] = None
    best_score = 0.0
    for cand in candidates:
        score = flex_match_score(term, cand)
        if score > best_score:
            best_score = score
            best_cand = cand
    if best_cand is not None and best_score >= min_score:
        return best_cand, best_score
    return None, best_score
