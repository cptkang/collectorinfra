"""plans/201 W2 독립 검증 — 첫 화면 상황 안내 · 담당별 예시 질의 (verifier).

구현 테스트(`test_plan201_w2_welcome.py`·`test_plan201_w2_frontend.py`)와 별개로 다음을 본다.

  A. 실 라우트 `/auth/me` 플래그 8조합 행렬(키 집합·값) · env 별칭 미수용(AppConfig 중첩 경로) · env → create_app 배선.
  B. `verified_by` 제외 + 경고(문구 원문 없음) · 정본 파일 로드 결과 4개 · `gold:` 참조의 D-192 동형(질의 대조).
  C. 선별 상한(영역 3 · 전체 6 · 선언 순서 · 입력 순서 무관).
  D. 프론트 — **실 `/auth/me` 응답 shape**를 app.js W2 구역(node 실행)에 그대로 넣어 끝까지 잇는다.
     스크립트는 stdin으로 넘긴다(Windows 명령줄 길이 WinError 206 회피).
  E. 정적 — HTML 문자열 삽입 0 · template 비렌더 · CSS · 알람 수신 판정 순서 · 첫 화면 유지 · 인가 비참조 · 기록 위생.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from itertools import product
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient

from src.domain.duty_areas import is_valid_verified_by, parse_vocabulary, parse_welcome_queries
from src.domain.user import User, UserRole, UserStatus
from src.infrastructure.duty_area_vocabulary import read_duty_area_vocabulary

_ROOT = Path(__file__).resolve().parents[1]
_USER_SECRET = "v" * 40
_ADMIN_SECRET = "w" * 40
_APP_JS = (_ROOT / "src/static/js/app.js").read_text(encoding="utf-8")
_INDEX = (_ROOT / "src/static/index.html").read_text(encoding="utf-8")
_CSS = (_ROOT / "src/static/css/style.css").read_text(encoding="utf-8")
_START = "// ─── plans/201 W2 첫 화면 시작 ───"
_END = "// ─── plans/201 W2 첫 화면 끝 ───"
_ENV_NAMES = ("DUTY_AREA_ENABLED", "DUTY_WELCOME_HINTS_ENABLED", "WELCOME_HINTS_ENABLED",
              "DUTY_AREA_WELCOME_HINTS_ENABLED", "WELCOME_GUIDE_ENABLED", "DUTY_ALARM_FOCUS_ENABLED")


def _region() -> str:
    return _APP_JS[_APP_JS.index(_START):_APP_JS.index(_END)]


# ─── A. 실 라우트 /auth/me ─────────────────────────────────────────────────────


def _user(user_id: str, **kw) -> User:
    return User(user_id=user_id, username=user_id, hashed_password="x", role=UserRole.USER,
                status=UserStatus.ACTIVE, **kw)


def _hdr(sub: str) -> dict[str, str]:
    tok = jwt.encode({"sub": sub, "type": "user", "exp": datetime.now(UTC) + timedelta(hours=1)},
                     _USER_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


_USERS = {
    # 입력 순서를 일부러 뒤집는다(선언 순서는 server_os → was_mw → db)
    "multi": _user("multi", duty_areas=["db", "was_mw", "server_os"]),
    "dbonly": _user("dbonly", duty_areas=["db"]),
    "none": _user("none"),
}


def _app(*, duty: bool, hints: bool, guide: bool, duty_cfg=None, guide_cfg=None):
    """실 create_app — 검증 대상 필드는 전부 명시한다(`.env` 누수 차단). 어휘는 실 정본 파일."""
    from src.api.server import create_app
    from src.config import AdminConfig, AppConfig, AuthConfig, DutyAreaConfig, WelcomeGuideConfig

    cfg = AppConfig(
        auth=AuthConfig(enabled=True, jwt_secret=_USER_SECRET),
        admin=AdminConfig(jwt_secret=_ADMIN_SECRET),
        duty_area=duty_cfg if duty_cfg is not None else DutyAreaConfig(
            _env_file=None, enabled=duty, alarm_focus_enabled=False, welcome_hints_enabled=hints),
        welcome_guide=guide_cfg if guide_cfg is not None else WelcomeGuideConfig(_env_file=None, enabled=guide),
    )
    app = create_app(cfg)
    app.state.user_repo = SimpleNamespace(get_by_user_id=AsyncMock(side_effect=lambda uid: _USERS.get(uid)))
    return app


def _me(app, sub: str = "multi") -> dict:
    resp = TestClient(app).get("/api/v1/auth/me", headers=_hdr(sub))
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture(scope="module")
def baseline_off() -> dict:
    return _me(_app(duty=False, hints=False, guide=False))


@pytest.mark.parametrize("duty,hints,guide", list(product([False, True], repeat=3)))
def test_me_flag_matrix_keys_and_values(duty, hints, guide, baseline_off) -> None:
    body = _me(_app(duty=duty, hints=hints, guide=guide))
    base_keys = list(baseline_off)
    expected = list(base_keys)
    if duty:
        expected += ["duty_areas", "duty_area_options"]
        if hints:
            expected += ["duty_welcome_queries"]
    if guide:
        expected += ["welcome_guide"]
    assert list(body) == expected
    # 기존 키 값은 플래그와 무관하게 같다
    assert {k: body[k] for k in base_keys} == baseline_off
    if guide:
        assert body["welcome_guide"] is True
    # W2 둘 다 off(담당만 켬)면 W1 응답과 비트 동일
    if duty and not hints and not guide:
        w1 = _me(_app(duty=True, hints=False, guide=False))
        assert json.dumps(body, sort_keys=True) == json.dumps(w1, sort_keys=True)


def test_me_off_body_has_no_new_tokens(baseline_off) -> None:
    text = json.dumps(baseline_off, ensure_ascii=False)
    assert "welcome" not in text and "duty" not in text


def test_me_real_shape_from_real_vocabulary() -> None:
    body = _me(_app(duty=True, hints=True, guide=True))
    picked = body["duty_welcome_queries"]
    assert all(set(p) == {"area", "area_label", "label", "query"} for p in picked)
    assert all(isinstance(v, str) and v for p in picked for v in p.values())
    # 선언 순서(server_os → was_mw) · 정본 로드 4개 · db 영역은 문구 0
    assert [(p["area"], p["label"]) for p in picked] == [
        ("server_os", "알람 많은 서버 상위 10"), ("server_os", "CPU 평균 상위 10대"),
        ("was_mw", "WAS 응답시간 상태"), ("was_mw", "실패율 기준 WAS 순위"),
    ]
    assert "verified_by" not in json.dumps(body) and "pending" not in json.dumps(body)
    # db만 담당 = 빈 목록(화면은 현행 버튼) · 미지정도 빈 목록
    assert _me(_app(duty=True, hints=True, guide=False), "dbonly")["duty_welcome_queries"] == []
    assert _me(_app(duty=True, hints=True, guide=False), "none")["duty_welcome_queries"] == []


def _clear_env(monkeypatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_env_aliases_not_read_through_appconfig_nested(monkeypatch) -> None:
    """AppConfig 중첩 default_factory 경로에서도 별칭 env는 읽히지 않는다."""
    from src.config import AppConfig

    _clear_env(monkeypatch)
    monkeypatch.setenv("DUTY_AREA_ENABLED", "true")
    monkeypatch.setenv("WELCOME_HINTS_ENABLED", "true")
    monkeypatch.setenv("DUTY_AREA_WELCOME_HINTS_ENABLED", "true")
    monkeypatch.setenv("welcome_hints_enabled", "true")
    cfg = AppConfig(_env_file=None)
    assert cfg.duty_area.enabled is True
    assert cfg.duty_area.welcome_hints_enabled is False and cfg.duty_area.welcome_hints_effective is False
    monkeypatch.setenv("DUTY_WELCOME_HINTS_ENABLED", "true")
    assert AppConfig(_env_file=None).duty_area.welcome_hints_effective is True


def test_env_effective_and_to_create_app(monkeypatch) -> None:
    """env만으로 켠 경우의 실효 AND와 create_app 반영(기동 시 1회)."""
    from src.config import DutyAreaConfig, WelcomeGuideConfig

    _clear_env(monkeypatch)
    monkeypatch.setenv("DUTY_WELCOME_HINTS_ENABLED", "true")
    monkeypatch.setenv("WELCOME_GUIDE_ENABLED", "true")
    app = _app(duty=False, hints=False, guide=False,
               duty_cfg=DutyAreaConfig(_env_file=None), guide_cfg=WelcomeGuideConfig(_env_file=None))
    assert app.state.duty_welcome_hints_enabled is False      # DUTY_AREA_ENABLED off → 키 없음
    assert app.state.welcome_guide_enabled is True            # 담당 off에서도 독립
    body = _me(app)
    assert "duty_welcome_queries" not in body and "duty_areas" not in body
    assert body["welcome_guide"] is True
    # 기동 뒤 env를 꺼도 앱 상태는 그대로(기동 시 1회)
    monkeypatch.setenv("WELCOME_GUIDE_ENABLED", "false")
    assert _me(app)["welcome_guide"] is True


def test_welcome_guide_env_aliases_not_read(monkeypatch) -> None:
    from src.config import WelcomeGuideConfig

    _clear_env(monkeypatch)
    for name in ("WELCOME_GUIDE", "GUIDE_ENABLED", "DUTY_WELCOME_GUIDE_ENABLED", "DUTY_AREA_WELCOME_GUIDE_ENABLED"):
        monkeypatch.setenv(name, "true")
    assert WelcomeGuideConfig(_env_file=None).enabled is False


# ─── B. verified_by 제외 · 정본 · 골드 동형 ──────────────────────────────────


_SECRET_Q = "QTEXT-원문-노출-금지-7f3a"


@pytest.mark.parametrize("entry", [
    {"label": "L-none"},
    {"label": "L-empty", "verified_by": ""},
    {"label": "L-space", "verified_by": "   "},
    {"label": "L-outside", "verified_by": "manual-check"},
    {"label": "L-prefix", "verified_by": "gold:gp"},
    {"label": "L-list", "verified_by": ["gold:gp/gp-001"]},
    {"label": "L-feb30", "verified_by": "closed_net:2026-02-30"},
    {"label": "L-month13", "verified_by": "closed_net:2026-13-01"},
    {"label": "L-short", "verified_by": "closed_net:2026-1-5"},
    {"label": "L-word", "verified_by": "closed_net:today"},
    {"label": "L-mlx", "verified_by": "mlx:remote"},
    {"label": "L-pending-valid", "verified_by": "gold:gp/gp-015", "pending": "closed_net"},
    {"label": "L-pending-only", "pending": "closed_net"},
])
def test_verified_by_excluded_with_warning_no_query_text(entry, caplog) -> None:
    item = dict(entry, query=f"{_SECRET_Q} {entry['label']}")
    raw = {"alpha": [item, {"label": "ok", "query": "정상 질의", "verified_by": "sandbox:polestar"}]}
    with caplog.at_level(logging.WARNING, logger="src.domain.duty_areas"):
        result = parse_welcome_queries(raw, ("alpha",))
    assert [q.label for q in result["alpha"]] == ["ok"]
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(entry["label"] in m for m in msgs), msgs
    assert not any(_SECRET_Q in m for m in msgs), msgs


def test_valid_conventions_loaded_in_order() -> None:
    raw = {"alpha": [
        {"label": "g", "query": "q1", "verified_by": "gold:gp/gp-015"},
        {"label": "a", "query": "q2", "verified_by": "assembler:try_deterministic_alarm_sql"},
        {"label": "s", "query": "q3", "verified_by": "sandbox:polestar"},
        {"label": "m", "query": "q4", "verified_by": "mlx:local"},
        {"label": "c", "query": "q5", "verified_by": "closed_net:2026-10-30"},
    ]}
    assert [q.label for q in parse_welcome_queries(raw, ("alpha",), today=date(2026, 10, 30))["alpha"]] == [
        "g", "a", "s", "m", "c"]


def test_closed_net_future_date_rejected() -> None:
    ref = date(2026, 10, 30)                                   # 기준일 고정(결정성)
    assert is_valid_verified_by("closed_net:2026-10-30", today=ref) is True     # 당일 허용
    assert is_valid_verified_by("closed_net:2026-10-31", today=ref) is False    # 다음 날 거부
    assert is_valid_verified_by("closed_net:2099-01-01") is False               # 기본값(오늘) 경로


def test_real_file_loads_exactly_four() -> None:
    vocab = read_duty_area_vocabulary(_ROOT / "config/duty_areas.yaml")
    loaded = {code: [(q.label, q.verified_by) for q in qs] for code, qs in vocab.welcome_queries.items()}
    assert loaded == {
        "server_os": [("알람 많은 서버 상위 10", "gold:gp/gp-015"), ("CPU 평균 상위 10대", "gold:syn/syn-e01")],
        "was_mw": [("WAS 응답시간 상태", "gold:scenarios/APM-HEALTH-untargeted"),
                   ("실패율 기준 WAS 순위", "gold:scenarios/APM-RANK-failrate-full")],
    }
    raw = yaml.safe_load((_ROOT / "config/duty_areas.yaml").read_text(encoding="utf-8"))["welcome_queries"]
    assert sum(1 for es in raw.values() for e in es if e.get("pending")) == 8


def _gold_item(rel: str, item_id: str) -> dict:
    body = yaml.safe_load((_ROOT / rel).read_text(encoding="utf-8"))
    items = body if isinstance(body, list) else next(v for v in body.values() if isinstance(v, list))
    hit = [it for it in items if isinstance(it, dict) and it.get("id") == item_id]
    assert len(hit) == 1, (rel, item_id)
    return hit[0]


def _welcome(label: str) -> str:
    vocab = read_duty_area_vocabulary(_ROOT / "config/duty_areas.yaml")
    return next(q.query for qs in vocab.welcome_queries.values() for q in qs if q.label == label)


def test_gold_gp015_isomorphic_only_zone_prefix_added() -> None:
    gold = _gold_item("testdata/text2sql_gold/gp.yaml", "gp-015")
    assert "known_fail" not in gold
    assert _welcome("알람 많은 서버 상위 10") == "ㅇㅇ존에서 " + gold["query"]


def test_gold_syn_e01_shape_axes_match_and_period_from_gp009() -> None:
    """syn-e01(서버별 월 CPU 평균 상위 N) — 축·집계·랭킹은 같고 기간 표현은 gp-009 「지난달 1개월 통계 기준」."""
    gold = _gold_item("testdata/text2sql_gold_sandbox/syn.yaml", "syn-e01")
    gp009 = _gold_item("testdata/text2sql_gold/gp.yaml", "gp-009")
    q = _welcome("CPU 평균 상위 10대")
    assert "known_fail" not in gold and "known_fail" not in gp009
    for token in ("서버별", "CPU 사용률 평균이 높은 상위", "대를 보여줘"):
        assert token in gold["query"] and token in q, token
    assert "지난달 1개월 통계 기준" in gp009["query"] and "지난달 1개월 통계 기준" in q
    assert "cmm_metric_stat_m" in gold["gold_sql"]            # 월 통계 — 「지난달 1개월 통계」와 같은 원천


@pytest.mark.parametrize("label,scenario_id", [
    ("WAS 응답시간 상태", "APM-HEALTH-untargeted"),
    ("실패율 기준 WAS 순위", "APM-RANK-failrate-full"),
])
def test_gold_apm_scenarios_verbatim_and_not_skipped(label, scenario_id) -> None:
    sc = _gold_item("testdata/apm_bench/scenarios.yaml", scenario_id)
    assert not ({"known_fail", "e2_skip", "refusal", "clarify"} & set(sc))
    assert len(sc["turns"]) == 1 and _welcome(label) == sc["turns"][0]["prompt"]
    assert "refusal" not in sc["turns"][0]["expect"] and "clarify" not in sc["turns"][0]["expect"]
    # 기록: 두 시나리오 모두 「무거운 등급」(D-323 운영 뷰 서버 허용 범위) — 보고서 Minor 근거
    assert sc.get("heavy") is True


# ─── C. 선별 상한 ─────────────────────────────────────────────────────────────


def _vocab4():
    return parse_vocabulary({
        "areas": [{"code": c, "label": c.upper()} for c in ("a", "b", "c")],
        "welcome_queries": {c: [{"label": f"{c}{i}", "query": f"{c} 질의 {i}", "verified_by": "mlx:local"}
                                for i in range(4)] for c in ("c", "b", "a")},   # 파일 키 순서도 뒤집는다
    })


@pytest.mark.parametrize("areas", [["a", "b", "c"], ["c", "b", "a"], ["c", "x", "a", "b", "a"]])
def test_select_caps_and_declared_order(areas) -> None:
    picked = _vocab4().select_welcome_queries(areas, per_area=3, total=6)
    assert [p["label"] for p in picked] == ["a0", "a1", "a2", "b0", "b1", "b2"]


def test_select_plan_caps_constants() -> None:
    from src.api import duty_area_support as das

    assert (das.WELCOME_QUERIES_PER_AREA, das.WELCOME_QUERIES_TOTAL) == (3, 6)
    assert _vocab4().select_welcome_queries(["b"], per_area=3, total=6)[-1]["label"] == "b2"
    assert _vocab4().select_welcome_queries(None) == [] and _vocab4().select_welcome_queries([]) == []


# ─── D. 프론트 — 실 shape 종단 (node · stdin) ─────────────────────────────────


class _Tree(HTMLParser):
    _VOID = {"br", "img", "input", "hr", "meta", "link", "polyline", "line", "path"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": {k: (v or "") for k, v in attrs}, "children": []}
        self.stack[-1]["children"].append(node)
        if tag not in self._VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag in self._VOID:
            return
        while len(self.stack) > 1:
            if self.stack.pop()["tag"] == tag:
                break

    def handle_data(self, data):
        if data.strip():
            self.stack[-1]["children"].append({"text": re.sub(r"\s+", " ", data.strip())})


def _spec(fragment: str) -> dict:
    t = _Tree()
    t.feed(fragment)
    return t.root["children"][0]


def _welcome_spec() -> dict:
    start = _INDEX.index('<div class="chat-welcome" id="chatWelcome">')
    block = _INDEX[start:_INDEX.index("<template", start)]
    return _spec(block)


def _template_spec() -> dict:
    m = re.search(r'<template id="welcomeGuideTemplate">(.*?)</template>', _INDEX, re.S)
    assert m
    return _spec(m.group(1))


_DOM = r"""
var LOG = { html: 0, sent: 0, views: [], focus: 0, files: 0 };
function El(tag) {
    var s = this; this.tagName = String(tag).toUpperCase(); this.attrs = {}; this.children = [];
    this.parentNode = null; this.ls = {}; this.disabled = false; this.dataset = {}; this.t = "";
    this.classList = {
        contains: function (c) { return (s.attrs["class"] || "").split(/\s+/).indexOf(c) >= 0; },
        add: function (c) { if (!s.classList.contains(c)) s.attrs["class"] = ((s.attrs["class"] || "") + " " + c).trim(); },
        remove: function (c) { s.attrs["class"] = (s.attrs["class"] || "").split(/\s+/).filter(function (x) { return x && x !== c; }).join(" "); },
        toggle: function (c, on) { if (on === undefined) on = !s.classList.contains(c); if (on) s.classList.add(c); else s.classList.remove(c); return on; }
    };
}
["innerHTML", "outerHTML"].forEach(function (k) {
    Object.defineProperty(El.prototype, k, { get: function () { return ""; }, set: function () { LOG.html += 1; } });
});
El.prototype.insertAdjacentHTML = function () { LOG.html += 1; };
Object.defineProperty(El.prototype, "className", { get: function () { return this.attrs["class"] || ""; }, set: function (v) { this.attrs["class"] = String(v); } });
Object.defineProperty(El.prototype, "id", { get: function () { return this.attrs.id || ""; } });
Object.defineProperty(El.prototype, "title", { get: function () { return this.attrs.title || ""; }, set: function (v) { this.attrs.title = String(v); } });
Object.defineProperty(El.prototype, "type", { get: function () { return this.attrs.type || ""; }, set: function (v) { this.attrs.type = String(v); } });
Object.defineProperty(El.prototype, "textContent", {
    get: function () { return this.tagName === "#TEXT" ? this.t : this.children.map(function (c) { return c.textContent; }).join(" "); },
    set: function (v) { this.children = []; var t = new El("#text"); t.t = String(v); t.parentNode = this; this.children.push(t); }
});
El.prototype.getAttribute = function (k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.appendChild = function (n) { return this.insertBefore(n, null); };
El.prototype.insertBefore = function (n, ref) {
    if (ref && ref.parentNode !== this) throw new Error("NotFoundError");
    if (n.isFrag) { var me = this; n.children.slice().forEach(function (c) { me.insertBefore(c, ref); }); return n; }
    if (n.parentNode) n.parentNode.children.splice(n.parentNode.children.indexOf(n), 1);
    n.parentNode = this; var at = ref ? this.children.indexOf(ref) : -1;
    if (at < 0) this.children.push(n); else this.children.splice(at, 0, n); return n;
};
El.prototype.remove = function () { if (this.parentNode) { this.parentNode.children.splice(this.parentNode.children.indexOf(this), 1); this.parentNode = null; } };
El.prototype.addEventListener = function (t, f) { (this.ls[t] = this.ls[t] || []).push(f); };
El.prototype.click = function () { if (this.disabled) return; var s = this; (this.ls.click || []).forEach(function (f) { f({ target: s }); }); };
function m(el, sel) {
    if (el.tagName === "#TEXT") return false;
    if (sel[0] === ".") return el.classList.contains(sel.slice(1));
    if (sel[0] === "#") return el.attrs.id === sel.slice(1);
    var a = /^\[([\w-]+)\]$/.exec(sel); if (a) return Object.prototype.hasOwnProperty.call(el.attrs, a[1]);
    return el.tagName === sel.toUpperCase();
}
El.prototype.querySelectorAll = function (sel) { var out = []; (function w(n) { n.children.forEach(function (c) { if (m(c, sel)) out.push(c); w(c); }); })(this); return out; };
El.prototype.querySelector = function (sel) { return this.querySelectorAll(sel)[0] || null; };
El.prototype.cloneNode = function (deep) {
    var c = new El(this.tagName === "#TEXT" ? "#text" : this.tagName); c.attrs = JSON.parse(JSON.stringify(this.attrs));
    c.t = this.t; c.isFrag = this.isFrag; c.disabled = this.disabled; c.dataset = JSON.parse(JSON.stringify(this.dataset));
    if (deep) this.children.forEach(function (k) { var kk = k.cloneNode(true); kk.parentNode = c; c.children.push(kk); });
    return c;
};
function build(sp) {
    if (sp.text !== undefined) { var t = new El("#text"); t.t = sp.text; return t; }
    var el = new El(sp.tag);
    Object.keys(sp.attrs).forEach(function (k) {
        el.attrs[k] = sp.attrs[k];
        if (k === "disabled") el.disabled = true;
        if (k.indexOf("data-") === 0) el.dataset[k.slice(5).replace(/-([a-z])/g, function (_, x) { return x.toUpperCase(); })] = sp.attrs[k];
    });
    sp.children.forEach(function (c) { var k = build(c); k.parentNode = el; el.children.push(k); });
    return el;
}
function ser(n) {
    if (n.tagName === "#TEXT") return n.t;
    var a = Object.keys(n.attrs).sort().map(function (k) { return k + "=" + JSON.stringify(n.attrs[k]); }).join(" ");
    return "<" + n.tagName + " " + a + (n.disabled ? " [d]" : "") + ">" + n.children.map(ser).join("") + "</" + n.tagName + ">";
}
var BODY = new El("body");
var chatWelcome = build(INPUT.welcome); BODY.appendChild(chatWelcome);
var TPL = new El("template"); TPL.content = build({ tag: "frag", attrs: {}, children: [INPUT.tpl] }); TPL.content.isFrag = true;
var document = {
    getElementById: function (id) { return id === "welcomeGuideTemplate" ? TPL : BODY.querySelector("#" + id); },
    createElement: function (t) { return new El(t); }
};
var promptEl = { value: "", focus: function () { LOG.focus += 1; } };
function autoResizeTextarea() {}
function handleSend() { LOG.sent += 1; }
function setActiveView(v) { LOG.views.push(v); }
var fileInput = { click: function () { LOG.files += 1; } };
var alarmCanReceive = false;
var ME = INPUT.me;
function fetchAuthMeOnce() { return Promise.resolve(ME); }
function hints() {
    return chatWelcome.querySelector(".chat-welcome-hints").querySelectorAll(".chat-welcome-hint").map(function (b) {
        return { text: b.textContent.trim(), cls: b.className, query: b.dataset.query || null, help: !!b.dataset.help, title: b.title };
    });
}
function top() { return chatWelcome.children.filter(function (c) { return c.tagName !== "#TEXT"; }).map(function (c) { return c.className || c.tagName; }); }
function guideLines() {
    return chatWelcome.querySelectorAll("[data-guide]").map(function (b) {
        return { g: b.getAttribute("data-guide"), disabled: !!b.disabled, aria: b.getAttribute("aria-disabled"),
                 reason: (b.querySelector(".chat-welcome-guide-reason") || { textContent: null }).textContent };
    });
}
function elementTags(n) { var out = []; (function w(x) { x.children.forEach(function (c) { if (c.tagName !== "#TEXT") out.push(c.tagName); w(c); }); })(n); return out; }
"""


def _node(me: object, body: str, *, alarm: bool = False) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node 미설치")
    payload = {"welcome": _welcome_spec(), "tpl": _template_spec(), "me": me}
    script = "\n".join([
        f"var INPUT = {json.dumps(payload, ensure_ascii=False)};",
        _DOM,
        _region(),
        "chatWelcome.querySelectorAll('.chat-welcome-hint').forEach(bindHintButton);",
        "var BEFORE = ser(chatWelcome);",
        f"alarmCanReceive = {'true' if alarm else 'false'};",
        "loadWelcomeEnhancements();",
        "setTimeout(function () { try {", body, "} catch (e) { console.log(JSON.stringify({error: String(e)})); } }, 0);",
    ])
    # stdin으로 넘긴다 — `node -e <긴 스크립트>`는 Windows 명령줄 길이(WinError 206)에 걸린다
    done = subprocess.run(["node", "-"], input=script.encode("utf-8"), capture_output=True, timeout=30, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    out = json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])
    assert "error" not in out, out
    return out


_SNAP = "console.log(JSON.stringify({same: ser(chatWelcome) === BEFORE, top: top(), hints: hints(), " \
        "guide: guideLines(), log: LOG}));"


@pytest.mark.parametrize("duty,hints,guide", list(product([False, True], repeat=3)))
def test_frontend_real_me_matrix(duty, hints, guide) -> None:
    """실 `/auth/me` 응답(8조합)을 그대로 프론트에 — W2 실효 키가 없으면 #chatWelcome 비트 동일."""
    me = _me(_app(duty=duty, hints=hints, guide=guide))
    out = _node(me, _SNAP)
    has_hints = duty and hints              # multi 사용자는 server_os·was_mw 문구가 있다
    assert out["same"] is (not has_hints and not guide)
    assert bool(out["guide"]) is guide
    labels = [h["text"] for h in out["hints"]]
    if has_hints:
        assert labels == ["알람 많은 서버 상위 10", "CPU 평균 상위 10대", "WAS 응답시간 상태",
                          "실패율 기준 WAS 순위", "❓ 사용법"]
        assert "chat-welcome-hints-title" in out["top"]
        assert all(h["query"] == q["query"] for h, q in zip(out["hints"], me["duty_welcome_queries"]))
    else:
        assert labels[0] == "CPU 사용률 현황" and labels[-1] == "❓ 사용법"
        assert "chat-welcome-hints-title" not in out["top"]
    if guide:
        tops = out["top"]
        assert tops.index("chat-welcome-guide") < tops.index("chat-welcome-hints")
    assert out["log"]["html"] == 0


def test_frontend_db_only_user_keeps_current_buttons() -> None:
    me = _me(_app(duty=True, hints=True, guide=False), "dbonly")
    assert me["duty_welcome_queries"] == []
    assert _node(me, _SNAP)["same"] is True


def test_frontend_title_and_click_fills_prompt_with_zone_placeholder() -> None:
    me = _me(_app(duty=True, hints=True, guide=False))
    out = _node(me, r"""
        var title = chatWelcome.querySelector(".chat-welcome-hints-title").textContent;
        var btns = chatWelcome.querySelectorAll(".chat-welcome-hint--duty");
        btns[0].click(); var v0 = promptEl.value; var sent0 = LOG.sent;
        chatWelcome.querySelector(".chat-welcome-hint--help").click();
        console.log(JSON.stringify({title: title, v0: v0, sent0: sent0, sent1: LOG.sent, focus: LOG.focus, h: hints()}));
    """)
    assert out["title"] == "이렇게 물어보세요 · 서버/OS · WAS/미들웨어 담당 추천"
    assert out["v0"].startswith("ㅇㅇ존에서 ") and out["sent0"] == 0      # 채우기만 · 전송 안 함
    assert out["sent1"] == 1 and out["focus"] == 2                          # 사용법은 즉시 전송
    assert out["h"][0]["title"].startswith("서버/OS 담당 · ㅇㅇ존")
    assert out["h"][-1]["help"] is True


def test_frontend_idempotent_second_load() -> None:
    me = _me(_app(duty=True, hints=True, guide=True))
    out = _node(me, r"""
        loadWelcomeEnhancements();
        setTimeout(function () {
            console.log(JSON.stringify({g: chatWelcome.querySelectorAll(".chat-welcome-guide").length,
                t: chatWelcome.querySelectorAll(".chat-welcome-hints-title").length, n: hints().length}));
        }, 0);
        return;
    """)
    assert out == {"g": 1, "t": 1, "n": 5}


@pytest.mark.parametrize("alarm", [False, True])
def test_frontend_guide_lines_targets_and_alarm_state(alarm) -> None:
    me = _me(_app(duty=False, hints=False, guide=True))
    out = _node(me, r"""
        var lines = guideLines();
        chatWelcome.querySelectorAll("[data-guide]").forEach(function (b) { b.click(); });
        console.log(JSON.stringify({lines: lines, log: LOG}));
    """, alarm=alarm)
    by = {ln["g"]: ln for ln in out["lines"]}
    assert list(by) == ["alarm", "investigate", "report"]
    if alarm:
        assert by["alarm"]["disabled"] is False and by["alarm"]["reason"] is None
        assert out["log"]["views"] == ["alarm"]
    else:
        assert by["alarm"]["disabled"] is True and by["alarm"]["aria"] == "true"
        assert by["alarm"]["reason"] == "알람 수신 지역이 지정되지 않았습니다 — 관리자에게 요청하세요."
        assert out["log"]["views"] == []
    assert by["investigate"]["disabled"] is False and by["report"]["disabled"] is False
    assert out["log"]["focus"] == 1 and out["log"]["files"] == 1


def test_frontend_xss_payload_stays_text() -> None:
    evil = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    me = {"duty_welcome_queries": [
        {"area": "server_os", "area_label": evil, "label": evil, "query": evil}], "welcome_guide": True}
    out = _node(me, r"""
        var b = chatWelcome.querySelector(".chat-welcome-hint--duty");
        console.log(JSON.stringify({html: LOG.html, text: b.textContent, q: b.dataset.query,
            tags: elementTags(b), titleTxt: chatWelcome.querySelector(".chat-welcome-hints-title").textContent}));
    """)
    assert out["html"] == 0 and out["tags"] == []
    assert out["text"] == evil and out["q"] == evil and evil in out["titleTxt"]


# ─── E. 정적 ─────────────────────────────────────────────────────────────────


def test_region_has_no_html_string_sinks() -> None:
    region = _region()
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in region, sink


def test_template_not_rendered_and_css_does_not_reveal_it() -> None:
    welcome_start = _INDEX.index('id="chatWelcome"')
    tpl_at = _INDEX.index('<template id="welcomeGuideTemplate">')
    messages_close = _INDEX.index("<!-- DB connection warning banner -->")
    assert welcome_start < tpl_at < messages_close
    # template은 #chatMessages 밖(chat-main 직속) — 첫 화면 블록 안에 들어 있지 않다
    chat_block = _INDEX[_INDEX.index('id="chatMessages"'):tpl_at]
    assert chat_block.count("<div") <= chat_block.count("</div>")
    # CSS가 template/내용을 보이게 하지 않는다
    assert not re.search(r"(^|[\s,}])template\b[^{]*\{", _CSS)
    assert "#welcomeGuideTemplate" not in _CSS and "#chatWelcomeGuide" not in _CSS
    w2_css = _CSS[_CSS.index("plans/201 W2 첫 화면"):_CSS.index("Zone Clarification")]
    assert "display: none" not in w2_css and "!important" not in w2_css


def test_alarm_can_receive_set_synchronously_before_welcome_loader() -> None:
    body = _APP_JS[_APP_JS.index("function checkAuthOnLoad"):]
    assert body.index("initAlarmSubscription(userInfo, data.auth_enabled)") < body.index("loadWelcomeEnhancements()")
    init = _APP_JS[_APP_JS.index("function initAlarmSubscription"):]
    init = init[:init.index("\n    }\n")]
    assert init.index("alarmCanReceive =") < init.index(".then(")
    # 상황 안내는 then 안(응답 뒤)에서 alarmCanReceive를 읽는다
    region = _region()
    assert region.index("var caps = { alarm: !!alarmCanReceive }") > region.index("function applyWelcomeGuide")


def test_welcome_dom_only_hidden_toggled_outside_region() -> None:
    """새 대화·탭 복원은 #chatWelcome hidden만 토글한다 — 바꾼 버튼·안내가 유지된다."""
    region = _region()
    outside = _APP_JS.replace(region, "")
    for m in re.finditer(r"chatWelcome\.(\w+)", outside):
        assert m.group(1) == "classList", m.group(0)
    assert not re.search(r"chatMessages\.(innerHTML|textContent|replaceChildren)\s*=?", outside)
    assert "chat-welcome-hint--duty" not in outside and "chatWelcomeGuide" not in outside
    # 대화 캡처는 .message만 떼어 낸다
    cap = outside[outside.index("function captureConversation"):]
    assert 'el.classList.contains("message")' in cap[:600]


def test_hint_buttons_share_single_handler() -> None:
    assert "hintButtons.forEach(bindHintButton);" in _APP_JS
    assert _APP_JS.count("function bindHintButton") == 1


def test_welcome_guide_loader_gated_by_auth_enabled() -> None:
    """인증 꺼짐 기동이면 로더를 부르지 않는다 — plans/201 W2 팀 리드 결정: 로그인 사용자 한정."""
    line = next(ln for ln in _APP_JS.splitlines() if "loadWelcomeEnhancements();" in ln and "if (" in ln)
    assert "data.auth_enabled" in line and "userInfo.user_id" in line


_WELCOME_REFERRERS = {
    "src/api/duty_area_support.py", "src/api/routes/user_auth.py", "src/api/schemas.py", "src/api/server.py",
    "src/api/settings_catalog.py", "src/config/services.py", "src/config/__init__.py", "src/domain/duty_areas.py",
}


def test_welcome_values_not_in_authz_or_tokens() -> None:
    found = set()
    for pkg in ("src", "noise_gate"):
        for p in (_ROOT / pkg).rglob("*.py"):
            rel = p.relative_to(_ROOT).as_posix()
            if "/tests/" in rel:
                continue
            if re.search(r"welcome_(guide|queries|hints)", p.read_text(encoding="utf-8", errors="ignore")):
                found.add(rel)
    assert found <= _WELCOME_REFERRERS, sorted(found - _WELCOME_REFERRERS)
    u = _user("t", duty_areas=["server_os"])
    assert not any("welcome" in k for k in u.to_auth_dict())
    # user_auth.py 안에서 welcome 키를 쓰는 곳은 get_me 하나뿐(토큰 발급 payload 아님)
    text = (_ROOT / "src/api/routes/user_auth.py").read_text(encoding="utf-8")
    me_fn = text[text.index("async def get_me"):]
    me_fn = me_fn[:me_fn.index("\n@router")]
    outside = text.replace(me_fn, "")
    assert not re.search(r"welcome_guide_enabled\(|duty_welcome_queries\(", outside.split("from src.api.schemas")[1])


def test_w1_allowlist_still_covers_duty_area_referrers() -> None:
    """W2가 `duty_area`를 새로 읽는 모듈을 늘리지 않았다(W1 verify 허용 목록 그대로 통과)."""
    w1 = (_ROOT / "tests/test_plan201_w1_verify.py").read_text(encoding="utf-8")
    block = w1[w1.index("_ALLOWED_REFERRERS = {"):]
    allowed = set(re.findall(r'"([\w/.]+\.py)"', block[:block.index("}")]))
    found = set()
    for pkg in ("src", "noise_gate"):
        for p in (_ROOT / pkg).rglob("*.py"):
            rel = p.relative_to(_ROOT).as_posix()
            if "/tests/" not in rel and "duty_area" in p.read_text(encoding="utf-8", errors="ignore"):
                found.add(rel)
    assert found <= allowed, sorted(found - allowed)


def test_records_have_no_server_names_ips_or_sql() -> None:
    texts = {
        "duty_areas.yaml": (_ROOT / "config/duty_areas.yaml").read_text(encoding="utf-8"),
        "welcome_guide.yaml": (_ROOT / "config/settings_help/welcome_guide.yaml").read_text(encoding="utf-8"),
        "duty_area.yaml": (_ROOT / "config/settings_help/duty_area.yaml").read_text(encoding="utf-8"),
        "template": re.search(r"<template id=\"welcomeGuideTemplate\">.*?</template>", _INDEX, re.S).group(0),
    }
    for name, text in texts.items():
        assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text), name
        assert not re.search(r"\bSELECT\b.+\bFROM\b", text, re.S), name
        assert not re.search(r"\b(cocm|cob0|sbhdbo|hostapo|ngcapo)[\w-]*", text, re.I), name


def test_settings_reflection_points() -> None:
    from src.api.settings_catalog import GROUP_ORDER, GROUP_TITLES, field_index

    idx = field_index()
    assert idx["DUTY_WELCOME_HINTS_ENABLED"].group_key == "duty_area"
    assert idx["WELCOME_GUIDE_ENABLED"].group_key == "welcome_guide"
    assert "welcome_guide" in GROUP_ORDER and GROUP_TITLES["welcome_guide"]
    # 같은 그룹의 모든 필드에 도움말 항목이 있다
    for group, fname in (("duty_area", "duty_area.yaml"), ("welcome_guide", "welcome_guide.yaml")):
        help_keys = set(yaml.safe_load((_ROOT / "config/settings_help" / fname).read_text(encoding="utf-8"))["settings"])
        keys = {k for k, f in idx.items() if f.group_key == group}
        assert keys <= help_keys, (group, sorted(keys - help_keys))
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("DUTY_WELCOME_HINTS_ENABLED", "WELCOME_GUIDE_ENABLED"):
        assert re.search(rf"^# {key}=false$", example, re.M), key
        # 인라인 주석 금지
        assert not re.search(rf"^#?\s*{key}=\S+[ \t]+#", example, re.M), key


def test_manual_u78_matches_behaviour() -> None:
    md = (_ROOT / "scripts/manual/content/user.md").read_text(encoding="utf-8")
    u78 = md[md.index("### U-78"):]
    u78 = u78[:u78.index("\n### ", 5)]
    assert "로그인한 뒤" in u78                                   # 인증 사용자만(loader가 auth_enabled로 묶임)
    assert "입력창으로 커서" in u78 and "조사 대화" not in u78     # 「원인을 찾고 있다면」 = 입력창 포커스
    assert "최대 3개" in u78 and "최대 6개" in u78 and "맨 끝" in u78
    assert "바로 보내지는 않습니다" in u78
    tpl = re.search(r"<template id=\"welcomeGuideTemplate\">.*?</template>", _INDEX, re.S).group(0)
    m = re.search(r"「(알람 수신 지역이 지정되지 않았습니다[^」]*)」", u78)
    assert m and m.group(1) in tpl
    user_html = (_ROOT / "src/static/manual/user.html").read_text(encoding="utf-8")
    assert 'id="u-78"' in user_html
