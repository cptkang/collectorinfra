"""조회 결과 그래프 데이터 API (plans/160 A-3 · D-327).

`GET /api/v1/query/{query_id}/chart` — 응답 `chart` 신호가 띄운 「그래프로 보기」 버튼이 부른다.
판정은 신호와 같은 함수(`src/api/result_chart_view.chart_tables`)다.

- `RESULT_VIZ_CHART_ENABLED` off → 404(현행과 같다). 앱 조립은 켜졌을 때만 이 라우터를 등록하고
  (`server.py` — 기동 시 1회), 라우터 의존성도 인증보다 먼저 404를 낸다(직접 조립한 앱 대비).
- 격자 행은 표마다 앞 `GRID_MAX_ROWS`행만 싣고 전체 행 수를 `rows_total`로 알린다. 그래프 데이터는
  전체 행으로 만든다(점 축소는 `build_chart_data`의 시리즈당 상한).
- 소유자 인가는 결과·CSV 라우트와 같다(`_owned_result` — D-262 · 관리자 통과 · 인증 off 통과).
  저장소에 없으면(재기동·축출) 만료 문구로 404 — 복원한 옛 대화에서 흔히 난다(G-6).
- 마스킹은 화면 표·CSV와 같은 `DataMasker`다. 감사는 다운로드 감사 1행(`file_type="chart"`).

용어·값 해석 API(plans/160 B-2 · C-2) — `GET /query/{id}/glossary` · `POST /query/{id}/interpret`.

- `RESULT_VIZ_EXPLAIN_ENABLED` off → 404 — 그래프 보기가 꺼져도 404다(라우터 미등록). 인가·만료
  문구는 그래프 API와 같다.
- 대상은 그래프 패널 격자와 **답 본문 표**다(D-327 부기 2026-10-13 — G-3 개정). 답 표는 코드가
  원본 행으로 렌더한 표라(`output_generator._render_result_table`) 화면이 `GET /query/{id}/tables`의
  원본 행과 칸을 맞춰 링크를 단다. 그래서 그릴 수 없는 표(사양 목록 등)도 해설 대상이다.
- 입력은 표 번호(`index`) · 격자 칼럼 · 격자 행 번호뿐이다 — 자유 텍스트 없음.
  실존하지 않으면 400. 서비스에는 격자 행(`ResultTable.rows` — 마스킹·평탄)만 넘긴다.
- 해설 로직은 `src/semantic/result_explainer`(사전 → 캐시 → LLM · 판정 어휘 가드)다.
- 감사는 요청마다 1행(`event="result_explain"`) — 대상 칼럼·행만, 셀 값은 sha256 해시만 남긴다.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.api.dependencies import require_user
from src.api.result_chart_view import ResultTable, chart_tables, result_tables
from src.api.routes.query import _audit_file_download, _owned_result
from src.domain.audit import AuditLogEntry
from src.domain.result_chart import build_chart_data
from src.semantic.result_explainer import explain_term, interpret_value
from src.semantic.result_glossary import DescriptionProvider, result_source

logger = logging.getLogger(__name__)


def _require_chart_enabled(request: Request) -> None:
    """그래프 보기가 꺼져 있으면 인증·검증보다 먼저 404 — 라우트가 없는 것과 같게 보인다."""
    if not request.app.state.config.result_viz.chart_enabled:
        raise HTTPException(status_code=404, detail=DISABLED_MESSAGE)


router = APIRouter(dependencies=[Depends(_require_chart_enabled)])

#: 저장소에 결과가 없을 때(재기동·축출) — 화면 패널이 그대로 보인다(G-6).
EXPIRED_MESSAGE = "결과가 만료되었습니다 — 같은 질문을 다시 실행해 주세요"
NO_ROWS_MESSAGE = "그래프로 볼 조회 결과가 없습니다."
NO_CHART_MESSAGE = "그래프로 그릴 수 있는 표가 없습니다."
DISABLED_MESSAGE = "그래프 보기가 꺼져 있습니다."
EXPLAIN_DISABLED_MESSAGE = "용어·값 해석이 꺼져 있습니다."
NO_TABLE_MESSAGE = "결과에 없는 표입니다."
NO_COLUMN_MESSAGE = "결과에 없는 칼럼입니다."
NO_ROW_MESSAGE = "결과에 없는 행입니다."
#: 칼럼명 길이 상한(자) — 결과 칼럼명이 이보다 길 일은 없다.
COLUMN_MAX_CHARS = 200
#: 표마다 격자에 싣는 행 상한 — 값 해석 API의 행 번호도 이 범위 안이다.
GRID_MAX_ROWS = 1000


@router.get("/query/{query_id}/chart")
async def get_result_chart(
    request: Request,
    query_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """조회 결과의 그래프 데이터와 격자 행(질의한 사용자·관리자만).

    표마다 판정(`spec`) · 시리즈(`data`) · 격자 칼럼 순서(`columns`) · 격자 행(`rows` — 중첩
    시계열을 편 뒤 마스킹한 행)을 준다. 그릴 수 있는 표만 싣고, `index`는 표 경계로 나눈 원래 표
    번호다(값 해석 API의 `table`이 이 번호를 쓴다). 격자 행 번호는 `rows` 기준이고 앞
    `GRID_MAX_ROWS`행만 싣는다(`rows_total` = 전체 행 수).
    """
    config = request.app.state.config
    stored = _owned_result(request, query_id, current_user, not_found=EXPIRED_MESSAGE)
    rows = stored.get("query_results") or []
    if not rows:
        raise HTTPException(status_code=404, detail=NO_ROWS_MESSAGE)

    found = chart_tables(rows, stored.get("result_tables"), config.security)
    if not found:
        raise HTTPException(status_code=404, detail=NO_CHART_MESSAGE)
    tables = [
        {
            "index": t.index,
            "label": t.label,
            "kind": t.spec.kind,
            "spec": t.spec.to_dict(),
            "data": build_chart_data(t.rows, t.spec),
            "columns": t.columns(),
            "rows": t.rows[:GRID_MAX_ROWS],
            "rows_total": len(t.rows),
        }
        for t in found
    ]
    body = {
        "query_id": query_id,
        "explain_enabled": bool(config.result_viz.explain_enabled),
        "tables": tables,
    }
    await _audit_file_download(
        request, current_user,
        file_name=f"{query_id}.chart", file_type="chart",
        file_size=len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")),
    )
    return body


# ── 용어·값 해석(B-2 · C-2) ─────────────────────────────────────────────


def _require_explain_enabled(request: Request) -> None:
    """용어·값 해석이 꺼져 있으면 404(그래프 보기는 라우터 의존성이 이미 검사했다)."""
    if not request.app.state.config.result_viz.explain_enabled:
        raise HTTPException(status_code=404, detail=EXPLAIN_DISABLED_MESSAGE)


@router.get("/query/{query_id}/tables")
async def get_result_tables(
    request: Request,
    query_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """답 본문 표의 해설 링크용 원본 표 전부(그릴 수 없는 표 포함 · 질의한 사용자·관리자만).

    표마다 원래 표 번호(`index`) · 라벨 · 판정 종류(`kind` — 그릴 수 없으면 None) · 격자 칼럼 ·
    격자 행(앞 `GRID_MAX_ROWS`행) · 전체 행 수를 준다. 행 번호는 그래프 API 격자와 같다.
    """
    _require_explain_enabled(request)
    config = request.app.state.config
    stored = _owned_result(request, query_id, current_user, not_found=EXPIRED_MESSAGE)
    every = result_tables(stored.get("query_results") or [], stored.get("result_tables"),
                          config.security)
    body = {
        "query_id": query_id,
        "tables": [
            {
                "index": t.index,
                "label": t.label,
                "kind": t.spec.kind if t.spec else None,
                "columns": t.columns(),
                "rows": t.rows[:GRID_MAX_ROWS],
                "rows_total": len(t.rows),
            }
            for t in every
        ],
    }
    await _audit_file_download(
        request, current_user,
        file_name=f"{query_id}.tables", file_type="table",
        file_size=len(json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")),
    )
    return body


class InterpretRequest(BaseModel):
    """값 해석 요청 — 표 번호 · 격자 칼럼 · 격자 행 번호만 받는다(자유 텍스트 없음)."""

    model_config = ConfigDict(extra="forbid")

    table: int
    column: str = Field(min_length=1, max_length=COLUMN_MAX_CHARS)
    row: int


def _explain_table(
    request: Request, query_id: str, current_user: dict[str, Any], table: int, column: str
) -> tuple[dict[str, Any], ResultTable]:
    """플래그·인가·실존 검증을 거친 저장 결과와 대상 표(그릴 수 없는 표 포함)."""
    config = request.app.state.config
    if not (config.result_viz.chart_enabled and config.result_viz.explain_enabled):
        raise HTTPException(status_code=404, detail=EXPLAIN_DISABLED_MESSAGE)
    stored = _owned_result(request, query_id, current_user, not_found=EXPIRED_MESSAGE)
    found = result_tables(stored.get("query_results") or [], stored.get("result_tables"),
                          config.security)
    target = next((t for t in found if t.index == table), None)
    if target is None:
        raise HTTPException(status_code=400, detail=NO_TABLE_MESSAGE)
    if column not in target.columns():
        raise HTTPException(status_code=400, detail=NO_COLUMN_MESSAGE)
    return stored, target


def _llm_factory(config: Any) -> Any:
    """워커 평면 LLM 팩토리 — 사전·캐시 적중이면 호출되지 않는다."""

    def make() -> Any:
        from src.llm import create_llm

        return create_llm(config, purpose="answer")

    return make


def _description_provider(config: Any) -> DescriptionProvider | None:
    """스키마 캐시 칼럼 설명 제공자 — 얻지 못하면 None(나머지 사전 원천만 쓴다)."""
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        provider: DescriptionProvider = get_cache_manager(config).get_descriptions
        return provider
    except Exception as e:  # noqa: BLE001 — 캐시 미가용이어도 용어 API는 동작한다
        logger.warning("결과 용어 — 스키마 캐시 관리자 미가용: %s: %s", type(e).__name__, e)
        return None


def _value_hash(value: Any) -> str:
    """감사용 셀 값 해시(원문 미기록)."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


async def _audit_explain(
    request: Request, current_user: dict[str, Any], **extra: Any
) -> None:
    """용어·값 해석 요청 1행을 감사에 남긴다. 실패는 응답을 깨지 않고 경고만 남긴다."""
    audit_service = getattr(request.app.state, "audit_service", None)
    if not audit_service:
        return
    try:
        await audit_service.log(
            AuditLogEntry(
                event="result_explain",
                user_id=current_user.get("sub") if current_user else None,
                client_ip=getattr(request.state, "client_ip", None)
                or (request.client.host if request.client else None),
                request_id=getattr(request.state, "request_id", None),
                extra=extra,
            )
        )
    except Exception as e:  # noqa: BLE001 — 감사 실패가 해설 응답을 막지 않는다
        logger.warning("결과 해설 감사 기록 실패: %s", e)


@router.get("/query/{query_id}/glossary")
async def get_result_glossary(
    request: Request,
    query_id: str,
    table: int = Query(...),
    column: str = Query(..., min_length=1, max_length=COLUMN_MAX_CHARS),
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """격자 머리글(칼럼) 설명 — 사전 적중이면 즉시, 아니면 LLM 생성(「사전 미등재」 표시용)."""
    stored, target = _explain_table(request, query_id, current_user, table, column)
    config = request.app.state.config
    try:
        result = await explain_term(
            column,
            rows=target.rows,
            user_query=str(stored.get("user_query") or ""),
            source=result_source(stored),
            llm_factory=_llm_factory(config),
            owner=stored.get("owner_sub"),
            description_provider=_description_provider(config),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await _audit_explain(
        request, current_user, kind="glossary", query_id=query_id, table=table, column=column,
        source=result.get("source"),
    )
    return {"column": column, **result}


@router.post("/query/{query_id}/interpret")
async def interpret_result_value(
    request: Request,
    query_id: str,
    body: InterpretRequest,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """격자 셀 값 해석 — 코드가 계산한 위치 사실 + LLM 문장(판정·원인 어휘면 계산값만)."""
    stored, target = _explain_table(request, query_id, current_user, body.table, body.column)
    if not 0 <= body.row < min(len(target.rows), GRID_MAX_ROWS):
        raise HTTPException(status_code=400, detail=NO_ROW_MESSAGE)
    config = request.app.state.config
    try:
        result = await interpret_value(
            body.column,
            body.row,
            rows=target.rows,
            user_query=str(stored.get("user_query") or ""),
            llm_factory=_llm_factory(config),
            cache_key=(query_id, str(body.table)),
            source=result_source(stored),
            description_provider=_description_provider(config),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await _audit_explain(
        request, current_user, kind="interpret", query_id=query_id, table=body.table,
        column=body.column, row=body.row,
        value_sha256=_value_hash(target.rows[body.row].get(body.column)),
        source=result.get("source"),
    )
    return result
