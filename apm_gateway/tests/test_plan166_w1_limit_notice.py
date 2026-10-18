"""plans/166 W1 M3 — 조회 실패 `[한계]` 고지 헬퍼 `limit_notice`의 규칙.

사본 28곳(tools 22 · scope_tools 5 · manage_tools 1)이 쓰던 「계약 위반은 재발생 · 그 밖은 고지」
규칙의 단일 출처다. 한 사본이 재발생을 빠뜨리면 게이트웨이 버그(`contract_violation`)가 한계
고지로 강등되므로, 재발생과 고지 문구 조립을 여기서 고정한다.
"""

from __future__ import annotations

import pytest
from apm_gateway.application.tools import limit_notice
from apm_gateway.domain.errors import (
    API_ERROR,
    CONTRACT_VIOLATION,
    ERROR_CODES,
    SOURCE_UNAVAILABLE,
    ApmError,
)


def test_contract_violation_reraises_same_object() -> None:
    err = ApmError(CONTRACT_VIOLATION, "요청 계약 위반")
    with pytest.raises(ApmError) as info:
        limit_notice(err, "실시간", "(도메인 1)")
    assert info.value is err


@pytest.mark.parametrize("code", sorted(ERROR_CODES - {CONTRACT_VIOLATION}))
def test_other_codes_return_notice(code: str) -> None:
    assert limit_notice(ApmError(code, "사유"), "실시간", "(도메인 1)") == (
        f"[한계] 실시간 조회 실패(도메인 1): {code}"
    )


def test_notice_shapes() -> None:
    err = ApmError(SOURCE_UNAVAILABLE, "사유")
    # 위치 없음
    assert limit_notice(err, "SQL") == "[한계] SQL 조회 실패: source_unavailable"
    # 꼬리
    assert limit_notice(err, "지표 카탈로그", "", " — 그대로 조회했다") == (
        "[한계] 지표 카탈로그 조회 실패: source_unavailable — 그대로 조회했다"
    )
    # detail은 오류 코드 자리를 대신한다(manage_tools `_why`)
    assert limit_notice(ApmError(API_ERROR, "r"), "이벤트 규칙", "(소스 a)", detail="x — y") == (
        "[한계] 이벤트 규칙 조회 실패(소스 a): x — y"
    )


def test_contract_violation_reraises_even_with_detail() -> None:
    err = ApmError(CONTRACT_VIOLATION, "r")
    with pytest.raises(ApmError):
        limit_notice(err, "x", detail="d")
