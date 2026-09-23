"""plans/114 T-1 — 계획 단일 출구 정규화를 `COMPOSITE_TASK_FRAME_ENABLED`에서 뗀다(D-250 ⑤).

결정적 교정(`_coerce_alarm_intent`·`_coerce_process_intent`)은 LLM 출력을 **고치기만** 하는
후처리다. 프롬프트 계약을 바꾸는 111 C-1~C-3 플래그와 묶여 있을 이유가 없고, 묶인 채로 두면
2단 기본 설정에서 알람 질의가 전건 `data_query`로 남는다 — run `20260922-112010`에서 존
자동응답 105턴이 전부 이 경로였고 D군 16턴 중 15턴이 타임아웃이었다(`plans/114` §2.5).

실 LLM 0 — 사전 처리 조기 반환(존 선택 재진입 ②.5)은 LLM을 부르지 않는다.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestration.intent_planner import _normalize_plan_exit, intent_planner
from src.state import create_initial_state

ALARM_Q = "현재 활성 상태인 심각 알람 목록 보여줘"
PROCESS_Q = "web-01 서버의 현재 실행 중인 프로세스 목록 보여줘"


def _llm() -> AsyncMock:
    """부르면 테스트가 실패하도록 빈 응답만 두는 LLM — 이 경로는 LLM을 타지 않는다."""
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content="{}")
    return llm


def _zone_resume_state(query: str) -> dict:
    """존 선택 재진입 턴(②.5) — 앞 턴 역질문에 존을 고른 뒤 이어지는 턴이다."""
    state = create_initial_state(user_query=query)
    state["selected_db_ids"] = ["polestar_cm_gp"]
    return state


@pytest.fixture
def frame_off(mock_config):
    """T-1 이전이라면 교정이 **건너뛰어지던** 설정."""
    mock_config.composite.task_frame_enabled = False
    return mock_config


@pytest.mark.asyncio
class TestFlagIndependent:
    async def test_alarm_coerced_with_flag_off(self, frame_off):
        llm = _llm()
        out = await intent_planner(_zone_resume_state(ALARM_Q), llm=llm, app_config=frame_off)

        assert out["task_plan"][0]["agent"] == "alarm_query"
        assert out["task_plan"][0]["db_ids"] == ["polestar_cm_gp"]
        llm.ainvoke.assert_not_called(), "존 선택 재진입은 LLM 분해를 타지 않는다"

    async def test_process_coerced_with_flag_off(self, frame_off):
        out = await intent_planner(_zone_resume_state(PROCESS_Q), llm=_llm(), app_config=frame_off)

        assert out["task_plan"][0]["agent"] == "process_query"

    async def test_flag_on_behaviour_unchanged(self, mock_config):
        """플래그 on 일 때의 결과는 T-1 전과 같다 — 이 변경은 off 쪽만 연다."""
        mock_config.composite.task_frame_enabled = True
        out = await intent_planner(_zone_resume_state(ALARM_Q), llm=_llm(),
                                   app_config=mock_config)

        assert out["task_plan"][0]["agent"] == "alarm_query"

    async def test_form_fill_turn_still_excluded(self, frame_off):
        """양식 채우기 턴은 종전대로 제외다 — 알람 템플릿으로 뒤집으면 양식 경로를 잃는다."""
        state = _zone_resume_state("알람 현황 양식 채워줘")
        state["template_structure"] = {"sheets": []}

        out = await intent_planner(state, llm=_llm(), app_config=frame_off)

        assert out["task_plan"][0]["agent"] == "data_query"

    async def test_uploaded_file_turn_still_excluded(self, frame_off):
        state = _zone_resume_state("알람 현황 양식 채워줘")
        state["uploaded_file"] = "/tmp/form.xlsx"

        out = await intent_planner(state, llm=_llm(), app_config=frame_off)

        assert out["task_plan"][0]["agent"] == "data_query"


@pytest.mark.asyncio
class TestContractStaysGated:
    """**상시화 대상은 결정적 교정뿐이다**(`plans/111` 소유자 요청 · 111 §2.7 실측).

    조각 계약(`_apply_task_frames`·`verify_task_frames`)까지 상시화하면 안 된다 — 2026-09-22
    측정에서 9B 모델이 8건 중 4건에서 조각을 내지 못해 **원문 단일 task 폴백**이 됐다. 계약이
    항상 돌면 그 폴백이 기본 동작이 되어 2단 분해가 사실상 꺼진다.

    경계는 호출부가 갈라져 있어서 지켜진다 — 교정은 `intent_planner` 출구, 계약은 `_plan_turn`
    안(`_task_frame_on` 뒤)이다.
    """

    async def test_flag_off_skips_span_contract_but_coerces(self, frame_off, monkeypatch):
        # `src.orchestration` 패키지의 `intent_planner` 속성은 **함수**다(`__init__` 재노출) —
        # `import … as` 로는 모듈을 못 잡는다. 모듈은 `sys.modules` 에서 꺼낸다.
        planner = sys.modules["src.orchestration.intent_planner"]

        called: list[str] = []
        monkeypatch.setattr(planner, "_apply_task_frames",
                            lambda *a, **kw: called.append("_apply_task_frames"))
        monkeypatch.setattr(planner, "verify_task_frames",
                            lambda *a, **kw: called.append("verify_task_frames"))

        out = await intent_planner(_zone_resume_state(ALARM_Q), llm=_llm(), app_config=frame_off)

        assert out["task_plan"][0]["agent"] == "alarm_query", "교정은 적용된다"
        assert called == [], "조각 계약은 플래그 뒤에 그대로 있다"

    async def test_flag_off_keeps_llm_sub_query_verbatim(self):
        """계약이 꺼져 있으면 `sub_query` 는 LLM 문장 그대로다(조각으로 다시 만들지 않는다)."""
        from unittest.mock import MagicMock

        from src.orchestration.intent_planner import _llm_decompose

        config = MagicMock()
        config.composite.task_frame_enabled = False
        config.composite.plan_dag_validation_enabled = False
        config.composite.sequential_replan_enabled = False
        config.router.capability_ownership_enabled = False
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content=json.dumps(
            {"clarification_needed": None,
             "tasks": [{"task_id": "t1", "agent": "data_query", "sub_query": "LLM 문장",
                        "spans": ["은행존 서버 목록"], "depends_on": [], "input_from": []}]},
            ensure_ascii=False))

        out = await _llm_decompose(llm, "은행존 서버 목록 보여줘", config)

        assert out["tasks"][0]["sub_query"] == "LLM 문장"
        assert "spans" not in out["tasks"][0]


class TestIdempotent:
    """LLM 분해 경로는 이미 교정을 거친다 — 출구에서 한 번 더 돌아도 값이 바뀌지 않아야 한다."""

    def test_second_pass_changes_nothing(self):
        state = create_initial_state(user_query=ALARM_Q)
        result = {"task_plan": [{"task_id": "t1", "agent": "data_query",
                                 "sub_query": ALARM_Q, "input_from": []}]}

        _normalize_plan_exit(result, state)
        once = [dict(t) for t in result["task_plan"]]
        _normalize_plan_exit(result, state)

        assert result["task_plan"] == once
        assert once[0]["agent"] == "alarm_query"

    def test_non_data_query_agents_untouched(self):
        state = create_initial_state(user_query=ALARM_Q)
        result = {"task_plan": [{"task_id": "t1", "agent": "general_inference",
                                 "sub_query": ALARM_Q, "input_from": []}]}

        _normalize_plan_exit(result, state)

        assert result["task_plan"][0]["agent"] == "general_inference"

    def test_dependent_task_alarm_vocabulary_untouched(self):
        """선행 task 의존(`input_from`)의 알람 어휘는 선별 조건 잔재다(D-086) — 뒤집지 않는다."""
        state = create_initial_state(user_query=ALARM_Q)
        result = {"task_plan": [{"task_id": "t2", "agent": "data_query",
                                 "sub_query": "심각 알람이 있는 서버들의 CPU 사용률",
                                 "input_from": ["t1"]}]}

        _normalize_plan_exit(result, state)

        assert result["task_plan"][0]["agent"] == "data_query"

    def test_empty_plan_is_noop(self):
        state = create_initial_state(user_query=ALARM_Q)
        for plan in ({}, {"task_plan": None}, {"task_plan": []}):
            before = dict(plan)
            _normalize_plan_exit(plan, state)
            assert plan == before
