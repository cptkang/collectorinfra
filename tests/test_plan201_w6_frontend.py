"""plans/201 W6 — 첫 화면 「지난 24시간 · 내 담당」 요약 카드(app.js) 실행 검증.

app.js 전체를 node에서 가짜 DOM으로 띄운다(W5 `test_plan201_w5_card_flow.py`의 가짜 DOM 재사용). 기동 시
`checkAuthOnLoad()`가 그대로 돌고, 조회(`fetch`)는 하네스가 URL별로 답한다. 닫는 IIFE 앞에 시험 출구(`__APP`)
한 줄만 끼운다 — 제품 코드는 바꾸지 않는다. node는 stdin으로 실행한다(`node -e` 금지 — WinError 206). 시각
표시는 `TZ=UTC`로 고정한다.

  1. off(`/auth/me`에 `duty_daily_summary` 없음·false) — 기준 `5343f437` app.js와 #chatWelcome DOM·조회 목록이 같다.
  2. on — #chatWelcome 맨 위에 카드 1개 · 요약 1회 조회 · 티어 줄(키 있는 칸만) · 확인 필요 3 · 반복 1.
  3. XSS — 알람 이름·서버 이름의 `<img onerror>`는 텍스트로만 나온다.
  4. `unavailable`·조회 실패 = 사유 1줄 · eligible false = 카드 없음.
  5. [이벤트 알람에서 보기] — 알람 탭 + 「내 담당 먼저」 기존 저장 키(`alarm_duty_first`) 켬 · 새 키 없음.
  6. 서버 `duty_overlap` ↔ 화면 `dutyBadgesOf` 같은 픽스처 같은 결과(정본 어휘).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_plan201_w5_card_flow import _FAKE_DOM

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "src" / "static" / "js"
_BASE_SHA = "5343f437"

_LOADER = r"""
var fs = require("fs");
var ids = {};
fakeDoc.getElementById = function (id) {
    if (!ids[id]) { ids[id] = fakeDoc.createElement("div"); ids[id].id = id; }
    return ids[id];
};
var store = {};
global.localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
    setItem: function (k, v) { store[k] = String(v); },
    removeItem: function (k) { delete store[k]; },
};
global.sessionStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
var fetches = [];
function reply(body, ok) {
    return Promise.resolve({ ok: ok !== false, status: ok === false ? 500 : 200,
        json: function () { return Promise.resolve(body); } });
}
global.fetch = function (u) {
    u = String(u);
    fetches.push(u);
    if (u.indexOf("/api/v1/auth/status") === 0) return reply(STATUS);
    if (u.indexOf("/api/v1/auth/me") === 0) return reply(ME);
    if (u.indexOf("/api/v1/auth/duty-areas/daily-summary") === 0) {
        if (SUMMARY === "reject") return Promise.reject(new Error("net"));
        if (SUMMARY === "500") return reply({}, false);
        return reply(SUMMARY);
    }
    return new Promise(function () {});
};
global.EventSource = function () { this.close = function () {}; };
global.document = fakeDoc;
global.window = global;
global.navigator = {};
global.location = { href: "http://x/", origin: "http://x", pathname: "/", search: "", hash: "", protocol: "http:" };
global.history = { replaceState: function () {}, pushState: function () {} };
global.setInterval = function () { return 0; };
global.matchMedia = function () { return { matches: false, addEventListener: function () {} }; };
global.addEventListener = function () {};
global.requestAnimationFrame = function () {};
global.BriefingView = { setDutyMeta: function () {}, setActionAssist: function () {} };
global.DiagnosisView = {};
var _log = console.log; console.log = function () {}; console.warn = function () {}; console.error = function () {};
process.on("unhandledRejection", function () {});
var welcome = fakeDoc.getElementById("chatWelcome");
var hints = fakeDoc.createElement("div"); hints.className = "chat-welcome-hints";
welcome.appendChild(fakeDoc.createElement("h2")); welcome.appendChild(hints);
var src = fs.readFileSync(APP_PATH, "utf8");
var end = src.lastIndexOf("})();");
src = src.slice(0, end) + "global.__APP = { " +
    "view: function () { return activeView; }, " +
    "focus: function (m, a) { dutyAlarmFocus = m; dutyUserAreas = a; }, " +
    "badges: typeof dutyBadgesOf === 'function' ? dutyBadgesOf : null, " +
    "open: typeof openAlarmsDutyFirst === 'function' ? openAlarmsDutyFirst : null };\n" + src.slice(end);
try { (0, eval)(src); } catch (e) { process.stderr.write("eval " + (e && e.stack || e)); }
console.log = _log;
var APP = global.__APP;
function flush(n) { var p = Promise.resolve(); for (var i = 0; i < (n || 30); i++) p = p.then(function () { return new Promise(function (r) { setTimeout(r, 0); }); }); return p; }
"""

_STATUS = {"auth_enabled": True, "user": {"user_id": "u1", "username": "u1", "role": "user",
                                           "alarm_zones": ["bankjon"]}}
_ME_BASE = {"user_id": "u1", "username": "u1", "role": "user", "alarm_zones": ["bankjon"],
            "duty_areas": ["server_os"], "duty_area_options": [{"code": "server_os", "label": "서버/OS"}]}

_SUMMARY = {
    "eligible": True,
    "window_hours": 24,
    "areas": [{"code": "server_os", "label": "서버/OS"}, {"code": "db", "label": "DB"}],
    "tiers": {"page": 2, "ticket": 5, "dashboard": 12},
    "needs_attention": [
        {"ts": "2026-10-30T08:05:00+00:00", "tier": "page", "server_name": "가짜서버1", "alarm_name": "CPU 사용률",
         "resource_type": "server", "occurrence": True, "hint": False, "hint_label": ""},
        {"ts": "2026-10-30T07:00:00+00:00", "tier": "ticket", "server_name": "가짜서버2", "alarm_name": "",
         "resource_type": "Database", "occurrence": False, "hint": True, "hint_label": "SQL 응답 지연"},
        {"ts": "bad", "tier": "ticket", "server_name": "", "alarm_name": "디스크", "resource_type": "",
         "occurrence": True, "hint": False, "hint_label": "무시돼야 함"},
        {"ts": "2026-10-30T06:00:00+00:00", "tier": "ticket", "server_name": "넷째", "alarm_name": "넷째",
         "resource_type": "", "occurrence": True, "hint": False, "hint_label": ""},
    ],
    "top_repeat": {"alarm_name": "디스크 사용률 경고", "count": 7},
    "generated_at": "2026-10-30T09:00:00+00:00",
}


def _js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _node(body: str, *, me: dict[str, Any], summary: Any = None, app: Path | None = None) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    script = "\n".join([
        f"var APP_PATH = {_js(str(app or _JS / 'app.js'))};",
        f"var STATUS = {_js(_STATUS)};",
        f"var ME = {_js(me)};",
        f"var SUMMARY = {_js(summary if summary is not None else _SUMMARY)};",
        _FAKE_DOM,
        _LOADER,
        "var out = null;",
        "(async function () {",
        "await flush();",
        body,
        # 파이프 쓰기는 비동기 — 바로 exit하면 64KB에서 잘린다(쓰기 완료 콜백에서 종료)
        "process.stdout.write(JSON.stringify(out), function () { process.exit(0); });",
        "})().catch(function (e) { process.stderr.write(String(e && e.stack || e)); process.exit(1); });",
    ])
    env = {**os.environ, "TZ": "UTC"}
    done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=120,
                          check=False, encoding="utf-8", env=env)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_SNAPSHOT = """
out = { welcome: welcome.shape(), fetches: fetches.slice().sort(), store: Object.keys(store).sort(),
        card: !!welcome.querySelector("#dutyDailySummary") };
"""


@pytest.fixture(scope="module")
def base_app(tmp_path_factory) -> Path:
    done = subprocess.run(["git", "show", f"{_BASE_SHA}:src/static/js/app.js"], cwd=_ROOT, capture_output=True,
                          check=False)
    if done.returncode != 0:
        pytest.skip("기준 커밋 app.js를 읽을 수 없음")
    path = tmp_path_factory.mktemp("w6base") / "app.js"
    path.write_bytes(done.stdout)
    return path


# ── 1. off = 기준과 비트 동일 ────────────────────────────────────────────────────


@pytest.mark.parametrize("me", [_ME_BASE, {**_ME_BASE, "duty_daily_summary": False}],
                         ids=["key_absent", "key_false"])
def test_off_matches_base_dom_and_requests(me, base_app) -> None:
    now = _node(_SNAPSHOT, me=me)
    base = _node(_SNAPSHOT, me=me, app=base_app)
    assert now == base
    assert now["card"] is False
    assert not any("daily-summary" in u for u in now["fetches"])
    assert any(u.startswith("/api/v1/auth/me") for u in now["fetches"])        # 하네스가 실제로 기동했다


# ── 2. on = 카드 ────────────────────────────────────────────────────────────────

_READ = """
var card = welcome.querySelector("#dutyDailySummary");
out = card ? {
    first: welcome.children[0] === card,
    count: welcome.querySelectorAll("#dutyDailySummary").length,
    title: card.querySelector(".duty-summary-title").textContent,
    tiers: card.querySelector(".duty-summary-tiers") ? card.querySelector(".duty-summary-tiers").textContent : null,
    items: card.querySelectorAll(".duty-summary-item").map(function (li) { return li.textContent; }),
    repeat: card.querySelector(".duty-summary-repeat") ? card.querySelector(".duty-summary-repeat").textContent : null,
    unavailable: card.querySelector(".duty-summary-unavailable") ? card.querySelector(".duty-summary-unavailable").textContent : null,
    empty: card.querySelector(".duty-summary-empty") ? card.querySelector(".duty-summary-empty").textContent : null,
    button: card.querySelector(".duty-summary-go").textContent,
    html: card.innerHTML,
    imgs: card.querySelectorAll("img").length,
    summaryFetches: fetches.filter(function (u) { return u.indexOf("daily-summary") >= 0; }).length,
} : { card: false, summaryFetches: fetches.filter(function (u) { return u.indexOf("daily-summary") >= 0; }).length };
"""

_ME_ON = {**_ME_BASE, "duty_daily_summary": True}


def test_on_renders_card_at_top_once() -> None:
    out = _node(_READ, me=_ME_ON)
    assert out["first"] is True and out["count"] == 1 and out["summaryFetches"] == 1
    assert out["title"] == "지난 24시간 · 내 담당(서버/OS · DB)"
    assert out["tiers"] == "즉시 통보 2 · 대기열 5 · 화면 표시 12"                # 억제 키 없음 = 칸 없음
    assert out["items"] == [
        "10-30 08:05 · 가짜서버1 · CPU 사용률",
        "10-30 07:00 · 가짜서버2 · Database 단서: SQL 응답 지연",
        "디스크",                                                                   # 시각 파싱 실패 · 단서 아님
    ]
    assert out["repeat"] == "가장 많이 반복 — 디스크 사용률 경고 7건"
    assert out["button"] == "이벤트 알람에서 보기" and out["unavailable"] is None


def test_admin_suppress_cell_and_empty_attention() -> None:
    summary = {**_SUMMARY, "tiers": {"page": 0, "ticket": 0, "dashboard": 1, "suppress": 9},
               "needs_attention": [], "top_repeat": None}
    out = _node(_READ, me=_ME_ON, summary=summary)
    assert out["tiers"] == "즉시 통보 0 · 대기열 0 · 화면 표시 1 · 억제 9"
    assert out["items"] == [] and out["empty"] == "즉시 통보·대기열 알람이 없습니다." and out["repeat"] is None


def test_xss_names_are_text_only() -> None:
    evil = '<img src=x onerror="alert(1)">'
    summary = {**_SUMMARY, "needs_attention": [{**_SUMMARY["needs_attention"][0], "alarm_name": evil,
                                                "server_name": evil}],
               "top_repeat": {"alarm_name": evil, "count": 2},
               "areas": [{"code": "x", "label": evil}]}
    out = _node(_READ, me=_ME_ON, summary=summary)
    assert out["imgs"] == 0
    assert "&lt;img" in out["html"] and "<img" not in out["html"]
    assert evil in out["items"][0] and evil in out["repeat"] and evil in out["title"]


@pytest.mark.parametrize("summary,expected", [
    ({"eligible": True, "unavailable": "timeout", "window_hours": 24, "areas": []},
     "지금은 요약을 불러올 수 없습니다 — 집계 시간이 초과됐습니다."),
    ({"eligible": True, "unavailable": "error", "window_hours": 24, "areas": []},
     "지금은 요약을 불러올 수 없습니다 — 집계 중 오류가 났습니다."),
    ("500", "지금은 요약을 불러올 수 없습니다 — 집계 중 오류가 났습니다."),
    ("reject", "지금은 요약을 불러올 수 없습니다 — 집계 중 오류가 났습니다."),
])
def test_unavailable_shows_one_reason_line(summary, expected) -> None:
    out = _node(_READ, me=_ME_ON, summary=summary)
    assert out["unavailable"] == expected and out["tiers"] is None and out["items"] == []


def test_ineligible_response_draws_nothing() -> None:
    out = _node(_READ, me=_ME_ON, summary={"eligible": False, "reason": "no_duty", "window_hours": 24})
    assert out == {"card": False, "summaryFetches": 1}


def test_card_lives_inside_welcome_so_first_send_hides_it() -> None:
    src = (_JS / "app.js").read_text(encoding="utf-8")
    block = src[src.index("// ─── plans/201 W6 첫 화면"):src.index("// ─── plans/201 W6 끝 ───")]
    assert "chatWelcome.insertBefore(card, chatWelcome.firstChild)" in block
    assert "innerHTML" not in block                                            # createElement·textContent만
    assert "setInterval" not in block and "setTimeout" not in block            # 자동 갱신 없음
    assert "server_os" not in block and "was_mw" not in block                  # 영역 어휘 사본 없음


# ── 5. [이벤트 알람에서 보기] ─────────────────────────────────────────────────────

_CLICK = """
APP.focus({ areas: [], cause_hints: {} }, ["server_os"]);
var before = Object.keys(store).sort();
welcome.querySelector("#dutyDailySummary .duty-summary-go").click();
out = { view: APP.view(), key: localStorage.getItem("alarm_duty_first"), before: before,
        after: Object.keys(store).sort() };
"""


def test_button_opens_alarm_tab_and_turns_on_duty_first() -> None:
    out = _node(_CLICK, me=_ME_ON)
    assert out["view"] == "alarm" and out["key"] == "1"
    assert set(out["after"]) - set(out["before"]) <= {"alarm_duty_first"}      # 새 저장 키 없음


def test_button_without_focus_meta_only_opens_tab() -> None:
    out = _node("""
welcome.querySelector("#dutyDailySummary .duty-summary-go").click();
out = { view: APP.view(), key: localStorage.getItem("alarm_duty_first") };
""", me=_ME_ON)
    assert out == {"view": "alarm", "key": None}


def test_button_reuses_existing_storage_key_constant() -> None:
    src = (_JS / "app.js").read_text(encoding="utf-8")
    fn = src[src.index("function openAlarmsDutyFirst()"):]
    fn = fn[:fn.index("\n    }\n") + 6]
    assert "DUTY_FIRST_STORAGE_KEY" in fn and "applyDutyAlarmOrder()" in fn and '"alarm_duty_first"' not in fn
    assert len(re.findall(r'"alarm_duty_first"', src)) == 1


# ── 6. 서버 ↔ 화면 담당 겹침 같은 결과 ───────────────────────────────────────────


def test_server_overlap_matches_frontend_badges() -> None:
    from src.api.duty_area_support import duty_alarm_focus_meta
    from src.domain.duty_areas import duty_overlap
    from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

    vocab = read_duty_area_vocabulary(_ROOT / "config" / "duty_areas.yaml")
    from src.api import duty_area_support

    orig = duty_area_support.load_duty_area_vocabulary
    duty_area_support.load_duty_area_vocabulary = lambda: vocab          # type: ignore[assignment]
    try:
        meta = duty_alarm_focus_meta()
    finally:
        duty_area_support.load_duty_area_vocabulary = orig               # type: ignore[assignment]
    rts = [rt for area in vocab.areas for rt in area.resource_types][:6] + ["", "미매핑유형"]
    types = list(vocab.cause_hints.apm_event_types)[:4] + ["", "ERROR_" + (list(vocab.cause_hints.apm_event_types)
                                                                           or ["X"])[0], "NOPE"]
    cases = []
    for rt in rts:
        for et in types:
            for areas in ([a.code] for a in vocab.areas):
                cases.append({"alarm": {"resource_type": rt, "apm_event_type": et}, "areas": areas})
    out = _node(f"""
var cases = {_js(cases)};
out = cases.map(function (c) {{ var b = APP.badges(c.alarm, {_js(meta)}, c.areas);
    return [b.occurrence, b.hint]; }});
""", me=_ME_BASE)
    server = []
    for c in cases:
        o = duty_overlap(vocab, c["areas"], resource_type=c["alarm"]["resource_type"],
                         event_type=c["alarm"]["apm_event_type"])
        server.append([o.occurrence, o.hint])
    assert out == server
    assert any(o for o, _ in server) and any(h for _, h in server)           # 픽스처가 양쪽 판정을 실제로 밟는다
