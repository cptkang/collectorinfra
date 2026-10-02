"""트레이스 JSONL 스키마 계약 테스트 (D-141).

로그 파일은 사람과 도구가 읽는 **인터페이스**다. 필드명·타입이 조용히 바뀌면
grep·파서·대시보드가 말없이 깨지므로 여기서 고정한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.observability import trace_collector as tc
from src.observability import trace_writer as tw
from src.observability.levels import TraceLevel

#: 요약 헤더(첫 줄)의 필수 필드와 타입.
HEADER_SCHEMA: dict[str, type | tuple] = {
    "kind": str,
    "ts": str,
    "request_id": str,
    "severity": str,
    "triggers": list,
    "total_ms": (int, float),
    "node_path": list,
    "step_count": int,
    "user_query": str,
}

#: 단계 줄의 필수 필드와 타입. `thread_id`·`reason`은 None일 수 있다.
STEP_SCHEMA: dict[str, type | tuple] = {
    "ts": str,
    "request_id": str,
    "step": int,
    "node": str,
    "level": str,
    "event": str,
    "elapsed_ms": (int, float),
    "payload": dict,
}

NULLABLE_FIELDS = ("thread_id", "reason")


@pytest.fixture(autouse=True)
def _clean():
    tc.reset_all()
    yield
    tc.reset_all()


@pytest.fixture
def dumped(tmp_path) -> list[dict]:
    """실패 트레이스를 하나 만들고 파싱해 돌려준다."""
    tc.start_request("req1", thread_id="t1", user_query="CPU 사용률")
    tc.record_step("req1", tc.TraceStep(
        step=1, node="query_generator", level=TraceLevel.INFO,
        event="node.exit", elapsed_ms=12.3,
    ))
    tc.record_step("req1", tc.TraceStep(
        step=2, node="query_executor", level=TraceLevel.ERROR, event="node.exception",
        elapsed_ms=44.0, reason="db2_sql_error", payload={"error": "SQLCODE=-206"},
    ))
    state = {"routing_intent": "data_query", "error_message": "boom",
             "query_results": [], "retry_count": 1}
    path = tw.flush_if_failed("req1", state, project_root=tmp_path)
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestHeaderContract:
    def test_required_fields_and_types(self, dumped):
        header = dumped[0]
        for field, expected in HEADER_SCHEMA.items():
            assert field in header, f"헤더 필수 필드 누락: {field}"
            assert isinstance(header[field], expected), (
                f"헤더 {field} 타입 불일치: {type(header[field])} != {expected}"
            )

    def test_kind_marks_summary(self, dumped):
        assert dumped[0]["kind"] == "summary"

    def test_severity_is_known_value(self, dumped):
        assert dumped[0]["severity"] in {"error", "warn"}

    def test_triggers_are_known_values(self, dumped):
        known = {"exception", "zero_rows", "retry", "output_failed"}
        assert set(dumped[0]["triggers"]) <= known

    def test_timestamp_is_iso8601(self, dumped):
        datetime.fromisoformat(dumped[0]["ts"])  # 파싱 실패 시 예외

    def test_node_path_collapses_consecutive_duplicates(self, dumped):
        """노드마다 enter/exit 두 단계가 쌓이므로 연속 중복은 접는다."""
        nodes = [s["node"] for s in dumped[1:]]
        expected = [n for i, n in enumerate(nodes) if i == 0 or nodes[i - 1] != n]

        assert dumped[0]["node_path"] == expected
        assert dumped[0]["step_count"] == len(dumped) - 1

    def test_node_path_keeps_retry_loops(self, tmp_path):
        """재시도로 되돌아온 노드는 접히지 않는다 (루프 횟수가 보여야 한다)."""
        tc.start_request("req2")
        for node in ("query_generator", "query_validator", "query_generator"):
            tc.record_step("req2", tc.TraceStep(
                step=1, node=node, level=TraceLevel.INFO, event="node.exit", elapsed_ms=1.0))
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 1}

        path = tw.flush_if_failed("req2", state, project_root=tmp_path)
        header = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

        assert header["node_path"] == ["query_generator", "query_validator", "query_generator"]


class TestStepContract:
    def test_required_fields_and_types(self, dumped):
        for step in dumped[1:]:
            for field, expected in STEP_SCHEMA.items():
                assert field in step, f"단계 필수 필드 누락: {field}"
                assert isinstance(step[field], expected), (
                    f"단계 {field} 타입 불일치: {type(step[field])} != {expected}"
                )

    def test_nullable_fields_present(self, dumped):
        """null이어도 키는 있어야 한다 — 소비자가 KeyError를 만나지 않도록."""
        for step in dumped[1:]:
            for field in NULLABLE_FIELDS:
                assert field in step, f"nullable 필드 키 누락: {field}"

    def test_level_is_known_value(self, dumped):
        for step in dumped[1:]:
            assert step["level"] in {"ERROR", "WARN", "INFO", "DEBUG"}

    def test_error_step_carries_reason(self, dumped):
        """ERROR·WARN 단계는 구조화 사유를 반드시 갖는다."""
        for step in dumped[1:]:
            if step["level"] in {"ERROR", "WARN"}:
                assert step["reason"], f"{step['level']} 단계에 reason이 없음: {step}"


class TestFileLayout:
    def test_path_layout(self, tmp_path):
        tc.start_request("abc123")
        tc.record_step("abc123", tc.TraceStep(
            step=1, node="n", level=TraceLevel.INFO, event="node.exit", elapsed_ms=1.0))
        state = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}

        path = tw.flush_if_failed("abc123", state, project_root=tmp_path)

        expected = tmp_path / "logs" / "trace" / datetime.now().strftime("%Y-%m-%d") / "abc123.jsonl"
        assert path == expected

    def test_every_line_is_valid_json(self, tmp_path, dumped):
        """모든 줄이 독립적으로 파싱 가능한 JSON이다 (JSONL 계약)."""
        assert all(isinstance(line, dict) for line in dumped)

    def test_no_trailing_blank_lines_break_parsing(self, dumped):
        assert len(dumped) >= 2
