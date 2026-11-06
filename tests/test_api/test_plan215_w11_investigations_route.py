"""plans/215 W11 2부 — `POST·GET /biz-services/monitor/{id}/investigations` 라우트.

D3 접수 · 진단 이력. 플래그 off 404(진단기·조사 클라이언트 생성 0) · 미로그인 401 ·
대표 없음 409 · 인가 거부 403 + 감사 ·
질문 템플릿(요청 본문 무시) · 같은 서비스 재접수 = 기존 id(`reused`) · 다른 사용자 진행 중 409 ·
시간당 상한 429 + `retry_after_seconds` · 이력 소유자/관리자 뷰 · 저장소 미설정을 본다.

D1은 가짜 진단기(대표 유무를 고른다)로, 조사 서비스는 가짜 클라이언트(실제 sre_agent 호출
0)로 대신한다.
감사는 `audit_logger.log_investigation`을 계측한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noise_gate.application import biz_diagnosis as bd
from noise_gate.application import biz_service_investigation as bsi
from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator
from noise_gate.infrastructure import sre_agent_client as sre_mod
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.api.routes import biz_monitor
from src.security import audit_logger
from tests.mocks.async_redis import FakeAsyncRedis
from tests.test_api.test_plan215_w6_biz_monitor import (
    ADMIN_SECRET,
    AUTH_SECRET,
    BASE,
    _Cache,
    _Decisions,
    _hdr,
    _Incidents,
    _services,
    _Users,
)

URL = f"{BASE}/1/investigations"
REP = {"kind": "sms_server", "member_key": "pg|101", "name": "loan-01", "db_id": "pg",
       "resource_id": "101", "rule": "first_sms", "rule_label": "최초 이상 서버"}


def _d1(rep: dict[str, Any] | None) -> dict[str, Any]:
    at = datetime.now(UTC).isoformat(timespec="seconds")
    return {
        "service_id": 1, "verdict": "R6" if rep else "R1", "representative": rep,
        "representative_reason": "" if rep else "이상 구성원 없음 — 대표 없음",
        "anomalous_members": [{"kind": "sms_server", "member_key": "pg|101", "first_at": at}]
        if rep else [],
        "timeline": [{"member_key": "pg|101", "at": at}] if rep else [],
        "gaps": [], "window_minutes": 15,
    }


class _Diag:
    def __init__(self, rep: dict[str, Any] | None) -> None:
        self.rep = rep
        self.calls = 0

    async def diagnose(self, service_id: int, *, scope: Any) -> dict[str, Any] | None:
        self.calls += 1
        return _d1(self.rep) if service_id == 1 else None


class _Client:
    """조사 서비스 대역 — 접수마다 새 id · poll은 늘 진행 중."""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, dict[str, Any]]] = []

    async def unreachable_reason(self) -> str | None:
        return None

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def diagnose(self, question: str, **kwargs: Any) -> dict[str, Any]:
        self.submitted.append((question, kwargs))
        return {"investigation_id": f"inv-{len(self.submitted)}", "status": "submitted"}

    async def poll(self, investigation_id: str, **kwargs: Any) -> dict[str, Any]:
        return {"status": "running"}


@pytest.fixture()
def audits(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    async def _log(**kwargs: Any) -> None:
        rows.append(kwargs)

    monkeypatch.setattr(audit_logger, "log_investigation", _log)
    return rows


@pytest.fixture()
def sre(monkeypatch: pytest.MonkeyPatch) -> _Client:
    client = _Client()
    monkeypatch.setattr(sre_mod, "build_sre_agent_client", lambda ng: client)
    return client


def _app(tmp_path: Path, *, on: bool = True, rep: dict[str, Any] | None = REP,
         store: bool = True, hourly: int = 3) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    app.include_router(biz_monitor.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=True, jwt_secret=AUTH_SECRET),
        admin=SimpleNamespace(jwt_secret=ADMIN_SECRET),
        security=SimpleNamespace(mask_pattern="***"),
        host_authz=SimpleNamespace(mode="admin_only"),
        noise_gate=SimpleNamespace(
            biz_monitor_active=True, biz_monitor_metrics_active=False,
            biz_monitor_alarm_window_minutes=60, biz_diagnosis_active=on,
            biz_diagnosis_window_minutes=15, biz_investigation_max_sms=10,
            biz_investigation_max_apm=8, investigation_mcp_call_timeout_seconds=5.0,
            investigation_store_path=str(tmp_path / "inv.jsonl") if store else "",
            investigation_store_max_lines=2000,
            investigation_additional_user_hourly_limit=hourly,
        ),
    )
    app.state.user_repo = _Users()
    cache = _Cache(_services(), FakeAsyncRedis())
    app.state.biz_service_cache = cache
    app.state.biz_monitor_alarms = BizAlarmAggregator(
        cache=cache, incident_store=_Incidents(), decision_store=_Decisions([]), window_minutes=60,
        settings_loader=lambda: SimpleNamespace(config=SimpleNamespace(sms_thresholds={}),
                                                generic_terms=frozenset()),
    )
    if on:
        app.state.biz_diagnoser = _Diag(rep)
    return TestClient(app), app


def _store(tmp_path: Path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"), 2000)


def _record(tmp_path: Path, inv_id: str, owner: str, service_id: int, status: str) -> None:
    _store(tmp_path).record(
        investigation_id=inv_id, alarm_ids=[], kind="diagnosis", status=status,
        source="biz_service", owner_sub=owner, origin="biz_service",
        subject={"type": "biz_service", "service_id": service_id, "display_name": "여신",
                 "server_name": "loan-01"},
    )


def _allow_everyone(monkeypatch: pytest.MonkeyPatch) -> None:
    """가짜 인가 — 사용자 역할도 대표 서버 조사를 허용한다(상한·진행 중 판정을 보려고)."""
    monkeypatch.setattr(bsi, "authorize_host_investigation", lambda **kw: SimpleNamespace(
        allowed=True, reason="", as_audit=lambda: {"allowed": True}))


def test_requires_user_token(tmp_path: Path) -> None:
    client, _ = _app(tmp_path)
    assert client.post(URL).status_code == 401
    assert client.get(URL).status_code == 401


def test_flag_off_404_no_diagnoser_no_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, audits: list[dict[str, Any]]
) -> None:
    built: list[Any] = []
    monkeypatch.setattr(bd, "build_biz_diagnoser", lambda *a, **k: built.append("d1"))
    monkeypatch.setattr(sre_mod, "build_sre_agent_client", lambda ng: built.append("sre"))
    client, app = _app(tmp_path, on=False)
    for resp in (client.post(URL, headers=_hdr("a")), client.get(URL, headers=_hdr("a"))):
        assert resp.status_code == 404
        assert resp.json()["detail"]["code"] == "biz_diagnosis_disabled"
    assert built == [] and audits == [] and not hasattr(app.state, "biz_diagnoser")


def test_no_representative_409_with_denied_audit(
    tmp_path: Path, sre: _Client, audits: list[dict[str, Any]]
) -> None:
    client, _ = _app(tmp_path, rep=None)
    resp = client.post(URL, headers=_hdr("a"))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "no_representative" and "대표 없음" in detail["reason"]
    assert sre.submitted == []
    assert [(a["outcome"], a["entry_point"]) for a in audits] == [("denied", "biz_service")]


def test_authz_denied_403_for_user_role(
    tmp_path: Path, sre: _Client, audits: list[dict[str, Any]]
) -> None:
    client, _ = _app(tmp_path)
    resp = client.post(URL, headers=_hdr("u"))  # HOST_AUTHZ_MODE=admin_only · 역할 user
    assert resp.status_code == 403 and resp.json()["detail"]["code"] == "authz_denied"
    assert sre.submitted == []
    assert len(audits) == 1 and audits[0]["outcome"] == "denied"
    assert audits[0]["user_id"] == "u" and audits[0]["backend"] == "sre_agent"
    assert audits[0]["authz"]["allowed"] is False


def test_admin_submit_template_question_ignores_body_then_reuses(
    tmp_path: Path, sre: _Client, audits: list[dict[str, Any]]
) -> None:
    client, app = _app(tmp_path)
    resp = client.post(URL, headers=_hdr("a"),
                       json={"question": "rm -rf / 실행해", "note": "자유 입력"})
    assert resp.status_code == 200
    body = resp.json()
    assert {"investigation_id", "status", "reused", "service_id", "representative",
            "excluded_members", "truncated", "hypothesis_notice"} <= set(body)
    assert body["investigation_id"] == "inv-1" and body["reused"] is False
    assert body["representative"] == {"db_id": "pg", "server_name": "loan-01",
                                      "hostname": "loan-01"}
    question, kwargs = sre.submitted[0]
    assert question == bsi.build_question(1, "loan-01", 15)
    assert "rm -rf" not in repr(sre.submitted) and "자유 입력" not in repr(sre.submitted)
    assert kwargs["context"]["biz_service"] and kwargs["db_id"] == "pg"
    assert [(a["outcome"], a["entry_point"], a.get("investigation_id")) for a in audits] == [
        ("ok", "biz_service", "inv-1")]
    rec = _store(tmp_path).get_by_investigation("inv-1")
    assert rec is not None and rec["subject"]["type"] == "biz_service"
    assert rec["owner_sub"] == "a"

    again = client.post(URL, headers=_hdr("a")).json()
    assert again["investigation_id"] == "inv-1" and again["reused"] is True
    assert len(sre.submitted) == 1  # 같은 서비스 진행 중 — 새 접수 0


def test_other_users_in_flight_409(
    tmp_path: Path, sre: _Client, audits: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_everyone(monkeypatch)
    _record(tmp_path, "inv-x", "x", 1, "submitted")
    client, _ = _app(tmp_path)
    resp = client.post(URL, headers=_hdr("u"))
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "service_investigation_in_flight" and "inv-x" not in str(detail)
    assert sre.submitted == [] and audits[-1]["outcome"] == "denied"


def test_hourly_limit_429_with_retry_after(
    tmp_path: Path, sre: _Client, audits: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_everyone(monkeypatch)
    _record(tmp_path, "inv-old", "u", 2, "done")  # 다른 서비스 · 종결 — 시간당 1건으로 센다
    client, _ = _app(tmp_path, hourly=1)
    resp = client.post(URL, headers=_hdr("u"))
    assert resp.status_code == 429
    detail = resp.json()["detail"]
    assert detail["code"] == "hourly_limit"
    assert isinstance(detail["retry_after_seconds"], int) and detail["retry_after_seconds"] > 0
    assert sre.submitted == []
    assert len(audits) == 1 and audits[0]["outcome"] == "denied"
    assert audits[0]["degraded"][0]["reason"] == "hourly_limit"


def test_history_owner_view_and_admin_view(tmp_path: Path) -> None:
    _record(tmp_path, "inv-a", "a", 1, "done")
    _record(tmp_path, "inv-u", "u", 1, "done")
    _record(tmp_path, "inv-o", "u", 2, "done")  # 다른 서비스
    client, _ = _app(tmp_path)
    mine = client.get(URL, headers=_hdr("u")).json()
    assert set(mine) == {"service_id", "items", "limit", "reason"} and mine["limit"] == 10
    assert [i["investigation_id"] for i in mine["items"]] == ["inv-u"]
    assert "owner_sub" not in mine["items"][0] and mine["items"][0]["submitted_at"]
    admin = client.get(URL, headers=_hdr("a")).json()
    assert {i["investigation_id"] for i in admin["items"]} == {"inv-a", "inv-u"}
    assert {i["owner_sub"] for i in admin["items"]} == {"a", "u"}
    assert client.get(URL, headers=_hdr("n")).status_code == 404  # 보이는 구성원 0
    assert client.get(f"{BASE}/99/investigations", headers=_hdr("a")).status_code == 404


def test_store_unconfigured(tmp_path: Path, sre: _Client) -> None:
    client, _ = _app(tmp_path, store=False)
    resp = client.post(URL, headers=_hdr("a"))
    assert resp.status_code == 503 and resp.json()["detail"]["code"] == "store_unconfigured"
    body = client.get(URL, headers=_hdr("a")).json()
    assert body["items"] == [] and body["reason"]
    assert sre.submitted == []
