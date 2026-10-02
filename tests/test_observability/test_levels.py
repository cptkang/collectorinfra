"""로그 레벨 규약과 실패 판정 술어 테스트 (D-141)."""

from __future__ import annotations

import pytest

from src.observability.levels import (
    FailureTrigger,
    TraceLevel,
    failure_triggers,
    severity_for,
)


class TestTraceLevel:
    def test_four_levels_defined(self):
        """규약은 4단이다 — 그 이상도 이하도 아니다."""
        assert {lv.value for lv in TraceLevel} == {"ERROR", "WARN", "INFO", "DEBUG"}

    def test_severity_ordering(self):
        """ERROR > WARN > INFO > DEBUG 순으로 심각도가 비교된다."""
        assert TraceLevel.ERROR.rank > TraceLevel.WARN.rank
        assert TraceLevel.WARN.rank > TraceLevel.INFO.rank
        assert TraceLevel.INFO.rank > TraceLevel.DEBUG.rank


class TestFailureTriggers:
    """사용자 확정 4기준 (스펙 §6.3)."""

    def test_no_trigger_on_healthy_state(self):
        state = {
            "routing_intent": "data_query",
            "query_results": [{"a": 1}],
            "retry_count": 0,
            "current_node": "output_generator",
        }
        assert failure_triggers(state) == []
        assert severity_for([]) is None

    def test_error_message_triggers_exception(self):
        state = {"error_message": "boom", "query_results": [{"a": 1}], "retry_count": 0}
        assert FailureTrigger.EXCEPTION in failure_triggers(state)

    def test_error_response_node_triggers_exception(self):
        state = {"current_node": "error_response", "query_results": [{"a": 1}], "retry_count": 0}
        assert FailureTrigger.EXCEPTION in failure_triggers(state)

    def test_zero_rows_triggers_only_for_data_query(self):
        """결과 0건은 data_query일 때만 실패 신호다 — 캐시 관리 등은 원래 행이 없다."""
        data = {"routing_intent": "data_query", "query_results": [], "retry_count": 0}
        cache = {"routing_intent": "cache_management", "query_results": [], "retry_count": 0}

        assert FailureTrigger.ZERO_ROWS in failure_triggers(data)
        assert FailureTrigger.ZERO_ROWS not in failure_triggers(cache)

    def test_retry_triggers(self):
        state = {"routing_intent": "data_query", "query_results": [{"a": 1}], "retry_count": 2}
        assert FailureTrigger.RETRY in failure_triggers(state)

    def test_output_failure_when_file_requested_but_missing(self):
        state = {
            "routing_intent": "data_query",
            "query_results": [{"a": 1}],
            "retry_count": 0,
            "file_type": "xlsx",
            "output_file": None,
        }
        assert FailureTrigger.OUTPUT_FAILED in failure_triggers(state)

    def test_output_ok_when_file_produced(self):
        state = {
            "routing_intent": "data_query",
            "query_results": [{"a": 1}],
            "retry_count": 0,
            "file_type": "xlsx",
            "output_file": b"xx",
        }
        assert FailureTrigger.OUTPUT_FAILED not in failure_triggers(state)

    def test_unresolved_mapping_triggers_output_failure(self):
        state = {
            "routing_intent": "data_query",
            "query_results": [{"a": 1}],
            "retry_count": 0,
            "smq_derivation": [{"unresolved": [{"field": "cpu", "reason": "no match"}]}],
        }
        assert FailureTrigger.OUTPUT_FAILED in failure_triggers(state)


class TestSeverity:
    def test_exception_is_error(self):
        assert severity_for([FailureTrigger.EXCEPTION]) == "error"

    def test_output_failure_is_error(self):
        assert severity_for([FailureTrigger.OUTPUT_FAILED]) == "error"

    def test_zero_rows_is_warn(self):
        assert severity_for([FailureTrigger.ZERO_ROWS]) == "warn"

    def test_retry_is_warn(self):
        assert severity_for([FailureTrigger.RETRY]) == "warn"

    def test_highest_severity_wins(self):
        """복수 해당 시 가장 높은 severity를 채택한다."""
        assert severity_for([FailureTrigger.RETRY, FailureTrigger.EXCEPTION]) == "error"
        assert severity_for([FailureTrigger.ZERO_ROWS, FailureTrigger.RETRY]) == "warn"

    def test_all_triggers_are_recorded(self):
        """severity는 하나지만 triggers는 전부 남는다 (스펙 §6.3)."""
        state = {
            "routing_intent": "data_query",
            "error_message": "boom",
            "query_results": [],
            "retry_count": 3,
        }
        triggers = failure_triggers(state)

        assert FailureTrigger.EXCEPTION in triggers
        assert FailureTrigger.ZERO_ROWS in triggers
        assert FailureTrigger.RETRY in triggers
        assert severity_for(triggers) == "error"


class TestMalformedState:
    """상태가 예상 형태가 아니어도 판정이 터지지 않는다 (관측이 앱을 깨면 안 됨)."""

    @pytest.mark.parametrize("state", [
        {},
        {"query_results": None, "retry_count": None},
        {"retry_count": "많음"},
        {"smq_derivation": "not-a-list"},
        {"query_results": "not-a-list", "routing_intent": "data_query"},
    ])
    def test_no_exception_on_odd_state(self, state):
        result = failure_triggers(state)
        assert isinstance(result, list)
        severity_for(result)  # 예외 없음


class TestJudgementIsolation:
    """판정 자체가 예외를 내면 안 된다 — 관측이 앱을 깨면 본말전도."""

    def test_exception_inside_state_access_is_absorbed(self, caplog):
        class Hostile(dict):
            def get(self, *a, **k):
                raise RuntimeError("hostile state")

        with caplog.at_level("DEBUG"):
            result = failure_triggers(Hostile())

        assert result == []

    def test_unknown_trigger_is_ignored_by_severity(self):
        """알 수 없는 트리거가 섞여도 severity 계산이 깨지지 않는다."""
        assert severity_for([FailureTrigger.RETRY, "made-up"]) == "warn"  # type: ignore[list-item]

    def test_empty_triggers_yield_none(self):
        assert severity_for([]) is None
