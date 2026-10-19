"""plans/187 W11 — 「이 알람 분석」(사용자 요청 알람 조사) 백엔드 (D-341 ㉑~㉔ · G-28~G-31).

- 플래그 off: 접수 라우트 404 · 저장소 무기록 · by-alarm 응답 종전과 같음(manual 레코드가 있어도)
- 접수: 존 검사(fail-closed 403) → 30일·존재 404 → 합류(joined) → 상한 429(추가 조사와 합산)
  → 자동 조사와 같은 페이로드로 `sre_investigate_alarm` 제출 → `kind=manual` 새 레코드
- 원 조사 레코드 불변 · 통보·승격 0건 · PAGE가 아닌 알람도 접수 · 감사 기록
- by-alarm: 자기 단독 다음에 사용자 요청 최신 · `previous`

조사 서비스는 띄우지 않는다 — 가짜 클라이언트를 `build_sre_agent_client`에 끼운다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noise_gate.application.nodes.alarm_notifier as notifier_mod
import noise_gate.infrastructure.polestar_hostname_resolver as resolver_mod
import noise_gate.infrastructure.sre_agent_client as sre_client_mod
import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api import investigation_poll
from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.domain.host_availability import judge_availability
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_config,
    _seed_decisions,
)

GONGJON_DB = "polestar_cm_gp"
BANKJON_DB = "polestar_b0"

ADMIN = {"sub": "admin1", "role": "admin"}
GONGJON_USER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
GONGJON_USER2 = {"sub": "u3", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BANKJON_USER = {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]}


@pytest.fixture(autouse=True)
def _reset_poll_state():
    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LOCKS.clear()
    yield
    investigation_poll.POLL_AT.clear()
    investigation_poll.POLL_LOCKS.clear()


def _config(tmp_path, *, enabled: bool = True, **ng_over) -> SimpleNamespace:
    config = _make_config(tmp_path)
    ng = config.noise_gate
    ng.investigation_store_path = str(tmp_path / "inv.jsonl")
    ng.investigation_store_max_lines = 2000
    ng.investigation_trigger_enabled = True
    ng.investigation_service_url = "http://127.0.0.1:9/sse"
    ng.investigation_service_token = ""
    ng.investigation_mcp_call_timeout_seconds = 2.0
    ng.investigation_additional_enabled = False
    ng.investigation_additional_user_hourly_limit = 3
    ng.investigation_additional_note_max_chars = 200
    ng.investigation_manual_enabled = enabled
    ng.investigation_manual_join_minutes = 10
    for key, value in ng_over.items():
        setattr(ng, key, value)
    config.host_authz = SimpleNamespace(mode="admin_only")
    return config


def _client(config, user) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def _rows(tmp_path) -> list[dict]:
    path = tmp_path / "inv.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _seed_alarm(tmp_path, *, alarm_id: str = "a1", db_id: str = GONGJON_DB, **over) -> None:
    _seed_decisions(tmp_path, [_decision_record(alarm_id=alarm_id, db_id=db_id, **over)])


def _seed_auto(tmp_path, *, inv: str = "inv-auto", alarm_id: str = "a1") -> None:
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=[alarm_id], status="done", source="inline",
        verdict={"level": "critical"}, briefing={"summary": "원 조사 요약"},
        alarm_db_ids={alarm_id: GONGJON_DB},
    )


class _FakeClient:
    """`SreAgentClient`의 submit/diagnose/poll/connect 계약만 흉내 낸다."""

    def __init__(self, *, status: str = "accepted", reason: str | None = None,
                 poll: dict | None = None, unreachable: str | None = None) -> None:
        self.status, self.reason = status, reason
        self.poll_res = poll or {"status": "running"}
        self.unreachable = unreachable
        self.submit_calls: list[dict] = []
        self.diagnose_calls: list[dict] = []
        self.poll_calls: list[str] = []
        self._n = 0

    async def unreachable_reason(self):  # noqa: ANN201
        return self.unreachable

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def submit(self, payload: dict) -> dict:
        self._n += 1
        self.submit_calls.append(payload)
        res = {"investigation_id": f"man-{self._n}", "status": self.status}
        if self.reason:
            res["reason"] = self.reason
        return res

    async def diagnose(self, question, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self._n += 1
        self.diagnose_calls.append({"question": question, **kwargs})
        return {"investigation_id": f"diag-{self._n}", "status": "accepted"}

    async def poll(self, investigation_id: str) -> dict:
        self.poll_calls.append(investigation_id)
        return dict(self.poll_res)


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


@pytest.fixture(autouse=True)
def _hostname(monkeypatch):
    """판정 기록에는 hostname이 없다 — 폴스타 등록 정보 해소(D-046)를 가짜로 둔다."""

    async def _lookup(app_config, db_id, value):  # noqa: ANN001, ANN202
        return resolver_mod.HostLookup(
            f"{value.lower()}-host", value, judge_availability(lookup_failed=True)
        )

    monkeypatch.setattr(resolver_mod, "lookup_host", _lookup)


def _inject(monkeypatch, client) -> None:  # noqa: ANN001
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda gate_cfg: client)


def _post(client: TestClient, alarm_id: str = "a1"):  # noqa: ANN202
    return client.post(f"/api/v1/alarm/{alarm_id}/investigations")


# ── 플래그 off = 비트 동일 ────────────────────────────────────────────────
def test_flag_off_route_404_and_store_untouched(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    before = _rows(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    resp = _post(_client(_config(tmp_path, enabled=False), ADMIN))
    assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}
    assert _rows(tmp_path) == before and fake.submit_calls == [] and audits == []


def test_flag_off_by_alarm_unchanged_even_with_manual_records(tmp_path, monkeypatch):
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    off = _client(_config(tmp_path, enabled=False), ADMIN)
    baseline = off.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    # 켜져 있던 시기에 남은 manual 레코드(더 최신)가 있어도 꺼져 있으면 후보가 아니다.
    _store(tmp_path).record(
        investigation_id="man-old", alarm_ids=["a1"], kind="manual", status="done",
        source="manual", owner_sub="admin1", origin="manual", parent_alarm_id="a1",
    )
    after = off.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert after == baseline and "previous" not in after
    assert after["investigation_id"] == "inv-auto"
    # 원 조사가 없는 알람도 manual이 끼지 않는다(종전: status none).
    _seed_alarm(tmp_path, alarm_id="a2")
    _store(tmp_path).record(
        investigation_id="man-a2", alarm_ids=["a2"], kind="manual", status="done",
        source="manual", origin="manual", parent_alarm_id="a2",
    )
    assert off.get("/api/v1/alarm/investigations/by-alarm/a2").json() == {"status": "none"}


# ── 존 · 범위 ────────────────────────────────────────────────────────────
def test_out_of_zone_alarm_403(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path, db_id=BANKJON_DB)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    resp = _post(_client(_config(tmp_path), GONGJON_USER))
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "zone_denied"
    assert fake.submit_calls == [] and _rows(tmp_path) == []
    assert audits[-1]["entry_point"] == "manual_alarm" and audits[-1]["outcome"] == "denied"


def test_unknown_db_id_is_403_for_zone_user(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path, db_id="")  # db_id 없는 판정 기록 — 존 미상은 거부(fail-closed)
    _inject(monkeypatch, _FakeClient())
    assert _post(_client(_config(tmp_path), GONGJON_USER)).status_code == 403
    # 기록이 없는 알람도 존 제한 사용자에게는 존재 여부를 드러내지 않는다(403).
    assert _post(_client(_config(tmp_path), GONGJON_USER), "nope").status_code == 403


def test_zone_user_can_request_without_admin(tmp_path, monkeypatch, audits):
    # G-30: 관리자 전용이 아니다 — HOST_AUTHZ_MODE=admin_only여도 존 범위 사용자는 접수한다.
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _FakeClient())
    resp = _post(_client(_config(tmp_path), GONGJON_USER))
    assert resp.status_code == 200 and resp.json()["joined"] is False


def test_missing_or_older_than_30_days_404(tmp_path, monkeypatch, audits):
    old = (datetime.now(UTC) - timedelta(days=31)).isoformat()
    _seed_alarm(tmp_path, alarm_id="old", ts=old)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    client = _client(_config(tmp_path), ADMIN)
    for alarm_id in ("nope", "old"):
        resp = _post(client, alarm_id)
        assert resp.status_code == 404 and resp.json()["detail"]["code"] == "alarm_not_found"
    assert fake.submit_calls == []


# ── 접수 · 페이로드 · 저장 ────────────────────────────────────────────────
def test_non_page_alarm_submits_same_payload_contract(tmp_path, monkeypatch, audits):
    ts = (datetime.now(UTC) - timedelta(days=3)).replace(microsecond=0)
    _seed_alarm(tmp_path, tier="suppress", ts=ts.isoformat(), server_name="WEB-01",
                resource_name="cpu0", condition_log="cpu=97")
    _seed_auto(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), GONGJON_USER)).json()
    assert body == {"investigation_id": "man-1", "status": "submitted", "joined": False,
                    "reference_time_source": "decision_ts"}
    payload = fake.submit_calls[0]
    assert set(payload) == {"contract_version", "event", "decision", "meta"}
    assert payload["contract_version"] == "1"
    event = payload["event"]
    assert event["alarmId"] == "a1" and event["dbId"] == GONGJON_DB
    assert event["serverName"] == "WEB-01" and event["hostname"] == "web-01-host"
    assert event["severity"] == 2 and event["resourceName"] == "cpu0"
    assert event["conditionLog"] == "cpu=97"
    # 구 기록(원 알람 시각 없음) — 판정 시각으로 대체하고 출처를 표기한다(서버 로컬 형식).
    assert event["alarmTime"] == ts.astimezone().strftime("%Y%m%d%H%M%S")
    assert payload["decision"]["tier"] == "suppress"
    assert payload["decision"]["fingerprint"].startswith("manual:a1:")
    assert payload["meta"]["investigation_kind"] == "manual"
    assert payload["meta"]["source"] == "collectorinfra"


def test_new_record_and_original_untouched(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    before = _rows(tmp_path)
    original = _store(tmp_path).get_by_investigation("inv-auto")
    _inject(monkeypatch, _FakeClient())
    _post(_client(_config(tmp_path), GONGJON_USER))
    rows = _rows(tmp_path)
    assert rows[: len(before)] == before and len(rows) == len(before) + 1
    new = rows[-1]
    assert new["investigation_id"] == "man-1" and new["kind"] == "manual"
    assert new["alarm_ids"] == ["a1"] and new["alarm_db_ids"] == {"a1": GONGJON_DB}
    assert new["parent_alarm_id"] == "a1" and new["owner_sub"] == "u1"
    assert new["origin"] == "manual" and new["parent_investigation_id"] == "inv-auto"
    assert _store(tmp_path).get_by_investigation("inv-auto") == original


def test_duplicate_id_of_original_is_not_recorded(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    before = _rows(tmp_path)

    class _Dup(_FakeClient):
        async def submit(self, payload: dict) -> dict:
            return {"investigation_id": "inv-auto", "status": "duplicate"}

    _inject(monkeypatch, _Dup())
    resp = _post(_client(_config(tmp_path), ADMIN))
    assert resp.status_code == 502 and resp.json()["detail"]["code"] == "submit_conflict"
    assert _rows(tmp_path) == before


def test_service_rejected_is_recorded_with_reason(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _FakeClient(status="rejected", reason="hourly_budget_exceeded"))
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["status"] == "rejected" and body["reason"] == "hourly_budget_exceeded"
    rec = _store(tmp_path).get_by_investigation(body["investigation_id"])
    assert rec["status"] == "rejected" and rec["kind"] == "manual"
    assert audits[-1]["outcome"] == "denied"


@pytest.mark.parametrize(("client_obj", "code"), [
    (None, "service_unconfigured"),
    (_FakeClient(unreachable="connection refused"), "service_unreachable"),
])
def test_service_unavailable_503(tmp_path, monkeypatch, audits, client_obj, code):
    _seed_alarm(tmp_path)
    _inject(monkeypatch, client_obj)
    resp = _post(_client(_config(tmp_path), ADMIN))
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == code
    assert _rows(tmp_path) == [] and audits[-1]["outcome"] == "failed"


def test_audit_records_entry_point_and_target(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _FakeClient())
    _post(_client(_config(tmp_path), GONGJON_USER))
    entry = audits[-1]
    assert entry["entry_point"] == "manual_alarm" and entry["outcome"] == "ok"
    assert entry["user_id"] == "u1" and entry["backend"] == "sre_agent"
    assert entry["alarm_id"] == "a1" and entry["investigation_id"] == "man-1"
    assert entry["joined"] is False
    assert entry["targets"] == [
        {"server_name": "WEB-01", "hostname": "web-01-host", "db_id": GONGJON_DB}
    ]


# ── 합류 ─────────────────────────────────────────────────────────────────
def test_repeated_requests_join_one_investigation(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    config = _config(tmp_path)
    first = _post(_client(config, GONGJON_USER)).json()
    again = _post(_client(config, GONGJON_USER)).json()
    other = _post(_client(config, GONGJON_USER2)).json()
    # 진행 중 조사는 지연 poll로 갱신한 상태를 돌려준다(가짜 서비스 = running).
    assert again == {"investigation_id": first["investigation_id"], "status": "running",
                     "joined": True}
    assert other["investigation_id"] == first["investigation_id"] and other["joined"] is True
    assert len(fake.submit_calls) == 1
    assert len([r for r in _rows(tmp_path) if r.get("kind") == "manual"]) == 1
    assert audits[-1]["joined"] is True and audits[-1]["user_id"] == "u3"


def test_new_investigation_after_join_window(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _store(tmp_path).record(
        investigation_id="man-old", alarm_ids=["a1"], kind="manual", status="done",
        source="manual", owner_sub="u9", origin="manual", parent_alarm_id="a1",
        ts=datetime.now(UTC) - timedelta(minutes=11),
    )
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), GONGJON_USER)).json()
    assert body["joined"] is False and body["investigation_id"] == "man-1"
    assert len(fake.submit_calls) == 1
    # 새 레코드의 부모는 그 알람의 최신 조사다.
    assert _store(tmp_path).get_by_investigation("man-1")["parent_investigation_id"] == "man-old"


def test_recent_done_joins_but_recent_failed_does_not(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _seed_alarm(tmp_path, alarm_id="a2")
    store = _store(tmp_path)
    recent = datetime.now(UTC) - timedelta(minutes=3)
    store.record(
        investigation_id="man-done", alarm_ids=["a1"], kind="manual", status="done",
        source="manual", origin="manual", parent_alarm_id="a1", ts=recent,
    )
    store.record(
        investigation_id="man-fail", alarm_ids=["a2"], kind="manual", status="failed",
        source="manual", origin="manual", parent_alarm_id="a2", ts=recent,
    )
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    client = _client(_config(tmp_path), ADMIN)
    assert _post(client, "a1").json() == {
        "investigation_id": "man-done", "status": "done", "joined": True,
    }
    assert _post(client, "a2").json()["joined"] is False
    assert len(fake.submit_calls) == 1


def test_stale_active_record_is_polled_before_join(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _store(tmp_path).record(
        investigation_id="man-old", alarm_ids=["a1"], kind="manual", status="submitted",
        source="manual", origin="manual", parent_alarm_id="a1",
        ts=datetime.now(UTC) - timedelta(minutes=30),
    )
    fake = _FakeClient(poll={"status": "done", "briefing": {"summary": "끝"}})
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert fake.poll_calls == ["man-old"]
    assert body["joined"] is False and body["investigation_id"] == "man-1"


# ── 상한(추가 조사와 같은 사용자별 카운터) ──────────────────────────────────
def test_inflight_diagnosis_blocks_manual(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _store(tmp_path).record(
        investigation_id="diag-9", alarm_ids=[], kind="diagnosis", status="submitted",
        source="investigation_chat", owner_sub="u1", parent_investigation_id="inv-x",
        focus="db_wait", origin="investigation_chat",
    )
    fake = _FakeClient(poll={"status": "running"})
    _inject(monkeypatch, fake)
    resp = _post(_client(_config(tmp_path), GONGJON_USER))
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["code"] == "inflight_limit" and "diag-9" in detail["reason"]
    assert fake.submit_calls == [] and audits[-1]["degraded"][0]["reason"] == "inflight_limit"


def test_empty_sub_is_rejected_fail_closed(tmp_path, monkeypatch, audits):
    """(교정 지적 5) 요청자 sub가 비면 403 owner_unknown · 접수 0 · 거부 감사."""
    _seed_alarm(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    user = {**GONGJON_USER, "sub": ""}
    resp = _post(_client(_config(tmp_path), user))
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "owner_unknown"
    assert fake.submit_calls == [] and _rows(tmp_path) == []
    assert audits[-1]["outcome"] == "denied"
    assert audits[-1]["degraded"][0]["reason"] == "owner_unknown"


def test_hourly_limit_counts_diagnosis_and_manual(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    store = _store(tmp_path)
    now = datetime.now(UTC)
    store.record(
        investigation_id="diag-1", alarm_ids=[], kind="diagnosis", status="done",
        source="investigation_chat", owner_sub="u1", focus="db_wait",
        ts=now - timedelta(minutes=40),
    )
    for i, alarm_id in enumerate(["x1", "x2"]):
        store.record(
            investigation_id=f"man-x{i}", alarm_ids=[alarm_id], kind="manual", status="done",
            source="manual", owner_sub="u1", origin="manual", parent_alarm_id=alarm_id,
            ts=now - timedelta(minutes=20 + i),
        )
    _inject(monkeypatch, _FakeClient())
    resp = _post(_client(_config(tmp_path), GONGJON_USER))
    assert resp.status_code == 429 and resp.json()["detail"]["code"] == "hourly_limit"
    assert "3건" in resp.json()["detail"]["reason"]
    resp = _post(_client(_config(tmp_path, investigation_additional_user_hourly_limit=4),
                         GONGJON_USER))
    assert resp.status_code == 200


def test_inflight_manual_blocks_followup(tmp_path, monkeypatch, audits):
    # 반대 방향 — 진행 중인 알람 다시 분석이 추가 조사(W10)도 막는다(한 카운터).
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    fake = _FakeClient(poll={"status": "running"})
    _inject(monkeypatch, fake)
    config = _config(tmp_path, investigation_additional_enabled=True)
    assert _post(_client(config, ADMIN)).status_code == 200
    resp = _client(config, ADMIN).post(
        "/api/v1/alarm/investigations/inv-auto/followup", json={"focus": "cause_recheck"}
    )
    assert resp.status_code == 429 and resp.json()["detail"]["code"] == "inflight_limit"
    assert "man-1" in resp.json()["detail"]["reason"] and fake.diagnose_calls == []


# ── 통보 · 승격 0건 ──────────────────────────────────────────────────────
def test_manual_completion_sends_no_notification(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    decisions_before = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    sent: list[str] = []

    def _spy(name):  # noqa: ANN001, ANN202
        def _f(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            sent.append(name)
        return _f

    for name in ("_start_followup_task", "_send_workb_followup", "_send_workb",
                 "_send_webhook", "_spawn_investigation_followup"):
        monkeypatch.setattr(notifier_mod, name, _spy(name))
    fake = _FakeClient(poll={
        "status": "done", "briefing": {"summary": "조사 완료"},
        "verdict": {"level": "critical", "confidence": "high", "escalate": True},
    })
    _inject(monkeypatch, fake)
    config = _config(tmp_path, investigation_followup_enabled=True, fault_escalation_enabled=True)
    client = _client(config, ADMIN)
    inv = _post(client).json()["investigation_id"]
    view = client.get(f"/api/v1/alarm/investigations/{inv}").json()
    assert view["status"] == "done" and view["kind"] == "manual"
    assert view["parent_alarm_id"] == "a1"
    assert sent == []
    # 판정 감사 파일(조사 감사 행 포함)에 아무것도 더하지 않는다.
    assert (tmp_path / "decisions.jsonl").read_text(encoding="utf-8") == decisions_before


# ── by-alarm 우선순위 · previous ─────────────────────────────────────────
def test_by_alarm_own_auto_first_then_manual_and_previous(tmp_path, monkeypatch):
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    store = _store(tmp_path)
    store.record(
        investigation_id="man-1", alarm_ids=["a1"], kind="manual", status="done",
        source="manual", origin="manual", parent_alarm_id="a1", owner_sub="u1",
    )
    store.record(
        investigation_id="man-2", alarm_ids=["a1"], kind="manual", status="done",
        source="manual", origin="manual", parent_alarm_id="a1", owner_sub="u1",
    )
    client = _client(_config(tmp_path), GONGJON_USER)
    body = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert body["investigation_id"] == "inv-auto"  # 자기 단독이 사용자 요청보다 먼저
    assert [p["investigation_id"] for p in body["previous"]] == ["man-2", "man-1"]
    assert set(body["previous"][0]) == {"investigation_id", "kind", "status", "submitted_at"}
    assert body["previous"][0]["kind"] == "manual"


def test_by_alarm_manual_latest_when_no_own_investigation(tmp_path, monkeypatch):
    _seed_alarm(tmp_path)
    store = _store(tmp_path)
    # 다른 알람 단독 조사에 연결 행으로 붙은 경우(등급 0) — 사용자 요청 최신이 그보다 먼저다.
    store.record(investigation_id="man-1", alarm_ids=["a1"], kind="manual", status="done",
                 source="manual", origin="manual", parent_alarm_id="a1")
    store.record(investigation_id="inv-other", alarm_ids=["a0", "a1"], status="done",
                 source="inline", alarm_db_ids={"a0": GONGJON_DB, "a1": GONGJON_DB})
    client = _client(_config(tmp_path), ADMIN)
    body = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert body["investigation_id"] == "man-1" and body["kind"] == "manual"
    assert [p["investigation_id"] for p in body["previous"]] == ["inv-other"]
    # 종결된 종합 조사는 여전히 맨 앞이다.
    store.record(investigation_id="inv-ep", alarm_ids=["a0", "a1"], kind="episode",
                 status="done", source="followup",
                 alarm_db_ids={"a0": GONGJON_DB, "a1": GONGJON_DB})
    body = client.get("/api/v1/alarm/investigations/by-alarm/a1").json()
    assert body["investigation_id"] == "inv-ep"


def test_previous_hides_out_of_zone_members(tmp_path, monkeypatch):
    _seed_alarm(tmp_path)
    _seed_auto(tmp_path)
    _store(tmp_path).record(
        investigation_id="inv-mixed", alarm_ids=["b9", "a1"], kind="episode", status="failed",
        source="followup", alarm_db_ids={"b9": BANKJON_DB, "a1": GONGJON_DB},
    )
    zone_user = _client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    admin = _client(_config(tmp_path), ADMIN).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert [p["investigation_id"] for p in zone_user["previous"]] == []
    assert [p["investigation_id"] for p in admin["previous"]] == ["inv-mixed"]


def test_manual_record_visible_by_id_to_zone_user_only(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)
    _inject(monkeypatch, _FakeClient(poll={"status": "running"}))
    config = _config(tmp_path)
    inv = _post(_client(config, GONGJON_USER)).json()["investigation_id"]
    assert _client(config, GONGJON_USER2).get(
        f"/api/v1/alarm/investigations/{inv}").status_code == 200
    assert _client(config, BANKJON_USER).get(
        f"/api/v1/alarm/investigations/{inv}").status_code == 403


# ── 판정 기록의 원 알람 정보(교정 — 기준 시각 = 원 알람 발생 시각) ─────────────
async def _gate_record(tmp_path, **gate) -> dict:  # noqa: ANN003
    from noise_gate.application.nodes.notification_gate import notification_gate_node
    from noise_gate.domain.alarm import AlarmAnalysisResult
    from noise_gate.infrastructure.decision_store import DecisionStore
    from noise_gate.tests.test_notification_policy import IMP_MAP, make_ctx, make_event

    store = DecisionStore(str(tmp_path / "gate.jsonl"))
    event = make_event(severity=2, alarm_time=datetime(2026, 10, 1, 9, 30, 15),
                       hostname="saisvd01", resource_type="server.Cpus")
    result = AlarmAnalysisResult(
        alarm_event=event, severity_label="경고", summary="s", probable_cause="c",
        recommended_action="a", notification_channels=[], error=None,
    )
    state = {"alarm_event": event, "analysis_result": result, "history_stats": None,
             "noise_context": make_ctx(), "error": None}
    base = dict(enable_noise_gate=True, suppress_max_severity=2, importance_value_map=IMP_MAP,
                resolved_to_dashboard=False, **gate)
    await notification_gate_node(state, {"configurable": {
        "app_config": SimpleNamespace(noise_gate=SimpleNamespace(**base)), "decision_store": store,
    }})
    return json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])


async def test_gate_records_alarm_source_fields_only_when_on(tmp_path):
    off = await _gate_record(tmp_path / "off")
    off_explicit = await _gate_record(tmp_path / "off2", investigation_manual_enabled=False)
    on = await _gate_record(tmp_path / "on", investigation_manual_enabled=True)
    for rec in (off, off_explicit):
        assert not {"alarm_time", "hostname", "resource_type"} & set(rec)
    assert on["alarm_time"] == "2026-10-01T09:30:15"
    assert on["hostname"] == "saisvd01" and on["resource_type"] == "server.Cpus"
    # 판정 자체와 나머지 키는 플래그와 무관하다.
    strip = {"ts", "alarm_time", "hostname", "resource_type"}
    assert {k: v for k, v in on.items() if k not in strip} == {
        k: v for k, v in off.items() if k not in strip
    }


def test_decision_view_masks_hostname_like_server_name(tmp_path):
    from noise_gate.infrastructure.decision_store import DecisionStore

    _seed_alarm(tmp_path, hostname="sk-abcdefghijklmnopqrstuvwx")
    view = DecisionStore(str(tmp_path / "decisions.jsonl")).get_decision(
        "a1", mask_fn=lambda t: "***" if t.startswith("sk-") else t
    )
    assert view["hostname"] == "***"


def test_recorded_alarm_time_hostname_and_type_drive_payload(tmp_path, monkeypatch, audits):
    alarm_at = (datetime.now() - timedelta(hours=2)).replace(microsecond=0)
    _seed_alarm(tmp_path, alarm_time=alarm_at.isoformat(), hostname="was-real-01",
                resource_type="apm.Instance", server_name="WAS-01")

    async def _no_lookup(*args):  # noqa: ANN002, ANN202
        raise AssertionError("hostname이 기록에 있으면 lookup_host를 부르지 않는다")

    monkeypatch.setattr(resolver_mod, "lookup_host", _no_lookup)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    body = _post(_client(_config(tmp_path), GONGJON_USER)).json()
    assert body["reference_time_source"] == "alarm_time"
    event = fake.submit_calls[0]["event"]
    assert event["alarmTime"] == alarm_at.strftime("%Y%m%d%H%M%S")
    assert event["hostname"] == "was-real-01" and event["resourceType"] == "apm.Instance"
    assert fake.submit_calls[0]["meta"]["hints"]["solution"] == "apm"
    rec = _store(tmp_path).get_by_investigation(body["investigation_id"])
    assert rec["subject"] == {"reference_time": alarm_at.isoformat(),
                              "reference_time_source": "alarm_time"}
    assert audits[-1]["reference_time_source"] == "alarm_time"


def test_old_record_fallback_is_marked(tmp_path, monkeypatch, audits):
    _seed_alarm(tmp_path)  # 플래그 켜기 전 기록 — alarm_time·hostname 없음
    _inject(monkeypatch, _FakeClient())
    body = _post(_client(_config(tmp_path), ADMIN)).json()
    assert body["reference_time_source"] == "decision_ts"
    rec = _store(tmp_path).get_by_investigation(body["investigation_id"])
    assert rec["subject"]["reference_time_source"] == "decision_ts"
