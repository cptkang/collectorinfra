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

from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
)

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
        recurrence: Optional[dict] = None,
        correlation_meta: Optional[dict] = None,
        semantic_annotation: Optional[dict] = None,
    ) -> None:
        """결정을 JSONL 한 줄로 append 한다.

        tier·reason·priority·signals·fingerprint·alarm_id·ts(ISO8601)를 기록한다.
        DASHBOARD/SUPPRESS 결정도 반드시 기록한다(억제 ≠ 삭제).
        디렉토리는 자동 생성하며, 기록 실패 시 logger.warning 후 무시(발송 차단 금지).
        enabled=False면 no-op.

        recurrence(Plan 60 E1): 재통보 시 직전 창 재발 메타(count/first_seen 등)를
        받으면 **최상위 `recurrence` 필드**로 기록한다(NotificationDecision.signals
        동결 스키마는 훼손하지 않는다, §3.3). None이면 키를 넣지 않아 현행과 동일하다.

        correlation_meta(Plan 60 E2): 크로스-호스트 상관 억제(correlated=True) 시 대표
        식별자·멤버 순번·유사도(`{representative_fp, member_seq, similarity}`)를 받으면
        **최상위 `correlation_meta` 필드**로 기록한다(recurrence와 동일 패턴 — signals 동결
        스키마 미훼손, §10). None이면 키를 넣지 않아 기존 스냅샷과 동일하다.

        semantic_annotation(Plan 60 B-7 L-2 · §15.4 D-035 경계): 의미적 근접중복 **후보 주석**
        (`{semantic_near_dup: {matched_fp, similarity, hint}}`)를 받으면 **최상위
        `semantic_annotation` 필드**로 기록한다(recurrence/correlation_meta와 동일 패턴 —
        signals 동결 스키마 미훼손). **임베딩은 감사·관측 전용**이며 tier/reason/priority·
        지문은 이 값과 무관하다(주석은 결정 산출 뒤 병렬 기록). None이면 키를 넣지 않아 기존
        스냅샷과 비트 동일하다(옵트인 off·provider inert 시 회귀 0).
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
        if recurrence is not None:
            record["recurrence"] = recurrence
        if correlation_meta is not None:
            record["correlation_meta"] = correlation_meta
        if semantic_annotation is not None:
            record["semantic_annotation"] = semantic_annotation
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:  # 디스크/권한 등 — 발송을 막지 않고 경고만
            logger.warning("결정 감사 기록 실패(무시): %s", exc)

    def record_resolution(
        self,
        *,
        fingerprint: str,
        duration_seconds: float,
        ts: Optional[datetime] = None,
    ) -> None:
        """자가복구(self-heal) 소요시간을 JSONL 한 줄로 append 한다 (D-049).

        `type="resolution"` 레코드로 적재하여 발송 판단(decision) 레코드와 구분한다.
        aggregate가 이 레코드를 by_tier/total 집계에서 제외하고
        `auto_recovery_mttr_seconds`(self-heal 소요시간 평균) 산출에만 사용한다.

        주의(환각 금지): self-heal은 sev1..suppress_max만 대상(sev3 제외)이라
        **편향 부분지표**이며, paged incident의 운영자 MTTR(`incident_mttr_seconds`)과
        명확히 구분된다(라벨 `auto_recovery_mttr_seconds`).

        기록 실패는 warning 후 무시(발송 차단 금지). enabled=False면 no-op.
        """
        if not self.enabled:
            return
        when = ts or datetime.now(timezone.utc)
        record = {
            "type": "resolution",
            "fingerprint": fingerprint,
            "duration_seconds": duration_seconds,
            "ts": when.isoformat(),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("자가복구 소요시간 기록 실패(무시): %s", exc)

    def record_recurrence(
        self,
        *,
        fingerprint: str,
        count: int,
        first_seen_ts: Optional[float] = None,
        alarm_id: str = "",
        ts: Optional[datetime] = None,
        annotation: Optional[dict] = None,
    ) -> None:
        """재발생 억제 카운트를 JSONL 한 줄로 append 한다 (Plan 60 E1 · E7-a).

        `type="recurrence"` 레코드로 적재하여 발송 판단(decision)·resolution 레코드와
        구분한다(record_resolution 전례 동일). 억제된 재발생은 그래프에 진입하지 않아
        gate 감사 사각지대였다(§3.1) — 워커가 직접 이 메서드로 "억제≠삭제"를 강화한다.
        `aggregate()`는 `type` 필드 보유 레코드를 by_tier/total에서 제외하므로 기존
        티어 집계는 불변이다.

        (Plan 60 E7-a §17.3) `annotation`(dict|None)이 주어지면 계획작업/해소/운영자접수
        주석 신호를 최상위 필드로 첨부한다 — 재발신이 억제로 폐기되던 텍스트 신호를 원
        인시던트 컨텍스트에 보존한다("억제≠삭제"의 텍스트 확장·재통보 0). annotation=None
        (기본·플래그 off)이면 키를 추가하지 않아 기존 레코드와 **비트동일**(회귀 0).

        기록 실패는 warning 후 무시(발송 차단 금지). enabled=False면 no-op.
        """
        if not self.enabled:
            return
        when = ts or datetime.now(timezone.utc)
        record = {
            "type": "recurrence",
            "fingerprint": fingerprint,
            "count": count,
            "first_seen_ts": first_seen_ts,
            "alarm_id": alarm_id,
            "ts": when.isoformat(),
        }
        if annotation is not None:
            # E7-a 주석 하베스트 — off/미하베스트면 키 미포함(비트동일).
            record["annotation"] = annotation
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("재발생 카운트 기록 실패(무시): %s", exc)

    def record_investigation(
        self,
        *,
        alarm_id: str = "",
        fingerprint: str = "",
        investigation_id: Optional[str] = None,
        status: str = "",
        verdict: Optional[str] = None,
        ts: Optional[datetime] = None,
    ) -> None:
        """자동 조사 트리거 결과를 JSONL 한 줄로 append 한다 (Plan 64 CW-A · graceful).

        `type="investigation"` 레코드로 적재하여 발송 판단(decision)·resolution·recurrence
        레코드와 구분한다(record_recurrence 전례 동일). 게이트 PAGE 결정 직후 sre_agent 조사
        서비스에 submit→poll한 결과(investigation_id·최종 status·verdict)를 감사에 남긴다.
        `aggregate()`는 `type` 필드 보유 레코드를 by_tier/total에서 제외하므로 기존 티어 집계는
        불변이다(회귀 0).

        조사 서비스 다운/타임아웃/거부 시에도 status(down/timeout/rejected 등)를 정직하게
        기록한다(침묵 폴백 금지). 기록 실패는 warning 후 무시(발송 차단 금지). enabled=False면 no-op.
        """
        if not self.enabled:
            return
        when = ts or datetime.now(timezone.utc)
        record = {
            "type": "investigation",
            "alarm_id": alarm_id,
            "fingerprint": fingerprint,
            "investigation_id": investigation_id,
            "status": status,
            "verdict": verdict,
            "ts": when.isoformat(),
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            logger.warning("조사 트리거 감사 기록 실패(무시): %s", exc)

    @staticmethod
    def _empty_aggregate() -> dict:
        """집계 결과의 빈 형태(전 키 포함)를 반환한다(E3 확장 — 키 스키마 일관성)."""
        return {
            "total": 0,
            "by_tier": {},
            "suppress_ratio": 0.0,
            # ── E3 확장(하위호환 — 기존 3키 유지) ──
            "page_count": 0,
            "ticket_count": 0,
            "dashboard_count": 0,
            "suppress_count": 0,
            "actionable": 0,        # page + ticket
            "actionable_ratio": 0.0,
            "last_event_ts": None,
            "last_event_age_seconds": None,
            # ── D-049: 자가복구 소요시간 평균(편향 부분지표 — sev3 제외) ──
            "auto_recovery_mttr_seconds": None,
        }

    def aggregate(self, *, window_seconds: Optional[int] = None) -> dict:
        """티어별 건수와 억제율·액션가능 비율·최근 이벤트 경과를 집계한다(E3 확장).

        기존 키(total/by_tier/suppress_ratio)는 그대로 유지하며(하위호환), 운영지표용으로
        page_count/ticket_count/dashboard_count/suppress_count, actionable(=page+ticket),
        actionable_ratio(=actionable/total), last_event_ts(ISO|None),
        last_event_age_seconds(now-last|None)를 추가한다.

        window_seconds가 주어지면 현재 시각 기준 해당 창 내 기록만 집계한다.
        파일이 없거나 비활성이면 빈 집계(전 키 포함)를 반환한다.
        """
        if not self.enabled or not self.path.exists():
            return self._empty_aggregate()

        cutoff_ts: Optional[float] = None
        if window_seconds is not None:
            cutoff_ts = datetime.now(timezone.utc).timestamp() - window_seconds

        by_tier: dict[str, int] = {}
        total = 0
        latest_dt: Optional[datetime] = None
        latest_iso: Optional[str] = None
        resolution_durations: list[float] = []  # (D-049) self-heal 소요시간 수집
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
                    # (D-049 · Plan 60 E1) 비-decision 레코드(`type` 필드 보유)는
                    # by_tier/total 집계에서 일반 제외한다(향후 레코드 타입 추가에도 안전).
                    # resolution은 duration 수집 후, 그 외(recurrence 등)는 그냥 제외한다.
                    rec_type = rec.get("type")
                    if rec_type:
                        if rec_type == "resolution":
                            dur = rec.get("duration_seconds")
                            if isinstance(dur, (int, float)):
                                resolution_durations.append(float(dur))
                        continue
                    tier = rec.get("tier", "")
                    by_tier[tier] = by_tier.get(tier, 0) + 1
                    total += 1
                    parsed = self._parse_ts(rec.get("ts"))
                    if parsed is not None and (latest_dt is None or parsed > latest_dt):
                        latest_dt = parsed
                        latest_iso = str(rec.get("ts"))
        except OSError as exc:
            logger.warning("결정 감사 집계 읽기 실패(무시): %s", exc)
            return self._empty_aggregate()

        page = by_tier.get(TIER_PAGE, 0)
        ticket = by_tier.get(TIER_TICKET, 0)
        dashboard = by_tier.get(TIER_DASHBOARD, 0)
        suppress = by_tier.get(TIER_SUPPRESS, 0)
        actionable = page + ticket
        last_age: Optional[float] = None
        if latest_dt is not None:
            last_age = max(
                0.0, datetime.now(timezone.utc).timestamp() - latest_dt.timestamp()
            )
        return {
            "total": total,
            "by_tier": by_tier,
            "suppress_ratio": (suppress / total) if total else 0.0,
            "page_count": page,
            "ticket_count": ticket,
            "dashboard_count": dashboard,
            "suppress_count": suppress,
            "actionable": actionable,
            "actionable_ratio": (actionable / total) if total else 0.0,
            "last_event_ts": latest_iso,
            "last_event_age_seconds": last_age,
            "auto_recovery_mttr_seconds": (
                sum(resolution_durations) / len(resolution_durations)
                if resolution_durations
                else None
            ),
        }

    def meta_alerts(
        self,
        *,
        window_seconds: int,
        suppress_ratio_threshold: float,
        min_events: int,
    ) -> list[dict]:
        """억제기 메타모니터링 경보를 산출한다(Google SRE "모니터를 모니터링").

        decision_store에서 직접 산출 가능한 신호만 사용한다(외부 데이터 금지):
            - 억제율 > suppress_ratio_threshold → high_suppress_ratio
              (억제기가 과도하게 억제 — 진짜 장애 묵살 위험).
            - 창 내 total < min_events → no_events
              (이벤트 무수신 — 억제기·수집 경로 장애 가능성).

        둘은 OR 조건이며 동시 충족 시 모두 반환한다. 정상이면 빈 리스트.
        """
        agg = self.aggregate(window_seconds=window_seconds)
        total = agg["total"]
        suppress_ratio = agg["suppress_ratio"]

        alerts: list[dict] = []
        if total > 0 and suppress_ratio > suppress_ratio_threshold:
            alerts.append({
                "type": "high_suppress_ratio",
                "value": suppress_ratio,
                "threshold": suppress_ratio_threshold,
                "window_seconds": window_seconds,
            })
        if total < min_events:
            alerts.append({
                "type": "no_events",
                "total": total,
                "min_events": min_events,
                "window_seconds": window_seconds,
            })
        return alerts

    @staticmethod
    def _parse_ts(raw) -> Optional[datetime]:  # noqa: ANN001
        """기록의 ts(ISO)를 datetime으로 파싱한다(실패 시 None)."""
        if not raw:
            return None
        try:
            return datetime.fromisoformat(str(raw))
        except (ValueError, TypeError):
            return None

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
