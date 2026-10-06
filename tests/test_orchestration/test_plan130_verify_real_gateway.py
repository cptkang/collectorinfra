"""plans/130 독립 검증(verify-130 · 2026-10-06) — 실프로세스 왕복.

목 Open API(이 프로세스 스레드 · 127.0.0.1 임시 포트) ↔ `python -m apm_gateway`(임시 포트 · 임시
스풀 · 소스 두 개 `bank`·`common`이 같은 목을 본다) ↔ 실제 MCP SSE ↔ `run_apm_query`.

FastMCP는 **모르는 인자를 조용히 버린다** — 키 이름이 어긋나도 오류가 없다. 그래서 인자가 실제로
닿았는지를 결과 모양으로 가른다:

- `instance_name` — hostName이 빈 인스턴스를 이름으로 불러 성공해야 한다(버려지면 hostname·이름이
  둘 다 없어 `invalid_argument`).
- `source_ids` — 같은 인스턴스가 두 소스에 있다. 소스마다 1호출 · 1행이어야 한다(버려지면 호출마다
  두 소스 행이 다 와서 2배).
- `instance_id` — 같은 이름·다른 id 인스턴스 둘. 호출마다 1행이어야 한다(버려지면 2배).

결함 재현 V130-2(같은 이름·같은 id · 두 도메인 → 같은 인자 두 번 → 행 중복)는 실프로세스에서도
고정한다(W4 교정 뒤 통과 — `xfail` 표지는 걷었다). 실 제니퍼·외부 네트워크·실 LLM·실 DB 0 · 포트 9096·9097·9099·8080
미사용.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from typing import Any

import pytest

from src.orchestration import apm_query as aq
from src.orchestration.entity_link import UNLINKED, LinkEntry
from src.utils.prior_dependency import NOTE_BRIDGE
from tests.test_orchestration.test_plan134_w567_body_verify import (
    FORBIDDEN_PORTS,
    GW_ROOT,
    _app_config,
    _free_port,
    _fx,
)

_GW_TOKEN = "tok-chat-v130-7Qe"
_MOCK_TOKEN = "tok-mock-v130-4Wd"
#: 대상 해석이 부르는 인벤토리·업무 경로(데이터 조회 아님)
_RESOLUTION_PATHS = {"/api/domain", "/api/instance", "/api/business"}

_INSTANCES = [
    {"instanceId": 1001, "name": "abc-was01", "hostName": "was-host01", "domainId": 1000},
    {"instanceId": 1002, "name": "abc-was02", "hostName": "", "domainId": 2000},
    {"instanceId": 1003, "name": "pay-api", "hostName": "was-host03", "domainId": 1000},
    {"instanceId": 6001, "name": "twin-was", "hostName": "twin-a", "domainId": 1000},
    {"instanceId": 6002, "name": "twin-was", "hostName": "twin-b", "domainId": 1000},
    {"instanceId": 5001, "name": "dup-was", "hostName": "dup-a", "domainId": 1000},
    {"instanceId": 5001, "name": "dup-was", "hostName": "dup-b", "domainId": 2000},
]


@pytest.fixture(scope="module")
def real_gateway(tmp_path_factory):
    """목 Open API + 게이트웨이 실프로세스(소스 bank·common) — `(sse_url, mock_state)`."""
    pytest.importorskip("mcp")
    scripts = GW_ROOT / "testdata" / "jennifer" / "scripts"
    sys.path.insert(0, str(scripts))
    import mock_openapi

    now_ms = int(time.time() * 1000)
    realtime = [{"domainId": i["domainId"], "instanceId": i["instanceId"],
                 "instanceName": i["name"], "responseTime": 120.0, "tps": 3.0}
                for i in _INSTANCES]
    active = [{"domainId": 1000, "instanceId": 1003, "instanceName": "pay-api",
               "application": "/pay", "status": "RUNNING", "txid": "1",
               "startTime": now_ms - 1000, "businessId": [9], "businessName": ["결제"]}]
    fixtures = tmp_path_factory.mktemp("fx130")
    for i, fx in enumerate([
        _fx("/api/domain", {"result": [{"domainId": 1000, "name": "d1000"},
                                       {"domainId": 2000, "name": "d2000"}]}),
        _fx("/api/instance", {"result": _INSTANCES}),
        _fx("/api/realtime/instance", {"result": realtime}),
        _fx("/api/business", {"result": [
            {"domainId": 1000, "businessId": 9, "name": "결제", "description": "카드 승인"},
            {"domainId": 1000, "businessId": 7, "name": "주문", "description": "주문 처리"}]}),
        _fx("/api/activeService/list", {"result": active}),
        _fx("/api/transaction/time", {"result": []}),
    ]):
        (fixtures / f"fx_{i:02d}.json").write_text(json.dumps(fx), encoding="utf-8")

    original = mock_openapi.Handler._narrow

    def narrow(template: str, query: dict[str, str], body: Any) -> Any:
        body = original(template, query, body)
        if isinstance(body, dict) and isinstance(body.get("result"), list) \
                and "domain_id" in query:
            rows = [r for r in body["result"]
                    if str(r.get("domainId", query["domain_id"])) == query["domain_id"]]
            body = {**body, "result": rows}
        return body

    mock_openapi.Handler._narrow = staticmethod(narrow)
    server, state = mock_openapi.make_server(fixtures, _MOCK_TOKEN, "connected")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mock_url = f"http://127.0.0.1:{server.server_address[1]}"
    port = _free_port()
    assert port not in FORBIDDEN_PORTS
    spool = tmp_path_factory.mktemp("spool130")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))}
    env.update({
        "JENNIFER_SOURCES": '["bank", "common"]', "JENNIFER_API_URL": "",
        "JENNIFER_API_TOKEN": "",
        "JENNIFER_BANK_API_URL": mock_url, "JENNIFER_BANK_API_TOKEN": _MOCK_TOKEN,
        "JENNIFER_COMMON_API_URL": mock_url, "JENNIFER_COMMON_API_TOKEN": _MOCK_TOKEN,
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
        yield f"http://127.0.0.1:{port}/sse", state
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        server.shutdown()
        server.server_close()
        mock_openapi.Handler._narrow = staticmethod(original)
        sys.path.remove(str(scripts))


class _NoEdge:
    """업무명 간선(E6) 0건 — 실 DB를 읽지 않는다(게이트웨이 근거만 본다)."""

    async def __call__(self, terms, *, app_config, authorized_db_ids):
        entries = [LinkEntry(t, "business_name", "E6", UNLINKED, grade="none") for t in terms]
        return {t: [] for t in terms}, entries, []


@pytest.fixture
def live(real_gateway, monkeypatch):
    url, state = real_gateway
    monkeypatch.setattr(aq, "_SESSION_FACTORY", None)
    monkeypatch.setattr(aq, "link_business_names", _NoEdge())
    with state.lock:
        state.hits.clear()
    return url, state


async def _real(url: str, views: list[str], targets: list[dict]) -> dict:
    task = {"task_id": "t1", "agent": "apm_query", "views": views, "targets": targets,
            "sub_query": "q", "input_from": []}
    isolated = {"parsed_requirements": {"filter_conditions": [], "time_range": None},
                "conversation_context": {}, "thread_id": "th-v130", "user_id": "alice"}
    return await aq.run_apm_query(task, isolated, llm=None,
                                  app_config=_app_config(url, _GW_TOKEN))


def _hits(state) -> list[dict]:
    with state.lock:
        return list(state.hits)


def _keys(res: dict) -> list[tuple[Any, Any, Any]]:
    return [(r.get("source_id"), r.get("domain_id"), r.get("instance_id"))
            for r in res.get("query_results") or []]


async def test_empty_hostname_instance_is_queried_by_name_per_source(live) -> None:
    """`instance_name`·`source_ids`가 FastMCP를 지나 게이트웨이에 닿는다(빈 hostName · 두 소스)."""
    url, state = live
    res = await _real(url, ["apm.app_health"], [{"text": "abc-was02", "kind": "instance"}])
    assert res["apm_query"]["source_status"]["status"] == "ok", res.get("error") or res
    assert sorted(_keys(res)) == [("bank", 2000, 1002), ("common", 2000, 1002)]
    assert {r.get("instance_name") for r in res["query_results"]} == {"abc-was02"}
    realtime = [h for h in _hits(state) if h["path"] == "/api/realtime/instance"]
    assert realtime and all(h["query"].get("domain_id") == "2000" for h in realtime)


async def test_instance_id_narrows_same_named_instances(live) -> None:
    """같은 이름·다른 id — 호출마다 그 id 한 행(`instance_id`가 버려지지 않는다)."""
    url, _ = live
    res = await _real(url, ["apm.app_health"], [{"text": "twin-was", "kind": "instance"}])
    assert res["apm_query"]["source_status"]["status"] == "ok", res.get("error") or res
    assert sorted(_keys(res)) == [("bank", 1000, 6001), ("bank", 1000, 6002),
                                  ("common", 1000, 6001), ("common", 1000, 6002)]


async def test_same_name_and_id_in_two_domains_is_not_duplicated(live) -> None:
    url, _ = live
    res = await _real(url, ["apm.app_health"], [{"text": "dup-was", "kind": "instance"}])
    counts = Counter(_keys(res))
    assert counts and max(counts.values()) == 1, counts


async def test_business_text_resolves_through_jennifer_business_trace(live) -> None:
    """업무명(auto) — 이름 검색 0건 → 게이트웨이 업무 정의(B2) 역추적 → 그 인스턴스만 조회."""
    url, state = live
    res = await _real(url, ["apm.app_health"], [{"text": "결제", "kind": "auto"}])
    assert res["apm_query"]["source_status"]["status"] == "ok", res.get("error") or res
    assert sorted(_keys(res)) == [("bank", 1000, 1003), ("common", 1000, 1003)]
    bridges = [d["text"] for d in res.get("disclosures") or [] if d["kind"] == NOTE_BRIDGE]
    assert any("'결제'" in t and "업무" in t for t in bridges), bridges
    paths = {h["path"] for h in _hits(state)}
    assert "/api/business" in paths and "/api/activeService/list" in paths


async def test_unresolved_text_hits_no_data_endpoint(live) -> None:
    """해석 0건 — 인벤토리·업무 목록만 묻고 데이터 경로(실시간 등)는 0회 · 「찾지 못함」."""
    url, state = live
    res = await _real(url, ["apm.app_health"], [{"text": "zzz-none", "kind": "auto"}])
    assert res["apm_query"]["source_status"]["status"] == "not_queried", res
    assert res.get("degraded_reason") == "apm_target_unresolved" or "찾지 못" in str(
        res.get("final_response")), res
    paths = {h["path"] for h in _hits(state)}
    assert paths <= _RESOLUTION_PATHS, paths


async def test_instances_view_answers_with_resolved_rows_only(live) -> None:
    """인스턴스 목록 + 대상 텍스트 — 해석 행이 답이다(목록 전체 · 데이터 조회 없음)."""
    url, state = live
    res = await _real(url, ["apm.instances"], [{"text": "abc-was", "kind": "instance"}])
    assert res["apm_query"]["source_status"]["status"] == "ok", res.get("error") or res
    assert sorted(_keys(res)) == [("bank", 1000, 1001), ("bank", 2000, 1002),
                                  ("common", 1000, 1001), ("common", 2000, 1002)]
    assert {h["path"] for h in _hits(state)} <= _RESOLUTION_PATHS
