"""사전 스캔 — APM 게이트웨이 스캐너(인벤토리 · 노드 주소) + DPM 슬롯.

plans/210 §3.5 · §3.9 · D-362.

게이트웨이 MCP 도구 계약만 쓴다(`apm_gateway` import 0 · D-274):
- `apm_instance_map(source_ids?)` — 인스턴스 목록. 큰 목록은 결과 파일(`artifact`)로 가므로
  `apm_job_read` 청크로 끝까지 읽는다.
- `apm_node_addresses(source_ids?, domain_ids?)` — 전 도메인 웹 조회(소스 간 병렬 · 소스 안
  순차 · plans/210 W9 — 오래 걸린다 → 작업 핸들 `job` → `apm_job_status` 재확인 → 결과 파일
  청크). 미등록(게이트웨이 플래그 off)이면 FastMCP가
  `Unknown tool`로 답하므로 스캐너 상태 `disabled`로 바꾼다.
- 작업 도구는 같은 `owner`일 때만 답한다 — 스캔은 시스템 주체 `system:apm_host_map_scan`.

스캐너는 예외를 올리지 않고 `ScannerResult`(실패면 `error_kind`)를 돌려준다 — 스캔 서비스가
스캐너별로 격리한다. 행 값·자격증명은 로그에 남기지 않는다(소스·건수·예외 형 이름만).

계층: infrastructure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from noise_gate.domain.apm_host_map import workload_match_key
from noise_gate.domain.apm_host_map_scan import (
    SCANNER_DISABLED,
    SCANNER_FAILED,
    SCANNER_NOT_INTEGRATED,
    SCANNER_OK,
    SCANNER_PARTIAL,
    ScannerResult,
    failed_result,
)
from noise_gate.infrastructure.apm_gateway_client import ApmGatewayClient, ApmGatewayClientError

logger = logging.getLogger(__name__)

INSTANCE_MAP_TOOL = "apm_instance_map"
NODE_ADDRESS_TOOL = "apm_node_addresses"
JOB_STATUS_TOOL = "apm_job_status"
JOB_READ_TOOL = "apm_job_read"
#: 스캔 작업의 게이트웨이 `owner`(작업 도구는 같은 owner에만 답한다).
SCAN_OWNER = "system:apm_host_map_scan"
#: 게이트웨이 작업 상태(SPEC-apm-gateway §3.3).
_LIVE_STATES = frozenset({"queued", "running"})
_READABLE_STATES = frozenset({"completed", "partial"})
#: 미등록 도구 오류의 머리(FastMCP).
_UNKNOWN_TOOL = "Unknown tool"

ERROR_NOT_CONFIGURED = "gateway_not_configured"
ERROR_TOOL_UNREGISTERED = "tool_unregistered"
ERROR_JOB_TIMEOUT = "job_timeout"
ERROR_SHORT_READ = "result_short_read"


class GatewayCaller(Protocol):
    """게이트웨이 도구 1회 호출(반환 dict는 오류 봉투 `{"error": …}`도 그대로)."""

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]: ...


class GatewayToolCaller:
    """`ApmGatewayClient` → `GatewayCaller` 어댑터 — 호출마다 연결하고 끝나면 닫는다.

    클라이언트의 공개 메서드는 도구별(`apm_events`·`apm_instance_map(query)`)이라 임의 도구·인자를
    부르려고 같은 연결 경로(`_call`)를 쓴다. 실패는 `ApmGatewayClientError`로 바꾼다.
    """

    def __init__(self, client: ApmGatewayClient, *, timeout: float = 30.0) -> None:
        """클라이언트와 기본 호출 상한(초)을 받는다."""
        self._client = client
        self._timeout = float(timeout)

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        """도구 1회 — 시간 초과·통신 실패는 `ApmGatewayClientError`."""
        limit = self._timeout if timeout is None else float(timeout)
        try:
            return await asyncio.wait_for(
                self._client._call(tool_name, arguments), timeout=limit  # noqa: SLF001
            )
        except ApmGatewayClientError:
            raise
        except TimeoutError as e:
            raise ApmGatewayClientError(
                f"게이트웨이 호출 타임아웃({tool_name}, {limit:g}초 초과)"
            ) from e
        except asyncio.CancelledError as e:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            raise ApmGatewayClientError(f"게이트웨이 세션 중단({tool_name})") from e
        except Exception as e:  # noqa: BLE001 — 통신 실패를 명확히 노출(침묵 금지)
            raise ApmGatewayClientError(
                f"게이트웨이 호출 실패({tool_name}): {type(e).__name__}: {str(e)[:200]}"
            ) from e


class GatewayReadError(Exception):
    """게이트웨이 읽기 실패 — `kind`는 실행 기록 `error_kind`."""

    def __init__(self, kind: str, detail: str = "") -> None:
        """종류와 (로그용) 상세를 싣는다."""
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


def _error_code(envelope: Mapping[str, Any]) -> str | None:
    err = envelope.get("error")
    if not err:
        return None
    if isinstance(err, Mapping):
        return str(err.get("code") or "gateway_error")
    return str(err)


def _job(envelope: Mapping[str, Any]) -> dict[str, Any]:
    handle = envelope.get("job")
    return dict(handle) if isinstance(handle, Mapping) else {}


async def read_all_rows(
    caller: GatewayCaller,
    tool: str,
    arguments: Mapping[str, Any],
    *,
    wait_seconds: float = 20.0,
    call_timeout: float = 30.0,
    deadline_seconds: float = 1800.0,
    poll_interval: float = 2.0,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """데이터 도구 1회 → (봉투, 행 전부).

    작업 핸들이 오면 `deadline_seconds`까지 `apm_job_status`를 다시 보고, 결과 파일로 간 행
    (`total_row_count` > 실은 행)은 `apm_job_read` 청크로 끝까지 읽는다.

    Raises:
        GatewayReadError: 오류 봉투(`kind` = 게이트웨이 오류 코드) · 미등록 도구 · 작업 시간 초과 ·
            끝까지 못 읽음 · 통신 실패(`ApmGatewayClientError`)
    """
    args = {**dict(arguments), "owner": SCAN_OWNER, "wait_seconds": float(wait_seconds)}
    until = clock() + float(deadline_seconds)
    try:
        env = await caller.call_tool(tool, args, timeout=call_timeout)
        code = _error_code(env)
        if code:
            raise GatewayReadError(code, str(env.get("reason") or "")[:160])
        while _job(env).get("state") in _LIVE_STATES:
            if clock() >= until:
                raise GatewayReadError(ERROR_JOB_TIMEOUT, f"{tool} 작업이 마감 안에 끝나지 않음")
            await sleep(poll_interval)
            env = await caller.call_tool(
                JOB_STATUS_TOOL, {"job_id": _job(env)["job_id"], "owner": SCAN_OWNER},
                timeout=call_timeout,
            )
            code = _error_code(env)
            if code:
                raise GatewayReadError(code, str(env.get("reason") or "")[:160])
        state = _job(env).get("state")
        if state is not None and state not in _READABLE_STATES:
            raise GatewayReadError(f"job_{state}", f"{tool} 작업 상태 {state}")
        meta = env.get("result_meta")
        merged: dict[str, Any] = {**(dict(meta) if isinstance(meta, Mapping) else {}), **env}
        rows = [r for r in env.get("rows") or [] if isinstance(r, Mapping)]
        total = env.get("total_row_count")
        if isinstance(total, int) and not isinstance(total, bool) and total > len(rows):
            rows = await _read_chunks(caller, merged, total, call_timeout)
        return merged, [dict(r) for r in rows]
    except ApmGatewayClientError as e:
        if _UNKNOWN_TOOL in str(e):
            raise GatewayReadError(ERROR_TOOL_UNREGISTERED, tool) from e
        raise GatewayReadError("gateway_unreachable", type(e).__name__) from e


async def _read_chunks(
    caller: GatewayCaller, env: Mapping[str, Any], total: int, call_timeout: float
) -> list[Mapping[str, Any]]:
    """결과 파일 청크를 처음부터 끝까지 읽는다(덜 읽으면 `result_short_read`)."""
    artifact = env.get("artifact")
    ref: Mapping[str, Any] = artifact if isinstance(artifact, Mapping) else {}
    job_id = ref.get("job_id") or _job(env).get("job_id")
    chunks = ref.get("chunks")
    if not job_id or not isinstance(chunks, list) or not chunks:
        raise GatewayReadError(ERROR_SHORT_READ, "결과 파일 정보(작업·청크)가 봉투에 없음")
    out: list[Mapping[str, Any]] = []
    for index in range(len(chunks)):
        part = await caller.call_tool(
            JOB_READ_TOOL, {"job_id": str(job_id), "owner": SCAN_OWNER, "chunk": index},
            timeout=call_timeout,
        )
        code = _error_code(part)
        if code:
            raise GatewayReadError(code, f"청크 {index + 1}/{len(chunks)}")
        out.extend(r for r in part.get("rows") or [] if isinstance(r, Mapping))
    if len(out) < total:
        raise GatewayReadError(ERROR_SHORT_READ, f"{len(out)}/{total}행")
    return out


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _failed_sources(env: Mapping[str, Any]) -> list[str]:
    sources = env.get("sources")
    if not isinstance(sources, list):
        return []
    return [
        _text(s.get("source_id")) for s in sources
        if isinstance(s, Mapping) and _text(s.get("status")) not in ("", "ok")
    ]


def inventory_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """`apm_instance_map` 행 → `apm_inventory` 스냅샷 행.

    노드 주소 칸은 노드 주소 스캐너가 채운다.
    """
    name = _text(row.get("instance_name"))
    return {
        "source_id": _text(row.get("source_id")),
        "domain_id": _text(row.get("domain_id")),
        "domain_name": _text(row.get("domain_name")),
        "domain_description": _text(row.get("domain_description")),
        "instance_id": _text(row.get("instance_id")),
        "instance_name": name,
        "ip": _text(row.get("ip_address") or row.get("ip")),
        "host_name": _text(row.get("host_name")),
        "workload_key": workload_match_key(name) or "",
        "host_address": _text(row.get("host_address")),
        "host_address_status": "",
    }


def instance_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """인스턴스 동일성 `(source_id, domain_id, instance_id)`."""
    return (_text(row.get("source_id")), _text(row.get("domain_id")),
            _text(row.get("instance_id")))


async def scan_apm_inventory(
    caller: GatewayCaller | None,
    *,
    source_ids: Sequence[str] | None = None,
    **read_kwargs: Any,
) -> ScannerResult:
    """APM 인벤토리 스캐너 — `data` = `apm_inventory` 행 목록.

    조회하지 못한 소스(`sources[].status != ok`)는 `unchecked_zones`에 적고 상태 partial이다
    (서비스가 그 소스의 이전 스냅샷 행을 이어 쓴다).
    """
    started = time.monotonic()
    if caller is None:
        return failed_result(ERROR_NOT_CONFIGURED)
    args: dict[str, Any] = {}
    if source_ids:
        args["source_ids"] = list(source_ids)
    try:
        env, rows = await read_all_rows(caller, INSTANCE_MAP_TOOL, args, **read_kwargs)
    except GatewayReadError as e:
        logger.warning("APM·SMS 연계 스캔 — APM 인벤토리 실패: %s", e.kind)
        return failed_result(e.kind, seconds=time.monotonic() - started)
    snapshot = [inventory_row(r) for r in rows if _text(r.get("instance_name"))]
    failed = _failed_sources(env)
    status = SCANNER_PARTIAL if failed or env.get("partial") else SCANNER_OK
    return ScannerResult(
        status, rows=len(snapshot), seconds=time.monotonic() - started,
        unchecked_zones=failed,
        extra={"sources": len({r["source_id"] for r in snapshot}),
               "domains": len({(r["source_id"], r["domain_id"]) for r in snapshot})},
        data=snapshot,
    )


def _node_status(entries: list[dict[str, Any]]) -> str:
    """소스 상태들 → 스캐너 상태.

    전부 disabled = disabled · 전부 ok = ok · ok·partial 섞임 = partial.
    """
    statuses = [e["status"] for e in entries]
    if statuses and all(s == "disabled" for s in statuses):
        return SCANNER_DISABLED
    if all(s == "ok" for s in statuses):
        return SCANNER_OK
    if any(s in ("ok", "partial") for s in statuses):
        return SCANNER_PARTIAL
    return SCANNER_FAILED


async def scan_node_addresses(
    caller: GatewayCaller | None,
    *,
    source_ids: Sequence[str] | None = None,
    **read_kwargs: Any,
) -> ScannerResult:
    """APM 노드 주소 스캐너.

    `data` = `{(source_id, domain_id, instance_id): (host_address, status)}`.

    소스별 상태(`disabled`·`login_failed`·`login_blocked`…)는 기록 `sources`에 그대로 싣는다. 도구가
    없으면(게이트웨이 `JENNIFER_NODE_ADDRESS_ENABLED` off) `disabled`.
    """
    started = time.monotonic()
    if caller is None:
        return failed_result(ERROR_NOT_CONFIGURED)
    args: dict[str, Any] = {}
    if source_ids:
        args["source_ids"] = list(source_ids)
    try:
        env, rows = await read_all_rows(caller, NODE_ADDRESS_TOOL, args, **read_kwargs)
    except GatewayReadError as e:
        seconds = time.monotonic() - started
        if e.kind == ERROR_TOOL_UNREGISTERED:
            return ScannerResult(SCANNER_DISABLED, seconds=seconds, error_kind=e.kind)
        logger.warning("APM·SMS 연계 스캔 — 노드 주소 실패: %s", e.kind)
        return failed_result(e.kind, seconds=seconds)
    found: dict[tuple[str, str, str], tuple[str, str]] = {}
    for row in rows:
        found[instance_key(row)] = (_text(row.get("host_address")), _text(row.get("status")))
    entries = [
        {"source_id": _text(s.get("source_id")), "status": _text(s.get("status")),
         "reason": _text(s.get("reason"))[:200]}
        for s in env.get("node_address_sources") or [] if isinstance(s, Mapping)
    ]
    status = _node_status(entries)
    unchecked = [e["source_id"] for e in entries if e["status"] not in ("ok", "partial")]
    return ScannerResult(
        status, rows=len(found), seconds=time.monotonic() - started, unchecked_zones=unchecked,
        extra={"sources": entries,
               "ok": sum(1 for _, s in found.values() if s == "ok")},
        data=found,
    )


async def scan_dpm() -> ScannerResult:
    """DPM 슬롯 — 미연동 고정(§3.9 · D-341 ⑤)."""
    return ScannerResult(SCANNER_NOT_INTEGRATED)


__all__ = [
    "ERROR_JOB_TIMEOUT",
    "ERROR_NOT_CONFIGURED",
    "ERROR_SHORT_READ",
    "ERROR_TOOL_UNREGISTERED",
    "INSTANCE_MAP_TOOL",
    "JOB_READ_TOOL",
    "JOB_STATUS_TOOL",
    "NODE_ADDRESS_TOOL",
    "SCAN_OWNER",
    "GatewayCaller",
    "GatewayReadError",
    "GatewayToolCaller",
    "instance_key",
    "inventory_row",
    "read_all_rows",
    "scan_apm_inventory",
    "scan_dpm",
    "scan_node_addresses",
]
