"""plans/187 W16 — 「조사 대화」 뷰 탭 · 노이즈 관제 「조사 대화로 열기」 상시 표시.

브라우저 e2e는 이 환경에서 돌지 않으므로 두 갈래로 고정한다:
    1. app.js의 탭 전환 함수(capture/restore/swap/close)와 noise.js의 포털 진입 함수를
       node로 실제 실행한다 — 가짜 DOM·창을 주입해 질의응답 대화 보존 · 스트리밍 중 전환
       거부 · 닫기 시 재조회 정지 · 관제 진입은 항상 새 탭(W23 — 켜짐 `/?diagnosis=` ·
       꺼짐 `/?conversation=`)을 단언한다.
    2. 정적 — 탭 마크업 · 버튼 상시 조건(브리핑 종류·장애 진단 켜짐 무관) · 쿼리 진입.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_APP_JS = _STATIC / "js" / "app.js"
_NOISE_JS = _STATIC / "js" / "noise.js"

_SWAP_FUNCS = (
    "captureConversation",
    "restoreConversation",
    "swapConversation",
    "showInvestigationTab",
    "closeInvestigationTab",
    "forgetInvestigationConversation",  # (plans/192 W4) 닫으면 새로고침 복원 저장값을 지운다
)

# app.js 함수가 기대는 전역 · DOM 최소 가짜
_APP_HARNESS = r"""
function mkEl(cls) {
    var e = { cls: cls || "", parent: null, style: {} };
    function has(c) { return e.cls.split(" ").indexOf(c) >= 0; }
    e.classList = {
        contains: has,
        toggle: function (c, on) {
            if (on && !has(c)) e.cls += " " + c;
            if (!on && has(c)) {
                e.cls = e.cls.split(" ").filter(function (x) { return x !== c; }).join(" ");
            }
        },
    };
    return e;
}
function Box() { this.children = []; }
Box.prototype.appendChild = function (n) {
    if (n.isFrag) {
        var kids = n.children.slice();
        n.children = [];
        kids.forEach(this.appendChild, this);
        return n;
    }
    if (n.parent) n.parent.children.splice(n.parent.children.indexOf(n), 1);
    n.parent = this;
    this.children.push(n);
    return n;
};
var document = {
    createDocumentFragment: function () { var f = new Box(); f.isFrag = true; return f; },
};
var chatMessages = new Box();
var chatWelcome = mkEl("chat-welcome");
var promptEl = { placeholder: "기본", value: "" };
var PROMPT_PLACEHOLDER = "기본";
var investigationTab = { style: { display: "none" } };
var investigationTabClose = { style: { display: "none" } };
var isProcessing = false, activeView = "chat", conversationSlot = "chat";
var parkedConversation = null;
var messages = [], currentThreadId = null, lastUploadedFile = null, currentDbScope = null;
var pendingDbIds = null, pendingReset = false, currentApmScope = null;
var apmScopeInherited = false, pendingApmIds = null, pendingApmReset = false;
var pendingInvestigationRef = null, investigationPinPanel = null;
var chatInvestigationPanels = [], stickToBottom = false;
var INVESTIGATION_CONV_KEY = "investigation_conversation";
var errors = [];
function showError(t) { errors.push(t); }
function hideError() {}
function hidePromptConfirm() {}
function closeApmScopePopover() {}
function updateDbScopeChip() {}
function updateApmScopeChip() {}
function showProgressEmpty() {}
function renderHistoryList() {}
function setActiveView(v) {
    var conv = v === "chat" || v === "investigation";
    if (conv && v !== conversationSlot && !swapConversation(v)) return;
    activeView = v;
}
function panel(name) {
    return {
        name: name, opened: 0, closed: 0,
        open: function () { this.opened += 1; },
        close: function () { this.closed += 1; },
    };
}
"""


def _run(src: str, funcs: tuple[str, ...], harness: str, body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    parts = [harness, *(_js_function_body(src, f) for f in funcs)]
    parts += ["var out = null;", body, "process.stdout.write(JSON.stringify(out));"]
    done = subprocess.run(
        ["node", "-e", "\n".join(parts)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _app(body: str) -> Any:
    return _run(_APP_JS.read_text(encoding="utf-8"), _SWAP_FUNCS, _APP_HARNESS, body)


def test_swap_keeps_chat_conversation_and_restores_it() -> None:
    out = _app(
        r"""
        var m1 = mkEl("message");
        chatMessages.appendChild(m1);
        chatMessages.appendChild(chatWelcome);
        chatWelcome.classList.toggle("hidden", true);
        currentThreadId = "t-chat"; messages = [{ q: 1 }];
        pendingDbIds = ["polestar"]; promptEl.value = "쓰던 질의";
        var chatCard = panel("chat-card");
        chatInvestigationPanels = [chatCard];
        showInvestigationTab(true);
        setActiveView("investigation");
        var inInv = {
            slot: conversationSlot, thread: currentThreadId,
            msgs: chatMessages.children.length,
            welcomeHidden: chatWelcome.classList.contains("hidden"),
            draft: promptEl.value, pending: pendingDbIds,
        };
        investigationPinPanel = panel("pin");
        pendingInvestigationRef = "inv-1";
        var m2 = mkEl("message");
        chatMessages.appendChild(m2);
        setActiveView("chat");
        var kids = chatMessages.children;
        out = {
            inInv: inInv, slot: conversationSlot, thread: currentThreadId,
            draft: promptEl.value, pending: pendingDbIds,
            hasM1: kids.indexOf(m1) >= 0, hasM2: kids.indexOf(m2) >= 0,
            welcomeKept: kids.indexOf(chatWelcome) >= 0,
            chatCardReopened: chatCard.opened,
            parkedRef: parkedConversation.pendingInvestigationRef,
            tab: investigationTab.style.display,
        };
        """
    )
    # 조사 대화 탭은 빈 대화로 시작한다 — 질의응답의 thread·범위 칩·입력 초안이 새지 않는다
    assert out["inInv"] == {
        "slot": "investigation", "thread": None, "msgs": 1,
        "welcomeHidden": False, "draft": "", "pending": None,
    }
    # 질의응답으로 돌아오면 그 대화가 그대로 — 말풍선·thread·칩·초안 복원,
    # 떼어 냈던 카드는 다시 읽는다
    assert out["slot"] == "chat" and out["thread"] == "t-chat"
    assert out["draft"] == "쓰던 질의" and out["pending"] == ["polestar"]
    assert out["hasM1"] and not out["hasM2"] and out["welcomeKept"]
    assert out["chatCardReopened"] == 1 and out["parkedRef"] == "inv-1"
    assert out["tab"] == ""


def test_swap_refused_while_streaming() -> None:
    out = _app(
        r"""
        currentThreadId = "t-chat"; isProcessing = true;
        setActiveView("investigation");
        out = { view: activeView, slot: conversationSlot, thread: currentThreadId,
            errors: errors };
        """
    )
    assert out["view"] == "chat" and out["slot"] == "chat" and out["thread"] == "t-chat"
    assert out["errors"] == ["답변이 끝난 뒤 탭을 옮길 수 있습니다."]


def test_close_tab_returns_to_chat_and_stops_investigation_polling() -> None:
    out = _app(
        r"""
        currentThreadId = "t-chat";
        showInvestigationTab(true); setActiveView("investigation");
        var pin = panel("pin");
        investigationPinPanel = pin; pendingInvestigationRef = "inv-1";
        closeInvestigationTab();
        var first = {
            view: activeView, thread: currentThreadId, pinClosed: pin.closed,
            parked: parkedConversation, tab: investigationTab.style.display,
            close: investigationTabClose.style.display,
        };
        // 다른 뷰(이벤트 알람)에 있다가 닫아도 질의응답 대화가 화면에 오른다
        showInvestigationTab(true); setActiveView("investigation");
        var pin2 = panel("pin2");
        investigationPinPanel = pin2;
        setActiveView("alarm"); closeInvestigationTab();
        out = { first: first, view: activeView, slot: conversationSlot,
            thread: currentThreadId, pin2Closed: pin2.closed };
        """
    )
    assert out["first"] == {
        "view": "chat", "thread": "t-chat", "pinClosed": 1, "parked": None,
        "tab": "none", "close": "none",
    }
    assert out["view"] == "alarm" and out["slot"] == "chat" and out["thread"] == "t-chat"
    assert out["pin2Closed"] == 1


_NOISE_HARNESS = r"""
var opened = [];
var liveViewOn = false;
var opts = {};  // (plans/193 W1) mount 인자 생략 = 종전 새 탭 진입
function liveViewNow() { return liveViewOn; }
var window = { open: function (url, name, feat) { opened.push([url, name, feat]); return null; } };
"""


def _portal(body: str) -> Any:
    src = _NOISE_JS.read_text(encoding="utf-8")
    return _run(src, ("openInPortal",), _NOISE_HARNESS, body)


def test_entry_always_opens_new_tab_by_live_view() -> None:
    # (W23 · D-341 부기 14) 포털 창 재사용 폐기 — 누를 때마다 새 탭 · 켜짐에 따라 쿼리만 다르다
    out = _portal(
        r"""
        openInPortal("inv 9");
        liveViewOn = true;
        openInPortal("inv-9");
        out = { opened: opened };
        """
    )
    assert out == {"opened": [
        ["/?conversation=inv%209", "_blank", "noopener"],
        ["/?diagnosis=inv-9", "_blank", "noopener"],
    ]}


def test_markup_and_static_wiring() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    start = html.index('data-view="investigation"') - 80
    tab = html[start: html.index('id="investigationTabClose"') + 120]
    assert 'id="investigationTab"' in tab and tab.count('style="display: none;"') == 2
    assert html.index('data-view="alarm"') < html.index('data-view="investigation"')
    app = _APP_JS.read_text(encoding="utf-8")
    # (W23) 열린 포털 창 재사용(opener·BroadcastChannel) 폐기 — 관제는 항상 새 탭
    assert "window.name" not in app
    assert "KBPortal" not in app and "kbaiops-portal" not in app
    query = _js_function_body(app, "openInvestigationFromQuery")
    # `?conversation=`은 장애 진단 켜짐과 무관하게 조사 대화로 — 종전 두 쿼리는 진단 우선 그대로
    assert 'params.get("conversation")' in query
    assert "conv === null && window.DiagnosisView" in query
    assert 'setActiveView("chat")' in _js_function_body(app, "openThread")
    noise = _NOISE_JS.read_text(encoding="utf-8")
    btn = _js_function_body(noise, "portalEntryButton")
    # 상시 조건: 조사 id가 있으면 단다 — 플레이북 여부 무관 · 버튼 1개(켜짐에 따라 이름만 바뀐다)
    assert 'view.status === "none"' in btn and "openInPortal(" in btn
    assert '"장애 진단에서 보기"' in btn and '"조사 대화로 열기"' in btn
    assert "openPortalConversation" not in noise and "PORTAL_WINDOW" not in noise
    assert "openConversation: true" not in noise
