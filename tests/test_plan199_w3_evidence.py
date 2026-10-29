"""plans/199 W3 — 상세형 3단(① 카드 → ② 근거 요약 → ③ 상세) · 펼침 기억 · D-351 ①.

`BriefingView.renderBody`(상세형 · `summaryView` 켜짐)·`attach`(카드 [근거 보기]·
[자세히] · 펼침 기억 · 단계 이동)를 node로 실행해 검사한다. W0 화면 픽스처
(`tests/fixtures/plan199/`)는 읽기만 하고, 정렬·이스케이프·런북 기한 같은 경계 입력은
테스트 안에서 사본을 고쳐 만든다. 플래그 off 비트 동일은 W1
(`test_flag_off_render_is_bit_identical_to_base` · `…attach…`)이 기준 SHA 원문과
대조한다 — 여기서는 off에서 저장소를 건드리지 않는 것만 본다. 끝으로 사용자 뷰 응답의
서버 경계(운영자 전용 키 미포함)를 확인한다.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from noise_gate.infrastructure.investigation_store import PLAYBOOK_VIEW_KEYS, investigation_view
from tests.test_plan199_w1_summary import _FIX, _js, _node, _view

_ROOT = Path(__file__).resolve().parents[1]
_CSS = _ROOT / "src" / "static" / "css" / "style.css"
_PLAYBOOK_CASES = [
    "confirmed_cpu_ke", "confirmed_same_layer", "deep_failed", "deep_skipped", "different_layer",
    "episode_scope", "evidence_missing", "markdown_narrative", "missing_many", "running_collect",
    "running_deep", "user_view_secret",
]
_DETAIL_ON = {"mode": "detail", "summaryView": True}

# attach 하네스 — 이벤트 종류별 처리기 · ②·③ details 대역 · 단계 대역 · localStorage 대역(정상·차단)
_HARNESS = """
var handlers = {};
var tiers = {evidence: {open: false, scrolled: 0, scrollIntoView: function () { this.scrolled++; }},
             detail: {open: false, scrolled: 0, scrollIntoView: function () { this.scrolled++; }}};
var stepSec = {open: false, scrolled: 0, scrollIntoView: function () { this.scrolled++; },
               getAttribute: function (k) { return k === "data-bv-step" ? "S5" : null; }};
var asked = [];
var container = {innerHTML: "", isConnected: true,
    addEventListener: function (t, f) { handlers[t] = f; },
    contains: function () { return true; },
    querySelectorAll: function (sel) { return sel === "[data-bv-step]" ? [stepSec] : []; },
    querySelector: function (sel) {
        asked.push(sel);
        var m = /^details\\[data-bv-key="(\\w+)"\\]$/.exec(sel);
        return m && tiers[m[1]] ? tiers[m[1]] : null;
    }};
function click(attrs) {
    var el = {disabled: false,
      getAttribute: function (k) { return attrs[k] === undefined ? null : attrs[k]; }};
    handlers.click({target: {closest: function () { return el; }}, preventDefault: function () {}});
}
function toggle(key, open) {
    handlers.toggle({target: {open: open,
        getAttribute: function (k) { return k === "data-bv-key" ? key : null; }}});
}
var gets = [], sets = [];
function storage(mode, saved) {
    var s = mode === "throw" ? {
        getItem: function (k) { gets.push(k); throw new Error("SecurityError"); },
        setItem: function (k, v) { sets.push([k, v]); throw new Error("QuotaExceeded"); },
    } : {
        getItem: function (k) {
            gets.push(k);
            return saved && saved[k] !== undefined ? saved[k] : null;
        },
        setItem: function (k, v) { sets.push([k, v]); },
    };
    Object.defineProperty(globalThis, "localStorage",
        {value: s, configurable: true, writable: true});
}
function has(html, text) { return html.indexOf(text) >= 0; }
"""


def _render(view: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
    return _node(f"out = BV.renderBody({_js(view)}, {_js(extra or _DETAIL_ON)});")


def _renders(views: list[dict[str, Any]]) -> list[str]:
    return _node(
        f"out = {_js(views)}.map(function (v) {{ return BV.renderBody(v, {_js(_DETAIL_ON)}); }});")


def _span(html: str, start: int, tag: str) -> tuple[int, int]:
    """`start`에서 열린 `<tag`의 짝 닫힘까지 — 같은 태그 중첩을 센다."""
    depth = 0
    for m in re.finditer(rf"<{tag}[\s>]|</{tag}>", html[start:]):
        depth += -1 if m.group(0).startswith("</") else 1
        if depth == 0:
            return start, start + m.end()
    raise AssertionError(f"<{tag}> 짝 없음")


def _regions(html: str) -> dict[str, str]:
    """카드 · ② · ③ 본문과 나머지(바깥)를 나눈다."""
    card = _span(html, html.index('<section class="bv-sum"'), "section")
    ev = _span(html, html.index('<details class="bv-ev bv-ev--evidence"'), "details")
    dt = _span(html, html.index('<details class="bv-ev bv-ev--detail"'), "details")
    outside = html
    for a, b in sorted((card, ev, dt), reverse=True):
        outside = outside[:a] + outside[b:]
    return {"card": html[card[0]:card[1]], "evidence": html[ev[0]:ev[1]],
            "detail": html[dt[0]:dt[1]], "outside": outside}


def _briefing(view: dict[str, Any]) -> dict[str, Any]:
    return view.get("briefing") or view["partial_briefing"]


def _section(view: dict[str, Any], step: str) -> dict[str, Any]:
    return next(s for s in _briefing(view)["playbook"]["sections"] if s.get("step") == step)


def _ev_text() -> dict[str, Any]:
    return _node("out = BV.EV_TEXT;")


# ─── 1. 배치 — 카드 → ② → ③ · 기본 접힘 · 반복 없음 ─────────────────────────


def test_detail_on_orders_card_evidence_detail_closed_by_default() -> None:
    views = [_view(n) for n in _PLAYBOOK_CASES]
    for name, html in zip(_PLAYBOOK_CASES, _renders(views), strict=True):
        card = html.index('data-bv-summary="1"')
        ev = html.index('<details class="bv-ev bv-ev--evidence" data-bv-key="evidence">')
        dt = html.index('<details class="bv-ev bv-ev--detail" data-bv-key="detail">')
        assert card < ev < dt, name
        assert html.count('data-bv-key="evidence"') == 1, name
        assert html.count('data-bv-key="detail"') == 1, name
    ev_text = _ev_text()
    html = _render(_view("confirmed_cpu_ke"))
    assert f'<summary class="bv-ev-head">{ev_text["evidenceHead"]}</summary>' in html
    assert f'<summary class="bv-ev-head">{ev_text["detailHead"]}</summary>' in html


def test_conclusion_blocks_live_only_in_tiers() -> None:
    """같은 결론은 카드 한 곳만 펼친 채 — 가설·엔진 비교·종합·단계는 ②·③ 안에만, 가설 표는 1벌."""
    names = [n for n in _PLAYBOOK_CASES if n != "running_collect"]
    for name, html in zip(names, _renders([_view(n) for n in names]), strict=True):
        parts = _regions(html)
        out = parts["outside"]
        for text in ("원인 가설 순위", "bv-engine", "종합(플레이북)", "bv-steps", "bv-pb-meta"):
            assert text not in out, (name, text)
        assert "bv-hypo-table" not in html, name
        assert html.count('<div class="bv-pb-block bv-engine">') == 1, name
        assert html.count('data-bv-slot="hypothesis"') == 1, name
        ev = parts["evidence"]
        # 엔진 비교 · 가설 표 · 단계 스트립은 ②, 단계별 근거 · 종합은 ③
        assert '<div class="bv-pb-block bv-engine">' in ev, name
        assert 'data-bv-slot="hypothesis"' in ev, name
        assert "bv-strip-chip" in ev and "bv-strip-chip" not in parts["detail"], name
        assert "종합(플레이북)" in parts["detail"] and "bv-pb-meta" in parts["detail"], name
        assert html.count('data-bv-key="narr-hg"') <= 1, name


def test_engine_narrative_box_moves_into_evidence_once() -> None:
    for name in ("confirmed_cpu_ke", "different_layer", "markdown_narrative"):
        parts = _regions(_render(_view(name)))
        assert parts["evidence"].count('data-bv-key="narr-hg"') == 1, name
        assert 'data-bv-key="narr-hg"' not in parts["detail"] + parts["outside"], name


# ─── 2. 단계 스트립 — S번호 자연 정렬 · 사람 말 라벨 · ③ 단계 연결 ──────────────────


_STRIP_CHIP = re.compile(
    r'<button type="button" class="bv-step-chip bv-step-chip--(\w+) bv-strip-chip" '
    r'data-bv-action="goto-step" data-step="([^"]*)">(.*?) '
    r'<span class="bv-strip-no">([^<]*)</span></button>')


def test_strip_sorts_step_numbers_naturally() -> None:
    v = _view("confirmed_cpu_ke")
    pb = _briefing(v)["playbook"]
    base = next(s for s in pb["sections"] if s.get("step") == "S1")
    steps = ["S10", "S2.pool", "S0r", "S2", "S1"]
    pb["sections"] = [dict(copy.deepcopy(base), step=s, title=f"단계 {s}") for s in steps]
    strip = _regions(_render(v))["evidence"]
    assert [m[1] for m in _STRIP_CHIP.findall(strip)] == ["S0r", "S1", "S2", "S2.pool", "S10"]


def test_strip_labels_are_plain_words_and_link_detail_steps() -> None:
    v = _view("confirmed_cpu_ke")
    parts = _regions(_render(v))
    chips = _STRIP_CHIP.findall(parts["evidence"])
    titles = {s["step"]: s["title"] for s in _briefing(v)["playbook"]["sections"]}
    # S9 종합은 단계 칸이 아니다(종전 그대로)
    assert [c[1] for c in chips] == [f"S{i}" for i in range(9)]
    for kind, step, label, tail in chips:
        assert label.startswith(titles[step]) and tail == step and kind, step
    linked = set(re.findall(r'data-bv-step="([^"]+)"', parts["detail"]))
    assert {c[1] for c in chips} <= linked


# ─── 3. 미니 타임라인 — 구조 필드만 · 2점 미만이면 없음 · 텍스트 이스케이프 ─────────────────


def test_timeline_draws_lanes_from_step_tables() -> None:
    ev = _regions(_render(_view("confirmed_cpu_ke")))["evidence"]
    svg = ev[ev.index("<svg"):ev.index("</svg>")]
    assert svg.startswith('<svg class="bv-tl" viewBox="0 0 600 66" role="img">')
    # S5 −8분 alarm → 인프라 · S5 0분 apm_event → APM · S7 50분 전 apm_source_changes → APM
    assert svg.count("bv-tl-dot--infra") == 1 and svg.count("bv-tl-dot--apm") == 2
    assert "알람 8분 전 · S5 · [severity 3] CPU 사용률 임계 초과 (wasdemo01)" in svg
    assert "알람 50분 전 · S7 · A1 소스 변경 감지" in svg
    assert "알람 시각 2026-10-19 10:00" in ev


def test_timeline_skipped_under_two_points() -> None:
    """deep_failed — S5 0분 1건뿐(S7 빈 표) → 그림 없음. running_collect — 재료 없음."""
    for name in ("deep_failed", "running_collect"):
        assert "<svg" not in _render(_view(name)), name


def test_timeline_escapes_event_text_and_ignores_non_numeric() -> None:
    v = _view("deep_failed")
    sec = _section(v, "S5")
    sec["table"]["rows"] = [
        [-5, "alarm", "<script>alert(1)</script>"],
        [0, "apm_event", 'x" onload="alert(2)'],
        ["n/a", "alarm", "숫자 아님"],
    ]
    html = _render(v)
    svg = html[html.index("<svg"):html.index("</svg>")]
    assert svg.count("<circle") == 2 and "숫자 아님" not in svg
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in svg
    assert "<script>" not in html and 'onload="alert(2)' not in html
    # 속성값은 숫자·고정 class만
    for attr in re.findall(r'\s(\w[\w-]*)="([^"]*)"', svg):
        assert attr[0] in {"class", "viewBox", "role", "x", "y", "x1", "y1", "x2", "y2",
                           "cx", "cy", "r", "text-anchor"}, attr


# ─── 4. 가설 표 슬롯 — 3칸 · 코드는 운영자 뷰만 ───────────────────────────────


def test_hypothesis_grid_three_columns_and_code_only_in_operator_view() -> None:
    fx = json.loads((_FIX / "episode_scope.json").read_text(encoding="utf-8"))
    op, user = _renders([fx["view"], fx["user_view"]])
    cols = _ev_text()["hypoColumns"]
    for html in (op, user):
        grid = _regions(html)["evidence"]
        grid = grid[grid.index('data-bv-slot="hypothesis"'):]
        heads = re.findall(r'<span class="bv-hypo bv-hypo--(\w+)">([^<]+)</span> '
                           r'<span class="bv-meta">(\d+)</span>', grid)
        # H-DB-SHARED 판단 보류 1 · H-DB-POOL-CHAIN·H-DB-INSTANCE 반박 2
        assert heads == [(c[1], c[0], n) for c, n in zip(cols, ("0", "1", "2"), strict=True)]
    assert _regions(op)["evidence"].count("bv-ev-code") == 3 and "H-DB-SHARED" in op
    assert "bv-ev-code" not in user and not re.search(r"H-[A-Z]+-", user)


def test_hypothesis_unknown_status_goes_to_hold_with_badge() -> None:
    v = _view("confirmed_same_layer")
    hyps = _briefing(v)["playbook"]["synthesis"]["hypotheses"]
    hyps[0]["status_label"] = "검토 중"
    hyps[0].pop("status", None)
    grid = _regions(_render(v))["evidence"]
    grid = grid[grid.index('data-bv-slot="hypothesis"'):]
    hold = grid[grid.index(">판단 보류<"):grid.index(">반박<")]
    assert hyps[0]["title"] in hold and "검토 중" in hold


# ─── 5. 담당자용 꼭 확인 ────────────────────────────────────────────────


def _must(view: dict[str, Any]) -> str:
    ev = _regions(_render(view))["evidence"]
    at = ev.index('<div class="bv-pb-block bv-ev-must">')
    return ev[at:]


def test_owner_must_engine_mismatch_and_deep_gaps() -> None:
    assert ("엔진 간 판단 불일치 — 플레이북 외부 연계(외부 호출) · 심층 조사 DB(SQL·커넥션 풀)"
            in _must(_view("different_layer")))
    failed = _must(_view("deep_failed"))
    assert "심층 조사 실패 — RuntimeError: holmes 연결 실패" in failed
    assert "심층 조사 실패 — 심층 조사 실패" not in failed       # 사유 머리를 겹쳐 쓰지 않는다
    assert "심층 조사 생략 — 심층 모드 off — 플레이북만 실행" in _must(_view("deep_skipped"))


def test_owner_must_lists_all_limitations_and_known_error_detail() -> None:
    v = _view("confirmed_cpu_ke")
    b = _briefing(v)
    must = _must(v)
    for line in b["limitations"] + b["deep_dive"]["limitations"]:
        assert f"<li>{line}</li>" in must, line
    assert must.count("<li>프로세스·자원 스냅샷은 조사 시점 단면일 수 있음</li>") == 1   # 중복 1회
    assert "알려진 문제" in must and "KE-DEMO-012" in must


def test_owner_must_user_view_has_deep_limitations_only() -> None:
    fx = json.loads((_FIX / "user_view_secret.json").read_text(encoding="utf-8"))
    must = _must(fx["user_view"])
    assert "<li>프로세스·자원 스냅샷은 조사 시점 단면일 수 있음</li>" in must
    assert "상관 ≠ 인과" not in must                     # 최상위 limitations는 운영자 뷰에만 실린다
    assert "엔진 간 판단 불일치 — 플레이북 외부 연계(외부 호출)" in must


def test_owner_must_stale_runbook_and_empty_case() -> None:
    v = _view("confirmed_same_layer")
    rb = _briefing(v).get("runbook") or _briefing(v)["playbook"]["runbook"]
    rb.update(stale=True, last_reviewed="2026-01-02")
    assert "런북 검토 기한 경과 — 마지막 검토 2026-01-02" in _must(v)
    empty = _view("confirmed_same_layer")
    b = _briefing(empty)
    b.pop("limitations", None)
    b["deep_dive"].pop("limitations", None)
    b["playbook"].pop("known_errors", None)
    assert "bv-ev-must" not in _render(empty)            # 해당 없으면 칸 없음


# ─── 6. 카드 버튼 · attach(펼침 기억 · 이동) ───────────────────────────────────


def test_tier_buttons_only_in_attached_detail() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
storage("ok");
var v = {_js(v)};
var np = {_js(_view("non_playbook"))};
function shown(view, render) {{
    var panel = BV.attach(container, {{ initial: view, load: function () {{ return view; }},
        render: render, summaryView: true }});
    return panel.open().then(function () {{ panel.close(); return container.innerHTML; }});
}}
var detail = await shown(v, {{ mode: "detail" }});
out = {{ detail: detail, summary: await shown(v, {{ mode: "summary" }}),
        nonPlaybook: await shown(np, {{ mode: "detail" }}),
        direct: BV.renderBody(v, {{ mode: "detail", summaryView: true }}) }};
""")
    html = out["detail"]
    ev_btn = html.index('data-bv-action="expand" data-bv-target="evidence"')
    dt_btn = html.index('data-bv-action="expand" data-bv-target="detail"')
    assert html.index('data-bv-action="handoff-copy"') < ev_btn < dt_btn
    card = _regions(html)["card"]
    assert 'data-bv-target="evidence"' in card and 'data-bv-target="detail"' in card
    for key in ("summary", "nonPlaybook", "direct"):
        assert 'data-bv-action="expand"' not in out[key], key


def test_expand_memory_restores_saved_open_state() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
var v = {_js(v)};
var res = {{}};
var cases = [["ok", {{ "bv.expand.evidence": "1", "bv.expand.detail": "0" }}], ["throw", null]];
for (var i = 0; i < cases.length; i++) {{
    gets = []; storage(cases[i][0], cases[i][1]);
    var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
        render: {{ mode: "detail" }}, summaryView: function () {{ return true; }} }});
    await panel.open(); await panel.open();
    panel.close();
    var h = container.innerHTML;
    res[cases[i][0]] = [has(h, 'data-bv-key="evidence" open>'),
        has(h, 'data-bv-key="detail" open>'), gets.slice().sort()];
}}
out = res;
""")
    keys = ["bv.expand.detail", "bv.expand.evidence"]
    assert out["ok"] == [True, False, keys]               # 켜진 첫 렌더에 1회만 읽는다
    assert out["throw"] == [False, False, keys]           # 저장소 차단 — 기본 접힘 · 예외 없음


def test_expand_toggle_writes_only_on_change() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
var v = {_js(v)};
storage("ok");
var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
    render: {{ mode: "detail" }}, summaryView: true }});
await panel.open();
toggle("evidence", true);
toggle("evidence", true);          // 같은 값(다시 그리기 · 열림 유지 복원) — 쓰지 않음
toggle("handoff", true);           // 펼침 기억 대상 아님
toggle("detail", true);
toggle("evidence", false);
var okSets = sets.slice();
sets = []; storage("throw");
toggle("detail", false);           // 쓰기 실패 — 던지지 않음
panel.close();
out = {{ ok: okSets, thrown: sets }};
""")
    assert out["ok"] == [
        ["bv.expand.evidence", "1"], ["bv.expand.detail", "1"], ["bv.expand.evidence", "0"]]
    assert out["thrown"] == [["bv.expand.detail", "0"]]


def test_expand_and_goto_step_open_tiers() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
var v = {_js(v)};
storage("ok");
var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
    render: {{ mode: "detail" }}, summaryView: true }});
await panel.open();
click({{ "data-bv-action": "expand", "data-bv-target": "evidence" }});
var afterEvidence = [tiers.evidence.open, tiers.evidence.scrolled, tiers.detail.open];
asked = [];
click({{ "data-bv-action": "expand", "data-bv-target": "handoff" }});
var unknownAsked = asked.slice();
click({{ "data-bv-action": "goto-step", "data-step": "S5" }});
panel.close();
out = {{ evidence: afterEvidence, unknown: unknownAsked,
        detail: tiers.detail.open, step: [stepSec.open, stepSec.scrolled] }};
""")
    assert out["evidence"] == [True, 1, False]
    assert out["unknown"] == []                          # 기억 대상 아닌 키는 찾지도 않는다
    assert out["detail"] is True and out["step"] == [True, 1]


def test_flag_off_touches_no_storage() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
var v = {_js(v)};
storage("ok");
var opts = [{{ render: {{ mode: "detail" }}, summaryView: function () {{ return false; }} }},
            {{ render: {{ mode: "detail" }} }}, {{ summaryView: false }}];
var res = [];
for (var i = 0; i < opts.length; i++) {{
    handlers = {{}};
    opts[i].initial = v; opts[i].load = function () {{ return v; }};
    var panel = BV.attach(container, opts[i]);
    await panel.open();
    if (handlers.toggle) toggle("evidence", true);
    panel.close();
    res.push([has(container.innerHTML, 'data-bv-key="evidence"'),
              has(container.innerHTML, 'data-bv-action="expand"')]);
}}
out = {{ res: res, gets: gets, sets: sets }};
""")
    assert out == {"res": [[False, False]] * 3, "gets": [], "sets": []}


# ─── 7. CSS ─────────────────────────────────────────────────────────────


def test_css_w3_rules_use_theme_tokens() -> None:
    css = _CSS.read_text(encoding="utf-8")
    at = css.index("/* plans/199 W3")
    block = css[at:css.index("/* plans/187 FE-2", at)]
    for sel in (".bv-ev ", ".bv-ev > .bv-ev-head", ".bv-ev-hypo", ".bv-strip-chip", ".bv-tl ",
                ".bv-tl-dot--infra", ".bv-tl-dot--apm"):
        assert sel in block, sel
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block)


# ─── 8. 서버 경계 — 사용자 뷰에 운영자 전용 키 없음 ──────────────────────────────


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


@pytest.mark.parametrize("name", ["episode_scope", "user_view_secret"])
def test_user_view_fixture_has_no_operator_only_keys(name: str) -> None:
    uv = json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))["user_view"]
    assert "owner_sub" not in _keys(uv)
    assert set(uv["briefing"]) <= set(PLAYBOOK_VIEW_KEYS)
    assert "ai_note" not in json.dumps(uv, ensure_ascii=False)


def test_investigation_view_user_drops_owner_and_raw_briefing() -> None:
    op = json.loads((_FIX / "user_view_secret.json").read_text(encoding="utf-8"))["view"]
    rec = {"status": "done", "investigation_id": "inv-w3", "kind": "followup",
           "owner_sub": "user-77", "briefing_lines": op["briefing_lines"],
           "briefing": op["briefing"], "reason": op.get("reason")}
    user = investigation_view(copy.deepcopy(rec), mask_fn=lambda s: s.replace("abc123", "***"))
    oper = investigation_view(copy.deepcopy(rec), mask_fn=lambda s: s, operator_view=True)
    assert "owner_sub" not in _keys(user) and oper["owner_sub"] == "user-77"
    assert set(user["briefing"]) <= set(PLAYBOOK_VIEW_KEYS)
    assert "limitations" not in user["briefing"] and "limitations" in oper["briefing"]
    assert oper["briefing"] == rec["briefing"]
