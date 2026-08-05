"""Plan 60 Wave A 회귀 가드 — 신규 플래그가 전부 off면 현행 게이트와 비트 동일(회귀 0).

Wave A(E1 재발생 관측성 · E4 토폴로지 다홉 · E6 통보 컨텍스트 보강)는 전부 옵트인이다.
게이트가 켜져 있어도(enable_noise_gate) Wave A 신규 경로가 비활성이면:
  A. 정책(decide_notification): cascaded 미제공(multi_hop off)·correlated 휴면이면 현행
     1홉 parent_avail_status 판정과 tier/reason/priority 비트 동일. 심각도3 단락 불변.
  B. signals: Wave A 신규 키(cascaded/root_resource/correlated)가 존재하되 off-기본값
     (None/None/False) — §8.2 동결 스키마 확장은 Wave A에서 일괄 1회.
  C. 통보(build_workb_body): enrichment=None(message off)이면 본문 비트 동일.
  D. 그래프: Wave A 플래그는 노드집합을 바꾸지 않는다(전부 worker/policy/enricher 내부).

E1 dedup·E4 다홉·E6 보강 각각의 상세 동작은 test_recurrence_dedup / test_multi_hop_cascade /
test_message_enrichment 가 담당한다. 본 파일은 "전부 off = 무변경" 교차 회귀만 고정한다.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.application.nodes.alarm_analyzer import alarm_analyzer_node
from src.alarm.application.nodes.alarm_context_enricher import (
    alarm_context_enricher_node,
)
from src.alarm.application.nodes.alarm_notifier import build_workb_body
from src.alarm.domain.alarm import AlarmEvent
from src.alarm.domain.notification_policy import (
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)
from src.alarm.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph


def _event(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def _cfg(**over) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2,
        importance_value_map={"HIGH": "높음"},
        resolved_to_dashboard=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(parent=None, **extra) -> dict:
    ctx = {
        "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
        "parent_avail_status": parent, "source": "polestar_db",
    }
    ctx.update(extra)
    return ctx


# ─────────────────────────────────────────────────────────────
# A. 정책 — Wave A 신규 경로 off면 현행 1홉 판정과 비트 동일
# ─────────────────────────────────────────────────────────────
class TestPolicyWaveAOffEqualsCurrent:
    def test_multihop_off_falls_back_to_1hop_suppress(self):
        # dependency_suppression on + cascaded 미제공(noise_ctx에 cascaded 키 없음)
        # → 현행 1홉 parent_avail_status 판정(부모 비정상 → SUPPRESS)과 동일.
        cfg = _cfg(dependency_suppression=True)
        d = decide_notification(_event(2), None, None, _ctx(parent=1), cfg)
        assert d.tier == TIER_SUPPRESS
        assert "부모 리소스 비정상" in d.reason  # 1홉 사유(다홉 사유 아님)

    def test_multihop_off_parent_normal_not_suppressed(self):
        cfg = _cfg(dependency_suppression=True)
        d = decide_notification(_event(2), None, None, _ctx(parent=0), cfg)
        assert d.tier == TIER_PAGE  # 부모 정상 → 억제 없음, 매트릭스(높음+sev2=PAGE)

    def test_correlated_arg_ignored_when_flag_off(self):
        # E2 step7.5 구현됨 — 단 cross_host_correlation_enabled 미설정(off·기본)이면
        # correlated=True를 넘겨도 tier/reason/priority 불변(플래그 게이팅·회귀 0).
        cfg = _cfg()
        base = decide_notification(_event(2), None, None, _ctx(None), cfg)
        with_corr = decide_notification(
            _event(2), None, None, _ctx(None), cfg, correlated=True
        )
        assert with_corr.tier == base.tier
        assert with_corr.reason == base.reason
        assert with_corr.priority == base.priority
        assert with_corr.signals["correlated"] is True   # signals에만 스냅샷
        assert base.signals["correlated"] is False

    def test_severity3_short_circuits_even_with_cascade(self):
        # 심각도3은 cascaded=True·root 미통보여도 step3에서 PAGE 단락(불변).
        cfg = _cfg(dependency_suppression=True)
        d = decide_notification(
            _event(3), None, None,
            _ctx(parent=1, cascaded=True, root_resource="R99", root_notified=False),
            cfg,
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason


# ─────────────────────────────────────────────────────────────
# B. signals — Wave A 신규 키가 off-기본값으로 존재(동결 스키마)
# ─────────────────────────────────────────────────────────────
class TestSignalsWaveAKeysOffDefaults:
    def test_new_keys_present_at_off_defaults(self):
        d = decide_notification(_event(2), None, None, _ctx(None), _cfg())
        sig = d.signals
        assert sig["cascaded"] is None
        assert sig["root_resource"] is None
        assert sig["correlated"] is False


# ─────────────────────────────────────────────────────────────
# C. 통보 — E6 enrichment=None이면 본문 비트 동일
# ─────────────────────────────────────────────────────────────
def _analysis() -> SimpleNamespace:
    ev = SimpleNamespace(
        severity=2, alarm_name="CPU", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="root/srv-1", resource_type="server.Cpus",
        resource_name="r1", alarm_status="OPEN", conditions=">90%", condition_log="cpu high",
    )
    return SimpleNamespace(
        alarm_event=ev, severity_label="심각", summary="s", probable_cause="c",
        recommended_action="a", pattern_type="", pattern_analysis="",
    )


class TestNotifierEnrichmentOff:
    def test_body_bit_identical_when_enrichment_none(self):
        result = _analysis()
        # E6 인자를 기본(None)으로 둔 본문 == enrichment 인자를 명시 None으로 넘긴 본문.
        default_body = build_workb_body(result)
        explicit_none = build_workb_body(
            result, process_snapshot=None, recurrence=None, enrichment=None
        )
        assert default_body == explicit_none
        assert "보강" not in default_body  # 신규 kind 보강 블록 미첨부


# ─────────────────────────────────────────────────────────────
# D. 그래프 — Wave A 플래그가 노드집합을 바꾸지 않음
# ─────────────────────────────────────────────────────────────
def _gcfg(**ng) -> SimpleNamespace:
    base = dict(enable_noise_gate=True)
    base.update(ng)
    return SimpleNamespace(
        alarm=SimpleNamespace(history_enabled=True),
        noise_gate=SimpleNamespace(**base),
    )


class TestGraphNodesUnchangedByWaveA:
    def test_node_set_identical_wave_a_on_vs_off(self):
        nodes_off = set(build_alarm_graph(_gcfg()).get_graph().nodes.keys())
        nodes_on = set(build_alarm_graph(_gcfg(
            dependency_suppression=True, multi_hop_cascade_enabled=True,
            message_enrichment_enabled=True, change_correlation_enabled=True,
        )).get_graph().nodes.keys())
        assert nodes_off == nodes_on


# ─────────────────────────────────────────────────────────────
# E. 워커 — E2 상관이 off(기본)면 detection 미수행·_detect_storm 비트동일
# ─────────────────────────────────────────────────────────────
def _sev(is_clear: bool = False, server: str = "srv-1",
         db_id: str = "db1") -> SimpleNamespace:
    return SimpleNamespace(is_clear=is_clear, db_id=db_id, server_name=server)


class TestWorkerCorrelationOffIndependent:
    def test_detect_storm_bit_identical_and_correlation_state_untouched(self):
        # _process는 cross_host_correlation_enabled일 때만 _detect_correlated_storm을
        # 호출한다 — off면 상관 상태(_correlation_clusters)가 비어 있고 storm 판정은 불변.
        cfg = SimpleNamespace(noise_gate=SimpleNamespace(
            storm_threshold=3, storm_window_seconds=60,
        ))
        w = AlarmWorker(cfg)
        results = [w._detect_storm(_sev(), now=1000.0) for _ in range(5)]
        assert results == [False, False, False, True, True]  # 기존 스톰 경계 불변
        # storm 경로는 상관 상태를 건드리지 않는다(독립 dict) → detection 미수행 = 빈 dict.
        assert w._correlation_clusters == {}


# ─────────────────────────────────────────────────────────────
# E-bis. E2 위상 가중 off — adjacent 미주입·match_cluster 점수 Jaccard 비트동일
# ─────────────────────────────────────────────────────────────
def _corr_cfg(*, topo: bool) -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(
        correlation_window_seconds=120, correlation_sim_threshold=0.5,
        correlation_min_cluster_size=2, correlation_buffer_max=1000,
        correlation_topology_weight_enabled=topo, correlation_topology_weight=0.2,
        topology_max_hops=5,
    ))


def _corr_ev(server: str = "A", rtype: str = "p") -> SimpleNamespace:
    return SimpleNamespace(
        is_clear=False, db_id="db1", server_name=server, hostname=server,
        alarm_name="x", resource_type=rtype, resource_name="r", condition_log="",
    )


class TestTopologyWeightOffRegression:
    def test_match_cluster_score_bit_identical_jaccard_only(self):
        # 위상 가중 off(adjacent=None·topo_weight=0.0)이면 match_cluster가 현행 Jaccard와 비트동일.
        from src.alarm.domain.correlation import ClusterState, jaccard, match_cluster

        clusters = [ClusterState("fp", frozenset({"a", "b"}), 10.0, 10.0)]
        toks = frozenset({"a", "c"})
        idx, score = match_cluster(clusters, toks, sim_threshold=0.5)
        # 기본 인자(adjacent 미주입) == 명시 off 인자 == 순수 Jaccard.
        assert (idx, score) == match_cluster(
            clusters, toks, sim_threshold=0.5, adjacent=None, topo_weight=0.0
        )
        assert score == 0.0 and idx is None  # jaccard 1/3 < 0.5 → 미매칭
        # 임계 이상 케이스도 순수 Jaccard 점수와 동일.
        idx2, score2 = match_cluster(clusters, frozenset({"a", "b"}), sim_threshold=0.5)
        assert idx2 == 0 and score2 == jaccard(frozenset({"a", "b"}), frozenset({"a", "b"}))

    def test_worker_topology_off_no_bonus_bit_identical(self):
        # correlation_topology_weight_enabled=False → graph를 넘겨도 topo_weight=0.0 →
        # 임계 미달 쌍(1/3<0.5)은 비군집(Jaccard 판정과 동일). correlation_meta는 None.
        from src.alarm.domain.topology import DependencyGraph

        w = AlarmWorker(_corr_cfg(topo=False))
        g = DependencyGraph({"idA": ["p"], "idB": ["p"]}, names={"idA": "A", "idB": "B"})
        c1, m1 = w._detect_correlated_storm(_corr_ev("A", "p"), 1000.0, graph=g)
        c2, m2 = w._detect_correlated_storm(_corr_ev("B", "q"), 1001.0, graph=g)
        assert (c1, m1) == (False, None)
        assert (c2, m2) == (False, None)   # 보너스 없음 → 미군집·메타 없음

    def test_correlation_meta_only_when_correlated(self):
        # correlation_meta는 correlated=True(억제)일 때만 채워지고, 그 외에는 None.
        w = AlarmWorker(_corr_cfg(topo=False))
        # 동일 시그니처 → 대표(None) + 억제 멤버(meta).
        c1, m1 = w._detect_correlated_storm(_corr_ev("A", "p"), 1000.0)
        c2, m2 = w._detect_correlated_storm(_corr_ev("B", "p"), 1001.0)
        assert (c1, m1) == (False, None)   # 대표 → 메타 없음
        assert c2 is True and m2 is not None and m2["member_seq"] == 2


# ─────────────────────────────────────────────────────────────
# F. Plan 64 CW-A — 자동 조사 트리거 off면 게이트·통보·감사 비트동일
# ─────────────────────────────────────────────────────────────
class TestInvestigationTriggerOffRegression:
    def test_graph_node_set_identical_when_trigger_off(self):
        # investigation_trigger_enabled 미설정(off)이면 그래프 노드집합은 게이트-only와 동일.
        nodes_off = set(build_alarm_graph(_gcfg()).get_graph().nodes.keys())
        nodes_explicit_off = set(build_alarm_graph(
            _gcfg(investigation_trigger_enabled=False)
        ).get_graph().nodes.keys())
        assert nodes_off == nodes_explicit_off
        assert "investigation_trigger" not in nodes_off

    def test_notifier_body_bit_identical_when_briefing_none(self):
        # investigation_briefing=None(off·트리거 미발화)이면 통보 본문 비트 동일.
        result = _analysis()
        default_body = build_workb_body(result)
        explicit_none = build_workb_body(result, investigation_briefing=None)
        assert default_body == explicit_none
        assert "조사 브리핑" not in default_body

    async def test_trigger_node_no_op_when_flag_off(self):
        # 방어적 진입부 재확인 — off면 노드가 클라이언트를 만지지 않고 빈 dict 반환.
        from src.alarm.application.nodes.investigation_trigger import (
            investigation_trigger_node,
        )
        from src.alarm.domain.notification_policy import NotificationDecision

        calls = {"n": 0}

        class _Spy:
            async def connect(self):
                calls["n"] += 1

            async def submit(self, payload):
                calls["n"] += 1
                return {}

        state = {
            "alarm_event": SimpleNamespace(alarm_id="A", server_name="s", hostname="h",
                                           severity=2, db_id="d", alarm_name="n",
                                           resource_type="t", resource_name="r",
                                           conditions="", condition_log="", alarm_time=None),
            "notification_decision": NotificationDecision(
                tier=TIER_PAGE, reason="r", priority=3, signals={}, fingerprint="fp"
            ),
            "recurrence": None, "correlation_meta": None,
        }
        cfg = {"configurable": {
            "app_config": SimpleNamespace(noise_gate=SimpleNamespace(
                investigation_trigger_enabled=False
            )),
            "sre_agent_client": _Spy(),
            "decision_store": None,
        }}
        out = await investigation_trigger_node(state, cfg)
        assert out == {} and calls["n"] == 0  # 클라이언트 미접촉(회귀 0)

    def test_decision_store_tier_aggregate_unaffected_by_investigation_record(self, tmp_path):
        # record_investigation 추가가 기존 티어 집계를 바꾸지 않는다(type 필드 제외).
        from src.alarm.infrastructure.decision_store import DecisionStore
        from src.alarm.domain.notification_policy import NotificationDecision

        store = DecisionStore(str(tmp_path / "d.jsonl"), enabled=True)
        d = NotificationDecision(tier=TIER_PAGE, reason="r", priority=3,
                                 signals={}, fingerprint="fp")
        store.record(d, alarm_id="A")
        before = store.aggregate()
        store.record_investigation(alarm_id="A", status="done", investigation_id="i1")
        after = store.aggregate()
        assert before["total"] == after["total"] == 1
        assert before["by_tier"] == after["by_tier"]

    async def test_followup_off_keeps_inline_attach_and_no_background_task(self):
        # (Plan 66 3-E) investigation_followup_enabled off면 트리거는 인라인 poll로 브리핑을
        # 첨부하고 pending을 만들지 않으며, notifier는 후속 태스크를 띄우지 않는다(비트동일).
        import src.alarm.application.nodes.alarm_notifier as notifier_mod
        from src.alarm.application.nodes.investigation_trigger import (
            investigation_trigger_node,
        )
        from src.alarm.domain.notification_policy import NotificationDecision

        class _Client:
            async def connect(self):
                pass

            async def disconnect(self):
                pass

            async def submit(self, payload):
                return {"investigation_id": "inv-1", "status": "accepted"}

            async def poll(self, inv_id):
                return {"status": "done", "briefing": {"cause": "x"}, "verdict": None}

        state = {
            "alarm_event": SimpleNamespace(alarm_id="A", server_name="s", hostname="h",
                                           severity=2, db_id="d", alarm_name="n",
                                           resource_type="t", resource_name="r",
                                           conditions="", condition_log="", alarm_time=None),
            "notification_decision": NotificationDecision(
                tier=TIER_PAGE, reason="r", priority=3, signals={}, fingerprint="fp"
            ),
            "recurrence": None, "correlation_meta": None,
        }
        cfg = {"configurable": {
            "app_config": SimpleNamespace(noise_gate=SimpleNamespace(
                investigation_trigger_enabled=True,
                investigation_trigger_min_tier="PAGE",
                investigation_poll_interval_seconds=0.0,
                investigation_total_timeout_seconds=5.0,
            )),
            "sre_agent_client": _Client(),
            "decision_store": None,
        }}
        out = await investigation_trigger_node(state, cfg)
        # 플래그 미설정(=off) → 기존 CW-A 인라인 첨부 경로 그대로.
        assert out["investigation_briefing"] == {"cause": "x"}
        assert "investigation_pending" not in out
        assert not notifier_mod._FOLLOWUP_TASKS


# ─────────────────────────────────────────────────────────────
# F. E3 동적 baseline — dynamic_baseline_enabled=False면 무변경(회귀 0)
# ─────────────────────────────────────────────────────────────
def _full_event() -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="/S/srv/Cpus", alarm_id="A-1",
        severity=1, alarm_status="NOT_ACK", resource_type="server.Cpus",
        resource_name="cpu0", alarm_name="CPU Utilization", alarm_time=datetime(2026, 7, 22),
        conditions=">90", condition_log="95", is_clear=False,
    )


class _FakeNoiseRepo:
    def is_db_registered(self, db_id):
        return True

    async def fetch(self, event, **kw):
        return {
            "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
            "parent_avail_status": None, "cascaded": None, "root_resource": None,
            "root_resource_name": None, "source": "polestar_db",
        }


class _SpyBaseline:
    def __init__(self):
        self.called = 0

    async def compute_severity(self, event, noise_cfg, redis_client=None):
        self.called += 1
        return 3


def _enricher_cfg(*, dynamic: bool) -> SimpleNamespace:
    alarm = SimpleNamespace(history_enabled=False, enrich_timeout_seconds=5)
    noise_gate = SimpleNamespace(
        enable_noise_gate=True, dependency_suppression=False,
        multi_hop_cascade_enabled=False, message_enrichment_enabled=False,
        dynamic_baseline_enabled=dynamic, noise_context_cache_ttl_seconds=0,
    )
    return SimpleNamespace(alarm=alarm, noise_gate=noise_gate)


class TestEnricherAnomalyKeyOff:
    async def test_key_absent_and_adapter_not_called_when_off(self):
        # dynamic_baseline_enabled=False → 이상탐지 태스크·키 미추가(키셋 불변), 어댑터 미호출.
        spy = _SpyBaseline()
        config = {"configurable": {
            "app_config": _enricher_cfg(dynamic=False), "noise_repo": _FakeNoiseRepo(),
            "metric_baseline": spy, "history_repo": None, "process_client": None,
            "history_redis": None,
        }}
        out = await alarm_context_enricher_node({"alarm_event": _full_event()}, config)
        assert "anomaly_severity" not in out  # 키셋 불변(회귀 0)
        assert set(out.keys()) == {"history_stats", "process_snapshot", "noise_context"}
        assert spy.called == 0

    async def test_key_present_when_on(self):
        # 양성 대조: dynamic_baseline_enabled=True면 키 추가·어댑터 호출·값 반영.
        spy = _SpyBaseline()
        config = {"configurable": {
            "app_config": _enricher_cfg(dynamic=True), "noise_repo": _FakeNoiseRepo(),
            "metric_baseline": spy, "history_repo": None, "process_client": None,
            "history_redis": None,
        }}
        out = await alarm_context_enricher_node({"alarm_event": _full_event()}, config)
        assert out["anomaly_severity"] == 3
        assert spy.called == 1


class _StubLLM:
    async def ainvoke(self, messages, *args, **kwargs):
        return SimpleNamespace(content=json.dumps({
            "severity_label": "주의", "summary": "s", "probable_cause": "c",
            "recommended_action": "a", "pattern_type": "",
        }, ensure_ascii=False))


class TestAnalyzerAnomalyOff:
    async def test_ai_severity_unchanged_when_dynamic_off(self, monkeypatch):
        # dynamic_baseline_enabled=False면 주입 anomaly_severity가 있어도 ai_message_severity 무변경.
        monkeypatch.setattr(
            "src.alarm.application.nodes.alarm_analyzer.create_llm",
            lambda cfg, **kw: _StubLLM(),
        )
        cfg = SimpleNamespace(
            alarm=SimpleNamespace(get_notification_channels=lambda: ["workb"]),
            noise_gate=SimpleNamespace(
                enable_ai_severity_boost=True, dynamic_baseline_enabled=False,
                enable_llm_actionability=False,
            ),
        )
        state = {"alarm_event": _full_event(), "history_stats": None,
                 "process_snapshot": None, "analysis_result": None, "error": None,
                 "anomaly_severity": 3}
        out = await alarm_analyzer_node(state, {"configurable": {"app_config": cfg}})
        assert out["analysis_result"].ai_message_severity is None  # 무변경(회귀 0)


# ─────────────────────────────────────────────────────────────
# F-2. E3 2차 STL — anomaly_stl_enabled=False면 STL 헬퍼 미호출·HW 경로 비트동일 (D-113)
# ─────────────────────────────────────────────────────────────
class _StlFakeClient:
    def __init__(self, rows):
        self._rows = rows

    async def execute_sql(self, sql):
        return SimpleNamespace(rows=self._rows)


class _StlFakeRegistry:
    def __init__(self, rows):
        self._rows = rows

    def is_registered(self, db_id):
        return True

    @asynccontextmanager
    async def get_client(self, db_id):
        yield _StlFakeClient(self._rows)


def _spike_rows_desc() -> list[dict]:
    """스파이크(최신 150) 시계열을 DESC(최신 우선) 행으로 — HW 경로 시 severity 3."""
    total = 5 * 24 + 1
    asc = [(90.0 if i % 24 == 3 else 20.0) + (i % 5 - 2) * 0.3 for i in range(total - 1)]
    asc.append(150.0)
    return [{"stat_date": str(i), "avg_val": v} for i, v in enumerate(reversed(asc))]


class TestStlOffRegression:
    async def test_stl_helper_not_called_and_hw_path_when_off(self, monkeypatch):
        # anomaly_stl_enabled=False → STL 헬퍼 미진입(호출 0), HW 경로가 스파이크로 severity 3.
        from src.alarm.infrastructure.polestar_metric_baseline import (
            PolestarMetricBaselineAdapter,
        )

        calls = {"n": 0}

        def _spy(series, period, *, min_periods=3):
            calls["n"] += 1
            return 2  # STL 경로였다면 severity 2가 나옴(대조)

        monkeypatch.setattr(
            "src.alarm.infrastructure.metric_stl.stl_anomaly_score", _spy
        )
        reg = _StlFakeRegistry(_spike_rows_desc())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        cfg = SimpleNamespace(
            anomaly_z_high=3.0, anomaly_min_periods=3,
            anomaly_baseline_cache_ttl_seconds=0, noise_context_timeout_seconds=3.0,
            anomaly_stl_enabled=False,
        )
        sev = await adapter.compute_severity(_full_event(), cfg, redis_client=None)
        assert calls["n"] == 0  # 플래그 off → STL 미호출
        assert sev == 3         # HW 경로 비트동일


# ─────────────────────────────────────────────────────────────
# G. E5 변경 상관 — change_correlation off면 변경 조회 미수행·게이트 비트동일
# ─────────────────────────────────────────────────────────────
class TestPolicyChangeCorrelationOff:
    def test_change_nearby_off_bit_identical(self):
        # change_nearby None(off) → 승격 없음. change_nearby 키 부재/None 모두 baseline과 동일.
        cfg = _cfg()
        baseline = decide_notification(_event(2), None, None, _ctx(None), cfg)
        with_none = decide_notification(
            _event(2), None, None, _ctx(None, change_nearby=None), cfg
        )
        assert with_none.tier == baseline.tier == TIER_PAGE  # 높음+sev2=PAGE(승격 무관)
        assert with_none.reason == baseline.reason
        assert with_none.priority == baseline.priority
        assert "변경 근접" not in baseline.reason

    def test_change_nearby_not_in_signals_schema(self):
        # §7.2: change_nearby는 signals 동결 스키마에 넣지 않는다(스냅샷 전수 갱신 불필요).
        d = decide_notification(_event(2), None, None, _ctx(None), _cfg())
        assert "change_nearby" not in d.signals
        assert "change_candidates" not in d.signals


class _RecordingClient:
    def __init__(self):
        self.executed_sqls: list[str] = []

    async def execute_sql(self, sql):
        self.executed_sqls.append(sql)
        return SimpleNamespace(rows=[], row_count=0)


class _RecordingRegistry:
    def __init__(self, client):
        self._client = client

    def is_registered(self, db_id):
        return True

    @asynccontextmanager
    async def get_client(self, db_id):
        yield self._client


def _alarm_event() -> AlarmEvent:
    return AlarmEvent(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/svr", alarm_id="A1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Server", resource_name="r1", alarm_name="CPU",
        alarm_time=datetime(2026, 7, 22, 10, 0, 0), conditions="", condition_log="",
    )


class TestRepoChangeCorrelationOff:
    async def test_no_change_sql_when_disabled(self):
        # change_correlation=False → 변경이력 SQL 미실행, change_nearby/change_candidates None.
        client = _RecordingClient()
        repo = PolestarNoiseContextRepository(_RecordingRegistry(client), None)
        ctx = await repo.fetch(_alarm_event(), change_correlation=False)
        joined = "\n".join(client.executed_sqls)
        assert "cmm_resource_lifecycle_history" not in joined  # 변경 조회 미수행
        assert ctx["change_nearby"] is None
        assert ctx["change_candidates"] is None


# ─────────────────────────────────────────────────────────────
# H. B-7 임베딩 주석 — 두 플래그 off면 provider 미주입·주석 경로 미진입·감사 키 부재(회귀 0)
# ─────────────────────────────────────────────────────────────
class TestEmbeddingProviderOptIn:
    def test_provider_none_when_both_flags_off(self):
        # semantic_dedup_annotation_enabled·topology_text_fusion_enabled 둘 다 off → provider 미생성.
        cfg = SimpleNamespace(noise_gate=SimpleNamespace(
            semantic_dedup_annotation_enabled=False,
            topology_text_fusion_enabled=False,
        ))
        assert AlarmWorker(cfg)._build_embedding_provider() is None

    def test_provider_built_when_l2_on(self):
        # 한쪽(L-2)만 켜도 provider 생성(모델 경로 미설정 → inert이지만 객체는 존재).
        cfg = SimpleNamespace(noise_gate=SimpleNamespace(
            semantic_dedup_annotation_enabled=True,
            topology_text_fusion_enabled=False,
            embedding_model_path="", embedding_timeout_seconds=2.0,
        ))
        provider = AlarmWorker(cfg)._build_embedding_provider()
        assert provider is not None
        assert provider.is_available() is False  # 경로 미설정 → inert(주석 미생성·graceful)

    def test_annotate_noop_when_provider_off(self):
        # provider 미주입이면 워커 주석 산출이 None·recent_texts 무변경(주석 경로 미진입).
        w = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
            embedding_similarity_threshold=0.85, repeat_interval_seconds=14400,
        )))
        ev = SimpleNamespace(db_id="db1", alarm_name="CPU", resource_type="server.Cpus",
                             condition_log="95", is_clear=False)
        assert w._annotate_semantic_dedup(ev, "fp", 1000.0) is None
        assert w._recent_event_texts == {}


class TestDecisionStoreAnnotationKeysAbsentWhenOff:
    async def test_no_semantic_annotation_key_when_none(self, tmp_path):
        # semantic_annotation=None(off/inert) → decision_store 레코드에 키 부재(비트동일).
        from src.alarm.infrastructure.decision_store import DecisionStore
        from src.alarm.application.nodes.notification_gate import (
            notification_gate_node,
        )

        p = tmp_path / "d.jsonl"
        store = DecisionStore(str(p))
        state = {
            "alarm_event": _full_event(),
            "history_stats": None,
            "analysis_result": SimpleNamespace(pattern_type="", is_routine=None),
            "error": None,
            "noise_context": {
                "importance_id": None, "maintenance": False, "noti_policy": None,
                "parent_avail_status": None, "source": "polestar_db",
            },
            "self_heal": False,
            "semantic_annotation": None,   # off/inert 경로
        }
        cfg = SimpleNamespace(noise_gate=SimpleNamespace(
            enable_noise_gate=True, suppress_max_severity=2,
            importance_value_map={}, resolved_to_dashboard=False,
        ))
        await notification_gate_node(
            state, {"configurable": {"app_config": cfg, "decision_store": store}}
        )
        rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
        assert "semantic_annotation" not in rec        # L-2 키 부재
        assert "root_text_similarity" not in rec        # L-4 키(감사엔 미기록)


class TestEnricherL4KeyAbsentWhenProviderNone:
    async def test_no_root_text_similarity_when_provider_none(self):
        # embedding_provider=None(off) → root_text_similarity 키 미첨부(하위호환·회귀 0).
        from src.alarm.application.nodes.alarm_context_enricher import (
            enrich_noise_context,
        )

        class _Repo:
            async def fetch(self, event, **kw):
                return {
                    "importance_id": None, "maintenance": False, "noti_policy": None,
                    "parent_avail_status": None, "cascaded": True, "root_resource": "R",
                    "root_resource_name": "db-01", "source": "polestar_db",
                }

        ev = SimpleNamespace(
            alarm_name="CPU", resource_type="server.Cpus", condition_log="95",
            db_id="db1", server_name="srv-1",
        )
        cfg = SimpleNamespace(
            noise_context_cache_ttl_seconds=0, inhibition_window_seconds=300,
            topology_max_hops=5, topology_cache_ttl_seconds=86400,
            change_window_seconds=3600, topology_text_fusion_enabled=True,
        )
        ctx = await enrich_noise_context(
            ev, cfg, _Repo(), None, collect_dependency=True, multi_hop=True,
            embedding_provider=None,
        )
        assert "root_text_similarity" not in ctx


# ═════════════════════════════════════════════════════════════
# Plan 60 E7 (D-116) — 실측 ITSM 사례 기반 텍스트·주석 신호 보완.
# 신규 5플래그(annotation_harvest_enabled·annotation_planned_suppress·non_alarm_filter_enabled·
# format_tolerant_parsing_enabled·correlation_site_dimension_enabled) 전부 off면 비트동일(회귀 0).
# off/on 대조로 §17.9 수용 기준 ①~⑥을 고정한다.
# ═════════════════════════════════════════════════════════════
from src.alarm.domain.annotation_signal import extract_annotation_signal  # noqa: E402
from src.alarm.domain.correlation import extract_site_token  # noqa: E402
from src.alarm.domain.notification_policy import (  # noqa: E402
    TIER_DASHBOARD,
    is_operational_alarm,
)


# ── I. E7-a annotation_signal 도메인 순수함수(결정적 추출) ──
class TestE7aAnnotationSignalDomain:
    def test_s3_operator_ack_and_resolution_markers(self):
        # S3 재발신: '=> 담당자 통화'(operator_ack) / '서비스 영향 없음'(resolution).
        s1 = extract_annotation_signal("szaaso01 사용률 [95%] => 담당자 홍길동 통화. 확인 후 연락")
        assert s1.operator_ack is True and s1.planned_work is False
        s2 = extract_annotation_signal("szaaso01 사용률 [95%] => 확인 결과 서비스 영향 없음.")
        assert s2.resolution is True

    def test_s4_planned_work_markers(self):
        # S4: '예정된 IPL 작업으로 발생'(planned_work) / '계획정지 … 서비스 이상없음'.
        s1 = extract_annotation_signal("staapos1 가용성 DOWN — 예정된 IPL 작업으로 발생")
        assert s1.planned_work is True
        s2 = extract_annotation_signal("계획정지 관련 작업으로 서비스 이상없음")
        assert s2.planned_work is True and s2.resolution is True

    def test_empty_signal_when_no_markers(self):
        # 마커 부재 → 빈 신호(하위호환). None·빈 문자열도 안전.
        assert extract_annotation_signal("").has_signal() is False
        assert extract_annotation_signal(None).has_signal() is False  # type: ignore[arg-type]
        assert extract_annotation_signal("cpu usage 95 percent").has_signal() is False


# ── J. E7-b is_operational_alarm + 게이트 step0.5(비알람 사전분류) ──
class TestE7bNonAlarmFilter:
    def test_is_operational_alarm_marker_judgement(self):
        # 비알람(승인요청): 알람 마커 부재 + 비알람 마커 존재 → False.
        non = SimpleNamespace(alarm_name="내부Cloud Cloud PC 사양변경 승인바랍니다",
                              condition_log="", conditions="", resource_name="", resource_type="")
        assert is_operational_alarm(non) is False
        # 실알람(사용률): 알람 마커 존재 → True. 애매(마커 둘 다 없음) → True(재현율 우선).
        real = SimpleNamespace(alarm_name="파일시스템 사용률", condition_log="[95%]",
                              conditions="", resource_name="/data", resource_type="")
        assert is_operational_alarm(real) is True
        amb = SimpleNamespace(alarm_name="foo", condition_log="bar",
                             conditions="", resource_name="", resource_type="")
        assert is_operational_alarm(amb) is True

    def test_filter_off_bit_identical(self):
        # non_alarm_filter_enabled off(기본) → 비알람 이벤트도 현행 매트릭스 판정(비트동일).
        non = _event(2)
        non.alarm_name = "Cloud PC 사양변경 승인바랍니다"
        base = decide_notification(non, None, None, _ctx(None), _cfg())
        assert base.tier == TIER_PAGE  # 높음+sev2=PAGE(마커 필터 미적용)

    def test_filter_on_suppresses_non_alarm(self):
        # on + 비알람 → step0.5 SUPPRESS(사유 감사). 실알람은 억제 안 됨.
        cfg = _cfg(non_alarm_filter_enabled=True)
        non = _event(2)
        non.alarm_name = "Cloud PC 사양변경 승인바랍니다"
        d = decide_notification(non, None, None, _ctx(None), cfg)
        assert d.tier == TIER_SUPPRESS and "비운영 알람" in d.reason
        real = _event(2)
        real.alarm_name = "파일시스템 사용률 임계 초과"
        assert decide_notification(real, None, None, _ctx(None), cfg).tier == TIER_PAGE

    def test_severity3_short_circuit_unchanged_when_filter_off(self):
        # 필터 off → 심각도3 단락 불변(현행 비트동일).
        d = decide_notification(_event(3), None, None, _ctx(None), _cfg())
        assert d.tier == TIER_PAGE and "심각도3" in d.reason


# ── K. E7-a 코로보레이션 게이팅 DASHBOARD 강등(decide_notification annotation) ──
def _planned(**kw) -> dict:
    base = {"planned_work": False, "resolution": False, "operator_ack": False}
    base.update(kw)
    return base


class TestE7aCorroborationDemotion:
    def test_annotation_ignored_when_flag_off(self):
        # annotation_planned_suppress off(기본) → annotation을 넘겨도 tier/reason/priority 불변.
        cfg = _cfg()
        base = decide_notification(_event(2), None, None, _ctx(None), cfg)
        withann = decide_notification(
            _event(2), None, None, _ctx(None), cfg,
            annotation=_planned(planned_work=True, resolution=True),
        )
        assert (withann.tier, withann.reason, withann.priority) == (
            base.tier, base.reason, base.priority
        )

    def test_planned_plus_resolution_demotes_to_dashboard(self):
        # on + planned_work + resolution → DASHBOARD 강등(PAGE 아님).
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _event(2), None, None, _ctx(None), cfg,
            annotation=_planned(planned_work=True, resolution=True),
        )
        assert d.tier == TIER_DASHBOARD and "계획-무해" in d.reason

    def test_planned_plus_change_nearby_demotes(self):
        # on + planned_work + E5 change_nearby(코로보레이션) → DASHBOARD.
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _event(2), None, None, _ctx(None, change_nearby=True), cfg,
            annotation=_planned(planned_work=True),
        )
        assert d.tier == TIER_DASHBOARD

    def test_planned_plus_correlated_demotes(self):
        # on + planned_work + E2 클러스터 소속(correlated) → DASHBOARD.
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _event(2), None, None, _ctx(None), cfg,
            annotation=_planned(planned_work=True), correlated=True,
        )
        assert d.tier == TIER_DASHBOARD

    def test_planned_alone_no_corroboration_not_demoted(self):
        # on + planned_work 단독(코로보레이션 없음) → 강등 없이 매트릭스(PAGE) — 텍스트 단독 억제 금지.
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _event(2), None, None, _ctx(None), cfg,
            annotation=_planned(planned_work=True),
        )
        assert d.tier == TIER_PAGE

    def test_severity3_not_demoted_by_annotation(self):
        # 심각도3은 step3에서 단락 → planned+corroboration이어도 PAGE(불변).
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _event(3), None, None, _ctx(None, change_nearby=True), cfg,
            annotation=_planned(planned_work=True, resolution=True),
        )
        assert d.tier == TIER_PAGE and "심각도3" in d.reason

    def test_annotation_not_in_signals_schema(self):
        # annotation은 signals 동결 스키마에 넣지 않는다(change_nearby 전례).
        cfg = _cfg(annotation_planned_suppress=True)
        d = decide_notification(
            _event(2), None, None, _ctx(None), cfg,
            annotation=_planned(planned_work=True, resolution=True),
        )
        assert "annotation" not in d.signals
        assert "planned_work" not in d.signals


# ── K-bis. E7-a 워커 하베스팅(record_recurrence annotation·chattering) ──
class _RecCaptureStore:
    def __init__(self):
        self.calls: list[dict] = []

    def record_recurrence(self, **kwargs):
        self.calls.append(kwargs)


def _harvest_event() -> AlarmEvent:
    ev = _full_event()
    ev.condition_log = "szaaso01 사용률 [95%] => 확인 결과 서비스 영향 없음."
    return ev


class TestE7aWorkerHarvesting:
    def test_no_annotation_kwarg_when_harvest_off(self):
        # annotation_harvest_enabled off(기본) → record_recurrence 호출에 annotation kwarg 부재(비트동일).
        w = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
            recurrence_audit_every_n=1, annotation_harvest_enabled=False,
        )))
        w._decision_store = _RecCaptureStore()
        w._record_recurrence("fp", _harvest_event(), {"count": 2, "first_seen": 1.0})
        assert "annotation" not in w._decision_store.calls[0]

    def test_harvest_on_attaches_resolution_and_chattering(self):
        # on + resolution 주석 → annotation.resolution=True·chattering='repeating' 적재(재통보 0=append만).
        w = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
            recurrence_audit_every_n=1, annotation_harvest_enabled=True,
        )))
        w._decision_store = _RecCaptureStore()
        w._record_recurrence("fp", _harvest_event(), {"count": 2, "first_seen": 1.0})
        ann = w._decision_store.calls[0]["annotation"]
        assert ann["resolution"] is True and ann["chattering"] == "repeating"


# ── L. E7-c 파서 견고성(_build_alarm_event_from_payload graceful 폴백) ──
def _payload(**over) -> dict:
    base = {"dbId": "db1", "serverName": "srv-1", "alarmId": "A-1",
            "severity": 2, "alarmName": "CPU", "resourceName": "r1"}
    base.update(over)
    return base


class TestE7cParserRobustness:
    def test_known_format_bit_identical_off_vs_on(self):
        # 정상 severity 페이로드 → format_tolerant off/on 파싱 결과 비트동일(신규 폴백만 추가).
        from src.api.routes.alarm import _build_alarm_event_from_payload

        p = _payload(severity=2)
        off = _build_alarm_event_from_payload(p, format_tolerant=False)
        on = _build_alarm_event_from_payload(p, format_tolerant=True)
        assert (off.severity, off.is_clear) == (on.severity, on.is_clear) == (2, False)

    def test_missing_severity_off_current_behavior(self):
        # off(기본) + severity 누락 → 현행 동작(0 → is_clear=True).
        from src.api.routes.alarm import _build_alarm_event_from_payload

        p = _payload()
        del p["severity"]
        ev = _build_alarm_event_from_payload(p, format_tolerant=False)
        assert ev.severity == 0 and ev.is_clear is True

    def test_noninteger_severity_off_raises(self):
        # off + 비정수 severity → 현행처럼 ValueError 전파(크래시 동작 보존·비트동일).
        import pytest

        from src.api.routes.alarm import _build_alarm_event_from_payload

        with pytest.raises(ValueError):
            _build_alarm_event_from_payload(_payload(severity="abc"), format_tolerant=False)

    def test_noninteger_severity_tolerant_conservative(self):
        # on + 비정수 severity → 크래시 금지, 보수적 비-해소(드롭 방지).
        from src.api.routes.alarm import _build_alarm_event_from_payload

        ev = _build_alarm_event_from_payload(_payload(severity="abc"), format_tolerant=True)
        assert ev.severity == 2 and ev.is_clear is False

    def test_missing_severity_tolerant_not_dropped(self):
        # on + severity 누락 → 보수적 비-해소(is_clear=False → 드롭 방지).
        from src.api.routes.alarm import _build_alarm_event_from_payload

        p = _payload()
        del p["severity"]
        ev = _build_alarm_event_from_payload(p, format_tolerant=True)
        assert ev.is_clear is False and ev.severity == 2

    def test_site_token_exposed_when_tolerant(self):
        # on + 네트워크 장비 포맷 → raw_payload 사본에 _site_token 노출(원본 payload 불변).
        from src.api.routes.alarm import _build_alarm_event_from_payload

        p = _payload(serverName="S8530JUM-4331-1.sotori.com||(장애) 세종대")
        ev = _build_alarm_event_from_payload(p, format_tolerant=True)
        assert ev.raw_payload.get("_site_token") == "세종대"
        assert "_site_token" not in p  # 원본 불변


# ── M. E7-d 사이트 상관 차원(extract_site_token + signature_tokens extra) ──
class TestE7dSiteDimension:
    def test_extract_site_token_network_format(self):
        assert extract_site_token("S8530JUM-4331-1.sotori.com||(장애) 세종대") == "세종대"
        assert extract_site_token("", "K8530JUM-4331-2||(경고) 부산대") == "부산대"

    def test_extract_site_token_empty_for_plain_server(self):
        # 일반 서버 알람(마커 없음) → 빈 토큰(E2 상관 무영향·off와 동일 효과).
        assert extract_site_token("srv-1", "r1") == ""

    def test_signature_tokens_extra_off_bit_identical(self):
        # extra="" (site off) → 현행 signature_tokens와 비트동일.
        from src.alarm.domain.correlation import signature_tokens

        base = signature_tokens("가용성", "network.Device", "")
        assert signature_tokens("가용성", "network.Device", "", extra="") == base

    def test_signature_tokens_site_extra_adds_dimension(self):
        # extra=사이트토큰(site on) → 사이트 토큰이 상관 차원으로 추가된다.
        from src.alarm.domain.correlation import signature_tokens

        toks = signature_tokens("가용성", "network.Device", "", extra="세종대")
        assert "세종대" in toks
        assert "세종대" not in signature_tokens("가용성", "network.Device", "")

    def test_worker_site_dimension_off_bit_identical(self):
        # correlation_site_dimension_enabled off → 워커가 extra 미주입 → 현행 상관 판정 비트동일.
        w = AlarmWorker(_corr_cfg(topo=False))
        c1, m1 = w._detect_correlated_storm(_corr_ev("A", "p"), 1000.0)
        c2, m2 = w._detect_correlated_storm(_corr_ev("B", "p"), 1001.0)
        assert (c1, m1) == (False, None)
        assert c2 is True and m2 is not None  # 사이트 차원 없이도 alarm_name+rtype로 군집(현행)


# ── N. E7 전 플래그 off 교차 회귀(비트동일) ──
class TestE7AllFlagsOffBitIdentical:
    def test_decide_notification_bit_identical_all_e7_off(self):
        # 5플래그 전부 off(미설정=getattr False) → 대표 케이스 tier/reason/priority 불변.
        base = decide_notification(_event(2), None, None, _ctx(None), _cfg())
        alloff = _cfg(
            non_alarm_filter_enabled=False, annotation_planned_suppress=False,
        )
        d = decide_notification(
            _event(2), None, None, _ctx(None), alloff,
            annotation=_planned(planned_work=True, resolution=True),
        )
        assert (d.tier, d.reason, d.priority) == (base.tier, base.reason, base.priority)
