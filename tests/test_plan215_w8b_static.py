"""plans/215 W8b — 「이벤트」 탭 · 관제 mount 옵션 `openAlarm` · 같은 창 전환(정적 + node).

세 갈래(브라우저 없이 · 망·LLM 호출 0):
    1. 정적 — noise.js `openAlarm`은 인자가 있을 때만 돈다(없으면 종전) ·
       app.js 관제·업무 현황 배선 ·
       새 창 0.
    2. node 조각 실행 — noise.js `openRequestedAlarm`·`resumeWithAlarm` ·
       app.js `openAlarmInNoise`·`takeNoiseAlarm`·
       `syncNoiseViewParam`을 원문에서 잘라 가짜 환경에서 돌린다(수용 23 — 같은 창 관제 탭 서랍).
    3. biz-monitor mount 상태기계 — 「이벤트」 탭 요청·60초 자동·pause 0·행 동작·
       억제 포함(관리자만)·
       변경 칩 없음(수용 25 화면 쪽).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from tests.test_plan215_w8_static import _BODIES, _FAKE_DOM, _node, _node_bin, _slots

_STATIC = Path(__file__).resolve().parents[1] / "src" / "static"
_NOISE = _STATIC / "js" / "noise.js"
_APP = _STATIC / "js" / "app.js"
_JS = _STATIC / "js" / "biz-monitor.js"


def _func(src: str, name: str) -> str:
    """원문에서 `function name(...) { ... }` 한 덩어리를 잘라 낸다(중괄호 짝 맞춤)."""
    start = src.index(f"function {name}(")
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(name)


def _run(script: str) -> Any:
    done = subprocess.run(
        [_node_bin(), "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ─── 1. 정적 ────────────────────────────────────────────────────────────


def test_noise_open_alarm_runs_only_with_option() -> None:
    src = _NOISE.read_text(encoding="utf-8")
    assert src.count("opts.openAlarm") == 5  # 값 읽기 3 + mount 가드 1 + 핸들 선택 1
    assert src.count("openRequestedAlarm(") == 3  # 정의 1 + mount 가드 1 + resumeWithAlarm 1
    assert "    if (opts.openAlarm) openRequestedAlarm();" in src
    # 핸들 resume은 인자가 함수일 때만 감싼다 — 없으면 종전 resume 그대로(plans/193 하니스 무변경)
    handle = 'var resumeHandle = typeof opts.openAlarm === "function" ? resumeWithAlarm : resume;'
    assert "    " + handle in src
    assert "    return { pause: pause, resume: resumeHandle, unmount: unmount };" in src
    assert "openRequestedAlarm" not in _func(src, "resume")
    body = _func(src, "openRequestedAlarm")
    assert "openDrawer(" in body and "window.open" not in body


def test_app_wires_same_window_noise_drawer_and_diagnosis() -> None:
    src = _APP.read_text(encoding="utf-8")
    assert 'alarm: new URLSearchParams(window.location.search).get("alarm")' in src
    assert "openAlarm: takeNoiseAlarm," in src
    assert "onOpenAlarm: openAlarmInNoise," in src
    assert "onOpenInvestigation: openInvestigationEntry," in src
    for name in ("openAlarmInNoise", "takeNoiseAlarm", "syncNoiseViewParam"):
        body = _func(src, name)
        for banned in ("window.open", "_blank", "innerHTML", "confirm("):
            assert banned not in body, f"{name}: {banned}"
    assert 'setActiveView("noise")' in _func(src, "openAlarmInNoise")


def test_events_tab_has_no_html_injection_or_new_window() -> None:
    src = _JS.read_text(encoding="utf-8")
    seg = src[src.index("// ─── 「이벤트」 보기 탭") : src.index("// ─── mount ───")]
    for banned in ("innerHTML", "confirm(", "window.open", "_blank", "target"):
        assert banned not in seg, banned
    assert "상관 ≠ 인과" in src
    assert '{ id: "events", label: "이벤트"' in src
    assert "events: { load: loadEvents }" in src


# ─── 2. node 조각 실행 ────────────────────────────────────────────────────


def test_noise_open_requested_alarm_fragment() -> None:
    fn = _func(_NOISE.read_text(encoding="utf-8"), "openRequestedAlarm")
    out = _run(
        "var calls = [];\n"
        "function openDrawer(id, trigger) { calls.push([id, trigger]); }\n"
        "function run(opts) { calls = [];\n"
        + fn
        + "\n if (opts.openAlarm) openRequestedAlarm();\n return calls; }\n"
        "var pending = 'a9';\n"
        "var out = {none: run({}), str: run({openAlarm: 'a1'}),"
        " fnOnce: run({openAlarm: function () { var v = pending; pending = null; return v; }}),"
        " fnEmpty: run({openAlarm: function () { return null; }})};\n"
        "process.stdout.write(JSON.stringify(out));"
    )
    assert out == {"none": [], "str": [["a1", None]], "fnOnce": [["a9", None]], "fnEmpty": []}


def test_noise_resume_with_alarm_fragment() -> None:
    src = _NOISE.read_text(encoding="utf-8")
    fns = _func(src, "resumeWithAlarm")
    out = _run(
        "var life = {paused: true, dead: false}, opened = 0, resumed = 0;\n"
        "function resume() { if (!life.paused || life.dead) return;\n"
        " life.paused = false; resumed++; }\n"
        "function openRequestedAlarm() { opened++; }\n"
        + fns
        + "\nresumeWithAlarm(); var first = [resumed, opened];\n"
        "resumeWithAlarm(); var again = [resumed, opened];\n"  # 이미 보이는 중 → 서랍 재시도 0
        "life.paused = true; life.dead = true; resumeWithAlarm(); var dead = [resumed, opened];\n"
        "process.stdout.write(JSON.stringify({first: first, again: again, dead: dead}));"
    )
    assert out == {"first": [1, 1], "again": [1, 1], "dead": [1, 1]}


def test_app_open_alarm_switches_tab_in_same_window_and_url() -> None:
    src = _APP.read_text(encoding="utf-8")
    names = ("openAlarmInNoise", "takeNoiseAlarm", "syncNoiseViewParam")
    parts = "\n".join(_func(src, n) for n in names)
    out = _run(
        "var search = '?view=biz&service=1&tab=events';\n"
        "global.window = {location: {pathname: '/', hash: '', get search() { return search; }},"
        " open: function () { throw new Error('새 창'); }};\n"
        "global.history = {replaceState: function (a, b, url) { search = url.slice(1); }};\n"
        "var noiseTabEnabled = true, noiseView = {}, activeView = 'biz';\n"
        "var pendingNoiseAlarm = null, opened = [];\n"
        "var handleMounted = true;\n"
        "function setActiveView(v) {\n"
        "  if (activeView === 'noise' && v !== 'noise') syncNoiseViewParam(false);\n"
        "  activeView = v; if (v === 'noise') { syncNoiseViewParam(true);\n"
        "  var id = takeNoiseAlarm(); if (id) opened.push(id); } }\n"
        + parts
        + "\nvar ok = openAlarmInNoise('a1');\n"
        "var afterOpen = {view: activeView, search: search, opened: opened.slice(),"
        " pending: pendingNoiseAlarm};\n"
        "setActiveView('chat');\n"
        "var afterLeave = search;\n"
        "noiseTabEnabled = false; activeView = 'biz';\n"
        "var refused = openAlarmInNoise('a2');\n"
        "process.stdout.write(JSON.stringify({ok: ok, afterOpen: afterOpen, afterLeave: afterLeave,"
        " refused: refused, view: activeView}));"
    )
    assert out["ok"] is True
    assert out["afterOpen"]["view"] == "noise"
    assert out["afterOpen"]["opened"] == ["a1"] and out["afterOpen"]["pending"] is None
    assert "view=noise" in out["afterOpen"]["search"] and "alarm=a1" in out["afterOpen"]["search"]
    assert "alarm=" not in out["afterLeave"] and "view=noise" not in out["afterLeave"]
    assert out["refused"] is False and out["view"] == "biz"  # 관제 탭 꺼짐 → 옮기지 않는다


# ─── 3. biz-monitor 「이벤트」 탭 mount ─────────────────────────────────────

_EVENTS = {
    "generated_at": "2026-11-05T01:00:00Z",
    "service_id": 1,
    "window": "1h",
    "sources_available": {
        "polestar_alarm": {"label": "폴스타 알람", "available": True, "reason": ""},
        "apm_event": {"label": "제니퍼 이벤트", "available": True, "reason": ""},
        "state_change": {"label": "서비스 상태 전이", "available": True, "reason": ""},
        "investigation": {"label": "조사", "available": True, "reason": ""},
        "change": {"label": "변경 이력", "available": False, "reason": "변경 이력 꺼짐"},
    },
    "rows": [
        {"at": "2026-11-05T00:59:00Z", "source": "polestar_alarm", "source_label": "폴스타 알람",
         "member_key": "pg|101", "member_name": "web-01", "summary": "CPU 높음",
         "severity": "critical", "severity_label": "심각", "handling": "delivered",
         "handling_label": "전달", "count": 4, "alarm_id": "a1"},
        {"at": "2026-11-05T00:58:00Z", "source": "investigation", "source_label": "조사",
         "member_key": "pg|101", "member_name": "web-01", "summary": "조사 완료 · 알람 조사",
         "severity": None, "severity_label": "", "handling": None, "handling_label": "",
         "count": 1, "investigation_id": "inv-1"},
        {"at": "2026-11-05T00:50:00Z", "source": "state_change", "source_label": "서비스 상태 전이",
         "member_key": "", "member_name": "", "summary": "이상 신호 없음 → 위험",
         "severity": "critical", "severity_label": "심각", "handling": None, "handling_label": "",
         "count": 1},
    ],
    "truncated": False,
    "hidden_member_count": 1,
    "hidden_row_count": 2,
    "notices": [],
    "anomaly_start": None,
    "pinned_note": "",
}
_CHANGE_BODY = {
    **_EVENTS,
    "sources_available": {
        **_EVENTS["sources_available"],
        "change": {"label": "변경 이력", "available": True, "reason": ""},
    },
    "rows": [
        {"at": "2026-11-05T00:40:00Z", "source": "change", "source_label": "변경 이력",
         "member_key": "pg|101", "member_name": "web-01", "summary": "patch",
         "severity": None, "handling": None, "count": 1, "pinned": True,
         "change": {"change_type": "patch", "resource_type": "server", "description": "커널"}},
    ],
    "anomaly_start": {"at": "2026-11-05T00:50:00Z", "basis": "state_log"},
    "pinned_note": (
        "이상 시작 전 60분 안 변경 — 시간이 가까울 뿐 원인이라는 뜻은 아닙니다(상관 ≠ 인과)"
    ),
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
function events() { return take().filter(function (c) { return c.indexOf("/events") >= 0; }); }
"""


def _mount_events(script: str, bodies: dict[str, Any]) -> Any:
    return _node(_FAKE_DOM + _HELPERS + script, SLOTS=_slots(), BODIES={**_BODIES, **bodies})


def test_events_tab_requests_rows_actions_and_60s_auto() -> None:
    out = _mount_events(
        """
var alarms = [], invs = [];
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events",
    isAdmin: function () { return false; },
    onOpenAlarm: function (id) { alarms.push(id); return true; },
    onOpenInvestigation: function (id) { invs.push(id); return true; }});
await flush();
var first = events();
var panel = slot("detailPanel");
var text = panel.textContent;
var chips = all(panel, function (n) { return n.getAttribute("data-bm-source") !== null; })
    .map(function (n) { return n.getAttribute("data-bm-source"); });
var include = all(panel, attr("data-bm-include-suppressed"));
button(panel, "관제에서 보기").fire("click");
button(panel, "장애 진단에서 보기").fire("click");
await advance(30000);
var tick30 = events();
await advance(30000);
var tick60 = events();
h.pause();
await advance(180000);
var paused = take();
h.resume();
await flush();
take();
button(panel, "새로고침").fire("click");
await flush();
var manual = events();
button(panel, "6h").fire("click");
await flush();
var six = events();
all(panel, attr("data-bm-source", "state_change"))[0].fire("click");
var rowsAfterChip = all(panel, function (n) { return n.getAttribute("data-bm-event") !== null; })
    .map(function (n) { return n.getAttribute("data-bm-event"); });
out = {first: first, text: text, chips: chips, include: include.length, alarms: alarms,
       invs: invs, tick30: tick30, tick60: tick60, paused: paused, manual: manual, six: six,
       rowsAfterChip: rowsAfterChip};
""",
        {
            "/1/events?window=1h": _EVENTS,
            "/1/events?window=6h": {**_EVENTS, "window": "6h"},
        },
    )
    ev = "GET /api/v1/biz-services/monitor/1/events?window="
    assert out["first"] == [ev + "1h"]
    assert "CPU 높음 ×4" in out["text"]  # 같은 분 같은 구성원 같은 알람 4건 → 1행 ×4
    assert "권한 밖 구성원 행 2건" in out["text"]
    assert out["chips"] == ["polestar_alarm", "apm_event", "state_change", "investigation"]
    assert out["include"] == 0  # 사용자에게 「억제 포함」 없음
    assert out["alarms"] == ["a1"] and out["invs"] == ["inv-1"]  # 같은 창 콜백
    assert out["tick30"] == []  # 30초 상세 갱신에는 다시 읽지 않는다
    assert out["tick60"] == [ev + "1h"]  # 60초 자동
    assert out["paused"] == []  # pause = 요청 0
    assert out["manual"] == [ev + "1h"]
    assert out["six"] == [ev + "6h"]
    assert out["rowsAfterChip"] == ["polestar_alarm", "investigation"]  # 칩은 요청 없이 거른다


def test_events_admin_include_suppressed_and_pinned_change_expand() -> None:
    out = _mount_events(
        """
var h = BM.mount(rootHost, {fetch: fakeFetch, service: "1", tab: "events",
    isAdmin: function () { return true; }});
await flush();
take();
var panel = slot("detailPanel");
var box = all(panel, attr("data-bm-include-suppressed"))[0];
box.checked = true;
box.fire("change");
await flush();
var withSup = events();
var text = panel.textContent;
button(panel, "펼치기").fire("click");
var detail = all(panel, function (n) { return n.getAttribute("data-bm-event-detail") !== null; })
    .map(function (n) { return n.textContent; });
out = {withSup: withSup, text: text, detail: detail,
       chips: all(panel, attr("data-bm-source")).length};
""",
        {
            "/1/events?window=1h": _EVENTS,
            "/1/events?window=1h&include_suppressed=true": _CHANGE_BODY,
        },
    )
    assert out["withSup"] == [
        "GET /api/v1/biz-services/monitor/1/events?window=1h&include_suppressed=true"
    ]
    assert "장애 직전 변경" in out["text"] and "상관 ≠ 인과" in out["text"]
    assert out["detail"] == ["server · patch — 커널"]
    assert out["chips"] == 5  # 변경 이력이 켜져 있으면 칩이 생긴다


def test_events_pure_helpers() -> None:
    out = _node(
        """
out = {url: P.eventsUrl("1", {window: "zz"}),
  urlSup: P.eventsUrl("1", {window: "24h", includeSuppressed: true}),
       rows: P.filterEventRows(R, {off: {state_change: true}, severity: "critical", member: ""})
           .map(function (r) { return r.source; }),
       members: P.eventMembers(R),
       load: typeof BM.events.load};
""",
        R=_EVENTS["rows"],
    )
    assert out["url"] == "/api/v1/biz-services/monitor/1/events?window=1h"
    assert out["urlSup"].endswith("/1/events?window=24h&include_suppressed=true")
    assert out["rows"] == ["polestar_alarm"]
    assert out["members"] == [{"value": "pg|101", "label": "web-01"}]
    assert out["load"] == "function"


def test_events_tab_text_has_no_normal_word() -> None:
    src = _JS.read_text(encoding="utf-8")
    seg = src[src.index("// ─── 「이벤트」 보기 탭") : src.index("// ─── mount ───")]
    assert not re.search(r"정상", seg)
