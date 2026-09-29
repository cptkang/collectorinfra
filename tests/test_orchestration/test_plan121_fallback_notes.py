"""plans/121 TP-1.6 — 침묵 폴백 사유화(§12.4 · §12.5 · §12.6 ④).

단언하는 것:
    F1 분류 폴백(1·2단 핸들러) — 소유 플래그 off에서도 `routing_fallback` 노트. 폴백 DB가 위치 힌트
       고정·승계로 바뀌었거나 인가 밖이거나 존 역질문 턴이면 싣지 않는다. 원인은 클래스명만
    F2 소유 플래그 on 경로는 종전과 바이트 동일(노트 dict·문구·가장자리 동작)
    F3 3단 라우터 대칭 — off 폴백 노트 · 존 역질문·인가 밖·힌트 고정은 제외 ·
       응답 말미 렌더 경로 존재
    F4 1단 — 같은 핸들러·같은 집계기라 운영 응답에도 노트가 붙는다(의도된 변경)
    F5 분해 폴백 — 관측 필드는 항상, 사용자 노트는 순차 표지가 있을 때만 ·
       기존 사유 노트와 중복 없음
    F6 2단 재계획 — 상한 노트(3단과 같은 문구) · 평가 실패 노트 · 거짓 상한 노트 없음(TP-1.1)
    F7 재계획 의미 선언 — 분류 폴백은 안내성 · 성공 턴 평가 LLM 호출 수 비증가(§12.6 ④)
    F8 존 역질문 턴에는 분해 폴백 노트를 붙이지 않는다
    F9 문구 규율 — 하네스 표지어 0 · D-241 판정 어휘와 코드 충돌 0

실 LLM 호출 0(D-127) — 대역 LLM·함수 대역만 쓴다.
"""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import HumanMessage

from src.config import AppConfig, MultiDBConfig, RouterConfig
from src.orchestration.result_aggregator import _apply_dependency_notes
from src.orchestration.schemas import DecomposedPlan
from src.routing import capability_ownership as own
from src.state import create_followup_input, create_initial_state
from src.utils.prior_dependency import (
    CROSS_SYSTEM_NOTE_KINDS,
    NOTE_DECOMPOSE,
    NOTE_ROUTING_FALLBACK,
)

sa = importlib.import_module("src.orchestration.subagents")
sr = importlib.import_module("src.routing.semantic_router")
ip = importlib.import_module("src.orchestration.intent_planner")
rp = importlib.import_module("src.orchestration.replanner")
t3 = importlib.import_module("src.orchestration.tier3_plan")
og = importlib.import_module("src.nodes.output_generator")

_POLESTAR_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
_ACTIVE = [*_POLESTAR_ZONES, "itam"]
_SEQ_QUERY = "CPU 높은 서버를 찾아서 그 서버들의 메모리도 보여줘"
_PLAIN_QUERY = "전체 서버의 OS 종류"
_SECRET = "secret-url?token=x"

# 종전 X-T3 문구(소유 플래그 on) — 바이트 비교 기준.
_ON_LLM_ERROR_DETAIL = (
    "조회 대상 시스템 분류에 실패해(RuntimeError) 첫 활성 DB(polestar_b0)로 조회했습니다 — "
    "질의에 맞는 시스템이 아닐 수 있습니다."
)
_ON_EMPTY_DETAIL = (
    "조회 대상 시스템을 분류하지 못해(유효한 분류 결과 없음) 첫 활성 DB(polestar_b0)로 "
    "조회했습니다 — 질의에 맞는 시스템이 아닐 수 있습니다."
)


def _cfg(*, ownership: bool) -> AppConfig:
    """검증 대상 필드를 명시한 설정(.env 누수 차단)."""
    return AppConfig(
        multi_db=MultiDBConfig(active_db_ids_csv=",".join(_ACTIVE)),
        router=RouterConfig(
            capability_ownership_enabled=ownership,
            two_stage_enabled=False,
            unknown_enabled=False,
            early_stop_enabled=False,
        ),
        enable_semantic_routing=True,
    )


@pytest.fixture(autouse=True)
def _no_cache_manager(monkeypatch):
    """분류기의 DB 설명 로드는 외부 캐시를 친다 — 실패 경로(디버그 로그 후 계속)로 고정한다."""

    def _boom(*_a, **_kw):
        raise RuntimeError("cache disabled in test")

    monkeypatch.setattr("src.schema_cache.cache_manager.get_cache_manager", _boom)


def _classify_outcome(monkeypatch, module: Any, outcome: Any) -> None:
    async def _fake(*_a, **_kw):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(module, "_llm_classify", _fake)


_EMPTY = {"intent": "data_query", "databases": [], "dropped": []}


# ──────────────────────────────────────────────
# F1 · F2 · F4 — 1·2단 핸들러(`run_data_query_pipeline`)
# ──────────────────────────────────────────────


@pytest.fixture
def pipeline(monkeypatch):
    """실행부만 대역으로 바꾼다 — 분류(`classify_dbs`)·대상 결정·노트 조립은 실제로 돈다."""
    rec: dict[str, Any] = {"executed": []}

    async def _single(s, llm, app_config):
        rec["executed"].append([t["db_id"] for t in s["target_databases"]])
        return {}

    async def _noop(*_a, **_kw):
        return {}

    async def _organize(s, llm=None, app_config=None):
        return {"organized_data": {"rows": [{"hostname": "web-01"}]}}

    monkeypatch.setattr(sa, "_run_single_db_pipeline", _single)
    monkeypatch.setattr(sa, "multi_db_executor", _noop)
    monkeypatch.setattr(sa, "result_merger", _noop)
    monkeypatch.setattr(sa, "result_organizer", _organize)
    monkeypatch.setattr(sa, "emit_step", _noop)
    monkeypatch.setattr(sa, "_zone_clarification_before_classify", lambda *_a, **_kw: None)
    return rec


def _isolated(query: str = "조회", **extra: Any) -> dict[str, Any]:
    parsed = {"original_query": query, "query_targets": ["서버"], "filter_conditions": []}
    return {"user_query": query, "parsed_requirements": parsed, "is_composite": False, **extra}


async def _run(cfg: AppConfig, *, task_id: str = "t1", **isolated_extra: Any) -> dict:
    task = {"task_id": task_id, "agent": "data_query", "sub_query": "조회"}
    return await sa.run_data_query_pipeline(
        task, _isolated(**isolated_extra), llm=object(), app_config=cfg,
    )


def _fallback_notes(res: dict) -> list[dict]:
    return [n for n in res.get("dependency_notes") or [] if n.get("kind") == NOTE_ROUTING_FALLBACK]


class TestHandlerFallbackNote:
    async def test_classify_marks_fallback_regardless_of_flag(self, monkeypatch):
        _classify_outcome(monkeypatch, sa, _EMPTY)
        for ownership in (False, True):
            targets = await sa.classify_dbs(object(), "q", _cfg(ownership=ownership))
            assert targets[0]["routing_fallback"]["reason"] == own.REASON_NO_CLASSIFICATION

    @pytest.mark.parametrize(
        ("outcome", "reason", "detail"),
        [
            (RuntimeError(_SECRET), own.REASON_LLM_ERROR, _ON_LLM_ERROR_DETAIL),
            (_EMPTY, own.REASON_NO_CLASSIFICATION, _ON_EMPTY_DETAIL),
        ],
    )
    async def test_off_emits_same_note_as_on(self, monkeypatch, pipeline, outcome, reason, detail):
        """★ off도 폴백을 알린다 — 정상 폴백에서는 on과 같은 노트(같은 빌더·같은 문구)."""
        _classify_outcome(monkeypatch, sa, outcome)
        off = await _run(_cfg(ownership=False))
        on = await _run(_cfg(ownership=True))
        assert _fallback_notes(off) == _fallback_notes(on)
        (note,) = _fallback_notes(off)
        assert (note["reason"], note["task_id"], note["db_id"]) == (reason, "t1", "polestar_b0")
        assert note["detail"] == detail  # 종전 X-T3 문구 바이트 동일
        assert "token" not in json.dumps(off, ensure_ascii=False, default=str)  # 예외 원문 비노출
        assert all("routing_fallback" not in t for t in off["source"])  # 표지는 떼어 낸다

    async def test_off_skips_note_when_hint_pinning_replaced_target(self, monkeypatch, pipeline):
        _classify_outcome(monkeypatch, sa, _EMPTY)
        pinned = [{"db_id": "polestar_cm_gp", "relevance_score": 1.0, "sub_query_context": "q",
                   "user_specified": True, "reason": "힌트"}]
        monkeypatch.setattr(sa, "_apply_turn_hint_pinning", lambda *_a, **_kw: (pinned, True))
        res = await _run(_cfg(ownership=False))
        assert res["target_db_ids"] == ["polestar_cm_gp"]
        assert not _fallback_notes(res)

    async def test_on_keeps_note_when_hint_pinned_unchanged_behavior(self, monkeypatch, pipeline):
        """소유 플래그 on은 종전 그대로 — 힌트 고정 뒤에도 노트가 남는다(가장자리 동작 불변)."""
        _classify_outcome(monkeypatch, sa, _EMPTY)
        pinned = [{"db_id": "polestar_cm_gp", "relevance_score": 1.0, "sub_query_context": "q",
                   "user_specified": True, "reason": "힌트"}]
        monkeypatch.setattr(sa, "_apply_turn_hint_pinning", lambda *_a, **_kw: (pinned, True))
        res = await _run(_cfg(ownership=True))
        assert [n["detail"] for n in _fallback_notes(res)] == [_ON_EMPTY_DETAIL]

    async def test_off_skips_note_when_succession_replaced_target(self, monkeypatch, pipeline):
        _classify_outcome(monkeypatch, sa, _EMPTY)
        inherited = [{"db_id": "polestar_cm_yd", "relevance_score": 1.0, "sub_query_context": "q",
                      "user_specified": False, "reason": "승계"}]
        monkeypatch.setattr(sa, "_apply_db_succession", lambda *_a, **_kw: (inherited, True))
        res = await _run(_cfg(ownership=False))
        assert res["target_db_ids"] == ["polestar_cm_yd"]
        assert not _fallback_notes(res)

    async def test_off_zone_question_turn_has_no_note(self, monkeypatch, pipeline):
        _classify_outcome(monkeypatch, sa, _EMPTY)
        monkeypatch.setattr(
            sa, "_zone_clarification_or_none_task",
            lambda *_a, **_kw: {"question": "어느 존을 조회할까요?", "options": []},
        )
        res = await _run(_cfg(ownership=False))
        assert res["zone_clarification"] and "dependency_notes" not in res

    async def test_off_unauthorized_fallback_db_is_never_named(self, monkeypatch, pipeline):
        """D-264 — 폴백 DB가 인가 밖이면 조회 거부로 끝나고 노트(그 DB 이름)가 없다."""
        _classify_outcome(monkeypatch, sa, _EMPTY)
        res = await _run(_cfg(ownership=False), allowed_db_ids=["polestar_cm_gp"], user_role="user")
        assert res.get("routing_intent") == "access_denied"
        assert "polestar_b0" not in json.dumps(res, ensure_ascii=False)

    async def test_first_tier_aggregator_renders_note(self, monkeypatch, pipeline):
        """★ 1단 — 도구 결과(collector)가 같은 집계기로 가서 운영 응답 말미에 노트가 붙는다."""
        _classify_outcome(monkeypatch, sa, RuntimeError(_SECRET))
        res = await _run(_cfg(ownership=False), task_id="tool_data_query_1")
        task = {"task_id": "tool_data_query_1", "agent": "data_query", "status": "completed"}
        out = _apply_dependency_notes(
            {"final_response": "본문"},
            {"task_plan": [task], "task_results": {"tool_data_query_1": res}},  # type: ignore[typeddict-item]
        )
        assert "## 순차 처리 경과" in out["final_response"]
        assert f"- [tool_data_query_1] {_ON_LLM_ERROR_DETAIL}" in out["final_response"]


# ──────────────────────────────────────────────
# F3 — 3단 라우터(`semantic_router`)
# ──────────────────────────────────────────────


def _router_state(**extra: Any) -> dict[str, Any]:
    st = create_initial_state(user_query="질의")
    st["parsed_requirements"] = {"query_targets": ["서버"], "original_query": "질의"}
    st.update(extra)  # type: ignore[typeddict-item]
    return dict(st)


async def _route(monkeypatch, cfg: AppConfig, outcome: Any, **state_extra: Any) -> dict:
    monkeypatch.setattr(sr, "load_config", lambda: cfg)
    _classify_outcome(monkeypatch, sr, outcome)
    return await sr.semantic_router(
        _router_state(**state_extra), llm=object(), app_config=cfg,  # type: ignore[arg-type]
    )


class TestRouterFallbackNote:
    @pytest.mark.parametrize(
        ("outcome", "reason", "detail"),
        [
            (RuntimeError(_SECRET), own.REASON_LLM_ERROR, _ON_LLM_ERROR_DETAIL),
            ({**_EMPTY, "chain": []}, own.REASON_NO_CLASSIFICATION, _ON_EMPTY_DETAIL),
        ],
    )
    async def test_off_reports_fallback_like_on(self, monkeypatch, outcome, reason, detail):
        off = await _route(monkeypatch, _cfg(ownership=False), outcome)
        on = await _route(monkeypatch, _cfg(ownership=True), outcome)
        assert [t["db_id"] for t in off["target_databases"]] == ["polestar_b0"]
        assert off["dependency_notes"] == on["dependency_notes"]  # on 노트 바이트 동일
        (note,) = off["dependency_notes"]
        assert (note["kind"], note["reason"], note["detail"]) == (
            NOTE_ROUTING_FALLBACK, reason, detail,
        )
        # off는 소유 키를 싣지 않는다(노트만)
        assert not {"required_capabilities", "capability_chain"} & set(off)

    async def test_off_zone_question_has_no_note(self, monkeypatch):
        monkeypatch.setattr(
            sr, "_zone_clarification_or_none_router",
            lambda *_a, **_kw: {"question": "어느 존을 조회할까요?", "options": []},
        )
        out = await _route(monkeypatch, _cfg(ownership=False), RuntimeError("x"))
        assert out["routing_intent"] == "zone_clarification"
        assert "dependency_notes" not in out

    async def test_off_unauthorized_fallback_db_not_named(self, monkeypatch):
        out = await _route(
            monkeypatch, _cfg(ownership=False), RuntimeError("x"),
            allowed_db_ids=["polestar_cm_gp"], user_role="user",
        )
        assert "dependency_notes" not in out

    async def test_off_hint_pinned_has_no_note(self, monkeypatch):
        pinned = [{"db_id": "polestar_cm_gp", "relevance_score": 1.0, "sub_query_context": "q",
                   "user_specified": True, "reason": "힌트"}]
        monkeypatch.setattr(sr, "_pin_turn_location_hints", lambda *_a, **_kw: (pinned, True))
        out = await _route(monkeypatch, _cfg(ownership=False), RuntimeError("x"))
        assert [t["db_id"] for t in out["target_databases"]] == ["polestar_cm_gp"]
        assert "dependency_notes" not in out

    async def test_off_existing_request_notes_are_kept(self, monkeypatch):
        prior = {"kind": NOTE_DECOMPOSE, "task_id": None, "detail": "앞 노트"}
        out = await _route(
            monkeypatch, _cfg(ownership=False), RuntimeError("x"), dependency_notes=[prior],
        )
        assert out["dependency_notes"][0] == prior
        assert out["dependency_notes"][1]["kind"] == NOTE_ROUTING_FALLBACK

    def test_third_tier_renders_fallback_kind_at_response_end(self):
        """3단 단일·멀티 DB 경로는 기존 렌더(`CROSS_SYSTEM_NOTE_KINDS`)로 응답 말미에 싣는다."""
        assert NOTE_ROUTING_FALLBACK in CROSS_SYSTEM_NOTE_KINDS
        note = own.routing_fallback_note(own.REASON_NO_CLASSIFICATION, db_id="polestar_b0")
        text = og._append_cross_system_notes("본문", {"dependency_notes": [note]})
        assert text == f"본문\n\n**[조회 시스템 경과]**\n- {_ON_EMPTY_DETAIL}"


# ──────────────────────────────────────────────
# F5 — 분해 폴백(`intent_planner`)
# ──────────────────────────────────────────────


def _dec_cfg(cfg: AppConfig, *, replan_on: bool = False) -> AppConfig:
    """분해 경로 설정 고정 — JSON 경로 · 순차 계약 on/off · DAG off · 소유·프레임 off."""
    cfg.structured_output_backend = "none"
    cfg.composite.sequential_replan_enabled = replan_on
    cfg.composite.plan_dag_validation_enabled = False
    cfg.composite.task_frame_enabled = False
    cfg.router.capability_ownership_enabled = False
    return cfg


def _llm_raising(*errors: Exception) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.side_effect = list(errors)
    return llm


def _llm_text(*contents: str) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.side_effect = [MagicMock(content=c) for c in contents]
    return llm


async def _plan(llm: Any, query: str, cfg: AppConfig) -> dict[str, Any]:
    return await ip.intent_planner(create_initial_state(user_query=query), llm=llm, app_config=cfg)


class TestDecomposeFallback:
    async def test_single_intent_fallback_is_observation_only(self, mock_config):
        out = await _plan(_llm_raising(RuntimeError(_SECRET)), _PLAIN_QUERY, _dec_cfg(mock_config))
        assert out[ip.DECOMPOSE_FALLBACK_KEY] == ip.DECOMPOSE_FALLBACK_LLM_ERROR
        assert "dependency_notes" not in out  # 단일 의도 — 사용자 노트 없음(소음)
        assert len(out["task_plan"]) == 1 and out["task_plan"][0]["sub_query"] == _PLAIN_QUERY

    async def test_sequential_marker_fallback_gets_user_note(self, mock_config):
        out = await _plan(_llm_raising(RuntimeError(_SECRET)), _SEQ_QUERY, _dec_cfg(mock_config))
        assert out[ip.DECOMPOSE_FALLBACK_KEY] == ip.DECOMPOSE_FALLBACK_LLM_ERROR
        (note,) = out["dependency_notes"]
        assert (note["kind"], note["reason"]) == (NOTE_DECOMPOSE, ip.REASON_DECOMPOSE_FALLBACK)
        assert note["detail"].startswith("질의 분해 호출이 실패해(RuntimeError) 순차 분해가")
        assert "token" not in note["detail"]

    async def test_malformed_output_with_marker(self, mock_config):
        out = await _plan(_llm_text("JSON이 아닌 평문"), _SEQ_QUERY, _dec_cfg(mock_config))
        assert out[ip.DECOMPOSE_FALLBACK_KEY] == ip.DECOMPOSE_FALLBACK_MALFORMED
        assert out["dependency_notes"][0]["detail"].startswith("질의 분해 결과의 형식이 맞지 않아")

    async def test_no_valid_task_is_malformed(self, mock_config):
        out = await _plan(
            _llm_text(json.dumps({"tasks": ["문자열"]})), _PLAIN_QUERY, _dec_cfg(mock_config),
        )
        assert out[ip.DECOMPOSE_FALLBACK_KEY] == ip.DECOMPOSE_FALLBACK_MALFORMED

    def test_empty_structured_result_is_marked(self):
        plan = ip._plan_from_model(DecomposedPlan.model_construct(tasks=[]), "q")
        assert plan["fallback_cause"]["code"] == ip.DECOMPOSE_FALLBACK_EMPTY_STRUCTURED
        assert len(plan["tasks"]) == 1

    async def test_existing_sequential_note_is_not_duplicated(self, mock_config):
        """순차 계약 on — 되먹임 재요청까지 실패하면 종전 `sequential_not_applied` 한 건만."""
        llm = _llm_raising(RuntimeError("a"), RuntimeError("b"))
        out = await _plan(llm, _SEQ_QUERY, _dec_cfg(mock_config, replan_on=True))
        assert llm.ainvoke.await_count == 2
        assert [n["reason"] for n in out["dependency_notes"]] == ["sequential_not_applied"]
        assert out[ip.DECOMPOSE_FALLBACK_KEY] == ip.DECOMPOSE_FALLBACK_LLM_ERROR

    async def test_recovered_retry_is_not_a_fallback(self, mock_config):
        chain = json.dumps({"tasks": [
            {"task_id": "t1", "agent": "data_query", "sub_query": "CPU 높은 서버"},
            {"task_id": "t2", "agent": "data_query", "sub_query": "그 서버들의 메모리",
             "depends_on": ["t1"], "input_from": ["t1"]},
        ]}, ensure_ascii=False)
        llm = AsyncMock()
        llm.ainvoke.side_effect = [RuntimeError("a"), MagicMock(content=chain)]
        out = await _plan(llm, _SEQ_QUERY, _dec_cfg(mock_config, replan_on=True))
        assert len(out["task_plan"]) == 2
        assert ip.DECOMPOSE_FALLBACK_KEY not in out and "dependency_notes" not in out

    async def test_shared_fallback_object_is_not_mutated(self, mock_config):
        fallback = {"tasks": [{"task_id": "t1", "agent": "data_query", "sub_query": "q"}],
                    "clarification_needed": None}
        got = await ip._decompose_once(
            _llm_raising(RuntimeError("x")), [HumanMessage(content="q")], "q",
            _dec_cfg(mock_config), fallback,
        )
        assert "fallback_cause" not in fallback
        assert got["tasks"][0] is not fallback["tasks"][0]

    def test_state_key_is_request_scoped(self):
        assert create_initial_state(user_query="q")["decompose_fallback"] is None
        assert create_followup_input("q")["decompose_fallback"] is None

    def test_plan_summary_carries_code_only_when_fallback(self):
        from src.api.routes.query import _plan_summary_field

        base = {"plan_path": "llm_decompose",
                "task_plan": [{"task_id": "t1", "agent": "data_query", "order": 1}],
                "task_results": {}}
        assert "decompose_fallback" not in _plan_summary_field(base)["plan_summary"]
        summary = _plan_summary_field({**base, "decompose_fallback": "llm_error"})["plan_summary"]
        assert summary["decompose_fallback"] == "llm_error"


# ──────────────────────────────────────────────
# F6 · F7 · F8 — 재계획기
# ──────────────────────────────────────────────

_NO_FOLLOWUP = {"needs_followup": False, "reason": "충분", "new_tasks": []}


def _llm_json(payload: dict[str, Any]) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    return llm


def _pin(cfg: AppConfig, *, max_replan: int = 3) -> AppConfig:
    cfg.server.answer_reserve_sec = 0
    cfg.replan_budget_prompt_enabled = False
    cfg.max_replan = max_replan
    return cfg


def _task(tid: str = "t1") -> dict[str, Any]:
    return {"task_id": tid, "agent": "data_query", "sub_query": "김포 서버 CPU 상위 10건",
            "depends_on": [], "input_from": [], "order": int(tid[1:]), "status": "completed"}


def _rows() -> dict[str, Any]:
    return {"query_results": [{"hostname": "web-01"}],
            "organized_data": {"rows": [{"hostname": "web-01"}], "is_sufficient": True}}


def _empty() -> dict[str, Any]:
    return {"query_results": [], "organized_data": {"rows": []}}


def _rstate(result: dict[str, Any], *, query: str = "김포 서버 CPU 상위 10건",
            **over: Any) -> dict[str, Any]:
    st = dict(create_initial_state(user_query=query))
    st["task_plan"] = [_task()]
    st["task_results"] = {"t1": result}
    st.update(over)
    return st


_ROUTING_NOTE = own.routing_fallback_note(own.REASON_NO_CLASSIFICATION, db_id="polestar_b0",
                                          task_id="t1")
_DECOMPOSE_NOTE = ip.decompose_fallback_note(ip.DECOMPOSE_FALLBACK_LLM_ERROR, "RuntimeError")


class TestReplanSemantics:
    def test_declarations(self):
        assert NOTE_ROUTING_FALLBACK in rp._INFO_NOTE_KINDS  # 안내성 — 성공 종료 유지
        assert NOTE_DECOMPOSE not in rp._INFO_NOTE_KINDS  # 미완 유지(발동 조건으로 호출 수 불변)

    async def test_success_turn_with_routing_fallback_makes_no_llm_call(self, mock_config):
        """★ §12.6 ④ — 분류 폴백 노트가 붙은 성공 턴도 평가 LLM 없이 끝난다."""
        llm = _llm_json(_NO_FOLLOWUP)
        state = _rstate({**_rows(), "dependency_notes": [_ROUTING_NOTE]})
        out = await rp.replanner(state, llm=llm, app_config=_pin(mock_config))
        llm.ainvoke.assert_not_awaited()
        assert out == {"needs_replan": False, "replan_history": [], "current_node": "replanner"}

    async def test_decompose_note_does_not_add_llm_calls(self, mock_config):
        """★ §12.6 ④ — 분해 폴백 노트는 순차 표지 턴에만 붙고, 그 턴은 노트 없이도 평가를 탄다."""
        calls = []
        for notes in (None, [_DECOMPOSE_NOTE]):
            llm = _llm_json(_NO_FOLLOWUP)
            state = _rstate(_rows(), query=_SEQ_QUERY, dependency_notes=notes)
            await rp.replanner(state, llm=llm, app_config=_pin(mock_config))
            calls.append((llm.ainvoke.await_count, llm.ainvoke.await_args.args[0][-1].content))
        assert calls[0] == calls[1] and calls[0][0] == 1  # 호출 수·평가 입력 동일


class TestReplanStopNotes:
    async def test_cap_note_matches_third_tier(self, mock_config):
        cfg = _pin(mock_config)
        llm = _llm_json(_NO_FOLLOWUP)
        out = await rp.replanner(_rstate(_empty(), replan_count=3), llm=llm, app_config=cfg)
        llm.ainvoke.assert_not_awaited()
        assert out["needs_replan"] is False
        note = out["dependency_notes"][-1]
        assert note["detail"] == "재계획 상한(3회)에 도달해 지금까지의 결과로 답했습니다."
        tier3 = await t3.replan(
            {"user_query": "q", "replan_count": 3, "replan_history": [],  # type: ignore[arg-type]
             "task_plan": [], "task_results": {}},
            llm=AsyncMock(), app_config=cfg,
        )
        assert tier3["dependency_notes"][0] == note  # 3단과 같은 종류·사유·문구

    async def test_cap_before_first_round_has_no_note(self, mock_config):
        out = await rp.replanner(
            _rstate(_empty(), replan_count=0), llm=_llm_json(_NO_FOLLOWUP),
            app_config=_pin(mock_config, max_replan=0),
        )
        assert "dependency_notes" not in out

    async def test_cap_reached_on_success_is_silent(self, mock_config):
        out = await rp.replanner(
            _rstate(_rows(), replan_count=3), llm=_llm_json(_NO_FOLLOWUP),
            app_config=_pin(mock_config),
        )
        assert out == {"needs_replan": False, "replan_history": [], "current_node": "replanner"}

    async def test_previous_turn_cap_gives_no_false_note(self, mock_config):
        """TP-1.1 — 앞 턴이 상한이어도 후속 턴 입력이 재계획 횟수를 비워 거짓 상한 노트가 없다."""
        turn1 = _rstate(_empty(), replan_count=3)
        merged = {**turn1, **create_followup_input("다음 질문")}
        merged["task_plan"] = [_task()]
        merged["task_results"] = {"t1": _empty()}
        llm = _llm_json(_NO_FOLLOWUP)
        out = await rp.replanner(merged, llm=llm, app_config=_pin(mock_config))
        llm.ainvoke.assert_awaited_once()
        assert not [n for n in out.get("dependency_notes") or [] if n.get("reason") == "replan_cap"]

    async def test_eval_exception_note_uses_class_name_only(self, mock_config):
        llm = AsyncMock()
        llm.ainvoke.side_effect = RuntimeError(_SECRET)
        out = await rp.replanner(_rstate(_empty()), llm=llm, app_config=_pin(mock_config))
        assert out["needs_replan"] is False and "task_plan" not in out
        (note,) = out["dependency_notes"]
        assert (note["kind"], note["reason"]) == (NOTE_DECOMPOSE, rp.REASON_REPLAN_EVAL_FAILED)
        assert "RuntimeError" in note["detail"] and "token" not in note["detail"]

    async def test_eval_invalid_response_note(self, mock_config):
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="JSON이 아닌 평문")
        out = await rp.replanner(_rstate(_empty()), llm=llm, app_config=_pin(mock_config))
        assert out["dependency_notes"][0]["detail"] == (
            "추가 조회가 필요한지 판단하는 단계의 응답 형식이 맞지 않아 "
            "지금까지의 결과로 답했습니다."
        )

    async def test_no_followup_decision_stays_silent(self, mock_config):
        out = await rp.replanner(
            _rstate(_empty()), llm=_llm_json(_NO_FOLLOWUP), app_config=_pin(mock_config),
        )
        assert "dependency_notes" not in out


class TestZoneTurn:
    async def test_zone_wait_drops_decompose_fallback_note_only(self, mock_config):
        gate = {"kind": "gate", "task_id": "t1", "detail": "선행 0건"}
        state = _rstate({"zone_clarification": {"question": "어느 존?"}},
                        dependency_notes=[_DECOMPOSE_NOTE, gate], replan_count=3)
        out = await rp.replanner(state, llm=_llm_json(_NO_FOLLOWUP), app_config=_pin(mock_config))
        assert out["dependency_notes"] == [gate]  # 상한 노트도 붙지 않는다(존 대기가 먼저)

    async def test_zone_wait_without_fallback_note_is_unchanged(self, mock_config):
        state = _rstate({"zone_clarification": {"question": "어느 존?"}})
        out = await rp.replanner(state, llm=_llm_json(_NO_FOLLOWUP), app_config=_pin(mock_config))
        assert out == {"needs_replan": False, "replan_history": [], "current_node": "replanner"}


# ──────────────────────────────────────────────
# F9 — 문구 규율 · 어휘
# ──────────────────────────────────────────────


def _new_texts() -> list[str]:
    return [
        own.routing_fallback_note(own.REASON_LLM_ERROR, db_id="d", cause="ReadTimeout")["detail"],
        own.routing_fallback_note(own.REASON_NO_CLASSIFICATION, db_id="d")["detail"],
        *(ip.decompose_fallback_note(code, "ReadTimeout")["detail"] for code in (
            ip.DECOMPOSE_FALLBACK_LLM_ERROR, ip.DECOMPOSE_FALLBACK_MALFORMED,
            ip.DECOMPOSE_FALLBACK_EMPTY_STRUCTURED,
        )),
        rp._replan_cap_note(3)["detail"],
        rp._eval_failure_note("ReadTimeout")["detail"],
        rp._eval_failure_note("")["detail"],
    ]


def test_texts_avoid_harness_markers_and_timeout_phrases():
    from scripts.scenario.assertions import (
        _CORRECT_MARKERS,
        _GUIDE_MARKERS,
        _PARTIAL_MARKERS,
        _REFUSE_MARKERS,
    )

    markers = (*_REFUSE_MARKERS, *_GUIDE_MARKERS, *_CORRECT_MARKERS, *_PARTIAL_MARKERS)
    for text in _new_texts():
        assert not [m for m in markers if m in text], text
        assert "처리 시간이 초과" not in text and "504" not in text  # D-241 timeout 도출 문구


def test_codes_do_not_collide_with_d241_vocabulary():
    d241 = {"invalid", "timeout", "clarify_blocked"}
    codes = {
        ip.DECOMPOSE_FALLBACK_LLM_ERROR, ip.DECOMPOSE_FALLBACK_MALFORMED,
        ip.DECOMPOSE_FALLBACK_EMPTY_STRUCTURED, ip.REASON_DECOMPOSE_FALLBACK,
        rp.REASON_REPLAN_CAP, rp.REASON_REPLAN_EVAL_FAILED,
        own.REASON_LLM_ERROR, own.REASON_NO_CLASSIFICATION,
    }
    assert not codes & d241
