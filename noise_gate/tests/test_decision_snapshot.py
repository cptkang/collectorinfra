"""판정 비트 동일 스냅샷 (plans/112 S6 수용 기준 ① — 근거 기록 전후 판정 불변).

`decide_notification`의 판정 6-튜플 `(tier, reason, priority, signals, fingerprint, stage)`를
대표 입력(단계 14종 × 경계 케이스)에 대해 **근거 기록(`evidence`) 도입 전에** 떠 둔 픽스처
`fixtures/decision_snapshot.json`과 대조한다. 근거는 판정 옆에 붙는 감사 필드일 뿐이므로
이 스냅샷은 어떤 근거 변경 뒤에도 한 글자도 달라지면 안 된다.

픽스처 재생성은 **판정을 의도적으로 바꾼 경우에만** 한다(그때는 결정 기록이 먼저다):
    .venv/bin/python -m noise_gate.tests.test_decision_snapshot --write
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from noise_gate.domain.notification_policy import decide_notification
from noise_gate.domain.silence import SilenceRule
from noise_gate.tests.test_notification_policy import IMP_MAP, make_config, make_ctx, make_event

FIXTURE = Path(__file__).parent / "fixtures" / "decision_snapshot.json"

# 침묵 만료 판정 기준 시각 — 스냅샷이 실행 시각에 흔들리지 않게 고정한다.
NOW = datetime(2026, 9, 22, 1, 0, 0, tzinfo=UTC)


def _cfg(**kwargs) -> SimpleNamespace:
    return make_config(importance_value_map=IMP_MAP, **kwargs)


def _analysis(**kwargs) -> SimpleNamespace:
    base = dict(
        pattern_type="",
        is_routine=None,
        llm_actionability=None,
        ai_message_severity=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _ctx(**kwargs) -> dict:
    """make_ctx + 동결 계약 밖의 선택 키(parent_avail_status·cascaded 등)."""
    extra = {k: kwargs.pop(k) for k in list(kwargs) if k not in {
        "importance_id", "maintenance", "noti_policy", "source",
    }}
    ctx = make_ctx(**kwargs)
    ctx.update(extra)
    return ctx


def _rule(**kwargs) -> SilenceRule:
    base = dict(
        id="slc_snap",
        db_id="",
        server_name="cop0-*",
        alarm_name="",
        resource_name="",
        max_severity=2,
        reason="월간 배포 점검",
        created_by="adm",
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=2),
    )
    base.update(kwargs)
    return SilenceRule(**base)


def _cases() -> dict[str, dict]:
    """케이스 ID → decide_notification 인자. ID는 픽스처 키라 바꾸지 않는다."""
    cfg = _cfg
    ev = make_event
    return {
        # ── 비운영 알람 ──
        "non_alarm/on_marker_only": dict(
            event=ev(severity=1, alarm_name="계정 생성 승인 요청"), ctx=_ctx(),
            config=cfg(non_alarm_filter_enabled=True)),
        "non_alarm/on_precedes_sev3": dict(
            event=ev(severity=3, alarm_name="작업 안내 공지"), ctx=_ctx(),
            config=cfg(non_alarm_filter_enabled=True)),
        "non_alarm/on_alarm_marker_wins": dict(
            event=ev(severity=2, alarm_name="승인 요청 — CPU 사용률 초과"), ctx=_ctx(),
            config=cfg(non_alarm_filter_enabled=True)),
        "non_alarm/off_passes_through": dict(
            event=ev(severity=1, alarm_name="계정 생성 승인 요청"), ctx=_ctx(),
            config=cfg()),
        # ── 심각도3 ──
        "severity3/plain": dict(event=ev(severity=3), ctx=_ctx(), config=cfg()),
        "severity3/ignores_maintenance": dict(
            event=ev(severity=3), ctx=_ctx(maintenance=True, noti_policy="suppress"),
            config=cfg()),
        "severity3/ai_boost_2_to_3": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(enable_ai_severity_boost=True),
            analysis=_analysis(ai_message_severity=3)),
        "severity3/ai_boost_disabled": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(),
            analysis=_analysis(ai_message_severity=3)),
        # ── 자가복구 · 독립 해소 ──
        "self_heal/match": dict(
            event=ev(severity=0), ctx=_ctx(), config=cfg(), kw=dict(self_heal=True)),
        "resolved/suppress_default": dict(event=ev(severity=0), ctx=_ctx(), config=cfg()),
        "resolved/to_dashboard": dict(
            event=ev(severity=0), ctx=_ctx(), config=cfg(resolved_to_dashboard=True)),
        # ── 수집 실패 ──
        "collection_failed/ctx_none": dict(event=ev(severity=2), ctx=None, config=cfg()),
        "collection_failed/source_unavailable": dict(
            event=ev(severity=1), ctx=_ctx(source="unavailable"), config=cfg()),
        # ── 유지보수 ──
        "maintenance/on": dict(event=ev(severity=2), ctx=_ctx(maintenance=True), config=cfg()),
        # ── 침묵 ──
        "silence/match": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(), kw=dict(silence_rules=[_rule()])),
        "silence/no_match_server": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(),
            kw=dict(silence_rules=[_rule(server_name="DB-*")])),
        "silence/no_match_severity_cap": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(),
            kw=dict(silence_rules=[_rule(max_severity=1)])),
        "silence/expired": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(),
            kw=dict(silence_rules=[_rule(expires_at=NOW - timedelta(minutes=1))])),
        "silence/empty_reason": dict(
            event=ev(severity=1), ctx=_ctx(), config=cfg(),
            kw=dict(silence_rules=[_rule(reason="")])),
        # ── 의존성 ──
        "dependency/one_hop": dict(
            event=ev(severity=2), ctx=_ctx(parent_avail_status=2),
            config=cfg(dependency_suppression=True)),
        "dependency/multi_hop_root_notified": dict(
            event=ev(severity=2),
            ctx=_ctx(cascaded=True, root_notified=True, root_resource="r-9",
                     root_resource_name="core-sw-01"),
            config=cfg(dependency_suppression=True)),
        "dependency/multi_hop_root_not_notified": dict(
            event=ev(severity=2),
            ctx=_ctx(cascaded=True, root_notified=False, root_resource="r-9"),
            config=cfg(dependency_suppression=True)),
        "dependency/on_parent_unknown": dict(
            event=ev(severity=2), ctx=_ctx(parent_avail_status=None),
            config=cfg(dependency_suppression=True)),
        "dependency/off_parent_abnormal": dict(
            event=ev(severity=2), ctx=_ctx(parent_avail_status=2), config=cfg()),
        # ── 인히비션 ──
        "inhibition/on": dict(
            event=ev(severity=1), ctx=_ctx(), config=cfg(inhibition_enabled=True),
            kw=dict(inhibited=True)),
        "inhibition/off_signal_ignored": dict(
            event=ev(severity=1), ctx=_ctx(), config=cfg(), kw=dict(inhibited=True)),
        # ── 플래핑 ──
        "flapping/under_cap": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(flapping_enabled=True),
            kw=dict(flapping=True)),
        "flapping/over_cap": dict(
            event=ev(severity=2), ctx=_ctx(),
            config=cfg(flapping_enabled=True, suppress_max_severity=1),
            kw=dict(flapping=True)),
        # ── 스톰 · 상관 ──
        "storm/on": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(storm_grouping_enabled=True),
            kw=dict(storm=True)),
        "correlation/on": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(cross_host_correlation_enabled=True),
            kw=dict(correlated=True)),
        # ── 계획-무해 주석 ──
        "annotation/resolution": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(annotation_planned_suppress=True),
            kw=dict(annotation={"planned_work": True, "resolution": True})),
        "annotation/correlated": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(annotation_planned_suppress=True),
            kw=dict(correlated=True, annotation={"planned_work": True})),
        "annotation/change_nearby": dict(
            event=ev(severity=1), ctx=_ctx(change_nearby=True),
            config=cfg(annotation_planned_suppress=True),
            kw=dict(annotation={"planned_work": True})),
        "annotation/all_three": dict(
            event=ev(severity=2), ctx=_ctx(change_nearby=True),
            config=cfg(annotation_planned_suppress=True),
            kw=dict(correlated=True, annotation={"planned_work": True, "resolution": True})),
        "annotation/no_corroboration": dict(
            event=ev(severity=2), ctx=_ctx(), config=cfg(annotation_planned_suppress=True),
            kw=dict(annotation={"planned_work": True})),
        # ── 매트릭스 ──
        "matrix/sev2_high": dict(event=ev(severity=2), ctx=_ctx(), config=cfg()),
        "matrix/sev2_mid": dict(event=ev(severity=2), ctx=_ctx(importance_id="MID"),
                                config=cfg()),
        "matrix/sev2_low": dict(event=ev(severity=2), ctx=_ctx(importance_id="LOW"),
                                config=cfg()),
        "matrix/sev1_high": dict(event=ev(severity=1), ctx=_ctx(), config=cfg()),
        "matrix/sev1_mid": dict(event=ev(severity=1), ctx=_ctx(importance_id="MID"),
                                config=cfg()),
        "matrix/sev1_low": dict(event=ev(severity=1), ctx=_ctx(importance_id="LOW"),
                                config=cfg()),
        "matrix/unmapped_importance": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="ZZZ"), config=cfg()),
        "matrix/undefined_severity": dict(event=ev(severity=4), ctx=_ctx(), config=cfg()),
        "matrix/promote_noti_policy": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID", noti_policy="notify"),
            config=cfg()),
        "matrix/demote_noti_policy": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID", noti_policy="suppress"),
            config=cfg()),
        "matrix/demote_floor_suppress": dict(
            event=ev(severity=1), ctx=_ctx(importance_id="LOW", noti_policy="suppress"),
            config=cfg()),
        "matrix/promote_ceiling_page": dict(
            event=ev(severity=2), ctx=_ctx(noti_policy="notify"), config=cfg()),
        "matrix/conflict_promote_wins": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID", noti_policy="notify"),
            config=cfg(), analysis=_analysis(is_routine=True)),
        "matrix/routine_demote": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID"), config=cfg(),
            analysis=_analysis(is_routine=True, pattern_type="routine")),
        "matrix/routine_above_cap_no_demote": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID"),
            config=cfg(suppress_max_severity=1), analysis=_analysis(is_routine=True)),
        "matrix/non_routine_promote": dict(
            event=ev(severity=1), ctx=_ctx(importance_id="MID"), config=cfg(),
            analysis=_analysis(is_routine=False)),
        "matrix/history_pattern_fallback": dict(
            event=ev(severity=1), ctx=_ctx(importance_id="MID"), config=cfg(),
            history=SimpleNamespace(pre_classification="burst")),
        "matrix/llm_actionable": dict(
            event=ev(severity=1), ctx=_ctx(importance_id="MID"),
            config=cfg(enable_llm_actionability=True),
            analysis=_analysis(llm_actionability="actionable")),
        "matrix/llm_noise": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID"),
            config=cfg(enable_llm_actionability=True),
            analysis=_analysis(llm_actionability="noise")),
        "matrix/llm_disabled_ignored": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID"), config=cfg(),
            analysis=_analysis(llm_actionability="noise")),
        "matrix/change_nearby_promote": dict(
            event=ev(severity=1), ctx=_ctx(importance_id="MID", change_nearby=True),
            config=cfg()),
        "matrix/promote_and_many_demotes": dict(
            event=ev(severity=2), ctx=_ctx(importance_id="MID", noti_policy="suppress",
                                         change_nearby=True),
            config=cfg(enable_llm_actionability=True),
            analysis=_analysis(is_routine=True, llm_actionability="noise")),
    }


def compute_snapshot() -> dict[str, dict]:
    """전 케이스의 판정 6-튜플을 JSON 직렬화 가능한 dict로 계산한다."""
    out: dict[str, dict] = {}
    for case_id, spec in _cases().items():
        d = decide_notification(
            spec["event"],
            spec.get("history"),
            spec.get("analysis"),
            spec["ctx"],
            spec["config"],
            now=NOW,
            **spec.get("kw", {}),
        )
        out[case_id] = {
            "tier": d.tier,
            "reason": d.reason,
            "priority": d.priority,
            "signals": d.signals,
            "fingerprint": d.fingerprint,
            "stage": d.stage,
        }
    return out


def test_decision_snapshot_is_bit_identical():
    # 판정 6-튜플이 도입 전 스냅샷과 한 글자도 다르지 않아야 한다(근거 기록은 판정 무관).
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(compute_snapshot(), ensure_ascii=False))
    assert set(actual) == set(expected)
    for case_id in expected:
        assert actual[case_id] == expected[case_id], case_id


def test_snapshot_covers_every_stage():
    # 스냅샷이 14단계를 모두 밟는지 고정한다 — 케이스가 조용히 빠지면 비트 동일이 공허해진다.
    from noise_gate.domain.notification_policy import STAGE_ORDER

    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert {row["stage"] for row in expected.values()} == set(STAGE_ORDER)


if __name__ == "__main__":  # pragma: no cover — 픽스처 재생성(의도적 판정 변경 시에만)
    if "--write" not in sys.argv:
        raise SystemExit("재생성하려면 --write 를 붙이십시오.")
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(
        json.dumps(compute_snapshot(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURE}")
