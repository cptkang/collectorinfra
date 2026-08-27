"""감사 로그 모듈.

모든 쿼리 실행 이력을 기록한다. Phase 1에서는 파일 기반,
Phase 3에서는 DB 기반 저장소로 확장한다.
날짜별 로그 파일 로테이션을 지원한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from noise_gate.domain.process_rank import mask_args

logger = structlog.get_logger("audit")

# Phase 1: 파일 기반 감사 로그 (날짜별 분리)
AUDIT_LOG_DIR = Path("logs")
MAX_LOG_SIZE_MB = 100


class AuditEntry:
    """감사 로그 엔트리."""

    def __init__(self, **kwargs: Any) -> None:
        """엔트리를 생성한다.

        Args:
            **kwargs: 로그 필드 (timestamp, event, sql 등)
        """
        self._data = kwargs

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환한다.

        Returns:
            None이 아닌 필드만 포함하는 딕셔너리
        """
        return {k: v for k, v in self._data.items() if v is not None}

    def to_json(self) -> str:
        """JSON 문자열로 변환한다.

        Returns:
            JSON 문자열
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)


def _get_audit_log_path() -> Path:
    """날짜 기반 감사 로그 파일 경로를 반환한다.

    Returns:
        오늘 날짜의 감사 로그 파일 경로
    """
    today = datetime.now().strftime("%Y-%m-%d")
    return AUDIT_LOG_DIR / f"audit-{today}.jsonl"


async def log_query_execution(
    sql: str,
    row_count: int,
    execution_time_ms: float,
    success: bool,
    error: Optional[str] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    validation_warnings: Optional[list[str]] = None,
    retry_attempt: int = 0,
    source_name: Optional[str] = None,
    masked_columns: Optional[list[str]] = None,
) -> None:
    """쿼리 실행을 감사 로그에 기록한다.

    Args:
        sql: 실행된 SQL
        row_count: 결과 행 수
        execution_time_ms: 실행 시간 (ms)
        success: 성공 여부
        error: 에러 메시지 (실패 시)
        user_id: 사용자 ID (Phase 3)
        thread_id: 세션 ID
        validation_warnings: SQL 검증 경고 목록
        retry_attempt: 재시도 횟수
        source_name: DB 소스명
        masked_columns: 마스킹된 컬럼 목록
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="query_execution",
        sql=sql,
        row_count=row_count,
        execution_time_ms=round(execution_time_ms, 2),
        success=success,
        error=error,
        user_id=user_id,
        thread_id=thread_id,
        validation_warnings=validation_warnings,
        retry_attempt=retry_attempt,
        source_name=source_name,
        masked_columns=masked_columns,
    )

    # 구조화된 로깅 (event 키 충돌 방지)
    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("query_executed", **log_data)

    # 파일에 기록 (Phase 1)
    await _write_audit_file(entry)


async def log_user_request(
    user_query: str,
    output_format: str,
    has_file: bool,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """사용자 요청을 감사 로그에 기록한다.

    Args:
        user_query: 사용자 질의
        output_format: 요청 출력 형식
        has_file: 파일 업로드 여부
        user_id: 사용자 ID
        thread_id: 세션 ID
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="user_request",
        user_query=user_query,
        output_format=output_format,
        has_file=has_file,
        user_id=user_id,
        thread_id=thread_id,
    )

    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("user_request", **log_data)
    await _write_audit_file(entry)


async def log_drm_decrypt(
    file_name: Optional[str],
    file_size_bytes: int,
    success: bool,
    error: Optional[str] = None,
    ret_code: Optional[int] = None,
    elapsed_ms: Optional[float] = None,
    user_id: Optional[str] = None,
    temp_file: Optional[str] = None,
    mode: str = "form_fill",
) -> None:
    """DRM 복호화 시도를 감사 로그에 기록한다 (Plan 74 §2.8).

    파일 내용은 기록하지 않는다. temp_file은 ServiceLinker 자체 로그
    (LogPath/TransLogPath)와의 대사 키로 사용된다.

    Args:
        file_name: 업로드 파일명
        file_size_bytes: 파일 크기
        success: 복호화 성공 여부 (drm_disabled 차단도 False로 기록)
        error: 실패 사유
        ret_code: scsl CreateDecryptFileDAC 반환값
        elapsed_ms: 소요 시간 (ms)
        user_id: 사용자 ID
        temp_file: 복호화에 사용된 temp 파일명 (scsl 로그 대사용)
        mode: 호출 경로 — "form_fill"(양식 업로드) | "admin_verify"(어드민 진단)
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="drm_decrypt",
        mode=mode,
        file_name=file_name,
        file_size_bytes=file_size_bytes,
        success=success,
        error=error,
        ret_code=ret_code,
        elapsed_ms=round(elapsed_ms, 2) if elapsed_ms is not None else None,
        user_id=user_id,
        temp_file=temp_file,
    )
    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("drm_decrypt", **log_data)
    await _write_audit_file(entry)


#: 조사 원문(stdout)의 기록 상한. 전량은 CSV·트레이스가 갖고, 감사는 대조용 앞부분만 든다.
_INVESTIGATION_STDOUT_LIMIT = 4000


def _mask_shell_text(text: str, *, max_len: int) -> str:
    """수집 명령·표준출력의 민감정보를 가린다 (D-117 §18.5 계승).

    **두 마스커를 겹쳐 쓴다** — 각자 잡는 것이 다르다:
    - `mask_args`(Plan 47-1): `password=…`·`token: …`·접속문자열의 **값**. 키는 보존한다
    - `_mask_text`(D-141): `sk-…`·JWT·AWS/GitHub 토큰처럼 **접두사로 식별되는** 값

    한쪽만 쓰면 반대쪽이 평문으로 남는다 — 조사 stdout은 셸 명령 출력이라 둘 다 나온다.
    """
    if not text:
        return ""
    # 지연 import — `observability.trace_writer`가 `security.data_masker`를 참조하므로
    # 모듈 최상단에서 끌어오면 `src.security` 패키지 초기화와 순환한다(실측 2026-08-27).
    from src.observability.trace_writer import _mask_text

    return _mask_text(mask_args(text, max_len=max_len))


# 조사 감사 레코드의 결과 구분 (Plan 78 W6-1). 문자열 상수로 고정해 표기 드리프트를 막는다.
INVESTIGATION_OK = "ok"
INVESTIGATION_PARTIAL = "partial"
INVESTIGATION_FAILED = "failed"
INVESTIGATION_DENIED = "denied"
INVESTIGATION_TIMEOUT = "timeout"


async def log_investigation(
    *,
    request_id: Optional[str] = None,
    entry_point: str = "chat",
    targets: Optional[list[dict]] = None,
    outcome: str = INVESTIGATION_OK,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    profile: Optional[str] = None,
    commands: Optional[list[str]] = None,
    backend: Optional[str] = None,
    rc: Optional[int] = None,
    duration_ms: Optional[float] = None,
    authz: Optional[dict] = None,
    stdout: Optional[str] = None,
    truncation: Optional[dict] = None,
    cache: Optional[dict] = None,
    degraded: Optional[list[dict]] = None,
    **extra: Any,
) -> None:
    """호스트 조사 1건을 감사 로그에 기록한다 (Plan 78 W6 · 계약 C-B v2).

    **감사 스키마의 소유권은 78 W6에 있다**(80 §6 계약 C-B v2). 79 트랙 C가 재개되면
    신뢰도·분포·엔트로피 필드를 **추가**하는데, `AuditEntry`가 `**kwargs`를 그대로 받고
    `to_dict()`가 None을 떨구므로 **기존 레코드를 깨지 않고 확장**된다 — 이것이 v2 계약이
    성립하는 근거다(`**extra`가 그 확장 지점이다).

    신규 모듈을 만들지 않는 이유(SPEC C-5): 여기에 `AuditEntry` + 날짜별 JSONL + 로테이션이
    **이미 있다**. 별도 감사 경로를 세우면 "누가 무엇을 조사했는가"의 기록이 두 벌이 된다(D-053).

    Args:
        request_id: 요청 식별자(실패 트레이스와 대조하는 키)
        entry_point: 진입점 — "chat"(CW-B) | "event"(CW-A). G5 대칭 확인의 재료다
        targets: 조사 대상 [{server_name, hostname, ip, db_id}]
        outcome: ok | partial | failed | denied | timeout
        user_id: 사용자 ID
        thread_id: 세션 ID
        profile: 수집 프로파일(vm/middleware 등)
        commands: 실행된 수집 명령. 마스킹 후 저장한다
        backend: 조사 경로 — sre_agent | mcp_server | process_api
        rc: 수집 종료 코드
        duration_ms: 소요 시간(비용 귀속의 지연 축)
        authz: 인가 판정 {allowed, mode, principal, reason} — **W3-5가 채운다**(W6-5).
            추적에 신원과 권한 상태가 같은 세밀도로 남아야 G 계층의 증거가 된다
        stdout: 수집 원문. **마스킹 후** 저장한다(D-117 §18.5 계승)
        truncation: 절단 사실 {truncated, truncated_count, per_host}
        cache: 캐시 {hit, age_seconds}
        degraded: 강등·폴백 사유 목록(침묵 폴백 금지)
        **extra: 후속 확장 필드(계약 C-B v2)
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="host_investigation",
        request_id=request_id,
        entry_point=entry_point,
        targets=targets or None,
        target_count=len(targets) if targets else 0,
        outcome=outcome,
        user_id=user_id,
        thread_id=thread_id,
        profile=profile,
        commands=[_mask_shell_text(c, max_len=500) for c in commands] if commands else None,
        backend=backend,
        rc=rc,
        duration_ms=duration_ms,
        authz=authz,
        stdout=_mask_shell_text(stdout, max_len=_INVESTIGATION_STDOUT_LIMIT) if stdout else None,
        truncation=truncation,
        cache=cache,
        degraded=degraded or None,
        **extra,
    )
    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("host_investigation", **log_data)
    await _write_audit_file(entry)


async def _write_audit_file(entry: AuditEntry) -> None:
    """감사 로그를 날짜별 JSONL 파일에 추가한다.

    파일 크기가 MAX_LOG_SIZE_MB를 초과하면 순번을 붙여 로테이션한다.
    동기 파일 I/O를 asyncio.to_thread()로 감싸 이벤트 루프 블로킹을 방지한다.

    Args:
        entry: 감사 로그 엔트리
    """
    try:
        await asyncio.to_thread(_write_audit_file_sync, entry)
    except Exception as e:
        logging.getLogger(__name__).error(f"감사 로그 파일 쓰기 실패: {e}")


def _write_audit_file_sync(entry: AuditEntry) -> None:
    """감사 로그를 동기적으로 파일에 기록한다 (스레드에서 실행).

    Args:
        entry: 감사 로그 엔트리
    """
    log_path = _get_audit_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 파일 크기 체크 및 로테이션
    if log_path.exists() and log_path.stat().st_size > MAX_LOG_SIZE_MB * 1024 * 1024:
        counter = 1
        while True:
            rotated = log_path.with_suffix(f".{counter}.jsonl")
            if not rotated.exists():
                log_path.rename(rotated)
                break
            counter += 1

    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry.to_json() + "\n")


def setup_logging(log_level: str = "INFO") -> None:
    """structlog 기반 구조화된 로깅을 설정한다.

    표준 logging 루트 로거도 함께 설정하여
    logging.getLogger(__name__) 로그도 출력되도록 한다.

    Args:
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # 표준 logging 설정
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )

    # httpx는 성공한 모든 HTTP 요청을 INFO로 기록하여 노이즈가 큼 (헬스체크, MCP, LLM 호출 등)
    # 오류는 예외로 전파되어 앱 레벨에서 별도 로깅되므로 WARNING 이상만 출력
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # structlog 설정
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
