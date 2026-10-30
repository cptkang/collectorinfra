"""제니퍼 뷰서버 웹 세션 클라이언트 — 노드 주소 조회 전용 (plans/202 §2.1·§3 · D-355 ①⑥).

웹 내부 API `GET /agent/upgrade/list?sid=`의 `hostAddress`(파드가 도는 노드 IP)는 Open API 토큰으로
읽을 수 없고 웹 세션으로만 읽힌다(§0-8). 그래서 소스별 **전용 조회 권한 웹 계정**으로 로그인한다.

허용목록(코드 상수 · Open API `allowlist.py`와 분리 · 설정으로 넓히지 않는다):

| 메서드 | 경로 | 요청 |
|---|---|---|
| GET | `/login` | 질의·본문 없음 — 로그인 전 세션 쿠키만 받는다(본문은 버린다) |
| POST | `/login/page` | 폼 `id`·`password`·`autoLogin`(`false` 고정)·`redirect`(빈 값 고정) |
| GET | `/agent/upgrade/list` | 질의 `sid`(정수) 하나 |

그 밖의 경로·메서드·질의·폼은 **네트워크 호출 전에** `WebNotAllowedError`로 거부한다(정확 일치 ·
`..`·`//`·`%`·`\\` 거부). `/login/duplicate`는 쓰지 않는다(3소스 404 — §0-16·17).

로그인 순서(브라우저 방식 — b0 https는 이 방식에서만 성립 · §0-17·19): 쿠키를 비우고
`GET /login`으로 `JSESSIONID<포트>`를 받은 뒤 `Origin`·`Referer`·브라우저 `User-Agent`를 붙여
`POST /login/page`.
성공은 `/`로, 실패는 `/login`으로 302다. 리다이렉트는 따라가지 않는다.

세션:
- 소스별 1세션 · 동시 1(`asyncio.Lock` — 대기는 도착 순서). 쿠키는 이 프로세스 메모리에만 둔다.
- `JSESSIONID*` 밖의 쿠키(`ID<포트>`·`PASSWORD<포트>`)는 받는 즉시 버린다(세션 유지에
  불필요 — 실측).
- 로그인 판정: Location 경로에서 `;…` 매개변수를 떼고 소문자·끝 `/`를 정규화한다. 같은 호스트
  `/`면 성공 · `/login*`(또는 200 로그인 화면)이면 로그인 실패(차단기 집계) · 그 밖 3xx·5xx는
  `WebRequestError`(차단기 미집계). 전송 실패·시간 초과는 `WebTransportError`.
- 응답 본문은 스트리밍으로 받으며 상한(목록 4MiB · 로그인 화면 1MiB)을 넘으면 읽기를 멈춘다.
- 웹 주소는 스킴 http/https만 · 사용자 정보가 있으면 그 소스 웹 조회를 끈다(경고 · 값 없음).
- 조회 응답이 `/login` 리다이렉트이거나 HTML(또는 401)이면 세션 만료로 보고 **1회** 재로그인 뒤
  재시도한다. 만료 시간을 가정하지 않는다.
- 로그·예외 사유에는 비밀번호·쿠키 값·응답 본문을 싣지 않는다(상태 코드·경로·예외 형 이름만 ·
  D-300 ③). 사유 문자열은 마지막으로 자격증명 가림(`domain/credentials.scrub_text`)을 지난다.
- `tls_verify=False`면 인증서 검증을 끈다(b0 사설 인증서 · D-355 ⑥ 위험 수용) — 경고 로그 1회.

로그인 실패 연속 횟수에 따른 정지(차단기)는 응용 계층(`application/node_address.py`)이 소스별로
건다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from apm_gateway.adapters.jennifer.web_fields import parse_upgrade_list
from apm_gateway.domain.credentials import scrub_text

logger = logging.getLogger(__name__)

LOGIN_PAGE_PATH = "/login"
LOGIN_POST_PATH = "/login/page"
LIST_PATH = "/agent/upgrade/list"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"
)
SESSION_COOKIE_PREFIX = "JSESSIONID"
DEFAULT_TIMEOUT_SECONDS = 5.0
# 목록 1건 응답 상한(실측 최대 127KB · 도메인당 에이전트 100 상한) — 넘으면 형태 이상으로 본다
MAX_LIST_BYTES = 4 * 1024 * 1024
# 로그인 화면·로그인 POST 응답 본문 상한(읽고 버린다)
MAX_PAGE_BYTES = 1024 * 1024

_LOGIN_FORM_KEYS = frozenset({"id", "password", "autoLogin", "redirect"})
# (메서드, 경로) → 허용 질의 키 — 정본. 폼은 로그인 POST에만 있다.
WEB_ALLOWED: dict[tuple[str, str], frozenset[str]] = {
    ("GET", LOGIN_PAGE_PATH): frozenset(),
    ("POST", LOGIN_POST_PATH): frozenset(),
    ("GET", LIST_PATH): frozenset({"sid"}),
}
_FORBIDDEN_FRAGMENTS = ("//", "..", "%", "\\", "?", "#", "://")


class WebNotAllowedError(ValueError):
    """웹 허용목록 밖 요청 — 네트워크 호출 전에 올린다."""


class WebSessionError(Exception):
    """웹 세션 조회 실패. `code`는 노드 주소 상태 어휘(`login_failed`·`error`)다."""

    code = "error"

    def __init__(self, reason: str) -> None:
        super().__init__(scrub_text(reason))
        self.reason = scrub_text(reason)


class WebLoginFailed(WebSessionError):
    """로그인 거부(자격증명 오류 등) — 차단기 연속 실패에 센다."""

    code = "login_failed"


class WebRequestError(WebSessionError):
    """예상 밖 응답·재로그인 뒤에도 만료·상한 초과 — 차단기에 세지 않는다(실패 간격 대상)."""

    code = "error"


class WebTransportError(WebRequestError):
    """전송 실패·시간 초과(뷰서버 무응답) — 소스 단위 실패 간격 대상."""


def check_web_request(
    method: str,
    path: str,
    params: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
) -> None:
    """요청을 웹 허용목록으로 검사한다. 어긋나면 `WebNotAllowedError`(HTTP를 보내기 **전**)."""
    verb = str(method).upper()
    if not isinstance(path, str) or not path.startswith("/"):
        raise WebNotAllowedError("경로 형식 밖")
    if any(frag in path for frag in _FORBIDDEN_FRAGMENTS):
        raise WebNotAllowedError(f"허용목록 밖 경로 표기: {path}")
    allowed = WEB_ALLOWED.get((verb, path))
    if allowed is None:
        raise WebNotAllowedError(f"웹 허용목록 밖: {verb} {path}")
    keys = dict(params or {})
    if set(keys) != set(allowed):
        raise WebNotAllowedError(f"허용 밖 질의 키: {sorted(keys)} ({verb} {path})")
    if path == LIST_PATH:
        sid = keys["sid"]
        if isinstance(sid, bool) or not (
            isinstance(sid, int) or (isinstance(sid, str) and sid.isdigit() and sid.isascii())
        ):
            raise WebNotAllowedError("sid는 정수여야 한다")
    if verb == "POST":
        body = dict(form or {})
        if set(body) != _LOGIN_FORM_KEYS:
            raise WebNotAllowedError(f"허용 밖 로그인 폼 키: {sorted(body)}")
        if body["autoLogin"] != "false" or body["redirect"] != "":
            raise WebNotAllowedError("로그인 폼 고정값(autoLogin·redirect)이 다르다")
    elif form:
        raise WebNotAllowedError(f"GET에는 폼이 없다: {path}")


@dataclass
class _Reply:
    """응답 머리와 상한 안에서 읽은 본문(스트리밍으로 받는다 — 상한을 넘으면 읽기를 멈춘다)."""

    status_code: int
    headers: httpx.Headers
    body: bytes


def _redirect_target(reply: _Reply, base_netloc: str) -> tuple[str, bool]:
    """(정규화한 Location 경로, 같은 호스트인가). 경로 매개변수(`;jsessionid=…`)를 떼고 소문자로 ·
    끝 `/`를 걷는다(루트는 `/`). Location 원문은 로그·사유에 쓰지 않는다."""
    location = reply.headers.get("location") or ""
    if not location:
        return "", True
    parts = urlsplit(location)
    same_host = not parts.netloc or parts.netloc.lower() == base_netloc.lower()
    path = parts.path.split(";", 1)[0].lower().rstrip("/") or "/"
    return path, same_host


def _is_login_path(path: str) -> bool:
    return path == LOGIN_PAGE_PATH or path.startswith(LOGIN_PAGE_PATH + "/")


def _looks_html(reply: _Reply) -> bool:
    if "html" in (reply.headers.get("content-type") or "").lower():
        return True
    return reply.body[:64].lstrip().startswith(b"<")


def _usable_base(source_id: str, url: str) -> str:
    """웹 주소 검증 — 스킴 http/https · 호스트 있음 · 사용자 정보 없음. 어긋나면 빈 값(그 소스는
    웹 조회 disabled)과 경고(값 없이 소스 id만)."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return ""
    try:
        parts = urlsplit(base)
        ok = parts.scheme in ("http", "https") and bool(parts.hostname)
        userinfo = "@" in parts.netloc
    except ValueError:
        ok, userinfo = False, False
    if ok and not userinfo:
        return base
    logger.warning(
        "노드 주소 조회: 소스 %s 웹 주소가 형식 밖이다(스킴 http/https만 · 사용자 정보 금지)"
        " — 그 소스 웹 조회 disabled",
        source_id,
    )
    return ""


def _same_sid(value: Any, sid: int) -> bool:
    """응답 행의 `sid`가 없거나 요청 sid와 같은가."""
    if value is None or value == "":
        return True
    try:
        return int(str(value).strip()) == int(sid)
    except ValueError:
        return False


class WebSession:
    """소스 하나의 웹 세션. 인스턴스 하나를 프로세스 수명 동안 공유한다."""

    def __init__(
        self,
        *,
        source_id: str,
        url: str,
        user: str,
        password: str,
        tls_verify: bool = True,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.source_id = source_id
        self._base = _usable_base(source_id, url)
        self._netloc = urlsplit(self._base).netloc if self._base else ""
        self._user = user or ""
        self._password = password or ""
        self._tls_verify = bool(tls_verify)
        self._timeout = float(timeout_seconds)
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._logged_in = False
        self.calls_total = 0
        if self.configured and not self._tls_verify:
            logger.warning(
                "노드 주소 조회: 소스 %s 웹 TLS 인증서 검증 off"
                "(WEB_TLS_VERIFY=false · D-355 ⑥ 위험 수용)",
                source_id,
            )

    def __repr__(self) -> str:  # 비밀번호·쿠키 없음
        return f"WebSession(source_id={self.source_id!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self._base and self._user and self._password)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base,
                timeout=self._timeout,
                follow_redirects=False,
                trust_env=False,
                verify=self._tls_verify,
                transport=self._transport,
            )
        return self._client

    def _prune_cookies(self) -> None:
        """세션 쿠키(`JSESSIONID*`) 밖은 받는 즉시 버린다(`ID<포트>`·`PASSWORD<포트>`)."""
        jar = self._http().cookies.jar
        for cookie in list(jar):
            if not cookie.name.upper().startswith(SESSION_COOKIE_PREFIX):
                jar.clear(cookie.domain, cookie.path, cookie.name)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> _Reply:
        """허용목록 검사 → 스트리밍 요청 → 본문을 `max_bytes`까지만 읽는다(Content-Length 선검사).
        넘으면 `WebRequestError`."""
        check_web_request(method, path, params, form)
        if max_bytes is None:
            max_bytes = MAX_PAGE_BYTES
        self.calls_total += 1
        client = self._http()
        too_big = f"{method} {path} 응답이 상한({max_bytes}바이트)을 넘는다"
        chunks: list[bytes] = []
        try:
            request = client.build_request(
                method, path, params=params, data=form, headers=headers
            )
            response = await client.send(request, stream=True)
            try:
                declared = (response.headers.get("content-length") or "").strip()
                if declared.isdigit() and int(declared) > max_bytes:
                    raise WebRequestError(too_big)
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise WebRequestError(too_big)
                    chunks.append(chunk)
            finally:
                await response.aclose()
        except httpx.TimeoutException as e:
            raise WebTransportError(f"{method} {path} 시간 초과({type(e).__name__})") from None
        except httpx.HTTPError as e:
            raise WebTransportError(f"{method} {path} 전송 실패({type(e).__name__})") from None
        finally:
            if self._client is not None:
                self._prune_cookies()
        return _Reply(response.status_code, response.headers, b"".join(chunks))

    async def _login(self) -> None:
        self._logged_in = False
        self._http().cookies.clear()
        parts = urlsplit(self._base)
        origin = f"{parts.scheme}://{parts.netloc}"
        await self._send("GET", LOGIN_PAGE_PATH, headers={"User-Agent": BROWSER_UA})  # 본문 버림
        reply = await self._send(
            "POST",
            LOGIN_POST_PATH,
            form={
                "id": self._user,
                "password": self._password,
                "autoLogin": "false",
                "redirect": "",
            },
            headers={
                "Origin": origin,
                "Referer": self._base + LOGIN_PAGE_PATH,
                "User-Agent": BROWSER_UA,
            },
        )
        status = reply.status_code
        if 300 <= status < 400:
            path, same_host = _redirect_target(reply, self._netloc)
            if same_host and path == "/":
                self._logged_in = True
                return
            if same_host and _is_login_path(path):
                self._login_rejected(f"{status} → 로그인 화면")
            where = "같은 호스트 예상 밖 경로" if same_host else "다른 호스트"
            raise WebRequestError(f"웹 로그인 응답이 예상 밖이다({status} → {where})")
        if status == 200:  # 로그인 화면을 다시 그렸다
            self._login_rejected("200 로그인 화면")
        raise WebRequestError(f"웹 로그인 응답 HTTP {status}")

    def _login_rejected(self, detail: str) -> None:
        logger.warning("노드 주소 조회: 소스 %s 웹 로그인 거부(%s)", self.source_id, detail)
        raise WebLoginFailed(f"웹 로그인 거부({detail})")

    async def _get_list(self, sid: int) -> tuple[str, Any]:
        """('expired' | 'ok', JSON) — 만료는 재로그인 판단용."""
        reply = await self._send(
            "GET",
            LIST_PATH,
            params={"sid": sid},
            headers={"Accept": "application/json, text/plain, */*", "User-Agent": BROWSER_UA},
            max_bytes=MAX_LIST_BYTES,
        )
        if 300 <= reply.status_code < 400:
            path, same_host = _redirect_target(reply, self._netloc)
            if same_host and _is_login_path(path):
                return "expired", None
            raise WebRequestError(f"목록 조회 예상 밖 리다이렉트({reply.status_code})")
        if reply.status_code == 401:
            return "expired", None
        if reply.status_code != 200:
            raise WebRequestError(f"목록 조회 HTTP {reply.status_code}")
        if _looks_html(reply):
            return "expired", None
        try:
            return "ok", json.loads(reply.body)
        except ValueError:
            raise WebRequestError("목록 응답이 JSON이 아니다") from None

    async def fetch_list(self, sid: int) -> list[dict[str, Any]]:
        """도메인(`sid`)의 인스턴스 행 — 허용 칸만(`web_fields.parse_upgrade_list`). 행의 `sid`가
        요청과 다르면 그 행을 버린다.

        실패: `WebLoginFailed`(로그인 거부) · `WebTransportError`(전송 실패·시간 초과) ·
        `WebRequestError`(응답 이상) · `WebShapeError`(모양 다름) · `WebNotAllowedError`(허용목록
        밖 — 네트워크 호출 0).
        """
        if not self.configured:
            raise WebRequestError("웹 계정 미설정")
        check_web_request("GET", LIST_PATH, {"sid": sid})
        async with self._lock:
            if not self._logged_in:
                await self._login()
            state, data = await self._get_list(sid)
            if state == "expired":
                logger.info("노드 주소 조회: 소스 %s 웹 세션 만료 — 1회 재로그인", self.source_id)
                await self._login()
                state, data = await self._get_list(sid)
                if state == "expired":
                    self._logged_in = False
                    raise WebRequestError("재로그인 뒤에도 웹 세션이 만료 응답이다")
            rows = parse_upgrade_list(data)
            del data  # 허용 칸 밖(`jmx.*` 포함) 원본을 더 붙잡지 않는다
            return [row for row in rows if _same_sid(row.get("sid"), sid)]

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        self._logged_in = False
