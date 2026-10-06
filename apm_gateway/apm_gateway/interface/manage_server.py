"""관리·민감 조회 도구 4종 MCP 등록 (plans/134 W7 · N-15~N-17 · D-299 ③ 조사에서도 노출).

`register_tools`(같은 계층의 MCP 서버 모듈)가 데이터 도구 실행기(`run_data` — 작업 · 감사 ·
오류 봉투)를 넘겨 부른다. 도구 설명(docstring)은 조사 LLM이 읽는다 — 짧게, 인자의 출처를 적는다.
감사 대상 문자열에는 계정 ID를 싣지 않는다(개인정보 · G-11).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Protocol

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from apm_gateway.application.manage_tools import ManageTools
from apm_gateway.interface.server import OwnerArg, WaitArg

TOOL_NAMES = ("apm_config", "apm_environment", "apm_users", "apm_active_detail")


class RunData(Protocol):
    """`register_tools`의 데이터 도구 실행기 — 작업으로 돌리고 감사 1줄을 남긴다."""

    def __call__(
        self,
        tool: str,
        target: str,
        call: Callable[[], Awaitable[dict[str, Any]]],
        *,
        owner: str | None,
        wait_seconds: float | None,
        investigation_id: str | None,
        thread_id: str | None,
    ) -> Awaitable[str]: ...


def register_manage_tools(mcp: FastMCP, manage: ManageTools, run_data: RunData) -> list[str]:
    """`apm_config`·`apm_environment`·`apm_users`·`apm_active_detail`을 등록하고 이름을 돌려준다."""

    @mcp.tool()
    async def apm_config(
        kind: Annotated[
            str,
            Field(
                description="event_rules(이벤트 룰 — error·metric·compare · 적용 여부) ·"
                " color_boundary(액티브 서비스 색상 경계) · process_instance(PID → 인스턴스) ·"
                " data_server(데이터 서버 도메인 배치·CPU·시스템 속성) · db_path(도메인 DB 경로) ·"
                " loaded_classes(로드된 클래스 — hostname 필수) · rdb_export(수동 RDB Export 상태)"
            ),
        ],
        hostname: Annotated[
            str | None,
            Field(
                description="event_rules·db_path는 그 서버의 도메인만(비우면 전 도메인) ·"
                " loaded_classes는 필수 · process_instance는 원천 조건으로 그대로 보낸다"
            ),
        ] = None,
        source_ids: list[str] | None = None,
        rule_type: Annotated[
            str | None, Field(description="event_rules 룰 종류 error·metric·compare(비우면 셋 다)")
        ] = None,
        target: Annotated[
            str | None,
            Field(description="event_rules 대상 domain·instance·business(compare는 앞 둘)"),
        ] = None,
        error_type: Annotated[
            str | None,
            Field(
                description="ERROR 유형(대문자) — 도메인별 적용 여부와 hostname이 있으면 인스턴스별"
                " 개별 설정도 조회"
            ),
        ] = None,
        process_id: Annotated[
            int | None, Field(description="process_instance의 프로세스 ID(양의 정수 · 필수)")
        ] = None,
        search: Annotated[
            str | None, Field(description="loaded_classes 클래스 이름 일부(비우면 전체)")
        ] = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """APM 설정·관리 조회(읽기 전용) — 이벤트 룰·색상 경계·PID → 인스턴스·데이터 서버·
        DB 경로·로드된 클래스·RDB Export 상태. 자격증명 값은 가려져 온다. 행은 kind마다
        다르다."""
        return await run_data(
            "apm_config",
            f"{kind}:{hostname or '*'}",
            lambda: manage.apm_config(
                kind, hostname, source_ids, rule_type, target, error_type, process_id, search
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_environment(
        hostname: Annotated[
            str | None, Field(description="그 서버 WAS 인스턴스만(비우면 고른 소스 전 도메인)")
        ] = None,
        source_ids: list[str] | None = None,
        scope: Annotated[
            str | None,
            Field(description="SYSTEM(OS 환경변수)·JAVA(JVM 시스템 속성) — 비우면 전부"),
        ] = None,
        key: Annotated[
            str | None, Field(description="이름 일부(대소문자 무시) — 비우면 전부")
        ] = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """WAS 인스턴스의 OS 환경변수·JVM 시스템 속성(JVM 옵션 포함) 전부 — 행 = 인스턴스·
        묶음·이름·값. 비밀번호·토큰 등 비밀 값은 [가림]으로 온다(키 이름은 남는다)."""
        return await run_data(
            "apm_environment",
            hostname or "*",
            lambda: manage.apm_environment(hostname, source_ids, scope, key),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_users(
        user_id: Annotated[
            str | None, Field(description="계정 ID(주면 그 계정 1건 · 비우면 사용자·계정 목록)")
        ] = None,
        source_ids: list[str] | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """APM 사용자 계정 — 목록(origin user_list·accounts) 또는 계정 1건. 비밀번호는 없고
        ID·이름·이메일·휴대폰·허용 IP는 가려져 온다. 계정 문제를 볼 때만 쓴다."""
        return await run_data(
            "apm_users",
            "*",
            lambda: manage.apm_users(user_id, source_ids),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    @mcp.tool()
    async def apm_active_detail(
        domain_id: int,
        txid: int | str,
        session_id: int | None = None,
        thread_hash: int | None = None,
        source_id: str | None = None,
        hostname: Annotated[
            str | None,
            Field(description="주면 domain_id가 그 서버의 정합 도메인인지 확인한다"),
        ] = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        owner: OwnerArg = None,
        wait_seconds: WaitArg = None,
    ) -> str:
        """지금 실행 중인 요청 1건의 상세(현재값 전용) — 사용자 ID(가림)·GUID·SQL(리터럴 가림)·HTTP
        메서드·쿼리(값 가림). source_id·domain_id·txid·session_id·thread_hash는 apm_active_services
        행의 active_ref를 그대로 넘긴다(APM 소스가 둘 이상이면 source_id 필수)."""
        return await run_data(
            "apm_active_detail",
            hostname or "*",
            lambda: manage.apm_active_detail(
                domain_id, txid, session_id, thread_hash, source_id, hostname
            ),
            owner=owner,
            wait_seconds=wait_seconds,
            investigation_id=investigation_id,
            thread_id=thread_id,
        )

    return list(TOOL_NAMES)
