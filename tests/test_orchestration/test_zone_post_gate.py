"""존 역질문 후단 게이트 테스트 (D-140 후속2).

라우트 pre-gate(표면어 "서버"+전량 조회 필수)가 놓치는 형태 — 위치어 없는 존 단위
조회("OS 버전 확인하시오")가 LLM 임의 팬아웃(전 존/임의 존)으로 흐르던 것을,
분류·핀·승계가 끝난 시점의 실제 신호로 판정해 존 선택 역질문으로 전환한다.
§4.2 비발동 목록(서버명 지목·승계·존 무관·비대화 채널)의 결정적 경계를 고정한다.
전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_POLESTAR = set(_ACTIVE)


def _post_gate_config(active=None):
    active = _ACTIVE if active is None else active
    return SimpleNamespace(
        multi_db=SimpleNamespace(get_active_db_ids=lambda: active),
        get_polestar_db_ids=lambda: _POLESTAR,
    )


def _targets(*db_ids):
    return [{"db_id": d, "relevance_score": 0.9} for d in db_ids]


def _isolated(**over):
    base = {
        "zone_clarification_allowed": True,
        "is_composite": False,
        "conversation_context": None,
        "original_user_query": "OS 종류, OS 버전 및 OS패치버전을 확인하시오",
        "user_query": "OS 종류 확인",
        "parsed_requirements": {
            "original_query": "OS 종류 확인",
            "query_targets": ["os_type", "os_version"],
            "filter_conditions": [],
        },
    }
    base.update(over)
    return base


_TASK = {"task_id": "t1", "agent": "data_query", "sub_query": "OS 종류 확인"}


class TestZonePostGateTask:
    """트랙 A(subagents) 후단 게이트 — §4.2 비발동 목록의 결정적 판정."""

    def _call(self, isolated=None, targets=None, task=None, **kw):
        from src.orchestration.subagents import _zone_clarification_or_none_task

        return _zone_clarification_or_none_task(
            task or _TASK,
            isolated if isolated is not None else _isolated(),
            targets if targets is not None else _targets(*sorted(_POLESTAR)),
            db_pinned=kw.get("db_pinned", False),
            db_succeeded=kw.get("db_succeeded", False),
            app_config=_post_gate_config(),
        )

    def test_zoneless_data_query_fanout_triggers(self):
        """실측 재현: 위치어 없는 'OS 확인' 첫 턴이 3개 존 팬아웃 → 역질문 발동."""
        payload = self._call()
        assert payload is not None
        assert payload["kind"] == "zone_select"
        assert payload["original_query"] == "OS 종류, OS 버전 및 OS패치버전을 확인하시오"
        assert [o["db_id"] for o in payload["options"]] == _ACTIVE

    def test_non_interactive_channel_skips(self):
        """§4.3-3: 배치·평가·API 직접 호출(플래그 미주입)은 기존 폴백 유지."""
        assert self._call(isolated=_isolated(zone_clarification_allowed=False)) is None

    def test_location_signal_skips(self):
        assert self._call(
            isolated=_isolated(original_user_query="공동존 김포 전체 서버 OS 확인")
        ) is None

    def test_hostname_filter_skips(self):
        """서버명 지목 질의(§4.2 ⓐ) — 존이 결과에 영향 없음."""
        iso = _isolated()
        iso["parsed_requirements"]["filter_conditions"] = [
            {"field": "hostname", "op": "=", "value": "webdb01"}
        ]
        assert self._call(isolated=iso) is None

    def test_demonstrative_filter_does_not_skip(self):
        """지시어 값("해당 서버")은 실제 식별자가 아님 — 발동 유지."""
        iso = _isolated()
        iso["parsed_requirements"]["filter_conditions"] = [
            {"field": "hostname", "op": "=", "value": "해당 서버"}
        ]
        assert self._call(isolated=iso) is not None

    def test_previous_db_ids_skips(self):
        """후속 턴(직전 DB 존재)은 승계 계열이 처리(§4.2 승계 우선)."""
        iso = _isolated(conversation_context={"previous_db_ids": ["polestar_cm_gp"]})
        assert self._call(isolated=iso) is None

    def test_pinned_or_succeeded_skips(self):
        assert self._call(db_pinned=True) is None
        assert self._call(db_succeeded=True) is None

    def test_composite_plan_skips(self):
        assert self._call(isolated=_isolated(is_composite=True)) is None

    def test_alarm_query_skips(self):
        task = {"task_id": "t1", "agent": "alarm_query", "sub_query": "알람 조회"}
        assert self._call(task=task) is None

    def test_no_query_targets_skips(self):
        """조회 대상 필드 미파싱(잡담성 오분류) — 과잉 역질문 방지."""
        iso = _isolated()
        iso["parsed_requirements"]["query_targets"] = []
        assert self._call(isolated=iso) is None

    def test_non_polestar_target_skips(self):
        assert self._call(targets=_targets("default")) is None

    def test_single_polestar_arbitrary_zone_triggers(self):
        """LLM이 임의 단일 존만 골라도(후속1 실측 형태: 임의 b0) 신호 없으면 발동."""
        assert self._call(targets=_targets("polestar_b0")) is not None


class TestZonePostGateRouter:
    """레거시(semantic_router) 후단 게이트 — 트랙 A와 판정 대칭."""

    def _call(self, state=None, targets=None, user_specified=None):
        from src.routing.semantic_router import _zone_clarification_or_none_router

        base_state = {
            "zone_clarification_allowed": True,
            "conversation_context": None,
            "user_query": "OS 종류, OS 버전 및 OS패치버전을 확인하시오",
            "parsed_requirements": {
                "query_targets": ["os_type"],
                "filter_conditions": [],
            },
        }
        if state:
            base_state.update(state)
        return _zone_clarification_or_none_router(
            base_state,
            targets if targets is not None else _targets(*sorted(_POLESTAR)),
            user_specified,
            _post_gate_config(),
        )

    def test_zoneless_fanout_triggers(self):
        payload = self._call()
        assert payload is not None and payload["kind"] == "zone_select"

    def test_channel_flag_required(self):
        assert self._call(state={"zone_clarification_allowed": None}) is None

    def test_user_specified_db_skips(self):
        assert self._call(user_specified="polestar_b0") is None

    def test_location_term_skips(self):
        assert self._call(state={"user_query": "여의도 서버 OS 확인"}) is None

    def test_previous_db_ids_skips(self):
        assert self._call(
            state={"conversation_context": {"previous_db_ids": ["polestar_b0"]}}
        ) is None


class TestReplannerZoneShortCircuit:
    """존 역질문 턴은 재계획 금지 — 재조회 후속이 역질문을 덮는 것 차단(FIX-22 동형)."""

    @pytest.mark.asyncio
    async def test_zone_clarification_result_stops_replan(self):
        from src.orchestration.replanner import replanner

        llm = MagicMock()
        llm.ainvoke = MagicMock(side_effect=AssertionError("LLM이 호출되면 안 됨"))
        state = {
            "replan_count": 0,
            "replan_history": [],
            "task_plan": [{"task_id": "t1", "agent": "data_query", "status": "completed"}],
            "task_results": {
                "t1": {
                    "final_response": "조회할 존이 지정되지 않았습니다...",
                    "zone_clarification": {"kind": "zone_select"},
                }
            },
            "user_query": "OS 확인",
        }
        out = await replanner(
            state, llm=llm, app_config=SimpleNamespace(max_replan=3)
        )
        assert out["needs_replan"] is False


class TestZoneClarificationResultShape:
    """게이트 발동 시 task 결과 shape — DB 승격 오염 차단(체크포인트 위생)."""

    @pytest.mark.asyncio
    async def test_data_query_returns_clarification_without_db_promotion(self, monkeypatch):
        import src.orchestration.subagents as sub

        async def fake_classify(llm, q, cfg):
            return _targets(*sorted(_POLESTAR))

        monkeypatch.setattr(sub, "classify_dbs", fake_classify)
        cfg = _post_gate_config()
        cfg.polestar_rest = SimpleNamespace(realtime_usage_enabled=False)

        task = dict(_TASK)
        isolated = _isolated()
        isolated.update({
            "selected_db_ids": None,
            "realtime_usage_intent": False,
        })
        out = await sub.run_data_query_pipeline(
            task, isolated, llm=MagicMock(), app_config=cfg
        )
        assert out["zone_clarification"]["kind"] == "zone_select"
        assert out["final_response"]  # 비-UI 폴백 문구
        # DB 승격 차단: 임의 분류 결과가 previous_db_ids로 남지 않도록 target_db_ids 부재
        assert "target_db_ids" not in out


class TestResultAggregatorZonePromotion:
    """단일 task의 zone_clarification이 top-level로 승격되는지 고정."""

    @pytest.mark.asyncio
    async def test_finalize_task_carries_zone_clarification(self):
        from src.orchestration.result_aggregator import _finalize_task

        task = {"task_id": "t1", "agent": "data_query", "order": 0, "sub_query": "OS 확인"}
        res = {
            "final_response": "조회할 존이 지정되지 않았습니다...",
            "zone_clarification": {"kind": "zone_select"},
        }
        out = await _finalize_task(
            task, res, {"user_query": "OS 확인"}, MagicMock(), MagicMock()
        )
        assert out["zone_clarification"] == {"kind": "zone_select"}
        assert out["text"] == "조회할 존이 지정되지 않았습니다..."


class TestZoneResumeTurnLimit:
    """존 선택 재개 턴 LIMIT 전량 상향 (D-150 후속1) — 100건 절단 방지.

    존 역질문은 존 단위 전량 조회에서만 발동하므로 재개 턴은 전량 상향이 기본.
    미상향 시 "모든/전체" 표면어 없는 질의에서 few-shot 말미 캡(FETCH FIRST 100)
    모방이 교정되지 않았다(2026-08-04 라이브 실측: 은행존 VM 100건 절단).
    """

    def _build(self, query, selected, checkpoint):
        from src.api.routes.query import _build_turn_input_state
        from src.api.schemas import QueryRequest

        body = QueryRequest(query=query, selected_db_ids=selected)
        return _build_turn_input_state(body, "t-1", checkpoint, {})

    def test_followup_resume_turn_uplifts_limit(self):
        delta = self._build(
            "OS 종류, OS 버전 및 OS패치버전을 확인하시오",
            ["polestar_b0", "polestar_cm_gp"],
            {},  # 후단 게이트 재개 턴(체크포인트 존재)
        )
        assert delta["resolved_limit"] == 100_000

    def test_first_turn_resume_uplifts_limit(self):
        """pre-gate 재개 턴은 파이프라인 미실행이라 첫 턴(체크포인트 없음)으로 도착."""
        state = self._build(
            "모든 서버들의 OS 종류를 확인해줘",
            ["polestar_cm_gp", "polestar_cm_yd"],
            None,
        )
        assert state["resolved_limit"] == 100_000

    def test_explicit_count_wins(self):
        delta = self._build(
            "서버 100건만 OS 종류 확인", ["polestar_b0"], {},
        )
        assert delta["resolved_limit"] == 100

    def test_non_resume_turn_unchanged(self):
        delta = self._build("OS 종류 확인", None, {})
        assert delta["resolved_limit"] is None
