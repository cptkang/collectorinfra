"""스레드 DB 스코프 API 계약 (plans/90 · D-205 · SPEC-thread-db-scope §Success 6).

★ 응답 키는 **4경로 동시**여야 한다 — `scope_reexpand`가 `/query`에만 실리고 SSE done에는 빠져
   범위 재확장 패널이 스트리밍에서 뜨지 않았던 선례(plans/90 §1.2)를 반복하지 않는다. 라우트 코드에
   `"db_scope"` 삽입 지점이 6(응답 조립)+4(done 이벤트)곳 있는지 정적으로 센다.
LLM·DB 0.
"""

from __future__ import annotations

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

    def test_response_data_blocks_all_carry_db_scope(self, query_py):
        """/query · /query/stream(astream+폴백) · /query/file · /query/file/stream(astream+폴백) = 6."""
        assert len(re.findall(r'"db_scope": build_db_scope\((result|output), selected_db_ids=', query_py)) == 6

    def test_done_events_all_carry_db_scope(self, query_py):
        """SSE done 이벤트 4곳(텍스트 astream·폴백 · 파일 astream·폴백)."""
        assert query_py.count('"db_scope": response_data.get("db_scope")') == 4

    def test_text_routes_use_body_and_file_routes_use_form(self, query_py):
        assert query_py.count("selected_db_ids=body.selected_db_ids)") == 3
        assert query_py.count("selected_db_ids=selected_list)") == 3

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
