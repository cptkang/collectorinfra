"""근거 후처리·인용 조립 (plans/126 §4.8·§4.11 · W2).

여기서 하는 축소는 **문자 예산 하나**다. 점수 임계·건수 절단은 하지 않는다 — 플랫폼이 이미
자기 threshold·rerank·top_k 로 걸러 «쓸 수 있다»고 판정해 보낸 결과이고(plans/126 §3.3 ①),
우리가 척도를 모르는 숫자로 그 판정을 뒤집으면 ①의미를 모르는 필터가 되고 ②플랫폼이 임계를
낮출 때 우리 값이 조용히 진짜 필터로 바뀐다.

인용은 **코드가 결정적으로** 붙인다 — 프롬프트에 "각주를 달아라"를 지시하면 누락된다
(`output_generator` 의 같은 규율). 유사도는 **사용자에게 표기하지 않는다**(척도가 플랫폼
소유라 `0.52`가 "반만 맞음"으로 오독된다) — 순서가 상대 강도를 전달하고, 점수는 LLM 근거
메타와 진단 로그에만 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from src.clients.fabrix_retrieval import DocHit
from src.prompts.doc_answer import DOC_CLOSE, DOC_OPEN

#: 본문 절단 표시 — 사용자 각주와 LLM 근거 양쪽에 같은 표시를 쓴다.
TRUNCATION_MARK = " …(이하 생략)"


@dataclass(frozen=True)
class Citation:
    """사용자에게 보여줄 출처 1건. 점수는 담지 않는다(§4.8)."""

    collection_title: str
    title: str
    subtitle: str
    url: str
    doc_id: str
    truncated: bool

    def render(self, index: int) -> str:
        head = f"{index}. {self.collection_title} — {self.title}"
        if self.subtitle:
            head += f" `{self.subtitle}`"
        if self.url:
            head += f" ({self.url})"
        return head


@dataclass
class EvidenceSet:
    """예산 적용 후의 근거 묶음 + 무엇이 잘렸는지."""

    hits: list[DocHit]
    citations: list[Citation]
    truncated_docs: int = 0      # 본문이 잘린 문서 수
    dropped_docs: int = 0        # 총 예산 초과로 제외된 문서 수

    @property
    def is_empty(self) -> bool:
        return not self.hits


def _doc_url(url: str, base: str) -> str:
    """상대 경로에 베이스를 붙인다. 베이스가 없으면 링크를 만들지 않는다(§4.8)."""
    if not url or not base:
        return ""
    return f"{base.rstrip('/')}/{url.lstrip('/')}"


def sort_hits(hits: Iterable[DocHit]) -> list[DocHit]:
    """전역 정렬 — 리랭커가 BGE-M3 하나라 컬렉션이 달라도 척도가 같다(§3.1a).

    서로 다른 리랭커였다면 전역 정렬이 성립하지 않아 컬렉션별로 나눠 제시해야 했다.
    """
    return sorted(hits, key=lambda h: (-h.rank_score, h.rank, h.collection_id))


def build_evidence(
    hits: Sequence[DocHit],
    *,
    collection_titles: dict[str, str],
    max_doc_chars: int,
    max_context_chars: int,
    doc_url_base: str = "",
) -> EvidenceSet:
    """정렬 → 문서별 본문 상한 → 총 예산으로 묶는다. **점수로 버리지 않는다.**

    총 예산 초과 시 제외 순서는 `rank_score` 낮은 순이다 — 품질 판정이 아니라 **예산 처리**이며
    제외 사실은 각주로 고지한다(침묵 절단 금지).
    """
    ordered = sort_hits(hits)
    kept: list[DocHit] = []
    citations: list[Citation] = []
    truncated = 0
    dropped = 0
    used = 0

    for hit in ordered:
        body = hit.content or ""
        cut = False
        if max_doc_chars > 0 and len(body) > max_doc_chars:
            body = body[:max_doc_chars] + TRUNCATION_MARK
            cut = True
        if max_context_chars > 0 and used + len(body) > max_context_chars:
            dropped += 1
            continue
        used += len(body)
        kept.append(hit._replace(content=body))
        if cut:
            truncated += 1
        citations.append(Citation(
            collection_title=collection_titles.get(hit.collection_id, hit.collection_id),
            title=hit.display_title,
            subtitle=hit.subtitle,
            url=_doc_url(hit.url, doc_url_base),
            doc_id=hit.doc_id,
            truncated=cut,
        ))

    return EvidenceSet(hits=kept, citations=citations,
                       truncated_docs=truncated, dropped_docs=dropped)


def render_evidence_block(evidence: EvidenceSet, *, collection_titles: dict[str, str]) -> str:
    """LLM 에 넘길 근거 구획. **점수·순위를 메타로 함께 준다**(§4.7a 7).

    하한이 0.3 이라 약한 근거가 섞여 오므로, 점수를 숨기면 LLM 이 3건을 동등한 사실로
    취급한다. 필터가 아니라 **판단 재료**다 — 낮은 점수를 이유로 코드가 버리지는 않는다.
    """
    parts: list[str] = []
    for i, hit in enumerate(evidence.hits, start=1):
        title = collection_titles.get(hit.collection_id, hit.collection_id)
        header = (
            f"{DOC_OPEN.format(n=i)}\n"
            f"[문서군] {title}\n"
            f"[제목] {hit.display_title}\n"
        )
        if hit.subtitle:
            header += f"[위치] {hit.subtitle}\n"
        header += f"[유사도] {hit.rank_score:.2f} (순위 {i})\n"
        parts.append(f"{header}\n{hit.content}\n{DOC_CLOSE.format(n=i)}")
    return "\n\n".join(parts)


def render_citations(
    evidence: EvidenceSet, *, note_lines: Sequence[str] = ()
) -> str:
    """답변 뒤에 붙일 출처 블록(결정적 조립). 근거가 없으면 빈 문자열."""
    if evidence.is_empty:
        return ""
    lines = ["---", "**참고 문서**"]
    lines += [c.render(i) for i, c in enumerate(evidence.citations, start=1)]
    notes = list(note_lines)
    if evidence.truncated_docs:
        notes.append(
            f"문서 본문이 길어 {evidence.truncated_docs}건은 일부만 근거로 사용했습니다."
        )
    if evidence.dropped_docs:
        notes.append(
            f"근거 분량 한도로 유사도가 낮은 {evidence.dropped_docs}건은 제외했습니다."
        )
    notes.append("문서 내용은 문서 작성 시점 기준이며, 최신본 여부는 문서 관리 주체 기준입니다.")
    lines += [f"> {n}" for n in notes]
    return "\n".join(lines)


def attach_citations(answer: str, evidence: EvidenceSet, *, note_lines: Sequence[str] = ()) -> str:
    """서술 + 결정적 출처 블록. LLM 이 각주를 빠뜨려도 여기서 항상 붙는다."""
    block = render_citations(evidence, note_lines=note_lines)
    if not block:
        return answer.strip()
    return f"{answer.strip()}\n\n{block}"
