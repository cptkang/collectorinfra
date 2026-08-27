"""Plan 64 Wave 3-A (CW-A) — 게이트 훅 → 조사 submit/poll → 브리핑 첨부 계약 테스트.

검증 범위(sre_agent 패키지 미import — MCP 클라이언트를 계약대로 모사한 FakeSreClient 사용):
  1. 페이로드 직렬화: build_trigger_payload가 contract_version "1"·event/decision/meta 구조.
  2. 트리거 노드: PAGE 결정 시 submit→poll→브리핑을 state에 싣고 감사한다.
  3. graceful: 서비스 다운/전체 타임아웃/거부 시 브리핑 미첨부·게이트 무영향·사유 감사.
  4. 옵트인/티어 게이트: off·min_tier 미달·클라이언트 미주입 시 no-op.
  5. dedup: 동일 fingerprint 재submit → duplicate(기존 조사 재사용).
  6. 브리핑 렌더: build_workb_body가 조사 브리핑 블록을 첨부한다.
  7. 감사: decision_store.record_investigation은 type="investigation"이라 티어 집계 불변.
  8. 그래프 배선: investigation_trigger_enabled일 때만 트리거 노드가 배선된다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes.alarm_notifier import build_workb_body
from noise_gate.application.nodes.investigation_trigger import (
    investigation_trigger_node,
)
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.investigation_payload import (
    CONTRACT_VERSION,
    build_trigger_payload,
)
from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_PAGE,
    TIER_TICKET,
)
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.orchestration.alarm_graph import build_alarm_graph


# ── 헬퍼 ────────────────────────────────────────────────────────────────
def _event(server: str = "srv-1", host: str = "h1", severity: int = 2) -> AlarmEvent:
    return AlarmEvent(
        db_id="db1", server_name=server, hostname=host, ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=severity,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="r1",
        alarm_name="CPU High", alarm_time=datetime(2026, 7, 27, 12, 0, 0),
        conditions=">90%", condition_log="cpu 95%",
    )


def _decision(tier: str = TIER_PAGE, fp: str = "fp-1", **sig) -> NotificationDecision:
    signals = {"root_resource": None}
    signals.update(sig)
    return NotificationDecision(
        tier=tier, reason="심각도2·중요도높음", priority=3, signals=signals, fingerprint=fp
    )


def _ng_cfg(**over) -> SimpleNamespace:
    base = dict(
        investigation_trigger_enabled=True,
        investigation_trigger_min_tier="PAGE",
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _app_cfg(ng: SimpleNamespace, *, authz_mode: str = "admin_only") -> SimpleNamespace:
    """앱 설정 대역.

    `host_authz`·`composite`는 실제 `AppConfig`가 **항상** 갖는 필드다(Plan 78 W3-5 · W1).
    대역에서 빼면 조사 인가가 `unknown_authz_mode`로 **차단**된다 — 그것이 fail-closed의
    설계 의도이므로, 대역을 프로덕션 형태에 맞춘다(정책을 느슨하게 하지 않는다).
    """
    return SimpleNamespace(
        noise_gate=ng,
        host_authz=SimpleNamespace(mode=authz_mode),
        composite=SimpleNamespace(prior_targets_enabled=False),
    )


def _state(decision, **extra) -> dict:
    st = {
        "alarm_event": _event(),
        "notification_decision": decision,
        "recurrence": None,
        "correlation_meta": None,
    }
    st.update(extra)
    return st


def _config(ng, client=None, store=None, *, authz_mode: str = "admin_only") -> dict:
    return {
        "configurable": {
            "app_config": _app_cfg(ng, authz_mode=authz_mode),
            "sre_agent_client": client,
            "decision_store": store,
        }
    }


class FakeSreClient:
    """sre_agent 조사 서비스 MCP 계약(submit/poll)을 모사한 인메모리 페이크.

    JobStore(sre-agent/05 §3)의 계약을 최소 재현한다: 필수 event 필드 결측 → rejected,
    동일 fingerprint 재submit → duplicate, 그 외 accepted 후 poll 시 브리핑 반환. sre_agent
    패키지를 import하지 않고 계약만 재현해 경계(패키지 import 0)를 지킨다.
    """

    def __init__(self, briefing=None, poll_status="stub", verdict=None,
                 connect_error=None, never_terminal=False) -> None:
        self.briefing = briefing if briefing is not None else {
            "stub": True, "message": "조사 미실행(스텁)", "elements": None
        }
        self.poll_status = poll_status
        self.verdict = verdict
        self.connect_error = connect_error
        self.never_terminal = never_terminal
        self._fp_index: dict[str, str] = {}
        self._next = 0
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.submit_calls: list[dict] = []
        self.poll_calls: list[str] = []

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def submit(self, payload: dict) -> dict:
        self.submit_calls.append(payload)
        if payload.get("contract_version") != CONTRACT_VERSION:
            return {"investigation_id": self._new_id(), "status": "rejected",
                    "reason": "contract_version 불일치"}
        event = payload.get("event") or {}
        missing = [f for f in ("serverName", "hostname", "severity")
                   if event.get(f) in (None, "")]
        if missing:
            return {"investigation_id": self._new_id(), "status": "rejected",
                    "reason": f"필수 event 필드 결측: {', '.join(missing)}"}
        fp = (payload.get("decision") or {}).get("fingerprint")
        if fp and fp in self._fp_index:
            return {"investigation_id": self._fp_index[fp], "status": "duplicate"}
        inv_id = self._new_id()
        if fp:
            self._fp_index[fp] = inv_id
        return {"investigation_id": inv_id, "status": "accepted"}

    async def poll(self, investigation_id: str) -> dict:
        self.poll_calls.append(investigation_id)
        if self.never_terminal:
            return {"status": "running"}
        return {"status": self.poll_status, "briefing": self.briefing,
                "verdict": self.verdict}

    def _new_id(self) -> str:
        self._next += 1
        return f"inv-{self._next}"


# ── 1. 페이로드 직렬화 ───────────────────────────────────────────────────
class TestBuildTriggerPayload:
    def test_contract_v1_structure(self):
        p = build_trigger_payload(
            _event(), _decision(),
            recurrence={"count": 3}, correlation_meta={"member_seq": 2},
            root_resource="R9",
        )
        assert p["contract_version"] == "1"
        ev = p["event"]
        assert ev["serverName"] == "srv-1" and ev["hostname"] == "h1"
        assert ev["severity"] == 2 and ev["alarmName"] == "CPU High"
        assert ev["alarmTime"] == "20260727120000"  # yyyyMMddHHmmss
        assert p["decision"]["tier"] == "page"
        assert p["decision"]["fingerprint"] == "fp-1"
        assert p["meta"] == {
            "recurrence": {"count": 3}, "cluster": {"member_seq": 2},
            "root_resource": "R9", "source": "collectorinfra",
        }

    def test_empty_identifiers_pass_through_for_service_rejection(self):
        # 필수 식별자 결측 이벤트는 빈 값으로 직렬화되어 조사 서비스가 rejected로 응답한다.
        p = build_trigger_payload(_event(server="", host=""), _decision())
        assert p["event"]["serverName"] == "" and p["event"]["hostname"] == ""


# ── 2. 트리거 노드 — 브리핑 첨부 ─────────────────────────────────────────
class TestNodeAttachesBriefing:
    async def test_page_submits_polls_and_attaches(self):
        client = FakeSreClient(
            briefing={"cause": "OOM", "recommendation": "힙 상향"},
            poll_status="done", verdict="escalate",
        )
        store = DecisionStore("unused", enabled=False)  # no-op 감사
        out = await investigation_trigger_node(
            _state(_decision()), _config(_ng_cfg(), client, store)
        )
        assert out == {"investigation_briefing": {"cause": "OOM", "recommendation": "힙 상향"}}
        assert client.connect_calls == 1 and client.disconnect_calls == 1
        assert len(client.submit_calls) == 1 and len(client.poll_calls) == 1
        # submit에 실린 페이로드가 계약(contract_version "1")을 만족.
        assert client.submit_calls[0]["contract_version"] == "1"

    async def test_stub_briefing_attached(self):
        client = FakeSreClient()  # 기본 스텁 브리핑
        out = await investigation_trigger_node(
            _state(_decision()), _config(_ng_cfg(), client)
        )
        assert out.get("investigation_briefing", {}).get("stub") is True


# ── 3. graceful — 다운/타임아웃/거부는 게이트 무영향 ─────────────────────
class TestGraceful:
    async def test_service_down_no_briefing_and_audited(self, tmp_path):
        client = FakeSreClient(connect_error=ConnectionRefusedError("service down"))
        store = DecisionStore(str(tmp_path / "audit.jsonl"), enabled=True)
        out = await investigation_trigger_node(
            _state(_decision()), _config(_ng_cfg(), client, store)
        )
        assert out == {}  # 브리핑 미첨부(게이트 통보·판정 무영향)
        # 사유 구조화 감사(침묵 금지) — status="down".
        import json
        recs = [json.loads(l) for l in (tmp_path / "audit.jsonl").read_text().splitlines()]
        assert recs and recs[-1]["type"] == "investigation" and recs[-1]["status"] == "down"

    async def test_total_timeout_guard(self, tmp_path):
        # poll이 영원히 running → 전체 타임아웃 가드가 노드를 유계로 만든다(브리핑 미첨부).
        client = FakeSreClient(never_terminal=True)
        store = DecisionStore(str(tmp_path / "a.jsonl"), enabled=True)
        ng = _ng_cfg(investigation_total_timeout_seconds=0.05,
                     investigation_poll_interval_seconds=0.0)
        out = await investigation_trigger_node(
            _state(_decision()), _config(ng, client, store)
        )
        assert out == {}
        import json
        recs = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text().splitlines()]
        assert recs[-1]["status"] == "timeout"

    async def test_missing_required_fields_rejected(self, tmp_path):
        # 필수 식별자 결측 이벤트 → 조사 서비스 rejected → 브리핑 미첨부·rejected 감사.
        client = FakeSreClient()
        store = DecisionStore(str(tmp_path / "a.jsonl"), enabled=True)
        st = _state(_decision())
        st["alarm_event"] = _event(server="", host="")
        out = await investigation_trigger_node(st, _config(_ng_cfg(), client, store))
        assert out == {}
        assert client.poll_calls == []  # rejected면 poll하지 않음
        import json
        recs = [json.loads(l) for l in (tmp_path / "a.jsonl").read_text().splitlines()]
        assert recs[-1]["status"] == "rejected"


# ── 4. 옵트인/티어 게이트 — no-op ────────────────────────────────────────
class TestNoOpPaths:
    async def test_disabled_flag_no_call(self):
        client = FakeSreClient()
        out = await investigation_trigger_node(
            _state(_decision()),
            _config(_ng_cfg(investigation_trigger_enabled=False), client),
        )
        assert out == {} and client.connect_calls == 0 and client.submit_calls == []

    async def test_below_min_tier_no_call(self):
        client = FakeSreClient()
        out = await investigation_trigger_node(
            _state(_decision(tier=TIER_TICKET)), _config(_ng_cfg(), client)
        )
        assert out == {} and client.submit_calls == []

    async def test_client_missing_no_op(self):
        out = await investigation_trigger_node(
            _state(_decision()), _config(_ng_cfg(), client=None)
        )
        assert out == {}

    async def test_no_decision_no_op(self):
        client = FakeSreClient()
        out = await investigation_trigger_node(
            _state(None), _config(_ng_cfg(), client)
        )
        assert out == {} and client.submit_calls == []


# ── 5. dedup — 동일 fingerprint 재submit → duplicate ─────────────────────
class TestDuplicateSubmit:
    async def test_second_submit_same_fingerprint_is_duplicate(self):
        client = FakeSreClient(poll_status="done", briefing={"cause": "x"})
        cfg = _config(_ng_cfg(), client)
        await investigation_trigger_node(_state(_decision(fp="dup-fp")), cfg)
        # 두 번째 submit은 동일 fingerprint라 기존 조사 재사용(duplicate) — 그래도 poll·첨부.
        out2 = await investigation_trigger_node(_state(_decision(fp="dup-fp")), cfg)
        assert client.submit_calls[1]  # 두 번째 submit 호출됨
        # FakeSreClient 계약: 2회차는 동일 id 반환(중복 조사 미생성).
        assert out2.get("investigation_briefing") == {"cause": "x"}
        assert client.poll_calls[0] == client.poll_calls[1]  # 같은 investigation_id poll

    async def test_fake_client_contract_duplicate_status(self):
        # 계약 자체 검증: 2회차 submit이 status="duplicate"·동일 id.
        client = FakeSreClient()
        p = build_trigger_payload(_event(), _decision(fp="c-fp"))
        r1 = await client.submit(p)
        r2 = await client.submit(p)
        assert r1["status"] == "accepted"
        assert r2["status"] == "duplicate" and r2["investigation_id"] == r1["investigation_id"]


# ── 6. 브리핑 렌더 ───────────────────────────────────────────────────────
def _analysis() -> SimpleNamespace:
    ev = SimpleNamespace(
        severity=2, alarm_name="CPU", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="root/srv-1",
        resource_type="server.Cpus", resource_name="r1", alarm_status="OPEN",
        conditions=">90%", condition_log="cpu high",
    )
    return SimpleNamespace(
        alarm_event=ev, severity_label="심각", summary="s", probable_cause="c",
        recommended_action="a", pattern_type="", pattern_analysis="",
    )


class TestBriefingRender:
    def test_stub_briefing_block_rendered(self):
        body = build_workb_body(
            _analysis(),
            investigation_briefing={"stub": True, "message": "조사 미실행(스텁)"},
        )
        assert "조사 브리핑 (자동 조사)" in body
        assert "조사 미실행(스텁)" in body

    def test_six_element_briefing_rendered(self):
        body = build_workb_body(
            _analysis(),
            investigation_briefing={"cause": "OOM으로 java 종료", "recommendation": "힙 상향"},
        )
        assert "원인:" in body and "OOM으로 java 종료" in body
        assert "권고:" in body and "힙 상향" in body

    def test_none_briefing_bit_identical(self):
        base = build_workb_body(_analysis())
        explicit = build_workb_body(_analysis(), investigation_briefing=None)
        assert base == explicit
        assert "조사 브리핑" not in base


# ── 7. 감사 — type="investigation"이라 티어 집계 불변 ────────────────────
class TestAuditRecord:
    def test_record_investigation_excluded_from_tier_aggregate(self, tmp_path):
        store = DecisionStore(str(tmp_path / "a.jsonl"), enabled=True)
        # 1건의 PAGE 결정 + 1건의 investigation 감사.
        store.record(_decision(), alarm_id="A-1")
        store.record_investigation(
            alarm_id="A-1", fingerprint="fp-1", investigation_id="inv-1",
            status="done", verdict="escalate",
        )
        agg = store.aggregate()
        assert agg["total"] == 1  # investigation 레코드는 total/by_tier에서 제외
        assert agg["by_tier"].get("page") == 1

    def test_record_investigation_disabled_no_op(self, tmp_path):
        path = tmp_path / "a.jsonl"
        DecisionStore(str(path), enabled=False).record_investigation(
            alarm_id="A", status="done"
        )
        assert not path.exists()


# ── 8. 그래프 배선 ───────────────────────────────────────────────────────
def _graph_cfg(**ng) -> SimpleNamespace:
    base = dict(enable_noise_gate=True)
    base.update(ng)
    return SimpleNamespace(
        alarm=SimpleNamespace(history_enabled=True),
        noise_gate=SimpleNamespace(**base),
    )


class TestGraphWiring:
    def test_trigger_node_wired_only_when_enabled(self):
        off = set(build_alarm_graph(_graph_cfg()).get_graph().nodes.keys())
        on = set(build_alarm_graph(
            _graph_cfg(investigation_trigger_enabled=True)
        ).get_graph().nodes.keys())
        assert "investigation_trigger" not in off
        assert "investigation_trigger" in on
        # 트리거 노드는 gate와 notifier 사이에만 추가된다(그 외 노드집합 동일).
        assert on - off == {"investigation_trigger"}

    def test_trigger_needs_gate_enabled(self):
        # enable_noise_gate=False면 investigation_trigger_enabled여도 미배선(게이트 없으면 결정 없음).
        nodes = set(build_alarm_graph(SimpleNamespace(
            alarm=SimpleNamespace(history_enabled=True),
            noise_gate=SimpleNamespace(
                enable_noise_gate=False, investigation_trigger_enabled=True
            ),
        )).get_graph().nodes.keys())
        assert "investigation_trigger" not in nodes
