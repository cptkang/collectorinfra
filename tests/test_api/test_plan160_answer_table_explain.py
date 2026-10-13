"""plans/160 §8 (D-327 부기 2026-10-13) — 압축 날짜 시간 축 · 답 본문 표 해설.

- 통계 기준 월(`YYYYMM` 문자열)·일·시 압축 날짜를 시간 축으로 읽어 선 그래프가 된다
  (폐쇄망 실측: 은행존 월간 메모리 추세 3행에 「그래프로 보기」가 없었다 — `202607`이 등차 정수
  식별자로 빠지고 시간 칼럼이 없어 그릴 수 없음 판정).
- 응답 신호에 `explain: true`가 붙고(두 플래그 on), 그릴 수 없는 표도 `GET /query/{id}/tables`로
  원본 행을 받아 답 표 머리글·칸에 해설을 단다. 해설 API는 그릴 수 없는 표도 대상이다.

가짜 LLM만 쓴다 — 실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_api.test_plan160_explain_api import (
    _auth,
    _client,
    _config,
    _gloss,
    _interp,
    _LLMPatch,
    _store,
    env,  # noqa: F401 — pytest 픽스처 재사용
)


def month_rows() -> list[dict[str, Any]]:
    """폐쇄망 화면과 같은 모양 — 서버 1대 · 월 3행 · 값은 % 붙은 문자열."""
    return [
        {"server_name": "svr-db-01(DB 모니터링 보조 #1)", "hostname": "svr-db-01",
         "ipaddress": "10.0.0.11", "stat_month": m, "mem_avg_pct": a, "mem_max_pct": b}
        for m, a, b in (("202607", "50.3%", "67.5%"), ("202608", "38.9%", "87.9%"),
                        ("202609", "25.5%", "100.0%"))
    ]


def spec_rows() -> list[dict[str, Any]]:
    """그릴 수 없는 사양 목록(변하는 범주 칼럼 3개)."""
    return [
        {"hostname": f"web-0{i}", "os": f"RHEL {7 + i}", "cpu_model": f"Xeon {i}",
         "mem_gb": 64}
        for i in range(3)
    ]


# ── 압축 날짜 시간 축 ─────────────────────────────────────────────────


def test_yyyymm_strings_make_a_line_chart() -> None:
    from src.domain.result_chart import detect_chart

    spec = detect_chart(month_rows())
    assert spec is not None
    assert (spec.kind, spec.x, spec.x_type) == ("line", "stat_month", "time")
    assert spec.y == ("mem_avg_pct", "mem_max_pct")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("202607", (2026, 7, 1, 0)),
        ("20260522", (2026, 5, 22, 0)),
        ("2026052214", (2026, 5, 22, 14)),
        ("2026-07", (2026, 7, 1, 0)),
        ("202613", None),       # 13월 없음
        ("20260230", None),     # 2월 30일 없음
        ("2026052224", None),   # 24시 없음
        ("196912", None),       # 연도 범위 밖
        ("12345678", None),
        (202607, None),         # 정수는 압축 날짜로 보지 않는다(epoch·ID와 겹친다)
    ],
)
def test_parse_compact_dates(value: Any, expected: tuple[int, int, int, int] | None) -> None:
    from src.domain.result_chart import parse_datetime

    got = parse_datetime(value)
    if expected is None:
        assert got is None
    else:
        assert got is not None
        assert (got.year, got.month, got.day, got.hour) == expected


def test_compact_x_keeps_grid_text_and_chart_points() -> None:
    from src.api.result_chart_view import chart_tables
    from src.config import SecurityConfig
    from src.domain.result_chart import build_chart_data

    [table] = chart_tables(month_rows(), None, SecurityConfig(mask_ip=False))
    # 격자 값은 원문 그대로(벽시계 ISO로 바꾸지 않는다 — epoch 정수만 바꾼다)
    assert [r["stat_month"] for r in table.rows] == ["202607", "202608", "202609"]
    data = build_chart_data(table.rows, table.spec)
    xs = [p[0] for p in data["series"][0]["points"]]
    assert xs == sorted(xs) and len(xs) == 3


# ── 응답 신호 ─────────────────────────────────────────────────────────


def test_signal_explain_without_chart_and_unchanged_when_off() -> None:
    from src.api.result_chart_view import chart_field

    on = chart_field(_config(), spec_rows(), None)
    assert on == {"kind": None, "tables": [], "explain": True}
    # 해석 off — 종전 그대로(그릴 표가 없으면 None · 있으면 explain 키 없음)
    assert chart_field(_config(explain=False), spec_rows(), None) is None
    off_chart = chart_field(_config(explain=False), month_rows(), None)
    assert off_chart == {"kind": "line", "tables": [{"index": 0, "label": "", "kind": "line"}]}
    assert chart_field(_config(), [], None) is None


# ── 원본 표 API ──────────────────────────────────────────────────────


def test_tables_api_returns_unchartable_tables(env: _LLMPatch) -> None:  # noqa: F811
    _store("q1", spec_rows())
    client: TestClient = _client()
    r = client.get("/api/v1/query/q1/tables", headers=_auth("u1"))
    assert r.status_code == 200
    [t] = r.json()["tables"]
    assert t["kind"] is None
    assert t["columns"] == ["hostname", "os", "cpu_model", "mem_gb"]
    assert t["rows_total"] == 3 and t["rows"][1]["hostname"] == "web-01"
    assert client.get("/api/v1/query/q1/tables", headers=_auth("intruder")).status_code == 403


def test_tables_api_404_when_explain_off(env: _LLMPatch) -> None:  # noqa: F811
    _store("q1", spec_rows())
    r = _client(explain=False).get("/api/v1/query/q1/tables", headers=_auth("u1"))
    assert r.status_code == 404
    assert r.json()["detail"] == "용어·값 해석이 꺼져 있습니다."


def test_explain_works_on_unchartable_table(env: _LLMPatch) -> None:  # noqa: F811
    _store("q1", spec_rows())
    client = _client()
    g = _gloss(client, "q1", "cpu_model", headers=_auth("u1"))
    assert g.status_code == 200
    i = _interp(client, "q1", {"table": 0, "column": "mem_gb", "row": 2}, headers=_auth("u1"))
    assert i.status_code == 200
    assert i.json()["value"] in (64, "64")
