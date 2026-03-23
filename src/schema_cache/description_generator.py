"""LLM 기반 컬럼 설명 + 유사 단어 생성기.

테이블 단위로 배치 처리하여 1회 LLM 호출로
각 컬럼의 한국어 설명(description)과 유사 단어(synonyms)를 동시 생성한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.prompts.schema_description import (
    DB_DESCRIPTION_SYSTEM_PROMPT,
    DB_DESCRIPTION_USER_TEMPLATE,
    SCHEMA_DESCRIPTION_SYSTEM_PROMPT,
    SCHEMA_DESCRIPTION_USER_TEMPLATE,
)
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)


class DescriptionGenerator:
    """LLM을 사용하여 컬럼별 설명 + 유사 단어를 생성한다."""

    def __init__(self, llm: BaseChatModel) -> None:
        """생성기를 초기화한다.

        Args:
            llm: LLM 인스턴스
        """
        self._llm = llm

    async def generate_db_description(
        self,
        db_id: str,
        schema_dict: dict,
    ) -> Optional[str]:
        """DB 전체의 한국어 설명을 LLM으로 생성한다.

        DB의 테이블 목록과 샘플 데이터를 분석하여
        DB가 어떤 데이터를 보유하고 있는지 간결하게 설명한다.

        Args:
            db_id: DB 식별자
            schema_dict: 스키마 딕셔너리 (tables 키 포함)

        Returns:
            DB 설명 문자열 또는 None (실패 시)
        """
        tables = schema_dict.get("tables", {})
        if not tables:
            return None

        # 테이블 요약 정보 구성
        table_summaries: list[str] = []
        for table_name, table_data in tables.items():
            columns = table_data.get("columns", [])
            col_names = [c["name"] for c in columns]
            sample = table_data.get("sample_data", [])
            summary = f"- {table_name}: 컬럼 [{', '.join(col_names[:10])}]"
            if len(col_names) > 10:
                summary += f" 외 {len(col_names) - 10}개"
            if sample:
                sample_str = json.dumps(sample[:2], ensure_ascii=False)
                if len(sample_str) > 200:
                    sample_str = sample_str[:200] + "..."
                summary += f"\n  샘플: {sample_str}"
            table_summaries.append(summary)

        tables_info = "\n".join(table_summaries)

        user_prompt = DB_DESCRIPTION_USER_TEMPLATE.format(
            db_id=db_id,
            table_count=len(tables),
            tables_info=tables_info,
        )

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=DB_DESCRIPTION_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])

            # LLM 응답에서 설명 텍스트 추출 (JSON이 아닌 순수 텍스트)
            description = response.content.strip()
            # 따옴표로 감싸진 경우 제거
            if description.startswith('"') and description.endswith('"'):
                description = description[1:-1]
            if description.startswith("'") and description.endswith("'"):
                description = description[1:-1]

            logger.info(
                "DB 설명 생성 완료: db_id=%s, description=%s",
                db_id,
                description[:80],
            )
            return description

        except Exception as e:
            logger.error("DB 설명 생성 실패 (db_id=%s): %s", db_id, e)
            return None

    async def generate_for_table(
        self,
        table_name: str,
        columns: list[dict],
        sample_data: list[dict] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """특정 테이블의 컬럼 설명 + 유사 단어를 생성한다.

        Args:
            table_name: 테이블명
            columns: 컬럼 정보 목록
            sample_data: 샘플 데이터 (선택)

        Returns:
            {table.column: {"description": str, "synonyms": list[str]}}
        """
        # 컬럼 정보 포맷
        columns_lines = []
        for col in columns:
            line = f"- {col['name']}: {col.get('type', 'unknown')}"
            if col.get("primary_key"):
                line += " [PK]"
            if col.get("foreign_key"):
                line += f" [FK -> {col.get('references', '?')}]"
            if not col.get("nullable", True):
                line += " NOT NULL"
            if col.get("comment"):
                line += f" -- {col['comment']}"
            columns_lines.append(line)
        columns_info = "\n".join(columns_lines)

        # 샘플 데이터 포맷
        if sample_data:
            sample_text = json.dumps(
                sample_data[:3], ensure_ascii=False, indent=2
            )
        else:
            sample_text = "(샘플 데이터 없음)"

        user_prompt = SCHEMA_DESCRIPTION_USER_TEMPLATE.format(
            table_name=table_name,
            columns_info=columns_info,
            sample_data=sample_text,
        )

        try:
            response = await self._llm.ainvoke([
                SystemMessage(content=SCHEMA_DESCRIPTION_SYSTEM_PROMPT),
                HumanMessage(content=user_prompt),
            ])

            parsed = extract_json_from_response(response.content)
            if parsed is None:
                logger.warning(
                    "LLM 설명 생성 파싱 실패 (table=%s): 응답 길이=%d",
                    table_name,
                    len(response.content),
                )
                return {}

            return parsed

        except Exception as e:
            logger.error("LLM 설명 생성 실패 (table=%s): %s", table_name, e)
            return {}

    async def generate_for_db(
        self,
        schema_dict: dict,
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        """DB 전체 테이블의 설명 + 유사 단어를 생성한다.

        테이블 단위로 순차 처리한다.

        Args:
            schema_dict: 스키마 딕셔너리 (tables 키 포함)

        Returns:
            (descriptions, synonyms) 튜플
            - descriptions: {table.column: description}
            - synonyms: {table.column: [synonym, ...]}
        """
        all_descriptions: dict[str, str] = {}
        all_synonyms: dict[str, list[str]] = {}

        tables = schema_dict.get("tables", {})
        for table_name, table_data in tables.items():
            columns = table_data.get("columns", [])
            sample_data = table_data.get("sample_data", [])

            result = await self.generate_for_table(
                table_name, columns, sample_data
            )

            for col_key, col_info in result.items():
                if isinstance(col_info, dict):
                    desc = col_info.get("description", "")
                    syns = col_info.get("synonyms", [])
                    if desc:
                        all_descriptions[col_key] = desc
                    if syns:
                        all_synonyms[col_key] = syns

        logger.info(
            "DB 전체 설명 생성 완료: descriptions=%d, synonyms=%d",
            len(all_descriptions),
            len(all_synonyms),
        )
        return all_descriptions, all_synonyms

    async def generate_incremental(
        self,
        schema_dict: dict,
        existing_descriptions: dict[str, str],
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        """신규/변경 컬럼만 설명을 생성한다 (incremental).

        기존 설명이 있는 컬럼은 건너뛴다.

        Args:
            schema_dict: 현재 스키마 딕셔너리
            existing_descriptions: 기존 설명 매핑

        Returns:
            (new_descriptions, new_synonyms) 튜플
        """
        new_descriptions: dict[str, str] = {}
        new_synonyms: dict[str, list[str]] = {}

        tables = schema_dict.get("tables", {})
        for table_name, table_data in tables.items():
            columns = table_data.get("columns", [])
            # 설명이 없는 컬럼만 필터링
            new_columns = [
                col for col in columns
                if f"{table_name}.{col['name']}" not in existing_descriptions
            ]

            if not new_columns:
                continue

            sample_data = table_data.get("sample_data", [])
            result = await self.generate_for_table(
                table_name, new_columns, sample_data
            )

            for col_key, col_info in result.items():
                if isinstance(col_info, dict):
                    desc = col_info.get("description", "")
                    syns = col_info.get("synonyms", [])
                    if desc:
                        new_descriptions[col_key] = desc
                    if syns:
                        new_synonyms[col_key] = syns

        logger.info(
            "Incremental 설명 생성 완료: new_descriptions=%d, new_synonyms=%d",
            len(new_descriptions),
            len(new_synonyms),
        )
        return new_descriptions, new_synonyms


