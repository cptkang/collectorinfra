"""라우터 출력 계약 (Plan 79 트랙 E-3b / D-169).

`_llm_classify`가 LLM에서 받는 구조의 타입 계약이다. E-1·E-2가 코드 가드로 막은 것을
여기서는 **계약으로** 표현한다 — 다만 가드를 대체하지 않는다(플래그 off 경로가 상시 존재).

계층: infrastructure.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.prompts.semantic_router import allowed_intents


class RouterDatabase(BaseModel):
    """선택된 DB 하나와 그 관련도."""

    db_id: str
    relevance_score: float
    sub_query_context: str = ""
    user_specified: bool = False
    reason: str = ""


class IntentDecision(BaseModel):
    """1단계(intent 분류) 출력 계약 (Plan 79 트랙 B / WU-D2).

    **라벨 하나 + 자기보고 확신도**다. 순수 label-only가 아닌 이유는 신뢰도를 실을 자리가
    필요해서인데(B-1-5·B-2-1), **첫 토큰은 여전히 라벨**이므로 C-0 요구(그 자리의 logprob이
    곧 의도 신뢰도)는 충족된다 — 라우터 평면 이동 시 `confidence`를 logprob으로 갈아끼운다.

    `confidence`는 **잠정**이다: 모델이 스스로 매긴 값이라 교정 기반이 없다
    (S-3가 `MIN_RELEVANCE_SCORE=0.3`에 대해 고정한 문제와 같은 성격 — SPEC M-3).
    """

    intent: str
    confidence: Optional[float] = None

    @classmethod
    def validate_intent_against(
        cls, value: str, *, fault_diagnosis_enabled: bool
    ) -> bool:
        """허용 집합 대조 — 정본은 `allowed_intents()`다(D-053 · `RouterDecision`과 동일)."""
        return value in allowed_intents(fault_diagnosis_enabled=fault_diagnosis_enabled)


class DatabaseSelection(BaseModel):
    """2단계(DB 선택) 출력 계약 (Plan 79 트랙 B / WU-D2).

    **`intent`가 없다.** 2단계는 이미 확정된 의도 위에서 DB만 고른다(B-1-1).
    항목 타입은 기존 `RouterDatabase`를 **그대로 재사용**한다 — 멀티 DB 선택과
    `sub_query_context` 분리가 축소되지 않아야 하기 때문이다(79 §1.1 불변식).
    """

    databases: list[RouterDatabase] = Field(default_factory=list)


class RouterDecision(BaseModel):
    """라우터 분류 결과.

    `intent` 허용값은 **`allowed_intents()`가 정본**이다(WU-02와 같은 출처 · D-053).
    옵트인 플래그에 따라 집합이 달라지므로 검증 시점에 플래그를 넘겨야 한다.
    """

    intent: str
    databases: list[RouterDatabase] = Field(default_factory=list)

    @classmethod
    def validate_intent_against(
        cls, value: str, *, fault_diagnosis_enabled: bool
    ) -> bool:
        return value in allowed_intents(fault_diagnosis_enabled=fault_diagnosis_enabled)
