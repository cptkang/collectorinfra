"""plans/149 W3 독립 검증(verifier) — 이름 칸 등호 0행 1회 재생성의 경계.

구현 테스트(`test_plan149_name_column_retry.py`)가 다루지 않은 경계를 본다.

- 재생성 SQL이 검증에 실패하거나 조회 마감에 걸리면, 원래 0행 답이 실패로 바뀐다(회귀 위험).
  바라는 동작을 단언하고 `xfail(strict=True)`로 둔다 — 고쳐지면 XPASS가 실패로 드러난다.
  (교정 1: 2단은 재생성 전 0행 답으로 되돌린다 · 그래프 경로는 남은 예산 2회 이상에서만 발동 —
  연속 실패 잔여만 xfail로 남긴다.)
- 그래프 경로 후속 턴: `create_followup_input`이 `query_attempts`를 비우지 않아 판정이 스레드
  누적 기록을 센다(위음성).
- SQL 판정 정규식의 경계(문자열 리터럴·주석·서브쿼리·CASE WHEN·큰따옴표 리터럴).
- 실 저장소 정본 데이터: ITAM 이름 칸 집합 · 폴스타 계열 정본 없음(비트 동일).

LLM·DB·과금 API는 쓰지 않는다.
"""

from __future__ import annotations

from functools import partial
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

import src.nodes.name_match_retry as nmr
import src.orchestration.subagents as sub
from src.graph import route_after_execution, route_after_organization, route_after_validation
from src.nodes.name_match_retry import (
    find_name_equalities,
    name_match_columns,
    name_match_retry_hint,
)
from src.nodes.result_organizer import result_organizer
from src.schema_cache.knowledge_descriptions import KNOWLEDGE_ROOT, load_knowledge_descriptions
from src.state import AgentState, create_followup_input, create_initial_state
from tests.test_nodes.test_plan149_name_column_retry import (
    DB,
    DESCRIPTIONS,
    EQ_SQL,
    LIKE_SQL,
    NON_NAME_SQL,
    _org_cfg,
    _patch_tier2,
    _sql_sequence_nodes,
    _sub_cfg,
    _tier2_input,
)

BAD_SQL = "SELEC broken"


@pytest.fixture
def knowledge_root(tmp_path, monkeypatch):
    """구현 테스트와 같은 임시 설명 정본 루트(`itam`만 정본 파일)."""
    d = tmp_path / DB
    d.mkdir()
    (d / "column_descriptions.yaml").write_text(
        yaml.safe_dump({"version": 1, "origin": "claude_code", "descriptions": DESCRIPTIONS},
                       allow_unicode=True),
        encoding="utf-8",
    )
    monkeypatch.setattr(nmr, "KNOWLEDGE_ROOT", tmp_path)
    return tmp_path


async def _validator(state, app_config=None):
    """`BAD_SQL`만 검증 실패 — 나머지는 통과."""
    if state.get("generated_sql") == BAD_SQL:
        return {
            "validation_result": {"passed": False, "reason": "구문 오류", "auto_fixed_sql": None},
            "error_message": "SQL 검증 실패: 구문 오류",
        }
    return {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None},
            "error_message": None}


# ──────────────────────────────────────────────
# 회귀 위험 — 재생성 실패가 원래 0행 답을 실패로 바꾼다
# ──────────────────────────────────────────────

def _graph_with_validation(sqls: list[str], rows: dict[str, int], seen: list,
                           checkpointer: Any = None):
    gen, exe = _sql_sequence_nodes(sqls, rows, seen)
    g = StateGraph(AgentState)
    g.add_node("query_generator", gen)
    g.add_node("query_validator", _validator)
    g.add_node("query_executor", exe)
    g.add_node("result_organizer", partial(result_organizer, app_config=_org_cfg()))
    g.add_node("output_generator", lambda s: {"current_node": "output_generator"})
    g.add_node("error_response", lambda s: {"current_node": "error_response"})
    g.add_edge(START, "query_generator")
    g.add_edge("query_generator", "query_validator")
    g.add_conditional_edges(
        "query_validator", partial(route_after_validation, max_retry=3),
        {"query_executor": "query_executor", "query_generator": "query_generator",
         "error_response": "error_response"},
    )
    g.add_conditional_edges(
        "query_executor", partial(route_after_execution, max_retry=3),
        {"result_organizer": "result_organizer", "query_generator": "query_generator",
         "error_response": "error_response"},
    )
    g.add_conditional_edges(
        "result_organizer", partial(route_after_organization, max_retry=3),
        {"output_generator": "output_generator", "query_generator": "query_generator"},
    )
    g.add_edge("output_generator", END)
    g.add_edge("error_response", END)
    return g.compile(checkpointer=checkpointer)


def _initial(db_id: str = DB) -> dict:
    st = create_initial_state(user_query="통합인증 업무 스토리지", thread_id="t-149")
    st["parsed_requirements"] = {"query_targets": ["스토리지"], "output_format": "text"}
    st["active_db_id"] = db_id
    return st


async def test_graph_budget_tail_zero_row_answer_kept(knowledge_root):
    """M-1 교정 — 그래프 경로는 남은 재시도가 2회 미만이면 발동하지 않아 0행 답이 그대로 나간다.

    (교정 전에는 예산 끝자락에서 발동한 재생성이 검증에 실패해 error_response로 끝났다.)
    """
    seen: list = []
    app = _graph_with_validation([BAD_SQL, BAD_SQL, EQ_SQL, BAD_SQL], {}, seen)
    out = await app.ainvoke(_initial())
    assert len(seen) == 3  # 0행 뒤 재생성 없음
    assert out["current_node"] == "output_generator"


@pytest.mark.xfail(strict=True, reason=(
    "verify-149 M-1 잔여(그래프 경로): 재생성 전 0행 상태를 되돌릴 수단이 없다(상태 필드 미추가) — "
    "남은 예산 2회에서 발동한 재생성이 연속 실패로 예산을 다 쓰면 error_response로 끝난다"
))
async def test_graph_regen_validation_failure_keeps_zero_row_answer(knowledge_root):
    seen: list = []
    app = _graph_with_validation([BAD_SQL, EQ_SQL, BAD_SQL, BAD_SQL], {}, seen)
    out = await app.ainvoke(_initial())
    assert len(seen) == 4  # 재생성 1회 + 검증 실패 뒤 재시도 1회
    assert out["current_node"] == "output_generator"


async def test_graph_regen_validation_failure_reaches_error_response_today(knowledge_root):
    """M-1 잔여 현행 동작(위 xfail의 짝) — 재생성 실패 1회는 흡수, 연속 실패면 error_response."""
    seen: list = []
    app = _graph_with_validation([BAD_SQL, EQ_SQL, BAD_SQL, BAD_SQL], {}, seen)
    out = await app.ainvoke(_initial())
    assert len(seen) == 4 and "LIKE '%핵심어%'" in str(seen[2])
    assert out["current_node"] == "error_response"
    assert out["retry_count"] == 3


async def test_tier2_regen_validation_failure_keeps_zero_row_answer(monkeypatch, knowledge_root):
    seen = _patch_tier2(monkeypatch, [BAD_SQL, BAD_SQL, EQ_SQL, BAD_SQL], {})
    monkeypatch.setattr(sub, "query_validator", _validator)
    out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
    assert len(seen) == 4
    assert "regen_stop" not in out and not out.get("error_message")


async def test_tier2_regen_then_deadline_keeps_zero_row_answer(monkeypatch, knowledge_root):
    clock = {"t": 0.0, "gen_sec": 10.0}
    seen = _patch_tier2(monkeypatch, [EQ_SQL, BAD_SQL, LIKE_SQL], {}, clock=clock)
    monkeypatch.setattr(sub, "query_validator", _validator)
    out = await sub._run_single_db_pipeline(
        _tier2_input(request_deadline=25.0), MagicMock(), _sub_cfg(),
    )
    assert len(seen) == 2  # 첫 생성 + 힌트 재생성(시간 있음) — 그 뒤 마감
    assert "regen_stop" not in out and not out.get("error_message")


# ──────────────────────────────────────────────
# 그래프 경로 후속 턴 — 스레드 누적 실행 기록
# ──────────────────────────────────────────────

def test_followup_delta_does_not_reset_query_attempts():
    """후속 턴 델타가 `query_attempts`·`retry_count`를 비우지 않는다(체크포인터 델타 병합)."""
    delta = create_followup_input("두 번째 질문")
    assert "query_attempts" not in delta and "retry_count" not in delta


@pytest.mark.xfail(strict=True, reason=(
    "verify-149 L-1: 그래프 경로 후속 턴에서 직전 턴의 성공 0행 실행이 query_attempts에 남아 "
    "이번 턴 첫 0행이 「두 번째 0행」으로 세어져 재생성이 발동하지 않는다"
))
async def test_graph_followup_turn_first_zero_row_fires(knowledge_root):
    seen: list = []
    app = _graph_with_validation([NON_NAME_SQL, EQ_SQL, LIKE_SQL], {}, seen,
                                 checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t-149"}}
    await app.ainvoke(_initial(), cfg)
    assert len(seen) == 1  # 1턴: 이름 칸 아님 → 재생성 없음
    await app.ainvoke(create_followup_input("통합인증 업무 스토리지 다시"), cfg)
    assert len(seen) == 3, "2턴: 이름 칸 등호 0행 → 재생성 1회가 기대"


async def test_hint_counts_thread_cumulative_attempts(knowledge_root):
    """L-1 근거 — 판정은 상태의 `query_attempts` 전체를 센다(턴 구분 없음)."""
    prior = {"sql": NON_NAME_SQL, "success": True, "row_count": 0}
    now = {"sql": EQ_SQL, "success": True, "row_count": 0}
    st = {"query_results": [], "generated_sql": EQ_SQL, "active_db_id": DB,
          "is_multi_db": False, "retry_count": 0, "query_attempts": [prior, now]}
    assert name_match_retry_hint(st, max_retry=3) is None
    st["query_attempts"] = [now]
    assert name_match_retry_hint(st, max_retry=3) is not None


# ──────────────────────────────────────────────
# 판정 정규식 경계
# ──────────────────────────────────────────────

COLS = {"업무명": frozenset({"tcdmsif52"}), "그룹경로내용": frozenset({"tcdmsif72"})}


@pytest.mark.parametrize("sql,expected", [
    # 문자열 리터럴 안의 등호 모양은 세지 않는다
    ("SELECT 1 FROM tcdmsif52 a WHERE a.`비고` = '업무명 = ''x''' ", []),
    # 주석 안
    ("SELECT 1 FROM tcdmsif52 a -- a.업무명 = 'x'\nWHERE a.`업무명` LIKE '%x%'", []),
    ("SELECT 1 FROM tcdmsif52 a /* 업무명 IN ('x') */ WHERE 1=1", []),
    # 서브쿼리 안의 조회 조건은 센다
    ("SELECT s.`서버호스트명` FROM tcdmsif72 s WHERE s.`서버호스트명` IN "
     "(SELECT b.`호스트명` FROM tcdmsif52 b WHERE b.`업무명` = '통합인증')", ["`업무명`"]),
    # 공백 없음 · IN 공백
    ("SELECT 1 FROM tcdmsif52 a WHERE a.업무명='x'", ["업무명"]),
    ("SELECT 1 FROM tcdmsif52 a WHERE a.업무명 IN  (  'x')", ["업무명"]),
    # NOT IN · <> · LIKE · 칸끼리 등호는 대상 아님
    ("SELECT 1 FROM tcdmsif52 a WHERE a.업무명 NOT IN ('x')", []),
    ("SELECT 1 FROM tcdmsif52 a WHERE a.업무명 <> 'x'", []),
    ("SELECT 1 FROM tcdmsif52 a JOIN tcdmsif72 s ON s.그룹경로내용 = a.업무명", []),
    # 서브쿼리 IN(리터럴 아님)
    ("SELECT 1 FROM tcdmsif52 a WHERE a.업무명 IN (SELECT x FROM t)", []),
    # 바로 앞이 WHEN이면 제외
    ("SELECT CASE WHEN a.업무명 = 'x' THEN 1 END FROM tcdmsif52 a", []),
])
def test_find_name_equalities_boundaries(sql, expected):
    assert find_name_equalities(sql, COLS) == expected


def test_case_when_second_condition_not_counted():
    sql = ("SELECT CASE WHEN a.`상태` = '1' AND a.`업무명` = 'x' THEN 1 END AS f "
           "FROM tcdmsif52 a WHERE a.`호스트명` LIKE '%h%'")
    assert find_name_equalities(sql, COLS) == []


@pytest.mark.xfail(strict=True, reason=(
    "verify-149 L-3: MariaDB 기본 모드의 큰따옴표 문자열 리터럴(`= \"값\"`)은 판정에서 빠진다"
))
def test_double_quoted_string_literal_counted():
    sql = 'SELECT 1 FROM tcdmsif52 a WHERE a.`업무명` = "통합인증"'
    assert find_name_equalities(sql, COLS) == ["`업무명`"]


# ──────────────────────────────────────────────
# 실 저장소 정본 — ITAM 이름 칸 · 폴스타 비트 동일
# ──────────────────────────────────────────────

def test_real_itam_name_columns():
    cols = name_match_columns(load_knowledge_descriptions(KNOWLEDGE_ROOT, "itam"))
    assert cols == {
        "그룹경로내용": frozenset({"tcdmsif72"}),
        "용도내용": frozenset({"tcdmsif72"}),
        "구성항목설명내용": frozenset({"tcdmsif72"}),
        "업무명": frozenset({"tcdmsif52"}),
        "어플리케이션명": frozenset({"tcdmsgt82"}),
    }


@pytest.mark.parametrize("db_id", ["polestar", "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"])
def test_real_polestar_has_no_descriptions_file(db_id):
    assert load_knowledge_descriptions(KNOWLEDGE_ROOT, db_id) is None
    st = {"query_results": [], "generated_sql": "SELECT r.hostname FROM cmm_resource r "
          "WHERE r.hostname = 'web-01'", "active_db_id": db_id, "is_multi_db": False,
          "retry_count": 0, "query_attempts": [{"success": True, "row_count": 0}]}
    assert name_match_retry_hint(st, max_retry=3) is None


def test_real_itam_115_and_103_shapes_fire():
    """4회차 117·118(업무명 등호)·115(용도내용 등호)가 실 정본으로 발동한다.

    103 모양(용도내용에 서비스 핵심어 등호)도 발동한다 — 힌트는 「같은 칸을 LIKE로」라
    F3(용도내용을 서비스 단서로 쓰지 않는다)과 결이 다르다(보고 L-4).
    """
    cols = name_match_columns(load_knowledge_descriptions(KNOWLEDGE_ROOT, "itam"))
    sql_117 = ("SELECT a.`호스트명` FROM `tcdmsif52` a WHERE a.`업무명` = '통합인증'")
    sql_103 = ("SELECT s.`서버호스트명` FROM `tcdmsif72` s WHERE s.`용도내용` = '통합인증'")
    assert find_name_equalities(sql_117, cols) == ["`업무명`"]
    assert find_name_equalities(sql_103, cols) == ["`용도내용`"]
