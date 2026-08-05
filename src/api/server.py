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
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import admin, admin_auth, alarm, conversation, health, query, schema_cache, user_auth
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
    alarm_zones     TEXT[],
    is_protected    BOOLEAN NOT NULL DEFAULT FALSE,
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
        # 기존 설치(IF NOT EXISTS로 컬럼 미추가)에 신규 컬럼을 멱등 보강한다(Plan 59/59-a, incident 패턴 미러)
        async with pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS alarm_zones TEXT[]"
            )
            await conn.execute(
                "ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS is_protected BOOLEAN NOT NULL DEFAULT FALSE"
            )
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


def _validate_production_secrets(config) -> None:
    """운영 모드(AUTH_ENABLED=true)에서 필수 크레덴셜·시크릿 미설정 시 기동을 거부한다(D-071).

    os.getenv는 .env/.encenv 값을 못 보므로(Known Mistakes 2026-06-10) pydantic 필드와
    _jwt_secret_explicit 플래그로 '명시적 설정' 여부를 판정한다. 개발 모드는 검사하지 않는다.
    """
    if not config.auth.enabled:
        return
    missing = []
    if not config.admin.username or not config.admin.password:
        missing.append("ADMIN_USERNAME/ADMIN_PASSWORD(관리자 부트스트랩 계정)")
    if not config.admin._jwt_secret_explicit:
        missing.append("ADMIN_JWT_SECRET(운영자 토큰 시크릿)")
    if not config.auth._jwt_secret_explicit:
        missing.append("AUTH_JWT_SECRET(사용자 토큰 시크릿)")
    if missing:
        raise RuntimeError(
            "운영 모드(AUTH_ENABLED=true) 기동 거부 — 다음을 .env/.encenv에 설정하세요: "
            + ", ".join(missing)
        )


async def _seed_admin_user(user_repo, config) -> None:
    """활성 관리자가 0명이고 env 부트스트랩 크레덴셜이 있으면 seed admin을 1회 생성한다(멱등, §9.2).

    통합 RBAC(D-069)에서는 어드민 접근이 DB role==admin으로 판정되므로, 최초 기동 시
    관리자 한 명은 DB에 존재해야 한다. break-glass env 계정으로 그 씨앗을 심는다.
    """
    from src.domain.user import User, UserRole, UserStatus
    from src.utils.password import hash_password

    admin_cfg = config.admin
    if not admin_cfg.username or not admin_cfg.password:
        return  # 부트스트랩 계정 미설정 → seed 생략(개발 모드 등)
    try:
        users = await user_repo.list_all()
    except Exception as e:
        logger.warning("seed admin 확인 실패: %s", e)
        return
    if any(u.role == UserRole.ADMIN and u.is_active for u in users):
        return  # 이미 활성 관리자 존재 → 멱등 생략
    if await user_repo.exists(admin_cfg.username):
        logger.info("seed admin: user_id=%s 이미 존재 → 생성 생략", admin_cfg.username)
        return
    seed = User(
        user_id=admin_cfg.username,
        username=admin_cfg.username,
        hashed_password=hash_password(admin_cfg.password),
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
        is_protected=True,  # 솔루션 상시 관리자 — 역할/상태 변경·PW초기화·삭제 불가(Plan 59-a §9)
        auth_method="local",
    )
    try:
        await user_repo.create(seed)
        logger.info("seed admin 생성 완료: %s (활성 관리자 0명 → 부트스트랩)", admin_cfg.username)
    except Exception as e:
        logger.warning("seed admin 생성 실패: %s", e)


async def _cleanup_audit_once(audit_repo, retention_days: int) -> None:
    """보관 기간(retention_days)이 지난 감사 로그를 1회 삭제한다(Plan 59-a §11).

    retention_days<=0이면 비활성(정리 안 함). 예외는 warning으로 삼켜 기동/루프를 막지 않는다.
    """
    if not audit_repo or retention_days is None or retention_days <= 0:
        return
    try:
        deleted = await audit_repo.cleanup_old_logs(retention_days)
        logger.info("감사 로그 로테이션: %s일 경과 %s건 삭제", retention_days, deleted)
    except Exception as e:
        logger.warning("감사 로그 로테이션 실패: %s", e)


async def _run_audit_retention_loop(audit_repo, retention_days: int, interval_seconds: int = 86400) -> None:
    """감사 로그 로테이션을 주기적으로(기본 하루 1회) 수행하는 백그라운드 루프(Plan 59-a §11)."""
    import asyncio

    if not audit_repo or retention_days is None or retention_days <= 0:
        return
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            break
        await _cleanup_audit_once(audit_repo, retention_days)


# D-049: incident 라이프사이클 테이블 (ddl/alarm_incidents.sql과 동일)
_INCIDENT_DDL = """
CREATE TABLE IF NOT EXISTS alarm_incidents (
    id          BIGSERIAL PRIMARY KEY,
    fingerprint VARCHAR(128) NOT NULL,
    alarm_id    VARCHAR(64),
    alarm_name  VARCHAR(255),
    db_id       VARCHAR(64),
    server_name VARCHAR(255),
    severity    INTEGER,
    priority    VARCHAR(20),
    tier        VARCHAR(20),
    status      VARCHAR(20) NOT NULL DEFAULT 'open',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acked_at    TIMESTAMPTZ,
    acked_by    VARCHAR(100),
    resolved_at TIMESTAMPTZ,
    resolution  VARCHAR(20)
);
"""


async def _ensure_incident_tables(pool) -> None:
    """incident 테이블/인덱스가 없으면 생성한다 (D-049 · _ensure_auth_tables 미러)."""
    try:
        async with pool.acquire() as conn:
            await conn.execute(_INCIDENT_DDL)
        # 기존 테이블(IF NOT EXISTS로 컬럼 미추가)에 alarm_name을 멱등 보강한다 (D-049 delta).
        # 신규 환경은 위 CREATE로, 기존 환경은 이 ALTER로 커버한다(graceful·auth 패턴 일관).
        async with pool.acquire() as conn:
            await conn.execute(
                "ALTER TABLE alarm_incidents "
                "ADD COLUMN IF NOT EXISTS alarm_name VARCHAR(255)"
            )
        async with pool.acquire() as conn:
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alarm_incidents_status "
                "ON alarm_incidents(status)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alarm_incidents_fp_open "
                "ON alarm_incidents(fingerprint) WHERE status = 'open'"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_alarm_incidents_created_at "
                "ON alarm_incidents(created_at DESC)"
            )
    except Exception as e:
        logger.warning("incident 테이블 DDL 실행 실패: %s", e)


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

    # D-071: 운영 모드 필수 크레덴셜·시크릿 검증(미설정 시 기동 거부)
    _validate_production_secrets(config)

    # SQL 파일 로거 초기화
    from src.utils.sql_file_logger import init_sql_file_logger
    init_sql_file_logger()

    from src.graph import _create_checkpointer_async

    checkpointer = await _create_checkpointer_async(config)
    app.state.graph = build_graph(config, checkpointer=checkpointer)
    app.state.config = config
    # 설정 리로드(POST /admin/settings/reload) 시 그래프 재빌드에 재사용한다(Plan 68 §6).
    app.state.checkpointer = checkpointer
    logger.info("에이전트 그래프 빌드 완료")

    from noise_gate.infrastructure.notification_bus import AlarmNotificationBus
    app.state.alarm_bus = AlarmNotificationBus()

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
            # break-glass seed admin 부트스트랩(활성 관리자 0명 시, 멱등, §9.2)
            await _seed_admin_user(app.state.user_repo, config)
            # 감사 로그 로테이션(Plan 59-a §11): 기동 시 1회 정리
            await _cleanup_audit_once(app.state.audit_repo, config.audit.retention_days)
            logger.info("인증 DB 초기화 완료")
        except Exception as e:
            logger.warning("인증 DB 초기화 실패 (인증 기능 비활성): %s", e)

    # 감사 로그 로테이션 주기 태스크(하루 1회). audit_repo가 있고 retention_days>0일 때만.
    audit_retention_task = None
    if getattr(app.state, "audit_repo", None) and config.audit.retention_days > 0:
        import asyncio as _asyncio
        audit_retention_task = _asyncio.create_task(
            _run_audit_retention_loop(app.state.audit_repo, config.audit.retention_days)
        )
        logger.info("감사 로그 로테이션 주기 태스크 시작 (보관 %s일)", config.audit.retention_days)

    # Redis 연결 (스키마 캐시)
    if config.schema_cache.backend == "redis":
        from src.schema_cache.cache_manager import get_cache_manager
        cache_mgr = get_cache_manager(config)
        try:
            await cache_mgr.ensure_redis_connected()
            logger.info("Redis 스키마 캐시 연결 완료")
        except Exception as e:
            logger.warning("Redis 연결 실패 (파일 캐시로 폴백): %s", e)

    # 알람 분석 워커 시작 (ALARM_ENABLED=true인 경우에만)
    alarm_worker_task = None
    if config.alarm.enabled:
        import asyncio as _asyncio
        from noise_gate.application.alarm_worker import AlarmWorker
        alarm_worker_task = _asyncio.create_task(AlarmWorker(config).run())
        logger.info(
            "알람 분석 워커 시작 (stream=%s)", config.alarm.redis_stream_key
        )

    # 알람 SSE Redis pub/sub 브리지 구독 (워커→UI 실시간 SSE — D-048.9 해소).
    # 워커는 cross-process라 in-memory alarm_bus를 공유 못 하므로 Redis로 중계받는다.
    # 게이트 활성 + NOISE_SSE_BRIDGE_ENABLED=true일 때만 기동(기본 off → E3 무변경, 회귀 0).
    # Redis 연결 실패는 warning 후 폴백 — 서버 기동을 막지 않는다.
    sse_bridge_task = None
    sse_bridge_redis = None
    sse_bridge_stop = None
    if config.noise_gate.enable_noise_gate and config.noise_gate.sse_bridge_enabled:
        try:
            import asyncio as _asyncio
            import redis.asyncio as _aioredis

            from noise_gate.infrastructure.sse_bridge import run_sse_bridge_subscriber

            sse_bridge_redis = _aioredis.from_url(
                f"redis://{config.redis.host}:{config.redis.port}",
                password=config.redis.password or None,
                db=config.redis.db,
            )
            sse_bridge_stop = _asyncio.Event()
            sse_bridge_task = _asyncio.create_task(
                run_sse_bridge_subscriber(
                    sse_bridge_redis,
                    config.noise_gate.sse_bridge_channel,
                    app.state.alarm_bus,
                    stop_event=sse_bridge_stop,
                )
            )
            logger.info(
                "알람 SSE 브리지 구독 시작 (channel=%s)",
                config.noise_gate.sse_bridge_channel,
            )
        except Exception as e:
            logger.warning("알람 SSE 브리지 구독 시작 실패 (실시간 SSE 비활성): %s", e)

    # ── D-049: ack/incident 라이프사이클 계측 (PostgreSQL 단일 저장소) ──
    # incident_tracking_enabled=true일 때만 기동(기본 off → /alarm/metrics 기존 null 동작, 회귀 0).
    # 전용 PG 풀(auth 독립, DSN 재사용) + 단일 라이터(Redis 구독 subscriber)를 기동한다.
    # 어떤 단계가 실패해도 서버 기동을 막지 않는다(graceful — warning 후 트래커 비활성).
    app.state.incident_store = None
    app.state.incident_publisher = None
    incident_pool = None
    incident_redis = None
    incident_sub_task = None
    incident_sub_stop = None
    if config.noise_gate.enable_noise_gate and config.noise_gate.incident_tracking_enabled:
        incident_db_url = config.db_connection_string
        try:
            import asyncio as _asyncio
            import asyncpg

            from noise_gate.infrastructure.incident_repository import (
                PostgresIncidentStore,
            )

            if not incident_db_url:
                raise RuntimeError("db_connection_string 미설정 — incident 계측 비활성")

            incident_pool = await asyncpg.create_pool(
                incident_db_url, min_size=1, max_size=3
            )
            await _ensure_incident_tables(incident_pool)
            app.state.incident_store = PostgresIncidentStore(incident_pool)

            import redis.asyncio as _aioredis

            from noise_gate.infrastructure.incident_events import (
                RedisIncidentPublisher,
                run_incident_event_subscriber,
            )

            incident_redis = _aioredis.from_url(
                f"redis://{config.redis.host}:{config.redis.port}",
                password=config.redis.password or None,
                db=config.redis.db,
            )
            # API 경로(analyze-*)에서 PAGE 결정 시 open 발행에 재사용한다(같은 Redis 채널 → 단일 라이터).
            app.state.incident_publisher = RedisIncidentPublisher(
                incident_redis, config.noise_gate.incident_event_channel
            )
            incident_sub_stop = _asyncio.Event()
            incident_sub_task = _asyncio.create_task(
                run_incident_event_subscriber(
                    incident_redis,
                    config.noise_gate.incident_event_channel,
                    app.state.incident_store,
                    alarm_bus=app.state.alarm_bus,
                    stop_event=incident_sub_stop,
                )
            )
            logger.info(
                "incident 계측 시작 (channel=%s)",
                config.noise_gate.incident_event_channel,
            )
        except Exception as e:
            logger.warning("incident 계측 시작 실패 (계측 비활성): %s", e)
            app.state.incident_store = None
            app.state.incident_publisher = None

    yield

    # 종료 시: 알람 워커 정리
    if alarm_worker_task:
        alarm_worker_task.cancel()
        try:
            import asyncio as _asyncio
            await alarm_worker_task
        except Exception:
            pass

    # 종료 시: SSE 브리지 구독 task·Redis 연결 정리
    if sse_bridge_task:
        if sse_bridge_stop is not None:
            sse_bridge_stop.set()
        sse_bridge_task.cancel()
        try:
            await sse_bridge_task
        except Exception:
            pass
    if sse_bridge_redis is not None:
        try:
            await sse_bridge_redis.aclose()
        except Exception:
            pass

    # 종료 시: incident 계측 구독 task·Redis 연결·전용 PG 풀 정리 (D-049)
    if incident_sub_task:
        if incident_sub_stop is not None:
            incident_sub_stop.set()
        incident_sub_task.cancel()
        try:
            await incident_sub_task
        except Exception:
            pass
    if incident_redis is not None:
        try:
            await incident_redis.aclose()
        except Exception:
            pass
    if incident_pool is not None:
        try:
            await incident_pool.close()
        except Exception:
            pass

    # 종료 시: 감사 로그 로테이션 주기 태스크 정리
    if audit_retention_task:
        audit_retention_task.cancel()
        try:
            await audit_retention_task
        except Exception:
            pass

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
        docs_url=None,   # 기본 CDN 기반 Swagger UI 비활성화
        redoc_url=None,  # ReDoc도 동일하게 비활성화
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
    application.include_router(alarm.router, prefix="/api/v1", tags=["alarm"])
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

    # 로컬 에셋을 사용하는 Swagger UI (내부망 오프라인 환경 지원)
    @application.get("/docs", include_in_schema=False)
    async def custom_swagger_ui() -> HTMLResponse:
        from fastapi.openapi.docs import get_swagger_ui_html
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=application.title + " - Swagger UI",
            swagger_js_url="/static/swagger-ui/swagger-ui-bundle.js",
            swagger_css_url="/static/swagger-ui/swagger-ui.css",
            swagger_favicon_url="/static/swagger-ui/favicon-32x32.png",
        )

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
