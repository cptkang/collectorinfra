"""plans/187 W12 — `fault_diagnosis`의 채팅 증상 질의 조사 분기(`SRE_CHAT_INVESTIGATION_ENABLED`).

- 플래그 off(키 없음·False): 종전 동기 경로 비트 동일(응답 dict가 같다 · disclosures 키 없음).
- on · 플레이북 경로: 가상 알람 페이로드 submit ·
  저장 `kind=manual`·`origin=chat`·`parent_alarm_id` 없음 ·
  알람·결정 저장소 쓰기 0 · 카드 ref(조사 id·경로 표기·엔진·대상).
- on · HolmesGPT 경로: 비동기 `sre_diagnose`(질문 원문 · 식별된 대상만) · 저장 `kind=diagnosis`.
- 지시어만 있는 질의 = HolmesGPT · 승계 대상을 넘기지 않는다.
- 상한 공유(W10/W11 사용자별 카운터) · 권한 거부 사유 · 서비스 미가용 사유.

sre_agent는 import하지 않는다 — MCP 계약을 모사한 페이크 클라이언트를 쓴다(과금·실 LLM 0).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.nodes.fault_diagnosis as fd
from noise_gate.infrastructure.investigation_store import InvestigationStore
from src.nodes.fault_diagnosis import fault_diagnosis


class FakeClient:
    """sre_agent MCP 계약(submit/diagnose/poll) 페이크."""

    def __init__(self, *, poll_status: str = "running", sub_status: str = "accepted") -> None:
        self.poll_status = poll_status
        self.sub_status = sub_status
        self.submits: list[dict] = []
        self.diagnoses: list[dict] = []
        self.polls: list[str] = []
        self.unreachable: str | None = None

    async def unreachable_reason(self):
        return self.unreachable

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def submit(self, payload: dict) -> dict:
        self.submits.append(payload)
        if self.sub_status == "rejected":
            return {"status": "rejected", "investigation_id": None, "reason": "계약 위반"}
        return {"status": self.sub_status, "investigation_id": f"inv-pb-{len(self.submits)}"}

    async def diagnose(self, question, server_name=None, hostname=None, db_id=None, **kw) -> dict:
        self.diagnoses.append(
            {"question": question, "server_name": server_name, "hostname": hostname,
             "db_id": db_id, **kw}
        )
        return {"status": "accepted", "investigation_id": f"inv-dx-{len(self.diagnoses)}"}

    async def poll(self, investigation_id: str) -> dict:
        self.polls.append(investigation_id)
        if self.poll_status == "done":
            return {"status": "done", "answer": "web01 CPU 포화"}
        return {"status": self.poll_status}


def _gate(tmp_path, **over):
    base = dict(
        fault_diagnosis_enabled=True,
        investigation_service_url="http://localhost:9098/sse",
        investigation_service_token="",
        investigation_mcp_call_timeout_seconds=5.0,
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
        investigation_store_path=str(tmp_path / "inv.jsonl"),
        investigation_store_max_lines=2000,
        investigation_additional_user_hourly_limit=3,
        decision_store_path=str(tmp_path / "decisions.jsonl"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _app(gate, *, authz_mode: str = "admin_only"):
    return SimpleNamespace(
        noise_gate=gate,
        host_authz=SimpleNamespace(mode=authz_mode),
        composite=SimpleNamespace(
            prior_targets_enabled=False,
            max_targets=10,
            availability_precheck_enabled=False,
            availability_block_on_unavailable=False,
        ),
    )


def _state(query: str = "web01 CPU 사용률 원인 분석해줘", **extra) -> dict:
    st = {
        "user_query": query,
        "active_db_id": "polestar",
        "parsed_requirements": {"filter_conditions": [{"field": "hostname", "value": "web01"}]},
        "conversation_context": None,
        "user_role": "admin",
        "user_id": "admin-1",
    }
    st.update(extra)
    return st


@pytest.fixture
def client(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(fd, "_build_client", lambda gate_cfg: fake)
    return fake


@pytest.fixture
def audit(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(fd, "log_investigation", mock)
    return mock


@pytest.fixture
def no_decision_store(monkeypatch):
    """결정 저장소를 열면 실패하게 한다(가상 알람 쓰기 0 단언)."""
    import noise_gate.infrastructure.decision_store as ds

    def _boom(*a, **k):
        raise AssertionError("결정 저장소에 접근했다")

    monkeypatch.setattr(ds.DecisionStore, "__init__", _boom)


def _store(tmp_path) -> InvestigationStore:
    return InvestigationStore(str(tmp_path / "inv.jsonl"), 2000)


# ── 플래그 off: 비트 동일 ────────────────────────────────────────────────────


class TestFlagOff:
    async def test_off_equals_absent(self, tmp_path, client, audit):
        client.poll_status = "done"
        gate_absent = _gate(tmp_path)
        gate_false = _gate(tmp_path, sre_chat_investigation_enabled=False)
        a = await fault_diagnosis(_state(), app_config=_app(gate_absent))
        b = await fault_diagnosis(_state(), app_config=_app(gate_false))
        assert a["final_response"] == b["final_response"] == "web01 CPU 포화"
        assert {k: v for k, v in a.items() if k != "messages"} == {
            k: v for k, v in b.items() if k != "messages"
        }
        assert "disclosures" not in a and "disclosures" not in b
        # 동기 경로 — diagnose + poll, submit 없음, 저장소 쓰기 없음.
        assert len(client.diagnoses) == 2 and client.polls and not client.submits
        assert not (tmp_path / "inv.jsonl").exists()


# ── 플래그 on ───────────────────────────────────────────────────────────────


class TestPlaybookRoute:
    async def test_playbook_submit_and_record(self, tmp_path, client, audit, no_decision_store):
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))

        # 즉시 응답 — poll 없음 · 동기 diagnose 없음.
        assert client.polls == [] and client.diagnoses == []
        assert len(client.submits) == 1
        payload = client.submits[0]
        assert payload["contract_version"] == "1"
        assert payload["event"]["hostname"] == "web01"
        assert payload["event"]["alarmName"] == "CPU 사용률"
        assert payload["event"]["alarmId"] == ""
        assert payload["meta"]["virtual"] is True
        assert payload["meta"]["playbook_hint"] == "host.cpu"
        assert payload["meta"]["investigation_kind"] == "manual"
        assert payload["decision"]["fingerprint"].startswith("chat:")

        rec = _store(tmp_path).get_by_investigation("inv-pb-1")
        assert rec["kind"] == "manual"
        assert rec["origin"] == "chat"
        assert rec["owner_sub"] == "admin-1"
        assert rec["alarm_ids"] == []
        assert "parent_alarm_id" not in rec
        assert rec["subject"]["playbook"] == "host.cpu"
        assert rec["subject"]["route_label"] == "플레이북 host.cpu — 대상·증상 일치"

        # 결정 저장소 파일도 생기지 않는다(알람·결정 저장소 쓰기 0).
        assert not (tmp_path / "decisions.jsonl").exists()

        # 응답 텍스트 1~2줄 + 카드 ref.
        assert "플레이북 host.cpu — 대상·증상 일치" in out["final_response"]
        assert out["final_response"].count("\n") <= 1
        (card,) = out["disclosures"]
        assert card["kind"] == "investigation_ref"
        assert card["ref"] == {
            "investigation_id": "inv-pb-1",
            "status": "submitted",
            "route": "playbook",
            "engine": "플레이북",
            "route_label": "플레이북 host.cpu — 대상·증상 일치",
            "reason": "matched",
            "target": "web01",
            "playbook": "host.cpu",
            "alarm_kind": "cpu",
        }
        assert all(isinstance(v, str) for v in card["ref"].values())
        assert audit.await_args.kwargs["outcome"] == "ok"

    async def test_reference_time_from_query(self, tmp_path, client, audit):
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        await fault_diagnosis(
            _state("web01 CPU 사용률 오늘 14:05 원인 분석해줘"), app_config=_app(gate)
        )
        assert client.submits[0]["event"]["alarmTime"].endswith("140500")

    async def test_rejected_is_reported(self, tmp_path, client, audit):
        client.sub_status = "rejected"
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert "계약 위반" in out["final_response"]
        assert "disclosures" not in out
        assert not (tmp_path / "inv.jsonl").exists()

    async def test_rejected_with_id_shows_reason_text(self, tmp_path, client, audit):
        # plans/194 W1: 즉시 거부(조사 id 있음)도 사유 코드가 아니라 문구로 보인다.
        from src.domain.investigation_reject_reasons import REJECT_REASON_TEXTS

        async def _submit(payload: dict) -> dict:
            client.submits.append(payload)
            return {"status": "rejected", "investigation_id": "inv-rj", "reason": "queue_full"}

        client.submit = _submit
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert REJECT_REASON_TEXTS["queue_full"] in out["final_response"]
        assert "사유: queue_full" not in out["final_response"]

    async def test_rejected_real_shape_verdict_shows_table_text(self, tmp_path, client, audit):
        # plans/194 W1 교정: 실 sre는 「조사 거부 — <코드>」 verdict를 함께 싣는다 — 표 문구 우선.
        from src.domain.investigation_reject_reasons import REJECT_REASON_TEXTS

        async def _submit(payload: dict) -> dict:
            client.submits.append(payload)
            return {"status": "rejected", "investigation_id": "inv-rj",
                    "reason": "host_investigation_in_flight",
                    "verdict": "조사 거부 — host_investigation_in_flight"}

        client.submit = _submit
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert REJECT_REASON_TEXTS["host_investigation_in_flight"] in out["final_response"]
        assert "조사 거부 — host_investigation_in_flight" not in out["final_response"]


class TestHolmesRoute:
    async def test_type_unresolved_goes_to_diagnose(
        self, tmp_path, client, audit, no_decision_store
    ):
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        q = "web01 서버 상태 원인 분석해줘"
        out = await fault_diagnosis(_state(q), app_config=_app(gate))
        assert client.submits == [] and client.polls == []
        assert client.diagnoses == [
            {"question": q, "server_name": None, "hostname": "web01", "db_id": "polestar"}
        ]
        rec = _store(tmp_path).get_by_investigation("inv-dx-1")
        assert rec["kind"] == "diagnosis"
        assert rec["origin"] == "chat"
        assert rec["owner_sub"] == "admin-1"
        assert "parent_investigation_id" not in rec and "parent_alarm_id" not in rec
        assert out["disclosures"][0]["ref"]["route_label"] == "HolmesGPT — 증상 유형 미확정"
        assert out["disclosures"][0]["ref"]["engine"] == "HolmesGPT"

    async def test_demonstrative_only_is_holmes_without_inherited_target(
        self, tmp_path, client, audit
    ):
        """지시어(「그 서버」)는 식별자가 아니다 — 직전 턴 대상이 있어도 플레이북으로 안 간다."""
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        q = "그 서버 CPU 사용률 원인 분석해줘"
        state = _state(
            q,
            parsed_requirements={"filter_conditions": [{"field": "hostname", "value": "그 서버"}]},
            conversation_context={
                "previous_entities": [{"field": "hostname", "value": "web01"}],
            },
        )
        out = await fault_diagnosis(state, app_config=_app(gate))
        assert client.submits == []
        assert client.diagnoses == [
            {"question": q, "server_name": None, "hostname": None, "db_id": None}
        ]
        assert out["disclosures"][0]["ref"]["route_label"] == "HolmesGPT — 대상 미확정"
        assert "target" not in out["disclosures"][0]["ref"]


class TestGuards:
    async def test_shared_inflight_limit(self, tmp_path, client, audit):
        """W10/W11과 같은 사용자별 카운터 — 진행 중 사용자 요청 조사가 있으면 사유 응답."""
        _store(tmp_path).record(
            investigation_id="inv-prev", alarm_ids=["a1"], kind="manual", status="submitted",
            source="manual", owner_sub="admin-1", origin="manual", parent_alarm_id="a1",
        )
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert client.polls == ["inv-prev"]  # 미종결은 재조회로 먼저 갱신
        assert client.submits == [] and client.diagnoses == []
        assert "진행 중인 사용자 요청 조사" in out["final_response"]
        assert "disclosures" not in out
        assert audit.await_args.kwargs["outcome"] == "denied"
        # 재조회는 저장하지 않는다(종결 행 저장은 조회 라우트 소관).
        assert _store(tmp_path).get_by_investigation("inv-prev")["status"] == "submitted"

    async def test_shared_hourly_limit(self, tmp_path, client, audit):
        store = _store(tmp_path)
        for i in range(3):
            store.record(
                investigation_id=f"inv-{i}", alarm_ids=[], kind="diagnosis", status="done",
                source="investigation_chat", owner_sub="admin-1", origin="investigation_chat",
            )
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert client.submits == []
        assert "시간당 사용자 요청 조사 상한(3건)" in out["final_response"]

    async def test_finished_inflight_does_not_block(self, tmp_path, client, audit):
        _store(tmp_path).record(
            investigation_id="inv-prev", alarm_ids=[], kind="diagnosis", status="submitted",
            source="chat", owner_sub="admin-1", origin="chat",
        )
        client.poll_status = "done"
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert len(client.submits) == 1
        assert out["disclosures"][0]["ref"]["investigation_id"] == "inv-pb-1"

    async def test_authz_denied_reason(self, tmp_path, client, audit):
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(user_role="user"), app_config=_app(gate))
        assert "관리자 권한이 필요한" in out["final_response"]
        assert client.submits == [] and client.diagnoses == []
        assert not (tmp_path / "inv.jsonl").exists()

    async def test_empty_user_id_is_rejected_fail_closed(self, tmp_path, client, audit):
        """(교정 지적 5) 요청자 식별자가 없으면 상한을 셀 수 없어 접수하지 않는다(사유 · 감사)."""
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(user_id=None), app_config=_app(gate))
        assert client.submits == [] and client.diagnoses == []
        assert "요청자 식별 정보" in out["final_response"]
        assert audit.await_args.kwargs["degraded"][0]["reason"] == "owner_unknown"
        assert not (tmp_path / "inv.jsonl").exists()

    async def test_multi_target_any_denied_rejects(self, tmp_path, client, audit, monkeypatch):
        """(교정 M4) 대상 2개 이상이면 모든 대상에 인가 — 하나라도 거부면 접수 0(사유 · 감사)."""
        monkeypatch.setattr(fd, "_explicit_targets", lambda state, cfg: [
            {"hostname": "web01", "db_id": "polestar"},
            {"hostname": "db99", "db_id": "polestar_b0"},
        ])
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(allowed_db_ids=["polestar"]), app_config=_app(gate))
        assert client.submits == [] and client.diagnoses == []
        assert "조회 인가된 범위 밖" in out["final_response"]
        kwargs = audit.await_args.kwargs
        assert kwargs["outcome"] == "denied" and kwargs["authz"]["allowed"] is False
        assert kwargs["degraded"][0] == {
            "reason": "target_authz_denied", "detail": "db_not_allowed", "target": "db99",
        }
        assert not (tmp_path / "inv.jsonl").exists()

    async def test_multi_target_all_allowed_submits(self, tmp_path, client, audit, monkeypatch):
        monkeypatch.setattr(fd, "_explicit_targets", lambda state, cfg: [
            {"hostname": "web01", "db_id": "polestar"},
            {"hostname": "web02", "db_id": "polestar"},
        ])
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(allowed_db_ids=["polestar"]), app_config=_app(gate))
        assert len(client.diagnoses) == 1 and client.diagnoses[0]["hostname"] is None
        assert out["disclosures"][0]["ref"]["investigation_id"] == "inv-dx-1"

    async def test_service_unreachable_reason(self, tmp_path, client, audit):
        client.unreachable = "connection refused"
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True)
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert "connection refused" in out["final_response"]
        assert client.submits == []

    async def test_store_unconfigured_reason(self, tmp_path, client, audit):
        gate = _gate(tmp_path, sre_chat_investigation_enabled=True, investigation_store_path="")
        out = await fault_diagnosis(_state(), app_config=_app(gate))
        assert "조사 저장소" in out["final_response"]
        assert client.submits == []
