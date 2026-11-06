"""plans/215 잔여 ⑩ — CSV 가져오기 미리보기 본문 크기를 수신 중에 끊는다.

- `Content-Length`가 상한(1MB + multipart 여유분)을 넘으면 본문을 한 조각도 읽지 않고 413.
- 헤더가 없거나(chunked) 길이가 거짓이어도 누적이 상한을 넘는 순간 413 — 그 뒤 조각은 읽지 않는다.
- 인가(`require_admin_user` + role 재검증)가 본문 수신보다 먼저다 — 401·403이면 본문 0조각.
- 거부 응답·감사 형식은 기존 413 `import_too_large`(파일 판정)와 같다.

본문 조각 수는 ASGI `receive`를 직접 세어 본다(TestClient는 본문을 한 번에 넘긴다).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI

from noise_gate.application.biz_service_import import MAX_BYTES
from src.api.routes import admin_biz_service as routes
from tests.test_api.test_plan210_w6_api import ADMIN_BG, PLAIN_USER
from tests.test_api.test_plan215_w4_admin_biz_service import _audits
from tests.test_api.test_plan215_w5b_import_api import _app, _csv

PATH = "/api/v1/admin/biz-services/import/preview"
BOUNDARY = "XbizBoundary"
CHUNK = 64 * 1024
LIMIT = MAX_BYTES + routes._IMPORT_MULTIPART_SLACK


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.routing.registry as reg

    monkeypatch.setattr(reg, "get_registry",
                        lambda: SimpleNamespace(db_ids=lambda: ["pg_a", "pg_b"]))


def _multipart(data: bytes, field: str = "file") -> bytes:
    head = (f"--{BOUNDARY}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"svc.csv\"\r\nContent-Type: text/csv\r\n\r\n").encode()
    return head + data + f"\r\n--{BOUNDARY}--\r\n".encode()


class _Body:
    """ASGI receive — 본문을 `CHUNK` 조각으로 준다(`endless`면 끝없이) · 읽은 조각 수를 센다."""

    def __init__(self, data: bytes = b"", *, endless: bool = False) -> None:
        self.data = data
        self.endless = endless
        self.reads = 0

    async def __call__(self) -> dict[str, Any]:
        self.reads += 1
        if self.endless:  # 올바른 multipart 머리 뒤로 파일 데이터가 끝없이 온다
            part = _multipart(b"")[:-len(f"\r\n--{BOUNDARY}--\r\n")] if self.reads == 1 else b""
            return {"type": "http.request", "body": part + b"a" * CHUNK, "more_body": True}
        start = (self.reads - 1) * CHUNK
        part = self.data[start:start + CHUNK]
        return {"type": "http.request", "body": part, "more_body": start + CHUNK < len(self.data)}


def _call(app: FastAPI, body: _Body, *, headers: dict[str, str] = ADMIN_BG,
          content_length: int | None = None,
          content_type: str = f"multipart/form-data; boundary={BOUNDARY}") -> tuple[int, Any]:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    raw.append((b"content-type", content_type.encode()))
    if content_length is not None:
        raw.append((b"content-length", str(content_length).encode()))
    else:
        raw.append((b"transfer-encoding", b"chunked"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": PATH, "raw_path": PATH.encode(), "root_path": "",
             "query_string": b"", "headers": raw, "client": ("t", 1), "server": ("t", 80)}
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(app(scope, body, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(payload or b"null")


def _preview_denials(app: FastAPI) -> list[dict[str, Any]]:
    return [a for a in _audits(app) if a.get("action") == "biz_service_import_preview"]


def test_content_length_over_limit_413_before_reading_body() -> None:
    _, app, repo = _app()
    body = _Body(endless=True)
    status, out = _call(app, body, content_length=LIMIT + 1)
    assert status == 413 and out["detail"]["code"] == "import_too_large"
    assert out["detail"]["max_bytes"] == MAX_BYTES and "1MB" in out["detail"]["reason"]
    assert body.reads == 0  # 본문을 한 조각도 받지 않았다
    denied = _preview_denials(app)
    assert [(a["outcome"], a["reason"]) for a in denied] == [("denied", "import_too_large")]
    assert repo.writes == 0


def test_chunked_without_length_cut_while_receiving() -> None:
    _, app, _ = _app()
    body = _Body(endless=True)
    status, out = _call(app, body)
    assert status == 413 and out["detail"]["code"] == "import_too_large"
    assert body.reads <= LIMIT // CHUNK + 2  # 상한을 넘긴 조각에서 멈춘다(끝없는 본문)
    assert _preview_denials(app)[-1]["reason"] == "import_too_large"


def test_lying_content_length_still_cut() -> None:
    _, app, _ = _app()
    body = _Body(endless=True)
    status, out = _call(app, body, content_length=1000)
    assert status == 413 and out["detail"]["code"] == "import_too_large"
    assert body.reads <= LIMIT // CHUNK + 2


@pytest.mark.parametrize(("headers", "code"), [({}, 401), (PLAIN_USER, 403)])
def test_authorization_runs_before_body(headers: dict[str, str], code: int) -> None:
    _, app, _ = _app()
    body = _Body(endless=True)
    status, _out = _call(app, body, headers=headers)
    assert status == code and body.reads == 0
    assert _audits(app) == []


def test_chunked_small_file_passes_and_file_over_1mb_still_413() -> None:
    _, app, _ = _app()
    ok = _Body(_multipart(_csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")))
    status, out = _call(app, ok)
    assert status == 200, out
    assert out["rows"][0]["status"] == "add" and out["preview_id"]
    # 본문은 상한 안(여유분)이지만 파일이 1MB를 넘으면 종전 파일 판정이 413
    big = _csv("a,A,,,pg_a,ibkweb01,,")
    big += b"\n" * (MAX_BYTES + 1 - len(big))
    status, out = _call(app, _Body(_multipart(big)))
    assert status == 413 and out["detail"]["code"] == "import_too_large"


def test_not_multipart_or_missing_file_400() -> None:
    _, app, _ = _app()
    status, out = _call(app, _Body(b"{}"), content_type="application/json", content_length=2)
    assert status == 400 and out["detail"]["code"] == "invalid_request"
    status, out = _call(app, _Body(_multipart(b"x", field="other")))
    assert status == 400 and out["detail"]["code"] == "invalid_request"
    status, out = _call(app, _Body(b"garbage-no-boundary"))
    assert status == 400 and out["detail"]["code"] == "invalid_request"
    assert [a["reason"] for a in _preview_denials(app)] == ["invalid_request"] * 3


def test_openapi_still_documents_multipart_file() -> None:
    _, app, _ = _app()
    op = app.openapi()["paths"][PATH]["post"]
    schema = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert schema["required"] == ["file"] and schema["properties"]["file"]["format"] == "binary"
