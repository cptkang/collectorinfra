"""plans/207 트랙 B W0 · C-0 — 워커 구간 계측 · 부하 하네스.

- off(기본): 타이머를 만들지 않고(측정 호출 0) 감사 레코드에 `timing` 키·DEBUG 구간 줄이 없다.
- on: 감사 레코드마다 `timing`(기록 시점까지 아는 구간) · 알람 1건당 DEBUG 1줄(P1~P6) · 판정 불변.
- 부하 하네스: 짧은 지연으로 두 시나리오 · 간격 도착 · 실행기 주입.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from src.config import AlarmConfig

from noise_gate.application import alarm_worker as worker_mod
from noise_gate.application.worker_timing import StageTimer
from noise_gate.scripts import worker_load_bench as wlb
from noise_gate.tests import equivalence_harness as eh

_WORKER_LOGGER = "noise_gate.application.alarm_worker"
_AUDIT_KEYS = {"p1_ms", "p2_ms", "p3_ms", "p4_until_audit_ms"}


def _raw_records(work_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(work_dir.glob("run-*/decisions.jsonl"))
    assert len(paths) == 1, paths
    return [json.loads(x) for x in paths[0].read_text(encoding="utf-8").splitlines() if x.strip()]


def _timing_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """하네스 설정에 `worker_timing_enabled=True`만 더한다(나머지 동일)."""
    original = eh.harness_config

    def patched(rules_path: Path, **kw: Any) -> Any:
        cfg = original(rules_path, **kw)
        cfg.alarm = AlarmConfig.model_construct(min_severity=1, worker_timing_enabled=True)
        return cfg

    monkeypatch.setattr(eh, "harness_config", patched)


def _timing_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.name == _WORKER_LOGGER and "알람 구간 소요" in r.getMessage()
    ]


def test_flag_default_off() -> None:
    assert AlarmConfig.model_fields["worker_timing_enabled"].default is False


async def test_off_no_timer_no_key_no_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """off면 타이머 생성 0 · 감사 `timing` 키 0 · DEBUG 구간 줄 0."""

    def boom() -> None:
        raise AssertionError("계측 off인데 타이머를 만들었다")

    monkeypatch.setattr(worker_mod, "StageTimer", boom)
    caplog.set_level(logging.DEBUG, logger=_WORKER_LOGGER)
    res = await eh.run_sequence(eh.synthetic_alarms(), work_dir=tmp_path)

    records = _raw_records(tmp_path)
    assert records, "판정 레코드가 있어야 비교가 의미 있다"
    assert all("timing" not in rec for rec in records)
    assert _timing_lines(caplog) == []
    assert res.dead_letters == []


async def test_on_records_timing_and_debug_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """on이면 판정 레코드마다 `timing` · 처리 메시지마다 DEBUG 1줄 · 판정은 off와 같다."""
    alarms = eh.synthetic_alarms()
    off = await eh.run_sequence(alarms, work_dir=tmp_path / "off")

    _timing_on(monkeypatch)
    caplog.set_level(logging.DEBUG, logger=_WORKER_LOGGER)
    on = await eh.run_sequence(alarms, work_dir=tmp_path / "on")

    # 판정 불변 — 정규화(`timing` 제외) 결과가 같다.
    eh.assert_equivalent(off, on)

    raw = _raw_records(tmp_path / "on")
    # 판정 레코드만(`type` 없음) — 재발·해소 등 부속 레코드는 게이트 노드 밖이라 `timing`이 없다.
    records = [rec for rec in raw if "type" not in rec]
    assert records
    assert all("timing" not in rec for rec in raw if "type" in rec)
    for rec in records:
        timing = rec["timing"]
        assert set(timing) == _AUDIT_KEYS, timing
        assert all(isinstance(v, float) and v >= 0 for v in timing.values())

    lines = _timing_lines(caplog)
    assert len(lines) == len(alarms)  # 알람 1건당 1줄(조기 반환 포함)
    graph_ids = {rec["alarm_id"] for rec in records}
    for line in lines:
        alarm_id = line.split("alarm_id=")[1].split()[0]
        assert "p1=" in line and "p2=" in line and "p6=" in line and "total=" in line
        if alarm_id in graph_ids:
            assert all(f"{p}=" in line for p in ("p3", "p4", "p5")), line
    # 지문 중복(조기 반환)은 P3~P5가 없다 — 남은 P3와 ACK가 p6에 합산된다.
    dup = [ln for ln in lines if "alarm_id=BURST-dup " in ln]
    assert len(dup) == 1 and "p3=" not in dup[0] and "p4=" not in dup[0]


def test_stage_timer_marks_and_views() -> None:
    timer = StageTimer()
    for stage in ("p1", "p2", "p3"):
        timer.mark(stage)
    view = timer.audit_view()
    assert set(view) == _AUDIT_KEYS
    for stage in ("p4", "p5", "p6"):
        timer.mark(stage)
    text = timer.render()
    assert text.split()[0].startswith("p1=") and text.split()[-1].startswith("total=")
    assert [p.split("=")[0] for p in text.split()] == ["p1", "p2", "p3", "p4", "p5", "p6", "total"]


@pytest.mark.parametrize("scenario", wlb.SCENARIOS)
async def test_bench_short_run(tmp_path: Path, scenario: str) -> None:
    """짧은 지연으로 두 시나리오 — 전량 ACK · 판정 레코드 전량(지문 중복 0) · 지표 산출."""
    alarms = wlb.scenario_alarms(scenario, 6)
    res = await wlb.run_bench(
        alarms, scenario=scenario, collect_delay=0.002, llm_delay=0.01, work_dir=tmp_path
    )
    assert res.acked == 6 and res.dead_letters == 0
    assert len([r for r in _raw_records(tmp_path) if "type" not in r]) == 6
    assert res.throughput_per_s > 0
    assert res.max_stream_lag == 6 and res.max_backlog == 6  # 한꺼번에 도착
    assert 0 < res.p50_latency_s <= res.p95_latency_s <= res.max_latency_s
    # 직렬 하한 — 건마다 지연(수집+LLM)을 순서대로 기다린다.
    assert res.elapsed_s >= 6 * 0.012 * 0.9
    assert "| " + scenario + " | serial | 6 |" in wlb.render_table([res])


async def test_bench_interval_and_injected_executor(tmp_path: Path) -> None:
    """간격 도착 · 실행기 주입 — 실행기가 반복 호출되고 lag는 건수 이하."""
    calls = 0

    async def counting(worker: Any, r: Any, stream_key: str, group: str) -> None:
        nonlocal calls
        calls += 1
        await eh.serial_executor(worker, r, stream_key, group)

    res = await wlb.run_bench(
        wlb.scenario_alarms("distinct", 5), executor=counting, executor_name="counting",
        collect_delay=0.0, llm_delay=0.005, interval=0.01, work_dir=tmp_path,
    )
    assert res.acked == 5 and res.executor == "counting"
    assert calls >= 2
    assert 1 <= res.max_stream_lag <= 5


def test_bench_cli_json(capsys: pytest.CaptureFixture[str]) -> None:
    assert wlb.main(
        ["--scenario", "same", "--count", "3", "--llm-delay", "0.005", "--collect-delay", "0",
         "--json"]
    ) == 0
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert len(out) == 1 and out[0]["scenario"] == "same" and out[0]["acked"] == 3


def test_unknown_scenario_rejected() -> None:
    with pytest.raises(ValueError):
        wlb.scenario_alarms("nope", 1)
