"""context_resolver 노드 테스트."""

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.context_resolver import MAX_HISTORY_TURNS, _trim_messages, context_resolver
from src.state import create_initial_state


class TestContextResolverFirstTurn:
    """첫 턴에서 맥락이 None인지 검증."""

    async def test_first_turn_context_is_none(self):
        """첫 턴이면 conversation_context가 None이다."""
        state = create_initial_state(user_query="서버 목록 조회")
        result = await context_resolver(state)

        assert result["conversation_context"] is None
        assert result["current_node"] == "context_resolver"

    async def test_single_human_message_is_first_turn(self):
        """HumanMessage가 1개면 첫 턴으로 판단한다."""
        state = create_initial_state(user_query="test")
        result = await context_resolver(state)
        assert result["conversation_context"] is None


class TestContextResolverFollowUpTurn:
    """후속 턴에서 맥락 추출을 검증."""

    async def test_follow_up_extracts_previous_sql(self):
        """후속 턴에서 이전 SQL이 context에 포함된다."""
        state = create_initial_state(user_query="그 중에서 메모리 90% 이상만")
        # 이전 턴 상태 시뮬레이션
        state["messages"] = [
            HumanMessage(content="CPU 80% 이상 서버"),
            AIMessage(content="결과입니다..."),
            HumanMessage(content="그 중에서 메모리 90% 이상만"),
        ]
        state["generated_sql"] = "SELECT * FROM servers WHERE cpu > 80"
        state["query_results"] = [
            {"hostname": "web-01", "cpu": 85},
            {"hostname": "db-01", "cpu": 92},
        ]
        state["relevant_tables"] = ["servers", "cpu_metrics"]

        result = await context_resolver(state)
        ctx = result["conversation_context"]

        assert ctx is not None
        assert ctx["turn_count"] == 2
        assert ctx["previous_sql"] == "SELECT * FROM servers WHERE cpu > 80"
        assert ctx["previous_result_count"] == 2
        assert "web-01" not in ctx["previous_results_summary"]  # 요약이지 데이터가 아님
        assert "2건 조회됨" in ctx["previous_results_summary"]
        assert "servers" in ctx["previous_tables"]

    async def test_follow_up_detects_pending_synonym_reuse(self):
        """후속 턴에서 pending_synonym_reuse를 감지한다."""
        state = create_initial_state(user_query="재활용")
        state["messages"] = [
            HumanMessage(content="server_name 유사 단어 생성"),
            AIMessage(content="hostname과 유사합니다..."),
            HumanMessage(content="재활용"),
        ]
        state["pending_synonym_reuse"] = {
            "target_column": "server_name",
            "suggestions": [{"column": "hostname"}],
        }

        result = await context_resolver(state)
        ctx = result["conversation_context"]

        assert ctx["has_pending_synonym_reuse"] is True

    async def test_follow_up_detects_pending_registrations(self):
        """후속 턴에서 pending_synonym_registrations를 감지한다."""
        state = create_initial_state(user_query="전체 등록")
        state["messages"] = [
            HumanMessage(content="Excel 채워줘"),
            AIMessage(content="완료. 등록하시겠습니까?"),
            HumanMessage(content="전체 등록"),
        ]
        state["pending_synonym_registrations"] = [
            {"index": 1, "field": "CPU 사용률", "column": "cpu_metrics.usage_pct", "db_id": "polestar"},
        ]

        result = await context_resolver(state)
        ctx = result["conversation_context"]

        assert ctx["has_pending_synonym_registrations"] is True
        assert ctx["pending_synonym_reg_count"] == 1

    async def test_previous_db_id_in_context(self):
        """이전 턴의 active_db_id가 context에 포함된다."""
        state = create_initial_state(user_query="설명도 생성해줘")
        state["messages"] = [
            HumanMessage(content="polestar 캐시 생성"),
            AIMessage(content="완료"),
            HumanMessage(content="설명도 생성해줘"),
        ]
        state["active_db_id"] = "polestar"

        result = await context_resolver(state)
        ctx = result["conversation_context"]

        assert ctx["previous_db_id"] == "polestar"


class TestContextResolverEntityPreservation:
    """Plan 50 M3 — 후속 턴 DB/엔티티/위치 보존 검증."""

    def _follow_up_state(self):
        state = create_initial_state(user_query="해당 서버의 현재 프로세스 리스트를 확인해줘")
        state["messages"] = [
            HumanMessage(content="김포 운영 폴스타 ### 서버의 CPU 코어 수, 메모리 용량"),
            AIMessage(content="결과입니다..."),
            HumanMessage(content="해당 서버의 현재 프로세스 리스트를 확인해줘"),
        ]
        state["generated_sql"] = "SELECT hostname, cpu_cores FROM ..."
        state["query_results"] = [{"hostname": "###", "cpu_cores": 8, "mem_gb": 64}]
        state["target_databases"] = [
            {"db_id": "polestar_cm_gp", "relevance_score": 0.9},
        ]
        state["active_db_id"] = "polestar_cm_gp"
        state["parsed_requirements"] = {
            "filter_conditions": [{"field": "hostname", "value": "###"}],
            "target_db_hints": ["김포", "운영"],
        }
        return state

    async def test_previous_db_ids_unified(self):
        """previous_db_ids에 target_databases/active_db_id가 통합된다."""
        result = await context_resolver(self._follow_up_state())
        ctx = result["conversation_context"]
        assert ctx["previous_db_ids"] == ["polestar_cm_gp"]

    async def test_previous_entities_from_filter_and_results(self):
        """식별 키(hostname)가 filter와 결과에서 값까지 보존된다."""
        result = await context_resolver(self._follow_up_state())
        ctx = result["conversation_context"]
        values = {(e["field"].lower(), str(e["value"])) for e in ctx["previous_entities"]}
        assert ("hostname", "###") in values

    async def test_previous_location_surface_extracted(self):
        """직전 위치/환경 신호(김포 운영)가 표면 추출된다."""
        result = await context_resolver(self._follow_up_state())
        ctx = result["conversation_context"]
        assert "김포" in ctx["previous_location"]
        assert "운영" in ctx["previous_location"]

    async def test_entity_row_cap_enforced(self):
        """대량 결과여도 엔티티 보존은 상한(_MAX_ENTITY_ROWS)을 넘지 않는다."""
        from src.nodes.context_resolver import _MAX_ENTITY_ROWS

        state = self._follow_up_state()
        state["query_results"] = [{"hostname": f"host-{i}"} for i in range(500)]
        state["parsed_requirements"] = {}
        result = await context_resolver(state)
        ctx = result["conversation_context"]
        assert len(ctx["previous_entities"]) <= _MAX_ENTITY_ROWS


class TestTrimMessages:
    """대화 히스토리 트리밍 검증."""

    def test_no_trimming_under_limit(self):
        """제한 이하면 트리밍하지 않는다."""
        messages = [HumanMessage(content=f"msg-{i}") for i in range(5)]
        result = _trim_messages(messages)
        assert len(result) == 5

    def test_trims_over_limit(self):
        """제한 초과 시 최근 메시지만 유지한다."""
        messages = []
        for i in range(MAX_HISTORY_TURNS + 5):
            messages.append(HumanMessage(content=f"q-{i}"))
            messages.append(AIMessage(content=f"a-{i}"))

        result = _trim_messages(messages)
        assert len(result) == MAX_HISTORY_TURNS * 2
        # 최근 메시지가 유지되는지 확인
        last_msg = result[-1]
        assert isinstance(last_msg, AIMessage)

    def test_trims_on_token_budget(self):
        """턴 수가 적어도 누적 문자수 상한 초과 시 추가 트리밍한다 (B3 이중 기준)."""
        from src.nodes.context_resolver import MAX_HISTORY_CHARS

        # 2턴(4메시지)이지만 각 메시지가 비대 → 누적 문자수 상한 초과
        big = "x" * (MAX_HISTORY_CHARS // 2)
        messages = [
            HumanMessage(content=big),
            AIMessage(content=big),
            HumanMessage(content=big),
            AIMessage(content="최근 응답"),
        ]
        result = _trim_messages(messages)
        # 최소 마지막 2개는 보존, 누적은 상한 이하로 줄어든다
        assert len(result) >= 2
        assert len(result) < len(messages)
