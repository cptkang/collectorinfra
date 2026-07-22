"""Text-to-SQL EX 하네스 순수 로직 단위 테스트 (Plan 61 / E1, D-072).

폐쇄망 CI 전제 — 실제 DB·LLM 없이 mock으로 통과해야 한다.
`scripts/eval_text2sql.py`는 패키지가 아니므로 importlib로 파일에서 직접 로드한다.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_PATH = _REPO_ROOT / "scripts" / "eval_text2sql.py"
_GOLD_DIR = _REPO_ROOT / "testdata" / "text2sql_gold"


def _load_harness():
    """scripts/eval_text2sql.py를 모듈로 로드한다."""
    spec = importlib.util.spec_from_file_location("eval_text2sql", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses + `from __future__ import annotations`는 cls.__module__을 sys.modules에서
    # 조회하므로, exec 전에 등록해야 한다.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


H = _load_harness()


# ──────────────────────────────────────────────
# execution_match — EX 채점 순수 로직
# ──────────────────────────────────────────────

class TestExecutionMatch:
    def test_identical_rows(self):
        g = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
        assert H.execution_match(g, [dict(r) for r in g]) is True

    def test_row_order_insensitive(self):
        g = [{"a": 1}, {"a": 2}]
        p = [{"a": 2}, {"a": 1}]
        assert H.execution_match(g, p) is True

    def test_order_sensitive_flag(self):
        g = [{"a": 1}, {"a": 2}]
        p = [{"a": 2}, {"a": 1}]
        assert H.execution_match(g, p, order_sensitive=True) is False

    def test_column_order_and_alias_insensitive(self):
        g = [{"hostname": "h1", "ip": "10.0.0.1"}]
        p = [{"server": "10.0.0.1", "name": "h1"}]  # 다른 별칭·순서, 같은 값집합
        assert H.execution_match(g, p) is True

    def test_int_float_equivalence(self):
        assert H.execution_match([{"a": 100}], [{"a": 100.0}]) is True

    def test_float_tolerance_pass(self):
        assert H.execution_match([{"a": 1.0000001}], [{"a": 1.0}], float_tol=1e-6) is True

    def test_float_tolerance_fail(self):
        assert H.execution_match([{"a": 1.001}], [{"a": 1.0}], float_tol=1e-6) is False

    def test_none_distinct_from_empty_string(self):
        assert H.execution_match([{"a": None}], [{"a": ""}]) is False

    def test_multiset_counts_matter(self):
        g = [{"a": 1}, {"a": 1}, {"a": 2}]
        p = [{"a": 1}, {"a": 2}, {"a": 2}]
        assert H.execution_match(g, p) is False

    def test_both_empty_match(self):
        assert H.execution_match([], []) is True

    def test_none_side_is_false(self):
        assert H.execution_match(None, [{"a": 1}]) is False
        assert H.execution_match([{"a": 1}], None) is False

    def test_tuple_rows(self):
        assert H.execution_match([(1, "x")], [(1, "x")]) is True


# ──────────────────────────────────────────────
# column_subset_match / column_subset_unmatched — EX-subset 보조 지표
# ──────────────────────────────────────────────

class TestColumnSubset:
    def test_subset_with_extra_pred_columns(self):
        g = [{"name": "a", "v": 1}, {"name": "b", "v": 2}]
        p = [{"n": "a", "x": 1, "extra": 9}, {"n": "b", "x": 2, "extra": 8}]
        assert H.column_subset_match(g, p) is True
        assert H.column_subset_unmatched(g, p) == []

    def test_duplicate_gold_columns_may_reuse_pred_column(self):
        """골드 중복 컬럼(알람 뷰 장비명=리소스명)은 같은 pred 컬럼 재사용을 허용한다.

        실측(2026-07-21 yd-006): 1:1 강제 시 pred가 이름 컬럼을 하나만 내면
        값이 전부 맞아도 subset 실패 — 값 멀티셋이 같으면 의미상 동치.
        """
        g = [{"server_name": "a", "resource_name": "a", "sev": 2},
             {"server_name": "b", "resource_name": "b", "sev": 3}]
        p = [{"name": "a", "sev": 2, "extra": 1},
             {"name": "b", "sev": 3, "extra": 2}]
        assert H.column_subset_match(g, p) is True

    def test_unmatched_reports_gold_column_names(self):
        g = [{"name": "a", "detail": "d1"}, {"name": "b", "detail": "d2"}]
        p = [{"n": "a", "other": "x"}, {"n": "b", "other": "y"}]
        assert H.column_subset_match(g, p) is False
        assert H.column_subset_unmatched(g, p) == ["detail"]

    def test_row_count_mismatch_returns_none(self):
        g = [{"a": 1}, {"a": 2}]
        p = [{"a": 1}]
        assert H.column_subset_match(g, p) is False
        assert H.column_subset_unmatched(g, p) is None

    def test_empty_results_match(self):
        assert H.column_subset_match([], []) is True
        assert H.column_subset_unmatched([], []) == []


# ──────────────────────────────────────────────
# is_select_only — 안전장치
# ──────────────────────────────────────────────

class TestSelectOnly:
    @pytest.mark.parametrize("sql", [
        "SELECT * FROM t",
        "select a from t where b = 1",
        "WITH x AS (SELECT 1) SELECT * FROM x",
        "SELECT a FROM t LIMIT 100;",
    ])
    def test_valid_select(self, sql):
        assert H.is_select_only(sql) is True

    @pytest.mark.parametrize("sql", [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a = 1",
        "DELETE FROM t",
        "DROP TABLE t",
        "SELECT 1; DROP TABLE t",  # 다중 문장
        "",
    ])
    def test_reject_non_select(self, sql):
        assert H.is_select_only(sql) is False

    def test_column_named_update_not_rejected(self):
        # update_time 같은 컬럼명은 \b 경계로 update 키워드와 구분되어야 함
        assert H.is_select_only("SELECT update_time, created_at FROM t") is True

    def test_trailing_semicolon_ok(self):
        assert H.is_select_only("SELECT 1;") is True


# ──────────────────────────────────────────────
# 골드셋 로드 / 검증 / 스키마 준수
# ──────────────────────────────────────────────

class TestGoldset:
    def test_load_real_goldset_nonempty(self):
        items = H.load_goldset(_GOLD_DIR)
        assert len(items) >= 20  # 계획서 목표(무리 시 20 이상)

    def test_real_goldset_valid(self):
        items = H.load_goldset(_GOLD_DIR)
        errors = H.validate_goldset(items)
        assert errors == [], f"골드셋 검증 위반: {errors}"

    def test_real_goldset_has_outside_representation(self):
        # 대표성 원칙: 실패/미처리(outside) 항목 최소 5건
        items = H.load_goldset(_GOLD_DIR)
        outside = [i for i in items if i.coverage == "outside"]
        assert len(outside) >= 5

    def test_all_items_have_coverage_field(self):
        # 트랙 C 커버리지율 측정을 위해 coverage 필수
        items = H.load_goldset(_GOLD_DIR)
        assert all(i.coverage in H.COVERAGES for i in items)

    def test_db_filter(self):
        gp = H.load_goldset(_GOLD_DIR, db_filter="gp")
        assert gp and all(i.db_id == "polestar_cm_gp" for i in gp)

    def test_all_gold_sql_select_only(self):
        items = H.load_goldset(_GOLD_DIR)
        assert all(H.is_select_only(i.gold_sql) for i in items)

    def test_validate_detects_violations(self):
        bad = [
            H.GoldItem(id="dup", query="q", db_id="d", gold_sql="SELECT 1",
                       category="server_config", coverage="inside"),
            H.GoldItem(id="dup", query="", db_id="", gold_sql="DELETE FROM t",
                       category="bogus", coverage="maybe"),
        ]
        errors = H.validate_goldset(bad)
        joined = " ".join(errors)
        assert "id 중복" in joined
        assert "query 누락" in joined
        assert "SELECT 전용" in joined
        assert "category" in joined
        assert "coverage" in joined


# ──────────────────────────────────────────────
# 커버리지율 집계
# ──────────────────────────────────────────────

class TestCoverageStats:
    def _items(self):
        return [
            H.GoldItem("a", "q", "d", "SELECT 1", "server_config", "inside"),
            H.GoldItem("b", "q", "d", "SELECT 1", "performance", "inside"),
            H.GoldItem("c", "q", "d", "SELECT 1", "complex", "outside"),
            H.GoldItem("d", "q", "d", "SELECT 1", "unhandled", "outside"),
        ]

    def test_rate(self):
        stats = H.coverage_stats(self._items())
        assert stats["total"] == 4
        assert stats["inside"] == 2
        assert stats["outside"] == 2
        assert stats["coverage_rate"] == 0.5

    def test_by_category(self):
        stats = H.coverage_stats(self._items())
        assert stats["by_category"]["server_config"]["inside"] == 1
        assert stats["by_category"]["complex"]["outside"] == 1

    def test_empty(self):
        stats = H.coverage_stats([])
        assert stats["coverage_rate"] == 0.0


# ──────────────────────────────────────────────
# SMQ 정확도 훅
# ──────────────────────────────────────────────

class TestSmqMatch:
    def test_match(self):
        gold = {"pattern": "A", "dimensions": ["Hostname", "OSType"], "time_grain": None}
        pred = {"pattern": "A", "dimensions": ["OSType", "Hostname"], "time_grain": None}  # 순서 무관
        assert H.smq_match(gold, pred) is True

    def test_pattern_mismatch(self):
        assert H.smq_match({"pattern": "A"}, {"pattern": "B"}) is False

    def test_dimension_mismatch(self):
        gold = {"pattern": "A", "dimensions": ["Hostname"]}
        pred = {"pattern": "A", "dimensions": ["Vendor"]}
        assert H.smq_match(gold, pred) is False

    def test_missing_side(self):
        assert H.smq_match({"pattern": "A"}, None) is False
        assert H.smq_match(None, {"pattern": "A"}) is False

    def test_active_only_flag(self):
        gold = {"pattern": "C", "active_only": True}
        pred = {"pattern": "C", "active_only": False}
        assert H.smq_match(gold, pred) is False


# ──────────────────────────────────────────────
# run_batch / aggregate / A/B (mock)
# ──────────────────────────────────────────────

class TestRunBatch:
    def _items(self):
        return [
            H.GoldItem("in1", "q", "d", "SELECT a FROM t1", "server_config", "inside"),
            H.GoldItem("in2", "q", "d", "SELECT b FROM t2", "performance", "inside"),
            H.GoldItem("out1", "q", "d", "SELECT c FROM t3", "complex", "outside"),
        ]

    def test_baseline_inside_pass_outside_fail(self):
        items = self._items()
        pred = H.MockPredictor(pass_outside_when={"semantic_compose"})
        rep = H.run_batch(items, pred, H.MockExecutor(), flags={})
        agg = H.aggregate(rep)
        assert agg["ex_scored"] == 3
        assert agg["ex_pass"] == 2  # inside 2건만 통과
        assert agg["ex_rate"] == pytest.approx(2 / 3)

    def test_variant_flag_improves_outside(self):
        items = self._items()
        pred = H.MockPredictor(pass_outside_when={"semantic_compose"})
        rep = H.run_batch(items, pred, H.MockExecutor(), flags={"semantic_compose": True})
        agg = H.aggregate(rep)
        assert agg["ex_pass"] == 3
        assert agg["ex_rate"] == 1.0

    def test_skipped_when_prediction_unavailable(self):
        items = self._items()
        rep = H.run_batch(items, H.UnavailablePredictor("no db"), H.MockExecutor(), flags={})
        agg = H.aggregate(rep)
        assert agg["ex_scored"] == 0
        assert agg["ex_skipped"] == 3
        assert agg["ex_rate"] is None

    def test_executor_failure_skips_scoring(self):
        items = [H.GoldItem("x", "q", "d", "SELECT fail_me FROM t", "server_config", "inside")]
        pred = H.MockPredictor()
        # 실행기가 'fail_me' 포함 SQL에 None 반환 → EX 스킵
        rep = H.run_batch(items, pred, H.MockExecutor(fail_sql_substrings=["fail_me"]), flags={})
        agg = H.aggregate(rep)
        assert agg["ex_scored"] == 0
        assert agg["ex_skipped"] == 1

    def test_cost_metrics_scale_with_candidates(self):
        items = self._items()
        pred = H.MockPredictor()
        single = H.aggregate(H.run_batch(items, pred, H.MockExecutor(), flags={}))
        multi = H.aggregate(H.run_batch(items, pred, H.MockExecutor(),
                                        flags={"candidate_count": 3, "multi_candidate": True}))
        assert multi["total_llm_calls"] > single["total_llm_calls"]

    def test_ab_report_delta(self):
        items = self._items()
        pred = H.MockPredictor(pass_outside_when={"semantic_compose"})
        base = H.run_batch(items, pred, H.MockExecutor(), label="base", flags={})
        var = H.run_batch(items, pred, H.MockExecutor(), label="var", flags={"semantic_compose": True})
        ab = H.build_ab_report(base, var, "semantic_compose")
        assert ab["ex_rate_delta"] > 0
        assert ab["variant"]["ex_rate"] == 1.0

    def test_smq_scored_when_gold_and_pred_present(self):
        items = [H.GoldItem("s1", "q", "d", "SELECT 1", "server_config", "inside",
                            gold_smq={"pattern": "A", "dimensions": ["Hostname"]})]
        pred = H.MockPredictor()  # emit_smq=True → gold_smq 그대로 반환
        rep = H.run_batch(items, pred, H.MockExecutor(), flags={})
        agg = H.aggregate(rep)
        assert agg["smq_scored"] == 1
        assert agg["smq_pass"] == 1
        assert agg["smq_rate"] == 1.0


# ──────────────────────────────────────────────
# 플래그 적용 (os.environ)
# ──────────────────────────────────────────────

class TestSetFlags:
    def test_sets_env_vars(self):
        saved = {k: os.environ.get(k) for k in ("SYNONYM_FUZZY_MATCH", "TEXT2SQL_CANDIDATE_COUNT")}
        try:
            H.set_pipeline_flags({"synonym_fuzzy": True, "candidate_count": 5})
            assert os.environ["SYNONYM_FUZZY_MATCH"] == "true"
            assert os.environ["TEXT2SQL_CANDIDATE_COUNT"] == "5"
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


# ──────────────────────────────────────────────
# 파이프라인 어댑터 (네트워크 미접속 — 순수 부분만)
# ──────────────────────────────────────────────

class TestPipelineAdapter:
    def test_invalid_path_raises(self):
        with pytest.raises(ValueError):
            H.PipelinePredictor("bogus")

    def test_extract_sql_generated(self):
        assert H.PipelinePredictor._extract_sql({"generated_sql": "SELECT 1"}) == "SELECT 1"

    def test_extract_sql_from_attempts(self):
        out = {"query_attempts": [{"sql": "SELECT 1"}, {"sql": "SELECT 2"}]}
        assert H.PipelinePredictor._extract_sql(out) == "SELECT 2"

    def test_extract_sql_none(self):
        assert H.PipelinePredictor._extract_sql({}) is None

    def test_unavailable_predictor_skips(self):
        item = H.GoldItem("x", "q", "d", "SELECT 1", "server_config", "inside")
        res = H.UnavailablePredictor("closed net").predict(item, {})
        assert res.skipped is True
        assert res.sql is None


# ──────────────────────────────────────────────
# CLI 스모크 (dry-run / mock) — 실제 DB·LLM 불필요
# ──────────────────────────────────────────────

class TestCli:
    def test_dry_run_json(self, capsys):
        rc = H.main(["--dry-run", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["stats"]["total"] >= 20
        assert out["errors"] == []

    def test_mock_json(self, capsys):
        rc = H.main(["--mock", "--json"])
        assert rc == 0
        agg = json.loads(capsys.readouterr().out)
        assert agg["total"] >= 20
        assert agg["ex_scored"] == agg["total"]  # mock 실행기는 모두 채점

    def test_mock_ab_json(self, capsys):
        rc = H.main(["--mock", "--ab", "semantic_compose", "--json"])
        assert rc == 0
        ab = json.loads(capsys.readouterr().out)
        assert ab["axis"] == "semantic_compose"
        assert ab["variant"]["ex_rate"] >= ab["baseline"]["ex_rate"]

    def test_mock_db_filter(self, capsys):
        rc = H.main(["--mock", "--db", "b0", "--json"])
        assert rc == 0
        agg = json.loads(capsys.readouterr().out)
        assert agg["total"] == 5  # b0.yaml 항목 수
