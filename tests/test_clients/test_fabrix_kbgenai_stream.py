"""KBGenAIChat SSE 스트림 파싱 가드 회귀 테스트.

라이브 실측(2026-08-03, 금감원 양식 폼필): FabriX가 `data: null` 라인을 보내면
json.loads는 성공(None 반환)하므로 JSONDecodeError로 걸러지지 않고,
line_json.get에서 AttributeError('NoneType' object has no attribute 'get')가
발생해 최종 응답 생성이 통째로 실패했다. non-dict 라인·content null은
스킵하고 정상 content 라인만 청크로 나와야 한다.
"""

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from src.clients.fabrix_kbgenai import KBGenAIChat


def _make_client() -> KBGenAIChat:
    return KBGenAIChat(
        endpoint_url="https://fabrix.test/api/chat",
        x_openapi_token="test-token",
        x_generative_ai_client="test-client",
        asset_id="test-asset",
    )


def _mock_response(lines: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.iter_lines.return_value = iter(lines)
    return resp


def _collect_stream(lines: list[str]) -> list[str]:
    client = _make_client()
    with patch("src.clients.fabrix_kbgenai.requests.post") as mock_post:
        mock_post.return_value = _mock_response(lines)
        chunks = list(client._stream([HumanMessage(content="질의")]))
    return [c.message.content for c in chunks]


def test_stream_skips_null_line():
    """`data: null` 라인(json.loads→None)은 크래시 없이 스킵돼야 한다."""
    contents = _collect_stream(
        [
            "data: null",
            'data: {"content": "안녕", "event_status": ""}',
        ]
    )
    assert contents == ["안녕"]


@pytest.mark.parametrize(
    "weird_line",
    ["data: 123", 'data: "just a string"', "data: [1, 2]", "data: true"],
)
def test_stream_skips_non_dict_json(weird_line: str):
    """dict가 아닌 유효 JSON 라인(숫자·문자열·리스트·불리언)도 스킵."""
    contents = _collect_stream(
        [weird_line, 'data: {"content": "ok", "event_status": ""}']
    )
    assert contents == ["ok"]


def test_stream_null_content_does_not_crash():
    """`"content": null`은 remove_llm_junk(.replace) 크래시 없이 빈 청크로 스킵."""
    contents = _collect_stream(
        [
            'data: {"content": null, "event_status": ""}',
            'data: {"content": "본문", "event_status": ""}',
        ]
    )
    assert contents == ["본문"]


def test_stream_invalid_json_still_skipped():
    """기존 동작 회귀 확인: 파싱 불가 라인은 종전대로 스킵."""
    contents = _collect_stream(
        ["data: {broken", 'data: {"content": "정상", "event_status": ""}']
    )
    assert contents == ["정상"]
