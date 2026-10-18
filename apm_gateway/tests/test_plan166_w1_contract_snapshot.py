"""plans/166 W1 M6 — 소비자 계약 스냅샷(정본 쪽 대조).

게이트웨이와 소비자(`src`·`noise_gate`·`sre_agent`)는 서로 import하지 않는다(D-139·D-274 —
`test_boundary.py`). 그래서 공유 라이브러리 대신 JSON 스냅샷 한 벌
(`snapshots/plan166_w1/apm_contract.json`)을 계약의 단일 출처로 두고 양쪽이 같은 파일을 대조한다.
여기서는 **정본**(`domain/errors.py` 오류 코드 · `ApmTools.ok`·`err` 봉투 · 등록된 작업 도구)이
스냅샷과 같은지 본다. 소비측 대조는 루트 `tests/test_plan166_w1_apm_contract_consumers.py`.

스냅샷이 깨지면 계약 변경이다 — 스냅샷을 고치기 전에 소비측 사본과 `spec/SPEC-apm-gateway.md`
§3.2·§3.6을 함께 고친다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.application.tools import ApmTools
from apm_gateway.domain import errors
from apm_gateway.interface.server import JOB_TOOLS, create_server, register_tools
from conftest import make_tools, synthetic_handler
from mcp.server.fastmcp import FastMCP

SNAPSHOT = Path(__file__).parent / "snapshots" / "plan166_w1" / "apm_contract.json"
CONTRACT: dict[str, Any] = json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _tools(tmp_path: Path) -> ApmTools:
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        extra={"APM_SPOOL_DIR": str(tmp_path / "spool")},
    )
    return tools


def test_error_codes_match_snapshot() -> None:
    assert sorted(errors.ERROR_CODES) == CONTRACT["error_codes"]
    # 모듈 상수가 빠짐없이 ERROR_CODES에 들어 있다(상수만 늘리고 집합을 빠뜨리지 않았다)
    constants = {
        v for k, v in vars(errors).items() if k.isupper() and isinstance(v, str)
    }
    assert constants == errors.ERROR_CODES


def test_error_envelope_matches_snapshot() -> None:
    env = ApmTools.err("apm_events", errors.SOURCE_UNAVAILABLE, "사유")
    assert sorted(env) == CONTRACT["error_envelope_keys"]
    assert env["source_kind"] == CONTRACT["source_kind"]
    assert env["source"] == CONTRACT["source"]


def test_ok_envelope_required_keys_match_snapshot(tmp_path: Path) -> None:
    env = _tools(tmp_path).ok("apm_events", [])
    assert sorted(env) == CONTRACT["ok_envelope_required_keys"]
    assert env["source_kind"] == CONTRACT["source_kind"]
    assert env["source"] == CONTRACT["source"]


@pytest.mark.asyncio
async def test_job_tools_match_registered_tools(tmp_path: Path) -> None:
    """수작업 `JOB_TOOLS` ↔ 실제 등록된 작업 도구 · 수작업 반환 목록 ↔ 등록 도구 전체."""
    tools = _tools(tmp_path)
    registered = {t.name for t in await create_server(tools).list_tools()}
    assert sorted(JOB_TOOLS) == CONTRACT["job_tools"]
    assert {n for n in registered if n.startswith("apm_job_")} == set(JOB_TOOLS)
    names = register_tools(FastMCP("x"), tools)
    assert len(names) == len(set(names))
    assert set(names) == registered
