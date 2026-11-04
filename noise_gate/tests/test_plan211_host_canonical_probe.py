"""plans/211 W4 — 소재 프로브 SQL·후보 등급·선택 재확인의 정규형 비교(플래그 on) · off 비트 동일.

DB는 띄우지 않는다 — SQL은 문자열, 프로브는 대역 함수다. SQL 조건 기대값은
`build_canonical_host_condition` 반환값을 그대로 쓴다(조립 정본이 한 곳이라 바뀌어도 여기는 그대로).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from src.config import AppConfig, MultiDBConfig
from src.domain.host_names import build_canonical_host_condition

from noise_gate.application import host_candidates as hc
from noise_gate.domain import host_link as hl
from noise_gate.infrastructure.polestar_hostname_resolver import (
    build_host_probe_sql,
    build_host_token_sql,
    probe_hosts,
)

ON = SimpleNamespace(host_canonical_match_enabled=True)
OFF = SimpleNamespace(host_canonical_match_enabled=False)


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def _cond(column: str, values: list[str]) -> str:
    cond = build_canonical_host_condition(column, values, business_suffix=True)
    assert cond is not None
    return cond


# ─── 소재 프로브 SQL ──────────────────────────────────────────────────


_PROBE_CASES = [
    {"names": ["webap01"], "prefixes": ["webap01"], "ips": ["10.1.2.3"]},
    {"names": ["webap01.corp.local", "webap01"], "prefixes": [], "ips": []},
    {"names": ["a'b", "web%01"], "prefixes": ["web%01"], "ips": []},
    {"names": [], "prefixes": [], "ips": ["10.1.2.3"]},
]


@pytest.mark.parametrize("case", _PROBE_CASES)
@pytest.mark.parametrize("engine", ["postgresql", "db2"])
def test_probe_sql_off_is_default(case: dict, engine: str) -> None:
    assert build_host_probe_sql("polestar_cm_gp", **case, db_engine=engine) == build_host_probe_sql(
        "polestar_cm_gp", **case, db_engine=engine, canonical=False
    )


def test_probe_sql_off_matches_previous_shape() -> None:
    sql = build_host_probe_sql(
        "polestar_cm_gp", names=["webap01"], prefixes=["webap01"], ips=["10.1.2.3"]
    )
    assert sql == (
        "SELECT r.hostname AS hostname, r.name AS name, r.ipaddress AS ipaddress\n"
        "FROM polestar.cmm_resource r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        "  AND (LOWER(r.hostname) IN ('webap01') OR LOWER(r.name) IN ('webap01')"
        " OR LOWER(r.hostname) LIKE 'webap01.%' OR r.ipaddress IN ('10.1.2.3'))"
    )


def test_probe_sql_on_uses_canonical_condition_for_both_columns() -> None:
    off = build_host_probe_sql(
        "polestar_cm_gp", names=["webap01"], prefixes=["webap01"], ips=["10.1.2.3"]
    )
    sql = build_host_probe_sql(
        "polestar_cm_gp", names=["webap01"], prefixes=["webap01"], ips=["10.1.2.3"],
        canonical=True,
    )
    assert _cond("r.hostname", ["webap01"]) in sql
    assert _cond("r.name", ["webap01"]) in sql
    assert "LOWER(r.hostname) IN" not in sql and "LOWER(r.hostname) LIKE" not in sql
    assert "r.ipaddress IN ('10.1.2.3')" in sql, "IP 조건 불변"
    # 투영·from·where 공통부 불변
    assert sql.split("  AND (", 1)[0] == off.split("  AND (", 1)[0]


def test_probe_sql_on_fqdn_names() -> None:
    names = ["webap01.corp.local", "webap01"]
    sql = build_host_probe_sql("polestar_cm_gp", names=names, prefixes=[], ips=[], canonical=True)
    assert _cond("r.hostname", names) in sql and _cond("r.name", names) in sql


def test_probe_sql_on_keeps_exact_in_for_non_key_names() -> None:
    sql = build_host_probe_sql(
        "polestar_cm_gp", names=["web%01", "webap01"], prefixes=["web%01"], ips=[],
        canonical=True,
    )
    assert "LOWER(r.hostname) IN ('web%01')" in sql and "LOWER(r.name) IN ('web%01')" in sql
    assert "LIKE 'web%01" not in sql, "키가 아닌 이름으로 LIKE를 만들지 않는다"
    assert _cond("r.hostname", ["webap01"]) in sql


def test_probe_sql_on_keeps_exact_in_for_ip_names() -> None:
    sql = build_host_probe_sql(
        "polestar_cm_gp", names=["10.1.2.3"], prefixes=[], ips=["10.1.2.3"], canonical=True
    )
    assert "LOWER(r.hostname) IN ('10.1.2.3')" in sql and "r.ipaddress IN ('10.1.2.3')" in sql
    assert "REPLACE(" not in sql


def test_probe_sql_on_db2() -> None:
    sql = build_host_probe_sql(
        "polestar_b0", names=["WEB-AP01"], prefixes=[], ips=[], db_engine="db2", canonical=True
    )
    off = build_host_probe_sql(
        "polestar_b0", names=["WEB-AP01"], prefixes=[], ips=[], db_engine="db2"
    )
    assert _cond("r.hostname", ["WEB-AP01"]) in sql
    assert "LIMIT" not in sql and "FETCH" not in sql
    assert sql.split("  AND (", 1)[0] == off.split("  AND (", 1)[0]


def test_probe_sql_on_still_requires_candidates() -> None:
    with pytest.raises(ValueError):
        build_host_probe_sql("polestar_cm_gp", names=[], prefixes=[], ips=[], canonical=True)
    with pytest.raises(ValueError):
        # 키가 아닌 접두만 남으면 조건이 없다 — LIKE를 만들지 않고 전수 조회도 만들지 않는다
        build_host_probe_sql(
            "polestar_cm_gp", names=[], prefixes=["(업무)"], ips=[], canonical=True
        )


def test_token_sql_unchanged() -> None:
    assert "canonical" not in inspect.signature(build_host_token_sql).parameters
    sql = build_host_token_sql("polestar_cm_gp", ["webap"])
    assert "REPLACE(" not in sql and "LOWER(r.hostname) LIKE '%webap%'" in sql


class _Client:
    def __init__(self) -> None:
        self.sql: str | None = None

    async def __aenter__(self):  # noqa: ANN204
        return self

    async def __aexit__(self, *exc):  # noqa: ANN002, ANN204
        return False

    async def execute_sql(self, sql: str):  # noqa: ANN201
        self.sql = sql
        return SimpleNamespace(rows=[])


def _app_config(enabled: object) -> AppConfig:
    config = AppConfig()
    config.multi_db = MultiDBConfig(active_db_ids_csv="polestar_cm_gp")
    config.host_canonical_match_enabled = enabled  # type: ignore[assignment]
    return config


@pytest.mark.parametrize("enabled", [True, False])
def test_probe_hosts_reads_flag(enabled: bool) -> None:
    client = _Client()
    with patch("src.routing.db_registry.DBRegistry.get_client", lambda self, db_id: client):
        _run(probe_hosts(
            _app_config(enabled), "polestar_cm_gp", names=["web-ap01"], prefixes=["web-ap01"],
            ips=[],
        ))
    expected = build_host_probe_sql(
        "polestar_cm_gp", names=["web-ap01"], prefixes=["web-ap01"], ips=[], canonical=enabled
    )
    assert client.sql == expected


# ─── 등급(grade_row · rank_candidates · selection_grade) ─────────────────

#: §5 표의 호스트 쌍 — (제니퍼 쪽 a, 관측 DB 행 b, 정규형 일치 기대)
_VECTORS = [
    ("WEBAP01", "webap01", True),
    ("web-ap01", "web_ap01", True),
    ("webap01.corp.local", "webap01", True),
    ("webap01(인터넷뱅킹)", "webap01", True),
    ("webap01 (인터넷뱅킹)", "WEB-AP01.corp.local", True),
    ("webap01.a.local", "webap01.b.local", False),
    ("webap01", "webap010", False),
    ("(인터넷뱅킹)", "webap01", False),
    ("10.1.2.3", "10.1.2.3", True),
]


def _grade(a: str, b: str, *, column: str = "hostname", canonical: bool) -> dict | None:
    row = {"hostname": "", "name": "", "ipaddress": ""}
    row[column] = b
    return hl.grade_row(hl.build_probe_plan(a), "db1", row, canonical=canonical)


@pytest.mark.parametrize("column", ["hostname", "name"])
@pytest.mark.parametrize(("a", "b", "match"), _VECTORS)
def test_grade_row_vectors_on(a: str, b: str, match: bool, column: str) -> None:
    for x, y in ((a, b), (b, a)):
        graded = _grade(x, y, column=column, canonical=True)
        is_exact = graded is not None and graded["grade_key"] == hl.GRADE_EXACT
        assert is_exact is match, (x, y, column, graded)


@pytest.mark.parametrize("column", ["hostname", "name"])
@pytest.mark.parametrize(("a", "b", "match"), _VECTORS)
def test_grade_row_off_is_default(a: str, b: str, match: bool, column: str) -> None:
    for x, y in ((a, b), (b, a)):
        row = {"hostname": "", "name": "", "ipaddress": "", column: y}
        plan = hl.build_probe_plan(x)
        assert hl.grade_row(plan, "db1", row) == hl.grade_row(plan, "db1", row, canonical=False)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("web-ap01", "web_ap01"),
        ("webap01.corp.local", "webap01"),
        ("webap01(인터넷뱅킹)", "webap01"),
        ("webap01 (인터넷뱅킹)", "WEB-AP01.corp.local"),
    ],
)
def test_grade_row_off_does_not_widen_exact(a: str, b: str) -> None:
    graded = _grade(a, b, canonical=False)
    assert graded is None or graded["grade_key"] != hl.GRADE_EXACT


def test_grade_row_reasons() -> None:
    same = _grade("webap01", "webap01", canonical=True)
    case = _grade("WEBAP01", "webap01", canonical=True)
    canon = _grade("web-ap01", "web_ap01", canonical=True)
    assert same is not None and same["reasons"] == ["이름 일치"]
    assert case is not None and case["reasons"] == ["대소문자만 다름"]
    assert canon is not None and canon["reasons"][0] == hl.REASON_CANONICAL == "정규형 일치"
    assert canon["score"] == 0.95


def test_grade_row_keeps_other_evidence_on() -> None:
    plan = hl.build_probe_plan("webap01.corp.local", ip="10.1.2.3")
    row = {"hostname": "webap01", "name": "WEBAP01(인터넷뱅킹)", "ipaddress": "10.1.2.3"}
    graded = hl.grade_row(plan, "db1", row, canonical=True)
    assert graded is not None and graded["grade_key"] == hl.GRADE_EXACT
    assert graded["reasons"] == ["정규형 일치", "FQDN 짧은 이름", "IP 같음"]
    link = {"state": "confirmed", "kind": "link", "db_id": "db1", "server_name": row["name"]}
    confirmed = hl.grade_row(plan, "db1", row, confirmed=link, canonical=True)
    assert confirmed is not None and confirmed["grade_key"] == hl.GRADE_CONFIRMED


def test_rank_candidates_canonical_threads() -> None:
    plan = hl.build_probe_plan("web-ap01")
    rows = [
        ("db1", {"hostname": "web-ap01x", "name": "web-ap01x", "ipaddress": ""}),
        ("db1", {"hostname": "web_ap01.corp.local", "name": "WEB_AP01(인터넷뱅킹)",
                 "ipaddress": ""}),
    ]
    on = hl.rank_candidates(plan, rows, canonical=True)
    assert on[0]["server_name"] == "WEB_AP01(인터넷뱅킹)" and on[0]["grade_key"] == hl.GRADE_EXACT
    off = hl.rank_candidates(plan, rows)
    assert off == hl.rank_candidates(plan, rows, canonical=False)
    assert all(c["grade_key"] != hl.GRADE_EXACT for c in off)


def test_selection_grade_canonical() -> None:
    row = {"hostname": "web_ap01", "name": "WEB_AP01(인터넷뱅킹)", "ipaddress": ""}
    assert hl.selection_grade("web-ap01", "db1", row, canonical=True) == hl.GRADE_EXACT
    assert hl.selection_grade("web-ap01", "db1", row) == hl.selection_grade(
        "web-ap01", "db1", row, canonical=False
    )
    assert hl.selection_grade("web-ap01", "db1", row) != hl.GRADE_EXACT


# ─── 후보 검색 · 선택 재확인 ──────────────────────────────────────────


def _probe_rows(rows: list[dict]):  # noqa: ANN202
    async def _probe(db_id, names, prefixes, ips):  # noqa: ANN001, ANN202
        return rows

    return _probe


async def _no_tokens(db_id, tokens):  # noqa: ANN001, ANN202
    return []


@pytest.mark.parametrize(
    ("config", "exact"), [(ON, True), (OFF, False), (MagicMock(), False)]
)
def test_find_host_candidates_reads_flag(config: object, exact: bool) -> None:
    rows = [{"hostname": "web_ap01", "name": "WEB_AP01(인터넷뱅킹)", "ipaddress": ""}]
    out = _run(hc.find_host_candidates(
        config, hostname="web-ap01", source_id="s1", db_ids=["db1"],
        probe=_probe_rows(rows), token_probe=_no_tokens,
    ))
    grades = [c["grade_key"] for c in out["candidates"]]
    assert (hl.GRADE_EXACT in grades) is exact


@pytest.mark.parametrize(
    ("row", "value", "off", "on"),
    [
        ({"name": "WEB_AP01", "hostname": ""}, "web-ap01", False, True),
        ({"name": "webap01(인터넷뱅킹)", "hostname": ""}, "webap01", False, True),
        ({"name": "", "hostname": "webap01.corp.local"}, "webap01", True, True),
        ({"name": "", "hostname": "WEBAP01"}, "webap01", True, True),
        # 도메인이 다른 FQDN끼리는 다른 호스트(D-224 ㉰) — on에서 좁아진다
        ({"name": "", "hostname": "webap01.b.local"}, "webap01.a.local", True, False),
        ({"name": "", "hostname": "webap010"}, "webap01", False, False),
        # IP 비교 불변
        ({"name": "x", "hostname": "y", "ipaddress": "10.1.2.3"}, "10.1.2.3", True, True),
        # 정규형이 빈 입력은 종전 비교
        ({"name": "-", "hostname": ""}, "-", True, True),
    ],
)
def test_row_matches_on_off(row: dict, value: str, off: bool, on: bool) -> None:
    assert hc._row_matches(row, value) is off
    assert hc._row_matches(row, value, canonical=False) is off
    assert hc._row_matches(row, value, canonical=True) is on


def test_verify_selected_server_reads_flag() -> None:
    rows = [{"hostname": "web_ap01.corp.local", "name": "WEB_AP01(인터넷뱅킹)",
             "ipaddress": "10.1.2.3"}]
    on = _run(hc.verify_selected_server(
        ON, db_id="db1", server_name="web-ap01", hostname=None, probe=_probe_rows(rows)
    ))
    assert on == {"server_name": "WEB_AP01(인터넷뱅킹)", "hostname": "web_ap01.corp.local",
                  "ipaddress": "10.1.2.3"}
    for config in (OFF, MagicMock()):
        off = _run(hc.verify_selected_server(
            config, db_id="db1", server_name="web-ap01", hostname=None, probe=_probe_rows(rows)
        ))
        assert off is None


def test_verify_selected_server_on_two_matches_is_ambiguous() -> None:
    rows = [
        {"hostname": "web_ap01", "name": "WEB_AP01(인터넷뱅킹)", "ipaddress": ""},
        {"hostname": "web-ap01", "name": "WEB-AP01(대외)", "ipaddress": ""},
    ]
    out = _run(hc.verify_selected_server(
        ON, db_id="db1", server_name="webap01", hostname=None, probe=_probe_rows(rows)
    ))
    assert out == {"ambiguous": True}
