"""스키마 캐시 운영자 관리 라우터.

스키마 캐시 생성/갱신/조회/삭제, 컬럼 설명 생성, 유사 단어 관리 API를 제공한다.
변경 작업은 `CACHE_OPERATION` 감사 로그를 남기고 응답에 `audit_logged`를 싣는다(plans/104 S4).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.api.admin_audit import log_admin_event
from src.api.dependencies import require_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()


async def _audit_cache_operation(
    request: Request, admin: dict[str, Any], action: str, **extra: object
) -> bool:
    """스키마 캐시 변경 작업을 `CACHE_OPERATION`으로 감사 기록한다(plans/104 S4).

    Returns:
        기록 성공 여부(응답 `audit_logged`)
    """
    from src.domain.audit import AuditEvent

    return await log_admin_event(
        request,
        admin.get("sub"),
        AuditEvent.CACHE_OPERATION.value,
        {"action": action, **extra},
    )


# === 요청/응답 모델 ===


class CacheGenerateRequest(BaseModel):
    """캐시 생성/갱신 요청."""

    db_ids: Optional[list[str]] = Field(
        None, description="대상 DB 식별자 목록 (null이면 전체)"
    )
    include_descriptions: bool = Field(
        True, description="LLM 설명 동시 생성 여부"
    )
    force: bool = Field(False, description="강제 갱신 (fingerprint 무시)")


class CacheGenerateResultItem(BaseModel):
    """개별 DB 캐시 갱신 결과."""

    db_id: str
    status: str
    table_count: int = 0
    fingerprint: str = ""
    description_status: str = "pending"
    message: str = ""


class CacheGenerateResponse(BaseModel):
    """캐시 생성/갱신 응답."""

    results: list[CacheGenerateResultItem]
    audit_logged: bool = False


class CacheStatusItem(BaseModel):
    """개별 DB 캐시 상태."""

    db_id: str
    fingerprint: str = ""
    cached_at: str = ""
    table_count: int = 0
    description_status: str = "pending"
    description_count: int = 0
    synonym_count: int = 0
    backend: str = "none"


class CacheStatusResponse(BaseModel):
    """캐시 상태 응답."""

    caches: list[CacheStatusItem]
    redis_connected: bool


class SynonymListResponse(BaseModel):
    """유사 단어 목록 응답."""

    db_id: str
    synonyms: dict[str, list[str]]


class SynonymGenerateRequest(BaseModel):
    """유사 단어 생성 요청."""

    column: Optional[str] = Field(
        None, description="특정 컬럼 (table.column), null이면 전체"
    )


class SynonymGenerateResponse(BaseModel):
    """유사 단어 생성 응답."""

    db_id: str
    generated_count: int
    message: str
    audit_logged: bool = False


# === 캐시 관리 엔드포인트 ===


@router.post(
    "/admin/schema-cache/generate",
    response_model=CacheGenerateResponse,
)
async def generate_cache(
    request: Request,
    body: CacheGenerateRequest,
    _admin: dict = Depends(require_admin_user),
) -> CacheGenerateResponse:
    """스키마 캐시를 생성/갱신한다.

    Args:
        request: FastAPI Request(감사 기록용)
        body: 생성 요청
        _username: 인증된 관리자

    Returns:
        DB별 갱신 결과
    """
    from src.config import load_config
    from src.db import get_db_client
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    results: list[CacheGenerateResultItem] = []

    db_ids = body.db_ids or config.multi_db.get_active_db_ids()
    if not db_ids:
        db_ids = ["_default"]

    for db_id in db_ids:
        try:
            async with get_db_client(config, db_id=db_id) as client:
                refresh_result = await cache_mgr.refresh_cache(
                    db_id, client, force=body.force
                )
                results.append(CacheGenerateResultItem(
                    db_id=refresh_result.db_id,
                    status=refresh_result.status,
                    table_count=refresh_result.table_count,
                    fingerprint=refresh_result.fingerprint,
                    description_status=refresh_result.description_status,
                    message=refresh_result.message,
                ))

                # 설명 생성 (요청 시)
                if (
                    body.include_descriptions
                    and refresh_result.status in ("created", "updated")
                ):
                    try:
                        await _generate_descriptions_for_db(
                            db_id, cache_mgr, config
                        )
                    except Exception as e:
                        logger.warning(
                            "설명 생성 실패 (db_id=%s): %s", db_id, e
                        )

        except Exception as e:
            results.append(CacheGenerateResultItem(
                db_id=db_id,
                status="error",
                message=str(e),
            ))

    audit_logged = await _audit_cache_operation(
        request, _admin, "generate",
        db_ids=db_ids,
        force=body.force,
        include_descriptions=body.include_descriptions,
        results=[{"db_id": r.db_id, "status": r.status} for r in results],
    )
    return CacheGenerateResponse(results=results, audit_logged=audit_logged)


@router.post(
    "/admin/schema-cache/generate-descriptions",
    response_model=CacheGenerateResponse,
)
async def generate_descriptions(
    request: Request,
    body: CacheGenerateRequest,
    _admin: dict = Depends(require_admin_user),
) -> CacheGenerateResponse:
    """컬럼 설명을 (재)생성한다.

    Args:
        request: FastAPI Request(감사 기록용)
        body: 생성 요청
        _username: 인증된 관리자

    Returns:
        DB별 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    results: list[CacheGenerateResultItem] = []

    db_ids = body.db_ids or config.multi_db.get_active_db_ids()
    if not db_ids:
        db_ids = ["_default"]

    for db_id in db_ids:
        try:
            desc_count = await _generate_descriptions_for_db(
                db_id, cache_mgr, config
            )
            results.append(CacheGenerateResultItem(
                db_id=db_id,
                status="updated",
                description_status="complete",
                message=f"설명 {desc_count}개 생성 완료",
            ))
        except Exception as e:
            results.append(CacheGenerateResultItem(
                db_id=db_id,
                status="error",
                message=str(e),
            ))

    audit_logged = await _audit_cache_operation(
        request, _admin, "generate_descriptions",
        db_ids=db_ids,
        results=[{"db_id": r.db_id, "status": r.status} for r in results],
    )
    return CacheGenerateResponse(results=results, audit_logged=audit_logged)


@router.get(
    "/admin/schema-cache/status",
    response_model=CacheStatusResponse,
)
async def get_cache_status(
    _admin: dict = Depends(require_admin_user),
) -> CacheStatusResponse:
    """전체 캐시 상태를 조회한다.

    Args:
        _username: 인증된 관리자

    Returns:
        캐시 상태 목록
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    statuses = await cache_mgr.get_all_status()
    caches = [
        CacheStatusItem(
            db_id=s.db_id,
            fingerprint=s.fingerprint,
            cached_at=s.cached_at,
            table_count=s.table_count,
            description_status=s.description_status,
            description_count=s.description_count,
            synonym_count=s.synonym_count,
            backend=s.backend,
        )
        for s in statuses
    ]

    return CacheStatusResponse(
        caches=caches,
        redis_connected=cache_mgr.redis_available,
    )


# === DB 설명 관리 엔드포인트 ===
# (plans/104 S3) 고정 경로 `db-descriptions*`는 `/admin/schema-cache/{db_id}` 경로보다
# **먼저** 선언한다 — 뒤에 두면 `GET …/db-descriptions`가 `{db_id}="db-descriptions"`로 잡힌다.


class DBDescriptionListResponse(BaseModel):
    """DB 설명 목록 응답."""

    descriptions: dict[str, str]


class DBDescriptionSetRequest(BaseModel):
    """DB 설명 설정 요청."""

    description: str = Field(..., description="DB 설명 (한국어)")


class DBDescriptionGenerateResponse(BaseModel):
    """DB 설명 생성 응답."""

    results: dict[str, str]
    message: str
    audit_logged: bool = False

# 수동 설정(출처 manual) 설명은 LLM 생성이 건너뛴다 — 결과 표기(plans/104 S5 · R10)
_MANUAL_PRESERVED = "(수동 설명 보존)"


@router.get(
    "/admin/schema-cache/db-descriptions",
    response_model=DBDescriptionListResponse,
)
async def get_db_descriptions(
    _admin: dict = Depends(require_admin_user),
) -> DBDescriptionListResponse:
    """모든 DB 설명을 조회한다.

    Args:
        _username: 인증된 관리자

    Returns:
        DB 설명 목록
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    descriptions = await cache_mgr.get_db_descriptions()

    return DBDescriptionListResponse(descriptions=descriptions)


@router.get(
    "/admin/schema-cache/db-descriptions/{db_id}",
)
async def get_db_description(
    db_id: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """특정 DB의 설명을 조회한다.

    Args:
        db_id: DB 식별자
        _username: 인증된 관리자

    Returns:
        DB 설명
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    description = await cache_mgr.get_db_description(db_id)

    if description is None:
        raise HTTPException(
            status_code=404,
            detail=f"DB 설명이 존재하지 않습니다: {db_id}",
        )

    return {"db_id": db_id, "description": description}


@router.put(
    "/admin/schema-cache/db-descriptions/{db_id}",
)
async def set_db_description(
    request: Request,
    db_id: str,
    body: DBDescriptionSetRequest,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """특정 DB의 설명을 수동 설정한다.

    출처 `manual`로 저장하므로 LLM 재생성 시에도 보존된다(plans/104 S5).

    Args:
        request: FastAPI Request(감사 기록용)
        db_id: DB 식별자
        body: 설명 설정 요청
        _username: 인증된 관리자

    Returns:
        설정 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    success = await cache_mgr.save_db_description(db_id, body.description, origin="manual")

    audit_logged = await _audit_cache_operation(
        request, _admin, "set_db_description",
        db_id=db_id, origin="manual", saved=success,
    )
    return {
        "db_id": db_id,
        "description": body.description,
        "saved": success,
        "audit_logged": audit_logged,
    }


@router.delete(
    "/admin/schema-cache/db-descriptions/{db_id}",
)
async def delete_db_description(
    request: Request,
    db_id: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """특정 DB의 설명을 삭제한다.

    Args:
        request: FastAPI Request(감사 기록용)
        db_id: DB 식별자
        _username: 인증된 관리자

    Returns:
        삭제 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    success = await cache_mgr.delete_db_description(db_id)

    audit_logged = await _audit_cache_operation(
        request, _admin, "delete_db_description", db_id=db_id, deleted=success,
    )
    return {"db_id": db_id, "deleted": success, "audit_logged": audit_logged}


@router.post(
    "/admin/schema-cache/db-descriptions/generate",
    response_model=DBDescriptionGenerateResponse,
)
async def generate_db_descriptions(
    request: Request,
    body: CacheGenerateRequest | None = None,
    _admin: dict = Depends(require_admin_user),
) -> DBDescriptionGenerateResponse:
    """LLM으로 DB 설명을 자동 생성한다.

    출처가 `manual`인 DB는 LLM을 호출하지 않고 결과에 "(수동 설명 보존)"을 남긴다.
    생성값은 출처 `llm`으로 저장한다(plans/104 S5 · R10).

    Args:
        request: FastAPI Request(감사 기록용)
        body: 생성 요청 (db_ids 지정 가능, None이면 전체)
        _username: 인증된 관리자

    Returns:
        생성 결과
    """
    from src.config import load_config
    from src.llm import create_llm
    from src.schema_cache.cache_manager import get_cache_manager
    from src.schema_cache.description_generator import DescriptionGenerator

    config = load_config()
    cache_mgr = get_cache_manager(config)
    generator: DescriptionGenerator | None = None

    db_ids = (body.db_ids if body and body.db_ids else None) or config.multi_db.get_active_db_ids()
    if not db_ids:
        # 캐시된 DB 목록에서 가져오기
        statuses = await cache_mgr.get_all_status()
        db_ids = [s.db_id for s in statuses if s.backend != "none"]

    results: dict[str, str] = {}
    for db_id in db_ids:
        if await cache_mgr.get_db_description_origin(db_id) == "manual":
            results[db_id] = _MANUAL_PRESERVED
            continue

        schema_dict = await cache_mgr.get_schema(db_id)
        if schema_dict is None:
            results[db_id] = "(캐시 없음)"
            continue

        if generator is None:
            generator = DescriptionGenerator(create_llm(config))
        description = await generator.generate_db_description(db_id, schema_dict)
        if not description:
            results[db_id] = "(생성 실패)"
        elif await cache_mgr.save_db_description(db_id, description, origin="llm"):
            results[db_id] = description
        else:
            # 생성 도중 수동 설명이 저장됐거나(보존 규칙) 저장소 기록에 실패했다
            results[db_id] = "(저장 안 됨 — 수동 설명 보존 또는 저장 실패)"

    audit_logged = await _audit_cache_operation(
        request, _admin, "generate_db_descriptions",
        db_ids=db_ids,
        results={
            db_id: ("generated" if not value.startswith("(") else value)
            for db_id, value in results.items()
        },
    )
    return DBDescriptionGenerateResponse(
        results=results,
        message=f"DB 설명 {len([v for v in results.values() if not v.startswith('(')])}개 생성 완료",
        audit_logged=audit_logged,
    )


@router.get(
    "/admin/schema-cache/{db_id}",
)
async def get_cache_detail(
    db_id: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """특정 DB 캐시 상세를 조회한다.

    Args:
        db_id: DB 식별자
        _username: 인증된 관리자

    Returns:
        캐시 상세 정보
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    schema_dict = await cache_mgr.get_schema(db_id)
    descriptions = await cache_mgr.get_descriptions(db_id)
    synonyms = await cache_mgr.get_synonyms(db_id)
    status = await cache_mgr.get_status(db_id)

    if schema_dict is None:
        raise HTTPException(status_code=404, detail=f"캐시가 존재하지 않습니다: {db_id}")

    return {
        "db_id": db_id,
        "status": {
            "fingerprint": status.fingerprint,
            "cached_at": status.cached_at,
            "table_count": status.table_count,
            "backend": status.backend,
        },
        "tables": schema_dict.get("tables", {}),
        "relationships": schema_dict.get("relationships", []),
        "descriptions": descriptions,
        "synonyms": synonyms,
    }


@router.delete("/admin/schema-cache/{db_id}")
async def delete_cache(
    request: Request,
    db_id: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """특정 DB 캐시를 삭제한다.

    Args:
        request: FastAPI Request(감사 기록용)
        db_id: DB 식별자
        _username: 인증된 관리자

    Returns:
        삭제 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    success = await cache_mgr.invalidate(db_id)
    audit_logged = await _audit_cache_operation(
        request, _admin, "invalidate", db_id=db_id, deleted=success,
    )
    return {
        "db_id": db_id,
        "deleted": success,
        "message": f"캐시 삭제 {'성공' if success else '실패'}: {db_id}",
        "audit_logged": audit_logged,
    }


@router.delete("/admin/schema-cache")
async def delete_all_caches(
    request: Request,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """전체 캐시를 삭제한다.

    Args:
        request: FastAPI Request(감사 기록용)
        _username: 인증된 관리자

    Returns:
        삭제 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    count = await cache_mgr.invalidate_all()
    audit_logged = await _audit_cache_operation(
        request, _admin, "invalidate_all", deleted_count=count,
    )
    return {
        "deleted_count": count,
        "message": f"전체 캐시 {count}개 삭제 완료",
        "audit_logged": audit_logged,
    }


# === 유사 단어 관리 엔드포인트 ===


@router.get(
    "/admin/schema-cache/{db_id}/synonyms",
    response_model=SynonymListResponse,
)
async def get_synonyms(
    db_id: str,
    _admin: dict = Depends(require_admin_user),
) -> SynonymListResponse:
    """유사 단어 목록을 조회한다.

    Args:
        db_id: DB 식별자
        _username: 인증된 관리자

    Returns:
        유사 단어 목록
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)
    synonyms = await cache_mgr.get_synonyms(db_id)

    return SynonymListResponse(db_id=db_id, synonyms=synonyms)


@router.post(
    "/admin/schema-cache/{db_id}/synonyms/generate",
    response_model=SynonymGenerateResponse,
)
async def generate_synonyms(
    request: Request,
    db_id: str,
    body: SynonymGenerateRequest | None = None,
    _admin: dict = Depends(require_admin_user),
) -> SynonymGenerateResponse:
    """LLM으로 유사 단어를 자동 생성한다.

    Args:
        request: FastAPI Request(감사 기록용)
        db_id: DB 식별자
        body: 생성 요청 (선택)
        _username: 인증된 관리자

    Returns:
        생성 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    count = await _generate_descriptions_for_db(db_id, cache_mgr, config)
    audit_logged = await _audit_cache_operation(
        request, _admin, "generate_synonyms", db_id=db_id, generated_count=count,
    )
    return SynonymGenerateResponse(
        db_id=db_id,
        generated_count=count,
        message=f"유사 단어 {count}개 컬럼 생성 완료",
        audit_logged=audit_logged,
    )


@router.delete("/admin/schema-cache/{db_id}/synonyms/{column}")
async def delete_column_synonyms(
    request: Request,
    db_id: str,
    column: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """특정 컬럼의 유사 단어를 삭제한다.

    기존 단어 목록을 모두 제거한다.

    Args:
        request: FastAPI Request(감사 기록용)
        db_id: DB 식별자
        column: table.column 형식
        _username: 인증된 관리자

    Returns:
        삭제 결과
    """
    from src.config import load_config
    from src.schema_cache.cache_manager import get_cache_manager

    config = load_config()
    cache_mgr = get_cache_manager(config)

    # 현재 유사 단어 조회 후 전체 삭제
    current_synonyms = await cache_mgr.get_synonyms(db_id)
    words_to_remove = current_synonyms.get(column, [])
    success = False
    if words_to_remove:
        success = await cache_mgr.remove_synonyms(db_id, column, words_to_remove)
    audit_logged = await _audit_cache_operation(
        request, _admin, "delete_column_synonyms",
        db_id=db_id, column=column, removed_count=len(words_to_remove), deleted=success,
    )
    if words_to_remove:
        return {
            "db_id": db_id, "column": column, "deleted": success, "audit_logged": audit_logged,
        }

    return {
        "db_id": db_id, "column": column, "deleted": False, "message": "유사 단어 없음",
        "audit_logged": audit_logged,
    }


# === 내부 헬퍼 ===


async def _generate_descriptions_for_db(
    db_id: str,
    cache_mgr: Any,
    config: Any,
) -> int:
    """특정 DB의 컬럼 설명 + 유사 단어를 생성한다.

    Args:
        db_id: DB 식별자
        cache_mgr: SchemaCacheManager 인스턴스
        config: AppConfig

    Returns:
        생성된 설명 수
    """
    from src.llm import create_llm
    from src.schema_cache.description_generator import DescriptionGenerator

    schema_dict = await cache_mgr.get_schema(db_id)
    if schema_dict is None:
        raise HTTPException(
            status_code=404,
            detail=f"캐시가 존재하지 않습니다: {db_id}. 먼저 캐시를 생성하세요.",
        )

    llm = create_llm(config)
    generator = DescriptionGenerator(llm)
    descriptions, synonyms = await generator.generate_for_db(schema_dict)

    await cache_mgr.save_descriptions(db_id, descriptions)
    await cache_mgr.save_synonyms(db_id, synonyms)

    return len(descriptions)


# 타입 임포트 (lazy)
from typing import Any
