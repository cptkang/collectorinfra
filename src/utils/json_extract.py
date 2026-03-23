"""LLM 응답에서 JSON을 추출하는 유틸리티.

LLM이 markdown 코드블록(```json ... ```)으로 감싸거나,
순수 JSON, 또는 텍스트 안에 JSON을 포함하는 경우를 모두 처리한다.

여러 모듈(routing, nodes, document, schema_cache)에서 공통으로 사용한다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def extract_json_from_response(content: str) -> Optional[dict]:
    """LLM 응답에서 JSON 딕셔너리를 추출한다.

    다음 순서로 시도한다:
    1. ```json ... ``` 코드블록 내부
    2. 중괄호({...}) 기반 추출
    3. 전체 content를 JSON으로 파싱

    Args:
        content: LLM 응답 텍스트

    Returns:
        파싱된 딕셔너리 또는 None (파싱 실패 시)
    """
    # 1. ```json ... ``` 패턴
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. 중괄호 기반 추출
    brace_match = re.search(r"\{.*\}", content, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group())
        except json.JSONDecodeError:
            pass

    # 3. 전체 content 시도
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.debug("JSON 파싱 실패: %s", content[:200])
        return None
