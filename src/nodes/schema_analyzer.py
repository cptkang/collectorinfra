"""스키마 분석 노드.

DB 스키마를 조회하고 관련 테이블을 식별한다.
parsed_requirements를 기반으로 LLM이 필요한 테이블과 컬럼을 선택한다.

캐시 구조 (SchemaCacheManager 통합):
  1차: 메모리 캐시 (TTL 기반, SchemaCacheManager._memory_cache)
  2차: Redis 캐시 (fingerprint 기반, SchemaCacheManager)
  2차-fallback: 파일 캐시 (Redis 장애 시)
  3차: DB 전체 조회 (캐시 미스 또는 변경 감지 시)
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from src.config import AppConfig, load_config
from src.db import get_db_client
from src.dbhub.models import SchemaInfo, schema_to_dict
from src.llm import create_llm
from src.schema_cache.cache_manager import SchemaCacheManager, get_cache_manager
from src.state import AgentState

logger = logging.getLogger(__name__)


class _SchemaCacheProxy:
    """호환성 프록시: 기존 _schema_cache 심볼을 사용하는 코드를 위한 래퍼.

    SchemaCacheManager._memory_cache에 위임한다.
    테스트 코드에서 _schema_cache.invalidate()를 호출하는 패턴을 지원한다.
    """

    def invalidate(self, db_id: Optional[str] = None) -> None:
        """메모리 캐시를 무효화한다.

        Args:
            db_id: 특정 DB만 무효화 (None이면 전체)
        """
        try:
            cache_mgr = get_cache_manager()
            cache_mgr.invalidate_memory_cache(db_id)
        except Exception:
            pass  # 캐시 매니저 초기화 전이면 무시


# 모듈 레벨 호환 심볼 (테스트에서 import하여 사용)
_schema_cache = _SchemaCacheProxy()


def invalidate_schema_cache(db_id: Optional[str] = None) -> None:
    """스키마 캐시를 무효화한다. 스키마 변경 시 호출.

    SchemaCacheManager에 위임하여 메모리 + Redis + 파일 캐시를 모두 무효화한다.

    Args:
        db_id: 특정 DB만 무효화 (None이면 전체)
    """
    try:
        import asyncio

        cache_mgr = get_cache_manager()
        cache_mgr.invalidate_memory_cache(db_id)
        # Redis/파일 캐시 무효화는 비동기이므로 동기 함수에서는 메모리만 처리
        # 완전한 무효화가 필요하면 await cache_mgr.invalidate(db_id) 사용
    except Exception:
        pass  # 캐시 매니저 초기화 전이면 무시


def _format_schema_for_analysis(schema_dict: dict) -> str:
    """스키마 딕셔너리를 LLM 분석용 텍스트로 변환한다.

    각 테이블의 컬럼 정보(이름, 타입, PK, FK, nullable)를 나열하여
    LLM이 구조적 패턴을 감지할 수 있도록 한다.

    Args:
        schema_dict: 스키마 딕셔너리 (tables 키 포함)

    Returns:
        LLM 프롬프트에 삽입할 텍스트
    """
    lines: list[str] = []
    tables = schema_dict.get("tables", {})
    for table_name, table_data in tables.items():
        lines.append(f"### {table_name}")
        columns = table_data.get("columns", [])
        for col in columns:
            attrs: list[str] = []
            if col.get("primary_key"):
                attrs.append("PK")
            if col.get("foreign_key"):
                ref = col.get("references", "")
                attrs.append(f"FK->{ref}" if ref else "FK")
            if not col.get("nullable", True):
                attrs.append("NOT NULL")
            attr_str = f" ({', '.join(attrs)})" if attrs else ""
            col_type = col.get("type", "")
            lines.append(f"  - {col['name']}: {col_type}{attr_str}")
        lines.append("")

    # 관계 정보가 있으면 추가
    relationships = schema_dict.get("relationships", [])
    if relationships:
        lines.append("### FK 관계")
        for rel in relationships:
            from_t = rel.get("from", "")
            to_t = rel.get("to", "")
            lines.append(f"  - {from_t} -> {to_t}")
        lines.append("")

    return "\n".join(lines)


def _parse_llm_json(raw_text: str) -> Any:
    """LLM 응답에서 JSON을 추출하여 파싱한다.

    마크다운 코드 블록(```json ... ```)을 자동 제거한다.

    Args:
        raw_text: LLM 응답 원문

    Returns:
        파싱된 Python 객체

    Raises:
        ValueError: JSON 파싱 실패 시
    """
    import json
    import re

    text = raw_text.strip()
    # ```json ... ``` 또는 ``` ... ``` 블록 제거
    md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if md_match:
        text = md_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 응답 JSON 파싱 실패: {exc}") from exc


async def _analyze_db_structure(
    llm: BaseChatModel,
    schema_dict: dict,
) -> Optional[dict]:
    """LLM을 사용하여 DB 스키마의 구조적 패턴을 분석한다.

    EAV, 계층형, JOIN 관계 등 특수 패턴을 감지하고,
    쿼리 가이드를 생성한다.

    Args:
        llm: LLM 인스턴스
        schema_dict: 스키마 딕셔너리

    Returns:
        구조 분석 결과 딕셔너리 또는 None (분석 실패 또는 패턴 없음)
    """
    from src.prompts.structure_analyzer import STRUCTURE_ANALYSIS_PROMPT

    schema_text = _format_schema_for_analysis(schema_dict)
    prompt = STRUCTURE_ANALYSIS_PROMPT + "\n\n## DB 스키마\n\n" + schema_text
    logger.info("LLM 프롬프트 %s",prompt)
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        result = _parse_llm_json(response.content)

        # patterns가 빈 배열이면 특수 구조 없음 -> None 반환
        if not result.get("patterns"):
            logger.info("LLM 구조 분석: 특수 패턴 미감지")
            return None

        logger.info(
            "LLM 구조 분석 완료: %d개 패턴 감지",
            len(result["patterns"]),
        )
        return result

    except ValueError as e:
        logger.warning("구조 분석 LLM 응답 파싱 실패: %s", e)
        return None
    except Exception as e:
        logger.warning("구조 분석 LLM 호출 실패: %s", e)
        return None


def _validate_sample_sql(sql: str) -> bool:
    """샘플 SQL의 안전성을 검증한다.

    SELECT 문만 허용하며, DML/DDL 키워드가 포함되면 거부한다.
    LIMIT 또는 FETCH FIRST 절이 있는지도 확인한다.

    Args:
        sql: 검증할 SQL 문자열

    Returns:
        안전하면 True, 위험하면 False
    """
    sql_upper = sql.strip().upper()

    # SELECT 로 시작해야 함
    if not sql_upper.startswith("SELECT"):
        return False

    # 위험한 키워드 검사
    forbidden = [
        "INSERT ", "UPDATE ", "DELETE ", "DROP ", "CREATE ",
        "ALTER ", "TRUNCATE ", "GRANT ", "REVOKE ", "EXEC ",
        "EXECUTE ", "MERGE ",
    ]
    for kw in forbidden:
        if kw in sql_upper:
            return False

    # LIMIT 또는 FETCH FIRST 절 필수
    has_limit = "LIMIT " in sql_upper or "FETCH FIRST" in sql_upper
    if not has_limit:
        return False

    return True


async def _collect_structure_samples(
    llm: BaseChatModel,
    client: Any,
    schema_dict: dict,
    structure_meta: dict,
) -> dict:
    """LLM이 감지한 구조에 맞는 샘플 데이터를 수집한다.

    LLM에 구조 분석 결과와 스키마를 제공하여 샘플 SQL을 생성하고,
    안전성 검증을 통과한 SQL만 실행한다.

    Args:
        llm: LLM 인스턴스
        client: DB 클라이언트
        schema_dict: 스키마 딕셔너리
        structure_meta: 구조 분석 결과

    Returns:
        샘플 데이터가 추가된 schema_dict
    """
    import json

    from src.prompts.structure_analyzer import SAMPLE_SQL_GENERATION_PROMPT

    schema_text = _format_schema_for_analysis(schema_dict)
    structure_text = json.dumps(structure_meta, ensure_ascii=False, indent=2)

    prompt = (
        SAMPLE_SQL_GENERATION_PROMPT
        + "\n\n## 구조 분석 결과\n\n"
        + structure_text
        + "\n\n## DB 스키마\n\n"
        + schema_text
    )

    samples: dict[str, Any] = {}

    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        sql_list = _parse_llm_json(response.content)

        if not isinstance(sql_list, list):
            logger.warning("샘플 SQL 생성 결과가 배열이 아님")
            return schema_dict

        # 최대 3개만 처리
        for item in sql_list[:3]:
            purpose = item.get("purpose", "")
            sql = item.get("sql", "")

            if not sql or not _validate_sample_sql(sql):
                logger.warning("샘플 SQL 안전성 검증 실패 (skip): %s", purpose)
                continue

            try:
                result = await client.execute_sql(sql)
                if result.rows:
                    samples[purpose] = result.rows
                    logger.debug("샘플 수집 성공: %s (%d행)", purpose, len(result.rows))
            except Exception as e:
                logger.warning("샘플 SQL 실행 실패 (%s): %s", purpose, e)

    except ValueError as e:
        logger.warning("샘플 SQL 생성 LLM 응답 파싱 실패: %s", e)
    except Exception as e:
        logger.warning("샘플 SQL 생성 LLM 호출 실패: %s", e)

    if samples:
        structure_meta["samples"] = samples

    schema_dict["_structure_meta"] = structure_meta
    return schema_dict


def _format_structure_approval_summary(structure_meta: dict) -> str:
    """구조 분석 결과를 사용자가 읽기 쉬운 요약으로 변환한다.

    HITL 승인 요청 시 사용자에게 보여줄 요약 텍스트를 생성한다.

    Args:
        structure_meta: LLM 구조 분석 결과 딕셔너리

    Returns:
        승인 요청용 요약 텍스트
    """
    lines: list[str] = ["DB 구조 분석 결과를 확인해주세요.\n"]
    for pattern in structure_meta.get("patterns", []):
        ptype = pattern.get("type", "unknown")
        if ptype == "eav":
            lines.append(
                f"- EAV 구조: {pattern.get('entity_table', '?')} "
                f"+ {pattern.get('config_table', '?')}"
            )
            join_cond = pattern.get("join_condition")
            value_joins = pattern.get("value_joins")
            if value_joins:
                vj_desc = "; ".join(
                    f"{vj.get('eav_attribute', '?')} -> {vj.get('entity_column', '?')}"
                    for vj in value_joins
                )
                lines.append(f"  조인: 값 기반 브릿지 ({vj_desc})")
            elif join_cond:
                lines.append(f"  조인: {join_cond}")
            else:
                lines.append("  조인: (값 기반 조인 참조)")
        elif ptype == "hierarchy":
            lines.append(
                f"- 계층 구조: {pattern.get('table', '?')} "
                f"(parent: {pattern.get('parent_column', '?')})"
            )
        else:
            lines.append(f"- {ptype}: {pattern.get('description', '?')}")
    if structure_meta.get("query_guide"):
        guide_text = structure_meta["query_guide"][:200]
        lines.append(f"\n쿼리 가이드:\n{guide_text}...")
    lines.append('\n- 승인: "approve" 또는 "승인"')
    lines.append('- 거부: "reject" 또는 "거부"')
    return "\n".join(lines)


def _read_existing_profile_source(profiles_dir: str, db_id: str) -> Optional[str]:
    """기존 프로필 파일의 source 필드를 읽는다.

    YAML 파일 우선, JSON fallback으로 source 값을 반환한다.
    파일이 없거나 읽기 실패 시 None을 반환한다.

    Args:
        profiles_dir: 프로필 디렉토리 경로
        db_id: DB 식별자

    Returns:
        source 필드 값 ("manual", "auto" 등) 또는 None
    """
    import json

    # YAML 시도
    yaml_path = os.path.join(profiles_dir, f"{db_id}.yaml")
    try:
        import yaml

        if os.path.exists(yaml_path):
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data.get("source")
    except ImportError:
        pass
    except Exception:
        pass

    # JSON fallback
    json_path = os.path.join(profiles_dir, f"{db_id}.json")
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data.get("source")
        except Exception:
            pass

    return None


def _load_manual_profile(db_id: str) -> Optional[dict]:
    """수동 프로필(source: manual)을 YAML/JSON 파일에서 로드한다.

    config/db_profiles/{db_id}.yaml 파일을 읽어서 source 필드가
    "manual"이면 내용을 dict로 반환한다.
    YAML이 없으면 JSON fallback({db_id}.json)도 시도한다.

    known_attributes가 객체 리스트([ {name, description, synonyms} ])일 경우,
    다운스트림 호환을 위해 문자열 리스트 버전도 각 패턴에 추가한다:
    - known_attributes: 문자열 리스트 (기존 호환용)
    - known_attributes_detail: 원본 객체 리스트

    Args:
        db_id: DB 식별자

    Returns:
        수동 프로필 딕셔너리 또는 None (파일 없음/읽기 실패/source가 manual이 아님)
    """
    import json

    profiles_dir = os.path.join("config", "db_profiles")
    profile_data: Optional[dict] = None

    # YAML 시도
    yaml_path = os.path.join(profiles_dir, f"{db_id}.yaml")
    try:
        import yaml

        if os.path.exists(yaml_path):
            with open(yaml_path, "r", encoding="utf-8") as f:
                profile_data = yaml.safe_load(f)
    except ImportError:
        pass  # PyYAML 없으면 JSON fallback
    except Exception as e:
        logger.warning("수동 프로필 YAML 읽기 실패 (%s): %s", yaml_path, e)

    # JSON fallback
    if profile_data is None:
        json_path = os.path.join(profiles_dir, f"{db_id}.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    profile_data = json.load(f)
            except Exception as e:
                logger.warning("수동 프로필 JSON 읽기 실패 (%s): %s", json_path, e)

    if not isinstance(profile_data, dict):
        return None

    # source 필드 확인: manual이 아니면 None
    if profile_data.get("source") != "manual":
        return None

    # known_attributes 포맷 호환 처리
    # 객체 리스트를 문자열 리스트로 변환하여 다운스트림 코드 호환성 보장
    for pattern in profile_data.get("patterns", []):
        raw_attrs = pattern.get("known_attributes", [])
        if not raw_attrs:
            continue

        # 첫 번째 요소로 포맷 판별
        if isinstance(raw_attrs[0], dict):
            # 객체 리스트 -> 상세 버전 보존 + 문자열 리스트 생성
            pattern["known_attributes_detail"] = raw_attrs
            pattern["known_attributes"] = [
                attr["name"] for attr in raw_attrs if isinstance(attr, dict) and "name" in attr
            ]
        # 이미 문자열 리스트이면 변환 불필요

    logger.info("수동 프로필 로드: db_id=%s", db_id)
    return profile_data


def _get_eav_companion_tables(db_id: str) -> list[tuple[str, str]]:
    """수동 프로필에서 EAV entity-config 테이블 쌍을 추출한다.

    Args:
        db_id: DB 식별자

    Returns:
        (entity_table, config_table) 튜플 리스트
    """
    profile = _load_manual_profile(db_id)
    if not profile:
        return []
    pairs: list[tuple[str, str]] = []
    for pattern in profile.get("patterns", []):
        if pattern.get("type") == "eav":
            entity = pattern.get("entity_table", "")
            config = pattern.get("config_table", "")
            if entity and config:
                pairs.append((entity, config))
    return pairs


def _supplement_eav_tables(
    relevant: list[str],
    all_tables: list[str],
    db_id: str,
) -> list[str]:
    """EAV entity 테이블이 선택되었으면 config 테이블도 포함시킨다.

    수동 프로필의 EAV 패턴에서 entity-config 쌍을 읽어,
    entity 테이블이 relevant에 있으면 config 테이블을 자동 추가한다.
    테이블명은 스키마 접두사(예: polestar.cmm_resource)를 고려하여 매칭한다.

    Args:
        relevant: LLM이 선택한 관련 테이블 목록
        all_tables: 전체 테이블 목록 (스키마 접두사 포함)
        db_id: DB 식별자

    Returns:
        EAV 동반 테이블이 보충된 테이블 목록
    """
    eav_pairs = _get_eav_companion_tables(db_id)
    if not eav_pairs:
        return relevant

    relevant_lower = {t.lower() for t in relevant}
    # all_tables에서 bare_name → full_name 매핑 구축
    bare_to_full: dict[str, str] = {}
    for full_name in all_tables:
        bare = full_name.rsplit(".", 1)[-1].lower()
        bare_to_full[bare] = full_name

    supplemented = list(relevant)
    for entity_bare, config_bare in eav_pairs:
        # entity 테이블이 relevant에 있는지 확인 (bare name 또는 full name)
        entity_full = bare_to_full.get(entity_bare.lower(), "")
        entity_in_relevant = (
            entity_bare.lower() in relevant_lower
            or entity_full.lower() in relevant_lower
        )
        if not entity_in_relevant:
            continue
        # config 테이블이 아직 relevant에 없으면 추가
        config_full = bare_to_full.get(config_bare.lower(), "")
        config_in_relevant = (
            config_bare.lower() in relevant_lower
            or config_full.lower() in relevant_lower
        )
        if not config_in_relevant and config_full:
            supplemented.append(config_full)
            logger.info(
                "EAV 동반 테이블 자동 추가: %s (entity: %s)",
                config_full,
                entity_full or entity_bare,
            )

    return supplemented


async def _save_structure_profile(
    db_id: str,
    structure_meta: dict,
    cache_mgr: SchemaCacheManager,
) -> None:
    """LLM 분석 결과를 캐시와 YAML 파일에 자동 저장한다.

    Redis 캐시에 구조 분석 결과를 저장하고,
    config/db_profiles/{db_id}.yaml 파일에도 YAML 형식으로 저장한다.
    YAML 라이브러리가 없으면 JSON 형식으로 fallback한다.

    기존 파일의 source가 "manual"이면 덮어쓰기를 방지한다.
    자동 생성 시 source: auto를 YAML에 포함한다.

    Args:
        db_id: DB 식별자
        structure_meta: 구조 분석 결과 딕셔너리
        cache_mgr: 스키마 캐시 매니저
    """
    import json

    # 기존 파일의 source가 manual이면 파일 덮어쓰기 방지
    profiles_dir = os.path.join("config", "db_profiles")
    existing_source = _read_existing_profile_source(profiles_dir, db_id)
    if existing_source == "manual":
        logger.info("manual 프로필 보호: 덮어쓰기 스킵 (db_id=%s)", db_id)
        # Redis 캐시에는 저장 (성능 최적화)
        try:
            await cache_mgr.save_structure_meta(db_id, structure_meta)
        except Exception as e:
            logger.warning("구조 분석 결과 캐시 저장 실패: %s", e)
        return

    # Redis 캐시 저장
    try:
        await cache_mgr.save_structure_meta(db_id, structure_meta)
        logger.info("구조 분석 결과 캐시 저장 완료: db_id=%s", db_id)
    except Exception as e:
        logger.warning("구조 분석 결과 캐시 저장 실패: %s", e)

    # 자동 생성 시 source: auto 포함
    save_data = {**structure_meta, "source": "auto"}

    # YAML/JSON 파일 자동 생성
    os.makedirs(profiles_dir, exist_ok=True)

    try:
        import yaml

        file_path = os.path.join(profiles_dir, f"{db_id}.yaml")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# AUTO-GENERATED by structure analyzer\n")
            f.write(f"# db_id: {db_id}\n\n")
            yaml.dump(
                save_data,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
        logger.info("구조 프로필 YAML 저장: %s", file_path)
    except ImportError:
        # PyYAML 미설치 시 JSON fallback
        file_path = os.path.join(profiles_dir, f"{db_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        logger.info("구조 프로필 JSON 저장 (YAML fallback): %s", file_path)
    except Exception as e:
        logger.warning("구조 프로필 파일 저장 실패: %s", e)


async def _get_schema_with_cache(
    client: Any,
    db_id: str,
    app_config: AppConfig,
) -> tuple[SchemaInfo, dict, bool, dict[str, str], dict[str, list[str]]]:
    """캐시 매니저의 통합 메서드를 활용하여 스키마를 조회한다.

    SchemaCacheManager.get_schema_or_fetch()에 위임하며,
    반환된 schema_dict에서 SchemaInfo를 복원한다.

    Args:
        client: DB 클라이언트
        db_id: DB 식별자
        app_config: 앱 설정

    Returns:
        (SchemaInfo, schema_dict, cache_hit, descriptions, synonyms) 튜플
        - SchemaInfo: 복원된 SchemaInfo 객체
        - schema_dict: 스키마 딕셔너리
        - cache_hit: 캐시 히트 여부 (True이면 save 불필요)
        - descriptions: {table.column: description}
        - synonyms: {table.column: [synonym, ...]}
    """
    cache_mgr = get_cache_manager(app_config)
    schema_dict, cache_hit, descriptions, synonyms = (
        await cache_mgr.get_schema_or_fetch(client, db_id)
    )
    full_schema = _reconstruct_schema_info(schema_dict)
    return full_schema, schema_dict, cache_hit, descriptions, synonyms


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
    2. LLM을 사용하여 query_targets 기반으로 관련 테이블을 선택한다.
    3. 관련 테이블의 샘플 데이터를 수집한다.
    4. 스키마를 영구 캐시에 저장한다.

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

        async with get_db_client(app_config, db_id=db_id if db_id != "_default" else None) as client:
            # 캐시 매니저를 활용한 스키마 조회 (통합 메서드)
            full_schema, full_schema_dict, cache_hit, descriptions, synonyms = (
                await _get_schema_with_cache(client, db_id, app_config)
            )

            # 2. LLM 기반 관련 테이블 선택
            relevant = await _llm_select_relevant_tables(
                llm,
                full_schema,
                query_targets,
                parsed.get("original_query", ""),
            )

            # 2-1. EAV 동반 테이블 자동 보충
            relevant = _supplement_eav_tables(
                relevant,
                list(full_schema.tables.keys()),
                db_id,
            )

            # 3. 스키마를 딕셔너리로 변환 (관련 테이블만 추출)
            schema_dict = schema_to_dict(full_schema, relevant)

            # 4. 샘플 데이터 수집 (관련 테이블만)
            # 캐시에서 로드한 경우 샘플 데이터가 있을 수 있음
            if full_schema_dict:
                for table_name in relevant:
                    cached_table = full_schema_dict.get("tables", {}).get(table_name, {})
                    if cached_table.get("sample_data"):
                        schema_dict["tables"][table_name]["sample_data"] = cached_table["sample_data"]

            for table_name in relevant:
                if not schema_dict["tables"].get(table_name, {}).get("sample_data"):
                    try:
                        samples = await client.get_sample_data(table_name, limit=5)
                        schema_dict["tables"][table_name]["sample_data"] = samples
                    except Exception as e:
                        logger.warning(f"샘플 데이터 조회 실패 ({table_name}): {e}")

            # 구조 분석: 수동 프로필 -> Redis 캐시 -> LLM 분석 -> HITL 승인 -> 자동 저장
            structure_meta: Optional[dict] = None
            manual_profile_loaded = False

            # 1차: 수동 프로필 확인 (Redis 캐시보다 우선)
            manual_profile = _load_manual_profile(db_id)
            if manual_profile is not None:
                # source 필드는 메타데이터이므로 structure_meta에서 제거
                structure_meta = {
                    k: v for k, v in manual_profile.items() if k != "source"
                }
                manual_profile_loaded = True
                # Redis 캐시에도 저장 (성능 최적화)
                try:
                    await cache_mgr.save_structure_meta(db_id, structure_meta)
                except Exception as e:
                    logger.warning(
                        "수동 프로필 Redis 캐시 저장 실패: %s", e
                    )

                # known_attributes_detail → Redis eav_name_synonyms 동기화
                for pattern in structure_meta.get("patterns", []):
                    detail = pattern.get("known_attributes_detail")
                    if detail:
                        try:
                            synced = await cache_mgr.sync_known_attributes_to_eav_synonyms(
                                detail
                            )
                            if synced > 0:
                                logger.info(
                                    "known_attributes → eav_name_synonyms 동기화: %d개 (db_id=%s)",
                                    synced, db_id,
                                )
                        except Exception as e:
                            logger.warning(
                                "known_attributes → eav_name_synonyms 동기화 실패: %s", e
                            )

            # 2차: Redis 캐시 확인
            if structure_meta is None:
                try:
                    cached_structure = await cache_mgr.get_structure_meta(
                        db_id
                    )
                    if cached_structure:
                        structure_meta = cached_structure
                        logger.info("구조 분석 캐시 히트: db_id=%s", db_id)
                except Exception as e:
                    logger.warning("구조 분석 캐시 조회 실패: %s", e)

            # 3차: LLM 분석 (수동 프로필과 캐시 모두 없는 경우)
            if structure_meta is None:
                # HITL 재진입 확인: approval_context에 이미 분석 결과가 있으면 승인된 것
                approval_ctx = state.get("approval_context")
                if (
                    approval_ctx
                    and approval_ctx.get("type") == "structure_analysis"
                    and state.get("approval_action") == "approve"
                ):
                    # 승인됨 -> 저장하고 진행
                    structure_meta = approval_ctx.get("analysis_result")
                    if structure_meta:
                        await _save_structure_profile(
                            db_id, structure_meta, cache_mgr
                        )
                        logger.info(
                            "구조 분석 HITL 승인 -> 프로필 저장: db_id=%s",
                            db_id,
                        )
                else:
                    # 새 분석 수행
                    structure_meta = await _analyze_db_structure(
                        llm, schema_dict
                    )
                    if (
                        structure_meta
                        and app_config.enable_structure_approval
                    ):
                        # HITL 승인 요청: 중간 결과를 함께 반환
                        logger.info(
                            "구조 분석 HITL 승인 요청: db_id=%s", db_id
                        )
                        return {
                            "relevant_tables": relevant,
                            "schema_info": schema_dict,
                            "column_descriptions": descriptions,
                            "column_synonyms": synonyms,
                            "resource_type_synonyms": {},
                            "eav_name_synonyms": {},
                            "awaiting_approval": True,
                            "approval_context": {
                                "type": "structure_analysis",
                                "db_id": db_id,
                                "analysis_result": structure_meta,
                                "summary": _format_structure_approval_summary(
                                    structure_meta
                                ),
                            },
                            "current_node": "schema_analyzer",
                            "error_message": None,
                        }
                    elif structure_meta:
                        # HITL 비활성화: 바로 저장
                        await _save_structure_profile(
                            db_id, structure_meta, cache_mgr
                        )

            if structure_meta:
                schema_dict["_structure_meta"] = structure_meta
                # 수동 프로필은 이미 완전한 정보를 포함하므로 LLM 샘플 수집 스킵
                if not manual_profile_loaded:
                    schema_dict = await _collect_structure_samples(
                        llm, client, schema_dict, structure_meta
                    )

            # 5. 캐시 미스였던 경우에만 저장 (cache_hit=True면 이미 저장됨)
            # get_schema_or_fetch 내부에서 이미 save_schema를 호출하므로
            # 여기서는 추가 저장이 불필요하다.

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


async def _llm_select_relevant_tables(
    llm: BaseChatModel,
    full_schema: SchemaInfo,
    query_targets: list[str],
    user_query: str,
) -> list[str]:
    """LLM을 사용하여 사용자 질의에 관련된 테이블을 선택한다.

    각 테이블의 주요 컬럼 정보와 FK 관계를 프롬프트에 포함하여
    LLM이 정확한 테이블을 선택할 수 있도록 한다.
    query_targets가 비어있으면 LLM 호출 없이 전체 테이블을 반환한다.

    Args:
        llm: LLM 인스턴스
        full_schema: 전체 스키마 정보 (테이블, 컬럼, 관계 포함)
        query_targets: 조회 대상 도메인 목록
        user_query: 원본 사용자 질의

    Returns:
        LLM이 선택한 관련 테이블 이름 목록
    """
    all_tables = list(full_schema.tables.keys())

    if not query_targets:
        return all_tables

    # 테이블별 컬럼 요약 생성
    table_summaries: list[str] = []
    for table_name, table_info in full_schema.tables.items():
        col_names = [col.name for col in table_info.columns]
        col_summary = ", ".join(col_names[:15])
        if len(col_names) > 15:
            col_summary += f" ... (외 {len(col_names) - 15}개)"
        table_summaries.append(f"- {table_name}: [{col_summary}]")

    table_info_text = "\n".join(table_summaries)

    # FK 관계 요약
    relationship_text = ""
    if full_schema.relationships:
        rel_lines = [
            f"- {rel.get('from', '')} -> {rel.get('to', '')}"
            for rel in full_schema.relationships
        ]
        relationship_text = "\n\nFK 관계:\n" + "\n".join(rel_lines)

    prompt = f"""다음 DB 테이블 목록에서 사용자 질의에 필요한 테이블만 선택하세요.

테이블 및 주요 컬럼:
{table_info_text}
{relationship_text}

사용자 질의: {user_query}
조회 대상 도메인: {', '.join(query_targets)}

규칙:
1. 사용자 질의를 처리하는 데 직접 필요한 테이블을 선택하세요.
2. JOIN에 필요한 테이블(FK 관계로 연결된 테이블)도 반드시 포함하세요.
3. 테이블명만 쉼표로 구분하여 응답하세요. 설명은 불필요합니다.
"""
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        selected = [t.strip() for t in response.content.split(",")]
        valid_tables = set(all_tables)
        result = sorted(t for t in selected if t in valid_tables)
        if result:
            return result
        # LLM이 유효한 테이블을 하나도 반환하지 못한 경우 전체 반환
        logger.warning("LLM이 유효한 테이블을 선택하지 못함, 전체 테이블 반환")
        return all_tables
    except Exception as e:
        logger.warning(f"LLM 테이블 선택 실패: {e}, 전체 테이블 반환")
        return all_tables
