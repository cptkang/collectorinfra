"""plans/211 W2 — 폴스타 앵커 해석의 호스트 정규형 일치 (D-363).

- 사본 ``mcp_server.host_canonical``이 본체 정본(``src/domain/host_names.py``)과 같은 결과를 내는지
  §5 벡터 표와 SQL 조건 문자열로 고정한다(본체는 import하지 않는다 — D-139).
- ``build_anchor_lookup_sql``: 플래그 off = 현행 SQL 비트 동일 · on = 두 칸 L0 조건(괄호 꼬리 포함)
  · 빈 키·IP 입력은 현행 완전 일치 유지 · PG/DB2 방언(스키마 한정·행 제한).
- 플래그: ``ServerConfig`` 기본 off · env/toml 로딩 · ``resolve_polestar_anchor``까지 전달.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from mcp_server.config import AppServerConfig, ServerConfig, _apply_env_overrides, _load_toml

from mcp_server import host_canonical as hc

try:
    from mcp_server.security import validate_readonly

    from mcp_server import polestar_tools as pt

    HAS_MCP = True
except ImportError:
    HAS_MCP = False

needs_mcp = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")

_MODULE = Path(hc.__file__)


# =====================================================================
# §5 벡터 표 (공통 사양 정본)
# =====================================================================

_MATCH_VECTORS = [
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


@pytest.mark.parametrize(("a", "b", "expected"), _MATCH_VECTORS)
def test_host_keys_match_vectors(a: str, b: str, expected: bool) -> None:
    assert hc.host_keys_match(a, b) is expected
    assert hc.host_keys_match(b, a) is expected


def test_split_business_suffix_vector() -> None:
    assert hc.split_business_suffix("webap01(인터넷뱅킹)") == ("webap01", "인터넷뱅킹")
    assert hc.split_business_suffix("webap01 (인터넷뱅킹)") == ("webap01", "인터넷뱅킹")
    assert hc.split_business_suffix("webap01") == ("webap01", "")


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("(인터넷뱅킹)", ""),
        ("web%01", ""),
        ("WEBAP01.", "webap01"),
        ("web..ap01", ""),
        ("-", ""),
        ("", ""),
        (None, ""),
        ("웹서버01", ""),
        ("10.1.2.3", "10.1.2.3"),
        ("2001:DB8::0001", "2001:db8::1"),
        ("WEB-AP01.Corp.Local", "webap01.corp.local"),
    ],
)
def test_canonical_host_key_vectors(value: str | None, key: str) -> None:
    assert hc.canonical_host_key(value) == key


# =====================================================================
# SQL 조건 문자열 — 본체 build_canonical_host_condition과 같은 문자열
# =====================================================================

_N = "REPLACE(REPLACE(LOWER(TRIM(CR.NAME)), '-', ''), '_', '')"


def test_condition_single_label_with_suffix() -> None:
    cond = hc.build_canonical_host_condition("CR.NAME", ["WEB-AP01"], business_suffix=True)
    assert cond == (
        f"({_N} = 'webap01' OR {_N} LIKE 'webap01.%'"
        f" OR {_N} LIKE 'webap01(%' OR {_N} LIKE 'webap01 (%')"
    )


def test_condition_fqdn_without_suffix() -> None:
    cond = hc.build_canonical_host_condition(
        "CR.NAME", ["webap01.corp.local"], business_suffix=False
    )
    assert cond == f"({_N} = 'webap01.corp.local' OR {_N} = 'webap01')"


def test_condition_multi_values_dedup_and_skip() -> None:
    cond = hc.build_canonical_host_condition(
        "CR.NAME", ["a1", "A-1", "10.0.0.1", "web%01", "b2"], business_suffix=False
    )
    assert cond == (
        f"(({_N} = 'a1' OR {_N} LIKE 'a1.%') OR ({_N} = 'b2' OR {_N} LIKE 'b2.%'))"
    )


@pytest.mark.parametrize("value", ["web%01", "웹서버01", "(인터넷뱅킹)", "", "10.1.2.3"])
def test_condition_none_for_non_key_or_ip(value: str) -> None:
    assert hc.build_canonical_host_condition("CR.NAME", [value], business_suffix=True) is None


# =====================================================================
# 앵커 조회 SQL — off 비트 동일 · on L0 · 방언
# =====================================================================


def _legacy_anchor_sql(is_db2: bool, lit: str, limit: int = 10) -> str:
    """plans/211 이전 ``build_anchor_lookup_sql``의 출력(비트 동일 기준)."""
    schema = "POLESTAR" if is_db2 else "polestar"
    tail = f"FETCH FIRST {limit} ROWS ONLY" if is_db2 else f"LIMIT {limit}"
    return (
        "SELECT\n"
        "    CR.ID AS resource_id,\n"
        "    CR.NAME AS name,\n"
        "    CR.HOSTNAME AS hostname,\n"
        "    CR.IPADDRESS AS ipaddress\n"
        f"FROM {schema}.cmm_resource CR\n"
        "WHERE CR.RESOURCE_TYPE = 'server.Server'\n"
        "  AND CR.DTIME IS NULL\n"
        f"  AND (CR.NAME = {lit} OR CR.HOSTNAME = {lit})\n"
        "ORDER BY CR.NAME\n"
        f"{tail}"
    )


@needs_mcp
@pytest.mark.parametrize("is_db2", [False, True])
def test_anchor_sql_off_is_bit_identical(is_db2: bool) -> None:
    assert pt.build_anchor_lookup_sql(is_db2, "web01") == _legacy_anchor_sql(is_db2, "'web01'")
    assert pt.build_anchor_lookup_sql(is_db2, "web01", canonical=False) == _legacy_anchor_sql(
        is_db2, "'web01'"
    )


@needs_mcp
@pytest.mark.parametrize("is_db2", [False, True])
def test_anchor_sql_on_uses_l0_on_both_columns(is_db2: bool) -> None:
    sql = pt.build_anchor_lookup_sql(is_db2, "WEB-AP01", canonical=True)
    by_name = hc.build_canonical_host_condition("CR.NAME", ["WEB-AP01"], business_suffix=True)
    by_host = hc.build_canonical_host_condition("CR.HOSTNAME", ["WEB-AP01"], business_suffix=True)
    assert f"  AND ({by_name} OR {by_host})\n" in sql
    assert "LIKE 'webap01(%'" in sql and "LIKE 'webap01 (%'" in sql
    assert "CR.NAME = 'WEB-AP01'" not in sql
    if is_db2:
        assert "FROM POLESTAR.cmm_resource CR\n" in sql
        assert sql.endswith("FETCH FIRST 10 ROWS ONLY")
        assert "LIMIT" not in sql
    else:
        assert "FROM polestar.cmm_resource CR\n" in sql
        assert sql.endswith("LIMIT 10")
    assert "ORDER BY CR.NAME\n" in sql


@needs_mcp
def test_anchor_sql_on_fqdn_input() -> None:
    sql = pt.build_anchor_lookup_sql(False, "webap01.corp.local", canonical=True)
    n_host = "REPLACE(REPLACE(LOWER(TRIM(CR.HOSTNAME)), '-', ''), '_', '')"
    assert f"{_N} = 'webap01.corp.local' OR {_N} = 'webap01'" in sql
    assert f"{n_host} = 'webap01.corp.local' OR {n_host} = 'webap01'" in sql


@needs_mcp
@pytest.mark.parametrize("is_db2", [False, True])
@pytest.mark.parametrize("value", ["web%01", "웹서버01", "10.1.2.3", "(인터넷뱅킹)"])
def test_anchor_sql_on_keeps_exact_match_for_non_key(is_db2: bool, value: str) -> None:
    on = pt.build_anchor_lookup_sql(is_db2, value, canonical=True)
    assert on == pt.build_anchor_lookup_sql(is_db2, value, canonical=False)
    assert "LIKE" not in on and "REPLACE(" not in on


@needs_mcp
@pytest.mark.parametrize("is_db2", [False, True])
def test_anchor_sql_on_passes_readonly_guard(is_db2: bool) -> None:
    """on SQL도 실행 지점 읽기 전용 가드(``validate_readonly``)를 통과해야 한다(함수형 REPLACE)."""
    validate_readonly(pt.build_anchor_lookup_sql(is_db2, "web-ap01", canonical=True))


# =====================================================================
# 플래그 — 기본 off · 로딩 · resolve_polestar_anchor까지 전달
# =====================================================================


def test_flag_default_off() -> None:
    assert ServerConfig().host_canonical_match_enabled is False
    cfg = AppServerConfig()
    _apply_env_overrides(cfg, env={})
    assert cfg.server.host_canonical_match_enabled is False


@needs_mcp
def test_module_flag_default_off() -> None:
    assert pt._HOST_CANONICAL_MATCH is False


@pytest.mark.parametrize(("raw", "expected"), [("true", True), ("1", True), ("false", False)])
def test_flag_env_override(raw: str, expected: bool) -> None:
    cfg = AppServerConfig()
    _apply_env_overrides(cfg, env={"HOST_CANONICAL_MATCH_ENABLED": raw})
    assert cfg.server.host_canonical_match_enabled is expected


def test_flag_toml_key(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[server]\nhost_canonical_match_enabled = true\n", encoding="utf-8")
    assert _load_toml(path).server.host_canonical_match_enabled is True
    path.write_text("[server]\n", encoding="utf-8")
    assert _load_toml(path).server.host_canonical_match_enabled is False


class _FakePool:
    """DBPoolManager 대역 — 실행 SQL을 기록하고 주어진 행을 돌려준다."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, str]] = []

    def is_source_active(self, name: str) -> bool:
        return name == "pg"

    def get_source_type(self, name: str) -> str:
        return "postgresql"

    def get_active_sources(self) -> list[str]:
        return ["pg"]

    async def execute(self, name: str, sql: str) -> list[dict[str, Any]]:
        self.calls.append((name, sql))
        return self.rows


@needs_mcp
@pytest.mark.parametrize("enabled", [False, True])
async def test_resolve_anchor_uses_module_flag(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setattr(pt, "_HOST_CANONICAL_MATCH", enabled)
    pool = _FakePool([{"name": "WEB-AP01(인터넷뱅킹)", "hostname": "webap01", "ipaddress": "1"}])
    res = await pt.resolve_polestar_anchor(pool, "webap01", source="pg")
    assert res.status == pt.ANCHOR_RESOLVED
    (_, sql), = pool.calls
    assert sql == pt.build_anchor_lookup_sql(False, "webap01", canonical=enabled)
    assert ("LIKE 'webap01(%'" in sql) is enabled


@needs_mcp
async def test_resolve_anchor_two_rows_stays_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pt, "_HOST_CANONICAL_MATCH", True)
    pool = _FakePool([
        {"name": "webap01", "hostname": "webap01", "ipaddress": "1"},
        {"name": "WEBAP01.corp.local", "hostname": "webap01.corp.local", "ipaddress": "2"},
    ])
    res = await pt.resolve_polestar_anchor(pool, "webap01", source="pg")
    assert res.status == pt.ANCHOR_AMBIGUOUS
    assert res.anchor is None and len(res.candidates) == 2


@needs_mcp
def test_set_host_canonical_match(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pt, "_HOST_CANONICAL_MATCH", False)
    pt.set_host_canonical_match(True)
    assert pt._HOST_CANONICAL_MATCH is True
    pt.set_host_canonical_match(False)
    assert pt._HOST_CANONICAL_MATCH is False


# =====================================================================
# 경계 — 사본 모듈은 본체를 import하지 않는다(D-139)
# =====================================================================


def test_copy_module_imports_stdlib_only() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
    assert "src" not in roots
    assert roots <= {"__future__", "ipaddress", "re", "collections"}
