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
