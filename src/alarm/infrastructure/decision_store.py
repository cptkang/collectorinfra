"""발송 판단 감사 저장소 (Plan 52 §8.3 / Plan 54 §6 공용).

`NotificationDecision`을 JSONL 1줄로 append 적재한다(읽기전용 DB 무관, 로컬 파일 감사).
원칙(억제 ≠ 삭제): DASHBOARD/SUPPRESS 결정도 반드시 기록하여 억제 내역을 추적·집계한다.
기록 실패가 알람 발송을 막아서는 안 되므로, 실패는 logger.warning 후 무시한다(graceful degradation).

표준 라이브러리(json/pathlib/datetime/logging)만 사용한다. 외부 패키지·Redis 금지.
NotificationDecision은 domain 계층이므로 infrastructure에서 import 가능하다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.alarm.domain.notification_policy import NotificationDecision, TIER_SUPPRESS

logger = logging.getLogger(__name__)


class DecisionStore:
    """발송 판단을 JSONL로 적재하고 티어별 집계·억제율을 산출하는 경량 감사 저장소."""

    def __init__(self, path: str, enabled: bool = True) -> None:
        """저장 경로와 활성 여부를 받는다.

        enabled=False면 record/aggregate가 부작용 없이 no-op 동작한다.
        """
        self.path = Path(path)
        self.enabled = enabled

    def record(
        self,
        decision: NotificationDecision,
        *,
        alarm_id: str = "",
        ts: Optional[datetime] = None,
    ) -> None:
        """결정을 JSONL 한 줄로 append 한다.

        tier·reason·priority·signals·fingerprint·alarm_id·ts(ISO8601)를 기록한다.
        DASHBOARD/SUPPRESS 결정도 반드시 기록한다(억제 ≠ 삭제).
        디렉토리는 자동 생성하며, 기록 실패 시 logger.warning 후 무시(발송 차단 금지).
        enabled=False면 no-op.
        """
        if not self.enabled:
            return
        when = ts or datetime.now(timezone.utc)
        record = {
            "ts": when.isoformat(),
            "alarm_id": alarm_id,
            "tier": decision.tier,
            "reason": decision.reason,
            "priority": decision.priority,
            "fingerprint": decision.fingerprint,
            "signals": decision.signals,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:  # 디스크/권한 등 — 발송을 막지 않고 경고만
            logger.warning("결정 감사 기록 실패(무시): %s", exc)

    def aggregate(self, *, window_seconds: Optional[int] = None) -> dict:
        """티어별 건수와 억제율(suppress/total)을 집계한다.

        window_seconds가 주어지면 현재 시각 기준 해당 창 내 기록만 집계한다.
        파일이 없거나 비활성이면 빈 집계를 반환한다.
        """
        empty = {"total": 0, "by_tier": {}, "suppress_ratio": 0.0}
        if not self.enabled or not self.path.exists():
            return empty

        cutoff_ts: Optional[float] = None
        if window_seconds is not None:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - window_seconds

        by_tier: dict[str, int] = {}
        total = 0
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 손상된 줄은 건너뜀
                    if cutoff_ts is not None and not self._within_window(rec, cutoff_ts):
                        continue
                    tier = rec.get("tier", "")
                    by_tier[tier] = by_tier.get(tier, 0) + 1
                    total += 1
        except OSError as exc:
            logger.warning("결정 감사 집계 읽기 실패(무시): %s", exc)
            return empty

        suppress = by_tier.get(TIER_SUPPRESS, 0)
        ratio = (suppress / total) if total else 0.0
        return {"total": total, "by_tier": by_tier, "suppress_ratio": ratio}

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
