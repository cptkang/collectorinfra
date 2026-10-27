"""plans/192 W2 — 포털 안 진입점(E1~E8·E11·E12)의 문구와 목적지를 표 하나로 고정한다.

규칙: 장애 진단으로 가는 버튼 = 「장애 진단에서 보기」 ·
조사 대화로 가는 버튼 = 「조사 대화로 열기」(「진행 보기」 폐기).
각 행은 장애 진단 꺼짐/켜짐에서 버튼 문구와 클릭 목적지를 node 실행으로 확인한다
(E7·E8은 진단 뷰 정적 단언). 관제 E9·머리글 E13·E14는 plans/193 범위라 여기서 다루지 않는다.
다만 수동 조사 접수 결과의 진행 중 조사(IF)·합류(JN) 버튼은 관제 서랍까지 같은 규칙으로 본다
(사용자 결정 2026-10-26 — 194의 「진행 중 조사 보기」 폐기 · 거절 안내문은 그대로).
예외(규칙 밖 동작 버튼): E8 「조사 시작」.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.test_api.test_ui_plan192_w1_entry import (
    _ALARM_EL,
    _DRAWER_FUNCS,
    _DRAWER_PRELUDE,
    APP_JS,
    BV_JS,
    DV_JS,
    FAKE_CONV,
    NOISE_JS,
    app_run,
    body_of,
    dv_text,
    entry_prelude,
    node_run,
    pin_run,
)
from tests.test_api.test_ui_plan192_w4_conversation_restore import thread_run

TO_DIAG = "장애 진단에서 보기"
TO_CONV = "조사 대화로 열기"
# (plans/193 W4 · K-1 B 규칙 2) 독립 관제 페이지의 진입은 새 탭 — 같은 문구 뒤에 표지
NEW_TAB = " (새 탭)"

# 클릭 결과 판독 — 목적지 한 곳에 그 조사 id로 갔는지
_WHERE = r"""
function where(id) {
    var d = dest();
    var nd = d.diagnosis.length, nc = d.conversation.length;
    if (nd && !nc) return d.diagnosis[0][0] === id ? "diagnosis" : "wrong-id";
    if (nc && !nd) return d.conversation[0] === id ? "conversation" : "wrong-id";
    return d.diagnosis.length || d.conversation.length ? "both" : "none";
}
"""


def _pre(extra: str = "") -> str:
    return entry_prelude(FAKE_CONV + _WHERE + "var chatInvestigationPanels = [];\n" + extra)


def _bv_open_conversation_text() -> str:
    m = re.search(r'^\s+openConversation: "([^"]*)",', BV_JS.read_text(encoding="utf-8"), re.M)
    assert m
    return m.group(1)


# ─── 행별 실행기 — {label: 버튼 문구 또는 None(버튼 없음·숨김), dest: 목적지} ──────────


def _e1(on: bool) -> Any:
    # 알람 카드 요약형 버튼 — 켜짐이면 숨김(E2가 대신) · 늦게 남은 버튼을 눌러도 진단으로
    return app_run(
        ("bindAlarmBriefing",),
        f"""
diagOn = {json.dumps(on)};
bindAlarmBriefing(alarmEl(), {{ alarm_id: "a-1", investigation_id: "inv-1" }});
var o = attached[0].opts;
var shown = !!o.render.openConversation;
o.onOpenConversation("inv-1");
out = {{ label: shown ? BV_OPEN : null, dest: where("inv-1") }};
""",
        _pre(
            _ALARM_EL
            + f"var BV_OPEN = {json.dumps(_bv_open_conversation_text(), ensure_ascii=False)};\n"
        ),
    )


def _e2(on: bool) -> Any:
    return app_run(
        ("bindDiagnosisButton",),
        f"""
diagOn = {json.dumps(on)};
var host = new El("section");
bindDiagnosisButton(host, {{ investigation_id: "inv-2", status: "running" }}, "a-2");
var btn = host.findByClass("diagnosis-open");
if (btn && !btn.hidden) btn.click();
out = {{ label: btn && !btn.hidden ? btn.textContent : null, dest: where("inv-2") }};
""",
        _pre(),
    )


def _e3(on: bool) -> Any:
    return app_run(
        ("buildPastAlarmCard",),
        f"""
diagOn = {json.dumps(on)};
var card = new El("div");
buildPastAlarmCard(card, {{ alarm_id: "a-3", tier: "T1" }});
attached[0].opts.onView({{ investigation_id: "inv-3", status: "done" }});
var btn = card.findByClass("alarm-past-open");
if (btn && !btn.hidden) btn.click();
out = {{ label: btn && !btn.hidden ? btn.textContent : null, dest: where("inv-3") }};
""",
        _pre(),
    )


def _e4(on: bool) -> Any:
    return app_run(
        ("appendChatInvestigationCards",),
        f"""
diagOn = {json.dumps(on)};
var bubble = new El("div");
appendChatInvestigationCards(bubble, [{{ kind: "investigation_ref",
    ref: {{ investigation_id: "inv-4", status: "submitted", route: "fault",
        engine: "holmes" }} }}]);
var card = bubble.children[0];
var btn = card && card.querySelector(".chat-inv-open");
if (btn && !btn.hidden) btn.click();
out = {{ label: btn && !btn.hidden ? btn.textContent : null, dest: where("inv-4") }};
""",
        _pre(),
    )


def _e5(host: str) -> Any:
    scripts = {
        "alarm": 'bindAlarmBriefing(alarmEl(), { alarm_id: "a-5", investigation_id: "inv-5" });'
        " var o = attached[0].opts;",
        "past": 'buildPastAlarmCard(new El("div"), { alarm_id: "a-5", tier: "T1" });'
        " var o = attached[0].opts;",
        "pin": 'investigationAlarmIds["inv-5"] = "a-5";'
        ' var o = pinOf("inv-5", { restored: false }).opts;',
    }

    def run(on: bool) -> Any:
        script = f"""
diagOn = {json.dumps(on)};
{scripts[host]}
if (typeof o.onOpenPrevious !== "function") throw new Error("onOpenPrevious 미배선");
var label = labelOf(o.openPreviousLabel);
o.onOpenPrevious("inv-old");
out = {{ label: label, dest: where("inv-old") }};
"""
        if host == "pin":
            return pin_run(script, _WHERE)
        return app_run(
            ("bindAlarmBriefing" if host == "alarm" else "buildPastAlarmCard",),
            script,
            _pre(_ALARM_EL),
        )

    return run


def _e6(on: bool) -> Any:
    return app_run(
        ("followToDiagnosis",),
        f"""
diagOn = {json.dumps(on)};
var res = {{ ok: true, status: 200, body: {{ investigation_id: "inv-6", status: "submitted" }} }};
var back = followToDiagnosis(res, "a-6");
var d = where("inv-6");
out = {{ label: null, dest: back === res ? d : "lost-response" }};
""",
        _pre(),
    )


def _e7(on: bool) -> Any:
    # 진단 종결 화면 [조사 대화로 열기] → opts.onOpenConversation(tab.id)
    # → app의 openInvestigationConversation
    dv = DV_JS.read_text(encoding="utf-8")
    final = dv[dv.index('var finalBox = el("section", "dv-final")') :]
    final = final[: final.index("panel.appendChild(finalBox)")]
    assert 'el("button", "bv-open-conv dv-open-conv", TEXT.openConversation)' in final
    assert "opts.onOpenConversation(tab.id)" in final
    init = body_of(APP_JS.read_text(encoding="utf-8"), "initDiagnosisView", "app.js")
    assert "onOpenConversation: openInvestigationConversation" in init
    return {"label": dv_text()["openConversation"], "dest": "conversation"}


def _e8(on: bool) -> Any:
    # 「이벤트 찾아 조사」[조사 시작] — 동작 버튼(규칙 예외) · 접수 뒤 그 조사 하위 탭을 연다
    dv = DV_JS.read_text(encoding="utf-8")
    assert "opts.startInvestigation(alarmId)" in dv
    return {"label": dv_text()["startBtn"], "dest": "diagnosis"}


def _e11(key: str) -> Any:
    def run(on: bool) -> Any:
        return app_run(
            ("openInvestigationFromQuery",),
            f"""
diagOn = {json.dumps(on)};
window.location = {{ pathname: "/", search: "?{key}=inv-11", hash: "" }};
openInvestigationFromQuery();
out = {{ label: null, dest: where("inv-11"), left: replaced[0] }};
""",
            _pre(
                "var replaced = [];\n"
                "var history = { replaceState: function (s, t, u) { replaced.push(u); } };\n"
            ),
        )

    return run


def _e12(pinned: bool) -> Any:
    def run(on: bool) -> Any:
        turns = "PINNED" if pinned else "PLAIN"
        out = thread_run(
            f"""
diagOn = {json.dumps(on)};
threadBodies["t-12"] = {{ turns: {turns} }};
openThread("t-12");
await tick(); await tick(); await tick();
out = state();
"""
        )
        return {"label": None, "dest": out["slot"] + "-tab" if out["shown"] else "none"}

    return run


# ─── 수동 조사 접수 결과 — 진행 중 조사(IF)·합류(JN)로 가는 버튼 ─────────────────
# 실물 공용 렌더(manualOutcome)로 [조사 시작] → 응답 → 버튼 문구·클릭 목적지를 본다.
_START = r"""
function act(box, name, id) {
    var target = { disabled: false, getAttribute: function (k) {
        if (k === "data-bv-action") return name;
        return k === "data-investigation-id" ? id || null : null; } };
    target.closest = function () { return target; };
    box.h({ target: target, preventDefault: function () {} });
}
function goOf(html) {
    var re = /data-bv-action="open-investigation" data-investigation-id="([^"]*)">([^<]*)</;
    var m = re.exec(html);
    return m ? { id: m[1], label: m[2] } : null;
}
var STARTS = {
    in_flight: { ok: true, status: 200, body: { investigation_id: "inv-r", status: "rejected",
        reason: "host_investigation_in_flight", reason_text: "같은 서버를 조사 중입니다.",
        existing_investigation_id: "inv-9" } },
    joined: { ok: true, status: 200,
        body: { investigation_id: "inv-9", status: "running", joined: true } },
};
"""

# require가 전역 BriefingView를 실물로 바꾼다 — 카드 쪽 가짜(attach 기록)를 되돌린다
_BV_BOX = r"""
var fakeBV = window.BriefingView;
var BVreal = require(BV_PATH);
window.BriefingView = fakeBV;
function bvBox() {
    var b = { innerHTML: "", isConnected: true, h: null,
        addEventListener: function (t, f) { b.h = f; }, contains: function () { return true; },
        querySelectorAll: function () { return []; }, querySelector: function () { return null; } };
    return b;
}
"""


def _bv_path() -> str:
    return f"var BV_PATH = {json.dumps(str(BV_JS))};\n"


def _card_drawer(kind: str) -> Any:
    # 알람 카드 「조사 브리핑」(수동 조사 모드)
    # — app이 넘긴 onOpenInvestigation·openInvestigationLabel
    def run(on: bool) -> Any:
        return app_run(
            ("bindAlarmBriefing",),
            f"""
diagOn = {json.dumps(on)};
bindAlarmBriefing(alarmEl(), {{ alarm_id: "a-7", investigation_id: "" }});
var o = attached[0].opts;
var box = bvBox();
var none = {{ status: "none", previous: [] }};
var panel = BVreal.attach(box, {{ initial: none,
    load: function () {{ return Promise.resolve(none); }},
    render: {{ mode: "summary" }}, manualMode: true,
    analyze: {{ submit: function () {{ return Promise.resolve(STARTS[{json.dumps(kind)}]); }},
        loadById: function (id) {{
            return Promise.resolve({{ status: "running", investigation_id: id }}); }} }},
    onOpenInvestigation: o.onOpenInvestigation,
    openInvestigationLabel: o.openInvestigationLabel }});
await panel.open(); await tick();
act(box, "analyze"); await tick(); await tick(); await tick();
var g = goOf(box.innerHTML);
if (g) act(box, "open-investigation", g.id);
panel.close();
out = {{ label: g ? g.label : null, dest: where("inv-9") }};
""",
            _pre(_ALARM_EL + _bv_path() + _BV_BOX + _START),
        )

    return run


def _card_start(kind: str) -> Any:
    # 알람 카드 「조사」 칸 [조사 시작] — 거부(in_flight)는 사유 + 버튼,
    # 합류는 브리핑 칸으로 바꾸고 안내 + 버튼
    def run(on: bool) -> Any:
        if kind == "in_flight":
            call = (
                "var host = new El(\"div\");\n"
                "showAlarmStartOutcome(host, { disabled: false }, { kind: \"in_flight\","
                " text: \"같은 서버를 조사 중입니다.\", existingId: \"inv-9\" });\n"
            )
        else:
            call = (
                "var fresh = new El(\"div\");\n"
                "fresh.q[\".alarm-briefing-toggle\"] = new El(\"button\");\n"
                "Object.defineProperty(El.prototype, \"firstChild\", { configurable: true,"
                " get: function () { return fresh; } });\n"
                "var section = new El(\"section\");\n"
                "section.parentNode = { replaceChild: function () {} };\n"
                "swapToBriefing(new El(\"div\"), section, { alarm_id: \"a-7\" },"
                " { kind: \"joined\", text: \"합류했습니다.\", investigationId: \"inv-9\" });\n"
                "var host = fresh.children[0];\n"
            )
        return app_run(
            ("showAlarmStartOutcome", "swapToBriefing"),
            f"""
diagOn = {json.dumps(on)};
{call}
var b = host && host.querySelector(".inv-go");
if (b) b.click();
out = {{ label: b ? b.textContent : null, dest: where("inv-9") }};
""",
            _pre(
                "window.BriefingView.startMsgHtml = function (text, go) {\n"
                "    return text + (go ? '<button class=\"bv-choice inv-go\">' + go.label"
                " + '</button>' : '');\n};\n"
                "window.BriefingView.TEXT = { title: \"조사 브리핑\" };\n"
                "function renderAlarmBriefingSection() { return \"\"; }\n"
                "function bindAlarmBriefing() {}\n"
            ),
        )

    return run


def _noise_drawer(kind: str) -> Any:
    # 관제 서랍(사용자 토큰 · 조사 기록 없음 → 접수 칸) — 문구는 관제의 진단 켜짐 판정(liveViewOn)
    def run(on: bool) -> Any:
        src = NOISE_JS.read_text(encoding="utf-8")
        defs = "\n".join(body_of(src, n, "noise.js") for n in _DRAWER_FUNCS)
        out = node_run(
            _bv_path() + _DRAWER_PRELUDE + _START,
            defs,
            f"""
liveViewOn = {json.dumps(on)};
manualOnlyMode = Promise.resolve(true);
postRes = STARTS[{json.dumps(kind)}];
renderInvestigation({{ alarm_id: "a-8" }});
for (var i = 0; i < 4; i++) await tick();
var box = attaches[0].c;
act(box, "analyze"); await tick(); await tick(); await tick();
var g = goOf(box.innerHTML);
if (g) act(box, "open-investigation", g.id);
if (investigationPanel) investigationPanel.close();
out = {{ label: g ? g.label : null, portal: portal }};
""",
        )
        dest = "portal" if out["portal"] == ["inv-9"] else "none"
        return {"label": out["label"], "dest": dest}

    return run


def _dv_start(kind: str) -> Any:
    # 장애 진단 「이벤트 찾아 조사」 — 진단 뷰 안(켜짐 전용)이라 늘 「장애 진단에서 보기」
    # (node 실행은 194 테스트)
    def run(on: bool) -> Any:
        body = body_of(DV_JS.read_text(encoding="utf-8"), "showOutcome", "diagnosis-view.js")
        target = "r.existingId" if kind == "in_flight" else "r.investigationId"
        assert re.search(r"goButton\(TEXT\.viewInDiagnosis, function \(\) \{ open\(" + target, body)
        assert "viewInFlight" not in body
        return {"label": dv_text()["viewInDiagnosis"], "dest": "diagnosis"}

    return run


_RUNNERS = {
    "E1": _e1,
    "E2": _e2,
    "E3": _e3,
    "E4": _e4,
    "E5-alarm": _e5("alarm"),
    "E5-past": _e5("past"),
    "E5-pin": _e5("pin"),
    "E6": _e6,
    "E7": _e7,
    "E8": _e8,
    "E11-investigation": _e11("investigation"),
    "E11-diagnosis": _e11("diagnosis"),
    "E11-conversation": _e11("conversation"),
    "E12-pinned": _e12(True),
    "E12-plain": _e12(False),
    "IF-card": _card_start("in_flight"),
    "IF-card-drawer": _card_drawer("in_flight"),
    "IF-noise": _noise_drawer("in_flight"),
    "IF-dv": _dv_start("in_flight"),
    "JN-card": _card_start("joined"),
    "JN-card-drawer": _card_drawer("joined"),
    "JN-noise": _noise_drawer("joined"),
    "JN-dv": _dv_start("joined"),
}

# (진입점, 진단 켜짐, 문구(None = 버튼 없음·숨김), 목적지)
_TABLE = [
    ("E1", False, TO_CONV, "conversation"),
    ("E1", True, None, "diagnosis"),
    ("E2", False, None, "none"),
    ("E2", True, TO_DIAG, "diagnosis"),
    ("E3", False, TO_CONV, "conversation"),
    ("E3", True, TO_DIAG, "diagnosis"),
    ("E4", False, TO_CONV, "conversation"),
    ("E4", True, TO_DIAG, "diagnosis"),
    ("E5-alarm", False, TO_CONV, "conversation"),
    ("E5-alarm", True, TO_DIAG, "diagnosis"),
    ("E5-past", False, TO_CONV, "conversation"),
    ("E5-past", True, TO_DIAG, "diagnosis"),
    ("E5-pin", False, TO_CONV, "conversation"),
    ("E5-pin", True, TO_DIAG, "diagnosis"),
    ("E6", False, None, "none"),
    ("E6", True, None, "diagnosis"),
    ("E7", True, TO_CONV, "conversation"),
    ("E8", True, "조사 시작", "diagnosis"),
    ("E11-investigation", False, None, "conversation"),
    ("E11-investigation", True, None, "diagnosis"),
    ("E11-diagnosis", False, None, "conversation"),
    ("E11-diagnosis", True, None, "diagnosis"),
    ("E11-conversation", False, None, "conversation"),
    ("E11-conversation", True, None, "conversation"),
    ("E12-pinned", False, None, "investigation-tab"),
    ("E12-pinned", True, None, "investigation-tab"),
    ("E12-plain", False, None, "chat-tab"),
    ("E12-plain", True, None, "chat-tab"),
    ("IF-card", False, TO_CONV, "conversation"),
    ("IF-card", True, TO_DIAG, "diagnosis"),
    ("IF-card-drawer", False, TO_CONV, "conversation"),
    ("IF-card-drawer", True, TO_DIAG, "diagnosis"),
    ("IF-noise", False, TO_CONV + NEW_TAB, "portal"),
    ("IF-noise", True, TO_DIAG + NEW_TAB, "portal"),
    ("IF-dv", True, TO_DIAG, "diagnosis"),
    ("JN-card", False, TO_CONV, "conversation"),
    ("JN-card", True, TO_DIAG, "diagnosis"),
    ("JN-card-drawer", False, TO_CONV, "conversation"),
    ("JN-card-drawer", True, TO_DIAG, "diagnosis"),
    ("JN-noise", False, TO_CONV + NEW_TAB, "portal"),
    ("JN-noise", True, TO_DIAG + NEW_TAB, "portal"),
    ("JN-dv", True, TO_DIAG, "diagnosis"),
]


@pytest.mark.parametrize(
    ("entry", "diag_on", "label", "destination"),
    _TABLE,
    ids=[f"{e}-{'on' if on else 'off'}" for e, on, _, _ in _TABLE],
)
def test_entry_table(entry: str, diag_on: bool, label: str | None, destination: str) -> None:
    out = _RUNNERS[entry](diag_on)
    assert {"label": out["label"], "dest": out["dest"]} == {"label": label, "dest": destination}


def test_rule_labels_match_diagnosis_view_text() -> None:
    text = dv_text()
    assert text["viewInDiagnosis"] == TO_DIAG
    assert text["openConversation"] == TO_CONV
    assert _bv_open_conversation_text() == TO_CONV


@pytest.mark.parametrize("on", [False, True])
def test_entry_label_and_join_button_label_agree(on: bool) -> None:
    # entryLabel()(E3·E4·E5)과 194의 합류 버튼 문구(openInvestigationLabel)는 같은 값이다
    out = app_run(
        ("entryLabel", "bindAlarmBriefing"),
        f"""
diagOn = {json.dumps(on)};
bindAlarmBriefing(alarmEl(), {{ alarm_id: "a-1", investigation_id: "inv-1" }});
out = {{ entry: entryLabel(), join: labelOf(attached[0].opts.openInvestigationLabel) }};
""",
        _pre(_ALARM_EL),
    )
    want = TO_DIAG if on else TO_CONV
    assert out == {"entry": want, "join": want}


def test_progress_view_label_is_gone() -> None:
    for path in (APP_JS, DV_JS):
        assert '"진행 보기"' not in path.read_text(encoding="utf-8"), path.name
    # (사용자 결정 2026-10-26) 194의 in_flight 전용 문구도 폐기 — 합류와 같은 규칙 문구
    for path in (APP_JS, BV_JS, DV_JS, NOISE_JS):
        assert '"진행 중 조사 보기"' not in path.read_text(encoding="utf-8"), path.name


def test_entry_label_helper_shape() -> None:
    body = body_of(APP_JS.read_text(encoding="utf-8"), "entryLabel", "app.js")
    assert "diagnosisOn()" in body and "window.DiagnosisView.TEXT.viewInDiagnosis" in body
    assert '"조사 대화로 열기"' in body
