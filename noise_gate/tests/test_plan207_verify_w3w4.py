"""plans/207 트랙 B W3(C-5 종료 드레인 · 기동 시 미ACK 회수) · W4(C-6 임베딩 스레드 이전) 독립 검증.

implementer 테스트(`test_plan207_w3_drain.py` · `test_plan207_w4_to_thread.py`)가 비운 자리만 본다.

- 드레인: 자리가 꽉 찬 상태(`free <= 0` 대기)에서 취소 · 드레인 뒤 태스크 누수 0 · 드레인 시간
  0에서 P3 전 대기 건도 pending(교착 없이 끝남) · 드레인 중 진행 건이 예외를 내면 그 건은 ACK +
  dead-letter이고 남은 것은 pending.
- 회수: 트림된 pending(빈 필드)은 dead-letter → ACK · 회수한 건은 뒤 `>` 읽기에서 다시 오지 않음 ·
  회수 중 취소는 N과 무관하게 처리 중 1건을 ACK한다(현행 고정 — 보고 지적 대상).
- W4: provider 예외는 async 판도 같은 예외 그대로(동기 판과 같은 dead-letter 사유) · 최근 텍스트
  상태 무변경 · 계산 중 같은 스코프에 항목이 늘어도 결과 인덱스는 스냅샷 기준 · N≥2 드레인 0 취소가
  스레드 계산 도중이면 pending + 최근 텍스트 무변경 · provider Lock은 캐시만 지키고 모델 `encode`는
  P3·P4 스레드에서 겹칠 수 있다(현행 사실 고정 — 실 모델 스레드 안전성은 폐쇄망 확인 항목).

실 LLM·실 Redis·실 임베딩 모델을 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.nodes.alarm_context_enricher import (
    _annotate_root_text_similarity_async,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.embedding_provider import (
    AlarmEmbeddingProvider,
    build_event_text,
)
from noise_gate.infrastructure.redis_queue import read_messages
from noise_gate.tests.equivalence_harness import (
    CONSUMER,
    STREAM_KEY,
    FakeStreamRedis,
    harness_config,
    polestar_payload,
    write_enforce_rules,
)

_BASE = datetime(2026, 11, 2, 9, 0, 0)


class _YieldingRedis(FakeStreamRedis):
    """빈 읽기면 잠깐 양보한다(`block_ms` 흉내)."""

    async def xreadgroup(self, *a: Any, **k: Any) -> list[list[Any]]:
        out = await super().xreadgroup(*a, **k)
        if not out or all(not batch for _key, batch in out):
            await asyncio.sleep(0.002)
        return out


def _worker(tmp_path: Path) -> tuple[AlarmWorker, _YieldingRedis]:
    cfg = harness_config(write_enforce_rules(tmp_path))
    worker = AlarmWorker(cfg)
    r = _YieldingRedis()
    worker._redis = r  # type: ignore[assignment]
    worker._decision_store = DecisionStore(str(tmp_path / "d.jsonl"))
    worker._episodes = worker._build_episode_tracker()
    worker._page_registry = worker._build_page_registry()
    return worker, r


def _add(r: FakeStreamRedis, alarm_id: str, *, host: str, **kw: Any) -> bytes:
    payload = polestar_payload(alarm_id, kw.pop("at", _BASE), host=host,
                               name=kw.pop("name", f"알람 {alarm_id}"), **kw)
    return r.add(STREAM_KEY, {b"data": json.dumps(payload, ensure_ascii=False).encode()})


class _Graph:
    """그래프 대역 — `release` 전까지 대기 · `block`은 끝내 안 끝남 · `boom`은 예외."""

    def __init__(self, *, block: frozenset[str] = frozenset(),
                 boom: frozenset[str] = frozenset()) -> None:
        self.entered: list[str] = []
        self.completed: list[str] = []
        self.cancelled: list[str] = []
        self._block, self._boom = block, boom
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        alarm_id = state["alarm_event"].alarm_id
        self.entered.append(alarm_id)
        try:
            await self._gate.wait()
            if alarm_id in self._boom:
                raise RuntimeError(f"주입 예외 {alarm_id}")
            if alarm_id in self._block:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.append(alarm_id)
            raise
        self.completed.append(alarm_id)
        return {}


async def _until(pred: Any, *, tries: int = 400) -> None:
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("조건이 끝내 참이 되지 않았다")


def _loop(worker: AlarmWorker, r: FakeStreamRedis, **kw: Any) -> asyncio.Task[None]:
    return asyncio.create_task(worker._consume_concurrent(
        r, STREAM_KEY, "g", {}, block_ms=0, **kw,  # type: ignore[arg-type]
    ))


def _delivered(r: FakeStreamRedis) -> set[bytes]:
    return {m for m, _ in r._streams.get(STREAM_KEY, [])[: r._cursor.get(STREAM_KEY, 0)]}


def _other_tasks() -> set[asyncio.Task[Any]]:
    return {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}


# ═════════════════════════════════════════════════════════════════════════════
# W3 드레인
# ═════════════════════════════════════════════════════════════════════════════


class TestDrainEdges:
    async def test_cancel_while_capacity_full_drains_and_leaks_no_task(self, tmp_path: Path):
        """자리가 꽉 차 `asyncio.wait(FIRST_COMPLETED)`에서 기다리는 중 취소 → 드레인 · 누수 0."""
        worker, r = _worker(tmp_path)
        graph = _Graph()
        worker._graph = graph
        for i in range(3):  # N=2 · 3번째는 읽히지 않는다(자리 없음)
            _add(r, f"C-{i}", host=f"vc{i}")
        before = _other_tasks()
        task = _loop(worker, r, max_concurrency=2, drain_timeout=5.0)
        await _until(lambda: len(graph.entered) == 2)
        await asyncio.sleep(0.02)
        assert _delivered(r) == {b"1-0", b"2-0"}
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        graph.release()
        await asyncio.wait_for(task, 2.0)
        assert sorted(r.acked) == [b"1-0", b"2-0"]
        assert _delivered(r) == {b"1-0", b"2-0"}  # 드레인 중 3번째를 읽지 않았다
        assert _other_tasks() - before == set()  # 태스크 누수 0

    async def test_zero_drain_cancels_waiting_before_p3_without_deadlock(self, tmp_path: Path):
        """드레인 0 — 같은 호스트 2번째 건(P3 전 대기)도 취소 → 둘 다 pending · 교착 없이 끝난다."""
        worker, r = _worker(tmp_path)
        graph = _Graph()
        worker._graph = graph
        for i in range(2):
            _add(r, f"Z-{i}", host="vzz", resource=f"r{i}")
        before = _other_tasks()
        task = _loop(worker, r, max_concurrency=2, drain_timeout=0.0)
        await _until(lambda: graph.entered == ["Z-0"])
        await asyncio.sleep(0.02)
        assert graph.entered == ["Z-0"]  # 2번째는 P3 전에서 대기
        task.cancel()
        await asyncio.wait_for(task, 1.0)
        assert r.acked == []
        assert r.pending_ids(STREAM_KEY) == [b"1-0", b"2-0"]
        assert _other_tasks() - before == set()

    async def test_failure_during_drain_acks_that_one_and_reraises(self, tmp_path: Path):
        """드레인 중 진행 건이 그래프 예외 → 예외 건은 dead-letter + ACK(현행) · 유실 0."""
        worker, r = _worker(tmp_path)
        graph = _Graph(boom=frozenset({"E-0"}))
        worker._graph = graph
        for i in range(2):
            _add(r, f"E-{i}", host=f"ve{i}")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=5.0)
        await _until(lambda: len(graph.entered) == 2)
        task.cancel()
        await asyncio.sleep(0.01)
        graph.release()
        await asyncio.wait_for(task, 2.0)
        acked, pending = set(r.acked), set(r.pending_ids(STREAM_KEY))
        assert acked == {b"1-0", b"2-0"} and pending == set()
        assert len(r.dead_letters) == 1 and "E-0" in r.dead_letters[0]["error"]
        assert len(r.acked) == len(acked)  # 이중 ACK 없음


# ═════════════════════════════════════════════════════════════════════════════
# W3 회수
# ═════════════════════════════════════════════════════════════════════════════


class _TrimmedRedis(_YieldingRedis):
    """pending 읽기에서 지정 id의 필드를 빈 dict로 돌려준다 — 스트림 트림으로 지워진 항목(redis-py
    `pairs_to_dict(None) == {}`)."""

    def __init__(self, trimmed: set[bytes]) -> None:
        super().__init__()
        self._trimmed = trimmed

    def _read_pending(self, streams: dict[str, Any], count: int | None) -> list[list[Any]]:
        out = super()._read_pending(streams, count)
        return [[k, [(m, {} if m in self._trimmed else f) for m, f in batch]] for k, batch in out]


def _mark_delivered(r: FakeStreamRedis, count: int) -> None:
    r._cursor[STREAM_KEY] = r._cursor.get(STREAM_KEY, 0) + count


class TestReclaimEdges:
    async def test_trimmed_pending_goes_to_dead_letter_and_is_acked(self, tmp_path: Path):
        worker, _ = _worker(tmp_path)
        r = _TrimmedRedis({b"2-0"})
        worker._redis = r  # type: ignore[assignment]
        graph = _Graph()
        graph.release()
        worker._graph = graph
        for i in range(3):
            _add(r, f"T-{i}", host=f"vt{i}")
        _mark_delivered(r, 3)
        total = await asyncio.wait_for(
            worker._reclaim_pending(r, STREAM_KEY, "g", {}), 2.0  # type: ignore[arg-type]
        )
        assert total == 3
        assert graph.completed == ["T-0", "T-2"]
        assert sorted(r.acked) == [b"1-0", b"2-0", b"3-0"]
        assert len(r.dead_letters) == 1 and r.dead_letters[0]["source_msg_id"] == "2-0"
        assert "KeyError" in r.dead_letters[0]["error"]
        assert r.pending_ids(STREAM_KEY) == []

    async def test_reclaimed_messages_not_redelivered_by_new_read(self, tmp_path: Path):
        worker, r = _worker(tmp_path)
        graph = _Graph()
        graph.release()
        worker._graph = graph
        for i in range(3):
            _add(r, f"N-{i}", host=f"vn{i}")
        _mark_delivered(r, 2)
        await worker._reclaim_pending(r, STREAM_KEY, "g", {})  # type: ignore[arg-type]
        got = await read_messages(r, STREAM_KEY, "g", CONSUMER, count=10, block_ms=0)  # type: ignore[arg-type]
        assert [m for m, _ in got] == [b"3-0"]
        assert graph.completed == ["N-0", "N-1"]

    @pytest.mark.parametrize("n", [1, 4])
    async def test_cancel_during_reclaim_acks_inflight_regardless_of_n(
        self, tmp_path: Path, n: int
    ):
        """회수는 slot 없는 직렬 경로라 취소돼도 처리 중 1건을 ACK한다 — N≥2(C-5 「취소된 건은
        ACK 안 함」)에서도 같다(현행 고정 · 보고 지적: 회수 중 취소 1건 유실 가능)."""
        worker, r = _worker(tmp_path)
        worker._max_concurrency = n
        graph = _Graph()
        worker._graph = graph
        for i in range(2):
            _add(r, f"K-{i}", host=f"vk{i}")
        _mark_delivered(r, 2)
        task = asyncio.create_task(
            worker._reclaim_pending(r, STREAM_KEY, "g", {})  # type: ignore[arg-type]
        )
        await _until(lambda: graph.entered == ["K-0"])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1.0)
        assert graph.cancelled == ["K-0"]
        assert r.acked == [b"1-0"]  # 끝나지 않은 K-0이 ACK됨
        assert r.pending_ids(STREAM_KEY) == [b"2-0"]


# ═════════════════════════════════════════════════════════════════════════════
# W4
# ═════════════════════════════════════════════════════════════════════════════

TEXT_A = build_event_text("CPU high", "server.Cpus", "95")
TEXT_B = build_event_text("CPU elevated", "server.Cpus", "94")


def _ev(alarm_name: str, clog: str = "95") -> SimpleNamespace:
    return SimpleNamespace(db_id="db1", alarm_name=alarm_name, resource_type="server.Cpus",
                           condition_log=clog, is_clear=False)


def _sem_worker(provider: Any) -> AlarmWorker:
    w = AlarmWorker(SimpleNamespace(noise_gate=SimpleNamespace(
        embedding_similarity_threshold=0.87, repeat_interval_seconds=14400,
    )))
    w._embedding_provider = provider
    return w


class _RaisingProvider:
    def is_available(self) -> bool:
        return True

    def most_similar(self, q: str, cands: list[str]) -> Any:
        raise RuntimeError("provider boom")

    def similarity(self, a: str, b: str) -> Any:
        raise RuntimeError("provider boom")


class _OkProvider:
    def is_available(self) -> bool:
        return True

    def most_similar(self, q: str, cands: list[str]) -> tuple[int, float] | None:
        return (0, 0.9) if cands else None

    def similarity(self, a: str, b: str) -> float:
        return 0.5


class TestW4Exceptions:
    async def test_async_raises_same_exception_and_keeps_state(self):
        sync_w, async_w = _sem_worker(_OkProvider()), _sem_worker(_OkProvider())
        for w in (sync_w, async_w):  # 후보 1건을 쌓은 뒤 provider를 예외형으로 바꾼다
            w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)
            w._embedding_provider = _RaisingProvider()
        snap = {k: list(v) for k, v in async_w._recent_event_texts.items()}
        with pytest.raises(RuntimeError, match="provider boom"):
            sync_w._annotate_semantic_dedup(_ev("CPU elevated", "94"), "fp_b", 1001.0)
        with pytest.raises(RuntimeError, match="provider boom"):
            await async_w._annotate_semantic_dedup_async(_ev("CPU elevated", "94"), "fp_b", 1001.0)
        # 동기·async 모두 사후 단계(추가·sweep)에 닿지 않는다 — 상태 동일·무변경
        assert {k: list(v) for k, v in async_w._recent_event_texts.items()} == snap
        assert {k: list(v) for k, v in sync_w._recent_event_texts.items()} == snap

    async def test_enricher_async_propagates_provider_exception(self):
        ctx = {"root_resource": "r1", "root_resource_name": "db-host"}
        noise_cfg = SimpleNamespace(topology_text_fusion_enabled=True)
        with pytest.raises(RuntimeError, match="provider boom"):
            await _annotate_root_text_similarity_async(
                _ev("CPU high"), ctx, noise_cfg, _RaisingProvider()  # type: ignore[arg-type]
            )
        assert "root_text_similarity" not in ctx

    async def test_process_provider_exception_dead_letters_and_acks(self, tmp_path: Path):
        """`_process` P3에서 provider 예외 → 동기 판(W4 이전)과 같이 dead-letter + ACK."""
        worker, r = _worker(tmp_path)
        worker._embedding_provider = _OkProvider()
        graph = _Graph()
        graph.release()
        worker._graph = graph
        _add(r, "X-0", host="vx0", name="CPU high")
        _add(r, "X-1", host="vx1", name="CPU elevated", at=_BASE.replace(second=5))
        msgs = await read_messages(r, STREAM_KEY, "g", CONSUMER, count=10, block_ms=0)  # type: ignore[arg-type]
        dedup: dict[str, float] = {}
        await worker._process(r, STREAM_KEY, "g", msgs[0][0], msgs[0][1], dedup)  # type: ignore[arg-type]
        worker._embedding_provider = _RaisingProvider()
        await worker._process(r, STREAM_KEY, "g", msgs[1][0], msgs[1][1], dedup)  # type: ignore[arg-type]
        assert r.acked == [b"1-0", b"2-0"]
        assert len(r.dead_letters) == 1
        assert r.dead_letters[0]["error"] == "RuntimeError: provider boom"
        assert graph.completed == ["X-0"]


class _BlockingProvider:
    """`most_similar`이 이벤트를 기다린다 — 스레드 계산 도중 상태를 본다."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.go = threading.Event()
        self.received: list[list[str]] = []

    def is_available(self) -> bool:
        return True

    def most_similar(self, q: str, cands: list[str]) -> tuple[int, float] | None:
        self.received.append(cands)
        self.started.set()
        self.go.wait(5.0)
        return (0, 0.95) if cands else None

    def similarity(self, a: str, b: str) -> float:
        return 0.5


class TestW4Snapshot:
    async def test_result_index_maps_to_snapshot_even_if_scope_grows(self):
        prov = _BlockingProvider()
        prov.go.set()
        w = _sem_worker(_OkProvider())
        w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)
        prov.go.clear()
        w._embedding_provider = prov
        task = asyncio.create_task(
            w._annotate_semantic_dedup_async(_ev("CPU elevated", "94"), "fp_b", 1001.0)
        )
        await asyncio.to_thread(prov.started.wait, 2.0)
        # 계산 도중 같은 스코프 앞쪽에 항목이 끼어도(P3 직렬이면 일어나지 않지만) 인덱스는 스냅샷
        (scope,) = list(w._recent_event_texts)
        w._recent_event_texts[scope].appendleft(("fp_intruder", "intruder", 1000.5))
        prov.go.set()
        ann = await asyncio.wait_for(task, 2.0)
        assert ann is not None and ann["semantic_near_dup"]["matched_fp"] == "fp_a"
        assert prov.received == [[TEXT_A]]

    async def test_n2_zero_drain_cancel_mid_thread_leaves_pending_and_state(
        self, tmp_path: Path
    ):
        worker, r = _worker(tmp_path)
        worker._embedding_provider = _OkProvider()
        graph = _Graph()
        graph.release()
        worker._graph = graph
        _add(r, "Y-0", host="vy0", name="CPU high")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=0.0)
        await _until(lambda: graph.completed == ["Y-0"])
        await _until(lambda: r.acked == [b"1-0"])
        prov = _BlockingProvider()
        worker._embedding_provider = prov
        snap = {k: list(v) for k, v in worker._recent_event_texts.items()}
        _add(r, "Y-1", host="vy1", name="CPU elevated", at=_BASE.replace(second=5))
        await asyncio.to_thread(prov.started.wait, 2.0)
        task.cancel()
        try:
            await asyncio.wait_for(task, 1.0)
        finally:
            prov.go.set()  # 스레드를 풀어 기본 실행기를 비운다
        assert r.acked == [b"1-0"] and r.pending_ids(STREAM_KEY) == [b"2-0"]
        assert {k: list(v) for k, v in worker._recent_event_texts.items()} == snap
        assert graph.entered == ["Y-0"]


class _OverlapEmbedder:
    """가짜 SentenceTransformer — `encode` 동시 진입 수를 잰다."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.both_in = threading.Event()

    def encode(self, texts: list[str], **_k: Any) -> Any:
        import numpy as np

        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active >= 2:
                self.both_in.set()
        try:
            self.both_in.wait(0.5)
            return np.ones((len(texts), 4), dtype=np.float32)
        finally:
            with self._guard:
                self.active -= 1


class TestW4ProviderLockScope:
    async def test_encode_serialized_between_p3_and_p4_threads(self):
        """W4 뒤 P3 `most_similar`와 다른 알람의 P4 `similarity`가 서로 다른 스레드에서 돌아도 모델
        `encode`는 한 번에 하나만 돈다(W4 교정 — encode 전용 Lock ·
        검증 당시에는 캐시 Lock만 있어 겹쳤다).

        W4 이전에는 둘 다 이벤트 루프 스레드라 겹치지 않았다. 실 SentenceTransformer·토크나이저의
        동시 호출 안전성은 이 테스트가 보지 않는다(실 모델 미사용) — 보고서 지적 참조.
        """
        prov = AlarmEmbeddingProvider("")
        emb = _OverlapEmbedder()
        prov._embedder = emb  # 로드된 상태로 둔다(실 모델 미사용)
        w = _sem_worker(_OkProvider())
        w._annotate_semantic_dedup(_ev("CPU high"), "fp_a", 1000.0)  # 스코프 db1에 후보 1건
        w._embedding_provider = prov
        ctx = {"root_resource": "r1", "root_resource_name": "db-host"}
        noise_cfg = SimpleNamespace(topology_text_fusion_enabled=True)
        ann, _ = await asyncio.gather(
            w._annotate_semantic_dedup_async(_ev("CPU elevated", "94"), "fp_b", 1001.0),
            _annotate_root_text_similarity_async(_ev("Disk full"), ctx, noise_cfg, prov),
        )
        assert ann is not None  # 단위벡터 동일 → 유사도 1.0
        assert ctx["root_text_similarity"] == 1.0
        assert emb.max_active == 1  # encode 전용 Lock — 두 스레드의 encode가 직렬화됐다


class _SlowDeadLetterRedis(_YieldingRedis):
    """dead-letter `xadd`가 풀릴 때까지 멈춘다."""

    def __init__(self) -> None:
        super().__init__()
        self.in_xadd = asyncio.Event()
        self.release_xadd = asyncio.Event()

    async def xadd(self, key: str, fields: dict[str, Any], **kw: Any) -> bytes:
        self.in_xadd.set()
        await self.release_xadd.wait()
        return await super().xadd(key, fields, **kw)


class TestCancelDuringDeadLetter:
    async def test_n2_cancel_while_dead_lettering_stays_pending(self, tmp_path: Path):
        """N≥2 — 예외 처리(dead-letter 적재) 도중 취소돼도 ACK하지 않는다(L-1 교정 · C-5 「취소된
        건은 ACK 안 함」). dead-letter는 못 남았지만 pending으로 남아 회수 대상이다."""
        worker, _ = _worker(tmp_path)
        r = _SlowDeadLetterRedis()
        worker._redis = r  # type: ignore[assignment]
        graph = _Graph(boom=frozenset({"DL-0"}))
        graph.release()
        worker._graph = graph
        _add(r, "DL-0", host="vdl0")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=0.0)
        await asyncio.wait_for(r.in_xadd.wait(), 1.0)
        task.cancel()
        await asyncio.wait_for(task, 1.0)
        assert r.dead_letters == []
        assert r.acked == [] and r.pending_ids(STREAM_KEY) == [b"1-0"]
