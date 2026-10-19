"""plans/187 W9 — KB 쓰기·참조 측 API (D-341 ⑩⑮⑳ · G-14~G-16 · G-27).

- 플래그 off: 네 라우트 404(인증보다 먼저) · 관리자 라우터 미등록 · 조회 응답 비트 동일 · 기록 0
- KB 초안: 운영자만(비운영자 403 + 감사) · 완료 조사만 · state=wip · 본문에 섞인 state/match 무시 ·
  sre_agent 아래 파일 쓰기 0(파일 시스템 감시) · 엔진 선택이 원인 기본값
- 엔진 선택: 판정·티어 불변(조사·결정 저장소 바이트 동일) · 통계·전환 권고
- 포스트모템: 기준 미충족 409 + 사유 · 사용자 경로 마스킹
- KE 피드백: 브리핑에 없는 KE 404 · 「아님」 3회 → 검토 대상
- 참고 문서(RAG): 운영자 응답 사본에만 · 저장 레코드 불변 · 추가 조사 요청(sre_agent)에 섞이지 않음
- 관리자 API: 비관리자 403(role 클레임 명시 검증) · YAML 내려받기
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.dependencies as deps
import src.clients.fabrix_retrieval as retrieval_mod
import src.infrastructure.doc_sources as doc_sources_mod
import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.incident_kb_store import FORBIDDEN_ROOT
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import admin_incident_kb as admin_routes
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_noise_dashboard_api import _decision_record, _seed_decisions
from tests.test_api.test_plan187_w10_followup import _config as _w10_config
from tests.test_api.test_plan187_w10_followup import _FakeClient, _inject

GONGJON_DB = "polestar_cm_gp"
SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)
ADMIN = {"sub": "admin1", "role": "admin"}
GONGJON_USER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BANKJON_USER = {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]}
REF_TITLE = "WAS 지연 대응 매뉴얼 3장"


def _briefing(**pb_over) -> dict:
    pb = {
        "id": "was.latency",
        "version": 1,
        "scope": {"host": SECRET, "alarm_instance": "A1", "polestar_server": "wasdemo01",
                  "polestar_source": GONGJON_DB,
                  "incident_window": {"end": "2026-10-19T10:10:00", "minutes": 40}},
        "readings": {"values": {"dominant": "sql", "db_split": "n/a", "scope": None}},
        "sections": [{"step": "S3", "status": "ok", "verdict": "degraded",
                      "tools": ["apm_slow_transactions"]}],
        "synthesis": {"hypotheses": [{"rank": 1, "title": "SQL 실행 지연", "status": "supported"}],
                      "actions": ["DBA 확인 요청"]},
        "runbook": {"owner": "WAS운영팀", "references": ["manual://incident/was-latency"]},
        "known_errors": [{"id": "KE-1", "state": "validated", "result": "match"}],
    }
    pb.update(pb_over)
    return {"severity": {"level": "심각", "confidence": "high"}, "summary": "WAS 응답 지연",
            "cause": "SQL 지연", "playbook": pb, "deep_dive": {"cause": "GC 정지"}}


def _config(tmp_path, *, kb: bool = True, **ng_over) -> SimpleNamespace:
    config = _w10_config(tmp_path, enabled=True)
    ng = config.noise_gate
    ng.investigation_kb_enabled = kb
    ng.investigation_kb_store_path = str(tmp_path / "kb.jsonl")
    ng.investigation_kb_store_max_lines = 20000
    ng.investigation_kb_scan_max_lines = 10000
    ng.investigation_kb_incorrect_streak = 3
    ng.investigation_kb_engine_min_samples = 30
    ng.investigation_kb_engine_switch_ratio = 0.8
    for key, value in ng_over.items():
        setattr(ng, key, value)
    config.rag = SimpleNamespace(enabled=False)
    return config


def _client(config, user) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.include_router(admin_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_admin_user] = lambda: user
    return TestClient(app)


def _store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def _seed(tmp_path, *, status: str = "done", briefing: dict | None = None,
          tier: str = "page", verdict: str = "심각", inv: str = "inv-1") -> None:
    _seed_decisions(tmp_path, [_decision_record(
        db_id=GONGJON_DB, tier=tier, alarm_name="응답 지연", condition_log="p95>3s",
    )])
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=["a1"], status=status, source="inline",
        verdict={"level": verdict}, briefing=briefing or _briefing(),
        alarm_db_ids={"a1": GONGJON_DB},
    )


def _kb_rows(tmp_path) -> list[dict]:
    path = tmp_path / "kb.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob("*") if p.is_file() and ".venv" not in p.parts
            and "__pycache__" not in p.parts}


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    async def _doc(**kwargs):  # noqa: ANN003, ANN202
        calls.append({"doc_retrieval": True, **kwargs})

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    monkeypatch.setattr(audit_mod, "log_doc_retrieval", _doc)
    return calls


# ── 플래그 off = 비트 동일 ────────────────────────────────────────────────
def test_flag_off_routes_404_and_nothing_written(tmp_path, audits):
    _seed(tmp_path)
    client = _client(_config(tmp_path, kb=False), ADMIN)
    base = "/api/v1/alarm/investigations/inv-1"
    assert client.post(f"{base}/kb-draft").status_code == 404
    assert client.get(f"{base}/postmortem").status_code == 404
    assert client.post(f"{base}/known-errors/KE-1/feedback",
                       json={"verdict": "incorrect"}).status_code == 404
    assert client.post(f"{base}/engine-choice", json={"choice": "playbook"}).status_code == 404
    assert _kb_rows(tmp_path) == [] and audits == []


def test_flag_off_404_precedes_auth(tmp_path):
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = _config(tmp_path, kb=False)

    def _unauth():  # noqa: ANN202
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="인증이 필요합니다.")

    app.dependency_overrides[require_user] = _unauth
    resp = TestClient(app).post("/api/v1/alarm/investigations/inv-1/kb-draft")
    assert resp.status_code == 404


def test_flag_off_investigation_view_bit_identical(tmp_path, audits):
    _seed(tmp_path)
    off = _client(_config(tmp_path, kb=False), ADMIN).get("/api/v1/alarm/investigations/inv-1")
    config = _config(tmp_path, kb=False)
    del config.noise_gate.investigation_kb_enabled  # 키 자체가 없던 시절
    legacy = _client(config, ADMIN).get("/api/v1/alarm/investigations/inv-1")
    assert off.json() == legacy.json()
    assert "references" not in off.json()["briefing"]["playbook"]


@pytest.mark.parametrize("enabled", [False, True])
def test_create_app_registers_admin_routes_only_when_enabled(enabled):
    from src.api.server import create_app
    from src.config import AppConfig, AuthConfig, NoiseGateConfig

    app = create_app(AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        auth=AuthConfig(enabled=False),
        noise_gate=NoiseGateConfig(investigation_kb_enabled=enabled),
    ))
    paths = set(app.openapi()["paths"])
    admin = {"/api/v1/admin/incident-kb/drafts", "/api/v1/admin/incident-kb/drafts/{draft_id}/yaml",
             "/api/v1/admin/incident-kb/summary"}
    assert (admin & paths) == (admin if enabled else set())


# ── KB 초안 ──────────────────────────────────────────────────────────────────
def test_kb_draft_operator_only(tmp_path, audits):
    _seed(tmp_path)
    resp = _client(_config(tmp_path), GONGJON_USER).post(
        "/api/v1/alarm/investigations/inv-1/kb-draft"
    )
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "operator_only"
    assert _kb_rows(tmp_path) == []
    assert audits[-1]["outcome"] == "denied" and audits[-1]["action"] == "kb_draft"


def test_kb_draft_wip_ignores_body_and_never_writes_under_sre_agent(tmp_path, audits):
    _seed(tmp_path)
    before = _tree(FORBIDDEN_ROOT)
    resp = _client(_config(tmp_path), ADMIN).post(
        "/api/v1/alarm/investigations/inv-1/kb-draft",
        json={"state": "validated", "match": {"all": ["evil == 'x'"]}, "cause_test": ["x"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    ke = body["draft"]["ke"]
    assert body["state"] == "wip" and ke["state"] == "wip"
    assert ke["match"] == {"all": ["dominant == 'sql'"]}
    assert ke["cause_test"] == {"all": []} and ke["workaround"] == []
    assert ke["issue"] == "응답 지연 — p95>3s"
    assert yaml.safe_load(body["yaml"])["state"] == "wip"
    rows = _kb_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["type"] == "kb_draft"
    assert _tree(FORBIDDEN_ROOT) == before  # sre_agent 아래 쓰기 0
    assert audits[-1]["outcome"] == "ok" and audits[-1]["draft_id"] == body["draft_id"]


def test_kb_store_path_under_sre_agent_is_refused(tmp_path, audits):
    _seed(tmp_path)
    bad = FORBIDDEN_ROOT / "config" / "known_errors" / "drafts.jsonl"
    before = _tree(FORBIDDEN_ROOT)
    resp = _client(_config(tmp_path, investigation_kb_store_path=str(bad)), ADMIN).post(
        "/api/v1/alarm/investigations/inv-1/kb-draft"
    )
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == "kb_store_forbidden"
    assert not bad.exists() and _tree(FORBIDDEN_ROOT) == before


def test_kb_draft_requires_done_and_playbook(tmp_path, audits):
    _seed(tmp_path, status="running")
    client = _client(_config(tmp_path), ADMIN)
    assert client.post("/api/v1/alarm/investigations/inv-1/kb-draft").json()["detail"]["code"] \
        == "not_done"
    _seed(tmp_path, inv="inv-2", briefing={"cause": "x"})
    resp = client.post("/api/v1/alarm/investigations/inv-2/kb-draft")
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "no_playbook"


def test_kb_draft_zone_check(tmp_path, audits):
    _seed(tmp_path)
    resp = _client(_config(tmp_path), BANKJON_USER).post(
        "/api/v1/alarm/investigations/inv-1/engine-choice", json={"choice": "playbook"}
    )
    assert resp.status_code == 403 and _kb_rows(tmp_path) == []


# ── 엔진 선택 ────────────────────────────────────────────────────────────────
def test_engine_choice_keeps_verdict_tier_and_sets_draft_cause(tmp_path, audits):
    _seed(tmp_path)
    inv_before = (tmp_path / "inv.jsonl").read_bytes()
    dec_before = (tmp_path / "decisions.jsonl").read_bytes()
    client = _client(_config(tmp_path), ADMIN)
    view_before = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    resp = client.post("/api/v1/alarm/investigations/inv-1/engine-choice",
                       json={"choice": "holmesgpt"})
    assert resp.status_code == 200 and resp.json()["playbook_id"] == "was.latency"
    assert (tmp_path / "inv.jsonl").read_bytes() == inv_before
    assert (tmp_path / "decisions.jsonl").read_bytes() == dec_before
    view_after = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert view_after["verdict_level"] == view_before["verdict_level"] == "심각"
    assert view_after["status"] == view_before["status"]
    draft = client.post("/api/v1/alarm/investigations/inv-1/kb-draft").json()["draft"]
    assert draft["engine_choice"] == "holmesgpt" and draft["ke"]["cause"].startswith("GC 정지")
    assert client.post("/api/v1/alarm/investigations/inv-1/engine-choice",
                       json={"choice": "auto"}).status_code == 400


def test_summary_engine_switch_recommendation(tmp_path, audits):
    _seed(tmp_path)
    for i in range(30):
        user = {"sub": f"u{i}", "role": "admin"}
        choice = "playbook" if i < 24 else "holmesgpt"
        assert _client(_config(tmp_path), user).post(
            "/api/v1/alarm/investigations/inv-1/engine-choice", json={"choice": choice}
        ).status_code == 200
    summary = _client(_config(tmp_path), ADMIN).get("/api/v1/admin/incident-kb/summary").json()
    [stat] = summary["engine_stats"]["items"]
    assert stat["samples"] == 30 and stat["recommend_switch"] is True
    assert "자동 전환 없음" in summary["engine_stats"]["notice"]
    assert summary["runbook_coverage"]["by_playbook"][0]["playbook_id"] == "was.latency"
    assert summary["window"]["rows"] == 1


# ── 포스트모템 ───────────────────────────────────────────────────────────────
def test_postmortem_rejected_when_criteria_unmet(tmp_path, audits):
    _seed(tmp_path, tier="ticket")
    resp = _client(_config(tmp_path), ADMIN).get("/api/v1/alarm/investigations/inv-1/postmortem")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "criteria_unmet" and "당직 호출" in detail["reason"]
    assert [c["met"] for c in detail["criteria"]] == [False, True]


def test_postmortem_user_path_masked(tmp_path, audits):
    _seed(tmp_path)
    user = _client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1/postmortem"
    )
    assert user.status_code == 200, user.text
    assert SECRET not in user.json()["markdown"] and "## Timeline" in user.json()["markdown"]
    admin = _client(_config(tmp_path), ADMIN).get("/api/v1/alarm/investigations/inv-1/postmortem")
    assert SECRET in admin.json()["markdown"]


# ── KE 피드백 ────────────────────────────────────────────────────────────────
def test_ke_feedback_streak_becomes_review_target(tmp_path, audits):
    _seed(tmp_path)
    client = _client(_config(tmp_path), GONGJON_USER)
    url = "/api/v1/alarm/investigations/inv-1/known-errors/{}/feedback"
    assert client.post(url.format("KE-404"), json={"verdict": "incorrect"}).status_code == 404
    assert client.post(url.format("KE-1"), json={"verdict": "nope"}).status_code == 400
    # (교정 M6) 같은 사용자의 반복 기록은 집계에 1건 — 플러드로 검토 대상이 되지 않는다.
    for _ in range(3):
        assert client.post(url.format("KE-1"), json={"verdict": "incorrect"}).status_code == 200
    admin = _client(_config(tmp_path), ADMIN)
    summary = admin.get("/api/v1/admin/incident-kb/summary").json()
    kinds = {(i["kind"], i["id"]) for i in summary["review_targets"]["items"]}
    assert ("ke_incorrect_streak", "KE-1") not in kinds
    # 서로 다른 사용자 3명의 「아님」 연속은 검토 대상이다.
    for sub in ("u3", "u4"):
        user = {"sub": sub, "role": "user", "alarm_zones": [ZONE_GONGJON]}
        resp = _client(_config(tmp_path), user).post(url.format("KE-1"),
                                                       json={"verdict": "incorrect"})
        assert resp.status_code == 200
    summary = admin.get("/api/v1/admin/incident-kb/summary").json()
    kinds = {(i["kind"], i["id"]) for i in summary["review_targets"]["items"]}
    assert ("ke_incorrect_streak", "KE-1") in kinds
    assert summary["ke_recurrence"]["KE-1"]["count_30d"] == 1


@pytest.mark.parametrize("ke_id", ["ke-1", "KE-", "KE-1%20x", "KE-" + "A" * 62, "X-1"])
def test_ke_feedback_rejects_malformed_ke_id_and_audits(tmp_path, audits, ke_id):
    """(교정 M6) KE id 형식 밖이면 400 + 거부 감사 · 기록 0."""
    _seed(tmp_path)
    client = _client(_config(tmp_path), GONGJON_USER)
    resp = client.post(f"/api/v1/alarm/investigations/inv-1/known-errors/{ke_id}/feedback",
                       json={"verdict": "incorrect"})
    assert resp.status_code == 400 and resp.json()["detail"]["code"] == "invalid_ke_id"
    assert audits[-1]["outcome"] == "denied" and audits[-1]["reason"] == "invalid_ke_id"
    assert _kb_rows(tmp_path) == []


def test_kb_feedback_and_choice_validation_rejects_are_audited(tmp_path, audits):
    """(교정 M6) verdict·choice 값 거부·브리핑에 없는 KE도 감사 1건씩."""
    _seed(tmp_path)
    client = _client(_config(tmp_path), GONGJON_USER)
    base = "/api/v1/alarm/investigations/inv-1"
    assert client.post(f"{base}/known-errors/KE-1/feedback",
                       json={"verdict": "nope"}).status_code == 400
    assert client.post(f"{base}/known-errors/KE-404/feedback",
                       json={"verdict": "incorrect"}).status_code == 404
    assert client.post(f"{base}/engine-choice", json={"choice": "nope"}).status_code == 400
    reasons = [(a["action"], a["outcome"], a.get("reason")) for a in audits]
    assert reasons == [
        ("ke_feedback", "denied", "invalid_verdict"),
        ("ke_feedback", "denied", "ke_not_found"),
        ("engine_choice", "denied", "invalid_choice"),
    ]


# ── 관리자 API ────────────────────────────────────────────────────────────────
def test_admin_api_rejects_non_admin_role(tmp_path):
    client = _client(_config(tmp_path), GONGJON_USER)  # 의존성 대역이 비관리자를 통과시켜도
    for path in ("/admin/incident-kb/drafts", "/admin/incident-kb/summary",
                 "/admin/incident-kb/drafts/abc/yaml"):
        assert client.get(f"/api/v1{path}").status_code == 403


def test_admin_api_real_dependency_403(tmp_path, monkeypatch):
    async def _user(request, authorization=None):  # noqa: ANN001, ANN202
        return dict(GONGJON_USER)

    monkeypatch.setattr(deps, "require_user", _user)
    app = FastAPI()
    app.include_router(admin_routes.router, prefix="/api/v1")
    config = _config(tmp_path)
    config.auth.enabled = True
    config.admin = SimpleNamespace(jwt_secret="admin-secret-for-test-only-0123456789")
    app.state.config = config
    resp = TestClient(app).get("/api/v1/admin/incident-kb/summary",
                               headers={"Authorization": "Bearer x"})
    assert resp.status_code == 403


def test_admin_drafts_list_and_yaml_download(tmp_path, audits):
    _seed(tmp_path)
    client = _client(_config(tmp_path), ADMIN)
    draft_id = client.post("/api/v1/alarm/investigations/inv-1/kb-draft").json()["draft_id"]
    items = client.get("/api/v1/admin/incident-kb/drafts").json()["items"]
    assert [i["draft_id"] for i in items] == [draft_id]
    resp = client.get(f"/api/v1/admin/incident-kb/drafts/{draft_id}/yaml")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    assert f'filename="{draft_id}.yaml"' in resp.headers["content-disposition"]
    assert yaml.safe_load(resp.text)["state"] == "wip"
    assert client.get("/api/v1/admin/incident-kb/drafts/nope/yaml").status_code == 404
    assert client.get("/api/v1/admin/incident-kb/drafts/a.b/yaml").status_code == 400


# ── 매뉴얼 RAG 참고 인용 (G-15) ───────────────────────────────────────────────
def _fake_rag(monkeypatch) -> list[str]:
    queries: list[str] = []
    col = doc_sources_mod.DocCollection(
        id="incident_docs", title="장애 대응 매뉴얼", description="d",
        endpoint="e", token="t", client_key="c", retrieval_id="r",
    )
    monkeypatch.setattr(doc_sources_mod, "resolve_collections", lambda rag, path=None: (col,))

    async def _retrieve(cols, query, **kwargs):  # noqa: ANN001, ANN003, ANN202
        queries.append(query)
        hit = retrieval_mod.DocHit(
            collection_id="incident_docs", doc_id="d1", title=REF_TITLE, filename="",
            subtitle="3.2",
            content="커넥션 풀 확인 절차", rank=1, rank_score=0.9, url="/doc/1",
            content_type="", catalog_id="", result_id="",
        )
        ok = retrieval_mod.STATUS_OK
        return [retrieval_mod.RetrievalOutcome("incident_docs", ok, hits=[hit])]

    monkeypatch.setattr(retrieval_mod, "retrieve_many", _retrieve)
    return queries


def test_references_attached_to_operator_copy_only(tmp_path, monkeypatch, audits):
    _seed(tmp_path)
    queries = _fake_rag(monkeypatch)
    config = _config(tmp_path)
    config.rag = SimpleNamespace(enabled=True, timeout=5, total_timeout=5, doc_url_base="")
    stored_before = (tmp_path / "inv.jsonl").read_bytes()
    view = _client(config, ADMIN).get("/api/v1/alarm/investigations/inv-1").json()
    pb = view["briefing"]["playbook"]
    assert [r["title"] for r in pb["references"]] == ["manual://incident/was-latency", REF_TITLE]
    assert pb["references"][0]["pinned"] is True
    assert pb["references_status"] == {"status": "ok", "reason": "", "notice": "참고 · 판정 무관"}
    assert pb["known_errors"][0]["recurrence"]["count_30d"] == 1
    assert queries == ["was.latency manual://incident/was-latency"]
    assert (tmp_path / "inv.jsonl").read_bytes() == stored_before  # 저장 레코드 불변
    assert any(a.get("doc_retrieval") and a["source"] == "incident_kb" for a in audits)
    user_view = _client(config, GONGJON_USER).get("/api/v1/alarm/investigations/inv-1").json()
    # 사용자 뷰는 마스킹한 플레이북 표시부만 싣는다(W5a) — KB 참고 문서는 운영자 사본에만.
    assert "references" not in user_view.get("briefing", {}).get("playbook", {})
    assert len(queries) == 1


def test_references_never_reach_sre_agent_request(tmp_path, monkeypatch, audits):
    _seed(tmp_path)
    _fake_rag(monkeypatch)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    config = _config(tmp_path)
    config.rag = SimpleNamespace(enabled=True, timeout=5, total_timeout=5, doc_url_base="")
    client = _client(config, ADMIN)
    assert REF_TITLE in json.dumps(client.get("/api/v1/alarm/investigations/inv-1").json(),
                                   ensure_ascii=False)
    resp = client.post("/api/v1/alarm/investigations/inv-1/followup",
                       json={"focus": "cause_recheck"})
    assert resp.status_code == 200, resp.text
    sent = json.dumps(fake.diagnose_calls, ensure_ascii=False)
    assert REF_TITLE not in sent and "references" not in sent and "커넥션 풀 확인" not in sent


def test_rag_disabled_gives_pinned_only_with_reason(tmp_path, audits):
    _seed(tmp_path)
    view = _client(_config(tmp_path), ADMIN).get("/api/v1/alarm/investigations/inv-1").json()
    pb = view["briefing"]["playbook"]
    assert [r["title"] for r in pb["references"]] == ["manual://incident/was-latency"]
    assert pb["references_status"]["status"] == "disabled" and pb["references_status"]["reason"]
