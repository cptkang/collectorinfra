"""plans/209 W1 독립 검증 — 재조회 SQL 인자 동일성 · 리터럴 이스케이프 · 비기본 인자.

구현 테스트(`test_plan209_identifier_requery.py`)는 기본 인자로만 부른다. 여기서는 창·limit·
reference_time 등 **비기본 인자**로 부를 때 재조회 SQL이 원 SQL에서 식별자 리터럴만 바뀐
문자열인지 본다(`run` 클로저가 같은 인자로 재조립하는지).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

try:
    from mcp_server import polestar_tools as pt
    from mcp_server.config import AppServerConfig

    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

from tests.test_plan209_identifier_requery import _HOST, _NAME, _Pool, _anchor_row
from tests.test_polestar_tools import _CaptureMCP, _fake_ctx

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")

_YD = "polestar_cm_yd"
_B0 = "polestar_b0"
_REF = "2026-10-01T10:00:00"

#: 비기본 인자(창·limit·기준시각·제외 ID·홉 등).
_RICH: dict[str, dict[str, Any]] = {
    "polestar_alarm_history": {"alarm_name": "CPU", "hours": 48, "exclude_alarm_id": "777",
                               "reference_time": _REF, "lookback_minutes": 90},
    "polestar_incident_alarms": {"reference_time": _REF, "lookback_minutes": 45, "alarm_name": "MEM"},
    "polestar_metric_trend": {"kind": "memory", "granularity": "h", "periods": 6,
                              "reference_time": _REF, "lookback_minutes": 120, "baseline_periods": 4},
    "polestar_resource_status": {},
    "polestar_topology": {"max_hops": 5},
    "polestar_change_history": {"reference_time": _REF, "lookback_minutes": 30},
}


async def _call(pool: _Pool, tool: str, ident: str, source: str, **kw: Any) -> dict[str, Any]:
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    ctx = _fake_ctx(pool, AppServerConfig())
    key = "hostname" if tool == "polestar_os_config" else "server_name"
    out = await capture.tools[tool](source=source, ctx=ctx, **{key: ident}, **kw)
    return json.loads(out)


def _swap(sql: str, old: str, new: str) -> str:
    assert f"'{old}'" in sql
    return sql.replace(f"'{old}'", f"'{new}'")


@pytest.mark.parametrize(("engine", "source"), [("postgresql", _YD), ("db2", _B0)])
@pytest.mark.parametrize("tool", list(_RICH))
async def test_requery_sql_equals_original_with_only_identifier_swapped(tool, engine, source):
    if tool == "polestar_change_history" and engine == "db2":
        pytest.skip("change_history는 DB2 미지원(빈 결과 강등)")
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()], engine=engine, source=source)
    body = await _call(pool, tool, _HOST, source, **_RICH[tool])
    assert len(pool.calls) == 3, body
    original, requery = pool.calls[0], pool.calls[2]
    assert requery == _swap(original, _HOST, _NAME)


async def test_os_config_requery_sql_equals_original_with_hostname_swapped():
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()])
    await _call(pool, "polestar_os_config", _NAME, _YD)
    assert pool.calls[2] == _swap(pool.calls[0], _NAME, _HOST)


async def test_change_history_without_reference_time_requery_has_no_upper_bound(monkeypatch):
    """reference_time 없음 → until_epoch=None — 재조회 SQL도 상한 줄이 없고 원 SQL과 같은 cutoff."""
    monkeypatch.setattr(pt.time, "time", lambda: 1_800_000_000.0)
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()])
    body = await _call(pool, "polestar_change_history", _HOST, _YD, hours=12)
    original, requery = pool.calls[0], pool.calls[2]
    assert "event_time <=" not in original and "event_time <=" not in requery
    assert requery == _swap(original, _HOST, _NAME)
    assert "window" not in body and _NAME in body["server_name_note"]


async def test_resolved_value_with_quote_is_escaped_in_requery():
    """재해소 값(DB에서 온 값)도 기존 리터럴 이스케이프 경로를 탄다 — 따옴표 두 겹."""
    evil = "x' OR '1'='1"
    pool = _Pool(lambda sql: [], anchor=[_anchor_row(name=evil)])
    await _call(pool, "polestar_resource_status", _HOST, _YD)
    assert "'x'' OR ''1''=''1'" in pool.calls[2]


async def test_resolved_value_with_backslash_is_not_requeried_and_hints():
    """리터럴로 못 쓰는 재해소 값(백슬래시) — 재조회 SQL 조립 실패를 삼키지 않고 hint·사유를 단다."""
    pool = _Pool(lambda sql: [], anchor=[_anchor_row(name="a\\b")])
    body = await _call(pool, "polestar_resource_status", _HOST, _YD)
    assert len(pool.calls) == 2 and body["rows"] == []
    assert "재조회 실패" in body["server_name_note"] and "hint" in body


async def test_topology_db2_note_and_requery_note_coexist():
    pool = _Pool(lambda sql: [], anchor=[_anchor_row()], engine="db2", source=_B0)
    body = await _call(pool, "polestar_topology", _HOST, _B0)
    assert "1홉" in body["note"] and _NAME in body["server_name_note"]


async def test_first_query_error_still_err_and_no_resolve():
    def boom(sql: str) -> list[dict[str, Any]]:
        raise RuntimeError("db down")

    pool = _Pool(boom, anchor=[_anchor_row()])
    body = await _call(pool, "polestar_resource_status", _HOST, _YD)
    assert "error" in body and pool.anchor_calls() == 0


def test_new_comments_have_no_new_schema_literal():
    """D-088 — 헬퍼 독스트링·상수 문구에 폴스타 스키마 리터럴(테이블명) 신규 유입 없음."""
    import inspect

    text = inspect.getsource(pt.requery_with_resolved_identifier) + "".join(pt.ZERO_ROW_HINTS.values())
    for lit in ("cmm_resource", "core_config_prop", "cmm_alarm", "cmm_metric"):
        assert lit not in text
