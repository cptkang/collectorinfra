"""FabriX KBGenAI 업스트림 호출 (plans/148 §3.2·§3.6·§3.7).

이식(복사·축약 — import하지 않는다):
- `src/clients/fabrix_kbgenai.py` `KBGenAIChat._agenerate_impl` — 헤더 · `raise_for_status` ·
  비dict 거부 · `status != "SUCCESS"` 거부 · junk 토큰 제거.
- `src/security/pii_filter.py` `is_filter_blocked`·`_normalize_reasons`·`_APIM_BLOCK_MARK` —
  PII 필터 차단 판정(통과 표지 FR-200은 차단이 아니다).

업스트림 재시도는 0이다(D-268). 벽시계 총상한은 호출자(app)가 교정 재질의까지 묶어 건다.
로그에 페이로드·응답 본문·키·URL을 싣지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from fabrix_proxy.config import ProxySettings
from fabrix_proxy.tool_protocol import remove_llm_junk

logger = logging.getLogger(__name__)

# 이식: src/security/pii_filter.py `_APIM_BLOCK_MARK`(APIM 필터 400 detail 표지)
APIM_BLOCK_MARK = "민감정보 감지됨"
_HTTP_LOGGERS = ("httpx", "httpcore")


def quiet_http_loggers() -> None:
    """httpx·httpcore 로거를 WARNING으로 올린다 — INFO/DEBUG에서 요청 URL(업스트림)을 찍는다.

    엔트리(`__main__`) 밖에서 앱·업스트림을 써도 막히도록 `create_app`과 `KBGenAIUpstream`이 부른다.
    """
    for name in _HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


class UpstreamError(Exception):
    """업스트림 실패(HTTP 오류 · 비dict · status != SUCCESS). 메시지에 본문을 싣지 않는다."""


class ContentFilterError(Exception):
    """FabriX PII 필터 차단."""


@dataclass
class UpstreamResult:
    """업스트림 응답 1건 — junk 제거된 텍스트와(있으면) usage."""

    text: str
    usage: dict[str, Any] | None = None


class Upstream(Protocol):
    """프록시가 쓰는 업스트림 인터페이스 — 테스트는 가짜를 주입한다."""

    async def complete(self, payload: dict[str, Any]) -> UpstreamResult:
        """KBGenAI 페이로드 1건을 보내고 텍스트를 받는다."""
        ...

    async def native(self, body: dict[str, Any]) -> dict[str, Any]:
        """passthrough — OpenAI 바디를 네이티브 엔드포인트로 보내고 응답 dict를 받는다."""
        ...


def _find_all(obj: Any, key: str) -> list[Any]:
    found: list[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                found.append(v)
            else:
                found.extend(_find_all(v, key))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_find_all(item, key))
    return found


def _has_block_reason(result: Any) -> bool:
    """이식: `_normalize_reasons` — FR-400 · policy_id 존재 · "blocked" 문구만 차단 사유로 본다."""
    for key in ("filterBlockReason", "filter_block_reason"):
        for block in _find_all(result, key):
            candidates = block if isinstance(block, list) else [block]
            for cand in candidates:
                if not isinstance(cand, dict):
                    cand = {"ko": str(cand)}
                result_code = str(cand.get("result_code") or "").upper()
                message = str(cand.get("message") or "")
                pids = cand.get("policy_id")
                pid_list = pids if isinstance(pids, list) else [pids]
                if (
                    result_code == "FR-400"
                    or "blocked" in message.lower()
                    or any(p not in (None, "", "null") for p in pid_list)
                ):
                    return True
    return False


def is_filter_blocked(result: Any = None, raw_text: str | None = None) -> bool:
    """응답(dict) 또는 원문 문자열이 PII 필터 차단인지 판정한다(이식: `is_filter_blocked`)."""
    if isinstance(result, (dict, list)):
        for status in _find_all(result, "status"):
            if str(status).upper() == "FILTER_INVALID":
                return True
        if _has_block_reason(result):
            return True
    blob = raw_text or ""
    if isinstance(result, dict):
        blob += " " + str(result.get("content", ""))
    low = blob.lower()
    return (
        "blocked by the filter" in low
        or "filter_invalid" in low
        or "filterblockreason" in low
        or APIM_BLOCK_MARK in blob
    )


class KBGenAIUpstream:
    """실 FabriX KBGenAI 호출자(비스트림). 업스트림 재시도 0."""

    def __init__(self, settings: ProxySettings) -> None:
        self._settings = settings
        quiet_http_loggers()

    def _headers(self) -> dict[str, str]:
        return {
            "x-openapi-token": f"Bearer {self._settings.fabrix_api_key}",
            "x-generative-ai-client": self._settings.fabrix_client_key,
            "Content-Type": "application/json",
        }

    async def _post(self, url: str, body: dict[str, Any]) -> Any:
        if not url:
            raise UpstreamError("업스트림 URL 미설정")
        async with httpx.AsyncClient(verify=self._settings.fabrix_verify_ssl) as client:
            response = await client.post(
                url, json=body, headers=self._headers(), timeout=self._settings.fabrix_timeout
            )
            if response.status_code >= 400:
                if is_filter_blocked(raw_text=response.text):
                    raise ContentFilterError("PII 필터 차단(HTTP 오류 응답)")
                raise UpstreamError(f"업스트림 HTTP {response.status_code}")
            try:
                return response.json()
            except ValueError as exc:
                raise UpstreamError("업스트림 응답이 JSON이 아니다") from exc

    async def complete(self, payload: dict[str, Any]) -> UpstreamResult:
        """KBGenAI 비스트림 호출(이식: `_agenerate_impl`)."""
        result = await self._post(self._settings.fabrix_base_url, payload)
        if not isinstance(result, dict):
            raise UpstreamError("업스트림 응답이 객체가 아니다")
        if is_filter_blocked(result):
            logger.warning("FabriX PII 필터 차단 응답")
            raise ContentFilterError("PII 필터 차단")
        if result.get("status") != "SUCCESS":
            raise UpstreamError(f"업스트림 status={result.get('status')!s:.40}")
        usage = result.get("usage")
        return UpstreamResult(
            text=remove_llm_junk(str(result.get("content") or "")),
            usage=usage if isinstance(usage, dict) else None,
        )

    async def native(self, body: dict[str, Any]) -> dict[str, Any]:
        """passthrough 호출 — 네이티브 OpenAI 호환 엔드포인트로 바디를 그대로 보낸다.

        인증 헤더는 KBGenAI와 같은 `x-openapi-token`·`x-generative-ai-client`로 **추정**한다
        (네이티브 엔드포인트의 인증 방식은 내부망 실측 전이다).
        """
        result = await self._post(self._settings.fabrix_native_url, body)
        if not isinstance(result, dict):
            raise UpstreamError("네이티브 응답이 객체가 아니다")
        if is_filter_blocked(result):
            raise ContentFilterError("PII 필터 차단")
        return result
