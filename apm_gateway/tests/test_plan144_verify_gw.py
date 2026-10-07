"""plans/144 W1·W2 독립 검증 (verifier) — 게이트웨이 쪽 계약 고정.

  - `resolver.reverse`가 `raw.apm.match_reason`에 싣는 어휘 =
    {override, host_name, regex, unresolved}. noise_gate `AlarmEvent.host_key_strength`의 strong
    집합 {override, host_name}이 이 어휘의 부분집합이어야 한다(noise_gate는 import하지 않는다 —
    D-274 ③ · 값은 복제해 단언).
  - `event_type_norm`은 벤더 접두 규칙을 어댑터에서만 적용한다 — domain 폴백은 원문 그대로다.
  - 페이로드 추가 칸은 `event_type_norm` 1개뿐이다(민감 정보 유입 경로 없음).
"""

from __future__ import annotations

import ast
from pathlib import Path

from apm_gateway.adapters.jennifer.fields import parse_event
from apm_gateway.domain.events import build_alarm_payload

PKG = Path(__file__).resolve().parents[1] / "apm_gateway"

# noise_gate/domain/alarm.py `_STRONG_MATCH_REASONS` 복제(import 금지 경계).
NOISE_GATE_STRONG = {"override", "host_name"}


def _reverse_reasons() -> set[str]:
    tree = ast.parse((PKG / "application" / "resolver.py").read_text(encoding="utf-8"))
    reasons: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "reverse":
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple):
                    third = ret.value.elts[2]
                    assert isinstance(third, ast.Constant), "reverse 사유는 리터럴이어야 한다"
                    reasons.add(third.value)
    return reasons


def test_reverse_reason_vocabulary_pinned():
    assert _reverse_reasons() == {"override", "host_name", "regex", "unresolved"}


def test_noise_gate_strong_set_is_subset_of_reverse_vocabulary():
    assert NOISE_GATE_STRONG <= _reverse_reasons()
    assert "regex" not in NOISE_GATE_STRONG and "unresolved" not in NOISE_GATE_STRONG


def _payload(event: dict) -> dict:
    return build_alarm_payload(
        event, source="jennifer", source_label="JENNIFER", source_id="default", hostname="h",
        ip_address="", match_confidence="high", match_reason="host_name", severity=2,
        was_signals=[], tz="Asia/Seoul",
    )


def _raw(**over) -> dict:
    base = {
        "time": 1_700_000_000_000, "eventLevel": "WARNING", "errorType": "",
        "metricsName": "WARNING_JVM_HEAP_MEM_HIGH", "instanceId": 1, "instanceName": "w",
        "domainId": 7, "domainName": "d", "message": "m",
    }
    base.update(over)
    return base


def test_only_one_added_apm_key():
    ev = parse_event(_raw())
    with_norm = set(_payload(ev)["apm"])
    without = set(_payload({k: v for k, v in ev.items() if k != "event_type_norm"})["apm"])
    assert with_norm == without  # 폴백으로도 칸은 있다
    assert "event_type_norm" in with_norm


def test_metric_event_norm_and_kind():
    p = _payload(parse_event(_raw()))
    assert p["apm"]["event_kind"] == "metric"
    assert p["apm"]["event_type_norm"] == "JVM_HEAP_MEM_HIGH"
    assert p["alarmName"] == "WARNING_JVM_HEAP_MEM_HIGH"


def test_domain_fallback_does_not_strip_vendor_prefix():
    # 어댑터 칸이 없을 때 domain은 원문을 그대로 싣는다(벤더 접두 규칙은 어댑터에만).
    p = _payload({"event_type": "ERROR_X", "instance_name": "w"})
    assert p["apm"]["event_type_norm"] == "ERROR_X"
