"""interface 계층 — sre_agent 조사 서비스의 FastMCP(SSE) 노출 (Plan 05 §3·§5).

§3 도구 5종을 FastMCP로 노출하고, 정적 Bearer 인증 미들웨어를 제공한다.
도구는 **JobStore 위임만** 수행하며(로직 없음), 실 dispatcher·severity_judge·브리핑
6요소 조립은 **2-D 소관**이다(여기서는 스텁 executor·인터페이스만).

transport는 SSE(collectorinfra 클라이언트 실측 경로와 동일), 포트는 폴스타 MCP(9099)와
분리해 9098을 기본으로 한다.
"""

from __future__ import annotations

import json
import logging

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from sre_agent import __version__
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import CONTRACT_VERSION, JobStore
from sre_agent.diagnosis import DiagnosisAgent, DiagnosisResult
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import remote_vm_profile

logger = logging.getLogger(__name__)

# 폴스타 MCP 서버(9099)와 분리한 조사 서비스 포트 (Plan 05 §5).
DEFAULT_PORT = 9098
DEFAULT_HOST = "127.0.0.1"

# 테스트/run_service가 JobStore에 접근하기 위한 FastMCP 인스턴스 속성 키.
_JOB_STORE_ATTR = "_sre_job_store"


class StaticBearerAuthMiddleware:
    """정적 Bearer 토큰 검증 ASGI 미들웨어 (Plan 05 §5-인증).

    token이 None이면(로컬/개발) 검증을 통과시킨다. 설정되면 모든 HTTP 요청에
    `Authorization: Bearer <token>` 헤더를 요구하고, 불일치 시 401을 반환한다.
    Plan 04 §6-4와 동일한 정적 Bearer 방식(협의 후 mTLS 승격).
    """

    def __init__(self, app, token: str | None) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self.token is None:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = headers.get(b"authorization", b"").decode("latin-1")
        if provided != f"Bearer {self.token}":
            await self._reject(send)
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps({"error": "unauthorized"}, ensure_ascii=False).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _job_to_question(job) -> str:
    """조사 잡을 DiagnosisAgent 질의 문자열로 변환한다(pull은 question, push는 이벤트 요약)."""
    if getattr(job, "kind", None) == "diagnosis" and getattr(job, "question", None):
        return job.question
    event = (getattr(job, "payload", None) or {}).get("event") or {}
    server = event.get("serverName") or event.get("hostname") or "대상 서버"
    # 결론 유도(수렴 가드): 트리아지 순서는 유지하되 핵심 지표 위주로 간결히 조사하고
    # 충분한 근거가 모이면 즉시 결론을 내도록 지시한다(불필요한 반복 조회가 step을 소진해
    # 미완주로 빠지는 것을 방지 — 실측 기반).
    return (
        f"{server} 장애 원인을 조사하고 트리아지하라(부하→병목→원인 격리→로그). "
        f"핵심 지표(CPU·메모리·디스크·네트워크) 위주로 **간결히** 조사하고, 충분한 근거가 "
        f"모이면 **즉시 결론**을 내라(동일 지표 반복 조회 금지). 모든 주장에 도구 출력을 인용하라."
    )


def _build_mcp_servers(settings: AgentSettings) -> dict[str, dict] | None:
    """AgentSettings 폴스타 MCP 접속 설정을 holmes Config.mcp_servers 형식으로 조립한다.

    URL 미설정이면 None(로컬 vm_profile 경로 유지). 토큰(SecretStr) 설정 시 Bearer 헤더
    첨부(D-125). 관측 데이터 접근 경계는 mcp_server 하나로 일원화(D-119) — 폴스타 SQL
    고수준 도구·PromQL 도구가 이 한 엔드포인트에서 자동 발견된다(RemoteMCPToolset).
    """
    url = settings.polestar_mcp_url
    if not url:
        return None
    config: dict = {"mode": "sse", "url": url, "health_check_tool": "list_sources"}
    tok = settings.polestar_mcp_token
    if tok is not None and tok.get_secret_value():
        config["headers"] = {"Authorization": f"Bearer {tok.get_secret_value()}"}
    return {"polestar": {"config": config}}


def _default_diagnose_fn(settings: AgentSettings):
    """실 조사 함수(DiagnosisAgent)를 지연 생성해 반환한다 — dispatcher.diagnose_fn 주입용.

    LLM 키가 있을 때만 dispatcher가 호출하므로 agent는 최초 호출 시점에 1회 생성한다
    (create_service 시점의 holmes prerequisite 검사·비용 회피). **원격 프로파일 배선**:
    `remote_vm_profile()`(로컬 셸 미확장·Prometheus 내장 toolset 비활성) + 폴스타 MCP를
    `mcp_servers`로 등록해 조사가 mcp_server 고수준 도구(폴스타 SQL·PromQL)를 소비한다
    (D-119). mcp_server는 조사 배치에서 execute_sql·raw_promql을 비노출로 두어야
    LLM이 raw SQL/PromQL 방언 오류로 step을 소진하지 않는다(D-122 — 배치 config 규약).
    """
    holder: dict[str, DiagnosisAgent] = {}

    def _diagnose(job) -> DiagnosisResult:
        agent = holder.get("agent")
        if agent is None:
            agent = DiagnosisAgent(
                settings,
                toolsets=remote_vm_profile(),
                mcp_servers=_build_mcp_servers(settings),
            )
            holder["agent"] = agent
        return agent.ask(_job_to_question(job))

    return _diagnose


def _build_dispatcher(settings: AgentSettings) -> InvestigationDispatcher:
    """실 dispatcher를 조립한다(diagnose_fn·briefing_fn 주입). JobStore executor로 배선된다."""
    return InvestigationDispatcher(
        settings,
        diagnose_fn=_default_diagnose_fn(settings),
        briefing_fn=build_briefing,
    )


def create_service(
    settings: AgentSettings | None = None,
    job_store: JobStore | None = None,
) -> FastMCP:
    """§3 도구 5종을 등록한 FastMCP 인스턴스를 생성한다.

    도구는 JobStore 위임만 수행한다(로직 없음). JobStore는 `_sre_job_store` 속성으로
    접근 가능하다(run_service의 재기동 복구·테스트 검증용). job_store 미지정 시 실
    dispatcher(가드 5종·severity_judge·briefing 후처리)를 executor로 배선한다.
    """
    settings = settings or AgentSettings()
    if job_store is not None:
        store = job_store
    else:
        store = JobStore(settings, executor=_build_dispatcher(settings))

    mcp = FastMCP("sre-agent", host=DEFAULT_HOST, port=DEFAULT_PORT)

    @mcp.tool()
    async def sre_investigate_alarm(payload: dict, wait_seconds: int = 0) -> str:
        """알람 트리거 페이로드(contract_version "1")로 조사 잡을 제출한다(§3·§4).

        반환(JSON): {investigation_id, status: accepted|duplicate|rejected, reason?}.
        wait_seconds>0이면 현재 잡 상태를 함께 반환한다(스텁 경로는 즉시 확정 — 실
        타임아웃 대기는 2-D 소관).
        """
        result = store.submit(payload)
        if wait_seconds > 0 and result.get("status") == "accepted":
            result = store.get(result["investigation_id"])
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    async def sre_get_investigation(investigation_id: str) -> str:
        """조사 잡 상태를 조회한다(§3).

        반환(JSON): {status: running|done|failed|timeout|stub|..., briefing?, verdict?,
        tool_calls_summary?, tokens?, cost?, error?}.
        """
        return json.dumps(store.get(investigation_id), ensure_ascii=False)

    @mcp.tool()
    async def sre_diagnose(
        question: str,
        server_name: str | None = None,
        hostname: str | None = None,
        db_id: str | None = None,
        target_state: dict | None = None,
    ) -> str:
        """pull형 자연어 진단 잡을 제출한다(§3 — 챗 의도 위임용). 동일 잡 패턴.

        `target_state`(Plan 81)는 호출자가 판정한 대상 가용성이다. **선택 인자**이므로
        넘기지 않는 구버전 호출자는 종전과 동일하게 동작한다(fail-open). 값이 있고
        `state == "unavailable"`이면 dispatcher가 조사 전에 거부한다.
        """
        return json.dumps(
            store.submit_diagnosis(question, server_name, hostname, db_id, target_state),
            ensure_ascii=False,
        )

    @mcp.tool()
    async def sre_list_investigations(limit: int = 20) -> str:
        """최근 조사 잡 요약을 반환한다(§3)."""
        return json.dumps(store.list(limit), ensure_ascii=False)

    @mcp.tool()
    async def sre_health() -> str:
        """서비스 상태를 반환한다(§3 — collectorinfra health_check_tool 지정 대상).

        반환(JSON): {status, version, contract_version, holmes_ready,
        polestar_mcp_reachable}. holmes_ready는 조사 실행 가능성(LLM 키 존재)을
        정직하게 반영한다. polestar_mcp_reachable 라이브 프로브는 2-D 소관 → 미확인(None).
        """
        return json.dumps(
            {
                "status": "ok",
                "version": __version__,
                "contract_version": CONTRACT_VERSION,
                "holmes_ready": settings.gemini_api_key is not None,
                "polestar_mcp_reachable": None,
            },
            ensure_ascii=False,
        )

    setattr(mcp, _JOB_STORE_ATTR, store)
    logger.info("sre_agent 조사 서비스 생성: 도구 5종 등록(port=%d)", DEFAULT_PORT)
    return mcp


def get_job_store(mcp: FastMCP) -> JobStore:
    """create_service가 부착한 JobStore를 반환한다(run_service·테스트용)."""
    return getattr(mcp, _JOB_STORE_ATTR)


def build_asgi_app(mcp: FastMCP, token: str | None) -> Starlette:
    """FastMCP SSE 앱에 정적 Bearer 인증 미들웨어를 씌워 반환한다.

    `mcp.run(transport="sse")`는 내부에서 자체 앱을 만들어 미들웨어 주입점이 없으므로,
    run_service는 이 함수로 앱을 조립해 uvicorn에 직접 넘긴다.
    """
    app = mcp.sse_app()
    app.add_middleware(StaticBearerAuthMiddleware, token=token)
    return app


__all__ = [
    "DEFAULT_PORT",
    "DEFAULT_HOST",
    "StaticBearerAuthMiddleware",
    "create_service",
    "get_job_store",
    "build_asgi_app",
]
