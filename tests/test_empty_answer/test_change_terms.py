"""변화 어휘 선언 파일 + 로더 (Plan 82 W8-T1 · SPEC-empty-answer-diagnosis).

선언 파일만으로 정책이 결정되는지 검증한다 — 어휘·임계가 코드 리터럴로 새면
"규칙 추가에 코드 변경이 필요한" 설계 실패이므로 AST로 못 박는다(`test_middleware` 선례).

LLM·DB·네트워크 0(D-127).
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.domain import change_terms as ct


@pytest.fixture(autouse=True)
def _clear_cache():
    """lru_cache가 테스트 간 선언 파일 경로를 물고 가지 않게 한다."""
    ct._raw_config.cache_clear()
    ct.load_change_terms.cache_clear()
    yield
    ct._raw_config.cache_clear()
    ct.load_change_terms.cache_clear()


def test_declared_file_parses_with_required_keys():
    terms = ct.load_change_terms()

    assert terms.spike_terms, "급증 어휘가 선언 파일에 있어야 한다"
    assert "갑자기" in terms.spike_terms
    assert "급증" in terms.spike_terms
    # U18 사용자 확정 — 기본 차분 임계 +20%p
    assert terms.default_delta_pp == 20
    # U15=(b) 월 단위 한정
    assert terms.default_baseline == "month"
    assert terms.explicit_delta_patterns
    assert terms.week_terms


def test_missing_keys_fall_back_to_model_defaults():
    terms = ct.ChangeTerms()

    assert terms.spike_terms == []
    assert terms.default_delta_pp == 20.0
    assert terms.default_baseline == "month"
    assert terms.explicit_delta_patterns == []


def test_missing_file_yields_empty_rules_not_exception(monkeypatch, tmp_path):
    """파일 부재는 **빈 규칙**이다 — 예외로 파이프라인을 죽이지 않는다."""
    monkeypatch.setattr(ct, "_CONFIG", tmp_path / "does-not-exist.yaml")
    ct._raw_config.cache_clear()
    ct.load_change_terms.cache_clear()

    terms = ct.load_change_terms()

    assert terms.spike_terms == []
    # 어휘가 없으면 급증 판정은 발동하지 않는다(현행 동작 유지 = 회귀 0)
    assert ct.resolve_spike_request("파일시스템이 갑자기 급증했다") is None


def test_policy_literals_live_in_declaration_not_code():
    """어휘·임계가 모듈 코드에 하드코딩돼 있지 않다(표 29 G).

    docstring의 *설명* 문구는 의존이 아니므로 AST에서 docstring을 제외하고 본다
    (`test_middleware`가 낸 오탐을 되풀이하지 않는다).
    """
    tree = ast.parse(inspect.getsource(ct))
    docstrings = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef))}
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and id(n.value) not in docstrings
    ]

    declared = ct.load_change_terms()
    for term in declared.spike_terms + declared.week_terms:
        assert term not in literals, f"어휘 '{term}'가 코드에 하드코딩됨 — 선언 파일이 정본이어야 한다"


def test_injected_terms_override_declaration():
    """주입점이 있어야 테스트·확장이 선언 파일을 건드리지 않고 검증된다."""
    injected = ct.ChangeTerms(spike_terms=["폭주"], default_delta_pp=5)

    assert ct.matched_spike_term("트래픽이 폭주했다", injected) == "폭주"
    assert ct.matched_spike_term("트래픽이 급증했다", injected) == ""
    assert ct.resolve_spike_request("폭주", injected).delta_pp == 5
