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


# ── 의도 분해 오케스트레이션 플래그 개명 하위호환 (plans/70 L2 / D-144) ──
#
# `enable_deepagent_orchestration` → `enable_intent_orchestration` 개명.
# 구 이름은 이름만 보면 1단(`enable_deepagents_package`, 트랙 B)을 가리키는 것처럼 읽히지만
# 실체는 2단(트랙 A)이다 — plans/70 v1 오판의 원인 중 하나였다.
#
# 운영 `.env`가 구 키를 쓰고 있을 수 있으므로 alias로 계속 받는다(2027-02-20 폐기 예정).
# 판정은 반드시 **pydantic 필드**로 한다 — `env_file` 로딩은 os.environ에 주입되지 않아
# `os.getenv`로는 `.env`-only 설정을 볼 수 없다(Known Mistakes 2026-06-10).

import pytest

from src.config import AppConfig


def _flag(**env) -> bool | None:
    """주어진 OS env만으로 필드를 읽는다.

    `_env_file=None`으로 `.env`를 끊는다 — 끊지 않으면 리포지토리 `.env`의 신 키가
    별칭 순서상 OS env의 구 키를 이겨서(실측), 별칭 처리 자체를 검증할 수 없다.
    """
    import os

    keys = ("ENABLE_INTENT_ORCHESTRATION", "ENABLE_DEEPAGENT_ORCHESTRATION")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        for k in keys:
            os.environ.pop(k, None)
        os.environ.update(env)
        cfg = AppConfig(_env_file=None, enable_semantic_routing=False)
        return cfg.enable_intent_orchestration
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_new_env_name_is_honored(value, expected):
    assert _flag(ENABLE_INTENT_ORCHESTRATION=value) is expected


@pytest.mark.parametrize("value,expected", [("true", True), ("false", False)])
def test_legacy_env_name_still_honored(value, expected):
    """구 키로 기동하던 운영 환경이 개명으로 깨지면 안 된다."""
    assert _flag(ENABLE_DEEPAGENT_ORCHESTRATION=value) is expected


def test_new_name_wins_when_both_present():
    """둘 다 있으면 신 이름이 이긴다(AliasChoices 순서)."""
    assert _flag(ENABLE_INTENT_ORCHESTRATION="true",
                 ENABLE_DEEPAGENT_ORCHESTRATION="false") is True


def test_alias_order_beats_source_priority(monkeypatch, tmp_path):
    """**침묵 손실 경로** — `.env`의 신 키가 OS env의 구 키를 이긴다.

    보통 OS env가 dotenv보다 우선하지만, AliasChoices는 소스 우선순위보다 **별칭 순서**를
    먼저 적용한다. 따라서 구 키로 오버라이드하려던 운영자의 의도가 조용히 사라진다.
    개명의 대가로 생긴 함정이므로 동작을 못 박고, 경고로 가시화한다(아래 테스트).
    """
    env_file = tmp_path / ".env"
    env_file.write_text("ENABLE_INTENT_ORCHESTRATION=true\n", encoding="utf-8")
    monkeypatch.setenv("ENABLE_DEEPAGENT_ORCHESTRATION", "false")
    monkeypatch.delenv("ENABLE_INTENT_ORCHESTRATION", raising=False)

    cfg = AppConfig(_env_file=str(env_file), enable_semantic_routing=False)

    assert cfg.enable_intent_orchestration is True, "별칭 순서가 소스 우선순위를 이긴다"


def test_legacy_env_name_warns(monkeypatch, caplog):
    """구 키 사용은 조용히 넘어가지 않는다 — 폐기 예고 + 무시 가능성 경고."""
    import logging

    monkeypatch.setenv("ENABLE_DEEPAGENT_ORCHESTRATION", "true")
    monkeypatch.delenv("ENABLE_INTENT_ORCHESTRATION", raising=False)

    with caplog.at_level(logging.WARNING, logger="src.config"):
        AppConfig(_env_file=None, enable_semantic_routing=False)

    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "ENABLE_INTENT_ORCHESTRATION" in joined
    assert "2027-02-20" in joined


def test_no_warning_without_legacy_key(monkeypatch, caplog):
    import logging

    monkeypatch.delenv("ENABLE_DEEPAGENT_ORCHESTRATION", raising=False)
    monkeypatch.setenv("ENABLE_INTENT_ORCHESTRATION", "true")

    with caplog.at_level(logging.WARNING, logger="src.config"):
        AppConfig(_env_file=None, enable_semantic_routing=False)

    assert "2027-02-20" not in " ".join(r.getMessage() for r in caplog.records)


def test_old_field_name_is_gone():
    """필드명은 개명됐다 — 구 필드명을 읽는 코드가 남아 있으면 조용히 False가 된다."""
    assert not hasattr(AppConfig(enable_semantic_routing=False),
                       "enable_deepagent_orchestration")


def test_field_name_injection_still_works():
    """`validation_alias`를 붙이면 기본적으로 **필드명 주입이 막힌다**.

    막힌 채로 두면 `AppConfig(enable_intent_orchestration=False)`가 조용히 무시되고
    `.env` 값으로 떨어진다 — 값이 사라지는데 예외도 경고도 없다. 격리 실행에서는
    `.env`가 우연히 같은 값이면 통과하므로, 전체 스위트에서만 드러났다(실측 2026-08-24).
    `populate_by_name=True`가 이를 막는다.
    """
    for value in (True, False):
        cfg = AppConfig(_env_file=None, enable_semantic_routing=False,
                        enable_intent_orchestration=value)
        assert cfg.enable_intent_orchestration is value, (
            f"필드명 주입 {value}가 무시됨 — populate_by_name 확인"
        )
