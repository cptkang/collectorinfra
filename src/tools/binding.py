"""LangChain 도구 바인딩 계층 (Plan 67 Phase S1 §4.2).

순수 함수 계층(catalog·lexicon·interpretation·schema_probe·metrics·validation)을 LLM이
tool-calling으로 부를 수 있는 LangChain 도구로 감싼다. 의존성(카탈로그·유사어 사전·값
인덱스·DB 클라이언트·스키마)은 ``ToolContext``로 주입하며, 도구 인자에는 LLM이 실제로
결정해야 하는 값만 남긴다.

도구 docstring이 그대로 LLM에게 보이는 설명이므로 한국어로 쓴다. 의존성이 없는 도구는
목록에서 빠진다 — 있으나 마나 한 도구를 노출해 헛호출을 유도하지 않는다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from langchain_core.tools import BaseTool, tool

from src.db.interface import DBClient
from src.nodes.semantic_compiler import render_catalog
from src.tools.catalog import check_smq_coverage, search_catalog
from src.tools.interpretation import resolve_limit, resolve_time_range
from src.tools.lexicon import SemanticFn, lookup_synonym, search_value_index
from src.tools.metrics import classify_metric_field
from src.tools.schema_probe import DEFAULT_SAMPLE_LIMIT, RowMasker, get_sample_data, get_table_schema
from src.tools.validation import validate_sql_draft

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """도구가 쓰는 주입 의존성 묶음.

    비어 있는 항목은 해당 도구를 목록에서 제외하는 신호다(예: db_client가 없으면
    실 스키마·샘플 조회 도구를 노출하지 않는다).
    """

    semantic_model: Optional[dict] = None
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    synonym_meta: dict[str, dict] = field(default_factory=dict)
    value_index: dict[str, list[str]] = field(default_factory=dict)
    schema_info: dict = field(default_factory=dict)
    db_client: Optional[DBClient] = None
    row_masker: Optional[RowMasker] = None
    db_id: Optional[str] = None
    adapter_db_ids: Optional[set[str]] = None
    db_engine: str = "postgresql"
    user_query: str = ""
    default_limit: int = 100
    # 임베딩 의미 검색 함수 — 설정이 켜졌을 때만 주입한다(기본 미주입 = 임베딩 단계 생략).
    semantic_fn: Optional[SemanticFn] = None
    # 유사어 퍼지 매칭 확정 임계
    synonym_min_score: float = 0.85
    # 값 인덱스 유연 매칭 사용 여부
    value_fuzzy: bool = False


def _dump(payload: Any) -> str:
    """도구 반환값을 LLM이 읽을 JSON 문자열로 직렬화한다."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _error(message: str) -> str:
    """도구 실패를 사유와 함께 노출한다(침묵 폴백 금지)."""
    return _dump({"error": message})


def build_query_tools(context: ToolContext) -> list[BaseTool]:
    """주입된 의존성으로 쿼리 조립용 도구 목록을 만든다.

    Args:
        context: 카탈로그·사전·스키마·DB 클라이언트 등 주입 의존성

    Returns:
        LangChain 도구 목록(의존성이 없는 도구는 제외)
    """
    tools: list[BaseTool] = []

    if context.semantic_model:
        @tool("search_catalog")
        def search_catalog_tool(term: str) -> str:
            """질의 용어와 관련된 선택 가능 항목(dimension/measure/알람 엔터티)을 검색한다.

            term을 비우면 선택 가능한 전체 카탈로그를 보여준다. 여기 없는 컬럼은 선택할 수
            없으므로, 컬럼을 고르기 전에 반드시 이 도구로 실제 이름을 확인하라.
            """
            model = context.semantic_model or {}
            if not term or not term.strip():
                return render_catalog(model)
            return _dump(search_catalog(term, model))

        tools.append(search_catalog_tool)

        @tool("check_smq_coverage")
        def check_smq_coverage_tool(smq: dict) -> str:
            """지금까지 누적한 선택(SMQ)이 결정적으로 조립 가능한지 판정한다.

            covered=false면 reason이 어떤 항목이 조립 불가인지 알려준다 — 그 항목을 카탈로그
            안의 다른 선택으로 바꾸거나, 불가하면 그대로 보고하라(임의로 지어내지 말 것).
            """
            return _dump(
                check_smq_coverage(
                    smq, context.semantic_model or {}, value_index=context.value_index or None
                )
            )

        tools.append(check_smq_coverage_tool)

    if context.synonyms:
        @tool("lookup_synonym")
        def lookup_synonym_tool(term: str) -> str:
            """사용자 표현(예: "사용률")에 대응하는 컬럼 후보를 유사어 사전에서 찾는다.

            정확 일치 → 유연 근사 순으로 찾고, 후보마다 근거 단계(stage)와 점수를 함께
            돌려준다. 후보가 비어 있으면 그 표현은 등록된 유사어로 해소되지 않는다는 뜻이다.
            """
            return _dump(
                lookup_synonym(
                    term,
                    context.synonyms,
                    min_score=context.synonym_min_score,
                    semantic_fn=context.semantic_fn,
                    meta=context.synonym_meta,
                )
            )

        tools.append(lookup_synonym_tool)

    if context.value_index:
        @tool("search_value_index")
        def search_value_index_tool(keywords: list[str]) -> str:
            """필터 값 후보를 실측 값 목록에서 검색한다(존재하는 값만 반환).

            필터 리터럴을 쓰기 전에 이 도구로 실제 존재하는 값인지 확인하라. 반환되지 않은
            값은 DB에 없는 값이므로 필터로 쓰면 결과가 0건이 된다.
            """
            return _dump(
                search_value_index(keywords, context.value_index, fuzzy=context.value_fuzzy)
            )

        tools.append(search_value_index_tool)

    @tool("resolve_time_range")
    def resolve_time_range_tool(query: str) -> str:
        """질의의 기간 표현("지난 3개월", "2026년 6월")을 통계 월 범위로 해석한다.

        resolved=false면 질의에 기간 표현이 없다는 뜻이다(기간 필터 없이 전 기간 대상).
        기간은 직접 계산하지 말고 이 도구의 결과를 쓰라.
        """
        return _dump(resolve_time_range(query or context.user_query))

    tools.append(resolve_time_range_tool)

    @tool("resolve_limit")
    def resolve_limit_tool(query: str) -> str:
        """질의의 건수 표현("상위 10", "100건", "전체")을 반영한 행 제한 값을 반환한다."""
        return _dump({
            "limit": resolve_limit(
                query or context.user_query, default_limit=context.default_limit
            )
        })

    tools.append(resolve_limit_tool)

    @tool("classify_metric_field")
    def classify_metric_field_tool(field: str) -> str:
        """필드 표현("CPU 평균")이 사용률 지표인지 판정하고 조립 정보를 반환한다.

        지표가 아니면 is_metric=false다. source가 generic이면 대상 DB의 분류 규칙이 없어
        표면어로만 판정한 것이므로, 리소스·집계·값 컬럼은 다른 도구로 확인해야 한다.
        """
        classified = classify_metric_field(
            field, db_id=context.db_id, adapter_db_ids=context.adapter_db_ids
        )
        if classified is None:
            return _dump({"is_metric": False, "field": field})
        return _dump({"is_metric": True, **classified})

    tools.append(classify_metric_field_tool)

    if context.db_client is not None:
        @tool("get_table_schema")
        async def get_table_schema_tool(table: str) -> str:
            """테이블의 실제 컬럼 구조(이름·타입·NULL 허용)를 DB에서 조회한다."""
            try:
                return _dump(await get_table_schema(table, context.db_client))
            except Exception as e:  # noqa: BLE001 — 조회 실패 사유를 LLM에 그대로 노출
                logger.warning("get_table_schema 실패(%s): %s", table, e)
                return _error(f"테이블 스키마 조회 실패({table}): {e}")

        tools.append(get_table_schema_tool)

        @tool("get_sample_data")
        async def get_sample_data_tool(table: str) -> str:
            """테이블의 샘플 행을 조회해 값의 실제 모양을 확인한다(민감 값은 마스킹)."""
            try:
                rows = await get_sample_data(
                    table,
                    context.db_client,
                    limit=DEFAULT_SAMPLE_LIMIT,
                    masker=context.row_masker,
                )
                return _dump(rows)
            except Exception as e:  # noqa: BLE001 — 조회 실패 사유를 LLM에 그대로 노출
                logger.warning("get_sample_data 실패(%s): %s", table, e)
                return _error(f"샘플 데이터 조회 실패({table}): {e}")

        tools.append(get_sample_data_tool)

    if context.schema_info:
        @tool("validate_sql_draft")
        def validate_sql_draft_tool(sql: str) -> str:
            """SQL 초안을 실행 전에 검증한다(문법·안전성·스키마 정합·행 제한).

            valid=false면 errors의 사유를 고쳐 다시 작성하라. SELECT 외의 문장은 항상 거부된다.
            """
            return _dump(
                validate_sql_draft(
                    sql,
                    context.schema_info,
                    db_engine=context.db_engine,
                    user_query=context.user_query,
                    default_limit=context.default_limit,
                    db_id=context.db_id,
                    adapter_db_ids=context.adapter_db_ids,
                )
            )

        tools.append(validate_sql_draft_tool)

    return tools
