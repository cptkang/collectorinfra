"""plans/198 W7 I-8 — 비슷한 과거 조사 API · KEDB 부트스트랩 · 화면 (D-350 ⑤).

- 플래그 off: `/similar` 404 · 서버 미등록 · 라벨 응답 키 종전과 같음 · KB 초안 0
- 유사 조사: 결론은 라벨 붙은 조사만(「확정 전」) · 사용자 뷰 결론 마스킹 · 조회 인가 밖 조사 제외
- KEDB: N-1회 미생성 · N회 `wip` 1건 · 같은 묶음 재라벨·N+1회에도 중복 없음 · KB 꺼짐이면 0
- 화면(diagnosis-view.js): `similar` 미주입이면 조회 0 · 404면 그리기 0 · 켜짐이면 날짜·결론·라벨 줄
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.incident_kb_store import IncidentKbStore
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import investigation_label as label_routes
from src.routing.zones import ZONE_GONGJON
from tests.test_api.test_ui_plan187_w15_diagnosis import _node
from tests.test_plan198_w0_label_api import _config as _w0_config

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"

GONGJON_DB = "polestar_cm_gp"
BANKJON_DB = "polestar_b0"
SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)
ADMIN = {"sub": "admin1", "role": "admin"}
GONG_USER = {"sub": "u9", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BASE = "/api/v1/alarm/investigations"


def _briefing() -> dict:
    return {
        "summary": "WAS 응답 지연",
        "cause": "가설 1",
        "root_cause_hypotheses": [
            {"rank": 1, "cause": "DB 지연 → 풀 고갈", "status": "supported",
             "reasoning": "H-DB-POOL-CHAIN"},
            {"rank": 2, "cause": "GC 과다", "status": "inconclusive", "reasoning": "H-GC"},
        ],
        "playbook": {
            "id": "was.latency",
            "readings": {"dominant": "sql"},
            "scope": {"alarm_instance": "shop01", "host": "web01",
                      "targets": [{"instance": "shop01", "source_id": "s1"}]},
            "synthesis": {"hypotheses": [{"rank": 1, "title": "DB 지연 → 풀 고갈"}]},
        },
    }


def _config(tmp_path, *, memory: bool = True, kb: bool = True, **over):  # noqa: ANN202
    return _w0_config(
        tmp_path, investigation_memory_enabled=memory, investigation_kedb_min_count=3,
        investigation_kb_enabled=kb, investigation_kb_store_path=str(tmp_path / "kb.jsonl"),
        investigation_kb_store_max_lines=20000, **over,
    )


def _client(config, user) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(label_routes.router, prefix="/api/v1")
    app.include_router(label_routes.memory_router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_admin_user] = lambda: user
    return TestClient(app)


def _seed(tmp_path, ids_dbs: list[tuple[str, str]]) -> None:
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    for iid, db in ids_dbs:
        store.record(
            investigation_id=iid, alarm_ids=[f"a-{iid}"], status="done", source="inline",
            kind="alarm", briefing=_briefing(), alarm_db_ids={f"a-{iid}": db},
        )


def _drafts(tmp_path) -> list[dict]:
    return IncidentKbStore(str(tmp_path / "kb.jsonl")).list_drafts()


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


# ── 플래그 off = 비트 동일 ────────────────────────────────────────────────
def test_flag_off_similar_404_and_label_response_unchanged(tmp_path, audits) -> None:
    _seed(tmp_path, [(f"i{n}", GONGJON_DB) for n in range(4)])
    client = _client(_config(tmp_path, memory=False), ADMIN)
    assert client.get(f"{BASE}/i0/similar").status_code == 404
    for n in range(4):
        res = client.post(f"{BASE}/i{n}/cause-label", json={"verdict": "top1"})
        assert res.status_code == 200
        assert set(res.json()) == {"recorded", "investigation_id", "verdict", "rank", "note", "ts"}
    assert _drafts(tmp_path) == []


def test_server_registration_and_defaults() -> None:
    src = (_ROOT / "src" / "api" / "server.py").read_text(encoding="utf-8")
    block = src[src.index("if config.noise_gate.investigation_memory_enabled:"):]
    assert "memory_router" in block[:300]
    from src.config.alarm import NoiseGateConfig

    fields = NoiseGateConfig.model_fields
    assert fields["investigation_memory_enabled"].default is False
    assert fields["investigation_kedb_min_count"].default == 3


# ── 유사 조사 ────────────────────────────────────────────────────────────
def test_similar_conclusion_only_when_labeled_and_masked(tmp_path, audits) -> None:
    _seed(tmp_path, [("t", GONGJON_DB), ("a", GONGJON_DB), ("b", GONGJON_DB),
                     ("bank", BANKJON_DB)])
    admin = _client(_config(tmp_path), ADMIN)
    assert admin.post(f"{BASE}/a/cause-label",
                      json={"verdict": "not_listed", "note": f"키 {SECRET}"}).status_code == 200
    body = admin.get(f"{BASE}/t/similar").json()
    by = {i["investigation_id"]: i for i in body["items"]}
    assert set(by) == {"a", "b", "bank"} and body["limit"] == 3 and "확정 전" in body["notice"]
    assert by["b"]["labeled"] is False and by["b"]["conclusion"] is None
    assert by["a"]["labeled"] is True and by["a"]["verdict"] == "not_listed"
    # 라벨 저장 때 이미 마스킹된 서술 — 원 비밀값은 어느 응답에도 없다
    assert SECRET not in json.dumps(body, ensure_ascii=False)

    user = _client(_config(tmp_path), GONG_USER)
    res = user.get(f"{BASE}/t/similar")
    assert res.status_code == 200
    ids = {i["investigation_id"] for i in res.json()["items"]}
    assert ids == {"a", "b"}  # 은행존 조사는 조회 인가 밖 — 빠진다


def test_similar_target_access_denied(tmp_path, audits) -> None:
    _seed(tmp_path, [("bank", BANKJON_DB), ("a", BANKJON_DB)])
    res = _client(_config(tmp_path), GONG_USER).get(f"{BASE}/bank/similar")
    assert res.status_code == 403
    assert _client(_config(tmp_path), GONG_USER).get(f"{BASE}/none/similar").status_code == 404


def test_similar_user_view_masks_conclusion(tmp_path, audits, monkeypatch) -> None:
    _seed(tmp_path, [("t", GONGJON_DB), ("a", GONGJON_DB)])
    labels = {"a": {"verdict": "not_listed", "rank": None, "note": f"키 {SECRET}"}}
    monkeypatch.setattr(label_routes, "_labels_or_empty", lambda config: labels)
    user = _client(_config(tmp_path), GONG_USER).get(f"{BASE}/t/similar").json()
    admin = _client(_config(tmp_path), ADMIN).get(f"{BASE}/t/similar").json()
    assert SECRET not in user["items"][0]["conclusion"]
    assert SECRET in admin["items"][0]["conclusion"]  # 운영자 뷰 원문(조사 뷰와 같은 규칙)


# ── KEDB 부트스트랩 ──────────────────────────────────────────────────────
def test_kedb_threshold_creates_one_wip_draft(tmp_path, audits) -> None:
    _seed(tmp_path, [(f"i{n}", GONGJON_DB) for n in range(5)])
    client = _client(_config(tmp_path), ADMIN)
    for n in range(2):
        res = client.post(f"{BASE}/i{n}/cause-label", json={"verdict": "top1"}).json()
        assert "kedb_draft" not in res
    assert _drafts(tmp_path) == []  # N-1회
    res = client.post(f"{BASE}/i2/cause-label", json={"verdict": "top1"}).json()
    assert res["kedb_draft"]["state"] == "wip" and res["kedb_draft"]["count"] == 3
    drafts = _drafts(tmp_path)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft["ke"]["state"] == "wip" and draft["created_by"] == "kedb_bootstrap"
    assert draft["ke"]["cause"].startswith("DB 지연 → 풀 고갈 (운영 라벨 3회 확정")
    assert draft["cause_source"] == "label"
    assert draft["bootstrap"]["investigation_ids"] == ["i0", "i1", "i2"]
    assert draft["bootstrap"]["triggered_by"] == "admin1"
    assert any(a.get("action") == "kb_draft_bootstrap" for a in audits)
    # 재라벨 · N+1회 — 중복 없음
    for iid in ("i2", "i3"):
        res = client.post(f"{BASE}/{iid}/cause-label", json={"verdict": "top1"}).json()
        assert "kedb_draft" not in res
    assert len(_drafts(tmp_path)) == 1


def test_kedb_skipped_when_kb_off(tmp_path, audits) -> None:
    _seed(tmp_path, [(f"i{n}", GONGJON_DB) for n in range(3)])
    client = _client(_config(tmp_path, kb=False), ADMIN)
    for n in range(3):
        res = client.post(f"{BASE}/i{n}/cause-label", json={"verdict": "top1"}).json()
        assert res["recorded"] is True and "kedb_draft" not in res
    assert _drafts(tmp_path) == []


def test_kedb_failure_does_not_break_label(tmp_path, audits, monkeypatch) -> None:
    _seed(tmp_path, [("i0", GONGJON_DB)])

    def _boom(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise RuntimeError("boom")

    monkeypatch.setattr(label_routes, "kedb_candidate", _boom)
    res = _client(_config(tmp_path), ADMIN).post(f"{BASE}/i0/cause-label",
                                                 json={"verdict": "top1"})
    assert res.status_code == 200 and "kedb_draft" not in res.json()


# ── 화면(diagnosis-view.js) ─────────────────────────────────────────────
def _final_view() -> dict:
    return {"status": "done", "investigation_id": "inv-f", "kind": "manual", "briefing_lines": [],
            "briefing": {"playbook": {"id": "fallback", "sections": []}},
            "activity": {"next_seq": 9, "truncated": False, "events": []}}


_SIMILAR_200 = (
    'routes[API + "/inv-f/similar"] = { status: 200, body: { investigation_id: "inv-f", '
    'limit: 3, notice: "n", items: ['
    '{ investigation_id: "a", ts: "2026-10-20T10:00:00", labeled: true, verdict: "top1", '
    'rank: 1, conclusion: "DB 지연" },'
    '{ investigation_id: "b", ts: "2026-10-21T10:00:00", labeled: false, verdict: null, '
    'rank: null, conclusion: null },'
    '{ investigation_id: "c", ts: "2026-10-22T10:00:00", labeled: true, '
    'verdict: "undeterminable", rank: null, conclusion: null }] } };'
)
_OUT = (
    "var n = calls.filter(function (u) { return u.indexOf('/similar') >= 0; }).length;\n"
    "var rows = tab.dom.similar ? tab.dom.similar.children[1].children.map("
    "function (r) { return r.textContent; }) : null;\n"
    "out = { calls: n, similar: !!tab.dom.similar, rows: rows };"
)


def _dv_script(route: str, inject: bool | str) -> str:
    """`inject` — True면 `similar: true`, 문자열이면 그 옵션 원문(기능 조회 함수 등).

    False면 미주입.
    """
    opt = inject if isinstance(inject, str) else ("similar: true," if inject else "")
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(_final_view(), ensure_ascii=False)} }};
{route}
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }},
    onActivate: function () {{}}, onOpenConversation: function () {{}},
    analyzeFor: function () {{ return null; }},
    {opt}
    deps: {{ document: document, fetch: fakeFetch, EventSource: FakeES, setTimeout: setT,
        clearTimeout: clearT, now: function () {{ return clock; }}, storage: storage }} }});
await ctl.probe();
runTimers(1000);
ctl.open("inv-f");
await flush();
runTimers(1000);
var tab = ctl.tab("inv-f");
await flush();
await flush();
{_OUT}
"""


def test_dv_similar_not_injected_means_no_fetch() -> None:
    out = _node(_dv_script(_SIMILAR_200, False))
    assert out == {"calls": 0, "similar": False, "rows": None}


def test_dv_similar_404_means_no_dom() -> None:
    out = _node(_dv_script("", True))
    assert out == {"calls": 1, "similar": False, "rows": None}


def test_dv_similar_capability_off_means_no_fetch() -> None:
    """(교정) app.js가 넘기는 기능 조회 값이 거짓이면 GET 자체가 없다(404 왕복 0)."""
    out = _node(_dv_script(_SIMILAR_200, "similar: function () { return false; },"))
    assert out == {"calls": 0, "similar": False, "rows": None}
    on = _node(_dv_script(_SIMILAR_200, "similar: function () { return true; },"))
    assert on["calls"] == 1 and on["similar"] is True


def test_dv_cause_then_similar_order_is_fixed() -> None:
    """(교정) 「실제 원인」 응답이 늦게 와도 두 상자는 실제 원인 → 비슷한 과거 조사 순이다."""
    label = ('{ investigation_id: "inv-f", labelable: true, hypothesis_ranks: [1], '
             'verdicts: ["top1", "undeterminable"], label: null }')
    route = _SIMILAR_200 + (
        'routes[API + "/inv-f/cause-label"] = { status: 200, body: new Promise(function (r) { '
        'setTimeout(function () { r(' + label + '); }, 5); }) };'
    )
    script = _dv_script(route, "similar: true, saveCauseLabel: function () {"
                               " return Promise.resolve({ ok: true, body: {} }); },")
    script = script.replace(_OUT, (
        "await new Promise(function (r) { setTimeout(r, 20); });\nawait flush();\n"
        "var kids = tab.dom.finalBox.children;\n"
        "out = { last2: [kids[kids.length - 2] === tab.dom.cause, "
        "kids[kids.length - 1] === tab.dom.similar] };"
    ))
    assert _node(script) == {"last2": [True, True]}


def test_dv_similar_rows() -> None:
    out = _node(_dv_script(_SIMILAR_200, True))
    assert out["calls"] == 1 and out["similar"] is True
    assert out["rows"] == [
        "2026-10-20 · 결론 DB 지연 · 실제 원인 1순위 가설 맞음",
        "2026-10-21 · 확정 전",
        "2026-10-22 · 실제 원인 판단 불가",
    ]


def test_static_wiring() -> None:
    app_js = (_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    # (교정) 기능 조회 값으로 켠다 — 꺼져 있으면 GET이 나가지 않는다
    assert "alarmCapabilities.investigation_memory_enabled === true" in app_js
    assert "alarmCapabilities.investigation_label_enabled === true" in app_js
    css = (_STATIC / "css" / "style.css").read_text(encoding="utf-8")
    assert ".dv-similar {" in css
