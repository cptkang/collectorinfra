"""plans/215 W2 — 설정(§7 전 키 기본값·검증·의존 속성) · DDL 정합 · 기동 배선(플래그 off = 0)."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from noise_gate.infrastructure import biz_service_cache as cache_mod
from noise_gate.tests.test_plan215_w2_cache import _Redis
from noise_gate.tests.test_plan215_w2_repository import _Conn, _Pool
from src.api import server
from src.config import NoiseGateConfig

ROOT = Path(__file__).resolve().parents[1]
_DEFAULTS: dict[str, Any] = {
    "biz_service_enabled": False,
    "biz_service_max_members": 200,
    "biz_monitor_enabled": False,
    "biz_monitor_metrics_enabled": False,
    "biz_monitor_max_services": 20,
    "biz_monitor_max_apm_targets": 100,
    "biz_monitor_apm_interval_seconds": 60,
    "biz_monitor_sms_interval_seconds": 600,
    "biz_monitor_alarm_window_minutes": 60,
    "biz_diagnosis_enabled": False,
    "biz_diagnosis_max_apm_targets": 50,
    "biz_diagnosis_window_minutes": 15,
    "biz_investigation_context_enabled": False,
    "biz_investigation_max_sms": 10,
    "biz_investigation_max_apm": 8,
    "biz_monitor_changes_enabled": False,
    "biz_monitor_change_lookback_minutes": 60,
    "biz_monitor_history_enabled": False,
    "biz_monitor_history_max_members": 20,
    "biz_monitor_history_max_concurrency": 2,
}
_FLAGS = [k for k, v in _DEFAULTS.items() if v is False]
_ACTIVE = (
    "biz_service_active", "biz_monitor_active", "biz_monitor_metrics_active",
    "biz_monitor_changes_active", "biz_monitor_history_active", "biz_diagnosis_active",
    "biz_investigation_context_active",
)


@pytest.fixture
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _DEFAULTS:
        monkeypatch.delenv(f"NOISE_{key.upper()}", raising=False)


def _cfg_env(monkeypatch: pytest.MonkeyPatch, **env: str) -> NoiseGateConfig:
    for key, value in env.items():
        monkeypatch.setenv(f"NOISE_{key.upper()}", value)
    return NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]


# ── 설정 ────────────────────────────────────────────────────────────────
def test_config_defaults_all_off(_clean_env: None) -> None:
    cfg = NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]
    for key, value in _DEFAULTS.items():
        assert getattr(cfg, key) == value, key
    assert len(_FLAGS) == 7
    assert all(getattr(cfg, name) is False for name in _ACTIVE)


@pytest.mark.parametrize(("key", "bad", "good"), [
    ("biz_monitor_apm_interval_seconds", "29", "30"),
    ("biz_monitor_sms_interval_seconds", "299", "300"),
    ("biz_monitor_history_max_members", "21", "20"),
    ("biz_monitor_history_max_members", "0", "1"),
    ("biz_service_max_members", "0", "1"),
    ("biz_monitor_history_max_concurrency", "0", "1"),
])
def test_config_bounds(
    _clean_env: None, monkeypatch: pytest.MonkeyPatch, key: str, bad: str, good: str
) -> None:
    with pytest.raises(ValidationError):
        _cfg_env(monkeypatch, **{key: bad})
    assert getattr(_cfg_env(monkeypatch, **{key: good}), key) == int(good)


def test_dependent_flags_need_their_parents(
    _clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 하위 플래그만 켜면 아무것도 유효하지 않다
    cfg = _cfg_env(monkeypatch, **{k: "true" for k in _FLAGS if k != "biz_service_enabled"})
    assert all(getattr(cfg, name) is False for name in _ACTIVE)
    # service만 켜면 context는 유효 · monitor 계열은 무효
    monkeypatch.setenv("NOISE_BIZ_SERVICE_ENABLED", "true")
    monkeypatch.setenv("NOISE_BIZ_MONITOR_ENABLED", "false")
    cfg = NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]
    assert cfg.biz_service_active and cfg.biz_investigation_context_active
    assert not any(getattr(cfg, n) for n in (
        "biz_monitor_active", "biz_monitor_metrics_active", "biz_monitor_changes_active",
        "biz_monitor_history_active", "biz_diagnosis_active",
    ))
    # 전부 켜면 전부 유효
    monkeypatch.setenv("NOISE_BIZ_MONITOR_ENABLED", "true")
    cfg = NoiseGateConfig(_env_file=None)  # type: ignore[call-arg]
    assert all(getattr(cfg, name) is True for name in _ACTIVE)


def test_child_flags_off_stay_off_with_parents_on(
    _clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _cfg_env(monkeypatch, biz_service_enabled="true", biz_monitor_enabled="true")
    assert cfg.biz_monitor_active is True
    assert not any(getattr(cfg, n) for n in (
        "biz_monitor_metrics_active", "biz_monitor_changes_active",
        "biz_monitor_history_active", "biz_diagnosis_active", "biz_investigation_context_active",
    ))


# ── DDL ─────────────────────────────────────────────────────────────────
def _statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [re.sub(r"\s+", " ", s).strip() for s in body.split(";") if s.strip()]


def test_server_ddl_matches_ddl_file() -> None:
    file_sql = (ROOT / "ddl" / "biz_service.sql").read_text(encoding="utf-8")
    assert _statements(server._BIZ_SERVICE_DDL) == _statements(file_sql)
    stmts = _statements(file_sql)
    tables = {s.split()[5] for s in stmts if s.startswith("CREATE TABLE")}
    assert tables == {
        "biz_service", "biz_service_member", "biz_service_candidate", "biz_service_run",
        "biz_service_attr", "biz_service_state_log",
    }
    assert all("IF NOT EXISTS" in s for s in stmts)
    joined = " ".join(stmts)
    assert "UNIQUE (kind, member_key)" in joined and "UNIQUE (candidate_key)" in joined
    assert "tier IS NULL OR tier BETWEEN 1 AND 3" in joined
    assert "description VARCHAR(500)" in joined and "owner_org VARCHAR(60)" in joined
    assert "monitored BOOLEAN NOT NULL DEFAULT FALSE" in joined
    assert "version INTEGER NOT NULL DEFAULT 1" in joined


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
    ng = SimpleNamespace() if enabled is None else SimpleNamespace(biz_service_active=enabled)
    return SimpleNamespace(
        noise_gate=ng, redis=SimpleNamespace(host="127.0.0.1", port=6399, password="", db=0),
    )


@pytest.mark.parametrize("enabled", [None, False, 1, "true"])
async def test_flag_off_runs_no_ddl_no_state(enabled: Any) -> None:
    conn = _Conn()
    state = _RecState(auth_pool=_Pool(conn))
    out = await server._start_biz_service(SimpleNamespace(state=state), _cfg(enabled))  # type: ignore[arg-type]
    assert out is None
    assert conn.calls == [] and state.assigned == []


async def test_real_config_flag_off_runs_nothing(_clean_env: None) -> None:
    conn = _Conn()
    state = _RecState(auth_pool=_Pool(conn))
    cfg = SimpleNamespace(noise_gate=NoiseGateConfig(_env_file=None))  # type: ignore[call-arg]
    assert await server._start_biz_service(SimpleNamespace(state=state), cfg) is None  # type: ignore[arg-type]
    assert conn.calls == [] and state.assigned == []


async def test_magicmock_config_is_not_enabled() -> None:
    conn = _Conn()
    state = _RecState(auth_pool=_Pool(conn))
    out = await server._start_biz_service(SimpleNamespace(state=state), MagicMock())  # type: ignore[arg-type]
    assert out is None and conn.calls == []


async def test_flag_on_without_auth_pool_skips() -> None:
    state = _RecState(auth_pool=None)
    out = await server._start_biz_service(SimpleNamespace(state=state), _cfg(True))  # type: ignore[arg-type]
    assert out is None and state.assigned == []


async def test_flag_on_wires_ddl_repo_cleanup_and_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn()
    conn.execute_q.extend(["CREATE", "UPDATE 1"])  # DDL · running → aborted 1건
    redis = _Redis()
    monkeypatch.setattr(cache_mod, "connect_redis", lambda cfg: redis)
    state = _RecState(auth_pool=_Pool(conn))
    got = await server._start_biz_service(SimpleNamespace(state=state), _cfg(True))  # type: ignore[arg-type]
    assert got is redis
    assert state.assigned == ["biz_service_repo", "biz_service_cache"]
    executed = [q for kind, q, _ in conn.calls if kind == "execute"]
    assert executed[0] == server._BIZ_SERVICE_DDL
    assert "biz_service_run SET status" in executed[1]
    assert any("DELETE FROM biz_service_member" in q for q in executed)  # 정리
    assert isinstance(state.biz_service_cache, cache_mod.RedisBizServiceCache)
    assert redis.dump() == {}  # 활성 서비스 0 → 사본도 비어 있다


async def test_flag_on_redis_failure_keeps_going(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn()

    class _Down(_Redis):
        def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> Any:
            raise ConnectionError("down")

    redis = _Down()
    monkeypatch.setattr(cache_mod, "connect_redis", lambda cfg: redis)
    state = _RecState(auth_pool=_Pool(conn))
    got = await server._start_biz_service(SimpleNamespace(state=state), _cfg(True))  # type: ignore[arg-type]
    assert got is redis  # 종료 단계가 닫는다
    assert state.assigned == ["biz_service_repo", "biz_service_cache"]
