"""관리자 「DB 구조」 API (plans/104 §3.2 · Wave 2 계약 §5).

MCP 소스 목록 · 변경 점검 · 구조 분석 초안·승인·버전 · 신규 시스템 등록 흐름 · DDL 스키마
등록(D-292)을 관리자에게 연다. 무거운 작업(점검·분석·등록·DDL 등록)은 `AdminJobRunner` 백그라운드
잡으로 돌리고 202 + `job_id`를 돌려준다.

- 전 엔드포인트 `require_admin_user`(D-069) + `ADMIN_ACTION` 감사 · 응답 `audit_logged`
  (diff-report는 YAML 본문이라 `X-Audit-Logged` 헤더로 싣는다).
- 서비스(`DBStructureService`·`DBRegistrationService`)가 유일한 진입이다 —
  라우트는 입력 검증·예외 매핑·감사만 한다.
- 고정 경로(`sources`·`jobs`)를 `{source}` 경로보다 **먼저** 선언한다(plans/104 S3 재발 방지).
- 예외 매핑: `StructureStoreUnavailable` 503 · `JobConflictError` 409 · `DraftNotApprovable` 409
  (code가 `not_found`·`version_not_found`·`legacy_not_found`면 404) · `LookupError` 404 ·
  그 밖의 `ValueError` 422.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import Any, Literal, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, model_validator

from src.api.admin_audit import log_admin_event
from src.api.dependencies import require_admin_user
from src.domain.table_definitions import IMPORT_MAX_CHARS as _DEFINITION_IMPORT_MAX_CHARS

logger = logging.getLogger(__name__)
router = APIRouter()

_PREFIX = "/admin/db-structure"
# 소스·초안·잡 식별자 형식 — 파일 경로·Redis 키에 쓰이므로 좁게 허용한다(계약 §5)
_ID_PATTERN = r"^[A-Za-z0-9_-]+$"

RegisterStep = Literal["probe", "schema", "descriptions", "db_description", "seeds", "value_index"]
DDLEngine = Literal["postgresql", "db2", "mariadb"]
AssetKind = Literal[
    "relationships", "allowed_tables", "code_values", "entity_keys", "query_rules",
    "query_examples", "seeds", "prompt_template", "table_definitions",
]
# 테이블 정의 편집 한 번에 고칠 수 있는 테이블 수 상한(D-305)
_DEFINITION_EDITS_MAX = 1000
# DDL 본문 상한(문자 수) — 화면의 파일 크기 상한(5MB)과 맞춘다
_DDL_MAX_CHARS = 5_000_000

# `DraftNotApprovable.code` 중 "대상 없음"(404)
# — 나머지(not_pending·validation_failed·env_mismatch)는 409
_NOT_FOUND_CODES = frozenset({"not_found", "version_not_found", "legacy_not_found"})

_T = TypeVar("_T")


# === 요청 모델 ===


class CheckRequest(BaseModel):
    """변경 점검 요청."""

    code_columns: list[str] | None = Field(
        None, description="값 목록을 수집할 코드성 컬럼 `table.column`(G-10 (a))"
    )


class AnalyzeRequest(BaseModel):
    """구조 분석 요청."""

    scope: Literal["all", "changed", "tables"] = Field(
        ..., description="all=전체 · changed=마지막 점검에서 바뀐 테이블 · tables=지정 테이블"
    )
    tables: list[str] | None = Field(None, description="scope=tables일 때 대상 테이블")
    code_columns: list[str] | None = Field(
        None, description="값 목록을 수집할 코드성 컬럼 `table.column`(G-10 (a))"
    )

    @model_validator(mode="after")
    def _tables_required_for_tables_scope(self) -> AnalyzeRequest:
        if self.scope == "tables" and not self.tables:
            raise ValueError("scope=tables이면 tables를 1개 이상 지정해야 합니다")
        return self


class ReasonRequest(BaseModel):
    """승인·반려·되돌리기 사유."""

    reason: str = Field("", max_length=2000, description="사유(감사 기록에 남는다)")


class RegisterRequest(BaseModel):
    """신규 연동 등록 잡 요청."""

    steps: list[RegisterStep] = Field(
        ..., min_length=1, description="실행할 단계(순서는 서비스가 고정)"
    )
    tables: list[str] | None = Field(
        None, description="설명 생성 범위 테이블(없으면 서비스 기본 범위)"
    )


class DescriptionApplyRequest(BaseModel):
    """컬럼 설명·유사어 초안 적용 요청."""

    exclude_tables: list[str] | None = Field(None, description="적용에서 제외할 테이블")


class DBDescriptionRequest(BaseModel):
    """DB 상세 설명 적용 요청."""

    text: str = Field(..., min_length=1, description="DB 상세 설명")
    origin: Literal["manual", "llm"] = Field(
        ..., description="manual=직접 입력 · llm=LLM 초안 채택"
    )


class DDLPreviewRequest(BaseModel):
    """DDL 해석 미리보기 요청(D-292) — SQL은 실행하지 않고 텍스트로만 읽는다."""

    engine: DDLEngine = Field(..., description="DDL 방언(식별자 대소문자·타입 표기 기준)")
    text: str = Field(..., min_length=1, max_length=_DDL_MAX_CHARS, description="DDL 원문")


class AssetProfileRequest(BaseModel):
    """자산 자동 생성 프로파일링 요청(D-294)."""

    tables: list[str] | None = Field(
        None, description="프로파일링 범위(없으면 프로필 allowed_tables · 없으면 전체)"
    )


class AssetApproveRequest(BaseModel):
    """자산 초안 승인 요청 — 고른 자산만 적용한다."""

    include: list[AssetKind] = Field(..., min_length=1, description="적용할 자산")
    allowed_tables: list[str] | None = Field(
        None, description="조회 대상 테이블 선택(없으면 초안 후보 그대로)"
    )
    table_definition_tables: list[str] | None = Field(
        None, description="승인할 테이블 정의 선택(없으면 초안 행 전부 — 검증 실패 행이 있으면 409)"
    )
    reason: str = Field("", max_length=2000, description="사유(감사 기록에 남는다)")


class TableDefinitionImportRequest(BaseModel):
    """테이블 정의 가져오기 요청(D-305) — 시드 형식 YAML 원문(최상위 `tables:`)."""

    text: str = Field(
        ..., min_length=1, max_length=_DEFINITION_IMPORT_MAX_CHARS, description="YAML 원문"
    )


class TableDefinitionLlmRequest(BaseModel):
    """테이블 정의 LLM 묶음 초안 요청."""

    only_failed: bool = Field(False, description="실패 묶음만 다시 실행")


class TableDefinitionEdit(BaseModel):
    """테이블 정의 1행 편집 — 보낸 필드만 바꾸고 출처는 `manual`이 된다.

    길이·형식은 서비스가 검증한다.
    """

    manages: str | None = Field(None, max_length=5000, description="관리 정보")
    notes: str | None = Field(None, max_length=5000, description="주의")
    kind: str | None = Field(None, max_length=50, description="성격")
    key_columns: list[str] | None = Field(None, max_length=50, description="대표 컬럼")


class TableDefinitionEditRequest(BaseModel):
    """테이블 정의 편집 저장 요청 — ``{테이블: 편집}``."""

    edits: dict[str, TableDefinitionEdit] = Field(..., description="테이블별 편집")

    @model_validator(mode="after")
    def _bounded(self) -> TableDefinitionEditRequest:
        if not self.edits or len(self.edits) > _DEFINITION_EDITS_MAX:
            raise ValueError(f"edits는 1~{_DEFINITION_EDITS_MAX}개여야 합니다")
        return self


class DDLImportRequest(DDLPreviewRequest):
    """DDL 스키마 등록 요청 — 미리보기에서 본 결과와 같을 때만 저장한다."""

    expected_hash: str = Field(
        ..., pattern=r"^[0-9a-f]{1,128}$", description="미리보기 응답의 `snapshot_hash`"
    )


# === 의존성 — 서비스·잡 러너 (테스트는 dependency_overrides로 교체) ===


def _cache_manager(config: Any) -> Any:
    from src.schema_cache.cache_manager import get_cache_manager

    return get_cache_manager(config)


def build_structure_service(config: Any) -> Any:
    """`DBStructureService`를 만든다(서비스 모듈은 지연 import — 라우터 import 시점 부작용 0)."""
    from src.schema_cache.db_structure_service import DBStructureService

    return DBStructureService(config, _cache_manager(config))


def build_registration_service(config: Any) -> Any:
    """`DBRegistrationService`를 만든다(설정 저장 B-7 판정도 이 함수를 쓴다 — 사본 금지)."""
    from src.schema_cache.db_registration_service import DBRegistrationService

    return DBRegistrationService(config, _cache_manager(config))


def get_structure_service(request: Request) -> Any:
    """실행 중 프로세스 설정(`app.state.config`)으로 구조 서비스를 만든다."""
    return build_structure_service(request.app.state.config)


def get_registration_service(request: Request) -> Any:
    """실행 중 프로세스 설정(`app.state.config`)으로 등록 서비스를 만든다."""
    return build_registration_service(request.app.state.config)


def asset_sql_checker(sql: str, schema_info: Any, engine: str, db_id: str = "") -> list[str]:
    """LLM 자산 SQL 검증기 — 질의 경로 `validate_sql`(참조 테이블·컬럼 실존 · SELECT 전용)의 오류.

    서비스(`src/schema_cache`)는 application 계층의 `validate_sql`을 import할 수 없어 조립부가
    주입한다(D-294). DB별 한글 식별자 허용 정책은 질의 경로와 같이 레지스트리로 해석한다
    (plans/137 — 한글 컬럼 DB의 예시 SQL이 질의 경로와 다르게 거부되지 않게).
    """
    from src.routing.registry import hangul_identifiers_allowed
    from src.sql_validation import validate_sql

    return list(validate_sql(
        sql, dict(schema_info), db_engine=engine, default_limit=50,
        allow_hangul_identifiers=hangul_identifiers_allowed(db_id),
    ).errors)


def build_asset_service(config: Any) -> Any:
    """`AssetGenerationService`를 만든다(D-294 — 지연 import · LLM SQL 검증기 주입)."""
    from src.schema_cache.asset_generation_service import AssetGenerationService

    return AssetGenerationService(config, _cache_manager(config), sql_checker=asset_sql_checker)


def get_asset_service(request: Request) -> Any:
    """실행 중 프로세스 설정으로 자산 자동 생성 서비스를 만든다."""
    return build_asset_service(request.app.state.config)


def get_admin_job_runner(request: Request) -> Any:
    """모듈 싱글톤 관리자 잡 러너를 돌려준다."""
    from src.schema_cache.admin_jobs import get_job_runner

    return get_job_runner(_cache_manager(request.app.state.config))


# === 공통 헬퍼 ===


def _to_http_error(error: Exception) -> HTTPException | None:
    """서비스·저장소 예외를 HTTP 오류로 바꾼다(매핑 대상이 아니면 None — 500으로 전파)."""
    from src.schema_cache.admin_jobs import JobConflictError
    from src.schema_cache.db_structure_service import DraftNotApprovable
    from src.schema_cache.structure_store import StructureStoreUnavailable

    if isinstance(error, StructureStoreUnavailable):
        return HTTPException(status_code=503, detail=str(error))
    if isinstance(error, DraftNotApprovable):
        # 대상 자체가 없으면 404, 있지만 지금 승인·반려할 수 없는 상태면 409
        status = 404 if error.code in _NOT_FOUND_CODES else 409
        return HTTPException(status_code=status, detail=str(error))
    if isinstance(error, JobConflictError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, LookupError):
        detail = error.args[0] if error.args else str(error)
        return HTTPException(status_code=404, detail=str(detail))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return None


async def _call(awaitable: Awaitable[_T]) -> _T:
    """서비스 호출을 기다리고 예외를 HTTP 오류로 매핑한다."""
    try:
        return await awaitable
    except HTTPException:
        raise
    except Exception as e:
        http_error = _to_http_error(e)
        if http_error is None:
            raise
        logger.info("DB 구조 API 요청 거절(%d): %s", http_error.status_code, e)
        raise http_error from e


async def _audit(request: Request, admin: dict[str, Any], action: str, **extra: Any) -> bool:
    """관리자 구조 작업을 `ADMIN_ACTION`으로 감사 기록한다(값이 None인 필드는 뺀다)."""
    from src.domain.audit import AuditEvent

    fields = {key: value for key, value in extra.items() if value is not None}
    return await log_admin_event(
        request,
        admin.get("sub"),
        AuditEvent.ADMIN_ACTION.value,
        {"action": action, **fields},
    )


def _with_audit(result: dict[str, Any], audit_logged: bool) -> dict[str, Any]:
    return {**result, "audit_logged": audit_logged}


_SourcePath = Path(..., pattern=_ID_PATTERN, description="MCP 소스 이름(= db_id)")
_DraftPath = Path(..., pattern=_ID_PATTERN, description="초안 식별자")


# === 고정 경로 — `{source}`보다 먼저 선언 ===


@router.get(f"{_PREFIX}/sources")
async def list_sources(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
) -> dict[str, Any]:
    """MCP 소스 × 레지스트리 × ACTIVE_DB_IDS 대조 목록(구조 상태·마지막 점검·등록·준비도 포함)."""
    result = await _call(service.list_sources())
    return _with_audit(result, await _audit(request, _admin, "list_sources"))


@router.get(f"{_PREFIX}/jobs/{{job_id}}")
async def get_job(
    job_id: str = Path(..., pattern=_ID_PATTERN),
    _admin: dict[str, Any] = Depends(require_admin_user),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """관리자 잡 진행·결과를 조회한다.

    감사하지 않는다 — 화면이 수 초 간격으로 폴링하는 상태 조회라 감사 로그를 잠식한다. 잡의 시작
    (점검·분석·등록)은 시작 엔드포인트가 이미 감사로 남긴다.
    """
    record: dict[str, Any] | None = await _call(runner.get(job_id))
    if record is None:
        raise HTTPException(status_code=404, detail=f"잡이 존재하지 않습니다: {job_id}")
    return record


# === 소스 단위 경로 ===


@router.get(f"{_PREFIX}/{{source}}")
async def get_detail(
    request: Request,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
) -> dict[str, Any]:
    """소스 상세 — 현행 프로필·초안·버전·마지막 점검·스냅샷·등록 상태·준비도."""
    result = await _call(service.get_detail(source))
    return _with_audit(result, await _audit(request, _admin, "get_detail", source=source))


@router.post(f"{_PREFIX}/{{source}}/check", status_code=202)
async def start_check(
    request: Request,
    source: str = _SourcePath,
    body: CheckRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """변경 점검 잡을 시작한다(스냅샷 diff · 구조 영향 · 신규 코드값)."""
    by = _admin.get("sub")
    code_columns = body.code_columns if body else None
    job = await _call(
        runner.start(
            kind="check",
            db_id=source,
            by=by,
            params={"code_columns": code_columns},
            work=lambda ctx: service.run_check(source, by=by, code_columns=code_columns, ctx=ctx),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "check",
        source=source,
        job_id=job.get("job_id"),
        code_columns=code_columns,
    )
    return _with_audit(job, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/analyze", status_code=202)
async def start_analyze(
    request: Request,
    body: AnalyzeRequest,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """구조 분석 잡을 시작한다(결과는 초안으로 저장된다)."""
    by = _admin.get("sub")
    job = await _call(
        runner.start(
            kind="analyze",
            db_id=source,
            by=by,
            params={"scope": body.scope, "tables": body.tables, "code_columns": body.code_columns},
            work=lambda ctx: service.run_analyze(
                source,
                scope=body.scope,
                tables=body.tables,
                code_columns=body.code_columns,
                by=by,
                ctx=ctx,
            ),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "analyze",
        source=source,
        job_id=job.get("job_id"),
        scope=body.scope,
        tables=body.tables,
        code_columns=body.code_columns,
    )
    return _with_audit(job, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/legacy/draft", status_code=202)
async def start_legacy_draft(
    request: Request,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """레거시 분석본(승인 버전 없는 Redis 구조 정보)으로 구조 초안 잡을 시작한다.

    LLM 구조 분석은 하지 않는다(패턴이 있으면 샘플 SQL 생성 1회). 후보가 아니면 잡을 만들지 않고
    404로 거절한다 — 잡 안에서도 같은 확인을 다시 한다(그사이 승인됐을 수 있다).
    """
    by = _admin.get("sub")
    await _call(service.legacy_structure_meta(source))
    job = await _call(
        runner.start(
            kind="legacy_draft",
            db_id=source,
            by=by,
            params={},
            work=lambda ctx: service.run_legacy_draft(source, by=by, ctx=ctx),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "legacy_draft",
        source=source,
        job_id=job.get("job_id"),
    )
    return _with_audit(job, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/drafts/{{draft_id}}/approve")
async def approve_draft(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    body: ReasonRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
) -> dict[str, Any]:
    """구조 초안을 승인해 프로필에 적용한다(검증 실패·환경 불일치·대기 아님이면 409)."""
    reason = body.reason if body else ""
    result = await _call(
        service.approve_draft(source, draft_id, by=_admin.get("sub"), reason=reason)
    )
    audit_logged = await _audit(
        request,
        _admin,
        "approve_draft",
        source=source,
        draft_id=draft_id,
        reason=reason,
        ver=(result.get("version") or {}).get("ver"),
    )
    return _with_audit(result, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/drafts/{{draft_id}}/reject")
async def reject_draft(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    body: ReasonRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
) -> dict[str, Any]:
    """구조 초안을 반려한다."""
    reason = body.reason if body else ""
    result = await _call(
        service.reject_draft(source, draft_id, by=_admin.get("sub"), reason=reason)
    )
    audit_logged = await _audit(
        request,
        _admin,
        "reject_draft",
        source=source,
        draft_id=draft_id,
        reason=reason,
    )
    return _with_audit(result, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/versions/{{ver}}/rollback")
async def rollback_version(
    request: Request,
    source: str = _SourcePath,
    ver: int = Path(..., ge=0, description="되돌릴 버전 번호"),
    body: ReasonRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
) -> dict[str, Any]:
    """이전 버전의 프로필 원문을 다시 적용한다."""
    reason = body.reason if body else ""
    result = await _call(service.rollback(source, ver, by=_admin.get("sub"), reason=reason))
    audit_logged = await _audit(
        request,
        _admin,
        "rollback",
        source=source,
        ver=ver,
        reason=reason,
        new_ver=(result.get("version") or {}).get("ver"),
    )
    return _with_audit(result, audit_logged)


@router.get(f"{_PREFIX}/{{source}}/diff-report")
async def diff_report(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = Query(..., pattern=_ID_PATTERN, description="초안 식별자"),
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_structure_service),
) -> PlainTextResponse:
    """초안의 필드별 차이 보고서(YAML)를 내려준다 — 감사 여부는 `X-Audit-Logged` 헤더."""
    text = await _call(service.diff_report(source, draft_id))
    audit_logged = await _audit(
        request,
        _admin,
        "diff_report",
        source=source,
        draft_id=draft_id,
    )
    return PlainTextResponse(
        text,
        media_type="text/yaml; charset=utf-8",
        headers={
            "X-Audit-Logged": "true" if audit_logged else "false",
            "Content-Disposition": f'attachment; filename="{source}-{draft_id}-diff.yaml"',
        },
    )


@router.post(f"{_PREFIX}/{{source}}/register", status_code=202)
async def start_register(
    request: Request,
    body: RegisterRequest,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """신규 연동 등록 잡을 시작한다(probe·schema·descriptions·db_description·seeds·value_index)."""
    by = _admin.get("sub")
    steps = list(dict.fromkeys(body.steps))
    job = await _call(
        runner.start(
            kind="register",
            db_id=source,
            by=by,
            params={"steps": steps, "tables": body.tables},
            work=lambda ctx: service.run_register(
                source,
                steps=steps,
                tables=body.tables,
                by=by,
                ctx=ctx,
            ),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "register",
        source=source,
        job_id=job.get("job_id"),
        steps=steps,
        tables=body.tables,
    )
    return _with_audit(job, audit_logged)


@router.get(f"{_PREFIX}/{{source}}/registration")
async def get_registration(
    request: Request,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
) -> dict[str, Any]:
    """단계별 등록 상태 · 준비도 · 예상 LLM 호출 수 · 서버 변수."""
    result = await _call(service.get_registration(source))
    return _with_audit(
        result,
        await _audit(request, _admin, "get_registration", source=source),
    )


@router.post(f"{_PREFIX}/{{source}}/ddl/preview")
async def preview_ddl(
    request: Request,
    body: DDLPreviewRequest,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
) -> dict[str, Any]:
    """DDL을 해석해 테이블·컬럼·PK·FK와 현재 기준선 대비 차이를 보인다(저장 0 · D-292)."""
    result = await _call(service.preview_ddl(source, engine=body.engine, text=body.text))
    audit_logged = await _audit(
        request,
        _admin,
        "ddl_preview",
        source=source,
        engine=body.engine,
        text_length=len(body.text),
        table_count=result.get("table_count"),
        snapshot_hash=result.get("snapshot_hash"),
    )
    return _with_audit(result, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/ddl/import", status_code=202)
async def start_ddl_import(
    request: Request,
    body: DDLImportRequest,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """DDL 해석 결과를 O-2 자리(스키마 캐시·기준선)에 저장하는 잡을 시작한다(D-292).

    미리보기와 다르면(해시 불일치) 잡을 만들지 않고 422로 거절한다. 잡 파라미터에는 DDL 본문을
    싣지 않는다(잡 레코드는 Redis에 남는다).
    """
    by = _admin.get("sub")
    prepared = await _call(service.prepare_ddl_import(
        source, engine=body.engine, text=body.text, expected_hash=body.expected_hash,
    ))
    table_count = prepared.snapshot["table_count"]
    job = await _call(
        runner.start(
            kind="ddl_import",
            db_id=source,
            by=by,
            params={
                "engine": body.engine,
                "text_length": len(body.text),
                "snapshot_hash": body.expected_hash,
                "table_count": table_count,
            },
            work=lambda ctx: service.run_ddl_import(source, prepared, by=by, ctx=ctx),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "ddl_import",
        source=source,
        job_id=job.get("job_id"),
        engine=body.engine,
        text_length=len(body.text),
        table_count=table_count,
        snapshot_hash=body.expected_hash,
    )
    return _with_audit(job, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/description-drafts/{{draft_id}}/apply")
async def apply_description_draft(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    body: DescriptionApplyRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
) -> dict[str, Any]:
    """컬럼 설명·유사어 초안을 적용한다(제외 테이블 제외 · 사람 자산 보존 병합)."""
    exclude_tables = body.exclude_tables if body else None
    result = await _call(
        service.apply_description_draft(
            source,
            draft_id,
            exclude_tables=exclude_tables,
            by=_admin.get("sub"),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "apply_description_draft",
        source=source,
        draft_id=draft_id,
        exclude_tables=exclude_tables,
    )
    return _with_audit(result, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/description-drafts/{{draft_id}}/discard")
async def discard_description_draft(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
) -> dict[str, Any]:
    """컬럼 설명·유사어 초안을 폐기한다."""
    result = await _call(service.discard_description_draft(source, draft_id, by=_admin.get("sub")))
    audit_logged = await _audit(
        request,
        _admin,
        "discard_description_draft",
        source=source,
        draft_id=draft_id,
    )
    return _with_audit(result, audit_logged)


@router.put(f"{_PREFIX}/{{source}}/db-description")
async def set_db_description(
    request: Request,
    body: DBDescriptionRequest,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
) -> dict[str, Any]:
    """DB 상세 설명을 적용한다(LLM 초안 채택 = llm · 직접 입력 = manual)."""
    result = await _call(
        service.set_db_description(
            source,
            text=body.text,
            origin=body.origin,
            by=_admin.get("sub"),
        )
    )
    audit_logged = await _audit(
        request,
        _admin,
        "set_db_description",
        source=source,
        origin=body.origin,
        text_length=len(body.text),
    )
    return _with_audit(result, audit_logged)


@router.get(f"{_PREFIX}/{{source}}/config-snippets")
async def config_snippets(
    request: Request,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_registration_service),
) -> dict[str, Any]:
    """사람이 반영할 레지스트리 항목 조각(파일 쓰기 0 — R11)."""
    result = await _call(service.config_snippets(source))
    return _with_audit(
        result,
        await _audit(request, _admin, "config_snippets", source=source),
    )


# === 스키마 자산 자동 생성 (D-294 · plans/133) ===

_AssetFileKind = Literal["seeds", "prompt_template"]


@router.get(f"{_PREFIX}/{{source}}/assets")
async def get_assets(
    request: Request,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """자산 초안 목록 · 시드·DB 전용 섹션 파일과 버전."""
    result = await _call(service.overview(source))
    return _with_audit(result, await _audit(request, _admin, "get_assets", source=source))


@router.post(f"{_PREFIX}/{{source}}/assets/profile", status_code=202)
async def start_asset_profile(
    request: Request,
    source: str = _SourcePath,
    body: AssetProfileRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """프로파일링 잡을 시작한다 — 카탈로그·데이터 읽기 전용 조회로 자산 초안(LLM 0)."""
    by = _admin.get("sub")
    tables = body.tables if body else None
    job = await _call(runner.start(
        kind="asset_profile", db_id=source, by=by, params={"tables": tables},
        work=lambda ctx: service.run_asset_profile(source, tables=tables, by=by, ctx=ctx),
    ))
    audit_logged = await _audit(
        request, _admin, "asset_profile", source=source, job_id=job.get("job_id"), tables=tables,
    )
    return _with_audit(job, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/asset-drafts/{{draft_id}}/llm", status_code=202)
async def start_asset_llm(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """LLM 보조 잡을 시작한다 — 쿼리 예시 · DB 전용 규칙 섹션(결정적 검증·실행 포함)."""
    by = _admin.get("sub")
    job = await _call(runner.start(
        kind="asset_llm", db_id=source, by=by, params={"draft_id": draft_id},
        work=lambda ctx: service.run_asset_llm(source, draft_id, by=by, ctx=ctx),
    ))
    audit_logged = await _audit(
        request, _admin, "asset_llm", source=source, draft_id=draft_id, job_id=job.get("job_id"),
    )
    return _with_audit(job, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/asset-drafts/{{draft_id}}/approve")
async def approve_asset_draft(
    request: Request,
    body: AssetApproveRequest,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """고른 자산만 적용한다(프로필 키 · 유사어 시드 · DB 전용 규칙 섹션)."""
    include = list(dict.fromkeys(body.include))
    result = await _call(service.approve_asset_draft(
        source, draft_id, include=include, allowed_tables=body.allowed_tables,
        by=_admin.get("sub"), reason=body.reason,
        table_definition_tables=body.table_definition_tables,
    ))
    audit_logged = await _audit(
        request, _admin, "approve_asset_draft", source=source, draft_id=draft_id,
        include=include, allowed_tables=body.allowed_tables, reason=body.reason,
        table_definition_tables=body.table_definition_tables,
        applied=result.get("applied"),
    )
    return _with_audit(result, audit_logged)


# --- 테이블 정의 (D-305 · plans/138 W3) ---


@router.post(f"{_PREFIX}/{{source}}/table-definitions/import")
async def import_table_definitions(
    request: Request,
    body: TableDefinitionImportRequest,
    source: str = _SourcePath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """테이블 정의 YAML을 가져와 결정적 검증 뒤 자산 초안을 만든다(검증 실패는 오류 행)."""
    result = await _call(
        service.import_table_definitions(source, body.text, by=_admin.get("sub"))
    )
    audit_logged = await _audit(
        request, _admin, "import_table_definitions", source=source,
        draft_id=result.get("draft_id"), summary=result.get("summary"), chars=len(body.text),
    )
    return _with_audit(result, audit_logged)


@router.get(f"{_PREFIX}/{{source}}/asset-drafts/{{draft_id}}/table-definitions/estimate")
async def estimate_table_definition_llm(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    only_failed: bool = Query(False, description="실패 묶음만 다시 실행할 때의 예상"),
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """테이블 정의 LLM 묶음 초안의 예상 호출 수(실행 전 표시 · LLM 0)."""
    result = await _call(
        service.estimate_table_definition_llm(source, draft_id, only_failed=only_failed)
    )
    audit_logged = await _audit(
        request, _admin, "estimate_table_definition_llm", source=source, draft_id=draft_id,
        only_failed=only_failed, calls=result.get("calls"),
    )
    return _with_audit(result, audit_logged)


@router.post(
    f"{_PREFIX}/{{source}}/asset-drafts/{{draft_id}}/table-definitions/llm", status_code=202
)
async def start_table_definition_llm(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    body: TableDefinitionLlmRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
    runner: Any = Depends(get_admin_job_runner),
) -> dict[str, Any]:
    """테이블 정의 LLM 묶음 초안 잡을 시작한다(주석 없는 테이블만 · 실패 묶음만 재실행 가능)."""
    by = _admin.get("sub")
    only_failed = bool(body.only_failed) if body else False
    job = await _call(runner.start(
        kind="asset_table_definitions", db_id=source, by=by,
        params={"draft_id": draft_id, "only_failed": only_failed},
        work=lambda ctx: service.run_table_definition_llm(
            source, draft_id, only_failed=only_failed, by=by, ctx=ctx,
        ),
    ))
    audit_logged = await _audit(
        request, _admin, "asset_table_definitions", source=source, draft_id=draft_id,
        only_failed=only_failed, job_id=job.get("job_id"),
    )
    return _with_audit(job, audit_logged)


@router.put(f"{_PREFIX}/{{source}}/asset-drafts/{{draft_id}}/table-definitions")
async def update_table_definitions(
    request: Request,
    body: TableDefinitionEditRequest,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """검토 화면 편집을 저장한다 — 고친 행은 출처 `manual`로 다시 검증한다(실패 시 422)."""
    edits = {table: edit.model_dump(exclude_unset=True) for table, edit in body.edits.items()}
    result = await _call(
        service.update_table_definitions(source, draft_id, edits, by=_admin.get("sub"))
    )
    audit_logged = await _audit(
        request, _admin, "update_table_definitions", source=source, draft_id=draft_id,
        tables=sorted(edits),
    )
    return _with_audit(result, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/asset-drafts/{{draft_id}}/reject")
async def reject_asset_draft(
    request: Request,
    source: str = _SourcePath,
    draft_id: str = _DraftPath,
    body: ReasonRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """자산 초안을 반려한다."""
    reason = body.reason if body else ""
    result = await _call(
        service.reject_asset_draft(source, draft_id, by=_admin.get("sub"), reason=reason)
    )
    audit_logged = await _audit(
        request, _admin, "reject_asset_draft", source=source, draft_id=draft_id, reason=reason,
    )
    return _with_audit(result, audit_logged)


@router.post(f"{_PREFIX}/{{source}}/assets/{{kind}}/versions/{{ver}}/rollback")
async def rollback_asset(
    request: Request,
    kind: _AssetFileKind,
    source: str = _SourcePath,
    ver: int = Path(..., ge=0, description="되돌릴 버전 번호"),
    body: ReasonRequest | None = None,
    _admin: dict[str, Any] = Depends(require_admin_user),
    service: Any = Depends(get_asset_service),
) -> dict[str, Any]:
    """유사어 시드 · DB 전용 규칙 섹션 파일을 이전 버전 원문으로 되돌린다."""
    reason = body.reason if body else ""
    result = await _call(
        service.rollback_asset(source, kind, ver, by=_admin.get("sub"), reason=reason)
    )
    audit_logged = await _audit(
        request, _admin, "rollback_asset", source=source, kind=kind, ver=ver, reason=reason,
        new_ver=(result.get("version") or {}).get("ver"),
    )
    return _with_audit(result, audit_logged)
