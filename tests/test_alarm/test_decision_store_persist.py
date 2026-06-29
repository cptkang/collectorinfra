"""notification_gate_node ↔ DecisionStore 적재 통합 테스트 (Plan 52 §8.3).

게이트 노드 실행 시 결정이 JSONL 에 적재되고(tier·reason·priority·signals·fingerprint·
alarm_id·ts 포함), SUPPRESS/DASHBOARD 결정도 적재됨(억제≠삭제)을 검증한다.
게이트 비활성 시 노드가 빈 dict 를 반환하고 적재가 없음을 확인한다.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from src.alarm.application.nodes.notification_gate import notification_gate_node
from src.alarm.domain.alarm import AlarmEvent
from src.alarm.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
)
from src.alarm.infrastructure.decision_store import DecisionStore

REF = datetime(2026, 6, 29, 14, 0, 0)
IMP_MAP = {"HIGH": "높음", "MID": "보통", "LOW": "낮음"}


def make_event(**kwargs) -> AlarmEvent:
    defaults = dict(
        db_id="polestar_cm_gp", server_name="srv-1", hostname="h-1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/svr/Cpus", alarm_id="A-1", severity=2, alarm_status="NOT_ACK",
        resource_type="server.Cpus", resource_name="svr-1-CPU", alarm_name="CPU 임계",
        alarm_time=REF, conditions="", condition_log="",
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def gate_config(*, enabled: bool = True, resolved_to_dashboard: bool = False) -> SimpleNamespace:
    return SimpleNamespace(noise_gate=SimpleNamespace(
        enable_noise_gate=enabled,
        suppress_max_severity=2,
        importance_value_map=IMP_MAP,
        resolved_to_dashboard=resolved_to_dashboard,
    ))


def make_ctx(*, importance_id="HIGH", maintenance=False, noti_policy=None, source="polestar_db"):
    return {
        "importance_id": importance_id, "maintenance": maintenance,
        "noti_policy": noti_policy, "parent_avail_status": None, "source": source,
    }


def _read_lines(path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


async def _run(event, ctx, cfg, store, *, analysis=None, self_heal=False):
    state = {
        "alarm_event": event,
        "history_stats": None,
        "analysis_result": analysis or SimpleNamespace(pattern_type="", is_routine=None),
        "error": None,
        "noise_context": ctx,
        "self_heal": self_heal,
    }
    config = {"configurable": {"app_config": cfg, "decision_store": store}}
    return await notification_gate_node(state, config)


class TestPersist:
    async def test_single_record_with_all_fields(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        # sev3 → PAGE 결정
        event = make_event(severity=3, alarm_id="PAGE-1")
        out = await _run(event, make_ctx(), gate_config(), store)

        assert out["notification_decision"].tier == TIER_PAGE
        rows = _read_lines(store_path)
        assert len(rows) == 1
        rec = rows[0]
        assert rec["alarm_id"] == "PAGE-1"
        assert rec["tier"] == TIER_PAGE
        assert rec["reason"]
        assert isinstance(rec["priority"], int)
        assert rec["fingerprint"]
        assert datetime.fromisoformat(rec["ts"])
        # signals 동결 스키마 일부 키 확인
        assert rec["signals"]["severity"] == 3
        assert rec["signals"]["effective_severity"] == 3

    async def test_suppress_is_recorded(self, tmp_path):
        # 억제 ≠ 삭제: 유지보수 SUPPRESS 도 적재되어야 한다
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        event = make_event(severity=2, alarm_id="SUP-1")
        out = await _run(event, make_ctx(importance_id="HIGH", maintenance=True),
                         gate_config(), store)
        assert out["notification_decision"].tier == TIER_SUPPRESS
        rows = _read_lines(store_path)
        assert len(rows) == 1
        assert rows[0]["tier"] == TIER_SUPPRESS

    async def test_dashboard_is_recorded(self, tmp_path):
        # sev1/보통 → DASHBOARD (억제≠삭제, 적재 확인)
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        event = make_event(severity=1, alarm_id="DASH-1")
        out = await _run(event, make_ctx(importance_id="MID"), gate_config(), store)
        assert out["notification_decision"].tier == TIER_DASHBOARD
        rows = _read_lines(store_path)
        assert rows[0]["tier"] == TIER_DASHBOARD

    async def test_aggregate_counts_suppress(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        await _run(make_event(severity=3, alarm_id="P"), make_ctx(), gate_config(), store)
        await _run(make_event(severity=2, alarm_id="S"),
                   make_ctx(importance_id="HIGH", maintenance=True), gate_config(), store)
        agg = store.aggregate()
        assert agg["total"] == 2
        assert agg["by_tier"][TIER_SUPPRESS] == 1
        assert agg["by_tier"][TIER_PAGE] == 1
        assert agg["suppress_ratio"] == 0.5


class TestGateDisabled:
    async def test_disabled_returns_empty_and_no_record(self, tmp_path):
        store_path = tmp_path / "decisions.jsonl"
        store = DecisionStore(str(store_path))
        out = await _run(make_event(severity=2), make_ctx(),
                         gate_config(enabled=False), store)
        assert out == {}                      # 결정 안 함
        assert not store_path.exists()        # 적재 없음

    async def test_no_store_does_not_crash(self, tmp_path):
        # decision_store 미주입이어도 결정은 산출되고 예외 없음
        state = {
            "alarm_event": make_event(severity=2),
            "history_stats": None,
            "analysis_result": SimpleNamespace(pattern_type="", is_routine=None),
            "error": None,
            "noise_context": make_ctx(),
            "self_heal": False,
        }
        config = {"configurable": {"app_config": gate_config()}}  # decision_store 없음
        out = await notification_gate_node(state, config)
        assert "notification_decision" in out
