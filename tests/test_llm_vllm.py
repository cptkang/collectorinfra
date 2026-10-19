"""D-340: 워커 vLLM provider 단위 테스트.

사내 vLLM(OpenAI 호환 `/v1`)에 붙는 워커 설정·팩토리를 서버 없이 검증한다(네트워크 0).
단언은 `ChatOpenAI`의 실제 필드명으로 한다 — `base_url`→`openai_api_base` ·
`model`→`model_name` · `timeout`→`request_timeout`.

테스트 config는 provider를 명시하고 `_env_file=None`으로 만든다(.env 누수 방지 —
docs/18_known_mistakes.md).
"""

from __future__ import annotations

import pytest

from src.config import LLMConfig
from src.llm import create_llm

pytest.importorskip("langchain_openai")

from langchain_openai import ChatOpenAI  # noqa: E402 — importorskip 뒤

from src.clients.mlx_client import HAS_STREAM_CHUNK_TIMEOUT  # noqa: E402


def _vllm_llm_config(**overrides) -> LLMConfig:
    values = {"vllm_base_url": "http://vllm:8000/v1", "vllm_model": "Qwen3.5-9B"}
    values.update(overrides)
    return LLMConfig(_env_file=None, provider="vllm", **values)


# ── 설정 ─────────────────────────────────────────────────────────────────


def test_provider_vllm_accepted_and_defaults():
    assert _vllm_llm_config().provider == "vllm"
    fields = LLMConfig.model_fields
    assert fields["vllm_base_url"].default == ""
    assert fields["vllm_model"].default == ""
    assert fields["vllm_api_key"].default == ""
    assert fields["vllm_timeout"].default == 300
    assert fields["vllm_verify_ssl"].default is True
    assert fields["vllm_enable_thinking"].default is False
    # 기본 provider는 그대로다(운영 비트 동일)
    assert fields["provider"].default == "ollama"


# ── 팩토리 ───────────────────────────────────────────────────────────────


def test_create_llm_builds_plain_chat_openai(mock_config):
    mock_config.llm = _vllm_llm_config(vllm_timeout=240)
    llm = create_llm(mock_config)

    # 서브클래스가 아니라 ChatOpenAI 그대로 — 구조화 출력이 TOOLS 모드로 간다(D-169)
    assert type(llm) is ChatOpenAI
    assert llm.openai_api_base == "http://vllm:8000/v1"
    assert llm.model_name == "Qwen3.5-9B"
    assert llm.temperature == 0.0
    assert llm.request_timeout == 240
    assert llm.max_retries == 0
    assert llm.openai_api_key.get_secret_value() == "EMPTY"
    # mlx와 달리 max_tokens를 보내지 않는다(vLLM은 max_model_len까지 생성한다)
    assert llm.max_tokens is None
    assert "max_completion_tokens" not in llm._default_params


def test_worker_vllm_uses_tools_mode(mock_config):
    from src.clients.instructor_adapter import MODE_TOOLS, select_mode

    mock_config.llm = _vllm_llm_config()
    assert select_mode(create_llm(mock_config)) == MODE_TOOLS


def test_qwen_model_sends_thinking_toggle(mock_config):
    mock_config.llm = _vllm_llm_config(vllm_model="Qwen3.5-9B", vllm_enable_thinking=True)
    params = create_llm(mock_config)._default_params
    assert params["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_non_qwen_model_sends_no_extra_body(mock_config):
    """비-Qwen 템플릿에는 모르는 chat_template_kwargs를 보내지 않는다.

    오케스트레이터 vLLM 경로와 같은 Qwen 이름 가드다.
    """
    mock_config.llm = _vllm_llm_config(vllm_model="gpt-oss-20b")
    llm = create_llm(mock_config)
    assert getattr(llm, "extra_body", None) in (None, {})
    assert "extra_body" not in llm._default_params


def test_api_key_passed_when_set(mock_config):
    mock_config.llm = _vllm_llm_config(vllm_api_key="secret-token")
    assert create_llm(mock_config).openai_api_key.get_secret_value() == "secret-token"


def test_verify_ssl_false_injects_unverified_clients(mock_config):
    mock_config.llm = _vllm_llm_config(vllm_verify_ssl=False)
    llm = create_llm(mock_config)
    assert llm.http_client is not None
    assert llm.http_async_client is not None


def test_verify_ssl_default_keeps_library_clients(mock_config):
    mock_config.llm = _vllm_llm_config()
    llm = create_llm(mock_config)
    assert llm.http_client is None
    assert llm.http_async_client is None


@pytest.mark.skipif(
    not HAS_STREAM_CHUNK_TIMEOUT,
    reason="langchain-openai<1.2.0에는 stream_chunk_timeout 필드가 없다",
)
def test_stream_chunk_timeout_follows_vllm_timeout(mock_config):
    mock_config.llm = _vllm_llm_config(vllm_timeout=420)
    assert create_llm(mock_config).stream_chunk_timeout == 420.0


@pytest.mark.parametrize("missing", ["vllm_base_url", "vllm_model"])
def test_missing_required_setting_raises(mock_config, missing):
    """비어 있으면 엉뚱한 모델명(LLM_MODEL)으로 조용히 부르지 않고 기동에서 실패한다."""
    mock_config.llm = _vllm_llm_config(**{missing: ""})
    with pytest.raises(ValueError, match="LLM_VLLM_BASE_URL"):
        create_llm(mock_config)


def test_request_body_carries_model_and_thinking(mock_config, monkeypatch):
    """팩토리가 만든 LLM의 실제 요청 본문에 모델 ID와 no-think 토글이 실린다(네트워크 0).

    verify_ssl=false 경로가 주입하는 httpx 클라이언트를 MockTransport로 바꿔 요청을 가로챈다.
    """
    import json

    import httpx

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "id": "cmpl-1", "object": "chat.completion", "created": 0, "model": "Qwen3.5-9B",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "SELECT 1"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    class _MockClient(httpx.Client):
        def __init__(self, **_kw):
            super().__init__(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", _MockClient)
    mock_config.llm = _vllm_llm_config(vllm_verify_ssl=False)
    llm = create_llm(mock_config)
    monkeypatch.undo()

    assert llm.invoke("hi").content == "SELECT 1"
    assert captured["url"] == "http://vllm:8000/v1/chat/completions"
    assert captured["body"]["model"] == "Qwen3.5-9B"
    assert captured["body"]["temperature"] == 0.0
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "max_tokens" not in captured["body"]
    assert "max_completion_tokens" not in captured["body"]


# ── 소비처 ───────────────────────────────────────────────────────────────


def test_db_structure_provider_info_reports_vllm_model(mock_config):
    from src.schema_cache.db_structure_service import DBStructureService

    mock_config.llm = _vllm_llm_config(vllm_model="Qwen3.5-9B")
    svc = DBStructureService.__new__(DBStructureService)
    svc._config = mock_config
    assert svc.provider_info() == {"provider": "vllm", "model": "Qwen3.5-9B"}


def test_worker_vllm_is_internal_plane():
    """사내 vLLM 워커는 비과금 평면이다 — 오케스트레이터 vllm과 같은 취급(D-340)."""
    from scripts.scenario.preflight import external_planes

    assert external_planes("vllm", "vllm") == []
    assert external_planes("vllm", "gemini") == ["ORCHESTRATOR_PROVIDER=gemini"]
