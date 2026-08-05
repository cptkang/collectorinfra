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

from noise_gate.domain.alarm import (
    AlarmAnalysisResult,
    AlarmEvent,
    AlarmHistoryStats,
    MessageEnrichment,
    ProcessSnapshot,
)
from noise_gate.domain.notification_policy import NotificationDecision


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
    correlated: bool                                    # Plan 60 E2: 워커가 시드한 크로스-호스트 상관 매칭 여부
    correlation_meta: Optional[dict]                    # Plan 60 E2: 상관 억제 시 클러스터 메타(대표 지문·멤버 순번·유사도) — correlated일 때만 채워짐
    recurrence: Optional[dict]                          # Plan 60 E1: 재통보 시 직전 창 재발 메타(대표 알람 표기용)
    semantic_annotation: Optional[dict]                 # Plan 60 B-7 L-2: 의미적 근접중복 후보 주석(감사 전용·판정 무관, semantic_dedup_annotation_enabled+provider 가용 시에만)
    annotation: Optional[dict]                          # Plan 60 E7-a: 워커가 산출한 계획-무해 코로보레이션 게이팅용 주석 신호(annotation_planned_suppress 시에만·off/없으면 None)
    enrichment: Optional[MessageEnrichment]             # Plan 60 E6: kind별 L1 보강 블록(message_enrichment_enabled 시에만 채워짐)
    anomaly_severity: Optional[int]                     # Plan 60 E3: enricher가 산출한 동적 baseline 이상 상향 후보(dynamic_baseline_enabled 시에만 채워짐)
    investigation_briefing: Optional[dict]              # Plan 64 CW-A: sre_agent 조사 서비스 브리핑(investigation_trigger_enabled 시에만 채워짐·notifier가 통보 첨부)
    investigation_escalation: Optional[dict]            # Plan 64 CW-C: escalate-only 후속 통보 승격 데이터(fault_escalation_enabled + verdict.escalate 시에만 채워짐·notifier가 상향 안내 첨부·게이트 판정 소급 변경 없음)
    investigation_pending: Optional[dict]               # Plan 66 3-E: 후속 모드에서 submit만 된 조사 식별자(investigation_followup_enabled 시에만 채워짐·notifier가 즉시 통보 후 백그라운드로 poll·후속 발송)


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
    from noise_gate.application.nodes.alarm_analyzer import alarm_analyzer_node
    from noise_gate.application.nodes.alarm_notifier import alarm_notifier_node

    history_enabled = True
    gate_enabled = False
    enricher_enabled = False
    message_enrichment = False
    investigation_enabled = False
    if config is not None:
        history_enabled = bool(config.alarm.history_enabled)
        noise_gate = getattr(config, "noise_gate", None)
        gate_enabled = bool(getattr(noise_gate, "enable_noise_gate", False))
        # (E5) Advisory Enricher는 게이트 활성 + enable_agentic_enricher일 때만 삽입한다.
        enricher_enabled = gate_enabled and bool(
            getattr(noise_gate, "enable_agentic_enricher", False)
        )
        # (Plan 60 E6) 메시지 기반 L1 보강은 context_enricher가 수집원이다(옵트인).
        message_enrichment = bool(
            getattr(noise_gate, "message_enrichment_enabled", False)
        )
        # (Plan 64 CW-A) 자동 조사 트리거는 게이트 활성 + investigation_trigger_enabled일 때만
        # gate 다음·notifier 이전에 삽입한다. off(기본)면 미배선 → 노드집합·경로 비트동일(회귀 0).
        investigation_enabled = gate_enabled and bool(
            getattr(noise_gate, "investigation_trigger_enabled", False)
        )

    # context_enricher는 history/게이트/E6 보강 중 하나라도 필요하면 포함한다(수집원).
    # 게이트 활성 시엔 noise_context 수집원이라 끌 수 없다. 전부 off면 미포함(회귀 0).
    include_enricher = history_enabled or gate_enabled or message_enrichment

    builder = StateGraph(AlarmState)
    builder.add_node("alarm_analyzer", alarm_analyzer_node)
    builder.add_node("alarm_notifier", alarm_notifier_node)

    if include_enricher:
        from noise_gate.application.nodes.alarm_context_enricher import (
            alarm_context_enricher_node,
        )

        builder.add_node("alarm_context_enricher", alarm_context_enricher_node)
        builder.set_entry_point("alarm_context_enricher")
        builder.add_edge("alarm_context_enricher", "alarm_analyzer")
    else:
        builder.set_entry_point("alarm_analyzer")

    if gate_enabled:
        # 지연 import — notification_gate는 게이트 활성 시에만 로드한다.
        from noise_gate.application.nodes.notification_gate import (
            notification_gate_node,
        )

        builder.add_node("notification_gate", notification_gate_node)
        if enricher_enabled:
            # (E5) alarm_analyzer → agentic_enricher → notification_gate 삽입.
            # 지연 import — enricher 활성 시에만 로드한다.
            from noise_gate.application.nodes.agentic_enricher import (
                agentic_enricher_node,
            )

            builder.add_node("agentic_enricher", agentic_enricher_node)
            builder.add_edge("alarm_analyzer", "agentic_enricher")
            builder.add_edge("agentic_enricher", "notification_gate")
        else:
            builder.add_edge("alarm_analyzer", "notification_gate")
        if investigation_enabled:
            # (Plan 64 CW-A) gate → investigation_trigger → notifier 삽입.
            # 지연 import — 트리거 활성 시에만 로드한다.
            from noise_gate.application.nodes.investigation_trigger import (
                investigation_trigger_node,
            )

            builder.add_node("investigation_trigger", investigation_trigger_node)
            builder.add_edge("notification_gate", "investigation_trigger")
            builder.add_edge("investigation_trigger", "alarm_notifier")
        else:
            builder.add_edge("notification_gate", "alarm_notifier")
    else:
        builder.add_edge("alarm_analyzer", "alarm_notifier")

    builder.add_edge("alarm_notifier", END)
    return builder.compile()
