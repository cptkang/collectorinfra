"""단일/멀티 DB 경로가 공유하는 프롬프트·재료 조립 블록 (Plan 69 P3-1).

`query_generator`(단일 DB)와 `multi_db_executor`(멀티 DB)가 각자 재구현하던 준-동일
블록(계획서 §1.2)을 여기로 모은다. 두 경로가 같은 빌더를 소비하므로 한쪽만 고치는
비대칭(D-066의 반복 원인)이 구조적으로 생기지 않는다.

**경로별 문구 차이는 통일하지 않고 파라미터로 보존한다** — 프롬프트 바이트가 바뀌면
LLM 출력 분포가 달라져 회귀 원인 분리가 불가능해지기 때문이다(계획서 §0.3-2 1단계).
여기 남은 파라미터(`style`·`hangul_alias`·`sample_style` 등)가 곧 2단계 문구 통일의
diff 목록이다.

DB 특화 스키마 리터럴은 이 모듈에 두지 않는다(D-088). 예시 문구에 필요한 리터럴
(엔티티 resource_type·EAV 조인 컬럼 등)은 호출부가 인자로 주입한다 —
`scripts/overfit_check.py` 기준선이 호출부 파일 기준이라 그래야 계층 가드가 유지된다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal, Optional

from src.config import AppConfig
from src.utils.query_gen_common import (
    build_query_examples_block,
    build_value_index_block,
    collect_prior_identity_values,
)
from src.utils.schema_utils import build_excluded_join_map

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 플래그 ON 경로에서만 수행한다.
    from src.nodes.column_deriver import StepwiseDeps

logger = logging.getLogger(__name__)

#: field_mapper가 EAV 속성 매핑에 붙이는 접두사.
EAV_PREFIX = "EAV:"

#: EAV entity↔config 직접 id 조인 금지 지침(구조 가이드 선두에 삽입).
EAV_JOIN_RULE_BLOCK = (
    "## EAV 테이블 조인 규칙\n"
    "EAV 구조의 entity 테이블과 config 테이블을 조인할 때 "
    "id 컬럼으로 직접 조인하지 마세요.\n"
    "두 테이블의 ID 체계가 다릅니다. "
    "반드시 아래 지침의 JOIN SQL 패턴을 그대로 사용하세요.\n\n"
)


# ──────────────────────────────────────────────
# 구조 가이드 (EAV 조인 규칙 · value_joins · 금지 JOIN)
# ──────────────────────────────────────────────


def eav_patterns_of(structure_meta: Optional[dict]) -> list[dict]:
    """구조 분석 메타에서 EAV 패턴만 골라낸다.

    Args:
        structure_meta: `schema_info["_structure_meta"]` (없으면 빈 목록)

    Returns:
        EAV 패턴 딕셔너리 목록
    """
    if not structure_meta:
        return []
    return [p for p in structure_meta.get("patterns", []) if p.get("type") == "eav"]


def first_eav_pattern(schema_info: Optional[dict]) -> Optional[dict]:
    """스키마 정보에서 첫 번째 EAV 패턴을 반환한다(없으면 None).

    Args:
        schema_info: 스키마 정보 딕셔너리 (선택)

    Returns:
        EAV 패턴 딕셔너리 또는 None
    """
    if not schema_info:
        return None
    return next(iter(eav_patterns_of(schema_info.get("_structure_meta"))), None)


def build_value_joins_block(eav_pattern: dict) -> str:
    """EAV 패턴의 값 기반 조인(value_joins) 안내를 만든다(없으면 빈 문자열).

    config 테이블과 entity 테이블 사이에 FK가 없을 때, 어떤 속성 값이 어떤 엔티티
    컬럼과 대응하는지 알려 브릿지 조인을 유도한다.

    Args:
        eav_pattern: EAV 패턴 딕셔너리

    Returns:
        구조 가이드에 덧붙일 블록(value_joins 부재 시 "")
    """
    value_joins = eav_pattern.get("value_joins", [])
    if not value_joins:
        return ""

    entity_table = eav_pattern.get("entity_table", "entity_table")
    config_table = eav_pattern.get("config_table", "config_table")
    attr_col = eav_pattern.get("attribute_column", "NAME")

    block = "\n\n[값 기반 조인 (value-based join)]"
    block += (
        f"\n{config_table}과 {entity_table} 간 FK가 없습니다. "
        "다음 값 대응 관계를 조인에 활용하세요:"
    )
    for vj in value_joins:
        block += (
            f"\n- {config_table}.{attr_col}='{vj['eav_attribute']}'인 행의 "
            f"{vj['eav_value_column']} 값은 "
            f"{entity_table}.{vj['entity_column']}과 동일한 값입니다."
        )
    return block


def build_forbidden_join_block(patterns: list[dict]) -> str:
    """JOIN ON 절에서 쓰면 안 되는 컬럼 경고를 만든다(없으면 빈 문자열).

    소제목 + 불릿 목록 형태로, 단일·멀티가 같은 문구를 쓴다(W-1 채택 — 종전 멀티의
    inline 한 줄 문구는 소비처가 사라져 분기와 함께 제거했다).

    Args:
        patterns: 검사할 패턴 목록(단일은 전체 패턴, 멀티는 EAV 패턴 1건씩)

    Returns:
        구조 가이드에 덧붙일 경고 블록
    """
    block = ""
    for pattern in patterns:
        excluded = pattern.get("excluded_join_columns", [])
        if not excluded:
            continue
        block += "\n\n[금지 JOIN 컬럼]"
        block += "\n다음 컬럼은 JOIN ON 절에서 절대 사용하지 마세요:"
        for excl in excluded:
            block += (
                f"\n- {excl.get('table', '?')}.{excl.get('column', '?')}: "
                f"{excl.get('reason', 'JOIN 불가')}"
            )
    return block


# ──────────────────────────────────────────────
# few-shot 예시 (프로필 고정 ↔ 질의 이력)
# ──────────────────────────────────────────────


def build_query_examples(
    structure_meta: Optional[dict], history_examples: Optional[list[dict]]
) -> str:
    """few-shot 쿼리 예시 블록을 만든다 (N2/D-133).

    이력 검색이 유사 예시를 골라오면 프로필 고정 예시 대신 그것을 쓰되, 블록 포맷은
    같은 헬퍼를 통과시켜 유지한다. 미적중·플래그 OFF면 고정 예시 경로 그대로다.

    Args:
        structure_meta: 구조 분석 메타(고정 예시 출처)
        history_examples: 질의 이력에서 선택된 예시 (없으면 고정 예시 사용)

    Returns:
        few-shot 블록 문자열
    """
    if history_examples:
        return build_query_examples_block({"query_examples": history_examples})
    return build_query_examples_block(structure_meta)


async def select_history_fewshot(
    db_id: str, user_query: str, app_config: Optional[AppConfig]
) -> Optional[list[dict]]:
    """검증된 질의 이력에서 few-shot 예시를 선택한다 (N2/D-133).

    설정값 → 검색 인자 매핑을 한 곳에 둬 경로 간 검색 조건이 갈라지지 않게 한다.

    Args:
        db_id: DB 식별자
        user_query: 검색에 쓸 자연어 질의
        app_config: 앱 설정 (없으면 미적용)

    Returns:
        few-shot 예시 목록(question/sql) 또는 None(고정 예시 유지)
    """
    if app_config is None:
        return None
    from src.schema_cache.query_history import select_fewshot_examples

    t2 = app_config.text2sql
    return await select_fewshot_examples(
        db_id,
        user_query,
        enabled=t2.query_history_fewshot,
        top_k=t2.query_history_top_k,
        min_score=t2.query_history_min_score,
    )


# ──────────────────────────────────────────────
# column_mapping 정제 · 분류
# ──────────────────────────────────────────────


def filter_mapping_by_schema(
    column_mapping: dict[str, Optional[str]],
    schema_info: Optional[dict],
    *,
    log_label: str,
    log_schema_tables: bool = False,
    strip_db_prefix: bool = False,
) -> dict[str, Optional[str]]:
    """현재 스키마에 없는 테이블의 매핑을 걸러낸다.

    field_mapper가 다른 DB의 테이블이나 존재하지 않는 테이블로 매핑하면 LLM이 그 이름을
    그대로 SQL에 써서 실행 에러가 난다. `db_id.table.column` 3단 표기는 `table.column`으로
    정규화한다. EAV 매핑과 미매핑(None)은 통과시킨다.

    Args:
        column_mapping: 필드-컬럼 매핑
        schema_info: DB 스키마 정보 (없으면 원본 그대로 반환)
        log_label: 필터링 경고 로그 접두 문구(경로 구분용)
        log_schema_tables: 경고에 스키마 테이블 샘플을 덧붙일지 여부
        strip_db_prefix: `db_id:table.column`의 콜론 접두사를 테이블 판정 전에 떼어낼지.
            단일 경로만 떼어낸다 — 멀티는 콜론 표기를 미존재 테이블로 보고 버린다.
            경로 간 실동작 차이라 통일하지 않고 보존한다(§0.3-2 2단계 대상).

    Returns:
        필터링된 매핑
    """
    if not schema_info:
        return column_mapping

    tables_in_schema = set(schema_info.get("tables", {}).keys())
    # schema_info 키가 "schema.table" 형식일 수 있으므로 마지막 부분도 매칭 대상에 추가
    tables_lower: set[str] = set()
    for t in tables_in_schema:
        tables_lower.add(t.lower())
        if "." in t:
            tables_lower.add(t.rsplit(".", 1)[-1].lower())

    filtered: dict[str, Optional[str]] = {}
    for field, col in column_mapping.items():
        if not col or col.startswith(EAV_PREFIX):
            filtered[field] = col
            continue
        effective_col = (
            col.split(":", 1)[-1] if strip_db_prefix and ":" in col else col
        )
        col_parts = effective_col.split(".")
        if len(col_parts) >= 3:
            table_part = col_parts[-2]
            col = f"{col_parts[-2]}.{col_parts[-1]}"
        elif len(col_parts) == 2:
            table_part = col_parts[0]
        else:
            table_part = ""

        if table_part.lower() in tables_lower:
            filtered[field] = col
        elif log_schema_tables:
            logger.warning(
                "%s: '%s' -> '%s' (테이블 '%s' 미존재, schema_tables=%s)",
                log_label, field, col, table_part, list(tables_in_schema)[:5],
            )
        else:
            logger.warning(
                "%s: '%s' -> '%s' (테이블 '%s' 미존재)",
                log_label, field, col, table_part,
            )
    return filtered


def split_mapping_entries(
    column_mapping: dict[str, Optional[str]]
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """매핑을 정규 컬럼과 EAV 속성으로 가른다("EAV:" 접두사는 제거).

    Args:
        column_mapping: 필드-컬럼 매핑

    Returns:
        (정규 매핑 [(필드, 컬럼)], EAV 매핑 [(필드, 속성명)])
    """
    regular = [
        (field, col) for field, col in column_mapping.items()
        if col and not col.startswith(EAV_PREFIX)
    ]
    eav = [
        (field, col[len(EAV_PREFIX):]) for field, col in column_mapping.items()
        if col and col.startswith(EAV_PREFIX)
    ]
    return regular, eav


def split_eav_by_resource_type(
    eav_entries: list[tuple[str, str]],
    attr_resource_types: dict[str, str],
    *,
    entity_resource_type: str,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str]]]:
    """EAV 속성을 자식 리소스 소속과 엔티티 자신 소속으로 가른다 (D-068).

    자식 리소스(CPU·메모리 등) 속성이 섞이면 엔티티 행 브릿지 조인만으로는 NULL이 되므로,
    호출부는 자식 목록이 비지 않을 때 다중 리소스 피벗으로 전환한다.

    Args:
        eav_entries: [(필드, 속성명)] 목록
        attr_resource_types: {대문자 속성명: resource_type} 매핑
        entity_resource_type: 엔티티 자신의 resource_type — 호출부가 주입한다(D-088)

    Returns:
        (자식 [(필드, 속성명, resource_type)], 엔티티 [(필드, 속성명)])
    """
    child = [
        (field, attr, attr_resource_types[attr.upper()])
        for field, attr in eav_entries
        if attr.upper() in attr_resource_types
        and attr_resource_types[attr.upper()] != entity_resource_type
    ]
    own = [
        (field, attr)
        for field, attr in eav_entries
        if attr.upper() not in attr_resource_types
        or attr_resource_types[attr.upper()] == entity_resource_type
    ]
    return child, own


# ──────────────────────────────────────────────
# EAV 피벗 매핑 프롬프트 블록
# ──────────────────────────────────────────────


def build_eav_join_hint(
    eav_pattern: Optional[dict], *, host_attribute: str, link_column: str
) -> str:
    """EAV 브릿지 조인 힌트를 만든다(value_joins 우선, 없으면 join_condition 폴백).

    Args:
        eav_pattern: EAV 패턴 딕셔너리 (선택)
        host_attribute: 브릿지 예시에 쓸 엔티티 식별 속성명 — 호출부 주입(D-088)
        link_column: config 행끼리 잇는 컬럼명 — 호출부 주입(D-088)

    Returns:
        조인 힌트 문자열(없으면 "")
    """
    if eav_pattern and eav_pattern.get("value_joins"):
        config_table = eav_pattern.get("config_table", "config_table")
        attr_col = eav_pattern.get("attribute_column", "NAME")
        val_col = eav_pattern.get("value_column", "VALUE")
        entity_table = eav_pattern.get("entity_table", "entity_table")
        vj_lines = [
            f"  {config_table}.{attr_col}='{vj['eav_attribute']}' -> "
            f"{vj['eav_value_column']} = {entity_table}.{vj['entity_column']}"
            for vj in eav_pattern["value_joins"]
        ]
        return (
            "\n주의: 두 테이블 간 FK가 없으므로 값 기반 브릿지 조인을 사용하세요:\n"
            + "\n".join(vj_lines)
            + f"\n예: LEFT JOIN {config_table} p_host ON p_host.{attr_col}='{host_attribute}'"
            f" AND p_host.{val_col} = r.hostname"
            f"\n     LEFT JOIN {config_table} p_attr ON p_attr.{link_column} ="
            f" p_host.{link_column} AND p_attr.{attr_col} = '속성명'"
        )

    join_cond = eav_pattern.get("join_condition", "") if eav_pattern else ""
    if join_cond:
        return f"\n조인 조건: {join_cond}"
    return ""


def build_eav_pivot_block(
    eav_entries: list[tuple[str, str]],
    eav_pattern: Optional[dict],
    *,
    hangul_alias: bool,
    host_attribute: str,
    link_column: str,
) -> str:
    """EAV 속성을 CASE WHEN 피벗으로 뽑도록 지시하는 블록을 만든다.

    ``hangul_alias``는 경로별 alias 지시 차이를 보존한다(§0.3-3 (e) — 멀티는 폼필 헤더
    매칭 때문에 한글 alias를 강제하고, 단일은 자유 alias다. 의도된 차이이므로 통일하지
    않는다).

    Args:
        eav_entries: [(필드, 속성명)] 목록
        eav_pattern: EAV 패턴 딕셔너리 (선택 — 없으면 일반 명칭으로 렌더)
        hangul_alias: 결과 alias를 한글 양식 필드명으로 강제할지 여부
        host_attribute: 브릿지 예시 속성명 — 호출부 주입(D-088)
        link_column: config 연결 컬럼명 — 호출부 주입(D-088)

    Returns:
        사용자 프롬프트에 덧붙일 EAV 피벗 블록
    """
    config_table = eav_pattern.get("config_table", "config_table") if eav_pattern else "config_table"
    attr_col = eav_pattern.get("attribute_column", "NAME") if eav_pattern else "NAME"
    val_col = eav_pattern.get("value_column", "VALUE") if eav_pattern else "VALUE"

    eav_lines = "\n".join(
        f'- "{field}" → EAV 속성 "{attr}" ({config_table}.{attr_col} = \'{attr}\' → {val_col})'
        for field, attr in eav_entries
    )
    join_hint = build_eav_join_hint(
        eav_pattern, host_attribute=host_attribute, link_column=link_column
    )

    if hangul_alias:
        instruction = (
            f"위 EAV 속성은 {config_table} 테이블에서 피벗 쿼리로 추출해야 합니다.\n"
            "**결과 alias는 반드시 양식 필드명(왼쪽 한글, 따옴표 포함) 그대로** 하세요"
            "(임의 영문 alias 금지 — 결과 컬럼명이 양식 헤더와 일치해야 채워집니다):\n"
            f"  MAX(CASE WHEN p.{attr_col} = '속성명' THEN p.{val_col} END) AS \"양식필드명\""
        )
    else:
        instruction = (
            f"위 EAV 속성은 {config_table} 테이블에서 피벗 쿼리로 추출해야 합니다:\n"
            f"  MAX(CASE WHEN p.{attr_col} = '속성명' THEN p.{val_col} END) AS alias"
        )

    return (
        f"## EAV 피벗 매핑 (반드시 CASE WHEN 피벗으로 변환)\n{eav_lines}\n\n"
        f"{instruction}{join_hint}\n"
        "반드시 GROUP BY를 포함하세요."
    )


# ──────────────────────────────────────────────
# 스키마 텍스트화
# ──────────────────────────────────────────────


def format_schema_text(
    schema_info: dict,
    *,
    column_descriptions: Optional[dict[str, str]] = None,
    column_synonyms: Optional[dict[str, list[str]]] = None,
    resource_type_synonyms: Optional[dict[str, list[str]]] = None,
    eav_name_synonyms: Optional[dict[str, list[str]]] = None,
    include_not_null: bool = False,
    sample_style: Literal["labeled", "compact"] = "compact",
    relationships_header: str = "### FK Relationships",
) -> str:
    """스키마 정보를 프롬프트용 텍스트로 변환한다.

    단일 경로는 주석(설명·유사어·NOT NULL·참조 섹션)까지 실은 상세판을, 멀티 경로는
    축약판을 쓴다 — 어느 쪽을 쓸지는 인자로 정한다(문구·수록 범위 차이 보존, §0.3-2).

    Args:
        schema_info: 스키마 딕셔너리
        column_descriptions: {table.column: 설명} (선택 — 주면 컬럼 뒤에 주석)
        column_synonyms: {table.column: [유사어, ...]} (선택)
        resource_type_synonyms: {resource_type값: [한국어, ...]} (선택 — 주면 참조 섹션)
        eav_name_synonyms: {EAV 속성명: [한국어, ...]} (선택 — 주면 참조 섹션)
        include_not_null: NOT NULL 표기 포함 여부
        sample_style: 샘플 데이터 표기("labeled"=건수 포함 한글 / "compact"=`sample:`)
        relationships_header: FK 관계 섹션 헤더 문구

    Returns:
        사람이 읽기 쉬운 스키마 텍스트
    """
    descriptions = column_descriptions or {}
    synonyms = column_synonyms or {}
    excluded_join_map = build_excluded_join_map(schema_info)

    lines: list[str] = []
    for table_name, table_data in schema_info.get("tables", {}).items():
        # table_name에서 스키마 접두사 제거한 bare name 추출
        bare_table = table_name.rsplit(".", 1)[-1].lower()
        lines.append(f"### {table_name}")
        for col in table_data.get("columns", []):
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            if include_not_null and not col.get("nullable", True):
                col_str += " NOT NULL"
            excluded_reason = excluded_join_map.get((bare_table, col["name"].lower()))
            if excluded_reason:
                col_str += f" -- JOIN 금지({excluded_reason})"
            desc = descriptions.get(f"{table_name}.{col['name']}")
            if desc:
                col_str += f" -- {desc}"
            syns = synonyms.get(f"{table_name}.{col['name']}")
            if syns:
                col_str += f" [유사: {', '.join(syns[:5])}]"
            lines.append(col_str)

        samples = table_data.get("sample_data", [])
        if samples:
            preview = json.dumps(samples[:3], ensure_ascii=False, indent=2)
            if sample_style == "labeled":
                lines.append(f"  샘플 데이터 ({len(samples)}건):\n{preview}")
            else:
                lines.append(f"  sample: {preview}")
        lines.append("")

    if resource_type_synonyms:
        lines.append("")
        lines.append("### 참조: RESOURCE_TYPE 값과 한국어 표현")
        lines.append(
            "아래 값들은 RESOURCE_TYPE 컬럼에 저장되는 값입니다. "
            "사용자가 한국어로 질의할 때 아래 매핑을 참고하세요."
        )
        for rt_value, words in sorted(resource_type_synonyms.items()):
            lines.append(f"  - {rt_value} = {', '.join(words)}")

    if eav_name_synonyms:
        lines.append("")
        lines.append("### 참조: EAV 설정 항목명과 한국어 표현")
        lines.append(
            "아래는 EAV 테이블의 속성명 컬럼에 저장되는 설정 항목명입니다. "
            "사용자가 한국어로 질의할 때 아래 매핑을 참고하세요."
        )
        for eav_name, words in sorted(eav_name_synonyms.items()):
            lines.append(f"  - {eav_name} = {', '.join(words)}")

    rels = schema_info.get("relationships", [])
    if rels:
        lines.append(relationships_header)
        for rel in rels:
            lines.append(f"  {rel['from']} -> {rel['to']}")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 경로 대칭 재료 (P3-2)
# ──────────────────────────────────────────────


def path_parity_enabled(app_config: Optional[AppConfig]) -> bool:
    """경로 대칭 갭 해소(§0.3-3 (a)~(d)) 옵트인 여부를 판정한다.

    ``is True`` 비교는 의도적이다 — 설정 대역(MagicMock)의 미정의 속성은 기본 truthy라
    플래그가 저절로 켜진 것처럼 보인다(Known Mistakes). 실 bool True일 때만 발동한다.

    Args:
        app_config: 앱 설정 (없으면 OFF)

    Returns:
        경로 대칭 주입 활성 여부
    """
    return getattr(getattr(app_config, "text2sql", None), "path_parity", False) is True


def build_schema_prefix_rule(
    schema_prefix: str, *, example_table: str, foreign_prefix_example: str
) -> str:
    """스키마 한정 규칙(D-057)을 만든다.

    LLM이 임의 스키마를 붙이거나, DB2에서 무스키마로 둬 연결 계정 CURRENT SCHEMA로
    잘못 해소되는 것을 막는다.

    Args:
        schema_prefix: 강제할 스키마 접두사(빈 문자열이면 무스키마 규칙)
        example_table: 예시에 쓸 테이블명 — 호출부 주입(D-088)
        foreign_prefix_example: 붙이지 말아야 할 접두사 예시 — 호출부 주입(D-088)

    Returns:
        엔진 힌트에 덧붙일 규칙 문자열
    """
    if schema_prefix:
        return (
            f"\n[스키마 한정 규칙] 이 DB의 모든 테이블은 반드시 접두사 `{schema_prefix}`를 붙여 "
            f"`{schema_prefix}테이블명` 형식으로 참조하세요 (예: {schema_prefix}{example_table}). "
            f"다른 스키마명을 임의로 붙이지 마세요."
        )
    return (
        "\n[스키마 한정 규칙] 이 DB의 테이블은 **스키마 접두사 없이(무스키마)** 참조하세요 "
        f"(예: {example_table}). `{foreign_prefix_example}` 등 임의의 스키마 접두사를 붙이지 마세요."
    )


def prior_server_scope(prior_rows: Any) -> Optional[tuple[str, list[str]]]:
    """선행 task 결과(prior_rows)에서 결정적 서버 스코프를 뽑는다 (D-099).

    시맨틱 컴파일러에 넘겨 HAVING 집계 필터로 강제한다 — 프롬프트 지시에 의존하면 LLM이
    WHERE 배치·모순 alias 등 변종을 생성해 침묵 0건/오답이 반복된다(D-096·D-098).

    Args:
        prior_rows: 선행 task 결과 {task_id: rows}

    Returns:
        (식별컬럼, 값목록) 또는 None(선행 스코프 없음)
    """
    if not prior_rows:
        return None
    col, values = collect_prior_identity_values(prior_rows)
    if not col or not values:
        return None
    return col, values


def query_keywords(user_query: str) -> list[str]:
    """값 인덱스 검색용 질의 토큰(2글자 이상)을 추출한다."""
    tokens = re.split(r"[\s,./()\[\]{}'\"`~!@#$%^&*=+:;?<>|\\-]+", user_query or "")
    return [t for t in tokens if len(t) >= 2]


def build_value_index_injection(
    value_index: Optional[dict[str, list[str]]],
    user_query: str,
    app_config: AppConfig,
) -> str:
    """E5-2 값 검색 리터럴 주입 블록을 만든다(플래그 OFF·인덱스 부재·미매칭 시 "").

    Args:
        value_index: 컬럼 값 인덱스
        user_query: 사용자 자연어 질의
        app_config: 앱 설정

    Returns:
        검증 리터럴 블록
    """
    if not app_config.synonym.value_retrieval:
        return ""
    if not value_index:
        return ""
    from src.schema_cache.value_index import search_value_index

    matched = search_value_index(
        value_index,
        query_keywords(user_query),
        fuzzy=app_config.synonym.fuzzy_match,
        min_score=app_config.synonym.match_confidence_min,
    )
    return build_value_index_block(matched)


def build_stepwise_deps(
    app_config: AppConfig,
    *,
    path: str,
    synonyms: dict[str, list[str]],
    schema_info: dict,
    db_engine: str,
    default_limit: int,
    value_index: Optional[dict[str, list[str]]] = None,
) -> Optional["StepwiseDeps"]:
    """단계적 도출(S2/D-128) 도구 재료를 만든다(플래그 OFF면 None).

    설정값 → 재료 매핑을 한 곳에 둬 경로 간 **도구 목록**이 갈라지지 않게 한다(D-066).
    경로별로 손에 있는 재료(유사어 출처·값 인덱스 가용성)만 인자로 다르다.

    Args:
        app_config: 앱 설정
        path: 발동 경로 라벨("single"|"multi_db")
        synonyms: 유사어 사전
        schema_info: 대상 DB 스키마 정보
        db_engine: DB 엔진 타입
        default_limit: 결정적으로 해석된 기본 행 제한
        value_index: 컬럼 값 인덱스 (가용 경로만)

    Returns:
        ``column_deriver.StepwiseDeps`` 또는 None(플래그 OFF)
    """
    if not app_config.text2sql.stepwise_derivation:
        return None
    from src.nodes.column_deriver import StepwiseDeps

    return StepwiseDeps(
        path=path,
        synonyms=synonyms,
        value_index=value_index,
        schema_info=schema_info or {},
        db_engine=db_engine or "postgresql",
        adapter_db_ids=app_config.get_polestar_db_ids() or None,
        default_limit=default_limit,
        synonym_min_score=app_config.synonym.match_confidence_min,
        value_fuzzy=app_config.synonym.fuzzy_match,
    )
