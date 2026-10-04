"""APM 장기 작업 — 본체 쪽 수명 · 인가 · 전달 (plans/134 W0-B M-10·M-11 ·
SPEC-apm-question-coverage §3.1·§3.7·§3.8 · D-299 ④).

게이트웨이(`apm_gateway`)는 데이터 도구 호출을 작업으로 돌리고, `wait_seconds` 안에 끝나지 않으면
작업 핸들(`job`)을 돌려준 뒤 백그라운드로 계속한다. 이 모듈은 본체가 그 작업을 다루는 규칙이다.

- **호출 인자**: `owner = "user:<sub>"`(주체 없음·인증 꺼짐 = `user:anonymous`) ·
  `wait_seconds = max(1, min(호출 상한 − 2, 조회 마감까지 남은 시간))` — MCP 호출 상한보다 항상
  짧게 둬 호출이 상한에 끊기지 않게 한다(묶이지 않은 컨텍스트는 `호출 상한 − 2`).
- **처리 마감 안 재확인**: 작업 핸들이 오면 조회 마감까지 `apm_job_status`를 짧은 간격으로 다시
  본다(`poll_job`). 일반 요청 처리 상한(D-267 ⑦)은 올리지 않는다 — 끝나지 않으면 접수 답이다.
- **장부**: 작업을 맡긴 사용자를 `src/infrastructure/apm_job_store.py`에 남긴다.
- **작업 API**(`ApmJobService`): 장부 소유자 또는 관리자만(D-262 `_owned_result`와 같은 판정 —
  인증 꺼짐은 통과) · 장부 확인 → 게이트웨이 호출 순서 · 게이트웨이에도 장부의 `owner`를 다시
  싣는다 · 미리보기·다운로드는 화면과 같은 `DataMasker` · 다운로드는 청크를 차례로 받아 흘려보낸다.
- **경계**: 게이트웨이 패키지 import 0(D-274) — MCP 작업 도구 3종(JSON)만 쓴다. 본체는 스풀 경로를
  모른다. LLM은 작업 ID·경로·URL을 만들지 않는다.

계층: orchestration.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.clients.source_mcp_client import SourceMcpError, open_source_session
from src.domain.user import UserRole
from src.infrastructure.apm_job_store import ApmJobStore, get_apm_job_store, is_job_id
from src.security.data_masker import DataMasker
from src.utils.deadline import bound_deadline, retrieval_deadline

logger = logging.getLogger(__name__)

STATUS_TOOL = "apm_job_status"
CANCEL_TOOL = "apm_job_cancel"
READ_TOOL = "apm_job_read"
#: 게이트웨이 작업 상태(SPEC §3.3) — 진행 중 · 결과를 읽을 수 있음.
LIVE_STATES: frozenset[str] = frozenset({"queued", "running"})
READABLE_STATES: frozenset[str] = frozenset({"completed", "partial"})
#: 게이트웨이 오류 코드(SPEC §2.2) — 작업 없음(남의 것·보관 경과 포함) · 아직 안 끝남.
JOB_NOT_FOUND = "job_not_found"
JOB_NOT_READY = "job_not_ready"
#: `wait_seconds` = 호출 상한 − 이 값(초) · 하한.
WAIT_MARGIN_SEC = 2.0
MIN_WAIT_SEC = 1.0
#: 처리 마감 안에서 작업 상태를 다시 보는 간격(초).
POLL_INTERVAL_SEC = 1.0
#: 마감이 묶이지 않은 컨텍스트(CLI 등)의 재확인 예산 — 설정이 없을 때의 처리 상한·서술 예약(초).
DEFAULT_PROCESSING_SEC = 120.0
DEFAULT_RESERVE_SEC = 15.0
ANONYMOUS_SUB = "anonymous"
DOWNLOAD_FORMATS: tuple[str, ...] = ("csv", "jsonl", "txt")
_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "jsonl": "application/x-ndjson; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}


# ── 호출 인자 · 마감 ──────────────────────────────────────────────────────────

def owner_sub(sub: Any) -> str:
    """장부 소유자 — 요청 사용자 `sub`(없으면 `anonymous`)."""
    text = str(sub).strip() if sub else ""
    return text or ANONYMOUS_SUB


def gateway_owner(sub: Any) -> str:
    """게이트웨이 `owner` 인자 — `user:<sub>`(인증 꺼짐·주체 없음 = `user:anonymous`)."""
    return f"user:{owner_sub(sub)}"


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def wait_seconds(call_timeout: float, *, now: float | None = None) -> float:
    """`wait_seconds = max(1, min(호출 상한 − 2, 조회 마감까지 남은 시간))`(SPEC §3.8).

    마감이 묶이지 않은 컨텍스트는 `호출 상한 − 2`다. 하한은 1초이되 **호출 상한의 절반을 넘지
    않는다** — 호출 상한이 2초 이하인 설정에서도 `wait_seconds < 호출 상한`이 깨지지 않게(작은
    상한에서는 1초 하한이 상한 이상이 된다).
    """
    timeout = float(call_timeout)
    cap = timeout - WAIT_MARGIN_SEC
    bound = bound_deadline()
    if bound is not None:
        until = retrieval_deadline(*bound)
        if until is not None:
            cap = min(cap, until - (time.monotonic() if now is None else now))
    floor = min(MIN_WAIT_SEC, timeout / 2)
    wait = round(max(floor, cap), 1)
    return wait if wait < timeout else max(timeout / 2, 0.0)


def poll_deadline(app_config: Any, *, now: float | None = None) -> float:
    """작업 상태를 다시 볼 수 있는 마지막 시각(monotonic) — 조회 마감(처리 마감 − 서술 예약).

    마감이 묶이지 않은 컨텍스트(CLI·단위 테스트)는 설정의 처리 상한 − 서술 예약을 지금부터 센다
    (라우트가 묶었을 때와 같은 예산 — 종전 동작은 호출 상한에서 끊겨 결과가 없었다).
    """
    bound = bound_deadline()
    if bound is not None:
        until = retrieval_deadline(*bound)
        if until is not None:
            return until
    server = getattr(app_config, "server", None)
    limit = _number(getattr(server, "query_timeout", None), DEFAULT_PROCESSING_SEC)
    reserve = _number(getattr(server, "answer_reserve_sec", None), DEFAULT_RESERVE_SEC)
    return (time.monotonic() if now is None else now) + max(0.0, limit - reserve)


# ── 봉투 해석 ─────────────────────────────────────────────────────────────────

def job_handle(envelope: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """봉투의 작업 핸들(`job`) — 없거나 형식이 틀리면 None."""
    handle = (envelope or {}).get("job")
    if isinstance(handle, dict) and is_job_id(handle.get("job_id")):
        return handle
    return None


def job_state(envelope: Mapping[str, Any] | None) -> str | None:
    handle = job_handle(envelope)
    return str(handle.get("state")) if handle and handle.get("state") else None


def is_live(envelope: Mapping[str, Any] | None) -> bool:
    """접수 봉투인가(작업이 아직 진행 중 — 데이터가 아니다)."""
    return job_state(envelope) in LIVE_STATES


def result_envelope(status: Mapping[str, Any]) -> dict[str, Any]:
    """끝난 작업의 상태 봉투 → 동기 결과와 같은 모양(`result_meta` + 미리보기 행)."""
    meta = status.get("result_meta")
    out: dict[str, Any] = dict(meta) if isinstance(meta, dict) else {}
    rows = [r for r in status.get("rows") or [] if isinstance(r, dict)]
    out["rows"] = rows
    out["row_count"] = len(rows)
    for key in ("total_row_count", "artifact", "job"):
        if status.get(key) is not None:
            out[key] = status[key]
    if status.get("partial"):
        out["partial"] = True
    if status.get("limits") and not out.get("limits"):
        out["limits"] = list(status["limits"])
    return out


def ledger_fields(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """장부에 남길 게이트웨이 상태 캐시(상태·진행·예측·시각·오류·전체 행 수)."""
    handle = job_handle(envelope) or {}
    fields: dict[str, Any] = {
        key: handle.get(key)
        for key in ("state", "progress", "estimate", "created_at", "updated_at", "expires_at")
        if key in handle
    }
    fields["error"] = handle.get("error")
    total = envelope.get("total_row_count")
    if total is not None:
        fields["total_row_count"] = total
    if handle.get("state") in READABLE_STATES:
        fields["partial"] = handle.get("state") == "partial"
    return fields


async def poll_job(
    session: Any,
    job_id: str,
    owner: str,
    *,
    until: float,
    interval: float | None = None,
) -> dict[str, Any] | None:
    """조회 마감(`until`, monotonic)까지 `apm_job_status`를 다시 본다.

    Returns:
        끝난 작업의 상태 봉투 · 게이트웨이 오류 봉투 · 마감까지 진행 중이면 마지막 상태 봉투 ·
        한 번도 보지 못했으면(마감 지남 · 상태 호출 실패) None.
    """
    step = POLL_INTERVAL_SEC if interval is None else interval
    last: dict[str, Any] | None = None
    while True:
        left = until - time.monotonic()
        if left <= 0:
            return last
        await asyncio.sleep(max(0.0, min(step, left)))
        # 상태 호출도 조회 마감 안에서만 기다린다 — 게이트웨이가 늦게 답해도 서술 예약을 깎지 않게
        # (데이터 호출은 `wait_seconds`로, 상태 호출은 남은 마감으로 묶는다).
        remaining = max(0.05, until - time.monotonic())
        try:
            status: dict[str, Any] = await asyncio.wait_for(
                session.call_tool(STATUS_TOOL, {"job_id": job_id, "owner": owner}),
                timeout=remaining,
            )
        except (SourceMcpError, TimeoutError) as e:
            # 상태를 못 봤을 뿐 작업은 이어질 수 있다 — 접수로 끝내고 카드가 다시 본다
            logger.warning("APM 작업 상태 확인 실패(job_id=%s): %s", job_id[:8], e)
            return last
        if status.get("error"):
            return status
        last = status
        if job_state(status) not in LIVE_STATES:
            return status


# ── 장부 등록 ─────────────────────────────────────────────────────────────────

def store_for(app_config: Any) -> ApmJobStore:
    """작업 장부(서버 Redis 캐시 · 없으면 메모리)."""
    return get_apm_job_store(app_config)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


async def register(
    store: ApmJobStore,
    envelope: Mapping[str, Any],
    *,
    sub: Any,
    thread_id: Any,
    tool: str,
    view: str,
    view_label: str,
    hostname: str | None,
    scope: str,
) -> str:
    """작업 핸들이 있는 봉투를 장부에 올린다 — 저장소 종류(`redis`·`memory`)를 돌려준다."""
    handle = job_handle(envelope)
    if handle is None:
        raise ValueError("작업 핸들이 없는 봉투다")
    record: dict[str, Any] = {
        "job_id": handle["job_id"],
        "owner_sub": owner_sub(sub),
        "thread_id": str(thread_id) if thread_id else None,
        "tool": tool,
        "view": view,
        "view_label": view_label,
        "hostname": hostname,
        "scope": scope,
        "registered_at": _now_iso(),
        **ledger_fields(envelope),
    }
    return await store.register(record)


# ── 작업 API 서비스 ───────────────────────────────────────────────────────────

class JobNotFoundError(LookupError):
    """장부에 없거나 게이트웨이가 모르는 작업(보관 기간 경과 포함)."""


class JobForbiddenError(PermissionError):
    """요청자가 장부 소유자도 관리자도 아니다."""


class JobConflictError(RuntimeError):
    """요청한 동작을 지금 상태에서 할 수 없다(예: 끝나지 않은 작업의 다운로드)."""


class JobUnavailableError(RuntimeError):
    """게이트웨이 미연결·호출 실패·게이트웨이 오류."""


@dataclass
class ApmDownload:
    """다운로드 응답 재료 — 본문은 청크를 차례로 받아 흘려보내는 비동기 반복자다."""

    file_name: str
    file_type: str
    media_type: str
    state: str
    body: AsyncIterator[bytes]


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return "" if value is None else value


def mask_deep(masker: DataMasker, value: Any) -> Any:
    """중첩 값까지 화면과 같은 규칙(`DataMasker`)으로 가린다(심층 방어).

    `DataMasker`는 행의 1단 키·문자열 값만 본다 — 중첩 dict 의 민감 키·값과 목록 안의 문자열도
    같은 규칙으로 걷는다. 1차 방어는 게이트웨이의 자격증명 제거다.
    """
    if isinstance(value, dict):
        masked = masker.mask_rows([value])[0]
        return {k: mask_deep(masker, v) if isinstance(v, (dict, list)) else v
                for k, v in masked.items()}
    if isinstance(value, list):
        out: list[Any] = []
        for item in value:
            if isinstance(item, (dict, list)):
                out.append(mask_deep(masker, item))
            elif isinstance(item, str):
                out.append(masker.mask_rows([{"value": item}])[0]["value"])
            else:
                out.append(item)
        return out
    return value


class ApmJobService:
    """작업 API(`/api/v1/apm/jobs`)의 판단 — 인가 · 게이트웨이 작업 도구 · 마스킹 · 전달."""

    def __init__(self, app_config: Any, store: ApmJobStore | None = None) -> None:
        self._config = app_config
        self._store = store or store_for(app_config)
        self._masker = DataMasker(app_config.security)

    # ── 인가 ──
    def _auth_enabled(self) -> bool:
        return bool(getattr(getattr(self._config, "auth", None), "enabled", False))

    async def _owned(self, job_id: str, user: Mapping[str, Any]) -> dict[str, Any]:
        """장부를 먼저 본다 — 없으면 404, 소유자·관리자가 아니면 403(게이트웨이 호출 전)."""
        record = await self._store.get(job_id) if is_job_id(job_id) else None
        if record is None:
            raise JobNotFoundError("작업을 찾을 수 없습니다(없거나 보관 기간이 지났습니다).")
        if not self._auth_enabled():
            return record  # 인증 꺼짐 — D-262 `_owned_result`와 같다(전원이 anonymous 한 명)
        if user.get("role") == UserRole.ADMIN.value:
            return record
        owner = record.get("owner_sub")
        if not owner or owner != user.get("sub"):
            logger.warning(
                "APM 작업 접근 거부: job_id=%s 요청자=%s (소유자 아님)", job_id[:8], user.get("sub")
            )
            raise JobForbiddenError("이 작업은 조회를 요청한 사용자만 볼 수 있습니다.")
        return record

    # ── 게이트웨이 ──
    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """게이트웨이 작업 도구 1회 — 세션을 열고 부르고 닫는다.

        다운로드 응답 본문을 흘려보내는 동안 세션을 잡고 있지 않는다(청크마다 새로 연다).
        """
        from src.orchestration import apm_query as aq  # 지연 — apm_query가 이 모듈을 쓴다
        from src.routing.registry import get_registry

        endpoint = aq.apm_endpoint(self._config)
        if endpoint is None:
            raise JobUnavailableError("APM 게이트웨이가 연결되어 있지 않습니다.")
        url, token = endpoint
        timeout = getattr(getattr(self._config, "dbhub", None), "source_call_timeout", 10.0)
        label = get_registry().system_label(aq.APM_SYSTEM)
        try:
            async with open_source_session(
                url, token,
                call_timeout=float(timeout) if isinstance(timeout, (int, float)) else 10.0,
                label=label, session_factory=aq._SESSION_FACTORY,
            ) as session:
                envelope = await session.call_tool(tool, args)
        except SourceMcpError as e:
            raise JobUnavailableError(str(e)) from e
        code = envelope.get("error")
        if code == JOB_NOT_FOUND:
            raise JobNotFoundError(
                "작업을 찾을 수 없습니다(보관 기간이 지났거나 게이트웨이가 다시 시작됐습니다)."
            )
        if code == JOB_NOT_READY:
            raise JobConflictError("작업이 아직 끝나지 않았습니다.")
        if code:
            raise JobUnavailableError(f"{code}: {str(envelope.get('reason') or '')[:200]}")
        return envelope

    async def _refresh(self, record: dict[str, Any], envelope: Mapping[str, Any]) -> None:
        try:
            await self._store.update(record["job_id"], ledger_fields(envelope))
        except Exception as e:  # noqa: BLE001 — 상태 캐시 갱신 실패가 응답을 막지 않는다
            logger.warning("APM 작업 장부 갱신 실패(job_id=%s): %s", record["job_id"][:8], e)

    # ── 표현 ──
    def _view(
        self, record: Mapping[str, Any], envelope: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        handle = job_handle(envelope) or {}
        state = handle.get("state") or record.get("state")
        artifact = (envelope or {}).get("artifact") or {}
        rows = [mask_deep(self._masker, r) for r in (envelope or {}).get("rows") or []
                if isinstance(r, dict)]
        total = (envelope or {}).get("total_row_count")
        return {
            "job_id": record.get("job_id"),
            "state": state,
            "progress": handle.get("progress", record.get("progress")),
            "estimate": handle.get("estimate", record.get("estimate")),
            "created_at": handle.get("created_at", record.get("created_at")),
            "updated_at": handle.get("updated_at", record.get("updated_at")),
            "expires_at": handle.get("expires_at", record.get("expires_at")),
            "error": handle.get("error", record.get("error")),
            "view": record.get("view"),
            "view_label": record.get("view_label"),
            "hostname": record.get("hostname"),
            "scope": record.get("scope"),
            "thread_id": record.get("thread_id"),
            "registered_at": record.get("registered_at"),
            "total_row_count": total if total is not None else record.get("total_row_count"),
            "preview_rows": rows,
            "preview_row_count": len(rows),
            "columns": list(artifact.get("columns") or []),
            "text_parts": [
                p.get("name") for p in artifact.get("text_parts") or [] if isinstance(p, dict)
            ],
            "limits": list((envelope or {}).get("limits") or []),
            "partial": state == "partial",
            "downloadable": state in READABLE_STATES,
            "ledger": record.get("ledger"),
        }

    # ── API 동작 ──
    async def list_jobs(self, user: Mapping[str, Any]) -> list[dict[str, Any]]:
        """내 작업(장부 캐시 — 게이트웨이를 부르지 않는다)."""
        records = await self._store.list_for(owner_sub(user.get("sub")))
        return [self._view(r, None) for r in records]

    async def status(self, job_id: str, user: Mapping[str, Any]) -> dict[str, Any]:
        record = await self._owned(job_id, user)
        envelope = await self._call(
            STATUS_TOOL, {"job_id": job_id, "owner": gateway_owner(record.get("owner_sub"))}
        )
        await self._refresh(record, envelope)
        return self._view(record, envelope)

    async def cancel(self, job_id: str, user: Mapping[str, Any]) -> dict[str, Any]:
        record = await self._owned(job_id, user)
        envelope = await self._call(
            CANCEL_TOOL, {"job_id": job_id, "owner": gateway_owner(record.get("owner_sub"))}
        )
        await self._refresh(record, envelope)
        logger.info("APM 작업 취소 요청: job_id=%s 요청자=%s", job_id[:8], user.get("sub"))
        return self._view(record, envelope)

    async def open_download(
        self, job_id: str, user: Mapping[str, Any], fmt: str
    ) -> ApmDownload:
        """다운로드 준비 — 인가 · 상태 확인(완료·부분만) · 파일 이름 · 본문 반복자."""
        if fmt not in DOWNLOAD_FORMATS:
            raise JobConflictError(f"지원하지 않는 형식입니다: {fmt}")
        record = await self._owned(job_id, user)
        owner = gateway_owner(record.get("owner_sub"))
        envelope = await self._call(STATUS_TOOL, {"job_id": job_id, "owner": owner})
        await self._refresh(record, envelope)
        handle = job_handle(envelope) or {}
        state = str(handle.get("state") or "")
        if state not in READABLE_STATES:
            reason = (handle.get("error") or {}).get("reason") if handle.get("error") else ""
            raise JobConflictError(
                f"작업이 {state or '알 수 없는'} 상태라 결과를 받을 수 없습니다"
                + (f" — {reason}" if reason else "")
                + "(완료·부분 완료만 받을 수 있습니다)."
            )
        artifact = envelope.get("artifact") or {}
        if fmt == "txt":
            parts = [
                str(p.get("name")) for p in artifact.get("text_parts") or []
                if isinstance(p, dict) and p.get("name")
            ]
            if not parts:
                raise JobNotFoundError("이 작업에는 텍스트 결과가 없습니다(CSV·JSONL로 받으세요).")
            body = self._text_body(job_id, owner, parts)
        else:
            chunks = len(artifact.get("chunks") or [])
            columns = [str(c) for c in artifact.get("columns") or []]
            body = self._rows_body(job_id, owner, fmt, chunks, columns)
        view = str(record.get("view") or "apm").replace(".", "_")
        suffix = "_partial" if state == "partial" else ""
        return ApmDownload(
            file_name=f"{view}_{job_id[:8]}{suffix}.{fmt}",
            file_type=fmt,
            media_type=_MEDIA_TYPES[fmt],
            state=state,
            body=body,
        )

    async def _rows_body(
        self, job_id: str, owner: str, fmt: str, chunks: int, columns: list[str]
    ) -> AsyncIterator[bytes]:
        """청크를 하나씩 받아 가린 뒤 흘려보낸다 — 한 번에 한 청크만 메모리에 둔다."""
        header_done = False
        if fmt == "csv":
            yield "﻿".encode()  # UTF-8 BOM(Excel 한글)
        for index in range(chunks):
            envelope = await self._call(
                READ_TOOL, {"job_id": job_id, "owner": owner, "chunk": index}
            )
            rows = [mask_deep(self._masker, r) for r in envelope.get("rows") or []
                    if isinstance(r, dict)]
            if fmt == "jsonl":
                yield "".join(
                    json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows
                ).encode("utf-8")
                continue
            if not columns:
                columns = list(dict.fromkeys(k for r in rows for k in r))
            out = io.StringIO()
            writer = csv.writer(out)
            if not header_done:
                writer.writerow(columns)
                header_done = True
            for row in rows:
                writer.writerow([_cell(row.get(c)) for c in columns])
            yield out.getvalue().encode("utf-8")
        if fmt == "csv" and not header_done:  # 0행 — 머리글만
            out = io.StringIO()
            csv.writer(out).writerow(columns)
            yield out.getvalue().encode("utf-8")

    async def _text_body(self, job_id: str, owner: str, parts: list[str]) -> AsyncIterator[bytes]:
        """텍스트 부분(마스킹본)을 차례로 — 줄마다 화면과 같은 값 규칙(`DataMasker`)을 지난다."""
        for index, name in enumerate(parts):
            envelope = await self._call(READ_TOOL, {"job_id": job_id, "owner": owner, "part": name})
            text = str(envelope.get("text") or "")
            lines = [
                str(self._masker.mask_rows([{"line": line}])[0]["line"])
                for line in text.splitlines()
            ]
            head = f"===== {name} =====\n" if len(parts) > 1 else ""
            tail = "\n" if index < len(parts) - 1 else ""
            yield (head + "\n".join(lines) + "\n" + tail).encode("utf-8")


__all__ = [
    "DOWNLOAD_FORMATS",
    "LIVE_STATES",
    "READABLE_STATES",
    "ApmDownload",
    "ApmJobService",
    "JobConflictError",
    "JobForbiddenError",
    "JobNotFoundError",
    "JobUnavailableError",
    "gateway_owner",
    "is_live",
    "job_handle",
    "ledger_fields",
    "owner_sub",
    "poll_deadline",
    "poll_job",
    "register",
    "result_envelope",
    "store_for",
    "wait_seconds",
]
