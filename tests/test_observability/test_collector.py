"""요청 스코프 단계 수집기 테스트 (D-141).

정상 경로에서는 파일을 쓰지 않고 메모리에만 누적한다. 버퍼는 반드시 bound되어야
하며(값·키 양쪽), 요청 종료 시 해제되어 누수가 없어야 한다.
"""

from __future__ import annotations

import asyncio

import pytest

from src.observability import trace_collector as tc
from src.observability.levels import TraceLevel


@pytest.fixture(autouse=True)
def _clean():
    tc.reset_all()
    yield
    tc.reset_all()


def _step(n: int, level: TraceLevel = TraceLevel.INFO, **kw) -> tc.TraceStep:
    kw.setdefault("reason", "r" if level.requires_reason else None)
    return tc.TraceStep(step=n, node=f"node{n}", level=level, event="node.exit", elapsed_ms=1.0, **kw)


class TestLifecycle:
    def test_records_and_reads_back(self):
        tc.start_request("req1", thread_id="t1", user_query="질의")
        tc.record_step("req1", _step(1))
        tc.record_step("req1", _step(2))

        steps = tc.steps_for("req1")
        assert [s.step for s in steps] == [1, 2]

    def test_end_request_releases_buffer(self):
        tc.start_request("req1")
        tc.record_step("req1", _step(1))

        tc.end_request("req1")

        assert tc.steps_for("req1") == []
        assert tc.active_request_count() == 0

    def test_record_without_start_is_ignored(self):
        """시작되지 않은 요청의 기록은 버려진다 — 임의 키로 dict가 자라지 않는다."""
        tc.record_step("ghost", _step(1))

        assert tc.steps_for("ghost") == []
        assert tc.active_request_count() == 0

    def test_meta_is_kept(self):
        tc.start_request("req1", thread_id="t9", user_query="CPU 사용률")

        meta = tc.meta_for("req1")
        assert meta["thread_id"] == "t9"
        assert meta["user_query"] == "CPU 사용률"


class TestBounds:
    def test_step_buffer_is_bounded(self):
        """상한을 넘으면 가장 오래된 단계부터 밀어낸다."""
        tc.start_request("req1", max_steps=10)
        for i in range(30):
            tc.record_step("req1", _step(i))

        steps = tc.steps_for("req1")
        assert len(steps) == 10
        assert [s.step for s in steps] == list(range(20, 30))

    def test_active_requests_are_bounded(self):
        """동시 요청 키도 bound된다 — end_request 누락 시 메모리 누수 방지."""
        for i in range(tc._MAX_ACTIVE_REQUESTS + 20):
            tc.start_request(f"req{i}")

        assert tc.active_request_count() == tc._MAX_ACTIVE_REQUESTS
        assert tc.steps_for("req0") == []          # 가장 오래된 것이 밀려남
        assert tc.meta_for(f"req{tc._MAX_ACTIVE_REQUESTS + 19}") is not None


class TestReasonContract:
    def test_error_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            tc.TraceStep(step=1, node="n", level=TraceLevel.ERROR, event="e", elapsed_ms=0.0)

    def test_warn_requires_reason(self):
        with pytest.raises(ValueError, match="reason"):
            tc.TraceStep(step=1, node="n", level=TraceLevel.WARN, event="e", elapsed_ms=0.0)

    def test_info_does_not_require_reason(self):
        tc.TraceStep(step=1, node="n", level=TraceLevel.INFO, event="e", elapsed_ms=0.0)


class TestFailureIsolation:
    def test_record_failure_does_not_raise(self, monkeypatch):
        """수집 실패가 메인 로직으로 전파되지 않는다."""
        tc.start_request("req1")

        def _boom(*a, **k):
            raise RuntimeError("buffer broken")

        monkeypatch.setattr(tc, "_buffers", property(_boom), raising=False)
        monkeypatch.setattr(tc, "_append", _boom, raising=False)

        tc.record_step("req1", _step(1))  # 예외 없음


class TestTracedWrapper:
    """`traced()`는 노드 함수를 감싸 진입·이탈·예외를 자동 기록한다."""

    async def test_records_enter_and_exit(self):
        async def node(state):
            return {"current_node": "x"}

        wrapped = tc.traced(node, name="my_node")
        tc.start_request("req1")

        out = await wrapped({"request_id": "req1"})

        assert out == {"current_node": "x"}
        events = [s.event for s in tc.steps_for("req1")]
        assert "node.enter" in events and "node.exit" in events
        assert all(s.node == "my_node" for s in tc.steps_for("req1"))

    async def test_records_exception_and_reraises(self):
        async def node(state):
            raise ValueError("nope")

        wrapped = tc.traced(node, name="bad_node")
        tc.start_request("req1")

        with pytest.raises(ValueError, match="nope"):
            await wrapped({"request_id": "req1"})

        errors = [s for s in tc.steps_for("req1") if s.level is TraceLevel.ERROR]
        assert errors and errors[-1].event == "node.exception"
        assert errors[-1].reason == "ValueError"

    async def test_passthrough_when_no_request_id(self):
        """request_id가 없으면 수집만 건너뛰고 노드는 정상 동작한다."""
        async def node(state):
            return {"ok": True}

        wrapped = tc.traced(node, name="n")

        assert await wrapped({}) == {"ok": True}
        assert tc.active_request_count() == 0

    async def test_sync_node_is_supported(self):
        """동기 노드(`error_response` 등)도 감쌀 수 있다."""
        def node(state):
            return {"sync": True}

        wrapped = tc.traced(node, name="sync_node")
        tc.start_request("req1")

        assert await wrapped({"request_id": "req1"}) == {"sync": True}
        assert tc.steps_for("req1")

    async def test_step_numbers_increase(self):
        async def node(state):
            return {}

        wrapped = tc.traced(node, name="n")
        tc.start_request("req1")

        await wrapped({"request_id": "req1"})
        await wrapped({"request_id": "req1"})

        numbers = [s.step for s in tc.steps_for("req1")]
        assert numbers == sorted(numbers)
        assert len(set(numbers)) == len(numbers)

    async def test_concurrent_requests_are_isolated(self):
        """동시 요청의 단계가 서로 섞이지 않는다."""
        async def node(state):
            await asyncio.sleep(0)
            return {}

        wrapped = tc.traced(node, name="n")
        tc.start_request("reqA")
        tc.start_request("reqB")

        await asyncio.gather(
            wrapped({"request_id": "reqA"}),
            wrapped({"request_id": "reqB"}),
            wrapped({"request_id": "reqA"}),
        )

        assert len(tc.steps_for("reqA")) == 4  # enter/exit × 2
        assert len(tc.steps_for("reqB")) == 2


class TestObserveState:
    """실패 신호 관찰 — 미들웨어가 최종 state 없이 판정할 수 있게 하는 경로."""

    def test_unresolved_fields_are_reduced_to_a_flag(self):
        """`smq_derivation`은 미해결 유무만 남는다 (원본을 담지 않는다)."""
        tc.start_request("req1")

        tc.observe_state("req1", {
            "smq_derivation": [{"unresolved": [{"field": "cpu", "reason": "no match"}]}]
        })

        assert tc.observed_state("req1")["smq_derivation"] == [{"unresolved": [None]}]

    def test_resolved_derivation_leaves_empty_marker(self):
        tc.start_request("req1")

        tc.observe_state("req1", {"smq_derivation": [{"unresolved": []}]})

        assert tc.observed_state("req1")["smq_derivation"] == []

    def test_malformed_derivation_does_not_raise(self):
        tc.start_request("req1")

        tc.observe_state("req1", {"smq_derivation": "not-a-list"})

        assert tc.observed_state("req1")["smq_derivation"] == []

    def test_observe_ignores_non_mapping(self):
        tc.start_request("req1")

        tc.observe_state("req1", ["not", "a", "mapping"])

        assert tc.observed_state("req1") == {}

    def test_observe_on_unknown_request_is_noop(self):
        tc.observe_state("ghost", {"retry_count": 3})

        assert tc.observed_state("ghost") == {}

    def test_later_observations_override_earlier(self):
        """마지막 노드가 본 값이 최종 판정 근거가 된다."""
        tc.start_request("req1")

        tc.observe_state("req1", {"retry_count": 0})
        tc.observe_state("req1", {"retry_count": 2})

        assert tc.observed_state("req1")["retry_count"] == 2


class TestDeltaSummary:
    """노드 반환 델타 요약 — 원본을 통째로 담지 않는다."""

    async def test_non_mapping_return_summarized_as_empty(self):
        async def node(state):
            return "not-a-mapping"

        wrapped = tc.traced(node, name="n")
        tc.start_request("req1")

        await wrapped({"request_id": "req1"})

        exit_step = [s for s in tc.steps_for("req1") if s.event == "node.exit"][0]
        assert exit_step.payload == {}

    async def test_error_delta_is_flagged(self):
        async def node(state):
            return {"error_message": "boom"}

        wrapped = tc.traced(node, name="n")
        tc.start_request("req1")

        await wrapped({"request_id": "req1"})

        exit_step = [s for s in tc.steps_for("req1") if s.event == "node.exit"][0]
        assert exit_step.payload["has_error"] is True

    async def test_row_count_recorded_without_rows(self):
        """행 수만 남고 행 내용은 담기지 않는다."""
        async def node(state):
            return {"query_results": [{"secret": "x"} for _ in range(7)]}

        wrapped = tc.traced(node, name="n")
        tc.start_request("req1")

        await wrapped({"request_id": "req1"})

        exit_step = [s for s in tc.steps_for("req1") if s.event == "node.exit"][0]
        assert exit_step.payload["row_count"] == 7
        assert "secret" not in str(exit_step.payload)
