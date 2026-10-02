"""감사 로그 모듈.

모든 쿼리 실행 이력을 기록한다. Phase 1에서는 파일 기반,
Phase 3에서는 DB 기반 저장소로 확장한다.
날짜별 로그 파일 로테이션을 지원한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

from noise_gate.domain.process_rank import mask_args

logger = structlog.get_logger("audit")

# Phase 1: 파일 기반 감사 로그 (날짜별 분리)
AUDIT_LOG_DIR = Path("logs")
MAX_LOG_SIZE_MB = 100

# 쿼리 실행의 DB 복제 대상(D-027 이중 기록). 노드는 `app.state`에 닿지 못하므로(D-183)
# DB 감사가 구성된 `AuditService`가 생성될 때 자신을 등록한다. 약한 참조라 서비스가 사라지면
# (앱 종료·테스트 종료) 복제도 멈춘다. 등록이 없으면(CLI 등) 종전처럼 파일에만 남는다.
_db_mirror: weakref.ReferenceType | None = None

# DB 복제 1건(행 + 대량 조회 경보)의 시간 제한(초). 조회 경로는 복제를 기다리지 않으므로 이 값은
# 조회 지연이 아니라, 앱 DB가 느리거나 멈췄을 때 복제 태스크가 쌓여 남지 않게 하는 상한이다.
_DB_MIRROR_TIMEOUT_S = 5.0

# 진행 중인 복제 태스크. 이벤트 루프는 태스크를 약한 참조로만 들고 있어 참조를 따로 두지 않으면
# 끝나기 전에 GC될 수 있다(asyncio.create_task 문서). 끝난 태스크는 스스로 빠진다.
_pending_mirrors: set[asyncio.Task] = set()


def register_db_mirror(service: Any) -> None:
    """쿼리 실행을 DB에도 남길 `AuditService`를 등록한다(`mirror_query_execution` 보유)."""
    global _db_mirror
    _db_mirror = weakref.ref(service)


def _schedule_query_execution_mirror(**fields: Any) -> None:
    """등록된 서비스가 있으면 쿼리 실행 1건의 DB 복제를 백그라운드 태스크로 띄운다(기다리지 않는다).

    요청 식별자·클라이언트 IP는 감사 미들웨어가 structlog 컨텍스트에 묶어 둔 값을 **띄우는 지금**
    읽는다(요청 밖 실행이면 비어 있다). 호출자는 코루틴(`log_query_execution`)이라 실행 중인
    이벤트 루프가 항상 있다 — 루프 없는 동기 CLI 경로는 이 함수에 닿지 않고, CLI는 복제 대상을
    등록하지도 않는다(`AuditService`는 API 서버 기동 시에만 만든다).
    """
    service = _db_mirror() if _db_mirror is not None else None
    if service is None:
        return
    context = structlog.contextvars.get_contextvars()
    task = asyncio.get_running_loop().create_task(
        _mirror_query_execution(
            service,
            request_id=context.get("request_id"),
            client_ip=context.get("client_ip"),
            **fields,
        )
    )
    _pending_mirrors.add(task)
    task.add_done_callback(_pending_mirrors.discard)


async def _mirror_query_execution(service: Any, **fields: Any) -> None:
    """쿼리 실행 1건을 DB에 복제한다. 시간 초과·실패는 경고 로그로 남기고 삼킨다.

    조회 결과와 JSONL 1줄은 이미 조회 경로에서 끝났으므로 여기서의 실패는 DB 행 하나(와 대량 조회
    경보)가 빠지는 것에 그친다.
    """
    try:
        await asyncio.wait_for(service.mirror_query_execution(**fields), _DB_MIRROR_TIMEOUT_S)
    except TimeoutError:
        logging.getLogger(__name__).warning(
            "쿼리 실행 DB 감사 기록 시간 초과(%s초) — DB 행 누락, JSONL에는 남음",
            _DB_MIRROR_TIMEOUT_S,
        )
    except Exception as e:
        logging.getLogger(__name__).warning("쿼리 실행 DB 감사 기록 실패: %s", e)


async def wait_pending_db_mirrors() -> None:
    """이 루프에서 진행 중인 쿼리 실행 DB 복제가 끝나기를 기다린다(종료 정리·테스트용).

    복제마다 시간 제한이 있어 무한히 기다리지 않는다. 다른(닫힌) 루프의 태스크는 건너뛴다.
    """
    loop = asyncio.get_running_loop()
    tasks = [t for t in _pending_mirrors if not t.done() and t.get_loop() is loop]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


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

    # DB에도 한 행 — 관리자 화면(성공률·「쿼리 실행」 필터·대량 조회 경보)은 DB를 읽는다.
    # 백그라운드로 띄우고 기다리지 않는다 — 앱 DB 지연이 조회 경로에 얹히지 않게 한다.
    _schedule_query_execution_mirror(
        sql=sql,
        row_count=row_count,
        execution_time_ms=execution_time_ms,
        success=success,
        error=error,
        user_id=user_id,
        session_id=thread_id,
        target_db=source_name,
        retry_attempt=retry_attempt,
        masked_columns=masked_columns,
        validation_warnings=validation_warnings,
    )


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


async def log_rewrite_trace(
    trace: dict[str, Any],
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """재작성 감사 레코드를 남긴다(plans/107 §4.9 — `INTENT_FRAME_ENABLED`일 때만 호출된다).

    원문 전문은 싣지 않는다 — 원문은 ``user_request``에만 있고 이 레코드에는 슬롯 값과
    출처·게이트·검증 결과만 있다(D-183 PII 정책). 시나리오 하네스가 ``thread_id``로
    이 이벤트를 모아 게이트 통과 비율·검증 실패율을 낸다(plans/94 §19 O-e).

    Args:
        trace: ``build_rewrite_trace`` 산출물
        user_id: 사용자 ID
        thread_id: 세션 ID
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="rewrite_trace",
        rewrite_trace=trace,
        user_id=user_id,
        thread_id=thread_id,
    )

    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("rewrite_trace", **log_data)
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


async def log_silence_change(
    *,
    action: str,
    rule_id: str,
    actor: str,
    detail: Optional[dict] = None,
) -> None:
    """침묵 규칙의 생성·해제를 감사 로그에 기록한다 (Plan 54 모듈 5).

    침묵은 **실제로 알람을 억제**하므로, 누가 언제 무엇을 조용히 시켰는지가 규칙 자체보다
    오래 남아야 한다. 규칙 내용(매처·심각도 상한·사유·만료)을 통째로 남긴다 — 나중에
    "그때 어떤 조건이었나"를 되짚을 수 있어야 오억제 조사가 가능하다.

    Args:
        action: "create" | "revoke".
        rule_id: 침묵 규칙 id.
        actor: 행위자.
        detail: 규칙 스냅샷(생성 시 새 규칙, 해제 시 해제 직전 규칙).
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="silence_change",
        action=action,
        rule_id=rule_id,
        user_id=actor,
        detail=detail or {},
    )
    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("silence_change", **log_data)
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


async def _record_event(event: str, **fields: Any) -> None:
    """이벤트 1건을 구조화 로그와 날짜별 JSONL에 남긴다(아래 실행 그룹 계열 공용)."""
    entry = AuditEntry(timestamp=datetime.now(timezone.utc).isoformat(), event=event, **fields)
    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info(event, **log_data)
    await _write_audit_file(entry)


async def log_group_execution(
    *,
    group_key: str,
    label: str,
    kind: str,
    db_ids: list[str],
    row_count: int,
    elapsed_ms: float,
    error_db_ids: Optional[list[str]] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """실행 그룹 1건의 소요를 기록한다 (plans/82 v7 R-5 · D-249).

    그룹 계측(`observability.group_metrics`)은 프로세스 로컬 인메모리라 재기동마다 비는데,
    어느 존 그룹이 느린지는 사후에 봐야 한다. 신규 저장소를 만들지 않고 감사 파일에 남긴다.
    ``elapsed_ms``는 SQL 실행만이 아니라 스키마 분석·SQL 생성·재생성·소급 복구를 포함한
    **그룹 전체** 소요다(`query_execution`의 ``execution_time_ms``와 다르다).
    """
    await _record_event(
        "group_execution",
        group_key=group_key,
        label=label,
        kind=kind,
        db_ids=db_ids,
        row_count=row_count,
        elapsed_ms=round(elapsed_ms, 2),
        error_db_ids=error_db_ids or None,
        user_id=user_id,
        thread_id=thread_id,
    )


async def log_clarification(
    *,
    kind: str,
    axis: Optional[str] = None,
    option_count: int = 0,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """파이프라인 전 역질문의 발동을 기록한다 — 발동률의 분자 (plans/82 v7 R-6 · D-249).

    분모는 같은 파일의 ``user_request``다. 범위 선택(`scope_select`)은 시간 임계 없이
    그룹 2개 이상이면 묻기 때문에(U11) 습관화를 **발동률로** 통제하기로 했다 — 그 관측이
    이 레코드다. 원문은 싣지 않는다(``user_request``에만 둔다 — D-183).
    """
    await _record_event(
        "clarification_issued",
        kind=kind,
        axis=axis,
        option_count=option_count,
        user_id=user_id,
        thread_id=thread_id,
    )


async def log_source_memory(
    *,
    action: str,
    scope: str,
    case_id: str,
    sources: list[str] | None = None,
    origin: str | None = None,
    user_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    """소스 선택 기억의 쓰기·사용·삭제·승격을 기록한다(plans/132 W5 · §6.5).

    ``action``: write | use | delete | promote. 질의 원문은 싣지 않는다(``user_request``에만 —
    D-183) — 사례는 ``case_id``로 가리킨다.
    """
    await _record_event(
        "source_memory",
        action=action,
        scope=scope,
        case_id=case_id,
        sources=sources or None,
        origin=origin,
        user_id=user_id,
        thread_id=thread_id,
    )


async def log_doc_retrieval(
    *,
    collection_ids: list[str],
    status: str,
    hit_count: int,
    elapsed_ms: float,
    query: str,
    doc_ids: Optional[list[str]] = None,
    source: str = "api",
    reason: Optional[str] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """문서 RAG 조회 1건을 기록한다 (plans/126 W5 · D-261 정합).

    남기는 것과 남기지 않는 것을 의도적으로 갈랐다:
      - **남긴다**: 누가·어느 문서군·무엇을 물었는지(스크럽본)·결과 상태·건수·상위 문서 식별자·소요.
        "누가 규정 문서를 조회했나"가 감사의 요건이고, 자산 폐기(`stale_id`)처럼 관리자 조치가
        필요한 사건도 이 레코드로 사후 판독한다.
      - **남기지 않는다**: 문서 **본문**과 접속 토큰. 본문은 길고 기밀이며(감사 파일이 문서
        사본이 되어선 안 된다) 토큰은 어디에도 원문으로 남기지 않는다(plans/126 R-17).

    `query`는 호출부가 `scrub_pii` 를 통과시킨 값을 넘긴다 — 이 함수는 마스킹하지 않는다
    (마스킹 책임을 한 곳에 두고, 여기서 다시 덮으면 무엇이 가려졌는지 추적이 어렵다).
    """
    await _record_event(
        "doc_retrieval",
        collection_ids=collection_ids,
        doc_status=status,
        hit_count=hit_count,
        elapsed_ms=round(float(elapsed_ms), 1),
        user_query=query,
        doc_ids=doc_ids or [],
        source=source,
        reason=reason,
        user_id=user_id,
        thread_id=thread_id,
    )


async def log_scope_narrowed(
    *,
    selected: list[str],
    skipped: list[str],
    skipped_db_ids: list[str],
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """사용자가 조회 범위를 좁힌 턴을 기록한다 (plans/82 §5.3 불변식 6 · v7 R-6 · D-249).

    범위 축소는 복구되지 않는 절단이라 무엇을 보지 않았는지가 응답(미조회 범위 문구)과
    **감사 양쪽**에 남아야 한다 — 이 레코드가 감사 쪽이다.
    """
    await _record_event(
        "scope_narrowed",
        selected=selected,
        skipped=skipped,
        skipped_db_ids=skipped_db_ids,
        user_id=user_id,
        thread_id=thread_id,
    )


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
