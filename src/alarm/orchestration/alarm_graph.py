"""알람 분석 LangGraph 서브그래프.

기존 쿼리 그래프와 독립된 2-노드 경량 서브그래프.
트리거: Redis Stream 이벤트 (AlarmWorker가 호출)

그래프 구조:
    START → alarm_analyzer → alarm_notifier → END

상태(AlarmState):
    alarm_event: AlarmEvent        — 입력 알람 이벤트
    analysis_result: Optional      — LLM 분석 결과
    error: Optional[str]           — 오류 메시지
"""

from __future__ import annotations

from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from src.alarm.domain.alarm import AlarmAnalysisResult, AlarmEvent


class AlarmState(TypedDict):
    """알람 분석 그래프 상태."""

    alarm_event: AlarmEvent
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]


def build_alarm_graph(config=None):  # noqa: ANN001
    """알람 분석 LangGraph 서브그래프를 빌드한다.

    노드를 지연 import하여 순환 import를 방지한다.

    Args:
        config: AppConfig (현재 미사용, 향후 그래프 레벨 설정 확장 시 활용)

    Returns:
        컴파일된 LangGraph CompiledStateGraph
    """
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node
    from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

    builder = StateGraph(AlarmState)
    builder.add_node("alarm_analyzer", alarm_analyzer_node)
    builder.add_node("alarm_notifier", alarm_notifier_node)
    builder.set_entry_point("alarm_analyzer")
    builder.add_edge("alarm_analyzer", "alarm_notifier")
    builder.add_edge("alarm_notifier", END)
    return builder.compile()
