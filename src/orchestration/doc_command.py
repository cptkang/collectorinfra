"""문서 검색 명령 판정 — `plans/138` W1·W2 · D-307.

「<문서 소스 이름>에서 … 검색해줘」처럼 사용자가 **등록된 문서 소스 이름으로 지목하고 검색을
명령한** 문장을 결정적으로 알아본다. 계층 A 단락(`intent_planner` ②.9)이 이 판정으로 분해 LLM 없이
`doc_query` 단일 task를 만든다.

- **판정 재료는 둘뿐**(D-004 부기 · D-281 ⑦ · D-307 ①): 레지스트리 문서 소스 별칭(정본
  `solutions[doc].aliases`)과 명령 형태(지목 조사 + 검색 동사). **질문 내용어로 소스를 정하지 않는다.**
- **문서군 고정**(D-307 ③): 지목 구간 안에서만 문서군 `title`·`surface_terms`(정본
  `config/rag_collections.yaml`)를 공백 무시로 대조한다. 문장 중간의 일반어는 보지 않는다.
- **다른 소스 지목이 함께 있으면 판정하지 않는다**(D-307 ⑤ — 복합 질의는 종전 분해).
- 검색 질의는 지목 구간과 명령 꼬리만 뗀 원문이다(용어·조항 번호 보존 — plans/126 §4.5 규칙 1).

계층: orchestration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from src.infrastructure.doc_sources import DocCollection
from src.orchestration.doc_query import DOC_SYSTEM, doc_active, doc_views, view_id
from src.routing.registry import get_registry
from src.utils.query_gen_common import term_in_text

logger = logging.getLogger(__name__)

#: 계획 경로 표지(`intent_planner` 계획 요약·로그).
DOC_COMMAND_PATH = "doc_command"
#: 지목 조사 — 긴 것부터 대조한다(「에서는」이 「에서」보다 먼저).
_DESIGNATOR_PARTICLES = ("에서는", "에서", "으로", "로")
#: 지목 구간 길이 상한(조사가 붙은 어절 포함) — 「아키텍처 설계문서에서」 같은 여러 어절 이름.
_DESIGNATOR_MAX_WORDS = 3
#: 검색 명령 동사 어간(양식 기억 명령 선례 — 명사 + 동사).
_COMMAND_VERBS = ("검색", "찾아", "찾기", "찾아봐", "조회", "알려")
#: 명령 꼬리에서 함께 떼는 어절(동사 뒤에 띄어 쓴 보조 표현).
_COMMAND_FILLERS = frozenset({
    "줘", "줘요", "주세요", "주십시오", "주시오", "봐", "봐줘", "봐주세요", "해", "해줘", "해주세요",
    "하시오", "하세요", "해봐", "좀",
})
_TRAILING_PUNCT = " \t\n.!?。"


@dataclass(frozen=True)
class DocCommand:
    """문서 검색 명령 판정 결과(판정되면 `intent_planner`가 계획을 만든다)."""

    #: 사용자가 쓴 지목 표현(안내 문구용 — 사용자 표현만 싣는다 · D-264)
    hint: str
    #: 문서 라우팅 활성 여부(비활성이면 조회하지 않고 안내만 — D-293 G-1)
    active: bool
    #: 고정할 보기(`doc.<문서군 id>`) — 비면 처리기가 전부 찾고 고지한다(plans/127 G-6 (a))
    views: list[str] = field(default_factory=list)
    #: 검색 질의(지목 구간·명령 꼬리 제거)
    query: str = ""


def _strip_particle(word: str) -> tuple[str, str] | None:
    """어절 끝의 지목 조사를 떼어 `(어간, 조사)`를 돌려준다(조사가 없거나 어간이 비면 None)."""
    for particle in _DESIGNATOR_PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[: -len(particle)], particle
    return None


def _doc_aliases() -> tuple[str, ...]:
    """문서 소스 별칭(정본 레지스트리 · 시스템 코드는 제외 — 사용자가 부르는 이름만)."""
    for spec in get_registry().non_db_systems():
        if spec.code == DOC_SYSTEM:
            return tuple(a for a in spec.aliases if a)
    return ()


def _other_source_terms(doc_aliases: Sequence[str]) -> tuple[str, ...]:
    """문서 외 소스를 지목하는 등록 표면어(위치·환경·제품·DB·비DB 시스템 별칭)."""
    excluded = set(doc_aliases)
    return tuple(t for t in get_registry().new_db_signal_terms() if t and t not in excluded)


def _find_alias(text: str, aliases: Sequence[str]) -> str:
    """텍스트에 든 별칭 중 가장 긴 것(없으면 빈 문자열)."""
    found = [a for a in aliases if term_in_text(a, text)]
    return max(found, key=len) if found else ""


@dataclass(frozen=True)
class _Designator:
    start: int  # 지목 구간 첫 어절 위치
    end: int    # 조사가 붙은 어절 위치(포함)
    text: str   # 조사를 뗀 지목 구간
    alias: str  # 그 안의 문서 별칭(문서 지목이 아니면 빈 문자열)


def _designators(words: list[str], aliases: Sequence[str]) -> list[_Designator]:
    """조사가 붙은 어절마다 지목 구간 후보를 만든다.

    문서 별칭이 들어 있으면 **가장 긴 별칭을 담는 가장 짧은 구간**(「아키텍처 설계문서에서」는
    「설계문서」가 아니라 두 어절 전체 · 앞쪽 「RAG의」처럼 별칭을 담은 소유격 어절은 함께 묶는다),
    없으면 조사가 붙은 어절 하나다(다른 소스 지목 판정용).
    """
    out: list[_Designator] = []
    for i, word in enumerate(words):
        parsed = _strip_particle(word)
        if parsed is None:
            continue
        stem, _particle = parsed
        chosen: _Designator | None = None
        for start in range(i, max(-1, i - _DESIGNATOR_MAX_WORDS), -1):
            text = " ".join([*words[start:i], stem])
            alias = _find_alias(text, aliases)
            if alias and (chosen is None or len(alias) > len(chosen.alias)):
                chosen = _Designator(start, i, text, alias)
        if chosen is not None:
            # 바로 앞 어절이 소유격으로 이어지는 문서 이름이면(「RAG의 본부 매뉴얼에서」) 함께 뗀다
            prev = chosen.start - 1
            if prev >= 0 and words[prev].endswith("의") and _find_alias(words[prev][:-1], aliases):
                chosen = _Designator(prev, i, " ".join([words[prev][:-1], chosen.text]), chosen.alias)
            out.append(chosen)
        else:
            out.append(_Designator(i, i, stem, ""))
    return out


def _compact(text: str) -> str:
    return "".join(text.split())


def match_collections(text: str, collections: Sequence[DocCollection]) -> list[str]:
    """지목 구간에 이름(`title`·`surface_terms`)이 든 문서군 id(선언 순서 · 공백 무시)."""
    target = _compact(text)
    picked: list[str] = []
    for col in collections:
        names = [_compact(n) for n in (col.title, *col.surface_terms) if n and n.strip()]
        if any(name and name in target for name in names):
            picked.append(col.id)
    return picked


def _is_command_word(word: str) -> bool:
    return any(word.startswith(v) for v in _COMMAND_VERBS)


def _strip_command_tail(words: list[str]) -> list[str]:
    """문장 끝 명령 꼬리(「을/를 검색해줘」·「찾아 줘」·「검색하시오」)를 뗀다."""
    kept = list(words)
    while kept and kept[-1].strip(_TRAILING_PUNCT) in _COMMAND_FILLERS:
        kept.pop()
    if not kept or not _is_command_word(kept[-1].strip(_TRAILING_PUNCT)):
        return list(words)
    kept.pop()
    while kept and kept[-1].strip(_TRAILING_PUNCT) in _COMMAND_FILLERS:
        kept.pop()
    if kept:
        last = kept[-1].rstrip(_TRAILING_PUNCT)
        for particle in ("을", "를"):
            if last.endswith(particle) and len(last) > 1:
                last = last[:-1]
                break
        kept[-1] = last
    return kept


def parse_doc_command(user_query: str, app_config: Any) -> DocCommand | None:
    """원문이 문서 검색 명령이면 판정 결과, 아니면 None(종전 분해로 간다).

    판정(전부 결정적 · LLM 0):
        1. 조사(에서는·에서·으로·로)가 붙은 지목 구간에 문서 소스 별칭이 있다
        2. 그 지목 구간 **뒤**에 검색 동사(검색·찾아·찾기·조회·알려)가 있다
        3. 다른 지목 구간에 문서 외 소스 표면어가 없다(복합 질의 제외 — D-307 ⑤)
        4. 지목 구간과 명령 꼬리를 뗀 질의가 비지 않는다
    """
    text = (user_query or "").strip()
    if not text:
        return None
    aliases = _doc_aliases()
    if not aliases:
        return None
    words = text.split()
    designators = _designators(words, aliases)
    doc_hits = [d for d in designators if d.alias]
    if not doc_hits:
        return None
    hit = doc_hits[0]
    tail = " ".join(words[hit.end + 1:])
    if not any(v in tail for v in _COMMAND_VERBS):
        return None
    others = _other_source_terms(aliases)
    for d in designators:
        if d.alias or d.start <= hit.end and d.end >= hit.start:
            continue
        if any(term_in_text(t, d.text) for t in others):
            logger.info("문서 검색 명령 보류 — 다른 소스 지목 동반(복합 질의 · D-307 ⑤): %s", d.text)
            return None
    remaining = words[:hit.start] + words[hit.end + 1:]
    query = " ".join(_strip_command_tail(remaining)).strip(_TRAILING_PUNCT)
    if not query:
        return None

    active = doc_active(app_config)
    views: list[str] = []
    if active:
        views = [view_id(cid) for cid in match_collections(hit.text, doc_views(app_config))]
    return DocCommand(hint=hit.alias, active=active, views=views, query=query)


__all__ = ["DOC_COMMAND_PATH", "DocCommand", "match_collections", "parse_doc_command"]
