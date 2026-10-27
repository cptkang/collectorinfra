"""plans/196 — 장애 진단 조사 화면 결함(D5·D6·D7) · 폴스타 서버 선택(§1.1 ②) ·
관리자 「호스트 연결」.

`diagnosis-view.js`를 node로 실제 실행한다(W15 하네스 재사용).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_DV_JS = _STATIC / "js" / "diagnosis-view.js"
_APP_JS = _STATIC / "js" / "app.js"
_HL_JS = _STATIC / "js" / "admin-host-links.js"
_DASH = _STATIC / "admin" / "dashboard.html"

_LINK_HOST = (
    "linkHost: function (id, b) { posts.push({ id: id, body: b }); "
    "return Promise.resolve({ ok: linkRes.status < 300, status: linkRes.status, "
    "body: linkRes.body }); },"
)
_WAIT = "폴스타 서버 선택 대기 — 조사 화면에서 후보를 골라 다시 조회"


def _ev(seq: int, step: str, kind: str, **extra: object) -> dict:
    return {
        "seq": seq,
        "at": 1792390000 + seq,
        "engine": "playbook",
        "kind": kind,
        "step": step,
        "label": step,
        **extra,
    }


def _final(briefing: dict, events: list[dict] | None = None, **extra: object) -> dict:
    return {
        "status": "done",
        "investigation_id": "inv-f",
        "kind": "alarm",
        "briefing_lines": [],
        "briefing": briefing,
        "activity": {"next_seq": 99, "truncated": False, "events": events or []},
        **extra,
    }


def _script(final: dict, body: str, link_host: bool = True, cands: dict | None = None) -> str:
    """종결 조사 inv-f를 연 뒤 body를 돈다. linkHost 주입 여부를 고른다(app.js 배선과 같은 꼴).

    cands = `GET …/host-candidates` 응답(없으면 하네스 기본 404 = 기능 꺼짐).
    """
    pre = (
        f'routes[API + "/inv-f/host-candidates"] = {{ status: 200, body: '
        f"{json.dumps(cands, ensure_ascii=False)} }};"
        if cands is not None
        else ""
    )
    return f"""
{pre}
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(final, ensure_ascii=False)} }};
var posts = [];
var linkRes = {{ status: 200, body: {{ link: {{ state: "confirmed" }}, investigation_id: "inv-r",
    status: "submitted" }} }};
routes[API + "/inv-r"] = {{ status: 200, body: running("inv-r") }};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }},
    onActivate: function () {{}}, onOpenConversation: function () {{}},
    analyzeFor: function () {{ return null; }},
    {_LINK_HOST if link_host else ""}
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
runTimers(1000);
ctl.open("inv-f");
await flush();
runTimers(1000);
var tab = ctl.tab("inv-f");
await flush();
{body}
"""


# ─── D5 · 종결 탭 머리 경과 고정 ─────────────────────────────────────────


def test_done_tab_head_elapsed_is_fixed_after_time_passes() -> None:
    final = _final({"playbook": {"id": "fallback", "sections": []}}, elapsed_s=125)
    out = _node(
        _script(
            final,
            """
var before = tab.dom.meta.textContent;
clock += 3 * 3600 * 1000;
ctl.select("inv-f");
out = { before: before, after: tab.dom.meta.textContent };
""",
        )
    )
    assert "2분 5초" in out["before"]
    assert "2분 5초" in out["after"] and "시간" not in out["after"]


# ─── D6 · 상태 라벨 · 운영자 상세 · 조사 이유 원시 키 ─────────────────────


def test_status_labels_and_raw_keys_are_korean() -> None:
    out = _node("""
out = {
    words: DV.statusWords("단계 상태 missing — 소견 없음"),
    keep: DV.statusWords("단계 상태 weird — 소견 없음"),
    why: DV.whyLines([
        { seq: 1, kind: "started", engine: "playbook", label: "플레이북 fallback 선택",
          purpose: "공통 점검" },
        { seq: 2, kind: "deep_start", engine: "system",
          label: "심층 조사(HolmesGPT) 시작 — parallel",
          purpose: "두 번째 의견" },
    ]).map(function (w) { return w.text; }),
    steps: DV.conversationHeadLines({ kind: "alarm", status: "done", briefing: { playbook: {
        id: "fallback", sections: [{ step: "S4", title: "호스트 자원", status: "missing" }] } } })
        .filter(function (l) { return l.head === "조사 항목"; })[0].items,
};
""")
    assert out["words"] == "단계 상태 수집 결손 — 소견 없음"
    assert out["keep"] == "단계 상태 weird — 소견 없음"
    assert out["why"] == [
        "플레이북 공통 점검 선택 — 공통 점검",
        "심층 조사(HolmesGPT) 시작 — 병렬 — 두 번째 의견",
    ]
    assert out["steps"] == ["① 호스트 자원 — 수집 결손"]


def test_reason_detail_only_behind_detail_toggle_and_opinion_status_label() -> None:
    sections = [
        {
            "step": "S4",
            "title": "호스트 자원",
            "status": "missing",
            "finding": "수집 결손 — 서버를 찾지 못함",
            "reason_detail": "meta.episode.infra_db_ids 없음",
            "opinion": "",
            "opinion_dropped_reason": "단계 상태 missing — 소견 없음",
        },
        {"step": "S5", "title": "연관 알람", "status": "ok", "finding": "결과 0건"},
    ]
    final = _final(
        {"playbook": {"id": "fallback", "sections": sections}},
        [_ev(1, "S4", "step_end", status="missing")],
    )
    out = _node(
        _script(
            final,
            """
var card = findAll(tab.dom.flowBody, "dv-card")[0];
var toggle = findAll(card, "dv-flow-toggle")
    .filter(function (b) { return b.textContent === "ⓘ 상세"; })[0];
var detail = findAll(card, "dv-detail")[0];
var hidden = detail.hidden;
toggle.click();
out = { op: texts(card, "dv-opinion"), hidden: hidden, shown: !detail.hidden,
    text: detail.textContent,
    others: findAll(findAll(tab.dom.flowBody, "dv-card")[1], "dv-detail").length };
""",
        )
    )
    assert out["op"] == ["AI 해석 없음 — 단계 상태 수집 결손 — 소견 없음"]
    assert out["hidden"] is True and out["shown"] is True
    assert out["text"] == "meta.episode.infra_db_ids 없음"
    assert out["others"] == 0


# ─── D7 · 판정 근거 부족 줄 · HolmesGPT 요약 ─────────────────────────────


def test_done_without_hypothesis_shows_gap_counts_and_ai_summary() -> None:
    sections = [
        {"step": "S4", "title": "호스트 자원", "status": "missing", "finding": "수집 결손"},
        {"step": "S5a", "title": "재발 이력", "status": "skipped", "reason": _WAIT},
        {"step": "S5", "title": "연관 알람", "status": "ok", "finding": "판정 없음 · 결과 0건"},
        {"step": "S7", "title": "변경 대조", "status": "ok", "finding": "판정 없음 · 결과 0건"},
    ]
    briefing = {
        "playbook": {"id": "fallback", "sections": sections},
        "deep_dive": {"status": "done", "cause": "커넥션 풀 고갈 의심", "summary": "길다."},
    }
    out = _node(
        _script(
            _final(briefing),
            """
out = { sofar: tab.dom.sofarText.textContent, ai: tab.dom.sofarAi.textContent,
    hidden: tab.dom.sofarAi.hidden };
""",
            link_host=False,
        )
    )
    assert out["sofar"] == "판정 근거 부족 — 수집 결손 1단계 · 생략 1단계 · 0건 2단계"
    assert out["ai"] == "AI 판단 — 검증 전커넥션 풀 고갈 의심" and out["hidden"] is False


def test_gap_line_pure_and_hypothesis_wins() -> None:
    out = _node("""
var secs = [{ step: "S4", status: "missing", finding: "결손" }];
var top = { hypotheses: [{ rank: 1, title: "CPU 포화", status: "supported",
    status_label: "지지" }] };
out = {
    gap: DV.flowModel([], secs, { done: true }).sofar,
    running: DV.flowModel([], secs, { done: false }).sofar,
    top: DV.flowModel([], secs, { done: true, synthesis: top,
        deepDive: { status: "done", cause: "x" } }),
};
""")
    assert out["gap"] == "판정 근거 부족 — 수집 결손 1단계"
    assert out["running"] == ""
    assert out["top"]["sofar"].startswith("종합 — CPU 포화") and out["top"]["sofarAi"] == ""


def test_gap_line_groups_by_axis_status() -> None:
    """섹션 `axis_status`가 있으면 축별 집계.

    순서 폴스타 → 제니퍼 → ITSM → 기타 · 혼합 단계는 축마다 1.
    """
    out = _node("""
var secs = [
    { step: "S0", action: "scope.resolve_was_targets", status: "ok", axis_status: { apm: "ok" } },
    { step: "S4", action: "host.usage_trend", status: "missing",
        axis_status: { polestar: "missing" } },
    { step: "S5a", action: "host.alarm_history", status: "skipped",
        axis_status: { polestar: "skipped" } },
    { step: "S5", action: "xsource.related_alarms", status: "ok",
        axis_status: { apm: "ok", polestar: "skipped" } },
    { step: "S7", action: "change.recent", status: "ok",
        axis_status: { apm: "missing", polestar: "ok", itsm: "skipped" } },
    { step: "S8", action: "dpm.db_health", status: "skipped" },
    { step: "S9", action: "x.future", status: "ok", axis_status: { zzz: "ok" } },
];
out = DV.flowModel([], secs, { done: true }).sofar;
""")
    assert out == (
        "판정 근거 부족 — 폴스타: 수집 결손 1단계 · 생략 2단계 · 0건 1단계"
        " / 제니퍼: 수집 결손 1단계 · 0건 2단계 / ITSM: 생략 1단계"
        " / 기타: 0건 1단계"
    )


def test_gap_line_axis_less_skipped_card_not_counted() -> None:
    """축 상태 없는 건너뜀 카드(미연동 DPM 등)는 세지 않는다 · 축 없는 결손은 「기타」로 센다."""
    out = _node("""
var secs = [
    { step: "S1", action: "apm.golden_signals", status: "ok", axis_status: { apm: "ok" } },
    { step: "S8", action: "dpm.db_health", status: "skipped", reason: "not_connected" },
];
var miss = secs.concat([{ step: "S8b", action: "x.future", status: "missing" }]);
out = { skip: DV.flowModel([], secs, { done: true }).sofar,
    miss: DV.flowModel([], miss, { done: true }).sofar };
""")
    assert out["skip"] == "판정 근거 부족 — 제니퍼: 0건 1단계"
    assert out["miss"] == "판정 근거 부족 — 제니퍼: 0건 1단계 / 기타: 수집 결손 1단계"


def test_gap_line_without_axis_status_is_unchanged_and_rows_suppress_empty() -> None:
    """어느 섹션에도 `axis_status`가 없으면 종전 문장 그대로.

    결과 행이 있으면 ok 축은 0건이 아니다.
    """
    out = _node("""
var old = [{ step: "S4", status: "missing" }, { step: "S5", status: "ok" }];
var rows = [{ step: "S1", status: "missing", axis_status: { apm: "missing" } },
    { step: "S5", status: "ok", axis_status: { apm: "ok", polestar: "ok" },
      table: { columns: ["a"], rows: [[1]] } }];
out = { old: DV.flowModel([], old, { done: true }).sofar,
    rows: DV.flowModel([], rows, { done: true }).sofar };
""")
    assert out["old"] == "판정 근거 부족 — 수집 결손 1단계 · 0건 1단계"
    assert out["rows"] == "판정 근거 부족 — 제니퍼: 수집 결손 1단계"


# ─── §1.1 ② 폴스타 서버 선택 ─────────────────────────────────────────────


def _wait_final() -> dict:
    sections = [
        {"step": "S4", "title": "호스트 자원", "status": "skipped", "reason": _WAIT},
        {"step": "S5a", "title": "재발 이력", "status": "skipped", "reason": _WAIT},
    ]
    return _final({"playbook": {"id": "fallback", "sections": sections}})


_CANDS = {
    "hostname": "bfm-kbland-<b>price</b>",
    "source_id": "cm_prd",
    "link": None,
    "candidates": [
        {
            "db_id": "polestar_cm_gp",
            "server_name": "kbl-price-ap01",
            "hostname": "kbl-price-ap01",
            "ipaddress": "10.0.0.1",
            "grade": "유사",
            "grade_key": "similar",
            "score": 0.6,
            "reasons": ["디플로이먼트 이름 토큰 kbland·price 일치", "<img src=x onerror=alert(1)>"],
        },
        {
            "db_id": "polestar_cm_gp",
            "server_name": "kbl-price-ap02",
            "hostname": "kbl-price-ap02",
            "grade": "유사",
            "grade_key": "similar",
            "score": 0.5,
            "reasons": [],
        },
    ],
    "unchecked": [{"db_id": "polestar_b0", "reason": "시간 초과"}],
}


def test_wait_card_lists_candidates_and_rerun_opens_new_tab() -> None:
    out = _node(
        _script(
            _wait_final(),
            """
// 처음 그릴 때 1번 부른다 — 이미 불렀으면 다시 그려도 더 부르지 않는다
ctl.select("inv-f");
await flush();
var calls1 = calls.filter(function (u) { return u.indexOf("host-candidates") >= 0; }).length;
var cards = findAll(tab.dom.flowBody, "dv-card");
var hosts = cards.map(function (c) { return findAll(c, "dv-host").length; });
var box = findAll(cards[0], "dv-host")[0];
var names = texts(box, "dv-host-name");
var why = texts(box, "dv-host-why");
var warn = texts(box, "dv-note--warn");
var intro = texts(box, "dv-host-intro")[0];
// 고르지 않고 누르면 안내만
findAll(box, "dv-host-select")[0].click();
await flush();
var nopick = posts.length;
box = findAll(findAll(tab.dom.flowBody, "dv-card")[0], "dv-host")[0];
var r = findAll(box, "dv-host-radio")[1];
r.checked = true; r.fire("change");
findAll(box, "dv-host-select")[0].click();
await flush(); await flush();
out = { calls1: calls1, hosts: hosts, names: names, why: why, warn: warn, intro: intro,
    nopick: nopick, posts: posts, tabs: ctl.state().tabs, current: ctl.state().current,
    html: htmlWrites.filter(function (n) { return n.className.indexOf("dv-host") >= 0; }).length,
    msg: texts(tab.dom.flowBody, "dv-note").join("|") };
""",
            cands=_CANDS,
        )
    )
    assert out["calls1"] == 1
    assert out["hosts"] == [1, 0]  # 첫 대기 단계 카드에만
    assert out["names"] == ["kbl-price-ap01", "kbl-price-ap02"]
    assert out["why"] == ["디플로이먼트 이름 토큰 kbland·price 일치 · <img src=x onerror=alert(1)>"]
    assert out["warn"] == ["확인하지 못한 소스 — polestar_b0: 시간 초과"]
    assert "bfm-kbland-<b>price</b>" in out["intro"] and out["html"] == 0  # 텍스트 노드로만
    assert out["nopick"] == 0
    assert out["posts"] == [
        {
            "id": "inv-f",
            "body": {
                "action": "select",
                "db_id": "polestar_cm_gp",
                "server_name": "kbl-price-ap02",
                "hostname": "kbl-price-ap02",
            },
        }
    ]
    assert out["tabs"] == ["inv-f", "inv-r"] and out["current"] == "inv-r"
    assert "다시 조회를 접수했습니다" in out["msg"]


def _direct_script(body: str) -> str:
    empty = {"hostname": "web01", "source_id": "cm_prd", "candidates": [], "unchecked": []}
    return _script(
        _wait_final(),
        """
ctl.select("inv-f");
await flush();
function hostBox() { return findAll(tab.dom.flowBody, "dv-host")[0]; }
function warnText() { return texts(tab.dom.flowBody, "dv-note--warn").join("|"); }
async function press(cls) { findAll(hostBox(), cls)[0].click(); await flush(); await flush(); }
var input = findAll(hostBox(), "dv-host-input")[0];
input.value = " web01.kb.local "; input.fire("input");
"""
        + body,
        cands=empty,
    )


def test_zero_candidates_direct_input_real_flow() -> None:
    """후보 0건 직접 입력 — 본문은 db_id 없이 서버명만(본체가 인가 소스를 찾는다).
    거부 코드 `server_not_found`(400)·`server_ambiguous`(409)는 한국어 문구, 성공이면 새 탭."""
    out = _node(
        _direct_script("""
linkRes = { status: 400, body: { detail: { code: "server_not_found",
    reason: "그 소스에서 해당 서버를 찾지 못했습니다." } } };
await press("dv-host-select");
var notFound = warnText();
linkRes = { status: 409, body: { detail: { code: "server_ambiguous",
    reason: "같은 이름의 서버가 여러 대라 하나로 정하지 못했습니다." } } };
await press("dv-host-select");
var ambiguous = warnText();
linkRes = { status: 200, body: { link: { status: "linked", state: "pending" },
    investigation_id: "inv-r", status: "submitted" } };
await press("dv-host-select");
out = { posts: posts, notFound: notFound, ambiguous: ambiguous, tabs: ctl.state().tabs,
    msg: texts(tab.dom.flowBody, "dv-note").join("|") };
""")
    )
    sent = {"id": "inv-f", "body": {"action": "select", "server_name": "web01.kb.local"}}
    assert out["posts"] == [sent, sent, sent]
    assert "폴스타에서 그 서버를 찾지 못했습니다" in out["notFound"]
    assert "하나로 정하지 못했습니다 — 전체 이름(FQDN)으로 입력하세요" in out["ambiguous"]
    # 비운영자 응답의 link에는 by·at·investigation_id가 없다 — 그래도 그린다
    assert out["tabs"] == ["inv-f", "inv-r"]
    assert "다시 조회를 접수했습니다" in out["msg"] and "관리자 승인 전까지" in out["msg"]


def test_none_action_records_absent_and_locks_buttons() -> None:
    out = _node(
        _direct_script("""
linkRes = { status: 200, body: { link: { status: "absent" }, status: "saved" } };
await press("dv-host-none");
out = { posts: posts, tabs: ctl.state().tabs, msg: texts(tab.dom.flowBody, "dv-note").join("|"),
    disabled: findAll(tab.dom.flowBody, "dv-host-select")[0].disabled };
""")
    )
    assert out["posts"] == [{"id": "inv-f", "body": {"action": "none"}}]
    assert "폴스타 미등록으로 기록했습니다" in out["msg"]
    assert out["tabs"] == ["inv-f"] and out["disabled"] is True


def test_flag_off_404_or_no_link_callback_hides_selection() -> None:
    off = _node(
        _script(
            _wait_final(),
            """
ctl.select("inv-f");
await flush();
out = { host: findAll(tab.dom.flowBody, "dv-host").length,
    called: calls.filter(function (u) { return u.indexOf("host-candidates") >= 0; }).length };
""",
        )
    )
    assert off == {"host": 0, "called": 1}  # 하네스 기본 라우트 = 404
    nocb = _node(
        _script(
            _wait_final(),
            """
out = { host: findAll(tab.dom.flowBody, "dv-host").length,
    called: calls.filter(function (u) { return u.indexOf("host-candidates") >= 0; }).length };
""",
            link_host=False,
        )
    )
    assert nocb == {"host": 0, "called": 0}


def test_linked_investigation_shows_basis_and_change_button() -> None:
    sections = [{"step": "S4", "title": "호스트 자원", "status": "ok", "finding": "CPU 정상"}]
    final = _final(
        {
            "playbook": {
                "id": "fallback",
                "sections": sections,
                "scope": {"polestar_basis": "이전 확정 연결 — kim·2026-10-27 10:00"},
            }
        }
    )
    out = _node(
        _script(
            final,
            """
var before = calls.filter(function (u) { return u.indexOf("host-candidates") >= 0; }).length;
var basis = texts(tab.dom.flowBody, "dv-host-basis");
findAll(tab.dom.flowBody, "dv-host-change")[0].click();
await flush();
out = { before: before, basis: basis, names: texts(tab.dom.flowBody, "dv-host-name"),
    change: findAll(tab.dom.flowBody, "dv-host-change").length };
""",
            cands=_CANDS,
        )
    )
    assert out["before"] == 0  # 연결 변경을 누르기 전에는 후보를 부르지 않는다
    assert out["basis"] == ["폴스타 서버 연결 — 이전 확정 연결 — kim·2026-10-27 10:00"]
    assert out["names"] == ["kbl-price-ap01", "kbl-price-ap02"] and out["change"] == 0


def test_host_link_state_pure() -> None:
    out = _node("""
var c = function (step, because, skipRaw) {
    return { step: step, skipped: true, because: because, skipRaw: skipRaw }; };
out = [
    DV.hostLinkState([c("S1", "조건에 맞지 않아 생략", ""),
        c("S4", "x", "폴스타 서버 미확정 — 구 문구")], null),
    DV.hostLinkState([c("S4", "폴스타 미등록(사용자 확인 · kim·10:00)", "")], null),
    DV.hostLinkState([], { polestar_basis: "사용자 선택으로 연결 — lee·11:00" }),
    DV.hostLinkState([], { polestar_basis: "설정 매핑으로 대체(cm_prd→polestar_cm_gp)" }),
];
""")
    assert out[0] == {"mode": "wait", "step": "S4", "basis": ""}
    assert out[1]["mode"] == "absent" and out[1]["step"] == "S4"
    assert out[2] == {"mode": "linked", "step": "", "basis": "사용자 선택으로 연결 — lee·11:00"}
    assert out[3] is None


# ─── 정적 — 쓰기 경로는 app.js 주입 · 관리자 탭 ───────────────────────────


def test_write_path_injected_from_app_js_only() -> None:
    dv = _DV_JS.read_text(encoding="utf-8")
    assert 'host-link"' not in dv and "POST" not in dv
    app = _APP_JS.read_text(encoding="utf-8")
    body = app[
        app.index("linkHost: function") : app.index("}).catch", app.index("linkHost: function"))
    ]
    assert '"/host-link", "POST", body' in body


def test_admin_host_links_tab_wired_and_text_only() -> None:
    dash = _DASH.read_text(encoding="utf-8")
    assert 'data-tab="hostlinks"' in dash and 'id="tab-hostlinks"' in dash
    assert 'id="refreshHostLinksBtn"' in dash and "admin-host-links.js?v=1" in dash
    src = _HL_JS.read_text(encoding="utf-8")
    assert '"/api/v1/admin/host-links"' in src and '"/approve"' in src and '"DELETE"' in src
    assert "body.links" in src and '"link_changed"' in src
    assert "innerHTML" not in src and "insertAdjacentHTML" not in src


_HL_HARNESS = r"""
function El(tag) { this.tagName = tag; this.children = []; this._t = ""; this.style = {};
  this.className = ""; this.listeners = {}; }
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.addEventListener = function (k, f) { this.listeners[k] = f; };
Object.defineProperty(El.prototype, "textContent", {
  get: function () { return this._t + this.children.map(function (c) { return c.textContent; })
    .join(""); },
  set: function (v) { this._t = String(v); this.children = []; } });
globalThis.Node = El;
var ids = { hostLinksBody: new El("tbody"), hostLinksPatternBody: new El("tbody"),
  hostLinksNotice: new El("div"), refreshHostLinksBtn: new El("button") };
var ready = [];
globalThis.document = {
  getElementById: function (id) { return ids[id] || null; },
  createElement: function (t) { return new El(t); },
  addEventListener: function (k, f) { if (k === "DOMContentLoaded") ready.push(f); },
  querySelectorAll: function () { return []; } };
globalThis.localStorage = { getItem: function () { return "tok"; } };
globalThis.window = { confirm: function () { return true; } };
var reqs = [];
var LIST = __LIST__;
var APPROVE = { status: 409, body: { detail: { code: "link_changed", reason: "바뀜" } } };
globalThis.fetch = function (url, init) {
  reqs.push({ url: url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
  var hit = init.method === "GET" ? { status: 200, body: LIST } : APPROVE;
  return Promise.resolve({ ok: hit.status < 300, status: hit.status,
    json: function () { return Promise.resolve(hit.body); } });
};
function find(n, tag, text) {
  var out = [];
  (function walk(x) { if (x.tagName === tag && (!text || x._t === text)) out.push(x);
    x.children.forEach(walk); })(n);
  return out;
}
function wait() { return new Promise(function (r) { setTimeout(r, 20); }); }
"""


def test_admin_approve_sends_listed_row_and_reloads_on_link_changed() -> None:
    listed = {
        "links": [
            {
                "state": "pending",
                "kind": "link",
                "source_id": "cm_prd",
                "hostname": "web01",
                "db_id": "polestar_cm_gp",
                "server_name": "S1",
                "at": "2026-10-27T10:00:00+00:00",
            },
            {"state": "confirmed", "kind": "absent", "source_id": "cm_prd", "hostname": "pod-x"},
        ],
        "patterns": [
            {
                "key": "k8s|a|b",
                "kind": "k8s",
                "from": "a",
                "to": "b",
                "label": "K8s 해시 제거",
                "count": 3,
            }
        ],
    }
    out = _node(
        "\n".join(
            [
                _HL_HARNESS.replace("__LIST__", json.dumps(listed)),
                f"require({json.dumps(str(_HL_JS))});",
                "ready.forEach(function (f) { f(); });",
                "ids.refreshHostLinksBtn.listeners.click();",
                "await wait();",
                "var rows = ids.hostLinksBody.children.length;",
                "var who = ids.hostLinksBody.children[1].textContent;",
                "find(ids.hostLinksBody, 'button', '승인')[0].listeners.click();",
                "await wait();",
                "out = { rows: rows, who: who, reqs: reqs,",
                "  notice: ids.hostLinksNotice.textContent,",
                "  pattern: ids.hostLinksPatternBody.textContent };",
            ]
        )
    )
    assert out["rows"] == 2
    assert out["reqs"][1] == {
        "url": "/api/v1/admin/host-links/approve",
        "method": "POST",
        "body": {
            "source_id": "cm_prd",
            "hostname": "web01",
            "db_id": "polestar_cm_gp",
            "server_name": "S1",
            "at": "2026-10-27T10:00:00+00:00",
        },
    }
    assert out["reqs"][2]["method"] == "GET"  # 409 link_changed → 다시 읽음
    assert out["notice"] == "목록이 바뀌었습니다 — 새로 고친 뒤 다시 확인하세요"
    assert "K8s 해시 제거" in out["pattern"] and "3" in out["pattern"]
    # 선택자·시각이 없는 행은 「-」만(빈 「 · 」 없음)
    assert "부정(해당 서버 없음)" in out["who"] and " · " not in out["who"].split("pod-x")[1]
