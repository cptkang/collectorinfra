"""조사 잡 저장소 — submit/poll 상태 관리 (Plan 05 §3·§8, D-017 예약).

collectorinfra의 MCP 호출 타임아웃(기본 60s)보다 조사(최대 300s)가 길어
동기 호출로는 계약이 성립하지 않으므로 **submit/poll 비동기 잡 패턴**을 구현한다.

설계 원칙(CLAUDE.md Known Mistakes 계승):
- 잡 상태는 **in-memory dict(값 bound + 키 만료 sweep) + 감사 JSONL 이중 기록**.
- **재기동 시 running/accepted 잡은 `failed(reason=restart)`로 확정**하고 감사에 남긴다
  (침묵 유실 금지). in-memory dict는 새 프로세스에서 비므로 감사 JSONL을 근거로 복구한다.
- **침묵 폴백 금지**: 조사 실행부(executor)가 미배선/LLM 키 부재이면 잡을 `done`이 아니라
  명확한 `stub` 상태로 확정하고 사유를 briefing/verdict에 노출한다.

범위 경계(Wave 2-C):
- 실 dispatcher(dedup TTL·동시 상한·전체 타임아웃 300s·시간당 예산)·severity_judge·
  브리핑 6요소 조립은 **2-D 소관**. 여기서는 그 **호출 지점(executor 인터페이스)**만 두고
  최소 스텁 executor를 기본 주입한다. 기본 dedup(동일 fingerprint 재submit → duplicate)만 제공한다.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sre_agent.settings import AgentSettings

logger = logging.getLogger(__name__)

# 트리거 페이로드 계약 버전 (Plan 05 §4). 구버전 1개 병행 수용은 필수 필드 변경 시 도입.
CONTRACT_VERSION = "1"

# 필수 event 필드 (Plan 05 §4 — 식별자 이원화 규칙, Plan 01 §4).
# 선택 필드(decision.signals·meta 등) 결측은 거부하지 않는다(스코프만 축소).
REQUIRED_EVENT_FIELDS: tuple[str, ...] = ("serverName", "hostname", "severity")

# 잡 상태.
ACTIVE_STATUSES: frozenset[str] = frozenset({"accepted", "running"})
TERMINAL_STATUSES: frozenset[str] = frozenset({"done", "failed", "timeout", "stub", "rejected"})

# in-memory 저장소 기본 bound.
_DEFAULT_MAX_JOBS = 500
_DEFAULT_TTL_SECONDS = 3600.0

# 감사 JSONL 기본 경로(런타임 전용 — 테스트는 항상 tmp 경로를 주입한다).
_DEFAULT_AUDIT_PATH = Path(__file__).resolve().parents[2] / ".data" / "investigation_audit.jsonl"


@dataclass
class InvestigationJob:
    """단일 조사 잡의 상태. `summary()`는 §3 get/list 반환용 dict를 만든다."""

    investigation_id: str
    kind: str  # "alarm" | "diagnosis"
    status: str  # accepted|running|done|failed|timeout|stub|rejected
    created_at: float
    updated_at: float
    fingerprint: str | None = None
    payload: dict | None = None
    question: str | None = None
    briefing: dict | None = None
    verdict: str | None = None
    tool_calls_summary: list[str] | None = None
    tokens: int | None = None
    cost: float | None = None
    error: str | None = None
    reason: str | None = None

    def summary(self) -> dict:
        """§3 `sre_get_investigation`/`sre_list_investigations` 반환 스키마."""
        return {
            "investigation_id": self.investigation_id,
            "kind": self.kind,
            "status": self.status,
            "fingerprint": self.fingerprint,
            "briefing": self.briefing,
            "verdict": self.verdict,
            "tool_calls_summary": self.tool_calls_summary,
            "tokens": self.tokens,
            "cost": self.cost,
            "error": self.error,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# executor(dispatcher 인터페이스): 잡을 받아 in-place로 terminal 상태(및 briefing/verdict 등)를
# 채운다. running으로 남기면 "진행 중"(실 async dispatcher 경로)으로 간주한다. 2-D가 실 dispatcher를
# 주입한다. 여기서는 make_stub_executor가 기본값.
Executor = Callable[[InvestigationJob], None]


def validate_payload(payload: object) -> tuple[bool, str | None]:
    """트리거 페이로드 계약(Plan 05 §4)을 검증한다.

    반환: (유효 여부, 거부 사유). contract_version 불일치·event 결측·필수 필드
    (serverName/hostname/severity) 결측 시 거부한다. severity 0은 유효(빈값만 거부).
    """
    if not isinstance(payload, dict):
        return False, "payload가 JSON 객체가 아님"
    version = payload.get("contract_version")
    if version != CONTRACT_VERSION:
        return False, f"지원하지 않는 contract_version: {version!r} (지원: {CONTRACT_VERSION!r})"
    event = payload.get("event")
    if not isinstance(event, dict):
        return False, "event 객체 결측"
    missing = [f for f in REQUIRED_EVENT_FIELDS if event.get(f) in (None, "")]
    if missing:
        return False, f"필수 event 필드 결측: {', '.join(missing)}"
    return True, None


def make_stub_executor(settings: AgentSettings) -> Executor:
    """실 dispatcher 미배선 상태의 명시적 스텁 executor(침묵 금지).

    조사를 실제로 수행하지 않고 잡을 `stub` 상태로 확정하며, 사유를 briefing/verdict에
    노출한다. LLM 키(gemini_api_key) 부재를 특히 구분해 명시한다(§10.1 데이터 통제 맥락).
    """

    def _stub(job: InvestigationJob) -> None:
        if settings.gemini_api_key is None:
            message = "조사 미실행 — LLM 키 부재(스텁)"
        else:
            message = "조사 미실행 — dispatcher 미배선(2-D 소관, 스텁)"
        job.status = "stub"
        job.verdict = message
        # 브리핑 6요소(Plan 02 §7) 조립은 2-D 소관 — 여기서는 스텁 표기만.
        job.briefing = {"stub": True, "message": message, "elements": None}
        job.tool_calls_summary = []
        job.tokens = 0
        job.cost = 0.0
        job.error = None

    return _stub


class JobStore:
    """조사 잡 in-memory 저장소 + 감사 JSONL 이중 기록.

    스레드 안전(단일 Lock). 모든 상태 변화는 감사 JSONL에 append된다.
    """

    def __init__(
        self,
        settings: AgentSettings,
        executor: Executor | None = None,
        audit_path: str | Path | None = None,
        max_jobs: int = _DEFAULT_MAX_JOBS,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._executor: Executor = executor or make_stub_executor(settings)
        self._audit_path = Path(audit_path) if audit_path is not None else _DEFAULT_AUDIT_PATH
        self._max_jobs = max_jobs
        self._ttl = ttl_seconds
        self._clock = clock
        self._jobs: dict[str, InvestigationJob] = {}
        self._fingerprint_index: dict[str, str] = {}
        self._lock = threading.Lock()

    # ── 감사 JSONL ──────────────────────────────────────────

    def _audit(self, record: dict) -> None:
        """감사 JSONL에 한 줄 append한다(디렉토리 자동 생성)."""
        record = {"ts": self._clock(), **record}
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:  # 감사 기록 실패는 침묵하지 않고 로그로 가시화
            logger.error("감사 JSONL 기록 실패: %s (%s)", self._audit_path, exc)

    # ── sweep(값 bound + 키 만료) ────────────────────────────

    def _evict(self, investigation_id: str) -> None:
        job = self._jobs.pop(investigation_id, None)
        if job is not None and job.fingerprint is not None:
            if self._fingerprint_index.get(job.fingerprint) == investigation_id:
                self._fingerprint_index.pop(job.fingerprint, None)

    def _sweep(self) -> None:
        """terminal 잡을 TTL·최대 개수 기준으로 제거한다(active 잡은 보존)."""
        now = self._clock()
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.status in TERMINAL_STATUSES and (now - job.updated_at) > self._ttl
        ]
        for jid in expired:
            self._evict(jid)

        if len(self._jobs) > self._max_jobs:
            terminal = sorted(
                (j for j in self._jobs.values() if j.status in TERMINAL_STATUSES),
                key=lambda j: j.updated_at,
            )
            for job in terminal:
                if len(self._jobs) <= self._max_jobs:
                    break
                self._evict(job.investigation_id)

    # ── 잡 실행(executor 위임) ───────────────────────────────

    def _dispatch(self, job: InvestigationJob) -> None:
        """accepted 잡을 running으로 올리고 executor에 위임한다.

        executor 예외는 삼키지 않고 `failed`로 확정·감사한다(침묵 폴백 금지).
        executor가 running으로 남기면 진행 중으로 간주(실 async dispatcher 경로)한다.
        """
        job.status = "running"
        job.updated_at = self._clock()
        self._audit({"investigation_id": job.investigation_id, "event": "running", "status": "running"})
        try:
            self._executor(job)
        except Exception as exc:  # noqa: BLE001 — 실패 사유를 구조화해 노출
            job.status = "failed"
            job.error = f"executor 예외: {exc}"
            logger.exception("executor 예외: investigation_id=%s", job.investigation_id)
        job.updated_at = self._clock()
        if job.status not in ACTIVE_STATUSES:
            self._audit(
                {
                    "investigation_id": job.investigation_id,
                    "event": "terminal",
                    "status": job.status,
                    "reason": job.reason,
                    "error": job.error,
                }
            )

    # ── 공개 API ────────────────────────────────────────────

    def submit(self, payload: object) -> dict:
        """알람 트리거 페이로드로 조사 잡을 제출한다(§3 `sre_investigate_alarm`).

        반환: {investigation_id, status: accepted|duplicate|rejected, reason?}.
        - 계약 위반 → `rejected`(사유 포함, 감사 기록).
        - 동일 fingerprint의 기존 잡 존재 → `duplicate`(기존 id).
        """
        with self._lock:
            self._sweep()

            ok, reason = validate_payload(payload)
            if not ok:
                job = self._new_job(kind="alarm", status="rejected", reason=reason, payload=payload if isinstance(payload, dict) else None)
                self._jobs[job.investigation_id] = job
                self._audit(
                    {"investigation_id": job.investigation_id, "event": "rejected", "status": "rejected", "reason": reason}
                )
                return {"investigation_id": job.investigation_id, "status": "rejected", "reason": reason}

            assert isinstance(payload, dict)  # validate_payload 통과 보장
            fingerprint = (payload.get("decision") or {}).get("fingerprint")

            if fingerprint:
                existing_id = self._fingerprint_index.get(fingerprint)
                if existing_id and existing_id in self._jobs:
                    self._audit(
                        {
                            "investigation_id": existing_id,
                            "event": "duplicate",
                            "status": "duplicate",
                            "fingerprint": fingerprint,
                        }
                    )
                    return {"investigation_id": existing_id, "status": "duplicate"}

            job = self._new_job(kind="alarm", status="accepted", fingerprint=fingerprint, payload=payload)
            self._jobs[job.investigation_id] = job
            if fingerprint:
                self._fingerprint_index[fingerprint] = job.investigation_id
            self._audit(
                {
                    "investigation_id": job.investigation_id,
                    "event": "accepted",
                    "status": "accepted",
                    "fingerprint": fingerprint,
                    "kind": "alarm",
                }
            )
            self._dispatch(job)
            return {"investigation_id": job.investigation_id, "status": "accepted"}

    def submit_diagnosis(
        self,
        question: str,
        server_name: str | None = None,
        hostname: str | None = None,
        db_id: str | None = None,
    ) -> dict:
        """pull형 자연어 진단 잡을 제출한다(§3 `sre_diagnose`). 계약 검증 불요.

        반환: {investigation_id, status: accepted|rejected, reason?}.
        """
        with self._lock:
            self._sweep()
            if not question or not question.strip():
                job = self._new_job(kind="diagnosis", status="rejected", reason="question 결측")
                self._jobs[job.investigation_id] = job
                self._audit(
                    {"investigation_id": job.investigation_id, "event": "rejected", "status": "rejected", "reason": "question 결측"}
                )
                return {"investigation_id": job.investigation_id, "status": "rejected", "reason": "question 결측"}

            job = self._new_job(kind="diagnosis", status="accepted", question=question)
            job.payload = {"server_name": server_name, "hostname": hostname, "db_id": db_id}
            self._jobs[job.investigation_id] = job
            self._audit(
                {"investigation_id": job.investigation_id, "event": "accepted", "status": "accepted", "kind": "diagnosis"}
            )
            self._dispatch(job)
            return {"investigation_id": job.investigation_id, "status": "accepted"}

    def get(self, investigation_id: str) -> dict:
        """조사 잡 상태를 조회한다(§3 `sre_get_investigation`).

        미존재 id는 침묵하지 않고 `not_found`로 정직하게 보고한다.
        """
        with self._lock:
            self._sweep()
            job = self._jobs.get(investigation_id)
            if job is None:
                return {"status": "not_found", "investigation_id": investigation_id, "error": "해당 investigation_id 없음"}
            return job.summary()

    def list(self, limit: int = 20) -> dict:
        """최근 조사 잡 요약을 반환한다(§3 `sre_list_investigations`).

        in-memory dict(재기동 시 감사 JSONL로 복구됨)를 updated_at 내림차순으로 반환한다.
        """
        with self._lock:
            self._sweep()
            jobs = sorted(self._jobs.values(), key=lambda j: j.updated_at, reverse=True)
            limited = jobs[: max(0, limit)]
            return {"count": len(limited), "investigations": [j.summary() for j in limited]}

    def recover_on_start(self) -> int:
        """재기동 복구: 감사 JSONL에서 마지막 상태가 active(accepted/running)인 잡을
        `failed(reason=restart)`로 확정하고 in-memory에 적재한다(침묵 유실 금지).

        반환: 복구(실패 확정)한 잡 수.
        """
        with self._lock:
            last: dict[str, dict] = {}
            if self._audit_path.exists():
                for line in self._audit_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    jid = rec.get("investigation_id")
                    if jid:
                        last[jid] = rec

            recovered = 0
            now = self._clock()
            for jid, rec in last.items():
                if rec.get("status") in ACTIVE_STATUSES:
                    job = InvestigationJob(
                        investigation_id=jid,
                        kind=rec.get("kind", "alarm"),
                        status="failed",
                        created_at=now,
                        updated_at=now,
                        fingerprint=rec.get("fingerprint"),
                        reason="restart",
                        error="재기동 중단으로 실패 확정(restart)",
                    )
                    self._jobs[jid] = job
                    self._audit(
                        {"investigation_id": jid, "event": "restart_failed", "status": "failed", "reason": "restart"}
                    )
                    recovered += 1
            if recovered:
                logger.warning("재기동 복구: running/accepted 잡 %d건을 failed(restart)로 확정", recovered)
            return recovered

    # ── 내부 헬퍼 ────────────────────────────────────────────

    def _new_job(
        self,
        kind: str,
        status: str,
        fingerprint: str | None = None,
        payload: dict | None = None,
        question: str | None = None,
        reason: str | None = None,
    ) -> InvestigationJob:
        now = self._clock()
        return InvestigationJob(
            investigation_id=uuid.uuid4().hex,
            kind=kind,
            status=status,
            created_at=now,
            updated_at=now,
            fingerprint=fingerprint,
            payload=payload,
            question=question,
            reason=reason,
        )


__all__ = [
    "CONTRACT_VERSION",
    "REQUIRED_EVENT_FIELDS",
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
    "InvestigationJob",
    "Executor",
    "JobStore",
    "validate_payload",
    "make_stub_executor",
]
