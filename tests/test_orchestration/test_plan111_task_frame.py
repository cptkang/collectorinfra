"""plans/111 C-1~C-3 — 복합 질의 task 프레임 계약(`COMPOSITE_TASK_FRAME_ENABLED`).

- 분해 LLM은 task마다 원문 조각(`spans`)만 내고 `sub_query`는 코드가 조각으로 만든다.
- 조각이 원문 밖이거나 원문 숫자를 덮지 못하면 원문 단일 task로 폴백하고 사유를 남긴다.
- 계획의 모든 분기 출구에 담당 교정(알람·프로세스)을 적용한다(D-1) — 존 선택 재진입(②.5) 포함.
- 꺼져 있으면 프롬프트·분해·계획이 종전과 비트 동일하다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.task_frame import PRIOR_PREFIX, render_task_query, verify_task_frames
from src.orchestration.intent_planner import (
    _apply_task_frames,
    _llm_decompose,
    _planner_system_prompt,
    intent_planner,
)
from src.prompts.intent_planner import (
    INTENT_PLANNER_SYSTEM_TEMPLATE,
    INTENT_PLANNER_TASK_FRAME_SECTION,
    render_intent_planner_task_frame_template,
)
from src.state import create_initial_state

Q = "은행존 서버 목록 보여주고, 그중 CPU 90% 넘는 건 알람 이력도 같이 알려줘"


def _llm(content: str) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=content)
    return llm


def _plan(*tasks: dict) -> str:
    return json.dumps({"clarification_needed": None, "tasks": list(tasks)}, ensure_ascii=False)


def _fallback(query: str) -> dict:
    return {"tasks": [{"task_id": "t1", "agent": "data_query", "sub_query": query,
                       "depends_on": [], "input_from": [], "order": 1, "status": "pending"}],
            "clarification_needed": None}


# ── 도메인(순수) ────────────────────────────────────────────────

class TestVerify:
    def test_spans_inside_original_pass(self):
        tasks = [{"task_id": "t1", "spans": ["은행존 서버 목록"]},
                 {"task_id": "t2", "spans": ["CPU 90% 넘는 건", "알람 이력"], "input_from": ["t1"]}]
        assert verify_task_frames(tasks, Q).ok

    def test_invented_literal_is_violation(self):
        tasks = [{"task_id": "t1", "spans": ["심각(alarm_severity='critical') 알람"]}]
        verdict = verify_task_frames(tasks, "현재 활성 상태인 심각 알람 목록 보여줘")
        assert not verdict.ok and any("없는 조각" in v for v in verdict.violations)

    def test_sql_statement_is_violation(self):
        q = "SELECT * FROM cmm_resource WHERE 1=1 이거 돌려줘"
        tasks = [{"task_id": "t1", "spans": ["SELECT * FROM cmm_resource WHERE 1=1"]}]
        verdict = verify_task_frames(tasks, q)
        assert not verdict.ok and any("SQL" in v for v in verdict.violations)

    def test_number_not_covered_is_violation(self):
        tasks = [{"task_id": "t1", "spans": ["은행존 서버 목록"]},
                 {"task_id": "t2", "spans": ["알람 이력"]}]
        verdict = verify_task_frames(tasks, Q)
        assert not verdict.ok and any("90" in v for v in verdict.violations)

    def test_empty_spans_is_violation(self):
        assert not verify_task_frames([{"task_id": "t1", "spans": []}], Q).ok

    def test_context_span_allowed_for_follow_up(self):
        ctx = "## 이전 대화 맥락\n직전 대상 서버: cocm-hdkapp01"
        tasks = [{"task_id": "t1", "spans": ["cocm-hdkapp01", "현재 프로세스"]}]
        assert verify_task_frames(tasks, "그 서버의 현재 프로세스 보여줘", ctx).ok

    def test_whitespace_insensitive(self):
        tasks = [{"task_id": "t1", "spans": ["은행존  서버목록"]}]
        assert verify_task_frames(tasks, "은행존 서버 목록 보여줘").ok


class TestRender:
    def test_orders_by_original_position(self):
        task = {"spans": ["알람 이력", "CPU 90% 넘는 건"]}
        assert render_task_query(task, Q) == "CPU 90% 넘는 건 알람 이력"

    def test_prior_prefix_only_with_input_from(self):
        task = {"spans": ["알람 이력"], "input_from": ["t1"]}
        assert render_task_query(task, Q) == PRIOR_PREFIX + "알람 이력"


# ── 프롬프트 ────────────────────────────────────────────────────

class TestPrompt:
    def test_insert_only(self):
        out = render_intent_planner_task_frame_template(INTENT_PLANNER_SYSTEM_TEMPLATE)
        assert out.replace(INTENT_PLANNER_TASK_FRAME_SECTION, "") == INTENT_PLANNER_SYSTEM_TEMPLATE
        assert out.index(INTENT_PLANNER_TASK_FRAME_SECTION) < out.index("## 출력 형식\n")

    def test_off_is_byte_identical(self, mock_config):
        mock_config.composite.task_frame_enabled = False
        assert _planner_system_prompt(mock_config) == INTENT_PLANNER_SYSTEM_TEMPLATE

    def test_on_contains_section(self, mock_config):
        mock_config.composite.task_frame_enabled = True
        assert INTENT_PLANNER_TASK_FRAME_SECTION in _planner_system_prompt(mock_config)

    def test_magicmock_config_is_not_on(self):
        assert _planner_system_prompt(MagicMock()) is not None  # 예외 없이
        from src.orchestration.intent_planner import _task_frame_on
        assert _task_frame_on(MagicMock()) is False


# ── 분해 적용 ───────────────────────────────────────────────────

class TestApply:
    def test_pass_renders_sub_query_from_spans(self):
        result = {"tasks": [
            {"task_id": "t1", "agent": "data_query", "sub_query": "",
             "spans": ["은행존 서버 목록"]},
            {"task_id": "t2", "agent": "alarm_query", "sub_query": "LLM이 쓴 문장",
             "spans": ["CPU 90% 넘는 건", "알람 이력"], "input_from": ["t1"], "depends_on": ["t1"]},
        ]}
        out = _apply_task_frames(result, Q, "", _fallback(Q))
        assert out["tasks"][0]["sub_query"] == "은행존 서버 목록"
        assert out["tasks"][1]["sub_query"] == PRIOR_PREFIX + "CPU 90% 넘는 건 알람 이력"

    def test_violation_falls_back_with_reason(self):
        result = {"tasks": [{"task_id": "t1", "agent": "alarm_query", "sub_query": "",
                             "spans": ["심각도='critical'"]}]}
        out = _apply_task_frames(result, Q, "", _fallback(Q))
        assert len(out["tasks"]) == 1 and out["tasks"][0]["sub_query"] == Q
        assert out["degraded"][-1]["reason"] == "task_frame_contract_violation"

    def test_fallback_plan_passes_through(self):
        fb = _fallback(Q)
        assert _apply_task_frames(fb, Q, "", fb) is fb

    def test_sub_query_without_spans_is_rejected(self):
        """계약을 무시하고 자유문만 쓴 계획은 폴백한다(발명 차단)."""
        result = {"tasks": [
            {"task_id": "t1", "agent": "data_query", "sub_query": "심각(critical) 알람"}]}
        out = _apply_task_frames(result, Q, "", _fallback(Q))
        assert out["tasks"][0]["sub_query"] == Q and "degraded" in out


@pytest.mark.asyncio
class TestDecompose:
    async def test_json_path_keeps_spans_when_on(self, mock_config):
        mock_config.composite.task_frame_enabled = True
        mock_config.composite.plan_dag_validation_enabled = False
        mock_config.composite.sequential_replan_enabled = False
        llm = _llm(_plan(
            {"task_id": "t1", "agent": "data_query", "sub_query": "",
             "spans": ["은행존 서버 목록"], "depends_on": [], "input_from": []},
            {"task_id": "t2", "agent": "alarm_query", "sub_query": "",
             "spans": ["CPU 90% 넘는 건", "알람 이력"], "depends_on": ["t1"], "input_from": ["t1"]},
        ))
        out = await _llm_decompose(llm, Q, mock_config)
        assert [t["sub_query"] for t in out["tasks"]] == [
            "은행존 서버 목록", PRIOR_PREFIX + "CPU 90% 넘는 건 알람 이력"]

    async def test_off_ignores_spans_byte_identical(self, mock_config):
        mock_config.composite.task_frame_enabled = False
        mock_config.composite.plan_dag_validation_enabled = False
        mock_config.composite.sequential_replan_enabled = False
        content = _plan({"task_id": "t1", "agent": "data_query", "sub_query": "LLM 문장",
                         "spans": ["은행존 서버 목록"], "depends_on": [], "input_from": []})
        out = await _llm_decompose(_llm(content), Q, mock_config)
        assert out["tasks"][0]["sub_query"] == "LLM 문장" and "spans" not in out["tasks"][0]


# ── 단일 출구 정규화(D-1) ───────────────────────────────────────

@pytest.mark.asyncio
class TestExitNormalize:
    ALARM_Q = "현재 활성 상태인 심각 알람 목록 보여줘"

    def _zone_state(self, query: str) -> dict:
        state = create_initial_state(user_query=query)
        state["selected_db_ids"] = ["polestar_cm_gp"]
        return state

    async def test_zone_resume_alarm_off_is_also_coerced(self, mock_config):
        """**플래그 off 에서도** 교정된다(plans/114 T-1 · D-250 ⑤ — 111 G-5·108 G-2 개정).

        종전 단언은 *"꺼져 있으면 data_query 고정"* 이었다. 그 동작이 2단 기본 설정에서
        알람 질의를 전건 오분류시켜 단 비교를 불공정하게 만든다는 것이 개정 근거다.
        """
        mock_config.composite.task_frame_enabled = False
        state = self._zone_state(self.ALARM_Q)
        out = await intent_planner(state, llm=_llm("{}"), app_config=mock_config)
        assert out["task_plan"][0]["agent"] == "alarm_query"

    async def test_zone_resume_alarm_on_is_coerced(self, mock_config):
        """켜면 존 선택 재진입도 알람 교정을 지난다(111 §2.4 27턴 부류)."""
        mock_config.composite.task_frame_enabled = True
        llm = _llm("{}")
        out = await intent_planner(self._zone_state(self.ALARM_Q), llm=llm, app_config=mock_config)
        assert out["task_plan"][0]["agent"] == "alarm_query"
        assert out["task_plan"][0]["db_ids"] == ["polestar_cm_gp"]
        llm.ainvoke.assert_not_called()

    async def test_form_fill_turn_not_coerced(self, mock_config):
        mock_config.composite.task_frame_enabled = True
        state = self._zone_state("알람 현황 양식 채워줘")
        state["template_structure"] = {"sheets": []}
        out = await intent_planner(state, llm=_llm("{}"), app_config=mock_config)
        assert out["task_plan"][0]["agent"] == "data_query"
