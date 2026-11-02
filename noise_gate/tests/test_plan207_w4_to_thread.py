"""plans/207 트랙 B W4 — 임베딩 유사도 계산의 스레드 이전(C-6).

- 임베딩을 쓰지 않는 경로(provider 미주입 · inert · 플래그 off · root 부재 · 비교 후보 없음)는
  `asyncio.to_thread`를 부르지 않는다(D-162 — off 경로 현행 그대로).
- 임베딩을 쓰는 경로는 주석 값·최근 텍스트 상태가 동기 판과 같고, 계산(`most_similar`·
  `similarity`)은 이벤트 루프 스레드가 아닌 스레드에서 돈다.
- 알람 워커 `_process`가 async 변형을 쓰고, 가짜 provider를 켠 하네스 결과가 동기 판(W4 이전)과
  같다 — N=1·N=4 모두.

가짜 provider만 쓴다(실 E5 모델 로드 없음).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_context_enricher import (
    _annotate_root_text_similarity,
    _annotate_root_text_similarity_async,
    enrich_noise_context,
)
from noise_gate.infrastructure.embedding_provider import build_event_text
from noise_gate.tests.equivalence_harness import (
    assert_equivalent,
    concurrent_executor,
    run_sequence,
    serial_executor,
    synthetic_alarms,
)

TEXT_A = build_event_text("CPU high", "server.Cpus", "95")
TEXT_B = build_event_text("CPU elevated", "server.Cpus", "94")


def _score(a: str, b: str) -> float:
    return 0.9 if frozenset({a, b}) == frozenset({TEXT_A, TEXT_B}) else 0.1


class _ThreadRecordingProvider:
    """AlarmEmbeddingProvider 대역 — 결정적 점수 + 호출 스레드 기록."""

    def __init__(
        self, score_fn: Callable[[str, str], float] = _score, *, available: bool = True
    ) -> None:
        self._score_fn = score_fn
        self._available = available
        self.threads: list[int] = []

    def is_available(self) -> bool:
        return self._available

    def similarity(self, a: str, b: str) -> float | None:
        self.threads.append(threading.get_ident())
        if not self._available:
            return None
        return self._score_fn(a, b)

    def most_similar(self, query: str, candidates: list[str]) -> tuple[int, float] | None:
        self.threads.append(threading.get_ident())
        if not self._available or not candidates:
            return None
        scores = [(i, self._score_fn(query, c)) for i, c in enumerate(candidates)]
        return max(scores, key=lambda x: x[1])


class _ToThreadSpy:
    """`asyncio.to_thread` 감시 — 호출 횟수를 세고 실제 to_thread로 넘긴다."""

    def __init__(self) -> None:
        self.calls = 0
        self._real = asyncio.to_thread

    async def __call__(self, func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return await self._real(func, *args, **kwargs)


@pytest.fixture
def to_thread_spy(monkeypatch: pytest.MonkeyPatch) -> _ToThreadSpy:
    spy = _ToThreadSpy()
    monkeypatch.setattr(asyncio, "to_thread", spy)
    return spy


def _ev(alarm_name: str, clog: str = "95", db_id: str = "db1") -> SimpleNamespace:
    return SimpleNamespace(
        db_id=db_id, alarm_name=alarm_name, resource_type="server.Cpus",
        condition_log=clog, is_clear=False,
    )


def _worker(provider: Any) -> AlarmWorker:
    w = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
        embedding_similarity_threshold=0.87, repeat_interval_seconds=14400,
    )))
    w._embedding_provider = provider
    return w


def _texts_state(w: AlarmWorker) -> dict[str, list[tuple[str, str, float]]]:
    return {k: list(v) for k, v in w._recent_event_texts.items()}


# 근접중복 후보 · 임계 미만 · 동일 지문 · 다른 스코프 · TTL 밖을 고루 지나는 순서.
_SEQUENCE = [
    (_ev("CPU high"), "fp_a", 1000.0),
    (_ev("CPU elevated", clog="94"), "fp_b", 1010.0),
    (_ev("Disk full", clog="99"), "fp_d", 1020.0),
    (_ev("CPU high"), "fp_a", 1030.0),
    (_ev("CPU elevated", clog="94", db_id="db2"), "fp_b2", 1040.0),
    (_ev("CPU high", db_id="db2"), "fp_a2", 1050.0),
    (_ev("CPU elevated", clog="94"), "fp_b", 20000.0),
]


# ─────────────────────────────────────────────────────────────
# P3 — 의미 근접중복 주석 (DedupService / AlarmWorker 위임)
# ─────────────────────────────────────────────────────────────
class TestSemanticDedupOffPaths:
    async def test_provider_none_no_thread(self, to_thread_spy: _ToThreadSpy) -> None:
        w = _worker(None)
        assert await w._annotate_semantic_dedup_async(_ev("CPU high"), "fp", 1000.0) is None
        assert to_thread_spy.calls == 0
        assert w._recent_event_texts == {}

    async def test_provider_inert_no_thread(self, to_thread_spy: _ToThreadSpy) -> None:
        provider = _ThreadRecordingProvider(available=False)
        w = _worker(provider)
        assert await w._annotate_semantic_dedup_async(_ev("CPU high"), "fp", 1000.0) is None
        assert to_thread_spy.calls == 0
        assert provider.threads == []
        assert w._recent_event_texts == {}

    async def test_empty_text_no_thread(self, to_thread_spy: _ToThreadSpy) -> None:
        w = _worker(_ThreadRecordingProvider())
        ev = SimpleNamespace(db_id="db1", alarm_name="", resource_type="", condition_log="",
                             is_clear=False)
        assert await w._annotate_semantic_dedup_async(ev, "fp", 1000.0) is None
        assert to_thread_spy.calls == 0

    async def test_no_candidates_no_thread_but_state_appended(
        self, to_thread_spy: _ToThreadSpy
    ) -> None:
        # 첫 이벤트는 비교 후보가 없어 계산 없음 — 상태 추가는 동기 판과 같다.
        provider = _ThreadRecordingProvider()
        w = _worker(provider)
        assert await w._annotate_semantic_dedup_async(_ev("CPU high"), "fp_a", 1000.0) is None
        assert to_thread_spy.calls == 0
        assert provider.threads == []
        assert _texts_state(w) == {"db1": [("fp_a", TEXT_A, 1000.0)]}


class TestSemanticDedupOnPath:
    async def test_async_matches_sync_annotations_and_state(
        self, to_thread_spy: _ToThreadSpy
    ) -> None:
        w_sync = _worker(_ThreadRecordingProvider())
        w_async = _worker(_ThreadRecordingProvider())
        sync_out = [w_sync._annotate_semantic_dedup(ev, fp, now) for ev, fp, now in _SEQUENCE]
        async_out = [
            await w_async._annotate_semantic_dedup_async(ev, fp, now)
            for ev, fp, now in _SEQUENCE
        ]
        assert async_out == sync_out
        assert _texts_state(w_async) == _texts_state(w_sync)
        # 근접중복 후보가 실제로 나왔다(동등 비교가 빈 결과끼리가 아니다).
        assert sync_out[1] is not None
        assert sync_out[1]["semantic_near_dup"]["matched_fp"] == "fp_a"
        assert to_thread_spy.calls > 0

    async def test_most_similar_runs_off_loop_thread(self) -> None:
        provider = _ThreadRecordingProvider()
        w = _worker(provider)
        loop_thread = threading.get_ident()
        await w._annotate_semantic_dedup_async(_ev("CPU high"), "fp_a", 1000.0)
        ann = await w._annotate_semantic_dedup_async(
            _ev("CPU elevated", clog="94"), "fp_b", 1010.0
        )
        assert ann is not None and ann["semantic_near_dup"]["similarity"] == 0.9
        assert provider.threads and all(t != loop_thread for t in provider.threads)

    def test_sync_seam_still_runs_on_caller_thread(self) -> None:
        # 기존 동기 seam은 그대로 — 호출 스레드에서 계산한다.
        provider = _ThreadRecordingProvider()
        w = _worker(provider)
        w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)
        w._annotate_semantic_dedup(_ev("CPU elevated", clog="94"), "fp_b", 1010.0)
        assert provider.threads == [threading.get_ident()]


# ─────────────────────────────────────────────────────────────
# P4 — enricher root_text_similarity
# ─────────────────────────────────────────────────────────────
def _l4_event() -> SimpleNamespace:
    return SimpleNamespace(
        alarm_name="CPU high", resource_type="server.Cpus", condition_log="95",
        db_id="polestar_cm_gp", server_name="srv-1",
    )


def _root_ctx(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        importance_id=None, maintenance=False, noti_policy=None,
        parent_avail_status=None, cascaded=True, root_resource="R",
        root_resource_name="CPU high", source="polestar_db", root_notified=True,
    )
    base.update(over)
    return base


def _fusion(on: bool) -> SimpleNamespace:
    return SimpleNamespace(topology_text_fusion_enabled=on)


class TestRootTextSimilarity:
    @pytest.mark.parametrize(
        ("cfg", "provider", "ctx"),
        [
            (_fusion(True), None, _root_ctx()),
            (_fusion(False), "p", _root_ctx()),
            (SimpleNamespace(), "p", _root_ctx()),
            (_fusion(True), "p", _root_ctx(root_resource=None, root_resource_name=None)),
            (_fusion(True), "p", _root_ctx(root_resource_name="")),
        ],
        ids=["provider-none", "flag-off", "flag-absent", "root-absent", "root-name-empty"],
    )
    async def test_off_paths_no_thread_no_key(
        self, to_thread_spy: _ToThreadSpy, cfg: Any, provider: Any, ctx: dict[str, Any]
    ) -> None:
        prov = _ThreadRecordingProvider(lambda a, b: 0.73) if provider else None
        before = dict(ctx)
        await _annotate_root_text_similarity_async(_l4_event(), ctx, cfg, prov)
        assert to_thread_spy.calls == 0
        assert ctx == before
        if prov is not None:
            assert prov.threads == []

    async def test_on_matches_sync_and_runs_off_loop(
        self, to_thread_spy: _ToThreadSpy
    ) -> None:
        prov_sync = _ThreadRecordingProvider(lambda a, b: 0.731234)
        prov_async = _ThreadRecordingProvider(lambda a, b: 0.731234)
        ctx_sync, ctx_async = _root_ctx(), _root_ctx()
        _annotate_root_text_similarity(_l4_event(), ctx_sync, _fusion(True), prov_sync)
        await _annotate_root_text_similarity_async(
            _l4_event(), ctx_async, _fusion(True), prov_async
        )
        assert ctx_async == ctx_sync
        assert ctx_async["root_text_similarity"] == 0.7312
        assert to_thread_spy.calls == 1
        assert prov_async.threads and prov_async.threads[0] != threading.get_ident()

    async def test_inert_provider_no_key(self) -> None:
        # inert면 스레드에서 None이 돌아와 미첨부 — 동기 판과 같다.
        ctx = _root_ctx()
        await _annotate_root_text_similarity_async(
            _l4_event(), ctx, _fusion(True), _ThreadRecordingProvider(available=False)
        )
        assert "root_text_similarity" not in ctx

    async def test_enrich_noise_context_uses_thread(self, to_thread_spy: _ToThreadSpy) -> None:
        class _Repo:
            async def fetch(self, event: Any, **kw: Any) -> dict[str, Any]:
                return _root_ctx()

        cfg = SimpleNamespace(
            noise_context_cache_ttl_seconds=0, inhibition_window_seconds=300,
            topology_max_hops=5, topology_cache_ttl_seconds=86400,
            change_window_seconds=3600, topology_text_fusion_enabled=True,
        )
        prov = _ThreadRecordingProvider(lambda a, b: 0.66)
        out = await enrich_noise_context(
            _l4_event(), cfg, _Repo(), None, multi_hop=True, active_firings={},
            embedding_provider=prov,
        )
        assert out["root_text_similarity"] == 0.66
        assert to_thread_spy.calls == 1
        assert prov.threads[0] != threading.get_ident()

        to_thread_spy.calls = 0
        off = await enrich_noise_context(
            _l4_event(), cfg, _Repo(), None, multi_hop=True, active_firings={},
            embedding_provider=None,
        )
        assert "root_text_similarity" not in off
        assert to_thread_spy.calls == 0


# ─────────────────────────────────────────────────────────────
# 워커 `_process` 배선 — 하네스(가짜 그래프)로 W4 이전 동기 판과 동등
# ─────────────────────────────────────────────────────────────
def _same_name_score(a: str, b: str) -> float:
    """텍스트 첫 성분(알람명)이 같으면 근접중복 — 다른 호스트 같은 알람이 후보가 된다."""
    return 0.95 if a.split()[:1] == b.split()[:1] else 0.1


def _inject_provider(monkeypatch: pytest.MonkeyPatch, provider: Any) -> None:
    """하네스가 만드는 워커에 가짜 provider를 넣는다(하네스는 `run()`을 거치지 않는다)."""
    real_init = AlarmWorker.__init__

    def init(self: AlarmWorker, *a: Any, **k: Any) -> None:
        real_init(self, *a, **k)
        self._embedding_provider = provider

    monkeypatch.setattr(AlarmWorker, "__init__", init)


def _use_sync_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """W4 이전 경로 재현 — `_process`의 async 변형을 동기 seam으로 돌린다."""

    async def legacy(self: AlarmWorker, event: Any, fingerprint: str, now: float) -> Any:
        return self._annotate_semantic_dedup(event, fingerprint, now)

    monkeypatch.setattr(AlarmWorker, "_annotate_semantic_dedup_async", legacy)


class TestWorkerProcessWiring:
    async def test_process_off_no_thread(
        self, tmp_path: Path, to_thread_spy: _ToThreadSpy
    ) -> None:
        # provider 미주입(현행 기본) — `_process` 전 구간에서 to_thread 0회.
        await run_sequence(synthetic_alarms(), work_dir=tmp_path)
        assert to_thread_spy.calls == 0

    @pytest.mark.parametrize("n", [1, 4])
    async def test_process_on_matches_pre_w4(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, n: int
    ) -> None:
        executor = serial_executor if n == 1 else concurrent_executor(n)
        alarms = synthetic_alarms()

        legacy_provider = _ThreadRecordingProvider(_same_name_score)
        with monkeypatch.context() as m:
            _inject_provider(m, legacy_provider)
            _use_sync_seam(m)
            legacy = await run_sequence(
                alarms, work_dir=tmp_path / "legacy", executor=executor
            )

        spy = _ToThreadSpy()
        provider = _ThreadRecordingProvider(_same_name_score)
        with monkeypatch.context() as m:
            m.setattr(asyncio, "to_thread", spy)
            _inject_provider(m, provider)
            threaded = await run_sequence(
                alarms, work_dir=tmp_path / "threaded", executor=executor
            )

        assert_equivalent(legacy, threaded)
        # 주석이 실제로 실렸다(동등 비교가 빈 주석끼리가 아니다).
        assert any("semantic_near_dup" in rec for rec in threaded.records)
        assert spy.calls > 0
        loop_thread = threading.get_ident()
        assert provider.threads and all(t != loop_thread for t in provider.threads)
        assert legacy_provider.threads and all(
            t == loop_thread for t in legacy_provider.threads
        )


# ─────────────────────────────────────────────────────────────
# provider encode 직렬화 — 캐시 적중 경로는 encode 대기에 막히지 않는다
# ─────────────────────────────────────────────────────────────
class _VecEmbedder:
    """가짜 SentenceTransformer — 텍스트 수만큼 같은 벡터."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts: list[str], **_k: Any) -> Any:
        import numpy as np

        self.calls += 1
        return np.ones((len(texts), 4), dtype=np.float32)


class TestProviderEncodeLock:
    def test_cache_hit_not_blocked_by_encode_lock(self) -> None:
        from noise_gate.infrastructure.embedding_provider import AlarmEmbeddingProvider

        prov = AlarmEmbeddingProvider("")
        emb = _VecEmbedder()
        prov._embedder = emb  # 로드된 상태로 둔다(실 모델 미사용)
        assert prov.similarity("cpu high", "disk full") == 1.0
        assert emb.calls == 1

        done = threading.Event()
        result: list[float | None] = []

        def hit() -> None:
            result.append(prov.similarity("cpu high", "disk full"))
            done.set()

        with prov._encode_lock:  # 다른 스레드가 encode 중인 상황
            t = threading.Thread(target=hit)
            t.start()
            assert done.wait(1.0)  # 캐시 적중은 encode Lock을 기다리지 않는다
        t.join()
        assert result == [1.0]
        assert emb.calls == 1

    def test_miss_waits_for_encode_lock(self) -> None:
        from noise_gate.infrastructure.embedding_provider import AlarmEmbeddingProvider

        prov = AlarmEmbeddingProvider("")
        emb = _VecEmbedder()
        prov._embedder = emb
        done = threading.Event()

        def miss() -> None:
            prov.similarity("cpu high", "disk full")
            done.set()

        with prov._encode_lock:
            t = threading.Thread(target=miss)
            t.start()
            assert not done.wait(0.1)  # 캐시 미스는 encode Lock이 풀릴 때까지 기다린다
            assert emb.calls == 0
        t.join(1.0)
        assert done.is_set() and emb.calls == 1
