"""스트림 합성 — 완성된 응답 1건을 `chat.completion.chunk` SSE로 바꾼다 (plans/148 §3.5).

PoC는 업스트림을 항상 비스트림으로 부르고, 호출자가 `stream:true`면 결과로 청크를 합성한다:
role 청크 → (content 청크) → (tool_calls 청크 — 인자 전체 JSON 한 조각) → finish 청크 → `[DONE]`.
하트비트·업스트림 SSE 중계는 2단계다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def _event(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def completion_chunks(
    *,
    completion_id: str,
    created: int,
    model: str,
    message: dict[str, Any],
    finish_reason: str,
) -> Iterator[str]:
    """assistant message 하나를 SSE 이벤트 문자열들로 합성한다."""

    def chunk(delta: dict[str, Any], finish: str | None = None) -> str:
        return _event(
            {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )

    yield chunk({"role": "assistant", "content": ""})
    if message.get("content"):
        yield chunk({"content": message["content"]})
    calls = message.get("tool_calls") or []
    if calls:
        yield chunk(
            {
                "tool_calls": [
                    {
                        "index": i,
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": call["function"]["arguments"],
                        },
                    }
                    for i, call in enumerate(calls)
                ]
            }
        )
    yield chunk({}, finish_reason)
    yield "data: [DONE]\n\n"
