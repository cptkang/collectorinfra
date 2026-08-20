"""로그 레벨 규약과 실패 판정 술어 (D-141).

## 레벨 규약 (스펙 §6.2)

| 레벨 | 의미 | 예시 이벤트 |
|---|---|---|
| ERROR | 요청이 최종 실패. 사용자에게 결과를 못 줌 | node.exception, graph.error_response, output.generation_failed |
| WARN  | 응답은 나갔으나 열화됨. 원인 추적 가치 있음 | query.zero_rows, generator.retry, mapping.unresolved |
| INFO  | 정상 경로의 의사결정 지점 | node.enter, node.exit, router.intent, sql.executed |
| DEBUG | 진단 시에만 필요한 상세 | llm.prompt, llm.raw_response, schema.detail |

ERROR·WARN은 구조화 `reason`을 강제한다 — 메시지 문자열로 사후 분류하면
집계가 문구 변경에 깨진다.

수집 레벨은 콘솔 출력 레벨(`AppConfig.log_level`)과 **독립**이다. 콘솔이 INFO여도
버퍼는 DEBUG까지 담는다 — 사후 진단이 목적이기 때문이다.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class TraceLevel(str, Enum):
    """트레이스 단계의 심각도."""

    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"
    DEBUG = "DEBUG"

    @property
    def rank(self) -> int:
        """비교용 순위. 클수록 심각하다."""
        return _RANKS[self]

    @property
    def requires_reason(self) -> bool:
        """구조화 사유가 필수인 레벨인지 반환한다."""
        return self in (TraceLevel.ERROR, TraceLevel.WARN)


_RANKS: dict[TraceLevel, int] = {
    TraceLevel.DEBUG: 0,
    TraceLevel.INFO: 1,
    TraceLevel.WARN: 2,
    TraceLevel.ERROR: 3,
}


class FailureTrigger(str, Enum):
    """"정상 응답을 제공하지 못한" 판정 기준 (사용자 확정 4기준)."""

    EXCEPTION = "exception"          # 예외 전파 · error_response 도달
    ZERO_ROWS = "zero_rows"          # data_query인데 결과 0건
    RETRY = "retry"                  # 검증·실행 실패로 재시도 발생
    OUTPUT_FAILED = "output_failed"  # 산출물 생성 실패 · 미해결 필드


#: 각 트리거의 severity. ERROR급은 "결과를 못 줌", WARN급은 "줬지만 열화".
_TRIGGER_SEVERITY: dict[FailureTrigger, str] = {
    FailureTrigger.EXCEPTION: "error",
    FailureTrigger.OUTPUT_FAILED: "error",
    FailureTrigger.ZERO_ROWS: "warn",
    FailureTrigger.RETRY: "warn",
}

_SEVERITY_RANK = {"warn": 1, "error": 2}


def _as_list(value: Any) -> list:
    """리스트가 아니면 빈 리스트로 취급한다(관측이 앱을 깨면 안 됨)."""
    return value if isinstance(value, list) else []


def _positive_int(value: Any) -> int:
    """정수로 해석되면 그 값을, 아니면 0을 반환한다."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _has_unresolved_fields(state: Mapping[str, Any]) -> bool:
    """단계적 도출(`smq_derivation`)에 미해결 필드가 남았는지 본다."""
    for entry in _as_list(state.get("smq_derivation")):
        if isinstance(entry, Mapping) and _as_list(entry.get("unresolved")):
            return True
    return False


def failure_triggers(state: Mapping[str, Any]) -> list[FailureTrigger]:
    """상태에서 실패 신호를 모두 추출한다.

    하나라도 해당하면 트레이스를 파일로 남긴다. 여러 기준에 동시 해당하면
    전부 기록하고(`triggers`), severity는 가장 높은 것을 채택한다.

    Args:
        state: LangGraph AgentState (또는 그 부분집합)

    Returns:
        해당하는 트리거 목록. 정상이면 빈 리스트.
    """
    triggers: list[FailureTrigger] = []

    try:
        # (1) 예외 전파 · error_response 도달
        if state.get("error_message") or state.get("current_node") == "error_response":
            triggers.append(FailureTrigger.EXCEPTION)

        # (2) data_query인데 결과 0건 — 캐시 관리·일반 추론은 원래 행이 없다
        if state.get("routing_intent") == "data_query":
            results = state.get("query_results")
            if isinstance(results, list) and not results:
                triggers.append(FailureTrigger.ZERO_ROWS)

        # (3) 재시도 발생 — 최종 성공했어도 원인 추적 가치가 있다
        if _positive_int(state.get("retry_count")) > 0:
            triggers.append(FailureTrigger.RETRY)

        # (4) 산출물 요청됐으나 미생성, 또는 매핑 미해결
        wanted_file = bool(state.get("file_type"))
        if (wanted_file and not state.get("output_file")) or _has_unresolved_fields(state):
            triggers.append(FailureTrigger.OUTPUT_FAILED)

    except Exception as e:  # pragma: no cover - 방어
        logger.debug("실패 판정 중 예외(무시): %s", e)

    return triggers


def severity_for(triggers: list[FailureTrigger]) -> str | None:
    """트리거 목록의 최고 severity를 반환한다.

    Args:
        triggers: `failure_triggers()` 결과

    Returns:
        "error" | "warn" | None(정상)
    """
    best: str | None = None
    for t in triggers:
        sev = _TRIGGER_SEVERITY.get(t)
        if sev is None:
            continue
        if best is None or _SEVERITY_RANK[sev] > _SEVERITY_RANK[best]:
            best = sev
    return best
