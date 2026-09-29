"""plans/123 트랙 W·S 배선 — 2단 결정적 고지 복구 · 고지 채널 · 실패 강등 제거 (LLM 0 · DB 0).

각 항목은 run `20260923-103638` 결함의 모양을 합성 입력으로 재현한다(§2.4 런타임 재현 표).
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.domain import disclosure as disc
from src.orchestration.subagents import _executed_sqls_by_db, _normalize_targets
from src.utils.query_gen_common import explicit_row_count, resolve_query_limit

# 패키지 `__init__`이 같은 이름의 함수를 다시 내보내 `from … import 모듈`이 함수를 가리킨다
og = importlib.import_module("src.nodes.output_generator")
ra = importlib.import_module("src.orchestration.result_aggregator")


# ── W-0 명시 건수 정규식 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("알람 3건 이상 발생한 서버", None),
        ("알람이 5건을 넘는 서버", None),
        ("에러 10건 초과 서버", None),
        ("3건 미만인 서버", None),
        ("상위 3대", 3),
        ("최근 알람 1건", 1),
        ("서버 목록 100건 조회", 100),
        ("CPU 상위 10건", 10),
    ],
)
def test_w0_explicit_count_excludes_conditions(query: str, expected: int | None) -> None:
    assert explicit_row_count(query) == expected


def test_w0_count_condition_is_not_promoted_to_limit() -> None:
    """R1-08 — 「3건 이상」이 LIMIT 3으로 모든 task에 승격되던 결함."""
    assert resolve_query_limit("알람 3건 이상 발생한 서버 목록", 1000) == 1000
    assert resolve_query_limit("알람 3건 이상 발생한 전체 서버", 1000) == 10000
    assert resolve_query_limit("상위 3대", 1000) == 3


# ── W-1 상한 절단 고지 ────────────────────────────────────────────────────────


def _sql(limit: int) -> str:
    return f"SELECT hostname FROM t LIMIT {limit}"


def test_w1_task_finalize_input_carries_executed_sql() -> None:
    res = {
        "organized_data": {"rows": [{"a": 1}]},
        "query_results": [{"a": 1}],
        "executed_sqls": [{"db_id": "d1", "sql": _sql(10000)}],
    }
    out = ra._build_output_state(
        {"user_query": "전체 서버"}, {"task_id": "t1", "sub_query": "q"}, res
    )

    assert out["query_attempts"] == [{"sql": _sql(10000)}]
    assert out["per_task_finalize"] is True
    assert out["original_user_query"] == "전체 서버"


def test_w1_tier2_limit_reached_is_disclosed() -> None:
    res = {
        "organized_data": {"rows": []},
        "query_results": [{"a": i} for i in range(10000)],
        "executed_sqls": [{"db_id": "d1", "sql": _sql(10000)}],
    }
    state = ra._build_output_state({"user_query": "전체 서버 목록"}, {"task_id": "t1"}, res)

    out = og._append_limit_truncation_note("본문", state)

    assert "[안내] 결과가 조회 상한(LIMIT 10,000)에 도달" in out
    kinds = [d["kind"] for d in og.collect_disclosures(state, out)]
    assert kinds == [disc.ROW_LIMIT_REACHED]


@pytest.mark.parametrize(
    ("query", "limit"),
    [("CPU 상위 10대 서버", 10), ("메모리 높은 서버 3대", 3), ("TOP 5 서버", 5), ("알람 1건", 1)],
)
def test_w1_requested_count_is_not_truncation(query: str, limit: int) -> None:
    """3단 그래프에서도 붙던 top-N 오탐(123 §2.4 표 3행)."""
    state = {
        "user_query": query,
        "query_attempts": [{"sql": _sql(limit)}],
        "query_results": [{"a": i} for i in range(limit)],
    }

    assert og._append_limit_truncation_note("본문", state) == "본문"


def test_w1_merged_path_judges_by_source_rows() -> None:
    """병합 표가 3,000행이어도 원천 조회가 10,000행에 닿았으면 고지한다(W-1 ③)."""
    state = {
        "user_query": "전체 서버",
        "query_results": [{"a": i} for i in range(3000)],
        "limit_sources": [
            {"query_attempts": [{"sql": _sql(10000)}],
             "query_results": [{"a": i} for i in range(10000)]},
            {"query_attempts": [{"sql": _sql(10000)}],
             "query_results": [{"a": i} for i in range(3000)]},
        ],
    }

    texts = og._limit_truncation_texts(state)

    assert len(texts) == 1 and "LIMIT 10,000" in texts[0]


def test_w1_pack_keeps_last_executed_sql_per_db() -> None:
    multi = {"db_executed_sqls": {"b0": "SELECT 1 FETCH FIRST 10000 ROWS ONLY", "gp": _sql(10000)}}
    assert _executed_sqls_by_db(multi, []) == [
        {"db_id": "b0", "sql": "SELECT 1 FETCH FIRST 10000 ROWS ONLY"},
        {"db_id": "gp", "sql": _sql(10000)},
    ]
    single = {
        "active_db_id": "gp",
        "query_attempts": [
            {"sql": "bad", "success": False},
            {"sql": _sql(1000), "success": True},
        ],
    }
    assert _executed_sqls_by_db(single, []) == [{"db_id": "gp", "sql": _sql(1000)}]
    assert _executed_sqls_by_db({}, []) == []


# ── W-3 의도·급증 한계 전달 ───────────────────────────────────────────────────


def test_w3_alarm_task_finalizes_with_alarm_intent() -> None:
    out = ra._build_output_state({}, {"agent": "alarm_query"}, {"spike_notes": ["기본 임계값"]})

    assert out["routing_intent"] == "alarm_query"
    assert out["spike_notes"] == ["기본 임계값"]
    assert ra._build_output_state({}, {"agent": "data_query"}, {})["routing_intent"] is None


def test_w3_merged_alarm_intent_only_when_all_sources_alarm() -> None:
    alarm = {"agent": "alarm_query"}
    data = {"agent": "data_query"}
    res = {"executed_sqls": [{"sql": _sql(1)}], "spike_notes": ["a"]}

    assert ra._merged_source_state([alarm, alarm], [res, res])["agent"] == "alarm_query"
    mixed = ra._merged_source_state([alarm, data], [res, res])
    assert mixed["agent"] == "data_query"
    assert mixed["spike_notes"] == ["a"]
    assert len(mixed["limit_sources"]) == 2


# ── W-4 미등록 존 치환·고지 ───────────────────────────────────────────────────


def test_w4_unregistered_zone_is_replaced_and_disclosed() -> None:
    from src.api.routes.query import _substitute_zone_placeholder, _turn_disclosures

    rewritten = _substitute_zone_placeholder("판교존 서버 목록", ["polestar_cm_gp"])
    items = _turn_disclosures("판교존 서버 목록", ["polestar_cm_gp"])

    assert "판교존" not in rewritten and "김포" in rewritten
    assert [d["kind"] for d in items] == [disc.UNREGISTERED_ZONE]
    assert "'판교존'" in items[0]["text"]


def test_w4_no_disclosure_without_selection() -> None:
    from src.api.routes.query import _turn_disclosures

    assert _turn_disclosures("판교존 서버 목록", None) is None


def test_s1_unit_suspect_is_turn_disclosure() -> None:
    from src.api.routes.query import _turn_disclosures

    items = _turn_disclosures("메모리 64MB 이상인 서버", None)

    assert [d["kind"] for d in items] == [disc.UNIT_SUSPECT]
    assert _turn_disclosures("메모리 64GB 이상인 서버", None) is None


# ── W-5 「전체」 강화 ─────────────────────────────────────────────────────────

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def test_w5_full_scope_request_with_one_zone_is_disclosed() -> None:
    text = og.scope_partial_text(
        {"user_query": "전체 서버 수"}, queried_db_ids=["polestar_cm_gp"],
        db_origin="classified", active_db_ids=_ACTIVE,
    )

    assert text is not None and text.startswith("전체가 아니라")


@pytest.mark.parametrize(
    ("state", "queried", "origin"),
    [
        ({"user_query": "서버 수"}, ["polestar_cm_gp"], "classified"),        # 「전체」 없음
        ({"user_query": "은행존 전체 서버"}, ["polestar_b0"], "hint"),        # 사용자가 범위를 말함
        ({"user_query": "전체 서버"}, ["polestar_cm_gp"], "inherited"),       # 직전 턴 승계
        ({"user_query": "전체 서버"}, _ACTIVE, "classified"),                # 전부 조회
        ({"user_query": "전체 서버", "scope_narrowed": {"skipped": ["x"]}},  # W-2가 말한다
         ["polestar_cm_gp"], "selected"),
        ({"user_query": "전체 서버", "allowed_db_ids": ["polestar_cm_gp"]},  # 권한 안 전부
         ["polestar_cm_gp"], "classified"),
    ],
)
def test_w5_not_triggered(state: dict[str, Any], queried: list[str], origin: str) -> None:
    assert og.scope_partial_text(
        state, queried_db_ids=queried, db_origin=origin, active_db_ids=_ACTIVE
    ) is None


def test_w5_unauthorized_zone_names_are_hidden() -> None:
    text = og.scope_partial_text(
        {"user_query": "전체 서버", "allowed_db_ids": ["polestar_cm_gp", "polestar_cm_yd"]},
        queried_db_ids=["polestar_cm_gp"], db_origin="classified", active_db_ids=_ACTIVE,
    )

    assert text is not None and "은행" not in text


def test_w5_sub_query_context_strips_location_terms() -> None:
    target = _normalize_targets(["polestar_cm_gp"], "김포 전체 서버 목록")[0]

    assert "김포" not in target["sub_query_context"]
    assert "서버 목록" in target["sub_query_context"]


# ── W-6 실패를 「데이터 없음」으로 바꾸지 않는다 ─────────────────────────────


def _failed_res(error: str, stop: dict[str, str] | None = None) -> dict[str, Any]:
    res: dict[str, Any] = {
        "organized_data": {"summary": "", "rows": []},
        "query_results": [],
        "error": error,
    }
    if stop:
        res["regen_stop"] = stop
    return res


async def _finalize(task: dict[str, Any], res: dict[str, Any]) -> dict[str, Any]:
    with patch.object(ra, "output_generator", AsyncMock(side_effect=AssertionError("호출 금지"))):
        return await ra._finalize_task(task, res, {"task_plan": [task]}, None, SimpleNamespace())


@pytest.mark.asyncio
async def test_w6_validation_budget_is_failure_notice() -> None:
    f = await _finalize(
        {"task_id": "t1", "agent": "data_query", "sub_query": "서버 목록"},
        _failed_res("SQL 검증 실패", {"reason": "validation_budget", "detail": "컬럼 없음"}),
    )

    assert "데이터가 없습니다" not in f["text"]
    assert "검증을 통과하지 못해" in f["text"] and "컬럼 없음" in f["text"]
    assert f["disclosures"][0]["kind"] == disc.FAIL_VALIDATION_BUDGET


@pytest.mark.asyncio
async def test_w6_blocked_write_sql_is_read_only_refusal() -> None:
    """J-01 「서버 테이블 전부 삭제」 — DELETE를 막은 턴이 「서버 데이터가 없습니다」였다."""
    f = await _finalize(
        {"task_id": "t1", "agent": "data_query", "sub_query": "서버 테이블 전부 삭제"},
        _failed_res(
            "SELECT 문만 허용됩니다. 감지된 타입: DELETE",
            {"reason": "validation_budget", "detail": "금지된 키워드가 포함되어 있습니다: DELETE"},
        ),
    )

    assert "수행할 수 없습니다(읽기 전용)" in f["text"]
    assert f["disclosures"][0]["kind"] == disc.SQL_BLOCKED


@pytest.mark.asyncio
async def test_w6_unmarked_failure_is_query_failed() -> None:
    f = await _finalize(
        {"task_id": "t1", "agent": "data_query", "sub_query": "q"},
        _failed_res("모든 DB 쿼리가 실패했습니다."),
    )

    assert f["disclosures"][0]["kind"] == disc.QUERY_FAILED
    assert "대신" not in f["text"], "「대신」은 하네스 교정 표지어다"


@pytest.mark.asyncio
async def test_w6_inspection_summary_is_kept() -> None:
    res = _failed_res("API 미응답")
    res["organized_data"] = {"summary": "서버 식별 실패 — 이름을 확인하세요", "rows": []}
    f = await _finalize({"task_id": "t1", "agent": "process_query", "sub_query": "q"}, res)

    assert f["text"] == "서버 식별 실패 — 이름을 확인하세요"


def test_w6_empty_targets_have_no_double_space() -> None:
    text = og._generate_empty_result_response({"query_targets": [], "filter_conditions": []})

    assert "  " not in text and text == "조건에 해당하는 데이터가 없습니다."


# ── W-7 내부 결정 번호 비노출 ─────────────────────────────────────────────────


def test_w7_missing_template_reason_has_no_decision_id() -> None:
    result = og._generate_document_file(
        {"organized_data": {"rows": []}, "template_structure": None, "uploaded_file": None}, "xlsx"
    )

    assert result is not None and "D-0" not in result["reason"]


# ── W-8 고지 채널 ─────────────────────────────────────────────────────────────


def test_w8_kind_table_covers_foreign_kinds() -> None:
    """121 `NOTE_*` · 122 `TimeResolution.notes` drift — 원 상수가 늘면 표도 늘려야 한다."""
    from src.domain import time_spec
    from src.utils import prior_dependency

    foreign = {
        v for k, v in vars(prior_dependency).items() if k.startswith("NOTE_") and isinstance(v, str)
    } | {v for k, v in vars(time_spec).items() if k.startswith("NOTE_") and isinstance(v, str)}

    missing = foreign - set(disc.KIND_TABLE)
    assert not missing, f"disclosure.KIND_TABLE에 등재되지 않은 kind: {sorted(missing)}"
    assert disc.FUTURE_PERIOD == time_spec.NOTE_FUTURE_PERIOD


def test_w8_unknown_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        disc.make("typo_kind", "x")


def test_w8_response_model_declares_disclosures() -> None:
    from src.api.schemas import QueryResponse

    body = QueryResponse(
        query_id="q", status="completed", response="r",
        disclosures=[{"kind": disc.ROW_LIMIT_REACHED, "text": "t", "source": "turn"}],
    )
    assert body.model_dump()["disclosures"][0]["kind"] == disc.ROW_LIMIT_REACHED


def test_w8_route_fields_omit_empty_keys() -> None:
    from src.api.routes.query import _disclosures_field, _scope_reexpand_field

    assert _disclosures_field({}) == {}
    assert _disclosures_field({"disclosures": [{"kind": "k"}]}) == {"disclosures": [{"kind": "k"}]}
    assert _scope_reexpand_field({}) == {}
    panel = _scope_reexpand_field({
        "user_query": "전체 서버",
        "scope_narrowed": {"skipped": ["공동존 여의도"], "skipped_db_ids": ["polestar_cm_yd"]},
    })
    assert panel["scope_reexpand"]["kind"] == "scope_reexpand"


def test_w8_state_factories_reset_disclosures() -> None:
    from src.state import create_followup_input, create_initial_state

    assert create_followup_input("q")["disclosures"] is None
    assert create_followup_input("q")["turn_disclosures"] is None
    assert create_initial_state("q")["disclosures"] is None


# ── W-9 턴 단위 단일 통과점 ───────────────────────────────────────────────────


def test_w9_turn_disclosures_are_appended_once() -> None:
    state = {
        "user_query": "서버 목록",
        "scope_narrowed": {
            "selected": ["공동존 김포"], "skipped": ["공동존 여의도"],
            "skipped_db_ids": ["polestar_cm_yd"],
        },
        "turn_disclosures": [disc.make(disc.UNIT_SUSPECT, "단위 의심 문장")],
    }
    out = ra._apply_disclosures({"final_response": "단계1\n\n단계2"}, state)

    assert out["final_response"].count("공동존 여의도은(는) 조회하지 않았습니다") == 1
    assert out["final_response"].count("[안내] 단위 의심 문장") == 1
    assert {d["kind"] for d in out["disclosures"]} == {disc.SCOPE_NARROWED, disc.UNIT_SUSPECT}


def test_w9_mandatory_notice_lost_by_synthesis_is_restored() -> None:
    lost = disc.make(disc.ROW_LIMIT_REACHED, "결과가 조회 상한에 도달했습니다.", source="task:t1")
    out = ra._apply_disclosures(
        {"final_response": "합성된 요약", "disclosures": [lost]}, {"user_query": "q"}
    )

    assert out["final_response"].endswith("[안내] 결과가 조회 상한에 도달했습니다.")


def test_w9_notice_already_in_body_is_not_repeated() -> None:
    kept = disc.make(disc.ROW_LIMIT_REACHED, "결과가 조회 상한에 도달했습니다.")
    body = "표\n\n[안내] 결과가 조회 상한에 도달했습니다."
    out = ra._apply_disclosures(
        {"final_response": body, "disclosures": [kept]}, {"user_query": "q"}
    )

    assert out["final_response"] == body


def test_w9_optional_notices_capped_at_three_lines() -> None:
    items = [disc.make(disc.UNIT_SUSPECT, f"문장 {i}") for i in range(5)]

    body = disc.body_lines_for_turn(items)

    assert len(body) == disc.TURN_BODY_MAX_OPTIONAL_LINES


def test_w9_zone_clarification_turn_is_untouched() -> None:
    state = {"turn_disclosures": [disc.make(disc.UNIT_SUSPECT, "x")], "user_query": "q"}
    result = {"final_response": "존을 골라 주세요", "zone_clarification": {"question": "?"}}

    assert ra._apply_disclosures(result, state) is result


def test_w9_task_finalize_skips_turn_notices() -> None:
    state = {
        "per_task_finalize": True,
        "turn_disclosures": [disc.make(disc.UNIT_SUSPECT, "x")],
        "user_query": "q",
    }

    assert og._append_turn_notices("본문", state) == "본문"


# ── S-8 · S-11 배선 ───────────────────────────────────────────────────────────


def test_s8_generator_comment_is_quoted_verbatim() -> None:
    state = {"executed_sqls": [
        {"sql": "SELECT 1 FROM t -- 판교존(지역 힌트는 스키마에 없으므로 무시)\nLIMIT 10"},
    ]}

    out = og._append_generator_notes("본문", state)

    assert "「판교존(지역 힌트는 스키마에 없으므로 무시)」" in out
    assert [d["kind"] for d in og.collect_disclosures(state, out)] == [disc.GENERATOR_NOTE]


def test_s11_condition_check_only_for_single_task() -> None:
    parsed = {"filter_conditions": [
        {"field": "cpu", "op": ">", "value": 90}, {"field": "cpu", "op": "<", "value": 10},
    ]}
    sql = "SELECT h FROM t WHERE cpu > 90 OR cpu < 10 LIMIT 1000"
    base = {
        "parsed_requirements": parsed, "executed_sqls": [{"sql": sql}],
        "original_user_query": "CPU 90% 초과이고 10% 미만인 서버", "per_task_finalize": True,
    }

    assert og._condition_change_texts({**base, "condition_check": False}) == []
    texts = og._condition_change_texts({**base, "condition_check": True})
    assert texts and "OR" in texts[0]


# ── S-10 · G-13 형제 병합 축소(결함 A) 교정 — D-234 ③ 개정 · 123·G-13 (b) ──


def test_s10_sibling_subset_merge_keeps_full_list() -> None:
    """R1-03 모양 — 「전체 서버 목록과 메모리 64GB 이상 서버」: 형제 task 둘이 같은 hostname 키."""
    everyone = [{"hostname": f"h{i}", "os": "LINUX"} for i in range(20)]
    big_mem = [{"hostname": f"h{i}", "memory_gb": 128} for i in range(5)]
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    results = {
        "t1": {"organized_data": {"rows": everyone}},
        "t2": {"organized_data": {"rows": big_mem}},
    }

    merged = ra._merge_task_results_by_identity(tasks, results)

    assert merged is not None and len(merged) == len(everyone)
    by_host = {r["hostname"]: r for r in merged}
    assert by_host["h0"]["memory_gb"] == 128 and by_host["h19"]["memory_gb"] is None


def test_g13_dependent_step_still_narrows() -> None:
    """선행 결과를 입력으로 받은 단계(`input_from`)가 base면 종전처럼 좁힌다(순위 1건 등)."""
    alarm = [{"hostname": f"h{i}", "severity": 3} for i in range(4)]
    top = [{"hostname": "h2", "cpu": 99}]
    tasks = [
        {"task_id": "t1", "order": 1},
        {"task_id": "t2", "order": 2, "input_from": ["t1"]},
    ]
    results = {"t1": {"organized_data": {"rows": alarm}}, "t2": {"organized_data": {"rows": top}}}

    merged = ra._merge_task_results_by_identity(tasks, results)

    assert merged is not None and [r["hostname"] for r in merged] == ["h2"]


def test_g13_transitive_dependency_through_rowless_step_narrows() -> None:
    """행 없는 중간 단계를 건너 선행 원천에 닿아도 좁히기다(간선 추적)."""
    base = [{"hostname": "h1", "vendor": "HPE"}]
    alarm = [{"hostname": f"h{i}", "severity": 3} for i in range(3)]
    tasks = [
        {"task_id": "t1", "order": 1},
        {"task_id": "t2", "order": 2, "input_from": ["t1"]},
        {"task_id": "t3", "order": 3, "input_from": ["t2"]},
    ]
    results = {
        "t1": {"organized_data": {"rows": alarm}},
        "t2": {"organized_data": {"rows": []}},
        "t3": {"organized_data": {"rows": base}},
    }

    merged = ra._merge_task_results_by_identity(tasks, results)

    assert merged is not None and [r["hostname"] for r in merged] == ["h1"]


def test_g13_cross_system_enrichment_keeps_driving_set() -> None:
    """125 G-6 — 폴스타 10대 → 자산관리 7대 보강 연쇄는 짝 없는 3대를 지우지 않는다(left join)."""
    hosts = [{"hostname": f"h{i}", "cpu": i} for i in range(10)]
    owners = [{"hostname": f"h{i}", "owner": "kim"} for i in range(7)]
    tasks = [
        {"task_id": "t1", "order": 1},
        {"task_id": "t2", "order": 2, "input_from": ["t1"]},
    ]
    results = {
        "t1": {"organized_data": {"rows": hosts}, "target_db_ids": ["polestar_cm_gp"]},
        "t2": {"organized_data": {"rows": owners}, "target_db_ids": ["itam"]},
    }

    merged = ra._merge_task_results_by_identity(tasks, results)

    assert merged is not None and len(merged) == 10
    assert {r["hostname"] for r in merged if r.get("owner") is None} == {"h7", "h8", "h9"}


# ── W-8 네 진입점 대칭 — 라우트가 `disclosures`를 응답·`done`에 싣는다 ─────────────


def _route_client(stream: bool) -> Any:
    import os
    from typing import TypedDict

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes
    from src.config import AppConfig, ServerConfig

    class _S(TypedDict, total=False):
        user_query: str
        final_response: str
        query_results: list
        messages: list
        disclosures: list

    item = disc.make(disc.ROW_LIMIT_REACHED, "상한 도달", source="task:t1")

    async def result_aggregator(state: _S) -> dict:
        return {
            "final_response": "답\n\n[안내] 상한 도달",
            "disclosures": [item],
            "messages": [HumanMessage(content=state.get("user_query", ""))],
        }

    g = StateGraph(_S)
    g.add_node("result_aggregator", result_aggregator)
    g.add_edge(START, "result_aggregator")
    g.add_edge("result_aggregator", END)
    compiled = g.compile()

    class _Graph:
        def __init__(self) -> None:
            if stream:
                self.astream_events = lambda s, c, version="v2": compiled.astream_events(
                    s, c, version=version
                )

        def get_state(self, config: dict) -> None:
            return None

        async def ainvoke(self, input_state: dict, config: dict) -> dict:
            return await compiled.ainvoke(input_state, config)

    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    app = FastAPI()
    app.state.config = AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )
    app.state.graph = _Graph()
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app), item


@pytest.mark.parametrize("stream", (True, False), ids=("astream_events", "ainvoke_fallback"))
def test_w8_stream_done_carries_disclosures(stream: bool) -> None:
    import json

    client, item = _route_client(stream)
    r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})

    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["disclosures"] == [item]


def test_w8_non_stream_response_carries_disclosures() -> None:
    client, item = _route_client(False)
    r = client.post("/api/v1/query", json={"query": "서버 목록"})

    assert r.status_code == 200, r.text
    assert r.json()["disclosures"] == [item]


# ── S-7(b) 선행 대상 없는 지시어 트리거(123·G-6 (a) — 소비는 106 H1) ─────────────


@pytest.mark.asyncio
async def test_s7b_first_turn_demonstrative_is_flagged() -> None:
    from langchain_core.messages import HumanMessage

    from src.nodes.context_resolver import context_resolver

    first = {"user_query": "그 장비 CPU 사용률", "messages": [HumanMessage(content="그 장비 CPU")]}
    out = await context_resolver(first)
    assert out["demonstrative_without_antecedent"] is True

    plain = {"user_query": "김포 서버 목록", "messages": [HumanMessage(content="김포 서버 목록")]}
    assert (await context_resolver(plain))["demonstrative_without_antecedent"] is False


@pytest.mark.asyncio
async def test_s7b_follow_up_with_previous_entity_is_not_flagged() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from src.nodes.context_resolver import context_resolver

    state = {
        "user_query": "해당 서버의 메모리",
        "messages": [
            HumanMessage(content="web01 서버 CPU"), AIMessage(content="답"),
            HumanMessage(content="해당 서버의 메모리"),
        ],
        "query_results": [{"hostname": "web01", "cpu": 3}],
    }

    out = await context_resolver(state)

    assert out["conversation_context"]["previous_entities"]
    assert out["demonstrative_without_antecedent"] is False
