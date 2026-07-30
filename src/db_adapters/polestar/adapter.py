"""폴스타 DB 어댑터 — 특화 로직을 훅으로 제공 (Plan 63 P2, D-089).

공용 코어에서 분리 이동한 폴스타 전용 로직(전용 프롬프트 템플릿·라우팅 필터 검증)을
어댑터 훅으로 노출한다. 동작 불변 — 공용 코어는 담당 DB(POLESTAR_DB_IDS)에서만 훅을 발동한다.
"""

from __future__ import annotations

from typing import Callable

from src.db_adapters.polestar.assembler import classify_metric_field as _classify_metric_field
from src.db_adapters.polestar.prompts import (
    knowledge_render_enabled,
    render_alarm_system_template,
    render_system_template,
)
from src.db_adapters.polestar.validators import (
    check_contradictory_alias_resource_type,
    check_routing_filter_misuse,
    check_metric_join_on_server_entity,
    check_pivot_metric_inner_join,
    check_ranking_order_by_nulls_last,
    check_scope_filter_where_demotion,
    check_scoped_pivot_missing_server_identity,
    check_value_column_join,
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
        성능 템플릿의 지표 목록은 지식 정본에서 렌더한다(Plan 67 R1-2, 결과 캐시). 잔여 블록
        (SQL 예제·심각도 매핑·알람 조인)은 옵트인 플래그 ON에서만 정본 렌더로 바뀐다.
        """
        if routing_intent == "alarm_query":
            return render_alarm_system_template()
        return render_system_template()

    def classify_metric_field(self, field: str) -> tuple[str, str, str] | None:
        """필드명을 (resource_type, agg_function, value_column)으로 분류한다(assembler 위임).

        src/tools/metrics.py의 optional 훅 계약 — 미분류 필드는 None.
        """
        return _classify_metric_field(field)

    def validator_checks(self) -> list[Callable[[str], list[str]]]:
        """폴스타 전용 SQL 검증 함수 목록(라우팅 필터 오용·피벗 스코프 WHERE 강등 탐지).

        값 컬럼 조인 검사(`check_value_column_join`)는 **프롬프트 지식 렌더 플래그와 같은 게이트**
        뒤에 둔다. 이 검사는 현행(플래그 OFF) 프롬프트의 Template B 예제
        (`ON svr.ipaddress = hi.ipaddress`)를 위반으로 잡으므로, 예제 교정 없이 등록하면 LLM이
        예제대로 생성한 SQL이 매번 반려되어 재시도만 소모한다(검사와 예제는 함께 움직여야 한다).
        """
        checks = [
            check_routing_filter_misuse,
            check_scope_filter_where_demotion,
            check_scoped_pivot_missing_server_identity,
            check_metric_join_on_server_entity,
            check_pivot_metric_inner_join,
            check_contradictory_alias_resource_type,
            check_ranking_order_by_nulls_last,
        ]
        if knowledge_render_enabled():
            checks.append(check_value_column_join)
        return checks
