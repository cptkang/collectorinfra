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
    rows: list[dict[str, Any]]


class OrganizedData(TypedDict):
    """정리된 결과 데이터."""

    summary: str
    rows: list[dict[str, Any]]
    column_mapping: Optional[dict[str, str]]
    is_sufficient: bool
    sheet_mappings: Optional[list[SheetMappingResult]]


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

    # === DB 관련 ===
    relevant_tables: list[str]               # 관련 테이블 목록
    schema_info: dict                        # 스키마 상세 (테이블, 컬럼, FK)
    column_descriptions: dict[str, str]      # 컬럼 설명 {table.column: description}
    column_synonyms: dict[str, list[str]]    # 유사 단어 {table.column: [synonym, ...]}
    resource_type_synonyms: dict[str, list[str]]  # RESOURCE_TYPE 값 유사단어
    eav_name_synonyms: dict[str, list[str]]       # EAV NAME 값 유사단어
    generated_sql: str                       # 현재 SQL 쿼리
    validation_result: ValidationResult      # 검증 결과
    query_results: list[dict[str, Any]]      # 현재 쿼리 실행 결과

    # === 가공 결과 ===
    organized_data: OrganizedData            # 정리된 데이터

    # === 제어 ===
    retry_count: int                         # 재시도 횟수 (최대 3)
    error_message: Optional[str]             # 에러 메시지 (재시도 시 참조)
    current_node: str                        # 현재 실행 중인 노드

    # === 실행 이력 ===
    query_attempts: list[QueryAttempt]       # SQL 시도 이력 (디버깅/감사용)

    # === 필드 매핑 (field_mapper 노드에서 생성) ===
    column_mapping: Optional[dict[str, Optional[str]]]       # 통합 매핑 {field: "table.column"}
    db_column_mapping: Optional[dict[str, dict[str, str]]]   # DB별 매핑 {db_id: {field: "table.column"}}
    mapping_sources: Optional[dict[str, str]]                # 매핑 출처 {field: "hint"|"synonym"|"llm_inferred"}
    mapped_db_ids: Optional[list[str]]                       # 매핑에서 식별된 DB 목록
    pending_synonym_registrations: Optional[list[dict]]      # 유사어 등록 대기 [{index, field, column, db_id}]

    # === 유사단어 재활용 대기 ===
    pending_synonym_reuse: Optional[dict]
    # {
    #   "target_column": "server_name",
    #   "target_db_id": "new_db",  (선택)
    #   "suggestions": [{"column": "hostname", "words": [...], "description": "..."}],
    # }

    # === 시멘틱 라우팅 ===
    routing_intent: Optional[str]            # 라우팅 의도 ("data_query" | "cache_management")
    target_databases: list[dict]             # 라우팅된 대상 DB 목록 (DBRouteTarget)
    active_db_id: Optional[str]              # 현재 처리 중인 DB 식별자
    db_results: dict[str, list[dict]]        # DB별 쿼리 결과 {db_id: rows}
    db_schemas: dict[str, dict]              # DB별 스키마 정보 {db_id: schema_info}
    db_errors: dict[str, str]                # DB별 에러 메시지 {db_id: error_msg}
    is_multi_db: bool                        # 멀티 DB 쿼리 여부
    user_specified_db: Optional[str]         # 사용자가 직접 지정한 DB (없으면 None)

    # === [Phase 3] 멀티턴 대화 ===
    messages: Annotated[list[BaseMessage], add_messages]  # 대화 히스토리 (누적 reducer)
    thread_id: Optional[str]                              # 세션 식별자
    conversation_context: Optional[dict]                  # context_resolver가 추출한 이전 맥락

    # === [Phase 3] Human-in-the-loop ===
    awaiting_approval: bool                    # 사용자 승인 대기 여부
    approval_context: Optional[dict]           # 승인 요청 컨텍스트
    approval_action: Optional[str]             # 사용자 승인 응답 ("approve"|"reject"|"modify")
    approval_modified_sql: Optional[str]       # 수정된 SQL (modify 시)

    # === 출력 ===
    final_response: str                      # 자연어 응답
    output_file: Optional[bytes]             # 생성된 파일 바이너리
    output_file_name: Optional[str]          # 출력 파일명


def create_initial_state(
    user_query: str,
    uploaded_file: Optional[bytes] = None,
    file_type: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> AgentState:
    """초기 State를 생성한다.

    Args:
        user_query: 사용자 자연어 질의
        uploaded_file: 업로드된 파일 바이너리 (선택)
        file_type: 파일 유형 (선택)
        thread_id: 세션 식별자 (선택, 멀티턴 대화용)

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
        relevant_tables=[],
        schema_info={},
        column_mapping=None,
        db_column_mapping=None,
        mapping_sources=None,
        mapped_db_ids=None,
        pending_synonym_registrations=None,
        pending_synonym_reuse=None,
        column_descriptions={},
        column_synonyms={},
        resource_type_synonyms={},
        eav_name_synonyms={},
        generated_sql="",
        validation_result={"passed": False, "reason": "", "auto_fixed_sql": None},
        query_results=[],
        organized_data={
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "is_sufficient": False,
            "sheet_mappings": None,
        },
        retry_count=0,
        error_message=None,
        current_node="",
        query_attempts=[],
        routing_intent=None,
        target_databases=[],
        active_db_id=None,
        db_results={},
        db_schemas={},
        db_errors={},
        is_multi_db=False,
        user_specified_db=None,
        # Phase 3: 멀티턴 대화
        messages=[HumanMessage(content=user_query)],
        thread_id=thread_id,
        conversation_context=None,
        # Phase 3: Human-in-the-loop
        awaiting_approval=False,
        approval_context=None,
        approval_action=None,
        approval_modified_sql=None,
        # 출력
        final_response="",
        output_file=None,
        output_file_name=None,
    )
