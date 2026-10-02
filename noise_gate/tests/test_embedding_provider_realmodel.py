"""실모델 옵트인 검증 — 확정 모델 multilingual-e5-small (Plan 60 B-7 · D-114).

CI/폐쇄망 안전: `pytest.importorskip("sentence_transformers")` + 환경변수 `E5_MODEL_PATH`
(로컬 e5-small 스냅샷 디렉토리) 미설정 시 자동 스킵. 실행:
    E5_MODEL_PATH=/path/to/multilingual-e5-small python -m pytest \
        tests/test_alarm/test_embedding_provider_realmodel.py -v

검증 내용(2026-07-23 팀리드 실측 근거):
- provider가 로컬 경로에서 e5-small을 로드(오프라인·다운로드 없음), 차원 384.
- 한/영 혼용 알람 근접중복 > 확정 임계 0.87 > 이질(분리 실증).
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("sentence_transformers")

_MODEL_PATH = os.getenv("E5_MODEL_PATH")

pytestmark = pytest.mark.skipif(
    not _MODEL_PATH or not os.path.isdir(_MODEL_PATH),
    reason="E5_MODEL_PATH(로컬 e5-small 디렉토리) 미설정 — 실모델 검증 스킵(옵트인)",
)

# 확정 임계(D-114 실측): 이질 max 0.852 < 0.87 < 근접 min 0.893.
_THRESHOLD = 0.87
_NEAR = [
    ("CPU 사용률 임계 초과", "CPU Utilization 높음"),
    ("디스크 용량 부족", "디스크 여유공간 부족 경고"),
    ("메모리 부족 경고", "Memory 사용률 임계 초과"),
]
_DISSIMILAR = [
    ("CPU 사용률 임계 초과", "Memory OOM 강제종료"),
    ("디스크 용량 부족", "네트워크 트래픽 폭주"),
]


@pytest.fixture(scope="module")
def provider():
    from noise_gate.infrastructure.embedding_provider import AlarmEmbeddingProvider

    p = AlarmEmbeddingProvider(model_path=_MODEL_PATH)
    assert p.is_available(), "로컬 e5-small 로드 실패 — 경로/파일 확인"
    return p


def test_dimension_384(provider):
    # e5-small은 384차원(config.json hidden_size=384).
    dim = provider._embedder.get_sentence_embedding_dimension()
    assert dim == 384


def test_near_duplicate_above_threshold(provider):
    for a, b in _NEAR:
        s = provider.similarity(a, b)
        assert s is not None and s > _THRESHOLD, f"근접중복 {a}↔{b} 유사도 {s} ≤ {_THRESHOLD}"


def test_dissimilar_below_threshold(provider):
    for a, b in _DISSIMILAR:
        s = provider.similarity(a, b)
        assert s is not None and s < _THRESHOLD, f"이질 {a}↔{b} 유사도 {s} ≥ {_THRESHOLD}"


def test_complete_separation(provider):
    # 근접 최소 > 이질 최대 (완전 분리 실증).
    near = [provider.similarity(a, b) for a, b in _NEAR]
    diss = [provider.similarity(a, b) for a, b in _DISSIMILAR]
    assert min(near) > max(diss), f"분리 실패: 근접min={min(near):.3f} 이질max={max(diss):.3f}"
