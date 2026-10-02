"""LLM 스트리밍 응답의 콘텐츠 블록 리스트 정규화 회귀 테스트.

Gemini 3.x thinking 계열은 `chunk.content`를 **콘텐츠 블록 리스트**로 반환한다
(2026-08-04 E1 라이브 실측 — 목은 str만 반환해 미검출). 이때 누적·전달 지점이
str을 가정하면 사용자 응답에 파이썬 repr(`[{'type': 'text', ...}]`)이 그대로
실려 마크다운 표가 한 줄 텍스트로 렌더된다(웹 화면 깨짐, CSV는 정상).

`coerce_content_text`가 이미 있었으나 소비처 4곳만 교정되고 **최종 응답 경로**
(`astream_text`·SSE 토큰)가 누락돼 있던 것의 재발 방지.
"""

from __future__ import annotations

import pytest

from src.api.routes.query import _token_text
from src.llm import astream_text

_TABLE_MD = "## 조회 결과\n\n| 서버명 | CPU |\n|---|---|\n| web01 | 82.1 |\n"


class _Chunk:
    def __init__(self, content):
        self.content = content


class _FakeLLM:
    """astream이 지정한 청크들을 흘리는 최소 대역."""

    def __init__(self, contents):
        self._contents = contents

    def astream(self, messages, config=None):
        async def _gen():
            for c in self._contents:
                yield _Chunk(c)

        return _gen()


class TestAstreamTextBlockContent:
    @pytest.mark.asyncio
    async def test_block_list_yields_plain_text(self):
        """블록 리스트 content는 text만 이어 붙여 마크다운 원문을 보존한다."""
        llm = _FakeLLM([
            [{"type": "text", "text": "## 조회 결과\n\n| 서버명 | CPU |\n|---|---|\n"}],
            [{"type": "text", "text": "| web01 | 82.1 |\n"}],
        ])
        assert await astream_text(llm, []) == _TABLE_MD

    @pytest.mark.asyncio
    async def test_no_python_repr_leaks(self):
        """repr 흔적(`{'type':`)이 응답에 새지 않는다."""
        llm = _FakeLLM([[{"type": "text", "text": "표\n"}]])
        out = await astream_text(llm, [])
        assert "'type'" not in out and "{" not in out

    @pytest.mark.asyncio
    async def test_str_content_unchanged(self):
        """기존 str 청크 경로는 바이트 불변이다."""
        llm = _FakeLLM(["## 조회 결과\n", "| a | b |\n"])
        assert await astream_text(llm, []) == "## 조회 결과\n| a | b |\n"

    @pytest.mark.asyncio
    async def test_thinking_block_dropped(self):
        """text 키가 없는 블록(thinking 등)은 응답에 실리지 않는다."""
        llm = _FakeLLM([[{"type": "thinking", "thinking": "속으로"}, {"type": "text", "text": "답"}]])
        assert await astream_text(llm, []) == "답"


class TestSSETokenNormalization:
    """SSE 토큰 이벤트도 같은 정규화를 거쳐야 한다(프론트는 문자열만 이어 붙인다)."""

    def test_block_list_chunk(self):
        assert _token_text(_Chunk([{"type": "text", "text": "| a |"}])) == "| a |"

    def test_str_chunk_unchanged(self):
        assert _token_text(_Chunk("| a |")) == "| a |"

    def test_missing_content(self):
        assert _token_text(object()) == ""
