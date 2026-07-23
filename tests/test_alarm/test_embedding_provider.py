"""알람 로컬 임베딩 provider 단위 테스트 (Plan 60 B-7 · §15.4 D-035 · §10).

검증 축:
    ① fake 모델 주입 결정적 단위 — similarity·most_similar가 고정 벡터로 결정적 코사인 반환,
       LRU 캐시가 재인코딩을 회피(핵심 성능·정확성).
    ② graceful inert(핵심) — model_path="" / hub 이름(비디렉토리) / sentence-transformers 미설치
       시뮬레이션에서 앱 미충돌·경고 1회·None.
    ③ 다운로드 금지 실증 — 비디렉토리 경로에서 SentenceTransformer가 호출되지 않음(네트워크 접근 0).
    ④ 최상단 lazy — sentence-transformers 차단 상태에서도 모듈 import(reload) 성공.

실모델 테스트는 이번 범위 밖(dev venv 실모델로 별도 실측). 여기선 fake·inert 경로만 다룬다.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys

import pytest

from src.alarm.infrastructure import embedding_provider as ep_module
from src.alarm.infrastructure.embedding_provider import AlarmEmbeddingProvider

# 정규화(소문자·공백축약) 후 키에 매핑되는 고정 단위벡터. cos([1,0,0],[0.8,0.6,0])=0.8,
# cos([1,0,0],[0,0,1])=0.0 — 결정적으로 검증 가능한 값.
_VECTORS: dict[str, list[float]] = {
    "cpu high": [1.0, 0.0, 0.0],
    "cpu elevated": [0.8, 0.6, 0.0],
    "disk full": [0.0, 0.0, 1.0],
    "memory leak": [0.0, 1.0, 0.0],
}


class _FakeModel:
    """SentenceTransformer.encode 인터페이스 대역 — 텍스트별 고정 벡터 반환·호출 배치 기록."""

    def __init__(self) -> None:
        self.encode_calls: list[list[str]] = []

    def encode(
        self, texts: list[str], batch_size: int = 64, show_progress_bar: bool = False
    ) -> list[list[float]]:
        self.encode_calls.append(list(texts))
        return [list(_VECTORS.get(t, [0.0, 0.0, 0.0])) for t in texts]


def _provider_with_fake() -> tuple[AlarmEmbeddingProvider, _FakeModel]:
    """fake 모델을 주입한 provider — _load_embedder가 즉시 fake를 반환한다."""
    provider = AlarmEmbeddingProvider(model_path="/unused-when-fake-injected")
    fake = _FakeModel()
    provider._embedder = fake  # 주입: _load_embedder 첫 분기에서 그대로 반환
    return provider, fake


@pytest.fixture(autouse=True)
def _preserve_offline_env(monkeypatch):
    """provider가 로드 시 설정하는 offline 환경변수가 세션에 누수되지 않도록 스냅샷·복원.

    monkeypatch가 각 키의 원래 값(또는 부재)을 기록해 teardown에서 복원한다 — 프로덕션 코드가
    테스트 중 os.environ에 직접 설정해도 세션에 잔류하지 않는다.
    """
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if key in os.environ:
            monkeypatch.setenv(key, os.environ[key])
        else:
            monkeypatch.delenv(key, raising=False)
    yield


# ── ① fake 결정적 단위 ──────────────────────────────────────────────


class TestSimilarityDeterministic:
    def test_similarity_returns_fixed_cosine(self):
        provider, _ = _provider_with_fake()
        # 대소문자·공백은 정규화로 흡수 → "cpu high" vs "cpu elevated" cos=0.8
        assert provider.similarity("CPU  High", "cpu elevated") == pytest.approx(0.8)

    def test_orthogonal_texts_zero_similarity(self):
        provider, _ = _provider_with_fake()
        assert provider.similarity("cpu high", "disk full") == pytest.approx(0.0)

    def test_blank_input_returns_none(self):
        provider, fake = _provider_with_fake()
        assert provider.similarity("", "cpu high") is None
        assert provider.similarity("cpu high", "   ") is None
        assert fake.encode_calls == []  # 빈 입력은 인코딩 자체를 하지 않음


class TestMostSimilar:
    def test_returns_best_index_and_score(self):
        provider, _ = _provider_with_fake()
        result = provider.most_similar(
            "cpu high", ["disk full", "cpu elevated", "memory leak"]
        )
        assert result == (1, pytest.approx(0.8))

    def test_index_is_original_position_skipping_blanks(self):
        provider, _ = _provider_with_fake()
        # 인덱스 0은 빈 문자열(정규화 후 제거) — best는 원본 인덱스 2("cpu elevated")여야 한다.
        result = provider.most_similar("cpu high", ["", "disk full", "cpu elevated"])
        assert result is not None
        assert result[0] == 2

    def test_empty_candidates_returns_none(self):
        provider, fake = _provider_with_fake()
        assert provider.most_similar("cpu high", []) is None
        assert provider.most_similar("cpu high", ["", "  "]) is None
        assert fake.encode_calls == []


class TestCache:
    def test_repeated_similarity_reuses_cache(self):
        provider, fake = _provider_with_fake()
        provider.similarity("cpu high", "cpu elevated")
        provider.similarity("cpu high", "cpu elevated")
        # 첫 호출에서 두 텍스트를 1배치 인코딩, 두 번째는 전부 캐시 히트 → 추가 인코딩 없음.
        assert len(fake.encode_calls) == 1
        assert sorted(fake.encode_calls[0]) == ["cpu elevated", "cpu high"]

    def test_only_missing_text_is_encoded(self):
        provider, fake = _provider_with_fake()
        provider.similarity("cpu high", "cpu elevated")
        provider.similarity("cpu high", "disk full")  # "cpu high"는 캐시, "disk full"만 신규
        assert len(fake.encode_calls) == 2
        assert fake.encode_calls[1] == ["disk full"]

    def test_cache_bounded_by_cache_max(self):
        provider = AlarmEmbeddingProvider(model_path="/unused", cache_max=2)
        provider._embedder = _FakeModel()
        provider.similarity("cpu high", "cpu elevated")  # 캐시 2건(상한)
        provider.similarity("disk full", "memory leak")  # 신규 2건 → LRU 축출
        assert len(provider._cache) <= 2


# ── ② graceful inert ────────────────────────────────────────────────


class TestInertEmptyPath:
    def test_empty_model_path_inert_and_warns_once(self, caplog):
        provider = AlarmEmbeddingProvider(model_path="")
        with caplog.at_level(logging.WARNING):
            assert provider.similarity("cpu high", "cpu elevated") is None
            assert provider.most_similar("cpu high", ["disk full"]) is None
        assert provider.is_available() is False
        # 경고는 1회만(실패 확정 플래그로 재시도·재경고 폭주 방지).
        warns = [r for r in caplog.records if "로컬 디렉토리가 아님" in r.message]
        assert len(warns) == 1


class TestInertHubName:
    def test_hub_name_path_inert_no_download(self, monkeypatch, caplog):
        # hub 이름(비디렉토리)은 os.path.isdir False → 로드 시도 없이 inert. SentenceTransformer가
        # 호출되지 않음을 fake 모듈로 실증(네트워크 접근 0 — 다운로드 금지 실증).
        import types

        fake_st = types.ModuleType("sentence_transformers")
        calls: list[str] = []

        class _NeverCalled:
            def __init__(self, *a, **k):
                calls.append("instantiated")

        fake_st.SentenceTransformer = _NeverCalled  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st)

        provider = AlarmEmbeddingProvider(model_path="sentence-transformers/all-MiniLM-L6-v2")
        with caplog.at_level(logging.WARNING):
            assert provider.similarity("cpu high", "cpu elevated") is None
        assert calls == []  # SentenceTransformer 미호출 = 다운로드 시도 0
        assert any("로컬 디렉토리가 아님" in r.message for r in caplog.records)


class TestInertMissingDependency:
    def test_sentence_transformers_missing_inert_and_warns(
        self, tmp_path, monkeypatch, caplog
    ):
        # 실제 디렉토리(isdir 통과)이나 sentence-transformers 미설치를 시뮬레이션 → import 실패 →
        # 경고 후 inert(앱 미충돌). offline env가 로드 직전 설정됐는지도 확인(이중 다운로드 차단).
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        provider = AlarmEmbeddingProvider(model_path=str(tmp_path))
        with caplog.at_level(logging.WARNING):
            assert provider.similarity("cpu high", "cpu elevated") is None
        assert provider.is_available() is False
        assert any("의존성 미반입" in r.message for r in caplog.records)
        # belt-and-suspenders: isdir 통과 후 로드 직전 offline 환경변수 설정.
        import os

        assert os.environ.get("HF_HUB_OFFLINE") == "1"
        assert os.environ.get("TRANSFORMERS_OFFLINE") == "1"


# ── ④ 최상단 lazy(미설치에서도 import 성공) ─────────────────────────────


class TestLazyImport:
    def test_module_imports_without_sentence_transformers(self, monkeypatch):
        # sentence-transformers를 차단한 채 모듈을 reload해도 성공해야 한다(최상단 lazy import 없음).
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        reloaded = importlib.reload(ep_module)
        assert hasattr(reloaded, "AlarmEmbeddingProvider")
        # inert 동작도 유지(경로 미설정 → None).
        provider = reloaded.AlarmEmbeddingProvider(model_path="")
        assert provider.similarity("a", "b") is None


class TestResetForTests:
    def test_reset_clears_state(self):
        provider, fake = _provider_with_fake()
        provider.similarity("cpu high", "cpu elevated")
        assert len(provider._cache) > 0
        provider._reset_state_for_tests()
        assert len(provider._cache) == 0
        assert provider._embedder is None
        assert provider._embedder_failed is False
