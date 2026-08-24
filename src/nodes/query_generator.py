"""SQL 생성 노드.

LLM을 사용하여 사용자 요구사항과 스키마 정보를 기반으로
SQL SELECT 쿼리를 자동 생성한다.
재시도 시 이전 에러 메시지를 반영하여 수정된 SQL을 생성한다.
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from src.utils.llm_compat import is_kbgenai
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.db_adapters import get_adapter
from src.security.pii_filter import (
    diagnose_blocked_prompt,
    is_filter_blocked,
    is_scrub_samples_enabled,
    scrub_pii,
)
from src.state import AgentState
from src.routing.domain_config import get_domain_by_id
from src.utils.query_gen_common import (
    build_generic_period_hint,
    build_prior_rows_block,
    build_stat_month_block,
    correct_servername_hostname_mapping,
    eav_value_cast_columns,
    enforce_all_query_limit,
    extract_sql_from_response,
    normalize_eav_numeric_casts,
    resolve_effective_limit,
    resolve_stat_month_range,
    template_context_text,
)
# 단일/멀티 경로 공유 프롬프트 블록 빌더(Plan 69 P3-1, D-066). 폴스타 스키마 리터럴은
# 공용 빌더에 두지 않고 이 파일이 인자로 주입한다(D-088 — overfit 기준선은 호출부 기준).
from src.nodes.prompt_blocks import (
    EAV_JOIN_RULE_BLOCK,
    build_eav_pivot_block,
    build_forbidden_join_block,
    build_query_examples,
    build_schema_prefix_rule,
    build_stepwise_deps,
    build_unmapped_fields_block,
    build_value_index_injection,
    build_value_joins_block,
    eav_patterns_of,
    estimate_prompt_tokens,
    filter_mapping_by_schema,
    first_eav_pattern,
    format_schema_text,
    path_parity_enabled,
    prior_server_scope,
    resolve_prompt_token_budget,
    select_history_fewshot,
    split_eav_by_resource_type,
    split_mapping_entries,
)
# 폴스타 EAV/피벗 결정적 조립기는 어댑터로 이동(Plan 63 P2, D-089) — application 직접 임포트.
# 아래 4종은 `DBAdapter` 훅 표면(owns/system_template/validator_checks/classify_metric_field)에
# 대응하는 훅이 없어 직접 임포트로 남긴다. 새 훅 신설은 두 번째 어댑터가 생기기 전까지 금지
# (Plan 63 §9 어댑터 과설계 금지) — 훅이 있는 `classify_metric_field`만 레지스트리 경유로 옮겼다.
from src.db_adapters.polestar.assembler import (
    METRIC_PIVOT_KEYS,
    METRIC_PIVOT_TABLE,
    apply_capacity_scope_rule,
    apply_remark_server_name_rule,
    build_form_fill_candidates,
    build_form_fill_pivot_sql,
    build_month_series_block,
    build_multi_resource_pivot_block,
    decimal_cast_example,
    eav_attr_resource_types,
    filter_pivot_regular_entries,
    find_vendor_model_concat,
    recognize_month_series,
    resolve_form_fill_answers,
)
from src.nodes.candidate_generator import classify_complexity
from src.nodes.semantic_compiler import compile_from_nl
# 지표 필드 분류는 어댑터 레지스트리 경유 도구를 쓴다(D-089). 검증 코어가 도구 계층으로
# 내려가 tools→nodes 역참조가 사라졌으므로 모듈 수준 임포트가 안전하다(후속 2단계).
from src.tools.metrics import classify_metric_field
from src.schema_cache.form_memory import load_form_memory_answers
from src.utils.schema_utils import safe_sample_preview
from src.utils.synonym_usage import extract_synonym_usage

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 플래그 ON 경로에서만 수행한다.
    from src.nodes.column_deriver import StepwiseDeps

logger = logging.getLogger(__name__)

# 공유 빌더에 주입하는 DB 특화 리터럴 — 공용 빌더(prompt_blocks)로 옮기지 않고 호출부인
# 이 파일에 남긴다(D-088: 공용 계층 DB-agnostic, overfit_check 기준선은 파일 단위).
_ENTITY_RESOURCE_TYPE = "server.Server"     # 엔티티(서버) 자신의 resource_type
_EAV_HOST_ATTRIBUTE = "Hostname"            # 브릿지 조인 예시의 엔티티 식별 속성
_EAV_LINK_COLUMN = "configuration_id"       # config 행끼리 잇는 컬럼
_SCHEMA_EXAMPLE_TABLE = "cmm_resource"      # 스키마 한정 규칙 예시 테이블
_FOREIGN_SCHEMA_PREFIX = "polestar."        # 붙이지 말아야 할 접두사 예시


def _format_structure_guide(
    structure_meta: dict,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    query_history_examples: list[dict] | None = None,
) -> str:
    """구조 분석 메타데이터에서 쿼리 가이드를 포맷한다.

    structure_meta의 query_guide 문자열을 기반으로,
    EAV 패턴이 감지된 경우 resource_type/eav_name 유사단어 정보를 추가한다.

    Args:
        structure_meta: schema_info["_structure_meta"] 딕셔너리
        resource_type_synonyms: RESOURCE_TYPE 유사단어 매핑 (선택)
        eav_name_synonyms: EAV NAME 유사단어 매핑 (선택)
        query_history_examples: 질의 이력에서 선택된 few-shot 예시 (선택, N2).
            주어지면 프로필 고정 예시 대신 이 예시로 블록을 만든다.

    Returns:
        포맷된 가이드 텍스트
    """
    guide = structure_meta.get("query_guide", "")

    # EAV 패턴 존재 여부 확인
    eav_patterns = eav_patterns_of(structure_meta)

    # EAV 패턴이 있으면 조인 규칙 지침을 앞에 삽입 — guide가 빈 프로필에서도 금지
    # 규칙 자체는 유효하다(빈 guide 시 규칙이 통째로 빠지던 결함 수정, Plan 69 P0-③).
    if eav_patterns:
        guide = EAV_JOIN_RULE_BLOCK + guide

    # RESOURCE_TYPE 유사단어 추가
    if resource_type_synonyms:
        guide += "\n\n### RESOURCE_TYPE 유사 단어\n"
        for rt_value, syns in resource_type_synonyms.items():
            guide += f"  - {rt_value} (유사: {', '.join(syns)})\n"

    # EAV 속성명 유사단어 추가 (EAV 패턴이 있을 때만)
    if eav_name_synonyms and eav_patterns:
        guide += "\n\n### EAV 속성명 유사 단어\n"
        for attr, syns in eav_name_synonyms.items():
            guide += f"  - {attr} (유사: {', '.join(syns)})\n"

    # EAV 패턴의 value_joins 정보를 쿼리 가이드에 추가
    for eav_p in eav_patterns:
        guide += build_value_joins_block(eav_p)

    # 샘플 데이터 정보
    samples = structure_meta.get("samples", {})
    if samples:
        for purpose, rows in samples.items():
            if isinstance(rows, list) and rows:
                guide += f"\n\n### {purpose}\n"
                for row in rows[:10]:
                    guide += f"  - {row}\n"

    # 금지 JOIN 컬럼 경고
    guide += build_forbidden_join_block(structure_meta.get("patterns", []))

    # 쿼리 예시 (few-shot) — 질문→SQL 쌍을 직접 제시하여 LLM 환각 방지.
    # 멀티 DB 경로(multi_db_executor)와 동일 출처를 쓰도록 공용 헬퍼로 분리(D-066).
    # N2(D-133): 이력 검색이 유사 예시를 골라오면 고정 예시 대신 그것을 쓴다.
    guide += build_query_examples(structure_meta, query_history_examples)

    return guide


def _prior_server_scope(state: AgentState) -> Optional[tuple[str, list[str]]]:
    """선행 task 결과(prior_rows)에서 결정적 서버 스코프를 뽑는다 (D-099).

    시맨틱 컴파일러에 넘겨 HAVING 집계 필터로 강제한다 — 프롬프트 지시(build_prior_rows_block)에
    의존하면 LLM이 WHERE 배치·모순 alias 등 변종을 생성해 침묵 0건/오답이 반복된다(D-096·D-098).

    Args:
        state: 현재 에이전트 상태

    Returns:
        (식별컬럼, 값목록) 또는 None(선행 스코프 없음)
    """
    return prior_server_scope(state.get("prior_rows"))


def _try_build_form_fill_pivot_sql(
    state: AgentState,
    limit_value: int,
    user_query: str,
    *,
    adapter_db_ids: set[str] | None = None,
) -> Optional[dict]:
    """폼필 결정적 피벗 SQL 조립 — 자식 리소스 EAV·월 시리즈·양식 업로드(D-068/D-146/D-149).

    프롬프트로 스켈레톤을 "제안"하면 LLM이 프로필 few-shot(월별 GROUP BY 등)과 경쟁해 무시·변형
    (서버 중복·config 누락)한다. well-defined 폼필 쿼리는 코드가 직접 조립해 LLM을 우회한다
    (D-068 2차). 양식 업로드(template_structure) 턴은 월 시리즈·자식 EAV가 없어도 항상 결정적
    조립한다(D-149 — 경로 선택이 per-DB LLM 매핑에 종속돼 LLM 폴백이 GROUP BY 계약을 깨던
    라이브 실측의 근본 수정). 해당 케이스가 아니면 None(LLM 경로 유지).

    Args:
        adapter_db_ids: 어댑터 담당 db_id 집합 — 지표 필드 분류를 레지스트리로 디스패치한다.

    Returns:
        {"sql": str, "month_anchor": dict|None, "mapping_updates": dict, "candidates": list,
        "overrides": dict, "literals": dict} 또는 None.
        month_anchor는 M~M+max 실제 월(응답 명시 §2.4)·인식 필드 목록(D-147 사유 제외용),
        mapping_updates는 요청 스코프 규칙(처리능력 GB 등)의 매핑 갱신분(state 반영용).
    """
    column_mapping = state.get("column_mapping")
    if not column_mapping:
        return None
    form_intent = bool(state.get("template_structure"))
    schema_info = state.get("schema_info") or {}
    eav_pattern = _get_eav_pattern(schema_info)
    active_db_id = state.get("active_db_id") or ""
    # 서버명/서버이름류가 EAV Hostname으로 오매핑되면 등록명 컬럼으로 결정적 교정(프로필 확정 규칙).
    # state를 오염시키지 않도록 사본에 적용.
    column_mapping = dict(column_mapping)
    if eav_pattern:
        correct_servername_hostname_mapping(column_mapping, eav_pattern.get("entity_table", ""))
    attr_rt = eav_attr_resource_types(schema_info)

    # 폼필에서 llm_inferred 매핑은 채움에 쓰지 않는다(D-149) — 라이브 오염(TPMC·acl_id·
    # epoch류)의 공통 출처. 유사어·힌트 출처와 아래 확정 규칙만 채움 허용, 나머지는
    # 공란+사유(역질문 후보). 키는 유지(월 시리즈 인식·결합 규칙이 필드명을 본다).
    _inferred_dropped: list[str] = []
    if form_intent:
        _sources = state.get("mapping_sources") or {}
        _inferred_dropped = [
            f for f, c in column_mapping.items()
            if c and _sources.get(f) == "llm_inferred"
        ]
        for f in _inferred_dropped:
            column_mapping[f] = None
        if _inferred_dropped:
            logger.info(
                "폼필 llm_inferred 매핑 %d건 채움 제외(D-149, 역질문 후보): %s",
                len(_inferred_dropped), _inferred_dropped,
            )

    # 월 시리즈(가로 6개월 등) 인식 + 요청 스코프 규칙(D-146/D-148). 인식 실패는 폴백(무발동).
    month_series = recognize_month_series(
        column_mapping,
        context_text=template_context_text(state.get("template_structure")),
        user_query=user_query,
    )
    mapping_updates: dict[str, Optional[str]] = {}
    # 채움 제외된 llm_inferred 필드는 state 매핑도 None으로 — writer가 낡은 매핑으로
    # 역조회해 무관 칼럼 값을 채우는 것을 차단(엄격 필드명 조회로 공란 보장).
    # 이후 확정 규칙(_cap 등)이 같은 필드를 갱신하면 규칙이 우선한다(dict.update 순서).
    mapping_updates.update({f: None for f in _inferred_dropped})
    if month_series:
        _cap = apply_capacity_scope_rule(
            column_mapping, attr_rt, month_series.resource_type
        )
        _remark = apply_remark_server_name_rule(
            column_mapping,
            (eav_pattern or {}).get("entity_table", "cmm_resource"),
        )
        column_mapping.update(_cap)
        column_mapping.update(_remark)  # SQL은 등록명 SELECT — 비고 규칙(사용자 확정)
        mapping_updates.update(_cap)
        # 월 시리즈·비고 필드는 state 매핑을 **강제 None**으로 갱신한다 — writer가 필드명
        # (=SELECT alias=행 키)으로 조회하게 하기 위함. 라이브 실측(2026-07-28 3차):
        # field_mapper가 월 필드 전부를 같은 칼럼(cmm_metric_stat_m.avg_val)으로 매핑해
        # 두면 writer 역매핑(N:1의 마지막 필드)이 6칼럼에 동일값(M+5)을 복제한다.
        mapping_updates.update({f: None for f in month_series.fields})
        mapping_updates.update({f: None for f in _remark})

    # 제조사(모델명)류는 Vendor+Model 결합으로 채운다(D-148 — 라이브 실측: 한쪽만 매핑돼
    # 반쪽 값). 결합 대상 필드는 단독 EAV/직접 컬럼 파티션에서 제외(중복 alias 방지).
    concat_eav = find_vendor_model_concat(schema_info, column_mapping)
    concat_fields = {c[0] for c in concat_eav}
    if month_series and concat_fields:
        # 결합 필드도 행 키=필드명 조회 강제(잔존 EAV:Vendor류 매핑의 오조회 방지)
        mapping_updates.update({f: None for f in concat_fields})

    # 사용자 답변 오버라이드(D-151) — 우선순위 최상위(사용자 > 확정 규칙 > 자동 매핑).
    # 규칙·결합 산출 뒤에 적용해 같은 필드는 사용자 답이 이긴다. 검증(존재성)은
    # resolve_form_fill_answers가 수행하고, 탈락분은 사유와 함께 반환돼 응답에 노출된다.
    overrides_out: dict[str, dict] = {}
    literals_out: dict[str, str] = {}
    _answers = state.get("form_fill_answers") if form_intent else None
    if _answers:
        _protected = set(month_series.fields) if month_series else set()
        overrides_out, _ov_map, literals_out = resolve_form_fill_answers(
            _answers, schema_info, eav_pattern, protected_fields=_protected,
        )
        _applied = [f for f, o in overrides_out.items() if o.get("applied")]
        _rejected = [(f, o.get("reason")) for f, o in overrides_out.items() if not o.get("applied")]
        if _applied:
            logger.info("폼필 답변 오버라이드 적용(D-151): %s", _applied)
        if _rejected:
            logger.info("폼필 답변 오버라이드 거부(D-151): %s", _rejected)
        column_mapping.update(_ov_map)
        mapping_updates.update(_ov_map)
        # 오버라이드/직접입력 필드는 결합 규칙에서 제외(중복 alias·사용자 층 우선)
        _ov_fields = set(_ov_map.keys()) | set(literals_out.keys())
        concat_eav = [c for c in concat_eav if c[0] not in _ov_fields]
        concat_fields = {c[0] for c in concat_eav}

    eav_entries = [
        (f, c[4:]) for f, c in column_mapping.items()
        if c and c.startswith("EAV:") and f not in concat_fields
    ]
    child_eav = [
        (f, a, attr_rt[a.upper()])
        for f, a in eav_entries
        if a.upper() in attr_rt and attr_rt[a.upper()] != _ENTITY_RESOURCE_TYPE
    ]
    # D-149 게이트: 자식 EAV·월 시리즈 외에 양식 업로드(form_intent) 자체가 발동 조건.
    # eav_pattern 부재 DB(비폴스타)는 발동하지 않는다(현행 LLM 경로 유지).
    if not child_eav and not month_series and not form_intent:
        return None
    if not eav_pattern:
        return None
    if form_intent and not child_eav and not month_series:
        logger.info(
            "폼필 결정적 계약 경로(D-149): 월시리즈·자식EAV 없음 — 게이트 확장으로 조립"
        )
    server_eav = [
        (f, a) for f, a in eav_entries
        if a.upper() not in attr_rt or attr_rt[a.upper()] == _ENTITY_RESOURCE_TYPE
    ]
    # 직접 컬럼(server.Server) — 사용률 통계 컬럼은 제외(미매핑으로 접힘)
    regular_entries = [
        (f, c) for f, c in column_mapping.items()
        if c and not c.startswith("EAV:") and "cmm_metric_stat" not in c.lower()
        and f not in concat_fields
    ]
    # 환각 매핑 칼럼(스키마에 없는 칼럼)이 결정적 SELECT에 유입되면 쿼리 전체가 죽는다
    # (라이브 실측: 구분→cmm_resource.category). 스키마 검증 + 검증 불가 시 entity
    # 안전 화이트리스트로 결정적 차단(FIX-5/FIX-13).
    regular_entries, _dropped = filter_pivot_regular_entries(
        regular_entries, schema_info, eav_pattern.get("entity_table", "cmm_resource")
    )
    if _dropped:
        logger.warning(
            "폼필 결정적 피벗: 스키마에 없는 매핑 칼럼 %d건 제외(환각 매핑 차단) — %s",
            len(_dropped), _dropped,
        )
        # 불변식(FIX-25): SQL SELECT에서 제외된 필드는 state 매핑도 None — 낡은 매핑이
        # 남으면 writer의 유연 조회(부분 매칭 등)가 무관한 행 키(예: 'IP')로 오채움한다
        # (라이브 실측: 비고=IP값. 월/결합/llm_inferred 강제 None과 동일 패턴).
        mapping_updates.update({f: None for f, _c in _dropped})
    # 월 피벗/폼필인데 서버 식별 컬럼이 하나도 없으면 행 대조가 불가능 — 결정적 식별 컬럼 주입
    # (alias는 양식 헤더와 무충돌인 라틴명 — writer가 무시하고 병합·진단에만 쓰임).
    if (month_series or form_intent) and not regular_entries and not server_eav and not concat_eav:
        entity = eav_pattern.get("entity_table", "cmm_resource")
        regular_entries = [
            ("server_name", f"{entity}.name"), ("hostname", f"{entity}.hostname"),
        ]
    month_fields = set(month_series.fields) if month_series else set()
    # 적용된 오버라이드(공란/직접입력 포함)는 metric 회수 대상에서 제외 — 사용자 층 우선
    _applied_ov = {f for f, o in overrides_out.items() if o.get("applied")}
    metric_fields = [
        f for f, c in column_mapping.items()
        if c is None and f not in month_fields and f not in concat_fields
        and f not in _applied_ov and classify_metric_field(
            f, db_id=active_db_id, adapter_db_ids=adapter_db_ids
        )
    ]
    domain = get_domain_by_id(active_db_id)
    db_schema = domain.db_schema if domain else ""
    sql = build_form_fill_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        metric_fields=metric_fields,
        db_engine=state.get("active_db_engine"),
        db_schema=db_schema,
        limit=limit_value,
        # 폼필 피벗도 기간 2단 폴백에 **포함**한다(R3-(i), 2026-07-30 결정 변경). 제외하면
        # "지난 반년 + 양식 첨부"처럼 표면어가 미매칭인 질의에서 stat_date 필터가 통째로 빠져
        # 전 기간 평균으로 침묵 왜곡된다(D-099 계열). 멀티 경로 폼필 피벗도 동형(D-066).
        stat_month=resolve_stat_month_range(
            user_query,
            parsed_time_range=(state.get("parsed_requirements") or {}).get("time_range"),
        ),
        month_measures=month_series.measures if month_series else None,
        concat_eav=concat_eav or None,
    )
    month_anchor = None
    if month_series:
        month_anchor = {
            "start": month_series.anchor[0],
            "end": month_series.anchor[1],
            "resource_type": month_series.resource_type,
            "fields": month_series.fields,
        }
        logger.info(
            "폼필 월 시리즈 인식(D-146): rt=%s, 기간=%s~%s, 필드=%d개",
            month_series.resource_type, month_series.anchor[0],
            month_series.anchor[1], len(month_series.fields),
        )
    return {
        "sql": sql,
        "month_anchor": month_anchor,
        "mapping_updates": mapping_updates,
        # HITL 폼필(D-151): 역질문 드롭다운 후보(스키마 실측) + 답변 적용/거부 내역 + 상수
        "candidates": build_form_fill_candidates(schema_info, eav_pattern) if form_intent else [],
        "overrides": overrides_out,
        "literals": literals_out,
    }


@dataclass(frozen=True)
class _GenContext:
    """``query_generator`` 단계 함수들이 공유하는 준비 결과 (Plan 69 P5-2).

    노드 본체가 트랙 분기만 남기도록, 설정·LLM·결정적으로 해석된 값(상한·기간·스코프)을
    한 번에 모아 단계 함수에 넘긴다.
    """

    llm: BaseChatModel
    app_config: AppConfig
    user_query: str
    retry_count: int
    is_retry: bool
    limit_value: int
    stat_month: Any
    stat_block_db: bool
    conversation_context: Optional[dict]
    prior_scope: Optional[tuple[str, list[str]]]
    adapter_db_ids: Optional[set[str]]


def _prepare(
    state: AgentState,
    llm: BaseChatModel | None,
    app_config: AppConfig | None,
) -> _GenContext:
    """설정·LLM을 확정하고 질의에서 결정적으로 해석 가능한 값을 모두 뽑는다."""
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    # 재시도 횟수 관리
    retry_count = state.get("retry_count", 0)
    is_retry = bool(state.get("error_message"))
    if is_retry:
        retry_count += 1

    # 모든/전체 조회 쿼리인 경우 LIMIT 값을 높여 1000건 제한 우회 (멀티 DB 경로와 공용, D-066)
    user_query = state.get("user_query", "") or ""
    # 표면어 정규식이 미매칭일 때만 input_parser LLM 산출물(time_range/limit)로 2단 폴백한다
    # (Plan 67 R3-(i)). 종전에는 이 두 값이 계산만 되고 SQL 경로에서 소비되지 않아 "지난 반년"·
    # "100개만" 류가 침묵 소실됐다. 정규식이 매칭되면 폴백은 발동하지 않는다(동작 불변).
    _parsed_req = state.get("parsed_requirements") or {}
    # 승격된 원문 기준 resolved_limit 우선(Plan 75 §3 — 오케스트레이션의 정제 질의가
    # "모든" 등 수량 한정어를 탈락시켜도 state로 운반된 값을 신뢰). 없으면 표면어 →
    # input_parser LLM 산출물(limit) 순 2단 폴백(R3-(i)). 멀티 경로와 동일 규칙(D-066).
    limit_value = resolve_effective_limit(
        state, user_query, app_config.query.default_limit,
        parsed_limit=_parsed_req.get("limit"),
    )
    # 기간 표현(지난 N개월/지난달 등)의 결정적 해석 — 트랙 C 컴파일과 LLM 폴백 프롬프트가 공유
    stat_month = resolve_stat_month_range(
        user_query, parsed_time_range=_parsed_req.get("time_range")
    )
    # 통계 테이블 강제 블록(build_stat_month_block)은 폴스타 월 통계 테이블(cmm_metric_stat_m)
    # 규약에 특화된 지시라, 그 테이블을 선언한 DB에만 주입한다(L2 일반화, P1-3/D-088). 현재는
    # 폴스타가 유일한 선언 DB이므로 폴스타 게이트(폴스타 시스템 템플릿과 동일 신호)로 판정하고,
    # 프로필 time_grain 선언 기반 전환은 P3(D-090). 프로필 부재 DB는 미주입 — 시스템 템플릿의
    # 일반 기간 규칙(CURRENT_DATE 동적 계산)만 남아 LLM이 스키마의 시간 컬럼으로 해석한다.
    polestar_db_ids = app_config.get_polestar_db_ids() or set()
    return _GenContext(
        llm=llm,
        app_config=app_config,
        user_query=user_query,
        retry_count=retry_count,
        is_retry=is_retry,
        limit_value=limit_value,
        stat_month=stat_month,
        stat_block_db=state.get("active_db_id") in polestar_db_ids,
        # 멀티턴 맥락에서 이전 SQL 참조
        conversation_context=state.get("conversation_context"),
        # prior_rows(선행 task 결과 스코프)는 컴파일러에 server_scope로 결정적 전달한다(D-099).
        # 과거에는 SMQ가 스코프를 표현하지 못해 우회했으나(D-086), 이제 조립기가 HAVING으로
        # 강제하므로 이 형태(선행 스코프 + 메트릭 순위 + EAV 속성)도 결정적 조립 대상이다.
        prior_scope=_prior_server_scope(state),
        adapter_db_ids=polestar_db_ids or None,
    )


def _try_deterministic(state: AgentState, ctx: _GenContext) -> Optional[dict]:
    """폼필 다중 리소스 피벗을 코드가 결정적으로 조립한다(LLM 우회, 해당 없으면 None).

    재시도(에러 컨텍스트) 시엔 결정적 SQL이 이미 실패했을 수 있으므로 진입하지 않고
    LLM 폴백이 에러를 반영해 수정하게 한다.

    Returns:
        폼필 산출 dict({"sql", "month_anchor", "mapping_updates", "candidates",
        "overrides", "literals"}) 또는 None — 호출부가 sql 외 키를 state 델타로 승격한다.
    """
    if ctx.is_retry:
        if state.get("template_structure"):
            # 침묵 금지: 폼필 턴에서 결정적 조립이 재시도 사유로 스킵되어 LLM 폴백이
            # 양식을 처리하는 시점을 로그로 가시화(라이브 실측: 이 경로가 월 방향
            # 뒤집힘의 근원 — ux_improvement 승계).
            logger.info(
                "폼필 결정적 조립 스킵 — 재시도 턴(error=%s). LLM 폴백이 양식 처리",
                str(state.get("error_message") or "")[:120],
            )
        return None
    form_fill = _try_build_form_fill_pivot_sql(
        state, ctx.limit_value, ctx.user_query, adapter_db_ids=ctx.adapter_db_ids,
    )
    if form_fill and form_fill.get("sql"):
        logger.info(
            "폼필 다중 리소스 피벗 SQL 결정적 조립(LLM 우회): %s",
            form_fill["sql"][:500],
        )
    return form_fill


async def _try_semantic(
    state: AgentState,
    ctx: _GenContext,
    deterministic_sql: Optional[str],
    derivation_sink: list[dict],
) -> tuple[Optional[str], bool]:
    """트랙 C(D-076) — 커버리지 내 정형 질의를 시맨틱 모델로 결정적 컴파일한다.

    폼필(deterministic_sql)·재시도가 아닐 때만 진입한다. 커버리지 밖이면 None → 호출부가
    LLM 폴백으로 진행한다(회귀 0). 삽입 지점 원칙(§3): query_generator 함수 내부 →
    그래프 경로(A)·orchestration 인라인(B)이 자동 공유한다.

    Returns:
        (semantic_sql, coverage_outside) — 트랙 C ON인데 커버리지 밖이면 두 번째가 True
        (3단 폴백에서 트랙 A 폴백 대상 판정에 쓰인다).
    """
    if (ctx.is_retry or deterministic_sql or state.get("column_mapping")
            or not ctx.app_config.text2sql.semantic_compose):
        return None, False
    value_index = (
        state.get("column_value_index")
        if ctx.app_config.synonym.value_retrieval else None
    )
    semantic_sql, _smq, _cov = await compile_from_nl(
        ctx.llm, ctx.user_query, state.get("active_db_id") or "",
        default_limit=ctx.limit_value,
        stat_month=ctx.stat_month,
        value_index=value_index,
        server_scope=ctx.prior_scope,
        app_config=ctx.app_config,
        stepwise_deps=_build_stepwise_deps(state, ctx.app_config, ctx.limit_value),
        derivation_sink=derivation_sink,
    )
    if semantic_sql:
        logger.info("시맨틱 결정적 컴파일 SQL(LLM 우회): %s", semantic_sql[:500])
    return semantic_sql, semantic_sql is None


async def _build_fallback_prompts(
    state: AgentState, ctx: _GenContext,
) -> tuple[str, str]:
    """트랙 A LLM 폴백에 쓸 (시스템, 사용자) 프롬프트를 조립한다.

    사용자 프롬프트는 기본 조립 뒤 조건부 블록(기간 강제·값 인덱스·선행 스코프)을 순서대로
    덧붙인다 — 이 순서가 곧 프롬프트 바이트라 sha256 골든의 판정 대상이다.
    """
    app_config = ctx.app_config
    is_retry, user_query = ctx.is_retry, ctx.user_query

    # N2(D-133): 폴백 프롬프트의 few-shot을 검증된 질의 이력에서 동적 선택한다.
    # 플래그 OFF·무적중이면 None → 기존 고정 few-shot 경로(바이트 무변경).
    history_examples = await _select_query_history_examples(
        state, user_query, app_config,
    )

    # 프롬프트 구성
    system_prompt = _build_system_prompt(
        schema_info=state["schema_info"],
        default_limit=ctx.limit_value,
        column_descriptions=state.get("column_descriptions", {}),
        column_synonyms=state.get("column_synonyms", {}),
        resource_type_synonyms=state.get("resource_type_synonyms"),
        eav_name_synonyms=state.get("eav_name_synonyms"),
        active_db_id=state.get("active_db_id"),
        polestar_db_ids=ctx.adapter_db_ids,
        active_db_engine=state.get("active_db_engine"),
        routing_intent=state.get("routing_intent"),
        query_history_examples=history_examples,
        path_parity=path_parity_enabled(app_config),
    )

    # 토큰 예산 가드(D-159, 단일·멀티 공통 후속 — W-6 예고분). 단일 경로는 relevant
    # 게이트로 이미 좁혀져 발동이 이례적이므로 강등만 하고 실패시키지는 않는다 —
    # 최종 초과분은 백엔드 예외 감지(멀티 D-159 FIX-C 대응)가 사후 방어한다.
    # 예산 내면 바이트 무변경(프롬프트 sha256 스냅샷 계약 유지).
    _budget = resolve_prompt_token_budget(app_config)
    if _budget and estimate_prompt_tokens(system_prompt) > _budget:
        _est_before = estimate_prompt_tokens(system_prompt)
        logger.warning(
            "[토큰예산] 단일 경로 초과(추정 %d > 예산 %d, db=%s) — 유사어·설명 재료 "
            "제거 후 재조립", _est_before, _budget, state.get("active_db_id"),
        )
        system_prompt = _build_system_prompt(
            schema_info=state["schema_info"],
            default_limit=ctx.limit_value,
            column_descriptions=None,
            column_synonyms=None,
            resource_type_synonyms=None,
            eav_name_synonyms=None,
            active_db_id=state.get("active_db_id"),
            polestar_db_ids=ctx.adapter_db_ids,
            active_db_engine=state.get("active_db_engine"),
            routing_intent=state.get("routing_intent"),
            query_history_examples=history_examples,
            path_parity=path_parity_enabled(app_config),
        )
        _est_after = estimate_prompt_tokens(system_prompt)
        if _est_after > _budget:
            # 재료 제거로도 초과 — 스키마 자체가 과대(스코프 미필터 캐시). 강등 사실과
            # 잔여 초과를 로그로 남긴다(단일 경로는 여기서 실패시키지 않음 — 위 주석).
            logger.error(
                "[토큰예산] 단일 경로 재료 제거 후에도 초과(추정 %d > 예산 %d, db=%s, "
                "테이블 %d개) — relevant 게이트/프로필 점검 필요",
                _est_after, _budget, state.get("active_db_id"),
                len((state["schema_info"] or {}).get("tables") or {}),
            )

    user_prompt = _build_user_prompt(
        parsed_requirements=state["parsed_requirements"],
        template_structure=state.get("template_structure"),
        error_message=state.get("error_message") if is_retry else None,
        previous_sql=state.get("generated_sql") if is_retry else None,
        column_mapping=state.get("column_mapping"),
        conversation_context=ctx.conversation_context,
        schema_info=state["schema_info"],
        db_engine=state.get("active_db_engine"),
        db_id=state.get("active_db_id"),
        adapter_db_ids=ctx.adapter_db_ids,
    )
    # 기간 표현이 있으면 결정적으로 해석된 단일 월(YYYYMM)을 강제한다 — 시스템 템플릿의
    # "CURRENT_DATE 동적 계산" 일반 규칙을 LLM이 따르면 BETWEEN으로 진행 중인 달까지
    # 포함하는 실측 오류가 있었다(D-076 후속4).
    _sm_block = build_stat_month_block(ctx.stat_month) if ctx.stat_block_db else ""
    if _sm_block:
        user_prompt += "\n\n" + _sm_block
    # 무선언(프로필 없음) DB: GENERIC_LLM_MAPPING 옵트인 시 범용 기간 힌트(폴스타 리터럴 없음).
    # 선언 우선 — 폴스타(stat_block_db)는 위 결정적 블록을 쓰므로 이 경로에 들어오지 않는다(P3/D-090).
    elif app_config.text2sql.generic_llm_mapping:
        _gp_block = build_generic_period_hint(ctx.stat_month)
        if _gp_block:
            user_prompt += "\n\n" + _gp_block

    # 폼필 월 시리즈 양식이 LLM 폴백으로 흐르는 경우(재시도 턴 등) — 인식기가 확정한
    # 필드↔YYYYMM 리터럴을 강제해 CURRENT_DATE 역방향 계산(월 뒤집힘 실측)을 차단(D-146).
    if state.get("template_structure"):
        _ms_block = build_month_series_block(recognize_month_series(
            state.get("column_mapping") or {},
            context_text=template_context_text(state.get("template_structure")),
            user_query=user_query,
        ))
        if _ms_block:
            user_prompt += "\n\n" + _ms_block

    # E5-2 값 검색 리터럴 주입 — value_retrieval ON + 인덱스 매칭 시만(회귀 0).
    _vi_block = _build_value_index_injection(state, user_query, app_config)
    if _vi_block:
        # 실 DB 값 리터럴이므로 PII 스크럽(D-155 후속3 — FabriX 필터 오탐 차단)
        if is_scrub_samples_enabled():
            _vi_block = scrub_pii(_vi_block)
        user_prompt += _vi_block

    # 선행 task 결과 서버 스코프 강제 — orchestration 데이터 의존(input_from) 경로(D-086).
    # prior_rows는 생성만 되고 소비처가 없던 죽은 배선이었다(2026-07-18 실측: 의존 task가
    # 알람 조건을 재표현하다 resource_type='alarm.Alarm' 환각으로 0건).
    _pr_block = build_prior_rows_block(state.get("prior_rows"))
    if _pr_block:
        # 실행 결과 라이브 행이므로 PII 스크럽(D-155 후속3 — 멀티 경로와 대칭)
        if is_scrub_samples_enabled():
            _pr_block = scrub_pii(_pr_block)
        user_prompt += "\n\n" + _pr_block

    return system_prompt, user_prompt


async def _llm_fallback(
    state: AgentState, ctx: _GenContext, coverage_outside: bool,
) -> tuple[str, Optional[list[dict]], Optional[dict], dict]:
    """트랙 A — 조립한 프롬프트로 LLM에서 SQL을 받는다(결정적 경로가 비었을 때).

    Returns:
        (sql, sql_candidates, text2sql_fallback, extra_return)
    """
    llm, app_config = ctx.llm, ctx.app_config
    is_retry, user_query = ctx.is_retry, ctx.user_query
    sql_candidates: list[dict] | None = None
    text2sql_fallback: dict | None = None
    extra_return: dict = {}

    system_prompt, user_prompt = await _build_fallback_prompts(state, ctx)

    # 트랙 A(E2~E4): 다중 후보 생성·선택. 재시도(에러 컨텍스트)에는 미진입(현행 단일 수정 경로).
    use_multi = (
        app_config.text2sql.multi_candidate and not is_retry
        and (not app_config.text2sql.complexity_gate
             or classify_complexity(
                 user_query, state.get("parsed_requirements"),
                 state.get("schema_info"),
             ) == "complex")
    )
    if use_multi:
        selection = await _run_multi_candidate_single_db(
            state, llm, app_config, system_prompt, user_prompt, user_query,
        )
        sql = selection["sql"]
        sql_candidates = selection.get("sql_candidates")
        text2sql_fallback = _decide_fallback_tier(
            coverage_outside, app_config, selection,
        )
        if text2sql_fallback and text2sql_fallback.get("tier") == "human_review":
            # 저신뢰 → 사람 검토 회부(HITL은 기존 approval_gate 재사용; 미활성 시 정보 필드로만).
            extra_return["awaiting_approval"] = True
            extra_return["approval_context"] = {
                "type": "text2sql_low_confidence",
                "sql": sql,
                "confidence": text2sql_fallback.get("confidence"),
                "reason": text2sql_fallback.get("reason"),
            }
        logger.info(
            "다중 후보 선택: method=%s conf=%.2f (%d 후보)",
            selection.get("method"), selection.get("confidence", 0.0),
            len(sql_candidates or []),
        )
    else:
        # LLM 호출 (현행 단일 경로 — 바이트 무변경)
        messages = [
            SystemMessage(content=system_prompt),
            # Insert dummy AIMessage when using KBGenAIChat to satisfy required order
            AIMessage(content="") if is_kbgenai(llm) else None,
            HumanMessage(content=user_prompt),
        ]
        # Remove any None entries (no effect for other LLMs)
        messages = [m for m in messages if m is not None]
        response = await llm.ainvoke(messages)

        # SQL 추출
        sql = extract_sql_from_response(response.content)

        # FabriX PII 필터 차단 응답(비-SQL) 감지 시 원인 블록·값을 즉시 특정한다(D-155).
        # 프롬프트 재료(system/user)를 모두 가진 유일한 지점 — 진단을 state
        # (pii_block_diagnosis)로 승격해 query_validator 에러 메시지에 노출한다
        # (폐쇄망은 로그 접근이 어려워 UI 노출이 1차 진단 채널 — D-153 후속2 원칙).
        if is_filter_blocked(raw_text=sql):
            _pii_diag = diagnose_blocked_prompt({
                "시스템 프롬프트(스키마·샘플·유사어)": system_prompt,
                "사용자 프롬프트(질의·매핑·컨텍스트)": user_prompt,
            })
            logger.warning(
                "[PII-FILTER] SQL 생성 응답 차단(db=%s, retry=%d) — 원인 후보: %s",
                state.get("active_db_id"), ctx.retry_count, _pii_diag,
            )
            extra_return["pii_block_diagnosis"] = _pii_diag

    # "모든/전체" 상향인데 LLM이 프로필 few-shot 예시의 말미 캡(FETCH FIRST 100 등)을
    # 모방한 경우 결정적 교정 — 2026-07-24 b0 실측(Plan 75 §5.1 항목 6, D-066 후속8).
    sql = enforce_all_query_limit(
        sql, ctx.limit_value, ctx.app_config.query.default_limit
    )
    # EAV 숫자 값 정수 캐스트 결정적 교정(D-160) — 멀티 경로와 동일 가드(D-066 대칭).
    # 값 컬럼 리터럴은 구조 메타 선언에서 도출한다(D-088).
    sql = normalize_eav_numeric_casts(
        sql, eav_value_cast_columns(first_eav_pattern(state.get("schema_info")))
    )

    return sql, sql_candidates, text2sql_fallback, extra_return


def _eav_attribute_columns(state: AgentState) -> Optional[list[str]]:
    """스키마 메타에서 EAV 속성 컬럼명을 모은다(없거나 실패하면 None).

    유사어 역조회의 **독립 신호**라 자체 try로 감싼다 — 여기서 실패해도 역조회 자체는
    속성 컬럼 없이 수행돼야 한다(Known Mistakes: 독립 신호 개별 try).
    """
    try:
        structure_meta = (state.get("schema_info") or {}).get("_structure_meta") or {}
        return [
            p["attribute_column"]
            for p in structure_meta.get("patterns", [])
            if p.get("type") == "eav" and p.get("attribute_column")
        ] or None
    except Exception as e:  # noqa: BLE001 — 메타 형식 이상은 속성 컬럼 없이 진행
        logger.warning("EAV 속성 컬럼 수집 실패(속성 컬럼 없이 역조회 진행): %s", e)
        return None


def _instrument_synonym_usage(state: AgentState, sql: str) -> Optional[dict]:
    """최종 SQL에 실제 반영된 유사어를 역조회하고 계측 로그를 남긴다.

    처리 현황 표시용이라 **실패해도 SQL 생성에는 영향이 없다**. 역조회·매핑 로그·미등록
    리터럴 로그는 서로 독립 신호이므로 각각 별도 try로 감싸 하나가 깨져도 나머지가 남는다.
    """
    try:
        usage = extract_synonym_usage(
            sql,
            column_synonyms=state.get("column_synonyms") or {},
            resource_type_synonyms=state.get("resource_type_synonyms") or {},
            eav_name_synonyms=state.get("eav_name_synonyms") or {},
            query_targets=(state.get("parsed_requirements") or {}).get(
                "query_targets"
            )
            or [],
            attribute_columns=_eav_attribute_columns(state),
        )
    except Exception as e:  # noqa: BLE001 — 역조회 실패는 표시 생략(SQL 생성 영향 없음)
        logger.warning("유사어 사용 역조회 실패: %s", e)
        return None

    # 계측(E5-계측): 최종 SQL에 실제 반영된 동의어를 "[동의어]" 태그로 콘솔 로깅.
    # 매칭 시점 로그(schema_analyzer/field_mapper)와 짝을 이뤄, 매칭된 동의어가
    # 생성 SQL까지 적절히 쓰였는지 테스트 중 육안 검증할 수 있다.
    try:
        if usage.get("mappings"):
            _details = "; ".join(
                f"{m.get('key')}(질의어: "
                f"{', '.join(m.get('matched_user_terms') or []) or '매칭 없음'})"
                for m in usage["mappings"]
            )
            logger.info(
                "[동의어] 최종 SQL 반영 %d건: %s",
                len(usage["mappings"]), _details,
            )
    except Exception as e:  # noqa: BLE001 — 로그 실패가 미등록 리터럴 계측을 막지 않게
        logger.warning("[동의어] 반영 내역 로깅 실패: %s", e)

    try:
        if usage.get("unregistered"):
            logger.info(
                "[동의어] 사전 미등록 리터럴 %d건(LLM 직접 추론 — 적절성 점검 대상): %s",
                len(usage["unregistered"]),
                "; ".join(
                    f"{u['type']}:'{u['literal']}'"
                    for u in usage["unregistered"]
                ),
            )
    except Exception as e:  # noqa: BLE001 — 로그 실패가 역조회 결과 반환을 막지 않게
        logger.warning("[동의어] 미등록 리터럴 로깅 실패: %s", e)

    return usage


async def query_generator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 요구사항과 스키마를 기반으로 SQL 쿼리를 생성한다.

    재시도(회귀) 시에는 이전 에러 메시지를 컨텍스트에 포함하여
    수정된 SQL을 생성한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - generated_sql: 생성된 SQL 쿼리 문자열
        - retry_count: 재시도 횟수 (증가)
        - error_message: None (초기화)
        - current_node: "query_generator"
    """
    ctx = _prepare(state, llm, app_config)

    # 폼필 확인 이력(D-151 Phase 3) — 시그니처 이력을 답변 형식으로 로드해 이번 턴
    # 답변 아래에 병합(이번 턴 답이 이김: {**memory, **answers}). 적용 시 sliding TTL
    # 연장. Redis 불가·TTL 0이면 빈 dict(현행 동일). 멀티 경로(_prepare_multi_run)와 대칭.
    if state.get("template_structure") and not ctx.is_retry:
        _sig, _mem_answers, _ = await load_form_memory_answers(
            state.get("template_structure"), ctx.app_config
        )
        if _mem_answers:
            state = {
                **state,
                "form_fill_answers": {
                    **_mem_answers, **(state.get("form_fill_answers") or {}),
                },
            }

    # 트랙 S(S2/D-128) 단계적 도출 관측 레코드 — 루프 미발동이면 빈 리스트로 남아 None을 반환한다
    # (요청 스코프 상태 자기정리 — 노드 스킵 경로가 직전 턴 값을 물려주지 않게).
    derivation_records: list[dict] = []
    # 결정적 경로 우선(LLM 우회) → 둘 다 비면 트랙 A LLM 폴백.
    form_fill = _try_deterministic(state, ctx)
    sql = form_fill["sql"] if form_fill else None
    semantic_sql, coverage_outside = await _try_semantic(
        state, ctx, sql, derivation_records,
    )
    sql = sql or semantic_sql

    # 트랙 A 산출물(기본 None — 단일 경로·결정적 경로는 무변경)
    sql_candidates: list[dict] | None = None
    text2sql_fallback: dict | None = None
    extra_return: dict = {}
    if not sql:
        sql, sql_candidates, text2sql_fallback, extra_return = await _llm_fallback(
            state, ctx, coverage_outside,
        )

    # 폼필 산출 승격(D-146/D-148/D-151) — 월 앵커·매핑 갱신분·HITL 산출물을 state 델타로.
    # output_generator가 역질문 페이로드·사유·상수 기입에 사용한다(멀티 경로와 대칭).
    if form_fill:
        if form_fill.get("month_anchor"):
            extra_return["form_month_anchor"] = form_fill["month_anchor"]
        if form_fill.get("mapping_updates"):
            extra_return["column_mapping"] = {
                **(state.get("column_mapping") or {}),
                **form_fill["mapping_updates"],
            }
        if form_fill.get("candidates"):
            extra_return["form_fill_candidates"] = form_fill["candidates"]
        if form_fill.get("overrides"):
            extra_return["form_fill_overrides"] = form_fill["overrides"]
        if form_fill.get("literals"):
            extra_return["form_fill_literals"] = form_fill["literals"]

    logger.info(f"SQL 생성 완료 (retry={ctx.retry_count}): {sql[:1000]}...")

    return {
        "generated_sql": sql,
        "sql_candidates": sql_candidates,
        "text2sql_fallback": text2sql_fallback,
        "smq_derivation": derivation_records or None,
        # 유사어 사용 역조회 (처리 현황 표시용) — 실패해도 SQL 생성에는 영향 없음
        "synonym_usage": _instrument_synonym_usage(state, sql),
        "retry_count": ctx.retry_count,
        "error_message": None,  # 에러 메시지 초기화
        "current_node": "query_generator",
        **extra_return,
    }



def _build_stepwise_deps(
    state: AgentState, app_config: AppConfig, limit_value: int
) -> Optional["StepwiseDeps"]:
    """단일 경로(그래프·orchestration 인라인·deepagents)의 단계적 도출 도구 재료를 만든다.

    플래그 OFF면 None을 돌려 도구·컨텍스트 조립 자체를 하지 않는다(회귀 0). ON이면 state가
    가진 유사어·값 인덱스·스키마·엔진을 그대로 주입한다 — 멀티 DB 경로(`_generate_sql`)도
    같은 형태를 만들어 넘기므로 경로 간 재료 비대칭이 없다(D-066).

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정
        limit_value: 결정적으로 해석된 기본 행 제한

    Returns:
        ``column_deriver.StepwiseDeps`` 또는 None(플래그 OFF)
    """
    return build_stepwise_deps(
        app_config,
        path="single",
        synonyms=state.get("column_synonyms") or {},
        value_index=(
            state.get("column_value_index")
            if app_config.synonym.value_retrieval else None
        ),
        schema_info=state.get("schema_info") or {},
        db_engine=state.get("active_db_engine") or "postgresql",
        default_limit=limit_value,
    )


async def _select_query_history_examples(
    state: AgentState, user_query: str, app_config: AppConfig
) -> list[dict] | None:
    """검증된 질의 이력에서 few-shot 예시를 선택한다 (Plan 67 N2 / D-133).

    실제 선택은 공용 헬퍼(``query_history.select_fewshot_examples``)가 수행한다 — 멀티 DB
    경로(`multi_db_executor._generate_sql`)가 같은 함수를 쓰므로 경로 간 비대칭이 없다(D-066).
    이 함수는 state에서 db_id를 뽑아 넘기는 얇은 어댑터다.

    Args:
        state: 현재 에이전트 상태
        user_query: 사용자 자연어 질의
        app_config: 앱 설정

    Returns:
        few-shot 예시 목록(question/sql) 또는 None(고정 예시 유지)
    """
    return await select_history_fewshot(
        state.get("active_db_id") or "", user_query, app_config
    )


def _build_value_index_injection(
    state: AgentState, user_query: str, app_config: AppConfig
) -> str:
    """E5-2 값 검색 리터럴 주입 블록을 만든다(value_retrieval OFF·미매칭 시 빈 문자열)."""
    return build_value_index_injection(
        state.get("column_value_index"), user_query, app_config
    )


async def _run_multi_candidate_single_db(
    state: AgentState,
    llm: BaseChatModel,
    app_config: AppConfig,
    system_prompt: str,
    user_prompt: str,
    user_query: str,
) -> dict:
    """단일 DB 경로(A/B)의 다중 후보 생성·선택(E2~E4).

    경로별 validator(query_validator)·executor(get_db_client.execute_sql)를 주입해
    selector가 경로 비대칭 없이 동작한다(§2.1 / D-066 계열).
    """
    from src.db import get_db_client
    from src.dbhub.models import QueryExecutionError, QueryTimeoutError
    from src.nodes.candidate_generator import generate_candidates
    from src.nodes.candidate_selector import run_candidate_pipeline
    from src.nodes.query_validator import query_validator

    t2 = app_config.text2sql
    is_kbgenai_llm = is_kbgenai(llm)
    db_id = state.get("active_db_id")

    async def _validate(sql: str):
        vstate = {**state, "generated_sql": sql, "error_message": None}
        try:
            vr = await query_validator(vstate, app_config=app_config)
        except Exception as e:  # noqa: BLE001
            return f"validator 예외: {e}"
        res = (vr or {}).get("validation_result") or {}
        return None if res.get("passed") else (res.get("reason") or "검증 실패")

    client_db_id = db_id if db_id and db_id not in ("_default", "default") else None
    # DB 연결 실패와 파이프라인 자체 실패를 분리한다 — 종전에는 파이프라인(LLM 호출·
    # 선택 로직) 예외까지 바깥 except가 삼켜 "no_db"로 위장됐다 (Plan 69 P0-②).
    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(
            get_db_client(app_config, db_id=client_db_id)
        )
    except Exception as e:  # noqa: BLE001 — DB 연결 실패: 생성만 수행하고 첫 후보 반환
        await stack.aclose()
        logger.warning("다중 후보 실행 컨텍스트 실패, 생성만 수행: %s", e)
        candidates = await generate_candidates(
            llm, system_prompt, user_prompt,
            count=t2.candidate_count, strategies=t2.candidate_strategies,
            is_kbgenai=is_kbgenai_llm, extract_sql=extract_sql_from_response,
        )
        first = candidates[0] if candidates else {"sql": "", "strategy": None, "confidence": 0.0}
        return {"sql": first["sql"], "strategy": first.get("strategy"), "confidence": 0.0,
                "all_failed": True, "method": "no_db", "audit": {"error": str(e)},
                "sql_candidates": candidates}

    try:
        async def _execute(sql: str) -> dict:
            try:
                result = await client.execute_sql(sql)
                return {"rows": result.rows, "error": None}
            except (QueryExecutionError, QueryTimeoutError) as e:
                return {"rows": None, "error": str(e)}
            except Exception as e:  # noqa: BLE001
                return {"rows": None, "error": str(e)}

        return await run_candidate_pipeline(
            llm, system_prompt, user_prompt,
            count=t2.candidate_count, strategies=t2.candidate_strategies,
            selection=t2.selection, is_kbgenai=is_kbgenai_llm,
            extract_sql=extract_sql_from_response,
            validate=_validate, execute=_execute, user_query=user_query,
        )
    except Exception as e:  # noqa: BLE001 — 파이프라인 실패: 사유 가시화, 재생성은 재시도 루프에 위임
        logger.exception("다중 후보 파이프라인 실패: %s", e)
        return {"sql": "", "strategy": None, "confidence": 0.0,
                "all_failed": True, "method": "pipeline_error",
                "audit": {"error": str(e)}, "sql_candidates": None}
    finally:
        await stack.aclose()


def _decide_fallback_tier(
    coverage_outside: bool, app_config: AppConfig, selection: dict
) -> dict | None:
    """트랙 C 커버리지 밖 3단 폴백의 티어를 판정한다(E6-3).

    커버리지 내(컴파일)·트랙 C 미사용·과도기 llm 폴백이면 None(게이트 무관).
    candidate_then_human: 전 후보 실패 또는 신뢰도<임계면 human_review, 아니면 auto.
    human: 항상 human_review.
    """
    t2 = app_config.text2sql
    if not coverage_outside or t2.semantic_fallback == "llm":
        return None
    conf = float(selection.get("confidence", 0.0))
    all_failed = bool(selection.get("all_failed"))
    below = all_failed or conf < t2.fallback_confidence_min
    if t2.semantic_fallback == "human" or (
        t2.semantic_fallback == "candidate_then_human" and below
    ):
        reason = ("전 후보 실행 실패" if all_failed
                  else f"선택 신뢰도 {conf:.2f} < 임계 {t2.fallback_confidence_min:.2f}")
        return {"tier": "human_review", "confidence": conf,
                "method": selection.get("method"), "reason": reason}
    return {"tier": "auto", "confidence": conf, "method": selection.get("method"),
            "reason": "트랙 A 선택 신뢰도 임계 통과"}


def _build_system_prompt(
    schema_info: dict,
    default_limit: int,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    active_db_id: str | None = None,
    polestar_db_ids: set[str] | None = None,
    active_db_engine: str | None = None,
    routing_intent: str | None = None,
    query_history_examples: list[dict] | None = None,
    path_parity: bool = False,
) -> str:
    """시스템 프롬프트를 구성한다.

    Args:
        schema_info: DB 스키마 정보
        default_limit: 기본 LIMIT 값
        column_descriptions: 컬럼 설명 매핑 (선택)
        column_synonyms: 유사 단어 매핑 (선택)
        resource_type_synonyms: RESOURCE_TYPE 값-한국어 매핑 (선택)
        eav_name_synonyms: EAV NAME 값-한국어 매핑 (선택)
        active_db_id: 현재 활성 DB 식별자 (선택)
        polestar_db_ids: Polestar 전용 프롬프트 적용 DB ID 집합 (선택, .env 설정)
        active_db_engine: 대상 DB 엔진 타입 (선택, 예: "db2", "postgresql")
        routing_intent: 시멘틱 라우터가 분류한 의도 (선택, 예: "alarm_query", "data_query")
        query_history_examples: 질의 이력 few-shot 예시 (선택, N2 — 없으면 고정 예시)
        path_parity: 경로 대칭 옵트인 (ON이면 멀티 전용이던 스키마 한정 규칙을 주입)

    Returns:
        시스템 프롬프트 문자열
    """
    schema_text = _format_schema_for_prompt(
        schema_info,
        column_descriptions=column_descriptions,
        column_synonyms=column_synonyms,
        resource_type_synonyms=resource_type_synonyms,
        eav_name_synonyms=eav_name_synonyms,
    )

    # 구조 분석 가이드 (있으면 삽입)
    structure_meta = schema_info.get("_structure_meta")
    structure_guide = ""
    if structure_meta:
        structure_guide = _format_structure_guide(
            structure_meta,
            resource_type_synonyms=resource_type_synonyms,
            eav_name_synonyms=eav_name_synonyms,
            query_history_examples=query_history_examples,
        )

    # DB 엔진 힌트
    if not active_db_engine:
        # 그래프 경로 상시 폴백 실측용 로그 — 결정적 주입 전환 판단 재료 (Plan 69 P4-4)
        logger.info("[엔진폴백] active_db_engine 미설정 — postgresql 가정(프롬프트 힌트)")
    db_engine = active_db_engine or "postgresql"
    db_engine_hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."

    # 경로 대칭 (b): 스키마 한정 규칙(D-057)은 종전 멀티 전용이었다. 옵트인 ON이면 단일에도
    # 멀티와 같은 문구를 주입한다 — DB2 단일 조회에서 무스키마 참조가 연결 계정 CURRENT
    # SCHEMA로 잘못 해소되는 것을 막는다(Plan 69 P3-2). OFF면 프롬프트 바이트 불변.
    if path_parity:
        from src.routing.db_schema import get_schema_prefix

        _prefix = get_schema_prefix(active_db_id) if active_db_id else ""
        db_engine_hint += build_schema_prefix_rule(
            _prefix,
            example_table=_SCHEMA_EXAMPLE_TABLE,
            foreign_prefix_example=_FOREIGN_SCHEMA_PREFIX,
        )
        logger.info(
            "[경로대칭] (b) 스키마 한정 규칙 주입(db=%s, prefix=%s)",
            active_db_id, _prefix or "(무스키마)",
        )

    # DB 어댑터 디스패치: 담당 어댑터(폴스타)가 있으면 의도별 전용 템플릿, 없으면 공통 템플릿.
    # POLESTAR_DB_IDS 게이트는 어댑터 owns()로 이동(Plan 63 P2/D-089, 동작 불변).
    adapter = get_adapter(active_db_id, polestar_db_ids)
    template = None
    if adapter is not None:
        template = adapter.system_template(routing_intent)
    if template is None:
        template = QUERY_GENERATOR_SYSTEM_TEMPLATE

    return template.format(
        schema=schema_text,
        default_limit=default_limit,
        structure_guide=structure_guide,
        db_engine_hint=db_engine_hint,
    )


def _get_eav_pattern(schema_info: Optional[dict]) -> Optional[dict]:
    """_structure_meta에서 첫 번째 EAV 패턴을 반환한다.

    Args:
        schema_info: 스키마 정보 딕셔너리 (선택)

    Returns:
        EAV 패턴 딕셔너리 또는 None
    """
    return first_eav_pattern(schema_info)


def _mapping_prompt_sections(
    column_mapping: dict[str, Optional[str]],
    schema_info: Optional[dict],
    db_engine: Optional[str],
    *,
    db_id: Optional[str] = None,
    adapter_db_ids: set[str] | None = None,
) -> list[str]:
    """양식-DB 매핑(column_mapping)에서 파생되는 사용자 프롬프트 섹션들을 만든다.

    피벗 블록·매핑 지시·EAV 블록·미매핑 안내 네 섹션이 **하나의 판정 흐름**을 공유한다 —
    자식 리소스 EAV 유무가 다중 리소스 피벗 진입을 정하고, 그 결과가 미매핑 목록에서
    사용률 필드를 걷어낸다. 그래서 섹션별로 더 쪼개지 않고 이 단위로 묶었다.

    Args:
        column_mapping: 필드-컬럼 매핑(field_mapper 결과)
        schema_info: DB 스키마 정보(매핑 검증·EAV 패턴 추출용)
        db_engine: 대상 엔진(방언 예시용)
        db_id/adapter_db_ids: 지표 필드 분류의 어댑터 디스패치용

    Returns:
        프롬프트에 이어 붙일 섹션 문자열 목록(해당 없으면 빈 목록)
    """
    parts: list[str] = []
    # 수정 A: schema_info 기반 column_mapping 필터링
    column_mapping = filter_mapping_by_schema(
        column_mapping, schema_info,
        log_label="column_mapping 필터링",
        log_schema_tables=True,
        strip_db_prefix=True,
    )

    # 정규 매핑과 EAV 매핑 분리
    regular_entries, eav_entries = split_mapping_entries(column_mapping)

    # EAV config 테이블과 entity 테이블이 다를 수 있으므로
    # 정규 컬럼 필터링을 제거하고 LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 함.
    # (Plan 37: 수정 3-2)

    # 미매핑 필드(column_mapping[field] = None) — 사용률 통계 필드가 여기로 흐른다(D-066 후속3).
    unmapped_fields = [field for field, col in column_mapping.items() if col is None]
    # 자식 리소스(server.Cpus/Memory 등) EAV 속성이 섞이면 서버 행 브릿지 조인으로는
    # NULL이 되므로, resource_type 구분 다중 리소스 피벗 블록으로 대체한다(D-068).
    attr_rt = eav_attr_resource_types(schema_info)
    child_eav, server_eav = split_eav_by_resource_type(
        eav_entries, attr_rt, entity_resource_type=_ENTITY_RESOURCE_TYPE
    )
    use_multi_resource_pivot = bool(child_eav)
    if use_multi_resource_pivot:
        eav_pattern = _get_eav_pattern(schema_info) or {}
        # 사용률 통계 필드는 통합 피벗에 접어 넣고 미매핑 목록에서 뺀다(블록 충돌·GROUP BY 위반 방지).
        metric_unmapped = [
            f for f in unmapped_fields
            if classify_metric_field(f, db_id=db_id, adapter_db_ids=adapter_db_ids)
        ]
        unmapped_fields = [f for f in unmapped_fields if f not in metric_unmapped]
        parts.append(
            build_multi_resource_pivot_block(
                regular_entries, server_eav, child_eav, eav_pattern,
                metric_fields=metric_unmapped, db_engine=db_engine,
            )
        )

    if not use_multi_resource_pivot and regular_entries:
        parts.append(_mapping_instruction_section(regular_entries))

    if eav_entries and not use_multi_resource_pivot:
        # _structure_meta에서 EAV 패턴 정보를 동적 추출. alias 지시는 단일 경로 규약
        # (테이블.컬럼 형식)이라 한글 alias 강제는 하지 않는다(§0.3-3 (e) 의도된 차이).
        parts.append(
            build_eav_pivot_block(
                eav_entries, _get_eav_pattern(schema_info),
                hangul_alias=False,
                host_attribute=_EAV_HOST_ATTRIBUTE,
                link_column=_EAV_LINK_COLUMN,
            )
        )
    # 미매핑 필드 별도 안내 — 위에서 계산·필터링한 unmapped_fields 사용(사용률은 통합 피벗에 접혀 제외됨).
    if unmapped_fields:
        parts.append(_unmapped_fields_section(unmapped_fields, db_engine))
    return parts


def _mapping_instruction_section(regular_entries: list[tuple[str, str]]) -> str:
    """정규 매핑 컬럼을 SELECT에 포함시키는 지시 섹션을 만든다."""
    # cmm_resource.name 컬럼 매핑이 포함되어 있는지 검사 (대소문자 및 접두사 무관)
    has_resource_name = any(
        col.lower().endswith("cmm_resource.name") or col.lower() == "name"
        for field, col in regular_entries
    )
    resource_name_hint = ""
    if has_resource_name:
        resource_name_hint = (
            "\n\n**특별 지침 (서버 이름 조회)**:\n"
            "- `cmm_resource.name` 컬럼은 서버 종합 정보 피벗 쿼리 시, "
            "서버 리소스 행(`resource_type = 'server.Server'`)의 이름을 뜻합니다.\n"
            "- 따라서 SELECT 절에 단독으로 쓰지 말고, 반드시 다음과 같이 피벗 집계 함수 형태로 변환하여 사용하세요:\n"
            "  `MAX(CASE WHEN c.resource_type = 'server.Server' THEN c.name END) AS server_name`"
        )

    mapping_lines = "\n".join(
        f'- "{field}" -> {col}' for field, col in regular_entries
    )
    return (
        f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
        "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
        'SELECT 시 "테이블명.컬럼명" 형식의 alias를 사용하세요.\n'
        '예: SELECT s.hostname AS "servers.hostname", ...'
        f"{resource_name_hint}"
    )


def _unmapped_fields_section(
    unmapped_fields: list[str], db_engine: Optional[str]
) -> str:
    """자동 매핑에 실패한 양식 필드의 직접 조회 안내 섹션을 만든다(U-1 공유 빌더)."""
    return build_unmapped_fields_block(
        unmapped_fields,
        cast_example=decimal_cast_example(db_engine),
        metric_table=METRIC_PIVOT_TABLE,
        metric_pivot_keys=METRIC_PIVOT_KEYS,
    )


def _build_user_prompt(
    parsed_requirements: dict,
    template_structure: Optional[dict],
    error_message: Optional[str],
    previous_sql: Optional[str],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
    conversation_context: Optional[dict] = None,
    schema_info: Optional[dict] = None,
    db_engine: Optional[str] = None,
    db_id: Optional[str] = None,
    adapter_db_ids: set[str] | None = None,
) -> str:
    """사용자 프롬프트를 구성한다.

    column_mapping이 제공되면 (field_mapper에서 생성) 매핑된 컬럼을
    명시적으로 SELECT에 포함하도록 지시한다.
    schema_info가 제공되면 column_mapping의 테이블이 현재 스키마에
    존재하는지 검증하여 필터링한다.

    Args:
        parsed_requirements: 구조화된 요구사항
        template_structure: 양식 구조 (있으면)
        error_message: 이전 에러 메시지 (재시도 시)
        previous_sql: 이전 생성 SQL (재시도 시)
        column_mapping: 필드-컬럼 매핑 (field_mapper 결과, 선택)
        conversation_context: 멀티턴 대화 맥락 (선택)
        schema_info: DB 스키마 정보 (column_mapping 검증용, 선택)
        db_id: 대상 DB 식별자 (지표 필드 분류의 어댑터 디스패치용, 선택)
        adapter_db_ids: 어댑터 담당 db_id 집합 (선택)

    Returns:
        사용자 프롬프트 문자열
    """
    parts: list[str] = []

    # 멀티턴 맥락 (이전 SQL 참조)
    if (
        conversation_context
        and conversation_context.get("turn_count", 0) > 1
        and conversation_context.get("previous_sql")
        and not error_message  # 재시도가 아닌 경우에만
    ):
        parts.append(
            f"## 이전 대화의 SQL (참조용)\n"
            f"```sql\n{conversation_context['previous_sql']}\n```\n"
            f"이전 결과: {conversation_context.get('previous_results_summary', '없음')}\n\n"
            f"사용자가 이전 결과를 참조하는 경우, 이전 SQL을 기반으로 조건을 추가/수정하세요."
        )

    # 원본 질의
    original = parsed_requirements.get("original_query", "")
    parts.append(f"## 사용자 질의\n{original}")

    # 구조화된 요구사항
    req_json = json.dumps(parsed_requirements, ensure_ascii=False, indent=2)
    parts.append(f"## 파싱된 요구사항\n```json\n{req_json}\n```")

    # 양식-DB 매핑 (field_mapper에서 생성된 column_mapping 우선)
    if column_mapping:
        parts.extend(_mapping_prompt_sections(
            column_mapping, schema_info, db_engine,
            db_id=db_id, adapter_db_ids=adapter_db_ids,
        ))

    elif template_structure:
        # column_mapping이 없으면 기존 방식 (하위 호환)
        tmpl_json = json.dumps(template_structure, ensure_ascii=False, indent=2)
        parts.append(f"## 양식 구조\n```json\n{tmpl_json}\n```")
        parts.append(
            "양식의 헤더/플레이스홀더에 해당하는 컬럼을 반드시 SELECT에 포함하세요."
        )

    # 재시도 컨텍스트
    if error_message and previous_sql:
        parts.append(
            f"## 이전 시도 (실패)\n"
            f"이전 SQL:\n```sql\n{previous_sql}\n```\n"
            f"에러: {error_message}"
        )
        parts.append("위 에러를 수정한 새로운 SQL을 생성하세요.")

    return "\n\n".join(parts)


def _format_schema_for_prompt(
    schema_info: dict,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> str:
    """스키마 정보를 프롬프트에 적합한 텍스트로 변환한다.

    컬럼 설명과 유사 단어가 있으면 함께 포함하여
    LLM의 컬럼 선택 정확도를 높인다.
    resource_type/eav_name 유사단어가 있으면 참조 정보로 추가한다.

    Args:
        schema_info: 스키마 딕셔너리
        column_descriptions: {table.column: description} 매핑 (선택)
        column_synonyms: {table.column: [synonym, ...]} 매핑 (선택)
        resource_type_synonyms: {resource_type값: [한국어 표현, ...]} 매핑 (선택)
        eav_name_synonyms: {eav_name값: [한국어 표현, ...]} 매핑 (선택)

    Returns:
        사람이 읽기 쉬운 스키마 텍스트
    """
    # 멀티 경로(_format_schema)와 같은 빌더를 쓰되, 단일 경로는 상세판 옵션으로 호출한다
    # (설명·유사어·NOT NULL·참조 섹션 수록, 문구 차이 보존 — Plan 69 P3-1).
    return format_schema_text(
        schema_info,
        column_descriptions=column_descriptions,
        column_synonyms=column_synonyms,
        resource_type_synonyms=resource_type_synonyms,
        eav_name_synonyms=eav_name_synonyms,
        include_not_null=True,
        sample_style="labeled",
        relationships_header="### 테이블 관계 (FK)",
        # 라이브 샘플 방어(D-155): 크기 상한 프리뷰(값 200자·테이블당 2,000자)로 스크럽
        # 비용을 bound하고, PII 스크럽으로 FabriX 필터 오탐을 차단한다(멀티 경로와 대칭).
        sample_renderer=_render_samples_secure,
    )


def _render_samples_secure(samples: list) -> str:
    """스키마 샘플을 상한 프리뷰로 만들고 PII를 스크럽한다(단일 경로 프롬프트 방어)."""
    preview = safe_sample_preview(samples)
    if is_scrub_samples_enabled():
        preview = scrub_pii(preview)  # 라이브 샘플 PII → FabriX 필터 오탐 차단 예방
    return preview


