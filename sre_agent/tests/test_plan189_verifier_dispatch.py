"""plans/189 W5 독립 검증 — `prom_empty_reason` 오탐·누락 경계.

- holmes MCP 출력 꼴(본문 JSON + structuredContent `{"result": "<이스케이프된 JSON>"}` 꼬리)에서도 잡는다.
- Prometheus 데이터의 `reason` 라벨(예: kube_*_waiting_reason)은 닫힌 어휘 밖이라 오탐하지 않는다.
- 정상 응답의 hint·warning·fallback_reason 문자열에는 반응하지 않는다.
- 교차 검증 응답의 중첩 `prometheus.reason`은 exporter 데이터가 있어도 호출 전체를 인용에서 뺀다(현행 고정).
"""

from __future__ import annotations

import json

from sre_agent.application.investigation_dispatcher import prom_empty_reason
from sre_agent.diagnosis import ToolCallRecord


def _rec(output: str, name: str = "prom_metric_instant") -> ToolCallRecord:
    return ToolCallRecord(name, "d", "success", output)


def test_holmes_merged_output_with_structured_tail_is_detected():
    body = json.dumps({"data": {"resultType": "vector", "result": []},
                       "reason": "prometheus_not_monitored", "hint": "x"}, ensure_ascii=False)
    merged = f"{body}\n{json.dumps({'result': body})}"
    assert prom_empty_reason(_rec(merged)) == "prometheus_not_monitored"
    # 이스케이프된 꼬리만 있으면 잡지 못한다(본문이 없을 때 — holmes는 본문을 앞에 둔다)
    assert prom_empty_reason(_rec(json.dumps({"result": body}))) is None


def test_reason_label_in_prometheus_data_is_not_false_positive():
    body = {"data": {"resultType": "vector", "result": [
        {"metric": {"__name__": "kube_pod_container_status_waiting_reason",
                    "reason": "CrashLoopBackOff"}, "value": [1, "1"]}]},
        "matched_by": "node_ip", "tried": ['up{node_ip="10.0.0.1"}']}
    assert prom_empty_reason(_rec(json.dumps(body))) is None


def test_success_with_warning_and_fallback_reason_not_detected():
    body = {"data": {"resultType": "vector", "result": [{"value": [1, "2"]}]},
            "matched_by": "hostname_unverified", "warning": "폴스타 조회 실패 — 서버 신원 미확인",
            "fallback_reason": "host_not_covered"}
    assert prom_empty_reason(_rec(json.dumps(body, ensure_ascii=False))) is None


def test_cross_check_nested_reason_excludes_whole_call():
    body = {"cross_check": {"verdict": "not_scraped"},
            "prometheus": {"data": {"result": []}, "reason": "prometheus_unsupported_zone"},
            "openmetrics": {"data": {"result": [{"value": [1, "0.5"]}]}}}
    assert prom_empty_reason(_rec(json.dumps(body))) == "prometheus_unsupported_zone"
