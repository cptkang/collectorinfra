"""`mcp_server` 고수준 도구 배선 (Plan 78 W3-1·4 / Plan 80 WU-13 · SPEC M4).

**도구 시그니처는 실측본이다**(`mcp_server/mcp_server/polestar_tools.py` · 2026-08-27) —
`plans/78` W3-1이 적은 `os_config`·`resource_status`·`metric_trend`는 실제로 `polestar_` 접두를
가지며, `polestar_process_snapshot`은 **`source` 인자가 없다**(SPEC 정정 C-3).
계획서 의사코드를 신뢰하지 않고 실측한다(Known Mistakes).
"""

from __future__ import annotations

import json

import pytest

from src.config import DBHubConfig, QueryConfig
from src.dbhub.client import DBHubClient


class _Raw:
    """MCP 도구 결과 대역(`content`에 TextContent 유사 객체)."""

    class _Text:
        def __init__(self, text):
            self.text = text

    def __init__(self, payload):
        self.content = [self._Text(json.dumps(payload, ensure_ascii=False))]


@pytest.fixture
def client(monkeypatch):
    c = DBHubClient(DBHubConfig(source_name="polestar_gimpo"), QueryConfig())
    calls: list[tuple[str, dict]] = []

    async def _fake_call(tool_name, arguments):
        calls.append((tool_name, dict(arguments)))
        return _Raw({
            "rows": [{"prop_name": "OSType", "prop_value": "Linux"}],
            "row_count": 1,
            "queried_at": "2026-08-27T12:00:00",
            "source_kind": "polestar_db",
            "source": arguments.get("source", ""),
            "engine": "postgres",
        })

    monkeypatch.setattr(c, "_ensure_connected", lambda: None)
    monkeypatch.setattr(c, "_call_tool", _fake_call)
    c._test_calls = calls
    return c


# ──────────────────────────────────────────────
# 실측 시그니처 (SPEC C-3)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_os_config_uses_hostname_and_source(client):
    await client.inspect_host(profile="os_config", hostname="svweb001")
    tool, args = client._test_calls[0]
    assert tool == "polestar_os_config"
    assert args == {"source": "polestar_gimpo", "hostname": "svweb001"}


@pytest.mark.asyncio
async def test_resource_status_uses_server_name(client):
    """★ 식별 키가 갈린다 — `resource_status`는 `server_name`이다(D-046: name ≠ hostname)."""
    await client.inspect_host(profile="resource_status", server_name="웹서버01")
    tool, args = client._test_calls[0]
    assert tool == "polestar_resource_status"
    assert args == {"source": "polestar_gimpo", "server_name": "웹서버01"}


@pytest.mark.asyncio
async def test_process_snapshot_has_no_source_argument(client):
    """★ 실측 — `polestar_process_snapshot`은 프로세스 API 직결이라 `source`를 받지 않는다.

    계획서대로 `source`를 넣었다면 서버가 거부했을 것이다.
    """
    await client.inspect_host(profile="processes", hostname="svweb001", top_n=5, sort="mem")
    tool, args = client._test_calls[0]
    assert tool == "polestar_process_snapshot"
    assert "source" not in args
    assert args == {"hostname": "svweb001", "top_n": 5, "sort": "mem"}


@pytest.mark.asyncio
async def test_metric_trend_passes_all_options(client):
    await client.inspect_host(
        profile="metric_trend", server_name="웹서버01",
        kind="cpu", granularity="d", periods=7,
    )
    tool, args = client._test_calls[0]
    assert tool == "polestar_metric_trend"
    assert args["kind"] == "cpu" and args["granularity"] == "d" and args["periods"] == 7


# ──────────────────────────────────────────────
# 반환 계약 그대로 소비 (W3-1)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_server_contract_is_consumed_verbatim(client):
    """반환 계약 `{rows,row_count,queried_at,source_kind,source,engine}`을 **그대로** 쓴다.

    방언·금지조인·마스킹은 서버가 처리하므로 본체는 무지해도 된다(D-122) —
    본체가 재가공하면 그 무지가 깨진다.
    """
    result = await client.inspect_host(profile="os_config", hostname="svweb001")
    assert set(result) == {
        "rows", "row_count", "queried_at", "source_kind", "source", "engine",
    }
    assert result["rows"][0]["prop_name"] == "OSType"


# ──────────────────────────────────────────────
# 실행 전 검증 · 구조화 실패 (W3-4)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_profile_is_rejected_before_call(client):
    result = await client.inspect_host(profile="없는프로파일", hostname="h1")
    assert "error" in result
    assert client._test_calls == []          # ★ 호출 자체가 일어나지 않는다


@pytest.mark.asyncio
async def test_missing_identifier_is_rejected_before_call(client):
    """식별 키가 없으면 호출하지 않는다 — 어느 키가 필요한지 사유에 담는다."""
    result = await client.inspect_host(profile="resource_status", hostname="svweb001")
    assert "server_name" in result["error"]
    assert client._test_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs,token",
    [
        ({"profile": "metric_trend", "server_name": "s", "kind": "cpuu"}, "kind"),
        ({"profile": "metric_trend", "server_name": "s", "kind": "cpu",
          "granularity": "y"}, "granularity"),
        ({"profile": "metric_trend", "server_name": "s", "kind": "cpu",
          "periods": 0}, "periods"),
        ({"profile": "processes", "hostname": "h", "sort": "disk"}, "sort"),
        ({"profile": "processes", "hostname": "h", "top_n": -1}, "top_n"),
    ],
)
async def test_enum_and_range_args_are_validated(client, kwargs, token):
    """열거·범위 인자를 **호출 전에** 거른다 — 실패는 구조화해 모델에 돌려준다."""
    result = await client.inspect_host(**kwargs)
    assert token in result["error"]
    assert client._test_calls == []


@pytest.mark.asyncio
async def test_tool_failure_returns_structured_error(client, monkeypatch):
    """도구 호출이 터져도 **예외가 아니라 `{error}`** 로 돌려준다(침묵 금지·구조화)."""
    async def _boom(tool_name, arguments):
        raise RuntimeError("MCP 세션 끊김")

    monkeypatch.setattr(client, "_call_tool", _boom)
    result = await client.inspect_host(profile="os_config", hostname="h1")
    assert "MCP 세션 끊김" in result["error"]


@pytest.mark.asyncio
async def test_unparseable_response_is_reported(client, monkeypatch):
    """응답을 해석 못 하면 빈 dict가 아니라 사유를 돌려준다."""
    async def _garbage(tool_name, arguments):
        return "not json"

    monkeypatch.setattr(client, "_call_tool", _garbage)
    result = await client.inspect_host(profile="os_config", hostname="h1")
    assert "해석하지 못했습니다" in result["error"]


# ──────────────────────────────────────────────
# 도구 수를 늘리지 않는다 · 읽기 전용 (W3-4 · D-122 ④)
# ──────────────────────────────────────────────

def test_one_public_entry_absorbs_four_tools():
    """★ "적지만 더 나은 도구" — 공개 API는 하나고 `profile`이 도구를 고른다."""
    assert len(DBHubClient.HOST_INSPECT_PROFILES) == 4
    assert not any(
        name.startswith("polestar_") for name in dir(DBHubClient)
    ), "도구별 공개 메서드를 만들면 도구 수를 늘린 것이다"


def test_inspect_host_never_touches_execute_sql():
    """★ 읽기 전용 불변 — 고수준 도구만 부른다. `execute_sql` 노출 정책 무변경(D-122 ④).

    **원시 소스 grep을 쓰지 않는다** — 이 함수의 docstring이 설명으로 `execute_sql`을
    언급하므로 문자열 검색은 오탐한다(같은 함정에 이미 한 번 걸렸다 — 미들웨어 테스트).
    호출 노드와 도구 이름만 본다.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(DBHubClient.inspect_host)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "execute_sql" not in called

    tools = {spec["tool"] for spec in DBHubClient.HOST_INSPECT_PROFILES.values()}
    assert all(t.startswith("polestar_") for t in tools)

    # 인자 조립에 SQL이 섞이지 않는다(본체는 SQL을 만들지 않는다 — 서버가 조립한다).
    vtree = ast.parse(textwrap.dedent(inspect.getsource(DBHubClient._validate_inspect_args)))
    literals = {
        n.value for n in ast.walk(vtree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert not any("SELECT" in v.upper() for v in literals)
