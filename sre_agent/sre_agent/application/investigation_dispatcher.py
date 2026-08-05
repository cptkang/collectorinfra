"""조사 dispatcher — 결정적 폭주 방지 가드 (Plan 02 §4·§10, D-035 계승).

HolmesGPT ReAct 조사 루프를 감싸되, **트리거·dedup·동시성·타임아웃·예산은 코드가 전담**한다
(LLM에 위임 금지). JobStore(2-C)가 남긴 executor 주입점에 배선되는 실 dispatcher다.

가드 5종:
1. fingerprint dedup TTL — 완료된 조사도 `investigation_dedup_ttl_seconds` 내 재조사 억제.
2. 동시 상한 — `investigation_max_concurrent`(기본 2) 세마포어.
3. 전체 타임아웃 — `investigation_timeout_seconds`(기본 300s), **조사 1건 전체**에 asyncio.wait_for
   (per-call 아님). collectorinfra MCP 동기 타임아웃(60s)보다 길므로 조사는 백그라운드 워커에서
   실행하고 submit은 즉시 `running`을 남겨 반환한다(submit/poll 계약 성립).
4. 시간당 예산 — `investigation_hourly_budget` 초과 시 신규 조사 거부.
5. 토큰 비용 감사 — DiagnosisResult.total_tokens/total_cost를 잡·decision JSONL에 기록.

in-memory 상태(dedup dict·budget window)는 **값 bound + 키 만료 sweep**을 동시 구현한다
(CLAUDE.md Known Mistakes — 데몬 dict는 값 bound뿐 아니라 키 sweep도).

계층 청결(0 warning): job/DiagnosisResult 등 application 세부 타입은 **구조적 Protocol**로
디커플링하고, 실 조사 함수(diagnose_fn)·브리핑 함수(briefing_fn)는 **주입**받는다. 직접 참조는
domain(severity_signatures)뿐이다(application→domain 허용). LLM 키 부재 시 스텁을 유지하되
가드는 그대로 적용한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

from sre_agent.domain.remediation import recommend_lines
from sre_agent.domain.severity_signatures import (
    LEVEL_NAMES,
    ImportanceVerdict,
    clamp_level,
    judge,
)
from sre_agent.settings import AgentSettings

logger = logging.getLogger(__name__)

# dedup dict 방어적 하드 상한(ttl sweep에 더해 무한 성장 방지).
_MAX_DEDUP_KEYS = 4096


# ── 구조적 타입(Protocol) — application 세부에 직접 의존하지 않기 위함 ──


class ToolOutputLike(Protocol):
    tool_name: str
    description: str
    output: str
    error: str | None


class DiagnosisLike(Protocol):
    answer: str
    total_tokens: int
    total_cost: float
    tool_calls: list[str]
    tool_outputs: Sequence[ToolOutputLike]


class JobLike(Protocol):
    investigation_id: str
    kind: str
    status: str
    fingerprint: str | None
    payload: dict | None
    question: str | None
    verdict: str | None
    briefing: dict | None
    tool_calls_summary: list[str] | None
    tokens: int | None
    cost: float | None
    error: str | None
    reason: str | None
    updated_at: float


DiagnoseFn = Callable[[JobLike], DiagnosisLike]
BriefingFn = Callable[..., dict]


class InvestigationDispatcher:
    """JobStore executor로 주입되는 결정적 dispatcher.

    __call__(job)이 executor 인터페이스다. dedup·예산 가드는 동기로 판정하고(위반 시 즉시
    terminal 확정), 통과하면 조사를 **백그라운드 워커**에 넘기고 job을 `running`으로 남긴다
    (JobStore는 running을 진행 중으로 간주). 워커가 동시 상한·전체 타임아웃 하에 조사·후처리를
    수행하고 job을 terminal로 확정한다.
    """

    def __init__(
        self,
        settings: AgentSettings,
        diagnose_fn: DiagnoseFn | None = None,
        briefing_fn: BriefingFn | None = None,
        *,
        remote: bool = False,
        timeout_seconds: float | None = None,
        audit_path: str | Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._diagnose_fn = diagnose_fn
        self._briefing_fn = briefing_fn
        self._remote = remote
        self._timeout = timeout_seconds if timeout_seconds is not None else float(settings.investigation_timeout_seconds)
        self._audit_path = Path(audit_path) if audit_path is not None else None
        self._clock = clock
        self._wall_clock = wall_clock

        self._semaphore = threading.BoundedSemaphore(max(1, settings.investigation_max_concurrent))
        self._lock = threading.Lock()
        self._dedup: dict[str, float] = {}          # fingerprint -> 마지막 조사 시작 시각(clock)
        self._budget_window: deque[float] = deque()  # 최근 1시간 조사 시작 시각들
        self._workers: set[threading.Thread] = set()

    # ── executor 진입점 ────────────────────────────────────────

    def __call__(self, job: JobLike) -> None:
        """JobStore가 호출하는 executor. 동기 가드 → (통과 시) 백그라운드 조사 위임."""
        # 스텁 경로(조사 불가): LLM 키 부재 또는 조사함수 미주입 → 동기 스텁 확정.
        # 스텁도 폭주 가드(dedup·예산)는 그대로 통과시켜야 하므로 가드 판정을 먼저 한다.
        blocked = self._apply_sync_guards(job)
        if blocked is not None:
            job.status, job.reason = "rejected", blocked
            job.verdict = f"조사 거부 — {blocked}"
            job.briefing = {"stub": True, "message": job.verdict, "elements": None}
            job.tool_calls_summary, job.tokens, job.cost = [], 0, 0.0
            job.updated_at = self._wall_clock()
            self._audit({"event": "rejected", "investigation_id": job.investigation_id, "reason": blocked})
            return

        if self._diagnose_fn is None or self._settings.gemini_api_key is None:
            self._finalize_stub(job)
            return

        # 실 조사: running 유지 + 백그라운드 워커 위임(submit 즉시 반환).
        job.status = "running"
        job.updated_at = self._wall_clock()
        worker = threading.Thread(target=self._worker, args=(job,), daemon=True)
        with self._lock:
            self._workers.add(worker)
        worker.start()

    # ── 동기 가드(dedup TTL · 시간당 예산) + sweep ──────────────

    def _apply_sync_guards(self, job: JobLike) -> str | None:
        """dedup TTL·예산 가드를 판정한다. 통과면 None, 차단이면 사유 문자열.

        통과 시 dedup·예산 상태를 기록한다. in-memory 상태는 값 bound + 키 만료 sweep한다.
        """
        now = self._clock()
        with self._lock:
            self._sweep(now)

            fp = job.fingerprint
            ttl = self._settings.investigation_dedup_ttl_seconds
            if fp and ttl is not None:
                last = self._dedup.get(fp)
                if last is not None and (now - last) < ttl:
                    return "dedup_ttl_active"

            budget = self._settings.investigation_hourly_budget
            if budget is not None and len(self._budget_window) >= budget:
                return "hourly_budget_exceeded"

            # 통과 — 상태 기록.
            if fp and ttl is not None:
                self._dedup[fp] = now
            if budget is not None:
                self._budget_window.append(now)
            return None

    def _sweep(self, now: float) -> None:
        """dedup dict(ttl 만료·하드 상한)·budget window(1시간 만료)를 정리한다(lock 보유 전제)."""
        ttl = self._settings.investigation_dedup_ttl_seconds
        if ttl is not None:
            expired = [fp for fp, ts in self._dedup.items() if (now - ts) > ttl]
            for fp in expired:
                self._dedup.pop(fp, None)
        # 방어적 하드 상한(가장 오래된 것부터 축출).
        if len(self._dedup) > _MAX_DEDUP_KEYS:
            for fp in sorted(self._dedup, key=self._dedup.get)[: len(self._dedup) - _MAX_DEDUP_KEYS]:
                self._dedup.pop(fp, None)
        # budget window: 1시간 초과분 제거.
        while self._budget_window and (now - self._budget_window[0]) > 3600.0:
            self._budget_window.popleft()

    # ── 백그라운드 워커(동시 상한 · 전체 타임아웃 · 후처리) ─────

    def _worker(self, job: JobLike) -> None:
        try:
            with self._semaphore:  # 동시 상한
                self._run_and_postprocess(job)
        except Exception as exc:  # noqa: BLE001 — 실패를 침묵하지 않고 구조화 노출
            job.status = "failed"
            job.error = f"dispatcher 워커 예외: {exc}"
            job.updated_at = self._wall_clock()
            logger.exception("dispatcher 워커 예외: investigation_id=%s", job.investigation_id)
            self._audit({"event": "failed", "investigation_id": job.investigation_id, "error": job.error})
        finally:
            with self._lock:
                self._workers.discard(threading.current_thread())

    def _run_and_postprocess(self, job: JobLike) -> None:
        """전체 타임아웃 하에 조사를 실행하고 severity_judge·briefing으로 후처리한다."""
        try:
            result = self._investigate_with_timeout(job)
        except asyncio.TimeoutError:
            # 전체 타임아웃 — 부분 결과 없이 사유를 구조화해 전달(침묵 실패 금지, §12-④).
            job.status = "timeout"
            job.reason = "investigation_timeout"
            job.verdict = f"조사 타임아웃(미완주) — investigation_timeout_seconds({self._timeout}s) 초과"
            job.briefing = {"stub": True, "message": job.verdict, "elements": None}
            job.tool_calls_summary, job.tokens, job.cost = [], 0, 0.0
            job.updated_at = self._wall_clock()
            self._audit({"event": "timeout", "investigation_id": job.investigation_id, "timeout_s": self._timeout})
            return

        gate_severity, gate_tier = self._gate_context(job)
        verdict = self._run_severity_judge(gate_severity, result)
        briefing = self._briefing_fn(
            answer=result.answer,
            verdict=verdict,
            tool_names=[to.tool_name for to in result.tool_outputs],
            gate_tier=gate_tier,
            remediation=self._recommend_remediation(verdict),
        )

        job.status = "done"
        job.verdict = f"{verdict.level}(신뢰도 {verdict.confidence}) escalate={verdict.escalate}"
        job.briefing = briefing
        job.tool_calls_summary = list(result.tool_calls)
        job.tokens = result.total_tokens
        job.cost = result.total_cost
        job.updated_at = self._wall_clock()
        self._audit(
            {
                "event": "done",
                "investigation_id": job.investigation_id,
                "level": verdict.level,
                "confidence": verdict.confidence,
                "escalate": verdict.escalate,
                "signals": [s.name for s in verdict.signals],
                "tokens": result.total_tokens,
                "cost": result.total_cost,
            }
        )

    def _investigate_with_timeout(self, job: JobLike) -> DiagnosisLike:
        """조사 **전체**를 asyncio.wait_for로 감싸 실행한다(per-call 타임아웃 아님).

        블로킹 조사(diagnose_fn)를 executor 스레드에서 돌리고 wait_for로 전체를 감싼다.
        타임아웃 시 in-flight 스레드는 결과가 폐기된다(중단 불가하나 결과 미채택).
        """
        assert self._diagnose_fn is not None

        async def _runner() -> DiagnosisLike:
            loop = asyncio.get_running_loop()
            return await asyncio.wait_for(
                loop.run_in_executor(None, self._diagnose_fn, job),
                timeout=self._timeout,
            )

        return asyncio.run(_runner())

    def _run_severity_judge(self, gate_severity: int, result: DiagnosisLike) -> ImportanceVerdict:
        """severity_judge_enabled면 도구 원시 출력 시그니처 매칭, 아니면 게이트 승계(상향 없음)."""
        if not self._settings.severity_judge_enabled:
            return ImportanceVerdict(
                level=LEVEL_NAMES[clamp_level(gate_severity)],
                confidence="none",
                escalate=False,
            )
        texts: list[str] = [to.output for to in result.tool_outputs]
        texts += [to.error for to in result.tool_outputs if to.error]
        return judge(gate_severity, texts, remote=self._remote)

    def _recommend_remediation(self, verdict: ImportanceVerdict) -> list[str] | None:
        """remediation_recommender_enabled면 매칭 시그니처에서 조치 후보를 도출한다(제시 전용).

        off면 None을 반환해 briefing_builder의 기존 문구를 유지한다(회귀 0). 켜져 있어도
        매칭 시그니처가 없으면 None — 근거 없는 권고를 만들지 않는다(D-035·§9).
        **실행 경로 없음**: 반환값은 브리핑에 실릴 문자열일 뿐이다(D-003·D-011).
        """
        if not self._settings.remediation_recommender_enabled:
            return None
        lines = recommend_lines(verdict.signals)
        return lines or None

    # ── 스텁(조사 불가) ─────────────────────────────────────────

    def _finalize_stub(self, job: JobLike) -> None:
        """LLM 키 부재 또는 조사함수 미주입 시 명시적 스텁 확정(침묵 금지)."""
        if self._settings.gemini_api_key is None:
            message = "조사 미실행 — LLM 키 부재(스텁)"
        else:
            message = "조사 미실행 — dispatcher 조사함수 미주입(스텁)"
        job.status = "stub"
        job.verdict = message
        job.briefing = {"stub": True, "message": message, "elements": None}
        job.tool_calls_summary, job.tokens, job.cost, job.error = [], 0, 0.0, None
        job.updated_at = self._wall_clock()
        self._audit({"event": "stub", "investigation_id": job.investigation_id, "message": message})

    # ── 게이트 컨텍스트 추출 ────────────────────────────────────

    @staticmethod
    def _gate_context(job: JobLike) -> tuple[int, str | None]:
        """잡에서 게이트 severity·tier를 추출한다(alarm만 게이트 존재, diagnosis는 baseline 0)."""
        payload = job.payload or {}
        if job.kind == "alarm":
            event = payload.get("event") or {}
            raw = event.get("severity")
            try:
                severity = int(raw)
            except (TypeError, ValueError):
                severity = 0
            tier = (payload.get("decision") or {}).get("tier")
            return severity, tier
        return 0, None

    # ── 감사(decision JSONL) ───────────────────────────────────

    def _audit(self, record: dict) -> None:
        """토큰 비용·판정 등 decision 레코드를 감사한다(경로 미설정 시 로그로만)."""
        record = {"ts": self._wall_clock(), **record}
        if self._audit_path is None:
            logger.info("dispatcher decision: %s", record)
            return
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.error("decision JSONL 기록 실패: %s (%s)", self._audit_path, exc)

    # ── 테스트/종료 지원 ───────────────────────────────────────

    def wait_workers(self, timeout: float | None = None) -> None:
        """진행 중인 백그라운드 워커가 모두 끝날 때까지 대기한다(테스트·graceful 종료용)."""
        with self._lock:
            workers = list(self._workers)
        for worker in workers:
            worker.join(timeout)


__all__ = [
    "InvestigationDispatcher",
    "ToolOutputLike",
    "DiagnosisLike",
    "JobLike",
    "DiagnoseFn",
    "BriefingFn",
]
