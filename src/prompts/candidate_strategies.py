"""다중 후보 생성 프롬프트 전략 (Plan 61 트랙 A / E2 / D-073).

CHASE-SQL·XiYan-SQL 문헌 근거(§0.2-1): 후보의 이득은 "많음"이 아니라 "다양성"에서
온다. 단순 temperature 재샘플링은 유사 오류를 반복하기 쉬우므로, **서로 다른 추론
경로**(divide-and-conquer, 실행계획 기반 CoT)를 1순위로 둔다.

각 전략은 이미 구성된 기본(base) 프롬프트에 **추가 지시(suffix)만** 덧붙인다 —
스키마·유사어·엔진 방언 등 정확성 근거는 base가 그대로 보유하므로, 전략 suffix가
그것을 바꾸지 않는다(회귀 안전). 최종 SELECT/출력 형식 계약도 base를 따른다.
"""

from __future__ import annotations

# 전략 식별자 — config `candidate_strategies=multi_prompt`일 때 이 순서로 순환한다.
STRATEGY_BASE = "base"
STRATEGY_DIVIDE_CONQUER = "divide_conquer"
STRATEGY_EXEC_PLAN_COT = "exec_plan_cot"

# 기본 3전략(N=3 기본값). count>3이면 순환하며 temperature 보조 다양화가 붙는다.
MULTI_PROMPT_ORDER: list[str] = [
    STRATEGY_BASE,
    STRATEGY_DIVIDE_CONQUER,
    STRATEGY_EXEC_PLAN_COT,
]

_DIVIDE_CONQUER_SUFFIX = """

[후보 생성 전략 — 분할 정복(divide-and-conquer)]
질의를 독립적으로 검증 가능한 하위 문제로 분해한 뒤 조립하세요.
- 필요한 엔터티/집계/필터를 개별 하위질의(CTE, WITH 절)로 먼저 정의하고,
  마지막에 결합하여 최종 SELECT를 완성하세요.
- 각 CTE는 한 가지 관심사만 담당하게 하여 조인·집계 스코프 오류를 줄이세요.
- 위 스키마·조인 규칙·유사어 매핑은 그대로 준수하며, 최종 출력 컬럼/형식 계약도 동일합니다.
"""

_EXEC_PLAN_COT_SUFFIX = """

[후보 생성 전략 — 실행계획 기반 추론(execution-plan CoT)]
SQL을 쓰기 전에 실행 순서를 먼저 설계하세요(주석이나 사고과정으로 남기지 말고 결과 SQL에 반영).
- 어떤 테이블을 기준(driving)으로 시작할지, 어떤 순서로 조인할지, 어느 단계에서 필터·집계를
  적용해야 행이 잘못 걸러지지 않을지 판단한 뒤 그 계획대로 SQL을 작성하세요.
- 특히 EAV 피벗/자식 리소스 조인은 집계(GROUP BY) 이전에 필터가 대상 행을 제거하지 않도록
  실행 순서를 고려하세요(HAVING vs WHERE).
- 위 스키마·조인 규칙·유사어 매핑을 그대로 준수하며, 최종 출력 컬럼/형식 계약도 동일합니다.
"""

_STRATEGY_SUFFIX: dict[str, str] = {
    STRATEGY_BASE: "",
    STRATEGY_DIVIDE_CONQUER: _DIVIDE_CONQUER_SUFFIX,
    STRATEGY_EXEC_PLAN_COT: _EXEC_PLAN_COT_SUFFIX,
}


# ──────────────────────────────────────────────
# E4 후보 선택 — LLM 쌍대비교 폴백 프롬프트 (hybrid/llm)
# ──────────────────────────────────────────────

SELECTOR_SYSTEM_TEMPLATE = """당신은 Text-to-SQL 후보 선택 심판입니다.
사용자 질의와 여러 SQL 후보(각 후보의 실행 결과 샘플 포함)가 주어집니다.
질의 의도를 **가장 정확하게** 충족하는 후보 하나를 고르세요.

판단 기준(우선순위):
1. 질의가 요구한 컬럼·필터·집계·정렬을 정확히 반영했는가.
2. 결과가 비어있지 않고 의미상 타당한가(빈 결과·명백한 오류 결과는 감점).
3. 불필요한 컬럼/조인이 없는가.

반드시 아래 JSON 한 줄만 출력하세요(설명 금지):
{{"choice": <후보번호 정수>, "reason": "<간단한 근거>"}}"""

SELECTOR_USER_TEMPLATE = """[사용자 질의]
{user_query}

[후보 목록]
{candidates_block}

가장 적합한 후보 번호를 JSON으로 답하세요."""


def strategy_names(count: int, strategies: str) -> list[str]:
    """후보 수 N과 다양화 방식에 따라 각 후보의 전략 이름을 반환한다.

    - multi_prompt: MULTI_PROMPT_ORDER를 순환(중복 시 뒤에 `#k` 표기로 구분).
    - temperature: 전부 base 전략(다양화는 temperature로 — llm 지원 시).
    """
    count = max(1, int(count))
    if strategies == "temperature":
        return [STRATEGY_BASE for _ in range(count)]
    names: list[str] = []
    for i in range(count):
        base = MULTI_PROMPT_ORDER[i % len(MULTI_PROMPT_ORDER)]
        cycle = i // len(MULTI_PROMPT_ORDER)
        names.append(base if cycle == 0 else f"{base}#{cycle}")
    return names


def apply_strategy(base_user_prompt: str, strategy: str) -> str:
    """기본 user 프롬프트에 전략 suffix를 덧붙인다(base·미지정은 무변경).

    `name#k`(순환 표기)는 base 전략명으로 정규화하여 suffix를 찾는다.
    """
    key = strategy.split("#", 1)[0]
    suffix = _STRATEGY_SUFFIX.get(key, "")
    return base_user_prompt + suffix if suffix else base_user_prompt
