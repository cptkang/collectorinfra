"""plans/190 W3·W4 — APM 게이트웨이 호출 상한 분기 · `.env.example` 키 정합.

실 MCP 없이 `run_tool_batch`를 대역으로 바꿔 배치 호출자가 넘기는 타임아웃만 검증한다.
"""

from pathlib import Path

import pytest

from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.settings import AgentSettings

_TOP = Path(__file__).resolve().parents[1]
_PLAN190_KEYS = ("APM_TOOL_TIMEOUT_SECONDS", "APM_XVIEW_CONFIRM_OVER")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """셸 env 누수 차단 — 검증 대상 키는 테스트가 명시한 값만 쓴다."""
    for key in (*_PLAN190_KEYS, "EVIDENCE_PREFETCH_TIMEOUT_SECONDS"):
        monkeypatch.delenv(key, raising=False)


def _settings(**kw) -> AgentSettings:
    base = {"model": "m", "gemini_api_key": None, "evidence_prefetch_timeout_seconds": 9}
    return AgentSettings(_env_file=None, **{**base, **kw})


def _capture(monkeypatch) -> dict:
    captured: dict = {}

    def fake_run(url, headers, calls, *, timeout_seconds, concurrency=1):
        captured.update(url=url, timeout=timeout_seconds)
        return []

    monkeypatch.setattr(mc, "run_tool_batch", fake_run)
    return captured


@pytest.mark.parametrize("value", [None, 0, -5])
def test_apm_timeout_unset_or_nonpositive_falls_back_to_prefetch(monkeypatch, value):
    captured = _capture(monkeypatch)
    s = _settings(apm_mcp_url="http://gw/sse", apm_tool_timeout_seconds=value)
    mc.make_apm_batch_caller(s)([("apm_app_health", {})])
    assert captured == {"url": "http://gw/sse", "timeout": 9.0}


def test_apm_timeout_set_overrides_prefetch(monkeypatch):
    captured = _capture(monkeypatch)
    s = _settings(apm_mcp_url="http://gw/sse", apm_tool_timeout_seconds=60)
    mc.make_apm_batch_caller(s)([("apm_app_health", {})])
    assert captured == {"url": "http://gw/sse", "timeout": 60.0}


def test_polestar_batch_caller_ignores_apm_timeout(monkeypatch):
    captured = _capture(monkeypatch)
    s = _settings(polestar_mcp_url="http://ps/sse", apm_tool_timeout_seconds=60)
    mc.make_batch_caller(s)([("polestar_metric", {})])
    assert captured == {"url": "http://ps/sse", "timeout": 9.0}


def test_apm_batch_caller_still_requires_url():
    assert mc.make_apm_batch_caller(_settings(apm_mcp_url="", apm_tool_timeout_seconds=60)) is None


def _example_lines() -> list[str]:
    return (_TOP / ".env.example").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("key", _PLAN190_KEYS)
def test_env_example_keys_match_settings_fields(key):
    """`.env.example` 키 ↔ `AgentSettings` 필드 정합 · 인라인 주석 금지 · 예시 값이 로드된다."""
    hits = [ln for ln in _example_lines() if ln.lstrip("# ").startswith(f"{key}=")]
    assert len(hits) == 1, f".env.example에 {key} 정확히 1줄이어야 한다: {hits}"
    value = hits[0].lstrip("# ").split("=", 1)[1]
    assert "#" not in value, f"{key} 인라인 주석 금지"
    field = key.lower()
    assert field in AgentSettings.model_fields
    loaded = getattr(_settings(**{field: value}), field)
    if not hits[0].startswith("#"):
        # 활성 줄은 코드 기본값과 같아야 한다(.env.example 유지 규칙).
        assert loaded == AgentSettings.model_fields[field].default


def test_env_example_apm_timeout_default_is_commented():
    """기본 None은 줄 자체를 두지 않는 것으로 표현한다(빈 값은 숫자 필드 로드 실패)."""
    hits = [ln for ln in _example_lines() if ln.lstrip("# ").startswith("APM_TOOL_TIMEOUT_SECONDS=")]
    assert hits and hits[0].startswith("#")
    assert AgentSettings.model_fields["apm_tool_timeout_seconds"].default is None
