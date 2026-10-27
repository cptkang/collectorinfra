"""조사 잡 저장소 — submit/poll 상태 관리 (Plan 05 §3·§8, D-017 예약).

collectorinfra의 MCP 호출 타임아웃(기본 60s)보다 조사(최대 600s · plans/187 W22)가 길어
동기 호출로는 계약이 성립하지 않으므로 **submit/poll 비동기 잡 패턴**을 구현한다.

설계 원칙(CLAUDE.md Known Mistakes 계승):
- 잡 상태는 **in-memory dict(값 bound + 키 만료 sweep) + 감사 JSONL 이중 기록**.
- **재기동 시 running/accepted 잡은 `failed(reason=restart)`로 확정**하고 감사에 남긴다
  (침묵 유실 금지). in-memory dict는 새 프로세스에서 비므로 감사 JSONL을 근거로 복구한다.
- **침묵 폴백 금지**: 조사 실행부(executor)가 미배선/LLM 키 부재이면 잡을 `done`이 아니라
  명확한 `stub` 상태로 확정하고 사유를 briefing/verdict에 노출한다.

범위 경계(Wave 2-C):
- 실 dispatcher(dedup TTL·동시 상한·전체 타임아웃 600s·시간당 예산)·severity_judge·
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

from sre_agent.application.investigation_guidance import clean_prior_findings
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.domain.cross_source_episode import episode_reference_time
from sre_agent.domain.incident_scope import (
    DEFAULT_LOOKBACK_MINUTES,
    alarm_time_to_iso,
    normalize_lookback,
    normalize_reference_time,
)
from sre_agent.domain.job_watchdog import STUCK_WATCHDOG_REASON, stuck_after_seconds
from sre_agent.infrastructure.activity_masking import mask_activity_text
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
    # 사건 좌표계(plans/50 A′-5 · D-197). push=alarmTime, pull=호출자 파싱. None이면 앵커 없는 조사.
    reference_time: str | None = None
    lookback_minutes: int | None = None
    # 결정적 상관 결과(CorrelationResult.to_dict — plans/50 G4). 사전수집 off·불가면 None.
    correlation: dict | None = None
    # 계측 가산 키(plans/154 W1 · D-324 ⑦) — dispatcher가 종결 시 채우고 `summary()`가 펼친다.
    # done: incomplete·elapsed_s·prefetch_s·steps·tool_calls_n·model·max_steps·
    #       tool_calls[{name, args_digest, status}]
    # timeout·failed: elapsed_s·model·max_steps. 원시 도구 기록(ToolCallRecord)은 남기지 않는다.
    instrumentation: dict | None = None
    # 플레이북 모드 진척(plans/187 W4 · G-9) — `SRE_PLAYBOOK_ENABLED` on이고
    # 플레이북이 돈 잡만 채운다.
    # progress: `{done, total, current_step}` · partial_briefing: 종결 전 부분 브리핑(종결 시 None).
    # 둘 다 None이면 `summary()`에 키가 생기지 않는다(off = 종전 반환과 비트 동일).
    progress: dict | None = None
    partial_briefing: dict | None = None
    # 실행 기록(plans/187 W13 · `SRE_ACTIVITY_LOG_ENABLED` on이고 접수된 잡만)
    # — 잡 TTL과 함께 사라진다.
    # `summary()`에는 나오지 않는다 — `live_view()`가 `activity_since ≥ 0` 조회에만 더한다.
    activity: ActivityLog | None = None
    # 동시 상한 대기 순번(plans/194 W3 · D-347) — dispatcher가 대기 중에만 단다(1부터 · 그 외 None).
    # 실행 기록 유무와 무관하게 `summary()`가 `phase="queued"`·`queue_position`으로 펼친다.
    queue_position: int | None = None

    def summary(self) -> dict:
        """§3 `sre_get_investigation`/`sre_list_investigations` 반환 스키마.

        계측 가산 키(`instrumentation`)는 종전 키 뒤에 더한다 — 종전 키는 그대로다(R-8).
        플레이북 진척 키(`progress`·`partial_briefing` — plans/187 W4)는 값이 있을 때만 더한다.
        동시 상한 대기 중(진행 중 + `queue_position`)이면 `phase="queued"`·`queue_position`을 더한다
        (plans/194 W3 — 실행 기록이 꺼져 있어도 상시 · 그 밖에는 키 없음).
        """
        summary = {
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
            "reference_time": self.reference_time,
            "lookback_minutes": self.lookback_minutes,
            "correlation": self.correlation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        summary.update(self.instrumentation or {})
        if self.progress is not None:
            summary["progress"] = self.progress
        if self.partial_briefing is not None:
            summary["partial_briefing"] = self.partial_briefing
        if self.queue_position is not None and self.status in ACTIVE_STATUSES:
            summary["phase"] = "queued"
            summary["queue_position"] = self.queue_position
        return summary

    def live_view(self, activity_since: int, now: float) -> dict:
        """실행 기록 가산 키(plans/187 W13) — `activity`·`phase`·`queue_position`·`elapsed_s`.

        `activity`는 `seq > activity_since`인 이벤트다(`next_seq`를 다음 커서로 쓴다).
        기록이 없는 잡(거부·중복 등)은 빈 기록이다. `phase`·`queue_position`은
        진행 중(D-123 `accepted`·`running`)일 때만 값이 있다 — 대기도 `status=running`이고
        `phase=queued`로 구분한다. `elapsed_s`는 접수 시각부터(동시 상한 대기 포함)이며 종결 잡은
        마지막 갱신 시각까지다 — 단 종결 잡에 계측 키 `elapsed_s`(동시 상한 통과부터 ·
        plans/154 W1)가 이미 있으면 `get()`이 그 값을 둔다.
        """
        log = self.activity
        active = self.status in ACTIVE_STATUSES
        if log is None:
            activity: dict = {"next_seq": 0, "truncated": False, "events": []}
            phase, position = None, None
        else:
            activity = log.since(activity_since)
            phase = log.phase if active else None
            position = log.queue_position if active else None
        end = now if active else self.updated_at
        return {
            "activity": activity,
            "phase": phase,
            "queue_position": position,
            "elapsed_s": round(max(0.0, end - self.created_at), 3),
        }


# executor(dispatcher 인터페이스): 잡을 받아 in-place로 terminal 상태(및 briefing/verdict 등)를
# 채운다. running으로 남기면 "진행 중"(실 async dispatcher 경로)으로 간주한다. 2-D가 실 dispatcher를
# 주입한다. 여기서는 make_stub_executor가 기본값.
Executor = Callable[[InvestigationJob], None]


def default_audit_path() -> Path:
    """감사 JSONL 기본 경로(JobStore·dispatcher 공용).

    **두 생산자가 같은 파일을 써야 한다** — JobStore는 accepted/running/terminal/restart_failed를,
    dispatcher는 done/timeout/failed를 남긴다. 갈라지면 잡의 전반부만 파일에 남아 운영자가
    결과를 추적할 수 없고, `recover_on_start`가 완료된 잡까지 active로 오인한다(D-213 후속).
    """
    return _DEFAULT_AUDIT_PATH


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
    노출한다. 조사 LLM 게이트 차단(플래그 off·키 부재 — `investigation_llm_stub_reason`)을 특히 구분해
    명시한다(§10.1 데이터 통제 맥락 · D-230).
    """

    def _stub(job: InvestigationJob) -> None:
        message = settings.investigation_llm_stub_reason() or "조사 미실행 — dispatcher 미배선(2-D 소관, 스텁)"
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

        # ── 낑긴 active 잡 워치독 (D-213 후속) ────────────────────────────
        # dispatcher 전체 타임아웃이 어떤 이유로든 발화하지 못해도(폐쇄망 실측:
        # 타임박스 밖 구간 wedge — 2026-09-10, 새 코드에서도 3.9h running 잔류)
        # 잡이 영원히 running으로 남지 않도록, updated_at이 임계(타임아웃×2,
        # 최소 600s)를 넘긴 active 잡을 failed로 확정한다(침묵 잔류 금지).
        # submit/get/list 경로에 편승 — 별도 스레드 없음. 매달린 워커가 훗날
        # 돌아와 덮어써도 감사 이력에는 두 이벤트가 모두 남는다.
        # 동시 상한 대기 중에 확정된 잡은 dispatcher가 슬롯을 얻은 직후 같은 임계로 보고
        # 조사하지 않는다(`_skip_if_stale` — 받을 쪽이 없는 조사로 슬롯을 쓰지 않게).
        stuck_after = stuck_after_seconds(self._settings.investigation_timeout_seconds)
        for job in self._jobs.values():
            if job.status in ACTIVE_STATUSES and (now - job.updated_at) > stuck_after:
                job.status = "failed"
                job.reason = STUCK_WATCHDOG_REASON
                job.error = (
                    f"워치독 확정: active {stuck_after:.0f}s 초과 — "
                    "dispatcher 타임아웃 미발화(wedge 의심). 스택은 SIGUSR1 덤프로 실측"
                )
                job.updated_at = now
                self._audit(
                    {
                        "investigation_id": job.investigation_id,
                        "event": "stuck_watchdog",
                        "status": "failed",
                        "reason": "stuck_watchdog",
                        "stuck_after_s": stuck_after,
                    }
                )

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
        except BaseException as exc:  # noqa: BLE001 — Exception만 잡으면 CancelledError가 새어나간다
            # dispatcher `_worker`와 같은 이유로 BaseException이다(D-213 근본원인).
            # 여기서 예외가 새면 바로 위에서 `running`으로 올려둔 잡이 그대로 남은 채
            # 예외만 submit 호출자에게 전파된다 — 잡은 영원히 active, 감사에는 아무 기록도 없다.
            job.status = "failed"
            job.error = f"executor 예외: {type(exc).__name__}: {exc}"
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

        반환: {investigation_id, status: accepted|duplicate|rejected, reason?, verdict?}.
        - 계약 위반 → `rejected`(사유 포함, 감사 기록).
        - 동일 fingerprint의 기존 잡 존재 → `duplicate`(기존 id).
        - dispatcher 동기 가드 거부(대기 상한 `queue_full`·`service_overloaded` 등) → `rejected`
          (`reason`·`verdict` — plans/194 W3).
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
            # 사건 좌표계: 알람 시각을 기준시각으로(변환 불가면 앵커 없는 조사 — 종전과 동일).
            job.reference_time = alarm_time_to_iso((payload.get("event") or {}).get("alarmTime"))
            if self._settings.cross_source_investigation_enabled:
                # plans/153 — 종합 조사는 사건 멤버 중 가장 늦은 시각을 기준으로 잡는다(증상 포함).
                # 질문·사전수집·타임라인이 모두 이 값을 읽는다. 플래그 off면 종전과 같다.
                job.reference_time = episode_reference_time(payload) or job.reference_time
            job.lookback_minutes = DEFAULT_LOOKBACK_MINUTES if job.reference_time else None
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
            return self._submit_result(job)

    def submit_diagnosis(
        self,
        question: str,
        server_name: str | None = None,
        hostname: str | None = None,
        db_id: str | None = None,
        target_state: dict | None = None,
        reference_time: str | None = None,
        lookback_minutes: int | None = None,
        prior_findings: dict | None = None,
    ) -> dict:
        """pull형 자연어 진단 잡을 제출한다(§3 `sre_diagnose`). 계약 검증 불요.

        `target_state`(Plan 81)는 호출자가 판정한 대상 가용성이며 잡 payload에 보존된다 —
        dispatcher의 가용성 가드가 이 값을 읽는다. 없으면 종전과 동일(가드 통과).

        `reference_time`(ISO 8601)·`lookback_minutes`(plans/50 A′-5)는 사건 좌표계다. 형식 오류는
        침묵 폴백 대신 **거부**한다(호출자가 결정적으로 만들어 넘기는 값이라 오류는 결함이다).

        `prior_findings`(plans/187 W10 · 선택)는 부모 조사의 판정 요약이다. 화이트리스트로
        다시 거른 값만 payload에 보관하고(서술 키 제거), 남는 것이 없거나 넘기지 않으면
        payload 키를 만들지 않는다
        (종전과 비트 동일). 지침 조립(`investigation_guidance`)이 이 값을 렌더한다.

        반환: {investigation_id, status: accepted|rejected, reason?, verdict?}
        — dispatcher 동기 가드 거부도 `rejected`로 바로 돌려준다(plans/194 W3).
        """
        with self._lock:
            self._sweep()
            reason: str | None = None
            if not question or not question.strip():
                reason = "question 결측"
            elif reference_time is not None:
                try:
                    reference_time = normalize_reference_time(reference_time)
                except ValueError as e:
                    reason = str(e)
            if reason is not None:
                job = self._new_job(kind="diagnosis", status="rejected", reason=reason)
                self._jobs[job.investigation_id] = job
                self._audit(
                    {"investigation_id": job.investigation_id, "event": "rejected", "status": "rejected", "reason": reason}
                )
                return {"investigation_id": job.investigation_id, "status": "rejected", "reason": reason}

            job = self._new_job(kind="diagnosis", status="accepted", question=question)
            job.reference_time = reference_time
            job.lookback_minutes = normalize_lookback(lookback_minutes) if reference_time else None
            job.payload = {
                "server_name": server_name,
                "hostname": hostname,
                "db_id": db_id,
                "target_state": target_state,
            }
            cleaned = clean_prior_findings(prior_findings) if prior_findings is not None else None
            if cleaned:
                job.payload["prior_findings"] = cleaned
            self._jobs[job.investigation_id] = job
            self._audit(
                {"investigation_id": job.investigation_id, "event": "accepted", "status": "accepted", "kind": "diagnosis"}
            )
            self._dispatch(job)
            return self._submit_result(job)

    @staticmethod
    def _submit_result(job: InvestigationJob) -> dict[str, object]:
        """접수 응답 — executor가 동기 가드로 거부를 확정했으면 바로 `rejected`로 돌려준다.

        plans/194 W3 · D-347: 종전에는 거부(대기 상한·같은 호스트·dedup·예산·가용성)도 항상
        `accepted`를 돌려줘 호출자가 poll해야 알았다. 거부면 `reason`과 사람이 읽는 `verdict`
        (가용성 거부의 사실 문구 포함 — G-2)를 싣는다. 그 밖(진행 중·스텁 등)은 종전대로 `accepted`.
        """
        if job.status == "rejected":
            return {"investigation_id": job.investigation_id, "status": "rejected",
                    "reason": job.reason, "verdict": job.verdict}
        return {"investigation_id": job.investigation_id, "status": "accepted"}

    def get(self, investigation_id: str, activity_since: int = -1) -> dict:
        """조사 잡 상태를 조회한다(§3 `sre_get_investigation`).

        미존재 id는 침묵하지 않고 `not_found`로 정직하게 보고한다.
        `activity_since ≥ 0`이고 `sre_activity_log_enabled`면 실행 기록 가산 키(`live_view`)를
        더한다
        (plans/187 W13). `-1`(기본)이거나 플래그 off면 종전 반환과 비트 동일하다.
        """
        with self._lock:
            self._sweep()
            job = self._jobs.get(investigation_id)
            if job is None:
                return {"status": "not_found", "investigation_id": investigation_id, "error": "해당 investigation_id 없음"}
            summary = job.summary()
            if activity_since >= 0 and self._settings.sre_activity_log_enabled:
                live = job.live_view(activity_since, self._clock())
                if "elapsed_s" in summary:
                    # 종결 잡의 계측 키(plans/154 W1 — 슬롯 획득부터)는 뜻을 바꾸지 않는다.
                    live.pop("elapsed_s")
                summary.update(live)
            return summary

    def list(self, limit: int = 20) -> dict:
        """최근 조사 잡 요약을 반환한다(§3 `sre_list_investigations`).

        in-memory dict(재기동 시 감사 JSONL로 복구됨)를 updated_at 내림차순으로 반환한다.
        """
        with self._lock:
            self._sweep()
            jobs = sorted(self._jobs.values(), key=lambda j: j.updated_at, reverse=True)
            limited = jobs[: max(0, limit)]
            return {"count": len(limited), "investigations": [j.summary() for j in limited]}

    def load_snapshot(self) -> dict[str, object] | None:
        """executor 부하 게이지(plans/194 W3 — `sre_health`).

        게이지가 없는 executor(스텁)면 None.
        """
        snapshot = getattr(self._executor, "load_snapshot", None)
        return snapshot() if callable(snapshot) else None

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
        job = InvestigationJob(
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
        # 실행 기록(plans/187 W13) — 접수된 잡만. off면 만들지 않는다(기록 지점 전부 무동작).
        if status == "accepted" and self._settings.sre_activity_log_enabled:
            job.activity = ActivityLog(masker=mask_activity_text, clock=self._clock)
        return job


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
