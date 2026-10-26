"""plans/193 W1 — `noise.js` mount 모듈화(동작 불변 리팩터 · D-337)를 고정한다.

    - 자동 mount: `<body data-noise-mode>`가 있을 때만 로드 시 붙는다(독립 두 페이지) · 없으면
      요청·타이머·토큰 조회 0.
    - mount 인자(포털 탭 W2용): `token` 명시 · `onAuthFail` · `onForbidden` · `liveView` ·
      `onOpenInvestigation` — 생략하면 종전 동작(리다이렉트·`/active` 1회·새 탭).
    - 핸들: `pause`(주기 갱신 0 · 팝오버·툴팁 닫기 · Esc 무시) · `resume` · `unmount`(스트림 중단).
    - `document` 범위였던 조회·부착을 mount 루트로 — 오버레이는 루트에 붙고 루트 밖
      `data-help`는 그대로.

브라우저 대신 실물 `noise.html`(사용자 화면) 마크업으로 가짜 DOM을 세워 `noise.js` 전체를 node로
실행한다(node 없으면 생략). 실브라우저 비교는 계획서 W1 검증(Playwright)이 맡는다.
"""

from __future__ import annotations

import json
import subprocess
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from tests.test_api.test_ui_plan192_w1_entry import body_of, need_node, node_run

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_NOISE_JS = _STATIC / "js" / "noise.js"
_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "source",
    "wbr",
}


class _Tree(HTMLParser):
    """`<body>` 안 마크업을 {tag, attrs, kids|text} 트리로(스크립트 제외)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict[str, Any] = {"tag": "body", "attrs": {}, "kids": []}
        self.stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "body":
            self.root["attrs"] = {k: v or "" for k, v in attrs}
            self.stack = [self.root]
            return
        if not self.stack:
            return
        node = {"tag": tag, "attrs": {k: v or "" for k, v in attrs}, "kids": []}
        self.stack[-1]["kids"].append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag == "body" or len(self.stack) <= 1 or tag in _VOID:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["tag"] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self.stack and self.stack[-1]["tag"] not in ("script", "style") and data.strip():
            self.stack[-1]["kids"].append({"text": data})


def _markup(page: str) -> dict[str, Any]:
    parser = _Tree()
    parser.feed((_STATIC / page).read_text(encoding="utf-8"))
    body = parser.root
    body["kids"] = [k for k in body["kids"] if k.get("tag") != "script"]
    return body


# 가짜 DOM · fetch · localStorage · 타이머(기록만 — 테스트가 직접 발화) · 창
_PRELUDE = r"""
var realSetTimeout = setTimeout;
var timers = { seq: 0, live: {} };
function addTimer(kind, fn, ms) {
    var id = ++timers.seq;
    timers.live[id] = { kind: kind, ms: ms, fn: fn };
    return id;
}
globalThis.setInterval = function (fn, ms) { return addTimer("interval", fn, ms); };
globalThis.setTimeout = function (fn, ms) { return addTimer("timeout", fn, ms); };
globalThis.clearInterval = globalThis.clearTimeout = function (id) { delete timers.live[id]; };
function liveTimers() {
    return Object.keys(timers.live).map(function (k) { return timers.live[k]; });
}
function intervals() {
    return liveTimers().filter(function (t) { return t.kind === "interval"; })
        .map(function (t) { return t.ms; }).sort();
}
function fire(ms) {
    liveTimers().forEach(function (t) {
        if (t.kind === "interval" && t.ms === ms) t.fn();
    });
}
function tick() { return new Promise(function (r) { realSetTimeout(r, 5); }); }

function camel(s) {
    return s.replace(/-([a-z])/g, function (_m, c) { return c.toUpperCase(); });
}
function Text(v) { this.nodeType = 3; this.nodeValue = v; this.parentNode = null; }
Object.defineProperty(Text.prototype, "textContent", {
    get: function () { return this.nodeValue; } });
function El(tag) {
    this.nodeType = 1; this.tagName = tag.toUpperCase(); this.attrs = {};
    this.childNodes = []; this.parentNode = null; this.style = {}; this.dataset = {};
    this.listeners = {}; this.value = ""; this.checked = false; this.disabled = false;
    this.hidden = false; this.title = ""; this._html = ""; this.offsetWidth = 0;
    this.offsetHeight = 0; this.tabIndex = -1; this.options = [];
    var self = this;
    function names() { return self.className.split(/\s+/).filter(Boolean); }
    this.classList = {
        has: function (c) { return names().indexOf(c) >= 0; },
        add: function (c) { if (!this.has(c)) self.className = names().concat([c]).join(" "); },
        remove: function (c) {
            self.className = names().filter(function (x) { return x !== c; }).join(" ");
        },
        toggle: function (c, on) {
            if (on === undefined) on = !this.has(c);
            if (on) this.add(c); else this.remove(c);
            return on;
        },
        contains: function (c) { return this.has(c); },
    };
}
function prop(proto, name, get, set) {
    Object.defineProperty(proto, name, { get: get, set: set });
}
prop(El.prototype, "id", function () { return this.attrs.id || ""; },
    function (v) { this.attrs.id = String(v); });
prop(El.prototype, "className", function () { return this.attrs["class"] || ""; },
    function (v) { this.attrs["class"] = String(v); });
prop(El.prototype, "children", function () {
    return this.childNodes.filter(function (n) { return n.nodeType === 1; });
});
prop(El.prototype, "firstChild", function () { return this.childNodes[0] || null; });
function nextSibling() {
    if (!this.parentNode) return null;
    var k = this.parentNode.childNodes;
    return k[k.indexOf(this) + 1] || null;
}
prop(El.prototype, "nextSibling", nextSibling);
prop(Text.prototype, "nextSibling", nextSibling);
prop(El.prototype, "textContent", function () {
    return this.childNodes.map(function (n) { return n.textContent; }).join("");
}, function (v) {
    this.childNodes = [];
    if (v !== "") this.appendChild(new Text(String(v)));
});
prop(El.prototype, "innerHTML", function () { return this._html; },
    function (v) { this.childNodes = []; this._html = String(v); });
El.prototype.setAttribute = function (k, v) {
    this.attrs[k] = String(v);
    if (k.indexOf("data-") === 0) this.dataset[camel(k.slice(5))] = String(v);
};
El.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
El.prototype.hasAttribute = function (k) { return k in this.attrs; };
El.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
El.prototype.appendChild = function (n) { return this.insertBefore(n, null); };
El.prototype.insertBefore = function (n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    n.parentNode = this;
    var at = ref ? this.childNodes.indexOf(ref) : -1;
    if (at < 0) this.childNodes.push(n); else this.childNodes.splice(at, 0, n);
    return n;
};
El.prototype.removeChild = function (n) {
    this.childNodes.splice(this.childNodes.indexOf(n), 1);
    n.parentNode = null;
    return n;
};
El.prototype.contains = function (n) {
    for (; n; n = n.parentNode) if (n === this) return true;
    return false;
};
El.prototype.addEventListener = function (t, f) {
    (this.listeners[t] = this.listeners[t] || []).push(f);
};
El.prototype.dispatch = function (t, e) {
    (this.listeners[t] || []).forEach(function (f) { f(e); });
};
El.prototype.focus = function () { document.activeElement = this; };
El.prototype.scrollIntoView = function () {};
El.prototype.getBoundingClientRect = function () {
    return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
};

// 단순 선택자 — 태그 · #id · .class · [attr] · [attr="v"] · 자손 결합(공백) · 쉼표 목록
function compound(node, part) {
    if (node.nodeType !== 1) return false;
    var re = /([a-zA-Z][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g, m;
    while ((m = re.exec(part))) {
        if (m[1] && node.tagName !== m[1].toUpperCase()) return false;
        if (m[2] && node.id !== m[2]) return false;
        if (m[3] && !node.classList.has(m[3])) return false;
        if (m[4] && !(m[4] in node.attrs)) return false;
        if (m[4] && m[5] !== undefined && node.attrs[m[4]] !== m[5]) return false;
    }
    return true;
}
function matches(node, sel) {
    return sel.split(",").some(function (one) {
        var parts = one.trim().split(/\s+/);
        if (!compound(node, parts[parts.length - 1])) return false;
        var i = parts.length - 2;
        for (var p = node.parentNode; p && i >= 0; p = p.parentNode) {
            if (compound(p, parts[i])) i--;
        }
        return i < 0;
    });
}
El.prototype.querySelectorAll = function (sel) {
    var out = [];
    (function walk(n) {
        n.childNodes.forEach(function (c) {
            if (c.nodeType !== 1) return;
            if (matches(c, sel)) out.push(c);
            walk(c);
        });
    })(this);
    return out;
};
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
El.prototype.closest = function (sel) {
    for (var n = this; n && n.nodeType === 1; n = n.parentNode) if (matches(n, sel)) return n;
    return null;
};

function build(spec) {
    if (spec.text !== undefined) return new Text(spec.text);
    var node = new El(spec.tag);
    Object.keys(spec.attrs).forEach(function (k) { node.setAttribute(k, spec.attrs[k]); });
    if (node.tagName === "INPUT" && spec.attrs.value) node.value = spec.attrs.value;
    if ("checked" in spec.attrs) node.checked = true;
    spec.kids.forEach(function (k) { node.appendChild(build(k)); });
    return node;
}

var docListeners = {};
var html = new El("html");
var document = globalThis.document = {
    body: null, activeElement: null,
    documentElement: { clientWidth: 1280, clientHeight: 800 },
    createElement: function (t) { return new El(t); },
    createTextNode: function (v) { return new Text(String(v)); },
    getElementById: function (id) { return html.querySelector("#" + id); },
    addEventListener: function (t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
};
function setBody(spec, mode) {
    var body = build(spec);
    if (mode === null) delete body.attrs["data-noise-mode"];
    else body.setAttribute("data-noise-mode", mode);
    html.childNodes = [];
    html.appendChild(body);
    document.body = body;
    document.activeElement = body;
    return body;
}

var store = {};
var reads = [];
var removed = [];
globalThis.localStorage = {
    getItem: function (k) { reads.push(k); return k in store ? store[k] : null; },
    removeItem: function (k) { removed.push(k); delete store[k]; },
};
var calls = [];
var statusFor = {};   // 경로 앞부분 → 상태 코드
var aborted = [];
var streamHold = false;   // 참이면 스트림 본문이 열린 채로 남는다(abort 때 읽기 실패)
function heldBody(signal) {
    return { getReader: function () { return { read: function () {
        return new Promise(function (_r, rej) {
            signal.addEventListener("abort", function () { rej(new Error("aborted")); });
        });
    } }; } };
}
globalThis.fetch = function (url, o) {
    o = o || {};
    calls.push({ url: url, auth: (o.headers || {}).Authorization || null });
    if (o.signal) o.signal.addEventListener("abort", function () { aborted.push(url); });
    var status = 200;
    Object.keys(statusFor).forEach(function (p) {
        if (url.indexOf(p) === 0) status = statusFor[p];
    });
    var body = streamHold && o.signal ? heldBody(o.signal) : null;
    return Promise.resolve({ ok: status < 300, status: status, body: body,
        json: function () { return Promise.resolve({}); } });
};
var opened = [];
var window = globalThis.window = globalThis;
window.location = { href: "about:start" };
window.innerWidth = 1280;
window.innerHeight = 800;
window.open = function (u, n, f) { opened.push([u, n, f]); return null; };
window.addEventListener = function () {};
window.BriefingView = {
    responseOf: function (res) {
        return res.json().then(function (b) {
            return { ok: res.ok, status: res.status, body: b };
        });
    },
};
function urls() { return calls.map(function (c) { return c.url; }); }
function load() { require(NOISE_JS); return window.NoiseConsole; }
"""


def _run(body: str, page: str = "noise.html", mode: str | None = "user") -> Any:
    prelude = (
        f"var NOISE_JS = {json.dumps(str(_NOISE_JS))};\n"
        + _PRELUDE
        + f"var MARKUP = {json.dumps(_markup(page), ensure_ascii=False)};\n"
        + f"setBody(MARKUP, {json.dumps(mode)});\n"
    )
    # node_run의 tick은 전역 setTimeout을 쓴다 — 이 하네스는 그것을 가로채므로 자체 실행기
    need_node()
    script = "\n".join(
        [
            '"use strict";',
            prelude,
            "var out = null;",
            "(async function () {",
            body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error((e && e.stack) || e); process.exit(1); });",
        ]
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout, "본문이 끝나지 않았다(대기 중인 tick?)"
    return json.loads(done.stdout)


_USER_FIRST = [
    "/api/v1/noise/summary?range=24h",
    "/api/v1/noise/timeseries",
    "/api/v1/noise/top-suppressed",
    "/api/v1/noise/decisions",
]


def _starts(urls: list[str], prefix: str) -> int:
    return sum(1 for u in urls if u.startswith(prefix))


# ── 자동 mount ──


def test_auto_mount_with_attribute_runs_as_before() -> None:
    out = _run(
        r"""
store.user_token = "u-1";
var NC = load();
await tick();
out = { urls: urls(), auths: calls.map(function (c) { return c.auth; }), reads: reads,
        intervals: intervals(), api: Object.keys(NC), href: window.location.href };
"""
    )
    assert out["api"] == ["mount"]
    assert out["reads"] == ["user_token"]  # 사용자 화면 — user_token 먼저
    assert out["intervals"] == [30000, 60000]
    assert out["href"] == "about:start"
    urls = out["urls"]
    for prefix in _USER_FIRST:
        assert _starts(urls, prefix) >= 1, prefix
    assert _starts(urls, "/api/v1/alarm/investigations/active") == 1
    assert _starts(urls, "/api/v1/alarm/capabilities") == 1
    assert _starts(urls, "/api/v1/admin/") == 0  # 사용자 화면은 운영자 API·스트림 0
    assert set(out["auths"]) == {"Bearer u-1"}


def test_no_attribute_means_no_mount() -> None:
    out = _run(
        r"""
store.user_token = "u-1";
var NC = load();
await tick();
out = { calls: calls.length, reads: reads.length, timers: Object.keys(timers.live).length,
        mount: typeof NC.mount, docKeys: Object.keys(docListeners),
        help: html.querySelectorAll("[data-help-bound]").length };
""",
        mode=None,
    )
    assert out == {
        "calls": 0,
        "reads": 0,
        "timers": 0,
        "mount": "function",
        "docKeys": [],
        "help": 0,
    }


def test_missing_token_redirects_and_mount_returns_null() -> None:
    out = _run(
        r"""
var NC = load();
var again = NC.mount(document.body, { mode: "user" });
out = { href: window.location.href, calls: calls.length, timers: Object.keys(timers.live).length,
        again: again };
"""
    )
    assert out == {"href": "/login?next=%2Fnoise", "calls": 0, "timers": 0, "again": None}


def test_admin_page_break_glass_skips_capabilities() -> None:
    # 독립 운영자 화면 + admin_token = break-glass — 기능 조회(사용자 토큰 인가)를 안 부른다(종전)
    out = _run(
        r"""
store.admin_token = "a-1";
load();
await tick();
out = { urls: urls(), reads: reads };
""",
        page="admin/noise.html",
        mode="admin",
    )
    assert out["reads"] == ["admin_token"]
    assert _starts(out["urls"], "/api/v1/alarm/capabilities") == 0
    assert _starts(out["urls"], "/api/v1/admin/noise/stream") == 1


# ── mount 인자 ──

_PORTAL = r"""
store.admin_token = "a-should-not-be-read";
var NC = load();
var host = new El("div");
host.setAttribute("id", "portalNoise");
var outside = new El("h2");
outside.setAttribute("data-help", "outside");
outside.appendChild(new Text("포털 사용법"));
// 포털 화면: body에 속성 없음 · 관제 마크업은 host로 옮긴다 · host 밖에 data-help 표지
var kids = document.body.childNodes.slice();
kids.forEach(function (k) { host.appendChild(k); });
document.body.appendChild(outside);
document.body.appendChild(host);
"""


def test_explicit_token_skips_storage_and_is_not_break_glass() -> None:
    out = _run(
        _PORTAL
        + r"""
var h = NC.mount(host, { mode: "user", token: "p-1" });
await tick();
out = { reads: reads, auths: calls.map(function (c) { return c.auth; }), urls: urls(),
        handle: Object.keys(h).sort(), href: window.location.href };
""",
        mode=None,
    )
    assert out["reads"] == []
    assert set(out["auths"]) == {"Bearer p-1"}
    # break-glass가 아니다 — 기능 조회(수동 조사 모드 판정)를 부른다
    assert _starts(out["urls"], "/api/v1/alarm/capabilities") == 1
    assert out["handle"] == ["pause", "resume", "unmount"]
    assert out["href"] == "about:start"


def test_empty_token_sends_no_authorization_and_no_redirect() -> None:
    out = _run(
        _PORTAL
        + r"""
NC.mount(host, { mode: "user", token: "" });
await tick();
out = { reads: reads, auths: calls.map(function (c) { return c.auth; }), n: calls.length,
        href: window.location.href };
""",
        mode=None,
    )
    assert out["reads"] == [] and out["href"] == "about:start"
    assert out["n"] > 0 and set(out["auths"]) == {None}


def test_token_getter_is_read_on_every_request() -> None:
    """(plans/193 교정 m-4) mount `token`이 함수면 요청마다 불러 그 값을 싣는다.

    저장소는 읽지 않는다.
    """
    out = _run(
        _PORTAL
        + r"""
var tk = "p-1";
NC.mount(host, { mode: "user", token: function () { return tk; } });
await tick();
var first = calls.map(function (c) { return c.auth; });
var n = calls.length;
tk = "p-2";                      // 다른 탭에서 다시 로그인
fire(30000);
await tick();
var second = calls.slice(n).map(function (c) { return c.auth; });
n = calls.length;
tk = "";                         // 빈 값 — 헤더 없이(인증 꺼짐)
fire(30000);
await tick();
out = { first: first, second: second, third: calls.slice(n).map(function (c) { return c.auth; }),
        reads: reads, href: window.location.href };
""",
        mode=None,
    )
    assert out["first"] and set(out["first"]) == {"Bearer p-1"}
    assert out["second"] and set(out["second"]) == {"Bearer p-2"}
    assert out["third"] and set(out["third"]) == {None}
    assert out["reads"] == [] and out["href"] == "about:start"


def test_on_auth_fail_and_on_forbidden_replace_redirects() -> None:
    out = _run(
        _PORTAL
        + r"""
var fails = 0, forbids = 0;
statusFor["/api/v1/noise/summary"] = 401;
statusFor["/api/v1/noise/timeseries"] = 403;
NC.mount(host, { mode: "user", token: "p-1",
    onAuthFail: function () { fails++; }, onForbidden: function () { forbids++; } });
await tick(); await tick();
out = { fails: fails, forbids: forbids, removed: removed, href: window.location.href };
""",
        mode=None,
    )
    assert out == {"fails": 1, "forbids": 1, "removed": [], "href": "about:start"}


def test_default_auth_fail_and_forbidden_keep_redirects() -> None:
    out = _run(
        r"""
store.user_token = "u-1";
statusFor["/api/v1/noise/timeseries"] = 403;
load();
await tick(); await tick();
var forbidHref = window.location.href;
statusFor["/api/v1/noise/decisions"] = 401;
fire(30000);
await tick(); await tick();
out = { forbidHref: forbidHref, href: window.location.href, removed: removed };
"""
    )
    assert out == {"forbidHref": "/", "href": "/login?next=%2Fnoise", "removed": ["user_token"]}


def test_live_view_option_replaces_active_probe() -> None:
    out = _run(
        _PORTAL
        + r"""
NC.mount(host, { mode: "user", token: "p-1", liveView: function () { return true; } });
await tick();
out = { active: urls().filter(function (u) {
    return u.indexOf("/investigations/active") >= 0;
}).length };
""",
        mode=None,
    )
    assert out == {"active": 0}


def test_live_view_and_open_investigation_are_read_at_call_time() -> None:
    src = _NOISE_JS.read_text(encoding="utf-8")
    defs = body_of(src, "liveViewNow", "noise.js") + "\n" + body_of(src, "openInPortal", "noise.js")
    out = node_run(
        r"""
var opened = [];
var window = { open: function (u, n, f) { opened.push([u, n, f]); return null; } };
var liveViewOn = false;
var on = false;
var opts = { liveView: function () { return on; } };
""",
        defs,
        r"""
var first = liveViewNow();
openInPortal("i-1");
on = true;
var second = liveViewNow();
openInPortal("i-2");
var hooked = [];
opts.onOpenInvestigation = function (id) { hooked.push(id); };
openInPortal("i-3");
delete opts.liveView;
liveViewOn = true;
out = { first: first, second: second, opened: opened, hooked: hooked, fallback: liveViewNow() };
""",
    )
    assert out == {
        "first": False,
        "second": True,
        "opened": [
            ["/?conversation=i-1", "_blank", "noopener"],
            ["/?diagnosis=i-2", "_blank", "noopener"],
        ],
        "hooked": ["i-3"],
        "fallback": True,
    }


# ── 핸들 ──


def test_pause_stops_timers_and_closes_overlays_resume_restarts() -> None:
    out = _run(
        _PORTAL
        + r"""
var h = NC.mount(host, { mode: "user", token: "p-1" });
await tick();
var base = calls.length;
// 설명 팝오버를 열고 차트 툴팁을 켠 채로 pause
var info = host.querySelector(".info-btn");
info.dispatch("click", { stopPropagation: function () {} });
var popOpen = document.getElementById("infoPop").classList.contains("on");
document.getElementById("chartTip").classList.add("on");
h.pause();
var paused = { intervals: intervals(),
    pop: document.getElementById("infoPop").classList.contains("on"),
    tip: document.getElementById("chartTip").classList.contains("on") };
fire(30000); fire(60000);
await tick();
var whilePaused = calls.length - base;
// 일시정지 중 Esc는 무시
document.getElementById("chartTip").classList.add("on");
var prevented = 0;
var esc = { key: "Escape", preventDefault: function () { prevented++; } };
docListeners.keydown.forEach(function (f) { f(esc); });
var tipAfterEsc = document.getElementById("chartTip").classList.contains("on");
// 갱신이 30초 안이면 resume은 타이머만 다시 건다
// — 차트는 가진 데이터로 한 번 다시 그린다(교정 m-6 · 요청 0)
document.getElementById("chart").innerHTML = "STALE";
h.resume();
await tick();
var resumedFresh = { intervals: intervals(), extra: calls.length - base,
    chartRedrawn: document.getElementById("chart").innerHTML !== "STALE" };
// resume의 차트 재렌더가 툴팁을 닫으므로 다시 켜고 Esc가 살아났는지 본다
document.getElementById("chartTip").classList.add("on");
docListeners.keydown.forEach(function (f) { f(esc); });
var tipAfterResumeEsc = document.getElementById("chartTip").classList.contains("on");
// 30초를 넘겨 다시 보이면 곧바로 집계·피드를 다시 읽는다
h.pause();
var realNow = Date.now;
Date.now = function () { return realNow() + 31000; };
var before = calls.length;
h.resume();
await tick();
var stale = urls().slice(before);
out = { popOpen: popOpen, paused: paused, whilePaused: whilePaused, tipAfterEsc: tipAfterEsc,
        prevented: prevented, resumedFresh: resumedFresh, tipAfterResumeEsc: tipAfterResumeEsc,
        stale: stale };
""",
        mode=None,
    )
    assert out["popOpen"] is True
    assert out["paused"] == {"intervals": [], "pop": False, "tip": False}
    assert out["whilePaused"] == 0
    assert out["tipAfterEsc"] is True
    assert out["resumedFresh"] == {"intervals": [30000, 60000], "extra": 0, "chartRedrawn": True}
    assert out["tipAfterResumeEsc"] is False and out["prevented"] == 1
    for prefix in _USER_FIRST:
        assert _starts(out["stale"], prefix) >= 1, prefix


_LIFE_HARNESS = r"""
var RESYNC_MS = 30000;
var life = { paused: false, dead: false, summaryAt: 0, feedAt: 0, reconnectDue: false,
             summaryTimer: null, resyncTimer: null, reconnectTimer: null, panelDue: false };
var log = [];
function closeInfoPop() {}
function hideChartTip() {}
function startTimers() { log.push("timers"); }
function refreshAggregates() { log.push("refresh"); }
function renderChart() { log.push("chart"); }
function connectStream() { log.push("stream"); }
function panel(withPoller) {
    var p = { close: function () { log.push("close"); } };
    if (withPoller) {
        p.poller = { stop: function () { log.push("stop"); },
                     start: function (a) {
                         log.push(a === undefined ? "start" : "start:initial");
                     } };
    }
    return p;
}
var investigationPanel = null;
var investigationView = null;
var window = { BriefingView: { isActive: function (v) { return v.status === "running"; } } };
"""


def test_pause_stops_drawer_briefing_poller_and_resume_restarts_only_live() -> None:
    """(plans/193 교정 m-3·m-6) pause는 열린 서랍의 조사 브리핑 재조회(poller)만 멈춘다
    (close·DOM은 손대지 않음). resume은 마지막으로 그린 조사가 진행 중이거나 첫 응답 전일
    때만 다시 건다. 차트는 resume마다 가진 데이터로 한 번 다시 그린다
    (renderChart는 데이터가 없으면 그냥 돌아온다 · 요청 없음)."""
    src = _NOISE_JS.read_text(encoding="utf-8")
    defs = body_of(src, "pause", "noise.js") + "\n" + body_of(src, "resume", "noise.js")
    out = node_run(
        _LIFE_HARNESS,
        defs,
        r"""
function cycle(view, withPoller) {
    log = [];
    life.summaryAt = life.feedAt = Date.now();
    investigationPanel = panel(withPoller);
    investigationView = view;
    pause(); pause();                 // 두 번 불러도 한 번
    var paused = log.slice();
    resume(); resume();
    return { paused: paused, resumed: log.slice(paused.length), due: life.panelDue };
}
var running = cycle({ status: "running" }, true);
var done = cycle({ status: "done" }, true);
var firstLoad = cycle(null, true);
var noPoller = cycle({ status: "running" }, false);
investigationPanel = null;
log = [];
pause(); resume();
out = { running: running, done: done, firstLoad: firstLoad, noPoller: noPoller, none: log };
""",
    )
    assert out["running"] == {
        "paused": ["stop"], "resumed": ["timers", "chart", "start"], "due": False,
    }
    assert out["done"] == {"paused": ["stop"], "resumed": ["timers", "chart"], "due": False}
    assert out["firstLoad"]["resumed"] == ["timers", "chart", "start"]
    assert out["noPoller"] == {"paused": [], "resumed": ["timers", "chart"], "due": False}
    assert out["none"] == ["timers", "chart"]


def test_unmount_aborts_admin_stream_without_reconnect() -> None:
    out = _run(
        r"""
store.admin_token = "a-1";
var NC = load();
await tick(); await tick();
// 스트림 응답 본문이 없어 끊김 → 재연결 타이머가 걸린 상태
function timeouts(pred) {
    return liveTimers().filter(function (t) { return t.kind === "timeout" && pred(t); }).length;
}
var pending = timeouts(function (t) { return t.ms === 1000; });
var h = NC.mount(document.body, { mode: "user" });   // 두 번째 mount — 같은 핸들 · 인자 무시
h.pause();
var pausedTimeouts = timeouts(function (t) { return t.ms === 1000; });
streamHold = true;   // resume이 다시 붙인 연결은 열린 채로 남는다
h.resume();
await tick(); await tick();
var streams = urls().filter(function (u) { return u === "/api/v1/admin/noise/stream"; }).length;
h.unmount();
await tick();
var after = calls.length;
h.resume();
fire(30000); fire(60000);
await tick();
out = { pending: pending, pausedTimeouts: pausedTimeouts, streams: streams, aborted: aborted,
        intervals: intervals(), afterUnmount: calls.length - after,
        reconnects: timeouts(function (t) { return String(t.fn).indexOf("connectStream") >= 0; }) };
""",
        page="admin/noise.html",
        mode="admin",
    )
    assert out["pending"] == 1
    assert out["pausedTimeouts"] == 0
    assert out["streams"] == 2  # 첫 연결 + resume이 미뤄 둔 재연결
    assert out["intervals"] == [] and out["afterUnmount"] == 0
    assert out["reconnects"] == 0
    assert out["aborted"] == ["/api/v1/admin/noise/stream"]  # 열린 연결 중단 · 이후 재연결 없음


def test_second_mount_returns_same_handle() -> None:
    out = _run(
        _PORTAL
        + r"""
var a = NC.mount(host, { mode: "user", token: "p-1" });
await tick();
var n = calls.length;
var b = NC.mount(document.body, { mode: "admin", token: "x" });
await tick();
out = { same: a === b, extra: calls.length - n, intervals: intervals() };
""",
        mode=None,
    )
    assert out == {"same": True, "extra": 0, "intervals": [30000, 60000]}


def test_overlays_attach_to_root_and_help_stays_inside() -> None:
    out = _run(
        _PORTAL
        + r"""
NC.mount(host, { mode: "user", token: "p-1" });
await tick();
function parentId(id) { var n = document.getElementById(id); return n && n.parentNode === host; }
var panel = document.getElementById("panelScrim");
out = { tip: parentId("chartTip"), info: parentId("infoPop"), scrim: parentId("panelScrim"),
        panelRoot: panel.nextSibling ? panel.nextSibling.parentNode === host : false,
        bodyKids: document.body.children.length,
        outsideBound: outside.hasAttribute("data-help-bound"),
        outsideBtn: outside.querySelectorAll(".info-btn").length,
        insideBound: host.querySelectorAll("[data-help-bound]").length > 0 };
""",
        mode=None,
    )
    assert out == {
        "tip": True,
        "info": True,
        "scrim": True,
        "panelRoot": True,
        "bodyKids": 2,
        "outsideBound": False,
        "outsideBtn": 0,
        "insideBound": True,
    }
