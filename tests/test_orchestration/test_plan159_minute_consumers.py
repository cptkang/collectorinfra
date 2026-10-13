"""plans/159 W3 — 분 단위 기간(D-328)의 소비처: 고지 · 제니퍼 창·비교 창 · 알람 `ctime` · 종단.

고정하는 계약(기준 시각 2026-10-13(화) 10:37 KST):
  ① 고지 — 통계 확장(`minute_widened_to_hour`)은 `[조회 기간]` 줄(kind `query_period` · 의무·중립)에
     문구로 실린다. 사건 분 구간은 넓히지 않아 그 문구가 없다
  ② 제니퍼 창 — 사건 분 구간이 그대로 `lookback_minutes`로 간다(`reference_time` null). 롤링 보정은
     분 구간에 걸리지 않거나(끝이 정시 아님) 걸려도 같은 값이다(기준 시각이 정각)
  ③ 제니퍼 비교 창 — 「최근 30분 대비」는 비교 도구가 시 단위 통계라 통계 해석대로 정시 구간으로
     넓힌다(종전: 비교 구간 (10:07, 10:00) 역전 → 되묻기). 시 단위 롤링 비교는 종전 그대로
  ④ 폴스타 — 알람 `ctime` 리터럴은 분 경계 그대로 · 통계 경계는 시 경계(변경 0) · 검증기 통과
  ⑤ 종단(가짜 LLM · 실제 `build_graph()` · 2단 `intent_orchestration`) — 응답시간(제니퍼) · CPU(통계
     확장 + 고지) · 알람(분 경계 SQL · 검증기 통과 · 재생성 0)

실 LLM·DB·네트워크 0. 2단 종단은 plans/122 트랙 T 검증 하네스(`build_env`)를 그대로 쓴다.
"""

from __future__ import annotations

import importlib
import json
import re
from datetime import datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from src.db_adapters.polestar.time_period import (
    alarm_ts_bounds,
    sql_applies_period,
    stat_bounds,
)
from src.db_adapters.polestar.validators import check_time_conditions
from src.domain import disclosure as disc
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST, NOTE_MINUTE_WIDENED
from src.orchestration import apm_query as aq
from tests.test_orchestration.test_plan125_apm_query import _env, _Gateway
from tests.test_orchestration.test_plan134_w34_verify_real import _NoEdge
from tests.test_plan122_t_verify import (  # noqa: F401 — build_env는 픽스처
    _STAT_RE,
    _TS_RE,
    DB_MULTI,
    DB_SINGLE,
    _tr,
    build_env,
)

NOW = datetime(2026, 10, 13, 10, 37, tzinfo=KST)
WIDENED_PHRASE = "시 단위 통계라 분 단위 기간을 정시 구간으로 넓혀 조회했습니다"
CPU_PERIOD_LINE = (
    "[조회 기간] 2026-10-13 09:00 ~ 2026-10-13 10:00 · 시간 단위 (「최근 30분」) · "
    + WIDENED_PHRASE
)


def _qt(text: str, now: datetime = NOW) -> QueryTime:
    return resolve_query_time(text, now)


def _views() -> dict[str, aq.ViewSpec]:
    return {v.id: v for v in aq.apm_views()}


# ── ① 고지 ──────────────────────────────────────────────────────────────────────

def test_widened_metric_period_line_carries_the_neutral_phrase() -> None:
    qt = _qt("최근 30분 CPU 사용률")
    assert qt.metric is not None and NOTE_MINUTE_WIDENED in qt.metric.notes
    assert disc.query_period_text(qt.metric) == CPU_PERIOD_LINE


def test_event_minute_period_is_not_widened_and_has_no_phrase() -> None:
    qt = _qt("최근 10분 알람")
    assert qt.event is not None and NOTE_MINUTE_WIDENED not in qt.event.notes
    text = disc.query_period_text(qt.event, subject="event")
    assert text == "[조회 기간] 2026-10-13 10:27 ~ 2026-10-13 10:37 (「최근 10분」)"


def test_period_line_is_mandatory_and_survives_the_optional_cap() -> None:
    """`[조회 기간]`은 의무 kind다 — 선택 고지가 상한을 넘게 몰려도 본문에서 밀리지 않는다."""
    spec = disc.KIND_TABLE[disc.QUERY_PERIOD]
    assert spec.mandatory and spec.grade == "neutral"
    optional = [k for k, s in disc.KIND_TABLE.items()
                if not s.mandatory and s.scope != "shadow"]
    items = [disc.make(k, f"선택 고지 {i}") for i, k in enumerate(optional)]
    items.append(disc.make(disc.QUERY_PERIOD, CPU_PERIOD_LINE))
    body = disc.body_lines_for_turn(items)
    assert CPU_PERIOD_LINE in [d["text"] for d in body]
    assert disc.render_line(disc.make(disc.QUERY_PERIOD, CPU_PERIOD_LINE)) == CPU_PERIOD_LINE


# ── ② 제니퍼 창 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("view", ["apm.app_health", "apm.events", "apm.app_stats"])
def test_minute_event_window_goes_as_lookback_minutes(view: str) -> None:
    plan = aq.plan_window(_views()[view], None, NOW, query_time=_qt("web01 최근 30분 응답시간"))
    assert plan.mode == "window"
    assert plan.args == {"reference_time": None, "lookback_minutes": 30}


def test_minute_window_with_seconds_in_anchor_stays_at_the_minute_boundary() -> None:
    """기준 시각에 초가 있으면 구간 끝은 분 경계(10:37:00)다 — 1분 안이라 `reference_time` null."""
    at = datetime(2026, 10, 13, 10, 37, 42, tzinfo=KST)
    plan = aq.plan_window(_views()["apm.app_health"], None, at,
                          query_time=_qt("web01 최근 30분 응답시간", at))
    assert plan.args == {"reference_time": None, "lookback_minutes": 30}


def test_rolling_hour_correction_coinciding_on_the_hour_keeps_minute_window() -> None:
    """기준 시각이 정각(10:00:00)이면 분 구간 끝도 정시라 롤링 보정 조건이 우연히 성립한다 —
    보정 결과가 같은 구간이어야 한다(길이 N분 · 끝 = 기준 시각)."""
    at = datetime(2026, 10, 13, 10, 0, tzinfo=KST)
    qt = _qt("web01 최근 10분 응답시간", at)
    assert qt.event is not None
    assert (qt.event.start, qt.event.end) == (datetime(2026, 10, 13, 9, 50, tzinfo=KST), at)
    plan = aq.plan_window(_views()["apm.app_health"], None, at, query_time=qt)
    assert plan.args == {"reference_time": None, "lookback_minutes": 10}


def test_hour_rolling_window_is_unchanged() -> None:
    """시 단위 롤링(「최근 3시간」) 보정은 종전 그대로 — 같은 길이를 기준 시각까지 민다(V6-2)."""
    plan = aq.plan_window(_views()["apm.app_health"], None, NOW,
                          query_time=_qt("web01 최근 3시간 응답시간"))
    assert plan.args == {"reference_time": None, "lookback_minutes": 180}


# ── ③ 제니퍼 비교 창 ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("text", "cur", "base"), [
    ("web01 최근 30분 대비 응답시간", ("09:00", "10:00"), ("08:00", "09:00")),
    ("web01 최근 90분 대비 처리량", ("08:00", "10:00"), ("06:00", "08:00")),
    ("web01 최근 1시간 30분 대비 처리량", ("08:00", "10:00"), ("06:00", "08:00")),
])
def test_minute_rolling_compare_widens_to_hour_statistics(text, cur, base) -> None:
    """교정 전: 비교 구간 = (분 시작, 직전 정시) — 30분은 (10:07, 10:00) 역전 → 되묻기,
    90분은 (09:07, 10:00) 53분과 그 앞 53분(게이트웨이 시 경계 확장에서 겹친다)."""
    plan = aq.plan_compare_window(_qt(text))
    assert plan.mode == "window", plan
    day = "2026-10-13T"
    assert plan.args == {
        "current_start": f"{day}{cur[0]}:00+09:00", "current_end": f"{day}{cur[1]}:00+09:00",
        "baseline_start": f"{day}{base[0]}:00+09:00", "baseline_end": f"{day}{base[1]}:00+09:00",
    }
    assert "분 단위 기간을 정시 구간으로 넓혀" in plan.note
    assert plan.kind == aq.NOTE_TRACE


def test_hour_rolling_compare_is_unchanged() -> None:
    plan = aq.plan_compare_window(_qt("web01 최근 3시간 대비 처리량"))
    assert plan.args == {
        "current_start": "2026-10-13T07:00:00+09:00", "current_end": "2026-10-13T10:00:00+09:00",
        "baseline_start": "2026-10-13T04:00:00+09:00",
        "baseline_end": "2026-10-13T07:00:00+09:00",
    }
    assert plan.note == (
        "기준 2026-10-13 04:00 ~ 2026-10-13 07:00 · 비교 2026-10-13 07:00 ~ 2026-10-13 10:00"
        "(요청 기간과 바로 앞 같은 길이 기간을 비교) · 진행 중인 시간대는 시 단위 통계라 뺐습니다"
    )


def test_two_period_compare_widens_the_minute_period() -> None:
    """기간 2개 중 분 단위가 있으면 그 기간만 정시 구간으로 넓힌다(V-5 교정)."""
    plan = aq.plan_compare_window(_qt("web01 지난주 대비 최근 90분 처리량"))
    assert plan.mode == "window", plan.note
    assert plan.args == {
        "current_start": "2026-10-13T08:00:00+09:00", "current_end": "2026-10-13T10:00:00+09:00",
        "baseline_start": "2026-10-05T00:00:00+09:00",
        "baseline_end": "2026-10-12T00:00:00+09:00",
    }
    assert plan.note.endswith("(시 단위 통계라 분 단위 기간을 정시 구간으로 넓혀 비교)")


def test_two_period_compare_overlapping_after_widening_names_the_overlap() -> None:
    """「최근 2시간 대비 최근 30분」 — 넓히면 [08,10) ⊃ [09,10) 겹침 → 겹침 사유로 되묻는다."""
    plan = aq.plan_compare_window(_qt("web01 최근 2시간 대비 최근 30분 처리량"))
    assert plan.mode == "ask" and plan.args == {}
    assert plan.note == (
        f"{aq.COMPARE_OVERLAP_NOTE}(2026-10-13 08:00 ~ 2026-10-13 10:00 · "
        "2026-10-13 09:00 ~ 2026-10-13 10:00)"
    )


def test_minute_period_is_rolling_never_in_progress() -> None:
    """분 기간은 늘 롤링이다 — 진행 중 분기의 `shift_unit(start, unit, -1)`(한 단위 앞)에 분이
    들어가지 않는다(끝 = 기준 시각의 분 경계 ≤ 기준 시각 · 완결성 rolling)."""
    qt = _qt("web01 최근 30분 대비 응답시간")
    (period,) = aq._period_list(qt) or []
    assert (period.unit, period.kind) == ("minute", "rolling")


# ── ④ 폴스타 알람·통계 경계 · 검증기 ───────────────────────────────────────────

def test_alarm_ctime_literals_keep_the_minute_boundary() -> None:
    qt = _qt("최근 10분 알람")
    assert qt.event is not None
    assert alarm_ts_bounds(qt.event) == ("2026-10-13 10:27:00", "2026-10-13 10:37:00")


def test_widened_metric_stat_bounds_equal_last_hour() -> None:
    """통계 경계는 해석기가 넓힌 시 경계 그대로 — 「최근 1시간」과 같은 리터럴(변경 0)."""
    widened = stat_bounds(_qt("최근 30분 CPU 사용률").metric)  # type: ignore[arg-type]
    hour = stat_bounds(_qt("최근 1시간 CPU 사용률").metric)  # type: ignore[arg-type]
    assert widened == hour
    assert widened is not None
    assert (widened.table, widened.lo, widened.hi) == ("cmm_metric_stat_h", "2026101309",
                                                      "2026101310")


def _alarm_sql(lo: str, hi: str, *, ts: bool = True) -> str:
    lit = "TIMESTAMP " if ts else ""
    return ("SELECT a.id, a.ctime FROM polestar.cmm_alarm a "
            f"WHERE a.ctime >= {lit}'{lo}' AND a.ctime < {lit}'{hi}' LIMIT 100")


@pytest.mark.parametrize("ts", [True, False], ids=["timestamp", "string"])
def test_validator_accepts_minute_boundary_alarm_sql(ts: bool) -> None:
    qt = _qt("최근 10분 알람")
    sql = _alarm_sql("2026-10-13 10:27:00", "2026-10-13 10:37:00", ts=ts)
    assert check_time_conditions(sql, qt) == []
    assert qt.event is not None and sql_applies_period(sql, qt.event)


def test_validator_rejects_alarm_sql_cut_to_the_hour() -> None:
    """정시로 깎은 끝(10:00)은 해석한 분 구간과 다르다 — 재생성 사유로 돌린다."""
    qt = _qt("최근 10분 알람")
    errors = check_time_conditions(_alarm_sql("2026-10-13 10:27:00", "2026-10-13 10:00:00"), qt)
    assert errors and "2026-10-13 10:37:00" in errors[0]


# ── ⑤ 종단 — 실제 그래프 · 2단 ──────────────────────────────────────────────────

def _at_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.import_module("src.nodes.input_parser"), "_now", lambda: NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("db_ids", [DB_SINGLE, DB_MULTI], ids=["single", "multi"])
async def test_e2e_recent_30min_cpu_widens_with_period_notice(
    build_env, monkeypatch, db_ids,  # noqa: F811
):
    env, _ = build_env("t2", db_ids)
    _at_now(monkeypatch)
    out = await env.run("최근 30분 CPU 사용률", f"m159-cpu-{len(db_ids)}")
    qt = _tr(out)
    assert qt.clarify is None and qt.metric is not None
    assert NOTE_MINUTE_WIDENED in qt.metric.notes
    assert len(env.executed) == len(db_ids)
    for _db, sql in env.executed:
        assert "cmm_metric_stat_h" in sql
        assert _STAT_RE.search(sql).groups() == ("2026101309", "2026101310")  # type: ignore[union-attr]
    final = out.get("final_response") or ""
    assert final.count(CPU_PERIOD_LINE) == 1, final


@pytest.mark.asyncio
@pytest.mark.parametrize("alarm_det", [False, True], ids=["llm_sql", "deterministic"])
async def test_e2e_recent_10min_alarm_uses_minute_ctime_bounds(
    build_env, monkeypatch, alarm_det,  # noqa: F811
):
    env, _ = build_env("t2", DB_SINGLE, alarm_det=alarm_det)
    _at_now(monkeypatch)
    out = await env.run("최근 10분 알람", f"m159-alarm-{alarm_det}")
    assert _tr(out).clarify is None
    assert len(env.llm.sql_humans()) == (0 if alarm_det else 1), "검증기 반려(재생성) 0"
    ((_db, sql),) = env.executed
    assert _TS_RE.search(sql).groups() == ("2026-10-13 10:27:00", "2026-10-13 10:37:00")  # type: ignore[union-attr]
    assert check_time_conditions(sql, _tr(out)) == []
    final = out.get("final_response") or ""
    assert "[조회 기간] 2026-10-13 10:27 ~ 2026-10-13 10:37 (「최근 10분」)" in final, final
    assert WIDENED_PHRASE not in final


@pytest.mark.asyncio
async def test_e2e_recent_30min_response_time_reaches_apm_with_30min_window(
    build_env, monkeypatch,  # noqa: F811
):
    import src.graph as graph_module

    real_build = graph_module.build_graph

    async def _planner(state: Any, **kw: Any) -> dict:
        return {"task_plan": [{
            "task_id": "t1", "agent": "apm_query", "sub_query": state["user_query"],
            "views": ["apm.app_health"], "depends_on": [], "input_from": [], "order": 1,
            "status": "pending",
        }], "is_composite": False, "current_node": "intent_planner"}

    def _build(cfg: Any, **kw: Any) -> Any:
        monkeypatch.setattr(graph_module, "intent_planner", _planner)
        cfg.dbhub.source_endpoints = {"apm": "http://127.0.0.1:9/sse"}
        return real_build(cfg, **kw)

    monkeypatch.setattr(graph_module, "build_graph", _build)
    env, _ = build_env("t2", DB_SINGLE)
    _at_now(monkeypatch)
    gw = _Gateway({"apm_app_health": lambda a: _env(
        "apm_app_health", [{"tps": 1.0}], window={"start": "x", "end": "y", "minutes": 30})})
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    parse = env.llm._respond

    def _with_host(messages: Any) -> AIMessage:  # 입력 파서가 hostname 필터를 뽑았다고 본다
        msg = parse(messages)
        if env.llm.calls[-1][0] == "input_parser":
            data = json.loads(str(msg.content))
            data["filter_conditions"] = [{"field": "hostname", "op": "=", "value": "web01"}]
            return AIMessage(content=json.dumps(data, ensure_ascii=False))
        return msg

    monkeypatch.setattr(env.llm, "_respond", _with_host)
    out = await env.run("web01 최근 30분 응답시간", "m159-apm")
    assert _tr(out).clarify is None and out.get("clarification") is None
    ((tool, args),) = gw.calls
    assert tool == "apm_app_health" and args["hostname"] == "web01"
    assert args["lookback_minutes"] == 30 and args.get("reference_time") is None
    assert env.executed == [] and env.llm.sql_humans() == []
    final = out.get("final_response") or ""
    assert "[조회 기간]" not in final and WIDENED_PHRASE not in final, final
    assert re.search(r"web01", final), final
