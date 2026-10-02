"""문서 질의 응답 엔진 (plans/126 · W2).

`(컬렉션 목록, 질의)` 쌍을 받아 근거 기반 답변을 만든다. **라우팅은 이 패키지에 없다** —
어떤 질의를 문서로 보낼지는 `plans/125` 계약 위의 2단 조건부 처리기 `doc_query`
(`src/orchestration/doc_query.py` · `plans/127`)가 정하고 이 함수를 부른다
(호출부 추가 — 엔진 무변경).

계층: application(`arch_check.MODULE_LAYER_MAP`) — infrastructure(`clients`·`infrastructure`)와
`prompts`를 소비하고, interface(`api`)·orchestration·CLI가 이 패키지를 소비한다.
"""

from src.doc_qa.evidence import Citation, EvidenceSet, build_evidence
from src.doc_qa.service import DocAnswer, answer_from_documents

__all__ = [
    "Citation",
    "DocAnswer",
    "EvidenceSet",
    "answer_from_documents",
    "build_evidence",
]
