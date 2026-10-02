"""plans/132 W4 — 사용법 안내의 APM 조건부 행(125 A-8 · LLM 0).

활성(레지스트리 보기 ∧ `MCP_SOURCE_ENDPOINTS`) ∧ 허용일 때만 싣고, 아니면 안내문 바이트 불변이다.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

from src.config import DBHubConfig
from src.routing.registry import get_registry

gi = importlib.import_module("src.nodes.general_inference")


def _cfg(endpoints: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(dbhub=DBHubConfig(source_endpoints=endpoints))


def test_apm_usage_lines_only_when_active_and_allowed() -> None:
    allowed = {"allowed_sources": None, "user_role": "user"}
    source, capability = gi._apm_usage_lines(allowed, _cfg({"apm": "http://127.0.0.1:9096/sse"}))
    reg = get_registry()
    assert source.startswith(f"- {reg.system_label('apm')}:")
    assert reg.views_of("apm")[0].label in capability and "제니퍼" in capability
    assert gi._apm_usage_lines(allowed, _cfg({})) == ("", ""), "비활성 = 안내문 바이트 불변"
    denied = {"allowed_sources": [], "user_role": "user"}
    assert gi._apm_usage_lines(denied, _cfg({"apm": "http://x/sse"})) == ("", ""), "권한 밖 비노출"


def test_usage_answer_lists_apm_after_db_catalog(monkeypatch) -> None:
    monkeypatch.setattr(gi, "_build_source_catalog", lambda _s, _c: "- 폴스타: 서버")
    monkeypatch.setattr(gi, "_doc_usage_lines", lambda _s, _c: ("", ""))
    answer = gi._build_usage_answer({"allowed_sources": None, "user_role": "user"},
                                    _cfg({"apm": "http://127.0.0.1:9096/sse"}))
    reg = get_registry()
    assert answer.index("- 폴스타: 서버") < answer.index(f"- {reg.system_label('apm')}:")
    assert "- WAS·미들웨어:" in answer
