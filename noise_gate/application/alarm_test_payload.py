"""알람 분석 테스트 API의 응답·웹 UI 푸시 payload 조립 (plans/166 A8).

`src/api/routes/alarm.py`의 두 테스트 엔드포인트(`/alarm/analyze-test`·`/alarm/analyze-test/raw`)가
같은 본문으로 조립하던 dict를 옮겨 왔다. 노드 호출·발송·버스 발행은 라우트에 남고, 여기에는
도메인 객체 → dict 변환(순수 함수)만 둔다.

계층: application — domain(AlarmEvent·AlarmAnalysisResult·AlarmHistoryStats·ProcessSnapshot)만 소비.
"""

from __future__ import annotations

from typing import Any

from noise_gate.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    AlarmHistoryStats,
    ProcessSnapshot,
)


def stats_to_dict(stats: AlarmHistoryStats) -> dict[str, Any]:
    """AlarmHistoryStats를 응답용 dict로 변환한다 (Plan 47 §5.9)."""
    return {
        "total_count": stats.total_count,
        "count_24h": stats.count_24h,
        "count_7d": stats.count_7d,
        "count_30d": stats.count_30d,
        "same_resource_count": stats.same_resource_count,
        "first_seen": stats.first_seen.isoformat() if stats.first_seen else None,
        "last_seen": stats.last_seen.isoformat() if stats.last_seen else None,
        "hour_histogram": {str(h): c for h, c in sorted(stats.hour_histogram.items())},
        "median_interval_minutes": stats.median_interval_minutes,
        "interval_cv": stats.interval_cv,
        "period_label": stats.period_label,
        "truncated": stats.truncated,
        "pre_classification": stats.pre_classification,
        "source": stats.source,
    }


def process_to_dict(snapshot: ProcessSnapshot) -> dict[str, Any]:
    """ProcessSnapshot을 응답/UI push용 dict로 변환한다 (Plan 47-1 §5.6).

    args는 이미 마스킹된 값이므로 그대로 노출한다 (평문 인자는 ProcessInfo에 없음).
    """
    return {
        "alarm_kind": snapshot.alarm_kind,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "total_count": snapshot.total_count,
        "source_host": snapshot.source_host,
        "top": [
            {
                "name": p.name,
                "pid": p.pid,
                "ppid": p.ppid,
                "user": p.user,
                "p100cpu": p.p100cpu,
                "pcpu": p.pcpu,
                "pmem": p.pmem,
                "rss": p.rss,
                "args": p.args,
            }
            for p in snapshot.top
        ],
    }


def identity_dict(event: AlarmEvent) -> dict[str, Any] | None:
    """SSE payload용 서버 식별 dict (없으면 None)."""
    identity = getattr(event, "server_identity", None)
    return identity.to_dict() if identity is not None else None


def build_ui_push_payload(
    event: AlarmEvent,
    analysis_result: AlarmAnalysisResult,
    history_stats: AlarmHistoryStats | None,
    process_snapshot: ProcessSnapshot | None,
) -> dict[str, Any]:
    """테스트 엔드포인트의 웹 UI 푸시(`push_to_ui`) `alarm_notification` payload를 조립한다."""
    return {
        "type": "alarm_notification",
        "alarm_id": event.alarm_id,
        "severity": event.severity,
        "severity_label": analysis_result.severity_label,
        "alarm_name": event.alarm_name,
        "db_id": event.db_id,
        "server_name": event.server_name,
        "hostname": event.hostname,
        "ip_address": event.ip_address,
        "resource_type": event.resource_type,
        "resource_name": event.resource_name,
        "alarm_status": event.alarm_status,
        "server_identity": identity_dict(event),  # (D-188) UI 헤더(등록명·IP·존) 렌더용
        "summary": analysis_result.summary,
        "probable_cause": analysis_result.probable_cause,
        "recommended_action": analysis_result.recommended_action,
        # Plan 47: 패턴 분석 결과
        "pattern_type": analysis_result.pattern_type,
        "is_routine": analysis_result.is_routine,
        "pattern_analysis": analysis_result.pattern_analysis,
        # (Plan 83 T6) 결정적 사전분류 — 워커 경로 payload와 대칭
        "pre_classification": analysis_result.pre_classification,
        # Plan 47: 패턴 근거 표 렌더용 — 이력 통계 원본 + 현재 알람 시각
        "alarm_time": event.alarm_time.isoformat(),
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "history_stats": stats_to_dict(history_stats) if history_stats else None,
        # Plan 47-1: 영향 프로세스 표 렌더용 (args는 마스킹된 값)
        "process_snapshot": process_to_dict(process_snapshot) if process_snapshot else None,
    }
