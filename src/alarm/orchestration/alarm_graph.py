"""알람 분석 LangGraph 서브그래프.

기존 쿼리 그래프와 독립된 경량 서브그래프.
트리거: Redis Stream 이벤트 (AlarmWorker가 호출)

그래프 구조 (Plan 47 — history_enabled=True 기본):
    START → alarm_context_enricher → alarm_analyzer → alarm_notifier → END

ALARM_HISTORY_ENABLED=false이면 기존 2-노드 구조를 유지한다 (완전 동일 동작):
    START → alarm_analyzer → alarm_notifier → END

상태(AlarmState):
    alarm_event: AlarmEvent        — 입력 알람 이벤트
    history_stats: Optional        — 폴스타 DB 이력 통계 (Plan 47, 실패 시 None)
    process_snapshot: Optional     — 영향 프로세스 스냅샷 (Plan 47-1, CPU/메모리 발생 알람만)
    analysis_result: Optional      — LLM 분석 결과
    error: Optional[str]           — 오류 메시지
"""

from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from src.alarm.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    AlarmHistoryStats,
    ProcessSnapshot,
)


class AlarmState(TypedDict):
    """알람 분석 그래프 상태."""

    alarm_event: AlarmEvent
    history_stats: Optional[AlarmHistoryStats]
    process_snapshot: Optional[ProcessSnapshot]
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]


def build_alarm_graph(config=None):  # noqa: ANN001
    """알람 분석 LangGraph 서브그래프를 빌드한다.

    노드를 지연 import하여 순환 import를 방지한다.

    Args:
        config: AppConfig — alarm.history_enabled=False이면 enricher 노드를 제외하여
            기존 2-노드 동작과 완전히 동일하게 구성한다. None이면 3-노드 구성
            (enricher가 런타임에 설정/리포지토리 미주입을 감지하여 건너뜀).

    Returns:
        컴파일된 LangGraph CompiledStateGraph
    """
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node
    from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

    history_enabled = True
    if config is not None:
        history_enabled = bool(config.alarm.history_enabled)

    builder = StateGraph(AlarmState)
    builder.add_node("alarm_analyzer", alarm_analyzer_node)
    builder.add_node("alarm_notifier", alarm_notifier_node)

    if history_enabled:
        from src.alarm.application.nodes.alarm_context_enricher import (
            alarm_context_enricher_node,
        )

        builder.add_node("alarm_context_enricher", alarm_context_enricher_node)
        builder.set_entry_point("alarm_context_enricher")
        builder.add_edge("alarm_context_enricher", "alarm_analyzer")
    else:
        builder.set_entry_point("alarm_analyzer")

    builder.add_edge("alarm_analyzer", "alarm_notifier")
    builder.add_edge("alarm_notifier", END)
    return builder.compile()
