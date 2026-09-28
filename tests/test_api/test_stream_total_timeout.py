"""SSE 전체 경과 상한 (CU-11 · P-16 · 2026-09-16 → plans/119 T-5 · D-267 ⑦ 개정).

스트리밍 경로에는 `idle_timeout`이 있었지만 **무이벤트 구간**만 끊는다. 진행 이벤트와
하트비트가 계속 나오면 영영 걸리지 않아, 실측에서 B-06 455초 · K-10 251초가 상한을
넘겨 계속 돌았다(hang 7건 중 2건). 전체 경과로 끊는 판정을 고정한다.

plans/119 T-5 로 종전 `_exceeded_total_timeout`(요청 시작 기준 단일 상한)을 `StreamCaps` 가
대체했다 — 첫 답변 전에는 처리 상한, 첫 답변 뒤에는 처리 상한 + 전달 연장이 전체 상한이다.
hang 방지(이 파일의 원래 계약)는 두 구간 모두에서 유지된다.
"""

from __future__ import annotations

from src.api.stream_failure import StreamCaps


def _caps(limit: float, *, idle: float = 30.0, grace: float = 60.0) -> StreamCaps:
    return StreamCaps(limit_sec=limit, idle_sec=idle, grace_sec=grace, start=1000.0)


def test_상한을_넘기면_참이다() -> None:
    assert _caps(60.0).exceeded(now=1000.0 + 120.0) == "processing"


def test_상한_안이면_거짓이다() -> None:
    assert _caps(60.0).exceeded(now=1000.0 + 5.0) is None


def test_이벤트가_계속_나와도_전체_경과로_끊는다() -> None:
    """idle_timeout 은 무이벤트만 본다 — 455초 실행이 살아남은 경로."""
    before_answer = _caps(60.0)
    assert before_answer.exceeded(now=1000.0 + 455.0) == "processing"

    # 첫 답변 뒤에도 토큰이 계속 흘러 idle 이 안 걸리는 경우 — 전체 상한(60 + 60)으로 끊는다
    streaming = _caps(60.0)
    for t in range(50, 455, 5):
        streaming.mark_answer(now=1000.0 + t)
        if streaming.exceeded(now=1000.0 + t) is not None:
            break
    assert streaming.exceeded(now=1000.0 + 455.0) == "hard_cap"


def test_상한이_0_이하면_끄기로_본다() -> None:
    """설정으로 비활성화할 수 있어야 한다 — 기존 동작 보존."""
    assert _caps(0.0).exceeded(now=1000.0 + 999.0) is None
