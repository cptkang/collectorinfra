"""plans/162 W2 R4 독립 검증(verifier) — 수집 실패 PAGE 「심각도3만」 + 수집 실패율 메타경보.

구현자 테스트(test_collection_failed_sev3_only.py)가 덮지 않은 축을 본다.
- `_ConfigOverride`가 실 pydantic `NoiseGateConfig`·SimpleNamespace 양쪽에서
  속성을 그대로 통과시키는지
- shadow `would_tier`가 플래그 on 직접 판정과 입력 격자 전반에서 같은지 ·
  같은 now 결정성 · 재귀 1단계
- 게이트 노드 → 저장소 → 메타경보 실경로에서 off+shadow / on 이 같은 비율을 내는지 · 중복 집계 없음
- 분모가 `aggregate`의 total과 같은지(손상 줄·부속 레코드·ts 없는 레코드 섞음)
- `/alarm/metrics` 응답에 새 경보가 스키마 위반 없이 실리는지
- 순환 import 없음(양 import 순서)
"""

from __future__ import annotations

import asyncio
import itertools
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from src.config import NoiseGateConfig

from noise_gate.domain import notification_policy as np_mod
from noise_gate.domain.notification_policy import (
    EVIDENCE_COLLECTION_FAILED,
    EVIDENCE_COLLECTION_FAILED_SHADOW,
    STAGE_COLLECTION_FAILED,
    TIER_PAGE,
    TIER_SUPPRESS,
    _ConfigOverride,
    decide_notification,
)
from noise_gate.domain.silence import SilenceRule
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.tests.test_notification_policy import IMP_MAP, make_config, make_ctx, make_event

ROOT = Path(__file__).resolve().parents[2]

# 과거 고정 시각 — 실시계(datetime.now)로 재계산되면 침묵 규칙이 만료로 보여 결과가 달라진다.
PAST = datetime(2020, 1, 6, 3, 0, 0, tzinfo=UTC)


def _ngc(**over) -> NoiseGateConfig:
    """`.env` 누수를 막은 실 NoiseGateConfig — 판정이 읽는 필드를 모두 명시한다."""
    fields = dict(
        enable_noise_gate=True,
        suppress_max_severity=2,
        importance_value_map_csv="HIGH=높음,MID=보통,LOW=낮음",
        resolved_to_dashboard=False,
        dependency_suppression=False,
        inhibition_enabled=False,
        flapping_enabled=False,
        storm_grouping_enabled=False,
        cross_host_correlation_enabled=False,
        non_alarm_filter_enabled=False,
        annotation_planned_suppress=False,
        enable_ai_severity_boost=False,
        enable_llm_actionability=False,
        page_review_mode="off",
        collection_failed_page_sev3_only=False,
        app_impact_enabled=False,
        apm_healthy_demotion_shadow=False,
        decision_analysis_text_enabled=False,
        meta_alert_collection_failed_ratio=0.0,
    )
    fields.update(over)
    return NoiseGateConfig(_env_file=None, **fields)


# ── _ConfigOverride ─────────────────────────────────────────────────────


class TestConfigOverride:
    def test_pydantic_property_and_fields_pass_through(self):
        base = _ngc(storm_grouping_enabled=True, page_review_mode="shadow")
        view = _ConfigOverride(base, collection_failed_page_sev3_only=True)
        assert view.collection_failed_page_sev3_only is True
        assert base.collection_failed_page_sev3_only is False  # 원본 불변
        # property(importance_value_map)·일반 필드·Literal 필드 모두 원본 값
        assert view.importance_value_map == base.importance_value_map
        assert view.importance_value_map == {"HIGH": "높음", "MID": "보통", "LOW": "낮음"}
        assert view.storm_grouping_enabled is True
        assert view.page_review_mode == "shadow"

    def test_missing_attribute_falls_back_to_getattr_default(self):
        view = _ConfigOverride(SimpleNamespace(a=1), x=2)
        assert view.a == 1 and view.x == 2
        assert getattr(view, "nope", "기본") == "기본"
        with pytest.raises(AttributeError):
            view.nope  # noqa: B018

    def test_override_view_decision_equals_flag_on_config(self):
        # 덮어쓴 보기로 판정 = 처음부터 플래그 on인 config로 판정(pydantic 경로)
        ev = make_event(severity=2)
        on = decide_notification(ev, None, None, None, _ngc(collection_failed_page_sev3_only=True))
        via = decide_notification(
            ev, None, None, None, _ConfigOverride(_ngc(), collection_failed_page_sev3_only=True)
        )
        assert (via.tier, via.reason, via.priority, via.stage, via.evidence) == (
            on.tier, on.reason, on.priority, on.stage, on.evidence
        )


# ── shadow would_tier ≡ 플래그 on 판정 ───────────────────────────────────

_GRID = list(
    itertools.product(
        [1, 2],                                   # severity
        [None, "unavailable"],                    # 수집 실패 두 형태
        [False, True],                            # storm
        [False, True],                            # flapping
        [False, True],                            # inhibited
        [False, True],                            # correlated
    )
)


def _ctx(kind):
    return None if kind is None else make_ctx(source="unavailable")


@pytest.mark.parametrize("cfg_kind", ["pydantic", "namespace"])
def test_would_tier_matches_flag_on_across_grid(cfg_kind):
    def mk(**over):
        if cfg_kind == "pydantic":
            return _ngc(
                storm_grouping_enabled=True, flapping_enabled=True,
                inhibition_enabled=True, cross_host_correlation_enabled=True, **over,
            )
        return make_config(
            importance_value_map=IMP_MAP, storm_grouping_enabled=True,
            flapping_enabled=True, inhibition_enabled=True,
            cross_host_correlation_enabled=True, **over,
        )

    shadow_cfg = mk(page_review_mode="shadow")
    on_cfg = mk(collection_failed_page_sev3_only=True)
    mismatches = []
    for sev, kind, storm, flapping, inhibited, correlated in _GRID:
        kw = dict(storm=storm, flapping=flapping, inhibited=inhibited, correlated=correlated,
                  now=PAST)
        ev = make_event(severity=sev)
        sh = decide_notification(ev, None, None, _ctx(kind), shadow_cfg, **kw)
        on = decide_notification(ev, None, None, _ctx(kind), on_cfg, **kw)
        assert sh.tier == TIER_PAGE and sh.stage == STAGE_COLLECTION_FAILED
        would = sh.evidence[EVIDENCE_COLLECTION_FAILED_SHADOW]
        assert would["mode"] == "shadow"
        if would["would_tier"] != on.tier:
            mismatches.append((sev, kind, storm, flapping, inhibited, correlated,
                               would["would_tier"], on.tier))
        # shadow 판정은 수집 실패 표지를 싣지 않는다(off 판정이므로)
        assert EVIDENCE_COLLECTION_FAILED not in sh.evidence
    assert mismatches == []


def test_would_tier_uses_given_now_not_wall_clock():
    # PAST에만 유효한 침묵 규칙 — 내부 재계산이 실시계를 쓰면 만료로 보여 TICKET이 나온다.
    rule = SilenceRule(
        id="slc_past", db_id="", server_name="cop0-*", alarm_name="", resource_name="",
        max_severity=2, reason="점검", created_by="op",
        created_at=PAST - timedelta(minutes=5), expires_at=PAST + timedelta(minutes=5),
    )
    d = decide_notification(
        make_event(severity=2), None, None, None, _ngc(page_review_mode="shadow"),
        silence_rules=[rule], now=PAST,
    )
    assert d.tier == TIER_PAGE
    assert d.evidence[EVIDENCE_COLLECTION_FAILED_SHADOW]["would_tier"] == TIER_SUPPRESS


def test_shadow_recursion_is_single_level(monkeypatch):
    calls = []
    real = np_mod.decide_notification

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(np_mod, "decide_notification", counting)
    np_mod.decide_notification(
        make_event(severity=2), None, None, None, _ngc(page_review_mode="shadow"), now=PAST
    )
    assert len(calls) == 2  # 바깥 1 + 재계산 1


def test_shadow_off_mode_is_bit_identical_to_default():
    # page_review_mode off·enforce · 플래그 off 실 config — 기본 config와 판정·근거 동일
    ev = make_event(severity=2)
    base = decide_notification(ev, None, None, None, _ngc(), now=PAST)
    for mode in ("off", "enforce"):
        d = decide_notification(ev, None, None, None, _ngc(page_review_mode=mode), now=PAST)
        assert (d.tier, d.reason, d.priority, d.signals, d.stage, d.evidence) == (
            base.tier, base.reason, base.priority, base.signals, base.stage, base.evidence
        )
        assert d.evidence == {}


# ── 게이트 노드 → 저장소 → 메타경보 실경로 ──────────────────────────────


def _run_gate(store: DecisionStore, gate_cfg, event, noise_ctx) -> None:
    from noise_gate.application.nodes.notification_gate import notification_gate_node

    analysis = SimpleNamespace(
        error=None, pattern_type="", is_routine=None,
        ai_message_severity=None, llm_actionability=None,
    )
    state = {"analysis_result": analysis, "alarm_event": event, "noise_context": noise_ctx}
    cfg = {"configurable": {
        "app_config": SimpleNamespace(noise_gate=gate_cfg), "decision_store": store,
    }}
    out = asyncio.run(notification_gate_node(state, cfg))
    assert "notification_decision" in out


_TRAFFIC = [
    (3, None), (2, None), (1, None), (0, None),
    (2, "unavailable"), (1, "unavailable"),
    (2, "ok"), (1, "ok"), (3, "ok"),
]


def _traffic_ctx(kind):
    if kind is None:
        return None
    return make_ctx(source="unavailable") if kind == "unavailable" else make_ctx()


def _cf_alert(store: DecisionStore, threshold: float = 0.01) -> dict:
    alerts = [
        a for a in store.meta_alerts(
            window_seconds=3600, suppress_ratio_threshold=0.99, min_events=1,
            collection_failed_ratio_threshold=threshold,
        )
        if a["type"] == "collection_failed_ratio"
    ]
    assert len(alerts) == 1
    return alerts[0]


def test_gate_path_off_shadow_and_on_give_same_ratio(tmp_path):
    results = {}
    for name, cfg in (
        ("off", _ngc()),
        ("shadow", _ngc(page_review_mode="shadow")),
        ("on", _ngc(collection_failed_page_sev3_only=True)),
    ):
        store = DecisionStore(str(tmp_path / f"{name}.jsonl"))
        for sev, kind in _TRAFFIC:
            _run_gate(store, cfg, make_event(severity=sev), _traffic_ctx(kind))
        a = _cf_alert(store)
        results[name] = (a["count"], a["total"])
        if name == "shadow":
            recs = [json.loads(x) for x in store.path.read_text(encoding="utf-8").splitlines()]
            shadow_blocks = [
                r["stage_evidence"][EVIDENCE_COLLECTION_FAILED_SHADOW]
                for r in recs if EVIDENCE_COLLECTION_FAILED_SHADOW in r.get("stage_evidence", {})
            ]
            # sev1·2 수집 실패 4건에만 「했을 티어」가 실린다
            assert len(shadow_blocks) == 4
    # 분자: sev1·2 수집 실패 4건(sev3·sev0 제외) · 분모: 전량 9건
    assert results["off"] == results["shadow"] == results["on"] == (4, 9)


def test_marker_and_original_stage_counted_once(tmp_path):
    path = tmp_path / "d.jsonl"
    now = datetime.now(UTC).isoformat()
    recs = [
        # 같은 레코드에 세 신호가 겹쳐도 1건
        {"ts": now, "tier": "ticket", "stage": "page_review", "reason": "PAGE 재판정 — x",
         "stage_evidence": {"original_stage": "collection_failed", "collection_failed": True}},
        {"ts": now, "tier": "page", "stage": "collection_failed", "reason": "신호 수집 실패",
         "stage_evidence": {"collection_failed": True}},
        # 표지 값이 True가 아니면 세지 않는다
        {"ts": now, "tier": "ticket", "stage": "matrix", "reason": "매트릭스",
         "stage_evidence": {"collection_failed": "yes"}},
        {"ts": now, "tier": "ticket", "stage": "matrix", "reason": "매트릭스",
         "stage_evidence": "not-a-dict"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                    encoding="utf-8")
    a = _cf_alert(DecisionStore(str(path)))
    assert (a["count"], a["total"]) == (2, 4)


def test_denominator_equals_aggregate_total(tmp_path):
    path = tmp_path / "d.jsonl"
    now = datetime.now(UTC)
    lines = [
        json.dumps({"ts": now.isoformat(), "tier": "page", "stage": "collection_failed"}),
        json.dumps({"ts": now.isoformat(), "tier": "ticket", "stage": "matrix"}),
        json.dumps({"tier": "ticket", "stage": "matrix"}),            # ts 없음 → 창 안
        json.dumps({"ts": "garbage", "tier": "dashboard"}),            # 파싱 실패 → 창 안
        json.dumps({"type": "resolution", "ts": now.isoformat(), "duration_seconds": 3}),
        json.dumps({"type": "recurrence", "ts": now.isoformat()}),
        json.dumps({"ts": (now - timedelta(hours=5)).isoformat(), "tier": "page",
                    "stage": "collection_failed"}),                    # 창 밖
        "{손상된 줄",
        "",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    store = DecisionStore(str(path))
    agg_total = store.aggregate(window_seconds=3600)["total"]
    a = _cf_alert(store)
    assert a["total"] == agg_total == 4
    assert a["count"] == 1


@pytest.mark.parametrize(("threshold", "expect"), [(0.25, False), (0.2499, True), (-1.0, False),
                                                    (1.0, False)])
def test_threshold_is_strict_and_nonpositive_is_off(tmp_path, threshold, expect):
    path = tmp_path / "d.jsonl"
    now = datetime.now(UTC).isoformat()
    recs = [{"ts": now, "tier": "page", "stage": "collection_failed"}] + [
        {"ts": now, "tier": "ticket", "stage": "matrix"}
    ] * 3
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    alerts = DecisionStore(str(path)).meta_alerts(
        window_seconds=3600, suppress_ratio_threshold=0.99, min_events=1,
        collection_failed_ratio_threshold=threshold,
    )
    assert any(a["type"] == "collection_failed_ratio" for a in alerts) is expect


def test_threshold_zero_does_not_scan_again(tmp_path, monkeypatch):
    path = tmp_path / "d.jsonl"
    path.write_text(json.dumps({"ts": datetime.now(UTC).isoformat(), "tier": "page"}) + "\n",
                    encoding="utf-8")
    store = DecisionStore(str(path))

    def boom(*a, **k):
        raise AssertionError("임계 0인데 수집 실패 스캔이 돌았다")

    monkeypatch.setattr(store, "_collection_failed_counts", boom)
    store.meta_alerts(window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1)
    store.meta_alerts(window_seconds=3600, suppress_ratio_threshold=0.9, min_events=1,
                      collection_failed_ratio_threshold=0.0)


# ── /alarm/metrics 응답 ─────────────────────────────────────────────────


def _metrics_client(decisions_path, **ng_over):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.routes import alarm as alarm_routes

    ng = dict(
        decision_store_path=str(decisions_path),
        meta_alert_window_seconds=3600,
        meta_alert_suppress_ratio=0.99,
        meta_alert_min_events=1,
    )
    ng.update(ng_over)
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False), noise_gate=SimpleNamespace(**ng)
    )
    return TestClient(app)


def _seed_metrics(path) -> None:
    now = datetime.now(UTC).isoformat()
    recs = [
        {"ts": now, "tier": "page", "stage": "collection_failed", "reason": "신호 수집 실패",
         "priority": 1, "signals": {}, "fingerprint": "a"},
        {"ts": now, "tier": "ticket", "stage": "matrix", "reason": "매트릭스",
         "priority": 1, "signals": {}, "fingerprint": "b"},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                    encoding="utf-8")


def test_alarm_metrics_exposes_collection_failed_ratio(tmp_path):
    path = tmp_path / "d.jsonl"
    _seed_metrics(path)
    resp = _metrics_client(path, meta_alert_collection_failed_ratio=0.3).get(
        "/api/v1/alarm/metrics"
    )
    assert resp.status_code == 200
    meta = [a for a in resp.json()["meta_alerts"] if a["type"] == "collection_failed_ratio"]
    assert meta == [{
        "type": "collection_failed_ratio", "value": 0.5, "threshold": 0.3,
        "window_seconds": 3600, "count": 1, "total": 2,
    }]


def test_alarm_metrics_old_config_without_field_has_no_alert(tmp_path):
    path = tmp_path / "d.jsonl"
    _seed_metrics(path)
    resp = _metrics_client(path).get("/api/v1/alarm/metrics")
    assert resp.status_code == 200
    assert not any(a["type"] == "collection_failed_ratio" for a in resp.json()["meta_alerts"])


# ── 설정 기본값 · 순환 import ───────────────────────────────────────────


def test_config_defaults_are_off():
    fields = NoiseGateConfig.model_fields
    assert fields["collection_failed_page_sev3_only"].default is False
    assert fields["meta_alert_collection_failed_ratio"].default == 0.0


@pytest.mark.parametrize("order", [
    ("noise_gate.domain.page_review", "noise_gate.domain.notification_policy"),
    ("noise_gate.domain.notification_policy", "noise_gate.domain.page_review"),
    ("noise_gate.infrastructure.decision_store",),
])
def test_no_circular_import(order):
    code = "; ".join(f"import {m}" for m in order)
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_domain_module_imports_stay_pure():
    # notification_policy가 infrastructure·application·src를 끌어오지 않는지(도메인 순수성)
    code = (
        "import sys; import noise_gate.domain.notification_policy; "
        "bad=[m for m in sys.modules if m.startswith(('noise_gate.infrastructure',"
        "'noise_gate.application','src.'))]; print(bad); assert not bad"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
