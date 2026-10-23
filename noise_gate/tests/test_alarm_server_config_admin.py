"""alarm_server 설정 관리 리스너·어댑터 검증 (plans/191 W5 · D-345 · SPEC §3).

CWD는 tmp_path로 옮겨 실제 `alarm_server.env`·`.env` 누수를 막는다. Redis·소켓은 열지 않는다
(TcpReceiver·uvicorn.Server는 가짜로 바꾼다).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import noise_gate.alarm_server.__main__ as main_mod
from noise_gate.alarm_server.config import AlarmServerConfig
from noise_gate.alarm_server.config_admin import build_admin_app
from noise_gate.alarm_server.config_admin_adapter import build_adapter, build_field_specs

ADMIN = "alarm-admin-tok"
AUTH = {"Authorization": f"Bearer {ADMIN}"}
EXAMPLE = Path(main_mod.__file__).resolve().parent / "alarm_server.env.example"
FILE_TEXT = (
    "# 전용 설정\n"
    "ALARM_SERVER_REDIS_HOST=redis.local\n"
    "# 포트\n"
    "ALARM_SERVER_REDIS_PORT=6379\n"
    "ALARM_SERVER_REDIS_PASSWORD=pw-secret-value\n"
)


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    """CWD = tmp_path · ALARM_SERVER_* OS env 제거."""
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.upper().startswith("ALARM_SERVER_"):
            monkeypatch.delenv(key)
    return tmp_path


def _config(**kwargs) -> AlarmServerConfig:
    return AlarmServerConfig(_env_file=None, **kwargs)  # type: ignore[call-arg]


def _client(cwd: Path, **kwargs) -> TestClient:
    adapter = build_adapter(_config(**kwargs), cwd=cwd, audit_path=cwd / "audit.jsonl")
    return TestClient(build_admin_app(adapter, ADMIN))


def _items(body: dict) -> dict[str, dict]:
    return {item["env_key"]: item for group in body["groups"] for item in group["settings"]}


# ── 리스너 기동 조건 ──


def test_unset_port_and_token_means_no_listener(cwd) -> None:
    assert main_mod.build_admin(_config()) is None


@pytest.mark.parametrize(
    "kwargs",
    [{"admin_port": 9101}, {"admin_token": ADMIN}, {"admin_port": 9100, "admin_token": ADMIN}],
)
def test_partial_or_conflicting_admin_settings_refuse_listener(cwd, kwargs, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        assert main_mod.build_admin(_config(socket_port=9100, **kwargs)) is None
    assert "설정 관리 리스너 미기동" in caplog.text


def test_main_without_admin_creates_no_uvicorn(cwd, monkeypatch) -> None:
    """미설정이면 종전 경로 — `asyncio.run(TcpReceiver(config).start())`만, uvicorn 미생성."""
    calls: list[str] = []

    class FakeReceiver:
        def __init__(self, config: object) -> None:
            pass

        async def start(self) -> None:
            calls.append("start")

    def no_server(*_a, **_k):
        raise AssertionError("uvicorn.Server가 만들어지면 안 된다")

    cfg = SimpleNamespace(
        log_level="INFO", stream_key="alarm:raw", socket_port=9100, admin_port=None, admin_token=""
    )
    monkeypatch.setattr(main_mod, "AlarmServerConfig", lambda: cfg)
    monkeypatch.setattr(main_mod, "TcpReceiver", FakeReceiver)
    monkeypatch.setattr(main_mod, "install_log_record_factory", lambda: None)
    monkeypatch.setattr(main_mod.uvicorn, "Server", no_server)
    monkeypatch.setattr(main_mod.uvicorn, "Config", no_server)
    main_mod.main()
    assert calls == ["start"]


class _FakeServer:
    """uvicorn.Server 가짜 — `fail`이면 바인딩 실패처럼 sys.exit, 아니면 should_exit까지 대기."""

    instances: list[_FakeServer] = []

    def __init__(self, config: object, fail: bool = False) -> None:
        self.config = config
        self.fail = fail
        self.should_exit = False
        _FakeServer.instances.append(self)

    async def serve(self) -> None:
        if self.fail:
            raise SystemExit(1)
        while not self.should_exit:
            await asyncio.sleep(0.005)


def _receiver(log: list[str], error: Exception | None = None):
    class FakeReceiver:
        def __init__(self, config: object) -> None:
            pass

        async def start(self) -> None:
            await asyncio.sleep(0.02)
            log.append("tcp_done")
            if error is not None:
                raise error

    return FakeReceiver


def _admin_cfg(cwd: Path) -> AlarmServerConfig:
    return _config(admin_port=9101, admin_token=ADMIN, admin_host="127.0.0.1")


def test_admin_listener_runs_alongside_tcp_and_stops_with_it(cwd, monkeypatch) -> None:
    log: list[str] = []
    _FakeServer.instances.clear()
    monkeypatch.setattr(main_mod, "TcpReceiver", _receiver(log))
    monkeypatch.setattr(main_mod.uvicorn, "Server", _FakeServer)
    cfg = _admin_cfg(cwd)
    admin = main_mod.build_admin(cfg)
    assert admin is not None
    asyncio.run(main_mod.serve_with_admin(cfg, admin))
    assert log == ["tcp_done"]
    server = _FakeServer.instances[-1]
    assert server.should_exit is True
    assert (server.config.host, server.config.port) == ("127.0.0.1", 9101)


def test_admin_listener_failure_does_not_kill_tcp(cwd, monkeypatch, caplog) -> None:
    log: list[str] = []
    monkeypatch.setattr(main_mod, "TcpReceiver", _receiver(log))
    monkeypatch.setattr(main_mod.uvicorn, "Server", lambda config: _FakeServer(config, fail=True))
    cfg = _admin_cfg(cwd)
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        asyncio.run(main_mod.serve_with_admin(cfg, main_mod.build_admin(cfg)))
    assert log == ["tcp_done"]
    assert "설정 관리 리스너 실패" in caplog.text


def test_tcp_failure_propagates_and_stops_admin(cwd, monkeypatch) -> None:
    log: list[str] = []
    _FakeServer.instances.clear()
    monkeypatch.setattr(main_mod, "TcpReceiver", _receiver(log, RuntimeError("redis down")))
    monkeypatch.setattr(main_mod.uvicorn, "Server", _FakeServer)
    cfg = _admin_cfg(cwd)
    with pytest.raises(RuntimeError, match="redis down"):
        asyncio.run(main_mod.serve_with_admin(cfg, main_mod.build_admin(cfg)))
    assert _FakeServer.instances[-1].should_exit is True


# ── 관리 앱 · 어댑터 ──


def test_unauthorized(cwd) -> None:
    client = _client(cwd)
    assert client.get("/admin/config/schema").status_code == 401
    bad = {"Authorization": "Bearer nope"}
    assert client.put("/admin/config", json={"settings": {}}, headers=bad).status_code == 401


def test_missing_dedicated_file_blocks_writes(cwd) -> None:
    client = _client(cwd)
    resp = client.put(
        "/admin/config", json={"settings": {"ALARM_SERVER_REDIS_PORT": "6380"}}, headers=AUTH
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "write_blocked"
    assert "cp noise_gate/alarm_server/alarm_server.env.example alarm_server.env" in body["reason"]
    assert not (cwd / "alarm_server.env").exists()
    schema = client.get("/admin/config/schema", headers=AUTH).json()
    assert schema["writable"] is False and schema["write_block_reason"]


def test_dedicated_file_beats_dotenv_in_loader(cwd) -> None:
    """같은 키가 둘 다에 있으면 전용 파일이 이긴다 · 없는 키는 `.env`로 보충된다(D-345 부기)."""
    (cwd / "alarm_server.env").write_text("ALARM_SERVER_REDIS_PORT=6380\n", encoding="utf-8")
    (cwd / ".env").write_text(
        "ALARM_SERVER_REDIS_PORT=6390\nALARM_SERVER_REDIS_DB=5\n", encoding="utf-8"
    )
    loaded = AlarmServerConfig()
    assert loaded.redis_port == 6380 and loaded.redis_db == 5


def test_dotenv_key_is_writable_and_dedicated_file_wins(cwd) -> None:
    """`.env`는 보충 원천이다 — `.env`에 있는 키도 저장되고 전용 파일 값이 적용된다."""
    target = cwd / "alarm_server.env"
    target.write_text(FILE_TEXT, encoding="utf-8")
    (cwd / ".env").write_text("ALARM_SERVER_REDIS_PORT=6390\n", encoding="utf-8")
    client = _client(cwd)
    schema = client.get("/admin/config/schema", headers=AUTH).json()
    item = _items(schema)["ALARM_SERVER_REDIS_PORT"]
    assert item["override"] is None and item["writable"] is True
    roles = {src["role"]: src["priority"] for src in schema["sources"]}
    assert roles["fallback"] < roles["write_target"]
    body = client.put(
        "/admin/config", json={"settings": {"ALARM_SERVER_REDIS_PORT": "6381"}}, headers=AUTH
    ).json()
    assert body["saved_keys"] == ["ALARM_SERVER_REDIS_PORT"] and body["ignored_keys"] == []
    assert AlarmServerConfig().redis_port == 6381


def test_os_env_is_override_os(cwd, monkeypatch) -> None:
    (cwd / "alarm_server.env").write_text(FILE_TEXT, encoding="utf-8")
    monkeypatch.setenv("ALARM_SERVER_LOG_LEVEL", "WARNING")
    body = (
        _client(cwd)
        .put("/admin/config", json={"settings": {"ALARM_SERVER_LOG_LEVEL": "DEBUG"}}, headers=AUTH)
        .json()
    )
    assert body["ignored_keys"] == [{"key": "ALARM_SERVER_LOG_LEVEL", "reason": "override_os"}]


def test_password_is_set_only_and_readonly_keys(cwd) -> None:
    (cwd / "alarm_server.env").write_text(FILE_TEXT, encoding="utf-8")
    client = _client(cwd, redis_password="pw-secret-value", admin_token=ADMIN)
    resp = client.get("/admin/config/schema", headers=AUTH)
    assert "pw-secret-value" not in resp.text and ADMIN not in resp.text
    items = _items(resp.json())
    pw = items["ALARM_SERVER_REDIS_PASSWORD"]
    assert pw["is_secret"] and pw["is_set"] and pw["file_value"] is None
    assert pw["effective_value"] is None and pw["writable"] is False
    assert items["ALARM_SERVER_ADMIN_TOKEN"]["is_secret"] is True
    for key in ("ALARM_SERVER_SOCKET_HOST", "ALARM_SERVER_ADMIN_HOST", "ALARM_SERVER_ADMIN_PORT"):
        assert items[key]["readonly_reason"] == "readonly"
    body = client.put(
        "/admin/config",
        json={"settings": {"ALARM_SERVER_REDIS_PASSWORD": "new", "ALARM_SERVER_SOCKET_HOST": "x"}},
        headers=AUTH,
    ).json()
    assert {i["key"]: i["reason"] for i in body["ignored_keys"]} == {
        "ALARM_SERVER_REDIS_PASSWORD": "secret",
        "ALARM_SERVER_SOCKET_HOST": "readonly",
    }


def test_load_target_keys_readonly_before_override(cwd, monkeypatch) -> None:
    """적재 대상(Redis 호스트·스트림 키)은 읽기 전용 — override보다 readonly가 먼저다."""
    (cwd / "alarm_server.env").write_text(FILE_TEXT, encoding="utf-8")
    (cwd / ".env").write_text("ALARM_SERVER_REDIS_HOST=other\n", encoding="utf-8")
    monkeypatch.setenv("ALARM_SERVER_STREAM_KEY", "alarm:other")
    client = _client(cwd)
    items = _items(client.get("/admin/config/schema", headers=AUTH).json())
    for key in ("ALARM_SERVER_REDIS_HOST", "ALARM_SERVER_STREAM_KEY"):
        assert items[key]["readonly_reason"] == "readonly" and items[key]["writable"] is False
    body = client.put(
        "/admin/config",
        json={"settings": {"ALARM_SERVER_REDIS_HOST": "evil", "ALARM_SERVER_STREAM_KEY": "x"}},
        headers=AUTH,
    ).json()
    assert {i["key"]: i["reason"] for i in body["ignored_keys"]} == {
        "ALARM_SERVER_REDIS_HOST": "readonly",
        "ALARM_SERVER_STREAM_KEY": "readonly",
    }
    assert (cwd / "alarm_server.env").read_text(encoding="utf-8") == FILE_TEXT


def test_dry_run_failure_leaves_file_unchanged(cwd) -> None:
    """dry-run은 실 로더(`[cwd/.env, 후보]`)로 읽는다 — `.env`가 깨져 있으면 저장하지 않는다."""
    target = cwd / "alarm_server.env"
    target.write_text(FILE_TEXT, encoding="utf-8")
    (cwd / ".env").write_text("ALARM_SERVER_REDIS_DB=not-a-number\n", encoding="utf-8")
    resp = _client(cwd).put(
        "/admin/config", json={"settings": {"ALARM_SERVER_REDIS_PORT": "6380"}}, headers=AUTH
    )
    assert resp.status_code == 400
    assert any("redis_db" in e["message"] for e in resp.json()["errors"])
    assert "not-a-number" not in resp.text
    assert target.read_text(encoding="utf-8") == FILE_TEXT
    assert not (cwd / "alarm_server.env.bak").exists()


def test_save_preserves_lines_and_marks_pending(cwd) -> None:
    target = cwd / "alarm_server.env"
    target.write_text(FILE_TEXT, encoding="utf-8")
    client = _client(cwd)
    resp = client.put(
        "/admin/config",
        json={"settings": {"ALARM_SERVER_REDIS_PORT": "6380", "ALARM_SERVER_LOG_LEVEL": "DEBUG"}},
        headers={**AUTH, "X-Request-ID": "req-1"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved_keys"] == ["ALARM_SERVER_REDIS_PORT", "ALARM_SERVER_LOG_LEVEL"]
    assert body["restart_required"] is True
    assert set(body["restart_pending_keys"]) == {
        "ALARM_SERVER_REDIS_PORT",
        "ALARM_SERVER_LOG_LEVEL",
    }
    text = target.read_text(encoding="utf-8")
    assert text == FILE_TEXT.replace("REDIS_PORT=6379", "REDIS_PORT=6380") + (
        "ALARM_SERVER_LOG_LEVEL=DEBUG\n"
    )
    assert (cwd / "alarm_server.env.bak").read_text(encoding="utf-8") == FILE_TEXT
    loaded = AlarmServerConfig(_env_file=[target])  # type: ignore[call-arg]
    assert loaded.redis_port == 6380 and loaded.log_level == "DEBUG"
    audit = [json.loads(line) for line in (cwd / "audit.jsonl").read_text().splitlines()]
    assert audit[-1]["result"] == "saved" and audit[-1]["request_id"] == "req-1"
    assert audit[-1]["component"] == "alarm_server"


def test_real_paths_default_to_cwd(cwd) -> None:
    adapter = build_adapter(_config())
    assert adapter.write_target == cwd.resolve() / "alarm_server.env"
    assert adapter.audit_path.parent == cwd.resolve() / "logs"


def test_field_table_covers_env_example() -> None:
    """필드 표 ⊇ 예시 키 − 시크릿(주석 처리된 `# KEY=` 포함)."""
    specs = {s.env_key: s for s in build_field_specs()}
    keys = set(
        re.findall(r"^#?\s*(ALARM_SERVER_[A-Z_]+)=", EXAMPLE.read_text(encoding="utf-8"), re.M)
    )
    non_secret = {k for k in keys if not (k in specs and specs[k].is_secret)}
    assert non_secret <= set(specs)
    assert {
        "ALARM_SERVER_ADMIN_PORT",
        "ALARM_SERVER_ADMIN_HOST",
        "ALARM_SERVER_ADMIN_TOKEN",
    } <= keys
    assert specs["ALARM_SERVER_ADMIN_PORT"].type == "int"
