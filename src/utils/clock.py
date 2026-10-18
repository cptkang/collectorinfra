"""현재 시각 문자열 — 기록·작업 메타의 시각 표기 정본 1곳 (plans/166 X4).

관리자 작업 레지스트리·구조 저장소·DB 구조 서비스·APM 작업 레지스트리가 같은 표기
(로컬 시간대 오프셋 포함 ISO 8601, 초 단위)를 각자 정의하던 사본 4벌을 모은다.

계층: utils.
"""

from __future__ import annotations

from datetime import datetime


def now_iso() -> str:
    """현재 시각(로컬 시간대 오프셋 포함 ISO 8601, 초 단위)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = ["now_iso"]
