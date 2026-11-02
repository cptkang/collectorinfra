"""plans/195 W4 — 섹션 `queries[{tool, phrase}]`의 저장 축소 순서 · 사용자 뷰 마스킹.

- 축소 ①·②는 `queries`를 남긴다(도구 목록과 같은 높이 — 표·소견보다 오래 남는다) · ③부터 뺀다.
- 사용자 뷰는 `queries[].phrase`를 `mask_fn`으로 가린다(D-325 ③).
- 구버전 브리핑(키 없음)은 축소·사용자 뷰 결과가 바뀌지 않는다.
"""

from __future__ import annotations

from typing import Any

from noise_gate.infrastructure.investigation_store import (
    _playbook_candidates,
    clip_briefing,
    playbook_user_view,
)

_Q = [{"tool": "apm_app_health", "phrase": "제니퍼 · wasdemo01 응답·처리량·오류"},
      {"tool": "unknown_tool"}]


def _pb(queries: list[dict[str, str]] | None = _Q) -> dict[str, Any]:
    sec: dict[str, Any] = {
        "step": "S1", "action": "apm.app_health", "title": "t", "status": "ok",
        "verdict": None, "verdict_source": None, "badges": [], "tools": ["apm_app_health"],
        "missing": [], "reason": None, "elapsed_ms": 1,
        "table": {"columns": ["a"], "rows": [["x" * 50]] * 20}, "opinion": "o",
    }
    if queries is not None:
        sec["queries"] = queries
    return {"id": "was.latency", "version": 1, "matched_by": "t", "sections": [sec],
            "synthesis": {"hypotheses": [], "actions": []}, "notes": []}


def test_clip_levels_keep_queries_until_section_heads() -> None:
    lvl1, lvl2, lvl3, lvl4 = _playbook_candidates(_pb())
    assert lvl1["sections"][0]["queries"] == _Q
    assert "table" not in lvl2["sections"][0] and lvl2["sections"][0]["queries"] == _Q
    assert "queries" not in lvl3["sections"][0] and "queries" not in lvl4["sections"][0]


def test_clip_briefing_with_queries_keeps_them_when_tables_shrink() -> None:
    brief = {"summary": "s", "playbook": _pb()}
    out = clip_briefing(brief, max_bytes=1200)
    sec = out["playbook"]["sections"][0]
    assert sec["queries"] == _Q
    assert len(sec.get("table", {}).get("rows") or []) < 20 or "table" not in sec


def test_old_briefing_without_queries_unchanged() -> None:
    old = _pb(None)
    for cand in _playbook_candidates(old):
        assert "queries" not in cand["sections"][0]
    view = playbook_user_view({"playbook": old}, lambda s: s)
    assert view is not None and "queries" not in view["playbook"]["sections"][0]


def test_user_view_masks_query_phrase() -> None:
    def mask(s: str) -> str:
        return s.replace("wasdemo01", "***")

    view = playbook_user_view({"playbook": _pb()}, mask)
    assert view is not None
    q = view["playbook"]["sections"][0]["queries"]
    assert q[0]["phrase"] == "제니퍼 · *** 응답·처리량·오류"
    assert q[1] == {"tool": "unknown_tool"}
