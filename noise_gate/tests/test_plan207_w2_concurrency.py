"""plans/207 트랙 B W2 — 알람 워커 동시 처리 실행기(C-2·C-3·C-4).

- 동등성: 픽스처·합성 입력을 N=1(직렬)과 N=2·4로 처리한 판정 감사 레코드 집합 · 그래프 입력 ·
  그래프 뒤 상태 · ACK 다중집합이 같다(서버명 역조회·P3 안 await에 무작위 지연을 넣어 양보를
  만든다).
  완료 순서가 실제로 바뀐다(겹침 증거).
- N=1 비트 동일: `run()`의 N=1 분기(기존 직렬 루프)가 하네스 직렬 실행기와 같다 — 정상 · 예외 주입
  (파싱 실패 · P3 예외 · 그래프 예외 → dead-letter).
- 백프레셔(진행 중 ≤ N) · 같은 호스트 연속은 수신 순서 그대로 · PAGE 레지스트리 정리 배리어 ·
  사건 상한 경계 · 범위 밖 N 클램프 · 취소 시 정리.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.application.worker_concurrency import (
    MAX_CONCURRENCY,
    SerialStageGate,
    conflict_keys,
    resolve_max_concurrency,
)
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import NotificationDecision
from noise_gate.domain.page_review import PageRegistry, fanout_key_of, host_key_of
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.equivalence_harness import (
    STREAM_KEY,
    Executor,
    FakeStreamRedis,
    HarnessAlarm,
    HarnessResult,
    assert_equivalent,
    concurrent_executor,
    fixture_alarms,
    harness_config,
    jennifer_payload,
    polestar_payload,
    run_sequence,
    serial_executor,
    synthetic_alarms,
    write_enforce_rules,
)

_BASE = datetime(2026, 11, 2, 9, 0, 0)
_REAL_ATTACH = worker_mod.attach_server_identity

# ═════════════════════════════════════════════════════════════════════════════
# 실행기 조립 도구
# ═════════════════════════════════════════════════════════════════════════════


def _jitter(tag: str, alarm_id: str, max_delay: float) -> float:
    return random.Random(f"{tag}:{alarm_id}").uniform(0.0, max_delay)


def with_yields(inner: Executor, seed: int, max_delay: float = 0.01) -> Executor:
    """서버명 역조회(P2)와 해소 발행(P3 안 await)에 알람별 무작위 지연을 넣는다.

    하네스의 가짜 Redis는 양보하지 않으므로, 이 지연이 없으면 P1~P3 순번·잠금이 시험되지 않는다.
    지연은 결과를 바꾸지 않는다(직렬 경로에서는 순서가 그대로다).
    """

    async def executor(worker: AlarmWorker, r: FakeStreamRedis, sk: str, group: str) -> None:
        async def attach(event: AlarmEvent, *a: Any, **k: Any) -> None:
            await asyncio.sleep(_jitter(f"p2-{seed}", event.alarm_id, max_delay))
            await _REAL_ATTACH(event, *a, **k)

        original_publish = worker._publish_incident_resolved

        async def publish(event: AlarmEvent, fingerprint: str, self_heal: bool) -> None:
            await asyncio.sleep(_jitter(f"p3-{seed}", event.alarm_id, max_delay))
            await original_publish(event, fingerprint, self_heal)

        worker._publish_incident_resolved = publish  # type: ignore[method-assign]
        worker_mod.attach_server_identity = attach  # type: ignore[assignment]
        try:
            await inner(worker, r, sk, group)
        finally:
            worker_mod.attach_server_identity = _REAL_ATTACH

    return executor


def with_setup(
    inner: Executor, setup: Callable[[AlarmWorker, FakeStreamRedis, str], None]
) -> Executor:
    """실행 전 워커·스트림을 바꾸는 준비 단계를 얹는다(상한 축소 · 예외 주입 등)."""

    async def executor(worker: AlarmWorker, r: FakeStreamRedis, sk: str, group: str) -> None:
        setup(worker, r, sk)
        await inner(worker, r, sk, group)

    return executor


class _RunLoopRedis:
    """`run()` 시험용 — 하네스 스트림에 위임하되, 빈 읽기면 전부 ACK될 때까지 짧게
    기다리고(블록 흉내) 전부 ACK되면 취소(`run()` 종료 신호)를 올린다."""

    def __init__(self, inner: FakeStreamRedis, stream_key: str) -> None:
        self._inner = inner
        self._stream_key = stream_key

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def xreadgroup(self, *a: Any, **k: Any) -> list[list[Any]]:
        out = await self._inner.xreadgroup(*a, **k)
        if out:
            return out
        total = {m for m, _ in self._inner._streams.get(self._stream_key, [])}
        if total <= set(self._inner.acked):
            raise asyncio.CancelledError
        await asyncio.sleep(0.002)
        return []

    async def aclose(self) -> None:
        return None


def run_loop_executor(max_concurrency: int) -> Executor:
    """`AlarmWorker.run()`을 그대로 돈다 — 협력자 생성(`_build_*`)·Redis·그래프만 하네스 것으로."""

    async def executor(worker: AlarmWorker, r: FakeStreamRedis, sk: str, group: str) -> None:
        cfg = worker._config
        cfg.alarm = AlarmConfig.model_construct(
            min_severity=1, enabled=True, redis_stream_key=sk, redis_consumer_group=group,
            worker_max_concurrency=max_concurrency,
        )
        cfg.redis = SimpleNamespace(host="127.0.0.1", port=0, password="", db=0)
        worker._max_concurrency = resolve_max_concurrency(max_concurrency)
        keep = {
            "_build_decision_store": worker._decision_store,
            "_build_episode_tracker": worker._episodes,
            "_build_page_registry": worker._page_registry,
        }
        for name in dir(worker):
            if name.startswith("_build_") and callable(getattr(worker, name)):
                value = keep.get(name)
                setattr(worker, name, lambda value=value: value)
        graph = worker._graph
        proxy = _RunLoopRedis(r, sk)
        saved = (worker_mod.aioredis.from_url, worker_mod.build_alarm_graph)
        worker_mod.aioredis.from_url = lambda *a, **k: proxy  # type: ignore[assignment]
        worker_mod.build_alarm_graph = lambda *_a, **_k: graph  # type: ignore[assignment]
        try:
            await worker.run()
        finally:
            worker_mod.aioredis.from_url, worker_mod.build_alarm_graph = saved  # type: ignore[assignment]

    return executor


def _inject_faults(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
    """예외 3종 — 파싱 실패(P1) · 해소 발행 예외(P3 순번을 쥔 채) · 그래프 예외(P4)."""
    r._streams[sk].insert(3, (b"9001-0", {b"data": b"{not json"}))
    original_publish = worker._publish_incident_resolved

    async def publish(event: AlarmEvent, fingerprint: str, self_heal: bool) -> None:
        if event.alarm_id == "HEAL-0":
            raise RuntimeError("주입 — 해소 발행 실패")
        await original_publish(event, fingerprint, self_heal)

    worker._publish_incident_resolved = publish  # type: ignore[method-assign]
    graph = worker._graph
    original_invoke = graph.ainvoke  # type: ignore[union-attr]

    async def ainvoke(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        if state["alarm_event"].alarm_id == "MIX-3":
            await asyncio.sleep(0.005)
            raise RuntimeError("주입 — 그래프 실패")
        return await original_invoke(state, config)

    graph.ainvoke = ainvoke  # type: ignore[union-attr]


def _all_ids(res: HarnessResult) -> set[str]:
    return set(res.acked)


# ═════════════════════════════════════════════════════════════════════════════
# 동등성 (§4 기준 2) · 겹침 증거
# ═════════════════════════════════════════════════════════════════════════════


class TestEquivalence:
    @pytest.mark.parametrize(("n", "seeds"), [(2, (1, 2)), (4, (3, 4, 5, 6))])
    async def test_synthetic_n_equals_serial_and_overlaps(
        self, tmp_path: Path, n: int, seeds: tuple[int, ...]
    ):
        alarms = synthetic_alarms()
        base = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_yields(serial_executor, 0)
        )
        reordered = 0
        for seed in seeds:
            got = await run_sequence(
                alarms, work_dir=tmp_path, executor=with_yields(concurrent_executor(n), seed),
                seed=seed, max_delay=0.02,
            )
            assert_equivalent(base, got)
            # 판정 시각을 시계 함수로 읽어도 가상 시계가 이벤트 시각을 찾는다(대체값 증가 없음).
            assert got.clock_fallbacks == base.clock_fallbacks
            reordered += got.completion_order != base.completion_order
        # 완료 순서가 실제로 뒤집혔다 — 서로 다른 호스트가 겹쳐 처리됐다는 증거.
        assert reordered >= 1

    @pytest.mark.parametrize("page_review_mode", ["enforce", "off"])
    async def test_fixture_n4_equals_serial(self, tmp_path: Path, page_review_mode: str):
        alarms = fixture_alarms()
        base = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_yields(serial_executor, 0),
            page_review_mode=page_review_mode,
        )
        reordered = 0
        for seed in (11, 12, 13):
            got = await run_sequence(
                alarms, work_dir=tmp_path, executor=with_yields(concurrent_executor(4), seed),
                seed=seed, max_delay=0.02, page_review_mode=page_review_mode,
            )
            assert_equivalent(base, got)
            reordered += got.completion_order != base.completion_order
        if page_review_mode == "off":
            # 동시발생 키가 없으면 서로 다른 호스트의 같은 알람명(xspay01·xspay02)이 겹친다.
            assert reordered >= 1

    async def test_ack_multiset_same_and_nothing_lost(self, tmp_path: Path):
        alarms = synthetic_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        got = await run_sequence(
            alarms, work_dir=tmp_path, executor=concurrent_executor(4), seed=8, max_delay=0.02
        )
        assert got.acked == base.acked  # 다중집합(정렬 목록) — 조기 반환 이중 ACK까지 같다
        assert _all_ids(got) == {f"{i}-0" for i in range(1, len(alarms) + 1)}
        assert len(got.acked) > len(_all_ids(got))  # 현행 조기 반환 이중 ACK가 그대로 남았다


# ═════════════════════════════════════════════════════════════════════════════
# N=1 비트 동일 · 예외 주입
# ═════════════════════════════════════════════════════════════════════════════


class TestRunLoop:
    async def test_run_n1_matches_serial_executor(self, tmp_path: Path):
        alarms = synthetic_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path, executor=serial_executor)
        got = await run_sequence(alarms, work_dir=tmp_path, executor=run_loop_executor(1))
        assert_equivalent(base, got)
        assert got.completion_order == base.completion_order  # 직렬 — 순서까지 같다

    async def test_run_n1_does_not_enter_concurrent_path(self, tmp_path: Path, monkeypatch):
        async def boom(*_a: Any, **_k: Any) -> None:
            raise AssertionError("N=1이 동시 처리 경로에 들어갔다")

        monkeypatch.setattr(AlarmWorker, "_consume_concurrent", boom)
        res = await run_sequence(fixture_alarms(), work_dir=tmp_path, executor=run_loop_executor(1))
        assert res.records

    @pytest.mark.parametrize("executor_name", ["run_n1", "run_n4", "concurrent_n4"])
    async def test_faults_dead_letter_and_ack_same_as_serial(
        self, tmp_path: Path, executor_name: str
    ):
        alarms = synthetic_alarms()
        base = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(serial_executor, _inject_faults)
        )
        assert len(base.dead_letters) == 3
        inner = {
            "run_n1": run_loop_executor(1),
            "run_n4": run_loop_executor(4),
            "concurrent_n4": with_yields(concurrent_executor(4), 21),
        }[executor_name]
        got = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(inner, _inject_faults),
            seed=21, max_delay=0.02,
        )
        assert_equivalent(base, got)
        assert sorted(d["source_msg_id"] for d in got.dead_letters) == sorted(
            d["source_msg_id"] for d in base.dead_letters
        )
        assert "9001-0" in _all_ids(got)  # 파싱 실패 건도 ACK(유실 0)


# ═════════════════════════════════════════════════════════════════════════════
# 백프레셔 · 같은 호스트 직렬 · 배리어 · 사건 상한
# ═════════════════════════════════════════════════════════════════════════════


def _count_inflight(worker: AlarmWorker, peaks: dict[str, int]) -> None:
    """`_process` 동시 실행 수와 그래프 동시 실행 수의 최대값을 잰다."""
    original_process = worker._process
    graph = worker._graph
    original_invoke = graph.ainvoke  # type: ignore[union-attr]
    live = {"process": 0, "graph": 0}

    async def process(*a: Any, **k: Any) -> None:
        live["process"] += 1
        peaks["process"] = max(peaks.get("process", 0), live["process"])
        try:
            await original_process(*a, **k)
        finally:
            live["process"] -= 1

    async def ainvoke(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        live["graph"] += 1
        peaks["graph"] = max(peaks.get("graph", 0), live["graph"])
        try:
            return await original_invoke(state, config)
        finally:
            live["graph"] -= 1

    worker._process = process  # type: ignore[method-assign]
    graph.ainvoke = ainvoke  # type: ignore[union-attr]


def _same_host_alarms(count: int) -> list[HarnessAlarm]:
    return [
        HarnessAlarm(payload=polestar_payload(
            f"SAME-{i}", _BASE + timedelta(seconds=i), host="eqsame01",
            name=f"알람 {i}", resource=f"r{i}",
        ))
        for i in range(count)
    ]


class TestOrdering:
    @pytest.mark.parametrize("n", [2, 4])
    async def test_inflight_never_exceeds_n(self, tmp_path: Path, n: int):
        peaks: dict[str, int] = {}
        res = await run_sequence(
            synthetic_alarms(), work_dir=tmp_path,
            executor=with_setup(
                with_yields(concurrent_executor(n), 31), lambda w, r, sk: _count_inflight(w, peaks)
            ),
            seed=31, max_delay=0.02,
        )
        assert res.records
        assert peaks["process"] <= n
        assert peaks["graph"] >= 2  # 실제로 겹쳤다

    async def test_same_host_keeps_receive_order(self, tmp_path: Path):
        alarms = _same_host_alarms(10)
        peaks: dict[str, int] = {}
        res = await run_sequence(
            alarms, work_dir=tmp_path,
            executor=with_setup(
                with_yields(concurrent_executor(4), 41), lambda w, r, sk: _count_inflight(w, peaks)
            ),
            seed=41, max_delay=0.03,
        )
        assert res.completion_order == [a.alarm_id for a in alarms]
        assert peaks["graph"] == 1
        assert peaks["process"] > 1  # 파싱·역조회는 앞서 들어와 기다렸다

    async def test_forced_sweep_barrier_serializes_graph(self, tmp_path: Path):
        """정리 기한이면 진행 중 전부를 기다린다 — 강제로 늘 참이면 그래프가 한 건씩만 돈다."""
        peaks: dict[str, int] = {}
        calls: list[int] = []

        def setup(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
            _count_inflight(worker, peaks)
            registry = worker._page_registry
            assert registry is not None

            def due(now: float, pending: int = 0) -> bool:
                calls.append(pending)
                return True

            registry.sweep_due = due  # type: ignore[method-assign]

        alarms = synthetic_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        got = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(concurrent_executor(4), setup),
            seed=51, max_delay=0.02,
        )
        assert_equivalent(base, got)
        assert peaks["graph"] == 1
        assert calls and max(calls) >= 1  # 진행 중이 있을 때 물었다

    async def test_sweep_interval_barrier_engages_on_synthetic(self, tmp_path: Path):
        """합성 입력(약 7분)에서 60초 정리 기한 배리어가 진행 중이 있을 때 실제로 걸린다."""
        hits: list[tuple[int, bool]] = []

        def setup(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
            registry = worker._page_registry
            assert registry is not None
            original = registry.sweep_due

            def spy(now: float, pending: int = 0) -> bool:
                out = original(now, pending)
                hits.append((pending, out))
                return out

            registry.sweep_due = spy  # type: ignore[method-assign]

        alarms = synthetic_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        got = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(concurrent_executor(4), setup),
            seed=52, max_delay=0.02,
        )
        assert_equivalent(base, got)
        assert any(pending >= 1 and due for pending, due in hits)

    async def test_registry_cap_barrier_keeps_equivalence(self, tmp_path: Path):
        """키 상한 근처에서는 진행 중 반영이 상한 정리를 일으킬 수 있어 전부를 기다린다.

        상한 4 · 서로 다른 호스트 PAGE 10건 뒤 앞쪽 지문 재발 — 배리어가 없으면 재발 알람이 아직
        밀려나지 않은 대표 PAGE를 보고 강등된다(직렬과 다름).
        """
        alarms = [
            HarnessAlarm(payload=polestar_payload(
                f"CAP-{i}", _BASE + timedelta(seconds=i), host=f"eqcap{i:02d}",
                name=f"디스크 {i}", severity=3, resource="d",
            ))
            for i in range(10)
        ] + [
            HarnessAlarm(payload=polestar_payload(
                f"CAP-R{i}", _BASE + timedelta(seconds=20 + j), host=f"eqcap{i:02d}",
                name=f"디스크 {i}", severity=3, resource="d",
            ))
            for j, i in enumerate((3, 4, 5))
        ]

        def cap(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
            assert worker._page_registry is not None
            worker._page_registry._max_keys = 4

        overrides = {"repeat_interval_seconds": 10, "sev3_repeat_interval_seconds": 10}
        base = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(serial_executor, cap), **overrides
        )
        for seed in (61, 62, 63):
            got = await run_sequence(
                alarms, work_dir=tmp_path, executor=with_setup(concurrent_executor(4), cap),
                seed=seed, max_delay=0.02, **overrides,
            )
            assert_equivalent(base, got)

    async def test_episode_cap_boundary(self, tmp_path: Path):
        """사건 상한(§1.4 「수용」 행) 경계 — 상한 N+1에서 사건이 밀려나도 결과가 같다.

        밀려나는 사건은 마지막 관측이 가장 이른 것이고, 진행 중 알람의 사건은 가장 최근 N개라
        상한 > N이면 진행 중 사건이 밀려나지 않는다.
        """
        alarms = [
            HarnessAlarm(payload=polestar_payload(
                f"EC-C{i}", _BASE + timedelta(seconds=i), host=f"eqec{i:02d}",
                name="CPU 사용률", resource="CPU",
            ))
            for i in range(8)
        ] + [
            HarnessAlarm(payload=jennifer_payload(
                f"EC-E{i}", _BASE + timedelta(seconds=30 + i), host=f"eqec{i:02d}"
            ))
            for i in range(8)
        ]
        n = 4

        def cap(worker: AlarmWorker, r: FakeStreamRedis, sk: str) -> None:
            assert worker._episodes is not None
            worker._episodes.max_episodes = n + 1

        overrides = {"page_review_mode": "off", "cross_host_correlation_enabled": False}
        base = await run_sequence(
            alarms, work_dir=tmp_path, executor=with_setup(serial_executor, cap), **overrides
        )
        assert len(base.final_state["episodes"]) == n + 1  # 상한에 닿아 사건이 밀려났다
        for seed in (71, 72, 73):
            got = await run_sequence(
                alarms, work_dir=tmp_path, executor=with_setup(concurrent_executor(n), cap),
                seed=seed, max_delay=0.02, **overrides,
            )
            assert_equivalent(base, got)


# ═════════════════════════════════════════════════════════════════════════════
# 단위 — 충돌 키 · 순번 · 정리 기한 · 클램프 · 취소
# ═════════════════════════════════════════════════════════════════════════════


def _event(**kw: Any) -> AlarmEvent:
    base: dict[str, Any] = dict(
        db_id="polestar_cm_gp", server_name="eqk01", hostname="eqk01", ip_address="",
        resource_ancestry="", alarm_id="K-1", severity=2, alarm_status="", resource_type="",
        resource_name="", alarm_name="CPU 사용률", alarm_time=_BASE, conditions="",
        condition_log="", is_clear=False, raw_payload={},
    )
    base.update(kw)
    return AlarmEvent(**base)


class TestConflictKeys:
    def test_keys_follow_enabled_state(self):
        ev = _event()
        assert conflict_keys(ev, fanout=False, episodes=False) == {
            ("host", *host_key_of("polestar_cm_gp", "eqk01")),
            ("cache", "polestar_cm_gp", "eqk01", "CPU 사용률"),
        }
        full = conflict_keys(ev, fanout=True, episodes=True)
        assert ("fanout", *fanout_key_of("polestar_cm_gp", "CPU 사용률")) in full
        assert ("episode", "|eqk01") in full

    def test_no_episode_key_without_hostname(self):
        keys = conflict_keys(_event(hostname=""), fanout=False, episodes=True)
        assert not any(k[0] == "episode" for k in keys)  # type: ignore[index]

    def test_host_key_uses_hostname_when_server_name_empty(self):
        keys = conflict_keys(_event(server_name=""), fanout=False, episodes=False)
        assert ("host", *host_key_of("polestar_cm_gp", "eqk01")) in keys


class TestGate:
    async def test_turn_order_and_early_pass(self):
        gate = SerialStageGate()
        s0, s1, s2 = gate.issue(), gate.issue(), gate.issue()
        order: list[int] = []

        async def go(slot: Any, keys: frozenset[Any]) -> None:
            await slot.enter(keys, 0.0)
            order.append(slot.ticket)
            slot.leave_serial()

        t2 = asyncio.create_task(go(s2, frozenset({"b"})))
        await asyncio.sleep(0)
        s1.finish()  # 1번은 P3 전에 끝남(파싱 실패 등) — 순번만 넘긴다
        assert order == []  # 0번이 아직 안 들어왔다 — 2번은 기다린다
        await go(s0, frozenset({"a"}))
        await t2
        assert order == [0, 2]

    async def test_conflict_waits_until_finish(self):
        gate = SerialStageGate()
        s0, s1 = gate.issue(), gate.issue()
        await s0.enter(frozenset({"h"}), 0.0)
        s0.leave_serial()
        t1 = asyncio.create_task(s1.enter(frozenset({"h"}), 0.0))
        await asyncio.sleep(0.01)
        assert not t1.done()
        s0.finish()
        await asyncio.wait_for(t1, 1.0)
        assert gate.held_count == 1

    async def test_barrier_waits_for_all_held(self):
        gate = SerialStageGate()
        s0, s1 = gate.issue(), gate.issue()
        await s0.enter(frozenset({"a"}), 10.0)
        s0.leave_serial()
        seen: list[tuple[float, list[float]]] = []

        def barrier(now: float, held: Any) -> bool:
            seen.append((now, list(held)))
            return True

        t1 = asyncio.create_task(s1.enter(frozenset({"b"}), 20.0, barrier))
        await asyncio.sleep(0.01)
        assert not t1.done() and seen == [(20.0, [10.0])]
        s0.finish()
        await asyncio.wait_for(t1, 1.0)

    async def test_finish_is_idempotent(self):
        gate = SerialStageGate()
        s0, s1 = gate.issue(), gate.issue()
        await s0.enter(frozenset({"a"}), 0.0)
        s0.finish()
        s0.finish()
        await asyncio.wait_for(s1.enter(frozenset({"a"}), 0.0), 1.0)

    async def test_clock_is_read_at_each_judgment(self):
        """시계 함수를 주면 대기 뒤 판정·진입 시각이 현재 시각이다(대기 전 값에 고정되지 않는다)."""
        gate = SerialStageGate()
        s0, s1 = gate.issue(), gate.issue()
        clock = {"now": 10.0}
        await s0.enter(frozenset({"a"}), lambda: clock["now"])
        assert s0.entered_at == 10.0
        seen: list[float] = []

        def barrier(now: float, held: Any) -> bool:
            seen.append(now)
            return now >= 70.0

        # s0이 P3를 쥔 동안 s1은 순번을 기다린다(시각 20).
        clock["now"] = 20.0
        t1 = asyncio.create_task(s1.enter(frozenset({"b"}), lambda: clock["now"], barrier))
        await asyncio.sleep(0)
        assert not t1.done() and seen == []
        clock["now"] = 70.0  # 대기 중 기한을 넘긴다
        s0.leave_serial()  # 차례가 왔다 — 배리어가 대기 전 값(20)이 아니라 70으로 판정해 막는다
        await asyncio.sleep(0.01)
        assert not t1.done() and seen == [70.0]
        s0.finish()
        await asyncio.wait_for(t1, 1.0)
        assert s1.entered_at == 70.0


class TestCapBarrierLog:
    async def test_warns_once_on_engage_and_info_on_release(self, tmp_path: Path, caplog):
        worker = AlarmWorker(harness_config(write_enforce_rules(tmp_path)))
        worker._episodes = None
        registry = PageRegistry(max_keys=2)
        TestSweepDue()._observe(registry, 0, 100.0)
        registry._swept_at = 1e18  # 주기 정리 기한은 오지 않게 — 상한 판정만 본다
        worker._page_registry = registry
        gate = SerialStageGate()
        held = gate.issue()
        await held.enter(frozenset({"other"}), 100.0)
        held.leave_serial()
        with caplog.at_level(logging.INFO, logger="noise_gate.application.alarm_worker"):
            for i in range(3):  # 상한(지문 1 + 진행 중 1 ≥ 2) — 매번 배리어
                slot = gate.issue()
                task = asyncio.create_task(worker._enter_serial_stage(
                    slot, _event(alarm_id=f"K-{i}", alarm_time=_BASE + timedelta(seconds=i)),
                ))
                await asyncio.sleep(0.005)
                assert not task.done()
                held.finish()
                await asyncio.wait_for(task, 1.0)
                slot.leave_serial()
                held = slot  # 이 건이 다음 건의 진행 중이 된다
            registry._pages.clear()  # 상한에서 내려온다(지문·동시발생 키 모두)
            registry._fanout.clear()
            slot = gate.issue()
            await asyncio.wait_for(worker._enter_serial_stage(
                slot, _event(alarm_id="K-9", hostname="eqk09", server_name="eqk09", alarm_name="X"),
            ), 1.0)
        msgs = [r for r in caplog.records if "키 상한" in r.getMessage()]
        assert [r.levelno for r in msgs] == [logging.WARNING, logging.INFO]


class TestSweepDue:
    def _decision(self) -> NotificationDecision:
        return NotificationDecision(
            tier="page", reason="", priority=0, signals={}, stage="severity3"
        )

    def _observe(self, reg: PageRegistry, i: int, now: float) -> None:
        reg.observe(
            self._decision(), fingerprint=f"fp{i}", host_key=("db", f"h{i}"),
            fanout_key=("db", f"a{i}"), alarm_id=f"A{i}", now=now,
        )

    def test_interval(self):
        reg = PageRegistry()
        assert reg.sweep_due(0.0)  # 한 번도 정리하지 않았다
        self._observe(reg, 0, 100.0)  # 반영이 정리 시각을 100으로 둔다
        assert not reg.sweep_due(159.0)
        assert reg.sweep_due(160.0)

    def test_cap_with_pending(self):
        reg = PageRegistry(max_keys=3)
        self._observe(reg, 0, 100.0)
        self._observe(reg, 1, 101.0)
        assert not reg.sweep_due(102.0)
        assert reg.sweep_due(102.0, pending=1)
        self._observe(reg, 2, 102.0)
        assert reg.sweep_due(103.0)

    def test_query_does_not_change_state(self):
        reg = PageRegistry()
        self._observe(reg, 0, 100.0)
        before = (dict(reg._pages), reg._swept_at)
        reg.sweep_due(10_000.0, pending=5)
        assert (dict(reg._pages), reg._swept_at) == before


class TestConfig:
    def test_default_is_one(self):
        assert AlarmConfig.model_construct().worker_max_concurrency == 1

    def test_env_name(self, monkeypatch):
        monkeypatch.setenv("ALARM_WORKER_MAX_CONCURRENCY", "4")
        assert AlarmConfig(_env_file=None).worker_max_concurrency == 4  # type: ignore[call-arg]

    @pytest.mark.parametrize(("raw", "want", "warns"), [
        (1, 1, False), (4, 4, False), (16, 16, False),
        (0, 1, True), (-3, 1, True), (17, MAX_CONCURRENCY, True), (99, 16, True), ("x", 1, True),
    ])
    def test_clamp_with_single_warning(self, caplog, raw: Any, want: int, warns: bool):
        with caplog.at_level(logging.WARNING, logger="noise_gate.application.worker_concurrency"):
            assert resolve_max_concurrency(raw) == want
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == (1 if warns else 0)

    def test_worker_reads_once_at_init(self, tmp_path: Path):
        cfg = harness_config(write_enforce_rules(tmp_path))
        cfg.alarm = AlarmConfig.model_construct(min_severity=1, worker_max_concurrency=40)
        assert AlarmWorker(cfg)._max_concurrency == 16
        cfg.alarm = AlarmConfig.model_construct(min_severity=1)
        assert AlarmWorker(cfg)._max_concurrency == 1


class _BlockingGraph:
    """그래프가 풀리지 않는다 — 취소 시험용."""

    def __init__(self) -> None:
        self.entered: list[str] = []

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.entered.append(state["alarm_event"].alarm_id)
        await asyncio.Event().wait()
        return {}


class TestCancellation:
    async def test_cancel_cancels_inflight_and_leaves_them_pending(self, tmp_path: Path):
        """취소 시 루프가 끝나고 진행 중 태스크는 취소된다 — (W3 · C-5) 취소된 건은 ACK하지 않아
        pending으로 남는다(직접 호출 기본 드레인 0초 · 드레인은 `test_plan207_w3_drain`).
        읽지 않은 건은 미배달로 남는다."""
        worker = AlarmWorker(harness_config(write_enforce_rules(tmp_path)))
        r = FakeStreamRedis()
        graph = _BlockingGraph()
        worker._graph = graph
        worker._redis = r  # type: ignore[assignment]
        worker._decision_store = DecisionStore(str(tmp_path / "d.jsonl"))
        worker._episodes = worker._build_episode_tracker()
        worker._page_registry = worker._build_page_registry()
        for i in range(6):
            payload = polestar_payload(
                f"CX-{i}", _BASE + timedelta(seconds=i), host=f"eqcx{i}", name=f"알람 {i}"
            )
            r.add(STREAM_KEY, {b"data": json.dumps(payload, ensure_ascii=False).encode()})

        task = asyncio.create_task(worker._consume_concurrent(
            r, STREAM_KEY, "g", {}, max_concurrency=2, block_ms=0,  # type: ignore[arg-type]
        ))
        for _ in range(200):
            if len(graph.entered) == 2:
                break
            await asyncio.sleep(0.005)
        assert graph.entered == ["CX-0", "CX-1"]
        task.cancel()
        await asyncio.wait_for(task, 2.0)  # 루프가 취소를 받아 정상 종료(직렬 루프와 같다)
        assert r.acked == []  # 진행 중 2건은 pending · 나머지 4건은 미읽음
        assert r.pending_ids(STREAM_KEY) == [b"1-0", b"2-0"]
        assert r.dead_letters == []
