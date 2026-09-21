"""LangGraph 그래프 빌드 모듈.

에이전트의 노드, 엣지, 조건부 라우팅을 정의하고
컴파일된 그래프를 반환한다.
LLM 인스턴스를 한 번 생성하여 partial로 노드에 주입한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import contextmanager
from functools import partial

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.config import AppConfig
from src.llm import create_llm
from src.nodes.approval_gate import approval_gate
from src.nodes.cache_management import cache_management
from src.nodes.context_resolver import context_resolver
from src.nodes.fault_diagnosis import fault_diagnosis as fault_diagnosis_node
from src.nodes.general_inference import general_inference as general_inference_node
from src.nodes.field_mapper import field_mapper
from src.nodes.input_parser import input_parser
from src.nodes.multi_db_executor import multi_db_executor
from src.nodes.output_generator import append_structure_missing_note, output_generator
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import query_validator
from src.nodes.result_merger import result_merger
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import schema_analyzer
from src.nodes.synonym_registrar import synonym_registrar
from src.observability.graph_proxy import TracedGraph
from src.observability.investigation_metrics import log_investigation_startup
from src.observability.ladder import record_ladder_resolution, resolve_ladder_tier
from src.orchestration import (
    agent_orchestrator,
    intent_planner,
    replanner,
    result_aggregator,
    run_deep_agent,
    select_orchestration_backend,
)
from src.orchestration.entity_locator import entity_locator, probe_halted
from src.orchestration.sequential_runner import sequential_entry, sequential_runner
from src.routing.db_authz import ACCESS_DENIED_INTENT, authorized_router
from src.routing.semantic_router import semantic_router
from src.state import AgentState

logger = logging.getLogger(__name__)


#: 생성기가 SQL 대신 산문(되물음·불가 사유)을 반환한 경우의 재시도 예산.
#: 전체 예산(`QUERY_MAX_RETRY_COUNT`)과 별도로 둔다 — 산문은 프롬프트가 지시한 동작이라
#: (polestar 템플릿 [Strict Constraints] 1: *"모호하거나 스키마 범위를 벗어나면 쿼리를
#: 생성하지 말고 추가 맥락을 요청하라"*) 같은 프롬프트를 다시 돌려도 대개 같은 산문이
#: 돌아온다. run `20260918-182507` 실측(체인 39건): 회복은 retry=1 **7건**인데 retry=2·3은
#: 합해 3건이고, 회복하지 못한 29건이 각 3회를 더 태워 턴을 60초 벽으로 밀어냈다.
NON_SQL_RETRY_BUDGET = 1


def _non_sql_exhausted(state: AgentState) -> bool:
    """산문 응답이고 그 전용 예산을 소진했는가."""
    return bool(
        (state.get("validation_result") or {}).get("non_sql")
    ) and state["retry_count"] >= NON_SQL_RETRY_BUDGET


def route_after_validation(state: AgentState, max_retry: int = 3) -> str:
    """query_validator 이후 라우팅을 결정한다.

    - 검증 통과: query_executor (또는 approval_gate)로 진행
    - 산문(비-SQL) 응답 + 전용 예산 소진: error_response로 조기 종료
    - 검증 실패 + 재시도 가능: query_generator로 회귀
    - 검증 실패 + 재시도 초과: error_response로 종료
    """
    if state["validation_result"]["passed"]:
        return "query_executor"
    if _non_sql_exhausted(state):
        return "error_response"
    if state["retry_count"] >= max_retry:
        return "error_response"
    return "query_generator"


def route_after_validation_with_approval(state: AgentState, max_retry: int = 3) -> str:
    """query_validator 이후 라우팅 (SQL 승인 활성화 시).

    검증 통과 시 approval_gate로 보낸다. 산문 예산은 기본 경로와 대칭이다.
    """
    if state["validation_result"]["passed"]:
        return "approval_gate"
    if _non_sql_exhausted(state):
        return "error_response"
    if state["retry_count"] >= max_retry:
        return "error_response"
    return "query_generator"


def route_after_approval(state: AgentState) -> str:
    """approval_gate 이후 라우팅을 결정한다.

    - approve: query_executor로 진행
    - modify: query_validator로 재검증
    - 그 외(reject·None·미지 값): 종료 — 명시 승인 없이는 SQL을 실행하지 않는다(fail-closed, D-130)
    """
    action = state.get("approval_action")
    if action == "approve":
        return "query_executor"
    if action == "modify":
        return "query_validator"
    return END


def route_after_execution(state: AgentState, max_retry: int = 3) -> str:
    """query_executor 이후 라우팅을 결정한다.

    - 정상 실행: result_organizer로 진행
    - 실행 에러 + 재시도 가능: query_generator로 회귀
    - 실행 에러 + 재시도 초과: error_response로 종료
    """
    if state.get("error_message"):
        if state["retry_count"] >= max_retry:
            return "error_response"
        return "query_generator"
    return "result_organizer"


def route_after_organization(state: AgentState, max_retry: int = 3) -> str:
    """result_organizer 이후 라우팅을 결정한다.

    - 데이터 충분: output_generator로 진행
    - 데이터 부족 + 재시도 가능: query_generator로 회귀
    - 데이터 부족 + 재시도 초과: 있는 데이터로 output_generator 진행
    """
    if not state["organized_data"]["is_sufficient"]:
        if state["retry_count"] < max_retry:
            return "query_generator"
    return "output_generator"


_INTENT_ROUTE_MAP: dict[str, str] = {
    "cache_management": "cache_management",
    "synonym_registration": "synonym_registrar",
    "general_inference": "general_inference",
    # (Plan 64 CW-B · D-004 대칭) 장애 진단 pull 위임. fault_diagnosis_enabled off면
    # 라우터가 이 의도를 산출하지 않아(프롬프트 미노출+강등) 이 항목은 도달 불가(비트동일).
    "fault_diagnosis": "fault_diagnosis",
}


def route_after_semantic_router(state: AgentState) -> str:
    """semantic_router 이후 라우팅을 결정한다.

    - 캐시 관리 의도: cache_management로 진행
    - 유사어 등록 의도: synonym_registrar로 진행
    - 일반 추론 의도: general_inference로 진행
    - 멀티 DB: multi_db_executor로 진행
    - 단일 DB: 기존 파이프라인(schema_analyzer)으로 진행
    """
    intent = state.get("routing_intent")
    if intent in _INTENT_ROUTE_MAP:
        return _INTENT_ROUTE_MAP[intent]
    # 존 역질문 후단 게이트(D-143 후속2): 역질문은 이번 턴의 최종 응답 — 즉시 종료.
    # 라우트가 zone_clarification 페이로드를 status="clarification"으로 변환한다.
    if intent == "zone_clarification":
        return END
    # 조회 가능 DB가 없는 사용자(plans/104 C-4 · D-232) — 사유를 final_response로 이미 실었다.
    if intent == ACCESS_DENIED_INTENT:
        return END
    if state.get("is_multi_db"):
        return "multi_db_executor"
    return "schema_analyzer"


def route_after_semantic_router_sequential(state: AgentState, *, config: AppConfig) -> str:
    """3단 + `sequential_runner` 등록 시의 라우팅 (D-203 · plans/88 §4.7).

    진입 조건(플래그·표지·데이터 의도·HITL off·폼필 아님)이 전부 성립할 때만 `sequential_runner`,
    아니면 현행 `route_after_semantic_router` 그대로. 캐시·유사어·일반추론·존 역질문 의도는
    `sequential_entry`가 데이터 의도가 아니라고 판정해 가로채지 않는다.
    """
    if sequential_entry(state, config):
        return "sequential_runner"
    return route_after_semantic_router(state)


def route_after_entity_locator(state: AgentState, *, delegate: Callable[[AgentState], str]) -> str:
    """3단 + `entity_locator` 등록 시의 라우팅 (plans/102 §3.4 · D-224 ⑤).

    소재 프로브가 "조회하지 않고 사유 노출"로 끝냈으면 END, 아니면 `semantic_router` 뒤에 있던
    분기 함수(`delegate`)를 그대로 부른다 — 프로브는 대상 DB를 좁힐 뿐 분기 규칙을 바꾸지 않는다.
    """
    if probe_halted(state):
        return END
    return delegate(state)


def route_after_field_mapper_legacy(state: AgentState, *, config: AppConfig) -> str:
    """4단(legacy) + `sequential_runner` 등록 시: field_mapper → sequential_runner | schema_analyzer."""
    if sequential_entry(state, config):
        return "sequential_runner"
    return "schema_analyzer"


def route_after_orchestrator(state: AgentState) -> str:
    """agent_orchestrator 이후 항상 replanner로 보내 종료/추가를 평가한다.

    루프 제어를 replanner 단일 지점에 집중시켜 분기 복잡도를 낮춘다(Plan 49 §3.3).
    재진입 여부는 replanner가 needs_replan으로 결정한다.
    """
    return "replanner"


def route_after_replanner(state: AgentState) -> str:
    """replanner 이후 라우팅을 결정한다.

    - 재계획 필요(needs_replan=True): agent_orchestrator로 재진입(신규 task 실행)
    - 종료(needs_replan=False): result_aggregator로 진행
    """
    if state.get("needs_replan"):
        return "agent_orchestrator"
    return "result_aggregator"


def _error_response_node(state: AgentState) -> dict:
    """최대 재시도 초과 시 에러 응답을 생성한다."""
    if (state.get("validation_result") or {}).get("non_sql"):
        # 생성기가 남긴 되물음·불가 사유를 그대로 싣는다 — 그 텍스트가 사용자가 받아야 할
        # 답이고, 종전에는 "재시도 3회 초과"로 덮여 통째로 버려졌다(침묵적 폐기 금지).
        prose = (state.get("generated_sql") or "").strip()[:1500]
        response = (
            "요청을 SQL로 옮기지 못했습니다. 조회 엔진이 대신 남긴 설명입니다.\n\n"
            f"{prose}\n\n"
            "조회 대상(서버·지표·기간)을 구체적으로 지정해 주시면 다시 시도하겠습니다."
        )
        response = append_structure_missing_note(response, state)
        return {
            "final_response": response,
            "current_node": "error_response",
            "messages": [AIMessage(content=response)],
        }
    error_msg = state.get("error_message") if state.get("error_message") is not None else "알 수 없는 에러가 발생했습니다."
    response = (
        f"죄송합니다. 요청을 처리하는 중 문제가 발생했습니다.\n"
        f"에러 내용: {error_msg}\n"
        f"재시도 횟수가 최대({state['retry_count']}회)에 도달하여 처리를 중단합니다."
    )
    # 구조 정보 없이 조회하다 실패했다면 그 사유가 가장 필요한 자리다 — 정상 응답과 같은 노트
    # (plans/104).
    response = append_structure_missing_note(response, state)
    # 답변을 대화 이력에 누적한다(②, 멀티턴 후속 턴이 직전 상황을 인지하도록).
    return {
        "final_response": response,
        "current_node": "error_response",
        "messages": [AIMessage(content=response)],
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


def _deep_agent_buildable(config: AppConfig, worker_llm) -> bool:
    """deepagents 에이전트를 실제로 조립 가능한지 빌드 시점에 확인한다.

    `build_deep_agent`는 deepagents 패키지 미설치(폐쇄망 wheel 미반입) 시 RuntimeError를
    던진다. 가용성 판정(select_orchestration_backend)이 통과해도 패키지가 없으면 그래프 빌드가
    크래시하므로, 여기서 조립을 시도해보고 실패 시 False를 반환하여 semantic_router로 폴백한다.

    Args:
        config: 앱 설정
        worker_llm: FabriX 워커 LLM (조립 시도에 재사용)

    Returns:
        조립 가능하면 True, RuntimeError(패키지/조립 실패) 시 False
    """
    from src.orchestration import build_deep_agent

    try:
        build_deep_agent(config, worker_llm=worker_llm)
        return True
    except RuntimeError as e:
        logger.info("deepagents 조립 불가(빌드 시점 점검): %s", e)
        return False
    except Exception as e:  # noqa: BLE001 — 조립 단계의 어떤 실패도 폴백 사유로 처리
        logger.warning("deepagents 조립 중 예기치 못한 오류 → 폴백: %s", e)
        return False


def build_graph(config: AppConfig, checkpointer=None):
    """에이전트 그래프를 빌드한다.

    LLM 인스턴스를 한 번 생성하여 partial로 LLM을 사용하는 노드에 주입한다.

    Args:
        config: 애플리케이션 설정
        checkpointer: 외부에서 주입할 체크포인터 (None이면 동기 SqliteSaver 사용)

    Returns:
        컴파일된 LangGraph 그래프
    """
    # 워커(데이터 평면) LLM. worker_provider_override가 설정되면(테스트 전용) 운영
    # config.llm.provider(보통 fabrix) 대신 해당 provider로 강제 생성한다 — deepagent 경로
    # 전체(input_parser/field_mapper + deep_agent 워커)를 gemini로 검증 (D-037 / Plan 49 §4.7).
    llm = create_llm(config, provider_override=config.worker_provider_override)
    if config.worker_provider_override:
        logger.info(
            "워커 provider override 활성(테스트): %s (운영 기본=%s)",
            config.worker_provider_override, config.llm.provider,
        )

    # Plan 49 / D-037 트랙 B: deepagents 실제 패키지(vLLM 오케스트레이터 + FabriX 워커) 백엔드 선택.
    # enable_deepagents_package=on + 오케스트레이터 가용 시 "deep_agent", 그 외 "semantic_router"(§4.6).
    # 실행 경로 4종은 대등한 병존이 아니라 위에서부터 성립하는 한 단만 확정되는
    # 사다리다 — 기준 경로는 3단 semantic_router, 1단 deep_agent는 부가 경로(opt-in)다
    # (D-225). 구조·활성 조건·확정 사유·모듈 의존 방향은
    # docs/21_orchestration_ladder.md가 단일 출처다.
    # 특히 §7: 배선은 배타적이지만 모듈 의존은 아니다(1단이 2·3단 모듈을 재사용).
    # 빌드 시 1회 가용성 판정으로 백엔드를 확정한다(결정적). 가용 판정이어도 deepagents 패키지
    # 미설치(폐쇄망 wheel 미반입)면 RuntimeError가 발생하므로, 빌드 시점에 조립을 시도해보고
    # 실패하면 기존 semantic_router 경로로 안전 폴백한다(그래프 크래시 방지 — 회귀 없음).
    _backend = select_orchestration_backend(config)
    _buildable = _deep_agent_buildable(config, llm) if _backend == "deep_agent" else False
    use_deep_agent = _backend == "deep_agent" and _buildable
    if _backend == "deep_agent" and not _buildable:
        logger.warning(
            "Track B 선택(deep_agent)이나 deepagents 패키지 조립 불가 → "
            "semantic_router 경로로 폴백합니다(폐쇄망 wheel 반입 필요). "
            "degraded_reason=package_missing"
        )

    # (Plan 64 CW-B) 장애 진단 pull 위임 옵트인. 시멘틱 라우팅 경로에서만·플래그 on일 때만
    # fault_diagnosis 노드를 배선한다. off면 노드·엣지 미배선 → 라우팅 비트동일(회귀 0).
    fault_dx_enabled = bool(
        getattr(getattr(config, "noise_gate", None), "fault_diagnosis_enabled", False)
    )

    # 노드 트레이싱 프록시 (D-141). `add_node`만 가로채므로 조건부·신규 노드가 자동 편입되고,
    # 노드 파일은 수정하지 않는다. off면 원본 함수를 그대로 등록해 비트동일하게 동작한다.
    graph = TracedGraph(
        StateGraph(AgentState),
        enabled=bool(getattr(getattr(config, "observability", None), "trace_enabled", False)),
    )

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

    # Plan 49 트랙 B: deepagents 실제 패키지 노드 (vLLM 오케스트레이터 + FabriX 워커).
    # 가용 시 다른 모든 경로보다 우선하며, field_mapper -> deep_agent -> END로 배선한다.
    if use_deep_agent:
        graph.add_node(
            "deep_agent",
            partial(run_deep_agent, app_config=config, worker_llm=llm),
        )

    # Plan 48: deepagents 의도 분해 오케스트레이션 노드 (semantic_routing보다 우선, 상호 배타)
    # 트랙 B(deep_agent) 활성 시에는 트랙 A 노드를 등록하지 않는다(상호 배타, 죽은 노드 방지).
    if config.enable_intent_orchestration and not use_deep_agent:
        graph.add_node(
            "intent_planner",
            partial(intent_planner, llm=llm, app_config=config),
        )
        graph.add_node(
            "agent_orchestrator",
            partial(agent_orchestrator, llm=llm, app_config=config),
        )
        # synthesize=True: 다중 의도 분해/재계획으로 task가 여러 개면 deterministic
        # 이어붙이기(_merge_finalized)는 "없음→있음" 모순·부분 결과를 한 말풍선에 그대로
        # 나열한다. LLM 1회로 단일 일관 답변을 합성해 모순/중복을 해소한다(D-062).
        graph.add_node(
            "result_aggregator",
            partial(result_aggregator, llm=llm, app_config=config, synthesize=True),
        )
        # Plan 49: 결과 기반 동적 재계획 노드 (orchestrator↔replanner 루프)
        graph.add_node(
            "replanner",
            partial(replanner, llm=llm, app_config=config),
        )

    # 시멘틱 라우팅 노드 (멀티 DB 지원)
    # 트랙 B(deep_agent) 활성 시에는 등록하지 않는다(상호 배타, 죽은 노드 방지).
    if config.enable_semantic_routing and not use_deep_agent:
        # 사용자별 DB 인가(plans/104 C-4 · D-232)는 라우터 노드 경계에서 한 번만 건다 —
        # 라우터 본체는 반환 지점이 여러 곳이라 안에서 거르면 빠뜨린다.
        graph.add_node(
            "semantic_router",
            partial(
                authorized_router,
                inner=partial(semantic_router, llm=llm, app_config=config),
            ),
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
        # 일반 추론 노드 (DB 접근 없이 LLM 직접 응답)
        graph.add_node(
            "general_inference",
            partial(general_inference_node, llm=llm, app_config=config),
        )
        # (Plan 64 CW-B · D-004 대칭 2/3) 장애 진단 pull 위임 노드 (옵트인 on일 때만).
        if fault_dx_enabled:
            graph.add_node(
                "fault_diagnosis",
                partial(fault_diagnosis_node, app_config=config),
            )

    # D-203 (plans/88 §4.7): 3단·4단 빌드 전용 순차 2-pass 노드 — 1·2단 빌드에는 등록하지 않는다
    # (1단은 도구 루프, 2단은 task DAG가 이미 순차를 갖는다). 플래그 off면 등록도 하지 않는다.
    sequential_tier = (
        bool(getattr(getattr(config, "composite", None), "sequential_fallback_tiers_enabled", False))
        and not use_deep_agent
        and not config.enable_intent_orchestration
    )
    if sequential_tier:
        graph.add_node(
            "sequential_runner",
            partial(sequential_runner, llm=llm, app_config=config),
        )

    # plans/102 §3.4 (D-224 ⑤): 3단 전용 식별자 소재 프로브 — 플래그 on일 때만 등록한다(LLM 미주입).
    # off거나 1·2·4단이면 노드·엣지·분기 함수가 현행과 같다.
    probe_tier = (
        bool(getattr(config, "cross_system_probe_enabled", False))
        and config.enable_semantic_routing
        and not use_deep_agent
        and not config.enable_intent_orchestration
    )
    if probe_tier:
        graph.add_node(
            "entity_locator",
            partial(entity_locator, app_config=config),
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

    if use_deep_agent:
        # Plan 49 트랙 B: field_mapper -> deep_agent -> END (모든 경로 중 최우선)
        # deepagents가 도구(=FabriX 파이프라인) 호출·동적 재계획·최종 응답 생성을 담당한다.
        graph.add_edge("field_mapper", "deep_agent")
        graph.add_edge("deep_agent", END)
    elif config.enable_intent_orchestration:
        # Plan 48/49: 의도 분해 오케스트레이션 경로 (semantic_routing보다 우선)
        # field_mapper -> intent_planner -> agent_orchestrator -> [replanner 루프] -> result_aggregator -> END
        graph.add_edge("field_mapper", "intent_planner")
        graph.add_edge("intent_planner", "agent_orchestrator")
        graph.add_conditional_edges(
            "agent_orchestrator",
            route_after_orchestrator,
            {"replanner": "replanner"},
        )
        graph.add_conditional_edges(
            "replanner",
            route_after_replanner,
            {
                "agent_orchestrator": "agent_orchestrator",
                "result_aggregator": "result_aggregator",
            },
        )
        graph.add_edge("result_aggregator", END)
    elif config.enable_semantic_routing:
        # field_mapper -> semantic_router -> 조건부
        graph.add_edge("field_mapper", "semantic_router")

        # (Plan 64 CW-B · D-004 대칭 3/3) fault_diagnosis 노드 배선 시에만 라우팅 대상에 추가.
        # off면 대상 dict에 미포함 → route_after_semantic_router가 이 값을 반환하지 않는다
        # (라우터가 fault_diagnosis 의도를 산출하지 않으므로 도달 불가). 비트동일(회귀 0).
        _router_targets = {
            "schema_analyzer": "schema_analyzer",
            "multi_db_executor": "multi_db_executor",
            "cache_management": "cache_management",
            "synonym_registrar": "synonym_registrar",
            "general_inference": "general_inference",
            # 존 역질문 후단 게이트(D-143 후속2) — 역질문은 턴 종결
            END: END,
        }
        if fault_dx_enabled:
            _router_targets["fault_diagnosis"] = "fault_diagnosis"
        _router_route: Callable[[AgentState], str]
        if sequential_tier:
            # D-203: 진입 조건이 성립할 때만 순차 러너로 — 불성립이면 현행 분기 함수 그대로 위임.
            _router_targets["sequential_runner"] = "sequential_runner"
            _router_route = partial(route_after_semantic_router_sequential, config=config)
            graph.add_edge("sequential_runner", END)
        else:
            _router_route = route_after_semantic_router
        if probe_tier:
            # plans/102 §3.4: semantic_router → entity_locator → (위 분기 함수 그대로).
            # 사유 노출 행만 END(END는 이미 대상에 있다 — 존 역질문).
            graph.add_edge("semantic_router", "entity_locator")
            graph.add_conditional_edges(
                "entity_locator",
                partial(route_after_entity_locator, delegate=_router_route),
                _router_targets,
            )
        else:
            graph.add_conditional_edges(
                "semantic_router",
                _router_route,
                _router_targets,
            )

        # 멀티 DB 경로
        graph.add_edge("multi_db_executor", "result_merger")
        graph.add_edge("result_merger", "result_organizer")

        # 캐시 관리 경로
        graph.add_edge("cache_management", END)

        # 유사어 등록 경로
        graph.add_edge("synonym_registrar", END)

        # 일반 추론 경로
        graph.add_edge("general_inference", END)

        # (Plan 64 CW-B) 장애 진단 경로 (옵트인 on일 때만 노드가 존재)
        if fault_dx_enabled:
            graph.add_edge("fault_diagnosis", END)
    elif sequential_tier:
        # 레거시 모드 + 순차 러너(D-203): 진입 조건 성립 시에만 분기, 아니면 현행 직행.
        graph.add_conditional_edges(
            "field_mapper",
            partial(route_after_field_mapper_legacy, config=config),
            {"sequential_runner": "sequential_runner", "schema_analyzer": "schema_analyzer"},
        )
        graph.add_edge("sequential_runner", END)
    else:
        # 레거시 모드
        graph.add_edge("field_mapper", "schema_analyzer")

    # 단일 DB 경로: schema_analyzer -> query_generator 직행.
    # 구조 승인 HITL 게이트 노드는 plans/104(D-227)에서 삭제됐다 — 질의 경로는
    # 구조를 분석하지 않고 수동 프로필·관리자 승인본을 읽기만 한다(없으면 사유 노트).
    graph.add_edge("schema_analyzer", "query_generator")
    graph.add_edge("query_generator", "query_validator")

    # query_validator 이후: 조건부 라우팅
    if config.enable_sql_approval:
        # SQL 승인 활성화: validator -> approval_gate -> executor
        graph.add_conditional_edges(
            "query_validator",
            partial(route_after_validation_with_approval, max_retry=config.query.max_retry_count),
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
            partial(route_after_validation, max_retry=config.query.max_retry_count),
            {
                "query_executor": "query_executor",
                "query_generator": "query_generator",
                "error_response": "error_response",
            },
        )

    # query_executor 이후: 조건부 라우팅
    graph.add_conditional_edges(
        "query_executor",
        partial(route_after_execution, max_retry=config.query.max_retry_count),
        {
            "result_organizer": "result_organizer",
            "query_generator": "query_generator",
            "error_response": "error_response",
        },
    )

    # result_organizer 이후: 조건부 라우팅
    graph.add_conditional_edges(
        "result_organizer",
        partial(route_after_organization, max_retry=config.query.max_retry_count),
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

    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before if interrupt_before else None,
    )

    logger.info(
        "에이전트 그래프 빌드 완료 (orchestration=%s, semantic_routing=%s, sql_approval=%s)",
        config.enable_intent_orchestration,
        config.enable_semantic_routing,
        config.enable_sql_approval,
    )

    # 확정된 사다리 단과 그 사유를 기록한다(D-161 / plans/70 P0-1 · D-225 기준 개정).
    # 경로 4종은 병존이 아니라 한 단만 확정되는 사다리(기준 3단 · 1단 부가)이며,
    # 확정은 여기서 1회 일어난다.
    # 이 로그가 "레거시 4단이 실제로 쓰이는가"를 판정하는 유일한 근거다.
    _tier, _reason = resolve_ladder_tier(config, backend=_backend, buildable=_buildable)
    record_ladder_resolution(
        _tier, _reason,
        flag_origin=getattr(config, "_orchestration_resolved_by", "explicit_env"),
    )

    # 호스트 조사 경로의 확정값도 같은 자리에서 1회 남긴다 (Plan 78 W6-3 · P14).
    # 사다리 로그와 나란히 두는 이유: 둘 다 "무엇이 켜진 채 돌고 있는가"의 재구성 재료이고,
    # 호출 지점이 흩어지면 한쪽만 남는 상태가 생긴다(ladder record/log를 묶은 것과 같은 이유).
    log_investigation_startup(config)

    return compiled
