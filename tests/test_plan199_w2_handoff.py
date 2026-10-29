"""plans/199 W2 — 담당자 전달문(`handoffText`) · [전달문 보기] · [전달문 복사] · D-351 ④.

`BriefingView.handoffText`(고정 양식 순수 함수)·`isRawView`(운영자 뷰 판정)·카드 버튼·`attach`
복사 처리를 node로 실행해 검사한다. W0 화면 픽스처(`tests/fixtures/plan199/`)는 읽기만 하고,
마스킹 경로 검사는 픽스처의 운영자 뷰·사용자 뷰 **같은 경로**의 서버 산출 문자열을 요약 재료 칸에
옮겨 만든다(사용자 뷰 쪽은 서버가 가린 값 그대로 — 손으로 가린 값을 쓰지 않는다).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from noise_gate.infrastructure.investigation_store import PLAYBOOK_VIEW_KEYS
from tests.test_plan199_w1_summary import _BRIEFING_BUILDER, _DIAG_JS, _js, _node, _view

_ROOT = Path(__file__).resolve().parents[1]
_CSS = _ROOT / "src" / "static" / "css" / "style.css"
_SECRET_RAW = ("abc123", "eyJhbGci")

# attach 하네스 — 클릭 처리기 · 펼침 상자(details) 대역 · 클립보드 대역(성공·실패·미지원)
_HARNESS = """
var handler = null;
var details = {open: false};
var container = {innerHTML: "", isConnected: true,
    addEventListener: function (t, f) { handler = f; },
    contains: function () { return true; }, querySelectorAll: function () { return []; },
    querySelector: function (sel) {
        return sel === 'details[data-bv-key="handoff"]' ? details : null;
    }};
function click(attrs) {
    var el = {disabled: false,
      getAttribute: function (k) { return attrs[k] === undefined ? null : attrs[k]; }};
    handler({target: {closest: function () { return el; }}, preventDefault: function () {}});
}
var written = [];
function clipboard(mode) {
    var nav = mode === "none" ? {} : {clipboard: {writeText: function (t) {
        if (mode === "reject") return Promise.reject(new Error("denied"));
        written.push(t);
        return Promise.resolve();
    }}};
    Object.defineProperty(globalThis, "navigator",
        {value: nav, configurable: true, writable: true});
}
function has(html, text) { return html.indexOf(text) >= 0; }
"""


def _handoff(view: dict[str, Any], opts: dict[str, Any] | None = None) -> str:
    return _node(f"out = BV.handoffText(BV.summaryModel({_js(view)}), {_js(opts)});")


def _lines(text: str) -> list[str]:
    return text.split("\n")


def _heads(text: str) -> list[str]:
    """줄 머리말(첫 「: 」 앞 · 첫 줄은 `[장애 전달]`)."""
    out = []
    for line in _lines(text):
        out.append("[장애 전달]" if line.startswith("[장애 전달]") else line.split(": ", 1)[0])
    return out


def _syn(view: dict[str, Any]) -> dict[str, Any]:
    return view["briefing"]["playbook"]["synthesis"]


# ─── 1. 양식 — 줄 순서 · 머리말 · 빈 항목 줄 생략 ──────────────────────────

_FULL_MODEL = {
    "status": {"code": "done", "kind": "done", "text": "완료", "at": ""},
    "area": {"layer": "cpu", "label": "WAS 애플리케이션(CPU 처리)", "certainty": "확정"},
    "conclusion": {"text": "WAS 요청 처리 부하 — CPU 주인은 WAS 인스턴스 A1",
                   "source": "playbook", "progress": ""},
    "target": {"name": "svr-was-01", "symptom": "CPU 사용률 95%", "at": "2026-10-26 13:15"},
    "scope": ["같은 사건 알람 2건(WAS 응답 지연)", "이상 인스턴스 1/2"],
    "actions": ["A1 /order/submit 의 ORDER_ITEM 조회 실행계획 확인"],
    "mustSee": [{"kind": "warn", "text": "S1 WAS 골든 시그널 수집 결손"},
                {"kind": "info", "text": "알려진 문제 KE-012 일치 · 우회 있음"}],
    "owner": None,
}


def test_full_form_matches_plan_layout() -> None:
    """계획서 §3.4 양식 — 머리말·줄 순서 그대로.

    여러 항목은 「 / 」로 끊는다(항목 문구 안의 「 · 」와 구분).
    """
    out = _node(f"out = BV.handoffText({_js(_FULL_MODEL)}, "
                '{id: "inv-1", link: "http://portal.local/?investigation=inv-1"});')
    assert out == "\n".join([
        "[장애 전달] svr-was-01 · CPU 사용률 95% · 발생 2026-10-26 13:15",
        "원인 영역: WAS 애플리케이션(CPU 처리) — 확정",
        "결론: WAS 요청 처리 부하 — CPU 주인은 WAS 인스턴스 A1",
        "영향 범위: 같은 사건 알람 2건(WAS 응답 지연) / 이상 인스턴스 1/2",
        "확인 요청: A1 /order/submit 의 ORDER_ITEM 조회 실행계획 확인",
        "주의: S1 WAS 골든 시그널 수집 결손 / 알려진 문제 KE-012 일치 · 우회 있음",
        "조사: inv-1 · 포털에서 근거 확인 http://portal.local/?investigation=inv-1",
    ])


def test_owner_line_only_when_owner_present_and_time_is_minute_form() -> None:
    """담당자 줄은 `owner`가 있을 때만(원인 영역 바로 뒤).

    조사 시각은 머리 줄과 같은 `YYYY-MM-DD HH:MM`(브라우저 지역 시각).
    카드 머리 표시(공용 fmtTime)는 그대로.
    """
    model = copy.deepcopy(_FULL_MODEL)
    model["owner"] = "DB팀 담당"
    model["status"]["at"] = "2026-10-26T04:19:00+00:00"
    out = _node(f"""
process.env.TZ = "Asia/Seoul";
var m = {_js(model)};
var card = BV.summaryCardHtml(m);
var shown = new Date(m.status.at).toLocaleString("ko-KR", {{ hour12: false }});
out = {{ text: BV.handoffText(m, {{ id: "inv-1" }}), inCard: card.indexOf(shown) >= 0,
        none: BV.handoffText(Object.assign({{}}, m, {{ owner: null }}), {{ id: "inv-1" }}) }};
""")
    lines = _lines(out["text"])
    assert lines[1].startswith("원인 영역: ") and lines[2] == "담당: DB팀 담당"
    assert lines[-1] == "조사: inv-1 · 2026-10-26 13:19" and out["inCard"]
    assert "담당: " not in out["none"]


def test_investigation_time_minute_form_local_raw_or_omitted() -> None:
    """「조사:」 시각 — 시간대 없는 값은 지역 시각 그대로 분까지.

    해석 안 되면 원문, 비면 시각 부분이 없다.
    """
    out = _node(f"""
process.env.TZ = "Asia/Seoul";
var m = {_js(_FULL_MODEL)};
function at(v) {{
    return BV.handoffText(Object.assign({{}}, m, {{ status: {{ at: v }} }}), {{ id: "inv-1" }});
}}
out = [at("2026-10-19T10:10:00"), at("2026-10-19T01:05:30Z"), at("어제 10시"), at("")];
""")
    assert [_lines(t)[-1] for t in out] == [
        "조사: inv-1 · 2026-10-19 10:10",
        "조사: inv-1 · 2026-10-19 10:05",
        "조사: inv-1 · 어제 10시",
        "조사: inv-1",
    ]


def test_empty_items_drop_their_lines() -> None:
    """영향 범위·할 일·주의가 비면 그 줄이 없다.

    대상이 없으면 머리말만, 조사 재료가 없으면 조사 줄도 없다.
    """
    model = copy.deepcopy(_FULL_MODEL)
    model.update(scope=[], actions=[], mustSee=[], target=None)
    out = _node(f"out = BV.handoffText({_js(model)});")
    assert _lines(out) == [
        "[장애 전달]",
        "원인 영역: WAS 애플리케이션(CPU 처리) — 확정",
        "결론: WAS 요청 처리 부하 — CPU 주인은 WAS 인스턴스 A1",
    ]
    # 모델이 비정상이면 빈 문자열(카드와 같은 조건)
    assert _node("out = [BV.handoffText(null), BV.handoffText({area: {}})];") == ["", ""]


def test_fixture_confirmed_case_all_lines() -> None:
    v = _view("confirmed_cpu_ke")
    text = _handoff(v, {"id": v["investigation_id"]})
    assert _heads(text) == [
        "[장애 전달]", "원인 영역", "결론", "영향 범위", "확인 요청", "주의", "조사"]
    lines = _lines(text)
    assert lines[0].startswith("[장애 전달] wasdemo01 · A1 · ")
    assert "발생 2026-10-19 10:00" in lines[0]
    assert lines[1] == "원인 영역: WAS 애플리케이션(CPU 처리) — 확정"
    assert "알려진 문제 KE-DEMO-012" in lines[5] and "우회 있음" in lines[5]
    assert lines[6].startswith("조사: inv-confirmed_cpu_ke · ")
    # 「조치 권고 없음(」 고정 표지는 확인 요청에 실리지 않는다(W1 모델 그대로)
    assert _node("out = BV.NO_ACTION_PREFIX;") not in text


def test_running_keeps_so_far_and_running_warning() -> None:
    for name in ("running_collect", "running_deep"):
        text = _handoff(_view(name))
        lines = _lines(text)
        concl = [x for x in lines if x.startswith("결론: ")]
        assert concl and concl[0].startswith("결론: 지금까지: "), (name, text)
        must = [x for x in lines if x.startswith("주의: ")]
        assert must and must[0].startswith("주의: 조사 진행 중"), (name, text)


def test_non_playbook_uses_same_form() -> None:
    text = _handoff(_view("non_playbook"), {"id": "inv-np"})
    assert _heads(text)[:3] == ["[장애 전달]", "원인 영역", "결론"]
    assert _lines(text)[1] == "원인 영역: 미확정"
    assert _lines(text)[-1].startswith("조사: inv-np")


def _area_cell(card: str) -> str:
    """카드 첫 행(원인 영역)의 값 칸 HTML."""
    m = re.search(
        r'<div class="bv-label">원인 영역</div><div class="bv-text">(.*?)</div></div>', card)
    assert m, card
    return m.group(1)


def test_unknown_area_wording_does_not_repeat_and_keeps_presumed() -> None:
    """원인 영역을 모르면 「원인 영역 미확정」 대신 「미확정」 — 같은 말이 겹치지 않게.

    확실성도 미확정이면 카드 배지 1개·전달문 「원인 영역: 미확정」. 추정이면 추정 신호를 남긴다
    (카드 「미확정」 글자 + [추정] 배지 · 전달문 「원인 영역: 미확정 — 추정」
    — 가설이 단정처럼 읽히지 않게).
    아는 영역(확정·추정)은 영역 이름 + 확실성 배지 그대로. 모델 값은 바뀌지 않는다.
    """
    hypo_view = {"status": "done", "investigation_id": "inv-h", "briefing_lines": [
        {"label": "원인 가설", "text": "[가설] 배치 겹침 CPU 포화"}]}
    out = _node(f"""
var base = {_js(_FULL_MODEL)};
function pick(area) {{
    var m = Object.assign({{}}, base, {{ area: area }});
    return {{ text: BV.handoffText(m), card: BV.summaryCardHtml(m) }};
}}
function real(v) {{
    var m = BV.summaryModel(v);
    return {{ text: BV.handoffText(m), card: BV.summaryCardHtml(m), area: m.area }};
}}
out = {{ und: pick({{ layer: "unknown", label: BV.LAYER_LABELS.unknown, certainty: "미확정" }}),
        pre: pick({{ layer: "unknown", label: BV.LAYER_LABELS.unknown, certainty: "추정" }}),
        known: pick({{ layer: "db", label: BV.LAYER_LABELS.db, certainty: "추정" }}),
        np: real({_js(_view("non_playbook"))}), hypo: real({_js(hypo_view)}) }};
""")

    def text_of(html: str) -> str:
        return re.sub(r"<[^>]+>", "", html)

    for key in ("und", "np"):
        case = out[key]
        assert _lines(case["text"])[1] == "원인 영역: 미확정", key
        cell = _area_cell(case["card"])
        assert cell.startswith("<span") and text_of(cell) == "미확정", cell   # 배지 1개뿐
        assert 'bv-chip--none">미확정</span>' in cell
    for key in ("pre", "hypo"):
        case = out[key]
        assert _lines(case["text"])[1] == "원인 영역: 미확정 — 추정", key
        cell = _area_cell(case["card"])
        assert text_of(cell) == "미확정 추정" and 'bv-chip--warn">추정</span>' in cell, cell
    for case in out.values():
        assert "원인 영역 미확정" not in case["card"] and "원인 영역 미확정" not in case["text"]
    known = out["known"]
    assert _lines(known["text"])[1] == "원인 영역: DB(SQL·커넥션 풀) — 추정"
    assert text_of(_area_cell(known["card"])) == "DB(SQL·커넥션 풀) 추정"
    # 모델은 바뀌지 않는다 — 엔진 비교 등 다른 소비처의 LAYER_LABELS 문구 그대로
    assert out["np"]["area"]["label"] == "원인 영역 미확정"
    assert out["hypo"]["area"] == {
        "layer": "unknown", "label": "원인 영역 미확정", "certainty": "추정"}


def test_different_layer_names_the_other_area() -> None:
    line = _lines(_handoff(_view("different_layer")))[1]
    assert line == "원인 영역: 외부 연계(외부 호출) — 추정 · 다른 분석은 DB(SQL·커넥션 풀) 영역"


def test_multiline_values_fold_to_one_line_and_raw_rule_ignores_prototype() -> None:
    """(교정 V-01 · O-1) 여러 줄 값(`\\r\\n`·`\\r`·`\\n`)은 공백으로 접혀 머리말 줄이 늘지 않는다 ·
    `isRawView`는 프로토타입 이름(`constructor` 등)을 표시용 키로 보지 않는다."""
    v = _view("confirmed_cpu_ke")
    _syn(v)["hypotheses"][0]["title"] = "첫 줄\r\n원인 영역: DB — 확정\r조사: 가짜"
    _syn(v)["actions"][0]["text"] = "행동 A\n\n주의: 가짜"
    failed = {"status": "failed", "investigation_id": "inv-x", "briefing_lines": [],
              "reason": "오류\r\n원인 영역: DB — 확정"}
    out = _node(f"""
var texts = {_js([v, failed])}.map(function (x) {{
    return BV.handoffText(BV.summaryModel(x), {{ id: "inv-x" }}); }});
out = {{ texts: texts,
        proto: ["constructor", "toString", "hasOwnProperty", "__proto__"].map(function (k) {{
            var b = {{}}; Object.defineProperty(b, k, {{ value: 1, enumerable: true }});
            return BV.isRawView({{ briefing: b }}); }}) }};
""")
    for text in out["texts"]:
        assert "\r" not in text, text
        heads = _heads(text)
        assert heads.count("원인 영역") == 1 and heads.count("조사") == 1, text
        assert heads.count("주의") <= 1, text
    lines = _lines(out["texts"][0])
    assert "결론: 첫 줄 원인 영역: DB — 확정 조사: 가짜" in lines
    assert any(x.startswith("확인 요청: 행동 A 주의: 가짜 / ") for x in lines), lines
    assert out["proto"] == [True, True, True, True]


# ─── 2. 마스킹 · 「원문 포함」 표지 ─────────────────────────────────────────


def _inject_secret_paths(view: dict[str, Any]) -> dict[str, Any]:
    """같은 경로의 서버 산출 문자열(심층 가설 발췌)을 결론·할 일 재료로 옮긴다 — 뷰마다 자기 값."""
    v = copy.deepcopy(view)
    hyps = v["briefing"]["deep_dive"]["hypotheses"]
    _syn(v)["hypotheses"][0]["title"] = hyps[1]   # Bearer 토큰 발췌
    _syn(v)["actions"][0]["text"] = hyps[2]       # password= 발췌
    return v


def test_user_view_handoff_carries_only_masked_values() -> None:
    from tests.test_plan199_w1_summary import _fixture

    pair = _fixture("user_view_secret")
    op = _handoff(_inject_secret_paths(pair["view"]))
    user = _handoff(_inject_secret_paths(pair["user_view"]))
    # 주입 경로가 실제로 전달문에 실린다(운영자 원문에는 비밀값이 보인다)
    assert "abc123" in op and "eyJhbGci" in op
    for raw in _SECRET_RAW:
        assert raw not in user, raw
    assert "Bearer ***" in user and "password=***" in user
    # 주입 없이도 사용자 뷰 전달문에 원문 비밀값 없음 — 두 사용자 뷰 픽스처 모두
    for name in ("user_view_secret", "episode_scope"):
        text = _handoff(_view(name, "user_view"), {"id": "inv-u"})
        for raw in _SECRET_RAW:
            assert raw not in text, (name, raw)


def test_raw_marker_only_on_operator_view() -> None:
    names = ("user_view_secret", "episode_scope")
    pairs = {n: {k: _view(n, k) for k in ("view", "user_view")} for n in names}
    out = _node(f"""
var ex = {{ summaryView: true, handoff: true }};
var names = ["user_view_secret", "episode_scope"];
var pairs = {_js(pairs)};
out = {{}};
names.forEach(function (n) {{
    ["view", "user_view"].forEach(function (k) {{
        var html = BV.renderBody(pairs[n][k], ex);
        out[n + "." + k] = [BV.isRawView(pairs[n][k]), html.indexOf("원문 포함") >= 0,
                            html.indexOf('data-bv-action="handoff-copy"') >= 0];
    }});
}});
""")
    for name in ("user_view_secret", "episode_scope"):
        assert out[f"{name}.view"] == [True, True, True], name
        assert out[f"{name}.user_view"] == [False, False, True], name


def test_queued_hides_handoff_buttons_and_raw_marker() -> None:
    """대기 중(`queue`가 칩을 줌)이면 전달문 버튼·「원문 포함」을 모두 뺀다(전달할 결과 없음).

    대기 칩·꼭 확인 대기 문구는 그대로.
    대기가 아니면(null·키 없음) 진행 중 카드도 종전처럼 버튼이 있다.
    """
    raw = _view("running_deep")   # 운영자 뷰 모양(`briefing` 키가 null로 실림)
    assert "briefing" in raw and raw["briefing"] is None
    user = {k: val for k, val in raw.items() if k != "briefing"}   # 사용자 뷰 모양(키 없음)
    queue = {"chip": "대기 중 · 순번 2", "note": "앞 조사가 끝나면 자동으로 시작합니다."}
    out = _node(f"""
var q = {_js(queue)};
out = {_js([user, raw])}.map(function (x) {{
    return ["summary", "detail"].map(function (mode) {{
        var base = {{ mode: mode, summaryView: true, handoff: true }};
        return [BV.renderBody(x, Object.assign({{ queue: function () {{ return q; }} }}, base)),
                BV.renderBody(x, Object.assign({{ queue: function () {{ return null; }} }}, base)),
                BV.renderBody(x, base)];
    }});
}});
""")
    for i, per_view in enumerate(out):
        for queued, nulled, absent in per_view:
            assert 'bv-chip--hold">대기 중 · 순번 2</span>' in queued
            assert "조사 대기 중 — 시작되면 결과가 채워집니다" in queued
            for marker in ('data-bv-action="handoff-copy"', "bv-handoff", "원문 포함"):
                assert marker not in queued, marker
            assert nulled == absent
            assert 'data-bv-action="handoff-copy"' in absent and "bv-handoff-text" in absent
            assert ("원문 포함" in absent) == (i == 1)


def test_raw_view_rule_follows_server_shape() -> None:
    """운영자 뷰 판정 = 서버 응답 모양.

    키 없음·표시용 키만 = 사용자 뷰 · null·원 브리핑 키 = 운영자 뷰.
    """
    keys = list(PLAYBOOK_VIEW_KEYS)
    out = _node(f"""
var keys = {_js(keys)};
out = {{
    absent: BV.isRawView({{ status: "done" }}),
    nullBriefing: BV.isRawView({{ status: "running", briefing: null }}),
    displayOnly: keys.map(function (k) {{
        var b = {{}}; b[k] = {{}}; return BV.isRawView({{ briefing: b }});
    }}),
    allDisplay: BV.isRawView({{
        briefing: keys.reduce(function (a, k) {{ a[k] = {{}}; return a; }}, {{}}) }}),
    withSummary: BV.isRawView({{ briefing: {{ playbook: {{}}, summary: "s" }} }}),
    running: BV.isRawView({_js(_view("running_collect"))}),
}};
""")
    assert out == {"absent": False, "nullBriefing": True, "displayOnly": [False] * len(keys),
                   "allDisplay": False, "withSummary": True, "running": True}


def test_sre_briefing_builders_always_emit_summary() -> None:
    """판정 전제 — 원 브리핑(운영자 뷰 `briefing`)은 빌더 3종 모두 `summary`를 싣는다.

    `summary`는 사용자 뷰 표시용 키 밖이다.
    """
    src = _BRIEFING_BUILDER.read_text(encoding="utf-8")
    for fn in ("build_briefing", "build_playbook_briefing", "build_partial_briefing"):
        body = src.split(f"def {fn}(", 1)[1].split("\ndef ", 1)[0]
        assert '"summary":' in body, fn
    assert "summary" not in PLAYBOOK_VIEW_KEYS


# ─── 3. 버튼 — 플래그 · 처리기 있는 곳에서만 · XSS ───────────────────────────


def test_buttons_only_with_flag_and_handler_marker() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(f"""
var v = {_js(v)};
function btn(html) {{ return html.indexOf('data-bv-action="handoff-copy"') >= 0; }}
out = {{
    on: btn(BV.renderBody(v, {{ summaryView: true, handoff: true }})),
    onDetail: btn(BV.renderBody(v, {{ mode: "detail", summaryView: true, handoff: true }})),
    noMarker: btn(BV.renderBody(v, {{ summaryView: true }})),
    off: btn(BV.renderBody(v, {{ handoff: true }})),
    direct: BV.summaryCardHtml(BV.summaryModel(v)),
    none: btn(BV.renderBody({{ status: "none", previous: [] }},
        {{ summaryView: true, handoff: true }})),
}};
""")
    assert out["on"] and out["onDetail"]
    assert not out["noMarker"] and not out["off"] and not out["none"]
    # 직접 렌더 경로(장애 진단 진척형 카드)는 빈 버튼 자리 그대로 — 처리기가 없다
    assert out["direct"].endswith(
        '<div class="bv-sum-actions" data-bv-summary-actions></div></section>')
    diag = _DIAG_JS.read_text(encoding="utf-8")
    assert "bv.summaryCardHtml(bv.summaryModel(partView))" in diag
    assert "handoff" not in diag


def test_button_order_and_preview_markup() -> None:
    view = _view("confirmed_cpu_ke")
    html = _node(f"out = BV.renderBody({_js(view)}, {{summaryView: true, handoff: true}});")
    slot = html.split('<div class="bv-sum-actions" data-bv-summary-actions>', 1)[1]
    copy_at = slot.index('data-bv-action="handoff-copy"')
    raw_at = slot.index("원문 포함")
    view_at = slot.index('<details class="bv-handoff" data-bv-key="handoff">'
                         '<summary class="bv-choice">전달문 보기</summary>')
    assert copy_at < raw_at < view_at
    # 미리보기는 접힌 채(재조회 유지는 keepOpenState의 data-bv-key) · 읽기 전용 <pre>
    assert re.search(r'<details class="bv-handoff" data-bv-key="handoff">', slot)
    assert '<pre class="bv-pre bv-handoff-text">[장애 전달] wasdemo01' in slot


def test_preview_escapes_untrusted_text() -> None:
    v = _view("confirmed_cpu_ke")
    _syn(v)["hypotheses"][0]["title"] = "<script>alert(1)</script>"
    v["briefing"]["playbook"]["scope"]["host"] = '"><img src=x onerror=alert(1)>'
    _syn(v)["actions"][0]["text"] = "</pre><b onmouseover=alert(3)>x</b>"
    out = _node(f"""
var v = {_js(v)};
out = {{ html: BV.renderBody(v, {{ summaryView: true, handoff: true }}),
        text: BV.handoffText(BV.summaryModel(v)) }};
""")
    html = out["html"]
    for raw in ("<script", "<img", "<b ", "</pre><b"):
        assert raw not in html, raw
    pre = html.split('<pre class="bv-pre bv-handoff-text">', 1)[1].split("</pre>", 1)[0]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in pre and "&quot;&gt;&lt;img" in pre
    # 전달문 자체는 평문(클립보드용) — 원 글자 그대로
    assert "<script>alert(1)</script>" in out["text"]


# ─── 4. attach — 복사 · 실패 안내 · 미리보기 펼침 ───────────────────────────


def test_attach_copy_success_writes_same_text_as_preview() -> None:
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
Object.defineProperty(globalThis, "location",
    {{value: {{ origin: "http://portal.local" }}, configurable: true}});
clipboard("ok");
var v = {_js(v)};
var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
    summaryView: function () {{ return true; }} }});
await panel.open();
var before = container.innerHTML;
click({{ "data-bv-action": "handoff-copy" }});
await tick(); await tick();
panel.close();
var want = BV.handoffText(BV.summaryModel(v), {{ id: v.investigation_id,
    link: "http://portal.local/?investigation=" + v.investigation_id }});
out = {{ written: written, want: want, handler: !!handler, btn: has(before, "handoff-copy"),
        msg: has(container.innerHTML, BV.PB_TEXT.copied), open: details.open }};
""")
    assert out["handler"] and out["btn"]
    assert out["written"] == [out["want"]]
    assert out["want"].endswith(
        "포털에서 근거 확인 http://portal.local/?investigation=inv-confirmed_cpu_ke")
    assert out["msg"] and out["open"] is False


def test_attach_copy_failure_shows_copykb_notice_and_opens_preview() -> None:
    v = _view("running_collect")
    out = _node(_HARNESS + f"""
var v = {_js(v)};
var res = {{}};
var modes = ["reject", "none"];
for (var i = 0; i < modes.length; i++) {{
    clipboard(modes[i]);
    details.open = false;
    var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
        render: {{ mode: "detail" }}, summaryView: true }});
    await panel.open();
    click({{ "data-bv-action": "handoff-copy" }});
    await tick(); await tick();
    panel.close();
    res[modes[i]] = [has(container.innerHTML, BV.PB_TEXT.copyFailed), details.open, written.length];
}}
out = res;
""")
    # copyKb와 같은 안내(「내용을 직접 선택해 복사하세요」) + 미리보기 펼침 · 아무것도 쓰지 않음
    assert out == {"reject": [True, True, 0], "none": [True, True, 0]}
    assert "직접 선택해 복사" in _node("out = BV.PB_TEXT.copyFailed;")


def test_attach_flag_off_has_no_buttons_and_same_extra() -> None:
    """기능 꺼짐 — 버튼 없음 · attach가 렌더에 넘기는 extra도 종전 그대로(새 키 없음)."""
    v = _view("confirmed_cpu_ke")
    out = _node(_HARNESS + f"""
var v = {_js(v)};
var panel = BV.attach(container, {{ initial: v, load: function () {{ return v; }},
    render: {{ mode: "summary" }}, summaryView: function () {{ return false; }} }});
await panel.open();
panel.close();
out = {{ same: container.innerHTML === BV.renderBody(v, {{ mode: "summary", engineChoice: false,
        ui: {{ choice: null, choiceMsg: "", choiceOff: "", pending: false }} }}),
        btn: has(container.innerHTML, "handoff-copy") }};
""")
    assert out == {"same": True, "btn": False}


# ─── 5. CSS ─────────────────────────────────────────────────────────────


def test_css_handoff_rules_follow_summary_block() -> None:
    css = _CSS.read_text(encoding="utf-8")
    at = css.index(".bv-sum-actions:empty { display: none; }")
    tail = css[at:at + 900]
    sels = (".bv-handoff > summary", ".bv-handoff[open]", ".bv-handoff-text", ".bv-handoff-raw")
    for sel in sels:
        assert sel in tail, sel
    block = tail.split("/* plans/187 FE-2", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,6}\b", block)   # 테마 토큰만
