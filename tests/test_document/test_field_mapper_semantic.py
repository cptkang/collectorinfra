"""_apply_synonym_mapping 임베딩 의미 매칭 폴백(E5-4 Pass 4, D-084) 검증.

무회귀 최우선: semantic 플래그 OFF(기본)일 때 임베딩 모듈에 진입하지 않음을 고정하고,
ON일 때만 임계 이상 의미 매칭을 확정 채택함을 검증한다. 임계 미달은 확정하지 않아
다운스트림(LLM/pending) 경로로 위임되어야 한다.
동의어 사용 계측 로그("[동의어]" 태그)의 계약도 함께 고정한다.
"""

import logging

import pytest

from src.document.field_mapper import MappingResult, _apply_synonym_mapping
from src.schema_cache import synonym_semantic as ss


def _syns() -> dict[str, dict[str, list[str]]]:
    return {
        "db1": {
            "cmm_resource.mem_usage": ["메모리 사용률"],
            "cmm_resource.cpu_usage": ["CPU 사용률"],
        }
    }


def _boom(*args, **kwargs):
    raise AssertionError("semantic OFF인데 임베딩 모듈이 호출됨")


class TestSemanticOffByteIdentical:
    def test_off_default_never_enters_semantic(self, monkeypatch):
        monkeypatch.setattr(ss, "semantic_match_candidates", _boom)
        result = MappingResult()
        remaining = {"메모리점유율"}
        _apply_synonym_mapping(remaining, _syns(), ["db1"], result)
        assert "메모리점유율" in remaining
        assert result.db_column_mapping == {}

    def test_off_explicit_false_same(self, monkeypatch):
        monkeypatch.setattr(ss, "semantic_match_candidates", _boom)
        result = MappingResult()
        remaining = {"메모리점유율"}
        _apply_synonym_mapping(remaining, _syns(), ["db1"], result, semantic=False)
        assert "메모리점유율" in remaining

    def test_exact_match_wins_before_semantic(self, monkeypatch):
        # 정확 매칭(Pass 1/2)이 성공하면 remaining이 비어 Pass 4에 진입하지 않는다
        monkeypatch.setattr(ss, "semantic_match_candidates", _boom)
        result = MappingResult()
        remaining = {"메모리 사용률"}
        _apply_synonym_mapping(remaining, _syns(), ["db1"], result, semantic=True)
        assert result.db_column_mapping["db1"]["메모리 사용률"] == "cmm_resource.mem_usage"
        assert not remaining


class TestSemanticOn:
    def test_adopt_above_threshold(self, monkeypatch):
        monkeypatch.setattr(
            ss,
            "semantic_match_candidates",
            lambda term, cmap: [("cmm_resource.mem_usage", 0.9)],
        )
        result = MappingResult()
        remaining = {"메모리점유율"}
        _apply_synonym_mapping(
            remaining, _syns(), ["db1"], result, semantic=True, semantic_min=0.65
        )
        assert result.db_column_mapping["db1"]["메모리점유율"] == "cmm_resource.mem_usage"
        assert result.mapping_sources["메모리점유율"] == "synonym"
        assert "메모리점유율" not in remaining

    def test_below_threshold_delegates_to_llm_path(self, monkeypatch):
        monkeypatch.setattr(
            ss,
            "semantic_match_candidates",
            lambda term, cmap: [("cmm_resource.mem_usage", 0.5)],
        )
        result = MappingResult()
        remaining = {"메모리점유율"}
        _apply_synonym_mapping(
            remaining, _syns(), ["db1"], result, semantic=True, semantic_min=0.65
        )
        assert "메모리점유율" in remaining
        assert result.db_column_mapping == {}

    def test_unavailable_module_keeps_remaining(self, monkeypatch):
        # 모델 미가용 시 빈 리스트 → 아무 것도 채택하지 않고 LLM 경로에 위임
        monkeypatch.setattr(ss, "semantic_match_candidates", lambda term, cmap: [])
        result = MappingResult()
        remaining = {"메모리점유율"}
        _apply_synonym_mapping(
            remaining, _syns(), ["db1"], result, semantic=True, semantic_min=0.65
        )
        assert "메모리점유율" in remaining

    def test_servername_hostname_guard_excluded_from_candidates(self, monkeypatch):
        # 서버명류 필드는 hostname 컬럼이 임베딩 후보에서 원천 제외되어야 한다(D-068 계열).
        # "서버 이름"은 등록 유사어("서버명")와 정확/공백제거 매칭이 실패해 Pass 4까지
        # 도달하는 서버명류 필드다("서버명" 자체는 Pass 2 정확 매칭에서 소진되므로 부적합).
        captured: dict[str, list[str]] = {}

        def _capture(term, cmap):
            captured.update(cmap)
            return []

        monkeypatch.setattr(ss, "semantic_match_candidates", _capture)
        syns = {
            "db1": {
                "cmm_resource.hostname": ["호스트"],
                "cmm_resource.name": ["자원명"],
            }
        }
        result = MappingResult()
        remaining = {"서버 이름"}
        _apply_synonym_mapping(
            remaining, syns, ["db1"], result, semantic=True, semantic_min=0.65
        )
        assert "cmm_resource.hostname" not in captured
        assert "cmm_resource.name" in captured


class TestSynonymUsageLogging:
    """동의어 사용 계측 로그 계약: 채택·미달 결정이 "[동의어]" 태그로 콘솔에 남는다.

    테스트 실행 시 ``pytest --log-cli-level=INFO``로 육안 확인할 수 있다.
    """

    def _synonym_logs(self, caplog):
        return [r.getMessage() for r in caplog.records if "[동의어]" in r.getMessage()]

    def test_exact_adoption_logged(self, caplog):
        result = MappingResult()
        remaining = {"메모리 사용률"}
        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            _apply_synonym_mapping(remaining, _syns(), ["db1"], result)
        assert any(
            "정확 매칭 확정" in m and "cmm_resource.mem_usage" in m
            for m in self._synonym_logs(caplog)
        )

    def test_semantic_adoption_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(
            ss,
            "semantic_match_candidates",
            lambda term, cmap: [("cmm_resource.mem_usage", 0.9)],
        )
        result = MappingResult()
        remaining = {"메모리점유율"}
        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            _apply_synonym_mapping(
                remaining, _syns(), ["db1"], result, semantic=True, semantic_min=0.65
            )
        assert any(
            "의미(임베딩) 매칭 확정" in m and "신뢰도=0.90" in m
            for m in self._synonym_logs(caplog)
        )

    def test_semantic_below_threshold_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(
            ss,
            "semantic_match_candidates",
            lambda term, cmap: [("cmm_resource.mem_usage", 0.5)],
        )
        result = MappingResult()
        remaining = {"메모리점유율"}
        with caplog.at_level(logging.INFO, logger="src.document.field_mapper"):
            _apply_synonym_mapping(
                remaining, _syns(), ["db1"], result, semantic=True, semantic_min=0.65
            )
        assert any(
            "임계 미달" in m and "후보 제시로 위임" in m
            for m in self._synonym_logs(caplog)
        )
