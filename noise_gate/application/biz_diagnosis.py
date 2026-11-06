"""업무 서비스 D1 진단 스냅샷 조립 (plans/215 §4.6 · §4.7 · D-368).

보드 상세가 진단을 열 때(API 요청 시) 계산한다 — LLM 0 · 통보 0. 판독은 domain
`diagnose_service`(R1~R6 · 최초 이상 순 · 대표 · 결손)가 하고, 여기서는 입력을 모으고 존 필터를
건다.

- **입력**: 서비스 사본 + M2 귀속 사실(`BizAlarmAggregator.snapshot()` — 조회 추가 0) · 관측값 ·
  210 연계 사본(`read_link_records` — 소스당 HMGET 1회 · 구성 맵과 같은 경로) · 구성원 알람의 최근
  조사(`InvestigationStore.get_by_alarm` — 저장된 원인 영역을 보이기만 · D-352 ②).
- **관측값**: M3이 켜져 있고 그 서비스가 감시 대상이며 `bizmon:svc:{id}`가 있으면 그 값을 쓴다(관측
  호출 0). 아니면 **즉석 1회** — APM `apm_app_health(targets ≤ 상한, owner="biz_diagnosis")` 소스당
  1호출 + SMS 존(db_id)별 고정 SELECT(M3과 같은 빌더 · 최신 정각 확인 1건 + 지표 행 1건 · 공용
  `sms_query_semaphore`). SMS 지표 원천이 비면(W0-8 전) SMS SELECT 0 + 사유.
- **정각 통계 시각**: 존(db_id)마다 최신 정각을 구성원별로 실어 창 판정을 구성원별로 한다(F12) —
  즉석 조회는 존 묶음의 정각 · M3 최신값은 `sms_stat_time_by_db`. 그 칸이 없는 옛 형태 M3 값이면
  종전처럼 서비스 단위 시각(존별 최댓값)을 모든 구성원에 쓰고 결손 「서비스 단위 통계 시각」을
  남긴다.
- **상한**: 서비스당 결과 메모 60초(여럿이 열어도 1회 — 서비스마다 한 번만 계산) · 즉석 조회는 전역
  동시 1 · 예산 20초 — 넘으면 「일부만」 + 사유(침묵 폴백 금지).
- **존 필터**: 메모에는 존 필터 전 입력을 두고 요청마다 판독한다 — 판독은 요청자 존 안 구성원으로만
  하고, 존 밖 구성원은 이름·값 없이 결손의 건수로만 남는다.
- **D2 메모**: 존마다 그 존 구성원만으로 판독한 결과를 `bizdiag:d1:{id}`(`D1_MEMO_PREFIX` · TTL =
  메모 60초)에 `{by_zone: {존: {verdict, anomalous_members[{kind, member_key}]}}, computed_at}`로
  적는다(같은 입력으로 domain 판독만 다시 돌린다 — 관측 추가 0 · W10 `attach_biz_service`가 알람 존
  항목만 읽는다).

플래그(`biz_diagnosis_active`)가 꺼져 있으면 `build_biz_diagnoser`가 None을 돌려준다(클라이언트·
실행기 생성 0) — 라우트는 그때 404를 낸다.

계층: application — domain 판독 + infrastructure SQL 빌더·사본 키(덕 타이핑 주입).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from noise_gate.application.biz_investigation_context import D1_MEMO_PREFIX, member_zone
from noise_gate.application.biz_monitor_alarms import (
    FACT_INCIDENT,
    MONITOR_SVC_PREFIX,
    AlarmFact,
    MonitorValue,
    parse_monitor_value,
    zone_visible,
)
from noise_gate.application.biz_monitor_collector import (
    APM_HEALTH_TOOL,
    APM_LOOKBACK_MINUTES,
    MIN_CALL_SECONDS,
    REASON_SMS_UNSET,
    SMS_LATEST_LOOKBACK_HOURS,
    WAIT_MARGIN_SECONDS,
)
from noise_gate.application.biz_monitor_overview import read_link_records
from noise_gate.domain.apm_host_map import KIND_SMS
from noise_gate.domain.biz_diagnosis import (
    DiagAlarm,
    DiagLink,
    DiagMember,
    DiagMetrics,
    diagnose_service,
    iso_of,
)
from noise_gate.domain.biz_service_keys import KIND_APM_INSTANCE, KIND_SMS_SERVER, sms_member_key
from noise_gate.domain.biz_service_state import ApmSignalFact, SmsMetricFact
from noise_gate.infrastructure.apm_host_map_sql_scanners import SqlExecutor, db_engine_of
from noise_gate.infrastructure.biz_service_sql import (
    SMS_IN_CHUNK,
    int_key,
    read_sms_latest_stat,
    read_sms_metric_rows,
    sms_query_semaphore,
)

logger = logging.getLogger(__name__)

#: 게이트웨이 호출자(D-356 ⑤ 공정 분배 — M3 `biz_monitor`와 분리).
DIAG_OWNER = "biz_diagnosis"
#: 서비스당 결과 메모(초) — D2 메모 TTL도 같다.
MEMO_TTL_SECONDS = 60.0
#: 즉석 조회 예산(초).
BUDGET_SECONDS = 20.0
#: 조사 기록 조회 상한(알람 수).
INVESTIGATION_LOOKUP_LIMIT = 50

SOURCE_M3 = "m3"
SOURCE_ADHOC = "adhoc"
SOURCE_NONE = "none"

PARTIAL_PREFIX = "일부만 — "
REASON_GATEWAY_UNSET = "APM 게이트웨이 주소 미설정 — 즉석 APM 조회 0"
REASON_SMS_NO_EXECUTOR = "SMS 조회 실행기 없음 — 즉석 SMS 조회 0"
REASON_BUSY = "다른 서비스의 즉석 조회가 예산 안에 끝나지 않음 — 관측값 없이 판독"
REASON_M3_MISSING = "M3 최신값 없음 — 즉석 조회 1회"
REASON_M3_STALE = "M3 — 지표가 수집 주기 안에 들어오지 않음"
REASON_INVESTIGATION_FAILED = "조사 기록 읽기 실패 — 구성원 최근 조사 생략"


def _text(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", errors="replace")
    return "" if value is None else str(value).strip()


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(raw: Any) -> float | None:
    """ISO 8601 → epoch 초(시간대 없으면 UTC · 실패는 None)."""
    value = _text(raw)
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _local_epoch(raw: str, fmt: str) -> float | None:
    """서버 현지 시각 문자열 → epoch(정각 통계 키·표기 — M3과 같은 현지 시각)."""
    try:
        return time.mktime(time.strptime(raw, fmt))
    except (TypeError, ValueError, OverflowError):
        return None


def _stat_label(epoch: float | None) -> str:
    return "" if epoch is None else f"{time.localtime(epoch).tm_hour:02d}시 통계"


def _hour_key(epoch: float) -> str:
    return time.strftime("%Y%m%d%H", time.localtime(epoch))


def _error_text(env: Mapping[str, Any]) -> str | None:
    err = env.get("error")
    if not err:
        return None
    code = _text(err.get("code")) if isinstance(err, Mapping) else _text(err)
    reason = _text(env.get("reason"))[:160]
    return f"{code or 'gateway_error'}: {reason}" if reason else (code or "gateway_error")


def _member_name(m: Mapping[str, Any]) -> str:
    for key in ("server_name", "hostname", "instance_name", "domain_name", "member_key"):
        if _text(m.get(key)):
            return _text(m.get(key))
    return ""


def _members(svc: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = [m for m in svc.get("members") or () if isinstance(m, Mapping)]
    return sorted(rows, key=lambda m: (_text(m.get("kind")), _text(m.get("member_key"))))


def diagnosis_active(config: Any) -> bool:
    """D1 플래그 유효 여부 — 정확히 True일 때만(`MagicMock` 설정 오판 방지)."""
    ng = getattr(config, "noise_gate", None)
    return getattr(ng, "biz_diagnosis_active", False) is True


@dataclass
class _Observed:
    """관측값 1회분 + 출처·호출 수·사유."""

    metrics: DiagMetrics = field(default_factory=DiagMetrics)
    source: str = SOURCE_NONE
    at: float | None = None
    calls: dict[str, int] = field(default_factory=lambda: {"apm": 0, "sms": 0})
    notices: list[str] = field(default_factory=list)
    partial: bool = False

    def note(self, text: str, *, partial: bool = False) -> None:
        line = f"{PARTIAL_PREFIX}{text}" if partial else text
        if line not in self.notices:
            self.notices.append(line)
        self.partial = self.partial or partial


@dataclass(frozen=True)
class _Inputs:
    """서비스 1개의 진단 입력(존 필터 전 · 메모 단위)."""

    computed_at: float
    m2_at: float
    service: Mapping[str, Any]
    facts: tuple[AlarmFact, ...]
    observed: _Observed
    links: Mapping[str, DiagLink]
    investigations: Mapping[str, Mapping[str, Any]]
    notices: tuple[str, ...]


class BizDiagnoser:
    """D1 진단 조립기 — 프로세스 안 1개(라우트가 `app.state`에 둔다).

    Args:
        aggregator: M2 집계기(`BizAlarmAggregator` — `snapshot()`)
        redis: 업무 현황 Redis(`bizmon:svc:{id}` 읽기 · D2 메모 쓰기) — None이면 둘 다 생략
        gateway: `GatewayToolCaller`(`call_tool(tool, args, *, timeout)`)
            — None이면 즉석 APM 0 + 사유
        execute: SMS SQL 실행기(`registry_executor` — 실행 전 `SQLGuard` · readonly MCP)
        settings_loader: 업무 서비스 설정(`load_biz_service_config` — SMS 임계·지표 원천)
        map_cache: 210 연계 사본(`app.state.apm_host_map_cache` · `.redis`) — None이면 연계 없음
        investigation_store: 조사 저장소(`get_by_alarm`) — None이면 최근 조사 생략
        metrics_active: M3 수집 유효 여부(`biz_monitor_metrics_active`)
    """

    def __init__(
        self,
        *,
        aggregator: Any,
        redis: Any,
        gateway: Any,
        execute: SqlExecutor | None,
        settings_loader: Callable[[], Any],
        map_cache: Any = None,
        investigation_store: Any = None,
        metrics_active: bool = False,
        window_minutes: int = 15,
        max_apm_targets: int = 50,
        engine_of: Callable[[str], str] = db_engine_of,
        budget_seconds: float = BUDGET_SECONDS,
        memo_ttl: float = MEMO_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        """의존성을 받는다(시계는 테스트 seam)."""
        self._agg = aggregator
        self._redis = redis
        self._gateway = gateway
        self._execute = execute
        self._settings_loader = settings_loader
        self._settings: Any = None
        self._map_cache = map_cache
        self._investigations = investigation_store
        self._metrics_active = metrics_active is True
        self.window_minutes = max(1, int(window_minutes))
        self._max_apm = max(1, int(max_apm_targets))
        self._engine_of = engine_of
        self._budget = float(budget_seconds)
        self._ttl = float(memo_ttl)
        self._monotonic = monotonic
        self._wall = wall
        self._memo: dict[int, tuple[float, _Inputs]] = {}
        self._svc_locks: dict[int, asyncio.Lock] = {}
        self._adhoc_lock = asyncio.Lock()

    def _loaded_settings(self) -> Any:
        if self._settings is None:
            self._settings = self._settings_loader()
        return self._settings

    # ── 공개 ────────────────────────────────────────────────────────────
    async def diagnose(
        self, service_id: int, *, scope: Collection[str] | None
    ) -> dict[str, Any] | None:
        """서비스 1개의 D1 진단 — 서비스가 없거나 보이는 구성원이 없으면 None(라우트 404).

        Args:
            scope: 요청자 알람 존 범위(None = 전 존)
        """
        inputs = await self._inputs(int(service_id))
        if inputs is None:
            return None
        return self._view(inputs, scope)

    # ── 메모 ────────────────────────────────────────────────────────────
    def _fresh(self, sid: int) -> _Inputs | None:
        hit = self._memo.get(sid)
        if hit is not None and self._monotonic() - hit[0] < self._ttl:
            return hit[1]
        return None

    def _sweep(self) -> None:
        now = self._monotonic()
        for sid in [s for s, (at, _) in self._memo.items() if now - at >= self._ttl]:
            del self._memo[sid]

    async def _inputs(self, sid: int) -> _Inputs | None:
        hit = self._fresh(sid)
        if hit is not None:
            return hit
        lock = self._svc_locks.setdefault(sid, asyncio.Lock())
        try:
            async with lock:
                hit = self._fresh(sid)
                if hit is not None:
                    return hit
                inputs = await self._collect(sid)
                if inputs is None:
                    return None
                self._sweep()
                self._memo[sid] = (self._monotonic(), inputs)
                await self._write_d1_memo(sid, inputs)
                return inputs
        finally:
            if not lock.locked():
                self._svc_locks.pop(sid, None)

    async def _write_d1_memo(self, sid: int, inputs: _Inputs) -> None:
        """D2 메모(존별 판독 — 그 존 구성원만) — 기록 실패는 로그(D2는 메모 없이 종전 순서)."""
        if self._redis is None:
            return
        by_zone: dict[str, dict[str, Any]] = {}
        for zone in sorted({member_zone(m) for m in _members(inputs.service)} - {""}):
            view = self._view(inputs, frozenset({zone}))
            if view is None:
                continue
            by_zone[zone] = {
                "verdict": view["verdict"],
                "anomalous_members": [
                    {"kind": m["kind"], "member_key": m["member_key"]}
                    for m in view["anomalous_members"]
                ],
            }
        value = {"by_zone": by_zone, "computed_at": iso_of(inputs.computed_at)}
        try:
            await self._redis.set(f"{D1_MEMO_PREFIX}{sid}", json.dumps(value, ensure_ascii=False),
                                  ex=max(1, int(self._ttl)))
        except Exception as exc:  # noqa: BLE001 — 메모 기록 실패가 진단 응답을 막지 않는다
            logger.warning("D1 메모 기록 실패: service=%s %s", sid, exc)

    # ── 입력 모으기 ─────────────────────────────────────────────────────
    async def _collect(self, sid: int) -> _Inputs | None:
        snap = await self._agg.snapshot()
        svc = snap.services.get(sid)
        if not isinstance(svc, Mapping):
            return None
        members = _members(svc)
        facts = tuple(f for f in snap.facts if f.service_id == sid)
        notices = list(snap.notices)
        observed = await self._observe(sid, svc, members)
        links: dict[str, DiagLink] = {}
        if any(_text(m.get("kind")) == KIND_APM_INSTANCE for m in members):
            records, reason = await read_link_records(self._map_cache, members)
            if reason:
                notices.append(reason)
            for key, rec in records.items():
                targets = tuple(sorted({
                    k for k in (
                        sms_member_key(t.get("db_id"), t.get("resource_id"))
                        for t in rec.get("targets") or ()
                        if isinstance(t, Mapping) and t.get("target_gone") is not True
                        and (_text(t.get("kind")) or KIND_SMS) == KIND_SMS
                    ) if k
                }))
                links[key] = DiagLink(_text(rec.get("state")), targets)
        investigations = await self._recent_investigations(facts, notices)
        return _Inputs(self._wall(), float(snap.computed_at), svc, facts, observed, links,
                       investigations, tuple(notices))

    async def _observe(self, sid: int, svc: Mapping[str, Any],
                       members: Sequence[Mapping[str, Any]]) -> _Observed:
        """M3 최신값이 있으면 그것(관측 호출 0), 없으면 즉석 1회."""
        obs = _Observed()
        if self._metrics_active and svc.get("monitored") is True:
            got = await self._read_m3(sid)
            if got is not None:
                return self._from_m3(*got, members)
            obs.note(REASON_M3_MISSING)
        await self._adhoc(members, obs)
        return obs

    async def _read_m3(self, sid: int) -> tuple[MonitorValue, dict[str, str] | None] | None:
        """M3 최신값 1건(GET 1회) + 존별 정각 통계 시각(`sms_stat_time_by_db` — 칸 없음 = None).

        없음·손상·Redis 실패는 None(호출부가 즉석 조회 + 사유).
        """
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(f"{MONITOR_SVC_PREFIX}{int(sid)}")
        except Exception:  # noqa: BLE001 — 그 서비스만 「M3 최신값 없음」
            logger.debug("M3 최신값 조회 실패: service=%s", sid, exc_info=True)
            return None
        mv = parse_monitor_value(raw)
        if mv is None:
            return None
        if isinstance(raw, (bytes, bytearray)):
            raw = bytes(raw).decode("utf-8", errors="replace")
        by_db = json.loads(raw).get("sms_stat_time_by_db")
        if not isinstance(by_db, Mapping):
            return mv, None
        return mv, {_text(k): _text(v) for k, v in by_db.items() if _text(k) and _text(v)}

    @staticmethod
    def _from_m3(mv: MonitorValue, by_db: Mapping[str, str] | None,
                 members: Sequence[Mapping[str, Any]]) -> _Observed:
        """M3 최신값 → 관측값 — 존별 시각이 있으면 구성원별, 없으면(옛 형태) 서비스 단위."""
        if by_db is None:
            stat_at = _local_epoch(mv.sms_stat_time or "", "%Y-%m-%d %H:%M")
            metrics = DiagMetrics(
                apm_signals=mv.apm_signals, sms_metrics=mv.sms_metrics,
                apm_observed_at=_epoch(mv.updated_at), sms_stat_at=stat_at,
                sms_stat_label=_stat_label(stat_at),
            )
        else:
            stats: dict[str, tuple[float, str]] = {}
            for m in members:
                if _text(m.get("kind")) != KIND_SMS_SERVER:
                    continue
                at = _local_epoch(by_db.get(_text(m.get("db_id")), ""), "%Y-%m-%d %H:%M")
                if at is not None:
                    stats[_text(m.get("member_key"))] = (at, _stat_label(at))
            metrics = DiagMetrics(
                apm_signals=mv.apm_signals, sms_metrics=mv.sms_metrics,
                apm_observed_at=_epoch(mv.updated_at), sms_stats=stats,
            )
        obs = _Observed(metrics=metrics, source=SOURCE_M3, at=_epoch(mv.updated_at))
        if not mv.in_window:
            obs.note(REASON_M3_STALE, partial=True)
        for f in mv.fetch_failures:
            obs.note(f"M3 — {f}", partial=True)
        return obs

    async def _adhoc(self, members: Sequence[Mapping[str, Any]], obs: _Observed) -> None:
        """즉석 1회 — 전역 동시 1 · 예산 안(APM 소스당 1호출 · SMS 존별 고정 SELECT)."""
        deadline = self._monotonic() + self._budget
        try:
            await asyncio.wait_for(self._adhoc_lock.acquire(), timeout=self._budget)
        except TimeoutError:
            obs.note(REASON_BUSY, partial=True)
            return
        try:
            obs.source = SOURCE_ADHOC
            obs.at = self._wall()
            (signals, observed_at), (sms, stat_keys) = await asyncio.gather(
                self._adhoc_apm(members, deadline, obs),
                self._adhoc_sms(members, deadline, obs),
            )
        finally:
            self._adhoc_lock.release()
        stats: dict[str, tuple[float, str]] = {}
        for key, hour in stat_keys.items():
            at = _local_epoch(hour, "%Y%m%d%H")
            if at is not None:
                stats[key] = (at, _stat_label(at))
        obs.metrics = DiagMetrics(
            apm_signals=tuple(signals), sms_metrics=tuple(sms), apm_observed_at=observed_at,
            sms_stats=stats,
        )

    async def _adhoc_apm(
        self, members: Sequence[Mapping[str, Any]], deadline: float, obs: _Observed
    ) -> tuple[list[ApmSignalFact], float | None]:
        inst = [m for m in members if _text(m.get("kind")) == KIND_APM_INSTANCE]
        usable = [m for m in inst if _text(m.get("source_id")) and _text(m.get("instance_name"))]
        if len(usable) < len(inst):
            obs.note(f"APM 인스턴스 식별 불가 {len(inst) - len(usable)}개 — 조회 제외")
        if not usable:
            return [], None
        taken = usable[:self._max_apm]
        if len(usable) > len(taken):
            obs.note(f"APM 인스턴스 상한 {self._max_apm} 초과(제외 {len(usable) - len(taken)}개)",
                     partial=True)
        if self._gateway is None:
            obs.note(REASON_GATEWAY_UNSET)
            return [], None
        by_source: dict[str, list[Mapping[str, Any]]] = {}
        for m in taken:
            by_source.setdefault(_text(m.get("source_id")), []).append(m)
        observed_at = self._wall()
        results = await asyncio.gather(*(
            self._health(source, items, deadline, obs)
            for source, items in sorted(by_source.items())
        ))
        return [s for chunk in results for s in chunk], observed_at

    async def _health(self, source: str, items: Sequence[Mapping[str, Any]], deadline: float,
                      obs: _Observed) -> list[ApmSignalFact]:
        """APM 소스 1개 — `apm_app_health` 1호출(M3 수집기와 같은 봉투 해석)."""
        label = f"APM {source}"
        remaining = deadline - self._monotonic()
        if remaining < MIN_CALL_SECONDS:
            obs.note(f"{label} — 예산 {self._budget:g}초 초과로 조회 못 함", partial=True)
            return []
        args = {
            "targets": [{"instance_name": _text(m.get("instance_name")), "source_id": source}
                        for m in items],
            "lookback_minutes": APM_LOOKBACK_MINUTES,
            "owner": DIAG_OWNER,
            "wait_seconds": round(max(1.0, remaining - WAIT_MARGIN_SECONDS), 1),
        }
        obs.calls["apm"] += 1
        try:
            env = await self._gateway.call_tool(APM_HEALTH_TOOL, args, timeout=remaining)
            if not isinstance(env, Mapping):
                raise TypeError("게이트웨이 응답이 객체가 아님")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 소스 단위 실패(시간 초과 포함)는 사유로
            obs.note(f"{label} 조회 실패 — {type(exc).__name__}: {_text(exc)[:160]}", partial=True)
            return []
        err = _error_text(env)
        if err:
            obs.note(f"{label} 조회 실패 — {err}", partial=True)
            return []
        job = env.get("job")
        if isinstance(job, Mapping) and _text(job.get("state")) in ("queued", "running"):
            obs.note(f"{label} — 게이트웨이 작업이 예산 안에 끝나지 않음", partial=True)
            return []
        rows = [r for r in env.get("rows") or () if isinstance(r, Mapping)]
        batch = {int(e["index"]): e for e in env.get("batch") or ()
                 if isinstance(e, Mapping) and isinstance(e.get("index"), int)}
        out: list[ApmSignalFact] = []
        failed = 0
        for index, m in enumerate(items):
            entry = batch.get(index)
            domain = _text(m.get("domain_id"))
            matched = [r for r in rows
                       if r.get("target_index") == index and _text(r.get("domain_id")) == domain]
            if entry is None or _text(entry.get("status")) == "error" or not matched:
                failed += 1
                continue
            ids = {_text(r.get("instance_id")) for r in matched}
            for sig in entry.get("was_signals") or ():
                if isinstance(sig, Mapping) and _text(sig.get("instance_id")) in ids:
                    out.append(ApmSignalFact(_text(m.get("member_key")), _text(sig.get("kind")),
                                             _text(sig.get("level"))))
        if failed:
            obs.note(f"{label} — 인스턴스 {failed}개 결과 없음(조회 실패·미발견)", partial=True)
        return out

    async def _adhoc_sms(
        self, members: Sequence[Mapping[str, Any]], deadline: float, obs: _Observed
    ) -> tuple[list[SmsMetricFact], dict[str, str]]:
        """SMS 존별 조회 — (지표, 구성원 키 → 그 존 묶음의 최신 정각 키 `YYYYMMDDHH`)."""
        servers: dict[str, dict[int, list[str]]] = {}
        for m in members:
            if _text(m.get("kind")) != KIND_SMS_SERVER:
                continue
            db_id = _text(m.get("db_id"))
            try:
                rid = int_key(m.get("resource_id"))
            except Exception:  # noqa: BLE001 — 규칙 밖 id는 그 구성원만 제외
                obs.note(f"SMS 서버 식별 불가 — {_text(m.get('member_key'))}")
                continue
            key = sms_member_key(db_id, rid) if db_id else None
            if key is None:
                obs.note(f"SMS 서버 식별 불가 — {_text(m.get('member_key'))}")
                continue
            servers.setdefault(db_id, {}).setdefault(rid, []).append(key)
        if not servers:
            return [], {}
        sources = dict(self._loaded_settings().sms_metric_source)
        if not sources:
            obs.note(REASON_SMS_UNSET)
            return [], {}
        if self._execute is None:
            obs.note(REASON_SMS_NO_EXECUTOR)
            return [], {}
        execute = self._execute
        since = _hour_key(self._wall() - SMS_LATEST_LOOKBACK_HOURS * 3600)
        metrics = sorted(sources)
        out: list[SmsMetricFact] = []
        stat_keys: dict[str, str] = {}
        for db_id in sorted(servers):
            label = f"SMS {db_id}"
            engine = self._engine_of(db_id)
            ordered = sorted(servers[db_id])
            for start in range(0, len(ordered), SMS_IN_CHUNK):
                ids = tuple(ordered[start:start + SMS_IN_CHUNK])
                if deadline - self._monotonic() < MIN_CALL_SECONDS:
                    obs.note(f"{label} — 예산 {self._budget:g}초 초과로 조회 못 함", partial=True)
                    continue
                try:
                    async with sms_query_semaphore():
                        obs.calls["sms"] += 1
                        stat = await asyncio.wait_for(
                            read_sms_latest_stat(execute, db_id, engine, ids, sources,
                                                 since_hour=since),
                            timeout=max(0.1, deadline - self._monotonic()))
                        if stat is None:
                            obs.note(f"{label} — 최신 정각 통계 없음(최근"
                                     f" {SMS_LATEST_LOOKBACK_HOURS}시간)")
                            continue
                        obs.calls["sms"] += 1
                        rows = await asyncio.wait_for(
                            read_sms_metric_rows(execute, db_id, engine, ids, sources,
                                                 stat_hour_key=stat),
                            timeout=max(0.1, deadline - self._monotonic()))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — 존 단위 실패(시간 초과 포함)는 사유로
                    obs.note(f"{label} 조회 실패 — {type(exc).__name__}: {_text(exc)[:160]}",
                             partial=True)
                    continue
                got: dict[str, dict[str, float | None]] = {}
                for row in rows:
                    got.setdefault(row.server_id, {})[row.metric] = row.value
                for rid in ids:
                    for key in servers[db_id][rid]:
                        stat_keys[key] = stat
                        out.extend(SmsMetricFact(key, metric, got.get(str(rid), {}).get(metric))
                                   for metric in metrics)
        return out, stat_keys

    async def _recent_investigations(
        self, facts: Sequence[AlarmFact], notices: list[str]
    ) -> dict[str, dict[str, Any]]:
        """구성원별 최근 조사 1건(알람 최신 순 · 저장된 값만 — 원인 영역을 새로 판정하지 않는다)."""
        store = self._investigations
        if store is None:
            return {}
        by_member: dict[str, list[str]] = {}
        for f in sorted(facts, key=lambda x: (-(x.at or 0.0), x.alarm_id)):
            if f.member_key and f.alarm_id and f.alarm_id not in by_member.get(f.member_key, []):
                by_member.setdefault(f.member_key, []).append(f.alarm_id)

        def _scan() -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            budget = INVESTIGATION_LOOKUP_LIMIT
            for member_key in sorted(by_member):
                for alarm_id in by_member[member_key]:
                    if budget <= 0:
                        return out
                    budget -= 1
                    rec = store.get_by_alarm(alarm_id)
                    if isinstance(rec, Mapping) and rec.get("investigation_id"):
                        out[member_key] = _investigation_view(rec)
                        break
            return out

        try:
            return await asyncio.to_thread(_scan)
        except Exception as exc:  # noqa: BLE001 — 조사 기록 실패는 고지(진단은 계속)
            logger.warning("D1 조사 기록 읽기 실패: %s", exc)
            notices.append(REASON_INVESTIGATION_FAILED)
            return {}

    # ── 판독 · 존 필터 ──────────────────────────────────────────────────
    def _view(self, inputs: _Inputs, scope: Collection[str] | None) -> dict[str, Any] | None:
        members = _members(inputs.service)
        visible = [m for m in members if zone_visible(scope, member_zone(m))]
        if scope is not None and not visible:
            return None
        keys = {_text(m.get("member_key")) for m in visible}
        obs = inputs.observed
        metrics = DiagMetrics(
            apm_signals=tuple(s for s in obs.metrics.apm_signals if s.member_key in keys),
            sms_metrics=tuple(s for s in obs.metrics.sms_metrics if s.member_key in keys),
            apm_observed_at=obs.metrics.apm_observed_at,
            sms_stat_at=obs.metrics.sms_stat_at,
            sms_stat_label=obs.metrics.sms_stat_label,
            sms_stats={k: v for k, v in obs.metrics.sms_stats.items() if k in keys},
        )
        result = diagnose_service(
            members=[
                DiagMember(_text(m.get("kind")), _text(m.get("member_key")), _member_name(m),
                           _text(m.get("layer")), _text(m.get("db_id")),
                           _text(m.get("resource_id")))
                for m in visible
            ],
            alarms=[
                DiagAlarm(f.member_key, f.alarm_id, f.severity, f.at, f.kind == FACT_INCIDENT)
                for f in inputs.facts if f.member_key in keys and zone_visible(scope, f.zone)
            ],
            links={k: v for k, v in inputs.links.items() if k in keys},
            metrics=metrics,
            thresholds=self._loaded_settings().config.sms_thresholds,
            now=inputs.computed_at,
            window_minutes=self.window_minutes,
            hidden_members=len(members) - len(visible),
        )
        for row in result["anomalous_members"]:
            row["investigation"] = inputs.investigations.get(row["member_key"])
        return {
            "service_id": inputs.service.get("id"),
            "display_name": inputs.service.get("display_name"),
            **result,
            "computed_at": iso_of(inputs.computed_at),
            "window_minutes": self.window_minutes,
            "sources": {
                "m2": iso_of(inputs.m2_at),
                "metrics": obs.source,
                "metrics_at": iso_of(obs.at),
                "calls": dict(obs.calls),
            },
            "partial": obs.partial,
            "notices": [*inputs.notices, *obs.notices],
        }


def _investigation_view(rec: Mapping[str, Any]) -> dict[str, Any]:
    """조사 레코드 → 표시 칸(저장된 플레이북 원인 영역만 · 확실성 배지는 조사 뷰어가 만든다)."""
    briefing = rec.get("briefing")
    agreement = briefing.get("engine_agreement") if isinstance(briefing, Mapping) else None
    playbook = agreement.get("playbook") if isinstance(agreement, Mapping) else None
    layer = _text(playbook.get("layer")) if isinstance(playbook, Mapping) else ""
    return {
        "investigation_id": _text(rec.get("investigation_id")),
        "status": _text(rec.get("status")),
        "at": _text(rec.get("ts")) or None,
        "cause_layer": layer if layer and layer != "unknown" else None,
    }


def build_biz_diagnoser(
    config: Any,
    *,
    aggregator: Any,
    map_cache: Any = None,
    investigation_store: Any = None,
) -> BizDiagnoser | None:
    """라우트용 조립 — 플래그가 꺼져 있으면 None(게이트웨이 클라이언트·SQL 실행기 생성 0).

    Args:
        aggregator: 보드 라우트의 M2 집계기(같은 10초 메모를 나눠 쓴다)
        map_cache: `app.state.apm_host_map_cache`
        investigation_store: 조사 저장소(`InvestigationStore`)
    """
    if not diagnosis_active(config):
        return None
    from noise_gate.application.biz_service_config import load_biz_service_config
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_scanners import GatewayToolCaller
    from noise_gate.infrastructure.apm_host_map_sql_scanners import registry_executor

    ng = config.noise_gate
    client = build_apm_gateway_client(config)
    gateway = GatewayToolCaller(client, timeout=BUDGET_SECONDS) if client is not None else None
    return BizDiagnoser(
        aggregator=aggregator,
        redis=getattr(getattr(aggregator, "cache", None), "redis", None),
        gateway=gateway,
        execute=registry_executor(config),
        settings_loader=load_biz_service_config,
        map_cache=map_cache,
        investigation_store=investigation_store,
        metrics_active=getattr(ng, "biz_monitor_metrics_active", False) is True,
        window_minutes=int(ng.biz_diagnosis_window_minutes),
        max_apm_targets=int(ng.biz_diagnosis_max_apm_targets),
    )


__all__ = [
    "BUDGET_SECONDS",
    "DIAG_OWNER",
    "MEMO_TTL_SECONDS",
    "SOURCE_ADHOC",
    "SOURCE_M3",
    "SOURCE_NONE",
    "BizDiagnoser",
    "build_biz_diagnoser",
    "diagnosis_active",
]
