"""멀티 DB 순위 질의 전역 재정렬·재LIMIT(S-1)과 응답 미리보기 DB 균형(S-2) — plans/113.

결함(D-202 잔여 ① · plans/113 §2.2): 병합이 이어 붙이기뿐이라 DB마다 `LIMIT 10`이면 결과는
"김포 10행 + 여의도 10행"이고 전체 top 10으로 다시 정렬되지 않았다. 응답 LLM은 앞 20행만 보고
그 20행이 DB 순서대로 잘려, 앞 DB 행이 20건 이상이면 뒤 DB 행이 하나도 보이지 않았다.

수정: 각 DB가 **실행한 SQL**의 최외곽 ORDER BY 첫 키·방향·행 상한을 결정적으로 읽어(LLM 0),
모든 DB가 같으면 전역 재정렬 후 N행으로 자른다. 원본 병합(`query_results` — CSV 원천)은 그대로다.
LLM·Redis·DB 0.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.nodes.multi_db_executor import _merge_results
from src.nodes.output_generator import (
    _append_zone_coverage_notes,
    _build_response_prompt,
    _preview_rows,
)
from src.nodes.result_merger import (
    REASON_AGG_MISMATCH,
    REASON_AGG_UNREADABLE,
    REASON_KEY_MISMATCH,
    REASON_KEY_NOT_IN_RESULT,
    REASON_KEY_NOT_ORDERABLE,
    REASON_NULL_KEYS,
    REASON_SET_OPERATION,
    REASON_SQL_UNREADABLE,
    parse_rank_spec,
    plan_aggregate_synthesis,
    plan_global_ranking,
    result_merger,
)
from src.nodes.result_organizer import result_organizer

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
_PG_SQL = (
    'SELECT r.hostname AS "서버명", MAX(s.cpu) AS "최대 CPU" FROM x r JOIN y s ON 1=1 '
    'GROUP BY r.hostname ORDER BY "최대 CPU" DESC NULLS LAST LIMIT 10'
)
_CONFIG = SimpleNamespace(query=SimpleNamespace(default_limit=1000))


def _rows(prefix: str, values: list[float | None], key: str = "최대 CPU") -> list[dict]:
    return [{"서버명": f"{prefix}-{i:02d}", key: v} for i, v in enumerate(values)]


def _state(db_results: dict, sqls: dict, **extra) -> dict:
    state = {
        "db_results": db_results,
        "db_errors": {},
        "db_executed_sqls": sqls,
        "query_results": _merge_results(db_results),
        "parsed_requirements": {"query_targets": ["cpu"], "output_format": "text"},
    }
    state.update(extra)
    return state


# gp는 90~81, yd는 95·94·93 뒤에 낮은 값 — 전역 상위 10에 yd 3행이 들어가야 한다
_GP_ROWS = _rows("gp", [90, 89, 88, 87, 86, 85, 84, 83, 82, 81])
_YD_ROWS = _rows("yd", [95, 94, 93, 50, 49, 48, 47, 46, 45, 44])


class TestParseRankSpec:
    def test_reads_outermost_order_key_direction_and_limit(self):
        spec, reason = parse_rank_spec(_PG_SQL)
        assert reason is None
        assert (spec.key, spec.descending, spec.limit) == ("최대 CPU", True, 10)

    def test_db2_fetch_first_and_isolation_clause(self):
        spec, _ = parse_rank_spec(
            "SELECT h AS host, MAX(c) AS max_cpu FROM t GROUP BY h "
            "ORDER BY max_cpu DESC NULLS LAST FETCH FIRST 10 ROWS ONLY WITH UR"
        )
        assert (spec.key, spec.descending, spec.limit) == ("max_cpu", True, 10)

    def test_inner_order_by_is_not_outermost(self):
        sql = "SELECT * FROM (SELECT h FROM t ORDER BY h LIMIT 5) s"
        assert parse_rank_spec(sql) == (None, None)

    def test_order_without_row_limit_is_not_ranking(self):
        assert parse_rank_spec("SELECT h FROM t ORDER BY h DESC") == (None, None)

    def test_expression_key_maps_to_select_alias(self):
        spec, _ = parse_rank_spec(
            "SELECT t.h, MAX(s.v) AS mx FROM t GROUP BY t.h ORDER BY MAX(s.v) DESC LIMIT 5"
        )
        assert spec.key == "mx"

    def test_qualified_key_maps_to_alias(self):
        spec, _ = parse_rank_spec(
            "SELECT a.id, a.ctime AS alarm_time FROM x a ORDER BY a.ctime DESC LIMIT 10"
        )
        assert spec.key == "alarm_time"

    def test_union_is_excluded(self):
        """G-3 — 한 SQL이 여러 순위 목록을 합치는 형태는 적용 제외."""
        assert parse_rank_spec(
            "SELECT 1 AS a FROM t UNION ALL SELECT 2 FROM u ORDER BY 1 LIMIT 10"
        ) == (None, REASON_SET_OPERATION)

    def test_keywords_split_across_lines(self):
        """LLM SQL은 `ORDER\nBY`·`NULLS\nLAST`처럼 줄을 나누기도 한다 — 같은 키워드로 읽는다."""
        spec, _ = parse_rank_spec(
            "SELECT h, v AS x FROM t ORDER\nBY x DESC NULLS\nLAST\nLIMIT 5"
        )
        assert (spec.key, spec.descending, spec.limit, spec.nulls) == ("x", True, 5, "LAST")

    def test_unreadable_limit_reports_reason(self):
        assert parse_rank_spec("SELECT h FROM t ORDER BY h LIMIT ?")[1] == REASON_SQL_UNREADABLE


class TestGlobalRanking:
    @pytest.mark.asyncio
    async def test_global_top_n_includes_other_db_rows(self):
        """gp top10 + yd top10 → 전역 top10에 yd 행(95·94·93)이 들어간다."""
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL})
        out = await result_merger(state, app_config=_CONFIG)

        ranking = out["merged_ranking"]
        assert ranking["applied"] is True
        values = [r["최대 CPU"] for r in ranking["rows"]]
        assert values == [95, 94, 93, 90, 89, 88, 87, 86, 85, 84]
        assert [r["_source_db"] for r in ranking["rows"][:3]] == [_YD, _YD, _YD]
        assert ranking["source_row_count"] == 20
        # 원본 병합(CSV 원천)은 그대로 — DB별 전체 행(G-4)
        assert len(out["query_results"]) == 20
        assert out["query_results"] == state["query_results"]

    @pytest.mark.asyncio
    async def test_key_mismatch_keeps_concat_with_reason(self):
        other = _PG_SQL.replace('ORDER BY "최대 CPU"', 'ORDER BY "서버명"')
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: other})
        out = await result_merger(state, app_config=_CONFIG)
        assert out["merged_ranking"] == {"applied": False, "reason": REASON_KEY_MISMATCH}
        assert out["query_results"] == state["query_results"]

    @pytest.mark.asyncio
    async def test_limit_mismatch_is_a_key_mismatch(self):
        other = _PG_SQL.replace("LIMIT 10", "LIMIT 5")
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS[:5]}, {_GP: _PG_SQL, _YD: other})
        out = await result_merger(state, app_config=_CONFIG)
        assert out["merged_ranking"]["reason"] == REASON_KEY_MISMATCH

    @pytest.mark.asyncio
    async def test_single_db_is_noop(self):
        state = _state({_GP: _GP_ROWS}, {_GP: _PG_SQL})
        out = await result_merger(state, app_config=_CONFIG)
        assert out["merged_ranking"] is None
        assert out["query_results"] == state["query_results"]

    @pytest.mark.asyncio
    async def test_db2_lowercase_alias_matches_normalized_key(self):
        """b0(DB2)는 결과 칼럼 라틴 문자를 소문자로 돌려준다 — 정규형 대조로 같은 키다."""
        gp = [{"host": f"gp-{i}", "Max_CPU": v} for i, v in enumerate([70, 60])]
        b0 = [{"host": f"b0-{i}", "max_cpu": v} for i, v in enumerate([80, 10])]
        sqls = {
            _GP: 'SELECT h AS host, MAX(c) AS "Max_CPU" FROM t GROUP BY h '
                 'ORDER BY "Max_CPU" DESC NULLS LAST LIMIT 2',
            _B0: "SELECT h AS host, MAX(c) AS Max_CPU FROM t GROUP BY h "
                 "ORDER BY Max_CPU DESC NULLS LAST FETCH FIRST 2 ROWS ONLY",
        }
        state = _state({_B0: b0, _GP: gp}, sqls)
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert ranking["applied"] is True
        assert [r["host"] for r in ranking["rows"]] == ["b0-0", "gp-0"]

    def test_nulls_last_and_ties_follow_registry_order(self):
        yd = _rows("yd", [5, None])
        gp = _rows("gp", [5, None])
        sql = _PG_SQL.replace("LIMIT 10", "LIMIT 2")
        state = _state({_YD: yd, _GP: gp}, {_YD: sql, _GP: sql})  # 실행 순서는 yd 먼저
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        # 동률(5)은 레지스트리 선언 순(gp → yd), NULL은 뒤로 — 상위 2건
        assert [r["서버명"] for r in ranking["rows"]] == ["gp-00", "yd-00"]

    def test_iso_datetime_strings_are_ranked_chronologically(self):
        sql = ("SELECT a.id, a.ctime AS alarm_time FROM x a "
               "ORDER BY a.ctime DESC LIMIT 2")
        gp = [{"id": 1, "alarm_time": "2026-09-20T10:00:00"},
              {"id": 2, "alarm_time": "2026-09-01T10:00:00"}]
        yd = [{"id": 3, "alarm_time": "2026-09-21T09:00:00"},
              {"id": 4, "alarm_time": "2026-08-01T10:00:00"}]
        state = _state({_GP: gp, _YD: yd}, {_GP: sql, _YD: sql})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert [r["id"] for r in ranking["rows"]] == [3, 1]

    def test_plain_string_key_is_not_reranked(self):
        """일반 문자열은 DB 정렬 규칙과 파이썬 순서가 달라 전역 순위를 보장할 수 없다."""
        sql = _PG_SQL.replace('ORDER BY "최대 CPU" DESC', 'ORDER BY "서버명" DESC')
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: sql, _YD: sql})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert ranking == {"applied": False, "reason": REASON_KEY_NOT_ORDERABLE}

    def test_union_sql_is_excluded_with_reason(self):
        sql = "SELECT 1 AS a FROM t UNION ALL SELECT 2 FROM u ORDER BY 1 LIMIT 10"
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: sql, _YD: sql})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert ranking == {"applied": False, "reason": REASON_SET_OPERATION}

    def test_listing_without_ranking_shape_is_noop(self):
        sql = 'SELECT r.hostname AS "서버명" FROM x r'
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: sql, _YD: sql})
        assert plan_global_ranking(state, state["query_results"], _CONFIG) is None

    def test_default_safety_cap_is_not_a_ranking_request(self):
        sql = _PG_SQL.replace("LIMIT 10", "LIMIT 1000")
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: sql, _YD: sql})
        assert plan_global_ranking(state, state["query_results"], _CONFIG) is None

    def test_form_turn_is_excluded(self):
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL},
                       template_structure={"sheets": []})
        assert plan_global_ranking(state, state["query_results"], _CONFIG) is None

    def test_missing_executed_sql_is_not_silent(self):
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert ranking == {"applied": False, "reason": REASON_SQL_UNREADABLE}

    def test_no_executed_sqls_at_all_is_noop(self):
        """실행 SQL을 전혀 모르면(순위 형태 판정 불가) 노트 없이 종전 이어 붙이기다."""
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {})
        assert plan_global_ranking(state, state["query_results"], _CONFIG) is None


class TestOrganizerUsesRankedRows:
    @pytest.mark.asyncio
    async def test_organized_rows_are_global_top_n_and_csv_source_is_untouched(self):
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL})
        state.update(await result_merger(state, app_config=_CONFIG))
        out = await result_organizer(state)

        organized = out["organized_data"]
        assert [r["서버명"] for r in organized["rows"][:3]] == ["yd-00", "yd-01", "yd-02"]
        assert len(organized["rows"]) == 10
        assert organized["merge_ranking"]["applied"] is True
        assert "rows" not in organized["merge_ranking"]
        assert "전체 기준 상위 10건" in organized["summary"]
        assert len(state["query_results"]) == 20  # CSV 원천은 원본 그대로

    @pytest.mark.asyncio
    async def test_stale_ranking_is_ignored_after_single_db_retry(self):
        """재시도로 단일 경로 행(출처 태그 없음)이 들어오면 직전 재정렬 결과를 쓰지 않는다."""
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL})
        state.update(await result_merger(state, app_config=_CONFIG))
        state["query_results"] = [{"서버명": "single-01", "최대 CPU": 1.0}]
        out = await result_organizer(state)
        assert [r["서버명"] for r in out["organized_data"]["rows"]] == ["single-01"]
        assert "merge_ranking" not in out["organized_data"]


class TestResponsePreviewBalance:
    def test_single_db_preview_is_head_twenty(self):
        rows = [{"host": f"h{i}", "v": i} for i in range(25)]
        preview, balanced = _preview_rows(rows, ranked=False)
        assert preview == rows[:20] and balanced is False

    def test_single_db_prompt_is_unchanged(self):
        """단일 DB(출처 태그 없음)는 종전 프롬프트와 같다 — 표시 행·절단 문구."""
        rows = [{"그룹|서브": i, "v": i} for i in range(25)]
        prompt = _build_response_prompt("q", "s", rows)
        body = prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
        assert json.loads(body) == [{"그룹 > 서브": i, "v": i} for i in range(20)]
        assert "## 조회 결과 (25건, 상위 20건 표시)" in prompt
        assert "위 JSON은 표시용으로 상위 20건만 실은 것입니다" in prompt
        assert "출처" not in prompt

    def test_multi_db_preview_includes_every_db(self):
        """gp 25행 + yd 5행 → 미리보기에 yd 행이 있다(DB마다 1행 이상)."""
        rows = _merge_results({
            _GP: _rows("gp", list(range(25))), _YD: _rows("yd", list(range(5))),
        })
        preview, balanced = _preview_rows(rows, ranked=False)
        assert balanced is True and len(preview) == 20
        assert {r["_source_db"] for r in preview} == {_GP, _YD}
        assert sum(r["_source_db"] == _YD for r in preview) == 5
        # 원래 순서 유지(gp 블록 → yd 블록)
        assert preview == sorted(preview, key=rows.index)

    def test_ranked_rows_keep_head(self):
        rows = _merge_results({
            _GP: _rows("gp", list(range(25))), _YD: _rows("yd", list(range(5))),
        })
        preview, balanced = _preview_rows(rows, ranked=True)
        assert preview == rows[:20] and balanced is False

    def test_multi_db_prompt_shows_source_display_name(self):
        from src.routing.domain_config import get_domain_by_id

        rows = _merge_results({
            _GP: _rows("gp", list(range(25))), _YD: _rows("yd", list(range(5))),
        })
        prompt = _build_response_prompt("q", "s", rows)
        assert "DB별로 고르게 20건 표시" in prompt
        assert '"_source_db"' not in prompt
        assert get_domain_by_id(_YD).display_name in prompt


class TestZoneCountLineWithRanking:
    _SUMMARY = {
        _GP: {"row_count": 10, "display_name": "김포"},
        _YD: {"row_count": 10, "display_name": "여의도"},
    }

    def test_applied_ranking_shows_top_n_distribution(self):
        rows = [{"_source_db": _YD}] * 3 + [{"_source_db": _GP}] * 7
        state = {
            "db_result_summary": self._SUMMARY,
            "organized_data": {"rows": rows, "merge_ranking": {"applied": True}},
        }
        out = _append_zone_coverage_notes("응답", state)
        assert out == (
            "응답\n\n**[존별 결과]** 김포 10건 · 여의도 10건"
            " → 전체 기준 상위 10건(김포 7건 · 여의도 3건)"
        )

    def test_skipped_ranking_states_reason(self):
        state = {
            "db_result_summary": self._SUMMARY,
            "organized_data": {"rows": [{"_source_db": _GP}, {"_source_db": _YD}],
                               "merge_ranking": {"applied": False, "reason": REASON_KEY_MISMATCH}},
        }
        out = _append_zone_coverage_notes("응답", state)
        assert REASON_KEY_MISMATCH in out
        assert "존별 결과를 이어 붙였습니다" in out


class TestRankingGuards:
    """재정렬이 "전체 기준 상위 N"을 보장하지 못하는 형태는 적용하지 않는다(코드 리뷰 반영)."""

    def test_per_zone_request_is_not_globally_cut(self):
        """"김포·여의도 각각 상위 5"는 DB별 목록 요청 — 전역 한 표로 자르지 않는다(노트 없음)."""
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL},
                       user_query="김포·여의도 각각 CPU 상위 10 서버")
        assert plan_global_ranking(state, state["query_results"], _CONFIG) is None

    def test_global_request_wording_still_ranks(self):
        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL},
                       user_query="공동존의 vm 중 최대 cpu와 최대 memory 서버 top 10을 정리해줘")
        assert plan_global_ranking(state, state["query_results"], _CONFIG)["applied"] is True

    def test_desc_without_nulls_last_truncated_with_null_is_skipped(self):
        """PG·DB2는 DESC에서 NULL을 앞에 둔다 — N행에서 잘렸는데 NULL이 있으면 값 있는 상위 행이
        병합에 오지 않았을 수 있다."""
        sql = ('SELECT r.hostname AS "서버명", s.cpu AS "최대 CPU" FROM x '
               'ORDER BY "최대 CPU" DESC LIMIT 2')
        gp = _rows("gp", [None, 90])
        yd = _rows("yd", [80, 70])
        state = _state({_GP: gp, _YD: yd}, {_GP: sql, _YD: sql})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert ranking == {"applied": False, "reason": REASON_NULL_KEYS}

    def test_explicit_nulls_last_is_safe(self):
        sql = ('SELECT r.hostname AS "서버명", s.cpu AS "최대 CPU" FROM x '
               'ORDER BY "최대 CPU" DESC NULLS LAST LIMIT 2')
        gp = _rows("gp", [90, None])
        yd = _rows("yd", [80, 70])
        state = _state({_GP: gp, _YD: yd}, {_GP: sql, _YD: sql})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert [r["서버명"] for r in ranking["rows"]] == ["gp-00", "yd-00"]

    def test_not_truncated_db_with_null_is_safe(self):
        """행 수가 상한보다 적으면 그 DB는 전부 왔다 — NULL이 있어도 누락이 없다."""
        sql = ('SELECT r.hostname AS "서버명", s.cpu AS "최대 CPU" FROM x '
               'ORDER BY "최대 CPU" DESC LIMIT 3')
        gp = _rows("gp", [None, 90])
        yd = _rows("yd", [80, 70, 60])
        state = _state({_GP: gp, _YD: yd}, {_GP: sql, _YD: sql})
        ranking = plan_global_ranking(state, state["query_results"], _CONFIG)
        assert [r["서버명"] for r in ranking["rows"]] == ["gp-01", "yd-00", "yd-01"]

    def test_qualified_key_colliding_with_other_alias_is_not_read(self):
        """`ORDER BY s.value`인데 결과 `value`는 다른 식의 별칭 — 다른 값으로 정렬하게 된다."""
        spec, reason = parse_rank_spec(
            "SELECT h, s.avg_value AS value FROM t s ORDER BY s.value DESC LIMIT 5"
        )
        assert spec is None and reason == REASON_KEY_NOT_IN_RESULT


class TestDownstreamConsistency:
    """재정렬이 적용되면 선행 결과 전달도 응답에 보인 전체 상위 N을 쓴다 · 잔존 요약은 버린다."""

    @pytest.mark.asyncio
    async def test_pipeline_result_rows_follow_ranking_for_prior_rows(self):
        from src.orchestration.subagents import _pack_pipeline_result

        state = _state({_GP: _GP_ROWS, _YD: _YD_ROWS}, {_GP: _PG_SQL, _YD: _PG_SQL})
        state.update(await result_merger(state, app_config=_CONFIG))
        state.update(await result_organizer(state))
        res = _pack_pipeline_result(
            state, [{"db_id": _GP}, {"db_id": _YD}], None,
            ownership_notes=[], db_origin="hint", db_succeeded=False, db_pinned=True,
        )
        assert [r["서버명"] for r in res["rows"]][:3] == ["yd-00", "yd-01", "yd-02"]
        assert len(res["rows"]) == 10
        assert len(res["query_results"]) == 20  # CSV 원천은 원본

    def test_pipeline_result_has_no_rows_key_without_ranking(self):
        from src.orchestration.subagents import _pack_pipeline_result

        res = _pack_pipeline_result(
            {"query_results": [{"h": 1}], "organized_data": {"rows": [{"h": 1}]}},
            [{"db_id": _GP}], None,
            ownership_notes=[], db_origin="classified", db_succeeded=False, db_pinned=False,
        )
        assert "rows" not in res

    def test_followup_turn_clears_multi_db_summaries(self):
        from src.state import create_followup_input

        delta = create_followup_input("그 서버 메모리는?")
        assert delta["db_result_summary"] is None
        assert delta["db_executed_sqls"] is None
        assert delta["merged_ranking"] is None

    def test_zone_line_is_dropped_when_current_rows_are_single_db(self):
        """같은 요청 안 단일 경로 재시도 — 앞선 병합 요약이 남아도 이번 행에 출처가 없으면
        싣지 않는다."""
        state = {
            "db_result_summary": {
                _GP: {"row_count": 10, "display_name": "김포"},
                _YD: {"row_count": 10, "display_name": "여의도"},
            },
            "organized_data": {"rows": [{"서버명": "single-01"}]},
        }
        assert _append_zone_coverage_notes("응답", state) == "응답"


# ──────────────────────────────────────────────
# S-3 — 집계 질의 종합 (G-5 (가) · 평균은 존별만)
# ──────────────────────────────────────────────

_AGG_SQL = (
    "SELECT COUNT(*) AS server_count, SUM(r.cores) AS total_cores, MAX(r.cpu) AS max_cpu, "
    "MIN(r.cpu) AS min_cpu, AVG(r.cpu) AS avg_cpu FROM x r WHERE r.kind = 'vm'"
)


def _agg_row(count, cores, mx, mn, avg) -> list[dict]:
    return [{"server_count": count, "total_cores": cores, "max_cpu": mx, "min_cpu": mn,
             "avg_cpu": avg}]


class TestAggregateSynthesis:
    def _state(self, sql_gp=_AGG_SQL, sql_yd=_AGG_SQL, **extra):
        return _state(
            {_GP: _agg_row(12, 96, 91.5, 3.0, 40.0), _YD: _agg_row(8, 64, 88.0, 1.5, 30.0)},
            {_GP: sql_gp, _YD: sql_yd}, **extra,
        )

    @pytest.mark.asyncio
    async def test_totals_are_code_computed_and_avg_has_no_total(self):
        """gp COUNT 12 + yd COUNT 8 → 전체 20 · SUM 더함 · MAX/MIN · AVG는 DB별만."""
        out = await result_merger(self._state(), app_config=_CONFIG)
        agg = out["merged_aggregates"]
        assert agg["applied"] is True
        by = {c["column"]: c for c in agg["columns"]}
        assert by["server_count"]["kind"] == "COUNT" and by["server_count"]["total"] == 20
        assert by["total_cores"]["total"] == 160
        assert by["max_cpu"]["total"] == 91.5
        assert by["min_cpu"]["total"] == 1.5
        assert by["avg_cpu"]["kind"] == "AVG" and by["avg_cpu"]["total"] is None
        assert by["avg_cpu"]["per_db"] == {_GP: 40.0, _YD: 30.0}
        # 집계 결과는 순위 목록이 아니다 — S-1과 배타
        assert out["merged_ranking"] is None
        assert out["query_results"] == self._state()["query_results"]  # 원본 그대로(G-4)

    def test_count_distinct_and_wrapped_expressions_have_no_total(self):
        sql = "SELECT COUNT(DISTINCT r.host) AS hosts, ROUND(AVG(r.cpu), 2) AS avg_cpu FROM x r"
        state = _state({_GP: [{"hosts": 5, "avg_cpu": 1.0}], _YD: [{"hosts": 3, "avg_cpu": 2.0}]},
                       {_GP: sql, _YD: sql})
        agg = plan_aggregate_synthesis(state, state["query_results"])
        assert [(c["kind"], c["total"]) for c in agg["columns"]] == [
            ("COUNT_DISTINCT", None), ("OTHER", None),
        ]

    def test_mismatched_aggregate_shapes_are_not_combined(self):
        other = _AGG_SQL.replace("AVG(r.cpu) AS avg_cpu", "MAX(r.cpu) AS avg_cpu")
        agg = plan_aggregate_synthesis(self._state(sql_yd=other), self._state()["query_results"])
        assert agg == {"applied": False, "reason": REASON_AGG_MISMATCH}

    def test_unreadable_sql_is_reported(self):
        agg = plan_aggregate_synthesis(self._state(sql_yd="SELECT 1; SELECT 2"),
                                       self._state()["query_results"])
        assert agg == {"applied": False, "reason": REASON_AGG_UNREADABLE}

    def test_single_db_is_noop(self):
        state = _state({_GP: _agg_row(12, 96, 91.5, 3.0, 40.0)}, {_GP: _AGG_SQL})
        assert plan_aggregate_synthesis(state, state["query_results"]) is None

    def test_grouped_or_listing_results_are_not_aggregates(self):
        sql = "SELECT r.os AS os, COUNT(*) AS n FROM x r GROUP BY r.os"
        state = _state({_GP: [{"os": "linux", "n": 3}], _YD: [{"os": "linux", "n": 2}]},
                       {_GP: sql, _YD: sql})
        assert plan_aggregate_synthesis(state, state["query_results"]) is None

    def test_form_turn_is_excluded(self):
        state = self._state(template_structure={"sheets": []})
        assert plan_aggregate_synthesis(state, state["query_results"]) is None

    @pytest.mark.asyncio
    async def test_prompt_and_zone_lines_carry_per_db_and_total(self):
        state = self._state()
        state.update(await result_merger(state, app_config=_CONFIG))
        state.update(await result_organizer(state))
        organized = state["organized_data"]
        prompt = _build_response_prompt(
            "q", organized["summary"], organized["rows"],
            aggregates=organized.get("merge_aggregates"),
        )
        assert "## 수치 요약 (DB별 집계 — 코드 계산값)" in prompt
        assert "→ 전체 20" in prompt
        assert "(전체 값 없음)" in prompt
        # DB별 값끼리의 최소·최대·평균(DB를 넘나드는 무의미한 통계)은 싣지 않는다
        assert "전수 기준" not in prompt
        note = _append_zone_coverage_notes("응답", state)
        assert "**[존별 집계]**" in note
        assert "전체 20" in note

    def test_skip_reason_reaches_zone_line(self):
        state = {
            "db_result_summary": {_GP: {"row_count": 1, "display_name": "김포"},
                                  _YD: {"row_count": 1, "display_name": "여의도"}},
            "organized_data": {"rows": [{"_source_db": _GP}, {"_source_db": _YD}],
                               "merge_aggregates": {"applied": False,
                                                    "reason": REASON_AGG_MISMATCH}},
        }
        out = _append_zone_coverage_notes("응답", state)
        assert REASON_AGG_MISMATCH in out and "DB별 집계 값을 합치지 않았습니다" in out

    def test_single_db_prompt_unchanged_by_aggregates_param(self):
        rows = [{"n": 3}]
        assert _build_response_prompt("q", "s", rows) == _build_response_prompt(
            "q", "s", rows, aggregates=None
        )
