"""동의어 값 검색(Value Retrieval) 인덱스 (Plan 61 트랙 B / E5-2 / D-075).

WHERE 리터럴(``resource_type='server.Server'``, EAV ``NAME='Hostname'`` 등)이 사전에
없으면 LLM이 환각한다(Plan 25 관찰). 이를 막기 위해 **안정·유한한 폴스타 값집합**
(distinct ``resource_type``, EAV ``NAME``)을 읽기전용으로 인덱싱해 두고, 질의 키워드로
검색하여 **검증된 실측 리터럴만** 후보로 제시한다.

계층: infrastructure(값 인덱스 구축은 DB 클라이언트·utils.flex_match에 의존).
갱신은 ``SELECT DISTINCT`` 읽기전용만 수행 → D-003(3중 읽기전용 방어) 준수.
활성화는 ``cfg.synonym.value_retrieval`` 플래그로 제어하며, OFF 시 호출부가 미진입한다.

런타임 주입(Plan 61 §12.3-3): ``load_or_build_value_index``가 schema_analyzer에서
state(``column_value_index``)로 인덱스를 적재하고, 검색 결과를 SQL 생성/다중 후보 프롬프트
(트랙 A / E2)에 주입한다(``src/utils/query_gen_common.py::build_value_index_block``).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.utils.flex_match import flex_match_score

logger = logging.getLogger(__name__)

# 인덱스 값 수집 시 컬럼당 상한(폭증 방지 — 안정·유한 값집합 전제)
_DEFAULT_DISTINCT_LIMIT = 1000
# 검색 결과 키당 반환 상한(프롬프트 토큰 폭증 방지, D-051 정신)
_DEFAULT_MAX_PER_KEY = 20


def build_distinct_values_sql(
    table: str,
    column: str,
    engine: str,
    limit: int = _DEFAULT_DISTINCT_LIMIT,
) -> str:
    """엔진별 방언으로 ``SELECT DISTINCT`` 문을 생성한다(읽기전용).

    PostgreSQL은 ``LIMIT n``, DB2는 ``FETCH FIRST n ROWS ONLY``를 사용한다
    (Known Mistakes 2026-06-30 — 멀티 엔진 방언 분기 필수).

    Args:
        table: 대상 테이블(스키마 한정 포함 가능)
        column: distinct 대상 컬럼
        engine: DB 엔진 문자열("postgresql" | "db2" | ...)
        limit: 값 수집 상한

    Returns:
        읽기전용 SELECT DISTINCT SQL 문자열
    """
    eng = (engine or "").lower()
    base = f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL"
    if "db2" in eng:
        return f"{base} FETCH FIRST {limit} ROWS ONLY"
    return f"{base} LIMIT {limit}"


def _extract_value(row: Any) -> Optional[str]:
    """단일 컬럼 결과 행에서 문자열 값을 추출한다(dict/list/스칼라 모두 대응)."""
    if row is None:
        return None
    if isinstance(row, dict):
        for v in row.values():
            if v is not None:
                return str(v)
        return None
    if isinstance(row, (list, tuple)):
        for v in row:
            if v is not None:
                return str(v)
        return None
    return str(row)


async def build_value_index(
    client: Any,
    specs: list[dict],
    limit: int = _DEFAULT_DISTINCT_LIMIT,
) -> dict[str, list[str]]:
    """읽기전용 SELECT DISTINCT로 값 검색 인덱스를 구축한다(E5-2).

    각 spec은 ``{"key": <인덱스키>, "table": ..., "column": ..., "engine": ...}`` 이거나
    미리 조립된 ``{"key": ..., "sql": <SELECT DISTINCT ...>}`` 형태를 지원한다.
    개별 spec 실패는 격리하여 부분 인덱스를 반환한다(한 신호 실패가 전체를 무효화하지
    않도록 — Known Mistakes 2026-06-29 부분 반환 원칙).

    Args:
        client: DB 클라이언트(``execute_sql`` 제공, 읽기전용)
        specs: 인덱싱 대상 명세 리스트
        limit: 컬럼당 값 수집 상한

    Returns:
        {인덱스 키: [distinct 값, ...]} 인덱스
    """
    index: dict[str, list[str]] = {}
    for spec in specs:
        key = spec.get("key")
        if not key:
            continue
        sql = spec.get("sql")
        if not sql:
            table = spec.get("table")
            column = spec.get("column")
            if not table or not column:
                continue
            sql = build_distinct_values_sql(
                table, column, spec.get("engine", ""), limit
            )
        try:
            result = await client.execute_sql(sql)
            rows = getattr(result, "rows", None) or []
            values: list[str] = []
            seen: set[str] = set()
            for row in rows:
                val = _extract_value(row)
                if val is None:
                    continue
                if val not in seen:
                    seen.add(val)
                    values.append(val)
            if values:
                index.setdefault(key, [])
                for v in values:
                    if v not in index[key]:
                        index[key].append(v)
        except Exception as e:
            logger.warning("값 인덱스 구축 실패 (key=%s): %s", key, e)
            continue
    return index


def derive_value_specs(structure_meta: dict | None, engine: str) -> list[dict]:
    """구조 메타의 EAV 패턴에서 값 인덱싱 대상 spec을 도출한다(E5-2).

    EAV config 테이블의 속성명 컬럼(예: ``core_config_prop.name``)을 인덱싱해 EAV
    ``NAME`` 리터럴 환각(Plan 25 유형)을 차단한다. 도출 실패/부재 시 빈 리스트.

    Returns:
        [{"key": "table.column", "table": ..., "column": ..., "engine": ...}, ...]
    """
    specs: list[dict] = []
    seen: set[str] = set()
    for p in (structure_meta or {}).get("patterns", []):
        if p.get("type") != "eav":
            continue
        config_table = p.get("config_table")
        attr_col = p.get("attribute_column")
        if not config_table or not attr_col:
            continue
        key = f"{config_table}.{attr_col}"
        if key in seen:
            continue
        seen.add(key)
        specs.append({"key": key, "table": config_table, "column": attr_col, "engine": engine})
    return specs


async def load_or_build_value_index(
    db_id: str,
    structure_meta: dict | None,
    client: Any,
    engine: str,
    redis_cache: Any = None,
) -> dict[str, list[str]]:
    """값 인덱스를 런타임에 적재한다(load 우선, 없으면 best-effort build+save).

    E5-2 런타임 주입(§12.3-3): 캐시(Redis)에 인덱스가 있으면 그대로 사용하고, 없으면
    구조 메타에서 spec을 도출해 읽기전용으로 1회 구축 후 저장한다. 모든 단계는 실패를
    격리해 빈/부분 인덱스를 반환한다(회귀·부작용 없음, D-003).

    Returns:
        {인덱스 키: [distinct 값, ...]} — 실패 시 빈 dict.
    """
    if redis_cache is not None:
        try:
            cached = await redis_cache.load_column_value_index(db_id)
            if cached:
                return cached
        except Exception as e:  # noqa: BLE001
            logger.warning("값 인덱스 로드 실패(build 시도): %s", e)

    specs = derive_value_specs(structure_meta, engine)
    if not specs or client is None:
        return {}
    try:
        index = await build_value_index(client, specs)
    except Exception as e:  # noqa: BLE001
        logger.warning("값 인덱스 구축 실패: %s", e)
        return {}
    if index and redis_cache is not None:
        try:
            await redis_cache.save_column_value_index(db_id, index)
        except Exception as e:  # noqa: BLE001
            logger.warning("값 인덱스 저장 실패(무시): %s", e)
    return index


def search_value_index(
    index: dict[str, list[str]],
    keywords: list[str],
    *,
    fuzzy: bool = False,
    min_score: float = 0.85,
    max_per_key: int = _DEFAULT_MAX_PER_KEY,
) -> dict[str, list[str]]:
    """질의 키워드로 값 인덱스를 검색하여 매칭 리터럴을 반환한다(E5-2, 순수 함수).

    각 인덱스 키(예: resource_type)의 값 중, 질의 키워드와 일치(정확 부분어) 또는
    (fuzzy=True 시) 유연 근사 매칭되는 값만 추린다. 반환 리터럴은 실측 값이므로
    프롬프트에 주입해도 환각이 아니다.

    Args:
        index: {인덱스 키: [distinct 값, ...]}
        keywords: 질의에서 추출한 키워드/토큰 목록
        fuzzy: 유연 근사 매칭 활성화 여부(플래그, 기본 OFF=정확 부분어만)
        min_score: 유연 매칭 확정 신뢰도 임계
        max_per_key: 키당 반환 상한(토큰 폭증 방지)

    Returns:
        {인덱스 키: [매칭 리터럴, ...]} (매칭이 있는 키만 포함)
    """
    result: dict[str, list[str]] = {}
    if not index or not keywords:
        return result

    kw_low = [k.lower() for k in keywords if k and len(k) >= 2]
    if not kw_low:
        return result

    for key, values in index.items():
        matched: list[str] = []
        for value in values or []:
            if not value:
                continue
            v_low = str(value).lower()
            hit = any(kw in v_low or v_low in kw for kw in kw_low)
            if not hit and fuzzy:
                hit = any(
                    flex_match_score(kw, v_low) >= min_score for kw in kw_low
                )
            if hit and value not in matched:
                matched.append(value)
                if len(matched) >= max_per_key:
                    break
        if matched:
            result[key] = matched
    return result
