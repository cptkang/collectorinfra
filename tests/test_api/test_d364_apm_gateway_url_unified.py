"""D-364 — 노이즈 게이트의 APM 게이트웨이 주소를 채팅과 같은 MCP_SOURCE_ENDPOINTS["apm"]으로 통합.

옛 `NOISE_APM_MCP_URL`은 폐지됐다 — 값을 읽지 않고, 남아 있으면 기동 로그에 경고를 남긴다.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from pydantic import SecretStr

from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
from src.api.server import _warn_retired_apm_url
from src.config import NoiseGateConfig


def _cfg(apm_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        dbhub=SimpleNamespace(source_endpoints={"apm": apm_url} if apm_url else {}),
        noise_gate=SimpleNamespace(apm_mcp_token=SecretStr("")),
    )


def test_noise_gate_config_has_no_url_field_and_ignores_retired_key(monkeypatch):
    monkeypatch.setenv("NOISE_APM_MCP_URL", "http://old.test:9096/sse")
    cfg = NoiseGateConfig(_env_file=None)
    assert "apm_mcp_url" not in NoiseGateConfig.model_fields
    assert not hasattr(cfg, "apm_mcp_url")


def test_warns_when_retired_key_in_dotenv(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOISE_APM_MCP_URL", raising=False)
    (tmp_path / ".env").write_text("NOISE_APM_MCP_URL=http://old.test:9096/sse\n", "utf-8")
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        _warn_retired_apm_url(_cfg())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("NOISE_APM_MCP_URL은 폐지" in m and "비어 있음" in m for m in msgs)
    assert not any("old.test" in m for m in msgs)  # 값은 로그에 싣지 않는다


def test_warns_when_retired_key_in_environ(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NOISE_APM_MCP_URL", "http://old.test:9096/sse")
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        _warn_retired_apm_url(_cfg("http://127.0.0.1:9096/sse"))
    assert any("(현재 설정됨)" in r.getMessage() for r in caplog.records)


def test_no_warning_without_retired_key(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NOISE_APM_MCP_URL", raising=False)
    (tmp_path / ".env").write_text("# NOISE_APM_MCP_URL=http://old.test:9096/sse\n", "utf-8")
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        _warn_retired_apm_url(_cfg())
    assert not caplog.records


def test_client_reads_source_endpoint_not_retired_key():
    cfg = SimpleNamespace(
        dbhub=SimpleNamespace(source_endpoints={}),
        noise_gate=SimpleNamespace(apm_mcp_token=SecretStr(""), apm_mcp_url="http://old.test/sse"),
    )
    assert build_apm_gateway_client(cfg) is None  # 옛 키가 남아 있어도 읽지 않는다
    cfg.dbhub.source_endpoints = {"apm": "http://127.0.0.1:9096/sse"}
    client = build_apm_gateway_client(cfg)
    assert client is not None and client._server_url == "http://127.0.0.1:9096/sse"
