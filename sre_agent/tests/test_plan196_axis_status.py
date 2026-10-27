"""plans/196 D7 — 섹션 축별 상태(`axis_status`) 직렬화.

화면 「판정 근거 부족」 줄을 축(폴스타·제니퍼·ITSM)별로 나누는 가산 키. 가짜 콜러블만 쓴다.
"""

from __future__ import annotations

from sre_agent.application.playbook_runner import _reused_section, run_playbook
from sre_agent.application.playbook_steps import (
    ACTION_AXIS,
    ACTION_TITLES,
    AXIS_LABELS,
    MIXED_AXIS_ACTIONS,
    NOT_CONNECTED,
    POLESTAR_AXIS_ACTIONS,
    StepResult,
    section_axis_status,
)
from sre_agent.domain.playbook import StepSpec
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load


def test_action_axis_table_consistent_with_polestar_axis_actions() -> None:
    polestar_single = {a for a, ax in ACTION_AXIS.items() if ax == "polestar"}
    assert polestar_single | MIXED_AXIS_ACTIONS == POLESTAR_AXIS_ACTIONS
    assert not set(ACTION_AXIS) & MIXED_AXIS_ACTIONS
    assert set(ACTION_AXIS) | MIXED_AXIS_ACTIONS <= set(ACTION_TITLES)
    assert set(ACTION_AXIS.values()) <= set(AXIS_LABELS)
    # 축 없는 액션 — 표에 없다
    assert "dpm.db_health" not in ACTION_AXIS and "synthesize" not in ACTION_AXIS


def test_single_axis_uses_section_status() -> None:
    d = StepResult(step="S4", action="host.usage_trend", status="missing").to_dict(
        include_data=False
    )
    assert d["axis_status"] == {"polestar": "missing"}
    d = StepResult(step="S1", action="apm.golden_signals", status="ok").to_dict()
    assert d["axis_status"] == {"apm": "ok"}
    d = StepResult(step="S0", action="scope.host_to_was", status="skipped").to_dict()
    assert d["axis_status"] == {"apm": "skipped"}


def test_mixed_step_uses_data_axes_with_labels_only() -> None:
    res = StepResult(
        step="S7",
        action="change.recent",
        status="ok",
        data={"axes": {
            "apm": {"status": "ok", "reason": None},
            "polestar": {"status": "skipped", "reason": "x"},
            "itsm": {"status": "skipped", "reason": "미연동"},
            "other": {"status": "ok"},
        }},
    )
    d = res.to_dict(include_data=False)
    assert d["axis_status"] == {"apm": "ok", "polestar": "skipped", "itsm": "skipped"}
    assert "data" not in d


def test_unknown_action_or_no_value_omits_key() -> None:
    assert "axis_status" not in StepResult(step="S8", action="dpm.db_health").to_dict()
    assert "axis_status" not in StepResult(step="S9", action="synthesize").to_dict()
    assert "axis_status" not in StepResult(step="S5", action="xsource.related_alarms").to_dict()
    assert section_axis_status("xsource.related_alarms", "ok", {"axes": "bad"}) == {}
    assert section_axis_status("host.topology", "", {}) == {}


def test_reused_section_carries_parent_axis_status() -> None:
    step = StepSpec(id="S7", action="change.recent")
    prior = {"status": "ok", "axis_status": {"apm": "ok", "polestar": "missing"}}
    d = _reused_section(step, prior, "inv-p").to_dict(include_data=False)
    assert d["axis_status"] == {"apm": "ok", "polestar": "missing"}
    # 부모가 옛 브리핑(키 없음)이면 단일 축은 상태로 계산 · 혼합은 싣지 않는다
    old = {"status": "skipped"}
    d1 = _reused_section(StepSpec(id="S1", action="apm.golden_signals"), old, "p").to_dict()
    assert d1["axis_status"] == {"apm": "skipped"}
    assert "axis_status" not in _reused_section(step, old, "p").to_dict()


def test_briefing_sections_carry_axis_status() -> None:
    fx = _load("latency_sql")
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                       apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    sections = run.to_briefing_dict()["sections"]
    by = {s["action"]: s for s in sections}
    for action, sec in by.items():
        if action in ACTION_AXIS:
            assert sec["axis_status"] == {ACTION_AXIS[action]: sec["status"]}
        elif action in MIXED_AXIS_ACTIONS:
            assert set(sec["axis_status"]) <= set(AXIS_LABELS) and sec["axis_status"]
        else:
            assert "axis_status" not in sec
    assert any(a in MIXED_AXIS_ACTIONS for a in by)


def test_mixed_step_drops_not_connected_axis() -> None:
    """미연동 축(ITSM 연동 전 등)은 판정 근거 부족의 원인이 아니라 축 상태에서 뺀다."""
    data = {"axes": {
        "apm": {"status": "ok", "reason": None},
        "polestar": {"status": "missing", "reason": "x"},
        "itsm": {"status": "skipped", "reason": NOT_CONNECTED},
    }}
    assert section_axis_status("change.recent", "ok", data) == {"apm": "ok", "polestar": "missing"}
    only = {"axes": {"itsm": {"status": "skipped", "reason": NOT_CONNECTED}}}
    assert "axis_status" not in StepResult(action="change.recent", data=only).to_dict()
