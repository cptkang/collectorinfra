"""plans/199 W5a — 장애 진단 탭 배치(§3.10 A안 · §3.8 · D-351 ⑥ · G-50 개정).

`diagnosis-view.js`를 node로 실제 실행한다
(W15 하네스 재사용 · `summaryView` 옵션·`localStorage` 주입):
    - 기능 켜짐: 전환 탭 없음 · 진행 중 순서(한눈에 보기 → 조사 흐름 →
      [근거 보기] 중간 결과 → 실행 기록) · 종결 순서(조사 결과 → 조사 흐름 →
      실행 기록) · 진행 중 카드 1회만 · 실행 기록 기본 접힘 · 접힌 머리 건수 ·
      0건 사유(「아직 기록 없음」/「조사 서비스 실행 기록 꺼짐」)·표지 ·
      0건 펼침 시 안내 한 줄만 · 접힌 동안 건수 갱신(스트림 1개 · 자동 스크롤은
      펼쳤을 때만) · 펼침 기억(`dv.expand.*`) · 저장소 차단 시 기본 접힘 ·
      머리 「조사 이유」 1줄 · 기능 조회가 늦게 와도 다음 머리 갱신에서 배치 전환.
    - 기능 꺼짐: 종전 골격(전환 탭 · dv-grid 2칸) 그대로 · 저장소 접근 0.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tests.test_api.test_ui_plan187_w15_diagnosis import _node
from tests.test_api.test_ui_plan187_w20_flow import _RUN_EVENTS, _RUN_PARTIAL, _manual_example

_MAKE = r"""
var lstore = {}; var lsLog = [];
var local = {
    getItem: function (k) { lsLog.push("get:" + k); return k in lstore ? lstore[k] : null; },
    setItem: function (k, v) { lsLog.push("set:" + k + "=" + v); lstore[k] = String(v); },
};
var blocked = {
    getItem: function () { throw new Error("blocked"); },
    setItem: function () { throw new Error("blocked"); },
};
var flag = true;
function makeOn(localDep, summaryOpt) {
    var rootEl = new Node("div"); var tabBtn = new Node("button"); var badge = new Node("span");
    var o = {
        root: rootEl, tabButton: tabBtn, badge: badge, operator: false,
        headers: function () { return {}; },
        onActivate: function () {}, onOpenConversation: function (id) { opened.push(id); },
        analyzeFor: function () { return null; },
        summaryView: function () { return flag; },
        deps: { document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
            clearTimeout: clearT, now: function () { return clock; }, storage: storage,
            localStorage: localDep === undefined ? local : localDep },
    };
    if (summaryOpt === "absent") delete o.summaryView;
    return { ctl: DV.create(o), root: rootEl };
}
function cls(n) { return n.children.map(function (c) { return c.className; }); }
function shown(n) { return n.children.filter(function (c) { return !c.hidden; })
    .map(function (c) { return c.className; }); }
function sec(tab, name) {
    var s = tab.dom.secs[name];
    return { open: !s.body.hidden, title: s.title.textContent, caret: s.caret.textContent,
        info: s.info.textContent, attached: !!s.wrap.parentNode,
        expanded: s.btn.getAttribute("aria-expanded") };
}
function cards(n) { return (n.innerHTML.match(/data-bv-summary="1"/g) || []).length; }
function cardCount(tab) {
    return cards(tab.dom.sumBox) + cards(tab.dom.partBody) + cards(tab.dom.finalBody);
}
async function openRun(v, act, partial) {
    await v.ctl.probe();
    runTimers(1000);
    v.ctl.open("inv-f");
    await flush();
    runTimers(1000);
    // 이 컨트롤러의 스트림(앞 컨트롤러 스트림은 닫지 않는다)
    v.es = liveES()[liveES().length - 1];
    v.es.onopen();
    if (act) v.es.emit("activity", Object.assign({ investigation_id: "inv-f", status: "running",
        next_seq: 99 }, act));
    if (partial) v.es.emit("partial", { investigation_id: "inv-f", partial_briefing: partial });
    return v.ctl.tab("inv-f");
}
routes[API + "/active"] = { status: 200, body: { items: [] } };
"""


def _running_route(
    source: str | None = "live", events: list[dict] | None = None, **extra: Any
) -> str:
    activity: dict[str, Any] = {"next_seq": 0, "truncated": False, "events": events or []}
    if source is not None:
        activity["source"] = source
    body = {
        "status": "running",
        "investigation_id": "inv-f",
        "kind": "alarm",
        "phase": "collecting",
        "activity": activity,
        **extra,
    }
    return _route(body)


def _route(body: dict[str, Any]) -> str:
    data = json.dumps(body, ensure_ascii=False)
    return f'routes[API + "/inv-f"] = {{ status: 200, body: {data} }};\n'


def _run(body: str) -> Any:
    return _node(_MAKE + body)


_EV = json.dumps(_RUN_EVENTS, ensure_ascii=False)
_PART = json.dumps(_RUN_PARTIAL, ensure_ascii=False)


# ─── 진행 중 배치 ──────────────────────────────────────────────────────────


def test_running_layout_has_no_mode_tabs_and_vertical_order() -> None:
    out = _run(
        _running_route()
        + f"""
var v = makeOn();
var tab = await openRun(v, {{ source: "live", events: {_EV} }}, {_PART});
out = {{ panel: cls(tab.dom.panel), shown: shown(tab.dom.panel),
    modes: findAll(v.root, "dv-modes").length + findAll(v.root, "dv-mode").length,
    grid: !!tab.dom.grid.parentNode, flow: sec(tab, "flow"), part: sec(tab, "partial"),
    log: sec(tab, "log"), sumHidden: tab.dom.sumBox.hidden, sumCards: cards(tab.dom.sumBox),
    partCards: cards(tab.dom.partBody),
    partDetail: tab.dom.partBody.innerHTML.indexOf("bv--playbook") >= 0,
    labels: [tab.dom.logLabel.hidden, tab.dom.partLabel.hidden],
    flowCards: findAll(tab.dom.flowBody, "dv-card").length, live: liveES().length }};
"""
    )
    # 전환 탭(dv-modes)·2칸 그리드 없음
    # — 한눈에 보기 → 조사 흐름 → [근거 보기] 중간 결과 → 실행 기록
    assert out["modes"] == 0 and out["grid"] is False
    assert out["panel"] == [
        "dv-tab-head",
        "dv-sum-box bv",
        "dv-sec dv-sec--flow",
        "dv-sec dv-sec--partial",
        "dv-sec dv-sec--log",
        "dv-final",
    ]
    assert out["shown"][:5] == out["panel"][:5]  # 조사 결과 상자만 종결 전 숨김
    # 조사 흐름 펼침 · 중간 결과·실행 기록 기본 접힘
    assert out["flow"]["open"] is True and out["flow"]["title"] == "조사 흐름"
    assert out["flow"]["caret"] == "▾" and out["flow"]["expanded"] == "true"
    assert out["part"] == {
        "open": False,
        "title": "근거 보기",
        "caret": "▸",
        "info": "중간 결과",
        "attached": True,
        "expanded": "false",
    }
    assert out["log"]["open"] is False and out["log"]["caret"] == "▸"
    assert out["log"]["title"].startswith("실행 기록 (") and out["log"]["title"].endswith("건)")
    # 진척형 카드는 맨 위 자기 칸에 1번만 — 접힌 중간 결과 칸은 카드 없는 상세형
    assert out["sumHidden"] is False and out["sumCards"] == 1 and out["partCards"] == 0
    assert out["partDetail"] is True
    # 섹션 머리가 이름을 지므로 안쪽 라벨은 숨긴다
    assert out["labels"] == [True, True]
    assert out["flowCards"] == 4 and out["live"] == 1


def test_running_without_playbook_partial_hides_summary_box() -> None:
    out = _run(
        _running_route()
        + """
var v = makeOn();
var tab = await openRun(v, { source: "live", events: [] }, null);
out = { sumHidden: tab.dom.sumBox.hidden, sum: tab.dom.sumBox.innerHTML,
    part: tab.dom.partBody.textContent };
"""
    )
    assert out["sumHidden"] is True and out["sum"] == ""
    assert out["part"].startswith("도구 결과 0건")


def test_summary_box_takes_shared_bv_font_and_hidden_still_wins() -> None:
    """맨 위 카드 칸도 조사 결과 안 카드처럼 공용 `.bv` 글자 크기·줄 간격을 받는다.

    `.bv`가 `display: flex`라 숨김 규칙(`.dv-sum-box[hidden]` — 선택자 우선순위가 더 높다)이
    있어야 빈 칸이 접힌다(칸 class 자체는 배치 순서 단언이 확인한다).
    """
    css_path = Path(__file__).resolve().parents[2] / "src/static/css/style.css"
    css = css_path.read_text(encoding="utf-8")
    assert re.search(r"\.dv-sum-box\[hidden\][^{]*\{\s*display:\s*none;", css)
    assert re.search(r"^\.bv \{[^}]*font-size: 0\.78rem", css, re.M)


# ─── 실행 기록 접힘 섹션 ─────────────────────────────────────────────────


def test_log_zero_rows_reason_off_when_source_none() -> None:
    """허브 `source: none`(조사 서비스 응답에 activity 키 없음 = 기록 꺼짐)
    → 「조사 서비스 실행 기록 꺼짐」."""
    out = _run(
        _running_route(source="none")
        + """
var v = makeOn();
var tab = await openRun(v, null, null);
var head = sec(tab, "log");
tab.dom.secs.log.btn.click();
out = { head: head, open: sec(tab, "log").open, note: tab.dom.logNote.textContent,
    bar: tab.dom.logBar.hidden, rows: tab.dom.rows.hidden, kids: shown(tab.dom.logBox) };
"""
    )
    assert out["head"]["title"] == "실행 기록 (0건)"
    assert out["head"]["info"] == "조사 서비스 실행 기록 꺼짐"
    # 펼쳐도 0건이면 필터 줄·목록 없이 안내 한 줄만
    assert out["open"] is True and out["bar"] is True and out["rows"] is True
    assert out["kids"] == ["dv-note"]
    assert out["note"] == (
        "조사 서비스의 실행 기록(SRE_ACTIVITY_LOG_ENABLED)이 꺼져 있어 "
        "이 조사의 실행 기록이 쌓이지 않습니다."
    )


def test_log_zero_rows_reason_wait_before_first_frame_and_when_live() -> None:
    out = _run(
        _running_route(source="live")
        + """
var v = makeOn();
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-f");
var tab = v.ctl.tab("inv-f");
var before = sec(tab, "log").info;           // 첫 프레임 전 — 판정 못 함
var beforeNote = tab.dom.logNote.textContent;
await flush();
out = { before: before, beforeNote: beforeNote, live: sec(tab, "log").info,
    liveNote: tab.dom.logNote.textContent };
"""
    )
    assert out["before"] == "아직 기록 없음" and out["beforeNote"] == "아직 실행 기록이 없습니다."
    assert out["live"] == "아직 기록 없음" and out["liveNote"] == "아직 실행 기록이 없습니다."


def test_log_none_with_poll_error_is_not_called_off() -> None:
    """갱신 실패(`poll_error`)와 겹친 `none`은 꺼짐으로 단정하지 않는다."""
    out = _run(
        _running_route(source="none", poll_error="sre_agent 응답 없음")
        + """
var v = makeOn();
var tab = await openRun(v, null, null);
out = { info: sec(tab, "log").info, warn: tab.dom.warn.textContent };
"""
    )
    assert out["info"] == "아직 기록 없음"
    assert "갱신 실패" in out["warn"]


def test_log_head_follows_poll_error_from_fallback_polling_list() -> None:
    """15초 강등 조회: `/active` 항목의 `poll_error`만 바뀌고 조사 조회(`/inv-f`)가 실패해도
    실행 기록 머리가 「꺼짐」에 머물지 않는다(머리 갱신마다 판정을 다시 칠한다)."""
    out = _run(
        _running_route(source="none")
        + """
var v = makeOn();
var tab = await openRun(v, null, null);
var before = sec(tab, "log").info;
routes[API + "/active"] = { status: 200, body: { items: [{ investigation_id: "inv-f",
    status: "running", kind: "alarm", poll_error: "sre_agent 응답 없음" }] } };
routes[API + "/inv-f"] = { status: 503, body: { detail: "busy" } };
v.es.onerror(); v.es.onerror(); v.es.onerror();
await flush();
out = { before: before, after: sec(tab, "log").info, polling: v.ctl.state().polling,
    err: tab.pollError, loadErr: tab.error };
"""
    )
    assert out["before"] == "조사 서비스 실행 기록 꺼짐"
    assert out["polling"] is True and out["err"] and out["loadErr"]
    assert out["after"] == "아직 기록 없음"


def test_collapsed_log_keeps_merging_and_count_one_stream_scroll_only_when_open() -> None:
    out = _run(
        _running_route()
        + f"""
var v = makeOn();
var tab = await openRun(v, {{ source: "live", events: [] }}, null);
var made = esList.length;
var evs = {_EV};
v.es.emit("activity", {{ investigation_id: "inv-f", status: "running", next_seq: 2,
    source: "live", events: evs.slice(0, 2) }});
var t1 = sec(tab, "log").title;
var rows1 = findAll(tab.dom.rows, "dv-row").length;
tab.dom.rows.scrollTop = 50;
v.es.emit("activity", {{ investigation_id: "inv-f", status: "running", next_seq: 99,
    truncated: true, source: "live", events: evs.slice(2) }});
var t2 = sec(tab, "log");
var keep = tab.dom.rows.scrollTop;
tab.dom.secs.log.btn.click();
out = {{ t1: t1, rows1: rows1, t2: t2, keep: keep, afterOpen: tab.dom.rows.scrollTop,
    open: sec(tab, "log").open, bar: tab.dom.logBar.hidden, list: tab.dom.rows.hidden,
    note: tab.dom.logNote.textContent, made: made, after: esList.length, live: liveES().length,
    total: tab.log.rows.length }};
"""
    )
    # 접혀 있어도 행 병합·건수 갱신은 계속 — 스트림은 그대로 1개
    assert out["t1"] == "실행 기록 (2건)" and out["rows1"] == 2
    assert out["t2"]["title"] == f"실행 기록 ({out['total']}건)" and out["t2"]["open"] is False
    assert out["t2"]["info"] == "오래된 기록 일부 빠짐"
    assert out["made"] == out["after"] and out["live"] == 1
    # 자동 스크롤은 펼쳤을 때만 — 접힌 동안 갱신은 위치를 건드리지 않고, 펼치면 최신(맨 위)으로
    assert out["keep"] == 50 and out["afterOpen"] == 0
    assert out["open"] is True and out["bar"] is False and out["list"] is False
    assert out["note"] == "오래된 기록 일부는 버퍼 상한으로 빠졌습니다."


# ─── 펼침 기억 ──────────────────────────────────────────────────────────


def test_expand_state_is_remembered_in_local_storage_and_survives_rerender() -> None:
    out = _run(
        _running_route()
        + f"""
var v = makeOn();
var tab = await openRun(v, {{ source: "live", events: {_EV} }}, {_PART});
tab.dom.secs.log.btn.click();
tab.dom.secs.partial.btn.click();
v.es.emit("partial", {{ investigation_id: "inv-f", partial_briefing: {_PART} }});
v.es.emit("activity", {{ investigation_id: "inv-f", status: "running", next_seq: 120,
    source: "live", events: [] }});
var kept = [sec(tab, "log").open, sec(tab, "partial").open];
var saved = JSON.parse(JSON.stringify(lstore));
v.ctl.close("inv-f");
var v2 = makeOn();
await v2.ctl.probe();
v2.ctl.open("inv-f");
var tab2 = v2.ctl.tab("inv-f");
var reopened = [sec(tab2, "log").open, sec(tab2, "partial").open];
tab2.dom.secs.log.btn.click();
out = {{ kept: kept, saved: saved, reopened: reopened, after: lstore["dv.expand.log"],
    sets: lsLog.filter(function (x) {{ return x.indexOf("set:") === 0; }}) }};
"""
    )
    assert out["kept"] == [True, True]  # 재렌더(partial·activity)에도 펼침 유지
    assert out["saved"] == {"dv.expand.log": "1", "dv.expand.partial": "1"}
    assert out["reopened"] == [True, True]  # 다른 화면에서도 기억한 펼침으로 시작
    assert out["after"] == "0"
    assert out["sets"] == ["set:dv.expand.log=1", "set:dv.expand.partial=1", "set:dv.expand.log=0"]


def test_flow_section_is_not_persisted() -> None:
    out = _run(
        _running_route()
        + """
var v = makeOn();
var tab = await openRun(v, { source: "live", events: [] }, null);
tab.dom.secs.flow.btn.click();
out = { open: sec(tab, "flow").open, keys: Object.keys(lstore) };
"""
    )
    assert out == {"open": False, "keys": []}


def test_blocked_storage_defaults_to_collapsed_and_toggle_still_works() -> None:
    out = _run(
        _running_route()
        + """
var res = {};
for (var name of ["blocked", "null"]) {
    var v = makeOn(name === "blocked" ? blocked : null);
    var tab = await openRun(v, { source: "live", events: [] }, null);
    var before = [sec(tab, "log").open, sec(tab, "partial").open];
    tab.dom.secs.log.btn.click();
    res[name] = { before: before, after: sec(tab, "log").open };
    v.ctl.close("inv-f");
}
out = res;
"""
    )
    for name in ("blocked", "null"):
        assert out[name] == {"before": [False, False], "after": True}


# ─── 종결 배치 ──────────────────────────────────────────────────────────


def _done_route() -> str:
    briefing, events = _manual_example()
    final = {
        "status": "done",
        "investigation_id": "inv-f",
        "kind": "alarm",
        "briefing_lines": [],
        "briefing": briefing,
        "activity": {"next_seq": 99, "truncated": False, "source": "archive", "events": events},
    }
    return _route(final)


def test_done_layout_order_result_then_flow_then_log() -> None:
    out = _run(
        _done_route()
        + """
var v = makeOn();
await v.ctl.probe();
runTimers(1000);
v.ctl.open("inv-f");
await flush();
runTimers(1000);
var tab = v.ctl.tab("inv-f");
out = { done: tab.done, panel: cls(tab.dom.panel), shown: shown(tab.dom.panel),
    flow: sec(tab, "flow"), log: sec(tab, "log"), part: sec(tab, "partial").attached,
    sum: !!tab.dom.sumBox.parentNode, cards: cardCount(tab),
    finalCard: cards(tab.dom.finalBody), modes: findAll(v.root, "dv-mode").length };
"""
    )
    assert out["done"] is True and out["modes"] == 0
    assert out["panel"] == ["dv-tab-head", "dv-final", "dv-sec dv-sec--flow", "dv-sec dv-sec--log"]
    assert out["shown"] == out["panel"]
    assert out["part"] is False and out["sum"] is False
    # 조사 흐름·실행 기록은 종결 뒤 접힘 · 보관 요약 표지
    assert out["flow"]["open"] is False and out["log"]["open"] is False
    assert out["log"]["title"].startswith("실행 기록 (") and out["log"]["info"] == "보관 요약"
    # 「한눈에 보기」는 조사 결과 공용 렌더 안에 1번
    assert out["finalCard"] == 1 and out["cards"] == 1


def test_running_to_done_collapses_flow_unless_user_chose() -> None:
    briefing, events = _manual_example()
    final = {
        "status": "done",
        "investigation_id": "inv-f",
        "kind": "alarm",
        "briefing_lines": [],
        "briefing": briefing,
        "activity": {"next_seq": 99, "truncated": False, "source": "live", "events": []},
    }
    out = _run(
        _running_route()
        + f"""
var res = {{}};
for (var chose of [false, true]) {{
    routes[API + "/inv-f"] = {{ status: 200, body: running("inv-f") }};
    var v = makeOn();
    var tab = await openRun(v, {{ source: "live", events: {_EV} }}, {_PART});
    // 접었다 다시 펼침
    if (chose) {{ tab.dom.secs.flow.btn.click(); tab.dom.secs.flow.btn.click(); }}
    var runOrder = cls(tab.dom.panel);
    routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(final, ensure_ascii=False)} }};
    v.es.emit("done", {{ investigation_id: "inv-f", status: "done" }});
    await flush();
    res[String(chose)] = {{ runOrder: runOrder, doneOrder: cls(tab.dom.panel),
        flow: sec(tab, "flow").open,
        cards: cardCount(tab), sum: !!tab.dom.sumBox.parentNode }};
    v.ctl.close("inv-f");
}}
out = res;
"""
    )
    for chose, flow_open in (("false", False), ("true", True)):
        r = out[chose]
        assert r["runOrder"][1] == "dv-sum-box bv"
        assert r["doneOrder"] == [
            "dv-tab-head",
            "dv-final",
            "dv-sec dv-sec--flow",
            "dv-sec dv-sec--log",
        ]
        assert r["flow"] is flow_open
        assert r["sum"] is False and r["cards"] == 1  # 진척형 카드는 떼고 조사 결과 카드 1장


# ─── 머리 「조사 이유」 ──────────────────────────────────────────────────


_WHY_EVENTS = [
    {
        "seq": 1,
        "at": 1.0,
        "engine": "playbook",
        "kind": "started",
        "label": "플레이북 host.cpu 선택",
        "purpose": "증상 「CPU 사용률」 — 대상 서버 svr-was-01",
    },
    {
        "seq": 2,
        "at": 2.0,
        "engine": "system",
        "kind": "deep_start",
        "label": "심층 조사(HolmesGPT) 시작 — parallel",
        "purpose": "플레이북과 따로 두 번째 의견을 낸다(플레이북 결과를 넘기지 않음)",
    },
]
_WHY_SUBJECT = {
    "server_name": "svr-was-01",
    "route_label": "플레이북 host.cpu",
    "route_reason": "대상 서버 1대(svr-was-01)와 증상 「CPU 사용률」이 규칙으로 정해짐",
}


def _why_script(flag: str) -> str:
    return (
        _running_route(events=_WHY_EVENTS, subject=_WHY_SUBJECT)
        + f"""
flag = {flag};
var v = makeOn();
var tab = await openRun(v, null, null);
function heads() {{
    return tab.dom.target.children.map(function (c) {{ return [c.tagName, c._text]; }});
}}
var first = heads();
var more = findAll(tab.dom.target, "dv-why-more");
var btn = findAll(tab.dom.target, "dv-flow-toggle");
var hiddenBefore = more.length ? more[0].hidden : null;
var btnText = btn.length ? btn[0].textContent : null;
if (btn.length) btn[0].click();
v.ctl.select("inv-f");          // 머리 다시 그리기 — 펼침 유지
var more2 = findAll(tab.dom.target, "dv-why-more");
out = {{ heads: first, hiddenBefore: hiddenBefore, btn: btnText,
    after: more2.length ? more2[0].hidden : null,
    afterBtn: findAll(tab.dom.target, "dv-flow-toggle")
        .map(function (b) {{ return b.textContent; }}),
    rest: more2.length ? more2[0].children.map(function (c) {{ return c.textContent; }}) : [] }};
"""
    )


def test_why_lines_fold_into_one_when_on() -> None:
    out = _run(_why_script("true"))
    dts = [t for tag, t in out["heads"] if tag == "dt"]
    assert dts.count("조사 이유") == 1
    why = [t for tag, t in out["heads"] if tag == "dd" and t.startswith("플레이북 host.cpu")]
    assert why == [
        "플레이북 host.cpu — 대상 서버 1대(svr-was-01)와 증상 「CPU 사용률」이 규칙으로 정해짐"
    ]
    assert out["hiddenBefore"] is True and out["btn"] == "외 2줄 ▸"
    assert out["after"] is False and out["afterBtn"] == ["외 2줄 ▾"]
    assert out["rest"] == [
        "플레이북 host.cpu 선택 — 증상 「CPU 사용률」 — 대상 서버 svr-was-01",
        "심층 조사(HolmesGPT) 시작 — 병렬 — "
        "플레이북과 따로 두 번째 의견을 낸다(플레이북 결과를 넘기지 않음)",
    ]


def test_why_lines_unchanged_when_off() -> None:
    out = _run(_why_script("false"))
    dts = [t for tag, t in out["heads"] if tag == "dt"]
    assert dts.count("조사 이유") == 3
    assert out["btn"] is None and out["rest"] == []


def test_fold_why_pure() -> None:
    out = _node("""
var T = DV.TEXT.whyHead;
var lines = [{ head: "대상", text: "a" }, { head: T, text: "w1" }, { head: "조건", text: "c" },
    { head: T, text: "w2" }, { head: T, text: "w3" }];
out = { folded: DV.foldWhy(lines), one: DV.foldWhy([{ head: T, text: "w" }]),
    none: DV.foldWhy(null) };
""")
    assert out["folded"] == [
        {"head": "대상", "text": "a"},
        {"head": "조사 이유", "text": "w1", "more": ["w2", "w3"]},
        {"head": "조건", "text": "c"},
    ]
    assert out["one"] == [{"head": "조사 이유", "text": "w"}] and out["none"] == []


# ─── 기능 조회가 늦게 오는 경우 · 꺼짐 ─────────────────────────────────────


def test_late_capability_switches_layout_on_next_head_refresh() -> None:
    out = _run(
        _running_route()
        + f"""
flag = false;
var v = makeOn();
var tab = await openRun(v, {{ source: "live", events: {_EV} }}, {_PART});
var before = cls(tab.dom.panel);
flag = true;
runTimers(5000);   // 5초 틱 → 머리 갱신
out = {{ before: before, after: cls(tab.dom.panel), sumCards: cards(tab.dom.sumBox),
    partCards: cards(tab.dom.partBody), live: liveES().length }};
"""
    )
    assert out["before"] == ["dv-tab-head", "dv-modes", "dv-flow", "dv-grid", "dv-final"]
    assert out["after"][:5] == [
        "dv-tab-head",
        "dv-sum-box bv",
        "dv-sec dv-sec--flow",
        "dv-sec dv-sec--partial",
        "dv-sec dv-sec--log",
    ]
    assert out["sumCards"] == 1 and out["partCards"] == 0 and out["live"] == 1


def test_off_keeps_legacy_skeleton_and_never_touches_local_storage() -> None:
    script = f"""
var res = {{}};
for (var mode of ["absent", "false"]) {{
    flag = false;
    var v = makeOn(undefined, mode);
    var tab = await openRun(v, {{ source: "none", events: {_EV} }}, {_PART});
    function shape(n) {{ return {{ c: n.className, h: !!n.hidden, k: n.children.map(shape) }}; }}
    res[mode] = {{ panel: cls(tab.dom.panel), grid: cls(tab.dom.grid), log: cls(tab.dom.logBox),
        bar: cls(tab.dom.logBar), part: cls(tab.dom.partBox),
        modes: findAll(tab.dom.panel, "dv-mode").map(function (b) {{ return b.textContent; }}),
        secs: findAll(v.root, "dv-sec").length + findAll(v.root, "dv-sum-box").length,
        partCards: cards(tab.dom.partBody), note: tab.dom.logNote.textContent,
        shape: JSON.stringify(shape(tab.dom.panel)) }};
    v.ctl.close("inv-f");
}}
out = {{ res: res, ls: lsLog.length }};
"""
    out = _run(_running_route() + script)
    for mode in ("absent", "false"):
        r = out["res"][mode]
        assert r["panel"] == ["dv-tab-head", "dv-modes", "dv-flow", "dv-grid", "dv-final"]
        assert r["grid"] == ["dv-log", "dv-partial"]
        assert r["log"] == ["dv-log-bar", "dv-note", "dv-rows"]
        assert r["bar"] == ["dv-label", "dv-filters", "dv-check"]
        assert r["part"] == ["dv-label", "dv-partial-body"]
        assert r["modes"] == ["조사 흐름", "실행 기록"]
        assert r["secs"] == 0 and r["partCards"] == 0
        assert r["note"] == ""  # 행이 있으면 종전처럼 안내 없음
    assert out["res"]["absent"]["shape"] == out["res"]["false"]["shape"]
    assert out["ls"] == 0
