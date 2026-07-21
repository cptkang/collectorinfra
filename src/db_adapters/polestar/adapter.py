"""폴스타 DB 어댑터 — 특화 로직을 훅으로 제공 (Plan 63 P2, D-089).

공용 코어에서 분리 이동한 폴스타 전용 로직(전용 프롬프트 템플릿·라우팅 필터 검증)을
어댑터 훅으로 노출한다. 동작 불변 — 공용 코어는 담당 DB(POLESTAR_DB_IDS)에서만 훅을 발동한다.
"""

from __future__ import annotations

from typing import Callable

from src.db_adapters.polestar.prompts import (
    POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
)
from src.db_adapters.polestar.validators import (
    check_contradictory_alias_resource_type,
    check_routing_filter_misuse,
    check_metric_join_on_server_entity,
    check_pivot_metric_inner_join,
    check_ranking_order_by_nulls_last,
    check_scope_filter_where_demotion,
    check_scoped_pivot_missing_server_identity,
)


class PolestarAdapter:
    """폴스타(POLESTAR) 모니터링 DB 어댑터."""

    name = "polestar"

    def owns(self, db_id: str | None, polestar_db_ids: set[str] | None = None) -> bool:
        """POLESTAR_DB_IDS(.env 런타임 설정)에 db_id가 포함되면 담당한다."""
        return bool(polestar_db_ids) and db_id in polestar_db_ids

    def system_template(self, routing_intent: str | None) -> str | None:
        """의도별 폴스타 전용 시스템 프롬프트 템플릿을 반환한다.

        alarm_query면 알람 전용 템플릿, 그 외엔 성능 템플릿(기존 query_generator 분기와 동일).
        """
        if routing_intent == "alarm_query":
            return POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE
        return POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

    def validator_checks(self) -> list[Callable[[str], list[str]]]:
        """폴스타 전용 SQL 검증 함수 목록(라우팅 필터 오용·피벗 스코프 WHERE 강등 탐지)."""
        return [
            check_routing_filter_misuse,
            check_scope_filter_where_demotion,
            check_scoped_pivot_missing_server_identity,
            check_metric_join_on_server_entity,
            check_pivot_metric_inner_join,
            check_contradictory_alias_resource_type,
            check_ranking_order_by_nulls_last,
        ]
