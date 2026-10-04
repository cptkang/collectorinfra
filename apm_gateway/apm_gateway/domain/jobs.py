"""작업 어휘 — 상태 · 호출 주체 · 작업 ID 형식 (plans/134 W0-B ·
SPEC-apm-question-coverage §3.3·§3.6).

순수 상수·검사 함수만 둔다(표준 라이브러리 · 벤더 무지). 상태 머신 전이와 저장은 애플리케이션 계층
(`application/jobs.py`)이 맡는다.
"""

from __future__ import annotations

import re

# 상태 머신: queued → running → completed | partial | failed | cancelled
#            queued|running → interrupted(재기동) · running → failed(정체 — error.code = stalled)
QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
PARTIAL = "partial"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"

LIVE_STATES: frozenset[str] = frozenset({QUEUED, RUNNING})
FINISHED_STATES: frozenset[str] = frozenset({COMPLETED, PARTIAL, FAILED, CANCELLED, INTERRUPTED})
# 결과(청크·텍스트)를 읽을 수 있는 상태 — 나머지 종료 상태는 결과가 없다.
READABLE_STATES: frozenset[str] = frozenset({COMPLETED, PARTIAL})

# 작업 기록 `error.code`(도구 오류 코드와 별개 — 작업이 왜 끝났는지)
ERROR_STALLED = "stalled"
ERROR_INTERRUPTED = "interrupted"
ERROR_CANCELLED = "cancelled"

# 호출 주체(§3.6) — 단일 `APM_GATEWAY_BEARER_TOKEN`은 `default`, 인증이 꺼져 있으면 `anonymous`.
DEFAULT_PRINCIPAL = "default"
ANONYMOUS_PRINCIPAL = "anonymous"
RESERVED_PRINCIPALS: frozenset[str] = frozenset({ANONYMOUS_PRINCIPAL})
_PRINCIPAL = re.compile(r"[a-z][a-z0-9_-]{0,31}")

# 작업 ID = uuid4 hex(서버 발급). 형식 밖 값은 경로로 쓰지 않는다(경로 조작 차단).
_JOB_ID = re.compile(r"[0-9a-f]{32}")
# 텍스트 부분 이름(`text-<name>.txt`)
_PART_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}")
OWNER_MAX = 200


def principal_error(principal: str) -> str | None:
    """설정에 적은 주체 이름의 형식 오류 사유(정상이면 None)."""
    if not _PRINCIPAL.fullmatch(principal):
        return f"주체 이름 {principal!r}는 소문자 슬러그 [a-z][a-z0-9_-]{{0,31}}여야 한다"
    if principal in RESERVED_PRINCIPALS:
        return f"주체 이름 {principal!r}는 예약어다({sorted(RESERVED_PRINCIPALS)})"
    return None


def is_job_id(value: object) -> bool:
    return isinstance(value, str) and bool(_JOB_ID.fullmatch(value))  # `$`는 끝 개행을 받는다


def is_part_name(value: object) -> bool:
    return isinstance(value, str) and bool(_PART_NAME.fullmatch(value))
