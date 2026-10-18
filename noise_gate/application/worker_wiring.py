"""알람 워커 의존성 와이어링 (plans/177 W1).

`AlarmWorker`가 기동 시 만드는 리포지토리·저장소·클라이언트·발행기 생성 함수를 모은다.
각 함수는 설정(과 필요하면 Redis 클라이언트)만 받아 객체를 만들거나 None을 돌려준다 —
플래그 off·생성 실패는 None으로 흡수한다(graceful degradation). 본문은 `AlarmWorker._build_*`
에서 동작 그대로 옮겼고, 워커에는 같은 이름의 위임 메서드가 남아 `run()`·테스트 seam이 된다.

선택 의존(infrastructure 어댑터·`src.routing.db_registry` 등)은 함수 안에서 지연 import한다 —
테스트·하네스가 원천 모듈의 클래스를 바꿔 끼우는 지점과 import 시점을 보존하기 위해서다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from noise_gate.domain.cross_source import MODE_OFF, EpisodeTracker
from noise_gate.domain.page_review import MODE_OFF as PAGE_REVIEW_OFF
from noise_gate.domain.page_review import PageRegistry
from noise_gate.infrastructure.apm_noise_context import ApmNoiseContext
from noise_gate.infrastructure.cross_source_rules import load_rules

if TYPE_CHECKING:
    from noise_gate.infrastructure.investigation_store import InvestigationStore

# 이동 전과 같은 로거 이름 — 운영 로그 `%(name)s`와 테스트 caplog 로거 필터를 보존한다.
logger = logging.getLogger("noise_gate.application.alarm_worker")


def build_history_repo(config: Any) -> Any:
    """이력 조회 리포지토리를 생성한다 (Plan 47).

    history_enabled=False이거나 생성 실패 시 None을 반환한다 —
    패턴 분석만 생략되고 알람 분석·발송은 정상 진행된다 (graceful degradation).
    """
    if not config.alarm.history_enabled:
        return None
    try:
        from noise_gate.infrastructure.polestar_history import (  # noqa: I001 — 이동 전 import 순서 보존
            PolestarAlarmHistoryRepository,
        )
        from src.routing.db_registry import DBRegistry

        return PolestarAlarmHistoryRepository(
            DBRegistry(config), config.alarm
        )
    except Exception:
        logger.exception("알람 이력 리포지토리 생성 실패 — 패턴 분석 비활성으로 진행")
        return None


def build_identity_resolver(config: Any) -> Any:
    """서버 식별 역조회 리졸버를 생성한다 (D-188 · _build_history_repo 미러).

    상시 동작(끄는 플래그 없음 — D-162 §6 플래그 부채 원칙). 생성 실패 시 None — 식별 정보는
    존 라벨만 붙고(source=event) server_name·ip 승격이 생략된다(graceful, 알람 처리 정상 진행).
    """
    try:
        from noise_gate.infrastructure.polestar_hostname_resolver import (  # noqa: I001 — 이동 전 import 순서 보존
            PolestarHostnameResolver,
        )
        from src.routing.db_registry import DBRegistry

        return PolestarHostnameResolver(DBRegistry(config))
    except Exception:
        logger.exception("서버 식별 리졸버 생성 실패 — 역조회 없이 진행")
        return None


def build_process_client(config: Any) -> Any:
    """영향 프로세스 API 클라이언트를 생성한다 (Plan 47-1).

    process_enrich_enabled=False이거나 생성 실패 시 None을 반환한다 —
    프로세스 보강만 생략되고 알람 분석·발송은 정상 진행된다 (graceful degradation).
    """
    if not config.alarm.process_enrich_enabled:
        return None
    try:
        from noise_gate.infrastructure.polestar_process_api import (
            PolestarProcessApiClient,
        )

        return PolestarProcessApiClient(config.alarm)
    except Exception:
        logger.exception("영향 프로세스 클라이언트 생성 실패 — 프로세스 보강 비활성으로 진행")
        return None


def build_noise_repo(config: Any) -> Any:
    """노이즈 컨텍스트 리포지토리를 생성한다 (Plan 52).

    enable_noise_gate=False이거나 생성 실패 시 None을 반환한다 —
    노이즈 컨텍스트 수집만 생략되고(정책 계층이 보수적 PAGE 처리) 발송은 진행된다.
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    try:
        from noise_gate.infrastructure.polestar_noise_context import (  # noqa: I001 — 이동 전 import 순서 보존
            PolestarNoiseContextRepository,
        )
        from src.routing.db_registry import DBRegistry

        return PolestarNoiseContextRepository(
            DBRegistry(config), config.alarm
        )
    except Exception:
        logger.exception("노이즈 컨텍스트 리포지토리 생성 실패 — 보수적(수집 없이) 진행")
        return None


def build_apm_noise_ctx(config: Any) -> ApmNoiseContext | None:
    """제니퍼 유형 정책 공급자를 생성한다 (plans/144 §4.1 · 기동 시 1회).

    enable_noise_gate AND apm_noise_policy_enabled일 때만 만든다(기본 off → None → 현행
    비트 동일). 정책 파일 적재 실패는 공급자 안에서 경고 1줄 후 unavailable 공급자가 된다
    (제니퍼 = 보수 PAGE).
    """
    gate = config.noise_gate
    if not gate.enable_noise_gate or not getattr(gate, "apm_noise_policy_enabled", False):
        return None
    return ApmNoiseContext.load()


def build_episode_tracker(config: Any) -> EpisodeTracker | None:
    """크로스소스 사건 추적기를 생성한다 (plans/144 W3 · 기동 시 1회).

    enable_noise_gate AND cross_source_mode≠off일 때만 만든다(기본 off → None → 비트 동일).
    규칙 표 적재 실패는 적재기가 경고 1줄을 남기고 None — 사건 상관 전체가 꺼진다.
    """
    gate = config.noise_gate
    mode = str(getattr(gate, "cross_source_mode", MODE_OFF) or MODE_OFF)
    if not gate.enable_noise_gate or mode == MODE_OFF:
        return None
    rules = load_rules(
        getattr(gate, "cross_source_rules_path", "config/cross_source_rules.yaml")
    )
    if rules is None:
        return None
    logger.info("크로스소스 사건 상관 활성: mode=%s 규칙=%d", mode, len(rules))
    return EpisodeTracker(
        rules,
        mode=mode,
        idle_seconds=float(getattr(gate, "episode_idle_seconds", 900)),
        on_evict=lambda what, n: logger.warning(
            "크로스소스 사건 상한 초과(%s) — 오래된 %d건 정리", what, n
        ),
    )


def build_page_registry(config: Any) -> PageRegistry | None:
    """PAGE 필요성 재판정 레지스트리를 생성한다 (plans/162 §4.5 · 기동 시 1회).

    enable_noise_gate AND page_review_mode≠off일 때만 만든다(기본 off → None → 비트 동일).
    """
    gate = config.noise_gate
    mode = str(getattr(gate, "page_review_mode", PAGE_REVIEW_OFF) or PAGE_REVIEW_OFF)
    if not gate.enable_noise_gate or mode == PAGE_REVIEW_OFF:
        return None
    logger.info("PAGE 필요성 재판정 활성: mode=%s", mode)
    return PageRegistry(
        host_window_minutes=int(getattr(gate, "page_review_host_window_minutes", 30)),
        fanout_window_minutes=int(getattr(gate, "page_review_fanout_window_minutes", 10)),
        reminder_hours=int(getattr(gate, "page_review_reminder_hours", 24)),
        reopen_quiet_minutes=int(getattr(gate, "page_review_reopen_quiet_minutes", 30)),
        idle_close_minutes=int(getattr(gate, "page_review_idle_close_minutes", 0)),
        on_evict=lambda what, n: logger.warning(
            "PAGE 재판정 레지스트리 상한 초과(%s) — 오래된 %d건 정리", what, n
        ),
    )


def build_metric_baseline(config: Any) -> Any:
    """동적 baseline 이상탐지 어댑터를 생성한다 (Plan 60 E3 · D-079).

    enable_noise_gate=False 또는 dynamic_baseline_enabled=False이면 None을 반환한다 —
    이상탐지만 생략되고(anomaly_severity 미산출) 분석·발송은 정상 진행된다(회귀 0·graceful).
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    if not getattr(config.noise_gate, "dynamic_baseline_enabled", False):
        return None
    try:
        from noise_gate.infrastructure.polestar_metric_baseline import (  # noqa: I001 — 이동 전 import 순서 보존
            PolestarMetricBaselineAdapter,
            parse_metric_source_map,
        )
        from src.routing.db_registry import DBRegistry

        # (Plan 67 R3-(v) 인접) kind→메트릭 소스 매핑을 설정에서 주입한다(미설정 시 None →
        # 어댑터 기본 매핑). 벤더 스키마 상수는 domain이 아니라 설정·어댑터에 둔다.
        return PolestarMetricBaselineAdapter(
            DBRegistry(config),
            config.alarm,
            metric_source_map=parse_metric_source_map(
                getattr(
                    config.noise_gate, "anomaly_metric_source_map_csv", ""
                )
            ),
        )
    except Exception:
        logger.exception("메트릭 baseline 어댑터 생성 실패 — 이상탐지 없이 진행")
        return None


def build_embedding_provider(config: Any) -> Any:
    """알람 로컬 임베딩 provider를 생성한다 (Plan 60 B-7 L-2/L-4 · §15.4 D-035 경계 · 주석 전용).

    semantic_dedup_annotation_enabled(L-2)·topology_text_fusion_enabled(L-4)가 **모두
    False**면 None을 반환한다(주석 경로 미진입·configurable 미주입 → 워커/enricher 비트동일·
    회귀 0). 하나라도 활성이면 로컬 모델 경로로 provider를 생성한다 — 모델 미가용(미설치·경로
    부재·로드 실패)이면 provider가 inert(모든 조회 None)이라 앱을 크래시시키지 않는다(graceful).

    임베딩 출력은 감사·주석 필드에만 실리며 결정적 게이트 판정·억제 지문·상관 군집을 절대
    바꾸지 않는다(§15.4 D-035 불변식). _build_metric_baseline과 동일한 옵트인·graceful 패턴.
    """
    gate = getattr(config, "noise_gate", None)
    if gate is None:
        return None
    l2 = bool(getattr(gate, "semantic_dedup_annotation_enabled", False))
    l4 = bool(getattr(gate, "topology_text_fusion_enabled", False))
    if not (l2 or l4):
        return None
    try:
        from noise_gate.infrastructure.embedding_provider import (
            AlarmEmbeddingProvider,
        )

        return AlarmEmbeddingProvider(
            getattr(gate, "embedding_model_path", ""),
            timeout_seconds=float(
                getattr(gate, "embedding_timeout_seconds", 2.0)
            ),
        )
    except Exception:
        logger.exception("알람 임베딩 provider 생성 실패 — 임베딩 주석 없이 진행")
        return None


def build_decision_store(config: Any) -> Any:
    """발송 판단 감사 저장소를 생성한다 (Plan 52 §8.3).

    enable_noise_gate=False이거나 생성 실패 시 None을 반환한다 —
    감사 적재만 생략되고 발송 판단·발송은 정상 진행된다 (graceful degradation).
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    try:
        from noise_gate.infrastructure.decision_store import DecisionStore

        return DecisionStore(
            config.noise_gate.decision_store_path,
            config.noise_gate.decision_store_enabled,
        )
    except Exception:
        logger.exception("발송 판단 감사 저장소 생성 실패 — 감사 없이 진행")
        return None


def build_investigation_store(config: Any) -> InvestigationStore | None:
    """조사 저장소를 생성한다 (plans/153 W2 · D-325 G-1 (a)).

    게이트 off·조사 트리거 off·생성 실패 시 None — 트리거·후속 태스크의 저장 호출이 전부
    no-op이라 통보 경로가 현행과 비트 동일하고 파일도 만들지 않는다
    (생성자는 파일을 열지 않는다).
    """
    ng = config.noise_gate
    if not ng.enable_noise_gate or not getattr(ng, "investigation_trigger_enabled", False):
        return None
    try:
        from noise_gate.infrastructure.investigation_store import InvestigationStore

        return InvestigationStore(
            getattr(ng, "investigation_store_path", "logs/alarm_investigations.jsonl"),
            int(getattr(ng, "investigation_store_max_lines", 2000)),
        )
    except Exception:
        logger.exception("조사 저장소 생성 실패 — 웹 조회용 조사 기록 없이 진행")
        return None


def build_silence_store(config: Any) -> Any:
    """침묵 규칙 저장소를 생성한다 (Plan 54 모듈 4).

    게이트 off·silence_enabled off·생성 실패 시 None을 반환한다 — 침묵 판정만 생략되고
    나머지 파이프라인은 그대로 돈다(graceful degradation · 기본 off면 회귀 0).
    """
    gate_cfg = config.noise_gate
    if not gate_cfg.enable_noise_gate or not getattr(gate_cfg, "silence_enabled", False):
        return None
    try:
        from noise_gate.infrastructure.silence_store import SilenceStore

        return SilenceStore(
            getattr(gate_cfg, "silence_store_path", "logs/alarm_silences.jsonl"),
            True,
        )
    except Exception:
        logger.exception("침묵 규칙 저장소 생성 실패 — 침묵 없이 진행")
        return None


def build_ticket_queue(config: Any) -> Any:
    """TICKET 티어 일배치 요약 큐를 생성한다 (Plan 52 §7 · Phase E3).

    enable_noise_gate=False이거나 ticket_batch_queue_enabled=False이면 None을 반환한다 —
    큐 적재만 생략되고 발송 판단·발송은 정상 진행된다 (graceful degradation).
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    if not config.noise_gate.ticket_batch_queue_enabled:
        return None
    try:
        from noise_gate.infrastructure.ticket_queue import TicketBatchQueue

        return TicketBatchQueue(
            config.noise_gate.ticket_batch_queue_path,
            config.noise_gate.ticket_batch_queue_enabled,
        )
    except Exception:
        logger.exception("TICKET 일배치 큐 생성 실패 — 큐 없이 진행")
        return None


def build_feedback_store(config: Any) -> Any:
    """운영자 피드백 few-shot 저장소를 생성한다 (Plan 52 Phase E4).

    enable_noise_gate=False이거나 enable_llm_actionability=False이면 None을 반환한다 —
    few-shot 조회만 생략되고(analyzer가 피드백 섹션 없이 진행) 발송은 정상 진행된다
    (graceful degradation·회귀 0).
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    if not getattr(config.noise_gate, "enable_llm_actionability", False):
        return None
    try:
        from noise_gate.infrastructure.feedback_store import FeedbackStore

        return FeedbackStore(
            config.noise_gate.feedback_store_path,
            config.noise_gate.feedback_store_enabled,
            getattr(config.noise_gate, "feedback_store_max_lines", 20000),
        )
    except Exception:
        logger.exception("피드백 저장소 생성 실패 — few-shot 없이 진행")
        return None


def build_sse_publisher(config: Any, redis: Any) -> Any:
    """워커→UI 실시간 SSE Redis pub/sub 발행기를 생성한다 (E3 후속 · D-048.9 해소).

    enable_noise_gate=False·sse_bridge_enabled=False·Redis 클라이언트 부재 시 None을
    반환한다 — 티어 SSE는 로그 폴백(E3 무변경)으로 진행된다 (graceful degradation).
    redis가 설정된 이후(run() 내)에 호출해야 한다.
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    if not getattr(config.noise_gate, "sse_bridge_enabled", False):
        return None
    if redis is None:
        return None
    try:
        from noise_gate.infrastructure.sse_bridge import RedisSseBridgePublisher

        return RedisSseBridgePublisher(
            redis, config.noise_gate.sse_bridge_channel
        )
    except Exception:
        logger.exception("SSE 브리지 발행기 생성 실패 — 로그 폴백으로 진행")
        return None


def build_incident_publisher(config: Any, redis: Any) -> Any:
    """incident 이벤트 Redis pub/sub 발행기를 생성한다 (D-049 · _build_sse_publisher 미러).

    enable_noise_gate=False·incident_tracking_enabled=False·Redis 클라이언트 부재 시
    None을 반환한다 — incident 발행은 스킵되고 발송은 정상 진행된다(회귀 0).
    redis가 설정된 이후(run() 내)에 호출해야 한다.
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    if not getattr(config.noise_gate, "incident_tracking_enabled", False):
        return None
    if redis is None:
        return None
    try:
        from noise_gate.infrastructure.incident_events import RedisIncidentPublisher

        return RedisIncidentPublisher(
            redis, config.noise_gate.incident_event_channel
        )
    except Exception:
        logger.exception("incident 발행기 생성 실패 — incident 계측 없이 진행")
        return None


def build_sre_agent_client(config: Any) -> Any:
    """sre_agent 조사 서비스 MCP 클라이언트를 생성한다 (Plan 64 CW-A · _build_* 미러).

    enable_noise_gate=False·investigation_trigger_enabled=False·생성 실패 시 None을 반환한다 —
    트리거 노드는 클라이언트 미주입 시 no-op(브리핑 미생성)이라 게이트 통보·판정은 정상
    진행된다(회귀 0·graceful). sre_agent 패키지는 import하지 않고 MCP(SSE)로만 통신한다.
    """
    if not config.noise_gate.enable_noise_gate:
        return None
    if not getattr(
        config.noise_gate, "investigation_trigger_enabled", False
    ):
        return None
    try:
        from noise_gate.infrastructure.sre_agent_client import SreAgentClient

        ng = config.noise_gate
        token = getattr(ng, "investigation_service_token", None)
        # SecretStr(.get_secret_value) 또는 평문 문자열(테스트 SimpleNamespace) 모두 수용.
        token_val = (
            token.get_secret_value()  # type: ignore[union-attr]  # 이동 전부터 있던 hasattr 분기
            if hasattr(token, "get_secret_value")
            else (token or "")
        )
        return SreAgentClient(
            server_url=ng.investigation_service_url,
            bearer_token=token_val or None,
            mcp_call_timeout=float(
                getattr(ng, "investigation_mcp_call_timeout_seconds", 10.0)
            ),
        )
    except Exception:
        logger.exception("sre_agent 조사 클라이언트 생성 실패 — 조사 트리거 없이 진행")
        return None


def build_apm_gateway_client(config: Any) -> Any:
    """APM 게이트웨이 MCP 클라이언트를 만든다 (plans/87 J4 · _build_sre_agent_client 미러).

    enable_noise_gate·app_impact_enabled 중 하나라도 off면 None(회귀 0 — 게이트 노드가 조회하지
    않는다). 켰는데 URL이 비었거나 생성에 실패하면 경고를 남기고 None이다(침묵 금지 — 승격 없이
    진행). `apm_gateway` 패키지는 import하지 않고 MCP(SSE)로만 통신한다(D-274 ③).
    """
    ng = getattr(config, "noise_gate", None)
    if ng is None or not getattr(ng, "enable_noise_gate", False):
        return None
    if not getattr(ng, "app_impact_enabled", False):
        return None
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client

    client = build_apm_gateway_client(ng)
    if client is None:
        logger.warning(
            "NOISE_APP_IMPACT_ENABLED=true지만 게이트웨이 클라이언트가 없다"
            "(NOISE_APM_MCP_URL 비었거나 생성 실패) — app_impact 승격 없이 진행"
        )
    else:
        logger.info("app_impact 승격 활성: 게이트웨이=%s", ng.apm_mcp_url)
    return client


def build_annotation_classifier(config: Any) -> Any:
    """운영자 주석 LLM 분류기를 생성한다 (Plan 67 R3-(v) · D-132 · _build_* 미러).

    enable_noise_gate=False 또는 annotation_llm_classification_enabled=False(기본)이면
    None을 반환한다 — 주석 신호는 기존 정규식 추출로 산출되어 현행과 비트동일하다(회귀 0).
    생성 실패도 None으로 흡수한다(정규식 분류로 진행). LLM 인스턴스 자체는 분류기가 첫
    분류 시점에 지연 획득하므로 여기서 과금 호출이 발생하지 않는다.
    """
    gate = getattr(config, "noise_gate", None)
    if gate is None or not getattr(gate, "enable_noise_gate", False):
        return None
    if not getattr(gate, "annotation_llm_classification_enabled", False):
        return None
    try:
        from noise_gate.application.annotation_classifier import (
            AnnotationClassifier,
        )

        return AnnotationClassifier(config)
    except Exception:
        logger.exception("주석 LLM 분류기 생성 실패 — 정규식 주석 분류로 진행")
        return None
