"""대역 LLM에 `astream`을 붙이는 헬퍼.

`output_generator`의 텍스트 응답은 `ainvoke`가 아니라 `src.llm.astream_text`(=`llm.astream`)로
흐른다. `AsyncMock()`은 `astream`을 코루틴으로 만들어 주므로 `async for`가
"'async for' requires an object with __aiter__ method, got coroutine"으로 깨진다 —
대역에 스트리밍 표면을 명시로 달아 준다(2026-09-21).
"""

from __future__ import annotations

from typing import Any


def attach_astream(llm: Any, next_message) -> Any:
    """`llm.astream`을 청크 1개짜리 async generator로 채운다.

    Args:
        llm: 대역 LLM (AsyncMock 등)
        next_message: 호출 때마다 다음 응답 메시지를 돌려주는 콜러블
    """

    def _astream(messages, config=None, **_kwargs):
        async def _gen():
            yield next_message()

        return _gen()

    llm.astream = _astream
    return llm
