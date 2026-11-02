"""plans/201 W5 독립 검증(verifier) — 조치 지원 · 알람 카드 인지→분석→조치 재배치.

구현자 테스트(`test_plan201_w5_{backend,assist_render,card_flow}.py`)가 다루지 않은 것을 고정한다.

  1. 플래그 off 비트 동일 — 기준 SHA(`43d7bdf7`)의 app.js·briefing-view.js·diagnosis-view.js를 `git show`로 꺼내
     **같은 가짜 DOM**에서 카드 13변형(예외 경로 4건 포함)과 브리핑 attach 렌더를 그려 현재 파일과 구조를 대조한다.
  2. 재배치 on에서 뒤늦게 오는 LLM 추천 질의(`insertAlarmPromptSection`) — 실제 DOM의 `insertBefore` 규칙
     (기준 노드가 직계 자식이 아니면 NotFoundError)으로 재현한다.
  3. 재배치 on + W3 담당 배지 공존 · 짝 불일치 폴백 뒤 상호작용 안전.
  4. `requeryGroups` 무작위 성질(개수·집합 불변 · 묶음 안 안정 순서).
  5. 인계 요약 — 본체 마스킹 경로(`investigation_view` + `_investigation_mask_fn`)를 거친 값만 · 운영자 원문 뷰 ·
     조사 id 형식 검사.
  6. XSS — 점검 순서·영역 라벨·질의 묶음 라벨·인계 글에 `<img onerror>` 꼴.
  7. `/auth/me` — env 경로로 `DUTY_AREA_ENABLED` off + `DUTY_ACTION_ASSIST_ENABLED` on = off.
  8. 낡은 캐시 버전 단언 0.

node는 stdin으로만 실행한다(`node -e` 금지 — WinError 206). 실 LLM·외부 호출 없음.
"""

from __future__ import annotations

import copy
import json
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.api.duty_area_support import duty_action_assist_meta, duty_alarm_focus_meta
from tests.test_plan201_w5_card_flow import _ALARM, _CAPS_BASE, _FAKE_DOM, _LOADER

_ROOT = Path(__file__).resolve().parents[1]
_JS = _ROOT / "src" / "static" / "js"
_FIX = _ROOT / "tests" / "fixtures" / "plan199"
_BASE_SHA = "43d7bdf7"
_STRICT_INSERT = r"""
// 실제 DOM 규칙 — 기준 노드가 직계 자식이 아니면 NotFoundError(가짜 DOM은 끝 앞에 끼워 넣어 결함을 가린다)
var __origInsert = El.prototype.insertBefore;
El.prototype.insertBefore = function (c, ref) {
    if (ref && this.childNodes.indexOf(ref) < 0) throw new Error("NotFoundError: insertBefore ref is not a child");
    return __origInsert.call(this, c, ref);
};
"""
#: 시험 출구에 W3 담당 메타 주입을 더한다(제품 코드 무변경 — 닫는 IIFE 앞 한 줄)
_LOADER_FOCUS = _LOADER.replace(
    '"assist: function (v) { dutyActionAssist = v; }, " +',
    '"assist: function (v) { dutyActionAssist = v; }, " +\n'
    '    "focus: function (f, a) { dutyAlarmFocus = f; dutyUserAreas = a; }, " +',
)
assert _LOADER_FOCUS != _LOADER


def _js(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _run(script: str) -> Any:
    if shutil.which("node") is None:
        pytest.skip("node 미설치 — JS 실행 테스트 생략")
    done = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=180,
                          check=False, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _app_node(body: str, *, app: Path | None = None, bv: Path | None = None, dv: Path | None = None,
              strict: bool = False, focus: bool = False) -> Any:
    script = "\n".join([
        f"var APP_PATH = {_js(str(app or _JS / 'app.js'))};",
        f"var BV_PATH = {_js(str(bv or _JS / 'briefing-view.js'))};",
        f"var DV_PATH = {_js(str(dv or _JS / 'diagnosis-view.js'))};",
        _FAKE_DOM,
        _STRICT_INSERT if strict else "",
        _LOADER_FOCUS if focus else _LOADER,
        "var out = null;",
        "(async function () {",
        body,
        "process.stdout.write(JSON.stringify(out), function () { process.exit(0); });",
        "})().catch(function (e) { process.stderr.write(String(e && e.stack || e)); process.exit(1); });",
    ])
    return _run(script)


def _bv_node(body: str, *, bv: Path | None = None) -> Any:
    script = "\n".join([
        _FAKE_DOM,
        "global.window = global; global.document = fakeDoc;",
        "global.location = { origin: 'http://portal.local' };",
        f"var BV = require({_js(str(bv or _JS / 'briefing-view.js'))});",
        "function flush() { return new Promise(function (r) { setTimeout(r, 0); }); }",
        "var out = null;",
        "(async function () {",
        body,
        "process.stdout.write(JSON.stringify(out), function () { process.exit(0); });",
        "})().catch(function (e) { process.stderr.write(String(e && e.stack || e)); process.exit(1); });",
    ])
    return _run(script)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory) -> dict[str, Path]:
    """기준 SHA의 정적 JS 3개(작업 트리를 건드리지 않는다 — `git show`)."""
    out = tmp_path_factory.mktemp("w5_base")
    paths: dict[str, Path] = {}
    for name in ("app.js", "briefing-view.js", "diagnosis-view.js"):
        done = subprocess.run(["git", "show", f"{_BASE_SHA}:src/static/js/{name}"], cwd=_ROOT,
                              capture_output=True, check=False)
        if done.returncode != 0:
            pytest.skip(f"기준 커밋 {_BASE_SHA} 없음 — 기준선 대조 생략")
        p = out / name
        p.write_bytes(done.stdout)
        paths[name] = p
    return paths


# ── 1. 플래그 off 비트 동일 — 카드 ─────────────────────────────────────────────────

_NO_PROMPT = {**_ALARM, "resource_type": "management.x", "alarm_name": "관리 알람", "process_snapshot": None}

#: 13변형 — (이름, capabilities, 알람, 후속 조작). 조작: suggest_ok · suggest_fail · ack_fail · ack_ok · expand_500
_VARIANTS: list[tuple[str, Any, dict[str, Any], str]] = [
    ("full", _CAPS_BASE, _ALARM, ""),
    ("routine", _CAPS_BASE, {**_ALARM, "is_routine": True, "pattern_type": "반복"}, ""),
    ("no_incident", _CAPS_BASE, {k: v for k, v in _ALARM.items() if k != "incident_id"}, ""),
    ("no_investigation", {**_CAPS_BASE, "investigation_enabled": False},
     {k: v for k, v in _ALARM.items() if k != "investigation_id"}, ""),
    ("manual_start", {**_CAPS_BASE, "investigation_manual_enabled": True, "investigation_auto_enabled": False},
     {k: v for k, v in _ALARM.items() if k != "investigation_id"}, ""),
    ("feedback_off", {**_CAPS_BASE, "feedback_enabled": False}, _ALARM, ""),
    ("caps_null", None, _ALARM, ""),                                         # 예외: 기능 조회 실패
    ("minimal", _CAPS_BASE, {"alarm_id": "al-9", "severity_label": "Warning", "summary": "s"}, ""),
    ("llm_prompt_ok", {**_CAPS_BASE, "prompt_suggest_enabled": True}, _NO_PROMPT, "suggest_ok"),
    ("llm_prompt_fail", {**_CAPS_BASE, "prompt_suggest_enabled": True}, _NO_PROMPT, "suggest_fail"),  # 예외
    ("ack_ok", _CAPS_BASE, _ALARM, "ack_ok"),
    ("ack_fail", _CAPS_BASE, _ALARM, "ack_fail"),                            # 예외: 네트워크 실패
    ("expand_500", _CAPS_BASE, _ALARM, "expand_500"),                        # 예외: 조사 조회 HTTP 500
]

_CARD_SNAPSHOT_BODY = r"""
var VARIANTS = __VARIANTS__;
// 확인 완료 시각(app.js `new Date()`)도 고정 — 두 스냅숏 사이 초가 넘어가면 기준 대조가 흔들린다
var _RealDate = Date;
Date = function () { return arguments.length ? new (Function.prototype.bind.apply(_RealDate, [null].concat([].slice.call(arguments))))() : new _RealDate(1793000000000); };
Date.prototype = _RealDate.prototype; Date.UTC = _RealDate.UTC; Date.parse = _RealDate.parse;
Date.now = function () { return 1793000000000; };
var res = [];
for (var vi = 0; vi < VARIANTS.length; vi++) {
    var v = VARIANTS[vi];
    var list = fakeDoc.getElementById("alarmList");
    list._clear();
    for (var k in store) delete store[k];
    fetches.length = 0;
    var act = v[3];
    fetchImpl = function (u) {
        if (u.indexOf("suggest-prompt") >= 0) {
            if (act === "suggest_fail") return Promise.reject(new Error("network down"));
            return Promise.resolve({ ok: true, json: function () {
                return Promise.resolve({ suggestion: { label: "L", text: "T" } }); } });
        }
        if (u.indexOf("/ack") >= 0) {
            if (act === "ack_fail") return Promise.reject(new Error("network down"));
            return Promise.resolve({ ok: true, json: function () { return Promise.resolve({ acked: true }); } });
        }
        if (act === "expand_500") {
            return Promise.resolve({ ok: false, status: 500, json: function () { return Promise.resolve({ detail: "boom" }); } });
        }
        return new Promise(function () {});
    };
    APP.caps(v[1]);
    var el = card(v[2]);
    if (act === "ack_ok" || act === "ack_fail") el.querySelector(".btn-alarm-ack").click();
    if (act === "expand_500") el.querySelector(".alarm-briefing-toggle").click();
    for (var f = 0; f < 6; f++) await flush();
    res.push({ name: v[0], shape: el.shape(), dataset: JSON.parse(JSON.stringify(el.dataset)),
               fetches: fetches.slice(), store: JSON.parse(JSON.stringify(store)) });
}
out = res;
"""


def _card_snapshots(paths: dict[str, Path] | None) -> list[dict[str, Any]]:
    body = _CARD_SNAPSHOT_BODY.replace("__VARIANTS__", _js([list(v) for v in _VARIANTS]))
    if paths is None:
        return _app_node(body)
    return _app_node(body, app=paths["app.js"], bv=paths["briefing-view.js"], dv=paths["diagnosis-view.js"])


def test_off_card_dom_bit_identical_to_baseline(baseline) -> None:
    """두 플래그 off(capabilities에 `card_flow_enabled` 없음 · 조치 지원 메타 없음) — 13변형 모두 기준과 같다."""
    old = _card_snapshots(baseline)
    new = _card_snapshots(None)
    assert [o["name"] for o in old] == [v[0] for v in _VARIANTS]
    for o, n in zip(old, new, strict=True):
        assert n["shape"] == o["shape"], o["name"]
        assert n["dataset"] == o["dataset"], o["name"]
        assert n["fetches"] == o["fetches"], o["name"]
        assert n["store"] == o["store"], o["name"]
    # 변형이 실제로 갈래를 탔는지(예외 경로 포함) — 대조가 빈 비교가 되지 않게
    by = {n["name"]: json.dumps(n["shape"], ensure_ascii=False) for n in new}
    assert "alarm-prompt-section" in by["llm_prompt_ok"] and "alarm-prompt-section" not in by["llm_prompt_fail"]
    assert "조사 안 됨" in by["manual_start"] and "alarm-feedback-section" not in by["feedback_off"]
    assert "alarm-feedback-section" in by["caps_null"]


# ── 1'. 플래그 off 비트 동일 — 브리핑 attach(관제 드로어·카드 공용 렌더) ───────────────────


def _views_for_briefing() -> list[dict[str, Any]]:
    views = []
    for name in ("confirmed_same_layer", "different_layer", "non_playbook", "episode_scope", "running_collect",
                 "user_view_secret"):
        views.append(json.loads((_FIX / f"{name}.json").read_text(encoding="utf-8"))["view"])
    dv = copy.deepcopy(views[0])
    dv["briefing"]["duty_view"] = {"areas": [
        {"code": "was_mw", "label": "WAS/미들웨어", "status": "suspect", "status_label": "의심 · 확인 필요",
         "signals": ["풀 고갈"], "steps": ["S3"]}]}
    dv["requery"] = [
        {"step": "S2", "title": "t", "prompt": "inst1 에러율 보여줘", "path": "apm_query", "source_ref": "r",
         "realtime": False, "axis": "apm"},
        {"step": "S4", "title": "t", "prompt": "서버 CPU 보여줘", "path": "sql", "source_ref": "r",
         "realtime": False, "axis": "polestar", "polestar_source": "x"},
    ]
    views.append(dv)
    return views


_BRIEFING_BODY = r"""
var VS = __VIEWS__;
var res = [];
for (var i = 0; i < VS.length; i++) {
    var modes = [{ mode: "detail" }, { mode: "summary", openConversation: true }];
    for (var m = 0; m < modes.length; m++) {
        var box = fakeDoc.createElement("div");
        box.ownerDocument = fakeDoc;
        (function (view) {
            var p = BV.attach(box, { initial: view, load: function () { return Promise.resolve(view); },
                render: modes[m], requery: { stage: function () {}, busy: function () { return false; } },
                summaryView: function () { return m === 1; } });
            p.open();
        })(VS[i]);
        for (var f = 0; f < 4; f++) await flush();
        res.push(box.shape());
    }
}
out = res;
"""


def test_off_briefing_dom_bit_identical_to_baseline(baseline) -> None:
    """조치 지원 메타 미주입(`setActionAssist` 안 부름) — attach 렌더 + decorate가 기준 briefing-view.js와 같다."""
    body = _BRIEFING_BODY.replace("__VIEWS__", _js(_views_for_briefing()))
    old = _bv_node(body, bv=baseline["briefing-view.js"])
    new = _bv_node(body)
    assert len(old) == len(new) == 14
    for i, (o, n) in enumerate(zip(old, new, strict=True)):
        assert n == o, i
    joined = json.dumps(new, ensure_ascii=False)
    assert "bv-requery" in joined and "bv-checklist" not in joined and "bv-requery-area" not in joined


# ── 2. 재배치 on — 뒤늦은 LLM 추천 질의 ───────────────────────────────────────────


_LLM_BODY = r"""
var warned = [];
console.warn = function (a, b) { warned.push(String(a) + " " + String(b)); };
APP.caps(__CAPS__);
fetchImpl = function (u) {
    if (u.indexOf("suggest-prompt") >= 0) return Promise.resolve({ ok: true, json: function () {
        return Promise.resolve({ suggestion: { label: "L", text: "T" } }); } });
    return new Promise(function () {});
};
var el = card(__ALARM__);
for (var i = 0; i < 6; i++) await flush();
var s = el.querySelector(".alarm-prompt-section");
out = { n: el.querySelectorAll(".alarm-prompt-section").length, warned: warned,
        inAct: !!(s && s.closest(".alarm-flow-group--act")) };
"""


def _llm(caps: dict[str, Any]) -> Any:
    body = _LLM_BODY.replace("__CAPS__", _js(caps)).replace("__ALARM__", _js(_NO_PROMPT))
    return _app_node(body, strict=True)


def test_llm_prompt_inserted_when_flow_off_strict_dom() -> None:
    """대조군 — 재배치 off면 실제 DOM 규칙에서도 LLM 추천 칸이 들어간다."""
    out = _llm({**_CAPS_BASE, "prompt_suggest_enabled": True})
    assert out["n"] == 1 and out["warned"] == []


def test_llm_prompt_survives_card_flow_on() -> None:
    out = _llm({**_CAPS_BASE, "prompt_suggest_enabled": True, "card_flow_enabled": True})
    assert out["n"] == 1 and out["warned"] == []
    assert out["inAct"] is True


# ── 3. 재배치 on + W3 담당 배지 공존 · 짝 불일치 폴백 ─────────────────────────────────


def test_flow_on_coexists_with_duty_badge() -> None:
    """W3 담당 배지 칸(.alarm-duty-section)은 재배치 뒤에 머리 아래 끼워진다 — 칸 누락 0 · 진행 표시 그대로."""
    focus = duty_alarm_focus_meta()
    out = _app_node(f"""
function snap(caps) {{
    APP.caps(caps);
    APP.focus({_js(focus)}, ["server_os", "was_mw", "db"]);
    var el = card({_js({**_ALARM, "resource_type": "CPU"})});
    var b = el.querySelector(".message-bubble");
    return {{
        sections: el.querySelectorAll(".alarm-section").filter(function (s) {{
            return !s.classList.contains("alarm-flow-caption"); }}).map(function (s) {{ return s.className; }}).sort(),
        duty: el.querySelectorAll(".alarm-duty-section").length,
        dutyParent: el.querySelector(".alarm-duty-section") ? el.querySelector(".alarm-duty-section").parentNode.className : null,
        progress: el.querySelectorAll(".alarm-flow-step").map(function (s) {{ return s.textContent; }}),
        mine: el.dataset.dutyMine,
    }};
}}
out = {{ off: snap({_js(_CAPS_BASE)}), on: snap({_js({**_CAPS_BASE, "card_flow_enabled": True})}) }};
""", focus=True)
    assert out["off"]["duty"] == 1 and out["on"]["duty"] == 1
    assert out["on"]["sections"] == out["off"]["sections"]
    assert out["on"]["dutyParent"] == "message-bubble"
    assert out["on"]["progress"] == ["● 인지", "○ 분석", "○ 조치"]
    assert out["on"]["mine"] == out["off"]["mine"] == "1"


def test_flow_mismatch_fallback_then_interactions_safe() -> None:
    """짝이 안 맞아 옮기지 않은 카드 — 이후 확인(ack)·추천 칩 클릭이 예외 없이 동작하고 칸 순서가 그대로다."""
    out = _app_node(f"""
APP.caps({_js({**_CAPS_BASE, "card_flow_enabled": True})});
fetchImpl = function (u) {{
    return Promise.resolve({{ ok: true, json: function () {{ return Promise.resolve({{ acked: true }}); }} }});
}};
var el = card({_js(_ALARM)});
var b = el.querySelector(".message-bubble");
// 재배치를 되돌린 것처럼 — 묶음을 풀고 예상 밖 칸을 하나 더한 뒤 다시 재배치를 시도한다
var secs = el.querySelectorAll(".alarm-section").filter(function (s) {{ return !s.classList.contains("alarm-flow-caption"); }});
b.querySelectorAll(".alarm-flow-group, .alarm-flow-progress").forEach(function (g) {{ g.remove(); }});
secs.forEach(function (s) {{ b.appendChild(s); }});
var extra = fakeDoc.createElement("div"); extra.className = "alarm-section"; b.appendChild(extra);
var before = b.children.map(function (c) {{ return c.className; }});
APP.arrange(el, {_js(_ALARM)}, {{ process: true, pattern: true, episode: true, start: false, briefing: true,
    ack: true, prompt: true, feedback: true }});
var mid = b.children.map(function (c) {{ return c.className; }});
el.querySelector(".btn-alarm-ack").click();
el.querySelector(".alarm-prompt-chip").click();
for (var i = 0; i < 4; i++) await flush();
out = {{ before: before, mid: mid, after: b.children.map(function (c) {{ return c.className; }}),
        ack: el.querySelector(".btn-alarm-ack").textContent }};
""")
    assert out["mid"] == out["before"] == out["after"]
    assert out["ack"].startswith("확인됨")


# ── 4. requeryGroups 성질 ─────────────────────────────────────────────────────────


def test_requery_groups_random_property() -> None:
    rng = random.Random(201)
    axes = {"apm": {"area": "was_mw", "label": "WAS/미들웨어"}, "polestar": {"area": "server_os", "label": "서버/OS"},
            "x": {"area": "db", "label": "DB"}}
    cases = []
    for _ in range(60):
        items = []
        for i in range(rng.randint(0, 12)):
            it: dict[str, Any] = {"step": f"S{i}", "prompt": f"p{i}"}
            ax = rng.choice(["apm", "polestar", "x", "zz", None])
            if ax is not None:
                it["axis"] = ax
            items.append(it)
        mine = rng.sample(["was_mw", "server_os", "db"], rng.randint(1, 3))
        cases.append({"items": items, "mine": mine})
    out = _bv_node(f"""
var C = {_js(cases)};
out = C.map(function (c) {{ return BV.requeryGroups(c.items, {{ axes: {_js(axes)}, mine: c.mine }}); }});
""")
    for case, groups in zip(cases, out, strict=True):
        flat = [it["prompt"] for g in groups for it in g["items"]]
        assert sorted(flat) == sorted(it["prompt"] for it in case["items"])     # 개수·집합 불변
        assert len(flat) == len(set(flat))
        for g in groups:                                                         # 묶음 안 = 원래 순서
            idx = [int(it["prompt"][1:]) for it in g["items"]]
            assert idx == sorted(idx)
        areas = [g["area"] for g in groups]
        assert len(areas) == len(set(areas))
        mine_seen = [a for a in areas if a in case["mine"]]
        assert mine_seen == [a for a in case["mine"] if a in mine_seen]          # 담당 순서
        assert areas[: len(mine_seen)] == mine_seen                              # 담당 묶음이 앞
        if "" in areas:
            assert areas[-1] == ""                                               # 「그 밖」은 끝
    # 담당 미지정 · 메타 없음 = 종전(null)
    none = _bv_node('out = [BV.requeryGroups([{prompt: "a", axis: "apm"}], {axes: {}, mine: []}), '
                    'BV.requeryGroups([{prompt: "a"}])];')
    assert none == [None, None]


# ── 5. 인계 요약 — 본체 마스킹 경로 ───────────────────────────────────────────────

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
_SK = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1"


def _rec_views() -> tuple[dict[str, Any], dict[str, Any]]:
    from noise_gate.infrastructure.investigation_store import investigation_view
    from src.api.routes.alarm import _investigation_mask_fn
    from src.config import AppConfig, SecurityConfig

    raw = json.loads((_FIX / "confirmed_same_layer.json").read_text(encoding="utf-8"))["view"]
    briefing = copy.deepcopy(raw["briefing"])
    briefing["duty_view"] = {"areas": [
        {"code": "was_mw", "label": "WAS/미들웨어", "status": "abnormal", "status_label": "이상",
         "signals": [f"풀 고갈 token={_SK}", "두 번째 근거"], "steps": ["S3", "S2"],
         "reason": f"Authorization: Bearer {_JWT}"},
        {"code": "db", "label": "DB", "status": "unverified", "status_label": "확인 안 됨",
         "signals": [_JWT], "steps": ["S3", "bad step<x>"], "reason": "사유 password=hunter2"},
    ]}
    rec = {"status": raw["status"], "investigation_id": raw["investigation_id"], "kind": "alarm",
           "briefing_lines": raw.get("briefing_lines") or [], "briefing": briefing, "ts": raw.get("updated_at")}
    cfg = AppConfig(security=SecurityConfig(_env_file=None, mask_pattern="***MASKED***"))
    user = investigation_view(copy.deepcopy(rec), mask_fn=_investigation_mask_fn(cfg))
    op = investigation_view(copy.deepcopy(rec), operator_view=True)
    return user, op


_HANDOFF_ALARM = {"server": "서버 A", "alarm_name": "응답 지연", "resource": "inst1", "severity": "Critical",
                  "time": "2026-10-26 19:10:00", "type": "apm.Instance"}


def test_handoff_masked_user_view_and_raw_view() -> None:
    user, op = _rec_views()
    bad_id = copy.deepcopy(user)
    bad_id["investigation_id"] = "inv 1<script>"
    out = _bv_node(f"""
var A = {_js(_HANDOFF_ALARM)};
out = {{ user: BV.alarmHandoffText(A, {_js(user)}), op: BV.alarmHandoffText(A, {_js(op)}),
        bad: BV.alarmHandoffText(A, {_js(bad_id)}), raw: [BV.isRawView ? BV.isRawView({_js(op)}) : null] }};
""")
    for secret in (_JWT, _SK, "hunter2"):
        assert secret not in out["user"], secret
        assert secret not in out["op"], secret                     # 원문 뷰는 근거·사유 칸을 싣지 않는다
    assert "***MASKED***" in out["user"]
    assert "WAS/미들웨어 이상" in out["op"] and "DB 확인 안 됨" in out["op"]
    assert "근거 단계: S3 · S2" in out["user"] and "bad step" not in out["user"]
    assert "http://portal.local/?investigation=" in out["user"]
    assert "inv 1" not in out["bad"] and "<script>" not in out["bad"] and "?investigation=" not in out["bad"]
    assert all(len(line) < 600 for line in out["user"].split("\n"))
    for bad in ("SELECT", "owner_sub", "subject", "\r"):
        assert bad not in out["user"]


# ── 6. XSS ────────────────────────────────────────────────────────────────────────

_XSS = '<img src=x onerror="alert(1)">'


def test_new_dom_is_text_only_for_payloads() -> None:
    view = json.loads((_FIX / "confirmed_same_layer.json").read_text(encoding="utf-8"))["view"]
    pid = view["briefing"]["playbook"]["id"]
    meta = {"requery_axes": {"apm": {"area": "was_mw", "label": "WAS" + _XSS}},
            "checklists": {pid: [{"area": "was_mw", "area_label": "라벨" + _XSS, "steps": ["단계" + _XSS]},
                                 {"area": "db", "area_label": "DB" + _XSS, "steps": [_XSS]}]},
            "mine": ["was_mw"]}
    items = [{"step": "S1", "prompt": "질의" + _XSS, "axis": "apm"}, {"step": "S2", "prompt": "q2", "axis": "zz"}]
    v2 = copy.deepcopy(view)
    v2["requery"] = items
    out = _bv_node(f"""
BV.setActionAssist({_js(meta)});
var box = fakeDoc.createElement("div"); box.ownerDocument = fakeDoc;
var V = {_js(v2)};
BV.attach(box, {{ initial: V, load: function () {{ return Promise.resolve(V); }}, render: {{ mode: "detail" }},
    requery: {{ stage: function () {{}}, busy: function () {{ return false; }} }} }}).open();
for (var i = 0; i < 4; i++) await flush();
var ck = box.querySelector(".bv-checklist");
out = {{ imgs: box.querySelectorAll("img").length,
        ck: ck ? ck.textContent : null,
        areas: box.querySelectorAll(".bv-requery-area").map(function (n) {{ return n.textContent; }}),
        handoff: BV.alarmHandoffText({{ server: "s" + {_js(_XSS)}, alarm_name: {_js(_XSS)} }}, null) }};
""")
    assert out["imgs"] == 0
    assert out["ck"] is not None and _XSS in out["ck"] and "단계" + _XSS in out["ck"]
    assert out["areas"] == ["WAS" + _XSS, "그 밖"]
    assert _XSS in out["handoff"]                                   # 평문(클립보드·textarea.value) — 해석 없음


def test_handoff_box_on_card_text_only_with_payload_alarm() -> None:
    """카드 [인계 요약 복사] 칸 — 알람 값에 payload가 있어도 요소가 생기지 않고 글 상자 값으로만 들어간다."""
    evil = {**_ALARM, "server_name": _XSS, "alarm_name": _XSS, "resource_type": _XSS}
    out = _app_node(f"""
APP.caps({_js({**_CAPS_BASE, "card_flow_enabled": True})});
APP.assist({_js(duty_action_assist_meta(["was_mw"]))});
var el = card({_js(evil)});
navigator.clipboard = undefined;
el.querySelector(".btn-alarm-handoff").click();
await flush(); await flush();
out = {{ imgs: el.querySelectorAll("img").length, value: el.querySelector(".alarm-handoff-text").value,
        fetches: fetches.slice() }};
""")
    assert out["imgs"] == 0 and _XSS in out["value"] and out["fetches"] == []


# ── 7. /auth/me — env 경로의 실효 AND ─────────────────────────────────────────────


def test_me_assist_env_on_but_duty_off_is_off(monkeypatch) -> None:
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import jwt
    from fastapi.testclient import TestClient

    from src.api.server import create_app
    from src.config import AdminConfig, AlarmCardFlowConfig, AppConfig, AuthConfig, DutyAreaConfig
    from src.domain.user import User, UserRole, UserStatus

    for name in ("DUTY_AREA_ENABLED", "DUTY_ALARM_FOCUS_ENABLED", "DUTY_WELCOME_HINTS_ENABLED",
                 "WELCOME_GUIDE_ENABLED", "ALARM_CARD_FLOW_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DUTY_ACTION_ASSIST_ENABLED", "true")
    secret = "s" * 40
    cfg = AppConfig(auth=AuthConfig(enabled=True, jwt_secret=secret), admin=AdminConfig(jwt_secret="a" * 40),
                    duty_area=DutyAreaConfig(_env_file=None), alarm_card_flow=AlarmCardFlowConfig(_env_file=None))
    assert cfg.duty_area.action_assist_enabled is True and cfg.duty_area.enabled is False
    app = create_app(cfg)
    assert app.state.duty_action_assist_enabled is False
    user = User(user_id="u1", username="u1", hashed_password="x", role=UserRole.USER, status=UserStatus.ACTIVE,
                duty_areas=["db"])
    app.state.user_repo = SimpleNamespace(get_by_user_id=AsyncMock(return_value=user))
    tok = jwt.encode({"sub": "u1", "type": "user", "exp": datetime.now(UTC) + timedelta(hours=1)}, secret,
                     algorithm="HS256")
    body = TestClient(app).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()
    assert "duty_action_assist" not in body and "duty_areas" not in body
    caps = TestClient(app).get("/api/v1/alarm/capabilities", headers={"Authorization": f"Bearer {tok}"}).json()
    assert "card_flow_enabled" not in caps


# ── 8. 캐시 버전 · 실 플레이북 id ──────────────────────────────────────────────────


def test_no_stale_cache_version_assertions() -> None:
    # 리터럴로 적으면 W4 저장소 전역 캐시 버전 검사가 이 파일을 옛 버전 단언으로 오탐한다
    stale = ("app.js?v=" + "50", "briefing-view.js?v=" + "12")
    hits = []
    for base in (_ROOT / "tests", _ROOT / "src" / "static", _ROOT / "scripts"):
        for p in base.rglob("*"):
            if p.suffix not in {".py", ".html", ".js", ".md"} or not p.is_file() or p.name == Path(__file__).name:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            hits += [f"{p.relative_to(_ROOT)}:{s}" for s in stale if s in text]
    assert hits == []


def test_checklist_keys_resolve_to_runtime_playbook_ids() -> None:
    """정본 키가 sre_agent 플레이북 파일의 실제 `id:` 값이다(파일 이름이 아니라 본문 id로 대조)."""
    import yaml

    ids = set()
    for p in (_ROOT / "sre_agent" / "config" / "playbooks").glob("*.yaml"):
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict) and doc.get("id"):
            ids.add(str(doc["id"]))
    raw = yaml.safe_load((_ROOT / "config" / "duty_areas.yaml").read_text(encoding="utf-8"))
    keys = set(raw.get("checklists") or {})
    assert keys and keys <= ids, keys - ids
