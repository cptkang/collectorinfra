"""카탈로그 탐색·커버리지 판정 도구 검증 (Plan 67 Phase S1 §4.2)."""

from __future__ import annotations

from src.tools.catalog import (
    KIND_ALARM_DIMENSION,
    KIND_ALARM_ENTITY,
    KIND_DIMENSION,
    KIND_MEASURE,
    catalog_entries,
    check_smq_coverage,
    search_catalog,
)


class TestCatalogEntries:
    def test_flattens_all_patterns(self, semantic_model):
        kinds = {e["kind"] for e in catalog_entries(semantic_model)}
        assert kinds == {
            KIND_DIMENSION, KIND_MEASURE, KIND_ALARM_ENTITY, KIND_ALARM_DIMENSION
        }

    def test_lob_dimension_marked_unsupported(self, semantic_model):
        lob = [e for e in catalog_entries(semantic_model) if e["name"] == "OSParameter"]
        assert lob and lob[0]["unsupported"] is True

    def test_empty_model_returns_empty(self):
        assert catalog_entries({}) == []


class TestSearchCatalog:
    def test_alias_exact_match_scores_top(self, semantic_model):
        hits = search_catalog("서버명", semantic_model)
        assert hits[0]["name"] == "hostname"
        assert hits[0]["score"] == 1.0

    def test_measure_found_by_alias(self, semantic_model):
        hits = search_catalog("CPU 사용률", semantic_model)
        assert hits[0]["name"] == "Utilization"
        assert hits[0]["kind"] == KIND_MEASURE

    def test_fuzzy_match_within_threshold(self, semantic_model):
        """오타·띄어쓰기 흔들림은 유연 매칭이 흡수한다."""
        hits = search_catalog("호스트 명", semantic_model)
        assert any(h["name"] == "hostname" for h in hits)

    def test_unrelated_term_returns_nothing(self, semantic_model):
        assert search_catalog("전화번호", semantic_model) == []

    def test_empty_term_returns_empty(self, semantic_model):
        assert search_catalog("   ", semantic_model) == []

    def test_limit_applied(self, semantic_model):
        assert len(search_catalog("OS", semantic_model, limit=1, min_score=0.1)) == 1


class TestCheckSmqCoverage:
    def test_covered_selection(self, semantic_model):
        result = check_smq_coverage(
            {"pattern": "A", "dimensions": ["hostname"]}, semantic_model
        )
        assert result == {"covered": True, "reason": ""}

    def test_uncovered_reports_reason(self, semantic_model):
        result = check_smq_coverage(
            {"pattern": "A", "dimensions": ["존재하지않는차원"]}, semantic_model
        )
        assert result["covered"] is False
        assert "존재하지않는차원" in result["reason"]

    def test_malformed_smq_reports_format_error(self, semantic_model):
        result = check_smq_coverage({"pattern": "Z"}, semantic_model)
        assert result["covered"] is False
        assert result["reason"]

    def test_value_index_rejects_unverified_literal(self, semantic_model):
        result = check_smq_coverage(
            {
                "pattern": "A",
                "dimensions": ["hostname"],
                "filters": [{"field": "resource_type", "op": "eq", "value": "없는값"}],
            },
            semantic_model,
            value_index={"resource_type": ["host.cpu"]},
        )
        assert result["covered"] is False
