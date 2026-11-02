"""plans/207 트랙 B W3 · C-5 — 종료 드레인(N≥2) · 기동 시 미ACK(pending) 회수(옵트인).

- 설정 2종 기본값·환경 변수 이름 · 워커 해석(음수 드레인 → 0).
- 드레인: 드레인 안에 끝난 건 ACK · 못 끝낸 건 pending · 수신 = ACK ∪ pending(유실 0) · 드레인 중
  P3 전 대기 건도 이어서 처리 · 다시 취소되면 바로 정리하고 취소를 올린다 · 드레인 경로의 ACK
  다중집합 = 직렬(N=1) ACK 다중집합(조기 반환 이중 ACK 포함 — 신규 경로가 이중 ACK를 더하지 않음).
- N=1 불변: slot 없는 `_process`는 취소돼도 `finally`에서 ACK(현행) · `run()`은 플래그 off면
  회수 호출 0.
- 회수: pending을 수신 순서대로 먼저 처리한 뒤 새 메시지 · `run()` N=1·N≥2 둘 다 · 기존
  `read_messages` 불변.
- Q5 실측: 재기동 뒤 회수한 같은 알람은 지문 중복으로 걸러지지 않는다(재발 레코드 0). PAGE
  재판정 `off`(기본)·`shadow`면 다시 PAGE, `enforce`면 판정 기록 복원(R1)이 TICKET으로 내린다 —
  같은 프로세스 재배달은 지문 중복으로 걸러진다(대조). 현행 동작 고정.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from src.config import AlarmConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.redis_queue import read_messages, read_pending_messages
from noise_gate.tests.equivalence_harness import (
    CONSUMER,
    STREAM_KEY,
    FakeAlarmGraph,
    FakeStreamRedis,
    HarnessAlarm,
    assert_equivalent,
    harness_config,
    polestar_payload,
    run_sequence,
    serial_executor,
    synthetic_alarms,
    write_enforce_rules,
)
from noise_gate.tests.test_plan207_w2_concurrency import run_loop_executor, with_yields

_BASE = datetime(2026, 11, 2, 9, 0, 0)


class _BlockingReadRedis(FakeStreamRedis):
    """빈 읽기면 잠깐 양보한다(`block_ms` 흉내) — 하네스 가짜는 양보하지 않아 자리가 남은 소비
    루프가 빈 읽기를 돌며 이벤트 루프를 독점한다."""

    async def xreadgroup(self, *a: Any, **k: Any) -> list[list[Any]]:
        out = await super().xreadgroup(*a, **k)
        if not out:
            await asyncio.sleep(0.002)
        return out


def _worker(tmp_path: Path, **alarm: Any) -> tuple[AlarmWorker, FakeStreamRedis]:
    cfg = harness_config(write_enforce_rules(tmp_path))
    if alarm:
        cfg.alarm = AlarmConfig.model_construct(min_severity=1, **alarm)
    worker = AlarmWorker(cfg)
    r = _BlockingReadRedis()
    worker._redis = r  # type: ignore[assignment]
    worker._decision_store = DecisionStore(str(tmp_path / "d.jsonl"))
    worker._episodes = worker._build_episode_tracker()
    worker._page_registry = worker._build_page_registry()
    return worker, r


def _add(r: FakeStreamRedis, alarm_id: str, *, host: str, at: datetime = _BASE, **kw: Any) -> None:
    name = kw.pop("name", f"알람 {alarm_id}")
    payload = polestar_payload(alarm_id, at, host=host, name=name, **kw)
    r.add(STREAM_KEY, {b"data": json.dumps(payload, ensure_ascii=False).encode()})


class _GatedGraph:
    """그래프 대역 — 풀릴 때까지 기다린다(`release`). `block`에 든 알람은 끝내 풀리지 않는다."""

    def __init__(self, *, block: frozenset[str] = frozenset()) -> None:
        self.entered: list[str] = []
        self.completed: list[str] = []
        self.cancelled: list[str] = []
        self._block = block
        self._gate = asyncio.Event()

    def release(self) -> None:
        self._gate.set()

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        alarm_id = state["alarm_event"].alarm_id
        self.entered.append(alarm_id)
        try:
            await self._gate.wait()
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


# ═════════════════════════════════════════════════════════════════════════════
# 설정
# ═════════════════════════════════════════════════════════════════════════════


class TestConfig:
    def test_defaults(self):
        cfg = AlarmConfig.model_construct()
        assert cfg.worker_drain_timeout_seconds == 30.0
        assert cfg.worker_reclaim_pending_on_start is False

    def test_env_names(self, monkeypatch):
        monkeypatch.setenv("ALARM_WORKER_DRAIN_TIMEOUT_SECONDS", "7.5")
        monkeypatch.setenv("ALARM_WORKER_RECLAIM_PENDING_ON_START", "true")
        cfg = AlarmConfig(_env_file=None)  # type: ignore[call-arg]
        assert cfg.worker_drain_timeout_seconds == 7.5
        assert cfg.worker_reclaim_pending_on_start is True

    def test_worker_resolves_once(self, tmp_path: Path):
        worker, _ = _worker(tmp_path)
        assert worker._drain_timeout == 30.0 and worker._reclaim_pending_on_start is False
        worker, _ = _worker(
            tmp_path, worker_drain_timeout_seconds=-3, worker_reclaim_pending_on_start=True
        )
        assert worker._drain_timeout == 0.0 and worker._reclaim_pending_on_start is True


# ═════════════════════════════════════════════════════════════════════════════
# 종료 드레인 (N≥2 · §4 기준 5)
# ═════════════════════════════════════════════════════════════════════════════


class TestDrain:
    async def test_inflight_finished_within_drain_are_acked(self, tmp_path: Path):
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        worker._graph = graph
        for i in range(2):
            _add(r, f"D-{i}", host=f"vd{i}")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=5.0)
        await _until(lambda: len(graph.entered) == 2)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()  # 취소를 받고도 진행 중을 기다린다(새로 읽지 않는다)
        graph.release()
        await asyncio.wait_for(task, 2.0)  # 드레인 뒤 정상 종료
        assert sorted(r.acked) == [b"1-0", b"2-0"]
        assert r.pending_ids(STREAM_KEY) == [] and graph.cancelled == []

    async def test_waiting_before_p3_proceeds_during_drain(self, tmp_path: Path):
        """같은 호스트 2번째 건은 P3 전에서 1번째의 P5를 기다린다 — 드레인 중 이어서 끝난다."""
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        worker._graph = graph
        for i in range(2):
            _add(r, f"W-{i}", host="vdw", at=_BASE + timedelta(seconds=i), resource=f"r{i}")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=5.0)
        await _until(lambda: graph.entered == ["W-0"])
        await asyncio.sleep(0.02)
        task.cancel()
        await asyncio.sleep(0.01)
        graph.release()
        await asyncio.wait_for(task, 2.0)
        assert graph.completed == ["W-0", "W-1"]
        assert sorted(set(r.acked)) == [b"1-0", b"2-0"] and r.pending_ids(STREAM_KEY) == []

    async def test_unfinished_after_drain_stay_pending_no_loss(self, tmp_path: Path, caplog):
        worker, r = _worker(tmp_path)
        graph = _GatedGraph(block=frozenset({"U-1"}))
        worker._graph = graph
        for i in range(3):  # U-2는 U-0이 자리를 비운 뒤 읽힌다
            _add(r, f"U-{i}", host=f"vu{i}")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=0.2)
        await _until(lambda: len(graph.entered) == 2)
        graph.release()  # U-0·U-2는 끝나고 U-1은 끝내 안 끝난다
        await _until(lambda: graph.completed == ["U-0", "U-2"])
        await asyncio.sleep(0.01)
        _add(r, "U-3", host="vu3")  # 취소와 같은 틈에 도착 — 드레인 중에는 읽지 않는다
        task.cancel()
        with caplog.at_level("INFO", logger=worker_mod.__name__):
            await asyncio.wait_for(task, 2.0)
        assert graph.cancelled == ["U-1"]
        delivered = {msg_id for msg_id, _ in r._streams[STREAM_KEY][: r._cursor[STREAM_KEY]]}
        assert delivered == {b"1-0", b"2-0", b"3-0"}  # 취소 뒤 새로 읽지 않았다(U-3 미배달)
        acked, pending = set(r.acked), set(r.pending_ids(STREAM_KEY))
        assert acked & pending == set()
        assert acked == {b"1-0", b"3-0"} and pending == {b"2-0"}
        # 유실 0 — 배달된 건은 ACK 아니면 pending이다.
        assert (acked | pending) == delivered
        assert len(r.acked) == len(acked)  # 이 경로는 이중 ACK를 만들지 않는다
        assert r.dead_letters == []
        assert any("미완료" in rec.getMessage() for rec in caplog.records)

    async def test_zero_drain_cancels_immediately(self, tmp_path: Path):
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        worker._graph = graph
        _add(r, "Z-0", host="vz0")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=0.0)
        await _until(lambda: graph.entered == ["Z-0"])
        task.cancel()
        await asyncio.wait_for(task, 1.0)
        assert r.acked == [] and r.pending_ids(STREAM_KEY) == [b"1-0"]

    async def test_second_cancel_during_drain_cleans_up_and_reraises(self, tmp_path: Path):
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        worker._graph = graph
        for i in range(2):
            _add(r, f"S-{i}", host=f"vs{i}")
        task = _loop(worker, r, max_concurrency=2, drain_timeout=30.0)
        await _until(lambda: len(graph.entered) == 2)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()  # 드레인 중 다시 취소 — 30초를 다 기다리지 않는다
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1.0)
        assert sorted(graph.cancelled) == ["S-0", "S-1"]
        assert r.acked == [] and r.pending_ids(STREAM_KEY) == [b"1-0", b"2-0"]

    @pytest.mark.parametrize("seed", [3, 11])
    async def test_drained_ack_multiset_equals_serial(self, tmp_path: Path, seed: int):
        """빈 읽기에서 취소가 오고 드레인으로 마저 끝낸 결과 = 직렬 결과(판정·ACK 다중집합 모두).

        합성 순서에는 조기 반환(지문 중복 등) 경로가 있어 직렬부터 이중 ACK가 있다 — 드레인 경로가
        그 다중집합을 그대로 재현하고 더하지 않는지 본다.
        """
        alarms = synthetic_alarms()
        base = await run_sequence(
            alarms, work_dir=tmp_path / "n1", executor=with_yields(serial_executor, 0)
        )

        async def drain_executor(worker: AlarmWorker, r: FakeStreamRedis, sk: str, g: str) -> None:
            await worker._consume_concurrent(
                r, sk, g, {}, max_concurrency=4, block_ms=0,  # type: ignore[arg-type]
                consumer=CONSUMER, drain_timeout=10.0,
            )

        got = await run_sequence(
            alarms, work_dir=tmp_path / "n4", executor=with_yields(drain_executor, seed),
            seed=seed, max_delay=0.01, cancel_when_drained=True,
        )
        assert_equivalent(base, got)
        assert got.acked == base.acked
        assert len(got.acked) > len(set(got.acked))  # 현행 조기 반환 이중 ACK가 그대로(더하지 않음)


# ═════════════════════════════════════════════════════════════════════════════
# N=1 불변
# ═════════════════════════════════════════════════════════════════════════════


class TestSerialUnchanged:
    async def test_process_without_slot_still_acks_on_cancel(self, tmp_path: Path):
        """N=1(slot 없음) — 처리 중 취소된 1건은 현행대로 `finally`에서 ACK한다."""
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        worker._graph = graph
        _add(r, "N1-0", host="vn1")
        [(msg_id, fields)] = await read_messages(r, STREAM_KEY, "g", CONSUMER, count=1, block_ms=0)  # type: ignore[arg-type]
        task = asyncio.create_task(
            worker._process(r, STREAM_KEY, "g", msg_id, fields, {})  # type: ignore[arg-type]
        )
        await _until(lambda: graph.entered == ["N1-0"])
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert r.acked == [b"1-0"]

    async def test_process_without_slot_acks_when_cancelled_while_dead_lettering(
        self, tmp_path: Path, monkeypatch
    ):
        """N=1(slot 없음) — dead-letter 적재 중 취소돼도 현행대로 ACK한다(L-1 교정은 N≥2만)."""
        worker, r = _worker(tmp_path)
        in_dead_letter = asyncio.Event()

        class _Boom:
            async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                raise RuntimeError("주입 — 그래프 실패")

        async def stuck_dead_letter(*a: Any, **k: Any) -> None:
            in_dead_letter.set()
            await asyncio.Event().wait()

        worker._graph = _Boom()
        monkeypatch.setattr(worker_mod, "dead_letter_message", stuck_dead_letter)
        _add(r, "N1-DL", host="vn1dl")
        [(msg_id, fields)] = await read_messages(r, STREAM_KEY, "g", CONSUMER, count=1, block_ms=0)  # type: ignore[arg-type]
        task = asyncio.create_task(
            worker._process(r, STREAM_KEY, "g", msg_id, fields, {})  # type: ignore[arg-type]
        )
        await asyncio.wait_for(in_dead_letter.wait(), 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert r.acked == [b"1-0"] and r.dead_letters == []

    async def test_reclaim_off_makes_no_pending_read(self, tmp_path: Path, monkeypatch):
        calls: list[Any] = []

        async def spy(*a: Any, **k: Any) -> list[Any]:
            calls.append(a)
            return []

        monkeypatch.setattr(worker_mod, "read_pending_messages", spy)
        alarms = [HarnessAlarm(polestar_payload("OFF-0", _BASE, host="voff", name="CPU"))]
        res = await run_sequence(alarms, work_dir=tmp_path, executor=run_loop_executor(1))
        assert calls == [] and res.acked == ["1-0"]


# ═════════════════════════════════════════════════════════════════════════════
# 기동 시 미ACK 회수 (옵트인 · Q5)
# ═════════════════════════════════════════════════════════════════════════════


def _deliver(r: FakeStreamRedis, count: int) -> None:
    """직전 실행이 읽고(배달) ACK하지 못한 상태를 만든다."""
    msgs = r._streams[STREAM_KEY]
    r._cursor[STREAM_KEY] = r._cursor.get(STREAM_KEY, 0) + count
    assert r._cursor[STREAM_KEY] <= len(msgs)


class TestReadPending:
    async def test_reads_only_pending_after_id(self):
        r = FakeStreamRedis()
        for i in range(4):
            r.add(STREAM_KEY, {b"data": b"{}"})
        _deliver(r, 3)
        await r.xack(STREAM_KEY, "g", b"2-0")
        got = await read_pending_messages(r, STREAM_KEY, "g", CONSUMER)  # type: ignore[arg-type]
        assert [m for m, _ in got] == [b"1-0", b"3-0"]
        got = await read_pending_messages(r, STREAM_KEY, "g", CONSUMER, after=b"1-0")  # type: ignore[arg-type]
        assert [m for m, _ in got] == [b"3-0"]
        assert await read_pending_messages(r, STREAM_KEY, "g", CONSUMER, after=b"3-0") == []  # type: ignore[arg-type]
        # 새 메시지(">") 읽기는 그대로 — 미배달 4번째만
        got = await read_messages(r, STREAM_KEY, "g", CONSUMER, block_ms=0)  # type: ignore[arg-type]
        assert [m for m, _ in got] == [b"4-0"]

    async def test_xreadgroup_call_shape(self):
        seen: list[Any] = []

        class _R:
            async def xreadgroup(self, *a: Any, **k: Any) -> list[Any]:
                seen.append((a, k))
                return [[STREAM_KEY.encode(), []]]

        assert await read_pending_messages(_R(), STREAM_KEY, "g", "c", count=5) == []  # type: ignore[arg-type]
        assert seen == [(("g", "c", {STREAM_KEY: "0"}), {"count": 5})]  # 블로킹 인자 없음


class TestReclaim:
    async def test_reclaim_processes_pending_in_order_then_stops(self, tmp_path: Path):
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        graph.release()
        worker._graph = graph
        for i in range(12):  # 10건 묶음을 넘겨 이어 읽기까지 본다
            _add(r, f"R-{i}", host=f"vr{i}")
        _deliver(r, 12)
        total = await worker._reclaim_pending(r, STREAM_KEY, "g", {})  # type: ignore[arg-type]
        assert total == 12
        assert graph.completed == [f"R-{i}" for i in range(12)]
        assert r.pending_ids(STREAM_KEY) == []

    async def test_reclaim_ends_even_if_ack_keeps_failing(self, tmp_path: Path, monkeypatch):
        worker, r = _worker(tmp_path)
        graph = _GatedGraph()
        graph.release()
        worker._graph = graph
        for i in range(3):
            _add(r, f"F-{i}", host=f"vf{i}")
        _deliver(r, 3)

        async def no_ack(*a: Any, **k: Any) -> None:
            return None  # ACK가 반영되지 않는다 — 같은 id를 다시 읽으면 무한 반복

        monkeypatch.setattr(worker_mod, "ack_message", no_ack)
        total = await asyncio.wait_for(
            worker._reclaim_pending(r, STREAM_KEY, "g", {}), 2.0  # type: ignore[arg-type]
        )
        assert total == 3 and graph.completed == ["F-0", "F-1", "F-2"]

    @pytest.mark.parametrize("n", [1, 4])
    async def test_run_reclaims_before_new_messages(self, tmp_path: Path, n: int):
        """`run()` — 회수가 켜져 있으면 pending(1·2번)을 먼저 처리한 뒤 새 메시지(3·4번)를 읽는다.
        """
        order: list[str] = []
        alarms = [
            HarnessAlarm(polestar_payload(f"RR-{i}", _BASE + timedelta(seconds=i), host=f"vrr{i}",
                                          name="CPU"))
            for i in range(4)
        ]
        inner = run_loop_executor(n)

        async def executor(worker: AlarmWorker, r: FakeStreamRedis, sk: str, g: str) -> None:
            worker._reclaim_pending_on_start = True
            _deliver(r, 2)
            graph = worker._graph
            original = graph.ainvoke  # type: ignore[union-attr]

            async def ainvoke(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                order.append(state["alarm_event"].alarm_id)
                return await original(state, config)

            graph.ainvoke = ainvoke  # type: ignore[union-attr]
            await inner(worker, r, sk, g)

        res = await run_sequence(alarms, work_dir=tmp_path, executor=executor)
        assert order[:2] == ["RR-0", "RR-1"] and sorted(order) == [f"RR-{i}" for i in range(4)]
        assert sorted(set(res.acked)) == ["1-0", "2-0", "3-0", "4-0"]


class TestQ5DuplicateNotification:
    """Q5 실측 — 회수한 알람이 다시 통보되는가(현행 동작 고정 · 막는 장치는 범위 밖)."""

    @staticmethod
    def _setup(
        work: Path, store: Path, alarm: HarnessAlarm, page_review_mode: str = "enforce"
    ) -> tuple[AlarmWorker, FakeAlarmGraph]:
        work.mkdir(parents=True, exist_ok=True)
        worker = AlarmWorker(
            harness_config(write_enforce_rules(work), page_review_mode=page_review_mode)
        )
        graph = FakeAlarmGraph({alarm.alarm_id: alarm})
        worker._graph = graph
        worker._decision_store = DecisionStore(str(store))
        worker._episodes = worker._build_episode_tracker()
        worker._page_registry = worker._build_page_registry()
        return worker, graph

    @staticmethod
    def _decisions(store: Path, alarm_id: str) -> list[dict[str, Any]]:
        rows = [json.loads(line) for line in store.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row.get("alarm_id") == alarm_id]

    @pytest.mark.parametrize("severity", [2, 3])
    @pytest.mark.parametrize(("mode", "second_tier"), [
        ("off", "page"),  # 코드 기본 — 다시 PAGE(중복 통보)
        ("shadow", "page"),  # 기록만 — 다시 PAGE
        ("enforce", "ticket"),  # 판정 기록으로 복원한 PAGE 재판정(R1)이 TICKET으로 내린다
    ])
    async def test_reclaimed_alarm_after_restart(
        self, tmp_path: Path, severity: int, mode: str, second_tier: str
    ):
        """재기동 뒤 회수한 알람은 지문 중복으로 걸러지지 않는다(인메모리 상태가 비어 재발 0).
        다시 PAGE가 나가지 않는 것은 PAGE 재판정 `enforce`(기동 시 판정 기록에서 복원)뿐이다."""
        store = tmp_path / "decisions.jsonl"
        alarm = HarnessAlarm(polestar_payload(
            "Q5-1", datetime.now().replace(microsecond=0), host="vq5", name="CPU 사용률",
            severity=severity,
        ))
        r = _BlockingReadRedis()
        r.add(STREAM_KEY, {b"data": json.dumps(alarm.payload, ensure_ascii=False).encode()})

        # 1차 실행 — 그래프가 통보 판정(게이트 노드)까지 마친 뒤 뒷단에서 멈춘 채 종료(드레인 초과).
        first, graph1 = self._setup(tmp_path / "w1", store, alarm, mode)
        first._redis = r  # type: ignore[assignment]
        original = graph1.ainvoke

        async def stuck_after_gate(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
            await original(state, config)
            await asyncio.Event().wait()
            return {}

        graph1.ainvoke = stuck_after_gate  # type: ignore[method-assign]
        task = _loop(first, r, max_concurrency=2, drain_timeout=0.05, consumer=CONSUMER)
        await _until(lambda: graph1.inputs)
        task.cancel()
        await asyncio.wait_for(task, 2.0)
        assert r.pending_ids(STREAM_KEY) == [b"1-0"]
        assert [d["tier"] for d in self._decisions(store, "Q5-1")] == ["page"]

        # 재기동 — 새 워커(인메모리 중복 억제 상태 없음) · 같은 판정 저장소 · PAGE 레지스트리 복원.
        second, graph2 = self._setup(tmp_path / "w2", store, alarm, mode)
        second._redis = r  # type: ignore[assignment]
        second._restore_page_registry(datetime.now().timestamp())
        total = await second._reclaim_pending(r, STREAM_KEY, "g", {}, consumer=CONSUMER)  # type: ignore[arg-type]
        assert total == 1 and graph2.completed == ["Q5-1"]
        assert r.pending_ids(STREAM_KEY) == []
        # 현행: 지문 중복으로 걸러지지 않고 다시 판정된다 — off·shadow면 PAGE 재통보
        # (Q5 기본 off 근거).
        tiers = [d.get("tier") for d in self._decisions(store, "Q5-1") if "tier" in d]
        assert tiers == ["page", second_tier]
        assert not any(d.get("type") == "recurrence" for d in self._decisions(store, "Q5-1"))

    async def test_same_process_redelivery_is_fingerprint_deduped(self, tmp_path: Path):
        """대조 — 재기동 없이 같은 워커가 같은 알람을 다시 받으면 지문 중복으로 걸러진다."""
        store = tmp_path / "decisions.jsonl"
        alarm = HarnessAlarm(polestar_payload(
            "Q5-2", datetime.now().replace(microsecond=0), host="vq52", name="CPU 사용률",
        ))
        worker, graph = self._setup(tmp_path / "w", store, alarm)
        r = FakeStreamRedis()
        worker._redis = r  # type: ignore[assignment]
        for _ in range(2):
            r.add(STREAM_KEY, {b"data": json.dumps(alarm.payload, ensure_ascii=False).encode()})
        await serial_executor(worker, r, STREAM_KEY, "g")  # type: ignore[arg-type]
        rows = self._decisions(store, "Q5-2")
        assert [d.get("tier") for d in rows if "tier" in d] == ["page"]
        assert any(d.get("type") == "recurrence" for d in rows)
