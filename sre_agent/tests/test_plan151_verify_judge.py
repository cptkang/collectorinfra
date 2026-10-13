"""plans/151 W3 채점기 독립 검증 — HALLU 추출의 거짓 양성/음성 표본 (verifier).

통과 테스트는 의도된 허용(반올림·단위 환산·시각 제외)을 고정한다. `xfail(strict=True)`는 검증에서
찾은 결함을 재현한다. 실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from investigation_bench import judge

# 세계 응답 모양 그대로(D07 polestar_incident_alarms) — 시각 문자열이 섞여 있다.
_REPLY = {"rows": [{"alarm_time": "2026-10-12T09:52:00", "alarm_name": "DB 세션 락 대기 증가",
                    "value": "lock wait 312건"}], "row_count": 1}


def _numbers(*nodes: object) -> list[judge.Num]:
    out: list[judge.Num] = []
    for n in nodes:
        judge.collect_numbers(n, out)
    return out


def _supported(text: str, numbers: list[judge.Num], hosts: tuple[str, ...] = ()) -> list[bool]:
    return [judge.claim_supported(c, numbers) for c in judge.extract_claims([text], hosts)]


@pytest.mark.parametrize("text", [
    "락 대기 312건 증가",          # 응답 문자열 속 수치
    "CPU 98% (응답 98.4)",         # 반올림
    "메모리 2GB 사용",             # 2048 ↔ 2GB(1024 기수)
    "응답 50ms",                   # 0.05(초) ↔ 50ms
])
def test_supported_claims(text: str) -> None:
    nums = _numbers(_REPLY, {"v": 98.4, "mem_mb": 2048, "await_s": 0.05})
    assert _supported(text, nums) == [True]


@pytest.mark.parametrize("text", ["디스크 사용률 91%", "락 대기 977건", "응답 4812ms"])
def test_unsupported_claims_flagged(text: str) -> None:
    assert _supported(text, _numbers(_REPLY)) == [False]


@pytest.mark.parametrize("text", ["응답 812ms", "캐시 700MB", "힙 900KB"])
def test_unit_conversion_tolerance_does_not_accept_any_small_value(text: str) -> None:
    assert _supported(text, _numbers(_REPLY)) == [False]


@pytest.mark.parametrize("text", [
    "2026-10-12 09:52:00 기준", "10월 12일 9시 52분", "bw-d07-db01 상태", "v1.2.3 배포",
    "20261012101500 알람",
])
def test_time_host_version_not_claims(text: str) -> None:
    assert judge.extract_claims([text], ("bw-d07-db01",)) == []


@pytest.mark.parametrize("text", ["락 대기 12건 증가", "CPU 52% 도달"])
def test_reply_timestamp_digits_do_not_support_claims(text: str) -> None:
    assert _supported(text, _numbers(_REPLY)) == [False]


def test_claim_supported_rounding_boundary() -> None:
    nums = [judge.Num(Decimal("98.4"), 1)]
    c_ok = judge.extract_claims(["98.4%"])[0]
    c_bad = judge.extract_claims(["98.5%"])[0]
    assert judge.claim_supported(c_ok, nums) and not judge.claim_supported(c_bad, nums)
