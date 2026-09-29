"""문서 검색 시험 API — T-2·T-3 시험 표면 (plans/126 §4.17 · W4).

라우팅 없이 엔진을 부르는 **운영자 전용** 진입이다. 일반 사용자에게 열지 않는다(R-18 —
시험 표면이 운영 표면으로 굳는 것을 막는다). 라우팅이 만들어지면(`plans/125`) 이 라우트의
인가 범위를 재검토한다.

  POST /api/v1/doc/search        질의 → 근거 기반 답변(단발 · 스트리밍 아님)
  GET  /api/v1/doc/collections   문서군·접속 상태(토큰 마스킹) — 시험 패널 드롭다운·진단용

**접속 4종 저장 엔드포인트는 만들지 않는다.** `RAG_*` 9키가 이미 설정 카탈로그의 `rag` 그룹으로
관리자 「환경변수 설정」 탭에서 편집·마스킹·`설정 반영`까지 되기 때문이다(W1) — 여기에 또 만들면
`.env` 쓰기 구현이 둘이 되고 백업·검증 규율이 갈린다(정본 이중화 금지). 시험 패널은 **무엇이
비었는지 보여주고 그 탭으로 보낸다**. 4종을 함께 바꿔야 한다는 규율은 화면 안내와 CLI
(`scripts/rag_conn.py`, 원자적 저장·백업)가 지킨다.

`collection_ids` 는 **필수**다(§4.6) — 미지정 전체 검색은 암묵적 라우팅이라 만들지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.api.dependencies import require_admin_user
from src.doc_qa.service import answer_from_documents
from src.infrastructure.doc_sources import CONNECTION_FIELD_MAP, resolve_collections

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/doc", tags=["doc-search"])

#: 접속 4종 — 「환경변수 설정」 탭에서 편집한다(여기서는 상태 표시·안내만).
_CONN_FIELDS = ("endpoint", "token", "client_key", "retrieval_id")


class DocSearchRequest(BaseModel):
    collection_ids: list[str] = Field(..., min_length=1,
                                      description="조회할 문서군 id(필수 — 미지정 전체 검색 없음)")
    query: str = Field(..., min_length=1, description="질의 문장(용어·조항 번호 보존 권장)")
    include_raw: bool = Field(default=False, description="원시 응답 포함(진단용)")
    search_only: bool = Field(default=False, description="검색만(LLM 호출 0회)")


class CitationOut(BaseModel):
    collection: str
    title: str
    subtitle: str = ""
    url: str = ""
    doc_id: str = ""
    truncated: bool = False


class DocSearchResponse(BaseModel):
    status: str
    reason: str = ""
    answer: str
    citations: list[CitationOut] = []
    diagnostics: dict[str, Any] = {}
    raw: dict[str, Any] | None = None


def _config(request: Request):
    """요청 시점 설정 — reload 로 교체된 값을 읽는다(자산 회전을 재기동 없이 반영)."""
    config = getattr(request.app.state, "config", None)
    if config is None:  # pragma: no cover — 앱 조립이 항상 넣는다
        from src.config import load_config
        config = load_config()
    return config


@router.get("/collections")
async def list_collections(request: Request, _admin: dict = Depends(require_admin_user)):
    """문서군 목록 + 접속 입력 상태. **토큰은 마스킹**해서 내려간다."""
    config = _config(request)
    rag = getattr(config, "rag", None)
    items = []
    for col in resolve_collections(rag):
        masked = col.masked_connection()
        items.append({
            "id": col.id,
            "title": col.title,
            "usable": col.usable,
            "disabled_reason": col.disabled_reason,
            "sensitive": col.sensitive,
            "asset_recorded_at": col.asset_recorded_at,
            "endpoint": masked["endpoint"],
            "token_masked": masked["token"],
            "client_key_masked": masked["client_key"],
            "retrieval_id": masked["retrieval_id"],
            "platform_params": dict(col.platform_params_snapshot),
            "editable": col.id in CONNECTION_FIELD_MAP,
        })
    return {
        "enabled": bool(getattr(rag, "enabled", False)),
        "collections": items,
        "runbook": "docs/32_rag_retrieval_runbook.md",
    }


@router.post("/search", response_model=DocSearchResponse)
async def search_documents(
    body: DocSearchRequest,
    request: Request,
    admin: dict = Depends(require_admin_user),
) -> DocSearchResponse:
    """문서군을 검색해 근거 기반 답변을 만든다(단발 응답).

    라우팅을 거치지 않는다 — 어느 문서군을 볼지는 **요청이 명시**한다.
    """
    config = _config(request)
    llm = None
    if not body.search_only:
        from src.llm import create_llm
        llm = create_llm(config, purpose="answer")

    result = await answer_from_documents(
        body.query,
        body.collection_ids,
        llm=llm,
        app_config=config,
        include_raw=body.include_raw,
        search_only=body.search_only,
    )
    logger.info(
        "문서 검색 API status=%s 컬렉션=%s 근거=%d건 by=%s",
        result.status, body.collection_ids, len(result.citations),
        admin.get("username") or admin.get("sub") or "?",
    )
    return DocSearchResponse(
        status=result.status,
        reason=result.reason,
        answer=result.answer,
        citations=[
            CitationOut(
                collection=c.collection_title, title=c.title, subtitle=c.subtitle,
                url=c.url, doc_id=c.doc_id, truncated=c.truncated,
            )
            for c in result.citations
        ],
        diagnostics=result.diagnostics,
        raw=result.raw,
    )
