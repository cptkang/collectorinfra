"""plans/149 W3 — 이름 칸 등호 0행 → 부분 일치 힌트 1회 재생성 (G-4 (b) · D-297 부기).

이름 칸은 설명 정본(`config/knowledge/{db_id}/column_descriptions.yaml`)이 「부분 일치」로
표시한 칸이다.
여기서는 임시 정본 루트를 끼워 넣는다(실 저장소 정본은 W1이 고치는 중이라 단언하지 않는다).
LLM·DB는 쓰지 않는다 — SQL 생성·실행 노드는 대역이다.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml
from langgraph.graph import END, START, StateGraph

import src.nodes.name_match_retry as nmr
import src.orchestration.subagents as sub
from src.graph import route_after_organization
from src.nodes.name_match_retry import (
    NAME_MATCH_CHECKED_KEY,
    find_name_equalities,
    name_match_columns,
    name_match_retry_hint,
)
from src.nodes.query_generator import _build_user_prompt
from src.nodes.result_organizer import result_organizer
from src.state import AgentState, create_initial_state

DB = "itam"

DESCRIPTIONS = {
    "tcdmsif52.업무명": "업무 이름 자유 기재. 등호·IN이 아니라 LIKE 부분 일치로 건다.",
    "tcdmsgt82.어플리케이션명": "서비스 이름 — 핵심어로 부분일치 조회한다.",
    "tcdmsif52.호스트명": "서버 원장 서버호스트명과 잇는 키(정의 관계).",
    "tcdmsif72.용도내용": "서버 용도 자유 기재.",
}

EQ_SQL = (
    "SELECT a.`호스트명`, a.`업무명`\nFROM `tcdmsif52` AS a\n"
    "WHERE a.`업무명` = '통합인증'\nLIMIT 10000;"
)
IN_SQL = (
    "SELECT a.`호스트명`\nFROM `tcdmsif52` a\n"
    "WHERE a.`업무명` IN ('통합인증', '인증')\nLIMIT 100;"
)
LIKE_SQL = (
    "SELECT a.`호스트명`, a.`업무명`\nFROM `tcdmsif52` AS a\n"
    "WHERE a.`업무명` LIKE '%통합인증%'\nLIMIT 10000;"
)
NON_NAME_SQL = (
    "SELECT s.`서버호스트명`\nFROM `tcdmsif72` AS s\n"
    "WHERE s.`용도내용` = '통합인증'\nLIMIT 10000;"
)
POLESTAR_SQL = (
    "SELECT r.hostname FROM polestar.cmm_resource r "
    "WHERE r.hostname = 'web-01' LIMIT 100"
)


@pytest.fixture
def knowledge_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """임시 설명 정본 루트 — `itam`만 정본 파일이 있다(폴스타 계열은 없다)."""
    d = tmp_path / DB
    d.mkdir()
    (d / "column_descriptions.yaml").write_text(
        yaml.safe_dump(
            {"version": 1, "origin": "claude_code", "descriptions": DESCRIPTIONS},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(nmr, "KNOWLEDGE_ROOT", tmp_path)
    return tmp_path


def _attempt(sql: str, rows: int, *, success: bool = True) -> dict[str, Any]:
    return {"sql": sql, "success": success, "error": None if success else "e",
            "row_count": rows, "execution_time_ms": 1.0}


def _state(sql: str, *, db_id: str = DB, attempts: list | None = None, **kw: Any) -> dict:
    return {
        "query_results": [],
        "generated_sql": sql,
        "active_db_id": db_id,
        "is_multi_db": False,
        "retry_count": 0,
        "query_attempts": attempts if attempts is not None else [_attempt(sql, 0)],
        **kw,
    }


# ──────────────────────────────────────────────
# 이름 칸 신호 · SQL 판정(순수 함수)
# ──────────────────────────────────────────────

class TestNameColumnsFromData:
    def test_marked_columns_only(self):
        cols = name_match_columns(DESCRIPTIONS)
        assert cols == {"업무명": frozenset({"tcdmsif52"}),
                        "어플리케이션명": frozenset({"tcdmsgt82"})}

    def test_none_and_malformed(self):
        assert name_match_columns(None) == {}
        assert name_match_columns({"업무명": "부분 일치"}) == {}, "table.column 형식만"


class TestFindNameEqualities:
    cols = name_match_columns(DESCRIPTIONS)

    def test_equality_with_alias(self):
        assert find_name_equalities(EQ_SQL, self.cols) == ["`업무명`"]

    def test_in_list(self):
        assert find_name_equalities(IN_SQL, self.cols) == ["`업무명`"]

    def test_like_is_not_a_hit(self):
        assert find_name_equalities(LIKE_SQL, self.cols) == []

    def test_not_in_and_inequalities(self):
        sql = ("SELECT 1 FROM tcdmsif52 a WHERE a.업무명 NOT IN ('x') "
               "AND a.업무명 <> 'y' AND a.업무명 != 'z' AND a.업무명 >= 'w'")
        assert find_name_equalities(sql, self.cols) == []

    def test_alias_of_other_table(self):
        """같은 이름 칸이라도 표시되지 않은 테이블의 칸이면 세지 않는다."""
        sql = "SELECT 1 FROM tcdmsif73 h WHERE h.`업무명` = '통합인증'"
        assert find_name_equalities(sql, self.cols) == []

    def test_unqualified_and_table_qualified(self):
        assert find_name_equalities(
            "SELECT 1 FROM tcdmsif52 WHERE 업무명 = '통합인증'", self.cols) == ["업무명"]
        assert find_name_equalities(
            "SELECT 1 FROM tcdmsif52 WHERE tcdmsif52.업무명='통합인증'", self.cols) == ["업무명"]

    def test_derived_table_alias_falls_back_to_column(self):
        sql = ("SELECT 1 FROM (SELECT `업무명` FROM `tcdmsif52`) AS x "
               "WHERE x.`업무명` = '통합인증'")
        assert find_name_equalities(sql, self.cols) == ["`업무명`"]

    def test_literals_comments_case_when_and_empty(self):
        sql = (
            "-- a.업무명 = '주석'\n"
            "SELECT CASE WHEN a.업무명 = 'x' THEN 1 END, 'a.업무명 = ''y''' AS t\n"
            "FROM tcdmsif52 a WHERE a.업무명 = '' /* a.업무명 = 'z' */"
        )
        assert find_name_equalities(sql, self.cols) == []

    def test_polestar_shape_has_no_columns(self):
        assert find_name_equalities(POLESTAR_SQL, {}) == []

    def test_case_end_span_nested_excluded_where_counted(self):
        """CASE … END 구간(중첩 포함)의 조건은 빼고, 구간 밖 WHERE 조건은 센다(검증 149 L-2)."""
        sql = ("SELECT CASE WHEN a.`상태` = '1' THEN CASE WHEN a.업무명 = 'x' THEN 1 END "
               "ELSE 0 END AS f FROM tcdmsif52 a WHERE a.업무명 = 'y'")
        assert find_name_equalities(sql, self.cols) == ["업무명"]


class TestRetryHintJudgment:
    def test_fires_once_on_first_zero_row(self, knowledge_root):
        hint = name_match_retry_hint(_state(EQ_SQL), max_retry=3)
        assert hint is not None
        assert "`업무명`" in hint and "LIKE '%핵심어%'" in hint

    def test_in_fires(self, knowledge_root):
        assert name_match_retry_hint(_state(IN_SQL), max_retry=3) is not None

    def test_rows_present_no_retry(self, knowledge_root):
        st = _state(EQ_SQL, attempts=[_attempt(EQ_SQL, 5)])
        st["query_results"] = [{"a": 1}]
        assert name_match_retry_hint(st, max_retry=3) is None

    def test_second_zero_row_no_retry(self, knowledge_root):
        st = _state(EQ_SQL, attempts=[_attempt(EQ_SQL, 0), _attempt(EQ_SQL, 0)], retry_count=1)
        assert name_match_retry_hint(st, max_retry=3) is None

    def test_after_execution_error_still_first_zero_row(self, knowledge_root):
        st = _state(EQ_SQL, attempts=[_attempt("x", 0, success=False), _attempt(EQ_SQL, 0)],
                    retry_count=1)
        assert name_match_retry_hint(st, max_retry=3) is not None

    def test_budget_exhausted(self, knowledge_root):
        assert name_match_retry_hint(_state(EQ_SQL, retry_count=3), max_retry=3) is None

    def test_min_retries_left(self, knowledge_root):
        """그래프 경로(남은 재시도 2회 이상 요구)는 예산 끝자락에서 발동하지 않는다(M-1)."""
        st = _state(EQ_SQL, retry_count=2)
        assert name_match_retry_hint(st, max_retry=3) is not None
        assert name_match_retry_hint(st, max_retry=3, min_retries_left=2) is None
        assert name_match_retry_hint(
            _state(EQ_SQL, retry_count=1), max_retry=3, min_retries_left=2) is not None

    def test_hint_defers_to_usage_scope(self, knowledge_root):
        hint = name_match_retry_hint(_state(EQ_SQL), max_retry=3)
        assert hint is not None and "사용 범위를 제한하면" in hint

    def test_non_name_column(self, knowledge_root):
        assert name_match_retry_hint(_state(NON_NAME_SQL), max_retry=3) is None

    def test_polestar_db_without_knowledge(self, knowledge_root):
        assert name_match_retry_hint(_state(POLESTAR_SQL, db_id="polestar"), max_retry=3) is None
        # 같은 SQL 모양이라도 정본 파일이 없는 DB는 판정하지 않는다
        assert name_match_retry_hint(_state(EQ_SQL, db_id="polestar_b0"), max_retry=3) is None

    def test_multi_db_and_marker(self, knowledge_root):
        assert name_match_retry_hint(_state(EQ_SQL, is_multi_db=True), max_retry=3) is None
        assert name_match_retry_hint(
            _state(EQ_SQL, **{NAME_MATCH_CHECKED_KEY: True}), max_retry=3) is None

    def test_hint_reaches_retry_prompt(self, knowledge_root):
        hint = name_match_retry_hint(_state(EQ_SQL), max_retry=3)
        prompt = _build_user_prompt({"original_query": "통합인증 스토리지"}, None, hint, EQ_SQL)
        assert hint in prompt and EQ_SQL in prompt


# ──────────────────────────────────────────────
# 단일 그래프 경로 — result_organizer → route_after_organization → query_generator
# ──────────────────────────────────────────────

def _org_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.query.max_retry_count = 3
    cfg.text2sql.empty_diagnosis_enabled = False
    cfg.security.sensitive_columns = []
    cfg.security.mask_pattern = "***"
    return cfg


def _graph_state(sql: str, db_id: str = DB) -> dict:
    st = create_initial_state(user_query="통합인증 업무 스토리지")
    st["parsed_requirements"] = {"query_targets": ["스토리지"], "output_format": "text"}
    st.update(_state(sql, db_id=db_id))
    return st


class TestOrganizerHook:
    async def test_zero_row_name_equality_requests_retry(self, knowledge_root):
        out = await result_organizer(_graph_state(EQ_SQL), app_config=_org_cfg())
        assert out["organized_data"]["is_sufficient"] is False
        assert "LIKE '%핵심어%'" in out["error_message"]
        assert route_after_organization({**_graph_state(EQ_SQL), **out}, max_retry=3) \
            == "query_generator"

    async def test_polestar_shape_unchanged(self, knowledge_root):
        out = await result_organizer(_graph_state(POLESTAR_SQL, "polestar"), app_config=_org_cfg())
        assert out["error_message"] is None
        assert out["organized_data"]["is_sufficient"] is True

    async def test_tier2_marker_suppresses(self, knowledge_root):
        st = _graph_state(EQ_SQL)
        st[NAME_MATCH_CHECKED_KEY] = True  # type: ignore[literal-required]
        out = await result_organizer(st, app_config=_org_cfg())
        assert out["error_message"] is None


def _sql_sequence_nodes(sqls: list[str], rows_by_sql: dict[str, int], seen: list):
    """SQL 생성·실행 대역 — 생성은 순서대로 SQL을 내고, 실행은 SQL별 행 수를 돌려준다."""
    it = iter(sqls)

    async def _gen(state, llm=None, app_config=None):
        seen.append(state.get("error_message"))
        rc = state.get("retry_count", 0) + (1 if state.get("error_message") else 0)
        return {"generated_sql": next(it), "retry_count": rc, "error_message": None}

    async def _exec(state, app_config=None):
        sql = state["generated_sql"]
        n = rows_by_sql.get(sql, 0)
        return {
            "query_results": [{"h": i} for i in range(n)],
            "error_message": None,
            "query_attempts": list(state.get("query_attempts") or []) + [_attempt(sql, n)],
        }

    return _gen, _exec


async def _run_graph(sqls: list[str], rows_by_sql: dict[str, int], db_id: str = DB):
    seen: list = []
    gen, exe = _sql_sequence_nodes(sqls, rows_by_sql, seen)
    g = StateGraph(AgentState)
    g.add_node("query_generator", gen)
    g.add_node("query_executor", exe)
    g.add_node("result_organizer", partial(result_organizer, app_config=_org_cfg()))
    g.add_node("output_generator", lambda s: {"current_node": "output_generator"})
    g.add_edge(START, "query_generator")
    g.add_edge("query_generator", "query_executor")
    g.add_edge("query_executor", "result_organizer")
    g.add_conditional_edges(
        "result_organizer", partial(route_after_organization, max_retry=3),
        {"output_generator": "output_generator", "query_generator": "query_generator"},
    )
    g.add_edge("output_generator", END)
    st = create_initial_state(user_query="통합인증 업무 스토리지")
    st["parsed_requirements"] = {"query_targets": ["스토리지"], "output_format": "text"}
    st["active_db_id"] = db_id
    out = await g.compile().ainvoke(st)
    return out, seen


class TestGraphPathLoop:
    async def test_retry_once_then_stop_on_zero(self, knowledge_root):
        out, seen = await _run_graph([EQ_SQL, LIKE_SQL, EQ_SQL], {})
        assert len(seen) == 2, "재생성 1회 — 두 번째 0행에서 끝"
        assert seen[0] is None and "LIKE '%핵심어%'" in seen[1]
        assert out["generated_sql"] == LIKE_SQL and out["query_results"] == []
        assert out["error_message"] is None

    async def test_retry_finds_rows(self, knowledge_root):
        out, seen = await _run_graph([EQ_SQL, LIKE_SQL], {LIKE_SQL: 3})
        assert len(seen) == 2 and len(out["query_results"]) == 3

    async def test_regenerated_equality_again_does_not_loop(self, knowledge_root):
        """LLM이 힌트를 무시하고 또 등호를 써도 3회 이상 재생성하지 않는다."""
        out, seen = await _run_graph([EQ_SQL, EQ_SQL, EQ_SQL], {})
        assert len(seen) == 2

    @pytest.mark.parametrize("sql,rows", [(EQ_SQL, {EQ_SQL: 2}), (NON_NAME_SQL, {})])
    async def test_no_retry(self, knowledge_root, sql, rows):
        _out, seen = await _run_graph([sql, LIKE_SQL], rows)
        assert len(seen) == 1

    async def test_polestar_no_retry(self, knowledge_root):
        _out, seen = await _run_graph([POLESTAR_SQL, LIKE_SQL], {}, db_id="polestar")
        assert len(seen) == 1


# ──────────────────────────────────────────────
# 2단 task 루프 — subagents._run_single_db_pipeline (같은 판정 · 대칭)
# ──────────────────────────────────────────────

def _sub_cfg():
    return SimpleNamespace(
        query=SimpleNamespace(max_retry_count=3, default_limit=1000),
        server=SimpleNamespace(answer_reserve_sec=0.0),
    )


def _patch_tier2(monkeypatch, sqls: list[str], rows_by_sql: dict[str, int], clock=None):
    seen: list = []
    gen, exe = _sql_sequence_nodes(sqls, rows_by_sql, seen)

    async def _gen(state, llm=None, app_config=None):
        if clock is not None:
            clock["t"] += clock["gen_sec"]
        return await gen(state, llm=llm, app_config=app_config)

    async def _schema(state, llm=None, app_config=None):
        return {"schema_info": {"tables": {}}}

    async def _validate(state, app_config=None):
        return {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None},
                "error_message": None}

    monkeypatch.setattr(sub, "schema_analyzer", _schema)
    monkeypatch.setattr(sub, "query_generator", _gen)
    monkeypatch.setattr(sub, "query_validator", _validate)
    monkeypatch.setattr(sub, "query_executor", exe)
    if clock is not None:
        monkeypatch.setattr(sub, "_monotonic", lambda: clock["t"])
    return seen


def _tier2_input(db_id: str = DB, **kw: Any) -> dict:
    return {
        "user_query": "통합인증 업무 스토리지", "retry_count": 0, "error_message": None,
        "query_results": [], "query_attempts": [], "generated_sql": "",
        "active_db_id": db_id, "is_multi_db": False, "request_deadline": None, **kw,
    }


class TestTier2Loop:
    async def test_retry_once_then_stop_on_zero(self, monkeypatch, knowledge_root):
        seen = _patch_tier2(monkeypatch, [EQ_SQL, LIKE_SQL, EQ_SQL], {})
        out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
        assert len(seen) == 2 and "LIKE '%핵심어%'" in seen[1]
        assert out["generated_sql"] == LIKE_SQL and out["query_results"] == []
        assert out["error_message"] is None and "regen_stop" not in out
        assert out[NAME_MATCH_CHECKED_KEY] is True

    async def test_in_retry_finds_rows(self, monkeypatch, knowledge_root):
        seen = _patch_tier2(monkeypatch, [IN_SQL, LIKE_SQL], {LIKE_SQL: 4})
        out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
        assert len(seen) == 2 and len(out["query_results"]) == 4

    @pytest.mark.parametrize("sql,rows,db", [
        (EQ_SQL, {EQ_SQL: 2}, DB),          # 행이 있으면 재생성 없음
        (NON_NAME_SQL, {}, DB),             # 이름 칸이 아니면 재생성 없음
        (POLESTAR_SQL, {}, "polestar"),     # 이름 칸 표시 없는 DB — 종전과 같다
    ])
    async def test_no_retry(self, monkeypatch, knowledge_root, sql, rows, db):
        seen = _patch_tier2(monkeypatch, [sql, LIKE_SQL], rows)
        out = await sub._run_single_db_pipeline(_tier2_input(db), MagicMock(), _sub_cfg())
        assert len(seen) == 1
        assert NAME_MATCH_CHECKED_KEY not in out

    async def test_regen_execution_errors_restore_zero_row_answer(
        self, monkeypatch, knowledge_root,
    ):
        """재생성 SQL이 실행 오류로 예산을 다 써도 재생성 전 0행 답으로 끝난다(검증 149 M-1)."""
        seen = _patch_tier2(monkeypatch, [EQ_SQL, LIKE_SQL, LIKE_SQL, LIKE_SQL], {})
        exe = sub.query_executor

        async def _exec(state, app_config=None):
            if state["generated_sql"] == LIKE_SQL:
                return {"error_message": "SQL 실행 오류: 칸 없음",
                        "query_attempts": [*state["query_attempts"],
                                           _attempt(LIKE_SQL, 0, success=False)]}
            return await exe(state, app_config=app_config)

        monkeypatch.setattr(sub, "query_executor", _exec)
        out = await sub._run_single_db_pipeline(_tier2_input(), MagicMock(), _sub_cfg())
        assert len(seen) == 4 and out["retry_count"] == 0
        assert out["generated_sql"] == EQ_SQL and out["query_results"] == []
        assert out["error_message"] is None and "regen_stop" not in out
        assert len(out["query_attempts"]) == 1 and out[NAME_MATCH_CHECKED_KEY] is True

    async def test_no_time_keeps_zero_rows_and_organizer_stays_quiet(
        self, monkeypatch, knowledge_root,
    ):
        """재생성할 시간이 없으면 0행 그대로 — 조회 마감 실패로 바꾸지 않는다.

        결과 정리도 재생성을 다시 요청하지 않는다(판정 표지).
        """
        clock = {"t": 0.0, "gen_sec": 10.0}
        seen = _patch_tier2(monkeypatch, [EQ_SQL, LIKE_SQL], {}, clock=clock)
        out = await sub._run_single_db_pipeline(
            _tier2_input(request_deadline=15.0), MagicMock(), _sub_cfg(),
        )
        assert len(seen) == 1
        assert out["error_message"] is None and "regen_stop" not in out
        assert out[NAME_MATCH_CHECKED_KEY] is True
        st = create_initial_state(user_query="통합인증 업무 스토리지")
        st["parsed_requirements"] = {"query_targets": ["스토리지"], "output_format": "text"}
        st.update(out)  # type: ignore[typeddict-item]
        organized = await result_organizer(st, app_config=_org_cfg())
        assert organized["error_message"] is None
