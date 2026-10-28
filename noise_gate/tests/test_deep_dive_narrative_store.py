"""심층 답변 원문 `deep_dive.narrative` — 저장 상한 축소 순서와 사용자 뷰 마스킹."""

from __future__ import annotations

import json

from noise_gate.infrastructure.investigation_store import (
    DEEP_NARRATIVE_CLIP_CHARS,
    DEEP_NARRATIVE_CLIPPED_NOTE,
    MAX_BRIEFING_BYTES,
    clip_briefing,
    playbook_user_view,
)

SECRET = "pw=hunter2"


def _briefing(narrative: str, pad: int = 0) -> dict:
    return {
        "playbook": {"id": "host.cpu", "sections": [], "notes": ["x" * pad] if pad else []},
        "deep_dive": {"status": "done", "mode": "parallel", "label": "심층", "summary": "첫 줄",
                      "cause": "원인", "narrative": narrative},
    }


def _size(b: dict) -> int:
    return len(json.dumps(b, ensure_ascii=False).encode("utf-8"))


def test_small_briefing_keeps_narrative_as_is() -> None:
    b = _briefing("### 보고서\n- 원인 A\n- 조치 B")
    assert clip_briefing(b) is b


def test_oversized_narrative_clipped_first_keeping_summary_and_cause() -> None:
    b = _briefing("가" * 20000)
    assert _size(b) > MAX_BRIEFING_BYTES
    dd = clip_briefing(b)["deep_dive"]
    assert dd["narrative"] == "가" * DEEP_NARRATIVE_CLIP_CHARS + DEEP_NARRATIVE_CLIPPED_NOTE
    assert dd["summary"] == "첫 줄" and dd["cause"] == "원인"


def test_narrative_dropped_before_summary_when_still_too_big() -> None:
    # 플레이북이 상한을 거의 다 쓰면 원문 앞부분도 못 들어가 원문만 뺀다(요약·원인은 남는다).
    pad = MAX_BRIEFING_BYTES - 600
    dd = clip_briefing(_briefing("나" * 20000, pad=pad))["deep_dive"]
    assert "narrative" not in dd
    assert dd["summary"] == "첫 줄" and dd["cause"] == "원인"


def test_user_view_masks_narrative_line_by_line() -> None:
    view = playbook_user_view(_briefing(f"### 보고서\n{SECRET}\n- 조치"),
                              lambda line: "[가림]" if SECRET in line else line)
    assert view is not None
    assert view["deep_dive"]["narrative"] == "### 보고서\n[가림]\n- 조치"
