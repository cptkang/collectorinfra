"""plans/215 W8c — 업무 현황 상세 「지표」 탭 화면(biz-monitor.js) 정적·node 검사.

브라우저 도구 없이 가짜 DOM·타이머·fetch(W8 검사 도구)로 mount 상태기계를 돌린다.
그래프는 가짜 `Chart`로 설정만 받아 본다(전역 `Chart`가 없으면 표 대체).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tests.test_plan215_w8_static import _BODIES, _FAKE_DOM, _node, _slots

_JS = Path(__file__).resolve().parents[1] / "src" / "static" / "js" / "biz-monitor.js"
_CSS = Path(__file__).resolve().parents[1] / "src" / "static" / "css" / "biz-monitor.css"
BASE = "/api/v1/biz-services/monitor"
CAPS = "/api/v1/alarm/capabilities"


def _metrics(window: str = "24h", *, thresholds: dict[str, Any] | None = None,
             max_members: int = 20) -> dict[str, Any]:
    until = "2026-11-05T01:00:00Z"
    since = {"1h": "2026-11-05T00:00:00Z", "24h": "2026-11-04T01:00:00Z"}.get(
        window, "2026-11-04T01:00:00Z")
    return {
        "service_id": 1, "window": window, "compare": False, "memo": False,
        "generated_at": until, "range": {"since": since, "until": until},
        "selection": {"mode": "auto", "queried": 2, "members": 3, "max": max_members},
        "hidden_member_count": 1,
        "members": [
            {"id": 11, "kind": "sms_server", "name": "web-01", "layer": "web", "state": "risk",
             "state_label": "위험", "recent_alarms": 3, "open_incidents": 1, "supported": True,
             "queried": True},
            {"id": 21, "kind": "apm_instance", "name": "was-a", "layer": "was",
             "state": "no_signal", "state_label": "이상 신호 없음", "recent_alarms": 0,
             "open_incidents": 0, "supported": True, "queried": True},
            {"id": 31, "kind": "sms_server", "name": "db-01", "layer": "db", "state": "no_signal",
             "state_label": "이상 신호 없음", "recent_alarms": 0, "open_incidents": 0,
             "supported": True, "queried": False},
        ],
        "series": [
            {"member_id": 11, "kind": "sms_server", "source": "sms", "metric": "cpu",
             "label": "CPU", "unit": "%", "step_seconds": 3600, "yesterday": False,
             "points": [["2026-11-04T22:00:00Z", 40.0], ["2026-11-04T23:00:00Z", 55.0],
                        ["2026-11-05T01:00:00Z", 60.0]]},  # 00시 칸 없음 → 끊김
            {"member_id": 21, "kind": "apm_instance", "source": "apm", "metric": "service_time",
             "label": "응답시간", "unit": "ms", "step_seconds": 900, "yesterday": False,
             "points": [["2026-11-05T00:45:00Z", 120.0], ["2026-11-05T01:00:00Z", 130.0]]},
        ],
        "sms": {"state": "ok", "reason": "", "calls": 1, "through": "10시 통계까지",
                "table": "hour"},
        "apm": {"state": "partial", "reason": "s2 조회 실패", "calls": 1, "through": ""},
        "thresholds": thresholds or {},
        "links": [{"from": 21, "to": 11, "estimated": True}],
        "partial": True, "notices": [],
    }


_EVENTS24 = {
    "service_id": 1, "window": "24h", "generated_at": "2026-11-05T01:00:00Z",
    "sources_available": {"polestar_alarm": {"label": "폴스타 알람", "available": True}},
    "rows": [
        {"at": "2026-11-05T00:40:00Z", "source": "polestar_alarm", "source_label": "폴스타 알람",
         "member_key": "pg|101", "member_name": "web-01", "summary": "CPU 높음",
         "severity": "critical", "count": 4, "alarm_id": "a1"},
        {"at": "2026-11-05T00:41:00Z", "source": "polestar_alarm", "source_label": "폴스타 알람",
         "member_key": "pg|101", "member_name": "web-01", "summary": "메모리",
         "severity": "warning", "count": 1},
        {"at": "2026-11-04T12:00:00Z", "source": "polestar_alarm", "source_label": "폴스타 알람",
         "member_key": "pg|101", "member_name": "web-01", "summary": "디스크",
         "severity": "attention", "count": 1},
    ],
    "hidden_row_count": 0, "truncated": False, "notices": [],
}

_HELPERS = r"""
Date.now = function () { return clock; };
function all(node, pred) {
  var out = []; node.walk(function (n) { if (pred(n)) out.push(n); }); return out;
}
function attr(name, value) {
  return function (n) { return value === undefined ? n.getAttribute(name) !== null
    : n.getAttribute(name) === value; };
}
function button(node, label) {
    return all(node, function (n) { return n.tagName === "BUTTON" && n.textContent === label; })[0];
}
function tabs() {
    return all(slot("detailTabs"), attr("data-bm-tab")).map(function (b) {
        return b.getAttribute("data-bm-tab"); });
}
function only(part) { return take().filter(function (c) { return c.indexOf(part) >= 0; }); }
var charts = [], destroyed = 0;
function FakeChart(canvas, cfg) {
    this.cfg = cfg; charts.push(cfg);
    this.destroy = function () { destroyed += 1; };
    this.draw = function () {};
}
"""


def _run(script: str, *, caps: dict[str, Any] | None, bodies: dict[str, Any] | None = None) -> Any:
    merged: dict[str, Any] = {**_BODIES, **(bodies or {})}
    if caps is not None:
        merged[CAPS] = caps
    return _node(_FAKE_DOM + _HELPERS + script, SLOTS=_slots(), BODIES=merged)


_ON = {"biz_history_enabled": True}


# ─── 1. 정적 ────────────────────────────────────────────────────────────


def test_metrics_tab_segment_has_no_html_injection_or_new_window() -> None:
    src = _JS.read_text(encoding="utf-8")
    seg = src[src.index("// ─── 「지표」 보기 탭") : src.index("// ─── 「이벤트」 보기 탭")]
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "confirm(", "window.open",
                   "_blank", "target", "location"):
        assert banned not in seg, banned
    assert not re.search(r"정상", seg)
    assert "spanGaps: false" in seg
    # 지표 탭은 이벤트 앞 · W9 「진단」 탭은 이벤트 뒤(W9가 자리 주석 `(W9)`를 탭 정의로 바꿨다)
    tabs = src[src.index("var DETAIL_TABS = [") : src.index("];", src.index("var DETAIL_TABS = ["))]
    assert tabs.index('id: "overview"') < tabs.index('id: "metrics"') < tabs.index('id: "events"')
    assert tabs.index('id: "events"') < tabs.index('id: "diagnosis"')
    assert "biz_history_enabled === true" in tabs


def test_css_wide_mode_and_line_tokens_use_theme() -> None:
    css = _CSS.read_text(encoding="utf-8")
    assert ".bm--wide .bm-list { display: none; }" in css
    tokens = re.findall(r"--bm-line-\d: ([^;]+);", css)
    assert len(tokens) == 8 and all(t.startswith("var(--") for t in tokens)


# ─── 2. 순수 함수 ─────────────────────────────────────────────────────────


def test_pure_metrics_url_gap_lanes_markers() -> None:
    body = _metrics()
    many = [
        {"member_id": i, "source": "sms", "metric": "cpu", "label": "CPU", "unit": "%",
         "step_seconds": 3600, "yesterday": False, "points": []}
        for i in range(1, 11)
    ]
    out = _node(
        """
out = {
  u1: P.metricsUrl("1", {window: "6h", members: [11, 21], compare: true}),
  u2: P.metricsUrl("1", {window: "7d", compare: true}),
  u3: P.metricsUrl("1", {window: "zz", members: ["1 OR 1"]}),
  gap: P.gapPoints(S[0].points, 3600),
  nul: P.gapPoints([["2026-11-05T00:00:00Z", null], ["bad", 3]], 60),
  lanes: P.laneGroups(S, M).map(function (g) {
      return [g.lane, g.label, g.charts.map(function (c) { return c.key; })]; }),
  cap: P.laneGroups(MANY, []).map(function (g) {
      return g.charts.map(function (c) { return [c.members.length, c.more]; }); }),
  marks: P.eventMarkers(EV, R.since, R.until, 120),
  cursor: P.cursorValues(S, Date.parse("2026-11-05T00:10:00Z")).map(function (v) {
      return v.value; }),
  stat: P.statLabel("2026-11-05T01:00:00Z", "24h") !== "",
};
""",
        S=body["series"], M=body["members"], MANY=many, EV=_EVENTS24["rows"], R=body["range"],
    )
    assert out["u1"] == f"{BASE}/1/metrics?window=6h&members=11,21&compare=yesterday"
    assert out["u2"] == f"{BASE}/1/metrics?window=7d"  # 7d는 비교 없음
    assert out["u3"] == f"{BASE}/1/metrics?window=24h"
    ys = [p["y"] for p in out["gap"]]
    assert ys == [40.0, 55.0, None, 60.0]  # 빈 칸에 null — 이어 긋지 않는다(보간 0)
    assert out["nul"] == []
    assert out["lanes"] == [["web", "웹", ["sms:cpu"]], ["was", "WAS", ["apm:service_time"]]]
    assert out["cap"] == [[[8, 2]]]  # 그래프 하나 선 8개 + 「외 2대」
    assert sum(m["count"] for m in out["marks"]) == 3  # 표지 건수 합 = 기간 안 행 수
    assert out["marks"][-1]["worst"] == "critical"
    assert out["cursor"] == [None, None]  # 반 칸 안에 점 없음 → 값 없음
    assert out["stat"] is True


# ─── 3. mount ─────────────────────────────────────────────────────────────


def test_flag_off_no_tab_no_request_and_tab_param_falls_back() -> None:
    out = _run(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "metrics",
    onRoute: function (r) { routes.push(r); }});
await flush();
var first = take();
await advance(120000);
out = {first: first, later: take(), tabs: tabs(), routes: routes};
""",
        caps={"biz_history_enabled": False},
    )
    assert not [c for c in out["first"] + out["later"] if "/metrics" in c]
    assert out["tabs"] == ["overview", "events"]
    assert out["routes"][-1] == {"service": "1", "tab": "overview"}
    assert out["first"].count(f"GET {CAPS}") == 1 and f"GET {CAPS}" not in out["later"]


def test_restore_tab_param_table_fallback_markers_and_pause() -> None:
    out = _run(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "metrics",
    onRoute: function (r) { routes.push(r); }});
await flush();
var first = take();
var panel = slot("detailPanel");
var text = panel.textContent;
var grids = all(panel, attr("data-bm-metrics-grid")).map(function (n) {
    return n.getAttribute("data-bm-metrics-grid"); });
var marks = all(panel, attr("data-bm-marker")).map(function (n) {
    return Number(n.getAttribute("data-bm-marker")); });
await advance(30000);
var tick30 = take();
await advance(90000);
var later = only("/metrics");
h.pause();
await advance(300000);
var paused = take();
out = {first: first, routes: routes, tabs: tabs(), text: text, grids: grids, marks: marks,
       tick30: tick30, later: later, paused: paused};
""",
        caps=_ON,
        bodies={"/1/metrics?window=24h": _metrics(), "/1/events?window=24h": _EVENTS24},
    )
    assert out["tabs"] == ["overview", "metrics", "events"]
    assert out["routes"][-1] == {"service": "1", "tab": "metrics"}  # &tab=metrics 복원
    assert sorted(c for c in out["first"] if "/1" in c) == sorted([
        f"GET {BASE}/1", f"GET {BASE}/1/metrics?window=24h", f"GET {BASE}/1/events?window=24h",
    ])  # 개요 조회(topology·trend) 0
    assert "그래프 도구를 불러오지 못했습니다 — 표로 보여 줍니다" in out["text"]
    assert out["grids"] == ["sms:cpu", "apm:service_time"]
    assert "조회 2대 / 구성원 3대" in out["text"]
    assert "SMS 조회됨 · 10시 통계까지" in out["text"]
    assert "APM 일부만 조회 · s2 조회 실패" in out["text"]
    assert "권한 밖 구성원 1대 — 선 없이 건수만" in out["text"]
    assert "시 통계" in out["text"]  # 표의 SMS 시각
    assert sum(out["marks"]) == 3 and "이벤트 3건" in out["text"]
    # 24h는 수동 — 30초 상세 갱신에도 지표를 다시 읽지 않는다
    assert out["tick30"] == [f"POST {BASE}/heartbeat", f"GET {BASE}/1"]
    assert out["later"] == []
    assert out["paused"] == []  # pause = 요청 0


def test_chart_config_no_apm_threshold_cursor_and_leave_destroys() -> None:
    out = _run(
        """
global.Chart = FakeChart;
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "metrics"});
await flush();
take();
var panel = slot("detailPanel");
var sets = charts.map(function (c) { return c.options && c.data.datasets.map(function (d) {
    return {label: d.label, threshold: !!d.threshold, spanGaps: d.spanGaps,
            ys: d.data.map(function (p) { return p.y; })}; }); });
var x = charts[0].options.scales.x;
var t = Date.parse("2026-11-05T01:00:00Z");
charts[0].options.onHover({x: 5}, [], {scales: {x: {getValueForPixel: function () { return t; }}}});
var readouts = all(panel, attr("data-bm-cursor")).map(function (n) { return n.textContent; });
charts[1].options.onClick({}, [{datasetIndex: 0}], {data: charts[1].data});
var drawer = all(panel, attr("data-bm-drawer"))[0];
var drawerText = drawer ? drawer.textContent : "";
button(slot("detailTabs"), "개요").fire("click");
await flush();
out = {sets: sets, xType: x.type, readouts: readouts,
       drawer: drawer && drawer.getAttribute("data-bm-drawer"),
       drawerText: drawerText, destroyed: destroyed, charts: charts.length, after: take()};
""",
        caps=_ON,
        bodies={
            "/1/metrics?window=24h": _metrics(thresholds={"cpu": {"warn": 80.0, "crit": 90.0}}),
            "/1/events?window=24h": _EVENTS24,
        },
    )
    sms, apm = out["sets"][0], out["sets"][1]
    assert out["xType"] == "linear"
    assert [s["threshold"] for s in sms] == [False, True, True]  # SMS 「설정 임계」 주의·위험
    assert not any(s["threshold"] for s in apm)  # APM 임계선 0
    assert sms[0]["spanGaps"] is False and sms[0]["ys"] == [40.0, 55.0, None, 60.0]
    assert out["readouts"][0].endswith("web-01 60%") and "시 통계" in out["readouts"][0]
    assert "was-a 130ms" in out["readouts"][1]
    assert out["drawer"] == "21" and "연계: → web-01" in out["drawerText"]
    # 서랍을 열며 다시 그릴 때 2개 · 다른 탭으로 옮길 때 2개 — 만든 그래프는 모두 해제(누수 0)
    assert out["destroyed"] == out["charts"] == 4
    assert f"GET {BASE}/1/topology" in out["after"]


def test_one_hour_auto_60s_leave_tab_zero_requests_and_429_note() -> None:
    out = _run(
        """
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "metrics"});
await flush();
take();
var panel = slot("detailPanel");
button(panel, "1h").fire("click");
await flush();
var click = only("/metrics");
await advance(30000);
var t30 = only("/metrics");
await advance(30000);
var t60 = only("/metrics");
button(slot("detailTabs"), "이벤트").fire("click");
await flush();
take();
await advance(120000);
var away = only("/metrics");
button(slot("detailTabs"), "지표").fire("click");
await flush();
take();
button(panel, "6h").fire("click");
await flush();
var busy = panel.textContent;
out = {click: click, t30: t30, t60: t60, away: away, busy: busy};
""",
        caps=_ON,
        bodies={
            "/1/metrics?window=24h": _metrics(),
            "/1/metrics?window=1h": _metrics("1h"),
            "/1/events?window=24h": _EVENTS24,
            "/1/events?window=1h": {**_EVENTS24, "window": "1h", "rows": []},
        },
    )
    assert out["click"] == [f"GET {BASE}/1/metrics?window=1h"]
    assert out["t30"] == []
    assert out["t60"] == [f"GET {BASE}/1/metrics?window=1h"]  # 1h만 60초 자동
    assert out["away"] == []  # 탭을 떠나면 지표 요청 0
    assert "지표를 불러오지 못했습니다(HTTP 404)" in out["busy"]


def test_busy_429_note_and_no_parallel_request() -> None:
    out = _node(
        _FAKE_DOM
        + _HELPERS
        + """
var gate = null;
var base = fakeFetch;
var wrapped = function (url, init) {
    if (url.indexOf("/metrics?window=6h") >= 0) {
        calls.push("GET " + url);
        return Promise.resolve({ok: false, status: 429, json: function () {
            return Promise.resolve({detail: "다른 지표 조회가 진행 중입니다"}); }});
    }
    if (url.indexOf("/metrics?window=1h") >= 0) {
        calls.push("GET " + url);
        return new Promise(function (res) { gate = res; });
    }
    return base(url, init);
};
var h = BM.mount(rootHost, {fetch: wrapped, service: "1", tab: "metrics"});
await flush();
take();
var panel = slot("detailPanel");
button(panel, "1h").fire("click");
await flush();
button(panel, "6h").fire("click");
await flush();
var during = only("/metrics");
gate({ok: true, status: 200, json: function () { return Promise.resolve(B1); }});
await flush();
var after = only("/metrics");
out = {during: during, after: after, text: panel.textContent};
""",
        SLOTS=_slots(),
        BODIES={**_BODIES, CAPS: _ON, "/1/metrics?window=24h": _metrics(),
                "/1/events?window=24h": _EVENTS24},
        B1=_metrics("1h"),
    )
    assert out["during"] == [f"GET {BASE}/1/metrics?window=1h"]  # 진행 중이면 새로 보내지 않는다
    assert out["after"] == [f"GET {BASE}/1/metrics?window=6h"]  # 끝난 뒤 지금 조건으로 1회
    assert "다른 지표 조회가 진행 중입니다" in out["text"]


def test_marker_opens_events_tab_same_window_without_extra_query() -> None:
    out = _run(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "metrics",
    onRoute: function (r) { routes.push(r); }});
await flush();
take();
var panel = slot("detailPanel");
var mk = all(panel, attr("data-bm-marker"));
mk[mk.length - 1].fire("click");
await flush();
var after = take();
var focused = all(panel, function (n) { return n.getAttribute("data-bm-event") !== null &&
    n.className.indexOf("bm-row--focus") >= 0; }).length;
out = {after: after, route: routes[routes.length - 1], focused: focused, tabs: tabs(),
       pressed: all(panel, attr("data-bm-window", "24h"))[0].getAttribute("aria-pressed")};
""",
        caps=_ON,
        bodies={"/1/metrics?window=24h": _metrics(), "/1/events?window=24h": _EVENTS24},
    )
    # (§5.2.1) 주소에 그 칸 마지막 행 시각 `&at=` — plans/215 잔여 ⑤
    assert out["route"] == {"service": "1", "tab": "events", "at": "2026-11-05T00:41:00Z"}
    assert out["after"] == []  # 표지가 가진 타임라인을 그대로 — 추가 조회 0
    assert out["pressed"] == "true"
    assert out["focused"] == 1  # 그 시각에 가장 가까운 행(00:41) 하나만 강조


def test_picker_cap_wide_same_window_and_drawer_one_member() -> None:
    out = _run(
        """
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "metrics",
    fillQuestion: function (t) { asked.push(t); return true; },
    onInvestigateMember: function (m) { asked.push("inv:" + m.name); return true; }});
var asked = [];
await flush();
take();
var panel = slot("detailPanel");
button(panel, "넓게 보기").fire("click");
var wide = section.className;
button(panel, "구성원 고르기").fire("click");
function box(id) { return all(panel, attr("data-bm-pick", id))[0]; }
var locked = [box("11").disabled, box("21").disabled, box("31").disabled];
box("21").checked = false; box("21").fire("change");
var unlocked = box("31").disabled;
box("31").checked = true; box("31").fire("change");
button(panel, "이 구성원으로 조회").fire("click");
await flush();
var picked = only("/metrics");
all(panel, attr("data-bm-line", "11"))[0].fire("click");
button(panel, "이 구성원 알람 조사").fire("click");
button(panel, "질의응답에서 묻기").fire("click");
button(panel, "이 구성원만 보기").fire("click");
await flush();
var one = only("/metrics");
button(slot("detailTabs"), "개요").fire("click");
out = {wide: wide, locked: locked, unlocked: unlocked, picked: picked, one: one, asked: asked,
       narrow: section.className};
""",
        caps=_ON,
        bodies={
            "/1/metrics?window=24h": _metrics(max_members=2),
            "/1/metrics?window=24h&members=11,31": _metrics(max_members=2),
            "/1/metrics?window=24h&members=11": _metrics(max_members=2),
            "/1/events?window=24h": _EVENTS24,
        },
    )
    assert "bm--wide" in out["wide"]  # 같은 창에서 목록 칸을 접는다
    assert "bm--wide" not in out["narrow"]  # 탭을 떠나면 원래대로
    assert out["locked"] == [False, False, True]  # 상한 2 — 3번째 칸 잠김
    assert out["unlocked"] is False
    assert out["picked"] == [f"GET {BASE}/1/metrics?window=24h&members=11,31"]
    assert out["one"] == [f"GET {BASE}/1/metrics?window=24h&members=11"]
    assert out["asked"][0] == "inv:web-01"
    assert out["asked"][1] == "업무 서비스 「여신」 구성원 web-01의 최근 24h 지표 추이를 알려줘"
