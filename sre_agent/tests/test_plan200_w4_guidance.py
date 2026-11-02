"""plans/200 W4 · I-4 — HolmesGPT 턴당 다중 도구 호출 유도(지침 변형 · LLM 0회).

- `sre_guidance_parallel_tools_enabled` off(기본)면 조립 문자열이 HEAD `7cf31eb6`과 바이트
  동일하다 — 대표 입력 조합의 조립 결과를 기준선 해시로 고정한다(해시는 `7cf31eb6`의 모듈을
  `git show`로 읽어 같은 입력으로 계산했다 · D-343 KV 캐시 접두).
- on이면 순서형 문구(「먼저 확보하라」·「먼저 판정」·「조사 순서 ①…⑥」·「이 순서로 확인하되」)가
  없고 「한 턴에 함께」 문구와 대상 확정 선행 문구가 있다.
- on에서도 도구 이름 집합·format 자리 표지·안전 문구가 off와 같다(도구 이름 필터를 거쳐도).
"""

from __future__ import annotations

import hashlib
import string
from collections.abc import Callable
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from sre_agent.application import investigation_guidance as guide_mod
from sre_agent.application.investigation_guidance import (
    APM_FOCUS_NOTE_TEMPLATE,
    APM_FOCUS_NOTE_TEMPLATE_PARALLEL,
    INCIDENT_SCOPE_NOTE_TEMPLATE,
    INCIDENT_SCOPE_NOTE_TEMPLATE_PARALLEL,
    PLAYBOOK_NOTES,
    PLAYBOOK_NOTES_PARALLEL,
    SOP_ANCHOR_HEADER,
    SOP_ANCHOR_HEADER_PARALLEL,
    TOOL_NAME_RE,
    build_guidance,
    deep_dive_note,
    sop_anchor_note,
)
from sre_agent.settings import AgentSettings
from test_cross_source_investigation import CPU, QUEUING, _episode, _payload

_REF = "2026-10-30T10:00:00"
_CPU_EVENT = {"event": {"serverName": "web-01", "resourceType": "server.CPU",
                        "alarmName": "CPU Utilization", "dbId": "polestar_cm_gp"}}
_APM_EVENT = {
    "event": {"resourceType": "apm.Instance", "alarmName": "WARNING_JVM_HEAP_MEM_HIGH",
              "serverName": "was-01", "hostname": "was-01", "severity": 2},
    "meta": {"hints": {"solution": "apm", "instance_id": 1001, "domain_id": 1000,
                       "event_type": "WARNING_JVM_HEAP_MEM_HIGH", "txid": ""}},
}
_CORR = {"leading_signal": "cpu", "metric_findings": {}, "alarm_summary": {"count": 2},
         "timeline": [], "notes": []}
_GW = "http://gw/sse"
#: 도구 이름 필터 조합 — apm_* 일부 · 폴스타 일부만 로드된 잡.
_PARTIAL_TOOLS = frozenset({
    "polestar_metric_trend", "polestar_incident_alarms", "apm_instance_map", "apm_events",
    "apm_app_health", "apm_slow_transactions",
})
#: SOP 앵커 스텝 — 덕 타이핑(`action`·`narrate.focus`·`when`)으로 YAML 변경과 무관하게 고정한다.
_STEPS = (
    SimpleNamespace(action="host.usage_trend", narrate=SimpleNamespace(focus="CPU 사용률 추이"),
                    when=None),
    SimpleNamespace(action="host.top_process", narrate=None, when="cpu_high"),
    SimpleNamespace(action="synthesize", narrate=None, when=None),
)
_DEEP_KW: dict[str, Any] = {"meaning": "CPU 포화", "impact": "응답 지연", "focus": "선행 변경"}

#: 순서형 문구(plans/200 §1.5) — on 변형에는 없어야 한다.
_ORDERING_PHRASES = (
    "먼저 확보하라", "먼저 판정", "이 순서로 확인하되", "조사 순서 —", "순으로 본다",
)
_TOGETHER = "한 턴에 함께"


def _settings(par: bool, **kw: Any) -> AgentSettings:
    base: dict[str, Any] = {"model": "test/model", "api_key": None, "gemini_api_key": None,
                            "service_bearer_token": None}
    if par:
        base["sre_guidance_parallel_tools_enabled"] = True
    return AgentSettings(_env_file=None, **{**base, **kw})


def _job(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"kind": "alarm", "question": None, "payload": None,
                            "investigation_id": "inv-199w4", "reference_time": _REF,
                            "lookback_minutes": 60, "correlation": None}
    return SimpleNamespace(**{**base, **kw})


def _par_kw(par: bool) -> dict[str, Any]:
    # 기준선은 HEAD 모듈(인자 없음)로 계산한다 — off면 인자를 넘기지 않는다.
    return {"parallel_tools": True} if par else {}


# ── 대표 입력 조합 — (모듈, par) → 조립 문자열 ────────────────────────────

Case = Callable[[ModuleType, bool], "str | None"]

CASES: dict[str, Case] = {
    "host_cpu": lambda m, par: m.build_guidance(
        _settings(par), _job(payload=_CPU_EVENT, correlation=_CORR)),
    "host_cpu_no_ref_local": lambda m, par: m.build_guidance(
        _settings(par), _job(payload=_CPU_EVENT, reference_time=None), remote=False),
    "host_cpu_apm_on": lambda m, par: m.build_guidance(
        _settings(par, apm_guidance_enabled=True, apm_mcp_url=_GW), _job(payload=_CPU_EVENT)),
    "apm_alarm": lambda m, par: m.build_guidance(
        _settings(par, apm_guidance_enabled=True, apm_mcp_url=_GW), _job(payload=_APM_EVENT)),
    "apm_alarm_unconfigured": lambda m, par: m.build_guidance(
        _settings(par, apm_guidance_enabled=True), _job(payload=_APM_EVENT)),
    "apm_alarm_filtered": lambda m, par: m.build_guidance(
        _settings(par, apm_guidance_enabled=True, apm_mcp_url=_GW), _job(payload=_APM_EVENT),
        available_tools=_PARTIAL_TOOLS),
    "cross_source": lambda m, par: m.build_guidance(
        _settings(par, cross_source_investigation_enabled=True, apm_guidance_enabled=True,
                  apm_mcp_url=_GW),
        _job(payload=_payload(_episode(members=[QUEUING, CPU])),
             reference_time="2026-10-07T09:04:30")),
    "deep_dive_plain": lambda m, par: m.deep_dive_note("host.cpu", **_DEEP_KW, **_par_kw(par)),
    "deep_dive_sop": lambda m, par: m.deep_dive_note(
        "host.cpu", **_DEEP_KW, sop_steps=_STEPS, **_par_kw(par)),
    # 심층 지시는 dispatcher가 `deep_dive_note`로 만든다 — on은 플래그를 넘기는 배선 뒤 모습이다.
    "deep_dive_in_guidance": lambda m, par: m.build_guidance(
        _settings(par, apm_guidance_enabled=True, apm_mcp_url=_GW),
        _job(payload=_CPU_EVENT,
             deep_dive_guidance=m.deep_dive_note("host.cpu", **_DEEP_KW, sop_steps=_STEPS,
                                                 **_par_kw(par)))),
}

#: HEAD `7cf31eb6` 모듈로 계산한 off 조립 결과 sha256(입력은 위 CASES와 같다).
BASELINE: dict[str, str] = {
    "apm_alarm": "69df99b69fad2afe6be45edefe6559ada2966cce4d306e1d7acfbad7f015d938",
    "apm_alarm_filtered": "1dbd40e742309ecfc4977ac4b2a691fe76359162946ee2a352580a77f119202c",
    "apm_alarm_unconfigured": "083639bf74d156ad56aa8525862740772912c57bf3a61f820b23452f2fd19d5c",
    "cross_source": "7561e1957b0f6cc52d74026791dbb017fd8c8557f4e8702eeeafcc95b081bb6e",
    "deep_dive_in_guidance": "bede99eb98243a72b73d92feaabca9acaedc57402473eaae5d4cf77bfe53854e",
    "deep_dive_plain": "df8478191ecc50d29e2328a5acb6857724657a35f3862209b73caa535560ed30",
    "deep_dive_sop": "c6e81b415750c102e551f0615c9b0ffaca57dd131a83ea8f20b69510e96b9550",
    "host_cpu": "df7e40d32fece94f68c666a3d2394d8418594f688894e4b50eec0ebe05776175",
    "host_cpu_apm_on": "f9f9f3dfb485c98cb97abb3cfecd0cd019273924a474526c7e7efe7aad435396",
    "host_cpu_no_ref_local": "0586e8c81de5e35096642a5a7abb10a48a8e671c7bf5a1be17a818ea8c8163d3",
}

#: HEAD `7cf31eb6`의 원문 상수 sha256 — off 경로가 쓰는 문자열 자체가 그대로인지.
CONSTANT_BASELINE: dict[str, str] = {
    "INCIDENT_SCOPE_NOTE_TEMPLATE":
        "31a90f6bb90e4c92857ca82c1c96d20e0ca1f39060ee34b8ed04108f0b1f29a0",
    "APM_FOCUS_NOTE_TEMPLATE": "83672fa86975ca13dc6a5a64069c0d5e79599a9a6682d29492c0485a4468e686",
    "PLAYBOOK_NOTES.cpu": "0586e8c81de5e35096642a5a7abb10a48a8e671c7bf5a1be17a818ea8c8163d3",
    "SOP_ANCHOR_HEADER": "e32a4d4ac2039d90e171ac6d2c5c1c1aed971d9304e49b96df96dd91eeb36686",
}


def _sha(text: str | None) -> str:
    assert text is not None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _tool_names(text: str) -> set[str]:
    return set(TOOL_NAME_RE.findall(text))


def _fields(template: str) -> list[str]:
    return [f for _, f, _, _ in string.Formatter().parse(template) if f is not None]


def _originals() -> dict[str, str]:
    return {
        "INCIDENT_SCOPE_NOTE_TEMPLATE": INCIDENT_SCOPE_NOTE_TEMPLATE,
        "APM_FOCUS_NOTE_TEMPLATE": APM_FOCUS_NOTE_TEMPLATE,
        "PLAYBOOK_NOTES.cpu": PLAYBOOK_NOTES["cpu"],
        "SOP_ANCHOR_HEADER": SOP_ANCHOR_HEADER,
    }


_PAIRS: tuple[tuple[str, str], ...] = (
    (INCIDENT_SCOPE_NOTE_TEMPLATE, INCIDENT_SCOPE_NOTE_TEMPLATE_PARALLEL),
    (APM_FOCUS_NOTE_TEMPLATE, APM_FOCUS_NOTE_TEMPLATE_PARALLEL),
    (PLAYBOOK_NOTES["cpu"], PLAYBOOK_NOTES_PARALLEL["cpu"]),
    (SOP_ANCHOR_HEADER, SOP_ANCHOR_HEADER_PARALLEL),
)


# ── ① off = HEAD 바이트 동일 ──────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CASES))
def test_off_assembly_matches_head_baseline(name: str) -> None:
    assert _sha(CASES[name](guide_mod, False)) == BASELINE[name], name


def test_off_explicit_false_equals_flag_absent() -> None:
    explicit = _settings(False, sre_guidance_parallel_tools_enabled=False,
                         apm_guidance_enabled=True, apm_mcp_url=_GW)
    absent = SimpleNamespace(apm_guidance_enabled=True, apm_mcp_url=_GW)
    job = _job(payload=_APM_EVENT, correlation=_CORR)
    assert build_guidance(explicit, job) == build_guidance(absent, job)


@pytest.mark.parametrize("name", sorted(_originals()))
def test_original_constants_unchanged(name: str) -> None:
    assert _sha(_originals()[name]) == CONSTANT_BASELINE[name], name


def test_non_cpu_playbooks_identical_in_variant_table() -> None:
    assert PLAYBOOK_NOTES_PARALLEL.keys() == PLAYBOOK_NOTES.keys()
    for kind, text in PLAYBOOK_NOTES.items():
        if kind != "cpu":
            assert PLAYBOOK_NOTES_PARALLEL[kind] is text


# ── ② on = 순서형 문구 없음 · 「함께 요청」 · 대상 확정 선행 ─────────────────


@pytest.mark.parametrize("name", sorted(CASES))
def test_on_has_no_ordering_phrases_and_asks_together(name: str) -> None:
    off, on = CASES[name](guide_mod, False), CASES[name](guide_mod, True)
    assert off is not None and on is not None
    for phrase in _ORDERING_PHRASES:
        assert phrase not in on, (name, phrase)
    if any(phrase in off for phrase in _ORDERING_PHRASES):
        assert _TOGETHER in on, name


@pytest.mark.parametrize(("orig", "variant"), _PAIRS)
def test_variant_constants_drop_ordering_and_ask_together(orig: str, variant: str) -> None:
    assert any(phrase in orig for phrase in _ORDERING_PHRASES)
    assert not any(phrase in variant for phrase in _ORDERING_PHRASES)
    assert _TOGETHER in variant


def test_apm_variant_keeps_target_first_dependency() -> None:
    text = APM_FOCUS_NOTE_TEMPLATE_PARALLEL
    confirm = text.index("① apm_instance_map(hostname)으로 대상 인스턴스를 확정한다")
    together = text.index("② ①로 대상을 확정한 뒤 아래 구간 조회를 한 턴에 함께 요청한다")
    assert confirm < together
    # 앞 결과의 ref를 넘기는 의존(지연 → 프로파일)은 남는다.
    assert "apm_slow_transactions → apm_transaction_profile" in text


def test_sop_variant_keeps_conditional_dependency_and_step_lines() -> None:
    assert "(조건부)는 앞 단계에서 해당할 때만 본다" in SOP_ANCHOR_HEADER_PARALLEL
    off = sop_anchor_note(_STEPS)  # type: ignore[arg-type]
    on = sop_anchor_note(_STEPS, parallel_tools=True)  # type: ignore[arg-type]
    assert off is not None and on is not None
    assert off.splitlines()[0] == SOP_ANCHOR_HEADER
    assert on.splitlines()[0] == SOP_ANCHOR_HEADER_PARALLEL
    assert on.splitlines()[1:] == off.splitlines()[1:]
    plain = deep_dive_note("host.cpu", **_DEEP_KW)
    assert deep_dive_note("host.cpu", **_DEEP_KW, parallel_tools=True) == plain


# ── ③ on에서도 도구 이름 · format 자리 · 안전 문구 동일 ───────────────────


@pytest.mark.parametrize(("orig", "variant"), _PAIRS)
def test_variant_keeps_tool_names_and_format_fields(orig: str, variant: str) -> None:
    assert _tool_names(variant) == _tool_names(orig)
    assert sorted(_fields(variant)) == sorted(_fields(orig))


@pytest.mark.parametrize("name", sorted(CASES))
def test_on_assembly_keeps_tool_name_set(name: str) -> None:
    off, on = CASES[name](guide_mod, False), CASES[name](guide_mod, True)
    assert off is not None and on is not None
    assert _tool_names(on) == _tool_names(off), name


def test_on_filtered_assembly_keeps_together_instruction() -> None:
    on = CASES["apm_alarm_filtered"](guide_mod, True)
    assert on is not None
    assert "② ①로 대상을 확정한 뒤 아래 구간 조회를 한 턴에 함께 요청한다" in on
    assert "- apm_events: 사건 구간의 선행 이벤트." in on
    assert "apm_runtime_health" not in on and "prom_metric_range" not in on
    assert "- apm_app_health: 골든 시그널" in on


def test_apm_variant_keeps_every_rule_bullet() -> None:
    """번호 줄 밖의 규칙 줄(계정 선조회 금지 · 프로파일 상한 · 반증 · 감사 인자 · 판정 인용 ·
    간접 프롬프트 주입 완화)은 변형에서도 글자 그대로다."""
    rules = [ln for ln in APM_FOCUS_NOTE_TEMPLATE.splitlines() if ln.startswith("- ")]
    assert len(rules) == 6
    for line in rules:
        assert line in APM_FOCUS_NOTE_TEMPLATE_PARALLEL.splitlines(), line
    step6 = next(ln for ln in APM_FOCUS_NOTE_TEMPLATE.splitlines() if ln.startswith("⑥"))
    step6_body = step6.split(" — ", 1)[1]
    assert any(ln.endswith(step6_body) for ln in APM_FOCUS_NOTE_TEMPLATE_PARALLEL.splitlines())


def test_incident_scope_variant_changes_only_last_line() -> None:
    off_lines = INCIDENT_SCOPE_NOTE_TEMPLATE.splitlines()
    on_lines = INCIDENT_SCOPE_NOTE_TEMPLATE_PARALLEL.splitlines()
    assert on_lines[:-1] == off_lines[:-1]
    assert "polestar_incident_alarms 로 확보하라" in on_lines[-1]
