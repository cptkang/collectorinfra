"""plans/213 W3 — Harmony 파싱 오류 재시도 가드 (D-366 ①).

실 LLM 0 — 실 holmes `DefaultLLM` 클래스의 `completion`을 가짜 함수로 바꿔 끼운 뒤 가드를 설치한다.
몽키패치는 프로세스 전역이라 모든 테스트가 `DefaultLLM.completion`을 원복한다.
"""

from __future__ import annotations

import inspect
import logging
import sys
from typing import Any

import litellm
import pytest
from holmes.core.llm import DefaultLLM
from litellm.types.utils import Choices, Message, ModelResponse, Usage
from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.infrastructure import harmony_retry_guard as hr
from sre_agent.infrastructure import llm_message_guard as smg
from sre_agent.infrastructure import tool_call_markup_guard as c8
from sre_agent.infrastructure.harmony_retry_guard import (
    HARMONY_PARSE_ERROR_MARKER,
    install_harmony_retry_guard,
    is_harmony_parse_error,
)
from sre_agent.infrastructure.llm_message_guard import install_system_message_guard
from sre_agent.infrastructure.tool_call_markup_guard import install_tool_call_markup_guard
from sre_agent.settings import AgentSettings

HARMONY_MSG = (
    "OpenAIException - Unexpected token 12 while expecting start token 200006"
)
TOOLS = [{"type": "function", "function": {"name": "t", "parameters": {}}}]
MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]


def _harmony_400() -> Exception:
    return litellm.BadRequestError(HARMONY_MSG, model="m", llm_provider="openai")


def _resp(content: Any = "ok", tool_calls: list | None = None) -> ModelResponse:
    return ModelResponse(
        id="chatcmpl-x",
        model="m",
        choices=[Choices(finish_reason="stop",
                         message=Message(content=content, tool_calls=tool_calls))],
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


class _Base:
    """가짜 원본 `completion` — 정해 둔 결과(예외·응답)를 순서대로 내고 호출 인자를 기록한다."""

    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, llm: Any, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        out = self.outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


class _Clock:
    """주입 시계 — 호출마다 정해 둔 값을 차례로 돌려준다(마지막 값 유지)."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


@pytest.fixture
def clean_completion():
    """가드 없는 holmes 원본으로 시작하고, 끝나면 테스트 전 상태로 되돌린다."""
    saved = DefaultLLM.__dict__["completion"]
    DefaultLLM.completion = inspect.unwrap(saved)  # type: ignore[method-assign]
    yield DefaultLLM
    DefaultLLM.completion = saved  # type: ignore[method-assign]


def _use(cls: Any, base: _Base) -> None:
    def completion(self: Any, *args: Any, **kwargs: Any) -> Any:
        return base(self, *args, **kwargs)

    cls.completion = completion


# ── 판정 ──────────────────────────────────────────────────────────


def test_marker_is_single_literal():
    assert HARMONY_PARSE_ERROR_MARKER == "expecting start token"


@pytest.mark.parametrize("exc, expected", [
    (litellm.BadRequestError(HARMONY_MSG, model="m", llm_provider="openai"), True),
    (litellm.InternalServerError(HARMONY_MSG, llm_provider="openai", model="m"), True),
    (litellm.BadRequestError("context length exceeded", model="m", llm_provider="openai"), False),
    (litellm.ContextWindowExceededError("maximum context length", model="m",
                                        llm_provider="openai"), False),
    (litellm.AuthenticationError(HARMONY_MSG, llm_provider="openai", model="m"), False),
    (RuntimeError(HARMONY_MSG), False),
    (ValueError(HARMONY_MSG), False),
])
def test_is_harmony_parse_error(exc, expected):
    assert is_harmony_parse_error(exc) is expected


def test_name_fallback_without_litellm(monkeypatch):
    """litellm import 실패 시 litellm 모듈의 같은 이름 클래스만 대상(다른 패키지 같은 이름은 제외)."""
    monkeypatch.setitem(sys.modules, "litellm", None)  # import litellm → ImportError
    assert hr._retryable_classes() is None
    assert is_harmony_parse_error(_harmony_400()) is True

    class BadRequestError(Exception):  # 다른 패키지의 같은 이름
        pass

    assert is_harmony_parse_error(BadRequestError(HARMONY_MSG)) is False


# ── 재시도 동작 ────────────────────────────────────────────────────


def test_marker_error_then_success_returns_response(clean_completion, caplog):
    ok = _resp()
    base = _Base([_harmony_400(), ok])
    _use(clean_completion, base)
    assert install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0, 3.0)) is True
    with caplog.at_level(logging.INFO, logger=hr.__name__):
        out = clean_completion.completion(None, MSGS, TOOLS, "auto")
    assert out is ok
    assert len(base.calls) == 2
    msgs = [r.getMessage() for r in caplog.records]
    warn = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warn) == 1
    assert "Harmony 파싱 오류 재시도 1/1 — 첫 시도 경과 3.0s · 호출 종류=조사" in warn[0].getMessage()
    assert any("재시도 결과 — 통과 1/1" in m for m in msgs)


def test_retry_request_args_identical(clean_completion):
    """재시도 호출 인자(위치·키워드)는 첫 호출과 완전히 같다 — temperature 등 불변."""
    base = _Base([_harmony_400(), _resp()])
    _use(clean_completion, base)
    install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0))
    clean_completion.completion(
        None, MSGS, TOOLS, "auto", temperature=1e-08, drop_params=True, stream=False,
    )
    (a1, k1), (a2, k2) = base.calls
    assert a1 == a2 and k1 == k2
    assert a1[0] is a2[0] and k1 == {"temperature": 1e-08, "drop_params": True, "stream": False}


def test_two_consecutive_marker_errors_raise_last(clean_completion, caplog):
    first, second = _harmony_400(), _harmony_400()
    base = _Base([first, second])
    _use(clean_completion, base)
    install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0))
    with caplog.at_level(logging.INFO, logger=hr.__name__), \
            pytest.raises(litellm.BadRequestError) as ei:
        clean_completion.completion(None, MSGS)
    assert ei.value is second
    assert len(base.calls) == 2
    msgs = [r.getMessage() for r in caplog.records]
    assert any("호출 종류=요약" in m for m in msgs)
    assert any("재시도 결과 — 재실패 1/1" in m for m in msgs)


def test_retry_max_bounds_attempts(clean_completion):
    base = _Base([_harmony_400(), _harmony_400(), _harmony_400(), _resp()])
    _use(clean_completion, base)
    install_harmony_retry_guard(2, 45.0, clock=_Clock(0.0))
    with pytest.raises(litellm.BadRequestError):
        clean_completion.completion(None, MSGS)
    assert len(base.calls) == 3  # 첫 시도 + 재시도 2


def test_non_marker_400_raises_immediately(clean_completion):
    err = litellm.BadRequestError("403 forbidden-ish bad request", model="m", llm_provider="openai")
    base = _Base([err, _resp()])
    _use(clean_completion, base)
    install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0))
    with pytest.raises(litellm.BadRequestError) as ei:
        clean_completion.completion(None, MSGS, TOOLS, "auto")
    assert ei.value is err
    assert len(base.calls) == 1


def test_elapsed_over_threshold_skips_retry(clean_completion):
    err = _harmony_400()
    base = _Base([err, _resp()])
    _use(clean_completion, base)
    install_harmony_retry_guard(1, 45.0, clock=_Clock(0.0, 45.5))
    with pytest.raises(litellm.BadRequestError) as ei:
        clean_completion.completion(None, MSGS, TOOLS, "auto")
    assert ei.value is err
    assert len(base.calls) == 1


def test_flag_zero_not_installed(clean_completion):
    base = _Base([_harmony_400(), _resp()])
    _use(clean_completion, base)
    before = clean_completion.completion
    assert install_harmony_retry_guard(0, 45.0) is False
    assert clean_completion.completion is before
    with pytest.raises(litellm.BadRequestError):
        clean_completion.completion(None, MSGS)
    assert len(base.calls) == 1


def test_normal_call_bit_identical(clean_completion, caplog):
    ok = _resp()
    base = _Base([ok])
    _use(clean_completion, base)
    install_harmony_retry_guard(1, 45.0)
    with caplog.at_level(logging.INFO, logger=hr.__name__):
        out = clean_completion.completion(None, MSGS, TOOLS, "auto", temperature=0.0)
    assert out is ok
    assert base.calls == [((MSGS, TOOLS, "auto"), {"temperature": 0.0})]
    assert caplog.records == []


# ── 설치 순서 · 멱등 ────────────────────────────────────────────────


def _layer_flags(fn: Any) -> list[str]:
    """바깥 → 안쪽 래핑 층마다 그 층이 **새로** 더한 가드 표식(functools.wraps가 안쪽 표식을 복사)."""
    flags = (hr._GUARD_FLAG, smg._GUARD_FLAG, c8._GUARD_FLAG)
    layers: list[set[str]] = []
    while hasattr(fn, "__wrapped__"):
        layers.append({f for f in flags if getattr(fn, f, False)})
        fn = fn.__wrapped__
    layers.append(set())
    return [next(iter(outer - inner)) for outer, inner in zip(layers, layers[1:], strict=False)]


def test_diagnosis_agent_installs_innermost_and_idempotent(clean_completion):
    """DiagnosisAgent 설치 순서 고정: C8(D-213(Harmony(원본))) — C8이 재시도 뒤 최종 응답을 본다."""
    seen: list = []
    gemma = "<|tool_call>call:t{a:1}<tool_call|>"

    def completion(self: Any, messages: Any, *args: Any, **kwargs: Any) -> Any:
        seen.append(messages)
        if len(seen) == 1:
            raise _harmony_400()
        return _resp(gemma)

    clean_completion.completion = completion
    s = AgentSettings(_env_file=None, model="m", bash_write_guard_enabled=False)
    DiagnosisAgent(s)
    assert _layer_flags(clean_completion.completion) == [
        c8._GUARD_FLAG, smg._GUARD_FLAG, hr._GUARD_FLAG,
    ]
    # 재설치(두 번째 에이전트 · 직접 호출)는 모두 no-op — 층 수 그대로
    DiagnosisAgent(s)
    assert install_harmony_retry_guard() is False
    assert install_system_message_guard() is False
    assert install_tool_call_markup_guard() is False
    assert len(_layer_flags(clean_completion.completion)) == 3

    compacted = [*MSGS, {"role": "system", "content": "compacted"}]
    out = clean_completion.completion(None, compacted, TOOLS, "auto")
    assert len(seen) == 2
    assert [m["role"] for m in seen[0]] == ["system", "user", "user"]   # D-213: 재시도에도 교정
    assert seen[0] == seen[1]
    tc = out.choices[0].message.tool_calls                              # C8: 재시도 응답을 변환
    assert [t.function.name for t in tc] == ["t"]


def test_diagnosis_agent_flag_zero_skips_install(clean_completion):
    base = clean_completion.completion
    DiagnosisAgent(AgentSettings(
        _env_file=None, model="m", llm_harmony_retry_max=0, system_message_position_fix=False,
        tool_call_markup_parse_enabled=False, bash_write_guard_enabled=False,
    ))
    assert clean_completion.completion is base


def test_install_on_real_holmes_idempotent(clean_completion):
    assert install_harmony_retry_guard() is True
    assert install_harmony_retry_guard() is False
    assert not getattr(inspect.unwrap(clean_completion.completion), hr._GUARD_FLAG, False)


def test_settings_defaults():
    s = AgentSettings(_env_file=None, model="m")
    assert s.llm_harmony_retry_max == 1
    assert s.llm_harmony_retry_skip_after_s == 45.0
