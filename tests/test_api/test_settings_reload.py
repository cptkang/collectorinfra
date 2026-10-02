"""설정 리로드 엔드포인트 검증 (Plan 68 §6 Phase 4).

- 실 `.env`·전역 상태는 건드리지 않는다: `src.config.load_config`를 가짜로 치환하고
  `build_graph`·싱글톤 리셋·`setup_logging`은 카운터 스텁으로 대체한다.
- 외부 네트워크·LLM 호출 0건(D-127).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.admin import reload_settings
from src.config import AdminConfig, AlarmConfig, AppConfig, AuthConfig, LLMConfig

_ADMIN = {"sub": "admin"}


class _CapturingAuditService:
    """감사 기록을 가로채 검증에 사용한다."""

    def __init__(self) -> None:
        self.entries: list = []

    async def log(self, entry) -> None:
        self.entries.append(entry)


class _FakeLoadConfig:
    """lru_cache 인터페이스(cache_clear)를 흉내 내는 load_config 대역."""

    def __init__(self, config) -> None:
        self.config = config
        self.cleared = 0

    def __call__(self):
        if isinstance(self.config, Exception):
            raise self.config
        return self.config

    def cache_clear(self) -> None:
        self.cleared += 1


def _config(**overrides) -> AppConfig:
    """`.env` 격리 config — 비교 대상 필드만 명시한다(known mistakes).

    auth/admin은 운영 게이트(_validate_production_secrets) 판정에 쓰이므로 실 `.env`
    누수를 막기 위해 항상 명시 격리한다(auth.enabled 기본 False → 게이트 미작동).
    """
    overrides.setdefault("auth", AuthConfig(_env_file=None))
    overrides.setdefault("admin", AdminConfig(_env_file=None))
    return AppConfig(_env_file=None, **overrides)


def _request(config, audit_service=None):
    """라우트 직접 호출용 가짜 Request (test_plan59a.py 패턴)."""
    old_graph = SimpleNamespace(checkpointer=None, name="old-graph")
    state = SimpleNamespace(
        config=config,
        graph=old_graph,
        checkpointer=object(),
        audit_service=audit_service,
        audit_repo=None,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="127.0.0.1", request_id="req-1"),
    )


@pytest.fixture
def stubs(monkeypatch):
    """빌드·리셋·로깅을 카운터 스텁으로 치환한다(전역 부작용 차단)."""
    calls = SimpleNamespace(
        build=[], cache_reset=0, embedder_reset=0, history_reset=0,
        logging=[], new_graph=object(),
    )

    def fake_build_graph(config, checkpointer=None):
        calls.build.append((config, checkpointer))
        return calls.new_graph

    monkeypatch.setattr("src.graph.build_graph", fake_build_graph)
    monkeypatch.setattr(
        "src.schema_cache.cache_manager.reset_cache_manager",
        lambda: setattr(calls, "cache_reset", calls.cache_reset + 1),
    )
    monkeypatch.setattr("src.schema_cache.cache_manager._cache_manager", None)
    monkeypatch.setattr("src.schema_cache.query_history._store", None)
    monkeypatch.setattr(
        "src.schema_cache.query_history.reset_query_history_store",
        lambda store=None: setattr(calls, "history_reset", calls.history_reset + 1),
    )
    monkeypatch.setattr(
        "src.schema_cache.synonym_semantic.reset_embedder_state",
        lambda: setattr(calls, "embedder_reset", calls.embedder_reset + 1),
    )
    monkeypatch.setattr(
        "src.security.audit_logger.setup_logging",
        lambda level="INFO": calls.logging.append(level),
    )
    return calls


def _install_fresh(monkeypatch, fresh) -> _FakeLoadConfig:
    fake = _FakeLoadConfig(fresh)
    monkeypatch.setattr("src.config.load_config", fake)
    return fake


async def test_reload_swaps_config_and_graph(monkeypatch, stubs):
    """리로드가 fresh config로 그래프를 재빌드하고 app.state를 교체한다."""
    old = _config(llm=LLMConfig(_env_file=None, model="m-old"))
    fresh = _config(llm=LLMConfig(_env_file=None, model="m-new"))
    fake = _install_fresh(monkeypatch, fresh)
    request = _request(old)
    checkpointer = request.app.state.checkpointer

    response = await reload_settings(request, _ADMIN)

    assert response.reloaded is True
    assert response.graph_rebuilt is True
    assert "LLM_MODEL" in response.changed_keys
    assert fake.cleared == 1
    assert request.app.state.config is fresh
    assert request.app.state.graph is stubs.new_graph
    # 기동 시 체크포인터를 재사용해야 한다(미전달 시 동기 SqliteSaver 신규 생성 — 회귀 금지)
    assert stubs.build == [(fresh, checkpointer)]
    assert stubs.cache_reset == 1
    assert stubs.history_reset == 1
    assert stubs.embedder_reset == 1


async def test_reload_no_change_reports_empty_diff(monkeypatch, stubs):
    """실효값 변경이 없으면 changed_keys가 비고 메시지에 명시된다."""
    old = _config(llm=LLMConfig(_env_file=None, model="same"))
    fresh = _config(llm=LLMConfig(_env_file=None, model="same"))
    _install_fresh(monkeypatch, fresh)
    request = _request(old)

    response = await reload_settings(request, _ADMIN)

    assert response.changed_keys == []
    assert response.restart_only_keys == []
    assert "변경은 없습니다" in response.message


async def test_reload_reports_restart_only_keys(monkeypatch, stubs):
    """기동 캡처 소비 필드(alarm 워커 등)는 restart_only_keys로 정직하게 알린다."""
    old = _config(alarm=AlarmConfig(_env_file=None, enabled=False))
    fresh = _config(alarm=AlarmConfig(_env_file=None, enabled=True))
    _install_fresh(monkeypatch, fresh)
    request = _request(old)

    response = await reload_settings(request, _ADMIN)

    assert "ALARM_ENABLED" in response.changed_keys
    assert "ALARM_ENABLED" in response.restart_only_keys
    assert "재시작" in response.message


async def test_reload_load_failure_keeps_state(monkeypatch, stubs):
    """fresh config 로드 실패 시 400 — 기존 config·그래프를 유지한다."""
    old = _config()
    _install_fresh(monkeypatch, ValueError("broken .env"))
    request = _request(old)
    old_graph = request.app.state.graph

    with pytest.raises(HTTPException) as exc:
        await reload_settings(request, _ADMIN)

    assert exc.value.status_code == 400
    assert request.app.state.config is old
    assert request.app.state.graph is old_graph
    assert stubs.build == []


async def test_reload_build_failure_keeps_state(monkeypatch, stubs):
    """그래프 재빌드 실패 시 500 — 기존 config·그래프를 유지한다(침묵적 강등 금지)."""
    old = _config(llm=LLMConfig(_env_file=None, model="m-old"))
    fresh = _config(llm=LLMConfig(_env_file=None, model="m-new"))
    _install_fresh(monkeypatch, fresh)

    def broken_build(config, checkpointer=None):
        raise RuntimeError("build boom")

    monkeypatch.setattr("src.graph.build_graph", broken_build)
    request = _request(old)
    old_graph = request.app.state.graph

    with pytest.raises(HTTPException) as exc:
        await reload_settings(request, _ADMIN)

    assert exc.value.status_code == 500
    assert request.app.state.config is old
    assert request.app.state.graph is old_graph
    assert stubs.cache_reset == 0
    assert stubs.embedder_reset == 0


async def test_reload_reapplies_log_level_only_when_changed(monkeypatch, stubs):
    """log_level이 바뀐 경우에만 setup_logging을 재호출한다."""
    old = _config(log_level="INFO")
    fresh = _config(log_level="DEBUG")
    _install_fresh(monkeypatch, fresh)

    await reload_settings(_request(old), _ADMIN)
    assert stubs.logging == ["DEBUG"]

    same_old = _config(log_level="INFO")
    same_fresh = _config(log_level="INFO")
    _install_fresh(monkeypatch, same_fresh)
    await reload_settings(_request(same_old), _ADMIN)
    assert stubs.logging == ["DEBUG"]  # 추가 호출 없음


async def test_reload_rejected_by_production_gate(monkeypatch, stubs):
    """운영 게이트(D-071) 불통과 설정은 리로드로도 적용되지 않는다(fail-closed)."""
    old = _config()  # auth.enabled=False
    fresh = _config(auth=AuthConfig(_env_file=None, enabled=True))  # 크레덴셜 미설정
    _install_fresh(monkeypatch, fresh)
    request = _request(old)

    with pytest.raises(HTTPException) as exc:
        await reload_settings(request, _ADMIN)

    assert exc.value.status_code == 400
    assert request.app.state.config is old
    assert stubs.build == []


async def test_reload_preserves_autogenerated_jwt_secrets(monkeypatch, stubs):
    """개발 모드(시크릿 미설정)에서 리로드가 기존 토큰을 무효화하지 않는다(시크릿 승계)."""
    old = _config()
    fresh = _config()
    assert old.admin.jwt_secret != fresh.admin.jwt_secret  # 인스턴스별 랜덤 생성 전제
    _install_fresh(monkeypatch, fresh)
    request = _request(old)

    await reload_settings(request, _ADMIN)

    assert request.app.state.config.admin.jwt_secret == old.admin.jwt_secret
    assert request.app.state.config.auth.jwt_secret == old.auth.jwt_secret


async def test_reload_warns_worker_asymmetry_for_llm_keys(monkeypatch, stubs):
    """알람 워커 활성 시 LLM/DB 키 변경은 워커 미반영 경고를 명시한다(§6.2 비대칭)."""
    old = _config(
        llm=LLMConfig(_env_file=None, model="m-old"),
        alarm=AlarmConfig(_env_file=None, enabled=True),
    )
    fresh = _config(
        llm=LLMConfig(_env_file=None, model="m-new"),
        alarm=AlarmConfig(_env_file=None, enabled=True),
    )
    _install_fresh(monkeypatch, fresh)

    response = await reload_settings(_request(old), _ADMIN)

    assert "알람 워커" in response.message
    assert "LLM_MODEL" in response.message

    # 알람 비활성이면 경고를 붙이지 않는다
    old2 = _config(
        llm=LLMConfig(_env_file=None, model="m-old"),
        alarm=AlarmConfig(_env_file=None, enabled=False),
    )
    fresh2 = _config(
        llm=LLMConfig(_env_file=None, model="m-new"),
        alarm=AlarmConfig(_env_file=None, enabled=False),
    )
    _install_fresh(monkeypatch, fresh2)
    response2 = await reload_settings(_request(old2), _ADMIN)
    assert "알람 워커" not in response2.message


async def test_reload_audit_logged_keys_only(monkeypatch, stubs):
    """감사 로그에 SETTINGS_RELOAD 이벤트가 키 이름만으로 기록된다(값 미노출)."""
    old = _config(llm=LLMConfig(_env_file=None, model="m-old"))
    fresh = _config(llm=LLMConfig(_env_file=None, model="m-new"))
    _install_fresh(monkeypatch, fresh)
    audit = _CapturingAuditService()

    await reload_settings(_request(old, audit_service=audit), _ADMIN)

    assert len(audit.entries) == 1
    entry = audit.entries[0]
    assert entry.event == "settings_reload"
    assert "LLM_MODEL" in entry.extra["changed_keys"]
    assert "m-new" not in str(entry.extra)
