"""존 역질문 게이트·selected_db_ids 결정적 고정 테스트 (Plan 75 §4).

- 게이트(_zone_clarification_or_none): 결정적 발동/비발동 경계 고정
- intent_planner pre-check: selected_db_ids → LLM 분해 우회, task.db_ids 고정
- semantic_router: selected_db_ids → LLM 라우팅 스킵, targets 고정
전부 mock — LLM·네트워크 미사용.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api.routes.query import _zone_clarification_or_none
from src.api.schemas import QueryRequest

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _config(active=_ACTIVE):
    return SimpleNamespace(multi_db=SimpleNamespace(get_active_db_ids=lambda: active))


class TestZoneClarificationGate:
    """결정적 발동 조건 (§4.2) — 과잉 역질문 방지 경계 포함."""

    def test_placeholder_always_triggers(self):
        """'ㅇㅇ존' 리터럴(버튼 프리필 무수정 전송)은 후속 턴이어도 발동."""
        body = QueryRequest(query="ㅇㅇ존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘")
        clar = _zone_clarification_or_none(body, {"some": "state"}, _config())
        assert clar is not None
        assert clar["kind"] == "zone_select"
        assert [o["db_id"] for o in clar["options"]] == _ACTIVE
        assert clar["original_query"] == body.query
        assert clar["multi"] is True

    def test_zoneless_mass_query_first_turn_triggers(self):
        body = QueryRequest(query="모든 서버들의 OS 종류를 확인해줘")
        assert _zone_clarification_or_none(body, None, _config()) is not None

    def test_per_server_listing_triggers(self):
        """'서버별 …' — "모든" 없는 전량 나열 표면어도 발동 (2026-07-24 실측 질의 고정:
        존 미지정인데 침묵으로 3개 존 전부에 SQL이 나감)."""
        body = QueryRequest(
            query="2026년 6월 서버별 CPU 사용률과 메모리 사용률 평균을 CPU 사용률이 높은 순으로 함께 보여줘"
        )
        clar = _zone_clarification_or_none(body, None, _config())
        assert clar is not None
        assert clar["kind"] == "zone_select"

    def test_zone_named_query_passes(self):
        """존 표면어가 해소되면 비발동 — D-065 결정적 보강이 처리."""
        body = QueryRequest(query="은행존의 모든 서버들에 대해 OS 종류를 확인")
        assert _zone_clarification_or_none(body, None, _config()) is None

    def test_followup_turn_passes(self):
        """후속 턴은 직전 존 승계 우선(§4.2 비발동)."""
        body = QueryRequest(query="모든 서버의 OS 종류를 확인해줘")
        assert _zone_clarification_or_none(body, {"prev": True}, _config()) is None

    def test_non_mass_query_passes(self):
        """대량 조회 의도 아님 — 과잉 역질문 방지."""
        for q in ("서버 abc01 CPU 사용률", "모든 알람 이력 조회"):
            assert _zone_clarification_or_none(QueryRequest(query=q), None, _config()) is None

    def test_selected_resume_turn_passes(self):
        """선택 재개 턴(selected_db_ids 동봉)은 게이트 통과."""
        body = QueryRequest(
            query="ㅇㅇ존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘",
            selected_db_ids=["polestar_b0"],
        )
        assert _zone_clarification_or_none(body, None, _config()) is None

    def test_inactive_dbs_excluded_from_options(self):
        body = QueryRequest(query="ㅇㅇ존 모든 서버 조회")
        clar = _zone_clarification_or_none(body, None, _config(active=["polestar_b0"]))
        assert [o["db_id"] for o in clar["options"]] == ["polestar_b0"]


class TestFileZoneClarification:
    """파일(폼필) 경로 존 역질문 (Plan 75 §4 확장, 2026-07-24 실측 요구).

    폼필은 항상 존 단위 대량 조회 — 위치어 미해소면 "모든" 표면어 없이도 발동.
    """

    def test_no_location_triggers_with_has_file(self):
        from src.api.routes.query import _file_zone_clarification_or_none

        clar = _file_zone_clarification_or_none(
            "사용률은 지난달 1개월 통계를 사용하시오", None, _config()
        )
        assert clar is not None
        assert clar["has_file"] is True  # 프론트가 보관 파일과 함께 재전송하는 신호
        assert [o["db_id"] for o in clar["options"]] == _ACTIVE

    def test_location_named_passes(self):
        from src.api.routes.query import _file_zone_clarification_or_none

        assert _file_zone_clarification_or_none(
            "은행존 서버 사양으로 양식을 채워줘", None, _config()
        ) is None

    def test_placeholder_triggers_even_with_other_terms(self):
        from src.api.routes.query import _file_zone_clarification_or_none

        clar = _file_zone_clarification_or_none("ㅇㅇ존 양식 채우기", None, _config())
        assert clar is not None

    def test_selected_resume_passes(self):
        from src.api.routes.query import _file_zone_clarification_or_none

        assert _file_zone_clarification_or_none(
            "사용률은 지난달 통계", ["polestar_b0"], _config()
        ) is None

    def test_selected_db_ids_form_csv_parse(self):
        from src.api.routes.query import _parse_selected_db_ids_form

        assert _parse_selected_db_ids_form("polestar_b0, polestar_cm_gp,,") == [
            "polestar_b0", "polestar_cm_gp",
        ]
        assert _parse_selected_db_ids_form("") is None
        assert _parse_selected_db_ids_form(None) is None

    def test_form_fill_limit_defaults_to_full(self):
        """폼필 기본 LIMIT 상향(전량 채움) — 명시 건수는 여전히 우선(2026-07-24 실측:
        지시문에 '모든'이 없으면 1,000행 절단되던 문제)."""
        from src.api.routes.query import _FORM_FILL_DEFAULT_LIMIT
        from src.utils.query_gen_common import resolve_query_limit

        assert resolve_query_limit(
            "사용률은 지난달 1개월 통계를 사용하시오", _FORM_FILL_DEFAULT_LIMIT
        ) == _FORM_FILL_DEFAULT_LIMIT  # 전량 상향값(D-134 하향 후 10,000)과 동일
        assert resolve_query_limit("상위 100건만 채워줘", _FORM_FILL_DEFAULT_LIMIT) == 100


class TestZonePlaceholderSubstitution:
    """'ㅇㅇ존' 플레이스홀더 라벨 치환 (2026-07-24 폐쇄망 실측 수정).

    치환 없이는 라우팅이 정상이어도 sub_query·처리 현황·응답 서술에 'ㅇㅇ존'이 잔존.
    """

    def test_placeholder_replaced_with_labels(self):
        from src.api.routes.query import _substitute_zone_placeholder

        q = "ㅇㅇ존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘"
        out = _substitute_zone_placeholder(q, ["polestar_b0", "polestar_cm_gp"])
        assert "ㅇㅇ존" not in out
        assert out.startswith("은행존, 공동존 김포의 모든 서버들")

    def test_no_selection_unchanged(self):
        from src.api.routes.query import _substitute_zone_placeholder

        q = "ㅇㅇ존의 모든 서버 조회"
        assert _substitute_zone_placeholder(q, None) == q
        assert _substitute_zone_placeholder(q, []) == q

    def test_no_placeholder_unchanged(self):
        """존 미지정('모든 서버') 역질문 재개 턴 — 치환 대상 없으면 원문 유지."""
        from src.api.routes.query import _substitute_zone_placeholder

        q = "모든 서버들의 OS 종류를 확인해줘"
        assert _substitute_zone_placeholder(q, ["polestar_b0"]) == q

    def test_unknown_db_id_falls_back_to_id(self):
        from src.api.routes.query import _substitute_zone_placeholder

        out = _substitute_zone_placeholder("ㅇㅇ존 서버", ["unknown_db"])
        assert out == "unknown_db 서버"


class TestSelectedDbIdsFixing:
    """selected_db_ids → LLM 재해석 없는 결정적 라우팅 고정 (mapped_db_ids 선례 동형)."""

    async def test_intent_planner_precheck_skips_llm(self):
        from src.orchestration.intent_planner import intent_planner

        llm = MagicMock()  # 호출되면 안 됨 — pre-check가 LLM 분해를 우회
        state = {
            "user_query": "ㅇㅇ존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘",
            "selected_db_ids": ["polestar_b0", "polestar_cm_gp"],
        }
        result = await intent_planner(state, llm=llm, app_config=MagicMock())
        tasks = result["task_plan"]
        assert len(tasks) == 1
        assert tasks[0]["agent"] == "data_query"
        assert tasks[0]["db_ids"] == ["polestar_b0", "polestar_cm_gp"]
        assert tasks[0]["sub_query"] == state["user_query"]  # 자연어 재조합 없음
        llm.assert_not_called()

    async def test_semantic_router_fixes_targets(self):
        from src.routing.semantic_router import semantic_router

        llm = MagicMock()
        state = {
            "user_query": "모든 서버 OS 조회",
            "selected_db_ids": ["polestar_cm_gp", "polestar_cm_yd"],
        }
        out = await semantic_router(state, llm=llm, app_config=_config())
        ids = [t["db_id"] for t in out["target_databases"]]
        assert ids == ["polestar_cm_gp", "polestar_cm_yd"]
        assert out["is_multi_db"] is True
        assert out["routing_intent"] == "data_query"
        # 정제 질의 대신 원문 유지 — sub_query_context 압축 탈락(D-066 후속7) 미발생
        assert out["target_databases"][0]["sub_query_context"] == state["user_query"]
        llm.assert_not_called()

    async def test_semantic_router_filters_inactive(self):
        from src.routing.semantic_router import semantic_router

        state = {"user_query": "q", "selected_db_ids": ["polestar_b0", "unknown_db"]}
        out = await semantic_router(state, llm=MagicMock(), app_config=_config())
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_b0"]
        assert out["is_multi_db"] is False
        assert out["user_specified_db"] == "polestar_b0"

    def test_isolated_input_propagates_selection(self):
        from src.orchestration.subagents import _make_isolated_input

        state = {
            "user_query": "모든 서버 조회",
            "parsed_requirements": {},
            "selected_db_ids": ["polestar_b0"],
        }
        task = {"task_id": "t1", "agent": "data_query", "sub_query": "서버 조회"}
        isolated = _make_isolated_input(task, state, prior={})
        assert isolated["selected_db_ids"] == ["polestar_b0"]

    def test_isolated_input_promotes_realtime_intent(self):
        """Plan 71: 실시간 의도는 원문 기준으로 승격 — sub_query 재작성과 무관."""
        from src.orchestration.subagents import _make_isolated_input

        state = {
            "user_query": "은행존의 모든 서버들에 대해 실시간 CPU 사용률을 조회해줘",
            "parsed_requirements": {},
        }
        task = {"task_id": "t1", "agent": "data_query", "sub_query": "CPU 사용률 조회"}
        isolated = _make_isolated_input(task, state, prior={})
        assert isolated["realtime_usage_intent"] is True

        state2 = {"user_query": "은행존 서버 CPU 사용률 현황", "parsed_requirements": {}}
        isolated2 = _make_isolated_input(task, state2, prior={})
        assert isolated2["realtime_usage_intent"] is False  # B안 — "현황" 단독 비트리거
