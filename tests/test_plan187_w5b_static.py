"""plans/187 W5b — 웹 렌더(플레이북 브리핑 요약형·상세형 · 조사 대화 · 엔진 비교).

세 갈래를 겨눈다:
    1. JS 순수 함수 — `briefing-view.js`를 node로 실행한다. 픽스처 5종(정상·SQL 지배·외부 지배·GC·
       결손)과 진행 중 스냅숏 2종은 W4 dispatcher를 가짜 콜러블·가짜 LLM·가짜 HolmesGPT로 돌려 얻은
       **실제 브리핑 모양**이다(`tests/fixtures/plan187_w5b/` — 망·LLM 호출 0).
       `playbook` 키가 없으면 종전 렌더 그대로인지, 판정 칩이 공통 칸 1회인지, 근거가 서술보다
       먼저인지, 가드 사유·실시간 배지·누적 막대·스파크라인·4택·스켈레톤·XSS 이스케이프를 본다.
    2. JS 정적 — 조사 대화(`?investigation=` → `replaceState`) · 첫 턴 헤더 · 복원 · 범위 칩 ·
       스트리밍 중 비활성 · 추천 칩 미발행 · 관제 드로어 새 탭 / break-glass 상세형.
    3. HTML — 캐시 버전.
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

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_APP_JS = _STATIC / "js" / "app.js"
_NOISE_JS = _STATIC / "js" / "noise.js"
_FIX = _ROOT / "tests" / "fixtures" / "plan187_w5b"
_CASES = ("normal", "latency_sql", "latency_external", "gc", "partial_missing")


def _node(body: str) -> Any:
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


def _render(view: dict, extra: dict | None = None) -> str:
    return _node(
        f"out = BV.renderBody({json.dumps(view, ensure_ascii=False)}, "
        f"{json.dumps(extra, ensure_ascii=False)});"
    )


def _briefing(name: str) -> dict:
    return json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))["briefing"]


def _done_view(name: str) -> dict:
    return {
        "status": "done",
        "investigation_id": f"inv-{name}",
        "kind": "alarm",
        "briefing_lines": [],
        "briefing": _briefing(name),
        "updated_at": "2026-10-19T01:00:00+00:00",
    }


def _running_view(name: str) -> dict:
    snap = json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))
    return {
        "status": "running",
        "investigation_id": "inv-run",
        "briefing_lines": [],
        "progress": snap["progress"],
        "partial_briefing": snap["partial_briefing"],
    }


def _user_view(view: dict) -> dict:
    """W5a 사용자 경로 모양 — `briefing`에 `playbook`·`deep_dive`·`engine_agreement`만 실린다."""
    keep = ("partial", "playbook", "deep_dive", "engine_agreement")
    out = dict(view)
    for key in ("briefing", "partial_briefing"):
        if isinstance(out.get(key), dict):
            out[key] = {k: v for k, v in out[key].items() if k in keep}
    return out


# ─── 1. 종전 렌더 비트 동일 ───────────────────────────────────────────────


def test_without_playbook_key_render_is_unchanged() -> None:
    lines = [{"label": "요약", "text": "web-01 CPU 95%"}, {"label": "한계", "text": "x"}]
    base = {"status": "done", "investigation_id": "i", "briefing_lines": lines}
    plain = _render(base, {"note": "n"})
    # 운영자 뷰의 원 브리핑(playbook 없음) · 플레이북 없는 부분 결과 ·
    # 플레이북용 extra 키는 무시된다
    with_briefing = _render({**base, "briefing": {"summary": "s"}}, {"note": "n"})
    with_partial = _render(
        {**base, "partial_briefing": {"partial": True}},
        {"note": "n", "mode": "detail", "openConversation": True, "engineChoice": True},
    )
    assert plain == with_briefing == with_partial
    assert "bv--playbook" not in plain and "조사 대화로 열기" not in plain


# ─── 2. 요약형 — 5 픽스처 ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "name, chips, engine",
    [
        ("normal", ["S0 정상", "S1 정상", "S3 생략", "S8 미연동"], "HolmesGPT: 비교 불가"),
        ("latency_sql", ["S0 이상", "S3 SQL", "S5 참고", "S8 미연동"], "HolmesGPT: 같은 계층"),
        ("latency_external", ["S1 이상", "S3 외부"], "HolmesGPT: 다른 계층"),
        ("gc", ["S2 이상", "S4 이상"], "HolmesGPT: 다른 계층"),
        ("partial_missing", ["S3 결손", "S6 결손"], "HolmesGPT: 비교 불가"),
    ],
)
def test_summary_fixture(name: str, chips: list[str], engine: str) -> None:
    out = _render(_done_view(name), {"openConversation": True})
    assert "bv--playbook" in out and "플레이북 was.latency" in out
    for chip in chips:
        assert f">{chip}</span>" in out, chip
    assert out.count(">HolmesGPT: ") == 1 and engine in out  # 엔진 칩은 하나
    assert "S9" not in out  # 종합 스텝은 칩 줄에 없다(종합 칸이 맡는다)
    assert f'data-investigation-id="inv-{name}"' in out and "조사 대화로 열기" in out
    hyps = _briefing(name)["playbook"]["synthesis"]["hypotheses"]
    if hyps:
        assert "1순위" in out and hyps[0]["status_label"] in out
    # 요약형은 표·소견·4택을 싣지 않는다
    assert "<table" not in out and "data-choice" not in out


def test_summary_uses_synthesis_on_user_shaped_briefing() -> None:
    """W5a 사용자 경로(6요소 원문 없음)도 같은 요약을 그린다 — 종합은 `playbook.synthesis`에서."""
    view = _done_view("latency_sql")
    assert _render(_user_view(view), {"openConversation": True}) == _render(
        view, {"openConversation": True}
    )


def test_open_button_absent_without_option_and_disabled_when_busy() -> None:
    view = _done_view("latency_sql")
    assert "조사 대화로 열기" not in _render(view, {})
    busy = _render(view, {"openConversation": True, "busy": True})
    assert re.search(r'data-bv-action="open-conversation"[^>]*disabled', busy)


# ─── 3. 상세형 — 배치 순서 · 공통 판정 · 근거 먼저 ─────────────────────────


def _detail(name: str, **extra: Any) -> str:
    return _render(_done_view(name), {"mode": "detail", "engineChoice": True, "ui": {}, **extra})


def test_detail_layout_order_fixed() -> None:
    out = _detail("latency_sql")
    marks = ["단계 판정 — 공통", "엔진 비교", "종합(플레이북)", ">런북", "단계별 근거"]
    idx = [out.index(m) for m in marks]
    assert idx == sorted(idx), list(zip(marks, idx, strict=True))
    # 판정 칩(단계 칩)은 공통 칸 1회 — 엔진 비교 블록 안에는 없다
    engine = out[out.index("bv-engine") : out.index("bv-pb-synthesis")]
    assert "bv-step-chip" not in engine
    assert out.count("단계 판정 — 공통") == 1


def test_detail_engine_compare_same_frame_and_choice() -> None:
    out = _detail("latency_sql")
    for row in ("1순위 원인", "근거", "대응", "미결"):
        assert out.count(f">{row}</div>") >= 2, row  # 두 엔진이 같은 틀
    assert "대략적 대조" in out and "같은 계층" in out
    assert "dominant=sql" in out and "tool:apm_resource_pool" in out  # 근거 = enum·인용 도구
    # 각 엔진 칸에서 서술은 틀 아래 접힘(근거가 먼저)
    assert out.index("dominant=sql") < out.index("서술 보기")
    assert out.index("tool:apm_resource_pool") < out.rindex("서술 보기")
    for choice in ("playbook", "holmesgpt", "both", "neither"):
        assert f'data-choice="{choice}"' in out
    assert "선택은 판정·티어·통보를 바꾸지 않습니다" in out
    assert "data-choice" not in _render(_done_view("latency_sql"), {"mode": "detail"})


def test_detail_external_shows_disagreement() -> None:
    out = _detail("latency_external")
    assert "다른 계층" in out and "bv-engine" in out


def test_detail_sections_evidence_badges_and_guards() -> None:
    out = _detail("latency_sql")
    # 이상 단계는 기본 펼침 · 정상 단계는 접힘
    assert re.search(r'<details class="bv-step" data-bv-step="S3"[^>]*open>', out)
    assert re.search(r'<details class="bv-step" data-bv-step="S2"[^>]*"(?!\s*open)>', out)
    assert "게이트웨이 판정" in out and "판독 규칙" in out
    assert "bv-badge--live" in out and "현재값 · 알람 후" in out
    assert "AI 소견" in out
    assert "AI 소견을 싣지 않았습니다 — 소견 호출 상한" in out  # 가드로 버려진 소견은 사유
    assert "일부 결손: " in out  # 성공 단계의 결손도 침묵하지 않는다
    # S3 가로 누적 막대 + 범례 · 근거(표)가 소견보다 먼저
    s3 = out[out.index('data-bv-step="S3"') :]
    s3 = s3[: s3.index("</details>")]
    assert "bv-bar-seg--sql" in s3 and "bv-bar-legend" in s3
    assert s3.index("<table") < s3.index("AI 소견")
    # 가설 근거 단계 링크 · 대응 순서 아래 human-gated 문구
    assert 'data-bv-action="goto-step" data-step="S3"' in out
    assert "※ 실행은 운영자 승인 후 수동 — 시스템은 제안만(자동 실행 경로 없음)" in out
    assert "알람 160분 전" in out  # 변경 시점(minutes_before_alarm) 선후 표시


def test_detail_missing_steps_show_reason() -> None:
    out = _detail("partial_missing")
    s3 = out[out.index('data-bv-step="S3"') :]
    assert "결손" in s3[:400] and "bv-reason--fail" in s3[: s3.index("</details>")]


def test_detail_runbook_collapsed_with_stale_badge_and_known_errors() -> None:
    view = _done_view("latency_sql")
    view["briefing"]["playbook"]["runbook"]["stale"] = True
    view["briefing"]["known_errors"] = [
        {
            "id": "KE-2026-001",
            "title": "배치 시간대 풀 고갈",
            "state": "validated",
            "result": "match",
            "satisfied": ["dominant == 'sql'"],
            "missing_steps": [],
            "workaround": ["배치 지연"],
        },
    ]
    out = _render(view, {"mode": "detail"})
    assert re.search(r'<details class="bv-step bv-runbook" data-bv-key="runbook">', out)  # 접힘
    assert "검토 기한 경과" in out
    assert "KE-2026-001" in out and ">일치<" in out and "충족 조건" in out
    assert out.index("종합(플레이북)") < out.index("KE-2026-001") < out.index(">런북")
    summary = _render(view, {})
    assert "KE-2026-001" in summary and ">일치<" in summary  # 요약형 일치 배지


def test_references_block_marked_unrelated_to_verdict_and_safe_href() -> None:
    view = _done_view("normal")
    view["briefing"]["references"] = [
        {"title": "WAS 대응 매뉴얼", "section": "3.2", "excerpt": "풀 확인", "href": "/docs/a"},
        {"title": "나쁜 링크", "href": "javascript:alert(1)"},
    ]
    out = _render(view, {"mode": "detail"})
    assert "참고 · 판정 무관" in out and 'href="/docs/a"' in out
    assert "javascript:" not in out


def test_sparkline_for_series_cells() -> None:
    view = _done_view("normal")
    sec = next(s for s in view["briefing"]["playbook"]["sections"] if s["step"] == "S4")
    sec["table"] = {"columns": ["kind", "trend"], "rows": [["cpu", [10, 20, 90, 40]]]}
    out = _render(view, {"mode": "detail"})
    assert '<svg class="bv-spark"' in out and "<polyline" in out and "최대 90" in out


def test_xss_external_text_is_escaped() -> None:
    view = _done_view("latency_sql")
    pb = view["briefing"]["playbook"]
    evil = '<img src=x onerror="alert(1)">'
    pb["synthesis"]["hypotheses"][0]["title"] = evil
    pb["sections"][0]["opinion"] = evil
    pb["sections"][0]["table"]["rows"][0][0] = evil
    pb["sections"][0]["step"] = '"><script>1</script>'
    for extra in ({}, {"mode": "detail", "engineChoice": True}):
        out = _render(view, extra)
        assert "<img" not in out and "<script" not in out


# ─── 4. 진행 중 — 스켈레톤 · 심층 진행 중 ──────────────────────────────


def test_partial_collection_shows_progress_and_skeleton() -> None:
    view = _running_view("running_collect")
    summary = _render(view, {})
    assert "진행 중 S3/10" in summary and "S5 수집 중" in summary
    assert "종합은 모든 단계가 끝난 뒤 나옵니다." in summary
    assert "HolmesGPT: 진행 중" in summary
    detail = _render(view, {"mode": "detail", "engineChoice": True})
    assert "bv-step--skeleton" in detail and 'role="progressbar"' in detail
    assert "data-choice" not in detail  # 심층 진행 중엔 고를 결론이 없다
    assert _render(_user_view(view), {}) == summary


def test_deep_running_after_playbook_done() -> None:
    view = _running_view("running_deep")
    out = _render(view, {"mode": "detail", "engineChoice": True})
    assert "심층 조사가 진행 중입니다" in out and "data-choice" not in out
    assert "업무 DB 지연(공통 의존)" in out  # 플레이북 종합은 먼저 보인다


# ─── 5. attach — 4택 기록 · 404 비활성 · 열기 콜백 ─────────────────────


_HARNESS = """
var handler = null;
var container = {innerHTML: "", isConnected: true,
    addEventListener: function (t, f) { handler = f; },
    contains: function () { return true; }, querySelectorAll: function () { return []; }};
function click(attrs) {
    var el = {disabled: false,
      getAttribute: function (k) { return attrs[k] === undefined ? null : attrs[k]; }};
    handler({target: {closest: function () { return el; }}, preventDefault: function () {}});
}
function tick() { return new Promise(function (r) { setTimeout(r, 5); }); }
"""


def test_attach_engine_choice_records_and_disables_on_404() -> None:
    view = _done_view("latency_sql")
    out = _node(
        _HARNESS
        + f"var view = {json.dumps(view, ensure_ascii=False)};"
        + """
var calls = [];
var status = 200;
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "detail"},
    engineChoice: function (c) { calls.push(c); return {ok: status === 200, status: status}; }});
await panel.open();
click({"data-bv-action": "engine-choice", "data-choice": "holmesgpt"});
await tick();
var recorded = container.innerHTML;
status = 404;
click({"data-bv-action": "engine-choice", "data-choice": "both"});
await tick();
var off = container.innerHTML;
click({"data-bv-action": "engine-choice", "data-choice": "neither"});
await tick();
out = {calls: calls,
       recorded: recorded.indexOf("기록했습니다 — HolmesGPT") >= 0 &&
                 /data-choice="holmesgpt" aria-pressed="true"/.test(recorded),
       off: off.indexOf("엔진 선택 기록이 꺼져 있어") >= 0 &&
         /data-choice="playbook"[^>]*disabled/.test(off)};
panel.close();
"""
    )
    assert out == {"calls": ["holmesgpt", "both"], "recorded": True, "off": True}


def test_attach_open_conversation_callback() -> None:
    view = _done_view("gc")
    out = _node(
        _HARNESS
        + f"var view = {json.dumps(view, ensure_ascii=False)};"
        + """
var opened = [];
var panel = BV.attach(container, {initial: view, load: function () { return view; },
    render: {mode: "summary", openConversation: true},
    onOpenConversation: function (id) { opened.push(id); }});
await panel.open();
click({"data-bv-action": "open-conversation", "data-investigation-id": "inv-gc"});
out = {opened: opened, button: container.innerHTML.indexOf("조사 대화로 열기") >= 0};
panel.close();
"""
    )
    assert out == {"opened": ["inv-gc"], "button": True}


# ─── 6. JS 정적 — 소비자 배선 ─────────────────────────────────────────


def test_renderer_assigns_markup_only_in_attach() -> None:
    bv = _BV_JS.read_text(encoding="utf-8")
    attach = _js_function_body(bv, "attach")
    assert bv.count("innerHTML") == attach.count("innerHTML")
    assert "eval(" not in bv and "new Function" not in bv and "document.write" not in bv


def test_conversation_entry_from_query_string() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    entry = _js_function_body(src, "openInvestigationFromQuery")
    assert 'params.get("investigation")' in entry and "history.replaceState(" in entry
    assert entry.index("history.replaceState(") < entry.index("openInvestigationConversation(id)")
    assert "openInvestigationFromQuery();" in _js_function_body(src, "checkAuthOnLoad")
    opener = _js_function_body(src, "openInvestigationConversation")
    # (W16) 질의응답 탭이 아니라 「조사 대화」 탭으로 연다
    assert "isProcessing" in opener and 'setActiveView("investigation")' in opener
    assert opener.index("startNewChat()") < opener.index("pendingInvestigationRef = ")
    # 추천 칩은 내지 않는다(W0 — 골드셋 동형 문구 없음) · 확인 바 경로도 타지 않는다
    pin = _js_function_body(src, "renderInvestigationPin")
    for body in (opener, pin):
        assert "stageAlarmPrompt" not in body and "alarm-prompt-chip" not in body
    assert 'mode: "detail"' in pin and "/engine-choice" in pin
    assert "질의 없이 닫으면 이 대화는 남지 않습니다." in pin
    assert "지시어는 풀리지 않" in pin


def test_first_turn_header_restore_and_scope_chip() -> None:
    src = _APP_JS.read_text(encoding="utf-8")
    header = _js_function_body(src, "investigationRefHeader")
    assert '"X-Investigation-Ref"' in header and "!currentThreadId" in header
    assert src.count("investigationRefHeader())") == 4  # 질의 진입 4곳(텍스트·파일 × 일반·스트림)
    assert "resetInvestigationConversation()" in _js_function_body(src, "startNewChat")
    turns = _js_function_body(src, "showThreadTurns")
    assert "investigationRefOf(turns)" in turns and "restored: true" in turns
    scope = _js_function_body(src, "applyInvestigationScope")
    assert "loadScopeOptions()" in scope and "pendingDbIds = [dbId]" in scope
    assert "범위 밖" in scope  # 범위 밖이면 맞추지 않고 사유
    bind = _js_function_body(src, "bindAlarmBriefing")
    assert "openConversation: true" in bind
    assert "onOpenConversation: openInvestigationConversation" in bind
    assert "busy: function () { return isProcessing; }" in bind
    assert "BriefingView.setBusy(document" in _js_function_body(src, "setSendButtonMode")


def test_drawer_new_tab_and_break_glass_detail() -> None:
    src = _NOISE_JS.read_text(encoding="utf-8")
    assert 'var IS_BREAK_GLASS = tokenKey === "admin_token"' in src
    inv = _js_function_body(src, "renderInvestigation")
    assert 'IS_BREAK_GLASS ? { mode: "detail" } : { mode: "summary" }' in inv
    # (W16) 요약형 버튼 대신 ⓒ 머리 버튼(conversationButton)이 포털 「조사 대화」 탭으로
    # 연다 — 새 탭 직행 0
    assert "onView: onInvestigationView" in inv
    assert '"/?investigation="' not in src


@pytest.mark.parametrize(
    "page, asset",
    [
        ("index.html", "briefing-view.js?v=5"),
        ("index.html", "app.js?v=34"),
        ("index.html", "style.css?v=36"),
        ("noise.html", "briefing-view.js?v=5"),
        ("noise.html", "noise.js?v=18"),
        ("admin/noise.html", "briefing-view.js?v=5"),
        ("admin/noise.html", "noise.js?v=18"),
    ],
)
def test_cache_versions_bumped(page: str, asset: str) -> None:
    assert asset in (_STATIC / page).read_text(encoding="utf-8")


def test_fixtures_are_the_five_cases_plus_running() -> None:
    names = sorted(p.stem for p in _FIX.glob("*.json"))
    assert names == sorted([*_CASES, "running_collect", "running_deep"])
