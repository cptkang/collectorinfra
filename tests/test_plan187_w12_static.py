"""plans/187 W12 — 채팅 증상 질의 조사 카드(웹 프런트 · §5.6.2 ③ · G-33).

`app.js`의 카드 함수들을 소스에서 잘라 node로 실행한다
(가짜 DOM · 실제 `briefing-view.js` · 망 호출 0).
    1. `ref.route`가 있는 `investigation_ref` → 답변 아래 카드 ·
       머리 「엔진 · route_label」(서버 문자열 그대로).
    2. `status=rejected` → 사유 안내만 · 재조회(load) 0회 · 「조사 대화로 열기」 없음.
    3. `ref.route`가 없는 ref(W5b 조사 대화 첫 턴) → 카드 없음 ·
       고정 카드 복원(`investigationRefOf`) 종전 그대로.
    4. 서버 문자열 이스케이프 · 형식이 어긋난 조사 id 무시.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tests.test_api.test_noise_dashboard_api import _js_function_body
from tests.test_plan187_w5b_static import _STATIC, _done_view, _node

_APP_JS = _STATIC / "js" / "app.js"
_XSS = '<img src=x onerror="alert(1)">'

_FUNCS = (
    "cssEsc",
    "escapeHtml",
    "chatInvestigationRefs",
    "chatInvestigationCardHtml",
    "appendChatInvestigationCards",
    "investigationRefOf",
)

# 가짜 DOM — textContent→innerHTML 이스케이프는 브라우저와 같게 &·<·>만 바꾼다.
_DOM = r"""
function makeEl(tag) {
    var el = {tag: tag, attrs: {}, children: [], listeners: {}, isConnected: true,
              _html: "", subs: {}};
    Object.defineProperty(el, "textContent", {set: function (v) {
        el._html = String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }});
    Object.defineProperty(el, "innerHTML", {
        get: function () { return el._html; },
        set: function (v) { el._html = v; el.subs = {}; }});
    el.setAttribute = function (k, v) { el.attrs[k] = String(v); };
    el.getAttribute = function (k) { return el.attrs[k] === undefined ? null : el.attrs[k]; };
    el.appendChild = function (c) { el.children.push(c); };
    el.addEventListener = function (t, f) { el.listeners[t] = f; };
    el.contains = function () { return true; };
    el.querySelectorAll = function () { return []; };
    el.querySelector = function (sel) {
        var m = /data-investigation-id="([^"]+)"/.exec(sel);
        if (m) {
            return el.children.filter(function (c) {
                return c.attrs["data-investigation-id"] === m[1];
            })[0] || null;
        }
        var cls = /^\.([\w-]+)$/.exec(sel);
        var hit = cls && (el._html.indexOf('class="' + cls[1]) >= 0
                          || el._html.indexOf(" " + cls[1] + '"') >= 0);
        if (hit) {
            return el.subs[sel] || (el.subs[sel] = makeEl("div"));
        }
        return null;
    };
    return el;
}
var document = {createElement: makeEl};
var window = {BriefingView: BV};
var alarmCapabilities = null;
var isProcessing = false;
var chatInvestigationPanels = [];
var CHAT_INVESTIGATION_KIND = "investigation_ref";
var loads = [];
var opened = [];
var views = {};
function loadInvestigationView(id) {
    loads.push(id);
    return Promise.resolve(views[id] || {status: "running", investigation_id: id});
}
function openInvestigationConversation(id) { opened.push(id); }
function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }
function closeAll() { chatInvestigationPanels.forEach(function (p) { p.close(); }); }
function cardOf(bubble) {
    var c = bubble.children[0];
    if (!c) return null;
    var body = c.subs[".chat-inv-body"];
    return {html: c.innerHTML, status: c.getAttribute("data-status"),
            id: c.getAttribute("data-investigation-id"),
            body: body ? body.innerHTML : null, hasOpen: c.innerHTML.indexOf("chat-inv-open") >= 0};
}
"""


def _harness() -> str:
    src = _APP_JS.read_text(encoding="utf-8")
    regex = re.search(r"var INVESTIGATION_ID_RE = (/[^\n]+/);", src)
    assert regex, "INVESTIGATION_ID_RE 정의를 찾지 못했다"
    funcs = "\n".join(_js_function_body(src, name) for name in _FUNCS)
    return f"var INVESTIGATION_ID_RE = {regex.group(1)};\n" + _DOM + funcs + "\n"


def _run(script: str, **values: Any) -> Any:
    head = "".join(
        f"var {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in values.items()
    )
    return _node(_harness() + head + script)


def _ref(**over: str) -> dict:
    ref = {
        "investigation_id": "inv-chat-1",
        "status": "submitted",
        "route": "playbook",
        "engine": "플레이북",
        "route_label": "플레이북 was.latency — 대상·증상 일치",
        "reason": "matched",
        "target": "web01",
        "playbook": "was.latency",
        "alarm_kind": "latency",
    }
    ref.update(over)
    return {
        "kind": "investigation_ref",
        "text": "조사 inv-chat-1 — 플레이북",
        "source": "turn",
        "ref": ref,
    }


# ─── 1. 접수 카드 ────────────────────────────────────────────────────────


def test_submitted_route_ref_renders_card_head_and_polls_summary() -> None:
    out = _run(
        """
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, ds);
await tick();
var card = cardOf(bubble);
out = {card: card, loads: loads.slice(), pending: chatInvestigationPanels[0].poller.pending(),
       count: bubble.children.length};
closeAll();
""",
        ds=[_ref()],
    )
    card = out["card"]
    assert out["count"] == 1
    assert card["status"] == "submitted" and card["id"] == "inv-chat-1"
    # 머리 = 엔진 · route_label(서버 문자열 그대로)
    assert "플레이북 was.latency — 대상·증상 일치" in card["html"]
    assert "대상 web01" in card["html"]
    assert card["hasOpen"] and "조사 대화로 열기" in card["html"]
    # 진행 중 → 공용 렌더 요약형 · 15초 재조회 예약
    assert out["loads"] == ["inv-chat-1"]
    assert out["pending"] is True
    assert "조사 중" in card["body"]


def test_done_playbook_view_renders_summary_and_stops_polling() -> None:
    view = _done_view("latency_sql")
    view["investigation_id"] = "inv-chat-1"
    out = _run(
        """
views["inv-chat-1"] = view;
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, ds);
await tick();
out = {body: cardOf(bubble).body, pending: chatInvestigationPanels[0].poller.pending()};
closeAll();
""",
        ds=[_ref()],
        view=view,
    )
    assert out["pending"] is False
    assert 'class="bv bv--playbook"' in out["body"]
    # 요약형 버튼은 카드 꼬리 하나만 — 공용 렌더에 openConversation을 넘기지 않는다
    assert 'data-bv-action="open-conversation"' not in out["body"]


def test_holmes_route_head_and_open_button_calls_conversation() -> None:
    ref = _ref(
        investigation_id="inv-holmes-2",
        route="holmesgpt",
        engine="HolmesGPT",
        route_label="HolmesGPT — 증상 유형 미확정",
        reason="type_none",
        target="",
    )
    ref["ref"].pop("target")
    out = _run(
        """
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, ds);
await tick();
var c = bubble.children[0];
c.subs[".chat-inv-open"].listeners.click();
out = {html: c.innerHTML, opened: opened.slice()};
closeAll();
""",
        ds=[ref],
    )
    assert "HolmesGPT — 증상 유형 미확정" in out["html"]
    assert "대상 " not in out["html"]
    assert out["opened"] == ["inv-holmes-2"]


def test_same_id_is_rendered_once() -> None:
    out = _run(
        """
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, ds);
appendChatInvestigationCards(bubble, ds);
await tick();
out = {count: bubble.children.length, loads: loads.length};
closeAll();
""",
        ds=[_ref(), _ref()],
    )
    assert out == {"count": 1, "loads": 1}


# ─── 2. 접수 거부 — 재조회 없음 ──────────────────────────────────────────


def test_rejected_card_shows_reason_only_without_reload() -> None:
    out = _run(
        """
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, ds);
await tick();
out = {card: cardOf(bubble), loads: loads.length, panels: chatInvestigationPanels.length};
""",
        ds=[_ref(status="rejected")],
    )
    card = out["card"]
    assert card["status"] == "rejected"
    assert out["loads"] == 0 and out["panels"] == 0
    assert "접수 거부" in card["html"] and "사유는 위 답변에 있습니다" in card["html"]
    assert not card["hasOpen"]
    assert "chat-inv-body" not in card["html"]


# ─── 3. W5b 고정 카드 ref(route 없음)는 종전 그대로 ──────────────────────


def test_routeless_ref_is_not_a_chat_card_and_pin_restore_unchanged() -> None:
    pin = {
        "kind": "investigation_ref",
        "text": "조사 대화 — 조사 inv-pin",
        "ref": {"investigation_id": "inv-pin"},
    }
    out = _run(
        """
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, [pin]);
out = {
    cards: bubble.children.length,
    loads: loads.length,
    refs: chatInvestigationRefs([pin]).length,
    pinOnly: investigationRefOf([{disclosures: [pin]}]),
    chatOnly: investigationRefOf([{disclosures: [chat]}]),
    // 조사 대화 첫 턴이 증상 질의여도 고정 카드는 W5b ref를 고른다
    both: investigationRefOf([{disclosures: [chat, pin]}]),
    later: investigationRefOf([{disclosures: [chat]}, {disclosures: [pin]}]),
};
""",
        pin=pin,
        chat=_ref(),
    )
    assert out["cards"] == 0 and out["loads"] == 0 and out["refs"] == 0
    assert out["pinOnly"] == "inv-pin"
    assert out["chatOnly"] is None
    assert out["both"] == "inv-pin"
    assert out["later"] == "inv-pin"


def test_other_disclosures_are_ignored() -> None:
    out = _run(
        "out = chatInvestigationRefs(ds).length;",
        ds=[
            {"kind": "apm_job", "ref": {"apm_job_id": "j1"}},
            {"kind": "scope", "text": "x"},
            {"kind": "other", "ref": {"investigation_id": "inv-x", "route": "playbook"}},
            None,
        ],
    )
    assert out == 0


# ─── 4. XSS · 형식 검사 ──────────────────────────────────────────────────


def test_server_strings_are_escaped_and_bad_ids_dropped() -> None:
    out = _run(
        """
var bubble = makeEl("div");
appendChatInvestigationCards(bubble, ds);
await tick();
out = {html: bubble.children.map(function (c) { return c.innerHTML; }).join(""),
       ids: bubble.children.map(function (c) { return c.getAttribute("data-investigation-id"); })};
closeAll();
""",
        ds=[
            _ref(engine=_XSS, route_label=_XSS, target=_XSS, status="rejected"),
            _ref(investigation_id='inv"><script>x</script>'),
        ],
        XSS=_XSS,
    )
    assert out["ids"] == ["inv-chat-1"]
    assert "<img" not in out["html"] and "<script" not in out["html"]
    assert "&lt;img src=x" in out["html"]


# ─── 5. 배선(정적) ───────────────────────────────────────────────────────


def test_wiring_render_paths_and_reset() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    render = _js_function_body(src, "renderAgentMessage")
    bubble = 'querySelector(".message-bubble")'
    assert f"appendChatInvestigationCards(el.{bubble}, data.disclosures)" in render
    assert f"appendChatInvestigationCards(streamingMsgJob.{bubble}, meta.disclosures)" in src
    reset = _js_function_body(src, "resetInvestigationConversation")
    assert "chatInvestigationPanels.forEach" in reset and "p.close()" in reset
    card = _js_function_body(src, "chatInvestigationCardHtml")
    # 서버 문자열은 모두 escapeHtml을 거친다
    for key in ("escapeHtml(head", "escapeHtml(ref.target)", "escapeHtml(ref.investigation_id)"):
        assert key in card


def test_server_ids_in_css_selectors_are_escaped() -> None:
    """(교정 L3) 서버 id(조사·잡·태스크)를 속성 선택자에 넣는 곳은 `cssEsc`(CSS.escape)를 거친다."""
    src = _APP_JS.read_text(encoding="utf-8")
    assert "CSS.escape" in _js_function_body(src, "cssEsc")
    for needle in ('[data-investigation-id="\' + cssEsc(id)', '[data-job-id="\' + cssEsc(ref.id)',
                   'li[data-key="\' + cssEsc(key)'):
        assert needle in src, needle
    assert re.search(r"data-investigation-id=\"' \+ id \+", src) is None
    # node 하네스에는 CSS가 없다 → 폴백이 따옴표·역슬래시를 이스케이프한다.
    assert _run("out = cssEsc(raw);", raw='a"b\\c') == 'a\\"b\\\\c'
