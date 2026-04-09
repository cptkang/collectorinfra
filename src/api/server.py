"""FastAPI 애플리케이션 설정.

애플리케이션 생성, 미들웨어 설정, 라우트 등록, 라이프사이클 관리를 수행한다.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import admin, admin_auth, conversation, health, query, schema_cache, user_auth
from src.config import AppConfig, load_config
from src.graph import build_graph
from src.security.audit_logger import setup_logging

logger = logging.getLogger(__name__)

_AUTH_DDL = """
CREATE TABLE IF NOT EXISTS auth_users (
    user_id         VARCHAR(50) PRIMARY KEY,
    username        VARCHAR(100) NOT NULL,
    hashed_password VARCHAR(256) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user',
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    department      VARCHAR(100),
    allowed_db_ids  TEXT[],
    auth_method     VARCHAR(20) NOT NULL DEFAULT 'local',
    login_fail_count INTEGER NOT NULL DEFAULT 0,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,
    user_id         VARCHAR(50),
    detail          JSONB,
    ip_address      VARCHAR(45),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


async def _ensure_auth_tables(pool) -> None:
    """인증/감사 테이블이 없으면 생성한다."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(_AUTH_DDL)
        # 인덱스는 IF NOT EXISTS로 별도 실행
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_user_id ON audit_logs(user_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_event_type ON audit_logs(event_type)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at DESC)"
            )
    except Exception as e:
        logger.warning("인증 테이블 DDL 실행 실패: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """앱 시작/종료 시 실행되는 라이프사이클 관리자.

    시작 시: 로깅 설정, 그래프 빌드, 설정 공유
    종료 시: 리소스 정리

    Args:
        app: FastAPI 앱 인스턴스

    Yields:
        None
    """
    # 시작 시
    config = load_config()
    setup_logging(config.log_level)

    # SQL 파일 로거 초기화
    from src.utils.sql_file_logger import init_sql_file_logger
    init_sql_file_logger()

    from src.graph import _create_checkpointer_async

    checkpointer = await _create_checkpointer_async(config)
    app.state.graph = build_graph(config, checkpointer=checkpointer)
    app.state.config = config
    logger.info("에이전트 그래프 빌드 완료")

    # 인증 DB 초기화 (AUTH_ENABLED 여부와 무관하게 테이블은 생성)
    auth_db_url = config.auth.auth_db_url or config.db_connection_string
    app.state.auth_pool = None
    app.state.user_repo = None
    app.state.audit_repo = None
    app.state.auth_provider = None
    app.state.audit_service = None  # lifespan 이전 기본값

    if auth_db_url:
        try:
            import asyncpg

            from src.infrastructure.audit_repository import PostgresAuditRepository
            from src.infrastructure.auth_provider import LocalAuthProvider
            from src.infrastructure.user_repository import PostgresUserRepository

            auth_pool = await asyncpg.create_pool(
                auth_db_url, min_size=1, max_size=5
            )
            app.state.auth_pool = auth_pool
            app.state.user_repo = PostgresUserRepository(auth_pool)
            app.state.audit_repo = PostgresAuditRepository(auth_pool)

            from src.security.audit_service import AuditService

            app.state.audit_service = AuditService(
                config=config.audit,
                audit_repo=app.state.audit_repo,
            )
            app.state.auth_provider = LocalAuthProvider(app.state.user_repo)

            # DDL 자동 실행 (테이블이 없으면 생성)
            await _ensure_auth_tables(auth_pool)
            logger.info("인증 DB 초기화 완료")
        except Exception as e:
            logger.warning("인증 DB 초기화 실패 (인증 기능 비활성): %s", e)

    # Redis 연결 (스키마 캐시)
    if config.schema_cache.backend == "redis":
        from src.schema_cache.cache_manager import get_cache_manager
        cache_mgr = get_cache_manager(config)
        try:
            await cache_mgr.ensure_redis_connected()
            logger.info("Redis 스키마 캐시 연결 완료")
        except Exception as e:
            logger.warning("Redis 연결 실패 (파일 캐시로 폴백): %s", e)

    yield

    # 종료 시: 인증 DB 풀 정리
    if app.state.auth_pool:
        try:
            await app.state.auth_pool.close()
        except Exception:
            pass

    # 종료 시: 체크포인터 연결 정리
    if hasattr(checkpointer, "conn") and hasattr(checkpointer.conn, "close"):
        try:
            await checkpointer.conn.close()
        except Exception:
            pass

    if config.schema_cache.backend == "redis":
        from src.schema_cache.cache_manager import get_cache_manager
        cache_mgr = get_cache_manager(config)
        await cache_mgr.disconnect()

    logger.info("서버 종료")


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    """FastAPI 앱을 생성한다.

    Args:
        config: 애플리케이션 설정 (없으면 내부에서 로드)

    Returns:
        구성된 FastAPI 인스턴스
    """
    if config is None:
        config = load_config()

    application = FastAPI(
        title="인프라 데이터 조회 에이전트",
        description=(
            "자연어로 인프라 데이터를 조회하고 "
            "문서를 생성하는 AI 에이전트 API"
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # 설정을 app.state에 저장 (lifespan 이전에도 접근 가능하도록)
    application.state.config = config

    # CORS 설정 (환경변수에서 제어)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 감사 미들웨어 (요청별 request_id, client_ip 자동 설정)
    from src.api.middleware.audit_middleware import AuditMiddleware

    application.add_middleware(AuditMiddleware)

    # 라우트 등록
    application.include_router(health.router, prefix="/api/v1", tags=["health"])
    application.include_router(query.router, prefix="/api/v1", tags=["query"])
    application.include_router(
        admin_auth.router, prefix="/api/v1", tags=["admin-auth"]
    )
    application.include_router(admin.router, prefix="/api/v1", tags=["admin"])
    application.include_router(
        schema_cache.router, prefix="/api/v1", tags=["schema-cache"]
    )
    application.include_router(
        conversation.router, prefix="/api/v1", tags=["conversation"]
    )
    application.include_router(
        user_auth.router, prefix="/api/v1", tags=["user-auth"]
    )

    # 정적 파일 디렉토리
    static_dir = Path(__file__).resolve().parent.parent / "static"

    # HTML 페이지 라우트
    @application.get("/", include_in_schema=False)
    async def user_page() -> FileResponse:
        """사용자 메인 화면."""
        return FileResponse(static_dir / "index.html")

    @application.get("/login", include_in_schema=False)
    async def user_login_page() -> FileResponse:
        """사용자 로그인 화면."""
        return FileResponse(static_dir / "login.html")

    @application.get("/register", include_in_schema=False)
    async def user_register_page() -> FileResponse:
        """사용자 가입 화면."""
        return FileResponse(static_dir / "register.html")

    @application.get("/admin/login", include_in_schema=False)
    async def admin_login_page() -> FileResponse:
        """운영자 로그인 화면."""
        return FileResponse(static_dir / "admin" / "login.html")

    @application.get("/admin", include_in_schema=False)
    async def admin_dashboard_page() -> FileResponse:
        """운영자 대시보드 화면."""
        return FileResponse(static_dir / "admin" / "dashboard.html")

    # 정적 파일 서빙 (라우트 등록 후에 마운트해야 우선순위 보장)
    if static_dir.exists():
        application.mount(
            "/static",
            StaticFiles(directory=str(static_dir)),
            name="static",
        )

    return application


# 실행 진입점
app = create_app()
