"""사건 시각 파싱 + 위임 인자 전달 (plans/50 A′-5 · SPEC-incident-scope · D-194)."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from src.domain.incident_time import DEFAULT_LOOKBACK_MINUTES, parse_incident_time
from src.nodes.fault_diagnosis import _diagnose_and_poll

NOW = datetime(2026, 9, 2, 10, 30)


def _p(q):
    return parse_incident_time(q, NOW)


# ── SPEC 표 ───────────────────────────────────────────────────────


def test_yesterday_hour_approx():
    r = _p("어제 14시쯤 web-01 장애 원인 분석해줘")
    assert r.anchor == datetime(2026, 9, 1, 14, 0) and r.resolution == "hour"
    assert r.reference_time == "2026-09-01T15:00:00"
    assert r.lookback_minutes == DEFAULT_LOOKBACK_MINUTES + 60


def test_yesterday_hour_minute():
    r = _p("어제 14시 20분에 난 장애")
    assert r.resolution == "minute"
    assert r.reference_time == "2026-09-01T14:50:00" and r.lookback_minutes == 150


def test_hhmm_form():
    r = _p("어제 14:20 장애")
    assert r.anchor == datetime(2026, 9, 1, 14, 20) and r.resolution == "minute"


def test_half_hour():
    r = _p("어제 2시 반 오후 장애")  # 표지가 뒤에 오면 보정하지 않는다(앞 12자만)
    assert r.anchor == datetime(2026, 9, 1, 2, 30)


def test_meridiem_pm():
    assert _p("어제 오후 2시 장애").anchor == datetime(2026, 9, 1, 14, 0)
    assert _p("어제 밤 11시 장애").anchor == datetime(2026, 9, 1, 23, 0)
    assert _p("어제 오전 12시 장애").anchor == datetime(2026, 9, 1, 0, 0)
    assert _p("어제 새벽 3시 장애").anchor == datetime(2026, 9, 1, 3, 0)


def test_day_only_covers_whole_day():
    r = _p("어제 장애 원인")
    assert r.resolution == "day"
    assert r.reference_time == "2026-09-01T23:59:59" and r.lookback_minutes == 1440


def test_day_words():
    assert _p("그제 장애").anchor == datetime(2026, 8, 31)
    assert _p("그저께 장애").anchor == datetime(2026, 8, 31)
    assert _p("오늘 9시 장애").anchor == datetime(2026, 9, 2, 9, 0)


def test_explicit_dates():
    assert _p("2026-08-30 14시 장애").anchor == datetime(2026, 8, 30, 14, 0)
    assert _p("2026.08.30 장애").anchor == datetime(2026, 8, 30)
    assert _p("8월 30일 14시 장애").anchor == datetime(2026, 8, 30, 14, 0)


def test_month_day_in_future_rolls_to_last_year():
    assert _p("12월 25일 장애").anchor == datetime(2025, 12, 25)


def test_hours_ago():
    r = _p("2시간 전 장애")
    assert r.anchor == datetime(2026, 9, 2, 8, 30) and r.resolution == "hour"
    assert r.reference_time == "2026-09-02T09:30:00"


def test_minutes_ago():
    r = _p("30분 전부터 느려짐")
    assert r.anchor == datetime(2026, 9, 2, 10, 0) and r.resolution == "minute"


def test_recent_window_ends_now():
    r = _p("최근 2시간 장애 원인")
    assert r.resolution == "range"
    assert r.reference_time == "2026-09-02T10:30:00" and r.lookback_minutes == 120


def test_time_only_today_or_yesterday_if_future():
    assert _p("9시쯤 장애").anchor == datetime(2026, 9, 2, 9, 0)   # 오늘 09:00 ≤ now
    assert _p("14시쯤 장애").anchor == datetime(2026, 9, 1, 14, 0)  # 오늘 14:00 > now → 어제


def test_no_time_expression_returns_none():
    assert _p("web-01 서버 원인 분석해줘") is None
    assert _p("") is None


def test_duration_is_not_a_clock():
    """'1시간 동안'의 '1시'는 시각이 아니다."""
    assert _p("1시간 동안 CPU가 높았어") is None


# ── 위임 인자 전달 ─────────────────────────────────────────────────


class _FakeClient:
    def __init__(self):
        self.kwargs = None

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def diagnose(self, question, server_name=None, hostname=None, db_id=None, **kw):
        self.kwargs = kw
        return {"investigation_id": "inv-1", "status": "accepted"}

    async def poll(self, investigation_id):
        return {"status": "done", "answer": "원인"}


class _StrictFakeClient(_FakeClient):
    """구버전 fake — 시각 kwargs를 받지 않는다(시각 없는 질의는 이 계약으로도 동작해야 한다)."""

    async def diagnose(self, question, server_name=None, hostname=None, db_id=None):
        self.kwargs = {}
        return {"investigation_id": "inv-1", "status": "accepted"}


def test_scope_kwargs_forwarded_to_client():
    c = _FakeClient()
    text, status = asyncio.run(_diagnose_and_poll(
        c, object(), "어제 14시 원인", "web-01", None, "polestar",
        {"reference_time": "2026-09-01T15:00:00", "lookback_minutes": 180},
    ))
    assert text == "원인" and status == "done"
    assert c.kwargs == {"reference_time": "2026-09-01T15:00:00", "lookback_minutes": 180}


def test_no_scope_sends_no_extra_args():
    c = _StrictFakeClient()
    asyncio.run(_diagnose_and_poll(c, object(), "원인", "web-01", None, "polestar"))
    assert c.kwargs == {}
