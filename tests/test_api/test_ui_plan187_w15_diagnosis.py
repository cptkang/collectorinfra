"""plans/187 W15 — 화면 「장애 진단」 뷰 탭(§5.6.3 ⓓ · G-34~G-40).

브라우저 e2e는 이 환경에서 돌지 않으므로 두 갈래로 고정한다:
    1. `diagnosis-view.js`를 node로 실제 실행한다 — 가짜 DOM·EventSource·fetch·타이머를 주입해
       사전 게이트(404·401이면 탭 숨김·스트림 0) · 같은 id 두 번 = 탭 1개 · 탭 20개 = 연결 1개 ·
       사용자 화면 AI 메모 행 0 · 3회 실패 강등 문구 · 401 재연결 루프 금지 · 종결 처리를 단언한다.
    2. 정적 — 탭 마크업 · 스크립트 순서 · 진입 배선(app.js·noise.js) ·
       innerHTML 미사용(서버 텍스트).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body
from tests.test_plan187_w5b_static import _done_view, _running_view

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_DV_JS = _STATIC / "js" / "diagnosis-view.js"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_APP_JS = _STATIC / "js" / "app.js"
_NOISE_JS = _STATIC / "js" / "noise.js"

_HARNESS = r"""
function Node(tag) {
    this.tagName = tag; this.children = []; this.parentNode = null; this._text = "";
        this._html = null;
    this.className = ""; this.attrs = {}; this.style = {}; this.hidden = false; this.listeners = {};
    this.scrollTop = 0; this.isConnected = true; this.type = ""; this.checked = false;
        this.title = "";
    var self = this;
    this.classList = {
        toggle: function (c, on) {
            var list = self.className.split(" ").filter(Boolean)
                .filter(function (x) { return x !== c; });
            if (on) list.push(c);
            self.className = list.join(" ");
        },
        contains: function (c) { return self.className.split(" ").indexOf(c) >= 0; },
    };
}
Object.defineProperty(Node.prototype, "textContent", {
    get: function () {
        if (this._html !== null) return this._html;
        return this._text + this.children.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) {
        this.children.forEach(function (c) { c.parentNode = null; });
        this.children = []; this._text = String(v); this._html = null;
    },
});
Object.defineProperty(Node.prototype, "innerHTML", {
    get: function () { return this._html || ""; },
    set: function (v) { this.children = []; this._text = ""; this._html = String(v);
        htmlWrites.push(this); },
});
Object.defineProperty(Node.prototype, "firstChild",
    { get: function () { return this.children[0] || null; } });
Node.prototype.appendChild = function (c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this; this.children.push(c); return c;
};
Node.prototype.removeChild = function (c) {
    var i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1);
        c.parentNode = null; return c;
};
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
Node.prototype.getAttribute = function (k) { return k in this.attrs ? this.attrs[k] : null; };
Node.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
Node.prototype.addEventListener = function (t,
    f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
Node.prototype.fire = function (t) { var me = this;
    (this.listeners[t] || []).forEach(function (f) { f({ target: me }); }); };
Node.prototype.click = function () { this.fire("click"); };
Node.prototype.contains = function () { return true; };
Node.prototype.querySelectorAll = function () { return []; };
Node.prototype.querySelector = function () { return null; };
var htmlWrites = [];
var document = { createElement: function (t) { return new Node(t); } };
function findAll(node, cls) {
    var out = [];
    (function walk(n) {
        if (n.className && n.className.split(" ").indexOf(cls) >= 0) out.push(n);
        n.children.forEach(walk);
    })(node);
    return out;
}
function texts(node, cls) { return findAll(node, cls).map(function (n) { return n.textContent; }); }

var esList = [];
function FakeES(url) { this.url = url; this.readyState = 0; this.listeners = {};
    this.closed = false; esList.push(this); }
FakeES.prototype.addEventListener = function (t,
    f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
FakeES.prototype.close = function () { this.closed = true; this.readyState = 2; };
FakeES.prototype.emit = function (t, data) {
    (this.listeners[t] || []).forEach(function (f) { f({ data: JSON.stringify(data) }); });
};
function liveES() { return esList.filter(function (e) { return !e.closed; }); }

var timers = []; var timerSeq = 0; var clock = 1000000;
function setT(f, ms) { timerSeq += 1; timers.push({ id: timerSeq, f: f, ms: ms });
    return timerSeq; }
function clearT(id) { timers = timers.filter(function (t) { return t.id !== id; }); }
function runTimers(maxMs) {
    var due = timers.filter(function (t) { return maxMs === undefined || t.ms <= maxMs; });
    timers = timers.filter(function (t) { return due.indexOf(t) < 0; });
    due.forEach(function (t) { t.f(); });
}
function flush() { return new Promise(function (r) { setTimeout(r, 0); }).then(function () {
    return new Promise(function (r) { setTimeout(r, 0); }); }); }

var routes = {}; var calls = [];
function fakeFetch(url) {
    calls.push(url);
    var path = url.split("?")[0];
    var hit = routes[url] || routes[path] || { status: 404, body: { detail: "Not Found" } };
    if (typeof hit === "function") hit = hit(url);
    return Promise.resolve({ ok: hit.status >= 200 && hit.status < 300, status: hit.status,
        json: function () { return Promise.resolve(hit.body); } });
}
var store = {};
var storage = { getItem: function (k) { return k in store ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); } };
var opened = []; var activated = 0;
function make(operator) {
    var rootEl = new Node("div"); var tabBtn = new Node("button"); var badge = new Node("span");
    var ctl = DV.create({
        root: rootEl, tabButton: tabBtn, badge: badge, operator: !!operator,
        headers: function () { return {}; },
        onActivate: function () { activated += 1; },
        onOpenConversation: function (id) { opened.push(id); },
        analyzeFor: function () { return null; },
        deps: { document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
            clearTimeout: clearT, now: function () { return clock; }, storage: storage },
    });
    return { ctl: ctl, root: rootEl, tabBtn: tabBtn, badge: badge };
}
var API = "/api/v1/alarm/investigations";
function running(id) {
    return { status: "running", investigation_id: id, kind: "alarm",
        activity: { next_seq: 0, truncated: false, events: [] }, phase: "collecting" };
}
"""


def _node(body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    script = "\n".join(
        [
            f"var BV = require({json.dumps(str(_BV_JS))});",
            "globalThis.BriefingView = BV;",
            f"var DV = require({json.dumps(str(_DV_JS))});",
            _HARNESS,
            "var out = null;",
            "(async function () {",
            body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error(e && e.stack || e); process.exit(1); });",
        ]
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ─── 1. 사전 게이트 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [404, 401, 403])
def test_gate_off_hides_tab_and_opens_no_stream(status: int) -> None:
    out = _node(
        f"""
routes[API + "/active"] = {{ status: {status}, body: {{}} }};
var v = make(false);
var on = await v.ctl.probe();
runTimers();
await flush();
out = {{ on: on, display: v.tabBtn.style.display, es: esList.length, open: v.ctl.open("inv-1"),
        enabled: v.ctl.enabled(), calls: calls.length }};
"""
    )
    assert out == {
        "on": False, "display": "none", "es": 0, "open": False, "enabled": False, "calls": 1,
    }


def test_gate_on_shows_tab_list_badge_and_one_stream() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [
    { investigation_id: "inv-a", kind: "alarm", origin: null, status: "running", phase: "queued",
      queue_position: 2, elapsed_s: 41, subject: "A1" },
    { investigation_id: "inv-b", kind: "diagnosis", origin: "chat", status: "running",
        phase: "tools",
      elapsed_s: 130, subject: "web01" } ], poll_seconds: 5 } };
var v = make(false);
var on = await v.ctl.probe();
runTimers(1000);
out = { on: on, display: v.tabBtn.style.display, badge: v.badge.textContent,
        badgeOn: v.badge.classList.contains("has-unread"),
            es: esList.map(function (e) { return e.url; }),
        rows: texts(v.root, "dv-list-row"), conn: texts(v.root, "dv-conn")[0] };
"""
    )
    assert out["on"] is True and out["display"] == ""
    assert out["badge"] == "2" and out["badgeOn"] is True
    assert out["es"] == ["/api/v1/alarm/investigations/stream"]
    # (plans/194 W5 · U3) 대기 중 칩 — service_load가 없으면 「 / 동시 조사」 부분은 뺀다
    assert "대기 중 · 순번 2" in out["rows"][0] and "동시 조사" not in out["rows"][0]
    assert "자동" in out["rows"][0]
    assert "채팅" in out["rows"][1] and "2분 10초" in out["rows"][1]
    assert out["conn"] == "◌ 연결 중"


def test_list_row_click_opens_subtab_and_owner_only_for_operator() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [
    { investigation_id: "inv-a", kind: "alarm", status: "running", owner_sub: "kim",
        subject: "A1" } ] } };
routes[API + "/inv-a"] = { status: 200, body: running("inv-a") };
var admin = make(true);
await admin.ctl.probe();
var heads = texts(admin.root, "dv-list")[0];
findAll(admin.root, "dv-list-row")[0].click();
await flush();
out = { head: heads, tabs: admin.ctl.state().tabs, current: admin.ctl.state().current,
    activated: activated };
"""
    )
    assert "요청자" in out["head"] and "kim" in out["head"]
    assert out["tabs"] == ["inv-a"] and out["current"] == "inv-a" and out["activated"] == 1


# ─── 2. 하위 탭 · 연결 1개 ───────────────────────────────────────────────


def test_same_id_twice_is_one_tab_and_twenty_tabs_one_stream() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [] } };
routes[API + "/inv-0"] = { status: 200, body: running("inv-0") };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-0"); v.ctl.open("inv-0");
var afterTwice = v.ctl.state().tabs.length;
for (var i = 1; i < 20; i++) { routes[API + "/inv-" + i] = { status: 200,
    body: running("inv-" + i) }; v.ctl.open("inv-" + i); }
runTimers(1000);
await flush();
var st = v.ctl.state();
out = { twice: afterTwice, tabs: st.tabs.length, live: liveES().length, made: esList.length,
        watchItems: decodeURIComponent(liveES()[0].url.split("watch=")[1]).split(",").length,
        menu: texts(v.root, "dv-menu-btn")[0], subtabs: findAll(v.root, "dv-subtab").length };
"""
    )
    assert out["twice"] == 1
    assert out["tabs"] == 20 and out["subtabs"] == 20
    assert out["live"] == 1 and out["made"] == 2  # 첫 연결 + 탭 묶음 재연결 1번
    assert out["watchItems"] == 20
    assert out["menu"] == "▾ 열린 탭 20"


def test_session_storage_restore_and_close_finished() -> None:
    out = _node(
        """
store["diagnosis_open_tabs"] = JSON.stringify({ tabs: ["inv-x", "inv-y", "bad id!"],
    current: "inv-y" });
routes[API + "/active"] = { status: 200, body: { items: [] } };
routes[API + "/inv-x"] = { status: 200, body: { status: "done", investigation_id: "inv-x",
    briefing_lines: [] } };
routes[API + "/inv-y"] = { status: 200, body: running("inv-y") };
var v = make(false);
await v.ctl.probe();
await flush();
runTimers(1000);
var restored = v.ctl.state();
v.ctl.closeFinished();
runTimers(1000);
out = { restored: restored.tabs, current: restored.current, after: v.ctl.state().tabs,
        saved: JSON.parse(store["diagnosis_open_tabs"]).tabs, watch: v.ctl.state().watch,
        live: liveES().length };
"""
    )
    assert out["restored"] == ["inv-x", "inv-y"] and out["current"] == "inv-y"
    assert out["after"] == ["inv-y"] and out["saved"] == ["inv-y"]
    assert out["watch"].startswith("inv-y:") and out["live"] == 1


def test_storage_failure_starts_empty() -> None:
    out = _node(
        """
storage.getItem = function () { throw new Error("blocked"); };
storage.setItem = function () { throw new Error("blocked"); };
routes[API + "/active"] = { status: 200, body: { items: [] } };
var v = make(false);
var on = await v.ctl.probe();
out = { on: on, tabs: v.ctl.state().tabs };
"""
    )
    assert out == {"on": True, "tabs": []}


# ─── 3. 실행 기록 · AI 메모 ──────────────────────────────────────────────

_EVENTS = [
    {"seq": 1, "at": 1792395109.7, "engine": "system", "kind": "queued", "status": "waiting",
     "label": "동시 조사 상한 대기 — 대기 순번 1"},
    {"seq": 2, "at": 1792395150.1, "engine": "playbook", "kind": "tool_start", "step": "S2",
     "tool": "apm_resource_pool", "label": "apm_resource_pool {A1}"},
    {"seq": 3, "at": 1792395151.3, "engine": "playbook", "kind": "tool_end", "step": "S2",
     "tool": "apm_resource_pool", "label": "apm_resource_pool {A1}", "status": "success",
     "elapsed_ms": 1200, "rows": 3, "excerpt": "<img src=x onerror=alert(1)> pool=20"},
    {"seq": 4, "at": 1792395152.0, "engine": "holmes", "kind": "ai_note",
     "label": "HolmesGPT 2회차 메모",
     "note": "커넥션 대기 의심"},
    {"seq": 5, "at": 1792395153.0, "engine": "holmes", "kind": "tool_end",
     "tool": "polestar_metric_trend", "label": "polestar_metric_trend {...}", "status": "cached",
     "elapsed_ms": 38},
]


def _activity_script(operator: bool) -> str:
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-1"] = {{ status: 200, body: running("inv-1") }};
var v = make({str(operator).lower()});
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-1");
await flush();
runTimers(1000);
var es = liveES()[0];
es.onopen();
es.emit("activity", {{ investigation_id: "inv-1", status: "running", next_seq: 5, truncated: false,
    source: "live", events: {json.dumps(_EVENTS, ensure_ascii=False)}, phase: "tools" }});
es.emit("activity", {{ investigation_id: "inv-1", status: "running", next_seq: 5,
    events: {json.dumps(_EVENTS[2:3], ensure_ascii=False)} }});
var tab = v.ctl.tab("inv-1");
var rows = findAll(tab.dom.grid, "dv-row");
var rowHtml = rows.some(function (r) {{ return htmlWrites.indexOf(r) >= 0 || findAll(r,
    "dv-text").some(function (t) {{ return htmlWrites.indexOf(t) >= 0; }}); }});
var expand = findAll(tab.dom.grid, "dv-expand")[0];
var pre = findAll(tab.dom.grid, "dv-excerpt")[0];
var before = pre.hidden;
expand.click();
out = {{ rows: rows.map(function (r) {{ return r.textContent; }}), ai: findAll(tab.dom.grid,
    "dv-row--ai").length,
        badges: texts(tab.dom.grid, "dv-ai-badge"), seq: tab.seq, rowHtml: rowHtml,
        excerpt: pre.textContent, before: before, after: pre.hidden,
        filters: texts(tab.dom.panel, "dv-filters")[0], conn: texts(v.root, "dv-conn")[0],
        watch: v.ctl.state().watch, partial: tab.dom.partBody.textContent }};
"""


def test_user_screen_has_zero_ai_note_rows() -> None:
    out = _node(_activity_script(False))
    assert out["ai"] == 0 and out["badges"] == []
    assert not any("커넥션 대기 의심" in r for r in out["rows"])
    # 최신이 위 · 진행 행은 끝 행으로 바뀐다 · 같은 seq 재수신은 중복 0
    assert len(out["rows"]) == 3
    assert "↺" in out["rows"][0] and "38ms" in out["rows"][0]
    assert "✓" in out["rows"][1] and "1.2s" in out["rows"][1] and "3행" in out["rows"][1]
    assert "⟳" not in "".join(out["rows"])
    assert "⏸" in out["rows"][2]
    assert out["seq"] == 5
    # 서버 텍스트는 textContent로만 — 발췌의 태그가 원문 그대로 남는다
    assert out["rowHtml"] is False
    assert "<img src=x onerror=alert(1)>" in out["excerpt"]
    assert out["before"] is True and out["after"] is False
    # parallel(두 엔진)이면 엔진 필터 · 사용자 화면엔 AI 메모 필터 없음
    assert "플레이북" in out["filters"] and "HolmesGPT" in out["filters"]
    assert "AI 메모" not in out["filters"]
    assert out["conn"] == "● 실시간"


def test_operator_screen_ai_note_row_has_badge_and_filter() -> None:
    out = _node(_activity_script(True))
    assert out["ai"] == 1 and out["badges"] == ["AI 메모 — 검증 전"]
    assert any("커넥션 대기 의심" in r for r in out["rows"])
    assert "AI 메모" in out["filters"]


def test_holmes_only_middle_result_counts_tools() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [] } };
routes[API + "/inv-h"] = { status: 200, body: running("inv-h") };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-h");
await flush();
runTimers(1000);
liveES()[0].emit("activity", { investigation_id: "inv-h", status: "running", next_seq: 2, events: [
    { seq: 1, at: 1, engine: "holmes", kind: "tool_end", tool: "t1", label: "t1",
        status: "success" },
    { seq: 2, at: 2, engine: "holmes", kind: "tool_end", tool: "t2", label: "t2",
        status: "no_data" } ] });
out = v.ctl.tab("inv-h").dom.partBody.textContent;
"""
    )
    assert out == "도구 결과 2건 수집 — 결론은 종결 후"


def test_partial_briefing_uses_shared_detail_render() -> None:
    view = _running_view("running_collect")
    out = _node(
        f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-run"] = {{ status: 200, body: running("inv-run") }};
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-run");
await flush();
runTimers(1000);
liveES()[0].emit("partial", {{ investigation_id: "inv-run",
    partial_briefing: {json.dumps(view["partial_briefing"], ensure_ascii=False)} }});
var html = v.ctl.tab("inv-run").dom.partBody.innerHTML;
out = {{ html: html, expected: BV.renderBody({{ status: "running", investigation_id: "inv-run",
    partial_briefing: {json.dumps(view["partial_briefing"], ensure_ascii=False)},
        progress: null }}, {{ mode: "detail", runningText: DV.TEXT.partialRunning }}) }};
"""
    )
    assert out["html"] == out["expected"] and "bv--playbook" in out["html"]
    # 실시간 화면에 「15초마다 다시 확인」 안내가 새지 않는다(W15 교정)
    assert "15초마다" not in out["html"] and "실시간으로 더해집니다" in out["html"]


_OLD_NOTE = '<div class="bv-note">조사가 진행 중입니다 — 15초마다 다시 확인합니다.</div>'


@pytest.mark.parametrize("kind", ["playbook", "plain"])
def test_running_text_option_default_is_unchanged(kind: str) -> None:
    """`runningText` 미지정 = 종전 문구 · 문자열 = 그 줄만 대체 · 빈 문자열 = 그 줄만 생략."""
    view: dict[str, Any] = (
        _running_view("running_collect")
        if kind == "playbook"
        else {"status": "running", "investigation_id": "inv-p", "briefing_lines": []}
    )
    out = _node(
        f"""
var view = {json.dumps(view, ensure_ascii=False)};
out = {{ base: BV.renderBody(view, {{ mode: "detail" }}),
        swap: BV.renderBody(view, {{ mode: "detail", runningText: "실시간 <갱신>" }}),
        drop: BV.renderBody(view, {{ mode: "detail", runningText: "" }}),
        none: BV.renderBody(view) }};
"""
    )
    base = out["base"]
    assert base.count(_OLD_NOTE) == 1
    assert out["swap"] == base.replace(
        _OLD_NOTE, '<div class="bv-note">실시간 &lt;갱신&gt;</div>'
    )
    assert out["drop"] == base.replace(_OLD_NOTE, "")
    assert _OLD_NOTE in out["none"]


def test_list_engine_cell_prefers_server_engines_key() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [
    { investigation_id: "inv-a", kind: "alarm", status: "running", subject: "A1",
      engines: ["holmes", "playbook"], progress: { engine: "holmes" } },
    { investigation_id: "inv-b", kind: "alarm", status: "running", subject: "B1",
      engines: [], progress: { engine: "holmes" } },
    { investigation_id: "inv-c", kind: "alarm", status: "running", subject: "C1" } ] } };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
out = findAll(v.root, "dv-list-row").map(function (tr) { return tr.children[2].textContent; });
"""
    )
    assert out == ["플레이북 + HolmesGPT", "HolmesGPT", "—"]


# ─── 4. 연결 상태 · 강등 · 401 ───────────────────────────────────────────


def test_three_failures_degrade_to_polling_with_visible_text() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [] } };
routes[API + "/inv-1"] = { status: 200, body: running("inv-1") };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-1");
await flush();
runTimers(1000);
var es = liveES()[0];
es.onerror();
var reconn = texts(v.root, "dv-conn")[0];
es.onerror();
calls = [];
es.onerror();
await flush();
var st = v.ctl.state();
var pollTimers = timers.filter(function (t) { return t.ms === DV.FALLBACK_POLL_MS; }).length;
out = { reconn: reconn, conn: texts(v.root, "dv-conn")[0], polling: st.polling,
    live: liveES().length,
        calls: calls, pollTimers: pollTimers, made: esList.length };
"""
    )
    assert out["reconn"].startswith("◌ 재연결 중")
    assert out["conn"] == "⚠ 실시간 연결 끊김 — 15초 간격 조회 중"
    assert out["polling"] is True and out["live"] == 0
    assert "/api/v1/alarm/investigations/active" in out["calls"]
    assert "/api/v1/alarm/investigations/inv-1?activity_since=0" in out["calls"]
    assert out["pollTimers"] == 1
    assert out["made"] == 2  # 강등 뒤 새 EventSource를 만들지 않는다


def test_closed_stream_with_401_stops_without_reconnect_loop() -> None:
    out = _node(
        """
var n = 0;
routes[API + "/active"] = function () { n += 1; return n === 1 ? { status: 200,
    body: { items: [] } } : { status: 401, body: {} }; };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
var es = liveES()[0];
es.readyState = 2;
es.onerror();
await flush();
runTimers();
await flush();
out = { conn: texts(v.root, "dv-conn")[0], stopped: v.ctl.state().stopped, made: esList.length,
        timers: timers.filter(function (t) { return t.ms !== 5000; }).length };
"""
    )
    assert out["conn"] == "⚠ 끊김 — 인증이 만료되었습니다 — 다시 로그인하십시오"
    assert out["stopped"] is True and out["made"] == 1 and out["timers"] == 0


def test_conn_text_idle_and_states() -> None:
    out = _node(
        """
out = [DV.connText({ state: "live", lastRecv: 0 }, 10),
       DV.connText({ state: "live", lastRecv: 1000 }, 1000 + 59000),
       DV.connText({ state: "live", lastRecv: 1000 }, 1000 + 75000),
       DV.connText({ state: "reconnecting", since: 1000 }, 7000),
       DV.connText({ state: "stopped", reason: "기능 꺼짐" }, 0)];
"""
    )
    assert out == [
        "● 실시간",
        "● 실시간",
        "● 실시간 · 마지막 수신 75초 전",
        "◌ 재연결 중 6초",
        "⚠ 끊김 — 기능 꺼짐",
    ]


def test_long_running_has_no_time_limit() -> None:
    """300초를 넘어도 연결·탭이 멈추지 않는다 — 종결(done)만이 watch에서 뺀다(G-39)."""
    src = _DV_JS.read_text(encoding="utf-8")
    assert "limitSeconds" not in src and "300000" not in src
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [] } };
routes[API + "/inv-1"] = { status: 200, body: running("inv-1") };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-1");
await flush();
runTimers(1000);
clock += 900000;
runTimers();
var es = liveES()[0];
es.emit("ping", {});
out = { live: liveES().length, watch: v.ctl.state().watch, conn: texts(v.root, "dv-conn")[0] };
"""
    )
    assert out == {"live": 1, "watch": "inv-1:0", "conn": "● 실시간"}


# ─── 5. 종결 · 거부 ─────────────────────────────────────────────────────


def test_done_removes_from_watch_and_renders_final_with_conversation_button() -> None:
    final = _done_view("latency_sql")
    final["investigation_id"] = "inv-1"
    out = _node(
        f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
var finalView = {json.dumps(final, ensure_ascii=False)};
var first = true;
routes[API + "/inv-1"] = function () {{ if (first) {{ first = false; return {{ status: 200,
    body: running("inv-1") }}; }}
    return {{ status: 200, body: finalView }}; }};
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-1");
await flush();
runTimers(1000);
liveES()[0].emit("done", {{ investigation_id: "inv-1", status: "done", next_seq: 7 }});
await flush();
runTimers(1000);
var tab = v.ctl.tab("inv-1");
var conv = findAll(tab.dom.panel, "dv-open-conv")[0];
conv.click();
out = {{ done: tab.done, finalHidden: tab.dom.finalBox.hidden, partHidden: tab.dom.partBox.hidden,
        final: tab.dom.finalBody.innerHTML.indexOf("bv--playbook") >= 0, opened: opened,
        watch: v.ctl.state().watch, live: liveES().length, last: liveES()[0].url,
        lastCall: calls[calls.length - 1], state: tab.dom.state.textContent }};
"""
    )
    assert out["done"] is True and out["finalHidden"] is False and out["partHidden"] is True
    assert out["final"] is True and out["opened"] == ["inv-1"]
    assert out["watch"] == "" and out["live"] == 1
    assert "watch=" not in out["last"]
    assert out["lastCall"] == "/api/v1/alarm/investigations/inv-1?activity_since=0"
    assert "✓" in out["state"]


@pytest.mark.parametrize(
    ("reason", "text"),
    [
        ("forbidden", "이 조사를 볼 권한이 없습니다."),
        ("unavailable", "열 수 없는 조사 — 없거나 권한이 없습니다."),
    ],
)
def test_denied_shows_reason_and_leaves_watch(reason: str, text: str) -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [] } };
routes[API + "/inv-z"] = { status: 403, body: { detail: "존 밖" } };
var v = make(false);
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-z");
await flush();
runTimers(1000);
liveES()[0].emit("denied", { investigation_id: "inv-z", reason: "__REASON__" });
runTimers(1000);
var tab = v.ctl.tab("inv-z");
out = { warn: tab.dom.warn.textContent, hidden: tab.dom.warn.hidden, watch: v.ctl.state().watch };
""".replace("__REASON__", reason)
    )
    assert out == {"warn": text, "hidden": False, "watch": ""}


@pytest.mark.parametrize(
    ("phase", "label"),
    [
        ("collecting", "수집"),
        ("narrating", "서술"),
        ("deep_dive", "심층 조사"),
        ("finalizing", "브리핑 조립"),
        ("someday_new", "진행 중"),
    ],
)
def test_phase_labels_follow_sre_agent_vocabulary(phase: str, label: str) -> None:
    out = _node(f"out = DV.statusText({{ phase: {json.dumps(phase)}, status: 'running' }});")
    assert out == "⟳ 진행 · " + label


@pytest.mark.parametrize(
    ("status", "icon"),
    [
        ("success", "✓"),
        ("ok", "✓"),
        ("cached", "↺"),
        ("no_data", "⚠"),
        ("approval_required", "⚠"),
        ("error", "⚠"),
    ],
)
def test_holmes_non_success_tool_status_is_warn(status: str, icon: str) -> None:
    out = _node(
        "out = DV.eventRow({ seq: 1, at: 1, engine: 'holmes', kind: 'tool_end', tool: 't', "
        f"status: {json.dumps(status)} }}).icon;"
    )
    assert out == icon


def test_pure_helpers() -> None:
    out = _node(
        """
out = { watch: DV.watchParam([{ id: "a", seq: 3 }, { id: "b", seq: 0, done: true }, { id: "c",
    seq: 1, denied: "x" }]),
        queued: DV.statusText({ phase: "queued", queue_position: 1 }),
        elapsed: [DV.fmtElapsed(5), DV.fmtElapsed(134), DV.fmtElapsed(3700)] };
var st = { rows: [], seen: {}, clipped: false, tools: 0 };
var evs = [];
for (var i = 1; i <= 320; i++) evs.push({ seq: i, at: i, engine: "playbook", kind: "tool_end",
    tool: "t", step: "S" + i, label: "x" });
DV.mergeEvents(st, evs, false, DV.ROW_CAP);
out.cap = st.rows.length; out.clipped = st.clipped; out.top = st.rows[0].seq;
"""
    )
    assert out["watch"] == "a:3"
    assert out["queued"] == "⏸ 대기 순번 1"
    assert out["elapsed"] == ["5초", "2분 14초", "1시간 1분"]
    assert out["cap"] == 300 and out["clipped"] is True and out["top"] == 320


# ─── 6. 정적 — 마크업 · 배선 · 보안 ──────────────────────────────────────


def test_tab_markup_hidden_by_default_and_script_order() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    tab = html[html.index('data-view="diagnosis"') - 60: html.index('id="diagnosisTabBadge"')]
    assert 'style="display: none;"' in tab and 'aria-controls="diagnosisView"' in tab
    assert 'id="diagnosisView"' in html and 'class="diagnosis-view view-hidden"' in html
    bv = html.index("briefing-view.js?v=9")
    dv = html.index("diagnosis-view.js?v=13")
    app = html.index("app.js?v=43")
    assert bv < dv < app
    css = (_STATIC / "css" / "style.css").read_text(encoding="utf-8")
    gate = css[css.index("body.auth-pending > header"):]
    assert ".diagnosis-view" in gate[: gate.index("}")]


def test_view_registry_and_gate_before_query_entry() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    assert "diagnosis: diagnosisView" in _js_function_body(src, "viewRegistry")
    auth = _js_function_body(src, "checkAuthOnLoad")
    assert auth.index("initAlarmSubscription(") < auth.index("initDiagnosisView().then(")
    query = _js_function_body(src, "openInvestigationFromQuery")
    assert 'params.get("diagnosis")' in query
    assert query.index("DiagnosisView.openIfEnabled(id)") < query.index(
        "openInvestigationConversation(id)"
    )
    init = _js_function_body(src, "initDiagnosisView")
    assert "operator: alarmOperator" in init
    assert "onOpenConversation: openInvestigationConversation" in init


def test_entry_wiring_g34() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    bind = _js_function_body(src, "bindAlarmBriefing")
    assert "entryRender(" in bind and "bindDiagnosisButton(section, view, data.alarm_id)" in bind
    assert "followToDiagnosis(res, alarmId)" in _js_function_body(src, "analyzeOptions")
    chat = _js_function_body(src, "appendChatInvestigationCards")
    assert "openInvestigationEntry(id)" in chat
    past = _js_function_body(src, "buildPastAlarmCard")
    assert "openInvestigationEntry(shownId, item.alarm_id)" in past and "entryRender(" in past
    entry = _js_function_body(src, "openInvestigationEntry")
    assert entry.index("openIfEnabled") < entry.index(
        "openInvestigationConversation(investigationId)"
    )
    noise = _NOISE_JS.read_text(encoding="utf-8")
    # (W23) 항상 새 탭 — 켜짐이면 `/?diagnosis=` · 꺼짐이면 `/?conversation=`
    entry = _js_function_body(noise, "openInPortal")
    assert '"diagnosis" : "conversation"' in entry and '"_blank", "noopener"' in entry
    assert "portalEntryButton(view)" in _js_function_body(noise, "onInvestigationView")
    assert noise.count("onView: onInvestigationView") == 2


def test_entry_render_is_identical_when_off_and_hides_button_when_on() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    funcs = _js_function_body(src, "diagnosisOn") + "\n" + _js_function_body(src, "entryRender")
    view = _done_view("normal")
    out = _node(
        f"""
var window = {{ DiagnosisView: {{ isEnabled: function () {{ return flag; }} }} }};
var flag = false;
{funcs}
var view = {json.dumps(view, ensure_ascii=False)};
var plain = {{ mode: "summary", openConversation: true, busy: false }};
var off = BV.renderBody(view, entryRender(plain));
var same = off === BV.renderBody(view, plain);
var keys = Object.keys(entryRender(plain)).sort().join(",");
flag = true;
var on = BV.renderBody(view, entryRender(plain));
out = {{ same: same, keys: keys, offBtn: off.indexOf('data-bv-action="open-conversation"') >= 0,
        onBtn: on.indexOf('data-bv-action="open-conversation"') >= 0 }};
"""
    )
    assert out == {
        "same": True, "keys": "busy,mode,openConversation", "offBtn": True, "onBtn": False,
    }


def test_server_text_never_goes_through_inner_html_and_no_write_buttons() -> None:
    src = _DV_JS.read_text(encoding="utf-8")
    assigns = re.findall(r"\.innerHTML\s*=\s*([^;]+);", src)
    assert len(assigns) == 1 and assigns[0].startswith("bv.renderBody(")
    assert "insertAdjacentHTML" not in src and "outerHTML" not in src and "eval(" not in src
    # D-003 — 조사 취소·명령 실행 경로 없음(읽기 GET만)
    assert "POST" not in src and "DELETE" not in src and "method:" not in src
    assert "new ES(" in src and src.count("new ES(") == 1
