"""plans/173 T1 특성화 — `lifespan` 기동·종료 단계의 호출 순서·로그 순서·app.state 대입 순서.

동작 불변 리팩터(A4 · Extract Function) 전에 **현재 값**을 고정한다.
외부 의존(설정·로깅·그래프 빌드·asyncpg·Redis·알람 워커·SSE 브리지·incident 계측·
스키마 캐시·감사 미러)은 모두 가짜로 바꾸고 `async with lifespan(app)`를 구동해
한 줄짜리 사건 목록으로 기록한다. 실 네트워크 접속 0.

기록 규칙:
- 가짜 함수는 **호출 시점**에 사건을 남긴다(백그라운드 태스크 본문 시작 시점은 비결정이라
  쓰지 않는다).
- 백그라운드 태스크는 취소될 때 `<이름>:cancelled`(stop 이벤트가 있으면 set 여부 포함)를 남긴다.
- `app.state.<이름> = 값` 대입은 `state.<이름>=<값 종류>`로 남긴다.
- `src.api.server` 로거의 기록은 `(수준, 메시지)` 목록으로 따로 모은다.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest
import redis.asyncio as aioredis

import noise_gate.application.alarm_worker as alarm_worker_mod
import noise_gate.infrastructure.incident_events as incident_events_mod
import noise_gate.infrastructure.incident_repository as incident_repo_mod
import noise_gate.infrastructure.notification_bus as notification_bus_mod
import noise_gate.infrastructure.sse_bridge as sse_bridge_mod
import src.graph as graph_mod
import src.infrastructure.audit_repository as audit_repo_mod
import src.infrastructure.auth_provider as auth_provider_mod
import src.infrastructure.thread_repository as thread_repo_mod
import src.infrastructure.user_repository as user_repo_mod
import src.schema_cache.cache_manager as cache_manager_mod
import src.security.audit_logger as audit_logger_mod
import src.security.audit_service as audit_service_mod
import src.utils.log_retention as log_retention_mod
import src.utils.sql_file_logger as sql_file_logger_mod
from src.api import server


class _BoomError(Exception):
    """가짜 실패 — 메시지가 로그에 그대로 실린다."""


class _RecordingState:
    """`app.state` 대입 순서를 기록하는 가짜 상태 객체."""

    def __init__(self, events: list[str]) -> None:
        object.__setattr__(self, "_events", events)

    def __setattr__(self, name: str, value: Any) -> None:
        label = "None" if value is None else type(value).__name__
        self._events.append(f"state.{name}={label}")
        object.__setattr__(self, name, value)


def _config(
    *,
    auth_db_url: str = "",
    auth_enabled: bool = False,
    db_url: str = "",
    retention_days: int = 0,
    sql_days: int = 0,
    trace_days: int = 0,
    cache_backend: str = "file",
    alarm: bool = False,
    noise_gate: bool = False,
    sse_bridge: bool = False,
    incident: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        log_level="INFO",
        db_connection_string=db_url,
        auth=SimpleNamespace(auth_db_url=auth_db_url, enabled=auth_enabled),
        audit=SimpleNamespace(retention_days=retention_days),
        observability=SimpleNamespace(
            sql_log_enabled=True,
            sql_log_retention_days=sql_days,
            trace_retention_days=trace_days,
        ),
        schema_cache=SimpleNamespace(backend=cache_backend),
        alarm=SimpleNamespace(enabled=alarm, redis_stream_key="alarm:raw"),
        noise_gate=SimpleNamespace(
            enable_noise_gate=noise_gate,
            sse_bridge_enabled=sse_bridge,
            sse_bridge_channel="alarm:sse",
            incident_tracking_enabled=incident,
            incident_event_channel="alarm:incident",
        ),
        redis=SimpleNamespace(host="127.0.0.1", port=6399, password="", db=0),
    )


class _Harness:
    """가짜 의존을 꽂고 사건을 모은다."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace,
                 fail: frozenset[str]) -> None:
        self.events: list[str] = []
        self.config = config
        self.fail = fail
        self._install(monkeypatch)

    # ── 공통 ──
    def rec(self, event: str) -> None:
        self.events.append(event)

    def _maybe_fail(self, key: str) -> None:
        if key in self.fail:
            raise _BoomError(f"{key} 실패")

    async def _forever(self, name: str, stop: asyncio.Event | None = None) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            suffix = "" if stop is None else f" stop={stop.is_set()}"
            self.rec(f"{name}:cancelled{suffix}")
            raise

    def _install(self, mp: pytest.MonkeyPatch) -> None:
        h = self

        # ── 서버 모듈 전역(seam) ──
        def load_config() -> SimpleNamespace:
            h.rec("load_config")
            return h.config

        mp.setattr(server, "load_config", load_config)
        mp.setattr(server, "setup_logging", lambda level: h.rec(f"setup_logging({level})"))
        mp.setattr(server, "_validate_production_secrets",
                   lambda cfg: h.rec("validate_production_secrets"))
        mp.setattr(server, "_warn_shared_jwt_secret", lambda cfg: h.rec("warn_shared_jwt_secret"))

        def build_graph(cfg: Any, checkpointer: Any = None) -> object:
            h.rec(f"build_graph(checkpointer={type(checkpointer).__name__})")
            return SimpleNamespace()

        mp.setattr(server, "build_graph", build_graph)

        async def ensure_auth_tables(pool: Any) -> None:
            h.rec("ensure_auth_tables")
            h._maybe_fail("ensure_auth_tables")

        mp.setattr(server, "_ensure_auth_tables", ensure_auth_tables)

        async def seed_admin(user_repo: Any, cfg: Any) -> None:
            h.rec("seed_admin_user")

        mp.setattr(server, "_seed_admin_user", seed_admin)

        async def cleanup_audit_once(repo: Any, days: int) -> None:
            h.rec(f"cleanup_audit_once({days})")

        mp.setattr(server, "_cleanup_audit_once", cleanup_audit_once)

        def audit_loop(repo: Any, days: int) -> Any:
            h.rec(f"audit_retention_loop({days})")
            return h._forever("audit_retention_task")

        mp.setattr(server, "_run_audit_retention_loop", audit_loop)

        def file_loop(sql_days: Any, trace_days: Any) -> Any:
            h.rec(f"file_log_retention_loop({sql_days},{trace_days})")
            return h._forever("file_log_retention_task")

        mp.setattr(server, "_run_file_log_retention_loop", file_loop)

        async def check_apm(cfg: Any) -> None:
            h.rec("check_apm_sources")
            return None

        mp.setattr(server, "_check_apm_sources", check_apm)

        async def ensure_incident_tables(pool: Any) -> None:
            h.rec("ensure_incident_tables")

        mp.setattr(server, "_ensure_incident_tables", ensure_incident_tables)

        # ── 지연 import 대상 ──
        mp.setattr(sql_file_logger_mod, "init_sql_file_logger",
                   lambda enabled: h.rec(f"init_sql_file_logger({enabled})"))
        mp.setattr(log_retention_mod, "cleanup_file_logs",
                   lambda root, sql_days, trace_days: h.rec(
                       f"cleanup_file_logs({sql_days},{trace_days})"))

        class FakeConn:
            async def close(self) -> None:
                h.rec("checkpointer.conn.close")
                h._maybe_fail("checkpointer_close")

        class FakeCheckpointer:
            conn = FakeConn()

        async def create_checkpointer(cfg: Any) -> FakeCheckpointer:
            h.rec("create_checkpointer")
            return FakeCheckpointer()

        mp.setattr(graph_mod, "_create_checkpointer_async", create_checkpointer)

        class FakeBus:
            def __init__(self) -> None:
                h.rec("AlarmNotificationBus()")

        mp.setattr(notification_bus_mod, "AlarmNotificationBus", FakeBus)

        class FakePool:
            def __init__(self, kind: str) -> None:
                self.kind = kind

            async def close(self) -> None:
                h.rec(f"{self.kind}_pool.close")
                h._maybe_fail(f"{self.kind}_pool_close")

        class AuthPool(FakePool):
            pass

        class IncidentPool(FakePool):
            pass

        async def create_pool(dsn: str, min_size: int, max_size: int) -> FakePool:
            kind = "auth" if max_size == 5 else "incident"
            h.rec(f"create_pool({kind},{min_size},{max_size})")
            h._maybe_fail(f"{kind}_create_pool")
            return AuthPool(kind) if kind == "auth" else IncidentPool(kind)

        mp.setattr(asyncpg, "create_pool", create_pool)

        class UserRepo:
            def __init__(self, pool: Any) -> None:
                h.rec("PostgresUserRepository()")

        class AuditRepo:
            def __init__(self, pool: Any) -> None:
                h.rec("PostgresAuditRepository()")

        class AuthProvider:
            def __init__(self, repo: Any) -> None:
                h.rec("LocalAuthProvider()")

        class FakeAuditService:
            def __init__(self, config: Any, audit_repo: Any) -> None:
                h.rec("AuditService()")

        class ThreadRepo:
            def __init__(self, pool: Any) -> None:
                h.rec("PostgresThreadRepository()")

            async def ensure_tables(self) -> None:
                h.rec("thread_repo.ensure_tables")

        mp.setattr(user_repo_mod, "PostgresUserRepository", UserRepo)
        mp.setattr(audit_repo_mod, "PostgresAuditRepository", AuditRepo)
        mp.setattr(auth_provider_mod, "LocalAuthProvider", AuthProvider)
        mp.setattr(audit_service_mod, "AuditService", FakeAuditService)
        mp.setattr(thread_repo_mod, "PostgresThreadRepository", ThreadRepo)

        class FakeCacheMgr:
            async def ensure_redis_connected(self) -> None:
                h.rec("cache.ensure_redis_connected")
                h._maybe_fail("cache_connect")

            async def disconnect(self) -> None:
                h.rec("cache.disconnect")

        cache_mgr = FakeCacheMgr()

        def get_cache_manager(cfg: Any) -> FakeCacheMgr:
            h.rec("get_cache_manager")
            return cache_mgr

        mp.setattr(cache_manager_mod, "get_cache_manager", get_cache_manager)

        class FakeWorker:
            def __init__(self, cfg: Any) -> None:
                h.rec("AlarmWorker()")

            def run(self) -> Any:
                h.rec("AlarmWorker.run")
                return h._forever("alarm_worker_task")

        mp.setattr(alarm_worker_mod, "AlarmWorker", FakeWorker)

        class FakeRedis:
            def __init__(self, n: int) -> None:
                self.n = n

            async def aclose(self) -> None:
                h.rec(f"redis#{self.n}.aclose")
                h._maybe_fail(f"redis#{self.n}_aclose")

        redis_count = [0]

        def from_url(url: str, password: Any = None, db: int = 0) -> FakeRedis:
            redis_count[0] += 1
            n = redis_count[0]
            h.rec(f"redis.from_url#{n}({url},password={password},db={db})")
            h._maybe_fail(f"redis#{n}_from_url")
            return FakeRedis(n)

        mp.setattr(aioredis, "from_url", from_url)

        def run_sse(redis_client: Any, channel: str, bus: Any, stop_event: Any) -> Any:
            h.rec(f"run_sse_bridge_subscriber({channel},redis#{redis_client.n})")
            return h._forever("sse_bridge_task", stop_event)

        mp.setattr(sse_bridge_mod, "run_sse_bridge_subscriber", run_sse)

        class FakeIncidentStore:
            def __init__(self, pool: Any) -> None:
                h.rec("PostgresIncidentStore()")

        class FakePublisher:
            def __init__(self, redis_client: Any, channel: str) -> None:
                h.rec(f"RedisIncidentPublisher({channel},redis#{redis_client.n})")

        def run_incident(redis_client: Any, channel: str, store: Any, alarm_bus: Any,
                         stop_event: Any) -> Any:
            h.rec(f"run_incident_event_subscriber({channel},redis#{redis_client.n},"
                  f"bus={type(alarm_bus).__name__})")
            h._maybe_fail("incident_subscriber")
            return h._forever("incident_sub_task", stop_event)

        mp.setattr(incident_repo_mod, "PostgresIncidentStore", FakeIncidentStore)
        mp.setattr(incident_events_mod, "RedisIncidentPublisher", FakePublisher)
        mp.setattr(incident_events_mod, "run_incident_event_subscriber", run_incident)

        async def wait_pending() -> None:
            h.rec("wait_pending_db_mirrors")
            h._maybe_fail("wait_pending")

        mp.setattr(audit_logger_mod, "wait_pending_db_mirrors", wait_pending)


async def _drive(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
                 config: SimpleNamespace, fail: frozenset[str] = frozenset()
                 ) -> tuple[list[str], list[tuple[str, str]]]:
    h = _Harness(monkeypatch, config, fail)
    app = SimpleNamespace(state=_RecordingState(h.events))
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        async with server.lifespan(app):
            # 백그라운드 태스크가 첫 await 까지 진행하게 둔다(취소 기록이 남도록).
            for _ in range(5):
                await asyncio.sleep(0)
            h.rec("--- yield ---")
    logs = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "src.api.server"]
    return h.events, logs


def _pre_auth(sql_days: int, trace_days: int) -> list[str]:
    """인증 DB 블록 전까지 — 플래그와 무관하게 늘 같은 순서다."""
    return [
        "load_config",
        "setup_logging(INFO)",
        "validate_production_secrets",
        "warn_shared_jwt_secret",
        "init_sql_file_logger(True)",
        f"cleanup_file_logs({sql_days},{trace_days})",
        "create_checkpointer",
        "build_graph(checkpointer=FakeCheckpointer)",
        "state.graph=SimpleNamespace",
        "state.config=SimpleNamespace",
        "state.checkpointer=FakeCheckpointer",
        "AlarmNotificationBus()",
        "state.alarm_bus=FakeBus",
        "state.auth_pool=None",
        "state.user_repo=None",
        "state.audit_repo=None",
        "state.auth_provider=None",
        "state.audit_service=None",
        "state.thread_repo=None",
    ]


_AUTH_UNTIL_TABLES = [
    "create_pool(auth,1,5)",
    "state.auth_pool=AuthPool",
    "PostgresUserRepository()",
    "state.user_repo=UserRepo",
    "PostgresAuditRepository()",
    "state.audit_repo=AuditRepo",
    "AuditService()",
    "state.audit_service=FakeAuditService",
    "LocalAuthProvider()",
    "state.auth_provider=AuthProvider",
    "ensure_auth_tables",
]
_AUTH_OK = [
    *_AUTH_UNTIL_TABLES,
    "PostgresThreadRepository()",
    "state.thread_repo=ThreadRepo",
    "thread_repo.ensure_tables",
    "seed_admin_user",
    "cleanup_audit_once(30)",
]
_RETENTION_CACHE_APM_ALARM = [
    "audit_retention_loop(30)",
    "file_log_retention_loop(7,3)",
    "get_cache_manager",
    "cache.ensure_redis_connected",
    "check_apm_sources",
    "state.apm_source_report=None",
    "AlarmWorker()",
    "AlarmWorker.run",
]
_SSE_OK = [
    "redis.from_url#1(redis://127.0.0.1:6399,password=None,db=0)",
    "run_sse_bridge_subscriber(alarm:sse,redis#1)",
]
_INCIDENT_OK = [
    "state.incident_store=None",
    "state.incident_publisher=None",
    "create_pool(incident,1,3)",
    "ensure_incident_tables",
    "PostgresIncidentStore()",
    "state.incident_store=FakeIncidentStore",
    "redis.from_url#2(redis://127.0.0.1:6399,password=None,db=0)",
    "RedisIncidentPublisher(alarm:incident,redis#2)",
    "state.incident_publisher=FakePublisher",
    "run_incident_event_subscriber(alarm:incident,redis#2,bus=FakeBus)",
]
_SHUTDOWN_ALL = [
    "alarm_worker_task:cancelled",
    "sse_bridge_task:cancelled stop=True",
    "redis#1.aclose",
    "incident_sub_task:cancelled stop=True",
    "redis#2.aclose",
    "incident_pool.close",
    "audit_retention_task:cancelled",
    "file_log_retention_task:cancelled",
    "wait_pending_db_mirrors",
    "auth_pool.close",
    "checkpointer.conn.close",
    "get_cache_manager",
    "cache.disconnect",
]
_LOG_BUILD = ("INFO", "에이전트 그래프 빌드 완료")
_LOG_AUTH_OK = ("INFO", "인증 DB 초기화 완료")
_LOG_AUDIT_LOOP = ("INFO", "감사 로그 로테이션 주기 태스크 시작 (보관 30일)")
_LOG_FILE_LOOP = ("INFO", "파일 로그 정리 주기 태스크 시작 (SQL 7일 / 트레이스 3일)")
_LOG_CACHE_OK = ("INFO", "Redis 스키마 캐시 연결 완료")
_LOG_ALARM = ("INFO", "알람 분석 워커 시작 (stream=alarm:raw)")
_LOG_SSE_OK = ("INFO", "알람 SSE 브리지 구독 시작 (channel=alarm:sse)")
_LOG_INCIDENT_OK = ("INFO", "incident 계측 시작 (channel=alarm:incident)")
_LOG_END = ("INFO", "서버 종료")

_ALL_ON = dict(
    auth_db_url="postgresql://auth", db_url="postgresql://app", retention_days=30,
    sql_days=7, trace_days=3, cache_backend="redis", alarm=True, noise_gate=True,
    sse_bridge=True, incident=True,
)


# ─── 1. 플래그 전부 off · 인증 DB 없음 · 파일 캐시 ────────────────────────────


async def test_all_off(monkeypatch, caplog) -> None:
    events, logs = await _drive(monkeypatch, caplog, _config())
    assert events == [
        *_pre_auth(0, 0),
        "check_apm_sources",
        "state.apm_source_report=None",
        "state.incident_store=None",
        "state.incident_publisher=None",
        "--- yield ---",
        "checkpointer.conn.close",
    ]
    assert logs == [_LOG_BUILD, _LOG_END]


# ─── 2. 전부 on · 전부 성공 ───────────────────────────────────────────────────


async def test_all_on_success(monkeypatch, caplog) -> None:
    events, logs = await _drive(monkeypatch, caplog, _config(**_ALL_ON))
    assert events == [
        *_pre_auth(7, 3),
        *_AUTH_OK,
        *_RETENTION_CACHE_APM_ALARM,
        *_SSE_OK,
        *_INCIDENT_OK,
        "--- yield ---",
        *_SHUTDOWN_ALL,
    ]
    assert logs == [
        _LOG_BUILD, _LOG_AUTH_OK, _LOG_AUDIT_LOOP, _LOG_FILE_LOOP, _LOG_CACHE_OK,
        _LOG_ALARM, _LOG_SSE_OK, _LOG_INCIDENT_OK, _LOG_END,
    ]


# ─── 3. 노이즈 게이트 off — SSE 브리지·incident 계측 플래그가 켜져 있어도 미기동 ──


async def test_noise_gate_off_skips_bridge_and_incident(monkeypatch, caplog) -> None:
    events, logs = await _drive(monkeypatch, caplog, _config(**{**_ALL_ON, "noise_gate": False}))
    assert events == [
        *_pre_auth(7, 3),
        *_AUTH_OK,
        *_RETENTION_CACHE_APM_ALARM,
        "state.incident_store=None",
        "state.incident_publisher=None",
        "--- yield ---",
        "alarm_worker_task:cancelled",
        "audit_retention_task:cancelled",
        "file_log_retention_task:cancelled",
        "wait_pending_db_mirrors",
        "auth_pool.close",
        "checkpointer.conn.close",
        "get_cache_manager",
        "cache.disconnect",
    ]
    assert logs == [
        _LOG_BUILD, _LOG_AUTH_OK, _LOG_AUDIT_LOOP, _LOG_FILE_LOOP, _LOG_CACHE_OK,
        _LOG_ALARM, _LOG_END,
    ]


# ─── 4. 기동 단계 실패 — 인증 풀·캐시 연결·SSE Redis·incident DSN 미설정 ──────


async def test_start_failures_are_swallowed(monkeypatch, caplog) -> None:
    events, logs = await _drive(
        monkeypatch, caplog, _config(**{**_ALL_ON, "db_url": ""}),
        frozenset({"auth_create_pool", "cache_connect", "redis#1_from_url"}),
    )
    assert events == [
        *_pre_auth(7, 3),
        "create_pool(auth,1,5)",
        "file_log_retention_loop(7,3)",
        "get_cache_manager",
        "cache.ensure_redis_connected",
        "check_apm_sources",
        "state.apm_source_report=None",
        "AlarmWorker()",
        "AlarmWorker.run",
        "redis.from_url#1(redis://127.0.0.1:6399,password=None,db=0)",
        "state.incident_store=None",
        "state.incident_publisher=None",
        "state.incident_store=None",
        "state.incident_publisher=None",
        "--- yield ---",
        "alarm_worker_task:cancelled",
        "file_log_retention_task:cancelled",
        "checkpointer.conn.close",
        "get_cache_manager",
        "cache.disconnect",
    ]
    assert logs == [
        _LOG_BUILD,
        ("WARNING", "인증 DB 초기화 실패 (인증 기능 비활성): auth_create_pool 실패"),
        _LOG_FILE_LOOP,
        ("WARNING", "Redis 연결 실패 (파일 캐시로 폴백): cache_connect 실패"),
        _LOG_ALARM,
        ("WARNING", "알람 SSE 브리지 구독 시작 실패 (실시간 SSE 비활성): redis#1_from_url 실패"),
        ("WARNING", "incident 계측 시작 실패 (계측 비활성): "
                    "db_connection_string 미설정 — incident 계측 비활성"),
        _LOG_END,
    ]


# ─── 5. 기동 중간 실패 — 인증 DDL 실패 · incident 구독 실패(자원은 종료 때)
#        인증 활성이면 앞서 둔 state 유지 · 비활성이면 원복·풀 닫기·감사 로테이션 미시작
#        (plans/184 L1)


async def test_mid_start_failures_keep_partial_state(monkeypatch, caplog) -> None:
    events, logs = await _drive(
        monkeypatch, caplog, _config(**{**_ALL_ON, "auth_enabled": True}),
        frozenset({"ensure_auth_tables", "incident_subscriber"}),
    )
    assert events == [
        *_pre_auth(7, 3),
        *_AUTH_UNTIL_TABLES,
        *_RETENTION_CACHE_APM_ALARM,
        *_SSE_OK,
        *_INCIDENT_OK,
        "state.incident_store=None",
        "state.incident_publisher=None",
        "--- yield ---",
        "alarm_worker_task:cancelled",
        "sse_bridge_task:cancelled stop=True",
        "redis#1.aclose",
        "redis#2.aclose",
        "incident_pool.close",
        "audit_retention_task:cancelled",
        "file_log_retention_task:cancelled",
        "wait_pending_db_mirrors",
        "auth_pool.close",
        "checkpointer.conn.close",
        "get_cache_manager",
        "cache.disconnect",
    ]
    assert logs == [
        _LOG_BUILD,
        ("WARNING", "인증 DB 초기화 일부 실패 — 인증 활성이라 사용자 조회 연결은 유지합니다: "
                    "ensure_auth_tables 실패"),
        _LOG_AUDIT_LOOP, _LOG_FILE_LOOP, _LOG_CACHE_OK, _LOG_ALARM, _LOG_SSE_OK,
        ("WARNING", "incident 계측 시작 실패 (계측 비활성): incident_subscriber 실패"),
        _LOG_END,
    ]


async def test_mid_start_failures_revert_auth_state(monkeypatch, caplog) -> None:
    events, logs = await _drive(
        monkeypatch, caplog, _config(**_ALL_ON),
        frozenset({"ensure_auth_tables", "incident_subscriber"}),
    )
    assert events == [
        *_pre_auth(7, 3),
        *_AUTH_UNTIL_TABLES,
        "state.auth_pool=None",
        "state.user_repo=None",
        "state.audit_repo=None",
        "state.auth_provider=None",
        "state.audit_service=None",
        "state.thread_repo=None",
        "auth_pool.close",
        *[e for e in _RETENTION_CACHE_APM_ALARM if e != "audit_retention_loop(30)"],
        *_SSE_OK,
        *_INCIDENT_OK,
        "state.incident_store=None",
        "state.incident_publisher=None",
        "--- yield ---",
        "alarm_worker_task:cancelled",
        "sse_bridge_task:cancelled stop=True",
        "redis#1.aclose",
        "redis#2.aclose",
        "incident_pool.close",
        "file_log_retention_task:cancelled",
        "checkpointer.conn.close",
        "get_cache_manager",
        "cache.disconnect",
    ]
    assert logs == [
        _LOG_BUILD,
        ("WARNING", "인증 DB 초기화 실패 (인증 기능 비활성): ensure_auth_tables 실패"),
        _LOG_FILE_LOOP, _LOG_CACHE_OK, _LOG_ALARM, _LOG_SSE_OK,
        ("WARNING", "incident 계측 시작 실패 (계측 비활성): incident_subscriber 실패"),
        _LOG_END,
    ]


# ─── 6. 종료 단계 close 실패 — 삼키고 다음 정리로 넘어간다(감사 미러 대기만 warning) ──


async def test_close_failures_are_swallowed(monkeypatch, caplog) -> None:
    events, logs = await _drive(
        monkeypatch, caplog, _config(**_ALL_ON),
        frozenset({"redis#1_aclose", "redis#2_aclose", "incident_pool_close", "wait_pending",
                   "auth_pool_close", "checkpointer_close"}),
    )
    assert events == [
        *_pre_auth(7, 3),
        *_AUTH_OK,
        *_RETENTION_CACHE_APM_ALARM,
        *_SSE_OK,
        *_INCIDENT_OK,
        "--- yield ---",
        *_SHUTDOWN_ALL,
    ]
    assert logs == [
        _LOG_BUILD, _LOG_AUTH_OK, _LOG_AUDIT_LOOP, _LOG_FILE_LOOP, _LOG_CACHE_OK,
        _LOG_ALARM, _LOG_SSE_OK, _LOG_INCIDENT_OK,
        ("WARNING", "쿼리 실행 감사 DB 쓰기 종료 대기 실패(무시)"),
        _LOG_END,
    ]
    (mirror,) = [r for r in caplog.records
                 if r.name == "src.api.server" and "감사 DB 쓰기" in r.getMessage()]
    assert mirror.exc_info is not None
