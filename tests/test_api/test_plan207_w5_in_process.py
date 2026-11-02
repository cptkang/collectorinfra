"""plans/207 트랙 B W5 · C-7 — 본체 기동 분기 `ALARM_WORKER_IN_PROCESS`.

- 기본(true · 필드 없음 · `MagicMock` 설정): 본체가 종전처럼 워커 태스크를 만든다(비트 동일).
- false: `ALARM_ENABLED=true`여도 워커를 만들지 않고 INFO 1줄(별도 프로세스 안내).
- `ALARM_ENABLED=false`: 종전대로 None · 안내 줄 없음.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

import noise_gate.application.alarm_worker as worker_mod
from src.api import server
from src.config import AlarmConfig


@pytest.fixture
def made(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """본체가 만든 워커(가짜) 목록."""
    seen: list[Any] = []

    class _FakeWorker:
        def __init__(self, config: Any, **kwargs: Any) -> None:
            seen.append(config)

        async def run(self) -> None:
            return None

    monkeypatch.setattr(worker_mod, "AlarmWorker", _FakeWorker)
    return seen


def _cfg(**alarm: Any) -> SimpleNamespace:
    return SimpleNamespace(
        alarm=AlarmConfig.model_construct(enabled=True, **alarm),
        duty_area=SimpleNamespace(),
    )


async def _start(cfg: Any) -> Any:
    task = server._start_alarm_worker(cfg)
    if task is not None:
        await task
    return task


def test_default_starts_worker_in_process(made: list[Any], caplog) -> None:
    cfg = _cfg()
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        task = asyncio.run(_start(cfg))
    assert task is not None and made == [cfg]
    assert "알람 분석 워커 시작" in caplog.text
    assert "noise_gate.worker" not in caplog.text


def test_missing_field_and_mock_config_keep_in_process(made: list[Any]) -> None:
    legacy = SimpleNamespace(alarm=SimpleNamespace(enabled=True, redis_stream_key="s"))
    assert asyncio.run(_start(legacy)) is not None
    mocked = SimpleNamespace(alarm=MagicMock(enabled=True), duty_area=None)
    assert asyncio.run(_start(mocked)) is not None
    assert made == [legacy, mocked]


def test_out_of_process_skips_worker_with_info(made: list[Any], caplog) -> None:
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        task = asyncio.run(_start(_cfg(worker_in_process=False)))
    assert task is None and made == []
    lines = [r for r in caplog.records if "python -m noise_gate.worker" in r.getMessage()]
    assert len(lines) == 1 and lines[0].levelno == logging.INFO
    assert "알람 분석 워커 시작" not in caplog.text


def test_alarm_disabled_unchanged(made: list[Any], caplog) -> None:
    cfg = SimpleNamespace(alarm=AlarmConfig.model_construct(enabled=False, worker_in_process=False))
    with caplog.at_level(logging.INFO, logger=server.logger.name):
        assert asyncio.run(_start(cfg)) is None
    assert made == [] and "noise_gate.worker" not in caplog.text
