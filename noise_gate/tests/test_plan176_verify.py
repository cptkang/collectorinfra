"""plans/176 verifier — 함수 분해 뒤 seam·우선순위·순서 계약 고정.

차분 하네스(기준선 1ad7c161 대비 비트 동일)와 별개로, 분해로 생긴 이음매가 앞으로도 유지돼야 할
계약만 단언한다:
  1. `notification_gate.review_page`·`decide_notification` 모듈 patch가
     뽑은 함수(`_decide`)에도 닿는다.
  2. `_resolve_first_target` — 미확정 + prior_targets off면 (False, None)으로 진행(차단 아님).
  3. 인가 거부면 조사 클라이언트에 닿지 않고(connect·submit 0회) 감사는 authz_denied로 남는다.
  4. enricher 보강 타임아웃이면 키셋 그대로 전부 None, 로그는 warning 1건.
  5. polestar fetch — 한 조회 실패가 다른 신호를 지우지 않는다(got_any 경로).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from types import SimpleNamespace

import noise_gate.application.nodes.alarm_context_enricher as enr
import noise_gate.application.nodes.investigation_trigger as inv
import noise_gate.application.nodes.notification_gate as ng
import noise_gate.infrastructure.polestar_noise_context as pnc
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import NotificationDecision


def _event(**over) -> AlarmEvent:
    base = dict(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/r", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Cpus", resource_name="r1", alarm_name="CPU High",
        alarm_time=datetime(2026, 7, 27, 12, 0, 0), conditions=">90%", condition_log="cpu 95%",
    )
    base.update(over)
    return AlarmEvent(**base)


def _decision(tier: str = "page", stage: str = "matrix") -> NotificationDecision:
    return NotificationDecision(tier, "r", 1, {}, "fp-1", stage)


# ── 1. notification_gate seam ──────────────────────────────────────────
def test_module_patches_reach_extracted_decide_and_review(monkeypatch):
    seen: list[str] = []
    d1 = _decision("dashboard")
    d2 = _decision("page")

    def fake_decide(event, hist, result, noise_ctx, gate_cfg, **kw):
        seen.append("decide:" + ("impact" if noise_ctx and "app_impact" in noise_ctx else "base"))
        return d2 if noise_ctx and "app_impact" in noise_ctx else d1

    def fake_review(decision, signals, gate_cfg):
        seen.append(f"review:{decision.tier}")
        return decision

    monkeypatch.setattr(ng, "decide_notification", fake_decide)
    monkeypatch.setattr(ng, "review_page", fake_review)
    monkeypatch.setattr(ng, "_app_impact_applicable", lambda *a: True)
    monkeypatch.setattr(ng, "_healthy_shadow_applicable", lambda *a: False)
    gate = SimpleNamespace(enable_noise_gate=True, page_review_mode="enforce")
    state = {
        "alarm_event": _event(), "analysis_result": SimpleNamespace(error=None),
        "noise_context": {"source": "polestar_db"}, "page_review_signals": {"s": 1},
        "episode_app_impact": {"app_impact": {"source": "episode", "fatal_events": 1,
                                              "event_types": ["X"]}},
    }
    out = asyncio.run(ng.notification_gate_node(
        state, {"configurable": {"app_config": SimpleNamespace(noise_gate=gate)}}))
    assert seen == ["decide:base", "decide:impact", "review:page"]
    assert out["notification_decision"] is d2
    assert out["noise_context"]["app_impact"]["source"] == "episode"


# ── 2·3. investigation_trigger ─────────────────────────────────────────
def test_resolve_first_target_unresolved_without_prior_targets_is_not_blocked(monkeypatch):
    monkeypatch.setattr(inv, "resolve_targets", lambda **kw: SimpleNamespace(
        resolved=False, targets=[], dropped=[{"reason": "placeholder"}]))
    cfg = SimpleNamespace(composite=SimpleNamespace(prior_targets_enabled=False))
    got = inv._resolve_first_target(cfg, _event(), _decision(), None, None, "inline")
    assert got == (False, None)
    cfg_on = SimpleNamespace(composite=SimpleNamespace(prior_targets_enabled=True))
    got_on = inv._resolve_first_target(cfg_on, _event(), _decision(), None, None, "inline")
    assert got_on == (True, None)


def test_resolve_first_target_resolved_returns_first(monkeypatch):
    first = SimpleNamespace(hostname="h1", server_name="srv-1")
    monkeypatch.setattr(inv, "resolve_targets", lambda **kw: SimpleNamespace(
        resolved=True, targets=[first, SimpleNamespace()], dropped=[]))
    cfg = SimpleNamespace(composite=SimpleNamespace(prior_targets_enabled=True))
    blocked, got = inv._resolve_first_target(cfg, _event(), _decision(), None, None, "inline")
    assert blocked is False and got is first


class _Client:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def unreachable_reason(self):
        self.calls.append("probe")
        return None

    async def connect(self):
        self.calls.append("connect")

    async def disconnect(self):
        self.calls.append("disconnect")

    async def submit(self, payload):
        self.calls.append("submit")
        return {"status": "accepted", "investigation_id": "i1"}

    async def poll(self, iid):
        self.calls.append("poll")
        return {"status": "done", "briefing": {"s": 1}, "verdict": None}


class _DStore:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def record_investigation(self, **kw):
        self.statuses.append(kw["status"])


def test_authz_denied_never_touches_client(monkeypatch):
    monkeypatch.setattr(inv, "authorize_host_investigation", lambda **kw: SimpleNamespace(
        allowed=False, reason="not_allowed", as_audit=lambda: "a"))
    client, store = _Client(), _DStore()
    gate = SimpleNamespace(
        investigation_trigger_enabled=True, investigation_trigger_min_tier="page"
    )
    cfg = SimpleNamespace(noise_gate=gate, host_authz=SimpleNamespace(mode="admin_only"),
                          composite=SimpleNamespace(prior_targets_enabled=False,
                                                    availability_precheck_enabled=False))
    out = asyncio.run(inv.investigation_trigger_node(
        {"alarm_event": _event(), "notification_decision": _decision()},
        {"configurable": {
            "app_config": cfg, "sre_agent_client": client, "decision_store": store,
        }},
    ))
    assert out == {}
    assert client.calls == []  # 도달성 probe조차 인가 뒤다
    assert store.statuses == ["authz_denied"]


# ── 4. enricher 타임아웃 ───────────────────────────────────────────────
def test_enricher_timeout_returns_all_keys_none(monkeypatch, caplog):
    async def hang(*a, **kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(enr, "enrich_processes", hang)
    monkeypatch.setattr(enr, "enrich_noise_context", hang)
    gate = SimpleNamespace(enable_noise_gate=True, message_enrichment_enabled=False,
                           dynamic_baseline_enabled=False)
    cfg = SimpleNamespace(alarm=SimpleNamespace(history_enabled=False, enrich_timeout_seconds=0.05),
                          noise_gate=gate)
    with caplog.at_level(logging.WARNING, logger=enr.logger.name):
        out = asyncio.run(enr.alarm_context_enricher_node(
            {"alarm_event": _event()},
            {"configurable": {"app_config": cfg, "noise_repo": object()}},
        ))
    assert out == {"history_stats": None, "process_snapshot": None, "noise_context": None}
    warns = [r for r in caplog.records if r.name == enr.logger.name]
    assert len(warns) == 1 and "타임아웃" in warns[0].getMessage()


# ── 5. polestar fetch 부분 실패 ────────────────────────────────────────
class _Res:
    def __init__(self, rows):
        self.rows = rows


def test_polestar_fetch_partial_failure_keeps_other_signals(monkeypatch):
    ev = _event()
    res_sql = pnc.build_resource_signal_sql(ev.db_id, ev.server_name)
    executed: list[str] = []

    class Client:
        async def execute_sql(self, sql):
            executed.append("res" if sql == res_sql else "other")
            if sql == res_sql:
                raise RuntimeError("table missing")
            return _Res([{"noti_count": 2}])

    class CM:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    registry = SimpleNamespace(is_registered=lambda db: True, get_client=lambda db: CM())
    repo = pnc.PolestarNoiseContextRepository(registry, None)
    ctx = asyncio.run(repo.fetch(ev))
    assert executed == ["res", "other"]
    assert ctx["source"] == "polestar_db"
    assert ctx["importance_id"] is None
    assert list(ctx) == [*pnc._NOISE_CTX_KEYS, "source"]


def test_polestar_fetch_all_failed_is_unavailable():
    class Client:
        async def execute_sql(self, sql):
            raise RuntimeError("down")

    class CM:
        async def __aenter__(self):
            return Client()

        async def __aexit__(self, *a):
            return False

    registry = SimpleNamespace(is_registered=lambda db: True, get_client=lambda db: CM())
    repo = pnc.PolestarNoiseContextRepository(registry, None)
    assert asyncio.run(repo.fetch(_event()))["source"] == "unavailable"
