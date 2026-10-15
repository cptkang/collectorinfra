"""`decide_notification` 플래그 × 심각도 × 상태 골든 매트릭스 (plans/166 W0 · 스멜 G1).

plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지(갱신이
필요하면 리팩터가 아니다). 기대값은 「맞는 값」이 아니라 「지금 값」이다(D-337 ⑤).

W4에서 `decide_notification`을 「순서 있는 규칙 표」로 바꿀 때 평가 순서(D-048 심각도3 절대
PAGE·보수적 PAGE가 앞)가 보존됐는지 잡는다. 기존 `test_decision_snapshot.py`(단계 14종 ×
경계 케이스 대표 입력)는 그대로 두고, 이 파일은 그 위에 **조합 매트릭스**를 더한다.

매트릭스 축:
    - 신호 프로필 2종 — ``quiet``(플래그가 소비할 신호 없음) · ``loud``(플래그 뒤 신호 전부
      주입: 비운영 문구·AI 심각도 3·LLM noise·부모 비정상·인히비션·플래핑·스톰·상관·계획 주석).
    - 플래그 조합 — 함수가 읽는 bool 플래그 10개의 전부 off · 전부 on · 각 단독 on(12조합).
      ``loud``에는 평가 순서를 드러내는 **누적 해제(cascade)** 8조합을 더한다 — 평가 위치 순으로
      앞쪽 플래그를 하나씩 끈다. 켜진 플래그 중 가장 앞 단계가 결론을 내므로 순서가 바뀌면 깨진다.
    - 심각도 0~3.
    - 상태 4종 — 정상 발생(is_clear False) · 해소(is_clear True) · 유지보수 창 · 침묵 규칙 일치.
      심각도와 is_clear는 독립 축으로 고정한다(sev0 정상·sev3 해소 같은 경계도 지금 값으로 남는다).

직렬화는 기존 스냅샷과 같은 6-튜플 ``(tier, reason, priority, signals, fingerprint, stage)``이다.
시각 의존(침묵 만료)은 기존 스냅샷의 고정 시각 ``NOW``를 쓴다.

스냅샷 (재)생성(단계 2 이전에만):
    UPDATE_PLAN162_SNAPSHOT=1 .venv/bin/python -m pytest \
        noise_gate/tests/test_plan166_w0_decision_matrix.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from noise_gate.domain.notification_policy import decide_notification
from noise_gate.tests.test_decision_snapshot import NOW, _analysis, _cfg, _rule
from noise_gate.tests.test_notification_policy import make_ctx, make_event

SNAPSHOT_PATH = Path(__file__).parent / "fixtures" / "plan166_w0_decision_matrix.json"

# 함수가 읽는 bool 플래그 10개 — **평가 위치 순서**로 둔다(누적 해제 조합의 기준).
#   non_alarm(0.5) → ai_boost(1, 심각도3 단락 3) → resolved(4) → dependency(6.4)
#   → inhibition(6.5) → flapping(6) → storm(7) → correlation(7.5) → annotation(7.7)
#   → llm_actionability(9)
FLAGS: tuple[str, ...] = (
    "non_alarm_filter_enabled",
    "enable_ai_severity_boost",
    "resolved_to_dashboard",
    "dependency_suppression",
    "inhibition_enabled",
    "flapping_enabled",
    "storm_grouping_enabled",
    "cross_host_correlation_enabled",
    "annotation_planned_suppress",
    "enable_llm_actionability",
)

SEVERITIES: tuple[int, ...] = (0, 1, 2, 3)
STATES: tuple[str, ...] = ("normal", "resolved", "maintenance", "silence")
PROFILES: tuple[str, ...] = ("quiet", "loud")


def _combos(profile: str) -> dict[str, frozenset[str]]:
    """조합 ID → 켜는 플래그 집합. ID는 스냅샷 키라 바꾸지 않는다."""
    combos: dict[str, frozenset[str]] = {
        "all_off": frozenset(),
        "all_on": frozenset(FLAGS),
    }
    for flag in FLAGS:
        combos[f"only:{flag}"] = frozenset({flag})
    if profile == "loud":
        # 앞에서 k개를 끈다(k=1..8) — k=0은 all_on, k=9는 only:enable_llm_actionability와 같다.
        for k in range(1, len(FLAGS) - 1):
            combos[f"from:{FLAGS[k]}"] = frozenset(FLAGS[k:])
    return combos


def _config(on: frozenset[str]) -> SimpleNamespace:
    """bool 플래그 10개를 모두 명시한 설정(기본값 의존·누수 차단)."""
    return _cfg(suppress_max_severity=2, **{flag: flag in on for flag in FLAGS})


def _args(profile: str, severity: int, state: str) -> dict[str, Any]:
    """프로필·심각도·상태로 decide_notification 인자를 만든다(플래그 무관)."""
    loud = profile == "loud"
    event_kw: dict[str, Any] = {"severity": severity, "is_clear": state == "resolved"}
    if loud:
        event_kw["alarm_name"] = "계정 생성 승인 요청"  # 비운영 마커만 있고 알람 마커 없음
    ctx = make_ctx(maintenance=state == "maintenance")
    kw: dict[str, Any] = {}
    analysis = None
    if loud:
        ctx["parent_avail_status"] = 2
        analysis = _analysis(ai_message_severity=3, llm_actionability="noise")
        kw.update(
            inhibited=True,
            flapping=True,
            storm=True,
            correlated=True,
            annotation={"planned_work": True, "resolution": True},
        )
    if state == "silence":
        kw["silence_rules"] = [_rule()]
    return {"event": make_event(**event_kw), "analysis": analysis, "ctx": ctx, "kw": kw}


def compute_matrix() -> dict[str, dict]:
    """전 케이스의 판정 6-튜플을 JSON 직렬화 가능한 dict로 계산한다."""
    out: dict[str, dict] = {}
    for profile in PROFILES:
        for combo_id, on in _combos(profile).items():
            config = _config(on)
            for severity in SEVERITIES:
                for state in STATES:
                    a = _args(profile, severity, state)
                    d = decide_notification(
                        a["event"], None, a["analysis"], a["ctx"], config, now=NOW, **a["kw"]
                    )
                    out[f"{profile}/{combo_id}/sev{severity}/{state}"] = {
                        "tier": d.tier,
                        "reason": d.reason,
                        "priority": d.priority,
                        "signals": d.signals,
                        "fingerprint": d.fingerprint,
                        "stage": d.stage,
                    }
    return out


def _current() -> dict[str, dict]:
    return json.loads(json.dumps(compute_matrix(), ensure_ascii=False))


def test_decision_matrix_matches_snapshot() -> None:
    # 매트릭스 전 케이스의 판정 6-튜플이 채록 시점과 한 글자도 다르지 않아야 한다.
    current = _current()
    if os.environ.get("UPDATE_PLAN162_SNAPSHOT") == "1":
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        pytest.skip(f"스냅샷을 갱신했다: {SNAPSHOT_PATH}")

    assert SNAPSHOT_PATH.exists(), (
        f"스냅샷이 없다: {SNAPSHOT_PATH} — UPDATE_PLAN162_SNAPSHOT=1로 최초 채록하라"
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert set(current) == set(expected), (
        f"케이스 집합이 달라졌다 (신규: {sorted(set(current) - set(expected))}, "
        f"소실: {sorted(set(expected) - set(current))})"
    )
    changed = sorted(k for k in expected if current[k] != expected[k])
    assert not changed, f"판정이 바뀌었다({len(changed)}건): {changed[:20]}"


def test_every_flag_is_consumed_in_loud_profile() -> None:
    # 매트릭스가 공허해지지 않게 고정한다 — 각 플래그 단독 on은 loud에서 전부 off와 최소 1케이스가
    # 달라야 한다(플래그가 판정에 실제로 닿는다는 뜻).
    current = _current()
    for flag in FLAGS:
        diff = [
            f"sev{s}/{st}"
            for s in SEVERITIES
            for st in STATES
            if current[f"loud/only:{flag}/sev{s}/{st}"]
            != current[f"loud/all_off/sev{s}/{st}"]
        ]
        assert diff, flag
