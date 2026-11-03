"""plans/210 W1 — 설정 6키 기본값·검증 · DDL 정합 · 기동 배선(플래그 off = DDL·태스크·state 0)."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from src.api import server
from src.config import NoiseGateConfig

from noise_gate.infrastructure import apm_host_map_cache as cache_mod
from noise_gate.tests.test_plan210_w1_cache import _Redis
from noise_gate.tests.test_plan210_w1_repository import _Conn, _Pool

ROOT = Path(__file__).resolve().parents[2]
_KEYS = (
    "ENABLED", "AUTO_ENABLED", "MAX_TARGETS", "LIVE_BUDGET_SECONDS", "RETRY_HOURS",
    "SCAN_PAGE_ROWS",
)


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _KEYS:
        monkeypatch.delenv(f"NOISE_APM_HOST_MAP_{key}", raising=False)


# ── 설정 ────────────────────────────────────────────────────────────────
def test_config_defaults_are_off(_clean_env: None) -> None:
    cfg = NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.apm_host_map_enabled is False
    assert cfg.apm_host_map_auto_enabled is False
    assert cfg.apm_host_map_max_targets == 10
    assert cfg.apm_host_map_live_budget_seconds == 5
    assert cfg.apm_host_map_retry_hours == 24
    assert cfg.apm_host_map_scan_page_rows == 1000


def test_config_env_and_scan_page_rows_bounds(
    _clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NOISE_APM_HOST_MAP_ENABLED", "true")
    monkeypatch.setenv("NOISE_APM_HOST_MAP_SCAN_PAGE_ROWS", "9999")
    cfg = NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.apm_host_map_enabled is True and cfg.apm_host_map_scan_page_rows == 9999
    for bad in ("0", "10000"):
        monkeypatch.setenv("NOISE_APM_HOST_MAP_SCAN_PAGE_ROWS", bad)
        with pytest.raises(ValidationError):
            NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]


# ── DDL ─────────────────────────────────────────────────────────────────
def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [re.sub(r"\s+", " ", s).strip() for s in body.split(";") if s.strip()]


def test_server_ddl_matches_ddl_file() -> None:
    file_sql = (ROOT / "ddl" / "apm_host_map.sql").read_text(encoding="utf-8")
    assert _statements(server._APM_HOST_MAP_DDL) == _statements(file_sql)
    stmts = _statements(file_sql)
    tables = {s.split()[5] for s in stmts if s.startswith("CREATE TABLE")}
    assert tables == {
        "apm_host_map", "apm_host_map_scan_run", "apm_host_map_snapshot_current",
        "apm_inventory", "sms_inventory", "itam_snapshot_app", "itam_snapshot_server",
    }
    assert all("IF NOT EXISTS" in s for s in stmts)
    assert "UNIQUE (source_id, domain_id, scope, match_key)" in stmts[0]


# ── 기동 배선 ───────────────────────────────────────────────────────────
class _RecState:
    def __init__(self, **initial: Any) -> None:
        object.__setattr__(self, "assigned", [])
        for k, v in initial.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, name: str, value: Any) -> None:
        self.assigned.append(name)
        object.__setattr__(self, name, value)


def _cfg(enabled: Any) -> SimpleNamespace:
    ng = SimpleNamespace() if enabled is None else SimpleNamespace(apm_host_map_enabled=enabled)
    return SimpleNamespace(
        noise_gate=ng, redis=SimpleNamespace(host="127.0.0.1", port=6399, password="", db=0),
    )


@pytest.mark.parametrize("enabled", [None, False])
async def test_flag_off_runs_no_ddl_no_task_no_state(enabled: Any) -> None:
    conn = _Conn()
    state = _RecState(auth_pool=_Pool(conn))
    out = await server._start_apm_host_map(SimpleNamespace(state=state), _cfg(enabled))  # type: ignore[arg-type]
    assert out == (None, None)
    assert conn.calls == [] and state.assigned == []


async def test_magicmock_config_is_not_enabled() -> None:
    conn = _Conn()
    state = _RecState(auth_pool=_Pool(conn))
    out = await server._start_apm_host_map(SimpleNamespace(state=state), MagicMock())  # type: ignore[arg-type]
    assert out == (None, None) and conn.calls == []


async def test_flag_on_without_auth_pool_skips() -> None:
    state = _RecState(auth_pool=None)
    out = await server._start_apm_host_map(SimpleNamespace(state=state), _cfg(True))  # type: ignore[arg-type]
    assert out == (None, None) and state.assigned == []


async def test_flag_on_wires_ddl_repo_cache_and_sync_task(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn()
    conn.execute_q.extend(["CREATE", "UPDATE 1"])  # DDL · running → aborted 1건
    redis = _Redis()
    monkeypatch.setattr(cache_mod, "connect_redis", lambda cfg: redis)
    state = _RecState(auth_pool=_Pool(conn))
    task, got_redis = await server._start_apm_host_map(  # type: ignore[arg-type]
        SimpleNamespace(state=state), _cfg(True),
    )
    try:
        assert got_redis is redis and isinstance(task, asyncio.Task)
        assert state.assigned == ["apm_host_map_repo", "apm_host_map_cache"]
        executed = [q for kind, q, _ in conn.calls if kind == "execute"]
        assert executed[0] == server._APM_HOST_MAP_DDL
        assert "status = $1" in executed[1]  # 남은 running → aborted
        assert sum(1 for q in executed if "CREATE TABLE" in q) == 1
        assert isinstance(state.apm_host_map_cache, cache_mod.RedisApmHostMapCache)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
