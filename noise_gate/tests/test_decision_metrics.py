"""노이즈 게이트 결정 카운터 테스트 (plans/92 트랙 B-1 · O4).

- `DecisionStore.record()` 호출 시 `noise_gate_decisions_total{stage,decision}`가 1 증가한다.
- 라벨 값은 유한 집합이다 — 어휘 밖 단계·티어는 `other`로 묶인다.
- 감사 파일(JSONL)의 모양은 바뀌지 않는다(관찰 동작 불변) · enabled=False는 계속 no-op이다.
- 이름 충돌 스모크: `import prometheus_client`는 pip 패키지로 해석되고, 같은 이름의
  `noise_gate.infrastructure.prometheus_client`(PromQL 조회 클라이언트)와 공존한다.

레지스트리는 프로세스 전역이라 값은 전·후 차이로만 단언한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import prometheus_client
from prometheus_client import REGISTRY

from noise_gate.domain.notification_policy import (
    STAGE_ORDER,
    STAGE_STORM,
    STAGE_UNKNOWN,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
)
from noise_gate.infrastructure.decision_store import DecisionStore


def _decision(tier: str = TIER_SUPPRESS, **kwargs: object) -> NotificationDecision:
    base: dict = dict(
        tier=tier,
        reason="테스트 결정",
        priority=100,
        signals={"severity": 2},
        fingerprint="fp-o4",
    )
    base.update(kwargs)
    return NotificationDecision(**base)


def _count(stage: str, decision: str) -> float:
    value = REGISTRY.get_sample_value(
        "noise_gate_decisions_total", {"stage": stage, "decision": decision}
    )
    return value or 0.0


def test_record_increments_counter_by_stage_and_tier(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    before = _count(STAGE_STORM, TIER_SUPPRESS)

    store.record(_decision(stage=STAGE_STORM), alarm_id="A-1")

    assert _count(STAGE_STORM, TIER_SUPPRESS) == before + 1


def test_empty_stage_falls_back_to_reason_prefix_like_funnel(tmp_path: Path) -> None:
    """단계가 빈 결정은 퍼널 `_stage_of`와 같게 사유 접두로 되짚는다."""
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    before_storm = _count(STAGE_STORM, TIER_DASHBOARD)
    before_unknown = _count(STAGE_UNKNOWN, TIER_PAGE)

    store.record(_decision(tier=TIER_DASHBOARD, reason="스톰 그룹핑 — 대표 1건"))
    store.record(_decision(tier=TIER_PAGE, reason="접두 없는 사유"))

    assert _count(STAGE_STORM, TIER_DASHBOARD) == before_storm + 1
    assert _count(STAGE_UNKNOWN, TIER_PAGE) == before_unknown + 1


def test_out_of_vocabulary_values_are_folded_to_other(tmp_path: Path) -> None:
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    before = _count("other", "other")

    store.record(_decision(tier="tier-x-123", stage="stage-y-456"))

    assert _count("other", "other") == before + 1
    body = prometheus_client.generate_latest(REGISTRY).decode("utf-8")
    assert "stage-y-456" not in body and "tier-x-123" not in body


def test_label_values_stay_in_finite_sets(tmp_path: Path) -> None:
    DecisionStore(str(tmp_path / "d.jsonl")).record(_decision(stage="임의-단계"))

    allowed_stages = set(STAGE_ORDER) | {STAGE_UNKNOWN, "other"}
    allowed_decisions = {TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS, "other"}
    samples = [
        s for family in REGISTRY.collect() if family.name == "noise_gate_decisions"
        for s in family.samples if s.name == "noise_gate_decisions_total"
    ]
    assert samples
    for sample in samples:
        assert set(sample.labels) == {"stage", "decision"}
        assert sample.labels["stage"] in allowed_stages
        assert sample.labels["decision"] in allowed_decisions


def test_audit_line_shape_unchanged(tmp_path: Path) -> None:
    """카운터가 붙어도 감사 파일 한 줄의 키 집합은 그대로다."""
    path = tmp_path / "d.jsonl"
    DecisionStore(str(path)).record(_decision(stage=STAGE_STORM), alarm_id="A-9")

    row = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert set(row) == {
        "ts", "alarm_id", "tier", "reason", "priority", "fingerprint", "signals", "stage",
    }


def test_disabled_store_stays_noop(tmp_path: Path) -> None:
    path = tmp_path / "d.jsonl"
    before = _count(STAGE_STORM, TIER_SUPPRESS)

    DecisionStore(str(path), enabled=False).record(_decision(stage=STAGE_STORM))

    assert _count(STAGE_STORM, TIER_SUPPRESS) == before
    assert not path.exists()


def test_pip_prometheus_client_coexists_with_local_module() -> None:
    import noise_gate.infrastructure.metrics as gate_metrics
    import noise_gate.infrastructure.prometheus_client as local_promql_client

    pip_path = Path(prometheus_client.__file__).resolve()
    assert "site-packages" in pip_path.parts
    assert Path(local_promql_client.__file__).resolve().parent.name == "infrastructure"
    assert local_promql_client is not prometheus_client
    assert isinstance(gate_metrics.NOISE_GATE_DECISIONS_TOTAL, prometheus_client.Counter)
