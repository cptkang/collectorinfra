"""에이전트 상태 정의 모듈.

LangGraph 에이전트의 전역 상태(AgentState)와 관련 타입을 정의한다.
모든 노드는 이 State를 읽고/쓰며, 각 노드가 담당하는 필드만 쓰기를 수행한다.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph import add_messages


class ValidationResult(TypedDict):
    """SQL 검증 결과."""

    passed: bool
    reason: str
    auto_fixed_sql: Optional[str]


class SheetMappingResult(TypedDict):
    """시트별 필드 매핑 및 데이터 결과."""

    sheet_name: str
    column_mapping: Optional[dict[str, str]]
    resolved_mapping: Optional[dict[str, str]]
    rows: list[dict[str, Any]]


class OrganizedData(TypedDict):
    """정리된 결과 데이터."""

    summary: str
    rows: list[dict[str, Any]]
    column_mapping: Optional[dict[str, str]]
    resolved_mapping: Optional[dict[str, str]]
    is_sufficient: bool
    sheet_mappings: Optional[list[SheetMappingResult]]


class SmqDerivation(TypedDict):
    """단계적 컬럼 도출 루프 1회 실행 기록 (Plan 67 S2 / D-128).

    감사·평가(S3 토큰·지연 상한 판정)와 미해결 사유 노출에 쓰는 관측 레코드다.
    루프가 실제로 발동한 경로마다 1건 누적되며(단일 1건 / 멀티 DB는 DB별), 플래그 OFF나
    미발동이면 ``AgentState.smq_derivation``이 None으로 남는다.
    """

    path: str                       # 발동 경로 라벨 ("single" | "multi_db")
    db_id: str
    smq: Optional[dict]             # 루프가 누적한 SMQ(도출 실패 시 None)
    fields: list[dict]              # [{field, role, selection, evidence, confidence}]
    unresolved: list[dict]          # [{field, reason}] — 미해결 필드의 구조화 사유
    rounds: int                     # tool-calling 라운드 수
    tool_calls: int                 # 누적 tool 호출 수
    llm_calls: int                  # 누적 LLM 호출 수(요구 분해 포함)
    elapsed_ms: float
    stopped_reason: str             # completed | max_rounds | max_tool_calls | timeout | ...
    covered: Optional[bool]         # 누적 SMQ의 커버리지 판정 결과(미판정 None)
    guards: dict[str, int]          # 이 질의에서 발동한 교정 가드 {이름: 횟수} (Plan 67 R4)


class QueryAttempt(TypedDict):
    """개별 SQL 실행 시도 기록.

    query_executor 노드에서 각 실행 시도마다 기록한다.
    디버깅과 감사 로그용으로 활용된다.
    """

    sql: str
    success: bool
    error: Optional[str]
    row_count: int
    execution_time_ms: float


class AgentState(TypedDict):
    """LangGraph 에이전트의 전역 상태.

    모든 노드는 이 State를 읽고/쓰며, 각 노드가 담당하는
    필드만 쓰기를 수행한다.
    """

    # === 사용자 입력 ===
    user_query: str                          # 자연어 질의
    uploaded_file: Optional[bytes]           # 업로드된 양식 파일 바이너리
    file_type: Optional[str]                 # "xlsx" | "docx" | None

    # === 파싱 결과 ===
    parsed_requirements: dict                # 구조화된 요구사항
    template_structure: Optional[dict]       # 양식 구조 정보
    target_sheets: Optional[list[str]]       # 대상 시트 목록 (None이면 전체 시트)
    csv_sheet_data: Optional[dict[str, Any]]  # 시트별 CsvSheetData (dict 형태)

    # === DB 관련 ===
    relevant_tables: list[str]               # 관련 테이블 목록
    schema_info: dict                        # 스키마 상세 (테이블, 컬럼, FK)
    schema_cache_source: Optional[str]       # 스키마 캐시 출처 ("메모리" | "Redis" | "DB 직접 조회")
    column_descriptions: dict[str, str]      # 컬럼 설명 {table.column: description}
    column_synonyms: dict[str, list[str]]    # 유사 단어 {table.column: [synonym, ...]}
    resource_type_synonyms: dict[str, list[str]]  # RESOURCE_TYPE 값 유사단어
    eav_name_synonyms: dict[str, list[str]]       # EAV NAME 값 유사단어
    generated_sql: str                       # 현재 SQL 쿼리 (다중 후보 경로에서는 선택 결과)
    sql_candidates: Optional[list[dict]]     # 트랙 A(E2) 다중 후보 [{sql, strategy, confidence}]; 단일 경로는 None
    text2sql_fallback: Optional[dict]        # 트랙 A 3단 폴백 결과 {tier, confidence, method, reason}; 미진입 None
    smq_derivation: Optional[list[SmqDerivation]]  # 트랙 S(S2/D-128) 단계적 도출 기록; 미발동 None
    column_value_index: Optional[dict[str, list[str]]]  # E5-2 실측 값 인덱스 런타임 주입 {column: [값,...]}
    synonym_usage: Optional[dict]            # SQL에 사용된 유사어 매핑 역조회 결과 (처리 현황 표시용)
    # FabriX PII 필터 차단 시 프롬프트 섹션별 로컬 스캔 진단(D-155) — query_generator가
    # 차단 감지 시 산출, query_validator가 에러 메시지에 노출(폐쇄망 UI 자가 진단).
    # 생성 시도 스코프 값(차단 아닌 생성이 성공하면 의미 없음 — 소비부가 차단 시에만 읽음).
    pii_block_diagnosis: Optional[str]
    validation_result: ValidationResult      # 검증 결과
    query_results: list[dict[str, Any]]      # 현재 쿼리 실행 결과

    # === 가공 결과 ===
    organized_data: OrganizedData            # 정리된 데이터

    # === 제어 ===
    retry_count: int                         # 재시도 횟수 (최대 3)
    error_message: Optional[str]             # 에러 메시지 (재시도 시 참조)
    current_node: str                        # 현재 실행 중인 노드
    # 원문 기준 LIMIT 확정값 (Plan 75 §3 / D-066 후속). 오케스트레이션이 user_query를
    # sub_query/sub_query_context로 교체하기 전에 원문으로 계산해 승격한다 — 문자열 훼손과
    # 무관하게 보존. None이면 소비부(resolve_effective_limit)가 user_query로 폴백 계산.
    # 요청 스코프 값이므로 매 턴 초기화(create_initial_state/create_followup_input).
    resolved_limit: Optional[int]

    # === 실행 이력 ===
    query_attempts: list[QueryAttempt]       # SQL 시도 이력 (디버깅/감사용)

    # === 필드 매핑 (field_mapper 노드에서 생성) ===
    column_mapping: Optional[dict[str, Optional[str]]]       # 통합 매핑 {field: "table.column"}
    db_column_mapping: Optional[dict[str, dict[str, str]]]   # DB별 매핑 {db_id: {field: "table.column"}}
    mapping_sources: Optional[dict[str, str]]                # 매핑 출처 {field: "hint"|"synonym"|"llm_inferred"}
    mapped_db_ids: Optional[list[str]]                       # 매핑에서 식별된 DB 목록
    pending_synonym_registrations: Optional[list[dict]]      # 유사어 등록 대기 [{index, field, column, db_id}]
    llm_inference_details: Optional[list[dict]]              # LLM 추론 매핑 상세 [{field, db_id, column, matched_synonym, confidence, reason}]
    mapping_report_md: Optional[str]                         # 매핑 보고서 Markdown 텍스트
    # 폼필 월 시리즈(M~M+5) 인식 결과(D-146) — {start, end: YYYYMM, resource_type, fields}.
    # output_generator가 기준월을 응답에 명시하고 인식 필드를 미작성 사유(D-147)에서 제외.
    # 요청 스코프 값 — 매 턴 초기화.
    form_month_anchor: Optional[dict]

    # === 멀티턴 HITL 폼필 (Plan 73 Phase 2, D-151) ===
    # 역질문 답변(요청 스코프 — route가 이번 턴 값 주입, followup에서 매 턴 초기화).
    # {field: {"action": "blank"|"column"|"eav"|"literal", "value": str|None}}
    form_fill_answers: Optional[dict[str, dict]]
    # 검증 통과 오버라이드(요청 스코프, query_generator/multi 산출) — 사유 노출용.
    # {field: {"action":..., "value":..., "applied": bool, "reason": str|None}}
    form_fill_overrides: Optional[dict[str, dict]]
    # 직접 입력 상수(요청 스코프) — writer가 전 데이터 행 동일값 기입. {field: value}
    form_fill_literals: Optional[dict[str, str]]
    # 역질문 드롭다운 후보(요청 스코프, 스키마 실측 산출) — [{value, label, kind}]
    form_fill_candidates: Optional[list[dict]]
    # 역질문 대기 상태(멀티턴 보존 — pending_synonym_registrations 동형).
    # {uploaded_file: bytes, file_type: str, original_query: str,
    #  unresolved: [필드명, ...], candidates: [...]}
    # 정리: ①답변 적용 후 미해결 0 ②새 파일 업로드 턴(교체). output_generator가 관리.
    pending_form_fill: Optional[dict]
    # 역질문 페이로드(요청 스코프) — API 응답이 프론트 패널 렌더에 사용.
    # {"question": str, "fields": [{"name", "reason"}], "candidates": [...]}
    form_fill_clarification: Optional[dict]
    # 기억 옵트인(요청 스코프, Phase 3) — 답변 턴에서만 라우트가 주입.
    form_fill_remember: Optional[bool]
    # 직전 양식 시그니처(멀티턴 보존, FIX-23) — 파일 재첨부 없는 "기억 보여줘/삭제"가
    # 직전 양식을 가리키게 한다. 양식 턴(②.7 조회·③.5 채우기)마다 갱신(최신 승리),
    # followup에서 비우지 않는다(pending_* 계열).
    last_form_signature: Optional[str]

    # === 유사단어 재활용 대기 ===
    pending_synonym_reuse: Optional[dict]
    # {
    #   "target_column": "server_name",
    #   "target_db_id": "new_db",  (선택)
    #   "suggestions": [{"column": "hostname", "words": [...], "description": "..."}],
    # }

    # === DB 엔진 정보 ===
    active_db_engine: Optional[str]  # 현재 DB의 엔진 타입 ("db2", "postgresql", etc.)

    # === 시멘틱 라우팅 ===
    routing_intent: Optional[str]            # 라우팅 의도 ("data_query" | "cache_management")
    target_databases: list[dict]             # 라우팅된 대상 DB 목록 (DBRouteTarget)
    active_db_id: Optional[str]              # 현재 처리 중인 DB 식별자
    db_results: dict[str, list[dict]]        # DB별 쿼리 결과 {db_id: rows}
    db_schemas: dict[str, dict]              # DB별 스키마 정보 {db_id: schema_info}
    db_errors: dict[str, str]                # DB별 에러 메시지 {db_id: error_msg}
    is_multi_db: bool                        # 멀티 DB 쿼리 여부
    user_specified_db: Optional[str]         # 사용자가 직접 지정한 DB (없으면 None)
    # 존 역질문(Plan 75 §4)에서 사용자가 체크박스로 선택한 DB 목록. LLM 재해석 없이
    # semantic_router/intent_planner가 mapped_db_ids 선례로 결정적 고정한다.
    # 요청 스코프 — 매 턴 라우트가 재공급(미선택 턴은 None).
    selected_db_ids: Optional[list[str]]
    # 존 역질문 후단 게이트 허용 채널 여부(D-143 후속2). 대화형 텍스트 라우트만 True로
    # 주입 — API 직접 호출·배치·평가 하네스는 역질문에 답할 수 없어 기존 폴백 유지
    # (§4.3-3 비대화 경로 분기). 요청 스코프 — 매 턴 라우트가 재공급.
    zone_clarification_allowed: Optional[bool]
    # 존 역질문 후단 게이트 발동 페이로드(D-143 후속2, 요청 스코프) — 라우트가
    # status="clarification" 응답으로 변환(pre-gate와 동일 shape, 프론트 재사용).
    zone_clarification: Optional[dict]

    # === [Phase 3] 멀티턴 대화 ===
    messages: Annotated[list[BaseMessage], add_messages]  # 대화 히스토리 (누적 reducer)
    thread_id: Optional[str]                              # 세션 식별자
    conversation_context: Optional[dict]                  # context_resolver가 추출한 이전 맥락
    # conversation_context 구조 (context_resolver가 채움, 후속 턴에만 non-None):
    #   previous_sql: str                  — 직전 턴 생성 SQL
    #   previous_results_summary: str      — "N건 조회됨, 컬럼: ..."
    #   previous_result_count: int
    #   previous_tables: list[str]
    #   previous_db_id: Optional[str]      — 직전 단일 DB 경로 active_db_id (레거시 호환)
    #   turn_count: int
    #   has_pending_synonym_reuse / has_pending_synonym_registrations / pending_synonym_reg_count
    #   [Plan 50 / M3 신규]
    #   previous_db_ids: list[str]         — 직전 턴 대상 DB 통합(target_databases∪active_db_id∪mapped_db_ids).
    #                                        후속 턴 DB 승계 우선 후보 (M2).
    #   previous_entities: list[dict]      — [{"field": "hostname", "value": "###"}] 직전 식별 서버/장비
    #                                        (filter_conditions 식별 키 + 결과 식별 컬럼 값, 행수 상한). "해당 서버" 해소.
    #   previous_location: str             — 직전 폴스타 위치/환경 신호("김포 운영" 등). DB 식별 신호 승계.

    # === [Phase 3] Human-in-the-loop ===
    awaiting_approval: bool                    # 사용자 승인 대기 여부
    approval_context: Optional[dict]           # 승인 요청 컨텍스트
    approval_action: Optional[str]             # 사용자 승인 응답 ("approve"|"reject"|"modify")
    approval_modified_sql: Optional[str]       # 수정된 SQL (modify 시)

    # === 사용자 컨텍스트 (인증 시스템에서 주입) ===
    user_id: Optional[str]                   # "anonymous" 또는 실제 user_id
    user_department: Optional[str]
    # 조사 인가 판정 재료 (Plan 78 W3-5 · C-4). **없으면 차단**된다(fail-closed) —
    # 전파 누락이 곧 fail-open이 되지 않도록 판정 기본값을 거부로 두었다.
    user_role: Optional[str]
    allowed_db_ids: Optional[list[str]]      # None=전체 허용

    # === 감사 로깅 ===
    request_id: Optional[str]                # 요청 추적 ID
    client_ip: Optional[str]                 # 클라이언트 IP
    accessed_tables: list[str]               # 실제 접근한 테이블 목록 (미소비 — 쓰기 없음, Plan 69 §1.6. 삭제는 별건)

    # === [D-176] 실행 그룹 (plans/82 §4.7) — 전부 **요청 스코프**다 ===
    # 라우트가 매 턴 명시 초기화해야 한다(체크포인터는 델타만 병합 — Known Mistakes).
    execution_groups: Optional[list[dict]]   # 순서 확정된 실행 그룹 목록(미설정=단일 그룹 폴백)
    group_results: Optional[dict[str, dict]] # {group_key: {row_count, elapsed_ms, errors, sqls}}
    group_packets: Optional[list[dict]]      # peer 그룹의 부분 결과(완료 즉시 노출용 — 문헌 정정 ②)
    db_result_summary: Optional[dict[str, dict]]  # result_merger의 DB별 요약(종전 폐기분 승격)
    # 0건 원인 진단(D-176 후속1 · §6). `src.domain.empty_answer.as_payload()` 산출물 —
    # 체크포인터 직렬화 대상이라 dataclass가 아니라 dict로 싣는다.
    empty_diagnosis: Optional[dict]
    # 급증 조회의 **한계 표기**(D-176 후속2 · §6.12) — 기본 임계값·용량 미대조·주 단위 차단.
    # 응답에 결정적으로 덧붙인다(LLM에 맡기면 누락된다).
    spike_notes: Optional[list[str]]
    # 존 순회 탐색 경과(D-176 후속3 · §4.3) — 어느 존을 돌았고 어디가 실패했는지.
    # `src.domain.host_discovery.trace_payload()` 산출물(체크포인터 직렬화 대상 dict).
    discovery_trace: Optional[dict]
    # 범위 축소 기록(D-176 후속4 · §5.3 불변식 6) — {selected, skipped, skipped_db_ids}.
    # **미조회 범위를 남기지 않으면 침묵 절단이다**(복구 불가한 정보 손실).
    scope_narrowed: Optional[dict]

    # === 출력 ===
    final_response: str                      # 자연어 응답
    output_file: Optional[bytes]             # 생성된 파일 바이너리
    output_file_name: Optional[str]          # 출력 파일명

    # === [Plan 48] deepagents 의도 분해 오케스트레이션 ===
    task_plan: list[dict]            # intent_planner 결과 (TaskSpec 목록; 각 항목에 status)
    task_results: dict[str, dict]    # {task_id: {organized_data, query_results, source, error, ...}}
    is_composite: bool               # task 2개 이상 여부
    prior_rows: Optional[dict[str, list[dict]]]  # 선행 task 결과 식별 행 {task_id: [행, ...]} (input_from 주입, D-086)
    # 선행 결과에서 해소한 **조사 대상** [{server_name, hostname, ip, db_id}] (Plan 78 W1-5).
    # prior_rows(SQL 스코프 키)와 목적이 다르다 — 이쪽은 실호스트 조사의 대상 집합이다.
    # 값은 dict 목록으로 싣는다(TargetRef.model_dump()) — 체크포인터 직렬화 대상이므로.
    prior_targets: Optional[list[dict]]

    # === [Plan 49] 동적 재계획 ===
    replan_count: int                # 결과 기반 재계획 반복 횟수 (MAX_REPLAN 상한)
    needs_replan: bool               # replanner → 라우팅 신호 (True면 agent_orchestrator 재진입)
    replan_history: list[dict]       # 재계획 이력 [{count, reason, added}] (처리 현황 표시용, 루프 누적)


def create_followup_input(
    user_query: str,
    selected_db_ids: Optional[list[str]] = None,
    allow_zone_clarification: bool = False,
) -> dict:
    """후속(텍스트) 턴의 델타 입력을 생성한다 (D-064).

    멀티턴에서 체크포인터는 이전 턴 State 전체를 복원하고, 텍스트 경로는 델타 키만
    병합한다. 따라서 직전 폼업로드 턴의 **요청-스코프 폼필 트리거**(uploaded_file/
    file_type/csv_sheet_data)가 새 텍스트 턴으로 잔존하면 input_parser가 옛 파일을
    재파싱하여 template_structure를 되살리고(field_mapper 재실행) → intent_planner가
    옛 mapped_db_ids로 DB를 고정한다(2026-07-09 버그). 이를 막기 위해 트리거를
    명시적으로 비운다. 나머지 매핑 산출물(mapped_db_ids/column_mapping 등)은
    field_mapper 스킵 경로가 정리한다(단일 출처, 진입 경로 무관).

    세션 승계 신호(messages/conversation_context/pending_synonym_reuse/
    pending_synonym_registrations/승인 컨텍스트)는 **보존**한다 — 멀티턴 지시어 해소
    (D-055/D-056)와 유사어 등록 흐름이 이 신호에 의존한다.

    Args:
        user_query: 이번 턴 자연어 질의

    Returns:
        graph.ainvoke에 전달할 델타 입력 dict
    """
    return {
        "user_query": user_query,
        "messages": [HumanMessage(content=user_query)],
        # 폼필 트리거 초기화 (input_parser 재파싱 방지). 파일 업로드 경로는 이 함수를
        # 쓰지 않고 create_initial_state로 새 파일을 실어 보낸다.
        "uploaded_file": None,
        "file_type": None,
        "csv_sheet_data": None,
        # 원문 기준 LIMIT 확정값은 요청 스코프 — 직전 턴 값이 승계되지 않도록 명시 초기화
        # (이번 턴 원문으로 오케스트레이션/소비부가 재계산·재승격한다. Plan 75 §3).
        "resolved_limit": None,
        # 폼필 월 시리즈 앵커(D-146)도 요청 스코프 — 직전 폼필 턴 값이 텍스트 턴 응답에
        # 기준월 안내로 잔존하지 않도록 명시 초기화(field_mapper 산출물이 아니라 자기정리 필요).
        "form_month_anchor": None,
        # 존 선택(Plan 75 §4)도 요청 스코프 — 이번 턴 선택값 또는 None으로 매 턴 재공급
        # (직전 턴 선택이 체크포인터로 승계돼 새 질의를 오염시키지 않도록).
        "selected_db_ids": selected_db_ids,
        # 존 역질문 후단 게이트(D-143 후속2) — 채널 플래그·발동 페이로드 모두 요청 스코프.
        # 직전 턴 발동 페이로드가 체크포인터로 승계돼 새 턴 응답을 오염시키지 않도록 초기화.
        "zone_clarification_allowed": allow_zone_clarification,
        "zone_clarification": None,
        # HITL 폼필(D-151) 요청 스코프 값들 — 직전 턴 산출이 새 턴을 오염시키지 않도록
        # 매 턴 초기화. 답변 턴은 route가 이 델타 위에 form_fill_answers·복원 파일을 덮어쓴다.
        # pending_form_fill(멀티턴 보존)은 여기서 비우지 않는다.
        "form_fill_answers": None,
        "form_fill_overrides": None,
        "form_fill_literals": None,
        "form_fill_candidates": None,
        "form_fill_clarification": None,
        "form_fill_remember": None,
    }


def create_initial_state(
    user_query: str,
    uploaded_file: Optional[bytes] = None,
    file_type: Optional[str] = None,
    thread_id: Optional[str] = None,
    csv_sheet_data: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
    user_department: Optional[str] = None,
    user_role: Optional[str] = None,
    allowed_db_ids: Optional[list[str]] = None,
    request_id: Optional[str] = None,
    client_ip: Optional[str] = None,
    selected_db_ids: Optional[list[str]] = None,
    resolved_limit: Optional[int] = None,
    allow_zone_clarification: bool = False,
) -> AgentState:
    """초기 State를 생성한다.

    Args:
        user_query: 사용자 자연어 질의
        uploaded_file: 업로드된 파일 바이너리 (선택)
        file_type: 파일 유형 (선택)
        thread_id: 세션 식별자 (선택, 멀티턴 대화용)
        csv_sheet_data: 시트별 CsvSheetData dict (선택, Excel CSV 변환 결과)
        user_id: 인증된 사용자 ID (선택, 인증 비활성화 시 None)
        user_department: 사용자 부서 (선택)
        user_role: 사용자 역할 (조사 인가 판정용 — 없으면 조사 차단)
        allowed_db_ids: 허용 DB 목록 (선택, None=전체 허용)
        request_id: 요청 추적 ID (선택, 미들웨어에서 주입)
        client_ip: 클라이언트 IP (선택, 미들웨어에서 주입)

    Returns:
        초기화된 AgentState
    """
    return AgentState(
        user_query=user_query,
        uploaded_file=uploaded_file,
        file_type=file_type,
        parsed_requirements={},
        template_structure=None,
        target_sheets=None,
        csv_sheet_data=csv_sheet_data,
        relevant_tables=[],
        schema_info={},
        schema_cache_source=None,
        column_mapping=None,
        db_column_mapping=None,
        mapping_sources=None,
        mapped_db_ids=None,
        pending_synonym_registrations=None,
        llm_inference_details=None,
        mapping_report_md=None,
        form_month_anchor=None,
        # HITL 폼필(D-151): answers는 라우트가 답변 턴에만 주입, pending은 멀티턴 보존
        # (새 파일 업로드 턴은 output_generator가 새 미해결로 교체).
        form_fill_answers=None,
        form_fill_overrides=None,
        form_fill_literals=None,
        form_fill_candidates=None,
        form_fill_clarification=None,
        form_fill_remember=None,
        last_form_signature=None,
        pending_form_fill=None,
        pending_synonym_reuse=None,
        column_descriptions={},
        column_synonyms={},
        resource_type_synonyms={},
        eav_name_synonyms={},
        generated_sql="",
        sql_candidates=None,
        text2sql_fallback=None,
        smq_derivation=None,
        column_value_index=None,
        synonym_usage=None,
        pii_block_diagnosis=None,
        validation_result={"passed": False, "reason": "", "auto_fixed_sql": None},
        query_results=[],
        organized_data={
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": False,
            "sheet_mappings": None,
        },
        retry_count=0,
        error_message=None,
        current_node="",
        # 라우트가 원문 기준으로 승격한 LIMIT 확정값(D-066 후속7). 파일(폼필) 경로는
        # 전량 채움이 기본이라 라우트가 상향값을 명시 전달한다(Plan 71 후속 — 폼필 1000 절단).
        resolved_limit=resolved_limit,
        query_attempts=[],
        active_db_engine=None,
        routing_intent=None,
        target_databases=[],
        active_db_id=None,
        db_results={},
        db_schemas={},
        db_errors={},
        is_multi_db=False,
        user_specified_db=None,
        selected_db_ids=selected_db_ids,
        zone_clarification_allowed=allow_zone_clarification,
        zone_clarification=None,
        # Phase 3: 멀티턴 대화
        messages=[HumanMessage(content=user_query)],
        thread_id=thread_id,
        conversation_context=None,
        # Phase 3: Human-in-the-loop
        awaiting_approval=False,
        approval_context=None,
        approval_action=None,
        approval_modified_sql=None,
        # 사용자 컨텍스트
        user_id=user_id,
        user_department=user_department,
        user_role=user_role,
        allowed_db_ids=allowed_db_ids,
        # 감사 로깅
        request_id=request_id,
        client_ip=client_ip,
        accessed_tables=[],
        # D-176 실행 그룹 — 요청 스코프(이전 턴 승계 차단)
        execution_groups=None,
        group_results=None,
        group_packets=None,
        db_result_summary=None,
        empty_diagnosis=None,
        spike_notes=None,
        discovery_trace=None,
        scope_narrowed=None,
        # 출력
        final_response="",
        output_file=None,
        output_file_name=None,
        # Plan 48: deepagents 오케스트레이션
        task_plan=[],
        task_results={},
        is_composite=False,
        prior_rows=None,  # 요청 스코프 — 명시 초기화로 이전 턴 승계 차단 (Plan 69 P0-⑥)
        # 요청 스코프 — LangGraph 체크포인터는 델타만 병합하므로 명시 초기화가 없으면
        # 이전 턴 대상이 승계돼 엉뚱한 호스트를 조사한다(Plan 78 W1-5).
        prior_targets=None,
        # Plan 49: 동적 재계획
        replan_count=0,
        needs_replan=False,
        replan_history=[],
    )
