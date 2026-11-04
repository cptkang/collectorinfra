"""plans/210 W6 — 관리자 「APM·SMS 연계」 탭(`admin-apm-host-map.js`) ·
장애 진단 화면 연계 문구(§3.11).

JS는 node로 실제 실행한다(W15 하네스 재사용 · plans/196 `_script` 꼴).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from tests.test_api.test_ui_plan187_w15_diagnosis import _node
from tests.test_api.test_ui_plan196_diagnosis import _CANDS, _LINK_HOST, _final

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "src" / "static"
_DV_JS = _STATIC / "js" / "diagnosis-view.js"
_AHM_JS = _STATIC / "js" / "admin-apm-host-map.js"
_DASH = _STATIC / "admin" / "dashboard.html"

_AUTO = "APM↔SMS 연계 — 자동(노드 주소 · VM 일치 · ITAM 업무 추정)"
_MANUAL = "APM↔SMS 연계 — 관리자 등록"
_MULTI = " · 연계 대상 2대(kbl-a·kbl-b) — 지표 추이는 대상마다 조회 · 그 밖의 폴스타 조회는 첫 대상"
_UNRESOLVED = (
    "폴스타 서버 미확정 — APM↔SMS 연계 미확정(자동 연계 실패 — 일치하는 폴스타 서버 없음)"
    " · 제니퍼 노드 주소 조회 비활성(게이트웨이 노드 주소 조회 꺼짐)"
)
_ABSENT = "APM↔SMS 연계 없음(관리자 지정 — 폴스타 조회 생략)"


def _script(final: dict, body: str, *, operator: bool = False, link_host: bool = True) -> str:
    """종결 조사 inv-f를 연 뒤 body를 돈다(plans/196 `_script`에 operator 선택만 더한 꼴)."""
    return f"""
routes[API + "/inv-f/host-candidates"] = {{ status: 200,
    body: {json.dumps(_CANDS, ensure_ascii=False)} }};
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(final, ensure_ascii=False)} }};
var posts = [];
var linkRes = {{ status: 200, body: {{}} }};
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: {str(operator).lower()}, headers: function () {{ return {{}}; }},
    onActivate: function () {{}}, onOpenConversation: function () {{}},
    analyzeFor: function () {{ return null; }},
    {_LINK_HOST if link_host else ""}
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
runTimers(1000);
ctl.open("inv-f");
await flush();
runTimers(1000);
var tab = ctl.tab("inv-f");
await flush();
ctl.select("inv-f");
await flush();
{body}
"""


_PROBE = """
var edits = findAll(tab.dom.flowBody, "dv-apm-edit");
out = { apm: findAll(tab.dom.flowBody, "dv-apm-map").length,
    basis: texts(tab.dom.flowBody, "dv-host-basis"),
    badges: findAll(tab.dom.flowBody, "dv-apm-map")
        .map(function (b) { return texts(b, "dv-badge"); }),
    targets: texts(tab.dom.flowBody, "dv-apm-target"),
    notes: findAll(tab.dom.flowBody, "dv-apm-map")
        .map(function (b) { return texts(b, "dv-note"); }),
    edit: edits.map(function (a) { return a.attrs.href; }),
    change: findAll(tab.dom.flowBody, "dv-host-change").length - edits.length,
    intro: findAll(tab.dom.flowBody, "dv-host-intro").length,
    called: calls.filter(function (u) { return u.indexOf("host-candidates") >= 0; }).length };
"""


def _linked(
    basis: str, *, targets: list[dict] | None = None, operator: bool = False, link_host: bool = True
) -> dict:
    sections = [{"step": "S4", "title": "호스트 자원", "status": "ok", "finding": "CPU 정상"}]
    scope: dict = {"polestar_basis": basis, "alarm_instance": "ib-was01"}
    if targets:
        scope["polestar_targets"] = targets
    final = _final({"playbook": {"id": "fallback", "sections": sections, "scope": scope}})
    return _node(_script(final, _PROBE, operator=operator, link_host=link_host))


def _skipped(reason: str, *, operator: bool = False) -> dict:
    sections = [{"step": "S4", "title": "호스트 자원", "status": "skipped", "reason": reason}]
    final = _final(
        {
            "playbook": {
                "id": "fallback",
                "sections": sections,
                "scope": {"alarm_instance": "ib-was01"},
            }
        }
    )
    return _node(_script(final, _PROBE, operator=operator))


# ─── 장애 진단 화면 — 연계 근거 ────────────────────────────────────────


def test_host_link_state_apm_map_sentences() -> None:
    """연계 근거 = linked · 연계 없음 = UI 없음 · 연계 미확정 = 선택 대기(D-348 그대로 · Q6)."""
    out = _node(f"""
var c = function (because) {{
    return {{ step: "S4", skipped: true, because: because, skipRaw: "" }}; }};
out = [
    DV.hostLinkState([], {{ polestar_basis: {json.dumps(_AUTO + _MULTI, ensure_ascii=False)} }}),
    DV.hostLinkState([c({json.dumps(_ABSENT, ensure_ascii=False)})], null),
    DV.hostLinkState([c({json.dumps(_UNRESOLVED, ensure_ascii=False)})], null),
];
""")
    assert out[0] == {"mode": "linked", "step": "", "basis": _AUTO + _MULTI}
    assert out[1] is None
    assert out[2] == {"mode": "wait", "step": "S4", "basis": ""}


def test_auto_basis_shows_tag_and_target_list_without_change_button() -> None:
    targets = [
        {"db_id": "polestar_cm_gp", "server_name": "kbl-a"},
        {"db_id": "polestar_b0", "server_name": "kbl-b", "hostname": "kbl-b.corp"},
    ]
    out = _linked(_AUTO + _MULTI, targets=targets)
    assert out["apm"] == 1
    assert out["basis"] == ["폴스타 서버 연결 — " + _AUTO + _MULTI + "자동 · 추정"]
    assert out["badges"] == [["자동 · 추정"]]
    assert out["targets"] == ["kbl-a (polestar_cm_gp)", "kbl-b (polestar_b0)"]
    # 일반 사용자 — 보기만(수정 링크·[연결 변경] 없음 · 후보 조회 없음)
    assert out["edit"] == [] and out["change"] == 0 and out["called"] == 0


def test_manual_basis_has_no_auto_tag_and_shows_without_link_host() -> None:
    out = _linked(_MANUAL, link_host=False)
    assert out["apm"] == 1 and out["badges"] == [[]] and out["targets"] == []
    assert out["basis"] == ["폴스타 서버 연결 — " + _MANUAL]


def test_admin_sees_edit_link_to_admin_tab() -> None:
    out = _linked(_MANUAL, operator=True)
    assert out["edit"] == ["/admin?apm_map_q=ib-was01#apmhostmap"]
    assert out["change"] == 0


def test_unresolved_card_shows_node_status_and_keeps_selection_ui() -> None:
    out = _skipped(_UNRESOLVED, operator=True)
    assert out["apm"] == 1
    assert out["notes"] == [["제니퍼 노드 주소 조회 비활성(게이트웨이 노드 주소 조회 꺼짐)"]]
    assert out["edit"] == ["/admin?apm_map_q=ib-was01#apmhostmap"]
    # D-348 선택 UI는 그대로 열린다(후보 조회 · 안내 문구)
    assert out["called"] == 1 and out["intro"] == 1


def test_absent_card_no_selection_ui_and_user_sees_nothing_extra() -> None:
    user = _skipped(_ABSENT)
    assert user["apm"] == 0 and user["called"] == 0 and user["intro"] == 0
    admin = _skipped(_ABSENT, operator=True)
    assert admin["apm"] == 1 and admin["edit"] == ["/admin?apm_map_q=ib-was01#apmhostmap"]
    assert admin["called"] == 0


def test_diagnosis_view_regex_literals_and_no_write_path() -> None:
    js = _DV_JS.read_text(encoding="utf-8")
    for name in ("HOST_APM_MAP_RE", "HOST_APM_SKIP_RE", "HOST_NODE_STATUS_RE"):
        m = re.search(rf"var {name} = /(.*?)/;", js)
        assert m is not None, name
        re.compile(m.group(1))
    assert "POST" not in js and 'host-link"' not in js
    # 새 상자 렌더는 textContent만(기존 공용 렌더의 innerHTML은 대상 밖)
    body = js[js.index("function apmMapNode(") : js.index("function hostNode(")]
    assert "innerHTML" not in body and "insertAdjacentHTML" not in body


# ─── 관리자 탭 — 정적 ──────────────────────────────────────────────────


def test_dashboard_tab_hidden_by_default_and_wired() -> None:
    dash = _DASH.read_text(encoding="utf-8")
    assert re.search(
        r'<button class="tab" data-tab="apmhostmap" hidden>APM·SMS 연계</button>', dash
    )
    assert 'id="tab-apmhostmap"' in dash and "admin-apm-host-map.js?v=3" in dash
    for el_id in (
        "apmMapBody",
        "apmMapEditor",
        "apmMapPreview",
        "apmScanStartBtn",
        "apmScanScannerBody",
        "apmScanNodeSourcesBody",
        "apmScanRunsBody",
        "apmInvKind",
        "apmInvBody",
        "apmInvUnmapped",
        "apmMapFilterReview",
    ):
        assert f'id="{el_id}"' in dash, el_id


def test_admin_js_text_only_and_reads_both_tokens() -> None:
    src = _AHM_JS.read_text(encoding="utf-8")
    for bad in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval("):
        assert bad not in src, bad
    assert 'TOKEN_KEYS = ["admin_token", "user_token"]' in src
    assert '"/api/v1/admin/apm-host-map"' in src


# ─── 관리자 탭 — node 실행 ─────────────────────────────────────────────

_AHM_HARNESS = r"""
function El(tag) { this.tagName = tag; this.children = []; this._t = ""; this.style = {};
  this.className = ""; this.listeners = {}; this.hidden = false; this.disabled = false;
  this.value = ""; this.checked = false; }
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.addEventListener = function (k, f) { this.listeners[k] = f; };
El.prototype.click = function () { if (this.listeners.click) this.listeners.click(); };
Object.defineProperty(El.prototype, "textContent", {
  get: function () { return this._t + this.children.map(function (c) { return c.textContent; })
    .join(""); },
  set: function (v) { this._t = String(v); this.children = []; } });
globalThis.Node = El;
var ids = {};
var tab = new El("button"); tab.hidden = true;
var ready = [];
globalThis.document = {
  getElementById: function (id) { if (!ids[id]) ids[id] = new El("div"); return ids[id]; },
  createElement: function (t) { return new El(t); },
  addEventListener: function (k, f) { if (k === "DOMContentLoaded") ready.push(f); },
  querySelector: function () { return tab; },
  querySelectorAll: function () { return [tab]; } };
var store = { user_token: "utok" };
globalThis.localStorage = { getItem: function (k) { return k in store ? store[k] : null; } };
globalThis.window = { confirm: function () { return true; },
  location: { search: __SEARCH__, hash: __HASH__ } };
var reqs = [];
var ROUTES = __ROUTES__;
globalThis.fetch = function (url, init) {
  reqs.push({ url: url, method: init.method, auth: init.headers.Authorization,
    body: init.body ? JSON.parse(init.body) : null });
  var path = url.split("?")[0];
  var hit = ROUTES[init.method + " " + path] || { status: 404, body: { detail: "Not Found" } };
  return Promise.resolve({ ok: hit.status < 300, status: hit.status,
    json: function () { return Promise.resolve(hit.body); } });
};
function find(n, tag, text) {
  var out = [];
  (function walk(x) { if (x.tagName === tag && (!text || x.textContent === text)) out.push(x);
    x.children.forEach(walk); })(n);
  return out;
}
function wait() { return new Promise(function (r) { setTimeout(r, 20); }); }
"""

_API = "/api/v1/admin/apm-host-map"
_ROW = {
    "id": 7,
    "source_id": "jen1",
    "domain_id": "1001",
    "domain_name": "인터넷뱅킹",
    "scope": "instance",
    "match_key": "ib-was01",
    "instance_example": "ib-was01",
    "origin": "auto",
    "basis": "vm_exact",
    "state": "active",
    "verified": False,
    "version": 3,
    "note": "",
    "targets": [{"db_id": "polestar_cm_gp", "server_name": "kbl-a", "ip": "10.0.0.7"}],
    "review_reasons": ["changed"],
    "updated_by": "system",
    "updated_at": "2026-11-01T00:00:00+00:00",
}
_RUN = {
    "run_id": "run-2",
    "status": "partial",
    "requested_by": "a",
    "started_at": "2026-11-01T00:00:00+00:00",
    "finished_at": "2026-11-01T00:01:05+00:00",
    "scanners": {
        "apm_inventory": {"status": "ok", "rows": 12, "seconds": 3},
        "node_address": {
            "status": "partial",
            "rows": 4,
            "seconds": 9,
            "sources": [{"source_id": "jen1", "status": "failed", "reason": "login_failed"}],
        },
        "dpm": {"status": "not_integrated"},
    },
    "link_summary": {
        "new": 2,
        "changed": 1,
        "ambiguous": 0,
        "failed": 1,
        "gone": 0,
        "kept_manual": 3,
        "errors": 0,
    },
}


def _admin(routes: dict, body: str, *, search: str = "", hash_: str = "") -> dict:
    return _node(
        "\n".join(
            [
                _AHM_HARNESS.replace("__ROUTES__", json.dumps(routes, ensure_ascii=False))
                .replace("__SEARCH__", json.dumps(search))
                .replace("__HASH__", json.dumps(hash_)),
                f"require({json.dumps(str(_AHM_JS))});",
                "ready.forEach(function (f) { f(); });",
                "await wait();",
                body,
            ]
        )
    )


def test_admin_tab_stays_hidden_when_feature_off() -> None:
    out = _admin({}, "out = { hidden: tab.hidden, reqs: reqs.length };")
    assert out == {"hidden": True, "reqs": 1}


def test_admin_tab_lists_maps_scan_and_inventory_from_diagnosis_link() -> None:
    routes = {
        f"GET {_API}": {
            "status": 200,
            "body": {"rows": [_ROW], "total": 1, "auto_enabled": True, "scan_available": True},
        },
        f"GET {_API}/scan/runs": {"status": 200, "body": {"runs": [_RUN], "scan_available": True}},
        f"GET {_API}/inventory/apm": {
            "status": 200,
            "body": {
                "run_id": "run-2",
                "has_more": False,
                "rows": [
                    {
                        "source_id": "jen1",
                        "domain_id": "1001",
                        "instance_name": "ib-was02",
                        "ip": "10.0.0.8",
                    }
                ],
            },
        },
    }
    out = _admin(
        routes,
        """
await wait();
out = { hidden: tab.hidden, first: reqs[0].url, auth: reqs[0].auth,
    query: ids.apmMapFilterQuery.value,
    map: ids.apmMapBody.textContent, scanners: ids.apmScanScannerBody.textContent,
    sources: ids.apmScanNodeSourcesBody.textContent, latest: ids.apmScanLatest.textContent,
    inv: ids.apmInvBody.textContent, scanDisabled: ids.apmScanStartBtn.disabled };
""",
        search="?apm_map_q=ib-was01",
        hash_="#apmhostmap",
    )
    assert out["hidden"] is False and out["auth"] == "Bearer utok"
    assert out["first"].startswith(_API + "?") and "q=ib-was01" in out["first"]
    assert out["query"] == "ib-was01"
    assert "자동 · 추정" in out["map"] and "변경됨" in out["map"] and "kbl-a" in out["map"]
    assert "DPM미연동" in out["scanners"] and "APM 인벤토리정상" in out["scanners"]
    assert "jen1failedlogin_failed" in out["sources"]
    assert "1분 5초" in out["latest"] and "관리자 행 유지 3" in out["latest"]
    assert "ib-was02" in out["inv"] and out["scanDisabled"] is False


def test_admin_bad_percent_encoding_in_link_does_not_stop_init() -> None:
    """L-5 — `apm_map_q`의 깨진 퍼센트 인코딩(URIError)은 무시하고 목록을 계속 불러온다."""
    routes = {
        f"GET {_API}": {
            "status": 200,
            "body": {"rows": [_ROW], "total": 1, "auto_enabled": True, "scan_available": False},
        },
    }
    out = _admin(
        routes,
        "out = { hidden: tab.hidden, query: ids.apmMapFilterQuery.value,"
        " map: ids.apmMapBody.textContent, first: reqs[0] && reqs[0].url };",
        search="?apm_map_q=%E0%A4%A",
    )
    assert out["hidden"] is False and out["query"] == ""
    assert out["first"].startswith(_API) and "ib-was01" in out["map"]


def test_admin_edit_save_sends_version_and_reloads_on_conflict() -> None:
    routes = {
        f"GET {_API}": {
            "status": 200,
            "body": {"rows": [_ROW], "total": 1, "auto_enabled": True, "scan_available": False},
        },
        f"PUT {_API}/7": {
            "status": 409,
            "body": {
                "detail": {"code": "version_conflict", "reason": "바뀜", "current_version": 4}
            },
        },
    }
    out = _admin(
        routes,
        """
find(ids.apmMapBody, "button", "수정")[0].click();
ids.apmMapEditState.value = "disabled";
ids.apmMapEditNote.value = "안 씀";
ids.apmMapEditSaveBtn.click();
await wait();
out = { put: reqs.filter(function (r) { return r.method === "PUT"; })[0],
    gets: reqs.filter(function (r) { return r.method === "GET"; }).length,
    notice: ids.apmMapNotice.textContent };
""",
    )
    assert out["put"]["url"] == _API + "/7"
    assert out["put"]["body"] == {
        "version": 3,
        "state": "disabled",
        "note": "안 씀",
        "targets": [{"db_id": "polestar_cm_gp", "server_name": "kbl-a", "hostname": ""}],
    }
    assert out["gets"] == 2 and "다른 변경이 먼저 저장" in out["notice"]
