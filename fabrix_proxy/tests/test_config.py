"""config · 엔트리 — 토큰 기동 거부 · JSON 파싱 · `.env` 차단 (plans/148 1단계 테스트 A)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeUpstream, make_settings
from fabrix_proxy.app import create_app
from fabrix_proxy.config import PACKAGE_ROOT, ConfigError, ProxySettings, require_token

from fabrix_proxy import __main__ as entry

ENV_KEYS = (
    "FABRIX_PROXY_TOKEN",
    "FABRIX_PROXY_MODEL_ALIASES",
    "FABRIX_LLM_CONFIG",
    "FABRIX_PROXY_FEWSHOT",
    "FABRIX_TIMEOUT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_package_root_is_proxy_dir() -> None:
    assert PACKAGE_ROOT == Path(__file__).resolve().parents[1]


def test_empty_token_refused() -> None:
    settings = ProxySettings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(ConfigError):
        require_token(settings)
    with pytest.raises(ConfigError):
        create_app(make_settings(fabrix_proxy_token="  "), upstream=FakeUpstream())


def test_main_refuses_without_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(ProxySettings.model_config, "env_file", None)
    monkeypatch.setenv("FABRIX_API_KEY", "should-not-print")
    started = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: started.append(1))
    assert entry.main([]) != 0
    assert started == []
    err = capsys.readouterr().err
    assert "FABRIX_PROXY_TOKEN" in err
    assert "should-not-print" not in err


def test_main_bad_value_reports_field_without_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(ProxySettings.model_config, "env_file", None)
    monkeypatch.setenv("FABRIX_TIMEOUT", "secretish-not-a-number")
    assert entry.main([]) == 2
    err = capsys.readouterr().err
    assert "fabrix_timeout" in err
    assert "secretish" not in err


def test_main_starts_with_cli_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ProxySettings.model_config, "env_file", None)
    monkeypatch.setenv("FABRIX_PROXY_TOKEN", "tok")
    seen: dict = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **k: seen.update(k))
    assert entry.main(["--host", "127.0.0.2", "--port", "9999"]) == 0
    assert seen["host"] == "127.0.0.2" and seen["port"] == 9999


def test_json_list_and_dict_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABRIX_PROXY_MODEL_ALIASES", '["a","b"]')
    monkeypatch.setenv("FABRIX_LLM_CONFIG", '{"temperature": 0.1, "top_k": 5}')
    settings = ProxySettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.fabrix_proxy_model_aliases == ["a", "b"]
    assert settings.fabrix_llm_config == {"temperature": 0.1, "top_k": 5}


def test_json_from_env_file(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "FABRIX_PROXY_TOKEN=x\n"
        'FABRIX_PROXY_MODEL_ALIASES=["m1"]\n'
        'FABRIX_LLM_CONFIG={"top_p": 0.5}\n'
    )
    settings = ProxySettings(_env_file=env)  # type: ignore[call-arg]
    assert settings.fabrix_proxy_model_aliases == ["m1"]
    assert settings.fabrix_llm_config == {"top_p": 0.5}


def test_defaults_without_env_file() -> None:
    settings = ProxySettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.fabrix_proxy_host == "127.0.0.1"
    assert settings.fabrix_proxy_port == 9095
    assert settings.fabrix_proxy_model_aliases == ["fabrix-tools"]
    assert settings.fabrix_llm_config == {}
    assert settings.fabrix_verify_ssl is False
    assert settings.fabrix_proxy_contents_mode == "turns"
    assert settings.fabrix_proxy_fewshot == "static"
    assert settings.fabrix_proxy_poc_mode is False


def test_invalid_choice_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FABRIX_PROXY_FEWSHOT", "lots")
    with pytest.raises(ValueError):
        ProxySettings(_env_file=None)  # type: ignore[call-arg]


def test_env_example_loads_and_has_no_inline_comments() -> None:
    example = PACKAGE_ROOT / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            assert "=" in line and " #" not in line, line
    settings = ProxySettings(_env_file=example)  # type: ignore[call-arg]
    assert settings.fabrix_proxy_token == ""
    assert settings.fabrix_api_key == ""
    field_keys = {name.upper() for name in ProxySettings.model_fields}
    example_keys = {
        line.split("=", 1)[0]
        for line in example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    assert example_keys == field_keys


def test_protocol_and_fewshot_files_loaded_once(tmp_path: Path) -> None:
    protocol = tmp_path / "p.txt"
    protocol.write_text("CUSTOM\n{{TOOLS}}", encoding="utf-8")
    app = create_app(
        make_settings(fabrix_proxy_protocol_file=str(protocol)), upstream=FakeUpstream()
    )
    assert app is not None
    bad = tmp_path / "bad.txt"
    bad.write_text("no placeholder", encoding="utf-8")
    with pytest.raises(ConfigError):
        create_app(make_settings(fabrix_proxy_protocol_file=str(bad)), upstream=FakeUpstream())
    with pytest.raises(ConfigError):
        create_app(
            make_settings(fabrix_proxy_fewshot_file=str(tmp_path / "missing.json")),
            upstream=FakeUpstream(),
        )
