"""폼필 결정적 피벗 전처리 — 단일/멀티 경로 공용 순수 함수 (plans/169 N1(a) · D-066 · D-089).

단일 경로(`query_generator._try_build_form_fill_pivot_sql`)와 멀티 경로
(`multi_db_executor._build_mapping_user_parts`·`_split_mapping_for_prompt`·
`_multi_resource_pivot_result`)가 **같은 결과를 내던 단계만** 모은다. 경로마다 다른 것 —
게이트(재시도·EAV 패턴 유무)·입력 모양(멀티는 미매핑 필드를 따로 받는다)·지표 필드 수집·
로그 문구(`DB '%s'` 접두)·state 반영(반환 dict vs `form_fill_out`)·조립기 호출 — 은 호출부에 남긴다.

조립기 `build_form_fill_pivot_sql`·월 시리즈 인식 `recognize_month_series`·`get_domain_by_id`·
기간 해석 `resolve_stat_month_range`는 여기서 부르지 않는다 — 테스트가 노드 모듈
네임스페이스에서 바꿔 끼우는 seam이다(D-337 ②). 로그도 쓰지 않는다(로거 이름·문구·순서는
경로별 호출부 소유).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.db_adapters.polestar.assembler import (
    MonthSeries,
    apply_capacity_scope_rule,
    apply_remark_server_name_rule,
    filter_pivot_regular_entries,
    resolve_form_fill_answers,
)
from src.db_adapters.time_hint import metric_period
from src.domain.query_time import QueryTime
from src.domain.time_spec import TimeResolution
from src.pipeline.prompt_blocks import split_mapping_entries

# 공유 빌더(prompt_blocks)에 주입하는 폴스타 리터럴 — 공용 빌더로 옮기지 않고(D-088) 어댑터가
# 정본을 소유한다(D-089). 두 노드가 같은 `_` 이름으로 import해 쓴다(plans/169 N1(b)).
ENTITY_RESOURCE_TYPE = "server.Server"     # 엔티티(서버) 자신의 resource_type
EAV_HOST_ATTRIBUTE = "Hostname"            # 브릿지 조인 예시의 엔티티 식별 속성
EAV_LINK_COLUMN = "configuration_id"       # config 행끼리 잇는 컬럼
SCHEMA_EXAMPLE_TABLE = "cmm_resource"      # 스키마 한정 규칙 예시 테이블
FOREIGN_SCHEMA_PREFIX = "polestar."        # 붙이지 말아야 할 접두사 예시

_DEFAULT_ENTITY_TABLE = "cmm_resource"     # EAV 패턴에 entity_table이 없을 때의 엔티티 테이블
_METRIC_STAT_TABLE = "cmm_metric_stat"     # 사용률 통계 테이블 접두(_h/_d/_m)


def drop_llm_inferred_mappings(
    column_mapping: dict[str, Any],
    mapping_sources: Mapping[str, str] | None,
) -> list[str]:
    """폼필에서 llm_inferred 출처 매핑을 채움에서 뺀다(D-149) — 매핑을 제자리에서 None으로 바꾼다.

    라이브 오염(TPMC·acl_id·epoch류)의 공통 출처다. 키는 유지한다(월 시리즈 인식·결합 규칙이
    필드명을 본다). 발동 게이트와 로그·state 반영은 호출부가 정한다.

    Returns:
        None으로 바꾼 필드명 목록(매핑 순서)
    """
    sources = mapping_sources or {}
    dropped = [
        f for f, c in column_mapping.items()
        if c and sources.get(f) == "llm_inferred"
    ]
    for f in dropped:
        column_mapping[f] = None
    return dropped


def month_series_rule_updates(
    rule_mapping: dict[str, str | None],
    attr_rt: dict[str, str],
    eav_pattern: dict[str, Any] | None,
    month_series: MonthSeries,
) -> tuple[dict[str, Any], dict[str, str | None]]:
    """월 시리즈 양식의 요청 스코프 규칙(처리능력 용량·비고 등록명 — D-146/D-148)을 산출한다.

    Args:
        rule_mapping: 규칙이 필드명을 훑을 매핑(멀티는 미매핑 필드를 합친 매핑)
        attr_rt: `eav_attr_resource_types` 결과
        eav_pattern: 첫 EAV 패턴(없으면 기본 엔티티 테이블)
        month_series: 인식된 월 시리즈

    Returns:
        (로컬 매핑에 덮을 패치, state 매핑 갱신분). 갱신분은 처리능력 규칙값에 더해 월 시리즈·
        비고 필드를 **강제 None**으로 둔다 — writer가 필드명(=SELECT alias=행 키)으로 조회하게
        하기 위함(라이브 실측 2026-07-28 3차: N:1 metric 매핑의 역매핑이 6칼럼에 동일값 복제).
    """
    cap = apply_capacity_scope_rule(rule_mapping, attr_rt, month_series.resource_type)
    remark = apply_remark_server_name_rule(
        rule_mapping,
        (eav_pattern or {}).get("entity_table", _DEFAULT_ENTITY_TABLE),
    )
    mapping_patch: dict[str, Any] = {**cap, **remark}
    state_updates: dict[str, str | None] = {
        **cap,
        **{f: None for f in month_series.fields},
        **{f: None for f in remark},
    }
    return mapping_patch, state_updates


def resolve_answer_overrides(
    answers: dict[str, dict[str, Any]],
    schema_info: dict[str, Any] | None,
    eav_pattern: dict[str, Any] | None,
    month_series: MonthSeries | None,
    concat_eav: list[tuple[str, str, str]],
) -> tuple[
    dict[str, dict[str, Any]], dict[str, str], dict[str, str], list[tuple[str, str, str]]
]:
    """사용자 답변 오버라이드(D-151)를 확정하고 결합 규칙에서 덮인 필드를 뺀다.

    월 시리즈 필드는 답변으로 덮지 못한다(구조 채움 영역). 오버라이드·직접입력 필드는 결합
    규칙에서 제외한다(중복 alias·사용자 층 우선).

    Returns:
        (오버라이드 전량, 매핑 갱신분, 상수 기입분, 걸러진 결합 목록)
    """
    protected = set(month_series.fields) if month_series else set()
    overrides, ov_map, literals = resolve_form_fill_answers(
        answers, schema_info, eav_pattern, protected_fields=protected,
    )
    ov_fields = set(ov_map.keys()) | set(literals.keys())
    concat_eav = [c for c in concat_eav if c[0] not in ov_fields]
    return overrides, ov_map, literals, concat_eav


def split_pivot_mapping(
    column_mapping: Mapping[str, str | None],
    *,
    exclude_fields: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """매핑을 정규(직접 컬럼)·EAV·성능지표 묶음으로 가른다.

    성능 지표(사용률) 매핑 — 사용률 통계 테이블(`_h/_d/_m`) 컬럼 — 은 정규 묶음에서 뺀다.
    CPU/메모리 사용률은 통계 테이블의 resource_type + definition_name 피벗으로만 얻을 수 있어
    단일 field→table.column 매핑이 불가능하다(D-066 후속/RC2).

    Args:
        column_mapping: 필드-컬럼 매핑(None 값은 어느 묶음에도 들지 않는다)
        exclude_fields: 세 묶음 모두에서 뺄 필드(단일 경로의 Vendor+Model 결합 필드)

    Returns:
        (정규 [(필드, 컬럼)], EAV [(필드, 속성명)], 성능지표 [(필드, 컬럼)])
    """
    regular, eav = split_mapping_entries(
        {f: c for f, c in column_mapping.items() if f not in exclude_fields}
    )
    metric = [(f, c) for f, c in regular if _METRIC_STAT_TABLE in c.lower()]
    regular = [(f, c) for f, c in regular if _METRIC_STAT_TABLE not in c.lower()]
    return regular, eav, metric


@dataclass(frozen=True)
class FormFillPivotPlan:
    """조립기 `build_form_fill_pivot_sql`에 넘길 피벗 입력 — 스키마 검증·식별 컬럼 주입 후.

    지표 필드·엔진·스키마·LIMIT·`stat_month`는 경로별 호출부가 정한다. `stat_month`의 표면어
    해석(`resolve_stat_month_range`)은 테스트가 노드 모듈에서 바꿔 끼우는 seam이고, 그 폴백
    계측 로그가 아래 `dropped` 경고 뒤에 나와야 하므로 호출부에 남긴다.
    """

    regular_entries: list[tuple[str, str]]
    server_eav: list[tuple[str, str]]
    child_eav: list[tuple[str, str, str]]
    eav_pattern: dict[str, Any]
    concat_eav: list[tuple[str, str, str]] | None
    month_measures: list[tuple[str, str, str, str]] | None
    period: TimeResolution | None
    #: 스키마에 없어 SELECT에서 뺀 직접 컬럼 — 호출부가 경고하고 state 매핑을 None으로 둔다(FIX-25)
    dropped: list[tuple[str, str]]


def plan_form_fill_pivot(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict[str, Any],
    *,
    schema_info: dict[str, Any] | None,
    concat_eav: list[tuple[str, str, str]],
    month_series: MonthSeries | None,
    form_intent: bool,
    query_time: QueryTime | None,
) -> FormFillPivotPlan:
    """폼필 결정적 피벗의 입력을 확정한다(D-068·D-146·D-148·D-149).

    - 환각 매핑 칼럼(스키마 부재)이 결정적 SELECT에 들어가면 쿼리 전체가 죽는다(라이브 실측:
      구분→cmm_resource.category). 스키마 검증 + 검증 불가 시 entity 안전 화이트리스트로
      결정적으로 차단한다(FIX-5/FIX-13).
    - 월 피벗/폼필인데 서버 식별 컬럼이 하나도 없으면 행 대조가 불가능하다 — 결정적 식별 컬럼을
      주입한다(alias는 양식 헤더와 무충돌인 라틴명 — writer가 무시하고 병합·진단에만 쓰임).
    - 요청 시간 해석이 있으면(plans/122 T-4) 성능 통계 해석을 `period`로 넘긴다. 「현재·지금」+
      기간 미지정은 None(기본값 강제 안 함 — `metric_period`).
    """
    entity = eav_pattern.get("entity_table", _DEFAULT_ENTITY_TABLE)
    regular_entries, dropped = filter_pivot_regular_entries(
        regular_entries, schema_info, entity
    )
    if (month_series or form_intent) and not regular_entries and not server_eav and not concat_eav:
        regular_entries = [
            ("server_name", f"{entity}.name"), ("hostname", f"{entity}.hostname"),
        ]
    return FormFillPivotPlan(
        regular_entries=regular_entries,
        server_eav=server_eav,
        child_eav=child_eav,
        eav_pattern=eav_pattern,
        concat_eav=concat_eav or None,
        month_measures=month_series.measures if month_series else None,
        period=metric_period(query_time),
        dropped=dropped,
    )

