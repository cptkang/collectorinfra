"""사용법·지원 소스 문의 판정(plans/116 §10.3 — 결정적 · LLM 0).

1·2단(`intent_planner`)·3단(`semantic_router`) 단락과 `general_inference` 노드가 같은 판정을
쓴다. 라우팅(infrastructure)이 노드(application)를 import 할 수 없어 utils 에 둔다.
"""

from __future__ import annotations

# 이 판정이 맞으면 안내문을 코드가 조립한다 — LLM 에 맡기면 「데이터베이스 직접 쿼리는
# 불가능」·없는 소스(문서·AWS)를 지어냈다(로컬 9B 녹화).
# 강한 표현은 단독으로, 「사용법」류는 에이전트를 가리킬 때만 인정한다
# ("쿠버네티스 사용법"은 개념 질문).
_USAGE_STRONG_PHRASES = (
    "지원 가능한 소스", "지원하는 소스", "지원 소스", "조회 가능한 데이터", "조회할 수 있는 데이터",
    "뭘 할 수 있", "무엇을 할 수 있", "뭐 할 수 있", "뭘 조회할 수 있", "어떤 기능이 있",
)
_USAGE_WORDS = ("사용법", "사용 방법", "이용 방법", "도움말")
_AGENT_REFERENCES = ("에이전트", "이 시스템", "이 서비스", "챗봇", "너는", "너의", "당신")
_USAGE_FILLERS = ("알려줘", "알려 줘", "알려주세요", "보여줘", "좀", "?", "!", ".")


def is_usage_query(query: str) -> bool:
    """에이전트 사용법·지원 소스 문의인지 결정적으로 판정한다(LLM 0)."""
    text = (query or "").strip()
    if any(p in text for p in _USAGE_STRONG_PHRASES):
        return True
    if not any(w in text for w in _USAGE_WORDS):
        return False
    if any(r in text for r in _AGENT_REFERENCES):
        return True
    # 「사용법 알려줘」처럼 대상 없이 사용법만 물으면 이 에이전트의 사용법이다.
    rest = text
    for token in (*_USAGE_WORDS, *_USAGE_FILLERS):
        rest = rest.replace(token, "")
    return not rest.strip()
