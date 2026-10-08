"""plans/147 W0 재현 → W2에서 뒤집힌 기대(D-322 · 제니퍼 다중 소스 선택 사다리).

실프로세스 왕복: 목 Open API(이 프로세스 스레드 · 127.0.0.1 임시 포트) ↔ `python -m apm_gateway`
(임시 포트 · 임시 스풀 · 소스 3개 `bank`·`common`·`legacy`가 같은 목을 본다) ↔ 실제 MCP SSE ↔
`run_apm_query`. 분해는 가짜 LLM 고정 출력(`_llm_decompose`)으로 만든다. 본체가 게이트웨이에 실은
도구 인자는 실 세션을 감싼 기록기로 잰다(FastMCP는 모르는 인자를 조용히 버린다 — 결과 행의
`source_id`로도 확인한다).

W0 현행 → W2 기대:
  ① 「김포 WAS 응답시간」 — 3소스 모두 조회 → 공동존(`common`)만 조회(모든 호출 `source_ids`).
  ② 레지스트리에만 있고 게이트웨이에 없는 소스 지목 — 게이트웨이까지 가서 `invalid_argument`
     (설정 소스 목록 노출) → 조회 0 · 「연결되지 않아 조회하지 않았습니다」 안내만(넓히지 않음).
  ③ 지목 없음 — 전 소스 조회 · 되묻기 0 → 조회 0 · 되묻기(소스 3 + 「전체」).

실 제니퍼·외부 네트워크·실 LLM·실 DB 0 · 포트 9096·9097·9099·8080 미사용.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.clients import source_mcp_client as smc
from src.orchestration import apm_query as aq
from src.routing import apm_source_select as sel
from src.routing.registry import SourceSpec, get_registry
from tests.test_orchestration.test_plan134_w567_body_verify import (
    FORBIDDEN_PORTS,
    GW_ROOT,
    _app_config,
    _free_port,
    _fx,
)

ip = importlib.import_module("src.orchestration.intent_planner")

pytestmark = pytest.mark.apm_source_ladder

_GW_TOKEN = "tok-chat-p147-w0"
_MOCK_TOKEN = "tok-mock-p147-w0"
_SOURCES = ("bank", "common", "legacy")
_INSTANCES = [
    {"instanceId": 1001, "name": "gp-was01", "hostName": "gp-was-host01", "domainId": 1000},
    {"instanceId": 1002, "name": "gp-was02", "hostName": "gp-was-host02", "domainId": 1000},
]


@contextmanager
def launch_gateway(tmp_path_factory, sources: tuple[str, ...]) -> Iterator[str]:
    """목 Open API + 게이트웨이 실프로세스(소스 `sources`가 같은 목을 본다) — sse url."""
    pytest.importorskip("mcp")
    scripts = GW_ROOT / "testdata" / "jennifer" / "scripts"
    sys.path.insert(0, str(scripts))
    import mock_openapi

    realtime = [{"domainId": i["domainId"], "instanceId": i["instanceId"],
                 "instanceName": i["name"], "responseTime": 120.0, "tps": 3.0}
                for i in _INSTANCES]
    fixtures = tmp_path_factory.mktemp("fx147")
    for i, fx in enumerate([
        _fx("/api/domain", {"result": [{"domainId": 1000, "name": "d1000"}]}),
        _fx("/api/instance", {"result": _INSTANCES}),
        _fx("/api/realtime/instance", {"result": realtime}),
        _fx("/api/transaction/time", {"result": []}),
    ]):
        (fixtures / f"fx_{i:02d}.json").write_text(json.dumps(fx), encoding="utf-8")
    server, _state = mock_openapi.make_server(fixtures, _MOCK_TOKEN, "connected")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mock_url = f"http://127.0.0.1:{server.server_address[1]}"
    port = _free_port()
    assert port not in FORBIDDEN_PORTS
    spool = tmp_path_factory.mktemp("spool147")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))}
    env.update({
        "JENNIFER_SOURCES": json.dumps(list(sources)), "JENNIFER_API_URL": "",
        "JENNIFER_API_TOKEN": "",
        **{f"JENNIFER_{s.upper()}_API_URL": mock_url for s in sources},
        **{f"JENNIFER_{s.upper()}_API_TOKEN": _MOCK_TOKEN for s in sources},
        "JENNIFER_DOMAIN_IDS": "[]", "JENNIFER_RATE_LIMIT_PER_SEC": "200",
        "APM_GATEWAY_HOST": "127.0.0.1", "APM_GATEWAY_PORT": str(port),
        "APM_GATEWAY_BEARER_TOKEN": "",
        "APM_GATEWAY_BEARER_TOKENS": json.dumps({"chat": _GW_TOKEN}),
        "APM_EVENT_POLLER_ENABLED": "false", "APM_SPOOL_DIR": str(spool),
        "APM_GATEWAY_LOG_LEVEL": "WARNING", "APM_TIMEZONE": "Asia/Seoul",
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "1",
    })
    proc = subprocess.Popen([sys.executable, "-m", "apm_gateway"], cwd=GW_ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(300):
            try:
                socket.create_connection(("127.0.0.1", port), 0.1).close()
                break
            except OSError:
                if proc.poll() is not None:
                    err = proc.stderr.read().decode(errors="replace")[-2000:] if proc.stderr \
                        else ""
                    pytest.fail(f"게이트웨이 기동 실패(exit {proc.returncode}): {err}")
                time.sleep(0.1)
        yield f"http://127.0.0.1:{port}/sse"
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        server.shutdown()
        server.server_close()
        sys.path.remove(str(scripts))


@pytest.fixture(scope="module")
def real_gateway(tmp_path_factory):
    """목 Open API + 게이트웨이 실프로세스(소스 bank·common·legacy) — sse url."""
    with launch_gateway(tmp_path_factory, _SOURCES) as url:
        yield url


@pytest.fixture
def recorded(monkeypatch) -> list[tuple[str, dict]]:
    """실 MCP 세션을 감싸 본체가 실은 도구 인자를 기록한다(세션은 실제로 연다)."""
    calls: list[tuple[str, dict]] = []

    class _Rec:
        def __init__(self, inner: Any) -> None:
            self._inner = inner

        async def call_tool(self, name: str, arguments: dict):
            calls.append((name, dict(arguments)))
            return await self._inner.call_tool(name, arguments)

    @asynccontextmanager
    async def factory(url, headers):
        async with smc._mcp_sse_session(url, headers) as inner:
            yield _Rec(inner)

    monkeypatch.setattr(aq, "_SESSION_FACTORY", factory)
    return calls


class _JsonLLM:
    """가짜 분해 LLM — 고정 JSON 출력."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


async def _decompose_and_run(url: str, query: str, sources: list[str],
                             hosts: list[str] | None = None, **extra: Any) -> dict:
    """가짜 LLM 분해(APM task 1개) → `run_apm_query`(실 게이트웨이) · `extra`는 격리 입력에
    더한다."""
    cfg = _app_config(url, _GW_TOKEN)
    payload = {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": query,
                          "views": ["apm.app_health"], "sources": sources, "areas": [],
                          "requested_source": ""}]}
    plan = await ip._llm_decompose(_JsonLLM(payload), query, cfg)
    (task,) = plan["tasks"]
    conditions = [{"field": "hostname", "op": "=", "value": h} for h in hosts or []]
    isolated = {"parsed_requirements": {"filter_conditions": conditions, "time_range": None},
                "conversation_context": {}, "thread_id": "th-p147", "user_id": "alice",
                "user_query": query, "original_user_query": query, **extra}
    return await aq.run_apm_query(task, isolated, llm=None, app_config=cfg)


def _row_sources(res: dict) -> set[Any]:
    return {r.get("source_id") for r in res.get("query_results") or []}


def _asks_back(res: dict) -> bool:
    return res.get("degraded_reason") == "apm_unresolved_condition"


# W0 현행: 3소스 모두 조회 → W2: 위치어 「김포」(공동존 단어)면 `common`만 조회한다(D-322).
async def test_gimpo_was_response_time_queries_common_only(real_gateway, recorded) -> None:
    res = await _decompose_and_run(real_gateway, "김포 WAS 응답시간", [])
    assert res["source_status"][0]["status"] in ("ok", "partial"), res.get("error") or res
    (ranking, health) = recorded
    assert ranking[0] == "apm_fleet" and ranking[1]["source_ids"] == ["common"]
    assert health[0] == "apm_app_health" and health[1]["source_ids"] == ["common"]
    assert {t["source_id"] for t in health[1]["targets"]} == {"common"}
    assert _row_sources(res) == {"common"}, "공동존 행만"
    assert res["apm_query"]["source_selection"]["used"] == ["common"]
    assert "공동존 제니퍼만 조회했습니다(근거: 「김포」)" in [
        d["text"] for d in res.get("disclosures") or []]


class _RegistryPlus:
    """레지스트리 소스 표에 게이트웨이에 없는 소스 1개를 더한 대역(그 밖은 실 레지스트리)."""

    def __init__(self, extra: SourceSpec) -> None:
        self._base = get_registry()
        self._extra = extra

    def sources_of(self, system: str) -> tuple[SourceSpec, ...]:
        found = self._base.sources_of(system)
        return (*found, self._extra) if system == aq.APM_SYSTEM else found

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


# W0 현행: 게이트웨이까지 가서 `invalid_argument`(설정 소스 목록 노출) → W2: 미연결 소스는
# 조회하지 않고 안내만 한다(§4.1 「모르는·미연결 소스」 — 다른 소스로 넓히지 않음).
async def test_registry_only_source_is_notice_only_without_gateway_call(
        real_gateway, recorded, monkeypatch) -> None:
    monkeypatch.setattr(aq, "get_registry", lambda: _RegistryPlus(
        SourceSpec("dr", "DR 제니퍼", terms=("DR 제니퍼",))))
    sel.set_available_apm_sources(set(_SOURCES))
    for query, hosts in (("DR 제니퍼 WAS 응답시간", None),
                         ("DR 제니퍼 gp-was-host01 응답시간", ["gp-was-host01"])):
        recorded.clear()
        res = await _decompose_and_run(real_gateway, query, ["dr"], hosts=hosts)
        assert recorded == [], "게이트웨이 호출 0"
        assert res["degraded_reason"] == "source_unavailable", res
        assert res["final_response"] == "DR 제니퍼는 연결되지 않아 조회하지 않았습니다."
        assert "설정된 소스" not in json.dumps(res, ensure_ascii=False, default=str)
        assert not _asks_back(res) and aq.SOURCE_CLARIFICATION_KEY not in res


# W0 현행: 전 소스 조회 · 되묻기 0 → W2: 소스 ≥2 배포에서 지목이 없으면 조회 전에 되묻는다(단 4).
async def test_no_source_named_asks_before_querying(real_gateway, recorded) -> None:
    res = await _decompose_and_run(real_gateway, "WAS 응답시간 알려줘", [])
    assert _asks_back(res) and res["error"]
    assert recorded == [], "조회 0"
    ask = res[aq.SOURCE_CLARIFICATION_KEY]
    assert [o["key"] for o in ask["options"]] == [*_SOURCES, sel.ALL_SOURCES]
    assert ask["allow_all"] is True and ask["multi"] is True
    assert "zone_clarification" not in res
