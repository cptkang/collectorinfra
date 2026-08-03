"""SQL 생성 노드.

LLM을 사용하여 사용자 요구사항과 스키마 정보를 기반으로
SQL SELECT 쿼리를 자동 생성한다.
재시도 시 이전 에러 메시지를 반영하여 수정된 SQL을 생성한다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.db_adapters import get_adapter
from src.security.pii_filter import is_scrub_samples_enabled, scrub_pii
from src.state import AgentState
from src.routing.domain_config import get_domain_by_id
from src.utils.query_gen_common import (
    _collect_prior_identity_values,
    build_generic_period_hint,
    build_prior_rows_block,
    build_query_examples_block,
    build_stat_month_block,
    build_value_index_block,
    correct_servername_hostname_mapping,
    enforce_all_query_limit,
    resolve_effective_limit,
    resolve_query_limit,
    resolve_stat_month_range,
    template_context_text,
)
# 폴스타 EAV/피벗 결정적 조립기는 어댑터로 이동(Plan 63 P2, D-089) — application 직접 임포트.
from src.db_adapters.polestar.assembler import (
    apply_capacity_scope_rule,
    apply_remark_server_name_rule,
    build_form_fill_candidates,
    build_month_series_block,
    build_multi_resource_pivot_block,
    build_multi_resource_pivot_sql,
    classify_metric_field,
    decimal_cast_example,
    eav_attr_resource_types,
    filter_pivot_regular_entries,
    find_vendor_model_concat,
    recognize_month_series,
    resolve_form_fill_answers,
)
from src.nodes.candidate_generator import classify_complexity
from src.nodes.semantic_compiler import compile_from_nl
from src.schema_cache.form_memory import load_form_memory_answers
from src.utils.schema_utils import build_excluded_join_map
from src.utils.synonym_usage import extract_synonym_usage

logger = logging.getLogger(__name__)


def _format_structure_guide(
    structure_meta: dict,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> str:
    """구조 분석 메타데이터에서 쿼리 가이드를 포맷한다.

    structure_meta의 query_guide 문자열을 기반으로,
    EAV 패턴이 감지된 경우 resource_type/eav_name 유사단어 정보를 추가한다.

    Args:
        structure_meta: schema_info["_structure_meta"] 딕셔너리
        resource_type_synonyms: RESOURCE_TYPE 유사단어 매핑 (선택)
        eav_name_synonyms: EAV NAME 유사단어 매핑 (선택)

    Returns:
        포맷된 가이드 텍스트
    """
    guide = structure_meta.get("query_guide", "")

    # EAV 패턴 존재 여부 확인
    eav_patterns = [
        p for p in structure_meta.get("patterns", [])
        if p.get("type") == "eav"
    ]

    # EAV 패턴이 있고 query_guide가 존재하면, 조인 규칙 지침을 앞에 삽입
    if eav_patterns and guide:
        eav_join_rule = (
            "## EAV 테이블 조인 규칙\n"
            "EAV 구조의 entity 테이블과 config 테이블을 조인할 때 "
            "id 컬럼으로 직접 조인하지 마세요.\n"
            "두 테이블의 ID 체계가 다릅니다. "
            "반드시 아래 지침의 JOIN SQL 패턴을 그대로 사용하세요.\n\n"
        )
        guide = eav_join_rule + guide

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
        value_joins = eav_p.get("value_joins", [])
        if value_joins:
            entity_table = eav_p.get("entity_table", "entity_table")
            config_table = eav_p.get("config_table", "config_table")
            attr_col = eav_p.get("attribute_column", "NAME")
            guide += "\n\n[값 기반 조인 (value-based join)]"
            guide += (
                f"\n{config_table}과 {entity_table} 간 FK가 없습니다. "
                "다음 값 대응 관계를 조인에 활용하세요:"
            )
            for vj in value_joins:
                guide += (
                    f"\n- {config_table}.{attr_col}='{vj['eav_attribute']}'인 행의 "
                    f"{vj['eav_value_column']} 값은 "
                    f"{entity_table}.{vj['entity_column']}과 동일한 값입니다."
                )

    # 샘플 데이터 정보
    samples = structure_meta.get("samples", {})
    if samples:
        for purpose, rows in samples.items():
            if isinstance(rows, list) and rows:
                guide += f"\n\n### {purpose}\n"
                for row in rows[:10]:
                    guide += f"  - {row}\n"

    # 금지 JOIN 컬럼 경고
    for pattern in structure_meta.get("patterns", []):
        excluded = pattern.get("excluded_join_columns", [])
        if excluded:
            guide += "\n\n[금지 JOIN 컬럼]"
            guide += "\n다음 컬럼은 JOIN ON 절에서 절대 사용하지 마세요:"
            for excl in excluded:
                guide += (
                    f"\n- {excl.get('table', '?')}.{excl.get('column', '?')}: "
                    f"{excl.get('reason', 'JOIN 불가')}"
                )

    # 쿼리 예시 (few-shot) — 질문→SQL 쌍을 직접 제시하여 LLM 환각 방지.
    # 멀티 DB 경로(multi_db_executor)와 동일 출처를 쓰도록 공용 헬퍼로 분리(D-066).
    guide += build_query_examples_block(structure_meta)

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
    prior_rows = state.get("prior_rows")
    if not prior_rows:
        return None
    col, values = _collect_prior_identity_values(prior_rows)
    if not col or not values:
        return None
    return col, values


def _try_build_form_fill_pivot_sql(
    state: AgentState, limit_value: int, user_query: str
) -> Optional[dict]:
    """폼필 결정적 피벗 SQL 조립 — 자식 리소스 EAV·월 시리즈·양식 업로드(D-068/D-113/D-116).

    프롬프트로 스켈레톤을 "제안"하면 LLM이 프로필 few-shot(월별 GROUP BY 등)과 경쟁해 무시·변형
    (서버 중복·config 누락)한다. well-defined 폼필 쿼리는 코드가 직접 조립해 LLM을 우회한다
    (D-068 2차). 양식 업로드(template_structure) 턴은 월 시리즈·자식 EAV가 없어도 항상 결정적
    조립한다(D-116 — 경로 선택이 per-DB LLM 매핑에 종속돼 LLM 폴백이 GROUP BY 계약을 깨던
    라이브 실측의 근본 수정). 해당 케이스가 아니면 None(LLM 경로 유지).

    Returns:
        {"sql": str, "month_anchor": dict|None, "mapping_updates": dict} 또는 None.
        month_anchor는 M~M+max 실제 월(응답 명시 §2.4)·인식 필드 목록(D-114 사유 제외용),
        mapping_updates는 요청 스코프 규칙(처리능력 GB 등)의 매핑 갱신분(state 반영용).
    """
    column_mapping = state.get("column_mapping")
    if not column_mapping:
        return None
    form_intent = bool(state.get("template_structure"))
    schema_info = state.get("schema_info") or {}
    eav_pattern = _get_eav_pattern(schema_info)
    # 서버명/서버이름류가 EAV Hostname으로 오매핑되면 등록명 컬럼으로 결정적 교정(프로필 확정 규칙).
    # state를 오염시키지 않도록 사본에 적용.
    column_mapping = dict(column_mapping)
    if eav_pattern:
        correct_servername_hostname_mapping(column_mapping, eav_pattern.get("entity_table", ""))
    attr_rt = eav_attr_resource_types(schema_info)

    # 폼필에서 llm_inferred 매핑은 채움에 쓰지 않는다(D-116) — 라이브 오염(TPMC·acl_id·
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
                "폼필 llm_inferred 매핑 %d건 채움 제외(D-116, 역질문 후보): %s",
                len(_inferred_dropped), _inferred_dropped,
            )

    # 월 시리즈(가로 6개월 등) 인식 + 요청 스코프 규칙(D-113/D-115). 인식 실패는 폴백(무발동).
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

    # 제조사(모델명)류는 Vendor+Model 결합으로 채운다(D-115 — 라이브 실측: 한쪽만 매핑돼
    # 반쪽 값). 결합 대상 필드는 단독 EAV/직접 컬럼 파티션에서 제외(중복 alias 방지).
    concat_eav = find_vendor_model_concat(schema_info, column_mapping)
    concat_fields = {c[0] for c in concat_eav}
    if month_series and concat_fields:
        # 결합 필드도 행 키=필드명 조회 강제(잔존 EAV:Vendor류 매핑의 오조회 방지)
        mapping_updates.update({f: None for f in concat_fields})

    # 사용자 답변 오버라이드(D-118) — 우선순위 최상위(사용자 > 확정 규칙 > 자동 매핑).
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
            logger.info("폼필 답변 오버라이드 적용(D-118): %s", _applied)
        if _rejected:
            logger.info("폼필 답변 오버라이드 거부(D-118): %s", _rejected)
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
        if a.upper() in attr_rt and attr_rt[a.upper()] != "server.Server"
    ]
    # D-116 게이트: 자식 EAV·월 시리즈 외에 양식 업로드(form_intent) 자체가 발동 조건.
    # eav_pattern 부재 DB(비폴스타)는 발동하지 않는다(현행 LLM 경로 유지).
    if not child_eav and not month_series and not form_intent:
        return None
    if not eav_pattern:
        return None
    if form_intent and not child_eav and not month_series:
        logger.info(
            "폼필 결정적 계약 경로(D-116): 월시리즈·자식EAV 없음 — 게이트 확장으로 조립"
        )
    server_eav = [
        (f, a) for f, a in eav_entries
        if a.upper() not in attr_rt or attr_rt[a.upper()] == "server.Server"
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
        and f not in _applied_ov and classify_metric_field(f)
    ]
    domain = get_domain_by_id(state.get("active_db_id") or "")
    db_schema = domain.db_schema if domain else ""
    sql = build_multi_resource_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        metric_fields=metric_fields,
        db_engine=state.get("active_db_engine"),
        db_schema=db_schema,
        limit=limit_value,
        stat_month=resolve_stat_month_range(user_query),
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
            "폼필 월 시리즈 인식(D-113): rt=%s, 기간=%s~%s, 필드=%d개",
            month_series.resource_type, month_series.anchor[0],
            month_series.anchor[1], len(month_series.fields),
        )
    return {
        "sql": sql,
        "month_anchor": month_anchor,
        "mapping_updates": mapping_updates,
        # HITL 폼필(D-118): 역질문 드롭다운 후보(스키마 실측) + 답변 적용/거부 내역 + 상수
        "candidates": build_form_fill_candidates(schema_info, eav_pattern) if form_intent else [],
        "overrides": overrides_out,
        "literals": literals_out,
    }


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
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    # 재시도 횟수 관리
    retry_count = state.get("retry_count", 0)
    is_retry = bool(state.get("error_message"))
    if is_retry:
        retry_count += 1

    # 멀티턴 맥락에서 이전 SQL 참조
    conversation_context = state.get("conversation_context")

    # 모든/전체 조회 쿼리인 경우 LIMIT 값을 높여 1000건 제한 우회 (멀티 DB 경로와 공용, D-066).
    # 오케스트레이션 경로는 user_query가 sub_query_context(정제 질의)로 교체돼 "모든"이
    # 탈락할 수 있으므로, 승격된 원문 기준 resolved_limit을 우선한다(Plan 65 §3).
    user_query = state.get("user_query", "") or ""
    limit_value = resolve_effective_limit(state, user_query, app_config.query.default_limit)
    # 기간 표현(지난 N개월/지난달 등)의 결정적 해석 — 트랙 C 컴파일과 LLM 폴백 프롬프트가 공유
    stat_month = resolve_stat_month_range(user_query)
    # 통계 테이블 강제 블록(build_stat_month_block)은 폴스타 월 통계 테이블(cmm_metric_stat_m)
    # 규약에 특화된 지시라, 그 테이블을 선언한 DB에만 주입한다(L2 일반화, P1-3/D-088). 현재는
    # 폴스타가 유일한 선언 DB이므로 폴스타 게이트(폴스타 시스템 템플릿과 동일 신호)로 판정하고,
    # 프로필 time_grain 선언 기반 전환은 P3(D-090). 프로필 부재 DB는 미주입 — 시스템 템플릿의
    # 일반 기간 규칙(CURRENT_DATE 동적 계산)만 남아 LLM이 스키마의 시간 컬럼으로 해석한다.
    _stat_block_db = state.get("active_db_id") in (app_config.get_polestar_db_ids() or set())

    # 폼필 확인 이력(D-118 Phase 3) — 시그니처 이력을 답변 형식으로 로드해 이번 턴
    # 답변 아래에 병합(이번 턴 답이 이김: {**memory, **answers}). 적용 시 sliding TTL
    # 연장. Redis 불가·TTL 0이면 빈 dict(현행 동일 — C4).
    if state.get("template_structure") and not is_retry:
        _sig, _mem_answers, _ = await load_form_memory_answers(
            state.get("template_structure"), app_config
        )
        if _mem_answers:
            state = {
                **state,
                "form_fill_answers": {
                    **_mem_answers, **(state.get("form_fill_answers") or {}),
                },
            }

    # 폼필 다중 리소스 피벗은 코드가 결정적으로 조립(LLM 우회). 재시도(에러 컨텍스트) 시엔
    # 결정적 SQL이 이미 실패했을 수 있으므로 LLM 폴백으로 에러를 반영해 수정한다.
    _form_fill = None if is_retry else _try_build_form_fill_pivot_sql(
        state, limit_value, user_query
    )
    deterministic_sql = _form_fill["sql"] if _form_fill else None
    if is_retry and state.get("template_structure"):
        # 침묵 금지: 폼필 턴에서 결정적 조립이 재시도 사유로 스킵되어 LLM 폴백이
        # 양식을 처리하는 시점을 로그로 가시화(라이브 실측: 이 경로가 월 방향 뒤집힘의 근원).
        logger.info(
            "폼필 결정적 조립 스킵 — 재시도 턴(error=%s). LLM 폴백이 양식 처리",
            str(state.get("error_message") or "")[:120],
        )
    # 트랙 C(D-076): 커버리지 내 정형 질의는 시맨틱 모델로 결정적 컴파일(LLM SQL 생성 우회).
    # 폼필(deterministic_sql)·재시도가 아닐 때만 진입. 커버리지 밖이면 None → 아래 LLM 폴백(회귀 0).
    # 삽입 지점 원칙(§3): query_generator 함수 내부 → 그래프 경로(A)·orchestration 인라인(B) 자동 공유.
    semantic_sql = None
    coverage_outside = False  # 트랙 C ON인데 커버리지 밖 → 트랙 A 폴백 대상 여부(3단 폴백)
    # prior_rows(선행 task 결과 스코프)는 컴파일러에 server_scope로 결정적 전달한다(D-099).
    # 과거에는 SMQ가 스코프를 표현하지 못해 우회했으나(D-086), 이제 조립기가 HAVING으로
    # 강제하므로 이 형태(선행 스코프 + 메트릭 순위 + EAV 속성)도 결정적 조립 대상이다.
    prior_scope = _prior_server_scope(state)
    if (not is_retry and not deterministic_sql
            and not state.get("column_mapping")
            and app_config.text2sql.semantic_compose):
        value_index = (
            state.get("column_value_index")
            if app_config.synonym.value_retrieval else None
        )
        semantic_sql, _smq, _cov = await compile_from_nl(
            llm, user_query, state.get("active_db_id") or "",
            default_limit=limit_value,
            stat_month=stat_month,
            value_index=value_index,
            server_scope=prior_scope,
        )
        coverage_outside = semantic_sql is None

    # 트랙 A 산출물(기본 None — 단일 경로·결정적 경로는 무변경)
    sql_candidates: list[dict] | None = None
    text2sql_fallback: dict | None = None
    extra_return: dict = {}

    if deterministic_sql:
        sql = deterministic_sql
        logger.info("폼필 다중 리소스 피벗 SQL 결정적 조립(LLM 우회): %s", sql[:500])
        # 월 시리즈 앵커·요청 스코프 매핑 갱신분을 state에 반영(D-113/D-115).
        # 앵커는 output_generator가 기준월을 응답에 명시(§2.4)하고 인식 필드를
        # 미작성 사유(D-114)에서 제외하는 데 쓴다.
        if _form_fill and _form_fill.get("month_anchor"):
            extra_return["form_month_anchor"] = _form_fill["month_anchor"]
        if _form_fill and _form_fill.get("mapping_updates"):
            extra_return["column_mapping"] = {
                **(state.get("column_mapping") or {}),
                **_form_fill["mapping_updates"],
            }
        # HITL 폼필(D-118): 역질문 후보·답변 적용 내역·직접입력 상수를 state로 승격
        # (output_generator가 역질문 페이로드·사유 노출·writer 상수 기입에 사용).
        if _form_fill and _form_fill.get("candidates"):
            extra_return["form_fill_candidates"] = _form_fill["candidates"]
        if _form_fill and _form_fill.get("overrides"):
            extra_return["form_fill_overrides"] = _form_fill["overrides"]
        if _form_fill and _form_fill.get("literals"):
            extra_return["form_fill_literals"] = _form_fill["literals"]
    elif semantic_sql:
        sql = semantic_sql
        logger.info("시맨틱 결정적 컴파일 SQL(LLM 우회): %s", sql[:500])
    else:
        # 프롬프트 구성
        system_prompt = _build_system_prompt(
            schema_info=state["schema_info"],
            default_limit=limit_value,
            column_descriptions=state.get("column_descriptions", {}),
            column_synonyms=state.get("column_synonyms", {}),
            resource_type_synonyms=state.get("resource_type_synonyms"),
            eav_name_synonyms=state.get("eav_name_synonyms"),
            active_db_id=state.get("active_db_id"),
            polestar_db_ids=app_config.get_polestar_db_ids() or None,
            active_db_engine=state.get("active_db_engine"),
            routing_intent=state.get("routing_intent"),
        )

        user_prompt = _build_user_prompt(
            parsed_requirements=state["parsed_requirements"],
            template_structure=state.get("template_structure"),
            error_message=state.get("error_message") if is_retry else None,
            previous_sql=state.get("generated_sql") if is_retry else None,
            column_mapping=state.get("column_mapping"),
            conversation_context=conversation_context,
            schema_info=state["schema_info"],
            db_engine=state.get("active_db_engine"),
        )
        # 기간 표현이 있으면 결정적으로 해석된 단일 월(YYYYMM)을 강제한다 — 시스템 템플릿의
        # "CURRENT_DATE 동적 계산" 일반 규칙을 LLM이 따르면 BETWEEN으로 진행 중인 달까지
        # 포함하는 실측 오류가 있었다(D-076 후속4).
        _sm_block = build_stat_month_block(stat_month) if _stat_block_db else ""
        if _sm_block:
            user_prompt += "\n\n" + _sm_block
        # 폼필 월 시리즈 양식이 LLM 폴백으로 흐르는 경우(재시도 턴 등) — 인식기가 확정한
        # 필드↔YYYYMM 리터럴을 강제해 CURRENT_DATE 역방향 계산(월 뒤집힘 실측)을 차단.
        _ms_block = build_month_series_block(recognize_month_series(
            state.get("column_mapping") or {},
            context_text=template_context_text(state.get("template_structure")),
            user_query=user_query,
        )) if state.get("template_structure") else ""
        if _ms_block:
            user_prompt += "\n\n" + _ms_block
        # 무선언(프로필 없음) DB: GENERIC_LLM_MAPPING 옵트인 시 범용 기간 힌트(폴스타 리터럴 없음).
        # 선언 우선 — 폴스타(_stat_block_db)는 위 결정적 블록을 쓰므로 이 경로에 들어오지 않는다(P3/D-090).
        elif app_config.text2sql.generic_llm_mapping:
            _gp_block = build_generic_period_hint(stat_month)
            if _gp_block:
                user_prompt += "\n\n" + _gp_block

        # E5-2 값 검색 리터럴 주입 — value_retrieval ON + 인덱스 매칭 시만(회귀 0).
        _vi_block = _build_value_index_injection(state, user_query, app_config)
        if _vi_block:
            user_prompt += _vi_block

        # 선행 task 결과 서버 스코프 강제 — orchestration 데이터 의존(input_from) 경로(D-086).
        # prior_rows는 생성만 되고 소비처가 없던 죽은 배선이었다(2026-07-18 실측: 의존 task가
        # 알람 조건을 재표현하다 resource_type='alarm.Alarm' 환각으로 0건).
        _pr_block = build_prior_rows_block(state.get("prior_rows"))
        if _pr_block:
            user_prompt += "\n\n" + _pr_block

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
                AIMessage(content="") if isinstance(llm, KBGenAIChat) else None,
                HumanMessage(content=user_prompt),
            ]
            # Remove any None entries (no effect for other LLMs)
            messages = [m for m in messages if m is not None]
            response = await llm.ainvoke(messages)

            # SQL 추출
            sql = _extract_sql_from_response(response.content)

    # "모든/전체" 상향인데 LLM이 프로필 few-shot 예시의 말미 캡(FETCH FIRST 100 등)을
    # 모방한 경우 결정적 교정 — 2026-07-24 b0 실측(Plan 65 §5.1 항목 6, D-066 후속8).
    sql = enforce_all_query_limit(sql, limit_value, app_config.query.default_limit)

    logger.info(f"SQL 생성 완료 (retry={retry_count}): {sql[:1000]}...")

    # 유사어 사용 역조회 (처리 현황 표시용) — 실패해도 SQL 생성에는 영향 없음
    synonym_usage: dict | None = None
    try:
        structure_meta = (state.get("schema_info") or {}).get("_structure_meta") or {}
        attr_cols = [
            p["attribute_column"]
            for p in structure_meta.get("patterns", [])
            if p.get("type") == "eav" and p.get("attribute_column")
        ]
        synonym_usage = extract_synonym_usage(
            sql,
            column_synonyms=state.get("column_synonyms") or {},
            resource_type_synonyms=state.get("resource_type_synonyms") or {},
            eav_name_synonyms=state.get("eav_name_synonyms") or {},
            query_targets=(state.get("parsed_requirements") or {}).get(
                "query_targets"
            )
            or [],
            attribute_columns=attr_cols or None,
        )
        # 계측(E5-계측): 최종 SQL에 실제 반영된 동의어를 "[동의어]" 태그로 콘솔 로깅.
        # 매칭 시점 로그(schema_analyzer/field_mapper)와 짝을 이뤄, 매칭된 동의어가
        # 생성 SQL까지 적절히 쓰였는지 테스트 중 육안 검증할 수 있다.
        if synonym_usage.get("mappings"):
            _details = "; ".join(
                f"{m.get('key')}(질의어: "
                f"{', '.join(m.get('matched_user_terms') or []) or '매칭 없음'})"
                for m in synonym_usage["mappings"]
            )
            logger.info(
                "[동의어] 최종 SQL 반영 %d건: %s",
                len(synonym_usage["mappings"]), _details,
            )
        if synonym_usage.get("unregistered"):
            logger.info(
                "[동의어] 사전 미등록 리터럴 %d건(LLM 직접 추론 — 적절성 점검 대상): %s",
                len(synonym_usage["unregistered"]),
                "; ".join(
                    f"{u['type']}:'{u['literal']}'"
                    for u in synonym_usage["unregistered"]
                ),
            )
    except Exception as e:
        logger.warning("유사어 사용 역조회 실패: %s", e)

    return {
        "generated_sql": sql,
        "sql_candidates": sql_candidates,
        "text2sql_fallback": text2sql_fallback,
        "synonym_usage": synonym_usage,
        "retry_count": retry_count,
        "error_message": None,  # 에러 메시지 초기화
        "current_node": "query_generator",
        **extra_return,
    }


def _query_keywords(user_query: str) -> list[str]:
    """값 인덱스 검색용 질의 토큰(2글자 이상)을 추출한다."""
    tokens = re.split(r"[\s,./()\[\]{}'\"`~!@#$%^&*=+:;?<>|\\-]+", user_query or "")
    return [t for t in tokens if len(t) >= 2]


def _build_value_index_injection(
    state: AgentState, user_query: str, app_config: AppConfig
) -> str:
    """E5-2 값 검색 리터럴 주입 블록을 만든다(value_retrieval OFF·미매칭 시 빈 문자열)."""
    if not app_config.synonym.value_retrieval:
        return ""
    index = state.get("column_value_index")
    if not index:
        return ""
    from src.schema_cache.value_index import search_value_index

    matched = search_value_index(
        index, _query_keywords(user_query),
        fuzzy=app_config.synonym.fuzzy_match,
        min_score=app_config.synonym.match_confidence_min,
    )
    return build_value_index_block(matched)


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
    is_kbgenai = isinstance(llm, KBGenAIChat)
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
    try:
        async with get_db_client(app_config, db_id=client_db_id) as client:
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
                selection=t2.selection, is_kbgenai=is_kbgenai,
                extract_sql=_extract_sql_from_response,
                validate=_validate, execute=_execute, user_query=user_query,
            )
    except Exception as e:  # noqa: BLE001 — DB 연결 실패: 생성만 수행하고 첫 후보 반환
        logger.warning("다중 후보 실행 컨텍스트 실패, 생성만 수행: %s", e)
        candidates = await generate_candidates(
            llm, system_prompt, user_prompt,
            count=t2.candidate_count, strategies=t2.candidate_strategies,
            is_kbgenai=is_kbgenai, extract_sql=_extract_sql_from_response,
        )
        first = candidates[0] if candidates else {"sql": "", "strategy": None, "confidence": 0.0}
        return {"sql": first["sql"], "strategy": first.get("strategy"), "confidence": 0.0,
                "all_failed": True, "method": "no_db", "audit": {"error": str(e)},
                "sql_candidates": candidates}


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
        )

    # DB 엔진 힌트
    db_engine = active_db_engine or "postgresql"
    db_engine_hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."

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
    if not schema_info:
        return None
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return None
    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") == "eav":
            return pattern
    return None


def _extract_eav_tables(schema_info: Optional[dict]) -> set[str]:
    """_structure_meta에서 EAV 패턴의 관련 테이블명을 추출한다.

    Args:
        schema_info: 스키마 정보 딕셔너리 (선택)

    Returns:
        EAV 패턴과 관련된 테이블명 집합 (소문자)
    """
    if not schema_info:
        return set()
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return set()
    tables: set[str] = set()
    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") == "eav":
            for key in ("entity_table", "config_table", "table"):
                val = pattern.get(key)
                if val:
                    tables.add(val.lower())
    return tables


def _build_user_prompt(
    parsed_requirements: dict,
    template_structure: Optional[dict],
    error_message: Optional[str],
    previous_sql: Optional[str],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
    conversation_context: Optional[dict] = None,
    schema_info: Optional[dict] = None,
    db_engine: Optional[str] = None,
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
        # 수정 A: schema_info 기반 column_mapping 필터링
        if schema_info:
            tables_in_schema = set(schema_info.get("tables", {}).keys())
            # schema_info 키가 "schema.table" 형식일 수 있으므로 마지막 부분도 매칭 대상에 추가
            # 예: "polestar.cmm_resource" → "cmm_resource"도 매칭
            tables_lower = set()
            for t in tables_in_schema:
                tables_lower.add(t.lower())
                # "schema.table" → "table" 부분도 추가
                if "." in t:
                    tables_lower.add(t.rsplit(".", 1)[-1].lower())

            filtered_mapping: dict[str, Optional[str]] = {}
            for field, col in column_mapping.items():
                if col and not col.startswith("EAV:"):
                    # "db_id:table.column" → "table.column" (콜론 접두사 제거)
                    effective_col = col.split(":", 1)[-1] if ":" in col else col
                    # 주의: 누적 리스트 `parts`를 덮어쓰지 않도록 별도 변수 사용.
                    # (과거 `parts = split(...)`가 프롬프트 누적 리스트를 클로버링해
                    #  '## 사용자 질의'·'## 파싱된 요구사항' 섹션이 유실됐음)
                    col_parts = effective_col.split(".")
                    # "db_id.table.column" (3단계) → table = col_parts[-2]
                    # "table.column" (2단계) → table = col_parts[0]
                    if len(col_parts) >= 3:
                        table_part = col_parts[-2]
                        col = f"{col_parts[-2]}.{col_parts[-1]}"
                    elif len(col_parts) == 2:
                        table_part = col_parts[0]
                    else:
                        table_part = ""

                    if table_part.lower() in tables_lower:
                        filtered_mapping[field] = col
                    else:
                        logger.warning(
                            "column_mapping 필터링: '%s' -> '%s' (테이블 '%s' 미존재, schema_tables=%s)",
                            field, col, table_part, list(tables_in_schema)[:5],
                        )
                else:
                    filtered_mapping[field] = col
            column_mapping = filtered_mapping

        # 정규 매핑과 EAV 매핑 분리
        regular_entries = [
            (field, col) for field, col in column_mapping.items()
            if col and not col.startswith("EAV:")
        ]
        eav_entries = [
            (field, col[4:])  # "EAV:" 접두사 제거
            for field, col in column_mapping.items()
            if col and col.startswith("EAV:")
        ]

        # EAV config 테이블과 entity 테이블이 다를 수 있으므로
        # 정규 컬럼 필터링을 제거하고 LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 함.
        # (Plan 37: 수정 3-2)

        # 미매핑 필드(column_mapping[field] = None) — 사용률 통계 필드가 여기로 흐른다(D-066 후속3).
        unmapped_fields = [field for field, col in column_mapping.items() if col is None]
        # 자식 리소스(server.Cpus/Memory 등) EAV 속성이 섞이면 서버 행 브릿지 조인으로는
        # NULL이 되므로, resource_type 구분 다중 리소스 피벗 블록으로 대체한다(D-068).
        attr_rt = eav_attr_resource_types(schema_info)
        child_eav = [
            (field, attr, attr_rt[attr.upper()])
            for field, attr in eav_entries
            if attr.upper() in attr_rt and attr_rt[attr.upper()] != "server.Server"
        ]
        use_multi_resource_pivot = bool(child_eav)
        if use_multi_resource_pivot:
            server_eav = [
                (field, attr)
                for field, attr in eav_entries
                if attr.upper() not in attr_rt or attr_rt[attr.upper()] == "server.Server"
            ]
            eav_pattern = _get_eav_pattern(schema_info) or {}
            # 사용률 통계 필드는 통합 피벗에 접어 넣고 미매핑 목록에서 뺀다(블록 충돌·GROUP BY 위반 방지).
            metric_unmapped = [f for f in unmapped_fields if classify_metric_field(f)]
            unmapped_fields = [f for f in unmapped_fields if f not in metric_unmapped]
            parts.append(
                build_multi_resource_pivot_block(
                    regular_entries, server_eav, child_eav, eav_pattern,
                    metric_fields=metric_unmapped, db_engine=db_engine,
                )
            )

        if not use_multi_resource_pivot and regular_entries:
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
            parts.append(
                f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
                "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
                'SELECT 시 "테이블명.컬럼명" 형식의 alias를 사용하세요.\n'
                '예: SELECT s.hostname AS "servers.hostname", ...'
                f"{resource_name_hint}"
            )

        if eav_entries and not use_multi_resource_pivot:
            # _structure_meta에서 EAV 패턴 정보를 동적 추출
            eav_pattern = _get_eav_pattern(schema_info)
            config_table = eav_pattern.get("config_table", "config_table") if eav_pattern else "config_table"
            attr_col = eav_pattern.get("attribute_column", "NAME") if eav_pattern else "NAME"
            val_col = eav_pattern.get("value_column", "VALUE") if eav_pattern else "VALUE"
            eav_lines = "\n".join(
                f'- "{field}" → EAV 속성 "{attr}" ({config_table}.{attr_col} = \'{attr}\' → {val_col})'
                for field, attr in eav_entries
            )
            # value_joins를 우선 사용하고, 없을 때만 join_condition 폴백
            join_hint = ""
            if eav_pattern and eav_pattern.get("value_joins"):
                vjs = eav_pattern["value_joins"]
                entity_table = eav_pattern.get("entity_table", "entity_table")
                vj_lines = []
                for vj in vjs:
                    vj_lines.append(
                        f"  {config_table}.{attr_col}='{vj['eav_attribute']}' -> "
                        f"{vj['eav_value_column']} = {entity_table}.{vj['entity_column']}"
                    )
                join_hint = (
                    "\n주의: 두 테이블 간 FK가 없으므로 값 기반 브릿지 조인을 사용하세요:\n"
                    + "\n".join(vj_lines)
                    + "\n예: LEFT JOIN {ct} p_host ON p_host.{ac}='Hostname' AND p_host.{vc} = r.hostname"
                    "\n     LEFT JOIN {ct} p_attr ON p_attr.configuration_id = p_host.configuration_id AND p_attr.{ac} = '속성명'"
                ).format(ct=config_table, ac=attr_col, vc=val_col)
            else:
                join_cond = eav_pattern.get("join_condition", "") if eav_pattern else ""
                if join_cond:
                    join_hint = f"\n조인 조건: {join_cond}"
            parts.append(
                f"## EAV 피벗 매핑 (반드시 CASE WHEN 피벗으로 변환)\n{eav_lines}\n\n"
                f"위 EAV 속성은 {config_table} 테이블에서 피벗 쿼리로 추출해야 합니다:\n"
                f"  MAX(CASE WHEN p.{attr_col} = '속성명' THEN p.{val_col} END) AS alias"
                f"{join_hint}\n"
                "반드시 GROUP BY를 포함하세요."
            )
        # 미매핑 필드 별도 안내 — 위에서 계산·필터링한 unmapped_fields 사용(사용률은 통합 피벗에 접혀 제외됨).
        if unmapped_fields:
            parts.append(
                "## 자동 매핑 실패 필드 (스키마에서 직접 조회 필요)\n"
                "아래 양식 필드들은 자동 매핑에 실패했습니다. "
                "스키마와 사용자 질의를 참고하여 적절한 DB 컬럼 또는 계산식으로 반드시 SELECT에 포함하세요.\n"
                "**중요**: 각 필드명을 그대로 SQL alias로 사용하세요 (따옴표 포함).\n"
                f"예: {decimal_cast_example(db_engine)}\n"
                "CPU/메모리/디스크 사용률·통계 관련 필드는 cmm_metric_stat_[h,d,m] 테이블을 활용하고, "
                "Template B 패턴을 따르세요:\n"
                + "\n".join(f'- "{f}"' for f in unmapped_fields)
            )

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
    descriptions = column_descriptions or {}
    synonyms = column_synonyms or {}

    # excluded_join_columns 추출: {(table_lower, column_lower): reason}
    excluded_join_map = build_excluded_join_map(schema_info)

    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_data in tables.items():
        # table_name에서 스키마 접두사 제거한 bare name 추출
        bare_table = table_name.rsplit(".", 1)[-1].lower()
        columns_desc: list[str] = []
        for col in table_data.get("columns", []):
            col_key = f"{table_name}.{col['name']}"
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            if not col.get("nullable", True):
                col_str += " NOT NULL"
            # JOIN 금지 컬럼 주석 추가
            col_lower = col["name"].lower()
            excluded_reason = excluded_join_map.get((bare_table, col_lower))
            if excluded_reason:
                col_str += f" -- JOIN 금지({excluded_reason})"
            # 컬럼 설명 추가
            desc = descriptions.get(col_key)
            if desc:
                col_str += f" -- {desc}"
            # 유사 단어 추가
            syns = synonyms.get(col_key)
            if syns:
                col_str += f" [유사: {', '.join(syns[:5])}]"
            columns_desc.append(col_str)
        lines.append(f"### {table_name}")
        lines.extend(columns_desc)

        # 샘플 데이터 (있으면, 3건까지 표시)
        samples = table_data.get("sample_data", [])
        if samples:
            preview = json.dumps(samples[:3], ensure_ascii=False, indent=2)
            if is_scrub_samples_enabled():
                preview = scrub_pii(preview)  # 라이브 샘플 PII → FabriX 필터 오탐 차단 예방
            lines.append(f"  샘플 데이터 ({len(samples)}건):\n{preview}")
        lines.append("")

    # resource_type_values 참조 정보
    if resource_type_synonyms:
        lines.append("")
        lines.append("### 참조: RESOURCE_TYPE 값과 한국어 표현")
        lines.append("아래 값들은 RESOURCE_TYPE 컬럼에 저장되는 값입니다. 사용자가 한국어로 질의할 때 아래 매핑을 참고하세요.")
        for rt_value, words in sorted(resource_type_synonyms.items()):
            lines.append(f"  - {rt_value} = {', '.join(words)}")

    # eav_name_values 참조 정보
    if eav_name_synonyms:
        lines.append("")
        lines.append("### 참조: EAV 설정 항목명과 한국어 표현")
        lines.append("아래는 EAV 테이블의 속성명 컬럼에 저장되는 설정 항목명입니다. 사용자가 한국어로 질의할 때 아래 매핑을 참고하세요.")
        for eav_name, words in sorted(eav_name_synonyms.items()):
            lines.append(f"  - {eav_name} = {', '.join(words)}")

    # FK 관계
    rels = schema_info.get("relationships", [])
    if rels:
        lines.append("### 테이블 관계 (FK)")
        for rel in rels:
            lines.append(f"  {rel['from']} -> {rel['to']}")

    return "\n".join(lines)


def _extract_sql_from_response(content: str) -> str:
    """LLM 응답에서 SQL 쿼리를 추출한다.

    Args:
        content: LLM 응답 텍스트

    Returns:
        추출된 SQL 문자열
    """
    # ```sql ... ``` 패턴
    sql_match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL)
    if sql_match:
        return sql_match.group(1).strip()

    # ``` ... ``` 패턴 (SELECT로 시작)
    code_match = re.search(
        r"```\s*(SELECT.*?)\s*```", content, re.DOTALL | re.IGNORECASE
    )
    if code_match:
        return code_match.group(1).strip()

    # SELECT로 시작하는 텍스트 직접 추출
    select_match = re.search(r"(SELECT\s+.*?;)", content, re.DOTALL | re.IGNORECASE)
    if select_match:
        return select_match.group(1).strip()

    # 전체 내용 반환 (최후 수단)
    return content.strip()


