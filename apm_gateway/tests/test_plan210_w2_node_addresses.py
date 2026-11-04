"""plans/210 W2 — 노드 주소 일괄 조회 도구 `apm_node_addresses` (§3.6 · D-355 · D-274).

Open API는 합성 다중 소스(`fleet_fake.FakeFleet` — 소스 bank·common × 도메인 4), 웹 목록은 대역
(`FakeLister`) 또는 실측 규칙 가짜 웹(`test_plan202_web_session.FakeWeb`)으로 대신한다 — 실제
제니퍼·외부 네트워크·실 LLM 없음. 스풀은 임시 디렉토리만 쓴다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from apm_gateway.adapters.jennifer.web_fields import parse_upgrade_list
from apm_gateway.adapters.jennifer.web_session import WebLoginFailed, WebRequestError, WebSession
from apm_gateway.application import node_address as na
from apm_gateway.application.node_address import NodeAddressService, build_node_address
from apm_gateway.application.node_address_tools import NodeAddressTools
from apm_gateway.application.sources import build_source_set
from apm_gateway.application.tools import ApmTools
from apm_gateway.interface.server import create_server
from conftest import NOW_S
from fleet_fake import FakeFleet
from mcp.server.fastmcp.exceptions import ToolError
from test_plan202_web_session import COOKIE_ID, COOKIE_PW, JMX_PW, PASSWORD, USER, WEB, FakeWeb

from apm_gateway.config import load_config

TOOL = "apm_node_addresses"
SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "plan166_w0" / "tool_list.json"


def _web_rows(domain: int, sid: str) -> list[dict[str, Any]]:
    """도메인의 인스턴스 2개 중 k=0만 웹 목록에 있다(k=1은 `not_found`) · 파드(ip ≠ 노드)."""
    raw = [
        {
            "instId": domain * 100,
            "sid": domain,
            "hostName": f"{sid}-pod-{domain}",
            "ip": f"10.139.{domain}.5",
            "hostAddress": f"10.136.{domain}.7",
            "status": 3,
            "statusMsg": "",
            "lastLiveTime": 0,
            "jmx": {"passwordOrEmpty": JMX_PW},
        }
    ]
    return parse_upgrade_list(raw)


class FakeLister:
    """웹 세션 대역 — 동시 진입 수를 잰다(순차 조회 확인)."""

    def __init__(self, source_id: str, *, configured: bool = True, domains: int = 4) -> None:
        self.source_id = source_id
        self._configured = configured
        self.rows = {d: _web_rows(d, source_id) for d in range(1, domains + 1)}
        self.calls: list[int] = []
        self.active = 0
        self.max_active = 0
        self.fail: Exception | None = None
        self.fail_domains: set[int] = set()

    @property
    def configured(self) -> bool:
        return self._configured

    async def fetch_list(self, sid: int) -> list[dict[str, Any]]:
        import asyncio

        self.calls.append(sid)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0)
            if self.fail is not None and (not self.fail_domains or sid in self.fail_domains):
                raise self.fail
            return self.rows.get(sid, [])
        finally:
            self.active -= 1

    async def aclose(self) -> None:
        return None


def _fleet_tools(tmp_path: Path, service: NodeAddressService | None, **extra: str) -> ApmTools:
    fake = FakeFleet(4)
    cfg = load_config(fake.env(APM_SPOOL_DIR=str(tmp_path / "spool"), **extra))
    sources = build_source_set(cfg, transport=httpx.MockTransport(fake.handler))
    return ApmTools(sources, cfg, clock=lambda: NOW_S, node_address=service)


def _service(*listers: FakeLister) -> NodeAddressService:
    return NodeAddressService({x.source_id: x for x in listers}, clock=lambda: NOW_S)


async def _tool_list(mcp) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
        for t in await mcp.list_tools()
    ]


# ── 등록: 플래그 off = 도구 목록 비트 동일 · on = 1종 추가 ─────────────────────


async def test_flag_off_tool_list_is_byte_identical_to_snapshot(tmp_path):
    cfg = load_config(FakeFleet(4).env(APM_SPOOL_DIR=str(tmp_path / "spool")))
    assert cfg.node_address.enabled is False and build_node_address(cfg) is None
    off = create_server(_fleet_tools(tmp_path, None))
    listed = await _tool_list(off)
    assert TOOL not in {t["name"] for t in listed}
    assert listed == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


async def test_flag_off_call_is_unknown_tool(tmp_path):
    """본체 `apm_query`가 읽는 계약 — 미등록 도구 호출은 FastMCP `Unknown tool: <이름>` 오류."""
    off = create_server(_fleet_tools(tmp_path, None))
    with pytest.raises(ToolError, match=f"Unknown tool: {TOOL}"):
        await off.call_tool(TOOL, {})


async def test_flag_on_registers_only_the_new_tool_before_job_tools(tmp_path):
    cfg = load_config(
        FakeFleet(4).env(APM_SPOOL_DIR=str(tmp_path / "s"), JENNIFER_NODE_ADDRESS_ENABLED="true")
    )
    service = build_node_address(cfg)
    assert service is not None
    on = await _tool_list(create_server(_fleet_tools(tmp_path, service)))
    off = await _tool_list(create_server(_fleet_tools(tmp_path, None)))
    assert [t for t in on if t["name"] != TOOL] == off  # 나머지 도구는 그대로
    names = [t["name"] for t in on]
    assert names.index(TOOL) == names.index("apm_job_status") - 1
    schema = next(t for t in on if t["name"] == TOOL)["inputSchema"]
    assert set(schema["properties"]) == {
        "source_ids",
        "domain_ids",
        "instance_ids",  # plans/210 W8 A — 가산 인자
        "investigation_id",
        "thread_id",
        "owner",
        "wait_seconds",
    }
    assert not schema.get("required")
    await service.aclose()


# ── 조회: 전 도메인 순차 · 도메인 수 상한 없음 · 행 모양 ─────────────────────────


async def test_all_domains_sequential_without_domain_limit(tmp_path):
    bank, common = FakeLister("bank"), FakeLister("common")
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank, common)))
    out = await tools.apm_node_addresses()
    assert not hasattr(na, "MCP_FETCH_DOMAIN_LIMIT")  # plans/210 W8 B — 옛 상한 3 폐지
    assert bank.calls == [1, 2, 3, 4] and common.calls == [1, 2, 3, 4]
    assert bank.max_active == 1 and common.max_active == 1
    rows = out["rows"]
    assert len(rows) == 2 * 4 * 2
    assert set(rows[0]) == {
        "source_id",
        "domain_id",
        "instance_id",
        "instance_name",
        "ip",
        "host_address",
        "pod",
        "status",
    }
    first = next(r for r in rows if r["source_id"] == "bank" and r["instance_id"] == 200)
    assert first == {
        "source_id": "bank",
        "domain_id": 2,
        "instance_id": 200,
        "instance_name": "bank-i2-0",
        "ip": "10.0.0.1",  # Open API 인스턴스 IP(인벤토리) 우선
        "host_address": "10.136.2.7",
        "pod": True,
        "status": "ok",
    }
    missing = next(r for r in rows if r["source_id"] == "bank" and r["instance_id"] == 201)
    # 웹 목록에 없는 인스턴스 — 도메인 종류(pod)는 기억값으로 싣는다
    assert missing["status"] == "not_found" and missing["host_address"] == ""
    assert missing["pod"] is True
    assert [s["status"] for s in out["node_address_sources"]] == ["ok", "ok"]
    assert out["node_address_sources"][0]["instance_status"] == {"ok": 4, "not_found": 4}
    assert not out.get("partial")


async def test_domain_ids_narrow_and_unknown_domain_is_limit(tmp_path):
    bank = FakeLister("bank")
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank)))
    out = await tools.apm_node_addresses(source_ids=["bank"], domain_ids=[3, 99])
    assert bank.calls == [3]
    assert {r["domain_id"] for r in out["rows"]} == {3}
    assert any("도메인 99" in x for x in out["limits"])


async def test_domains_per_call_capped_with_limit(tmp_path, monkeypatch):
    """보안 M-2 — 호출당 (소스, 도메인) 상한을 넘으면 앞에서부터 상한까지만 웹 조회하고 `[한계]`."""
    from apm_gateway.application import node_address_tools as nat

    assert nat.MAX_DOMAINS_PER_CALL == 50
    monkeypatch.setattr(nat, "MAX_DOMAINS_PER_CALL", 5)
    bank, common = FakeLister("bank"), FakeLister("common")
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank, common)))
    out = await tools.apm_node_addresses()
    assert bank.calls == [1, 2, 3, 4] and common.calls == [1]
    assert {(r["source_id"], r["domain_id"]) for r in out["rows"]} == {
        ("bank", 1), ("bank", 2), ("bank", 3), ("bank", 4), ("common", 1)
    }
    assert out["partial"] is True
    assert any("8개 중 5개만 조회" in x and "domain_ids" in x for x in out["limits"])


async def test_domains_within_cap_has_no_cap_limit(tmp_path, monkeypatch):
    from apm_gateway.application import node_address_tools as nat

    monkeypatch.setattr(nat, "MAX_DOMAINS_PER_CALL", 8)
    bank, common = FakeLister("bank"), FakeLister("common")
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank, common)))
    out = await tools.apm_node_addresses()
    assert len(bank.calls) + len(common.calls) == 8
    assert not out.get("partial")
    assert not any("개만 조회" in x for x in out.get("limits") or [])


async def test_cache_within_ttl_is_reused(tmp_path):
    bank = FakeLister("bank")
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank)))
    await tools.apm_node_addresses(source_ids=["bank"])
    await tools.apm_node_addresses(source_ids=["bank"])
    assert bank.calls == [1, 2, 3, 4]  # TTL 안 캐시 — 두 번째는 웹 호출 0


# ── 소스 상태 · 사유(침묵 금지) ─────────────────────────────────────────────


async def test_unconfigured_source_is_disabled_with_reason(tmp_path):
    bank, common = FakeLister("bank"), FakeLister("common", configured=False)
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank, common)))
    out = await tools.apm_node_addresses()
    assert common.calls == []
    by_src = {s["source_id"]: s for s in out["node_address_sources"]}
    assert by_src["common"]["status"] == "disabled"
    assert "WEB_USER" in by_src["common"]["reason"]
    assert {r["status"] for r in out["rows"] if r["source_id"] == "common"} == {"disabled"}
    assert out["partial"] is True
    assert any("common" in x and "disabled" in x for x in out["limits"])


async def test_login_failures_block_and_source_reports_login_blocked(tmp_path):
    bank = FakeLister("bank")
    bank.fail = WebLoginFailed("로그인 거부")
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank)))
    out = await tools.apm_node_addresses(source_ids=["bank"])
    # 3회 실패 → 정지(네 번째 도메인은 네트워크 0)
    assert bank.calls == [1, 2, 3]
    src = out["node_address_sources"][0]
    assert src["status"] == "login_blocked"
    assert [d["status"] for d in src["failed_domains"]] == [
        "login_failed",
        "login_failed",
        "login_failed",
        "login_blocked",
    ]
    assert {r["status"] for r in out["rows"]} == {"login_failed", "login_blocked"}
    assert all(r["host_address"] == "" for r in out["rows"])


async def test_some_domains_failed_is_partial(tmp_path):
    bank = FakeLister("bank")
    bank.fail = WebRequestError("가짜 실패")
    bank.fail_domains = {2}
    tools = NodeAddressTools(_fleet_tools(tmp_path, _service(bank)))
    out = await tools.apm_node_addresses(source_ids=["bank"])
    src = out["node_address_sources"][0]
    assert src["status"] == "partial" and src["failed_domains"] == [
        {"domain_id": 2, "status": "error"}
    ]
    assert "1/4" in src["reason"] and out["partial"] is True


async def test_unexpected_exception_is_error_and_scan_continues(tmp_path, monkeypatch):
    bank = FakeLister("bank")
    service = _service(bank)
    real = service.domain_snapshot

    async def flaky(source_id, domain_id, *, fetch):
        if domain_id == 1:
            raise RuntimeError("boom")
        return await real(source_id, domain_id, fetch=fetch)

    monkeypatch.setattr(service, "domain_snapshot", flaky)
    out = await NodeAddressTools(_fleet_tools(tmp_path, service)).apm_node_addresses(
        source_ids=["bank"]
    )
    assert bank.calls == [2, 3, 4]
    assert out["node_address_sources"][0]["failed_domains"] == [{"domain_id": 1, "status": "error"}]


async def test_core_without_service_is_not_configured(tmp_path):
    from apm_gateway.domain.errors import ApmError

    with pytest.raises(ApmError) as e:
        await NodeAddressTools(_fleet_tools(tmp_path, None)).apm_node_addresses()
    assert "JENNIFER_NODE_ADDRESS_ENABLED" in e.value.reason


# ── MCP 표면: 결과 파일·청크 · 비밀번호·쿠키 무노출 ────────────────────────────


def _text(out: Any) -> str:
    content = out[0] if isinstance(out, tuple) else out
    return content[0].text


async def test_large_result_goes_to_result_file_and_chunks(tmp_path):
    bank, common = FakeLister("bank"), FakeLister("common")
    tools = _fleet_tools(tmp_path, _service(bank, common), APM_INLINE_ROWS="3",
                         APM_ARTIFACT_CHUNK_ROWS="5")
    mcp = create_server(tools)
    env = json.loads(_text(await mcp.call_tool(TOOL, {"owner": "o1"})))
    assert env["row_count"] == 3 and env["total_row_count"] == 16
    assert env["artifact"]["total_rows"] == 16 and len(env["artifact"]["chunks"]) == 4
    assert [s["status"] for s in env["node_address_sources"]] == ["ok", "ok"]
    chunk = json.loads(
        _text(await mcp.call_tool("apm_job_read", {"job_id": env["job"]["job_id"],
                                                   "owner": "o1", "chunk": 1}))
    )
    assert len(chunk["rows"]) == 5 and all("host_address" in r for r in chunk["rows"])
    assert (tmp_path / "spool").exists()


async def test_real_web_session_leaks_no_password_or_cookie(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    fake = FakeWeb()
    fake.rows = {d: [_raw(d)] for d in range(1, 5)}
    session = WebSession(
        source_id="bank", url=WEB, user=USER, password=PASSWORD,
        transport=httpx.MockTransport(fake),
    )
    service = NodeAddressService({"bank": session}, clock=lambda: NOW_S)
    mcp = create_server(_fleet_tools(tmp_path, service))
    text = _text(await mcp.call_tool(TOOL, {"source_ids": ["bank"]}))
    env = json.loads(text)
    assert {r["status"] for r in env["rows"]} == {"ok", "not_found"}
    assert {r["host_address"] for r in env["rows"] if r["status"] == "ok"} == {
        f"10.136.{d}.7" for d in range(1, 5)
    }
    for secret in (PASSWORD, COOKIE_ID, COOKIE_PW, JMX_PW):
        assert secret not in text
        assert secret not in caplog.text
    await service.aclose()


def _raw(domain: int) -> dict[str, Any]:
    return {
        "instId": domain * 100,
        "sid": domain,
        "hostName": f"pod-{domain}",
        "ip": f"10.139.{domain}.5",
        "hostAddress": f"10.136.{domain}.7",
        "status": 3,
        "statusMsg": "",
        "lastLiveTime": 0,
        "jmx": {"idOrEmpty": "jmxuser", "passwordOrEmpty": JMX_PW},
    }
