"""양식 필드명을 성능 지표(메트릭)로 분류하는 순수 유틸리티 (D-038 Phase 1).

프로필의 ``metric_patterns`` 메타데이터(cmm_metric_stat_[h,d,m] 구조)를 기반으로,
"CPU 평균"·"메모리 최대 사용률" 같은 양식 컬럼을 결정적으로
(resource_type + definition_name + 집계종류 + 값컬럼)으로 해석한다.

이 모듈은 어떤 노드/상태에도 의존하지 않는 순수 함수 모음이며, 향후 결정적
SQL 빌더(D-038 Phase 2)가 양식 필드를 ⓐ직접컬럼/ⓑEAV/ⓒ메트릭으로 분류할 때
ⓒ 판정에 사용한다. metric_patterns가 없으면 항상 None을 반환하므로(=메트릭 아님)
메타데이터가 없는 DB/프로필에서는 아무 영향이 없다.

분류 규칙:
  1) 완전 동의어(예: "CPU 사용률") 매칭 → 해당 메트릭. 집계는 필드에서 감지(없으면 기본 avg).
  2) 집계 키워드(평균/최대/최소…)가 있고 + 도메인어(예: "CPU")가 있으면 → 해당 메트릭.
  "CPU 코어 수"·"메모리 용량"처럼 집계 키워드도 완전 동의어도 없는 필드는 None
  (→ EAV/직접컬럼으로 처리되도록 둔다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.utils.schema_utils import normalize_field_name

# 집계 키워드가 없을 때 메트릭 필드에 적용하는 기본 집계 종류
_DEFAULT_AGGREGATION = "avg"


@dataclass(frozen=True)
class MetricFieldSpec:
    """양식 필드가 메트릭으로 분류된 결과 명세."""

    name: str            # 메트릭 식별자 (예: "cpu_utilization")
    resource_type: str   # 예: "server.Cpus"
    definition_name: str  # 예: "Utilization"
    aggregation: str     # "avg" | "max" | "min"
    value_column: str    # 통계 테이블의 값 컬럼 (예: "avg_val")
    unit: str = ""       # 표시 단위 (예: "%")


@dataclass(frozen=True)
class StatTable:
    """시간 단위별 통계 테이블."""

    table: str            # 예: "cmm_metric_stat_m"
    stat_date_format: str  # 예: "YYYYMM"


def _norm(text: Optional[str]) -> str:
    """부분 문자열 매칭용 정규화: NFC/공백정리 후 공백 제거 + 소문자."""
    if not text:
        return ""
    return normalize_field_name(text).replace(" ", "").lower()


def load_metric_patterns(structure_meta: Optional[dict]) -> dict:
    """structure_meta(또는 프로필 dict)에서 metric_patterns 블록을 추출한다.

    없거나 형식이 잘못되면 빈 dict를 반환한다.
    """
    if not structure_meta:
        return {}
    mp = structure_meta.get("metric_patterns")
    return mp if isinstance(mp, dict) else {}


def detect_aggregation(
    field: str,
    metric_patterns: dict,
    default: Optional[str] = _DEFAULT_AGGREGATION,
) -> Optional[str]:
    """필드명에서 집계 종류(avg/max/min)를 감지한다.

    metric_patterns["aggregations"]의 인식어 목록과 부분 문자열로 대조한다.
    어떤 집계어도 없으면 ``default``를 반환한다(기본 "avg", None 지정 가능).
    """
    fnorm = _norm(field)
    if not fnorm:
        return default
    aggregations = metric_patterns.get("aggregations") or {}
    for agg_key, words in aggregations.items():
        for word in words or []:
            wnorm = _norm(word)
            if wnorm and wnorm in fnorm:
                return agg_key
    return default


def _value_column(metric_patterns: dict, aggregation: str) -> str:
    """집계 종류에 해당하는 값 컬럼명을 반환한다 (없으면 avg 컬럼 폴백)."""
    cols = metric_patterns.get("value_columns") or {}
    return cols.get(aggregation) or cols.get(_DEFAULT_AGGREGATION) or "avg_val"


def classify_metric_field(
    field: str, metric_patterns: dict
) -> Optional[MetricFieldSpec]:
    """양식 필드명을 메트릭으로 분류한다. 메트릭이 아니면 None을 반환한다.

    Args:
        field: 양식 컬럼명 (예: "CPU 평균", "메모리 최대 사용률").
        metric_patterns: 프로필의 metric_patterns 블록.

    Returns:
        매칭 시 MetricFieldSpec, 아니면 None.
    """
    if not field or not metric_patterns:
        return None
    metrics = metric_patterns.get("metrics") or []
    fnorm = _norm(field)
    if not fnorm:
        return None

    detected_agg = detect_aggregation(field, metric_patterns, default=None)

    def _build(metric: dict, agg: Optional[str]) -> MetricFieldSpec:
        resolved_agg = agg or _DEFAULT_AGGREGATION
        return MetricFieldSpec(
            name=metric.get("name", ""),
            resource_type=metric.get("resource_type", ""),
            definition_name=metric.get("definition_name", ""),
            aggregation=resolved_agg,
            value_column=_value_column(metric_patterns, resolved_agg),
            unit=metric.get("unit", "") or "",
        )

    # 규칙 1: 완전 동의어 매칭 (그 자체로 메트릭이 분명한 표현)
    for metric in metrics:
        for syn in metric.get("synonyms") or []:
            snorm = _norm(syn)
            if snorm and snorm in fnorm:
                return _build(metric, detected_agg)

    # 규칙 2: 도메인어 + 집계 키워드 조합 (예: "CPU" + "평균")
    if detected_agg is not None:
        for metric in metrics:
            for term in metric.get("domain_terms") or []:
                tnorm = _norm(term)
                if tnorm and tnorm in fnorm:
                    return _build(metric, detected_agg)

    return None


def resolve_stat_table(
    metric_patterns: dict, resolution: Optional[str] = None
) -> Optional[StatTable]:
    """시간 단위(hour/day/month)에 해당하는 통계 테이블을 반환한다.

    resolution이 없으면 metric_patterns["default_resolution"](기본 "month")을 사용한다.
    stat_tables가 비어있으면 None을 반환한다.
    """
    tables = metric_patterns.get("stat_tables") or {}
    if not tables:
        return None
    default_res = metric_patterns.get("default_resolution") or "month"
    res = resolution or default_res
    entry = tables.get(res) or tables.get(default_res)
    if not entry:
        return None
    return StatTable(
        table=entry.get("table", ""),
        stat_date_format=entry.get("stat_date_format", ""),
    )
