"""plans/195 W4 — 섹션 `queries[{tool, phrase}]` 저장(단계별 조회를 브리핑에 남긴다).

- 조회 문장은 `tool_phrase` — 인자 원문(`tool_label`·args JSON)은 싣지 않는다.
- 문장이 없는 도구는 `{tool}`만 · 같은 (도구, 문장)은 1건.
- 비면 키 없음(가산 키) · 재사용 섹션은 부모 `queries`를 그대로 가진다.
- 부분 브리핑(dispatcher)도 같은 규칙.
"""

from __future__ import annotations

import json

from sre_agent.application.playbook_runner import run_playbook
from sre_agent.application.playbook_steps import StepResult, step_queries, tool_label
from sre_agent.domain.step_findings import tool_phrase
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load
from test_plan187_w4_dispatch import build, make_settings, run_job
from test_plan196_sre_screen_defects import _LINKED, _latency_alarm


def _pb():  # noqa: ANN202
    return next(p for p in PLAYBOOKS if p.id == "was.latency")


def test_step_queries_phrase_dedupe_and_unphrased_tool() -> None:
    args = {"instance": "wasdemo01"}
    calls = [
        ("apm_resource_pool", args),
        ("apm_resource_pool", dict(args)),  # 같은 (도구, 문장) — 1건만
        ("unknown_tool", {"q": "SELECT 1"}),  # 사전 밖 — 도구명만(인자 원문 미저장)
        ("unknown_tool", {"q": "SELECT 2"}),
    ]
    out = step_queries(calls)
    phrase = tool_phrase("apm_resource_pool", args)
    assert phrase
    assert out == [{"tool": "apm_resource_pool", "phrase": phrase}, {"tool": "unknown_tool"}]
    assert "SELECT" not in json.dumps(out, ensure_ascii=False)


def test_queries_is_additive_key() -> None:
    assert "queries" not in StepResult().to_dict()
    d = StepResult(queries=[{"tool": "t", "phrase": "p"}]).to_dict(include_data=False)
    assert d["queries"] == [{"tool": "t", "phrase": "p"}]


def test_run_sections_carry_queries_without_raw_args() -> None:
    fx = _load("latency_sql")
    run = run_playbook(fx["alarm"], _pb(), matched_by="t", polestar=FakeCaller(fx),
                       apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    sections = run.to_briefing_dict()["sections"]
    called = {c["step"] for c in run.tool_calls}
    assert called
    for sec in sections:
        if sec["step"] in called:
            assert sec["queries"], sec["step"]
            assert {q["tool"] for q in sec["queries"]} <= set(sec["tools"])
        else:
            assert "queries" not in sec  # 호출 0 단계는 키 없음
    raw_labels = {tool_label(c["tool"], c["args"]) for c in run.tool_calls}
    for sec in sections:
        for q in sec.get("queries") or []:
            assert set(q) <= {"tool", "phrase"}
            assert q.get("phrase") not in raw_labels
    # 섹션별 (도구, 문장) 중복 없음
    for sec in sections:
        keys = [(q["tool"], q.get("phrase")) for q in sec.get("queries") or []]
        assert len(keys) == len(set(keys))


def test_reused_sections_keep_parent_queries() -> None:
    fx = _load("latency_sql")
    pb = _pb()
    parent = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    prior = parent.to_briefing_dict()["sections"]
    assert any(s.get("queries") for s in prior)
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "inv-parent",
                                       "axes": ["polestar"], "prior_sections": prior}
    run = run_playbook(alarm, pb, matched_by="t", polestar=FakeCaller(fx), apm=FakeCaller(fx),
                       max_workers=1, for_each_max=1)
    prior_by = {s["step"]: s for s in prior}
    reused = [s for s in run.sections if s.reused_from == "inv-parent"]
    assert reused
    for sec in reused:
        d = sec.to_dict(include_data=False)
        assert d.get("queries") == prior_by[sec.step].get("queries"), sec.step


def test_reused_section_from_old_parent_has_no_queries_key() -> None:
    """구버전 부모 섹션(키 없음)·형식 밖 원소 — 재사용 섹션도 키 없음 · 잘못된 원소는 버린다."""
    fx = _load("latency_sql")
    pb = _pb()
    parent = run_playbook(fx["alarm"], pb, matched_by="t", polestar=FakeCaller(fx),
                          apm=FakeCaller(fx), max_workers=1, for_each_max=1)
    prior = [{k: v for k, v in s.items() if k != "queries"}
             for s in parent.to_briefing_dict()["sections"]]
    prior[0]["queries"] = ["x", {"phrase": "p"}, {"tool": "apm_events", "phrase": ""}]
    alarm = _latency_alarm(_LINKED | {"server_name": "wasdemo01"})
    alarm["meta"]["hints"]["rerun"] = {"parent_investigation_id": "p", "axes": ["polestar"],
                                       "prior_sections": prior}
    run = run_playbook(alarm, pb, matched_by="t", polestar=FakeCaller(fx), apm=FakeCaller(fx),
                       max_workers=1, for_each_max=1)
    first = run.section(prior[0]["step"])
    assert first is not None and first.reused_from == "p"
    assert first.to_dict()["queries"] == [{"tool": "apm_events"}]
    for sec in run.sections:
        if sec.reused_from == "p" and sec.step != prior[0]["step"]:
            assert "queries" not in sec.to_dict()


def test_dispatcher_briefing_sections_carry_queries() -> None:
    fx = _load("latency_sql")
    job = run_job(build(make_settings(), FakeCaller(fx)), fx["alarm"])
    sections = job.briefing["playbook"]["sections"]
    assert any(s.get("queries") for s in sections)
    for s in sections:
        for q in s.get("queries") or []:
            assert set(q) <= {"tool", "phrase"}
