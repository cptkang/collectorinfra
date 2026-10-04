"""MCP 서버 — `apm_*` 도구 등록 · 주체별 Bearer · 작업 도구 · 감사 (plans/87 §0.7 (3) ·
SPEC-apm-gateway §3 · plans/134 W0-B SPEC-apm-question-coverage §2.1·§3.6·§3.7).

전송은 SSE(`sre_agent` RemoteMCPToolset `mode: sse` 전례). 전송 인증은 `mcp_server` 패키지의
`StaticBearerAuthMiddleware`에서 출발해 **주체별 토큰**으로 넓혔다(경계 불변식상 import 불가 —
헤더 판독·401 응답 모양은 같다). 요청 토큰으로 호출 주체를 정해 요청 scope에 싣고, 도구는 그
주체로 작업을 만들고 찾는다. 도구는 예외를 전파하지 않고 `{"error": code, "reason": …}` JSON을
돌려준다.

- 데이터 도구 11종(W2에서 통계·지표·변경 감지 3종 추가)은 모두 작업(`JobManager.execute`)으로
  돈다 — 선택 인자 `owner`·`wait_seconds`.
  `wait_seconds`가 없으면 끝날 때까지 기다린다(기존 소비자 의미 그대로).
- 작업 도구 3종(`apm_job_status`·`apm_job_cancel`·`apm_job_read`)은 같은 주체 + 같은 `owner`일 때만
  응답하고(아니면 `job_not_found`) 외부 API를 부르지 않는다(감사 `api_calls=0`).
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any

from mcp.server.fastmcp import FastMCP
from pydantic import Field
from starlette.applications import Starlette
from starlette.types import ASGIApp, Receive, Scope, Send

from apm_gateway.adapters.jennifer.allowlist import NotAllowedError
from apm_gateway.application.jobs import Job, JobManager
from apm_gateway.application.masking import mask_text
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain import jobs as js
from apm_gateway.domain.errors import API_ERROR, CONTRACT_VIOLATION, INVALID_ARGUMENT, ApmError
from apm_gateway.interface.audit import audit

logger = logging.getLogger(__name__)

SERVER_NAME = "apm-gateway"
# 전송 미들웨어가 요청 ASGI scope에 싣는 호출 주체 키
PRINCIPAL_SCOPE_KEY = "apm_gateway.principal"
JOB_TOOLS = ("apm_job_status", "apm_job_cancel", "apm_job_read")

OwnerArg = Annotated[
    str | None,
    Field(description="결과·작업 소유자(불투명 문자열). 작업 도구는 같은 owner일 때만 응답한다"),
]
NArg = Annotated[
    int | None,
    Field(description="상위 N(1 이상 · 상한 없음). 비우면 도구 기본(상위 10)"),
]
FullArg = Annotated[
    bool,
    Field(description="true면 상위 N 대신 전체 행(정렬 유지 · 큰 결과는 결과 파일 artifact)"),
]
MetricsArg = Annotated[
    list[str] | None,
    Field(
        description="지표 이름 목록(인스턴스 지표 카탈로그 — apm_metrics catalog로 확인). 모르는"
        " 지표는 invalid_argument와 비슷한 이름 후보"
    ),
]
IntervalArg = Annotated[
    int | None,
    Field(description="시계열 간격(분 · 양의 정수 · 기본 5). 허용값은 서버가 정한다"),
]
WaitArg = Annotated[
    float | None,
    Field(
        description="이 초 안에 끝나지 않으면 작업 핸들(job)을 돌려주고 백그라운드로 계속한다"
        "(apm_job_status로 확인). 비우면 끝날 때까지 기다린다"
    ),
]


class BearerPrincipalMiddleware:
    """전송 인증 + 호출 주체(D-125 · §3.6) — `mcp_server` `StaticBearerAuthMiddleware`의 주체별
    확장.

    토큰이 하나도 없으면 무인증 통과(로컬/개발 · 주체 `anonymous`). 있으면 모든 HTTP 요청에
    `Authorization: Bearer <token>`을 요구하고, 맞는 토큰의 주체를 요청 scope에 싣는다. 불일치는
    401. 비교는 상수 시간이고 토큰 값은 어디에도 남기지 않는다.
    """

    def __init__(self, app: ASGIApp, tokens: Mapping[str, str] | None) -> None:
        self.app = app
        self._bearers = [
            (f"Bearer {token}".encode(), principal) for principal, token in (tokens or {}).items()
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not self._bearers:
            scope[PRINCIPAL_SCOPE_KEY] = js.ANONYMOUS_PRINCIPAL
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        provided = headers.get(b"authorization", b"").decode("latin-1")
        principal = self._principal_of(provided.encode("latin-1"))
        if principal is None:
            await self._reject(send)
            return
        scope[PRINCIPAL_SCOPE_KEY] = principal
        await self.app(scope, receive, send)

    def _principal_of(self, provided: bytes) -> str | None:
        found = None
        # 전부 비교한다(상수 시간 — 일치 위치를 흘리지 않게)
        for bearer, principal in self._bearers:
            if hmac.compare_digest(provided, bearer):
                found = principal
        return found

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


def build_asgi_app(mcp: FastMCP, tokens: Mapping[str, str] | str | None) -> Starlette:
    """SSE 앱에 주체별 Bearer 미들웨어를 씌운다(`mcp.run(transport="sse")`에는 주입점이 없다).

    `tokens`는 `{주체: 토큰}`(설정 `ServerConfig.bearer_tokens`) — 문자열 하나를 주면 주체
    `default`다.
    """
    if isinstance(tokens, str):
        tokens = {js.DEFAULT_PRINCIPAL: tokens}
    app = mcp.sse_app()
    app.add_middleware(BearerPrincipalMiddleware, tokens=dict(tokens or {}))
    return app


def request_principal(mcp: FastMCP) -> str:
    """도구 호출의 주체 — 전송 미들웨어가 그 요청 scope에 실은 값(HTTP 밖 직접 호출은 anonymous)."""
    try:
        request = mcp.get_context().request_context.request
    except (LookupError, ValueError):
        return js.ANONYMOUS_PRINCIPAL
    scope = getattr(request, "scope", None)
    value = scope.get(PRINCIPAL_SCOPE_KEY) if isinstance(scope, dict) else None
    return value if isinstance(value, str) and value else js.ANONYMOUS_PRINCIPAL


async def _guarded(
    tools: ApmTools, tool: str, call: Callable[[], Awaitable[dict[str, Any]]]
) -> dict[str, Any]:
    """도구 코어 1회 — 예외를 계약 오류 봉투로 바꾼다(작업 안에서도 같은 봉투)."""
    try:
        return await call()
    except ApmError as e:
        return tools.err(tool, e.code, e.reason)
    except NotAllowedError as e:  # 도구 코드가 허용목록 밖 요청을 만들었다 — 게이트웨이 버그
        logger.warning("허용목록 거부(게이트웨이 버그): tool=%s %s", tool, e)
        return tools.err(tool, CONTRACT_VIOLATION, f"허용목록 거부: {e}")
    except (TypeError, ValueError) as e:
        return tools.err(tool, INVALID_ARGUMENT, str(e))
    except Exception as e:
        logger.exception("도구 실행 실패: %s", tool)
        return tools.err(tool, API_ERROR, f"내부 오류: {type(e).__name__}")


def _job_id_of(result: dict[str, Any]) -> str | None:
    job = result.get("job")
    return job.get("job_id") if isinstance(job, dict) else None


def _sources_text(calls: dict[str, int], order: list[str]) -> str:
    """감사 `sources=` — 설정 선언 순서 · 호출 없으면 `-`."""
    rank = {sid: i for i, sid in enumerate(order)}
    items = sorted(
        ((sid, n) for sid, n in calls.items() if n > 0), key=lambda x: rank.get(x[0], len(rank))
    )
    return ",".join(f"{sid}:{n}" for sid, n in items) or "-"


async def run_tool(
    tools: ApmTools,
    tool: str,
    target: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    investigation_id: str | None = None,
    thread_id: str | None = None,
    jobs: JobManager | None = None,
    principal: str = js.ANONYMOUS_PRINCIPAL,
    owner: str | None = None,
    wait_seconds: float | None = None,
) -> str:
    """도구 1회 실행 — 작업으로 돌리고(`jobs`가 있으면) 오류를 계약 JSON으로 바꾸고 감사 1줄을
    남긴다. 작업으로 돌면 `api_calls`·`sources`는 **그 작업의** 호출 수다(같은 시간에 도는 다른
    작업의 호출이 섞이지 않는다 · 승격된 작업은 접수까지의 호출 수)."""
    started = time.monotonic()
    calls_before = tools.sources.calls()
    owned: list[Job] = []
    try:
        if jobs is None:
            result = await _guarded(tools, tool, call)
        else:
            result = await jobs.execute(
                tool,
                lambda: _guarded(tools, tool, call),
                principal=principal,
                owner=owner,
                target=target,
                wait_seconds=wait_seconds,
                investigation_id=investigation_id,
                thread_id=thread_id,
                on_job=owned.append,
            )
    except ApmError as e:  # 작업 인자 검사(wait_seconds·owner)
        result = tools.err(tool, e.code, e.reason)
    elapsed_ms = (time.monotonic() - started) * 1000
    if owned:
        called = dict(owned[0].calls_by_source)
    elif jobs is None:
        called = {
            sid: n - calls_before.get(sid, 0)
            for sid, n in tools.sources.calls().items()
            if n > calls_before.get(sid, 0)
        }
    else:
        called = {}
    audit(
        tool,
        mask_text(target, limit=120),
        elapsed_ms,
        rows=result.get("row_count"),
        api_calls=sum(called.values()),
        sources=_sources_text(called, tools.sources.ids),
        error=result.get("error"),
        investigation_id=investigation_id,
        thread_id=thread_id,
        principal=principal,
        job_id=_job_id_of(result),
    )
    return json.dumps(result, ensure_ascii=False, default=str)


async def run_job_tool(
    tools: ApmTools,
    tool: str,
    job_id: str,
    call: Callable[[], Awaitable[dict[str, Any]]],
    *,
    principal: str,
) -> str:
    """작업 도구 1회 — 외부 API를 부르지 않는다(감사 `api_calls=0`)."""
    started = time.monotonic()
    result = await _guarded(tools, tool, call)
    audit(
        tool,
        "-",
        (time.monotonic() - started) * 1000,
        rows=result.get("row_count"),
        api_calls=0,
        error=result.get("error"),
        principal=principal,
        job_id=job_id if js.is_job_id(job_id) else "-",
    )
    return json.dumps(result, ensure_ascii=False, default=str)


def audit_job_finished(job: Job) -> None:
    """백그라운드(승격된) 작업이 끝났을 때의 감사 1줄 — 작업 수명 전체의 호출 수·소스별 분해."""
    finished = job.finished_at if job.finished_at is not None else job.updated_at
    readable = job.state in js.READABLE_STATES
    audit(
        job.tool,
        job.target,
        (finished - job.created_at) * 1000,
        rows=(job.artifact or {}).get("total_rows", 0),
        api_calls=job.total_calls,
        sources=_sources_text(job.calls_by_source, sorted(job.calls_by_source)),
        error=None if readable else (job.error or {}).get("code", job.state),
        investigation_id=job.investigation_id,
        thread_id=job.thread_id,
        principal=job.principal,
        job_id=job.job_id,
    )


def register_tools(mcp: FastMCP, tools: ApmTools, jobs: JobManager | None = None) -> list[str]:
    """`apm_*` 데이터 도구 11종 + 작업 도구 3종 + `gateway_health`를 등록하고 이름 목록을
    돌려준다."""
    if jobs is None:
        jobs = JobManager.from_config(
            tools.cfg, envelope=tools.ok, error_envelope=tools.err, on_finish=audit_job_finished
        )
    manager = jobs

    async def run_data(
        tool: str,
        target: str,
        call: Callable[[], Awaitable[dict[str, Any]]],
        *,
        owner: str | None,
        wait_seconds: float | None,
        investigation_id: str | None,
        thread_id: str | None,
    ) -> str:
        return await run_tool(
            tools,
            tool,
            target,
            call,
            investigation_id=investigation_id,
            thread_id=thread_id,
            jobs=manager,
            principal=request_principal(mcp),
            owner=owner,
            wait_seconds=wait_seconds,
        )

    @mcp.tool()
    async def apm_instance_map(
        hostname: str | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """APM 인스턴스 목록(전부 — 큰 목록은 결과 파일)과 hostname 정합 결과. hostname을 주면 그
        서버의 WAS 인스턴스만(정합 신뢰도·근거 포함). source_ids로 APM 소스를 좁힐 수 있다."""
        return await run_data(
            "apm_instance_map",
            hostname or "*",
            lambda: tools.apm_instance_map(hostname, source_ids),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_app_health(
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """WAS 골든 시그널 — 평균 응답시간·TPS·액티브 서비스·PLC 거절률·방문·호출 수·액티브 구간
        4칸(현재) + 구간 p50/p95·에러율(1분 조각으로 창 전체) + 긴 창은 시 단위 합계 + 판정."""
        return await run_data(
            "apm_app_health",
            hostname,
            lambda: tools.apm_app_health(
                hostname, instance_id, reference_time, lookback_minutes, source_ids
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_runtime_health(
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
        metrics: MetricsArg = None,
        interval_minute: IntervalArg = None,
    ) -> str:
        """JVM 런타임 — 힙 사용량(MB)·GC 시간 비중(%)·프로세스 CPU·스레드·소켓·파일 수 + 구간
        추세(기본 힙 사용·힙 할당·GC 시간 비중 3종 · 5분 간격 · 정합된 인스턴스 전부 · metrics로
        지표를 바꾼다) + 판정(힙 압박·GC 지연)."""
        return await run_data(
            "apm_runtime_health",
            hostname,
            lambda: tools.apm_runtime_health(
                hostname,
                instance_id,
                reference_time,
                lookback_minutes,
                source_ids,
                metrics,
                interval_minute,
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_resource_pool(
        hostname: str,
        instance_id: int | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """자원 풀(현재값 전용) — DB 커넥션 풀 사용률·실행 모드별 액티브 서비스 수 + 판정(DB 풀
        고갈·스레드 정체)."""
        return await run_data(
            "apm_resource_pool",
            hostname,
            lambda: tools.apm_resource_pool(hostname, instance_id, source_ids),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_slow_transactions(
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        n: NArg = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
        full: FullArg = False,
    ) -> str:
        """느린 트랜잭션 상위 N(기본 10 · full이면 전부) — 시간 분해(cpu·sql·fetch·external·
        network)·SQL/fetch/외부 호출 건수·guid·오류 유형·profile_ref + 판정(SQL 지연·외부 호출
        지연). 사용자·클라이언트 식별자는 가린다."""
        return await run_data(
            "apm_slow_transactions",
            hostname,
            lambda: tools.apm_slow_transactions(
                hostname, instance_id, reference_time, lookback_minutes, n, source_ids, full
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_active_services(
        hostname: str,
        instance_id: int | None = None,
        n: NArg = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
        full: FullArg = False,
    ) -> str:
        """지금 실행 중인 서비스 상위 N(경과 시간 순 · 기본 10 · full이면 전부 · 현재값 전용) —
        상태·실행 모드·실행 텍스트(마스킹)·CPU·SQL·fetch 건수·active_ref(실행 중 요청 상세 입력) +
        판정(스레드 정체)."""
        return await run_data(
            "apm_active_services",
            hostname,
            lambda: tools.apm_active_services(hostname, instance_id, n, source_ids, full),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_events(
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        level: str | None = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
        level_mode: Annotated[
            str | None,
            Field(description="level 해석 — min(기본: 그 레벨 이상)·exact(그 레벨만)"),
        ] = None,
        error_type: Annotated[
            str | None,
            Field(
                description="오류 유형(대문자로 맞춘다) — 오류 기록과 이벤트를 그 유형으로 거른다"
            ),
        ] = None,
        record: Annotated[
            str | None,
            Field(description="행 종류 — event(기본: 이벤트)·error(오류 기록)"),
        ] = None,
        n: Annotated[
            int | None,
            Field(description="최근 N건(1 이상). 비우면 전부"),
        ] = None,
        full: FullArg = False,
    ) -> str:
        """APM 이벤트·오류 기록(기본 최근 30분 · 기간 상한 없음) — 유형·레벨·값·메시지(마스킹)·
        profile_ref + 오류 유형별 건수(전 유형) + 판정. level은 fatal·warning·normal."""
        return await run_data(
            "apm_events",
            hostname,
            lambda: tools.apm_events(
                hostname,
                reference_time,
                lookback_minutes,
                level,
                source_ids,
                level_mode,
                error_type,
                record,
                n,
                full,
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_transaction_profile(
        hostname: str,
        domain_id: int | None = None,
        txid: str | None = None,
        time_ms: int | None = None,
        top_k: Annotated[
            int | None, Field(description="SQL 개수(1 이상). 비우면 전부")
        ] = None,
        source_id: str | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """개별 트랜잭션 프로파일(화면용 마스킹 발췌 — 전문은 결과 파일)·SQL 전부(리터럴 마스킹).
        source_id·domain_id·txid·time_ms는 앞 도구의 profile_ref를 그대로 넘긴다(APM 소스가 둘
        이상이면 source_id 필수)."""
        return await run_data(
            "apm_transaction_profile",
            hostname,
            lambda: tools.apm_transaction_profile(
                hostname, domain_id, txid, time_ms, top_k, investigation_id, source_id
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_status_stats(
        kind: Annotated[
            str,
            Field(description="통계 종류 — application(서비스)·sql·external_call(외부 호출)"),
        ],
        hostname: str,
        instance_id: int | None = None,
        reference_time: str | None = None,
        lookback_minutes: Annotated[
            int | None, Field(description="구간 길이(분 · 기본 60) — 시 경계로 넓혀 조회한다")
        ] = None,
        sort_by: Annotated[
            str | None,
            Field(description="정렬 기준 지표 이름(서버 기본 = 호출 수). 서버가 거부하면 그 사유"),
        ] = None,
        n: NArg = None,
        full: FullArg = False,
        application_name: Annotated[
            str | None,
            Field(description="서비스 이름 패턴(kind application에서만)"),
        ] = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """시 단위 통계 상위 N(기본 10 · full이면 서버가 주는 전부) — 서비스·SQL·외부 호출별 호출·
        실패·응답시간(평균·최대·합계) + 합계(호출 수 가중 평균). 구간은 정시 경계로 맞춘다. SQL
        리터럴·URL 쿼리 값은 가린다."""
        return await run_data(
            "apm_status_stats",
            hostname,
            lambda: tools.apm_status_stats(
                kind,
                hostname,
                instance_id,
                reference_time,
                lookback_minutes,
                sort_by,
                n,
                full,
                application_name,
                source_ids,
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_metrics(
        mode: Annotated[
            str,
            Field(description="catalog(지표 이름 목록 · 기본)·series(인스턴스 지표 시계열)"),
        ] = "catalog",
        scope: Annotated[
            str | None,
            Field(
                description="지표 군 — catalog는 domain·instance·business·application·sql·"
                "external_call 중 거르기(비우면 전부) · series는 instance"
            ),
        ] = None,
        hostname: Annotated[
            str | None, Field(description="series 대상 서버(정합된 인스턴스 전부)")
        ] = None,
        instance_id: int | None = None,
        metrics: MetricsArg = None,
        interval_minute: IntervalArg = None,
        reference_time: str | None = None,
        lookback_minutes: Annotated[
            int | None, Field(description="series 구간 길이(분 · 기본 60)")
        ] = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """APM 지표 — catalog: 소스별 지표 이름 전부(행 = 소스·군·지표) · series: 지표 시계열
        (행 = 인스턴스·지표·시각·값 · 지표 이름은 카탈로그로 검증)."""
        return await run_data(
            "apm_metrics",
            hostname or "*",
            lambda: tools.apm_metrics(
                mode,
                scope,
                hostname,
                instance_id,
                metrics,
                interval_minute,
                reference_time,
                lookback_minutes,
                source_ids,
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_source_changes(
        hostname: str,
        reference_time: str | None = None,
        lookback_minutes: Annotated[
            int | None, Field(description="구간 길이(분 · 기본 1440 = 24시간 · 상한 없음)")
        ] = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """소스코드·리소스 변경 감지 이력(인스턴스·감지 시각) — 데이터 서버가 변경을 인지한
        시각이며 배포 확정이 아니다."""
        return await run_data(
            "apm_source_changes",
            hostname,
            lambda: tools.apm_source_changes(
                hostname, reference_time, lookback_minutes, source_ids
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_job_status(job_id: str, owner: OwnerArg = None) -> str:
        """오래 걸린 조회 작업의 상태·진행·예상 시간. 끝났으면 결과 요약(result_meta)·결과 파일
        참조(artifact)·앞 행 미리보기(rows)를 함께 돌려준다."""
        principal = request_principal(mcp)
        return await run_job_tool(
            tools,
            "apm_job_status",
            job_id,
            lambda: manager.status(job_id, principal=principal, owner=owner),
            principal=principal,
        )

    @mcp.tool()
    async def apm_job_cancel(job_id: str, owner: OwnerArg = None) -> str:
        """진행 중인 조회 작업을 취소한다(받아 둔 결과 조각도 지운다). 끝난 작업은 그대로 둔다."""
        principal = request_principal(mcp)
        return await run_job_tool(
            tools,
            "apm_job_cancel",
            job_id,
            lambda: manager.cancel(job_id, principal=principal, owner=owner),
            principal=principal,
        )

    @mcp.tool()
    async def apm_job_read(
        job_id: str,
        owner: OwnerArg = None,
        chunk: int | None = None,
        part: str | None = None,
    ) -> str:
        """끝난 조회 작업의 결과 파일을 읽는다 — chunk(0부터)를 주면 그 청크의 행 전부, part를
        주면 그 텍스트 부분 전문(마스킹본). 둘 다 없으면 첫 청크."""
        principal = request_principal(mcp)
        return await run_job_tool(
            tools,
            "apm_job_read",
            job_id,
            lambda: manager.read(job_id, principal=principal, owner=owner, chunk=chunk, part=part),
            principal=principal,
        )

    @mcp.tool()
    async def gateway_health() -> str:
        """게이트웨이 상태 — APM 소스별 설정·도달 여부·도메인 수 · 허용 경로 수 · 전체 상태 ·
        폴러 상태 · 작업 실행 현황(헬스체크용)."""

        async def health() -> dict[str, Any]:
            return {**await tools.gateway_health(), "jobs": manager.summary()}

        return await run_tool(
            tools, "gateway_health", "-", health, principal=request_principal(mcp)
        )

    return [
        "apm_instance_map",
        "apm_app_health",
        "apm_runtime_health",
        "apm_resource_pool",
        "apm_slow_transactions",
        "apm_active_services",
        "apm_events",
        "apm_transaction_profile",
        "apm_status_stats",
        "apm_metrics",
        "apm_source_changes",
        *JOB_TOOLS,
        "gateway_health",
    ]


def create_server(
    tools: ApmTools,
    *,
    jobs: JobManager | None = None,
    host: str = "127.0.0.1",
    port: int = 9096,
) -> FastMCP:
    mcp = FastMCP(SERVER_NAME, host=host, port=port)
    names = register_tools(mcp, tools, jobs)
    logger.info("APM 게이트웨이 MCP 서버 생성: 도구 %d종(%s)", len(names), ", ".join(names))
    return mcp
