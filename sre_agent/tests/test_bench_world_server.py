"""plans/151 W2 — 가짜 세계 MCP 서버 동작 규칙(§4.10).

실 LLM 0 · 네트워크는 127.0.0.1 임의 포트(포트 0)만. 스냅샷·세계는 소형 픽스처
(`tests/fixtures/investigation_bench/`)를 써서 실 스냅샷 갱신에 흔들리지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from investigation_bench import schema
from investigation_bench.world_server import LOG_FIELDS, WorldServer
from investigation_bench.worlds import load_worlds
from starlette.testclient import TestClient

_FIX = Path(__file__).resolve().parent / "fixtures" / "investigation_bench"
_HOST = "bw-t01-app01"


def _snapshot() -> schema.Snapshot:
    return schema.load_snapshot(_FIX / "tool_schemas.json")


def _server(tmp_path: Path) -> WorldServer:
    return WorldServer(_snapshot(), load_worlds([_FIX / "worlds"]), tmp_path / "world_calls.jsonl")


def _trend(**extra: Any) -> dict[str, Any]:
    return {"source": "polestar", "server_name": _HOST, **extra}


def _payload(result: Any) -> Any:
    return json.loads(result.content[0].text)


def _log_rows(ws: WorldServer) -> list[dict[str, Any]]:
    return [json.loads(line) for line in ws.log_path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def ws(tmp_path: Path) -> WorldServer:
    server = _server(tmp_path)
    server.set_active("T01", trial=1)
    return server


@pytest.fixture
def live(tmp_path: Path):
    server = _server(tmp_path)
    server.start({"polestar": 0, "apm": 0})
    yield server
    server.stop()


async def _mcp(url: str, calls: list[tuple[str, dict[str, Any]]]) -> tuple[Any, list[Any]]:
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            results = [await session.call_tool(name, args) for name, args in calls]
    return listed, results


def _post_active(port: int, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/_bench/active", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ── 등록 · MCP 왕복 ──


def test_snapshot_tools_registered_verbatim(live: WorldServer) -> None:
    """스냅샷 도구 이름·inputSchema·outputSchema가 그대로 등록된다(엔드포인트별)."""
    snap = _snapshot()
    for endpoint in schema.SERVERS:
        listed, _ = asyncio.run(_mcp(f"http://127.0.0.1:{live.ports[endpoint]}/sse", []))
        got = {t.name: t for t in listed.tools}
        assert sorted(got) == [t["name"] for t in snap[endpoint]]
        for tool in snap[endpoint]:
            assert got[tool["name"]].inputSchema == tool["inputSchema"]
            assert got[tool["name"]].outputSchema == tool.get("outputSchema")
            assert got[tool["name"]].description == tool.get("description")


def test_health_tools_succeed_without_world(live: WorldServer) -> None:
    """현재 세계가 없어도 헬스체크 도구는 성공(isError=false) — holmes toolset이 안 꺼진다."""
    assert live.world_id is None
    _, (pol,) = asyncio.run(_mcp(f"http://127.0.0.1:{live.ports['polestar']}/sse",
                                 [("list_sources", {})]))
    _, (apm,) = asyncio.run(_mcp(f"http://127.0.0.1:{live.ports['apm']}/sse",
                                 [("gateway_health", {})]))
    pol_body = _payload(pol)
    assert pol.isError is False and isinstance(pol_body, list) and pol_body
    assert apm.isError is False and _payload(apm)["tool"] == "gateway_health"
    # 세계 진행 중에도 헬스 응답은 세계 default(빈 rows)가 아니라 소스 목록이다
    live.set_active("T01")
    assert live.resolve("polestar", "list_sources", {}).reply == pol_body


def test_mcp_call_follows_active_world(live: WorldServer) -> None:
    """제어 경로로 바꾼 세계가 다음 호출에 반영된다 · 응답은 실 서버 모양(텍스트 JSON + result)."""
    port = live.ports["polestar"]
    assert _post_active(port, {"world_id": "T01", "trial": 2}) == {"world_id": "T01", "trial": 2}
    url = f"http://127.0.0.1:{port}/sse"
    _, (r1,) = asyncio.run(_mcp(url, [("polestar_metric_trend", _trend(kind="filesystem"))]))
    assert r1.isError is False
    assert _payload(r1)["rows"][0]["value"] == 98.4
    assert r1.structuredContent == {"result": r1.content[0].text}

    _post_active(port, {"world_id": "T02", "trial": 1})
    args = {"source": "polestar", "server_name": "bw-t02-web01", "kind": "memory"}
    _, (r2,) = asyncio.run(_mcp(url, [("polestar_metric_trend", args)]))
    assert _payload(r2)["rows"][0]["value"] == 41.0
    rows = _log_rows(live)
    assert [(r["world_id"], r["trial"]) for r in rows] == [("T01", 2), ("T02", 1)]


def test_control_endpoint_arm_is_logged(live: WorldServer) -> None:
    """W3 가산: 제어 경로의 선택 필드 `arm`이 응답과 호출 로그 행에 실린다(없으면 종전 모양)."""
    port = live.ports["polestar"]
    body = {"world_id": "T01", "trial": 1, "arm": "control_b"}
    assert _post_active(port, body) == body
    url = f"http://127.0.0.1:{port}/sse"
    asyncio.run(_mcp(url, [("polestar_metric_trend", _trend(kind="filesystem"))]))
    assert _post_active(port, {"world_id": "T02", "trial": 1}) == {"world_id": "T02", "trial": 1}
    asyncio.run(_mcp(url, [("polestar_metric_trend", _trend(kind="memory"))]))
    rows = _log_rows(live)
    assert [r["arm"] for r in rows] == ["control_b", None]


def test_control_endpoint_unknown_world_404(live: WorldServer) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post_active(live.ports["polestar"], {"world_id": "NOPE"})
    assert exc.value.code == 404


def test_control_endpoint_loopback_only(tmp_path: Path, live: WorldServer) -> None:
    """바인딩은 127.0.0.1뿐이고, 루프백이 아닌 클라이언트의 제어 요청은 403이다."""
    for s in live._servers:
        for sock in s.servers[0].sockets:
            assert sock.getsockname()[0] == "127.0.0.1"
    app = _server(tmp_path).build_app("polestar", control=True)
    with TestClient(app) as client:  # TestClient의 클라이언트 주소는 "testclient"
        resp = client.post("/_bench/active", json={"world_id": "T01"})
    assert resp.status_code == 403
    # apm 엔드포인트에는 제어 경로가 없다
    apm = _server(tmp_path).build_app("apm", control=False)
    with TestClient(apm) as client:
        assert client.post("/_bench/active", json={"world_id": "T01"}).status_code == 404


# ── 매처 ──


def test_first_match_wins_and_subset(ws: WorldServer) -> None:
    """위에서부터 첫 일치(뒤 규칙이 더 구체적이어도 앞 규칙) · match는 인자의 부분집합이면 된다."""
    out = ws.resolve("polestar", "polestar_metric_trend",
                     _trend(kind="filesystem", granularity="d", periods=3))
    assert out.matcher == "polestar_metric_trend#0"
    assert out.reply["rows"][0]["value"] == 98.4


def test_no_rule_match_uses_default(ws: WorldServer) -> None:
    out = ws.resolve("polestar", "polestar_metric_trend", _trend(kind="disk_io"))
    assert out.matcher == "default" and out.reply == {"error": None, "rows": []}
    assert out.arg_error is None and out.is_error is False


def test_undefined_tool_is_empty_not_error(tmp_path: Path) -> None:
    """세계에 정의 없는 도구 = 빈 결과(오류 아님). default도 없는 세계면 빈 rows."""
    ws = _server(tmp_path)
    ws.set_active("T02")
    out = ws.resolve("polestar", "polestar_alarm_history",
                     {"source": "polestar", "server_name": "bw-t02-web01", "alarm_name": "x"})
    assert out.matcher == "default" and out.is_error is False
    assert out.reply == {"rows": [], "row_count": 0}


def test_null_args_are_absent(ws: WorldServer) -> None:
    """null 인자는 「없음」 — 매처 키·호스트·시간창 검사 모두."""
    out = ws.resolve("polestar", "polestar_metric_trend",
                     _trend(kind="filesystem", granularity=None, reference_time=None,
                            lookback_minutes=None))
    assert out.matcher == "polestar_metric_trend#0" and out.arg_error is None
    # APM 선택 인자(hostname·targets)가 모두 null → 호스트 검사 없음 · match {hostname: …} 규칙에
    # 맞지 않음 → 마지막(무조건) 규칙
    out = ws.resolve("apm", "apm_app_health",
                     {"hostname": None, "targets": None, "instance_name": "was01"})
    assert out.arg_error is None and out.matcher == "apm_app_health#1"


def test_missing_required_is_tool_error(ws: WorldServer) -> None:
    """필수 인자 누락(또는 null)은 실 FastMCP 서버처럼 isError 검증 오류다."""
    result = ws.call("polestar", "polestar_metric_trend", {"source": "polestar", "kind": "cpu",
                                                           "server_name": None})
    assert result.isError is True and result.structuredContent is None
    assert "server_name" in result.content[0].text
    assert _log_rows(ws)[-1]["arg_error"] == "required"


# ── 호스트 · 시간창 ──


def test_host_outside_world_is_target_missing(ws: WorldServer) -> None:
    out = ws.resolve("polestar", "polestar_metric_trend",
                     {"source": "polestar", "server_name": "bw-d99-app01", "kind": "filesystem"})
    assert out.arg_error == "host" and out.matcher is None
    assert out.reply["rows"] == [] and "대상 없음" in out.reply["message"]
    # 대소문자 차이는 같은 호스트
    out = ws.resolve("polestar", "polestar_metric_trend", _trend(kind="filesystem") | {
        "server_name": _HOST.upper()})
    assert out.arg_error is None
    # APM: 실 게이트웨이 오류 모양(instance_unresolved) · targets 항목도 검사한다
    out = ws.resolve("apm", "apm_app_health", {"targets": [{"hostname": "other-host"}]})
    assert out.arg_error == "host"
    assert out.reply["error"] == "instance_unresolved" and "대상 없음" in out.reply["reason"]


def test_time_window_outside_incident_is_empty(ws: WorldServer) -> None:
    """사건 구간(알람 03:15 기준 -45m~0m)을 안 덮는 시간창 → 빈 결과 + arg_error=time_window."""
    day_before = _trend(kind="filesystem", reference_time="2026-10-11T03:15:00",
                        lookback_minutes=60)
    out = ws.resolve("polestar", "polestar_metric_trend", day_before)
    assert out.arg_error == "time_window" and out.reply == {"rows": [], "row_count": 0}
    # 겹치면 정상(구간 끝이 조금 지나도 폭이 덮는다)
    ok = _trend(kind="filesystem", reference_time="2026-10-12T03:20:00+09:00", lookback_minutes=60)
    assert ws.resolve("polestar", "polestar_metric_trend", ok).arg_error is None
    # 폭 인자가 없으면 스키마 기본값(incident_alarms lookback 60)
    alarms = {"source": "polestar", "server_name": _HOST, "reference_time": "2026-10-11T03:15:00"}
    assert ws.resolve("polestar", "polestar_incident_alarms", alarms).arg_error == "time_window"
    # hours 폭(alarm_history 기본 168h = 7일)이면 하루 전 기준시각도 덮는다
    hist = {"source": "polestar", "server_name": _HOST, "alarm_name": "x",
            "reference_time": "2026-10-13T03:15:00"}
    assert ws.resolve("polestar", "polestar_alarm_history", hist).arg_error is None
    # 기간 문자열 폭(prom window 기본 1h)
    prom = {"hostname": _HOST, "metric": "node_load1", "reference_time": "2026-10-12T05:00:00"}
    assert ws.resolve("polestar", "prom_metric_range", prom).arg_error == "time_window"
    # 시작·끝 쌍(apm_period_compare current_*)
    cmp_args = {"hostname": _HOST, "current_start": "2026-10-12T01:00:00",
                "current_end": "2026-10-12T02:00:00", "baseline_start": "2026-10-11T01:00:00",
                "baseline_end": "2026-10-11T02:00:00"}
    out = ws.resolve("apm", "apm_period_compare", cmp_args)
    assert out.arg_error == "time_window" and out.reply["tool"] == "apm_period_compare"
    # 폭을 모르면(metric_trend: lookback·hours·window 없음) 검사하지 않는다
    unknown = _trend(kind="filesystem", reference_time="2026-10-01T00:00:00")
    assert ws.resolve("polestar", "polestar_metric_trend", unknown).arg_error is None


# ── 생성기 · 오류 주입 ──


def test_gen_same_args_same_reply(ws: WorldServer) -> None:
    a = ws.call("polestar", "polestar_metric_trend", _trend(kind="cpu", periods=6))
    b = ws.call("polestar", "polestar_metric_trend",
                _trend(kind="cpu", periods=6, granularity=None))
    c = ws.call("polestar", "polestar_metric_trend", _trend(kind="cpu", periods=7))
    assert a.content[0].text == b.content[0].text
    series = _payload(a)["series"]
    assert len(series) == 6 and series[-1]["ts"] == "2026-10-12T03:15:00"
    assert series[0]["ts"] == "2026-10-12T02:25:00"
    assert all(43 <= p["value"] <= 47 for p in series[:2])     # spike_at -30m 이전 = base ± noise
    assert all(58 <= p["value"] <= 62 for p in series[3:])     # 이후 = to ± noise
    assert _payload(c)["series"] != series                     # 다른 인자 = 다른 시드
    rows = _log_rows(ws)
    assert rows[0]["reply_hash"] == rows[1]["reply_hash"] != rows[2]["reply_hash"]


def test_error_injection_shape(ws: WorldServer) -> None:
    """오류 주입 = 실 서버 도구 오류 모양(isError=false · 텍스트 JSON error 키 · inject 없음)."""
    pol = ws.call("polestar", "polestar_incident_alarms",
                  {"source": "polestar", "server_name": _HOST,
                   "reference_time": "2026-10-12T03:15:00"})
    assert pol.isError is False
    assert _payload(pol) == {"error": "DB 연결 시간 초과"}
    assert pol.structuredContent == {"result": pol.content[0].text}
    apm = ws.call("apm", "apm_app_health", {"instance_name": "was01"})
    body = _payload(apm)
    assert apm.isError is False and "inject" not in body
    assert body["error"] == "source_unavailable" and body["reason"] == "제니퍼 응답 지연"
    assert body["source_kind"] == "apm_api" and body["tool"] == "apm_app_health"


# ── 호출 로그 ──


def test_call_log_line_fields(ws: WorldServer) -> None:
    """world_calls.jsonl 한 줄 = §4.9 필드 전부(받은 인자 원문 · 맞은 규칙 · 응답 전문 · 해시)."""
    args = _trend(kind="filesystem", granularity=None)
    result = ws.call("polestar", "polestar_metric_trend", args)
    (row,) = _log_rows(ws)
    assert tuple(row) == LOG_FIELDS
    assert row["world_id"] == "T01" and row["trial"] == 1 and row["endpoint"] == "polestar"
    assert row["tool"] == "polestar_metric_trend" and row["args"] == args
    assert row["matcher"] == "polestar_metric_trend#0" and row["arg_error"] is None
    assert row["reply"] == _payload(result)
    assert len(row["reply_hash"]) == 64
