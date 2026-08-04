"""LLM 구현별 호환 판정 유틸 (Plan 69 P2).

KBGenAIChat 판정이 3형태(isinstance / 클래스명 문자열 비교 / bool 인자 주입)로
11곳에 흩어져 있던 것의 단일 출처. 클래스명 비교를 쓰는 이유: 판정 소비처
(nodes·utils)가 clients 계층의 무거운 임포트 없이 판정할 수 있어야 하고,
KBGenAIChat은 서브클래스가 없어(전수 grep) isinstance와 동작이 같다.

계층: utils.
"""

from __future__ import annotations

from typing import Any


def is_kbgenai(llm: Any) -> bool:
    """KBGenAIChat 여부 — 메시지 순서 규약(System 다음 빈 AIMessage) 적용 대상 판정."""
    return type(llm).__name__ == "KBGenAIChat"
