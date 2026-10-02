"""라우팅 평가 하네스 신규 모드 검증 (plans/121 TP-0.2 · TP-0.6).

- **기존 모드 불변**: 정적 core26 · 분해 함수 단위 core 5건의 판정·요약 출력이 바이트 그대로다.
- **노드 모드**(`--decomposition --node`): `intent_planner` 노드 함수 통째 평가 — 사전 처리
  계층 A · 파서 출력 동결 · 상태 주입 · node/edge F1 · 형식 오류율(구조 오답과 분리) · pass^k ·
  작업 유형 채점 불가.
- **런타임 프롬프트 조건 모드**(`--runtime-prompt-conditions`): 활성 DB 집합 · DB 설명 스냅샷 ·
  플래그 기록.
- **채점 불가 표기**: 선언·동적 사유 모두 합격·불합격과 따로 센다.

실 LLM 호출 0건 — 대본 LLM(덕 타이핑)과 FabriX KBGenAI 목업(HTTP 경계만 교체)만 쓴다. 네트워크 0.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.mocks import fabrix_kbgenai_mock as fx

_REPO = Path(__file__).resolve().parents[2]


def _harness():
    spec = importlib.util.spec_from_file_location(
        "eval_routing_modes", _REPO / "scripts" / "eval_routing.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H = _harness()

#: 정적 core26 — 기본 모드가 채점하는 항목. 여기 id가 바뀌면 정적 점수의 모집단이 바뀐 것이다.
_CORE26 = [
    "r-001", "r-002", "r-003", "r-010", "r-011", "r-012", "r-013", "r-020", "r-021", "r-030",
    "r-031", "r-040", "r-050", "r-051", "r-060", "r-061", "r-062", "r-063", "r-064", "r-070",
    "r-071", "r-072", "r-073", "r-074", "r-075", "r-076",
]
_DECOMP_CORE = ["d-001", "d-002", "d-003", "d-004", "d-005"]
_ZONE_SET = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"]


# ── 대본 LLM · 설정 ─────────────────────────────────────────────────


class _ScriptLLM:
    """질의 → 응답 목록(호출마다 차례로 · 마지막 응답 반복). 예외 인스턴스면 raise한다.

    `KBGenAIChat`이 아니므로 분해 메시지에 빈 AIMessage가 끼지 않는다(덕 타이핑 — 구조화
    백엔드 none).
    되먹임 재요청은 사람 메시지 말미에 「## 재요청 사유」를 덧붙이므로 그 앞을 질의로 본다.
    """

    def __init__(self, script: dict[str, list]):
        self._script = {k: list(v) for k, v in script.items()}
        self._seen: dict[str, int] = {}
        self.calls: list[str] = []

    async def ainvoke(self, messages):
        query = str(messages[-1].content).split("\n\n## 재요청 사유")[0]
        self.calls.append(query)
        answers = self._script[query]
        n = self._seen.get(query, 0)
        self._seen[query] = n + 1
        answer = answers[min(n, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return SimpleNamespace(content=answer)


def _plan(*tasks: tuple[str, str, list[str]]) -> str:
    """(task_id, agent, input_from) 목록 → 분해 JSON."""
    return json.dumps({"tasks": [
        {"task_id": tid, "agent": agent, "sub_query": f"{tid} 질의",
         "depends_on": list(inputs), "input_from": list(inputs)}
        for tid, agent, inputs in tasks
    ]}, ensure_ascii=False)


def _cfg(*, dag: bool = True, replan: bool = True):
    """검증 대상 필드를 명시한 설정 사본(.env 누수 차단)."""
    from src.config import load_config

    base = load_config()
    return base.model_copy(update={
        "structured_output_backend": "none",
        "composite": base.composite.model_copy(update={
            "plan_dag_validation_enabled": dag, "sequential_replan_enabled": replan,
            "task_frame_enabled": False, "investigation_enabled": False,
        }),
        "router": base.router.model_copy(update={"capability_ownership_enabled": False}),
    })


_SEQ_Q = "CPU가 높은 서버를 찾아 그 서버들의 메모리 사용률"      # 순차 표지 있음
_SINGLE_Q = "전체 서버 OS 버전 목록"                             # 순차 표지 없음

_SEQ_ITEM = {
    "id": "t-seq", "query": _SEQ_Q, "critical": "sequential",
    "expect": {"min_tasks": 2, "agents": ["data_query", "data_query"],
               "edges": [["data_query", "data_query"]]},
}
_SINGLE_ITEM = {
    "id": "t-single", "query": _SINGLE_Q, "critical": "single",
    "expect": {"min_tasks": 1, "agents": ["data_query"], "edges": []},
}
_GOOD_SEQ = _plan(("t1", "data_query", []), ("t2", "data_query", ["t1"]))


def _node(items, llm, *, cfg=None, repeat=1):
    results = asyncio.run(H.run_decomposition_node(items, llm=llm, cfg=cfg or _cfg(),
                                                   repeat=repeat))
    return results, H.summarize_decomposition_node(results, repeat=repeat)


# ── 기존 모드 불변 ──────────────────────────────────────────────────


class TestLegacyModesUnchanged:
    def test_static_mode_reads_only_core26(self):
        """정적 core 점수의 모집단이 그대로다 — 새 system 항목은 기본 모드에 섞이지 않는다."""
        assert [i["id"] for i in H.load_gold()] == _CORE26
        assert all("suite" not in i and "unscored" not in i for i in H.load_gold())

    def test_function_level_decomposition_reads_only_core(self):
        assert [i["id"] for i in H.load_gold(H._DECOMP_GOLD)] == _DECOMP_CORE

    _ENV = {
        "ROUTER_CAPABILITY_OWNERSHIP_ENABLED": "false", "ROUTER_TWO_STAGE_ENABLED": "false",
        "ROUTER_UNKNOWN_ENABLED": "false", "STRUCTURED_OUTPUT_BACKEND": "none",
        "COMPOSITE_PLAN_DAG_VALIDATION_ENABLED": "true",
        "COMPOSITE_SEQUENTIAL_REPLAN_ENABLED": "true",
        "COMPOSITE_TASK_FRAME_ENABLED": "false", "COMPOSITE_INVESTIGATION_ENABLED": "false",
    }

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        import os

        env = {**os.environ, **self._ENV}
        env.pop("RUN_E2E", None)
        return subprocess.run(
            [sys.executable, str(_REPO / "scripts" / "eval_routing.py"), *args],
            cwd=_REPO, env=env, capture_output=True, text=True, timeout=180,
        )

    #: 세션 시작 커밋 2e635a9(변경 전)의 같은 명령 출력 — 바이트 그대로여야 한다.
    _ROUTING_MOCK_STDOUT = (
        "골든셋 26건 정합성 OK (멀티 DB 4건 포함)\n"
        "[mock] FabriX KBGenAI 목업 · fault=none · 실 호출 0건\n"
        + json.dumps({
            "total": 26, "passed": 26, "intent_match": 26, "multi_db_cases": 4,
            "multi_db_preserved": 4, "score_count": 33,
            "score_distribution": {"0.8~1.0 (확실)": 30, "0.3~0.5 (약함)": 3},
            "below_gate": 0, "dropped_total": 0, "errors": 0, "chain_cases": 7,
            "chain_scored": 0, "chain_matched": 0, "router_unscored_cases": 3,
        }, ensure_ascii=False, indent=2) + "\n"
    )
    _ROUTING_MOCK_STDERR = (
        "  ※ chain 판정 7건 채점 불가 — 라우터 출력에 chain 없음 — "
        "ROUTER_CAPABILITY_OWNERSHIP_ENABLED off(채점 불가)\n"
        "  ※ key_type·probe 판정 3건 — 라우터 단계 채점 불가(H-2 소관)\n"
    )
    _DECOMP_MOCK_STDOUT = (
        "분해 골든셋 5건 정합성 OK (순차 3건 포함)\n"
        "[mock] FabriX KBGenAI 목업 · fault=none · 실 호출 0건\n"
        + json.dumps({
            "total": 5, "passed": 2, "sequential_cases": 3, "sequential_preserved": 0,
            "single_false_split": 0, "degraded": 2, "errors": 0,
        }, ensure_ascii=False, indent=2) + "\n"
    )

    def test_static_mock_output_is_byte_identical(self):
        p = self._cli("--mock")
        assert p.returncode == 0
        assert p.stdout == self._ROUTING_MOCK_STDOUT
        assert p.stderr == self._ROUTING_MOCK_STDERR

    def test_function_level_decomposition_mock_output_is_byte_identical(self):
        """이 출력의 `passed 2`는 **거짓 통과**다(형식 폴백 — 노드 모드가 잡는다).

        그래도 기존 모드는 판정·출력을 바꾸지 않는다(리드 지시 — 기존 모드 불변).
        """
        p = self._cli("--decomposition", "--mock")
        assert p.returncode == 1
        assert p.stdout == self._DECOMP_MOCK_STDOUT


# ── 골드 검증기 ─────────────────────────────────────────────────────


class TestGoldValidation:
    def test_all_routing_suites_valid(self):
        assert H.validate_gold(H.load_gold(suites=None)) == []

    def test_all_decomposition_suites_valid(self):
        assert H.validate_decomposition_gold(H.load_gold(H._DECOMP_GOLD, suites=None)) == []

    def test_system_suite_covers_required_cases(self):
        """Prometheus · 폴스타 REST · ITAM · 기권 · 명시 소스 불가.

        비SQL·기권·불가는 채점 불가로 선언하고 ITAM(SQL)은 채점한다.
        """
        system = {i["critical"]: i for i in H.load_gold(suites=("system",))}
        assert set(system) >= {"system_prometheus", "system_rest", "system_itam",
                               "system_abstain", "system_unavailable"}
        for tag in ("system_prometheus", "system_rest", "system_abstain", "system_unavailable"):
            assert system[tag].get("unscored"), tag
        assert not system["system_itam"].get("unscored")   # SQL 시스템은 채점한다
        assert system["system_itam"]["expect"]["databases"] == ["itam"]

    def test_node_suite_uses_new_fields(self):
        node = {i["id"]: i for i in H.load_gold(H._DECOMP_GOLD, suites=("node",))}
        assert any("parsed" in i for i in node.values())
        assert any(i.get("state") for i in node.values())
        assert any(i.get("unscored") for i in node.values())
        assert any("request_type" in (i.get("expect") or {}) for i in node.values())

    @pytest.mark.parametrize("patch,needle", [
        ({"suite": "extra"}, "suite"),
        ({"suite": "system", "unscored": "  "}, "unscored"),
        ({"unscored": "사유"}, "core 항목은 unscored"),
    ])
    def test_invalid_routing_suite_fields(self, patch, needle):
        item = {"id": "t-x", "query": "q", "expect": {"intent": "data_query"}, **patch}
        errs = H.validate_gold([item])
        assert errs and needle in errs[0]

    def test_unscored_routing_item_may_omit_expect_but_not_query(self):
        assert H.validate_gold([{"id": "t-u", "query": "q", "suite": "system",
                                 "unscored": "사유"}]) == []
        errs = H.validate_gold([{"id": "t-u", "suite": "system", "unscored": "사유"}])
        assert errs == ["t-u: query 없음"]

    @pytest.mark.parametrize("patch,needle", [
        ({"suite": "system"}, "suite"),
        ({"unscored": "사유"}, "core 항목은 unscored"),
        ({"suite": "node", "parsed": {"nope": 1}}, "ParsedRequirements 밖 키"),
        ({"suite": "node", "parsed": {"limit": "많이"}}, "parsed 형식 오류"),
        ({"suite": "node", "parsed": ["x"]}, "parsed는"),
        ({"suite": "node", "state": {"nope": 1}}, "주입할 수 없는 키"),
        ({"suite": "node", "state": {"user_query": "x"}}, "주입할 수 없는 키"),
        ({"suite": "node", "state": {"selected_db_ids": ["nope"]}}, "등록되지 않은 db_id"),
    ])
    def test_invalid_decomposition_node_fields(self, patch, needle):
        item = {"id": "t-x", "query": "q",
                "expect": {"min_tasks": 1, "agents": ["data_query"], "edges": []}, **patch}
        errs = H.validate_decomposition_gold([item])
        assert errs and needle in errs[0], errs

    def test_request_type_must_be_a_code_string(self):
        item = {"id": "t-x", "query": "q", "suite": "node",
                "expect": {"min_tasks": 1, "request_type": 3}}
        assert any("request_type" in e for e in H.validate_decomposition_gold([item]))


# ── 노드 모드 ───────────────────────────────────────────────────────


class TestNodeModeStructure:
    def test_sequential_plan_scores_node_and_edge_f1(self):
        results, s = _node([_SEQ_ITEM], _ScriptLLM({_SEQ_Q: [_GOOD_SEQ]}))
        r = results[0]
        assert r["outcome"] == "passed" and r["llm_attempts"] == 1 and not r["preprocessed"]
        assert r["node"]["f1"] == 1.0 and r["edge"]["f1"] == 1.0
        assert s["sequential_preserved"] == s["sequential_cases"] == 1
        assert H._verdict_decomposition_node(s) == 0

    def test_structural_miss_is_failed_not_format_error(self):
        wrong = _plan(("t1", "data_query", []), ("t2", "alarm_query", ["t1"]))
        results, s = _node([_SEQ_ITEM], _ScriptLLM({_SEQ_Q: [wrong]}))
        r = results[0]
        assert r["outcome"] == "failed" and r["format_events"] == []
        assert r["node"] == {"tp": 1, "fp": 1, "fn": 1, "precision": 0.5, "recall": 0.5,
                             "f1": 0.5}
        assert (s["failed"], s["format_errors"], s["format_error_rate"]) == (1, 0, 0.0)
        assert H._verdict_decomposition_node(s) == 1

    def test_counts_are_multiset(self):
        assert H._counts(["a", "a"], ["a"]) == {"tp": 1, "fp": 0, "fn": 1}
        assert H._counts([], []) == {"tp": 0, "fp": 0, "fn": 0}
        assert H._prf({"tp": 0, "fp": 0, "fn": 0})["f1"] is None   # 해당 없음 — 0점 아님

    def test_edge_to_missing_task_is_false_positive(self):
        edges = H._plan_edges([{"task_id": "t2", "agent": "data_query", "input_from": ["t9"]}])
        assert edges == [("?", "data_query")]


class TestNodeModeFormatErrors:
    """★ 형식 오류는 구조 오답과 따로 센다 — 단일 정답 케이스의 거짓 통과를 막는다."""

    def test_single_item_format_fallback_is_not_a_pass(self):
        results, s = _node([_SINGLE_ITEM], _ScriptLLM({_SINGLE_Q: ["죄송합니다, 못 하겠습니다."]}))
        r = results[0]
        # 함수 단위 판정으로는 폴백 단일 task가 정답과 모양이 같아 통과한다(거짓 통과).
        assert r["single_ok"] and r["agents_ok"] and r["edge_ok"]
        assert r["outcome"] == "format_error" and r["passed"] is False
        assert r["format_events"] == ["parse"]
        assert (s["passed"], s["format_errors"], s["format_error_rate"]) == (0, 1, 1.0)
        assert s["node"]["f1"] is None     # 형식 폴백은 구조 F1에 넣지 않는다
        assert H._verdict_decomposition_node(s) == 1

    def test_call_error_is_measurement_error_not_pass(self):
        results, s = _node([_SINGLE_ITEM], _ScriptLLM({_SINGLE_Q: [RuntimeError("503")]}))
        assert results[0]["outcome"] == "error" and results[0]["call_errors"] == 1
        assert s["errors"] == 1 and H._verdict_decomposition_node(s, tolerate=99) == 1

    def test_retry_recovery_counts_attempt_error_but_passes(self):
        """첫 시도 형식 오류 → 순차 표지 되먹임 재요청 → 정상. 시도 단위 오류로만 남는다."""
        llm = _ScriptLLM({_SEQ_Q: ["엉망인 출력", _GOOD_SEQ]})
        results, s = _node([_SEQ_ITEM], llm, cfg=_cfg(dag=True, replan=True))
        r = results[0]
        assert len(llm.calls) == 2 and r["llm_attempts"] == 2
        assert r["outcome"] == "passed" and r["format_events"] == ["parse"]
        assert (s["attempt_format_errors"], s["format_errors"]) == (1, 0)

    def test_final_dag_violation_is_format_error(self):
        cyclic = _plan(("t1", "data_query", ["t1"]))           # 자기 참조 — 보정 불가
        llm = _ScriptLLM({_SINGLE_Q: [cyclic]})
        results, s = _node([_SINGLE_ITEM], llm, cfg=_cfg(dag=True, replan=False))
        r = results[0]
        assert len(llm.calls) == 2                              # 되먹임 1회 뒤에도 위반
        assert r["outcome"] == "format_error" and "plan_dag_invalid" in r["format_events"]

    def test_attempt_category_from_planner_log_prefix(self):
        def rec(msg):
            return logging.LogRecord("x", logging.WARNING, __file__, 1, msg, None, None)

        cat = H._attempt_category
        assert cat([rec("intent_planner 분해 결과 없음/무효, 단일 폴백")]) == "parse"
        assert cat([rec("intent_planner 구조화 분해 실패(%d회 시도): %s")]) == "schema"
        assert cat([rec("intent_planner LLM 분해 실패, 폴백: %s")]) == "call_error"
        assert cat([rec("무관한 경고")]) is None

    def test_log_prefixes_exist_in_planner_source(self):
        """관찰은 분해 로그 문구에 기댄다.

        문구가 바뀌면 여기서 먼저 깨져야 한다(관찰이 조용히 머는 것을 막는다).
        """
        src = (_REPO / "src" / "orchestration" / "intent_planner.py").read_text(encoding="utf-8")
        for prefix, _ in H._PLANNER_LOG_MARKERS:
            assert f'"{prefix}' in src, prefix
        assert "_decompose_once(" in src

    def test_observer_restores_module_and_logger(self):
        ip = importlib.import_module("src.orchestration.intent_planner")
        original = ip._decompose_once
        logger = logging.getLogger(ip.__name__)
        handlers = list(logger.handlers)
        with H._observe_planner(ip):
            assert ip._decompose_once is not original
        assert ip._decompose_once is original and logger.handlers == handlers


class TestNodeModePreprocessingAndState:
    def test_usage_query_short_circuits_with_zero_llm(self):
        item = next(i for i in H.load_gold(H._DECOMP_GOLD, suites=None) if i["id"] == "d-102")
        llm = _ScriptLLM({})
        results, s = _node([item], llm)
        r = results[0]
        assert llm.calls == [] and r["llm_attempts"] == 0 and r["preprocessed"]
        assert r["outcome"] == "passed" and r["agents_got"] == ["general_inference"]
        assert s["preprocessed_runs"] == 1 and s["llm_runs"] == 0 and s["format_error_rate"] is None

    def test_state_injection_reaches_zone_reentry_branch(self):
        item = next(i for i in H.load_gold(H._DECOMP_GOLD, suites=None) if i["id"] == "d-103")
        ip = importlib.import_module("src.orchestration.intent_planner")
        out = asyncio.run(ip.intent_planner(H.build_node_state(item), llm=_ScriptLLM({}),
                                            app_config=_cfg()))
        assert out["task_plan"][0]["db_ids"] == ["polestar_cm_gp"]

    def test_frozen_parsed_follows_input_parser_contract(self):
        from src.nodes.schemas import ParsedRequirements

        item = {"id": "t", "query": "q",
                "parsed": {"query_targets": ["CPU"], "time_range": {"relative": "최근 1주일"}}}
        state = H.build_node_state(item)
        parsed = state["parsed_requirements"]
        assert parsed["query_targets"] == ["CPU"] and parsed["original_query"] == "q"
        assert set(ParsedRequirements.model_fields) <= set(parsed)   # 파서 기본값 계약
        assert state["user_query"] == "q" and state["task_plan"] == []  # create_initial_state shape
        item["parsed"]["query_targets"].append("변조")                 # 골드 원본과 분리(깊은 복사)
        assert parsed["query_targets"] == ["CPU"]

    def test_request_type_is_marked_unscored_not_failed(self):
        expect = {**_SEQ_ITEM["expect"], "request_type": "select_then_detail"}
        item = {**_SEQ_ITEM, "expect": expect}
        results, s = _node([item], _ScriptLLM({_SEQ_Q: [_GOOD_SEQ]}))
        r = results[0]
        assert r["outcome"] == "passed" and r["request_type_scored"] is False
        assert "채점 불가" in r["request_type_unscored_reason"]
        assert s["request_type"] == {"declared_items": 1, "scored": 0,
                                     "reason": H._REQUEST_TYPE_UNSCORED_REASON}

    def test_declared_unscored_item_is_observed_but_not_counted(self):
        item = {"id": "t-u", "query": _SINGLE_Q, "suite": "node", "unscored": "처리기 없음"}
        results, s = _node([item, _SINGLE_ITEM],
                           _ScriptLLM({_SINGLE_Q: [_plan(("t1", "data_query", []))]}))
        assert results[0]["outcome"] == "unscored" and results[0]["passed"] is None
        assert results[0]["observed"]["agents"] == ["data_query"]
        assert s["total"] == 1 and s["unscored"] == {"runs": 1, "items": 1}
        assert s["pass_hat_k"]["items"] == 1


class TestPassHatK:
    def test_item_must_pass_every_run(self):
        flaky = _ScriptLLM({_SEQ_Q: [_GOOD_SEQ, _plan(("t1", "data_query", [])), _GOOD_SEQ]})
        stable = _ScriptLLM({_SINGLE_Q: [_plan(("t1", "data_query", []))]})

        class _Both:
            async def ainvoke(self, messages):
                q = str(messages[-1].content).split("\n\n## 재요청 사유")[0]
                return await (flaky if q == _SEQ_Q else stable).ainvoke(messages)

        results, s = _node([_SEQ_ITEM, _SINGLE_ITEM], _Both(), cfg=_cfg(dag=False, replan=False),
                           repeat=3)
        assert s["runs"] == 3 and s["total"] == 6 and s["passed"] == 5
        assert s["pass_hat_k"] == {"k": 3, "items": 2, "passed_all_runs": 1, "rate": 0.5}
        assert sorted({r["run"] for r in results}) == [1, 2, 3]


class TestNodeModeThroughRealClient:
    """FabriX KBGenAI 목업(실제 클라이언트 경로).

    목업에 분해 대본이 없으므로 LLM 경로 항목은 전부 형식 오류로 잡혀야 한다.
    """

    def _run(self, fault):
        items = H.load_gold(H._DECOMP_GOLD, suites=None)
        with fx.mock_kbgenai(fault=fault):
            return _node(items, fx.make_llm())

    def test_router_shaped_output_is_caught_as_format_error(self):
        results, s = self._run(fx.FAULT_NONE)
        by_id = {r["id"]: r for r in results}
        # 함수 단위 모드가 `passed`로 세던 단일 케이스 2건이 형식 오류로 드러난다.
        assert by_id["d-004"]["outcome"] == by_id["d-005"]["outcome"] == "format_error"
        assert s["format_error_rate"] == 1.0 and s["errors"] == 0
        # 사전 처리 단락(LLM 0회) 항목만 통과한다.
        assert {r["id"] for r in results if r["outcome"] == "passed"} == {"d-102", "d-103"}
        assert H._verdict_decomposition_node(s) == 1

    def test_error_status_is_counted_as_errors(self):
        """함수 단위 모드는 이 결함에서 `errors 0`을 낸다(호출 예외를 노드가 삼킨다)."""
        _, s = self._run(fx.FAULT_ERROR_STATUS)
        assert s["errors"] == s["llm_runs"] > 0
        assert H._verdict_decomposition_node(s, tolerate=99) == 1


# ── 런타임 프롬프트 조건 모드 ────────────────────────────────────────


def _runtime(items, *, active, descriptions=None, fault=fx.FAULT_NONE, repeat=1,
             on_request=None):
    from src.config import load_config

    cond = H.runtime_conditions(load_config(), active_dbs=active, db_descriptions=descriptions)
    with fx.mock_kbgenai(fault=fault, on_request=on_request):
        results = asyncio.run(H.run_runtime(items, conditions=cond, llm=fx.make_llm(),
                                            repeat=repeat))
    return results, H.summarize_runtime(results, conditions=cond, repeat=repeat)


@pytest.fixture
def _pinned_router_flags(monkeypatch):
    from src.config import load_config

    for key in ("ROUTER_CAPABILITY_OWNERSHIP_ENABLED", "ROUTER_TWO_STAGE_ENABLED",
                "ROUTER_UNKNOWN_ENABLED"):
        monkeypatch.setenv(key, "false")
    monkeypatch.setenv("STRUCTURED_OUTPUT_BACKEND", "none")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


@pytest.mark.usefixtures("_pinned_router_flags")
class TestRuntimePromptConditions:
    def test_prompt_carries_only_active_dbs_and_snapshot(self):
        prompts: list[str] = []
        item = next(i for i in H.load_gold() if i["id"] == "r-062")
        _runtime([item], active=["polestar", "itam"],
                 descriptions={"itam": "스냅샷 설명 — 자산 원장", "polestar_b0": "비활성 DB 설명"},
                 on_request=lambda p: prompts.append(str(p.get("systemPrompt"))))
        prompt = prompts[0]
        assert "(polestar)" in prompt and "(itam)" in prompt
        assert "(cloud_portal)" not in prompt and "(polestar_b0)" not in prompt
        assert "상세: 스냅샷 설명 — 자산 원장" in prompt and "비활성 DB 설명" not in prompt

    def test_static_mode_prompt_lists_every_registered_db(self):
        """대조군 — 정적 모드는 등록 DB 전부 · 설명 없음(§2.6의 조건 차이)."""
        prompts: list[str] = []
        item = next(i for i in H.load_gold() if i["id"] == "r-062")
        with fx.mock_kbgenai(on_request=lambda p: prompts.append(str(p.get("systemPrompt")))):
            asyncio.run(H.run([item], llm=fx.make_llm()))
        assert "(cloud_portal)" in prompts[0] and "상세:" not in prompts[0]

    def test_inactive_expected_db_is_unscored_not_failed(self):
        items = [i for i in H.load_gold() if i["id"] in ("r-001", "r-062")]
        results, s = _runtime(items, active=["polestar", "itam"])
        r001 = next(r for r in results if r["id"] == "r-001")
        assert r001["passed"] is None and "활성 집합 밖" in r001["unscored"]
        assert r001["unscored_kind"] == "inactive_expected_db" and "observed" in r001
        assert "polestar_cm_yd" in r001["unscored"]
        assert s["core"]["total"] == 1 and s["core"]["passed"] == 1
        assert s["unscored"] == {"runs": 1, "items": 1, "declared": 0, "inactive_expected_db": 1}

    def test_clean_mock_with_zone_injection_passes_scored_items(self, monkeypatch):
        """존 id 주입으로 로컬에서 존 DB 골드를 채점한다.

        system ITAM 항목은 목업 대본에 없어 넣어 준다(`tests/mocks` 대본 보강은 이 작업의 편집
        범위 밖).
        """
        itam_q = next(i["query"] for i in H.load_gold(suites=None) if i["id"] == "r-082")
        monkeypatch.setitem(fx.SCRIPT, itam_q, ("data_query", [("itam", 0.92)]))
        results, s = _runtime(H.load_gold(suites=None), active=_ZONE_SET)
        assert s["core"]["errors"] == 0 and s["core"]["passed"] == s["core"]["total"] == 24
        assert s["system"]["passed"] == s["system"]["total"] == 1
        assert s["unscored"]["declared"] == 4 and s["unscored"]["inactive_expected_db"] == 2
        declared = [r for r in results if r["id"] == "r-080"][0]
        assert declared["passed"] is None and "observed" in declared
        assert H._verdict(H.summarize([r for r in results if not r.get("unscored")])) == 0

    def test_mock_faults_still_detected_under_runtime_conditions(self):
        _, s = _runtime(H.load_gold(), active=_ZONE_SET, fault=fx.FAULT_COLLAPSE_MULTI)
        assert s["core"]["multi_db_preserved"] < s["core"]["multi_db_cases"]
        _, s = _runtime(H.load_gold(), active=_ZONE_SET, fault=fx.FAULT_ERROR_STATUS)
        assert s["core"]["errors"] > 0

    def test_first_db_fallback_is_observed(self, monkeypatch):
        item = next(i for i in H.load_gold() if i["id"] == "r-050")
        monkeypatch.setitem(fx.SCRIPT, item["query"], ("data_query", [("polestar", 0.2)]))
        results, s = _runtime([item], active=["polestar", "itam"])
        assert results[0]["first_db_fallback"] == "polestar" and s["first_db_fallback"] == 1

    def test_repeat_reports_pass_hat_k(self):
        items = [i for i in H.load_gold() if i["id"] in ("r-062", "r-063")]
        results, s = _runtime(items, active=["polestar", "itam"], repeat=2)
        assert len(results) == 4 and s["core"]["total"] == 4
        assert s["pass_hat_k"] == {"k": 2, "items": 2, "passed_all_runs": 2, "rate": 1.0}

    def test_conditions_record_flags_without_description_text(self):
        from src.config import load_config

        cond = H.runtime_conditions(load_config(), active_dbs=["itam"],
                                    db_descriptions={"itam": "설명"}, descriptions_source="x.yaml")
        rec = H._conditions_record(cond)
        assert rec["flags"]["router.capability_ownership_enabled"] is False
        assert rec["db_descriptions_count"] == 1 and rec["db_descriptions_sha256"]
        assert "db_descriptions" not in rec and "설명" not in json.dumps(rec, ensure_ascii=False)
        default = H.runtime_conditions(load_config())
        assert default["active_db_ids"] == list(load_config().multi_db.get_active_db_ids())
        assert default["active_db_source"].startswith("설정 해석값")


class TestRuntimeInputs:
    def test_parse_active_dbs(self):
        assert H.parse_active_dbs(" polestar , itam ") == ["polestar", "itam"]
        for bad in ("", "nope", "itam,itam"):
            with pytest.raises(ValueError):
                H.parse_active_dbs(bad)

    def test_load_db_descriptions(self, tmp_path):
        good = tmp_path / "d.yaml"
        good.write_text("itam: 자산 원장\n", encoding="utf-8")
        assert H.load_db_descriptions(good) == {"itam": "자산 원장"}
        for body in ("nope: 설명\n", "itam: ''\n", "- a\n"):
            bad = tmp_path / "bad.yaml"
            bad.write_text(body, encoding="utf-8")
            with pytest.raises(ValueError):
                H.load_db_descriptions(bad)


# ── CLI ─────────────────────────────────────────────────────────────


class TestCli:
    def _main(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["eval_routing.py", *argv])
        return H.main()

    @pytest.mark.parametrize("argv", [
        ("--node",),
        ("--runtime-prompt-conditions", "--decomposition"),
        ("--active-dbs", "itam"),
        ("--db-descriptions", "x.yaml"),
        ("--repeat", "0", "--decomposition", "--node"),
        ("--repeat", "2"),
        ("--repeat", "2", "--decomposition"),
    ])
    def test_invalid_mode_combinations_exit_2(self, monkeypatch, argv):
        with pytest.raises(SystemExit) as exc:
            self._main(monkeypatch, *argv)
        assert exc.value.code == 2

    def test_new_mode_dry_runs(self, monkeypatch, capsys):
        assert self._main(monkeypatch, "--decomposition", "--node", "--dry-run") == 0
        assert "[node]" in capsys.readouterr().out
        assert self._main(monkeypatch, "--runtime-prompt-conditions", "--dry-run",
                          "--active-dbs", ",".join(_ZONE_SET)) == 0
        out = capsys.readouterr().out
        assert "완전한 런타임이 아니다" in out and "채점 가능 25건" in out

    def test_runtime_rejects_unknown_active_db(self, monkeypatch, capsys):
        assert self._main(monkeypatch, "--runtime-prompt-conditions", "--dry-run",
                          "--active-dbs", "nope") == 1
        assert "--active-dbs" in capsys.readouterr().err

    @pytest.mark.parametrize("argv", [
        ("--decomposition", "--node"),
        ("--runtime-prompt-conditions",),
    ])
    def test_real_calls_stay_behind_optin_gate(self, monkeypatch, argv):
        """과금 게이트 — 목업·dry-run이 아니면 기존 `_require_optin`을 먼저 통과해야 한다."""
        def refuse():
            raise SystemExit(2)

        monkeypatch.setattr(H, "_require_optin", refuse)
        with pytest.raises(SystemExit) as exc:
            self._main(monkeypatch, *argv)
        assert exc.value.code == 2
