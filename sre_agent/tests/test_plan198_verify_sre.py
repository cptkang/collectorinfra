"""plans/198 sre_agent 변경 독립 검증(verifier) — Wave 자기 테스트가 비운 곳만.

`xfail(strict=True)`는 **결함 재현**이다 — 고치면 XPASS로 실패하므로 그때 마커를 지운다.
실 LLM 0 · 네트워크 0.
"""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from holmes.core.tools_utils.tool_executor import ToolExecutor
from sre_agent.application import investigation_dispatcher as disp_mod
from sre_agent.application.briefing_builder import build_partial_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.diagnosis import _with_tool_allowlist
from sre_agent.domain.evidence_ledger import build_ledger, check_claim, ledger_summary
from sre_agent.domain.failure_guards import ExpectedSources, StallTracker
from sre_agent.domain.severity_signatures import ImportanceVerdict
from sre_agent.infrastructure.activity_masking import mask_activity_text

SECRET = "S3cretPW"


def _rec(tool: str, output: str, params: dict | None = None):
    return SimpleNamespace(tool_name=tool, output=output, params=params or {}, description="")


def _cpu_ledger():
    return build_ledger([_rec("polestar_metric_trend",
                              json.dumps({"hostname": "web01", "cpu": 95.3}),
                              {"server_name": "web01"})])


# ── I-4 값 대조 — 오탐(정상 서술 강등) ───────────────────────────────


@pytest.mark.parametrize("term", ["IPv4", "UTF-8", "TLSv1.2", "G1GC", "eth0", "log4j", "Java17",
                                  "sha256", "CVE-2021-44228", "HTTP2"])
def test_tech_terms_are_not_treated_as_hosts(term):
    """원장 값(web01 · 95.3)만 쓴 정상 서술에 일반 기술 용어가 섞여도 강등되면 안 된다."""
    chk = check_claim(f"web01 CPU 95.3% 상태에서 {term} 관련 경고가 함께 보인다", _cpu_ledger())
    assert chk.unmatched == ()
    assert chk.checked == 2  # web01 · 95.3 — 기술 용어는 대조 불가(대조 수 제외)


@pytest.mark.parametrize("host", ["web02", "web-07", "WEB03.corp.local"])
def test_same_shape_host_hallucination_still_caught(host):
    """원장 호스트(web01)와 같은 꼴의 다른 호스트명은 계속 불일치로 잡는다."""
    chk = check_claim(f"{host} CPU 95.3%", _cpu_ledger())
    assert chk.unmatched == (host,)


@pytest.mark.xfail(strict=True,
                   reason="결함: 라벨 없는 산문 단위(1234ms)는 환산 근거가 없어 1.2초가 불일치")
def test_unit_conversion_from_prose_suffix():
    ledger = build_ledger([_rec("apm_service_latency", "응답시간 1234ms")])
    assert check_claim("응답시간 1.2초로 지연", ledger).unmatched == ()


def test_unit_conversion_from_labeled_field_passes():
    """대조군 — 라벨 칸(`elapsed_ms`)이면 환산 허용(계획 verify 「단위 변환 1자리 반올림」)."""
    ledger = build_ledger([_rec("apm_service_latency", json.dumps({"elapsed_ms": 1234}))])
    assert check_claim("응답시간 1.2초로 지연", ledger).unmatched == ()


def test_host_alias_fqdn_both_directions():
    ledger = build_ledger([_rec("polestar_x", "server WEB01.corp.local cpu 90")])
    assert check_claim("web01 CPU 90%", ledger).unmatched == ()
    ledger2 = build_ledger([_rec("polestar_x", "server web01 cpu 90")])
    assert check_claim("web01.corp.local CPU 90%", ledger2).unmatched == ()


# ── 원시 출력·비밀 누출 ─────────────────────────────────────────────


def test_ledger_summary_has_no_raw_output_and_masks_args():
    ledger = build_ledger(
        [_rec("bash", f"token={SECRET} raw-output-line",
              {"command": f"mysql -u root -p{SECRET} -e 'select 1'"})],
        extra=[("alarm.payload", {"password": SECRET})],
    )
    dumped = json.dumps(ledger_summary(ledger, masker=mask_activity_text), ensure_ascii=False)
    assert SECRET not in dumped and "raw-output-line" not in dumped


def test_partial_briefing_has_no_raw_output():
    ledger = build_ledger([_rec("polestar_metric_trend", f"raw-output {SECRET}",
                                {"server_name": "web01"})])
    b = build_partial_briefing(
        reason="incomplete",
        verdict=ImportanceVerdict(level="warning", confidence="none", escalate=False),
        ledger=ledger, ledger_masker=mask_activity_text,
    )
    assert SECRET not in json.dumps(b, ensure_ascii=False)
    assert b["summary"].startswith("[미결] ")


def test_stall_reason_masks_secret_args():
    """정체 사유의 인자 표기는 주입한 가림 콜백(diagnosis가 `mask_activity_text`)을 거친다."""
    st = StallTracker(repeat_threshold=3, idle_steps=4, masker=mask_activity_text)
    for _ in range(3):
        st.observe("bash", {"command": f"mysql -u root -p{SECRET} -e 'select 1'"}, "", "success")
    reason = st.end_step()
    assert reason is not None and "같은 도구·같은 인자 호출 3회" in reason
    assert SECRET.lower() not in reason.lower()


def test_stall_tracker_is_masked_in_diagnosis_wiring():
    """배선 — diagnosis가 만드는 정체 판정기에 가림 콜백이 실린다."""
    import inspect

    from sre_agent import diagnosis

    assert "masker=mask_activity_text" in inspect.getsource(diagnosis)


def test_stall_args_keep_case():
    """대소문자만 다른 인자(PromQL 라벨 값·호스트)는 다른 호출로 센다."""
    st = StallTracker(repeat_threshold=3, idle_steps=99)
    for host in ("WEB01", "web01", "Web01"):
        st.observe("prom_query", {"host": host}, "", "success")
    assert st.end_step() is None


# ── I-10 기대 소스 지연 적재 — 동시 첫 잡 ───────────────────────────


def test_unchecked_sources_concurrent_first_load(monkeypatch):
    spec = ExpectedSources(sources={"polestar": ("polestar_",), "prometheus": ("prom_",)},
                           kinds={"host": ("polestar", "prometheus")})
    entered, release = threading.Event(), threading.Event()

    def slow_load():
        if not entered.is_set():  # 첫 적재만 붙잡는다 — 동시 잡의 적재는 바로 끝난다
            entered.set()
            release.wait(5)
        return spec

    monkeypatch.setattr(disp_mod, "load_expected_sources", slow_load)
    fake = SimpleNamespace(
        _settings=SimpleNamespace(unchecked_source_limitation_enabled=True),
        _expected=None, _expected_loaded=False, _loaded_tools_fn=None,
    )
    job = SimpleNamespace(kind="alarm", payload={})
    out: dict[str, list[str]] = {}
    t = threading.Thread(target=lambda: out.setdefault(
        "a", InvestigationDispatcher._unchecked_sources(fake, job, [])))
    t.start()
    assert entered.wait(5)
    out["b"] = InvestigationDispatcher._unchecked_sources(fake, job, [])  # 두 번째 잡(동시)
    release.set()
    t.join(5)
    assert out["a"] == ["polestar", "prometheus"]
    assert out["b"] == ["polestar", "prometheus"]


# ── I-9 툴셋 좁히기 — 잡 사이 격리 ─────────────────────────────────


def test_allowlist_clones_are_isolated_between_jobs():
    executor = ToolExecutor([])
    names = {"polestar_metric_trend", "prom_query", "apm_app_health", "TodoWrite"}
    for n in names:
        executor.tools_by_name[n] = SimpleNamespace(name=n)
    llm = SimpleNamespace(tool_executor=executor)
    host = _with_tool_allowlist(llm, {"polestar_metric_trend", "prom_query", "TodoWrite"})
    was = _with_tool_allowlist(llm, {"polestar_metric_trend", "apm_app_health", "TodoWrite"})
    assert "apm_app_health" not in host.tool_executor.tools_by_name
    assert "prom_query" not in was.tool_executor.tools_by_name
    assert "apm_app_health" in was.tool_executor.tools_by_name
    assert set(executor.tools_by_name) == names  # 공유 원본 무변
    assert host.tool_executor is not was.tool_executor is not executor


def test_allowlist_drops_emptied_toolsets_from_clone_only():
    """도구가 모두 빠진 툴셋은 사본의 toolsets·enabled_toolsets에서 빠진다(지침 렌더 제외)."""
    ts_pole, ts_apm, ts_off = (SimpleNamespace(name=n) for n in ("polestar", "jennifer", "off"))
    executor = ToolExecutor([])
    for n, ts in (("polestar_metric_trend", ts_pole), ("apm_app_health", ts_apm),
                  ("apm_tx", ts_apm)):
        executor.tools_by_name[n] = SimpleNamespace(name=n)
        executor._tool_to_toolset[n] = ts
    executor.toolsets = [ts_pole, ts_apm, ts_off]
    executor.enabled_toolsets = [ts_pole, ts_apm]
    llm = SimpleNamespace(tool_executor=executor)
    host = _with_tool_allowlist(llm, {"polestar_metric_trend"})
    assert host.tool_executor.toolsets == [ts_pole, ts_off]   # 원래 도구 없던 툴셋은 그대로
    assert host.tool_executor.enabled_toolsets == [ts_pole]
    assert executor.toolsets == [ts_pole, ts_apm, ts_off]     # 공유 원본 무변
    assert executor.enabled_toolsets == [ts_pole, ts_apm]
    was = _with_tool_allowlist(llm, {"polestar_metric_trend", "apm_tx"})
    assert was.tool_executor.toolsets == [ts_pole, ts_apm, ts_off]  # 일부라도 남으면 유지
