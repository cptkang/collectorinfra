"""plans/196 §5.5 — 호스트 후보·연결 API(본체) · 관리자 「호스트 연결」.

- 플래그 off: 후보·연결 라우트 404
- 후보: 인가 존 소스만 조회 · 등급·사유 · 소스 실패는 unchecked · 존 밖 403
- 연결: 운영자 select → confirmed + 재조회 접수(`meta.hints.polestar_link`·`rerun`) · 합류하지 않음
  · 일반 사용자 → pending · none → 부정 연결 · 미존재 서버 400 · 감사
- 관리자: 목록·승인·삭제 · 일반 사용자 403

DB·Redis·조사 서비스는 띄우지 않는다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noise_gate.infrastructure.polestar_hostname_resolver as resolver_mod
import noise_gate.infrastructure.sre_agent_client as sre_client_mod
import src.security.audit_logger as audit_mod
from noise_gate.infrastructure.host_link_store import HostLinkStore
from src.api.dependencies import require_admin_user, require_user
from src.api.routes import admin_host_links
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON
from tests.mocks.async_redis import FakeAsyncRedis
from tests.test_api.test_plan187_w11_manual import (
    _config,
    _FakeClient,
    _reset_poll_state,  # noqa: F401 — autouse 픽스처 재사용
    _rows,
    _seed_alarm,
    _store,
)

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6"
APM_DB = "jennifer_cm_prd"
ADMIN = {"sub": "admin1", "role": "admin"}
GONGJON_USER = {"sub": "u1", "role": "user", "alarm_zones": [ZONE_GONGJON]}
BANKJON_USER = {"sub": "u2", "role": "user", "alarm_zones": [ZONE_BANKJON]}
SECTIONS = [
    {"step": "S2", "status": "ok", "action": "apm.summary"},
    {"step": "S4", "status": "skipped", "reason": "서버 선택 대기", "reason_detail": "W21 P6"},
]


def _host_config(tmp_path, *, enabled: bool = True):  # noqa: ANN001, ANN202
    config = _config(tmp_path, host_link_enabled=enabled)
    config.multi_db = SimpleNamespace(
        get_active_db_ids=lambda: ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
    )
    config.composite = SimpleNamespace(availability_precheck_enabled=False)
    return config


def _app(config, user, *, admin_router: bool = False):  # noqa: ANN001, ANN202
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    if admin_router:
        app.include_router(admin_host_links.router, prefix="/api/v1")
        app.dependency_overrides[require_admin_user] = lambda: user
    app.state.config = config
    app.state.host_link_store = HostLinkStore(FakeAsyncRedis())
    app.dependency_overrides[require_user] = lambda: user
    return app


def _seed(tmp_path) -> None:  # noqa: ANN001
    _seed_alarm(
        tmp_path, db_id=APM_DB, server_name=POD, hostname=POD, resource_type="apm.Instance",
        alarm_name="SQL_TOOMANY_FETCH",
    )
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="inline",
        briefing={"playbook": {"sections": SECTIONS}}, alarm_db_ids={"a1": APM_DB},
    )


def _finish(tmp_path, inv: str) -> None:  # noqa: ANN001
    """재조회 조사를 종결로 남긴다(사용자 진행 1건 상한 해제 · 합류 창 판정 재료)."""
    _store(tmp_path).record(
        investigation_id=inv, alarm_ids=["a1"], kind="manual", status="done", source="manual",
        owner_sub="admin1", origin="host_rerun", parent_alarm_id="a1",
        alarm_db_ids={"a1": APM_DB},
    )


@pytest.fixture
def probes(monkeypatch) -> list[tuple]:  # noqa: ANN001
    calls: list[tuple] = []

    async def _probe(app_config, db_id, *, names, prefixes, ips):  # noqa: ANN001, ANN202
        calls.append(("probe", db_id, tuple(names)))
        if db_id == "polestar_cm_yd":
            raise RuntimeError("down")
        if "kbl-kbland-admin-price" in names:
            return [{"hostname": "kbl-kbland-admin-price", "name": "KBL-KBLAND-ADMIN-PRICE",
                     "ipaddress": "192.0.2.5"}]
        return []

    async def _tokens(app_config, db_id, tokens):  # noqa: ANN001, ANN202
        calls.append(("tokens", db_id, tuple(tokens)))
        return [{"hostname": "kbl-kbland-admin-price", "name": "KBL-KBLAND-ADMIN-PRICE",
                 "ipaddress": "192.0.2.5"}]

    monkeypatch.setattr(resolver_mod, "probe_hosts", _probe)
    monkeypatch.setattr(resolver_mod, "probe_host_tokens", _tokens)
    return calls


@pytest.fixture
def audits(monkeypatch) -> list[dict]:  # noqa: ANN001
    calls: list[dict] = []

    async def _log(**kwargs):  # noqa: ANN003, ANN202
        calls.append(kwargs)

    monkeypatch.setattr(audit_mod, "log_investigation", _log)
    return calls


def _inject(monkeypatch, client) -> None:  # noqa: ANN001
    monkeypatch.setattr(sre_client_mod, "build_sre_agent_client", lambda gate_cfg: client)


CAND = "/api/v1/alarm/investigations/inv-1/host-candidates"
LINK = "/api/v1/alarm/investigations/inv-1/host-link"
SELECT = {"action": "select", "db_id": "polestar_cm_gp", "server_name": "kbl-kbland-admin-price"}


def test_flag_off_routes_404(tmp_path, probes):
    _seed(tmp_path)
    client = TestClient(_app(_host_config(tmp_path, enabled=False), ADMIN))
    assert client.get(CAND).status_code == 404
    assert client.post(LINK, json={"action": "none"}).status_code == 404
    assert probes == []


def test_candidates_authorized_sources_and_unchecked(tmp_path, probes):
    _seed(tmp_path)
    body = TestClient(_app(_host_config(tmp_path), GONGJON_USER)).get(CAND).json()
    assert body["hostname"] == POD and body["source_id"] == "cm_prd" and body["link"] is None
    # 존 제한 사용자 — 공동존 소스만(은행존 미조회)
    assert {c[1] for c in probes} == {"polestar_cm_gp", "polestar_cm_yd"}
    assert body["unchecked"] == [{"db_id": "polestar_cm_yd", "reason": "조회 실패(RuntimeError)"}]
    (cand,) = body["candidates"]
    assert set(cand) == {"db_id", "server_name", "hostname", "ipaddress", "grade", "grade_key",
                         "score", "reasons"}
    assert cand["grade_key"] == "similar" and cand["db_id"] == "polestar_cm_gp"


def test_candidates_zone_denied(tmp_path, probes):
    _seed(tmp_path)
    assert TestClient(_app(_host_config(tmp_path), BANKJON_USER)).get(CAND).status_code == 403
    assert probes == []


def test_operator_select_confirms_and_submits_rerun(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    app = _app(_host_config(tmp_path), ADMIN)
    client = TestClient(app)
    res = client.post(LINK, json=SELECT)
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["status"] == "submitted" and out["investigation_id"] == "man-1"
    assert out["stored"] is True and out["link"]["state"] == "confirmed"
    hints = fake.submit_calls[0]["meta"]["hints"]
    assert hints["polestar_link"]["basis"] == "user_selected"
    assert hints["polestar_link"]["server_name"] == "KBL-KBLAND-ADMIN-PRICE"
    assert hints["rerun"] == {"parent_investigation_id": "inv-1", "axes": ["polestar"],
                              "prior_sections": SECTIONS}
    row = _rows(tmp_path)[-1]
    assert row["origin"] == "host_rerun" and row["parent_investigation_id"] == "inv-1"
    assert any(a.get("entry_point") == "host_link" for a in audits)
    # 사용자 진행 1건 상한은 수동 조사와 함께 센다 — 진행 중이면 429
    assert client.post(LINK, json=SELECT).status_code == 429
    _finish(tmp_path, "man-1")
    # 끝난 뒤 다시 select하면 합류하지 않고 새 조사를 접수한다
    again = client.post(LINK, json=SELECT).json()
    assert again["investigation_id"] == "man-2"
    # 이후 자동 경로 힌트 — 확정 연결
    store = app.state.host_link_store
    import asyncio

    link = asyncio.run(store.get("cm_prd", POD))
    assert link["state"] == "confirmed"
    assert asyncio.run(store.patterns()) == {"prefix|bfm|kbl": 1}


def test_user_select_is_pending(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    out = TestClient(_app(_host_config(tmp_path), GONGJON_USER)).post(LINK, json=SELECT).json()
    assert out["link"]["state"] == "pending"
    # M2 — 누가·언제·어느 조사는 운영자에게만
    assert set(out["link"]) <= {"state", "kind", "db_id", "server_name", "polestar_hostname",
                                "grade", "expires_at"}


def test_select_unknown_server_and_foreign_source(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    client = TestClient(_app(_host_config(tmp_path), GONGJON_USER))
    res = client.post(LINK, json={**SELECT, "server_name": "nope"})
    assert res.status_code == 400 and res.json()["detail"]["code"] == "server_not_found"
    res = client.post(LINK, json={**SELECT, "db_id": "polestar_b0"})
    assert res.status_code == 403 and res.json()["detail"]["code"] == "zone_denied"
    assert fake.submit_calls == []


def test_none_records_absent(tmp_path, probes, audits):
    _seed(tmp_path)
    out = TestClient(_app(_host_config(tmp_path), ADMIN)).post(LINK, json={"action": "none"}).json()
    assert out["status"] == "recorded" and out["investigation_id"] is None
    assert out["link"]["kind"] == "absent" and out["link"]["expires_at"]


def test_manual_request_does_not_join_rerun(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    client = TestClient(_app(_host_config(tmp_path), ADMIN))
    assert client.post(LINK, json=SELECT).json()["investigation_id"] == "man-1"
    _finish(tmp_path, "man-1")
    manual = client.post("/api/v1/alarm/a1/investigations").json()
    assert manual["joined"] is False and manual["investigation_id"] == "man-2"


def test_admin_list_approve_delete(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    app = _app(_host_config(tmp_path), GONGJON_USER)
    TestClient(app).post(LINK, json=SELECT)  # 사용자 선택 → pending
    app.dependency_overrides[require_user] = lambda: ADMIN
    app.include_router(admin_host_links.router, prefix="/api/v1")
    app.dependency_overrides[require_admin_user] = lambda: ADMIN
    admin = TestClient(app)
    listed = admin.get("/api/v1/admin/host-links").json()
    assert listed["links"][0]["state"] == "pending" and listed["patterns"] == []
    key = {"source_id": "cm_prd", "hostname": POD}
    seen = listed["links"][0]
    body = {**key, "db_id": seen["db_id"], "server_name": seen["server_name"], "at": seen["at"]}
    stale = admin.post("/api/v1/admin/host-links/approve", json={**body, "at": "old"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "link_changed"
    approved = admin.post("/api/v1/admin/host-links/approve", json=body).json()
    assert approved["link"]["state"] == "confirmed"
    listed = admin.get("/api/v1/admin/host-links").json()
    assert listed["patterns"][0] == {
        "key": "prefix|bfm|kbl", "kind": "prefix", "from": "bfm", "to": "kbl",
        "label": "접두 치환 bfm → kbl", "count": 1,
    }
    assert admin.request("DELETE", "/api/v1/admin/host-links", json=key).status_code == 200
    assert admin.request("DELETE", "/api/v1/admin/host-links", json=key).status_code == 404
    assert {a.get("action") for a in audits if a.get("entry_point") == "host_link"} >= {
        "approve", "delete",
    }
    admin_audits = [a for a in audits if a.get("action") in ("approve", "delete")
                    and a.get("outcome") == "ok"]
    assert admin_audits and all(
        a["link_db_id"] == "polestar_cm_gp" and a["link_server_name"] == "KBL-KBLAND-ADMIN-PRICE"
        for a in admin_audits
    )
    bad = admin.request("DELETE", "/api/v1/admin/host-links",
                        json={"source_id": "cm:prd", "hostname": POD})
    assert bad.status_code == 400 and bad.json()["detail"]["code"] == "invalid_link_key"


def test_admin_role_claim_checked(tmp_path):
    app = _app(_host_config(tmp_path), GONGJON_USER, admin_router=True)
    assert TestClient(app).get("/api/v1/admin/host-links").status_code == 403


# ─── 교정 라운드 — 직접 입력 · 입력 화이트리스트 · 호출 상한 · 감사 · 재조회 판독 맵 ──────


def test_direct_input_without_db_id_ambiguous_across_sources(tmp_path, monkeypatch, probes,
                                                             audits):
    """db_id 없는 직접 입력 — 인가 소스 두 곳(b0·cm_gp)에 같은 서버가 있으면 409."""
    _seed(tmp_path)
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    res = TestClient(_app(_host_config(tmp_path), ADMIN)).post(
        LINK, json={"action": "select", "server_name": "kbl-kbland-admin-price"}
    )
    assert res.status_code == 409 and res.json()["detail"]["code"] == "server_ambiguous"
    assert {c[1] for c in probes} == {"polestar_b0", "polestar_cm_gp", "polestar_cm_yd"}
    assert fake.submit_calls == []


def test_direct_input_not_found_with_failed_source_is_verify_failed(tmp_path, monkeypatch,
                                                                     probes, audits):
    """0곳 일치인데 확인하지 못한 소스가 있으면 「없음」으로 단정하지 않는다(502)."""
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    res = TestClient(_app(_host_config(tmp_path), GONGJON_USER)).post(
        LINK, json={"action": "select", "server_name": "nope"}
    )
    assert res.status_code == 502 and res.json()["detail"]["code"] == "verify_failed"


def test_select_rejects_unsafe_text_before_probe(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    res = TestClient(_app(_host_config(tmp_path), ADMIN)).post(
        LINK, json={**SELECT, "server_name": "x' OR '1'='1"}
    )
    assert res.status_code == 400 and res.json()["detail"]["code"] == "invalid_selection"
    assert probes == []


def test_candidates_rate_limited_per_user(tmp_path, probes):
    _seed(tmp_path)
    client = TestClient(_app(_host_config(tmp_path), GONGJON_USER))
    codes = [client.get(CAND).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429
    assert client.get(CAND).json()["detail"]["code"] == "host_link_rate_limited"


def test_candidates_user_view_hides_actor(tmp_path, monkeypatch, probes, audits):
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    app = _app(_host_config(tmp_path), ADMIN)
    TestClient(app).post(LINK, json=SELECT)  # 운영자 확정
    app.dependency_overrides[require_user] = lambda: GONGJON_USER
    body = TestClient(app).get(CAND).json()
    assert body["link"]["state"] == "confirmed" and "by" not in body["link"]
    assert all("admin1" not in r for c in body["candidates"] for r in c["reasons"])
    app.dependency_overrides[require_user] = lambda: ADMIN
    assert TestClient(app).get(CAND).json()["link"]["by"] == "admin1"


def test_early_denials_are_audited(tmp_path, probes, audits):
    _seed(tmp_path)
    res = TestClient(_app(_host_config(tmp_path), BANKJON_USER)).post(LINK, json=SELECT)
    assert res.status_code == 403
    denied = [a for a in audits if a.get("entry_point") == "host_link"]
    assert denied and denied[-1]["outcome"] == audit_mod.INVESTIGATION_DENIED


def test_rejected_by_slot_does_not_save_link(tmp_path, monkeypatch, probes, audits):
    """상한 거부(진행 중 1건)면 연결을 저장하지 않는다 — save는 슬롯 검사 뒤."""
    _seed(tmp_path)
    _inject(monkeypatch, _FakeClient())
    app = _app(_host_config(tmp_path), GONGJON_USER)
    client = TestClient(app)
    assert client.post(LINK, json=SELECT).status_code == 200
    import asyncio

    store = app.state.host_link_store
    asyncio.run(store.delete("cm_prd", POD))
    assert client.post(LINK, json=SELECT).status_code == 429
    assert asyncio.run(store.get("cm_prd", POD)) is None


def test_rerun_carries_prior_readings(tmp_path, monkeypatch, probes, audits):
    _seed_alarm(
        tmp_path, db_id=APM_DB, server_name=POD, hostname=POD, resource_type="apm.Instance",
        alarm_name="SQL_TOOMANY_FETCH",
    )
    readings = {"S2": "ok"}
    _store(tmp_path).record(
        investigation_id="inv-1", alarm_ids=["a1"], status="done", source="inline",
        briefing={"playbook": {"sections": SECTIONS, "readings": readings}},
        alarm_db_ids={"a1": APM_DB},
    )
    fake = _FakeClient()
    _inject(monkeypatch, fake)
    res = TestClient(_app(_host_config(tmp_path), ADMIN)).post(LINK, json=SELECT)
    assert res.status_code == 200
    assert fake.submit_calls[0]["meta"]["hints"]["rerun"]["prior_readings"] == readings


def test_rate_limit_shared_across_app_instances(tmp_path, probes):
    """(plans/196 잔여 5) 두 앱(별도 app.state · 프로세스 대역)이 같은 Redis로 상한을 공유한다."""
    _seed(tmp_path)
    redis = FakeAsyncRedis()
    apps = [_app(_host_config(tmp_path), GONGJON_USER) for _ in range(2)]
    for app in apps:
        app.state.host_link_store = HostLinkStore(redis)
    clients = [TestClient(app) for app in apps]
    codes = [clients[i % 2].get(CAND).status_code for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    body = clients[0].get(CAND).json()["detail"]
    assert body["code"] == "host_link_rate_limited" and body["retry_after_seconds"] >= 1
    assert all(getattr(app.state, "host_link_rate", None) is None for app in apps)


def test_rate_limit_falls_back_in_process_on_redis_error(tmp_path, probes, caplog):
    """Redis 카운터 오류면 상한을 열지 않고 프로세스 내 카운터로 센다(warning)."""
    _seed(tmp_path)
    app = _app(_host_config(tmp_path), GONGJON_USER)
    store = app.state.host_link_store

    async def _down(*a, **kw):  # noqa: ANN002, ANN003, ANN202
        raise ConnectionError("redis down")

    store.rate_hit = _down
    client = TestClient(app)
    codes = [client.get(CAND).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    assert app.state.host_link_rate
    assert "호출 상한 Redis 카운터 실패" in caplog.text
