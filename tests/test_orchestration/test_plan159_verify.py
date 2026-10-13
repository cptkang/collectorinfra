"""plans/159 독립 검증(D-326 ③) — 제니퍼 비교 창의 분 기간.

기준 시각 2026-10-13(화) 10:37 KST. 결함은 `xfail(strict=True)`로 남긴다(V-5는 교정 뒤 통과로 전환).
"""

from __future__ import annotations

from datetime import datetime

from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST
from src.orchestration import apm_query as aq

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)


def test_two_period_compare_with_minute_period_widens_not_asks() -> None:
    """V-5 교정(2026-10-13) — 기간 2개 분기도 분 구간을 시 단위 통계 구간으로 넓힌다(D-326 ③).

    교정 전: 분 구간 끝을 정시로 깎아(10:07 ~ 10:00) 역전 → 「비교할 두 기간을 정하지 못해」 되묻기.
    """
    plan = aq.plan_compare_window(resolve_query_time("web01 어제 대비 최근 30분 응답시간", NOW))
    assert plan.mode == "window", plan.note
    assert plan.args == {
        "current_start": "2026-10-13T09:00:00+09:00", "current_end": "2026-10-13T10:00:00+09:00",
        "baseline_start": "2026-10-12T00:00:00+09:00",
        "baseline_end": "2026-10-13T00:00:00+09:00",
    }
    assert "정시 구간으로 넓혀" in plan.note


def test_two_period_compare_overlapping_after_widening_asks_with_reason() -> None:
    """V-5 교정 — 「지난 1시간 대비 최근 30분」은 넓히면 두 구간이 같은 [09:00, 10:00)이다.

    검증 초안의 기대(window)는 정책과 맞지 않아 바꿨다: 시 단위 통계로는 겹치지 않는 두 구간을 만들
    수 없으니 되묻되, 사유는 「기간을 정하지 못해」가 아니라 겹침이다(`COMPARE_OVERLAP_NOTE`).
    """
    plan = aq.plan_compare_window(
        resolve_query_time("web01 지난 1시간 대비 최근 30분 처리량", NOW))
    assert plan.mode == "ask"
    assert plan.note.startswith(aq.COMPARE_OVERLAP_NOTE)
    assert plan.note != aq.COMPARE_ASK_NOTE
    assert "2026-10-13 09:00 ~ 2026-10-13 10:00" in plan.note


def test_single_minute_ago_compare_widens() -> None:
    """「5분 전 대비」(relation ago)도 롤링 분 기간이라 시 단위로 넓힌다(통과 고정)."""
    plan = aq.plan_compare_window(resolve_query_time("web01 5분 전 대비 처리량", NOW))
    assert plan.mode == "window"
    assert plan.args["current_start"].endswith("09:00:00+09:00")
    assert plan.args["current_end"].endswith("10:00:00+09:00")
    assert "정시 구간으로 넓혀" in (plan.note or "")
