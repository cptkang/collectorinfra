"""다중 후보 SQL 생성 (Plan 61 트랙 A / E2·E3 / D-073).

문헌(CHASE-SQL·XiYan-SQL, §0.2-1): 후보의 이득은 "다양성"에서 온다. 서로 다른 추론
경로(base·divide-and-conquer·실행계획 CoT)로 N개 후보를 생성한다.

**삽입 지점 원칙(§3)**: 이 모듈은 그래프 노드가 아니라 `query_generator` 함수 내부와
`multi_db_executor._generate_sql`에서 **공유 헬퍼**로 호출된다 — 경로 (A)그래프·
(B)orchestration 인라인·(C)멀티 DB가 동일 로직을 공유(단일/멀티 비대칭 방지, D-066 계열).

기본 OFF(`TEXT2SQL_MULTI_CANDIDATE=false`): 호출부가 이 모듈에 진입하지 않아 기존 단일
생성 경로가 바이트 무변경(회귀 0).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from src.prompts.candidate_strategies import apply_strategy, strategy_names

logger = logging.getLogger(__name__)

# E3 복잡도 신호 — 집계·그룹·순위·범위 비교(HAVING 유발)·다중 속성 등.
_COMPLEX_KEYWORDS = (
    "평균", "최대", "최소", "합계", "건수", "개수", "상위", "하위", "순위", "top",
    "그룹", "별로", "각각", "이상", "초과", "미만", "이하", "비율", "사용률",
    "추이", "증가", "감소", "많은", "적은", "정렬",
)


def classify_complexity(
    user_query: str,
    parsed_requirements: Optional[dict] = None,
    schema_info: Optional[dict] = None,
) -> str:
    """질의 복잡도를 결정적 규칙으로 분류한다(E3 게이트 입력).

    복잡 신호: 다중 query_target, 집계/그룹/순위/범위비교 키워드, EAV 다중 속성 피벗.
    신호가 없으면 "simple"(단일 후보로 비용 절감). 모호하면 보수적으로 "complex".

    Returns:
        "simple" | "complex"
    """
    q = (user_query or "").lower()
    reqs = parsed_requirements or {}

    targets = reqs.get("query_targets") or []
    if isinstance(targets, list) and len(targets) > 1:
        return "complex"

    filters = reqs.get("filter_conditions") or []
    if isinstance(filters, list) and len(filters) >= 2:
        return "complex"

    # EAV 다중 속성/피벗 신호: 구조 메타에 EAV 패턴이 있고 질의가 복수 속성/집계를 요구
    meta = (schema_info or {}).get("_structure_meta") or {}
    has_eav = any(
        p.get("type") == "eav" for p in (meta.get("patterns") or [])
    )
    if has_eav and any(k in q for k in ("사용률", "코어", "메모리", "용량", "평균", "최대")):
        return "complex"

    if any(k in q for k in _COMPLEX_KEYWORDS):
        return "complex"

    return "simple"


def _norm_sql(sql: str) -> str:
    """중복 후보 제거용 정규화(공백 접기·소문자·세미콜론 제거)."""
    return " ".join((sql or "").lower().split()).rstrip(";")


def _prior_confidence(strategy: str) -> float:
    """전략별 초기 신뢰도(선택 단계에서 결과 일치도로 재계산되는 사전값)."""
    return 1.0 if strategy.split("#", 1)[0] == "base" else 0.95


async def generate_candidates(
    llm: Any,
    base_system_prompt: str,
    base_user_prompt: str,
    *,
    count: int,
    strategies: str,
    is_kbgenai: bool,
    extract_sql: Callable[[str], str],
) -> list[dict]:
    """N개 후보 SQL을 서로 다른 프롬프트 전략으로 생성한다.

    Args:
        llm: LLM 인스턴스(ainvoke 지원).
        base_system_prompt / base_user_prompt: 각 경로가 이미 구성한 기본 프롬프트.
        count: 후보 수 N(`TEXT2SQL_CANDIDATE_COUNT`).
        strategies: "multi_prompt" | "temperature".
        is_kbgenai: KBGenAIChat 여부(System 다음 AIMessage 순서 요구).
        extract_sql: 응답 content → SQL 추출 함수(경로별 주입).

    Returns:
        [{"sql": str, "strategy": str, "confidence": float}, ...]
        중복 SQL은 제거하며, 최소 1건은 반환(전부 빈 SQL이면 빈 리스트).
    """
    names = strategy_names(count, strategies)

    async def _one(strategy: str, temperature: Optional[float]) -> tuple[str, str]:
        user = apply_strategy(base_user_prompt, strategy)
        messages = [
            SystemMessage(content=base_system_prompt),
            AIMessage(content="") if is_kbgenai else None,
            HumanMessage(content=user),
        ]
        messages = [m for m in messages if m is not None]
        invoker = llm
        if temperature is not None:
            # temperature 다양화는 best-effort — 미지원 provider는 무시(무해).
            try:
                invoker = llm.bind(temperature=temperature)
            except Exception:  # noqa: BLE001
                invoker = llm
        response = await invoker.ainvoke(messages)
        return strategy, extract_sql(getattr(response, "content", "") or "")

    # temperature 전략: 0.0부터 단조 증가시켜 재샘플 다양화(multi_prompt은 None=기본).
    temps: list[Optional[float]] = []
    for i in range(len(names)):
        if strategies == "temperature":
            temps.append(round(0.2 * i, 2))
        else:
            temps.append(None)

    results = await asyncio.gather(
        *[_one(names[i], temps[i]) for i in range(len(names))],
        return_exceptions=True,
    )

    candidates: list[dict] = []
    seen: set[str] = set()
    for r in results:
        if isinstance(r, Exception):
            logger.warning("후보 생성 1건 실패(무시): %s", r)
            continue
        strategy, sql = r
        sql = (sql or "").strip()
        if not sql:
            continue
        norm = _norm_sql(sql)
        if norm in seen:
            continue
        seen.add(norm)
        candidates.append(
            {"sql": sql, "strategy": strategy, "confidence": _prior_confidence(strategy)}
        )

    logger.info(
        "다중 후보 생성: 요청 %d · 고유 %d (전략=%s)", count, len(candidates), strategies
    )
    return candidates
