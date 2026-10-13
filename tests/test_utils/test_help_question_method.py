"""`is_method_question` 단위 테스트 (plans/142 W4 — 등록 오인 가드 전용의 좁은 방법 의문 판정)."""

from __future__ import annotations

import pytest

from src.utils.help_question import is_method_or_concept_question, is_method_question


@pytest.mark.parametrize(
    "text",
    [
        "itam db에 유사어를 등록하려고 한다. 어떻게 해야 되냐?",
        "유사어 등록은 어떻게 해?",
        "유사어 등록 방법 알려줘",
        "유사어를 등록하려면?",
        "유사어 등록 사용법",
        "유사어 등록 절차가 궁금해",
        # 「하려면」 단독이어도 명령형 요청으로 끝나지 않으면 방법 의문이다(V-7 경계)
        "유사어를 등록하려면 뭘 해야 돼?",
        "등록하려면 무엇이 필요한지 알려줘",
        # 「어떻게」·「방법」이 있으면 명령형 어미여도 방법 의문이다(종전대로)
        "유사어를 등록하려면 어떻게 하는지 설명해줘",
        "등록하려면 필요한 방법을 정리해주세요",
    ],
)
def test_method_question_detected(text: str) -> None:
    """방법 의문 표지가 있으면 True."""
    assert is_method_question(text)


@pytest.mark.parametrize(
    "text",
    [
        "hostname에 '장비이름' 유사 단어를 추가해줘",
        "hostname에 '장비이름' 유사어 등록해줘",
        "hostname에 '장비이름' 유사 단어를 추가해줘, 뜻은 서버 이름",
        "vcore, cpu, core은 동의어이다. 캐시에 등록하라.",
        "전체 등록",
        "1, 3번만 등록",
        # 「하려면」 단독 + 명령형 요청 어미 — 등록 명령이다(V-7)
        "CPU를 조회하려면 vcore도 인식하도록 유사어 등록해줘",
        "CPU를 조회하려면 vcore도 인식하도록 유사어 등록해 줘.",
        "메모리를 조회하려면 mem도 쓰도록 유사어로 등록해주세요!",
        "조회하려면 vcore를 cpu 유사어로 등록하세요",
        "",
    ],
)
def test_registration_command_not_method_question(text: str) -> None:
    """실제 등록 명령문·등록 답변은 방법 의문이 아니다."""
    assert not is_method_question(text)


def test_narrower_than_method_or_concept() -> None:
    """개념 표지(뜻·의미·설명)는 넓은 판정에만 걸리고 좁은 판정에는 걸리지 않는다."""
    text = "hostname에 '장비이름' 유사 단어를 추가해줘, 뜻은 서버 이름"
    assert is_method_or_concept_question(text)
    assert not is_method_question(text)
