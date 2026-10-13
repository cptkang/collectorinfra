"""사용법·개념 의문과 데이터 요청 표지 판정 (plans/142 W2 · G-5 (b)).

질문이 «기능을 어떻게 쓰나·무엇인가»를 묻는지, «데이터를 달라»는지를 표면 표지로만 가린다.
포탈 도움말 되돌림 판정(`src/doc_qa/portal_help.portal_redirect_match`)은 넓은 판정
(`is_method_or_concept_question`·`has_data_request_cue`)을, `field_mapper` 등록 오인 가드
(W4 — 「등록」이 있어도 방법 의문이면 등록 신호를 버린다)는 좁은 판정(`is_method_question`)을 쓴다.

순수 규칙 기반이며 LLM 의존성이 없다. 표지는 **공백을 뺀** 질의에서 부분 문자열로 찾는다.
계층: utils (config/utils)
"""

from __future__ import annotations

import re

#: 설명·방법 의문 표지.
METHOD_OR_CONCEPT_CUES: tuple[str, ...] = (
    "뭐", "무엇", "무슨", "의미", "뜻", "설명", "어떻게", "방법", "하려면", "사용법",
)
#: 방법 의문 표지 — 등록 오인 가드(W4) 전용의 좁은 집합. 「뜻」「의미」「설명」처럼 실제 등록
#: 명령문(예: 「… 유사 단어를 추가해줘, 뜻은 서버 이름」)에도 들어가는 표지는 넣지 않는다.
METHOD_QUESTION_CUES: tuple[str, ...] = ("어떻게", "방법", "하려면", "사용법", "절차")
#: 데이터 요청 표지 — 하나라도 있으면 사용법 질문으로 보지 않는다. 값 질문(「억제율이 어떻게 돼」
#: ·「얼마」)과 시점 표지(「지금」·「현재」)도 데이터 요청으로 본다(plans/142 V-4 — 「어떻게」가
#: 방법 의문으로 읽히지 않게).
DATA_REQUEST_CUES: tuple[str, ...] = (
    "몇건", "건수", "개수", "목록", "상위", "최근", "오늘", "어제", "이번주", "조회해", "보여줘",
    "어떻게돼", "어떻게되", "얼마", "지금", "현재",
)

#: 명령형 요청 어미 — 「하려면」 단독 표지는 문장이 이 어미로 끝나면 방법 의문으로 보지 않는다
#: (「CPU를 조회하려면 vcore도 인식하도록 유사어 등록해줘」는 등록 명령 — plans/142 V-7).
IMPERATIVE_ENDINGS: tuple[str, ...] = (
    "해줘", "해주세요", "해주십시오", "해주시오", "하세요", "하십시오", "하시오", "해라",
)
#: 「하려면」 — 조건 연결어미라 명령문에도 들어간다(`IMPERATIVE_ENDINGS` 참조).
_CONDITIONAL_CUE = "하려면"

_SPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT = ".!?~。…"


def compact_text(text: str) -> str:
    """공백을 모두 빼고 소문자화한다 — 표지·용어 포함 판정의 공통 정규화."""
    return _SPACE_RE.sub("", text or "").lower()


def is_method_or_concept_question(text: str) -> bool:
    """설명·방법 의문 표지(`METHOD_OR_CONCEPT_CUES`)가 하나라도 있으면 True."""
    compact = compact_text(text)
    return any(cue in compact for cue in METHOD_OR_CONCEPT_CUES)


def is_method_question(text: str) -> bool:
    """방법 의문 표지(`METHOD_QUESTION_CUES`)가 하나라도 있으면 True.

    `is_method_or_concept_question`보다 좁다 — 개념 의문(뜻·의미·설명·무엇)은 보지 않는다.
    「하려면」만 있고 문장이 명령형 요청(`IMPERATIVE_ENDINGS`)으로 끝나면 False 다.
    """
    compact = compact_text(text)
    cues = [cue for cue in METHOD_QUESTION_CUES if cue in compact]
    if cues == [_CONDITIONAL_CUE] and compact.rstrip(_TRAILING_PUNCT).endswith(IMPERATIVE_ENDINGS):
        return False
    return bool(cues)


def has_data_request_cue(text: str) -> bool:
    """데이터 요청 표지(`DATA_REQUEST_CUES`)가 하나라도 있으면 True."""
    compact = compact_text(text)
    return any(cue in compact for cue in DATA_REQUEST_CUES)


__all__ = [
    "DATA_REQUEST_CUES",
    "IMPERATIVE_ENDINGS",
    "METHOD_OR_CONCEPT_CUES",
    "METHOD_QUESTION_CUES",
    "compact_text",
    "has_data_request_cue",
    "is_method_or_concept_question",
    "is_method_question",
]
