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


# ── 답변 영역 소유(plans/102 X-7 · `ROUTER_CAPABILITY_OWNERSHIP_ENABLED`) ──
# **켜졌을 때만 쓰는 서브클래스**다.
#
# `STRUCTURED_OUTPUT_BACKEND=instructor`면 응답 모델의 JSON 스키마가 프롬프트 말미에 그대로
# 실린다(`instructor_adapter._korean_schema_block` → `model_json_schema()`). 기존 모델에 필드를
# 더하면 플래그 off에서도 LLM에 전달되는 스키마가 바뀌므로, 필드는 **별도 서브클래스**에만
# 둔다 — off는 위 모델 그대로다. 코드 값 검증(카탈로그 대조)은 스키마가 아니라
# `_validate_db_entries`가 한다(한 항목 오류로 전체를 버리지 않는 E-2 원칙 — 스키마에 enum을
# 걸면 재질의·전체 실패로 번진다).


class OwnershipRouterDatabase(RouterDatabase):
    """선택된 DB 하나 + 그 DB에서 답할 답변 영역 코드."""

    capabilities: list[str] = Field(default_factory=list)


class OwnershipRouterDecision(RouterDecision):
    """라우터 분류 결과 + 교차 체인 신호.

    `chain`: 앞 영역의 결과가 뒤 영역의 조회 대상을 정할 때만 답변 영역 코드를 조회 순서대로
    담는다. 서로 독립이면 빈 목록이다.
    """

    databases: list[OwnershipRouterDatabase] = Field(default_factory=list)
    chain: list[str] = Field(default_factory=list)


class OwnershipDatabaseSelection(DatabaseSelection):
    """2단계(DB 선택) 출력 + 답변 영역 · 교차 체인 신호 — 단일 호출 계약과 대칭."""

    databases: list[OwnershipRouterDatabase] = Field(default_factory=list)
    chain: list[str] = Field(default_factory=list)


# ── 3단 계획 필요 신호(plans/103 §3.2 · `TIER3_PLAN_LOOP_ENABLED`) ──
# 소유 서브클래스와 같은 이유로 **켜졌을 때만 쓰는 서브클래스**다 — 기존 모델에 필드를 더하면
# 플래그 off에서도 instructor 스키마 블록이 바뀐다. 소유 플래그와 곱으로 조합된다.


class PlanRouterDecision(RouterDecision):
    """라우터 분류 결과 + 계획 필요 신호(하위 조회가 둘 이상이거나 앞 결과가 뒤 조회를 정함)."""

    needs_plan: bool = False


class OwnershipPlanRouterDecision(OwnershipRouterDecision):
    """답변 영역 소유 분류 결과 + 계획 필요 신호."""

    needs_plan: bool = False
