"""plans/215 W9 · W11 2부 — 업무 현황 상세 「진단」 탭 화면(biz-monitor.js) 정적·node 검사.

브라우저 도구 없이 W8 검사 도구(가짜 DOM·타이머·fetch)로 mount 상태기계를 돌린다. 접수 거부 응답의
상태 코드를 고르려고 fetch만 이 파일에서 감싼다(`diagFetch` — 호출 기록은 W8 `calls`에 같이 쌓는다).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_plan215_w8_static import _BODIES, _FAKE_DOM, _node, _slots

_JS = Path(__file__).resolve().parents[1] / "src" / "static" / "js" / "biz-monitor.js"
_CSS = Path(__file__).resolve().parents[1] / "src" / "static" / "css" / "biz-monitor.css"
BASE = "/api/v1/biz-services/monitor"
CAPS = "/api/v1/alarm/capabilities"
_ON = {"biz_diagnosis_enabled": True, "investigation_enabled": True}

_D1 = {
    "service_id": 1, "display_name": "여신", "computed_at": "2026-11-05T01:00:00Z",
    "window_minutes": 15, "verdict": "R3", "verdict_text": "APM 이상 + 그 서버 OS 동반",
    "verdict_guidance": "서버 자원 쪽을 먼저 확인하세요", "hypothesis_label": "가설 · 참고용",
    "correlation_note": "최초 이상 순서는 「누가 먼저」만 보입니다 — 상관≠인과",
    "anomalous_members": [
        {"kind": "sms_server", "member_key": "pg|101", "name": "web-01", "layer": "web",
         "db_id": "pg", "resource_id": "101", "first_at": "2026-11-05T00:50:00Z",
         "signals": [{"basis": "alarm", "label": "알람 CPU 높음", "at": "2026-11-05T00:50:00Z",
                      "time_basis": "알람 발생"}],
         "linked_servers": [],
         "investigation": {"investigation_id": "inv-m", "status": "done",
                           "at": "2026-11-05T00:55:00Z", "cause_layer": "host_resource"}},
        {"kind": "apm_instance", "member_key": "apm|s1|d|instance|was-a", "name": "was-a",
         "layer": "was", "db_id": "", "resource_id": "", "first_at": "2026-11-05T00:52:00Z",
         "signals": [{"basis": "apm_signal", "label": "APM 신호 error_rate", "at": None,
                      "time_basis": "관측 시각"}],
         "linked_servers": ["pg|101"], "investigation": None},
    ],
    "timeline": [],
    "representative": {"kind": "sms_server", "member_key": "pg|101", "name": "web-01",
                       "db_id": "pg", "resource_id": "101", "rule": "first_sms",
                       "rule_label": "최초 이상 서버"},
    "representative_reason": "",
    "gaps": [{"code": "no_link", "label": "연계 없음", "count": 1, "member_keys": []},
             {"code": "out_of_scope", "label": "권한 밖", "count": 2, "member_keys": []}],
    "sources": {"m2": None, "metrics": "adhoc", "metrics_at": None,
                "calls": {"apm": 1, "sms": 1}},
    "partial": True, "notices": ["SMS 지표 원천 미설정 — SMS 임계 판정 불가"],
}
_HISTORY = {
    "service_id": 1, "limit": 10, "reason": "",
    "items": [{"investigation_id": "inv-h1", "status": "running",
               "submitted_at": "2026-11-05T00:40:00+00:00",
               "subject": {"type": "biz_service", "server_name": "web-01"}}],
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
var inits = [];
function diagFetch(url, init) {
    inits.push(init || {});
    var path = url.replace("/api/v1/biz-services/monitor", "");
    var key = (init && init.method || "GET") + " " + path;
    var fixed = RESP[key];
    if (!fixed) return fakeFetch(url, init);
    calls.push((init && init.method || "GET") + " " + url);
    return Promise.resolve({
        ok: fixed.status >= 200 && fixed.status < 300, status: fixed.status,
        json: function () { return Promise.resolve(fixed.body); },
    });
}
var opened = [];
function openInv(id) { opened.push(id); return true; }
"""


def _run(script: str, *, caps: dict[str, Any] | None,
         bodies: dict[str, Any] | None = None, resp: dict[str, Any] | None = None) -> Any:
    merged: dict[str, Any] = {**_BODIES, **(bodies or {})}
    if caps is not None:
        merged[CAPS] = caps
    return _node(_FAKE_DOM + _HELPERS + script, SLOTS=_slots(), BODIES=merged, RESP=resp or {})


_DIAG_BODIES = {"/1/diagnosis": _D1, "/1/investigations": _HISTORY}


# ─── 1. 정적 ────────────────────────────────────────────────────────────


def _segment() -> str:
    src = _JS.read_text(encoding="utf-8")
    return src[src.index("// ─── 「진단」 보기 탭") : src.index("// ─── mount")]


def test_diagnosis_segment_has_no_html_injection_confirm_or_new_window() -> None:
    seg = _segment()
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "confirm(", "window.open",
                   "_blank", "location.href", "정상"):
        assert banned not in seg, banned
    # 접수는 요청 본문 없이 POST만 보낸다(질문은 서버 코드 템플릿)
    assert 'ctx.request(diagUrl(ctx, "investigations"), "POST")' in seg
    assert "body:" not in seg and "JSON.stringify" not in seg


def test_tab_order_and_css_tokens() -> None:
    src = _JS.read_text(encoding="utf-8")
    tabs = src[src.index("var DETAIL_TABS = [") : src.index("];", src.index("var DETAIL_TABS = ["))]
    assert (tabs.index('id: "overview"') < tabs.index('id: "metrics"')
            < tabs.index('id: "events"') < tabs.index('id: "diagnosis"'))
    assert "biz_diagnosis_enabled === true" in tabs
    css = _CSS.read_text(encoding="utf-8")
    seg = css[css.index("(W9 · W11)") :]
    assert ".bm-diag-hypo" in seg and "var(--info)" in seg and "#" not in seg.split("@media")[0]


def test_pure_investigate_state_and_reject_text() -> None:
    out = _node(
        """
var on = {investigation_enabled: true};
out = [
  P.investigateState({representative: {name: "a"}}, on),
  P.investigateState({representative: null,
                      representative_reason: "이상 구성원 없음 — 대표 없음"}, on),
  P.investigateState({representative: {name: "a"}}, {}),
  P.investigateState({representative: {name: "a"}}, {investigation_enabled: false}),
  P.rejectText({status: 429, body: {detail: {code: "hourly_limit", reason: "상한",
                                              retry_after_seconds: 61}}}),
  P.rejectText({status: 409, body: {detail: {code: "x", reason: "r", reason_text: "문구"}}}),
  P.rejectText({status: 404, body: {detail: "없음"}, reason: "없음"}),
];
"""
    )
    assert out[0] == {"enabled": True, "reason": ""}
    assert out[1] == {"enabled": False, "reason": "이상 구성원 없음 — 대표 없음"}
    assert out[2]["enabled"] is False and "조사 서비스가 꺼져" in out[2]["reason"]
    assert out[3]["enabled"] is False
    assert out[4] == "상한 — 약 2분 뒤 다시 시도하세요"
    assert out[5] == "문구" and out[6] == "없음"


# ─── 2. mount ─────────────────────────────────────────────────────────────


def test_flag_off_no_tab_no_request_and_tab_param_falls_back() -> None:
    out = _run(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: diagFetch, service: "1", tab: "diagnosis",
    onRoute: function (r) { routes.push(r); }});
await flush();
var first = take();
await advance(120000);
out = {first: first, later: take(), tabs: tabs(), routes: routes};
""",
        caps={"biz_diagnosis_enabled": False, "investigation_enabled": True},
        bodies=_DIAG_BODIES,
    )
    every = out["first"] + out["later"]
    assert not [c for c in every if "/diagnosis" in c or "/investigations" in c]
    assert out["tabs"] == ["overview", "events"]
    assert out["routes"][-1] == {"service": "1", "tab": "overview"}


def test_panel_render_once_then_manual_refresh_and_pause_zero() -> None:
    out = _run(
        """
var routes = [];
var h = BM.mount(rootHost, {fetch: diagFetch, service: "1", tab: "diagnosis",
    onOpenInvestigation: openInv, onRoute: function (r) { routes.push(r); }});
await flush();
var first = take();
var panel = slot("detailPanel");
var text = panel.textContent;
var btn = all(panel, attr("data-bm-diag-investigate"))[0];
await advance(30000);
var tick = take();
button(panel, "새로고침").fire("click");
await flush();
var manual = take();
all(panel, attr("data-bm-diag-history", "inv-h1"))[0].children[3].children[0].fire("click");
all(panel, attr("data-bm-diag-member", "pg|101"))[0].children[5].children[1].fire("click");
h.pause();
await advance(300000);
out = {first: first, tabs: tabs(), route: routes[routes.length - 1], text: text,
       disabled: btn.disabled, tick: tick, manual: manual, paused: take(), opened: opened};
""",
        caps=_ON,
        bodies=_DIAG_BODIES,
    )
    assert out["tabs"] == ["overview", "events", "diagnosis"]
    assert out["route"] == {"service": "1", "tab": "diagnosis"}  # &tab=diagnosis 복원
    assert sorted(c for c in out["first"] if "/1" in c) == sorted([
        f"GET {BASE}/1", f"GET {BASE}/1/diagnosis", f"GET {BASE}/1/investigations"])
    t = out["text"]
    for want in ("APM 이상 + 그 서버 OS 동반", "가설 · 참고용", "서버 자원 쪽을 먼저 확인하세요",
                 "상관≠인과", "일부만 조회했습니다", "SMS 지표 원천 미설정", "연계 없음 1건",
                 "권한 밖 2건", "원인 영역 서버 자원(OS·메모리·디스크) · 완료",
                 "대표 서버: web-01 (최초 이상 서버)", "진단 이력", "진행 중", "지표 즉석 조회"):
        assert want in t, want
    assert t.index("web-01") < t.index("was-a")  # 최초 이상 순(서버가 정렬한 순서 그대로)
    assert "정상" not in t
    assert out["disabled"] is False
    # 30초 상세 갱신에 진단 요청 0 · 수동 [새로고침]만 다시 읽는다
    assert out["tick"] == [f"POST {BASE}/heartbeat", f"GET {BASE}/1"]
    assert sorted(out["manual"]) == [f"GET {BASE}/1/diagnosis", f"GET {BASE}/1/investigations"]
    assert out["paused"] == []
    assert out["opened"] == ["inv-h1", "inv-m"]  # 이력 행 · 구성원 조사 → 같은 창 장애 진단 탭


def test_preview_then_submit_posts_without_body_and_opens_same_window() -> None:
    out = _run(
        """
var h = BM.mount(rootHost, {fetch: diagFetch, service: "1", tab: "diagnosis",
    onOpenInvestigation: openInv});
await flush();
take(); inits = [];
var panel = slot("detailPanel");
all(panel, attr("data-bm-diag-investigate"))[0].fire("click");
await flush();
var preview = all(panel, attr("data-bm-diag-preview"))[0];
var pvText = preview ? preview.textContent : "";
var before = take();
all(panel, attr("data-bm-diag-submit"))[0].fire("click");
await flush();
var posted = take();
var result = all(panel, attr("data-bm-diag-result"))[0];
out = {pvText: pvText, before: before, posted: posted,
       bodies: inits.map(function (i) { return i.body === undefined; }),
       result: result && result.getAttribute("data-bm-diag-result"),
       resultText: result && result.textContent, opened: opened,
       noPreview: all(panel, attr("data-bm-diag-preview")).length};
""",
        caps=_ON,
        bodies=_DIAG_BODIES,
        resp={"POST /1/investigations": {"status": 200, "body": {
            "investigation_id": "inv-9", "status": "submitted", "reused": False,
            "service_id": 1, "excluded_members": 1, "truncated": False,
            "hypothesis_notice": "가설입니다 — 확인하세요",
            "representative": {"db_id": "pg", "server_name": "web-01", "hostname": "web-01"}}}},
    )
    assert "대표 서버 web-01 기준으로 최근 15분" in out["pvText"]
    assert "비교 구성원 후보 1대" in out["pvText"]
    assert out["before"] == []  # 미리보기는 요청 0 · 확인 대화상자 없이 같은 탭 안 패널
    assert out["posted"] == [f"POST {BASE}/1/investigations", f"GET {BASE}/1/investigations"]
    assert all(out["bodies"])  # 요청 본문 0
    assert out["result"] == "ok" and "서비스 심층 조사를 접수했습니다" in out["resultText"]
    assert "권한 밖 비교 구성원 1대 제외" in out["resultText"]
    assert out["opened"] == ["inv-9"] and out["noPreview"] == 0


def test_denied_submit_shows_reason_and_retry_without_opening() -> None:
    out = _run(
        """
var h = BM.mount(rootHost, {fetch: diagFetch, service: "1", tab: "diagnosis",
    onOpenInvestigation: openInv});
await flush();
var panel = slot("detailPanel");
all(panel, attr("data-bm-diag-investigate"))[0].fire("click");
all(panel, attr("data-bm-diag-submit"))[0].fire("click");
await flush();
var result = all(panel, attr("data-bm-diag-result"))[0];
out = {state: result.getAttribute("data-bm-diag-result"), text: result.textContent,
       opened: opened};
""",
        caps=_ON,
        bodies=_DIAG_BODIES,
        resp={"POST /1/investigations": {"status": 429, "body": {"detail": {
            "code": "hourly_limit", "reason": "시간당 사용자 요청 조사 상한(3건)에 도달했습니다",
            "retry_after_seconds": 600}}}},
    )
    assert out["state"] == "denied" and out["opened"] == []
    assert out["text"] == (
        "시간당 사용자 요청 조사 상한(3건)에 도달했습니다 — 약 10분 뒤 다시 시도하세요")


def test_button_disabled_with_reason_when_no_representative_or_no_service() -> None:
    no_rep = {**_D1, "representative": None,
              "representative_reason": "최초 이상 APM 구성원의 이어진 서버가 2대라 고르지 않습니다"}
    script = """
var h = BM.mount(rootHost, {fetch: diagFetch, service: "1", tab: "diagnosis",
    onOpenInvestigation: openInv});
await flush();
var panel = slot("detailPanel");
var btn = all(panel, attr("data-bm-diag-investigate"))[0];
btn.fire("click");
out = {disabled: btn.disabled, text: panel.textContent,
       preview: all(panel, attr("data-bm-diag-preview")).length};
"""
    a = _run(script, caps=_ON, bodies={**_DIAG_BODIES, "/1/diagnosis": no_rep})
    assert a["disabled"] is True and "이어진 서버가 2대라 고르지 않습니다" in a["text"]
    assert "대표 서버 없음" in a["text"] and a["preview"] == 0
    b = _run(script, caps={"biz_diagnosis_enabled": True}, bodies=_DIAG_BODIES)
    assert b["disabled"] is True and "조사 서비스가 꺼져 있어 접수할 수 없습니다" in b["text"]
    assert b["preview"] == 0
