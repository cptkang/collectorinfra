"""폼필 요청-스코프 상태의 턴 간 누수 차단 회귀 테스트 (D-064).

시나리오(2026-07-09 버그): 폼업로드 턴(은행 b0) 다음의 텍스트 질의(공동존)가
- input_parser에서 옛 uploaded_file 재파싱 → template_structure 부활
- intent_planner에서 옛 mapped_db_ids(=b0)로 DB 고정
되던 문제.

수정: 텍스트 후속 턴은 create_followup_input이 폼필 트리거(uploaded_file 등)를 비우고,
field_mapper 스킵 경로가 매핑 산출물(mapped_db_ids 등)을 비운다.
"""

import pytest

from langchain_core.messages import HumanMessage

from src.nodes.field_mapper import _CLEARED_MAPPING_FIELDS, field_mapper
from src.state import create_followup_input


class TestCreateFollowupInput:
    """텍스트 후속 턴 델타 입력이 폼필 트리거를 비우는지 검증."""

    def test_clears_form_fill_triggers(self):
        """uploaded_file/file_type/csv_sheet_data를 None으로 비운다."""
        delta = create_followup_input("공동존 전체 서버 메트릭 조회")
        assert delta["uploaded_file"] is None
        assert delta["file_type"] is None
        assert delta["csv_sheet_data"] is None

    def test_carries_query_and_message(self):
        """질의 텍스트와 HumanMessage는 그대로 전달한다."""
        delta = create_followup_input("공동존 전체 서버 메트릭 조회")
        assert delta["user_query"] == "공동존 전체 서버 메트릭 조회"
        assert len(delta["messages"]) == 1
        assert isinstance(delta["messages"][0], HumanMessage)
        assert delta["messages"][0].content == "공동존 전체 서버 메트릭 조회"

    def test_does_not_touch_session_carriers(self):
        """세션 승계 신호(conversation_context/pending_synonym_*)는 델타에 없음
        (=체크포인트 값 보존). 델타에 해당 키가 없어야 병합 시 유지된다."""
        delta = create_followup_input("질의")
        assert "conversation_context" not in delta
        assert "pending_synonym_registrations" not in delta
        assert "pending_synonym_reuse" not in delta


class TestTurnInputStateSingleSource:
    """텍스트 라우트(/query·/query/stream)의 입력 상태 조립 단일 출처 검증 (D-064 후속).

    버그(2026-07-16): D-064의 create_followup_input이 /query(비스트리밍)에만 적용되고
    SSE(/query/stream) 후속 턴은 {user_query, messages}만 보내, 직전 폼업로드 턴의
    uploaded_file이 체크포인터로 복원 → 파일 없는 텍스트 질의(알람 조회)가 옛 양식을
    재파싱해 옛 폼필 조회를 재실행함. 수정: _build_turn_input_state 단일 헬퍼를 두
    라우트가 공유.
    """

    def _body(self, query="은행과 공동존 여의도 서버 최근 1개월 알람 리스트"):
        from src.api.schemas import QueryRequest
        return QueryRequest(query=query, thread_id="t-1")

    def test_followup_turn_clears_form_triggers(self):
        """후속 턴(승인 아님)은 create_followup_input과 동일하게 폼필 트리거를 비운다."""
        from src.api.routes.query import _build_turn_input_state

        state = _build_turn_input_state(
            self._body(), "t-1", checkpoint_state={"user_query": "이전 질의"}, current_user={}
        )
        assert state["uploaded_file"] is None
        assert state["file_type"] is None
        assert state["csv_sheet_data"] is None
        assert state["user_query"].startswith("은행과 공동존 여의도")

    def test_approval_turn_passes_action(self):
        """승인 대기 턴은 approval delta를 전달한다(폼필 상태 초기화 없음 — 같은 질의 계속)."""
        from src.api.routes.query import _build_turn_input_state

        state = _build_turn_input_state(
            self._body("승인"), "t-1",
            checkpoint_state={"awaiting_approval": True}, current_user={},
        )
        assert "approval_action" in state
        assert "uploaded_file" not in state

    def test_first_turn_uses_initial_state(self):
        """체크포인트 없으면 create_initial_state 전체 초기화."""
        from src.api.routes.query import _build_turn_input_state

        state = _build_turn_input_state(
            self._body(), "t-1", checkpoint_state=None,
            current_user={"sub": "u1", "department": "d1", "allowed_db_ids": None},
        )
        assert state["thread_id"] == "t-1"
        assert state["user_id"] == "u1"
        assert state["uploaded_file"] is None

    def test_both_text_routes_share_the_helper(self):
        """/query와 /query/stream 핸들러가 모두 단일 헬퍼를 호출한다(비대칭 재발 방지).

        인라인 재구현으로 회귀하면 이 테스트가 잡는다. 헬퍼를 대체하는 리팩터링 시
        두 라우트가 동일 조립을 공유하는지 확인 후 이 단언을 갱신할 것.
        """
        import inspect
        from src.api.routes import query as query_routes

        for handler in (query_routes.process_query, query_routes.process_query_stream):
            src = inspect.getsource(handler)
            assert "_build_turn_input_state" in src, (
                f"{handler.__name__} 가 입력 상태 조립 단일 헬퍼를 사용하지 않음"
            )


class TestFieldMapperSkipClearsMapping:
    """template이 없으면 field_mapper가 잔존 매핑 산출물을 비우는지 검증."""

    async def test_skip_clears_stale_mapped_db_ids(self):
        """template_structure 없음 + 잔존 mapped_db_ids → 매핑 필드 전부 None."""
        state = {
            "template_structure": None,
            # 직전 폼업로드 턴에서 잔존한 값(은행 b0)
            "mapped_db_ids": ["polestar_b0"],
            "column_mapping": {"서버명": "cmm_resource.name"},
            "db_column_mapping": {"polestar_b0": {"서버명": "cmm_resource.name"}},
            "mapping_sources": {"서버명": "synonym"},
            "parsed_requirements": {},
        }
        result = await field_mapper(state)

        assert result["current_node"] == "field_mapper"
        for key in _CLEARED_MAPPING_FIELDS:
            assert result[key] is None, f"{key} 가 비워지지 않음"

    async def test_skip_preserves_pending_synonym_registrations(self):
        """멀티턴 유사어 등록 신호는 스킵 정리 대상이 아니다(반환 dict에 없음 → 보존)."""
        state = {
            "template_structure": None,
            "mapped_db_ids": ["polestar_b0"],
            "parsed_requirements": {},
        }
        result = await field_mapper(state)
        assert "pending_synonym_registrations" not in result


class TestIntentPlannerNoShortCircuitAfterReset:
    """mapped_db_ids가 비워지면 intent_planner의 DB 고정 단축이 발동하지 않음을 검증."""

    async def test_no_mapped_db_ids_skips_shortcircuit(self, monkeypatch):
        """mapped_db_ids None이면 mapped_db_ids 단축 대신 LLM 분해로 진행한다."""
        import importlib

        # 패키지 __init__이 동명 함수를 재노출해 submodule 이름을 가리므로 importlib로 실제
        # 모듈을 얻는다.
        ip_mod = importlib.import_module("src.orchestration.intent_planner")

        captured = {}

        async def fake_decompose(llm, user_query, app_config, conversation_context=None):
            captured["called"] = True
            captured["ctx"] = conversation_context
            return {"tasks": [{
                "task_id": "t1", "agent": "data_query", "sub_query": user_query,
                "depends_on": [], "input_from": [], "order": 1, "status": "pending",
            }]}

        monkeypatch.setattr(ip_mod, "_llm_decompose", fake_decompose)

        state = {
            "user_query": "공동존 전체 서버 메트릭 조회",
            "mapped_db_ids": None,  # D-064 리셋 결과
            "parsed_requirements": {},
            "conversation_context": {"turn_count": 2, "previous_db_ids": ["polestar_b0"]},
        }
        # llm/app_config는 fake_decompose가 사용하지 않으므로 임의 sentinel 전달
        result = await ip_mod.intent_planner(state, llm=object(), app_config=object())

        assert captured.get("called") is True, "mapped_db_ids None인데 단축이 발동함"
        # 단축 발동 시엔 db_ids가 [polestar_b0]로 고정되지만, 여기선 LLM 경로라 미고정
        task = result["task_plan"][0]
        assert "db_ids" not in task or task.get("db_ids") != ["polestar_b0"]
