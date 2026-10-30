"""plans/201 W4 독립 검증(verifier) — `duty_view` 저장·조회·화면·계약·경계 (D-354).

- 저장(`InvestigationStore.record`) → 사용자 뷰(**실제 라우트 마스킹 함수** `_investigation_mask_fn`) → 조회:
  코드·상태·단계 ID는 그대로, 근거 문구는 과마스킹 없이, 섞인 비밀값은 가린다.
- 계약 테스트가 한쪽 YAML 변경에 실제로 깨지는지(사본·정본 변형 → 단언 실패) 사고 실험을 실행으로 고정.
- `briefing-view.js` 새 표: payload의 HTML·셀렉터 주입이 노드·속성으로 새지 않는다(가짜 DOM).
- 인가 비참조 · D-139 양방향 import 0 · 판독/가설/정본 YAML 무변경 · 캐시 버전.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from noise_gate.domain.investigation_briefing import render_briefing_lines
from noise_gate.infrastructure.investigation_store import (
    InvestigationStore,
    clip_briefing,
    investigation_view,
)
from tests import test_plan201_w4_duty_contract as contract
from tests.test_plan201_w4_duty_render import _briefing, _js, _node

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"
BASE_SHA = "a289818f"

#: 실제 러너 산출(sre_agent 픽스처 case2_ldap_external · latency_sql 실행 결과에서 옮겨 적음 — 문구 원형).
REAL_DUTY_VIEW: dict[str, Any] = {
    "areas": [
        {"code": "server_os", "label": "서버/OS", "status": "abnormal", "status_label": "이상",
         "signals": [
             "WAS 밖 프로세스 CPU 점유 — 지지 · 상위 프로세스 종류 미식별(이름 기준 DB 엔진 아님 · WAS·웹서버 "
             "여부는 이름만으로 판정 불가) (S4·S2·S6 ← polestar_metric_trend, apm_runtime_health, "
             "apm_resource_pool, polestar_process_snapshot)",
             "CPU 귀속 other_process · 상위 프로세스 종류 미식별(이름 기준 DB 엔진 아님 · WAS·웹서버 여부는 "
             "이름만으로 판정 불가) (S4·S2·S6 ← polestar_metric_trend, apm_runtime_health, apm_resource_pool, "
             "polestar_process_snapshot)",
         ], "steps": ["S4", "S2", "S6"]},
        {"code": "was_mw", "label": "WAS/미들웨어", "status": "suspect", "status_label": "의심 · 확인 필요",
         "signals": ["업무 DB 지연(공통 의존) — 판단 보류 (S3·S0 ← apm_slow_transactions, "
                     "apm_transaction_profile, apm_instance_map, apm_app_health)"],
         "steps": ["S3", "S0"], "reason": "획득·실행 구분 정보 없음", "tag": "외부 연계"},
        {"code": "db", "label": "DB", "status": "unverified", "status_label": "확인 안 됨",
         "signals": [], "steps": [], "reason": "S3 느린 트랜잭션 Top 시간 분해: 수집 결손"},
    ],
    "handoff": "이상 신호가 있는 영역은 서버/OS·WAS/미들웨어(외부 연계)입니다 — 2개 영역 담당의 확인이 필요합니다.",
}


def _with(briefing: dict[str, Any], dv: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in briefing.items():
        out[k] = v
        if k == "summary":
            out["duty_view"] = copy.deepcopy(dv)
    return out


def _route_mask_fn() -> Callable[[str], str]:
    from src.api.routes.alarm import _investigation_mask_fn

    return _investigation_mask_fn(SimpleNamespace(security=SimpleNamespace(mask_pattern="***MASKED***")))


# ── 7. 저장·조회 · 실제 마스킹 ───────────────────────────────────────────────


def test_real_mask_fn_does_not_overmask_duty_view(tmp_path: Path) -> None:
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    store.record(investigation_id="inv-v", alarm_ids=["a1"], status="done", source="sre_agent",
                 briefing=_with(_briefing(), REAL_DUTY_VIEW))
    rec = store.get_by_investigation("inv-v")
    assert rec is not None
    user = investigation_view(copy.deepcopy(rec), mask_fn=_route_mask_fn())
    assert user["briefing"]["duty_view"] == REAL_DUTY_VIEW  # 고정 어휘·도구 이름·단계 ID — 가릴 것 없음
    oper = investigation_view(copy.deepcopy(rec), mask_fn=None, operator_view=True)
    assert oper["briefing"]["duty_view"] == REAL_DUTY_VIEW
    # 동기 챗·카드 줄: 「영역별 점검」은 인계 문구 1줄(요약 바로 뒤)
    labels = [i["label"] for i in rec["briefing_lines"]]
    assert labels.count("영역별 점검") == 1
    assert labels.index("영역별 점검") == labels.index("요약") + 1
    assert {"label": "영역별 점검", "text": REAL_DUTY_VIEW["handoff"]} in rec["briefing_lines"]


def test_real_mask_fn_masks_secret_mixed_into_duty_strings(tmp_path: Path) -> None:
    dv = copy.deepcopy(REAL_DUTY_VIEW)
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlc2lnbmF0dXJl"
    dv["areas"][2]["reason"] = f"Authorization: Bearer {jwt}"
    dv["handoff"] = f"x password=hunter2secret {jwt}"
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    store.record(investigation_id="inv-s", alarm_ids=["a1"], status="done", source="sre_agent",
                 briefing=_with(_briefing(), dv))
    rec = store.get_by_investigation("inv-s")
    assert rec is not None
    user = investigation_view(copy.deepcopy(rec), mask_fn=_route_mask_fn())
    text = json.dumps(user["briefing"]["duty_view"], ensure_ascii=False)
    assert jwt not in text and "hunter2secret" not in text
    assert all(r["code"] in ("server_os", "was_mw", "db") for r in user["briefing"]["duty_view"]["areas"])
    lines = json.dumps(user["briefing_lines"], ensure_ascii=False)
    assert jwt not in lines and "hunter2secret" not in lines


def test_clip_keeps_duty_view_first() -> None:
    b = _with(_briefing(), REAL_DUTY_VIEW)
    b["timeline"] = ["y" * 500] * 400
    clipped = clip_briefing(b, max_bytes=32 * 1024)
    assert clipped["duty_view"] == REAL_DUTY_VIEW
    assert list(clipped).index("duty_view") == list(clipped).index("summary") + 1


def test_user_view_keeps_duty_view_when_playbook_clipped(tmp_path: Path) -> None:
    b = _with(_briefing(), REAL_DUTY_VIEW)
    b["timeline"] = ["y" * 500] * 400
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    store.record(investigation_id="inv-c", alarm_ids=["a1"], status="done", source="sre_agent", briefing=b)
    rec = store.get_by_investigation("inv-c")
    assert rec is not None and rec["briefing"].get("duty_view") == REAL_DUTY_VIEW
    user = investigation_view(copy.deepcopy(rec), mask_fn=_route_mask_fn())
    assert isinstance(user.get("briefing"), dict) and "duty_view" in user["briefing"]


def test_render_lines_without_key_bit_identical_to_baseline() -> None:
    """기준선 렌더러 원본과 현재 렌더러가 키 없는 브리핑에 같은 줄을 낸다."""
    src = subprocess.run(
        ["git", "show", f"{BASE_SHA}:noise_gate/domain/investigation_briefing.py"],
        cwd=_ROOT, capture_output=True, check=True, timeout=30,
    ).stdout.decode("utf-8")
    ns: dict[str, Any] = {"__name__": "_w4v_baseline_ib"}
    exec(compile(src, "baseline_investigation_briefing.py", "exec"), ns)  # noqa: S102
    b = _briefing()
    assert ns["render_briefing_lines"](copy.deepcopy(b)) == render_briefing_lines(copy.deepcopy(b))


# ── 6. 계약 테스트 변형 실험 ─────────────────────────────────────────────────


def _mutated(monkeypatch: pytest.MonkeyPatch, which: str, fn: Callable[[Any], None]) -> None:
    data = copy.deepcopy(getattr(contract, which))
    fn(data)
    monkeypatch.setattr(contract, which, data)


@pytest.mark.parametrize("which,mutate,test_name", [
    ("_COPY", lambda d: d["areas"][0].__setitem__("label", "서버"), "test_area_codes_order_and_labels_match_body"),
    ("_BODY", lambda d: d["areas"].append({"code": "network", "label": "네트워크"}),
     "test_area_codes_order_and_labels_match_body"),
    ("_COPY", lambda d: d["areas"].reverse(), "test_area_codes_order_and_labels_match_body"),
    ("_COPY", lambda d: d["external"].__setitem__("label", "외부 연계 — 확인"), "test_external_label_matches_cause_hints"),
    ("_COPY", lambda d: d["process"]["kind_areas"]["db"].remove("tibero"), "test_process_kind_areas_match_cause_hints"),
    ("_COPY", lambda d: d["process_signatures"][0].__setitem__("match_regex", r"^db2sysc"),
     "test_db_engine_signature_copies_match"),
])
def test_contract_breaks_when_one_side_changes(
    monkeypatch: pytest.MonkeyPatch, which: str, mutate: Callable[[Any], None], test_name: str
) -> None:
    getattr(contract, test_name)()  # 원본은 통과
    _mutated(monkeypatch, which, mutate)
    with pytest.raises(AssertionError):
        getattr(contract, test_name)()


def test_contract_breaks_when_body_signature_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    sigs = copy.deepcopy(contract._SIGS)
    entries = sigs["signatures"] if isinstance(sigs, dict) else sigs
    for e in entries:
        if isinstance(e, dict) and e.get("kind") == "postgresql":
            e["match_regex"] = e["match_regex"] + "x"
    monkeypatch.setattr(contract, "_SIGS", sigs)
    with pytest.raises(AssertionError):
        contract.test_db_engine_signature_copies_match()


def test_contract_reads_files_not_imports() -> None:
    src = (_ROOT / "tests" / "test_plan201_w4_duty_contract.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+(sre_agent|src|noise_gate)\b", src, re.M)


# ── 8. XSS · 셀렉터 주입 ─────────────────────────────────────────────────────

_XSS = '<img src=x onerror=alert(1)>'

_DOM = r"""
var created = [];
function XEl(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.className = ""; this._text = ""; }
Object.defineProperty(XEl.prototype, "textContent", {
    get: function () { return this._text + this.children.map(function (c) { return c.textContent; }).join(""); },
    set: function (v) { this._text = String(v); this.children = []; },
});
Object.defineProperty(XEl.prototype, "innerHTML", {
    get: function () { return ""; }, set: function (v) { throw new Error("innerHTML 사용: " + v); },
});
Object.defineProperty(XEl.prototype, "firstChild", { get: function () { return this.children[0] || null; } });
XEl.prototype.appendChild = function (c) { this.children.push(c); return c; };
XEl.prototype.removeChild = function (c) { this.children.splice(this.children.indexOf(c), 1); return c; };
XEl.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
XEl.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
XEl.prototype.all = function () { var o = []; this.children.forEach(function (c) { o.push(c); o = o.concat(c.all()); }); return o; };
var selectors = [];
XEl.prototype.querySelector = function (sel) {
    selectors.push(sel);
    var m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (!m) throw new Error("이상한 셀렉터: " + sel);
    return this.all().filter(function (e) { var v = e.getAttribute(m[1]); return v !== null && (m[2] === undefined || v === m[2]); })[0] || null;
};
var xDoc = { createElement: function (t) { created.push(String(t)); var e = new XEl(t); e.ownerDocument = xDoc; return e; } };
"""


def test_fill_duty_view_xss_payload_stays_text() -> None:
    view = {"status": "done", "briefing": {"duty_view": {
        "areas": [{
            "code": f'db"><script>x</script>', "label": _XSS, "tag": _XSS, "status": "abnormal",
            "status_label": _XSS, "signals": [_XSS], "steps": ['S3"],[onclick="x', "S3"], "reason": _XSS,
        }],
        "handoff": _XSS,
    }}}
    bv = (_STATIC / "js" / "briefing-view.js").as_posix()
    script = f"""
var BV = require({json.dumps(bv)});
{_DOM}
BV.setDutyMeta(null);
var c = xDoc.createElement("div");
var slot = xDoc.createElement("div"); slot.setAttribute("data-bv-duty", ""); c.appendChild(slot);
var st = xDoc.createElement("details"); st.setAttribute("data-bv-step", "S3"); c.appendChild(st);
created = [];
BV.fillDutyView(c, {_js(view)});
var tr = c.all().filter(function (e) {{ return e.tagName === "tr" && e.getAttribute("data-duty-code") !== null; }})[0];
var btn = c.all().filter(function (e) {{ return e.getAttribute("data-bv-action") === "goto-step"; }})[0];
out = {{ tags: created, selectors: selectors, code: tr.getAttribute("data-duty-code"),
        step: btn ? btn.getAttribute("data-step") : null, text: c.textContent }};
"""
    out = _node(script)
    assert set(out["tags"]) <= {"div", "table", "thead", "tbody", "tr", "th", "td", "span", "button"}
    assert "script" not in out["tags"] and "img" not in out["tags"]
    assert _XSS in out["text"]  # 텍스트로만 들어간다
    assert out["code"] == 'db"><script>x</script>'  # 속성값(setAttribute) — 마크업 아님
    step_sels = [s for s in out["selectors"] if s.startswith("[data-bv-step=")]
    assert all(re.fullmatch(r'\[data-bv-step="[A-Za-z0-9_-]*"\]', s) for s in step_sels), step_sels


def test_goto_step_attribute_matches_selector_value() -> None:
    view = {"status": "done", "briefing": {"duty_view": {"areas": [{
        "code": "db", "label": "DB", "status": "suspect", "status_label": "의심",
        "signals": [], "steps": ['S3"]'], "reason": "",
    }], "handoff": ""}}}
    bv = (_STATIC / "js" / "briefing-view.js").as_posix()
    out = _node(f"""
var BV = require({json.dumps(bv)});
{_DOM}
var c = xDoc.createElement("div");
var slot = xDoc.createElement("div"); slot.setAttribute("data-bv-duty", ""); c.appendChild(slot);
var st = xDoc.createElement("details"); st.setAttribute("data-bv-step", "S3"); c.appendChild(st);
BV.fillDutyView(c, {_js(view)});
var btn = c.all().filter(function (e) {{ return e.getAttribute("data-bv-action") === "goto-step"; }})[0];
out = btn ? btn.getAttribute("data-step") : null;
""")
    assert out in (None, "S3")


def test_render_body_placeholder_has_no_payload() -> None:
    dv = {"areas": [{"code": _XSS, "label": _XSS, "status": "x", "signals": [_XSS], "steps": []}], "handoff": _XSS}
    view = json.loads((_ROOT / "tests" / "fixtures" / "plan199" / "confirmed_same_layer.json")
                      .read_text(encoding="utf-8"))["view"]
    view["briefing"] = _with(view["briefing"], dv)
    bv = (_STATIC / "js" / "briefing-view.js").as_posix()
    out = _node(f"var BV = require({json.dumps(bv)}); out = [BV.renderBody({_js(view)}, {{}}), "
                f"BV.renderBody({_js(view)}, {{ mode: 'detail' }})];")
    for html in out:
        assert html.count('<div class="bv-pb-block bv-duty" data-bv-duty=""></div>') == 1
        assert "onerror" not in html and "<img" not in html


# ── 9·10. 인가 비참조 · D-139 · 무변경 ──────────────────────────────────────

_W4_ALLOWED = {
    "noise_gate/domain/investigation_briefing.py",
    "noise_gate/infrastructure/investigation_store.py",
}


def test_duty_view_token_only_in_allowlisted_body_modules() -> None:
    found: set[str] = set()
    for pkg in ("src", "noise_gate"):
        for p in (_ROOT / pkg).rglob("*.py"):
            rel = p.relative_to(_ROOT).as_posix()
            if "/tests/" in rel:
                continue
            if "duty_view" in p.read_text(encoding="utf-8", errors="ignore"):
                found.add(rel)
    assert found <= _W4_ALLOWED, sorted(found - _W4_ALLOWED)


def test_sre_agent_duty_modules_do_not_import_body() -> None:
    pat = re.compile(r"^\s*(from|import)\s+(src|noise_gate|apm_gateway|mcp_server)\b", re.M)
    for rel in ("sre_agent/sre_agent/domain/duty_view.py",
                "sre_agent/sre_agent/infrastructure/duty_area_loader.py",
                "sre_agent/sre_agent/application/briefing_builder.py",
                "sre_agent/sre_agent/application/investigation_dispatcher.py",
                "sre_agent/sre_agent/settings.py"):
        assert not pat.search((_ROOT / rel).read_text(encoding="utf-8")), rel
    body_pat = re.compile(r"^\s*(from|import)\s+sre_agent\b", re.M)
    for pkg in ("src", "noise_gate"):
        for p in (_ROOT / pkg).rglob("*.py"):
            assert not body_pat.search(p.read_text(encoding="utf-8", errors="ignore")), p


def test_duty_view_domain_is_stdlib_plus_domain_only() -> None:
    src = (_ROOT / "sre_agent/sre_agent/domain/duty_view.py").read_text(encoding="utf-8")
    mods = set(re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, re.M))
    assert all(m.split(".")[0] in {"__future__", "re", "collections", "dataclasses", "typing"}
               or m.startswith("sre_agent.domain.") for m in mods), mods


@pytest.mark.parametrize("rel", [
    "sre_agent/sre_agent/domain/engine_agreement.py",
    "sre_agent/sre_agent/domain/playbook_hypotheses.py",
    "config/duty_areas.yaml",
])
def test_source_tables_unchanged_since_base(rel: str) -> None:
    out = subprocess.run(["git", "diff", "--stat", BASE_SHA, "--", rel], cwd=_ROOT,
                         capture_output=True, check=True, timeout=30).stdout.decode("utf-8")
    assert out.strip() == "", out


def test_middleware_signatures_change_is_append_only() -> None:
    """W3가 DB 엔진 시그니처를 추가만 했다(삭제 줄 0) — W4 사본의 정본."""
    out = subprocess.run(["git", "diff", "--numstat", BASE_SHA, "--", "config/middleware_signatures.yaml"],
                         cwd=_ROOT, capture_output=True, check=True, timeout=30).stdout.decode("utf-8")
    if out.strip():
        added, deleted, _ = out.split("\t", 2)
        assert deleted == "0", out


# ── 11. 캐시 버전 ───────────────────────────────────────────────────────────


def test_cache_version_asserts_consistent_repo_wide() -> None:
    pages = {p: (_STATIC / p).read_text(encoding="utf-8") for p in ("index.html", "noise.html", "admin/noise.html")}
    bv = {re.search(r"briefing-view\.js\?v=(\d+)", t).group(1) for t in pages.values()}  # type: ignore[union-attr]
    app = re.search(r"app\.js\?v=(\d+)", pages["index.html"]).group(1)  # type: ignore[union-attr]
    assert len(bv) == 1
    stale: list[str] = []
    for p in (_ROOT / "tests").rglob("*.py"):
        if p.name == Path(__file__).name:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"briefing-view\.js\?v=(\d+)", text):
            if m.group(1) not in bv:
                stale.append(f"{p.name}: briefing-view v={m.group(1)}")
        for m in re.finditer(r"(?<![\w-])app\.js\?v=(\d+)", text):
            if m.group(1) != app:
                stale.append(f"{p.name}: app.js v={m.group(1)}")
    assert stale == [], stale
