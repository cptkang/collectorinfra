"""작업 유형 분류 사전 실험 검증 (plans/121 TP-0.8 · `--decomposition --node --type-experiment`).

- **초안 프롬프트**: 현행 분해 프롬프트에 **삽입만**(스크립트 실험 상수 · 제품 프롬프트 불변) ·
  유형 12종(§4.2) · 답변 영역은 레지스트리 렌더 · 슬롯은 파서 필드. 초안 2(기본)는 확장 4키를
  「출력 형식」 골격 자체에 넣고(§14.2 재개 조건), 초안 1(「출력 형식」 앞 절)은 비교용으로
  재현한다.
- **LLM 1회**: 계약 되먹임 재요청 없음(G-21 ⓐ) — 노드 모드와 달리 재요청 조건에서도 1회.
- **채점**: 다단계 3종 정밀도 · 12종 혼동 행렬 · 틀린 루틴 적용 · 확장 출력 형식
  오류율(현행 무효·실패 정의 병기) · tasks node/edge F1(노드 모드 채점기 재사용) · pass^k ·
  채점 불가 종류.
- **골드**: 기대 유형 라벨은 실험 카탈로그 안 · 사용자 검수 전 표지.

실 LLM 호출 0건 — 대본 LLM(덕 타이핑)과 FabriX KBGenAI 목업(HTTP 경계만 교체)만 쓴다. 네트워크 0.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.mocks import fabrix_kbgenai_mock as fx

_REPO = Path(__file__).resolve().parents[2]


def _harness() -> Any:
    spec = importlib.util.spec_from_file_location(
        "eval_routing_type_experiment", _REPO / "scripts" / "eval_routing.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _harness()
IP = importlib.import_module("src.orchestration.intent_planner")

#: 계획서 §4.2 작업 유형 12종(G-2 초안) — 실험 카탈로그가 이 목록과 같아야 한다.
_PLAN_TYPES = [
    "lookup", "rank", "aggregate", "trend", "compare", "select_then_detail", "cross_domain",
    "realtime_inspect", "document_fill", "admin_op", "usage_help", "free_composite",
]
_AREA = sorted(H._area_codes())[0]


# ── 대본 LLM · 설정 ─────────────────────────────────────────────────


class _ScriptLLM:
    """질의(사람 메시지 마지막 줄) → 응답 목록(호출마다 차례로 · 마지막 응답 반복).

    예외 인스턴스면 raise한다. `KBGenAIChat`이 아니므로 빈 assistant 턴이 끼지 않는다.
    노드 모드의 되먹임 재요청은 사람 메시지 말미에 「## 재요청 사유」를 덧붙이므로 그 앞을 본다.
    """

    def __init__(self, script: dict[str, list[Any]]):
        self._script = {k: list(v) for k, v in script.items()}
        self._seen: dict[str, int] = {}
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(list(messages))
        body = str(messages[-1].content).split("\n\n## 재요청 사유")[0]
        query = body.rsplit("\n", 1)[-1]
        answers = self._script[query]
        n = self._seen.get(query, 0)
        self._seen[query] = n + 1
        answer = answers[min(n, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=answer)


def _cfg(*, dag: bool = True, replan: bool = True) -> Any:
    """검증 대상 필드를 명시한 설정 사본(.env 누수 차단) — 되먹임 재요청 조건은 켠다."""
    from src.config import load_config

    base = load_config().model_copy(deep=True)
    return base.model_copy(update={
        "structured_output_backend": "none",
        "composite": base.composite.model_copy(update={
            "plan_dag_validation_enabled": dag, "sequential_replan_enabled": replan,
            "task_frame_enabled": False, "investigation_enabled": False,
        }),
        "router": base.router.model_copy(update={"capability_ownership_enabled": False}),
    })


def _tasks(*tasks: tuple[str, str, list[str]]) -> list[dict[str, Any]]:
    return [{"task_id": tid, "agent": agent, "sub_query": f"{tid} 질의",
             "depends_on": list(inputs), "input_from": list(inputs)}
            for tid, agent, inputs in tasks]


def _ext(request_type: Any, /, *tasks: tuple[str, str, list[str]],
         drop: tuple[str, ...] = (), **over: Any) -> str:
    """확장 출력 JSON — 기본은 형식 정상(모든 키 · 열거 안 값). `over`가 키를 덮는다."""
    body: dict[str, Any] = {
        "request_type": request_type,
        "required_areas": [_AREA],
        "step_slots": {tid: [] for tid, _, _ in tasks},
        "requested_source": None,
        "clarification_needed": None,
        "tasks": _tasks(*tasks),
    }
    body.update(over)
    for key in drop:
        body.pop(key)
    return json.dumps(body, ensure_ascii=False)


_SEQ_Q = "CPU가 높은 서버를 찾아 그 서버들의 메모리 사용률"   # 순차 표지 — 노드 모드는 재요청한다
_SINGLE_Q = "전체 서버 OS 버전 목록"
_USAGE_Q = "이 에이전트 사용법 알려줘"                          # 사전 처리 ③.8 단락

_SEQ_ITEM: dict[str, Any] = {
    "id": "t-seq", "query": _SEQ_Q, "critical": "sequential",
    "expect": {"min_tasks": 2, "agents": ["data_query", "data_query"],
               "edges": [["data_query", "data_query"]], "request_type": "select_then_detail"},
}
_SINGLE_ITEM: dict[str, Any] = {
    "id": "t-single", "query": _SINGLE_Q, "critical": "single",
    "expect": {"min_tasks": 1, "agents": ["data_query"], "edges": [], "request_type": "lookup"},
}
_TaskSpec = tuple[str, str, list[str]]
_SEQ_TASKS: tuple[_TaskSpec, ...] = (("t1", "data_query", []), ("t2", "data_query", ["t1"]))
_GOOD_SEQ = _ext("select_then_detail", *_SEQ_TASKS,
                 step_slots={"t1": ["filter_conditions"], "t2": ["query_targets"]})
_GOOD_SINGLE = _ext("lookup", ("t1", "data_query", []))


def _exp(items: list[dict[str, Any]], llm: Any, *, cfg: Any = None,
         repeat: int = 1) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    results = asyncio.run(H.run_type_experiment(items, llm=llm, cfg=cfg or _cfg(),
                                                repeat=repeat))
    return results, H.summarize_type_experiment(results, repeat=repeat)


# ── 초안 프롬프트 ───────────────────────────────────────────────────


_EXT_KEYS = ("request_type", "required_areas", "step_slots", "requested_source")


def _output_format_skeleton(prompt: str) -> dict[str, Any]:
    """「출력 형식」 절 첫 JSON 골격을 파싱한다(`{{` 표기를 풀어서)."""
    body = prompt.split("## 출력 형식\n", 1)[1].split("## 예시\n", 1)[0]
    raw = body.split("```json\n", 1)[1].split("\n```", 1)[0]
    parsed: dict[str, Any] = json.loads(raw.replace("{{", "{").replace("}}", "}"))
    return parsed


class TestDraftPrompt:
    def test_draft2_is_default_and_draft1_is_kept(self) -> None:
        assert H.TYPE_EXPERIMENT_DRAFT_ID == H.TYPE_EXPERIMENT_DRAFT_2 == "tp08-draft-2"
        assert H.TYPE_EXPERIMENT_DRAFTS == ("tp08-draft-1", "tp08-draft-2")
        cfg = _cfg()
        assert H.render_type_experiment_prompt(cfg) == H.render_type_experiment_prompt(
            cfg, H.TYPE_EXPERIMENT_DRAFT_2)
        with pytest.raises(ValueError, match="초안"):
            H.render_type_experiment_prompt(cfg, "tp08-draft-9")

    def test_draft1_section_is_inserted_only_before_output_format(self) -> None:
        """초안 1 재현 — 종전 렌더 그대로(「출력 형식」 앞 절 하나 · 골격 불변)."""
        cfg = _cfg()
        base = IP._planner_system_prompt(cfg)
        section = H._type_experiment_section()
        draft = H.render_type_experiment_prompt(cfg, H.TYPE_EXPERIMENT_DRAFT_1)
        assert draft.replace(section, "", 1) == base                 # 삽입만 — 기존 줄 변경 0
        assert draft.index(section) == base.index("## 출력 형식\n")  # 「출력 형식」 바로 앞
        assert "### 예시 3-1 " in draft and "### 예시 4 " in draft     # 앵커·D-086 예시 보존
        assert "이 절이 아래 「출력 형식」보다 우선합니다" in section
        assert set(_output_format_skeleton(draft)) == {"clarification_needed", "tasks"}

    def test_draft2_puts_extended_keys_in_output_format_skeleton(self) -> None:
        """초안 2(§14.2 재개 조건) — 확장 4키가 「출력 형식」 골격 JSON 자체에 있다."""
        cfg = _cfg()
        draft = H.render_type_experiment_prompt(cfg, H.TYPE_EXPERIMENT_DRAFT_2)
        skeleton = _output_format_skeleton(draft)
        assert list(skeleton) == [*_EXT_KEYS, "clarification_needed", "tasks"]
        assert skeleton["request_type"] in H._TYPE_CODES
        assert set(skeleton["required_areas"]) <= H._area_codes()
        assert H.check_extended_fields(skeleton, H._area_codes()) == ([], [])
        # 골격이 하나뿐이다 — 앞 절에는 키 모양 JSON을 두지 않는다
        assert draft.count('"request_type":') == 1
        assert draft.index('"request_type":') > draft.index("## 출력 형식\n")
        assert "```json" not in H._type_experiment_section_v2()
        # 주의 목록 한 줄 — 예시가 `tasks` 모양만 보여 줘도 네 키는 항상
        rules = draft.split("## 출력 형식\n", 1)[1].split("## 예시\n", 1)[0]
        assert H._TYPE_EXP2_RULE in rules
        assert rules.index("- `tasks`는 최소 1개 이상이어야 합니다.\n") < rules.index(
            H._TYPE_EXP2_RULE)

    def test_draft2_is_insertion_only(self) -> None:
        cfg = _cfg()
        base = IP._planner_system_prompt(cfg)
        draft = H.render_type_experiment_prompt(cfg, H.TYPE_EXPERIMENT_DRAFT_2)
        section = H._type_experiment_section_v2()
        stripped = (draft.replace(section, "", 1)
                    .replace(H._type_exp2_skeleton_keys(), "", 1)
                    .replace(H._TYPE_EXP2_RULE, "", 1))
        assert stripped == base                                      # 기존 줄 변경 0
        assert draft.index(section) == base.index("## 출력 형식\n")
        assert "### 예시 3-1 " in draft and "### 예시 4 " in draft

    def test_draft2_missing_skeleton_anchor_is_an_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(IP, "_planner_system_prompt", lambda cfg: "## 출력 형식\n골격 없음\n")
        with pytest.raises(RuntimeError, match="앵커"):
            H.render_type_experiment_prompt(_cfg(), H.TYPE_EXPERIMENT_DRAFT_2)
        # 초안 1은 골격 앵커를 쓰지 않는다
        assert H.render_type_experiment_prompt(_cfg(), H.TYPE_EXPERIMENT_DRAFT_1)

    def test_catalog_is_the_plan_twelve_types(self) -> None:
        assert [code for code, _ in H._TYPE_CATALOG] == _PLAN_TYPES
        assert H._MULTI_STEP_TYPES == ("compare", "select_then_detail", "cross_domain")
        section = H._type_experiment_section()
        for code in _PLAN_TYPES:
            assert section.count(f"- `{code}`:") == 1, code

    def test_areas_are_rendered_from_registry(self) -> None:
        from src.routing.capability_ownership import known_capability_codes

        assert H._area_codes() == known_capability_codes()
        section = H._type_experiment_section()
        for code in known_capability_codes():
            assert f"- `{code}`:" in section, code

    def test_slot_vocabulary_is_parser_fields(self) -> None:
        from src.nodes.schemas import ParsedRequirements

        assert set(H._TYPE_EXP_SLOTS) <= set(ParsedRequirements.model_fields)

    @pytest.mark.parametrize("draft_id", ["tp08-draft-1", "tp08-draft-2"])
    def test_product_prompt_untouched_and_draft_deterministic(self, draft_id: str) -> None:
        from src.prompts import intent_planner as prompts

        before = hashlib.sha256(prompts.INTENT_PLANNER_SYSTEM_TEMPLATE.encode()).hexdigest()
        cfg = _cfg()
        base = IP._planner_system_prompt(cfg)
        base_sha = hashlib.sha256(base.encode()).hexdigest()
        first = H.render_type_experiment_prompt(cfg, draft_id)
        assert H.render_type_experiment_prompt(cfg, draft_id) == first   # 요청마다 접두 불변
        assert hashlib.sha256(prompts.INTENT_PLANNER_SYSTEM_TEMPLATE.encode()).hexdigest() == before
        assert hashlib.sha256(IP._planner_system_prompt(cfg).encode()).hexdigest() == base_sha
        meta = H.type_experiment_prompt_meta(cfg, draft_id)
        assert meta["draft"] == draft_id
        assert meta["chars"] == len(first) and meta["base_chars"] == len(base)
        assert meta["added_chars"] == len(first) - len(base) > 0 and meta["types"] == 12
        assert meta["sha256"] == hashlib.sha256(first.encode()).hexdigest()[:16]

    def test_missing_anchor_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(IP, "_planner_system_prompt", lambda cfg: "출력 형식 없음")
        with pytest.raises(RuntimeError, match="앵커"):
            H.render_type_experiment_prompt(_cfg())

    def test_human_content_carries_frozen_parsed_and_ends_with_query(self) -> None:
        item = {"id": "t", "query": "q 원문",
                "parsed": {"query_targets": ["CPU"], "time_range": {"relative": "최근 1주일"}}}
        state = H.build_node_state(item)
        human = H.type_experiment_human_content("q 원문", state["parsed_requirements"], "맥락\n")
        assert human.startswith("맥락\n## 입력 해석(parsed_requirements)\n")
        assert human.rsplit("\n", 1)[-1] == "q 원문"
        block = json.loads(human.split("```json\n", 1)[1].split("\n```", 1)[0])
        # 기본값과 다른 필드만 — original_query·output_format 기본값은 싣지 않는다
        assert block == {"query_targets": ["CPU"], "time_range": {"relative": "최근 1주일"}}

    def test_kbgenai_gets_empty_assistant_turn_like_product(self) -> None:
        from langchain_core.messages import AIMessage

        kb_llm = fx.make_llm()  # type: ignore[no-untyped-call]
        kb = H.type_experiment_messages(kb_llm, "sys", "q")
        plain = H.type_experiment_messages(_ScriptLLM({}), "sys", "q")
        assert [type(m).__name__ for m in kb] == ["SystemMessage", "AIMessage", "HumanMessage"]
        assert isinstance(kb[1], AIMessage) and kb[1].content == ""
        assert [type(m).__name__ for m in plain] == ["SystemMessage", "HumanMessage"]


# ── 확장 필드 판정 ──────────────────────────────────────────────────


class TestExtendedFieldCheck:
    @pytest.mark.parametrize("over,drop,missing,bad", [
        ({}, (), [], []),
        ({}, ("request_type",), ["request_type"], []),
        ({}, ("required_areas", "step_slots", "requested_source"),
         ["required_areas", "step_slots", "requested_source"], []),
        ({"request_type": "선별 후 상세"}, (), [], ["request_type"]),
        ({"request_type": 3}, (), [], ["request_type"]),
        ({"required_areas": "area"}, (), [], ["required_areas"]),
        ({"required_areas": ["nope"]}, (), [], ["required_areas"]),
        ({"required_areas": []}, (), [], []),
        ({"step_slots": {"t9": ["time_range"]}}, (), [], ["step_slots"]),
        ({"step_slots": {"t1": ["nope"]}}, (), [], ["step_slots"]),
        ({"step_slots": ["t1"]}, (), [], ["step_slots"]),
        ({"requested_source": ""}, (), [], ["requested_source"]),
        ({"requested_source": "프로메테우스"}, (), [], []),
    ])
    def test_missing_and_out_of_enum(
        self,
        over: dict[str,
        Any],
        drop: tuple[str,
        ...],
        missing: list[str],
        bad: list[str],
    ) -> None:
        data = json.loads(_ext("lookup", ("t1", "data_query", []), drop=drop, **over))
        assert H.check_extended_fields(data, H._area_codes()) == (missing, bad)

    def test_task_without_id_uses_product_default_id(self) -> None:
        data = {"request_type": "lookup", "required_areas": [], "requested_source": None,
                "step_slots": {"t1": ["limit"]}, "tasks": [{"agent": "data_query"}]}
        assert H.check_extended_fields(data, H._area_codes()) == ([], [])

    def test_status_follows_planner_parse_category(self) -> None:
        areas = H._area_codes()
        assert H.inspect_type_output(None, "call_error", areas)["status"] == "call_error"
        assert H.inspect_type_output("x", "parse", areas)["status"] == "parse_invalid"
        ok = H.inspect_type_output(_GOOD_SINGLE, None, areas)
        assert ok["status"] == "ok" and ok["request_type"] == "lookup"


# ── 실험 실행 · 채점 ────────────────────────────────────────────────


class TestTypeExperimentScoring:
    def test_correct_extended_output_passes(self) -> None:
        llm = _ScriptLLM({_SEQ_Q: [_GOOD_SEQ]})
        results, s = _exp([_SEQ_ITEM], llm)
        r = results[0]
        assert r["llm_attempts"] == 1 and r["outcome"] == "passed" and r["passed"] is True
        assert r["request_type_correct"] is True and r["type_output"]["status"] == "ok"
        assert r["type_output"]["step_slots"] == {"t1": ["filter_conditions"],
                                                  "t2": ["query_targets"]}
        assert s["format"]["invalid_or_failed_rate"] == 0.0
        assert s["format"]["extended_error_rate"] == 0.0
        std = s["request_type"]["multi_step"]["select_then_detail"]
        assert std["precision"] == 1.0 and std["recall"] == 1.0
        assert s["tasks"]["node"]["f1"] == 1.0 and s["tasks"]["edge"]["f1"] == 1.0
        assert s["pass_hat_k"]["rate"] == 1.0 and s["type_pass_hat_k"]["rate"] == 1.0
        # 사람 메시지 = 동결 파서 블록 + 원문, 시스템 = 초안
        sys_msg, human = llm.calls[0][0].content, llm.calls[0][-1].content
        assert "## 작업 유형(request_type)과 확장 출력" in sys_msg
        assert "## 입력 해석(parsed_requirements)" in human and human.endswith(_SEQ_Q)

    @pytest.mark.parametrize("draft_id,skeleton_keys", [
        ("tp08-draft-1", {"clarification_needed", "tasks"}),
        ("tp08-draft-2", {*_EXT_KEYS, "clarification_needed", "tasks"}),
    ])
    def test_run_sends_selected_draft(self, draft_id: str, skeleton_keys: set[str]) -> None:
        llm = _ScriptLLM({_SEQ_Q: [_GOOD_SEQ]})
        results = asyncio.run(H.run_type_experiment([_SEQ_ITEM], llm=llm, cfg=_cfg(),
                                                    draft=draft_id))
        sys_msg = llm.calls[0][0].content
        assert sys_msg == H.render_type_experiment_prompt(_cfg(), draft_id)
        assert set(_output_format_skeleton(sys_msg)) == skeleton_keys
        assert results[0]["passed"] is True

    def test_one_llm_call_where_node_mode_re_requests(self) -> None:
        """순차 표지 + 단일 계획 — 노드 모드는 되먹임 재요청(2회), 실험은 1회(G-21 ⓐ)."""
        single_for_seq = _ext("select_then_detail", ("t1", "data_query", []))
        node_llm = _ScriptLLM({_SEQ_Q: [json.dumps({"tasks": _tasks(("t1", "data_query", []))})]})
        node = asyncio.run(H.run_decomposition_node([_SEQ_ITEM], llm=node_llm, cfg=_cfg()))
        assert node[0]["llm_attempts"] == 2 and len(node_llm.calls) == 2

        llm = _ScriptLLM({_SEQ_Q: [single_for_seq]})
        results, s = _exp([_SEQ_ITEM], llm)
        r = results[0]
        assert len(llm.calls) == 1 and r["llm_attempts"] == 1
        assert r["outcome"] == "failed" and r["request_type_correct"] is True
        assert r["passed"] is False                     # 구조 오답이면 run 불합격

    def test_legacy_shape_is_field_error_not_invalid(self) -> None:
        """현행형 `{"tasks"}`만 — 현행 정의로는 유효, 확장 정의로는 필드 누락 4."""
        legacy = json.dumps({"tasks": _tasks(*_SEQ_TASKS)})
        results, s = _exp([_SEQ_ITEM], _ScriptLLM({_SEQ_Q: [legacy]}))
        r = results[0]
        assert r["type_output"]["status"] == "field_error"
        assert r["type_output"]["missing"] == list(H._TYPE_EXP_FIELDS)
        assert r["outcome"] == "passed" and r["passed"] is False   # 구조는 그대로 채점된다
        f = s["format"]
        assert f["invalid_or_failed"] == 0 and f["invalid_or_failed_rate"] == 0.0
        assert f["extended_errors"] == 1 and f["extended_error_rate"] == 1.0
        assert f["missing_field"] == {k: 1 for k in H._TYPE_EXP_FIELDS}
        assert s["request_type"]["confusion"] == {"select_then_detail": {"(무효)": 1}}
        assert s["tasks"]["node"]["f1"] == 1.0

    def test_non_json_is_parse_invalid(self) -> None:
        results, s = _exp([_SINGLE_ITEM], _ScriptLLM({_SINGLE_Q: ["죄송합니다"]}))
        assert results[0]["type_output"]["status"] == "parse_invalid"
        assert results[0]["outcome"] == "format_error"
        assert s["format"]["parse_invalid"] == 1 and s["format"]["invalid_or_failed_rate"] == 1.0
        assert s["request_type"]["confusion"] == {"lookup": {"(무효)": 1}}

    def test_call_error_is_measurement_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        results, s = _exp([_SINGLE_ITEM], _ScriptLLM({_SINGLE_Q: [RuntimeError("down")]}))
        r = results[0]
        assert r["type_output"]["status"] == "call_error" and r["outcome"] == "error"
        assert r["request_type_scored"] is False
        assert r["request_type_unscored_kind"] == "call_error"
        assert s["format"]["call_errors"] == 1 and s["format"]["invalid_or_failed"] == 1
        assert s["tasks"]["errors"] == 1 and s["request_type"]["scored_runs"] == 0
        assert H._verdict_type_experiment(s) == 1
        assert "측정 무효" in capsys.readouterr().err

    def test_wrong_routine_counts_against_multi_step_precision(self) -> None:
        wrong = _ext("select_then_detail", ("t1", "data_query", []))
        results, s = _exp([_SINGLE_ITEM, _SEQ_ITEM],
                          _ScriptLLM({_SINGLE_Q: [wrong], _SEQ_Q: [_GOOD_SEQ]}))
        rt = s["request_type"]
        std = rt["multi_step"]["select_then_detail"]
        assert (std["tp"], std["fp"], std["fn"]) == (1, 1, 0) and std["precision"] == 0.5
        assert rt["multi_step_family"]["precision"] == 0.5 and rt["wrong_routine"] == 1
        assert rt["multi_step"]["compare"]["precision"] is None     # 예측 0 — 0점이 아니다
        assert rt["confusion"] == {"lookup": {"select_then_detail": 1},
                                   "select_then_detail": {"select_then_detail": 1}}
        assert rt["accuracy"] == 0.5 and H._verdict_type_experiment(s) == 0

    def test_preprocessed_item_skips_llm_and_is_unscored(self) -> None:
        usage = {"id": "t-usage", "query": _USAGE_Q, "suite": "node", "critical": "single",
                 "expect": {"min_tasks": 1, "agents": ["general_inference"], "edges": [],
                            "request_type": "usage_help"}}
        llm = _ScriptLLM({_SINGLE_Q: [_GOOD_SINGLE]})
        results, s = _exp([usage, _SINGLE_ITEM], llm)
        r = results[0]
        assert r["outcome"] == "preprocessed" and r["passed"] is None
        assert r["request_type_unscored_kind"] == "preprocessed"
        assert r["plan_path"] == "usage_help"
        assert [c[-1].content.rsplit("\n", 1)[-1] for c in llm.calls] == [_SINGLE_Q]
        assert s["preprocessed"] == {"runs": 1, "items": 1, "plan_paths": {"usage_help": 1},
                                     "expected_types_not_scored": ["usage_help"]}
        assert s["llm_runs"] == 1 and s["format"]["runs"] == 1
        assert s["pass_hat_k"]["items"] == 1 and s["tasks"]["total"] == 1

    def test_declared_unscored_is_observed_and_format_counted(self) -> None:
        item = {"id": "t-u", "query": _SINGLE_Q, "suite": "node", "unscored": "처리기 없음"}
        out = _ext("cross_domain", ("t1", "alarm_query", []), requested_source="프로메테우스")
        results, s = _exp([item], _ScriptLLM({_SINGLE_Q: [out]}))
        r = results[0]
        assert r["outcome"] == "unscored" and r["passed"] is None
        assert r["request_type_unscored_kind"] == "declared_unscored"
        assert r["type_output"]["requested_source"] == "프로메테우스"
        assert s["format"]["runs"] == 1 and s["request_type"]["scored_runs"] == 0
        assert s["request_type"]["observed_unscored"] == {"cross_domain": 1}
        assert s["pass_hat_k"]["items"] == 0

    def test_item_without_expected_type_scores_format_and_structure_only(self) -> None:
        item = {**_SINGLE_ITEM, "id": "t-nt",
                "expect": {k: v for k, v in _SINGLE_ITEM["expect"].items()
                           if k != "request_type"}}
        results, s = _exp([item], _ScriptLLM({_SINGLE_Q: [_GOOD_SINGLE]}))
        r = results[0]
        assert r["request_type_unscored_kind"] == "no_expected_type"
        assert "request_type_correct" not in r and r["passed"] is True
        assert s["request_type"]["unscored"] == {"no_expected_type": 1}
        assert s["request_type"]["observed_unscored"] == {"lookup": 1}

    def test_repeat_reports_pass_hat_k_and_type_pass_hat_k(self) -> None:
        flaky = [_GOOD_SINGLE, _ext("rank", ("t1", "data_query", [])), _GOOD_SINGLE]
        results, s = _exp([_SINGLE_ITEM, _SEQ_ITEM],
                          _ScriptLLM({_SINGLE_Q: flaky, _SEQ_Q: [_GOOD_SEQ]}), repeat=3)
        assert s["runs"] == 3 and s["llm_runs"] == 6
        assert s["type_pass_hat_k"] == {"k": 3, "items": 2, "passed_all_runs": 1, "rate": 0.5}
        assert s["pass_hat_k"] == {"k": 3, "items": 2, "passed_all_runs": 1, "rate": 0.5}
        assert s["request_type"]["confusion"]["lookup"] == {"lookup": 2, "rank": 1}
        assert sorted({r["run"] for r in results}) == [1, 2, 3]

    def test_conversation_context_block_is_kept_before_parsed_block(self) -> None:
        ctx = {"turn_count": 2, "previous_location": "", "previous_db_ids": [],
               "previous_entities": [], "previous_results_summary": "직전 요약"}
        item = {**_SINGLE_ITEM, "id": "t-ctx", "suite": "node",
                "state": {"conversation_context": ctx}}
        llm = _ScriptLLM({_SINGLE_Q: [_GOOD_SINGLE]})
        _exp([item], llm)
        human = llm.calls[0][-1].content
        assert human.startswith("## 이전 대화 맥락")
        assert human.index("## 이전 대화 맥락") < human.index("## 입력 해석(parsed_requirements)")

    def test_module_attributes_restored_even_on_node_exception(

        self,

        monkeypatch: pytest.MonkeyPatch,

    ) -> None:
        originals = (IP._llm_decompose, IP._decompose_once)

        def boom(*_a: Any, **_k: Any) -> Any:
            raise ValueError("메시지 조립 실패")

        monkeypatch.setattr(H, "type_experiment_messages", boom)
        results, s = _exp([_SINGLE_ITEM], _ScriptLLM({}))
        assert results[0]["outcome"] == "error" and "ValueError" in results[0]["error"]
        assert (IP._llm_decompose, IP._decompose_once) == originals
        assert s["tasks"]["errors"] == 1 and H._verdict_type_experiment(s) == 1


class TestThroughRealClient:
    """FabriX KBGenAI 목업(실제 클라이언트 경로) — 목업에 분해 대본이 없어 LLM 경로는 파싱 불가."""

    def _run(self, fault: str, on_request: Any = None) -> tuple[list[dict[str, Any]],
                                                                  dict[str, Any]]:
        items = H.load_gold(H._DECOMP_GOLD, suites=None)
        with fx.mock_kbgenai(fault=fault, on_request=on_request):
            llm = fx.make_llm()  # type: ignore[no-untyped-call]
            return _exp(items, llm)

    def test_router_shaped_output_is_parse_invalid(self) -> None:
        payloads: list[dict[str, Any]] = []
        results, s = self._run(fx.FAULT_NONE, on_request=payloads.append)
        assert {r["id"] for r in results if r["outcome"] == "preprocessed"} == {"d-102", "d-103"}
        f = s["format"]
        assert f["runs"] == s["llm_runs"] == len(payloads) == 7        # 항목당 1회
        assert f["parse_invalid"] == 7 and f["invalid_or_failed_rate"] == 1.0
        assert f["call_errors"] == 0 and H._verdict_type_experiment(s) == 0
        assert all(set(row) == {"(무효)"} for row in s["request_type"]["confusion"].values())
        assert "## 작업 유형(request_type)과 확장 출력" in str(payloads[0].get("systemPrompt"))

    def test_error_status_is_measurement_error(self) -> None:
        _, s = self._run(fx.FAULT_ERROR_STATUS)
        assert s["format"]["call_errors"] == s["llm_runs"] > 0
        assert H._verdict_type_experiment(s) == 1


# ── 골드 ────────────────────────────────────────────────────────────


class TestGold:
    def test_gold_is_valid_for_the_experiment(self) -> None:
        items = H.load_gold(H._DECOMP_GOLD, suites=None)
        assert H.validate_type_experiment_gold(items) == []
        labeled = {i["id"]: i["expect"]["request_type"] for i in items
                   if "request_type" in (i.get("expect") or {})}
        assert set(labeled.values()) <= set(_PLAN_TYPES)
        assert labeled == {"d-001": "select_then_detail", "d-004": "lookup", "d-005": "lookup",
                           "d-101": "select_then_detail", "d-102": "usage_help"}

    def test_labels_carry_unreviewed_marker(self) -> None:
        text = H._DECOMP_GOLD.read_text(encoding="utf-8")
        assert "모든 request_type 라벨은 사용자 검수 전이다" in text
        for item in H.load_gold(H._DECOMP_GOLD):          # 이번에 단 core 라벨 — 근거 note
            if "request_type" in (item.get("expect") or {}):
                assert "사용자 검수 전" in item["note"] and "§4." in item["note"], item["id"]

    def test_unknown_type_code_is_rejected(self) -> None:
        item = {"id": "t-x", "query": "q", "suite": "node",
                "expect": {"min_tasks": 1, "request_type": "select_detail"}}
        errs = H.validate_type_experiment_gold([item])
        assert any("실험 유형 카탈로그" in e for e in errs)

    def test_function_level_mode_still_reads_core_only(self) -> None:
        assert [i["id"] for i in H.load_gold(H._DECOMP_GOLD)] == [
            "d-001", "d-002", "d-003", "d-004", "d-005"]


# ── CLI ─────────────────────────────────────────────────────────────


class TestCli:
    def _main(self, monkeypatch: Any, *argv: str) -> int:
        monkeypatch.setattr(sys, "argv", ["eval_routing.py", *argv])
        return int(H.main())

    @pytest.mark.parametrize("argv", [
        ("--type-experiment",),
        ("--decomposition", "--type-experiment"),
        ("--decomposition", "--node", "--type-experiment", "--tolerate", "1"),
        ("--decomposition", "--node", "--type-draft", "tp08-draft-1"),
        ("--decomposition", "--node", "--type-experiment", "--type-draft", "tp08-draft-9"),
    ])
    def test_invalid_combinations_exit_2(
        self,
        monkeypatch: pytest.MonkeyPatch,
        argv: tuple[str,
        ...],
    ) -> None:
        with pytest.raises(SystemExit) as exc:
            self._main(monkeypatch, *argv)
        assert exc.value.code == 2

    def test_dry_run_reports_draft_without_calls(

        self,

        monkeypatch: pytest.MonkeyPatch,

        capsys: pytest.CaptureFixture[str],

    ) -> None:
        def refuse() -> None:
            raise AssertionError("dry-run은 게이트에 닿지 않는다")

        monkeypatch.setattr(H, "_require_optin", refuse)
        assert self._main(monkeypatch, "--decomposition", "--node", "--type-experiment",
                          "--dry-run") == 0
        out = capsys.readouterr().out
        assert "[type-experiment] 초안 tp08-draft-2" in out and "[dry-run] 실 호출 없음." in out
        assert "기대 유형 라벨 5건 — 다단계 2 · 사용자 검수 전" in out
        # 비교용 초안 1 재현
        assert self._main(monkeypatch, "--decomposition", "--node", "--type-experiment",
                          "--type-draft", "tp08-draft-1", "--dry-run") == 0
        assert "[type-experiment] 초안 tp08-draft-1" in capsys.readouterr().out

    def test_mock_run_prints_summary(

        self,

        monkeypatch: pytest.MonkeyPatch,

        capsys: pytest.CaptureFixture[str],

    ) -> None:
        monkeypatch.setattr(H, "_require_optin", lambda: pytest.fail("목업은 게이트 무관"))
        assert self._main(monkeypatch, "--decomposition", "--node", "--type-experiment",
                          "--mock") == 0
        captured = capsys.readouterr()
        assert '"mode": "type_experiment"' in captured.out
        assert "유형 채점 대상" in captured.err

    def test_real_calls_stay_behind_optin_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """실 호출은 기존 `_require_optin`(D-127 · 로컬 MLX 모드 D-240)을 먼저 통과해야 한다."""
        def refuse() -> None:
            raise SystemExit(2)

        monkeypatch.setattr(H, "_require_optin", refuse)
        monkeypatch.setattr(H, "run_type_experiment",
                            lambda *a, **k: pytest.fail("게이트 전에 실행되면 안 된다"))
        with pytest.raises(SystemExit) as exc:
            self._main(monkeypatch, "--decomposition", "--node", "--type-experiment")
        assert exc.value.code == 2
