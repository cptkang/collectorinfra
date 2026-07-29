"""지표 필드 분류 도구 (Plan 67 Phase S1 §4.2).

"CPU 평균" 같은 표현이 사용률 지표인지, 지표라면 어떤 리소스·집계·값 컬럼에 대응하는지
판정한다. 대응 규칙은 DB마다 다르므로 **어댑터 레지스트리를 경유**한다 — 공용 코어는
특정 DB의 스키마를 모른다(D-088/D-089).

담당 어댑터가 분류 훅(``classify_metric_field``)을 노출하지 않으면 스키마 무관 표면어
판정(``is_metric_field_name``)으로 강등하고, 그 사실을 ``source``로 알린다(침묵 강등 금지).
"""

from __future__ import annotations

from typing import Optional

from src.db_adapters import get_adapter
from src.utils.query_gen_common import is_metric_field_name

# 분류 근거 — adapter: 담당 어댑터 훅, generic: 스키마 무관 표면어 판정(강등)
SOURCE_ADAPTER = "adapter"
SOURCE_GENERIC = "generic"


def classify_metric_field(
    field: str,
    *,
    db_id: Optional[str] = None,
    adapter_db_ids: Optional[set[str]] = None,
) -> Optional[dict]:
    """필드 표현이 사용률 지표인지 판정하고, 가능하면 조립 정보까지 반환한다.

    Args:
        field: 헤더/필드 표현(예: "CPU 평균")
        db_id: 대상 DB 식별자(어댑터 디스패치용)
        adapter_db_ids: 어댑터 담당 db_id 집합(런타임 설정에서 주입)

    Returns:
        지표가 아니면 None. 지표면
        {"field", "resource_type", "agg_function", "value_column", "source"}
        — 담당 어댑터가 없거나 분류 훅이 없으면 세 값은 None이고 source="generic"이다.
    """
    if not field:
        return None

    adapter = get_adapter(db_id, adapter_db_ids)
    classify = getattr(adapter, "classify_metric_field", None) if adapter else None
    if callable(classify):
        classified = classify(field)
        if not classified:
            return None
        resource_type, agg_function, value_column = classified
        return {
            "field": field,
            "resource_type": resource_type,
            "agg_function": agg_function,
            "value_column": value_column,
            "source": SOURCE_ADAPTER,
        }

    if is_metric_field_name(field):
        return {
            "field": field,
            "resource_type": None,
            "agg_function": None,
            "value_column": None,
            "source": SOURCE_GENERIC,
        }
    return None
