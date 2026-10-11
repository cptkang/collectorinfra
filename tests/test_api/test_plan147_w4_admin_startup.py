"""plans/147 W4 — 기동 정합 점검 배선 · 관리 API · 대시보드 카드(D-322 ⑥).

관리 API = `GET /api/v1/admin/apm/sources`.

  1. 기동 점검: APM 활성 ∧ 소스 ≥2면 `gateway_health` 1회 → 사용 가능 집합 설정 · 로그 1줄
     (비밀 값 0)
  2. 게이트웨이 다운·시간 초과 → 기동 계속 · 집합 None(전 소스 사용 가능)
  3. APM 비활성·소스 1개 배포 → 게이트웨이 호출 0 · 집합 미설정
  4. 관리 API — 관리자 전용(사용자 토큰 403 · 미인증 401) · 재점검은 보고만(집합 불변) · URL·토큰 0
  5. 대시보드 카드 마크업·JS 배선
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import server
from src.api.routes import admin
from src.config import DBHubConfig
from src.domain.user import User, UserRole, UserStatus
from src.orchestration import apm_query as aq
from src.routing import apm_source_select as sel
from src.routing.registry import SourceSpec, get_registry

GW_URL = "http://gw-secret-host.example:9096/sse"
GW_TOKEN = "tok-very-secret-p147-w4"
ADMIN_SECRET = "admin-secret-p147"
AUTH_SECRET = "auth-secret-p147"
_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"


def _row(sid: str, *, reachable: bool = True) -> dict[str, Any]:
    return {"source_id": sid, "status": "ok" if reachable else "degraded",
            "jennifer_configured": True, "jennifer_reachable": reachable,
            "domain_count": 1 if reachable else None, "allowlist_size": 41, "api_calls_total": 1}


def _health(*ids: str, unreachable: tuple[str, ...] = ()) -> dict[str, Any]:
    rows = [_row(s, reachable=s not in unreachable) for s in ids]
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-08T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": "gateway_health",
            "limits": [], "status": "ok", "poller": {"enabled": False}}


def _config(*, apm: bool = True, auth: bool = False) -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": GW_URL} if apm else {},
                        source_tokens=json.dumps({"apm": GW_TOKEN}), source_call_timeout=10.0)
    return SimpleNamespace(dbhub=dbhub,
                           auth=SimpleNamespace(enabled=auth, jwt_secret=AUTH_SECRET),
                           admin=SimpleNamespace(jwt_secret=ADMIN_SECRET))


class _Gateway:
    """모의 게이트웨이 세션 공장 — 호출 기록 · 봉투/예외/지연 주입."""

    def __init__(self, envelope: dict | None = None, *, exc: Exception | None = None,
                 delay: float = 0.0, connect_exc: Exception | None = None) -> None:
        self.envelope, self.exc, self.delay, self.connect_exc = envelope, exc, delay, connect_exc
        self.calls: list[tuple[str, dict]] = []
        self.headers: list[dict | None] = []

    @asynccontextmanager
    async def __call__(self, url, headers):
        self.headers.append(headers)
        if self.connect_exc is not None:
            raise self.connect_exc
        gw = self

        class _S:
            async def call_tool(self, name, arguments):
                gw.calls.append((name, dict(arguments)))
                if gw.delay:
                    await asyncio.sleep(gw.delay)
                if gw.exc is not None:
                    raise gw.exc
                return gw.envelope

        yield _S()


@pytest.fixture
def available(monkeypatch) -> dict[str, Any]:
    """사용 가능 집합 보관 함수(W2 `apm_source_select`)를 기록기로 바꾼다."""
    box: dict[str, Any] = {"value": None, "set_calls": 0}

    def _set(ids):
        box["set_calls"] += 1
        box["value"] = None if ids is None else frozenset(ids)

    monkeypatch.setattr(sel, "set_available_apm_sources", _set, raising=False)
    monkeypatch.setattr(sel, "get_available_apm_sources", lambda: box["value"], raising=False)
    return box


def _use(monkeypatch, gateway: _Gateway) -> _Gateway:
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gateway)
    return gateway


# ─── 1. 기동 점검 ─────────────────────────────────────────────────────────────


def test_lifespan_calls_the_startup_check() -> None:
    """lifespan 이 기동 단계에서 점검을 1회 부르고 보고서를 app.state 에 둔다."""
    src = inspect.getsource(server.lifespan)
    assert "app.state.apm_source_report = await _check_apm_sources(config)" in src


async def test_startup_check_sets_available_and_logs_one_line(monkeypatch, available,
                                                              caplog) -> None:
    registry_ids = [s.id for s in get_registry().sources_of("apm")]
    assert len(registry_ids) >= 2, "전제: 레지스트리 제니퍼 소스 ≥2"
    gw = _use(monkeypatch, _Gateway(_health(*registry_ids[:-1])))   # 마지막 소스 미연결
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        report = await server._check_apm_sources(_config())
    assert gw.calls == [("gateway_health", {})], "gateway_health 1회"
    assert gw.headers == [{"Authorization": f"Bearer {GW_TOKEN}"}], "본체 APM 토큰으로 부른다"
    assert report is not None and report.registry_only == (registry_ids[-1],)
    assert available["value"] == frozenset(registry_ids[:-1])
    lines = [r for r in caplog.records if r.name == "src.api.server"]
    assert len(lines) == 1 and lines[0].levelno == logging.WARNING
    msg = lines[0].getMessage()
    assert "미연결" in msg and registry_ids[-1] in msg
    assert GW_TOKEN not in caplog.text and GW_URL not in caplog.text and "gw-secret-host" \
        not in caplog.text


async def test_startup_check_consistent_logs_info(monkeypatch, available, caplog) -> None:
    ids = [s.id for s in get_registry().sources_of("apm")]
    _use(monkeypatch, _Gateway(_health(*ids)))
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        report = await server._check_apm_sources(_config())
    assert report is not None and report.consistent
    assert available["value"] == frozenset(ids)
    (line,) = [r for r in caplog.records if r.name == "src.api.server"]
    assert line.levelno == logging.INFO


# ─── 2. 게이트웨이 다운·시간 초과 ─────────────────────────────────────────────


@pytest.mark.parametrize("gateway", [
    _Gateway(connect_exc=ConnectionRefusedError(f"refused {GW_URL}")),
    _Gateway(_health("bank"), delay=30.0),
    _Gateway(exc=RuntimeError(f"bad {GW_TOKEN}")),
], ids=["down", "timeout", "tool-error"])
async def test_gateway_failure_keeps_startup_and_all_sources(monkeypatch, available, caplog,
                                                             gateway) -> None:
    from src.routing import apm_source_health as ash

    monkeypatch.setattr(ash, "CHECK_TIMEOUT_SECONDS", 0.1)
    _use(monkeypatch, gateway)
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        report = await asyncio.wait_for(server._check_apm_sources(_config()), timeout=5)
    assert report is not None and not report.check_ok
    assert available["set_calls"] == 1 and available["value"] is None, \
        "점검 실패 = 전 소스 사용 가능(선택지를 비우지 않음)"
    assert "점검 실패" in caplog.text
    assert GW_TOKEN not in caplog.text and "gw-secret-host" not in caplog.text


async def test_unexpected_error_in_check_does_not_block_startup(monkeypatch, available,
                                                                caplog) -> None:
    async def boom(config):
        raise RuntimeError(f"secret {GW_TOKEN}")

    monkeypatch.setattr(admin, "check_apm_sources", boom)
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        assert await server._check_apm_sources(_config()) is None
    assert available["set_calls"] == 0
    assert GW_TOKEN not in caplog.text and "RuntimeError" in caplog.text


# ─── 3. 비대상 배포 = 호출 0 ─────────────────────────────────────────────────


async def test_apm_inactive_makes_no_gateway_call(monkeypatch, available, caplog) -> None:
    gw = _use(monkeypatch, _Gateway(_health("bank", "common")))
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        assert await server._check_apm_sources(_config(apm=False)) is None
    assert gw.calls == [] and gw.headers == []
    assert available["set_calls"] == 0
    assert not [r for r in caplog.records if r.name == "src.api.server"]


class _OneSourceRegistry:
    def __init__(self) -> None:
        self._base = get_registry()

    def sources_of(self, system: str):
        return (SourceSpec(id="bank", label="은행존 제니퍼", zone="bankjon"),)

    def __getattr__(self, name):
        return getattr(self._base, name)


async def test_single_source_deployment_makes_no_gateway_call(monkeypatch, available) -> None:
    monkeypatch.setattr(admin, "get_registry", lambda: _OneSourceRegistry())
    gw = _use(monkeypatch, _Gateway(_health("bank")))
    assert await server._check_apm_sources(_config()) is None
    assert gw.calls == [] and available["set_calls"] == 0


# ─── 4. 관리 API ─────────────────────────────────────────────────────────────


def _exp() -> datetime:
    return datetime.now(UTC) + timedelta(hours=1)


def _user_token(role: str = "user") -> str:
    return jwt.encode({"sub": "u1", "name": "U", "role": role, "type": "user", "exp": _exp()},
                      AUTH_SECRET, algorithm="HS256")


def _admin_token() -> str:
    return jwt.encode({"sub": "ops", "type": "admin", "exp": _exp()}, ADMIN_SECRET,
                      algorithm="HS256")


def _client(config, report=None) -> TestClient:
    app = FastAPI()
    app.include_router(admin.router, prefix="/api/v1")
    user = User(user_id="u1", username="U", hashed_password="x", role=UserRole.USER,
                status=UserStatus.ACTIVE)
    app.state.config = config
    app.state.user_repo = SimpleNamespace(get_by_user_id=AsyncMock(return_value=user))
    app.state.apm_source_report = report
    return TestClient(app)


def test_admin_api_rejects_user_token_and_anonymous(monkeypatch, available) -> None:
    gw = _use(monkeypatch, _Gateway(_health("bank", "common", "legacy")))
    client = _client(_config(auth=True))
    assert client.get("/api/v1/admin/apm/sources").status_code == 401
    r = client.get("/api/v1/admin/apm/sources?refresh=1",
                   headers={"Authorization": f"Bearer {_user_token()}"})
    assert r.status_code == 403
    # 사용자 토큰을 운영자 시크릿 경로로 올려도 통과하지 않는다(type 클레임 검증 · D-070)
    forged = jwt.encode({"sub": "u1", "type": "user", "exp": _exp()}, ADMIN_SECRET,
                        algorithm="HS256")
    r = client.get("/api/v1/admin/apm/sources", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code in (401, 403)
    assert gw.calls == [], "거부된 요청은 재점검을 부르지 않는다"


async def test_admin_api_returns_stored_report(monkeypatch, available) -> None:
    ids = [s.id for s in get_registry().sources_of("apm")]
    _use(monkeypatch, _Gateway(_health(*ids[:-1], "drsite", unreachable=(ids[0],))))
    report = await server._check_apm_sources(_config())
    gw = _use(monkeypatch, _Gateway(_health(*ids)))
    client = _client(_config(auth=True), report)
    r = client.get("/api/v1/admin/apm/sources",
                   headers={"Authorization": f"Bearer {_admin_token()}"})
    assert r.status_code == 200
    body = r.json()
    assert gw.calls == [], "refresh 없으면 보관된 보고서만"
    assert body["active"] is True and body["reason"] is None and body["refreshed"] is False
    assert body["applied_available"] == ids[:-1]
    rep = body["report"]
    assert rep["registry_only"] == [ids[-1]] and rep["gateway_only"] == ["drsite"]
    assert rep["unreachable"] == [ids[0]]
    assert [s["id"] for s in rep["sources"]] == [*ids, "drsite"]
    assert set(rep["sources"][0]) == {"id", "label", "zone", "term_count", "in_registry",
                                      "gateway_configured", "reachable", "domain_count", "status"}
    assert GW_TOKEN not in r.text and "gw-secret-host" not in r.text and "http" not in r.text


async def test_admin_refresh_reports_only_and_keeps_applied_set(monkeypatch, available) -> None:
    ids = [s.id for s in get_registry().sources_of("apm")]
    _use(monkeypatch, _Gateway(_health(*ids[:-1])))
    report = await server._check_apm_sources(_config())
    gw = _use(monkeypatch, _Gateway(_health(*ids)))          # 게이트웨이를 고친 뒤 재점검
    client = _client(_config(auth=True), report)
    r = client.get("/api/v1/admin/apm/sources?refresh=1",
                   headers={"Authorization": f"Bearer {_admin_token()}"})
    body = r.json()
    assert gw.calls == [("gateway_health", {})]
    assert body["refreshed"] is True and body["report"]["consistent"] is True
    assert body["applied_available"] == ids[:-1], "재점검은 보고만 — 집합은 재기동 시 갱신"
    assert available["set_calls"] == 1


def test_admin_api_apm_inactive(monkeypatch, available) -> None:
    gw = _use(monkeypatch, _Gateway(_health("bank")))
    client = _client(_config(apm=False, auth=False))
    body = client.get("/api/v1/admin/apm/sources?refresh=1").json()
    assert body == {"active": False, "reason": "apm_inactive", "refreshed": False,
                    "applied_available": None, "report": None}
    assert gw.calls == []


def test_admin_api_single_source(monkeypatch, available) -> None:
    monkeypatch.setattr(admin, "get_registry", lambda: _OneSourceRegistry())
    _use(monkeypatch, _Gateway(_health("bank")))
    body = _client(_config()).get("/api/v1/admin/apm/sources").json()
    assert body["active"] is False and body["reason"] == "single_source"


# ─── 5. 대시보드 카드 ────────────────────────────────────────────────────────


def test_dashboard_card_markup_and_js_wiring() -> None:
    html = (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    js = (_STATIC / "js" / "admin.js").read_text(encoding="utf-8")
    for ident in ('data-tab="apmsources"', 'id="tab-apmsources"', 'id="apmSourcesCard"',
                  'id="refreshApmSourcesBtn"', 'id="apmSourcesSummary"', 'id="apmSourcesTable"',
                  'id="apmSourcesBody"', "<h2>제니퍼 소스</h2>"):
        assert ident in html, ident
    assert '"/api/v1/admin/apm/sources"' in js and '"?refresh=1"' in js
    for status in ("registry_only", "gateway_only", "unreachable", "unknown"):
        assert status + ":" in js, status
    assert "비활성" in js


# ─── 6. W2 보관 함수와의 실제 결합 ──────────────────────────────────────────


async def test_startup_check_feeds_real_available_store(monkeypatch) -> None:
    """기동 점검 결과가 W2 `apm_source_select`의 실제 보관 함수에 실린다(대역 없이)."""
    before = sel.get_available_apm_sources()
    ids = [s.id for s in get_registry().sources_of("apm")]
    _use(monkeypatch, _Gateway(_health(*ids[:-1])))
    try:
        await server._check_apm_sources(_config())
        assert sel.get_available_apm_sources() == frozenset(ids[:-1])
        _use(monkeypatch, _Gateway(connect_exc=ConnectionRefusedError("down")))
        await server._check_apm_sources(_config())
        assert sel.get_available_apm_sources() is None
    finally:
        sel.set_available_apm_sources(before)
