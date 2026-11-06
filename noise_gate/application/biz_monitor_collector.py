"""업무 현황 M3 실시간 지표 수집기 (plans/215 §4.3 · §4.4 · §4.5 · D-368).

API 프로세스 백그라운드 태스크 1개다(`NOISE_BIZ_MONITOR_METRICS_ENABLED` —
`biz_monitor_metrics_active`일 때만 기동). 통보 0(Q16) · LLM 0.

- **리더 잠금** `bizmon:leader`(`worker_lock` 패턴 — SET NX EX · Lua 연장 · 자기 값만 해제) — API
  인스턴스가 여럿이어도 수집은 한 곳.
- **수요 기반**(Q13): `bizmon:viewers`(보드 heartbeat가 `EX 120`)가 없으면 그 주기 관측 호출 0.
- **감시 대상만**: 사본(`bizsvc:svc:{id}`)의 `monitored=true` 서비스 · 서비스 id 순 ≤ 상한.
- **APM**(주기 `NOISE_BIZ_MONITOR_APM_INTERVAL_SECONDS`): `apm_instance` 구성원을 소스별로 묶어
  `apm_app_health(targets=…, owner="biz_monitor", lookback_minutes=5)` 소스당 1호출(인스턴스 ≤
  상한 · 넘으면 서비스 순서대로 자르고 「일부만 감시」) · `apm_business` 구성원은 (소스, 도메인)마다
  `apm_business(mode=current, domain_id)` 1호출(업무 이름을 주지 않아야 도메인당 1호출이다 —
  응답에서 구성원 업무 id만 남긴다). 게이트웨이 신호(`was_signals`)와 값을 그대로 적는다(판정 로직
  신설 0 · 응답시간·TPS·에러율은 표시용 `apm_values`).
- **SMS**(확인 주기 `NOISE_BIZ_MONITOR_SMS_INTERVAL_SECONDS`): 존(db_id)별 감시 서버 ≤500개 묶음마다
  최신 정각 통계 확인 1건 → 직전과 같으면 지표 행 조회 생략. SQL은 `biz_service_sql`(고정 SELECT ·
  `sql_guard` · readonly MCP) · 동시 1(`sms_query_semaphore` — W8c 공유). 지표 원천이 비면(W0-8 전)
  SMS SELECT 0 + 사유 「SMS 지표 원천 미설정(W0-8)」.
- **시간 예산**: 주기마다 APM 20초 · SMS 60초. 호출마다 남은 예산을 상한으로 준다(게이트웨이에는
  `wait_seconds` = 남은 예산 − 여유 — 그 안에 못 끝낸 작업은 게이트웨이가 뒤에서 끝내고 여기는
  이번 주기를 건너뛴다 · 진행 중 제니퍼 조회를 끊지 않는다). 예산이 모자라 시작하지 못한 호출은
  「예산 초과 — 이번 주기 건너뜀」이고 백오프 상태를 건드리지 않는다. 호출 실패(시간 초과 포함 ·
  예외 전반)는 호출 단위로 잡아 연속 실패 수를 올리고 주기 ×2 백오프(최대 10분)한다 — 예산 종료가
  실패 기록을 지우지 않는다(docs/18 2026-11-03 plans/210 W9).
- **기록**: `bizmon:svc:{id}`(W6 `parse_monitor_value` 모양 · TTL 3×APM 주기 · 정각 통계 시각은
  서비스 단위 `sms_stat_time`(존별 최댓값) + 존별 `sms_stat_time_by_db` `{db_id: 시각}` — D1이
  존마다 창을 판정한다) · `bizmon:ts:{id}`
  (ZSET · 점수 = epoch 초 · 24시간 · ≤1,440점) · `bizmon:status`
  (`{state, last_success_at, reasons}`) · 상태가 바뀌면 `biz_service_state_log`
  (판정 `judge_service_state` · 사건은 M2 집계 스냅샷 재사용).

계층: application — domain 판정·키 + infrastructure 실행기·게이트웨이 호출기(주입).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from noise_gate.application.biz_monitor_alarms import (
    FACT_INCIDENT,
    METRICS_COLLECTING,
    METRICS_FAILED,
    METRICS_PARTIAL,
    MONITOR_STATUS_KEY,
    MONITOR_SVC_PREFIX,
    VIEWERS_KEY,
    AlarmSnapshot,
)
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain.biz_service_keys import (
    KIND_APM_BUSINESS,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    parse_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_state import (
    ApmSignalFact,
    ApmValueFact,
    IncidentFact,
    SmsMetricFact,
    judge_service_state,
)
from noise_gate.infrastructure.apm_host_map_sql_scanners import SqlExecutor, db_engine_of
from noise_gate.infrastructure.biz_service_sql import (
    SMS_IN_CHUNK,
    int_key,
    read_sms_latest_stat,
    read_sms_metric_rows,
    sms_query_semaphore,
)
from noise_gate.infrastructure.worker_lock import WorkerSingletonLock

logger = logging.getLogger(__name__)

#: 리더 잠금 키(API 인스턴스 중 한 곳만 수집).
LEADER_KEY = "bizmon:leader"
#: 24시간 시계열 키 접두(`bizmon:ts:{id}` — ZSET).
TS_PREFIX = "bizmon:ts:"
TS_MAX_POINTS = 1440
TS_RETENTION_SECONDS = 86400
#: 최신값·상태 TTL = 이 배수 × APM 주기.
VALUE_TTL_FACTOR = 3

#: 주기당 시간 예산(초).
APM_BUDGET_SECONDS = 20.0
SMS_BUDGET_SECONDS = 60.0
#: 남은 예산이 이보다 적으면 호출을 시작하지 않는다(이번 주기 건너뜀).
MIN_CALL_SECONDS = 3.0
#: 게이트웨이 `wait_seconds` = 남은 예산 − 이 여유(연결·응답 왕복).
WAIT_MARGIN_SECONDS = 2.0
#: 연속 실패 백오프 상한(초).
BACKOFF_MAX_SECONDS = 600.0
#: 주기 시각 오차 허용(초).
_TICK_SLACK = 1.0

#: 게이트웨이 호출자(D-356 ⑤ 공정 분배) · 창 5분 고정(긴 창 조회 없음).
APM_OWNER = "biz_monitor"
APM_LOOKBACK_MINUTES = 5
APM_HEALTH_TOOL = "apm_app_health"
APM_BUSINESS_TOOL = "apm_business"
#: 표시용 APM 값 칸(판정에 쓰지 않는다).
APM_VALUE_FIELDS = ("response_time_avg_ms", "tps", "active_services")
APM_ERROR_RATE = "error_rate"
BUSINESS_VALUE_FIELDS = ("tps", "response_time_avg_ms", "active_services")
_LIVE_JOB_STATES = frozenset({"queued", "running"})

#: SMS 최신 정각 통계 확인 창(시간) — 이보다 오래된 통계는 「최신 통계 없음」.
SMS_LATEST_LOOKBACK_HOURS = 48

REASON_SMS_UNSET = "SMS 지표 원천 미설정(W0-8)"
REASON_SMS_NO_EXECUTOR = "SMS 조회 실행기 없음 — SMS 수집 0"
REASON_GATEWAY_UNSET = "APM 게이트웨이 주소 미설정(MCP_SOURCE_ENDPOINTS의 apm) — APM 수집 0"
REASON_SNAPSHOT_FAILED = "알람 집계 실패 — 이번 주기 상태 전이 기록 생략"

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"
OUTCOME_BACKOFF = "backoff"
OUTCOME_BUDGET = "budget"

#: `run_once` 결과.
RUN_NOT_LEADER = "not_leader"
RUN_IDLE = "idle"
RUN_COLLECTED = "collected"
RUN_ERROR = "error"

AlarmSnapshotFn = Callable[[], Awaitable[AlarmSnapshot]]


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    return str(value if value is not None else "").strip()


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def _hhmm(epoch: float | None) -> str:
    """사유 문구용 시각(서버 현지 HH:MM · 없으면 「없음」)."""
    return datetime.fromtimestamp(epoch).strftime("%H:%M") if epoch is not None else "없음"


def _hour_key(epoch: float) -> str:
    """정각 통계 키 `YYYYMMDDHH`(서버 현지 시각)."""
    return time.strftime("%Y%m%d%H", time.localtime(epoch))


def _stat_label(hour: str) -> str:
    """`YYYYMMDDHH` → `YYYY-MM-DD HH:00`(최신 정각 통계 표기 — 실시간 아님)."""
    return f"{hour[0:4]}-{hour[4:6]}-{hour[6:8]} {hour[8:10]}:00"


def _error_text(env: Mapping[str, Any]) -> str | None:
    err = env.get("error")
    if not err:
        return None
    code = _text(err.get("code")) if isinstance(err, Mapping) else _text(err)
    reason = _text(env.get("reason"))[:160]
    return f"{code or 'gateway_error'}: {reason}" if reason else (code or "gateway_error")


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(i for i in items if i))


@dataclass
class _Backoff:
    """호출 단위(APM 소스·도메인 · SMS 존) 1개의 연속 실패 상태."""

    failures: int = 0
    next_at: float = 0.0  # 단조 시각
    last_success: float | None = None  # 벽시계(epoch)


@dataclass
class _SmsChunk:
    """존 하나 · 감시 서버 묶음 1개의 SMS 결과(다음 확인 주기까지 재사용)."""

    stat: str | None = None
    values: dict[str, dict[str, float | None]] = field(default_factory=dict)
    error: str | None = None  # 있으면 값 없음(조회 실패 · 백오프)
    note: str | None = None  # 있으면 값은 직전 것 + 사유(예산 초과)


@dataclass
class _Cycle:
    """주기 1회의 서비스별 결과와 상태 사유."""

    failures: dict[int, list[str]] = field(default_factory=dict)
    signals: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    values: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    sms: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    stat_time: dict[int, str] = field(default_factory=dict)
    stat_time_by_db: dict[int, dict[str, str]] = field(default_factory=dict)
    fresh: set[int] = field(default_factory=set)
    reasons: list[str] = field(default_factory=list)

    def fail(self, sid: int, text: str) -> None:
        bucket = self.failures.setdefault(sid, [])
        if text not in bucket:
            bucket.append(text)

    def reason(self, text: str) -> None:
        if text not in self.reasons:
            self.reasons.append(text)


def _members(svc: Mapping[str, Any], kind: str) -> list[Mapping[str, Any]]:
    rows = [m for m in svc.get("members") or () if isinstance(m, Mapping) and m.get("kind") == kind]
    return sorted(rows, key=lambda m: _text(m.get("member_key")))


def _max_by(rows: Iterable[tuple[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, raw in rows:
        value = _float(raw)
        if name and value is not None and (name not in out or value > out[name]):
            out[name] = value
    return out


class BizMonitorCollector:
    """M3 수집기 — `run_once`가 주기 1회, `start`가 백그라운드 태스크를 띄운다.

    Args:
        redis: `redis.asyncio` 클라이언트(업무 서비스 사본과 같은 연결)
        cache: `RedisBizServiceCache`(덕 타이핑 — `list_service_ids`·`get_service`)
        gateway: `GatewayCaller`(`call_tool(tool, args, *, timeout)`) — None이면 APM 수집 0 + 사유
        execute: SMS SQL 실행기(`registry_executor` — 실행 전 `SQLGuard` · readonly MCP)
        settings: 업무 서비스 설정(SMS 임계 · 지표 원천)
        repo: 상태 전이 기록 저장소(`add_state_log`) — None이면 기록 생략
        alarm_snapshot: M2 집계 스냅샷 함수(열린 사건) — None이면 사건 없이 판정
    """

    def __init__(
        self,
        *,
        redis: Any,
        cache: Any,
        gateway: Any,
        execute: SqlExecutor | None,
        settings: BizServiceSettings,
        repo: Any = None,
        alarm_snapshot: AlarmSnapshotFn | None = None,
        max_services: int = 20,
        max_apm_targets: int = 100,
        apm_interval: float = 60.0,
        sms_interval: float = 600.0,
        engine_of: Callable[[str], str] = db_engine_of,
        lock: WorkerSingletonLock | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        apm_budget: float = APM_BUDGET_SECONDS,
        sms_budget: float = SMS_BUDGET_SECONDS,
    ) -> None:
        """의존성을 받는다(시계·잠금은 테스트 seam)."""
        self._redis = redis
        self._cache = cache
        self._gateway = gateway
        self._execute = execute
        self._settings = settings
        self._repo = repo
        self._alarm_snapshot = alarm_snapshot
        self._max_services = max(1, int(max_services))
        self._max_apm_targets = max(1, int(max_apm_targets))
        self._apm_interval = float(apm_interval)
        self._sms_interval = float(sms_interval)
        self._engine_of = engine_of
        self._lock = lock or WorkerSingletonLock(redis, key=LEADER_KEY)
        self._monotonic = monotonic
        self._wall = wall
        self._apm_budget = float(apm_budget)
        self._sms_budget = float(sms_budget)
        self._backoff: dict[str, _Backoff] = {}
        self._apm_outcomes: dict[str, str] = {}
        self._sms_outcomes: dict[str, str] = {}
        self._sms_reasons: list[str] = []
        self._sms_chunks: dict[tuple[str, tuple[int, ...]], _SmsChunk] = {}
        self._next_sms = 0.0
        self._states: dict[int, str | None] = {}
        self._last_success: str | None = None
        self._last_success_loaded = False
        self._watch: asyncio.Task[str] | None = None
        self._task: asyncio.Task[None] | None = None

    # ── 수명 ────────────────────────────────────────────────────────────
    def start(self) -> asyncio.Task[None]:
        """백그라운드 태스크를 띄운다(이미 돌고 있으면 그 태스크)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run_forever(), name="biz-monitor-collector")
        return self._task

    async def run_forever(self) -> None:
        """APM 주기마다 `run_once` — 한 주기 실패는 로그 후 다음 주기에 다시 한다."""
        try:
            while True:
                started = self._monotonic()
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("업무 현황 M3 수집 주기 실패 — 다음 주기에 다시 한다")
                await asyncio.sleep(max(1.0, started + self._apm_interval - self._monotonic()))
        finally:
            await self._release()

    async def aclose(self) -> None:
        """태스크를 멈추고 리더 잠금을 놓는다(자기 값일 때만)."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (Exception, asyncio.CancelledError):
                pass
        await self._release()

    async def _release(self) -> None:
        watch, self._watch = self._watch, None
        if watch is not None:
            watch.cancel()
            try:
                await watch
            except (Exception, asyncio.CancelledError):
                pass
        await self._lock.release()

    async def _ensure_leader(self) -> bool:
        if self._lock.held:
            return True
        if self._watch is not None:
            if self._watch.done() and not self._watch.cancelled():
                logger.warning("업무 현황 M3 리더 잠금을 잃었다: %s", self._watch.result())
            self._watch.cancel()
            self._watch = None
        try:
            ok = await self._lock.acquire()
        except Exception as exc:
            logger.warning("업무 현황 M3 리더 잠금 획득 실패 — 이번 주기 건너뜀: %s", exc)
            return False
        if ok:
            logger.info("업무 현황 M3 리더 잠금 획득(%s)", self._lock.owner)
            self._watch = asyncio.create_task(self._lock.watch(), name="biz-monitor-leader")
        return ok

    # ── 주기 1회 ────────────────────────────────────────────────────────
    async def run_once(self) -> str:
        """주기 1회 — 리더 아님 · 보는 사람 없음이면 관측 호출 0."""
        if not await self._ensure_leader():
            return RUN_NOT_LEADER
        try:
            if not await self._redis.exists(VIEWERS_KEY):
                return RUN_IDLE
        except Exception as exc:
            logger.warning("업무 현황 M3 수요 키 조회 실패 — 이번 주기 건너뜀: %s", exc)
            return RUN_ERROR
        cyc = _Cycle()
        services = await self._monitored_services(cyc)
        await self._collect_apm(services, cyc)
        await self._collect_sms(services, cyc)
        values = await self._write_values(services, cyc)
        await self._judge(services, values, cyc)
        await self._write_status(cyc)
        return RUN_COLLECTED

    async def _monitored_services(self, cyc: _Cycle) -> list[tuple[int, Mapping[str, Any]]]:
        out: list[tuple[int, Mapping[str, Any]]] = []
        over = 0
        for sid in await self._cache.list_service_ids():
            svc = await self._cache.get_service(sid)
            if not isinstance(svc, Mapping) or svc.get("monitored") is not True:
                continue
            if len(out) >= self._max_services:
                over += 1
                continue
            out.append((int(sid), svc))
        if over:
            cyc.reason(f"감시 서비스 상한 {self._max_services} 초과 — {over}개 감시 제외")
        return out

    # ── 호출 단위 공통 ──────────────────────────────────────────────────
    def _failed(self, key: str, interval: float, now: float) -> None:
        bo = self._backoff.setdefault(key, _Backoff())
        bo.failures += 1
        delay = max(interval, min(interval * 2 ** bo.failures, BACKOFF_MAX_SECONDS))
        bo.next_at = now + delay

    def _succeeded(self, key: str) -> None:
        bo = self._backoff.setdefault(key, _Backoff())
        bo.failures, bo.next_at = 0, 0.0
        bo.last_success = self._wall()
        self._last_success = _iso(bo.last_success)

    def _backoff_note(self, key: str, label: str, now: float) -> str | None:
        """백오프 중이면 사유(다음 시도 · 마지막 성공), 아니면 None."""
        bo = self._backoff.get(key)
        if bo is None or now + _TICK_SLACK >= bo.next_at:
            return None
        nxt = self._wall() + (bo.next_at - now)
        return (f"{label} 조회 실패 · 연속 {bo.failures}회 — 백오프 중(다음 시도 {_hhmm(nxt)} ·"
                f" 마지막 성공 {_hhmm(bo.last_success)})")

    async def _gateway_call(
        self,
        key: str,
        label: str,
        tool: str,
        args: Mapping[str, Any],
        deadline: float,
        sids: Sequence[int],
        cyc: _Cycle,
    ) -> Mapping[str, Any] | None:
        """게이트웨이 1호출 — 성공 봉투 또는 None(사유는 `cyc`에 · 결과는 `_apm_outcomes`)."""
        now = self._monotonic()

        def skip(outcome: str, text: str) -> None:
            self._apm_outcomes[key] = outcome
            cyc.reason(text)
            for sid in sids:
                cyc.fail(sid, text)

        note = self._backoff_note(key, label, now)
        if note:
            skip(OUTCOME_BACKOFF, note)
            return None
        remaining = deadline - now
        if remaining < MIN_CALL_SECONDS:
            skip(OUTCOME_BUDGET, f"{label} — APM 시간 예산 {self._apm_budget:g}초 초과 · 이번 주기"
                                 " 건너뜀")
            return None
        call_args = {**dict(args), "owner": APM_OWNER,
                     "wait_seconds": round(max(1.0, remaining - WAIT_MARGIN_SECONDS), 1)}
        try:
            env = await self._gateway.call_tool(tool, call_args, timeout=remaining)
            if not isinstance(env, Mapping):
                raise TypeError("게이트웨이 응답이 객체가 아님")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 호출 단위 실패(시간 초과 포함)는 백오프로
            self._failed(key, self._apm_interval, now)
            skip(OUTCOME_FAILED, f"{label} 조회 실패 — {type(exc).__name__}: {_text(exc)[:160]}")
            return None
        err = _error_text(env)
        if err:
            self._failed(key, self._apm_interval, now)
            skip(OUTCOME_FAILED, f"{label} 조회 실패 — {err}")
            return None
        job = env.get("job")
        if isinstance(job, Mapping) and _text(job.get("state")) in _LIVE_JOB_STATES:
            skip(OUTCOME_BUDGET, f"{label} — 게이트웨이 작업이 APM 시간 예산 안에 끝나지 않음 ·"
                                 " 이번 주기 건너뜀")
            return None
        self._succeeded(key)
        self._apm_outcomes[key] = OUTCOME_OK
        total = env.get("total_row_count")
        rows = env.get("rows") or []
        if isinstance(total, int) and not isinstance(total, bool) and total > len(rows):
            cyc.reason(f"{label} — 결과 일부가 파일로 감(인라인 {len(rows)}/{total}행만 반영)")
        return env

    # ── APM ─────────────────────────────────────────────────────────────
    async def _collect_apm(self, services: Sequence[tuple[int, Mapping[str, Any]]],
                           cyc: _Cycle) -> None:
        self._apm_outcomes = {}
        health: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
        business: dict[tuple[str, str], list[tuple[int, str, str]]] = {}
        taken = 0
        cut: dict[int, int] = {}
        for sid, svc in services:
            for m in _members(svc, KIND_APM_INSTANCE):
                key = _text(m.get("member_key"))
                parts = parse_member_key(KIND_APM_INSTANCE, key)
                source = _text(m.get("source_id")) or (parts[0] if parts else "")
                if not source or not _text(m.get("instance_name")):
                    cyc.fail(sid, f"APM 인스턴스 식별 불가 — {key}")
                    continue
                if taken >= self._max_apm_targets:
                    cut[sid] = cut.get(sid, 0) + 1
                    continue
                health.setdefault(source, []).append((sid, m))
                taken += 1
            for m in _members(svc, KIND_APM_BUSINESS):
                key = _text(m.get("member_key"))
                bparts = parse_member_key(KIND_APM_BUSINESS, key)
                if bparts is None:
                    cyc.fail(sid, f"제니퍼 업무 식별 불가 — {key}")
                    continue
                business.setdefault((bparts[0], bparts[1]), []).append((sid, key, bparts[2]))
        if cut:
            cyc.reason(f"일부만 감시 — APM 인스턴스 상한 {self._max_apm_targets} 초과"
                       f"(제외 {sum(cut.values())}개)")
            for sid, n in cut.items():
                cyc.fail(sid, f"일부만 감시 — APM 인스턴스 상한 {self._max_apm_targets} 초과로"
                              f" {n}개 제외")
        if not health and not business:
            return
        if self._gateway is None:
            self._apm_outcomes["apm"] = OUTCOME_FAILED
            cyc.reason(REASON_GATEWAY_UNSET)
            for sid in {s for items in health.values() for s, _ in items} | {
                    s for items in business.values() for s, _, _ in items}:
                cyc.fail(sid, REASON_GATEWAY_UNSET)
            return
        deadline = self._monotonic() + self._apm_budget
        await asyncio.gather(
            *(self._health_unit(source, items, deadline, cyc)
              for source, items in sorted(health.items())),
            *(self._business_unit(source, domain, items, deadline, cyc)
              for (source, domain), items in sorted(business.items())),
        )

    async def _health_unit(self, source: str, items: Sequence[tuple[int, Mapping[str, Any]]],
                           deadline: float, cyc: _Cycle) -> None:
        targets = [{"instance_name": _text(m.get("instance_name")), "source_id": source}
                   for _, m in items]
        env = await self._gateway_call(
            f"apm:{source}", f"APM {source}", APM_HEALTH_TOOL,
            {"targets": targets, "lookback_minutes": APM_LOOKBACK_MINUTES}, deadline,
            sorted({sid for sid, _ in items}), cyc,
        )
        if env is None:
            return
        rows = [r for r in env.get("rows") or () if isinstance(r, Mapping)]
        batch: dict[int, Mapping[str, Any]] = {}
        for entry in env.get("batch") or ():
            if isinstance(entry, Mapping) and isinstance(entry.get("index"), int):
                batch[int(entry["index"])] = entry
        for index, (sid, m) in enumerate(items):
            name, domain = _text(m.get("instance_name")), _text(m.get("domain_id"))
            member_key = _text(m.get("member_key"))
            entry = batch.get(index)
            if entry is None or _text(entry.get("status")) == "error":
                code = _text(entry.get("error")) if entry is not None else "결과 없음"
                cyc.fail(sid, f"APM 조회 실패 — {name}({code or 'error'})")
                continue
            matched = [r for r in rows
                       if r.get("target_index") == index and _text(r.get("domain_id")) == domain]
            if not matched:
                cyc.fail(sid, f"APM 인스턴스 미발견 — {name}(도메인 {domain})")
                continue
            cyc.fresh.add(sid)
            ids = {_text(r.get("instance_id")) for r in matched}
            values = cyc.values.setdefault(sid, [])
            for r in matched:
                for field_name in APM_VALUE_FIELDS:
                    values.append({"member_key": member_key, "name": field_name,
                                   "value": _float(r.get(field_name))})
                window = r.get("window")
                if isinstance(window, Mapping):
                    values.append({"member_key": member_key, "name": APM_ERROR_RATE,
                                   "value": _float(window.get(APM_ERROR_RATE))})
            for sig in entry.get("was_signals") or ():
                if isinstance(sig, Mapping) and _text(sig.get("instance_id")) in ids:
                    cyc.signals.setdefault(sid, []).append({
                        "member_key": member_key, "signal": _text(sig.get("kind")),
                        "level": _text(sig.get("level")),
                    })

    async def _business_unit(self, source: str, domain: str,
                             items: Sequence[tuple[int, str, str]], deadline: float,
                             cyc: _Cycle) -> None:
        label = f"제니퍼 업무 {source}·{domain}"
        sids = sorted({sid for sid, _, _ in items})
        try:
            domain_id = int(domain)
        except ValueError:
            for sid in sids:
                cyc.fail(sid, f"{label} — 도메인 id가 정수가 아님")
            return
        env = await self._gateway_call(
            f"apmbiz:{source}|{domain}", label, APM_BUSINESS_TOOL,
            {"mode": "current", "domain_id": domain_id, "source_ids": [source]}, deadline, sids,
            cyc,
        )
        if env is None:
            return
        by_id: dict[str, Mapping[str, Any]] = {}
        for r in env.get("rows") or ():
            if isinstance(r, Mapping) and _text(r.get("domain_id")) == domain:
                by_id.setdefault(_text(r.get("business_id")), r)
        for sid, member_key, business_id in items:
            row = by_id.get(business_id)
            if row is None:
                cyc.fail(sid, f"{label} — 업무 {business_id} 현재값 없음")
                continue
            cyc.fresh.add(sid)
            cyc.values.setdefault(sid, []).extend(
                {"member_key": member_key, "name": n, "value": _float(row.get(n))}
                for n in BUSINESS_VALUE_FIELDS
            )

    # ── SMS ─────────────────────────────────────────────────────────────
    async def _collect_sms(self, services: Sequence[tuple[int, Mapping[str, Any]]],
                           cyc: _Cycle) -> None:
        servers: dict[str, dict[int, list[tuple[int, str]]]] = {}
        for sid, svc in services:
            for m in _members(svc, KIND_SMS_SERVER):
                db_id = _text(m.get("db_id"))
                try:
                    rid = int_key(m.get("resource_id"))
                except Exception:  # noqa: BLE001 — 규칙 밖 id는 그 구성원만 사유
                    cyc.fail(sid, f"SMS 서버 식별 불가 — {_text(m.get('member_key'))}")
                    continue
                key = sms_member_key(db_id, rid) if db_id else None
                if key is None:
                    cyc.fail(sid, f"SMS 서버 식별 불가 — {_text(m.get('member_key'))}")
                    continue
                servers.setdefault(db_id, {}).setdefault(rid, []).append((sid, key))
        sources = dict(self._settings.sms_metric_source)
        if not servers or not sources or self._execute is None:
            self._sms_outcomes, self._sms_reasons, self._sms_chunks = {}, [], {}
            if servers:
                why = REASON_SMS_UNSET if not sources else REASON_SMS_NO_EXECUTOR
                cyc.reason(why)
                for by_rid in servers.values():
                    for pairs in by_rid.values():
                        for sid, _ in pairs:
                            cyc.fail(sid, why)
            return
        unset = [m for m in ("cpu", "memory", "filesystem") if m not in sources]
        if unset:
            cyc.reason(f"{REASON_SMS_UNSET} — {'·'.join(unset)}")
        now = self._monotonic()
        if now + _TICK_SLACK >= self._next_sms:
            self._next_sms = now + self._sms_interval
            await self._run_sms(servers, sources)
        for text in self._sms_reasons:
            cyc.reason(text)
        self._assemble_sms(servers, sources, cyc)

    @staticmethod
    def _chunks(ids: Iterable[int]) -> list[tuple[int, ...]]:
        ordered = sorted(ids)
        return [tuple(ordered[i:i + SMS_IN_CHUNK]) for i in range(0, len(ordered), SMS_IN_CHUNK)]

    async def _sms_query(self, fn: Callable[[], Awaitable[Any]], remaining: float) -> Any:
        async def _locked() -> Any:
            async with sms_query_semaphore():
                return await fn()

        return await asyncio.wait_for(_locked(), timeout=remaining)

    async def _run_sms(self, servers: Mapping[str, Mapping[int, Any]],
                       sources: Mapping[str, tuple[str, str]]) -> None:
        """SMS 확인 주기 1회 — 존 · 묶음마다 확인 1건(+ 바뀌었을 때만 지표 행 1건)."""
        assert self._execute is not None
        execute = self._execute
        deadline = self._monotonic() + self._sms_budget
        since = _hour_key(self._wall() - SMS_LATEST_LOOKBACK_HOURS * 3600)
        chunks: dict[tuple[str, tuple[int, ...]], _SmsChunk] = {}
        self._sms_outcomes, self._sms_reasons = {}, []
        for db_id in sorted(servers):
            key, label = f"sms:{db_id}", f"SMS {db_id}"
            engine = self._engine_of(db_id)
            for ids in self._chunks(servers[db_id]):
                ck = (db_id, ids)
                prev = self._sms_chunks.get(ck)
                now = self._monotonic()
                note = self._backoff_note(key, label, now)
                if note:
                    self._sms_outcomes[key] = OUTCOME_BACKOFF
                    self._sms_reasons.append(note)
                    chunks[ck] = _SmsChunk(error=note)
                    continue
                budget_note = (f"{label} — SMS 시간 예산 {self._sms_budget:g}초 초과 · 이번 주기"
                               " 건너뜀")
                if deadline - now < MIN_CALL_SECONDS:
                    self._sms_outcomes.setdefault(key, OUTCOME_BUDGET)
                    self._sms_reasons.append(budget_note)
                    chunks[ck] = self._kept(prev, budget_note)
                    continue
                try:
                    latest = await self._sms_query(
                        lambda: read_sms_latest_stat(execute, db_id, engine, ids, sources,
                                                     since_hour=since),
                        deadline - now)
                    if latest is not None and prev is not None and prev.error is None \
                            and prev.note is None and prev.stat == latest:
                        chunk = prev  # 같은 정각 통계 — 지표 행 조회 생략
                    elif latest is None:
                        chunk = _SmsChunk()
                    else:
                        remaining = deadline - self._monotonic()
                        if remaining < MIN_CALL_SECONDS:
                            self._sms_outcomes.setdefault(key, OUTCOME_BUDGET)
                            self._sms_reasons.append(budget_note)
                            chunks[ck] = self._kept(prev, budget_note)
                            continue
                        rows = await self._sms_query(
                            lambda: read_sms_metric_rows(execute, db_id, engine, ids, sources,
                                                         stat_hour_key=latest),
                            remaining)
                        values: dict[str, dict[str, float | None]] = {}
                        for row in rows:
                            values.setdefault(row.server_id, {})[row.metric] = row.value
                        chunk = _SmsChunk(stat=latest, values=values)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — 존 단위 실패(시간 초과 포함)는 백오프로
                    self._failed(key, self._sms_interval, now)
                    text = f"{label} 조회 실패 — {type(exc).__name__}: {_text(exc)[:160]}"
                    self._sms_outcomes[key] = OUTCOME_FAILED
                    self._sms_reasons.append(text)
                    chunks[ck] = _SmsChunk(error=text)
                    continue
                self._succeeded(key)
                self._sms_outcomes.setdefault(key, OUTCOME_OK)
                chunks[ck] = chunk
        self._sms_reasons = _dedupe(self._sms_reasons)
        self._sms_chunks = chunks

    @staticmethod
    def _kept(prev: _SmsChunk | None, note: str) -> _SmsChunk:
        if prev is None or prev.error is not None:
            return _SmsChunk(error=note)
        return _SmsChunk(stat=prev.stat, values=prev.values, note=note)

    def _assemble_sms(self, servers: Mapping[str, Mapping[int, list[tuple[int, str]]]],
                      sources: Mapping[str, tuple[str, str]], cyc: _Cycle) -> None:
        metrics = sorted(sources)
        for db_id in sorted(servers):
            for ids in self._chunks(servers[db_id]):
                chunk = self._sms_chunks.get((db_id, ids))
                for rid in ids:
                    for sid, member_key in servers[db_id][rid]:
                        if chunk is None:
                            cyc.fail(sid, f"SMS {db_id} — 다음 확인 주기 대기(구성원 바뀜)")
                            continue
                        if chunk.error is not None:
                            cyc.fail(sid, chunk.error)
                            continue
                        if chunk.note is not None:
                            cyc.fail(sid, chunk.note)
                        if chunk.stat is None:
                            cyc.fail(sid, f"SMS {db_id} — 최신 정각 통계 없음(최근"
                                          f" {SMS_LATEST_LOOKBACK_HOURS}시간)")
                            continue
                        got = chunk.values.get(str(rid), {})
                        cyc.sms.setdefault(sid, []).extend(
                            {"member_key": member_key, "metric": metric, "value": got.get(metric)}
                            for metric in metrics
                        )
                        if got:
                            cyc.fresh.add(sid)
                        label = _stat_label(chunk.stat)
                        if label > cyc.stat_time.get(sid, ""):
                            cyc.stat_time[sid] = label
                        by_db = cyc.stat_time_by_db.setdefault(sid, {})
                        if label > by_db.get(db_id, ""):
                            by_db[db_id] = label

    # ── 기록 ────────────────────────────────────────────────────────────
    async def _write_values(self, services: Sequence[tuple[int, Mapping[str, Any]]],
                            cyc: _Cycle) -> dict[int, dict[str, Any]]:
        ttl = int(VALUE_TTL_FACTOR * self._apm_interval)
        now = _iso(self._wall())
        out: dict[int, dict[str, Any]] = {}
        metric_kinds = (KIND_APM_INSTANCE, KIND_APM_BUSINESS, KIND_SMS_SERVER)
        for sid, svc in services:
            has_metric = any(_members(svc, k) for k in metric_kinds)
            value = {
                "updated_at": now,
                "in_window": sid in cyc.fresh or not has_metric,
                "fetch_failures": list(cyc.failures.get(sid, [])),
                "sms_stat_time": cyc.stat_time.get(sid),
                "sms_stat_time_by_db": dict(sorted(cyc.stat_time_by_db.get(sid, {}).items())),
                "apm_signals": list(cyc.signals.get(sid, [])),
                "apm_values": list(cyc.values.get(sid, [])),
                "sms_metrics": list(cyc.sms.get(sid, [])),
            }
            out[sid] = value
            try:
                await self._redis.set(f"{MONITOR_SVC_PREFIX}{sid}",
                                      json.dumps(value, ensure_ascii=False), ex=ttl)
            except Exception as exc:  # noqa: BLE001 — 기록 실패는 그 서비스만(보드는 「결과 없음」)
                logger.warning("업무 현황 M3 최신값 기록 실패: service=%s %s", sid, exc)
        return out

    async def _incidents(self, cyc: _Cycle) -> tuple[bool, dict[int, list[IncidentFact]]]:
        if self._alarm_snapshot is None:
            return True, {}
        try:
            snap = await self._alarm_snapshot()
        except Exception as exc:  # noqa: BLE001 — 사건 미상이면 전이를 적지 않는다(오기록 방지)
            logger.warning("업무 현황 M3 — 알람 집계 실패: %s", exc)
            cyc.reason(REASON_SNAPSHOT_FAILED)
            return False, {}
        out: dict[int, list[IncidentFact]] = {}
        for f in snap.facts:
            if f.kind == FACT_INCIDENT and f.service_id is not None:
                out.setdefault(int(f.service_id), []).append(
                    IncidentFact(f.member_key, f.severity, incident_id=f.incident_id))
        return True, out

    async def _judge(self, services: Sequence[tuple[int, Mapping[str, Any]]],
                     values: Mapping[int, Mapping[str, Any]], cyc: _Cycle) -> None:
        known, incidents = await self._incidents(cyc)
        thresholds = self._settings.config.sms_thresholds
        wall = self._wall()
        for sid, _svc in services:
            v = values[sid]
            state: str | None = None
            if known:
                j = judge_service_state(
                    monitored=True,
                    incidents=incidents.get(sid, ()),
                    apm_signals=[ApmSignalFact(s["member_key"], s["signal"], s["level"])
                                 for s in v["apm_signals"]],
                    sms_metrics=[SmsMetricFact(s["member_key"], s["metric"], s["value"])
                                 for s in v["sms_metrics"]],
                    apm_values=[ApmValueFact(s["member_key"], s["name"], s["value"])
                                for s in v["apm_values"]],
                    thresholds=thresholds,
                    metrics_in_window=bool(v["in_window"]),
                    fetch_failures=v["fetch_failures"],
                )
                state = j.state
                await self._transition(sid, state, {
                    "label": j.label,
                    "reasons": list(j.reasons)[:10],
                    "evidence": [
                        {"member_key": e.member_key, "rule": e.rule, "metric": e.metric,
                         "value": e.value, "threshold": e.threshold}
                        for e in j.evidence[:10]
                    ],
                })
            await self._append_ts(sid, wall, state, v)

    async def _transition(self, sid: int, state: str, reason: Mapping[str, Any]) -> None:
        if sid in self._states:
            prev = self._states[sid]
        else:
            prev = await self._last_ts_state(sid)
        self._states[sid] = state
        if prev == state or self._repo is None:
            return
        try:
            await self._repo.add_state_log(sid, from_state=prev, to_state=state,
                                           reason=dict(reason))
        except Exception as exc:  # noqa: BLE001 — 전이 기록 실패는 로그(다음 전이는 계속 적는다)
            logger.warning("업무 서비스 상태 전이 기록 실패: service=%s %s→%s %s",
                           sid, prev, state, exc)

    async def _last_ts_state(self, sid: int) -> str | None:
        """프로세스가 처음 보는 서비스의 직전 상태 — 시계열 마지막 점(없으면 None)."""
        try:
            raw = await self._redis.zrange(f"{TS_PREFIX}{sid}", -1, -1)
        except Exception:  # noqa: BLE001 — 모르면 None(전이 1건이 「처음」으로 적힌다)
            return None
        for item in raw or ():
            try:
                point = json.loads(_text(item))
            except ValueError:
                return None
            state = point.get("state") if isinstance(point, Mapping) else None
            return _text(state) or None
        return None

    async def _append_ts(self, sid: int, wall: float, state: str | None,
                         v: Mapping[str, Any]) -> None:
        point = {
            "at": _iso(wall),
            "state": state,
            "sms_max": _max_by((s["metric"], s["value"]) for s in v["sms_metrics"]),
            "apm_max": _max_by((s["name"], s["value"]) for s in v["apm_values"]),
        }
        key = f"{TS_PREFIX}{sid}"
        try:
            await self._redis.zadd(key, {json.dumps(point, ensure_ascii=False): wall})
            await self._redis.zremrangebyscore(key, "-inf", wall - TS_RETENTION_SECONDS)
            await self._redis.zremrangebyrank(key, 0, -(TS_MAX_POINTS + 1))
            await self._redis.expire(key, TS_RETENTION_SECONDS)
        except Exception as exc:  # noqa: BLE001 — 시계열 실패는 로그(최신값은 이미 적었다)
            logger.warning("업무 현황 M3 시계열 기록 실패: service=%s %s", sid, exc)

    async def _write_status(self, cyc: _Cycle) -> None:
        outcomes = [*self._apm_outcomes.values(), *self._sms_outcomes.values()]
        ok = OUTCOME_OK in outcomes
        bad = any(o in (OUTCOME_FAILED, OUTCOME_BACKOFF) for o in outcomes)
        if bad and not ok:
            state = METRICS_FAILED
        elif bad or OUTCOME_BUDGET in outcomes or cyc.reasons:
            state = METRICS_PARTIAL
        else:
            state = METRICS_COLLECTING
        if self._last_success is None and not self._last_success_loaded:
            self._last_success_loaded = True
            self._last_success = await self._previous_success()
        value = {"state": state, "last_success_at": self._last_success,
                 "reasons": cyc.reasons[:20], "updated_at": _iso(self._wall())}
        try:
            await self._redis.set(MONITOR_STATUS_KEY, json.dumps(value, ensure_ascii=False),
                                  ex=int(VALUE_TTL_FACTOR * self._apm_interval))
        except Exception as exc:  # noqa: BLE001 — 상태 기록 실패는 로그(보드는 「상태 없음」)
            logger.warning("업무 현황 M3 수집 상태 기록 실패: %s", exc)

    async def _previous_success(self) -> str | None:
        """리더가 바뀐 직후 — 앞 리더가 남긴 마지막 성공 시각을 잇는다."""
        try:
            raw = await self._redis.get(MONITOR_STATUS_KEY)
            value = json.loads(_text(raw)) if raw else None
        except Exception:  # noqa: BLE001 — 모르면 없음
            return None
        return (_text(value.get("last_success_at")) or None) if isinstance(value, Mapping) else None


def _alarm_snapshot_fn(config: Any, cache: Any, incident_store: Any,
                       settings: BizServiceSettings) -> AlarmSnapshotFn:
    """M2 집계기(W6 `BizAlarmAggregator`) 1개 — 보드 라우트와 같은 재료로 만든다."""
    from noise_gate.application.biz_monitor_alarms import BizAlarmAggregator
    from noise_gate.infrastructure.decision_store import DecisionStore

    ng = config.noise_gate
    path = getattr(ng, "decision_store_path", None)
    decisions = DecisionStore(
        path,
        bool(getattr(ng, "decision_store_enabled", True)),
        int(getattr(ng, "decision_store_max_lines", 20000)),
    ) if path else None
    agg = BizAlarmAggregator(
        cache=cache, incident_store=incident_store, decision_store=decisions,
        window_minutes=int(getattr(ng, "biz_monitor_alarm_window_minutes", 60)),
        settings_loader=lambda: settings,
    )
    return agg.snapshot


def start_biz_monitor_collector(
    config: Any, *, cache: Any, repo: Any = None, incident_store: Any = None
) -> BizMonitorCollector | None:
    """기동 — `biz_monitor_metrics_active`가 정확히 True이고 사본 Redis가 있을 때만 태스크를 띄운다.

    꺼져 있으면 None(태스크 0 · 관측 호출 0). 게이트웨이 주소가 없으면 APM은 사유만 남기고 돈다.
    실행 중 이벤트 루프 안에서 부른다(lifespan).
    """
    ng: Any = getattr(config, "noise_gate", None)
    if getattr(ng, "biz_monitor_metrics_active", False) is not True:
        return None
    redis = getattr(cache, "redis", None)
    if redis is None:
        logger.warning("업무 현황 M3 수집기 비활성 — 업무 서비스 사본 Redis가 없다")
        return None
    from noise_gate.application.biz_service_config import load_biz_service_config
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_scanners import GatewayToolCaller
    from noise_gate.infrastructure.apm_host_map_sql_scanners import registry_executor

    settings = load_biz_service_config()
    client = build_apm_gateway_client(config)
    gateway = GatewayToolCaller(client, timeout=APM_BUDGET_SECONDS) if client is not None else None
    collector = BizMonitorCollector(
        redis=redis, cache=cache, gateway=gateway, execute=registry_executor(config),
        settings=settings, repo=repo,
        alarm_snapshot=_alarm_snapshot_fn(config, cache, incident_store, settings),
        max_services=int(ng.biz_monitor_max_services),
        max_apm_targets=int(ng.biz_monitor_max_apm_targets),
        apm_interval=float(ng.biz_monitor_apm_interval_seconds),
        sms_interval=float(ng.biz_monitor_sms_interval_seconds),
    )
    collector.start()
    logger.info("업무 현황 M3 수집기 기동 (APM %ss · SMS %ss · 게이트웨이 %s)",
                ng.biz_monitor_apm_interval_seconds, ng.biz_monitor_sms_interval_seconds,
                "있음" if gateway is not None else "없음")
    return collector


__all__ = [
    "APM_BUDGET_SECONDS",
    "APM_OWNER",
    "LEADER_KEY",
    "REASON_SMS_UNSET",
    "SMS_BUDGET_SECONDS",
    "TS_MAX_POINTS",
    "TS_PREFIX",
    "BizMonitorCollector",
    "start_biz_monitor_collector",
]
