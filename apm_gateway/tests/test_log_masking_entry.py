"""게이트웨이 진입점의 로그 비밀값 가림 부착 (plans/167 W1).

`_serve()`가 `basicConfig` 직후 레코드 팩토리를 설치하는지 본다. 설정은 가짜로 바꾸고 설치 바로
다음 단계(`quiet_http_loggers`)에서 멈춘다. 루트 로깅·팩토리는 끝나면 되돌린다. 가림 규칙 자체는
루트의 정본 테스트가 맡는다.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import apm_gateway.__main__ as main_mod
import apm_gateway.adapters.log_masking as log_masking
import pytest

import apm_gateway.config as config_mod


class _StopError(Exception):
    pass


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


def test_serve_installs_record_factory_after_basic_config(monkeypatch, restore_logging) -> None:
    calls: list[str] = []
    cfg = SimpleNamespace(server=SimpleNamespace(log_level="INFO"))
    monkeypatch.setattr(config_mod, "load_config", lambda: cfg)
    real_install = log_masking.install_log_record_factory

    def recording_install() -> None:
        calls.append("install")
        real_install()

    def stop() -> None:
        calls.append("quiet")
        raise _StopError

    monkeypatch.setattr(log_masking, "install_log_record_factory", recording_install)
    monkeypatch.setattr(main_mod, "quiet_http_loggers", stop)

    with pytest.raises(_StopError):
        asyncio.run(main_mod._serve())

    assert calls == ["install", "quiet"]
    assert getattr(logging.getLogRecordFactory(), "_log_secret_masking", False) is True
    record = logging.getLogRecordFactory()(
        "t", logging.WARNING, __file__, 1, "%s", ("Authorization: Bearer tok-1",), None
    )
    assert record.getMessage() == "Authorization: Bearer ***"
