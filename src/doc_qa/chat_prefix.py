"""명시 채팅 접두 — T-4 시험 표면 (plans/126 §4.17 · G-17 · W4).

**라우팅이 아니다.** 라우터·플래너·의도 집합·그래프를 전혀 건드리지 않고, 질의 라우트에서
**결정적 접두 파싱**으로 엔진을 직접 부른다. 추론이 0이고 사용자가 명시 호출하므로 소스 라우팅
설계(`plans/125`)를 선점하지 않는다.

기본 off(`RAG_CHAT_PREFIX_ENABLED=false`)다. 라우팅이 편입되면 이 경로는 **제거 대상**이다 —
라우터가 의도를 판정하면 접두는 중복이고, 남겨 두면 진입이 둘이 되어 라우팅 측정을 흐린다
(계획서 §4.17 「시험 표면의 수명」).
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

#: 인정하는 접두 — 한글·영문 둘 다. 뒤에 공백이 오거나 문장 끝이어야 한다.
#: (`/문서화`, `/docs-team` 같은 말이 걸리지 않게 경계를 못 박는다)
_PREFIX_RE = re.compile(r"^/(문서|doc|docs)(?:\s+|$)", re.IGNORECASE)

#: 컬렉션 지정 — `/문서:hq_manual 질문` 또는 `/문서 hq_manual: 질문`
_SCOPED_RE = re.compile(r"^/(?:문서|doc|docs):([A-Za-z0-9_\-]+)(?:\s+|$)", re.IGNORECASE)


class PrefixCommand(NamedTuple):
    """접두가 붙은 질의의 파싱 결과."""

    query: str
    collection_ids: tuple[str, ...]      # 비어 있으면 «호출부가 기본 목록을 정한다»
    search_only: bool = False


def is_enabled(app_config: Any) -> bool:
    """접두 경로가 열려 있는지(기능 on + 접두 플래그 on 둘 다 필요)."""
    rag = getattr(app_config, "rag", None)
    return bool(getattr(rag, "enabled", False) and getattr(rag, "chat_prefix_enabled", False))


def parse(text: str) -> PrefixCommand | None:
    """접두가 있으면 `PrefixCommand`, 없으면 None(=종전 경로 그대로).

    플래그 판정은 하지 않는다 — 순수 파서다(호출부가 `is_enabled`로 먼저 막는다).
    """
    raw = (text or "").strip()
    scoped = _SCOPED_RE.match(raw)
    if scoped:
        rest = raw[scoped.end():].strip()
        return PrefixCommand(query=rest, collection_ids=(scoped.group(1),))
    if _PREFIX_RE.match(raw):
        rest = _PREFIX_RE.sub("", raw, count=1).strip()
        return PrefixCommand(query=rest, collection_ids=())
    return None


def usage_hint(collection_ids: tuple[str, ...] | list[str]) -> str:
    """질의가 비었을 때의 안내 — 등록 문서군을 함께 보여준다."""
    names = ", ".join(collection_ids) or "(등록된 문서군 없음)"
    return (
        "문서 검색은 `/문서 <질문>` 형태로 씁니다.\n"
        f"문서군을 지정하려면 `/문서:<문서군> <질문>` — 사용 가능: {names}\n"
        "예) `/문서 계정 신청은 누가 승인하나요?`"
    )
