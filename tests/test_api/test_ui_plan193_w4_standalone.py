"""plans/193 W4 — 독립 관제 페이지의 조사 진입 「(새 탭)」 표지(K-1 B 운용 규칙 2).

규칙: 독립 페이지(mount 인자 `onOpenInvestigation` 없음)의 조사 진입은 새 탭이라 192 W2 목적지 문구
(「장애 진단에서 보기」·「조사 대화로 열기」) 뒤에 「(새 탭)」을 단다. 포털 탭(인자 있음)은 같은 창
탭 전환이라 표지가 없다. 표지는 서랍 머리 진입 버튼과 수동 모드 접수 결과의 진행 중 조사·합류 버튼에
같은 모양으로 붙는다. break-glass 운영자 관제는 종전대로 진입·진행 중·합류 버튼 0(plans/192 Q-3).

node로 `noise.js` 실물 함수만 뽑아 돈다(하네스는 plans/192 W1·W2 테스트 것을 쓴다).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.test_api.test_ui_plan192_w1_entry import (
    _DRAWER_FUNCS,
    _DRAWER_PRELUDE,
    _NOISE_PRELUDE,
    NOISE_JS,
    body_of,
    node_run,
)
from tests.test_api.test_ui_plan192_w2_entry_labels import _START, _bv_path

TO_DIAG = "장애 진단에서 보기"
TO_CONV = "조사 대화로 열기"
MARK = " (새 탭)"
TITLE = "새 브라우저 탭에서 엽니다"

# 포털 탭이 넘기는 mount 인자(같은 창 진입) — 없으면 독립 페이지
_PORTAL_OPTS = "opts = { onOpenInvestigation: function () {} };"


def _head_button(portal: bool, break_glass: bool, live: bool) -> Any:
    src = NOISE_JS.read_text(encoding="utf-8")
    defs = "\n".join(body_of(src, n, "noise.js") for n in ("el", "newTabMark", "portalEntryButton"))
    return node_run(
        _NOISE_PRELUDE,
        defs,
        f"""
{_PORTAL_OPTS if portal else ""}
IS_BREAK_GLASS = {json.dumps(break_glass)};
liveViewOn = {json.dumps(live)};
portalEntryButton({{ investigation_id: "inv-1", status: "done" }});
var b = investigationHead.kids[0];
out = b ? {{ text: b.textContent, title: b.title === undefined ? null : b.title,
             n: investigationHead.kids.length }} : null;
""",
    )


@pytest.mark.parametrize("live", [True, False], ids=["diag-on", "diag-off"])
def test_head_button_marks_new_tab_only_on_standalone_page(live: bool) -> None:
    label = TO_DIAG if live else TO_CONV
    assert _head_button(portal=False, break_glass=False, live=live) == {
        "text": label + MARK, "title": TITLE, "n": 1,
    }
    assert _head_button(portal=True, break_glass=False, live=live) == {
        "text": label, "title": None, "n": 1,
    }


@pytest.mark.parametrize("portal", [False, True], ids=["standalone", "portal"])
def test_head_button_still_absent_for_break_glass(portal: bool) -> None:
    for live in (True, False):
        assert _head_button(portal=portal, break_glass=True, live=live) is None


def _drawer(portal: bool, break_glass: bool, live: bool, kind: str) -> Any:
    src = NOISE_JS.read_text(encoding="utf-8")
    defs = "\n".join(body_of(src, n, "noise.js") for n in _DRAWER_FUNCS)
    return node_run(
        _bv_path() + _DRAWER_PRELUDE + _START,
        defs,
        f"""
{_PORTAL_OPTS if portal else ""}
IS_BREAK_GLASS = {json.dumps(break_glass)};
liveViewOn = {json.dumps(live)};
manualOnlyMode = Promise.resolve(true);
postRes = STARTS[{json.dumps(kind)}];
renderInvestigation({{ alarm_id: "a-8" }});
for (var i = 0; i < 4; i++) await tick();
if (!attaches.length) {{ out = {{ attaches: 0 }}; }} else {{
    var box = attaches[0].c;
    act(box, "analyze"); await tick(); await tick(); await tick();
    var g = goOf(box.innerHTML);
    out = {{ attaches: attaches.length, label: g ? g.label : null,
             fn: attaches[0].o.openInvestigationLabel() }};
    if (investigationPanel) investigationPanel.close();
}}
""",
    )


@pytest.mark.parametrize("kind", ["in_flight", "joined"])
@pytest.mark.parametrize("live", [True, False], ids=["diag-on", "diag-off"])
def test_manual_mode_go_button_matches_head_button_shape(live: bool, kind: str) -> None:
    label = TO_DIAG if live else TO_CONV
    standalone = _drawer(portal=False, break_glass=False, live=live, kind=kind)
    assert standalone == {"attaches": 1, "label": label + MARK, "fn": label + MARK}
    portal = _drawer(portal=True, break_glass=False, live=live, kind=kind)
    assert portal == {"attaches": 1, "label": label, "fn": label}


@pytest.mark.parametrize("portal", [False, True], ids=["standalone", "portal"])
def test_break_glass_drawer_has_no_entry_regardless_of_mount_args(portal: bool) -> None:
    # 조사 기록이 없는 break-glass 서랍에는 접수 칸 자체가 없다 — 진행 중 조사·합류 버튼 0
    assert _drawer(portal=portal, break_glass=True, live=True, kind="joined") == {"attaches": 0}
