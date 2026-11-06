"""업무 현황 상세 「지표」 보기 — 구성원별 지표 추이 조회 (plans/215 §5.2.1 · §4.5 · D-368).

P2 서비스 상세의 「지표」 보기 탭이 열릴 때 원천에서 1회 읽는다 — M3 수집기와 별개이고 저장 0
(PG 0 · Redis는 60초 메모만 — Q11·Q35). LLM 0 · 통보 0.

- **조회 구성원**(결정적): 상태 나쁜 순 → 최근 1시간 알람 수(많은 순) → 이름 → id ·
  ≤ `NOISE_BIZ_MONITOR_HISTORY_MAX_MEMBERS`(≤20). 요청자가 고른 구성원(`members`)은 그 서비스의
  활성 구성원 중 요청자에게 보이는 id만 받는다(아니면 `ValueError` → 400 — 임의 자원 id로 다른
  서버를 읽지 못하게 · 권한 밖 구성원도 같은 400이라 숨김 여부가 드러나지 않는다).
  자동 선택에서 존 권한 밖 구성원은 조회하지 않고 건수만 남는다(선 없음).
- **SMS**: 존(db_id)마다 고정 SELECT 1회(`biz_service_sql.read_sms_history` · 24시간 이하 =
  시간 통계 · 7일 = 일 통계 · 행 ≤ 점 × 서버 × 지표) · 공용 `sms_query_semaphore`(M3 수집기 ·
  D1과 동시 1 공유). 지표 원천(`sms_metric_source` · W0-8)이 비면 SMS SELECT 0 + 사유.
- **APM**: 소스마다 `apm_metrics(mode="series", scope="instance", targets ≤ 상한)` 1호출 ·
  제니퍼 업무 구성원은 (소스, 도메인)마다 `scope="business"` 1호출(업무 이름을 주지 않아 도메인당
  1호출 — 응답에서 구성원 업무 id만 남긴다). 호출자 `owner="biz_history"`(D-356 ⑤ 공정 분배 —
  M3 `biz_monitor`·D1 `biz_diagnosis`와 분리). 7일 창은 APM 미지원(v1 · Q36). 지표는 게이트웨이
  기본 지표(지표 이름을 지어내지 않는다) · **임계선 0**(D-335 ① — 본체 APM 임계 리터럴 0).
- **어제 같은 시각**(`compare=yesterday` · 1h/6h/24h만): 같은 조회를 하루 앞 창으로 1회 더 하고
  점 시각을 하루 뒤로 옮겨(`yesterday=true`) 같은 시간축에 겹친다.
- **상한**: 전체 동시 `NOISE_BIZ_MONITOR_HISTORY_MAX_CONCURRENCY`(프로세스 안) · 사용자당 진행 중
  1(넘으면 `HistoryBusy` → 429) · 요청 시간 예산 20초(넘은 소스는 빼고 부분 결과 + 사유 —
  침묵 금지).
- **메모**: Redis `bizmon:hist:{서비스·기간·조회 구성원·비교 해시}` `EX 60`(Redis가 없으면
  프로세스 안) — 여러 사람이 같은 화면을 봐도 원천 조회 1회. 소스 실패가 있는 결과는 적지
  않는다([다시 시도]가 다시 읽게).
- **보간 0**: 값이 없는 칸은 점을 만들지 않는다(화면이 선을 끊는다).

플래그(`biz_monitor_history_active`)가 꺼져 있으면 `build_biz_metrics_history`가 None을 돌려준다
(게이트웨이 클라이언트·실행기 생성 0) — 라우트는 그때 404를 낸다. M3 수요 키(`bizmon:viewers`)는
건드리지 않는다(Q37 — 감시 대상이 아닌 서비스도 열 때 1회).

계층: application — infrastructure SQL 빌더 + 게이트웨이 호출기(덕 타이핑 주입).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from noise_gate.application.biz_monitor_alarms import iso_of
from noise_gate.application.biz_monitor_collector import (
    MIN_CALL_SECONDS,
    REASON_SMS_UNSET,
    WAIT_MARGIN_SECONDS,
)
from noise_gate.domain.biz_service_keys import (
    KIND_APM_BUSINESS,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    parse_member_key,
)
from noise_gate.infrastructure.apm_host_map_sql_scanners import SqlExecutor, db_engine_of
from noise_gate.infrastructure.biz_service_sql import (
    HISTORY_GRAIN_DAY,
    HISTORY_GRAIN_HOUR,
    int_key,
    read_sms_history,
    sms_query_semaphore,
)

logger = logging.getLogger(__name__)

#: 게이트웨이 호출자(D-356 ⑤ 공정 분배 — M3·D1과 분리).
HISTORY_OWNER = "biz_history"
APM_SERIES_TOOL = "apm_metrics"
#: 메모 키 접두(`bizmon:hist:{해시}`).
MEMO_PREFIX = "bizmon:hist:"
MEMO_TTL_SECONDS = 60
#: 요청 시간 예산(초).
BUDGET_SECONDS = 20.0
COMPARE_YESTERDAY = "yesterday"
_DAY_SECONDS = 86400

#: 지표 보기 대상 종류(제니퍼 도메인 구성원은 v1 대상 아님).
SUPPORTED_KINDS = (KIND_SMS_SERVER, KIND_APM_INSTANCE, KIND_APM_BUSINESS)

BUSY_DETAIL = "다른 지표 조회가 진행 중입니다"
REASON_SMS_NO_EXECUTOR = "SMS 조회 실행기 없음 — SMS 이력 조회 0"
REASON_GATEWAY_UNSET = "APM 게이트웨이 주소 미설정 — APM 이력 조회 0"
REASON_APM_7D = "APM은 24시간까지"
NOTE_SMS_1H = "정각 통계 — 1시간 창은 점이 적다"

SOURCE_SMS = "sms"
SOURCE_APM = "apm"
#: 소스 상태.
ST_OK = "ok"
ST_PARTIAL = "partial"
ST_FAILED = "failed"
ST_UNSET = "unset"
ST_NONE = "none"
ST_UNSUPPORTED = "unsupported"

#: SMS 지표 표시(순서 = 그래프 순서).
SMS_METRICS: tuple[tuple[str, str, str], ...] = (
    ("cpu", "CPU", "%"), ("memory", "메모리", "%"), ("filesystem", "파일시스템", "%"),
)
#: 게이트웨이 시계열 지표 식별자 → (표시 이름, 단위). 모르는 식별자는 식별자 그대로 보인다.
APM_METRIC_LABELS: dict[str, tuple[str, str]] = {
    "service_time": ("응답시간", "ms"),
    "service_count": ("호출 수", ""),
    "service_err_count": ("오류 수", ""),
}
_STATE_ORDER = {"risk": 0, "caution": 1, "unknown": 2, "no_signal": 3}


@dataclass(frozen=True)
class WindowSpec:
    """기간 1개 — SMS 통계 단위·점 수 · APM 간격(분 · None = 미지원)."""

    name: str
    seconds: int
    grain: str
    sms_points: int
    apm_interval: int | None


#: 기간(§5.2.1 표) — 24h APM 간격 15는 W0-25(서버 허용값) 확인 전 계획값이다.
WINDOWS: dict[str, WindowSpec] = {
    "1h": WindowSpec("1h", 3600, HISTORY_GRAIN_HOUR, 2, 1),
    "6h": WindowSpec("6h", 6 * 3600, HISTORY_GRAIN_HOUR, 6, 5),
    "24h": WindowSpec("24h", 24 * 3600, HISTORY_GRAIN_HOUR, 24, 15),
    "7d": WindowSpec("7d", 7 * _DAY_SECONDS, HISTORY_GRAIN_DAY, 7, None),
}
DEFAULT_WINDOW = "24h"


class HistoryBusy(Exception):  # noqa: N818 — 라우트가 429로 바꾸는 신호(오류 아님)
    """동시 상한(전체 · 사용자당 1)에 걸렸다 — 라우트가 429 + `BUSY_DETAIL`."""


def history_active(config: Any) -> bool:
    """지표 보기 플래그 유효 여부 — 정확히 True일 때만(`MagicMock` 설정 오판 방지)."""
    ng = getattr(config, "noise_gate", None)
    return getattr(ng, "biz_monitor_history_active", False) is True


def window_spec(window: str | None) -> WindowSpec:
    """기간 이름 → 기간(비면 24h · 모르는 값은 `ValueError`)."""
    spec = WINDOWS.get(str(window or DEFAULT_WINDOW).strip())
    if spec is None:
        raise ValueError("window는 1h·6h·24h·7d 중 하나여야 합니다.")
    return spec


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


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_text(env: Mapping[str, Any]) -> str | None:
    err = env.get("error")
    if not err:
        return None
    code = _text(err.get("code")) if isinstance(err, Mapping) else _text(err)
    reason = _text(env.get("reason"))[:160]
    return f"{code or 'gateway_error'}: {reason}" if reason else (code or "gateway_error")


def _stat_key(epoch: float, grain: str) -> str:
    fmt = "%Y%m%d%H" if grain == HISTORY_GRAIN_HOUR else "%Y%m%d"
    return time.strftime(fmt, time.localtime(epoch))


def _key_epoch(key: str, grain: str) -> float | None:
    """통계 키(서버 현지 시각 — M3과 같다) → epoch."""
    fmt = "%Y%m%d%H" if grain == HISTORY_GRAIN_HOUR else "%Y%m%d"
    try:
        return time.mktime(time.strptime(key, fmt))
    except (TypeError, ValueError, OverflowError):
        return None


def _stat_through(key: str | None, grain: str) -> str:
    epoch = _key_epoch(key, grain) if key else None
    if epoch is None:
        return ""
    t = time.localtime(epoch)
    if grain == HISTORY_GRAIN_HOUR:
        return f"{t.tm_hour:02d}시 통계까지"
    return f"{t.tm_mon:02d}/{t.tm_mday:02d} 일 통계까지"


def _hhmm_through(ms: int | None) -> str:
    if ms is None:
        return ""
    t = time.localtime(ms / 1000)
    return f"{t.tm_hour:02d}:{t.tm_min:02d}까지"


def _member_name(m: Mapping[str, Any]) -> str:
    for key in ("name", "server_name", "hostname", "instance_name", "domain_name", "member_key"):
        if _text(m.get(key)):
            return _text(m.get(key))
    return ""


def _recent_alarms(m: Mapping[str, Any]) -> int:
    alarms = m.get("alarms")
    n = _int(alarms.get("recent_total")) if isinstance(alarms, Mapping) else None
    return n or 0


def selection_order(members: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """조회 구성원 순서 — 상태 나쁜 순 → 최근 1시간 알람 많은 순 → 이름 → id(결정적)."""
    return sorted(members, key=lambda m: (
        _STATE_ORDER.get(_text(m.get("state")), 9), -_recent_alarms(m),
        _member_name(m).lower(), _int(m.get("id")) or 0,
    ))


def memo_key(service_id: int, window: str, member_ids: Sequence[int], compare: bool) -> str:
    """메모 키 — 서비스 · 기간 · 조회 구성원(정렬) · 비교."""
    ids = ",".join(str(i) for i in sorted(member_ids))
    raw = f"{int(service_id)}|{window}|{ids}|{int(compare)}"
    return MEMO_PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


@dataclass
class _Source:
    """소스 1개(SMS·APM)의 조회 결과 상태."""

    state: str = ST_NONE
    reasons: list[str] = field(default_factory=list)
    calls: int = 0
    failed: bool = False
    through: str = ""

    def note(self, text: str, *, failed: bool = False) -> None:
        if text not in self.reasons:
            self.reasons.append(text)
        self.failed = self.failed or failed

    def view(self, **extra: Any) -> dict[str, Any]:
        return {"state": self.state, "reason": " · ".join(self.reasons), "calls": self.calls,
                "through": self.through, **extra}


@dataclass
class _Run:
    """조회 1회분."""

    spec: WindowSpec
    compare: bool
    deadline: float
    now: float
    series: list[dict[str, Any]] = field(default_factory=list)
    sms: _Source = field(default_factory=_Source)
    apm: _Source = field(default_factory=_Source)


@dataclass(frozen=True)
class _Unit:
    """APM 호출 1단위 성공 — 마지막 점 시각(ms · 점이 없으면 None)."""

    latest_ms: int | None


def _finish(src: _Source, *, attempted: int, ok: int) -> None:
    """시도·성공 단위 수로 상태를 정한다(미설정·미지원은 이미 정해 두었다)."""
    if src.state in (ST_UNSET, ST_UNSUPPORTED) or attempted == 0:
        return
    if ok == 0:
        src.state = ST_FAILED
    elif src.failed or ok < attempted:
        src.state = ST_PARTIAL
    else:
        src.state = ST_OK


class BizMetricsHistory:
    """지표 보기 조회기 — 프로세스 안 1개(라우트가 `app.state`에 둔다).

    Args:
        redis: 메모 Redis(`bizmon:hist:*` — None이면 프로세스 안 메모)
        gateway: `GatewayToolCaller`(`call_tool(tool, args, *, timeout)`) — None이면 APM 0 + 사유
        execute: SMS SQL 실행기(`registry_executor` — 실행 전 `SQLGuard` · readonly MCP)
        settings_loader: 업무 서비스 설정(`load_biz_service_config` — 지표 원천·SMS 임계)
        max_members: 조회 구성원 상한(1~20)
        max_concurrency: 전체 동시 조회 상한
    """

    def __init__(
        self,
        *,
        redis: Any,
        gateway: Any,
        execute: SqlExecutor | None,
        settings_loader: Callable[[], Any],
        max_members: int = 20,
        max_concurrency: int = 2,
        engine_of: Callable[[str], str] = db_engine_of,
        budget_seconds: float = BUDGET_SECONDS,
        memo_ttl: int = MEMO_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        """의존성을 받는다(시계는 테스트 seam)."""
        self._redis = redis
        self._gateway = gateway
        self._execute = execute
        self._settings_loader = settings_loader
        self._settings: Any = None
        self.max_members = max(1, min(20, int(max_members)))
        self._max_concurrency = max(1, int(max_concurrency))
        self._engine_of = engine_of
        self._budget = float(budget_seconds)
        self._ttl = max(1, int(memo_ttl))
        self._monotonic = monotonic
        self._wall = wall
        self._running = 0
        self._users: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self._local_memo: dict[str, tuple[float, dict[str, Any]]] = {}

    def _loaded_settings(self) -> Any:
        if self._settings is None:
            self._settings = self._settings_loader()
        return self._settings

    # ── 공개 ────────────────────────────────────────────────────────────
    def select(
        self,
        service: Mapping[str, Any],
        visible: Sequence[Mapping[str, Any]],
        member_ids: Sequence[int] | None,
    ) -> tuple[list[Mapping[str, Any]], str]:
        """조회 구성원 → (보이는 조회 구성원 · 선택 방식).

        `member_ids`가 None이면 자동(상태 → 알람 수 → 이름 순 · 상한). 그 서비스의 활성 구성원이
        아니거나 요청자에게 보이지 않는 id는 같은 `ValueError`(라우트 400).
        """
        active = {_int(m.get("id")) for m in service.get("members") or () if isinstance(m, Mapping)}
        active.discard(None)
        by_id = {_int(m.get("id")): m for m in visible if isinstance(m, Mapping)}
        if member_ids is None:
            pool = [m for m in selection_order(list(by_id.values()))
                    if _text(m.get("kind")) in SUPPORTED_KINDS]
            return pool[:self.max_members], "auto"
        wanted = list(dict.fromkeys(int(i) for i in member_ids))
        if len(wanted) > self.max_members:
            raise ValueError(f"members는 {self.max_members}개까지입니다.")
        # 권한 밖(보이지 않는) 구성원도 「구성원 아님」과 같은 400
        # — 숨김 여부를 응답으로 구별하지 못하게
        if any(i not in active or i not in by_id for i in wanted):
            raise ValueError("members에 이 서비스의 구성원이 아닌 id가 있습니다.")
        chosen = [by_id[i] for i in wanted if _text(by_id[i].get("kind")) in SUPPORTED_KINDS]
        return selection_order(chosen), "manual"

    async def query(
        self,
        *,
        service_id: int,
        service: Mapping[str, Any],
        visible: Sequence[Mapping[str, Any]],
        hidden_member_count: int,
        window: str | None,
        member_ids: Sequence[int] | None,
        compare: bool,
        user: str,
        links: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        """지표 보기 응답 1건.

        Raises:
            ValueError: 기간·비교·구성원 인자 오류(400)
            HistoryBusy: 동시 상한(429)
        """
        spec = window_spec(window)
        if compare and spec.grain == HISTORY_GRAIN_DAY:
            raise ValueError("어제 비교는 1h·6h·24h에서만 됩니다.")
        chosen, mode = self.select(service, visible, member_ids)
        who = user or "-"
        if who in self._users:
            raise HistoryBusy(BUSY_DETAIL)
        self._users.add(who)
        try:
            ids = [i for i in (_int(m.get("id")) for m in chosen) if i is not None]
            key = memo_key(service_id, spec.name, ids, compare)
            core = await self._memo_get(key)
            memo = core is not None
            if core is None:
                core, memo = await self._compute_once(key, spec, chosen, compare)
        finally:
            self._users.discard(who)
        return self._view(
            core, service_id=service_id, spec=spec, compare=compare, visible=visible,
            chosen=chosen, hidden_member_count=hidden_member_count,
            mode=mode, memo=memo, links=links,
        )

    # ── 메모 · 상한 ─────────────────────────────────────────────────────
    async def _compute_once(
        self, key: str, spec: WindowSpec, chosen: Sequence[Mapping[str, Any]], compare: bool
    ) -> tuple[dict[str, Any], bool]:
        if self._running >= self._max_concurrency:
            raise HistoryBusy(BUSY_DETAIL)
        self._running += 1
        lock = self._locks.setdefault(key, asyncio.Lock())
        try:
            async with lock:
                hit = await self._memo_get(key)
                if hit is not None:
                    return hit, True
                core = await self._compute(spec, chosen, compare)
                if not core["failed"]:
                    await self._memo_set(key, core)
                return core, False
        finally:
            self._running -= 1
            if not lock.locked():
                self._locks.pop(key, None)

    async def _memo_get(self, key: str) -> dict[str, Any] | None:
        if self._redis is None:
            hit = self._local_memo.get(key)
            if hit is not None and self._monotonic() - hit[0] < self._ttl:
                return hit[1]
            return None
        try:
            raw = await self._redis.get(key)
        except Exception as exc:  # noqa: BLE001 — 메모 읽기 실패는 원천 조회로(로그 남김)
            logger.warning("지표 보기 메모 읽기 실패: %s", exc)
            return None
        if raw is None:
            return None
        try:
            value = json.loads(_text(raw))
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    async def _memo_set(self, key: str, core: dict[str, Any]) -> None:
        if self._redis is None:
            now = self._monotonic()
            for k in [k for k, (at, _) in self._local_memo.items() if now - at >= self._ttl]:
                del self._local_memo[k]
            self._local_memo[key] = (now, core)
            return
        try:
            await self._redis.set(key, json.dumps(core, ensure_ascii=False), ex=self._ttl)
        except Exception as exc:  # noqa: BLE001 — 메모 기록 실패가 응답을 막지 않는다
            logger.warning("지표 보기 메모 기록 실패: %s", exc)

    # ── 조회 ────────────────────────────────────────────────────────────
    async def _compute(
        self, spec: WindowSpec, chosen: Sequence[Mapping[str, Any]], compare: bool
    ) -> dict[str, Any]:
        now = self._wall()
        run = _Run(spec, compare, self._monotonic() + self._budget, now)
        sms = [m for m in chosen if _text(m.get("kind")) == KIND_SMS_SERVER]
        apm = [m for m in chosen if _text(m.get("kind")) in (KIND_APM_INSTANCE, KIND_APM_BUSINESS)]
        await asyncio.gather(self._sms(run, sms), self._apm(run, apm))
        sms_view = run.sms.view(table=spec.grain, note=NOTE_SMS_1H if spec.name == "1h" else "")
        apm_view = run.apm.view(interval_minute=spec.apm_interval)
        return {
            "generated_at": iso_of(now),
            "range": {"since": iso_of(now - spec.seconds), "until": iso_of(now)},
            "series": run.series,
            "sms": sms_view,
            "apm": apm_view,
            "failed": run.sms.failed or run.apm.failed,
        }

    def _remaining(self, run: _Run) -> float:
        return run.deadline - self._monotonic()

    # SMS ──
    async def _sms(self, run: _Run, members: Sequence[Mapping[str, Any]]) -> None:
        src = run.sms
        if not members:
            return
        servers: dict[str, dict[int, list[Mapping[str, Any]]]] = {}
        for m in members:
            db_id = _text(m.get("db_id"))
            try:
                rid = int_key(m.get("resource_id"))
            except Exception:  # noqa: BLE001 — 규칙 밖 id는 그 구성원만 제외
                src.note(f"SMS 서버 식별 불가 — {_text(m.get('member_key'))}")
                continue
            if not db_id:
                src.note(f"SMS 서버 식별 불가 — {_text(m.get('member_key'))}")
                continue
            servers.setdefault(db_id, {}).setdefault(rid, []).append(m)
        if not servers:
            return
        sources = dict(self._loaded_settings().sms_metric_source)
        if not sources:
            src.state = ST_UNSET
            src.note(REASON_SMS_UNSET)
            return
        if self._execute is None:
            src.state = ST_FAILED
            src.note(REASON_SMS_NO_EXECUTOR, failed=True)
            return
        windows = [(False, run.now)] + ([(True, run.now - _DAY_SECONDS)] if run.compare else [])
        attempted = ok = 0
        latest: str | None = None
        for db_id in sorted(servers):
            for yesterday, end in windows:
                attempted += 1
                rows = await self._sms_select(run, db_id, sorted(servers[db_id]), sources, end)
                if rows is None:
                    continue
                ok += 1
                latest = self._sms_series(run, servers[db_id], rows, sources, yesterday, latest)
        _finish(src, attempted=attempted, ok=ok)
        src.through = _stat_through(latest, run.spec.grain)

    async def _sms_select(self, run: _Run, db_id: str, ids: Sequence[int],
                          sources: Mapping[str, tuple[str, str]], end: float) -> list[Any] | None:
        src = run.sms
        label = f"폴스타 {db_id}"
        spec = run.spec
        step = 3600 if spec.grain == HISTORY_GRAIN_HOUR else _DAY_SECONDS
        until = _stat_key(end, spec.grain)
        since = _stat_key(end - (spec.sms_points - 1) * step, spec.grain)
        remaining = self._remaining(run)
        if remaining < MIN_CALL_SECONDS:
            src.note(f"{label} — 예산 {self._budget:g}초 초과로 제외", failed=True)
            return None
        execute = self._execute
        assert execute is not None

        async def _locked() -> list[Any]:
            async with sms_query_semaphore():
                src.calls += 1
                return list(await read_sms_history(
                    execute, db_id, self._engine_of(db_id), ids, sources, grain=spec.grain,
                    since_key=since, until_key=until))

        try:
            return await asyncio.wait_for(_locked(), timeout=max(0.1, remaining))
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            src.note(f"{label} — 예산 {self._budget:g}초 초과로 제외", failed=True)
        except Exception as exc:  # noqa: BLE001 — 존 단위 실패는 사유로(나머지는 계속)
            src.note(f"{label} 조회 실패 — {type(exc).__name__}: {_text(exc)[:160]}",
                      failed=True)
        return None

    def _sms_series(self, run: _Run, servers: Mapping[int, Sequence[Mapping[str, Any]]],
                    rows: Sequence[Any], sources: Mapping[str, tuple[str, str]],
                    yesterday: bool, latest: str | None) -> str | None:
        grain = run.spec.grain
        step = 3600 if grain == HISTORY_GRAIN_HOUR else _DAY_SECONDS
        shift = _DAY_SECONDS if yesterday else 0
        points: dict[tuple[str, str], list[tuple[float, float]]] = {}
        for row in rows:
            epoch = _key_epoch(row.stat_key, grain)
            if epoch is None:
                continue
            points.setdefault((row.server_id, row.metric), []).append((epoch + shift, row.value))
            if not yesterday and (latest is None or row.stat_key > latest):
                latest = row.stat_key
        for rid in sorted(servers):
            for m in servers[rid]:
                for metric, label, unit in SMS_METRICS:
                    if metric not in sources:
                        continue
                    pts = sorted(points.get((str(rid), metric), []))
                    run.series.append(self._series(
                        m, SOURCE_SMS, metric, label, unit, step, pts, yesterday))
        return latest

    # APM ──
    async def _apm(self, run: _Run, members: Sequence[Mapping[str, Any]]) -> None:
        src = run.apm
        if not members:
            return
        if run.spec.apm_interval is None:
            src.state = ST_UNSUPPORTED
            src.note(REASON_APM_7D)
            return
        inst: dict[str, list[Mapping[str, Any]]] = {}
        business: dict[tuple[str, str], list[tuple[Mapping[str, Any], str]]] = {}
        for m in members:
            key = _text(m.get("member_key"))
            if _text(m.get("kind")) == KIND_APM_INSTANCE:
                parts = parse_member_key(KIND_APM_INSTANCE, key)
                source = _text(m.get("source_id")) or (parts[0] if parts else "")
                if not source or not _text(m.get("instance_name")):
                    src.note(f"APM 인스턴스 식별 불가 — {key}")
                    continue
                inst.setdefault(source, []).append(m)
            else:
                bparts = parse_member_key(KIND_APM_BUSINESS, key)
                if bparts is None:
                    src.note(f"제니퍼 업무 식별 불가 — {key}")
                    continue
                business.setdefault((bparts[0], bparts[1]), []).append((m, bparts[2]))
        if not inst and not business:
            return
        if self._gateway is None:
            src.state = ST_FAILED
            src.note(REASON_GATEWAY_UNSET, failed=True)
            return
        windows = [False] + ([True] if run.compare else [])
        units = [self._instance_unit(run, source, items, yesterday=y)
                 for source, items in sorted(inst.items()) for y in windows]
        units += [self._business_unit(run, source, domain, items, yesterday=y)
                  for (source, domain), items in sorted(business.items()) for y in windows]
        results = await asyncio.gather(*units)
        done = [r for r in results if r is not None]
        latest = [r.latest_ms for r in done if r.latest_ms is not None]
        _finish(src, attempted=len(units), ok=len(done))
        src.through = _hhmm_through(max(latest) if latest else None)

    async def _call(self, run: _Run, label: str, args: Mapping[str, Any],
                    yesterday: bool) -> Mapping[str, Any] | None:
        """게이트웨이 1호출 — 성공 봉투 또는 None(사유는 `run.apm`에)."""
        src = run.apm
        remaining = self._remaining(run)
        if remaining < MIN_CALL_SECONDS:
            src.note(f"{label} — 예산 {self._budget:g}초 초과로 제외", failed=True)
            return None
        spec = run.spec
        call_args: dict[str, Any] = {
            **dict(args), "mode": "series", "lookback_minutes": spec.seconds // 60,
            "interval_minute": spec.apm_interval, "owner": HISTORY_OWNER,
            "wait_seconds": round(max(1.0, remaining - WAIT_MARGIN_SECONDS), 1),
        }
        if yesterday:
            call_args["reference_time"] = datetime.fromtimestamp(
                run.now - _DAY_SECONDS, tz=UTC).isoformat()
        src.calls += 1
        try:
            env = await asyncio.wait_for(
                self._gateway.call_tool(APM_SERIES_TOOL, call_args, timeout=remaining),
                timeout=max(0.1, remaining))
            if not isinstance(env, Mapping):
                raise TypeError("게이트웨이 응답이 객체가 아님")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 호출 단위 실패(시간 초과 포함)는 사유로
            src.note(f"{label} 조회 실패 — {type(exc).__name__}: {_text(exc)[:160]}",
                      failed=True)
            return None
        err = _error_text(env)
        if err:
            src.note(f"{label} 조회 실패 — {err}", failed=True)
            return None
        job = env.get("job")
        if isinstance(job, Mapping) and _text(job.get("state")) in ("queued", "running"):
            src.note(f"{label} — 게이트웨이 작업이 예산 안에 끝나지 않아 제외", failed=True)
            return None
        rows = env.get("rows") or []
        total = env.get("total_row_count")
        if isinstance(total, int) and not isinstance(total, bool) and total > len(rows):
            src.note(f"{label} — 결과 일부만(인라인 {len(rows)}/{total}행)", failed=True)
        return env

    async def _instance_unit(self, run: _Run, source: str, items: Sequence[Mapping[str, Any]],
                             *, yesterday: bool) -> _Unit | None:
        """APM 소스 1개 인스턴스 시계열 1호출 — 성공이면 마지막 점 시각 · 실패 None."""
        env = await self._call(run, f"제니퍼 {source}", {
            "scope": "instance",
            "targets": [{"instance_name": _text(m.get("instance_name")), "source_id": source}
                        for m in items],
        }, yesterday)
        if env is None:
            return None
        rows = [r for r in env.get("rows") or () if isinstance(r, Mapping)]
        batch = {int(e["index"]): e for e in env.get("batch") or ()
                 if isinstance(e, Mapping) and isinstance(e.get("index"), int)}
        failed = 0
        latest: int | None = None
        for index, m in enumerate(items):
            entry = batch.get(index)
            domain = _text(m.get("domain_id"))
            matched = [r for r in rows if r.get("target_index") == index
                       and (not domain or _text(r.get("domain_id")) == domain)]
            if (entry is not None and _text(entry.get("status")) == "error") or (
                    entry is None and not matched):
                failed += 1
                continue
            latest = self._apm_series(run, m, matched, yesterday, latest)
        if failed:
            run.apm.note(f"제니퍼 {source} — 인스턴스 {failed}개 결과 없음(조회 실패·미발견)",
                         failed=True)
        return _Unit(latest)

    async def _business_unit(self, run: _Run, source: str, domain: str,
                             items: Sequence[tuple[Mapping[str, Any], str]],
                             *, yesterday: bool) -> _Unit | None:
        """제니퍼 (소스, 도메인) 업무 시계열 1호출 — 구성원 업무 id만 남긴다(실패 None)."""
        label = f"제니퍼 업무 {source}·{domain}"
        domain_id = _int(domain)
        if domain_id is None:
            run.apm.note(f"{label} — 도메인 id가 정수가 아님", failed=True)
            return None
        env = await self._call(run, label, {
            "scope": "business", "domain_id": domain_id, "source_ids": [source],
        }, yesterday)
        if env is None:
            return None
        by_id: dict[str, list[Mapping[str, Any]]] = {}
        for r in env.get("rows") or ():
            if isinstance(r, Mapping) and _text(r.get("domain_id")) == domain:
                by_id.setdefault(_text(r.get("business_id")), []).append(r)
        latest: int | None = None
        for m, business_id in items:
            latest = self._apm_series(run, m, by_id.get(business_id, []), yesterday, latest)
        return _Unit(latest)

    def _apm_series(self, run: _Run, m: Mapping[str, Any], rows: Sequence[Mapping[str, Any]],
                    yesterday: bool, latest: int | None) -> int | None:
        shift = _DAY_SECONDS if yesterday else 0
        by_metric: dict[str, list[tuple[float, float]]] = {}
        for r in rows:
            ms, value = _int(r.get("time_ms")), _float(r.get("value"))
            metric = _text(r.get("metric"))
            if ms is None or value is None or not metric:
                continue  # 값 없는 칸은 점을 만들지 않는다(보간 0)
            by_metric.setdefault(metric, []).append((ms / 1000 + shift, value))
            if not yesterday and (latest is None or ms > latest):
                latest = ms
        step = (run.spec.apm_interval or 1) * 60
        known = [k for k in APM_METRIC_LABELS if k in by_metric]
        for metric in known + sorted(k for k in by_metric if k not in APM_METRIC_LABELS):
            label, unit = APM_METRIC_LABELS.get(metric, (metric, ""))
            run.series.append(self._series(
                m, SOURCE_APM, metric, label, unit, step, sorted(by_metric[metric]), yesterday))
        return latest

    @staticmethod
    def _series(m: Mapping[str, Any], source: str, metric: str, label: str, unit: str,
                step: int, points: Sequence[tuple[float, float]],
                yesterday: bool) -> dict[str, Any]:
        return {
            "member_id": _int(m.get("id")),
            "member_key": _text(m.get("member_key")),
            "kind": _text(m.get("kind")),
            "source": source,
            "metric": metric,
            "label": label,
            "unit": unit,
            "step_seconds": step,
            "yesterday": yesterday,
            "points": [[iso_of(t), round(v, 3)] for t, v in points],
        }

    # ── 응답 ────────────────────────────────────────────────────────────
    def _view(
        self,
        core: Mapping[str, Any],
        *,
        service_id: int,
        spec: WindowSpec,
        compare: bool,
        visible: Sequence[Mapping[str, Any]],
        chosen: Sequence[Mapping[str, Any]],
        hidden_member_count: int,
        mode: str,
        memo: bool,
        links: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        queried = [_int(m.get("id")) for m in chosen]
        ordered = selection_order(list(visible))
        notices: list[str] = []
        unsupported = sum(1 for m in ordered if _text(m.get("kind")) not in SUPPORTED_KINDS)
        if unsupported:
            notices.append(f"제니퍼 도메인 구성원 {unsupported}개 — 지표 보기 대상 아님")
        if mode == "auto":
            pool = sum(1 for m in ordered if _text(m.get("kind")) in SUPPORTED_KINDS)
            if pool > len(queried):
                notices.append(f"조회 상한 {self.max_members}대 — 상태 나쁜 순으로 골랐습니다")
        thresholds = {}
        for metric, threshold in (self._loaded_settings().config.sms_thresholds or {}).items():
            warn, crit = getattr(threshold, "warn", None), getattr(threshold, "crit", None)
            if warn is not None or crit is not None:
                thresholds[str(metric)] = {"warn": warn, "crit": crit}
        series = list(core.get("series") or [])
        sms, apm = core.get("sms") or {}, core.get("apm") or {}
        return {
            "service_id": service_id,
            "window": spec.name,
            "compare": compare,
            "generated_at": core.get("generated_at"),
            "range": core.get("range"),
            "memo": memo,
            "selection": {
                "mode": mode,
                "queried": len(queried),
                "members": len(visible),
                "max": self.max_members,
            },
            "hidden_member_count": int(hidden_member_count or 0),
            "members": [
                {
                    "id": m.get("id"),
                    "kind": m.get("kind"),
                    "member_key": m.get("member_key"),
                    "name": _member_name(m),
                    "layer": m.get("layer"),
                    "zone": m.get("zone"),
                    "state": m.get("state"),
                    "state_label": m.get("state_label"),
                    "recent_alarms": _recent_alarms(m),
                    "open_incidents": _int((m.get("alarms") or {}).get("open_incidents")) or 0,
                    "last_alarm_at": (m.get("alarms") or {}).get("last_alarm_at"),
                    "supported": _text(m.get("kind")) in SUPPORTED_KINDS,
                    "queried": _int(m.get("id")) in queried,
                }
                for m in ordered
            ],
            "series": series,
            "sms": dict(sms),
            "apm": dict(apm),
            # SMS 「설정 임계」만(APM 임계선은 긋지 않는다 — D-335 ①)
            "thresholds": thresholds,
            "links": [dict(e) for e in links],
            "partial": bool(core.get("failed")),
            "notices": notices,
        }


def build_biz_metrics_history(config: Any, *, redis: Any = None) -> BizMetricsHistory | None:
    """라우트용 조립 — 플래그가 꺼져 있으면 None(게이트웨이 클라이언트·SQL 실행기 생성 0).

    Args:
        redis: 업무 서비스 Redis(`app.state.biz_service_cache.redis`) — 메모 `bizmon:hist:*`
    """
    if not history_active(config):
        return None
    from noise_gate.application.biz_service_config import load_biz_service_config
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_scanners import GatewayToolCaller
    from noise_gate.infrastructure.apm_host_map_sql_scanners import registry_executor

    ng = config.noise_gate
    client = build_apm_gateway_client(config)
    gateway = GatewayToolCaller(client, timeout=BUDGET_SECONDS) if client is not None else None
    return BizMetricsHistory(
        redis=redis,
        gateway=gateway,
        execute=registry_executor(config),
        settings_loader=load_biz_service_config,
        max_members=int(ng.biz_monitor_history_max_members),
        max_concurrency=int(ng.biz_monitor_history_max_concurrency),
    )


__all__ = [
    "APM_SERIES_TOOL",
    "BUDGET_SECONDS",
    "BUSY_DETAIL",
    "COMPARE_YESTERDAY",
    "DEFAULT_WINDOW",
    "HISTORY_OWNER",
    "MEMO_PREFIX",
    "MEMO_TTL_SECONDS",
    "SUPPORTED_KINDS",
    "WINDOWS",
    "BizMetricsHistory",
    "HistoryBusy",
    "WindowSpec",
    "build_biz_metrics_history",
    "history_active",
    "memo_key",
    "selection_order",
    "window_spec",
]
