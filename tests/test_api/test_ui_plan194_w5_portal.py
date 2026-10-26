"""plans/194 W5 — 포털 UI 장면 U1~U4(D-347 · 목업 승인 2026-10-26).

브라우저 e2e는 이 환경에서 돌지 않으므로 W15 가짜 DOM 하네스(`_node`)로 실제 JS를 돌리고,
배선은 정적으로 고정한다:
    - U2 접수 결과(`BriefingView.startOutcome`) — 합류·같은 서버 조사 중·대기열 포화·
      시간당 상한·거부·실패
    - U3 대기 표시(`DiagnosisView.queueInfo/queueParts`) — 목록 칩 ·
      `service_load` 없으면 「/ …」 생략
    - U4 이벤트 찾아 조사 — PAGE 먼저 · 조사 안 됨 먼저 · 조사 상태 열 · 필터 · 다음 행동
    - 공용 렌더 `manualMode`·`queue` — 키가 없으면 종전 렌더와 같다
    - U1 카드 — 조사 기록 없는 카드에 펼친 「조사」 칸(렌더 시 호출 없음 · 구서버는 종전)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_STATIC = Path(__file__).resolve().parents[2] / "src" / "static"
_APP_JS = _STATIC / "js" / "app.js"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_DV_JS = _STATIC / "js" / "diagnosis-view.js"

_TEXTS = '{ failed: "실패 — {reason}", rejected: "거부 — {reason}", joined: "합류했습니다." }'


# ─── U2 접수 결과 ────────────────────────────────────────────────────────


def _outcome(res: str) -> dict[str, Any]:
    return _node(f"out = BV.startOutcome({res}, clock, {_TEXTS});")


def test_outcome_accepted_and_joined() -> None:
    assert _outcome(
        "{ ok: true, status: 200, body: { investigation_id: 'inv-1', status: 'submitted',"
        " joined: false } }"
    ) == {
        "kind": "accepted",
        "text": "",
        "investigationId": "inv-1",
    }
    assert _outcome(
        "{ ok: true, status: 200, body: { investigation_id: 'inv-2', status: 'running',"
        " joined: true } }"
    ) == {
        "kind": "joined",
        "text": "합류했습니다.",
        "investigationId": "inv-2",
    }


def test_outcome_host_in_flight_keeps_existing_id_and_reason_text() -> None:
    out = _outcome(
        "{ ok: true, status: 200, body: { investigation_id: 'inv-r', status: 'rejected',"
        " reason: 'host_investigation_in_flight',"
        " reason_text: '같은 서버를 조사 중입니다 — 끝난 뒤 다시.',"
        " existing_investigation_id: 'inv-old' } }"
    )
    assert out == {
        "kind": "in_flight",
        "text": "같은 서버를 조사 중입니다 — 끝난 뒤 다시.",
        "existingId": "inv-old",
    }
    # existing id가 없으면 빈 문자열(화면이 진행 중 목록으로 간다)
    out = _outcome(
        "{ ok: true, status: 200, body: { status: 'rejected',"
        " reason: 'host_investigation_in_flight' } }"
    )
    assert out == {
        "kind": "in_flight",
        "text": "거부 — host_investigation_in_flight",
        "existingId": "",
    }


@pytest.mark.parametrize(
    ("reason_text", "expected"),
    [
        (
            "조사 대기열이 가득 찼습니다 — 잠시 뒤 다시 요청하세요.",
            "조사 대기열이 가득 찼습니다 — 잠시 뒤 다시 요청하세요.",
        ),
        ("조사 서비스가 바쁩니다.", "조사 서비스가 바쁩니다. — 잠시 뒤 다시 눌러 주세요."),
    ],
)
def test_outcome_busy_always_says_try_later(reason_text: str, expected: str) -> None:
    for code in ("queue_full", "service_overloaded"):
        out = _outcome(
            f"{{ ok: true, status: 200, body: {{ status: 'rejected', reason: '{code}',"
            f" reason_text: '{reason_text}' }} }}"
        )
        assert out == {"kind": "busy", "text": expected}


def test_outcome_hourly_limit_tells_retry_time_from_detail() -> None:
    """현행 서버의 시간당 상한 = 429 `detail = {code, reason}`.

    `retry_after_seconds`가 있으면 다시 요청할 수 있는 시각을 알린다.
    """
    out = _node(
        f"""
var res = {{ ok: false, status: 429, body: {{ detail: {{ code: "hourly_limit",
    reason: "시간당 사용자 요청 조사 상한(3건)에 도달했습니다 — 잠시 후 다시 요청해 주세요.",
    retry_after_seconds: 600 }} }} }};
var r = BV.startOutcome(res, clock, {_TEXTS});
var d = new Date(clock + 600000);
var hm = (d.getHours() < 10 ? "0" : "") + d.getHours() + ":"
    + (d.getMinutes() < 10 ? "0" : "") + d.getMinutes();
var top = BV.startOutcome({{ ok: false, status: 429,
    body: {{ detail: {{ code: "hourly_limit", reason: "x" }},
    retry_after_seconds: 60 }} }}, clock, {_TEXTS});
var none = BV.startOutcome({{ ok: false, status: 429,
    body: {{ detail: {{ code: "hourly_limit", reason: "한도" }} }} }},
    clock, {_TEXTS});
out = {{ r: r, hm: hm, at: clock + 600000, top: top.kind, none: none }};
"""
    )
    assert out["r"] == {
        "kind": "retry_later",
        "retryAt": out["at"],
        "text": (
            f"시간당 사용자 요청 조사 상한(3건)에 도달했습니다 — {out['hm']}"
            " 이후 다시 요청할 수 있습니다."
        ),
    }
    assert out["top"] == "retry_later"  # 최상위 `retry_after_seconds`도 받는다
    # 구서버(필드 없음) — 종전 실패 문구 그대로(사유 노출)
    assert out["none"] == {"kind": "failed", "text": "실패 — 한도"}


def test_outcome_other_rejection_and_failures() -> None:
    assert _outcome(
        "{ ok: true, status: 200, body: { status: 'rejected', reason: 'dedup_ttl_active' } }"
    ) == {
        "kind": "rejected",
        "text": "거부 — dedup_ttl_active",
    }
    assert _outcome(
        "{ ok: false, status: 403, body: { detail: { code: 'zone_denied', reason: '권한 없음' } } }"
    ) == {"kind": "failed", "text": "실패 — 권한 없음"}
    assert _outcome("{ ok: true, status: 200, body: { status: 'submitted' } }") == {
        "kind": "failed",
        "text": "실패 — 조사 id 없음",
    }


# ─── U3 대기 표시 ────────────────────────────────────────────────────────


def test_queue_info_and_parts() -> None:
    out = _node(
        """
var load = { running: 2, queued: 1, max_concurrent: 2, max_queued: 4, abandoned_slots: 0 };
out = {
    withLoad: DV.queueParts(DV.queueInfo(
        { phase: "queued", queue_position: 2, status: "running" }, load)),
    noLoad: DV.queueParts(DV.queueInfo({ status: "submitted", queue_position: 1 }, null)),
    running: DV.queueInfo({ phase: "collecting", queue_position: 2, status: "running" }, load),
    noPos: DV.queueInfo({ phase: "queued", queue_position: null }, load),
    badPos: DV.queueInfo({ phase: "queued", queue_position: 1.5 }, load),
    // phase 없는 미종결 뷰 — sre는 대기 중에만 순번을 싣는다
    // (백엔드가 phase를 안 실어도 대기로 본다)
    noPhase: DV.queueInfo({ status: "running", queue_position: 3 }, null),
    noPhaseDone: DV.queueInfo({ status: "done", queue_position: 3 }, null),
};
"""
    )
    assert out["withLoad"] == ["대기 중 · 순번 2", " / 동시 조사 2건 진행 중"]
    assert out["noLoad"] == ["대기 중 · 순번 1", ""]
    assert out["running"] is None and out["noPos"] is None and out["badPos"] is None
    assert out["noPhase"] == {"position": 3, "running": None}
    assert out["noPhaseDone"] is None


def test_list_row_queue_chip_with_service_load_and_subject_text() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [
    { investigation_id: "inv-req-web03", kind: "manual", origin: "manual", status: "submitted",
      phase: "queued", queue_position: 2, elapsed_s: 35,
      subject: { server_name: "svr-web-03", alarm_name: "CPU 사용률", reference_time: null } } ],
  service_load: { running: 2, queued: 1, max_concurrent: 2, max_queued: 4, abandoned_slots: 0 } } };
var v = make(false);
await v.ctl.probe();
var row = findAll(v.root, "dv-list-row")[0];
out = { cells: row.children.map(function (c) { return c.textContent; }),
    chip: texts(row, "bv-chip--hold") };
"""
    )
    assert out["cells"][0] == "svr-web-03 · CPU 사용률"  # 조사 id가 보이지 않는다
    assert out["cells"][3] == "대기 중 · 순번 2 / 동시 조사 2건 진행 중"
    assert out["chip"] == ["대기 중 · 순번 2"]


def test_summary_frame_service_load_refreshes_row() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [] } };
var v = make(false);
await v.ctl.probe();
runTimers(200);
var es = liveES()[0];
es.emit("summary", { items: [{ investigation_id: "inv-q", kind: "alarm", status: "running",
    phase: "queued", queue_position: 1, subject: "A1" }], service_load: { running: 3 } });
var first = texts(v.root, "dv-list-row")[0];
es.emit("summary", { items: [{ investigation_id: "inv-q", kind: "alarm", status: "running",
    phase: "queued", queue_position: 1, subject: "A1" }], service_load: null });
out = { first: first, second: texts(v.root, "dv-list-row")[0] };
"""
    )
    assert "대기 중 · 순번 1 / 동시 조사 3건 진행 중" in out["first"]
    assert "대기 중 · 순번 1" in out["second"] and "동시 조사" not in out["second"]


# ─── U4 이벤트 찾아 조사 ─────────────────────────────────────────────────

_ITEMS = """[
  { alarm_id: "d1", ts: "2026-10-26T01:13:00Z", server_name: "svr-was-01",
    alarm_name: "CPU 사용률", tier: "page",
    investigation: { investigation_id: "inv-done", status: "done", queue_position: null } },
  { alarm_id: "s1", ts: "2026-10-26T01:12:50Z", server_name: "noise-test",
    alarm_name: "가용성", tier: "suppress",
    investigation: null },
  { alarm_id: "n1", ts: "2026-10-26T01:12:40Z", server_name: "svr-web-03",
    alarm_name: "CPU 사용률", tier: "page",
    investigation: null },
  { alarm_id: "q1", ts: "2026-10-26T01:12:30Z", server_name: "nipdbr36",
    alarm_name: "가용성", tier: "page",
    investigation: { investigation_id: "inv-q", status: "submitted", queue_position: 1 } },
  { alarm_id: "f1", ts: "2026-10-26T01:12:20Z", server_name: "staaps2",
    alarm_name: "가용성", tier: "page",
    investigation: { investigation_id: "inv-f", status: "failed", queue_position: null } },
  { alarm_id: "n2", ts: "2026-10-26T01:12:10Z", server_name: "stafxo01",
    alarm_name: "가용성", tier: "page",
    investigation: null } ]"""


def _search(extra: str, items: str = _ITEMS) -> Any:
    return _node(
        f"""
var started = [];
var startRes = {{ ok: true, status: 200,
    body: {{ investigation_id: "inv-new", status: "submitted", joined: false }} }};
routes[API + "/active"] = {{ status: 200, body: {{ items: [], event_search: true }} }};
routes[API + "/inv-new"] = {{ status: 200, body: running("inv-new") }};
routes[API + "/inv-done"] = {{ status: 200, body: running("inv-done") }};
routes[API + "/inv-old"] = {{ status: 200, body: running("inv-old") }};
routes["/api/v1/noise/decisions"] = {{ status: 200, body: {{ items: {items}, total: 6 }} }};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }}, onActivate: function () {{}},
    analyzeFor: function () {{ return null; }},
    startInvestigation: function (id) {{ started.push(id); return Promise.resolve(startRes); }},
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
findAll(rootEl, "dv-search-btn")[0].click();
await flush();
function rows() {{ return findAll(rootEl, "dv-search-row"); }}
function servers() {{ return rows().map(function (r) {{ return r.children[1].textContent; }}); }}
{extra}
"""
    )


def test_search_orders_page_first_then_uninvestigated_with_state_column() -> None:
    out = _search(
        """
var states = rows().map(function (r) { return r.children[4].textContent; });
var acts = rows().map(function (r) { return r.children[5].children[0].textContent; });
out = { servers: servers(), states: states, acts: acts,
    stateTh: findAll(rootEl, "dv-search-statecol")[0].hidden,
    muted: rows().map(function (r) { return r.className.indexOf("dv-search-row--muted") >= 0; }),
    check: findAll(rootEl, "dv-search-check")[0].hidden };
"""
    )
    # PAGE·조사 안 됨(받은 순서) → PAGE·조사 있음 → 그 밖
    assert out["servers"] == [
        "svr-web-03",
        "stafxo01",
        "svr-was-01",
        "nipdbr36",
        "staaps2",
        "noise-test",
    ]
    assert out["states"] == [
        "조사 안 됨",
        "조사 안 됨",
        "완료",
        "대기 중 · 순번 1",
        "실패",
        "조사 안 됨",
    ]
    assert out["acts"] == [
        "조사 시작",
        "조사 시작",
        "장애 진단에서 보기",
        "장애 진단에서 보기",
        "조사 시작",
        "조사 시작",
    ]
    assert out["muted"] == [False, False, False, False, False, True]
    assert out["stateTh"] is False and out["check"] is False


def test_search_filters_are_client_side() -> None:
    out = _search(
        """
var n = calls.length;
var chk = findAll(rootEl, "dv-search-none")[0];
chk.checked = true; chk.fire("change");
var onlyNone = servers();
chk.checked = false; chk.fire("change");
var tier = findAll(rootEl, "dv-search-tier")[0];
tier.value = "suppress"; tier.fire("change");
var onlySuppress = servers();
chk.checked = true; chk.fire("change");
tier.value = "ticket"; tier.fire("change");
out = { onlyNone: onlyNone, onlySuppress: onlySuppress, empty: servers(),
    status: texts(rootEl, "dv-search-status")[0], refetch: calls.length - n };
"""
    )
    assert out["onlyNone"] == ["svr-web-03", "stafxo01", "noise-test"]
    assert out["onlySuppress"] == ["noise-test"]
    assert out["empty"] == []
    assert out["status"] == "판정 기록 6건 중 최근 6건 · 필터에 맞는 알람이 없습니다."
    assert out["refetch"] == 0  # 필터는 다시 묻지 않는다


def test_search_view_button_opens_existing_investigation() -> None:
    out = _search(
        """
rows()[2].children[5].children[0].click();
await flush();
out = { tabs: ctl.state().tabs, current: ctl.state().current, alarmId: ctl.tab("inv-done").alarmId,
    started: started };
"""
    )
    assert out == {"tabs": ["inv-done"], "current": "inv-done", "alarmId": "d1", "started": []}


def test_search_old_server_without_investigation_key_keeps_legacy_layout() -> None:
    out = _search(
        "out = { cells: rows()[0].children.length,"
        " th: findAll(rootEl, 'dv-search-statecol')[0].hidden,"
        " check: findAll(rootEl, 'dv-search-check')[0].hidden,"
        " btns: findAll(rootEl, 'dv-start-btn').length };",
        items=(
            '[{ alarm_id: "a1", ts: "2026-10-21T01:02:03Z", server_name: "WEB-01", tier: "page" }]'
        ),
    )
    assert out == {"cells": 5, "th": True, "check": True, "btns": 1}


def test_start_in_flight_and_joined_next_actions() -> None:
    out = _search(
        """
var btn = findAll(rows()[0], "dv-start-btn")[0];
var note = findAll(rows()[0], "dv-search-note")[0];
startRes = { ok: true, status: 200, body: { investigation_id: "inv-r", status: "rejected",
    reason: "host_investigation_in_flight", reason_text: "같은 서버를 조사 중입니다.",
    existing_investigation_id: "inv-old" } };
btn.click(); await flush();
var inflight = note.textContent;
findAll(note, "dv-search-go")[0].click(); await flush();
var afterGo = ctl.state().current;
startRes = { ok: true, status: 200,
    body: { status: "rejected", reason: "host_investigation_in_flight" } };
btn.click(); await flush();
var noId = { text: note.textContent, go: findAll(note, "dv-search-go").length };
startRes = { ok: true, status: 200, body: { status: "rejected",
    reason: "host_investigation_in_flight", existing_investigation_id: "../etc" } };
btn.click(); await flush();
var badId = findAll(note, "dv-search-go").length;
startRes = { ok: true, status: 200,
    body: { investigation_id: "inv-new", status: "running", joined: true } };
btn.click(); await flush();
ctl.select("list");
findAll(note, "dv-search-go")[0].click();
out = { inflight: inflight, afterGo: afterGo, noId: noId, badId: badId, joined: note.textContent,
    current: ctl.state().current, tabs: ctl.state().tabs };
"""
    )
    # (plans/192 W2) 진행 중 조사로 가는 버튼도 목적지 규칙 문구
    # (진단 뷰 안 = 「장애 진단에서 보기」)
    assert out["inflight"] == "같은 서버를 조사 중입니다.장애 진단에서 보기"
    assert out["afterGo"] == "inv-old"
    # 열 수 있는 기존 id가 없으면 사유만 — 진행 중 목록에는 남의·자동 조사가 없다
    assert out["noId"] == {
        "text": "조사 서비스가 거부했습니다 — host_investigation_in_flight",
        "go": 0,
    }
    assert out["badId"] == 0
    assert out["joined"] == "다른 사용자가 먼저 요청한 조사에 합류했습니다.장애 진단에서 보기"
    assert out["current"] == "inv-new" and "inv-new" in out["tabs"]


def test_start_hourly_limit_locks_button_until_retry_time() -> None:
    out = _search(
        """
var btn = findAll(rows()[0], "dv-start-btn")[0];
var note = findAll(rows()[0], "dv-search-note")[0];
startRes = { ok: false, status: 429, body: { detail: { code: "hourly_limit",
    reason: "이번 시간 요청 한도(1시간에 3건)를 모두 썼습니다 — 잠시 후 다시.",
    retry_after_seconds: 120 } } };
btn.click(); await flush();
var locked = btn.disabled;
var lockTimer = timers.filter(function (t) { return t.ms === 120000; }).length;
runTimers(120000);
out = { text: note.textContent, locked: locked, lockTimer: lockTimer, after: btn.disabled };
"""
    )
    assert out["text"].startswith("이번 시간 요청 한도(1시간에 3건)를 모두 썼습니다 — ")
    assert out["text"].endswith(" 이후 다시 요청할 수 있습니다.")
    assert out["locked"] is True and out["lockTimer"] == 1 and out["after"] is False


# ─── 공용 렌더 manualMode · queue ─────────────────────────────────────────


def test_render_manual_mode_none_and_default_unchanged() -> None:
    out = _node(
        """
var ui = { analyze: { enabled: true, previous: [] } };
out = {
    manual: BV.renderBody({ status: "none" }, { manualMode: true, analyze: true, ui: ui }),
    plain: BV.renderBody({ status: "none" }, { analyze: true, ui: ui }),
    bare: BV.renderBody({ status: "none" }),
};
"""
    )
    manual = out["manual"]
    assert "조사 안 됨" in manual and "bv-src" not in manual
    assert (
        "아직 조사하지 않은 알람입니다. 자동 조사는 꺼져 있습니다 — "
        "필요한 알람만 조사를 시작하세요." in manual
    )
    assert ">조사 시작</button>" in manual and "분석 요청" not in manual
    plain = out["plain"]
    assert ">없음<" in plain and "bv-src" in plain and ">분석 요청</button>" in plain
    assert "이 알람에는 자동 조사 기록이 없습니다." in out["bare"]


def test_render_queue_option_marks_hold_chip() -> None:
    out = _node(
        """
var view = { status: "submitted", investigation_id: "inv-q", briefing_lines: [] };
function q() { return { chip: "대기 중 · 순번 2", note: "동시 조사 2건 진행 중 — 앞 조사",
    runningText: "대기 중입니다" }; }
out = { queued: BV.renderBody(view, { queue: q }), plain: BV.renderBody(view, {}),
    done: BV.renderBody({ status: "done", briefing_lines: [] }, { queue: q }) };
"""
    )
    assert 'bv-chip--hold">대기 중 · 순번 2<' in out["queued"]
    assert "동시 조사 2건 진행 중 — 앞 조사" in out["queued"] and "대기 중입니다" in out["queued"]
    assert "bv-chip--hold" not in out["plain"] and "조사가 진행 중입니다" in out["plain"]
    assert "대기 중 · 순번" not in out["done"]  # 종결이면 대기 표시 없음


_ATTACH = """
var box = { innerHTML: "", isConnected: true, h: null,
    addEventListener: function (t, f) { this.h = f; },
    querySelectorAll: function () { return []; }, querySelector: function () { return null; },
    contains: function () { return true; } };
function act(name, id) {
    var target = { disabled: false, getAttribute: function (k) {
        return k === "data-bv-action" ? name : (k === "data-investigation-id" ? id : null); } };
    target.closest = function () { return target; };
    box.h({ target: target, preventDefault: function () {} });
}
var opened = [];
var submitRes = null;
var panel = BV.attach(box, {
    initial: { status: "none", previous: [] },
    load: function () { return Promise.resolve({ status: "none", previous: [] }); },
    render: { mode: "summary" },
    analyze: { submit: function () { return Promise.resolve(submitRes); },
        loadById: function (id) {
            return Promise.resolve({ status: "running", investigation_id: id }); } },
    manualMode: true,
    onOpenInvestigation: function (id) { opened.push(id); },
});
await panel.open();
await flush();
"""


def test_attach_manual_mode_in_flight_offers_existing_investigation() -> None:
    out = _node(
        _ATTACH
        + """
var before = box.innerHTML;
submitRes = { ok: true, status: 200, body: { investigation_id: "inv-r", status: "rejected",
    reason: "host_investigation_in_flight", reason_text: "같은 서버를 조사 중입니다.",
    existing_investigation_id: "inv-old" } };
act("analyze"); await flush(); await flush();
var html = box.innerHTML;
act("open-investigation", "inv-old");
panel.close();
out = { before: before, html: html, opened: opened };
"""
    )
    assert ">조사 시작</button>" in out["before"] and "조사 안 됨" in out["before"]
    html = out["html"]
    # (plans/192 W2) 문구는 합류와 같은 openInvestigationLabel(미지정 기본 「장애 진단에서 보기」)
    assert "같은 서버를 조사 중입니다." in html and ">장애 진단에서 보기</button>" in html
    assert "진행 중 조사 보기" not in html
    assert 'data-investigation-id="inv-old"' in html
    assert "조사 안 됨" in html  # 거부 레코드로 갈아타지 않는다
    assert out["opened"] == ["inv-old"]


def test_attach_manual_mode_joined_and_hourly_lock() -> None:
    out = _node(
        _ATTACH
        + """
// attach 잠금 해제는 실 setTimeout이라 1초로 둔다(node 프로세스가 기다린다)
submitRes = { ok: false, status: 429, body: { detail: { code: "hourly_limit",
    reason: "한도 — 나중에",
    retry_after_seconds: 1 } } };
act("analyze"); await flush(); await flush();
var locked = box.innerHTML;
submitRes = { ok: true, status: 200,
    body: { investigation_id: "inv-j", status: "running", joined: true } };
act("analyze"); await flush(); await flush(); await flush();
var joined = box.innerHTML;
panel.close();
out = { locked: locked, joined: joined };
"""
    )
    assert 'data-bv-action="analyze" disabled' in out["locked"]
    assert "한도 — " in out["locked"] and "이후 다시 요청할 수 있습니다." in out["locked"]
    joined = out["joined"]
    assert "다른 사용자가 먼저 요청한 조사에 합류했습니다." in joined
    assert (
        'data-bv-action="open-investigation" data-investigation-id="inv-j">장애 진단에서 보기<'
        in joined
    )


def test_start_block_markup() -> None:
    out = _node(
        """
out = { on: BV.startBlockHtml({ autoOff: true }), off: BV.startBlockHtml({}),
    msg: BV.startMsgHtml("<b>x</b>", { id: "inv-1", label: "장애 진단에서 보기" }, true),
    plain: BV.startMsgHtml("합류", null, false) };
"""
    )
    assert (
        "조사 안 됨" in out["on"] and 'class="bv-open-conv inv-start-btn">조사 시작<' in out["on"]
    )
    assert "자동 조사는 꺼져 있습니다 — 필요한 알람만 조사를 시작하세요." in out["on"]
    assert "자동 조사는 꺼져" not in out["off"]
    assert '<div class="inv-start-msg" hidden></div>' in out["on"]
    assert "&lt;b&gt;x&lt;/b&gt;" in out["msg"] and "bv-note--warn" in out["msg"]
    assert (
        'class="bv-choice inv-go" data-investigation-id="inv-1">장애 진단에서 보기<' in out["msg"]
    )
    assert "inv-go" not in out["plain"] and "bv-note--warn" not in out["plain"]


# ─── U1 카드 배선(정적) ──────────────────────────────────────────────────


def test_card_start_section_wiring() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    render = _js_function_body(src, "renderAlarmMessage")
    assert "hasAlarmStart(data) ? renderAlarmStartSection()" in render
    assert "!startHtml && hasAlarmBriefing(data) ? renderAlarmBriefingSection()" in render
    assert "bindAlarmStart(el, data)" in render
    assert "investigations" not in render  # 렌더 시 호출 없음
    cond = _js_function_body(src, "hasAlarmStart")
    assert "data.investigation_id" in cond and "manualOnlyMode()" in cond
    assert "investigation_enabled" in cond
    mode = _js_function_body(src, "manualOnlyMode")
    assert "investigation_manual_enabled === true" in mode
    assert "investigation_auto_enabled === false" in mode
    bind = _js_function_body(src, "bindAlarmStart")
    assert bind.index('addEventListener("click"') < bind.index("bvRequest(")
    assert '"/investigations", "POST"' in bind and "DV.startOutcome(res" in bind
    assert "swapToBriefing(el, section, data, r)" in bind
    outcome = _js_function_body(src, "showAlarmStartOutcome")
    assert "showListIfEnabled" not in outcome and "openInvestigationEntry(existing)" in outcome
    assert "btn.disabled = true" in outcome and "r.retryAt" in outcome
    swap = _js_function_body(src, "swapToBriefing")
    assert "openInvestigationEntry(id, data.alarm_id)" in swap
    briefing = _js_function_body(src, "bindAlarmBriefing")
    assert "queue: alarmQueueDisplay" in briefing and "manualMode: manualOnlyMode()" in briefing
    assert "onOpenInvestigation" in briefing
    assert '"장애 진단에서 보기"' in _js_function_body(src, "bindDiagnosisButton")
    assert "service_load" in _js_function_body(src, "alarmQueueDisplay")


def test_diagnosis_view_still_get_only() -> None:
    src = _DV_JS.read_text(encoding="utf-8")
    assert "POST" not in src and "method:" not in src
    bv = _BV_JS.read_text(encoding="utf-8")
    assert "startOutcome: startOutcome" in bv


def _card_js(*names: str) -> str:
    """app.js의 카드 함수를 꺼내 node 하네스에서 돌린다(app.js 전체는 브라우저 전역에 묶여 있다)."""
    src = _APP_JS.read_text(encoding="utf-8")
    return "\n".join(_js_function_body(src, n) for n in names)


def test_card_start_block_only_in_manual_only_mode() -> None:
    """자동 조사가 켜져 있으면 카드에 id가 없어도 조사가 있을 수 있다.

    그래서 「조사 안 됨」을 단정하지 않는다(수동 전용 모드에서만 시작 칸).
    """
    out = _node(
        "var window = { BriefingView: BV, DiagnosisView: DV }; var alarmCapabilities = null;\n"
        + _card_js("hasAlarmStart", "manualOnlyMode")
        + """
function at(caps, data) { alarmCapabilities = caps; return hasAlarmStart(data); }
var card = { alarm_id: "a1" };
out = {
    manualOnly: at({ investigation_enabled: true, investigation_manual_enabled: true,
        investigation_auto_enabled: false }, card),
    auto: at({ investigation_enabled: true, investigation_manual_enabled: true,
        investigation_auto_enabled: true }, card),
    oldServer: at({ investigation_enabled: true }, card),
    noAutoKey: at({ investigation_enabled: true, investigation_manual_enabled: true }, card),
    off: at({ investigation_enabled: false, investigation_manual_enabled: true,
        investigation_auto_enabled: false }, card),
    hasId: at({ investigation_enabled: true, investigation_manual_enabled: true,
        investigation_auto_enabled: false }, { alarm_id: "a1", investigation_id: "inv-1" }),
};
"""
    )
    assert out == {
        "manualOnly": True,
        "auto": False,
        "oldServer": False,
        "noAutoKey": False,
        "off": False,
        "hasId": False,
    }


def test_card_same_host_rejection_button_only_with_existing_id() -> None:
    """같은 서버 조사 중 — 열 수 있는 기존 id가 없으면 사유 한 줄만.

    진행 중 목록으로 보내지 않는다(남의·자동 조사는 그 목록에 없다).
    """
    out = _node(
        "var window = { BriefingView: BV, DiagnosisView: DV };"
        " var INVESTIGATION_ID_RE = /^[A-Za-z0-9._:-]{1,128}$/;"
        " var openedEntry = [];"
        " function openInvestigationEntry(id) { openedEntry.push(id); }\n"
        + _card_js("paintAlarmOutcome", "showAlarmStartOutcome", "entryLabel", "diagnosisOn")
        + """
function host() {
    var h = { hidden: true, innerHTML: "", go: null,
        querySelector: function (sel) {
            if (sel !== ".inv-go" || h.innerHTML.indexOf("inv-go") < 0) return null;
            h.go = { addEventListener: function (t, f) { h.go.f = f; } };
            return h.go;
        } };
    return h;
}
var btn = { disabled: false };
var withId = host();
showAlarmStartOutcome(withId, btn, { kind: "in_flight", text: "같은 서버를 조사 중입니다.",
    existingId: "inv-old" });
withId.go.f();
var noId = host();
showAlarmStartOutcome(noId, btn, { kind: "in_flight", text: "같은 서버를 조사 중입니다.",
    existingId: "" });
var badId = host();
showAlarmStartOutcome(badId, btn, { kind: "in_flight", text: "x", existingId: "../etc" });
out = { withId: withId.innerHTML, noId: noId.innerHTML, noIdShown: !noId.hidden,
    badId: badId.innerHTML, opened: openedEntry };
"""
    )
    # (plans/192 W2) 문구는 목적지 규칙(entryLabel) — 진단 꺼짐이면 「조사 대화로 열기」
    assert (
        ">조사 대화로 열기<" in out["withId"] and 'data-investigation-id="inv-old"' in out["withId"]
    )
    assert out["opened"] == ["inv-old"]
    assert "같은 서버를 조사 중입니다." in out["noId"] and out["noIdShown"] is True
    assert "inv-go" not in out["noId"] and "inv-go" not in out["badId"]


# ─── W6 캡처 교정 ────────────────────────────────────────────────────────


def test_search_tier_options_are_the_four_real_tiers() -> None:
    """판정 4티어(page·ticket·dashboard·suppress) — 문구는 노이즈 콘솔과 같다."""
    items = """[
  { alarm_id: "t1", ts: "2026-10-26T01:12:00Z", server_name: "svr-t", tier: "ticket",
    investigation: null },
  { alarm_id: "b1", ts: "2026-10-26T01:11:00Z", server_name: "svr-b", tier: "dashboard",
    investigation: null } ]"""
    out = _search(
        """
var tier = findAll(rootEl, "dv-search-tier")[0];
var opts = tier.children.map(function (o) { return [o.value, o.textContent]; });
var tiers = rows().map(function (r) { return r.children[3].textContent; });
tier.value = "dashboard"; tier.fire("change");
out = { opts: opts, tiers: tiers, onlyBoard: servers() };
""",
        items=items,
    )
    assert out["opts"] == [
        ["", "즉시 통보(PAGE) 먼저 · 전체"],
        ["page", "PAGE — 즉시 통보"],
        ["ticket", "TICKET — 대기열·기록"],
        ["dashboard", "DASHBOARD — 화면 표시만"],
        ["suppress", "SUPPRESS — 억제"],
    ]
    assert out["tiers"] == ["대기열·기록", "화면 표시만"]  # 영문 원문을 그대로 보이지 않는다
    assert out["onlyBoard"] == ["svr-b"]


def test_search_table_hidden_until_rows() -> None:
    out = _node(
        """
routes[API + "/active"] = { status: 200, body: { items: [], event_search: true } };
var empty = { items: [], total: 0 };
routes["/api/v1/noise/decisions"] = function () { return { status: 200, body: empty }; };
var rootEl = new Node("div");
var ctl = DV.create({ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () { return {}; }, onActivate: function () {},
    analyzeFor: function () { return null; },
    startInvestigation: function () {
        return Promise.resolve({ ok: true, status: 200, body: {} }); },
    deps: { document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () { return clock; }, storage: storage } });
await ctl.probe();
var table = findAll(rootEl, "dv-search-list")[0];
var before = table.hidden;
findAll(rootEl, "dv-search-btn")[0].click(); await flush();
var none = table.hidden;
empty = { items: [{ alarm_id: "a1", ts: "2026-10-26T01:00:00Z", server_name: "S", tier: "page",
    investigation: null }], total: 1 };
findAll(rootEl, "dv-search-btn")[0].click(); await flush();
var shown = table.hidden;
var tier = findAll(rootEl, "dv-search-tier")[0];
tier.value = "suppress"; tier.fire("change");
out = { before: before, none: none, shown: shown, filtered: table.hidden };
"""
    )
    assert out == {"before": True, "none": True, "shown": False, "filtered": True}


def test_manual_investigation_badge_and_no_reanalyze_while_unfinished() -> None:
    out = _node(
        """
var ui = { analyze: { enabled: true, previous: [] } };
function body(view) { return BV.renderBody(view, { analyze: true, ui: ui }); }
out = {
    queuedManual: body({ status: "submitted", kind: "manual", investigation_id: "inv-m" }),
    runningOrigin: body({ status: "running", origin: "manual", investigation_id: "inv-o" }),
    doneManual: body({ status: "done", kind: "manual", briefing_lines: [] }),
    runningAuto: body({ status: "running", kind: "alarm", investigation_id: "inv-a" }),
    doneAuto: body({ status: "done", kind: "alarm", briefing_lines: [] }),
};
"""
    )
    for key in ("queuedManual", "runningOrigin", "doneManual"):
        assert "요청 분석 · 도구 출력 인용" in out[key] and "자동 조사 ·" not in out[key]
    assert "자동 조사 · 도구 출력 인용" in out["runningAuto"]
    # 대기·진행 중에는 [다시 분석]이 없다 — 끝나면 다시 보인다
    for key in ("queuedManual", "runningOrigin", "runningAuto"):
        assert 'data-bv-action="analyze"' not in out[key]
    for key in ("doneManual", "doneAuto"):
        assert 'data-bv-action="analyze">다시 분석<' in out[key]


def test_search_filter_inherits_surrounding_font() -> None:
    css = (_STATIC / "css" / "style.css").read_text(encoding="utf-8")
    block = css[css.index(".dv-search-filter,\n.dv-search-filter label,") :]
    block = block[: block.index("}")]
    assert "font-family: var(--font-display)" in block and "letter-spacing: normal" in block
    assert ".dv-search-list[hidden]" in css
