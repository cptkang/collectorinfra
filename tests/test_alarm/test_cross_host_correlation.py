"""Plan 60 E2 — 크로스-호스트 이벤트 상관 (D-078).

A. domain `correlation` 순수함수: jaccard·signature_tokens(server_name 제외)·match_cluster
   (결정성·동점 first_ts 오름차순).
B. 워커 온라인 그리디 군집(`AlarmWorker._detect_correlated_storm`):
   - 서버 경계를 넘는 동일 시그니처 다발 → 대표 1건 외 억제(min_cluster_size 경계).
   - 존(db_id) 경계 내 상관만(§8 B-6) — 존 간 미상관.
   - 온라인 의미론(동일 이벤트 시퀀스 → 동일 판정 시퀀스·첫 도착=대표·소급 선출 없음).
   - 만료 sweep(window 밖 클러스터·빈 스코프 키)·버퍼 상한(oldest 제거+warning).
C. 정책 단계(`decide_notification` step7.5): correlated → SUPPRESS(별도 사유),
   심각도3 → PAGE(단락), 플래그 off는 미억제(회귀).
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.domain.correlation import (
    ClusterState,
    jaccard,
    match_cluster,
    signature_tokens,
)
from src.alarm.domain.notification_policy import (
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    compute_fingerprint,
    decide_notification,
)
from src.alarm.domain.topology import DependencyGraph
from src.alarm.infrastructure.decision_store import DecisionStore


# ─────────────────────────────────────────────────────────────
# A. domain 순수함수
# ─────────────────────────────────────────────────────────────
class TestJaccard:
    def test_identical_sets(self):
        assert jaccard(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0

    def test_disjoint_sets(self):
        assert jaccard(frozenset({"a"}), frozenset({"b"})) == 0.0

    def test_partial_overlap(self):
        # |∩|=1, |∪|=3 → 1/3
        assert jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == 1 / 3

    def test_both_empty_zero(self):
        # 빈 합집합은 정의되지 않으므로 0.0(과억제 방지).
        assert jaccard(frozenset(), frozenset()) == 0.0

    def test_one_empty_zero(self):
        assert jaccard(frozenset({"a"}), frozenset()) == 0.0

    def test_symmetric(self):
        a, b = frozenset({"a", "b", "c"}), frozenset({"b", "c", "d"})
        assert jaccard(a, b) == jaccard(b, a)


class TestSignatureTokens:
    def test_lowercase_and_split(self):
        toks = signature_tokens("CPU Utilization", "server.Cpus", "")
        assert toks == frozenset({"cpu", "utilization", "server", "cpus"})

    def test_korean_label_tokenized(self):
        toks = signature_tokens("MEM", "", "OOM 강제종료")
        assert toks == frozenset({"mem", "oom", "강제종료"})

    def test_server_name_excluded_by_design(self):
        # server_name은 파라미터에 없다 — 호스트 경계를 넘는 상관이 목적.
        # 서로 다른 호스트의 동일 알람 시그니처는 동일 토큰 → jaccard=1.0.
        host_a = signature_tokens("CPU Utilization", "server.Cpus", "OOM 강제종료")
        host_b = signature_tokens("CPU Utilization", "server.Cpus", "OOM 강제종료")
        assert host_a == host_b
        assert jaccard(host_a, host_b) == 1.0
        # 호스트 문자열은 애초에 토큰에 없다.
        assert "srv-1" not in host_a and "srv-2" not in host_a

    def test_empty_fields_empty_set(self):
        assert signature_tokens("", "", "") == frozenset()

    def test_extra_field_merged(self):
        toks = signature_tokens("A", "", "", extra="B-C")
        assert toks == frozenset({"a", "b", "c"})


def _cluster(tokens, first_ts: float, fp: str = "fp") -> ClusterState:
    return ClusterState(
        representative_fp=fp,
        tokens=frozenset(tokens),
        first_ts=first_ts,
        last_ts=first_ts,
    )


class TestMatchCluster:
    def test_empty_clusters_none(self):
        idx, score = match_cluster([], frozenset({"a"}), sim_threshold=0.5)
        assert idx is None and score == 0.0

    def test_below_threshold_none(self):
        clusters = [_cluster({"x", "y", "z"}, 10.0)]
        # jaccard({x,y,z},{a}) = 0 < 0.5
        idx, score = match_cluster(clusters, frozenset({"a"}), sim_threshold=0.5)
        assert idx is None and score == 0.0

    def test_highest_score_selected(self):
        clusters = [
            _cluster({"a"}, 10.0),          # jaccard({a},{a,b}) = 1/2 = 0.5
            _cluster({"a", "b"}, 20.0),     # jaccard = 1.0
        ]
        idx, score = match_cluster(clusters, frozenset({"a", "b"}), sim_threshold=0.5)
        assert idx == 1
        assert score == 1.0

    def test_tie_prefers_earliest_first_ts(self):
        clusters = [
            _cluster({"a", "b"}, 100.0, fp="late"),
            _cluster({"a", "b"}, 50.0, fp="early"),
        ]
        idx, _ = match_cluster(clusters, frozenset({"a", "b"}), sim_threshold=0.5)
        assert idx == 1  # first_ts=50 (더 이른 대표)

    def test_tie_same_first_ts_lowest_index(self):
        # first_ts 동일 → 리스트 인덱스 오름차순(먼저 발견) 결정성.
        clusters = [
            _cluster({"a", "b"}, 100.0, fp="c0"),
            _cluster({"a", "b"}, 100.0, fp="c1"),
        ]
        idx, _ = match_cluster(clusters, frozenset({"a", "b"}), sim_threshold=0.5)
        assert idx == 0

    def test_threshold_boundary_inclusive(self):
        # 정확히 임계값(0.5)이면 매칭(이상 포함).
        clusters = [_cluster({"a"}, 10.0)]  # jaccard({a},{a,b}) = 0.5
        idx, score = match_cluster(clusters, frozenset({"a", "b"}), sim_threshold=0.5)
        assert idx == 0 and score == 0.5

    def test_adjacent_none_bit_identical_jaccard(self):
        # adjacent=None·topo_weight=0.0(기본)이면 현행 Jaccard 판정과 비트동일(회귀 0).
        clusters = [_cluster({"a", "b"}, 10.0)]
        base = match_cluster(clusters, frozenset({"a", "c"}), sim_threshold=0.5)
        with_defaults = match_cluster(
            clusters, frozenset({"a", "c"}), sim_threshold=0.5,
            adjacent=None, topo_weight=0.2,
        )
        # jaccard({a,b},{a,c}) = 1/3 < 0.5 → 미매칭. adjacent 미주입이면 보너스 없음.
        assert base == with_defaults == (None, 0.0)

    def test_adjacent_bonus_clusters_below_threshold_pair(self):
        # 필드 Jaccard(1/3)는 임계(0.5) 미달이나, 인접 토폴로지 보너스(+0.2)로 0.533 → 군집.
        clusters = [_cluster({"a", "b"}, 10.0)]
        idx, score = match_cluster(
            clusters, frozenset({"a", "c"}), sim_threshold=0.5,
            adjacent=[True], topo_weight=0.2,
        )
        assert idx == 0
        assert abs(score - (1 / 3 + 0.2)) < 1e-9

    def test_adjacent_false_no_bonus(self):
        # adjacent[i]=False면 보너스 없음 → 임계 미달 유지(미매칭).
        clusters = [_cluster({"a", "b"}, 10.0)]
        idx, score = match_cluster(
            clusters, frozenset({"a", "c"}), sim_threshold=0.5,
            adjacent=[False], topo_weight=0.2,
        )
        assert idx is None and score == 0.0

    def test_adjacent_bonus_deterministic(self):
        # 동일 입력 → 동일 출력(결정성) — 보너스 반영 후 점수·동점 규칙 안정.
        clusters = [_cluster({"a", "b"}, 10.0), _cluster({"a", "b"}, 20.0)]
        kwargs = dict(sim_threshold=0.5, adjacent=[True, False], topo_weight=0.2)
        first = match_cluster(clusters, frozenset({"a", "b"}), **kwargs)
        assert first == match_cluster(clusters, frozenset({"a", "b"}), **kwargs)
        # cluster0 = jaccard 1.0 + 0.2 = 1.2, cluster1 = 1.0 → cluster0 승(보너스로 최고점).
        assert first[0] == 0 and abs(first[1] - 1.2) < 1e-9


# ─────────────────────────────────────────────────────────────
# B. 워커 온라인 그리디 군집 — _detect_correlated_storm
# ─────────────────────────────────────────────────────────────
def _corr_worker(
    *, window: int = 120, sim: float = 0.5, min_size: int = 2, buffer_max: int = 1000
) -> AlarmWorker:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            correlation_window_seconds=window,
            correlation_sim_threshold=sim,
            correlation_min_cluster_size=min_size,
            correlation_buffer_max=buffer_max,
        )
    )
    return AlarmWorker(cfg)


def _cev(
    *,
    alarm: str = "CPU Utilization",
    rtype: str = "server.Cpus",
    server: str = "A",
    db_id: str = "db1",
    is_clear: bool = False,
    condition_log: str = "",
    resource: str = "r",
) -> SimpleNamespace:
    return SimpleNamespace(
        is_clear=is_clear,
        db_id=db_id,
        server_name=server,
        hostname=server,
        alarm_name=alarm,
        resource_type=rtype,
        resource_name=resource,
        condition_log=condition_log,
    )


def _corr(w: AlarmWorker, ev, now: float, *, graph=None) -> bool:
    """_detect_correlated_storm의 (correlated, meta) 중 correlated만 반환하는 테스트 헬퍼."""
    correlated, _meta = w._detect_correlated_storm(ev, now, graph=graph)
    return correlated


class TestDetectCorrelatedStorm:
    def test_cross_host_same_signature_clusters(self):
        # 서로 다른 서버(A/B/C)의 동일 알람 시그니처 → 대표(첫 도착) 외 억제.
        w = _corr_worker(min_size=2)
        r1 = _corr(w, _cev(server="A"), 1000.0)
        r2 = _corr(w, _cev(server="B"), 1001.0)
        r3 = _corr(w, _cev(server="C"), 1002.0)
        assert r1 is False          # 첫 도착 = 대표(통보)
        assert r2 is True           # 2번째 멤버(>=min_size) → 억제
        assert r3 is True

    def test_min_cluster_size_boundary(self):
        # min_size=3 → 3번째 멤버부터 억제(대표+1은 아직 통보).
        w = _corr_worker(min_size=3)
        assert _corr(w, _cev(server="A"), 1000.0) is False
        assert _corr(w, _cev(server="B"), 1001.0) is False
        assert _corr(w, _cev(server="C"), 1002.0) is True

    def test_dissimilar_events_not_clustered(self):
        # 유사도 미달(CPU vs Disk) → 각각 대표(전건 통보, 과억제 없음).
        w = _corr_worker(min_size=2)
        r1 = _corr(w, _cev(alarm="CPU Utilization"), 1000.0)
        r2 = _corr(
            w, _cev(alarm="Filesystem Full", rtype="server.Filesystem"), 1001.0
        )
        assert r1 is False and r2 is False
        assert len(w._correlation_clusters["db1"]) == 2

    def test_zone_boundary_no_cross_zone_correlation(self):
        # §8 B-6: 존(db_id) 경계 내 상관만 — gp/yd 동일 시그니처도 미상관.
        w = _corr_worker(min_size=2)
        r_gp = _corr(w, _cev(db_id="gp", server="A"), 1000.0)
        r_yd = _corr(w, _cev(db_id="yd", server="B"), 1001.0)
        assert r_gp is False and r_yd is False  # 서로 다른 스코프 → 각각 대표
        assert set(w._correlation_clusters) == {"gp", "yd"}

    def test_clear_event_excluded(self):
        # 해소(is_clear) 이벤트는 군집/카운트 제외 → 클러스터 생성 안 함.
        w = _corr_worker(min_size=2)
        correlated, meta = w._detect_correlated_storm(_cev(is_clear=True), 1000.0)
        assert correlated is False and meta is None
        assert w._correlation_clusters == {}

    def test_online_semantics_deterministic(self):
        # 동일 이벤트 시퀀스 → 동일 판정 시퀀스(첫 도착=대표·소급 선출 없음).
        seq = [
            (_cev(server="A"), 1000.0),
            (_cev(server="B"), 1001.0),
            (_cev(alarm="Filesystem Full", rtype="server.Filesystem"), 1002.0),
            (_cev(server="C"), 1003.0),
        ]

        def run() -> list[bool]:
            w = _corr_worker(min_size=2)
            return [_corr(w, e, t) for e, t in seq]

        first = run()
        assert first == run()  # 결정적 재현
        assert first[0] is False  # 첫 도착 = 대표(소급 선출 없음)
        assert first == [False, True, False, True]

    def test_sig_label_boosts_similarity(self):
        # 동일 condition_log 시그니처(OOM)면 sig_label 토큰이 추가돼 유사도가 유지된다.
        # 서로 다른 alarm_name이라도 시그니처 라벨 공유로 임계 이상이면 군집.
        w = _corr_worker(sim=0.5, min_size=2)
        log = "kernel: Out of memory: Killed process 1234"
        r1 = _corr(
            w,
            _cev(alarm="MemMon", rtype="server.Memory", server="A", condition_log=log),
            1000.0,
        )
        r2 = _corr(
            w,
            _cev(alarm="MemMon", rtype="server.Memory", server="B", condition_log=log),
            1001.0,
        )
        assert r1 is False and r2 is True


class TestCorrelationSweepAndBuffer:
    def test_expired_clusters_and_empty_scope_swept(self):
        w = _corr_worker(window=120, min_size=2)
        _corr(w, _cev(db_id="db1", server="A"), 1000.0)
        _corr(w, _cev(db_id="db1", server="B"), 1000.0)
        assert "db1" in w._correlation_clusters
        # 창(120s) 밖 이벤트가 다른 존(db2)에 도착 → sweep이 db1 만료 클러스터 제거·빈 키 삭제.
        _corr(w, _cev(db_id="db2", server="C"), 1000.0 + 121)
        assert "db1" not in w._correlation_clusters   # 빈 스코프 키 삭제
        assert "db2" in w._correlation_clusters        # 신규 존 클러스터

    def test_expired_cluster_reforms_representative(self):
        # 만료 후 동일 스코프 재발 → 새 대표(첫 도착=False), 소급 이력 없음.
        w = _corr_worker(window=120, min_size=2)
        assert _corr(w, _cev(server="A"), 1000.0) is False
        assert _corr(w, _cev(server="B"), 1000.0) is True
        # 창 밖(만료) → 단독 재발은 다시 대표.
        assert _corr(w, _cev(server="C"), 1000.0 + 121) is False

    def test_buffer_cap_evicts_oldest_with_warning(self, caplog):
        # buffer_max=3, 서로 다른 시그니처 4개 → 상한 초과 시 oldest(first_ts 최소) 제거.
        w = _corr_worker(buffer_max=3, sim=0.99, min_size=2)
        with caplog.at_level(logging.WARNING):
            _corr(w, _cev(alarm="CPU"), 1000.0)
            _corr(w, _cev(alarm="MEM"), 1001.0)
            _corr(w, _cev(alarm="DISK"), 1002.0)
            _corr(w, _cev(alarm="NET"), 1003.0)
        clusters = w._correlation_clusters["db1"]
        assert len(clusters) == 3  # 상한 유지
        # oldest(CPU, first_ts=1000)가 제거됐는지 — 남은 first_ts에 1000.0 없음.
        assert all(c.first_ts != 1000.0 for c in clusters)
        assert any("상관 버퍼 상한" in rec.message for rec in caplog.records)


# ─────────────────────────────────────────────────────────────
# C. 정책 단계 — decide_notification step7.5
# ─────────────────────────────────────────────────────────────
def _pevent(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def _pconfig(**kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2, importance_value_map={},
        resolved_to_dashboard=False, cross_host_correlation_enabled=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _pctx() -> dict:
    return {
        "importance_id": None, "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }


class TestCorrelationPolicy:
    def test_correlated_suppressed(self):
        d = decide_notification(
            _pevent(2), None, None, _pctx(),
            _pconfig(cross_host_correlation_enabled=True), correlated=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert "크로스-호스트 상관" in d.reason
        assert d.signals["correlated"] is True

    def test_reason_distinct_from_storm(self):
        # storm 경로 재사용 금지 — "동일 서버 다발" 사유가 감사를 오도하면 안 됨.
        d = decide_notification(
            _pevent(2), None, None, _pctx(),
            _pconfig(cross_host_correlation_enabled=True), correlated=True,
        )
        assert "동일 서버 다발" not in d.reason

    def test_severity3_short_circuits(self):
        # 심각도3은 step3에서 단락 → 군집돼도 PAGE(불변).
        d = decide_notification(
            _pevent(3), None, None, _pctx(),
            _pconfig(cross_host_correlation_enabled=True), correlated=True,
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_flag_off_no_suppression(self):
        # cross_host_correlation_enabled=False → correlated 무시(회귀 0).
        d = decide_notification(
            _pevent(2), None, None, _pctx(),
            _pconfig(cross_host_correlation_enabled=False), correlated=True,
        )
        assert d.tier == TIER_TICKET  # sev2 × 보통 = 매트릭스 TICKET
        assert d.signals["correlated"] is True  # signals에는 스냅샷

    def test_correlated_false_no_suppression(self):
        d = decide_notification(
            _pevent(2), None, None, _pctx(),
            _pconfig(cross_host_correlation_enabled=True), correlated=False,
        )
        assert d.tier == TIER_TICKET
        assert d.signals["correlated"] is False


# ─────────────────────────────────────────────────────────────
# D. 위상 가중(E4 토폴로지 인접성) — 워커 _detect_correlated_storm(graph=…)
# ─────────────────────────────────────────────────────────────
def _topo_worker(
    *, enabled: bool = True, weight: float = 0.2, sim: float = 0.5,
    min_size: int = 2, max_hops: int = 5,
) -> AlarmWorker:
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(
        correlation_window_seconds=120,
        correlation_sim_threshold=sim,
        correlation_min_cluster_size=min_size,
        correlation_buffer_max=1000,
        correlation_topology_weight_enabled=enabled,
        correlation_topology_weight=weight,
        topology_max_hops=max_hops,
    ))
    return AlarmWorker(cfg)


def _sibling_graph() -> DependencyGraph:
    # 서버 A(idA)·B(idB)가 같은 부모 p에 의존(형제) → is_related True. 둘 다 name 보유.
    return DependencyGraph(
        {"idA": ["p"], "idB": ["p"]}, names={"idA": "A", "idB": "B"}
    )


# 서로 다른 rtype으로 필드 Jaccard가 임계(0.5) 미달인 쌍(1/3)을 만든다.
def _ev_low_a(server: str = "A"):
    return _cev(alarm="x", rtype="p", server=server)      # tokens {x, p}


def _ev_low_b(server: str = "B"):
    return _cev(alarm="x", rtype="q", server=server)      # tokens {x, q} → jaccard 1/3


class TestTopologyWeightWorker:
    def test_adjacent_bonus_clusters_below_threshold_pair(self):
        # 필드 Jaccard(1/3<0.5)로는 미군집이나, 인접 토폴로지 보너스(+0.2=0.533)로 군집.
        w = _topo_worker(enabled=True, weight=0.2, sim=0.5, min_size=2)
        g = _sibling_graph()
        c1, m1 = w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=g)
        c2, m2 = w._detect_correlated_storm(_ev_low_b("B"), 1001.0, graph=g)
        assert c1 is False and m1 is None           # 첫 도착 = 대표
        assert c2 is True                            # 위상 보너스로 임계 초과 → 억제
        assert m2["similarity"] == round(1 / 3 + 0.2, 4)  # 보너스 반영·4dp 반올림 유사도

    def test_off_flag_jaccard_bit_identical_even_with_graph(self):
        # 플래그 off면 graph가 주어져도 topo_weight=0.0 → 보너스 없음 → 임계 미달 유지(비트동일).
        w = _topo_worker(enabled=False, weight=0.2, sim=0.5, min_size=2)
        g = _sibling_graph()
        c1, _ = w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=g)
        c2, _ = w._detect_correlated_storm(_ev_low_b("B"), 1001.0, graph=g)
        assert c1 is False and c2 is False           # 보너스 없음 → 미군집

    def test_graph_none_no_bonus(self):
        # graph 미주입(위상 가중 off 경로) → adjacent=None → Jaccard 폴백(미군집).
        w = _topo_worker(enabled=True, weight=0.2, sim=0.5, min_size=2)
        c1, _ = w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=None)
        c2, _ = w._detect_correlated_storm(_ev_low_b("B"), 1001.0, graph=None)
        assert c1 is False and c2 is False

    def test_representative_node_resolved_and_stored(self):
        # 대표 클러스터 생성 시 representative_node = graph.id_of(server_name)로 해소·저장.
        w = _topo_worker(enabled=True)
        g = _sibling_graph()
        w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=g)
        cluster = w._correlation_clusters["db1"][0]
        assert cluster.representative_node == "idA"

    def test_known_mistake_1_root_no_name_graceful(self):
        # Known Mistakes #1: 대표 서버가 엣지 미보유 root(name 부재)면 id_of None →
        # representative_node None → 인접 판정 False → 보너스 미발동(Jaccard 폴백·graceful).
        w = _topo_worker(enabled=True, weight=0.2, sim=0.5, min_size=2)
        # root 서버 "A"는 name 맵에 없음, child 서버 "B"만 name 보유.
        g = DependencyGraph({"idB": ["p"]}, names={"idB": "B"})
        assert g.id_of("A") is None                  # root → 미해소
        c1, _ = w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=g)
        assert w._correlation_clusters["db1"][0].representative_node is None
        c2, _ = w._detect_correlated_storm(_ev_low_b("B"), 1001.0, graph=g)
        assert c1 is False and c2 is False           # 보너스 미발동 → 임계 미달 유지

    def test_topology_bonus_deterministic(self):
        # 동일 이벤트 시퀀스 → 동일 판정(보너스 반영 후에도 결정성 유지).
        g = _sibling_graph()

        def run() -> list[bool]:
            w = _topo_worker(enabled=True, weight=0.2, sim=0.5, min_size=2)
            return [
                w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=g)[0],
                w._detect_correlated_storm(_ev_low_b("B"), 1001.0, graph=g)[0],
            ]

        assert run() == run() == [False, True]


# ─────────────────────────────────────────────────────────────
# E. 클러스터 메타(대표 지문·멤버 순번·유사도) — 억제 시 반환·감사 노출
# ─────────────────────────────────────────────────────────────
class TestClusterMeta:
    def test_meta_on_suppress_only(self):
        # 대표(첫 도착)는 (False, None), 억제 멤버는 (True, meta) 반환.
        w = _corr_worker(min_size=2)
        c1, m1 = w._detect_correlated_storm(_cev(server="A"), 1000.0)
        c2, m2 = w._detect_correlated_storm(_cev(server="B"), 1001.0)
        assert (c1, m1) == (False, None)
        assert c2 is True
        assert m2 == {
            "representative_fp": compute_fingerprint(_cev(server="A")),
            "member_seq": 2,
            "similarity": 1.0,   # 동일 시그니처 → jaccard 1.0
        }

    def test_member_seq_increments(self):
        # 억제가 이어질수록 member_seq(대표 포함 누적 멤버 수)가 증가.
        w = _corr_worker(min_size=2)
        w._detect_correlated_storm(_cev(server="A"), 1000.0)
        _, m2 = w._detect_correlated_storm(_cev(server="B"), 1001.0)
        _, m3 = w._detect_correlated_storm(_cev(server="C"), 1002.0)
        assert m2["member_seq"] == 2
        assert m3["member_seq"] == 3

    def test_similarity_rounded_4dp(self):
        # 위상 보너스 반영 유사도가 소수 4자리로 반올림돼 메타에 노출.
        w = _topo_worker(enabled=True, weight=0.2, sim=0.5, min_size=2)
        g = _sibling_graph()
        w._detect_correlated_storm(_ev_low_a("A"), 1000.0, graph=g)
        _, m2 = w._detect_correlated_storm(_ev_low_b("B"), 1001.0, graph=g)
        assert m2["similarity"] == round(1 / 3 + 0.2, 4)


# ─────────────────────────────────────────────────────────────
# F. decision_store.record — correlation_meta 최상위 필드(signals 미훼손)
# ─────────────────────────────────────────────────────────────
def _decision() -> NotificationDecision:
    return NotificationDecision(
        tier=TIER_SUPPRESS, reason="크로스-호스트 상관 — 클러스터 대표 외 억제",
        priority=0, signals={"correlated": True, "severity": 2}, fingerprint="fp-b",
    )


class TestDecisionStoreCorrelationMeta:
    def test_meta_recorded_top_level(self, tmp_path):
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path))
        meta = {"representative_fp": "fp-a", "member_seq": 2, "similarity": 0.9333}
        store.record(_decision(), alarm_id="A-2", correlation_meta=meta)
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        assert rows[0]["correlation_meta"] == meta       # 최상위 필드
        assert "correlation_meta" not in rows[0]["signals"]  # signals 동결 스키마 미훼손
        assert rows[0]["signals"] == {"correlated": True, "severity": 2}

    def test_none_meta_omits_key(self, tmp_path):
        # correlation_meta=None(기본)이면 키 미포함(기존 스냅샷 무훼손).
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path))
        store.record(_decision(), alarm_id="A-2")
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        assert "correlation_meta" not in rows[0]

    def test_recurrence_and_correlation_meta_coexist(self, tmp_path):
        # E1 recurrence·E2 correlation_meta는 서로 독립인 최상위 필드로 공존 가능.
        path = tmp_path / "d.jsonl"
        store = DecisionStore(str(path))
        store.record(
            _decision(), alarm_id="A-3",
            recurrence={"count": 2}, correlation_meta={"member_seq": 2},
        )
        rows = [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
        assert rows[0]["recurrence"] == {"count": 2}
        assert rows[0]["correlation_meta"] == {"member_seq": 2}
