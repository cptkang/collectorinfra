"""plans/207 트랙 B W2 독립 검증(verifier) — 동시 처리 실행기의 맹점 점검.

- 하네스 민감도: P3 잠금 조기 반납 · 충돌 키 제거 · 배리어 제거 변이를 테스트 안에서 주입하면
  동등성 단언이 실제로 깨지는가(지연 주입이 순서 위반을 드러낼 수 있는 형태인가).
- 순번: `enter` 대기 중 취소 → `finish`가 순번을 넘긴다 · 태스크 예외가 루프를 끝낼 때
  남은 태스크 정리.
- 충돌 키: P2(역조회) 뒤 이벤트로 계산 · 사건 키가 사건 추적기의 실제 키와 같다.
- 결함 기록(xfail strict): 배리어 판정 시각이 대기 전 시각이다 · 취소 시 P3 전 대기 건까지 ACK.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.worker_concurrency import SerialStageGate, conflict_keys
from noise_gate.domain.alarm import AlarmEvent, ServerIdentity
from noise_gate.domain.cross_source import episode_key
from noise_gate.domain.page_review import PageRegistry
from noise_gate.infrastructure.cross_source_rules import episode_alarm
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.equivalence_harness import (
    STREAM_KEY,
    FakeStreamRedis,
    HarnessAlarm,
    assert_equivalent,
    concurrent_executor,
    harness_config,
    polestar_payload,
    run_sequence,
    serial_executor,
    synthetic_alarms,
    write_enforce_rules,
)
from noise_gate.tests.test_plan207_w2_concurrency import with_setup, with_yields

_BASE = datetime(2026, 11, 2, 9, 0, 0)


def _event(**kw: Any) -> AlarmEvent:
    base: dict[str, Any] = dict(
        db_id="polestar_cm_gp", server_name="vfk01", hostname="vfk01", ip_address="",
        resource_ancestry="", alarm_id="V-1", severity=2, alarm_status="", resource_type="",
        resource_name="", alarm_name="CPU 사용률", alarm_time=_BASE, conditions="",
        condition_log="", is_clear=False, raw_payload={},
    )
    base.update(kw)
    return AlarmEvent(**base)


def _bare_worker(tmp_path: Path) -> tuple[AlarmWorker, FakeStreamRedis]:
    worker = AlarmWorker(harness_config(write_enforce_rules(tmp_path)))
    r = FakeStreamRedis()
    worker._redis = r  # type: ignore[assignment]
    worker._decision_store = DecisionStore(str(tmp_path / "d.jsonl"))
    worker._episodes = worker._build_episode_tracker()
    worker._page_registry = worker._build_page_registry()
    return worker, r


def _add(r: FakeStreamRedis, payload: dict[str, Any]) -> None:
    r.add(STREAM_KEY, {b"data": json.dumps(payload, ensure_ascii=False).encode()})


# ═════════════════════════════════════════════════════════════════════════════
# 하네스 민감도 — 변이를 넣으면 동등성 단언이 깨져야 한다
# ═════════════════════════════════════════════════════════════════════════════


async def _detects(tmp_path: Path, alarms: list[HarnessAlarm], executor_for, seeds, **kw) -> int:
    base = await run_sequence(
        alarms, work_dir=tmp_path, executor=with_yields(serial_executor, 0), **kw
    )
    caught = 0
    for seed in seeds:
        got = await run_sequence(
            alarms, work_dir=tmp_path, executor=executor_for(seed), seed=seed, max_delay=0.02,
            **kw,
        )
        try:
            assert_equivalent(base, got)
        except AssertionError:
            caught += 1
    return caught


class TestHarnessSensitivity:
    async def test_early_release_of_p3_is_caught(self, tmp_path: Path, monkeypatch):
        """P3 잠금을 진입 직후 놓는 변이(순번만 지키고 P3 안 await는 안 감쌈).

        지연 주입이 있으면 잡힌다(검증 실측: 주입 有 8/8 · 無 0/8 시드).
        """
        original = AlarmWorker._enter_serial_stage

        async def early(self: AlarmWorker, slot: Any, event: AlarmEvent) -> None:
            await original(self, slot, event)
            slot.leave_serial()

        monkeypatch.setattr(AlarmWorker, "_enter_serial_stage", early)
        caught = await _detects(
            tmp_path, synthetic_alarms(),
            lambda seed: with_yields(concurrent_executor(4), seed), (3, 4, 5, 6),
        )
        assert caught >= 1

    async def test_dropping_conflict_keys_is_caught(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(worker_mod, "conflict_keys", lambda *a, **k: frozenset())
        caught = await _detects(
            tmp_path, synthetic_alarms(),
            lambda seed: with_yields(concurrent_executor(4), seed), (3, 4),
        )
        assert caught >= 1

    async def test_dropping_registry_barrier_is_caught(self, tmp_path: Path):
        """배리어를 끄면 키 상한 시나리오(W2 테스트와 같은 입력)가 직렬과 달라진다."""
        alarms = [
            HarnessAlarm(payload=polestar_payload(
                f"CAP-{i}", _BASE + timedelta(seconds=i), host=f"vfcap{i:02d}",
                name=f"디스크 {i}", severity=3, resource="d",
            ))
            for i in range(10)
        ] + [
            HarnessAlarm(payload=polestar_payload(
                f"CAP-R{i}", _BASE + timedelta(seconds=20 + j), host=f"vfcap{i:02d}",
                name=f"디스크 {i}", severity=3, resource="d",
            ))
            for j, i in enumerate((3, 4, 5))
        ]

        def cap(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
            assert worker._page_registry is not None
            worker._page_registry._max_keys = 4

        def cap_no_barrier(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
            cap(worker, r, sk)
            worker._page_registry.sweep_due = lambda *a, **k: False  # type: ignore[union-attr,method-assign]

        overrides = {"repeat_interval_seconds": 10, "sev3_repeat_interval_seconds": 10}
        base = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(serial_executor, cap), **overrides
        )
        caught = 0
        for seed in (61, 62, 63):
            got = await run_sequence(
                alarms, work_dir=tmp_path,
                executor=with_setup(concurrent_executor(4), cap_no_barrier),
                seed=seed, max_delay=0.02, **overrides,
            )
            try:
                assert_equivalent(base, got)
            except AssertionError:
                caught += 1
        assert caught >= 1


# ═════════════════════════════════════════════════════════════════════════════
# 순번 — 취소·예외에서 다음 건이 막히지 않는다 · 태스크 정리
# ═════════════════════════════════════════════════════════════════════════════


class TestGateLiveness:
    async def test_cancel_while_waiting_in_enter_passes_turn(self):
        gate = SerialStageGate()
        s0, s1, s2 = gate.issue(), gate.issue(), gate.issue()
        await s0.enter(frozenset({"a"}), 0.0)  # 0번이 P3를 쥐고 있다

        async def process(slot: Any, keys: frozenset[Any]) -> None:
            try:
                await slot.enter(keys, 0.0)
                slot.leave_serial()
            finally:
                slot.finish()

        t1 = asyncio.create_task(process(s1, frozenset({"b"})))
        t2 = asyncio.create_task(process(s2, frozenset({"c"})))
        await asyncio.sleep(0.01)
        t1.cancel()  # 1번이 순번 대기 중 취소
        await asyncio.gather(t1, return_exceptions=True)
        s0.finish()
        await asyncio.wait_for(t2, 1.0)  # 2번이 막히지 않는다
        assert gate.held_count == 0

    async def test_barrier_wakes_when_last_held_finishes(self):
        """배리어가 늘 참이어도 진행 중이 비면 들어간다(무한 대기 없음)."""
        gate = SerialStageGate()
        s0, s1, s2 = gate.issue(), gate.issue(), gate.issue()
        for s in (s0, s1):
            await s.enter(frozenset({f"k{s.ticket}"}), 0.0)
            s.leave_serial()
        t = asyncio.create_task(s2.enter(frozenset({"z"}), 0.0, lambda *_: True))
        await asyncio.sleep(0.01)
        s0.finish()
        await asyncio.sleep(0.01)
        assert not t.done()
        s1.finish()
        await asyncio.wait_for(t, 1.0)

    async def test_task_exception_ends_loop_and_leaves_no_task(self, tmp_path: Path, monkeypatch):
        """`_process` 밖으로 올라온 예외(ACK 실패) — 루프가 그 예외로 끝나고
        남은 처리 태스크가 없다."""
        worker, r = _bare_worker(tmp_path)
        gate_open = asyncio.Event()

        class _Graph:
            async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                await gate_open.wait()
                return {}

        worker._graph = _Graph()
        for i in range(6):
            _add(r, polestar_payload(
                f"TE-{i}", _BASE + timedelta(seconds=i), host=f"vfte{i}", name=f"알람 {i}"
            ))
        real_ack = worker_mod.ack_message

        async def ack(r_: Any, sk: str, group: str, msg_id: bytes) -> None:
            if msg_id == b"1-0":
                raise ConnectionError("주입 — ACK 실패")
            await real_ack(r_, sk, group, msg_id)

        monkeypatch.setattr(worker_mod, "ack_message", ack)
        before = {t for t in asyncio.all_tasks()}
        loop_task = asyncio.create_task(worker._consume_concurrent(
            r, STREAM_KEY, "g", {}, max_concurrency=3, block_ms=0,  # type: ignore[arg-type]
            stop_when_drained=True,
        ))
        await asyncio.sleep(0.02)
        gate_open.set()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(loop_task, 2.0)
        leftover = {t for t in asyncio.all_tasks() if not t.done()} - before
        leftover.discard(asyncio.current_task())  # type: ignore[arg-type]
        assert leftover == set()


# ═════════════════════════════════════════════════════════════════════════════
# 충돌 키 — P2 뒤 이벤트 · 사건 키 실측 일치
# ═════════════════════════════════════════════════════════════════════════════


class TestConflictKeySource:
    async def test_keys_use_event_after_reverse_lookup(self, tmp_path: Path, monkeypatch):
        worker, r = _bare_worker(tmp_path)
        seen: list[str] = []

        async def attach(event: AlarmEvent, *a: Any, **k: Any) -> None:
            event.server_name = f"REG-{event.hostname}"  # 역조회가 등록명으로 승격

        real_keys = worker_mod.conflict_keys

        def spy(event: AlarmEvent, **k: Any) -> frozenset[Any]:
            seen.append(event.server_name)
            return real_keys(event, **k)

        class _Graph:
            async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                return {}

        worker._graph = _Graph()
        monkeypatch.setattr(worker_mod, "attach_server_identity", attach)
        monkeypatch.setattr(worker_mod, "conflict_keys", spy)
        _add(r, polestar_payload("CK-1", _BASE, host="vfck01", name="CPU"))
        await worker._consume_concurrent(
            r, STREAM_KEY, "g", {}, max_concurrency=2, block_ms=0,  # type: ignore[arg-type]
            stop_when_drained=True,
        )
        assert seen == ["REG-vfck01"]

    @pytest.mark.parametrize("zone", ["gongjon", ""])
    def test_episode_key_matches_tracker(self, zone: str):
        identity = ServerIdentity(name="REG", hostname="Web01.Corp", zone=zone)
        ev = _event(hostname="Web01.Corp", server_name="REG")
        ev.server_identity = identity
        keys = conflict_keys(ev, fanout=False, episodes=True)
        alarm = episode_alarm(ev, fingerprint="fp", is_apm=False)
        assert ("episode", episode_key(alarm.zone, alarm.host_key)) in keys


# ═════════════════════════════════════════════════════════════════════════════
# 결함 기록 (xfail strict — 고치면 통과로 바뀌어 표시가 빠져야 한다)
# ═════════════════════════════════════════════════════════════════════════════


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


class TestDefects:
    async def test_barrier_judged_at_entry_time_not_pre_wait_time(
        self, tmp_path: Path, monkeypatch
    ):
        worker, _ = _bare_worker(tmp_path)
        worker._episodes = None
        registry = PageRegistry()
        registry._swept_at = 0.0
        worker._page_registry = registry
        clock = _Clock(20.0)
        monkeypatch.setattr(worker_mod, "time", clock)

        gate = SerialStageGate()
        s_held, s_p3, s_a = gate.issue(), gate.issue(), gate.issue()
        await s_held.enter(frozenset({"H"}), 10.0)  # 다른 호스트 — P4·P5 진행 중
        s_held.leave_serial()
        await s_p3.enter(frozenset({"P"}), 15.0)  # 앞 건이 P3를 쥐고 있다
        task = asyncio.create_task(
            worker._enter_serial_stage(s_a, _event(hostname="vfa", server_name="vfa"))
        )
        await asyncio.sleep(0)  # A가 t=20에 판정 시각을 잡고 순번을 기다린다
        clock.now = 70.0  # 대기가 정리 기한(0+60)을 넘는다
        s_p3.finish()
        await asyncio.sleep(0.01)
        try:
            assert registry.sweep_due(clock.now, pending=gate.held_count)
            # A의 P3 now(=70)는 정리 기한 — 진행 중(H)의 P5를 기다려야 직렬과 같다.
            assert not task.done(), "정리 기한인데 진행 중 P5를 기다리지 않고 P3에 들어갔다"
        finally:
            s_held.finish()
            await asyncio.wait_for(task, 1.0)

    async def test_cancel_does_not_ack_message_waiting_before_p3(self, tmp_path: Path):
        worker, r = _bare_worker(tmp_path)
        entered: list[str] = []

        class _Graph:
            async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
                entered.append(state["alarm_event"].alarm_id)
                await asyncio.Event().wait()
                return {}

        worker._graph = _Graph()
        for i in range(2):  # 같은 호스트 — 2번째는 1번째 P5를 기다린다(P3 전)
            _add(r, polestar_payload(
                f"CW-{i}", _BASE + timedelta(seconds=i), host="vfcw", name=f"알람 {i}",
                resource=f"r{i}",
            ))
        task = asyncio.create_task(worker._consume_concurrent(
            r, STREAM_KEY, "g", {}, max_concurrency=2, block_ms=0,  # type: ignore[arg-type]
        ))
        for _ in range(200):
            if entered:
                break
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.02)
        assert entered == ["CW-0"]
        task.cancel()
        await asyncio.wait_for(task, 2.0)
        # (plans/207 W3 · C-5) 드레인 없이(직접 호출 기본 0초) 취소된 진행 중 건도 ACK하지 않는다 —
        # 직렬(처리 중 1건 ACK)과 다르게 pending으로 남겨 기동 시 회수 대상으로 둔다(§4 기준 5).
        assert b"1-0" not in r.acked
        assert b"2-0" not in r.acked  # 처리하지 않은 건은 남아야 재처리된다
        assert r.pending_ids(STREAM_KEY) == [b"1-0", b"2-0"]
