"""plans/193 W2·W3 — 포털(`/`) 「노이즈 관제」 뷰 탭(플래그 아래)과
그 탭 서랍의 같은 창 조사 진입을 고정한다.

    - 플래그(기능 조회 `noise_console_portal_tab`) 거짓·조회 실패: 머리글 링크 유지 ·
      탭 숨김 · 관제 자산 요청 0. 참: 머리글 `/noise` 링크를 DOM에서 빼고 탭을 보인다
      (K-1 B 운용 규칙 1).
    - 첫 활성 때만 `/static/noise.html` 조각 조회 → 스크립트가 아닌 본문을 뷰로 옮기고
      (독립 전용 표지 요소 제거) 그 문서의 스타일시트·스크립트를 적힌 그대로(포털이 이미
      실은 파일은 건너뜀) 차례로 실은 뒤 mount 1회.
    - mount 인자: 사용자 모드(K-2 A) · 포털 토큰 · 401 = redirectToLogin · liveView = diagnosisOn ·
      서랍 진입 → openInvestigationEntry(W3 — 같은 창 장애 진단/조사 대화 탭).
    - 숨기면 pause · 다시 보이면 resume · 주소 `?view=noise` 동기(다른 쿼리 보존) ·
      로드 시 `?view=noise`는 진입·복원과 기능 조회가 끝난 뒤 마지막에 연다
      (최종 활성 탭 = 관제) · 꺼짐이면 파라미터만 지운다.
    - 불러오기 실패는 뷰 안 안내 + 다음 활성 때 재시도.

브라우저 대신 app.js 함수 본문을 잘라 가짜 DOM과 함께 node로 실행한다(node 없으면 생략).
실브라우저 장면(창 수·층·계산 스타일)은 계획서 W2 검증(Playwright)이 맡는다.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body
from tests.test_api.test_ui_plan192_w1_entry import app_defs, node_run

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_APP_JS = _STATIC / "js" / "app.js"
_NOISE_HTML = _STATIC / "noise.html"
_INDEX_HTML = _STATIC / "index.html"


class _Page(HTMLParser):
    """`noise.html`의 머리 스타일시트 · 본문 스크립트 · 본문 직계 요소(독립 전용 표지 포함 여부)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.scripts: list[str] = []
        self.top: list[dict[str, Any]] = []
        self.where = "head"
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "body":
            self.where = "body"
            return
        if self.where == "head":
            if tag == "link" and a.get("rel") == "stylesheet":
                self.links.append(a["href"])
            return
        if tag == "script":
            if self.depth == 0 and "src" in a:
                self.scripts.append(a["src"])
                self.top.append({"tag": "script", "cls": "", "standalone": 0})
            return
        if self.depth == 0:
            self.top.append({"tag": tag, "cls": a.get("class", ""), "standalone": 0})
        if "data-noise-standalone" in a:
            self.top[-1]["standalone"] += 1
        if tag not in {"input", "br", "img", "link", "meta", "hr"}:
            self.depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.where == "body" and tag not in {"body", "html", "script"}:
            self.depth -= 1


def _page() -> dict[str, Any]:
    p = _Page()
    p.feed(_NOISE_HTML.read_text(encoding="utf-8"))
    return {"links": p.links, "scripts": p.scripts, "top": p.top}


def _noise_entry_decl() -> str:
    """app.js의 `var noiseEntry = {…};` 초기화 실물(주소 판정을 하네스가 베끼지 않는다)."""
    src = _APP_JS.read_text(encoding="utf-8")
    m = re.search(r"^    var noiseEntry = \{.*?^    \};", src, re.S | re.M)
    assert m, "app.js에 noiseEntry 초기화가 없다"
    return m.group(0)


_PRELUDE = r"""
var window = globalThis;
function El(tag) {
    var self = this;
    this.tagName = String(tag || "div").toUpperCase();
    this.children = []; this.attrs = {}; this.style = {}; this.parentNode = null;
    this.className = ""; this.dataset = {}; this.onload = null; this.onerror = null;
    this.classList = {
        contains: function (c) { return (" " + self.className + " ").indexOf(" " + c + " ") >= 0; },
        toggle: function (c, on) {
            var has = self.classList.contains(c);
            if (on && !has) self.className = (self.className + " " + c).trim();
            if (!on && has) {
                self.className = self.className.split(" ")
                    .filter(function (x) { return x !== c; }).join(" ");
            }
        },
    };
}
Object.defineProperty(El.prototype, "textContent", {
    get: function () { return this._text || ""; },
    set: function (v) {
        var self = this;
        this.children.slice().forEach(function (c) { c.parentNode = null; });
        this.children = []; this._text = String(v); void self;
    },
});
El.prototype.appendChild = function (n) {
    if (n.parentNode) n.remove();
    n.parentNode = this; this.children.push(n);
    if (this.onChild) this.onChild(n);
    return n;
};
El.prototype.insertBefore = function (n) {
    if (n.parentNode) n.remove();
    n.parentNode = this; this.children.unshift(n); return n;
};
Object.defineProperty(El.prototype, "firstChild", {
    get: function () { return this.children[0] || null; },
});
El.prototype.remove = function () {
    if (!this.parentNode) return;
    var kids = this.parentNode.children;
    kids.splice(kids.indexOf(this), 1);
    if (this.parentNode.onRemove) this.parentNode.onRemove(this);
    this.parentNode = null;
};
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) {
    return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
El.prototype.walk = function (fn) {
    this.children.forEach(function (c) { fn(c); c.walk(fn); });
};
El.prototype.querySelector = function (sel) {
    var cls = /^\.([\w-]+)$/.exec(sel);
    var hit = null;
    this.walk(function (c) { if (!hit && cls && c.classList.contains(cls[1])) hit = c; });
    return hit;
};
El.prototype.querySelectorAll = function (sel) {
    var attr = /^\[([\w-]+)\]$/.exec(sel);
    var out = [];
    this.walk(function (c) { if (attr && c.getAttribute(attr[1]) !== null) out.push(c); });
    return out;
};

// ── 문서 ──
var requests = [];
var assetsLoaded = [];
var failAssets = {};
var head = new El("head");
var body = new El("body");
function tagOf(node, tag) { return node.tagName === tag.toUpperCase(); }
function mkAsset(tag, attr, url) { var n = new El(tag); n.setAttribute(attr, url); return n; }
// 포털이 이미 실은 자산(index.html 실물 값) — 같은 경로는 다시 싣지 않아야 한다
PORTAL_LINKS.forEach(function (u) {
    var n = mkAsset("link", "href", u);
    n.setAttribute("rel", "stylesheet");
    head.children.push(n); n.parentNode = head;
});
PORTAL_SCRIPTS.forEach(function (u) {
    var n = mkAsset("script", "src", u);
    body.children.push(n); n.parentNode = body;
});
function loadLater(n) {
    if (!(tagOf(n, "link") || tagOf(n, "script")) || !n.onload) return;
    var url = n.getAttribute(tagOf(n, "link") ? "href" : "src");
    requests.push(url);
    setTimeout(function () {
        if (failAssets[url.split("?")[0]]) { n.onerror(); return; }
        assetsLoaded.push(url);
        if (/\/noise\.js\?/.test(url)) window.NoiseConsole = FAKE_NC;
        n.onload();
    }, 0);
}
head.onChild = loadLater;
body.onChild = loadLater;
var links = { noiseConsoleLink: new El("a") };
links.noiseConsoleLink.setAttribute("href", "/noise");
var header = new El("nav");
header.appendChild(links.noiseConsoleLink);
var noiseView = new El("div"); noiseView.className = "noise-view view-hidden";
var chatView = new El("div"); chatView.className = "chat-layout";
var alarmView = new El("div"); alarmView.className = "alarm-view view-hidden";
var diagnosisView = new El("div"); diagnosisView.className = "diagnosis-view view-hidden";
function tabBtn(v) {
    var b = new El("button"); b.dataset.view = v; b.className = "view-tab"; return b;
}
var tabs = ["chat", "alarm", "noise", "diagnosis", "investigation"].map(tabBtn);
var noiseTab = tabs[2]; noiseTab.style.display = "none";
var document = {
    head: head, body: body,
    createElement: function (t) { return new El(t); },
    adoptNode: function (n) { return n; },
    getElementById: function (id) {
        var n = links[id];
        return n && n.parentNode ? n : null;
    },
    querySelectorAll: function (sel) {
        if (sel === ".view-tab") return tabs;
        if (sel === 'link[rel="stylesheet"]') {
            return head.children.filter(function (n) { return tagOf(n, "link"); });
        }
        if (sel === "script[src]") {
            return body.children.filter(function (n) { return tagOf(n, "script"); });
        }
        throw new Error("하네스에 없는 선택자: " + sel);
    },
};
// noise.html 조각 — 머리 스타일시트 · 본문 스크립트 · 본문 직계(독립 전용 표지 포함)
function DOMParser() {}
DOMParser.prototype.parseFromString = function () {
    var docBody = new El("body");
    PAGE.top.forEach(function (t) {
        var n = new El(t.tag); n.className = t.cls;
        for (var i = 0; i < t.standalone; i++) {
            var s = new El("a"); s.setAttribute("data-noise-standalone", ""); n.appendChild(s);
        }
        docBody.appendChild(n);
    });
    return {
        body: docBody,
        querySelectorAll: function (sel) {
            if (sel === 'head link[rel="stylesheet"]') {
                return PAGE.links.map(function (u) { return mkAsset("link", "href", u); });
            }
            if (sel === "body script[src]") {
                return PAGE.scripts.map(function (u) { return mkAsset("script", "src", u); });
            }
            throw new Error("하네스에 없는 조각 선택자: " + sel);
        },
    };
};
var pageStatus = 200;
function fetch(url, opts) {
    requests.push(url);
    return Promise.resolve({ ok: pageStatus < 300, status: pageStatus,
        text: function () { return Promise.resolve("<html></html>"); } });
}
var store = { user_token: "u-1" };
var localStorage = { getItem: function (k) {
    return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null;
} };
var replaced = [];
window.location = { pathname: "/", search: INITIAL_SEARCH, hash: "" };
var history = { replaceState: function (s, t, u) {
    replaced.push(u);
    var q = u.indexOf("?");
    window.location.search = q < 0 ? "" : u.slice(q).split("#")[0];
} };

// ── NoiseConsole 가짜(noise.js 스크립트가 「실린」 뒤에만 생긴다) ──
var mounts = [];
var handleLog = [];
var FAKE_NC = { mount: function (root, opts) {
    mounts.push({ root: root, opts: opts });
    return { pause: function () { handleLog.push("pause"); },
             resume: function () { handleLog.push("resume"); },
             unmount: function () { handleLog.push("unmount"); } };
} };

// ── 포털 전역 · 가짜 ──
var conversationSlot = "chat";
var activeView = "chat";
var alarmUnreadCount = 0;
var stickToBottom = false;
var chatMessages = null;
var swaps = [];
function swapConversation(v) { swaps.push(v); conversationSlot = v; return true; }
function renderAlarmBadge() {}
function updateAlarmScrollBtn() {}
var logins = 0;
function redirectToLogin() { logins += 1; }
var diagOn = false;
function diagnosisOn() { return diagOn; }
var entries = [];
function openInvestigationEntry(id, a) {
    entries.push([id, a === undefined ? null : a]);
    return true;
}
var alarmCapabilities = null;
var noiseTabEnabled = false;
var noiseHandle = null;
var noiseLoading = null;
var noiseFlash = null;
function settle() { return new Promise(function (r) { setTimeout(r, 15); }); }
function state() {
    return {
        view: activeView,
        noiseShown: !noiseView.classList.contains("view-hidden"),
        chatShown: !chatView.classList.contains("view-hidden"),
        tab: noiseTab.style.display,
        link: !!document.getElementById("noiseConsoleLink"),
        search: window.location.search,
        mounts: mounts.length,
        pageFetches: requests.filter(function (u) { return u === "/static/noise.html"; }).length,
        handle: handleLog.slice(),
    };
}
"""

_FUNCS = ("setActiveView", "applyNoiseTabCapability", "noiseEntrySettled", "loadNoiseConsole")


def _portal_assets() -> tuple[list[str], list[str]]:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
    scripts = re.findall(r'<script src="([^"]+)"', html)
    return links, scripts


def _run(body: str, search: str = "", drop: tuple[str, ...] = (), extra: str = "") -> Any:
    links, scripts = _portal_assets()
    harness = (
        f"var PAGE = {json.dumps(_page(), ensure_ascii=False)};\n"
        f"var PORTAL_LINKS = {json.dumps(links)};\n"
        f"var PORTAL_SCRIPTS = {json.dumps(scripts)};\n"
        f"var INITIAL_SEARCH = {json.dumps(search)};\n"
        + _PRELUDE
        + extra
    )
    for name in drop:  # 가짜를 빼면 실물을 끌어온다(예: openInvestigationEntry)
        harness = re.sub(rf"^function {name}\(.*?^\}}$", "", harness, flags=re.M | re.S)
    pre = harness + "\n" + _noise_entry_decl() + "\n"
    return node_run(pre, app_defs(_FUNCS, pre, body), body)


def _caps(on: bool | None) -> str:
    if on is None:
        return "alarmCapabilities = null;"  # 조회 실패
    return f"alarmCapabilities = {{ noise_console_portal_tab: {json.dumps(on)} }};"


# ── 플래그 ──


@pytest.mark.parametrize("caps", [False, None], ids=["flag-off", "caps-failed"])
def test_flag_off_keeps_link_hides_tab_and_loads_nothing(caps: bool | None) -> None:
    out = _run(
        _caps(caps)
        + r"""
applyNoiseTabCapability();
noiseEntrySettled();
await settle();
out = { st: state(), requests: requests, enabled: noiseTabEnabled };
"""
    )
    st = out["st"]
    assert out["enabled"] is False
    assert st["link"] is True and st["tab"] == "none"
    assert out["requests"] == [] and st["mounts"] == 0


def test_flag_on_removes_header_link_and_shows_tab_without_loading() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
noiseEntrySettled();
await settle();
out = { st: state(), requests: requests };
"""
    )
    st = out["st"]
    assert st["link"] is False and st["tab"] == ""
    # 탭을 열기 전에는 조각·자산 요청 0(지연 mount)
    assert out["requests"] == [] and st["mounts"] == 0 and st["view"] == "chat"


# ── 첫 활성 · mount ──


def test_first_activation_fetches_fragment_loads_assets_and_mounts_once() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
var first = state();
var kids = noiseView.children.map(function (c) {
    return c.tagName.toLowerCase() + "." + c.className;
});
var standalone = noiseView.querySelectorAll("[data-noise-standalone]").length;
setActiveView("chat");
setActiveView("noise");
await settle();
var m = mounts[0];
var tokenNow = m.opts.token();
store.user_token = "u-2";          // 다른 탭에서 다시 로그인 — 요청마다 읽으므로 새 값(교정 m-4)
out = { first: first, again: state(), kids: kids, standalone: standalone, requests: requests,
        loaded: assetsLoaded, root: m.root === noiseView,
        opts: { mode: m.opts.mode, token: [typeof m.opts.token, tokenNow, m.opts.token()],
                authFail: m.opts.onAuthFail === redirectToLogin,
                live: m.opts.liveView === diagnosisOn,
                keys: Object.keys(m.opts).sort() } };
"""
    )
    page = _page()
    first = out["first"]
    assert first["mounts"] == 1 and first["pageFetches"] == 1
    assert first["view"] == "noise" and first["noiseShown"] and not first["chatShown"]
    assert first["search"] == "?view=noise"
    # 본문 직계 중 스크립트가 아닌 것만 · 독립 전용 표지 요소는 빠진다
    expected = [f"{t['tag']}.{t['cls']}" for t in page["top"] if t["tag"] != "script"]
    assert out["kids"] == expected and len(expected) >= 3
    assert sum(t["standalone"] for t in page["top"]) == 1, "noise.html 「질의응답으로」 표지"
    assert out["standalone"] == 0
    # 자산은 noise.html에 적힌 그대로(버전 포함) — 포털이 이미 실은 경로
    # (style.css·briefing-view.js)는 건너뛴다
    portal_paths = {u.split("?")[0] for u in _portal_assets()[0] + _portal_assets()[1]}
    want = [u for u in page["links"] + page["scripts"] if u.split("?")[0] not in portal_paths]
    assert out["loaded"] == want
    assert any("noise-console.css?v=" in u for u in want)
    tail = [u.split("?")[0] for u in want[-2:]]
    assert tail == ["/static/js/noise-help.js", "/static/js/noise.js"]
    assert out["requests"] == ["/static/noise.html", *want]
    # 다시 열면 조각·자산·mount 반복 없음 — pause 뒤 resume
    again = out["again"]
    assert again["mounts"] == 1 and again["pageFetches"] == 1
    assert again["handle"] == ["pause", "resume"]
    assert out["root"] is True
    assert out["opts"] == {
        "mode": "user",            # K-2 A — 관리자도 읽기 전용 사용자 모드
        "token": ["function", "u-1", "u-2"],   # 포털 토큰(user_token)을 요청마다 읽는 게터
        "authFail": True,
        "live": True,
        "keys": ["liveView", "mode", "onAuthFail", "onForbidden", "onOpenInvestigation", "token"],
    }


def test_auth_off_passes_empty_token() -> None:
    out = _run(
        _caps(True)
        + r"""
delete store.user_token;
applyNoiseTabCapability();
setActiveView("noise");
await settle();
out = mounts[0].opts.token();
"""
    )
    assert out == ""  # 빈 값 = Authorization 헤더 없이(인증 꺼짐) · 로그인으로 보내지 않음


def test_leaving_pauses_and_other_query_is_kept() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
noiseEntrySettled();
setActiveView("noise");
await settle();
var on = window.location.search;
setActiveView("alarm");
out = { on: on, off: window.location.search, handle: handleLog, view: activeView,
        noiseShown: !noiseView.classList.contains("view-hidden") };
""",
        search="?keep=1",
    )
    assert out["on"] == "?keep=1&view=noise"
    assert out["off"] == "?keep=1"
    assert out["handle"] == ["pause"] and out["view"] == "alarm" and out["noiseShown"] is False


def test_switch_away_while_loading_pauses_right_after_mount() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
setActiveView("chat");     // 조각을 받는 사이 떠남
await settle();
out = { mounts: mounts.length, handle: handleLog };
"""
    )
    assert out == {"mounts": 1, "handle": ["pause"]}


def test_noise_tab_does_not_swap_conversation() -> None:
    """관제 탭은 대화 화면을 공유하지 않는다 — 스트리밍 중에도 바꿔 끼우기(거부 지점) 없이
    오간다."""
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
setActiveView("chat");
await settle();
out = { swaps: swaps, view: activeView, chatShown: !chatView.classList.contains("view-hidden") };
"""
    )
    assert out == {"swaps": [], "view": "chat", "chatShown": True}


# ── 실패 · 권한 ──


def test_fragment_failure_shows_notice_and_retries_next_activation() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
pageStatus = 503;
setActiveView("noise");
await settle();
var note = noiseView.querySelector(".noise-view-notice");
var first = { text: note && note.textContent, mounts: mounts.length,
              kids: noiseView.children.length };
pageStatus = 200;
setActiveView("chat");
setActiveView("noise");
await settle();
out = { first: first, mounts: mounts.length, fetches: state().pageFetches,
        notice: !!noiseView.querySelector(".noise-view-notice") };
"""
    )
    assert out["first"]["mounts"] == 0 and out["first"]["kids"] == 1
    assert "불러오지 못했습니다" in out["first"]["text"] and "HTTP 503" in out["first"]["text"]
    assert out["mounts"] == 1 and out["fetches"] == 2 and out["notice"] is False


def test_failed_script_is_dropped_and_reloaded_on_retry() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
failAssets["/static/js/noise.js"] = true;
setActiveView("noise");
await settle();
var left = body.children.filter(function (n) {
    return /noise\.js/.test(n.getAttribute("src") || "");
}).length;
delete failAssets["/static/js/noise.js"];
setActiveView("chat");
setActiveView("noise");
await settle();
var loads = assetsLoaded.map(function (u) { return u.split("?")[0]; });
out = { left: left, mounts: mounts.length,
        css: loads.filter(function (u) { return /noise-console/.test(u); }).length,
        help: loads.filter(function (u) { return /noise-help/.test(u); }).length,
        js: loads.filter(function (u) { return /\/noise\.js$/.test(u); }).length };
"""
    )
    # 실패한 스크립트 태그는 남기지 않는다(다시 실림) · 이미 실린 자산은 다시 싣지 않는다
    assert out == {"left": 0, "mounts": 1, "css": 1, "help": 1, "js": 1}


def test_forbidden_shows_notice_in_view() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
mounts[0].opts.onForbidden();
mounts[0].opts.onForbidden();
var notes = noiseView.children.filter(function (c) {
    return c.classList.contains("noise-view-notice");
});
out = { n: notes.length, text: notes[0].textContent,
        first: noiseView.children[0] === notes[0], logins: logins };
"""
    )
    assert out == {"n": 1, "text": "노이즈 관제를 볼 권한이 없습니다.", "first": True, "logins": 0}


# ── W3 조사 진입 ──


def test_open_investigation_goes_through_portal_entry() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
mounts[0].opts.onOpenInvestigation("inv-7");
out = entries;
"""
    )
    assert out == [["inv-7", None]]


@pytest.mark.parametrize(("diag", "dest"), [(True, "diagnosis"), (False, "conversation")])
def test_open_investigation_same_window_destination(diag: bool, dest: str) -> None:
    """실물 openInvestigationEntry — 진단 켜짐이면 장애 진단 하위 탭, 꺼짐이면
    조사 대화 탭(새 창 0)."""
    out = _run(
        _caps(True)
        + f"diagOn = {json.dumps(diag)};\n"
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
mounts[0].opts.onOpenInvestigation("inv-7");
mounts[0].opts.onOpenInvestigation("inv-7");
out = { went: went, live: mounts[0].opts.liveView() };
""",
        drop=("openInvestigationEntry",),
        extra=r"""
var went = [];
window.open = function () { went.push("window.open"); };
window.DiagnosisView = { openIfEnabled: function (id) {
    if (!diagOn) return false; went.push("diagnosis:" + id); return true; } };
function openInvestigationConversation(id) { went.push("conversation:" + id); return true; }
""",
    )
    assert out["went"] == [f"{dest}:inv-7", f"{dest}:inv-7"]
    assert out["live"] is diag  # 서랍 문구 판정(liveView)과 목적지 판정이 같은 값


def test_rejected_entry_flashes_reason_in_view_and_clears() -> None:
    """(plans/193 교정 M-1) 진입이 거부되면(답변 중) 사유를 관제 뷰 맨 앞 일시 안내로
    띄운다 — 한 개만, NOISE_FLASH_MS 뒤 또는 다음 진입 성공 때 사라진다.
    오래 남는 안내(권한 없음)는 건드리지 않는다."""
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
function flashes() {
    return noiseView.children.filter(function (c) {
        return c.classList.contains("noise-view-flash");
    });
}
function notes() {
    return noiseView.children.filter(function (c) {
        return c.classList.contains("noise-view-notice");
    })
        .map(function (c) { return c.textContent; });
}
function wait() { return new Promise(function (r) { setTimeout(r, 80); }); }
var open = mounts[0].opts.onOpenInvestigation;
isProcessing = true;
open("inv-7"); open("inv-7");          // 두 번 눌러도 안내는 하나
var f = flashes();
var shown = { n: f.length, text: f[0].textContent, first: noiseView.children[0] === f[0],
              role: f[0].getAttribute("role"), view: activeView };
await wait();
var afterTimeout = flashes().length;
open("inv-7");
isProcessing = false;
open("inv-7");                          // 성공 — 안내를 지운다
var afterSuccess = flashes().length;
isProcessing = true;
open("inv-7");
mounts[0].opts.onForbidden();           // 오래 남는 안내가 일시 안내를 대신한다
var replaced = { flashes: flashes().length, notes: notes() };
open("inv-7");                          // 오래 남는 안내 위에 뜨고, 사라져도 그것은 남는다
var stacked = notes();
await wait();
out = { shown: shown, afterTimeout: afterTimeout, afterSuccess: afterSuccess, replaced: replaced,
        stacked: stacked, left: notes(), went: went };
""",
        extra=r"""
var NOISE_FLASH_MS = 30;
var isProcessing = false;
var went = [];
// 하네스 가짜(늘 성공)를 덮는다 — 뒤 선언이 이긴다
function openInvestigationEntry(id) { went.push(id); return !isProcessing; }
""",
    )
    busy = "답변이 끝난 뒤 조사 대화를 열 수 있습니다."
    forbidden = "노이즈 관제를 볼 권한이 없습니다."
    assert out["shown"] == {"n": 1, "text": busy, "first": True, "role": "alert", "view": "noise"}
    assert out["afterTimeout"] == 0
    assert out["afterSuccess"] == 0
    assert out["replaced"] == {"flashes": 0, "notes": [forbidden]}
    assert out["stacked"] == [busy, forbidden]
    assert out["left"] == [forbidden]
    assert out["went"] == ["inv-7"] * 6


@pytest.mark.parametrize("failure", ["throw", "null"])
def test_mount_failure_stops_with_reload_notice(failure: str) -> None:
    """(plans/193 교정 m-1) mount가 예외·null이면 한 페이지 한 인스턴스(mountOnce)라
    다시 붙지 않는다 — 빈 마크업 대신 「새로고침하면 다시 시도합니다」를 남기고,
    다음 활성에서 조각을 다시 받지 않는다."""
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
setActiveView("noise");
await settle();
var first = { kids: noiseView.children.map(function (c) { return c.className; }),
              text: noiseView.children[0].textContent, handle: noiseHandle };
setActiveView("chat");
setActiveView("noise");
await settle();
out = { first: first, again: state(), kids: noiseView.children.length,
        loading: noiseLoading !== null };
""",
        extra=("FAKE_NC = { mount: function () { throw new Error(\"붙이기 실패\"); } };\n"
               if failure == "throw" else "FAKE_NC = { mount: function () { return null; } };\n"),
    )
    reason = "(붙이기 실패)" if failure == "throw" else ""
    assert out["first"] == {
        "kids": ["noise-view-notice"],
        "text": f"노이즈 관제를 시작하지 못했습니다{reason}. 새로고침하면 다시 시도합니다.",
        "handle": None,
    }
    assert out["again"]["pageFetches"] == 1 and out["again"]["handle"] == []
    assert out["kids"] == 1 and out["loading"] is True


# ── 로드 시 `?view=noise` ──


@pytest.mark.parametrize("order", ["caps-first", "entry-first"])
def test_view_param_opens_noise_after_entry_and_caps(order: str) -> None:
    first, second = (
        ("applyNoiseTabCapability", "noiseEntrySettled")
        if order == "caps-first"
        else ("noiseEntrySettled", "applyNoiseTabCapability")
    )
    out = _run(
        _caps(True)
        + f"""
{first}();
var mid = activeView;
// 복원이 조사 대화 탭을 연 뒤 진입이 끝난 경우를 흉내 — 최종 활성 탭은 관제여야 한다
setActiveView("investigation");
{second}();
await settle();
out = {{ mid: mid, st: state() }};
""",
        search="?view=noise",
    )
    assert out["mid"] == "chat"  # 둘 중 하나만 끝났을 때는 열지 않는다
    assert out["st"]["view"] == "noise" and out["st"]["search"] == "?view=noise"
    assert out["st"]["mounts"] == 1


def test_view_param_ignored_and_removed_when_flag_off() -> None:
    out = _run(
        _caps(False)
        + r"""
applyNoiseTabCapability();
noiseEntrySettled();
await settle();
out = { st: state(), requests: requests };
""",
        search="?view=noise&keep=1",
    )
    assert out["st"]["view"] == "chat" and out["st"]["search"] == "?keep=1"
    assert out["requests"] == []


def test_view_param_applies_once() -> None:
    out = _run(
        _caps(True)
        + r"""
applyNoiseTabCapability();
noiseEntrySettled();
await settle();
setActiveView("chat");
noiseEntrySettled();
applyNoiseTabCapability();
out = { view: activeView, search: window.location.search };
""",
        search="?view=noise",
    )
    assert out == {"view": "chat", "search": ""}


# ── 정적 ──


def test_markup_and_wiring() -> None:
    html = _INDEX_HTML.read_text(encoding="utf-8")
    tab = re.search(r'<button[^>]*id="noiseTab"[^>]*>', html)
    assert tab and 'data-view="noise"' in tab.group(0)
    assert 'aria-controls="noiseView"' in tab.group(0)
    assert 'style="display: none;"' in tab.group(0)
    # 이벤트 알람과 장애 진단 사이
    assert html.index('data-view="alarm"') < tab.start() < html.index('data-view="diagnosis"')
    assert '<div class="noise-view view-hidden" id="noiseView"></div>' in html
    # 마크업 사본 0(D-245) — 관제 id가 포털 템플릿에 없다
    for ident in ("closeDrawer", "decSearchBtn", "kpiPageSub", "rangeSeg"):
        assert f'id="{ident}"' not in html
    # 포털은 관제 자산을 미리 싣지 않는다(플래그 off면 0)
    for asset in ("noise.js", "noise-console.css", "noise-help.js"):
        assert asset not in html
    src = _APP_JS.read_text(encoding="utf-8")
    assert "noise: noiseView" in _js_function_body(src, "viewRegistry")
    assert "applyNoiseTabCapability();" in _js_function_body(src, "initAlarmSubscription")
    auth = _js_function_body(src, "checkAuthOnLoad")
    assert "}).then(noiseEntrySettled, noiseEntrySettled);" in auth
    css = (_STATIC / "css" / "style.css").read_text(encoding="utf-8")
    gate = css[css.index("body.auth-pending > header"):]
    assert ".noise-view" in gate[: gate.index("}")]
    view = css[css.index(".noise-view {"):]
    view = view[: view.index("}")]
    # 쌓임 맥락을 만들지 않는다 — 관제 오버레이가 머리글 위로 올라갈 수 있게
    assert "z-index" not in view and "position" not in view


def test_portal_overlay_layers_scoped_to_view() -> None:
    css = (_STATIC / "css" / "noise-console.css").read_text(encoding="utf-8")
    rules = dict(re.findall(r"^(\.noise-view [^{]+?)\s*\{\s*z-index:\s*(\d+)\s*\}", css, re.M))
    z = {k.strip(): int(v) for k, v in rules.items()}
    assert z[".noise-view .scrim.scrim--panel"] < z[".noise-view .scrim"]
    assert z[".noise-view .detail-panel"] < z[".noise-view .drawer"]
    assert min(z.values()) > 100  # 포털 머리글(sticky z 100) 위
    assert max(z.values()) < 2000  # 전역 스플래시 아래
    assert ".noise-view .info-pop,\n.noise-view .chart-tip { z-index: 150 }" in css
    for page in ("noise.html", "admin/noise.html"):
        assert "noise-console.css?v=11" in (_STATIC / page).read_text(encoding="utf-8")


def test_standalone_link_marked_only_on_user_page() -> None:
    user = _NOISE_HTML.read_text(encoding="utf-8")
    assert re.search(r'<a href="/" [^>]*data-noise-standalone[^>]*>질의응답으로</a>', user)
    assert user.count("data-noise-standalone") == 2  # 표지 1 + 설명 주석 1
    assert "data-noise-standalone" not in (_STATIC / "admin" / "noise.html").read_text(
        encoding="utf-8"
    )
