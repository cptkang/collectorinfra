"""plans/187 W18 — 장애 진단 탭 「이벤트 찾아 조사」(시간·알람명·서버로 판정 기록을 찾아 바로 조사).

W15 가짜 DOM 하네스(`_node`)로 실제 렌더·클릭을 돈다:
- 칸은 `/active`의 `event_search: true` + 접수 함수 주입일 때만 보인다(꺼짐 = 종전 화면)
- 검색 쿼리(시간 구간·부분일치 칸) · 결과 행 · [조사 시작] → 접수 → 그 조사 하위 탭이 열린다
- 접수 실패·거부·합류 사유가 그 행에 보인다 · 진단 화면 파일에는 여전히 쓰기 요청이 없다
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_DV_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "diagnosis-view.js"

_ITEMS = """[
  { alarm_id: "a1", ts: "2026-10-21T01:02:03+00:00", alarm_name: "CPU 사용률 임계 초과",
    server_name: "WEB-01", tier: "page", stage_label: "통과" },
  { ts: "2026-10-21T00:00:00+00:00", alarm_name: "id 없음", server_name: "WEB-02",
    tier: "suppress" } ]"""


def _make(event_search: bool, start: bool = True) -> str:
    start_opt = (
        "startInvestigation: function (id) { started.push(id); return Promise.resolve(startRes); },"
        if start else ""
    )
    return f"""
var started = [];
var startRes = {{ ok: true, status: 200, body: {{ investigation_id: "inv-9", status: "queued",
    joined: false }} }};
routes[API + "/active"] = {{ status: 200,
    body: {{ items: [], event_search: {str(event_search).lower()} }} }};
routes[API + "/inv-9"] = {{ status: 200, body: running("inv-9") }};
routes["/api/v1/noise/decisions"] = {{ status: 200, body: {{ items: {_ITEMS}, total: 7 }} }};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }}, onActivate: function () {{}},
    analyzeFor: function () {{ return null; }}, {start_opt}
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
var box = findAll(rootEl, "dv-search")[0];
"""


def test_hidden_when_manual_off_or_no_starter() -> None:
    out = _node(_make(False) + "out = { hidden: box.hidden };")
    assert out == {"hidden": True}
    out = _node(_make(True, start=False) + "out = { hidden: box.hidden };")
    assert out == {"hidden": True}


def test_search_renders_rows_and_start_opens_subtab() -> None:
    out = _node(
        _make(True)
        + """
findAll(rootEl, "dv-search-alarm")[0].value = " CPU ";
findAll(rootEl, "dv-search-server")[0].value = "web";
findAll(rootEl, "dv-search-btn")[0].click();
await flush();
var url = calls.filter(function (u) { return u.indexOf("/noise/decisions") >= 0; })[0];
var rows = findAll(rootEl, "dv-search-row");
var btns = findAll(rootEl, "dv-start-btn");
var before = { status: texts(rootEl, "dv-search-status")[0], rows: rows.length,
    btns: btns.length, noId: texts(rows[1], "dv-search-note")[0],
    first: rows[0].textContent };
btns[0].click();
await flush();
out = { hidden: box.hidden, url: url, before: before, started: started,
    tabs: ctl.state().tabs, current: ctl.state().current,
    alarmId: ctl.tab("inv-9") && ctl.tab("inv-9").alarmId };
"""
    )
    assert out["hidden"] is False
    assert out["url"] == (
        "/api/v1/noise/decisions?range=24h&alarm_q=CPU&server_q=web&size=30"
    )
    assert out["before"]["status"] == "판정 기록 7건 중 최근 2건"
    assert out["before"]["rows"] == 2 and out["before"]["btns"] == 1
    first = out["before"]["first"]
    assert "CPU 사용률 임계 초과" in first and "즉시 통보" in first
    assert out["before"]["noId"] == "알람 id가 없는 판정 기록이라 조사할 수 없습니다."
    assert out["started"] == ["a1"]
    assert out["tabs"] == ["inv-9"] and out["current"] == "inv-9" and out["alarmId"] == "a1"


def test_start_failure_rejected_and_joined_messages() -> None:
    out = _node(
        _make(True)
        + """
findAll(rootEl, "dv-search-btn")[0].click();
await flush();
var btn = findAll(rootEl, "dv-start-btn")[0];
var note = findAll(rootEl, "dv-search-note")[0];
var seen = [];
startRes = { ok: false, status: 403, body: { detail: { code: "zone_denied",
    reason: "해당 존의 알람에 대한 권한이 없습니다." } } };
btn.click(); await flush(); seen.push(note.textContent);
startRes = { ok: true, status: 200, body: { investigation_id: "inv-x", status: "rejected",
    reason: "상한 초과" } };
btn.click(); await flush(); seen.push(note.textContent);
startRes = { ok: true, status: 200, body: { investigation_id: "inv-9", status: "running",
    joined: true } };
btn.click(); await flush(); seen.push(note.textContent);
out = { seen: seen, tabs: ctl.state().tabs, disabled: btn.disabled, label: btn.textContent };
"""
    )
    assert out["seen"] == [
        "조사를 시작하지 못했습니다 — 해당 존의 알람에 대한 권한이 없습니다.",
        "조사 서비스가 거부했습니다 — 상한 초과",
        "같은 알람의 진행 중인 조사에 합류했습니다.",
    ]
    assert out["tabs"] == ["inv-9"]  # 거부·실패는 탭을 열지 않는다
    assert out["disabled"] is False and out["label"] == "조사 시작"


def test_search_query_pure() -> None:
    out: Any = _node(
        """
out = {
    empty: DV.searchQuery({}),
    range: DV.searchQuery({ since: "2026-10-20T09:00", until: "2026-10-21T09:00" }),
    bad: DV.searchQuery({ since: "2026-10-21T09:00", until: "2026-10-20T09:00" }),
    enc: DV.searchQuery({ alarm: "a&b" }),
};
"""
    )
    assert out["empty"] == "range=24h&size=30"
    assert out["range"].startswith("range=30d&since=2026-10-2") and "&until=" in out["range"]
    assert out["bad"] is None
    assert out["enc"] == "range=24h&alarm_q=a%26b&size=30"


def test_still_no_write_request_in_diagnosis_view() -> None:
    src = _DV_JS.read_text(encoding="utf-8")
    # D-003 — 접수(쓰기)는 app.js가 넘긴 함수로만. 진단 화면 파일 자체는 GET만 한다.
    assert "POST" not in src and "method:" not in src
    assert "opts.startInvestigation(alarmId)" in src
