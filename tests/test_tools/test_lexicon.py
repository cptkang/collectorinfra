"""유사어 조회·값 인덱스 검색 도구 검증 (Plan 67 Phase S1 §4.2)."""

from __future__ import annotations

from src.tools.lexicon import (
    STAGE_EMBEDDING,
    STAGE_EXACT,
    STAGE_FUZZY,
    lookup_synonym,
    search_value_index,
)


class TestLookupSynonym:
    def test_exact_stage(self, synonyms):
        hits = lookup_synonym("서버명", synonyms)
        assert hits[0]["column"] == "host.hostname"
        assert hits[0]["stage"] == STAGE_EXACT

    def test_fuzzy_stage_when_no_exact(self, synonyms):
        hits = lookup_synonym("호스트넴", synonyms, min_score=0.6)
        assert hits and hits[0]["stage"] == STAGE_FUZZY

    def test_embedding_stage_only_when_injected(self, synonyms):
        """임베딩 단계는 함수가 주입됐을 때만 동작한다(기본 OFF 존중)."""
        assert lookup_synonym("장비 이름", synonyms, min_score=0.99) == []

        calls: list[str] = []

        def fake_semantic(term, candidate_map):
            calls.append(term)
            return [("host.hostname", 0.81)]

        hits = lookup_synonym(
            "장비 이름", synonyms, min_score=0.99, semantic_fn=fake_semantic
        )
        assert calls == ["장비 이름"]
        assert hits[0]["stage"] == STAGE_EMBEDDING

    def test_embedding_below_threshold_dropped(self, synonyms):
        hits = lookup_synonym(
            "장비 이름",
            synonyms,
            min_score=0.99,
            semantic_fn=lambda t, m: [("host.hostname", 0.30)],
            semantic_min_score=0.65,
        )
        assert hits == []

    def test_operator_meta_ranks_first(self):
        """운영자 등록 유사어가 LLM 등록보다 앞선다(유사어 거버넌스 우선순위 재사용)."""
        synonyms = {"host.a_col": ["장비"], "host.b_col": ["장비"]}
        hits = lookup_synonym(
            "장비", synonyms, meta={"host.b_col": {"source": "operator"}}
        )
        assert [h["column"] for h in hits] == ["host.b_col", "host.a_col"]
        assert hits[0]["source"] == "operator"

    def test_no_match_returns_empty(self, synonyms):
        assert lookup_synonym("전화번호", synonyms) == []

    def test_short_term_ignored(self, synonyms):
        assert lookup_synonym("서", synonyms) == []

    def test_limit_applied(self):
        synonyms = {f"host.c{i}": ["장비"] for i in range(10)}
        assert len(lookup_synonym("장비", synonyms, limit=3)) == 3


class TestSearchValueIndex:
    def test_matching_literals_returned(self):
        index = {"resource_type": ["host.cpu", "host.memory"]}
        assert search_value_index(["cpu"], index) == {"resource_type": ["host.cpu"]}

    def test_no_keywords_returns_empty(self):
        assert search_value_index([], {"k": ["v"]}) == {}

    def test_empty_index_returns_empty(self):
        assert search_value_index(["cpu"], {}) == {}

    def test_fuzzy_flag_forwarded(self):
        index = {"zone": ["김포존"]}
        assert search_value_index(["김포죤"], index) == {}
        assert search_value_index(["김포죤"], index, fuzzy=True, min_score=0.5) == {
            "zone": ["김포존"]
        }
