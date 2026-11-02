"""plans/196 sre_agent 쪽 — 조사 화면 결함(D2·D3·D4·D6)과
페이로드 계약 §5.2(`polestar_link`·`rerun`).

녹화 픽스처·가짜 콜러블·가짜 LLM만 쓴다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any

import pytest
from sre_agent.application.investigation_dispatcher import (
    DEEP_SKIP_RERUN,
    PLAYBOOK_LABELS,
    playbook_purpose,
)
from sre_agent.application.playbook_narrator import Opinion, PlaybookNarrator
from sre_agent.application.playbook_prompts import STEP_SYSTEM
from sre_agent.application.playbook_runner import (
    POLESTAR_BRIDGE_MISSING,
    POLESTAR_BRIDGE_MISSING_DETAIL,
    POLESTAR_LINK_UNRESOLVED,
    SQL_PATTERN_KIND,
    STEP_STATUS_NO_OPINION,
    alarm_facts_from_payload,
    compute_windows,
    finalize_playbook_run,
    flow_readings,
    initial_scope,
    polestar_link,
    rerun_hint,
    resolve_polestar_source,
    run_playbook,
    step_finding,
)
from sre_agent.application.playbook_steps import (
    POLESTAR_AXIS_ACTIONS,
    RunScope,
    StepContext,
    recent_changes,
    related_alarms,
)
from sre_agent.domain.playbook import APM_FALLBACK_ID, FALLBACK_ID, WindowSpec, select_playbook
from sre_agent.domain.step_findings import step_status_label, summarize
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load
from test_plan187_w4_dispatch import FakeDeep, FakeLLM, build, make_settings, run_job

ALARM_TIME = "2026-10-27T09:23:00"


def _apm_payload(event_type: str) -> dict:
    return {
        "event": {"resourceType": "apm.Instance", "alarmName": event_type},
        "meta": {"hints": {"solution": "apm", "event_type": event_type}},
    }


# ── D2 — SQL 패턴 플레이북 · APM 공통 점검 ──────────────────────────────────


@pytest.mark.parametrize(
    "event_type",
    ["SQL_TOOMANY_FETCH", "WARNING_SQL_TOOMANY_FETCH", "DB_TOOMANY_FETCH",
     "WARNING_DB_BAD_RESPONSE", "JDBC_BAD_RESPONSE", "JDBC_STMT_EXCEPTION"],
)
def test_sql_event_types_select_was_sql(event_type: str) -> None:
    facts = alarm_facts_from_payload(_apm_payload(event_type))
    assert SQL_PATTERN_KIND in facts.kinds
    assert select_playbook(facts, PLAYBOOKS) == (
        next(p for p in PLAYBOOKS if p.id == "was.sql"), "match:resource_type+any_kind"
    )


def test_unmatched_apm_alarm_selects_apm_fallback_not_host_fallback() -> None:
    pb, matched_by = select_playbook(alarm_facts_from_payload(_apm_payload("SOMETHING_NEW")),
                                     PLAYBOOKS)
    assert pb is not None and pb.id == APM_FALLBACK_ID and matched_by == "match:resource_type"
    assert not any(s.action.startswith("host.") for s in pb.steps)
    host = alarm_facts_from_payload({"event": {"resourceType": "server", "alarmName": "XYZ"}})
    assert select_playbook(host, PLAYBOOKS)[0].id == FALLBACK_ID


def test_existing_apm_types_unchanged() -> None:
    for et, pid in [("TX_BAD_RESPONSE", "was.latency"), ("OUTOFMEMORY", "was.heap"),
                    ("HIGH_RATE_FAIL", "was.error"), ("SERVICE_QUEUING", "was.active"),
                    ("DB_CONN_UNCLOSED", "was.pool")]:
        facts = alarm_facts_from_payload(_apm_payload(et))
        assert SQL_PATTERN_KIND not in facts.kinds
        assert select_playbook(facts, PLAYBOOKS)[0].id == pid


def test_was_sql_uses_existing_actions_only() -> None:
    pb = next(p for p in PLAYBOOKS if p.id == "was.sql")
    actions = [s.action for s in pb.steps]
    assert {"apm.active_sql", "apm.slow_tx_breakdown", "change.recent"} <= set(actions)
    assert not any(a.startswith("host.") for a in actions)


# ── D6 — 사용자 문장 · 운영자 상세 · 상태 라벨 ─────────────────────────────


def test_bridge_missing_reason_split_user_sentence_and_detail() -> None:
    for text in (POLESTAR_BRIDGE_MISSING,):
        assert "meta." not in text and "plans/" not in text and "infra_db_ids" not in text
    assert "meta.episode.infra_db_ids" in POLESTAR_BRIDGE_MISSING_DETAIL
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], next(p for p in PLAYBOOKS if p.id == "was.latency"),
                       matched_by="t", polestar=FakeCaller(fx), apm=FakeCaller(fx),
                       max_workers=1, for_each_max=1)
    s4 = run.section("S4")
    assert s4 is not None and s4.status == "missing"
    assert POLESTAR_BRIDGE_MISSING in (s4.reason or "") and "plans/" not in (s4.reason or "")
    assert s4.to_dict()["reason_detail"] == POLESTAR_BRIDGE_MISSING_DETAIL
    assert "polestar_reason_detail" not in run.scope  # 운영자 상세는 섹션에만(D6)
    assert all("plans/" not in n for n in run.notes)


def test_reason_detail_and_reused_from_are_additive_keys() -> None:
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], next(p for p in PLAYBOOKS if p.id == "was.latency"),
                       matched_by="t", polestar=FakeCaller(fx), apm=FakeCaller(fx),
                       polestar_source="polestar", max_workers=1, for_each_max=1)
    for s in run.sections:
        d = s.to_dict()
        assert "reason_detail" not in d and "reused_from" not in d
    assert "polestar_link" not in run.scope and "polestar_reason_detail" not in run.scope


@pytest.mark.parametrize(("raw", "label"), [
    ("missing", "수집 결손"), ("skipped", "생략"), ("ok", "완료"), ("failed", "실패"),
    ("timeout", "시간 초과"), ("weird", "weird"),
])
def test_step_status_labels(raw: str, label: str) -> None:
    assert step_status_label(raw) == label


def test_no_opinion_reason_uses_korean_label() -> None:
    fx = _load("latency_sql")
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                       apm=FakeCaller(fx), max_workers=1, for_each_max=1, synthesize=True)
    assert run.opinions["S4"].dropped_reason == STEP_STATUS_NO_OPINION.format(status="수집 결손")
    assert "missing" not in (run.opinions["S4"].dropped_reason or "")


def test_activity_labels_are_korean() -> None:
    assert PLAYBOOK_LABELS[FALLBACK_ID] == "공통 점검"
    assert playbook_purpose("fallback").startswith("맞는 유형 플레이북이 없어")


# ── D3 · D4 — 축별 결정적 요약 · 조회 행 ≠ 타임라인 행 ────────────────────


def _gateway_event(i: int, base: int, *, time_ms: Any = "auto") -> dict:
    return {
        "time_ms": base + i * 60_000 if time_ms == "auto" else time_ms, "level": "warning",
        "event_type": "SQL_TOOMANY_FETCH", "event_kind": "error", "value": 1.0, "message": "m",
        "source_id": "cm_prd", "domain_id": 1, "domain_name": "d", "instance_id": 3,
        "instance_name": "A1", "instance_oid": 1, "application": "/x", "profile_ref": None,
    }


def _envelope(rows: list[dict], tool: str) -> str:
    return json.dumps({"rows": rows, "row_count": len(rows), "queried_at": "x",
                       "source_kind": "apm", "tool": tool, "was_signals": [], "limits": []})


def _ctx(apm: Any, scope: RunScope | None = None) -> StepContext:
    return StepContext(
        payload={"event": {}}, windows=compute_windows(ALARM_TIME, WindowSpec()),
        scope=scope or RunScope(alarm_instance="A1", polestar_reason=POLESTAR_BRIDGE_MISSING,
                                polestar_reason_detail=POLESTAR_BRIDGE_MISSING_DETAIL),
        args={}, targets=["A1"], polestar=None, apm=apm, now=datetime.now, prior={},
    )


def _finding(res: Any) -> str | None:
    return summarize(res.action, res.to_dict(), {})


def test_d3_gateway_shaped_rows_all_reach_timeline() -> None:
    """재현 시도 — 게이트웨이 행 모양(`apm_core.event_row`) 7행은 타임라인 7행이 된다."""
    base = int(datetime.fromisoformat(ALARM_TIME).timestamp() * 1000)
    rows = [_gateway_event(i, base) for i in range(7)]
    res = related_alarms(_ctx(lambda calls: [_envelope(rows, "apm_events")] * len(calls)))
    res.action = "xsource.related_alarms"
    assert res.status == "ok" and len(res.table["rows"]) == 7
    assert res.reason.startswith("제니퍼: 이벤트 7건 · 폴스타: 폴스타 서버 미확정")
    assert "타임라인 반영" not in res.reason
    assert res.reason_detail == POLESTAR_BRIDGE_MISSING_DETAIL


def test_d3_rows_without_time_report_gap_deterministically() -> None:
    base = int(datetime.fromisoformat(ALARM_TIME).timestamp() * 1000)
    rows = [_gateway_event(i, base) for i in range(5)] + [
        _gateway_event(9, base, time_ms=None), _gateway_event(10, base, time_ms="x")
    ]
    res = related_alarms(_ctx(lambda calls: [_envelope(rows, "apm_events")] * len(calls)))
    res.action = "xsource.related_alarms"
    assert len(res.table["rows"]) == 5
    gap = "제니퍼: 이벤트 7건 중 타임라인 반영 5건 — 발생 시각을 읽지 못한 이벤트 2건"
    assert gap in res.reason
    assert gap in (_finding(res) or "")


def test_d3_all_rows_without_time_is_not_silent_zero() -> None:
    rows = [_gateway_event(i, 0, time_ms=None) for i in range(7)]
    res = related_alarms(_ctx(lambda calls: [_envelope(rows, "apm_events")] * len(calls)))
    res.action = "xsource.related_alarms"
    text = _finding(res) or ""
    assert "결과 0건" in text and "이벤트 7건 중 타임라인 반영 0건" in text


def test_d4_change_axes_summary_zero_rows_means_none_and_itsm_tail_only() -> None:
    res = recent_changes(_ctx(lambda calls: [
        _envelope([], t) for t, _ in calls
    ]))
    res.action = "change.recent"
    assert res.status == "ok"
    assert res.reason == (
        "제니퍼: 조회 성공 0건 = 변경 없음 · "
        f"폴스타: 폴스타 서버 미확정 — {POLESTAR_BRIDGE_MISSING}"
    )
    assert "ITSM" not in res.reason and "itsm" not in res.reason
    text = _finding(res) or ""
    assert text.endswith(" · ITSM 미연동 · 일부 결손")
    assert "조회 성공 0건 = 변경 없음" in text


def test_d4_prompt_rule_and_axes_summary_reach_llm() -> None:
    assert "조회 성공 0건은 '없음'의 근거다 — '수집 못 함'으로 쓰지 마라." in STEP_SYSTEM
    res = recent_changes(_ctx(lambda calls: [_envelope([], t) for t, _ in calls]))
    res.step, res.action, res.title = "S7", "change.recent", "최근 변경 대조"
    from sre_agent.application.playbook_prompts import render_step_messages

    msgs = render_step_messages(section=res.to_dict(include_data=False), focus="f",
                                max_sentences=3, readings={})
    assert "제니퍼: 조회 성공 0건 = 변경 없음" in msgs[1]["content"]


# ── §5.2 polestar_link ─────────────────────────────────────────────────────


def _latency_alarm(link: dict | None = None, *, hostname: str | None = None) -> dict:
    alarm = copy.deepcopy(_load("latency_sql")["alarm"])
    if hostname is not None:
        alarm["event"]["hostname"] = hostname
    if link is not None:
        alarm["meta"]["hints"]["polestar_link"] = link
    return alarm


_LINKED = {"status": "linked", "db_id": "polestar", "server_name": "PS-REG-01",
           "hostname": "wasdemo01", "basis": "user_selected", "by": "kim", "at": "2026-10-27T10:00"}


def test_link_absent_key_keeps_current_behavior() -> None:
    plain = _latency_alarm()
    bogus = _latency_alarm({"status": "bogus"})
    half = _latency_alarm({"status": "linked", "db_id": "polestar"})  # server_name 없음 = 무효
    for p in (bogus, half):
        assert polestar_link(p) is None
        assert resolve_polestar_source(p) == resolve_polestar_source(plain)
        assert initial_scope(p, None, POLESTAR_BRIDGE_MISSING).to_dict() == initial_scope(
            plain, None, POLESTAR_BRIDGE_MISSING
        ).to_dict()
    assert resolve_polestar_source(plain, "polestar") == ("polestar", None)


def test_link_priority_infra_ids_then_link_then_configured() -> None:
    alarm = _latency_alarm(_LINKED)
    assert resolve_polestar_source(alarm, "configured_db") == ("polestar", None)
    alarm["meta"]["episode"] = {"episode_id": "e", "infra_db_ids": ["polestar_cm_gp"],
                                "members": []}
    assert resolve_polestar_source(alarm, "configured_db") == ("polestar_cm_gp", None)
    scope = initial_scope(alarm, "polestar_cm_gp", None)
    assert scope.polestar_link is None and scope.polestar_server == "wasdemo01"


def test_linked_uses_server_name_with_basis_and_s0_keeps_it() -> None:
    fx = _load("latency_sql")
    caller = FakeCaller(fx, strict=False)
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    run = run_playbook(_latency_alarm(_LINKED, hostname="WASDEMO01.kb.local"), pb,
                       matched_by="t", polestar=caller, apm=caller, max_workers=1,
                       for_each_max=1)
    basis = "사용자 선택으로 연결 — kim·2026-10-27T10:00"
    assert run.scope["polestar_source"] == "polestar"
    assert run.scope["polestar_server"] == "PS-REG-01"
    assert run.scope["polestar_basis"] == basis and basis in run.notes
    pol = [a for t, a in caller.calls if t.startswith("polestar_")]
    assert pol and all(
        (a.get("server_name") or a.get("hostname")) == "PS-REG-01" for a in pol
    )
    assert basis in (run.section("S4").reason or "")  # type: ignore[union-attr]


_ABSENT = {"status": "absent", "by": "lee", "at": "2026-10-27T11:00"}


@pytest.mark.parametrize(("link", "configured", "reason"), [
    (_ABSENT, "polestar", "폴스타 미등록(사용자 확인 · lee·2026-10-27T11:00)"),
    (_ABSENT, None, "폴스타 미등록(사용자 확인 · lee·2026-10-27T11:00)"),
    ({"status": "unresolved"}, None, POLESTAR_LINK_UNRESOLVED),
])
def test_absent_and_unresolved_skip_polestar_steps_not_missing(
    link: dict, configured: str | None, reason: str
) -> None:
    """absent는 설정 매핑보다 앞서 생략 · unresolved는 설정 소스가 없을 때만 생략."""
    fx = _load("latency_sql")
    caller = FakeCaller(fx)
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    run = run_playbook(_latency_alarm(link), pb, matched_by="t", polestar=caller, apm=caller,
                       polestar_source=configured, max_workers=1, for_each_max=1)
    assert run.scope["polestar_source"] is None and run.scope["polestar_link"] == link["status"]
    assert not [t for t, _ in caller.calls if t.startswith("polestar_")]
    for sid in ("S4", "S6"):
        s = run.section(sid)
        assert s is not None and s.status == "skipped" and s.reason == reason and not s.missing
    s5, s7 = run.section("S5"), run.section("S7")
    assert s5 is not None and s5.status == "ok" and "polestar_incident_alarms" not in s5.missing
    assert f"폴스타: {reason}" in (s5.reason or "")
    assert s7 is not None and s7.data["axes"]["polestar"]["status"] == "skipped"
    assert "polestar_change_history" not in s7.missing


def test_source_priority_order() -> None:
    """infra_db_ids → linked → absent → 설정 매핑 → unresolved → 힌트 없음(plans/196 §5.2)."""
    unresolved = _latency_alarm({"status": "unresolved"})
    assert resolve_polestar_source(unresolved, "cfg") == ("cfg", None)
    assert resolve_polestar_source(unresolved) == (None, POLESTAR_LINK_UNRESOLVED)
    assert resolve_polestar_source(_latency_alarm(_ABSENT), "cfg")[0] is None
    assert resolve_polestar_source(_latency_alarm(_LINKED), "cfg") == ("polestar", None)
    assert resolve_polestar_source(_latency_alarm(), "cfg") == ("cfg", None)
    assert resolve_polestar_source(_latency_alarm()) == (None, POLESTAR_BRIDGE_MISSING)


def test_unresolved_with_configured_source_is_current_behavior() -> None:
    """플래그를 켜도 현행보다 나빠지지 않는다 — unresolved + 설정 매핑 = 힌트 없음 + 설정 매핑."""
    fx = _load("latency_sql")
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    basis = "설정 매핑으로 대체(cm_prd→polestar) — 존이 다른 알람이면 다른 서버를 조회할 수 있음"
    runs, calls = [], []
    for alarm in (_latency_alarm({"status": "unresolved"}), _latency_alarm()):
        caller = FakeCaller(fx)
        runs.append(run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller,
                                 polestar_source="polestar", polestar_source_basis=basis,
                                 max_workers=1, for_each_max=1))
        calls.append(caller.calls)
    hinted, plain = runs
    assert hinted.scope == plain.scope and "polestar_link" not in hinted.scope
    assert hinted.scope["polestar_basis"] == basis and basis in hinted.notes
    assert hinted.notes == plain.notes and calls[0] == calls[1]
    def _shape(run: Any) -> list[dict]:  # 소요 시간(elapsed_ms)은 실행마다 다르다
        return [{k: v for k, v in s.to_dict().items() if k != "elapsed_ms"} for s in run.sections]

    assert _shape(hinted) == _shape(plain)
    assert any(t.startswith("polestar_") for t, _ in calls[0])


# ── §5.2 rerun ─────────────────────────────────────────────────────────────


def _parent_sections() -> tuple[list[dict], Any]:
    fx = _load("latency_sql")
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    parent = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1,
                          narrator=PlaybookNarrator(FakeLLM()))
    return parent.to_briefing_dict()["sections"], pb


def test_rerun_hint_parsing() -> None:
    assert rerun_hint(_latency_alarm()) is None
    a = _latency_alarm()
    a["meta"]["hints"]["rerun"] = {"parent_investigation_id": "p1", "axes": ["apm"],
                                   "prior_sections": []}
    assert rerun_hint(a) is None  # 폴스타 축이 아니면 무시
    a["meta"]["hints"]["rerun"]["axes"] = ["polestar"]
    hint = rerun_hint(a)
    assert hint is not None and hint.parent_investigation_id == "p1"


def test_rerun_reuses_non_polestar_steps_without_tool_calls() -> None:
    prior, pb = _parent_sections()
    fx = _load("latency_sql")
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "inv-parent",
                                       "axes": ["polestar"], "prior_sections": prior}
    caller = FakeCaller(fx)
    llm = FakeLLM()
    run = run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller, max_workers=1,
                       for_each_max=1, narrator=PlaybookNarrator(llm))
    by_action = {s.id: s.action for s in pb.steps}
    reused = {s.step for s in run.sections if s.reused_from == "inv-parent"}
    expected = {sid for sid, a in by_action.items()
                if a not in POLESTAR_AXIS_ACTIONS and a != "synthesize"}
    assert reused == expected and reused
    # 재사용 단계는 도구 호출 0 — 호출은 폴스타 축 단계에서만
    assert {c["step"] for c in run.tool_calls} <= {
        sid for sid, a in by_action.items() if a in POLESTAR_AXIS_ACTIONS
    }
    prior_by = {s["step"]: s for s in prior}
    assert any(prior_by[sid].get("opinion") for sid in reused)
    for sid in reused:
        sec = run.section(sid)
        assert sec is not None and sec.status == prior_by[sid]["status"]
        assert sec.table == prior_by[sid]["table"]
        assert sec.to_dict()["reused_from"] == "inv-parent"
        if prior_by[sid].get("opinion"):
            assert run.opinions[sid].text == prior_by[sid]["opinion"]  # 부모 소견 그대로
    # 소견기 호출 = 다시 돈 소견 대상 단계 + 종합 1(재사용 단계 재호출 0)
    rerun_narrated = [s.id for s in pb.steps if s.narrate is not None and s.id not in reused
                      and run.section(s.id).status == "ok"]  # type: ignore[union-attr]
    assert llm.calls == len(rerun_narrated) + 1
    assert run.section("S9").status == "ok"  # type: ignore[union-attr]
    assert any(n.startswith("재조회 — 폴스타 축 단계만 다시 조회") for n in run.notes)


def test_rerun_missing_prior_section_is_skipped_not_called() -> None:
    _, pb = _parent_sections()
    fx = _load("latency_sql")
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "p", "axes": ["polestar"],
                                       "prior_sections": []}
    caller = FakeCaller(fx)
    run = run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller, max_workers=1,
                       for_each_max=1)
    s0 = run.section("S0")
    assert s0 is not None and s0.status == "skipped" and s0.reused_from == "p"
    assert "S0" not in {c["step"] for c in run.tool_calls}


def test_finalize_without_reuse_is_unchanged() -> None:
    fx = _load("latency_sql")
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    run = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                       apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    assert not any("prior_opinion" in s.artifacts for s in run.sections)
    finalize_playbook_run(run, pb, step_opinions={"S1": Opinion(text="x")})
    assert run.opinions["S1"].text == "x"


def test_dispatcher_rerun_skips_deep_dive() -> None:
    prior, _ = _parent_sections()
    settings = make_settings(sre_deep_dive_mode="parallel")
    deep = FakeDeep(settings)
    fx = _load("latency_sql")
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "inv-parent",
                                       "axes": ["polestar"], "prior_sections": prior}
    job = run_job(build(settings, FakeCaller(fx), deep=deep), alarm)
    assert deep.jobs == []
    assert DEEP_SKIP_RERUN in json.dumps(job.briefing, ensure_ascii=False)


def test_flow_finding_for_reused_section() -> None:
    """재사용 섹션도 결과 한 줄은 결정적으로 나온다(표·상태 그대로)."""
    prior, pb = _parent_sections()
    fx = _load("latency_sql")
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "p", "axes": ["polestar"],
                                       "prior_sections": prior}
    run = run_playbook(alarm, pb, matched_by="t", polestar=FakeCaller(fx), apm=FakeCaller(fx),
                       max_workers=1, for_each_max=1)
    sec = run.section("S1")
    assert sec is not None and step_finding(sec, flow_readings(pb, {"S1": sec}))


def test_rerun_reused_s0_reason_follows_current_link() -> None:
    """부모 S0 사유의 「선택 대기」가 재조회 범위(확정 연결)와 어긋나지 않는다."""
    fx = _load("latency_sql")
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    parent = run_playbook(_latency_alarm({"status": "unresolved"}), pb, matched_by="t",
                          polestar=FakeCaller(fx), apm=FakeCaller(fx), max_workers=1,
                          for_each_max=1)
    assert POLESTAR_LINK_UNRESOLVED in (parent.section("S0").reason or "")  # type: ignore[union-attr]
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "p", "axes": ["polestar"],
                                       "prior_sections": parent.to_briefing_dict()["sections"]}
    run = run_playbook(alarm, pb, matched_by="t", polestar=FakeCaller(fx, strict=False),
                       apm=FakeCaller(fx, strict=False), max_workers=1, for_each_max=1)
    s0 = run.section("S0")
    assert s0 is not None and s0.reused_from == "p"
    assert POLESTAR_LINK_UNRESOLVED not in (s0.reason or "")
    assert (s0.reason or "").endswith("사용자 선택으로 연결 — kim·2026-10-27T10:00")
    assert s0.reason_detail is None


def test_rerun_prior_readings_reuse_and_absent_key() -> None:
    prior, pb = _parent_sections()
    fx = _load("latency_sql")
    parent = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    runs = {}
    for with_readings in (True, False):
        alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
        hint: dict[str, Any] = {"parent_investigation_id": "p", "axes": ["polestar"],
                                "prior_sections": prior}
        if with_readings:
            hint["prior_readings"] = parent.to_briefing_dict()["readings"]
        alarm["meta"]["hints"]["rerun"] = hint
        runs[with_readings] = run_playbook(
            alarm, pb, matched_by="t", polestar=FakeCaller(fx, strict=False),
            apm=FakeCaller(fx, strict=False), max_workers=1, for_each_max=1,
        )
    pv = parent.readings.values  # type: ignore[union-attr]
    got = runs[True].readings.values  # type: ignore[union-attr]
    assert got["dominant"] == pv["dominant"] is not None
    assert any(n.startswith("재조회 — 재사용 단계가 낸 판독") for n in runs[True].readings.notes)  # type: ignore[union-attr]
    # 키가 없으면 현행(재사용 섹션 없이 계산) — 예외 없음 · 부모 판독 메모 없음
    assert runs[False].readings is not None
    assert not any("부모 조사 값 사용" in n for n in runs[False].readings.notes)
