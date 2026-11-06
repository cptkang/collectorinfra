"""plans/215 W11 — D3 서비스 심층 조사 접수·진단 이력 (§4.9 · §13 수용 16).

거부 사유 3종(권한·상한·대표 없음) · 같은 서비스 재접수 = 기존 id · 질문 템플릿 사용자 입력 0 ·
존 밖 비교 구성원 제외·건수 · `context.biz_service`가 D2 모양 · 저장 `kind=diagnosis`+`subject` ·
이력(소유자/관리자) · 클라이언트 `context` 미전달 시 요청 인자 종전 동일을 본다.
조사 서비스는 가짜 클라이언트, 상한 임계구역·감사는 대역 콜백이다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.application import biz_service_investigation as bsi
from noise_gate.domain.biz_service_keys import KIND_APM_INSTANCE, KIND_SMS_SERVER, sms_member_key
from noise_gate.infrastructure.investigation_store import InvestigationStore
from noise_gate.infrastructure.sre_agent_client import SreAgentClient


def _cfg(active: bool = True, mode: str = "admin_only") -> Any:
    return SimpleNamespace(
        noise_gate=SimpleNamespace(
            biz_diagnosis_active=active, biz_diagnosis_window_minutes=15,
            biz_investigation_max_sms=10, biz_investigation_max_apm=8,
            investigation_mcp_call_timeout_seconds=5.0,
        ),
        host_authz=SimpleNamespace(mode=mode),
    )


def _sms(rid: str, zone: str, db_id: str = "polestar_cm_gp") -> dict[str, Any]:
    return {"kind": KIND_SMS_SERVER, "member_key": sms_member_key(db_id, rid), "db_id": db_id,
            "resource_id": rid, "server_name": f"SVR{rid}", "hostname": f"svr{rid}",
            "zone": zone, "confidence": "confirmed"}


def _service() -> dict[str, Any]:
    return {
        "id": 7, "display_name": "인터넷뱅킹",
        "members": [
            _sms("1", "gongjon"), _sms("2", "gongjon"), _sms("3", "bankjon", "polestar_b0"),
            {"kind": KIND_APM_INSTANCE, "member_key": "apm|x", "source_id": "default",
             "domain_id": "3000", "instance_name": "C1", "zone": "bankjon",
             "confidence": "inferred"},
        ],
    }


def _d1(**kw: Any) -> dict[str, Any]:
    """W9 `diagnose_service` 출력 모양(필요한 키만)."""
    k1, k2 = sms_member_key("polestar_cm_gp", "1"), sms_member_key("polestar_cm_gp", "2")
    value: dict[str, Any] = {
        "verdict": "R2", "verdict_text": "…", "hypothesis_label": "가설 · 참고용",
        "representative": {"kind": KIND_SMS_SERVER, "member_key": k1, "name": "SVR1",
                           "db_id": "polestar_cm_gp", "resource_id": "1", "rule": "first_sms",
                           "rule_label": "최초 이상 서버"},
        "representative_reason": "최초 이상 서버",
        "anomalous_members": [
            {"kind": KIND_SMS_SERVER, "member_key": k1, "name": "SVR1", "layer": "",
             "db_id": "polestar_cm_gp", "resource_id": "1",
             "first_at": "2026-11-05T01:00:00+00:00", "signals": [], "linked_servers": []},
            {"kind": KIND_SMS_SERVER, "member_key": k2, "name": "SVR2", "layer": "",
             "db_id": "polestar_cm_gp", "resource_id": "2",
             "first_at": "2026-11-05T01:03:00+00:00", "signals": [], "linked_servers": []},
        ],
        "timeline": [
            {"member_key": k1, "kind": KIND_SMS_SERVER, "name": "SVR1", "layer": "",
             "at": "2026-11-05T01:00:00+00:00", "basis": "alarm", "label": "알람 a1",
             "time_basis": "occurred"},
            {"member_key": k2, "kind": KIND_SMS_SERVER, "name": "SVR2", "layer": "",
             "at": "2026-11-05T01:03:00+00:00", "basis": "alarm", "label": "알람 a2",
             "time_basis": "occurred"},
        ],
        "gaps": [{"code": "no_metric", "label": "지표 없음", "count": 1,
                  "member_keys": [sms_member_key("polestar_b0", "3")]}],
    }
    value.update(kw)
    return value


class _Client:
    def __init__(self, res: dict[str, Any] | None = None, unreachable: str | None = None) -> None:
        self.res = res or {"investigation_id": "inv-1", "status": "accepted"}
        self.unreachable = unreachable
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def unreachable_reason(self) -> str | None:
        return self.unreachable

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def diagnose(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((args, kwargs))
        return self.res


def _slot(limited: tuple[str, str] | None = None):  # noqa: ANN202
    @asynccontextmanager
    async def _cm():  # noqa: ANN202
        yield limited
    return _cm


class _Audit:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    async def __call__(self, **fields: Any) -> None:
        self.rows.append(fields)


ADMIN = bsi.BizRequester(sub="admin1", role="admin", zones=None, is_admin=True)
GONG_ADMIN = bsi.BizRequester(sub="admin1", role="admin", zones=frozenset({"gongjon"}),
                              is_admin=True)
USER = bsi.BizRequester(sub="u1", role="user", zones=frozenset({"gongjon"}))


@pytest.fixture
def store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"))


async def _submit(store: InvestigationStore, *, requester=ADMIN, d1=None, client=None,
                  limited=None, cfg=None, audit=None, service="default"):  # noqa: ANN001,ANN202
    return await bsi.submit_biz_service_investigation(
        cfg=cfg or _cfg(), service=_service() if service == "default" else service,
        d1=_d1() if d1 is None else d1, requester=requester, store=store,
        client=_Client() if client is None else client, limit_slot=_slot(limited), audit=audit,
    )


# ── 정상 접수 ──────────────────────────────────────────────────────


async def test_submit_payload_and_record(store) -> None:
    client, audit = _Client(), _Audit()
    res = await _submit(store, requester=GONG_ADMIN, client=client, audit=audit)
    assert res.ok, res.body
    assert res.body["investigation_id"] == "inv-1" and res.body["status"] == "submitted"
    assert res.body["reused"] is False
    assert res.body["representative"] == {"db_id": "polestar_cm_gp", "server_name": "SVR1",
                                          "hostname": "svr1"}
    (args, kw), = client.calls
    assert kw["hostname"] == "svr1" and kw["db_id"] == "polestar_cm_gp"
    assert kw["reference_time"] == "2026-11-05T01:00:00+00:00"
    assert kw["lookback_minutes"] == 15
    biz = kw["context"]["biz_service"]
    # D2와 같은 모양(새 모양 금지)
    assert set(biz) == {"service_id", "display_name", "confidence", "sms_members", "apm_members",
                        "truncated", "d1_verdict"}
    # 대표 자신 제외 · 존 밖(bankjon SVR3·C1) 제외 · D1 이상 구성원이 앞
    assert [m["server_name"] for m in biz["sms_members"]] == ["SVR2"]
    assert kw["server_name"] == "SVR1"  # 대표 이름은 서비스 사본 구성원에서 찾는다
    assert biz["apm_members"] == []
    assert res.body["excluded_members"] == 2
    pf = kw["prior_findings"]
    assert pf["verdict"] == {"level": "R2"} and pf["source"] == bsi.PRIOR_SOURCE
    assert pf["readings"]["excluded_out_of_zone"] == 2
    assert pf["readings"]["anomalous_sms"] == 2 and pf["readings"]["gap_no_metric"] == 1
    assert pf["window"] == {"reference_time": "2026-11-05T01:00:00+00:00",
                            "lookback_minutes": 15}
    rec = store.get_by_investigation("inv-1")
    assert rec["kind"] == "diagnosis" and rec["origin"] == bsi.ORIGIN_BIZ_SERVICE
    assert rec["owner_sub"] == "admin1"
    assert rec["subject"]["type"] == "biz_service" and rec["subject"]["service_id"] == 7
    assert rec["subject"]["display_name"] == "인터넷뱅킹"
    assert rec["subject"]["service_key"] == "biz:7"
    assert rec["subject"]["excluded_members"] == 2
    assert audit.rows[-1]["outcome"] == bsi.AUDIT_OK
    assert audit.rows[-1]["investigation_id"] == "inv-1"


async def test_all_zones_requester_keeps_members(store) -> None:
    client = _Client()
    res = await _submit(store, client=client)
    biz = client.calls[0][1]["context"]["biz_service"]
    assert [m["server_name"] for m in biz["sms_members"]] == ["SVR2", "SVR3"]
    assert [m["instance_name"] for m in biz["apm_members"]] == ["C1"]
    assert res.body["excluded_members"] == 0


def test_question_has_no_user_text() -> None:
    q = bsi.build_question(7, "svr1", 15)
    assert q == bsi.QUESTION_TEMPLATE.format(service_id=7, target="svr1", window=15)
    assert "인터넷뱅킹" not in q


async def test_question_uses_template_only(store) -> None:
    client = _Client()
    await _submit(store, client=client)
    (args, _kw), = client.calls
    assert args == (bsi.build_question(7, "svr1", 15),)


# ── 거부 사유 3종 ───────────────────────────────────────────────────


async def test_authz_denied_403_and_audited(store) -> None:
    audit, client = _Audit(), _Client()
    res = await _submit(store, requester=USER, client=client, audit=audit)
    assert res.http_status == 403 and res.body["code"] == bsi.REASON_AUTHZ_DENIED
    assert client.calls == []
    assert audit.rows[-1]["outcome"] == bsi.AUDIT_DENIED
    assert audit.rows[-1]["authz"]["allowed"] is False
    assert store.list_diagnoses() == []


async def test_limit_denied_429(store) -> None:
    audit = _Audit()
    res = await _submit(store, limited=("hourly_limit", "시간당 상한"), audit=audit)
    assert res.http_status == 429 and res.body["code"] == "hourly_limit"
    assert audit.rows[-1]["degraded"][0]["reason"] == "hourly_limit"
    res2 = await _submit(store, limited=("owner_unknown", "sub 없음"))
    assert res2.http_status == 403


async def test_no_representative_409(store) -> None:
    audit, client = _Audit(), _Client()
    d1 = _d1(representative=None, representative_reason="이상 구성원 없음")
    res = await _submit(store, d1=d1, client=client, audit=audit)
    assert res.http_status == 409 and res.body == {"code": bsi.REASON_NO_REPRESENTATIVE,
                                                   "reason": "이상 구성원 없음"}
    assert client.calls == [] and audit.rows[-1]["outcome"] == bsi.AUDIT_DENIED


async def test_disabled_and_missing_service(store) -> None:
    assert (await _submit(store, cfg=_cfg(active=False))).body["code"] == bsi.REASON_DISABLED
    assert (await _submit(store, service=None)).body["code"] == bsi.REASON_SERVICE_NOT_FOUND


async def test_service_unconfigured_and_unreachable(store) -> None:
    res = await bsi.submit_biz_service_investigation(
        cfg=_cfg(), service=_service(), d1=_d1(), requester=ADMIN, store=store, client=None,
        limit_slot=_slot(),
    )
    assert res.http_status == 503 and res.body["code"] == bsi.REASON_SERVICE_UNCONFIGURED
    res2 = await _submit(store, client=_Client(unreachable="refused"))
    assert res2.body["code"] == bsi.REASON_SERVICE_UNREACHABLE


async def test_service_rejection_recorded_with_reason_text(store) -> None:
    client = _Client({"investigation_id": "inv-r", "status": "rejected",
                      "reason": "hourly_budget_exceeded", "verdict": "조사 거부"})
    res = await _submit(store, client=client)
    assert res.ok and res.body["status"] == "rejected"
    assert res.body["reason"] == "hourly_budget_exceeded" and res.body["reason_text"]
    assert store.get_by_investigation("inv-r")["status"] == "rejected"


# ── 같은 서비스 재접수 ───────────────────────────────────────────────


async def test_same_service_reuses_existing_id(store) -> None:
    first = await _submit(store)
    client, audit = _Client({"investigation_id": "inv-2", "status": "accepted"}), _Audit()
    again = await _submit(store, client=client, audit=audit)
    assert again.ok and again.body["reused"] is True
    assert again.body["investigation_id"] == first.body["investigation_id"]
    assert client.calls == []
    # (보안 L1) 재사용도 감사 1줄
    (row,) = audit.rows
    assert row["outcome"] == bsi.AUDIT_OK and row["reused"] is True
    assert row["investigation_id"] == first.body["investigation_id"]


async def test_other_users_in_flight_is_not_disclosed(store) -> None:
    await _submit(store)  # 관리자 admin1 조사 진행 중
    other = bsi.BizRequester(sub="admin2", role="admin", zones=None, is_admin=False)
    res = await _submit(store, requester=other)
    assert res.http_status == 409 and res.body["code"] == bsi.REASON_SERVICE_IN_FLIGHT
    assert "investigation_id" not in res.body


async def test_finished_investigation_allows_new_one(store) -> None:
    await _submit(store)
    store.record(investigation_id="inv-1", alarm_ids=[], kind="diagnosis", status="done",
                 source="api_poll")
    client = _Client({"investigation_id": "inv-2", "status": "accepted"})
    res = await _submit(store, client=client)
    assert res.body["investigation_id"] == "inv-2" and res.body["reused"] is False


async def test_refresh_terminal_allows_new_one(store) -> None:
    await _submit(store)

    async def _refresh(st, rec, ng):  # noqa: ANN001,ANN202
        return {**rec, "status": "done"}, None

    res = await bsi.submit_biz_service_investigation(
        cfg=_cfg(), service=_service(), d1=_d1(), requester=ADMIN, store=store,
        client=_Client({"investigation_id": "inv-3", "status": "accepted"}),
        limit_slot=_slot(), refresh=_refresh,
    )
    assert res.body["investigation_id"] == "inv-3"


# ── D1 해석 ────────────────────────────────────────────────────────


def test_first_anomaly_from_timeline() -> None:
    d1 = {"timeline": [{"at": "2026-11-05T10:05:00+09:00"},
                       {"at": "2026-11-05T10:01:00+09:00"}, {"x": 1}]}
    assert bsi.first_anomaly_time(d1) == "2026-11-05T10:01:00+09:00"
    assert bsi.first_anomaly_time({"timeline": [{"at": "bad"}]}) is None


def test_first_anomaly_falls_back_to_anomalous_members() -> None:
    d1 = {"timeline": [], "anomalous_members": [{"first_at": "2026-11-05T01:02:00+00:00"}]}
    assert bsi.first_anomaly_time(d1) == "2026-11-05T01:02:00+00:00"


def test_representative_requires_db_and_name() -> None:
    rep, reason = bsi.representative_of({"representative": {"db_id": "x"}})
    assert rep is None and reason


def test_representative_names_from_service_copy() -> None:
    rep, _ = bsi.representative_of(_d1(), _service())
    assert rep == {"db_id": "polestar_cm_gp", "resource_id": "1", "server_name": "SVR1",
                   "hostname": "svr1"}
    # member_key가 어긋나도 (db_id, resource_id)로 찾는다
    d1 = _d1(representative={**_d1()["representative"], "member_key": "?"})
    assert bsi.representative_of(d1, _service())[0]["hostname"] == "svr1"


async def test_integration_with_w9_diagnose_service(store) -> None:
    """W9 `diagnose_service` 실제 출력 1건으로 접수 — 대표·최초 이상 시각·판정이 그대로 닿는다."""
    from noise_gate.domain.biz_diagnosis import DiagAlarm, DiagMember, diagnose_service

    now = 1_762_300_000.0
    members = [
        DiagMember(KIND_SMS_SERVER, sms_member_key("polestar_cm_gp", r), f"SVR{r}", "",
                   "polestar_cm_gp", r)
        for r in ("1", "2")
    ]
    alarms = [
        DiagAlarm(sms_member_key("polestar_cm_gp", "2"), "a2", 2, now - 120),
        DiagAlarm(sms_member_key("polestar_cm_gp", "1"), "a1", 2, now - 300),
    ]
    d1 = diagnose_service(members=members, alarms=alarms, now=now, window_minutes=15)
    assert d1["representative"]["resource_id"] == "1"
    client = _Client()
    res = await _submit(store, d1=d1, client=client)
    assert res.ok, res.body
    kw = client.calls[0][1]
    assert kw["hostname"] == "svr1" and kw["server_name"] == "SVR1"
    expected = datetime.fromtimestamp(now - 300, UTC).isoformat(timespec="seconds")
    assert kw["reference_time"] == bsi.first_anomaly_time(d1) == expected
    assert kw["prior_findings"]["verdict"] == {"level": d1["verdict"]}


# ── 진단 이력 ──────────────────────────────────────────────────────


async def test_history_owner_or_admin(store) -> None:
    await _submit(store)
    store.record(investigation_id="other", alarm_ids=[], kind="diagnosis", status="submitted",
                 source="followup", owner_sub="admin1", subject={"hostname": "x"})
    admin_items = bsi.list_biz_service_investigations(store, 7, ADMIN)
    assert [i["investigation_id"] for i in admin_items] == ["inv-1"]
    assert bsi.list_biz_service_investigations(store, 7, USER) == []
    assert bsi.list_biz_service_investigations(store, 8, ADMIN) == []
    anon = bsi.BizRequester(sub="", role=None)
    assert bsi.list_biz_service_investigations(store, 7, anon) == []


async def test_history_limit_newest_first(store) -> None:
    for i in range(12):
        store.record(investigation_id=f"i{i:02d}", alarm_ids=[], kind="diagnosis",
                     status="done", source=bsi.ORIGIN_BIZ_SERVICE, owner_sub="u1",
                     subject={"type": "biz_service", "service_id": 7})
    items = bsi.list_biz_service_investigations(store, 7, USER, mask_fn=lambda s: s)
    assert len(items) == bsi.HISTORY_LIMIT
    assert items[0]["investigation_id"] == "i11"


# ── 클라이언트 context 가산 ──────────────────────────────────────────


class _Recorder(SreAgentClient):
    def __init__(self) -> None:  # 연결 없이 인자만 기록
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def _call_tool_json(self, tool_name: str, arguments: dict) -> dict:
        self.sent.append((tool_name, arguments))
        return {"investigation_id": "x", "status": "accepted"}


async def test_client_without_context_sends_same_arguments() -> None:
    c = _Recorder()
    await c.diagnose("q", server_name="S", db_id="d", prior_findings={"a": 1})
    assert c.sent == [("sre_diagnose", {"question": "q", "server_name": "S", "db_id": "d",
                                        "prior_findings": {"a": 1}})]


async def test_client_context_sent_when_given() -> None:
    c = _Recorder()
    await c.diagnose("q", context={"biz_service": {"service_id": 7}})
    assert c.sent[0][1]["context"] == {"biz_service": {"service_id": 7}}


# ── 서로 다른 사용자 동시 접수 ───────────────────────────────────────


class _SlowClient(_Client):
    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()

    async def diagnose(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((args, kwargs))
        await self.gate.wait()
        return self.res


async def test_concurrent_users_same_service_submit_once(store) -> None:
    client = _SlowClient()
    a = bsi.BizRequester(sub="admin1", role="admin")
    b = bsi.BizRequester(sub="admin2", role="admin")
    t1 = asyncio.create_task(_submit(store, requester=a, client=client))
    t2 = asyncio.create_task(_submit(store, requester=b, client=client))
    await asyncio.sleep(0.05)
    assert len(client.calls) == 1  # 두 번째는 서비스 잠금에서 기다린다
    client.gate.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1.ok and r1.body["investigation_id"] == "inv-1"
    assert r2.http_status == 409 and r2.body["code"] == bsi.REASON_SERVICE_IN_FLIGHT
    assert len(client.calls) == 1
    assert bsi._SERVICE_LOCKS == {}  # 쓰고 나면 정리된다
