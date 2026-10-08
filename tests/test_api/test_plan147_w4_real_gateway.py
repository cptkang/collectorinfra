"""plans/147 W4 AC-4·AC-5 — 실프로세스 게이트웨이로 기동 정합 점검(D-322 ⑥).

목 Open API(이 프로세스 스레드 · 127.0.0.1 임시 포트) ↔ `python -m apm_gateway`(임시 포트 ·
임시 스풀) ↔ 실제 MCP SSE ↔ 본체 기동 점검 `server._check_apm_sources`.

  AC-4: 4번째 소스 `newsrc`를 **설정만으로**(게이트웨이 환경 변수 + 레지스트리 한 행) 붙이면
        기동 점검이 4소스를 사용 가능으로 판정한다(코드 수정 0).
  AC-5: 레지스트리에 있고 게이트웨이에 없는 소스는 미연결로 드러나고 사용 가능 집합에서 빠진다 ·
        게이트웨이에만 있는 소스는 선택 불가로 표시된다.

실 제니퍼·외부 네트워크·실 LLM·실 DB 0 · 포트 9096·9097·9099·8080 미사용.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from src.api import server
from src.api.routes import admin
from src.orchestration import apm_query as aq
from src.routing import apm_source_select as sel
from src.routing.registry import SourceSpec, get_registry
from tests.test_orchestration.test_plan134_w567_body_verify import (
    FORBIDDEN_PORTS,
    GW_ROOT,
    _app_config,
    _free_port,
    _fx,
)

_GW_TOKEN = "tok-chat-p147-w4"
_MOCK_TOKEN = "tok-mock-p147-w4"
_GATEWAY_SOURCES = ("bank", "common", "legacy", "newsrc")
_NEW = SourceSpec(id="newsrc", label="신규 제니퍼", zone="gongjon", terms=("신규",))


@pytest.fixture(scope="module")
def real_gateway(tmp_path_factory):
    """목 Open API + 게이트웨이 실프로세스(소스 4개 — 4번째는 환경 변수만으로 추가) — sse url."""
    pytest.importorskip("mcp")
    scripts = GW_ROOT / "testdata" / "jennifer" / "scripts"
    sys.path.insert(0, str(scripts))
    import mock_openapi

    fixtures = tmp_path_factory.mktemp("fx147w4")
    (fixtures / "fx_00.json").write_text(
        json.dumps(_fx("/api/domain", {"result": [{"domainId": 1000, "name": "d1000"}]})),
        encoding="utf-8")
    mock, _state = mock_openapi.make_server(fixtures, _MOCK_TOKEN, "connected")
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    mock_url = f"http://127.0.0.1:{mock.server_address[1]}"
    port = _free_port()
    assert port not in FORBIDDEN_PORTS
    spool = tmp_path_factory.mktemp("spool147w4")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JENNIFER_", "APM_"))}
    env.update({
        "JENNIFER_SOURCES": json.dumps(list(_GATEWAY_SOURCES)), "JENNIFER_API_URL": "",
        "JENNIFER_API_TOKEN": "",
        **{f"JENNIFER_{s.upper()}_API_URL": mock_url for s in _GATEWAY_SOURCES},
        **{f"JENNIFER_{s.upper()}_API_TOKEN": _MOCK_TOKEN for s in _GATEWAY_SOURCES},
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
        yield f"http://127.0.0.1:{port}/sse"
    finally:
        proc.terminate()
        try:
            proc.wait(15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        mock.shutdown()
        mock.server_close()
        sys.path.remove(str(scripts))


class _Registry:
    """레지스트리 소스 표만 바꾼 대역(그 밖은 실 레지스트리)."""

    def __init__(self, sources: tuple[SourceSpec, ...]) -> None:
        self._base = get_registry()
        self._sources = sources

    def sources_of(self, system: str) -> tuple[SourceSpec, ...]:
        return self._sources if system == aq.APM_SYSTEM else self._base.sources_of(system)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


@pytest.fixture
def available(monkeypatch) -> dict[str, Any]:
    box: dict[str, Any] = {"value": "unset"}

    def _set(ids):
        box["value"] = None if ids is None else frozenset(ids)

    monkeypatch.setattr(sel, "set_available_apm_sources", _set, raising=False)
    monkeypatch.setattr(aq, "_SESSION_FACTORY", None)   # 실 MCP SSE 세션
    return box


async def test_fourth_source_added_by_settings_only_is_available(real_gateway, available,
                                                                 monkeypatch, caplog) -> None:
    """AC-4 — 게이트웨이 환경 변수 + 레지스트리 한 행만으로 4소스 사용 가능."""
    base = get_registry().sources_of(aq.APM_SYSTEM)
    assert [s.id for s in base] == ["bank", "common", "legacy"], "전제: 레지스트리 3소스"
    monkeypatch.setattr(admin, "get_registry", lambda: _Registry((*base, _NEW)))
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        report = await server._check_apm_sources(_app_config(real_gateway, _GW_TOKEN))
    assert report is not None and report.check_ok, report and report.error
    assert report.consistent
    assert available["value"] == frozenset(_GATEWAY_SOURCES)
    new = next(s for s in report.sources if s.id == "newsrc")
    assert (new.status, new.reachable, new.domain_count, new.label, new.term_count) == \
        ("ok", True, 1, "신규 제니퍼", 1)
    assert _GW_TOKEN not in caplog.text and _MOCK_TOKEN not in caplog.text
    assert real_gateway not in caplog.text


async def test_registry_only_and_gateway_only_through_real_gateway(real_gateway, available,
                                                                   monkeypatch, caplog) -> None:
    """AC-5 — 레지스트리∖게이트웨이(`ghost`)는 미연결 · 게이트웨이∖레지스트리(`newsrc`)는
    선택 불가."""
    base = get_registry().sources_of(aq.APM_SYSTEM)
    ghost = SourceSpec(id="ghost", label="미연결 제니퍼", zone="bankjon", terms=("유령",))
    monkeypatch.setattr(admin, "get_registry", lambda: _Registry((*base, ghost)))
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        report = await server._check_apm_sources(_app_config(real_gateway, _GW_TOKEN))
    assert report is not None and report.check_ok
    assert report.registry_only == ("ghost",) and report.gateway_only == ("newsrc",)
    assert available["value"] == frozenset({"bank", "common", "legacy"})
    (line,) = [r for r in caplog.records if r.name == "src.api.server"]
    assert line.levelno == logging.WARNING
    assert "미연결" in line.getMessage() and "ghost" in line.getMessage()
    assert "newsrc" in line.getMessage()
    assert _GW_TOKEN not in caplog.text and real_gateway not in caplog.text


async def test_gateway_down_keeps_all_registry_sources(available, caplog) -> None:
    """닫힌 포트(게이트웨이 다운) → 점검 실패 · 기동 계속 · 전 소스 사용 가능."""
    url = f"http://127.0.0.1:{_free_port()}/sse"
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        report = await server._check_apm_sources(_app_config(url, _GW_TOKEN))
    assert report is not None and not report.check_ok
    assert available["value"] is None
    assert "점검 실패" in caplog.text and url not in caplog.text and _GW_TOKEN not in caplog.text
