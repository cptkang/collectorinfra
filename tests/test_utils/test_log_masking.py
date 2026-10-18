"""로그 비밀값 가림 정본 단위 테스트 (plans/167 W1).

패턴별 양성·음성(과도 가림 금지) · 멱등 · 레코드 팩토리(렌더 메시지·트레이스백·스택 정보·
`propagate=False` 자체 핸들러·비트 동일·실패 통과·설치 멱등) · structlog 프로세서.
"""

from __future__ import annotations

import io
import logging
import time

import pytest

from src.utils import log_masking
from src.utils.log_masking import (
    MASK,
    install_log_record_factory,
    mask_event_dict,
    mask_secrets,
)

_GOOGLE_KEY = "AIza" + "SyA0123456789abcdefghij0123456789_-"  # 4 + 35자
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.c2lnbmF0dXJlLV8"


@pytest.fixture
def fresh_factory(monkeypatch):
    """기본 팩토리에서 시작하고 끝나면 원래 팩토리를 복원한다(전역 상태 원복)."""
    original = logging.getLogRecordFactory()
    logging.setLogRecordFactory(logging.LogRecord)
    monkeypatch.setattr(log_masking, "_warned", False)
    yield
    logging.setLogRecordFactory(original)


@pytest.fixture
def own_logger():
    """루트로 전파하지 않고 자기 핸들러로만 쓰는 로거(uvicorn 꼴)와 그 출력 버퍼."""
    logger = logging.getLogger("test.log_masking.own")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, stream
    logger.removeHandler(handler)
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


# ── 패턴 양성 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 1. URL·DSN 사용자 정보 — 사용자명·호스트·포트·DB 이름은 남는다
        (
            "connect postgresql://alice:S3cretPw!x@127.0.0.1:5432/infra",
            "connect postgresql://alice:***@127.0.0.1:5432/infra",
        ),
        ("redis://:pw123@localhost:6379/0", "redis://:***@localhost:6379/0"),
        ("mysql://u:p@ss:w@db:3306/itam", "mysql://u:***@db:3306/itam"),
        ("http://bob:pw@h/", "http://bob:***@h/"),
        ("postgresql://a:b@h/db?mail=a@b", "postgresql://a:***@h/db?mail=a@b"),
        # 2. 인증 헤더 — 스킴은 남긴다
        ("Authorization: Bearer abc.def-ghi", "Authorization: Bearer ***"),
        ("authorization=Basic dXNlcjpwYXNz", "authorization=Basic ***"),
        ("Proxy-Authorization: Token t0k", "Proxy-Authorization: Token ***"),
        ("{'authorization': 'raw-cred'}", "{'authorization': '***'}"),
        ("X-API-Key: k123", "X-API-Key: ***"),
        ("x-goog-api-key=zzz", "x-goog-api-key=***"),
        # 3. Bearer
        ("send bearer XYZ0+/== now", "send bearer *** now"),
        ("bearer abc.def", "bearer ***"),
        ("Bearer abc123: rejected by upstream", "Bearer ***: rejected by upstream"),
        # 민감 키의 값이 스킴 낱말이면 스킴은 남기고 토큰을 가린다
        ("x-openapi-token: Bearer abc", "x-openapi-token: Bearer ***"),
        ("access_token=Bearer abc", "access_token=Bearer ***"),
        ("Token=Basic abc== next", "Token=Basic *** next"),
        # 4. 민감 키 이름
        ("DB_PASSWORD=hunter2 port=5432", "DB_PASSWORD=*** port=5432"),
        ("FABRIX_API_KEY=abc,next", "FABRIX_API_KEY=***,next"),
        ("JWT_SECRET_KEY='s s'", "JWT_SECRET_KEY='***'"),
        ('GATEWAY_TOKEN="t"', 'GATEWAY_TOKEN="***"'),
        ('{"password": "x y", "user": "u"}', '{"password": "***", "user": "u"}'),
        ("{'token': 'abc'}", "{'token': '***'}"),
        ("client_secret:zz)", "client_secret:***)"),
        ("password = 'x y'", "password = '***'"),
        ('{"password": "a\\"b c", "u": 1}', '{"password": "***", "u": 1}'),
        ("{'token': 'a\\'b'}", "{'token': '***'}"),
        ("password=b'abc'", "password=b'***'"),
        ("UID=u;PWD={p;w};DB=x", "UID=u;PWD={***};DB=x"),
        ("client_key=ck LLM_FABRIX_CLIENT_KEY=ck2", "client_key=*** LLM_FABRIX_CLIENT_KEY=***"),
        ("passphrase=pp credentials=cc", "passphrase=*** credentials=***"),
        ("credential=c", "credential=***"),
        ("x-generative-ai-client: ck1", "x-generative-ai-client: ***"),
        ("{'x-generative-ai-client': 'ck1'}", "{'x-generative-ai-client': '***'}"),
        ("Cookie: sid=abc; other=1", "Cookie: ***"),
        ("{'set-cookie': 'sid=abc; Path=/'}", "{'set-cookie': '***'}"),
        ("dsn=secret://admin:pw@10.0.0.9", "dsn=secret://admin:***@10.0.0.9"),
        # 민감 키가 Bearer보다 먼저 — 키 이름 뒤 값이 새지 않는다
        ("token_type=bearer access_token=abc", "token_type=bearer access_token=***"),
        ("Bearer token=abc", "Bearer token=***"),
        ("auth bearer secret=xyz", "auth bearer secret=***"),
        ("passwd=a;pwd=b&apikey=c", "passwd=***;pwd=***&apikey=***"),
        ("access_key=AK private_key=PK", "access_key=*** private_key=***"),
        (
            "DATABASE=d;HOSTNAME=h;PORT=50000;UID=u;PWD=pw!;",
            "DATABASE=d;HOSTNAME=h;PORT=50000;UID=u;PWD=***;",
        ),
        # httpx 상태 오류의 URL 쿼리
        (
            "Client error '401 Unauthorized' for url 'https://x/y?api_key=abc&token=def'",
            "Client error '401 Unauthorized' for url 'https://x/y?api_key=***&token=***'",
        ),
        # 5. 쿼리의 맨 key=
        ("GET https://g/v1/models?key=abc123&alt=json", "GET https://g/v1/models?key=***&alt=json"),
        ("https://g/v1?alt=json&key=abc", "https://g/v1?alt=json&key=***"),
        # 6. JWT · Google API 키
        (f"token {_JWT} 만료", "token *** 만료"),
        (f"key {_GOOGLE_KEY} 거부", "key *** 거부"),
    ],
)
def test_masks_secret_shapes(raw: str, expected: str) -> None:
    assert mask_secrets(raw) == expected


# ── 과도 가림 금지 ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "max_tokens=512",
        "prompt_tokens: 30",
        "token_count=3",
        "stream_key=alarm:raw",
        "tokens=1200",
        "host=10.0.0.1 port=5432 database=infra user=alice UID=db2inst1",
        "SQLSTATE 28P01: InvalidPasswordError",
        "asyncpg.exceptions.InvalidPasswordError",
        "https://host:8080/path?x=1",
        "postgresql://alice@host/db",
        "전송 인증(정적 Bearer): on",
        "GET /auth/bearer HTTP/1.1",
        "전송 인증 off(APM_GATEWAY_BEARER_TOKEN·APM_GATEWAY_BEARER_TOKENS 미설정)",
        "password=",
        '"password": ""',
        # 따옴표 없는 띄어 쓴 문장 · `=` 뒤 공백 — 다음 낱말을 값으로 보지 않는다
        "token: expired",
        "invalid token: signature mismatch",
        "secrets_manager: enabled",
        "Password = abc def",
        "GET /x?password= HTTP/1.1",
        "김포 서버 CPU 사용률 상위 10건",
    ],
)
def test_does_not_over_mask(text: str) -> None:
    assert mask_secrets(text) is text


def test_mysql_access_denied_keeps_password_yes_no() -> None:
    """`(using password: YES|NO)`는 비밀값이 아니라 진단 정보라 남긴다(판정 고정)."""
    for flag in ("YES", "NO"):
        text = f"(1045, \"Access denied for user 'x'@'h' (using password: {flag})\")"
        assert mask_secrets(text) is text


@pytest.mark.parametrize(
    "text",
    [
        "postgresql://alice:S3cret@h/db password=x token: y Authorization: Basic q",
        f"Bearer abc {_JWT} {_GOOGLE_KEY} ?key=k &api_key=v PWD=z;",
        '{"password": "x", "secret_ref": "y"}',
    ],
)
def test_mask_is_idempotent(text: str) -> None:
    once = mask_secrets(text)
    assert once != text
    assert mask_secrets(once) == once


# ── 레코드 팩토리 ─────────────────────────────────────────────


def test_record_message_masked_on_own_handler_logger(fresh_factory, own_logger) -> None:
    """`propagate=False` 자체 핸들러 로거로 가도 레코드 생성 시점에 가려진다."""
    logger, stream = own_logger
    install_log_record_factory()
    logger.error("연결 실패: %s", "postgresql://alice:S3cretPw@db:5432/infra")
    out = stream.getvalue()
    assert "S3cretPw" not in out
    assert "postgresql://alice:***@db:5432/infra" in out


def test_record_without_secret_keeps_msg_and_args_identical(fresh_factory) -> None:
    install_log_record_factory()
    msg = "%s건 조회 — %s (%d ms)"
    args = (3, "host1", 12)
    record = logging.getLogRecordFactory()("x", logging.INFO, __file__, 1, msg, args, None)
    assert record.msg is msg
    assert record.args is args


def test_record_args_only_masked_keeps_tuple_for_access_formatter(fresh_factory) -> None:
    """인자만 가려서 충분하면 `args` 튜플을 유지한다 — uvicorn 접근 로그 포매터가 풀어 쓴다."""
    from uvicorn.logging import AccessFormatter

    install_log_record_factory()
    args = ("127.0.0.1:5000", "GET", "/api/x?token=abc123", "1.1", 200)
    record = logging.getLogRecordFactory()(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d', args, None
    )
    assert isinstance(record.args, tuple)
    assert record.args[2] == "/api/x?token=***"
    out = AccessFormatter(fmt="%(client_addr)s %(request_line)s %(status_code)s",
                          use_colors=False).format(record)
    assert "abc123" not in out
    assert "/api/x?token=***" in out


def test_record_secret_across_format_falls_back_to_masked_msg(fresh_factory) -> None:
    install_log_record_factory()
    record = logging.getLogRecordFactory()(
        "x", logging.INFO, __file__, 1, "password=%s user=%s", ("hunter2", "u"), None
    )
    assert record.args is None
    assert record.msg == "password=*** user=u"
    assert record.getMessage() == "password=*** user=u"


def test_access_logger_record_keeps_args_tuple_even_if_message_over_masks(fresh_factory) -> None:
    """`uvicorn.access` 레코드는 인자 단위로만 가린다 — 전체 메시지가 요청줄 뒤까지 가려지는
    경로(`/auth-bearer` → `bearer HTTP/1.1`)여도 `args` 튜플을 비우지 않는다."""
    install_log_record_factory()
    factory = logging.getLogRecordFactory()
    fmt = '%s - "%s %s HTTP/%s" %d'
    plain = ("127.0.0.1:5000", "GET", "/auth-bearer", "1.1", 401)
    record = factory("uvicorn.access", logging.INFO, __file__, 1, fmt, plain, None)
    assert record.args is plain and record.msg is fmt
    secret = ("127.0.0.1:5000", "GET", "/x?token=abc", "1.1", 200)
    record = factory("uvicorn.access", logging.INFO, __file__, 1, fmt, secret, None)
    assert record.msg is fmt
    assert record.args == ("127.0.0.1:5000", "GET", "/x?token=***", "1.1", 200)


def test_record_dict_args_masked(fresh_factory) -> None:
    install_log_record_factory()
    record = logging.getLogRecordFactory()(
        "x", logging.INFO, __file__, 1, "dsn=%(dsn)s n=%(n)d",
        ({"dsn": "redis://:pw@h:6379/0", "n": 1},), None,
    )
    assert record.getMessage() == "dsn=redis://:***@h:6379/0 n=1"


def _boom(detail: str) -> None:
    raise RuntimeError(detail)


def test_exc_text_traceback_masked(fresh_factory, own_logger) -> None:
    """예외 문구와 트레이스백 소스 줄의 리터럴이 함께 가려진다."""
    logger, stream = own_logger
    install_log_record_factory()
    try:
        _boom("password=hunter2 dsn=postgresql://u:S3cret@h/db")
    except RuntimeError:
        logger.exception("Exception in ASGI application")
    out = stream.getvalue()
    assert "hunter2" not in out
    assert "S3cret" not in out
    assert "Traceback (most recent call last)" in out
    assert "RuntimeError: password=*** dsn=postgresql://u:***@h/db" in out


def test_exc_without_secret_leaves_exc_text_unset(fresh_factory) -> None:
    install_log_record_factory()
    try:
        raise ValueError("평범한 오류")
    except ValueError:
        import sys

        record = logging.getLogRecordFactory()(
            "x", logging.ERROR, __file__, 1, "실패", (), sys.exc_info()
        )
    assert record.exc_text is None


def test_stack_info_masked(fresh_factory, own_logger) -> None:
    logger, stream = own_logger
    install_log_record_factory()
    logger.info("확인 token=abc123xyz", stack_info=True)
    out = stream.getvalue()
    assert "Stack (most recent call last)" in out
    assert "abc123xyz" not in out


def test_unrenderable_record_passes_through(fresh_factory, own_logger, capsys) -> None:
    """렌더 자체가 실패하는 레코드는 손대지 않고 logging 원래 오류 처리로 간다."""
    logger, stream = own_logger
    install_log_record_factory()
    record = logging.getLogRecordFactory()("x", logging.INFO, __file__, 1, "%d", ("x",), None)
    assert record.msg == "%d" and record.args == ("x",)
    logger.info("%d", "x")  # 예외를 올리지 않는다(Handler.handleError)
    assert "--- Logging error ---" in capsys.readouterr().err
    assert "log_masking:" not in stream.getvalue()


def test_mask_failure_passes_record_and_warns_once(fresh_factory, monkeypatch, capsys) -> None:
    def broken(text: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(log_masking, "mask_secrets", broken)
    install_log_record_factory()
    factory = logging.getLogRecordFactory()
    args = ("redis://:pw@h/0",)
    first = factory("x", logging.INFO, __file__, 1, "a %s", args, None)
    factory("x", logging.INFO, __file__, 1, "b %s", args, None)
    assert first.msg == "a %s" and first.args is args
    err = capsys.readouterr().err
    assert err.count("log_masking: 가림 실패") == 1
    assert "pw" not in err


def test_install_is_idempotent(fresh_factory) -> None:
    install_log_record_factory()
    first = logging.getLogRecordFactory()
    install_log_record_factory()
    assert logging.getLogRecordFactory() is first
    assert getattr(first, "_log_secret_masking") is True


def test_install_wraps_existing_factory(fresh_factory) -> None:
    seen: list[str] = []

    def custom(*args, **kwargs):
        seen.append("custom")
        return logging.LogRecord(*args, **kwargs)

    logging.setLogRecordFactory(custom)
    install_log_record_factory()
    record = logging.getLogRecordFactory()("x", logging.INFO, __file__, 1, "token=%s", ("t",), None)
    assert seen == ["custom"]
    assert record.getMessage() == "token=***"


# ── structlog 프로세서 ────────────────────────────────────────


def test_event_dict_masks_values_and_sensitive_keys() -> None:
    nested = {"dsn": "postgresql://u:pw@h/db", "items": ["Bearer abc", ("token=x", 3)]}
    event = {
        "event": "연결 실패 api_key=k1",
        "password": "hunter2",
        "db_token": 12345,
        "has_token": True,
        "jwt_secret": None,
        "max_tokens": 512,
        "ctx": nested,
    }
    out = mask_event_dict(None, "info", event)
    assert out is event
    assert out["event"] == "연결 실패 api_key=***"
    assert out["password"] == MASK
    assert out["db_token"] == MASK
    assert out["has_token"] is True
    assert out["jwt_secret"] is None
    assert out["max_tokens"] == 512
    assert out["ctx"] == {
        "dsn": "postgresql://u:***@h/db",
        "items": ["Bearer ***", ("token=***", 3)],
    }
    # 호출자가 넘긴 중첩 객체는 바꾸지 않는다
    assert nested["dsn"] == "postgresql://u:pw@h/db"
    assert nested["items"][0] == "Bearer abc"


def test_event_dict_masks_repr_of_non_primitive_values() -> None:
    """예외·bytes·임의 객체는 JSONRenderer가 repr로 찍는다 — repr을 가려 바뀐 경우만 바꾼다."""
    from types import SimpleNamespace

    plain = SimpleNamespace(a="host=h1")
    event = {
        "error": ValueError("dsn=postgresql://u:pw@h/db"),
        "raw": b"token=abc",
        "obj": SimpleNamespace(a="password=abc"),
        "plain": plain,
        "n": 3.5,
    }
    out = mask_event_dict(None, "info", event)
    assert out["error"] == "ValueError('dsn=postgresql://u:***@h/db')"
    assert out["raw"] == "b'token=***'"
    assert out["obj"] == "namespace(a='password=***')"
    assert out["plain"] is plain
    assert out["n"] == 3.5


def test_event_dict_header_field_names_masked_whole() -> None:
    event = {
        "ctx": {"authorization": "Basic abc", "Cookie": "sid=1", "x-generative-ai-client": "k"},
        "set-cookie": "sid=2",
    }
    out = mask_event_dict(None, "info", event)
    assert out["ctx"] == {"authorization": MASK, "Cookie": MASK, "x-generative-ai-client": MASK}
    assert out["set-cookie"] == MASK


def test_event_dict_without_secret_untouched() -> None:
    ctx = {"host": "h1", "rows": [1, 2]}
    event = {"event": "조회 완료", "ctx": ctx, "level": "info"}
    out = mask_event_dict(None, "info", dict(event))
    assert out == event
    assert out["ctx"] is ctx


class _BadRepr:
    def __repr__(self) -> str:
        raise RuntimeError("repr 실패")


def test_event_dict_value_failure_kept_and_warned_once(monkeypatch, capsys) -> None:
    """`__repr__`가 예외인 값은 그대로 두고 1회만 경고한다 — 다른 키는 계속 가린다."""
    monkeypatch.setattr(log_masking, "_warned", False)
    bad = _BadRepr()
    event = {"event": "x", "obj": bad, "dsn": "redis://:pw@h:6379/0", "obj2": _BadRepr()}
    out = mask_event_dict(None, "info", event)
    assert out["obj"] is bad
    assert out["dsn"] == "redis://:***@h:6379/0"
    assert capsys.readouterr().err.count("log_masking:") == 1


def test_event_dict_circular_reference_terminates() -> None:
    """순환 참조 dict도 깊이 상한에서 `repr` 가림으로 끊는다(RecursionError 없음)."""
    loop: dict[str, object] = {"password_hint": "x", "dsn": "postgresql://u:pw@h/db"}
    loop["self"] = loop
    out = mask_event_dict(None, "info", {"event": "x", "ctx": loop})
    assert "pw@" not in repr(out["ctx"])
    assert loop["dsn"] == "postgresql://u:pw@h/db"  # 호출자 객체는 그대로


def test_event_dict_fan_out_cycle_is_linear() -> None:
    """팬아웃이 있는 순환(`l = [s, l, l]`)도 지수 폭발 없이 끝나고 비밀은 가려진다."""
    loop: list[object] = ["password=x"]
    loop.extend([loop, loop])
    started = time.perf_counter()
    out = mask_event_dict(None, "info", {"l": loop})
    assert time.perf_counter() - started < 0.1
    assert out["l"][0] == "password=***"
    assert "password=x" not in repr(out["l"][1])


def test_event_dict_nested_bad_repr_does_not_block_sibling(monkeypatch) -> None:
    """중첩 dict 안 `__repr__` 예외 값 하나 때문에 형제 `token` 값이 안 가려지는 일이 없다."""
    monkeypatch.setattr(log_masking, "_warned", False)
    bad = _BadRepr()
    out = mask_event_dict(None, "info", {"payload": {"token": "abc", "x": bad}})
    assert out["payload"]["token"] == MASK
    assert out["payload"]["x"] is bad


# ── 정규식 비용(이차 역추적 차단) ──────────────────────────────

_N = 64 * 1024


@pytest.mark.parametrize(
    "text",
    [
        "secret-" * (_N // 7),
        "key " + "a-" * (_N // 2),
        "a+" * (_N // 2) + "://",
        "eyJ" + ".a" * (_N // 2),
        "x password " + "a." * (_N // 2),
        'password="' * (_N // 10),
        "PWD={;" * (_N // 6),
        "x://" + "u:" * (_N // 2) + "@",
        "bearer " * (_N // 7),
        "token=bearer " * (_N // 13),
    ],
    ids=[
        "secret-", "key a-", "a+://", "eyJ.a", "password a.",
        'password="', "PWD={;", "u:@", "bearer", "token=bearer",
    ],
)
def test_adversarial_64kb_input_is_fast(text: str) -> None:
    """적대 입력 64KB도 0.2초 안에 끝난다 — 로깅은 이벤트 루프에서 동기로 돈다.

    입력을 자르지 않는다(잘린 뒤쪽 비밀 누출 방지).
    """
    import time

    started = time.perf_counter()
    mask_secrets(text)
    assert time.perf_counter() - started < 0.2
