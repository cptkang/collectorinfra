"""질의 감사 경로 일원화 (D-183).

종전에는 파싱 노드가 파일 감사만 직접 호출했고 `AuditService.log_user_request`는
정의만 있고 호출부가 0건이라 DB `audit_logs`에 질의가 한 건도 없었다. 기록 주체를
요청 수신 지점(라우트 · CLI)으로 옮기면서 지켜야 하는 계약을 고정한다:

  ① 진입점 4개 **전부**가 기록한다 (비대칭 주입은 이 저장소의 반복 실수 유형)
  ② 노드는 기록하지 않는다 (라우트와 겹치면 파일에 같은 질의가 두 번 남는다)
  ③ CLI 경로는 여전히 기록한다 (API 밖 실행의 감사 상실 방지)
  ④ 감사 실패가 질의를 막지 않는다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.domain.audit import AuditEvent
from src.security.audit_service import AuditService

_SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.fixture(scope="module")
def query_routes() -> str:
    return (_SRC / "api" / "routes" / "query.py").read_text(encoding="utf-8")


# ─── ① 대칭 주입 ───


def test_all_four_entry_points_audit(query_routes: str) -> None:
    """텍스트·스트리밍·파일·파일스트리밍 네 경로 전부가 기록해야 한다."""
    handlers = [
        "async def process_query(",
        "async def process_query_stream(",
        "async def process_file_query(",
        "async def process_file_query_stream(",
    ]
    for handler in handlers:
        assert handler in query_routes, handler
        body = query_routes.split(handler)[1].split("\n@router")[0]
        assert "_audit_user_request(" in body, f"{handler} 에 감사 주입 누락"


def test_audit_helper_is_defensive(query_routes: str) -> None:
    """감사 실패가 질의를 막지 않되, 삼키지 않고 경고로 남긴다."""
    body = query_routes.split("async def _audit_user_request(")[1].split("\ndef ")[0]
    assert 'getattr(request.app.state, "audit_service", None)' in body
    assert "except Exception" in body
    assert "logger.warning" in body


# ─── ② 노드는 기록하지 않는다 ───


def test_input_parser_does_not_audit() -> None:
    """노드에 남아 있으면 라우트 기록과 겹쳐 파일에 두 번 남는다."""
    src = (_SRC / "nodes" / "input_parser.py").read_text(encoding="utf-8")
    assert "log_user_request" not in src


# ─── ③ CLI 보존 ───


def test_cli_still_audits() -> None:
    """CLI는 라우트를 지나지 않으므로 여기가 그 경로의 유일한 기록 지점이다."""
    src = (_SRC / "main.py").read_text(encoding="utf-8")
    assert "log_user_request" in src
    assert re.search(r"await log_user_request\(", src)


# ─── ④ 서비스 계약 ───


class _FakeRepo:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def log_event(self, event: dict) -> None:
        self.events.append(event)

    async def query_logs(self, **kwargs):  # pragma: no cover - 계약 충족용
        return []


class _Config:
    jsonl_enabled = False   # 파일 I/O 없이 DB 경로만 본다
    db_enabled = True


@pytest.mark.asyncio
async def test_log_user_request_writes_one_db_row() -> None:
    repo = _FakeRepo()
    service = AuditService(config=_Config(), audit_repo=repo)

    await service.log_user_request(
        user_id="anonymous",
        user_query="은행존 CPU 사용률 조회해줘",
        output_format="text",
        has_file=False,
        client_ip="127.0.0.1",
        session_id="thread-1",
    )

    assert len(repo.events) == 1
    event = repo.events[0]
    assert event["event_type"] == AuditEvent.USER_REQUEST.value
    assert event["user_id"] == "anonymous"
    assert event["ip_address"] == "127.0.0.1"
    assert event["detail"]["user_query"] == "은행존 CPU 사용률 조회해줘"


@pytest.mark.asyncio
async def test_db_failure_does_not_raise() -> None:
    """감사 저장소가 죽어도 호출자(질의 처리)는 계속 간다."""

    class _BrokenRepo(_FakeRepo):
        async def log_event(self, event: dict) -> None:
            raise RuntimeError("db down")

    service = AuditService(config=_Config(), audit_repo=_BrokenRepo())
    await service.log_user_request(
        user_id=None,
        user_query="q",
        output_format="text",
        has_file=False,
    )  # 예외가 새어 나오면 실패


@pytest.mark.asyncio
async def test_no_repo_is_noop() -> None:
    """audit_service는 있지만 DB가 초기화되지 않은 환경(인증 DB 없음)."""
    service = AuditService(config=_Config(), audit_repo=None)
    await service.log_user_request(
        user_id=None, user_query="q", output_format="text", has_file=False
    )


# ─── 관리자 표기 ───


def test_admin_audit_tab_explains_anonymous() -> None:
    static = _SRC / "static"
    html = (static / "admin" / "dashboard.html").read_text(encoding="utf-8")
    js = (static / "js" / "admin.js").read_text(encoding="utf-8")
    assert 'id="auditAnonymousNotice"' in html
    assert "renderAuditAnonymousNotice" in js
    # 실제 anonymous 행이 있을 때만 노출한다 — 인증을 켜면 저절로 사라져야 한다
    body = js.split("function renderAuditAnonymousNotice(logs)")[1].split("\n    }")[0]
    assert 'log.user_id === "anonymous"' in body
