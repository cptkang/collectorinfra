"""MCP 서버 진입점의 로그 비밀값 가림 부착 (plans/167 W1).

`main()`이 `basicConfig` 직후 레코드 팩토리를 설치하는지 본다. 설정·서버 생성은 가짜로 바꾸고
루트 로깅·팩토리는 끝나면 되돌린다. 가림 규칙 자체는 루트의 정본 테스트
(`tests/test_utils/test_log_masking.py`)가 맡는다.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import mcp_server.config as config_mod
import mcp_server.log_masking as log_masking
import mcp_server.server as server_mod
import pytest
from mcp_server.__main__ import main


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)


def test_main_installs_record_factory(monkeypatch, restore_logging) -> None:
    calls: list[str] = []
    cfg = SimpleNamespace(
        server=SimpleNamespace(
            log_level="INFO", name="t", host="127.0.0.1", port=1, transport="stdio",
            bearer_token="", admin_token="",
        )
    )
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
    monkeypatch.setattr(
        server_mod,
        "create_server",
        lambda c: SimpleNamespace(run=lambda transport: calls.append(f"run:{transport}")),
    )
    real_install = log_masking.install_log_record_factory

    def recording_install() -> None:
        calls.append("install")
        real_install()

    monkeypatch.setattr(log_masking, "install_log_record_factory", recording_install)

    main()

    assert calls == ["install", "run:stdio"]
    assert getattr(logging.getLogRecordFactory(), "_log_secret_masking", False) is True
    record = logging.getLogRecordFactory()(
        "t", logging.INFO, __file__, 1, "conn %s", ("DATABASE=d;UID=u;PWD=pw;",), None
    )
    assert record.getMessage() == "conn DATABASE=d;UID=u;PWD=***;"
