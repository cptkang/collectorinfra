"""서비스·업무 조회 도구 2종 MCP 등록 (plans/134 W3 N-9 · W4 N-12).

`register_tools`(같은 계층의 MCP 서버 모듈)가 데이터 도구 실행기(`run_data` — 작업 · 감사 ·
오류 봉투)를 넘겨 부른다 — 모든 데이터 도구는 작업으로 돈다(D-300 ①). 도구 설명(docstring)은
LLM이 읽는다 — 짧게, 인자의 뜻을 적는다. 도메인·업무 시계열은 `apm_metrics(series, scope
domain·business)`가 맡는다(SPEC E-24 — 시계열 정본은 하나).
"""

from __future__ import annotations

from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from apm_gateway.application.scope_tools import ScopeTools, scope_target
from apm_gateway.interface.manage_server import RunData
from apm_gateway.interface.server import OwnerArg, WaitArg

TOOL_NAMES = ("apm_service_status", "apm_business")

ServiceArg = Annotated[
    str | list[str] | None,
    Field(
        description="서비스(APM 도메인) 이름 — 하나 또는 목록. 정확 → 구분자 무시 → 앞부분 → 포함"
        " 중 가장 앞 단계만 · 못 찾은 이름은 다른 서비스로 대신하지 않고 비슷한 이름 후보"
        "(suggestions)만 준다 · 비우면 고른 소스의 전 서비스"
    ),
]
DomainArg = Annotated[
    int | None, Field(description="도메인 ID로 좁힘(service와 함께 주면 둘 다 만족)")
]


def register_scope_tools(mcp: FastMCP, scope: ScopeTools, run_data: RunData) -> list[str]:
    """`apm_service_status`·`apm_business`를 등록하고 이름을 돌려준다."""

    @mcp.tool()
    async def apm_service_status(
        service: ServiceArg = None,
        domain_id: DomainArg = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """서비스(APM 도메인) 현재값 — 서비스마다 TPS·평균 응답시간·액티브 서비스·사용자·동시
        사용자·PLC 거절률·방문·호출 수·액티브 구간 4칸·데이터 서버 주소(행 = 소스·도메인).
        이름으로 찾았으면 행에 service_text·match_tier. 추세는 apm_metrics(series, scope domain)."""
        return await run_data(
            "apm_service_status",
            scope_target(service, None, domain_id),
            lambda: scope.apm_service_status(service, domain_id, source_ids),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_business(
        mode: Annotated[
            str, Field(description="current(업무 현재값 · 기본)·list(업무 정의 목록)")
        ] = "current",
        business: Annotated[
            str | list[str] | None,
            Field(
                description="업무 이름 — 하나 또는 목록(여러 업무 비교). 검색 규칙은 service와 같다"
                " · 한 이름에 여럿이 맞으면 전부 · 비우면 범위의 전 업무"
            ),
        ] = None,
        service: ServiceArg = None,
        domain_id: DomainArg = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """APM 업무 — current: 업무마다 TPS·평균 응답시간·액티브 서비스·동시 사용자·액티브 구간
        4칸 · list: 업무 정의(이름·설명·규칙 · 설명·규칙은 마스킹). service·domain_id로 도메인을
        좁힌다. 추세는 apm_metrics(series, scope business)."""
        return await run_data(
            "apm_business",
            scope_target(service, business, domain_id),
            lambda: scope.apm_business(mode, business, service, domain_id, source_ids),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    return list(TOOL_NAMES)
