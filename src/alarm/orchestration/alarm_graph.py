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
from src.alarm.domain.notification_policy import NotificationDecision


class AlarmState(TypedDict):
    """알람 분석 그래프 상태."""

    alarm_event: AlarmEvent
    history_stats: Optional[AlarmHistoryStats]
    process_snapshot: Optional[ProcessSnapshot]
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]
    # ── Plan 52: 노이즈 게이트 (enable_noise_gate 활성 시에만 채워짐) ──
    noise_context: Optional[dict]                       # enricher가 수집한 억제 신호
    notification_decision: Optional[NotificationDecision]  # gate가 산출한 4-티어 판단
    self_heal: bool                                     # 워커가 시드한 자가복구 매칭 여부
    inhibited: bool                                     # 워커가 시드한 인히비션 매칭 여부 (E2)
    flapping: bool                                      # 워커가 시드한 플래핑 매칭 여부 (E2)
    storm: bool                                         # 워커가 시드한 스톰 매칭 여부 (E2)


def build_alarm_graph(config=None):  # noqa: ANN001
    """알람 분석 LangGraph 서브그래프를 빌드한다.

    노드를 지연 import하여 순환 import를 방지한다.

    플래그 조합별 배선 (§8.1 — history_enabled × enable_noise_gate):
        | history | gate | 배선 |
        |  F  |  F  | analyzer → notifier (현행 2노드, 무변경) |
        |  T  |  F  | enricher → analyzer → notifier (현행 3노드, 무변경) |
        |  F  |  T  | enricher(강제 포함) → analyzer → gate → notifier |
        |  T  |  T  | enricher → analyzer → gate → notifier |

    핵심: 게이트 활성 시 enricher는 noise_context 수집원이므로 history_enabled와
    무관하게 **강제 포함**한다(include_enricher = history_enabled or gate_enabled).
    게이트 비활성(기본값)이면 게이트 노드를 배선하지 않아 기존 경로가 무변경이다(회귀 0).

    Args:
        config: AppConfig — alarm.history_enabled / noise_gate.enable_noise_gate 참조.
            None이면 history 기본(True)·gate 비활성으로 본다. noise_gate 속성이 없는
            경량 설정(테스트 SimpleNamespace 등)도 getattr로 안전 처리한다.

    Returns:
        컴파일된 LangGraph CompiledStateGraph
    """
    from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node
    from src.alarm.application.nodes.alarm_notifier import alarm_notifier_node

    history_enabled = True
    gate_enabled = False
    if config is not None:
        history_enabled = bool(config.alarm.history_enabled)
        noise_gate = getattr(config, "noise_gate", None)
        gate_enabled = bool(getattr(noise_gate, "enable_noise_gate", False))

    # 게이트 활성 시 enricher 강제 포함(noise_context 수집원이므로 끌 수 없음).
    include_enricher = history_enabled or gate_enabled

    builder = StateGraph(AlarmState)
    builder.add_node("alarm_analyzer", alarm_analyzer_node)
    builder.add_node("alarm_notifier", alarm_notifier_node)

    if include_enricher:
        from src.alarm.application.nodes.alarm_context_enricher import (
            alarm_context_enricher_node,
        )

        builder.add_node("alarm_context_enricher", alarm_context_enricher_node)
        builder.set_entry_point("alarm_context_enricher")
        builder.add_edge("alarm_context_enricher", "alarm_analyzer")
    else:
        builder.set_entry_point("alarm_analyzer")

    if gate_enabled:
        # 지연 import — notification_gate는 게이트 활성 시에만 로드한다.
        from src.alarm.application.nodes.notification_gate import (
            notification_gate_node,
        )

        builder.add_node("notification_gate", notification_gate_node)
        builder.add_edge("alarm_analyzer", "notification_gate")
        builder.add_edge("notification_gate", "alarm_notifier")
    else:
        builder.add_edge("alarm_analyzer", "alarm_notifier")

    builder.add_edge("alarm_notifier", END)
    return builder.compile()
