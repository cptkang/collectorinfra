"""라우팅 프롬프트 렌더 **바이트 동일** 고정 (WU-D2 / `plans/79` 트랙 B · SPEC T1).

## 왜 이 테스트가 트랙 B에서 가장 먼저인가

트랙 B는 프롬프트를 1단(intent)·2단(DB)으로 나눈다. 나누면서 텍스트를 복사하면
**D-053(사본 금지)** 위반이고, 프롬프트와 코드가 조용히 어긋난다. 그래서 절을 **명명 상수로
추출**해 기존 템플릿과 신규 2단 템플릿이 **같은 상수를 조립**하게 한다.

문제는 추출 과정에서 **공백·개행 하나만 달라져도** 기존 단일 호출 경로의 프롬프트가 바뀐다는
것이다. 그러면 **S-1(골든셋 회귀 · `plans/80` WU-05) 기준선이 조용히 오염된다** — 트랙 A 효과를
재려는 측정이 트랙 B 부작용과 섞인다.

골든 파일은 **추출 이전**(2026-08-27)에 포착했다. 이 테스트가 깨지면 추출이 렌더를 바꾼 것이므로
되돌린다 — "보기에 같다"가 아니라 바이트로 판정한다.

## 골든 갱신이 정당한 유일한 경우

프롬프트 **문구를 의도적으로 바꾸는 작업**(트랙 A 후속·A-6 등)일 때뿐이며, 그때는
`plans/80` WU-05 이후여야 한다. 리팩터링은 절대 골든을 갱신하지 않는다.
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

from src.routing.domain_config import DB_DOMAINS

GOLDEN_DIR = pathlib.Path(__file__).parent / "goldens"

# `from src.routing import semantic_router`는 **함수**를 돌려준다 — 패키지 `__init__`가
# 동명 함수를 re-export해 모듈을 가린다. 모듈 객체가 필요하므로 importlib로 가져온다.
router = importlib.import_module("src.routing.semantic_router")


def _domains():
    return list(DB_DOMAINS.values()) if isinstance(DB_DOMAINS, dict) else list(DB_DOMAINS)


@pytest.mark.parametrize(
    "fault_enabled,golden",
    [(False, "router_prompt_fd_off.txt"), (True, "router_prompt_fd_on.txt")],
)
def test_render_is_byte_identical_to_golden(fault_enabled, golden):
    """★ 렌더 결과가 추출 이전과 **바이트 동일**하다.

    `fault_diagnosis_enabled` 두 상태를 모두 본다 — 옵트인 클래스는 프롬프트 **두 자리**
    (클래스 정의 줄 · 절 본문)에 조건부로 주입되므로(계약 C-A), 한 상태만 보면 나머지가 샌다.
    """
    rendered = router._build_router_prompt(
        _domains(), db_descriptions=None, fault_diagnosis_enabled=fault_enabled
    )
    expected = (GOLDEN_DIR / golden).read_text()
    assert rendered == expected, (
        f"프롬프트 렌더가 골든과 다르다({golden}). 절 추출이 텍스트를 바꿨다면 되돌릴 것 — "
        "S-1 기준선이 오염된다. 문구를 의도적으로 바꾼 작업이라면 WU-05 이후에 골든을 갱신한다."
    )


def test_optin_class_appears_only_when_enabled():
    """옵트인 클래스가 off 상태 렌더에 **새지 않는다**(계약 C-A).

    정의만 남아도 LLM이 그 클래스를 알게 되는데, 그때 그래프에는 해당 노드가 없다.
    """
    off = (GOLDEN_DIR / "router_prompt_fd_off.txt").read_text()
    on = (GOLDEN_DIR / "router_prompt_fd_on.txt").read_text()
    assert "fault_diagnosis" not in off
    assert "fault_diagnosis" in on
    assert len(on) > len(off)


def test_db_descriptions_are_additive_only():
    """Redis 캐시 설명 주입이 **기존 본문을 바꾸지 않는다**(덧붙이기만 한다).

    설명이 본문을 치환하면 캐시 상태에 따라 프롬프트가 흔들려 측정이 재현되지 않는다.
    """
    base = router._build_router_prompt(_domains(), db_descriptions=None)
    domains = _domains()
    with_desc = router._build_router_prompt(
        domains, db_descriptions={domains[0].db_id: "테스트 상세 설명"}
    )
    assert "테스트 상세 설명" in with_desc
    assert len(with_desc) > len(base)
    # 본문 말미(판단 규칙 이후)는 그대로여야 한다 — db_list만 늘어난다.
    tail = base.split("## 출력 형식", 1)[1]
    assert tail == with_desc.split("## 출력 형식", 1)[1]


def test_goldens_exist_and_are_not_empty():
    """골든이 사라지면 이 테스트가 조용히 통과하는 것을 막는다."""
    for name in ("router_prompt_fd_off.txt", "router_prompt_fd_on.txt"):
        p = GOLDEN_DIR / name
        assert p.exists(), f"골든 누락: {name}"
        assert len(p.read_text()) > 5000
