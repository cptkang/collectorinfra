"""노이즈 게이트 신호 탐지기 (plans/177 W3).

`AlarmWorker`에서 자가복구 상관(발생 레지스트리)·인히비션·플래핑·스톰·크로스-호스트 상관
(클러스터 sweep · 위상 가중 그래프 캐시) 탐지 책임을 옮긴다. 본문은 `AlarmWorker._<이름>`에서
동작 그대로 옮겼고, 워커에는 같은 이름의 위임 메서드가 남아 `_process`·테스트 seam이 된다.

탐지 상태(`_firing_registry`·`_active_firings`·`_flap_*`·`_storm_window`·`_correlation_clusters`·
`_topology_graph_cache`)와 직전 탐지 근거(`_last_*_evidence`·`_last_self_heal_duration`)는 워커가
계속 소유한다 — 테스트·하네스가 생성 뒤 워커 속성을 바꿔 끼우므로, 탐지기는 `SignalHost`를 거쳐
**접근 시점**의 값을 읽고 쓴다(생성 시점에 붙잡지 않는다).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Any, Protocol

from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.correlation import (
    ClusterState,
    extract_site_token,
    match_cluster,
    signature_tokens,
)
from noise_gate.domain.flapping import MAX_STATES, flap_percent, update_flap_state
from noise_gate.domain.notification_policy import compute_fingerprint
from noise_gate.domain.severity_signatures import scan_signature_severity
from noise_gate.infrastructure.apm_noise_context import (
    ApmNoiseContext,
    correlation_domain,
    correlation_extra,
)

if TYPE_CHECKING:
    from noise_gate.domain.topology import DependencyGraph

# 이동 전과 같은 로거 이름 — 운영 로그 `%(name)s`와 테스트 caplog 로거 필터를 보존한다.
logger = logging.getLogger("noise_gate.application.alarm_worker")

# 위상 가중 그래프 로드 실패 시 음성 캐시 TTL(초) — 재시도 허용하되 hot-path 폭주 방지.
_TOPOLOGY_NEGATIVE_TTL_SECONDS = 60


class SignalHost(Protocol):
    """탐지기가 접근 시점에 읽고 쓰는 워커 쪽 상태·협력자 (plans/177 W3)."""

    @property
    def config(self) -> Any: ...

    @property
    def apm_noise_ctx(self) -> ApmNoiseContext | None: ...

    @property
    def firing_registry(self) -> dict[str, tuple[float, int]]: ...

    @property
    def active_firings(self) -> dict[str, tuple[int, float, str]]: ...

    @property
    def flap_states(self) -> dict[str, deque[bool]]: ...

    @property
    def flap_flag(self) -> dict[str, bool]: ...

    @property
    def flap_last_seen(self) -> dict[str, float]: ...

    @property
    def storm_window(self) -> dict[str, deque[float]]: ...

    @property
    def correlation_clusters(self) -> dict[str, list[ClusterState]]: ...

    @property
    def topology_graph_cache(self) -> dict[str, tuple[float, DependencyGraph | None]]: ...

    last_self_heal_duration: float | None
    last_self_heal_evidence: dict[str, Any] | None
    last_inhibition_evidence: dict[str, Any] | None
    last_flapping_evidence: dict[str, Any] | None
    last_storm_evidence: dict[str, Any] | None


class SignalDetector:
    """자가복구·인히비션·플래핑·스톰·크로스-호스트 상관을 결정적으로 탐지한다 (plans/177 W3)."""

    def __init__(self, host: SignalHost) -> None:
        """워커 쪽 접근점 `host`를 받는다 — 값은 메서드가 부를 때마다 host에서 읽는다."""
        self._host = host

    def update_firing_registry(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> bool:
        """자가복구 상관용 발생 레지스트리를 갱신하고 self_heal 매칭 여부를 반환한다 (§3.7).

        - 발생 이벤트(not is_clear)이고 저심각도(1 <= severity <= suppress_max_severity)면
          (now, severity)를 기록한다.
        - 해소 이벤트(is_clear)면 동일 핑거프린트가 self_heal_window_seconds 내에 저심각도로
          발생했는지 확인하여 매칭되면 True를 반환한다(매칭 항목은 1회성 제거).
        - 심각도 3은 자가복구 후보에서 제외한다(해소가 와도 발생 PAGE 보존, §3.7).
        - 만료 항목은 정리한다(메모리 누수 방지).

        Returns:
            self_heal 매칭 여부 (해소 이벤트에서만 True 가능)
        """
        suppress_max = self._host.config.noise_gate.suppress_max_severity
        window = self._host.config.noise_gate.self_heal_window_seconds
        self_heal = False
        # (D-049) 직전 self-heal 소요시간 리셋 — 매칭 시에만 채운다(호출부가 기록).
        self._host.last_self_heal_duration = None
        # (plans/112 S6) 근거도 같이 리셋 — 매칭 시에만 채운다.
        self._host.last_self_heal_evidence = None
        if event.is_clear:
            rec = self._host.firing_registry.pop(fingerprint, None)
            if rec is not None:
                fired_ts, fired_sev = rec
                if now - fired_ts <= window and 1 <= fired_sev <= suppress_max:
                    self_heal = True
                    self._host.last_self_heal_duration = now - fired_ts
                    self._host.last_self_heal_evidence = {
                        "heal_seconds": round(now - fired_ts, 3),
                        "fired_severity": fired_sev,
                    }
        elif 1 <= event.severity <= suppress_max:
            self._host.firing_registry[fingerprint] = (now, event.severity)

        # 만료 항목 정리
        expired = [
            k for k, (ts, _) in self._host.firing_registry.items() if now - ts > window
        ]
        for k in expired:
            del self._host.firing_registry[k]
        return self_heal

    def detect_inhibition(self, event: AlarmEvent, now: float) -> bool:
        """인히비션(§3.4·E2): 동일 서버 상위 심각도 발생 중인지 결정적으로 탐지한다.

        스코프(db_id|server)별로 현재 활성 **최고 심각도 인히비터**를
        `(severity, 발생시각, alarm_key)`로 유지한다. 발생 이벤트가 도착하면:

        - 같은 스코프에 **더 높은 심각도**가 inhibition_window_seconds 내 활성이고
          그 인히비터가 **다른 알람**(self-inhibition 금지 — alarm_key 비교)이면 inhibited=True.
        - 활성 기록 갱신: 기존 기록이 없거나 만료됐거나 현재 발생이 같거나 더 높은 심각도면
          현재 발생으로 교체(스코프별 최고 심각도 인히비터 유지). 만료 스코프는 정리한다.
        - 해소 이벤트(is_clear)·무효 심각도는 인히비션 대상 아님(False) — 해당 스코프 기록은
          window 만료로 자연 정리된다(§3.4 해소는 detection 비대상).

        alarm_key는 alarm_name(없으면 fingerprint)으로, 동일 알람의 자기억제를 방지한다.
        inhibition_enabled일 때만 호출된다(_process 게이트 경로) — off면 detection 미수행.

        Returns:
            inhibited 여부(상위 심각도 다른 알람이 활성이면 True).
            (plans/112 S6) True일 때 누른 상위 알람을 `_last_inhibition_evidence`에 남긴다.
        """
        self._host.last_inhibition_evidence = None
        if event.is_clear or event.severity <= 0:
            return False

        scope = f"{event.db_id}|{event.server_name}"
        window = self._host.config.noise_gate.inhibition_window_seconds
        alarm_key = event.alarm_name or compute_fingerprint(event)

        inhibited = False
        rec = self._host.active_firings.get(scope)
        if rec is not None:
            rec_sev, rec_ts, rec_key = rec
            if (
                now - rec_ts <= window
                and rec_sev > event.severity
                and rec_key != alarm_key
            ):
                inhibited = True
                self._host.last_inhibition_evidence = {
                    "inhibitor": rec_key,
                    "inhibitor_severity": rec_sev,
                    "age_seconds": round(now - rec_ts, 3),
                    "window_seconds": window,
                }

        # 활성 기록 갱신 — 스코프별 최고 심각도 인히비터 유지(만료·동급/상위 발생 시 교체).
        if rec is None or now - rec[1] > window or event.severity >= rec[0]:
            self._host.active_firings[scope] = (event.severity, now, alarm_key)

        # 만료 스코프 정리(메모리 누수 방지)
        expired = [
            s for s, (_, ts, _) in self._host.active_firings.items() if now - ts > window
        ]
        for s in expired:
            del self._host.active_firings[s]
        return inhibited

    def detect_flapping(
        self, fingerprint: str, event: AlarmEvent, now: float
    ) -> bool:
        """플래핑(§3.7·E2): 핑거프린트별 상태 진동을 Nagios 알고리즘으로 탐지한다.

        핑거프린트별 최근 상태 시퀀스(firing=not is_clear)를 maxlen=MAX_STATES(21) deque로
        유지하고, 도메인 순수함수로 가중 %-state-change(`flap_percent`)와 히스테리시스
        (`update_flap_state`, high/low 임계)를 적용하여 플래핑 여부를 산출한다.

        - 발생·해소 모두 상태 전이의 한 점이므로 시퀀스에 append한다(is_clear 제외하지 않음).
        - 직전 플래핑 상태(워커 `_flap_flag`)를 히스테리시스 입력으로 사용하고 갱신한다.
        - 상태가 표본 하한(MIN_STATES=5) 미만이면 플래핑이 아니다(발생→해소→재발생 한 번 억제 방지).
        - flapping_enabled일 때만 호출된다(_process 게이트 경로) — off면 detection 미수행(회귀 0).

        만료 키 정리(메모리 일관성): 핑거프린트별 마지막 갱신 시각(워커 `_flap_last_seen`)을
        추적하여, repeat_interval_seconds(기본 4h, 관대) 동안 재등장하지 않은 핑거프린트의
        상태를 _flap_states/_flap_flag/_flap_last_seen 세 dict에서 함께 제거한다.
        ttl이 관대하므로 활성 플래핑 상태(짧은 간격 재등장)는 보존되어 산출값이 불변이다.

        Returns:
            갱신된 플래핑 상태(True=플래핑 중 → 게이트에서 억제 대상).
            (plans/112 S6) True일 때 변화율·임계·표본 수를 `_last_flapping_evidence`에 남긴다.
        """
        self._host.last_flapping_evidence = None
        states = self._host.flap_states.get(fingerprint)
        if states is None:
            states = deque(maxlen=MAX_STATES)
            self._host.flap_states[fingerprint] = states
        states.append(not event.is_clear)

        percent = flap_percent(list(states))
        prev = self._host.flap_flag.get(fingerprint, False)
        cfg = self._host.config.noise_gate
        new = update_flap_state(
            prev, percent, cfg.flap_high_threshold, cfg.flap_low_threshold,
            samples=len(states),
        )
        self._host.flap_flag[fingerprint] = new
        self._host.flap_last_seen[fingerprint] = now
        if new:
            self._host.last_flapping_evidence = {
                "flap_percent": round(percent, 2),
                "high": cfg.flap_high_threshold,
                "low": cfg.flap_low_threshold,
                "samples": len(states),
            }

        # 만료 핑거프린트 정리(메모리 누수 방지) — ttl은 재통보 간격 재사용(관대한 4h).
        # getattr 가드 — 경량 설정(테스트 SimpleNamespace 등)은 기본 4h로 폴백(무변경).
        ttl = getattr(cfg, "repeat_interval_seconds", 14400)
        expired = [
            k for k, ts in self._host.flap_last_seen.items() if now - ts > ttl
        ]
        for k in expired:
            del self._host.flap_last_seen[k]
            self._host.flap_states.pop(k, None)
            self._host.flap_flag.pop(k, None)
        return new

    def detect_storm(self, event: AlarmEvent, now: float) -> bool:
        """스톰(§3.8·E2): 동일 서버 사건창 내 발생 다발 여부를 탐지한다.

        스코프(db_id|server)별 사건창 deque에 **발생 이벤트만** now를 append하고
        (해소 이벤트는 카운트 제외 — 발생 다발만 스톰으로 본다), storm_window_seconds 밖
        타임스탬프를 제거한다. 창 크기가 storm_threshold를 **초과**하면 storm=True.

        경계 규칙(대표/억제):
            - 창에 threshold개 이하가 쌓이는 동안(첫 threshold건)은 storm=False → 통보(대표).
            - 이번 건을 더해 창 크기 > threshold이면 storm=True → 억제(대표 외 다발).
            예) threshold=5 → 1~5번째 발생은 통보, 6번째부터 억제. 경계는 테스트로 고정.

        - storm_grouping_enabled일 때만 호출된다(_process 게이트 경로) — off면 미수행(회귀 0).

        만료 키 정리(메모리 일관성): window 밖 타임스탬프를 popleft한 뒤 해당 scope의 deque가
        비면 scope 키를 dict에서 제거한다(빈 deque 미보관). 각 scope는 자체 발생 이벤트로
        다시 트리거되므로 현재 처리 중 scope만 정리해도 단조 증가를 막을 수 있다.

        Returns:
            storm 여부(창 크기가 임계를 초과하면 True).
            (plans/112 S6) True일 때 창 내 발생 수·임계·창 길이를 `_last_storm_evidence`에 남긴다.
        """
        self._host.last_storm_evidence = None
        if event.is_clear:
            return False

        scope = f"{event.db_id}|{event.server_name}"
        window_sec = self._host.config.noise_gate.storm_window_seconds
        threshold = self._host.config.noise_gate.storm_threshold

        win = self._host.storm_window.get(scope)
        if win is None:
            win = deque()
            self._host.storm_window[scope] = win
        win.append(now)
        # 창 밖(window_seconds 초과) 항목 제거(가장 오래된 쪽부터)
        while win and now - win[0] > window_sec:
            win.popleft()
        # 빈 deque scope 키 제거(메모리 누수 방지) — append 직후라 보통 1건 이상이나,
        # window_sec<=0 등 경계에서 모두 만료되면 빈 키를 남기지 않는다.
        if not win:
            del self._host.storm_window[scope]
        storm: bool = len(win) > threshold
        if storm:
            self._host.last_storm_evidence = {
                "window_count": len(win),
                "threshold": threshold,
                "window_seconds": window_sec,
            }
        return storm

    def sweep_correlation_clusters(self, now: float, window_sec: int) -> None:
        """만료된 상관 클러스터를 제거하고 빈 스코프 키를 정리한다 (Plan 60 E2·§10).

        전 스코프(db_id 존)를 순회하여 last_ts가 window_sec 밖인 클러스터를 제거하고,
        클러스터가 모두 사라진 스코프 키를 dict에서 삭제한다. 스코프 수는 존 개수로
        자연 유한하므로 전수 순회가 저렴하며, 형제 상태(_flap_last_seen·_gate_dedup)의
        전-키 정리 루프와 일관되게 조용한 존의 클러스터도 만료시킨다(단조 증가 방지).
        """
        empty_scopes = [
            scope
            for scope, clusters in self._host.correlation_clusters.items()
            if not self.prune_expired(clusters, now, window_sec)
        ]
        for scope in empty_scopes:
            del self._host.correlation_clusters[scope]

    @staticmethod
    def prune_expired(
        clusters: list[ClusterState], now: float, window_sec: int
    ) -> list[ClusterState]:
        """window_sec 밖 클러스터를 in-place 제거하고 남은 리스트를 반환한다."""
        clusters[:] = [c for c in clusters if now - c.last_ts <= window_sec]
        return clusters

    async def load_topology_graph(
        self, db_id: str, now: float
    ) -> DependencyGraph | None:
        """위상 가중용 정적 의존 그래프를 로드한다(워커 캐시 — hot-path 클라이언트 open 회피).

        correlation_topology_weight_enabled일 때만 _process(async)가 호출한다. cold/만료
        시에만 registry 클라이언트를 열어 TopologyLoader().load_graph로 로드하고, db_id별로
        (만료 epoch, graph)를 캐시한다. 실패(미등록·비PostgreSQL·조회 실패)는 None을 짧은
        음성 TTL(_TOPOLOGY_NEGATIVE_TTL_SECONDS)로 캐시해 재시도를 허용하되 hot-path 폭주를
        막는다(graceful — 보너스 없이 Jaccard로 진행).

        registry 접근은 _build_metric_baseline 패턴(DBRegistry(워커 config))을 재사용한다.
        그래프는 event.db_id(존)로 로드하므로 인접성도 존 내에서만 계산된다(B-6 불변).

        Args:
            db_id: 대상 폴스타 DB(존).
            now: 캐시 만료 판정 기준 시각(time.time()).

        Returns:
            DependencyGraph 또는 None(실패·미지원).
        """
        cached = self._host.topology_graph_cache.get(db_id)
        if cached is not None and now < cached[0]:
            return cached[1]

        cfg = self._host.config.noise_gate
        ttl = getattr(cfg, "topology_cache_ttl_seconds", 86400)
        max_hops = getattr(cfg, "topology_max_hops", 5)
        graph: DependencyGraph | None = None
        try:
            # 이동 전 순서 그대로(plans/177 W3) — ruff isort는 원본 위치에서도 같은 판정이었다.
            from noise_gate.infrastructure.topology_loader import TopologyLoader  # noqa: I001
            from src.routing.db_registry import DBRegistry

            registry = DBRegistry(self._host.config)
            if registry.is_registered(db_id):
                async with registry.get_client(db_id) as client:
                    graph = await TopologyLoader().load_graph(
                        client,
                        db_id,
                        cache_ttl_seconds=ttl,
                        max_hops=max_hops,
                    )
        except Exception:
            logger.exception(
                "위상 가중 그래프 로드 실패 — 보너스 없이 진행: db_id=%s", db_id
            )
            graph = None

        # 성공=장기 TTL, 실패(None)=짧은 음성 TTL(재시도 허용·폭주 방지).
        expiry = now + (ttl if graph is not None else _TOPOLOGY_NEGATIVE_TTL_SECONDS)
        self._host.topology_graph_cache[db_id] = (expiry, graph)
        return graph

    def detect_correlated_storm(
        self,
        event: AlarmEvent,
        now: float,
        *,
        graph: DependencyGraph | None = None,
    ) -> tuple[bool, dict[str, Any] | None]:
        """크로스-호스트 상관(§4·E2): db_id(존) 스코프 온라인 그리디 군집을 탐지한다.

        서버 경계를 넘어 필드 유사도(Jaccard)·시간 근접으로 사건을 군집한다. 첫 도착
        이벤트가 클러스터 **대표**(통보)이고, 이후 유사 이벤트가 correlation_min_cluster_size
        번째 멤버부터 억제(correlated=True)된다(소급 선출 없음 — 앞선 판정 불변).

        절차:
            - 해소(is_clear) 이벤트는 군집/카운트에서 제외한다(detect_storm 동일).
            - (plans/197) apm_correlation_domain_scope_enabled면 제니퍼 이벤트의 스코프를
              `db_id|domain_id`로 나눈다(도메인 없으면 db_id 그대로).
            - ① 만료 sweep: last_ts가 correlation_window_seconds 밖인 클러스터를 전 스코프에서
              제거하고 빈 스코프 키를 삭제한다(§10 — 키 만료 sweep 의무).
            - ② 버퍼 상한: 스코프의 활성 클러스터가 correlation_buffer_max 이상이면 oldest
              (first_ts 최소)부터 제거해 append 여지를 확보하고 warning을 남긴다(침묵 금지).
            - sig_label은 워커가 domain scan_signature_severity(event.condition_log)로 사전
              산출한다(정책 순수성 — correlation.py는 값만 소비). signature_tokens는 server_name을
              제외한 정규화 토큰을 만든다.
            - **위상 가중(E4 인접성)**: graph가 주어지고 event_node(=graph.id_of(server_name))
              해소에 성공하면, 각 클러스터의 대표 노드와의 is_related(max_hops)로 adjacent bool
              리스트를 산출해 match_cluster에 주입한다(인접 클러스터에 보너스). graph 미주어짐/
              이름 해소 실패(엣지 미보유 root 등) → adjacent=None(보너스 없음·Jaccard
              폴백·graceful).
            - match_cluster로 최고 유사도 클러스터를 찾는다(보너스 반영 후 점수, 동점 시 first_ts
              오름차순). 매칭되면 member_count+1·last_ts=now 후 member_count>=min_cluster_size면
              억제하고 **클러스터 메타**(대표 지문·멤버 순번·유사도)를 반환한다. 미매칭이면 대표
              (첫 도착) 클러스터를 append(대표 노드 저장)하고 (False, None)을 반환한다.

        cross_host_correlation_enabled일 때만 호출된다(_process 게이트 경로) — off면 미수행(회귀 0).

        Args:
            event: 알람 이벤트.
            now: 현재 시각(time.time()).
            graph: 위상 가중용 DependencyGraph(위상 가중 off·로드 실패면 None → Jaccard 폴백).

        Returns:
            (correlated, cluster_meta) 튜플. 억제(correlated=True)일 때만 메타
            (`{representative_fp, member_seq, similarity}`)를 싣고, 그 외에는 (…, None).
        """
        if event.is_clear:
            return False, None

        cfg = self._host.config.noise_gate
        window_sec = cfg.correlation_window_seconds

        # ① 만료 sweep — 전 스코프의 window 밖 클러스터 제거 + 빈 스코프 키 삭제.
        self.sweep_correlation_clusters(now, window_sec)

        scope = event.db_id  # db_id(존) 스코프 — 존 경계는 넘지 않는다(§8 B-6).
        # (plans/197 W3) 옵트인 — 제니퍼 이벤트는 같은 도메인(업무 앱)끼리만 묶는다. 흔한 유형은
        # 도메인 토큰 1개 차이로도 Jaccard 임계를 넘어 다른 앱 장애가 한 군집이 되기 때문이다.
        # 도메인 없음·폴스타·off(기본)면 현행 db_id 스코프(비트 동일).
        if getattr(cfg, "apm_correlation_domain_scope_enabled", False) and is_apm_source(
            event
        ):
            domain = correlation_domain(event)
            if domain:
                scope = f"{event.db_id}|{domain}"
        clusters = self._host.correlation_clusters.get(scope)
        if clusters is None:
            clusters = []
            self._host.correlation_clusters[scope] = clusters

        # ② 버퍼 상한 — 초과 시 oldest(first_ts 최소)부터 제거 + warning(침묵 금지).
        buffer_max = cfg.correlation_buffer_max
        if len(clusters) >= buffer_max:
            clusters.sort(key=lambda c: c.first_ts)
            overflow = len(clusters) - buffer_max + 1  # append 여지 1건 확보
            del clusters[:overflow]
            logger.warning(
                "상관 버퍼 상한 초과 — oldest %d건 제거: scope=%s max=%s",
                overflow,
                scope,
                buffer_max,
            )

        # sig_label은 domain 순수함수로 사전 산출(policy 순수성 — infra 미의존).
        hit = scan_signature_severity(getattr(event, "condition_log", "") or "")
        sig_label = hit[1] if hit else ""
        # (Plan 60 E7-d §17.6) 사이트/위치 토큰을 선택적 가중 차원으로 주입 — 워커가 domain 순수
        # 헬퍼로 산출해 signature_tokens의 extra로 넘긴다(correlation.py는 값만 소비·순수성 유지).
        # off(기본)면 extra="" → 현행 필드 Jaccard와 비트동일(회귀 0). 존 경계(B-6)는 scope로 불변.
        site_extra = ""
        if getattr(cfg, "correlation_site_dimension_enabled", False):
            site_extra = extract_site_token(
                getattr(event, "server_name", "") or "",
                getattr(event, "resource_name", "") or "",
            )
        # (plans/144 §4.4 · D-195 ② 개정) 제니퍼 정책 경로면 게이트웨이가 실어 보낸 was kind·
        # domain_id 값을 토큰으로 더한다 — 같은 도메인·같은 kind의 인스턴스 폭풍을 묶는다. 스코프는
        # 그대로 db_id(B-6). 폴스타·정책 off면 site_extra 그대로(비트 동일).
        if self._host.apm_noise_ctx is not None and is_apm_source(event):
            site_extra = " ".join(t for t in (site_extra, correlation_extra(event)) if t)
        tokens = signature_tokens(
            event.alarm_name, event.resource_type, sig_label, extra=site_extra
        )

        # 위상 가중(E4 인접성) — graph·event_node 해소 성공 시에만 adjacent 산출(그 외 None).
        topo_enabled = getattr(cfg, "correlation_topology_weight_enabled", False)
        topo_weight = cfg.correlation_topology_weight if topo_enabled else 0.0
        # domain.topology.id_of는 미타입이다(이동 전과 같음 · plans/177 W3).
        event_node = (
            graph.id_of(event.server_name)  # type: ignore[no-untyped-call]
            if graph is not None
            else None
        )
        adjacent: list[bool] | None = None
        if graph is not None and event_node is not None:
            max_hops = getattr(cfg, "topology_max_hops", 5)
            adjacent = [
                bool(
                    c.representative_node
                    and graph.is_related(
                        event_node, c.representative_node, max_hops=max_hops
                    )
                )
                for c in clusters
            ]

        idx, score = match_cluster(
            clusters,
            tokens,
            sim_threshold=cfg.correlation_sim_threshold,
            adjacent=adjacent,
            topo_weight=topo_weight,
        )
        if idx is not None:
            c = clusters[idx]
            c.member_count += 1
            c.last_ts = now
            correlated = c.member_count >= cfg.correlation_min_cluster_size
            if correlated:
                logger.debug(
                    "크로스-호스트 상관 억제: scope=%s 대표=%s member=%s sim=%.4f",
                    scope,
                    c.representative_fp,
                    c.member_count,
                    score,
                )
                # 감사 메타(decision_store 최상위 필드) — signals 동결 스키마 미훼손(§10,
                # E1 recurrence 전례). similarity는 위상 보너스 반영 후 최종 점수다.
                meta = {
                    "representative_fp": c.representative_fp,
                    "member_seq": c.member_count,
                    "similarity": round(score, 4),
                }
                return True, meta
            return False, None
        # 첫 도착 = 대표 → 통보(소급 선출 없음). 대표 노드(resource_id)를 해소해 저장.
        clusters.append(
            ClusterState(
                compute_fingerprint(event),
                tokens,
                now,
                now,
                representative_node=event_node,
            )
        )
        return False, None
