"""도구 호출 감사 (plans/87 §5.8 · R-19) — `mcp_server` PromQL `_audit`와 같은 형태(logger 1줄).

조사 id·thread_id를 함께 남겨 `mcp_server` 감사와 합쳐 볼 수 있게 한다. 대상은 hostname 같은 값만
싣고 토큰·원문 텍스트는 싣지 않는다(마스킹본만).
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
    error: str | None = None,
    investigation_id: str | None = None,
    thread_id: str | None = None,
) -> None:
    if error is not None:
        logger.warning(
            "apm audit: tool=%s target=%s elapsed_ms=%.1f api_calls=%d error=%s "
            "investigation_id=%s thread_id=%s",
            tool,
            target,
            elapsed_ms,
            api_calls,
            error,
            investigation_id or "-",
            thread_id or "-",
        )
    else:
        logger.info(
            "apm audit: tool=%s target=%s elapsed_ms=%.1f api_calls=%d rows=%s "
            "investigation_id=%s thread_id=%s",
            tool,
            target,
            elapsed_ms,
            api_calls,
            rows,
            investigation_id or "-",
            thread_id or "-",
        )
