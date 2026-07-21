"""영향 프로세스 선별·마스킹 (Plan 47-1 — 순수 함수).

폴스타 실시간 프로세스 API 응답(원시 dict 리스트)을 알람 종류에 맞춰 정렬·선별하고,
민감정보를 마스킹하여 `ProcessInfo` 목록으로 변환한다. DB/HTTP/LLM 의존이 없는
결정적(deterministic) 로직이므로 domain 계층에 배치한다 (Plan 47 alarm_pattern.py와 동일 원칙,
Known Mistakes 2026-03-23: 데이터 모델 변환 함수는 모델이 있는 계층에 배치).

핵심 원칙:
    - 정렬·선별·마스킹은 Python이 결정적으로 수행, LLM은 상위 프로세스 인용만 (Plan 47-1 §3.3).
    - args의 비밀번호/토큰/접속문자열은 mask_args()로 마스킹한 값만 보관 — 평문 노출 금지
      (프로젝트 제약: 민감 데이터 마스킹. 마스킹 누락은 보안 사고 — Plan 47-1 §9).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from src.alarm.domain.alarm import AlarmEvent, ProcessInfo

# 알람 종류 판정 키워드 (대소문자 무시 — Plan 47-1 §5.2, Plan 60 E6 §16.2 확장)
# 판정 순서: cpu → memory(기존, 비트 동일) → disk → network → process → log.
# cpu/memory를 최우선으로 검사해 기존 판정 결과를 보존한다(신규 키워드가 끼어들지 않음).
_CPU_KEYWORDS = ("cpu",)
_MEMORY_KEYWORDS = ("memory", "메모리", "mem")
_DISK_KEYWORDS = ("disk", "디스크", "volume", "filesystem", "inode")
_NETWORK_KEYWORDS = ("network", "net", "traffic", "네트워크", "bandwidth")
_PROCESS_KEYWORDS = ("process", "프로세스", "daemon", "service down", "프로세스다운")
_LOG_KEYWORDS = ("log", "logmonitor", "로그")

# 민감정보 마스킹 패턴 — 키(=password 등)는 보존하고 값만 *** 로 치환한다.
# 키워드 다음의 구분자([=: ] 또는 공백) 이후 비공백 토큰을 값으로 본다.
_SENSITIVE_KEY = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|credential)"
)
_SENSITIVE_RE = re.compile(
    rf"({_SENSITIVE_KEY})(\s*[=:]\s*|\s+)(\S+)",
    re.IGNORECASE,
)

# DB 접속 문자열(예: jdbc:postgresql://user:pass@host) 내 비밀번호 마스킹.
# scheme://user:<password>@host 형태에서 password 부분만 치환한다.
_CONN_STRING_RE = re.compile(
    r"(\b[a-zA-Z][\w+.-]*://[^\s:/@]+:)([^\s@]+)(@)",
)

_MASK = "***"


def classify_alarm_kind(event: AlarmEvent) -> Optional[str]:
    """알람 종류를 판정한다 (Plan 47-1 §5.2 + Plan 60 E6 §16.2).

    판정 키워드(대소문자 무시) — resource_type / alarm_name 에서 검색.
    **판정 순서**(cpu/memory 우선 → 나머지) — cpu/memory 판정은 기존과 비트 동일:
        1. cpu:     'cpu'
        2. memory:  'memory' | '메모리' | 'mem'
        3. disk:    'disk' | '디스크' | 'volume' | 'filesystem' | 'inode'
        4. network: 'network' | 'net' | 'traffic' | '네트워크' | 'bandwidth'
        5. process: 'process' | '프로세스' | 'daemon' | 'service down' | '프로세스다운'
        6. log:     'log' | 'logmonitor' | '로그'

    disk/network/process/log는 Plan 60 E6의 메시지 기반 보강(enrichment) 대상이다.
    기존 정렬 로직(select_top_processes·_sort_metric)은 cpu/memory만 특화 정렬하므로,
    신규 kind는 정렬 기본=cpu로 취급된다(프로세스 표는 host-wide 참고용으로 여전히 유효).

    Returns:
        "cpu" | "memory" | "disk" | "network" | "process" | "log" | None (비대상)
    """
    haystack = f"{event.resource_type} {event.alarm_name}".lower()
    if any(kw in haystack for kw in _CPU_KEYWORDS):
        return "cpu"
    if any(kw in haystack for kw in _MEMORY_KEYWORDS):
        return "memory"
    if any(kw in haystack for kw in _DISK_KEYWORDS):
        return "disk"
    if any(kw in haystack for kw in _NETWORK_KEYWORDS):
        return "network"
    if any(kw in haystack for kw in _PROCESS_KEYWORDS):
        return "process"
    if any(kw in haystack for kw in _LOG_KEYWORDS):
        return "log"
    return None


def mask_args(args: str, max_len: int = 120) -> str:
    """실행 인자에서 민감정보를 마스킹하고 길이를 제한한다 (Plan 47-1 §5.2).

    마스킹 대상(대소문자 무시, 키는 보존하고 값만 *** 로 치환):
        - password|passwd|pwd|secret|token|api_key|access_key|credential 의 값
        - DB 접속 문자열(scheme://user:<password>@host)의 비밀번호 부분
    그 외 매우 긴 인자는 max_len에서 절단 후 '…'를 부가한다.

    Returns:
        마스킹·절단된 안전한 인자 문자열
    """
    if not args:
        return ""
    # 1) 접속 문자열 비밀번호 마스킹 (key=value 마스킹보다 먼저 — '://' 컨텍스트 보존)
    masked = _CONN_STRING_RE.sub(rf"\1{_MASK}\3", args)
    # 2) key=value / key value 형태 민감정보 마스킹 (키 보존)
    masked = _SENSITIVE_RE.sub(rf"\1\2{_MASK}", masked)
    # 3) 길이 제한
    if len(masked) > max_len:
        masked = masked[:max_len].rstrip() + "…"
    return masked


def _as_float(value: Any) -> float:
    """값을 float로 변환한다 (None/파싱 실패 시 0.0)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _as_int(value: Any) -> int:
    """값을 int로 변환한다 (None/파싱 실패 시 0). 소수 문자열도 허용."""
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _sort_metric(item: dict[str, Any], alarm_kind: str) -> float:
    """정렬 기준 지표를 반환한다 (cpu=p100cpu→pcpu 폴백, memory=pmem)."""
    if alarm_kind == "memory":
        return _as_float(item.get("pmem"))
    # cpu: p100cpu 우선, 없으면 pcpu 폴백
    if item.get("p100cpu") is not None:
        return _as_float(item.get("p100cpu"))
    return _as_float(item.get("pcpu"))


def _to_process_info(item: dict[str, Any]) -> ProcessInfo:
    """원시 프로세스 dict를 마스킹·정규화된 ProcessInfo로 변환한다."""
    return ProcessInfo(
        name=str(item.get("name", "") or ""),
        pid=_as_int(item.get("pid")),
        ppid=_as_int(item.get("ppid")),
        user=str(item.get("user", "") or ""),
        p100cpu=_as_float(item.get("p100cpu")),
        pcpu=_as_float(item.get("pcpu")),
        pmem=_as_float(item.get("pmem")),
        rss=_as_int(item.get("rss")),
        args=mask_args(str(item.get("args", "") or "")),
    )


def select_top_processes(
    raw_list: list[dict[str, Any]], alarm_kind: str, top_n: int
) -> tuple[list[ProcessInfo], int]:
    """원시 프로세스 목록을 지표 내림차순 상위 N으로 선별한다 (Plan 47-1 §5.2).

    - cpu: p100cpu(없으면 pcpu) 내림차순
    - memory: pmem 내림차순
    - 각 항목 args는 mask_args()로 마스킹·절단
    - 동일 name 다수(워커 등)는 개별 행으로 유지 (pid로 구분, 집계 합산 안 함 — §9)

    Args:
        raw_list: API data.list 배열 (None/빈 리스트 허용)
        alarm_kind: "cpu" | "memory"
        top_n: 보존할 상위 프로세스 수

    Returns:
        (상위 N ProcessInfo 목록, 전체 프로세스 건수)
    """
    items = [it for it in (raw_list or []) if isinstance(it, dict)]
    total = len(items)
    ranked = sorted(items, key=lambda it: _sort_metric(it, alarm_kind), reverse=True)
    top = [_to_process_info(it) for it in ranked[: max(0, top_n)]]
    return top, total
