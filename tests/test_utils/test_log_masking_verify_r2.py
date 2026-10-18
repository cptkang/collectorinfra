"""plans/167 교정 라운드 재검증 — 교정이 새로 만든 회귀 여부 (verifier · 2차).

정본 테스트(`test_log_masking.py`)·1차 검증(`test_log_masking_verify.py`)과 겹치지 않는 것만 둔다.
- 1차에서 통과하던 양성이 새 공백 규칙 뒤에도 그대로 가려지는가(실 로그 꼴 그대로)
- `uvicorn.access` 인자 단위 가림 — 실 `AccessFormatter` + uvicorn 기본 접근 로그 서식
- 새 Bearer 앞 경계 `(?<![/A-Za-z0-9_])`
- structlog 비문자열 값이 실 `JSONRenderer` 출력까지 가려지는가
- 재현했던 결함(민감 키 값의 스킴 낱말 · Bearer 뒤 문장 부호 `:`)은 고친 뒤 일반 테스트로 둔다
"""

from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace

import pytest
import structlog
from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter

from src.utils import log_masking
from src.utils.log_masking import install_log_record_factory, mask_event_dict, mask_secrets

_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_abc-123"
_GOOGLE_KEY = "AIza" + "B" * 35
#: uvicorn 기본 접근 로그 서식
#: (`%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s`)
_UVICORN_ACCESS_FMT = LOGGING_CONFIG["formatters"]["access"]["fmt"]


# ── 1차에서 통과하던 양성 — 새 공백 규칙 뒤에도 가려진다 ─────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # httpx HTTPStatusError 전문(둘째 줄 안내 URL 포함)
        (
            "Client error '403 Forbidden' for url 'https://api.x.com/v1/q?api_key=AK9&page=2'\n"
            "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403",
            "Client error '403 Forbidden' for url 'https://api.x.com/v1/q?api_key=***&page=2'\n"
            "For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/403",
        ),
        (
            "Server error '500 Internal Server Error' for url 'http://10.0.0.1:8080/rest?token=TK1'",
            "Server error '500 Internal Server Error' for url 'http://10.0.0.1:8080/rest?token=***'",
        ),
        # 헤더 dict repr · httpx Headers repr
        (
            "headers={'X-Api-Key': 'K1', 'Accept': 'application/json'}",
            "headers={'X-Api-Key': '***', 'Accept': 'application/json'}",
        ),
        ("Headers({'x-api-key': 'K2', 'host': 'h'})", "Headers({'x-api-key': '***', 'host': 'h'})"),
        (
            "{'x-openapi-token': 'Bearer abc', 'x-generative-ai-client': 'ck'}",
            "{'x-openapi-token': '***', 'x-generative-ai-client': '***'}",
        ),
        # JSON
        ('{"password": "x"}', '{"password": "***"}'),
        ('{"password":"x","user":"u"}', '{"password":"***","user":"u"}'),
        # ODBC(DB2 연결 문자열 — 드라이버 중괄호 값은 남는다)
        (
            "DRIVER={IBM DB2};DATABASE=d;HOSTNAME=h;PORT=50000;UID=u;PWD=Pw1!;",
            "DRIVER={IBM DB2};DATABASE=d;HOSTNAME=h;PORT=50000;UID=u;PWD=***;",
        ),
        # redis 사용자 없는 userinfo · rediss
        ("redis://:pw@redis:6379/0", "redis://:***@redis:6379/0"),
        ("rediss://default:pw@redis:6379", "rediss://default:***@redis:6379"),
        # JWT · Google API 키
        (f"jwt {_JWT} rejected", "jwt *** rejected"),
        (f"GET /v1/models?key={_GOOGLE_KEY}&alt=json", "GET /v1/models?key=***&alt=json"),
        (f"api key {_GOOGLE_KEY}", "api key ***"),
        # 헤더형 키(`-` 포함)는 `:` 앞뒤 공백을 허용한다
        ("x-api-key : K6", "x-api-key : ***"),
        ("Private-Token: GL1", "Private-Token: ***"),
        # 따옴표 값이면 `=` 앞뒤 공백 허용 · 따옴표 키면 `:` 뒤 맨값도
        ('password = "p"', 'password = "***"'),
        ("'password' : hunter2", "'password' : ***"),
    ],
)
def test_first_round_positives_still_masked(raw: str, expected: str) -> None:
    assert mask_secrets(raw) == expected


# ── uvicorn 접근 로그 — 실 AccessFormatter · 기본 서식 ─────────────────


@pytest.fixture
def plain_factory(monkeypatch):
    original = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    monkeypatch.setattr(log_masking, "_warned", False)
    yield
    logging.setLogRecordFactory(original)


@pytest.fixture
def access_logger():
    logger = logging.getLogger("uvicorn.access")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(_UVICORN_ACCESS_FMT, use_colors=False))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield logger, stream
    logger.handlers[:], level, logger.propagate = saved
    logger.setLevel(level)


def _emit(logger: logging.Logger, stream: io.StringIO, path: str) -> str:
    """uvicorn h11_impl·httptools_impl의 접근 로그 호출과 같은 꼴로 1줄 쓰고 그 줄을 돌려준다."""
    stream.seek(0)
    stream.truncate()
    logger.info('%s - "%s %s HTTP/%s" %d', "10.0.0.5:41000", "GET", path, "1.1", 200)
    return stream.getvalue()


@pytest.mark.parametrize(
    ("path", "masked_path"),
    [
        ("/cb?state=1&access_token=AT1", "/cb?state=1&access_token=***"),
        (f"/v1/models?key={_GOOGLE_KEY}", "/v1/models?key=***"),
        ("/x?password=pw&a=1", "/x?password=***&a=1"),
        ("/x?client_secret=cs", "/x?client_secret=***"),
        (f"/x?id_token={_JWT}", "/x?id_token=***"),
    ],
)
def test_access_log_path_secrets_masked_args_tuple_kept(
    plain_factory, access_logger, path: str, masked_path: str, capsys
) -> None:
    install_log_record_factory()
    logger, stream = access_logger
    records: list[logging.LogRecord] = []

    class Keep(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            records.append(record)
            return True

    keep = Keep()
    logger.addFilter(keep)
    try:
        line = _emit(logger, stream, path)
    finally:
        logger.removeFilter(keep)
    assert line == f'INFO:     10.0.0.5:41000 - "GET {masked_path} HTTP/1.1" 200 OK\n'
    assert isinstance(records[0].args, tuple) and len(records[0].args) == 5
    assert "Logging error" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "path",
    [
        "/auth/bearer",
        "/auth-bearer",  # 전체 메시지 가림은 과잉이지만 인자 단위로는 바뀌지 않는다
        "/api/token:",
        "/x?password=",
        "/x?password=&a=1",
        "/api/Bearer",
        "/secrets/list",
    ],
)
def test_access_log_over_mask_candidates_render_identically(
    plain_factory, access_logger, path: str, capsys
) -> None:
    logger, stream = access_logger
    unmasked = _emit(logger, stream, path)
    install_log_record_factory()
    masked = _emit(logger, stream, path)
    assert masked == unmasked
    assert "Logging error" not in capsys.readouterr().err


# ── Bearer 앞 경계 ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Bearer abc"', '"Bearer ***"'),
        ("(Bearer abc)", "(Bearer ***)"),
        ("hdr=Bearer abc", "hdr=Bearer ***"),
        ("Bearer\tabc", "Bearer\t***"),
        ("using Bearer abc.", "using Bearer ***"),
        ("x-bearer abc", "x-bearer ***"),
    ],
)
def test_bearer_boundary_masks_token(raw: str, expected: str) -> None:
    assert mask_secrets(raw) == expected


@pytest.mark.parametrize(
    "text",
    ["GET /auth/bearer HTTP/1.1", "/api/Bearer x", "x_bearer abc", "token_type: bearer"],
)
def test_bearer_boundary_leaves_path_and_identifier(text: str) -> None:
    assert mask_secrets(text) is text


# ── structlog — 실 JSONRenderer 출력 ───────────────────────────


def test_structlog_non_string_values_masked_in_rendered_json() -> None:
    event = {
        "event": "upstream failed",
        "error": ValueError("dsn=postgresql://u:pw1@h/db"),
        "raw": b"token=tk2",
        "obj": SimpleNamespace(password="pw3"),
        "nested": {"items": [RuntimeError("api_key=k4"), {"Authorization": "Basic b5"}]},
        "count": 3,
        "ok": True,
        "none": None,
    }
    masked = mask_event_dict(None, "info", event)
    rendered = structlog.processors.JSONRenderer()(None, "info", masked)
    for secret in ("pw1", "tk2", "pw3", "k4", "b5"):
        assert secret not in rendered, secret
    doc = json.loads(rendered)
    assert doc["count"] == 3 and doc["ok"] is True and doc["none"] is None
    assert "postgresql://u:***@h/db" in doc["error"]


# ── 재현했던 결함(교정이 새로 만든 회귀 — 고침) ───────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # FabriX 실 헤더 꼴(fabrix_kbgenai.py:93 · fabrix_proxy/fabrix_client.py:132)
        "x-openapi-token: Bearer abc",
        "x-openapi-token:Bearer abc",
        "X-Api-Key: Bearer abc",
        "access_token=Bearer abc",
        "x-openapi-token: Basic abc",
    ],
)
def test_sensitive_key_with_scheme_value_does_not_leak_token(text: str) -> None:
    assert "abc" not in mask_secrets(text)


def test_bearer_token_followed_by_colon_punctuation_masked() -> None:
    assert "abc123" not in mask_secrets("Bearer abc123: rejected by upstream")
