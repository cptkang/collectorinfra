"""도구 호출 감사 (plans/87 §5.8 · R-19) — `mcp_server` PromQL `_audit`와 같은 형태(logger 1줄).

조사 id·thread_id를 함께 남겨 `mcp_server` 감사와 합쳐 볼 수 있게 한다. 대상은 hostname 같은 값만
싣고 토큰·원문 텍스트는 싣지 않는다(마스킹본만). `sources`는 이 호출이 HTTP를 보낸 제니퍼 소스와
소스별 호출 수(`bank:2,common:1` · 없으면 `-`)다 — 어느 서버·토큰을 썼는지 추적한다(plans/87 J8
M-9).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("apm_gateway.audit")


def audit(
    tool: str,
    target: str,
    elapsed_ms: float,
    *,
    rows: int | None = None,
    api_calls: int = 0,
    sources: str = "-",
    error: str | None = None,
    investigation_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    if error is not None:
        logger.warning(
            "apm audit: tool=%s target=%s elapsed_ms=%.1f api_calls=%d sources=%s error=%s "
            "investigation_id=%s thread_id=%s",
            tool,
            target,
            elapsed_ms,
            api_calls,
            sources,
            error,
            investigation_id or "-",
            thread_id or "-",
        )
    else:
        logger.info(
            "apm audit: tool=%s target=%s elapsed_ms=%.1f api_calls=%d sources=%s rows=%s "
            "investigation_id=%s thread_id=%s",
            tool,
            target,
            elapsed_ms,
            api_calls,
            sources,
            rows,
            investigation_id or "-",
            thread_id or "-",
        )
