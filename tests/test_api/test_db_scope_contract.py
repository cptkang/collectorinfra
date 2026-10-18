"""스레드 DB 스코프 API 계약 (plans/90 · D-205 · SPEC-thread-db-scope §Success 6).

★ 응답 키는 **4경로 동시**여야 한다 — `scope_reexpand`가 `/query`에만 실리고 SSE done에는 빠져
   범위 재확장 패널이 스트리밍에서 뜨지 않았던 선례(plans/90 §1.2)를 반복하지 않는다. 응답 조립
   6경로·done 4경로가 `"db_scope"`를 싣는 공유 빌더 한 벌을 지나는지 정적으로 본다(plans/179 W1a).
LLM·DB 0.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.schemas import QueryRequest, QueryResponse
from src.api.server import create_app
from src.orchestration.result_aggregator import _collect_db_promotion

_ROUTES = Path(__file__).resolve().parents[2] / "src" / "api" / "routes"


class TestSchemas:
    def test_request_reset_defaults_false(self):
        assert QueryRequest(query="q").reset_db_scope is False
        assert QueryRequest(query="q", reset_db_scope=True).reset_db_scope is True

    def test_response_accepts_db_scope(self):
        r = QueryResponse(query_id="1", status="completed", response="",
                          db_scope={"zone_group": None, "solutions": [], "db_ids": [], "source": "none"})
        assert r.db_scope["source"] == "none"
        assert QueryResponse(query_id="1", status="completed", response="").db_scope is None


class TestFourPathSymmetry:
    @pytest.fixture(scope="class")
    def query_py(self) -> str:
        return (_ROUTES / "query.py").read_text(encoding="utf-8")

    @pytest.fixture(scope="class")
    def tree(self, query_py) -> ast.Module:
        return ast.parse(query_py)

    @staticmethod
    def _func(tree: ast.Module, name: str) -> ast.AST:
        return next(
            n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        )

    def _calls(self, tree: ast.Module, caller: str, callee: str) -> list[ast.Call]:
        return [
            n for n in ast.walk(self._func(tree, caller))
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
        ]

    def test_response_data_blocks_all_carry_db_scope(self, query_py, tree):
        """/query · /query/stream(astream+폴백) · /query/file · /query/file/stream(astream+폴백)
        = 6경로.

        응답 dict는 `_turn_response_data` 한 벌이다(plans/179 W1a — 종전 리터럴 6벌 계수의 전환):
        비스트림 두 진입은 `_build_turn_response`, 스트림 두 진입은 `_stream_graph_turn`(astream
        `_build_stream_turn_response` · 폴백 `_build_turn_response`)을 부르고, 두 빌더가 그 한 벌을
        부른다. 스트림(astream)은 종료 노드 델타(`output`)가 아니라 누적 상태(`_scope_state`)로
        만든다 — 델타로 만들면 3단 스트림의 db_ids가 빈다(plans/120 S-1).
        """
        pattern = r'"db_scope": build_db_scope\(state, selected_db_ids=ctx\.selected_db_ids\)'
        assert len(re.findall(pattern, query_py)) == 1
        assert "'db_scope': build_db_scope(state, selected_db_ids=ctx.selected_db_ids)" in (
            ast.unparse(self._func(tree, "_turn_response_data"))
        )
        for entry in ("process_query", "process_file_query"):
            assert len(self._calls(tree, entry, "_build_turn_response")) == 1, entry
        for entry in ("process_query_stream", "process_file_query_stream"):
            assert len(self._calls(tree, entry, "_stream_graph_turn")) == 1, entry
        assert len(self._calls(tree, "_stream_graph_turn", "_build_turn_response")) == 1
        (astream,) = self._calls(tree, "_stream_graph_turn", "_build_stream_turn_response")
        assert ast.unparse(astream.args[3]) == "_scope_state"
        for builder, state in (("_build_turn_response", "result"),
                               ("_build_stream_turn_response", "scope_state")):
            (data_call,) = self._calls(tree, builder, "_turn_response_data")
            assert ast.unparse(data_call.args[2]) == state, builder
        assert "build_db_scope(output," not in query_py

    def test_done_events_all_carry_db_scope(self, tree):
        """SSE done 4경로(텍스트·파일 × astream·폴백) — done 페이로드 한 벌
        (`_turn_done_payload`)."""
        assert "'db_scope': response_data.get('db_scope')" in ast.unparse(
            self._func(tree, "_turn_done_payload")
        )
        assert len(self._calls(tree, "_stream_graph_turn", "_turn_done_payload")) == 2
        for entry in ("process_query_stream", "process_file_query_stream"):
            assert len(self._calls(tree, entry, "_stream_graph_turn")) == 1, entry

    def test_text_routes_use_body_and_file_routes_use_form(self, tree):
        """db_scope 입력 선택 — 텍스트 진입은 요청 본문, 파일 진입은 폼 값
        (진입마다 `TurnContext`)."""
        for entry, selected, text_entry in (
            ("process_query", "body.selected_db_ids", "True"),
            ("process_query_stream", "body.selected_db_ids", "True"),
            ("process_file_query", "selected_list", "False"),
            ("process_file_query_stream", "selected_list", "False"),
        ):
            (ctx,) = self._calls(tree, entry, "TurnContext")
            kwargs = {kw.arg: ast.unparse(kw.value) for kw in ctx.keywords}
            assert kwargs["selected_db_ids"] == selected, entry
            assert kwargs["text_entry"] == text_entry, entry

    def test_reset_is_passed_to_followup_input(self, query_py):
        assert 'reset_db_scope=bool(getattr(body, "reset_db_scope", False))' in query_py


class TestPromotion:
    def test_origin_promoted_without_changing_target_shape(self):
        out = _collect_db_promotion(
            [{"task_id": "t1"}],
            {"t1": {"target_db_ids": ["polestar_b0"], "db_origin": "inherited"}},
        )
        assert out["target_databases"] == [{"db_id": "polestar_b0"}]
        assert out["db_scope_source"] == "inherited"

    def test_no_origin_no_key(self):
        out = _collect_db_promotion([{"task_id": "t1"}], {"t1": {"target_db_ids": ["polestar_b0"]}})
        assert "db_scope_source" not in out


class TestScopeOptionsRoute:
    @pytest.fixture(scope="class")
    def client(self):
        return TestClient(create_app())

    def test_axes_payload(self, client):
        res = client.get("/api/v1/scope/options")
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["axes"][0]["axis"] == "zone_group"
        assert isinstance(body["axes"][0]["options"], list)
        for o in body["axes"][0]["options"]:
            assert set(o) >= {"key", "label", "group", "db_ids"}


class TestMixedZoneOpening:
    """D-206: 상호배타 옵트아웃 시 계약 — 문구·처리현황 그룹 요약·혼합 선택 통과."""

    def test_question_explains_sequential_query(self):
        from src.utils.query_gen_common import ZONE_CLARIFY_QUESTION, build_zone_clarification

        assert "은행존을 먼저 조회한 뒤 공동존을 조회합니다" in ZONE_CLARIFY_QUESTION
        payload = build_zone_clarification(["polestar_b0", "polestar_cm_gp"], "q", group_exclusive=False)
        assert "group_exclusive" not in payload and "먼저 조회" in payload["question"]

    def test_summarize_tasks_carries_group_results(self):
        from src.api.routes.query import _summarize_tasks

        gr = {"polestar:bank": {"label": "은행존", "row_count": 1, "elapsed_ms": 1.0, "errors": {}},
              "polestar:common": {"label": "공동존", "row_count": 2, "elapsed_ms": 2.0, "errors": {}}}
        items = _summarize_tasks(
            [{"task_id": "t1", "agent": "data_query", "sub_query": "q", "order": 1, "status": "completed"}],
            {"t1": {"target_db_ids": ["polestar_b0", "polestar_cm_gp"], "group_results": gr, "query_results": []}},
        )
        assert items[0]["group_results"] == gr

    def test_scope_options_not_exclusive_when_flag_off(self):
        from src.routing.db_scope import scope_axes_options

        assert scope_axes_options(["polestar_b0", "polestar_cm_gp"], group_exclusive=False)["axes"][0]["exclusive"] is False

    def test_mixed_selection_passes_gate_when_flag_off(self):
        from types import SimpleNamespace

        from src.api.routes.query import _zone_group_exclusive_or_none

        cfg = SimpleNamespace(multi_db=SimpleNamespace(
            get_active_db_ids=lambda: ["polestar_b0", "polestar_cm_gp"], zone_group_exclusive=False))
        assert _zone_group_exclusive_or_none("q", ["polestar_b0", "polestar_cm_gp"], cfg) is None
