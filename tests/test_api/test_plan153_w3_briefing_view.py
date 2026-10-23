"""plans/153 W3 — 조사 브리핑 웹 표시(R-1): 공용 렌더 `briefing-view.js` · 카드 · 드로어 ⓒ.

세 갈래를 겨눈다:
    1. JS 순수 함수 — `briefing-view.js`를 node로 실제 실행한다. 이스케이프(외부 텍스트) ·
       `[가설]`·`[미결]` 배지 · 상태 칩(실패 사유 침묵 금지) · 「한계」 항상 펼침 ·
       권고 아래 human-gated 문구 · 크로스소스 소표(지원 배지 3종·APM 미연결) ·
       재조회 상한(15초 간격 · 300초)과 정지.
    2. JS 정적 — 카드는 렌더 시 조회하지 않고(펼칠 때만) 드로어는 ⓐⓑ 뒤에 같은 공용 렌더를 붙이며
       닫으면 타이머를 멈춘다. 두 소비자가 자기 렌더러를 따로 두지 않는다(D-247 공용 패널).
    3. HTML — 세 화면이 공용 스크립트를 소비자보다 먼저 싣는다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.test_api.test_noise_dashboard_api import _js_function_body

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_APP_JS = _STATIC / "js" / "app.js"
_NOISE_JS = _STATIC / "js" / "noise.js"
_SRE_BRIEFING = _ROOT / "sre_agent" / "sre_agent" / "application" / "briefing_builder.py"


def _node(body: str) -> Any:
    """`BV`(= briefing-view.js의 module.exports)를 둔 채 `body`를 돌리고 `out`을 JSON으로 받는다."""
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 순수 함수 실행 테스트 생략")
    script = "\n".join(
        [
            f"var BV = require({json.dumps(str(_BV_JS))});",
            "var out = null;",
            "(async function () {",
            body,
            "process.stdout.write(JSON.stringify(out));",
            "})().catch(function (e) { console.error(e); process.exit(1); });",
        ]
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30, check=False
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _render(view: dict | None, extra: dict | None = None) -> str:
    return _node(
        f"out = BV.renderBody({json.dumps(view, ensure_ascii=False)}, "
        f"{json.dumps(extra, ensure_ascii=False)});"
    )


_DONE = {
    "status": "done",
    "investigation_id": "inv-7f3a",
    "kind": "alarm",
    "episode_id": None,
    "briefing_lines": [
        {"label": "요약", "text": "web-01 CPU 95% 지속"},
        {"label": "원인 가설", "text": "[가설] 배치 작업 과부하\n[미결] 원인 미확정"},
        {"label": "권고", "text": "배치 일정 확인"},
        {"label": "한계", "text": "인용 미검증"},
    ],
    "cross_source": None,
    "verdict_level": "high",
    "reason": None,
    "updated_at": "2026-10-12T06:08:10+00:00",
    "source": "followup",
}


# ─── 1. JS 순수 함수(node 실행) ─────────────────────────────────────────


def test_every_external_text_is_escaped():
    evil = '<img src=x onerror="alert(1)">'
    html = _render(
        {
            **_DONE,
            "verdict_level": evil,
            "updated_at": evil,
            "briefing_lines": [{"label": evil, "text": evil + "\n[가설] " + evil}],
            "cross_source": {
                "episode_id": evil,
                "host_key": evil,
                "sources": {evil: evil},
                "apm_tools_connected": True,
                "apm_tools_called": 2,
                "links": [
                    {
                        "rule_id": evil,
                        "cause": evil,
                        "effect": evil,
                        "lag_seconds": 30,
                        "supported_by_tools": True,
                    }
                ],
            },
        },
        {"note": evil},
    )
    assert "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    failed = _render({"status": "failed", "reason": "<script>x</script>"})
    assert "<script>" not in failed and "&lt;script&gt;x&lt;/script&gt;" in failed
    assert "<script>" not in _node('out = BV.renderError("<script>x</script>");')


def test_hypothesis_and_unresolved_prefixes_become_badges():
    html = _render(_DONE)
    assert '<span class="bv-badge bv-badge--hypo">가설</span> 배치 작업 과부하' in html
    assert '<span class="bv-badge bv-badge--open">미결</span> 원인 미확정' in html
    assert "[가설]" not in html and "[미결]" not in html


@pytest.mark.parametrize(
    "view, kind, text, reason",
    [
        ({"status": "running"}, "running", "조사 중", ""),
        ({"status": "submitted"}, "running", "조사 중", ""),
        ({"status": "done"}, "done", "완료", ""),
        ({"status": "failed", "reason": "연결 실패"}, "fail", "실패", "연결 실패"),
        ({"status": "timeout"}, "fail", "실패 · 시간 초과", "조사 시간 제한을 넘겼습니다"),
        ({"status": "rejected", "reason": "인가 거부"}, "fail", "실패 · 제출 거부", "인가 거부"),
        ({"status": "episode_existing"}, "linked", "같은 사건 조사로 연결됨", ""),
        ({"status": "none"}, "none", "없음", ""),
        (
            {"status": "running", "reason": "재조회 타임아웃(10s 초과)"},
            "running",
            "조사 중",
            "재조회 타임아웃(10s 초과)",
        ),
    ],
)
def test_status_chip_vocabulary_and_failure_reason_is_never_silent(view, kind, text, reason):
    chip = _node(f"out = BV.statusChip({json.dumps(view, ensure_ascii=False)});")
    assert chip == {"kind": kind, "text": text, "reason": reason}
    html = _render(view)
    assert f'<span class="bv-chip bv-chip--{kind}">{text}</span>' in html
    if reason:
        assert f"사유: {reason}" in html


def test_episode_link_and_none_have_explanations():
    linked = _render({"status": "episode_existing", "episode_id": "ep-42"})
    assert "같은 사건의 종합 조사에 연결됐습니다" in linked and "ep-42" in linked
    assert "자동 조사 기록이 없습니다" in _render({"status": "none"})
    assert "자동 조사 기록이 없습니다" in _render(None)


def test_limitations_are_always_open_and_recommendation_keeps_human_gated_note():
    html = _render(_DONE)
    assert '<div class="bv-item bv-item--limit"><div class="bv-label">한계</div>' in html
    assert "<details" not in html  # 접는 장치 자체가 없다
    note = _node("out = BV.HUMAN_GATED_NOTE;")
    sre_src = _SRE_BRIEFING.read_text(encoding="utf-8")
    assert f'HUMAN_GATED_NOTE = "{note}"' in sre_src  # 생산자 문구와 같은 글자
    assert html.count(note) == 1
    rec = html[html.index('<div class="bv-label">권고</div>') : html.index("bv-item--limit")]
    assert f'<div class="bv-gated">{note}</div>' in rec
    # 생산자가 권고 본문에 이미 실은 경우에도 한 번만, 권고 아래 별도 줄로
    again = _render(
        {"status": "done", "briefing_lines": [{"label": "권고", "text": "재시작 검토\n" + note}]}
    )
    assert again.count(note) == 1 and f'<div class="bv-gated">{note}</div>' in again


def test_cross_source_table_support_badges_and_apm_disconnected():
    cross = {
        "episode_id": "ep-9",
        "host_key": "gpapp01",
        "sources": {"polestar": 3, "jennifer": 2},
        "apm_tools_connected": False,
        "apm_tools_called": 0,
        "links": [
            {
                "rule_id": "host_resource_to_was_latency",
                "cause": "CPU 사용률",
                "effect": "응답 지연",
                "lag_seconds": 270,
                "supported_by_tools": True,
            },
            {
                "rule_id": "r2",
                "cause": "메모리",
                "effect": "큐잉",
                "lag_seconds": 45,
                "supported_by_tools": False,
            },
            {
                "rule_id": "r3",
                "cause": "디스크",
                "effect": "오류율",
                "lag_seconds": None,
                "supported_by_tools": None,
            },
        ],
    }
    html = _render({**_DONE, "cross_source": cross})
    assert "두 소스 타임라인" in html
    assert "사건 ep-9 · 호스트 gpapp01 · 폴스타 3건 · 제니퍼 2건" in html
    assert "APM 도구 미연결" in html  # 침묵 금지
    assert html.count("<tr>") == 4  # 머리 1 + 링크 3
    assert "CPU 사용률 → 응답 지연" in html and "4분 30초" in html and "45초" in html
    for kind, text in (("both", "양쪽 도구 확인"), ("one", "한쪽만"), ("none", "인용 없음")):
        assert f'<span class="bv-support bv-support--{kind}">{text}</span>' in html
    connected = _render(
        {**_DONE, "cross_source": {**cross, "apm_tools_connected": True, "apm_tools_called": 4}}
    )
    assert "APM 도구 연결 · 호출 4회" in connected and "미연결" not in connected
    # 블록이 없으면 소표를 그리지 않는다
    assert "bv-cross" not in _render(_DONE)


_FAKE_CLOCK = """
var t = 0, q = [], calls = 0, infos = [];
function flush() { return new Promise(function (r) { setImmediate(r); }); }
function poller(view) {
  return BV.createPoller({
    load: function () { calls += 1; return typeof view === "function" ? view(calls) : view; },
    onView: function (v, info) { infos.push(info.limited); },
    now: function () { return t; },
    setTimer: function (fn, ms) { q.push([fn, ms]); return q.length; },
    clearTimer: function () { q.length = 0; },
    intervalMs: 15000, limitMs: 300000,
  });
}
async function drain(max) {
  for (var i = 0; q.length && i < (max || 1000); i++) {
    var job = q.shift(); t += job[1]; await job[0](); await flush();
  }
}
"""


def test_poller_repolls_every_15s_and_stops_at_limit():
    out = _node(
        _FAKE_CLOCK
        + """
var p = poller({status: "running"});
await p.start(); await flush(); await drain();
out = {calls: calls, t: t, last: infos[infos.length - 1],
       limitedCount: infos.filter(Boolean).length, pending: p.pending()};
"""
    )
    # t=0,15,…,300초 — 상한(300초)에 닿은 응답에서 멈춘다(그 뒤 타이머 없음)
    assert out == {"calls": 21, "t": 300000, "last": True, "limitedCount": 1, "pending": False}


def test_poller_stops_when_terminal_and_when_closed():
    done = _node(
        _FAKE_CLOCK
        + """
var p = poller(function (n) { return n < 3 ? {status: "running"} : {status: "done"}; });
await p.start(); await flush(); await drain();
out = {calls: calls, pending: p.pending(), limited: infos.some(Boolean)};
"""
    )
    assert done == {"calls": 3, "pending": False, "limited": False}
    closed = _node(
        _FAKE_CLOCK
        + """
var p = poller({status: "running"});
await p.start(); await flush(); await drain(2);
p.stop();
var before = calls; await drain();
out = {before: before, after: calls, pending: p.pending(), views: infos.length};
"""
    )
    assert closed["before"] == closed["after"] == 3
    assert closed["pending"] is False and closed["views"] == 3


def test_poller_initial_block_renders_without_call_and_late_response_is_dropped():
    initial = _node(
        _FAKE_CLOCK
        + """
var p = poller({status: "done"});
await p.start({status: "done"}); await flush();
var a = calls;
var p2 = poller({status: "done"});
await p2.start({status: "running"}); await flush();
var b = calls; await drain();
out = {doneCalls: a, activeBeforeTimer: b, activeAfter: calls};
"""
    )
    # 이미 종결된 블록은 호출 0 · 미종결 블록은 첫 재조회를 15초 뒤에 한다
    assert initial == {"doneCalls": 0, "activeBeforeTimer": 0, "activeAfter": 1}
    late = _node(
        _FAKE_CLOCK
        + """
var release;
var p = BV.createPoller({
  load: function () { return new Promise(function (r) { release = r; }); },
  onView: function (v, info) { infos.push(v.status); },
  now: function () { return t; }, setTimer: function () { return 1; }, clearTimer: function () {},
});
var started = p.start(); await flush();
p.stop(); release({status: "done"}); await started; await flush();
out = infos;
"""
    )
    assert late == []


def test_attach_uses_capability_limit_and_shows_limit_note():
    out = _node("""
var container = {innerHTML: "", isConnected: true};
var panel = BV.attach(container, {limitSeconds: 0,
  load: function () { return {status: "running"}; }});
var note = BV.renderBody({status: "running"}, {note: "x"});
out = {limit: BV.DEFAULT_LIMIT_S, poll: BV.POLL_MS, loading: container.innerHTML,
       noteCls: note.indexOf("bv-note--warn") >= 0};
await panel.open(); panel.close();
out.afterOpen = container.innerHTML.indexOf("조사 중") >= 0;
""")
    assert out["limit"] == 1200 and out["poll"] == 15000
    assert out["afterOpen"] is True and out["noteCls"] is True
    assert "재조회 상한({s}초)" in _BV_JS.read_text(encoding="utf-8")


def test_attach_limit_follows_capability_seconds_else_default():
    # capabilities `investigation_followup_timeout_seconds`(float 초)를 그대로 상한으로 쓴다 —
    # 값이 없으면(None·0) 1200초(D-344 ④).
    # 응답이 상한을 넘겨 오면 그 자리에서 멈추고 상한 값을 안내한다.
    out = _node("""
function slow() {
  return new Promise(function (r) {
    setTimeout(function () { r({status: "running"}); }, 20);
  });
}
var a = {innerHTML: "", isConnected: true};
var pa = BV.attach(a, {limitSeconds: 0.005, load: slow});
await pa.open();
var b = {innerHTML: "", isConnected: true};
var pb = BV.attach(b, {limitSeconds: undefined, load: slow});
await pb.open();
out = {capNote: a.innerHTML.indexOf("재조회 상한(0.005초)") >= 0, capPending: pa.poller.pending(),
       defNote: b.innerHTML.indexOf("재조회 상한") >= 0, defPending: pb.poller.pending()};
pb.close();
""")
    assert out == {"capNote": True, "capPending": False, "defNote": False, "defPending": True}


# ─── 2. JS 정적 — 소비자 배선 ─────────────────────────────────────────


def test_card_section_is_lazy_and_conditional():
    src = _APP_JS.read_text(encoding="utf-8")
    render = _js_function_body(src, "renderAlarmMessage")
    assert "hasAlarmBriefing(data) ? renderAlarmBriefingSection()" in render
    assert "bindAlarmBriefing(el, data)" in render
    assert "investigations" not in render and "panel.open" not in render  # 렌더 시 호출 금지
    cond = _js_function_body(src, "hasAlarmBriefing")
    assert "data.investigation_id" in cond and "alarmCapabilities.investigation_enabled" in cond
    bind = _js_function_body(src, "bindAlarmBriefing")
    # 호출은 펼침 클릭 안에서만 · 접으면 정지
    click = bind[bind.index('addEventListener("click"') :]
    assert "panel.open()" in click and "panel.close()" in click
    assert bind.index("panel.open()") > bind.index('addEventListener("click"')
    assert "/api/v1/alarm/investigations/by-alarm/" in bind
    assert "investigation_followup_timeout_seconds" in bind
    # 알람 id가 있으면 항상 by-alarm(서버의 종합 조사 우선순위) —
    # 조사 id 경로는 알람 id가 없을 때만.
    # 재조회도 같은 경로(load가 같은 path를 쓴다)
    assert re.search(
        r'var path = data\.alarm_id\s*\?\s*"/api/v1/alarm/investigations/by-alarm/"', bind
    )
    assert "fetch(path," in bind


def test_drawer_appends_briefing_after_cause_and_stops_on_close():
    src = _NOISE_JS.read_text(encoding="utf-8")
    drawer = _js_function_body(src, "renderDrawer")
    assert drawer.index("renderCause(record)") < drawer.index("renderInvestigation(record)")
    assert "stopInvestigation()" in _js_function_body(src, "closeDrawer")
    inv = _js_function_body(src, "renderInvestigation")
    assert "stopInvestigation()" in inv and "BV.attach(" in inv and "initial: block" in inv
    assert "decisionInvestigation(alarmId)" in inv  # 행 레코드엔 블록이 없다 — 열 때 1회 읽기
    assert '"/decisions/"' in _js_function_body(src, "decisionInvestigation")
    # 관제 화면은 alarm capabilities를 읽지 않는다 — 상한은 공용 기본 1200초(limitSeconds 미지정)
    assert "limitSeconds" not in inv and "capabilities" not in src
    reload = _js_function_body(src, "reloadInvestigation")
    assert "/api/v1/alarm/investigations/" in reload
    assert "apiRequest" not in reload  # break-glass 운영자 토큰 401로 로그아웃시키지 않는다
    # ⓐⓑ 렌더는 W1 그대로 — ⓒ는 따로 붙는다
    assert "investigation" not in _js_function_body(src, "renderCause")


def test_consumers_do_not_carry_their_own_briefing_renderer():
    # 카드·드로어가 같은 렌더(BriefingView.renderBody)를 쓴다 — 본문 마크업은 공용 파일에만 있다.
    for path in (_APP_JS, _NOISE_JS):
        src = path.read_text(encoding="utf-8")
        assert "bv-" not in src, path.name
        assert "BriefingView" in src, path.name
    bv = _BV_JS.read_text(encoding="utf-8")
    assert "container.innerHTML = renderBody(" in _js_function_body(bv, "attach")
    # 전역 오염 최소 — 네임스페이스 1개
    assert re.findall(r"root\.(\w+)\s*=", bv) == ["BriefingView"]


# ─── 3. HTML — 세 화면이 공용 스크립트를 싣는다 ─────────────────────────


@pytest.mark.parametrize(
    "page, consumer",
    [("index.html", "app.js"), ("noise.html", "noise.js"), ("admin/noise.html", "noise.js")],
)
def test_pages_load_briefing_view_before_consumer(page, consumer):
    html = (_STATIC / page).read_text(encoding="utf-8")
    bv = re.search(r'<script src="/static/js/briefing-view\.js\?v=\d+"></script>', html)
    use = re.search(r'<script src="/static/js/' + re.escape(consumer) + r'\?v=\d+"></script>', html)
    assert bv and use and bv.start() < use.start()


def test_cross_source_line_hidden_when_table_drawn() -> None:
    """(plans/153 W6) 소표를 그리면 같은 내용의 「크로스소스」 평문 줄을 뺀다.

    소표가 없으면 그 줄을 그대로 둔다.
    """
    line = {"label": "크로스소스", "text": "사건 호스트 xsapp01 · 폴스타 1건 · 제니퍼 1건"}
    cs = {
        "episode_id": "ep-1",
        "host_key": "xsapp01",
        "sources": {"polestar": 1, "jennifer": 1},
        "apm_tools_connected": True,
        "apm_tools_called": 2,
        "links": [
            {
                "rule_id": "r1",
                "cause": "CPU",
                "effect": "Q",
                "lag_seconds": 270,
                "supported_by_tools": True,
            }
        ],
    }
    lines = [*_DONE["briefing_lines"], line]
    with_table = _render({**_DONE, "briefing_lines": lines, "cross_source": cs})
    assert "사건 호스트 xsapp01 · 폴스타 1건" not in with_table
    assert "web-01 CPU 95% 지속" in with_table
    without_table = _render({**_DONE, "briefing_lines": lines})
    assert "사건 호스트 xsapp01 · 폴스타 1건" in without_table
