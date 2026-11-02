"""plans/198 W0 I-1 — 조사 「실제 원인」 라벨 API · 관리자 조사 정확도 주간 표 (D-350 ②).

- 플래그 off: 네 라우트 404(인증보다 먼저 · 관리자 포함) · 피드백 파일 기록 0 · 서버 미등록
- 쓰기 인가 표: 사용자 유형(소유자 · 비소유자 제한 존 · 비소유자 전 존 · 관리자) × kind
  (alarm · episode · diagnosis · manual) — 소유자 없는 레코드는 관리자만(fail-closed)
- 라벨: 완료 조사만(409) · 순위 검증(400) · 자유 서술 정리·상한·마스킹 · 최신 1건 · 감사
- 주간 표 API: 관리자만(role 클레임 명시 검증) · 지표 값 · 후보 내보내기 메타만(D-308)
- 화면: diagnosis-view.js — 쓰기 경로 미주입·404면 조회·그리기 0(비트 동일) · 켜짐이면 1줄 입력
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import investigation_label as label_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON, all_zones
from tests.test_api.test_plan187_w10_followup import _config as _w10_config
from tests.test_api.test_ui_plan187_w15_diagnosis import _node

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "static"

GONGJON_DB = "polestar_cm_gp"
SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)
ADMIN = {"sub": "admin1", "role": "admin"}
OWNER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
OTHER_ZONE = {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]}
ALL_ZONES = {"sub": "u3", "role": "user", "alarm_zones": list(all_zones())}
KINDS = ("alarm", "episode", "diagnosis", "manual")
BASE = "/api/v1/alarm/investigations"


def _briefing(n: int = 3) -> dict:
    return {
        "summary": "WAS 응답 지연",
        "cause": "가설 1",
        "root_cause_hypotheses": [
            {"rank": i, "cause": f"가설 {i}", "confidence": "high"} for i in range(1, n + 1)
        ],
        "playbook": {"id": "was.latency", "synthesis": {"hypotheses": []}},
    }


def _config(tmp_path, *, label: bool = True, **ng_over):  # noqa: ANN202
    config = _w10_config(tmp_path, enabled=True)
    ng = config.noise_gate
    ng.investigation_label_enabled = label
    ng.investigation_manual_enabled = True
    ng.feedback_store_path = str(tmp_path / "fb.jsonl")
    ng.feedback_store_enabled = True
    ng.feedback_store_max_lines = 20000
    ng.investigation_kb_scan_max_lines = 10000
    for key, value in ng_over.items():
        setattr(ng, key, value)
    return config


def _client(config, user) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(label_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_admin_user] = lambda: user
    return TestClient(app)


def _seed(tmp_path, *, status: str = "done") -> None:
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    for kind in KINDS:
        alarm_ids = [] if kind == "diagnosis" else ["a1"]
        extra = {}
        if kind in ("diagnosis", "manual"):
            extra["owner_sub"] = OWNER["sub"]
        if kind == "manual":
            extra["parent_alarm_id"] = "a1"
        store.record(
            investigation_id=f"inv-{kind}", alarm_ids=alarm_ids, status=status, source="inline",
            kind=kind, briefing=_briefing(), alarm_db_ids={"a1": GONGJON_DB} if alarm_ids else None,
            **extra,
        )


def _fb_rows(tmp_path) -> list[dict]:
    path = tmp_path / "fb.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


# ── 플래그 off = 비트 동일 ────────────────────────────────────────────────
def test_flag_off_all_routes_404_and_nothing_written(tmp_path, audits) -> None:
    _seed(tmp_path)
    client = _client(_config(tmp_path, label=False), ADMIN)
    assert client.get(f"{BASE}/inv-alarm/cause-label").status_code == 404
    assert client.post(f"{BASE}/inv-alarm/cause-label", json={"verdict": "top1"}).status_code == 404
    assert client.get("/api/v1/admin/investigation/accuracy").status_code == 404
    assert client.get("/api/v1/admin/investigation/accuracy/candidates").status_code == 404
    assert _fb_rows(tmp_path) == [] and audits == []


def test_flag_off_server_does_not_register_router() -> None:
    src = (_ROOT / "src" / "api" / "server.py").read_text(encoding="utf-8")
    block = src[src.index("if config.noise_gate.investigation_label_enabled:"):]
    assert "investigation_label.router" in block[:300]
    from src.config.alarm import NoiseGateConfig

    assert NoiseGateConfig.model_fields["investigation_label_enabled"].default is False


# ── 쓰기 인가 표 ─────────────────────────────────────────────────────────
_MATRIX = {
    # 사용자 유형: POST (alarm, episode, diagnosis, manual) · GET 상태 · GET labelable
    # GET은 조회 권한만 보고, 입력 권한이 없으면 거부 감사 없이 200 labelable=false
    # (plans/198 교정 m2)
    "owner": (OWNER, (403, 403, 200, 200), (200, 200, 200, 200), (False, False, True, True)),
    "other_zone": (OTHER_ZONE, (403, 403, 403, 403), (403, 403, 403, 403), None),
    "all_zones": (ALL_ZONES, (403, 403, 403, 403), (200, 200, 403, 200),
                  (False, False, None, False)),
    "admin": (ADMIN, (200, 200, 200, 200), (200, 200, 200, 200), (True, True, True, True)),
}


@pytest.mark.parametrize("who", list(_MATRIX))
def test_write_authorization_matrix(tmp_path, audits, who) -> None:
    _seed(tmp_path)
    user, expected, expected_gets, expected_labelable = _MATRIX[who]
    client = _client(_config(tmp_path), user)
    got = tuple(
        client.post(f"{BASE}/inv-{kind}/cause-label", json={"verdict": "top1"}).status_code
        for kind in KINDS
    )
    assert got == expected
    res = [client.get(f"{BASE}/inv-{kind}/cause-label") for kind in KINDS]
    assert tuple(r.status_code for r in res) == expected_gets
    if expected_labelable is not None:
        assert tuple(
            r.json()["labelable"] if r.status_code == 200 else None for r in res
        ) == expected_labelable
    assert len(_fb_rows(tmp_path)) == expected.count(200)


def test_unknown_investigation_404(tmp_path, audits) -> None:
    _seed(tmp_path)
    client = _client(_config(tmp_path), ADMIN)
    res = client.post(f"{BASE}/nope/cause-label", json={"verdict": "top1"})
    assert res.status_code == 404


# ── 라벨 입력 ───────────────────────────────────────────────────────────
def test_label_roundtrip_latest_wins_and_audited(tmp_path, audits) -> None:
    _seed(tmp_path)
    client = _client(_config(tmp_path), OWNER)
    url = f"{BASE}/inv-manual/cause-label"
    first = client.get(url).json()
    assert first == {"investigation_id": "inv-manual", "labelable": True,
                     "hypothesis_ranks": [1, 2, 3],
                     "verdicts": ["top1", "ranked", "not_listed", "undeterminable"], "label": None,
                     "note_max_chars": 200}
    assert client.post(url, json={"verdict": "ranked", "rank": 2}).json()["rank"] == 2
    res = client.post(url, json={"verdict": "not_listed", "note": f"디스크\n장애 {SECRET}"})
    assert res.status_code == 200
    label = client.get(url).json()["label"]
    assert label["verdict"] == "not_listed" and label["rank"] is None
    assert SECRET not in label["note"] and "\n" not in label["note"]
    assert "디스크 장애" in label["note"]
    row = _fb_rows(tmp_path)[-1]
    assert row["label"] == "investigation_cause" and row["investigation_id"] == "inv-manual"
    assert row["labeled_by"] == "u1" and row["kind"] == "manual"
    assert [a["outcome"] for a in audits] == ["ok", "ok"]
    assert all(a["entry_point"] == "investigation_label" for a in audits)


@pytest.mark.parametrize(("body", "code"), [
    ({"verdict": "ranked", "rank": 1}, "invalid_label"),
    ({"verdict": "ranked", "rank": 5}, "invalid_label"),
    ({"verdict": "ranked"}, "invalid_label"),
    ({"verdict": "valid"}, "invalid_label"),
    ({"verdict": "not_listed", "note": "가" * 201}, "invalid_note"),
])
def test_label_validation_400(tmp_path, audits, body, code) -> None:
    _seed(tmp_path)
    res = _client(_config(tmp_path), ADMIN).post(f"{BASE}/inv-alarm/cause-label", json=body)
    assert res.status_code == 400 and res.json()["detail"]["code"] == code
    assert _fb_rows(tmp_path) == []
    assert audits[-1]["outcome"] == "denied"


def test_not_done_409_and_not_labelable(tmp_path, audits) -> None:
    _seed(tmp_path, status="running")
    client = _client(_config(tmp_path), ADMIN)
    assert client.get(f"{BASE}/inv-alarm/cause-label").json()["labelable"] is False
    res = client.post(f"{BASE}/inv-alarm/cause-label", json={"verdict": "top1"})
    assert res.status_code == 409 and res.json()["detail"]["code"] == "not_done"
    assert _fb_rows(tmp_path) == []


def test_feedback_store_disabled_503(tmp_path, audits) -> None:
    _seed(tmp_path)
    client = _client(_config(tmp_path, feedback_store_enabled=False), ADMIN)
    res = client.post(f"{BASE}/inv-alarm/cause-label", json={"verdict": "top1"})
    assert res.status_code == 503 and res.json()["detail"]["code"] == "feedback_store_disabled"


# ── 주간 표 · 후보 내보내기 ─────────────────────────────────────────────
def test_accuracy_report_admin_only(tmp_path, audits) -> None:
    _seed(tmp_path)
    for user in (OWNER, ALL_ZONES):
        client = _client(_config(tmp_path), user)
        assert client.get("/api/v1/admin/investigation/accuracy").status_code == 403
        assert client.get("/api/v1/admin/investigation/accuracy/candidates").status_code == 403


def test_accuracy_report_values(tmp_path, audits) -> None:
    _seed(tmp_path)
    client = _client(_config(tmp_path), ADMIN)
    client.post(f"{BASE}/inv-alarm/cause-label", json={"verdict": "top1"})
    client.post(f"{BASE}/inv-episode/cause-label", json={"verdict": "ranked", "rank": 3})
    client.post(f"{BASE}/inv-diagnosis/cause-label", json={"verdict": "undeterminable"})
    res = client.get("/api/v1/admin/investigation/accuracy?weeks=4")
    assert res.status_code == 200
    data = res.json()
    assert len(data["weeks"]) == 4 and data["window"]["weeks"] == 4
    total = data["total"]
    assert (total["done"], total["labeled"], total["judged"]) == (4, 3, 2)
    assert total["label_rate"] == 0.75
    assert total["rc_at_1"] == 0.5 and total["rc_at_3"] == 1.0 and total["candidate_recall"] == 1.0
    assert data["weeks"][0]["done"] == 4  # 방금 기록 = 이번 주
    assert set(data["by_engine"]) == {"playbook", "holmesgpt", "parallel", "none"}
    assert set(data["by_kind"]) == set(KINDS)
    assert data["scan_max_lines"] == 10000
    assert client.get("/api/v1/admin/investigation/accuracy?weeks=0").status_code == 422


def test_candidates_export_meta_only(tmp_path, audits) -> None:
    _seed(tmp_path)
    client = _client(_config(tmp_path), ADMIN)
    client.post(f"{BASE}/inv-alarm/cause-label",
                json={"verdict": "not_listed", "note": "원인 메모"})
    client.post(f"{BASE}/inv-manual/cause-label", json={"verdict": "undeterminable"})
    data = client.get("/api/v1/admin/investigation/accuracy/candidates").json()
    assert data["count"] == 1
    item = data["items"][0]
    assert item["investigation_id"] == "inv-alarm" and item["engine"] == "playbook"
    assert item["label"] == {"verdict": "not_listed", "rank": None, "has_note": True}
    text = json.dumps(data, ensure_ascii=False)
    assert "원인 메모" not in text and "가설 1" not in text and "WAS 응답 지연" not in text


# ── 설정 도움말 등록 ────────────────────────────────────────────────────
def test_settings_help_and_catalog_registered() -> None:
    from src.api.settings_catalog import field_index
    from src.api.settings_help import curated_index, reset_help_cache

    reset_help_cache()
    assert "NOISE_INVESTIGATION_LABEL_ENABLED" in field_index()
    assert "NOISE_INVESTIGATION_LABEL_ENABLED" in curated_index()


# ── 화면(diagnosis-view.js) ─────────────────────────────────────────────
def _final_view() -> dict:
    return {"status": "done", "investigation_id": "inv-f", "kind": "manual", "briefing_lines": [],
            "briefing": {"playbook": {"id": "fallback", "sections": []}},
            "activity": {"next_seq": 9, "truncated": False, "events": []}}


_SAVE = (
    "saveCauseLabel: function (id, b) { saves.push({ id: id, body: b }); "
    "return Promise.resolve({ ok: true, status: 200, "
    "body: { verdict: b.verdict, rank: b.rank || null, note: b.note || '' } }); },"
)


def _dv_script(label_route: str, inject: bool, body: str) -> str:
    return f"""
routes[API + "/active"] = {{ status: 200, body: {{ items: [] }} }};
routes[API + "/inv-f"] = {{ status: 200, body: {json.dumps(_final_view(), ensure_ascii=False)} }};
{label_route}
var saves = [];
var rootEl = new Node("div");
var ctl = DV.create({{ root: rootEl, tabButton: new Node("button"), badge: new Node("span"),
    operator: false, headers: function () {{ return {{}}; }},
    onActivate: function () {{}}, onOpenConversation: function () {{}},
    analyzeFor: function () {{ return null; }},
    {_SAVE if inject else ""}
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
{body}
"""


_LABEL_200 = (
    'routes[API + "/inv-f/cause-label"] = { status: 200, body: { investigation_id: "inv-f", '
    'labelable: true, hypothesis_ranks: [1, 2, 3], '
    'verdicts: ["top1", "ranked", "not_listed", "undeterminable"], '
    'label: { verdict: "ranked", rank: 2, note: "", ts: "x" } } };'
)
_COUNT = (
    "var n = calls.filter(function (u) { return u.indexOf('cause-label') >= 0; }).length;\n"
    "out = { calls: n, cause: !!tab.dom.cause, kids: tab.dom.finalBox.children.length };"
)


def test_dv_no_injection_means_no_fetch_and_no_dom() -> None:
    out = _node(_dv_script(_LABEL_200, False, _COUNT))
    assert out == {"calls": 0, "cause": False, "kids": out["kids"]}


def test_dv_404_means_no_dom() -> None:
    off = _node(_dv_script("", True, _COUNT))
    on = _node(_dv_script(_LABEL_200, True, _COUNT))
    assert off["calls"] == 1 and off["cause"] is False
    assert on["cause"] is True and on["kids"] == off["kids"] + 1


def test_dv_capability_off_means_no_fetch() -> None:
    """(교정) `causeLabel` 옵션(기능 조회 값)이 거짓이면 쓰기 경로가 있어도 GET이 없다."""
    script = _dv_script(_LABEL_200, True, _COUNT).replace(
        "saveCauseLabel:", "causeLabel: function () { return false; }, saveCauseLabel:")
    out = _node(script)
    assert out["calls"] == 0 and out["cause"] is False


def test_dv_label_line_saves_selection() -> None:
    out = _node(_dv_script(_LABEL_200, True, """
var box = tab.dom.cause;
var verdict = box.children[1], rank = box.children[2], note = box.children[3];
var save = box.children[4], msg = box.children[5];
var opts = verdict.children.map(function (o) { return o.value; });
var before = msg.textContent;
var rankHiddenTop = rank.hidden;
verdict.value = "ranked";
(verdict.listeners.change || []).forEach(function (f) { f({}); });
rank.value = "3";
(save.listeners.click || []).forEach(function (f) { f({}); });
await flush();
await flush();
out = { opts: opts, before: before, rankHiddenTop: rankHiddenTop, rankHidden: rank.hidden,
    noteHidden: note.hidden, saves: saves, after: msg.textContent,
    text: DV.causeLabelText({ verdict: "not_listed", note: "디스크" }) };
"""))
    assert out["opts"] == ["", "top1", "ranked", "not_listed", "undeterminable"]
    assert out["before"] == "입력됨 — 2순위 가설 맞음"
    assert out["rankHiddenTop"] is True and out["rankHidden"] is False and out["noteHidden"] is True
    assert out["saves"] == [{"id": "inv-f", "body": {"verdict": "ranked", "rank": 3}}]
    assert out["after"] == "저장했습니다 — 3순위 가설 맞음"
    assert out["text"] == "목록에 없음 · 디스크"


def test_static_wiring() -> None:
    app_js = (_STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert '"/cause-label", "POST", body' in app_js
    dash = (_STATIC / "admin" / "dashboard.html").read_text(encoding="utf-8")
    assert '<button class="tab" data-tab="invaccuracy" hidden>' in dash
    assert 'id="tab-invaccuracy"' in dash
    assert '<script src="/static/js/admin-investigation-accuracy.js?v=1"></script>' in dash
    js = (_STATIC / "js" / "admin-investigation-accuracy.js").read_text(encoding="utf-8")
    assert "res.status === 404 && first" in js and "innerHTML" not in js
