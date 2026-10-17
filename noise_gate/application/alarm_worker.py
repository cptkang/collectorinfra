"""알람 워커.

Redis Stream 'alarm:raw'에서 알람을 소비하여 AlarmAnalysisGraph를 실행한다.

주요 기능:
    - Consumer Group 기반 Redis Stream 소비 (XREADGROUP)
    - 중복 알람 제거 (alarm_id TTL 기반 in-memory dedup)
    - 심각도 임계값 필터링 (min_severity 미만 무시)
    - AlarmAnalysisGraph ainvoke → LLM 분석 → 채널 발송
    - 처리 완료 후 XACK

ALARM_ENABLED=false이면 즉시 반환한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

import redis.asyncio as aioredis

if TYPE_CHECKING:
    from noise_gate.domain.topology import DependencyGraph
    from noise_gate.infrastructure.investigation_store import InvestigationStore

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.annotation_signal import (
    AnnotationSignal,
    extract_annotation_signal,
)
from noise_gate.domain.correlation import ClusterState
from noise_gate.domain.cross_source import EpisodeTracker
from noise_gate.domain.page_review import PageRegistry, fanout_key_of, host_key_of
from noise_gate.domain.notification_policy import (
    STAGE_FLAPPING,
    STAGE_INHIBITION,
    STAGE_SELF_HEAL,
    STAGE_STORM,
    compute_fingerprint,
)
from noise_gate.application import worker_wiring
from noise_gate.application.dedup_service import DedupService
# (plans/177 W3) dedup 서비스로 옮긴 상수의 같은 이름 재노출(D-337 ①) — 테스트가 이 경로로
# import한다.
from noise_gate.application.dedup_service import _RECENT_TEXTS_MAX_PER_SCOPE  # noqa: F401
from noise_gate.application.episode_coordinator import EpisodeCoordinator
from noise_gate.application.server_identity import (
    attach_server_identity,
    is_apm_db_id,
    is_apm_source,
)
from noise_gate.application.signal_detection import SignalDetector
from noise_gate.domain.severity import coerce_severity
from noise_gate.infrastructure.apm_noise_context import (
    EVENT_KIND_METRIC,
    ApmNoiseContext,
    ApmPolicyMatch,
    event_kind,
)
from noise_gate.infrastructure.redis_queue import (
    ack_message,
    dead_letter_message,
    ensure_consumer_group,
    read_messages,
)
from noise_gate.application.nodes.alarm_notifier import (
    send_late_promotion,
    spawn_episode_investigation_followup,
)
from noise_gate.application.nodes.investigation_trigger import submit_episode_investigation
from noise_gate.orchestration.alarm_graph import build_alarm_graph
from noise_gate.orchestration.ticket_summary import run_ticket_summary_loop

logger = logging.getLogger(__name__)

_CONSUMER_NAME = "worker-1"

# 위상 가중 그래프 음성 캐시 TTL `_TOPOLOGY_NEGATIVE_TTL_SECONDS`는 signal_detection이, 스코프별
# 최근 이벤트 텍스트 상한 `_RECENT_TEXTS_MAX_PER_SCOPE`는 dedup_service가 정본이다(plans/177 W3).

# (plans/144 §4.2) 제니퍼 지속 조건 카운터·재판정 보류·보낸 심각도의 지문 키 상한 — 넘치면
# 가장 오래된 지문부터 버린다(경고 로그). 창 밖 타임스탬프 정리·키 만료 sweep과 병행하는
# 메모리 가드다.
_APM_PERSIST_MAX_KEYS = 10000

# 지문 dedup 레코드의 마지막 통보 심각도 키 `_DEDUP_SEVERITY_KEY`와 순수 함수
# `_dedup_severity_rises`·`_dedup_meta`는 dedup_service가 정본이다(plans/177 W3).

# (plans/144 W4 §5.6 · G-6) 사후 승격 후보(원 판정 결과 보관) 상한 — 넘치면 가장 오래된 것부터
# 버린다(경고 로그). 사건 종료·멤버 해소 시 정리하는 sweep과 병행하는 메모리 가드다.
_LATE_PROMOTION_MAX = 2000
# 트리거 1건이 순차 발송하는 사후 승격 상한 — 대상 N건 × workb 타임아웃만큼 처리 루프가 밀리지
# 않게 한다. 넘친 대상은 표시하지 않고 후보로 남겨 같은 사건의 다음 심각 이벤트에 기회를 준다.
_LATE_PROMOTION_PER_TRIGGER_MAX = 5
# 후보 전수 sweep 최소 간격 `_LATE_PROMOTION_SWEEP_SECONDS`는 episode_coordinator가 정본이다
# (plans/177 W2).

# (plans/153 W4 §4.5) 종합 조사 대표 알람 후보(멤버의 분석 결과·판정 보관) 상한 — 넘치면 닫힌 사건의
# 것부터, 그래도 넘치면 가장 오래된 것부터 버린다. 종합 조사 제출 뒤에는 그 사건 몫을 지운다.
_EPISODE_CTX_MAX = 2000
# 후속 알람을 묶지 않는 종합 조사 저장 상태 `_EPISODE_INV_UNUSABLE`도 episode_coordinator가
# 정본이다.


def _evict_oldest(store: dict[str, Any], last_ts: Callable[[Any], float], label: str) -> None:
    """지문 dict가 `_APM_PERSIST_MAX_KEYS`를 넘으면 `last_ts`가 가장 이른 키부터 지운다(경고)."""
    overflow = len(store) - _APM_PERSIST_MAX_KEYS
    if overflow <= 0:
        return
    for k in sorted(store, key=lambda k: last_ts(store[k]))[:overflow]:
        del store[k]
    logger.warning(
        "제니퍼 %s 상한(%d) 초과 — 오래된 지문 %d건 정리", label, _APM_PERSIST_MAX_KEYS, overflow
    )


class _WorkerEpisodeHost:
    """`EpisodeHost` 구현 — 워커 속성과 이 모듈 전역을 **접근 시점**에 읽는다(plans/177 W2).

    테스트·하네스가 생성 뒤 워커 속성(`w._episodes`·`w._decision_store` 등)과 모듈 전역
    (`_LATE_PROMOTION_MAX`·`send_late_promotion` 등)을 바꿔 끼우므로 값을 붙잡지 않는다.
    """

    __slots__ = ("_w",)

    def __init__(self, worker: AlarmWorker) -> None:
        self._w = worker

    @property
    def episodes(self) -> EpisodeTracker | None:
        return self._w._episodes

    @property
    def episode_ctx(self) -> dict[str, tuple[str, Any, Any, float]]:
        return self._w._episode_ctx

    @property
    def late_promotion(self) -> dict[str, tuple[str, Any, Any, float]]:
        return self._w._late_promotion

    @property
    def late_promotion_swept_at(self) -> float:
        return self._w._late_promotion_swept_at

    @late_promotion_swept_at.setter
    def late_promotion_swept_at(self, value: float) -> None:
        self._w._late_promotion_swept_at = value

    @property
    def config(self) -> Any:
        return self._w._config

    @property
    def decision_store(self) -> Any:
        return self._w._decision_store

    @property
    def investigation_store(self) -> Any:
        return self._w._investigation_store

    @property
    def sre_agent_client(self) -> Any:
        return self._w._sre_agent_client

    @property
    def incident_publisher(self) -> Any:
        return self._w._incident_publisher

    @property
    def late_promotion_max(self) -> int:
        return _LATE_PROMOTION_MAX

    @property
    def late_promotion_per_trigger_max(self) -> int:
        return _LATE_PROMOTION_PER_TRIGGER_MAX

    @property
    def episode_ctx_max(self) -> int:
        return _EPISODE_CTX_MAX

    @property
    def send_late_promotion(self) -> Callable[..., Awaitable[dict[str, bool]]]:
        return send_late_promotion

    @property
    def submit_episode_investigation(self) -> Callable[..., Awaitable[dict[str, Any]]]:
        return submit_episode_investigation

    @property
    def spawn_episode_investigation_followup(self) -> Callable[..., Any]:
        return spawn_episode_investigation_followup


def _episode_coordinator(worker: AlarmWorker) -> EpisodeCoordinator:
    """호출마다 만드는 사건 코디네이터 — 상태는 워커가 소유하므로 만드는 비용만 든다(plans/177 W2).

    워커에 인스턴스 속성·메서드를 더하지 않는다 — `__init__`을 거치지 않은 인스턴스(테스트의
    `object.__new__`)도 그대로 위임되게 하고, 클래스 표면(메서드 목록)을 바꾸지 않기 위해서다.
    """
    return EpisodeCoordinator(_WorkerEpisodeHost(worker))


class _WorkerSignalHost:
    """`SignalHost` 구현 — 워커 탐지 상태·근거를 **접근 시점**에 읽고 쓴다(plans/177 W3).

    테스트·하네스가 생성 뒤 워커 속성(`w._config`·`w._correlation_clusters` 등)을 바꿔 끼우므로
    값을 붙잡지 않는다. 직전 탐지 근거는 `_process`가 워커 속성으로 읽으므로 setter로 되쓴다.
    """

    __slots__ = ("_w",)

    def __init__(self, worker: AlarmWorker) -> None:
        self._w = worker

    @property
    def config(self) -> Any:
        return self._w._config

    @property
    def apm_noise_ctx(self) -> ApmNoiseContext | None:
        return self._w._apm_noise_ctx

    @property
    def firing_registry(self) -> dict[str, tuple[float, int]]:
        return self._w._firing_registry

    @property
    def active_firings(self) -> dict[str, tuple[int, float, str]]:
        return self._w._active_firings

    @property
    def flap_states(self) -> dict[str, deque[bool]]:
        return self._w._flap_states

    @property
    def flap_flag(self) -> dict[str, bool]:
        return self._w._flap_flag

    @property
    def flap_last_seen(self) -> dict[str, float]:
        return self._w._flap_last_seen

    @property
    def storm_window(self) -> dict[str, deque[float]]:
        return self._w._storm_window

    @property
    def correlation_clusters(self) -> dict[str, list[ClusterState]]:
        return self._w._correlation_clusters

    @property
    def topology_graph_cache(self) -> dict[str, tuple[float, DependencyGraph | None]]:
        return self._w._topology_graph_cache

    @property
    def last_self_heal_duration(self) -> float | None:
        return self._w._last_self_heal_duration

    @last_self_heal_duration.setter
    def last_self_heal_duration(self, value: float | None) -> None:
        self._w._last_self_heal_duration = value

    @property
    def last_self_heal_evidence(self) -> dict[str, Any] | None:
        return self._w._last_self_heal_evidence

    @last_self_heal_evidence.setter
    def last_self_heal_evidence(self, value: dict[str, Any] | None) -> None:
        self._w._last_self_heal_evidence = value

    @property
    def last_inhibition_evidence(self) -> dict[str, Any] | None:
        return self._w._last_inhibition_evidence

    @last_inhibition_evidence.setter
    def last_inhibition_evidence(self, value: dict[str, Any] | None) -> None:
        self._w._last_inhibition_evidence = value

    @property
    def last_flapping_evidence(self) -> dict[str, Any] | None:
        return self._w._last_flapping_evidence

    @last_flapping_evidence.setter
    def last_flapping_evidence(self, value: dict[str, Any] | None) -> None:
        self._w._last_flapping_evidence = value

    @property
    def last_storm_evidence(self) -> dict[str, Any] | None:
        return self._w._last_storm_evidence

    @last_storm_evidence.setter
    def last_storm_evidence(self, value: dict[str, Any] | None) -> None:
        self._w._last_storm_evidence = value


def _signal_detector(worker: AlarmWorker) -> SignalDetector:
    """호출마다 만드는 신호 탐지기 — 상태는 워커가 소유한다(plans/177 W3 · W2와 같은 방식)."""
    return SignalDetector(_WorkerSignalHost(worker))


class _WorkerDedupHost:
    """`DedupHost` 구현 — 워커 dedup 상태·협력자와 모듈 전역을 **접근 시점**에 읽는다(plans/177 W3).

    테스트가 생성 뒤 워커 속성(`w._gate_dedup`·`w._embedding_provider` 등)과 모듈 전역
    (`_APM_PERSIST_MAX_KEYS` — `_evict_oldest`가 호출 시점에 읽는다)을 바꿔 끼우므로 값을
    붙잡지 않는다.
    """

    __slots__ = ("_w",)

    def __init__(self, worker: AlarmWorker) -> None:
        self._w = worker

    @property
    def config(self) -> Any:
        return self._w._config

    @property
    def gate_dedup(self) -> dict[str, dict[str, Any]]:
        return self._w._gate_dedup

    @property
    def decision_store(self) -> Any:
        return self._w._decision_store

    @property
    def embedding_provider(self) -> Any:
        return self._w._embedding_provider

    @property
    def recent_event_texts(self) -> dict[str, deque[tuple[str, str, float]]]:
        return self._w._recent_event_texts

    @property
    def apm_persist(self) -> dict[str, tuple[int, deque[float]]]:
        return self._w._apm_persist

    @property
    def apm_persist_pending(self) -> dict[str, float]:
        return self._w._apm_persist_pending

    @property
    def apm_sent_severity(self) -> dict[str, tuple[int, float]]:
        return self._w._apm_sent_severity

    @property
    def evict_oldest(self) -> Callable[[dict[str, Any], Callable[[Any], float], str], None]:
        return _evict_oldest


def _dedup_service(worker: AlarmWorker) -> DedupService:
    """호출마다 만드는 dedup 서비스 — 상태는 워커가 소유한다(plans/177 W3 · W2와 같은 방식)."""
    return DedupService(_WorkerDedupHost(worker))


class AlarmWorker:
    """Redis Stream에서 알람을 소비하여 분석 그래프를 실행한다."""

    def __init__(self, config) -> None:  # noqa: ANN001
        """AlarmWorker를 초기화한다.

        Args:
            config: AppConfig 인스턴스
        """
        self._config = config
        self._graph = None
        self._history_repo = None
        self._process_client = None
        self._redis = None
        # ── Plan 52: 노이즈 게이트 (enable_noise_gate 활성 시에만 사용) ──
        self._noise_repo = None
        # (Plan 60 E3) 동적 baseline 이상탐지 어댑터 — dynamic_baseline_enabled 시에만 생성.
        self._metric_baseline = None
        self._decision_store = None
        # (plans/153 W2) 조사 저장소 — 조사 트리거가 켜졌을 때만 생성(off면 None·파일 생성 0).
        self._investigation_store: InvestigationStore | None = None
        # (Plan 54 모듈 4) 침묵 규칙 저장소 — silence_enabled off(기본)면 None(조회 자체 없음).
        self._silence_store = None
        # 활성 규칙 캐시 (만료 epoch, 규칙 목록) — 알람 1건마다 파일을 다시 읽지 않게 한다.
        self._silence_cache: tuple[float, list] = (0.0, [])
        self._ticket_queue = None  # (E3) TICKET 티어 일배치 요약 큐
        # (결함 ⑬) 큐를 읽어 요약을 보내고 보존 한도를 지키는 주기 루프 — 큐가 있을 때만 뜬다.
        self._ticket_summary_task: asyncio.Task | None = None
        self._feedback_store = None  # (E4) 운영자 피드백 few-shot 저장소
        self._sse_publisher = None  # (E3 후속) 워커→UI 실시간 SSE Redis pub/sub 발행기
        self._incident_publisher = None  # (D-049) incident 이벤트 Redis pub/sub 발행기
        self._sre_agent_client = None  # (Plan 64 CW-A) sre_agent 조사 서비스 MCP 클라이언트
        self._apm_client = None  # (plans/87 J4) APM 게이트웨이 MCP 클라이언트 — app_impact 승격용
        self._identity_resolver = None  # (D-188) hostname → 등록 서버명·IP 역조회 리졸버
        # (Plan 67 R3-(v) · D-132) 운영자 주석 LLM 분류기 — annotation_llm_classification_enabled
        # 활성 시에만 생성. None이면 주석 신호는 기존 정규식 추출로 산출된다(비트동일·회귀 0).
        self._annotation_classifier = None
        # (D-049) 직전 self-heal 매칭 소요시간(초) — _update_firing_registry가 설정,
        # _process가 decision_store.record_resolution 기록에 사용(매칭 없으면 None).
        self._last_self_heal_duration: Optional[float] = None
        # (plans/112 S6) 탐지 함수가 **직전 호출에서 True를 낸 근거** — 반환형(bool)을 바꾸지 않고
        # 옆에 남긴다. 각 탐지 함수가 호출 시작에 None으로 리셋하므로 이전 알람 값이 새지 않는다.
        # _process가 탐지가 True였던 단계만 골라 그래프 입력 `detection_evidence`로 싣는다.
        self._last_self_heal_evidence: dict[str, Any] | None = None
        self._last_inhibition_evidence: dict[str, Any] | None = None
        self._last_flapping_evidence: dict[str, Any] | None = None
        self._last_storm_evidence: dict[str, Any] | None = None
        # 핑거프린트 dedup(재발생 억제, §6.1 · Plan 60 E1) — alarm_id dedup과 별개 경로.
        # 레코드 = {first_seen, last_notified, last_seen, count}.
        #   판정 필드 last_notified = 마지막 *통보* 시각(중복 판정 시 갱신 안 함 → 고정창).
        #   정리 필드 last_seen     = 마지막 *목격* 시각(중복마다 갱신 → 만료 sweep 기준).
        # 둘의 의미가 다름에 주의(§3.3): last_seen으로 TTL 비교하면 슬라이딩 창 회귀 발생.
        # (plans/144 Q-4) dedup_severity_rise_bypass가 켜져 있으면 레코드에 마지막 통보 심각도
        # (_DEDUP_SEVERITY_KEY)를 함께 둔다 — 반환 메타(recurrence)에는 싣지 않는다.
        self._gate_dedup: dict[str, dict] = {}
        # 자가복구 상관용 발생 레지스트리(§3.7): fingerprint → (발생시각, severity).
        self._firing_registry: dict[str, tuple[float, int]] = {}
        # 인히비션(§3.4·E2): 스코프(db_id|server) → (최고심각도, 발생시각, 알람키).
        # 스코프별 현재 활성 최고 심각도 인히비터를 유지(self-inhibition 방지용 알람키 동반).
        self._active_firings: dict[str, tuple[int, float, str]] = {}
        # 플래핑(§3.7·E2): 핑거프린트 → 최근 상태(firing 여부) 시퀀스(maxlen=MAX_STATES).
        self._flap_states: dict[str, deque] = {}
        # 플래핑(§3.7·E2): 핑거프린트 → 직전 플래핑 상태(히스테리시스 유지용).
        self._flap_flag: dict[str, bool] = {}
        # 플래핑(§3.7·E2): 핑거프린트 → 마지막 갱신 시각(만료 키 정리용 — 메모리 일관성).
        self._flap_last_seen: dict[str, float] = {}
        # 스톰(§3.8·E2): 스코프(db_id|server) → 사건창 발생 타임스탬프 deque.
        self._storm_window: dict[str, deque] = {}
        # 크로스-호스트 상관(Plan 60 E2·§4): db_id(존) 스코프 → 활성 클러스터 리스트.
        # 존 경계는 넘지 않는다(§8 B-6). 만료 sweep(last_ts window 밖 제거·빈 스코프 키 삭제)
        # + 버퍼 상한(correlation_buffer_max)으로 메모리 가드(§10, 형제 상태 정리 루프와 일관).
        self._correlation_clusters: dict[str, list[ClusterState]] = {}
        # 위상 가중 그래프 캐시(Plan 60 E2 위상 가중): db_id → (만료 epoch, DependencyGraph|None).
        # correlation_topology_weight_enabled일 때만 사용. hot-path 클라이언트 open을 회피하기 위해
        # cold/만료 시에만 로드하고, 실패(None)도 짧은 음성 TTL로 캐시해 재시도를 허용한다(폭주 방지).
        self._topology_graph_cache: dict[str, tuple[float, Optional[DependencyGraph]]] = {}
        # (Plan 60 B-7 L-2 · §15.4 D-035 경계) 의미적 근접중복 **후보 주석**용 로컬 임베딩 provider.
        # semantic_dedup_annotation_enabled·topology_text_fusion_enabled 모두 off면 None(주석 경로
        # 미진입·configurable 미주입 → 워커/enricher 비트동일·회귀 0). 주석은 감사 전용이며
        # 결정적 게이트 판정(SUPPRESS/PAGE)·억제 지문(compute_fingerprint)을 절대 바꾸지 않는다.
        self._embedding_provider = None
        # (Plan 60 B-7 L-2) db_id 스코프 → 최근 (fingerprint, text, ts) deque. 신규(비-fingerprint-
        # dup) 이벤트가 이전의 **다른 지문·유사 텍스트** 이벤트와 근접중복인지 판단할 후보 풀이다.
        # 상한(maxlen=_RECENT_TEXTS_MAX_PER_SCOPE)·만료 sweep(repeat_interval_seconds 밖 popleft·빈
        # 스코프 키 삭제)으로 메모리 가드. provider 미주입(off/inert)이면 항상 비어 회귀 0.
        self._recent_event_texts: dict[str, deque] = {}
        # (plans/144 §4.1) 제니퍼 유형 정책 공급자 — apm_noise_policy_enabled(+게이트) 시에만
        # 기동 시 1회 생성. None이면 제니퍼 알람도 현행 경로(지문 원문 · enricher 미주입 →
        # unavailable PAGE).
        self._apm_noise_ctx: ApmNoiseContext | None = None
        # (plans/144 §4.2) 지속 조건 카운터: 지문 → (창 초, 창 안 발생 타임스탬프 deque).
        # 창 밖 popleft + 마지막 발생이 창 밖인 키 만료 sweep + 키 상한(_APM_PERSIST_MAX_KEYS).
        self._apm_persist: dict[str, tuple[int, deque[float]]] = {}
        # (plans/144 §4.2) 지속 조건 미달로 그래프에 보낸(=강등 판정 대상) 심각도2 지문 → 그 시각.
        # 지문 dedup이 같은 지문의 다음 이벤트를 repeat_interval 동안 버리므로, 창 안 반복으로
        # 조건이 채워지는 순간 1회만 dedup을 건너뛰어 매트릭스대로 다시 판정한다(지연 0 · 첫
        # 이벤트를 붙잡지 않는다). 실제로 그래프에 가는(dedup 비중복) 미달 이벤트만 세우므로
        # 재판정은 dedup 창마다 최대 1회다. 해소 이벤트가 오면 그 지문의 카운터와 함께 지운다.
        # repeat_interval_seconds 밖 항목은 sweep하고 키 상한을 둔다.
        self._apm_persist_pending: dict[str, float] = {}
        # (plans/144 §4.2) 정책 경로에서 마지막으로 게이트에 보낸 심각도: 지문 → (심각도, 시각).
        # 강등된 sev2 뒤 같은 지문의 상위 심각도(sev3)가 지문 dedup에 버려지지 않게 한다.
        # 폴스타·플래그 off는 기록하지 않는다(dedup 비트 동일). sweep·키 상한은 위와 같다.
        self._apm_sent_severity: dict[str, tuple[int, float]] = {}
        # (plans/144 W3) 크로스소스 사건 추적기 — cross_source_mode≠off(+게이트)이고 규칙 표가
        # 적재됐을 때만 기동 시 1회 생성. None이면 사건 추적·state 키 모두 없다(비트 동일).
        # 상태는 워커 메모리(G-8 (a) — 재기동 시 초기화). idle 종료·만료 sweep·사건 수 상한은
        # 추적기가 한다.
        self._episodes: EpisodeTracker | None = None
        # (plans/144 W4 §5.6 · G-6) 사후 승격 후보: 폴스타 alarm_id →
        # (사건 id, 원 분석 결과, 원 판정, 보관 시각). app_impact_late_promotion_enabled +
        # app_impact_enabled + 추적기가 있을 때만 매트릭스 DASHBOARD·TICKET 판정을 담는다.
        # 사건 종료·멤버 해소·승격 완료 시 sweep하고 상한(_LATE_PROMOTION_MAX)을 둔다.
        self._late_promotion: dict[str, tuple[str, Any, Any, float]] = {}
        self._late_promotion_swept_at = 0.0
        # (plans/153 W4 §4.5) 종합 조사 대표 알람 후보: alarm_id → (사건 id, 분석 결과, 판정,
        # 보관 시각). investigation_episode_context_enabled + 조사 트리거 + 추적기일 때만 담는다
        # (사건 상태 영속화 없음 — G-8). 상한 _EPISODE_CTX_MAX.
        self._episode_ctx: dict[str, tuple[str, Any, Any, float]] = {}
        # (plans/162 · D-330) PAGE 필요성 재판정 레지스트리 — page_review_mode≠off(+게이트)일 때만
        # 기동 시 1회 생성하고 판정 기록 꼬리로 복원한다. None이면 재판정 신호·state 키 모두 없다
        # (비트 동일). 창 밖 sweep·키 상한은 레지스트리가 한다.
        self._page_registry: PageRegistry | None = None

    def _build_history_repo(self):  # noqa: ANN202
        """`worker_wiring.build_history_repo` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_history_repo(self._config)

    def _build_identity_resolver(self):  # noqa: ANN202
        """`worker_wiring.build_identity_resolver` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_identity_resolver(self._config)

    def _build_process_client(self):  # noqa: ANN202
        """`worker_wiring.build_process_client` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_process_client(self._config)

    def _build_noise_repo(self):  # noqa: ANN202
        """`worker_wiring.build_noise_repo` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_noise_repo(self._config)

    def _build_apm_noise_ctx(self) -> ApmNoiseContext | None:
        """`worker_wiring.build_apm_noise_ctx` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_apm_noise_ctx(self._config)

    def _build_episode_tracker(self) -> EpisodeTracker | None:
        """`worker_wiring.build_episode_tracker` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_episode_tracker(self._config)

    def _build_page_registry(self) -> PageRegistry | None:
        """`worker_wiring.build_page_registry` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_page_registry(self._config)

    def _restore_page_registry(self, now: float) -> None:
        """판정 기록 꼬리(최근 max(reminder_hours, 24)시간)로 레지스트리를 다시 채운다.

        재기동 직후 진행 중 사건이 일괄 재PAGE되는 것을 막는다(plans/162 H2). 실패하면 빈
        레지스트리로 시작하고 경고 1줄을 남긴다(보수 = 현행 동작).
        """
        registry = self._page_registry
        if registry is None:
            return
        store = self._decision_store
        reader = getattr(store, "recent_decisions", None)
        if reader is None:
            logger.warning("PAGE 재판정 복원 생략 — 판정 기록 저장소 없음(빈 상태로 시작)")
            return
        if not getattr(store, "enabled", True):
            logger.warning("PAGE 재판정 복원 생략 — 판정 기록 비활성(빈 상태로 시작)")
            return
        hours = int(getattr(self._config.noise_gate, "page_review_reminder_hours", 24))
        idle = int(getattr(self._config.noise_gate, "page_review_idle_close_minutes", 0))
        try:
            window = PageRegistry.restore_window_seconds(hours)
            # (plans/162 F4) 무소식 닫힘이 켜져 있을 때만 지문 중복 재발 레코드도 읽는다.
            records = reader(window, include_recurrence=True) if idle > 0 else reader(window)
            applied = registry.restore(
                records, now,
                silent_source=lambda rec: is_apm_db_id(str(rec.get("db_id") or "")),
            )
        except Exception as exc:  # noqa: BLE001 — 복원 실패가 워커 기동을 막지 않는다
            self._page_registry = self._build_page_registry()
            logger.warning("PAGE 재판정 복원 실패 — 빈 상태로 시작: %s", exc)
            return
        logger.info("PAGE 재판정 복원: 레코드 %d건 · 대표 지문 %d건", applied, len(registry))

    def _observe_page(
        self, event: AlarmEvent, fingerprint: str, result: Any, now: float
    ) -> None:
        """그래프가 낸 최종 판정을 PAGE 레지스트리에 반영한다(해소 · 대표 PAGE · 강등 반복 수)."""
        if self._page_registry is None:
            return
        decision = result.get("notification_decision") if isinstance(result, dict) else None
        if decision is None:
            return
        self._page_registry.observe(
            decision,
            fingerprint=str(getattr(decision, "fingerprint", "") or fingerprint),
            host_key=host_key_of(event.db_id, event.server_name or event.hostname),
            fanout_key=fanout_key_of(event.db_id, event.alarm_name),
            alarm_id=event.alarm_id,
            now=now,
            silent_source=is_apm_source(event),
        )

    def _observe_episode(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> dict[str, Any] | None:
        """`EpisodeCoordinator.observe_episode` 위임 — 테스트·`_process` seam(plans/177)."""
        return _episode_coordinator(self).observe_episode(event, fingerprint, now)

    def _record_episode_tier(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any
    ) -> None:
        """`EpisodeCoordinator.record_episode_tier` 위임 — 테스트·`_process` seam(plans/177)."""
        _episode_coordinator(self).record_episode_tier(cross_source, event, result)

    def _episode_app_impact(
        self, cross_source: dict[str, Any] | None, event: AlarmEvent
    ) -> dict[str, Any] | None:
        """`EpisodeCoordinator.episode_app_impact` 위임 — 테스트·`_process` seam(plans/177)."""
        return _episode_coordinator(self).episode_app_impact(cross_source, event)

    def _episode_summary(self, cross_source: dict[str, Any] | None) -> dict[str, Any] | None:
        """`EpisodeCoordinator.episode_summary` 위임 — 테스트·`_process` seam(plans/177)."""
        return _episode_coordinator(self).episode_summary(cross_source)

    def _episode_investigation(
        self, cross_source: dict[str, Any] | None, event: AlarmEvent
    ) -> dict[str, Any] | None:
        """`EpisodeCoordinator.episode_investigation` 위임 — 테스트·`_process` seam(plans/177)."""
        return _episode_coordinator(self).episode_investigation(cross_source, event)

    def _episode_snapshot(self, cross_source: dict[str, Any] | None) -> dict[str, Any] | None:
        """`EpisodeCoordinator.episode_snapshot` 위임 — 테스트·`_process` seam(plans/177)."""
        return _episode_coordinator(self).episode_snapshot(cross_source)

    def _remember_episode_ctx(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any, now: float
    ) -> None:
        """`EpisodeCoordinator.remember_episode_ctx` 위임 — 테스트·`_process` seam(plans/177)."""
        _episode_coordinator(self).remember_episode_ctx(cross_source, event, result, now)

    async def _after_episode_graph(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any, now: float
    ) -> None:
        """`EpisodeCoordinator.after_episode_graph` 위임 — 테스트·`_process` seam(plans/177)."""
        await _episode_coordinator(self).after_episode_graph(cross_source, event, result, now)

    def _build_metric_baseline(self):  # noqa: ANN202
        """`worker_wiring.build_metric_baseline` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_metric_baseline(self._config)

    def _build_embedding_provider(self):  # noqa: ANN202
        """`worker_wiring.build_embedding_provider` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_embedding_provider(self._config)

    def _build_decision_store(self):  # noqa: ANN202
        """`worker_wiring.build_decision_store` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_decision_store(self._config)

    def _build_investigation_store(self) -> InvestigationStore | None:
        """`worker_wiring.build_investigation_store` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_investigation_store(self._config)

    def _active_silence_rules(self, now: float) -> list:
        """활성 침묵 규칙을 TTL 캐시로 돌려준다 (Plan 54 모듈 4).

        저장소가 없으면(플래그 off·생성 실패) 빈 목록이다 — 게이트는 규칙이 없으면 침묵
        단계를 평가하지 않으므로 판정이 현행과 비트 동일하다. 알람 1건마다 파일을 다시 읽으면
        hot-path가 느려지므로 짧은 TTL로 캐시한다(운영자가 규칙을 건 뒤 TTL 이내에 반영).

        Args:
            now: 현재 epoch 초.

        Returns:
            활성 SilenceRule 목록(조회 실패 시 빈 목록).
        """
        if self._silence_store is None:
            return []
        expires_at, cached = self._silence_cache
        if now < expires_at:
            return cached
        try:
            rules = self._silence_store.active_rules()
        except Exception:  # noqa: BLE001 — 조회 실패가 알람 처리를 막지 않는다
            logger.warning("침묵 규칙 조회 실패(무시) — 침묵 없이 진행")
            rules = []
        ttl = float(getattr(self._config.noise_gate, "silence_cache_ttl_seconds", 10) or 10)
        self._silence_cache = (now + ttl, rules)
        return rules

    def _build_silence_store(self):  # noqa: ANN202
        """`worker_wiring.build_silence_store` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_silence_store(self._config)

    def _build_ticket_queue(self):  # noqa: ANN202
        """`worker_wiring.build_ticket_queue` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_ticket_queue(self._config)

    def _build_feedback_store(self):  # noqa: ANN202
        """`worker_wiring.build_feedback_store` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_feedback_store(self._config)

    def _build_sse_publisher(self):  # noqa: ANN202
        """`worker_wiring.build_sse_publisher` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_sse_publisher(self._config, self._redis)

    def _build_incident_publisher(self):  # noqa: ANN202
        """`worker_wiring.build_incident_publisher` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_incident_publisher(self._config, self._redis)

    def _build_sre_agent_client(self):  # noqa: ANN202
        """`worker_wiring.build_sre_agent_client` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_sre_agent_client(self._config)

    def _build_apm_gateway_client(self):  # noqa: ANN202
        """`worker_wiring.build_apm_gateway_client` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_apm_gateway_client(self._config)

    def _build_annotation_classifier(self):  # noqa: ANN202
        """`worker_wiring.build_annotation_classifier` 위임 — 테스트·`run()` seam(plans/177)."""
        return worker_wiring.build_annotation_classifier(self._config)

    async def _annotation_signal(self, event: AlarmEvent) -> AnnotationSignal:
        """이벤트 텍스트에서 주석 신호(계획작업/해소/운영자인지)를 산출한다 (D-132).

        분류기 미주입(플래그 OFF·생성 실패)이면 기존 결정적 정규식 추출을 그대로 호출한다.
        분류기가 있으면 LLM 분류를 쓰고, 분류기 내부에서 타임아웃·실패 시 정규식으로 강등한다
        (사유 로그) — 이 메서드는 어느 경로에서도 예외를 올리지 않는다.
        """
        text = (
            getattr(event, "condition_log", "")
            or getattr(event, "description", "")
            or ""
        )
        if self._annotation_classifier is None:
            return extract_annotation_signal(text)
        return await self._annotation_classifier.classify(text)

    async def run(self) -> None:
        """알람 소비 루프를 실행한다.

        ALARM_ENABLED=false이면 즉시 반환한다.
        asyncio.CancelledError를 받으면 Redis 연결을 닫고 종료한다.
        """
        if not self._config.alarm.enabled:
            logger.info("알람 워커 비활성 (ALARM_ENABLED=false)")
            return

        r = aioredis.from_url(
            f"redis://{self._config.redis.host}:{self._config.redis.port}",
            password=self._config.redis.password or None,
            db=self._config.redis.db,
        )
        stream_key = self._config.alarm.redis_stream_key
        group = self._config.alarm.redis_consumer_group

        await ensure_consumer_group(r, stream_key, group)
        self._graph = build_alarm_graph(self._config)
        self._history_repo = self._build_history_repo()
        self._identity_resolver = self._build_identity_resolver()
        self._process_client = self._build_process_client()
        self._noise_repo = self._build_noise_repo()
        self._apm_noise_ctx = self._build_apm_noise_ctx()
        self._episodes = self._build_episode_tracker()
        self._metric_baseline = self._build_metric_baseline()
        # (Plan 60 B-7 L-2/L-4) 로컬 임베딩 provider — 두 주석 플래그 모두 off면 None(회귀 0).
        self._embedding_provider = self._build_embedding_provider()
        self._decision_store = self._build_decision_store()
        # (plans/162) PAGE 재판정 레지스트리 — off(기본)면 None(복원·신호 모두 없음).
        self._page_registry = self._build_page_registry()
        self._restore_page_registry(time.time())
        self._investigation_store = self._build_investigation_store()
        self._silence_store = self._build_silence_store()
        self._ticket_queue = self._build_ticket_queue()
        self._feedback_store = self._build_feedback_store()
        self._redis = r
        self._sse_publisher = self._build_sse_publisher()
        self._incident_publisher = self._build_incident_publisher()
        # (Plan 64 CW-A) sre_agent 조사 서비스 클라이언트 — off/미배선이면 None(회귀 0).
        self._sre_agent_client = self._build_sre_agent_client()
        # (plans/87 J4) APM 게이트웨이 클라이언트 — app_impact off(기본)면 None(회귀 0).
        self._apm_client = self._build_apm_gateway_client()
        # (Plan 67 R3-(v) · D-132) 주석 LLM 분류기 — 플래그 off(기본)면 None → 정규식 경로.
        self._annotation_classifier = self._build_annotation_classifier()
        dedup: dict[str, float] = {}

        logger.info(
            "알람 워커 시작 (stream=%s group=%s min_severity=%s)",
            stream_key,
            group,
            self._config.alarm.min_severity,
        )
        # (결함 ⑬) TICKET 일배치 요약·보존 정리 루프 — 큐가 없으면(게이트·큐 off) 띄우지 않는다.
        if self._ticket_queue is not None:
            self._ticket_summary_task = asyncio.create_task(
                run_ticket_summary_loop(self._ticket_queue, self._config)
            )

        try:
            while True:
                try:
                    messages = await read_messages(
                        r, stream_key, group, _CONSUMER_NAME,
                        count=10, block_ms=2000,
                    )
                except asyncio.CancelledError:
                    break
                for msg_id, fields in messages:
                    await self._process(r, stream_key, group, msg_id, fields, dedup)
        finally:
            if self._ticket_summary_task is not None:
                self._ticket_summary_task.cancel()
            await r.aclose()
            logger.info("알람 워커 종료")

    async def _process(
        self,
        r: aioredis.Redis,
        stream_key: str,
        group: str,
        msg_id: bytes,
        fields: dict,
        dedup: dict[str, float],
    ) -> None:
        """단일 알람 메시지를 처리한다.

        파싱 → 중복 제거 → 심각도 필터 → 그래프 실행 → ACK 순서로 진행한다.
        예외 발생 시에도 ACK하여 메시지가 무한 재처리되지 않도록 한다.

        Args:
            r: Redis 클라이언트
            stream_key: Redis Stream 키
            group: Consumer Group 이름
            msg_id: 메시지 ID
            fields: 메시지 필드 딕셔너리 (b"data" 키에 JSON 문자열)
            dedup: 중복 제거용 alarm_id → 마지막 처리 시각 딕셔너리
        """
        try:
            payload = json.loads(fields[b"data"])
            alarm_time_str = payload.get("alarmTime", "")
            try:
                alarm_time = datetime.strptime(alarm_time_str, "%Y%m%d%H%M%S")
            except ValueError:
                alarm_time = datetime.now()

            alarm_status = payload.get("alarmStatus", "")
            # (D-184) 폴스타 `${severity}`는 정수가 아니라 한글 라벨(해제/주의/경고/심각)로
            # 렌더링된다(폐쇄망 실측). 결정적 정규화 — API 경로 `_build_alarm_event_from_payload`와
            # 동일 함수를 쓴다(경로 대칭). 미지 값은 크래시·폐기 대신 보수적 폴백 + WARNING.
            severity, severity_fallback_reason = coerce_severity(payload.get("severity"))
            if severity_fallback_reason is not None:
                logger.warning(
                    "심각도 미식별 → 보수적 폴백 %s 적용(폐기 대신 통보 경로 유지): "
                    "alarm_id=%s raw=%r reason=%s",
                    severity,
                    payload.get("alarmId"),
                    payload.get("severity"),
                    severity_fallback_reason,
                )
            # is_clear는 severity == 0 단독 기준 — alarmStatus는 폴스타 UI 인지(ACK)
            # 상태(NOT_ACK 등)로 해소 여부와 무관하다 (Plan 47 §9, D-035)
            is_clear = (severity == 0)

            event = AlarmEvent(
                db_id=payload.get("dbId", ""),
                server_name=payload.get("serverName", ""),
                hostname=payload.get("hostname", ""),
                ip_address=payload.get("ipAddress", ""),
                resource_ancestry=payload.get("resourceAncestry", ""),
                alarm_id=str(payload["alarmId"]),
                severity=severity,
                alarm_status=alarm_status,
                resource_type=payload.get("resourceType", ""),
                resource_name=payload.get("resourceName", ""),
                alarm_name=payload.get("alarmName", ""),
                alarm_time=alarm_time,
                conditions=payload.get("conditions", ""),
                condition_log=payload.get("conditionLog", ""),
                is_clear=is_clear,
                raw_payload=payload,
                received_at=datetime.now(),
            )

            # (D-188) hostname → 폴스타 등록 서버명·IP 역조회 후 server_name/ip 승격.
            # dedup·지문·이력 매칭·통보·SSE보다 앞에 두어 모든 소비처가 등록명을 쓰게 한다
            # (API 경로 `_attach_server_identity`와 대칭). 실패·타임아웃은 graceful.
            await attach_server_identity(
                event,
                self._identity_resolver,
                redis=self._redis,
                timeout=float(getattr(self._config.alarm, "server_identity_timeout_seconds", 3.0)),
                cache_ttl=int(getattr(self._config.alarm, "server_identity_cache_ttl_seconds", 3600)),
            )

            gate_on = bool(self._config.noise_gate.enable_noise_gate)
            self_heal = False
            inhibited = False
            flapping = False
            storm = False
            correlated = False  # (Plan 60 E2) 크로스-호스트 상관 매칭 여부
            # (Plan 54 모듈 4) 활성 침묵 규칙 — 저장소 off/부재면 빈 목록(게이트가 평가 안 함).
            silence_rules: list = []
            # (Plan 60 E2) 상관 억제 시 클러스터 메타(대표 지문·멤버 순번·유사도) — 억제일 때만.
            correlation_meta: Optional[dict] = None
            # (Plan 60 E1) 재통보 시 직전 창 재발 메타 — 대표 알람 표기용 그래프 state.
            recurrence_prev: Optional[dict] = None
            # (Plan 60 B-7 L-2) 의미적 근접중복 후보 주석 — provider 가용 시에만 산출(감사 전용).
            # off/inert면 None → decision_store에 키 미포함(회귀 0). 티어·억제 판정과 무관.
            semantic_annotation: Optional[dict] = None
            # (Plan 60 E7-a) 계획-무해 코로보레이션 게이팅용 주석 신호 — 게이트로 진행하는
            # 비해소·비중복 이벤트에 대해 annotation_planned_suppress 시에만 산출(§17.3).
            # off/마커 없으면 None → decide_notification이 평가 안 함(회귀 0).
            annotation_signal_dict: Optional[dict] = None
            # (plans/112 S6) 탐지가 True였던 단계의 구체 근거 {stage: dict} — 게이트가 결정 단계의
            # 것만 골라 감사에 남긴다(판정 무관). 탐지 미수행·미탐지면 비어 None으로 넘긴다.
            detection_evidence: dict[str, dict[str, Any]] = {}
            # (plans/144 §4.2·4.3) 제니퍼 정책 근거 {event_type_norm, policy_row, persistence} —
            # 공급자 주입(apm_noise_policy_enabled) + 제니퍼 알람일 때만.
            # off/폴스타면 None(비트 동일).
            apm_policy: dict[str, Any] | None = None
            # (plans/144 W3) 크로스소스 사건 신호 — 추적기(cross_source_mode≠off)가 있고 사건에 붙은
            # 비해소 알람일 때만. 추적기가 없으면 그래프 입력에 키 자체를 넣지 않는다(비트 동일).
            cross_source: dict[str, Any] | None = None
            # (plans/144 W4) 사건 저장소 앱 영향 · 사건 내 기존 APM 조사(해당 없으면 None).
            episode_app_impact: dict[str, Any] | None = None
            episode_investigation: dict[str, Any] | None = None
            # (plans/144 W5) 통보문 사건 묶음 표시용 요약(annotate·enforce + 사건 소속일 때만).
            episode_summary: dict[str, Any] | None = None
            episode_snapshot_: dict[str, Any] | None = None
            # (plans/162) PAGE 재판정 신호 — 레지스트리(page_review_mode≠off)가 있고 비해소일 때만.
            # 레지스트리가 없으면 그래프 입력에 키 자체를 넣지 않는다(비트 동일).
            page_review_signals: dict[str, Any] | None = None
            # 그래프 뒤 레지스트리 반영용(지문·시각) — 레지스트리가 있을 때만 채운다.
            page_observe: tuple[str, float] | None = None

            if gate_on:
                # ── Plan 52 게이트 활성 경로 ──
                now = time.time()
                if self._apm_noise_ctx is not None and is_apm_source(event):
                    # (plans/144 §4.3) 지문·해소 짝맞춤·정책 조회에 정규화 유형을 쓴다
                    # (`ERROR_X` 발생 ↔ `X` 회복이 같은 지문).
                    match = self._apm_noise_ctx.match(event)
                    # 빈 유형이면 None — 정책(decide_notification)과 같은 규칙(현행 alarm_name)
                    fingerprint = compute_fingerprint(
                        event, alarm_key=match.event_type_norm or None
                    )
                    apm_policy = self._apm_policy_signal(event, fingerprint, match, now)
                else:
                    fingerprint = compute_fingerprint(event)

                # 핑거프린트 dedup(재발생 억제, §6.1). 해소 이벤트(severity 0)는
                # 자가복구 상관을 위해 dedup에서 제외하여 게이트까지 전달한다.
                # 심각도3은 sev3_repeat_interval_seconds(기본=공통)로 재통보 간격 분리(§6.1).
                # (Plan 60 E1) 억제 시 count 집계 감사(record_recurrence), 재통보 시
                # 직전 창 재발 메타(prev)를 그래프 state로 전달(대표 알람 표기).
                if not event.is_clear:
                    is_dup, rec_meta = self._is_duplicate_fingerprint(
                        fingerprint, now, event.severity, alarm_id=event.alarm_id
                    )
                    if is_dup:
                        logger.debug(
                            "중복(재발생) 알람 무시: fingerprint=%s alarm_id=%s count=%s",
                            fingerprint,
                            event.alarm_id,
                            rec_meta.get("count") if rec_meta else None,
                        )
                        # (D-132) 하베스팅 활성 + LLM 분류기 주입 시에만 async 분류를 선행해
                        # sync 적재 경로에 값으로 넘긴다. 분류기 미주입이면 signal=None →
                        # _record_recurrence가 기존 정규식 추출을 수행한다(비트동일).
                        harvest_signal = None
                        if (
                            self._annotation_classifier is not None
                            and getattr(
                                self._config.noise_gate,
                                "annotation_harvest_enabled",
                                False,
                            )
                        ):
                            harvest_signal = await self._annotation_signal(event)
                        self._record_recurrence(
                            fingerprint, event, rec_meta, signal=harvest_signal
                        )
                        if self._page_registry is not None:
                            # (plans/162 F4) 해소 없는 출처의 무소식 닫힘을 미룬다 — 봤음 통지.
                            self._page_registry.touch(fingerprint, now)
                        await ack_message(r, stream_key, group, msg_id)
                        return
                    # 비중복(재통보) — 직전 창 재발 메타가 있으면 대표 알람 표기용으로 보관.
                    recurrence_prev = rec_meta
                    if apm_policy is not None:
                        # (plans/144 §4.2) 심각도 상승 판별용 — 정책 경로만 기록한다.
                        self._apm_sent_severity[fingerprint] = (event.severity, now)
                        self._cap_apm_persistence()

                # min_severity 역할 분리(§4.8): severity 0(해소)·3은 절대 드롭 금지.
                # 1 <= severity < min_severity 인 경우만 드롭(강등·억제는 게이트가 수행).
                if 1 <= event.severity < self._config.alarm.min_severity:
                    logger.debug(
                        "심각도 미달 알람 무시(게이트): alarm_id=%s severity=%s min=%s",
                        event.alarm_id,
                        event.severity,
                        self._config.alarm.min_severity,
                    )
                    await ack_message(r, stream_key, group, msg_id)
                    return

                # (Plan 54 모듈 4) 활성 침묵 규칙 조회 — 저장소가 없으면 빈 목록(회귀 0).
                # 매칭은 게이트가 한다(실효 심각도 대조가 필요해 판정을 도메인에 둔다).
                silence_rules = self._active_silence_rules(now)

                # 자가복구 상관 시드(§3.7) — 발생 기록/해소 매칭.
                self_heal = self._update_firing_registry(event, fingerprint, now)
                if self_heal and self._last_self_heal_evidence:
                    detection_evidence[STAGE_SELF_HEAL] = dict(self._last_self_heal_evidence)

                # (D-049) 해소 이벤트 시 incident resolved 발행(트래커 off면 스킵) +
                # self-heal 매칭 시 자가복구 소요시간을 decision_store에 기록(편향 부분지표).
                if event.is_clear:
                    await self._publish_incident_resolved(event, fingerprint, self_heal)
                    if (
                        self_heal
                        and self._decision_store is not None
                        and self._last_self_heal_duration is not None
                    ):
                        self._decision_store.record_resolution(
                            fingerprint=fingerprint,
                            duration_seconds=self._last_self_heal_duration,
                        )

                # 인히비션 시드(§3.4·E2) — inhibition_enabled일 때만 탐지(아니면 detection
                # 자체 스킵 → 회귀 0). 동일 서버 상위 심각도 활성 시 inhibited=True.
                # getattr 기본 False — 경량 설정(테스트 SimpleNamespace 등)도 안전 처리.
                if getattr(self._config.noise_gate, "inhibition_enabled", False):
                    inhibited = self._detect_inhibition(event, now)
                    if inhibited and self._last_inhibition_evidence:
                        detection_evidence[STAGE_INHIBITION] = dict(
                            self._last_inhibition_evidence
                        )

                # 플래핑 시드(§3.7·E2) — flapping_enabled일 때만 탐지(아니면 스킵 → 회귀 0).
                # 핑거프린트별 상태 시퀀스로 Nagios 가중 %-state-change·히스테리시스 산출.
                if getattr(self._config.noise_gate, "flapping_enabled", False):
                    flapping = self._detect_flapping(fingerprint, event, now)
                    if flapping and self._last_flapping_evidence:
                        detection_evidence[STAGE_FLAPPING] = dict(self._last_flapping_evidence)

                # 스톰 시드(§3.8·E2) — storm_grouping_enabled일 때만 탐지(아니면 스킵 → 회귀 0).
                # 스코프(db_id|server) 사건창 내 발생 다발 시 대표 외 storm=True.
                if getattr(self._config.noise_gate, "storm_grouping_enabled", False):
                    storm = self._detect_storm(event, now)
                    if storm and self._last_storm_evidence:
                        detection_evidence[STAGE_STORM] = dict(self._last_storm_evidence)

                # 크로스-호스트 상관 시드(§4·E2) — cross_host_correlation_enabled일 때만
                # 탐지(아니면 미수행 → detection 스킵·회귀 0). storm(동일 서버)과 독립 병존.
                # db_id(존) 스코프에서 온라인 그리디 군집, 대표 외 멤버부터 correlated=True.
                # 위상 가중(correlation_topology_weight_enabled)일 때만 그래프를 async 로드해
                # sync detection에 주입한다(워커 캐시로 hot-path 클라이언트 open 회피).
                if getattr(
                    self._config.noise_gate, "cross_host_correlation_enabled", False
                ):
                    topo_graph = None
                    if getattr(
                        self._config.noise_gate,
                        "correlation_topology_weight_enabled",
                        False,
                    ):
                        topo_graph = await self._load_topology_graph(event.db_id, now)
                    correlated, correlation_meta = self._detect_correlated_storm(
                        event, now, graph=topo_graph
                    )

                # (Plan 60 B-7 L-2 · §15.4 D-035) 의미적 근접중복 후보 주석 — 게이트를 통과해
                # 그래프로 진행하는 비해소 이벤트에 대해 provider 가용 시에만 산출(감사 전용).
                # provider 미주입(off/inert)이면 내부에서 즉시 skip(비용 0). 결정적 dedup 판정·
                # 억제 지문·티어 판정은 이 호출과 무관하게 위에서 이미 확정됐다(주석은 병렬 관측).
                if not event.is_clear:
                    semantic_annotation = self._annotate_semantic_dedup(
                        event, fingerprint, now
                    )
                    # (Plan 60 E7-a §17.3) 코로보레이션 게이팅용 주석 신호를 산출해 게이트로
                    # 주입한다. annotation_planned_suppress off면 미산출(None → 게이트 무평가·
                    # 비트동일). 판정은 게이트가 코로보레이션과 함께 내린다. (D-132) 분류 수단은
                    # 정규식(기본) 또는 LLM 분류기(옵트인·실패 시 정규식 강등)이며, 게이트가 받는
                    # 신호 형태(3개 bool dict)는 어느 경로에서도 동일하다.
                    if getattr(
                        self._config.noise_gate,
                        "annotation_planned_suppress",
                        False,
                    ):
                        sig = await self._annotation_signal(event)
                        if sig.has_signal():
                            annotation_signal_dict = sig.to_dict()

                # (plans/144 W3) 크로스소스 사건 — 게이트로 가는 알람만(dedup·심각도 미달은 위에서
                # 이미 빠졌다). 해소는 멤버 해소 처리, 비해소는 사건에 붙이고 신호를 만든다.
                cross_source = self._observe_episode(event, fingerprint, now)
                episode_app_impact = self._episode_app_impact(cross_source, event)
                episode_investigation = self._episode_investigation(cross_source, event)
                episode_summary = self._episode_summary(cross_source)
                episode_snapshot_ = self._episode_snapshot(cross_source)
                if self._page_registry is not None:
                    page_observe = (fingerprint, now)
                    if not event.is_clear:
                        page_review_signals = dict(self._page_registry.signals_for(
                            fingerprint,
                            host_key_of(event.db_id, event.server_name or event.hostname),
                            fanout_key_of(event.db_id, event.alarm_name),
                            now,
                        ))
            else:
                # ── 기존 경로 (게이트 off — 무변경) ──
                if self._is_duplicate(event, dedup):
                    logger.debug("중복 알람 무시: alarm_id=%s", event.alarm_id)
                    await ack_message(r, stream_key, group, msg_id)
                    return

                if event.severity < self._config.alarm.min_severity:
                    logger.debug(
                        "심각도 미달 알람 무시: alarm_id=%s severity=%s min=%s",
                        event.alarm_id,
                        event.severity,
                        self._config.alarm.min_severity,
                    )
                    await ack_message(r, stream_key, group, msg_id)
                    return

            logger.info(
                "알람 처리 시작: alarm_id=%s severity=%s name=%s",
                event.alarm_id,
                event.severity,
                event.alarm_name,
            )

            graph_result = await self._graph.ainvoke(  # type: ignore[attr-defined]
                {
                    "alarm_event": event,
                    "history_stats": None,
                    "process_snapshot": None,
                    "analysis_result": None,
                    "error": None,
                    "noise_context": None,
                    "notification_decision": None,
                    "self_heal": self_heal,
                    "inhibited": inhibited,
                    "flapping": flapping,
                    "storm": storm,
                    # (Plan 60 E2) 크로스-호스트 상관 매칭 여부(off/미매칭이면 False).
                    "correlated": correlated,
                    # (Plan 60 E2) 상관 억제 시 클러스터 메타(비상관/미억제면 None).
                    "correlation_meta": correlation_meta,
                    # (Plan 60 E1) 재통보 시 직전 창 재발 메타(없으면 None).
                    "recurrence": recurrence_prev,
                    # (Plan 60 B-7 L-2) 의미적 근접중복 후보 주석(감사 전용·판정 무관, off/inert면 None).
                    "semantic_annotation": semantic_annotation,
                    # (Plan 60 E7-a) 계획-무해 코로보레이션 게이팅용 주석 신호(off/마커 없으면 None).
                    "annotation": annotation_signal_dict,
                    # (Plan 54 모듈 4) 활성 침묵 규칙(off/없으면 빈 목록 → 침묵 단계 미평가).
                    "silence_rules": silence_rules,
                    # (plans/112 S6) 탐지가 True였던 단계의 근거(감사 전용·판정 무관, 없으면 None).
                    "detection_evidence": detection_evidence or None,
                    # (plans/144) 제니퍼 정책 근거·지속 조건(off/폴스타면 None → 게이트 비트 동일).
                    "apm_policy": apm_policy,
                    # (Plan 60 E6) 메시지 기반 L1 보강 블록(enricher가 채움, off면 None).
                    "enrichment": None,
                    # (Plan 60 E3) 동적 baseline 이상 상향 후보(enricher가 채움, off면 None).
                    "anomaly_severity": None,
                    # (Plan 64 CW-A) sre_agent 조사 브리핑(트리거 노드가 채움, off면 None).
                    "investigation_briefing": None,
                    # (plans/144 W3) 크로스소스 사건 신호 — 추적기가 있을 때만 키를 넣는다.
                    **({"cross_source": cross_source} if self._episodes is not None else {}),
                    # (plans/144 W4) 사건 저장소 앱 영향(annotate·enforce) · 사건 내 기존 APM 조사 —
                    # 값이 있을 때만 키를 넣는다(off·shadow·해당 없음이면 키 없음 → 비트 동일).
                    **({"episode_app_impact": episode_app_impact} if episode_app_impact else {}),
                    **(
                        {"episode_investigation": episode_investigation}
                        if episode_investigation else {}
                    ),
                    # (plans/144 W5) 통보문 사건 묶음 표시 요약 — 값이 있을 때만 키(비트 동일).
                    **({"episode_summary": episode_summary} if episode_summary else {}),
                    # (plans/153 W4) 제출 시점 사건 스냅샷 — 사건 맥락 on·사건 소속일 때만 키
                    # (비트 동일).
                    **({"episode_snapshot": episode_snapshot_} if episode_snapshot_ else {}),
                    # (plans/162) PAGE 재판정 재료 — 레지스트리가 있는 비해소 알람만 키
                    # (비트 동일).
                    **(
                        {"page_review_signals": page_review_signals}
                        if page_review_signals is not None else {}
                    ),
                },
                config={
                    "configurable": {
                        "app_config": self._config,
                        "history_repo": self._history_repo,
                        "history_redis": self._redis,
                        "process_client": self._process_client,
                        "noise_repo": self._noise_repo,
                        # (plans/144 §4.1) 제니퍼 유형 정책 공급자 — off면 None →
                        # enricher 분기 없음.
                        "apm_noise_ctx": self._apm_noise_ctx,
                        # (Plan 60 E3) 동적 baseline 이상탐지 어댑터 — off/미생성 시 None →
                        # enricher는 이상탐지 태스크 미추가(anomaly_severity 미산출·회귀 0).
                        "metric_baseline": self._metric_baseline,
                        "decision_store": self._decision_store,
                        # (plans/153 W2) 조사 저장소 — 트리거 off면 None → 저장 호출 no-op.
                        "investigation_store": self._investigation_store,
                        # (E3) TICKET 일배치 큐 — 워커는 cross-process라 alarm_bus는 미주입.
                        # 큐 적재는 동작하고, 티어 SSE는 sse_publisher(Redis 브리지)로 중계한다.
                        "ticket_queue": self._ticket_queue,
                        # (E4) 운영자 피드백 few-shot 저장소 — off/비활성 시 None →
                        # analyzer는 피드백 섹션 없이 진행(회귀 0).
                        "feedback_store": self._feedback_store,
                        # (E3 후속·D-048.9) 워커→UI 실시간 SSE Redis pub/sub 발행기.
                        # off/Redis 부재 시 None → notifier는 로그 폴백(E3 무변경).
                        "sse_publisher": self._sse_publisher,
                        # (D-049) incident open 발행기 — notifier가 PAGE 결정 시 사용.
                        # off/Redis 부재 시 None → notifier는 발행 스킵(회귀 0).
                        "incident_publisher": self._incident_publisher,
                        # (Plan 60 E4) 인히비션 활성 상태 참조 전달(읽기전용) — enricher가
                        # root_notified(다홉 하이브리드) 산출에 소비. 신규 저장소 불필요.
                        "active_firings": self._active_firings,
                        # (Plan 60 B-7 L-4) 로컬 임베딩 provider — topology_text_fusion_enabled 시
                        # enricher가 root 리소스 NAME과 알람 텍스트 유사도(root_text_similarity)를
                        # 산출하는 데 소비(감사 전용). off/두 플래그 off면 None → enricher 미주입·
                        # 주석 경로 미진입(회귀 0). repo는 provider를 직접 생성하지 않는다(워커 주입).
                        "embedding_provider": self._embedding_provider,
                        # (Plan 64 CW-A) sre_agent 조사 서비스 MCP 클라이언트 — off/미배선 시 None →
                        # investigation_trigger 노드는 no-op(브리핑 미생성·회귀 0). 노드가 connect/
                        # submit/poll/disconnect를 전체 타임아웃 가드 안에서 관리한다.
                        "sre_agent_client": self._sre_agent_client,
                        # (plans/87 J4) APM 게이트웨이 클라이언트 — off/미설정이면 None →
                        # 게이트 노드가 app_impact를 조회하지 않는다(판정 비트동일·회귀 0).
                        "apm_client": self._apm_client,
                    }
                },
            )
            # (plans/144 W3) 최종 티어를 사건 멤버에 기록 — 뒤에 오는 증상의 원인 조건(④) 재료.
            if cross_source is not None:
                self._record_episode_tier(cross_source, event, graph_result)
                # (plans/144 W4) 조사 id 기록 · 사후 승격 후보·통보 — 개별 try/except(내부).
                await self._after_episode_graph(cross_source, event, graph_result, now)
            # (plans/162) 최종 판정을 PAGE 레지스트리에 반영 — 실패는 처리 흐름을 막지 않는다.
            if page_observe is not None:
                try:
                    self._observe_page(event, page_observe[0], graph_result, page_observe[1])
                except Exception:  # noqa: BLE001 — 레지스트리 갱신 실패는 발송·ACK와 무관
                    logger.warning(
                        "PAGE 재판정 레지스트리 갱신 실패(무시): alarm_id=%s",
                        event.alarm_id, exc_info=True,
                    )
        except Exception as exc:
            logger.exception("알람 처리 실패: msg_id=%s", msg_id)
            # (D-184) ACK 전에 dead-letter 스트림에 원문+사유를 보관한다 — 실패 건이 흔적 없이
            # 폐기되어 진단이 로그 전사에만 의존하던 상태(2026-08-25 severity 라벨 건) 재발 방지.
            await self._dead_letter(r, stream_key, msg_id, fields, exc)
        finally:
            await ack_message(r, stream_key, group, msg_id)

    async def _dead_letter(
        self,
        r: aioredis.Redis,
        stream_key: str,
        msg_id: bytes,
        fields: dict,
        exc: BaseException,
    ) -> None:
        """처리 실패 메시지를 dead-letter 스트림에 적재한다 (D-184 · graceful).

        상시 동작(끄는 플래그 없음 — D-162 §6). 적재 실패는 warning 후 무시한다 —
        dead-letter가 ACK·소비 루프를 막아서는 안 된다.
        """
        alarm_cfg = getattr(self._config, "alarm", None)
        try:
            await dead_letter_message(
                r,
                getattr(alarm_cfg, "dead_letter_stream_key", "alarm:dead"),
                stream_key,
                msg_id,
                fields,
                exc,
                maxlen=int(getattr(alarm_cfg, "dead_letter_maxlen", 1000)),
            )
        except Exception:  # noqa: BLE001 — dead-letter 실패가 소비 루프를 막지 않는다
            logger.warning("dead-letter 적재 실패(무시): msg_id=%s", msg_id)

    async def _publish_incident_resolved(
        self, event: AlarmEvent, fingerprint: str, self_heal: bool
    ) -> None:
        """해소 이벤트 시 incident resolved 이벤트를 발행한다 (D-049 · graceful).

        incident_publisher 미주입(트래커 off) 시 발행을 스킵한다(회귀 0). resolution은
        self-heal 매칭이면 'self_heal', 아니면 'clear'다. subscriber(API)가 fingerprint로
        매칭 open incident를 resolved UPDATE하며, 매칭이 없으면 no-op이다.
        발행 실패는 발행기 내부에서 삼킨다(graceful — 워커 파이프라인 무차단).
        """
        if self._incident_publisher is None:
            return
        payload = {
            "type": "resolved",
            "fingerprint": fingerprint,
            "alarm_id": event.alarm_id,
            "db_id": event.db_id,
            "server_name": event.server_name,
            "severity": event.severity,
            "tier": "",
            "ts": datetime.now().isoformat(),
            "resolution": "self_heal" if self_heal else "clear",
        }
        await self._incident_publisher.publish(payload)

    def _is_duplicate(self, event: AlarmEvent, dedup: dict[str, float]) -> bool:
        """`DedupService.is_duplicate` 위임 — 테스트·`_process` seam(plans/177)."""
        return _dedup_service(self).is_duplicate(event, dedup)

    def _is_duplicate_fingerprint(
        self, fingerprint: str, now: float, severity: int, *, alarm_id: str = ""
    ) -> tuple[bool, Optional[dict]]:
        """`DedupService.is_duplicate_fingerprint` 위임 — 테스트·`_process` seam(plans/177)."""
        return _dedup_service(self).is_duplicate_fingerprint(
            fingerprint, now, severity, alarm_id=alarm_id
        )

    def _record_recurrence(
        self,
        fingerprint: str,
        event: AlarmEvent,
        rec_meta: Optional[dict],
        *,
        signal: Optional[AnnotationSignal] = None,
    ) -> None:
        """`DedupService.record_recurrence` 위임 — 테스트·`_process` seam(plans/177)."""
        _dedup_service(self).record_recurrence(fingerprint, event, rec_meta, signal=signal)

    def _annotate_semantic_dedup(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> Optional[dict]:
        """`DedupService.annotate_semantic_dedup` 위임 — 테스트·`_process` seam(plans/177)."""
        return _dedup_service(self).annotate_semantic_dedup(event, fingerprint, now)

    def _update_firing_registry(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> bool:
        """`SignalDetector.update_firing_registry` 위임 — 테스트·`_process` seam(plans/177)."""
        return _signal_detector(self).update_firing_registry(event, fingerprint, now)

    def _apm_policy_signal(
        self, event: AlarmEvent, fingerprint: str, match: ApmPolicyMatch, now: float
    ) -> dict[str, Any]:
        """제니퍼 알람의 정책 근거와 지속 조건 신호를 만든다 (plans/144 §4.2·§6.2).

        지속 조건 대상 = 비해소 · `event_kind="metric"` · 정책 행에 persistence 있음. 대상이면
        지문별 창 안 발생 수를 세고(현재 건 포함) `{count, min_count, window_seconds, met}`을
        싣는다 — 판정(실효 심각도 2일 때만 강등 · 하한 DASHBOARD)은 정책 계층이 한다. 첫 이벤트를
        붙잡지 않는다.

        지문 dedup과의 연동(정책 경로만 — 폴스타·플래그 off는 호출되지 않는다):

        - 재판정(심각도 2): 조건 미달로 **실제로 그래프에 간**(dedup 비중복) 지문을 기억해 두고,
          창 안 반복으로 조건이 채워지는 이벤트가 오면 그 지문의 dedup 기록을 1회 지워 매트릭스대로
          다시 판정되게 한다(`reevaluated=True`). dedup에 버려질 미달 이벤트는 보류를 세우지
          않으므로 재판정은 dedup 창마다 최대 1회다.
        - 심각도 상승: 마지막으로 게이트에 보낸 심각도보다 높은 이벤트는 dedup 기록을 지워
          게이트까지 보낸다(`escalated_from`) — 강등된 sev2 뒤 sev3가 버려져 끝내 PAGE되지 않는
          일을 막는다.
        - 해소 이벤트는 그 지문의 카운터·보류를 지운다(발생→해소→재발이 「충족」으로 dedup을
          건너뛰지 않게).

        dedup 판정 **전**에 호출된다 — 중복으로 버려질 이벤트도 지속 조건 카운트에는 들어간다.
        """
        signal: dict[str, Any] = {
            "event_type_norm": match.event_type_norm,
            "policy_row": match.row_key,
            "persistence": None,
        }
        if self._apm_noise_ctx is not None and self._apm_noise_ctx.error:
            signal["policy_error"] = self._apm_noise_ctx.error
        self._sweep_apm_persistence(now)
        if event.is_clear:
            self._apm_persist.pop(fingerprint, None)
            self._apm_persist_pending.pop(fingerprint, None)
            return signal

        sent = self._apm_sent_severity.get(fingerprint)
        if sent is not None and event.severity > sent[0]:
            self._gate_dedup.pop(fingerprint, None)
            self._apm_persist_pending.pop(fingerprint, None)
            signal["escalated_from"] = sent[0]
            logger.info(
                "제니퍼 심각도 상승 — 지문 dedup 해제: fingerprint=%s %d→%d alarm_id=%s",
                fingerprint,
                sent[0],
                event.severity,
                event.alarm_id,
            )

        rule = match.row.persistence if match.row is not None else None
        if rule is None or event_kind(event) != EVENT_KIND_METRIC:
            return signal

        entry = self._apm_persist.get(fingerprint)
        win: deque[float] = entry[1] if entry is not None else deque()
        win.append(now)
        while win and now - win[0] > rule.window_seconds:
            win.popleft()
        self._apm_persist[fingerprint] = (rule.window_seconds, win)

        count = len(win)
        persistence: dict[str, Any] = {
            "count": count,
            "min_count": rule.min_count,
            "window_seconds": rule.window_seconds,
            "met": count >= rule.min_count,
        }
        if event.severity == 2:
            if not persistence["met"]:
                if not self._apm_dedup_active(fingerprint, now):
                    self._apm_persist_pending[fingerprint] = now
            elif self._apm_persist_pending.pop(fingerprint, None) is not None:
                self._gate_dedup.pop(fingerprint, None)
                persistence["reevaluated"] = True
        self._cap_apm_persistence()
        signal["persistence"] = persistence
        return signal

    def _apm_dedup_active(self, fingerprint: str, now: float) -> bool:
        """`DedupService.apm_dedup_active` 위임 — 테스트·`_apm_policy_signal` seam(plans/177)."""
        return _dedup_service(self).apm_dedup_active(fingerprint, now)

    def _sweep_apm_persistence(self, now: float) -> None:
        """`DedupService.sweep_apm_persistence` 위임 — `_apm_policy_signal` seam(plans/177)."""
        _dedup_service(self).sweep_apm_persistence(now)

    def _cap_apm_persistence(self) -> None:
        """`DedupService.cap_apm_persistence` 위임 — 테스트·`_process` seam(plans/177)."""
        _dedup_service(self).cap_apm_persistence()

    def _detect_inhibition(self, event: AlarmEvent, now: float) -> bool:
        """`SignalDetector.detect_inhibition` 위임 — 테스트·`_process` seam(plans/177)."""
        return _signal_detector(self).detect_inhibition(event, now)

    def _detect_flapping(
        self, fingerprint: str, event: AlarmEvent, now: float
    ) -> bool:
        """`SignalDetector.detect_flapping` 위임 — 테스트·`_process` seam(plans/177)."""
        return _signal_detector(self).detect_flapping(fingerprint, event, now)

    def _detect_storm(self, event: AlarmEvent, now: float) -> bool:
        """`SignalDetector.detect_storm` 위임 — 테스트·`_process` seam(plans/177)."""
        return _signal_detector(self).detect_storm(event, now)

    async def _load_topology_graph(
        self, db_id: str, now: float
    ) -> Optional[DependencyGraph]:
        """`SignalDetector.load_topology_graph` 위임 — 테스트·`_process` seam(plans/177)."""
        return await _signal_detector(self).load_topology_graph(db_id, now)

    def _detect_correlated_storm(
        self,
        event: AlarmEvent,
        now: float,
        *,
        graph: Optional[DependencyGraph] = None,
    ) -> tuple[bool, Optional[dict]]:
        """`SignalDetector.detect_correlated_storm` 위임 — 테스트·`_process` seam(plans/177)."""
        return _signal_detector(self).detect_correlated_storm(event, now, graph=graph)
