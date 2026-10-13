"""은행존 「6개월 추세」가 3개월만 나온 사례의 기간 사슬 고정 (LLM 0 · DB 0).

2026-10-13 폐쇄망 화면 사례다. 질문 「은행존의 ncldboa1 서버의 메모리 사용률을 6개월 추세
데이터를 보여줘.」는 「최근」·「간」이 없어 규칙이 기간으로 잡지 않고 input_parser LLM 슬롯
(source=llm)으로 해석된다. 화면 고지는
`[조회 기간] 2026-04-01 ~ 2026-09-30 · 월 단위 (「6개월」) · 모델 해석 …`였다.

이 파일은 원인 판정의 근거를 고정한다.
- 슬롯 해석은 완결 6개월(202604~202609)이다 — 해석 단계에서 3개월로 줄지 않는다
- 엄격 검증기는 3개월 창(BETWEEN · 현재시각 함수 · IN 목록)을 반려한다
- 고지는 실행 SQL **본문**에 6개월 경계 리터럴이 있을 때만 붙는다 — 주석의 리터럴은 세지 않는다
  (주석만 6개월이고 본문이 3개월이면 고지하지 않는다 · 이번 수정)
"""

from __future__ import annotations

import importlib
from datetime import datetime

import pytest

from src.db_adapters.polestar.time_period import (
    build_period_block,
    sql_applies_period,
    stat_bounds,
)
from src.db_adapters.polestar.validators import check_time_conditions
from src.domain import disclosure as disc
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST

og = importlib.import_module("src.nodes.output_generator")

QUESTION = "은행존의 ncldboa1 서버의 메모리 사용률을 6개월 추세 데이터를 보여줘."
NOW = datetime(2026, 10, 13, 18, 21, tzinfo=KST)
#: 폐쇄망 고지와 같은 해석을 낸 슬롯 모양(T-3 계약)
SLOT = {"relation": "last", "n": 6, "unit": "month", "span": "6개월"}
SCREEN_TEXT = (
    "[조회 기간] 2026-04-01 ~ 2026-09-30 · 월 단위 (「6개월」) · "
    "모델 해석 — 다르면 날짜를 직접 적어 주세요"
)

_SQL = """SELECT svr.name AS server_name, svr.hostname, svr.ipaddress,
       s.stat_date AS stat_month, AVG(s.avg_val) AS mem_avg_pct, MAX(s.max_val) AS mem_max_pct
FROM POLESTAR.cmm_resource r
JOIN POLESTAR.cmm_resource svr ON svr.id = r.platform_resource_id
JOIN POLESTAR.cmm_metric_stat_m s ON s.resource_id = r.id
WHERE r.resource_type = 'server.Memory' AND s.definition_name = 'Utilization'
  AND svr.name = 'ncldboa1' AND {cond}
GROUP BY svr.name, svr.hostname, svr.ipaddress, s.stat_date
ORDER BY s.stat_date
FETCH FIRST 1000 ROWS ONLY"""


@pytest.fixture()
def qt() -> QueryTime:
    q = resolve_query_time(QUESTION, NOW, slot=SLOT)
    assert q.clarify is None
    return q


def _state(qt: QueryTime, sql: str) -> dict:
    return {
        "time_resolution": qt.to_state(),
        "query_attempts": [{"sql": sql, "success": True}],
    }


def test_slot_resolves_six_complete_months(qt: QueryTime) -> None:
    metric = qt.metric
    assert metric is not None and metric.source == "llm" and qt.explicit
    assert metric.grain == "month"
    sb = stat_bounds(metric)
    assert sb is not None
    assert (sb.table, sb.lo, sb.hi, sb.last) == ("cmm_metric_stat_m", "202604", "202610", "202609")
    assert disc.query_period_text(metric) == SCREEN_TEXT


def test_period_block_instructs_six_month_literals(qt: QueryTime) -> None:
    block = build_period_block(qt)
    assert "s.stat_date >= '202604' AND s.stat_date < '202610'" in block
    assert "s.stat_date BETWEEN '202604' AND '202609'" in block


@pytest.mark.parametrize("cond", [
    "s.stat_date >= '202604' AND s.stat_date < '202610'",
    "s.stat_date BETWEEN '202604' AND '202609'",
])
def test_validator_accepts_six_month_window(qt: QueryTime, cond: str) -> None:
    assert check_time_conditions(_SQL.format(cond=cond), qt) == []


@pytest.mark.parametrize("cond", [
    "s.stat_date BETWEEN '202607' AND '202609'",
    "s.stat_date >= TO_CHAR(CURRENT DATE - 3 MONTHS, 'YYYYMM')",
    "s.stat_date IN ('202607', '202608', '202609')",
    "s.stat_date >= '202604' AND s.stat_date < '202610' AND s.stat_date >= '202607'",
])
def test_validator_rejects_three_month_window(qt: QueryTime, cond: str) -> None:
    assert check_time_conditions(_SQL.format(cond=cond), qt)


def test_disclosure_needs_six_month_literals_in_executed_sql(qt: QueryTime) -> None:
    six = _SQL.format(cond="s.stat_date BETWEEN '202604' AND '202609'")
    three = _SQL.format(cond="s.stat_date BETWEEN '202607' AND '202609'")
    assert og.query_period_texts(_state(qt, six)) == [SCREEN_TEXT]
    assert og.query_period_texts(_state(qt, three)) == []


@pytest.mark.parametrize("comment", [
    "-- ncldboa1 메모리 6개월 추세 ('202604' ~ '202609')\n",
    "/* 조회 기간 '202604' ~ '202610' */\n",
])
def test_disclosure_ignores_period_literals_in_comments(qt: QueryTime, comment: str) -> None:
    """주석에만 6개월 경계가 있고 본문은 3개월(검증기가 못 읽는 형태)이면 고지하지 않는다."""
    sql = comment + _SQL.format(cond="SUBSTR(s.stat_date, 1, 6) BETWEEN '202607' AND '202609'")
    assert not sql_applies_period(sql, qt.metric)
    assert og.query_period_texts(_state(qt, sql)) == []
