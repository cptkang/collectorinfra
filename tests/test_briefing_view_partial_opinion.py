"""진행 중 부분 브리핑의 단계 소견 객체 렌더 — 「[object Object]」 교정(2026-11-02 폐쇄망 화면).

부분 브리핑(`partial_briefing.playbook.sections[].opinion`)은
`{status, text|reason, basis}` 객체이고
(`sre_agent` `investigation_dispatcher._opinion_view`·`add_flow`), 종결 브리핑은 문자열이다.
플레이북 칸(`briefing-view.js` `opinionHtml`)이 두 꼴을 모두 그리는지 node로 공용 렌더에 넣어 본다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_BV_JS = Path(__file__).resolve().parents[1] / "src" / "static" / "js" / "briefing-view.js"


def _render(view: dict[str, Any]) -> str:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 렌더 테스트 생략")
    script = (f"var BV = require({json.dumps(str(_BV_JS))});"
              f"process.stdout.write(JSON.stringify(BV.renderBody({json.dumps(view)}, "
              '{mode: "detail"})));')
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                          timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _partial(opinion: Any, status: str = "ok") -> dict[str, Any]:
    return {"status": "running", "progress": {"done": 1, "total": 8},
            "partial_briefing": {"partial": True, "playbook": {"id": "was.latency", "sections": [
                {"step": "S5", "action": "related_alarms", "title": "전후 연관 알람",
                 "status": status, "opinion": opinion}]}}}


def test_ok_object_renders_text_escaped():
    html = _render(_partial({"status": "ok", "text": "선행 알람 <b>없음</b>", "basis": "b"}))
    assert "[object Object]" not in html
    assert "AI 소견</span> 선행 알람 &lt;b&gt;없음&lt;/b&gt;" in html


def test_none_object_renders_reason():
    html = _render(_partial({"status": "none", "reason": "단계 결손 — 소견 생략", "basis": "b"},
                            status="missing"))
    assert "[object Object]" not in html
    assert "AI 소견을 싣지 않았습니다 — 단계 결손 — 소견 생략" in html


def test_none_object_without_reason_has_default():
    html = _render(_partial({"status": "none", "basis": "b"}))
    assert "AI 소견을 싣지 않았습니다 — 소견 없음" in html


def test_pending_object_renders_pending_note():
    html = _render(_partial({"status": "pending", "basis": "b"}))
    assert "[object Object]" not in html
    assert "AI 소견 작성 중" in html
    assert "AI 소견</span>" not in html


def test_final_string_opinion_unchanged():
    html = _render(_partial("문자열 소견"))
    assert "AI 소견</span> 문자열 소견" in html
