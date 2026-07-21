"""Plan 60 Wave A 회귀 가드 — 신규 플래그가 전부 off면 현행 게이트와 비트 동일(회귀 0).

Wave A(E1 재발생 관측성 · E4 토폴로지 다홉 · E6 통보 컨텍스트 보강)는 전부 옵트인이다.
게이트가 켜져 있어도(enable_noise_gate) Wave A 신규 경로가 비활성이면:
  A. 정책(decide_notification): cascaded 미제공(multi_hop off)·correlated 휴면이면 현행
     1홉 parent_avail_status 판정과 tier/reason/priority 비트 동일. 심각도3 단락 불변.
  B. signals: Wave A 신규 키(cascaded/root_resource/correlated)가 존재하되 off-기본값
     (None/None/False) — §8.2 동결 스키마 확장은 Wave A에서 일괄 1회.
  C. 통보(build_workb_body): enrichment=None(message off)이면 본문 비트 동일.
  D. 그래프: Wave A 플래그는 노드집합을 바꾸지 않는다(전부 worker/policy/enricher 내부).

E1 dedup·E4 다홉·E6 보강 각각의 상세 동작은 test_recurrence_dedup / test_multi_hop_cascade /
test_message_enrichment 가 담당한다. 본 파일은 "전부 off = 무변경" 교차 회귀만 고정한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.alarm.application.nodes.alarm_notifier import build_workb_body
from src.alarm.domain.notification_policy import (
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph


def _event(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def _cfg(**over) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2,
        importance_value_map={"HIGH": "높음"},
        resolved_to_dashboard=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _ctx(parent=None, **extra) -> dict:
    ctx = {
        "importance_id": "HIGH", "maintenance": False, "noti_policy": None,
        "parent_avail_status": parent, "source": "polestar_db",
    }
    ctx.update(extra)
    return ctx


# ─────────────────────────────────────────────────────────────
# A. 정책 — Wave A 신규 경로 off면 현행 1홉 판정과 비트 동일
# ─────────────────────────────────────────────────────────────
class TestPolicyWaveAOffEqualsCurrent:
    def test_multihop_off_falls_back_to_1hop_suppress(self):
        # dependency_suppression on + cascaded 미제공(noise_ctx에 cascaded 키 없음)
        # → 현행 1홉 parent_avail_status 판정(부모 비정상 → SUPPRESS)과 동일.
        cfg = _cfg(dependency_suppression=True)
        d = decide_notification(_event(2), None, None, _ctx(parent=1), cfg)
        assert d.tier == TIER_SUPPRESS
        assert "부모 리소스 비정상" in d.reason  # 1홉 사유(다홉 사유 아님)

    def test_multihop_off_parent_normal_not_suppressed(self):
        cfg = _cfg(dependency_suppression=True)
        d = decide_notification(_event(2), None, None, _ctx(parent=0), cfg)
        assert d.tier == TIER_PAGE  # 부모 정상 → 억제 없음, 매트릭스(높음+sev2=PAGE)

    def test_correlated_arg_is_dormant(self):
        # E2 step7.5 미구현 — correlated=True를 넘겨도 tier/reason/priority 불변(휴면).
        cfg = _cfg()
        base = decide_notification(_event(2), None, None, _ctx(None), cfg)
        with_corr = decide_notification(
            _event(2), None, None, _ctx(None), cfg, correlated=True
        )
        assert with_corr.tier == base.tier
        assert with_corr.reason == base.reason
        assert with_corr.priority == base.priority
        assert with_corr.signals["correlated"] is True   # signals에만 스냅샷
        assert base.signals["correlated"] is False

    def test_severity3_short_circuits_even_with_cascade(self):
        # 심각도3은 cascaded=True·root 미통보여도 step3에서 PAGE 단락(불변).
        cfg = _cfg(dependency_suppression=True)
        d = decide_notification(
            _event(3), None, None,
            _ctx(parent=1, cascaded=True, root_resource="R99", root_notified=False),
            cfg,
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason


# ─────────────────────────────────────────────────────────────
# B. signals — Wave A 신규 키가 off-기본값으로 존재(동결 스키마)
# ─────────────────────────────────────────────────────────────
class TestSignalsWaveAKeysOffDefaults:
    def test_new_keys_present_at_off_defaults(self):
        d = decide_notification(_event(2), None, None, _ctx(None), _cfg())
        sig = d.signals
        assert sig["cascaded"] is None
        assert sig["root_resource"] is None
        assert sig["correlated"] is False


# ─────────────────────────────────────────────────────────────
# C. 통보 — E6 enrichment=None이면 본문 비트 동일
# ─────────────────────────────────────────────────────────────
def _analysis() -> SimpleNamespace:
    ev = SimpleNamespace(
        severity=2, alarm_name="CPU", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="root/srv-1", resource_type="server.Cpus",
        resource_name="r1", alarm_status="OPEN", conditions=">90%", condition_log="cpu high",
    )
    return SimpleNamespace(
        alarm_event=ev, severity_label="심각", summary="s", probable_cause="c",
        recommended_action="a", pattern_type="", pattern_analysis="",
    )


class TestNotifierEnrichmentOff:
    def test_body_bit_identical_when_enrichment_none(self):
        result = _analysis()
        # E6 인자를 기본(None)으로 둔 본문 == enrichment 인자를 명시 None으로 넘긴 본문.
        default_body = build_workb_body(result)
        explicit_none = build_workb_body(
            result, process_snapshot=None, recurrence=None, enrichment=None
        )
        assert default_body == explicit_none
        assert "보강" not in default_body  # 신규 kind 보강 블록 미첨부


# ─────────────────────────────────────────────────────────────
# D. 그래프 — Wave A 플래그가 노드집합을 바꾸지 않음
# ─────────────────────────────────────────────────────────────
def _gcfg(**ng) -> SimpleNamespace:
    base = dict(enable_noise_gate=True)
    base.update(ng)
    return SimpleNamespace(
        alarm=SimpleNamespace(history_enabled=True),
        noise_gate=SimpleNamespace(**base),
    )


class TestGraphNodesUnchangedByWaveA:
    def test_node_set_identical_wave_a_on_vs_off(self):
        nodes_off = set(build_alarm_graph(_gcfg()).get_graph().nodes.keys())
        nodes_on = set(build_alarm_graph(_gcfg(
            dependency_suppression=True, multi_hop_cascade_enabled=True,
            message_enrichment_enabled=True,
        )).get_graph().nodes.keys())
        assert nodes_off == nodes_on
