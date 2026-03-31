"""LangGraph 그래프 빌드 모듈.

에이전트의 노드, 엣지, 조건부 라우팅을 정의하고
컴파일된 그래프를 반환한다.
LLM 인스턴스를 한 번 생성하여 partial로 노드에 주입한다.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import partial

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.config import AppConfig
from src.llm import create_llm
from src.nodes.approval_gate import approval_gate
from src.nodes.cache_management import cache_management
from src.nodes.context_resolver import context_resolver
from src.nodes.field_mapper import field_mapper
from src.nodes.input_parser import input_parser
from src.nodes.multi_db_executor import multi_db_executor
from src.nodes.output_generator import output_generator
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import query_validator
from src.nodes.result_merger import result_merger
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import schema_analyzer
from src.nodes.structure_approval_gate import structure_approval_gate
from src.nodes.synonym_registrar import synonym_registrar
from src.routing.semantic_router import semantic_router
from src.state import AgentState

logger = logging.getLogger(__name__)


def route_after_validation(state: AgentState) -> str:
    """query_validator 이후 라우팅을 결정한다.

    - 검증 통과: query_executor (또는 approval_gate)로 진행
    - 검증 실패 + 재시도 가능: query_generator로 회귀
    - 검증 실패 + 재시도 초과: error_response로 종료
    """
    if state["validation_result"]["passed"]:
        return "query_executor"
    if state["retry_count"] >= 3:
        return "error_response"
    return "query_generator"


def route_after_validation_with_approval(state: AgentState) -> str:
    """query_validator 이후 라우팅 (SQL 승인 활성화 시).

    검증 통과 시 approval_gate로 보낸다.
    """
    if state["validation_result"]["passed"]:
        return "approval_gate"
    if state["retry_count"] >= 3:
        return "error_response"
    return "query_generator"


def route_after_approval(state: AgentState) -> str:
    """approval_gate 이후 라우팅을 결정한다.

    - reject: 종료
    - modify: query_validator로 재검증
    - approve (또는 기타): query_executor로 진행
    """
    action = state.get("approval_action")
    if action == "reject":
        return END
    if action == "modify":
        return "query_validator"
    return "query_executor"


def route_after_execution(state: AgentState) -> str:
    """query_executor 이후 라우팅을 결정한다.

    - 정상 실행: result_organizer로 진행
    - 실행 에러 + 재시도 가능: query_generator로 회귀
    - 실행 에러 + 재시도 초과: error_response로 종료
    """
    if state.get("error_message"):
        if state["retry_count"] >= 3:
            return "error_response"
        return "query_generator"
    return "result_organizer"


def route_after_organization(state: AgentState) -> str:
    """result_organizer 이후 라우팅을 결정한다.

    - 데이터 충분: output_generator로 진행
    - 데이터 부족 + 재시도 가능: query_generator로 회귀
    - 데이터 부족 + 재시도 초과: 있는 데이터로 output_generator 진행
    """
    if not state["organized_data"]["is_sufficient"]:
        if state["retry_count"] < 3:
            return "query_generator"
    return "output_generator"


def route_after_semantic_router(state: AgentState) -> str:
    """semantic_router 이후 라우팅을 결정한다.

    - 캐시 관리 의도: cache_management로 진행
    - 유사어 등록 의도: synonym_registrar로 진행
    - 멀티 DB: multi_db_executor로 진행
    - 단일 DB: 기존 파이프라인(schema_analyzer)으로 진행
    """
    intent = state.get("routing_intent")
    if intent == "cache_management":
        return "cache_management"
    if intent == "synonym_registration":
        return "synonym_registrar"
    if state.get("is_multi_db"):
        return "multi_db_executor"
    return "schema_analyzer"


def route_after_schema_analyzer(state: AgentState) -> str:
    """schema_analyzer 이후 라우팅을 결정한다.

    - 구조 분석 HITL 승인 대기: structure_approval_gate로 진행
    - 그 외: query_generator로 진행
    """
    if state.get("awaiting_approval"):
        ctx = state.get("approval_context", {})
        if ctx.get("type") == "structure_analysis":
            return "structure_approval_gate"
    return "query_generator"


def route_after_structure_approval(state: AgentState) -> str:
    """structure_approval_gate 이후 라우팅을 결정한다.

    - approve: schema_analyzer로 재진입 (승인된 결과로 캐시 저장 후 계속)
    - reject: query_generator로 진행 (구조 메타 없이)
    """
    action = state.get("approval_action")
    if action == "approve":
        return "schema_analyzer"
    return "query_generator"


def _error_response_node(state: AgentState) -> dict:
    """최대 재시도 초과 시 에러 응답을 생성한다."""
    error_msg = state.get("error_message") if state.get("error_message") is not None else "알 수 없는 에러가 발생했습니다."
    return {
        "final_response": (
            f"죄송합니다. 요청을 처리하는 중 문제가 발생했습니다.\n"
            f"에러 내용: {error_msg}\n"
            f"재시도 횟수가 최대({state['retry_count']}회)에 도달하여 처리를 중단합니다."
        ),
        "current_node": "error_response",
    }


@contextmanager
def _create_checkpointer(config: AppConfig):
    """체크포인트 저장소를 컨텍스트 매니저로 관리한다."""
    if config.checkpoint_backend == "sqlite":
        import sqlite3

        conn = sqlite3.connect(config.checkpoint_db_url, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            yield SqliteSaver(conn)
        finally:
            conn.close()
    else:
        yield InMemorySaver()


async def _create_checkpointer_async(config: AppConfig):
    """비동기 체크포인트 저장소를 생성한다.

    event loop 내에서 호출해야 한다 (lifespan 등).
    `from_conn_string`은 async context manager이므로 직접 aiosqlite 연결을 생성하여
    연결이 lifespan 동안 유지되도록 한다.
    """
    if config.checkpoint_backend == "sqlite":
        if config.checkpoint_db_url == ":memory:":
            return InMemorySaver()
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            conn = await aiosqlite.connect(config.checkpoint_db_url)
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute("PRAGMA synchronous=NORMAL")
            return AsyncSqliteSaver(conn)
        except Exception as e:
            logger.warning("AsyncSqliteSaver 생성 실패, InMemory 폴백: %s", e)
            return InMemorySaver()
    return InMemorySaver()


def _create_checkpointer_simple(config: AppConfig):
    """동기 체크포인트 저장소를 생성한다 (테스트/CLI용)."""
    if config.checkpoint_backend == "sqlite":
        if config.checkpoint_db_url == ":memory:":
            return InMemorySaver()
        try:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            conn = sqlite3.connect(
                config.checkpoint_db_url, check_same_thread=False
            )
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA synchronous=NORMAL")
            return SqliteSaver(conn)
        except Exception as e:
            logger.warning("SQLite 체크포인터 생성 실패, InMemory 폴백: %s", e)
            return InMemorySaver()
    return InMemorySaver()


def build_graph(config: AppConfig, checkpointer=None):
    """에이전트 그래프를 빌드한다.

    LLM 인스턴스를 한 번 생성하여 partial로 LLM을 사용하는 노드에 주입한다.

    Args:
        config: 애플리케이션 설정
        checkpointer: 외부에서 주입할 체크포인터 (None이면 동기 SqliteSaver 사용)

    Returns:
        컴파일된 LangGraph 그래프
    """
    llm = create_llm(config)

    graph = StateGraph(AgentState)

    # --- 노드 등록 ---

    # Phase 3: context_resolver (첫 노드)
    graph.add_node(
        "context_resolver",
        partial(context_resolver, app_config=config),
    )

    graph.add_node(
        "input_parser",
        partial(input_parser, llm=llm, app_config=config),
    )

    graph.add_node(
        "field_mapper",
        partial(field_mapper, llm=llm, app_config=config),
    )

    # 시멘틱 라우팅 노드 (멀티 DB 지원)
    if config.enable_semantic_routing:
        graph.add_node(
            "semantic_router",
            partial(semantic_router, llm=llm, app_config=config),
        )
        graph.add_node(
            "multi_db_executor",
            partial(multi_db_executor, llm=llm, app_config=config),
        )
        graph.add_node(
            "result_merger",
            partial(result_merger, app_config=config),
        )
        graph.add_node(
            "cache_management",
            partial(cache_management, llm=llm, app_config=config),
        )
        # Phase 3: synonym_registrar
        graph.add_node(
            "synonym_registrar",
            partial(synonym_registrar, app_config=config),
        )

    graph.add_node(
        "schema_analyzer",
        partial(schema_analyzer, llm=llm, app_config=config),
    )
    graph.add_node(
        "query_generator",
        partial(query_generator, llm=llm, app_config=config),
    )
    graph.add_node(
        "query_validator",
        partial(query_validator, app_config=config),
    )

    # Phase 3: approval_gate (SQL 승인 활성화 시)
    if config.enable_sql_approval:
        graph.add_node("approval_gate", approval_gate)

    # 구조 분석 HITL 승인 (활성화 시)
    if config.enable_structure_approval:
        graph.add_node("structure_approval_gate", structure_approval_gate)

    graph.add_node(
        "query_executor",
        partial(query_executor, app_config=config),
    )
    graph.add_node(
        "result_organizer",
        partial(result_organizer, llm=llm, app_config=config),
    )
    graph.add_node(
        "output_generator",
        partial(output_generator, llm=llm, app_config=config),
    )
    graph.add_node("error_response", _error_response_node)

    # --- 엣지 정의 ---

    # Phase 3: START -> context_resolver -> input_parser
    graph.add_edge(START, "context_resolver")
    graph.add_edge("context_resolver", "input_parser")

    # input_parser -> field_mapper
    graph.add_edge("input_parser", "field_mapper")

    if config.enable_semantic_routing:
        # field_mapper -> semantic_router -> 조건부
        graph.add_edge("field_mapper", "semantic_router")

        graph.add_conditional_edges(
            "semantic_router",
            route_after_semantic_router,
            {
                "schema_analyzer": "schema_analyzer",
                "multi_db_executor": "multi_db_executor",
                "cache_management": "cache_management",
                "synonym_registrar": "synonym_registrar",
            },
        )

        # 멀티 DB 경로
        graph.add_edge("multi_db_executor", "result_merger")
        graph.add_edge("result_merger", "result_organizer")

        # 캐시 관리 경로
        graph.add_edge("cache_management", END)

        # 유사어 등록 경로
        graph.add_edge("synonym_registrar", END)
    else:
        # 레거시 모드
        graph.add_edge("field_mapper", "schema_analyzer")

    # 단일 DB 경로: schema_analyzer -> (조건부) -> query_generator
    if config.enable_structure_approval:
        graph.add_conditional_edges(
            "schema_analyzer",
            route_after_schema_analyzer,
            {
                "structure_approval_gate": "structure_approval_gate",
                "query_generator": "query_generator",
            },
        )
        graph.add_conditional_edges(
            "structure_approval_gate",
            route_after_structure_approval,
            {
                "schema_analyzer": "schema_analyzer",
                "query_generator": "query_generator",
            },
        )
    else:
        graph.add_edge("schema_analyzer", "query_generator")
    graph.add_edge("query_generator", "query_validator")

    # query_validator 이후: 조건부 라우팅
    if config.enable_sql_approval:
        # SQL 승인 활성화: validator -> approval_gate -> executor
        graph.add_conditional_edges(
            "query_validator",
            route_after_validation_with_approval,
            {
                "approval_gate": "approval_gate",
                "query_generator": "query_generator",
                "error_response": "error_response",
            },
        )
        graph.add_conditional_edges(
            "approval_gate",
            route_after_approval,
            {
                "query_executor": "query_executor",
                "query_validator": "query_validator",
                END: END,
            },
        )
    else:
        graph.add_conditional_edges(
            "query_validator",
            route_after_validation,
            {
                "query_executor": "query_executor",
                "query_generator": "query_generator",
                "error_response": "error_response",
            },
        )

    # query_executor 이후: 조건부 라우팅
    graph.add_conditional_edges(
        "query_executor",
        route_after_execution,
        {
            "result_organizer": "result_organizer",
            "query_generator": "query_generator",
            "error_response": "error_response",
        },
    )

    # result_organizer 이후: 조건부 라우팅
    graph.add_conditional_edges(
        "result_organizer",
        route_after_organization,
        {
            "output_generator": "output_generator",
            "query_generator": "query_generator",
        },
    )

    # 종단 엣지
    graph.add_edge("output_generator", END)
    graph.add_edge("error_response", END)

    # --- 체크포인트 ---
    if checkpointer is None:
        checkpointer = _create_checkpointer_simple(config)

    # Phase 3: HITL 승인 시 interrupt_before 설정
    interrupt_before = []
    if config.enable_sql_approval:
        interrupt_before.append("approval_gate")
    if config.enable_structure_approval:
        interrupt_before.append("structure_approval_gate")

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before if interrupt_before else None,
    )

    logger.info(
        "에이전트 그래프 빌드 완료 (sql_approval=%s, structure_approval=%s)",
        config.enable_sql_approval,
        config.enable_structure_approval,
    )
    return compiled
