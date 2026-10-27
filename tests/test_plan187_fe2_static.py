"""plans/187 FE-2 — 웹 프런트(W8 알려진 문제 · W9 KB 꼬리·관리자 탭 · W10 추가 조사 · W11 분석).

세 갈래를 겨눈다(W5b 테스트와 같은 방식 — node로 `briefing-view.js`를 실행 · 망·LLM 호출 0):
    1. 공용 렌더 — 새 키가 없으면 종전 렌더 그대로 · 기능 꺼짐(평문 404)이면 버튼을 숨기거나
       막고 사유를 보인다 · 서버 문자열은 모두 이스케이프한다.
    2. 소비자 정적 — 지난 알람 검색은 `/api/v1/noise/decisions`만 부르고 알람 탭 목록에
       끼우지 않는다 · break-glass 관제 세션에는 「이 알람 분석」이 없다 · 관리자 탭은
       textContent로만 그린다.
    3. HTML — 탭·정적 id·스크립트 포함 · 캐시 버전.
"""

from __future__ import annotations

import json
import re
from typing import Any

from tests.test_api.test_noise_dashboard_api import _js_function_body
from tests.test_plan187_w5b_static import _CASES, _STATIC, _done_view, _node, _render

_APP_JS = _STATIC / "js" / "app.js"
_NOISE_JS = _STATIC / "js" / "noise.js"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_KB_JS = _STATIC / "js" / "admin-incident-kb.js"
_XSS = '<img src=x onerror="alert(1)">'

# W5b 하네스 + querySelector(입력 칸) 대역 · 응답 모양 `{ok, status, body}` 도우미
_HARNESS = """
var handler = null;
var inputs = {};
var container = {innerHTML: "", isConnected: true,
    addEventListener: function (t, f) { handler = f; },
    contains: function () { return true; }, querySelectorAll: function () { return []; },
    querySelector: function (sel) {
        var m = /data-bv-input="([^"]+)"/.exec(sel);
        return m && inputs[m[1]] !== undefined ? {value: inputs[m[1]]} : null;
    }};
function click(attrs) {
    var el = {disabled: false,
      getAttribute: function (k) { return attrs[k] === undefined ? null : attrs[k]; }};
    handler({target: {closest: function () { return el; }}, preventDefault: function () {}});
}
function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }
function res(status, body) {
    return {ok: status >= 200 && status < 300, status: status, body: body || {}};
}
function has(html, text) { return html.indexOf(text) >= 0; }
"""

_KE = {
    "id": "KE-001",
    "title": "커넥션 풀 고갈",
    "state": "verified",
    "result": "match",
    "satisfied": ["db_wait"],
    "workaround": ["풀 크기 확대", "재기동"],
}


def _with_ke(ke: dict, name: str = "latency_sql") -> dict:
    view = _done_view(name)
    view["briefing"]["playbook"]["known_errors"] = [ke]
    return view


def _run(script: str, **values: Any) -> Any:
    """하네스 + JSON 값 주입 + 스크립트."""
    head = "".join(
        f"var {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in values.items()
    )
    return _node(_HARNESS + head + script)


# ─── 1. 새 키가 없으면 종전 렌더 그대로 ──────────────────────────────────


def test_attach_without_new_options_matches_plain_render() -> None:
    """새 attach 옵션이 없으면 extra에 새 키가 실리지 않는다 — renderBody 직접 호출과 같다."""
    out = _run(
        """
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    engineChoice: function () { return {ok: true, status: 200}; }});
await panel.open();
out = {got: container.innerHTML,
       want: BV.renderBody(view, {mode: "detail", engineChoice: true,
           ui: {choice: null, choiceMsg: "", choiceOff: "", pending: false}})};
panel.close();
""",
        view=_with_ke(_KE),
    )
    assert out["got"] == out["want"]
    for marker in ("bv-analyze", "bv-followup", "bv-tail", "ke-feedback", "지식 남기기"):
        assert marker not in out["got"]


def test_new_markers_absent_without_extra_flags_on_all_fixtures() -> None:
    markers = ("bv-analyze", "bv-followup", "bv-tail", 'data-bv-action="ke-feedback"')
    for name in _CASES:
        view = {**_done_view(name), "previous": []}
        for extra in ({"mode": "detail", "engineChoice": True}, {"openConversation": True}):
            out = _render(view, extra)
            # previous만으로는 렌더가 바뀌지 않는다
            assert out == _render(_done_view(name), extra), name
            for marker in markers:
                assert marker not in out, (name, marker)
    plain = {"status": "none", "previous": [{"investigation_id": "x"}]}
    assert _render(plain, {}) == _render({"status": "none"}, {})


def test_known_error_old_shape_renders_as_before() -> None:
    """W8 계약 칸(issue·cause·구조 우회 …)이 없으면 종전 한 줄 우회 · 새 칩 없음."""
    out = _render(_with_ke(_KE), {"mode": "detail"})
    block = out[out.index('<div class="bv-ke">'):]
    assert "우회" in block and "풀 크기 확대 · 재기동" in block
    for word in ("증상:", "원인:", "영구 해결", "최근 30일", "가설 1순위 고정", "<ol",
                 "bv-ke-feedback"):
        assert word not in block, word


def test_known_error_w8_fields_and_feedback_buttons() -> None:
    ke = {
        **_KE,
        "badge": "검증됨",
        "pinned": True,
        "stale": True,
        "issue": "응답 지연",
        "cause": "풀 고갈",
        "unsatisfied": ["gc_pause"],
        "workaround": [
            {"action": "풀 확대", "risk": "낮음", "verification": "대기 0",
             "rollback": "원복", "review_only": True},
        ],
        "permanent_fix": {"status": "planned", "problem_ref": "PRB-9"},
        "owner": "DBA팀",
        "last_reviewed": "2026-09-01",
        "recurrence": {"count_30d": 4, "last": "2026-10-18T00:00:00+00:00"},
    }
    out = _render(_with_ke(ke), {"mode": "detail", "keFeedback": True, "ui": {}})
    expected = (
        "증상: 응답 지연", "원인: 풀 고갈", "미충족 조건: gc_pause",
        "영구 해결: planned · PRB-9", "담당 DBA팀", "최근 30일 일치 4회", "가설 1순위 고정",
        "검토 전용", "위험도 낮음", "이 알려진 문제가 맞나요?",
    )
    for text in expected:
        assert text in out, text
    for verdict in ("correct", "incorrect", "workaround_ineffective"):
        assert f'data-ke-id="KE-001" data-verdict="{verdict}"' in out
    assert ">맞음<" in out and ">아님<" in out and ">우회 효과 없음<" in out


# ─── 2. 기능 꺼짐(평문 404) · 사유 ─────────────────────────────────────


def test_flag_off_is_plain_404_only() -> None:
    out = _node(
        """
out = [BV.isFlagOff({status: 404, body: {detail: "Not Found"}}),
       BV.isFlagOff({status: 404, body: {detail: {code: "alarm_not_found", reason: "r"}}}),
       BV.isFlagOff({status: 403, body: {detail: "x"}}),
       BV.reasonOf({status: 429, body: {detail: {code: "hourly_limit", reason: "시간당 상한"}}}),
       BV.reasonOf({status: 502, body: {}})];
"""
    )
    assert out == [True, False, False, "시간당 상한", "HTTP 502"]


def test_ke_feedback_records_and_404_disables_kb_buttons() -> None:
    out = _run(
        """
var calls = [];
var status = 200;
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    kb: {operator: true,
         draft: function () { calls.push("draft"); return res(200, {}); },
         postmortem: function () { return res(200, {}); }},
    keFeedback: function (id, v) {
        calls.push([id, v]);
        return status === 200 ? res(200, {recorded: true}) : res(status, {detail: "Not Found"});
    }});
await panel.open();
click({"data-bv-action": "ke-feedback", "data-ke-id": "KE-001", "data-verdict": "incorrect"});
await tick();
var recorded = container.innerHTML;
status = 404;
click({"data-bv-action": "ke-feedback", "data-ke-id": "KE-001", "data-verdict": "correct"});
await tick();
var off = container.innerHTML;
click({"data-bv-action": "kb-draft"});
await tick();
out = {calls: calls,
       recorded: has(recorded, "기록했습니다 — 아님")
           && /data-verdict="incorrect" aria-pressed="true"/.test(recorded),
       off: has(off, "조사 지식(KB) 기능이 꺼져 있어")
           && /data-verdict="correct"[^>]*disabled/.test(off)
           && /data-bv-action="kb-draft" disabled/.test(off)
           && /data-bv-action="postmortem" disabled/.test(off)};
panel.close();
""",
        view=_with_ke(_KE),
    )
    calls = [["KE-001", "incorrect"], ["KE-001", "correct"]]  # 꺼진 뒤 초안 호출은 없다
    assert out == {"calls": calls, "recorded": True, "off": True}


def test_kb_tail_operator_only_and_postmortem_criteria() -> None:
    view = _done_view("gc")
    user = _render(view, {"mode": "detail", "kb": {"operator": False}, "ui": {}})
    assert "포스트모템 내보내기" in user and "KB 초안 만들기" not in user
    operator = {"mode": "detail", "kb": {"operator": True}, "ui": {}}
    assert "KB 초안 만들기" in _render(view, operator)
    assert "지식 남기기" not in _render({**view, "status": "running"}, operator)
    out = _run(
        """
var n = 0;
var unmet = {code: "criteria_unmet", reason: "조건 미충족",
             criteria: [{id: "c1", label: "원인 확정", met: false, detail: "가설뿐"}]};
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    kb: {operator: true,
         draft: function () { return res(200, {draft_id: "d-1", yaml: "id: KE-NEW\\n"}); },
         postmortem: function () {
             n += 1;
             return n === 1 ? res(409, {detail: unmet})
                 : res(200, {markdown: "# 포스트모템\\n",
                             criteria: [{id: "c1", label: "원인 확정", met: true}]});
         }}});
await panel.open();
click({"data-bv-action": "postmortem"});
await tick();
var first = container.innerHTML;
click({"data-bv-action": "postmortem"});
await tick();
var done = container.innerHTML;
click({"data-bv-action": "kb-draft"});
await tick();
var last = container.innerHTML;
out = {unmet: has(first, "포스트모템을 내보내지 못했습니다 — 조건 미충족")
           && has(first, "미충족") && has(first, "원인 확정") && !has(first, "내려받기(.md)"),
       done: has(done, "# 포스트모템") && has(done, "내려받기(.md)"),
       draft: has(last, "KB 초안을 만들었습니다 — d-1") && has(last, "id: KE-NEW")};
panel.close();
""",
        view=view,
    )
    assert out == {"unmet": True, "done": True, "draft": True}


def test_followup_hidden_on_404() -> None:
    out = _run(
        """
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    followup: {list: function () { return res(404, {detail: "Not Found"}); },
               submit: function () { throw new Error("no"); },
               loadById: function () { return view; }}});
await panel.open();
await tick();
out = !has(container.innerHTML, "bv-followup") && !has(container.innerHTML, "followup-submit");
panel.close();
""",
        view=_done_view("latency_sql"),
    )
    assert out is True


def test_followup_lists_focuses_submits_and_shows_reason() -> None:
    focuses = [
        {"id": "top_transaction", "label": "1위 트랜잭션 원인", "active": True},
        {"id": "db_wait", "label": "DB 측 대기", "active": False, "reason": "DB 단계 없음"},
    ]
    item = {**_done_view("gc"), "investigation_id": "inv-fu-1", "kind": "diagnosis",
            "focus": "top_transaction", "submitted_at": "2026-10-19T02:00:00+00:00"}
    out = _run(
        """
var sent = [];
var lists = 0;
var limit = {code: "hourly_limit", reason: "시간당 상한 초과"};
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    followup: {
        list: function () {
            lists += 1;
            return res(200, {focuses: focuses, items: lists > 1 ? [item] : [],
                             hypothesis_notice: "가설입니다"});
        },
        submit: function (f, n) {
            sent.push([f, n]);
            return sent.length === 1 ? res(429, {detail: limit})
                : res(200, {investigation_id: "inv-fu-1", status: "submitted", reused: false});
        },
        loadById: function () { return item; }}});
await panel.open();
await tick();
var listed = container.innerHTML;
inputs["fu-focus"] = "top_transaction";
inputs["fu-note"] = "배치와 겹치나";
click({"data-bv-action": "followup-submit"});
await tick();
var limited = container.innerHTML;
click({"data-bv-action": "followup-submit"});
await tick();
await tick();
var last = container.innerHTML;
out = {sent: sent, lists: lists,
       listed: has(listed, 'data-bv-action="followup-submit"')
           && /<option value="db_wait" disabled>DB 측 대기 — DB 단계 없음/.test(listed)
           && has(listed, "아직 추가 조사가 없습니다.") && has(listed, "통보되지 않습니다"),
       limited: has(limited, "추가 조사를 요청하지 못했습니다 — 시간당 상한 초과"),
       item: has(last, 'data-investigation-id="inv-fu-1"') && has(last, "1위 트랜잭션 원인")};
panel.close();
""",
        view=_done_view("latency_sql"),
        focuses=focuses,
        item=item,
    )
    note = ["top_transaction", "배치와 겹치나"]
    assert out == {"sent": [note, note], "lists": 2, "listed": True, "limited": True,
                   "item": True}


def test_analyze_hidden_without_previous() -> None:
    out = _run(
        """
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    analyze: {submit: function () {}, loadById: function () {},
              probe: function () { return res(200, {status: "none"}); }}});
await panel.open();
await tick();
out = !has(container.innerHTML, "분석 요청");
panel.close();
""",
        view={"status": "none"},
    )
    assert out is True
    # analyze 표지 없이 ui만 있으면(attach가 previous를 못 본 상태) 그리지 않는다
    none_with_previous = {"status": "none", "previous": []}
    assert "분석 요청" not in _render(none_with_previous, {"analyze": True, "ui": {}})


def test_analyze_404_disables_with_reason() -> None:
    out = _run(
        """
var calls = 0;
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    analyze: {submit: function () { calls += 1; return res(404, {detail: "Not Found"}); },
              loadById: function () {}}});
await panel.open();
var before = container.innerHTML;
click({"data-bv-action": "analyze"});
await tick();
var after = container.innerHTML;
click({"data-bv-action": "analyze"});
await tick();
out = {calls: calls,
       before: has(before, ">분석 요청</button>")
           && has(before, "통보(메신저·문자)로 나가지 않습니다"),
       off: /data-bv-action="analyze" disabled/.test(after)
           && has(after, "알람 다시 분석 기능이 꺼져 있습니다")};
panel.close();
""",
        view={"status": "none", "previous": []},
    )
    assert out == {"calls": 1, "before": True, "off": True}


def test_analyze_follows_response_id_and_lists_previous() -> None:
    previous = [{"investigation_id": "inv-old", "kind": "alarm", "status": "done",
                 "submitted_at": "2026-10-18T00:00:00+00:00"}]
    fresh = {**_done_view("normal"), "investigation_id": "inv-new", "kind": "manual"}
    out = _run(
        """
var loaded = [];
var accepted = {investigation_id: "inv-new", status: "submitted", joined: false,
                reference_time_source: "decision_ts"};
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "summary", openConversation: true}, onOpenConversation: function () {},
    analyze: {submit: function () { return res(200, accepted); },
              loadById: function (id) { loaded.push(id); return fresh; }}});
await panel.open();
var before = container.innerHTML;
click({"data-bv-action": "analyze"});
await tick();
await tick();
var after = container.innerHTML;
out = {loaded: loaded,
       again: has(before, ">다시 분석</button>") && has(before, "이전 조사 1건")
           && has(before, "inv-old"),
       after: has(after, "분석을 요청했습니다 — inv-new") && has(after, "판정 시각을 기준으로")
           && has(after, 'data-investigation-id="inv-new"') && has(after, "이전 조사 1건")};
panel.close();
""",
        view={**_done_view("gc"), "previous": previous},
        fresh=fresh,
    )
    assert out == {"loaded": ["inv-new"], "again": True, "after": True}


def test_analyze_no_follow_offers_new_conversation() -> None:
    out = _run(
        """
var opened = [];
var joined = {investigation_id: "inv-j", status: "running", joined: true};
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    onOpenConversation: function (id) { opened.push(id); },
    analyze: {follow: false, probe: function () { return res(200, {previous: []}); },
              submit: function () { return res(200, joined); },
              loadById: function () { throw new Error("no"); }}});
await panel.open();
await tick();
click({"data-bv-action": "analyze"});
await tick();
var html = container.innerHTML;
click({"data-bv-action": "open-conversation", "data-investigation-id": "inv-j"});
out = {joined: has(html, "요청 조사에 합류했습니다 — inv-j")
           && has(html, "새 조사로 조사 대화 열기"),
       opened: opened};
panel.close();
""",
        view=_done_view("gc"),
    )
    assert out == {"joined": True, "opened": ["inv-j"]}


# ─── 3. XSS ──────────────────────────────────────────────────────────


def test_xss_in_new_blocks_is_escaped() -> None:
    ke = {**_KE, "id": "KE-1", "title": _XSS, "issue": _XSS, "owner": _XSS,
          "workaround": [{"action": _XSS, "risk": _XSS}]}
    view = {**_with_ke(ke), "previous": []}
    ui = {
        "analyze": {"enabled": True, "msg": _XSS,
                    "previous": [{"investigation_id": _XSS, "kind": _XSS, "status": "done"}]},
        "followup": {"focuses": [{"id": _XSS, "label": _XSS, "active": False, "reason": _XSS}],
                     "items": [], "notice": _XSS, "msg": _XSS},
        "ke": {"KE-1": {"msg": _XSS}},
        "kb": {"draftYaml": _XSS, "pmMarkdown": _XSS, "pmMsg": _XSS,
               "pmCriteria": [{"id": "c", "label": _XSS, "detail": _XSS}]},
    }
    extra = {"mode": "detail", "openConversation": True, "analyze": True, "followup": True,
             "keFeedback": True, "kb": {"operator": True}, "ui": ui}
    out = _render(view, extra)
    assert "<img" not in out and 'onerror="' not in out
    assert out.count("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;") >= 10


# ─── 4. 소비자 정적 ───────────────────────────────────────────────────


def test_past_alarm_search_calls_only_decisions_and_never_touches_alarm_list() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    search = _js_function_body(src, "searchPastAlarms")
    assert search.count("fetch(") == 1 and '"/api/v1/noise/decisions?"' in search
    assert '"/api/v1/admin' not in search
    names = ("renderPastAlarmRow", "buildPastAlarmCard", "setupAlarmPastSearch", "clearPastPanels")
    bodies = [search] + [_js_function_body(src, f) for f in names]
    forbidden = ("alarmList", "renderAlarmMessage", "updateAlarmViewState", "alarmTabBadge",
                 "innerHTML", "alarmUnread")
    for body in bodies:
        for word in forbidden:
            assert word not in body, word
    # 행 값은 textContent로만
    assert "textContent = text" in _js_function_body(src, "pastText")
    # 카드는 by-alarm 조회 + 분석 요청 + 조사 진입(plans/192 P4 — 진단 켜짐이면 진단 하위 탭)
    card = _js_function_body(src, "buildPastAlarmCard")
    assert "/api/v1/alarm/investigations/by-alarm/" in card
    assert "analyzeOptions(item.alarm_id)" in card and "openInvestigationEntry" in card


def test_past_alarm_section_sits_outside_alarm_list() -> None:
    html = (_STATIC / "index.html").read_text(encoding="utf-8")
    past = html.index('id="alarmPast"')
    assert past < html.index('id="alarmTools"') < html.index('id="alarmList"')
    section = html[past:html.index("</details>", past)]
    assert 'id="alarmList"' not in section
    for ident in ("alarmPastRange", "alarmPastQuery", "alarmPastSearchBtn", "alarmPastList"):
        assert f'id="{ident}"' in section
    for value in ("1h", "24h", "7d", "30d"):
        assert f'value="{value}"' in section


def test_app_wiring_for_pin_and_alarm_card() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    pin = _js_function_body(src, "renderInvestigationPin")
    for key in ('"/followups"', '"/followup"', '"/feedback"', '"/kb-draft"', '"/postmortem"',
                "operator: alarmOperator", "follow: false"):
        assert key in pin, key
    bind = _js_function_body(src, "bindAlarmBriefing")
    assert "analyze: data.alarm_id ? analyzeOptions(data.alarm_id) : null" in bind
    # W5b 고정 문자열 → (plans/192 P4) 클릭 시점의 진단 판정(openInvestigationEntry)
    entry = "onOpenConversation: function (id) { openInvestigationEntry(id, data.alarm_id); }"
    assert entry in bind
    opts = _js_function_body(src, "analyzeOptions")
    assert '"/api/v1/alarm/" + encodeURIComponent(alarmId) + "/investigations", "POST"' in opts
    init = _js_function_body(src, "initAlarmSubscription")
    assert 'alarmOperator = (!authEnabled) || !!(userInfo && userInfo.role === "admin")' in init


def test_noise_console_analyze_is_user_session_only() -> None:
    src = _NOISE_JS.read_text(encoding="utf-8")
    assert "if (IS_BREAK_GLASS) return null;" in _js_function_body(src, "analyzeOptions")
    assert "if (IS_BREAK_GLASS) return;" in _js_function_body(src, "renderAnalyzeOnly")
    act = _js_function_body(src, "actionCell")
    assert "!IS_BREAK_GLASS && item.alarm_id && analyzeAvailable !== false" in act
    # 첫 버튼(포커스 복귀 대상)은 그대로 「결정 추적」
    assert act.index('"결정 추적"') < act.index('"이 알람 분석"')
    inv = _js_function_body(src, "renderInvestigation")
    assert "analyze: analyzeOptions(alarmId)" in inv
    # 사용자 토큰 직접 호출(apiRequest 401 → 로그아웃 경로를 타지 않는다)
    assert "apiRequest" not in _js_function_body(src, "alarmCall")


def test_admin_incident_kb_tab_and_script() -> None:
    html = (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert '<button class="tab" data-tab="incidentkb">조사 지식</button>' in html
    assert 'id="tab-incidentkb"' in html and 'id="refreshIncidentKbBtn"' in html
    assert '<script src="/static/js/admin-incident-kb.js?v=1"></script>' in html
    js = _KB_JS.read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js
    assert '"/api/v1/admin/incident-kb"' in js and '"/summary"' in js and '"/drafts"' in js
    assert "NOISE_INVESTIGATION_KB_ENABLED를 켜고 본체를 재기동" in js
    assert re.search(r'status === 404 && !\(detail && typeof detail === "object"\)', js)


def test_renderer_still_assigns_markup_only_in_attach() -> None:
    bv = _BV_JS.read_text(encoding="utf-8")
    attach = _js_function_body(bv, "attach")
    assert bv.count("innerHTML") == attach.count("innerHTML")
    assert "eval(" not in bv and "new Function" not in bv


def test_cache_versions_bumped() -> None:
    pages = {
        "index.html": ("style.css?v=42", "briefing-view.js?v=8", "app.js?v=42"),
        "noise.html": ("style.css?v=18", "briefing-view.js?v=8", "noise.js?v=24"),
        "admin/noise.html": ("style.css?v=16", "briefing-view.js?v=8", "noise.js?v=24"),
        "admin/dashboard.html": ("style.css?v=23", "admin-incident-kb.js?v=1"),
    }
    for page, assets in pages.items():
        text = (_STATIC / page).read_text(encoding="utf-8")
        for asset in assets:
            assert asset in text, (page, asset)
