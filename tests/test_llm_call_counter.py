"""llm_call_counter(요청당 LLM 호출 계측기) 테스트.

실제 LLM 호출 없이 핸들러 메서드를 직접 호출하여 시뮬레이션한다 (D-127 준수).
"""

from __future__ import annotations

import logging

import pytest

from src.observability import llm_call_counter as counter
from src.observability.llm_call_counter import (
    finish_request,
    get_handler,
    record_tool_use,
    start_request,
)

_LOGGER_NAME = "src.observability.llm_call_counter"


@pytest.fixture(autouse=True)
def _clean_ctx():
    """테스트 간 계측 컨텍스트 누수를 방지한다."""
    counter._request_ctx.set(None)
    yield
    counter._request_ctx.set(None)


def _fire_chat_start(handler, model: str = "test-model") -> None:
    handler.on_chat_model_start({"kwargs": {"model": model}}, [[]], run_id=None)


class TestCounting:
    def test_two_events_counted(self):
        """(a) 핸들러 이벤트 2회 → llm_calls==2, by_model 집계."""
        start_request("req-1")
        handler = get_handler()
        _fire_chat_start(handler, model="m1")
        # 방어 경로(on_llm_start)도 동일하게 계측되어야 한다.
        handler.on_llm_start({"kwargs": {"model": "m1"}}, ["prompt"], run_id=None)

        summary = finish_request()
        assert summary["request_id"] == "req-1"
        assert summary["llm_calls"] == 2
        assert summary["by_model"] == {"m1": 2}

    def test_finish_resets_context(self):
        """finish_request 후 이벤트는 다음 요청에 계상되지 않는다."""
        start_request("req-a")
        _fire_chat_start(get_handler())
        assert finish_request()["llm_calls"] == 1

        # 컨텍스트 종료 후 이벤트 → no-op
        _fire_chat_start(get_handler())
        summary = finish_request()
        assert summary["request_id"] is None
        assert summary["llm_calls"] == 0


class TestThresholdWarning:
    def test_warning_emitted_once_over_threshold(self, monkeypatch, caplog):
        """(b) 임계치 초과 시 warning이 정확히 1회만 발생한다."""
        monkeypatch.setenv("LLM_CALL_WARN_THRESHOLD", "3")
        start_request("req-warn")
        handler = get_handler()

        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            for _ in range(6):  # 임계치 3 → 4번째 호출에서 1회만 경고
                _fire_chat_start(handler)

        warnings = [r for r in caplog.records if "임계치" in r.getMessage()]
        assert len(warnings) == 1
        assert "임계치 3 초과" in warnings[0].getMessage()
        assert "req-warn" in warnings[0].getMessage()
        assert finish_request()["llm_calls"] == 6  # 차단 없음 — 전부 계측

    def test_invalid_threshold_falls_back_to_default(self, monkeypatch, caplog):
        """임계치 env가 int가 아니면 기본 30으로 안전 파싱된다."""
        monkeypatch.setenv("LLM_CALL_WARN_THRESHOLD", "abc")
        start_request("req-default")
        handler = get_handler()
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            for _ in range(30):
                _fire_chat_start(handler)
        assert not [r for r in caplog.records if "임계치" in r.getMessage()]
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            _fire_chat_start(handler)  # 31번째 → 기본 임계치 30 초과
        assert [r for r in caplog.records if "임계치 30 초과" in r.getMessage()]


class TestNoContextSafety:
    def test_events_without_start_request_do_not_raise(self):
        """(c) start_request 없이 이벤트가 발생해도 예외가 없다 (no-op)."""
        handler = get_handler()
        _fire_chat_start(handler)
        handler.on_llm_start({"kwargs": {"model": "m"}}, ["p"], run_id=None)
        record_tool_use("query_data")
        summary = finish_request()
        assert summary == {"request_id": None, "llm_calls": 0, "by_model": {}}


class TestTaskToolWarning:
    def test_task_tool_warns_once_per_request(self, caplog):
        """(d) record_tool_use('task') 경고는 요청당 1회만 발생한다."""
        start_request("req-task")
        with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
            record_tool_use("task")
            record_tool_use("task")
            record_tool_use("query_data")  # 일반 도구는 경고 없음
        warnings = [
            r for r in caplog.records if "task 내장 서브에이전트" in r.getMessage()
        ]
        assert len(warnings) == 1
        finish_request()
