"""plans/208 트랙 B W0 독립 검증 — 계측 off 비트 동일 · on 예외/조기 반환 안전 · 판정 불변.

`test_plan208_w0_timing.py`가 덮지 않는 경로를 본다.

- off: 그래프 예외 → dead-letter · 파싱 실패 · 조기 반환(게이트 on 심각도 미달 · 게이트 off 중복·
  심각도 미달)에서 타이머 생성 0 · DEBUG 구간 줄 0 · configurable `alarm_timing` 키 0.
- on: 같은 경로에서 ACK·dead-letter가 off와 같고 DEBUG 줄이 1건당 1줄(조기 반환·예외 포함).
- on/off 하네스 판정 동일(픽스처 순서 · 지연 섞은 합성 순서).

실 LLM·실 Redis·네트워크 0.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from src.config import AlarmConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests import equivalence_harness as eh

_WORKER_LOGGER = "noise_gate.application.alarm_worker"
_BASE = datetime(2026, 11, 2, 9, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# 구동기 — 하네스 재료로 원시 메시지(깨진 JSON 포함)와 그래프 대역을 직접 꽂는다
# ─────────────────────────────────────────────────────────────────────────────


class _RecordingGraph:
    """하네스 가짜 그래프를 감싸 configurable을 기록하고, 지정 알람에서 예외를 낸다."""

    def __init__(self, inner: eh.FakeAlarmGraph, *, fail_ids: set[str]) -> None:
        self._inner = inner
        self._fail_ids = fail_ids
        self.configurables: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict[str, Any], config: Any = None) -> dict[str, Any]:
        self.configurables.append(dict((config or {}).get("configurable", {})))
        if state["alarm_event"].alarm_id in self._fail_ids:
            raise RuntimeError("주입 예외 — 그래프 실패")
        return await self._inner.ainvoke(state, config)


def _cfg(
    rules_path: Path, *, timing: bool, min_severity: int = 1, gate: bool = True
) -> SimpleNamespace:
    cfg = eh.harness_config(rules_path, enable_noise_gate=gate)
    cfg.alarm = AlarmConfig.model_construct(
        min_severity=min_severity, worker_timing_enabled=timing
    )
    return cfg


async def _drive(
    work_dir: Path,
    messages: list[dict[bytes, bytes]],
    *,
    timing: bool,
    min_severity: int = 1,
    gate: bool = True,
    fail_ids: set[str] | None = None,
    specs: list[eh.HarnessAlarm] | None = None,
) -> tuple[eh.FakeStreamRedis, _RecordingGraph, Path]:
    work_dir.mkdir(parents=True, exist_ok=True)
    rules = eh.write_enforce_rules(work_dir)
    worker: Any = AlarmWorker(_cfg(rules, timing=timing, min_severity=min_severity, gate=gate))
    graph = _RecordingGraph(
        eh.FakeAlarmGraph({a.alarm_id: a for a in (specs or [])}), fail_ids=fail_ids or set()
    )
    r = eh.FakeStreamRedis()
    worker._graph = graph
    worker._redis = r
    path = work_dir / "decisions.jsonl"
    worker._decision_store = DecisionStore(str(path))
    worker._episodes = worker._build_episode_tracker()
    worker._page_registry = worker._build_page_registry()
    for m in messages:
        r.add(eh.STREAM_KEY, m)
    saved = worker_mod.time
    worker_mod.time = eh.EventClock(_BASE.timestamp())  # type: ignore[assignment]
    try:
        await eh.serial_executor(worker, r, eh.STREAM_KEY, eh.GROUP)
    finally:
        worker_mod.time = saved  # type: ignore[assignment]
    return r, graph, path


def _msg(payload: dict[str, Any]) -> dict[bytes, bytes]:
    return {b"data": json.dumps(payload, ensure_ascii=False).encode("utf-8")}


def _alarm(alarm_id: str, sec: int, **kw: Any) -> dict[str, Any]:
    kw.setdefault("host", f"vh{alarm_id.lower()}")
    kw.setdefault("name", f"검증 {alarm_id}")
    return eh.polestar_payload(alarm_id, _BASE + timedelta(seconds=sec), **kw)


def _timing_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _WORKER_LOGGER and "알람 구간 소요" in r.getMessage()
    ]


def _stages(line: str) -> list[str]:
    tail = line.split("alarm_id=", 1)[1].split(" ", 1)[1]
    return [part.split("=")[0] for part in tail.split()]


def _records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _dl_view(r: eh.FakeStreamRedis) -> list[dict[str, Any]]:
    """dead-letter 항목에서 시각 키만 뺀다(off/on 대조용)."""
    return [eh.normalize_record(d) for d in r.dead_letters]


def _no_timer(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("계측 off인데 StageTimer를 만들었다")

    monkeypatch.setattr(worker_mod, "StageTimer", boom)


# 예외 · 파싱 실패 · 정상 섞음 — 메시지 4건
def _mixed_messages() -> tuple[list[dict[bytes, bytes]], list[eh.HarnessAlarm]]:
    ok1 = _alarm("OK1", 0)
    bad = _alarm("FAIL1", 5)
    ok2 = _alarm("OK2", 10)
    msgs = [
        _msg(ok1),
        _msg(bad),
        {b"data": b"{not-json"},  # 파싱 실패 — 이벤트 생성 전 예외
        _msg(ok2),
    ]
    specs = [eh.HarnessAlarm(payload=p) for p in (ok1, bad, ok2)]
    return msgs, specs


# ─────────────────────────────────────────────────────────────────────────────
# off — 예외·조기 반환 경로에서도 측정 흔적 0
# ─────────────────────────────────────────────────────────────────────────────


async def test_off_exception_paths_no_timer_no_key_no_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _no_timer(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=_WORKER_LOGGER)
    msgs, specs = _mixed_messages()
    r, graph, path = await _drive(tmp_path, msgs, timing=False, fail_ids={"FAIL1"}, specs=specs)

    assert len(r.acked) == 4  # 예외·파싱 실패도 ACK
    assert len(r.dead_letters) == 2  # 그래프 예외 1 + 파싱 실패 1
    assert _timing_lines(caplog) == []
    assert all("alarm_timing" not in c for c in graph.configurables)
    recs = _records(path)
    assert {x["alarm_id"] for x in recs if "type" not in x} == {"OK1", "OK2"}
    assert all("timing" not in x for x in recs)


async def test_off_early_returns_no_timer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """게이트 on 심각도 미달 · 게이트 off 중복 · 게이트 off 심각도 미달 — 타이머·줄 0."""
    _no_timer(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=_WORKER_LOGGER)
    low = _alarm("LOW1", 0, severity=1)
    r1, g1, _ = await _drive(tmp_path / "gate", [_msg(low)], timing=False, min_severity=2)
    assert r1.acked and g1.configurables == []  # 그래프 미호출(조기 반환)

    a = _alarm("DUP1", 0)
    r2, g2, _ = await _drive(
        tmp_path / "nogate", [_msg(a), _msg(a), _msg(_alarm("LOW2", 3, severity=1))],
        timing=False, min_severity=2, gate=False,
        specs=[eh.HarnessAlarm(payload=a)],
    )
    assert len(r2.acked) >= 3 and len(g2.configurables) == 1  # 중복·미달은 그래프 전 반환
    assert all("alarm_timing" not in c for c in g2.configurables)
    assert _timing_lines(caplog) == []


# ─────────────────────────────────────────────────────────────────────────────
# on — 예외·조기 반환에서도 ACK·dead-letter가 off와 같고 DEBUG 줄은 1건당 1줄
# ─────────────────────────────────────────────────────────────────────────────


async def test_on_exception_paths_ack_deadletter_same_as_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    msgs, specs = _mixed_messages()
    r_off, _, p_off = await _drive(
        tmp_path / "off", msgs, timing=False, fail_ids={"FAIL1"}, specs=specs
    )
    caplog.set_level(logging.DEBUG, logger=_WORKER_LOGGER)
    r_on, g_on, p_on = await _drive(
        tmp_path / "on", msgs, timing=True, fail_ids={"FAIL1"}, specs=specs
    )

    assert r_on.acked == r_off.acked
    assert _dl_view(r_on) == _dl_view(r_off)
    # 판정 레코드 — timing 제외 동일
    assert sorted(eh.canonical(x) for x in _records(p_on)) == sorted(
        eh.canonical(x) for x in _records(p_off)
    )

    lines = _timing_lines(caplog)
    assert len(lines) == 4, lines  # 메시지 1건당 1줄(예외·파싱 실패 포함)
    by_id = {ln.split("alarm_id=")[1].split()[0] if "alarm_id= " not in ln else "": ln
             for ln in lines}
    # 정상 — P1~P6 전부
    assert _stages(by_id["OK1"]) == ["p1", "p2", "p3", "p4", "p5", "p6", "total"]
    # 그래프 예외 — P4·P5 없이 dead-letter가 p6에 합산
    assert _stages(by_id["FAIL1"]) == ["p1", "p2", "p3", "p6", "total"]
    # 파싱 실패 — 이벤트 전 예외라 alarm_id 빈 값 · p6만
    assert _stages(by_id[""]) == ["p6", "total"]
    # configurable 키는 콜러블로만 실리고(계측 on) 감사에는 dict로 기록된다
    assert all(callable(c.get("alarm_timing")) for c in g_on.configurables)
    decisions = [x for x in _records(p_on) if "type" not in x]
    assert decisions and all(
        set(x["timing"]) == {"p1_ms", "p2_ms", "p3_ms", "p4_until_audit_ms"} for x in decisions
    )


async def test_on_early_returns_line_and_ack(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=_WORKER_LOGGER)
    low = _alarm("LOW1", 0, severity=1)
    r1, g1, p1 = await _drive(tmp_path / "gate", [_msg(low)], timing=True, min_severity=2)
    assert r1.acked and g1.configurables == [] and r1.dead_letters == []
    assert _records(p1) == []
    lines = _timing_lines(caplog)
    assert len(lines) == 1 and _stages(lines[0]) == ["p1", "p2", "p6", "total"]

    caplog.clear()
    a = _alarm("DUP1", 0)
    r2, g2, _ = await _drive(
        tmp_path / "nogate", [_msg(a), _msg(a), _msg(_alarm("LOW2", 3, severity=1))],
        timing=True, min_severity=2, gate=False, specs=[eh.HarnessAlarm(payload=a)],
    )
    assert len(g2.configurables) == 1 and r2.dead_letters == []
    lines = _timing_lines(caplog)
    assert len(lines) == 3
    assert [_stages(ln) for ln in lines] == [
        ["p1", "p2", "p3", "p4", "p5", "p6", "total"],
        ["p1", "p2", "p6", "total"],
        ["p1", "p2", "p6", "total"],
    ]


async def test_on_debug_line_suppressed_when_logger_not_debug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """INFO 수준이면 DEBUG 줄은 안 보이지만 감사 `timing`은 남는다(설명서 「DEBUG일 때만」)."""
    caplog.set_level(logging.INFO, logger=_WORKER_LOGGER)
    a = _alarm("OK1", 0)
    _, _, path = await _drive(tmp_path, [_msg(a)], timing=True, specs=[eh.HarnessAlarm(payload=a)])
    assert _timing_lines(caplog) == []
    assert "timing" in [x for x in _records(path) if "type" not in x][0]


# ─────────────────────────────────────────────────────────────────────────────
# on/off 판정 동일 — W1 하네스 `diff`
# ─────────────────────────────────────────────────────────────────────────────


def _timing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    original = eh.harness_config

    def patched(rules_path: Path, **kw: Any) -> Any:
        cfg = original(rules_path, **kw)
        cfg.alarm = AlarmConfig.model_construct(min_severity=1, worker_timing_enabled=True)
        return cfg

    monkeypatch.setattr(eh, "harness_config", patched)


@pytest.mark.parametrize("which", ["fixture", "synthetic_delayed"])
async def test_on_off_harness_equivalent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    alarms = eh.fixture_alarms() if which == "fixture" else eh.synthetic_alarms()
    kw: dict[str, Any] = {} if which == "fixture" else {"seed": 3, "max_delay": 0.003}
    off = await eh.run_sequence(alarms, work_dir=tmp_path / "off", **kw)
    _timing_on(monkeypatch)
    on = await eh.run_sequence(alarms, work_dir=tmp_path / "on", **kw)
    assert off.records, "레코드가 있어야 비교가 의미 있다"
    assert eh.diff(off, on) == []
    assert on.completion_order == off.completion_order


async def test_on_raw_record_differs_only_by_timing(tmp_path: Path) -> None:
    """정규화 없이 원 레코드에서 `timing`·`ts`만 빼면 off와 바이트 단위로 같다.

    하네스 정규화는 접미사 `_ms` 키를 모든 깊이에서 빼므로, 계측이 다른 `_ms` 필드를 건드려도
    `diff`로는 못 잡는다 — 여기서는 `timing`·`ts` 두 키만 뺀다.
    """
    alarms = eh.synthetic_alarms()[:12]
    specs = list(alarms)
    msgs = [_msg(a.payload) for a in alarms]
    _, _, p_off = await _drive(tmp_path / "off", msgs, timing=False, specs=specs)
    _, _, p_on = await _drive(tmp_path / "on", msgs, timing=True, specs=specs)

    def strip(rec: dict[str, Any]) -> str:
        rec = {k: v for k, v in rec.items() if k not in ("timing", "ts")}
        return json.dumps(rec, ensure_ascii=False, sort_keys=True)

    off_recs, on_recs = _records(p_off), _records(p_on)
    assert len(off_recs) == len(on_recs) and off_recs
    assert sorted(strip(x) for x in off_recs) == sorted(strip(x) for x in on_recs)
    assert all("timing" not in x for x in off_recs)
