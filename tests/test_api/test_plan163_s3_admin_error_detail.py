"""plans/163 S3 관리자 경로 — 예외 원문은 서버 로그에만, 응답은 확정 A안 문구.

확정 문구(§2.1a #2): 「{작업} 실패했습니다 ({예외 종류} · 요청 {request_id}) — 상세는 서버 로그」.
위치마다 원문(`dsn=secret…`)을 담은 예외를 주입해 ① 응답에 원문이 없고 ② 예외 종류·요청 번호·
「상세는 서버 로그」가 있으며 ③ 서버 로그(caplog)에 원문이 남는지 본다.
`admin.py` 운영 필수 설정 누락(키 이름만 담는 메시지)은 원문 유지가 확정이라 따로 단언한다.

- 실 `.env`·DB·Redis·LLM 0: 라우트가 부르는 지점을 가짜로 바꾼다(D-127).
- 요청 번호: `request`를 받는 라우트는 `request.state.request_id`, 받지 않는 라우트는
  structlog 컨텍스트(감사 미들웨어가 바인딩하는 값)에서 읽는다.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noise_gate.domain.alarm import AlarmAnalysisResult
from src.api.routes import admin as admin_routes
from src.api.routes import alarm as alarm_routes
from src.api.routes import schema_cache as schema_cache_routes
from src.api.routes.admin import (
    DbConfigUpdateRequest,
    DbTestRequest,
    EnvUpdateRequest,
    get_audit_logs_paginated,
    get_audit_stats,
    get_settings_schema,
    reload_settings,
    update_db_config,
    update_settings,
)
from src.api.routes.admin import (
    test_db_connection as db_connection_route,
)
from src.config import AdminConfig, AppConfig, AuthConfig, LLMConfig

_ADMIN = {"sub": "admin"}
_SECRET = "dsn=secret://admin:pw@10.0.0.9"
_RID = "rid163"


def _assert_masked(text: str, exc_type: str, action: str, rid: str = _RID) -> None:
    """응답 문구가 A안 형식이고 예외 원문을 담지 않는다."""
    assert _SECRET not in text
    assert "secret" not in text
    assert text == f"{action} 실패했습니다 ({exc_type} · 요청 {rid}) — 상세는 서버 로그"


def _assert_logged(caplog: pytest.LogCaptureFixture) -> None:
    """원문은 서버 로그(스택 포함)에 남는다."""
    assert _SECRET in caplog.text


def _request(config: Any = None, audit: Any = None, audit_repo: Any = None) -> SimpleNamespace:
    """라우트 직접 호출용 가짜 Request — 감사 미들웨어가 넣는 `request_id`를 싣는다."""
    state = SimpleNamespace(
        config=config,
        graph=SimpleNamespace(checkpointer=None, name="old-graph"),
        checkpointer=object(),
        audit_service=audit,
        audit_repo=audit_repo,
    )
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="127.0.0.1", request_id=_RID),
    )


@pytest.fixture
def env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("LLM_MODEL=m\nACTIVE_DB_IDS=polestar\n", encoding="utf-8")
    monkeypatch.setattr("src.api.routes.admin._ENV_FILE", path)
    return path


@pytest.fixture
def ctx_rid():
    """`request`를 받지 않는 라우트용 — structlog 컨텍스트에 요청 번호를 바인딩한다."""
    structlog.contextvars.bind_contextvars(request_id=_RID)
    yield _RID
    structlog.contextvars.unbind_contextvars("request_id")


# ─── admin.py — 설정 저장 · 준비도 · 실효값 ───────────────────────────


async def test_readiness_error_hides_exception(env_file, monkeypatch, caplog):
    """준비도 판정 실패 — `_error`에 원문 대신 A안 문구(저장은 진행)."""

    class _Broken:
        async def readiness_many(self, db_ids: list[str]) -> dict:
            raise RuntimeError(_SECRET)

    monkeypatch.setattr(
        "src.api.routes.db_structure.build_registration_service", lambda cfg: _Broken()
    )
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        response = await update_settings(
            _request(config="running-config"),
            EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
            _ADMIN,
        )
    assert "ACTIVE_DB_IDS=polestar,itam" in env_file.read_text(encoding="utf-8")
    _assert_masked(response.readiness_warnings["_error"], "RuntimeError", "준비도 판정")
    _assert_logged(caplog)


async def test_settings_save_error_hides_exception(env_file, monkeypatch, caplog):
    """설정 저장(.env 원자 교체) 실패 — 500 detail에 원문 없음."""

    def broken(content: str) -> None:
        raise OSError(_SECRET)

    monkeypatch.setattr(admin_routes, "_atomic_write_env", broken)
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        with pytest.raises(HTTPException) as exc:
            await update_settings(
                _request(), EnvUpdateRequest(settings={"LLM_MODEL": "m2"}), _ADMIN
            )
    assert exc.value.status_code == 500
    _assert_masked(exc.value.detail, "OSError", "설정 저장")
    _assert_logged(caplog)


async def test_settings_schema_warning_hides_exception(
    env_file, monkeypatch, caplog, ctx_rid
):
    """실효값 계산 실패 — warnings에 원문 없음(요청 번호는 structlog 컨텍스트)."""

    def broken(*args: Any, **kwargs: Any) -> AppConfig:
        raise ValueError(_SECRET)

    monkeypatch.setattr("src.config.AppConfig", broken)
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        response = await get_settings_schema(_ADMIN)
    masked = [w for w in response.warnings if "현재 적용값 계산" in w]
    assert len(masked) == 1
    assert all(_SECRET not in w for w in response.warnings)
    _assert_masked(masked[0], "ValueError", "현재 적용값 계산")
    _assert_logged(caplog)


def test_request_id_falls_back_to_dash_without_context(monkeypatch, caplog):
    """요청 번호를 찾을 곳이 없으면(라우트 직접 호출 · 미들웨어 밖) 「-」로 낸다."""
    structlog.contextvars.unbind_contextvars("request_id")
    from src.api.error_detail import admin_error_detail

    text = admin_error_detail("설정 저장", OSError(_SECRET), admin_routes.logger)
    _assert_masked(text, "OSError", "설정 저장", rid="-")


# ─── admin.py — 설정 리로드 ──────────────────────────────────────────


class _FakeLoadConfig:
    """lru_cache 인터페이스(cache_clear)를 흉내 내는 load_config 대역."""

    def __init__(self, config: Any) -> None:
        self.config = config

    def __call__(self) -> Any:
        if isinstance(self.config, Exception):
            raise self.config
        return self.config

    def cache_clear(self) -> None:
        return None


def _config(**overrides: Any) -> AppConfig:
    overrides.setdefault("auth", AuthConfig(_env_file=None))
    overrides.setdefault("admin", AdminConfig(_env_file=None))
    return AppConfig(_env_file=None, **overrides)


async def test_reload_load_error_hides_exception(monkeypatch, caplog):
    """설정 로드 실패 — 400 detail에 원문 없음 · 기존 설정 유지."""
    old = _config()
    monkeypatch.setattr("src.config.load_config", _FakeLoadConfig(ValueError(_SECRET)))
    request = _request(config=old)
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        with pytest.raises(HTTPException) as exc:
            await reload_settings(request, _ADMIN)
    assert exc.value.status_code == 400
    suffix = " · 기존 설정이 유지됩니다"
    assert exc.value.detail.endswith(suffix)
    _assert_masked(exc.value.detail.removesuffix(suffix), "ValueError", "설정 로드")
    assert request.app.state.config is old
    _assert_logged(caplog)


async def test_reload_build_error_hides_exception(monkeypatch, caplog):
    """그래프 재빌드 실패 — 500 detail에 원문 없음 · 기존 설정 유지."""
    old = _config(llm=LLMConfig(_env_file=None, model="m-old"))
    fresh = _config(llm=LLMConfig(_env_file=None, model="m-new"))
    monkeypatch.setattr("src.config.load_config", _FakeLoadConfig(fresh))

    def broken_build(config: Any, checkpointer: Any = None) -> Any:
        raise RuntimeError(_SECRET)

    monkeypatch.setattr("src.graph.build_graph", broken_build)
    request = _request(config=old)
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        with pytest.raises(HTTPException) as exc:
            await reload_settings(request, _ADMIN)
    assert exc.value.status_code == 500
    suffix = " · 기존 설정이 유지됩니다"
    assert exc.value.detail.endswith(suffix)
    _assert_masked(exc.value.detail.removesuffix(suffix), "RuntimeError", "그래프 재빌드")
    assert request.app.state.config is old
    _assert_logged(caplog)


async def test_reload_production_gate_keeps_key_names(monkeypatch):
    """운영 필수 설정 누락(키 이름만 담는 메시지)은 원문 유지 — 확정 예외."""
    old = _config()
    monkeypatch.setattr("src.config.load_config", _FakeLoadConfig(_config()))

    def gate(config: Any) -> None:
        raise RuntimeError("필수 설정 누락: AUTH_JWT_SECRET")

    monkeypatch.setattr("src.api.server._validate_production_secrets", gate)
    with pytest.raises(HTTPException) as exc:
        await reload_settings(_request(config=old), _ADMIN)
    assert exc.value.status_code == 400
    assert exc.value.detail == (
        "운영 모드 필수 설정 검증에 실패했습니다 — 기존 설정이 유지됩니다: "
        "필수 설정 누락: AUTH_JWT_SECRET"
    )


# ─── admin.py — DB 설정 · 연결 테스트 ─────────────────────────────────


def _db_body(cls: type) -> Any:
    return cls(
        db_type="postgresql", host="10.0.0.9", port=5432,
        database="d", username="admin", password="pw",
    )


async def test_db_config_save_error_hides_exception(monkeypatch, caplog, ctx_rid):
    """DB 설정 저장 실패 — 500 detail에 원문 없음."""

    def broken(body: Any) -> str:
        raise RuntimeError(_SECRET)

    monkeypatch.setattr(admin_routes, "_build_connection_string", broken)
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        with pytest.raises(HTTPException) as exc:
            await update_db_config(_db_body(DbConfigUpdateRequest), _ADMIN)
    assert exc.value.status_code == 500
    _assert_masked(exc.value.detail, "RuntimeError", "DB 설정 저장")
    _assert_logged(caplog)


async def test_db_connection_test_error_hides_exception(monkeypatch, caplog, ctx_rid):
    """DB 연결 테스트 실패 — `message`에 원문 없음 · `details` 없음."""

    async def broken_connect(**kwargs: Any) -> Any:
        raise ConnectionError(_SECRET)

    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(connect=broken_connect))
    with caplog.at_level(logging.ERROR, logger=admin_routes.logger.name):
        response = await db_connection_route(_db_body(DbTestRequest), _ADMIN)
    assert response.success is False
    # 예외 분기는 message 하나로 끝난다(미지원 DB·드라이버 미설치 분기는 종전 message+details)
    assert response.details is None
    _assert_masked(response.message, "ConnectionError", "DB 연결 테스트")
    _assert_logged(caplog)


# ─── admin.py — 감사 로그 날짜 400 ───────────────────────────────────


def _assert_input_error_logged(caplog: pytest.LogCaptureFixture) -> None:
    """입력 오류(`input_error=True`)는 스택 없이 WARNING으로 남는다."""
    mine = [r for r in caplog.records if r.name == admin_routes.logger.name]
    assert [r.levelno for r in mine] == [logging.WARNING]
    assert mine[0].exc_info is None


class _BrokenAuditRepo:
    async def query_logs_paginated(self, **kwargs: Any) -> Any:
        raise ValueError(_SECRET)

    async def get_stats(self, **kwargs: Any) -> Any:
        raise ValueError(_SECRET)


async def test_audit_logs_date_error_same_format(caplog):
    """감사 로그 조회 날짜 오류(400) — 화면 미표시지만 문구는 같은 형식."""
    request = _request(audit_repo=_BrokenAuditRepo())
    with caplog.at_level(logging.WARNING, logger=admin_routes.logger.name):
        with pytest.raises(HTTPException) as exc:
            await get_audit_logs_paginated(
                request, start_date="x", end_date=None, user_id=None, event_type=None,
                target_db=None, success=None, keyword=None, page=1, page_size=50,
                _admin=_ADMIN,
            )
    assert exc.value.status_code == 400
    _assert_masked(exc.value.detail, "ValueError", "감사 로그 날짜 해석")
    _assert_logged(caplog)
    _assert_input_error_logged(caplog)


async def test_audit_stats_date_error_same_format(caplog):
    """감사 통계 날짜 오류(400) — 문구는 같은 형식."""
    request = _request(audit_repo=_BrokenAuditRepo())
    with caplog.at_level(logging.WARNING, logger=admin_routes.logger.name):
        with pytest.raises(HTTPException) as exc:
            await get_audit_stats(request, start_date="x", end_date=None, _admin=_ADMIN)
    assert exc.value.status_code == 400
    _assert_masked(exc.value.detail, "ValueError", "감사 통계 날짜 해석")
    _assert_logged(caplog)
    _assert_input_error_logged(caplog)


# ─── schema_cache.py — 캐시·설명 생성 ────────────────────────────────


def _rid_app(router: Any, config: Any) -> TestClient:
    """라우터 하나만 올린 앱 — 감사 미들웨어처럼 `request.state.request_id`를 넣는다."""
    app = FastAPI()

    @app.middleware("http")
    async def _rid(request: Any, call_next: Any) -> Any:
        request.state.request_id = _RID
        return await call_next(request)

    app.include_router(router, prefix="/api/v1")
    app.state.config = config
    return TestClient(app)


@pytest.fixture
def cache_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config = SimpleNamespace(multi_db=SimpleNamespace(get_active_db_ids=lambda: []))
    monkeypatch.setattr("src.config.load_config", lambda: config)
    monkeypatch.setattr(
        "src.schema_cache.cache_manager.get_cache_manager", lambda cfg=None: object()
    )
    return _rid_app(
        schema_cache_routes.router, SimpleNamespace(auth=SimpleNamespace(enabled=False))
    )


def test_cache_generate_error_hides_exception(cache_client, monkeypatch, caplog):
    """캐시 생성 실패 — 결과 항목 message에 원문 없음."""

    def broken_client(cfg: Any, db_id: str | None = None) -> Any:
        raise ConnectionError(_SECRET)

    monkeypatch.setattr("src.db.get_db_client", broken_client)
    with caplog.at_level(logging.ERROR, logger=schema_cache_routes.logger.name):
        response = cache_client.post(
            "/api/v1/admin/schema-cache/generate", json={"db_ids": ["db_a"]}
        )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] == "error"
    _assert_masked(item["message"], "ConnectionError", "스키마 캐시 생성")
    _assert_logged(caplog)


def test_cache_descriptions_error_hides_exception(cache_client, monkeypatch, caplog):
    """컬럼 설명 생성 실패 — 결과 항목 message에 원문 없음."""

    async def broken(db_id: str, cache_mgr: Any, config: Any) -> int:
        raise RuntimeError(_SECRET)

    monkeypatch.setattr(schema_cache_routes, "_generate_descriptions_for_db", broken)
    with caplog.at_level(logging.ERROR, logger=schema_cache_routes.logger.name):
        response = cache_client.post(
            "/api/v1/admin/schema-cache/generate-descriptions", json={"db_ids": ["db_a"]}
        )
    assert response.status_code == 200, response.text
    item = response.json()["results"][0]
    assert item["status"] == "error"
    _assert_masked(item["message"], "RuntimeError", "컬럼 설명 생성")
    _assert_logged(caplog)


# ─── alarm.py — 알람 분석 테스트 ─────────────────────────────────────

_RAW = "/api/v1/alarm/analyze-test/raw"
_STRUCTURED = "/api/v1/alarm/analyze-test"
_PAYLOAD = {
    "dbId": "polestar_pg",
    "serverName": "s3-test",
    "hostname": "s3-test",
    "ipAddress": "10.0.0.1",
    "resourceAncestry": "",
    "alarmId": "S3-1",
    "severity": 2,
    "alarmStatus": "NOT_ACK",
    "resourceType": "server.Server",
    "resourceName": "CPU",
    "alarmName": "S3 회귀",
    "alarmTime": "20261014090000",
    "conditions": "cpu>90",
    "conditionLog": "cpu=95",
}
_COMMON = {"push_to_ui": False, "query_history": False, "query_process": False}
_BODIES = {
    _RAW: {"message": json.dumps(_PAYLOAD, ensure_ascii=False), **_COMMON},
    _STRUCTURED: {"db_id": "polestar_pg", "server_name": "s3-test", "alarm_name": "S3 회귀",
                  **_COMMON},
}
_SEND = {"dry_run": False, "send_notification": True, "channels": []}


async def _ok_analyzer(state: dict, config: Any) -> dict:
    return {
        "analysis_result": AlarmAnalysisResult(
            alarm_event=state["alarm_event"],
            severity_label="경고",
            summary="요약",
            probable_cause="원인",
            recommended_action="조치",
            notification_channels=[],
        )
    }


async def _broken_node(state: dict, config: Any) -> dict:
    raise RuntimeError(_SECRET)


@pytest.fixture
def alarm_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.alarm_analyzer_node", _ok_analyzer
    )
    config = SimpleNamespace(
        auth=SimpleNamespace(enabled=False),
        alarm=SimpleNamespace(
            get_notification_channels=lambda: [],
            history_enabled=False,
            process_enrich_enabled=False,
        ),
        workb=SimpleNamespace(),
        noise_gate=SimpleNamespace(
            enable_noise_gate=False,
            enable_llm_actionability=False,
            decision_store_path=str(tmp_path / "decisions.jsonl"),
        ),
    )
    return _rid_app(alarm_routes.router, config)


@pytest.mark.parametrize("route", [_RAW, _STRUCTURED])
def test_alarm_analyze_error_hides_exception(alarm_client, monkeypatch, caplog, route):
    """알람 분석 노드 실패 — 응답 `error`에 원문 없음."""
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_analyzer.alarm_analyzer_node", _broken_node
    )
    with caplog.at_level(logging.ERROR, logger=alarm_routes.logger.name):
        response = alarm_client.post(route, json=_BODIES[route])
    assert response.status_code == 200, response.text
    _assert_masked(response.json()["error"], "RuntimeError", "알람 분석")
    _assert_logged(caplog)


@pytest.mark.parametrize("route", [_RAW, _STRUCTURED])
def test_alarm_notify_error_hides_exception(alarm_client, monkeypatch, caplog, route):
    """알림 발송 실패(dry_run=false) — 응답 `error`에 원문 없음."""
    monkeypatch.setattr(
        "noise_gate.application.nodes.alarm_notifier.alarm_notifier_node", _broken_node
    )
    with caplog.at_level(logging.ERROR, logger=alarm_routes.logger.name):
        response = alarm_client.post(route, json={**_BODIES[route], **_SEND})
    assert response.status_code == 200, response.text
    _assert_masked(response.json()["error"], "RuntimeError", "알림 발송")
    _assert_logged(caplog)


def test_alarm_raw_parse_error_same_format(alarm_client, caplog):
    """원문 파싱 실패 — JSON 오류 메시지(위치 정보) 대신 A안 문구 · 원문은 로그."""
    with caplog.at_level(logging.ERROR, logger=alarm_routes.logger.name):
        response = alarm_client.post(_RAW, json={"message": "{not json", **_COMMON})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["alarm_id"] == "PARSE-ERROR"
    assert body["error"] == (
        f"메시지 파싱 실패했습니다 (JSONDecodeError · 요청 {_RID}) — 상세는 서버 로그"
    )
    assert "Expecting" not in body["error"]
    assert "Expecting" in caplog.text


# ─── cache_manager.py — 캐시 갱신 결과 문구(라우트 `refresh_result.message`로 나감) ──────


async def test_cache_refresh_error_message_hides_exception(caplog, ctx_rid, tmp_path):
    """DB 연결 오류 원문(호스트·계정)은 갱신 결과 message에 싣지 않고 로그로만."""
    from unittest.mock import MagicMock

    from src.schema_cache import cache_manager as cm

    config = MagicMock()
    config.schema_cache.backend = "file"
    config.schema_cache.cache_dir = str(tmp_path)
    config.schema_cache.enabled = True

    class _BrokenClient:
        async def execute_sql(self, sql: str) -> Any:
            raise ConnectionError(_SECRET)

    with caplog.at_level(logging.ERROR, logger=cm.logger.name):
        result = await cm.SchemaCacheManager(config).refresh_cache("db_a", _BrokenClient())
    assert result.status == "error"
    _assert_masked(result.message, "ConnectionError", "스키마 캐시 갱신")
    _assert_logged(caplog)
