"""plans/192 W4 (Q-4 B) — 조사 대화의 새로고침 복원과 이력에서 다시 열기를 고정한다.

- 탭 저장소(sessionStorage `investigation_conversation` = `{"id", "thread", "user"}`) 3함수와
  기록 지점(조사 대화 열기 · 조사 대화 슬롯의 턴 완료 · 탭 닫기 · 로그아웃).
- 새로고침 복원은 쿼리 진입보다 뒤 · thread가 있으면 조사 대화 탭에 그 대화,
  404면 새 조사 대화로 대체.
- 이력에서 고정 카드가 있는 대화를 열면 「조사 대화」 탭(질의응답 대화는 보존) · 없으면 질의응답 탭.
  탭 전환은 대화를 받은 뒤에 한다. 이력 기록 규칙(완결 턴만 · D-248 ④)은 바꾸지 않는다.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.test_api.test_ui_plan192_w1_entry import (
    APP_JS,
    app_defs,
    body_of,
    entry_prelude,
    node_run,
    pin_run,
)

_KEY = "investigation_conversation"

# 탭 저장소 가짜 — ssMode: ok · throw-all(메서드 예외) · throw-access(접근 자체가 예외)
_STORAGE = r"""
var ssData = {};
var ssMode = "ok";
var fakeSS = {
    getItem: function (k) {
        if (ssMode !== "ok") throw new Error("blocked");
        return Object.prototype.hasOwnProperty.call(ssData, k) ? ssData[k] : null;
    },
    setItem: function (k, v) {
        if (ssMode !== "ok") throw new Error("quota");
        ssData[k] = String(v);
    },
    removeItem: function (k) { if (ssMode !== "ok") throw new Error("blocked"); delete ssData[k]; },
};
Object.defineProperty(globalThis, "sessionStorage", { configurable: true,
    get: function () {
        if (ssMode === "throw-access") throw new Error("SecurityError");
        return fakeSS;
    } });
function stored() {
    var raw = Object.prototype.hasOwnProperty.call(ssData, "investigation_conversation")
        ? ssData["investigation_conversation"] : null;
    return raw === null ? null : JSON.parse(raw);
}
"""

# 대화 화면 두 탭(질의응답·조사 대화) 하네스 — plans/187 W16 테스트와 같은 setActiveView 대역
_CONV = r"""
var chatMessages = new El("div");
var chatWelcome = new El("div");
chatWelcome.className = "chat-welcome";
var promptEl = { placeholder: "기본", value: "", focus: function () {} };
var PROMPT_PLACEHOLDER = "기본";
var investigationTab = { style: { display: "none" } };
var investigationTabClose = { style: { display: "none" } };
var activeView = "chat";
var conversationSlot = "chat";
var parkedConversation = null;
var messages = [];
var currentThreadId = null;
var lastUploadedFile = null;
var currentDbScope = null;
var pendingDbIds = null;
var pendingReset = false;
var currentApmScope = null;
var apmScopeInherited = false;
var pendingApmIds = null;
var pendingApmReset = false;
var pendingInvestigationRef = null;
var investigationPinPanel = null;
var chatInvestigationPanels = [];
var stickToBottom = false;
var threadsState = "ready";
var investigationConvUser = "demo";   // checkAuthOnLoad가 확정한 사용자(저장값의 주인)
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
var newChats = 0;
function startNewChat() { newChats += 1; currentThreadId = null; messages = []; }
var pins = [];
function renderInvestigationPin(id, opts) { pins.push([id, !!(opts && opts.restored)]); }
var shown = [];
function showThreadTurns(threadId, turns) {
    if (isProcessing) return;
    shown.push({ thread: threadId, slot: conversationSlot, view: activeView, n: turns.length });
    currentThreadId = threadId;
}
var fetches = [];
var threadBodies = {};
function fetch(url) {
    fetches.push({ url: url, view: activeView, slot: conversationSlot });
    var id = decodeURIComponent(String(url).split("/").pop());
    var b = threadBodies[id];
    var status = b ? (b.status || 200) : 404;
    return Promise.resolve({ status: status, ok: status >= 200 && status < 300,
        json: function () { return Promise.resolve({ turns: b ? b.turns : [] }); } });
}
function chatMsg() {
    var m = new El("div");
    m.className = "message";
    chatMessages.appendChild(m);
    return m;
}
function state() {
    return { view: activeView, slot: conversationSlot, thread: currentThreadId,
        tab: investigationTab.style.display, stored: stored(), shown: shown, errors: errors };
}
"""

_FAKE_CONV_OPEN = r"""
var convOpened = [];
function openInvestigationConversation(id) { convOpened.push(String(id)); return true; }
"""

PINNED = [
    {
        "user_query": "q1",
        "disclosures": [{"kind": "investigation_ref", "ref": {"investigation_id": "inv-1"}}],
    }
]
PLAIN = [{"user_query": "q1", "disclosures": []}]


def conv_run(funcs: tuple[str, ...], script: str, extra: str = "", **values: Any) -> Any:
    head = "".join(f"var {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in values.items())
    pre = entry_prelude(_STORAGE + _CONV + extra + head)
    return node_run(pre, app_defs(funcs, pre, script), script)


_STORE_FUNCS = (
    "rememberInvestigationConversation",
    "forgetInvestigationConversation",
    "storedInvestigationConversation",
)

# ─── 저장소 3함수 ─────────────────────────────────────────────────────────


def test_storage_key_constant() -> None:
    src = APP_JS.read_text(encoding="utf-8")
    assert re.search(r'^    var INVESTIGATION_CONV_KEY = "investigation_conversation";', src, re.M)


def test_remember_stored_forget_round_trip() -> None:
    out = conv_run(
        _STORE_FUNCS,
        r"""
rememberInvestigationConversation("inv-1", null);
var raw1 = JSON.parse(ssData["investigation_conversation"]);
var s1 = storedInvestigationConversation();
rememberInvestigationConversation("inv-1", "t-1");
var s2 = storedInvestigationConversation();
forgetInvestigationConversation();
out = { raw1: raw1, s1: s1, s2: s2, s3: storedInvestigationConversation(),
        has: Object.prototype.hasOwnProperty.call(ssData, "investigation_conversation") };
""",
    )
    assert out == {
        "raw1": {"id": "inv-1", "thread": None, "user": "demo"},
        "s1": {"id": "inv-1", "thread": None},
        "s2": {"id": "inv-1", "thread": "t-1"},
        "s3": None,
        "has": False,
    }


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{",
        "null",
        "[]",
        '"inv-1"',
        '{"thread": "t-1"}',
        '{"id": "", "thread": null, "user": "demo"}',
        '{"id": "bad id!", "thread": null, "user": "demo"}',
        '{"id": "inv-1", "thread": "t/1", "user": "demo"}',
        '{"id": "inv-1", "thread": "<x>", "user": "demo"}',
    ],
)
def test_stored_rejects_malformed_values(raw: str) -> None:
    out = conv_run(
        _STORE_FUNCS,
        "ssData[KEY] = RAW;\nout = storedInvestigationConversation();",
        KEY=_KEY,
        RAW=raw,
    )
    assert out is None


@pytest.mark.parametrize(
    ("raw", "kept"),
    [
        ('{"id": "inv-1", "thread": "t-1", "user": "demo"}', True),
        ('{"id": "inv-1", "thread": "t-1", "user": "other"}', False),
        ('{"id": "inv-1", "thread": "t-1", "user": null}', False),
        ('{"id": "inv-1", "thread": "t-1"}', False),  # user 없는 옛 형식
    ],
    ids=["same-user", "other-user", "null-user", "legacy"],
)
def test_stored_value_belongs_to_current_user(raw: str, kept: bool) -> None:
    # 같은 브라우저 탭에서 다른 계정으로 로그인하면 앞 사용자의 조사 대화를 되살리지 않는다 — 지운다
    out = conv_run(
        _STORE_FUNCS,
        "ssData[KEY] = RAW;\nvar s = storedInvestigationConversation();\n"
        "out = { s: s, has: Object.prototype.hasOwnProperty.call(ssData, KEY) };",
        KEY=_KEY,
        RAW=raw,
    )
    if kept:
        assert out == {"s": {"id": "inv-1", "thread": "t-1"}, "has": True}
    else:
        assert out == {"s": None, "has": False}


def test_remember_records_current_user_and_restore_skips_other_user() -> None:
    out = conv_run(
        ("restoreInvestigationConversation", *_STORE_FUNCS),
        r"""
rememberInvestigationConversation("inv-1", null);
var mine = stored();
investigationConvUser = "next-user";   // 같은 탭에서 다른 계정으로 다시 로그인
var ret = restoreInvestigationConversation();
await tick();
out = { mine: mine, ret: !!ret, opened: convOpened, left: stored() };
""",
        extra=_FAKE_CONV_OPEN,
    )
    assert out == {
        "mine": {"id": "inv-1", "thread": None, "user": "demo"},
        "ret": False,
        "opened": [],
        "left": None,
    }


def test_check_auth_on_load_sets_conversation_owner() -> None:
    src = APP_JS.read_text(encoding="utf-8")
    auth = body_of(src, "checkAuthOnLoad", "app.js")
    owner = "investigationConvUser = (userInfo && userInfo.user_id) || null;"
    assert owner in auth
    # 주인을 정한 뒤에 쿼리 진입·복원이 돈다
    assert auth.index(owner) < auth.index("restoreInvestigationConversation()")


@pytest.mark.parametrize("mode", ["throw-all", "throw-access"])
def test_storage_errors_are_silent(mode: str) -> None:
    out = conv_run(
        _STORE_FUNCS,
        f"""
ssMode = {json.dumps(mode)};
rememberInvestigationConversation("inv-1", "t-1");
forgetInvestigationConversation();
out = {{ s: storedInvestigationConversation() }};
""",
    )
    assert out == {"s": None}


# ─── 기록 지점 ───────────────────────────────────────────────────────────


def test_open_investigation_conversation_remembers_id_without_thread() -> None:
    out = conv_run(
        ("openInvestigationConversation",),
        r"""
var ok = openInvestigationConversation("inv-1");
var first = state();
isProcessing = true;
var busy = openInvestigationConversation("inv-2");
isProcessing = false;
var bad = openInvestigationConversation("bad id");
out = { ok: ok, first: first.stored, busy: busy, bad: bad, after: stored(),
    slot: conversationSlot };
""",
    )
    assert out["ok"] is True and out["first"] == {"id": "inv-1", "thread": None, "user": "demo"}
    # 답변 중·형식 불일치로 열지 않으면 저장값을 바꾸지 않는다
    assert out["busy"] is False and out["bad"] is False
    assert out["after"] == {"id": "inv-1", "thread": None, "user": "demo"}
    assert out["slot"] == "investigation"


def test_set_current_thread_records_thread_only_in_investigation_slot() -> None:
    out = conv_run(
        ("setCurrentThread", *_STORE_FUNCS),
        r"""
rememberInvestigationConversation("inv-1", null);
conversationSlot = "chat";
setCurrentThread("t-chat");
var chat = { stored: stored(), thread: currentThreadId };
conversationSlot = "investigation";
setCurrentThread("t-inv");
var inv = { stored: stored(), thread: currentThreadId };
forgetInvestigationConversation();
setCurrentThread("t-other");
out = { chat: chat, inv: inv, none: stored(), stale: threadsState };
""",
    )
    # 질의응답 슬롯의 턴은 조사 대화 저장값을 건드리지 않는다
    assert out["chat"] == {
        "stored": {"id": "inv-1", "thread": None, "user": "demo"},
        "thread": "t-chat",
    }
    assert out["inv"] == {
        "stored": {"id": "inv-1", "thread": "t-inv", "user": "demo"},
        "thread": "t-inv",
    }
    # 열린 조사 대화가 없으면(저장값 없음) 새로 만들지 않는다
    assert out["none"] is None and out["stale"] == "stale"


def test_close_investigation_tab_forgets() -> None:
    out = conv_run(
        ("openInvestigationConversation", "closeInvestigationTab"),
        r"""
openInvestigationConversation("inv-1");
ssData["investigation_conversation"] = JSON.stringify({ id: "inv-1", thread: "t-1", user: "demo" });
closeInvestigationTab();
var first = { stored: stored(), view: activeView, tab: investigationTab.style.display };
openInvestigationConversation("inv-2");
ssData["investigation_conversation"] = JSON.stringify({ id: "inv-2", thread: null, user: "demo" });
setActiveView("alarm");
closeInvestigationTab();
out = { first: first, second: stored(), view: activeView };
""",
    )
    assert out["first"] == {"stored": None, "view": "chat", "tab": "none"}
    assert out["second"] is None and out["view"] == "alarm"


def test_logout_forgets_investigation_conversation() -> None:
    src = APP_JS.read_text(encoding="utf-8")
    start = src.index('logoutBtn.addEventListener("click"')
    handler = src[start : src.index('window.location.href = "/login";', start)]
    assert "forgetInvestigationConversation()" in handler


# ─── 새로고침 복원 ───────────────────────────────────────────────────────


def test_restore_without_stored_value_does_nothing() -> None:
    out = conv_run(
        ("restoreInvestigationConversation", *_STORE_FUNCS),
        r"""
var ret = restoreInvestigationConversation();
await tick();
out = { ret: !!ret, opened: convOpened, fetches: fetches.length, view: activeView };
""",
        extra=_FAKE_CONV_OPEN,
    )
    assert out == {"ret": False, "opened": [], "fetches": 0, "view": "chat"}


def test_restore_without_thread_opens_new_conversation() -> None:
    out = conv_run(
        ("restoreInvestigationConversation", *_STORE_FUNCS),
        r"""
rememberInvestigationConversation("inv-1", null);
restoreInvestigationConversation();
await tick();
out = { opened: convOpened, fetches: fetches.length };
""",
        extra=_FAKE_CONV_OPEN,
    )
    assert out == {"opened": ["inv-1"], "fetches": 0}


def test_restore_with_thread_reopens_it_in_investigation_tab() -> None:
    out = conv_run(
        ("restoreInvestigationConversation", "openThread", *_STORE_FUNCS),
        r"""
threadBodies["t-1"] = { turns: PINNED };
rememberInvestigationConversation("inv-1", "t-1");
restoreInvestigationConversation();
await tick(); await tick(); await tick();
out = { st: state(), opened: convOpened };
""",
        extra=_FAKE_CONV_OPEN,
        PINNED=PINNED,
    )
    st = out["st"]
    assert out["opened"] == []
    assert st["view"] == "investigation" and st["slot"] == "investigation" and st["tab"] == ""
    assert st["shown"] == [
        {"thread": "t-1", "slot": "investigation", "view": "investigation", "n": 1}
    ]
    assert st["stored"] == {"id": "inv-1", "thread": "t-1", "user": "demo"}


def test_restore_with_missing_thread_falls_back_to_new_conversation() -> None:
    out = conv_run(
        ("restoreInvestigationConversation", "openThread", *_STORE_FUNCS),
        r"""
rememberInvestigationConversation("inv-1", "t-gone");
restoreInvestigationConversation();
await tick(); await tick(); await tick();
out = { opened: convOpened, shown: shown, stored: stored() };
""",
        extra=_FAKE_CONV_OPEN,
    )
    # 404 — 저장값을 지우고 같은 조사로 새 조사 대화를 연다(가짜 열기는 다시 기록하지 않는다)
    assert out == {"opened": ["inv-1"], "shown": [], "stored": None}


def test_restore_ignores_malformed_stored_value() -> None:
    out = conv_run(
        ("restoreInvestigationConversation", *_STORE_FUNCS),
        r"""
ssData["investigation_conversation"] = '{"id": "bad id!", "thread": null}';
var ret = restoreInvestigationConversation();
await tick();
out = { ret: !!ret, opened: convOpened, fetches: fetches.length };
""",
        extra=_FAKE_CONV_OPEN,
    )
    assert out == {"ret": False, "opened": [], "fetches": 0}


_AUTH = r"""
var calls = [];
var authBody = { auth_enabled: false, user: { user_id: "anonymous", role: "admin" } };
var queryHandled = false;
var investigationConvUser = null;
function fetch() {
    return Promise.resolve({ status: 200, ok: true,
        json: function () { return Promise.resolve(authBody); } });
}
function revealApp() {}
function loadScopeOptions() { return Promise.resolve(); }
function updateApmScopeChip() {}
function initAlarmSubscription() { calls.push("alarm"); }
function initDiagnosisView() { calls.push("diag"); return Promise.resolve(true); }
function openInvestigationFromQuery() { calls.push("query"); return queryHandled; }
function restoreInvestigationConversation() { calls.push("restore"); return true; }
function redirectToLogin() { calls.push("login"); }
// plans/193 W2 — 진입·복원 뒤 관제 탭 복귀 판정(이 테스트 범위 밖)
function noiseEntrySettled() {}
"""


@pytest.mark.parametrize(
    ("handled", "auth", "expected"),
    [
        (
            True,
            {"auth_enabled": False, "user": {"user_id": "anonymous"}},
            ["alarm", "diag", "query"],
        ),
        (
            False,
            {"auth_enabled": False, "user": {"user_id": "anonymous"}},
            ["alarm", "diag", "query", "restore"],
        ),
        (False, {"auth_enabled": True, "user": None}, ["login"]),
    ],
    ids=["query-wins", "restore-after-query", "unauthenticated"],
)
def test_check_auth_on_load_restores_only_without_query_entry(
    handled: bool, auth: dict, expected: list[str]
) -> None:
    pre = entry_prelude(_AUTH)
    out = node_run(
        pre,
        app_defs(("checkAuthOnLoad",), pre),
        f"""
queryHandled = {json.dumps(handled)};
authBody = {json.dumps(auth)};
checkAuthOnLoad();
for (var i = 0; i < 6; i++) await tick();
out = calls;
""",
    )
    assert out == expected


_QUERY = r"""
var replaced = [];
var history = { replaceState: function (s, t, u) { replaced.push(u); } };
window.location = { pathname: "/", search: "", hash: "" };
var convOpened = [];
function openInvestigationConversation(id) { convOpened.push(String(id)); return true; }
"""


@pytest.mark.parametrize(
    ("search", "diag_on", "handled"),
    [
        ("", False, False),
        ("?other=1", False, False),
        ("?conversation=inv-1", False, True),
        ("?investigation=inv-1", False, True),
        ("?diagnosis=inv-1", True, True),
    ],
)
def test_open_investigation_from_query_reports_handling(
    search: str, diag_on: bool, handled: bool
) -> None:
    pre = entry_prelude(_QUERY)
    out = node_run(
        pre,
        app_defs(("openInvestigationFromQuery",), pre),
        f"""
window.location.search = {json.dumps(search)};
diagOn = {json.dumps(diag_on)};
out = !!openInvestigationFromQuery();
""",
    )
    assert out is handled


# ─── 이력에서 열기 ───────────────────────────────────────────────────────


def thread_run(script: str) -> Any:
    """openThread만 실물로 — 구현이 부르는 저장소 함수는 전이 폐포로 함께 끌려온다."""
    return conv_run(("openThread",), script, PINNED=PINNED, PLAIN=PLAIN)


def test_open_thread_with_pin_opens_investigation_tab_and_keeps_chat() -> None:
    out = thread_run(
        r"""
var m1 = chatMsg();
currentThreadId = "t-chat";
promptEl.value = "쓰던 질의";
threadBodies["t-inv"] = { turns: PINNED };
openThread("t-inv");
await tick(); await tick(); await tick();
var inInv = state();
setActiveView("chat");
out = { inInv: inInv, fetchView: fetches[0].view,
    back: { thread: currentThreadId, draft: promptEl.value,
        hasM1: chatMessages.children.indexOf(m1) >= 0, view: activeView } };
"""
    )
    inv = out["inInv"]
    assert inv["view"] == "investigation" and inv["slot"] == "investigation" and inv["tab"] == ""
    assert inv["shown"] == [
        {"thread": "t-inv", "slot": "investigation", "view": "investigation", "n": 1}
    ]
    assert inv["stored"] == {"id": "inv-1", "thread": "t-inv", "user": "demo"}
    assert out["fetchView"] == "chat"  # 탭은 대화를 받은 뒤에 바꾼다
    # 질의응답 대화는 swap으로 보존된다
    assert out["back"] == {"thread": "t-chat", "draft": "쓰던 질의", "hasM1": True, "view": "chat"}


def test_open_thread_with_pin_while_in_investigation_tab_stays_there() -> None:
    out = thread_run(
        r"""
currentThreadId = "t-chat";
showInvestigationTab(true);
setActiveView("investigation");
threadBodies["t-inv"] = { turns: PINNED };
openThread("t-inv");
await tick(); await tick(); await tick();
out = { st: state(), parkedThread: parkedConversation && parkedConversation.currentThreadId };
"""
    )
    st = out["st"]
    assert st["view"] == "investigation" and st["slot"] == "investigation"
    assert st["shown"][0]["slot"] == "investigation" and st["stored"] == {
        "id": "inv-1",
        "thread": "t-inv",
        "user": "demo",
    }
    assert out["parkedThread"] == "t-chat"


def test_open_thread_without_pin_from_investigation_tab_goes_to_chat_after_fetch() -> None:
    out = thread_run(
        r"""
showInvestigationTab(true);
setActiveView("investigation");
ssData["investigation_conversation"] = JSON.stringify({ id: "inv-9", thread: null, user: "demo" });
threadBodies["t-plain"] = { turns: PLAIN };
openThread("t-plain");
await tick(); await tick(); await tick();
out = { st: state(), fetchView: fetches[0].view };
"""
    )
    st = out["st"]
    assert out["fetchView"] == "investigation"  # 전환 시점이 fetch 뒤로 옮겨졌다
    assert st["view"] == "chat" and st["slot"] == "chat"
    assert st["shown"] == [{"thread": "t-plain", "slot": "chat", "view": "chat", "n": 1}]
    # 내려 둔 조사 대화의 저장값은 그대로 — 탭으로 돌아가면 그 대화가 있다
    assert st["stored"] == {"id": "inv-9", "thread": None, "user": "demo"}


def test_open_thread_without_pin_from_chat_stays_in_chat() -> None:
    out = thread_run(
        r"""
threadBodies["t-plain"] = { turns: PLAIN };
openThread("t-plain");
await tick(); await tick(); await tick();
out = state();
"""
    )
    assert out["view"] == "chat" and out["slot"] == "chat" and out["tab"] == "none"
    assert out["shown"] == [{"thread": "t-plain", "slot": "chat", "view": "chat", "n": 1}]
    assert out["stored"] is None


def test_open_thread_missing_keeps_current_tab() -> None:
    out = thread_run(
        r"""
showInvestigationTab(true);
setActiveView("investigation");
openThread("t-missing");
await tick(); await tick(); await tick();
out = state();
"""
    )
    assert out["view"] == "investigation" and out["shown"] == []
    assert len(out["errors"]) == 1


def test_open_thread_refused_while_processing() -> None:
    out = thread_run(
        r"""
isProcessing = true;
openThread("t-plain");
await tick();
out = { st: state(), fetches: fetches.length };
"""
    )
    assert out["fetches"] == 0 and out["st"]["errors"] == [
        "처리 중인 질의가 끝난 뒤 대화를 불러올 수 있습니다."
    ]


# ─── 고정 카드 꼬리 문구 ──────────────────────────────────────────────────

_NEW_TAIL = "새로고침해도 이 탭에 다시 열립니다. 질의 없이 닫으면 질의 이력에 남지 않습니다."
_OLD_TAIL = "질의 없이 닫으면 이 대화는 남지 않습니다."


@pytest.mark.parametrize("restored", [False, True])
def test_pin_tail_text(restored: bool) -> None:
    out = pin_run(f"out = pinOf('inv-1', {{ restored: {json.dumps(restored)} }}).el.innerHTML;")
    assert _OLD_TAIL not in out
    assert (_NEW_TAIL in out) is (not restored)


def test_pin_tail_text_static() -> None:
    body = body_of(APP_JS.read_text(encoding="utf-8"), "renderInvestigationPin", "app.js")
    assert _NEW_TAIL in body and _OLD_TAIL not in body
