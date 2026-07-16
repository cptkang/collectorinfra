"""_synonym_tables_matching_query 임베딩 의미 검색(E5-4, D-084) 검증.

무회귀 최우선: semantic 플래그 OFF(기본)일 때 임베딩 모듈에 진입하지 않음을 고정하고,
ON일 때만 정확·퍼지 계단이 놓친 의역 유사어("사용률"↔"이용률")를 보완함을 검증한다.
동의어 사용 계측 로그("[동의어]" 태그)의 계약도 함께 고정한다.
"""

import logging

import numpy as np
import pytest

from src.nodes.schema_analyzer import _synonym_tables_matching_query
from src.schema_cache import synonym_semantic as ss

_VECS = {
    "사용률": [1.0, 0.05, 0, 0, 0, 0, 0, 0],
    "이용률": [0.97, 0.15, 0, 0, 0, 0, 0, 0],
    "회계전표": [0, 0, 0, 0, 0, 0, 0, 1.0],
}
_DEFAULT_VEC = [0, 0, 0, 0, 0, 1.0, 0, 0]


class FakeEmbedder:
    def encode(self, texts, batch_size=64, show_progress_bar=False):
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
    monkeypatch.setattr(ss, "_load_embedder", lambda: FakeEmbedder())


class TestSemanticOffByteIdentical:
    """플래그 OFF(기본)는 임베딩 모듈을 호출조차 하지 않아야 한다(회귀 0)."""

    def test_off_never_enters_semantic_module(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("semantic OFF인데 임베딩 모듈이 호출됨")

        monkeypatch.setattr(ss, "semantic_tables_matching_query", _boom)
        syns = {"cmm_metric.usage": ["사용률"]}
        # 의역 질의 — 정확 부분어 실패 → OFF에서는 그대로 무매칭
        assert _synonym_tables_matching_query(syns, "서버 이용률 조회") == set()
        assert (
            _synonym_tables_matching_query(syns, "서버 이용률 조회", semantic=False)
            == set()
        )

    def test_off_exact_substring_still_matches(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise AssertionError("semantic OFF인데 임베딩 모듈이 호출됨")

        monkeypatch.setattr(ss, "semantic_tables_matching_query", _boom)
        syns = {"cmm_metric.usage": ["사용률"]}
        assert _synonym_tables_matching_query(syns, "사용률 조회") == {"cmm_metric"}


class TestSemanticOn:
    def test_paraphrase_supplemented(self, fake):
        # 정확 부분어·퍼지가 못 잡는 의역("사용률" 등록 vs "이용률" 질의)을 임베딩이 보완
        syns = {"cmm_metric.usage": ["사용률"]}
        result = _synonym_tables_matching_query(
            syns, "서버 이용률 조회", semantic=True, semantic_min=0.9
        )
        assert result == {"cmm_metric"}

    def test_unrelated_still_excluded(self, fake):
        syns = {"accounting.x": ["회계전표"]}
        result = _synonym_tables_matching_query(
            syns, "cpu 이용률 조회", semantic=True, semantic_min=0.9
        )
        assert result == set()

    def test_cap_respected(self, fake):
        syns = {f"t{i}.c": ["사용률"] for i in range(10)}
        result = _synonym_tables_matching_query(
            syns, "이용률 조회", cap=3, semantic=True, semantic_min=0.9
        )
        assert len(result) == 3

    def test_exact_hit_not_duplicated_with_semantic(self, fake):
        # 정확 계단에서 이미 잡힌 테이블은 그대로 1건 유지(중복·상한 소모 없음)
        syns = {"cmm_metric.usage": ["사용률"]}
        result = _synonym_tables_matching_query(
            syns, "사용률 조회", semantic=True, semantic_min=0.9
        )
        assert result == {"cmm_metric"}


class TestSynonymUsageLogging:
    """동의어 사용 계측 로그 계약: 매칭 단계·근거가 "[동의어]" 태그로 콘솔에 남는다.

    테스트 실행 시 ``pytest --log-cli-level=INFO``로 육안 확인할 수 있다.
    """

    def test_exact_hit_logged(self, caplog):
        syns = {"cmm_metric.usage": ["사용률"]}
        with caplog.at_level(logging.INFO, logger="src.nodes.schema_analyzer"):
            _synonym_tables_matching_query(syns, "사용률 조회")
        msgs = [r.getMessage() for r in caplog.records if "[동의어]" in r.getMessage()]
        assert any("정확" in m and "cmm_metric.usage" in m for m in msgs)

    def test_fuzzy_hit_logged_with_score(self, caplog):
        syns = {"cmm_resource.mem": ["메모리 사용률"]}
        with caplog.at_level(logging.INFO, logger="src.nodes.schema_analyzer"):
            _synonym_tables_matching_query(
                syns, "서버의 메모리사용률 조회", fuzzy=True, min_score=0.85
            )
        msgs = [r.getMessage() for r in caplog.records if "[동의어]" in r.getMessage()]
        assert any("퍼지" in m and "신뢰도=" in m for m in msgs)

    def test_semantic_hit_logged(self, fake, caplog):
        syns = {"cmm_metric.usage": ["사용률"]}
        with caplog.at_level(logging.INFO):
            _synonym_tables_matching_query(
                syns, "서버 이용률 조회", semantic=True, semantic_min=0.9
            )
        msgs = [r.getMessage() for r in caplog.records if "[동의어]" in r.getMessage()]
        # 보완 요약(schema_analyzer)과 점수 상세(synonym_semantic) 양쪽 모두 남는다
        assert any("(임베딩)" in m for m in msgs)
        assert any("코사인=" in m for m in msgs)

    def test_no_match_no_synonym_log(self, caplog):
        syns = {"accounting.x": ["회계전표"]}
        with caplog.at_level(logging.INFO, logger="src.nodes.schema_analyzer"):
            _synonym_tables_matching_query(syns, "cpu 조회")
        assert not [r for r in caplog.records if "[동의어]" in r.getMessage()]
