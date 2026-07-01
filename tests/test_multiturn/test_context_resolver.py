"""context_resolver 노드 테스트."""

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.context_resolver import (
    MAX_HISTORY_TURNS,
    _extract_previous_entities,
    _looks_like_process_rows,
    _trim_messages,
    context_resolver,
)
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


class TestContextResolverEntityStickiness:
    """D-056 후속 — 분석/판단 턴(조회 없음)을 건너뛰어도 엔티티가 승계되는지 검증."""

    async def test_inherits_entities_when_current_extraction_empty(self):
        """직전 턴이 조회 없이(query_results=[]) 분석만 했으면, 그 직전 conversation_context의
        엔티티/DB/위치를 승계한다("해당 서버" 유지)."""
        state = create_initial_state(user_query="해당 서버의 최근 1개월 CPU 사용률 평균/최대")
        # 3턴: 스펙조회 → 프로세스 → 판단(general_inference, 조회없음) → (이번4턴)
        state["messages"] = [
            HumanMessage(content="은행존 ### 서버 IP/CPU/메모리"),
            AIMessage(content="사양입니다..."),
            HumanMessage(content="해당 서버 프로세스 리스트"),
            AIMessage(content="상위 5건..."),
            HumanMessage(content="프로세스 보니 정상인가"),
            AIMessage(content="판단 결과..."),
            HumanMessage(content="해당 서버의 최근 1개월 CPU 사용률 평균/최대"),
        ]
        # 직전 턴(판단)이 조회를 안 해 top-level 신호가 비어 있음
        state["query_results"] = []
        state["parsed_requirements"] = {"filter_conditions": []}
        # 직전 conversation_context엔 앞 턴들에서 보존된 엔티티/DB/위치가 남아 있음
        state["conversation_context"] = {
            "turn_count": 3,
            "previous_entities": [{"field": "hostname", "value": "###"}],
            "previous_db_ids": ["polestar_b0"],
            "previous_location": "은행",
        }

        result = await context_resolver(state)
        ctx = result["conversation_context"]

        values = {(e["field"].lower(), str(e["value"])) for e in ctx["previous_entities"]}
        assert ("hostname", "###") in values  # 분석 턴을 건너뛰어도 유지
        assert ctx["previous_db_ids"] == ["polestar_b0"]
        assert "은행" in ctx["previous_location"]

    async def test_current_extraction_takes_priority_over_inherited(self):
        """이번 턴에서 새 엔티티가 잡히면 승계값보다 우선한다(오염 없음)."""
        state = create_initial_state(user_query="다른 서버 조회")
        state["messages"] = [
            HumanMessage(content="### 서버"),
            AIMessage(content="..."),
            HumanMessage(content="다른 서버 조회"),
        ]
        state["query_results"] = [{"hostname": "NEW-HOST"}]
        state["parsed_requirements"] = {"filter_conditions": []}
        state["conversation_context"] = {
            "turn_count": 2,
            "previous_entities": [{"field": "hostname", "value": "###"}],
        }

        result = await context_resolver(state)
        values = {str(e["value"]) for e in result["conversation_context"]["previous_entities"]}
        assert "NEW-HOST" in values
        assert "###" not in values  # 이번 턴 추출이 있으면 승계 안 함


class TestProcessRowEntityExclusion:
    """프로세스 조회 결과 행이 서버 식별 엔티티로 오수집되지 않는지 검증.

    버그: 프로세스 리스트 조회 후 "해당 서버"가 프로세스명(mysql 등)으로 오해소되어
    알람/데이터 조회가 엉뚱한 대상으로 감. 프로세스 행({name,pid,...})은 서버가 아님.
    """

    _PROC_ROWS = [
        {"name": "mysql", "pid": 30176, "user": "mysql", "cpu_pct": 12.0, "args": "mysqld"},
        {"name": "node_exporter", "pid": 50482, "user": "root", "cpu_pct": 1.0, "args": "ne"},
    ]

    def test_looks_like_process_rows(self):
        assert _looks_like_process_rows(self._PROC_ROWS) is True
        assert _looks_like_process_rows([{"hostname": "###", "cpu_cores": 8}]) is False
        assert _looks_like_process_rows([]) is False

    def test_process_rows_not_harvested_as_entities(self):
        """프로세스 행은 결과 harvesting에서 제외 — 프로세스명/pid가 엔티티로 안 들어간다."""
        state = {"parsed_requirements": {"filter_conditions": []}}
        entities = _extract_previous_entities(state, self._PROC_ROWS)
        values = {str(e["value"]) for e in entities}
        assert "mysql" not in values
        assert "30176" not in values
        assert entities == []

    def test_hostname_filter_survives_with_process_results(self):
        """프로세스 조회여도 filter_conditions의 hostname은 서버 식별자로 보존된다."""
        state = {
            "parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "value": "###"}]
            }
        }
        entities = _extract_previous_entities(state, self._PROC_ROWS)
        values = {(e["field"].lower(), str(e["value"])) for e in entities}
        assert ("hostname", "###") in values
        assert "mysql" not in {str(e["value"]) for e in entities}

    def test_server_rows_still_harvested(self):
        """서버 조회 결과 행(pid 없음)은 기존대로 hostname 등을 수집한다(회귀 방지)."""
        state = {"parsed_requirements": {"filter_conditions": []}}
        rows = [{"hostname": "web-01", "cpu_cores": 8}, {"hostname": "db-01", "cpu_cores": 16}]
        entities = _extract_previous_entities(state, rows)
        values = {str(e["value"]) for e in entities}
        assert "web-01" in values and "db-01" in values

    async def test_chain_process_turn_keeps_server_via_stickiness(self):
        """프로세스 턴 다음 판단/알람 턴에서 '해당 서버'가 프로세스명이 아닌 서버로 유지된다.

        프로세스 결과가 harvesting에서 제외되어 fresh 추출이 비면, 직전 ctx의 hostname을
        sticky 승계한다(process 오염 없이)."""
        state = create_initial_state(user_query="지난 한 달 해당 서버의 알람 분석")
        state["messages"] = [
            HumanMessage(content="김포 ### 서버 사양"),
            AIMessage(content="사양..."),
            HumanMessage(content="해당 서버 프로세스 리스트"),
            AIMessage(content="프로세스..."),
            HumanMessage(content="지난 한 달 해당 서버의 알람 분석"),
        ]
        # 직전 턴이 프로세스 조회였음: query_results=프로세스 행, filter엔 hostname 없음(지시어)
        state["query_results"] = self._PROC_ROWS
        state["parsed_requirements"] = {"filter_conditions": []}
        # 직전 ctx엔 앞 턴에서 보존된 서버 hostname이 있음
        state["conversation_context"] = {
            "turn_count": 2,
            "previous_entities": [{"field": "hostname", "value": "###"}],
            "previous_db_ids": ["polestar_cm_gp"],
            "previous_location": "김포",
        }

        result = await context_resolver(state)
        ctx = result["conversation_context"]
        values = {str(e["value"]) for e in ctx["previous_entities"]}
        assert "###" in values  # 서버 유지
        assert "mysql" not in values and "30176" not in values  # 프로세스 오염 없음


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
