"""plans/194 W5 U5·U6 — 관제 수동 조사 모드 표지 · 관리자 「알람 조사 — 운영 요약」(D-347).

브라우저 e2e는 이 환경에서 돌지 않으므로 두 갈래로 고정한다(망·LLM 호출 0):
    1. 정적 — 캐시 버스터 · 요약 칸 자리(설정 목록 위) · break-glass 가드 ·
       innerHTML·polling 미사용.
    2. node 실행 — `admin.js` 요약 렌더 함수와 `noise.js` 수동 모드 판정식을 뽑아
       가짜 DOM·fetch로 돌린다.
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
_ADMIN_JS = _STATIC / "js" / "admin.js"
_NOISE_JS = _STATIC / "js" / "noise.js"
_DASHBOARD = _STATIC / "admin" / "dashboard.html"

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node 미설치")

_SUMMARY_FUNCS = (
    "invEl", "invNumber", "invClock", "invSummaryHead", "invModeLine", "invLimitTable",
    "invGauge", "invStatus", "renderInvestigationSummary",
)

_DOM = r"""
function Node(tag) {
    this.tagName = tag; this.children = []; this._text = ""; this.className = ""; this.id = "";
    this.type = ""; this.disabled = false; this.listeners = {};
}
Object.defineProperty(Node.prototype, "textContent", {
    get: function () {
        return this._text + this.children.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) { this.children = []; this._text = String(v); },
});
Node.prototype.appendChild = function (c) { this.children.push(c); return c; };
Node.prototype.addEventListener = function (n, f) { this.listeners[n] = f; };
Node.prototype.find = function (cls) {
    var out = [];
    (function walk(n) {
        if (n.className && n.className.split(" ").indexOf(cls) >= 0) out.push(n);
        (n.children || []).forEach(walk);
    })(this);
    return out;
};
function TextNode(t) { this._t = String(t); this.children = []; }
Object.defineProperty(TextNode.prototype, "textContent", { get: function () { return this._t; } });
var box = new Node("section");
var document = {
    createElement: function (t) { return new Node(t); },
    createTextNode: function (t) { return new TextNode(t); },
    getElementById: function (id) { return id === "investigationSummary" ? box : null; },
};
function loadInvestigationSummary() {}
"""


def _admin_render(data: Any) -> dict[str, Any]:
    src = _ADMIN_JS.read_text(encoding="utf-8")
    rows = re.search(r"var INV_LIMIT_ROWS = \[.*?\n    \];", src, re.S)
    assert rows, "INV_LIMIT_ROWS"
    funcs = "\n".join(_js_function_body(src, name) for name in _SUMMARY_FUNCS)
    script = _DOM + rows.group(0) + "\n" + funcs + f"""
renderInvestigationSummary({json.dumps(data, ensure_ascii=False)});
var btn = box.find("inv-summary__refresh")[0];
console.log(JSON.stringify({{
    text: box.textContent,
    gauges: box.find("inv-summary__gauge").map(function (g) {{ return g.textContent; }}),
    cells: box.find("inv-summary__num").map(function (c) {{ return c.textContent; }}),
    pill: (box.find("inv-summary__pill")[0] || {{}}).className || "",
    refresh: btn ? {{ text: btn.textContent, type: btn.type, click: !!btn.listeners.click }} : null,
}}));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _summary(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "mode": {"auto_enabled": False, "manual_enabled": True},
        "limits": {
            "max_concurrent": 2, "max_queued": 4, "per_user_active": 1, "per_user_hourly": 3,
            "tool_global_concurrency": 8, "hourly_budget": 20,
        },
        "load": {"running": 1, "queued": 0, "abandoned_slots": 0, "abandoned_threads": 0},
        "checked_at": "2026-10-26T10:15:20+09:00",
        "service_error": None,
    }
    data.update(over)
    return data


# ── U6 관리자 운영 요약 ──


@_needs_node
def test_summary_renders_mode_limits_and_gauges() -> None:
    out = _admin_render(_summary())
    text = out["text"]
    for phrase in ("알람 조사 — 운영 요약", "수동 전용", "자동 조사 꺼짐", "동시 조사", "대기열",
                   "사용자별 진행 중", "사용자별 시간당 요청", "조회 대상(DB·APM) 전역 동시 호출",
                   "시간당 전체 예산", "「대기열이 가득 찼습니다」로 즉시 거절",
                   "조사 서비스 상태", "기준)"):
        assert phrase in text, phrase
    assert "inv-summary__pill--manual" in out["pill"]
    assert out["cells"][1:] == ["2건", "4건", "1건", "3건", "8건", "20건"]  # 첫 칸은 머리 「값」
    assert out["gauges"] == ["진행 중1 / 2", "대기0 / 4", "정리 중0"]
    assert out["refresh"] == {"text": "새로 고침", "type": "button", "click": True}


@_needs_node
def test_summary_auto_mode_label() -> None:
    out = _admin_render(_summary(mode={"auto_enabled": True, "manual_enabled": True}))
    assert "inv-summary__pill--auto" in out["pill"] and "자동 조사 켜짐" in out["text"]
    assert "수동 전용" not in out["text"]


@_needs_node
def test_summary_null_values_show_unknown() -> None:
    data = _summary(limits={"max_concurrent": None}, load=None, mode=None, checked_at=None)
    out = _admin_render(data)
    assert out["cells"][1:] == ["확인 불가"] * 6
    assert out["gauges"] == ["진행 중확인 불가", "대기확인 불가", "정리 중확인 불가"]
    assert "알람 조사 모드: 확인 불가" in out["text"]
    assert "기준)" not in out["text"]


@_needs_node
def test_summary_service_error_is_one_line_without_gauges() -> None:
    out = _admin_render(_summary(load=None, service_error="조사 서비스에 연결하지 못했습니다"))
    assert "조사 서비스에 연결하지 못했습니다" in out["text"]
    assert out["gauges"] == []
    assert "동시 조사" in out["text"]  # 상한 표는 그대로


@_needs_node
def test_summary_failure_collapses_to_one_line() -> None:
    out = _admin_render(None)
    assert out["text"] == "요약을 불러오지 못했습니다"
    assert out["refresh"] is None


def test_summary_loader_uses_admin_api_without_polling() -> None:
    src = _ADMIN_JS.read_text(encoding="utf-8")
    load = _js_function_body(src, "loadInvestigationSummary")
    assert 'apiRequest("GET", "/api/v1/admin/investigation/summary")' in load
    assert "response.ok" in load  # 404·오류면 data=null → 한 줄
    assert "loadInvestigationSummary();" in _js_function_body(src, "ensureSettingsLoaded")
    for name in (*_SUMMARY_FUNCS, "loadInvestigationSummary"):
        body = _js_function_body(src, name)
        assert "innerHTML" not in body, name
        assert "setInterval" not in body and "setTimeout" not in body, name


def test_dashboard_summary_box_sits_above_settings_list() -> None:
    html = _DASHBOARD.read_text(encoding="utf-8")
    box = html.index('id="investigationSummary"')
    assert html.index('id="settingsSearch"') < box < html.index('id="settingsAccordion"')
    assert ".inv-summary {" in html
    assert "admin.js?v=14" in html


# ── U5 노이즈 관제 수동 조사 모드 ──


def _mode_snippet() -> str:
    src = _NOISE_JS.read_text(encoding="utf-8")
    end = r"\n        \}, function \(\) \{ return false; \}\);\n"
    m = re.search(r"    var manualOnlyMode = .*?" + end, src, re.S)
    assert m, "manualOnlyMode"
    return m.group(0)


_MANUAL_ONLY = {"investigation_manual_enabled": True, "investigation_auto_enabled": False}
_BOTH_ON = {"investigation_manual_enabled": True, "investigation_auto_enabled": True}


@_needs_node
@pytest.mark.parametrize(
    "caps, ok, break_glass, expected",
    [
        (_MANUAL_ONLY, True, False, True),
        (_BOTH_ON, True, False, False),
        ({"investigation_manual_enabled": True}, True, False, False),  # 키 없음 → 종전
        ({}, True, False, False),
        (_MANUAL_ONLY, False, False, False),  # 조회 실패 → 종전
        (_MANUAL_ONLY, True, True, False),  # break-glass
    ],
)
def test_noise_manual_only_mode_condition(
    caps: dict, ok: bool, break_glass: bool, expected: bool
) -> None:
    status = 200 if ok else 401
    script = f"""
var calls = [];
var window = {{ BriefingView: {{}} }};
var IS_BREAK_GLASS = {json.dumps(break_glass)};
function alarmCall(path) {{
    calls.push(path);
    return Promise.resolve({{
        ok: {json.dumps(ok)}, status: {status}, body: {json.dumps(caps)},
    }});
}}
{_mode_snippet()}
manualOnlyMode.then(function (on) {{ console.log(JSON.stringify({{ on: on, calls: calls }})); }});
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["on"] is expected
    # break-glass 운영자 토큰은 기능 조회(사용자 토큰 인가)를 부르지 않는다 — 401 로그아웃 회피
    assert out["calls"] == ([] if break_glass else ["/api/v1/alarm/capabilities"])


def test_noise_mode_badges_and_wiring() -> None:
    src = _NOISE_JS.read_text(encoding="utf-8")
    snippet = _mode_snippet()
    assert "apiRequest" not in snippet
    assert '"수동 조사 모드 · 자동 조사 꺼짐"' in src and '".noise-brand h1"' in src
    only = _js_function_body(src, "renderAnalyzeOnly")
    assert "if (IS_BREAK_GLASS) return;" in only
    assert "Promise.all([probeAnalyze(alarmId), manualOnlyMode])" in only
    chip = 'el("span", "noise-mode-chip", "수동 조사 모드")'
    assert f"if (got[1]) investigationHead.appendChild({chip});" in only
    # 접수는 종전 [분석 요청]과 같은 API·같은 인가
    submit = '"/api/v1/alarm/" + encodeURIComponent(alarmId) + "/investigations", "POST"'
    assert submit in _js_function_body(src, "analyzeOptions")


def test_noise_pages_cache_busted() -> None:
    css = (_STATIC / "css" / "noise-console.css").read_text(encoding="utf-8")
    assert ".noise-mode-chip {" in css
    for page in ("noise.html", "admin/noise.html"):
        html = (_STATIC / page).read_text(encoding="utf-8")
        assert "noise.js?v=25" in html and "noise-console.css?v=11" in html, page
