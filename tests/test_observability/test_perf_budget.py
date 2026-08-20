"""트레이스 수집 성능 예산 테스트 (D-141).

예산: 요청당 **5ms 미만·256KB 미만**. 초과하면 수집 항목을 줄이지, 예산을 늘리지 않는다.
응답시간 목표(단순 질의 <10s)에서 관측이 유의미한 몫을 먹으면 안 된다.

측정은 벽시계라 CI 부하에 흔들린다. 그래서 p95를 보고, 예산에 여유를 둔다.
"""

from __future__ import annotations

import asyncio
import statistics
import sys
import time

import pytest

from src.observability import trace_collector as tc
from src.observability.levels import TraceLevel

#: 요청당 수집 오버헤드 상한(ms).
_TIME_BUDGET_MS = 5.0

#: 요청당 버퍼 메모리 상한(bytes).
_MEMORY_BUDGET_BYTES = 256 * 1024

#: 전형적인 요청의 노드 실행 수 (노드 ~12개 + 재시도 여유).
_TYPICAL_STEPS = 40

#: 측정 전 버릴 워밍업 런 수 (첫 런은 import·캐시 효과로 느리다).
_WARMUP_RUNS = 5

#: 통계를 낼 런 수.
_MEASURED_RUNS = 25

#: 최악값 허용 배수. 산발적 노이즈는 통과시키되 구조적 퇴행은 잡는다.
_HARD_CEILING = 4


@pytest.fixture(autouse=True)
def _clean():
    tc.reset_all()
    yield
    tc.reset_all()


def _deep_size(obj, _seen=None) -> int:
    """객체 그래프의 대략적 총 바이트 수."""
    _seen = _seen if _seen is not None else set()
    if id(obj) in _seen:
        return 0
    _seen.add(id(obj))
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(_deep_size(k, _seen) + _deep_size(v, _seen) for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(_deep_size(v, _seen) for v in obj)
    elif hasattr(obj, "__dict__"):
        size += _deep_size(vars(obj), _seen)
    elif hasattr(obj, "__slots__"):
        size += sum(_deep_size(getattr(obj, s, None), _seen) for s in obj.__slots__)
    return size


class TestTimeBudget:
    async def test_collection_overhead_under_budget(self):
        """노드 40개를 감싼 요청의 수집 오버헤드가 예산 안이다.

        측정 방법: 워밍업을 버리고 **중앙값**을 본다. 벽시계 측정은 GC·스케줄러·
        커버리지 계측에 흔들려 상위 분위수가 쉽게 튄다 — 중앙값이 실제 비용을 더
        정직하게 대표한다. 심각한 퇴행은 아래 `_HARD_CEILING`이 따로 잡는다.
        """
        async def node(state):
            return {"query_results": [{"a": 1}], "retry_count": 0}

        wrapped = tc.traced(node, name="n")
        samples: list[float] = []

        for run in range(_WARMUP_RUNS + _MEASURED_RUNS):
            rid = f"req{run}"
            tc.start_request(rid, max_steps=200)
            state = {"request_id": rid}

            traced_start = time.perf_counter()
            for _ in range(_TYPICAL_STEPS):
                await wrapped(state)
            traced_ms = (time.perf_counter() - traced_start) * 1000

            tc.end_request(rid)

            bare_start = time.perf_counter()
            for _ in range(_TYPICAL_STEPS):
                await node(state)
            bare_ms = (time.perf_counter() - bare_start) * 1000

            if run >= _WARMUP_RUNS:
                samples.append(max(0.0, traced_ms - bare_ms))

        median = statistics.median(samples)
        worst = max(samples)

        assert median < _TIME_BUDGET_MS, (
            f"수집 오버헤드 중앙값 {median:.2f}ms가 예산 {_TIME_BUDGET_MS}ms를 초과 — "
            f"예산을 늘리지 말고 수집 항목을 줄일 것"
        )
        assert worst < _TIME_BUDGET_MS * _HARD_CEILING, (
            f"수집 오버헤드 최악값 {worst:.2f}ms — 산발적 노이즈를 감안해도 과다"
        )

    async def test_untracked_request_has_no_overhead_path(self):
        """추적 대상이 아닌 요청은 계측 코드를 타지 않는다."""
        async def node(state):
            return {}

        wrapped = tc.traced(node, name="n")

        start = time.perf_counter()
        for _ in range(200):
            await wrapped({})  # request_id 없음
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert tc.active_request_count() == 0
        assert elapsed_ms < 50  # 200회에 50ms — 사실상 통과 경로


class TestMemoryBudget:
    def test_buffer_under_budget_at_typical_load(self):
        tc.start_request("req1", thread_id="t", user_query="질의" * 50, max_steps=200)
        for i in range(_TYPICAL_STEPS):
            tc.record_step("req1", tc.TraceStep(
                step=i, node=f"node_{i}", level=TraceLevel.INFO, event="node.exit",
                elapsed_ms=1.0, payload={"keys": ["a", "b", "c"], "row_count": 10},
            ))

        size = _deep_size(tc._buffers["req1"])
        assert size < _MEMORY_BUDGET_BYTES, f"버퍼 {size}B가 예산 {_MEMORY_BUDGET_BYTES}B 초과"

    def test_buffer_bounded_at_max_steps(self):
        """상한까지 채워도 예산 안이다 (최악 케이스)."""
        tc.start_request("req1", max_steps=200)
        for i in range(500):
            tc.record_step("req1", tc.TraceStep(
                step=i, node=f"node_{i}", level=TraceLevel.INFO, event="node.exit",
                elapsed_ms=1.0, payload={"keys": [f"k{j}" for j in range(20)]},
            ))

        assert len(tc.steps_for("req1")) == 200
        assert _deep_size(tc._buffers["req1"]) < _MEMORY_BUDGET_BYTES

    def test_large_result_is_not_copied_into_buffer(self):
        """큰 쿼리 결과가 버퍼로 복사되지 않는다 (신호만 축약 보관)."""
        tc.start_request("req1")
        big_rows = [{"col": "x" * 100} for _ in range(10_000)]

        tc.observe_state("req1", {"query_results": big_rows, "routing_intent": "data_query"})

        observed = tc.observed_state("req1")
        assert observed["query_results"] == [None]  # 비어있지 않다는 신호만
        assert _deep_size(tc._buffers["req1"]) < _MEMORY_BUDGET_BYTES

    def test_output_file_binary_not_copied(self):
        """산출물 바이너리도 존재 여부만 남는다."""
        tc.start_request("req1")

        tc.observe_state("req1", {"output_file": b"x" * 5_000_000, "file_type": "xlsx"})

        assert tc.observed_state("req1")["output_file"] is True
        assert _deep_size(tc._buffers["req1"]) < _MEMORY_BUDGET_BYTES


class TestConcurrencyBudget:
    def test_total_memory_bounded_by_active_limit(self):
        """동시 요청 상한 덕분에 전체 메모리도 bound된다."""
        for i in range(200):
            rid = f"req{i}"
            tc.start_request(rid, max_steps=200)
            for j in range(20):
                tc.record_step(rid, tc.TraceStep(
                    step=j, node="n", level=TraceLevel.INFO, event="node.exit", elapsed_ms=1.0))

        assert tc.active_request_count() == tc._MAX_ACTIVE_REQUESTS
        total = _deep_size(tc._buffers)
        assert total < _MEMORY_BUDGET_BYTES * tc._MAX_ACTIVE_REQUESTS
