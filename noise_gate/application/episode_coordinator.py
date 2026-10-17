"""크로스소스 사건(episode) 코디네이터 (plans/177 W2).

`AlarmWorker`에서 사건 관찰·티어 기록, 사후 승격 후보 보관·sweep·통보(plans/144 W4 §5.6),
사건 종합 조사 대표 후보 보관·제출(plans/153 W4 §4.5) 책임을 옮긴다. 본문은
`AlarmWorker._<이름>`에서 동작 그대로 옮겼고, 워커에는 같은 이름의 위임 메서드가 남아
`_process`·테스트 seam이 된다.

상태(`_episodes`·`_episode_ctx`·`_late_promotion`·`_late_promotion_swept_at`)·협력자·상한
상수·발송 함수는 워커와 `alarm_worker` 모듈이 계속 소유한다 — 테스트·하네스가 생성 뒤 워커
속성과 모듈 전역을 바꿔 끼우므로, 코디네이터는 `EpisodeHost`를 거쳐 **접근 시점**의 값을 읽고
쓴다(생성 시점에 붙잡지 않는다).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from noise_gate.application.nodes.alarm_notifier import late_promotion_reason
from noise_gate.application.server_identity import is_apm_source
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.cross_source import (
    MODE_ANNOTATE,
    MODE_ENFORCE,
    Episode,
    EpisodeTracker,
    apm_alarms_in_window,
    app_impact_from_alarms,
    episode_investigation_due,
    episode_snapshot,
    late_promotion_targets,
)
from noise_gate.domain.notification_policy import STAGE_MATRIX, TIER_DASHBOARD, TIER_TICKET
from noise_gate.infrastructure.cross_source_rules import episode_alarm

# 이동 전과 같은 로거 이름 — 운영 로그 `%(name)s`와 테스트 caplog 로거 필터를 보존한다.
logger = logging.getLogger("noise_gate.application.alarm_worker")

# 후보 전수 sweep(사건 종료·해소·승격 정리) 최소 간격(초) — 메모리 위생용이라 정확성과 무관하다
# (대상 판정은 late_promotion_targets가 멤버 상태로 다시 거른다). 상한 검사는 매번 한다.
_LATE_PROMOTION_SWEEP_SECONDS = 60.0
# 후속 알람을 묶지 않는 종합 조사 저장 상태(조사 저장소 `normalize_status` 결과 — 종결 실패).
_EPISODE_INV_UNUSABLE = frozenset({"rejected", "failed", "timeout"})


class EpisodeHost(Protocol):
    """코디네이터가 접근 시점에 읽고 쓰는 워커 쪽 상태·협력자·seam (plans/177 W2)."""

    @property
    def episodes(self) -> EpisodeTracker | None: ...

    @property
    def episode_ctx(self) -> dict[str, tuple[str, Any, Any, float]]: ...

    @property
    def late_promotion(self) -> dict[str, tuple[str, Any, Any, float]]: ...

    late_promotion_swept_at: float

    @property
    def config(self) -> Any: ...

    @property
    def decision_store(self) -> Any: ...

    @property
    def investigation_store(self) -> Any: ...

    @property
    def sre_agent_client(self) -> Any: ...

    @property
    def incident_publisher(self) -> Any: ...

    @property
    def late_promotion_max(self) -> int: ...

    @property
    def late_promotion_per_trigger_max(self) -> int: ...

    @property
    def episode_ctx_max(self) -> int: ...

    @property
    def send_late_promotion(self) -> Callable[..., Awaitable[dict[str, bool]]]: ...

    @property
    def submit_episode_investigation(self) -> Callable[..., Awaitable[dict[str, Any]]]: ...

    @property
    def spawn_episode_investigation_followup(self) -> Callable[..., Any]: ...


class EpisodeCoordinator:
    """크로스소스 사건 관찰 · 사후 승격 · 사건 종합 조사를 조율한다 (plans/177 W2)."""

    def __init__(self, host: EpisodeHost) -> None:
        """워커 쪽 접근점 `host`를 받는다 — 값은 메서드가 부를 때마다 host에서 읽는다."""
        self._host = host

    def observe_episode(
        self, event: AlarmEvent, fingerprint: str, now: float
    ) -> dict[str, Any] | None:
        """게이트로 가는 알람을 사건에 반영한다 — 해소는 멤버 해소 처리, 그 밖은 크로스소스 신호."""
        if self._host.episodes is None:
            return None
        alarm = episode_alarm(event, fingerprint=fingerprint, is_apm=is_apm_source(event))
        if event.is_clear:
            self._host.episodes.resolve(alarm, now)
            return None
        return self._host.episodes.observe(alarm, now)

    def record_episode_tier(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any
    ) -> None:
        """그래프 반환값의 최종 티어를 사건 멤버에 기록한다(원인 조건 ④ 재료)."""
        if self._host.episodes is None:
            return
        decision = result.get("notification_decision") if isinstance(result, dict) else None
        self._host.episodes.record_tier(
            str(cross_source.get("episode_id") or ""),
            event.alarm_id,
            getattr(decision, "tier", None),
        )

    def episode_of(self, cross_source: dict[str, Any] | None) -> Episode | None:
        """크로스소스 신호의 사건(열려 있지 않으면 None)."""
        if self._host.episodes is None or not cross_source:
            return None
        return self._host.episodes.get(str(cross_source.get("episode_id") or ""))

    def late_promotion_on(self) -> bool:
        """사후 승격 활성 — 플래그 AND app_impact_enabled AND 모드 ∈ {annotate, enforce}.

        shadow는 판정·통보 불변이 원칙이라 재통보(PAGE)를 내지 않는다 — 사건 저장소 우선 조회와
        같은 모드 범위다.
        """
        gate = self._host.config.noise_gate
        return (
            self._host.episodes is not None
            and self._host.episodes.mode in (MODE_ANNOTATE, MODE_ENFORCE)
            and bool(getattr(gate, "app_impact_late_promotion_enabled", False))
            and bool(getattr(gate, "app_impact_enabled", False))
        )

    def episode_app_impact(
        self, cross_source: dict[str, Any] | None, event: AlarmEvent
    ) -> dict[str, Any] | None:
        """폴스타 알람의 앱 영향을 사건 저장소에서 미리 산출한다 (plans/144 W4 §5.6).

        cross_source_mode ∈ {annotate, enforce} AND app_impact_enabled이고 사건에 붙은 폴스타
        알람일 때만 `{app_impact, window_events, window_minutes}`를 돌려준다. shadow·off는 None —
        게이트 노드가 현행 게이트웨이 조회만 한다(판정 비트 동일 · 사건 저장소가 더 승격하지
        않는다).
        """
        gate = self._host.config.noise_gate
        if (
            self._host.episodes is None
            or self._host.episodes.mode not in (MODE_ANNOTATE, MODE_ENFORCE)
            or not getattr(gate, "app_impact_enabled", False)
            or is_apm_source(event)
        ):
            return None
        ep = self.episode_of(cross_source)
        member = ep.member(event.alarm_id) if ep is not None else None
        if ep is None or member is None:
            return None
        window = int(getattr(gate, "app_impact_window_minutes", 10))
        alarms = apm_alarms_in_window(ep, member.alarm.occurred_at, window * 60)
        return {
            "app_impact": app_impact_from_alarms(alarms),
            "window_events": len(alarms),
            "window_minutes": window,
        }

    def episode_summary(self, cross_source: dict[str, Any] | None) -> dict[str, Any] | None:
        """통보문 사건 묶음 표시용 요약 `{episode_id, member_count}` (plans/144 W5 · §5.4).

        annotate·enforce에서 사건에 붙은 알람일 때만 만든다(off·shadow는 None → 통보 본문 비트
        동일). member_count는 이 알람을 포함한 현재 멤버 수다(멤버 상한으로 잘린 만큼은 빠진다).
        판정에는 쓰지 않는다 — notifier가 표시에만 쓴다.
        """
        if self._host.episodes is None or self._host.episodes.mode not in (
            MODE_ANNOTATE, MODE_ENFORCE
        ):
            return None
        ep = self.episode_of(cross_source)
        if ep is None:
            return None
        return {"episode_id": ep.id, "member_count": len(ep.members)}

    def episode_investigation(
        self, cross_source: dict[str, Any] | None, event: AlarmEvent
    ) -> dict[str, Any] | None:
        """같은 사건에서 APM 알람이 이미 제출한 조사(plans/144 §4.5) — APM 알람에만.

        (plans/153 §4.5) 사건 종합 조사가 실제로 제출돼 조사 id가 있고 종결 실패가 아니면 그 id가
        우선한다 — 제니퍼 후속 알람은 종합 조사로 연결된다(`episode_existing`). 종합 조사 시도가
        실패했으면(id 없음 · 저장소상 거부·실패·타임아웃) 종전 동작(사건의 기존 단독 조사 id, 없으면
        제출)으로 돌아간다. 종합 조사는 플래그 on에서만 생기므로 off면 종전과 같다.
        """
        if not is_apm_source(event):
            return None
        ep = self.episode_of(cross_source)
        if ep is None:
            return None
        inv = self.usable_episode_investigation(ep) or ep.apm_investigation_id
        if not inv:
            return None
        return {"episode_id": ep.id, "investigation_id": inv}

    def usable_episode_investigation(self, ep: Episode) -> str:
        """연결해도 되는 사건 종합 조사 id — 제출 성공(id 있음)이고 저장소상 종결 실패가 아닐 때만.

        submit은 받았지만 조사 서비스가 뒤에 거부·실패·타임아웃으로 끝낸 조사(후속 poll이 저장소에
        남긴 상태)에는 후속 알람을 묶지 않는다 — 묶으면 그 알람은 아무 조사도 받지 못한다.
        """
        inv = ep.episode_investigation_id
        if not inv or ep.episode_investigation_status != "submitted":
            return ""
        store = self._host.investigation_store
        if store is None:
            return inv
        try:
            rec = store.get_by_investigation(inv)
        except Exception:  # noqa: BLE001 — 조회 실패는 제출 성공 사실로 판단한다
            logger.warning("종합 조사 상태 조회 실패(연결 유지): inv=%s", inv, exc_info=True)
            return inv
        status = str((rec or {}).get("status") or "")
        return "" if status in _EPISODE_INV_UNUSABLE else inv

    def episode_context_on(self) -> bool:
        """사건 맥락 활성 — 플래그 AND 조사 트리거 AND 추적기(cross_source_mode≠off · §4.4)."""
        gate = self._host.config.noise_gate
        return (
            self._host.episodes is not None
            and bool(getattr(gate, "investigation_episode_context_enabled", False))
            and bool(getattr(gate, "investigation_trigger_enabled", False))
        )

    def episode_snapshot(self, cross_source: dict[str, Any] | None) -> dict[str, Any] | None:
        """조사 페이로드용 제출 시점 사건 스냅샷(사본) — 사건 맥락 off·사건 미소속이면 None."""
        if not self.episode_context_on() or self._host.episodes is None:
            return None
        ep = self.episode_of(cross_source)
        return episode_snapshot(ep, self._host.episodes.rules) if ep is not None else None

    def remember_episode_ctx(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any, now: float
    ) -> None:
        """이 알람의 분석 결과·판정을 종합 조사 대표 후보로 보관한다(§4.5 · 상한 가드)."""
        if not isinstance(result, dict):
            return
        decision = result.get("notification_decision")
        analysis = result.get("analysis_result")
        if decision is None or analysis is None:
            return
        self._host.episode_ctx[event.alarm_id] = (
            str(cross_source.get("episode_id") or ""), analysis, decision, now
        )
        if len(self._host.episode_ctx) <= self._host.episode_ctx_max or self._host.episodes is None:
            return
        for alarm_id, (episode_id, *_rest) in list(self._host.episode_ctx.items()):
            if self._host.episodes.get(episode_id) is None:
                del self._host.episode_ctx[alarm_id]
        overflow = len(self._host.episode_ctx) - self._host.episode_ctx_max
        if overflow > 0:
            oldest = sorted(self._host.episode_ctx, key=lambda k: self._host.episode_ctx[k][3])
            for alarm_id in oldest[:overflow]:
                del self._host.episode_ctx[alarm_id]
            logger.warning(
                "종합 조사 대표 후보 상한(%d) 초과 — 오래된 %d건 정리",
                self._host.episode_ctx_max, overflow,
            )

    async def maybe_episode_investigation(
        self, cross_source: dict[str, Any], event: AlarmEvent
    ) -> None:
        """사건 종합 조사를 조건이 처음 성립할 때 1회 제출한다 (plans/153 §4.5 · G-6 (a)).

        조건은 `episode_investigation_due`(두 소스 멤버 · 링크 ≥1 · 통보 멤버 ≥1 · 미시도). 대표
        알람은 사건의 대표(먼저 쓰인 원인) — 그 알람의 결과가 보관돼 있지 않으면 지금 알람이다.
        시도는 성패와 무관하게 사건당 1회다(재시도 폭주 방지 · 사유는 감사·저장소). 판정은 바꾸지
        않는다.
        """
        ep = self.episode_of(cross_source)
        if ep is None or not episode_investigation_due(ep):
            return
        client = self._host.sre_agent_client
        if client is None or self._host.episodes is None:
            return
        rep_id = ep.representative_id
        ctx = self._host.episode_ctx.get(rep_id)
        if ctx is None or ctx[0] != ep.id:
            rep_id = event.alarm_id
            ctx = self._host.episode_ctx.get(rep_id)
        if ctx is None:
            return
        _, analysis, decision, _ = ctx
        ep.episode_investigation_status = "attempted"
        alarm_ids = [rep_id] + [
            m.alarm.alarm_id for m in ep.members if m.alarm.alarm_id != rep_id
        ]
        outcome = await self._host.submit_episode_investigation(
            client, analysis.alarm_event, decision,
            episode_snapshot(ep, self._host.episodes.rules), alarm_ids,
            cfg=self._host.config, decision_store=self._host.decision_store,
            inv_store=self._host.investigation_store,
        )
        ep.episode_investigation_status = str(outcome.get("status") or "attempted")
        if outcome.get("investigation_id") and outcome.get("status") == "submitted":
            ep.episode_investigation_id = str(outcome["investigation_id"])
        for alarm_id in [k for k, v in self._host.episode_ctx.items() if v[0] == ep.id]:
            del self._host.episode_ctx[alarm_id]
        pending = outcome.get("pending")
        if pending:
            self._host.spawn_episode_investigation_followup(
                pending, analysis, self._host.config, self._host.config.noise_gate,
                self._host.decision_store, self._host.investigation_store,
            )

    def record_apm_investigation(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any
    ) -> None:
        """APM 알람이 제출한 조사 id를 사건에 기록한다 — 사건당 1회 제출의 기준(§4.5)."""
        if not is_apm_source(event) or not isinstance(result, dict):
            return
        pending = result.get("investigation_pending")
        inv = result.get("investigation_id") or (
            pending.get("investigation_id") if isinstance(pending, dict) else None
        )
        ep = self.episode_of(cross_source)
        if inv and ep is not None and not ep.apm_investigation_id:
            ep.apm_investigation_id = str(inv)

    def remember_late_candidate(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any, now: float
    ) -> None:
        """매트릭스 DASHBOARD·TICKET 폴스타 판정을 사후 승격 후보로 보관한다(§5.6 · G-6)."""
        if is_apm_source(event) or not isinstance(result, dict) or event.severity >= 3:
            return
        decision = result.get("notification_decision")
        analysis = result.get("analysis_result")
        if (
            decision is None
            or analysis is None
            or getattr(decision, "stage", "") != STAGE_MATRIX
            or getattr(decision, "tier", "") not in (TIER_DASHBOARD, TIER_TICKET)
        ):
            return
        self._host.late_promotion[event.alarm_id] = (
            str(cross_source.get("episode_id") or ""), analysis, decision, now
        )
        self.sweep_late_promotion(now)

    def sweep_late_promotion(self, now: float) -> None:
        """사건이 닫혔거나 멤버가 해소·승격된 후보를 지우고 상한을 지킨다.

        전수 순회는 `_LATE_PROMOTION_SWEEP_SECONDS`마다 1회만 한다(후보 추가마다 돌지 않는다).
        상한 검사는 매번 한다.
        """
        if self._host.episodes is None:
            self._host.late_promotion.clear()
            return
        if now - self._host.late_promotion_swept_at >= _LATE_PROMOTION_SWEEP_SECONDS:
            self._host.late_promotion_swept_at = now
            for alarm_id, (episode_id, *_rest) in list(self._host.late_promotion.items()):
                ep = self._host.episodes.get(episode_id)
                member = ep.member(alarm_id) if ep is not None else None
                if member is None or member.resolved or member.late_promoted:
                    del self._host.late_promotion[alarm_id]
        overflow = len(self._host.late_promotion) - self._host.late_promotion_max
        if overflow > 0:
            oldest = sorted(
                self._host.late_promotion, key=lambda k: self._host.late_promotion[k][3]
            )
            for alarm_id in oldest[:overflow]:
                del self._host.late_promotion[alarm_id]
            logger.warning(
                "사후 승격 후보 상한(%d) 초과 — 오래된 %d건 정리",
                self._host.late_promotion_max, overflow,
            )

    async def late_promote(
        self, cross_source: dict[str, Any], event: AlarmEvent, now: float
    ) -> None:
        """APM 심각 이벤트가 붙은 사건의 낮게 판정된 폴스타 알람을 PAGE로 다시 통보한다.

        plans/144 W4 §5.6 · G-6 (a) — 알람당 1회(멤버 `late_promoted`) · 사건이 열려 있을 때만 ·
        원 판정 감사 레코드는 고치지 않고 `type="late_promotion"` 레코드를 따로 남긴다.
        트리거 1건당 `_LATE_PROMOTION_PER_TRIGGER_MAX`건까지만 보낸다 — 넘친 대상은 표시하지 않고
        후보로 남긴다(알람당 최대 1회는 그대로 · 다음 트리거에 기회).
        """
        if not is_apm_source(event):
            return
        ep = self.episode_of(cross_source)
        trigger = ep.member(event.alarm_id) if ep is not None else None
        if ep is None or trigger is None:
            return
        self.sweep_late_promotion(now)
        window = int(getattr(self._host.config.noise_gate, "app_impact_window_minutes", 10)) * 60
        promoted = skipped = 0
        for member in late_promotion_targets(ep, trigger.alarm, window):
            alarm_id = member.alarm.alarm_id
            cand = self._host.late_promotion.get(alarm_id)
            if cand is None or cand[0] != ep.id:
                continue  # 매트릭스 판정이 아니었거나 결과가 없다 — 대상 아님
            if promoted >= self._host.late_promotion_per_trigger_max:
                skipped += 1  # 표시하지 않는다 — 후보로 남아 다음 트리거에 기회
                continue
            del self._host.late_promotion[alarm_id]
            promoted += 1
            _, analysis, decision, _ = cand
            member.late_promoted = True  # 발송 성패와 무관하게 1회 — 재시도 폭주 방지
            reason = late_promotion_reason(trigger.alarm.alarm_name, ep.id)
            promotion = {
                "episode_id": ep.id,
                "trigger_alarm_id": trigger.alarm.alarm_id,
                "trigger_event_type": trigger.alarm.alarm_name,
                "trigger_level": trigger.alarm.level,
                "from_tier": str(getattr(decision, "tier", "")),
                "reason": reason,
            }
            try:
                sent = await self._host.send_late_promotion(
                    analysis, decision, promotion, self._host.config,
                    self._host.incident_publisher,
                )
            except Exception:  # noqa: BLE001 — 사후 통보 실패가 다음 알람 처리를 막지 않는다
                logger.warning("사후 승격 통보 실패: alarm_id=%s", alarm_id, exc_info=True)
                sent = {}
            logger.info(
                "사후 승격 통보: alarm_id=%s %s→page episode=%s trigger=%s sent=%s",
                alarm_id, promotion["from_tier"], ep.id, trigger.alarm.alarm_id, sent,
            )
            if self._host.decision_store is not None:
                try:
                    self._host.decision_store.record_late_promotion(
                        alarm_id=alarm_id,
                        fingerprint=str(getattr(decision, "fingerprint", "") or ""),
                        episode_id=ep.id,
                        trigger_alarm_id=trigger.alarm.alarm_id,
                        trigger_event_type=trigger.alarm.alarm_name,
                        trigger_level=trigger.alarm.level,
                        from_tier=promotion["from_tier"],
                        reason=reason,
                        sent=sent,
                    )
                except Exception:  # noqa: BLE001 — 감사 실패는 무시(통보는 이미 나갔다)
                    logger.warning("사후 승격 감사 기록 실패(무시): alarm_id=%s", alarm_id)
        if skipped:
            logger.warning(
                "사후 승격 트리거당 상한(%d) 초과 — %d건 건너뜀(후보 유지 · 다음 트리거에 재시도): "
                "episode=%s trigger=%s",
                self._host.late_promotion_per_trigger_max, skipped, ep.id,
                trigger.alarm.alarm_id,
            )

    async def after_episode_graph(
        self, cross_source: dict[str, Any], event: AlarmEvent, result: Any, now: float
    ) -> None:
        """그래프 뒤 사건 후처리(W4) — 조사 id 기록 · 사후 승격 후보 보관 · 사후 승격 통보.

        각 신호는 독립이라 개별 try/except로 감싼다 — 하나의 실패가 다른 것을 막지 않고, 이미 끝난
        알람 처리를 dead-letter로 돌리지 않는다(사유는 경고 로그).
        """
        try:
            self.record_apm_investigation(cross_source, event, result)
        except Exception:  # noqa: BLE001
            logger.warning("사건 조사 id 기록 실패: alarm_id=%s", event.alarm_id, exc_info=True)
        # (plans/153 W4 §4.5) 사건 종합 조사 — 플래그 off면 진입하지 않는다(비트 동일).
        if self.episode_context_on():
            try:
                self.remember_episode_ctx(cross_source, event, result, now)
                await self.maybe_episode_investigation(cross_source, event)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "사건 종합 조사 처리 실패: alarm_id=%s", event.alarm_id, exc_info=True
                )
        if not self.late_promotion_on():
            return
        try:
            self.remember_late_candidate(cross_source, event, result, now)
        except Exception:  # noqa: BLE001
            logger.warning("사후 승격 후보 보관 실패: alarm_id=%s", event.alarm_id, exc_info=True)
        try:
            await self.late_promote(cross_source, event, now)
        except Exception:  # noqa: BLE001
            logger.warning("사후 승격 처리 실패: alarm_id=%s", event.alarm_id, exc_info=True)
