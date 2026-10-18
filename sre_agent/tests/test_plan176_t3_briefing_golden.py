"""plans/176 T3 — `build_briefing` 판정/렌더 분리 전 출력 고정(특성화 골든 · D-337 ⑤).

기대값은 「맞는 값」이 아니라 리팩터 전 원 코드의 「지금 값」이다. 입력 분기 조합
(서술 인용 유무 · 도구 유무 · APM 도구 · 상관 유무/빈 dict/변경 선행/사건 편입/빈 타임라인 ·
소스 라벨 · 미결 · 크로스소스 성립/불성립 · APM 도구 연결)을 곱으로 돌려, 출력 `repr`
(키 순서·문구·줄바꿈·목록 순서·타입까지)의 sha256을 (상관, 크로스소스) 묶음별로 고정한다.
"""

from __future__ import annotations

import dataclasses
import hashlib
import itertools

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.domain.cross_source_episode import EpisodeContext, active_episode
from sre_agent.domain.severity_signatures import ImportanceVerdict, judge

_OOM_LOG = "kernel: Out of memory: Killed process 12345 (java)"

_ANSWERS = (
    "",
    "web-01 메모리 고갈 확인 ← polestar_metric_trend",
    "첫 줄 서술\n\n원인: 디스크 IO 폭주 ← polestar_metric_trend\n"
    "  apm_service_stats 응답 지연 확인  \n마지막 추정",
    "인용 없는 단정 하나\n두 번째 단정",
    "수집된 근거가 불충분합니다",
)
_TOOLS = (None, ["polestar_metric_trend"], ["polestar_metric_trend", "apm_service_stats"])

_CORR = {
    "reference_time": "2026-09-01T14:00:00",
    "timeline": [
        {"t_offset_min": -15, "kind": "metric", "detail": "disk_io sustained 시작 (peak 97.0)"},
        {"t_offset_min": -12, "kind": "metric", "detail": "cpu spike 시작 (peak 88.0)"},
        {"t_offset_min": -11, "kind": "change", "detail": "배포 v2"},
        {"t_offset_min": -10, "kind": "alarm", "detail": "[severity 3] CPU Utilization Critical"},
        {"t_offset_min": "x", "kind": "other", "detail": "오프셋 해석 불가"},
        "not-a-dict",
    ],
    "metric_findings": {
        "disk_io": {"is_anomalous": True, "kind": "sustained", "onset_offset_min": -15,
                    "peak_value": 97.0, "z_score": 12.5, "lead_lag_min": -5},
        "cpu": {"is_anomalous": True, "kind": "spike", "onset_offset_min": -12,
                "peak_value": 88.0, "z_score": None, "lead_lag_min": 3},
        "net": {"is_anomalous": True, "kind": "spike", "onset_offset_min": None,
                "peak_value": 1.0, "z_score": 2.0, "lead_lag_min": None},
        "memory": {"is_anomalous": False, "lead_lag_min": None},
    },
    "alarm_summary": {"count": 1, "first_offset_min": -10},
    "leading_signal": "disk_io",
    "notes": ["지표 정밀도 60분 단위", "보조 메모"],
}
_CORR_CHANGE = {
    **_CORR,
    "change_finding": {"before_first_alarm": True, "last_change_offset_min": -11, "count": 2,
                       "descriptions": ["배포 v2", "설정 변경"]},
}
_CORR_EPISODE = {
    "timeline": [
        {"t_offset_min": -5, "kind": "alarm", "detail": "[사건 · 인프라] CPU (cpu)"},
        {"t_offset_min": 0, "kind": "apm_event",
         "detail": "[사건 · 애플리케이션(APM)] SERVICE_QUEUING (was queuing)"},
    ],
    "metric_findings": {}, "alarm_summary": {"count": 0}, "leading_signal": None, "notes": [],
}
_CORRS = {
    "none": None,
    "empty": {},
    "corr": _CORR,
    "change": _CORR_CHANGE,
    "episode": _CORR_EPISODE,
    "empty_tl": {"timeline": [], "notes": ["타임라인 비어 있음"]},
}


def _ctx_payload(links: list[dict]) -> dict:
    return {"meta": {"episode": {
        "episode_id": "ep-1", "host_key": "web-01",
        "members": [
            {"alarm_id": "j1", "source": "jennifer", "alarm_name": "SERVICE_QUEUING",
             "was_kinds": ["queuing"], "occurred_at": "2026-09-01T14:00:00"},
            {"alarm_id": "p1", "source": "polestar", "alarm_name": "CPU", "kind": "cpu",
             "occurred_at": "2026-09-01T13:55:00"},
            {"alarm_id": "p2", "source": "polestar", "alarm_name": "MEM", "kind": "memory",
             "occurred_at": "2026-09-01T13:58:00"},
        ],
        "links": links,
    }}}


def _contexts() -> dict[str, EpisodeContext | None]:
    valid = active_episode(_ctx_payload([
        {"rule_id": "R1", "cause_alarm_id": "p1", "effect_alarm_id": "j1",
         "lag_seconds": 300, "missing": []},
        {"rule_id": "R2", "cause_alarm_id": "p2", "effect_alarm_id": "j1",
         "lag_seconds": -30, "missing": []},
        {"rule_id": "R3", "cause_alarm_id": "gone", "effect_alarm_id": "j1",
         "missing": []},
        {"rule_id": "R4", "cause_alarm_id": "p1", "effect_alarm_id": "j1",
         "lag_seconds": 10, "missing": ["host_key_strength"]},
    ]), True, "2026-09-01T14:00:00")
    invalid = active_episode(_ctx_payload([
        {"rule_id": "R5", "cause_alarm_id": "p1", "effect_alarm_id": "j1",
         "lag_seconds": 10, "missing": ["window"]},
    ]), True, "2026-09-01T14:00:00")
    assert valid is not None and invalid is not None
    return {
        "none": None,
        "valid": dataclasses.replace(valid, notes=("멤버 절단 1건",)),
        "invalid": invalid,
    }


_VERDICTS = (
    judge(gate_severity=2, tool_outputs=[_OOM_LOG]),
    ImportanceVerdict(level="경고", confidence="none", escalate=False),
    ImportanceVerdict(level="주의", confidence="none", escalate=False, evidence_insufficient=True),
)
_REMEDIATIONS = (None, ["재기동 검토", "로그 확인"], [])
_LIMITATIONS = (None, ["호출자 한계"])
_GATE_TIERS = (None, "PAGE", "DIGEST", "SILENT")


def _digests() -> dict[str, str]:
    """(상관, 크로스소스) 묶음별 출력 repr 연쇄의 sha256."""
    ctxs = _contexts()
    out: dict[str, str] = {}
    for corr_name, ctx_name in itertools.product(_CORRS, ctxs):
        h = hashlib.sha256()
        cases = itertools.product(_ANSWERS, _TOOLS, (False, True), (False, True), (False, True))
        for i, (answer, tools, labels, unresolved, apm_conn) in enumerate(cases):
            b = build_briefing(
                answer=answer,
                verdict=_VERDICTS[i % 3],
                tool_names=list(tools) if tools is not None else None,
                gate_tier=_GATE_TIERS[i % 4],
                remediation=_REMEDIATIONS[i % 3],
                limitations=_LIMITATIONS[i % 2],
                correlation=_CORRS[corr_name],
                source_labels=labels,
                unresolved=unresolved,
                cross_source=ctxs[ctx_name],
                apm_tools_connected=apm_conn,
            )
            h.update(repr(b).encode("utf-8"))
            h.update(b"\x00")
        out[f"{corr_name}/{ctx_name}"] = h.hexdigest()
    return out


# 리팩터 전 원 코드(1ad7c161)로 생성. `empty`({})는 falsy라 `none`과 같다.
_GOLDEN: dict[str, str] = {
    "none/none": "5a2f249e0b2e083f98dd7910c445cff1b3a15ab6b10feb0582a72379c941db9b",
    "none/valid": "5827f7814da7e4b5a521598512aa15c320387963ed2e1fcc1e517d6fc79a99cb",
    "none/invalid": "624846f96e13b67da3f51d9d0ef0aa225b200ff10b8a7366e9cb7fe9af70ab25",
    "empty/none": "5a2f249e0b2e083f98dd7910c445cff1b3a15ab6b10feb0582a72379c941db9b",
    "empty/valid": "5827f7814da7e4b5a521598512aa15c320387963ed2e1fcc1e517d6fc79a99cb",
    "empty/invalid": "624846f96e13b67da3f51d9d0ef0aa225b200ff10b8a7366e9cb7fe9af70ab25",
    "corr/none": "46cc68240e9acd710d09f6a13c9c92761965a5c2b788ebe900f2b039eda0c0e4",
    "corr/valid": "655831476e54ce69af8ac2dd7cae0c70919d1626aed4ad0202c236c5fcb80526",
    "corr/invalid": "a5ef6670c23cf9d06ab6be061156c0c7540d9d0ea2d780ad1bdf74c5ca72d8f0",
    "change/none": "bd178c3d9576e493682561b7e66fff813934eed3919b0e7c3309e826f6aded68",
    "change/valid": "3b35423de8d9d75e9bfede9fdc2799bc1df7bb5cbd2cc2720c8a45b8a0eaa481",
    "change/invalid": "e5360108cad04a96121dcdf6045ad48285774ebe6279a6e67fd5b618308e0278",
    "episode/none": "2b7a664b9e3c2600a4372e322251a51565bde7e9b627ada2daaf52acc442e199",
    "episode/valid": "815fd924ba1e2599bd85d96c52853dc7efb8244009da17a25c31d1119469abfe",
    "episode/invalid": "06b400834598c2fbde9a0a1aa794d44763b7039e029baae75b6846e974f5d85a",
    "empty_tl/none": "a1027327c2810d20dff09a5836171fd88fdd6099927ac9dc636ede2c3175dfd9",
    "empty_tl/valid": "fda51aaa12a845b02e1fbc34cc72f5d4dec39c0470458a14f1a0d7cbae879274",
    "empty_tl/invalid": "3a820ac5c28afbb7143b7a0b2b3e52a42aea907b6c8b443b1e35821323b119be",
}


def test_build_briefing_output_unchanged_across_branch_matrix():
    assert _digests() == _GOLDEN


def test_inputs_not_mutated():
    """호출자 목록·상관 dict를 변형하지 않는다(사본 조립)."""
    limits, rem = ["호출자 한계"], ["재기동 검토"]
    corr_before = repr(_CORR_CHANGE)
    build_briefing(answer=_ANSWERS[2], verdict=_VERDICTS[0], tool_names=list(_TOOLS[2]),
                   remediation=rem, limitations=limits, correlation=_CORR_CHANGE,
                   source_labels=True, cross_source=_contexts()["valid"])
    assert limits == ["호출자 한계"] and rem == ["재기동 검토"]
    assert repr(_CORR_CHANGE) == corr_before
