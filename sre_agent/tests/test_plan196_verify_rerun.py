"""plans/196 독립 검증 — 재조회(rerun) 종합 · 브리핑 범위의 운영자 상세 · 새 플레이북 재조회.

`xfail(strict=True)`는 검증에서 찾은 결함의 재현이다(고쳐지면 XPASS로 실패 → 마커 삭제).
"""

from __future__ import annotations

import pytest
from sre_agent.application.playbook_runner import (
    POLESTAR_BRIDGE_MISSING,
    POLESTAR_BRIDGE_MISSING_DETAIL,
    finalize_playbook_run,
    run_playbook,
)
from sre_agent.application.playbook_steps import POLESTAR_AXIS_ACTIONS
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load
from test_plan196_sre_screen_defects import _LINKED, _latency_alarm


def _pb(pid: str):  # noqa: ANN202
    return next(p for p in PLAYBOOKS if p.id == pid)


def _parent_and_rerun(pid: str = "was.latency"):  # noqa: ANN202
    fx = _load("latency_sql")
    pb = _pb(pid)
    link = _LINKED | {"server_name": "wasdemo01"}
    parent = run_playbook(_latency_alarm(link), pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    alarm = _latency_alarm(link)
    alarm["meta"]["hints"]["rerun"] = {
        "parent_investigation_id": "inv-parent", "axes": ["polestar"],
        "prior_sections": parent.to_briefing_dict()["sections"],
        # 본체가 함께 보내는 부모 판독(`briefing.playbook.readings`)
        "prior_readings": parent.to_briefing_dict()["readings"],
    }
    caller = FakeCaller(fx, strict=False)
    rerun = run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller, max_workers=1,
                         for_each_max=1)
    return pb, parent, rerun


def test_rerun_synthesis_keeps_jennifer_axis_readings_and_hypotheses() -> None:
    pb, parent, rerun = _parent_and_rerun()
    pv, rv = parent.readings.to_dict(), rerun.readings.to_dict()
    pv, rv = pv.get("values", pv), rv.get("values", rv)
    # 폴스타 축과 무관한(제니퍼 축) 판독은 같은 부모 결과를 재사용했으니 같아야 한다
    for key in ("dominant", "load_mode", "service_spread", "scope"):
        assert rv.get(key) == pv.get(key), key
    finalize_playbook_run(parent, pb)
    finalize_playbook_run(rerun, pb)
    assert parent.synthesis["hypotheses"]
    assert [h["title"] for h in rerun.synthesis["hypotheses"]] == [
        h["title"] for h in parent.synthesis["hypotheses"]
    ]


@pytest.mark.parametrize("pid", ["was.sql", "was.fallback"])
def test_rerun_new_playbooks_call_tools_only_in_polestar_axis_steps(pid: str) -> None:
    pb, _, rerun = _parent_and_rerun(pid)
    polestar_steps = {s.id for s in pb.steps if s.action in POLESTAR_AXIS_ACTIONS}
    assert {c["step"] for c in rerun.tool_calls} <= polestar_steps
    reused = {s.step for s in rerun.sections if s.reused_from == "inv-parent"}
    assert reused == {s.id for s in pb.steps
                      if s.action not in POLESTAR_AXIS_ACTIONS and s.action != "synthesize"}


def test_bridge_missing_detail_stays_out_of_briefing_scope() -> None:
    """D6 — 운영자 상세는 섹션 `reason_detail`에만 실리고 `scope`에는 직렬화되지 않는다
    (교정 전에는 `scope.polestar_reason_detail`로 사용자 뷰에 남았다)."""
    fx = _load("latency_sql")
    run = run_playbook(_latency_alarm(), _pb("was.latency"), matched_by="t",
                       polestar=FakeCaller(fx), apm=FakeCaller(fx), max_workers=1,
                       for_each_max=1)
    assert run.scope["polestar_reason"] == POLESTAR_BRIDGE_MISSING
    assert "polestar_reason_detail" not in run.scope
    assert "polestar_reason_detail" not in run.to_briefing_dict()["scope"]
    assert run.section("S4").to_dict()["reason_detail"] == (  # type: ignore[union-attr]
        POLESTAR_BRIDGE_MISSING_DETAIL
    )
    # 사용자 문장(reason)에는 필드명·계획 번호가 없다
    for sec in run.sections:
        assert "meta.episode" not in (sec.reason or "") and "plans/" not in (sec.reason or "")
