"""plans/197 W3(F3) — 제니퍼 알람 크로스-호스트 상관의 도메인 스코프 · DASHBOARD 상한.

A. `_detect_correlated_storm` 스코프: 플래그 on이면 제니퍼 이벤트는 `db_id|domain_id`로 나뉜다
   (다른 도메인은 군집 안 됨 · 같은 도메인은 군집 · 도메인 없음은 현행 db_id). 폴스타·off는 현행.
B. `decide_notification` step 7.5: 플래그 on + 제니퍼 + correlated → SUPPRESS 대신 DASHBOARD 상한
   (매트릭스 > DASHBOARD일 때만 · 이하면 올리지 않음). 폴스타·off는 SUPPRESS · 심각도3은 PAGE.

테스트 config는 SimpleNamespace로 검증 대상 필드를 명시한다(로컬 `.env` 누수 차단).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.config.alarm import NoiseGateConfig

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import (
    STAGE_CORRELATION,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
)
from noise_gate.infrastructure.apm_noise_context import ApmNoiseContext

REF = datetime(2026, 10, 27, 10, 0, 0)
_FLAG = "apm_correlation_domain_scope_enabled"


# ─── 합성 픽스처 ──────────────────────────────────────────────────────────────


def _apm_event(
    *,
    domain_id: object = 1000,
    instance: str = "was-a_8080",
    alarm_name: str = "SERVICE_EXCEPTION",
    severity: int = 2,
    db_id: str = "jennifer_default",
) -> AlarmEvent:
    """게이트웨이 `alarm:raw` 모양의 제니퍼 이벤트(같은 소스·같은 유형 · 도메인만 바꾼다)."""
    apm: dict[str, object] = {
        "source": "jennifer",
        "source_id": "default",
        "instance_id": 1001,
        "instance_name": instance,
        "event_type": alarm_name,
        "event_kind": "error",
        "level": "warning",
        "was_signals": [],
    }
    if domain_id is not None:
        apm["domain_id"] = domain_id
    server = instance.split("_")[0]
    return AlarmEvent(
        db_id=db_id,
        server_name=server,
        hostname=server,
        ip_address="192.0.2.11",
        resource_ancestry="JENNIFER > 도메인 > " + instance,
        alarm_id=f"jennifer:{instance}:{domain_id}",
        severity=severity,
        alarm_status="",
        resource_type="apm.Instance",
        resource_name=instance,
        alarm_name=alarm_name,
        alarm_time=REF,
        conditions=f"JENNIFER EVENT warning — {alarm_name}",
        condition_log="event (value=1)",
        is_clear=(severity == 0),
        raw_payload={"dbId": db_id, "source": "jennifer", "apm": apm},
    )


def _polestar_event(server: str = "srv-1", severity: int = 2) -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp",
        server_name=server,
        hostname=server,
        ip_address="192.0.2.21",
        resource_ancestry="",
        alarm_id=f"p:{server}",
        severity=severity,
        alarm_status="",
        resource_type="server.Server",
        resource_name="r1",
        alarm_name="CPU 사용률 임계 초과",
        alarm_time=REF,
        conditions="",
        condition_log="",
        is_clear=(severity == 0),
        raw_payload={"dbId": "polestar_cm_gp", "source": ""},
    )


def _worker(*, flag: bool, apm_policy: bool = True) -> AlarmWorker:
    w = AlarmWorker(
        SimpleNamespace(
            noise_gate=SimpleNamespace(
                correlation_window_seconds=120,
                correlation_sim_threshold=0.5,
                correlation_min_cluster_size=2,
                correlation_buffer_max=100,
                **{_FLAG: flag},
            )
        )
    )
    w._apm_noise_ctx = ApmNoiseContext.load() if apm_policy else None
    return w


def _pconfig(*, flag: bool, **kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = dict(
        suppress_max_severity=2,
        importance_value_map={},
        resolved_to_dashboard=False,
        cross_host_correlation_enabled=True,
        **{_FLAG: flag},
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _pctx(importance_id: object = None) -> dict:
    return {
        "importance_id": importance_id,
        "maintenance": False,
        "noti_policy": None,
        "parent_avail_status": None,
        "source": "polestar_db",
    }


def _decide(event: AlarmEvent, *, flag: bool, ctx: dict | None = None, **cfg: object):
    return decide_notification(
        event, None, None, ctx or _pctx(), _pconfig(flag=flag, **cfg), correlated=True
    )


# ═════════════════════════════════════════════════════════════════════════════
# 0. 설정 기본값
# ═════════════════════════════════════════════════════════════════════════════


def test_flag_default_off():
    assert NoiseGateConfig.model_fields[_FLAG].default is False


# ═════════════════════════════════════════════════════════════════════════════
# A. 스코프
# ═════════════════════════════════════════════════════════════════════════════


class TestScope:
    def test_flag_on_other_domain_not_clustered(self):
        w = _worker(flag=True)
        a = _apm_event(domain_id=1000, instance="was-a_8080")
        b = _apm_event(domain_id=2000, instance="was-b_8080")
        assert w._detect_correlated_storm(a, 1000.0) == (False, None)
        assert w._detect_correlated_storm(b, 1001.0) == (False, None)
        assert set(w._correlation_clusters) == {
            "jennifer_default|1000",
            "jennifer_default|2000",
        }

    def test_flag_on_same_domain_clustered(self):
        w = _worker(flag=True)
        a = _apm_event(domain_id=1000, instance="was-a_8080")
        b = _apm_event(domain_id=1000, instance="was-b_8080")
        assert w._detect_correlated_storm(a, 1000.0) == (False, None)
        correlated, meta = w._detect_correlated_storm(b, 1001.0)
        assert correlated is True and meta is not None
        assert set(w._correlation_clusters) == {"jennifer_default|1000"}

    def test_domain_normalized_like_correlation_token(self):
        # 문자열·숫자 domain_id가 같은 스코프 — correlation_extra의 apmdomain 토큰과 같은 정규화.
        w = _worker(flag=True)
        w._detect_correlated_storm(_apm_event(domain_id=1000, instance="was-a_8080"), 1000.0)
        correlated, _ = w._detect_correlated_storm(
            _apm_event(domain_id="1000", instance="was-b_8080"), 1001.0
        )
        assert correlated is True

    def test_flag_on_without_domain_uses_db_id_scope(self):
        w = _worker(flag=True)
        for domain in (None, "", True):
            w._correlation_clusters.clear()
            w._detect_correlated_storm(_apm_event(domain_id=domain), 1000.0)
            assert set(w._correlation_clusters) == {"jennifer_default"}

    def test_flag_on_scope_independent_of_policy_path(self):
        # 정책 경로(apm_noise_ctx) off여도 스코프 판정은 플래그 + 제니퍼 여부로만.
        w = _worker(flag=True, apm_policy=False)
        w._detect_correlated_storm(_apm_event(domain_id=1000, instance="was-a_8080"), 1000.0)
        w._detect_correlated_storm(_apm_event(domain_id=2000, instance="was-b_8080"), 1001.0)
        assert set(w._correlation_clusters) == {
            "jennifer_default|1000",
            "jennifer_default|2000",
        }

    def test_polestar_scope_unchanged_with_flag_on(self):
        on, off = _worker(flag=True), _worker(flag=False)
        for w in (on, off):
            assert w._detect_correlated_storm(_polestar_event("srv-1"), 1000.0) == (False, None)
            correlated, _ = w._detect_correlated_storm(_polestar_event("srv-2"), 1001.0)
            assert correlated is True
            assert set(w._correlation_clusters) == {"polestar_cm_gp"}

    def test_flag_off_other_domain_still_clustered(self):
        # 결함 재현(off = 현행 비트 동일): 도메인 토큰 1개 차이 4/6 ≥ 0.5 → 한 군집.
        w = _worker(flag=False)
        w._detect_correlated_storm(_apm_event(domain_id=1000, instance="was-a_8080"), 1000.0)
        correlated, meta = w._detect_correlated_storm(
            _apm_event(domain_id=2000, instance="was-b_8080"), 1001.0
        )
        assert correlated is True and meta is not None
        assert set(w._correlation_clusters) == {"jennifer_default"}


# ═════════════════════════════════════════════════════════════════════════════
# B. 정책 step 7.5
# ═════════════════════════════════════════════════════════════════════════════


class TestPolicy:
    def test_flag_on_apm_capped_to_dashboard(self):
        d = _decide(_apm_event(), flag=True)
        assert d.tier == TIER_DASHBOARD
        assert d.stage == STAGE_CORRELATION
        assert "제니퍼 군집 대표 외 대시보드 강등" in d.reason
        assert f"매트릭스 {TIER_TICKET}" in d.reason
        assert d.evidence["capped_from"] == TIER_TICKET

    def test_flag_on_apm_low_matrix_not_raised(self):
        # 매트릭스가 DASHBOARD 이하이면 step 7.5는 통과 — 티어를 올리지 않는다.
        ev = _apm_event(severity=1)
        off_corr = decide_notification(
            ev, None, None, _pctx(), _pconfig(flag=True), correlated=False
        )
        assert off_corr.tier in (TIER_DASHBOARD, TIER_SUPPRESS)
        d = _decide(ev, flag=True)
        assert d.tier == off_corr.tier
        assert d.stage != STAGE_CORRELATION

    def test_flag_off_apm_suppressed(self):
        d = _decide(_apm_event(), flag=False)
        assert d.tier == TIER_SUPPRESS
        assert d.reason == "크로스-호스트 상관 — 클러스터 대표 외 억제"

    def test_polestar_suppressed_with_flag_on(self):
        d = _decide(_polestar_event(), flag=True)
        assert d.tier == TIER_SUPPRESS
        assert d.reason == "크로스-호스트 상관 — 클러스터 대표 외 억제"

    def test_test_double_event_treated_as_non_apm(self):
        ev = SimpleNamespace(
            severity=2, is_clear=False, db_id="db1", server_name="srv-1",
            alarm_name="CPU", resource_name="r1", hostname="h1",
        )
        assert _decide(ev, flag=True).tier == TIER_SUPPRESS  # type: ignore[arg-type]

    def test_severity3_still_pages(self):
        d = _decide(_apm_event(severity=3), flag=True)
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_cross_host_off_ignores_flag(self):
        d = _decide(_apm_event(), flag=True, cross_host_correlation_enabled=False)
        assert d.tier == TIER_TICKET
