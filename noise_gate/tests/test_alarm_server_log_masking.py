"""alarm_server 진입점의 로그 비밀값 가림 부착 (plans/167 W1).

`main()`이 `basicConfig` 직후 레코드 팩토리를 설치하는지 본다(사본 없이 정본
`src.utils.log_masking`을 쓴다 — D-139 부기 역방향 허용 utils). 설정·수신부는 가짜로 바꾸고
루트 로깅·팩토리는 끝나면 되돌린다.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from src.utils import log_masking

import noise_gate.alarm_server.__main__ as main_mod


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

    class FakeReceiver:
        def __init__(self, config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("start")

    def recording_install() -> None:
        calls.append("install")
        log_masking.install_log_record_factory()

    cfg = SimpleNamespace(
        log_level="INFO", stream_key="alarm:raw", socket_port=9100, admin_port=None, admin_token=""
    )
    monkeypatch.setattr(main_mod, "AlarmServerConfig", lambda: cfg)
    monkeypatch.setattr(main_mod, "TcpReceiver", FakeReceiver)
    monkeypatch.setattr(main_mod, "install_log_record_factory", recording_install)

    main_mod.main()

    assert calls == ["install", "start"]
    assert getattr(logging.getLogRecordFactory(), "_log_secret_masking", False) is True
    record = logging.getLogRecordFactory()(
        "t", logging.ERROR, __file__, 1, "Redis 연결 실패: %s", ("redis://:pw@h:6379/0",), None
    )
    assert record.getMessage() == "Redis 연결 실패: redis://:***@h:6379/0"
