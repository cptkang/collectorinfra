"""plans/212 W4: 용도별 LLM 평면 — 설정 화면 승계 표시와 리로드 반영.

★ 이 파일이 지키는 계약
  ① 비어 있는 역할 항목은 카탈로그 응답 `inherited_hint`에 승계하는 현재 워커 값을 싣는다 —
     URL은 호스트(:포트)만, 키는 「설정됨/미설정」만(값·자격증명 미노출). 값을 적은 항목은 None.
  ② 역할 provider를 바꾸면 나머지 항목 힌트는 그 provider의 워커 필드를 가리킨다(§4.1).
  ③ 리로드(`POST /admin/settings/reload`)가 역할 설정을 바꾼 fresh config로 그래프를 다시 조립하면
     새 그래프의 해당 노드가 새 역할 객체를 받는다(가짜 LLM — 실 LLM·네트워크 0).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
import src.llm as llm_module
from src.api.routes.admin import reload_settings
from src.api.settings_catalog import build_catalog, role_inherited_hints
from src.config import AdminConfig, AppConfig, AuthConfig, LLMConfig
from tests.test_graph_llm_roles import _ROLE_URL, _WORKER_KEY, _config, _injected_llms

_ADMIN = {"sub": "admin"}
_SECRET_URL = "http://user:pw-77e1@10.0.0.5:8000/v1"


def _llm(**values: Any) -> AppConfig:
    """LLM 역할 필드 15개를 명시한 config(`.env` 누수 차단)."""
    roles: dict[str, Any] = {
        f"{role}_{item}": (0 if item == "timeout" else "")
        for role in llm_module.LLM_ROLES
        for item in ("provider", "base_url", "model", "api_key", "timeout")
    }
    base: dict[str, Any] = {
        "provider": "fabrix",
        "fabrix_base_url": _SECRET_URL,
        "fabrix_chat_model": "M1",
        "fabrix_api_key": "fabrix-key-5a1",
        "fabrix_total_timeout": 300,
        "vllm_base_url": "",
        "vllm_model": "",
        "vllm_api_key": "",
    }
    base.update(roles)
    base.update(values)
    return AppConfig(llm=LLMConfig(_env_file=None, **base))


# ── ① · ② 승계 힌트 ────────────────────────────────────────────────────


def test_hints_for_empty_roles_follow_worker() -> None:
    hints = role_inherited_hints(_llm())
    assert hints["LLM_ROUTER_PROVIDER"] == "fabrix"
    assert hints["LLM_ROUTER_BASE_URL"] == "fabrix · 10.0.0.5:8000"
    assert hints["LLM_ROUTER_MODEL"] == "fabrix · M1"
    assert hints["LLM_ROUTER_API_KEY"] == "fabrix · 설정됨"
    assert hints["LLM_ROUTER_TIMEOUT"] == "fabrix · 300초"
    # 역할 3종 × 5항목 모두 승계 표시
    assert len(hints) == 15


def test_filled_items_have_no_hint_and_provider_switch_retargets() -> None:
    hints = role_inherited_hints(_llm(router_model="M2", planner_provider="vllm"))
    assert "LLM_ROUTER_MODEL" not in hints
    assert hints["LLM_ROUTER_BASE_URL"] == "fabrix · 10.0.0.5:8000"
    # provider를 바꾼 역할 — 나머지 항목은 vllm 워커 필드(비어 있음)를 가리킨다
    assert "LLM_PLANNER_PROVIDER" not in hints
    assert hints["LLM_PLANNER_BASE_URL"] == "vllm · 주소 미설정"
    assert hints["LLM_PLANNER_MODEL"] == "vllm · 미설정"
    assert hints["LLM_PLANNER_API_KEY"] == "vllm · 미설정"


def test_hint_marks_fields_without_counterpart() -> None:
    hints = role_inherited_hints(_llm(answer_provider="gemini"))
    assert hints["LLM_ANSWER_BASE_URL"] == "gemini에는 해당 없음"
    assert hints["LLM_ANSWER_TIMEOUT"] == "gemini에는 해당 없음"
    assert role_inherited_hints(_llm(provider="mlx"))["LLM_ROUTER_API_KEY"] == "mlx에는 해당 없음"
    # mlx 모델이 비면 팩토리·기동 로그와 같이 default_model을 보인다(verifier Minor)
    assert role_inherited_hints(_llm(provider="mlx"))["LLM_ROUTER_MODEL"] == "mlx · default_model"


def test_catalog_carries_hint_without_secrets() -> None:
    catalog = build_catalog(
        file_values={"LLM_ROUTER_MODEL": "M2"},
        config=_llm(router_model="M2"),
        os_environ={},
        env_file_path="/tmp/.env",
    )
    items = {item.env_key: item for group in catalog.groups for item in group.settings}
    assert items["LLM_ROUTER_MODEL"].inherited_hint is None
    assert items["LLM_ROUTER_BASE_URL"].inherited_hint == "fabrix · 10.0.0.5:8000"
    assert items["LLM_PLANNER_API_KEY"].inherited_hint == "fabrix · 설정됨"
    assert items["LLM_PLANNER_API_KEY"].is_secret is True
    # 역할 항목 밖에는 힌트가 없다
    assert items["LLM_PROVIDER"].inherited_hint is None
    assert "fabrix-key-5a1" not in catalog.model_dump_json()
    # 힌트는 호스트만 — URL 자격증명을 싣지 않는다(워커 URL 칸 자체의 표시는 이 계획 범위 밖)
    hints = [i.inherited_hint for i in items.values() if i.inherited_hint]
    assert hints and not any("pw-77e1" in h or "user" in h for h in hints)


def test_catalog_without_config_has_no_hints() -> None:
    catalog = build_catalog(file_values={}, config=None, os_environ={}, env_file_path="/tmp/.env")
    assert all(
        item.inherited_hint is None for group in catalog.groups for item in group.settings
    )


# ── ③ 리로드 반영 ──────────────────────────────────────────────────────


class _FakeLLM:
    """생성 인자만 기록하는 가짜 LLM(호출하지 않는다)."""

    def __init__(self, provider: str, base_url: str, model: str) -> None:
        self.provider = provider
        self.base_url = base_url
        self.model = model


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[_FakeLLM]:
    """`_create_for_provider`를 가짜 객체 팩토리로 바꾸고 리로드 부수효과를 막는다."""
    made: list[_FakeLLM] = []

    def _fake(config: AppConfig, provider: str, purpose: str) -> _FakeLLM:
        fields = llm_module._ROLE_WORKER_FIELDS[provider]
        base_url = getattr(config.llm, fields["base_url"]) if fields["base_url"] else ""
        model = getattr(config.llm, fields["model"]) if fields["model"] else ""
        obj = _FakeLLM(provider, base_url, model)
        made.append(obj)
        return obj

    monkeypatch.setattr(llm_module, "_create_for_provider", _fake)
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")
    monkeypatch.setattr(graph_module, "record_ladder_resolution", lambda t, r, **_: None)
    monkeypatch.setattr("src.schema_cache.cache_manager.reset_cache_manager", lambda: None)
    monkeypatch.setattr("src.schema_cache.cache_manager._cache_manager", None)
    monkeypatch.setattr("src.schema_cache.query_history._store", None)
    monkeypatch.setattr(
        "src.schema_cache.query_history.reset_query_history_store", lambda store=None: None,
    )
    monkeypatch.setattr("src.schema_cache.synonym_semantic.reset_embedder_state", lambda: None)
    monkeypatch.setattr("src.security.audit_logger.setup_logging", lambda level="INFO": None)
    return made


class _FakeLoadConfig:
    """lru_cache 인터페이스(cache_clear)를 흉내 내는 load_config 대역."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def __call__(self) -> AppConfig:
        return self.config

    def cache_clear(self) -> None:
        pass


def _isolated(cfg: AppConfig) -> AppConfig:
    """리로드 게이트·캐시 판정 필드를 `.env`와 무관하게 고정한다."""
    cfg.auth = AuthConfig(_env_file=None)
    cfg.admin = AdminConfig(_env_file=None)
    cfg.alarm.enabled = False
    cfg.schema_cache.backend = "file"
    cfg.log_level = "INFO"
    return cfg


def _request(config: AppConfig) -> SimpleNamespace:
    state = SimpleNamespace(
        config=config,
        graph=SimpleNamespace(checkpointer=None),
        checkpointer=InMemorySaver(),
        audit_service=None,
        audit_repo=None,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="127.0.0.1", request_id="req-1"),
    )


@pytest.mark.parametrize(
    ("role", "nodes"),
    [
        ("planner", {"intent_planner", "replanner"}),
        ("answer", {"result_aggregator"}),
    ],
)
async def test_reload_rebuilds_graph_with_new_role_llm(
    monkeypatch: pytest.MonkeyPatch, created: list[_FakeLLM], role: str, nodes: set[str],
) -> None:
    old = _isolated(_config("intent_orchestration", plan_loop=False))
    fresh = _isolated(_config(
        "intent_orchestration", plan_loop=False,
        **{f"{role}_base_url": _ROLE_URL, f"{role}_model": f"{role}-model"},
    ))
    monkeypatch.setattr("src.config.load_config", _FakeLoadConfig(fresh))
    request = _request(old)

    response = await reload_settings(request, _ADMIN)

    assert response.graph_rebuilt is True
    key = f"LLM_{role.upper()}_BASE_URL"
    assert key in response.changed_keys
    assert key not in response.restart_only_keys  # 리로드 반영 키
    worker, role_llm = created  # 리로드 빌드 1회 — 워커 1 + 역할 1
    assert (role_llm.provider, role_llm.base_url, role_llm.model) == (
        "vllm", _ROLE_URL, f"{role}-model",
    )
    injected = _injected_llms(request.app.state.graph)
    assert {n for n, llm in injected.items() if llm is role_llm} == nodes
    assert all(injected[n] is worker for n in set(injected) - nodes)
    assert _WORKER_KEY not in response.message
