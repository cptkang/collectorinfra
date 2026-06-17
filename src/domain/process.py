"""공유 프로세스 도메인 (Plan 48 — 순수 함수).

폴스타 실시간 프로세스 API 응답(원시 dict 리스트)을 정렬·선별·집계하고,
민감정보를 마스킹하여 `ProcessInfo` 목록·`ProcessOverview` 현황으로 변환한다.
DB/HTTP/LLM 의존이 없는 결정적(deterministic) 로직이므로 domain 계층(최내곽)에 배치한다.

알람 서브시스템(`src/alarm/*`)과 메인 에이전트(`src/nodes/*`, `src/domain/*`)가
공유하는 자산이다. `ProcessInfo`/`mask_args`/`select_top_processes`는 Plan 47-1에서
`src/alarm/domain/`으로부터 승격(이동)되었으며, 알람 도메인은 이를 re-export하여
기존 동작을 보존한다 (Plan 48 §3.2, 동작 100% 불변 리팩터링).

핵심 원칙:
    - 정렬·선별·집계·마스킹은 Python이 결정적으로 수행, LLM은 인용·해석만 (Plan 47-1 §3.3).
    - args의 비밀번호/토큰/접속문자열은 mask_args()로 마스킹한 값만 보관 — 평문 노출 금지
      (프로젝트 제약: 민감 데이터 마스킹. 마스킹 누락은 보안 사고 — Plan 47-1 §9).
    - 집계는 "과한 해석 방지"(Plan 47-1 §9, Plan 48 §9)를 위해 단순 합/카운트만 수행한다.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

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


@dataclass
class ProcessInfo:
    """프로세스 1건 (마스킹·정규화 완료 — Plan 47-1).

    폴스타 실시간 프로세스 API 응답의 단일 프로세스를 선별·마스킹·정규화한 값.
    args는 반드시 mask_args()로 민감정보(비밀번호·토큰·접속문자열)를 제거한 값만 보관한다.
    """

    name: str
    pid: int
    ppid: int
    user: str
    p100cpu: float       # 100% 기준 CPU% (표시·랭킹 기본)
    pcpu: float          # 코어 합산 CPU%
    pmem: float          # 물리 메모리 %
    rss: int             # resident set size (bytes, 0 허용)
    args: str            # 마스킹·절단된 실행 인자


@dataclass
class ProcessOverview:
    """사용자 프로세스 조회 결과의 결정적 현황(마스킹 완료 — Plan 48 §5.1)."""

    source_host: str                 # 실제 조회에 사용한 hostname
    captured_at: Optional[datetime]  # API 응답 date
    total_count: int                 # 전체 프로세스 수
    metric: str                      # "cpu" | "memory" | "both"
    top_by_cpu: list[ProcessInfo]    # p100cpu(없으면 pcpu) 내림차순 상위 N (metric in cpu/both)
    top_by_mem: list[ProcessInfo]    # pmem 내림차순 상위 N (metric in memory/both)
    cpu_top_share: float             # 상위 N의 p100cpu 합(전체 점유 직관용)
    mem_top_share: float             # 상위 N의 pmem 합
    distinct_users: int              # 고유 실행 계정 수
    top_user_by_count: Optional[tuple[str, int]]  # 가장 많은 프로세스를 띄운 계정


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


def build_process_overview(
    raw_list: list[dict[str, Any]],
    metric: str,
    top_n: int,
    *,
    source_host: str,
    captured_at: Optional[datetime],
) -> ProcessOverview:
    """원시 프로세스 list를 마스킹·정규화·정렬·집계하여 현황으로 변환한다 (Plan 48 §5.1).

    순수 함수(외부 의존 없음). 모든 ProcessInfo.args는 mask_args()로 마스킹된다(필수).

    - cpu/both: p100cpu(없으면 pcpu) 내림차순 상위 N → top_by_cpu
    - memory/both: pmem 내림차순 상위 N → top_by_mem
    - cpu_top_share: top_by_cpu의 p100cpu 합 / mem_top_share: top_by_mem의 pmem 합
    - distinct_users: 전체 고유 user 수
    - top_user_by_count: 가장 많은 프로세스를 띄운 (user, count) — 빈 경우 None
    - 빈 list/누락 필드/0건/top_n 초과 안전 처리
    - 집계는 단순 합/카운트만 (name별 합산 등 과한 해석 금지 — §9)

    Args:
        raw_list: API data.list 배열 (None/빈 리스트 허용)
        metric: "cpu" | "memory" | "both"
        top_n: 보존할 상위 프로세스 수
        source_host: 실제 조회에 사용한 hostname
        captured_at: API 응답 date (스냅샷 시각)

    Returns:
        결정적·마스킹 완료된 ProcessOverview
    """
    items = [it for it in (raw_list or []) if isinstance(it, dict)]
    total = len(items)
    n = max(0, top_n)

    want_cpu = metric in ("cpu", "both")
    want_mem = metric in ("memory", "both")

    top_by_cpu: list[ProcessInfo] = []
    top_by_mem: list[ProcessInfo] = []
    cpu_top_share = 0.0
    mem_top_share = 0.0

    if want_cpu:
        ranked_cpu = sorted(
            items, key=lambda it: _sort_metric(it, "cpu"), reverse=True
        )
        top_by_cpu = [_to_process_info(it) for it in ranked_cpu[:n]]
        cpu_top_share = sum(p.p100cpu for p in top_by_cpu)

    if want_mem:
        ranked_mem = sorted(
            items, key=lambda it: _sort_metric(it, "memory"), reverse=True
        )
        top_by_mem = [_to_process_info(it) for it in ranked_mem[:n]]
        mem_top_share = sum(p.pmem for p in top_by_mem)

    # 고유 사용자 수 / 최다 프로세스 보유 계정 (단순 카운트만)
    users = [str(it.get("user", "") or "") for it in items]
    users = [u for u in users if u]
    distinct_users = len(set(users))
    top_user_by_count: Optional[tuple[str, int]] = None
    if users:
        user, count = Counter(users).most_common(1)[0]
        top_user_by_count = (user, count)

    return ProcessOverview(
        source_host=source_host,
        captured_at=captured_at,
        total_count=total,
        metric=metric,
        top_by_cpu=top_by_cpu,
        top_by_mem=top_by_mem,
        cpu_top_share=cpu_top_share,
        mem_top_share=mem_top_share,
        distinct_users=distinct_users,
        top_user_by_count=top_user_by_count,
    )


def resolve_metric_from_text(text: str) -> str:
    """질의 텍스트에서 정렬 기준을 추론한다 (폴백용 — Plan 48 §5.1).

    1순위는 input_parser가 채운 process_query_target.metric이며, 본 함수는 폴백이다.

    - 'cpu' 포함 → "cpu"
    - '메모리' | 'mem' | 'memory' 포함 → "memory"
    - 그 외 → "both"

    Returns:
        "cpu" | "memory" | "both"
    """
    haystack = (text or "").lower()
    if "cpu" in haystack:
        return "cpu"
    if "메모리" in haystack or "mem" in haystack or "memory" in haystack:
        return "memory"
    return "both"
