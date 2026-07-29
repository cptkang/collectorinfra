"""용어 해소 도구 — 유사어 조회·실측 값 검색 (Plan 67 Phase S1 §4.2).

사용자 표현("사용률", "김포")을 실제 컬럼·리터럴로 잇는 두 도구를 순수 함수로 제공한다.
유사어 사전·값 인덱스는 **인자로 주입**한다(캐시 I/O는 infrastructure 담당).
"""

from __future__ import annotations

from typing import Callable, Optional

from src.schema_cache.value_index import search_value_index as _search_value_index
from src.utils.flex_match import best_flex_match
from src.utils.synonym_governance import rank_synonym_candidates

# 매칭 단계 라벨 — LLM·감사 로그에 "무엇으로 찾았는지" 근거를 남긴다.
STAGE_EXACT = "exact"
STAGE_FUZZY = "fuzzy"
STAGE_EMBEDDING = "embedding"

# 1글자 유사어는 오매칭 위험이 커 제외한다(schema_analyzer 유사어 매칭과 동일 규칙).
_MIN_TERM_LEN = 2

# 임베딩 검색 함수 시그니처: (용어, {컬럼키: [단어들]}) → [(컬럼키, 점수)] 내림차순
SemanticFn = Callable[[str, dict[str, list[str]]], list[tuple[str, float]]]


def _candidate_words(col_key: str, words: Optional[list[str]]) -> list[str]:
    """컬럼의 비교 후보 단어(등록 유사어 + 컬럼명 자체)를 만든다."""
    candidates = [str(w) for w in (words or []) if w and len(str(w)) >= _MIN_TERM_LEN]
    col_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
    if col_name and len(col_name) >= _MIN_TERM_LEN:
        candidates.append(col_name)
    return candidates


def lookup_synonym(
    term: str,
    synonyms: dict[str, list[str]],
    *,
    min_score: float = 0.85,
    fuzzy: bool = True,
    semantic_fn: Optional[SemanticFn] = None,
    semantic_min_score: float = 0.65,
    meta: Optional[dict[str, dict]] = None,
    limit: int = 5,
) -> list[dict]:
    """용어에 대응하는 컬럼 후보를 정확 → 퍼지 → 임베딩 계단으로 찾는다.

    앞 단계에서 후보가 나오면 뒤 단계는 실행하지 않는다(기존 유사어 매칭 계단과 동일).
    임베딩 단계는 ``semantic_fn``이 주입됐을 때만 동작하므로, 의미 검색이 꺼진 기본
    설정에서는 호출 자체가 발생하지 않는다.

    Args:
        term: 사용자 표현
        synonyms: {테이블.컬럼: [유사어, ...]} 사전
        min_score: 퍼지 매칭 확정 임계
        fuzzy: 퍼지 단계 사용 여부
        semantic_fn: 임베딩 검색 함수(없으면 임베딩 단계 생략)
        semantic_min_score: 임베딩 확정 임계
        meta: {테이블.컬럼: {source, usage_count, ...}} 유사어 메타(우선순위 정렬용)
        limit: 반환 상한

    Returns:
        [{column, score, stage, source, usage_count}] — 유사어 거버넌스 우선순위 정렬
    """
    if not term or len(term.strip()) < _MIN_TERM_LEN or not synonyms:
        return []
    term_low = term.strip().lower()
    meta = meta or {}

    hits: list[tuple[str, float, str]] = []  # (컬럼키, 점수, 단계)

    # ① 정확 — 등록 유사어/컬럼명과 완전 일치하거나 서로를 포함하는 경우
    for col_key, words in synonyms.items():
        for cand in _candidate_words(col_key, words):
            cand_low = cand.lower()
            if cand_low == term_low or cand_low in term_low or term_low in cand_low:
                hits.append((col_key, 1.0, STAGE_EXACT))
                break

    # ② 퍼지 — 자모·편집거리 근사(확정 임계 이상만)
    if not hits and fuzzy:
        for col_key, words in synonyms.items():
            cand, score = best_flex_match(term_low, _candidate_words(col_key, words), min_score)
            if cand is not None:
                hits.append((col_key, score, STAGE_FUZZY))

    # ③ 임베딩 — 주입됐을 때만(기본 OFF 존중)
    if not hits and semantic_fn is not None:
        candidate_map = {
            col_key: words
            for col_key, words in (
                (k, _candidate_words(k, v)) for k, v in synonyms.items()
            )
            if words
        }
        for col_key, score in semantic_fn(term_low, candidate_map):
            if score >= semantic_min_score:
                hits.append((col_key, float(score), STAGE_EMBEDDING))

    candidates = [
        {
            "column": col_key,
            "score": round(score, 3),
            "stage": stage,
            "source": (meta.get(col_key) or {}).get("source", "llm"),
            "usage_count": int((meta.get(col_key) or {}).get("usage_count") or 0),
            "confidence": score,
        }
        for col_key, score, stage in hits
    ]
    ranked = rank_synonym_candidates(candidates)
    for cand in ranked:
        cand.pop("confidence", None)
    return ranked[:limit]


def search_value_index(
    keywords: list[str],
    index: dict[str, list[str]],
    *,
    fuzzy: bool = False,
    min_score: float = 0.85,
    max_per_key: int = 20,
) -> dict[str, list[str]]:
    """질의 키워드로 값 인덱스를 검색해 실존하는 리터럴만 돌려준다.

    필터 값을 LLM이 지어내지 못하게, 실제 DB에서 수집한 distinct 값 중 매칭되는 것만
    노출한다(값 인덱스는 인자 주입 — 수집·캐시는 infrastructure 담당).

    Args:
        keywords: 질의에서 뽑은 키워드 목록
        index: {인덱스 키: [실측 값, ...]}
        fuzzy: 유연 근사 매칭 사용 여부(기본 OFF = 정확 부분어)
        min_score: 유연 매칭 확정 임계
        max_per_key: 키당 반환 상한

    Returns:
        {인덱스 키: [매칭 리터럴, ...]} (매칭된 키만 포함)
    """
    if not index or not keywords:
        return {}
    return _search_value_index(
        index,
        [str(k) for k in keywords if k],
        fuzzy=fuzzy,
        min_score=min_score,
        max_per_key=max_per_key,
    )
