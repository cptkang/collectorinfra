"""설정 리로드 라우트 — `POST /admin/settings/reload` (plans/166 A7 — `admin.py`에서 이동).

`admin.router`가 이 라우터를 원래 선언 위치에 포함한다(경로·이름·OpenAPI 불변). 옛 경로
`src.api.routes.admin`에서 `reload_settings`·`SettingsReloadResponse`를 재노출한다(D-337 ①).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.admin_audit import log_admin_event as _log_settings_event
from src.api.dependencies import require_admin_user
from src.api.error_detail import admin_error_detail
from src.api.settings_catalog import diff_effective_keys, field_index

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 A7)
logger = logging.getLogger("src.api.routes.admin")
router = APIRouter()


class SettingsReloadResponse(BaseModel):
    """설정 리로드(`POST /admin/settings/reload`) 응답."""

    reloaded: bool
    changed_keys: list[str] = Field(
        default_factory=list, description="리로드로 실효값이 바뀐 키(값은 노출하지 않음)"
    )
    restart_only_keys: list[str] = Field(
        default_factory=list,
        description="실효값은 바뀌었으나 소비처가 기동 캡처라 재시작해야 반영되는 키",
    )
    graph_rebuilt: bool = False
    message: str


@router.post(
    "/admin/settings/reload",
    response_model=SettingsReloadResponse,
)
async def reload_settings(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> SettingsReloadResponse:
    """저장된 설정을 서버 재시작 없이 반영한다 (Plan 68 §6 Phase 4).

    처리 순서:
    1. fresh `AppConfig` 로드(실패 시 400 — 기존 상태 유지)
    2. 실효값 diff(키 이름만 — 값 미노출)
    3. 로그 레벨 변경 시 `setup_logging` 재적용
    4. 그래프 재빌드(기동 시 체크포인터 재사용 — 대화 이력 보존, 실패 시 500·기존 유지)
    5. 스키마 캐시·임베더 싱글톤 리셋(+ redis 백엔드면 재연결)
    6. `app.state.graph`/`app.state.config` 원자 교체 — 처리 중 요청은 옛 객체로 완주
    7. 감사 로그(SETTINGS_RELOAD)

    빌드 시점 외부 호출은 deepagents 경로 활성 시 vLLM `/models` health GET 1회뿐
    (로컬·비과금 — D-127 저촉 없음). AlarmWorker 등 기동 캡처 소비처는 반영되지 않으며
    해당 키는 `restart_only_keys`로 응답에 명시한다.
    """
    from fastapi.concurrency import run_in_threadpool

    from src.config import load_config
    from src.graph import build_graph
    from src.security.audit_logger import setup_logging

    old_config = request.app.state.config
    index = field_index()

    load_config.cache_clear()
    try:
        fresh = load_config()
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=admin_error_detail("설정 로드", e, logger, request)
            + " · 기존 설정이 유지됩니다",
        )

    # 개발 모드(JWT 시크릿 미설정)에서는 AppConfig 재생성이 시크릿을 재추첨해 기존 발급
    # 토큰이 전량 무효화된다(리로드 직후 운영자 세션 즉시 로그아웃). 명시 설정이 아니면
    # 실행 중이던 시크릿을 승계한다(운영 모드는 .encenv 고정이라 영향 없음).
    if not fresh.admin._jwt_secret_explicit and old_config.admin.jwt_secret:
        fresh.admin.jwt_secret = old_config.admin.jwt_secret
    if not fresh.auth._jwt_secret_explicit and old_config.auth.jwt_secret:
        fresh.auth.jwt_secret = old_config.auth.jwt_secret

    # 기동 시 필수 크레덴셜 게이트(D-071)를 리로드 시점에도 동일하게 강제한다 —
    # 게이트를 통과하지 못하는 설정이 재시작 없이 실서비스에 적용되는 것을 차단.
    from src.api.server import _validate_production_secrets

    try:
        _validate_production_secrets(fresh)
    except RuntimeError as e:
        logger.error("설정 리로드 거부(운영 게이트) — 기존 설정 유지: %s", e)
        raise HTTPException(
            status_code=400,
            detail=f"운영 모드 필수 설정 검증에 실패했습니다 — 기존 설정이 유지됩니다: {e}",
        )

    changed_keys = diff_effective_keys(old_config, fresh)
    restart_only_keys = [
        key for key in changed_keys
        if (spec := index.get(key)) is not None and spec.apply_mode == "restart"
    ]

    if old_config.log_level != fresh.log_level:
        setup_logging(fresh.log_level)

    # 그래프 재빌드 — 동기 빌드이므로 스레드풀에서 실행(이벤트 루프 블로킹 방지).
    # 체크포인터를 넘기지 않으면 build_graph가 동기 SqliteSaver를 새로 만들므로 반드시 재사용한다.
    checkpointer = getattr(request.app.state, "checkpointer", None)
    if checkpointer is None:
        checkpointer = getattr(request.app.state.graph, "checkpointer", None)
    try:
        new_graph = await run_in_threadpool(
            build_graph, fresh, checkpointer=checkpointer
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=admin_error_detail("그래프 재빌드", e, logger, request)
            + " · 기존 설정이 유지됩니다",
        )

    # 싱글톤 리셋 — 옛 redis 연결은 닫고(자원 누수 방지), 다음 접근부터 fresh 설정으로 재생성.
    from src.schema_cache import cache_manager as cache_manager_module
    from src.schema_cache import query_history as query_history_module
    from src.schema_cache.synonym_semantic import reset_embedder_state

    old_manager = cache_manager_module._cache_manager
    if old_manager is not None:
        try:
            await old_manager.disconnect()
        except Exception as e:
            logger.warning("리로드 중 기존 스키마 캐시 연결 종료 실패(계속 진행): %s", e)
    cache_manager_module.reset_cache_manager()

    old_history_store = query_history_module._store
    if old_history_store is not None:
        try:
            await old_history_store.disconnect()
        except Exception as e:
            logger.warning("리로드 중 기존 질의 이력 연결 종료 실패(계속 진행): %s", e)
    query_history_module.reset_query_history_store()

    reset_embedder_state()

    # 원자 교체(참조 대입) — 이 시점 이후의 요청부터 새 설정·그래프를 본다.
    request.app.state.graph = new_graph
    request.app.state.config = fresh

    # lifespan과 동일하게 redis 백엔드는 즉시 연결을 확인한다(실패 시 파일 캐시 폴백).
    if fresh.schema_cache.backend == "redis":
        try:
            await cache_manager_module.get_cache_manager(fresh).ensure_redis_connected()
        except Exception as e:
            logger.warning("리로드 후 Redis 스키마 캐시 연결 실패 (파일 캐시 폴백): %s", e)

    from src.domain.audit import AuditEvent

    audit_ok = await _log_settings_event(
        request,
        _admin.get("sub"),
        AuditEvent.SETTINGS_RELOAD.value,
        {
            "changed_keys": changed_keys,
            "restart_only_keys": restart_only_keys,
            "graph_rebuilt": True,
        },
    )

    logger.info(
        "설정 리로드 완료: 변경 %d건(재시작 필요 %d건) (by %s)",
        len(changed_keys), len(restart_only_keys), _admin.get("sub"),
    )

    applied = len(changed_keys) - len(restart_only_keys)
    if not changed_keys:
        message = "설정을 리로드했습니다 — 실효값 변경은 없습니다."
    elif restart_only_keys:
        message = (
            f"설정을 리로드했습니다 — {applied}건 반영, "
            f"{len(restart_only_keys)}건은 서버 재시작이 필요합니다."
        )
    else:
        message = f"설정을 리로드했습니다 — {applied}건이 반영되었습니다."
    # 알람 워커는 기동 캡처 config로 동작한다(§6.2 비대칭) — 워커가 함께 소비하는
    # 키가 바뀐 경우 질의 경로만 반영됐음을 명시한다(침묵 금지).
    worker_shared_changed = [
        key for key in changed_keys
        if key.startswith(("LLM_", "DBHUB_")) or key == "ACTIVE_DB_IDS"
    ]
    if fresh.alarm.enabled and worker_shared_changed:
        message += (
            " 알람 워커는 재시작 전까지 이전 LLM/DB 설정으로 동작합니다: "
            + ", ".join(worker_shared_changed)
        )
    if not audit_ok:
        message += " (감사 기록 불가 — 감사 저장소 미구성)"

    return SettingsReloadResponse(
        reloaded=True,
        changed_keys=changed_keys,
        restart_only_keys=restart_only_keys,
        graph_rebuilt=True,
        message=message,
    )
