"""plans/208 트랙 B W5 · C-7 독립 검증.

구현자 테스트(`test_plan208_w5_worker_process.py`)와 겹치지 않는 관점.

- 가드 off(기본): 실제 `run()` + 실제 `_consume_stream` 직렬 루프 — 잠금 객체 생성·
  잠금 Redis 호출 0, 읽기 인자(count=10·block_ms=2000·consumer)·처리 순서·
  취소 의미(읽기 중 = 정상 반환 · 처리 중 = CancelledError 전파)가 기준(afc5626c) `run()`과 같다.
- 가드 on: N≥2 종료 드레인(진행 중 완료 → 정상 반환) · 잠금 상실 드레인 중 종료 신호(재취소 →
  진행 중 취소 · CancelledError 전파 · 남의 잠금 보존) · 상실 경로의 요약 루프 정리 · 태스크 누수 0.
- 일시적 갱신 실패 1회 = 영구 정지·정상 반환(특성 고정) · 획득은 협력자 생성 뒤(특성 고정) ·
  획득 중 Redis 예외 시 연결 미정리(결함 — xfail strict).
- 진입점: 실제 SIGTERM → 취소 → 핸들러 해제 · 드레인 중 두 번째 SIGTERM이 프로세스를 죽이지 않음 ·
  재취소 뒤 무한 대기(결함 — xfail strict) · 잠금 상실·획득 실패는 종료 코드 0(특성 고정) ·
  설정 로드 실패 로그 가림 · 기동 로그에 Redis 비밀번호 없음.
- 정적: 두 Lua 스크립트가 비교 뒤에만 변경 · 인자 · 잠금 값 꼴 · import 경계 · 계층 표.

실 Redis·실 LLM·과금 API 0 — 가짜·목만.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import os
import re
import signal
import socket
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
    WorkerSingletonLock,
    default_owner,
)
from noise_gate.tests.equivalence_harness import harness_config, write_enforce_rules

_ROOT = Path(__file__).resolve().parents[2]


class FakeRedis:
    """잠금·워커 기동이 부르는 메서드만 흉내 낸다(실 Redis 없음)."""

    def __init__(self, kv: dict[str, bytes] | None = None) -> None:
        self.kv: dict[str, bytes] = kv if kv is not None else {}
        self.calls: list[str] = []
        self.eval_args: list[tuple[Any, ...]] = []
        self.fail_set = False
        self.fail_eval_left = 0  # 앞에서부터 이 횟수만큼 eval 실패
        self.closed = False

    async def set(self, key: str, value: str, *, nx: bool = False,
                  ex: int | None = None) -> bool | None:
        self.calls.append("set")
        if self.fail_set:
            raise ConnectionError("주입 — SET 실패")
        if nx and key in self.kv:
            return None
        self.kv[key] = value.encode()
        return True

    async def get(self, key: str) -> bytes | None:
        self.calls.append("get")
        return self.kv.get(key)

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        self.calls.append("eval")
        self.eval_args.append((script, numkeys, *args))
        if self.fail_eval_left > 0:
            self.fail_eval_left -= 1
            raise ConnectionError("주입 — 일시 끊김")
        key, owner = args[0], args[1]
        if self.kv.get(key) != str(owner).encode():
            return 0
        if "expire" in script:
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


def _worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, r: FakeRedis, *, owner: str = "h:1",
    renew_interval: float = 10.0, builds: list[str] | None = None, **alarm: Any,
) -> AlarmWorker:
    """실제 `run()`을 돌릴 워커 — `_build_*`·Redis 연결·그래프만 가짜."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = harness_config(write_enforce_rules(tmp_path))
    cfg.alarm = AlarmConfig.model_construct(enabled=True, min_severity=1, **alarm)
    cfg.redis = SimpleNamespace(host="127.0.0.1", port=0, password="", db=0)
    worker = AlarmWorker(cfg)
    for name in dir(worker):
        if name.startswith("_build_") and callable(getattr(worker, name)):
            def _fake(name: str = name) -> None:
                if builds is not None:
                    builds.append(name)
                    r.calls.append("build")
            setattr(worker, name, _fake)
    worker._singleton_lock_for = (  # type: ignore[method-assign]
        lambda conn: WorkerSingletonLock(conn, owner=owner, renew_interval=renew_interval)
    )
    monkeypatch.setattr(worker_mod.aioredis, "from_url", lambda *a, **k: r)
    monkeypatch.setattr(worker_mod, "build_alarm_graph", lambda *_a, **_k: None)
    return worker


async def _until(pred: Any, *, tries: int = 400) -> None:
    for _ in range(tries):
        if pred():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("조건이 끝내 참이 되지 않았다")


def _leftover(before: set[asyncio.Task[Any]]) -> set[asyncio.Task[Any]]:
    return asyncio.all_tasks() - before - {asyncio.current_task()}  # type: ignore[operator]


class _Reader:
    """`read_messages` 대역 — 정해 둔 묶음을 차례로 돌려주고, 다 쓰면 취소될 때까지 막힌다."""

    def __init__(self, batches: list[list[tuple[bytes, dict]]]) -> None:
        self.batches = list(batches)
        self.kwargs: list[dict[str, Any]] = []
        self.args: list[tuple[Any, ...]] = []
        self.blocked = asyncio.Event()
        self.stop_counting = False
        self.reads_after_stop = 0

    async def __call__(self, *a: Any, **k: Any) -> list[tuple[bytes, dict]]:
        self.args.append(a)
        self.kwargs.append(k)
        if self.stop_counting:
            self.reads_after_stop += 1
        if self.batches:
            await asyncio.sleep(0)
            return self.batches.pop(0)
        self.blocked.set()
        await asyncio.Event().wait()
        return []


# ═════════════════════════════════════════════════════════════════════════════
# 1. 가드 off(기본) — 기준 run()과 같은 흐름 · 잠금 Redis 호출 0
# ═════════════════════════════════════════════════════════════════════════════


class TestGuardOffBitIdentical:
    async def test_serial_loop_same_reads_order_and_no_lock(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r)

        def no_lock(*a: Any, **k: Any) -> None:
            raise AssertionError("가드 off인데 잠금 객체를 만들었다")

        monkeypatch.setattr(worker_mod, "WorkerSingletonLock", no_lock)

        async def no_guarded(*a: Any, **k: Any) -> None:
            raise AssertionError("가드 off인데 감시 경로를 탔다")

        worker._consume_guarded = no_guarded  # type: ignore[method-assign]
        reader = _Reader([
            [(b"1-0", {b"data": b"{}"}), (b"2-0", {b"data": b"{}"})],
            [(b"3-0", {b"data": b"{}"})],
        ])
        monkeypatch.setattr(worker_mod, "read_messages", reader)
        processed: list[bytes] = []

        async def process(*a: Any, **k: Any) -> None:
            assert k == {}  # 직렬 경로는 slot 없이 부른다(기준과 같다)
            processed.append(a[3])

        worker._process = process  # type: ignore[method-assign]
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(reader.blocked.wait(), 1.0)
        task.cancel()
        # 기준 run(): 읽기 중 취소는 break → 정상 반환(취소가 새어 나오지 않는다)
        assert await asyncio.wait_for(task, 1.0) is None
        assert processed == [b"1-0", b"2-0", b"3-0"]
        group = worker._config.alarm.redis_consumer_group
        for a, k in zip(reader.args, reader.kwargs, strict=True):
            assert a[2:] == (group, worker_mod._CONSUMER_NAME)
            assert k == {"count": 10, "block_ms": 2000}
        assert _lock_calls(r) == [] and r.calls == ["xgroup_create"] and r.closed

    async def test_cancel_mid_process_propagates_like_baseline(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r)
        monkeypatch.setattr(worker_mod, "read_messages",
                            _Reader([[(b"1-0", {b"data": b"{}"})]]))
        entered = asyncio.Event()

        async def process(*a: Any, **k: Any) -> None:
            entered.set()
            await asyncio.Event().wait()

        worker._process = process  # type: ignore[method-assign]
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(entered.wait(), 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1.0)
        assert r.closed and _lock_calls(r) == []


# ═════════════════════════════════════════════════════════════════════════════
# 2. 가드 on — 종료·상실 드레인 · ACK 계약 경로 · 누수
# ═════════════════════════════════════════════════════════════════════════════


def _blocking_process(outcome: list[str], entered: asyncio.Event, release: asyncio.Event) -> Any:
    async def process(*a: Any, **k: Any) -> None:
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            outcome.append("cancelled")
            raise
        outcome.append("done")
    return process


class TestGuardedShutdownAndLoss:
    async def test_n2_shutdown_drains_inflight_then_releases(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True,
                         worker_max_concurrency=2, worker_drain_timeout_seconds=5.0)
        reader = _Reader([[(b"1-0", {b"data": b"{}"})]])
        monkeypatch.setattr(worker_mod, "read_messages", reader)
        outcome: list[str] = []
        entered, release = asyncio.Event(), asyncio.Event()
        worker._process = _blocking_process(outcome, entered, release)  # type: ignore[method-assign]
        before = asyncio.all_tasks()
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(entered.wait(), 1.0)
        await asyncio.wait_for(reader.blocked.wait(), 1.0)
        reader.stop_counting = True
        task.cancel()
        await asyncio.sleep(0.03)
        assert not task.done() and LOCK_KEY in r.kv  # 드레인 중에는 잠금을 쥔 채 기다린다
        release.set()
        assert await asyncio.wait_for(task, 2.0) is None  # W3: 드레인 끝 → 정상 반환
        assert outcome == ["done"] and reader.reads_after_stop == 0
        assert LOCK_KEY not in r.kv and r.closed
        await asyncio.sleep(0)
        assert _leftover(before) == set()

    async def test_n2_shutdown_during_loss_drain_cancels_inflight_and_reraises(
        self, tmp_path, monkeypatch,
    ):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, renew_interval=0.01,
                         worker_singleton_guard=True, worker_max_concurrency=2,
                         worker_drain_timeout_seconds=5.0)
        reader = _Reader([[(b"1-0", {b"data": b"{}"})]])
        monkeypatch.setattr(worker_mod, "read_messages", reader)
        outcome: list[str] = []
        entered, release = asyncio.Event(), asyncio.Event()
        worker._process = _blocking_process(outcome, entered, release)  # type: ignore[method-assign]
        before = asyncio.all_tasks()
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(entered.wait(), 1.0)
        r.kv[LOCK_KEY] = b"other:9"  # 만료 뒤 다른 인스턴스가 잡았다
        await _until(lambda: "eval" in r.calls and reader.blocked.is_set())
        await asyncio.sleep(0.05)  # 상실 → 소비 본체 취소 → 드레인 대기 중
        assert not task.done() and outcome == []
        task.cancel()  # 드레인 중 종료 신호
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1.0)
        assert outcome == ["cancelled"]  # 남은 건은 취소(N≥2 → ACK 안 함 · pending)
        assert r.kv[LOCK_KEY] == b"other:9" and r.closed  # 남의 잠금은 보존
        await asyncio.sleep(0)
        assert _leftover(before) == set()

    async def test_loss_stops_ticket_summary_loop_and_watch(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, renew_interval=0.01,
                         worker_singleton_guard=True)
        worker._build_ticket_queue = lambda: object()  # type: ignore[method-assign]
        summary_cancelled = asyncio.Event()

        async def summary_loop(*a: Any, **k: Any) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                summary_cancelled.set()
                raise

        monkeypatch.setattr(worker_mod, "run_ticket_summary_loop", summary_loop)
        reader = _Reader([])
        monkeypatch.setattr(worker_mod, "read_messages", reader)
        before = asyncio.all_tasks()
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(reader.blocked.wait(), 1.0)
        r.kv[LOCK_KEY] = b"other:9"
        assert await asyncio.wait_for(task, 1.0) is None
        await asyncio.wait_for(summary_cancelled.wait(), 1.0)
        await asyncio.sleep(0)
        assert _leftover(before) == set()

    async def test_consume_error_cancels_watch_without_leak(self, tmp_path, monkeypatch):
        r = FakeRedis()
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)

        async def consume(*a: Any, **k: Any) -> None:
            await asyncio.sleep(0)
            raise RuntimeError("주입 — ACK 실패")

        worker._consume_stream = consume  # type: ignore[method-assign]
        before = asyncio.all_tasks()
        with pytest.raises(RuntimeError, match="ACK 실패"):
            await worker.run()
        await asyncio.sleep(0)
        assert _leftover(before) == set()
        assert LOCK_KEY not in r.kv and r.closed


# ═════════════════════════════════════════════════════════════════════════════
# 3. 일시 갱신 실패 · 획득 순서 · 획득 예외
# ═════════════════════════════════════════════════════════════════════════════


class TestTransientAndAcquireEdges:
    async def test_single_transient_renew_error_is_retried_and_consumption_continues(
        self, tmp_path, monkeypatch, caplog,
    ):
        """갱신 eval 1회 일시 실패는 재시도한다 — 소비가 계속되고 ERROR 0 · 잠금 유지.

        검증 당시에는 1회 실패 = 영구 정지(정상 반환)였다(지적 Medium). W5 교정 1(a)로
        TTL 안에서는 재시도하도록 바뀌어 이 특성 테스트를 새 계약으로 뒤집었다(팀 리드).
        """
        r = FakeRedis()
        r.fail_eval_left = 1
        worker = _worker(tmp_path, monkeypatch, r, renew_interval=0.01,
                         worker_singleton_guard=True)
        reader = _Reader([])
        monkeypatch.setattr(worker_mod, "read_messages", reader)
        with caplog.at_level(logging.ERROR, logger=worker_mod.__name__):
            task = asyncio.create_task(worker.run())
            await _until(lambda: reader.blocked.is_set() and r.calls.count("eval") >= 3)
            assert not task.done()
            assert LOCK_KEY in r.kv
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task  # 종료 취소의 반환 형태는 이 테스트의 관심 밖
        errors = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.ERROR]
        assert errors == []
        assert worker.lock_outcome is None

    async def test_acquire_happens_after_builds_and_group_create(
        self, tmp_path, monkeypatch,
    ):
        """특성 고정(보고 지적 Low): 획득 실패 인스턴스도 협력자 생성·그룹 생성을
        모두 마친 뒤 끝난다."""
        r = FakeRedis({LOCK_KEY: b"other:1"})
        builds: list[str] = []
        worker = _worker(tmp_path, monkeypatch, r, builds=builds,
                         worker_singleton_guard=True)
        await worker.run()
        assert r.calls.index("xgroup_create") < r.calls.index("set")
        assert builds and max(i for i, c in enumerate(r.calls) if c == "build") < (
            r.calls.index("set")
        )

    async def test_acquire_redis_error_closes_connection(self, tmp_path, monkeypatch):
        r = FakeRedis()
        r.fail_set = True
        worker = _worker(tmp_path, monkeypatch, r, worker_singleton_guard=True)
        with pytest.raises(ConnectionError):
            await worker.run()
        assert r.closed


# ═════════════════════════════════════════════════════════════════════════════
# 4. 진입점 — 실제 신호 · 재취소 · 종료 코드 · 로그
# ═════════════════════════════════════════════════════════════════════════════


def _entry_config(drain: float = 30.0, **alarm: Any) -> SimpleNamespace:
    return SimpleNamespace(alarm=AlarmConfig.model_construct(
        enabled=True, worker_drain_timeout_seconds=drain, **alarm,
    ))


def _handlers(loop: asyncio.AbstractEventLoop) -> dict[Any, Any]:
    return getattr(loop, "_signal_handlers", {})


class TestEntrySignals:
    async def test_real_sigterm_then_second_sigterm_during_drain(self, monkeypatch):
        """실제 SIGTERM → 워커 취소 · 드레인 중 두 번째 SIGTERM은 프로세스를 죽이지 않는다 ·
        끝나면 핸들러를 걷는다."""
        loop = asyncio.get_running_loop()
        orig = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        events: list[str] = []
        draining = asyncio.Event()
        finish = asyncio.Event()

        class _Worker:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    events.append("cancelled")
                    draining.set()
                    await finish.wait()  # 드레인 흉내
                    events.append("drained")

        monkeypatch.setattr(entry, "AlarmWorker", _Worker)
        try:
            runner = asyncio.create_task(entry.run_worker(_entry_config()))
            await _until(lambda: signal.SIGTERM in _handlers(loop))
            if signal.SIGTERM not in _handlers(loop):  # pragma: no cover
                pytest.skip("이 루프에서 신호 처리기를 걸 수 없다")
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.wait_for(draining.wait(), 1.0)
            assert signal.SIGTERM in _handlers(loop)  # 드레인 중에도 처리기가 남아 있다
            os.kill(os.getpid(), signal.SIGTERM)  # 두 번째 신호 — 기본 동작(종료)로 새지 않는다
            await asyncio.sleep(0.05)
            assert not runner.done()  # 두 번째 신호는 드레인을 끊지 않는다(특성)
            finish.set()
            await asyncio.wait_for(runner, 1.0)
            assert events == ["cancelled", "drained"]
            assert signal.SIGTERM not in _handlers(loop)
            assert signal.SIGINT not in _handlers(loop)
        finally:
            for s, h in orig.items():
                if s in _handlers(loop):
                    loop.remove_signal_handler(s)
                signal.signal(s, h)

    async def test_recancel_wait_is_bounded(self, monkeypatch):
        allow_exit = asyncio.Event()

        class _Hung:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                while True:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if allow_exit.is_set():
                            raise
                        # 정리가 끝나지 않는 워커(취소를 받고도 계속 대기)

        monkeypatch.setattr(entry, "AlarmWorker", _Hung)
        monkeypatch.setattr(entry, "SHUTDOWN_GRACE_SECONDS", 0.05)
        stop = asyncio.Event()
        stop.set()
        runner = asyncio.create_task(entry.run_worker(_entry_config(drain=0.0), stop=stop))
        try:
            await asyncio.wait_for(asyncio.shield(runner), 0.5)
        finally:
            allow_exit.set()
            for t in asyncio.all_tasks():
                if t.get_name() == "alarm-worker" and not t.done():
                    t.cancel()
            await asyncio.gather(runner, return_exceptions=True)


class TestEntryMainExtra:
    @pytest.fixture(autouse=True)
    def _restore_log_factory(self):
        saved = logging.getLogRecordFactory()
        yield
        logging.setLogRecordFactory(saved)

    def test_lock_loss_or_acquire_failure_exits_0(self, monkeypatch, caplog):
        """특성 고정(보고 지적 Medium): 워커가 잠금 상실·획득 실패로 정상 반환하면 종료 코드 0 —
        정상 정지와 구분되지 않는다(Restart=on-failure 감독자는 재기동하지 않는다)."""
        cfg = SimpleNamespace(log_level="INFO", alarm=AlarmConfig.model_construct(
            enabled=True, worker_in_process=False, worker_singleton_guard=True,
        ), redis=SimpleNamespace(password=""))
        monkeypatch.setattr(entry, "load_config", lambda: cfg)

        class _LostWorker:
            def __init__(self, config: Any) -> None:
                pass

            async def run(self) -> None:
                return None  # run()의 획득 실패·상실 경로는 예외 없이 반환한다

        monkeypatch.setattr(entry, "AlarmWorker", _LostWorker)
        with caplog.at_level(logging.INFO, logger=entry.logger.name):
            assert entry.main() == 0
        assert "알람 워커 프로세스 종료" in caplog.text

    def test_config_failure_log_is_masked(self, monkeypatch, caplog):
        def broken() -> Any:
            raise ValueError("REDIS_URL=redis://u:hunter2@h:6379 password=hunter2")

        monkeypatch.setattr(entry, "load_config", broken)
        with caplog.at_level(logging.ERROR, logger=entry.logger.name):
            assert entry.main() == 2
        assert "설정 로드 실패" in caplog.text
        assert "hunter2" not in caplog.text

    def test_start_logs_carry_no_redis_password(self, monkeypatch, caplog):
        cfg = SimpleNamespace(log_level="INFO", alarm=AlarmConfig.model_construct(
            enabled=True, worker_in_process=False,
        ), redis=SimpleNamespace(host="h", port=1, db=0, password="pw-xyz-secret"))
        monkeypatch.setattr(entry, "load_config", lambda: cfg)

        async def fake_run(config: Any) -> None:
            return None

        monkeypatch.setattr(entry, "run_worker", fake_run)
        with caplog.at_level(logging.DEBUG):
            assert entry.main() == 0
        assert "pw-xyz-secret" not in caplog.text


# ═════════════════════════════════════════════════════════════════════════════
# 5. 정적 — Lua · 인자 · 잠금 값 · import 경계 · 계층 표
# ═════════════════════════════════════════════════════════════════════════════


class TestStatic:
    def test_lua_scripts_mutate_only_after_own_value_compare(self):
        for script, verb, forbidden in (
            (lock_mod._RENEW_SCRIPT, "expire", ("del", "set", "persist")),
            (lock_mod._RELEASE_SCRIPT, "del", ("expire", "set")),
        ):
            m = re.fullmatch(
                r"if redis\.call\('get', KEYS\[1\]\) == ARGV\[1\] then "
                r"return redis\.call\('(\w+)', KEYS\[1\](, ARGV\[2\])?\) else return 0 end",
                script,
            )
            assert m is not None, script
            assert m.group(1) == verb
            assert (m.group(2) is not None) is (verb == "expire")
            calls = re.findall(r"redis\.call\('(\w+)'", script)
            assert calls == ["get", verb] and not set(calls) & set(forbidden)
            assert set(re.findall(r"KEYS\[(\d+)\]", script)) == {"1"}

    async def test_eval_arguments_carry_key_owner_ttl_only(self):
        r = FakeRedis()
        lock = WorkerSingletonLock(r, owner="h:7", renew_interval=0.001)
        assert await lock.acquire()
        r.kv[LOCK_KEY] = b"x"  # 첫 갱신에서 상실로 끝내게
        await asyncio.wait_for(lock.watch(), 1.0)
        await lock.release()
        renew, release = r.eval_args
        assert renew == (lock_mod._RENEW_SCRIPT, 1, LOCK_KEY, "h:7", LOCK_TTL_SECONDS)
        assert release == (lock_mod._RELEASE_SCRIPT, 1, LOCK_KEY, "h:7")
        assert r.kv[LOCK_KEY] == b"x"  # 남의 값은 지우지 않았다

    def test_lock_value_is_host_pid_only(self):
        assert default_owner() == f"{socket.gethostname()}:{os.getpid()}"

    @staticmethod
    def _imports(rel: str) -> set[str]:
        tree = ast.parse((_ROOT / rel).read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_worker_lock_imports_stdlib_only(self):
        assert self._imports("noise_gate/infrastructure/worker_lock.py") <= {
            "__future__", "asyncio", "logging", "os", "socket", "typing",
        }

    def test_entry_imports_within_d139_scope(self):
        src_mods = {m for m in self._imports("noise_gate/worker.py") if m.startswith("src")}
        assert src_mods == {"src.config", "src.utils.log_masking"}

    def test_arch_map_marks_entry(self):
        text = (_ROOT / "scripts" / "arch_check.py").read_text(encoding="utf-8")
        assert re.search(r'"noise_gate\.worker":\s*"entry"', text)
