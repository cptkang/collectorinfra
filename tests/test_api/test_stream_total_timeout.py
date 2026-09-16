"""SSE 전체 경과 상한 (CU-11 · P-16 · 2026-09-16).

스트리밍 경로에는 `idle_timeout`이 있었지만 **무이벤트 구간**만 끊는다. 진행 이벤트와
하트비트가 계속 나오면 영영 걸리지 않아, 실측에서 B-06 455초 · K-10 251초가 상한을
넘겨 계속 돌았다(hang 7건 중 2건). 전체 경과로 끊는 판정을 고정한다.
"""

from __future__ import annotations

import time

from src.api.routes.query import _exceeded_total_timeout


def test_상한을_넘기면_참이다() -> None:
    assert _exceeded_total_timeout(time.time() - 120.0, 60.0) is True


def test_상한_안이면_거짓이다() -> None:
    assert _exceeded_total_timeout(time.time() - 5.0, 60.0) is False


def test_이벤트가_계속_나와도_전체_경과로_끊는다() -> None:
    """idle_timeout 은 무이벤트만 본다 — 455초 실행이 살아남은 경로."""
    started = time.time() - 455.0

    assert _exceeded_total_timeout(started, 60.0) is True


def test_상한이_0_이하면_끄기로_본다() -> None:
    """설정으로 비활성화할 수 있어야 한다 — 기존 동작 보존."""
    assert _exceeded_total_timeout(time.time() - 999.0, 0.0) is False
