"""plans/104 S2·S5 — 채팅 캐시 관리의 역할 확인과 DB 설명 출처.

S2: 생성·무효화 동작은 인증이 켜져 있으면 관리자 역할만 수행한다. 3단 노드·2단 서브에이전트·
    1단 deep_agent 도구가 모두 같은 노드 함수를 지나므로 세 진입에서 `user_role`이
    도달하는지 확인한다.
S5: DB 설명 LLM 생성은 출처 `llm`으로 저장하고 수동 설정(`manual`)은 LLM 호출 전에 건너뛴다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.cache_management import (
    _ADMIN_ONLY_ACTIONS,
    _ADMIN_ONLY_MESSAGE,
    _handle_generate_db_description,
    _handle_set_db_description,
    cache_management,
)

DB = "db_a"


def _app_config(*, auth_enabled: bool) -> MagicMock:
    """인증 여부만 명시한 테스트 설정(.env 누수 차단)."""
    config = MagicMock()
    config.auth.enabled = auth_enabled
    config.multi_db.get_active_db_ids.return_value = [DB]
    config.orchestrator.max_tool_result_tokens = 1000
    return config


def _llm_for(action: str, **extra: object) -> MagicMock:
    """캐시 의도 파싱 LLM 목 — 지정한 action JSON을 돌려준다."""
    llm = MagicMock()
    payload = {"action": action, "db_id": DB, **extra}
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=json.dumps(payload)))
    return llm


def _cache_mgr() -> MagicMock:
    """쓰기 호출을 기록하는 캐시 매니저 목."""
    mgr = MagicMock()
    mgr.redis_available = True
    mgr.invalidate = AsyncMock(return_value=True)
    mgr.invalidate_all = AsyncMock(return_value=3)
    mgr.refresh_cache = AsyncMock()
    mgr.get_schema = AsyncMock(return_value={"tables": {"t1": {"columns": []}}})
    mgr.save_descriptions = AsyncMock(return_value=True)
    mgr.save_synonyms = AsyncMock(return_value=True)
    mgr.sync_global_synonyms = AsyncMock(return_value=0)
    mgr.generate_global_synonyms = AsyncMock(return_value={"words": [], "description": ""})
    mgr.get_global_synonyms = AsyncMock(return_value={})
    mgr.find_similar_global_columns = AsyncMock(return_value=[])
    mgr.get_db_description_origin = AsyncMock(return_value=None)
    mgr.save_db_description = AsyncMock(return_value=True)
    mgr.get_all_status = AsyncMock(return_value=[])
    mgr.get_status = AsyncMock()
    mgr.get_synonyms = AsyncMock(return_value={})
    return mgr


_WRITE_METHODS = (
    "invalidate",
    "invalidate_all",
    "refresh_cache",
    "save_descriptions",
    "save_synonyms",
    "sync_global_synonyms",
    "generate_global_synonyms",
    "save_db_description",
)


def _assert_no_writes(mgr: MagicMock) -> None:
    for name in _WRITE_METHODS:
        getattr(mgr, name).assert_not_called()


# ──────────────────────────────────────────────
# S2 — 3단 노드 진입
# ──────────────────────────────────────────────


def test_admin_only_actions_match_contract() -> None:
    """관리자 전용 동작 집합이 계약 §5 목록과 같다(조회·유사어 등록은 포함하지 않는다)."""
    assert set(_ADMIN_ONLY_ACTIONS) == {
        "generate",
        "generate-descriptions",
        "generate-synonyms",
        "generate-global-synonyms",
        "generate-db-description",
        "invalidate",
    }


@pytest.mark.parametrize("action", _ADMIN_ONLY_ACTIONS)
async def test_user_role_refused_for_generate_and_invalidate(action: str) -> None:
    """인증 켜짐 + user 역할이면 생성·무효화 동작을 실행하지 않고 관리자 페이지를 안내한다."""
    mgr = _cache_mgr()
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        result = await cache_management(
            {"user_query": "캐시 작업 요청", "user_role": "user", "user_id": "kim"},
            llm=_llm_for(action, target_column="hostname", target_table="t1"),
            app_config=_app_config(auth_enabled=True),
        )

    assert result["final_response"] == _ADMIN_ONLY_MESSAGE
    assert result["current_node"] == "cache_management"
    _assert_no_writes(mgr)


async def test_missing_role_refused_when_auth_enabled() -> None:
    """인증이 켜져 있는데 역할이 없으면 거절한다(fail-closed)."""
    mgr = _cache_mgr()
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        result = await cache_management(
            {"user_query": "db_a 캐시 삭제해줘"},
            llm=_llm_for("invalidate"),
            app_config=_app_config(auth_enabled=True),
        )

    assert result["final_response"] == _ADMIN_ONLY_MESSAGE
    mgr.invalidate.assert_not_called()


async def test_admin_role_invalidates() -> None:
    """인증 켜짐 + admin 역할이면 무효화를 실행한다."""
    mgr = _cache_mgr()
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        result = await cache_management(
            {"user_query": "db_a 캐시 삭제해줘", "user_role": "admin"},
            llm=_llm_for("invalidate"),
            app_config=_app_config(auth_enabled=True),
        )

    mgr.invalidate.assert_awaited_once_with(DB)
    assert "캐시 삭제 성공" in result["final_response"]


async def test_auth_disabled_invalidates_regardless_of_role() -> None:
    """인증이 꺼져 있으면(개발 모드) 역할과 무관하게 실행한다 — 관리자 API 개발 모드 우회와 같다."""
    mgr = _cache_mgr()
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        await cache_management(
            {"user_query": "db_a 캐시 삭제해줘", "user_role": "user"},
            llm=_llm_for("invalidate"),
            app_config=_app_config(auth_enabled=False),
        )

    mgr.invalidate.assert_awaited_once_with(DB)


@pytest.mark.parametrize("action", ["status", "list-synonyms", "db-guide"])
async def test_non_admin_actions_unchanged_for_user_role(action: str) -> None:
    """조회 계열 동작은 user 역할이어도 종전대로 수행한다."""
    mgr = _cache_mgr()
    mgr.get_db_descriptions = AsyncMock(return_value={DB: "설명"})
    mgr.get_status.return_value = MagicMock(backend="none")
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        result = await cache_management(
            {"user_query": "조회 요청", "user_role": "user"},
            llm=_llm_for(action),
            app_config=_app_config(auth_enabled=True),
        )

    assert result["final_response"] != _ADMIN_ONLY_MESSAGE


async def test_deterministic_synonym_set_path_unchanged_for_user_role() -> None:
    """결정적 선파서 경로(동의어 집합 등록)는 역할 확인 대상이 아니다 — LLM 파싱 전에 처리된다."""
    mgr = _cache_mgr()
    mgr.redis_available = False  # 등록 직전 Redis 안내로 끝나게 해 선파서 도달만 확인
    llm = _llm_for("invalidate")
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        result = await cache_management(
            {"user_query": "vcore, cpu, core은 동의어야. 등록해줘", "user_role": "user"},
            llm=llm,
            app_config=_app_config(auth_enabled=True),
        )

    llm.ainvoke.assert_not_called()
    assert result["final_response"] != _ADMIN_ONLY_MESSAGE
    assert "Redis" in result["final_response"]


# ──────────────────────────────────────────────
# S2 — 2단 서브에이전트 진입(격리 컨텍스트)
# ──────────────────────────────────────────────


def _task() -> dict:
    return {
        "task_id": "t1",
        "agent": "cache_management",
        "sub_query": "db_a 캐시 삭제해줘",
        "depends_on": [],
        "input_from": [],
        "order": 1,
    }


def test_isolated_input_carries_user_role() -> None:
    """2단 격리 컨텍스트가 user_role을 전달한다."""
    from src.orchestration.subagents import _make_isolated_input

    isolated = _make_isolated_input(
        _task(), {"user_query": "q", "user_role": "admin", "resolved_limit": 100}, prior={}
    )
    assert isolated["user_role"] == "admin"


@pytest.mark.parametrize(("role", "expect_invalidate"), [("user", False), ("admin", True)])
async def test_intent_orchestration_path_applies_role(role: str, expect_invalidate: bool) -> None:
    """2단 `_run_agent` → `run_cache_management` 경로에서 역할 확인이 동작한다."""
    from src.orchestration.agent_orchestrator import _run_agent

    mgr = _cache_mgr()
    state = {"user_query": "db_a 캐시 삭제해줘", "user_role": role, "resolved_limit": 100}
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        result = await _run_agent(
            _task(), state, _llm_for("invalidate"), _app_config(auth_enabled=True), prior={}
        )

    if expect_invalidate:
        mgr.invalidate.assert_awaited_once_with(DB)
    else:
        mgr.invalidate.assert_not_called()
        assert result["final_response"] == _ADMIN_ONLY_MESSAGE


# ──────────────────────────────────────────────
# S2 — 1단 deep_agent 도구 진입(ambient 컨텍스트)
# ──────────────────────────────────────────────


def test_deep_agent_ambient_state_carries_user_role() -> None:
    """1단 ambient 추출이 user_role을 포함한다."""
    from src.orchestration.deep_agent import _extract_ambient_state

    assert _extract_ambient_state({"user_role": "user", "user_query": "q"})["user_role"] == "user"


@pytest.mark.parametrize(("role", "expect_invalidate"), [("user", False), ("admin", True)])
async def test_deep_agent_tool_path_applies_role(role: str, expect_invalidate: bool) -> None:
    """1단 `manage_cache` 도구(`_run_subagent_tool`) 경로에서 역할 확인이 동작한다."""
    from src.orchestration.deep_agent import _extract_ambient_state
    from src.orchestration.deepagents_tools import _run_subagent_tool

    mgr = _cache_mgr()
    ambient = _extract_ambient_state({"user_role": role})
    ambient["resolved_limit"] = 100  # load_config 경유 LIMIT 산정을 피한다(.env 누수 차단)
    collector: list = []
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        await _run_subagent_tool(
            "cache_management",
            "db_a 캐시 삭제해줘",
            worker_llm=_llm_for("invalidate"),
            app_config=_app_config(auth_enabled=True),
            ambient_state=ambient,
            collector=collector,
        )

    _, result = collector[0]
    if expect_invalidate:
        mgr.invalidate.assert_awaited_once_with(DB)
    else:
        mgr.invalidate.assert_not_called()
        assert result["final_response"] == _ADMIN_ONLY_MESSAGE


# ──────────────────────────────────────────────
# S5 — DB 설명 출처
# ──────────────────────────────────────────────


async def test_generate_db_description_skips_manual_before_llm() -> None:
    """출처가 manual이면 LLM을 부르지 않고 저장도 하지 않으며 응답에 보존을 밝힌다."""
    mgr = _cache_mgr()
    mgr.get_db_description_origin = AsyncMock(return_value="manual")
    with patch("src.schema_cache.description_generator.DescriptionGenerator") as gen_cls:
        gen_cls.return_value.generate_db_description = AsyncMock(return_value="LLM 설명")
        text = await _handle_generate_db_description(mgr, MagicMock(), DB)

    gen_cls.return_value.generate_db_description.assert_not_called()
    mgr.save_db_description.assert_not_called()
    assert "수동 설정 설명 보존" in text


@pytest.mark.parametrize("origin", [None, "llm"])
async def test_generate_db_description_saves_with_llm_origin(origin: str | None) -> None:
    """출처가 없거나(레거시) llm이면 재생성하고 origin="llm"으로 저장한다."""
    mgr = _cache_mgr()
    mgr.get_db_description_origin = AsyncMock(return_value=origin)
    with patch("src.schema_cache.description_generator.DescriptionGenerator") as gen_cls:
        gen_cls.return_value.generate_db_description = AsyncMock(return_value="LLM 설명")
        text = await _handle_generate_db_description(mgr, MagicMock(), DB)

    mgr.save_db_description.assert_awaited_once_with(DB, "LLM 설명", origin="llm")
    assert f"- {DB}: LLM 설명" in text


async def test_generate_db_description_reports_manual_set_during_generation() -> None:
    """생성 도중 수동 설명이 설정돼 매니저가 저장을 거부하면 보존 사실을 밝힌다(침묵 금지)."""
    mgr = _cache_mgr()
    mgr.get_db_description_origin = AsyncMock(side_effect=[None, "manual"])
    mgr.save_db_description = AsyncMock(return_value=False)
    with patch("src.schema_cache.description_generator.DescriptionGenerator") as gen_cls:
        gen_cls.return_value.generate_db_description = AsyncMock(return_value="LLM 설명")
        text = await _handle_generate_db_description(mgr, MagicMock(), DB)

    assert "저장하지 않음 — 수동 설정 설명 보존" in text
    assert "LLM 설명" not in text


async def test_set_db_description_saves_manual_origin() -> None:
    """수동 설정은 origin="manual"로 저장한다."""
    mgr = _cache_mgr()
    text = await _handle_set_db_description(mgr, DB, "운영자 설명")

    mgr.save_db_description.assert_awaited_once_with(DB, "운영자 설명", origin="manual")
    assert "설정했습니다" in text
