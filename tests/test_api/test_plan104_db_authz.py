"""plans/104 C-4 · D-232 — 사용자별 DB 조회 인가.

`allowed_db_ids`는 D-026 때 생겼지만 질의 경로가 강제하지 않았다(Plan 41 미구현). 이제
라우터 노드 경계에서 한 번 거른다. 고정하는 계약:

① `None`=전체 허용(기존 사용자·개발 모드) · `[]`=조회 가능 DB 없음 · 목록=교집합
② 관리자 역할은 목록과 무관하게 전체(알림 존 D-082와 같은 기준)
③ 인가된 대상이 0이면 **사유를 담아 종결**한다 — 빈 대상으로 파이프라인을 계속 보내
   "데이터 없음"처럼 보이게 하지 않는다(침묵 강등 금지)
④ DB를 고르지 않는 의도(캐시 관리·유사어·일반 추론·장애 진단·존 역질문)는 그대로 통과
⑤ 신규 가입자의 초기 허용 목록 = `AUTH_DEFAULT_ALLOWED_DB_IDS`(빈 값이면 없음),
   기존 사용자는 불변

LLM·DB·Redis 0 — 전부 목이다.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.query import _build_turn_input_state, apply_selection_authorization
from src.api.schemas import QueryRequest
from src.graph import route_after_semantic_router
from src.orchestration import subagents
from src.routing.db_authz import (
    ACCESS_DENIED_INTENT,
    ACCESS_DENIED_MESSAGE,
    authorized_db_ids,
    authorized_router,
    filter_router_result,
    filter_selected_db_ids,
    is_db_access_denied,
    parse_allowed_db_ids,
)

ACTIVE = ["db_a", "db_b", "db_c"]


def _target(db_id: str) -> dict:
    return {
        "db_id": db_id,
        "relevance_score": 0.9,
        "sub_query_context": "질의",
        "user_specified": False,
        "reason": "테스트",
    }


def _router_result(*db_ids: str, intent: str | None = None) -> dict:
    targets = [_target(d) for d in db_ids]
    out = {
        "target_databases": targets,
        "is_multi_db": len(targets) > 1,
        "active_db_id": targets[0]["db_id"] if targets else None,
        "user_specified_db": None,
        "current_node": "semantic_router",
    }
    if intent:
        out["routing_intent"] = intent
    return out


# ──────────────────────────────────────────────
# ① 세 값의 의미
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("allowed", "role", "expected"),
    [
        (None, "user", ACTIVE),              # 전체 허용(기존 사용자)
        ([], "user", []),                    # 조회 가능 DB 없음
        (["db_b"], "user", ["db_b"]),        # 교집합
        (["db_b", "없는db"], "user", ["db_b"]),
        ([], "admin", ACTIVE),               # ② 관리자 예외
        (["db_b"], "admin", ACTIVE),
        (["db_b"], "ADMIN", ACTIVE),         # 대소문자 무관
    ],
)
def test_authorized_db_ids_semantics(allowed, role, expected):
    assert authorized_db_ids(ACTIVE, allowed, role) == expected


def test_authorized_db_ids_preserves_active_order():
    assert authorized_db_ids(ACTIVE, ["db_c", "db_a"], "user") == ["db_a", "db_c"]


@pytest.mark.parametrize(
    ("allowed", "role", "denied"),
    [(None, "user", False), ([], "user", True), ([], "admin", False), (["db_a"], "user", False)],
)
def test_is_db_access_denied(allowed, role, denied):
    assert is_db_access_denied(allowed, role) is denied


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, []), ("", []), ("db_a", ["db_a"]), (" db_a , db_b ", ["db_a", "db_b"]), (",,", [])],
)
def test_parse_allowed_db_ids(raw, expected):
    """빈 설정은 빈 목록 — 전체 허용(None)과 구분된다."""
    assert parse_allowed_db_ids(raw) == expected


# ──────────────────────────────────────────────
# ③④ 라우터 산출물 필터
# ──────────────────────────────────────────────


def test_filter_keeps_only_authorized_targets():
    out = filter_router_result(_router_result("db_a", "db_b"), ["db_b"], "user")
    assert [t["db_id"] for t in out["target_databases"]] == ["db_b"]
    assert out["active_db_id"] == "db_b"
    assert out["is_multi_db"] is False


def test_filter_passes_through_when_all_allowed():
    result = _router_result("db_a", "db_b")
    assert filter_router_result(result, None, "user") == result
    assert filter_router_result(result, ["db_a"], "admin") == result


def test_filter_denies_when_nothing_authorized():
    out = filter_router_result(_router_result("db_a"), ["db_b"], "user")
    assert out["routing_intent"] == ACCESS_DENIED_INTENT
    assert out["final_response"] == ACCESS_DENIED_MESSAGE
    assert out["target_databases"] == [] and out["active_db_id"] is None


def test_denied_intent_ends_the_turn():
    """거부는 이번 턴의 최종 응답 — 그래프가 END로 보낸다."""
    from langgraph.graph import END

    assert route_after_semantic_router({"routing_intent": ACCESS_DENIED_INTENT}) == END


@pytest.mark.parametrize(
    "intent",
    ["cache_management", "synonym_registration", "general_inference", "fault_diagnosis",
     "zone_clarification"],
)
def test_filter_passes_non_data_intents(intent):
    """DB를 고르지 않는 의도는 인가 필터를 타지 않는다."""
    result = {"routing_intent": intent, "target_databases": [], "current_node": "semantic_router"}
    assert filter_router_result(result, [], "user") == result


def test_filter_clears_user_specified_db_when_unauthorized():
    result = {**_router_result("db_a", "db_b"), "user_specified_db": "db_a"}
    out = filter_router_result(result, ["db_b"], "user")
    assert out["user_specified_db"] == "db_b"


@pytest.mark.asyncio
async def test_authorized_router_applies_filter_on_every_return_path():
    """래퍼는 라우터 본체의 어느 반환 경로든 같은 필터를 건다."""
    inner = AsyncMock(return_value=_router_result("db_a", "db_b"))
    state = {"allowed_db_ids": ["db_b"], "user_role": "user", "user_query": "질의"}

    out = await authorized_router(state, inner=inner)

    inner.assert_awaited_once_with(state)
    assert [t["db_id"] for t in out["target_databases"]] == ["db_b"]


@pytest.mark.asyncio
async def test_authorized_router_denies_user_without_any_db():
    inner = AsyncMock(return_value=_router_result("db_a"))
    out = await authorized_router({"allowed_db_ids": [], "user_role": "user"}, inner=inner)
    assert out["routing_intent"] == ACCESS_DENIED_INTENT


# ──────────────────────────────────────────────
# ⑤ 신규 가입자 초기 허용 목록
# ──────────────────────────────────────────────


def _register_body(user_id: str = "new_user"):
    from src.api.schemas import UserRegisterRequest

    return UserRegisterRequest(
        user_id=user_id, username="새 사용자", password="password123", department="인프라"
    )


async def _register_with(default_allowed: str) -> object:
    """가입 라우트를 목 저장소로 실행하고 저장된 User를 돌려준다."""
    from src.api.routes.user_auth import register

    created: dict = {}

    user_repo = MagicMock()
    user_repo.exists = AsyncMock(return_value=False)
    user_repo.create = AsyncMock(side_effect=lambda user: created.setdefault("user", user))
    config = SimpleNamespace(
        auth=SimpleNamespace(password_min_length=8, default_allowed_db_ids=default_allowed),
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(user_repo=user_repo, config=config)),
        state=SimpleNamespace(client_ip=None, request_id=None),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={},
    )
    with patch("src.api.routes.user_auth._log_audit_event", AsyncMock(return_value=True)):
        await register(request, _register_body())
    return created["user"]


@pytest.mark.asyncio
async def test_new_signup_gets_no_db_when_setting_empty():
    """빈 설정이면 신규 가입자는 조회 가능 DB가 없다(안전 실패 · `None`이 아니다)."""
    user = await _register_with("")
    assert user.allowed_db_ids == []


@pytest.mark.asyncio
async def test_new_signup_gets_configured_dbs():
    user = await _register_with("db_a, db_b")
    assert user.allowed_db_ids == ["db_a", "db_b"]


def test_setting_is_consumed_in_catalog():
    """이 설정은 더 이상 미소비 키가 아니다."""
    from src.api.settings_catalog import UNCONSUMED_KEYS, field_index

    assert "AUTH_DEFAULT_ALLOWED_DB_IDS" not in UNCONSUMED_KEYS
    assert field_index()["AUTH_DEFAULT_ALLOWED_DB_IDS"].consumed is True


# ──────────────────────────────────────────────
# ⑥ 보존 경로·task 고정과의 상호작용(plans/102 W-10 실측 대응)
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zone_preserved_targets_are_also_filtered():
    """존 선택 재개 턴의 **보존 경로**로 다시 실린 대상도 인가 필터를 지난다.

    `_keep_zoneless_targets`(존 미배정 DB 보존 · plans/102 W-10)는 후보를 `active_db_ids`
    기준으로만 고른다. 그 결과가 라우터 반환값(`target_databases`)에 합쳐지므로, 인가를
    **노드 경계**에서 걸면 보존 경로로 비허용 DB가 되살아나지 않는다 — 라우터 본체를 고치지
    않고도 같은 교집합이 적용된다.
    """
    selected = _target("db_a")                       # 사용자가 고른 존
    preserved = _target("db_c")                      # 보존 경로가 덧붙인 존 미배정 DB
    inner = AsyncMock(return_value={
        "target_databases": [selected, preserved],
        "is_multi_db": True,
        "active_db_id": "db_a",
        "user_specified_db": None,
        "current_node": "semantic_router",
    })

    out = await authorized_router(
        {"allowed_db_ids": ["db_a"], "user_role": "user"}, inner=inner
    )

    assert [t["db_id"] for t in out["target_databases"]] == ["db_a"]
    assert out["is_multi_db"] is False


@pytest.mark.asyncio
async def test_zone_preserved_only_targets_are_denied():
    """보존 대상만 남고 그것이 비허용이면 사유를 담아 종결한다(빈 대상 전달 금지)."""
    inner = AsyncMock(return_value=_router_result("db_c"))
    out = await authorized_router({"allowed_db_ids": ["db_a"], "user_role": "user"}, inner=inner)
    assert out["routing_intent"] == ACCESS_DENIED_INTENT
    assert out["target_databases"] == []


# ──────────────────────────────────────────────
# ⑦ 요청 본문의 DB 선택(`selected_db_ids`) — 외부 입력 경계
# ──────────────────────────────────────────────
#
# 라우터 래퍼(`authorized_router`)는 **라우터가 고른 대상**만 거른다. 요청이 직접 지정한
# 선택은 라우터를 우회해 상태에 실리고(`semantic_router` 우선순위 2.5), 순차 러너의 task
# 고정(`run_data_query_pipeline`의 `raw_targets`)이 그 DB로 조회를 확정한다 — 3단 기본
# 경로도 이 부품을 쓴다(docs/21 §7). 그래서 **요청 경계**에서 같은 교집합을 건다.


class TestSelectionFilter:
    """`filter_selected_db_ids` — 순수 판정."""

    def test_partial_drop_keeps_authorized_only(self):
        kept, dropped = filter_selected_db_ids(["db_a", "db_c"], ["db_a"], "user")
        assert (kept, dropped) == (["db_a"], ["db_c"])

    def test_all_unauthorized_returns_empty_with_reason(self):
        kept, dropped = filter_selected_db_ids(["db_b", "db_c"], ["db_a"], "user")
        assert kept == []
        assert dropped == ["db_b", "db_c"]

    def test_no_selection_is_passthrough(self):
        assert filter_selected_db_ids(None, [], "user") == (None, [])
        assert filter_selected_db_ids([], ["db_a"], "user") == (None, [])

    def test_admin_selection_is_unchanged(self):
        assert filter_selected_db_ids(["db_c"], [], "admin") == (["db_c"], [])

    def test_allowed_none_selection_is_unchanged(self):
        """전체 허용(기존 사용자·개발 모드)은 종전 동작 그대로 — 회귀 방지."""
        assert filter_selected_db_ids(["db_a", "db_c"], None, "user") == (["db_a", "db_c"], [])


class TestRequestBoundaryGate:
    """`apply_selection_authorization` — 라우트가 쓰는 경계 판정."""

    def test_all_unauthorized_denies_the_turn(self):
        selected, denied = apply_selection_authorization(
            ["db_c"], {"allowed_db_ids": ["db_a"], "role": "user"}
        )
        assert (selected, denied) == (None, True)

    def test_partial_selection_proceeds_with_authorized_subset(self):
        selected, denied = apply_selection_authorization(
            ["db_a", "db_c"], {"allowed_db_ids": ["db_a"], "role": "user"}
        )
        assert (selected, denied) == (["db_a"], False)

    def test_admin_and_full_allow_are_untouched(self):
        assert apply_selection_authorization(
            ["db_c"], {"allowed_db_ids": [], "role": "admin"}
        ) == (["db_c"], False)
        assert apply_selection_authorization(
            ["db_c"], {"allowed_db_ids": None, "role": "user"}
        ) == (["db_c"], False)

    def test_no_selection_is_not_denied(self):
        assert apply_selection_authorization(None, {"allowed_db_ids": [], "role": "user"}) == (
            None,
            False,
        )


class TestFourEntryPointsShareTheGate:
    """진입이 하나라도 빠지면 우회가 남는다 — 정적으로 전수를 센다(배선 확인)."""

    @pytest.fixture(scope="class")
    def query_py(self) -> str:
        return (
            Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py"
        ).read_text(encoding="utf-8")

    def test_gate_is_applied_at_four_entries(self, query_py):
        """/query · /query/stream · /query/file · /query/file/stream (정의 1 + 호출 4)."""
        assert query_py.count("apply_selection_authorization(") == 5

    def test_every_entry_exposes_the_reason(self, query_py):
        """침묵 강등 금지 — 네 진입 모두 사유 문구를 응답에 싣는다."""
        assert query_py.count("SELECTION_DENIED_MESSAGE") == 5  # import 1 + 응답 4

    def test_gate_precedes_state_assembly(self, query_py):
        """게이트는 상태 조립(`_build_turn_input_state`·`create_initial_state`)보다 앞이다."""
        for entry, builder in (
            ("body.selected_db_ids, _selection_denied", "input_state = _build_turn_input_state("),
            ("selected_list, _selection_denied", "initial_state = create_initial_state("),
        ):
            assert query_py.index(entry) < query_py.index(builder)


class TestRestoredSelection:
    """폼필 답변 턴의 존 복원 — 체크포인트 값도 인가를 지난다."""

    def _checkpoint(self, db_ids: list[str]) -> dict:
        return {
            "user_role": "user",
            "allowed_db_ids": ["db_a"],
            "pending_form_fill": {
                "uploaded_file": b"xlsx",
                "file_type": "xlsx",
                "original_query": "양식 채워줘",
                "db_ids": db_ids,
            },
        }

    def _answer_body(self):
        return QueryRequest(
            query="[양식 미해결 항목 답변]",
            form_fill_answers={"항목": {"action": "literal", "value": "값"}},
        )

    def test_unauthorized_restored_zone_is_dropped(self):
        """권한 회수·타인 스레드 지정으로 비인가 DB가 되살아나지 않는다."""
        delta = _build_turn_input_state(
            self._answer_body(),
            "t1",
            self._checkpoint(["db_c"]),
            {"sub": "u1", "role": "user", "allowed_db_ids": ["db_a"]},
        )
        assert not delta.get("selected_db_ids")

    def test_authorized_restored_zone_survives(self):
        """인가된 복원은 종전대로 유지된다(FIX-26 회귀 방지)."""
        delta = _build_turn_input_state(
            self._answer_body(),
            "t1",
            self._checkpoint(["db_a"]),
            {"sub": "u1", "role": "user", "allowed_db_ids": ["db_a"]},
        )
        assert delta["selected_db_ids"] == ["db_a"]


# ──────────────────────────────────────────────
# ⑧ 순차 러너(3단 기본 경로)까지의 실제 도달 — task 고정이 비인가 DB를 잡지 않는다
# ──────────────────────────────────────────────


def _runner_config():
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ACTIVE
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    return config


async def _pinned_targets_for(selection: list[str], user: dict) -> tuple[list[str], bool]:
    """요청 경계 → 상태 조립 → 격리 입력 → 순차 러너의 task 고정까지 실제로 태운다.

    Returns:
        `(파이프라인에 실린 db_id 목록, classify_dbs가 불렸는지)` — 고정되면 분류는 불리지 않는다.
    """
    body = QueryRequest(query="서버 목록 보여줘", selected_db_ids=list(selection))
    body.selected_db_ids, denied = apply_selection_authorization(body.selected_db_ids, user)
    assert denied is False, "이 헬퍼는 통과 경로만 다룬다"

    state = _build_turn_input_state(body, "t1", None, user)
    # 라우터 산출물은 이미 `authorized_router`를 지난 값이다 — 인가된 선택만 대상이 된다.
    state["target_databases"] = [_target(d) for d in (state.get("selected_db_ids") or [])]

    task = {"task_id": "t1", "agent": "data_query", "sub_query": "서버 목록"}
    isolated = subagents._make_isolated_input(task, state, {})

    captured: dict = {}

    async def _capture(node_state, *args, **kwargs):
        captured["state"] = node_state
        return {}

    classify = AsyncMock(return_value=[_target("db_b")])
    with patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})), \
         patch.object(subagents, "classify_dbs", classify):
        await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=_runner_config()
        )
    return [t["db_id"] for t in captured["state"]["target_databases"]], classify.called


@pytest.mark.asyncio
async def test_unauthorized_selection_never_pins_the_task():
    """비허용 DB를 섞어 보내도 task 고정이 그 DB를 잡지 않는다(우회 차단의 최종 확인)."""
    pinned, _ = await _pinned_targets_for(
        ["db_a", "db_c"], {"sub": "u1", "role": "user", "allowed_db_ids": ["db_a"]}
    )
    assert pinned == ["db_a"]
    assert "db_c" not in pinned


@pytest.mark.asyncio
async def test_authorized_selection_still_pins_the_task():
    """인가된 선택은 종전대로 고정된다 — 분류(LLM)를 부르지 않는다(회귀 방지)."""
    pinned, classified = await _pinned_targets_for(
        ["db_a"], {"sub": "u1", "role": "user", "allowed_db_ids": ["db_a"]}
    )
    assert pinned == ["db_a"]
    assert classified is False


@pytest.mark.asyncio
async def test_admin_selection_still_pins_unlisted_db():
    """관리자는 목록과 무관하게 선택한 DB로 고정된다(D-082 대칭)."""
    pinned, _ = await _pinned_targets_for(
        ["db_c"], {"sub": "admin1", "role": "admin", "allowed_db_ids": []}
    )
    assert pinned == ["db_c"]
