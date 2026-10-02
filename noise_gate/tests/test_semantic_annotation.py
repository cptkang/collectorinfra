"""Plan 60 B-7 L-2/L-4 임베딩 주석 배선 테스트 (§15.3 · §15.4 D-035 경계 · §10).

fake provider 주입으로 결정적으로 검증한다(실모델은 범위 밖):
  L-2(워커 `_annotate_semantic_dedup`): 근접중복 후보 주석 생성(임계 이상·다른 지문)·임계
     미만/동일 지문/후보없음 미생성·provider 미주입/inert graceful·recent_texts 상한·만료 sweep.
  L-4(enricher `_annotate_root_text_similarity`/`enrich_noise_context`): root_text_similarity
     주석 첨부·off/None/inert/root부재 미첨부·cascaded/root_notified 판정 불변.
  D-035 불변 단언(핵심 회귀 가드): 임베딩 주석이 있어도/없어도 게이트 결정(tier/reason/priority)
     비트동일 — 주석은 decision_store 최상위 필드에만 실리고 판정에 영향 0.

event/config/ctx는 노드가 덕 타이핑으로 소비하므로 SimpleNamespace/dict로 구성한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Optional

from noise_gate.application.alarm_worker import (
    _RECENT_TEXTS_MAX_PER_SCOPE,
    AlarmWorker,
)
from noise_gate.application.nodes.alarm_context_enricher import (
    _annotate_root_text_similarity,
    enrich_noise_context,
)
from noise_gate.application.nodes.notification_gate import notification_gate_node
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import TIER_PAGE
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.embedding_provider import build_event_text

# ── 결정적 텍스트·점수 맵 ────────────────────────────────────────────
# A와 B만 근접중복(0.9), 그 외 쌍은 0.1 — 임계 0.87 기준 A~B만 후보.
TEXT_A = build_event_text("CPU high", "server.Cpus", "95")
TEXT_B = build_event_text("CPU elevated", "server.Cpus", "94")
TEXT_D = build_event_text("Disk full", "server.Disks", "99")


def _score(a: str, b: str) -> float:
    return 0.9 if frozenset({a, b}) == frozenset({TEXT_A, TEXT_B}) else 0.1


class _FakeProvider:
    """AlarmEmbeddingProvider 대역 — similarity/most_similar를 결정적 점수로 반환.

    available=False면 inert(모든 조회 None)를 모사한다(모델 미가용 graceful 검증용).
    """

    def __init__(self, score_fn=_score, *, available: bool = True) -> None:  # noqa: ANN001
        self._score_fn = score_fn
        self._available = available
        self.similarity_calls: list[tuple[str, str]] = []
        self.most_similar_calls: list[tuple[str, list[str]]] = []

    def is_available(self) -> bool:
        return self._available

    def similarity(self, a: str, b: str) -> Optional[float]:
        if not self._available:
            return None
        self.similarity_calls.append((a, b))
        return self._score_fn(a, b)

    def most_similar(self, query: str, candidates: list[str]):  # noqa: ANN201
        if not self._available or not candidates:
            return None
        self.most_similar_calls.append((query, list(candidates)))
        scores = [(i, self._score_fn(query, c)) for i, c in enumerate(candidates)]
        return max(scores, key=lambda x: x[1])


def _ev(alarm_name: str, rtype: str = "server.Cpus", clog: str = "95",
        db_id: str = "db1") -> SimpleNamespace:
    return SimpleNamespace(
        db_id=db_id, alarm_name=alarm_name, resource_type=rtype,
        condition_log=clog, is_clear=False,
    )


def _worker_cfg(*, threshold: float = 0.87, ttl: int = 14400) -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(
        embedding_similarity_threshold=threshold, repeat_interval_seconds=ttl,
    ))


def _worker(**cfg_over) -> AlarmWorker:
    w = AlarmWorker(_worker_cfg(**cfg_over))
    w._embedding_provider = _FakeProvider()
    return w


# ─────────────────────────────────────────────────────────────
# L-2. 워커 근접중복 후보 주석 산출
# ─────────────────────────────────────────────────────────────
class TestL2Annotation:
    def test_near_dup_annotation_generated(self):
        # 이전 이벤트(A/fp_a) 시드 → 유사 이벤트(B/fp_b)가 임계 이상·다른 지문 → 후보 주석.
        w = _worker()
        assert w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0) is None
        ann = w._annotate_semantic_dedup(
            _ev("CPU elevated", clog="94"), "fp_b", 1001.0
        )
        assert ann is not None
        nd = ann["semantic_near_dup"]
        assert nd["matched_fp"] == "fp_a"
        assert nd["similarity"] == 0.9
        assert "근접중복" in nd["hint"]

    def test_below_threshold_no_annotation(self):
        # D(비유사·0.1)는 임계(0.87) 미만 → 주석 미생성.
        w = _worker()
        w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)
        ann = w._annotate_semantic_dedup(
            _ev("Disk full", rtype="server.Disks", clog="99"), "fp_d", 1001.0
        )
        assert ann is None

    def test_same_fingerprint_no_annotation(self):
        # 유사도 임계 이상이라도 매칭 후보 지문이 현재와 동일하면 주석 미생성
        # (동일 지문 재발은 결정적 dedup이 처리 — L-2는 다른 지문 후보만 주석).
        w = _worker()
        w._annotate_semantic_dedup(_ev("CPU high"), "fp_same", 1000.0)
        ann = w._annotate_semantic_dedup(
            _ev("CPU elevated", clog="94"), "fp_same", 1001.0
        )
        assert ann is None

    def test_first_event_no_candidates_no_annotation(self):
        # 스코프 첫 이벤트는 비교 후보가 없어 주석 미생성(상태만 시드).
        w = _worker()
        ann = w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)
        assert ann is None
        assert len(w._recent_event_texts["db1"]) == 1

    def test_provider_none_skips_and_state_untouched(self):
        # provider 미주입(두 플래그 off/생성 실패) → 즉시 None, recent_texts 무변경(비용 0).
        w = _worker()
        w._embedding_provider = None
        assert w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0) is None
        assert w._recent_event_texts == {}

    def test_provider_inert_skips_and_state_untouched(self):
        # provider inert(모델 미가용) → None, recent_texts 무변경(graceful·회귀 0).
        w = _worker()
        w._embedding_provider = _FakeProvider(available=False)
        assert w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0) is None
        assert w._recent_event_texts == {}


# ─────────────────────────────────────────────────────────────
# L-2. 워커 recent_texts 상태 — 상한·만료 sweep·빈 스코프 삭제
# ─────────────────────────────────────────────────────────────
class TestL2RecentTextsState:
    def test_deque_bounded_by_maxlen(self):
        w = _worker()
        # 동일 스코프에 상한+10건 투입(now 고정 → 만료 없음) → deque는 maxlen으로 제한.
        for i in range(_RECENT_TEXTS_MAX_PER_SCOPE + 10):
            w._annotate_semantic_dedup(_ev(f"alarm-{i}"), f"fp-{i}", 1000.0)
        assert len(w._recent_event_texts["db1"]) == _RECENT_TEXTS_MAX_PER_SCOPE

    def test_expiry_sweep_removes_empty_scope(self):
        # db1 이벤트 후 ttl+1 경과한 db2 이벤트 처리 시, db1의 만료 항목이 popleft되어
        # 빈 스코프 db1 키가 삭제된다(형제 상태 정리 루프와 일관).
        w = _worker(ttl=100)
        w._annotate_semantic_dedup(_ev("CPU high", db_id="db1"), "fp_a", 1000.0)
        w._annotate_semantic_dedup(_ev("CPU high", db_id="db2"), "fp_x", 1101.0)
        assert "db1" not in w._recent_event_texts   # 만료 → 빈 스코프 삭제
        assert list(w._recent_event_texts.keys()) == ["db2"]
        assert len(w._recent_event_texts["db2"]) == 1

    def test_expired_candidate_not_matched(self):
        # 만료된 후보(ttl 밖)는 비교 대상에서 제외 → 유사해도 주석 미생성(창 존중).
        w = _worker(ttl=100)
        w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)
        ann = w._annotate_semantic_dedup(
            _ev("CPU elevated", clog="94"), "fp_b", 1101.0
        )
        assert ann is None  # A는 만료 → 후보 제외
        assert len(w._recent_event_texts["db1"]) == 1  # A popleft, B만 잔류


# ─────────────────────────────────────────────────────────────
# D-035 불변 단언 (핵심 회귀 가드) — 주석 유무와 게이트 결정 비트동일
# ─────────────────────────────────────────────────────────────
REF = datetime(2026, 7, 21, 10, 0, 0)


def _full_event(**over) -> AlarmEvent:
    base = dict(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Cpus", alarm_id="A-1", severity=3,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="svr-1-CPU",
        alarm_name="CPU 임계", alarm_time=REF, conditions="", condition_log="95",
        is_clear=False,
    )
    base.update(over)
    return AlarmEvent(**base)


def _gate_cfg() -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(
        enable_noise_gate=True, suppress_max_severity=2,
        importance_value_map={"HIGH": "높음"}, resolved_to_dashboard=False,
    ))


def _ctx() -> dict:
    return {
        "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }


async def _run_gate(store, *, semantic_annotation=None):  # noqa: ANN001
    state = {
        "alarm_event": _full_event(),
        "history_stats": None,
        "analysis_result": SimpleNamespace(pattern_type="", is_routine=None),
        "error": None,
        "noise_context": _ctx(),
        "self_heal": False,
        "semantic_annotation": semantic_annotation,
    }
    config = {"configurable": {"app_config": _gate_cfg(), "decision_store": store}}
    return await notification_gate_node(state, config)


class TestD035TierInvariance:
    async def test_decision_identical_with_and_without_annotation(self, tmp_path):
        # 주석 유무와 무관하게 tier/reason/priority 비트동일(임베딩은 판정에 영향 0).
        s_off = DecisionStore(str(tmp_path / "off.jsonl"))
        s_on = DecisionStore(str(tmp_path / "on.jsonl"))
        ann = {"semantic_near_dup": {"matched_fp": "fp_x", "similarity": 0.91,
                                     "hint": "의미적 근접중복 — 재발 count 병합 후보(검토용)"}}
        d_off = (await _run_gate(s_off))["notification_decision"]
        d_on = (await _run_gate(s_on, semantic_annotation=ann))["notification_decision"]
        assert d_off.tier == d_on.tier == TIER_PAGE
        assert d_off.reason == d_on.reason
        assert d_off.priority == d_on.priority
        assert d_off.signals == d_on.signals  # 동결 스키마도 동일(주석은 signals 밖)

    async def test_annotation_recorded_as_top_level_field_only_when_present(
        self, tmp_path
    ):
        # 주석이 있으면 decision_store 최상위 semantic_annotation 필드로 적재, 없으면 키 부재.
        p_on = tmp_path / "on.jsonl"
        p_off = tmp_path / "off.jsonl"
        ann = {"semantic_near_dup": {"matched_fp": "fp_x", "similarity": 0.91,
                                     "hint": "h"}}
        await _run_gate(DecisionStore(str(p_on)), semantic_annotation=ann)
        await _run_gate(DecisionStore(str(p_off)))
        rec_on = json.loads(p_on.read_text(encoding="utf-8").splitlines()[0])
        rec_off = json.loads(p_off.read_text(encoding="utf-8").splitlines()[0])
        assert rec_on["semantic_annotation"] == ann
        assert "semantic_annotation" not in rec_off
        # signals 동결 스키마에 근접중복이 새지 않음(§8.2).
        assert "semantic_near_dup" not in rec_on["signals"]


# ─────────────────────────────────────────────────────────────
# L-4. enricher 토폴로지+텍스트 융합 주석 (root_text_similarity)
# ─────────────────────────────────────────────────────────────
def _l4_event() -> SimpleNamespace:
    return SimpleNamespace(
        alarm_name="CPU high", resource_type="server.Cpus", condition_log="95",
        db_id="polestar_cm_gp", server_name="srv-1",
    )


def _root_ctx(**over) -> dict:
    base = dict(
        importance_id=None, maintenance=False, noti_policy=None,
        parent_avail_status=None, cascaded=True, root_resource="R",
        root_resource_name="CPU high", source="polestar_db", root_notified=True,
    )
    base.update(over)
    return base


def _l4_cfg(*, fusion: bool) -> SimpleNamespace:
    return SimpleNamespace(topology_text_fusion_enabled=fusion)


class TestL4RootTextSimilarity:
    def test_similarity_attached_when_on(self):
        ctx = _root_ctx()  # root NAME "CPU high" == 알람텍스트 성분 → 유사도 산출.
        _annotate_root_text_similarity(_l4_event(), ctx, _l4_cfg(fusion=True),
                                       _FakeProvider(score_fn=lambda a, b: 0.73))
        assert ctx["root_text_similarity"] == 0.73

    def test_flag_off_no_key(self):
        ctx = _root_ctx()
        _annotate_root_text_similarity(_l4_event(), ctx, _l4_cfg(fusion=False),
                                       _FakeProvider(score_fn=lambda a, b: 0.73))
        assert "root_text_similarity" not in ctx

    def test_provider_none_no_key(self):
        ctx = _root_ctx()
        _annotate_root_text_similarity(_l4_event(), ctx, _l4_cfg(fusion=True), None)
        assert "root_text_similarity" not in ctx

    def test_provider_inert_no_key(self):
        ctx = _root_ctx()
        _annotate_root_text_similarity(_l4_event(), ctx, _l4_cfg(fusion=True),
                                       _FakeProvider(available=False))
        assert "root_text_similarity" not in ctx

    def test_root_absent_no_key(self):
        # cascaded=False → root_resource None → 유사도 미산출(하위호환).
        ctx = _root_ctx(cascaded=False, root_resource=None, root_resource_name=None)
        _annotate_root_text_similarity(_l4_event(), ctx, _l4_cfg(fusion=True),
                                       _FakeProvider(score_fn=lambda a, b: 0.73))
        assert "root_text_similarity" not in ctx


# ── L-4 enrich_noise_context 통합 — cascaded/root_notified 판정 불변 ──
class _FakeNoiseRepo:
    """repo.fetch 대역 — 다홉 cascade 산출값을 고정 반환(엔진·SQL 무관 결정적)."""

    def __init__(self, ctx: dict) -> None:
        self._ctx = ctx

    def is_db_registered(self, db_id: str) -> bool:
        return True

    async def fetch(self, event, **kw) -> dict:  # noqa: ANN001
        return dict(self._ctx)


def _noise_cfg() -> SimpleNamespace:
    return SimpleNamespace(
        noise_context_cache_ttl_seconds=0, inhibition_window_seconds=300,
        topology_max_hops=5, topology_cache_ttl_seconds=86400,
        change_window_seconds=3600, topology_text_fusion_enabled=True,
    )


class TestL4EnrichNoiseContextInvariance:
    async def test_cascaded_root_unchanged_by_annotation(self):
        # provider 유무와 무관하게 cascaded/root_resource/root_notified 동일 —
        # 임베딩은 root_text_similarity 키만 추가하고 판정 필드를 바꾸지 않는다(D-035).
        repo_ctx = dict(
            importance_id=None, maintenance=False, noti_policy=None,
            parent_avail_status=None, cascaded=True, root_resource="R",
            root_resource_name="CPU high", source="polestar_db",
        )
        firings = {"polestar_cm_gp|CPU high": (2, 990.0, "AlarmX")}
        args = dict(collect_dependency=True, multi_hop=True, active_firings=firings)

        no_prov = await enrich_noise_context(
            _l4_event(), _noise_cfg(), _FakeNoiseRepo(repo_ctx), None,
            embedding_provider=None, **args,
        )
        with_prov = await enrich_noise_context(
            _l4_event(), _noise_cfg(), _FakeNoiseRepo(repo_ctx), None,
            embedding_provider=_FakeProvider(score_fn=lambda a, b: 0.66), **args,
        )
        for k in ("cascaded", "root_resource", "root_resource_name", "root_notified"):
            assert no_prov[k] == with_prov[k]
        assert "root_text_similarity" not in no_prov       # provider None → 미첨부
        assert with_prov["root_text_similarity"] == 0.66    # provider 가용 → 첨부
