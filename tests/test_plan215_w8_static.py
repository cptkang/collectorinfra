"""plans/215 W8 — 업무 현황 포털 뷰 탭(정적 검사 + node 순수 함수·폴링 상태기계).

세 갈래(브라우저 없이 · 망·LLM 호출 0):
    1. 정적 — `biz-monitor.js`·`biz-monitor.html`에 HTML 문자열 삽입·확인 대화상자·새 창 0 ·
       새 탭은 P1 링크(`definitionLink`) 하나 · 포털 탭·컨테이너·스크립트 ·
       app.js 뷰 등록·기능 조회 키.
    2. 순수 함수 — 구성 맵 배치 결정성 · 필터 · 정렬 · 상태 띠 · 스파크라인 끊김 ·
       질문 채우기 · 탭 복원.
    3. mount 상태기계 — 가짜 DOM·가짜 타이머·가짜 fetch로 30초 폴링·heartbeat ·
       pause/hidden이면 요청 0 · 상세가 열려 있으면 상세만 갱신.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

_STATIC = Path(__file__).resolve().parents[1] / "src" / "static"
_JS = _STATIC / "js" / "biz-monitor.js"
_HTML = _STATIC / "biz-monitor.html"
_CSS = _STATIC / "css" / "biz-monitor.css"
_INDEX = _STATIC / "index.html"
_APP = _STATIC / "js" / "app.js"
_NVM_NODE = "/Users/cptkang/.nvm/versions/node/v20.20.0/bin/node"


def _node_bin() -> str:
    found = shutil.which("node") or (_NVM_NODE if Path(_NVM_NODE).exists() else None)
    if not found:
        pytest.skip("node 미설치 — JS 실행 검사 생략")
    return found


def _node(body: str, **values: Any) -> Any:
    head = "".join(f"var {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in values.items())
    script = "\n".join(
        [
            f"var BM = require({json.dumps(str(_JS))});",
            "var P = BM.pure;",
            "var out = null;",
            "(async function () {",
            head + body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error(e); process.exit(1); });",
        ]
    )
    done = subprocess.run(
        [_node_bin(), "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ─── 1. 정적 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [_JS, _HTML, _CSS])
def test_no_html_injection_confirm_or_new_window(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "confirm(",
        "window.open",
        'target="_blank"',
        "eval(",
    ):
        assert banned not in src, f"{path.name}: {banned}"


def test_only_new_tab_is_p1_definition_link() -> None:
    src = _JS.read_text(encoding="utf-8")
    assert src.count('"_blank"') == 1
    i = src.index('"_blank"')
    fn = src.rindex("function ", 0, i)
    assert src[fn:].startswith("function definitionLink(")
    assert '"/biz-services"' in src[fn:i]
    assert 'a.rel = "noopener"' in src[fn : i + 200]


def test_no_state_word_normal_and_state_vocabulary() -> None:
    src = _JS.read_text(encoding="utf-8")
    assert "정상" not in src
    for word in ("위험", "주의", "이상 신호 없음", "판정 불가"):
        assert word in src
    assert "지표 수집 꺼짐 — 알람 현황만" in src
    assert "자동 계산 또는 가져오기로 서비스를 만드세요" in src


def test_no_badge_and_no_external_library() -> None:
    html = _HTML.read_text(encoding="utf-8")
    # 조각은 마크업·스타일만(모듈은 index.html이 싣는다)
    assert "<script" not in html
    assert "view-tab-badge" not in html
    js = _JS.read_text(encoding="utf-8")
    assert "d3." not in js and "require(" not in js
    # (W8c) Chart.js는 「지표」 탭만 쓴다(전역 `Chart`가 없으면 표)
    # — 머리 주석·지표 탭 밖(개요 등)은 여전히 0
    body = js[js.index("(function (root)") :]
    metrics = body[body.index("// ─── 「지표」 보기 탭") : body.index("// ─── 「이벤트」 보기 탭")]
    assert "Chart" not in body.replace(metrics, "")


def test_index_tab_container_and_script() -> None:
    html = _INDEX.read_text(encoding="utf-8")
    tab = re.search(r'<button[^>]*data-view="biz"[^>]*>([^<]*)</button>', html)
    assert tab is not None and tab.group(1).strip() == "업무 현황"
    assert 'style="display: none;"' in tab.group(0)  # 기능 조회가 켜야 보인다
    assert 'id="bizView"' in html and 'class="biz-view view-hidden"' in html
    assert re.search(r'<script src="/static/js/biz-monitor\.js\?v=\d+"></script>', html)
    # 모듈은 app.js보다 먼저 싣는다
    assert html.index("biz-monitor.js") < html.index("/static/js/app.js")
    # 탭 줄에 배지 없음
    assert "bizTabBadge" not in html


def test_app_registers_view_and_reads_capability() -> None:
    app = _APP.read_text(encoding="utf-8")
    assert "biz: bizViewEl() }" in app
    # 진입 판정은 관제와 같은 시점 — 노드가 없으면(다른 하네스·조각 없음)
    # 업무 현황 상태를 건드리지 않는다
    settled = app[app.index("function noiseEntrySettled") :][:200]
    assert "bizEntrySettled();" in settled
    biz_settled = app[app.index("function bizEntrySettled") :][:120]
    assert "if (!bizViewEl()) return;" in biz_settled
    assert 'bizViewShown(view === "biz")' in app
    assert "alarmCapabilities.biz_monitor_enabled === true" in app
    assert 'BIZ_PAGE = "/static/biz-monitor.html"' in app
    assert "window.BizMonitor.mount(bizView" in app
    for opt in (
        "token:",
        "onAuthFail:",
        "isAdmin:",
        "service:",
        "tab:",
        "onRoute:",
        "fillQuestion:",
        "onInvestigateMember:",
    ):
        assert opt in app[app.index("window.BizMonitor.mount(bizView") :][:1200], opt
    # 질의응답 채우기는 채우기만(전송 함수 호출 없음)
    fill = app[app.index("function fillBizQuestion") : app.index("function investigateBizMember")]
    assert "reuseHistoryQuery(text)" in fill and "handleSend" not in fill


def test_fragment_has_every_slot_the_module_uses() -> None:
    js = _JS.read_text(encoding="utf-8")
    html = _HTML.read_text(encoding="utf-8")
    used = set(re.findall(r'q\("([A-Za-z]+)"\)', js))
    slots = set(re.findall(r'data-bm="([A-Za-z]+)"', html))
    assert used and used <= slots, used - slots
    assert "data-bm-root" in html and 'data-bm-layout="tile"' in html
    assert 'data-bm-layout="table"' in html
    assert 'href="/static/css/biz-monitor.css' in html


# ─── 2. 순수 함수 ───────────────────────────────────────────────────────

_TOPO = {
    "lanes": [
        {
            "layer": "web",
            "label": "웹",
            "folded_count": 0,
            "nodes": [{"id": 1, "kind": "sms_server", "name": "web-01", "state": "risk"}],
        },
        {
            "layer": "was",
            "label": "WAS",
            "folded_count": 3,
            "nodes": [
                {"id": 2, "kind": "apm_instance", "name": "was-a", "state": "caution"},
                {"id": 3, "kind": "sms_server", "name": "was-01", "state": "no_signal"},
            ],
        },
        {"layer": "db", "label": "DB", "folded_count": 0, "nodes": []},
        {
            "layer": "none",
            "label": "미지정",
            "folded_count": 0,
            "nodes": [{"id": 4, "kind": "sms_server", "name": "misc", "state": "unknown"}],
        },
    ],
    "edges": [
        {"from": 2, "to": 3, "estimated": True, "broken": False},
        {"from": 2, "to": 99, "estimated": False, "broken": False},
    ],
}


def test_layout_is_deterministic_and_lanes_stack() -> None:
    out = _node("out = [P.layoutTopology(T, 900), P.layoutTopology(T, 900)];", T=_TOPO)
    a, b = out
    assert a == b
    assert [lane["layer"] for lane in a["lanes"]] == ["web", "was", "db", "none"]
    ys = [lane["y"] for lane in a["lanes"]]
    assert ys == sorted(ys) and ys[0] == 0
    assert a["lanes"][2]["empty"] is True
    pos = {n["node"]["id"]: n for n in a["nodes"]}
    assert pos[2]["y"] < pos[3]["y"]  # 레인 안 APM 줄 → 서버 줄
    assert a["folds"] == [{"layer": "was", "count": 3, "x": pos[3]["x"] + 168, "y": pos[3]["y"]}]
    assert len(a["edges"]) == 1  # 보이지 않는 대상(99)으로는 긋지 않는다
    e = a["edges"][0]
    assert (e["y1"], e["y2"]) == (pos[2]["y"] + 36, pos[3]["y"])


def test_layout_wraps_by_width() -> None:
    nodes = [{"id": i, "kind": "sms_server", "name": f"s{i}", "state": "risk"} for i in range(9)]
    out = _node(
        "out = P.layoutTopology(T, 700);",
        T={
            "lanes": [{"layer": "web", "label": "웹", "folded_count": 0, "nodes": nodes}],
            "edges": [],
        },
    )
    per = out["perRow"]
    assert per == 3
    assert len({n["y"] for n in out["nodes"]}) == 3


def test_filter_options_and_filters() -> None:
    services = [
        {
            "id": 1,
            "display_name": "여신",
            "tier": 1,
            "zones": ["gongjon"],
            "state": "risk",
            "monitored": True,
        },
        {
            "id": 2,
            "display_name": "카드",
            "tier": 2,
            "zones": ["bankjon"],
            "state": "no_signal",
            "monitored": False,
        },
        {
            "id": 3,
            "display_name": "여신 배치",
            "tier": 1,
            "zones": ["bankjon", "gongjon"],
            "state": "caution",
            "monitored": True,
        },
    ]
    out = _node(
        """
var base = {state: "", zone: "", tier: "", monitoredOnly: false, q: ""};
function ids(f) {
    return P.filterServices(S, Object.assign({}, base, f)).map(function (s) { return s.id; });
}
out = {opts: P.filterOptions(S), all: ids({}), risk: ids({state: "risk"}),
       bank: ids({zone: "bankjon"}), t1: ids({tier: "1"}), mon: ids({monitoredOnly: true}),
       q: ids({q: "여신"})};
""",
        S=services,
    )
    assert out["opts"] == {"zones": ["bankjon", "gongjon"], "tiers": [1, 2]}
    assert out["all"] == [1, 2, 3]  # 서버 정렬 그대로
    assert out["risk"] == [1] and out["bank"] == [2, 3] and out["t1"] == [1, 3]
    assert out["mon"] == [1, 3] and out["q"] == [1, 3]


def test_sort_members_worst_first_then_name() -> None:
    out = _node(
        "out = P.sortMembers(M).map(function (m) { return m.id; });",
        M=[
            {"id": 1, "name": "b", "state": "no_signal"},
            {"id": 2, "name": "a", "state": "unknown"},
            {"id": 3, "name": "c", "state": "risk"},
            {"id": 4, "name": "a", "state": "no_signal"},
        ],
    )
    assert out == [3, 2, 4, 1]


def test_hour_label_and_formatting() -> None:
    out = _node(
        'out = [P.hourLabel("2026-11-05 13:00"), P.hourLabel(""), P.hourLabel(null),'
        ' P.fmtNum(12.345, "%"), P.fmtNum(1234.5, "ms"), P.fmtNum(null, "%")];'
    )
    assert out == ["13시 통계", "", "", "12.3%", "1235ms", "-"]


def test_evidence_line_and_reasons() -> None:
    out = _node("""
out = [P.evidenceLine({evidence: [{member_name: "web-01", metric: "cpu", value: 95, threshold: 90,
        rule: "sms_crit"}, {member_key: "x", rule: "incident_open"}]}),
       P.evidenceLine({evidence: [], reasons: ["지표 수집 꺼짐", "감시 꺼짐"]})];
""")
    assert out[0] == "web-01 · CPU 95% (임계 90%) · SMS 위험 임계 외 1건"
    assert out[1] == "지표 수집 꺼짐 · 감시 꺼짐"


def test_ask_text_lists_visible_servers_only() -> None:
    members = [
        {"id": i, "kind": "sms_server", "server_name": f"srv-{i:02d}", "state": "no_signal"}
        for i in range(25)
    ]
    members.append({"id": 99, "kind": "apm_instance", "name": "was-a", "state": "risk"})
    out = _node(
        "out = [P.askText(D), P.askText({service: {display_name: 'X'}, members: []})];",
        D={"service": {"display_name": "여신"}, "members": members},
    )
    assert out[0].startswith("업무 서비스 「여신」 구성원 서버 srv-00, srv-01")
    assert "외 5대" in out[0] and "was-a" not in out[0]
    assert out[1] == "업무 서비스 「X」의 최근 1시간 알람 현황을 알려줘"


def test_resolve_tab_unknown_or_disabled_falls_back_to_overview() -> None:
    out = _node("""
var tabs = [{id: "overview"}, {id: "events", enabled: function () { return true; }},
            {id: "metrics", enabled: function () { return false; }}];
out = [P.resolveTab(tabs, "events"), P.resolveTab(tabs, "metrics"), P.resolveTab(tabs, "zzz"),
       P.resolveTab(tabs, null), BM.DETAIL_TABS.map(function (t) { return t.id; })];
""")
    # W8b가 「이벤트」 탭을, W8c가 그 앞에 「지표」 탭을 더했다(W9 「진단」은 이벤트 뒤)
    assert out[:4] == ["events", "overview", "overview", "overview"]
    assert out[4][:3] == ["overview", "metrics", "events"]


def test_band_layout_and_spark_breaks_without_interpolation() -> None:
    since, now = "2026-11-05T00:00:00Z", "2026-11-05T01:00:00Z"
    segs = [
        {"start": since, "end": "2026-11-05T00:30:00Z", "state": "risk", "state_label": "위험"},
        {"start": "2026-11-05T00:30:00Z", "end": now, "state": None, "state_label": "기록 없음"},
    ]
    pts = [
        {"at": "2026-11-05T00:00:00Z", "sms_max": {"cpu": 10}},
        {"at": "2026-11-05T00:01:00Z", "sms_max": {"cpu": 20}},
        {"at": "2026-11-05T00:02:00Z", "sms_max": {}},  # 값 없음 → 끊김
        {"at": "2026-11-05T00:03:00Z", "sms_max": {"cpu": 40}},
        {"at": "2026-11-05T00:40:00Z", "sms_max": {"cpu": 30}},  # 먼 간격 → 끊김
    ]
    out = _node(
        """
out = {band: P.bandLayout(S, A, B, 600),
       spark: P.sparkSeries(PT, {group: "sms_max", key: "cpu"}, A, B, 600, 40),
       none: P.sparkSeries([], {group: "sms_max", key: "cpu"}, A, B, 600, 40)};
""",
        S=segs,
        A=since,
        B=now,
        PT=pts,
    )
    assert [(b["x"], b["w"], b["state"]) for b in out["band"]] == [
        (0, 300, "risk"),
        (300, 300, None),
    ]
    assert [len(line) for line in out["spark"]["lines"]] == [2, 1, 1]
    assert out["spark"]["last"] == 30 and out["spark"]["max"] == 40
    assert out["none"] == {"lines": [], "last": None, "min": None, "max": None}


# ─── 3. mount 상태기계(가짜 DOM · 타이머 · fetch) ─────────────────────────

_FAKE_DOM = r"""
function El(tag) {
    this.tagName = String(tag).toUpperCase(); this.children = []; this.parent = null;
    this.attrs = {};
    this.listeners = {}; this.style = {}; this.hidden = false; this._text = ""; this.className = "";
    this.value = ""; this.checked = false; this.clientWidth = 900;
    var self = this;
    this.classList = {
        add: function (c) {
            if (!self.classList.contains(c)) self.className = (self.className + " " + c).trim();
        },
        remove: function (c) {
            self.className = self.className.split(" ")
                .filter(function (x) { return x !== c; }).join(" ");
        },
        toggle: function (c, on) {
            if (on === undefined) on = !self.classList.contains(c);
            if (on) self.classList.add(c); else self.classList.remove(c);
        },
        contains: function (c) { return self.className.split(" ").indexOf(c) >= 0; },
    };
}
Object.defineProperty(El.prototype, "textContent", {
    get: function () {
        return this._text + this.children.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) {
        this.children.forEach(function (c) { c.parent = null; });
        this.children = []; this._text = String(v);
    },
});
Object.defineProperty(El.prototype, "firstChild", {
    get: function () { return this.children[0] || null; },
});
El.prototype.appendChild = function (c) {
    if (c.parent) c.parent.removeChild(c);
    c.parent = this; this.children.push(c); return c;
};
El.prototype.insertBefore = function (c, ref) {
    c.parent = this; var i = this.children.indexOf(ref);
    if (i < 0) this.children.push(c); else this.children.splice(i, 0, c);
    return c;
};
El.prototype.removeChild = function (c) {
    this.children = this.children.filter(function (x) { return x !== c; });
    c.parent = null; return c;
};
El.prototype.remove = function () { if (this.parent) this.parent.removeChild(this); };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) {
    return this.attrs[k] === undefined ? null : this.attrs[k];
};
El.prototype.addEventListener = function (t, f) {
    (this.listeners[t] = this.listeners[t] || []).push(f);
};
El.prototype.fire = function (t, ev) {
    var e = ev || { preventDefault: function () {} };
    (this.listeners[t] || []).forEach(function (f) { f(e); });
};
El.prototype.walk = function (fn) {
    fn(this); this.children.forEach(function (c) { c.walk(fn); });
};
function matcher(sel) {
    var m = /^\[([a-zA-Z-]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (!m) throw new Error("selector " + sel);
    return function (n) {
        return n.attrs[m[1]] !== undefined && (m[2] === undefined || n.attrs[m[1]] === m[2]);
    };
}
El.prototype.querySelectorAll = function (sel) {
    var f = matcher(sel), out = [];
    this.children.forEach(function (c) { c.walk(function (n) { if (f(n)) out.push(n); }); });
    return out;
};
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
var docListeners = {};
global.document = {
    hidden: false, fullscreenElement: null,
    createElement: function (t) { return new El(t); },
    createElementNS: function (ns, t) { return new El(t); },
    addEventListener: function (t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
    removeEventListener: function (t, f) {
        docListeners[t] = (docListeners[t] || []).filter(function (x) { return x !== f; });
    },
};
function fireDoc(t) { (docListeners[t] || []).forEach(function (f) { f(); }); }
// 가짜 타이머 — 시각을 손으로 민다
var clock = 0, timers = [], nextId = 1;
global.setTimeout = function (fn, ms) {
    var id = nextId++; timers.push({ id: id, at: clock + ms, fn: fn }); return id;
};
global.clearTimeout = function (id) {
    timers = timers.filter(function (t) { return t.id !== id; });
};
async function flush() { for (var i = 0; i < 30; i++) await Promise.resolve(); }
async function advance(ms) {
    var end = clock + ms;
    for (;;) {
        timers.sort(function (a, b) { return a.at - b.at; });
        var t = timers[0];
        if (!t || t.at > end) break;
        timers.shift(); clock = t.at; t.fn(); await flush();
    }
    clock = end;
}
// 조각 문서의 data-bm 칸을 그대로 만든다
var rootHost = new El("div");
var section = new El("section"); section.setAttribute("data-bm-root", "");
rootHost.appendChild(section);
SLOTS.forEach(function (name) {
    var n = new El("div"); n.setAttribute("data-bm", name); section.appendChild(n);
});
["tile", "table"].forEach(function (k) {
    var b = new El("button"); b.setAttribute("data-bm-layout", k); section.appendChild(b);
});
function slot(name) { return section.querySelector('[data-bm="' + name + '"]'); }
var calls = [];
function fakeFetch(url, init) {
    calls.push((init && init.method || "GET") + " " + url);
    var body = BODIES[url.replace("/api/v1/biz-services/monitor", "") || "/"];
    var status = body === undefined ? 404 : 200;
    return Promise.resolve({
        ok: status === 200, status: status,
        json: function () { return Promise.resolve(body || {}); },
    });
}
function take() { var c = calls; calls = []; return c; }
"""

_BOARD = {
    "generated_at": "2026-11-05T01:00:00Z",
    "window_minutes": 60,
    "summary": {
        "state_counts": {"risk": 1, "caution": 0, "no_signal": 1, "unknown": 0},
        "service_count": 2,
        "open_incidents": 2,
    },
    "metrics_status": {"state": "off", "label": "꺼짐", "last_success_at": None, "reasons": []},
    "services": [
        {
            "id": 1,
            "display_name": "여신",
            "tier": 1,
            "monitored": True,
            "zones": ["gongjon"],
            "member_count": 2,
            "hidden_member_count": 0,
            "state": "risk",
            "state_label": "위험",
            "reasons": [],
            "alarms": {
                "open_incidents": 2,
                "recent_by_handling": {"delivered": 3, "suppressed": 1},
            },
            "metrics": {
                "updated_at": "2026-11-05T01:00:00Z",
                "sms_stat_time": "2026-11-05 09:00",
                "sms_max": {"cpu": 91},
                "apm_max": {"error_rate": 1.5},
            },
        },
        {
            "id": 2,
            "display_name": "카드",
            "tier": 2,
            "monitored": False,
            "zones": ["bankjon"],
            "member_count": 1,
            "hidden_member_count": 0,
            "state": "no_signal",
            "state_label": "이상 신호 없음",
            "reasons": [],
            "alarms": {},
            "metrics": None,
        },
    ],
    "hidden_service_count": 0,
    "unattributed": {"recent_alarms": 0, "open_incidents": 0, "tree_estimates": []},
    "notices": [],
}
_DETAIL = {
    "generated_at": "2026-11-05T01:00:00Z",
    "metrics_status": {"state": "off"},
    "service": {
        "id": 1,
        "display_name": "여신",
        "tier": 1,
        "monitored": True,
        "state": "risk",
        "state_label": "위험",
        "member_count": 2,
        "evidence": [],
        "reasons": [],
    },
    "members": [
        {
            "id": 11,
            "kind": "sms_server",
            "name": "web-01",
            "server_name": "web-01",
            "state": "risk",
            "state_label": "위험",
            "layer": "web",
            "alarms": {},
            "sms": {"cpu": 91},
        },
        {
            "id": 21,
            "kind": "apm_instance",
            "name": "was-a",
            "state": "no_signal",
            "state_label": "이상 신호 없음",
            "alarms": {},
            "apm": {},
        },
    ],
    "hidden_member_count": 1,
    "unattributed_service_alarms": 0,
    "notices": [],
}
_TOPOLOGY = {
    "service_id": 1,
    "member_count": 2,
    "hidden_member_count": 1,
    "folded": False,
    "lanes": [
        {
            "layer": "web",
            "label": "웹",
            "folded_count": 0,
            "nodes": [
                {
                    "id": 11,
                    "kind": "sms_server",
                    "name": "web-01",
                    "state": "risk",
                    "state_label": "위험",
                }
            ],
        },
        {
            "layer": "was",
            "label": "WAS",
            "folded_count": 0,
            "nodes": [
                {
                    "id": 21,
                    "kind": "apm_instance",
                    "name": "was-a",
                    "state": "no_signal",
                    "state_label": "이상 신호 없음",
                    "outside_targets": 0,
                    "link_note": "",
                }
            ],
        },
    ],
    "edges": [{"from": 21, "to": 11, "estimated": True, "broken": False, "label": ""}],
    "links_status": {"available": True, "reason": ""},
}
_TREND = {
    "generated_at": "2026-11-05T01:00:00Z",
    "since": "2026-11-04T01:00:00Z",
    "monitored": True,
    "band": {
        "segments": [
            {
                "start": "2026-11-04T01:00:00Z",
                "end": "2026-11-05T01:00:00Z",
                "state": "risk",
                "state_label": "위험",
            }
        ],
        "reason": "",
        "truncated": False,
    },
    "series": {"points": [], "reason": "지표 수집 꺼짐 — 알람 현황만"},
}
_BODIES = {
    "/": _BOARD,
    "/1": _DETAIL,
    "/1/topology": _TOPOLOGY,
    "/1/trend": _TREND,
    "/heartbeat": {"recorded": False},
}


def _slots() -> list[str]:
    return sorted(set(re.findall(r'data-bm="([A-Za-z]+)"', _HTML.read_text(encoding="utf-8"))))


def _mount(script: str) -> Any:
    return _node(_FAKE_DOM + script, SLOTS=_slots(), BODIES=_BODIES)


def test_list_polls_30s_with_heartbeat_and_pause_stops_requests() -> None:
    out = _mount("""
var routes = [];
var h = BM.mount(rootHost, {token: function () { return "t"; }, fetch: fakeFetch,
    onRoute: function (r) { routes.push(r); }});
await flush();
var first = take();
await advance(29999);
var early = take();
await advance(1);
var tick = take();
h.pause();
await advance(120000);
var paused = take();
h.resume();
await flush();
var resumed = take();
document.hidden = true; fireDoc("visibilitychange");
await advance(120000);
var hidden = take();
document.hidden = false; fireDoc("visibilitychange");
await flush();
var shown = take();
var tiles = slot("services").children.filter(function (c) { return c.tagName === "BUTTON"; });
out = {first: first, early: early, tick: tick, paused: paused, resumed: resumed, hidden: hidden,
       shown: shown, routes: routes, tiles: tiles.map(function (t) { return t.textContent; }),
       chips: slot("stateChips").textContent, metrics: slot("metricsStatus").textContent,
       notices: slot("notices").textContent};
""")
    board = "GET /api/v1/biz-services/monitor"
    beat = "POST /api/v1/biz-services/monitor/heartbeat"
    assert sorted(out["first"]) == sorted([board, beat])
    assert out["early"] == []
    assert sorted(out["tick"]) == sorted([board, beat])
    assert out["paused"] == []  # 탭 비활성 = 요청 0
    assert sorted(out["resumed"]) == sorted([board, beat])
    assert out["hidden"] == []  # 브라우저 탭 숨김 = 요청 0
    assert sorted(out["shown"]) == sorted([board, beat])
    assert out["routes"] == []
    assert "위험" in out["tiles"][0] and "여신" in out["tiles"][0]
    assert "9시 통계" in out["tiles"][0] and "전달 3 / 억제 1" in out["tiles"][0]
    assert "감시 꺼짐 — 알람 현황만" in out["tiles"][1]  # 감시 꺼진 서비스는 알람 칸만
    assert "위험1" in out["chips"] and "판정 불가0" in out["chips"]
    assert out["metrics"].startswith("지표 수집: 꺼짐")
    assert "지표 수집 꺼짐 — 알람 현황만" in out["notices"]


def test_detail_polls_detail_only_and_restores_route() -> None:
    out = _mount("""
var routes = [];
var asked = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "zzz",
    onRoute: function (r) { routes.push(r); },
    fillQuestion: function (t) { asked.push(t); return true; },
    onInvestigateMember: function (m) { asked.push("inv:" + m.name); return true; },
    isAdmin: function () { return false; }});
await flush();
var first = take();
await advance(30000);
var tick = take();
var panel = slot("detailPanel");
var rows = panel.querySelectorAll('[data-bm-member]')
    .map(function (r) { return r.getAttribute("data-bm-member"); });
var chip = panel.querySelector('[data-bm-node="21"]');
chip.fire("click");
var focused = panel.querySelector('[data-bm-member="21"]').className;
var head = slot("detailHead");
var buttons = [];
head.walk(function (n) { if (n.tagName === "BUTTON" || n.tagName === "A") buttons.push(n); });
buttons.filter(function (b) { return b.textContent === "질의응답에서 묻기"; })[0].fire("click");
panel.querySelectorAll('[data-bm-member]')[0]
    .walk(function (n) { if (n.tagName === "BUTTON") n.fire("click"); });
var back = buttons.filter(function (b) { return b.textContent === "목록으로"; })[0];
back.fire("click");
await flush();
var afterBack = take();
out = {first: first, tick: tick, routes: routes, rows: rows, focused: focused,
       labels: buttons.map(function (b) { return b.textContent; }), asked: asked,
       afterBack: afterBack, panelText: panel.textContent, headText: head.textContent};
""")
    base = "/api/v1/biz-services/monitor"
    assert sorted(out["first"]) == sorted(
        [
            f"GET {base}",
            f"POST {base}/heartbeat",
            "GET /api/v1/alarm/capabilities",  # (W8c) 지표 탭 표시 — mount마다 1회
            f"GET {base}/1",
            f"GET {base}/1/topology",
            f"GET {base}/1/trend",
        ]
    )
    assert sorted(out["tick"]) == sorted(
        [  # 상세가 열려 있으면 상세만
            f"POST {base}/heartbeat",
            f"GET {base}/1",
            f"GET {base}/1/topology",
            f"GET {base}/1/trend",
        ]
    )
    assert out["routes"][0] == {"service": "1", "tab": "overview"}  # 모르는 tab → overview
    assert out["routes"][-1] == {"service": None, "tab": None}
    assert out["rows"] == ["11", "21"]  # 상태 나쁜 순
    assert "bm-row--focus" in out["focused"]  # 칩 클릭 → 그 행 강조
    assert "정의에서 편집" not in out["labels"]  # 관리자 아님
    assert out["asked"][0].startswith("업무 서비스 「여신」 구성원 서버 web-01")
    assert out["asked"][1] == "inv:web-01"
    # 목록으로 → 목록 갱신 재개(heartbeat는 주기 그대로)
    assert out["afterBack"] == [f"GET {base}"]
    assert "권한 밖 1대" in out["panelText"] and "구성원 2대 (권한 밖 1대)" in out["headText"]
    assert "지표 수집 꺼짐 — 알람 현황만" in out["panelText"]  # 추이 점 사유


def test_admin_sees_definition_link_only_new_tab_and_unmount_stops() -> None:
    out = _mount("""
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", isAdmin: function () { return true; }});
await flush();
var links = [];
slot("detailHead").walk(function (n) {
    if (n.tagName === "A") {
        links.push({t: n.textContent, href: n.href, target: n.target, rel: n.rel});
    }
});
take();
h.unmount();
await advance(120000);
out = {links: links, after: take()};
""")
    assert out["links"] == [
        {
            "t": "정의에서 편집",
            "href": "/biz-services?service=1",
            "target": "_blank",
            "rel": "noopener",
        }
    ]
    assert out["after"] == []


def test_unknown_service_404_returns_to_list_with_notice() -> None:
    out = _mount("""
var routes = [];
var h = BM.mount(rootHost, {
    fetch: fakeFetch, service: "7", onRoute: function (r) { routes.push(r); },
});
await flush();
await flush();
out = {routes: routes, hidden: slot("detailPane").hidden, notices: slot("flash").textContent};
""")
    assert out["routes"][-1] == {"service": None, "tab": None}
    assert out["hidden"] is True
    assert "찾을 수 없습니다" in out["notices"]
