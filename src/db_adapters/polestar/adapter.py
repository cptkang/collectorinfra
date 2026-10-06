"""폴스타 DB 어댑터 — 특화 로직을 훅으로 제공 (Plan 63 P2, D-089).

공용 코어에서 분리 이동한 폴스타 전용 로직(전용 프롬프트 템플릿·라우팅 필터 검증)을
어댑터 훅으로 노출한다. 동작 불변 — 공용 코어는 담당 DB(POLESTAR_DB_IDS)에서만 훅을 발동한다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.db_adapters.polestar.assembler import classify_metric_field as _classify_metric_field
from src.db_adapters.polestar.entity_probe import (
    build_business_lookup_sql,
    build_entity_probe_sql,
    build_hostname_lookup_sql,
)
from src.db_adapters.polestar.prompts import (
    knowledge_render_enabled,
    render_alarm_system_template,
    render_system_template,
)
from src.db_adapters.polestar.validators import (
    check_active_status_literal_filter,
    check_alarm_resource_server_type_filter,
    check_alarm_table_allowlist,
    check_contradictory_alias_resource_type,
    check_current_month_stat_table,
    check_routing_filter_misuse,
    check_metric_join_on_server_entity,
    check_pivot_metric_inner_join,
    check_ranking_order_by_nulls_last,
    check_scope_filter_where_demotion,
    check_scoped_pivot_missing_server_identity,
    check_severity_label_filter,
    check_time_conditions,
    check_value_column_join,
)
from src.domain.query_time import QueryTime


class PolestarAdapter:
    """폴스타(POLESTAR) 모니터링 DB 어댑터."""

    name = "polestar"
    # 활성 알람 결정적 조립(assembler.try_deterministic_alarm_sql)을 소유한다 — 단일·멀티 경로가
    # 이 표시로 붙일지 정한다(D-294: 다른 어댑터가 담당하는 DB에 폴스타 알람 SQL을 조립하지 않게)
    deterministic_alarm = True

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

    def entity_probe_sql(
        self, value: str, *, db_engine: str | None, db_schema: str | None
    ) -> str:
        """서버 식별자 1개의 존재 확인 SELECT(entity_probe 위임 · plans/123 S-4a).

        0건 진단(`src/nodes/result_organizer.py`)의 optional 훅 계약 — 행이 오면 등록돼 있다.
        """
        return build_entity_probe_sql(value, db_engine=db_engine, db_schema=db_schema)

    def hostname_lookup_sql(
        self, names: list[str], *, db_engine: str | None, db_schema: str | None
    ) -> str:
        """등록 서버명 → OS hostname 고정 조회(간선 E2 · plans/125 E-3 — 선택 훅 계약)."""
        return build_hostname_lookup_sql(names, db_engine=db_engine, db_schema=db_schema)

    def business_lookup_sql(
        self, terms: list[str], *, db_engine: str | None, db_schema: str | None
    ) -> str:
        """업무명 → 서버 행 고정 조회(간선 E6 · plans/130 M-3 — 선택 훅 계약)."""
        return build_business_lookup_sql(terms, db_engine=db_engine, db_schema=db_schema)

    def validator_checks(
        self,
        user_query: str | None = None,
        *,
        time_resolution: dict[str, Any] | None = None,
    ) -> list[Callable[[str], list[str]]]:
        """폴스타 전용 SQL 검증 함수 목록(라우팅 필터 오용·피벗 스코프 WHERE 강등 탐지).

        값 컬럼 조인 검사(`check_value_column_join`)는 **프롬프트 지식 렌더 플래그와 같은 게이트**
        뒤에 둔다. 이 검사는 현행(플래그 OFF) 프롬프트의 Template B 예제
        (`ON svr.ipaddress = hi.ipaddress`)를 위반으로 잡으므로, 예제 교정 없이 등록하면 LLM이
        예제대로 생성한 SQL이 매번 반려되어 재시도만 소모한다(검사와 예제는 함께 움직여야 한다).

        Args:
            user_query: 사용자 원문 질의 — 주면 질의 맥락 의존 검사(D-201 "이번 달"
                stat_m 반려)가 추가된다. 미지정(None)이면 종전 목록 그대로(동작 불변).
            time_resolution: state `time_resolution`(`QueryTime.to_state()` · plans/122 T-5b) —
                주면 D-201 검사 대신 시간 조건 대조(`check_time_conditions`)를 등록한다.
                None(플래그 off)이면 종전 목록 그대로.
        """
        checks = [
            check_routing_filter_misuse,
            check_scope_filter_where_demotion,
            check_scoped_pivot_missing_server_identity,
            check_metric_join_on_server_entity,
            check_pivot_metric_inner_join,
            check_contradictory_alias_resource_type,
            check_ranking_order_by_nulls_last,
            check_alarm_table_allowlist,
            check_severity_label_filter,
            check_active_status_literal_filter,
            check_alarm_resource_server_type_filter,
        ]
        if knowledge_render_enabled():
            checks.append(check_value_column_join)
        # 시간 조건 대조(plans/122 T-5b · D-306) — 요청 시간 해석이 있으면 생성 SQL의 기간 조건을
        # 그 해석과 대조한다. D-201(「이번 달」 stat_m 반려)은 이 검증기의 ②③ 규칙이 덮으므로
        # 중복 등록하지 않는다. 해석이 없으면(플래그 off · 옛 체크포인트) 종전 검사 그대로다.
        qt = QueryTime.from_state(time_resolution) if time_resolution is not None else None
        if qt is not None and qt.metric is not None:
            checks.append(lambda sql: check_time_conditions(sql, qt))
        elif user_query:
            checks.append(
                lambda sql: check_current_month_stat_table(sql, user_query)
            )
        return checks
