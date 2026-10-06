"""plans/134 W5 — 프로파일 확장(`profile_no`·`include_param_key` · `key` 미전달) · 오류 행
`profile_ref.profile_no` · 프로파일 예산 채팅/조사 분리(SPEC-coverage §0 ⑨ · D-296 ④).

예산 주체는 전송 토큰으로만 정해진다 — 코어 테스트는 `principal`을 직접 주고, 실 SSE 전송 테스트가
토큰 → 주체 → 예산 칸을 끝까지 본다. 외부 네트워크 0(목 서버 · MockTransport · 루프백).
"""

from __future__ import annotations

import asyncio
import json
import socket
import urllib.request

import httpx
import pytest
from apm_gateway.adapters.jennifer.allowlist import NotAllowedError, check_request
from apm_gateway.application.jobs import JobManager
from apm_gateway.application.spool import Spool
from apm_gateway.application.tools import CHAT_PRINCIPAL, KEY_ARG_NOTE
from apm_gateway.domain.errors import INVALID_ARGUMENT, RATE_LIMITED, ApmError
from apm_gateway.interface.server import audit_job_finished, create_server
from conftest import DOMAIN, NOW_MS, make_tools, synthetic_handler

from apm_gateway.config import JobConfig

_TX = {"domain_id": "1", "txid": "2", "time": "3"}


def _hits(base: str) -> list[dict]:
    with urllib.request.urlopen(f"{base}/__mock/hits", timeout=5) as r:
        return json.loads(r.read())


@pytest.fixture
def synth(mock_server_factory, synthetic_dir):
    base, _ = mock_server_factory(synthetic_dir, "connected")
    tools, _ = make_tools(base)
    return base, tools


async def _profile(tools, **kw):
    return await tools.apm_transaction_profile("was-host01", DOMAIN, "9000", NOW_MS, **kw)


# ── 허용목록 (N-8) ─────────────────────────────────────────


def test_txid_key_and_sql_optional_keys_allowed():
    # T-COV-TX-TXID-arg-key · T-COV-TX-SQL-arg-{profile_no·key·include_param_key}
    assert check_request("GET", "/api/transaction/txid", {**_TX, "key": "k"})
    sql = {**_TX, "profile_no": "3", "key": "k", "include_param_key": "true"}
    assert check_request("GET", "/api/transaction/sql", sql)
    for path in ("/api/transaction/txid", "/api/transaction/sql"):
        for extra in ({"time_pattern": "yyyyMMdd"}, {"token": "x"}):
            with pytest.raises(NotAllowedError):
                check_request("GET", path, {**_TX, **extra})
    with pytest.raises(NotAllowedError):  # sql 전용 키는 txid에 없다
        check_request("GET", "/api/transaction/txid", {**_TX, "profile_no": "1"})


# ── 프로파일 인자 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_no_and_include_param_key_go_to_sql_only_and_key_is_never_sent(synth):
    base, tools = synth
    out = await _profile(tools, profile_no=14, include_param_key=True)
    hits = {h["template"]: h["query"] for h in _hits(base)}
    assert hits["/api/transaction/sql"] == {
        "domain_id": str(DOMAIN),
        "txid": "9000",
        "time": str(NOW_MS),
        "profile_no": "14",
        "include_param_key": "true",
    }
    assert hits["/api/transaction/txid"] == {
        "domain_id": str(DOMAIN),
        "txid": "9000",
        "time": str(NOW_MS),
    }
    assert "key" not in hits["/api/transaction/profile.txt"]
    assert KEY_ARG_NOTE in out["limits"]
    assert any("include_param_key 응답 모양은 미공개" in x for x in out["limits"])
    assert out["rows"][0]["profile_no"] == 14
    assert out["rows"][0]["sqls"] == ["select * from orders where id = ?"]
    # 주지 않으면 싣지 않는다 · false도 그대로 넘긴다
    await _profile(tools, include_param_key=False)
    last = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/sql"][-1]
    assert "profile_no" not in last and last["include_param_key"] == "false"
    await _profile(tools)
    last = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/sql"][-1]
    assert set(last) == {"domain_id", "txid", "time"}


@pytest.mark.asyncio
async def test_error_row_profile_ref_carries_profile_no_to_profile(synth):
    base, tools = synth
    errors = await tools.apm_events("was-host01", record="error")
    ref = errors["rows"][0]["profile_ref"]
    assert ref["profile_no"] == 14
    # 본체는 행의 profile_ref를 그대로 넘긴다
    await tools.apm_transaction_profile("was-host01", **ref)
    last = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/sql"][-1]
    assert last["profile_no"] == "14"
    # 이벤트·느린 거래 행의 profile_ref에는 profile_no가 없다(프로파일 번호를 모른다)
    slow = await tools.apm_slow_transactions("was-host01", n=1)
    assert "profile_no" not in slow["rows"][0]["profile_ref"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        {"profile_no": -1},
        {"profile_no": "abc"},
        {"profile_no": True},
        {"profile_no": 1.5},
        {"include_param_key": "yes"},
        {"include_param_key": 1},
    ],
)
async def test_profile_argument_validation(synth, kwargs):
    base, tools = synth
    before = len(_hits(base))
    with pytest.raises(ApmError) as exc:
        await _profile(tools, **kwargs)
    assert exc.value.code == INVALID_ARGUMENT
    assert len(_hits(base)) == before  # HTTP 0회


@pytest.mark.asyncio
async def test_profile_no_zero_is_a_valid_profile_number(synth):
    base, tools = synth
    await _profile(tools, profile_no=0)
    last = [h["query"] for h in _hits(base) if h["template"] == "/api/transaction/sql"][-1]
    assert last["profile_no"] == "0"


# ── 프로파일 예산(채팅/조사 분리) — 필수 6종 ─────────────────


def _budget_tools():
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(synthetic_handler()))
    return tools


async def _limited(tools, **kw) -> bool:
    try:
        await _profile(tools, **kw)
    except ApmError as e:
        assert e.code == RATE_LIMITED
        return True
    return False


@pytest.mark.asyncio
async def test_budget_chat_principal_is_exempt():
    tools = _budget_tools()
    for _ in range(12):
        assert not await _limited(tools, principal=CHAT_PRINCIPAL, investigation_id="inv-1")
    for _ in range(6):  # investigation_id·owner가 없어도 같다
        assert not await _limited(tools, principal=CHAT_PRINCIPAL)


@pytest.mark.asyncio
async def test_budget_investigation_sixth_call_is_rate_limited():
    tools = _budget_tools()
    for _ in range(5):
        assert not await _limited(tools, principal="investigation", investigation_id="inv-1")
    with pytest.raises(ApmError) as exc:
        await _profile(tools, principal="investigation", investigation_id="inv-1")
    assert exc.value.code == RATE_LIMITED
    assert "주체 investigation" in exc.value.reason and "investigation_id=inv-1" in exc.value.reason


@pytest.mark.asyncio
async def test_budget_isolates_users_by_owner():
    """단일 토큰(default) 배포 — 면제는 없지만 사용자(owner)끼리는 격리된다."""
    tools = _budget_tools()
    for _ in range(5):
        assert not await _limited(tools, principal="default", owner="user:a")
    assert await _limited(tools, principal="default", owner="user:a")
    for _ in range(5):
        assert not await _limited(tools, principal="default", owner="user:b")
    assert await _limited(tools, principal="default", owner="user:b")


@pytest.mark.asyncio
async def test_budget_isolates_investigations():
    tools = _budget_tools()
    for _ in range(5):
        assert not await _limited(tools, principal="investigation", investigation_id="inv-x")
    assert await _limited(tools, principal="investigation", investigation_id="inv-x")
    assert not await _limited(tools, principal="investigation", investigation_id="inv-y")


@pytest.mark.asyncio
async def test_budget_unspecified_cell_is_per_principal():
    """`investigation_id`·`owner`를 빼면 그 주체의 `_unspecified` 칸 — 다른 주체와 나누지 않는다
    (종전 `_anonymous` 전 주체 공유 폐지)."""
    tools = _budget_tools()
    for _ in range(5):
        assert not await _limited(tools, principal="investigation")
    assert await _limited(tools, principal="investigation")
    assert await _limited(tools, principal="investigation", investigation_id="  ")  # 공백 = 없음
    for principal in ("alarm", "default", "anonymous"):
        assert not await _limited(tools, principal=principal)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forged",
    [
        {"owner": "chat"},
        {"investigation_id": "chat"},
        {"owner": "chat", "investigation_id": "chat"},
        {"owner": "principal:chat"},
    ],
)
async def test_budget_exemption_cannot_be_forged_by_arguments(forged):
    tools = _budget_tools()
    for _ in range(5):
        assert not await _limited(tools, principal="investigation", **forged)
    assert await _limited(tools, principal="investigation", **forged)


@pytest.mark.asyncio
async def test_budget_cell_expires_after_ttl():
    now = [1_790_643_600.0]
    tools, _ = make_tools(
        "http://apm.test",
        transport=httpx.MockTransport(synthetic_handler()),
        clock=lambda: now[0],
    )
    for _ in range(5):
        await _profile(tools, principal="investigation", investigation_id="inv-t")
    assert await _limited(tools, principal="investigation", investigation_id="inv-t")
    now[0] += 3601
    assert not await _limited(tools, principal="investigation", investigation_id="inv-t")


# ── MCP 표면 · 실 SSE 전송(토큰 → 주체 → 예산) ─────────────────


def _server(tmp_path):
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(synthetic_handler()))
    cfg = JobConfig(spool_dir=tmp_path / "spool")
    jobs = JobManager(
        Spool(cfg.spool_dir),
        cfg,
        envelope=tools.ok,
        error_envelope=tools.err,
        on_finish=audit_job_finished,
    )
    return create_server(tools, jobs=jobs), jobs


@pytest.mark.asyncio
async def test_profile_tool_schema_has_new_args_but_no_principal(tmp_path):
    mcp, jobs = _server(tmp_path)
    tool = next(t for t in await mcp.list_tools() if t.name == "apm_transaction_profile")
    props = tool.inputSchema["properties"]
    assert {"profile_no", "include_param_key", "owner", "investigation_id"} <= set(props)
    assert "principal" not in props
    await jobs.aclose()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.asyncio
async def test_sse_chat_token_is_exempt_and_investigation_token_is_limited(tmp_path):
    import uvicorn
    from apm_gateway.interface.server import build_asgi_app
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    mcp, jobs = _server(tmp_path)
    app = build_asgi_app(mcp, {"chat": "tok-chat-1", "investigation": "tok-inv-2"})
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    serving = asyncio.create_task(server.serve())
    for _ in range(200):
        if server.started:
            break
        await asyncio.sleep(0.01)
    url = f"http://127.0.0.1:{port}/sse"
    args = {
        "hostname": "was-host01",
        "domain_id": DOMAIN,
        "txid": "9000",
        "time_ms": NOW_MS,
        "investigation_id": "inv-1",
        "owner": "chat",
    }

    async def calls(token: str, count: int, extra: dict | None = None) -> list[dict]:
        out = []
        async with sse_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                for _ in range(count):
                    result = await session.call_tool(
                        "apm_transaction_profile", {**args, **(extra or {})}
                    )
                    out.append(json.loads(result.content[0].text))
        return out

    try:
        chat = await calls("tok-chat-1", 6)
        assert all("error" not in r for r in chat)
        inv = await calls("tok-inv-2", 6)
        assert all("error" not in r for r in inv[:5])
        assert inv[5]["error"] == RATE_LIMITED
        # 인자로 주체를 흉내 내도 면제되지 않는다(스키마 밖 인자는 쓰이지 않는다)
        forged = await calls("tok-inv-2", 1, {"principal": "chat"})
        assert forged[0].get("error") == RATE_LIMITED, forged
    finally:
        server.should_exit = True
        await serving
        await jobs.aclose()


@pytest.mark.asyncio
async def test_profile_sql_with_param_keys_carries_no_credentials():
    """`include_param_key` 응답 모양은 미공개(W10) — 비밀 키 값·SQL 리터럴이 새지 않는다."""
    canary = "hunter2-CANARY"
    body = {
        "result": [
            {
                "sql": f"select * from users where pw = '{canary}' and id = 7",
                "password": canary,
                "params": {"password": canary, "db.password": canary},
                "paramText": f"jdbc:oracle:thin:scott/{canary}@db",
            }
        ]
    }
    handler = synthetic_handler(override={"/api/transaction/sql": httpx.Response(200, json=body)})
    tools, _ = make_tools("http://apm.test", transport=httpx.MockTransport(handler))
    out = await _profile(tools, include_param_key=True, profile_no=3)
    sqls = out["rows"][0]["sqls"]
    # 중앙 경계가 비밀 키 값을 먼저 가리고(`pw = [가림]`) 리터럴은 mask_sql이 가린다(`id = ?`)
    assert sqls[0].startswith("select * from users where pw = ") and sqls[0].endswith("id = ?")
    assert canary not in json.dumps(out, ensure_ascii=False)
