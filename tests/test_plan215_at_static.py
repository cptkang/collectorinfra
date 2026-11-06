"""plans/215 잔여 ⑤ — 업무 현황 「이벤트」 보기 `&at=` 시점 지정 정적·node 검사.

대상: biz-monitor.js · app.js.

- 지표 표지 클릭 → 주소 `&tab=events&at=<ISO 8601 UTC>` · 그 시각에 가장 가까운 행 강조 +
  1회 스크롤.
- 주소로 다시 열면 같은 행 강조 — 그 시각이 기본 기간(1h) 밖이면 담는 가장 작은 기간으로 연다.
- 형식 오류·미래·7일 넘은 과거는 무시(사유 문구 0 · 종전 동작) · 기간을 바꾸거나 탭을 떠나면
  `at`을 지운다.
- pause면 요청 0 · `textContent`만 · 확인 대화상자·새 탭 0.
가짜 DOM·타이머·fetch는 W8 검사 도구를 쓴다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_plan215_w8_static import _BODIES, _FAKE_DOM, _node, _slots
from tests.test_plan215_w8b_static import _func, _run

_STATIC = Path(__file__).resolve().parents[1] / "src" / "static"
_JS = _STATIC / "js" / "biz-monitor.js"
_APP = _STATIC / "js" / "app.js"
BASE = "/api/v1/biz-services/monitor"
NOW = "2026-11-05T01:00:00Z"


def _row(at: str, summary: str) -> dict[str, Any]:
    return {"at": at, "source": "polestar_alarm", "source_label": "폴스타 알람",
            "member_key": "pg|101", "member_name": "web-01", "summary": summary,
            "severity": "warning", "count": 1}


def _events(window: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"service_id": 1, "window": window, "generated_at": NOW,
            "sources_available": {"polestar_alarm": {"label": "폴스타 알람", "available": True}},
            "rows": rows, "hidden_row_count": 0, "truncated": False, "notices": []}


_ROWS6 = [_row("2026-11-04T23:10:00Z", "A"), _row("2026-11-04T22:05:00Z", "B"),
          _row("2026-11-04T20:00:00Z", "C")]
_HELPERS = r"""
clock = Date.parse(NOW);
Date.now = function () { return clock; };
var scrolls = [];
El.prototype.scrollIntoView = function () { scrolls.push(this.textContent); };
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
function focused() {
    return all(slot("detailPanel"), function (n) {
        return n.getAttribute("data-bm-event") !== null &&
        n.className.indexOf("bm-row--focus") >= 0; }).map(function (n) { return n.textContent; });
}
function events() { return take().filter(function (c) { return c.indexOf("/events") >= 0; }); }
"""


def _mount(script: str, bodies: dict[str, Any] | None = None) -> Any:
    merged = {**_BODIES, "/1/events?window=1h": _events("1h", []),
              "/1/events?window=6h": _events("6h", _ROWS6), **(bodies or {})}
    return _node(_FAKE_DOM + _HELPERS + script, SLOTS=_slots(), BODIES=merged, NOW=NOW)


# ─── 1. 정적 ────────────────────────────────────────────────────────────


def test_static_no_html_injection_and_cache_versions() -> None:
    src = _JS.read_text(encoding="utf-8")
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "confirm(", "window.open"):
        assert banned not in src, banned
    assert src.count('"_blank"') == 1  # 새 탭은 [정의에서 편집] 하나뿐(시점 지정이 더하지 않는다)
    index = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/biz-monitor.js?v=3"></script>' in index
    assert '<script src="/static/js/app.js?v=56"></script>' in index


# ─── 2. 순수 함수 ─────────────────────────────────────────────────────────


def test_pure_parse_window_nearest_iso() -> None:
    out = _node(
        """
var now = Date.parse(NOW);
out = {
  ok: P.parseAt("2026-11-04T23:00:00Z", now), okMs: P.parseAt("2026-11-04T23:00:00.250Z", now),
  okMin: P.parseAt("2026-11-04T23:00Z", now),
  bad: ["", null, "2026-11-04 23:00:00Z", "2026-11-04T23:00:00", "2026-11-04T23:00:00+09:00",
        "2026-02-30T00:00:00Z", "2026-11-04T24:00:00Z", "2026-11-04T23:00:61Z", "abc",
        "2026-11-05T01:06:00Z", "2026-10-29T00:59:00Z"].map(function (v) {
      return P.parseAt(v, now); }),
  skew: P.parseAt("2026-11-05T01:04:00Z", now) !== null,
  edge7d: P.parseAt("2026-10-29T01:00:00Z", now) !== null,
  w: [P.atWindow(now - 30 * 60000, now, "1h"), P.atWindow(now - 3 * 3600000, now, "1h"),
      P.atWindow(now - 3 * 3600000, now, "24h"), P.atWindow(now - 10 * 3600000, now, "6h"),
      P.atWindow(now - 3 * 86400000, now, "1h"), P.atWindow(now + 60000, now, "1h")],
  near: P.nearestEvent(ROWS, Date.parse("2026-11-04T22:30:00Z")).summary,
  nearNone: P.nearestEvent([], now),
  iso: [P.atIso(Date.parse("2026-11-04T23:00:00Z")),
        P.atIso(Date.parse("2026-11-04T23:00:00.250Z"))],
};
""",
        NOW=NOW, ROWS=_ROWS6,
    )
    assert out["ok"] == out["okMin"] and out["okMs"] == out["ok"] + 250
    assert out["bad"] == [None] * 11  # 형식 오류 · 없는 날짜 · 미래 · 7일 넘은 과거
    assert out["skew"] is True and out["edge7d"] is True
    assert out["w"] == ["1h", "6h", "24h", "24h", "24h", "1h"]
    assert out["near"] == "B" and out["nearNone"] is None
    assert out["iso"] == ["2026-11-04T23:00:00Z", "2026-11-04T23:00:00.250Z"]


# ─── 3. mount ─────────────────────────────────────────────────────────────


def test_restore_opens_smallest_window_focuses_nearest_and_scrolls_once() -> None:
    out = _mount(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events",
    at: "2026-11-04T22:00:00Z", onRoute: function (r) { routes.push(r); }});
await flush();
var first = events();
var f1 = focused(), s1 = scrolls.slice();
var pressed = all(slot("detailPanel"), attr("data-bm-window", "6h"))[0]
    .getAttribute("aria-pressed");
await advance(60000);
var again = events();
out = {first: first, f1: f1, s1: s1, pressed: pressed, again: again, f2: focused(),
       scrolls: scrolls.length, route: routes[routes.length - 1], route0: routes[0],
       text: slot("detailPanel").textContent, flash: slot("flash").textContent};
""",
    )
    assert out["first"] == [f"GET {BASE}/1/events?window=6h"]  # 3시간 전 → 1h 밖 → 6h
    assert out["pressed"] == "true"
    assert len(out["f1"]) == 1 and "B" in out["f1"][0]  # 22:05 행 하나만 강조
    assert out["s1"] == out["f1"]  # 그 행으로 스크롤
    assert out["again"] == [f"GET {BASE}/1/events?window=6h"]  # 60초 자동 갱신
    assert out["f2"] == out["f1"] and out["scrolls"] == 1  # 다시 그려도 강조만 · 스크롤 1회
    want = {"service": "1", "tab": "events", "at": "2026-11-04T22:00:00Z"}
    assert out["route0"] == want and out["route"] == want


def test_invalid_at_ignored_silently_like_before() -> None:
    for bad in ("2026-11-04T22:00:00", "2026-11-06T01:00:00Z", "2026-10-20T00:00:00Z", "x"):
        out = _mount(
            """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events", at: BAD,
    onRoute: function (r) { routes.push(r); }});
await flush();
out = {first: events(), focused: focused(), scrolls: scrolls.length,
       route: routes[routes.length - 1], flash: slot("flash").textContent};
""".replace("BAD", repr(bad)),
        )
        assert out["first"] == [f"GET {BASE}/1/events?window=1h"], bad  # 기본 기간 그대로
        assert out["focused"] == [] and out["scrolls"] == 0
        assert out["route"] == {"service": "1", "tab": "events"}  # 주소에서 at을 지운다
        assert out["flash"] == ""  # 사유 문구 0


def test_at_only_for_events_tab() -> None:
    out = _mount(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "overview",
    at: "2026-11-04T22:00:00Z", onRoute: function (r) { routes.push(r); }});
await flush();
out = {route: routes[routes.length - 1], ev: events()};
""",
    )
    assert out["route"] == {"service": "1", "tab": "overview"} and out["ev"] == []


def test_window_change_or_leaving_tab_drops_at() -> None:
    out = _mount(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events",
    at: "2026-11-04T22:00:00Z", onRoute: function (r) { routes.push(r); }});
await flush();
take();
button(slot("detailPanel"), "1h").fire("click");
await flush();
var afterWindow = {route: routes[routes.length - 1], focused: focused()};
var h2 = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events",
    at: "2026-11-04T22:00:00Z", onRoute: function (r) { routes.push(r); }});
await flush();
all(slot("detailTabs"), attr("data-bm-tab", "overview"))[0].fire("click");
await flush();
var afterLeave = routes[routes.length - 1];
all(slot("detailTabs"), attr("data-bm-tab", "events"))[0].fire("click");
await flush();
out = {afterWindow: afterWindow, afterLeave: afterLeave, back: routes[routes.length - 1],
       focusedBack: focused()};
""",
    )
    assert out["afterWindow"] == {"route": {"service": "1", "tab": "events"}, "focused": []}
    assert out["afterLeave"] == {"service": "1", "tab": "overview"}
    assert out["back"] == {"service": "1", "tab": "events"} and out["focusedBack"] == []


def test_paused_mount_with_at_makes_no_request() -> None:
    out = _mount(
        """
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events",
    at: "2026-11-04T22:00:00Z"});
await flush();
h.pause();
take();
await advance(180000);
var paused = take();
h.resume();
await flush();
out = {paused: paused, resumed: events()};
""",
    )
    assert out["paused"] == []
    assert out["resumed"] == [f"GET {BASE}/1/events?window=6h"]


# ─── 4. app.js 주소 동기화 ────────────────────────────────────────────────


def test_app_sync_biz_params_writes_and_clears_at() -> None:
    fn = _func(_APP.read_text(encoding="utf-8"), "syncBizParams")
    out = _run(
        "var search = '?view=biz&service=1&tab=events&at=2026-11-04T22%3A00%3A00Z';\n"
        "global.window = {location: {pathname: '/', hash: '', get search() { return search; }}};\n"
        "global.history = {replaceState: function (a, b, url) { search = url.slice(1); }};\n"
        "var bizEntry = {service: '1', tab: 'events', at: '2026-11-04T22:00:00Z'};\n"
        "var bizHandle = null;\n"
        + fn
        + "\nsyncBizParams(true); var pre = search;\n"
        "bizHandle = {route: function () {\n"
        " return {service: '1', tab: 'events', at: '2026-11-04T23:00:00Z'}; }};\n"
        "syncBizParams(true); var withAt = search;\n"
        "bizHandle = {route: function () { return {service: '1', tab: 'events'}; }};\n"
        "syncBizParams(true); var dropped = search;\n"
        "bizHandle = {route: function () { return {service: '1', tab: 'overview', at: 'x'}; }};\n"
        "syncBizParams(true); var other = search;\n"
        "bizHandle = {route: function () {\n"
        " return {service: '1', tab: 'events', at: '2026-11-04T23:00:00Z'}; }};\n"
        "syncBizParams(true); syncBizParams(false); var left = search;\n"
        "process.stdout.write(JSON.stringify({pre: pre, withAt: withAt, dropped: dropped,"
        " other: other, left: left}));"
    )
    assert "at=2026-11-04T22%3A00%3A00Z" in out["pre"]  # mount 전에는 주소 값을 그대로 둔다
    assert "tab=events" in out["withAt"] and "at=2026-11-04T23%3A00%3A00Z" in out["withAt"]
    assert "at=" not in out["dropped"] and "at=" not in out["other"]
    assert "view=biz" not in out["left"] and "at=" not in out["left"]


def test_app_mount_passes_at_option() -> None:
    src = _APP.read_text(encoding="utf-8")
    assert 'at: p.get("at")' in src
    mount = src[src.index("window.BizMonitor.mount(bizView, {"):]
    mount = mount[: mount.index("});")]
    assert "at: bizEntry.wanted ? bizEntry.at : null," in mount
