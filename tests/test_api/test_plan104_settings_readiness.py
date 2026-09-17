"""plans/104 B-7 · G-7 (a) — `PUT /admin/settings`의 ACTIVE_DB_IDS 준비도 경고.

- 실 `.env`는 건드리지 않는다: `src.api.routes.admin._ENV_FILE`을 tmp_path로 치환한다
  (기존 설정 저장 테스트와 같은 방식).
- 준비도 판정은 `db_structure.build_registration_service`를 가짜로 바꾼다(실 MCP·Redis 0).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.api.routes import admin as admin_routes
from src.api.routes.admin import EnvUpdateRequest, update_settings
from src.domain.db_readiness import ReadinessItem, ReadinessReport

_ADMIN = {"sub": "admin"}


class _CapturingAuditService:
    def __init__(self) -> None:
        self.entries: list[Any] = []

    async def log(self, entry: Any) -> None:
        self.entries.append(entry)


def _report(db_id: str, unmet: list[tuple[str, str, str]]) -> ReadinessReport:
    items = [
        ReadinessItem(code=code, label=label, grade="required", ok=False, detail=detail)
        for code, label, detail in unmet
    ]
    items.append(
        ReadinessItem(code="C8", label="컬럼 설명", grade="recommended", ok=False, detail="권장")
    )
    return ReadinessReport(
        db_id=db_id,
        items=tuple(items),
        required_met=7 - len(unmet),
        required_total=7,
        recommended_met=0,
        recommended_total=2,
    )


class _FakeRegistrationService:
    def __init__(self, reports: dict[str, ReadinessReport] | Exception, delay: float = 0.0) -> None:
        self.reports = reports
        self.delay = delay
        self.asked: list[list[str]] = []

    async def readiness_many(self, db_ids: list[str]) -> dict[str, ReadinessReport]:
        self.asked.append(list(db_ids))
        if self.delay:
            await asyncio.sleep(self.delay)
        if isinstance(self.reports, Exception):
            raise self.reports
        return {db_id: self.reports[db_id] for db_id in db_ids if db_id in self.reports}


@pytest.fixture
def env_file(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("LLM_MODEL=m\nACTIVE_DB_IDS=polestar\n", encoding="utf-8")
    monkeypatch.setattr("src.api.routes.admin._ENV_FILE", path)
    return path


def _install_service(monkeypatch, service: _FakeRegistrationService) -> list[Any]:
    configs: list[Any] = []

    def factory(config: Any) -> _FakeRegistrationService:
        configs.append(config)
        return service

    monkeypatch.setattr("src.api.routes.db_structure.build_registration_service", factory)
    return configs


def _request(audit: Any = None, config: Any = "running-config") -> SimpleNamespace:
    state = SimpleNamespace(audit_service=audit, audit_repo=None)
    if config is not None:
        state.config = config
    return SimpleNamespace(
        app=SimpleNamespace(state=state),
        state=SimpleNamespace(client_ip="127.0.0.1", request_id="req-1"),
    )


async def test_unmet_new_db_warns_but_saves(env_file, monkeypatch):
    """필수 미충족 DB를 추가하면 경고만 싣고 `.env`는 그대로 저장한다(G-7 (a))."""
    service = _FakeRegistrationService(
        {
            "itam": _report(
                "itam",
                [
                    ("C6", "구조 정보", "수동 프로필·승인본 없음"),
                    ("C5", "스키마 캐시", "캐시 없음"),
                ],
            ),
        }
    )
    configs = _install_service(monkeypatch, service)
    audit = _CapturingAuditService()

    response = await update_settings(
        _request(audit),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )

    assert "ACTIVE_DB_IDS=polestar,itam" in env_file.read_text(encoding="utf-8")
    assert service.asked == [["itam"]]  # 새로 추가된 DB만 판정한다
    assert configs == ["running-config"]  # 실행 중 프로세스 설정으로 판정한다
    assert response.readiness_warnings == {
        "itam": [
            {"code": "C6", "label": "구조 정보", "detail": "수동 프로필·승인본 없음"},
            {"code": "C5", "label": "스키마 캐시", "detail": "캐시 없음"},
        ],
    }
    assert "itam" in response.message and "저장은 완료" in response.message
    # 감사 extra에 경고 사실이 남는다
    assert audit.entries[0].extra["readiness_warnings"] == response.readiness_warnings


async def test_ready_db_only_behaves_as_before(env_file, monkeypatch):
    """필수를 모두 충족한 DB만 추가하면 경고 없음 — 응답·감사가 종전과 같다."""
    service = _FakeRegistrationService({"itam": _report("itam", [])})
    _install_service(monkeypatch, service)
    audit = _CapturingAuditService()

    response = await update_settings(
        _request(audit),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )

    assert response.readiness_warnings is None
    assert response.message == "1개 설정이 저장되었습니다."
    assert "readiness_warnings" not in audit.entries[0].extra


async def test_unchanged_active_db_ids_does_not_judge(env_file, monkeypatch):
    """ACTIVE_DB_IDS를 건드리지 않거나 DB를 빼기만 하면 판정 자체를 하지 않는다."""
    service = _FakeRegistrationService(RuntimeError("불리면 안 된다"))
    _install_service(monkeypatch, service)

    other = await update_settings(
        _request(), EnvUpdateRequest(settings={"LLM_MODEL": "m2"}), _ADMIN
    )
    reordered = await update_settings(
        _request(),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": " polestar , polestar "}),
        _ADMIN,
    )
    removed = await update_settings(
        _request(), EnvUpdateRequest(settings={"ACTIVE_DB_IDS": ""}), _ADMIN
    )

    assert service.asked == []
    assert other.readiness_warnings is None
    assert removed.readiness_warnings is None
    assert reordered.readiness_warnings is None


async def test_reset_active_db_ids_does_not_judge(env_file, monkeypatch):
    service = _FakeRegistrationService(RuntimeError("불리면 안 된다"))
    _install_service(monkeypatch, service)
    response = await update_settings(
        _request(), EnvUpdateRequest(reset_keys=["ACTIVE_DB_IDS"]), _ADMIN
    )
    assert service.asked == []
    assert response.readiness_warnings is None


async def test_judgement_failure_is_warned_and_save_proceeds(env_file, monkeypatch):
    """판정 자체가 실패하면 `{"_error": 사유}`로 알리고 저장은 진행한다."""
    _install_service(monkeypatch, _FakeRegistrationService(RuntimeError("MCP 연결 실패")))
    audit = _CapturingAuditService()

    response = await update_settings(
        _request(audit),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )

    assert "ACTIVE_DB_IDS=polestar,itam" in env_file.read_text(encoding="utf-8")
    assert set(response.readiness_warnings) == {"_error"}
    assert "MCP 연결 실패" in response.readiness_warnings["_error"]
    assert "판정하지 못했습니다" in response.message
    assert audit.entries[0].extra["readiness_warnings"] == response.readiness_warnings


async def test_judgement_timeout_is_warned(env_file, monkeypatch):
    """판정이 상한을 넘기면 저장을 붙잡지 않고 시간 초과 경고로 끝낸다."""
    monkeypatch.setattr(admin_routes, "_READINESS_TIMEOUT_SECONDS", 0.05)
    _install_service(
        monkeypatch, _FakeRegistrationService({"itam": _report("itam", [])}, delay=1.0)
    )

    response = await update_settings(
        _request(),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )

    assert "시간 초과" in response.readiness_warnings["_error"]


async def test_service_factory_failure_is_warned(env_file, monkeypatch):
    """서비스 생성 실패(설정·import 오류)도 저장을 막지 않는다."""

    def broken(config: Any) -> Any:
        raise ImportError("서비스 모듈 없음")

    monkeypatch.setattr("src.api.routes.db_structure.build_registration_service", broken)
    response = await update_settings(
        _request(),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )
    assert "서비스 모듈 없음" in response.readiness_warnings["_error"]


async def test_missing_running_config_is_warned(env_file, monkeypatch):
    service = _FakeRegistrationService({"itam": _report("itam", [])})
    _install_service(monkeypatch, service)
    response = await update_settings(
        _request(config=None),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )
    assert service.asked == []
    assert "_error" in response.readiness_warnings


async def test_db_missing_from_result_is_not_silently_dropped(env_file, monkeypatch):
    _install_service(monkeypatch, _FakeRegistrationService({}))
    response = await update_settings(
        _request(),
        EnvUpdateRequest(settings={"ACTIVE_DB_IDS": "polestar,itam"}),
        _ADMIN,
    )
    assert response.readiness_warnings["itam"][0]["code"] == "unknown"
