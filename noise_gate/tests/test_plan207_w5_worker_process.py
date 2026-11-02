"""plans/207 트랙 B W5 · C-7 — 워커 별도 프로세스 진입점 · 단일 인스턴스 잠금.

- 설정 2종 기본값·환경 변수 이름.
- 잠금(`WorkerSingletonLock`): 획득(NX·EX) · 자기 값일 때만 갱신 · 값 불일치 → 즉시 상실 ·
  Redis 실패 → 만료 기한 전까지 WARNING 뒤 재시도, 기한에 닿으면 상실 · 자기 값일 때만 해제.
- 워커 배선: 가드 off(기본)면 잠금 Redis 호출 0 · 획득 실패면 소비 0 + ERROR · 실행 중 상실이면
  새로 읽지 않고 멈춤(N=1·N≥2 — N≥2는 진행 중 드레인) · 갱신 일시 실패 뒤 회복이면 소비 계속 ·
  종료 사유(`lock_outcome`) · 종료 시 자기 값만 삭제 · 두 인스턴스 동시 기동이면 하나만 소비 ·
  종료 취소도 소비 본체에 넘긴다.
- 진입점(`noise_gate.worker`): 종료 요청 → 워커 취소 → 드레인 뒤 정상 종료 · 드레인 상한 초과면
  다시 취소 · 재취소 뒤에도 안 끝나면 두고 나옴 · 워커 예외는 그대로 · 종료 코드(정상 0 · 예외·
  정지 상한 초과 1 · 설정 로드 실패 2 · 잠금 상실 3 · 획득 실패 4) · `ALARM_ENABLED=false`면 즉시 0
  (실제 서브프로세스 — Redis 없이 끝나는 형태만).

실 Redis·실 LLM·과금 API 0 — 가짜·목만.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig

from noise_gate import worker as entry
from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.infrastructure import worker_lock as lock_mod
from noise_gate.infrastructure.worker_lock import (
    LOCK_KEY,
    LOCK_TTL_SECONDS,
    OUTCOME_LOST,
    OUTCOME_NOT_ACQUIRED,
    RENEW_INTERVAL_SECONDS,
    WorkerSingletonLock,
    default_owner,
)
from noise_gate.tests.equivalence_harness import harness_config, write_enforce_rules

_ROOT = Path(__file__).resolve().parents[2]


class FakeRedis:
    """잠금·워커 기동이 부르는 메서드만 흉내 낸다.

    `SET NX EX` · `GET` · 두 Lua 스크립트(비교 후 연장·삭제).
    """

    def __init__(self, kv: dict[str, bytes] | None = None) -> None:
        self.kv: dict[str, bytes] = kv if kv is not None else {}
        self.ttl: dict[str, int] = {}
        self.calls: list[str] = []
        self.fail_eval = False
        self.closed = False

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None,
    ) -> bool | None:
        self.calls.append("set")
        if nx and key in self.kv:
            return None
        self.kv[key] = value.encode()
        if ex is not None:
            self.ttl[key] = ex
        return True

    async def get(self, key: str) -> bytes | None:
        self.calls.append("get")
        return self.kv.get(key)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.calls.append("eval")
        if self.fail_eval:
            raise ConnectionError("주입 — Redis 끊김")
        assert numkeys == 1
        assert "redis.call('get', KEYS[1]) == ARGV[1]" in script  # 비교가 스크립트 안(원자)
        key, owner = args[0], args[1]
        if self.kv.get(key) != str(owner).encode():
            return 0
        if "expire" in script:
            self.ttl[key] = int(args[2])
            return 1
        del self.kv[key]
        return 1

    async def xgroup_create(self, *a: Any, **k: Any) -> bool:
        self.calls.append("xgroup_create")
        return True

    async def aclose(self) -> None:
        self.closed = True


def _lock_calls(r: FakeRedis) -> list[str]:
    return [c for c in r.calls if c in ("set", "get", "eval")]


class _Clock:
    """수동 단조 시계 — 갱신 경과를 테스트가 정한다."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _until(pred: Any, *, tries: int = 400) -> None:
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("조건이 끝내 참이 되지 않았다")


# ═════════════════════════════════════════════════════════════════════════════
# 설정
# ═════════════════════════════════════════════════════════════════════════════


class TestConfig:
    def test_defaults(self):
        cfg = AlarmConfig.model_construct()
        assert cfg.worker_in_process is True
        assert cfg.worker_singleton_guard is False

    def test_env_names(self, monkeypatch):
        monkeypatch.setenv("ALARM_WORKER_IN_PROCESS", "false")
        monkeypatch.setenv("ALARM_WORKER_SINGLETON_GUARD", "true")
        cfg = AlarmConfig(_env_file=None)  # type: ignore[call-arg]
        assert cfg.worker_in_process is False
        assert cfg.worker_singleton_guard is True

    def test_constants(self):
        assert LOCK_KEY == "alarm:worker:lock"
        assert LOCK_TTL_SECONDS == 30
        assert RENEW_INTERVAL_SECONDS == 10.0
        host, _, pid = default_owner().rpartition(":")
        assert host and pid == str(os.getpid())


# ═════════════════════════════════════════════════════════════════════════════
# 잠금 단위
# ═════════════════════════════════════════════════════════════════════════════


class TestLock:
    async def test_acquire_sets_nx_ex(self):
        r = FakeRedis()
        lock = WorkerSingletonLock(r, owner="h:1")
        assert await lock.acquire() is True and lock.held
        assert r.kv[LOCK_KEY] == b"h:1" and r.ttl[LOCK_KEY] == LOCK_TTL_SECONDS

    async def test_acquire_fails_when_held(self):
        r = FakeRedis({LOCK_KEY: b"other:9"})
        lock = WorkerSingletonLock(r, owner="h:1")
        assert await lock.acquire() is False and not lock.held
        assert await lock.holder() == "other:9"
        assert r.kv[LOCK_KEY] == b"other:9"

    async def test_watch_renews_only_own_value_then_reports_loss(self):
        r = FakeRedis()
        lock = WorkerSingletonLock(r, owner="h:1", renew_interval=0.01)
        await lock.acquire()
        r.ttl[LOCK_KEY] = 1  # 갱신이 다시 늘리는지 본다
        watch = asyncio.create_task(lock.watch())
        await _until(lambda: r.ttl[LOCK_KEY] == LOCK_TTL_SECONDS)
        assert not watch.done()
        r.kv[LOCK_KEY] = b"other:9"  # 만료 뒤 다른 인스턴스가 잡았다
        reason = await asyncio.wait_for(watch, 1.0)
        assert "값 불일치" in reason and not lock.held
        assert r.kv[LOCK_KEY] == b"other:9"  # 남의 값은 연장도 삭제도 하지 않는다

    async def test_watch_reports_loss_on_redis_failure(self):
        """갱신 실패가 만료 기한(경과 + 갱신 주기 ≥ TTL)까지 이어지면 잃은 것으로 본다."""
        r = FakeRedis()
        clock = _Clock()
        lock = WorkerSingletonLock(r, owner="h:1", renew_interval=0.01, ttl_seconds=10,
                                   clock=clock)
        await lock.acquire()
        r.fail_eval = True
        clock.now = 9.99  # 9.99 + 0.01 ≥ 10
        reason = await asyncio.wait_for(lock.watch(), 1.0)
        assert reason.startswith("갱신 실패 지속") and not lock.held

    async def test_watch_retries_transient_failure_until_deadline(self, caplog):
        """기한 전 실패는 WARNING 뒤 다시 시도 · 성공하면 기한을 다시 잰다 · 기한에 닿으면 상실."""
        r = FakeRedis()
        clock = _Clock()
        lock = WorkerSingletonLock(r, owner="h:1", renew_interval=0.01, ttl_seconds=10,
                                   clock=clock)
        await lock.acquire()
        r.fail_eval = True
        with caplog.at_level(logging.WARNING, logger=lock_mod.__name__):
            watch = asyncio.create_task(lock.watch())
            await _until(lambda: r.calls.count("eval") >= 3)
            assert not watch.done() and lock.held  # 일시 실패로는 잃지 않는다
            assert "다시 시도한다" in caplog.text
            assert all(rec.levelno == logging.WARNING for rec in caplog.records)
            clock.now = 8.0
            r.fail_eval = False  # 회복 — 마지막 성공 갱신 시각이 8.0이 된다
            n = r.calls.count("eval")
            await _until(lambda: r.calls.count("eval") >= n + 2)
            r.fail_eval = True
            clock.now = 17.0  # 8.0 기준 9초 — 기한 전이라 아직 잃지 않는다
            n = r.calls.count("eval")
            await _until(lambda: r.calls.count("eval") >= n + 2)
            assert not watch.done() and lock.held
            clock.now = 18.0  # 8.0 기준 10초 — 기한
            reason = await asyncio.wait_for(watch, 1.0)
        assert reason.startswith("갱신 실패 지속") and not lock.held

    async def test_release_deletes_only_own_value(self):
        r = FakeRedis()
        mine = WorkerSingletonLock(r, owner="h:1")
        await mine.acquire()
        r.kv[LOCK_KEY] = b"other:9"
        await mine.release()
        assert r.kv[LOCK_KEY] == b"other:9"
        del r.kv[LOCK_KEY]
        await mine.acquire()
        await mine.release()
        assert LOCK_KEY not in r.kv

    async def test_release_without_acquire_makes_no_call(self):
        r = FakeRedis({LOCK_KEY: b"other:9"})
        lock = WorkerSingletonLock(r, owner="h:1")
        await lock.acquire()
        r.calls.clear()
        await lock.release()
        assert r.calls == [] and r.kv[LOCK_KEY] == b"other:9"

    async def test_release_after_failed_renew_still_compares(self):
        """갱신 호출이 실패해 잃은 것으로 봤어도 값이 아직 자기 것이면 지운다(비교 후 삭제)."""
        r = FakeRedis()
        clock = _Clock()
        lock = WorkerSingletonLock(r, owner="h:1", renew_interval=0.01, clock=clock)
        await lock.acquire()
        r.fail_eval = True
        clock.now = float(LOCK_TTL_SECONDS)
        await asyncio.wait_for(lock.watch(), 1.0)
        r.fail_eval = False
        await lock.release()
        assert LOCK_KEY not in r.kv

    async def test_release_failure_is_swallowed(self, caplog):
        r = FakeRedis()
        lock = WorkerSingletonLock(r, owner="h:1")
        await lock.acquire()
        r.fail_eval = True
        with caplog.at_level(logging.WARNING, logger=lock_mod.__name__):
            await lock.release()
        assert "잠금 해제 실패" in caplog.text


# ═════════════════════════════════════════════════════════════════════════════
# 워커 배선 — run()
# ═════════════════════════════════════════════════════════════════════════════


def _worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, r: FakeRedis, *, owner: str = "h:1",
    renew_interval: float = RENEW_INTERVAL_SECONDS, **alarm: Any,
) -> AlarmWorker:
    """`run()`을 그대로 돌릴 워커 — 협력자 생성(`_build_*`)·Redis·그래프만 가짜로."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = harness_config(write_enforce_rules(tmp_path))
    cfg.alarm = AlarmConfig.model_construct(enabled=True, min_severity=1, **alarm)
    cfg.redis = SimpleNamespace(host="127.0.0.1", port=0, password="", db=0)
    worker = AlarmWorker(cfg)
    for name in dir(worker):
        if name.startswith("_build_") and callable(getattr(worker, name)):
            setattr(worker, name, lambda: None)
    worker._singleton_lock_for = (  # type: ignore[method-assign]
        lambda conn: WorkerSingletonLock(conn, owner=owner, renew_interval=renew_interval)
    )
    monkeypatch.setattr(worker_mod.aioredis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr(worker_mod, "build_alarm_graph", lambda *_a, **_k: None)
    return worker


class _BlockingConsume:
    """소비 본체 대역 — 취소될 때까지 돈다(취소되면 `cancelled`)."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def __call__(self, *a: Any, **k: Any) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class TestRunGuardOff:
    async def test_default_makes_no_lock_call(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r)
        consumed: list[bool] = []

        async def consume(*a: Any, **k: Any) -> None:
            consumed.append(True)

        worker._consume_stream = consume  # type: ignore[method-assign]
        made: list[Any] = []
        worker._singleton_lock_for = lambda conn: made.append(conn)  # type: ignore[method-assign,assignment,return-value]
        await worker.run()
        assert consumed == [True] and made == []
        assert _lock_calls(r) == [] and r.closed

    def test_flag_resolved_with_is_true(self, tmp_path):
        """목 설정(MagicMock)의 임의 속성이 참으로 읽혀 가드가 켜지지 않는다."""
        from unittest.mock import MagicMock

        cfg = harness_config(write_enforce_rules(tmp_path))
        cfg.alarm = MagicMock()
        assert AlarmWorker(cfg)._singleton_guard is False


class TestRunGuardOn:
    async def test_acquires_consumes_and_releases(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)
        seen: list[bytes | None] = []

        async def consume(*a: Any, **k: Any) -> None:
            seen.append(r.kv.get(LOCK_KEY))

        worker._consume_stream = consume  # type: ignore[method-assign]
        await worker.run()
        assert seen == [b"h:1"]  # 잠금을 쥔 채 소비했다
        assert LOCK_KEY not in r.kv and r.closed  # 종료 시 자기 값 삭제
        assert worker.lock_outcome is None

    async def test_held_by_other_consumes_nothing(self, tmp_path, monkeypatch, caplog):
        r = FakeRedis({LOCK_KEY: b"other-host:4242"})
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)
        consumed: list[bool] = []

        async def consume(*a: Any, **k: Any) -> None:
            consumed.append(True)

        worker._consume_stream = consume  # type: ignore[method-assign]
        with caplog.at_level(logging.ERROR, logger=worker_mod.__name__):
            await worker.run()
        assert consumed == []
        assert worker.lock_outcome == OUTCOME_NOT_ACQUIRED
        assert worker._ticket_summary_task is None  # 요약 루프도 띄우지 않는다
        errors = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
        assert len(errors) == 1 and "other-host:4242" in errors[0].getMessage()
        assert r.kv[LOCK_KEY] == b"other-host:4242" and r.closed  # 남의 잠금은 그대로

    async def test_shutdown_cancel_passes_to_consumer_and_releases(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)
        consume = _BlockingConsume()
        worker._consume_stream = consume  # type: ignore[method-assign]
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(consume.started.wait(), 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1.0)
        assert consume.cancelled and LOCK_KEY not in r.kv and r.closed

    async def test_two_instances_only_one_consumes(self, tmp_path, monkeypatch, caplog):
        r = FakeRedis()
        consumers: dict[str, _BlockingConsume] = {}
        workers = []
        for owner in ("h:1", "h:2"):
            w = _worker(tmp_path / owner.replace(":", "_"), monkeypatch, r, owner=owner,
                        worker_singleton_guard=True)
            consumers[owner] = _BlockingConsume()
            w._consume_stream = consumers[owner]  # type: ignore[method-assign]
            workers.append(w)
        with caplog.at_level(logging.ERROR, logger=worker_mod.__name__):
            tasks = [asyncio.create_task(w.run()) for w in workers]
            done, pending = await asyncio.wait(tasks, timeout=0.3)
        assert len(done) == 1 and len(pending) == 1  # 못 잡은 쪽만 끝났다
        started = [o for o, c in consumers.items() if c.started.is_set()]
        assert len(started) == 1
        winner = started[0]
        assert r.kv[LOCK_KEY] == winner.encode()
        assert len([rec for rec in caplog.records if rec.levelno == logging.ERROR]) == 1
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        assert LOCK_KEY not in r.kv


class TestLossDuringRun:
    """실행 중 잠금 상실 → 새로 읽지 않고 멈춘다(실제 `_consume_stream` · 읽기·처리만 가짜)."""

    @pytest.mark.parametrize("n", [1, 2])
    async def test_stops_reading_after_loss(self, tmp_path, monkeypatch, caplog, n: int):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, renew_interval=0.01,
                         worker_singleton_guard=True, worker_max_concurrency=n,
                         worker_drain_timeout_seconds=1.0)
        lost = asyncio.Event()
        made: list[WorkerSingletonLock] = []

        def lock_for(conn: Any) -> WorkerSingletonLock:
            lock = WorkerSingletonLock(conn, owner="h:1", renew_interval=0.01)
            original = lock.watch

            async def watch() -> str:
                reason = await original()
                lost.set()
                return reason

            lock.watch = watch  # type: ignore[method-assign]
            made.append(lock)
            return lock

        worker._singleton_lock_for = lock_for  # type: ignore[method-assign]
        reads_after_loss: list[int] = []
        delivered = [False]

        async def read_messages(*a: Any, **k: Any) -> list[tuple[bytes, dict]]:
            if lost.is_set():
                reads_after_loss.append(1)
            await asyncio.sleep(0.002)
            if not delivered[0]:
                delivered[0] = True
                return [(b"1-0", {b"data": b"{}"})]
            return []

        monkeypatch.setattr(worker_mod, "read_messages", read_messages)
        entered = asyncio.Event()
        release = asyncio.Event()
        outcome: list[str] = []

        async def process(*a: Any, **k: Any) -> None:
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                outcome.append("cancelled")
                raise
            outcome.append("done")

        worker._process = process  # type: ignore[method-assign]
        with caplog.at_level(logging.ERROR, logger=worker_mod.__name__):
            task = asyncio.create_task(worker.run())
            await asyncio.wait_for(entered.wait(), 1.0)
            r.kv[LOCK_KEY] = b"other:9"  # 만료 뒤 다른 인스턴스가 잡았다
            await asyncio.wait_for(lost.wait(), 1.0)
            if n >= 2:
                await asyncio.sleep(0.02)
                assert not task.done()  # W3 드레인 — 진행 중을 기다린다
                release.set()
            await asyncio.wait_for(task, 2.0)  # 취소가 새어 나오지 않고 정상 반환
        assert reads_after_loss == []
        assert worker.lock_outcome == OUTCOME_LOST
        # N≥2는 드레인 안에 끝냈고, N=1은 처리 중 1건이 종료 취소와 같은 경로(취소)로 끝난다.
        assert outcome == (["done"] if n >= 2 else ["cancelled"])
        errors = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.ERROR]
        assert len(errors) == 1 and "잠금 상실" in errors[0]
        assert r.kv[LOCK_KEY] == b"other:9" and r.closed  # 남의 잠금은 지우지 않는다

    async def test_transient_renew_failure_keeps_consuming(self, tmp_path, monkeypatch, caplog):
        """갱신이 잠깐 실패했다가 회복하면 소비를 멈추지 않는다(ERROR 0 · 종료 사유 없음)."""
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)
        worker._singleton_lock_for = (  # type: ignore[method-assign]
            lambda conn: WorkerSingletonLock(conn, owner="h:1", renew_interval=0.01,
                                             clock=_Clock())
        )
        consume = _BlockingConsume()
        worker._consume_stream = consume  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING):
            task = asyncio.create_task(worker.run())
            await asyncio.wait_for(consume.started.wait(), 1.0)
            r.fail_eval = True
            n = r.calls.count("eval")
            await _until(lambda: r.calls.count("eval") >= n + 3)
            r.fail_eval = False
            n = r.calls.count("eval")
            await _until(lambda: r.calls.count("eval") >= n + 2)
            assert not task.done() and not consume.cancelled
            assert r.kv[LOCK_KEY] == b"h:1"
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1.0)
        assert "다시 시도한다" in caplog.text
        assert [rec for rec in caplog.records if rec.levelno >= logging.ERROR] == []
        assert worker.lock_outcome is None and LOCK_KEY not in r.kv

    async def test_consumer_error_propagates_and_releases(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)

        async def consume(*a: Any, **k: Any) -> None:
            raise RuntimeError("주입 — 소비 실패")

        worker._consume_stream = consume  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="소비 실패"):
            await worker.run()
        assert LOCK_KEY not in r.kv and r.closed


# ═════════════════════════════════════════════════════════════════════════════
# 진입점 — noise_gate.worker
# ═════════════════════════════════════════════════════════════════════════════


def _entry_config(drain: float = 30.0) -> SimpleNamespace:
    return SimpleNamespace(alarm=AlarmConfig.model_construct(
        enabled=True, worker_drain_timeout_seconds=drain,
    ))


class TestEntryRunWorker:
    async def test_stop_cancels_and_waits_for_drain(self, monkeypatch):
        events: list[str] = []

        class _Worker:
            def __init__(self, config: Any) -> None:
                events.append("init")

            async def run(self) -> None:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await asyncio.sleep(0.02)  # 드레인 흉내 — 취소 뒤에도 마무리한다
                    events.append("drained")

        monkeypatch.setattr(entry, "AlarmWorker", _Worker)
        stop = asyncio.Event()
        runner = asyncio.create_task(entry.run_worker(_entry_config(), stop=stop))
        await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(runner, 1.0)
        assert events == ["init", "drained"]

    async def test_grace_limit_cancels_again(self, monkeypatch, caplog):
        cancels: list[int] = []

        class _Stubborn:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancels.append(1)
                        if len(cancels) >= 2:
                            raise

        monkeypatch.setattr(entry, "AlarmWorker", _Stubborn)
        monkeypatch.setattr(entry, "SHUTDOWN_GRACE_SECONDS", 0.05)
        stop = asyncio.Event()
        stop.set()
        with caplog.at_level(logging.WARNING, logger=entry.logger.name):
            await asyncio.wait_for(entry.run_worker(_entry_config(drain=0.0), stop=stop), 1.0)
        assert len(cancels) == 2
        assert "남은 처리를 취소한다" in caplog.text

    async def test_worker_exception_propagates(self, monkeypatch):
        class _Broken:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                raise RuntimeError("주입 — 워커 실패")

        monkeypatch.setattr(entry, "AlarmWorker", _Broken)
        with pytest.raises(RuntimeError, match="워커 실패"):
            await entry.run_worker(_entry_config(), stop=asyncio.Event())

    async def test_worker_returning_ends_runner(self, monkeypatch):
        """잠금을 못 잡는 등으로 워커가 스스로 끝나면 진입점도 끝난다."""

        class _Quick:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                return None

        monkeypatch.setattr(entry, "AlarmWorker", _Quick)
        outcome = await asyncio.wait_for(
            entry.run_worker(_entry_config(), stop=asyncio.Event()), 1.0,
        )
        assert outcome is None

    @pytest.mark.parametrize("lock_outcome", [OUTCOME_LOST, OUTCOME_NOT_ACQUIRED])
    async def test_returns_worker_lock_outcome(self, monkeypatch, lock_outcome: str):
        """잠금 때문에 워커가 스스로 끝나면 그 사유를 돌려준다."""

        class _Locked:
            lock_outcome: str | None = None

            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                self.lock_outcome = lock_outcome

        monkeypatch.setattr(entry, "AlarmWorker", _Locked)
        outcome = await asyncio.wait_for(
            entry.run_worker(_entry_config(), stop=asyncio.Event()), 1.0,
        )
        assert outcome == lock_outcome

    async def test_hung_after_recancel_returns_without_waiting(self, monkeypatch, caplog):
        """재취소 뒤에도 끝나지 않으면 상한만큼만 기다리고 `STOP_HUNG`으로 나온다."""
        finish = asyncio.Event()
        cancels: list[int] = []

        class _Hung:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                while not finish.is_set():
                    try:
                        await finish.wait()
                    except asyncio.CancelledError:
                        cancels.append(1)

        monkeypatch.setattr(entry, "AlarmWorker", _Hung)
        monkeypatch.setattr(entry, "SHUTDOWN_GRACE_SECONDS", 0.05)
        stop = asyncio.Event()
        stop.set()
        try:
            with caplog.at_level(logging.WARNING, logger=entry.logger.name):
                outcome = await asyncio.wait_for(
                    entry.run_worker(_entry_config(drain=0.0), stop=stop), 1.0,
                )
        finally:
            finish.set()  # 두고 나온 태스크를 테스트 루프 안에서 끝낸다
            await asyncio.sleep(0.01)
        assert outcome == entry.STOP_HUNG and len(cancels) == 2
        assert "재취소 뒤에도" in caplog.text


class TestEntryMain:
    @pytest.fixture(autouse=True)
    def _no_global_log_factory(self, monkeypatch):
        monkeypatch.setattr(entry, "install_log_record_factory", lambda: None)

    def test_config_load_failure_exit_2(self, monkeypatch, caplog):
        def broken() -> Any:
            raise ValueError("ALARM_ENABLED: 불리언 아님")

        monkeypatch.setattr(entry, "load_config", broken)
        with caplog.at_level(logging.ERROR, logger=entry.logger.name):
            assert entry.main() == 2
        assert "설정 로드 실패" in caplog.text and "불리언 아님" in caplog.text

    def test_alarm_disabled_exits_0_without_worker(self, monkeypatch):
        cfg = SimpleNamespace(log_level="INFO", alarm=AlarmConfig.model_construct(enabled=False))
        monkeypatch.setattr(entry, "load_config", lambda: cfg)

        def no_run(*a: Any, **k: Any) -> None:
            raise AssertionError("워커를 띄우면 안 된다")

        monkeypatch.setattr(entry, "run_worker", no_run)
        assert entry.main() == 0

    @pytest.mark.parametrize(("in_process", "guard", "warned"), [
        (True, False, True), (False, False, False), (True, True, False),
    ])
    def test_warns_when_main_process_also_consumes(
        self, monkeypatch, caplog, in_process: bool, guard: bool, warned: bool,
    ):
        cfg = SimpleNamespace(log_level="INFO", alarm=AlarmConfig.model_construct(
            enabled=True, worker_in_process=in_process, worker_singleton_guard=guard,
        ))
        monkeypatch.setattr(entry, "load_config", lambda: cfg)

        async def fake_run(config: Any) -> None:
            return None

        monkeypatch.setattr(entry, "run_worker", fake_run)
        with caplog.at_level(logging.WARNING, logger=entry.logger.name):
            assert entry.main() == 0
        assert ("ALARM_WORKER_IN_PROCESS=true" in caplog.text) is warned

    @pytest.mark.parametrize(("outcome", "code"), [
        (OUTCOME_LOST, 3), (OUTCOME_NOT_ACQUIRED, 4), ("hung", 1), (None, 0),
    ])
    def test_exit_code_by_outcome(self, monkeypatch, caplog, outcome: str | None, code: int):
        cfg = SimpleNamespace(log_level="INFO", alarm=AlarmConfig.model_construct(
            enabled=True, worker_in_process=False,
        ))
        monkeypatch.setattr(entry, "load_config", lambda: cfg)

        async def fake_run(config: Any) -> str | None:
            return outcome

        monkeypatch.setattr(entry, "run_worker", fake_run)
        with caplog.at_level(logging.INFO, logger=entry.logger.name):
            assert entry.main() == code
        errors = [rec.getMessage() for rec in caplog.records if rec.levelno >= logging.ERROR]
        if code:
            assert len(errors) == 1 and f"종료 코드 {code}" in errors[0]
            assert f"사유={outcome}" in errors[0]
        else:
            assert errors == [] and "알람 워커 프로세스 종료" in caplog.text

    def test_worker_crash_exit_1(self, monkeypatch, caplog):
        cfg = SimpleNamespace(log_level="INFO", alarm=AlarmConfig.model_construct(
            enabled=True, worker_in_process=False,
        ))
        monkeypatch.setattr(entry, "load_config", lambda: cfg)

        async def crash(config: Any) -> None:
            raise RuntimeError("주입")

        monkeypatch.setattr(entry, "run_worker", crash)
        with caplog.at_level(logging.ERROR, logger=entry.logger.name):
            assert entry.main() == 1
        assert "비정상 종료" in caplog.text


class TestEntrySubprocess:
    """실제 `python -m noise_gate.worker` — Redis 없이 끝나는 형태만(빈 작업 폴더 · `.env` 없음)."""

    @staticmethod
    def _run(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
        child_env = {k: v for k, v in os.environ.items() if not k.startswith("ALARM_")}
        child_env.update(env)
        child_env["PYTHONPATH"] = str(_ROOT)
        return subprocess.run(
            [sys.executable, "-m", "noise_gate.worker"], cwd=tmp_path, env=child_env,
            capture_output=True, text=True, timeout=120,
        )

    def test_alarm_disabled_exits_0(self, tmp_path):
        proc = self._run(tmp_path, ALARM_ENABLED="false")
        assert proc.returncode == 0, proc.stderr
        assert "ALARM_ENABLED=false" in proc.stderr

    def test_bad_config_exits_2(self, tmp_path):
        proc = self._run(tmp_path, ALARM_ENABLED="not-a-bool")
        assert proc.returncode == 2, proc.stderr
        assert "설정 로드 실패" in proc.stderr
