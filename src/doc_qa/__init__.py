"""문서 질의 응답 엔진 (plans/126 · W2).

`(컬렉션 목록, 질의)` 쌍을 받아 근거 기반 답변을 만든다. **라우팅은 이 패키지에 없다** —
어떤 질의를 문서로 보낼지 판단하는 일은 `plans/125`(4소스 의도 라우팅)가 소유하며, 그 결론이
나오면 그래프 노드가 이 함수를 호출하는 얇은 래퍼로 편입된다(계획서 부록 D).

계층: application(`arch_check.MODULE_LAYER_MAP`) — infrastructure(`clients`·`infrastructure`)와
`prompts`를 소비하고, interface(`api`)·CLI가 이 패키지를 소비한다.
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
