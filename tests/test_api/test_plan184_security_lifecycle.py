"""plans/184 W1 — 보안·수명주기 행동 변경 단언.

- S1 DRM 사유: 설정 키 이름·경로가 담긴 사유는 화면에 싣지 않는다
  (고정 문구 + 요청 번호, 원문은 로그).
- S2 빈 `sub`: 운영자 토큰도 사용자·break-glass 경로처럼 `not sub` → 401.
- S3 다중 워커: 워커가 2개 이상으로 뜬 것으로 보이면 기동 경고 1줄 · 기본(워커 1)은 무로그.
- L1 인증 DB 초기화 실패(풀 생성 뒤): 인증 비활성이면 대입한 app.state 원복 · 풀 닫기 · 감사
  로테이션 미시작 · 인증 활성이면 원복하지 않는다(원복하면 사용자 조회가 토큰 payload 폴백 —
  fail-open).
- L2 종료 단계 캐시 연결 종료 실패: 삼키고 「서버 종료」 로그까지 간다.
- K7 사전 게이트: 텍스트 진입은 게이트가 본문에 적은 인가 선택을 쓴다(호출자 재대입 없음).

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
import structlog
from fastapi import HTTPException

from src.config import DrmConfig
from src.infrastructure.drm import DrmDecryptError
from src.infrastructure.drm.decryptor import reset_decryptor
from src.infrastructure.drm.scsl import ScslCliDecryptor

_ROOT = Path(__file__).resolve().parents[2]
_QUERY_PY = _ROOT / "src" / "api" / "routes" / "query.py"
SCDS = bytes.fromhex("5343445341303032" + "0000f0060000") + b"\xa7\x4d\xd9\x5e"


# ─── S1 DRM 사유 ────────────────────────────────────────────────────────────


@pytest.fixture
def scsl_config(tmp_path: Path) -> DrmConfig:
    """검증 대상 필드를 명시해 .env 누수를 차단한 활성 설정."""
    return DrmConfig(
        enabled=True,
        java_bin="/opt/secret/jdk/bin/java",
        wrapper_path=str(tmp_path / "Decrypt.java"),
        scsl_jar_path=str(tmp_path / "scsl.jar"),
        properties_path=str(tmp_path / "softcamp.properties"),
        key_file_path=str(tmp_path / "keyDAC_SVR0.sc"),
        temp_dir=str(tmp_path / "drm_temp"),
        timeout_sec=5,
    )


@pytest.fixture(autouse=True)
def _reset_drm_singleton():
    reset_decryptor()
    yield
    reset_decryptor()


async def test_scsl_missing_config_is_not_user_visible(tmp_path: Path) -> None:
    cfg = DrmConfig(enabled=True, scsl_jar_path="", temp_dir=str(tmp_path))
    with pytest.raises(DrmDecryptError) as ei:
        await ScslCliDecryptor(cfg).decrypt(SCDS, "a.xlsx")
    assert "DRM_SCSL_JAR_PATH" in ei.value.reason  # 원문(로그용)은 그대로
    assert ei.value.user_visible is False


async def test_scsl_java_not_found_is_not_user_visible(scsl_config, monkeypatch) -> None:
    dec = ScslCliDecryptor(scsl_config)

    async def missing_java(cmd: list[str]) -> Any:
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(dec, "_run_wrapper", missing_java)
    with pytest.raises(DrmDecryptError) as ei:
        await dec.decrypt(SCDS, "a.xlsx")
    assert "/opt/secret/jdk/bin/java" in ei.value.reason
    assert ei.value.user_visible is False


async def test_scsl_ret_reason_stays_user_visible(scsl_config, monkeypatch) -> None:
    dec = ScslCliDecryptor(scsl_config)

    async def ret_fail(cmd: list[str]) -> Any:
        return 1, "RET:3030\n", ""

    monkeypatch.setattr(dec, "_run_wrapper", ret_fail)
    with pytest.raises(DrmDecryptError) as ei:
        await dec.decrypt(SCDS, "a.xlsx")
    assert ei.value.user_visible is True
    assert ei.value.ret_code == 3030


class _UploadHarness:
    """`_resolve_uploaded_bytes`를 가짜 복호화기로 돌린다(감사 기록은 목록으로 받는다)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, error: DrmDecryptError,
                 drm: DrmConfig) -> None:
        import src.infrastructure.drm as drm_pkg
        import src.security.audit_logger as audit_logger

        self.audit: list[dict[str, Any]] = []

        async def audit_noop(**kwargs: Any) -> None:
            self.audit.append(kwargs)

        class Failing:
            async def decrypt(self, file_bytes: bytes, filename: str) -> bytes:
                raise error

        monkeypatch.setattr(audit_logger, "log_drm_decrypt", audit_noop)
        monkeypatch.setattr(drm_pkg, "get_decryptor", lambda cfg: Failing())
        self.config = SimpleNamespace(drm=drm)

    async def run(self) -> HTTPException:
        from src.api.query.upload import _resolve_uploaded_bytes

        with pytest.raises(HTTPException) as ei:
            await _resolve_uploaded_bytes(SCDS + b"x", "xlsx", "a.xlsx", self.config, user_id="u1")
        return ei.value


@pytest.mark.parametrize("reason", [
    "DRM 설정 누락: DRM_SCSL_JAR_PATH, DRM_KEY_FILE_PATH",
    "java 실행 파일을 찾을 수 없음: /opt/secret/jdk/bin/java",
])
async def test_upload_hides_config_reason_with_request_id(
    reason: str, scsl_config, monkeypatch, caplog
) -> None:
    h = _UploadHarness(monkeypatch, DrmDecryptError(reason, user_visible=False), scsl_config)
    structlog.contextvars.bind_contextvars(request_id="req-184")
    try:
        with caplog.at_level(logging.ERROR, logger="src.api.routes.query"):
            exc = await h.run()
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    assert exc.status_code == 502
    assert exc.detail == (
        "DRM 암호화 파일로 확인되나 복호화에 실패했습니다: "
        "서버 DRM 설정 오류 — 관리자에게 문의하세요 (요청 req-184)"
    )
    for leak in ("DRM_SCSL_JAR_PATH", "DRM_KEY_FILE_PATH", "/opt/secret", "누락", "java"):
        assert leak not in exc.detail
    # 원문은 서버 로그·감사 기록에 남는다
    assert any(reason in r.getMessage() for r in caplog.records)
    assert h.audit[-1]["error"] == reason


async def test_upload_keeps_fixed_ret_reason_on_screen(scsl_config, monkeypatch) -> None:
    h = _UploadHarness(
        monkeypatch,
        DrmDecryptError("인증되지 않은 키 파일 — KeyManager 재시작 필요", ret_code=3030),
        scsl_config,
    )
    exc = await h.run()
    assert exc.status_code == 502
    assert exc.detail == (
        "DRM 암호화 파일로 확인되나 복호화에 실패했습니다: "
        "인증되지 않은 키 파일 — KeyManager 재시작 필요"
    )


@pytest.mark.parametrize(("user_visible", "ret", "extra"), [
    (False, None, None),
    (False, 7, "stderr: /opt/secret/key.dat"),
    (True, 3030, "KeyManager 응답 원문"),
], ids=["hidden", "hidden_with_ret_detail", "visible"])
async def test_upload_failure_logs_once_with_raw_reason(
    user_visible: bool, ret: int | None, extra: str | None, scsl_config, monkeypatch, caplog
) -> None:
    """복호화 실패 로그는 1건 — 원문(파일·사유·ret·상세)과 요청 번호가 그 한 줄에 있다(184 교정)."""
    reason = "DRM 설정 누락: DRM_SCSL_JAR_PATH"
    h = _UploadHarness(
        monkeypatch,
        DrmDecryptError(reason, ret_code=ret, detail=extra, user_visible=user_visible),
        scsl_config,
    )
    structlog.contextvars.bind_contextvars(request_id="req-184b")
    try:
        with caplog.at_level(logging.DEBUG, logger="src.api.routes.query"):
            exc = await h.run()
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    assert exc.status_code == 502
    records = [r for r in caplog.records if r.name == "src.api.routes.query"]
    assert len(records) == 1, [r.getMessage() for r in records]
    message = records[0].getMessage()
    assert records[0].levelno == logging.ERROR
    for raw in ("a.xlsx", reason, f"ret={ret}", f"detail={extra}", "req-184b"):
        assert raw in message
    if not user_visible:
        assert exc.detail.endswith("(요청 req-184b)")


# ─── S2 빈 sub ──────────────────────────────────────────────────────────────


SECRET = "s" * 32


def _token(claims: dict[str, Any]) -> str:
    payload = {"exp": datetime.now(UTC) + timedelta(hours=1), **claims}
    return jwt.encode(payload, SECRET, algorithm="HS256")


@pytest.mark.parametrize("claims", [{"sub": ""}, {}, {"sub": None}])
def test_admin_token_empty_sub_rejected(claims: dict[str, Any]) -> None:
    from src.api.routes.admin_auth import verify_admin_token

    with pytest.raises(HTTPException) as ei:
        verify_admin_token(_token({**claims, "type": "admin"}), SECRET)
    assert (ei.value.status_code, ei.value.detail) == (401, "유효하지 않은 토큰입니다.")


def test_user_token_empty_sub_rejected() -> None:
    from src.api.dependencies import _verify_user_token

    with pytest.raises(HTTPException) as ei:
        _verify_user_token(_token({"sub": "", "type": "user"}), SECRET)
    assert ei.value.status_code == 401


def test_break_glass_empty_sub_not_admin() -> None:
    from src.api.dependencies import _try_break_glass_admin

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        config=SimpleNamespace(admin=SimpleNamespace(jwt_secret=SECRET)))))
    empty = _token({"sub": "", "type": "admin"})
    assert _try_break_glass_admin(request, f"Bearer {empty}") is None  # type: ignore[arg-type]
    ok = _token({"sub": "root", "type": "admin"})
    assert _try_break_glass_admin(request, f"Bearer {ok}")["sub"] == "root"  # type: ignore[arg-type,index]


def test_admin_token_type_still_checked() -> None:
    """D-070 — 빈 sub 통일이 type 검증을 대신하지 않는다."""
    from src.api.routes.admin_auth import verify_admin_token

    with pytest.raises(HTTPException) as ei:
        verify_admin_token(_token({"sub": "root", "type": "user"}), SECRET)
    assert ei.value.status_code == 401


# ─── S3 다중 워커 경고 ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("argv", "env", "expected"), [
    (["uvicorn", "src.api.server:app"], {}, 1),
    (["uvicorn", "src.api.server:app", "--workers", "4"], {}, 4),
    (["uvicorn", "src.api.server:app", "--workers=2"], {}, 2),
    (["uvicorn", "src.api.server:app"], {"WEB_CONCURRENCY": "3"}, 3),
    # uvicorn 규칙 — CLI `--workers`가 WEB_CONCURRENCY보다 우선
    (["uvicorn", "src.api.server:app", "--workers", "1"], {"WEB_CONCURRENCY": "4"}, 1),
    (["uvicorn", "src.api.server:app"], {"WEB_CONCURRENCY": "abc"}, 1),
    (["python", "-m", "src.main", "--server"], {}, 1),
    # uvicorn reload 모드는 workers를 무시하고 단일 프로세스다(plans/184 교정)
    (["uvicorn", "src.api.server:app", "--reload", "--workers", "4"], {}, 1),
    (["uvicorn", "src.api.server:app", "--workers=4", "--reload"], {}, 1),
    (["uvicorn", "src.api.server:app", "--reload"], {"WEB_CONCURRENCY": "4"}, 1),
    (["uvicorn", "src.api.server:app", "--reload=true", "--workers", "2"], {}, 1),
    # `--reload-dir`만으로는 reload가 켜지지 않는다
    (["uvicorn", "src.api.server:app", "--reload-dir", "src", "--workers", "2"], {}, 2),
])
def test_detect_worker_count(argv: list[str], env: dict[str, str], expected: int) -> None:
    from src.api import server

    assert server._detect_worker_count(argv, env) == expected


def test_multi_worker_warns_once(caplog) -> None:
    from src.api import server

    with caplog.at_level(logging.INFO, logger="src.api.server"):
        server._warn_multi_worker(["uvicorn", "src.api.server:app", "--workers", "2"], {})
    records = [r for r in caplog.records if r.name == "src.api.server"]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert "워커 프로세스 2개" in records[0].getMessage()


def test_single_worker_logs_nothing(caplog) -> None:
    from src.api import server

    with caplog.at_level(logging.DEBUG, logger="src.api.server"):
        server._warn_multi_worker(["uvicorn", "src.api.server:app"], {})
    assert [r for r in caplog.records if r.name == "src.api.server"] == []


def test_lifespan_calls_multi_worker_warning() -> None:
    """lifespan 기동 경로가 경고 판정을 부른다(인자 없이 — 실 argv·환경변수를 읽는다)."""
    import inspect

    from src.api import server

    tree = ast.parse(inspect.getsource(server.lifespan))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_warn_multi_worker"]
    assert len(calls) == 1 and not calls[0].args and not calls[0].keywords


# ─── L1 인증 DB 초기화 실패 — 인증 비활성이면 원복 · 활성이면 유지 ──────────


_AUTH_STATE = ("auth_pool", "user_repo", "audit_repo", "auth_provider", "audit_service",
               "thread_repo")


class _AuthDbHarness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, fail_at: str) -> None:
        import asyncpg

        import src.infrastructure.audit_repository as audit_repo_mod
        import src.infrastructure.auth_provider as auth_provider_mod
        import src.infrastructure.thread_repository as thread_repo_mod
        import src.infrastructure.user_repository as user_repo_mod
        import src.security.audit_service as audit_service_mod
        from src.api import server

        self.closed: list[str] = []
        self.fail_at = fail_at
        h = self

        class Pool:
            async def close(self) -> None:
                h.closed.append("auth_pool")

        async def create_pool(dsn: str, min_size: int, max_size: int) -> Pool:
            return Pool()

        class Repo:
            def __init__(self, *a: Any, **k: Any) -> None:
                pass

        class ThreadRepo(Repo):
            async def ensure_tables(self) -> None:
                h._maybe_fail("thread_tables")

        async def ensure_auth_tables(pool: Any) -> None:
            h._maybe_fail("auth_tables")

        async def seed_admin(repo: Any, cfg: Any) -> None:
            h._maybe_fail("seed_admin")

        async def cleanup_once(repo: Any, days: int) -> None:
            return None

        monkeypatch.setattr(asyncpg, "create_pool", create_pool)
        monkeypatch.setattr(user_repo_mod, "PostgresUserRepository", Repo)
        monkeypatch.setattr(audit_repo_mod, "PostgresAuditRepository", Repo)
        monkeypatch.setattr(auth_provider_mod, "LocalAuthProvider", Repo)
        monkeypatch.setattr(audit_service_mod, "AuditService", Repo)
        monkeypatch.setattr(thread_repo_mod, "PostgresThreadRepository", ThreadRepo)
        monkeypatch.setattr(server, "_ensure_auth_tables", ensure_auth_tables)
        monkeypatch.setattr(server, "_seed_admin_user", seed_admin)
        monkeypatch.setattr(server, "_cleanup_audit_once", cleanup_once)

    def _maybe_fail(self, key: str) -> None:
        if key == self.fail_at:
            raise RuntimeError(f"{key} 실패")


def _auth_config(*, enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(auth_db_url="postgresql://auth", enabled=enabled),
        db_connection_string="",
        audit=SimpleNamespace(retention_days=30),
    )


@pytest.mark.parametrize("fail_at", ["auth_tables", "thread_tables", "seed_admin"])
async def test_auth_db_failure_reverts_state_and_closes_pool(
    fail_at: str, monkeypatch, caplog
) -> None:
    """인증 비활성 — 대입한 state 원복 · 풀 닫기 · 감사 로테이션 미시작."""
    from src.api import server

    h = _AuthDbHarness(monkeypatch, fail_at)
    app = SimpleNamespace(state=SimpleNamespace())
    cfg = _auth_config(enabled=False)
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        await server._init_auth_db(app, cfg)  # type: ignore[arg-type]
    assert {name: getattr(app.state, name) for name in _AUTH_STATE} == dict.fromkeys(_AUTH_STATE)
    assert h.closed == ["auth_pool"]
    assert [r.getMessage() for r in caplog.records if r.name == "src.api.server"] == [
        f"인증 DB 초기화 실패 (인증 기능 비활성): {fail_at} 실패"
    ]
    # 감사 로그 로테이션은 시작되지 않는다
    assert server._start_audit_retention(app, cfg) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("fail_at", ["auth_tables", "thread_tables", "seed_admin"])
async def test_auth_db_failure_keeps_state_when_auth_enabled(
    fail_at: str, monkeypatch, caplog
) -> None:
    """인증 활성 — 원복하지 않는다(원복하면 get_current_user가 payload만으로 전 DB 허용)."""
    from src.api import server

    h = _AuthDbHarness(monkeypatch, fail_at)
    app = SimpleNamespace(state=SimpleNamespace())
    cfg = _auth_config(enabled=True)
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        await server._init_auth_db(app, cfg)  # type: ignore[arg-type]
    for name in ("auth_pool", "user_repo", "audit_repo", "auth_provider", "audit_service"):
        assert getattr(app.state, name) is not None
    # 스레드 이력은 대입 지점(인증 DDL 뒤)에 따라 달라진다 — 종전과 같다
    assert (app.state.thread_repo is None) == (fail_at == "auth_tables")
    assert h.closed == []
    assert [r.getMessage() for r in caplog.records if r.name == "src.api.server"] == [
        f"인증 DB 초기화 일부 실패 — 인증 활성이라 사용자 조회 연결은 유지합니다: {fail_at} 실패"
    ]
    # 감사 로그 로테이션은 종전대로 시작된다
    task = server._start_audit_retention(app, cfg)  # type: ignore[arg-type]
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("enabled", [False, True])
async def test_auth_db_pool_failure_keeps_old_warning(
    enabled: bool, monkeypatch, caplog
) -> None:
    """풀 생성 전 실패 — 대입한 값이 없어 인증 활성 여부와 무관하게 종전 문구 · 닫을 풀도 없다."""
    import asyncpg

    from src.api import server

    h = _AuthDbHarness(monkeypatch, fail_at="none")

    async def failing_pool(dsn: str, min_size: int, max_size: int) -> Any:
        raise OSError("connect refused")

    monkeypatch.setattr(asyncpg, "create_pool", failing_pool)
    app = SimpleNamespace(state=SimpleNamespace())
    with caplog.at_level(logging.WARNING, logger="src.api.server"):
        await server._init_auth_db(app, _auth_config(enabled=enabled))  # type: ignore[arg-type]
    assert {name: getattr(app.state, name) for name in _AUTH_STATE} == dict.fromkeys(_AUTH_STATE)
    assert h.closed == []
    assert [r.getMessage() for r in caplog.records if r.name == "src.api.server"] == [
        "인증 DB 초기화 실패 (인증 기능 비활성): connect refused"
    ]


async def test_auth_db_success_keeps_state(monkeypatch) -> None:
    """대조군 — 성공하면 풀을 닫지 않고 state가 남는다."""
    from src.api import server

    h = _AuthDbHarness(monkeypatch, fail_at="none")
    app = SimpleNamespace(state=SimpleNamespace())
    await server._init_auth_db(app, _auth_config(enabled=True))  # type: ignore[arg-type]
    assert all(getattr(app.state, name) is not None for name in _AUTH_STATE)
    assert h.closed == []


# ─── L2 종료 단계 캐시 연결 종료 실패 ───────────────────────────────────────


async def test_shutdown_cache_disconnect_failure_is_swallowed(monkeypatch, caplog) -> None:
    import src.schema_cache.cache_manager as cache_manager_mod
    from src.api import server

    cfg = SimpleNamespace(log_level="INFO", schema_cache=SimpleNamespace(backend="redis"))
    calls: list[str] = []

    async def none_async(*a: Any, **k: Any) -> None:
        return None

    async def init_auth(app: Any, config: Any) -> None:
        app.state.auth_pool = None

    class Cache:
        async def disconnect(self) -> None:
            calls.append("disconnect")
            raise ConnectionError("redis down")

    monkeypatch.setattr(server, "load_config", lambda: cfg)
    monkeypatch.setattr(server, "setup_logging", lambda level: None)
    monkeypatch.setattr(server, "_validate_production_secrets", lambda c: None)
    monkeypatch.setattr(server, "_warn_shared_jwt_secret", lambda c: None)
    monkeypatch.setattr(server, "_warn_multi_worker", lambda: None)
    monkeypatch.setattr(server, "_init_file_logs", lambda c: None)
    monkeypatch.setattr(server, "_build_agent_graph", none_async)
    monkeypatch.setattr(server, "_init_auth_db", init_auth)
    monkeypatch.setattr(server, "_start_audit_retention", lambda app, c: None)
    monkeypatch.setattr(server, "_start_file_log_retention", lambda c: None)
    monkeypatch.setattr(server, "_connect_schema_cache_redis", none_async)
    monkeypatch.setattr(server, "_check_apm_sources", none_async)
    monkeypatch.setattr(server, "_start_alarm_worker", lambda c: None)
    monkeypatch.setattr(server, "_start_sse_bridge", lambda app, c: (None, None, None))

    async def incident(app: Any, c: Any) -> tuple[None, None, None, None]:
        return None, None, None, None

    monkeypatch.setattr(server, "_start_incident_tracking", incident)
    monkeypatch.setattr(cache_manager_mod, "get_cache_manager", lambda c: Cache())

    app = SimpleNamespace(state=SimpleNamespace())
    with caplog.at_level(logging.INFO, logger="src.api.server"):
        async with server.lifespan(app):  # type: ignore[arg-type]
            pass
    assert calls == ["disconnect"]
    logs = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "src.api.server"]
    assert logs[-2:] == [
        ("WARNING", "Redis 스키마 캐시 연결 종료 실패(무시)"),
        ("INFO", "서버 종료"),
    ]


# ─── K7 사전 게이트 선택 대입 1곳 ──────────────────────────────────────────


async def test_pre_gate_writes_authorized_selection_to_body(monkeypatch) -> None:
    """텍스트 진입: 반환값을 버려도 본문에 인가한 선택이 남고, 뒤 게이트가 그 값을 읽는다."""
    from src.api.routes import query as q
    from src.api.schemas import QueryRequest

    seen: list[Any] = []

    def zone(body: Any, *a: Any, **k: Any) -> None:
        seen.append(("zone", list(body.selected_db_ids or [])))
        return None

    def scope(body: Any, *a: Any, **k: Any) -> None:
        seen.append(("scope", list(body.selected_db_ids or [])))
        return None

    async def mem_delete(body: Any, *a: Any, **k: Any) -> None:
        seen.append(("mem", list(body.selected_db_ids or [])))
        return None

    monkeypatch.setattr(q, "_zone_clarification_or_none", zone)
    monkeypatch.setattr(q, "_scope_select_or_none", scope)
    monkeypatch.setattr(q, "_form_memory_delete_or_none", mem_delete)

    body = QueryRequest(query="서버 목록", selected_db_ids=["polestar_b0", "polestar_cm_gp"])
    user = {"sub": "u1", "role": "user", "allowed_db_ids": ["polestar_cm_gp"]}
    _, gate = await q._run_pre_gates(
        body.selected_db_ids, user, SimpleNamespace(), thread_id="t1", body=body,
    )
    assert gate is None
    assert body.selected_db_ids == ["polestar_cm_gp"]
    assert seen == [("zone", ["polestar_cm_gp"]), ("scope", ["polestar_cm_gp"]),
                    ("mem", ["polestar_cm_gp"])]


def test_text_entries_do_not_reassign_selection() -> None:
    """`body.selected_db_ids` 대입은 게이트 안 1곳뿐이다(텍스트 진입 두 곳은 반환값을 버린다)."""
    tree = ast.parse(_QUERY_PY.read_text(encoding="utf-8"))
    writers = sorted({
        fn.name
        for fn in tree.body if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        for n in ast.walk(fn) if isinstance(n, ast.Assign)
        for t in n.targets
        for el in (t.elts if isinstance(t, ast.Tuple) else [t])
        if ast.unparse(el) == "body.selected_db_ids"
    })
    assert writers == ["_run_pre_gates"]
