"""본체 `setup_logging`의 로그 비밀값 가림 부착 (plans/167 W1).

레코드 팩토리 설치(재호출 멱등)와 structlog 프로세서(JSONRenderer 바로 앞)를 확인한다.
루트 핸들러·레벨·팩토리·structlog 설정은 끝나면 원래대로 되돌린다.
"""

from __future__ import annotations

import logging

import pytest
import structlog

from src.security.audit_logger import setup_logging
from src.utils.log_masking import mask_event_dict


@pytest.fixture
def restore_logging():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    httpx_level = logging.getLogger("httpx").level
    factory = logging.getLogRecordFactory()
    config = structlog.get_config()
    logging.setLogRecordFactory(logging.LogRecord)
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(httpx_level)
    logging.setLogRecordFactory(factory)
    structlog.configure(**config)


def test_setup_logging_installs_factory_once_and_processor(restore_logging) -> None:
    setup_logging("INFO")
    first = logging.getLogRecordFactory()
    assert getattr(first, "_log_secret_masking", False) is True

    setup_logging("DEBUG")  # 앱 lifespan·관리자 설정 리로드 재호출
    assert logging.getLogRecordFactory() is first

    processors = structlog.get_config()["processors"]
    idx = processors.index(mask_event_dict)
    assert isinstance(processors[idx + 1], structlog.processors.JSONRenderer)
    assert processors.count(mask_event_dict) == 1


def test_setup_logging_masks_stdlib_and_structlog_output(restore_logging, capsys) -> None:
    setup_logging("INFO")
    structlog.get_logger("t").info("연결", dsn="postgresql://u:S3cret@h/db", password="pw1")
    out = capsys.readouterr().out
    assert "S3cret" not in out and "pw1" not in out
    assert "postgresql://u:***@h/db" in out

    record = logging.getLogRecordFactory()(
        "t", logging.INFO, __file__, 1, "dsn=%s", ("redis://:pw2@h:6379/0",), None
    )
    assert record.getMessage() == "dsn=redis://:***@h:6379/0"


def test_main_installs_factory_before_config_load(restore_logging, monkeypatch) -> None:
    """`src.main.main()`은 설정 로드(·uvicorn reload 부모 로그)보다 먼저 팩토리를 설치한다."""
    import src.main as main_mod

    class _StopError(Exception):
        pass

    seen: list[bool] = []

    def stop() -> None:
        seen.append(getattr(logging.getLogRecordFactory(), "_log_secret_masking", False))
        raise _StopError

    monkeypatch.setattr(main_mod, "load_config", stop)
    monkeypatch.setattr("sys.argv", ["src.main"])
    with pytest.raises(_StopError):
        main_mod.main()
    assert seen == [True]
