"""로그 비밀값 가림 — 레코드 생성 시점에 비밀번호·토큰·API 키 꼴 값을 `***`로 바꾼다 (plans/167).

부착은 `install_log_record_factory()` 한 번이다. `logging.setLogRecordFactory`를 감싸 레코드가
만들어질 때 가리므로, 어느 로거·핸들러로 가든(`propagate=False`인 uvicorn 로거 포함) 같은 가린
레코드를 쓴다. structlog 경로는 `mask_event_dict`를 렌더러 바로 앞 프로세서로 단다.

가리는 꼴(치환 표지 `***` · 두 번 적용해도 결과가 같다):
1. URL·DSN 사용자 정보의 비밀번호 — `scheme://user:pw@host` → `scheme://user:***@host`
2. 민감 키 이름의 값 — 키가 `password`·`passwd`·`pwd`·`passphrase`·`token`·`api_key`·`apikey`·
   `access_key`·`private_key`·`client_key`·`client_secret`·`credential(s)`로 끝나거나 `secret`을
   포함하면(대소문자 무시) 값을 가린다. `=`·`:` 바로 뒤에 값이 붙은 꼴(`k=v`·`k:v`)과, 키나 값이
   따옴표로 둘러싸인 JSON·dict 꼴(`"k": "v"`·`'k': 'v'`·`k = 'v'`), 헤더 꼴 키(`X-Api-Key: v`)만
   본다 — 따옴표 없는 `token: expired`처럼 띄어 쓴 문장은 남긴다. ODBC 중괄호 값(`PWD={…}`)·
   따옴표 안 이스케이프(`\"`)를 처리한다. `max_tokens`·`token_count`·`stream_key`는 남는다.
3. 헤더 값 — `Authorization`·`Proxy-Authorization`(스킴 낱말은 남김)·`X-Generative-AI-Client`·
   `Cookie`·`Set-Cookie`(줄 끝·따옴표 앞까지)
4. `Bearer <토큰>` → `Bearer ***`
5. 쿼리의 맨 `key=`(`?`·`&` 바로 뒤)
6. JWT 꼴(`eyJ….….…`) · Google API 키 꼴(`AIza` + 35자)

정규식은 역추적이 입력 길이에 선형이 되게 쓴다(시작 위치 제한·소유 한정자·길이 상한) — 로깅은
이벤트 루프에서 동기로 돌기 때문이다. 입력을 잘라 내지는 않는다(잘린 뒤쪽 비밀 누출 방지).

비밀 꼴이 없는 레코드는 `msg`·`args`를 건드리지 않는다. uvicorn 접근 로그(`uvicorn.access`)는
포매터가 `args` 튜플을 직접 풀어 쓰므로 인자 단위로만 가린다. 가림 도중 예외가 나면 원 레코드를
그대로 통과시키고 표준 오류에 1회만 경고한다(logging 재진입을 피한다).

표준 라이브러리만 쓴다 — 패키지 경계(D-139)를 넘지 않도록 아래 사본을 둔다.
정본: `src/utils/log_masking.py`
사본: `mcp_server/mcp_server/log_masking.py` · `apm_gateway/apm_gateway/adapters/log_masking.py` ·
`sre_agent/sre_agent/infrastructure/log_masking.py` · `fabrix_proxy/fabrix_proxy/log_masking.py`
**정본을 고치면 사본 4개에 파일째 그대로 복사한다** — 바이트 일치를 루트 테스트
(`tests/test_utils/test_log_masking_copies.py`)가 단언한다.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

MASK = "***"

#: 1차 검사 — 이 낱말·기호가 하나도 없으면 정규식 치환을 건너뛴다.
_CANDIDATE_RE = re.compile(
    r"(?i:://|bearer|passw|passph|pwd|token|key|secret|credential|authorization|cookie|ai-client)"
    r"|eyJ|AIza"
)

#: 1. URL·DSN 사용자 정보. 비밀번호에 `@`·`:`가 섞여도 호스트 앞 마지막 `@`까지를 비밀번호로 본다.
#: 스킴은 식별자 문자 뒤에서 시작하지 않고 32자 이하다(긴 `a.b-c+…` 토큰의 이차 역추적 차단).
_URL_USERINFO_RE = re.compile(
    r"(?P<head>(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}://[^\s:/@'\"<>]*+:)"
    r"(?P<value>[^\s/?#'\"<>]+)"
    r"(?=@[^\s@/?#'\"<>]+)"
)

#: 2. 키 후보 — 식별자를 통째로(소유 한정자) 잡고 민감 여부는 `_is_sensitive_name`이 판정한다.
_KEY_HEAD_RE = re.compile(
    r"(?<![A-Za-z0-9_.\-])(?P<qk>[\"']?)(?P<key>[A-Za-z0-9_.\-]++)(?P=qk)"
    r"(?P<pre>[ \t]*+)(?P<op>[:=])(?P<post>[ \t]*+)"
)
#: 2. 값 — 따옴표(이스케이프 포함 · bytes 접두 허용) · ODBC 중괄호 · 맨값.
_VALUE_RE = re.compile(
    r"(?P<bp>[bB]?)(?:\"(?P<dq>(?:\\.|[^\"\\\n])*+)\"|'(?P<sq>(?:\\.|[^'\\\n])*+)')"
    r"|\{(?P<br>[^}\n]{0,256})\}"
    r"|(?P<bare>(?!//)[^\s'\",;&)\]}]++)"
)
_SENSITIVE_SUFFIXES = (
    "password", "passwd", "pwd", "passphrase", "token",
    "api_key", "api-key", "apikey", "access_key", "access-key", "accesskey",
    "private_key", "private-key", "privatekey", "client_key", "client-key", "clientkey",
    "credential", "credentials",
)  # fmt: skip
#: structlog 키 기반 전체 가림에만 더하는 헤더 이름(텍스트에서는 3. 헤더 규칙이 가린다).
_SENSITIVE_FIELD_NAMES = frozenset(
    {"authorization", "proxy-authorization", "cookie", "set-cookie", "x-generative-ai-client"}
)

#: 3. 인증 헤더 값. 스킴 낱말은 남기고 자격 증명만 가린다.
_AUTH_HEADER_RE = re.compile(
    r"(?i)(?P<head>\b(?:(?:proxy-)?authorization|x-generative-ai-client)[\"']?\s*[:=]\s*[\"']?"
    r"(?:(?:bearer|basic|token|digest|negotiate|ntlm|apikey)\s+)?)"
    r"(?P<value>[^\s'\",;)}]+)"
)
#: 3. 쿠키 헤더 값(줄 끝·따옴표 앞까지).
_COOKIE_RE = re.compile(
    r"(?i)(?P<head>(?<![A-Za-z0-9_\-])(?:set-)?cookie[\"']?[ \t]*[:=][ \t]*[\"']?)"
    r"(?P<value>[^\r\n'\"]+)"
)

#: 4. `Bearer <토큰>` — 뒤에 `=`·`:<글자>`가 이어지면 토큰이 아니라 키 이름이다(`Bearer token=…`).
#: `:` 뒤가 공백이면 문장 부호다(`Bearer abc: rejected`).
#: 앞이 `/`이면 URL 경로 조각이다(`GET /auth/bearer HTTP/1.1`).
_BEARER_RE = re.compile(
    r"(?i)(?P<head>(?<![/A-Za-z0-9_])bearer\s+)(?P<value>(?>[A-Za-z0-9\-._~+/]+=*))(?!=|:\S|\*)"
)

#: 5. 쿼리의 맨 `key=`.
_QUERY_KEY_RE = re.compile(r"(?i)(?P<head>[?&]key=)(?P<value>[^\s&#'\"]+)")

#: 6. JWT · Google API 키.
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]++\.[A-Za-z0-9_\-]++\.[A-Za-z0-9_\-]*+")
_GOOGLE_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{35}")


#: 민감 키의 맨값이 인증 스킴 낱말이면 뒤따르는 토큰이 실제 값이다(`x-openapi-token: Bearer abc`).
_SCHEME_TOKEN_RE = re.compile(
    r"(?i)(?:bearer|basic|token|digest|negotiate|ntlm|apikey)(?P<sp>[ \t]++)"
    r"(?P<tok>[^\s'\",;&)\]}]++)"
)


def _is_sensitive_name(name: str) -> bool:
    """키 이름이 민감 끝말로 끝나거나 `secret`을 포함하는지(대소문자 무시)."""
    lowered = name.lower()
    return "secret" in lowered or lowered.endswith(_SENSITIVE_SUFFIXES)


def _mask_value(match: re.Match[str]) -> str:
    return match.group("head") + MASK


def _mask_key_values(text: str) -> str:
    """민감 키의 값을 가린다. 민감하지 않은 키는 값을 소비하지 않고 다음 키를 찾는다."""
    parts: list[str] = []
    last = pos = 0
    while True:
        head = _KEY_HEAD_RE.search(text, pos)
        if head is None:
            break
        pos = head.end("key")
        if not _is_sensitive_name(head.group("key")):
            continue
        value = _VALUE_RE.match(text, head.end())
        if value is None:
            continue
        quoted_value = value.group("dq") is not None or value.group("sq") is not None
        spaced = bool(head.group("pre") or head.group("post"))
        header_key = head.group("op") == ":" and "-" in head.group("key")
        if spaced and not (quoted_value or head.group("qk") or header_key):
            continue
        if value.group("dq") is not None:
            if not value.group("dq"):
                continue
            masked = f'{value.group("bp")}"{MASK}"'
        elif value.group("sq") is not None:
            if not value.group("sq"):
                continue
            masked = f"{value.group('bp')}'{MASK}'"
        elif value.group("br") is not None:
            masked = "{" + MASK + "}"
        else:
            masked = MASK
            scheme = _SCHEME_TOKEN_RE.match(text, value.start())
            if scheme is not None and scheme.start("sp") == value.end():
                # 스킴 낱말은 남기고 토큰을 가린다 — 스킴만 가리면 토큰이 샌다.
                parts.append(text[last : scheme.start("tok")])
                parts.append(MASK)
                last = pos = scheme.end()
                continue
        parts.append(text[last : value.start()])
        parts.append(masked)
        last = pos = value.end()
    if not parts:
        return text
    parts.append(text[last:])
    return "".join(parts)


def mask_secrets(text: str) -> str:
    """문자열의 비밀값 꼴을 `***`로 가린다. 비밀 꼴이 없으면 같은 객체를 돌려준다.

    적용 순서: URL → 민감 키 → 헤더 → 쿠키 → Bearer → 쿼리 `key=` → JWT → Google 키. 민감 키를
    Bearer보다 먼저 가려 `Bearer token=abc` 꼴의 값이 새지 않게 한다.
    """
    if not _CANDIDATE_RE.search(text):
        return text
    masked = _URL_USERINFO_RE.sub(_mask_value, text)
    masked = _mask_key_values(masked)
    masked = _AUTH_HEADER_RE.sub(_mask_value, masked)
    masked = _COOKIE_RE.sub(_mask_value, masked)
    masked = _BEARER_RE.sub(_mask_value, masked)
    masked = _QUERY_KEY_RE.sub(_mask_value, masked)
    masked = _JWT_RE.sub(MASK, masked)
    masked = _GOOGLE_KEY_RE.sub(MASK, masked)
    return text if masked == text else masked


# ── 레코드 팩토리 ─────────────────────────────────────────────

_INSTALLED_MARK = "_log_secret_masking"
_ACCESS_LOGGER = "uvicorn.access"
_EXC_FORMATTER = logging.Formatter()
_warned = False


def _warn_once(exc: BaseException) -> None:
    """가림 실패를 표준 오류에 1회만 알린다 — logging을 쓰면 팩토리에 재진입한다."""
    global _warned
    if _warned:
        return
    _warned = True
    try:
        sys.stderr.write(
            f"log_masking: 가림 실패 — 원 레코드를 그대로 기록함 ({type(exc).__name__})\n"
        )
    except Exception:  # noqa: BLE001 — 표준 오류마저 못 쓰면 조용히 넘긴다
        pass


def _render(msg: object, args: Any) -> str:
    """`LogRecord.getMessage()`와 같은 규칙으로 렌더한다."""
    text = str(msg)
    if args:
        text = text % args
    return text


def _mask_args(args: Any) -> Any:
    """인자 중 문자열만 가린다. 바뀐 것이 없으면 원 객체를 돌려준다."""
    if isinstance(args, tuple):
        masked = tuple(mask_secrets(a) if isinstance(a, str) else a for a in args)
        return args if all(m is a for m, a in zip(masked, args)) else masked
    if isinstance(args, Mapping):
        changed = {k: mask_secrets(v) for k, v in args.items() if isinstance(v, str)}
        if all(changed[k] is args[k] for k in changed):
            return args
        return {**args, **changed}
    return args


def _masked_message(record: logging.LogRecord) -> tuple[object, Any] | None:
    """가린 `(msg, args)`를 돌려준다. 바꿀 것이 없거나 렌더가 실패하면 None.

    인자만 가려서 전체를 가린 결과와 같아지면 `args`를 유지한다 — uvicorn 접근 로그 포매터처럼
    `record.args`를 직접 풀어 쓰는 포매터가 깨지지 않게 한다. 아니면 `msg`를 가린 문자열로 바꾸고
    `args`를 비운다. `uvicorn.access` 레코드는 인자 단위 가림만 한다.
    """
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 — 렌더 실패는 logging 자체 오류 처리에 맡긴다
        return None
    masked = mask_secrets(message)
    if masked is message:
        return None
    if record.name == _ACCESS_LOGGER and isinstance(record.args, tuple):
        # 접근 로그 포매터는 `args` 5-튜플을 직접 푼다 — 인자만 가리고 튜플은 절대 비우지 않는다.
        masked_args = _mask_args(record.args)
        return None if masked_args is record.args else (record.msg, masked_args)
    if record.args:
        masked_args = _mask_args(record.args)
        if masked_args is not record.args:
            try:
                if _render(record.msg, masked_args) == masked:
                    return record.msg, masked_args
            except Exception:  # noqa: BLE001 — 아래 전체 가림으로 간다
                pass
    return masked, None


def mask_record(record: logging.LogRecord) -> None:
    """레코드의 렌더 메시지·예외 트레이스백·스택 정보를 가린다(제자리 변경).

    바꿀 값을 모두 계산한 뒤 한 번에 대입한다 — 도중 예외면 레코드를 그대로 둔다.
    """
    try:
        new_message = _masked_message(record)
        new_exc_text: str | None = None
        if record.exc_info and not record.exc_text:
            exc_text = _EXC_FORMATTER.formatException(record.exc_info)
            masked_exc = mask_secrets(exc_text)
            if masked_exc is not exc_text:
                new_exc_text = masked_exc
        new_stack: str | None = None
        if record.stack_info:
            masked_stack = mask_secrets(record.stack_info)
            if masked_stack is not record.stack_info:
                new_stack = masked_stack
    except Exception as exc:  # noqa: BLE001 — 가림 실패가 로깅을 막지 않는다
        _warn_once(exc)
        return
    if new_message is not None:
        record.msg, record.args = new_message
    if new_exc_text is not None:
        record.exc_text = new_exc_text
    if new_stack is not None:
        record.stack_info = new_stack


def install_log_record_factory() -> None:
    """현재 레코드 팩토리를 가림 팩토리로 감싼다. 이미 감쌌으면 아무것도 하지 않는다(멱등)."""
    current = logging.getLogRecordFactory()
    if getattr(current, _INSTALLED_MARK, False):
        return

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        mask_record(record)
        return record

    setattr(factory, _INSTALLED_MARK, True)
    logging.setLogRecordFactory(factory)


# ── structlog 프로세서 ────────────────────────────────────────


_PRIMITIVES = (int, float, bool, type(None))
#: 중첩 컨테이너를 따라 내려가는 깊이 상한 — 넘으면 `repr` 가림으로 끊는다.
_MAX_DEPTH = 20
#: 컨테이너 방문 표지 — 경로 위에 있는(가리는 중인) 컨테이너.
_IN_PROGRESS = object()
_MISSING = object()


def _mask_repr(value: Any) -> Any:
    """`repr`을 가려 바뀌었을 때만 가린 문자열을 돌려준다. `repr`이 실패하면 원값 그대로."""
    try:
        text = repr(value)
    except Exception as exc:  # noqa: BLE001 — 값 하나의 실패가 형제 값 가림을 막지 않게 한다
        _warn_once(exc)
        return value
    masked_text = mask_secrets(text)
    return value if masked_text is text else masked_text


def _mask_structure(value: Any, depth: int = 0, seen: dict[int, Any] | None = None) -> Any:
    """문자열·중첩 dict·list·tuple을 가린다.

    바뀐 것이 없으면 원 객체를 돌려준다 — 호출자가 넘긴 중첩 객체는 바꾸지 않는다. 원시형·컨테이너가
    아닌 값(예외·bytes·임의 객체)과 깊이 상한을 넘은 컨테이너는 JSONRenderer가 `repr`로 찍으므로
    `repr`을 가려 바뀌었을 때만 가린 문자열로 바꾼다. `seen`(id → 결과)으로 같은 컨테이너는 한 번만
    가리고, 경로 위의 컨테이너로 되돌아오는 순환 참조는 `repr` 가림으로 끊는다 — 팬아웃이 있는
    순환·공유 참조도 선형으로 끝난다.
    """
    if isinstance(value, str):
        return mask_secrets(value)
    if isinstance(value, _PRIMITIVES):
        return value
    if depth < _MAX_DEPTH and isinstance(value, (Mapping, list, tuple)):
        if seen is None:
            seen = {}
        done = seen.get(id(value), _MISSING)
        if done is _IN_PROGRESS:
            return _mask_repr(value)
        if done is not _MISSING:
            return done
        seen[id(value)] = _IN_PROGRESS
        result: Any
        if isinstance(value, Mapping):
            items = {k: _mask_item(k, v, depth + 1, seen) for k, v in value.items()}
            changed = not all(items[k] is v for k, v in value.items())
            result = items if changed else value
        else:
            masked = [_mask_structure(v, depth + 1, seen) for v in value]
            changed = not all(m is v for m, v in zip(masked, value))
            if not changed:
                result = value
            else:
                result = masked if isinstance(value, list) else tuple(masked)
        seen[id(value)] = result
        return result
    return _mask_repr(value)


def _mask_item(
    key: object, value: Any, depth: int = 0, seen: dict[int, Any] | None = None
) -> Any:
    """키 이름이 민감하면 값 전체를, 아니면 값 안의 비밀 꼴을 가린다."""
    if (
        isinstance(key, str)
        and value is not None
        and not isinstance(value, bool)
        and (_is_sensitive_name(key) or key.lower() in _SENSITIVE_FIELD_NAMES)
    ):
        return MASK
    return _mask_structure(value, depth, seen)


def mask_event_dict(
    logger: Any, method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog 프로세서 — 렌더러 바로 앞에 둔다. 최상위 dict만 제자리로 바꾼다.

    값 하나를 가리다 실패하면(`__repr__` 예외 등) 그 값만 그대로 두고 1회 경고한다 — 로그 호출
    지점으로 예외를 올리지 않는다.
    """
    seen: dict[int, Any] = {}
    for key, value in list(event_dict.items()):
        try:
            masked = _mask_item(key, value, 0, seen)
        except Exception as exc:  # noqa: BLE001 — 가림 실패가 로그 호출을 깨지 않게 한다
            _warn_once(exc)
            continue
        if masked is not value:
            event_dict[key] = masked
    return event_dict


__all__ = [
    "MASK",
    "install_log_record_factory",
    "mask_event_dict",
    "mask_record",
    "mask_secrets",
]
