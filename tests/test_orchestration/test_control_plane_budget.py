"""Plan 50 제어 평면 컨텍스트 예산(B6) · Qwen no-think(B7) · 도구 결과 축소(B1/B2) 테스트."""

from __future__ import annotations

import json
import warnings

import pytest

from src.config import OrchestratorConfig
from src.orchestration.deepagents_tools import _max_chars, _serialize_for_tool


# ──────────────────────────────────────────────
# B6 — 예산 노브 기본값
# ──────────────────────────────────────────────

class TestOrchestratorBudgetDefaults:
    def test_default_budget_matches_server_16384(self):
        """서버 max_model_len=16384 기준: 입력 예산 12000 (출력 여유 ~4000)."""
        cfg = OrchestratorConfig(_env_file=None)
        assert cfg.max_input_tokens == 12000
        assert cfg.context_budget_ratio == 0.8
        assert cfg.max_tool_result_tokens == 2000
        assert cfg.max_history_turns == 6

    def test_enable_thinking_default_false(self):
        """Qwen 계열 no-think 기본(false)."""
        cfg = OrchestratorConfig(_env_file=None)
        assert cfg.enable_thinking is False


# ──────────────────────────────────────────────
# B7 — Qwen no-think extra_body 부착
# ──────────────────────────────────────────────

class _Cfg:
    def __init__(self, model, enable_thinking, base_url="http://vllm:8000/v1"):
        self.orchestrator = OrchestratorConfig(
            _env_file=None, model=model, enable_thinking=enable_thinking, base_url=base_url
        )


class TestQwenNoThink:
    def test_qwen_no_think_extra_body(self):
        from src.llm import _create_orchestrator_vllm

        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)  # extra_body 경고 회귀 방지
            llm = _create_orchestrator_vllm(_Cfg("Qwen3.5-9B", False))
        body = getattr(llm, "extra_body", None)
        assert body == {"chat_template_kwargs": {"enable_thinking": False}}

    def test_qwen_thinking_true(self):
        from src.llm import _create_orchestrator_vllm

        llm = _create_orchestrator_vllm(_Cfg("Qwen3.5-9B", True))
        assert llm.extra_body == {"chat_template_kwargs": {"enable_thinking": True}}

    def test_non_qwen_no_extra_body(self):
        """비-Qwen 모델은 extra_body 미부착(미지원 서버 오류 회피)."""
        from src.llm import _create_orchestrator_vllm

        llm = _create_orchestrator_vllm(_Cfg("gpt-oss-20b", False))
        assert getattr(llm, "extra_body", None) in (None, {})


# ──────────────────────────────────────────────
# B1/B2 — 도구 결과 축소 (config 기반 상한)
# ──────────────────────────────────────────────

class TestToolResultBudget:
    def test_max_chars_from_config(self):
        cfg = OrchestratorConfig(_env_file=None)

        class _App:
            orchestrator = cfg

        # 2000 tokens * 4 chars/token
        assert _max_chars(_App()) == 8000

    def test_max_chars_fallback(self):
        from src.orchestration.deepagents_tools import _MAX_RAW_CHARS

        assert _max_chars(None) == _MAX_RAW_CHARS

    def test_large_nonquery_result_truncated(self):
        """조회형이 아닌 대형 결과는 상한 + 축소 표시로 잘린다."""

        class _App:
            orchestrator = OrchestratorConfig(_env_file=None, max_tool_result_tokens=10)

        big = {"blob": "x" * 5000}  # 상한 40chars 초과
        out = _serialize_for_tool(big, _App())
        assert "…(축소됨)" in out
        assert len(out) <= 40 + len("…(축소됨)")

    def test_query_rows_capped(self):
        """조회형 결과 행은 _MAX_TOOL_ROWS로 캡되고 truncated=True."""
        from src.orchestration.deepagents_tools import _MAX_TOOL_ROWS

        rows = [{"i": i} for i in range(_MAX_TOOL_ROWS + 30)]
        result = {"organized_data": {"summary": "s", "rows": rows}}
        out = _serialize_for_tool(result, None)
        payload = json.loads(out)
        assert payload["row_count"] == _MAX_TOOL_ROWS + 30
        assert len(payload["rows"]) == _MAX_TOOL_ROWS
        assert payload["truncated"] is True
