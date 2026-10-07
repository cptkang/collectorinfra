"""사용량(현재값)·사용 추이(기간 변화) 판정 — plans/132 v1.5 후속 ②(2026-10-07 사용자 정의).

사용량·사용률은 지금 시점 값, 사용 추이는 일정 기간 동안의 변화다 — 정본은 둘 다 관측 데이터다.
용량·사양(설치 크기)·부하·사용 현황은 판정 밖이다(조회 허용). 결정적 판정만 본다(LLM 0).
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.domain import knowledge_assets as ka
from src.domain.query_time import resolve_query_time
from src.routing.source_hints import utilization_notice_text

NOW = datetime(2026, 10, 7, 10, 0)


@pytest.mark.parametrize("text, kind", [
    # 종전 매치 유지
    ("CPU 사용률", "current"),
    ("서버 메모리 사용률 상위 10건", "current"),
    ("자산관리에서 CPU 사용량 보여줘", "current"),
    ("memory utilization", "current"),
    ("지난주 CPU 사용률 추이", "trend"),
    ("CPU 사용량 추이", "trend"),
    # 새로 잡는 사용 추이형
    ("자산관리에서 메모리 사용 추이", "trend"),
    ("서버 CPU 사용 추이 보여줘", "trend"),
    ("CPU사용추이", "trend"),
    ("디스크 추세", "trend"),
    ("메모리 사용 변화", "trend"),
    ("램 사용량", "current"),
    ("RAM 사용 추이", "trend"),
    ("DRAM 사용량", "current"),
    ("서버램 사용량", "current"),
    # 자원어 뒤 조사 「의」
    ("CPU의 사용 추이", "trend"),
    ("메모리의 사용량", "current"),
    ("CPU 사용률의 추이", "trend"),
    # 추이는 사용률 언급에 붙은 표현만 — 다른 절의 「변화」는 현재값
    ("CPU 사용률 상위 서버의 담당자 변화", "current"),
    ("CPU 사용률 일별 추이", "trend"),
    ("CPU 사용률, 서버 변화", "current"),   # 쉼표 경계를 넘지 않는다
    ("memory utilization trend", "trend"),
])
def test_utilization_kind(text: str, kind: str) -> None:
    assert ka.utilization_kind(text) == kind
    assert ka.mentions_utilization(text) is True


@pytest.mark.parametrize("text", [
    "CPU 코어 수",
    "메모리 용량",
    "디스크 용량",
    "서버 사양",
    "부하 분산 장비",
    "CPU 부하",
    "스토리지 사용량",      # 자산의 할당·장비 사용량 유사어와 겹친다 — 자원어로 넣지 않는다
    "할당/사용량",
    "장비사용량",
    "라이선스 사용 현황",
    "메모리 사용 현황",
    "서버 가동률",
    "program 사용량",       # 라틴 낱말 안의 ram은 자원어가 아니다
    "프로그램 사용량",       # 한글 낱말 안의 램도 자원어가 아니다
    "프로그램 사용 추이",
    "메모리의 용량",
    "프로그램의 사용량",
    "프로그램의 사용 추이",
])
def test_capacity_spec_and_asset_usage_are_not_utilization(text: str) -> None:
    assert ka.utilization_kind(text) is None
    assert ka.mentions_utilization(text) is False


# ── 지식 자산 K규칙(plans/143) — 새 표현도 같은 규칙 ──────────────────────────


@pytest.mark.parametrize("text", [
    "CPU 사용 추이는 이 칸으로 답한다.",
    "메모리 사용 추이 질문은 `zzab01`의 수집 이력으로 조회한다.",
])
def test_k_rule_rejects_trend_answer_here(text: str) -> None:
    assert [i["code"] for i in ka.utilization_issues(text)] == [ka.UTILIZATION_RULE]


def test_k_rule_trend_redirect_passes() -> None:
    assert ka.utilization_issues("CPU 사용 추이는 이 DB에서 답하지 않고 관측 DB로 넘긴다.") == []


def test_k3_synonym_rejects_trend_word() -> None:
    catalog = ka.make_catalog({"zzab01": ["상태구분"]}, ["zzab01"])
    item = {"table": "zzab01", "column": "상태구분", "words": ["상태값", "CPU 사용 추이"]}
    assert [i["code"] for i in ka.synonym_item_issues(item, catalog)] == [ka.UTILIZATION_RULE]


# ── 안내 문구 — 사용자가 물은 계열(현재값/추이)로 부른다(D-264) ─────────────────


def test_notice_text_follows_kind() -> None:
    current = utilization_notice_text(["자산관리"], kind="current")
    trend = utilization_notice_text(["자산관리"], kind="trend")
    assert "요청하신 사용량·사용률 정보는 「자산관리」에 없습니다" in current
    assert "사용량·사용률은 관측 데이터가 기준" in current
    assert "요청하신 사용 추이 정보는 「자산관리」에 없습니다" in trend
    assert "사용 추이는 관측 데이터가 기준" in trend
    assert utilization_notice_text([]) == utilization_notice_text([], kind="current")
    assert "사용 추이 정보는 조회 대상으로 정해진" in utilization_notice_text([], kind="trend")


# ── 시간 해석 현행 동작 고정(해석기는 plans/122 소관 — 여기서는 단언만) ───────────


def test_trend_without_period_uses_default_period_not_clarify() -> None:
    """기간 없는 추이 질의는 되묻지 않고 기본 기간(직전 완결 월)으로 해석한다(현행)."""
    qt = resolve_query_time("서버 CPU 사용 추이 보여줘", NOW)
    assert qt.clarify is None
    assert qt.metric is not None and qt.metric.source == "default"
    assert "default_period" in qt.metric.notes
    assert qt.metric.start is not None and qt.metric.end is not None
    assert (qt.metric.start.year, qt.metric.start.month, qt.metric.start.day) == (2026, 9, 1)
    assert (qt.metric.end.year, qt.metric.end.month, qt.metric.end.day) == (2026, 10, 1)


def test_trend_with_period_is_rule_resolved() -> None:
    qt = resolve_query_time("지난주 CPU 사용 추이", NOW)
    assert qt.clarify is None
    assert qt.metric is not None and qt.metric.source == "rule" and qt.metric.span == "지난주"
    assert qt.metric.start is not None and qt.metric.end is not None
    assert (qt.metric.start.month, qt.metric.start.day) == (9, 28)
    assert (qt.metric.end.month, qt.metric.end.day) == (10, 5)
