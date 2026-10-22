"""분리 컴포넌트 설정 관리 API 클라이언트 (plans/191 W2 · D-345).

계약: `spec/SPEC-component-config-admin.md` §5.

본체는 컴포넌트 패키지를 import하지 않고(D-139) HTTP JSON 계약(`/admin/config/*`)만 쓴다.

- URL은 **설정값 + 고정 경로**로만 조립한다 — 요청 입력으로 URL·경로를 만들지 않는다(SSRF).
- `follow_redirects=False` — 리다이렉트로 다른 호스트에 토큰을 실어 보내지 않는다.
- 호출은 컴포넌트별로 개별 try/except — 한 컴포넌트 실패가 다른 컴포넌트 결과를 깨지 않는다.
- 실패는 사유 어휘(`connect_error`·`timeout`·`unauthorized`·`not_enabled`·`bad_response`·
  `http_error`)와 문구로 돌려준다(침묵 금지).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import COMPONENT_ADMIN_NAMES, ComponentAdminConfig

logger = logging.getLogger(__name__)

#: 컴포넌트 관리 API 고정 경로 — 이 셋 외의 경로로는 호출하지 않는다.
STATUS_PATH = "/admin/config/status"
SCHEMA_PATH = "/admin/config/schema"
CONFIG_PATH = "/admin/config"

#: 컴포넌트 응답 본문 상한(바이트) — 넘으면 읽기를 멈추고 `bad_response`로 돌려준다
#: (본체 메모리 보호).
MAX_RESPONSE_BYTES = 1024 * 1024

#: PUT 응답 중 본문을 그대로 화면에 돌려주는 상태 코드(저장 결과 · 검증 오류 · 쓰기 차단).
_PUT_RELAY_STATUSES = frozenset({200, 400, 409})

_REASON_MESSAGES: dict[str, str] = {
    "connect_error": "컴포넌트에 연결하지 못했습니다(프로세스 미기동 또는 주소 오류).",
    "timeout": "컴포넌트 응답 시간이 초과되었습니다.",
    "unauthorized": "관리 토큰이 맞지 않습니다(COMPONENT_ADMIN_TOKENS 확인).",
    "not_enabled": "컴포넌트의 설정 관리 API가 꺼져 있습니다(컴포넌트 관리 토큰 미설정).",
    "bad_response": "컴포넌트 응답 형식이 올바르지 않습니다.",
    "http_error": "컴포넌트가 오류를 돌려주었습니다.",
}


@dataclass(frozen=True)
class ComponentResult:
    """컴포넌트 호출 1건의 결과.

    `reason`이 None이면 성공(본문은 `body`), 아니면 실패 사유 어휘와 운영자용 문구를 담는다.
    """

    status_code: int | None = None
    body: dict[str, Any] | None = None
    reason: str | None = None
    message: str | None = None

    @property
    def ok(self) -> bool:
        """성공 여부."""
        return self.reason is None


def _failure(reason: str, detail: str = "", status_code: int | None = None) -> ComponentResult:
    """실패 결과를 만든다(문구 = 사유 기본 문구 + 상세)."""
    message = _REASON_MESSAGES[reason] + (f" ({detail})" if detail else "")
    return ComponentResult(status_code=status_code, reason=reason, message=message)


def resolve_endpoints(config: ComponentAdminConfig) -> tuple[dict[str, str], list[str]]:
    """설정의 endpoints를 허용 이름·http(s) URL로 거른다.

    Returns:
        (이름 → 기준 URL — 허용 상수 순서, 무시한 항목 경고 문구 목록)
    """
    warnings: list[str] = []
    for name in config.endpoints:
        if name not in COMPONENT_ADMIN_NAMES:
            warnings.append(f"알 수 없는 컴포넌트 이름이라 무시했습니다: {name}")
    resolved: dict[str, str] = {}
    for name in COMPONENT_ADMIN_NAMES:
        url = config.endpoints.get(name)
        if url is None:
            continue
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            warnings.append(f"{name}: http(s) URL이 아니라 무시했습니다")
            continue
        resolved[name] = url.rstrip("/")
    return resolved, warnings


def parse_tokens(config: ComponentAdminConfig) -> tuple[dict[str, str], list[str]]:
    """`COMPONENT_ADMIN_TOKENS`(JSON 객체 문자열)를 이름 → 토큰으로 푼다.

    값은 로그·경고에 싣지 않는다. 형식이 틀리면 빈 맵과 경고를 돌려준다(각 호출은 401로 드러난다).
    """
    raw = config.tokens.get_secret_value().strip()
    if not raw:
        return {}, []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}, ["COMPONENT_ADMIN_TOKENS가 JSON 객체가 아니라 토큰 없이 호출합니다"]
    if not isinstance(parsed, dict):
        return {}, ["COMPONENT_ADMIN_TOKENS가 JSON 객체가 아니라 토큰 없이 호출합니다"]
    return {str(k): str(v) for k, v in parsed.items() if isinstance(v, str) and v}, []


class ComponentAdminClient:
    """컴포넌트 `/admin/config/*` 호출기 — 요청마다 만들어 쓴다."""

    def __init__(
        self,
        config: ComponentAdminConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """설정을 해석해 둔다.

        Args:
            config: `AppConfig.component_admin`
            transport: 테스트용 httpx 전송(가짜 컴포넌트 ASGI·MockTransport). 운영은 None.
        """
        self._endpoints, endpoint_warnings = resolve_endpoints(config)
        self._tokens, token_warnings = parse_tokens(config)
        self.warnings: list[str] = endpoint_warnings + token_warnings
        self._timeout = config.timeout_seconds
        self._transport = transport

    def names(self) -> list[str]:
        """호출 가능한 컴포넌트 이름(허용 상수 ∩ 설정 키 · 상수 순서)."""
        return list(self._endpoints)

    def has(self, name: str) -> bool:
        """이름이 호출 가능한 컴포넌트인지."""
        return name in self._endpoints

    async def get_status(self, name: str) -> ComponentResult:
        """`GET /admin/config/status`."""
        return await self._call(name, "GET", STATUS_PATH)

    async def get_schema(self, name: str) -> ComponentResult:
        """`GET /admin/config/schema`."""
        return await self._call(name, "GET", SCHEMA_PATH)

    async def put_config(self, name: str, body: dict[str, Any], request_id: str) -> ComponentResult:
        """`PUT /admin/config` — 본문을 그대로 전달하고 `X-Request-ID`를 싣는다."""
        return await self._call(name, "PUT", CONFIG_PATH, body=body, request_id=request_id)

    async def _call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> ComponentResult:
        """한 컴포넌트를 호출하고 결과를 분류한다(예외를 밖으로 내지 않는다)."""
        base = self._endpoints.get(name)
        if base is None:
            return _failure("http_error", "설정되지 않은 컴포넌트")
        headers: dict[str, str] = {}
        token = self._tokens.get(name)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if request_id:
            headers["X-Request-ID"] = request_id
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                follow_redirects=False,
                timeout=httpx.Timeout(self._timeout),
            ) as client:
                async with client.stream(
                    method, base + path, headers=headers, json=body,
                ) as response:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE_BYTES:
                            logger.warning("컴포넌트 관리 API 응답 크기 초과: %s %s", name, path)
                            return _failure(
                                "bad_response",
                                f"응답 크기 상한 {MAX_RESPONSE_BYTES // 1024}KiB 초과",
                                status_code=response.status_code,
                            )
        except httpx.TimeoutException:
            logger.warning("컴포넌트 관리 API 시간 초과: %s %s", name, path)
            return _failure("timeout", f"{self._timeout:g}초")
        except httpx.TransportError as e:
            logger.warning("컴포넌트 관리 API 연결 실패: %s %s (%s)", name, path, type(e).__name__)
            return _failure("connect_error", type(e).__name__)
        except Exception as e:  # noqa: BLE001 — 한 컴포넌트 실패가 목록 전체를 깨지 않는다
            logger.warning("컴포넌트 관리 API 호출 실패: %s %s (%s)", name, path, type(e).__name__)
            return _failure("http_error", type(e).__name__)
        return _classify(name, method, response, bytes(content))


def _classify(
    name: str, method: str, response: httpx.Response, content: bytes,
) -> ComponentResult:
    """HTTP 응답을 성공(본문)·실패(사유)로 나눈다."""
    status = response.status_code
    if status == 401:
        return _failure("unauthorized", status_code=status)
    if status == 404:
        return _failure("not_enabled", status_code=status)
    relay = status in _PUT_RELAY_STATUSES if method == "PUT" else 200 <= status < 300
    if not relay:
        detail = f"HTTP {status}"
        if 300 <= status < 400:
            detail += " — 리다이렉트는 따라가지 않습니다"
        logger.warning("컴포넌트 관리 API 오류 응답: %s %s", name, detail)
        return _failure("http_error", detail, status_code=status)
    try:
        payload = json.loads(content)
    except ValueError:
        return _failure("bad_response", "JSON 아님", status_code=status)
    if not isinstance(payload, dict):
        return _failure("bad_response", "JSON 객체 아님", status_code=status)
    return ComponentResult(status_code=status, body=payload)
