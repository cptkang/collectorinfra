"""plans/167 독립 검증 — 로그 비밀값 가림의 경계 사례 (verifier).

정본 테스트(`test_log_masking.py`)가 다루지 않는 것만 둔다.
- 실 uvicorn `AccessFormatter`(레코드 `args` 5-튜플을 직접 푼다)와의 결합
- 패턴 적용 순서(`Bearer` → 민감 키) 때문에 비밀값이 새는 꼴
- 멀티스레드 동시 기록 · 정본과 사본을 한 프로세스에서 함께 설치할 때의 멱등

검증 시점에 `xfail(strict=True)`로 재현했던 결함 3종(접근 로그 줄 소실 · 적용 순서 누출 · `secret`
반복 이차 역추적)은 plans/167 W1 교정에서 고쳐 일반 테스트로 바꿨다.
"""

from __future__ import annotations

import importlib.util
import io
import logging
import threading
from pathlib import Path

import pytest
from uvicorn.logging import AccessFormatter

from src.utils import log_masking
from src.utils.log_masking import install_log_record_factory, mask_secrets

_ROOT = Path(__file__).resolve().parents[2]
_ACCESS_FMT = '%(client_addr)s - "%(request_line)s" %(status_code)s'


@pytest.fixture
def fresh_factory(monkeypatch):
    original = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    monkeypatch.setattr(log_masking, "_warned", False)
    yield
    logging.setLogRecordFactory(original)


@pytest.fixture
def access_logger():
    """uvicorn 접근 로그 — 실 로거 이름 `uvicorn.access` · 자체 핸들러 · `propagate=False` ·
    실 `AccessFormatter`. 로거의 원래 핸들러·수준·전파 설정은 끝나면 되돌린다."""
    logger = logging.getLogger("uvicorn.access")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(AccessFormatter(_ACCESS_FMT, use_colors=False))
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield logger, stream
    logger.handlers[:], level, logger.propagate = saved
    logger.setLevel(level)


def _access(logger: logging.Logger, path: str) -> None:
    # uvicorn/protocols/http/h11_impl.py 의 접근 로그 호출과 같은 꼴
    logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", path, "1.1", 200)


# ── uvicorn 접근 로그 ─────────────────────────────────────────


def test_access_log_query_secret_masked_with_real_formatter(fresh_factory, access_logger) -> None:
    install_log_record_factory()
    logger, stream = access_logger
    _access(logger, "/x?token=abc&api_key=def&a=1")
    out = stream.getvalue()
    assert out == '127.0.0.1:5000 - "GET /x?token=***&api_key=***&a=1 HTTP/1.1" 200 OK\n'


def test_access_log_without_secret_unchanged(fresh_factory, access_logger) -> None:
    install_log_record_factory()
    logger, stream = access_logger
    _access(logger, "/api/v1/query?db=polestar")
    assert stream.getvalue() == '127.0.0.1:5000 - "GET /api/v1/query?db=polestar HTTP/1.1" 200 OK\n'


@pytest.mark.parametrize("path", ["/auth/bearer", "/api/token:", "/x?password="])
def test_access_log_line_survives_any_path(
    fresh_factory, access_logger, path, capsys
) -> None:
    install_log_record_factory()
    logger, stream = access_logger
    _access(logger, path)
    assert "Logging error" not in capsys.readouterr().err
    assert stream.getvalue().startswith('127.0.0.1:5000 - "GET ')


# ── 패턴 적용 순서 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "token_type=bearer access_token=abc",
        "Bearer token=abc",
        "auth bearer secret=abc",
    ],
)
def test_bearer_followed_by_sensitive_key_does_not_leak(text: str) -> None:
    assert "abc" not in mask_secrets(text)


@pytest.mark.parametrize(
    "text",
    [
        '{"token_type": "bearer", "access_token": "abc"}',
        "token_type=bearer&access_token=abc",
    ],
)
def test_oauth_token_response_shapes_masked(text: str) -> None:
    assert "abc" not in mask_secrets(text)


# ── 스레드 · 사본 공존 ────────────────────────────────────────


def test_concurrent_logging_masks_every_record(fresh_factory) -> None:
    install_log_record_factory()
    logger = logging.getLogger("test.log_masking_verify.threads")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        def work(n: int) -> None:
            for i in range(200):
                logger.info("dsn=%s n=%d", f"postgresql://u:pw{n}x{i}@h/db", i)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
        logger.setLevel(logging.NOTSET)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 8 * 200
    assert all(line.startswith("dsn=postgresql://u:***@h/db n=") for line in lines)


def _load_copy(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_canonical_then_copy_install_wraps_once(fresh_factory) -> None:
    """정본과 사본이 같은 표지를 쓰므로 한 프로세스에서 둘 다 설치해도 한 번만 감싼다."""
    copy = _load_copy("mcp_server/mcp_server/log_masking.py", "_verify_mcp_log_masking_copy")
    install_log_record_factory()
    first = logging.getLogRecordFactory()
    copy.install_log_record_factory()
    assert logging.getLogRecordFactory() is first


def test_exc_text_preset_used_by_standard_formatter_subclass(fresh_factory) -> None:
    """가린 `exc_text`를 미리 채워도 표준 포매터는 그것을 쓴다.

    `formatException`을 재정의하지 않은 하위 클래스 포함.
    """
    install_log_record_factory()
    logger = logging.getLogger("test.log_masking_verify.exc")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)

    class Sub(logging.Formatter):
        pass

    handler.setFormatter(Sub("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    try:
        try:
            raise ConnectionError("connect postgresql://u:TopS3cret@h:5432/db")
        except ConnectionError:
            logger.exception("실패")
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    out = stream.getvalue()
    assert "TopS3cret" not in out
    assert "ConnectionError: connect postgresql://u:***@h:5432/db" in out
    assert "Traceback (most recent call last)" in out


# ── 정규식 비용 ───────────────────────────────────────────────


def test_secret_word_repetition_is_not_quadratic() -> None:
    import time

    text = "/" + "secret-" * (16_000 // 7)  # uvicorn h11 요청줄 상한(16KB) 안
    started = time.perf_counter()
    mask_secrets(text)
    assert time.perf_counter() - started < 0.2
