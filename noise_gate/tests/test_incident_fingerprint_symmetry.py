"""incident open↔resolved fingerprint 대칭성 / 운영지표 비오염 검증 (D-049, verifier 보완).

기존 D-049 테스트는 fingerprint를 하드코딩("fp"/"fp-x")해 비교하므로,
notifier가 발행하는 open 이벤트의 fingerprint와 worker가 발행하는 resolved 이벤트의
fingerprint가 **같은 알람에 대해 실제로 일치**하는지(=resolve 매칭 성립) 증명하지 못한다.
불일치 시 resolve_by_fingerprint가 영영 0건 → incident_mttr 환각 null(item 1 최상위 위험).

또한 decision_store.aggregate가 resolution 레코드를 suppress_ratio/actionable_ratio
같은 **파생 운영지표**까지 오염시키지 않는지(item 2 회귀 최상위)도 추가 단언한다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_notifier import _incident_open_payload
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    compute_fingerprint,
)
from noise_gate.infrastructure.decision_store import DecisionStore

REF = datetime(2026, 6, 30, 9, 0, 0)


def _event(*, is_clear: bool, severity: int) -> AlarmEvent:
    """동일 incident의 firing/clear 이벤트를 만든다(식별 필드는 동일, 상태만 다름)."""
    return AlarmEvent(
        db_id="polestar",
        server_name="srv-1",
        hostname="h-1",
        ip_address="10.0.0.1",
        resource_ancestry="/Servers",
        alarm_id="A-1",
        severity=severity,
        alarm_status="NOT_ACK" if not is_clear else "CLEAR",
        resource_type="server.Cpus",
        resource_name="svr-1-CPU",
        alarm_name="CPU 임계",
        alarm_time=REF,
        conditions="",
        condition_log="",
        is_clear=is_clear,
    )


def _make_result(event: AlarmEvent) -> AlarmAnalysisResult:
    return AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a", notification_channels=["workb"],
    )


# ─── item 1: open↔resolved fingerprint 대칭성 ─────────────────────────────────

def test_compute_fingerprint_stable_across_firing_and_clear():
    """firing(sev2)과 clear(sev0)는 식별 필드가 같으므로 fingerprint가 동일해야 한다.

    compute_fingerprint는 db_id/server|hostname/alarm_name/resource_name만 사용하고
    severity·is_clear·alarm_time을 포함하지 않으므로 상태 전이에 불변이다.
    """
    firing = _event(is_clear=False, severity=2)
    clear = _event(is_clear=True, severity=0)
    assert compute_fingerprint(firing) == compute_fingerprint(clear)


async def test_notifier_open_and_worker_resolved_payloads_share_fingerprint():
    """notifier open 이벤트 fingerprint == worker resolved 이벤트 fingerprint.

    - notifier: 실 파이프라인은 decide_notification이 decision.fingerprint를
      compute_fingerprint(firing)으로 채운다 → 그 값을 그대로 발행한다.
    - worker: _publish_incident_resolved에 compute_fingerprint(clear)를 넘긴다.
    두 payload의 fingerprint가 일치해야 subscriber의 resolve_by_fingerprint가 매칭된다.
    """
    firing = _event(is_clear=False, severity=2)
    clear = _event(is_clear=True, severity=0)

    # notifier open payload — decision.fingerprint는 실제 compute_fingerprint(firing)
    decision = NotificationDecision(
        tier=TIER_PAGE, reason="r", priority=320, signals={"severity": 2},
        fingerprint=compute_fingerprint(firing),
    )
    open_payload = _incident_open_payload(_make_result(firing), decision)

    # worker resolved payload — fingerprint는 worker가 _process에서 산출하는 compute_fingerprint(clear)
    worker = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
        enable_noise_gate=True, incident_tracking_enabled=True,
        incident_event_channel="alarm:incident",
    )))
    pub = AsyncMock()
    worker._incident_publisher = pub
    await worker._publish_incident_resolved(
        clear, compute_fingerprint(clear), self_heal=False
    )
    resolved_payload = pub.publish.await_args.args[0]

    assert open_payload["type"] == "open"
    assert resolved_payload["type"] == "resolved"
    # 핵심: 두 이벤트의 fingerprint가 동일 → open 행을 resolved로 매칭 가능(MTTR 산출 가능)
    assert open_payload["fingerprint"] == resolved_payload["fingerprint"]
    # 식별 필드도 일관(서버/알람) — subscriber 영속 시 동일 incident로 인식
    assert open_payload["server_name"] == resolved_payload["server_name"]
    assert open_payload["alarm_id"] == resolved_payload["alarm_id"]


# ─── item 2: resolution 레코드가 파생 운영지표를 오염시키지 않음 ──────────────

def _decision(tier: str) -> NotificationDecision:
    return NotificationDecision(
        tier=tier, reason="r", priority=300, signals={"severity": 2}, fingerprint="fp",
    )


def test_resolution_records_do_not_pollute_suppress_or_actionable_ratio(tmp_path):
    """decision N건 + resolution M건 혼재 시, 파생 비율 지표가 N(결정)만 반영한다.

    suppress_ratio=suppress/total, actionable_ratio=(page+ticket)/total 모두
    resolution 레코드가 total을 늘리지 않아야 정확하다(조용한 오염 방지).
    """
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    # 결정 4건: PAGE, TICKET, SUPPRESS, SUPPRESS  → total=4
    store.record(_decision(TIER_PAGE))
    store.record(_decision(TIER_TICKET))
    store.record(_decision(TIER_SUPPRESS))
    store.record(_decision(TIER_SUPPRESS))
    # resolution 3건(평균 60) — total/비율에 절대 영향 없어야 함
    store.record_resolution(fingerprint="a", duration_seconds=30.0)
    store.record_resolution(fingerprint="b", duration_seconds=60.0)
    store.record_resolution(fingerprint="c", duration_seconds=90.0)

    agg = store.aggregate()

    assert agg["total"] == 4                       # 결정 4건만(resolution 제외)
    assert agg["by_tier"] == {TIER_PAGE: 1, TIER_TICKET: 1, TIER_SUPPRESS: 2}
    assert agg["suppress_ratio"] == 0.5            # 2/4 — resolution 미오염
    assert agg["actionable_ratio"] == 0.5          # (page1+ticket1)/4
    assert agg["page_count"] == 1
    assert agg["auto_recovery_mttr_seconds"] == 60.0   # (30+60+90)/3
    # resolution 레코드는 last_event_ts에도 영향 없음(결정만 갱신)
    assert agg["last_event_ts"] is not None
