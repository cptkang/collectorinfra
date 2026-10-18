"""plans/170 트랙 C(X2·X5) — `pii_filter` 플래그 5함수의 `_flag` 통합 특성화.

반환값은 리팩터 전 원 코드에서 읽어 고정했다(설정값 그대로 · 로드 실패 시 고정값).
로드 실패 시 경고 1건 · 예외 원문 미노출 · 정상 경로 경고 0을 단언한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest

from src.config import load_config
from src.security import pii_filter

# (공개 함수, security 필드, 로드 실패 시 고정값) — 원 코드의 except 반환값.
_FLAGS: list[tuple[Callable[[], bool], str, bool]] = [
    (pii_filter.is_scrub_suspect_dates_enabled, "pii_scrub_suspect_dates", False),
    (pii_filter.is_detection_enabled, "pii_filter_log_enabled", True),
    (pii_filter.is_unmask_enabled, "pii_filter_log_unmask", False),
    (pii_filter.is_scrub_samples_enabled, "pii_scrub_samples", True),
    (pii_filter.is_block_dump_enabled, "pii_block_dump_enabled", True),
]
_IDS = [field for _, field, _ in _FLAGS]
_LOGGER = pii_filter.__name__


def _patch_config(
    monkeypatch: pytest.MonkeyPatch, field: str, value: bool
) -> None:
    """lru_cache 싱글톤을 고치지 않도록 깊은 사본에 값을 넣어 `load_config`를 대체한다."""
    cfg = load_config().model_copy(deep=True)
    setattr(cfg.security, field, value)
    monkeypatch.setattr("src.config.load_config", lambda: cfg)


@pytest.mark.parametrize("value", [True, False])
@pytest.mark.parametrize(("func", "field", "fallback"), _FLAGS, ids=_IDS)
def test_flag_follows_config(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    func: Callable[[], bool],
    field: str,
    fallback: bool,
    value: bool,
) -> None:
    """설정값을 그대로 돌려주고 정상 경로에서는 경고를 남기지 않는다."""
    _patch_config(monkeypatch, field, value)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert func() is value
    assert [r for r in caplog.records if r.name == _LOGGER] == []


@pytest.mark.parametrize(("func", "field", "fallback"), _FLAGS, ids=_IDS)
def test_flag_load_failure_returns_fixed_default_and_warns(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    func: Callable[[], bool],
    field: str,
    fallback: bool,
) -> None:
    """설정 로드 실패 시 원 코드와 같은 고정값 · 경고 1건(예외 종류만, 원문·스택 없음)."""

    def _boom() -> None:
        raise ValueError("SECRET-VALUE")

    monkeypatch.setattr("src.config.load_config", _boom)
    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        assert func() is fallback
    records = [r for r in caplog.records if r.name == _LOGGER]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    msg = rec.getMessage()
    assert "SECRET-VALUE" not in msg
    assert "ValueError" in msg
    assert field in msg
    assert str(fallback) in msg
    assert rec.exc_info is None
    assert "SECRET-VALUE" not in caplog.text
