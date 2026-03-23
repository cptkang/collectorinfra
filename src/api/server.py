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

from src.api.routes import admin, admin_auth, conversation, health, query, schema_cache
from src.config import AppConfig, load_config
from src.graph import build_graph
from src.security.audit_logger import setup_logging

logger = logging.getLogger(__name__)


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
    setup_logging()
    config = load_config()

    app.state.graph = build_graph(config)
    app.state.config = config
    logger.info("에이전트 그래프 빌드 완료")

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

    # 종료 시
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

    # 정적 파일 디렉토리
    static_dir = Path(__file__).resolve().parent.parent / "static"

    # HTML 페이지 라우트
    @application.get("/", include_in_schema=False)
    async def user_page() -> FileResponse:
        """사용자 메인 화면."""
        return FileResponse(static_dir / "index.html")

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
