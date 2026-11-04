"""plans/208 트랙 B W1 — 동등성 하네스 자기 검증(현행 직렬 경로 · 제품 코드 무변경).

- 결정성: 같은 입력을 현행 직렬 경로로 두 번 돌리면 정규화 결과가 같다.
- 정규화: 타임스탬프·소요 시간 키만 빠지고 판정 키는 남는다.
- 지연 무작위: 직렬 경로에서는 그래프 지연(시드·크기)이 결과를 바꾸지 않는다.
- 감도: 처리 순서를 바꾸는 실행기는 차이로 잡힌다(하네스가 순서 위반을 놓치지 않는다).
- 입력 적합성: 합성 순서가 W2가 지켜야 할 순서 의존 단계를 실제로 밟는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from noise_gate.domain.notification_policy import (
    STAGE_CORRELATION,
    STAGE_CROSS_SOURCE,
    STAGE_DEPENDENCY,
    STAGE_FLAPPING,
    STAGE_INHIBITION,
    STAGE_PAGE_REVIEW,
    STAGE_SELF_HEAL,
    STAGE_STORM,
)
from noise_gate.infrastructure.redis_queue import read_messages
from noise_gate.tests.equivalence_harness import (
    CONSUMER,
    FakeAlarmGraph,
    FakeStreamRedis,
    HarnessResult,
    canonical,
    diff,
    fixture_alarms,
    normalize_record,
    run_sequence,
    serial_executor,
    synthetic_alarms,
)


def _stages(res: HarnessResult) -> set[str]:
    return {json.loads(r).get("stage", "") for r in res.records}


def _by_alarm(res: HarnessResult) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for line in res.records:
        rec = json.loads(line)
        if "type" not in rec and rec.get("alarm_id"):
            out[rec["alarm_id"]] = (rec.get("stage", ""), rec.get("tier", ""))
    return out


class TestDeterminism:
    async def test_fixture_serial_twice_is_identical(self, tmp_path: Path):
        alarms = fixture_alarms()
        a = await run_sequence(alarms, work_dir=tmp_path)
        b = await run_sequence(alarms, work_dir=tmp_path)
        assert diff(a, b) == []
        assert a.signature() == b.signature()
        assert len(set(a.acked)) == len(alarms) and not a.dead_letters
        assert a.records  # 판정 감사가 실제로 쌓였다

    async def test_synthetic_serial_twice_is_identical(self, tmp_path: Path):
        alarms = synthetic_alarms()
        a = await run_sequence(alarms, work_dir=tmp_path)
        b = await run_sequence(alarms, work_dir=tmp_path)
        assert diff(a, b) == []
        assert len(set(a.acked)) == len(alarms) and not a.dead_letters
        # 현행 조기 반환(지문 중복·심각도 미달)은 ACK를 두 번 한다(본문 + finally) — 결정적이라
        # 다중집합 그대로 비교한다.
        assert len(a.acked) > len(alarms)
        # 가상 시계가 P3의 `now`를 알람 발생 시각으로 매번 찾았다(대체값 0)
        assert a.clock_fallbacks == 0

    async def test_synthetic_exercises_order_dependent_stages(self, tmp_path: Path):
        res = await run_sequence(synthetic_alarms(), work_dir=tmp_path)
        assert {
            STAGE_STORM, STAGE_SELF_HEAL, STAGE_FLAPPING, STAGE_INHIBITION,
            STAGE_CORRELATION, STAGE_DEPENDENCY, STAGE_PAGE_REVIEW, STAGE_CROSS_SOURCE,
        } <= _stages(res)
        by = _by_alarm(res)
        # P4가 워커 상태(active_firings)를 읽는 경로 — 근본원인보다 먼저 온 자식만 미통보
        assert by["DEP-C1"] == (STAGE_DEPENDENCY, "dashboard")
        assert by["DEP-C2"] == (STAGE_DEPENDENCY, "suppress")
        # P5(사건 멤버 티어) → 다음 알람 P3(원인 조건 ④) 경로
        assert by["XS-EFF-1"] == (STAGE_CROSS_SOURCE, "dashboard")
        # 그래프 뒤 상태 요약에 사건·PAGE 레지스트리가 담긴다
        assert res.final_state["episodes"] and res.final_state["page_registry"]


class TestNormalization:
    def test_drops_timestamp_and_elapsed_keys_at_any_depth(self):
        a = {"ts": "2026-11-02T00:00:00+00:00", "tier": "page", "alarm_id": "A",
             "timing": {"p4_ms": 12}, "stage_evidence": {"x": {"elapsed_ms": 3, "k": 1}}}
        b = {"ts": "2026-11-02T09:09:09+00:00", "tier": "page", "alarm_id": "A",
             "timing": {"p4_ms": 999}, "stage_evidence": {"x": {"elapsed_ms": 7, "k": 1}}}
        assert canonical(a) == canonical(b)
        assert normalize_record(a) == {
            "tier": "page", "alarm_id": "A", "stage_evidence": {"x": {"k": 1}},
        }

    def test_keeps_decision_keys(self):
        base = {"ts": "t", "tier": "page", "stage": "matrix", "signals": {"storm": False},
                "fingerprint": "f"}
        for key, value in (("tier", "suppress"), ("stage", "storm"),
                           ("signals", {"storm": True}), ("fingerprint", "g")):
            assert canonical(base) != canonical({**base, key: value})


class TestRandomDelay:
    def test_delays_are_per_alarm_and_can_invert_completion_order(self):
        g = FakeAlarmGraph({}, seed=3, max_delay=0.02)
        ids = [a.alarm_id for a in synthetic_alarms()]
        delays = [g.delay_for(i) for i in ids]
        assert delays == [g.delay_for(i) for i in ids]  # 같은 알람 = 같은 지연(순서 무관)
        # 뒤 알람이 앞 알람보다 짧은 지연 — 동시 실행이면 완료 순서가 뒤집힌다
        assert any(later < earlier for earlier, later in zip(delays, delays[1:]))
        assert FakeAlarmGraph({}, seed=4, max_delay=0.02).delay_for(ids[0]) != delays[0]
        assert FakeAlarmGraph({}, seed=3).delay_for(ids[0]) == 0.0

    async def test_delay_does_not_change_serial_result(self, tmp_path: Path):
        alarms = synthetic_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        for seed in (1, 2):
            got = await run_sequence(alarms, work_dir=tmp_path, seed=seed, max_delay=0.004)
            assert diff(base, got) == []
            # 직렬은 지연과 무관하게 입력 순서대로 끝난다
            assert got.completion_order == base.completion_order


class TestExecutorSlot:
    async def test_custom_executor_is_used(self, tmp_path: Path):
        calls: list[str] = []

        async def wrapped(worker, r, stream_key, group):
            calls.append(stream_key)
            await serial_executor(worker, r, stream_key, group)

        alarms = fixture_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        got = await run_sequence(alarms, work_dir=tmp_path, executor=wrapped)
        assert calls == ["alarm:raw"]
        assert diff(base, got) == []

    async def test_cancel_when_drained_supports_endless_loop_executor(self, tmp_path: Path):
        """run() 소비 루프처럼 빈 읽기에서 끝나지 않는 실행기도 꽂을 수 있다."""

        async def endless(worker, r, stream_key, group):
            dedup: dict[str, float] = {}
            while True:
                msgs = await read_messages(r, stream_key, group, CONSUMER, count=10, block_ms=0)
                for msg_id, fields in msgs:
                    await worker._process(r, stream_key, group, msg_id, fields, dedup)

        alarms = fixture_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        got = await run_sequence(
            alarms, work_dir=tmp_path, executor=endless, cancel_when_drained=True
        )
        assert diff(base, got) == []

    async def test_reordering_executor_is_detected(self, tmp_path: Path):
        """감도 — P3 진입 순서를 뒤집는 실행기는 동등성 위반으로 잡힌다."""

        async def reversed_batches(worker, r: FakeStreamRedis, stream_key, group):
            dedup: dict[str, float] = {}
            while True:
                msgs = await read_messages(r, stream_key, group, CONSUMER, count=10, block_ms=0)
                if not msgs:
                    return
                for msg_id, fields in reversed(msgs):
                    await worker._process(r, stream_key, group, msg_id, fields, dedup)

        alarms = synthetic_alarms()
        base = await run_sequence(alarms, work_dir=tmp_path)
        got = await run_sequence(alarms, work_dir=tmp_path, executor=reversed_batches)
        assert diff(base, got) != []
        assert set(got.acked) == set(base.acked)  # 모두 ACK — 차이는 판정에서 났다
