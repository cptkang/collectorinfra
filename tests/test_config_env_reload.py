"""config 임포트 시점 고정 회귀 방지 (docs/plan61_bugfix_plan.md B2).

AppConfig의 nested config가 인스턴스 기본값으로 선언되면 모듈 임포트 시점의
env로 고정되어, 이후 os.environ 변경 + load_config.cache_clear() 재로드가
반영되지 않는다(2026-07-15 E1 하네스 A/B 무효화 실측). default_factory 전환
후에는 재로드 시점의 env가 반영되어야 한다.
"""

from src.config import load_config


def _reload_flag(monkeypatch, value: str) -> bool:
    monkeypatch.setenv("TEXT2SQL_SEMANTIC_COMPOSE", value)
    load_config.cache_clear()
    return load_config().text2sql.semantic_compose


def test_nested_config_reflects_env_after_cache_clear(monkeypatch):
    """cache_clear 후 재로드는 변경된 env를 nested config에 반영해야 한다."""
    try:
        assert _reload_flag(monkeypatch, "false") is False
        assert _reload_flag(monkeypatch, "true") is True
        assert _reload_flag(monkeypatch, "false") is False
    finally:
        # 다른 테스트가 임포트 시점 캐시에 의존하지 않도록 원상 복구
        monkeypatch.undo()
        load_config.cache_clear()


def test_nested_config_env_flip_int_field(monkeypatch):
    """bool 외 타입(int 상한 필드)도 재로드 시 env를 반영해야 한다."""
    try:
        monkeypatch.setenv("SYNONYM_MAX_SYNONYM_SUPPLEMENT_TABLES", "7")
        load_config.cache_clear()
        assert load_config().synonym.max_synonym_supplement_tables == 7
        monkeypatch.setenv("SYNONYM_MAX_SYNONYM_SUPPLEMENT_TABLES", "21")
        load_config.cache_clear()
        assert load_config().synonym.max_synonym_supplement_tables == 21
    finally:
        monkeypatch.undo()
        load_config.cache_clear()
