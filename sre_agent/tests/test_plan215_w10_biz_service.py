"""plans/215 W10 sre_agent 쪽 — 조사 페이로드 `meta.biz_service`(§4.8 D2 · 수용 15) 소비.

S0가 `apm_members`를 같은 업무 비교 후보로 쓰고 `apm_instance_map(business=)`를 부르지 않는다 ·
키가 없으면 종전 호출 그대로 · 폴스타 비교 ≤3대(연계 대상 `polestar_targets`와 섞지 않음 · 알람 대상
판정 불변) · 브리핑 선택 키 `biz_service`(6요소 불변)를 본다.
녹화 픽스처·가짜 콜러블만 쓴다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import copy
import json
from typing import Any

from sre_agent.application.briefing_builder import build_playbook_briefing
from sre_agent.application.evidence_prefetch import TOOL_METRIC
from sre_agent.application.playbook_runner import (
    BIZ_COMPARE_SMS_MAX,
    POLESTAR_LINK_TARGETS_MAX,
    biz_service_context,
    run_playbook,
)
from sre_agent.domain.severity_signatures import ImportanceVerdict
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load

SIX = ("summary", "timeline", "bottleneck", "cause", "recommendation", "limitations")


class _AnyServer(FakeCaller):
    """지표 응답을 서버와 무관하게 픽스처 서버(wasdemo01) 것으로 돌려준다 — `hot`은 cpu 급등."""

    def __init__(self, fx: dict, hot: str = "") -> None:
        super().__init__(fx, strict=False)
        self.hot = hot

    def lookup(self, tool: str, args: dict) -> dict | None:
        if tool == TOOL_METRIC and args.get("server_name") not in (None, "wasdemo01"):
            resp = copy.deepcopy(super().lookup(tool, {**args, "server_name": "wasdemo01"}))
            if resp and args.get("server_name") == self.hot and args.get("kind") == "cpu":
                inc = resp["window"]["stat_date_incident_from"]
                for row in resp["rows"]:
                    if row["stat_date"] >= inc:
                        row["avg_val"] = 97.0
            return resp
        return super().lookup(tool, args)


def _biz(n_sms: int = 5, n_apm: int = 2, **kw: Any) -> dict[str, Any]:
    return {
        "service_id": 7, "display_name": "인터넷뱅킹", "confidence": "confirmed",
        "sms_members": [
            {"db_id": "polestar", "server_name": f"SVR{i}", "hostname": f"svr{i}",
             "confidence": "inferred" if i == 2 else "confirmed"}
            for i in range(1, n_sms + 1)
        ],
        "apm_members": [
            {"source_id": "default", "domain_id": "3000", "instance_name": f"C{i}",
             "confidence": "inferred" if i == 1 else "confirmed"}
            for i in range(1, n_apm + 1)
        ],
        "truncated": False,
        **kw,
    }


def _alarm(biz: dict[str, Any] | None) -> dict[str, Any]:
    alarm = copy.deepcopy(_load("latency_sql")["alarm"])
    if biz is not None:
        alarm["meta"]["biz_service"] = biz
    return alarm


def _run(alarm: dict[str, Any], caller: FakeCaller, **args: Any):  # noqa: ANN202
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    return run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller,
                        polestar_source="polestar", max_workers=1, for_each_max=1)


def _map_calls(caller: FakeCaller) -> list[dict]:
    return [a for t, a in caller.calls if t == "apm_instance_map"]


# ── 페이로드 해석 ───────────────────────────────────────────────────────────
def test_context_parse_caps_json_and_invalid() -> None:
    assert biz_service_context(_alarm(None)) is None
    assert biz_service_context({"meta": {"biz_service": {"service_id": "x"}}}) is None
    ctx = biz_service_context({"meta": {"biz_service": json.dumps(_biz(d1_verdict="R3"))}})
    assert ctx is not None and ctx["service_id"] == 7 and ctx["d1_verdict"] == "R3"
    assert BIZ_COMPARE_SMS_MAX == 3 <= POLESTAR_LINK_TARGETS_MAX
    assert [t["server_name"] for t in ctx["compare_targets"]] == ["SVR1", "SVR2", "SVR3"]
    broken = _biz(n_sms=0, n_apm=0)
    broken["sms_members"] = [{"db_id": "", "server_name": "X"}, "junk",
                             {"db_id": "polestar", "server_name": "A"},
                             {"db_id": "polestar", "server_name": "A"}]
    ctx = biz_service_context({"meta": {"biz_service": broken}})
    assert ctx is not None and [t["server_name"] for t in ctx["sms_members"]] == ["A"]


# ── S0 같은 업무 비교 후보 ───────────────────────────────────────────────────
def test_s0_uses_apm_members_without_business_lookup() -> None:
    fx = _load("latency_sql")
    caller = _AnyServer(fx)
    run = _run(_alarm(_biz()), caller)
    assert not any("business" in a for a in _map_calls(caller))  # apm_instance_map(business=) 0회
    s0 = run.section("S0")
    assert s0 is not None
    names = [i["instance"] for i in s0.data["instances"]]
    assert names[0] == "A1" and {"C1", "C2"} <= set(names)
    assert s0.data["business"] == "인터넷뱅킹"
    assert s0.data["business_source"] == "meta.biz_service"


def test_s0_without_key_keeps_business_lookup() -> None:
    fx = _load("latency_sql")
    base_caller = FakeCaller(fx, strict=False)
    base = _run(_alarm(None), base_caller)
    assert any(a.get("business") == "계좌조회" for a in _map_calls(base_caller))
    assert base.biz_service is None
    assert "biz_service" not in base.scope


def test_s0_compare_cap_kept() -> None:
    fx = _load("latency_sql")
    run = _run(_alarm(_biz(n_apm=12)), _AnyServer(fx))
    s0 = run.section("S0")
    assert s0 is not None and len(s0.data["instances"]) == 8  # max_compare 8 그대로
    assert "범위 비교 상한 8" in (s0.reason or "")


# ── S4 폴스타 비교 ──────────────────────────────────────────────────────────
def test_s4_compares_three_members_without_changing_target_verdict() -> None:
    fx = _load("latency_sql")
    base = _run(_alarm(None), _AnyServer(fx))
    caller = _AnyServer(fx, hot="SVR2")
    run = _run(_alarm(_biz()), caller)
    metric_servers = {a["server_name"] for t, a in caller.calls if t == TOOL_METRIC}
    assert metric_servers == {"wasdemo01", "SVR1", "SVR2", "SVR3"}  # 비교 ≤3대
    s4, b4 = run.section("S4"), base.section("S4")
    assert s4 is not None and b4 is not None
    # 알람 대상 판정·표·사유는 비교 구성원과 섞이지 않는다
    assert (s4.verdict, s4.table, s4.reason) == (b4.verdict, b4.table, b4.reason)
    assert {k: v for k, v in s4.data.items() if k != "biz_compare"} == b4.data
    summary = {r["server_name"]: r for r in s4.data["biz_compare"]}
    assert summary["SVR2"]["verdict"] == "degraded" and summary["SVR2"]["anomalous"] == ["cpu"]
    assert "polestar_targets" not in run.scope  # 210 연계 대상 의미와 섞지 않는다
    # 범위 모양은 같다 — 대상(`targets`)만 같은 업무 후보가 바뀐 만큼 다르다(B1 → C1·C2 비교)
    assert {k: v for k, v in run.scope.items() if k != "targets"} == {
        k: v for k, v in base.scope.items() if k != "targets"}


# ── 브리핑 선택 키 ──────────────────────────────────────────────────────────
def _verdict() -> ImportanceVerdict:
    return ImportanceVerdict(level="경고", confidence="none", escalate=False)


def test_briefing_key_and_six_elements() -> None:
    fx = _load("latency_sql")
    run = _run(_alarm(_biz(d1_verdict="R4")), _AnyServer(fx, hot="SVR1"))
    brief = run.biz_service
    assert brief is not None
    assert brief["service_id"] == 7 and brief["display_name"] == "인터넷뱅킹"
    assert brief["d1_verdict"] == "R4" and brief["target_inferred"] is False
    sms = {r["server_name"]: r for r in brief["compared_sms"]}
    assert set(sms) == {"SVR1", "SVR2", "SVR3"}
    assert sms["SVR2"]["inferred"] is True and sms["SVR1"]["inferred"] is False
    apm = {r["instance_name"]: r for r in brief["compared_apm"]}
    assert set(apm) == {"C1", "C2"} and apm["C1"]["inferred"] is True
    block = run.to_briefing_dict()
    assert "biz_service" not in block
    plain = build_playbook_briefing(playbook=block, verdict=_verdict())
    keyed = build_playbook_briefing(playbook=block, verdict=_verdict(), biz_service=brief)
    assert "biz_service" not in plain
    assert keyed["biz_service"] == brief and list(keyed)[-1] == "biz_service"
    assert {k: v for k, v in keyed.items() if k != "biz_service"} == plain  # 6요소 불변
    assert all(k in keyed for k in SIX)
