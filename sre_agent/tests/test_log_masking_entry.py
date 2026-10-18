"""조사 서비스 진입점의 로그 비밀값 가림 부착 (plans/167 W1).

`run_service.main()`이 `basicConfig` 직후 레코드 팩토리를 설치하는지 본다. 설정 로드 단계에서
멈추고, 루트 로깅·팩토리는 끝나면 되돌린다. 가림 규칙 자체는 루트의 정본 테스트가 맡는다.
"""

from __future__ import annotations

import logging

import pytest
import sre_agent.run_service as run_service
from sre_agent.infrastructure import log_masking


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


def test_main_installs_record_factory(monkeypatch, restore_logging) -> None:
    calls: list[str] = []

    def recording_install() -> None:
        calls.append("install")
        log_masking.install_log_record_factory()

    def stop() -> None:
        calls.append("settings")
        raise _StopError

    monkeypatch.setattr(run_service, "install_log_record_factory", recording_install)
    monkeypatch.setattr(run_service.faulthandler, "register", lambda *a, **k: None)
    monkeypatch.setattr(run_service, "AgentSettings", stop)

    with pytest.raises(_StopError):
        run_service.main()

    assert calls == ["install", "settings"]
    assert getattr(logging.getLogRecordFactory(), "_log_secret_masking", False) is True
    record = logging.getLogRecordFactory()(
        "t", logging.ERROR, __file__, 1, "LLM 호출 실패: %s", ("api_key=sk-1 model=m",), None
    )
    assert record.getMessage() == "LLM 호출 실패: api_key=*** model=m"
