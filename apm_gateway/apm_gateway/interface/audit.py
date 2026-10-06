"""도구 호출 감사 (plans/87 §5.8 · R-19) — `mcp_server` PromQL `_audit`와 같은 형태(logger 1줄).

조사 id·thread_id를 함께 남겨 `mcp_server` 감사와 합쳐 볼 수 있게 한다. 대상은 hostname 같은 값만
싣고 토큰·원문 텍스트는 싣지 않는다(마스킹본만). `sources`는 이 호출이 HTTP를 보낸 제니퍼 소스와
소스별 호출 수(`bank:2,common:1` · 없으면 `-`)다 — 어느 서버·토큰을 썼는지 추적한다(plans/87 J8
M-9). `principal`은 전송 토큰으로 정한 호출 주체 이름(토큰 값 아님), `job_id`는 작업 ID다(작업
핸들을 돌려줬거나 결과 파일이 생긴 호출 · 작업 도구 · 백그라운드 작업 종료 — plans/134 W0-B §3.6).
`search`는 인스턴스 이름 검색의 채택 단계·단계별 후보 수다(그 호출에만 · 검색어는 대상 칸에
마스킹본 — plans/130 N-6). 업무명 해석이면 `business(근거:수,…)`다(대상 칸은 `business:<마스킹본>`
— N-2).
모든 칸의 제어·서식 문자는 이스케이프한다(`\n` → `\\n` — 대상·조사 ID 값으로 감사 줄을 위조하지
못하게).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("apm_gateway.audit")


def _safe(value: object) -> str:
    """감사 칸 값 — 출력 가능한 글자만 그대로, 제어·서식 문자는 이스케이프한다."""
    text = str(value)
    if text.isprintable():
        return text
    return "".join(
        ch if ch.isprintable() else ch.encode("unicode_escape").decode("ascii") for ch in text
    )


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
    principal: str = "-",
    job_id: str | None = None,
    search: str | None = None,
) -> None:
    tool, target, sources = _safe(tool), _safe(target), _safe(sources)
    tail = f" search={_safe(search)}" if search else ""
    investigation_id = _safe(investigation_id) if investigation_id else None
    thread_id = _safe(thread_id) if thread_id else None
    principal, job_id = _safe(principal), _safe(job_id) if job_id else None
    if error is not None:
        error = _safe(error)
        logger.warning(
            "apm audit: tool=%s target=%s elapsed_ms=%.1f api_calls=%d sources=%s error=%s "
            "investigation_id=%s thread_id=%s principal=%s job_id=%s",
            tool,
            target,
            elapsed_ms,
            api_calls,
            sources,
            error,
            investigation_id or "-",
            thread_id or "-",
            principal or "-",
            job_id or "-",
        )
    else:
        logger.info(
            "apm audit: tool=%s target=%s elapsed_ms=%.1f api_calls=%d sources=%s rows=%s "
            "investigation_id=%s thread_id=%s principal=%s job_id=%s%s",
            tool,
            target,
            elapsed_ms,
            api_calls,
            sources,
            rows,
            investigation_id or "-",
            thread_id or "-",
            principal or "-",
            job_id or "-",
            tail,
        )
