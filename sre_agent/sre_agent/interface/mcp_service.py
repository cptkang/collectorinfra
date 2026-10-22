"""interface 계층 — sre_agent 조사 서비스의 FastMCP(SSE) 노출 (Plan 05 §3·§5).

§3 도구 5종을 FastMCP로 노출하고, 정적 Bearer 인증 미들웨어를 제공한다.
도구는 **JobStore 위임만** 수행하며(로직 없음), 실 dispatcher·severity_judge·브리핑
6요소 조립은 **2-D 소관**이다(여기서는 스텁 executor·인터페이스만).

transport는 SSE(collectorinfra 클라이언트 실측 경로와 동일), 포트는 폴스타 MCP(9099)와
분리해 9098을 기본으로 한다.
"""

from __future__ import annotations

import hmac
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from sre_agent import __version__
from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.evidence_prefetch import prefetch_and_correlate, scope_from_job
from sre_agent.application.investigation_dispatcher import (
    InvestigationDispatcher,
    PlaybookWiring,
)
from sre_agent.application.investigation_guidance import build_guidance
from sre_agent.application.investigation_jobs import (
    CONTRACT_VERSION,
    JobStore,
    default_audit_path,
)
from sre_agent.diagnosis import (
    DiagnosisAgent,
    DiagnosisResult,
    clean_toolset_error,
    tools_unavailable_result,
)
from sre_agent.domain.answer_markup import has_tool_call_markup
from sre_agent.domain.cross_source_episode import (
    EpisodeContext,
    EpisodeMember,
    active_episode,
    prompt_text,
)
from sre_agent.infrastructure.known_error_loader import DEFAULT_KNOWN_ERROR_DIR, load_known_errors
from sre_agent.infrastructure.mcp_tool_client import make_apm_batch_caller, make_batch_caller
from sre_agent.infrastructure.playbook_loader import DEFAULT_PLAYBOOK_DIR, load_playbooks
from sre_agent.interface.config_admin import AdminPathRouter, build_admin_app
from sre_agent.interface.config_admin_adapter import build_adapter
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import remote_vm_profile

logger = logging.getLogger(__name__)

# 폴스타 MCP 서버(9099)와 분리한 조사 서비스 포트 (Plan 05 §5).
DEFAULT_PORT = 9098
DEFAULT_HOST = "127.0.0.1"

# 테스트/run_service가 JobStore에 접근하기 위한 FastMCP 인스턴스 속성 키.
_JOB_STORE_ATTR = "_sre_job_store"

# `_build_mcp_servers`가 폴스타 MCP를 등록하는 서버 이름
# — 이 툴셋이 FAILED면 조사를 생략한다(plans/188 C2).
POLESTAR_SERVER = "polestar"


@dataclass
class ToolsetCheck:
    """마지막 조사 직전 MCP 툴셋 검사 결과(plans/188 C6).

    `sre_health`가 읽는다(헬스 호출마다 프로브하지 않음). `polestar_reachable`: None = 아직 검사 전
    (또는 폴스타 URL 미설정) · True = 툴셋 정상 · False = FAILED(`polestar_error`에 holmes 사유).
    `last_answer_malformed`(C7): LLM 답을 받은 마지막 조사의 답에 도구 호출 마크업이 섞였는지
    — None = 아직 없음. 타임아웃으로 종결된 잡의 늦게 끝난 답도 반영될 수 있다.
    """

    polestar_reachable: bool | None = None
    polestar_error: str | None = None
    last_answer_malformed: bool | None = None

    def record(self, unavailable: dict[str, str]) -> None:
        """검사 결과를 기록한다."""
        self.polestar_reachable = POLESTAR_SERVER not in unavailable
        error = unavailable.get(POLESTAR_SERVER)
        self.polestar_error = None if error is None else clean_toolset_error(error)


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


#: 크로스소스 질문(plans/153 §4.6) — 규칙은 「가설」로만 제시한다
#: (결합 근거는 사건 추적기 판정 · 인과 아님).
CROSS_SOURCE_QUESTION_TEMPLATE = (
    "{host}에서 인프라(폴스타) 알람 {causes}와 애플리케이션(제니퍼) 이벤트 {effects}이 "
    "한 사건으로 묶였다{at}. "
    "두 소스의 도구로 선후와 연결 근거를 확인하고, 규칙 가설이 맞는지 반증까지 검토하라. "
    "모든 주장에 도구 출력을 인용하라."
)
_QUESTION_NAMES_MAX = 3


def _names(members: list[EpisodeMember]) -> str:
    """멤버 표시 이름(중복 제거 · 최대 3개) — 「A」·「B」 형식."""
    names = list(dict.fromkeys(prompt_text(m.label) for m in members))[:_QUESTION_NAMES_MAX]
    return "·".join(f"「{n}」" for n in names) or "「?」"


def _cross_source_question(server: str, when: str | None, xs: EpisodeContext) -> str:
    """크로스소스 질문 문자열.

    원인 후보 = 성립 링크의 원인 멤버(없으면 인프라 멤버) ·
    증상 = 링크의 증상 멤버(없으면 애플리케이션 멤버).
    """
    links = xs.valid_links
    causes = [m for m in (xs.member(lk.cause_alarm_id) for lk in links) if m is not None]
    effects = [m for m in (xs.member(lk.effect_alarm_id) for lk in links) if m is not None]
    at = f"(발생 시각 {when} — 이 시각 이전 구간을 조사)" if when else ""
    return CROSS_SOURCE_QUESTION_TEMPLATE.format(
        host=server,
        causes=_names(causes or list(xs.infra_members)),
        effects=_names(effects or list(xs.apm_members)),
        at=at,
    )


def _job_to_question(job, settings: AgentSettings | None = None) -> str:
    """조사 잡을 DiagnosisAgent 질의 문자열로 변환한다(pull은 question, push는 이벤트 요약).

    `settings.cross_source_investigation_enabled`(plans/153 §4.6)이고 `meta.episode`에
    두 소스 멤버가 모두 있으면 크로스소스 질문을 만든다. settings 미지정·플래그 off·
    한쪽 소스 사건이면 종전 문자열 그대로다.
    """
    if getattr(job, "kind", None) == "diagnosis" and getattr(job, "question", None):
        return job.question
    event = (getattr(job, "payload", None) or {}).get("event") or {}
    server = event.get("serverName") or event.get("hostname") or "대상 서버"
    # 사건 좌표계(plans/50 A′-5): 알람 시각을 질문에 싣는다 — 워커 지연·재처리 시에도 조사가
    # "지금"이 아니라 그 시각을 기준으로 이뤄지도록. 구간·도구 인자 지시는 guidance가 나른다.
    when = getattr(job, "reference_time", None)
    enabled = bool(getattr(settings, "cross_source_investigation_enabled", False))
    xs = active_episode(getattr(job, "payload", None), enabled, when)
    if xs is not None:
        return _cross_source_question(server, when, xs)
    at = f"(발생 시각 {when} — 이 시각 이전 구간을 조사) " if when else ""
    # 결론 유도(수렴 가드): 트리아지 순서는 유지하되 핵심 지표 위주로 간결히 조사하고
    # 충분한 근거가 모이면 즉시 결론을 내도록 지시한다(불필요한 반복 조회가 step을 소진해
    # 미완주로 빠지는 것을 방지 — 실측 기반).
    return (
        f"{server} 장애 원인을 {at}조사하고 트리아지하라(부하→병목→원인 격리→로그). "
        f"핵심 지표(CPU·메모리·디스크·네트워크) 위주로 **간결히** 조사하고, 충분한 근거가 "
        f"모이면 **즉시 결론**을 내라(동일 지표 반복 조회 금지). 모든 주장에 도구 출력을 인용하라."
    )


def _sse_server_config(url: str, health_check_tool: str, tok) -> dict:  # noqa: ANN001 — SecretStr | None
    """holmes `Config.mcp_servers` 항목 한 개(SSE · 헬스체크 도구 · 토큰이 있으면 Bearer — D-125)."""
    config: dict = {"mode": "sse", "url": url, "health_check_tool": health_check_tool}
    if tok is not None and tok.get_secret_value():
        config["headers"] = {"Authorization": f"Bearer {tok.get_secret_value()}"}
    return {"config": config}


def _build_mcp_servers(settings: AgentSettings) -> dict[str, dict] | None:
    """AgentSettings MCP 접속 설정을 holmes Config.mcp_servers 형식으로 조립한다.

    URL이 하나도 없으면 None(로컬 vm_profile 경로 유지). 토큰(SecretStr) 설정 시 Bearer 헤더
    첨부(D-125). 폴스타 관측 데이터 접근 경계는 mcp_server 하나로 일원화(D-119) — 폴스타 SQL
    고수준 도구·PromQL 도구가 이 한 엔드포인트에서 자동 발견된다(RemoteMCPToolset).

    제니퍼 APM 게이트웨이(plans/87 J3 · D-274 ⑦)는 `apm_mcp_url`이 있을 때만 `"apm"` 서버로 더한다 —
    폴스타 URL 없이 APM만 있어도 등록한다. `apm_mcp_url`이 비면 결과가 종전 dict(또는 None)와 같다.
    """
    servers: dict[str, dict] = {}
    if settings.polestar_mcp_url:
        servers["polestar"] = _sse_server_config(
            settings.polestar_mcp_url, "list_sources", settings.polestar_mcp_token
        )
    if settings.apm_mcp_url:
        servers["apm"] = _sse_server_config(settings.apm_mcp_url, "gateway_health", settings.apm_mcp_token)
    return servers or None


def _agent_getter(settings: AgentSettings):
    """`DiagnosisAgent`를 지연 생성해 공유하는 getter.

    조사 함수와 플레이북 소견기(plans/187 W4)가 같은 객체를 쓴다. 캐시된 agent에 FAILED MCP
    툴셋이 있으면 **한 번** 새로 만든다(plans/188 C1 — holmes는 같은 agent 안에서 재검사하지 않는다).
    """
    holder: dict[str, DiagnosisAgent] = {}
    lock = threading.Lock()

    def _get() -> DiagnosisAgent:
        with lock:
            agent = holder.get("agent")
            if agent is None or agent.mcp_unavailable():
                if agent is not None:
                    logger.info("MCP 툴셋 FAILED — 조사 에이전트 재생성(툴셋 재검사)")
                agent = DiagnosisAgent(
                    settings,
                    toolsets=remote_vm_profile(),
                    mcp_servers=_build_mcp_servers(settings),
                )
                holder["agent"] = agent
            return agent

    return _get


def _default_diagnose_fn(
    settings: AgentSettings,
    toolset_check: ToolsetCheck | None = None,
    agent_getter=None,  # noqa: ANN001 — `_agent_getter` 반환 콜러블
):
    """실 조사 함수(DiagnosisAgent)를 지연 생성해 반환한다 — dispatcher.diagnose_fn 주입용.

    LLM 키가 있을 때만 dispatcher가 호출하므로 agent는 최초 호출 시점에 1회 생성한다
    (create_service 시점의 holmes prerequisite 검사·비용 회피). **원격 프로파일 배선**:
    `remote_vm_profile()`(로컬 셸 **off**(D-233)·Prometheus 내장 toolset 비활성) + 폴스타 MCP를
    `mcp_servers`로 등록해 조사가 mcp_server 고수준 도구(폴스타 SQL·PromQL)를 소비한다
    (D-119). mcp_server는 조사 배치에서 execute_sql·raw_promql을 비노출로 두어야
    LLM이 raw SQL/PromQL 방언 오류로 step을 소진하지 않는다(D-122 — 배치 config 규약).

    **툴셋 가용성**(plans/188 C1·C2): holmes는 FAILED MCP 툴셋을 같은 에이전트 안에서 다시 검사하지
    않는다. 그래서 조사 직전 캐시된 agent에 FAILED MCP 툴셋이 있으면 agent를 **한 번** 새로 만든다
    (정상이면 재생성 0회 — 비용 불변). 새로 만든 뒤에도 `polestar` 툴셋이 FAILED면 LLM을
    부르지 않고 「조사 불가 — 관측 도구 연결 실패」 결과를 돌려준다(Q1 A안). APM 툴셋만
    FAILED면 조사를 진행하되, 지침의 도구 이름은 실제 로드된 도구로 거른다.
    검사 결과는 `toolset_check`에 남는다(C6).
    """
    get_agent = agent_getter or _agent_getter(settings)

    def _diagnose(job) -> DiagnosisResult:
        agent = get_agent()
        unavailable = agent.mcp_unavailable()
        if toolset_check is not None and settings.polestar_mcp_url:
            toolset_check.record(unavailable)
        if POLESTAR_SERVER in unavailable:
            logger.warning("폴스타 MCP 툴셋 FAILED — LLM 조사 생략: %s",
                           clean_toolset_error(unavailable[POLESTAR_SERVER]))
            return tools_unavailable_result(unavailable)
        # 조사 지침 주입(plans/50 G5 · D-197): 원격 셸 안내 · 사건 구간 · 운영자 추가 지침.
        # 종전에는 additions를 넘기지 않아 REMOTE_VM_SHELL_NOTE조차 주입되지 않았다.
        result = agent.ask(
            _job_to_question(job, settings),
            system_prompt_additions=build_guidance(
                settings, job, remote=True, available_tools=agent.tool_names()
            ),
        )
        if toolset_check is not None:
            toolset_check.last_answer_malformed = has_tool_call_markup(result.answer)
        return result

    return _diagnose


def _default_prefetch_fn(settings: AgentSettings):
    """결정적 사전수집 함수(plans/50 G4)를 만든다 — dispatcher.prefetch_fn 주입용.

    `evidence_correlation_enabled`가 off거나 mcp URL이 없으면 None(주입 자체를 하지 않아 dispatcher
    경로가 종전과 비트 동일). 켜져 있으면 잡의 기준시각·소스·서버명으로 mcp_server 도구를 배치
    호출해 `CorrelationResult.to_dict()`를 돌려준다. 범위를 만들 수 없는 잡은 None.
    """
    if not settings.evidence_correlation_enabled:
        return None
    call_batch = make_batch_caller(settings)
    if call_batch is None:
        logger.warning("evidence_correlation_enabled=true이나 POLESTAR_MCP_URL 미설정 — 사전수집 비활성")
        return None

    def _prefetch(job) -> dict | None:
        scope = scope_from_job(job, baseline_periods=settings.evidence_baseline_periods,
                               change_overlay=settings.evidence_change_overlay_enabled,
                               related_hosts_max=settings.evidence_correlation_related_hosts,
                               cross_source=settings.cross_source_investigation_enabled)
        if scope is None:
            return None
        return prefetch_and_correlate(scope, call_batch).to_dict()

    return _prefetch


def _default_playbook_wiring(
    settings: AgentSettings, agent_getter  # noqa: ANN001 — `_agent_getter` 반환 콜러블
) -> PlaybookWiring | None:
    """플레이북 배선(plans/187 W4)을 기동 시 1회 만든다. `sre_playbook_enabled` off면 None.

    off면 다른 플레이북 설정(디렉토리·모드·예산)을 읽지 않는다. on이면 플레이북 YAML을 적재하고,
    거부된 파일·fallback 부재를 기동 경고로 남기고, 거부 파일명은 배선에 실어 브리핑 notes에도
    드러낸다(조사는 막지 않는다 — 적재된 것만 쓴다).
    소견기 LLM은 조사 에이전트와 같은 holmes LLM(같은 모델·토큰 예산)을 `temperature=0`으로 쓴다.
    도구 호출은 폴스타·APM 배치 콜러블(URL 미설정이면 그 축 결손)이다.
    """
    if not settings.sre_playbook_enabled:
        return None
    directory = settings.sre_playbook_dir or DEFAULT_PLAYBOOK_DIR
    loaded = load_playbooks(directory)
    for name, reason in loaded.rejected:
        logger.warning("플레이북 거부(기동 적재): %s — %s", name, reason)
    if loaded.error:
        logger.warning("플레이북 적재 오류(%s): %s", directory, loaded.error)
    if loaded.fallback is None:
        logger.warning(
            "fallback 플레이북 미적재(%s) — 일치 없는 알람은 종전 HolmesGPT 조사로 간다", directory
        )
    logger.info("플레이북 %d개 적재(%s)", len(loaded.playbooks), directory)
    # 알려진 오류(W8) — 기동 시 1회 · 읽기 전용 · 거부 파일은 기동 경고(조사는 막지 않는다).
    known = load_known_errors(DEFAULT_KNOWN_ERROR_DIR)
    for name, reason in known.rejected:
        logger.warning("알려진 오류 거부(기동 적재): %s — %s", name, reason)
    logger.info("알려진 오류 %d건 적재(%s)", len(known.known_errors), DEFAULT_KNOWN_ERROR_DIR)

    def _llm(messages: list[dict[str, str]]) -> str:
        response = agent_getter().llm.llm.completion(messages, temperature=0)
        return response.choices[0].message.content or ""

    return PlaybookWiring(
        playbooks=tuple(loaded.playbooks),
        polestar=make_batch_caller(settings),
        apm=make_apm_batch_caller(settings),
        llm=_llm,
        known_errors=known.known_errors,
        # 거부 파일명(플레이북 → KE 순) — 브리핑 notes 1줄로 드러낸다(비면 문구 없음 · 지적 9).
        rejected_files=tuple(name for name, _ in (*loaded.rejected, *known.rejected)),
    )


def _build_dispatcher(
    settings: AgentSettings,
    audit_path: str | Path | None = None,
    toolset_check: ToolsetCheck | None = None,
) -> InvestigationDispatcher:
    """실 dispatcher를 조립한다(diagnose_fn·briefing_fn·prefetch_fn 주입). JobStore executor로 배선된다.

    **`audit_path`를 반드시 넘긴다**(D-213 후속): 미지정이면 dispatcher의 `_audit`이 파일 대신
    로그로만 나가, 감사 JSONL에 `accepted`/`running`만 쌓이고 **종결(done/timeout/failed)이
    한 건도 남지 않는다**(폐쇄망 실측 2026-09-11: 204건 중 종결 0건 · restart_failed 169건).
    JobStore와 같은 파일을 써야 잡 한 건의 생애가 한 곳에서 읽히고, 재기동 복구도 완료된 잡을
    active로 오인하지 않는다.

    `toolset_check`(plans/188 C6)는 조사 직전 툴셋 검사 결과를 받아 `sre_health`에 넘기는
    기록함이다.
    """
    agent_getter = _agent_getter(settings)
    return InvestigationDispatcher(
        settings,
        diagnose_fn=_default_diagnose_fn(settings, toolset_check, agent_getter),
        briefing_fn=build_briefing,
        prefetch_fn=_default_prefetch_fn(settings),
        audit_path=audit_path if audit_path is not None else default_audit_path(),
        playbook=_default_playbook_wiring(settings, agent_getter),
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
    # 주입 경로(테스트)는 실 조사 함수가 없어 툴셋을 검사하지 않는다
    # — `polestar_mcp_reachable`은 null.
    toolset_check = ToolsetCheck()
    if job_store is not None:
        store = job_store
    else:
        # 감사 경로 분리(plans/154 W1) — None이면 두 생산자 모두 기본 경로(종전 그대로).
        audit_path = settings.investigation_audit_path
        store = JobStore(
            settings,
            executor=_build_dispatcher(settings, audit_path, toolset_check),
            audit_path=audit_path,
        )

    mcp = FastMCP("sre-agent", host=settings.service_host, port=DEFAULT_PORT)

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
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        prior_findings: dict | None = None,
    ) -> str:
        """pull형 자연어 진단 잡을 제출한다(§3 — 챗 의도 위임용). 동일 잡 패턴.

        `target_state`(Plan 81)는 호출자가 판정한 대상 가용성이다. **선택 인자**이므로
        넘기지 않는 구버전 호출자는 종전과 동일하게 동작한다(fail-open). 값이 있고
        `state == "unavailable"`이면 dispatcher가 조사 전에 거부한다.

        `reference_time`(ISO 8601)·`lookback_minutes`(plans/50 A′-5)는 사건 좌표계다 — 조사가
        "지금"이 아니라 그 구간의 증거를 쓰게 한다. 형식 오류는 `rejected`로 돌려준다.

        `prior_findings`(plans/187 W10 · 선택)는 이미 수집된 판정 요약(판정·판독 enum·
        사용 도구·사건창 — LLM 서술 제외)이다. 지침에 「이미 수집된 판정」 블록으로 렌더된다.
        넘기지 않으면 종전과 같다.
        """
        return json.dumps(
            store.submit_diagnosis(
                question, server_name, hostname, db_id, target_state,
                reference_time=reference_time, lookback_minutes=lookback_minutes,
                prior_findings=prior_findings,
            ),
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
        polestar_mcp_reachable[, polestar_mcp_error][, last_answer_malformed]}.
        holmes_ready는 조사 실행 가능성(조사 LLM 게이트 통과
        — `investigation_llm_stub_reason() is None` · D-230)을 정직하게 반영한다.
        polestar_mcp_reachable(plans/188 C6)은 **마지막 조사 직전 툴셋 검사 결과**다
        — true/false, 아직 조사 전이면 null. false면 선택 키 polestar_mcp_error(holmes 사유)를
        더한다. 헬스 호출마다 프로브하지 않는다. last_answer_malformed(C7)는 LLM 답을 받은 마지막
        조사의 답에 도구 호출 마크업이 섞였는지(true/false) — 그런 조사가 없으면 키가 없다.
        """
        health: dict[str, object] = {
            "status": "ok",
            "version": __version__,
            "contract_version": CONTRACT_VERSION,
            "holmes_ready": settings.investigation_llm_stub_reason() is None,
            "polestar_mcp_reachable": toolset_check.polestar_reachable,
        }
        if toolset_check.polestar_reachable is False:
            health["polestar_mcp_error"] = toolset_check.polestar_error or ""
        if toolset_check.last_answer_malformed is not None:
            health["last_answer_malformed"] = toolset_check.last_answer_malformed
        return json.dumps(health, ensure_ascii=False)

    setattr(mcp, _JOB_STORE_ATTR, store)
    logger.info("sre_agent 조사 서비스 생성: 도구 5종 등록(port=%d)", DEFAULT_PORT)
    return mcp


def get_job_store(mcp: FastMCP) -> JobStore:
    """create_service가 부착한 JobStore를 반환한다(run_service·테스트용)."""
    return getattr(mcp, _JOB_STORE_ATTR)


def build_asgi_app(
    mcp: FastMCP, token: str | None, settings: AgentSettings | None = None
) -> Starlette | AdminPathRouter:
    """FastMCP SSE 앱에 정적 Bearer 인증 미들웨어를 씌워 반환한다.

    `mcp.run(transport="sse")`는 내부에서 자체 앱을 만들어 미들웨어 주입점이 없으므로,
    run_service는 이 함수로 앱을 조립해 uvicorn에 직접 넘긴다.
    settings에 관리 토큰(`SRE_AGENT_ADMIN_TOKEN`)이 있을 때만 `/admin/config` 경로 분기 래퍼를
    바깥에 씌운다(plans/191 · D-345). 없으면 반환 객체가 종전과 같다. 관리 토큰이 서비스 토큰과
    같으면 관리 경로가 서비스 토큰으로 열리므로 등록하지 않고 경고 1줄을 남긴다.
    """
    app = mcp.sse_app()
    app.add_middleware(StaticBearerAuthMiddleware, token=token)
    if settings is None or settings.sre_agent_admin_token is None:
        return app
    admin_token = settings.sre_agent_admin_token.get_secret_value().strip()
    if not admin_token:
        return app
    if token is not None and hmac.compare_digest(admin_token.encode(), token.encode()):
        logger.warning(
            "설정 관리 API 미등록: 관리 토큰이 서비스 Bearer 토큰과 같다"
            "(SRE_AGENT_ADMIN_TOKEN을 바꿀 것)"
        )
        return app
    return AdminPathRouter(app, build_admin_app(build_adapter(settings), admin_token))


__all__ = [
    "DEFAULT_PORT",
    "DEFAULT_HOST",
    "StaticBearerAuthMiddleware",
    "create_service",
    "get_job_store",
    "build_asgi_app",
]
