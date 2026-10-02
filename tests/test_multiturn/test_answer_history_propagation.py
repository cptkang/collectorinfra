"""멀티턴 답변 이력 전파 테스트 (Approach A — ①②④).

배경(진단): 후속 판단형 질의("위와 같은 상태면 rightsizing 해도 되는가?")가
general_inference로 라우팅되는데, orchestration 경로에서 (①) 격리 입력이 대화 이력을
비우고 (②) 어시스턴트 답변이 애초에 messages에 안 쌓여, 직전 턴 데이터를 근거로 판단하지
못하고 "데이터가 확보되지 않았다"고 거부하던 문제를 수정한다.

- ① _make_isolated_input이 추론 agent(needs_history)에만 트리밍 이력 전달, 조회 agent엔 [].
- ② result_aggregator/general_inference가 최종 답변을 AIMessage로 messages에 누적.
- ④ general_inference가 conversation_context를 판단 근거로 그라운딩 + 판단 지침.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.general_inference import (
    _JUDGMENT_GUIDANCE,
    _build_context_grounding,
    _build_system_prompt,
)
from src.orchestration.result_aggregator import _with_answer_history, result_aggregator
from src.orchestration.subagents import _history_for_agent, _make_isolated_input
from src.state import create_initial_state


# ──────────────────────────────────────────────
# ① 격리 입력 이력 선별 전달
# ──────────────────────────────────────────────

def _sample_messages() -> list:
    return [
        HumanMessage(content="은행 ### 서버 스펙"),
        AIMessage(content="### 서버 스펙 1건: CPU 8코어, 메모리 32GB"),
        HumanMessage(content="해당 서버 프로세스"),
        AIMessage(content="상위 5건: java CPU 90%..."),
        HumanMessage(content="rightsizing 괜찮은가?"),  # 현재 턴 질의
    ]


class TestHistoryForAgent:
    def test_general_inference_receives_trimmed_history(self):
        """general_inference(needs_history)는 직전 이력을 받되, 말미 현재 턴 질의는 제외."""
        state = {"messages": _sample_messages()}
        hist = _history_for_agent({"agent": "general_inference"}, state)
        # 현재 턴 질의(마지막 HumanMessage)는 노드가 user_query로 재부착하므로 제외
        assert all(
            not (isinstance(m, HumanMessage) and "rightsizing" in m.content) for m in hist
        )
        # 직전 턴 답변(판단 근거)은 포함
        assert any(isinstance(m, AIMessage) and "java CPU 90%" in m.content for m in hist)

    def test_data_query_receives_no_history(self):
        """데이터 조회 agent는 이력 격리 유지(빈 리스트)."""
        state = {"messages": _sample_messages()}
        assert _history_for_agent({"agent": "data_query"}, state) == []
        assert _history_for_agent({"agent": "process_query"}, state) == []

    def test_make_isolated_input_wires_history_by_agent(self):
        """_make_isolated_input이 agent별로 messages를 선별 주입한다."""
        state = {"messages": _sample_messages()}
        gi = _make_isolated_input({"agent": "general_inference"}, state, {})
        dq = _make_isolated_input({"agent": "data_query"}, state, {})
        assert len(gi["messages"]) > 0
        assert dq["messages"] == []

    def test_empty_messages_safe(self):
        """이력이 없어도 안전하게 빈 리스트."""
        assert _history_for_agent({"agent": "general_inference"}, {"messages": []}) == []


# ──────────────────────────────────────────────
# ② 최종 답변 messages 누적
# ──────────────────────────────────────────────

class TestAnswerHistoryAppend:
    @pytest.mark.asyncio
    async def test_single_task_appends_ai_message(self, mock_config):
        """단일 task 최종화 시 final_response가 AIMessage로 messages에 누적된다(②)."""
        state = create_initial_state(user_query="rightsizing 괜찮은가?")
        state["task_plan"] = [
            {"task_id": "t1", "agent": "general_inference", "sub_query": "q",
             "order": 1, "status": "completed"},
        ]
        state["task_results"] = {"t1": {"final_response": "판단 결과입니다"}}

        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

        assert out["final_response"] == "판단 결과입니다"
        msgs = out.get("messages") or []
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)
        assert msgs[0].content == "판단 결과입니다"

    @pytest.mark.asyncio
    async def test_composite_appends_single_ai_message(self, mock_config):
        """복합 task 통합 답변도 AIMessage 1건만 누적된다(이중 누적 방지)."""
        state = create_initial_state(user_query="복합")
        state["task_plan"] = [
            {"task_id": "t1", "agent": "general_inference", "sub_query": "q1",
             "order": 1, "status": "completed"},
            {"task_id": "t2", "agent": "general_inference", "sub_query": "q2",
             "order": 2, "status": "completed"},
        ]
        state["task_results"] = {
            "t1": {"final_response": "첫 답변"},
            "t2": {"final_response": "둘째 답변"},
        }

        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

        msgs = out.get("messages") or []
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)
        assert msgs[0].content == out["final_response"]

    def test_empty_final_response_no_append(self):
        """final_response가 비면 messages를 누적하지 않는다(_with_answer_history 가드)."""
        assert _with_answer_history({"final_response": ""}) == {"final_response": ""}
        assert _with_answer_history({"final_response": "   "}) == {"final_response": "   "}
        # 내용이 있으면 AIMessage 1건 부착
        out = _with_answer_history({"final_response": "답변"})
        assert out["messages"][0].content == "답변"


# ──────────────────────────────────────────────
# ④ general_inference 판단 근거 그라운딩
# ──────────────────────────────────────────────

class TestContextGrounding:
    def test_first_turn_no_grounding(self):
        """첫 턴(맥락 없음/turn_count<=1)이면 그라운딩 없음."""
        assert _build_context_grounding({}) == ""
        assert _build_context_grounding({"conversation_context": {"turn_count": 1}}) == ""

    def test_follow_up_grounding_contains_signals(self):
        """후속 턴이면 위치·DB·서버·요약이 그라운딩 블록에 포함된다."""
        state = {
            "conversation_context": {
                "turn_count": 3,
                "previous_location": "은행",
                "previous_db_ids": ["polestar_b0"],
                "previous_entities": [{"field": "hostname", "value": "###"}],
                "previous_results_summary": "35건 조회됨",
            }
        }
        block = _build_context_grounding(state)
        assert "은행" in block
        assert "polestar_b0" in block
        assert "hostname=###" in block
        assert "35건 조회됨" in block

    def test_system_prompt_includes_judgment_guidance_on_follow_up(self, mock_config):
        """후속 턴 시스템 프롬프트에 판단 지침이 포함된다(거부 환각 차단)."""
        state = {
            "conversation_context": {
                "turn_count": 3,
                "previous_location": "은행",
                "previous_db_ids": ["polestar_b0"],
                "previous_entities": [{"field": "hostname", "value": "###"}],
                "previous_results_summary": "35건 조회됨",
            }
        }
        prompt = _build_system_prompt(state, mock_config)
        assert _JUDGMENT_GUIDANCE.strip()[:20] in prompt
        assert "rightsizing" in prompt

    def test_system_prompt_first_turn_no_guidance(self, mock_config):
        """첫 턴이면 판단 지침을 붙이지 않는다(회귀 방지)."""
        state = {"conversation_context": None}
        prompt = _build_system_prompt(state, mock_config)
        assert "이전 대화 맥락 (후속 턴 판단 근거)" not in prompt


class TestCapabilityScopeConstraint:
    """미지원 기능 광고 차단 — 후속 제안은 지원 역량 목록으로 하드 바운딩."""

    def _follow_up_state(self):
        return {
            "conversation_context": {
                "turn_count": 3,
                "previous_location": "은행",
                "previous_db_ids": ["polestar_b0"],
                "previous_entities": [{"field": "hostname", "value": "###"}],
                "previous_results_summary": "1건 조회됨",
            }
        }

    def test_follow_up_prompt_forbids_unsupported_examples(self, mock_config):
        """후속 턴 프롬프트가 미지원 항목(InnoDB 등) 예시 제안을 금지한다."""
        prompt = _build_system_prompt(self._follow_up_state(), mock_config)
        assert "[지원 가능한 조회 유형]" in prompt  # 제안은 지원 목록 안에서만
        # ★ "InnoDB"는 **기본 프롬프트에도** 들어 있어 addendum 여부를 변별하지 못한다
        # (2026-08-28 실측 — 미지원 제안 금지가 첫 턴에도 적용되도록 확장됐다).
        # addendum 고유 문구인 "rightsizing"으로 판정한다.
        assert "rightsizing" in prompt  # _JUDGMENT_GUIDANCE 고유
        assert "프로세스" in prompt and "알람" in prompt  # 지원 목록 자체 존재

    def test_no_catalog_follow_up_still_carries_capability_list(self):
        """소스 카탈로그가 없어도 후속 턴이면 지원 목록이 실려 제약의 기준이 된다."""

        class _NoCatalogCfg:
            class _Multi:
                def get_active_db_ids(self):
                    return []

            multi_db = _Multi()

        prompt = _build_system_prompt(self._follow_up_state(), _NoCatalogCfg())
        assert "[지원 가능한 조회 유형]" in prompt
        assert "rightsizing" in prompt  # _JUDGMENT_GUIDANCE 고유(위 주석 참조)

    def test_first_turn_no_capability_constraint_addendum(self, mock_config):
        """첫 턴(맥락 없음)이면 판단·제안 제약 addendum을 붙이지 않는다(회귀 방지).

        ★ 종전에는 `"InnoDB" not in prompt`로 판정했는데, 미지원 제안 금지 문구가
        **기본 프롬프트로도 확장**되면서 그 마커가 변별력을 잃어 사전존재 실패로 남아
        있었다(2026-08-28 정리). 판정을 addendum 고유 문구로 좁히고, 기본 프롬프트의
        제약은 **여전히 있어야 한다**는 것도 함께 못박는다.
        """
        prompt = _build_system_prompt({"conversation_context": None}, mock_config)

        assert "rightsizing" not in prompt, "첫 턴에는 판단 addendum이 붙지 않는다"
        assert "InnoDB" in prompt, "미지원 제안 금지는 첫 턴에도 적용된다"
