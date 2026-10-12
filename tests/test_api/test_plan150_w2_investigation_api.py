"""plans/150 W2 — 자동 조사 브리핑 조회 API (§4.2 · G-3 (a)).

- `GET /alarm/investigations/by-alarm/{alarm_id}` · `/alarm/investigations/{investigation_id}`
  — 없음(`status: none`) · 존 403 · 사용자 마스킹/원 dict 제외 · 운영자 원문/원 dict
  · 지연 poll(미종결 → 1회 poll → 종결이면 `source: api_poll` 저장) · poll 실패는 저장소 상태 + 사유
  · (교정) 존 fail-closed — 걸린 알람 전부 검사·db_id 미상은 제한 스코프 403 · 조사별 poll 간격
    10초·동시성 1 · 종결 재기록 금지 · 서비스 사유 보존
- `GET /noise/decisions/{alarm_id}` · `/admin/noise/decisions/{alarm_id}` — 조사 레코드가 있을 때만
  `decision.investigation` 가산(사용자 마스킹 · 운영자 원문)
- `GET /alarm/capabilities` — `investigation_enabled`

조사 서비스는 띄우지 않는다 — 기존 `FakeSreClient`(MCP submit/poll 계약 모사)를 주입한다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noise_gate.infrastructure.sre_agent_client as sre_client_mod
from noise_gate.infrastructure.investigation_store import InvestigationStore
from noise_gate.tests.test_investigation_trigger import FakeSreClient
from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.test_api.test_noise_dashboard_api import (
    _decision_record,
    _make_client,
    _make_config,
    _make_user_client,
    _seed_decisions,
)

GONGJON_DB = "polestar_cm_gp"
BANKJON_DB = "polestar_b0"
SECRET = "sk-abcdefghijklmnopqrstuvwx"  # DataMasker 값 패턴(API 키)에 걸리는 문자열

ADMIN = {"sub": "admin1", "role": "admin"}
GONGJON_USER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BANKJON_USER = {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]}
NO_ZONE_USER = {"sub": "u3", "role": "user", "alarm_zones": []}


def _config(tmp_path, **ng_over) -> SimpleNamespace:
    config = _make_config(tmp_path)
    ng = config.noise_gate
    ng.investigation_store_path = str(tmp_path / "inv.jsonl")
    ng.investigation_store_max_lines = 2000
    ng.investigation_trigger_enabled = True
    ng.investigation_service_url = "http://127.0.0.1:9/sse"
    ng.investigation_service_token = ""
    ng.investigation_mcp_call_timeout_seconds = 2.0
    for key, value in ng_over.items():
        setattr(ng, key, value)
    return config


def _alarm_client(config, user) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


def _briefing() -> dict:
    return {
        "severity": {"level": "high", "confidence": "medium"},
        "summary": "web-01 CPU 포화",
        "recommendation": {"items": [SECRET], "note": "배치 중지 검토"},
        "limitations": ["로그 도구 미연결"],
    }


def _seed_done(tmp_path, alarm_id: str = "a1", inv: str = "inv-1") -> None:
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=[alarm_id], status="done", source="inline",
        verdict={"level": "critical"}, briefing=_briefing(),
    )


def _rows(tmp_path) -> list[dict]:
    path = tmp_path / "inv.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def no_network(monkeypatch):
    """지연 poll이 실 조사 서비스로 나가지 않게 막는다 — 테스트가 클라이언트를 직접 주입한다."""
    def _forbid(gate_cfg):  # noqa: ANN001
        raise AssertionError("이 테스트는 조사 서비스를 부르지 않아야 한다")

    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", _forbid)


def _inject_client(monkeypatch, client) -> list:  # noqa: ANN001
    built: list = []

    def _build(gate_cfg):  # noqa: ANN001
        built.append(gate_cfg)
        return client

    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", _build)
    return built


# ── 없음 ─────────────────────────────────────────────────────────────────
def test_none_when_no_record(tmp_path, no_network):
    config = _config(tmp_path)
    client = _alarm_client(config, ADMIN)
    assert client.get("/api/v1/alarm/investigations/by-alarm/a1").json() == {"status": "none"}
    assert client.get("/api/v1/alarm/investigations/inv-x").json() == {"status": "none"}
    assert not (tmp_path / "inv.jsonl").exists()  # 조회는 파일을 만들지 않는다


# ── 사용자 마스킹 / 운영자 원문 ───────────────────────────────────────────
def test_user_path_masks_lines_and_omits_raw_briefing(tmp_path, no_network):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _seed_done(tmp_path)
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert body["status"] == "done" and body["investigation_id"] == "inv-1"
    assert body["kind"] == "alarm" and body["source"] == "inline"
    assert body["verdict_level"] == "critical" and body["updated_at"]
    assert "briefing" not in body
    texts = "\n".join(line["text"] for line in body["briefing_lines"])
    assert SECRET not in texts and "***" in texts
    assert "배치 중지 검토" in texts  # 마스킹은 패턴 일치 줄만


def test_operator_gets_raw_lines_and_briefing_dict(tmp_path, no_network):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _seed_done(tmp_path)
    body = _alarm_client(_config(tmp_path), ADMIN).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["briefing"] == _briefing()
    assert any(SECRET in line["text"] for line in body["briefing_lines"])


def test_dev_mode_is_operator_view(tmp_path, no_network):
    # 인증 비활성(개발 모드)은 SSE 스트림 판정과 같이 운영자 뷰다.
    config = _config(tmp_path)
    config.auth.enabled = False
    _seed_done(tmp_path)
    body = _alarm_client(config, {"sub": "anonymous"}).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert body["briefing"] == _briefing()


# ── 존 검사 ──────────────────────────────────────────────────────────────
def test_other_zone_user_gets_403_on_both_paths(tmp_path, no_network):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _seed_done(tmp_path)
    client = _alarm_client(_config(tmp_path), BANKJON_USER)
    assert client.get("/api/v1/alarm/investigations/by-alarm/a1").status_code == 403
    assert client.get("/api/v1/alarm/investigations/inv-1").status_code == 403


def test_other_zone_403_even_without_record(tmp_path, no_network):
    # 레코드 유무를 흘리지 않는다 — 판정 기록의 존으로 먼저 막는다.
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    client = _alarm_client(_config(tmp_path), BANKJON_USER)
    assert client.get("/api/v1/alarm/investigations/by-alarm/a1").status_code == 403


def test_user_without_alarm_zones_gets_403(tmp_path, no_network):
    _seed_done(tmp_path)
    client = _alarm_client(_config(tmp_path), NO_ZONE_USER)
    assert client.get("/api/v1/alarm/investigations/by-alarm/a1").status_code == 403
    assert client.get("/api/v1/alarm/investigations/inv-1").status_code == 403
    assert client.get("/api/v1/alarm/investigations/inv-none").status_code == 403


def test_episode_member_in_other_zone_is_403_on_both_paths(tmp_path, no_network):
    # 사건 조사(W4)는 걸린 알람 전부가 사용자 존 안이어야 한다 — 첫 알람만 보지 않는다.
    _seed_decisions(tmp_path, [
        _decision_record(alarm_id="g1", db_id=GONGJON_DB),
        _decision_record(alarm_id="b1", db_id=BANKJON_DB),
    ])
    _store(tmp_path).record(
        investigation_id="inv-ep", alarm_ids=["g1", "b1"], status="done", source="followup",
        kind="episode", episode_id="ep-1", briefing=_briefing(),
    )
    client = _alarm_client(_config(tmp_path), GONGJON_USER)
    assert client.get("/api/v1/alarm/investigations/inv-ep").status_code == 403
    assert client.get("/api/v1/alarm/investigations/by-alarm/g1").status_code == 403
    # 무제한 스코프(관리자)는 종전대로 본다.
    admin = _alarm_client(_config(tmp_path), ADMIN)
    assert admin.get("/api/v1/alarm/investigations/inv-ep").json()["kind"] == "episode"


def test_restricted_scope_without_decision_record_is_403(tmp_path, no_network):
    # 판정 기록이 tail 밖으로 밀려 db_id를 모르면 존 제한 사용자에게는 fail-closed(보안 H1).
    _seed_done(tmp_path)
    client = _alarm_client(_config(tmp_path), GONGJON_USER)
    assert client.get("/api/v1/alarm/investigations/by-alarm/a1").status_code == 403
    assert client.get("/api/v1/alarm/investigations/inv-1").status_code == 403
    # 레코드가 없어도 존을 모르는 알람은 403(존재 여부를 흘리지 않는다).
    assert client.get("/api/v1/alarm/investigations/by-alarm/zz").status_code == 403


def test_unrestricted_scope_without_decision_record_is_allowed(tmp_path, no_network):
    # 전 존(관리자)·개발 모드는 db_id를 몰라도 종전대로 본다.
    _seed_done(tmp_path)
    admin = _alarm_client(_config(tmp_path), ADMIN)
    assert admin.get("/api/v1/alarm/investigations/by-alarm/a1").json()["status"] == "done"
    assert admin.get("/api/v1/alarm/investigations/inv-1").json()["status"] == "done"
    dev_config = _config(tmp_path)
    dev_config.auth.enabled = False
    dev = _alarm_client(dev_config, {"sub": "anonymous"})
    assert dev.get("/api/v1/alarm/investigations/by-alarm/a1").json()["status"] == "done"


def test_record_db_ids_are_used_before_decision_store(tmp_path, no_network):
    # 조사 레코드가 멤버 db_id를 들고 있으면 판정 기록 없이도 존을 판정한다.
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="inline",
        briefing=_briefing(), alarm_db_ids={"a1": GONGJON_DB},
    )
    assert _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()["status"] == "done"
    bank = _alarm_client(_config(tmp_path), BANKJON_USER)
    assert bank.get("/api/v1/alarm/investigations/inv-1").status_code == 403
    assert bank.get("/api/v1/alarm/investigations/by-alarm/a1").status_code == 403


def test_trigger_written_db_ids_gate_zone_without_decision_records(tmp_path, monkeypatch):
    """(교정 잔여) 트리거가 남긴 `alarm_db_ids`만으로 존을 판정한다 — 판정 저장소에 행이 없어도
    제한 스코프 사용자는 자기 존이면 200, 타 존 멤버가 섞이면 403."""
    from dataclasses import replace

    from noise_gate.application.nodes.investigation_trigger import (
        investigation_trigger_node,
        submit_episode_investigation,
    )
    from noise_gate.tests.test_investigation_followup import (
        FakeSubmitClient,
        _authz_ns,
        _decision,
        _event,
        _ng,
        _trigger_config,
    )

    class _EpisodeClient(FakeSubmitClient):
        def __init__(self, inv: str) -> None:
            super().__init__()
            self.inv = inv

        async def submit(self, payload: dict) -> dict:
            return {"investigation_id": self.inv, "status": "accepted"}

    _inject_client(monkeypatch, _PollClient({"status": "running"}))  # 제출된 조사는 진행 중
    store = _store(tmp_path)
    event = replace(_event(), db_id=GONGJON_DB)  # alarm_id A-1
    cfg = _trigger_config(_ng(), FakeSubmitClient(), None)
    cfg["configurable"]["investigation_store"] = store
    asyncio.run(investigation_trigger_node({
        "alarm_event": event, "notification_decision": _decision(),
        "recurrence": None, "correlation_meta": None,
    }, cfg))
    app_cfg = SimpleNamespace(noise_gate=_ng(), **_authz_ns())
    for inv, other_db in (("inv-ep-g", GONGJON_DB), ("inv-ep-mixed", BANKJON_DB)):
        episode = {"episode_id": inv, "members": [
            {"alarm_id": "A-1", "db_id": GONGJON_DB}, {"alarm_id": f"m-{inv}", "db_id": other_db},
        ]}
        outcome = asyncio.run(submit_episode_investigation(
            _EpisodeClient(inv), event, _decision(), episode, ["A-1", f"m-{inv}"],
            cfg=app_cfg, inv_store=store,
        ))
        assert outcome["pending"]["alarm_db_ids"] == {"A-1": GONGJON_DB, f"m-{inv}": other_db}

    assert store.get_by_investigation("inv-1")["alarm_db_ids"] == {"A-1": GONGJON_DB}
    assert not (tmp_path / "decisions.jsonl").exists()  # 판정 저장소 행 없음
    gong = _alarm_client(_config(tmp_path), GONGJON_USER)
    bank = _alarm_client(_config(tmp_path), BANKJON_USER)
    assert gong.get("/api/v1/alarm/investigations/inv-1").status_code == 200
    assert gong.get("/api/v1/alarm/investigations/inv-ep-g").status_code == 200
    assert gong.get("/api/v1/alarm/investigations/inv-ep-mixed").status_code == 403
    assert bank.get("/api/v1/alarm/investigations/inv-1").status_code == 403
    assert bank.get("/api/v1/alarm/investigations/inv-ep-g").status_code == 403


# ── 지연 poll ────────────────────────────────────────────────────────────
def _seed_submitted(tmp_path) -> None:
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="submitted", source="followup"
    )


def test_lazy_poll_terminal_is_stored_as_api_poll(tmp_path, monkeypatch):
    _seed_submitted(tmp_path)
    fake = FakeSreClient(poll_status="done", briefing=_briefing(), verdict={"level": "low"})
    built = _inject_client(monkeypatch, fake)
    body = _alarm_client(_config(tmp_path), ADMIN).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert len(built) == 1 and fake.poll_calls == ["inv-1"]
    assert fake.connect_calls == 1 and fake.disconnect_calls == 1
    assert body["status"] == "done" and body["source"] == "api_poll"
    assert body["briefing"] == _briefing() and body["verdict_level"] == "low"
    last = _rows(tmp_path)[-1]
    assert last["source"] == "api_poll" and last["status"] == "done"
    assert last["alarm_ids"] == ["a1"]


def test_lazy_poll_running_is_not_stored(tmp_path, monkeypatch):
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, FakeSreClient(never_terminal=True))
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["status"] == "running" and body["reason"] is None
    assert len(_rows(tmp_path)) == 1  # 재조회마다 행이 쌓이지 않는다


def test_lazy_poll_failure_keeps_store_status_with_reason(tmp_path, monkeypatch):
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, FakeSreClient(connect_error=ConnectionRefusedError("down")))
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert body["status"] == "submitted"
    assert body["reason"] == "조사 서비스 재조회 실패: ConnectionRefusedError"
    assert len(_rows(tmp_path)) == 1


def test_lazy_poll_without_service_url_reports_reason(tmp_path, monkeypatch):
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, None)  # URL 미설정 → 팩토리가 None
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert body["status"] == "submitted" and "URL 미설정" in body["reason"]


def test_lazy_poll_not_found_is_stored_as_failed(tmp_path, monkeypatch):
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, FakeSreClient(poll_status="not_found", briefing={}))
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()
    assert body["status"] == "failed" and "잡이 없습니다" in body["reason"]
    assert _rows(tmp_path)[-1]["source"] == "api_poll"


def test_terminal_record_is_not_polled(tmp_path, no_network):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _seed_done(tmp_path)
    assert _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/by-alarm/a1"
    ).json()["status"] == "done"


class _PollClient:
    """poll 응답을 고정해 돌려주는 대역 — 지연·poll 중 부수효과(다른 쓰기 모사)를 줄 수 있다."""

    def __init__(self, res: dict, *, delay: float = 0.0, on_poll=None) -> None:  # noqa: ANN001
        self.res = res
        self.delay = delay
        self.on_poll = on_poll
        self.poll_calls: list[str] = []

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def poll(self, investigation_id: str) -> dict:
        self.poll_calls.append(investigation_id)
        if self.on_poll is not None:
            self.on_poll()
        if self.delay:
            await asyncio.sleep(self.delay)
        return dict(self.res)


def test_lazy_poll_is_throttled_per_investigation(tmp_path, monkeypatch):
    # 간격(10초) 안의 재요청은 poll 없이 저장소 상태로 응답한다 — 간격이 지나면 다시 poll.
    _seed_submitted(tmp_path)
    fake = _PollClient({"status": "running"})
    _inject_client(monkeypatch, fake)
    client = _alarm_client(_config(tmp_path), GONGJON_USER)
    for _ in range(3):
        assert client.get("/api/v1/alarm/investigations/by-alarm/a1").json()["status"] in {
            "running", "submitted",
        }
    assert fake.poll_calls == ["inv-1"]
    key = (str(tmp_path / "inv.jsonl"), "inv-1")
    alarm_routes._INVESTIGATION_POLL_AT[key] -= alarm_routes._INVESTIGATION_POLL_MIN_INTERVAL
    client.get("/api/v1/alarm/investigations/inv-1")
    assert fake.poll_calls == ["inv-1", "inv-1"]


def test_expired_poll_keys_are_swept(tmp_path, monkeypatch):
    stale = (str(tmp_path / "other.jsonl"), "inv-old")
    alarm_routes._INVESTIGATION_POLL_AT[stale] = 0.0
    alarm_routes._INVESTIGATION_POLL_LOCKS[stale] = asyncio.Lock()
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, _PollClient({"status": "running"}))
    _alarm_client(_config(tmp_path), GONGJON_USER).get("/api/v1/alarm/investigations/inv-1")
    assert stale not in alarm_routes._INVESTIGATION_POLL_AT
    assert stale not in alarm_routes._INVESTIGATION_POLL_LOCKS


async def test_concurrent_refresh_polls_once(tmp_path, monkeypatch):
    # 같은 조사의 동시 요청은 1건만 poll하고 나머지는 그 결과(저장소)로 응답한다.
    _seed_submitted(tmp_path)
    fake = _PollClient(
        {"status": "done", "briefing": _briefing(), "verdict": {"level": "low"}}, delay=0.05
    )
    _inject_client(monkeypatch, fake)
    store = _store(tmp_path)
    rec = store.get_by_investigation("inv-1")
    results = await asyncio.gather(*[
        alarm_routes._refresh_investigation(store, rec, _config(tmp_path).noise_gate)
        for _ in range(3)
    ])
    assert fake.poll_calls == ["inv-1"]
    assert [r[0]["status"] for r in results] == ["done"] * 3
    assert [row["status"] for row in _rows(tmp_path)] == ["submitted", "done"]


def test_terminal_already_stored_is_not_written_again(tmp_path, monkeypatch):
    # poll 사이에 후속 폴링이 종결 행을 먼저 썼으면 API는 다시 쓰지 않는다.
    _seed_submitted(tmp_path)

    def _followup_wrote() -> None:
        _store(tmp_path).record(
            investigation_id="inv-1", alarm_ids=["a1"], status="done", source="followup",
            briefing=_briefing(),
        )

    _inject_client(
        monkeypatch,
        _PollClient({"status": "done", "briefing": _briefing()}, on_poll=_followup_wrote),
    )
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["status"] == "done" and body["source"] == "followup"
    assert [row["source"] for row in _rows(tmp_path)] == ["followup", "followup"]


@pytest.mark.parametrize(
    ("res", "status", "reason"),
    [
        ({"status": "failed", "error": "RuntimeError: holmes 실패"}, "failed",
         "RuntimeError: holmes 실패"),
        ({"status": "rejected", "reason": "호스트 인가 거부"}, "rejected", "호스트 인가 거부"),
        ({"status": "timeout", "reason": "investigation_timeout"}, "timeout",
         "investigation_timeout"),
        ({"status": "stub", "reason": "LLM 키 부재", "briefing": {"stub": True,
          "message": "조사 미실행(스텁)"}}, "done", "LLM 키 부재"),
        ({"status": "done", "error": "무시", "briefing": _briefing()}, "done", None),
    ],
)
def test_lazy_poll_keeps_service_reason(tmp_path, monkeypatch, res, status, reason):
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, _PollClient(res))
    body = _alarm_client(_config(tmp_path), ADMIN).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["status"] == status and body["reason"] == reason
    assert _rows(tmp_path)[-1].get("reason") == reason


def test_stub_message_renders_with_korean_label(tmp_path, monkeypatch):
    _seed_submitted(tmp_path)
    _inject_client(monkeypatch, _PollClient(
        {"status": "stub", "briefing": {"stub": True, "message": "조사 미실행(스텁)"}}
    ))
    body = _alarm_client(_config(tmp_path), GONGJON_USER).get(
        "/api/v1/alarm/investigations/inv-1"
    ).json()
    assert body["briefing_lines"] == [{"label": "안내", "text": "조사 미실행(스텁)"}]


# ── 관제 결정 추적 `investigation` 블록 ────────────────────────────────────
def test_decision_trace_carries_investigation_block(tmp_path):
    _seed_decisions(tmp_path, [_decision_record(db_id=GONGJON_DB)])
    _seed_done(tmp_path)
    config = _config(tmp_path)
    user_body = _make_user_client(config).get("/api/v1/noise/decisions/a1").json()
    op_body = _make_client(config).get("/api/v1/admin/noise/decisions/a1").json()

    user_inv = user_body["decision"]["investigation"]
    assert user_inv["status"] == "done" and "briefing" not in user_inv
    assert all(SECRET not in line["text"] for line in user_inv["briefing_lines"])
    op_inv = op_body["decision"]["investigation"]
    assert op_inv["briefing"] == _briefing()
    assert any(SECRET in line["text"] for line in op_inv["briefing_lines"])
    assert set(user_body) == {"decision", "timeline"}  # 최상위 모양은 그대로


def test_decision_trace_without_record_has_no_block(tmp_path):
    _seed_decisions(tmp_path, [_decision_record()])
    config = _config(tmp_path)
    for client, path in (
        (_make_user_client(config), "/api/v1/noise/decisions/a1"),
        (_make_client(config), "/api/v1/admin/noise/decisions/a1"),
    ):
        assert "investigation" not in client.get(path).json()["decision"]
    assert not (tmp_path / "inv.jsonl").exists()


def test_decision_trace_without_store_setting_has_no_block(tmp_path):
    # 설정 대역에 경로가 없으면(구 설정) 저장소를 읽지 않는다 — 기본 경로로 새지 않게.
    _seed_decisions(tmp_path, [_decision_record()])
    body = _make_user_client(_make_config(tmp_path)).get("/api/v1/noise/decisions/a1").json()
    assert "investigation" not in body["decision"]


# ── capabilities ─────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("over", "expected"),
    [
        ({}, True),
        ({"investigation_trigger_enabled": False}, False),
        ({"investigation_service_url": ""}, False),
        ({"enable_noise_gate": False}, False),
    ],
)
def test_capabilities_investigation_enabled(tmp_path, over, expected):
    config = _config(tmp_path, **over)
    config.noise_gate.sse_bridge_enabled = False
    config.noise_gate.incident_tracking_enabled = False
    body = _alarm_client(config, ADMIN).get("/api/v1/alarm/capabilities").json()
    assert body["investigation_enabled"] is expected
