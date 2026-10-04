"""제니퍼 Open API 클라이언트 (plans/87 §5.2(b)·(e) · §8.1·§8.4 · §0.10 #9~#13).

- 모든 요청은 `allowlist.check_request`를 **먼저** 통과해야 네트워크에 나간다(거부 = HTTP 0회).
- 토큰은 `Authorization: Bearer` 헤더로만 싣는다. 로그·오류 사유에 토큰이 들어가지 않게 가린다.
- `follow_redirects=False` — 리다이렉트를 따라가면 허용목록이 우회된다. 3xx는 오류로 돌려준다.
- timeout은 서버가 강제한다. 응답 크기는 **메모리 임계**다(plans/134 W0-B N-18 · D-296 ④) —
  Content-Length 선검사 또는 스트림 누적이 `max_response_bytes`를 넘으면 스풀 임시 파일로 받고
  `{"result": [...]}` 배열을 원소 단위로 점진 디코드한다(오류로 끊지 않는다 · 파싱 뒤 파일 삭제).
- 초당 호출 상한(토큰 사용량은 **실패 응답까지** 1건씩 센다 — §0.10 #10)을 클라이언트가 지킨다.
  대기열은 호출 맥락의 우선순위(`poller` > `interactive` > `background` · 에이징)로 판다(N-14).
- HTTP 직전에 호출 맥락의 훅을 부른다 — 작업의 동시 실행 슬롯 대기 · 호출 수·임대 시각 갱신.
- 오류는 HTTP 상태가 아니라 본문으로 분류한다(도메인 미접속·파라미터 누락이 모두 500 — §0.10 #11).
- **자격증명 제거**(N-17 · D-296 ③): JSON·텍스트를 파싱한 **직후** 이 클래스의 두 출구
  (`get_json`·`get_text`)와 오류 사유에서 `domain.credentials`를 지난다 — 위 계층은 가린 값만 본다.
  오류 사유는 **가린 뒤 자른다**(자른 뒤 가리면 `@`가 잘려 나간 비밀번호 앞부분이 남는다). 큰 본문의
  자격증명 검사는 스레드에서 돌려 이벤트 루프를 오래 막지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import httpx

from apm_gateway.adapters.jennifer.allowlist import (
    ACCEPT_TEXT,
    build_path,
    check_request,
)
from apm_gateway.adapters.json_stream import load_json_file
from apm_gateway.adapters.throttle import PriorityThrottle
from apm_gateway.config import JenniferApiConfig
from apm_gateway.domain.call_context import current_scope
from apm_gateway.domain.credentials import ROOT_FIELD, scrub_detail, scrub_text
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
_ERROR_HEAD_BYTES = 64 * 1024
# 이보다 큰 본문의 자격증명 검사는 스레드에서 한다(정규식은 짧은 호출의 연속이라 GIL이 사이사이
# 풀린다)
_THREAD_SCRUB_BYTES = 256 * 1024


@dataclass
class _Body:
    """응답 본문 — 메모리(`text`) 또는 임계를 넘어 받은 임시 파일(`path`)."""

    text: str = ""
    path: Path | None = None
    encoding: str = "utf-8"
    size: int = 0

    def head(self) -> str:
        if self.path is None:
            return self.text
        with self.path.open("rb") as fh:
            return fh.read(_ERROR_HEAD_BYTES).decode(self.encoding, errors="replace")

    def read_text(self) -> str:
        if self.path is None:
            return self.text
        return self.path.read_text(encoding=self.encoding, errors="replace")

    def discard(self) -> None:
        if self.path is not None:
            self.path.unlink(missing_ok=True)


class JenniferClient:
    """허용목록 GET 전용 클라이언트. 인스턴스 하나를 프로세스 수명 동안 공유한다."""

    def __init__(
        self,
        cfg: JenniferApiConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        spool_dir: Path | None = None,
        aging_seconds: float = 10.0,
    ) -> None:
        self._cfg = cfg
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._throttle = PriorityThrottle(float(cfg.rate_limit_per_sec), aging_seconds)
        # 메모리 임계를 넘는 응답 본문의 임시 파일 위치(없으면 시스템 임시 디렉터리)
        self._spool_dir = spool_dir
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

    @staticmethod
    def _absorb(notes: list[str], masked: set[str]) -> None:
        """자격증명 검사 메모·가린 칸 이름을 호출 맥락에 넘긴다(작업이면 봉투 `limits`·고지 ·
        아니면 메모는 경고 로그)."""
        scope = current_scope()
        for note in notes:
            scope.note(note)
        if masked:
            scope.masked(sorted(masked))

    async def get_json(
        self,
        template: str,
        params: dict[str, Any] | None = None,
        path_vars: dict[str, Any] | None = None,
    ) -> Any:
        """허용 경로를 GET 해 JSON을 돌려준다(자격증명 제거본). 실패는 `ApmError`."""
        body = await self._get(template, params, path_vars)
        try:
            if body.path is not None:
                parsed = await asyncio.to_thread(load_json_file, body.path, body.encoding)
            else:
                parsed = json.loads(body.text) if body.text else None
            if body.size > _THREAD_SCRUB_BYTES:
                cleaned, notes, masked = await asyncio.to_thread(scrub_detail, parsed)
            else:
                cleaned, notes, masked = scrub_detail(parsed)
        except RecursionError as e:  # 지나치게 깊은 중첩 — 내부 오류가 아니라 응답 오류
            raise ApmError(API_ERROR, f"JSON 파싱 실패(중첩 과다): {template}") from e
        except ValueError as e:
            raise ApmError(API_ERROR, f"JSON 파싱 실패: {template}") from e
        finally:
            body.discard()
        self._absorb(notes, masked)
        return cleaned

    async def get_text(
        self,
        template: str,
        params: dict[str, Any] | None = None,
        path_vars: dict[str, Any] | None = None,
    ) -> str:
        """허용 경로(텍스트 응답)를 GET 한다(자격증명 제거본)."""
        body = await self._get(template, params, path_vars)
        try:
            if body.path is None:
                text = body.read_text()
            else:
                text = await asyncio.to_thread(body.read_text)
        finally:
            body.discard()
        if len(text) > _THREAD_SCRUB_BYTES:
            cleaned = await asyncio.to_thread(scrub_text, text)
        else:
            cleaned = scrub_text(text)
        if cleaned != text:
            self._absorb([], {ROOT_FIELD})
        return cleaned

    async def _get(
        self,
        template: str,
        params: dict[str, Any] | None,
        path_vars: dict[str, Any] | None,
    ) -> _Body:
        if not self.configured:
            raise ApmError(NOT_CONFIGURED, "APM API URL 미설정 — 조회 불가")
        path = build_path(template, path_vars)
        query = {k: str(v) for k, v in (params or {}).items()}
        endpoint = check_request("GET", path, query)  # 거부 = 네트워크 0회
        headers = {
            "Accept": endpoint.accept if endpoint.accept == ACCEPT_TEXT else "application/json"
        }
        scope = current_scope()
        # 작업: 동시 실행 슬롯 대기 · 소스별 호출 수·임대 시각(감사 `api_calls`·`sources`)
        await scope.before_call(self._cfg.source_id)
        await self._throttle.acquire(scope.priority)
        self.calls_total += 1
        started = time.monotonic()
        try:
            async with self._http().stream("GET", path, params=query, headers=headers) as resp:
                body = await self._read_body(resp, path)
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
        try:
            head = body.head()
        finally:
            body.discard()
        raise self._classify(resp.status_code, head, path)

    def _open_spill(self) -> IO[bytes]:
        directory = self._spool_dir
        if directory is not None:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        return tempfile.NamedTemporaryFile(  # noqa: SIM115 — 파싱 뒤 호출자가 지운다
            mode="wb", dir=directory, prefix="apm-resp-", suffix=".body", delete=False
        )

    async def _read_body(self, resp: httpx.Response, path: str) -> _Body:
        """본문을 메모리 임계까지 메모리에, 넘으면 임시 파일로 받는다(오류로 끊지 않는다)."""
        cap = int(self._cfg.max_response_bytes)
        encoding = resp.encoding or "utf-8"
        declared = resp.headers.get("content-length")
        spill: IO[bytes] | None = None
        if declared and declared.isdigit() and int(declared) > cap:
            spill = self._open_spill()
        total = 0
        chunks: list[bytes] = []
        try:
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if spill is None and total > cap:
                    spill = self._open_spill()
                    spill.write(b"".join(chunks))
                    chunks = []
                if spill is None:
                    chunks.append(chunk)
                else:
                    spill.write(chunk)
        except BaseException:
            if spill is not None:
                spill.close()
                Path(spill.name).unlink(missing_ok=True)
            raise
        if spill is None:
            return _Body(text=b"".join(chunks).decode(encoding, errors="replace"), size=total)
        spill.close()
        logger.info(
            "apm 응답 본문이 메모리 임계를 넘어 임시 파일로 받았다: path=%s bytes=%d > %d",
            path,
            total,
            cap,
        )
        return _Body(path=Path(spill.name), encoding=encoding, size=total)

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
        message = scrub_text(_exception_message(body))
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
    """v1 JSON `{"exception":{"message":…}}` · v2 문자열 본문 · 그 밖 텍스트에서 메시지를 뽑는다.

    자르지 않는다 — 호출자가 자격증명을 가린 **뒤** 자른다(본문 앞부분은 이미 64 KiB 상한).
    """
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except (ValueError, RecursionError):
        return text
    if isinstance(parsed, dict):
        exc = parsed.get("exception")
        if isinstance(exc, dict) and exc.get("message"):
            return str(exc["message"])
    if isinstance(parsed, str):
        return parsed
    return text
