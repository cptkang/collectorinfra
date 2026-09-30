"""plans/123 S-7(a) — 조회 대상 도메인 ↔ 표면어 표와 해석 고지 트리거(사용자 확정 2026-09-30).

고정하는 계약:
  1. **drift** — 표의 도메인 키 = 입력 파서 프롬프트의 가능한 값(설명 줄 · 출력 예시 목록 둘 다).
  2. 파서가 낸 대상이 원문에 표면어로 없으면 트리거 키 `unanchored_query_targets`에 싣는다 —
     첨부 양식 헤더·플레이스홀더는 원문으로 본다(파서 규칙 12 — 말의 해석이 아니다).
  3. 트리거는 **키와 로그만** — 응답·파싱본은 바꾸지 않는다. 요청 스코프라 두 상태 생성 함수가
     초기화한다(체크포인터 델타 병합).
"""

from __future__ import annotations

import re

import pytest

from src.domain.query_target_surfaces import surface_table, surfaces_of
from src.nodes.input_parser import _unanchored_query_targets
from src.prompts.input_parser import INPUT_PARSER_SYSTEM_PROMPT
from src.state import create_followup_input, create_initial_state


def _prompt_domains() -> tuple[list[str], list[str]]:
    described = re.search(r"\*\*query_targets\*\*:.*?가능한 값: (.+)", INPUT_PARSER_SYSTEM_PROMPT)
    example = re.search(r'"query_targets": \[(.+?)\]', INPUT_PARSER_SYSTEM_PROMPT)
    assert described and example
    return (re.findall(r'"([^"]+)"', described.group(1)),
            re.findall(r'"([^"]+)"', example.group(1)))


def test_table_domains_match_parser_vocabulary() -> None:
    described, example = _prompt_domains()
    assert described == example, "파서 프롬프트 두 곳의 도메인 목록이 서로 다르다"
    assert list(surface_table()) == described


def test_domain_name_is_always_a_surface() -> None:
    for domain, surfaces in surface_table().items():
        assert surfaces[0] == domain
        assert len(set(surfaces)) == len(surfaces)
    assert surfaces_of("WAS") == ("WAS",), "어휘 밖 값은 그 이름 하나"


def _parsed(query: str, targets: list[str]) -> dict:
    return {"original_query": query, "query_targets": targets}


@pytest.mark.parametrize(
    ("query", "targets", "expected"),
    [
        ("CP 사용률 높은 서버", ["서버", "CPU"], ["CPU"]),          # SW08 — 「CP」를 CPU로 해석
        ("CPU 사용률 높은 서버", ["서버", "CPU"], []),
        ("장비별 메모리 사용률", ["서버", "메모리"], []),            # 표면어 「장비」
        ("host별 cpu", ["서버", "CPU"], []),                        # 라틴 대소문자 무시
        ("디스크 용량", ["디스크", "파일시스템"], ["파일시스템"]),
        ("OS 버전 목록", ["서버설정"], []),
        ("그거 보여줘", ["서버", "서버"], ["서버"]),                 # 중복 제거
    ],
)
def test_unanchored_targets(query, targets, expected) -> None:
    state = {"user_query": query}
    assert _unanchored_query_targets(_parsed(query, targets), state, None) == expected


def test_attachment_headers_anchor_targets() -> None:
    """양식 헤더에서 도출한 도메인은 해석이 아니다(파서 규칙 12)."""
    parsed = _parsed("양식 채워줘", ["서버", "CPU", "메모리"])
    state = {"user_query": "양식 채워줘",
             "csv_sheet_data": {"S1": {"headers": ["호스트명", "CPU 사용률"]}}}
    template = {"sheets": [{"headers": ["메모리(GB)"]}], "placeholders": [], "tables": []}
    assert _unanchored_query_targets(parsed, state, template) == []
    assert _unanchored_query_targets(parsed, state, None) == ["메모리"]


def test_trigger_key_is_request_scoped() -> None:
    assert create_initial_state(user_query="q")["unanchored_query_targets"] is None
    assert create_followup_input("q")["unanchored_query_targets"] is None


@pytest.mark.asyncio
async def test_node_output_carries_trigger_without_touching_parse() -> None:
    """노드는 키만 더한다 — 파싱본(`query_targets`)은 그대로다(응답 불변)."""
    from types import SimpleNamespace

    from src.nodes.input_parser import input_parser

    reused = {"query_targets": ["서버", "CPU"], "filter_conditions": []}
    state = {"user_query": "CP 높은 서버", "reuse_parsed_requirements": reused}
    out = await input_parser(state, llm=SimpleNamespace(), app_config=SimpleNamespace())
    assert out["unanchored_query_targets"] == ["CPU"]
    assert out["parsed_requirements"]["query_targets"] == ["서버", "CPU"]
