"""plans/192 W1·W2·W4 독립 검증(verifier) — 구현자 테스트가 덮지 않은 경계만 둔다.

- P1: `login.html` `safeNext`를 WHATWG URL 파서(node `URL`)로 해석해 **어떤 입력도 다른 출처로
  나가지 않는다**(인코딩 `%2F%2F`·`/%5C`·유니코드 슬래시·공백 앞머리·`/.//`·제어문자 + 2글자 전수).
  `redirectToLogin` → 로그인 → `safeNext` 왕복이 원래 주소로 정확히 돌아오는지도 본다.
- P4: 알람 카드·지난 알람의 「분석 요청/다시 분석」은 follow 기본(true) + loadById가 있어
  `a.openId`(「새 조사로 조사 대화 열기」 — open-conversation)가 생기지 않는다
  → 그 버튼이 진단 쪽으로 잘못 갈 경로가 없다.
- W4 openThread 경합(교정 고정): 조사 대화 탭에서 고정 카드 없는 이력을 누른 뒤 받는 사이
  다른 뷰 탭으로 옮겨도, 받은 대화가 **조사 대화 슬롯**을 덮지 않는다. 복원 경로는 이력
  저장소가 없을 때(503 등)도 같은 조사로 새 조사 대화를 열어 새로고침마다 막히지 않는다.

node가 없으면 생략한다. 하네스는 구현자 테스트
(`test_ui_plan192_w1_entry`·`_w4_conversation_restore`)를 쓴다.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.test_api.test_ui_plan192_w1_entry import (
    _LOGIN_PRELUDE,
    APP_JS,
    LOGIN_HTML,
    body_of,
    node_run,
)
from tests.test_api.test_ui_plan192_w4_conversation_restore import (
    PINNED,
    PLAIN,
    conv_run,
)

_ORIGIN = "http://portal.internal:8000"

# 로그인 화면 safeNext 실물 + location.search 가짜
_SAFE_NEXT_PRELUDE = r"""
var window = globalThis;
var curSearch = "";
window.location = {};
Object.defineProperty(window.location, "search", { get: function () { return curSearch; } });
function nextFor(rawQueryValue) {
    curSearch = "?next=" + rawQueryValue;
    return safeNext();
}
function landsAt(n) { return new URL(n, "http://portal.internal:8000/login" + curSearch); }
"""


def _safe_next_run(body: str) -> Any:
    html = LOGIN_HTML.read_text(encoding="utf-8")
    return node_run(_SAFE_NEXT_PRELUDE, body_of(html, "safeNext", "login.html"), body)


# 쿼리 문자열에 실린 그대로(디코딩 전)의 next 값 — 브라우저 URL 파서 기준 우회 시도
_BYPASS_RAW = [
    "%2F%2Fevil.com",
    "/%2Fevil.com",
    "%2F%5Cevil.com",
    "/%5Cevil.com",
    "%5C%5Cevil.com",
    "%2F%E2%88%95evil.com",  # U+2215 DIVISION SLASH
    "%2F%EF%BC%8Fevil.com",  # U+FF0F FULLWIDTH SOLIDUS
    "%2F%EF%BC%BCevil.com",  # U+FF3C FULLWIDTH REVERSE SOLIDUS
    "%E2%81%84%2Fevil.com",  # U+2044 FRACTION SLASH 앞머리
    "%20%2F%2Fevil.com",
    "+//evil.com",
    "%2F.%2F%2Fevil.com",
    "/.//evil.com",
    "/..//evil.com",
    "/../..//evil.com",
    "%2F%09%2Fevil.com",
    "%2F%0A%2Fevil.com",
    "%2F%0D%2Fevil.com",
    "%2F%00%2Fevil.com",
    "%2F%7F%2Fevil.com",
    "%2F%E3%80%80%2Fevil.com",
    "%2F%C2%A0%2Fevil.com",
    "%2F%E2%80%8B%2Fevil.com",
    "%2F%E2%80%A8%2Fevil.com",
    "%2F%EF%BB%BF%2Fevil.com",
    "/%252F%252Fevil.com",
    "/%255Cevil.com",
    "%2F%2509%2Fevil.com",
    "%2F@evil.com",
    "%2F%3A%2F%2Fevil.com",
    "javascript:alert(1)",
    "%2Fjavascript:alert(1)",
    "https:%2F%2Fevil.com",
    "%2F%2F%2Fevil.com",
]


def test_safe_next_bypass_inputs_never_leave_origin() -> None:
    out = _safe_next_run(
        f"""
var raws = {json.dumps(_BYPASS_RAW)};
out = raws.map(function (r) {{
    var n = nextFor(r);
    return {{ raw: r, next: n, origin: landsAt(n).origin }};
}});
"""
    )
    leaks = [o for o in out if o["origin"] != _ORIGIN]
    assert not leaks, leaks
    # 걸러진 값은 반드시 맨 루트로 — 앞머리가 `/`·`/` 아닌 다른 것으로 바뀐 채 통과하지 않는다
    assert all(o["next"].startswith("/") and not o["next"].startswith("//") for o in out)


def test_safe_next_two_char_exhaustive_never_leaves_origin() -> None:
    # `/` 뒤 두 글자(U+0000~U+2FFF + 슬래시류)를 전수로 넣는다 — 하나라도 다른 출처면 실패
    out = _safe_next_run(
        r"""
var cps = [];
for (var i = 0; i < 0x3000; i++) cps.push(i);
[0x3000, 0xfeff, 0xff0f, 0xff3c, 0x2215, 0x2044].forEach(function (x) { cps.push(x); });
var leaks = [];
var total = 0;
cps.forEach(function (a) {
    [0x2f, 0x5c, a].forEach(function (b) {
        // 짝 없는 서로게이트는 encodeURIComponent가 던진다
        if (a >= 0xd800 && a <= 0xdfff) return;
        var s = "/" + String.fromCodePoint(a) + String.fromCodePoint(b) + "evil.com";
        var n = nextFor(encodeURIComponent(s));
        total += 1;
        if (landsAt(n).origin !== "http://portal.internal:8000") leaks.push(s);
    });
});
out = { total: total, leaks: leaks.slice(0, 10) };
"""
    )
    assert out["total"] > 30000
    assert out["leaks"] == []


# redirectToLogin(app.js) → /login?next=… → safeNext(login.html) 왕복
_ROUND_TRIP_CASES = [
    # (pathname, search, hash, 기대 착지 — None이면 맨 루트)
    ("/", "?diagnosis=inv-1", "", "/?diagnosis=inv-1"),
    ("/", "?conversation=inv-1&x=%2F%2Fy", "", "/?conversation=inv-1&x=%2F%2Fy"),
    ("/", "?investigation=a.b:c-d_1", "#frag", "/?investigation=a.b:c-d_1#frag"),
    ("/", "?q=%E2%88%95%2F", "#%20x", "/?q=%E2%88%95%2F#%20x"),
    ("//evil.com/", "?diagnosis=inv-1", "", None),
    ("//evil.com", "", "", None),
    ("/", "", "", None),
]


@pytest.mark.parametrize("pathname,search,hash_,expect", _ROUND_TRIP_CASES)
def test_redirect_login_round_trip_lands_on_same_origin(
    pathname: str, search: str, hash_: str, expect: str | None
) -> None:
    src = APP_JS.read_text(encoding="utf-8")
    html = LOGIN_HTML.read_text(encoding="utf-8")
    defs = "\n".join(
        [body_of(src, "redirectToLogin", "app.js"), body_of(html, "safeNext", "login.html")]
    )
    out = node_run(
        _LOGIN_PRELUDE,
        defs,
        f"""
window.location.pathname = {json.dumps(pathname)};
window.location.search = {json.dumps(search)};
window.location.hash = {json.dumps(hash_)};
redirectToLogin();
var login = new URL(navigated[0], "{_ORIGIN}/");
window.location.search = login.search;
var n = safeNext();
var land = new URL(n, login.href);
out = {{ nav: navigated[0], next: n, origin: land.origin,
    path: land.pathname + land.search + land.hash }};
""",
    )
    assert out["origin"] == _ORIGIN
    if expect is None:
        assert out["nav"] == "/login"
        assert out["next"] == "/"
    else:
        assert out["next"] == expect
        assert out["path"] == expect


# ─── P4: 알람 카드·지난 알람의 「분석 요청」은 follow 기본이라 open-conversation이 생기지 않는다 ──


def test_alarm_card_and_past_alarm_analyze_follows_so_no_open_conversation_button() -> None:
    src = APP_JS.read_text(encoding="utf-8")
    card = body_of(src, "bindAlarmBriefing", "app.js")
    past = body_of(src, "buildPastAlarmCard", "app.js")
    # follow:false(→ a.openId → 「새 조사로 조사 대화 열기」)를 넘기지 않는다
    assert re.search(r"analyze: data\.alarm_id \? analyzeOptions\(data\.alarm_id\) : null,", card)
    assert re.search(r"analyze: analyzeOptions\(item\.alarm_id\),", past)
    assert "follow: false" not in card and "follow: false" not in past
    # analyzeOptions 실물 — 인자 하나면 follow true + loadById 있음
    # (공용 렌더가 openId를 만들지 않는 조건)
    out = node_run(
        r"""
function bvRequest() { return Promise.resolve({}); }
function followToDiagnosis(r) { return r; }
function loadInvestigationView() { return Promise.resolve({}); }
""",
        body_of(src, "analyzeOptions", "app.js"),
        r"""
var a = analyzeOptions("al-1");
var b = analyzeOptions("al-1", { follow: false, probe: true });
out = { follow: a.follow, load: typeof a.loadById, probe: a.probe, pinFollow: b.follow };
""",
    )
    assert out == {"follow": True, "load": "function", "probe": None, "pinFollow": False}


def test_briefing_view_open_id_only_when_follow_false_or_no_loader() -> None:
    # 공용 렌더가 openId(→ open-conversation 버튼)를 만드는 조건이 이 둘뿐인지 고정한다 —
    # 조건이 넓어지면 알람 카드의 open-conversation(지금은 openInvestigationEntry)이
    # 진단으로 잘못 갈 수 있다
    bv = (APP_JS.parent / "briefing-view.js").read_text(encoding="utf-8")
    hits = re.findall(r"a\.openId = ", bv)
    assert len(hits) == 1
    cond = "if (opts.analyze.follow === false || !opts.analyze.loadById) {"
    assert cond + "\n                    a.openId = id;" in bv


# ─── W4 openThread 경합 ───────────────────────────────────────────────

_DEFERRED_FETCH = r"""
var held = [];
function fetch(url) {
    fetches.push({ url: url, view: activeView, slot: conversationSlot });
    var id = decodeURIComponent(String(url).split("/").pop());
    return new Promise(function (resolve) {
        held.push(function () {
            var b = threadBodies[id];
            var status = b ? (b.status || 200) : 404;
            resolve({ status: status, ok: status >= 200 && status < 300,
                json: function () { return Promise.resolve({ turns: b ? b.turns : [] }); } });
        });
    });
}
function release() { held.splice(0).forEach(function (f) { f(); }); }
"""


def test_open_thread_plain_after_leaving_to_alarm_tab_keeps_investigation_slot() -> None:
    out = conv_run(
        ("openThread",),
        r"""
showInvestigationTab(true);
setActiveView("investigation");
ssData["investigation_conversation"] =
    JSON.stringify({ id: "inv-9", thread: "t-inv", user: "demo" });
currentThreadId = "t-inv";
threadBodies["t-plain"] = { turns: PLAIN };
openThread("t-plain");
setActiveView("alarm");          // 받는 사이 이벤트 알람 탭으로
release();
await tick(); await tick(); await tick();
out = state();
""",
        _DEFERRED_FETCH,
        PINNED=PINNED,
        PLAIN=PLAIN,
    )
    # 고정 카드 없는 대화는 질의응답 슬롯에 그려져야 한다(조사 대화 슬롯을 덮지 않는다)
    assert out["shown"] and out["shown"][0]["slot"] == "chat", out


_FAKE_OPEN_CONV = r"""
var convOpened = [];
function openInvestigationConversation(id) { convOpened.push(String(id)); return true; }
"""


def test_restore_with_unavailable_thread_store_does_not_get_stuck() -> None:
    out = conv_run(
        ("restoreInvestigationConversation",),
        r"""
ssData["investigation_conversation"] =
    JSON.stringify({ id: "inv-9", thread: "t-inv", user: "demo" });
threadBodies["t-inv"] = { status: 503, turns: [] };
restoreInvestigationConversation();
await tick(); await tick(); await tick();
out = { st: state(), conv: convOpened };
""",
        _FAKE_OPEN_CONV,
        PINNED=PINNED,
        PLAIN=PLAIN,
    )
    # 권고 동작: 같은 조사로 새 조사 대화를 열거나(404와 같게) 최소한 저장값을 지운다
    assert out["conv"] == ["inv-9"] or out["st"]["stored"] is None, out
