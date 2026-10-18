"""장기 작업 대기 · 진행 문구 · 게이트웨이 고지.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from typing import Any

from src.clients.source_mcp_client import SourceMcpError
from src.domain import disclosure as disc
from src.orchestration import apm_jobs as jobs
from src.orchestration.apm.calls import (
    _FIRST_HOP_TIMEOUT_ERROR,
    ROWS_IN_FILE_KEY,
    _Call,
    _envelope_error,
    _JobScope,
)
from src.orchestration.apm.common import APM_QUERY_AGENT

logger = logging.getLogger("src.orchestration.apm_query")


# ── 장기 작업 (plans/134 W0-B · SPEC-apm-question-coverage §3.8) ───────────────

def _progress_text(handle: dict[str, Any]) -> str:
    """예상 시간 · 진행 — 게이트웨이 `estimate`·`progress` 그대로(모르면 모른다고 쓴다)."""
    raw_estimate, raw_progress = handle.get("estimate"), handle.get("progress")
    estimate: dict[str, Any] = raw_estimate if isinstance(raw_estimate, dict) else {}
    progress: dict[str, Any] = raw_progress if isinstance(raw_progress, dict) else {}
    seconds, api_calls = estimate.get("seconds"), estimate.get("api_calls")
    if isinstance(seconds, (int, float)) and seconds > 0:
        eta = (f"예상 약 {round(seconds / 60, 1):g}분" if seconds >= 120
               else f"예상 약 {seconds:g}초")
        if api_calls:
            eta += f"(API 호출 {api_calls}회)"
    else:
        eta = "예상 시간 미정"
    done, total = progress.get("done"), progress.get("total")
    unit = progress.get("label") or "API 호출"
    if isinstance(done, int):
        if isinstance(total, int) and total:
            return f"{eta} · 진행 {done}/{total} {unit}"
        return f"{eta} · 진행 {unit} {done}회"
    return eta


async def _await_job(session: Any, call: _Call, scope: _JobScope, store: Any) -> None:
    """작업 1개를 조회 마감까지 기다린다 — 끝나면 미리보기 봉투로 바꾸고, 아니면 접수로 둔다."""
    if call.job is None:
        return
    job_id = str(call.job["job_id"])
    status = await jobs.poll_job(session, job_id, scope.owner, until=scope.poll_until)
    if status is None:
        call.accepted = True
        return
    if status.get("error"):
        call.error = _envelope_error(status)
        return
    if store is not None:
        try:
            await store.update(job_id, jobs.ledger_fields(status))
        except Exception as e:  # noqa: BLE001 — 상태 캐시 갱신 실패가 답을 막지 않는다
            logger.warning("APM 작업 장부 갱신 실패(job_id=%s): %s", job_id[:8], e)
    call.job = jobs.job_handle(status) or call.job
    state = call.job.get("state")
    if state in jobs.LIVE_STATES:
        call.accepted = True
    elif state in jobs.READABLE_STATES:
        call.envelope = jobs.result_envelope(status)
    else:
        raw_error = call.job.get("error")
        err: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        call.error = f"{err.get('code') or state}: {str(err.get('reason') or '')[:160]}"


async def _await_first_hop(session: Any, call: _Call, scope: _JobScope) -> None:
    """대상 선정용 인스턴스 목록이 작업으로 승격됐다 — 마감까지 기다리고, 못 끝나면 취소한다."""
    handle = jobs.job_handle(call.envelope) or {}
    job_id = str(handle.get("job_id"))
    status = await jobs.poll_job(session, job_id, scope.owner, until=scope.poll_until)
    if status is not None and status.get("error"):
        call.error = _envelope_error(status)
        return
    state = jobs.job_state(status)
    if status is not None and state in jobs.READABLE_STATES:
        call.envelope = jobs.result_envelope(status)
        return
    if status is not None and state not in jobs.LIVE_STATES:
        err = (jobs.job_handle(status) or {}).get("error") or {}
        call.error = f"{err.get('code') or state}: {str(err.get('reason') or '')[:160]}"
        return
    try:
        await session.call_tool(jobs.CANCEL_TOOL, {"job_id": job_id, "owner": scope.owner})
    except SourceMcpError as e:
        logger.warning("인스턴스 목록 작업 취소 실패(job_id=%s): %s", job_id[:8], e)
    call.error = _FIRST_HOP_TIMEOUT_ERROR


def _in_file_tail(call: _Call) -> str:
    """배치 결과 파일 고지 꼬리 — 행이 결과 파일에만 있는 대상 수(R34-1 · 배치가 아니면 빈 값)."""
    k = sum(1 for m in call.members or [] if (m.envelope or {}).get(ROWS_IN_FILE_KEY))
    return f" 일부 대상의 행은 결과 파일에만 있습니다({k}개 대상)." if k else ""


def _gateway_disclosures(envelope: dict[str, Any], source: str) -> list[disc.Disclosure]:
    """게이트웨이 봉투 고지(예 `apm_masked_fields`) — 고지 표에 등록된 kind만 싣는다.

    W1 검증 L-5.
    """
    out: list[disc.Disclosure] = []
    for item in envelope.get("disclosures") or []:
        kind = item.get("kind") if isinstance(item, dict) else None
        text = item.get("text") if isinstance(item, dict) else None
        if kind in disc.KIND_TABLE and isinstance(text, str) and text.strip():
            out.append(disc.make(str(kind), text, source=source))
        else:
            logger.info("%s: 등록되지 않았거나 문구가 없는 게이트웨이 고지는 싣지 않음: %r",
                        APM_QUERY_AGENT, str(kind)[:40])
    return out
