"""plans/201 W4 — `duty_view` 수신·저장·조회·화면 (D-354).

- 동기 챗 경로 `render_briefing_lines`: 키가 있으면 「영역별 점검」 인계 문구 1줄 · 없으면 비트 동일
- 저장(`InvestigationStore.record` → 32KB 축소) → 조회(`investigation_view`) 사용자·운영자 뷰 모두 보존
- 화면(`briefing-view.js`): 키 없으면 렌더 동일 · 자리 → textContent로만 채움 · 내 담당 먼저 · 어휘 사본 없음
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from noise_gate.domain.investigation_briefing import render_briefing_lines
from noise_gate.infrastructure.investigation_store import (
    PLAYBOOK_VIEW_KEYS,
    InvestigationStore,
    clip_briefing,
    investigation_view,
)

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"
_BV_JS = _STATIC / "js" / "briefing-view.js"
_APP_JS = _STATIC / "js" / "app.js"
_FIX = _ROOT / "tests" / "fixtures" / "plan199"

HANDOFF = "의심 신호가 있는 영역은 WAS/미들웨어·DB입니다 — 2개 영역 담당의 확인이 필요합니다."
DUTY_VIEW: dict[str, Any] = {
    "areas": [
        {"code": "server_os", "label": "서버/OS", "status": "normal", "status_label": "정상",
         "signals": ["호스트 자원 포화 none (S6 ← polestar_metric_trend)"], "steps": ["S6"]},
        {"code": "was_mw", "label": "WAS/미들웨어", "status": "suspect", "status_label": "의심 · 확인 필요",
         "signals": ["DB 지연 → 풀 고갈 — 판단 보류 (S3 ← apm_slow_transactions)"], "steps": ["S3"],
         "reason": "획득·실행 구분 정보 없음", "tag": "외부 연계"},
        {"code": "db", "label": "DB", "status": "unverified", "status_label": "확인 안 됨",
         "signals": [], "steps": [], "reason": "S3 느린 트랜잭션 Top 시간 분해: 생략"},
    ],
    "handoff": HANDOFF,
}


def _briefing() -> dict[str, Any]:
    view = json.loads((_FIX / "confirmed_same_layer.json").read_text(encoding="utf-8"))["view"]
    return copy.deepcopy(view["briefing"])


def _with_duty(briefing: dict[str, Any]) -> dict[str, Any]:
    """sre_agent 빌더와 같은 위치(`summary` 바로 뒤)에 키를 넣는다."""
    out: dict[str, Any] = {}
    for k, v in briefing.items():
        out[k] = v
        if k == "summary":
            out["duty_view"] = copy.deepcopy(DUTY_VIEW)
    return out


# ── 동기 챗 경로 ────────────────────────────────────────────────────────────


def test_render_lines_without_key_unchanged() -> None:
    b = _briefing()
    assert "duty_view" not in b
    lines = render_briefing_lines(b)
    assert all(label != "영역별 점검" for label, _ in lines)
    assert render_briefing_lines(copy.deepcopy(b)) == lines


def test_render_lines_with_key_adds_one_handoff_line() -> None:
    b = _briefing()
    base = render_briefing_lines(b)
    lines = render_briefing_lines(_with_duty(b))
    duty = [(label, text) for label, text in lines if label == "영역별 점검"]
    assert duty == [("영역별 점검", HANDOFF)]
    assert [x for x in lines if x[0] != "영역별 점검"] == base
    # 요약 바로 뒤
    labels = [label for label, _ in lines]
    if "요약" in labels:
        assert labels.index("영역별 점검") == labels.index("요약") + 1
    # 영역 표 내용(근거·사유)은 챗 줄에 싣지 않는다
    assert "획득·실행 구분 정보 없음" not in "\n".join(t for _, t in lines)


def test_render_lines_without_handoff_string_drops_label() -> None:
    b = {"summary": "s", "duty_view": {"areas": DUTY_VIEW["areas"]}}
    assert render_briefing_lines(b) == [("요약", "s")]


# ── 저장 → 조회 ─────────────────────────────────────────────────────────────


def test_store_and_views_preserve_duty_view(tmp_path: Path) -> None:
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    store.record(investigation_id="inv-w4", alarm_ids=["a1"], status="done", source="sre_agent",
                 briefing=_with_duty(_briefing()))
    rec = store.get_by_investigation("inv-w4")
    assert rec is not None and rec["briefing"]["duty_view"] == DUTY_VIEW
    assert {"label": "영역별 점검", "text": HANDOFF} in rec["briefing_lines"]
    user = investigation_view(copy.deepcopy(rec), mask_fn=lambda s: s.replace("apm_", "***"))
    oper = investigation_view(copy.deepcopy(rec), mask_fn=lambda s: s, operator_view=True)
    assert oper["briefing"]["duty_view"] == DUTY_VIEW
    dv = user["briefing"]["duty_view"]
    assert [r["code"] for r in dv["areas"]] == ["server_os", "was_mw", "db"]  # 코드는 enum — 가리지 않는다
    assert [r["status"] for r in dv["areas"]] == ["normal", "suspect", "unverified"]
    assert dv["handoff"] == HANDOFF
    assert "***" in dv["areas"][1]["signals"][0]  # 근거 문자열은 사용자 뷰 마스킹 대상
    assert "duty_view" in PLAYBOOK_VIEW_KEYS


def test_user_view_without_key_unchanged(tmp_path: Path) -> None:
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    store.record(investigation_id="inv-0", alarm_ids=["a1"], status="done", source="sre_agent",
                 briefing=_briefing())
    rec = store.get_by_investigation("inv-0")
    assert rec is not None
    user = investigation_view(copy.deepcopy(rec), mask_fn=lambda s: s)
    assert "duty_view" not in user["briefing"]


def test_clip_keeps_duty_view() -> None:
    b = _with_duty(_briefing())
    b["timeline"] = ["x" * 400] * 200  # 상한을 넘긴다
    clipped = clip_briefing(b, max_bytes=16 * 1024)
    assert clipped["duty_view"] == DUTY_VIEW
    assert list(clipped).index("duty_view") == list(clipped).index("summary") + 1


# ── 화면 ────────────────────────────────────────────────────────────────────


def _node(body: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    script = "\n".join([
        f"var BV = require({json.dumps(str(_BV_JS))});",
        _FAKE_DOM,
        "var out = null;",
        body,
        "process.stdout.write(JSON.stringify(out));",
    ])
    done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60,
                          check=False, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# 최소 가짜 DOM — createElement·appendChild·textContent·속성 선택자 querySelector만 흉내 낸다
# (innerHTML 없음).
_FAKE_DOM = r"""
function El(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.className = ""; this._text = "";
}
Object.defineProperty(El.prototype, "textContent", {
    get: function () {
        return this._text + this.children.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) { this._text = String(v); this.children = []; },
});
Object.defineProperty(El.prototype, "firstChild", {
    get: function () { return this.children[0] || null; },
});
El.prototype.appendChild = function (c) { this.children.push(c); c.parentNode = this; return c; };
El.prototype.removeChild = function (c) {
    this.children.splice(this.children.indexOf(c), 1); return c;
};
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) {
    return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
El.prototype.all = function () {
    var o = [];
    this.children.forEach(function (c) { o.push(c); o = o.concat(c.all()); });
    return o;
};
El.prototype.querySelector = function (sel) {
    var m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (!m) return null;
    return this.all().filter(function (e) {
        var v = e.getAttribute(m[1]); return v !== null && (m[2] === undefined || v === m[2]);
    })[0] || null;
};
var fakeDoc = {
    createElement: function (t) { var e = new El(t); e.ownerDocument = fakeDoc; return e; },
};
function container(steps) {
    var c = fakeDoc.createElement("div");
    var slot = fakeDoc.createElement("div");
    slot.setAttribute("data-bv-duty", "");
    c.appendChild(slot);
    (steps || []).forEach(function (s) {
        var d = fakeDoc.createElement("details");
        d.setAttribute("data-bv-step", s);
        c.appendChild(d);
    });
    return c;
}
function rows(c) {
    var trs = c.all().filter(function (e) {
        return e.tagName === "tr" && e.getAttribute("data-duty-code");
    });
    return trs.map(function (tr) {
        var btn = tr.all().filter(function (e) {
            return e.getAttribute("data-bv-action") === "goto-step";
        })[0];
        return {
            code: tr.getAttribute("data-duty-code"),
            cells: tr.children.map(function (td) { return td.textContent; }),
            step: btn ? btn.getAttribute("data-step") : null,
        };
    });
}
"""

_META_OPTIONS = [
    {"code": "server_os", "label": "서버·OS(메타)"},
    {"code": "was_mw", "label": "WAS/미들웨어"},
    {"code": "db", "label": "DB"},
]


def _js(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _ui_view(with_key: bool = True) -> dict[str, Any]:
    b = _with_duty(_briefing()) if with_key else _briefing()
    view = json.loads((_FIX / "confirmed_same_layer.json").read_text(encoding="utf-8"))["view"]
    view = copy.deepcopy(view)
    view["briefing"] = b
    return view


def test_render_body_without_key_identical_with_placeholder_only() -> None:
    v1, v0 = _ui_view(True), _ui_view(False)
    out = _node(f"""
var V1 = {_js(v1)};
var V0 = {_js(v0)};
var ex = [{{ mode: "detail", summaryView: true }}, {{ mode: "summary", summaryView: true }}, {{}}];
out = ex.map(function (x) {{ return [BV.renderBody(V1, x), BV.renderBody(V0, x)]; }});
""")
    slot = '<div class="bv-pb-block bv-duty" data-bv-duty=""></div>'
    for with_key, without in out:
        assert slot not in without
        assert with_key.count(slot) == 1
        assert with_key.replace(slot, "") == without


def test_fill_declared_order_and_payload_labels_without_meta() -> None:
    v = _ui_view()
    out = _node(f"""
BV.setDutyMeta(null);
var c = container(["S3", "S6"]);
BV.fillDutyView(c, {_js(v)});
out = {{ rows: rows(c), text: c.textContent }};
""")
    assert [r["code"] for r in out["rows"]] == ["server_os", "was_mw", "db"]
    was = out["rows"][1]
    assert was["cells"][0] == "WAS/미들웨어(외부 연계)"
    assert was["cells"][1] == "의심 · 확인 필요"
    assert was["cells"][2].startswith("DB 지연 → 풀 고갈")
    assert was["cells"][4] == "획득·실행 구분 정보 없음"
    assert was["step"] == "S3"
    assert out["rows"][2]["step"] is None  # 단계 없는 행은 [단계 보기] 없음
    assert "영역별 점검 결과" in out["text"] and HANDOFF in out["text"]
    assert "내 담당" not in out["text"]


def test_fill_mine_first_with_meta_labels() -> None:
    v = _ui_view()
    out = _node(f"""
BV.setDutyMeta({{ options: {_js(_META_OPTIONS)}, mine: ["db"] }});
var c = container(["S3"]);
BV.fillDutyView(c, {_js(v)});
out = {{ rows: rows(c), model: BV.dutyModel({_js(v)}) }};
""")
    assert [r["code"] for r in out["rows"]] == ["db", "server_os", "was_mw"]
    assert out["rows"][0]["cells"][0] == "DB 내 담당"
    assert out["rows"][1]["cells"][0] == "서버·OS(메타)"
    assert out["rows"][1]["step"] is None  # S6 단계 섹션이 화면에 없으면 버튼 없음
    assert [r["mine"] for r in out["model"]["rows"]] == [True, False, False]


def test_fill_no_key_no_table() -> None:
    v = _ui_view(False)
    out = _node(f"""
var c = container([]);
BV.fillDutyView(c, {_js(v)});
out = {{ n: c.all().length, model: BV.dutyModel({_js(v)}) }};
""")
    assert out == {"n": 1, "model": None}


def _meta_fn() -> str:
    app = _APP_JS.read_text(encoding="utf-8")
    start = app.index("function loadDutyBriefingMeta()")
    return app[start:app.index("function loadDutyAreaNotice(")]


def _duty_js_block() -> str:
    src = _BV_JS.read_text(encoding="utf-8")
    at = src.index("// ─── (plans/201 W4 · D-354) 영역별 점검 결과")
    return src[at:src.index("function rqNode(doc, tag, cls, text)", at)]


def test_duty_js_uses_text_content_only() -> None:
    block = _duty_js_block()
    assert "innerHTML" not in block and "insertAdjacentHTML" not in block
    assert "rqNode(" in block  # textContent 노드 생성기


def test_no_area_vocabulary_copy_in_frontend() -> None:
    import yaml

    body = yaml.safe_load((_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8"))
    block = _duty_js_block()
    meta_fn = _meta_fn()
    for area in body["areas"]:
        assert area["label"] not in block, area["label"]
        assert f'"{area["code"]}"' not in block, area["code"]
        assert area["label"] not in meta_fn
    for status_label in ("의심 · 확인 필요", "확인 안 됨"):
        assert status_label not in block


def test_user_briefing_keys_include_duty_view() -> None:
    src = _BV_JS.read_text(encoding="utf-8")
    m = re.search(r"var USER_BRIEFING_KEYS = \{([^}]*)\}", src)
    assert m is not None
    keys = set(re.findall(r"(\w+): true", m.group(1)))
    assert keys == set(PLAYBOOK_VIEW_KEYS)


def test_decorate_fills_duty_and_app_sets_meta() -> None:
    src = _BV_JS.read_text(encoding="utf-8")
    deco = src[src.index("function decorate(view)"):src.index("function repaint()")]
    assert "fillDutyView(container, view)" in deco
    assert deco.index("fillDutyView") < deco.index("if (!opts.requery")
    meta_fn = _meta_fn()
    assert '"duty_area_options"' in meta_fn and "setDutyMeta" in meta_fn
    assert "loadDutyBriefingMeta();" in _APP_JS.read_text(encoding="utf-8")


def test_cache_versions_bumped() -> None:
    index = (_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'src="/static/js/briefing-view.js?v=13"' in index
    assert 'src="/static/js/app.js?v=53"' in index
    for page in ("noise.html", "admin/noise.html"):
        assert 'briefing-view.js?v=13' in (_STATIC / page).read_text(encoding="utf-8")


def test_slot_without_playbook_and_not_in_followup_items() -> None:
    """축소로 playbook이 빠진 사용자 뷰에도 자리를 그리고, 추가 조사 항목 렌더에는 그리지 않는다."""
    view = {"status": "done", "briefing": {"duty_view": copy.deepcopy(DUTY_VIEW)}}
    out = _node(f"""
var V = {_js(view)};
var c = container(["S3"]);
BV.fillDutyView(c, V);
out = {{ html: BV.renderBody(V, {{}}), nested: BV.renderBody(V, {{ noDuty: true }}),
         rows: rows(c).length, raw: BV.isRawView(V) }};
""")
    assert 'data-bv-duty=""' in out["html"]
    assert "data-bv-duty" not in out["nested"]
    assert out["rows"] == 3 and out["raw"] is False
    src = _BV_JS.read_text(encoding="utf-8")
    fu = src[src.index("function followupHtml("):]
    assert "renderBody(item, { noDuty: true })" in fu[:fu.index("\n    }\n")]
