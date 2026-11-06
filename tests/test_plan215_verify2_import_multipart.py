"""plans/215 2차 독립 검증 — CSV 미리보기 직접 파싱(`_read_upload`)의 형식 경계.

검증자 작성(제품 코드 수정 없음). `MultiPartParser(max_files=1, max_fields=10)` 경계와 파일이 아닌
`file` 칸은 400 `invalid_request`이고, 거부 감사에는 오류 코드만 남는다(예외 문자열 0).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.test_api.test_plan215_import_size_stream import (
    BOUNDARY,
    _Body,
    _call,
    _preview_denials,
)
from tests.test_api.test_plan215_w5b_import_api import _app, _csv

_END = f"--{BOUNDARY}--\r\n".encode()


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.routing.registry as reg

    monkeypatch.setattr(reg, "get_registry",
                        lambda: SimpleNamespace(db_ids=lambda: ["pg_a", "pg_b"]))


def _part(name: str, data: bytes, filename: str | None = "svc.csv") -> bytes:
    disp = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename else "")
    head = f"--{BOUNDARY}\r\nContent-Disposition: {disp}\r\n"
    if filename:
        head += "Content-Type: text/csv\r\n"
    return (head + "\r\n").encode() + data + b"\r\n"


def _assert_400_code_only(app, status: int, out) -> None:  # noqa: ANN001
    assert status == 400 and out["detail"]["code"] == "invalid_request"
    denied = _preview_denials(app)
    assert denied and denied[-1]["outcome"] == "denied"
    assert denied[-1]["reason"] == "invalid_request"
    text = json.dumps(denied[-1], ensure_ascii=False)
    assert "Too many" not in text and "MultiPart" not in text


def test_two_files_rejected_400() -> None:
    _, app, repo = _app()
    data = _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")
    body = _part("file", data) + _part("file2", data, "b.csv") + _END
    status, out = _call(app, _Body(body))
    _assert_400_code_only(app, status, out)
    assert repo.writes == 0


def test_text_field_named_file_rejected_400() -> None:
    _, app, _ = _app()
    body = _part("file", b"ibk,x", filename=None) + _END
    status, out = _call(app, _Body(body))
    _assert_400_code_only(app, status, out)


def test_too_many_fields_rejected_400() -> None:
    _, app, _ = _app()
    body = b"".join(_part(f"f{i}", b"v", filename=None) for i in range(11))
    body += _part("file", _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")) + _END
    status, out = _call(app, _Body(body))
    _assert_400_code_only(app, status, out)


def test_extra_small_field_with_file_still_ok() -> None:
    _, app, _ = _app()
    body = _part("note", b"hello", filename=None)
    body += _part("file", _csv("ibk,인터넷뱅킹,,,pg_a,ibkweb01,,")) + _END
    status, out = _call(app, _Body(body))
    assert status == 200, out
    assert out["rows"][0]["status"] == "add"
