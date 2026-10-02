"""synonym_semantic(E5-4 임베딩 의미 검색, D-084) 모듈 검증.

실제 sentence-transformers 모델 없이 결정적 FakeEmbedder를 주입해 검증한다.
미가용(모델 경로 미설정) 시 빈 결과 + 경고 1회 가시화를 고정한다.
"""

import logging
from types import SimpleNamespace

import numpy as np
import pytest

from src.schema_cache import synonym_semantic as ss

# 테스트용 결정적 임베딩(8차원). 의미 군집을 수동 정의 — 사용률/이용률/가동률은 근접,
# hostname/서버명은 별개 군집, 회계전표는 직교. 미정의 단어는 5번 축 단위 벡터.
_VECS = {
    "사용률": [1.0, 0.05, 0, 0, 0, 0, 0, 0],
    "이용률": [0.97, 0.15, 0, 0, 0, 0, 0, 0],
    "가동률": [0.95, 0.2, 0, 0, 0, 0, 0, 0],
    "hostname": [0, 0, 1.0, 0.1, 0, 0, 0, 0],
    "서버명": [0, 0, 0.98, 0.15, 0, 0, 0, 0],
    "회계전표": [0, 0, 0, 0, 0, 0, 0, 1.0],
}
_DEFAULT_VEC = [0, 0, 0, 0, 0, 1.0, 0, 0]


class FakeEmbedder:
    """encode 호출 횟수를 세는 결정적 임베더."""

    def __init__(self):
        self.encode_calls = 0

    def encode(self, texts, batch_size=64, show_progress_bar=False):
        self.encode_calls += 1
        return np.asarray(
            [_VECS.get(t, _DEFAULT_VEC) for t in texts], dtype=np.float32
        )


@pytest.fixture(autouse=True)
def _reset_module_state():
    ss._reset_state_for_tests()
    yield
    ss._reset_state_for_tests()


@pytest.fixture()
def fake(monkeypatch):
    emb = FakeEmbedder()
    monkeypatch.setattr(ss, "_load_embedder", lambda: emb)
    return emb


class TestMatchCandidates:
    def test_paraphrase_ranks_similar_key_first(self, fake):
        ranked = ss.semantic_match_candidates(
            "이용률",
            {"cmm_metric.usage": ["사용률"], "acct.x": ["회계전표"]},
        )
        assert ranked[0][0] == "cmm_metric.usage"
        assert ranked[0][1] > 0.9
        scores = dict(ranked)
        assert scores["acct.x"] < 0.1

    def test_short_term_guard_returns_empty(self, fake):
        # 1글자 term은 과잉 매칭 위험 → 임베딩 단계도 무매칭(계단 가드와 동일)
        assert ss.semantic_match_candidates("률", {"t.c": ["사용률"]}) == []

    def test_short_words_filtered_out(self, fake):
        assert ss.semantic_match_candidates("이용률", {"t.c": ["a", ""]}) == []

    def test_empty_candidate_map(self, fake):
        assert ss.semantic_match_candidates("이용률", {}) == []


class TestTablesMatchingQuery:
    def test_paraphrase_hit_above_threshold(self, fake):
        result = ss.semantic_tables_matching_query(
            {"cmm_metric.usage": ["사용률"]}, ["이용률", "조회"], min_score=0.9
        )
        assert result == {"cmm_metric"}

    def test_below_threshold_excluded(self, fake):
        result = ss.semantic_tables_matching_query(
            {"cmm_metric.usage": ["사용률"]}, ["이용률"], min_score=0.999
        )
        assert result == set()

    def test_key_without_dot_skipped(self, fake):
        result = ss.semantic_tables_matching_query(
            {"nodot": ["사용률"]}, ["이용률"], min_score=0.5
        )
        assert result == set()

    def test_empty_tokens(self, fake):
        assert ss.semantic_tables_matching_query(
            {"cmm_metric.usage": ["사용률"]}, [], min_score=0.5
        ) == set()

    def test_hit_logged_with_cosine_detail(self, fake, caplog):
        # 계측 로그 계약: 임베딩 매칭은 단어→컬럼·최고 토큰·코사인 점수를 남긴다
        with caplog.at_level(logging.INFO, logger=ss.__name__):
            ss.semantic_tables_matching_query(
                {"cmm_metric.usage": ["사용률"]}, ["이용률"], min_score=0.9
            )
        msgs = [r.getMessage() for r in caplog.records if "[동의어]" in r.getMessage()]
        assert any(
            "임베딩 의미 매칭" in m and "코사인=" in m and "cmm_metric.usage" in m
            for m in msgs
        )


class TestUnavailable:
    def test_no_model_path_returns_empty_and_warns_once(self, monkeypatch, caplog):
        import src.config as config_mod

        stub = SimpleNamespace(
            synonym=SimpleNamespace(semantic_backend="local", semantic_model_path="")
        )
        monkeypatch.setattr(config_mod, "load_config", lambda: stub)
        with caplog.at_level(logging.WARNING, logger=ss.__name__):
            assert ss.semantic_match_candidates("이용률", {"t.c": ["사용률"]}) == []
            assert ss.semantic_tables_matching_query(
                {"t.c": ["사용률"]}, ["이용률"], min_score=0.5
            ) == set()
        warns = [
            r for r in caplog.records
            if "SYNONYM_SEMANTIC_MODEL_PATH" in r.getMessage()
        ]
        # 침묵적 강등 금지: 사유를 경고로 가시화하되, 반복 호출에도 1회만 남긴다
        assert len(warns) == 1


class TestEmbedCache:
    def test_cached_texts_not_reencoded(self, fake):
        args = ("이용률", {"cmm_metric.usage": ["사용률", "가동률"]})
        first = ss.semantic_match_candidates(*args)
        second = ss.semantic_match_candidates(*args)
        assert first == second
        # 두 번째 호출은 전부 캐시 적중 → encode 재호출 없음(배치 1회 고정)
        assert fake.encode_calls == 1


class TestVLLMBackend:
    def test_vllm_embedder_calls_openai_compatible_endpoint(self):
        import json

        import httpx

        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            # index를 뒤섞어 반환 — 순서 복원(index 정렬) 검증
            data = [
                {"index": 1, "embedding": [0.0, 1.0]},
                {"index": 0, "embedding": [1.0, 0.0]},
            ]
            return httpx.Response(200, json={"data": data})

        emb = ss._VLLMEmbedder(
            "http://vllm-host:8000/v1",
            "test-embed",
            transport=httpx.MockTransport(handler),
        )
        vecs = emb.encode(["단어1", "단어2"])
        assert seen["url"] == "http://vllm-host:8000/v1/embeddings"
        assert seen["body"] == {"model": "test-embed", "input": ["단어1", "단어2"]}
        assert vecs == [[1.0, 0.0], [0.0, 1.0]]

    def test_vllm_missing_config_unavailable_warns_once(self, monkeypatch, caplog):
        import src.config as config_mod

        stub = SimpleNamespace(
            synonym=SimpleNamespace(
                semantic_backend="vllm",
                semantic_vllm_base_url="",
                semantic_vllm_model="",
            )
        )
        monkeypatch.setattr(config_mod, "load_config", lambda: stub)
        with caplog.at_level(logging.WARNING, logger=ss.__name__):
            assert ss.semantic_match_candidates("이용률", {"t.c": ["사용률"]}) == []
            assert ss.semantic_match_candidates("이용률", {"t.c": ["사용률"]}) == []
        warns = [
            r for r in caplog.records
            if "SYNONYM_SEMANTIC_VLLM_BASE_URL" in r.getMessage()
        ]
        assert len(warns) == 1

    def test_vllm_backend_selected_from_config(self, monkeypatch):
        import json

        import httpx

        def handler(request):
            body = json.loads(request.content)
            data = [
                {"index": i, "embedding": _VECS.get(t, _DEFAULT_VEC)}
                for i, t in enumerate(body["input"])
            ]
            return httpx.Response(200, json={"data": data})

        import src.config as config_mod

        stub = SimpleNamespace(
            synonym=SimpleNamespace(
                semantic_backend="vllm",
                semantic_vllm_base_url="http://vllm-host:8000/v1",
                semantic_vllm_model="test-embed",
                semantic_vllm_verify_ssl=True,
            )
        )
        monkeypatch.setattr(config_mod, "load_config", lambda: stub)
        # 실제 네트워크 차단: _VLLMEmbedder 생성에 MockTransport 주입
        orig_cls = ss._VLLMEmbedder
        monkeypatch.setattr(
            ss,
            "_VLLMEmbedder",
            lambda base_url, model, **kw: orig_cls(
                base_url, model,
                verify_ssl=kw.get("verify_ssl", True),
                transport=httpx.MockTransport(handler),
            ),
        )
        ranked = ss.semantic_match_candidates("이용률", {"cmm_metric.usage": ["사용률"]})
        assert ranked and ranked[0][0] == "cmm_metric.usage"
        assert ranked[0][1] > 0.9


class TestRuntimeFailure:
    def test_encode_failure_degrades_to_no_match_with_warning(
        self, monkeypatch, caplog
    ):
        class FailingEmbedder:
            def encode(self, texts, batch_size=64, show_progress_bar=False):
                raise RuntimeError("vLLM down")

        monkeypatch.setattr(ss, "_load_embedder", lambda: FailingEmbedder())
        with caplog.at_level(logging.WARNING, logger=ss.__name__):
            assert ss.semantic_match_candidates("이용률", {"t.c": ["사용률"]}) == []
        assert any("임베딩 계산 실패" in r.getMessage() for r in caplog.records)
