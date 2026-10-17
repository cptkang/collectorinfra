"""처리기(subagent) 정의 스키마 — `SubAgentSpec` (plans/166 O4 · 종전 정의처 `subagents`).

leaf 모듈이다 — orchestration 모듈을 import하지 않는다. 조건부 처리기 모듈(`apm_query`·`doc_query`·
`portal_help`·`conditional_agents`)이 레지스트리(`subagents`)를 거치지 않고 정의 스키마만 쓰도록
떼어 냈다. 종전 이름(`subagents.SubAgentSpec`)은 재노출로 유지한다.

처리기 출력(task 결과 dict)의 키 선언 `HandlerResult`도 여기 둔다(plans/174 O7 — 순환 회피).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langchain_core.language_models import BaseChatModel


class HandlerResult(TypedDict, total=False):
    """처리기가 돌려주는 task 결과 dict(`task_results[task_id]`)의 키 선언(plans/174 O7).

    **선언만이다** — 런타임 객체는 그대로 dict이고 검사·변환을 하지 않는다(체크포인터 직렬화와
    처리기의 `**extra` 병합을 그대로 둔다 · pydantic 미도입). 키는 소비처 `replanner`·
    `result_aggregator`가 task 결과에서 실제로 읽는 것만 모았다(2026-10-16 grep). 값 모양이
    처리기마다 다르면 `Any`다. 새 키를 읽기 시작하면 여기에 더한다. 상수로 읽는 키는 그 상수
    정의처를 적었다.
    """

    # 조회 행·서술
    query_results: Any
    organized_data: Any
    final_response: Any
    display_rows_cut: Any  # `task_result_keys.DISPLAY_CUT_KEY`
    answer_lines: Any  # `apm_query.ANSWER_LINES_KEY`
    # 실패·미완 표지
    error: Any
    regen_stop: Any
    skipped: Any
    skip_reason: Any
    skipped_dbs: Any
    db_errors: Any
    degraded_reason: Any  # `task_result_keys.DEGRADED_KEY`
    routing_intent: Any  # `db_access.is_access_denied_result`
    accepted_jobs: Any
    zone_clarification: Any
    # 조회 대상·실행 기록
    source: Any
    target_db_ids: Any
    db_origin: Any
    db_result_summary: Any
    executed_sqls: Any
    spike_notes: Any
    empty_diagnosis: Any
    time_resolution: Any
    # 경과 노트·고지
    dependency_notes: Any
    disclosures: Any
    # 양식 채우기(D-146/D-151 · D-187)
    form_month_anchor: Any
    form_fill_candidates: Any
    form_fill_overrides: Any
    form_fill_literals: Any
    form_memory_panel: Any
    # APM 처리기(`apm_query.META_KEY` · `apm_query.SOURCE_CLARIFICATION_KEY`)
    apm_query: Any
    apm_source_clarification: Any


@dataclass(frozen=True)
class SubAgentSpec:
    """subagent 정의 (deepagents SubAgent dict와 1:1 대응).

    Attributes:
        name: task() 호출 시 식별자
        description: planner 위임 분류 근거 (SubAgent S2)
        handler: 실행 진입점 (기존 노드/파이프라인 래퍼)
        model: per-agent 모델 슬롯 — Phase 7 예약 (None=메인 LLM 사용)
        prompt: per-agent 프롬프트 슬롯 — Phase 7 예약
        fallback: general-purpose(미분류 시 기본) 여부
        needs_history: 이전 대화 이력(messages)을 격리 입력에 전달할지 여부(①).
            추론/판단형 응답(general_inference)은 직전 턴 답변을 근거로 삼아야 하므로 True.
            데이터 조회 agent(data_query/process_query)는 이력이 SQL 생성에 불필요·토큰
            부담·오염 위험이 있어 False(격리 유지).

    처리기 계약(plans/121 §4.4 · TP-2.1a) — 값은 현행 코드 실측이다. **이번 단계 소비처 0**:
    `description`·분해/재계획/1단 프롬프트는 바이트 그대로이고 목록 렌더 교체는 TP-2.1b다.
    모르는 값은 None(추정 금지). 슬롯 어휘는 §4.5 — `entity_set`(서버 식별 집합 — 현행
    `prior_rows`·`prior_targets`·`filter_conditions` 식별 키) · `db_set`(`task.db_ids`·존 선택) ·
    `time_window`(`parsed_requirements.time_range`).

        purpose: 목적 1문장(TP-2.1b 목록 렌더 원천)
        backend: 닿는 소스 종류 — `sql` · `mcp` · `rest` · `none`(None = 미선언)
        input_slots: 받는 슬롯
        required_inputs: 반드시 바인딩돼야 하는 슬롯(없으면 조회하지 않고 사유를 돌려준다)
        output_type: `rows` · `entity_set` · `scalar` · `text` · `file`(None = 미선언)
        key_facets_out: 결과 행에서 후속 단계가 뽑을 수 있는 키 패싯
        self_filters: 처리기가 스스로 거는 필터 — `zone` · `time` · `host`
        max_bind_values: 한 task가 받는 선행 키 값 상한(None = 설정값·상한 없음)
        bind_block: 한 번 호출에 싣는 키 값 수(None = 배치 분할 없음)
        side_effects: 관측 데이터 소스에 대한 쓰기 — 전부 `none`(D-003 읽기 전용). 앱 자체 저장소
            (스키마 캐시·유사어) 쓰기는 이 필드의 대상이 아니다.
        prerequisites: 선행 준비(없으면 조회하지 않거나 사유 노트로 끝난다)
        latency_class: 지연 등급(None = 미측정 — TP-0.4 노드별 소요 뒤)
        timeout_sec: 처리기 고정 호출 상한(None = 고정값 없음 — 요청 마감 파생 또는 설정값)
        concurrency: 처리기 동시 호출 상한(None = 상한 없음 — 병렬은 task 레벨 · TP-10.7)
    """

    name: str
    description: str
    handler: Callable[..., Any]
    model: BaseChatModel | None = None
    prompt: str | None = None
    fallback: bool = False
    needs_history: bool = False
    purpose: str = ""
    backend: str | None = None
    input_slots: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    output_type: str | None = None
    key_facets_out: tuple[str, ...] = ()
    self_filters: tuple[str, ...] = ()
    max_bind_values: int | None = None
    bind_block: int | None = None
    side_effects: str = "none"
    prerequisites: tuple[str, ...] = ()
    latency_class: str | None = None
    timeout_sec: float | None = None
    concurrency: int | None = None
