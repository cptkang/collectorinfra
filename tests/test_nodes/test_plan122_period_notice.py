"""plans/122 T-8 — 응답 `[조회 기간]` 고지 + 출력 노드·2단 집계기의 기간 소비 배선 (LLM 0 · DB 0).

검증 항목
- 고지 문구(도메인): 명시 기간 · 기본값(지난달) · 모델 해석 · 빈 구간 · 이번 달 제외 ·
  현재 시각까지 · 하루 · 'YYYYMM' 비노출
- 언제 싣는가: 실행 SQL이 해석의 리터럴 경계를 실제로 썼을 때만(기간과 무관한 질의 = 고지 없음) ·
  통계/사건 주체 선택(알람 질의 = 사건 우선)
- 출력 노드: 기준 정보(오늘 · 조회 기간) · 빈 결과 「시간 범위를 넓혀보세요」 · 당월 각주 · 알람
  헤드라인(`period=qt.event`) · 플래그 off 바이트 동일
- 2단: `_build_output_state` 허용목록 경유 · 단일 경로와 같은 문구 · 복합 계획 반복 제거 ·
  병합 경로 · 합성이 떨어뜨린 의무 고지 복원

해석 기준 시각은 2026-09-29(화) 10:00 KST(plans/122 §10.3.1 표의 기준)다.
"""

from __future__ import annotations

import importlib
import re
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessageChunk

from src.db_adapters.polestar.time_period import alarm_ts_bounds, stat_bounds
from src.domain import disclosure as disc
from src.domain.query_time import resolve_query_time
from src.domain.time_spec import KST, TimeResolution

# `src.nodes` 패키지가 같은 이름의 함수를 재노출하므로 모듈은 import_module로 잡는다.
og = importlib.import_module("src.nodes.output_generator")
ra = importlib.import_module("src.orchestration.result_aggregator")

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)
_CFG = SimpleNamespace(text2sql=SimpleNamespace(alarm_deterministic=False), query=None)
_ROWS = [{"hostname": "web01", "cpu_avg": 41.5}, {"hostname": "web02", "cpu_avg": 12.0}]


class _LLM:
    """요약 한 줄을 흘리고 받은 프롬프트를 기록하는 모의 LLM."""

    def __init__(self, text: str = "요약 문장입니다.") -> None:
        self.text = text
        self.prompts: list[str] = []

    def astream(self, messages, config=None, **kwargs):  # noqa: ANN001 - langchain 메시지 목록
        self.prompts.append(str(messages[-1].content))

        async def _gen():
            yield AIMessageChunk(content=self.text)

        return _gen()


#: 규칙이 못 잡는 「3일 전」은 LLM 슬롯으로 해석된다(source=llm) — 슬롯 모양은 T-3 계약.
_SLOTS: dict[str, dict[str, Any]] = {
    "3일 전 CPU 사용률": {"relation": "ago", "n": 3, "unit": "day", "completeness": None,
                       "anchor": None, "start": None, "end": None, "display_grain": "none",
                       "span": "3일 전"},
}


def _qt(text: str, now: datetime = NOW):
    qt = resolve_query_time(text, now, slot=_SLOTS.get(text))
    assert qt.clarify is None, qt.clarify
    return qt


def _stat_sql(res: TimeResolution | None, *, form: str = "range") -> str:
    assert res is not None
    sb = stat_bounds(res)
    assert sb is not None
    cond = sb.where() if form == "range" else f"s.stat_date = '{sb.last}'"
    return (
        f"SELECT s.hostname, AVG(s.avg_value) AS cpu_avg FROM POLESTAR.{sb.table} s "
        f"WHERE {cond} GROUP BY s.hostname"
    )


def _alarm_sql(res: TimeResolution | None) -> str:
    assert res is not None
    bounds = alarm_ts_bounds(res)
    assert bounds is not None
    start, end = bounds
    return (
        "SELECT a.hostname, a.ctime FROM polestar.alarm_hist a "
        f"WHERE a.ctime >= TIMESTAMP '{start}' AND a.ctime < TIMESTAMP '{end}' "
        "ORDER BY a.ctime DESC LIMIT 100"
    )


def _state(query: str, *, sql: str, time_resolution: Any, rows: list[dict] | None = None,
           **extra: Any) -> dict[str, Any]:
    rows = _ROWS if rows is None else rows
    state: dict[str, Any] = {
        "user_query": query,
        "organized_data": {"summary": f"{len(rows)}건 조회", "rows": rows,
                           "column_mapping": None, "is_sufficient": True},
        "parsed_requirements": {"original_query": query, "output_format": "text",
                                "query_targets": ["서버"]},
        "query_results": rows,
        "query_attempts": [{"sql": sql, "success": True, "error": None,
                            "row_count": len(rows), "execution_time_ms": 1.0}],
        "time_resolution": time_resolution,
    }
    state.update(extra)
    return state


def _period_lines(text: str) -> list[str]:
    return [ln for ln in text.split("\n") if ln.startswith(disc.QUERY_PERIOD_HEAD)]


# ── 고지 문구(도메인) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("최근 30일 서버별 CPU 사용률 평균",
         "[조회 기간] 2026-08-30 ~ 2026-09-28 · 일 단위 (「최근 30일」)"),
        ("서버별 CPU 사용률 평균",
         "[조회 기간] 기간 미지정 — 지난달 기준(2026-08-01 ~ 2026-08-31) · 월 단위"),
        ("올해 서버 CPU 사용률",
         "[조회 기간] 2026-01-01 ~ 2026-08-31 · 월 단위 (「올해」) · 이번 달 제외"),
        ("어제 CPU 사용률", "[조회 기간] 2026-09-28 · 일 단위 (「어제」)"),
        ("최근 3시간 CPU 사용률",
         "[조회 기간] 2026-09-29 07:00 ~ 2026-09-29 10:00 · 시간 단위 (「최근 3시간」)"),
        ("6월 CPU 사용률",
         "[조회 기간] 2026-06-01 ~ 2026-06-30 · 월 단위 (「6월」) · "
         "연도 미지정 — 가장 최근 연도로 해석"),
    ],
)
def test_metric_period_text(query: str, expected: str) -> None:
    assert disc.query_period_text(_qt(query).metric) == expected


def test_event_period_text_runs_to_now_without_grain() -> None:
    event = _qt("이번 달 알람 목록").event
    assert event is not None

    assert disc.query_period_text(event, subject="event") == (
        "[조회 기간] 2026-09-01 00:00 ~ 2026-09-29 10:00 (「이번 달」) · 현재 시각까지"
    )


def test_empty_range_on_first_day_of_month() -> None:
    """매월 1일의 「이번 달」 = 완결된 날이 없다(0행이 정답 · §10.3.1)."""
    metric = _qt("이번 달 CPU 사용률", datetime(2026, 10, 1, 9, 0, tzinfo=KST)).metric
    assert metric is not None and metric.is_empty

    assert disc.query_period_text(metric) == (
        "[조회 기간] 2026-10-01 · 일 단위 (「이번 달」) · 완결된 구간이 아직 없습니다"
    )


def test_llm_source_is_disclosed() -> None:
    metric = replace(_qt("최근 30일 CPU 사용률").metric, source="llm")

    assert disc.query_period_text(metric).endswith("· 모델 해석 — 다르면 날짜를 직접 적어 주세요")


@pytest.mark.parametrize(
    "query",
    ["지난달 CPU", "서버별 CPU", "이번 달 CPU", "작년 CPU", "1월부터 3월까지 CPU", "지난주 CPU"],
)
def test_period_text_never_shows_yyyymm(query: str) -> None:
    qt = _qt(query)
    for res, subject in ((qt.metric, "metric"), (qt.event, "event")):
        assert res is not None
        text = disc.query_period_text(res, subject=subject)  # type: ignore[arg-type]
        assert not re.search(r"(?<!\d)20\d{4}(?!\d)", text), text


def test_kind_is_mandatory_and_self_headed() -> None:
    spec = disc.KIND_TABLE[disc.QUERY_PERIOD]
    item = disc.make(disc.QUERY_PERIOD, "[조회 기간] 2026-08-01 ~ 2026-08-31 · 월 단위")
    optional = [disc.make(disc.UNIT_SUSPECT, f"문장 {i}") for i in range(5)]

    assert spec.mandatory and spec.grade == "neutral" and spec.scope == "task"
    assert disc.render_line(item) == item["text"]  # `[안내] [조회 기간]` 겹머리 금지
    assert disc.render_line(optional[0]) == "[안내] 문장 0"  # 다른 kind는 종전 그대로
    assert item in disc.body_lines_for_turn([*optional, item])  # W-9 상한에 밀리지 않는다


# ── 언제 싣는가 — 실행 SQL이 해석의 경계를 썼을 때만 ─────────────────────────


@pytest.mark.asyncio
async def test_explicit_period_with_literal_bounds_is_disclosed_once() -> None:
    """① 명시 기간 + SQL 리터럴 경계 → 고지 1줄 · 구조 필드 · 기준 정보가 같은 표기."""
    qt = _qt("최근 30일 서버별 CPU 사용률 평균")
    llm = _LLM()
    state = _state("최근 30일 서버별 CPU 사용률 평균", sql=_stat_sql(qt.metric),
                   time_resolution=qt.to_state())

    out = await og.output_generator(state, llm=llm, app_config=_CFG)

    line = "[조회 기간] 2026-08-30 ~ 2026-09-28 · 일 단위 (「최근 30일」)"
    assert _period_lines(out["final_response"]) == [line]
    assert "[안내] [조회 기간]" not in out["final_response"]
    assert {"kind": disc.QUERY_PERIOD, "text": line, "source": "turn"} in out["disclosures"]
    prompt = llm.prompts[0]
    assert "- 오늘: 2026-09-29" in prompt
    assert "- 조회 기간: 2026-08-30 ~ 2026-09-28 · 일 단위" in prompt
    assert "20260830" not in out["final_response"]


@pytest.mark.asyncio
async def test_period_free_query_has_no_notice() -> None:
    """② 기간과 무관한 SQL(서버 목록) — 「지난달 기준」을 붙이면 거짓 고지다."""
    qt = _qt("OS가 리눅스인 서버 목록")
    llm = _LLM()
    sql = "SELECT r.hostname FROM POLESTAR.cmm_resource r WHERE r.os_type = 'Linux'"
    state = _state("OS가 리눅스인 서버 목록", sql=sql, time_resolution=qt.to_state(),
                   rows=[{"hostname": "web01", "os_type": "Linux"}])

    out = await og.output_generator(state, llm=llm, app_config=_CFG)

    assert disc.QUERY_PERIOD_HEAD not in out["final_response"]
    assert all(d["kind"] != disc.QUERY_PERIOD for d in out.get("disclosures") or [])
    assert "조회 기간" not in llm.prompts[0]
    assert "- 오늘: 2026-09-29" in llm.prompts[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["range", "equal"])
async def test_default_period_on_stat_sql_says_last_month(form: str) -> None:
    """③ 기간 미지정 + 지난달 리터럴 통계 SQL → 「기간 미지정 — 지난달 기준」."""
    qt = _qt("서버별 CPU 사용률 평균")
    state = _state("서버별 CPU 사용률 평균", sql=_stat_sql(qt.metric, form=form),
                   time_resolution=qt.to_state())

    out = await og.output_generator(state, llm=_LLM(), app_config=_CFG)

    assert _period_lines(out["final_response"]) == [
        "[조회 기간] 기간 미지정 — 지난달 기준(2026-08-01 ~ 2026-08-31) · 월 단위"
    ]


@pytest.mark.asyncio
async def test_alarm_sql_uses_event_period() -> None:
    """④ 알람 SQL(사건 경계) → 사건 문구(현재 시각까지 · 집계 단위 없음)."""
    qt = _qt("이번 달 알람 목록")
    state = _state("이번 달 알람 목록", sql=_alarm_sql(qt.event), time_resolution=qt.to_state(),
                   routing_intent="alarm_query",
                   rows=[{"hostname": "web01", "ctime": "2026-09-10 11:00:00"}])

    out = await og.output_generator(state, llm=_LLM(), app_config=_CFG)

    assert _period_lines(out["final_response"]) == [
        "[조회 기간] 2026-09-01 00:00 ~ 2026-09-29 10:00 (「이번 달」) · 현재 시각까지"
    ]


def test_subject_priority_metric_first_alarm_query_event_first() -> None:
    """같은 경계를 두 주체가 다 맞추면 통계 우선 · 알람 질의면 사건 우선."""
    qt = _qt("지난달 알람 목록")
    sql = _alarm_sql(qt.event)
    base = _state("지난달 알람 목록", sql=sql, time_resolution=qt.to_state())

    metric = og.applied_period(base)
    event = og.applied_period({**base, "routing_intent": "alarm_query"})

    assert metric is not None and metric[1] == "metric"
    assert event is not None and event[1] == "event"


def test_alarm_without_period_has_no_notice() -> None:
    """알람 기간 미지정 = 기간 조건 없음(D-291) — 경계가 없으니 고지도 없다."""
    qt = _qt("알람 목록 최근 발생 순 100건")
    sql = "SELECT a.hostname FROM polestar.alarm_hist a ORDER BY a.ctime DESC LIMIT 100"
    state = _state("알람 목록 최근 발생 순 100건", sql=sql, time_resolution=qt.to_state(),
                   routing_intent="alarm_query")

    assert og.query_period_texts(state) == []


def test_multi_db_executed_sqls_are_read() -> None:
    """3단 멀티 DB — DB별 실행 SQL(`db_executed_sqls`)에서 판정한다."""
    qt = _qt("지난주 CPU 사용률")
    state = {"time_resolution": qt.to_state(),
             "db_executed_sqls": {"polestar_cm_gp": _stat_sql(qt.metric),
                                  "polestar_b0": _stat_sql(qt.metric)}}

    assert og.query_period_texts(state) == [
        "[조회 기간] 2026-09-21 ~ 2026-09-27 · 일 단위 (「지난주」)"
    ]


# ── 출력 노드의 기간 소비처 ──────────────────────────────────────────────────


def test_empty_result_hint_uses_query_time_explicit() -> None:
    parsed = {"query_targets": ["서버"],
              "filter_conditions": [{"field": "cpu", "op": ">", "value": 90}]}
    with_range = {**parsed, "time_range": {"start": "2026-08-01"}}

    hint = "시간 범위를 넓혀보세요"
    assert hint in og._generate_empty_result_response(parsed, period_explicit=True)
    assert "시간 범위를 넓혀보세요" not in og._generate_empty_result_response(
        with_range, period_explicit=False
    )
    # 플래그 off(None) — 종전대로 파서 time_range 유무
    assert "시간 범위를 넓혀보세요" in og._generate_empty_result_response(with_range)
    assert "시간 범위를 넓혀보세요" not in og._generate_empty_result_response(parsed)


_MONTH_TO_DATE_CASES = [
    ("이번 달 CPU 사용률", NOW, True),
    ("9월 CPU 사용률", NOW, True),  # 진행 중인 달 = 「이번 달」과 같게 자른다(D-291)
    # 검증 D4 — 당월에 걸쳐도 「당월 1일부터 어제까지의 일간 통계」가 아니다
    ("최근 3시간 CPU 사용률", NOW, False),
    ("어제 CPU 사용률", NOW, False),
    ("지난주 CPU 사용률", NOW, False),
    ("3일 전 CPU 사용률", NOW, False),
    ("최근 30일 CPU 사용률", NOW, False),
    ("올해 CPU 사용률", NOW, False),
    ("지난달 CPU 사용률", NOW, False),
    ("이번 달 CPU 사용률", datetime(2026, 10, 1, 9, 0, tzinfo=KST), False),  # 빈 구간
]


@pytest.mark.parametrize(("query", "now", "fires"), _MONTH_TO_DATE_CASES)
def test_current_month_note_only_for_month_to_date(query: str, now: datetime, fires: bool) -> None:
    state = {"user_query": query, "query_results": [{"cpu_avg": 41.5}],
             "time_resolution": _qt(query, now).to_state()}

    out = og._append_current_month_partial_note("응답", state)

    assert ("진행 중인 달(" in out) is fires
    if fires:
        assert "진행 중인 달(2026년 9월)" in out


@pytest.mark.parametrize(("query", "now", "fires"), _MONTH_TO_DATE_CASES)
def test_all_null_note_uses_query_time(query: str, now: datetime, fires: bool) -> None:
    """검증 D9 — 전 행 null 안내도 시간 해석(기준 시각 · 「이번 달」 규칙)으로 판정한다."""
    state = {"user_query": query, "parsed_requirements": {"original_query": query},
             "time_resolution": _qt(query, now).to_state()}

    out = og._generate_all_null_response(["cpu_avg"], [{"cpu_avg": None}], state)

    assert ("진행 중인 달이 포함되어" in out) is fires
    if fires:
        assert "조회 기간(2026-09-01 ~ 2026-09-28)에" in out
        assert not re.search(r"(?<!\d)20\d{4}(?!\d)", out)


def test_all_null_note_flag_off_keeps_legacy_text() -> None:
    """플래그 off — 종전 월 투영 문구(「YYYY년 M월~YYYY년 M월」) 그대로."""
    ym = datetime.now(KST).strftime("%Y%m")
    query = f"{ym[:4]}년 {int(ym[4:])}월 CPU 사용률"
    state = {"user_query": query, "parsed_requirements": {"original_query": query}}

    out = og._generate_all_null_response(["cpu_avg"], [{"cpu_avg": None}], state)

    label = og._format_ym(ym)
    assert f"[안내] 조회 기간({label}~{label})에 진행 중인 달이 포함되어 있습니다." in out


def test_form_month_series_anchor_suppresses_period_notice() -> None:
    """검증 D10 — 6칸 월 시리즈 양식 + 기간 미지정: SQL은 6개월인데 「지난달 기준」이면 거짓."""
    qt = _qt("서버별 CPU 사용률 10건만 채워줘")
    sql = ("SELECT s.hostname FROM POLESTAR.t s "
           "WHERE s.stat_date BETWEEN '202603' AND '202608' GROUP BY s.hostname")
    state = _state("서버별 CPU 사용률 10건만 채워줘", sql=sql, time_resolution=qt.to_state())
    anchored = {**state, "form_month_anchor": {"start": "202603", "end": "202608",
                                               "fields": ["M", "M+1"]}}

    assert og.query_period_texts(state) != []  # 앵커가 없으면 종전대로 판정(포함 판정)
    assert og.query_period_texts(anchored) == []
    assert og.applied_period(anchored) is None
    info = og._build_reference_info(anchored)
    assert info["anchor"] == ("202603", "202608") and "period_text" not in info
    merged = {**anchored, "period_sources": [{"executed_sqls": [{"sql": sql}]}]}
    assert og.query_period_texts(merged) == []


def test_span_is_normalized_and_capped() -> None:
    """리뷰 m-7 — LLM 슬롯 스팬의 개행·긴 문장이 `[조회 기간]` 줄을 쪼개거나 늘리지 않는다."""
    metric = _qt("최근 30일 CPU 사용률").metric
    assert metric is not None
    spaced = replace(metric, span="최근\n  30일")
    long = replace(metric, span="가" * 60)

    assert disc.query_period_text(spaced).endswith("(「최근 30일」)")
    text = disc.query_period_text(long)
    assert "\n" not in disc.query_period_text(spaced)
    assert "「" + "가" * (disc.SPAN_MAX_CHARS - 1) + "…」" in text


def test_alarm_headline_passes_event_period_and_hides_yyyymm() -> None:
    qt = _qt("이번 달 알람 목록")
    seen: dict[str, Any] = {}

    def _recognize(user_query: str, parsed_time_range=None, *, period=None):  # noqa: ANN001
        seen["period"] = period
        return SimpleNamespace(mode="history", month_range=("202609", "202609"), group_by=None,
                               type_label=None, severity=None, severity_op="=", unack_only=False)

    cfg = SimpleNamespace(text2sql=SimpleNamespace(alarm_deterministic=True))
    state = {"user_query": "이번 달 알람 목록", "routing_intent": "alarm_query",
             "query_results": [{"a": 1}], "time_resolution": qt.to_state()}
    with patch("src.db_adapters.polestar.assembler.recognize_active_alarm_query", _recognize):
        out = og._prepend_alarm_headline("본문", state, cfg)
        legacy = og._prepend_alarm_headline("본문", {**state, "time_resolution": None}, cfg)

    assert seen["period"] is None  # 마지막 호출(플래그 off)은 종전 그대로 None
    assert out.startswith("**[알람 조회]** 이력 · 2026-09-01 00:00 ~ 2026-09-29 10:00 — 총 1건")
    assert "202609" not in out
    assert legacy.startswith("**[알람 조회]** 이력 · 202609~202609 — 총 1건")  # 종전 바이트


def test_alarm_headline_receives_event_resolution() -> None:
    qt = _qt("어제 알람 목록")
    seen: dict[str, Any] = {}

    def _recognize(user_query: str, parsed_time_range=None, *, period=None):  # noqa: ANN001
        seen["period"] = period
        return None

    cfg = SimpleNamespace(text2sql=SimpleNamespace(alarm_deterministic=True))
    state = {"user_query": "어제 알람 목록", "routing_intent": "alarm_query",
             "time_resolution": qt.to_state()}
    with patch("src.db_adapters.polestar.assembler.recognize_active_alarm_query", _recognize):
        og._prepend_alarm_headline("본문", state, cfg)

    assert seen["period"] == qt.event


# ── ⑤ 플래그 off — 응답 바이트 동일 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_flag_off_response_is_byte_identical() -> None:
    query = "2026년 6월 서버별 CPU 사용률 평균"
    sql = ("SELECT s.hostname, AVG(s.avg_value) FROM POLESTAR.t s "
           "WHERE s.stat_date = '202606' GROUP BY s.hostname")
    absent = _state(query, sql=sql, time_resolution=None)
    absent.pop("time_resolution")
    llm_a, llm_b = _LLM(), _LLM()

    out_none = await og.output_generator(_state(query, sql=sql, time_resolution=None),
                                         llm=llm_a, app_config=_CFG)
    out_absent = await og.output_generator(absent, llm=llm_b, app_config=_CFG)

    assert out_none["final_response"] == out_absent["final_response"]
    assert out_none.get("disclosures") == out_absent.get("disclosures")
    assert disc.QUERY_PERIOD_HEAD not in out_none["final_response"]
    assert llm_a.prompts == llm_b.prompts
    # 종전 기준 정보 — 'YYYYMM' 월 투영을 「YYYY년 M월」로 렌더(D-186)
    assert "- 조회 기간: 2026년 6월 ~ 2026년 6월" in llm_a.prompts[0]


def test_flag_off_reference_info_keeps_legacy_shape() -> None:
    info = og._build_reference_info(
        {"user_query": "2026년 6월 CPU",
         "parsed_requirements": {"original_query": "2026년 6월 CPU"}}
    )

    assert info["period"] == ("202606", "202606")
    assert "period_text" not in info


# ── ⑥ 2단 — `_build_output_state` 허용목록 경유 ───────────────────────────────


def _task(tid: str, sub_query: str, agent: str = "data_query", order: int = 1) -> dict[str, Any]:
    return {"task_id": tid, "agent": agent, "sub_query": sub_query, "order": order}


def _res(sql: str, rows: list[dict] | None = None, **extra: Any) -> dict[str, Any]:
    rows = _ROWS if rows is None else rows
    res: dict[str, Any] = {
        "organized_data": {"summary": f"{len(rows)}건 조회", "rows": rows,
                           "column_mapping": None, "is_sufficient": True},
        "query_results": rows,
        "executed_sqls": [{"db_id": "polestar", "sql": sql}],
    }
    res.update(extra)
    return res


def _turn(query: str, qt_state: dict | None, tasks: list[dict]) -> dict[str, Any]:
    return {
        "user_query": query,
        "parsed_requirements": {"original_query": query, "output_format": "text",
                                "query_targets": ["서버"]},
        "time_resolution": qt_state,
        "task_plan": tasks,
    }


def test_build_output_state_carries_time_resolution() -> None:
    turn_qt = _qt("지난달 CPU").to_state()
    task_qt = _qt("지난주 CPU").to_state()

    from_turn = ra._build_output_state({"time_resolution": turn_qt}, _task("t1", "q"), {})
    from_task = ra._build_output_state(
        {"time_resolution": turn_qt}, _task("t1", "q"), {"time_resolution": task_qt}
    )
    off = ra._build_output_state({"time_resolution": None}, _task("t1", "q"), {})

    assert from_turn["time_resolution"] == turn_qt
    assert from_task["time_resolution"] == task_qt  # task별 해석 우선(§10.3 「2단 task」)
    assert off["time_resolution"] is None


@pytest.mark.asyncio
async def test_tier2_single_task_matches_single_path_text() -> None:
    """2단 출구가 3단 단일 경로와 **같은 문구**를 낸다 — 완전한 state를 직접 주입하지 않는다."""
    query = "최근 30일 서버별 CPU 사용률 평균"
    qt = _qt(query)
    sql = _stat_sql(qt.metric)
    single = await og.output_generator(_state(query, sql=sql, time_resolution=qt.to_state()),
                                       llm=_LLM(), app_config=_CFG)
    task = _task("t1", query)

    f = await ra._finalize_task(task, _res(sql), _turn(query, qt.to_state(), [task]), _LLM(), _CFG)

    assert _period_lines(f["text"]) == _period_lines(single["final_response"]) != []
    kinds = {(d["kind"], d["source"]) for d in f["disclosures"]}
    assert (disc.QUERY_PERIOD, "task:t1") in kinds


@pytest.mark.asyncio
async def test_tier2_alarm_task_uses_event_period() -> None:
    query = "이번 달 알람 목록"
    qt = _qt(query)
    task = _task("t1", query, agent="alarm_query")
    res = _res(_alarm_sql(qt.event), rows=[{"hostname": "web01", "ctime": "2026-09-10"}])

    f = await ra._finalize_task(task, res, _turn(query, qt.to_state(), [task]), _LLM(), _CFG)

    assert _period_lines(f["text"]) == [
        "[조회 기간] 2026-09-01 00:00 ~ 2026-09-29 10:00 (「이번 달」) · 현재 시각까지"
    ]


@pytest.mark.asyncio
async def test_tier2_steps_drop_repeated_period_line() -> None:
    """복합 계획 — 같은 `[조회 기간]` 줄은 한 번만, task별 기간이 다르면 각각."""
    query = "지난달 CPU 사용률과 메모리 사용률"
    qt = _qt(query)
    sql = _stat_sql(qt.metric)
    t1, t2 = _task("t1", "지난달 CPU 사용률"), _task("t2", "지난달 메모리 사용률", order=2)
    state = _turn(query, qt.to_state(), [t1, t2])

    same = await ra._finalize_steps([t1, t2], {"t1": _res(sql), "t2": _res(sql)}, state,
                                    _LLM(), _CFG)

    assert len(_period_lines(same["final_response"])) == 1

    week = _qt("지난주 메모리 사용률")
    differ = await ra._finalize_steps(
        [t1, t2],
        {"t1": _res(sql), "t2": _res(_stat_sql(week.metric), time_resolution=week.to_state())},
        state, _LLM(), _CFG,
    )

    assert _period_lines(differ["final_response"]) == [
        "[조회 기간] 2026-08-01 ~ 2026-08-31 · 월 단위 (「지난달」)",
        "[조회 기간] 2026-09-21 ~ 2026-09-27 · 일 단위 (「지난주」)",
    ]


def test_merge_finalized_drops_repeated_period_line() -> None:
    line = "[조회 기간] 2026-08-01 ~ 2026-08-31 · 월 단위 (「지난달」)"
    finalized = [{"text": f"표1\n\n{line}"}, {"text": f"표2\n\n{line}\n\n[안내] 기타"}]

    merged = ra._merge_finalized(finalized)

    assert merged["final_response"] == f"표1\n\n{line}\n\n표2\n\n[안내] 기타"


@pytest.mark.asyncio
async def test_tier2_merged_path_reads_source_periods() -> None:
    """병합 경로 — 원천 task의 실행 SQL·해석(`period_sources`)으로 판정하고 한 번만 싣는다."""
    query = "지난달 CPU 사용률과 메모리 사용률 서버별"
    qt = _qt(query)
    sql = _stat_sql(qt.metric)
    t1, t2 = _task("t1", "지난달 CPU 사용률"), _task("t2", "지난달 메모리 사용률", order=2)
    rows1 = [{"hostname": "web01", "cpu_avg": 41.5}]
    rows2 = [{"hostname": "web01", "mem_avg": 60.0}]
    results = {"t1": _res(sql, rows1), "t2": _res(sql, rows2)}
    merged_rows = [{"hostname": "web01", "cpu_avg": 41.5, "mem_avg": 60.0}]

    src = ra._merged_source_state([t1, t2], [results["t1"], results["t2"]])
    out = await ra._finalize_merged_path(
        merged_rows, [t1, t2], results, _turn(query, qt.to_state(), [t1, t2]), _LLM(), _CFG,
    )

    assert [p["routing_intent"] for p in src["period_sources"]] == [None, None]
    assert _period_lines(out["final_response"]) == [
        "[조회 기간] 2026-08-01 ~ 2026-08-31 · 월 단위 (「지난달」)"
    ]


def test_synthesis_lost_period_line_is_restored_without_info_prefix() -> None:
    line = "[조회 기간] 2026-08-01 ~ 2026-08-31 · 월 단위 (「지난달」)"
    lost = disc.make(disc.QUERY_PERIOD, line, source="task:t1")

    out = ra._apply_disclosures({"final_response": "합성된 요약", "disclosures": [lost]},
                                {"user_query": "q"})

    assert out["final_response"] == f"합성된 요약\n\n{line}"
