"""D-346 — uvicorn 자동 재시작은 옵트인(`API_RELOAD`)이다. 운영 기본은 꺼짐."""

from __future__ import annotations

from typing import Any

import pytest

from src.config.services import ServerConfig


def test_reload_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_RELOAD", raising=False)
    assert ServerConfig(_env_file=None).reload is False


def test_reload_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_RELOAD", "true")
    assert ServerConfig(_env_file=None).reload is True


@pytest.mark.parametrize("reload", [False, True])
def test_run_server_passes_config_reload(monkeypatch: pytest.MonkeyPatch, reload: bool) -> None:
    import src.main as main_mod

    seen: dict[str, Any] = {}

    class _Cfg:
        class server:  # noqa: N801 — 설정 그룹 모양만 흉내 낸다
            host, port = "127.0.0.1", 18000

    _Cfg.server.reload = reload  # type: ignore[attr-defined]
    monkeypatch.setattr(main_mod, "load_config", lambda: _Cfg)
    monkeypatch.setattr(main_mod.uvicorn, "run", lambda app, **kw: seen.update(kw, app=app))
    main_mod.run_server()
    assert seen["reload"] is reload and seen["app"] == "src.api.server:app"
