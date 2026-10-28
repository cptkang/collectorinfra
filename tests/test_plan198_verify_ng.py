"""plans/198 W0·W7·W8 독립 검증(verifier) — 구현 테스트가 덮지 않은 빈 곳.

결함 재현은 `xfail(strict=True)`로 두었다가 교정 후 표지를 지웠다(V1·V2·V3).
현행 동작 기록(characterization)은 통과 테스트로 두고 이름에 `characterize`를 붙인다 —
교정한 것(V3·V4·V5·V6)은 교정 뒤 동작으로 단언을 바꿨다.

- V1 유사 조사 후보 인가가 연결 행(`episode_existing`)의 알람을 빼고 판정 — 조사 열람 규칙과 비대칭
- V2 라벨 저장 디스크 실패가 `recorded: true`로 응답 — 침묵 폴백
- V3 철회 tombstone 라우트(`/alarm/feedback/retract`)로 비소유자가 조사 라벨을 지울 수 있다
- V4 비운영자(소유자) 라벨이 KB 초안을 만든다 — 기존 KB 초안 라우트는 운영자 전용
- V5 비소유자가 완료 조사를 볼 때마다 cause-label GET이 거부 감사를 남긴다
- V6 154 후보 내보내기 감사 없음
- V7 화면 신규 코드는 innerHTML을 쓰지 않는다(XSS)
- V8 사건 단위 합류는 inline 모드(후속 off)에서 구조적으로 일어나지 않는다
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.feedback_store import FeedbackStore
from noise_gate.infrastructure.incident_kb_store import IncidentKbStore
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import alarm as alarm_routes
from src.api.routes import investigation_label as label_routes
from src.routing.zones import ZONE_GONGJON
from tests.test_plan198_w0_label_api import _config as _w0_config

_ROOT = Path(__file__).resolve().parents[1]
GONGJON_DB = "polestar_cm_gp"
ADMIN = {"sub": "admin1", "role": "admin"}
OWNER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
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


def _config(tmp_path, **over):  # noqa: ANN202
    base = dict(
        investigation_memory_enabled=True, investigation_kedb_min_count=3,
        investigation_kb_enabled=True, investigation_kb_store_path=str(tmp_path / "kb.jsonl"),
        investigation_kb_store_max_lines=20000,
    )
    base.update(over)
    return _w0_config(tmp_path, **base)


def _client(config, user, *, alarm: bool = False) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    if alarm:
        app.include_router(alarm_routes.router, prefix="/api/v1")
    app.include_router(label_routes.router, prefix="/api/v1")
    app.include_router(label_routes.memory_router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_admin_user] = lambda: user
    return TestClient(app)


def _store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


def _request(config) -> Request:  # noqa: ANN001
    app = FastAPI()
    app.state.config = config
    return Request({"type": "http", "app": app, "headers": []})


# ── V1 유사 조사 후보 인가 — 연결 행 알람 누락 ─────────────────────────────────
def test_v1_similar_candidate_authz_matches_direct_view(tmp_path, audits) -> None:
    store = _store(tmp_path)
    for iid in ("t", "x"):
        store.record(
            investigation_id=iid, alarm_ids=[f"a-{iid}"], status="done", source="inline",
            kind="alarm", briefing=_briefing(), alarm_db_ids={f"a-{iid}": GONGJON_DB},
        )
    # x에 존을 알 수 없는 알람(제니퍼 등 · 판정 기록 없음)이 연결 행으로 붙는다
    store.record(investigation_id="x", alarm_ids=["j-unknown"], status="episode_existing",
                 source="inline")
    config = _config(tmp_path)
    merged = store.get_by_investigation("x")
    assert "j-unknown" in merged["alarm_ids"]
    # 조사 열람 규칙: 제한 존 사용자는 x를 볼 수 없다(fail-closed · plans/153 W2 H1)
    with pytest.raises(HTTPException):
        alarm_routes._assert_investigation_access(_request(config), GONG_USER, merged)
    res = _client(config, GONG_USER).get(f"{BASE}/t/similar")
    assert res.status_code == 200
    ids = {i["investigation_id"] for i in res.json()["items"]}
    assert "x" not in ids


# ── V2 라벨 저장 실패 침묵 ──────────────────────────────────────────────────
def test_v2_label_disk_failure_is_not_reported_recorded(tmp_path, audits) -> None:
    store = _store(tmp_path)
    store.record(investigation_id="i1", alarm_ids=["a1"], status="done", source="inline",
                 kind="alarm", briefing=_briefing(), alarm_db_ids={"a1": GONGJON_DB})
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    config = _config(tmp_path, investigation_memory_enabled=False,
                     feedback_store_path=str(blocker / "fb.jsonl"))
    res = _client(config, ADMIN).post(f"{BASE}/i1/cause-label", json={"verdict": "top1"})
    assert not (res.status_code == 200 and res.json().get("recorded") is True)
    assert res.status_code == 503
    assert res.json()["detail"]["code"] == "store_write_failed"
    assert [a.get("reason") for a in audits] == ["store_write_failed"]


# ── V3 철회 라우트로 비소유자가 조사 라벨을 지운다 ───────────────────────────
def test_v3_non_owner_cannot_retract_investigation_label(tmp_path, audits) -> None:
    store = _store(tmp_path)
    store.record(investigation_id="m1", alarm_ids=["a1"], status="done", source="inline",
                 kind="manual", briefing=_briefing(), alarm_db_ids={"a1": GONGJON_DB},
                 owner_sub=OWNER["sub"], parent_alarm_id="a1")
    config = _config(tmp_path, investigation_memory_enabled=False)
    config.noise_gate.enable_noise_gate = True
    config.noise_gate.enable_llm_actionability = True
    owner = _client(config, OWNER, alarm=True)
    res = owner.post(f"{BASE}/m1/cause-label", json={"verdict": "top1"})
    assert res.status_code == 200
    ts = res.json()["ts"]
    other = _client(config, GONG_USER, alarm=True)
    r = other.post("/api/v1/alarm/feedback/retract",
                   json={"target_ts": ts, "alarm_name": "x"})
    assert r.status_code == 200
    assert owner.get(f"{BASE}/m1/cause-label").json()["label"] is not None


def test_v3_characterize_store_tombstone_only_author_or_operator(tmp_path) -> None:
    fb = FeedbackStore(str(tmp_path / "fb.jsonl"))
    from datetime import UTC, datetime, timedelta

    when = datetime(2026, 10, 28, 1, 2, 3, 456789, tzinfo=UTC)
    assert fb.record_investigation_label(investigation_id="i1", verdict="top1", rank=1,
                                         labeled_by="u1", ts=when)
    assert "i1" in fb.investigation_labels()
    # 작성자·운영자가 아닌 철회는 조사 라벨에 효력이 없다(교정 m1)
    fb.record_retract(target_ts=when.isoformat(), alarm_name="", labeled_by="someone-else")
    assert "i1" in fb.investigation_labels()
    # 원 작성자의 철회는 인정
    fb.record_retract(target_ts=when.isoformat(), labeled_by="u1")
    assert fb.investigation_labels() == {}
    # 운영자 철회도 인정
    later = when + timedelta(minutes=1)
    fb.record_investigation_label(investigation_id="i2", verdict="top1", rank=1,
                                  labeled_by="u1", ts=later)
    fb.record_retract(target_ts=later.isoformat(), labeled_by="admin1", operator=True)
    assert fb.investigation_labels() == {}


def test_v3_alarm_feedback_retract_unchanged(tmp_path) -> None:
    """알람 피드백 철회는 종전 규약 그대로 — 작성자와 무관하게 target_ts로 빠진다."""
    fb = FeedbackStore(str(tmp_path / "fb.jsonl"))
    from datetime import UTC, datetime

    when = datetime(2026, 10, 28, 1, tzinfo=UTC)
    fb.record_feedback(label="valid", alarm_name="CPU", labeled_by="u1", ts=when)
    fb.record_retract(target_ts=when.isoformat(), labeled_by="someone-else")
    assert fb.find_similar(alarm_name="CPU") == []
    rows = [json.loads(line) for line in (tmp_path / "fb.jsonl").read_text().splitlines()]
    assert "operator" not in rows[-1]


# ── V4 비운영자 라벨은 KB 초안을 만들지 않는다(교정 M3 — 운영자 라벨만 트리거·계수) ──────
def test_v4_characterize_non_operator_owner_label_creates_no_kb_draft(tmp_path, audits) -> None:
    store = _store(tmp_path)
    for n in range(3):
        store.record(
            investigation_id=f"m{n}", alarm_ids=[f"a{n}"], status="done", source="inline",
            kind="manual", briefing=_briefing(), alarm_db_ids={f"a{n}": GONGJON_DB},
            owner_sub=OWNER["sub"], parent_alarm_id=f"a{n}",
        )
    config = _config(tmp_path, investigation_label_enabled=True)
    owner = _client(config, OWNER)
    outs = [owner.post(f"{BASE}/m{n}/cause-label", json={"verdict": "top1"}) for n in range(3)]
    assert all(r.status_code == 200 for r in outs)
    # 기존 KB 초안 라우트(create_kb_draft)와 같이 운영자만 — 소유자 라벨은 초안 0건
    assert all("kedb_draft" not in r.json() for r in outs)
    assert IncidentKbStore(str(tmp_path / "kb.jsonl")).list_drafts() == []
    # 소유자 라벨은 문턱 계수에도 들지 않는다 — 관리자가 1건만 더 달아도 초안이 생기지 않는다
    admin = _client(config, ADMIN)
    res = admin.post(f"{BASE}/m0/cause-label", json={"verdict": "top1"}).json()
    assert "kedb_draft" not in res
    assert IncidentKbStore(str(tmp_path / "kb.jsonl")).list_drafts() == []
    for n in (1, 2):
        res = admin.post(f"{BASE}/m{n}/cause-label", json={"verdict": "top1"}).json()
    assert res["kedb_draft"]["count"] == 3
    drafts = IncidentKbStore(str(tmp_path / "kb.jsonl")).list_drafts()
    assert len(drafts) == 1 and drafts[0]["bootstrap"]["triggered_by"] == ADMIN["sub"]


# ── V5 비소유자 조회는 거부 감사 없이 labelable=false(교정 m2) ──────────────────
def test_v5_characterize_non_owner_get_no_denied_audit(tmp_path, audits) -> None:
    store = _store(tmp_path)
    store.record(investigation_id="i1", alarm_ids=["a1"], status="done", source="inline",
                 kind="alarm", briefing=_briefing(), alarm_db_ids={"a1": GONGJON_DB})
    config = _config(tmp_path, investigation_memory_enabled=False)
    client = _client(config, GONG_USER)
    for _ in range(3):
        res = client.get(f"{BASE}/i1/cause-label")
        assert res.status_code == 200
        body = res.json()
        assert body["labelable"] is False and body["label"] is None
        assert body["hypothesis_ranks"] == []
    assert [a for a in audits if a.get("reason") == "owner_only"] == []
    # POST 거부 감사는 유지
    assert client.post(f"{BASE}/i1/cause-label", json={"verdict": "top1"}).status_code == 403
    assert len([a for a in audits if a.get("reason") == "owner_only"]) == 1


# ── V6 후보 내보내기 감사(교정 m3) ──────────────────────────────────────────
def test_v6_characterize_candidates_export_audited(tmp_path, audits) -> None:
    store = _store(tmp_path)
    store.record(investigation_id="i1", alarm_ids=["a1"], status="done", source="inline",
                 kind="alarm", briefing=_briefing(), alarm_db_ids={"a1": GONGJON_DB})
    config = _config(tmp_path, investigation_memory_enabled=False)
    admin = _client(config, ADMIN)
    assert admin.post(f"{BASE}/i1/cause-label", json={"verdict": "top1"}).status_code == 200
    before = len(audits)
    res = admin.get("/api/v1/admin/investigation/accuracy/candidates")
    assert res.status_code == 200 and res.json()["count"] == 1
    body = json.dumps(res.json(), ensure_ascii=False)
    # 메타만 — 가설 서술·요약 없음(D-308)
    assert "DB 지연" not in body and "WAS 응답 지연" not in body
    new = audits[before:]
    assert len(new) == 1
    assert new[0]["action"] == "accuracy_candidates_export" and new[0]["count"] == 1


# ── V7 화면 XSS — 신규 렌더는 textContent만 ─────────────────────────────────
def test_v7_new_view_code_has_no_innerhtml() -> None:
    dv = (_ROOT / "src/static/js/diagnosis-view.js").read_text(encoding="utf-8")
    start = dv.index("function loadSimilar(tab)")
    end = dv.index("function select(id)", start)
    assert "innerHTML" not in dv[start:end]
    for fn in ("function causeLabelText", "function similarText"):
        s = dv.index(fn)
        assert "innerHTML" not in dv[s:dv.index("\n    }\n", s)]
    acc = (_ROOT / "src/static/js/admin-investigation-accuracy.js").read_text(encoding="utf-8")
    assert "innerHTML" not in acc and "insertAdjacentHTML" not in acc
    assert not re.search(r"\.outerHTML\s*=", acc)


# ── V8 inline 모드 합류 ──────────────────────────────────────────────────────
async def test_v8_characterize_inline_mode_never_joins(tmp_path, monkeypatch) -> None:
    from noise_gate.application import alarm_worker as worker_mod
    from noise_gate.tests.test_plan144_cross_source import _Clock
    from noise_gate.tests.test_plan153_w4_episode_investigation import _feed, _lines
    from noise_gate.tests.test_plan198_w8_incident_unit import _p2, _setup

    clock = _Clock()
    monkeypatch.setattr(worker_mod.time, "time", clock)
    t = _setup(tmp_path, incident=True, followup=False, episode_ctx=False)
    await _feed(t.w, _lines()[0]["payload"], 1)
    await _feed(t.w, _p2(), 2)
    # 워커는 알람을 순차 처리하고 inline 노드는 종결까지 poll한다 — 두 번째 알람 시점에
    # 첫 조사는 이미 종결이라 합류하지 않는다(I-11 합류는 후속 모드에서만 효과)
    assert len(t.client.payloads) == 2
    assert "incident_joined" not in {i["status"] for i in t.audit.investigations}


# ── 교정 m5 — 자유 서술 입력란 길이 제한 = 서버 상한(GET note_max_chars) ─────────────
def test_m5_cause_note_input_has_server_maxlength() -> None:
    dv = (_ROOT / "src/static/js/diagnosis-view.js").read_text(encoding="utf-8")
    start = dv.index("function renderCauseLabel(tab, data)")
    body = dv[start:dv.index("function select(id)", start)]
    assert "data.note_max_chars" in body and "note.maxLength = noteMax" in body
