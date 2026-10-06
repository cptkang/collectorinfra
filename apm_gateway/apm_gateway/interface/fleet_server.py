"""전 대상 순위·이벤트 도구 `apm_fleet` MCP 등록 (plans/134 W3 N-10 · 계약 A-6).

`register_tools`(같은 계층의 MCP 서버 모듈)가 데이터 도구 실행기(`run_data` — 작업 · 감사 ·
오류 봉투)를 넘겨 부른다. `service` 인자 주석은 서비스 도구 모듈(`scope_server`)의 것을 쓴다(같은
뜻·같은 해석). 순위 지표(`metric`)는 MCP 스키마 enum으로 드러낸다 — 허용값은 도구
코어의 `RANKING_METRICS`(실시간 인스턴스 수치 칸 중립 이름 전부)다.
"""

from __future__ import annotations

from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from apm_gateway.application.fleet_tools import (
    DEFAULT_RANKING_METRIC,
    RANKING_METRICS,
    FleetTools,
)
from apm_gateway.application.scope_tools import scope_target
from apm_gateway.interface.manage_server import RunData
from apm_gateway.interface.scope_server import ServiceArg
from apm_gateway.interface.server import FullArg, OwnerArg, WaitArg

TOOL_NAMES = ("apm_fleet",)
# 런타임 enum — 튜플을 Literal에 넘기면 각 값이 허용값이 된다(정적 검사기는 값 목록을 모른다)
RankingMetric = Literal[RANKING_METRICS]  # type: ignore[valid-type]


def register_fleet_tools(mcp: FastMCP, fleet: FleetTools, run_data: RunData) -> list[str]:
    """`apm_fleet`을 등록하고 이름을 돌려준다."""

    @mcp.tool()
    async def apm_fleet(
        mode: Annotated[
            Literal["ranking", "events"],
            Field(
                description="ranking(전 인스턴스 실시간 지표 순위 — 서버를 정하지 않은 「가장 느린·"
                "바쁜」) · events(전 도메인 이벤트)"
            ),
        ],
        metric: Annotated[
            RankingMetric,
            Field(description="ranking 지표(실시간 인스턴스 수치 칸 · 기본 평균 응답시간)"),
        ] = DEFAULT_RANKING_METRIC,
        order: Annotated[
            Literal["desc", "asc"], Field(description="ranking 정렬 — desc(큰 값부터 · 기본)·asc")
        ] = "desc",
        n: Annotated[
            int | None,
            Field(description="상위 N(1 이상 · 상한 없음). 비우면 ranking 10 · events 전부"),
        ] = None,
        full: FullArg = False,
        level: Annotated[
            str | None, Field(description="events 레벨 — fatal·warning·normal")
        ] = None,
        level_mode: Annotated[
            str | None,
            Field(description="events level 해석 — min(기본: 그 레벨 이상)·exact(그 레벨만)"),
        ] = None,
        error_type: Annotated[
            str | None, Field(description="events 오류 유형(대문자로 맞춘다)")
        ] = None,
        reference_time: str | None = None,
        lookback_minutes: Annotated[
            int | None, Field(description="events 구간 길이(분 · 기본 30 · 상한 없음)")
        ] = None,
        domain_id: Annotated[
            int | None,
            Field(
                description="이 도메인만(service와 함께 주면 둘 다 만족 · 비우면 고른 소스의 전"
                " 도메인)"
            ),
        ] = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
        service: ServiceArg = None,
    ) -> str:
        """서버를 정하지 않은 전 대상 조회 — ranking: 전 인스턴스(고른 소스의 전 도메인)를 모은 뒤
        지표로 정렬한 상위 N(조회 실패 도메인이 있으면 잠정 순위 provisional) · events: 전 도메인의
        이벤트(최근 30분 기본 · 시각 내림차순 · 실패 도메인은 0건이 아니라 domains_failed ·
        coverage는 답한 출처). service(서비스 이름)를 주면 그 서비스의 도메인으로만 좁힌다.
        한 서버·인스턴스를 볼 때는 다른 도구를 쓴다."""
        target_text = scope_target(service, None, domain_id)
        if mode == "events":
            ignored = tuple(
                name
                for name, given in (
                    ("metric", metric != DEFAULT_RANKING_METRIC),
                    ("order", order != "desc"),
                )
                if given
            )
            label = f"events:{target_text}"
            return await run_data(
                "apm_fleet",
                label,
                lambda: fleet.events(
                    level,
                    level_mode,
                    error_type,
                    n,
                    full,
                    reference_time,
                    lookback_minutes,
                    domain_id,
                    source_ids,
                    ignored=ignored,
                    service=service,
                ),
                owner=owner,
                wait_seconds=wait_seconds,
                investigation_id=investigation_id,
                thread_id=thread_id,
            )
        ignored = tuple(
            name
            for name, value in (
                ("level", level),
                ("level_mode", level_mode),
                ("error_type", error_type),
                ("reference_time", reference_time),
                ("lookback_minutes", lookback_minutes),
            )
            if value is not None
        )
        label = f"ranking:{metric}:{target_text}"
        return await run_data(
            "apm_fleet",
            label,
            lambda: fleet.ranking(
                metric, order, n, full, domain_id, source_ids, ignored=ignored, service=service
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    return list(TOOL_NAMES)
