"""알람 워커 구간 계측 (plans/208 트랙 B W0 · C-0 — 측정 전용 · 판정 무관).

`ALARM_WORKER_TIMING_ENABLED`가 켜진 기동에서만 워커가 알람 1건마다 `StageTimer`를 만든다.
구간(P1 파싱 · P2 서버명 역조회 · P3 게이트 사전 판정 · P4 그래프 · P5 후처리 · P6 ACK)이
끝날 때마다 `mark`를 불러 직전 표시 이후 경과(ms)를 남긴다. 끄면 타이머를 만들지 않아 측정 호출이
없다.

조기 반환(지문 중복·심각도 미달)이면 P3~P5 표시가 없고, 남은 P3와 ACK가 `p6`에 합산된다.
처리 예외면 dead-letter 적재가 `p6`에 합산된다.
"""

from __future__ import annotations

import time


class StageTimer:
    """알람 1건의 구간별 소요(ms)를 모은다 — 단조 시계(`perf_counter`)만 쓴다."""

    __slots__ = ("alarm_id", "_start", "_last", "_spans")

    def __init__(self) -> None:
        self.alarm_id = ""
        self._start = time.perf_counter()
        self._last = self._start
        self._spans: dict[str, float] = {}

    def mark(self, stage: str) -> None:
        """`stage` 구간을 닫는다 — 직전 표시(없으면 생성 시각) 이후 경과를 기록한다."""
        now = time.perf_counter()
        self._spans[stage] = round((now - self._last) * 1000.0, 1)
        self._last = now

    def audit_view(self) -> dict[str, float]:
        """판정 감사 기록 시점까지 아는 구간 — 닫힌 구간 + 진행 중 구간(P4) 경과.

        게이트 노드가 감사 레코드를 쓰는 순간(P4 안)에 부른다. P5·P6는 이 시점에 모르므로
        DEBUG 줄(`render`)에만 남는다.
        """
        out = {f"{k}_ms": v for k, v in self._spans.items()}
        out["p4_until_audit_ms"] = round((time.perf_counter() - self._last) * 1000.0, 1)
        return out

    def render(self) -> str:
        """DEBUG 1줄용 — `p1=… p2=… … total=…`(ms)."""
        parts = [f"{k}={v:.1f}" for k, v in self._spans.items()]
        parts.append(f"total={(self._last - self._start) * 1000.0:.1f}")
        return " ".join(parts)
