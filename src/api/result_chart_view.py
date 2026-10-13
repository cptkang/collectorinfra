"""조회 결과 그래프 판정 파이프라인 (plans/160 A-2·A-3 · D-327).

응답 신호(`chart_field` — 질의 라우트가 결과 저장 직전에 1회)와 데이터 API
(`GET /query/{id}/chart`)가 **같은 함수**(`chart_tables`)로 판정한다 — 신호가 버튼을 띄웠는데
API가 다른 표를 그리는 어긋남을 막는다.

파이프라인: dict 행만 → `DataMasker.mask_rows`(화면 표·CSV와 같은 마스킹) → 표 경계로 분할
(`split_tables`) → 표마다 중첩 시계열 평탄화(`chart_rows`) → 판정(`detect_chart`). 평탄화로
새로 생긴 칼럼(중첩 안쪽 지표 키)에도 같은 마스킹을 한 번 더 건다 — 마스커는 중첩 값 안을 보지
않는다.

**epoch 시간 x는 서버 벽시계 문자열로 바꾼다.** 판정된 x가 epoch 정수 칼럼(APM `time_ms` 등)이면
격자 행의 그 값을 서버 시간대 벽시계 ISO 문자열로 바꾼 뒤 그 행으로 그래프·값 해석이 돈다.
datetime 칼럼은 벽시계 그대로 그려지므로(`datetime_to_ms`) epoch만 UTC로 보이던 비대칭(같은 답의
문장·표는 서버 시간대 — 9시간 차이)을 없앤다. domain은 시간대를 읽지 않는다(순수 유지).

질의 라우트(`routes/query.py`)는 이 모듈을 import하고 그래프 라우트(`routes/result_viz.py`)는 이
모듈과 질의 라우트를 import한다 — 이 모듈은 어느 라우트도 import하지 않는다(순환 방지).

계층: interface(api) — domain(`result_chart`)·infrastructure(`DataMasker`)만 쓴다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.config import AppConfig, SecurityConfig
from src.domain.result_chart import (
    ChartSpec,
    chart_rows,
    detect_chart,
    parse_datetime,
    split_tables,
    time_value_to_ms,
)
from src.domain.result_refs import RESULT_TABLES_KEY
from src.security.data_masker import DataMasker

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChartTable:
    """그릴 수 있는 표 1개 — 원래 표 번호 · 라벨 · 판정 · 격자 행(평탄·마스킹)."""

    index: int
    label: str
    spec: ChartSpec
    rows: list[dict[str, Any]]

    def columns(self) -> list[str]:
        """격자 칼럼 순서 — 행마다 키가 다를 수 있어 등장 순서 합집합이다(CSV와 같다)."""
        order: dict[str, None] = {}
        for row in self.rows:
            for key in row:
                order.setdefault(key, None)
        return list(order)


def chart_tables(rows: Any, tables: Any, security: SecurityConfig) -> list[ChartTable]:
    """저장된 결과 행 → 그릴 수 있는 표 목록(원래 표 순서). 그릴 표가 없으면 빈 목록."""
    if not isinstance(rows, list):
        return []
    masker = DataMasker(security)
    masked = masker.mask_rows([row for row in rows if isinstance(row, dict)])
    out: list[ChartTable] = []
    for index, (label, part) in enumerate(split_tables(masked, tables)):
        flat = chart_rows(part)
        if flat is not part:
            flat = masker.mask_rows(flat)
        spec = detect_chart(flat)
        if spec is not None:
            flat, spec = _wall_clock_x(flat, spec)
            out.append(ChartTable(index=index, label=label, spec=spec, rows=flat))
    return out


def _epoch_to_wall(value: Any) -> Any:
    """epoch 정수(초·ms) 하나를 서버 시간대 벽시계 ISO 문자열로 — epoch가 아니면 그대로."""
    if isinstance(value, bool) or parse_datetime(value) is not None:
        return value
    ms = time_value_to_ms(value)
    if ms is None:
        return value
    return datetime.fromtimestamp(ms / 1000).isoformat(timespec="seconds")


def _wall_clock_x(
    rows: list[dict[str, Any]], spec: ChartSpec
) -> tuple[list[dict[str, Any]], ChartSpec]:
    """시간 x 칼럼의 epoch 값을 벽시계 문자열로 바꾼 행과 판정 — 재판정이 달라지면 원래 그대로."""
    if spec.x_type != "time":
        return rows, spec
    converted = [
        {**row, spec.x: _epoch_to_wall(row[spec.x])} if spec.x in row else row for row in rows
    ]
    if all(a.get(spec.x) == b.get(spec.x) for a, b in zip(rows, converted, strict=True)):
        return rows, spec
    again = detect_chart(converted)
    if again != spec:
        logger.warning(
            "결과 그래프 — epoch x(%s) 벽시계 변환 뒤 판정이 달라 원값을 쓴다: %s → %s",
            spec.x, spec, again,
        )
        return rows, spec
    return converted, spec


def chart_field(
    config: AppConfig, rows: Any, tables: Any, *, status: str | None = None
) -> dict[str, Any] | None:
    """응답 `chart` 신호 — 그릴 표의 종류·라벨만 싣는다(데이터 없음).

    `RESULT_VIZ_CHART_ENABLED` off면 None(현행과 같다). 되묻기 턴(`status="clarification"` — 기간·존
    역질문)은 조회하지 않은 턴이라 판정하지 않고 None이다(그래프 상태에 직전 턴 행이 남아 있다).
    그릴 표가 없어도 None. 판정 실패는 질의 응답을 깨지 않도록 경고 1줄을 남기고 None이다.
    """
    if not config.result_viz.chart_enabled or status == "clarification":
        return None
    try:
        found = chart_tables(rows, tables, config.security)
    except Exception as e:  # noqa: BLE001 — 신호 실패가 질의 응답을 깨면 안 된다
        logger.warning("결과 그래프 판정 실패 — chart 신호 생략: %s: %s", type(e).__name__, e)
        return None
    if not found:
        return None
    return {
        "kind": found[0].spec.kind,
        "tables": [{"index": t.index, "label": t.label, "kind": t.spec.kind} for t in found],
    }


def chart_carry(chart: dict[str, Any] | None) -> dict[str, Any]:
    """SSE `done`·부분 결과 본문에 펼칠 키 — 신호가 없으면 키를 싣지 않는다(바이트 불변)."""
    return {"chart": chart} if chart else {}


def viz_store_fields(
    chart: dict[str, Any] | None, *, user_query: str | None, tables: Any
) -> dict[str, Any]:
    """결과 저장소에 함께 싣는 키 — 신호 · 질문 원문(값 해석 입력) · 표 경계 원본(API 분할)."""
    return {"chart": chart, "user_query": user_query, "result_tables": tables}


def result_tables_of(state: Mapping[str, Any] | None) -> Any:
    """복합 턴의 표 경계 원본(`conversation_context[RESULT_TABLES_KEY]`) — 없으면 None."""
    ctx = (state or {}).get("conversation_context")
    return ctx.get(RESULT_TABLES_KEY) if isinstance(ctx, Mapping) else None
