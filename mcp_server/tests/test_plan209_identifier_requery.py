"""plans/209 W1 — 폴스타 DB 도구 0건 식별자 재해소·안내(D-360 ①②).

등록명 전용 도구 6종은 OS 호스트명으로 오면 0건이었다(오류·안내 없음). 0건이고 ``source``가
있을 때만 폴스타 앵커로 1회 재해소해, 정확히 1건이면 그 등록명으로 재조회한다. OS 구성 도구는
대칭(등록명 → 호스트명)이다. 행이 있으면 현행과 비트 동일해야 한다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

try:
    from mcp_server import polestar_tools as pt
    from mcp_server.config import AppServerConfig

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

from tests.test_polestar_tools import _CaptureMCP, _fake_ctx

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")

_YD = "polestar_cm_yd"
_B0 = "polestar_b0"
_NAME = "cocm-ngcpkd05"
_HOST = "worker1"
_DATA = [{"v": 1}]

#: 등록명 전용 도구 → 호출 인자(server_name 제외).
_NAME_TOOLS: dict[str, dict[str, Any]] = {
    "polestar_alarm_history": {"alarm_name": "CPU"},
    "polestar_incident_alarms": {"reference_time": "2026-10-01T10:00:00"},
    "polestar_metric_trend": {"kind": "cpu"},
    "polestar_resource_status": {},
    "polestar_topology": {},
    "polestar_change_history": {},
}


class _Pool:
    """SQL별 응답을 정하는 가짜 풀 — 앵커 조회는 ``anchor``, 그 밖은 ``data(sql)``."""

    def __init__(
        self,
        data: Callable[[str], list[dict[str, Any]]],
        anchor: list[dict[str, Any]] | Exception | None = None,
        engine: str = "postgresql",
        source: str = _YD,
    ) -> None:
        self.data = data
        self.anchor = anchor if anchor is not None else []
        self.engines = {source: engine}
        self.calls: list[str] = []

    def is_source_active(self, name: str) -> bool:
        return name in self.engines

    def get_source_type(self, name: str) -> str:
        return self.engines[name]

    def get_active_sources(self) -> list[str]:
        return list(self.engines)

    def get_source_config(self, name: str) -> Any:
        return SimpleNamespace(max_rows=100)

    async def execute(self, name: str, sql: str) -> list[dict[str, Any]]:
        self.calls.append(sql)
        if "CR.HOSTNAME AS hostname" in sql:  # build_anchor_lookup_sql
            if isinstance(self.anchor, Exception):
                raise self.anchor
            return list(self.anchor)
        return self.data(sql)

    def anchor_calls(self) -> int:
        return sum("CR.HOSTNAME AS hostname" in s for s in self.calls)


def _anchor_row(name: str = _NAME, hostname: str = _HOST) -> dict[str, Any]:
    return {"resource_id": 1, "name": name, "hostname": hostname, "ipaddress": "10.0.0.1"}


def _only_for(value: str) -> Callable[[str], list[dict[str, Any]]]:
    """``'value'`` 리터럴이 들어간 SQL에만 행을 돌려준다."""
    return lambda sql: list(_DATA) if f"'{value}'" in sql else []


async def _call(pool: _Pool, tool: str, ident: str, source: str = _YD) -> dict[str, Any]:
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    ctx = _fake_ctx(pool, AppServerConfig())
    if tool == "polestar_os_config":
        out = await capture.tools[tool](source=source, hostname=ident, ctx=ctx)
    else:
        out = await capture.tools[tool](source=source, server_name=ident, ctx=ctx,
                                        **_NAME_TOOLS[tool])
    return json.loads(out)


def _strip_time(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k != "queried_at"}


# ── ① 행이 있으면 현행 동일 ──────────────────────────────────────────


@pytest.mark.parametrize("tool", list(_NAME_TOOLS))
async def test_registered_name_hit_no_resolve_and_same_payload(tool, monkeypatch):
    pool = _Pool(_only_for(_NAME), anchor=[_anchor_row()])
    body = await _call(pool, tool, _NAME)
    assert body["rows"] == _DATA and pool.anchor_calls() == 0 and len(pool.calls) == 1
    assert "server_name_note" not in body and "hint" not in body

    # 헬퍼를 우회한 응답(변경 전 경로)과 키·값이 같다
    async def passthrough(pool_, source, value, rows, run, **kw):
        return rows, {}

    monkeypatch.setattr(pt, "requery_with_resolved_identifier", passthrough)
    before = await _call(_Pool(_only_for(_NAME)), tool, _NAME)
    assert _strip_time(before) == _strip_time(body)


async def test_os_config_hostname_hit_no_resolve():
    pool = _Pool(_only_for(_HOST), anchor=[_anchor_row()])
    body = await _call(pool, "polestar_os_config", _HOST)
    assert body["rows"] == _DATA and pool.anchor_calls() == 0
    assert "hostname_note" not in body and "hint" not in body


# ── ② 호스트명 1건 해소 → 등록명 재조회 ─────────────────────────────


@pytest.mark.parametrize("tool", list(_NAME_TOOLS))
async def test_hostname_resolved_requeries_with_registered_name(tool):
    pool = _Pool(_only_for(_NAME), anchor=[_anchor_row()])
    body = await _call(pool, tool, _HOST)
    assert body["rows"] == _DATA and body["row_count"] == 1
    assert pool.anchor_calls() == 1 and len(pool.calls) == 3  # 원조회·앵커·재조회
    assert f"'{_NAME}'" in pool.calls[-1]
    note = body["server_name_note"]
    assert _HOST in note and _NAME in note and "OS 호스트명" in note
    assert "hint" not in body


async def test_requery_still_empty_keeps_note_without_hint():
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()])
    body = await _call(pool, "polestar_resource_status", _HOST)
    assert body["rows"] == [] and _NAME in body["server_name_note"] and "hint" not in body
    assert len(pool.calls) == 3


async def test_registered_name_zero_rows_only_note():
    """등록명이 맞는데 조건 행만 없으면 재조회하지 않고 노트만 단다."""
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()])
    body = await _call(pool, "polestar_alarm_history", _NAME)
    assert len(pool.calls) == 2 and "등록명이 맞음" in body["server_name_note"]
    assert "hint" not in body


# ── ③ 모호·0건·조회 오류·source 없음 → 재조회 없음 + hint ──────────


@pytest.mark.parametrize(
    ("anchor", "reason"),
    [
        ([_anchor_row("a", _HOST), _anchor_row("b", _HOST)], "2건"),
        ([], "없음"),
        (RuntimeError("db down"), "재해소 실패"),
    ],
    ids=["ambiguous", "not_found", "lookup_error"],
)
@pytest.mark.parametrize("tool", list(_NAME_TOOLS))
async def test_unresolved_no_requery_with_hint(tool, anchor, reason):
    pool = _Pool(lambda sql: [], anchor=anchor)
    body = await _call(pool, tool, _HOST)
    assert body["rows"] == [] and len(pool.calls) == 2  # 원조회 + 앵커 1회
    assert body["hint"] == pt.ZERO_ROW_HINTS[pt.IDENT_REGISTERED_NAME]
    assert reason in body["server_name_note"]


async def test_no_source_skips_resolve_with_hint():
    calls: list[str] = []

    async def run(name: str) -> list[dict[str, Any]]:
        calls.append(name)
        return list(_DATA)

    pool = _Pool(lambda sql: [], anchor=[_anchor_row()])
    rows, extra = await pt.requery_with_resolved_identifier(pool, None, _HOST, [], run)
    assert rows == [] and calls == [] and pool.calls == []
    assert extra["hint"] == pt.ZERO_ROW_HINTS[pt.IDENT_REGISTERED_NAME]
    assert "source 없음" in extra["server_name_note"]


async def test_requery_failure_keeps_empty_with_hint():
    async def run(name: str) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    pool = _Pool(lambda sql: [], anchor=[_anchor_row()])
    rows, extra = await pt.requery_with_resolved_identifier(pool, _YD, _HOST, [], run)
    assert rows == [] and "재조회 실패" in extra["server_name_note"] and "hint" in extra


async def test_hint_text_points_to_registered_name():
    hint = pt.ZERO_ROW_HINTS[pt.IDENT_REGISTERED_NAME]
    assert "폴스타 등록명" in hint and "polestar_process_snapshot" in hint


# ── ④ os_config 대칭 ────────────────────────────────────────────────


async def test_os_config_registered_name_resolved_to_hostname():
    pool = _Pool(_only_for(_HOST), anchor=[_anchor_row()])
    body = await _call(pool, "polestar_os_config", _NAME)
    assert body["rows"] == _DATA and f"'{_HOST}'" in pool.calls[-1]
    note = body["hostname_note"]
    assert _NAME in note and _HOST in note and "폴스타 등록명" in note
    assert "hint" not in body and "server_name_note" not in body


async def test_os_config_ambiguous_hint_symmetric():
    pool = _Pool(lambda sql: [], anchor=[_anchor_row("x", "h1"), _anchor_row("x", "h2")])
    body = await _call(pool, "polestar_os_config", "x")
    assert len(pool.calls) == 2
    assert body["hint"] == pt.ZERO_ROW_HINTS[pt.IDENT_OS_HOSTNAME]
    assert "OS 호스트명" in body["hint"]


# ── ⑤ 7개 도구 모두 같은 헬퍼 경유 ─────────────────────────────────


async def test_all_seven_tools_use_shared_helper(monkeypatch):
    seen: list[tuple[str, str]] = []

    async def spy(pool_, source, value, rows, run, *, target=pt.IDENT_REGISTERED_NAME,
                  tool=""):
        seen.append((tool, target))
        return rows, {}

    monkeypatch.setattr(pt, "requery_with_resolved_identifier", spy)
    for tool in [*_NAME_TOOLS, "polestar_os_config"]:
        await _call(_Pool(lambda sql: []), tool, _HOST)
    assert seen == [(t, pt.IDENT_REGISTERED_NAME) for t in _NAME_TOOLS] + [
        ("polestar_os_config", pt.IDENT_OS_HOSTNAME)]


async def test_change_history_db2_unsupported_untouched():
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()], engine="db2", source=_B0)
    body = await _call(pool, "polestar_change_history", _HOST, source=_B0)
    assert body["unsupported"] is True and pool.calls == []
    assert "hint" not in body


@pytest.mark.parametrize("tool", [*_NAME_TOOLS, "polestar_os_config"])
def test_tool_docstrings_name_identifier_kind(tool):
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    doc = capture.tools[tool].__doc__ or ""
    if tool == "polestar_os_config":
        assert "OS 호스트명" in doc and "폴스타 등록명 아님" in doc
    else:
        assert "폴스타 등록명" in doc and "OS 호스트명 아님" in doc
