"""plans/116 §10.3 제품 결함 2건 회귀.

1. 「❓ 사용법」 버튼 문장이 LLM 에서 「데이터베이스 직접 쿼리는 불가능」·없는 소스(문서·AWS)를
   지어냈다(D-038 과 반대). 사용법 문의는 활성∩허용 소스와 지원 조회 유형으로 **결정적으로**
   조립해 답한다 — LLM 을 부르지 않는다.
2. 「조회 가능한 DB 목록과 설명」이 「polestar (테이블 0개)」로 나왔다(실제 394개). 파일 캐시에만
   있는 DB 는 `get_all_status` 가 `table_count` 를 채우지 않아 항상 0 이었다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.nodes.cache_management import _handle_db_guide
from src.nodes.general_inference import general_inference
from src.schema_cache.cache_manager import SchemaCacheManager, reset_cache_manager
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.utils.usage_query import is_usage_query

HELP_QUERY = "이 에이전트의 사용법과 현재 지원 가능한 소스, 조회 가능한 데이터를 알려줘"


# ── 1. 사용법 안내 ──────────────────────────────────────────────


def _app_config(active: list[str]) -> MagicMock:
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = active
    return config


def _llm() -> MagicMock:
    llm = MagicMock()
    llm.astream = MagicMock(side_effect=AssertionError("사용법 안내는 LLM 을 부르지 않는다"))
    return llm


@pytest.mark.parametrize(
    "query",
    [
        HELP_QUERY,
        "사용법 알려줘",
        "이 에이전트로 뭘 할 수 있어?",
        "어떤 소스를 지원해? 지원 가능한 소스 알려줘",
    ],
)
def test_usage_query_detected(query: str) -> None:
    assert is_usage_query(query)


@pytest.mark.parametrize(
    "query",
    ["쿠버네티스 사용법 알려줘", "안녕", "RAID 5와 6의 차이는?", "vi 편집기 사용 방법"],
)
def test_non_usage_query_not_detected(query: str) -> None:
    assert not is_usage_query(query)


async def test_help_answer_is_grounded_in_active_sources_without_llm() -> None:
    llm = _llm()
    state = {"user_query": HELP_QUERY, "allowed_db_ids": None, "messages": []}
    out = await general_inference(state, llm=llm, app_config=_app_config(["polestar"]))

    text = out["final_response"]
    assert "로컬 개발 샌드박스 Polestar" in text  # DB_DOMAINS 표시명
    assert "서버 사양" in text and "모니터링 알람" in text  # 지원 조회 유형
    assert "불가능" not in text
    assert "AWS" not in text
    assert out["routing_intent"] == "general_inference"


async def test_help_answer_uses_original_query_on_orchestration_path() -> None:
    """2단은 task sub_query 로 user_query 를 덮는다 — 원문으로도 판정한다."""
    state = {
        "user_query": "에이전트 기능 안내",
        "original_user_query": HELP_QUERY,
        "allowed_db_ids": ["polestar"],
        "messages": [],
    }
    out = await general_inference(state, llm=_llm(), app_config=_app_config(["polestar"]))
    assert "로컬 개발 샌드박스 Polestar" in out["final_response"]


async def test_help_answer_without_accessible_source_says_so() -> None:
    """조회 가능 DB 가 없는 사용자(`[]`)에게는 권한 요청을 안내하고 소스를 광고하지 않는다."""
    state = {"user_query": HELP_QUERY, "allowed_db_ids": [], "messages": []}
    out = await general_inference(state, llm=_llm(), app_config=_app_config(["polestar"]))

    text = out["final_response"]
    assert "조회할 수 있는 DB가 없습니다" in text
    assert "로컬 개발 샌드박스 Polestar" not in text


async def test_help_answer_admin_sees_all_active_sources() -> None:
    """관리자는 허용 목록과 무관하게 전체(D-082 대칭 · db_authz 규약)."""
    state = {
        "user_query": HELP_QUERY, "allowed_db_ids": [], "user_role": "admin", "messages": [],
    }
    out = await general_inference(state, llm=_llm(), app_config=_app_config(["polestar"]))
    assert "로컬 개발 샌드박스 Polestar" in out["final_response"]


async def test_non_usage_query_still_uses_llm() -> None:
    llm = MagicMock()

    async def _astream(*_a, **_k):
        yield MagicMock(content="쿠버네티스는 컨테이너 오케스트레이션 도구입니다.")

    llm.astream = _astream
    state = {"user_query": "쿠버네티스란?", "allowed_db_ids": None, "messages": []}
    out = await general_inference(state, llm=llm, app_config=_app_config(["polestar"]))
    assert "쿠버네티스" in out["final_response"]


# ── 2. DB 목록 테이블 수 ─────────────────────────────────────────


@pytest.fixture
def file_cache_manager(tmp_path):
    reset_cache_manager()
    config = MagicMock()
    config.schema_cache.backend = "file"
    config.schema_cache.cache_dir = str(tmp_path)
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    schema = {"tables": {f"t{i}": {"columns": []} for i in range(3)}, "relationships": []}
    PersistentSchemaCache(cache_dir=str(tmp_path), enabled=True).save(
        "db_a", schema, fingerprint="fp"
    )
    yield mgr
    reset_cache_manager()


def test_file_cache_listing_carries_table_count(tmp_path) -> None:
    cache = PersistentSchemaCache(cache_dir=str(tmp_path), enabled=True)
    cache.save("db_a", {"tables": {"t1": {}, "t2": {}}}, fingerprint="fp")
    assert cache.list_cached_dbs()[0]["table_count"] == 2


async def test_get_all_status_file_only_db_has_table_count(file_cache_manager) -> None:
    statuses = await file_cache_manager.get_all_status()
    assert [(s.db_id, s.backend, s.table_count) for s in statuses] == [("db_a", "file", 3)]


async def test_db_guide_reports_real_table_count(file_cache_manager) -> None:
    file_cache_manager.get_db_descriptions = AsyncMock(return_value={})
    text = await _handle_db_guide(file_cache_manager, ["db_a"])
    assert "db_a (테이블 3개)" in text
    assert "테이블 0개" not in text


# ── 1-b. 2단 플래너 단락 (계층 A ③.8) ─────────────────────────────


async def test_planner_short_circuits_usage_query_to_general_inference(mock_config) -> None:
    """사용법 문의는 LLM 분해 없이 general_inference 단일 task 로 고정한다."""
    from src.orchestration.intent_planner import intent_planner
    from src.state import create_initial_state

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=AssertionError("분해 LLM 을 부르지 않는다"))
    state = create_initial_state(user_query=HELP_QUERY)

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert [t["agent"] for t in result["task_plan"]] == ["general_inference"]
    assert not result["task_plan"][0].get("direct_response")  # 조립은 노드가 한다(권한 반영)


async def test_planner_does_not_short_circuit_concept_usage_query(mock_config) -> None:
    """「쿠버네티스 사용법」은 개념 질문 — 종전대로 LLM 분해."""
    import json

    from src.orchestration.intent_planner import intent_planner
    from src.state import create_initial_state

    content = json.dumps(
        {"tasks": [{"task_id": "t1", "agent": "general_inference",
                    "sub_query": "쿠버네티스 사용법", "depends_on": [], "input_from": [],
                    "order": 1}]},
        ensure_ascii=False,
    )
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    state = create_initial_state(user_query="쿠버네티스 사용법 알려줘")

    await intent_planner(state, llm=llm, app_config=mock_config)
    llm.ainvoke.assert_awaited()


# ── 2-b. DB 목록 인가 필터 ────────────────────────────────────────


def _guide_mgr(db_ids: list[str], descriptions: dict[str, str] | None = None) -> MagicMock:
    from src.schema_cache.cache_manager import CacheStatus

    mgr = MagicMock()
    mgr.get_db_descriptions = AsyncMock(return_value=descriptions or {})
    mgr.get_all_status = AsyncMock(
        return_value=[CacheStatus(db_id=d, table_count=5, backend="file") for d in db_ids]
    )
    return mgr


@pytest.mark.parametrize("descriptions", [None, {"db_a": "A 설명", "db_b": "B 설명"}])
async def test_db_guide_filters_by_authorized_db_ids(descriptions) -> None:
    mgr = _guide_mgr(["db_a", "db_b"], descriptions)
    text = await _handle_db_guide(mgr, ["db_a"])
    assert "db_a" in text and "db_b" not in text


async def test_db_guide_without_permission_hides_db_names() -> None:
    mgr = _guide_mgr(["db_a"], {"db_a": "A 설명"})
    text = await _handle_db_guide(mgr, [])
    assert "db_a" not in text
    assert "조회할 수 있는 DB가 없습니다" in text


async def test_cache_management_db_guide_applies_user_authorization() -> None:
    """노드 진입 — 활성∩허용만, 관리자는 전체(db_authz 규약)."""
    import json
    from unittest.mock import patch

    from src.nodes.cache_management import cache_management

    config = MagicMock()
    config.auth.enabled = True
    config.multi_db.get_active_db_ids.return_value = ["db_a", "db_b"]
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(content=json.dumps({"action": "db-guide"}))
    )
    mgr = _guide_mgr(["db_a", "db_b", "db_inactive"])
    with patch("src.nodes.cache_management.get_cache_manager", return_value=mgr):
        user = await cache_management(
            {"user_query": "조회 가능한 DB 목록", "allowed_db_ids": ["db_b"],
             "user_role": "user"},
            llm=llm, app_config=config,
        )
        admin = await cache_management(
            {"user_query": "조회 가능한 DB 목록", "allowed_db_ids": [],
             "user_role": "admin"},
            llm=llm, app_config=config,
        )
    assert "db_b" in user["final_response"] and "db_a" not in user["final_response"]
    assert "db_a" in admin["final_response"] and "db_b" in admin["final_response"]
    assert "db_inactive" not in admin["final_response"]


# ── 1-c. 3단 semantic_router 단락 (우선순위 3.5) ──────────────────


async def test_router_short_circuits_usage_query_to_general_inference(mock_config) -> None:
    """3단도 사용법 문의는 라우터 LLM 없이 general_inference 로 보낸다(2단 ③.8 대칭)."""
    from src.routing.semantic_router import semantic_router
    from src.state import create_initial_state

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=AssertionError("라우터 LLM 을 부르지 않는다"))
    state = create_initial_state(user_query=HELP_QUERY)

    out = await semantic_router(state, llm=llm, app_config=mock_config)

    assert out["routing_intent"] == "general_inference"
    assert out["target_databases"] == []
    llm.ainvoke.assert_not_called()


async def test_router_mapped_form_upload_wins_over_usage_query(mock_config) -> None:
    """양식 업로드(mapped_db_ids)는 사용법 단락보다 우선 — 2단 ③ > ③.8 과 같은 순서."""
    from src.routing.semantic_router import semantic_router
    from src.state import create_initial_state

    state = create_initial_state(user_query=HELP_QUERY)
    state["mapped_db_ids"] = ["db_a"]
    out = await semantic_router(state, llm=MagicMock(), app_config=mock_config)
    assert out["routing_intent"] == "data_query"
