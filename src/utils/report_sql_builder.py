"""양식 채우기용 결정적 SQL 빌더 (D-038 Phase 2).

field_mapper의 매핑(column_mapping)과 Phase 1 메타데이터(metric_patterns,
known_attributes_detail, value_joins)를 입력으로 폴스타 "서버 인벤토리 + 사용량"
리포트 SQL을 **결정적으로** 생성한다. 모든 필드를 분류할 수 있을 때만 SQL을 반환하고,
하나라도 분류 실패 시 None을 반환하여 호출측이 LLM 생성으로 폴백하게 한다(분류-아니면-폴백).

생성 SQL은 D-037 규칙을 설계상 내장한다:
  - 메트릭(cmm_metric_stat_*)은 LEFT JOIN + ON절 필터 → server.Server 행이 탈락하지 않음.
  - EAV 속성은 소유 resource_type별 CASE로 분리 → 코어수/메모리용량 NULL 방지.
  - Hostname/IPaddress 등 value_joins로 직접컬럼 등가가 정의된 속성은 직접컬럼 사용
    (공동존 폴스타처럼 식별 정보가 EAV에 비어있는 경우에도 안전).
  - definition_name·EAV 속성명은 메타데이터 상수 → 오타(예: OSVerson) 불가.

순수 함수 모음이며 utils 계층(dict/메타데이터)만 의존한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.utils.metric_classifier import (
    MetricFieldSpec,
    classify_metric_field,
    resolve_stat_table,
)
from src.utils.schema_utils import normalize_field_name

# cmm_resource에서 직접 컬럼으로 허용하는 식별/상태 컬럼 (서버 행 기준)
_ALLOWED_DIRECT_COLUMNS = {"name", "hostname", "ipaddress", "avail_status"}

# 집계종류 → SQL 집계 함수
_AGG_FUNC = {"avg": "AVG", "max": "MAX", "min": "MIN"}

# description의 [resource_type: X] 태그 추출
_RT_TAG = re.compile(r"\[resource_type:\s*([^\]]+)\]")

# resource_type 모호성 해소용 도메인 힌트 (필드에 이 단어가 있으면 해당 타입 우선)
_RT_DOMAIN_HINTS = {
    "server.Memory": ["메모리", "memory", "mem", "ram"],
    "server.Disks": ["디스크", "disk"],
    "server.FileSystems": ["파일시스템", "파일 시스템", "fs"],
}

_SCHEMA = "polestar"
_ENTITY = "cmm_resource"
_CONFIG = "core_config_prop"
_SERVER_RT = "server.Server"


@dataclass
class BuildResult:
    """결정적 빌더 결과."""

    sql: str
    field_aliases: dict          # {원본 양식 필드: SQL alias} — column_mapping 갱신용
    classifications: dict        # {필드: (kind, detail)} — 디버그/테스트용


def _norm(s: Optional[str]) -> str:
    return normalize_field_name(s or "").replace(" ", "").lower()


def _eav_to_direct_map(value_joins: Optional[list]) -> dict:
    """value_joins에서 {EAV 속성(소문자): entity 직접컬럼} 매핑을 만든다.

    예: Hostname→hostname, IPaddress→ipaddress. 식별 정보가 EAV에 비어있어도
    직접컬럼으로 안전하게 가져오기 위함.
    """
    result: dict[str, str] = {}
    for vj in value_joins or []:
        attr = str(vj.get("eav_attribute", "")).strip()
        col = str(vj.get("entity_column", "")).strip()
        if attr and col and col.lower() in _ALLOWED_DIRECT_COLUMNS:
            result[attr.lower()] = col.lower()
    return result


def _eav_resource_type(
    known_attributes_detail: Optional[list], attr_name: str, field: str
) -> Optional[str]:
    """EAV 속성의 소유 resource_type을 description 태그에서 해석한다. 모호하면 None."""
    tags: list[str] = []
    for entry in known_attributes_detail or []:
        if str(entry.get("name", "")).lower() == attr_name.lower():
            tags = _RT_TAG.findall(entry.get("description", "") or "")
            break
    tags = [t.strip() for t in tags if t.strip()]
    if len(tags) == 1:
        return tags[0]
    if len(tags) > 1:
        # 모호 → 필드의 도메인어로 해소 (예: "메모리 용량" → server.Memory)
        fnorm = _norm(field)
        for t in tags:
            for hint in _RT_DOMAIN_HINTS.get(t, []):
                if _norm(hint) in fnorm:
                    return t
        return None
    return None


def _classify_field(
    field: str,
    mapped: Optional[str],
    metric_patterns: dict,
    known_attributes_detail: Optional[list],
    eav_to_direct: dict,
) -> Optional[tuple]:
    """필드를 ("metric"|"eav"|"direct", detail)로 분류한다. 불가능하면 None."""
    # 1) 메트릭 우선 (field_mapper가 None으로 두는 사용률 컬럼)
    spec = classify_metric_field(field, metric_patterns)
    if spec is not None and spec.resource_type and spec.definition_name:
        return ("metric", spec)

    # 2) 매핑 기반 (EAV / 직접컬럼)
    if not mapped:
        return None

    if mapped.startswith("EAV:"):
        attr = mapped[4:].strip()
        if not attr:
            return None
        # value_joins로 직접컬럼 등가가 있으면 직접컬럼 사용 (더 안전)
        if attr.lower() in eav_to_direct:
            return ("direct", eav_to_direct[attr.lower()])
        rt = _eav_resource_type(known_attributes_detail, attr, field)
        if not rt:
            return None
        return ("eav", (rt, attr))

    # "table.column" 또는 "column" → 직접컬럼 (허용 목록만)
    col = mapped.rsplit(".", 1)[-1].strip().lower()
    if col in _ALLOWED_DIRECT_COLUMNS:
        return ("direct", col)
    return None


def build_polestar_report_sql(
    fields: list[str],
    column_mapping: Optional[dict],
    metric_patterns: dict,
    known_attributes_detail: Optional[list],
    *,
    value_joins: Optional[list] = None,
    resolution: Optional[str] = None,
    limit: int = 100000,
) -> Optional[BuildResult]:
    """폴스타 양식 채우기 리포트 SQL을 결정적으로 생성한다.

    모든 필드가 분류되면 BuildResult, 하나라도 미분류면 None(→ LLM 폴백).
    """
    if not fields or not metric_patterns:
        return None

    column_mapping = column_mapping or {}
    eav_to_direct = _eav_to_direct_map(value_joins)

    classifications: dict[str, tuple] = {}
    for f in fields:
        c = _classify_field(
            f, column_mapping.get(f), metric_patterns,
            known_attributes_detail, eav_to_direct,
        )
        if c is None:
            return None  # 미분류 → 폴백
        classifications[f] = c

    has_metric = any(k == "metric" for k, _ in classifications.values())
    has_eav = any(k == "eav" for k, _ in classifications.values())

    stat = resolve_stat_table(metric_patterns, resolution) if has_metric else None
    if has_metric and (stat is None or not stat.table):
        return None

    select_lines: list[str] = []
    field_aliases: dict[str, str] = {}
    resource_types: set[str] = {_SERVER_RT}

    for f in fields:
        kind, detail = classifications[f]
        if kind == "direct":
            col = detail
            alias = f"{_ENTITY}.{col}"
            expr = f"MAX(CASE WHEN res.resource_type = '{_SERVER_RT}' THEN res.{col} END)"
        elif kind == "eav":
            rt, attr = detail
            resource_types.add(rt)
            alias = f"EAV:{attr}"
            expr = (
                f"MAX(CASE WHEN res.resource_type = '{rt}' "
                f"AND cc.name = '{attr}' THEN cc.stringvalue_short END)"
            )
        else:  # metric
            spec: MetricFieldSpec = detail
            resource_types.add(spec.resource_type)
            func = _AGG_FUNC.get(spec.aggregation, "AVG")
            alias = f"metric_{spec.name}_{spec.aggregation}"
            expr = (
                f"ROUND({func}(CASE WHEN res.resource_type = '{spec.resource_type}' "
                f"AND s.definition_name = '{spec.definition_name}' "
                f"THEN s.{spec.value_column} END)::numeric, 2)"
            )
        select_lines.append(f'  {expr} AS "{alias}"')
        field_aliases[f] = alias

    rt_list = ", ".join(f"'{rt}'" for rt in sorted(resource_types))
    from_lines = [f"FROM {_SCHEMA}.{_ENTITY} res"]
    if has_eav:
        from_lines.append(
            f"LEFT JOIN {_SCHEMA}.{_CONFIG} cc "
            f"ON res.resource_conf_id = cc.configuration_id"
        )
    if has_metric:
        # 메트릭은 LEFT JOIN + ON절 필터 → server.Server 행 탈락 방지(D-037)
        from_lines.append(
            f"LEFT JOIN {_SCHEMA}.{stat.table} s "
            f"ON s.resource_id = res.id "
            f"AND s.stat_date = (SELECT MAX(stat_date) FROM {_SCHEMA}.{stat.table})"
        )

    sql = (
        "SELECT\n"
        + ",\n".join(select_lines)
        + "\n"
        + "\n".join(from_lines)
        + "\n"
        + f"WHERE res.resource_type IN ({rt_list}) AND res.dtime IS NULL\n"
        + "GROUP BY COALESCE(res.platform_resource_id, res.id)\n"
        + "ORDER BY 1\n"
        + f"LIMIT {int(limit)};"
    )

    return BuildResult(
        sql=sql, field_aliases=field_aliases, classifications=classifications
    )
