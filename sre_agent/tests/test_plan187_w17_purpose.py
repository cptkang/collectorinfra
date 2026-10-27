"""plans/187 W17 — 실행 기록 `purpose`(무엇 때문에 무엇을 보는지).

플레이북 선택 근거 · 스텝 초점(`narrate.focus`) · HolmesGPT 조사 이유가 이벤트에 실린다.
실 LLM·MCP 호출 0 — W4 가짜 호출기·가짜 LLM만 쓴다.
"""

from __future__ import annotations

from sre_agent.application.investigation_dispatcher import (
    HOLMES_PURPOSE,
    PLAYBOOK_FALLBACK_PURPOSE,
    playbook_purpose,
)
from sre_agent.domain.activity_log import ActivityLimits, ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeLLM, alarm_of, build, make_job
from test_plan187_w4_dispatch import make_settings as playbook_settings

SECRET = "Sup3rS3cret"


def test_purpose_is_optional_masked_and_clipped() -> None:
    log = ActivityLog(masker=mask_activity_text, limits=ActivityLimits(max_label_bytes=64))
    log.append("step_start", engine="playbook", label="a")
    log.append("step_start", engine="playbook", label="b", purpose=f"secret={SECRET} " + "가" * 60)
    first, second = log.since(0)["events"]
    assert "purpose" not in first
    assert SECRET not in second["purpose"]
    assert len(second["purpose"].encode("utf-8")) <= 64 + 8


def test_playbook_purpose_reads_match_conditions() -> None:
    assert playbook_purpose("match:resource_type+alarm_kind") == (
        "알람의 자원 유형·알람 종류가 이 플레이북 조건과 일치"
    )
    assert playbook_purpose("match:any_kind") == "알람의 알람 키워드가 이 플레이북 조건과 일치"
    assert playbook_purpose("fallback") == PLAYBOOK_FALLBACK_PURPOSE
    assert playbook_purpose("none") == "none"
    assert HOLMES_PURPOSE


def test_playbook_job_records_selection_and_step_focus() -> None:
    job = make_job(alarm_of("latency_sql"), "inv-p")
    job.activity = ActivityLog(masker=mask_activity_text)
    d = build(playbook_settings(), FakeCaller(_load("latency_sql")), llm=FakeLLM())
    d(job)
    d.wait_workers(30)
    events = job.activity.since(0)["events"]
    chosen = [e for e in events if e["kind"] == "started" and e["engine"] == "playbook"]
    assert len(chosen) == 1
    assert chosen[0]["label"].startswith("플레이북 ") and chosen[0]["label"].endswith(" 선택")
    assert chosen[0]["purpose"].endswith("이 플레이북 조건과 일치")
    # 선택 줄은 스텝보다 먼저 남는다
    first_step = next(e["seq"] for e in events if e["kind"] == "step_start")
    assert chosen[0]["seq"] < first_step
    steps = [e for e in events if e["kind"] in ("step_start", "step_end")]
    # narrate가 있는 스텝은 초점이 목적으로 실린다(시작·끝 같은 값)
    with_focus = [e for e in steps if e.get("purpose")]
    assert with_focus
    by_step: dict[str, set[str]] = {}
    for e in with_focus:
        by_step.setdefault(e["step"], set()).add(e["purpose"])
    assert all(len(v) == 1 for v in by_step.values())
