"""plans/104 C-2·C-3·C-5 — 후속 턴 인가 재주입 · CLI 안내 · 소스 지정 연결의 전송 인증.

C-2: 체크포인터는 델타만 병합한다. 후속·승인 턴 델타에 이번 요청 토큰의 `user_role`·
     `allowed_db_ids`를 다시 싣지 않으면 스레드 첫 턴 값이 그대로 남아, 관리자에서 강등된
     사용자가 기존 스레드에서 관리자 전용 동작(채팅 캐시 생성·무효화 — S2)을 계속할 수 있다.
C-3: 그 거절 문구는 터미널 사용자를 위해 `scripts/schema_cache_cli.py`를 안내한다.
C-5: `db_id`로 소스를 바꿔 연결할 때 `bearer_token` 등 나머지 설정이 빠지지 않는다.

LLM·DB·Redis 0 — 전부 목이다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.query import _build_turn_input_state, _with_current_identity
from src.api.schemas import QueryRequest
from src.config import DBHubConfig
from src.nodes.cache_management import _ADMIN_ONLY_MESSAGE, cache_management

DB = "db_a"
ADMIN_USER = {"sub": "u1", "role": "admin", "allowed_db_ids": None, "department": "인프라"}
DEMOTED_USER = {"sub": "u1", "role": "user", "allowed_db_ids": [DB], "department": "인프라"}
#: 첫 턴에 관리자였던 스레드의 체크포인트(강등 전 값이 그대로 남아 있다)
CHECKPOINT = {"user_role": "admin", "allowed_db_ids": None, "thread_id": "t1"}


def _request(**kwargs) -> QueryRequest:
    return QueryRequest(query=kwargs.pop("query", "서버 목록 보여줘"), **kwargs)


# ──────────────────────────────────────────────
# C-2 후속 턴 인가 재주입
# ──────────────────────────────────────────────


def test_followup_turn_delta_carries_current_role_not_thread_role():
    """일반 후속 턴 — 델타가 이번 토큰의 역할·허용 DB를 싣는다(강등 즉시 반영)."""
    delta = _build_turn_input_state(
        _request(), "t1", CHECKPOINT, DEMOTED_USER,
    )
    assert delta["user_role"] == "user"
    assert delta["allowed_db_ids"] == [DB]


def test_approval_turn_delta_carries_current_role():
    """SQL 승인 턴 델타도 같은 규칙 — 승인 대기 중 강등돼도 이번 턴 역할이 적용된다."""
    checkpoint = {**CHECKPOINT, "awaiting_approval": True}
    delta = _build_turn_input_state(
        _request(query="승인"), "t1", checkpoint, DEMOTED_USER, approval=("approve", ""),
    )
    assert delta["approval_action"] == "approve"
    assert delta["user_role"] == "user"
    assert delta["allowed_db_ids"] == [DB]


def test_form_fill_answer_turn_delta_carries_current_role():
    """폼필 답변 턴 델타도 재주입한다(세 후속 경로 대칭)."""
    checkpoint = {
        **CHECKPOINT,
        "pending_form_fill": {
            "uploaded_file": b"xlsx-bytes",
            "file_type": "xlsx",
            "original_query": "양식 채워줘",
            "db_ids": [DB],
        },
    }
    body = _request(query="[양식 미해결 항목 답변]", form_fill_answers={"항목": {"action": "literal", "value": "값"}})
    delta = _build_turn_input_state(body, "t1", checkpoint, DEMOTED_USER)

    assert delta["form_fill_answers"] == {"항목": {"action": "literal", "value": "값"}}
    assert delta["user_role"] == "user"
    assert delta["allowed_db_ids"] == [DB]


def test_first_turn_still_carries_identity():
    """첫 턴(전체 초기화)은 종전대로 인가 정보를 싣는다(회귀 방지)."""
    state = _build_turn_input_state(_request(), "t1", None, ADMIN_USER)
    assert state["user_role"] == "admin"
    assert state["allowed_db_ids"] is None
    assert state["user_id"] == "u1"


def test_identity_helper_overwrites_stale_values():
    """헬퍼는 델타에 남아 있던 옛 값을 덮어쓴다."""
    delta = _with_current_identity(
        {"user_role": "admin", "allowed_db_ids": None}, DEMOTED_USER
    )
    assert delta["user_role"] == "user"
    assert delta["allowed_db_ids"] == [DB]


@pytest.mark.asyncio
async def test_demoted_user_cannot_invalidate_cache_in_existing_thread():
    """강등 사용자가 기존 스레드에서 캐시 무효화를 시도하면 거절된다(C-2 → S2 연결)."""
    delta = _build_turn_input_state(
        _request(query="캐시를 지워줘"), "t1", CHECKPOINT, DEMOTED_USER,
    )
    # 체크포인터 병합을 흉내 낸다 — 복원된 옛 상태 위에 이번 턴 델타를 얹는다.
    state = {**CHECKPOINT, **delta}

    config = MagicMock()
    config.auth.enabled = True
    config.multi_db.get_active_db_ids.return_value = [DB]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(content=json.dumps({"action": "invalidate", "db_id": DB}))
    )
    cache_mgr = MagicMock()
    cache_mgr.invalidate = AsyncMock(return_value=True)
    cache_mgr.invalidate_all = AsyncMock(return_value=0)

    with patch("src.nodes.cache_management.get_cache_manager", return_value=cache_mgr):
        result = await cache_management(state, llm=llm, app_config=config)

    assert result["final_response"] == _ADMIN_ONLY_MESSAGE
    cache_mgr.invalidate.assert_not_called()
    cache_mgr.invalidate_all.assert_not_called()


# ──────────────────────────────────────────────
# C-3 거절 문구의 CLI 안내
# ──────────────────────────────────────────────


def test_admin_only_message_points_to_cli():
    """터미널 사용자가 막다른 길에 서지 않도록 CLI 경로를 알려 준다."""
    assert "scripts/schema_cache_cli.py" in _ADMIN_ONLY_MESSAGE
    assert "/admin" in _ADMIN_ONLY_MESSAGE


# ──────────────────────────────────────────────
# C-5 소스 지정 연결의 전송 인증
# ──────────────────────────────────────────────


def _app_config_with_token(token: str) -> SimpleNamespace:
    return SimpleNamespace(
        db_backend="dbhub",
        dbhub=DBHubConfig(
            server_url="http://mcp.test/sse",
            source_name="default_src",
            mcp_call_timeout=42,
            bearer_token=token,
        ),
        query=SimpleNamespace(max_retry_count=3, default_limit=1000),
        db_connection_string="",
    )


@pytest.mark.asyncio
async def test_source_override_keeps_bearer_token_and_other_fields():
    """`db_id`로 소스를 바꿔도 전송 인증 토큰·타임아웃이 함께 간다(C-5)."""
    from src.db import get_db_client

    captured: dict = {}

    class _Client:
        def __init__(self, dbhub_config, query_config):
            captured["config"] = dbhub_config

        async def connect(self):
            return None

        async def disconnect(self):
            return None

    config = _app_config_with_token("secret-token")
    with patch("src.dbhub.client.DBHubClient", _Client):
        async with get_db_client(config, db_id="other_src"):
            pass

    cfg = captured["config"]
    assert cfg.source_name == "other_src"          # 소스는 바뀌고
    assert cfg.bearer_token == "secret-token"      # 인증 토큰은 유지된다
    assert cfg.mcp_call_timeout == 42
    assert cfg.server_url == "http://mcp.test/sse"


@pytest.mark.asyncio
async def test_registry_client_keeps_bearer_token():
    """레지스트리 경유 연결도 같은 규칙(두 생성 지점 대칭)."""
    from src.routing.db_registry import DBRegistry

    captured: dict = {}

    class _Client:
        def __init__(self, dbhub_config, query_config):
            captured["config"] = dbhub_config

        async def connect(self):
            return None

        async def disconnect(self):
            return None

    config = _app_config_with_token("secret-token")
    config.multi_db = SimpleNamespace(get_active_db_ids=lambda: ["other_src"])
    registry = DBRegistry(config)
    with patch("src.dbhub.client.DBHubClient", _Client):
        async with registry.get_client("other_src"):
            pass

    assert captured["config"].source_name == "other_src"
    assert captured["config"].bearer_token == "secret-token"
