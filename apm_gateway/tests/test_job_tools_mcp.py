"""MCP 작업 도구 · 주체 토큰 · `owner`/`wait_seconds` (plans/134 W0-B · SPEC-apm-question-coverage
§2.1·§3.6·§3.7).

- 데이터 도구에 `wait_seconds`를 주면 오래 걸리는 조회가 작업 핸들로 바뀌고, 작업 도구 3종으로 상태·
  취소·결과를 받는다. `wait_seconds`가 없으면(조사·알람 소비자) 끝까지 기다린다 — 종전 의미 고정.
- 실 SSE 전송(127.0.0.1 임시 포트 uvicorn)으로 주체 토큰이 작업 소유 판정까지 이어지는지 본다.
외부 네트워크 0(MockTransport · 루프백).
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket

import httpx
import pytest
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.spool import Spool
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import make_tools, synthetic_handler

from apm_gateway.config import JobConfig


def _json(result) -> dict:
    blocks = result[0] if isinstance(result, tuple) else result
    return json.loads(blocks[0].text)


def _server(tmp_path, handler, **job_cfg):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    cfg = JobConfig(spool_dir=tmp_path / "spool", **job_cfg)
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), tools, jobs


async def _wait_done(mcp, job_id: str, owner: str | None = None) -> dict:
    args = {"job_id": job_id, **({"owner": owner} if owner else {})}
    for _ in range(300):
        status = _json(await mcp.call_tool("apm_job_status", args))
        if status.get("job", {}).get("state") not in ("queued", "running"):
            return status
        await asyncio.sleep(0.01)
    raise AssertionError("작업이 끝나지 않았다")


@pytest.mark.asyncio
async def test_wait_seconds_hands_off_then_job_tools_complete(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="apm_gateway.audit")
    gate = asyncio.Event()
    mcp, _, _ = _server(
        tmp_path, synthetic_handler(gate=gate, gate_paths=("/api/transaction/time",))
    )
    out = _json(
        await mcp.call_tool(
            "apm_slow_transactions",
            {"hostname": "was-host01", "lookback_minutes": 3, "wait_seconds": 0.05, "owner": "u:1"},
        )
    )
    job = out["job"]
    assert out["rows"] == [] and out["row_count"] == 0 and job["state"] == "running"
    assert job["progress"]["total"] == 3 and job["estimate"]["api_calls"] == 3  # 1분 조각 3개
    handoff = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"][-1]
    assert f"job_id={job['job_id']}" in handoff and "principal=anonymous" in handoff

    status = _json(await mcp.call_tool("apm_job_status", {"job_id": job["job_id"], "owner": "u:1"}))
    assert status["job"]["state"] == "running" and status["tool"] == "apm_job_status"
    early = _json(await mcp.call_tool("apm_job_read", {"job_id": job["job_id"], "owner": "u:1"}))
    assert early["error"] == "job_not_ready"
    other = _json(await mcp.call_tool("apm_job_status", {"job_id": job["job_id"], "owner": "u:2"}))
    assert other["error"] == "job_not_found"

    gate.set()
    done = await _wait_done(mcp, job["job_id"], "u:1")
    assert done["job"]["state"] == "completed" and done["job"]["progress"]["done"] == 3
    assert done["result_meta"]["summary"]["calls"] == 6 and done["total_row_count"] == 6
    read = _json(await mcp.call_tool("apm_job_read", {"job_id": job["job_id"], "owner": "u:1"}))
    # 결과 파일(청크)에는 「전체 파일 전용」 칸(instance_oid)이 있고 미리보기 행에는 없다
    # (W1 · COV 표 C)
    assert all("instance_oid" in r for r in read["rows"])
    assert all("instance_oid" not in r for r in done["rows"])
    stripped = [{k: v for k, v in r.items() if k != "instance_oid"} for r in read["rows"]]
    assert stripped == done["rows"] and read["chunk"] == 0
    assert read["artifact"]["file_only_columns"] == ["instance_oid"]
    lines = [r.getMessage() for r in caplog.records if r.name == "apm_gateway.audit"]
    # 백그라운드 종료 감사 — 작업 수명 전체 호출(X-View 3 + 이 요청이 시작한 명단 적재 2)
    finished = [x for x in lines if "tool=apm_slow_transactions" in x and "api_calls=5" in x]
    assert finished and f"job_id={job['job_id']}" in finished[-1]
    assert "sources=default:5" in finished[-1]
    assert any("tool=apm_job_read" in x and "api_calls=0" in x for x in lines)


@pytest.mark.asyncio
async def test_without_wait_seconds_legacy_consumer_gets_full_result(tmp_path):
    """noise_gate·sre_agent가 보내는 인자 그대로(wait_seconds 없음) — 느려도 끝까지 기다린다."""
    mcp, _, jobs = _server(tmp_path, synthetic_handler(delay=0.03))
    started = asyncio.get_running_loop().time()
    out = _json(
        await mcp.call_tool(
            "apm_events",
            {
                "hostname": "was-host01",
                "reference_time": "2026-09-29T10:00:00",
                "lookback_minutes": 30,
                "level": "warning",
                "investigation_id": "inv-1",
            },
        )
    )
    assert asyncio.get_running_loop().time() - started >= 0.06  # 응답 2회 이상 기다렸다
    assert "error" not in out and "job" not in out and out["row_count"] == 1
    assert out["total_row_count"] == 1
    assert not any(p.is_dir() for p in (tmp_path / "spool").glob("*"))  # 디스크 0


@pytest.mark.asyncio
async def test_cancel_through_tool_and_bad_arguments(tmp_path):
    gate = asyncio.Event()
    mcp, _, _ = _server(
        tmp_path, synthetic_handler(gate=gate, gate_paths=("/api/dbsearch/event",))
    )
    out = _json(await mcp.call_tool("apm_events", {"hostname": "was-host01", "wait_seconds": 0}))
    job_id = out["job"]["job_id"]
    await asyncio.sleep(0.02)
    cancelled = _json(await mcp.call_tool("apm_job_cancel", {"job_id": job_id}))
    assert cancelled["job"]["state"] == "cancelled"
    assert _json(await mcp.call_tool("apm_job_read", {"job_id": job_id}))["error"] == (
        "invalid_argument"
    )
    bad = _json(await mcp.call_tool("apm_events", {"hostname": "was-host01", "wait_seconds": -1}))
    assert bad["error"] == "invalid_argument"
    nope = _json(await mcp.call_tool("apm_job_status", {"job_id": "../../x"}))
    assert nope["error"] == "job_not_found"
    gate.set()


@pytest.mark.asyncio
async def test_gateway_health_reports_job_summary(tmp_path):
    mcp, _, _ = _server(tmp_path, synthetic_handler(), max_concurrent=2)
    out = _json(await mcp.call_tool("gateway_health", {}))
    assert out["jobs"] == {"running": 0, "queued": 0, "slots_in_use": 0, "max_concurrent": 2}
    # 16 + plans/134 W5 `/api/transaction/guid` 1(W7 경로는 별도 — 병합 시 합산)
    assert out["rows"][0]["allowlist_size"] == 37  # 16 + W5 guid 1 + W7 19(plans/134) + 업무 1(130)


# ── 실 SSE 전송 — 주체 토큰 ─────────────────────────────────


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_sse_principal_tokens_scope_job_access(tmp_path):
    import uvicorn
    from apm_gateway.interface.server import build_asgi_app
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    gate = asyncio.Event()
    mcp, _, jobs = _server(
        tmp_path, synthetic_handler(gate=gate, gate_paths=("/api/dbsearch/event",))
    )
    app = build_asgi_app(mcp, {"chat": "tok-chat-1", "investigation": "tok-inv-2"})
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    url = f"http://127.0.0.1:{port}/sse"

    async def call(token: str, name: str, args: dict) -> dict:
        headers = {"Authorization": f"Bearer {token}"}
        async with sse_client(url, headers=headers) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return _json((await session.call_tool(name, args)).content)

    try:
        handle = await call(
            "tok-chat-1", "apm_events", {"hostname": "was-host01", "wait_seconds": 0, "owner": "o"}
        )
        job_id = handle["job"]["job_id"]
        record = json.loads((tmp_path / "spool" / job_id / "job.json").read_text("utf-8"))
        assert record["principal"] == "chat" and record["owner"] == "o"
        assert "tok-chat-1" not in json.dumps(record)
        mine = await call("tok-chat-1", "apm_job_status", {"job_id": job_id, "owner": "o"})
        theirs = await call("tok-inv-2", "apm_job_status", {"job_id": job_id, "owner": "o"})
        assert mine["job"]["job_id"] == job_id and theirs["error"] == "job_not_found"
        async with httpx.AsyncClient() as client:
            denied = await client.get(url, headers={"Authorization": "Bearer nope"})
        assert denied.status_code == 401
    finally:
        gate.set()
        server.should_exit = True
        await serving
        await jobs.aclose()
