"""제니퍼 Open API 클라이언트 (plans/87 §5.2(b)·(e) · §8.1·§8.4 · §0.10 #9~#13).

- 모든 요청은 `allowlist.check_request`를 **먼저** 통과해야 네트워크에 나간다(거부 = HTTP 0회).
- 토큰은 `Authorization: Bearer` 헤더로만 싣는다. 로그·오류 사유에 토큰이 들어가지 않게 가린다.
- `follow_redirects=False` — 리다이렉트를 따라가면 허용목록이 우회된다. 3xx는 오류로 돌려준다.
- timeout은 서버가 강제하고, 응답 크기는 Content-Length 선검사 + 스트림 누적 검사로 막는다.
- 초당 호출 상한(토큰 사용량은 **실패 응답까지** 1건씩 센다 — §0.10 #10)을 클라이언트가 지킨다.
- 오류는 HTTP 상태가 아니라 본문으로 분류한다(도메인 미접속·파라미터 누락이 모두 500 — §0.10 #11).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from apm_gateway.adapters.jennifer.allowlist import (
    ACCEPT_TEXT,
    build_path,
    check_request,
)
from apm_gateway.config import JenniferApiConfig
from apm_gateway.domain.errors import (
    API_ERROR,
    CONTRACT_VIOLATION,
    NOT_CONFIGURED,
    QUOTA_EXCEEDED,
    SOURCE_UNAVAILABLE,
    ApmError,
)

logger = logging.getLogger(__name__)

# 제니퍼 오류 본문 표지(§0.10 #11·#12 로컬 실측 문구).
_NOT_CONNECTED = "domain is not connected"
_CONTRACT_MARKERS = ("required request parameter", "cannot parse null string")
_REASON_MAX = 240


class JenniferClient:
    """허용목록 GET 전용 클라이언트. 인스턴스 하나를 프로세스 수명 동안 공유한다."""

    def __init__(
        self,
        cfg: JenniferApiConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cfg = cfg
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._rate_lock = asyncio.Lock()
        self._last_start = 0.0
        self.calls_total = 0

    @property
    def configured(self) -> bool:
        return bool(self._cfg.url)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self._cfg.token:
                headers["Authorization"] = f"Bearer {self._cfg.token}"
            self._client = httpx.AsyncClient(
                base_url=self._cfg.url.rstrip("/"),
                headers=headers,
                timeout=float(self._cfg.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def redact(self, text: str) -> str:
        """오류·로그 문자열에서 토큰을 가리고 길이를 자른다."""
        out = str(text)
        if self._cfg.token:
            out = out.replace(self._cfg.token, "***")
        return out[:_REASON_MAX]

    async def _throttle(self) -> None:
        rate = float(self._cfg.rate_limit_per_sec)
        if rate <= 0:
            return
        async with self._rate_lock:
            wait = self._last_start + 1.0 / rate - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_start = time.monotonic()

    async def get_json(
        self,
        template: str,
        params: dict[str, Any] | None = None,
        path_vars: dict[str, Any] | None = None,
    ) -> Any:
        """허용 경로를 GET 해 JSON을 돌려준다. 실패는 `ApmError`."""
        text = await self._get(template, params, path_vars)
        try:
            return json.loads(text) if text else None
        except ValueError as e:
            raise ApmError(API_ERROR, f"JSON 파싱 실패: {template}") from e

    async def get_text(
        self,
        template: str,
        params: dict[str, Any] | None = None,
        path_vars: dict[str, Any] | None = None,
    ) -> str:
        """허용 경로(텍스트 응답)를 GET 한다."""
        return await self._get(template, params, path_vars)

    async def _get(
        self,
        template: str,
        params: dict[str, Any] | None,
        path_vars: dict[str, Any] | None,
    ) -> str:
        if not self.configured:
            raise ApmError(NOT_CONFIGURED, "APM API URL 미설정 — 조회 불가")
        path = build_path(template, path_vars)
        query = {k: str(v) for k, v in (params or {}).items()}
        endpoint = check_request("GET", path, query)  # 거부 = 네트워크 0회
        headers = {
            "Accept": endpoint.accept if endpoint.accept == ACCEPT_TEXT else "application/json"
        }
        await self._throttle()
        self.calls_total += 1
        started = time.monotonic()
        try:
            async with self._http().stream("GET", path, params=query, headers=headers) as resp:
                body = await self._read_capped(resp)
        except ApmError:
            raise
        except httpx.TimeoutException as e:
            raise ApmError(SOURCE_UNAVAILABLE, f"APM API timeout: {path}") from e
        except httpx.HTTPError as e:
            raise ApmError(
                SOURCE_UNAVAILABLE, self.redact(f"APM API 연결 실패: {path} ({type(e).__name__})")
            ) from e
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.debug(
            "apm http: path=%s keys=%s status=%s elapsed_ms=%.1f",
            path,
            sorted(query),
            resp.status_code,
            elapsed_ms,
        )
        if resp.status_code == 200:
            return body
        raise self._classify(resp.status_code, body, path)

    async def _read_capped(self, resp: httpx.Response) -> str:
        cap = int(self._cfg.max_response_bytes)
        declared = resp.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            raise ApmError(API_ERROR, f"응답 크기 상한 초과(Content-Length {declared} > {cap})")
        total = 0
        chunks: list[bytes] = []
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > cap:
                raise ApmError(API_ERROR, f"응답 크기 상한 초과(> {cap} bytes)")
            chunks.append(chunk)
        return b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")

    def _classify(self, status: int, body: str, path: str) -> ApmError:
        """비200 응답을 오류 코드로 바꾼다 — 본문 기준(§0.10 #11)."""
        if 300 <= status < 400:
            return ApmError(
                API_ERROR, f"리다이렉트 응답(비추종): HTTP {status} {path}", status=status
            )
        if status in (401, 403):
            return ApmError(API_ERROR, f"인증 실패(HTTP {status}) — 토큰 확인 필요", status=status)
        if status == 429:
            return ApmError(
                QUOTA_EXCEEDED, f"토큰 사용량 초과 응답(HTTP 429): {path}", status=status
            )
        message = _exception_message(body)
        lowered = message.lower()
        if _NOT_CONNECTED in lowered:
            return ApmError(
                SOURCE_UNAVAILABLE, self.redact(f"APM 도메인 미접속: {message}"), status=status
            )
        if any(marker in lowered for marker in _CONTRACT_MARKERS):
            logger.warning(
                "apm 계약 위반(게이트웨이 버그 의심): path=%s message=%s",
                path,
                self.redact(message),
            )
            return ApmError(
                CONTRACT_VIOLATION, self.redact(f"요청 계약 위반: {message}"), status=status
            )
        return ApmError(
            API_ERROR, self.redact(f"APM API 오류 HTTP {status}: {message or path}"), status=status
        )


def _exception_message(body: str) -> str:
    """v1 JSON `{"exception":{"message":…}}` · v2 문자열 본문 · 그 밖 텍스트에서 메시지를 뽑는다."""
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return text[:_REASON_MAX]
    if isinstance(parsed, dict):
        exc = parsed.get("exception")
        if isinstance(exc, dict) and exc.get("message"):
            return str(exc["message"])[:_REASON_MAX]
    if isinstance(parsed, str):
        return parsed[:_REASON_MAX]
    return text[:_REASON_MAX]
