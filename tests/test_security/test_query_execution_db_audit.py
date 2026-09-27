"""쿼리 실행 감사를 DB에도 남긴다 (감사 결함 ① · D-027 이중 기록).

SQL을 실행하는 노드(`query_executor`·`multi_db_executor`·`realtime_usage`)는 전부
`audit_logger.log_query_execution` 한 함수로 감사를 남기는데, 이 함수는 JSONL에만 썼다.
관리자 화면의 성공률 카드·「쿼리 실행」 필터·대량 조회 경보는 DB `audit_logs`를 읽으므로
늘 비어 있었다.

고정하는 계약:
  ① DB 감사가 구성되면(`AuditService` 생성) 실행 1건마다 DB에 `query_execution` 1행이 남는다.
  ② JSONL은 여전히 1줄이다 — `AuditService.log`로 다시 쓰면 파일에 두 번 남는다(D-183).
  ③ 요청 식별자·클라이언트 IP는 감사 미들웨어가 묶어 둔 컨텍스트에서 채운다.
  ④ `AUDIT_ALERT_ON_LARGE_RESULT`를 넘는 조회는 `security_alert`(info)를 남긴다.
  ⑤ 서비스가 없으면(CLI·DB 감사 미구성) 종전과 같다 — 파일만 쓰고 예외가 없다.
"""

from __future__ import annotations

import gc
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import structlog

from src.domain.audit import AuditEvent
from src.security import audit_logger
from src.security.audit_service import AuditService


class _AuditRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)


def _config(large: int = 5000) -> SimpleNamespace:
    return SimpleNamespace(
        jsonl_enabled=True, db_enabled=True,
        alert_on_failed_login=5, alert_on_large_result=large,
    )


@pytest.fixture
def jsonl(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    writer = AsyncMock()
    monkeypatch.setattr(audit_logger, "_write_audit_file", writer)
    return writer


async def _execute(**overrides) -> None:
    fields = dict(
        sql="SELECT 1", row_count=3, execution_time_ms=12.345, success=True,
        user_id="u1", thread_id="thread-1", source_name="db_a",
    )
    fields.update(overrides)
    await audit_logger.log_query_execution(**fields)


async def test_execution_is_mirrored_to_db_once(jsonl: AsyncMock) -> None:
    repo = _AuditRepo()
    service = AuditService(_config(), repo)

    await _execute()

    assert [e["event_type"] for e in repo.events] == [AuditEvent.QUERY_EXECUTION.value]
    row = repo.events[0]
    assert row["user_id"] == "u1"
    detail = row["detail"]
    assert detail["success"] is True
    assert detail["row_count"] == 3
    assert detail["execution_time_ms"] == 12.35
    assert detail["target_db"] == "db_a"
    assert detail["session_id"] == "thread-1"
    assert detail["generated_sql"] == "SELECT 1"
    # ② JSONL은 노드 호출 1건당 1줄 그대로
    assert jsonl.await_count == 1
    del service


async def test_failed_execution_keeps_error(jsonl: AsyncMock) -> None:
    repo = _AuditRepo()
    service = AuditService(_config(), repo)

    await _execute(success=False, row_count=0, error="relation does not exist", retry_attempt=2)

    detail = repo.events[0]["detail"]
    assert detail["success"] is False
    assert detail["error"] == "relation does not exist"
    assert detail["extra"] == {"retry_attempt": 2}
    del service


async def test_request_context_fills_ip_and_request_id(jsonl: AsyncMock) -> None:
    repo = _AuditRepo()
    service = AuditService(_config(), repo)

    with structlog.contextvars.bound_contextvars(request_id="rid-7", client_ip="10.1.2.3"):
        await _execute()

    row = repo.events[0]
    assert row["ip_address"] == "10.1.2.3"
    assert row["detail"]["request_id"] == "rid-7"
    del service


async def test_large_result_raises_info_alert(jsonl: AsyncMock) -> None:
    repo = _AuditRepo()
    service = AuditService(_config(large=10), repo)

    await _execute(row_count=11)

    types = [e["event_type"] for e in repo.events]
    assert types == [AuditEvent.QUERY_EXECUTION.value, AuditEvent.SECURITY_ALERT.value]
    assert repo.events[1]["detail"]["severity"] == "info"
    del service


async def test_db_disabled_service_does_not_mirror(jsonl: AsyncMock) -> None:
    repo = _AuditRepo()
    config = _config()
    config.db_enabled = False
    service = AuditService(config, repo)

    await _execute()

    assert repo.events == []
    assert jsonl.await_count == 1
    del service


async def test_released_service_stops_mirroring(jsonl: AsyncMock) -> None:
    """서비스가 사라지면(앱 종료·다른 테스트) 복제도 멈춘다 — 전역 참조가 남지 않는다."""
    repo = _AuditRepo()
    service = AuditService(_config(), repo)
    del service
    gc.collect()

    await _execute()

    assert repo.events == []
    assert jsonl.await_count == 1
