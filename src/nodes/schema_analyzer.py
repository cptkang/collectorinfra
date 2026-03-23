"""스키마 분석 노드.

DB 스키마를 조회하고 관련 테이블을 식별한다.
parsed_requirements를 기반으로 필요한 테이블과 컬럼을 필터링한다.
키워드 매핑 실패 시 LLM 기반 테이블 매칭 폴백을 지원한다.

캐시 구조 (SchemaCacheManager 통합):
  1차: 메모리 캐시 (TTL 기반, SchemaCache)
  2차: Redis 캐시 (fingerprint 기반, SchemaCacheManager)
  2차-fallback: 파일 캐시 (Redis 장애 시)
  3차: DB 전체 조회 (캐시 미스 또는 변경 감지 시)
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from src.config import AppConfig, load_config
from src.db import get_db_client
from src.dbhub.models import SchemaInfo
from src.llm import create_llm
from src.schema_cache.cache_manager import SchemaCacheManager, get_cache_manager
from src.schema_cache.fingerprint import (
    FINGERPRINT_SQL,
    compute_fingerprint,
    compute_fingerprint_from_schema_dict,
)
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.state import AgentState

logger = logging.getLogger(__name__)

# 도메인 -> 예상 테이블명 키워드 매핑 (힌트용)
DOMAIN_TABLE_HINTS: dict[str, list[str]] = {
    "서버": ["server"],
    "CPU": ["cpu", "core", "processor"],
    "메모리": ["memory", "mem", "ram"],
    "디스크": ["disk", "storage", "volume", "partition"],
    "네트워크": ["network", "net", "traffic", "interface"],
}


class SchemaCache:
    """스키마 정보를 TTL 기반으로 캐시한다 (1차 메모리 캐시)."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        """캐시를 초기화한다.

        Args:
            ttl_seconds: 캐시 유효 시간 (기본 5분)
        """
        self._cache: dict[str, SchemaInfo] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl = ttl_seconds

    def get(self, db_id: str = "_default") -> Optional[SchemaInfo]:
        """캐시된 스키마를 반환한다. 만료 시 None.

        Args:
            db_id: DB 식별자 (단일 DB는 "_default")

        Returns:
            캐시된 SchemaInfo 또는 None
        """
        if db_id in self._cache and (
            time.time() - self._timestamps.get(db_id, 0)
        ) < self._ttl:
            return self._cache[db_id]
        return None

    def set(self, schema: SchemaInfo, db_id: str = "_default") -> None:
        """스키마를 캐시에 저장한다.

        Args:
            schema: 저장할 스키마 정보
            db_id: DB 식별자
        """
        self._cache[db_id] = schema
        self._timestamps[db_id] = time.time()

    def invalidate(self, db_id: Optional[str] = None) -> None:
        """캐시를 무효화한다.

        Args:
            db_id: 특정 DB만 무효화 (None이면 전체)
        """
        if db_id is None:
            self._cache.clear()
            self._timestamps.clear()
        else:
            self._cache.pop(db_id, None)
            self._timestamps.pop(db_id, None)


# 모듈 레벨 싱글톤
_schema_cache = SchemaCache()
_persistent_cache: Optional[PersistentSchemaCache] = None


def _get_persistent_cache(config: Optional[AppConfig] = None) -> PersistentSchemaCache:
    """영구 캐시 싱글톤을 반환한다.

    Args:
        config: 앱 설정 (None이면 로드)

    Returns:
        PersistentSchemaCache 인스턴스
    """
    global _persistent_cache
    if _persistent_cache is None:
        if config is None:
            config = load_config()
        _persistent_cache = PersistentSchemaCache(
            cache_dir=config.schema_cache.cache_dir,
            enabled=config.schema_cache.enabled,
        )
    return _persistent_cache


def invalidate_schema_cache(db_id: Optional[str] = None) -> None:
    """스키마 캐시를 무효화한다. 스키마 변경 시 호출.

    Args:
        db_id: 특정 DB만 무효화 (None이면 전체)
    """
    _schema_cache.invalidate(db_id)
    persistent = _get_persistent_cache()
    if db_id is None:
        persistent.invalidate_all()
    else:
        persistent.invalidate(db_id)


async def _fetch_fingerprint(client: Any) -> Optional[str]:
    """DB에서 스키마 fingerprint를 조회한다.

    가벼운 information_schema 쿼리로 테이블명+컬럼수를 조회하여
    해시를 생성한다.

    Args:
        client: DB 클라이언트

    Returns:
        fingerprint 해시 문자열, 실패 시 None
    """
    try:
        result = await client.execute_sql(FINGERPRINT_SQL)
        if result.rows:
            return compute_fingerprint(result.rows)
    except Exception as e:
        logger.warning("fingerprint 조회 실패: %s", e)
    return None


async def _get_schema_with_cache(
    client: Any,
    db_id: str,
    app_config: AppConfig,
) -> tuple[SchemaInfo, dict, dict[str, str], dict[str, list[str]]]:
    """캐시 매니저를 활용하여 스키마를 조회한다.

    1차: 메모리 캐시 확인
    2차: Redis 캐시 (SchemaCacheManager)
    2차-fallback: 파일 캐시 (Redis 장애 시)
    3차: 전체 DB 조회 -> 캐시 갱신

    Args:
        client: DB 클라이언트
        db_id: DB 식별자
        app_config: 앱 설정

    Returns:
        (SchemaInfo, schema_dict, descriptions, synonyms) 튜플
    """
    cache_mgr = get_cache_manager(app_config)
    descriptions: dict[str, str] = {}
    synonyms: dict[str, list[str]] = {}

    # 1차: 메모리 캐시 확인
    full_schema = _schema_cache.get(db_id)
    if full_schema is not None:
        logger.debug("메모리 캐시 히트: db_id=%s", db_id)
        # descriptions/synonyms는 캐시 매니저에서 로드 (글로벌 폴백 포함)
        descriptions = await cache_mgr.get_descriptions(db_id)
        synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
        return full_schema, {}, descriptions, synonyms

    # 2차: Redis/파일 캐시 (SchemaCacheManager)
    current_fingerprint = await _fetch_fingerprint(client)

    if current_fingerprint is not None:
        changed = await cache_mgr.is_changed(db_id, current_fingerprint)
        if not changed:
            cached_schema_dict = await cache_mgr.get_schema(db_id)
            if cached_schema_dict is not None:
                full_schema = _reconstruct_schema_info(cached_schema_dict)
                _schema_cache.set(full_schema, db_id)
                descriptions = await cache_mgr.get_descriptions(db_id)
                synonyms = await cache_mgr.load_synonyms_with_global_fallback(
                    db_id, cached_schema_dict
                )
                logger.info(
                    "캐시 히트: db_id=%s, fingerprint=%s, backend=%s",
                    db_id,
                    current_fingerprint,
                    cache_mgr.backend,
                )
                return full_schema, cached_schema_dict, descriptions, synonyms

    # 3차: DB 전체 조회
    logger.info("캐시 미스, DB 전체 스키마 조회: db_id=%s", db_id)
    full_schema = await client.get_full_schema()
    _schema_cache.set(full_schema, db_id)
    logger.info(
        "스키마 수집 완료: db_id=%s, %d개 테이블",
        db_id,
        len(full_schema.tables),
    )

    return full_schema, {}, descriptions, synonyms


def _reconstruct_schema_info(schema_dict: dict) -> SchemaInfo:
    """캐시된 schema_dict에서 SchemaInfo 객체를 복원한다.

    Args:
        schema_dict: 캐시된 스키마 딕셔너리

    Returns:
        복원된 SchemaInfo 인스턴스
    """
    from src.dbhub.models import ColumnInfo, TableInfo

    schema = SchemaInfo()
    for table_name, table_data in schema_dict.get("tables", {}).items():
        columns = [
            ColumnInfo(
                name=col["name"],
                data_type=col.get("type", ""),
                nullable=col.get("nullable", True),
                is_primary_key=col.get("primary_key", False),
                is_foreign_key=col.get("foreign_key", False),
                references=col.get("references"),
            )
            for col in table_data.get("columns", [])
        ]
        schema.tables[table_name] = TableInfo(
            name=table_name,
            columns=columns,
            row_count_estimate=table_data.get("row_count_estimate"),
        )
    schema.relationships = schema_dict.get("relationships", [])
    return schema


async def schema_analyzer(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """DB 스키마를 분석하여 관련 테이블과 컬럼을 식별한다.

    1. 3단계 캐시를 활용하여 스키마를 조회한다.
    2. parsed_requirements의 query_targets를 기반으로 관련 테이블을 필터링한다.
    3. 키워드 매핑 실패 시 LLM 기반 테이블 매칭 폴백을 사용한다.
    4. 관련 테이블의 샘플 데이터를 수집한다.
    5. 스키마를 영구 캐시에 저장한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - relevant_tables: 관련 테이블 이름 목록
        - schema_info: 스키마 상세 정보 딕셔너리
        - current_node: "schema_analyzer"
        - error_message: 에러 발생 시 메시지, 정상 시 None
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    parsed = state["parsed_requirements"]
    query_targets = parsed.get("query_targets", [])
    db_id = state.get("active_db_id") or "_default"

    try:
        cache_mgr = get_cache_manager(app_config)

        async with get_db_client(app_config) as client:
            # 캐시 매니저를 활용한 스키마 조회
            full_schema, cached_schema_dict, descriptions, synonyms = (
                await _get_schema_with_cache(client, db_id, app_config)
            )

            # 2. 관련 테이블 필터링
            relevant = _filter_relevant_tables(full_schema, query_targets)

            # 2-1. 키워드 매핑으로 찾지 못했으면 LLM 폴백
            if not relevant and query_targets and full_schema.tables:
                relevant = await _llm_filter_tables(
                    llm,
                    list(full_schema.tables.keys()),
                    query_targets,
                    parsed.get("original_query", ""),
                )

            # 3. 스키마를 딕셔너리로 변환
            schema_dict = _schema_to_dict(full_schema, relevant)

            # 4. 샘플 데이터 수집 (관련 테이블만)
            # 캐시에서 로드한 경우 샘플 데이터가 있을 수 있음
            if cached_schema_dict:
                for table_name in relevant:
                    cached_table = cached_schema_dict.get("tables", {}).get(table_name, {})
                    if cached_table.get("sample_data"):
                        schema_dict["tables"][table_name]["sample_data"] = cached_table["sample_data"]

            for table_name in relevant:
                if not schema_dict["tables"].get(table_name, {}).get("sample_data"):
                    try:
                        samples = await client.get_sample_data(table_name, limit=5)
                        schema_dict["tables"][table_name]["sample_data"] = samples
                    except Exception as e:
                        logger.warning(f"샘플 데이터 조회 실패 ({table_name}): {e}")

            # 5. 캐시 매니저를 통해 저장 (Redis + 파일 이중 저장)
            full_schema_dict = _schema_to_dict(
                full_schema, list(full_schema.tables.keys()),
            )
            await cache_mgr.save_schema(db_id, full_schema_dict)

            logger.info(f"관련 테이블: {relevant}")

            # resource_type/eav_name 유사단어 로드
            resource_type_synonyms: dict[str, list[str]] = {}
            eav_name_synonyms: dict[str, list[str]] = {}

            if cache_mgr and cache_mgr.redis_available:
                try:
                    resource_type_synonyms = await cache_mgr._redis_cache.load_resource_type_synonyms()
                    eav_name_synonyms = await cache_mgr._redis_cache.load_eav_name_synonyms()
                except Exception as e:
                    logger.warning("resource_type/eav_name 유사단어 로드 실패: %s", e)

            # 글로벌 유사단어 파일 자동 로드 (첫 실행 시)
            if cache_mgr and cache_mgr.redis_available:
                try:
                    existing_global = await cache_mgr._redis_cache.load_global_synonyms()
                    if not existing_global:
                        # 글로벌 사전이 비어있으면 파일에서 자동 로드
                        from src.schema_cache.synonym_loader import SynonymLoader

                        synonym_file = "config/global_synonyms.yaml"
                        if os.path.exists(synonym_file):
                            loader = SynonymLoader(redis_cache=cache_mgr._redis_cache)
                            result = await loader.load_auto(synonym_file)
                            logger.info("글로벌 유사단어 자동 로드: %s", result.message)
                except Exception as e:
                    logger.warning("글로벌 유사단어 자동 로드 실패: %s", e)

            return {
                "relevant_tables": relevant,
                "schema_info": schema_dict,
                "column_descriptions": descriptions,
                "column_synonyms": synonyms,
                "resource_type_synonyms": resource_type_synonyms,
                "eav_name_synonyms": eav_name_synonyms,
                "current_node": "schema_analyzer",
                "error_message": None,
            }

    except Exception as e:
        logger.error(f"스키마 분석 실패: {e}")
        return {
            "relevant_tables": [],
            "schema_info": {},
            "column_descriptions": {},
            "column_synonyms": {},
            "resource_type_synonyms": {},
            "eav_name_synonyms": {},
            "current_node": "schema_analyzer",
            "error_message": f"DB 스키마 조회 실패: {str(e)}",
        }


async def _llm_filter_tables(
    llm: BaseChatModel,
    all_tables: list[str],
    query_targets: list[str],
    user_query: str,
) -> list[str]:
    """LLM을 사용하여 관련 테이블을 필터링한다.

    키워드 매핑으로 관련 테이블을 찾지 못할 때 폴백으로 사용한다.

    Args:
        llm: LLM 인스턴스
        all_tables: 전체 테이블 이름 목록
        query_targets: 조회 대상 도메인 목록
        user_query: 원본 사용자 질의

    Returns:
        LLM이 선택한 관련 테이블 이름 목록
    """
    prompt = f"""다음 DB 테이블 목록 중에서 사용자 질의와 관련된 테이블만 선택하세요.

테이블 목록: {', '.join(all_tables)}
사용자 질의: {user_query}
조회 대상: {', '.join(query_targets)}

관련 테이블명을 쉼표로 구분하여 응답하세요.
"""
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        selected = [t.strip() for t in response.content.split(",")]
        valid_tables = set(all_tables)
        return sorted(t for t in selected if t in valid_tables)
    except Exception as e:
        logger.warning(f"LLM 테이블 필터링 실패: {e}")
        return []


def _filter_relevant_tables(
    schema: SchemaInfo,
    query_targets: list[str],
) -> list[str]:
    """query_targets를 기반으로 관련 테이블을 필터링한다.

    Args:
        schema: 전체 스키마 정보
        query_targets: 조회 대상 도메인 목록

    Returns:
        관련 테이블 이름 목록
    """
    if not query_targets:
        # 타겟이 없으면 전체 테이블 반환
        return list(schema.tables.keys())

    relevant: set[str] = set()

    # servers 테이블은 항상 포함 (다른 테이블과 JOIN 필요)
    for table_name in schema.tables:
        if "server" in table_name.lower():
            relevant.add(table_name)

    # 도메인 키워드로 테이블 매칭
    for target in query_targets:
        hints = DOMAIN_TABLE_HINTS.get(target, [])
        for table_name in schema.tables:
            for hint in hints:
                if hint in table_name.lower():
                    relevant.add(table_name)

    return sorted(relevant)


def _schema_to_dict(
    schema: SchemaInfo,
    relevant_tables: list[str],
) -> dict:
    """SchemaInfo를 dict로 변환한다 (State에 저장 가능한 형태).

    Args:
        schema: SchemaInfo 인스턴스
        relevant_tables: 포함할 테이블 목록

    Returns:
        스키마 딕셔너리
    """
    tables_dict: dict[str, Any] = {}
    for table_name in relevant_tables:
        if table_name in schema.tables:
            table = schema.tables[table_name]
            tables_dict[table_name] = {
                "columns": [
                    {
                        "name": col.name,
                        "type": col.data_type,
                        "nullable": col.nullable,
                        "primary_key": col.is_primary_key,
                        "foreign_key": col.is_foreign_key,
                        "references": col.references,
                    }
                    for col in table.columns
                ],
                "row_count_estimate": table.row_count_estimate,
                "sample_data": [],
            }

    # 관련 테이블 간의 FK 관계만 필터링
    relevant_set = set(relevant_tables)
    relationships = [
        rel
        for rel in schema.relationships
        if (
            rel["from"].split(".")[0] in relevant_set
            and rel["to"].split(".")[0] in relevant_set
        )
    ]

    return {
        "tables": tables_dict,
        "relationships": relationships,
    }
