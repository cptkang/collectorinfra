"""plans/143 3회차 반출 준비 — 회차 이력 문장을 LLM 프롬프트 밖으로(verifier 추가).

ITAM 지식 원천(`testdata/itam_bench/closed/knowledge/*.yaml` ·
`table_definitions.yaml`)과 빌더 산출물(`config/db_profiles/itam.yaml` ·
`config/knowledge/itam/*` · `config/synonym_seeds/itam.yaml`)의 **프롬프트로 렌더되는
문자열**에 회차·run 이력(「2회차에 …」「1회차 오답 사례」「두 회차 연속 오답」·
run ID)이 없음을 고정한다. 이력은 YAML 주석에만 둔다 — 주석은 파서에서 데이터가 아니다.

메타데이터 칸(`evidence`·`source_run`·`run_id`·`origin`·`status`)은 렌더되지 않아 대상 밖이다.
`tcdmsam63.manages`의 「점검 회차」는 도메인 낱말이라 숫자 없는 「회차」는 막지 않는다.
실 LLM·DB·Redis 0.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "testdata/itam_bench/closed/knowledge"

#: 회차·run 이력 표지 — 숫자 회차 · 「두 회차」 · run ID · 오답 판정 서술 · 반출 가림 · 시나리오 id
HISTORY = re.compile(
    r"\d\s*회차|[두세네]\s*회차|회차\s*연속|20\d{6}-\d{6}|오답|반출\s*가림|ITAM-\d+|이력\s*:"
)
#: 렌더되지 않는 메타데이터 칸
META_KEYS = frozenset({"evidence", "source_run", "run_id", "origin", "status", "generated"})


def _load(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _strings(node: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """메타데이터 칸을 뺀 모든 문자열 잎(키 포함 — 매핑 키도 렌더될 수 있다)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in META_KEYS:
                continue
            yield f"{path}.{key}#key", str(key)
            yield from _strings(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _strings(value, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


SOURCES = [
    KNOWLEDGE / "guide.yaml",
    KNOWLEDGE / "prompt_section.yaml",
    KNOWLEDGE / "descriptions.yaml",
    KNOWLEDGE / "synonyms.yaml",
    ROOT / "testdata/itam_bench/closed/table_definitions.yaml",
]
OUTPUTS = [
    ROOT / "config/db_profiles/itam.yaml",
    ROOT / "config/knowledge/itam/prompt_template.yaml",
    ROOT / "config/knowledge/itam/column_descriptions.yaml",
    ROOT / "config/synonym_seeds/itam.yaml",
]


@pytest.mark.parametrize("path", SOURCES + OUTPUTS, ids=lambda p: p.name)
def test_no_history_in_rendered_strings(path: Path) -> None:
    hits = [(where, HISTORY.findall(text)) for where, text in _strings(_load(path))]
    assert [h for h in hits if h[1]] == []


@pytest.mark.parametrize("name", ["guide.yaml", "prompt_section.yaml", "descriptions.yaml"])
def test_history_comments_are_not_data(name: str) -> None:
    """원천 주석 「# 이력: …」은 원문에 있고 파싱 결과에 없다(블록 스칼라에 빨려 들지 않음)."""
    raw = (KNOWLEDGE / name).read_text(encoding="utf-8")
    comments = [line for line in raw.splitlines() if line.lstrip().startswith("# 이력:")]
    assert comments  # 이력은 지워진 게 아니라 주석으로 옮겨졌다
    texts = [t for _, t in _strings(_load(KNOWLEDGE / name))]
    assert not any("이력:" in t or "회차" in t for t in texts)


def test_items_keep_only_known_fields() -> None:
    """주석 이동이 항목 칸을 바꾸지 않았다 — 칸 집합이 원천 형식 그대로."""
    for name, fields in (
        ("guide.yaml", {"id", "origin", "evidence", "status", "text"}),
        ("prompt_section.yaml", {"id", "origin", "evidence", "status", "text"}),
        ("descriptions.yaml", {"id", "table", "column", "origin", "evidence", "status", "text"}),
    ):
        items = _load(KNOWLEDGE / name)["items"]
        assert all(set(item) == fields for item in items), name


def test_table_purpose_block_has_no_history() -> None:
    """`(주의: notes)`로 렌더되는 테이블 용도 블록 — tcdmsif90 주의 문구에 이력이 없다."""
    from src.nodes.prompt_blocks import TABLE_DEFINITIONS_KEY, build_table_purpose_block

    profile = _load(ROOT / "config/db_profiles/itam.yaml")
    defs = profile["table_definitions"]
    block = build_table_purpose_block(
        {"_structure_meta": {TABLE_DEFINITIONS_KEY: defs}, "tables": {t: {} for t in defs}}
    )
    assert "- tcdmsif90: " in block
    line = next(x for x in block.splitlines() if x.startswith("- tcdmsif90: "))
    assert line.endswith("(주의: 사용률 정본 아님 — 관측 DB)")
    assert HISTORY.findall(block) == []


def test_rules_kept_after_history_removal() -> None:
    """이력만 뺐다 — 금지·지시 문장은 남는다(a95d8f5 대조로 고른 핵심 구절)."""
    section = _load(ROOT / "config/knowledge/itam/prompt_template.yaml")["section"]
    for phrase in (
        "결과 별칭은 큰따옴표로 감싸지 않는다",
        "조건 없이도 0행이었다(쓴 칸은 미확인 — 연결 키 미확정)",
        "자산 원장 계열과 잇지 않는다",
        "`구성항목설명내용`에 OR로 묶어 LIKE 부분 일치로 직접 건다",  # plans/149 W1 — s07 순서 반전
        "`tcdmsif73`·`tcdmsif90`으로도 사용률 추이 SQL을 만들지 않는다",
    ):
        assert phrase in section, phrase
    guide = _load(ROOT / "config/db_profiles/itam.yaml")["query_guide"]
    for phrase in (
        "블록에 그 칸이 없으면 값을 지어내지 않는다 — `활성화여부`도 같다.",
        "사용률 추이를 이 이력으로 답하지 않는다.",
        "두 원장의 연결 키는 미확정이다",
    ):
        assert phrase in guide.replace("\n", " "), phrase
