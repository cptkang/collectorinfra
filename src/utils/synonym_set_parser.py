"""앵커 없는 동의어 집합 선파서 (D-142).

`"vcore, cpu, core은 동의어이다. 캐시에 등록하라."`처럼 원소가 **대등한** 집합을
정규식으로 확정한다. LLM을 거치지 않으므로 같은 입력이 항상 같은 결과를 낸다.

기존 `add-synonym`은 앵커 컬럼을 요구한다(`"hostname에 '서버명' 추가"`). 그 표현은
여기서 **매칭되지 않아야** 기존 경로가 그대로 유지된다.

파싱에 실패하면 `None`을 돌려주고 상위(`cache_management`)가 LLM 폴백으로 넘긴다.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

#: 집합 크기 범위. 1개는 동의어가 될 수 없고, 과도하게 크면 오인식일 가능성이 높다.
_MIN_WORDS = 2
_MAX_WORDS = 20

#: 원소 길이 범위.
_MIN_WORD_LEN = 1
_MAX_WORD_LEN = 64

#: 원소 허용 문자 — 영문·숫자·한글·언더스코어·하이픈. SQL 메타문자나 따옴표는
#: 컬럼명·유사어로 쓰일 일이 없고, 섞여 있으면 문장을 잘못 끊은 신호다.
_WORD = re.compile(r"^[0-9A-Za-z_\-가-힣ㄱ-ㅎㅏ-ㅣ]+$")

#: "A, B, C" + 조사 + 동의어 선언. 쉼표가 최소 1개여야 하므로 단일 단어는 걸리지 않는다.
#:
#: 조사를 **필수 캡처**로 분리하는 이유: optional로 두면 non-greedy 나열이 조사까지
#: 삼켜 `core은`이 원소가 된다. 사후에 접미사를 떼는 방식은 `은행존`처럼 조사로 끝나는
#: 정당한 단어를 잘라먹으므로 쓰지 않는다 — 경계를 정규식이 정하게 한다.
#:
#: 나열은 허용 문자로만 이뤄지게 제한한다. 임의 문자를 허용하면 앞 문장이 첫 원소로
#: 딸려온다("캐시에 등록해줘. vcore, cpu" → 첫 원소가 문장 전체). 부수 효과로 SQL
#: 메타문자가 섞인 입력은 아예 매칭되지 않아 부분 등록도 막힌다.
_SET_DECLARATION = re.compile(
    r"(?:^|(?<=[\s.!?]))"                              # 나열 시작 경계
    r"(?P<words>[\w가-힣\-]+(?:\s*,\s*[\w가-힣\-]+)+)"  # 쉼표로 이어진 나열(허용 문자만)
    r"\s*(?P<particle>은|는|이|가|와|과)"               # 조사(필수)
    r"\s*(?:서로\s*)?"                                # "서로"(생략 가능)
    r"(?:동의어|유사어|같은\s*말|동일한\s*의미)"        # 동의어 선언
)

#: 등록 의사. 선언만 있고 등록 의사가 없으면(질문·서술) 처리하지 않는다.
_REGISTER_INTENT = re.compile(r"(등록|추가|저장|캐시에\s*(넣|담))")

#: 앵커가 있는 기존 표현. 하나라도 걸리면 이 파서는 손대지 않는다
#: (기존 add-synonym / remove-synonym / update-synonym / generate 경로 보존).
_ANCHORED_FORMS = re.compile(
    r"(에\s*['\"]|의\s*유사\s*단어|에서\s*['\"]|유사\s*단어를\s*(생성|만들|보여|변경|삭제))"
)

#: 질문형 종결. "동의어인가요?"는 등록 요청이 아니다.
_QUESTION = re.compile(r"(인가요|입니까|맞나요|맞아\?|\?\s*$)")


def _split_words(raw: str) -> list[str]:
    """쉼표 구분 나열을 원소 목록으로 자른다 (순서 유지·중복 제거)."""
    seen: list[str] = []
    for part in raw.split(","):
        word = part.strip()
        if word and word not in seen:
            seen.append(word)
    return seen


def _is_valid(words: list[str]) -> bool:
    """집합이 등록 가능한 형태인지 결정적으로 검증한다."""
    if not (_MIN_WORDS <= len(words) <= _MAX_WORDS):
        return False
    return all(
        _MIN_WORD_LEN <= len(w) <= _MAX_WORD_LEN and _WORD.match(w)
        for w in words
    )


def parse_synonym_set(user_query: str) -> list[str] | None:
    """자연어에서 앵커 없는 동의어 집합을 추출한다.

    Args:
        user_query: 사용자 자연어 입력

    Returns:
        동의어 목록(입력 순서·중복 제거). 형태가 아니거나 검증에 걸리면 None.
        None은 "이 파서가 처리할 문장이 아니다"라는 뜻이며, 상위가 LLM 폴백으로 넘긴다.
    """
    if not user_query or not user_query.strip():
        return None

    text = user_query.strip()

    # 앵커가 있는 기존 표현은 건드리지 않는다 — 경로 침범 방지.
    if _ANCHORED_FORMS.search(text):
        return None

    match = _SET_DECLARATION.search(text)
    if not match:
        return None

    # 등록 의사가 없으면(단순 질문·서술) 처리하지 않는다.
    if not _REGISTER_INTENT.search(text) or _QUESTION.search(text):
        return None

    words = _split_words(match.group("words"))
    if not _is_valid(words):
        logger.debug("동의어 집합 검증 실패(선파서 미적용): %r", words[:5])
        return None

    return words
