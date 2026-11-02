"""plans/201 W5 §7.3 · §7.4 — 알람 카드 [인계 요약 복사] · 카드 재배치(인지 → 분석 → 조치) 실행 검증.

app.js 전체를 node에서 가짜 DOM으로 띄우고(innerHTML 파서 포함) **실제 `renderAlarmMessage`** 로 카드를 그린다.
닫는 IIFE 앞에 시험 출구(`__APP`) 한 줄만 끼운다 — 제품 코드는 바꾸지 않는다. 공용 렌더(briefing-view.js)·
진단 렌더(diagnosis-view.js)는 실물을 require한다. node는 stdin으로 실행한다(`node -e` 금지 — WinError 206).

플래그 off(둘 다 꺼짐 · capabilities에 `card_flow_enabled` 없음 · `/auth/me`에 `duty_action_assist` 없음)면
카드 DOM이 종전 마크업 그대로인지, on이면 칸 집합이 같고 묶음·진행 표시만 더해지는지 본다. 보존 동작 4종
(Plan 86 일상 알람 접힘 · plans/153 W3 펼칠 때만 조회 · D-347 「조사 안 됨」+[조사 시작] · Plan 83 T7 피드백 off
미렌더)은 off·on 양쪽에서 단언한다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "src" / "static" / "js"

#: 최소 가짜 DOM — innerHTML 파서(카드 마크업용) · 단순 선택자 · 이벤트 · 형태 비교(shape)
_FAKE_DOM = r"""
// 최소 가짜 DOM — innerHTML 파서(카드 마크업용) · 단순 선택자(.cls · tag · [attr] · [attr="v"] · 자손 결합) · 이벤트
var VOID = { input: 1, br: 1, img: 1, hr: 1, meta: 1, link: 1, path: 1, circle: 1, rect: 1, line: 1, polyline: 1 };
function El(tag) {
    this.tagName = String(tag).toUpperCase(); this.nodeType = 1; this.children = []; this.childNodes = [];
    this.attrs = {}; this.parentNode = null; this._listeners = {}; this.style = {}; this.dataset = {}; this.value = "";
    var self = this;
    this.classList = {
        contains: function (c) { return self._cls().indexOf(c) >= 0; },
        add: function (c) { var l = self._cls(); if (l.indexOf(c) < 0) l.push(c); self.className = l.join(" "); },
        remove: function (c) { self.className = self._cls().filter(function (x) { return x !== c; }).join(" "); },
        toggle: function (c, on) {
            var has = self.classList.contains(c); if (on === undefined) on = !has;
            if (on) self.classList.add(c); else self.classList.remove(c); return on;
        },
    };
}
function Txt(t) { this.nodeType = 3; this.data = String(t); this.parentNode = null; }
Object.defineProperty(Txt.prototype, "textContent", { get: function () { return this.data; } });
El.prototype._cls = function () { return (this.className || "").split(/\s+/).filter(Boolean); };
Object.defineProperty(El.prototype, "className", {
    get: function () { return this.attrs["class"] || ""; },
    set: function (v) { this.attrs["class"] = String(v); },
});
Object.defineProperty(El.prototype, "id", {
    get: function () { return this.attrs.id || ""; }, set: function (v) { this.attrs.id = String(v); },
});
Object.defineProperty(El.prototype, "hidden", {
    get: function () { return Object.prototype.hasOwnProperty.call(this.attrs, "hidden"); },
    set: function (v) { if (v) this.attrs.hidden = ""; else delete this.attrs.hidden; },
});
Object.defineProperty(El.prototype, "textContent", {
    get: function () { return this.childNodes.map(function (c) { return c.textContent; }).join(""); },
    set: function (v) { this._clear(); if (String(v)) this._push(new Txt(v)); },
});
Object.defineProperty(El.prototype, "firstChild", { get: function () { return this.childNodes[0] || null; } });
Object.defineProperty(El.prototype, "nextSibling", {
    get: function () {
        if (!this.parentNode) return null;
        var s = this.parentNode.childNodes; return s[s.indexOf(this) + 1] || null;
    },
});
El.prototype._clear = function () { this.childNodes.forEach(function (c) { c.parentNode = null; }); this.childNodes = []; this.children = []; };
El.prototype._sync = function () { this.children = this.childNodes.filter(function (c) { return c.nodeType === 1; }); };
El.prototype._push = function (c) { this.childNodes.push(c); c.parentNode = this; this._sync(); };
El.prototype.appendChild = function (c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    this._push(c); return c;
};
El.prototype.removeChild = function (c) {
    var i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; this._sync(); return c;
};
El.prototype.insertBefore = function (c, ref) {
    if (!ref) return this.appendChild(c);
    if (c.parentNode) c.parentNode.removeChild(c);
    var i = this.childNodes.indexOf(ref); this.childNodes.splice(i, 0, c); c.parentNode = this; this._sync(); return c;
};
El.prototype.remove = function () { if (this.parentNode) this.parentNode.removeChild(this); };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); if (k.indexOf("data-") === 0) this.dataset[k.slice(5).replace(/-(\w)/g, function (m, x) { return x.toUpperCase(); })] = String(v); };
El.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
El.prototype.hasAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k); };
El.prototype.removeAttribute = function (k) { delete this.attrs[k]; };
El.prototype.addEventListener = function (t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); };
El.prototype.removeEventListener = function () {};
El.prototype.dispatch = function (t) { var self = this; (this._listeners[t] || []).slice().forEach(function (f) { f({ target: self, preventDefault: function () {}, stopPropagation: function () {} }); }); };
El.prototype.click = function () { this.dispatch("click"); };
El.prototype.focus = function () {};
El.prototype.closest = function (sel) { var n = this; while (n && n.nodeType === 1) { if (matches(n, sel)) return n; n = n.parentNode; } return null; };
El.prototype.all = function () {
    var o = []; this.children.forEach(function (c) { o.push(c); o = o.concat(c.all()); }); return o;
};
function matchSimple(e, s) {
    var re = /([.#]?[\w-]+)|\[([\w-]+)(?:="([^"]*)")?\]/g, m, any = false;
    while ((m = re.exec(s))) {
        any = true;
        if (m[1]) {
            if (m[1][0] === ".") { if (!e.classList.contains(m[1].slice(1))) return false; }
            else if (m[1][0] === "#") { if (e.id !== m[1].slice(1)) return false; }
            else if (e.tagName !== m[1].toUpperCase()) return false;
        } else {
            var v = e.getAttribute(m[2]); if (v === null || (m[3] !== undefined && v !== m[3])) return false;
        }
    }
    return any;
}
function matches(e, sel) {
    return sel.split(",").some(function (one) {
        var parts = one.trim().split(/\s+/);
        if (!matchSimple(e, parts[parts.length - 1])) return false;
        var n = e.parentNode;
        for (var i = parts.length - 2; i >= 0; i--) {
            while (n && n.nodeType === 1 && !matchSimple(n, parts[i])) n = n.parentNode;
            if (!n || n.nodeType !== 1) return false;
            n = n.parentNode;
        }
        return true;
    });
}
El.prototype.querySelectorAll = function (sel) { return this.all().filter(function (e) { return matches(e, sel); }); };
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
function decode(t) { return t.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&"); }
Object.defineProperty(El.prototype, "innerHTML", {
    get: function () {
        function esc(t) { return String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
        function ser(n) {
            if (n.nodeType === 3) return esc(n.data);
            var a = Object.keys(n.attrs).map(function (k) { return " " + k + '="' + esc(n.attrs[k]).replace(/"/g, "&quot;") + '"'; }).join("");
            var t = n.tagName.toLowerCase();
            return "<" + t + a + ">" + (VOID[t] ? "" : n.childNodes.map(ser).join("") + "</" + t + ">");
        }
        return this.childNodes.map(ser).join("");
    },
    set: function (html) {
        this._clear();
        var stack = [this], re = /<\/?([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:="[^"]*"|='[^']*')?)*)\s*(\/?)>|([^<]+)/g, m;
        while ((m = re.exec(html))) {
            var top = stack[stack.length - 1];
            if (m[4] !== undefined) { top._push(new Txt(decode(m[4]))); continue; }
            if (m[0][1] === "/") { if (stack.length > 1) stack.pop(); continue; }
            var e = fakeDoc.createElement(m[1].toLowerCase());
            var ar = /([\w:-]+)(?:="([^"]*)"|='([^']*)')?/g, a;
            while ((a = ar.exec(m[2] || ""))) e.setAttribute(a[1], decode(a[2] !== undefined ? a[2] : (a[3] !== undefined ? a[3] : "")));
            top._push(e);
            if (!m[3] && !VOID[m[1].toLowerCase()]) stack.push(e);
        }
    },
});
El.prototype.shape = function () {
    // 비교용 구조 — 태그·속성·텍스트(이벤트 제외)
    return [this.tagName, this.attrs, this.childNodes.map(function (c) { return c.nodeType === 3 ? c.data : c.shape(); })];
};
var fakeDoc = {
    createElement: function (t) { var e = new El(t); e.ownerDocument = fakeDoc; return e; },
    createTextNode: function (t) { return new Txt(t); },
    getElementById: function () { return null; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    addEventListener: function () {},
    body: null,
    documentElement: null,
    readyState: "loading",
    visibilityState: "visible",
    hidden: false,
};
fakeDoc.body = fakeDoc.createElement("body");
fakeDoc.documentElement = fakeDoc.createElement("html");
"""

#: app.js 기동 환경 — 브라우저 전역 최소 대역. 조회는 기록만 하고 기본은 응답하지 않는다(대기 Promise).
_LOADER = r"""
var fs = require("fs");
var ids = {};
fakeDoc.getElementById = function (id) {
    if (!ids[id]) { ids[id] = fakeDoc.createElement("div"); ids[id].id = id; }
    return ids[id];
};
var store = {};
global.localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; },
};
global.sessionStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
var fetches = [];
var fetchImpl = null;
global.fetch = function (u, o) {
    fetches.push(String(u));
    return fetchImpl ? fetchImpl(String(u), o) : new Promise(function () {});
};
global.document = fakeDoc;
global.window = global;
global.navigator = {};
global.location = { href: "http://x/", origin: "http://x", pathname: "/", search: "", hash: "", protocol: "http:" };
global.history = { replaceState: function () {}, pushState: function () {} };
global.setInterval = function () { return 0; };
global.matchMedia = function () { return { matches: false, addEventListener: function () {} }; };
global.addEventListener = function () {};
global.requestAnimationFrame = function () {};
var _log = console.log; console.log = function () {}; console.warn = function () {};
global.BriefingView = require(BV_PATH);
global.DiagnosisView = require(DV_PATH);
var src = fs.readFileSync(APP_PATH, "utf8");
var end = src.lastIndexOf("})();");
src = src.slice(0, end) + "global.__APP = { render: renderAlarmMessage, " +
    "caps: function (c) { alarmCapabilities = c; }, " +
    "assist: function (v) { dutyActionAssist = v; }, " +
    "arrange: typeof arrangeAlarmCardFlow === 'function' ? arrangeAlarmCardFlow : null };\n" + src.slice(end);
(0, eval)(src);
console.log = _log;
fetches.length = 0;
var APP = global.__APP;
function card(data) {
    var list = fakeDoc.getElementById("alarmList");
    APP.render(data);
    return list.children[0];
}
function bubbleOf(el) { return el.querySelector(".message-bubble"); }
function sectionNames(el) {
    // 카드 안 모든 칸(.alarm-section)의 첫 글귀 · 진행 표시/묶음 제목은 칸이 아니다
    return el.querySelectorAll(".alarm-section").map(function (s) {
        return (s.className + "|" + s.textContent).slice(0, 80);
    });
}
function flush() { return new Promise(function (r) { setTimeout(r, 0); }); }
"""

_ALARM: dict[str, Any] = {
    "alarm_id": "al-1",
    "incident_id": "inc-1",
    "investigation_id": "inv-1",
    "hostname": "host-a",
    "server_name": "서버 A",
    "alarm_name": "CPU 사용률",
    "resource_name": "cpu0",
    "resource_type": "CPU",
    "severity_label": "Critical",
    "alarm_time": "2026-10-26 19:10:00",
    "summary": "CPU 사용률 급증",
    "probable_cause": "배치 작업",
    "recommended_action": "프로세스 확인",
    "pattern_type": "급증",
    "pattern_analysis": "최근 1시간 3회",
    "is_routine": False,
    "episode_lines": ["사건 묶음 1줄"],
    "process_snapshot": {"alarm_kind": "cpu", "top": [{"name": "java", "pid": 10, "p100cpu": 90, "pmem": 10,
                                                      "user": "app"}]},
}

_CAPS_BASE: dict[str, Any] = {
    "investigation_enabled": True,
    "feedback_enabled": True,
    "prompt_suggest_enabled": False,
}

_ASSIST = {
    "requery_axes": {"apm": {"area": "was_mw", "label": "WAS/미들웨어"}},
    "checklists": {},
}


def _js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _node(body: str, *, app: Path | None = None, bv: Path | None = None, dv: Path | None = None) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    script = "\n".join([
        f"var APP_PATH = {_js(str(app or _JS / 'app.js'))};",
        f"var BV_PATH = {_js(str(bv or _JS / 'briefing-view.js'))};",
        f"var DV_PATH = {_js(str(dv or _JS / 'diagnosis-view.js'))};",
        _FAKE_DOM,
        _LOADER,
        "var out = null;",
        "(async function () {",
        body,
        "process.stdout.write(JSON.stringify(out));",
        "})().catch(function (e) { process.stderr.write(String(e && e.stack || e)); process.exit(1); });",
    ])
    done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=120,
                          check=False, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _modes() -> list[tuple[str, dict[str, Any]]]:
    return [("off", dict(_CAPS_BASE)), ("on", {**_CAPS_BASE, "card_flow_enabled": True})]


# ── off = 현행 DOM ───────────────────────────────────────────────────────────────


def test_off_card_has_no_w5_nodes() -> None:
    out = _node(f"""
APP.caps({_js(_CAPS_BASE)});
var el = card({_js(_ALARM)});
out = {{
    w5: el.querySelectorAll(".alarm-flow-progress, .alarm-flow-group, .alarm-handoff-section").length,
    flowStage: el.dataset.flowStage === undefined,
    order: bubbleOf(el).children.filter(function (c) {{ return c.classList.contains("alarm-section"); }})
        .map(function (c) {{ return c.textContent.slice(0, 6); }}),
    store: localStorage.getItem("alarm_card_flow_progress"),
}};
""")
    assert out["w5"] == 0 and out["flowStage"] is True and out["store"] is None
    assert [s[:2] for s in out["order"]] == ["요약", "추정", "권고", "영향", "패턴", "사건", "조사", "확인", "이 ", "이 "]


def test_assist_only_adds_handoff_section_after_ack() -> None:
    """조치 지원만 켜짐(재배치 off) — 인계 칸 1개가 확인 뒤 · 추천 질의 앞에 더해지고 나머지는 그대로."""
    out = _node(f"""
APP.caps({_js(_CAPS_BASE)});
var off = card({_js(_ALARM)});
APP.assist({_js(_ASSIST)});
var on = card({_js(_ALARM)});
function cls(el) {{ return bubbleOf(el).children.map(function (c) {{ return c.className; }}); }}
out = {{ off: cls(off), on: cls(on),
    progress: on.querySelectorAll(".alarm-flow-progress").length,
    btn: on.querySelector(".btn-alarm-handoff").textContent,
    fallbackHidden: on.querySelector(".alarm-handoff-text").hidden }};
""")
    i = out["on"].index("alarm-section alarm-handoff-section")
    assert out["on"][:i] + out["on"][i + 1:] == out["off"]
    assert "alarm-ack-section" in out["on"][i - 1]
    assert "alarm-prompt-section" in out["on"][i + 1]
    assert out["progress"] == 0 and out["btn"] == "인계 요약 복사" and out["fallbackHidden"] is True


# ── on = 칸 집합 동일 · 묶음 순서 ─────────────────────────────────────────────────


def test_flow_on_same_section_set_grouped_in_order() -> None:
    out = _node(f"""
function snap(caps, assist) {{
    APP.caps(caps); APP.assist(assist);
    var el = card({_js(_ALARM)});
    var b = bubbleOf(el);
    return {{
        sections: el.querySelectorAll(".alarm-section").filter(function (s) {{
            return !s.classList.contains("alarm-flow-caption");
        }}).map(function (s) {{ return s.className + "|" + s.textContent; }}).sort(),
        groups: b.querySelectorAll(".alarm-flow-group").map(function (g) {{
            return [g.className, g.children[0].textContent, g.children.slice(1).map(function (c) {{
                return c.textContent.slice(0, 4); }})];
        }}),
        progress: b.querySelectorAll(".alarm-flow-step").map(function (s) {{ return s.textContent; }}),
        directSections: b.children.filter(function (c) {{ return c.classList.contains("alarm-section"); }}).length,
    }};
}}
out = {{ off: snap({_js(_CAPS_BASE)}, null), on: snap({_js({**_CAPS_BASE, "card_flow_enabled": True})}, null),
        onAssist: snap({_js({**_CAPS_BASE, "card_flow_enabled": True})}, {_js(_ASSIST)}) }};
""")
    assert out["on"]["sections"] == out["off"]["sections"]          # 칸 집합 · 내용 불변
    assert out["off"]["groups"] == [] and out["on"]["directSections"] == 0
    groups = out["on"]["groups"]
    assert [g[1] for g in groups] == ["① 무슨 일인가", "② 원인이 내 쪽인가", "③ 무엇을 하나"]
    assert [c[:2] for c in groups[0][2]] == ["요약", "패턴", "사건"]
    assert [c[:2] for c in groups[1][2]] == ["조사", "영향", "추정"]
    assert [c[:2] for c in groups[2][2]] == ["권고", "이 ", "확인", "이 "]
    assert out["on"]["progress"] == ["● 인지", "○ 분석", "○ 조치"]
    # 조치 지원도 켜지면 인계 칸이 ③에 들어간다
    act = out["onAssist"]["groups"][2][2]
    assert [c[:2] for c in act] == ["권고", "이 ", "인계", "확인", "이 "]
    assert len(out["onAssist"]["sections"]) == len(out["off"]["sections"]) + 1


def test_flow_aborts_on_unexpected_markup_keeps_order() -> None:
    """칸 짝이 안 맞으면(예상 밖 마크업) 옮기지 않는다 — 종전 순서 그대로 · 칸을 잃지 않는다."""
    out = _node(f"""
APP.caps({_js(_CAPS_BASE)});
var el = card({_js(_ALARM)});
var b = bubbleOf(el);
var extra = fakeDoc.createElement("div");
extra.className = "alarm-section";
b.appendChild(extra);
var before = b.children.map(function (c) {{ return c.className; }});
APP.caps({_js({**_CAPS_BASE, "card_flow_enabled": True})});
APP.arrange(el, {_js(_ALARM)}, {{ process: true, pattern: true, episode: true, start: false, briefing: true,
    ack: true, prompt: true, feedback: true }});
out = {{ before: before, after: b.children.map(function (c) {{ return c.className; }}) }};
""")
    assert out["after"] == out["before"]


# ── 보존 동작 4종 (off · on 양쪽) ────────────────────────────────────────────────


@pytest.mark.parametrize("mode,caps", _modes())
def test_preserved_routine_prompt_collapsed(mode: str, caps: dict[str, Any]) -> None:
    out = _node(f"""
APP.caps({_js(caps)});
var r = card({_js({**_ALARM, "is_routine": True, "pattern_type": "반복"})});
var n = card({_js(_ALARM)});
function st(el) {{
    var s = el.querySelector(".alarm-prompt-section");
    return [s.classList.contains("alarm-prompt-section--collapsed"),
            s.querySelector(".alarm-prompt-toggle").getAttribute("aria-expanded"),
            s.querySelectorAll(".alarm-prompt-chip").length > 0];
}}
out = {{ routine: st(r), normal: st(n) }};
""")
    assert out["routine"] == [True, "false", True], mode
    assert out["normal"] == [False, "true", True], mode


@pytest.mark.parametrize("mode,caps", _modes())
def test_preserved_briefing_fetch_only_on_expand(mode: str, caps: dict[str, Any]) -> None:
    out = _node(f"""
APP.caps({_js(caps)});
APP.assist({_js(_ASSIST)});
var el = card({_js(_ALARM)});
var atRender = fetches.slice();
var sec = el.querySelector(".alarm-briefing-section");
var collapsed = sec.classList.contains("alarm-briefing-section--collapsed");
// [인계 요약 복사]는 조회하지 않는다(펼치기 전 — 기억된 뷰 없음)
var copied = null;
navigator.clipboard = {{ writeText: function (t) {{ copied = t; return Promise.resolve(); }} }};
el.querySelector(".btn-alarm-handoff").click();
await flush(); await flush();
var afterCopy = fetches.slice();
sec.querySelector(".alarm-briefing-toggle").click();
await flush();
out = {{ atRender: atRender, collapsed: collapsed, afterCopy: afterCopy, afterExpand: fetches.slice(),
        copied: copied, stage: el.dataset.flowStage || null,
        msg: el.querySelector(".alarm-handoff-msg").textContent }};
""")
    assert out["atRender"] == [], mode
    assert out["collapsed"] is True
    assert out["afterCopy"] == [], mode
    assert len(out["afterExpand"]) == 1 and "/investigations/" in out["afterExpand"][0]
    assert "조사 결과를 펼쳐 보지 않음" in out["copied"]
    assert out["msg"] == "복사했습니다 — 메신저·티켓에 붙여 넣으세요."
    if mode == "on":
        assert int(out["stage"]) == 1 | 2 | 4          # 인지 · 분석(펼침) · 조치(복사)
    else:
        assert out["stage"] is None


@pytest.mark.parametrize("mode,caps", _modes())
def test_preserved_manual_start_block(mode: str, caps: dict[str, Any]) -> None:
    manual = {**caps, "investigation_manual_enabled": True, "investigation_auto_enabled": False}
    data = {k: v for k, v in _ALARM.items() if k != "investigation_id"}
    out = _node(f"""
APP.caps({_js(manual)});
var el = card({_js(data)});
var sec = el.querySelector(".alarm-inv-section");
out = {{ text: sec ? sec.textContent : null, briefing: el.querySelectorAll(".alarm-briefing-section").length,
        fetches: fetches.slice(), inGroup: !!(sec && sec.closest(".alarm-flow-group--cause")) }};
""")
    assert out["text"] is not None and "조사 안 됨" in out["text"] and "조사 시작" in out["text"], mode
    assert out["briefing"] == 0 and out["fetches"] == []
    assert out["inGroup"] is (mode == "on")


@pytest.mark.parametrize("mode,caps", _modes())
def test_preserved_feedback_off_not_rendered(mode: str, caps: dict[str, Any]) -> None:
    out = _node(f"""
APP.caps({_js({**caps, "feedback_enabled": False})});
var el = card({_js(_ALARM)});
out = {{ fb: el.querySelectorAll(".alarm-feedback-section").length,
        act: el.querySelectorAll(".alarm-flow-group--act .alarm-section").length }};
""")
    assert out["fb"] == 0, mode
    assert out["act"] == (3 if mode == "on" else 0)


# ── 진행 표시 저장 · 상한 ─────────────────────────────────────────────────────────


def test_flow_progress_store_prune_and_ack() -> None:
    out = _node(f"""
var now = Date.now();
var seed = {{}};
for (var i = 0; i < 250; i++) seed["a:old" + i] = {{ s: 1, t: now - 1000 * i }};
seed["a:expired"] = {{ s: 7, t: now - 8 * 24 * 3600 * 1000 }};
seed["a:al-1"] = {{ s: 2, t: now - 5000 }};
localStorage.setItem("alarm_card_flow_progress", JSON.stringify(seed));
APP.caps({_js({**_CAPS_BASE, "card_flow_enabled": True})});
fetchImpl = function (u) {{
    return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve({{ acked: true }}); }} }});
}};
var el = card({_js(_ALARM)});
var afterRender = JSON.parse(localStorage.getItem("alarm_card_flow_progress"));
var steps0 = el.querySelectorAll(".alarm-flow-step").map(function (s) {{ return s.textContent; }});
el.querySelector(".btn-alarm-ack").click();
await flush(); await flush(); await flush();
var after = JSON.parse(localStorage.getItem("alarm_card_flow_progress"));
out = {{ n: Object.keys(afterRender).length, expired: "a:expired" in afterRender,
        mine: afterRender["a:al-1"].s, steps0: steps0,
        acked: after["a:al-1"].s,
        steps1: el.querySelectorAll(".alarm-flow-step").map(function (s) {{ return s.textContent; }}),
        keys: Object.keys(after).every(function (k) {{ return typeof after[k].s === "number" && typeof after[k].t === "number"; }}) }};
""")
    assert out["n"] == 200 and out["expired"] is False
    assert out["mine"] == 1 | 2                                        # 이 브라우저에 남은 「분석」도 보인다
    assert out["steps0"] == ["● 인지", "● 분석", "○ 조치"]
    assert out["acked"] == 7 and out["steps1"] == ["● 인지", "● 분석", "● 조치"]
    assert out["keys"] is True


def test_flow_progress_survives_broken_storage() -> None:
    out = _node(f"""
localStorage.setItem("alarm_card_flow_progress", "{{broken");
global.localStorage.setItem = function () {{ throw new Error("quota"); }};
APP.caps({_js({**_CAPS_BASE, "card_flow_enabled": True})});
var el = card({_js(_ALARM)});
out = el.querySelectorAll(".alarm-flow-step").map(function (s) {{ return s.textContent; }});
""")
    assert out == ["● 인지", "○ 분석", "○ 조치"]


def test_prompt_chip_marks_act() -> None:
    out = _node(f"""
APP.caps({_js({**_CAPS_BASE, "card_flow_enabled": True})});
var el = card({_js(_ALARM)});
el.querySelector(".alarm-prompt-chip").click();
out = {{ stage: Number(el.dataset.flowStage), fetches: fetches.slice() }};
""")
    assert out["stage"] == 1 | 4 and out["fetches"] == []


# ── 인계 복사 실패 사유 ───────────────────────────────────────────────────────────


def test_handoff_clipboard_failure_shows_reason_and_fallback() -> None:
    out = _node(f"""
APP.caps({_js(_CAPS_BASE)});
APP.assist({_js(_ASSIST)});
var el = card({_js(_ALARM)});
navigator.clipboard = {{ writeText: function () {{ var e = new Error("Document is not focused."); e.name = "NotAllowedError"; return Promise.reject(e); }} }};
el.querySelector(".btn-alarm-handoff").click();
await flush(); await flush();
var denied = [el.querySelector(".alarm-handoff-msg").textContent, el.querySelector(".alarm-handoff-text").hidden,
              el.querySelector(".alarm-handoff-text").value];
navigator.clipboard = undefined;
el.querySelector(".btn-alarm-handoff").click();
await flush(); await flush();
out = {{ denied: denied, unsupported: el.querySelector(".alarm-handoff-msg").textContent, fetches: fetches.slice() }};
""")
    msg, hidden, text = out["denied"]
    assert "Document is not focused." in msg and msg.startswith("복사하지 못했습니다")
    assert hidden is False and text.startswith("[알람 인계] 서버 A · CPU 사용률(cpu0) · Critical")
    assert "클립보드를 쓸 수 없습니다" in out["unsupported"]
    assert out["fetches"] == []


def test_handoff_uses_remembered_view_after_expand() -> None:
    """펼쳐서 받은 뷰를 기억했다가 복사 때 쓴다 — 복사 자체는 조회하지 않는다."""
    import copy as _copy

    fix = json.loads((_ROOT / "tests/fixtures/plan199/confirmed_same_layer.json").read_text("utf-8"))["view"]
    view = _copy.deepcopy(fix)
    view["briefing"]["duty_view"] = {"areas": [
        {"code": "was_mw", "label": "WAS/미들웨어", "status": "suspect", "status_label": "의심 · 확인 필요",
         "signals": ["풀 고갈"], "steps": ["S3"]}]}
    out = _node(f"""
APP.caps({_js(_CAPS_BASE)});
APP.assist({_js(_ASSIST)});
fetchImpl = function () {{
    return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve({_js(view)}); }} }});
}};
var el = card({_js(_ALARM)});
el.querySelector(".alarm-briefing-toggle").click();
for (var i = 0; i < 6; i++) await flush();
var n = fetches.length;
var copied = null;
navigator.clipboard = {{ writeText: function (t) {{ copied = t; return Promise.resolve(); }} }};
el.querySelector(".btn-alarm-handoff").click();
await flush(); await flush();
out = {{ before: n, after: fetches.length, copied: copied }};
""")
    assert out["before"] == out["after"]
    assert "영역별 점검: WAS/미들웨어 의심 · 확인 필요" in out["copied"]
    assert "근거 단계: S3" in out["copied"]


# ── 정적 계약 ────────────────────────────────────────────────────────────────


def _app_block() -> str:
    src = (_JS / "app.js").read_text(encoding="utf-8")
    i = src.index("// ─── plans/201 W5 §7.3 · §7.4")
    return src[i:src.index("// Plan 86: 추천 질의 칩을 DOM으로 만들고", i)]


def test_w5_card_block_text_content_only() -> None:
    block = _app_block()
    for bad in ("innerHTML", "insertAdjacentHTML", "outerHTML", "fetch(", "sendBeacon"):
        assert bad not in block, bad
    assert block.count("textContent") >= 5


def test_flags_gate_entries_in_render() -> None:
    src = (_JS / "app.js").read_text(encoding="utf-8")
    i = src.index("function renderAlarmMessage(data) {")
    render = src[i:src.index("// 알람은 질의응답 스트림이 아니라", i)]
    assert "if (dutyActionAssist) addAlarmHandoffSection(el, data);" in render
    assert "if (cardFlowOn()) arrangeAlarmCardFlow(el, data, {" in render
    assert "alarmCapabilities.card_flow_enabled === true" in _app_block()


# ── 늦게 붙는 LLM 추천 칸(verifier 교정) ─────────────────────────────────────────


@pytest.mark.parametrize("feedback", [True, False])
def test_late_llm_prompt_goes_into_act_group_and_marks_act(feedback: bool) -> None:
    data = {**_ALARM, "resource_type": "management.x", "alarm_name": "관리 알람", "process_snapshot": None}
    out = _node(f"""
APP.caps({_js({**_CAPS_BASE, "prompt_suggest_enabled": True, "card_flow_enabled": True, "feedback_enabled": feedback})});
fetchImpl = function (u) {{
    if (u.indexOf("suggest-prompt") >= 0) return Promise.resolve({{ ok: true, json: function () {{
        return Promise.resolve({{ suggestion: {{ label: "L", text: "T" }} }}); }} }});
    return new Promise(function () {{}});
}};
var el = card({_js(data)});
for (var i = 0; i < 6; i++) await flush();
var s = el.querySelector(".alarm-prompt-section");
var group = s ? s.parentNode : null;
var kids = group ? group.children.map(function (c) {{ return c.className; }}) : [];
var before = Number(el.dataset.flowStage);
s.querySelector(".alarm-prompt-chip").click();
out = {{ inAct: group.classList.contains("alarm-flow-group--act"), kids: kids, before: before,
        after: Number(el.dataset.flowStage) }};
""")
    assert out["inAct"] is True
    idx = next(i for i, c in enumerate(out["kids"]) if "alarm-prompt-section" in c)
    if feedback:
        assert "alarm-feedback-section" in out["kids"][idx + 1]
    else:
        assert idx == len(out["kids"]) - 1
    assert out["before"] & 4 == 0 and out["after"] & 4 == 4


def test_late_llm_prompt_flow_off_original_position() -> None:
    data = {**_ALARM, "resource_type": "management.x", "alarm_name": "관리 알람", "process_snapshot": None}
    out = _node(f"""
APP.caps({_js({**_CAPS_BASE, "prompt_suggest_enabled": True, "feedback_enabled": False})});
fetchImpl = function (u) {{
    if (u.indexOf("suggest-prompt") >= 0) return Promise.resolve({{ ok: true, json: function () {{
        return Promise.resolve({{ suggestion: {{ label: "L", text: "T" }} }}); }} }});
    return new Promise(function () {{}});
}};
var el = card({_js(data)});
for (var i = 0; i < 6; i++) await flush();
var b = bubbleOf(el);
out = {{ last: b.children[b.children.length - 1].className, stage: el.dataset.flowStage || null }};
""")
    assert "alarm-prompt-section" in out["last"] and out["stage"] is None
