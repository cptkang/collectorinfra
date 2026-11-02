"""plans/198 본체·noise_gate 측 심층 재검토(verifier 2차) — 앞선 검증이 덮지 않은 빈 곳.

결함 재현(R1·R2)은 교정 뒤 통과 테스트로 바꿨다(plans/198 교정 — 첫 판은 strict xfail).
현행 동작 기록(characterization)은 통과 테스트로 두고 이름에 `characterize`를 붙인다.

- R1 HolmesGPT(플레이북 없는) 조사의 지문이 `kind:` 토큰 하나로 축퇴 — 무관한 조사끼리 Jaccard 1.0
- R2 철회 라우트가 라벨 플래그 off에서도 운영자 철회 행에 `operator` 키를 싣는다
  (저장 형식 비트 동일 위반)
- R3 정확도 집계 — 입력 순서 무관 결정성 · 4순위 이상 정답은 후보 재현율에만 든다
- R4 유사 조사가 라벨(판정·서술)을 보이는 범위는 cause-label GET보다 넓다(조회 권한만으로 노출)
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.security.audit_logger as audit_mod
from noise_gate.domain.incident_fingerprint import fingerprint, similar_investigations
from noise_gate.domain.investigation_accuracy import accuracy_metrics, weekly_report
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import alarm as alarm_routes
from src.api.routes import investigation_label as label_routes
from src.routing.zones import ZONE_GONGJON
from tests.test_plan198_w0_label_api import _config as _w0_config

GONGJON_DB = "polestar_cm_gp"
ADMIN = {"sub": "admin1", "role": "admin"}
OWNER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
GONG_USER = {"sub": "u9", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BASE = "/api/v1/alarm/investigations"


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


def _client(config, user, *, alarm: bool = False) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    if alarm:
        app.include_router(alarm_routes.router, prefix="/api/v1")
    if config.noise_gate.investigation_label_enabled:
        app.include_router(label_routes.router, prefix="/api/v1")
    if getattr(config.noise_gate, "investigation_memory_enabled", False):
        app.include_router(label_routes.memory_router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[require_admin_user] = lambda: user
    return TestClient(app)


def _holmes(iid: str, cause: str, host: str, ts: str = "2026-10-20T01:00:00+00:00") -> dict:
    """플레이북 없는 HolmesGPT 6요소 브리핑의 완료 조사(실 계약 모양)."""
    return {
        "investigation_id": iid, "status": "done", "kind": "alarm", "ts": ts,
        "briefing": {
            "summary": f"{host} 조사",
            "cause": cause,
            "root_cause_hypotheses": [{"rank": 1, "cause": cause, "confidence": "high",
                                       "reasoning": "조사 서술의 인용 원인"}],
        },
    }


# ── R1 HolmesGPT 지문 축퇴 ──────────────────────────────────────────────────
def test_r1_characterize_holmes_fingerprint_is_kind_only() -> None:
    a = _holmes("a", "디스크 가득 참", "db01")
    b = _holmes("b", "JVM 힙 고갈", "was77")
    assert fingerprint(a) == fingerprint(b) == frozenset({"kind:alarm"})


def test_r1_unrelated_holmes_investigations_are_not_similar() -> None:
    target = _holmes("t", "네트워크 단절", "web01")
    records = [target, _holmes("a", "디스크 가득 참", "db01"),
               _holmes("b", "JVM 힙 고갈", "was77")]
    items = similar_investigations(target, records, {})
    assert items == []


# ── R2 철회 행 형식(라벨 플래그 off) ─────────────────────────────────────────
def test_r2_flag_off_operator_retract_row_unchanged(tmp_path, audits) -> None:
    config = _w0_config(tmp_path, label=False)
    config.noise_gate.enable_noise_gate = True
    config.noise_gate.enable_llm_actionability = True
    client = _client(config, ADMIN, alarm=True)
    res = client.post("/api/v1/alarm/feedback/retract",
                      json={"target_ts": "2026-10-28T00:00:00+00:00", "alarm_name": "CPU"})
    assert res.status_code == 200
    rows = [json.loads(x) for x in (tmp_path / "fb.jsonl").read_text().splitlines()]
    assert set(rows[-1]) == {"ts", "label", "target_ts", "alarm_name", "labeled_by"}


# ── R3 정확도 집계 결정성 · 분모 ────────────────────────────────────────────
def _rec(iid: str, n_hyp: int, ts: str, status: str = "done") -> dict:
    return {
        "investigation_id": iid, "status": status, "kind": "alarm", "ts": ts,
        "briefing": {"playbook": {"id": "pb"}, "root_cause_hypotheses": [
            {"rank": i, "cause": f"h{i}"} for i in range(1, n_hyp + 1)]},
    }


def test_r3_accuracy_order_independent_and_rank4_counts_recall_only() -> None:
    recs = [_rec(f"i{n}", 5, f"2026-10-2{n % 7}T03:00:00+00:00") for n in range(6)]
    recs.append(_rec("run", 5, "2026-10-26T03:00:00+00:00", status="running"))
    recs.append(_rec("fail", 5, "2026-10-26T03:00:00+00:00", status="failed"))
    labels = {
        "i0": {"verdict": "top1", "rank": 1},
        "i1": {"verdict": "ranked", "rank": 3},
        "i2": {"verdict": "ranked", "rank": 4},
        "i3": {"verdict": "not_listed", "rank": None, "note": "x"},
        "i4": {"verdict": "undeterminable", "rank": None},
        "run": {"verdict": "top1", "rank": 1},  # 진행 중 — 세지 않는다
    }
    m = accuracy_metrics(recs, labels)
    assert (m["investigations"], m["done"], m["incomplete"]) == (7, 6, 1)
    assert (m["labeled"], m["undeterminable"], m["judged"]) == (5, 1, 4)
    assert (m["in_list"], m["top1"], m["top3"]) == (3, 1, 2)
    assert m["candidate_recall"] == 0.75 and m["rc_at_1"] == 0.25 and m["rc_at_3"] == 0.5

    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    base = weekly_report(recs, labels, now=now, weeks=4)
    shuffled = list(recs)
    random.Random(7).shuffle(shuffled)
    assert weekly_report(shuffled, labels, now=now, weeks=4) == base


# ── R4 유사 조사 라벨 노출 범위 ─────────────────────────────────────────────
def _pb_briefing() -> dict:
    return {
        "summary": "WAS 응답 지연", "cause": "가설 1",
        "root_cause_hypotheses": [
            {"rank": 1, "cause": "DB 지연", "status": "supported", "reasoning": "H-DB"},
        ],
        "playbook": {"id": "was.latency", "readings": {"dominant": "sql"},
                     "scope": {"host": "web01"}},
    }


def test_r4_characterize_similar_shows_label_hidden_by_cause_label_get(tmp_path, audits) -> None:
    store = InvestigationStore(str(tmp_path / "inv.jsonl"))
    for iid in ("t", "a"):
        store.record(investigation_id=iid, alarm_ids=[f"al-{iid}"], status="done",
                     source="inline", kind="alarm", briefing=_pb_briefing(),
                     alarm_db_ids={f"al-{iid}": GONGJON_DB})
    config = _w0_config(tmp_path, investigation_memory_enabled=True,
                        investigation_kb_enabled=False)
    admin = _client(config, ADMIN)
    assert admin.post(f"{BASE}/a/cause-label",
                      json={"verdict": "not_listed", "note": "스위치 포트 불량"}).status_code == 200

    user = _client(config, GONG_USER)
    # 자동 조사 라벨은 관리자만 — 사용자의 cause-label GET은 라벨을 숨긴다
    got = user.get(f"{BASE}/a/cause-label").json()
    assert got["labelable"] is False and got["label"] is None
    # 같은 사용자가 유사 조사로는 그 라벨의 판정·자유 서술(결론)을 본다
    items = user.get(f"{BASE}/t/similar").json()["items"]
    assert items and items[0]["investigation_id"] == "a"
    assert items[0]["verdict"] == "not_listed" and items[0]["conclusion"] == "스위치 포트 불량"
