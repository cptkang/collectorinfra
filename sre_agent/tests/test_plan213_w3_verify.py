"""plans/213 W3 독립 검증 — Harmony 재시도 가드 (D-366 ①).

실 LLM 0. 실 holmes `DefaultLLM.completion`(로컬 0.45.0) + 실 litellm 전송 경로를 127.0.0.1 가짜
OpenAI 호환 서버에 붙여, 재시도 요청 본문이 첫 요청과 바이트 동일한지(temperature·max_tokens·
messages 포함) 확인한다. 몽키패치는 프로세스 전역이라 모든 테스트가 원복한다.
"""

from __future__ import annotations

import copy
import inspect
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import litellm
import pytest
from holmes.core.llm import DefaultLLM
from sre_agent.infrastructure import harmony_retry_guard as hr
from sre_agent.infrastructure.harmony_retry_guard import install_harmony_retry_guard

HARMONY_DETAIL = "500: Unexpected token 173781 while expecting start token 200006"


@pytest.fixture
def clean_completion():
    saved = DefaultLLM.__dict__["completion"]
    DefaultLLM.completion = inspect.unwrap(saved)  # type: ignore[method-assign]
    yield DefaultLLM
    DefaultLLM.completion = saved  # type: ignore[method-assign]


class _Server:
    """정해 둔 (status, body) 순서로 답하고 요청 본문을 기록하는 OpenAI 호환 가짜 서버."""

    def __init__(self, replies: list[tuple[int, dict]]) -> None:
        self.replies = list(replies)
        self.bodies: list[bytes] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                n = int(self.headers.get("Content-Length", "0"))
                outer.bodies.append(self.rfile.read(n))
                status, body = outer.replies.pop(0)
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):  # noqa: ANN002
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.t.start()
        return self

    def __exit__(self, *a):  # noqa: ANN002
        self.httpd.shutdown()
        self.httpd.server_close()


def _ok_body(content: str = "ok") -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _llm(port: int) -> DefaultLLM:
    llm = object.__new__(DefaultLLM)  # check_llm(환경 검사) 우회 — completion 경로만 쓴다
    llm.model = "openai/m"
    llm.api_key = "x"
    llm.api_base = f"http://127.0.0.1:{port}/v1"
    llm.api_version = None
    llm.args = {}
    llm.tracer = None
    llm.name = None
    llm.is_robusta_model = False
    llm.max_context_size = 32000
    llm.allowed_openai_params = None
    return llm


MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
TOOLS = [{"type": "function", "function": {"name": "t", "description": "d",
                                           "parameters": {"type": "object", "properties": {}}}}]


@pytest.mark.parametrize("with_tools", [False, True])
def test_real_holmes_retry_request_body_identical(clean_completion, monkeypatch, with_tools):
    """실 holmes 원본 + 실 litellm 전송: 400(표지) 뒤 재시도 본문이 첫 본문과 동일하다."""
    monkeypatch.setattr(DefaultLLM, "get_maximum_output_token", lambda self: 1024)
    assert install_harmony_retry_guard() is True
    msgs = copy.deepcopy(MSGS)
    snapshot = copy.deepcopy(msgs)
    with _Server([(400, {"detail": HARMONY_DETAIL}), (200, _ok_body())]) as srv:
        llm = _llm(srv.port)
        kw: dict[str, Any] = {"temperature": 1e-08, "drop_params": True, "stream": False}
        if with_tools:
            kw.update(tools=TOOLS, tool_choice="auto")
        out = DefaultLLM.completion(llm, msgs, **kw)
    assert out.choices[0].message.content == "ok"
    assert len(srv.bodies) == 2
    b1, b2 = (json.loads(b) for b in srv.bodies)
    assert b1 == b2
    assert b1["temperature"] == 1e-08
    assert ("tools" in b1) is with_tools
    assert msgs == snapshot  # 호출자 messages 무변형


def test_real_litellm_exception_is_marker_classified(clean_completion, monkeypatch):
    """vLLM 400 응답이 litellm에서 BadRequestError로 사상되고 표지 판정에 걸린다(실 전송 경로)."""
    monkeypatch.setattr(DefaultLLM, "get_maximum_output_token", lambda self: 1024)
    with _Server([(400, {"detail": HARMONY_DETAIL})]) as srv:
        with pytest.raises(Exception) as ei:
            DefaultLLM.completion(_llm(srv.port), copy.deepcopy(MSGS), temperature=0)
    assert isinstance(ei.value, litellm.BadRequestError)
    assert hr.is_harmony_parse_error(ei.value)
    assert hr.HARMONY_PARSE_ERROR_MARKER in hr._excerpt(ei.value)  # 200자 안에 표지가 남는다


def test_real_holmes_double_failure_raises_last(clean_completion, monkeypatch):
    monkeypatch.setattr(DefaultLLM, "get_maximum_output_token", lambda self: 1024)
    install_harmony_retry_guard()
    with _Server([(400, {"detail": HARMONY_DETAIL + " #1"}),
                  (400, {"detail": HARMONY_DETAIL + " #2"})]) as srv:
        with pytest.raises(litellm.BadRequestError) as ei:
            DefaultLLM.completion(_llm(srv.port), copy.deepcopy(MSGS), temperature=0)
    assert len(srv.bodies) == 2
    assert "#2" in str(ei.value)


def test_real_holmes_non_marker_400_no_retry(clean_completion, monkeypatch):
    monkeypatch.setattr(DefaultLLM, "get_maximum_output_token", lambda self: 1024)
    install_harmony_retry_guard()
    with _Server([(400, {"detail": "maximum context length exceeded"}),
                  (200, _ok_body())]) as srv:
        with pytest.raises(litellm.BadRequestError):
            DefaultLLM.completion(_llm(srv.port), copy.deepcopy(MSGS), temperature=0)
    assert len(srv.bodies) == 1


def test_retry_reuses_same_objects_no_snapshot(clean_completion):
    """가드는 인자를 복제하지 않는다 — 같은 객체 재전달(첫 시도가 변형하면 변형된 채 재전송).

    실 holmes 0.45.0 원본은 messages를 새 리스트로 정제해 변형하지 않음(위 실측) — 기록용 고정.
    """
    seen: list[int] = []

    def completion(self: Any, messages: Any, **kw: Any) -> Any:
        seen.append(id(messages))
        if len(seen) == 1:
            raise litellm.BadRequestError(HARMONY_DETAIL, model="m", llm_provider="openai")
        return "ok"

    clean_completion.completion = completion
    install_harmony_retry_guard()
    m = list(MSGS)
    assert clean_completion.completion(None, m, temperature=0) == "ok"
    assert seen == [id(m), id(m)]


def test_internal_server_error_marker_retried_and_base_exception_passes(clean_completion):
    calls: list[int] = []

    def completion(self: Any, *a: Any, **kw: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise litellm.InternalServerError(HARMONY_DETAIL, model="m", llm_provider="openai")
        raise KeyboardInterrupt

    clean_completion.completion = completion
    install_harmony_retry_guard()
    with pytest.raises(KeyboardInterrupt):
        clean_completion.completion(None, MSGS)
    assert len(calls) == 2


def test_marker_in_other_exception_types_not_retried(clean_completion):
    for exc in (litellm.APIError(500, HARMONY_DETAIL, llm_provider="openai", model="m"),
                RuntimeError(HARMONY_DETAIL)):
        calls: list[int] = []

        def completion(self: Any, *a: Any, _e: Exception = exc, **kw: Any) -> Any:
            calls.append(1)
            raise _e

        DefaultLLM.completion = inspect.unwrap(DefaultLLM.__dict__["completion"])
        clean_completion.completion = completion
        install_harmony_retry_guard()
        with pytest.raises(type(exc)):
            clean_completion.completion(None, MSGS)
        assert len(calls) == 1


def test_install_failure_logs_warning(monkeypatch, caplog):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):  # noqa: ANN001, ANN002
        if name == "holmes.core.llm":
            raise ImportError("boom")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level("WARNING", logger=hr.__name__):
        assert install_harmony_retry_guard() is False
    assert any("미설치" in r.getMessage() for r in caplog.records)


def test_env_example_matches_defaults():
    from pathlib import Path

    from sre_agent.settings import AgentSettings

    text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
    lines = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in text.splitlines()
             if ln and not ln.startswith("#") and "=" in ln}
    s = AgentSettings(_env_file=None, model="m")
    assert int(lines["LLM_HARMONY_RETRY_MAX"]) == s.llm_harmony_retry_max
    assert float(lines["LLM_HARMONY_RETRY_SKIP_AFTER_S"]) == s.llm_harmony_retry_skip_after_s
    assert "#" not in lines["LLM_HARMONY_RETRY_MAX"] + lines["LLM_HARMONY_RETRY_SKIP_AFTER_S"]
