"""알람 종류 판정 (Plan 47-1 — 순수 함수).

`classify_alarm_kind`는 `AlarmEvent`에 의존하는 알람 전용 로직이므로 알람 도메인에 잔류한다.

프로세스 선별·마스킹 원시 함수(`ProcessInfo`/`mask_args`/`select_top_processes` 및
내부 헬퍼·정규식 상수)는 Plan 48 §3.2에 따라 공유 도메인 `src/domain/process.py`로
승격(이동)되었다. 기존 import 경로(`from src.alarm.domain.process_rank import ...`)를
보존하기 위해 본 모듈은 이들을 re-export한다 (동작 100% 불변).

핵심 원칙:
    - 정렬·선별·마스킹은 Python이 결정적으로 수행, LLM은 상위 프로세스 인용만 (Plan 47-1 §3.3).
    - args의 비밀번호/토큰/접속문자열은 mask_args()로 마스킹한 값만 보관 — 평문 노출 금지
      (프로젝트 제약: 민감 데이터 마스킹. 마스킹 누락은 보안 사고 — Plan 47-1 §9).
"""

from __future__ import annotations

from typing import Optional

from src.alarm.domain.alarm import AlarmEvent

# Plan 48 §3.2: 공유 프로세스 도메인으로 승격된 자산 re-export (기존 import 경로 보존).
from src.domain.process import (  # noqa: F401  (re-export)
    _CONN_STRING_RE,
    _MASK,
    _SENSITIVE_KEY,
    _SENSITIVE_RE,
    ProcessInfo,
    _as_float,
    _as_int,
    _sort_metric,
    _to_process_info,
    mask_args,
    select_top_processes,
)

# 알람 종류 판정 키워드 (대소문자 무시 — Plan 47-1 §5.2)
_CPU_KEYWORDS = ("cpu",)
_MEMORY_KEYWORDS = ("memory", "메모리", "mem")


def classify_alarm_kind(event: AlarmEvent) -> Optional[str]:
    """알람이 CPU/메모리인지 판정한다. 아니면 None (Plan 47-1 §5.2).

    판정 키워드(대소문자 무시) — resource_type / alarm_name 에서 검색:
        - cpu:    'cpu'
        - memory: 'memory' | '메모리' | 'mem'

    Returns:
        "cpu" | "memory" | None (디스크/네트워크 등 비대상)
    """
    haystack = f"{event.resource_type} {event.alarm_name}".lower()
    if any(kw in haystack for kw in _CPU_KEYWORDS):
        return "cpu"
    if any(kw in haystack for kw in _MEMORY_KEYWORDS):
        return "memory"
    return None
