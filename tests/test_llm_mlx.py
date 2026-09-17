"""plans/100: MLX 로컬 서버 provider 단위 테스트 (T-1~T-6).

`mlx_lm.server`에 붙는 설정·팩토리를 서버 없이 검증한다(네트워크 0).
단언은 `ChatOpenAI`(langchain-openai 1.3.2)의 **실제 필드명**으로 한다 —
`base_url`→`openai_api_base` · `model`→`model_name` · `timeout`→`request_timeout`.

테스트 config는 provider를 명시하고 `_env_file=None`으로 만든다 — 로컬 `.env`의
`ORCHESTRATOR_PROVIDER=gemini`가 새어 든 선례가 있다(docs/18_known_mistakes.md).
"""

from __future__ import annotations

import sys

import pytest

from src.config import LLMConfig, OrchestratorConfig
from src.llm import create_llm, create_orchestrator_llm

pytest.importorskip("langchain_openai")


def _mlx_llm_config(**overrides) -> LLMConfig:
    return LLMConfig(_env_file=None, provider="mlx", **overrides)


# ── T-1 설정 ─────────────────────────────────────────────────────────────


def test_t1_provider_mlx_accepted_on_both_planes():
    assert _mlx_llm_config().provider == "mlx"
    assert OrchestratorConfig(_env_file=None, provider="mlx").provider == "mlx"


def test_t1_mlx_field_defaults():
    """기본값은 코드 기본값으로 단언한다(.env 누수 무관)."""
    fields = LLMConfig.model_fields
    assert fields["mlx_base_url"].default == "http://127.0.0.1:8080/v1"
    assert fields["mlx_model"].default == ""
    assert fields["mlx_max_tokens"].default == 4096
    assert fields["mlx_timeout"].default == 600
    assert fields["mlx_enable_thinking"].default is False


def test_t1_existing_defaults_unchanged():
    """mlx를 추가해도 두 평면의 기본 provider는 그대로다(운영 비트 동일)."""
    assert LLMConfig.model_fields["provider"].default == "ollama"
    assert OrchestratorConfig.model_fields["provider"].default == "vllm"


# ── T-2·T-3 워커 팩토리 ──────────────────────────────────────────────────


def test_t2_create_llm_builds_mlx_chat_openai(mock_config):
    from src.clients.mlx_client import MLXChatOpenAI

    mock_config.llm = _mlx_llm_config(
        mlx_base_url="http://127.0.0.1:8080/v1",
        mlx_model="mlx-community/Qwen3.5-9B-OptiQ-4bit",
    )
    llm = create_llm(mock_config)

    assert type(llm) is MLXChatOpenAI
    assert llm.openai_api_base == "http://127.0.0.1:8080/v1"
    assert llm.model_name == "mlx-community/Qwen3.5-9B-OptiQ-4bit"
    assert llm.max_tokens == 4096
    assert llm.temperature == 0.0
    assert llm.request_timeout == 600
    assert llm.extra_body["chat_template_kwargs"]["enable_thinking"] is False
    assert llm._llm_type == "mlx"
    # 서버가 읽는 요청 바디에 실제로 실린다(서버 기본 512 절단 방지 — J-1 ①)
    params = llm._default_params
    assert params["max_completion_tokens"] == 4096
    assert params["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_t3_empty_model_maps_to_default_model(mock_config):
    """빈 모델 ID는 서버 `--model`로 매핑되는 별칭을 보낸다(재적재 회피)."""
    mock_config.llm = _mlx_llm_config(mlx_model="")
    assert create_llm(mock_config).model_name == "default_model"


def test_t4_missing_langchain_openai_raises_install_hint(mock_config, monkeypatch):
    monkeypatch.setitem(sys.modules, "langchain_openai", None)
    monkeypatch.delitem(sys.modules, "src.clients.mlx_client", raising=False)
    mock_config.llm = _mlx_llm_config()

    with pytest.raises(ValueError, match="langchain-openai") as exc:
        create_llm(mock_config)
    assert "deepagents" in str(exc.value)


# ── T-5 구조화 출력 모드 ─────────────────────────────────────────────────


def test_t5_mlx_worker_uses_md_json_mode():
    """MLX 워커는 운영 FabriX와 같은 MD_JSON 모드여야 한다 — 클래스명이 모드를 정한다.

    `ChatOpenAI` 이름이면 TOOLS가 골라지는데, `mlx_lm.server`는 `tool_choice` 강제를 처리하지
    않는다(도입 당시에는 어댑터 결함으로 TOOLS가 늘 실패했다 — J-3, v4 B-3에서 수정).
    서브클래스 이름이 이 선택을 고정하는 유일한 장치다 — 이름을 바꾸면 이 테스트가 잡는다.
    """
    from langchain_openai import ChatOpenAI

    from src.clients.instructor_adapter import MODE_MD_JSON, MODE_TOOLS, select_mode
    from src.clients.mlx_client import MLXChatOpenAI

    kwargs = dict(base_url="http://127.0.0.1:8080/v1", api_key="EMPTY", model="default_model")
    assert select_mode(MLXChatOpenAI(**kwargs)) == MODE_MD_JSON
    assert select_mode(ChatOpenAI(**kwargs)) == MODE_TOOLS


# ── T-6 오케스트레이터 팩토리 ────────────────────────────────────────────


def test_t6_orchestrator_mlx_sets_max_tokens_and_extra_body(mock_config):
    """모델명이 `default_model`이어도 thinking 토글을 붙인다(qwen 이름 가드 우회)."""
    mock_config.llm = _mlx_llm_config(mlx_max_tokens=2048)
    mock_config.orchestrator = OrchestratorConfig(
        _env_file=None, provider="mlx", base_url="http://127.0.0.1:8080/v1", model="default_model"
    )
    llm = create_orchestrator_llm(mock_config)

    # 워커와 같은 서브클래스 — max_tokens 절단 경고(B-4)를 받는다
    assert type(llm).__name__ == "MLXChatOpenAI"
    assert llm.openai_api_base == "http://127.0.0.1:8080/v1"
    assert llm.model_name == "default_model"
    assert llm.max_tokens == 2048
    assert llm.extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


def test_t6_orchestrator_vllm_unchanged(mock_config):
    """회귀 방어 — vllm 경로는 max_tokens 미전송 · 비-qwen이면 extra_body 없음."""
    mock_config.orchestrator = OrchestratorConfig(
        _env_file=None, provider="vllm", base_url="http://vllm:8000/v1", model="gpt-oss-20b"
    )
    llm = create_orchestrator_llm(mock_config)

    from langchain_openai import ChatOpenAI

    assert type(llm) is ChatOpenAI, "vllm 경로는 ChatOpenAI 그대로여야 한다"
    assert llm.max_tokens is None
    assert getattr(llm, "extra_body", None) in (None, {})
    assert "max_completion_tokens" not in llm._default_params


# ── A-1 스트리밍 청크 대기 상한 (2026-09-17 plans/94 실 실행 결함 A) ─────────

from src.clients.mlx_client import HAS_STREAM_CHUNK_TIMEOUT  # noqa: E402 — importorskip 뒤

_needs_chunk_timeout_field = pytest.mark.skipif(
    not HAS_STREAM_CHUNK_TIMEOUT,
    reason="langchain-openai<1.2.0에는 stream_chunk_timeout 필드가 없다",
)


@_needs_chunk_timeout_field
def test_a1_worker_stream_chunk_timeout_follows_mlx_timeout(mock_config):
    """prefill이 120초(라이브러리 기본값)를 넘는 호출이 첫 청크 전에 끊기지 않게 한다."""
    mock_config.llm = _mlx_llm_config(mlx_timeout=900)
    llm = create_llm(mock_config)
    assert llm.request_timeout == 900
    assert llm.stream_chunk_timeout == 900.0


@_needs_chunk_timeout_field
def test_a1_orchestrator_mlx_stream_chunk_timeout_follows_timeout(mock_config):
    mock_config.llm = _mlx_llm_config()
    mock_config.orchestrator = OrchestratorConfig(
        _env_file=None, provider="mlx", base_url="http://127.0.0.1:8080/v1",
        model="default_model", timeout=300,
    )
    llm = create_orchestrator_llm(mock_config)
    assert llm.stream_chunk_timeout == 300.0


def test_a1_orchestrator_vllm_does_not_pass_chunk_timeout(mock_config):
    """회귀 방어 — vllm 경로는 이 인자를 넘기지 않는다(운영 비트 동일)."""
    mock_config.orchestrator = OrchestratorConfig(
        _env_file=None, provider="vllm", base_url="http://vllm:8000/v1", model="gpt-oss-20b"
    )
    llm = create_orchestrator_llm(mock_config)
    assert "stream_chunk_timeout" not in llm.model_fields_set


def test_a1_field_absent_passes_nothing(monkeypatch):
    """langchain-openai 1.1.13(pyproject 하한)에는 필드가 없다 — 넘기면 요청 본문으로 샌다."""
    import src.clients.mlx_client as mlx_client

    monkeypatch.setattr(mlx_client, "HAS_STREAM_CHUNK_TIMEOUT", False)
    assert mlx_client.stream_chunk_timeout_kwargs(900) == {}
    monkeypatch.setattr(mlx_client, "HAS_STREAM_CHUNK_TIMEOUT", True)
    assert mlx_client.stream_chunk_timeout_kwargs(900) == {"stream_chunk_timeout": 900.0}


# ── B-4 finish_reason=length 경고 (네트워크 0 — httpx MockTransport) ──────


def _completion(finish_reason: str) -> dict:
    return {
        "id": "chatcmpl-mlx-1", "object": "chat.completion", "created": 0,
        "model": "mlx-community/Qwen3.5-9B-OptiQ-4bit",
        "choices": [{"index": 0, "finish_reason": finish_reason,
                     "message": {"role": "assistant", "content": "SELECT name,"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 8, "total_tokens": 13},
    }


def _sse(finish_reason: str) -> bytes:
    import json

    def chunk(delta: dict, finish: str | None) -> str:
        body = {"id": "chatcmpl-mlx-2", "object": "chat.completion.chunk", "created": 0,
                "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(body)}\n\n"

    return (chunk({"role": "assistant", "content": "SELECT"}, None)
            + chunk({"content": " name,"}, None)
            + chunk({"role": "assistant"}, finish_reason)
            + "data: [DONE]\n\n").encode()


def _mlx_llm(*, body: dict | None = None, stream: bytes | None = None):
    import httpx

    from src.clients.mlx_client import MLXChatOpenAI

    def handler(request: httpx.Request) -> httpx.Response:
        if stream is not None:
            headers = {"content-type": "text/event-stream"}
            return httpx.Response(200, content=stream, headers=headers)
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    return MLXChatOpenAI(
        base_url="http://127.0.0.1:8080/v1", api_key="EMPTY", model="mlx-model",
        max_completion_tokens=8, max_retries=0,
        http_client=httpx.Client(transport=transport),
        http_async_client=httpx.AsyncClient(transport=transport),
    )


def _truncation_warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records
            if r.name == "src.clients.mlx_client" and "finish_reason=length" in r.getMessage()]


def test_b4_length_finish_reason_warns(caplog):
    """절단 응답은 형태를 바꾸지 않고 WARNING만 남긴다 — 모델·max_tokens·응답 id 포함."""
    llm = _mlx_llm(body=_completion("length"))
    with caplog.at_level("WARNING"):
        msg = llm.invoke("표를 출력하라")

    assert msg.content == "SELECT name,"
    warnings = _truncation_warnings(caplog)
    assert len(warnings) == 1
    assert "mlx-model" in warnings[0] and "max_tokens=8" in warnings[0]
    assert "chatcmpl-mlx-1" in warnings[0]


def test_b4_stop_finish_reason_is_silent(caplog):
    llm = _mlx_llm(body=_completion("stop"))
    with caplog.at_level("WARNING"):
        llm.invoke("표를 출력하라")
    assert _truncation_warnings(caplog) == []


@pytest.mark.asyncio
async def test_b4_streaming_last_chunk_length_warns(caplog):
    """SSE 경로(`astream_text`)도 마지막 청크의 finish_reason으로 경고한다."""
    llm = _mlx_llm(stream=_sse("length"))
    with caplog.at_level("WARNING"):
        parts = [c.content async for c in llm.astream("표를 출력하라")]

    assert "".join(parts) == "SELECT name,"
    warnings = _truncation_warnings(caplog)
    assert len(warnings) == 1 and "chatcmpl-mlx-2" in warnings[0]


@pytest.mark.asyncio
async def test_b4_streaming_stop_is_silent(caplog):
    llm = _mlx_llm(stream=_sse("stop"))
    with caplog.at_level("WARNING"):
        _ = [c async for c in llm.astream("표를 출력하라")]
    assert _truncation_warnings(caplog) == []
