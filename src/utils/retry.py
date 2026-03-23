"""비동기 재시도 유틸리티.

exponential backoff 기반의 재시도 로직을 제공한다.
LLM API 호출, DB 연결 등 실패 가능한 비동기 작업에 사용한다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs: object,
) -> T:
    """exponential backoff로 비동기 함수를 재시도한다.

    Args:
        func: 재시도할 비동기 함수
        max_retries: 최대 재시도 횟수 (기본 3)
        base_delay: 기본 대기 시간 (초, 기본 1.0)
        **kwargs: func에 전달할 키워드 인자

    Returns:
        func의 반환값

    Raises:
        Exception: 최대 재시도 초과 시 마지막 예외를 재발생
    """
    for attempt in range(max_retries):
        try:
            return await func(**kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(
                f"재시도 {attempt + 1}/{max_retries}, {delay}초 후: {e}"
            )
            await asyncio.sleep(delay)
    # 이 지점에 도달하지 않지만 타입 체커를 위해 추가
    raise RuntimeError("retry_with_backoff: 도달 불가능한 코드")
