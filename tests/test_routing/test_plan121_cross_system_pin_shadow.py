"""plans/121 TP-1.10a — 교차 시스템 위치 힌트 고정 섀도(로그만 · 응답 비트 동일).

TP-1.10(다른 시스템 task에 위치 힌트를 적용하지 않음)은 TP-10.1 소유 판정 단계에서 넣는다.
그 전에 발동 빈도를 재려고, 분류가 고른 시스템과 힌트가 고정한 시스템이 갈리는 경우만 로그로 남긴다.
"""

from __future__ import annotations

import logging

import pytest

import src.routing.location_hints as lh


def _t(db_id: str) -> dict:
    return {"db_id": db_id, "relevance_score": 0.9, "sub_query_context": "q",
            "user_specified": False, "reason": "분류"}


ACTIVE = ["polestar_cm_gp", "polestar_cm_yd", "itam"]


def test_cross_system_pin_is_logged_and_result_unchanged(caplog, monkeypatch):
    targets = [_t("itam")]
    with caplog.at_level(logging.INFO, logger=lh.logger.name):
        out = lh.pin_targets_to_hints(
            targets, ["김포"], ACTIVE, fill_query="계약 만료일", task_query="계약 만료일",
        )
    assert "교차 시스템 힌트 고정 섀도(TP-1.10a)" in caplog.text
    monkeypatch.setattr(lh, "_log_cross_system_pin", lambda *a, **k: None)
    assert lh.pin_targets_to_hints(
        [_t("itam")], ["김포"], ACTIVE, fill_query="계약 만료일", task_query="계약 만료일",
    ) == out


@pytest.mark.parametrize("classified", [["polestar_cm_yd"], ["polestar_cm_gp", "itam"]])
def test_same_or_overlapping_system_is_silent(caplog, classified):
    with caplog.at_level(logging.INFO, logger=lh.logger.name):
        lh.pin_targets_to_hints(
            [_t(d) for d in classified], ["김포"], ACTIVE, fill_query="q", task_query=None,
        )
    assert "TP-1.10a" not in caplog.text


def test_single_system_config_is_silent(caplog):
    with caplog.at_level(logging.INFO, logger=lh.logger.name):
        lh.pin_targets_to_hints([_t("polestar_cm_yd")], ["김포"], ["polestar_cm_gp",
                                "polestar_cm_yd"], fill_query="q")
    assert "TP-1.10a" not in caplog.text
