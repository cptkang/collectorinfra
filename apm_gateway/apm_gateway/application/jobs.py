"""장기 작업 — 모든 데이터 도구 호출을 작업으로 실행 (plans/134 W0-B N-14·N-18 · M-10 기반 ·
SPEC-apm-question-coverage §3.2~§3.5·§3.7 · D-299 ④).

- `JobManager.execute`: 도구 호출 1회 = 작업 1개. `wait_seconds` 안에 끝나면 결과 봉투를 바로
  돌려주고(인라인 초과 행·텍스트 부분은 스풀 → `artifact` + `job` + `total_row_count`), 못 끝나면
  작업 핸들을 돌려주고 같은 코루틴을 백그라운드로 계속한다(**승격**). `wait_seconds`가 없으면 끝날
  때까지 기다린다(종전 소비자 의미). 짧은 동기 작업은 스풀할 것이 없으면 기록을 남기지 않는다
  (디스크 0).
- 승격 전에 호출자가 끊기면(요청 취소) 작업도 취소한다. 승격 뒤에는 호출자 세션과 무관하게
  계속한다 — 작업 태스크는 요청 태스크 그룹 밖(`asyncio.create_task`)에서 만들고 강참조로 잡는다.
- 상태 머신(`domain.jobs`): queued → running → completed | partial | failed | cancelled ·
  queued|running → interrupted(재기동·종료) · running → failed(정체 `stalled`). 기록은 원자적 쓰기.
- 승격된 작업은 동시 실행 슬롯(`APM_JOB_MAX_CONCURRENT` · FIFO)을 **다음 API 호출 앞에서** 기다린다
  (그동안 `queued`). 슬롯 수는 메모리·공정성 수단이고 총 조회량을 자르지 않는다.
- 호출 맥락(`domain.call_context`): 작업이 맥락이다 — 어댑터가 호출마다 `before_call`로 슬롯 대기 ·
  `api_calls`·`updated_at`(임대)을 올리고, 승격되면 이후 호출 우선순위가 `background`가 된다.
- 비용 예측: 도구가 신고한 호출 계획(`expect_calls`)으로 `progress.total`·`estimate.api_calls`를
  채우고 `estimate.seconds = api_calls / 속도`. 신고가 없으면 `total = null`(호출 수만 센다).
- 취소 시 스풀 조각을 지운다(취소한 결과를 전체로 오인하지 않게). 정체 감시 · 재기동 스캔 · 보관
  만료 정리를 한다. 작업 도구는 같은 주체 + 같은 `owner`일 때만 응답한다(아니면 `job_not_found`).
- 기록에는 인자 원문·토큰·자격증명을 싣지 않는다(대상은 마스킹 요약 · `owner`는 불투명 문자열).
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime
from typing import Any

from apm_gateway.application.masking import mask_text
from apm_gateway.application.spool import Spool, SpoolCorruptedError
from apm_gateway.config import GatewayConfig, JobConfig
from apm_gateway.domain import jobs as js
from apm_gateway.domain.call_context import (
    PRIORITY_BACKGROUND,
    PRIORITY_INTERACTIVE,
    CallScope,
    set_scope,
)
from apm_gateway.domain.errors import (
    API_ERROR,
    INVALID_ARGUMENT,
    JOB_NOT_FOUND,
    JOB_NOT_READY,
    ApmError,
)

logger = logging.getLogger(__name__)

# 도구가 결과 봉투에 원문 텍스트 부분을 실을 때 쓰는 예약 키(반환 전에 떼어 스풀한다 — W1
# 프로파일 전문).
TEXT_PARTS_KEY = "_text_parts"
# 「전체 파일 전용」 칸 목록 예약 키(COV 표 C — 결과 파일에는 남기고 화면용 `rows`에서는 뺀다).
# 칸 이름에 `.`이 있으면 중첩 칸이다(`transaction.instance_oid` — W1 검증 L-3).
FILE_ONLY_KEY = "_file_only"
# 도구가 식별자를 실제로 가린 칸 이름 예약 키 — 자격증명 검사가 가린 칸과 합쳐 봉투 고지
# `disclosures`(`apm_masked_fields` · SPEC-coverage §2.2·§7.5)로 바꾼다(W1 검증 L-5 · 값 없음).
MASKED_KEY = "_masked_fields"
MASKED_KIND = "apm_masked_fields"
# 도구가 선택 조건을 빼거나 바꿔 조회했을 때의 사용자용 한 줄 예약 키 — 봉투 고지
# `apm_unresolved_condition`(의무 고지 · SPEC-coverage §7.5)으로 바꾼다(W2V 후속 — `[한계]`와 별도).
UNRESOLVED_KEY = "_unresolved"
UNRESOLVED_KIND = "apm_unresolved_condition"
# 고지 문구에 싣는 칸 이름 수(나머지는 「외 n개」 — 데이터가 아니라 고지 문구다)
_MASKED_NAMES_SHOWN = 20
PROGRESS_UNIT = "api_calls"
PROGRESS_LABEL = "API 호출"
INTERRUPTED_REASON = "게이트웨이 재기동으로 중단 — 다시 요청해야 합니다"
_PERSIST_EVERY = 1.0  # 진행 기록 쓰기 최소 간격(초) — 상태 전이는 즉시 쓴다
_CANCEL_WAIT = 5.0

Envelope = Callable[..., dict[str, Any]]
ErrorEnvelope = Callable[[str, str, str], dict[str, Any]]
ToolCall = Callable[[], Awaitable[dict[str, Any]]]


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _drop(row: dict[str, Any], column: str) -> dict[str, Any]:
    """칸 하나를 뺀 새 행(`a.b`는 중첩 dict 칸 — 입력은 바꾸지 않는다)."""
    head, _, rest = column.partition(".")
    if head not in row:
        return row
    if not rest:
        return {k: v for k, v in row.items() if k != head}
    child = row[head]
    return {**row, head: _drop(child, rest)} if isinstance(child, dict) else row


def _strip(rows: list[Any], columns: frozenset[str]) -> list[Any]:
    """화면용 행 — 「전체 파일 전용」 칸을 뺀다(결과 파일에는 그대로 남는다)."""
    if not columns:
        return rows
    out = []
    for row in rows:
        if isinstance(row, dict):
            for column in sorted(columns):
                row = _drop(row, column)
        out.append(row)
    return out


def masked_disclosure(fields: set[str]) -> dict[str, str]:
    """가린 칸 고지(값 없음 — 칸 이름만)."""
    names = sorted(fields)
    shown = ", ".join(names[:_MASKED_NAMES_SHOWN])
    more = f" 외 {len(names) - _MASKED_NAMES_SHOWN}개" if len(names) > _MASKED_NAMES_SHOWN else ""
    return {
        "kind": MASKED_KIND,
        "text": f"개인정보·자격증명 보호를 위해 값을 가린 칸: {shown}{more}",
    }


def settle_notices(
    result: dict[str, Any], notes: Iterable[str], masked_fields: Iterable[str]
) -> None:
    """정상 봉투의 메모·고지 정리(작업 마감 · 다건 대상 하위 봉투 공용) — 호출 맥락 메모를
    `limits`에 잇고, 예약 키(미해결 조건·가린 칸)를 고지 `disclosures`로 바꾼다(제자리 수정)."""
    result["limits"] = list(dict.fromkeys(list(result.get("limits") or []) + list(notes)))
    unresolved = list(dict.fromkeys(result.pop(UNRESOLVED_KEY, None) or ()))
    masked = set(result.pop(MASKED_KEY, None) or ()) | set(masked_fields)
    added = [{"kind": UNRESOLVED_KIND, "text": str(text)} for text in unresolved]
    if masked:
        added.append(masked_disclosure(masked))
    if added:
        result["disclosures"] = [
            *(
                d
                for d in result.get("disclosures") or []
                if d.get("kind") not in (MASKED_KIND, UNRESOLVED_KIND)
            ),
            *added,
        ]


def _epoch(value: Any) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


class _Slots:
    """승격된 작업의 동시 실행 슬롯 — 먼저 기다린 작업이 먼저 받는다(FIFO)."""

    def __init__(self, size: int) -> None:
        self.size = max(1, int(size))
        self._free = self.size
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def in_use(self) -> int:
        return self.size - self._free

    def try_acquire(self) -> bool:
        if self._free > 0:
            self._free -= 1
            return True
        return False

    async def acquire(self) -> None:
        if self.try_acquire():
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(future)
        try:
            await future
        except asyncio.CancelledError:
            if future.done() and not future.cancelled():
                self.release()  # 넘겨받은 슬롯을 돌려준다
            elif future in self._waiters:
                self._waiters.remove(future)
            raise

    def release(self) -> None:
        while self._waiters:
            future = self._waiters.popleft()
            if not future.done():
                future.set_result(None)
                return
        self._free = min(self.size, self._free + 1)


class Job(CallScope):
    """작업 1개 — 실행 중에는 호출 맥락이기도 하다(어댑터가 훅을 부른다)."""

    def __init__(
        self,
        manager: JobManager,
        *,
        tool: str,
        principal: str,
        owner: str | None,
        target: str,
        now: float,
        investigation_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        super().__init__(PRIORITY_INTERACTIVE)
        self._manager = manager
        self.job_id = uuid.uuid4().hex
        self.tool = tool
        self.principal = principal
        self.owner = owner
        self.target = target
        self.investigation_id = investigation_id
        self.thread_id = thread_id
        self.state = js.RUNNING
        self.created_at = self.started_at = self.updated_at = now
        self.finished_at: float | None = None
        self.expires_at: float | None = None
        self.api_calls = 0  # 이 작업 코루틴이 직접 부른 호출(진행 `done`)
        self.shared_calls = 0  # 이 작업이 시작한 공유 적재(인스턴스 명단)의 호출
        self.calls_by_source: dict[str, int] = {}  # 둘 다 — 감사 `api_calls`·`sources`
        self.expected_calls = 0
        self.promoted = False
        self.holds_slot = False
        self.finalizing = False
        self.cancel_reason: str | None = None
        self.task: asyncio.Task[dict[str, Any]] | None = None
        self.notes: list[str] = []
        self.masked_fields: set[str] = set()
        self.result_meta: dict[str, Any] | None = None
        self.artifact: dict[str, Any] | None = None
        self.limits: list[str] = []
        self.error: dict[str, str] | None = None
        self.persisted_at = 0.0

    # ── 호출 맥락 훅(어댑터가 부른다) ─────────────────────
    async def before_call(self, source_id: str = "") -> None:
        await self._manager.wait_slot(self)
        self.count_call(source_id)

    def count_call(self, source_id: str = "", *, shared: bool = False) -> None:
        """호출 1회 — 소스별 호출 수 · 임대 시각(`updated_at`)을 올린다(공유 적재 호출은 진행에
        넣지 않는다 — 호출 계획 밖이다)."""
        if shared:
            self.shared_calls += 1
        else:
            self.api_calls += 1
        if source_id:
            self.calls_by_source[source_id] = self.calls_by_source.get(source_id, 0) + 1
        if self.finished_at is None:  # 끝난 뒤 공유 적재가 센 호출은 기록을 다시 쓰지 않는다
            self.updated_at = self._manager.now()
            self._manager.persist(self)

    def expect_calls(self, count: int) -> None:
        self.expected_calls += int(count)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def masked(self, fields: Iterable[str]) -> None:
        self.masked_fields.update(fields)

    @property
    def total_calls(self) -> int:
        return self.api_calls + self.shared_calls

    # ── 표현 ──────────────────────────────────────────────
    def progress(self) -> dict[str, Any]:
        total = max(self.expected_calls, self.api_calls) if self.expected_calls else None
        return {
            "done": self.api_calls,
            "total": total,
            "unit": PROGRESS_UNIT,
            "label": PROGRESS_LABEL,
        }

    def estimate(self) -> dict[str, Any]:
        calls = self.expected_calls or None
        rate = self._manager.rate_per_sec
        seconds = round(calls / rate, 1) if calls and rate > 0 else None
        return {"api_calls": calls, "seconds": seconds}

    def record(self) -> dict[str, Any]:
        """작업 기록(SPEC §3.3) — 인자 원문·토큰·자격증명 없음."""
        return {
            "job_id": self.job_id,
            "tool": self.tool,
            "principal": self.principal,
            "owner": self.owner,
            "target": self.target,
            "state": self.state,
            "created_at": _iso(self.created_at),
            "started_at": _iso(self.started_at),
            "updated_at": _iso(self.updated_at),
            "finished_at": _iso(self.finished_at),
            "expires_at": _iso(self.expires_at),
            "progress": self.progress(),
            "estimate": self.estimate(),
            "api_calls": self.total_calls,
            "result_meta": self.result_meta,
            "artifact": self.artifact,
            "limits": self.limits,
            "error": self.error,
        }


def handle_of(record: dict[str, Any]) -> dict[str, Any]:
    """작업 핸들(봉투 `job`) — SPEC §2.2. 실패·취소·중단이면 `error{code, reason}`를 더한다."""
    handle = {
        key: record.get(key)
        for key in (
            "job_id",
            "state",
            "progress",
            "estimate",
            "created_at",
            "updated_at",
            "expires_at",
        )
    }
    if record.get("error"):
        handle["error"] = record["error"]
    return handle


def artifact_ref(job_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """봉투 `artifact` — `{job_id, total_rows, chunks[{index,rows,bytes,sha256}], chunk_rows,
    columns, text_parts[{name,bytes}]}`."""
    return {"job_id": job_id, **manifest}


class JobManager:
    def __init__(
        self,
        spool: Spool,
        cfg: JobConfig,
        *,
        envelope: Envelope,
        error_envelope: ErrorEnvelope,
        rate_per_sec: float = 0.0,
        clock: Callable[[], float] = time.time,
        on_finish: Callable[[Job], None] | None = None,
    ) -> None:
        self.spool = spool
        self.cfg = cfg
        self.rate_per_sec = float(rate_per_sec)
        self._envelope = envelope
        self._error_envelope = error_envelope
        self._clock = clock
        self.on_finish = on_finish
        self._slots = _Slots(cfg.max_concurrent)
        self._live: dict[str, Job] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._maintainer: asyncio.Task[None] | None = None

    @classmethod
    def from_config(
        cls,
        cfg: GatewayConfig,
        *,
        envelope: Envelope,
        error_envelope: ErrorEnvelope,
        on_finish: Callable[[Job], None] | None = None,
    ) -> JobManager:
        """비용 예측 속도 = 설정 소스 중 가장 느린 속도(보수적)."""
        rates = [s.rate_limit_per_sec for s in cfg.sources if s.rate_limit_per_sec > 0]
        rate = min(rates) if rates else cfg.jennifer.rate_limit_per_sec
        return cls(
            Spool(cfg.jobs.spool_dir),
            cfg.jobs,
            envelope=envelope,
            error_envelope=error_envelope,
            rate_per_sec=rate,
            on_finish=on_finish,
        )

    # ── 실행 ──────────────────────────────────────────────

    async def execute(
        self,
        tool: str,
        call: ToolCall,
        *,
        principal: str,
        owner: str | None = None,
        target: str = "",
        wait_seconds: float | None = None,
        investigation_id: str | None = None,
        thread_id: str | None = None,
        on_job: Callable[[Job], None] | None = None,
    ) -> dict[str, Any]:
        """도구 호출 1회를 작업으로 실행한다. `call`은 예외 대신 오류 봉투를 돌려주는 코루틴이다.

        `on_job`은 작업을 만든 직후 그 작업을 받는다(감사가 작업별 호출 수를 쓴다).
        """
        if wait_seconds is not None and not (
            math.isfinite(float(wait_seconds)) and float(wait_seconds) >= 0
        ):
            raise ApmError(
                INVALID_ARGUMENT, f"wait_seconds는 0 이상의 유한한 수여야 한다: {wait_seconds}"
            )
        if owner is not None and len(str(owner)) > js.OWNER_MAX:
            raise ApmError(INVALID_ARGUMENT, f"owner는 {js.OWNER_MAX}자 이하여야 한다")
        job = Job(
            self,
            tool=tool,
            principal=principal,
            owner=owner,
            target=mask_text(target, limit=120),
            now=self._clock(),
            investigation_id=investigation_id,
            thread_id=thread_id,
        )
        if on_job is not None:
            on_job(job)
        task = asyncio.create_task(self._run(job, call), name=f"apm-job-{job.job_id}")
        job.task = task
        self._live[job.job_id] = job
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            timeout = None if wait_seconds is None else float(wait_seconds)
            await asyncio.wait({task}, timeout=timeout)
        except asyncio.CancelledError:
            self._abandon(job)
            raise
        if task.done():
            return task.result()
        self._promote(job)
        return self._envelope(tool, [], job=handle_of(job.record()))

    async def _run(self, job: Job, call: ToolCall) -> dict[str, Any]:
        set_scope(job)  # 이 태스크(와 자식 태스크)의 호출 맥락 = 이 작업
        try:
            result = await call()
            job.finalizing = True
            try:
                return await self._finalize(job, result)
            except Exception as e:  # 결과 저장 실패 — 사유를 드러낸다(침묵 금지)
                logger.exception("작업 결과 저장 실패: job_id=%s tool=%s", job.job_id, job.tool)
                job.error = {"code": API_ERROR, "reason": f"결과 저장 실패: {type(e).__name__}"}
                job.artifact = None
                if job.promoted:
                    self.spool.delete_results(job.job_id)
                else:
                    self.spool.delete(job.job_id)
                self._finish(job, js.FAILED)
                return self._error_envelope(job.tool, API_ERROR, job.error["reason"])
        except asyncio.CancelledError:
            self._cancelled(job)
            raise
        finally:
            self._release(job)

    async def _finalize(self, job: Job, result: dict[str, Any]) -> dict[str, Any]:
        if "error" in result:
            job.error = {"code": str(result["error"]), "reason": str(result.get("reason") or "")}
            self._finish(job, js.FAILED)
            return result
        settle_notices(result, job.notes, job.masked_fields)
        text_parts: dict[str, str] = result.pop(TEXT_PARTS_KEY, None) or {}
        file_only = frozenset(result.pop(FILE_ONLY_KEY, None) or ())
        rows = list(result.get("rows") or [])
        total = len(rows)
        state = js.PARTIAL if result.get("partial") else js.COMPLETED
        if not (job.promoted or text_parts or total > self.cfg.inline_rows):
            result["rows"] = _strip(rows, file_only)
            result["total_row_count"] = total
            self._finish(job, state)
            return result
        manifest = await asyncio.to_thread(
            self.spool.write_result, job.job_id, rows, text_parts, self.cfg.chunk_rows
        )
        if file_only:  # 결과 파일(청크)에는 있고 화면용 행·미리보기에는 없는 칸
            manifest["file_only_columns"] = sorted(file_only)
        inline = _strip(rows[: self.cfg.inline_rows], file_only)
        job.artifact = manifest
        job.limits = list(result["limits"])
        job.result_meta = {
            **{k: v for k, v in result.items() if k not in ("rows", "row_count")},
            "total_row_count": total,
        }
        self._finish(job, state)
        return {
            **result,
            "rows": inline,
            "row_count": len(inline),
            "total_row_count": total,
            "artifact": artifact_ref(job.job_id, manifest),
            "job": handle_of(job.record()),
        }

    def _promote(self, job: Job) -> None:
        job.promoted = True
        job.priority = PRIORITY_BACKGROUND  # 이후 호출부터 background
        if job.state in js.LIVE_STATES:
            if self._slots.try_acquire():
                job.holds_slot = True
                job.state = js.RUNNING
            else:
                job.state = js.QUEUED
        self._persist(job, force=True)
        logger.info(
            "작업 승격(백그라운드): job_id=%s tool=%s state=%s", job.job_id, job.tool, job.state
        )

    def now(self) -> float:
        return self._clock()

    def persist(self, job: Job) -> None:
        """진행 기록(최소 간격 `_PERSIST_EVERY`)."""
        self._persist(job)

    async def wait_slot(self, job: Job) -> None:
        """어댑터 훅 — 승격 뒤 슬롯이 없으면 다음 API 호출 앞에서 기다린다(queued)."""
        if job.promoted and not job.holds_slot and job.state in js.LIVE_STATES:
            if job.state != js.QUEUED:
                job.state = js.QUEUED
                self._persist(job, force=True)
            await self._slots.acquire()
            job.holds_slot = True
            job.state = js.RUNNING
            job.updated_at = self._clock()
            self._persist(job, force=True)

    def _release(self, job: Job) -> None:
        if job.holds_slot:
            job.holds_slot = False
            self._slots.release()

    def _finish(self, job: Job, state: str) -> None:
        now = self._clock()
        job.state = state
        job.finished_at = job.updated_at = now
        if job.promoted or job.artifact is not None:
            job.expires_at = now + self.cfg.retention_seconds
            self._persist(job, force=True)
        self._live.pop(job.job_id, None)
        if job.promoted and self.on_finish is not None:
            self.on_finish(job)

    def _cancelled(self, job: Job) -> None:
        reason = job.cancel_reason or js.ERROR_CANCELLED
        if reason == js.ERROR_STALLED:
            state = js.FAILED
            job.error = {
                "code": js.ERROR_STALLED,
                "reason": f"정체 — {self.cfg.stall_seconds}초 넘게 진행이 없어 끝냈다",
            }
        elif reason == js.ERROR_INTERRUPTED:
            state = js.INTERRUPTED
            job.error = {"code": js.ERROR_INTERRUPTED, "reason": INTERRUPTED_REASON}
        else:
            state = js.CANCELLED
            job.error = {"code": js.ERROR_CANCELLED, "reason": "취소 요청으로 끝냈다"}
        if job.promoted:
            self.spool.delete_results(job.job_id)
            job.artifact = None
        self._finish(job, state)

    def _abandon(self, job: Job) -> None:
        """승격 전 호출자 취소 — 작업도 취소한다(아무도 모르는 작업을 남기지 않는다)."""
        task = job.task
        if task is None or task.done():
            return
        if job.finalizing:  # 결과를 쓰는 중 — 끝나면 지운다
            task.add_done_callback(lambda _t: self.spool.delete(job.job_id))
            return
        job.cancel_reason = js.ERROR_CANCELLED
        task.cancel()

    def _persist(self, job: Job, *, force: bool = False) -> None:
        if not job.promoted and job.artifact is None:
            return  # 동기 작업은 결과 파일이 생길 때만 기록한다
        now = time.monotonic()
        if not force and now - job.persisted_at < _PERSIST_EVERY:
            return
        try:
            self.spool.write_record(job.job_id, job.record())
        except OSError:
            logger.exception("작업 기록 쓰기 실패: job_id=%s", job.job_id)
            return
        job.persisted_at = now

    # ── 작업 도구 ─────────────────────────────────────────

    def _not_found(self) -> ApmError:
        return ApmError(
            JOB_NOT_FOUND,
            f"작업이 없다 — 보관 기간({self.cfg.retention_seconds}초)이 지났거나 접근할 수 없는"
            " 작업이다",
        )

    def _lookup(
        self, job_id: Any, principal: str, owner: str | None
    ) -> tuple[dict[str, Any], Job | None]:
        if not js.is_job_id(job_id):
            raise self._not_found()
        job = self._live.get(job_id)
        record: dict[str, Any] | None
        if job is not None and job.promoted:
            record = job.record()
        else:
            job = None
            record = self.spool.read_record(job_id)
        if record is None or record.get("principal") != principal or record.get("owner") != owner:
            raise self._not_found()
        expires = _epoch(record.get("expires_at"))
        if job is None and expires is not None and self._clock() >= expires:
            self.spool.delete(job_id)
            raise self._not_found()
        return record, job

    def _preview(self, job_id: str, manifest: dict[str, Any]) -> list[Any]:
        rows: list[Any] = []
        for chunk in manifest.get("chunks") or []:
            if len(rows) >= self.cfg.inline_rows:
                break
            rows.extend(self.spool.read_chunk(job_id, chunk))
        hidden = frozenset(manifest.get("file_only_columns") or ())
        return _strip(rows[: self.cfg.inline_rows], hidden)

    async def status(
        self, job_id: Any, *, principal: str, owner: str | None = None
    ) -> dict[str, Any]:
        record, _ = self._lookup(job_id, principal, owner)
        state = record.get("state")
        extra: dict[str, Any] = {"job": handle_of(record)}
        rows: list[Any] = []
        manifest = record.get("artifact")
        if state in js.READABLE_STATES and manifest:
            try:
                rows = await asyncio.to_thread(self._preview, job_id, manifest)
            except (OSError, SpoolCorruptedError, ValueError) as e:
                raise ApmError(API_ERROR, f"결과 미리보기 읽기 실패: {type(e).__name__}") from e
            extra["result_meta"] = record.get("result_meta")
            extra["artifact"] = artifact_ref(job_id, manifest)
            extra["total_row_count"] = manifest.get("total_rows")
        return self._envelope(
            "apm_job_status",
            rows,
            limits=list(record.get("limits") or []),
            partial=state == js.PARTIAL,
            **extra,
        )

    async def cancel(
        self, job_id: Any, *, principal: str, owner: str | None = None
    ) -> dict[str, Any]:
        record, job = self._lookup(job_id, principal, owner)
        if job is not None and job.task is not None and not job.finalizing:
            if record.get("state") in js.LIVE_STATES and not job.task.done():
                job.cancel_reason = js.ERROR_CANCELLED
                job.task.cancel()
                await asyncio.wait({job.task}, timeout=_CANCEL_WAIT)
                record = self.spool.read_record(job.job_id) or job.record()
        return self._envelope(
            "apm_job_cancel", [], limits=list(record.get("limits") or []), job=handle_of(record)
        )

    async def read(
        self,
        job_id: Any,
        *,
        principal: str,
        owner: str | None = None,
        chunk: int | None = None,
        part: str | None = None,
    ) -> dict[str, Any]:
        record, _ = self._lookup(job_id, principal, owner)
        state = record.get("state")
        if state in js.LIVE_STATES:
            raise ApmError(
                JOB_NOT_READY,
                f"작업이 아직 끝나지 않았다(state={state}) — apm_job_status로 진행을 본다",
            )
        manifest = record.get("artifact")
        if state not in js.READABLE_STATES or not manifest:
            raise ApmError(INVALID_ARGUMENT, f"읽을 결과가 없는 작업이다(state={state})")
        if chunk is not None and part is not None:
            raise ApmError(INVALID_ARGUMENT, "chunk와 part는 하나만 준다")
        extra: dict[str, Any] = {
            "job": handle_of(record),
            "artifact": artifact_ref(job_id, manifest),
            "total_row_count": manifest.get("total_rows"),
        }
        limits = list(record.get("limits") or [])
        if part is not None:
            names = [p.get("name") for p in manifest.get("text_parts") or []]
            if part not in names:
                raise ApmError(INVALID_ARGUMENT, f"모르는 part {part!r} — 있는 부분: {names}")
            try:
                text = await asyncio.to_thread(self.spool.read_text, job_id, part)
            except (OSError, ValueError) as e:
                raise ApmError(API_ERROR, f"텍스트 부분 읽기 실패: {type(e).__name__}") from e
            return self._envelope("apm_job_read", [], limits=limits, part=part, text=text, **extra)
        chunks = manifest.get("chunks") or []
        index = 0 if chunk is None else int(chunk)
        if not 0 <= index < len(chunks):
            raise ApmError(
                INVALID_ARGUMENT,
                f"chunk {index}는 범위 밖이다 — 청크 {len(chunks)}개"
                + (f"(0~{len(chunks) - 1})" if chunks else "(결과 행 없음)"),
            )
        try:
            rows = await asyncio.to_thread(self.spool.read_chunk, job_id, chunks[index])
        except (OSError, SpoolCorruptedError, ValueError) as e:
            # 형식 이름만 — 예외 문구에는 스풀 경로가 실린다(본체는 스풀 경로를 모른다 · §3.1)
            raise ApmError(API_ERROR, f"결과 조각 읽기 실패: {type(e).__name__}") from e
        return self._envelope("apm_job_read", rows, limits=limits, chunk=index, **extra)

    # ── 수명 관리 ─────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        live = [j for j in self._live.values() if j.promoted]
        return {
            "running": sum(1 for j in live if j.state == js.RUNNING),
            "queued": sum(1 for j in live if j.state == js.QUEUED),
            "slots_in_use": self._slots.in_use,
            "max_concurrent": self._slots.size,
        }

    def recover(self) -> dict[str, int]:
        """기동 스캔 — 남은 queued·running 기록을 interrupted로, 만료 기록을 지운다."""
        self.spool.ensure_root()
        counts = {"interrupted": 0, "expired": 0, "dropped": 0, "tmp_files": self.spool.clear_tmp()}
        now = self._clock()
        for job_id in self.spool.job_ids():
            if job_id in self._live:
                continue
            record = self.spool.read_record(job_id)
            if record is None:
                self.spool.delete(job_id)
                counts["dropped"] += 1
                continue
            expires = _epoch(record.get("expires_at"))
            if expires is not None and now >= expires:
                self.spool.delete(job_id)
                counts["expired"] += 1
                continue
            if record.get("state") in js.LIVE_STATES:
                record.update(
                    state=js.INTERRUPTED,
                    error={"code": js.ERROR_INTERRUPTED, "reason": INTERRUPTED_REASON},
                    artifact=None,
                    finished_at=_iso(now),
                    updated_at=_iso(now),
                    expires_at=_iso(now + self.cfg.retention_seconds),
                )
                self.spool.delete_results(job_id)
                self.spool.write_record(job_id, record)
                counts["interrupted"] += 1
        logger.info("작업 스풀 기동 스캔: %s", counts)
        return counts

    def check_stalled(self, now: float | None = None) -> list[str]:
        """`running` 작업의 임대(`updated_at`)가 정체 기준을 넘으면 끝낸다(failed · stalled)."""
        now = self._clock() if now is None else now
        stalled = []
        for job in list(self._live.values()):
            if (
                job.promoted
                and job.state == js.RUNNING
                and not job.finalizing
                and job.task is not None
                and not job.task.done()
                and now - job.updated_at > self.cfg.stall_seconds
            ):
                job.cancel_reason = js.ERROR_STALLED
                job.task.cancel()
                stalled.append(job.job_id)
                logger.warning("작업 정체 — 끝낸다: job_id=%s tool=%s", job.job_id, job.tool)
        return stalled

    def sweep_expired(self, now: float | None = None) -> list[str]:
        """보관 기간이 지난 기록·결과를 지운다."""
        now = self._clock() if now is None else now
        removed = []
        for job_id in self.spool.job_ids():
            if job_id in self._live:
                continue
            record = self.spool.read_record(job_id)
            expires = _epoch(record.get("expires_at")) if record else None
            if record is None or (expires is not None and now >= expires):
                self.spool.delete(job_id)
                removed.append(job_id)
        return removed

    async def start(self) -> None:
        """기동 스캔 + 정체 감시·만료 정리 루프."""
        await asyncio.to_thread(self.recover)
        if self._maintainer is None or self._maintainer.done():
            self._maintainer = asyncio.create_task(self._maintain(), name="apm-job-maintainer")

    async def _maintain(self) -> None:
        interval = max(1.0, min(self.cfg.stall_seconds / 5, 30.0))
        sweep_every = max(interval, min(self.cfg.retention_seconds / 10, 300.0))
        last_sweep = time.monotonic()
        while True:
            await asyncio.sleep(interval)
            try:
                self.check_stalled()
                if time.monotonic() - last_sweep >= sweep_every:
                    await asyncio.to_thread(self.sweep_expired)
                    last_sweep = time.monotonic()
            except Exception:  # 감시 루프 한 바퀴 실패가 루프를 멈추지 않게
                logger.exception("작업 감시 루프 실패")

    async def aclose(self) -> None:
        """종료 — 감시 루프를 멈추고 진행 중 백그라운드 작업을 interrupted로 끝낸다."""
        if self._maintainer is not None:
            self._maintainer.cancel()
            await asyncio.gather(self._maintainer, return_exceptions=True)
            self._maintainer = None
        for job in list(self._live.values()):
            if job.task is not None and not job.task.done() and not job.finalizing:
                job.cancel_reason = js.ERROR_INTERRUPTED
                job.task.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
