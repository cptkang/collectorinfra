"""평가·스모크 트래픽 키 분리(EVAL_GEMINI_API_KEY) 단위 테스트.

`scripts/eval_text2sql.py`·`scripts/run_pipeline_test.py`의 `_load_eval_config`가
env `EVAL_GEMINI_API_KEY` 설정 시 config의 Gemini API 키를 오버라이드하고,
미설정 시 기존 동작(운영 키) 그대로인지 검증한다. LLM 실호출 없음.

scripts는 패키지가 아니므로 importlib로 파일에서 직접 로드한다(기존 스크립트 테스트 관례).
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    """scripts/<name>.py를 모듈로 로드한다."""
    path = _REPO_ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_EVAL = _load_script("eval_text2sql")
_SMOKE = _load_script("run_pipeline_test")


@pytest.fixture
def prod_config(mock_config):
    """운영 키가 실린 AppConfig(테스트용)."""
    mock_config.llm.gemini_api_key = "prod-key"
    return mock_config


# ──────────────────────────────────────────────
# eval_text2sql._load_eval_config
# ──────────────────────────────────────────────

def test_eval_text2sql_overrides_with_env(prod_config, monkeypatch, caplog):
    """EVAL_GEMINI_API_KEY 설정 시 config Gemini 키를 그 값으로 오버라이드한다."""
    # _load_eval_config는 호출 시점에 src.config.load_config를 임포트해 호출한다.
    monkeypatch.setattr("src.config.load_config", lambda: prod_config)
    monkeypatch.setenv("EVAL_GEMINI_API_KEY", "eval-key-123")
    with caplog.at_level(logging.INFO):
        cfg = _EVAL._load_eval_config()
    assert cfg is prod_config
    assert cfg.llm.gemini_api_key == "eval-key-123"
    assert "[평가분리] EVAL_GEMINI_API_KEY 사용" in caplog.text


def test_eval_text2sql_unchanged_without_env(prod_config, monkeypatch):
    """미설정 시 기존 동작 그대로(하위호환) — 키 불변."""
    monkeypatch.setattr("src.config.load_config", lambda: prod_config)
    monkeypatch.delenv("EVAL_GEMINI_API_KEY", raising=False)
    cfg = _EVAL._load_eval_config()
    assert cfg.llm.gemini_api_key == "prod-key"


def test_eval_text2sql_no_repeated_log_on_same_object(prod_config, monkeypatch, caplog):
    """lru_cache로 같은 config 객체가 재반환되면 오버라이드 로그가 반복되지 않는다."""
    monkeypatch.setattr("src.config.load_config", lambda: prod_config)
    monkeypatch.setenv("EVAL_GEMINI_API_KEY", "eval-key-123")
    with caplog.at_level(logging.INFO):
        _EVAL._load_eval_config()
        _EVAL._load_eval_config()
    assert caplog.text.count("[평가분리]") == 1


# ──────────────────────────────────────────────
# run_pipeline_test._load_eval_config
# ──────────────────────────────────────────────

def test_run_pipeline_test_overrides_with_env(prod_config, monkeypatch):
    """EVAL_GEMINI_API_KEY 설정 시 config Gemini 키를 그 값으로 오버라이드한다."""
    # run_pipeline_test는 모듈 최상단에서 load_config를 바인딩하므로 모듈 속성을 패치한다.
    monkeypatch.setattr(_SMOKE, "load_config", lambda: prod_config)
    monkeypatch.setenv("EVAL_GEMINI_API_KEY", "eval-key-456")
    cfg = _SMOKE._load_eval_config()
    assert cfg.llm.gemini_api_key == "eval-key-456"


def test_run_pipeline_test_unchanged_without_env(prod_config, monkeypatch):
    """미설정 시 기존 동작 그대로(하위호환) — 키 불변."""
    monkeypatch.setattr(_SMOKE, "load_config", lambda: prod_config)
    monkeypatch.delenv("EVAL_GEMINI_API_KEY", raising=False)
    cfg = _SMOKE._load_eval_config()
    assert cfg.llm.gemini_api_key == "prod-key"
