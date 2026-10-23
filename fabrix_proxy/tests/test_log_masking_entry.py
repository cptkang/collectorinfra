"""프록시 진입점의 로그 비밀값 가림 부착 (plans/167 W1).

`main()`이 `basicConfig` 직후 레코드 팩토리를 설치하는지 본다. 설정·앱 생성은 가짜로 바꾸고
uvicorn 기동은 막는다. 루트 로깅·팩토리는 끝나면 되돌린다. 가림 규칙 자체는 루트의 정본 테스트가
맡는다.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from fabrix_proxy import __main__ as entry
from fabrix_proxy import log_masking


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
    settings = SimpleNamespace(
        fabrix_proxy_log_level="INFO",
        fabrix_proxy_admin_token="",
        fabrix_proxy_host="127.0.0.1",
        fabrix_proxy_port=1,
        fabrix_proxy_model_aliases=["a"],
        fabrix_proxy_contents_mode="m",
        fabrix_proxy_fewshot="f",
        fabrix_proxy_poc_mode=False,
    )

    def recording_install() -> None:
        calls.append("install")
        log_masking.install_log_record_factory()

    def fake_create_app(s: object) -> object:
        calls.append("create_app")
        return object()

    monkeypatch.setattr(entry, "ProxySettings", lambda: settings)
    monkeypatch.setattr(entry, "install_log_record_factory", recording_install)
    monkeypatch.setattr(entry, "create_app", fake_create_app)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: calls.append("run"))

    assert entry.main([]) == 0

    assert calls == ["install", "create_app", "run"]
    assert getattr(logging.getLogRecordFactory(), "_log_secret_masking", False) is True
    record = logging.getLogRecordFactory()(
        "t", logging.WARNING, __file__, 1, "upstream %s", ("x-generative-ai-client: ck-1",), None
    )
    assert record.getMessage() == "upstream x-generative-ai-client: ***"
