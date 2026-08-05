"""Plan 60 E4 — 다홉 토폴로지 연쇄 억제 통합 테스트 (§6.2 하이브리드 · B-5).

세 층을 결정적으로 검증한다:
  1. 게이트 step6.4(`decide_notification`): cascaded + root_notified 하이브리드 분기,
     1홉 폴백, off 무변경, 심각도3 단락 유지.
  2. enricher `_compute_root_notified`: active_firings 재사용으로 root 통보 판정.
  3. repo.fetch(multi_hop): 엣지 로드 → 조상 BFS → 비정상 상태 → cascaded/root 산출,
     비PostgreSQL(b0)·리소스 미해소 시 미산출(None → 1홉 폴백).

event/config/noise_ctx는 정책 모듈이 덕 타이핑으로 소비하므로 SimpleNamespace/dict로 구성한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes.alarm_context_enricher import _compute_root_notified
from noise_gate.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
)
from noise_gate.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
)
from src.config import AlarmConfig

REF = datetime(2026, 7, 21, 10, 0, 0)


def make_event(severity: int = 2, *, is_clear=None, **kwargs) -> SimpleNamespace:
    if is_clear is None:
        is_clear = severity == 0
    base = dict(
        severity=severity,
        is_clear=is_clear,
        db_id="polestar_cm_gp",
        server_name="web-01",
        alarm_name="CPU 임계",
        resource_name="web-01-CPU",
        hostname="h1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_config(**kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2,
        importance_value_map={},
        resolved_to_dashboard=False,
        dependency_suppression=False,
        multi_hop_cascade_enabled=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx(**kwargs) -> dict:
    # importance_id=None → '보통' → sev2 매트릭스 TICKET(미억제 기준 티어).
    base = dict(
        importance_id=None,
        maintenance=False,
        noti_policy=None,
        parent_avail_status=None,
        cascaded=None,
        root_resource=None,
        root_resource_name=None,
        source="polestar_db",
    )
    base.update(kwargs)
    return base


# ── 1. 게이트 step6.4 하이브리드 ────────────────────────────────
class TestGateHybrid:
    def test_cascaded_root_notified_suppress(self):
        d = decide_notification(
            make_event(2), None, None,
            make_ctx(cascaded=True, root_notified=True, root_resource="root"),
            make_config(dependency_suppression=True, multi_hop_cascade_enabled=True),
        )
        assert d.tier == TIER_SUPPRESS
        assert "다홉" in d.reason and "통보됨" in d.reason
        assert d.signals["cascaded"] is True
        assert d.signals["root_resource"] == "root"

    def test_cascaded_root_not_notified_dashboard(self):
        d = decide_notification(
            make_event(2), None, None,
            make_ctx(cascaded=True, root_notified=False, root_resource="root"),
            make_config(dependency_suppression=True, multi_hop_cascade_enabled=True),
        )
        assert d.tier == TIER_DASHBOARD
        assert "다홉" in d.reason and "미통보" in d.reason

    def test_cascaded_missing_falls_back_to_1hop_suppress(self):
        # cascaded 미제공(1홉 모드·수집 실패) + 부모 비정상 → 현행 1홉 SUPPRESS.
        d = decide_notification(
            make_event(2), None, None,
            make_ctx(cascaded=None, parent_avail_status=1),
            make_config(dependency_suppression=True, multi_hop_cascade_enabled=True),
        )
        assert d.tier == TIER_SUPPRESS
        assert "부모 리소스 비정상" in d.reason  # 1홉 사유(다홉 아님)

    def test_cascaded_missing_1hop_none_not_suppressed(self):
        # cascaded 미제공 + 부모 None → 보수적 미억제(매트릭스 TICKET).
        d = decide_notification(
            make_event(2), None, None,
            make_ctx(cascaded=None, parent_avail_status=None),
            make_config(dependency_suppression=True, multi_hop_cascade_enabled=True),
        )
        assert d.tier == TIER_TICKET

    def test_dependency_off_no_suppression_regression(self):
        # dependency_suppression=False → cascaded여도 미억제(회귀 0).
        d = decide_notification(
            make_event(2), None, None,
            make_ctx(cascaded=True, root_notified=True),
            make_config(dependency_suppression=False, multi_hop_cascade_enabled=True),
        )
        assert d.tier == TIER_TICKET
        assert "의존성" not in d.reason

    def test_severity3_short_circuits_cascade(self):
        # 심각도3은 step3에서 단락 → cascaded여도 항상 PAGE(단락 불변).
        d = decide_notification(
            make_event(3), None, None,
            make_ctx(cascaded=True, root_notified=True),
            make_config(dependency_suppression=True, multi_hop_cascade_enabled=True),
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_correlated_signal_reflected(self):
        # E2 자리예약 — correlated 인자가 signals에 반영(로직 없음).
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(dependency_suppression=True),
            correlated=True,
        )
        assert d.signals["correlated"] is True
        d2 = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(dependency_suppression=True),
        )
        assert d2.signals["correlated"] is False


# ── 2. enricher root_notified 산출 ─────────────────────────────
class TestComputeRootNotified:
    def test_root_active_within_window_true(self):
        ev = make_event(2)
        ctx = make_ctx(root_resource_name="root-srv")
        # active_firings: scope "db|root-srv"가 window 내 활성(ts=990, now=1000, window=300).
        firings = {"polestar_cm_gp|root-srv": (2, 990.0, "AlarmX")}
        assert _compute_root_notified(ev, ctx, firings, 300, now=1000.0) is True

    def test_root_expired_window_false(self):
        ev = make_event(2)
        ctx = make_ctx(root_resource_name="root-srv")
        firings = {"polestar_cm_gp|root-srv": (2, 500.0, "AlarmX")}  # 500초 경과 > 300
        assert _compute_root_notified(ev, ctx, firings, 300, now=1000.0) is False

    def test_no_root_scope_false(self):
        ev = make_event(2)
        ctx = make_ctx(root_resource_name="root-srv")
        firings = {"polestar_cm_gp|other-srv": (2, 999.0, "AlarmX")}
        assert _compute_root_notified(ev, ctx, firings, 300, now=1000.0) is False

    def test_active_firings_none_false(self):
        ev = make_event(2)
        ctx = make_ctx(root_resource_name="root-srv")
        assert _compute_root_notified(ev, ctx, None, 300, now=1000.0) is False

    def test_root_name_absent_false(self):
        ev = make_event(2)
        ctx = make_ctx(root_resource_name=None)
        firings = {"polestar_cm_gp|root-srv": (2, 999.0, "AlarmX")}
        assert _compute_root_notified(ev, ctx, firings, 300, now=1000.0) is False


# ── 3. repo.fetch(multi_hop) 산출 ──────────────────────────────
class _MultiHopFakeClient:
    """엣지/상태/리소스ID/noti SQL을 내용으로 분기(다홉 산출 통합용)."""

    def __init__(self, resource_rows, edge_rows, status_rows):
        self._resource_rows = resource_rows
        self._edge_rows = edge_rows
        self._status_rows = status_rows
        self.executed_sqls: list[str] = []

    async def execute_sql(self, sql):
        self.executed_sqls.append(sql)
        if "ID IN (" in sql:                        # 조상 상태 신선 조회(IN 절)
            rows = self._status_rows
        elif "AVAIL_DEPEND_RESOURCE_ID_2" in sql:   # 엣지 로더(보조 의존 컬럼 SELECT)
            rows = self._edge_rows
        elif "parent_avail_status" in sql:          # 1홉 의존성 self-join(별칭)
            rows = []
        elif "cmm_alarm_def_noti" in sql:           # 알림정책
            rows = []
        else:                                        # resource signal(resource_id 등)
            rows = self._resource_rows
        return SimpleNamespace(rows=rows, row_count=len(rows))


class _Registry:
    def __init__(self, client):
        self._client = client

    def is_registered(self, db_id):
        return True

    @asynccontextmanager
    async def get_client(self, db_id):
        yield self._client


def _alarm_cfg() -> AlarmConfig:
    return AlarmConfig(
        history_enabled=True, history_lookback_days=90, history_max_rows=2000,
        history_cache_ttl_seconds=300, enrich_timeout_seconds=5, burst_threshold_24h=5,
    )


class TestRepoMultiHopFetch:
    async def test_cascaded_root_computed(self):
        # leaf(web-01, id=L) → M(id=M) → ROOT(id=R). ROOT 비정상 → cascaded, root=R.
        client = _MultiHopFakeClient(
            resource_rows=[{"resource_id": "L", "importance_id": None, "maintenance": 0}],
            edge_rows=[
                {"id": "L", "name": "web-01", "avail_depend_resource_id": "M",
                 "avail_depend_resource_id_2": None},
                {"id": "M", "name": "app-01", "avail_depend_resource_id": "R",
                 "avail_depend_resource_id_2": None},
            ],
            status_rows=[
                {"id": "M", "name": "app-01", "avail_status": 0},
                {"id": "R", "name": "db-01", "avail_status": 1},  # 최상위 비정상
            ],
        )
        repo = PolestarNoiseContextRepository(_Registry(client), _alarm_cfg())
        ctx = await repo.fetch(
            make_event(), collect_dependency=True, multi_hop=True, topology_max_hops=5,
        )
        assert ctx["cascaded"] is True
        assert ctx["root_resource"] == "R"
        # root R은 부모 없어 엣지 로더 NAME 누락 — 조상 상태 IN 조회로 보강(db-01).
        assert ctx["root_resource_name"] == "db-01"

    async def test_all_ancestors_normal_not_cascaded(self):
        client = _MultiHopFakeClient(
            resource_rows=[{"resource_id": "L", "importance_id": None, "maintenance": 0}],
            edge_rows=[
                {"id": "L", "name": "web-01", "avail_depend_resource_id": "R",
                 "avail_depend_resource_id_2": None},
            ],
            status_rows=[{"id": "R", "avail_status": 0}],  # 정상
        )
        repo = PolestarNoiseContextRepository(_Registry(client), _alarm_cfg())
        ctx = await repo.fetch(make_event(), collect_dependency=True, multi_hop=True)
        assert ctx["cascaded"] is False
        assert ctx["root_resource"] is None

    async def test_non_postgres_db_skips_multi_hop(self):
        # b0(DB2)는 다홉 미시도 → cascaded None(1홉 폴백).
        client = _MultiHopFakeClient(
            resource_rows=[{"resource_id": "L", "importance_id": None, "maintenance": 0}],
            edge_rows=[], status_rows=[],
        )
        repo = PolestarNoiseContextRepository(_Registry(client), _alarm_cfg())
        ctx = await repo.fetch(
            make_event(db_id="polestar_b0"), collect_dependency=True, multi_hop=True,
        )
        assert ctx["cascaded"] is None
        assert ctx["root_resource"] is None

    async def test_multi_hop_off_no_computation(self):
        client = _MultiHopFakeClient(
            resource_rows=[{"resource_id": "L", "importance_id": None, "maintenance": 0}],
            edge_rows=[{"id": "L", "name": "web-01", "avail_depend_resource_id": "R",
                        "avail_depend_resource_id_2": None}],
            status_rows=[{"id": "R", "avail_status": 1}],
        )
        repo = PolestarNoiseContextRepository(_Registry(client), _alarm_cfg())
        ctx = await repo.fetch(make_event(), collect_dependency=True, multi_hop=False)
        assert ctx["cascaded"] is None  # 미산출(회귀 0)
