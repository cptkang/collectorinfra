"""TICKET 티어 일배치 요약 큐 (Plan 52 §7 / Phase E3).

TICKET 티어로 라우팅된 발송 판단(`NotificationDecision`)을 JSONL 1줄로 append 적재한다.
실시간 별도 채널은 없으며(사용자 결정), TICKET 티어 = (a) 감사 적재(decision_store) +
(b) DASHBOARD(SSE) 표시 + (c) 일배치 요약 큐 적재(이 모듈)의 3요소로 구성된다.

운영자/배치 작업이 `read_pending()`/`summarize()`로 누적된 TICKET을 하루 1회 요약 통보한다.
적재 실패가 알람 발송을 막아서는 안 되므로, 실패는 logger.warning 후 무시한다(graceful degradation).

표준 라이브러리(json/pathlib/datetime/logging)만 사용한다. 외부 패키지·Redis 금지.
TIER_TICKET 상수는 domain 계층이므로 infrastructure에서 import 가능하다(decision_store.py 동일).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from noise_gate.domain.notification_policy import TIER_TICKET

logger = logging.getLogger(__name__)


class TicketBatchQueue:
    """TICKET 결정을 JSONL로 적재하고 일배치 요약(전체 읽기·집계)을 제공하는 경량 큐."""

    def __init__(self, path: str, enabled: bool = True) -> None:
        """저장 경로와 활성 여부를 받는다.

        enabled=False면 enqueue가 부작용 없이 no-op 동작하고, 요약은 빈 결과를 반환한다.
        """
        self.path = Path(path)
        self.enabled = enabled

    def enqueue(
        self,
        decision,  # noqa: ANN001 — NotificationDecision (덕 타이핑)
        *,
        alarm_id: str = "",
        ts: Optional[datetime] = None,
    ) -> None:
        """TICKET 티어 결정을 JSONL 한 줄로 append 한다.

        ts(ISO8601)·alarm_id·fingerprint·reason·priority·signals를 기록한다.
        TICKET 외 티어(PAGE/DASHBOARD/SUPPRESS)는 큐 대상이 아니므로 무시한다.
        디렉토리는 자동 생성하며, 기록 실패 시 logger.warning 후 무시(발송 차단 금지).
        enabled=False면 no-op.
        """
        if not self.enabled:
            return
        if getattr(decision, "tier", None) != TIER_TICKET:
            return  # TICKET만 일배치 큐에 적재한다(감사·SSE는 별도 경로)
        when = ts or datetime.now(timezone.utc)
        record = {
            "ts": when.isoformat(),
            "alarm_id": alarm_id,
            "fingerprint": getattr(decision, "fingerprint", ""),
            "reason": getattr(decision, "reason", ""),
            "priority": getattr(decision, "priority", 0),
            "signals": getattr(decision, "signals", {}),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:  # 디스크/권한 등 — 발송을 막지 않고 경고만
            logger.warning("TICKET 일배치 큐 적재 실패(무시): %s", exc)

    def read_pending(self) -> list[dict]:
        """적재된 모든 TICKET 항목을 일배치 요약용으로 전체 읽어 반환한다.

        파일이 없거나 비활성이면 빈 리스트를 반환한다. 손상된 줄은 건너뛰며,
        읽기 실패 시 logger.warning 후 빈 리스트를 반환한다(graceful).
        """
        if not self.enabled or not self.path.exists():
            return []
        items: list[dict] = []
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue  # 손상된 줄은 건너뜀
        except OSError as exc:
            logger.warning("TICKET 일배치 큐 읽기 실패(무시): %s", exc)
            return []
        return items

    def summarize(self, *, window_seconds: Optional[int] = None) -> dict:
        """누적 TICKET을 일배치 요약(총건수 + 핑거프린트별 카운트)으로 집계한다.

        window_seconds가 주어지면 현재 시각 기준 해당 창 내 항목만 집계한다.
        반환: {"total": int, "by_fingerprint": {fingerprint: count}}.
        """
        records = self.read_pending()
        if window_seconds is not None:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - window_seconds
            records = [r for r in records if self._within_window(r, cutoff_ts)]

        by_fingerprint: dict[str, int] = {}
        for rec in records:
            fp = str(rec.get("fingerprint", ""))
            by_fingerprint[fp] = by_fingerprint.get(fp, 0) + 1
        return {"total": len(records), "by_fingerprint": by_fingerprint}

    @staticmethod
    def _within_window(rec: dict, cutoff_ts: float) -> bool:
        """기록의 ts(ISO)가 cutoff(epoch초) 이후인지 판정한다(파싱 실패 시 포함)."""
        raw = rec.get("ts")
        if not raw:
            return True
        try:
            return datetime.fromisoformat(raw).timestamp() >= cutoff_ts
        except (ValueError, TypeError):
            return True
