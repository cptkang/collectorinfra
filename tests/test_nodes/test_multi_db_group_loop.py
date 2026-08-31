"""실행 그룹 순차 루프 (D-176 · plans/82 §4.9 · SPEC-group-runner).

핵심 계약 둘:
  1. `execution_groups` 미설정이면 **현행 경로와 동일**하다(회귀 0) — 이 모듈은 소비처를
     만들지 않으므로 실제 런타임은 항상 이 경로로 돈다.
  2. 그룹이 2개 이상이면 **레지스트리 query_order 순**으로 순차 실행한다
     (LLM relevance_score가 아니라 — D-035).

전부 mock — LLM·MCP·DB 미사용(D-127).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"


def _result(rows=None):
    r = MagicMock()
    r.rows = rows if rows is not None else [{"a": 1}]
    r.row_count = len(r.rows)
    return r


def _run_executor(state, *, exec_result=None, exec_side_effect=None):
    """multi_db_executor를 완전 mock 환경에서 돌리고 (반환값, 실행된 db_id 순서)를 준다."""
    import src.nodes.multi_db_executor as mod

    seen: list[str] = []

    async def _fake_run_single(target, run):
        seen.append(target["db_id"])
        if exec_side_effect and target["db_id"] in exec_side_effect:
            run.db_errors[target["db_id"]] = exec_side_effect[target["db_id"]]
            return
        run.db_results[target["db_id"]] = list((exec_result or _result()).rows)

    def _fake_prepare(st, llm, cfg):
        run = MagicMock()
        run.state = st
        run.db_results, run.db_errors, run.db_schemas = {}, {}, {}
        run.all_attempts, run.validation_failed = [], {}
        run.sql_by_schema = {}
        run.mc_candidates, run.mc_derivations = [], []
        run.form_fill_out = {}
        return run

    with patch.object(mod, "_run_single_target", AsyncMock(side_effect=_fake_run_single)), \
         patch.object(mod, "_prepare_multi_run", AsyncMock(side_effect=_fake_prepare)):
        out = asyncio.run(mod.multi_db_executor(state, llm=MagicMock(), app_config=MagicMock()))
    return out, seen


def _targets(*db_ids):
    return [{"db_id": d, "relevance_score": 0.9, "sub_query_context": "q"} for d in db_ids]


class TestSingleGroupParity:
    """`execution_groups` 미설정 = 현행 동작(회귀 0)."""

    def test_execution_order_unchanged(self):
        state = {"user_query": "q", "target_databases": _targets(_B0, _GP, _YD)}
        _, seen = _run_executor(state)
        assert seen == [_B0, _GP, _YD]

    def test_return_keys_unchanged(self):
        """하류 소비처가 읽는 키 집합이 그대로여야 한다."""
        state = {"user_query": "q", "target_databases": _targets(_B0)}
        out, _ = _run_executor(state)
        for key in ("db_results", "db_schemas", "db_errors", "query_results",
                    "query_attempts", "current_node", "error_message"):
            assert key in out, key
        assert out["current_node"] == "multi_db_executor"

    def test_no_group_fields_when_not_grouped(self):
        """그룹 미사용 턴은 그룹 산출물을 만들지 않는다(상태 오염 방지)."""
        state = {"user_query": "q", "target_databases": _targets(_B0)}
        out, _ = _run_executor(state)
        assert not out.get("group_packets")


class TestGroupSequencing:
    """그룹 2개 — query_order 순차."""

    @staticmethod
    def _grouped_state():
        from src.routing.execution_groups import partition_execution_groups

        return {
            "user_query": "q",
            "target_databases": _targets(_YD, _GP, _B0),   # 일부러 뒤섞음
            "execution_groups": partition_execution_groups([_B0, _GP, _YD]),
        }

    def test_bank_group_runs_before_common(self):
        """사용자 요구: 은행존을 먼저, 완료되면 공동존."""
        _, seen = _run_executor(self._grouped_state())
        assert seen == [_B0, _GP, _YD]

    def test_group_results_recorded_per_group(self):
        out, _ = _run_executor(self._grouped_state())
        gr = out["group_results"]
        assert set(gr) == {"polestar:bank", "polestar:common"}
        for key, res in gr.items():
            assert "row_count" in res and "elapsed_ms" in res
            assert res["elapsed_ms"] >= 0

    def test_group_row_counts(self):
        out, _ = _run_executor(self._grouped_state())
        gr = out["group_results"]
        assert gr["polestar:bank"]["row_count"] == 1        # b0 1건
        assert gr["polestar:common"]["row_count"] == 2      # gp+yd 각 1건

    def test_failure_isolated_between_groups(self):
        """앞 그룹 전면 실패가 뒤 그룹을 막지 않는다."""
        out, seen = _run_executor(
            self._grouped_state(), exec_side_effect={_B0: "boom"}
        )
        assert seen == [_B0, _GP, _YD]                     # 뒤 그룹 실행됨
        assert out["db_errors"].get(_B0) == "boom"
        assert set(out["db_results"]) == {_GP, _YD}
        assert out["group_results"]["polestar:bank"]["errors"]

    def test_merged_results_still_produced(self):
        """전역 병합은 계속 생성한다 — CSV·row_count 하류 호환."""
        out, _ = _run_executor(self._grouped_state())
        assert len(out["query_results"]) == 3
        assert {r["_source_db"] for r in out["query_results"]} == {_B0, _GP, _YD}


class TestGroupPackets:
    """부분 결과 — peer 그룹만 적재한다(문헌 정정 ② · Online Aggregation)."""

    def test_peer_groups_emit_packets(self):
        out, _ = _run_executor(TestGroupSequencing._grouped_state())
        packets = out["group_packets"]
        assert [p["group_key"] for p in packets] == ["polestar:bank", "polestar:common"]
        assert packets[0]["label"] == "은행존"
        assert packets[0]["row_count"] == 1

    def test_packet_carries_rows_for_display(self):
        out, _ = _run_executor(TestGroupSequencing._grouped_state())
        assert out["group_packets"][0]["rows"]

    def test_non_peer_kind_does_not_emit(self):
        """discovery/dependent는 부분 결과를 내지 않는다(오해 방지)."""
        from src.routing.execution_groups import partition_execution_groups

        groups = partition_execution_groups([_B0, _GP, _YD])
        groups[1] = {**groups[1], "kind": "dependent"}
        state = {
            "user_query": "q",
            "target_databases": _targets(_B0, _GP, _YD),
            "execution_groups": groups,
        }
        out, _ = _run_executor(state)
        assert [p["group_key"] for p in out["group_packets"]] == ["polestar:bank"]


class TestResultMergerSummary:
    """`result_merger`가 만들고 버리던 요약을 반환한다(T13)."""

    def test_db_result_summary_returned(self):
        from src.nodes.result_merger import result_merger

        state = {
            "db_results": {_B0: [{"a": 1}], _GP: [{"a": 2}, {"a": 3}]},
            "db_errors": {},
            "query_results": [{"a": 1}, {"a": 2}, {"a": 3}],
        }
        out = asyncio.run(result_merger(state, app_config=MagicMock()))
        summary = out["db_result_summary"]
        assert summary[_B0]["row_count"] == 1
        assert summary[_GP]["row_count"] == 2

    def test_existing_keys_preserved(self):
        from src.nodes.result_merger import result_merger

        state = {"db_results": {_B0: [{"a": 1}]}, "db_errors": {}, "query_results": [{"a": 1}]}
        out = asyncio.run(result_merger(state, app_config=MagicMock()))
        for key in ("query_results", "error_message", "current_node"):
            assert key in out
