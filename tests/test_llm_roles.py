"""plans/212 W1: 용도별 LLM 평면 — 역할 설정·항목 단위 승계·팩토리·기동 로그.

「따로 설정하지 않으면 현재 설정된 LLM을 쓴다」(§4.1)를 서버 없이 검증한다(네트워크 0).
HTTP는 전송 계층에서 가로챈다(OpenAI 호환 = `httpx.HTTPTransport.handle_request` ·
FabriX OpenAI 호환 = `requests.post`) — 실 호출이 나가지 않는다.

테스트 LLM 설정은 `_env_file=None`에 provider·역할 15필드를 명시해 만든다(.env·OS env 누수 방지 —
docs/18_known_mistakes.md). `LLM_PROVIDER` OS 환경변수는 덮지 않는다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from src.config import AppConfig, LLMConfig, OrchestratorConfig
from src.llm import (
    LLM_ROLES,
    create_llm,
    create_role_llm,
    describe_llm_planes,
    resolve_role_config,
)

_ROLE_ITEMS = ("provider", "base_url", "model", "api_key", "timeout")
_EMPTY_ROLES: dict[str, Any] = {
    f"{role}_{item}": (0 if item == "timeout" else "")
    for role in LLM_ROLES
    for item in _ROLE_ITEMS
}

# §4.1 예시의 현재 워커 — fabrix · A · M1 · vllm 필드 비어 있음
_A = "http://fabrix-a:9000/v1"
_B = "http://fabrix-b:9001/v1"
_V = "http://vllm-v:8001/v1"
_FABRIX_KEY = "fake-fabrix-key-7f3a"
_WORKER_FABRIX: dict[str, Any] = {
    "fabrix_base_url": _A,
    "fabrix_chat_model": "M1",
    "fabrix_api_key": _FABRIX_KEY,
    "fabrix_client_key": "",
    "vllm_base_url": "",
    "vllm_model": "",
    "vllm_api_key": "",
}


def _llm(provider: str, **values: Any) -> LLMConfig:
    merged = dict(_EMPTY_ROLES)
    merged.update(values)
    return LLMConfig(_env_file=None, provider=provider, **merged)


def _orch(**values: Any) -> OrchestratorConfig:
    base: dict[str, Any] = {"provider": "vllm", "base_url": "", "model": "Qwen3.5-9B"}
    base.update(values)
    return OrchestratorConfig(_env_file=None, **base)


@pytest.fixture
def openai_classes():
    """(ChatOpenAI, MLXChatOpenAI). langchain-openai(선택 extra) 미설치면 그 테스트만 건너뛴다."""
    module = pytest.importorskip("langchain_openai")
    from src.clients.mlx_client import MLXChatOpenAI

    return module.ChatOpenAI, MLXChatOpenAI


@pytest.fixture
def make_config(mock_config: AppConfig):
    """워커 LLM 설정을 바꿔 끼운 AppConfig 사본(사다리·multi_db 등은 mock_config 그대로)."""

    def _make(provider: str = "fabrix", orch: OrchestratorConfig | None = None, **values: Any):
        return mock_config.model_copy(
            update={"llm": _llm(provider, **values), "orchestrator": orch or _orch()}
        )

    return _make


def _fabrix_config(make_config, **role_values: Any) -> AppConfig:
    return make_config("fabrix", **{**_WORKER_FABRIX, **role_values})


# ── 공통: 가로채기 ──────────────────────────────────────────────────────


@pytest.fixture
def recorded_factories(monkeypatch):
    """`_create_*` 팩토리를 가로채 받은 (provider, config, kwargs)를 남긴다."""
    import src.llm as llm_mod

    calls: list[tuple[str, AppConfig, dict[str, Any]]] = []
    sentinels: dict[str, object] = {}
    for provider in ("ollama", "fabrix", "gemini", "mlx", "vllm"):
        sentinel = object()
        sentinels[provider] = sentinel

        def _fake(config, _p=provider, _s=sentinel, **kwargs):
            calls.append((_p, config, kwargs))
            return _s

        monkeypatch.setattr(llm_mod, f"_create_{provider}", _fake)
    return calls, sentinels


_WORKER_BY_PROVIDER: dict[str, dict[str, Any]] = {
    "ollama": {"ollama_base_url": "http://ollama:11434", "model": "llama3.1:8b"},
    "fabrix": dict(_WORKER_FABRIX),
    "gemini": {"gemini_api_key": "fake-gemini-key", "gemini_model": "gemini-x"},
    "mlx": {"mlx_base_url": "http://127.0.0.1:9/v1", "mlx_model": "mlx-m"},
    "vllm": {"vllm_base_url": _V, "vllm_model": "Qwen3.5-9B"},
}


# ── ① 역할 미설정 = 현행 비트 동일 ─────────────────────────────────────


@pytest.mark.parametrize("provider", sorted(_WORKER_BY_PROVIDER))
@pytest.mark.parametrize("purpose", ["deterministic", "answer"])
def test_no_role_settings_keeps_current_factory_args(
    make_config, recorded_factories, provider, purpose
):
    calls, sentinels = recorded_factories
    cfg = make_config(provider, **_WORKER_BY_PROVIDER[provider])

    llm = create_llm(cfg, purpose=purpose)

    assert llm is sentinels[provider]
    assert len(calls) == 1
    called_provider, called_config, kwargs = calls[0]
    assert called_provider == provider
    assert called_config is cfg  # 사본이 아니라 원본 그대로
    assert kwargs == ({"purpose": purpose} if provider == "fabrix" else {})


@pytest.mark.parametrize("provider", sorted(_WORKER_BY_PROVIDER))
@pytest.mark.parametrize("role", LLM_ROLES)
def test_no_role_settings_resolves_to_worker(make_config, provider, role):
    cfg = make_config(provider, **_WORKER_BY_PROVIDER[provider])
    worker = object()

    assert resolve_role_config(cfg, role) is None
    assert create_role_llm(cfg, role, worker) is worker  # type: ignore[arg-type]


def test_explicit_role_without_settings_takes_worker_path(make_config, recorded_factories):
    calls, sentinels = recorded_factories
    cfg = make_config("vllm", **_WORKER_BY_PROVIDER["vllm"])

    assert create_llm(cfg, role="router") is sentinels["vllm"]
    assert calls[0][1] is cfg


# ── ② §4.1 예시 표 — 항목 단위 승계 ─────────────────────────────────────


@pytest.mark.parametrize(
    ("role_values", "expected"),
    [
        ({"router_model": "M2"}, ("fabrix", _A, "M2")),
        ({"router_base_url": _B}, ("fabrix", _B, "M1")),
        (
            {"router_provider": "vllm", "router_base_url": _V, "router_model": "Q"},
            ("vllm", _V, "Q"),
        ),
    ],
    ids=["model-only", "base_url-only", "provider-swap"],
)
def test_example_table_item_inheritance(make_config, role_values, expected):
    cfg = _fabrix_config(make_config, **role_values)
    provider, base_url, model = expected

    resolved = resolve_role_config(cfg, "router")

    assert resolved is not None
    assert resolved.llm.provider == provider
    prefix = "fabrix" if provider == "fabrix" else "vllm"
    assert getattr(resolved.llm, f"{prefix}_base_url") == base_url
    model_field = "fabrix_chat_model" if provider == "fabrix" else "vllm_model"
    assert getattr(resolved.llm, model_field) == model
    # 원본은 그대로다
    assert cfg.llm.provider == "fabrix"
    assert cfg.llm.fabrix_base_url == _A
    assert cfg.llm.fabrix_chat_model == "M1"
    assert cfg.llm.vllm_base_url == ""


def test_example_table_actual_instances(make_config, openai_classes):
    chat_openai, _ = openai_classes
    from src.clients.fabrix_client import FabriXAPIClient

    model_only = create_llm(_fabrix_config(make_config, router_model="M2"), role="router")
    assert isinstance(model_only, FabriXAPIClient)
    assert (model_only.base_url, model_only.chat_model) == (_A, "M2")
    assert model_only.api_key == _FABRIX_KEY  # 키는 워커 승계

    base_only = create_llm(_fabrix_config(make_config, router_base_url=_B), role="router")
    assert (base_only.base_url, base_only.chat_model) == (_B, "M1")

    swapped = create_llm(
        _fabrix_config(
            make_config, router_provider="vllm", router_base_url=_V, router_model="Q"
        ),
        role="router",
    )
    assert type(swapped) is chat_openai
    assert (swapped.openai_api_base, swapped.model_name) == (_V, "Q")


def test_example_table_provider_only_raises(make_config):
    cfg = _fabrix_config(make_config, router_provider="vllm")

    with pytest.raises(ValueError) as excinfo:
        create_llm(cfg, role="router")

    message = str(excinfo.value)
    assert "LLM_ROUTER_BASE_URL" in message
    assert "LLM_VLLM_BASE_URL" in message
    assert "router" in message


def test_provider_only_uses_filled_worker_fields_of_that_provider(make_config, openai_classes):
    chat_openai, _ = openai_classes
    cfg = _fabrix_config(
        make_config,
        router_provider="vllm",
        vllm_base_url="http://vllm-w:8000/v1",
        vllm_model="Qw",
    )

    llm = create_llm(cfg, role="router")

    assert type(llm) is chat_openai
    assert (llm.openai_api_base, llm.model_name) == ("http://vllm-w:8000/v1", "Qw")


def test_provider_swap_without_model_raises(make_config):
    cfg = _fabrix_config(make_config, router_provider="vllm", router_base_url=_V)

    with pytest.raises(ValueError, match="LLM_ROUTER_MODEL") as excinfo:
        create_llm(cfg, role="router")
    assert "LLM_VLLM_MODEL" in str(excinfo.value)


def test_gemini_role_without_key_raises(make_config):
    cfg = _fabrix_config(make_config, planner_provider="gemini", planner_model="gemini-x")

    with pytest.raises(ValueError, match="LLM_PLANNER_API_KEY") as excinfo:
        create_llm(cfg, role="planner")
    assert "LLM_GEMINI_API_KEY" in str(excinfo.value)


def test_factory_value_error_gets_role_context(make_config):
    # fabrix 키 누락은 기존 팩토리가 낸다 — 역할 문맥을 붙여 다시 던진다
    cfg = make_config("vllm", vllm_base_url=_V, vllm_model="Q",
                      answer_provider="fabrix", answer_base_url=_A, fabrix_api_key="")

    with pytest.raises(ValueError, match="LLM 역할 answer") as excinfo:
        create_llm(cfg, role="answer")
    assert "LLM_FABRIX_API_KEY" in str(excinfo.value)


@pytest.mark.parametrize("role", LLM_ROLES)
def test_each_role_reads_its_own_fields(make_config, role):
    cfg = _fabrix_config(make_config, **{f"{role}_model": f"M-{role}"})

    for other in LLM_ROLES:
        resolved = resolve_role_config(cfg, other)
        if other == role:
            assert resolved is not None
            assert resolved.llm.fabrix_chat_model == f"M-{role}"
        else:
            assert resolved is None


def test_role_timeout_maps_to_provider_timeout_field(make_config, openai_classes):
    cfg = make_config("vllm", vllm_base_url=_V, vllm_model="Q", vllm_timeout=300,
                      router_timeout=45)

    resolved = resolve_role_config(cfg, "router")

    assert resolved is not None
    assert resolved.llm.vllm_timeout == 45
    assert create_llm(cfg, role="router").request_timeout == 45


def test_provider_specific_knobs_stay_worker_values(make_config, openai_classes):
    cfg = make_config(
        "fabrix", **_WORKER_FABRIX,
        vllm_verify_ssl=False, vllm_enable_thinking=True, mlx_max_tokens=1234,
        router_provider="vllm", router_base_url=_V, router_model="Qwen-R",
    )

    resolved = resolve_role_config(cfg, "router")

    assert resolved is not None
    assert resolved.llm.vllm_verify_ssl is False
    assert resolved.llm.vllm_enable_thinking is True
    assert resolved.llm.mlx_max_tokens == 1234
    params = create_llm(cfg, role="router")._default_params
    assert params["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True}}


def test_unmapped_item_is_ignored_with_warning(make_config, caplog):
    cfg = make_config(
        "fabrix", **_WORKER_FABRIX,
        router_provider="mlx", router_base_url="http://127.0.0.1:9/v1",
        router_api_key="role-key-should-not-leak",
    )

    with caplog.at_level(logging.WARNING, logger="src.llm"):
        resolved = resolve_role_config(cfg, "router")

    assert resolved is not None
    assert resolved.llm.provider == "mlx"
    assert "LLM_ROUTER_API_KEY" in caplog.text
    assert "role-key-should-not-leak" not in caplog.text


def test_unknown_role_raises(make_config):
    cfg = _fabrix_config(make_config)

    with pytest.raises(ValueError, match="알 수 없는 LLM 역할"):
        create_llm(cfg, role="judge")
    with pytest.raises(ValueError, match="알 수 없는 LLM 역할"):
        create_role_llm(cfg, "judge", object())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="알 수 없는 LLM 역할"):
        resolve_role_config(cfg, "judge")


# ── purpose="answer" → role="answer" 자동 적용 ────────────────────────


def test_answer_purpose_applies_answer_role(make_config, recorded_factories):
    calls, _ = recorded_factories
    cfg = _fabrix_config(make_config, answer_model="M-answer")

    create_llm(cfg, purpose="answer")
    create_llm(cfg)  # 중간 합성(deterministic)은 워커 그대로

    (_, answer_cfg, answer_kwargs), (_, worker_cfg, worker_kwargs) = calls
    assert answer_cfg is not cfg
    assert answer_cfg.llm.fabrix_chat_model == "M-answer"
    assert answer_kwargs == {"purpose": "answer"}  # 프로파일(D-194)은 그대로 answer
    assert worker_cfg is cfg
    assert worker_kwargs == {"purpose": "deterministic"}


def test_create_role_llm_answer_uses_answer_profile(make_config, recorded_factories):
    calls, sentinels = recorded_factories
    cfg = _fabrix_config(make_config, answer_model="M-answer")

    assert create_role_llm(cfg, "answer", object()) is sentinels["fabrix"]  # type: ignore[arg-type]
    assert calls[0][2] == {"purpose": "answer"}


def test_create_role_llm_builds_new_instance_when_set(make_config, openai_classes):
    chat_openai, _ = openai_classes
    cfg = _fabrix_config(make_config, router_provider="vllm", router_base_url=_V,
                         router_model="Q")
    worker = create_llm(cfg)

    router = create_role_llm(cfg, "router", worker)
    planner = create_role_llm(cfg, "planner", worker)

    assert router is not worker
    assert type(router) is chat_openai
    assert planner is worker


# ── worker_provider_override 승계 ─────────────────────────────────────


def test_provider_override_is_inherited_when_role_provider_unset(make_config, openai_classes):
    chat_openai, _ = openai_classes
    cfg = make_config("fabrix", **{**_WORKER_FABRIX, "vllm_base_url": _V, "vllm_model": "Q0"},
                      router_model="Q2")

    resolved = resolve_role_config(cfg, "router", provider_override="vllm")

    assert resolved is not None
    assert resolved.llm.provider == "vllm"
    assert (resolved.llm.vllm_base_url, resolved.llm.vllm_model) == (_V, "Q2")
    llm = create_llm(cfg, role="router", provider_override="vllm")
    assert type(llm) is chat_openai
    assert llm.model_name == "Q2"


def test_role_provider_beats_provider_override(make_config, openai_classes):
    _, mlx_chat_openai = openai_classes
    cfg = make_config("fabrix", **_WORKER_FABRIX, mlx_base_url="http://127.0.0.1:9/v1",
                      router_provider="mlx")

    resolved = resolve_role_config(cfg, "router", provider_override="vllm")

    assert resolved is not None
    assert resolved.llm.provider == "mlx"
    llm = create_llm(cfg, role="router", provider_override="vllm")
    assert isinstance(llm, mlx_chat_openai)


# ── D-169 구조화 출력 모드 ──────────────────────────────────────────────


def test_structured_mode_follows_role_provider(make_config, openai_classes):
    from src.clients.instructor_adapter import MODE_MD_JSON, MODE_TOOLS, select_mode

    # select_mode는 클래스명만 본다 — instructor 설치 여부와 무관하게 제품이 쓰는 판정 그대로다
    vllm_role = create_llm(
        _fabrix_config(make_config, router_provider="vllm", router_base_url=_V,
                       router_model="Q"),
        role="router",
    )
    mlx_role = create_llm(
        _fabrix_config(make_config, router_provider="mlx",
                       router_base_url="http://127.0.0.1:9/v1"),
        role="router",
    )

    assert select_mode(vllm_role) == MODE_TOOLS
    assert select_mode(mlx_role) == MODE_MD_JSON


# ── ③ 역할별 요청이 자기 base_url·model로 간다 ────────────────────────


def _completion(model: str) -> dict[str, Any]:
    return {
        "id": "cmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "ok"},
             "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture
def captured_httpx(monkeypatch):
    """OpenAI SDK가 쓰는 httpx 동기 전송을 가로챈다(실 네트워크 0)."""
    import httpx

    requests_seen: list[httpx.Request] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        body = json.loads(request.content or b"{}")
        return httpx.Response(200, json=_completion(body.get("model", "")), request=request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _handle)
    return requests_seen


@pytest.mark.parametrize(
    ("role_values", "url", "model"),
    [
        (
            {"router_provider": "vllm", "router_base_url": "http://vllm-r:8101/v1",
             "router_model": "Q-router"},
            "http://vllm-r:8101/v1/chat/completions", "Q-router",
        ),
        (
            {"router_provider": "mlx", "router_base_url": "http://127.0.0.1:8181/v1",
             "router_model": "mlx-router"},
            "http://127.0.0.1:8181/v1/chat/completions", "mlx-router",
        ),
    ],
    ids=["vllm", "mlx"],
)
def test_openai_compatible_role_request_goes_to_role_endpoint(
    make_config, captured_httpx, openai_classes, role_values, url, model
):
    llm = create_llm(_fabrix_config(make_config, **role_values), role="router")

    assert llm.invoke("hi").content == "ok"

    assert len(captured_httpx) == 1
    request = captured_httpx[0]
    assert str(request.url) == url
    assert json.loads(request.content)["model"] == model


def test_fabrix_role_request_goes_to_role_endpoint(make_config, monkeypatch):
    import src.clients.fabrix_client as fabrix_client

    seen: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    class _Resp:
        text = ""

        def __init__(self, payload: dict[str, Any]):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return self._payload

    def _post(url, json=None, headers=None, timeout=None):  # noqa: A002 — requests 시그니처
        seen.append((url, json, headers))
        return _Resp(_completion(json["model"]))

    monkeypatch.setattr(fabrix_client.requests, "post", _post)
    cfg = _fabrix_config(make_config, planner_base_url=_B, planner_model="M-plan")

    planner = create_llm(cfg, role="planner")
    worker = create_llm(cfg)
    assert planner.invoke("hi").content == "ok"
    assert worker.invoke("hi").content == "ok"

    (p_url, p_body, p_headers), (w_url, w_body, _) = seen
    assert (p_url, p_body["model"]) == (f"{_B}/chat/completions", "M-plan")
    assert (w_url, w_body["model"]) == (f"{_A}/chat/completions", "M1")
    assert p_headers["Authorization"] == f"Bearer {_FABRIX_KEY}"  # 키 승계


# ── ③-2 리로드 승계 — 워커가 바뀌면 미설정 항목이 따라간다 ──────────────


def test_reresolve_follows_changed_worker(make_config):
    before = _fabrix_config(make_config, router_model="M2")
    after = make_config(
        "fabrix", **{**_WORKER_FABRIX, "fabrix_base_url": "http://fabrix-new:9100/v1"},
        router_model="M2",
    )

    first = resolve_role_config(before, "router")
    second = resolve_role_config(after, "router")

    assert first is not None and second is not None
    assert first.llm.fabrix_base_url == _A
    assert second.llm.fabrix_base_url == "http://fabrix-new:9100/v1"
    assert second.llm.fabrix_chat_model == "M2"
    # 미설정 역할은 바뀐 워커 그대로
    assert resolve_role_config(after, "planner") is None


def test_worker_provider_change_moves_unset_role_items(make_config):
    cfg_fabrix = _fabrix_config(make_config, router_timeout=33)
    cfg_vllm = make_config("vllm", vllm_base_url=_V, vllm_model="Q", router_timeout=33)

    first = resolve_role_config(cfg_fabrix, "router")
    second = resolve_role_config(cfg_vllm, "router")

    assert first is not None and second is not None
    assert (first.llm.provider, first.llm.fabrix_total_timeout) == ("fabrix", 33)
    assert (second.llm.provider, second.llm.vllm_timeout) == ("vllm", 33)


# ── 기동 로그 1줄 ───────────────────────────────────────────────────────


def test_describe_llm_planes_line(make_config):
    cfg = make_config(
        "fabrix", **_WORKER_FABRIX,
        orch=_orch(base_url="http://orch-host:8443/v1", model="Qwen-O"),
        router_provider="vllm", router_base_url=_V, router_model="Q",
        answer_model="M2",
    )

    line = describe_llm_planes(cfg)

    assert line == (
        "LLM 평면: worker=fabrix(M1@fabrix-a:9000) "
        "router=vllm(Q@vllm-v:8001 · provider·base_url·model 지정) "
        "planner=워커 승계 "
        "answer=fabrix(M2@fabrix-a:9000 · model 지정) "
        "orchestrator=vllm(Qwen-O@orch-host:8443)"
    )


def test_describe_llm_planes_all_inherited_and_orchestrator_unset(make_config):
    cfg = make_config("vllm", vllm_base_url=_V, vllm_model="Q")

    assert describe_llm_planes(cfg) == (
        "LLM 평면: worker=vllm(Q@vllm-v:8001) router=워커 승계 planner=워커 승계 "
        "answer=워커 승계 orchestrator=미설정"
    )


def test_describe_llm_planes_override_and_gemini(make_config):
    cfg = make_config(
        "fabrix", **{**_WORKER_FABRIX, "gemini_model": "gemini-x"},
        orch=_orch(provider="gemini", model="gemini-o"),
        planner_provider="mlx", planner_api_key="ignored-key",
        mlx_base_url="http://127.0.0.1:8080/v1", mlx_model="",
    )

    line = describe_llm_planes(cfg, provider_override="gemini")

    assert line.startswith("LLM 평면: worker=gemini(gemini-x) ")
    assert "planner=mlx(default_model@127.0.0.1:8080 · provider 지정 · api_key 무시)" in line
    assert line.endswith("orchestrator=gemini(gemini-o)")


# ── ④ 로그·메시지에 키가 없다 ───────────────────────────────────────────


def test_no_secret_in_logs_messages_or_plane_line(make_config, caplog, openai_classes):
    role_key = "sk-role-secret-91c2"
    worker_key = "sk-worker-secret-4d7e"
    url_password = "pw-in-url-55aa"
    cfg = make_config(
        "vllm",
        vllm_base_url=f"http://user:{url_password}@vllm-w:8000/v1",
        vllm_model="Q",
        vllm_api_key=worker_key,
        gemini_api_key="",
        router_provider="vllm",
        router_base_url=f"http://user:{url_password}@vllm-r:8001/v1",
        router_model="Q-r",
        router_api_key=role_key,
        planner_provider="gemini",
        planner_api_key="",
        answer_provider="mlx",
        answer_api_key=role_key,
        mlx_base_url="http://127.0.0.1:9/v1",
    )

    with caplog.at_level(logging.DEBUG):
        router = create_llm(cfg, role="router")
        resolve_role_config(cfg, "answer")
        line = describe_llm_planes(cfg)
        with pytest.raises(ValueError) as excinfo:
            create_llm(cfg, role="planner")

    assert router.openai_api_key.get_secret_value() == role_key  # 실제로는 실린다
    texts = [caplog.text, line, str(excinfo.value)]
    for text in texts:
        assert role_key not in text
        assert worker_key not in text
    assert url_password not in line  # 기동 로그 1줄은 호스트만
    assert "vllm-r:8001" in line
