"""LLM 응답에서 JSON을 추출하는 유틸리티.

LLM이 markdown 코드블록(```json ... ```)으로 감싸거나,
순수 JSON, 또는 텍스트 안에 JSON을 포함하는 경우를 모두 처리한다.

여러 모듈(routing, nodes, document, schema_cache)에서 공통으로 사용한다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


def coerce_content_text(content: Any) -> str:
    """LLM 응답 content를 텍스트로 정규화한다.

    실 모델(Gemini 3.x thinking 계열 등)은 content를 콘텐츠 블록 리스트로 반환한다 —
    str 가정 시 정규식 파서가 TypeError로 죽는다(2026-08-04 E1 라이브 실측, 목은 str만
    반환해 미검출). column_deriver._message_text와 동일 규칙의 유틸 진입부 일괄 적용판.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def extract_json_from_response(content: str | list) -> Optional[dict]:
    """LLM 응답에서 JSON 딕셔너리를 추출한다.

    다음 순서로 시도한다:
    1. ```json ... ``` 코드블록 내부
    2. 중괄호({...}) 기반 추출
    3. 전체 content를 JSON으로 파싱

    Args:
        content: LLM 응답 텍스트(실 모델의 콘텐츠 블록 리스트 허용)

    Returns:
        파싱된 딕셔너리 또는 None (파싱 실패 시)
    """
    content = coerce_content_text(content)

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


def extract_json_array_from_response(content: str | list) -> Optional[list]:
    """LLM 응답에서 JSON 배열을 추출한다(딕셔너리판의 배열 변형).

    추출 순서는 ``extract_json_from_response``와 같고 대괄호([...])를 대상으로 한다.

    Args:
        content: LLM 응답 텍스트(실 모델의 콘텐츠 블록 리스트 허용)

    Returns:
        파싱된 리스트 또는 None (파싱 실패 시)
    """
    content = coerce_content_text(content)

    # 1. ```json [ ... ] ``` 패턴
    json_match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # 2. 대괄호 기반 추출
    bracket_match = re.search(r"\[.*\]", content, re.DOTALL)
    if bracket_match:
        try:
            return json.loads(bracket_match.group())
        except json.JSONDecodeError:
            pass

    # 3. 전체 content 시도
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.debug("JSON 배열 파싱 실패: %s", content[:200])
        return None


def strip_code_fence(text: str | list) -> str:
    """LLM 응답에서 markdown 코드블록 표시를 벗겨 내부 텍스트만 반환한다.

    JSON 객체·배열 어느 쪽이든 감싼 펜스만 제거하므로, 파싱 결과 타입을 호출부가
    직접 정하는 경우(객체/배열 혼용)에 사용한다.

    Args:
        text: LLM 응답 텍스트(실 모델의 콘텐츠 블록 리스트 허용)

    Returns:
        코드블록 내부 텍스트(펜스가 없으면 원문) — 양끝 공백 제거
    """
    stripped = coerce_content_text(text).strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", stripped, re.DOTALL)
    if fence_match:
        return fence_match.group(1).strip()
    return stripped
