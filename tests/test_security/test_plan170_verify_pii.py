"""plans/170 트랙 C(X2·X5) 독립 검증 — `pii_filter._flag` 예외 범위·seam·반환 방향.

구현자 테스트(`test_plan170_pii_flag.py`)가 덮지 않은 경로를 보강한다.

- 필드 누락(AttributeError)·``security`` 그룹 누락도 원 코드처럼 고정값으로 떨어진다
  (``getattr``가 try 안에 있어 예외 범위가 원 코드와 같다).
- bool 변환이 원 코드와 같다(비-bool 진릿값).
- 지연 import seam — 모듈 전역에 ``load_config`` 바인딩이 없다.
- 경고는 예외 종류만 — exc_info·stack_info 없음, 메시지에 예외 원문 없음
  (ASVS v5.0.0-16.2.5 · CWE-532).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from src.security import pii_filter

_FLAGS = [
    (pii_filter.is_scrub_suspect_dates_enabled, "pii_scrub_suspect_dates", False),
    (pii_filter.is_detection_enabled, "pii_filter_log_enabled", True),
    (pii_filter.is_unmask_enabled, "pii_filter_log_unmask", False),
    (pii_filter.is_scrub_samples_enabled, "pii_scrub_samples", True),
    (pii_filter.is_block_dump_enabled, "pii_block_dump_enabled", True),
]
_IDS = [f for _, f, _ in _FLAGS]


@pytest.mark.parametrize(("func", "field", "fallback"), _FLAGS, ids=_IDS)
@pytest.mark.parametrize("cfg", [
    SimpleNamespace(security=SimpleNamespace()),  # 필드 누락
    SimpleNamespace(),  # security 그룹 누락
], ids=["no_field", "no_group"])
def test_attribute_error_falls_back_with_type_only_warning(
    monkeypatch, caplog, func, field, fallback, cfg
):
    monkeypatch.setattr("src.config.load_config", lambda: cfg)
    with caplog.at_level(logging.DEBUG, logger=pii_filter.__name__):
        assert func() is fallback
    recs = [r for r in caplog.records if r.name == pii_filter.__name__]
    assert len(recs) == 1
    r = recs[0]
    assert r.levelno == logging.WARNING
    assert "AttributeError" in r.getMessage()
    assert "has no attribute" not in r.getMessage()  # 예외 원문 미노출
    assert r.exc_info is None and r.stack_info is None


@pytest.mark.parametrize(("func", "field", "fallback"), _FLAGS, ids=_IDS)
@pytest.mark.parametrize(("raw", "expected"), [
    ("yes", True), ("", False), (0, False), (1, True), (None, False),
])
def test_truthiness_cast_unchanged(monkeypatch, func, field, fallback, raw, expected):
    cfg = SimpleNamespace(security=SimpleNamespace(**{field: raw}))
    monkeypatch.setattr("src.config.load_config", lambda: cfg)
    assert func() is expected


def test_lazy_import_seam_kept():
    """모듈 전역에 ``load_config``가 없다 — ``src.config.load_config`` patch가 호출 시점에 걸림."""
    assert not hasattr(pii_filter, "load_config")


@pytest.mark.parametrize(("func", "field", "fallback"), _FLAGS, ids=_IDS)
def test_import_failure_also_falls_back(monkeypatch, func, field, fallback):
    """지연 import 자체가 실패해도(원 코드와 같은 try 범위) 고정값."""
    import src.config as config_mod

    monkeypatch.delattr(config_mod, "load_config")
    assert func() is fallback
