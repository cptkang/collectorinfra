"""실행 관측 — 로그 레벨 규약과 실패 요청 단계 트레이스 (D-141).

감사 로그(`src/security/audit_logger.py`)와 목적이 다르다. 감사는 "누가 무엇을 했는가"의
규정 준수 기록이고, 여기는 "왜 실패했는가"의 진단 재료다.

수집은 상시, 파일 쓰기는 실패 시에만 한다 — 정상 경로의 디스크 비용은 0이다.
"""

from src.observability.levels import (
    FailureTrigger,
    TraceLevel,
    failure_triggers,
    severity_for,
)
from src.observability.trace_writer import flush_if_failed
from src.observability.trace_collector import (
    TraceStep,
    end_request,
    record_step,
    start_request,
    steps_for,
    traced,
)

__all__ = [
    "FailureTrigger",
    "TraceLevel",
    "TraceStep",
    "end_request",
    "flush_if_failed",
    "failure_triggers",
    "record_step",
    "severity_for",
    "start_request",
    "steps_for",
    "traced",
]
