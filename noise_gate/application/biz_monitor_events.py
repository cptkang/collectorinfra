"""업무 현황 상세 「이벤트」 — 통합 이벤트 타임라인 (plans/215 §5.2 ⑥ · §6 P2 · §9 · D-368).

서비스 1개의 최근 창(1h·6h·24h) 사건을 소스별로 모아 시각 순 행으로 합친다.

- **폴스타 알람 · 제니퍼 이벤트**(+ 노이즈 판정 전달/억제/강등 칩): 결정 이력
  (`DecisionStore.iter_recent_decisions`)을 W6 집계와 같은 귀속(`RedisBizServiceCache.attribute`
  — 폴스타 연계 → APM 인스턴스 → APM 도메인 → 호스트 · 첫 성립에서 멈춤)으로 이 서비스에 맞는
  기록만 고른다. 귀속 조회는 이 서비스 구성원 키와 겹치는 기록에만 건다(겹치지 않으면 이 서비스로
  귀속될 수 없다). 구성원은 W6 `_member_of`가 정한다.
- **서비스 상태 전이**: `biz_service_state_log`(W7 기록) — 서비스 단위라 존 권한 밖 구성원이 있으면
  보이지 않는다(추이 띠와 같은 규칙).
- **조사 시작/완료**: 본체 조사 저장소 — 그 조사의 알람이 이 서비스 구성원에 귀속됐거나 대상
  서버(`subject`)가 SMS 구성원과 같은 호스트일 때만(구성원 서버의 조사만). 알람 없는 사용자 요청
  조사는 소유자·운영자에게만.
- **변경 이력**(옵트인 `NOISE_BIZ_MONITOR_CHANGES_ENABLED`): 존(db_id)별 `change_feed` 고정 SELECT
  1회(구성원 자원 id ≤ `CHANGE_RESOURCE_CAP` · 60초 메모 · PostgreSQL 존만 — 그 밖은 「변경 이력
  미지원」 고지). 플래그가 꺼져 있으면 읽기기를 주입하지 않아 호출 0이다. `event_time`은 epoch 초로
  읽는다(단위 미확정 — W0-16 확인 전에는 켜지 않는다).

수집 결과(존·억제 필터 전)는 (서비스, 창)마다 **10초 메모**로 나눠 쓰고, 존 필터 · 억제 규칙 ·
마스킹 · 묶음(같은 분 · 같은 구성원 · 같은 알람 → 1행 + `count`) · 행 상한(≤ `MAX_ROWS`)은 요청마다
`build_events`가 건다. 「장애 직전 변경」은 이상 시작(상태 전이가 위험·주의로 들어간 최근 시각 —
없으면 창 안 첫 전달 알람) 전 lookback 창 안 변경이며 `pinned`로 머리에 고정한다(상관 ≠ 인과).

계층: application — 관측 시스템 호출은 변경 이력(옵트인) 1종뿐이고 그것도 주입된 읽기기로만 ·
LLM 0.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from src.domain.host_names import canonical_host_key

from noise_gate.application.biz_investigation_context import member_zone
from noise_gate.application.biz_monitor_alarms import (
    _TIER_HANDLING,
    HANDLING_DELIVERED,
    HANDLING_DEMOTED,
    HANDLING_OTHER,
    HANDLING_SUPPRESSED,
    SEVERITY_KEYS,
    _alarm_zone,
    _epoch,
    _identity,
    _member_of,
    iso_of,
    zone_visible,
)
from noise_gate.domain.alarm import is_apm_db_id
from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, SCOPE_WORKLOAD, match_key_for
from noise_gate.domain.biz_service_keys import (
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_domain_member_key,
    apm_instance_member_key,
    sms_member_key,
)
from noise_gate.domain.biz_service_state import STATE_CAUTION, STATE_LABELS, STATE_RISK
from noise_gate.domain.severity import coerce_severity

logger = logging.getLogger(__name__)

#: 수집 메모 유효 시간(초 · §6 P2).
MEMO_TTL_SECONDS = 10.0
#: 변경 이력 메모 유효 시간(초 · §7).
CHANGE_MEMO_TTL_SECONDS = 60.0
#: 응답 행 상한(넘으면 `truncated` — 화면은 「최근 500건만」).
MAX_ROWS = 500
#: 묶기 전 알람 사실 상한(넘으면 스캔을 멈추고 고지 — 최신부터 모은다).
RAW_ALARM_CAP = 5000
#: 상태 전이 한 번 읽기 상한(저장소 페이지 상한과 같다).
STATE_LOG_LIMIT = 100
#: 변경 이력 존 하나의 구성원 자원 id 상한.
CHANGE_RESOURCE_CAP = 500

#: 창 키 → 초.
WINDOWS: Mapping[str, int] = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600}
DEFAULT_WINDOW = "1h"

SOURCE_POLESTAR = "polestar_alarm"
SOURCE_APM = "apm_event"
SOURCE_STATE = "state_change"
SOURCE_INVESTIGATION = "investigation"
SOURCE_CHANGE = "change"
SOURCES: tuple[str, ...] = (
    SOURCE_POLESTAR, SOURCE_APM, SOURCE_STATE, SOURCE_INVESTIGATION, SOURCE_CHANGE,
)
SOURCE_LABELS: Mapping[str, str] = {
    SOURCE_POLESTAR: "폴스타 알람",
    SOURCE_APM: "제니퍼 이벤트",
    SOURCE_STATE: "서비스 상태 전이",
    SOURCE_INVESTIGATION: "조사",
    SOURCE_CHANGE: "변경 이력",
}
_ALARM_SOURCES = frozenset({SOURCE_POLESTAR, SOURCE_APM})

HANDLING_LABELS: Mapping[str, str] = {
    HANDLING_DELIVERED: "전달",
    HANDLING_SUPPRESSED: "억제",
    HANDLING_DEMOTED: "강등",
    HANDLING_OTHER: "기타",
}
SEVERITY_LABELS: Mapping[str, str] = {
    "critical": "심각", "warning": "경고", "attention": "주의", "clear": "해소",
}

#: 조사 상태 → 완료 행 문구(이 밖의 상태는 아직 진행 중 — 완료 행 없음).
_INVESTIGATION_END_LABELS: Mapping[str, str] = {
    "done": "조사 완료",
    "failed": "조사 실패",
    "timeout": "조사 시간 초과",
    "rejected": "조사 거부",
}
_INVESTIGATION_KIND_LABELS: Mapping[str, str] = {
    "alarm": "알람 조사", "episode": "사건 조사", "diagnosis": "추가 조사",
    "manual": "알람 다시 분석",
}
#: 알람 없이 소유자가 정해지는 사용자 요청 조사 kind(조사 저장소 `USER_REQUEST_KINDS`와 같다).
_USER_REQUEST_KINDS = frozenset({"diagnosis", "manual"})

STATE_HIDDEN_NOTICE = "권한 밖 구성원 {n}대가 있어 서비스 상태 전이를 보이지 않습니다"
PINNED_NOTE = "이상 시작 전 {m}분 안 변경 — 시간이 가까울 뿐 원인이라는 뜻은 아닙니다(상관 ≠ 인과)"

_DIGITS = re.compile(r"^\d{1,19}$")

#: 변경 이력 읽기기 — `(db_id, 자원 id 목록, 창 초, 기준 epoch 초)` → `(변경 목록, 실패 사유)`.
ChangeReader = Callable[[str, list[str], int, int], Awaitable[tuple[list[Any], str]]]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def window_seconds(window: str) -> int:
    """창 키(1h·6h·24h) → 초(모르는 값은 `ValueError`)."""
    if window not in WINDOWS:
        raise ValueError(f"지원하지 않는 기간입니다: {window} (가능: {', '.join(WINDOWS)})")
    return WINDOWS[window]


def parse_sources(raw: str | None) -> frozenset[str]:
    """쉼표 소스 목록 → 집합(빈 값 = 전체 · 모르는 값은 `ValueError`)."""
    items = [s.strip() for s in _text(raw).split(",") if s.strip()]
    unknown = sorted(set(items) - set(SOURCES))
    if unknown:
        raise ValueError(
            f"지원하지 않는 소스입니다: {', '.join(unknown)} (가능: {', '.join(SOURCES)})"
        )
    return frozenset(items) if items else frozenset(SOURCES)


# ── 사실 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class EventFact:
    """타임라인 행 재료 1건(존·억제 필터 · 묶음 전).

    `group_key`는 같은 알람 판별 키(알람 지문 — 없으면 알람명 · 알람 아닌 행은 빈 값 = 묶지 않음)다.
    `owner_sub`가 있으면 알람 없는 사용자 요청 조사라 소유자·운영자에게만 보인다.
    """

    at: float
    source: str
    summary: str
    member_key: str = ""
    zone: str = ""
    severity: str | None = None
    handling: str | None = None
    group_key: str = ""
    alarm_id: str = ""
    investigation_id: str = ""
    change: Mapping[str, Any] | None = None
    owner_sub: str = ""
    service_level: bool = False


@dataclass(frozen=True)
class EventSet:
    """수집 1회분(서비스 1개 · 창 1개 · 존·억제 필터 전)."""

    computed_at: float
    window_seconds: int
    facts: tuple[EventFact, ...] = ()
    notices: tuple[str, ...] = ()
    alarms_truncated: bool = False
    availability: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    anomaly_start: Mapping[str, Any] | None = None


# ── 변경 이력 읽기기(executor → change_feed) ─────────────────────────────
class _ExecClient:
    """`change_feed`가 받는 client 꼴 — 레지스트리 실행기(`SQLGuard` · readonly) 위에 얹는다.

    `change_feed`는 실패를 빈 목록으로 삼키므로 실패 여부는 여기서 기억해 사유로 올린다
    (침묵 금지).
    """

    def __init__(self, execute: Callable[[str, str], Awaitable[list[dict[str, Any]]]],
                 db_id: str) -> None:
        self._execute = execute
        self._db_id = db_id
        self.failed = False

    async def execute_sql(self, sql: str) -> Any:
        try:
            rows = await self._execute(self._db_id, sql)
        except Exception:
            self.failed = True
            raise
        return SimpleNamespace(rows=rows)


def change_reader_from_executor(
    execute: Callable[[str, str], Awaitable[list[dict[str, Any]]]],
) -> ChangeReader:
    """레지스트리 실행기 → 변경 이력 읽기(`change_feed.ChangeFeed` 재사용 · 존당 SELECT 1건)."""
    from noise_gate.infrastructure.change_feed import ChangeFeed

    feed = ChangeFeed()

    async def _read(
        db_id: str, resource_ids: list[str], window: int, reference_epoch: int
    ) -> tuple[list[Any], str]:
        client = _ExecClient(execute, db_id)
        rows = await feed.fetch_recent_changes(
            client, db_id, window, reference_epoch=reference_epoch, resource_ids=resource_ids,
        )
        return rows, (f"변경 이력 조회 실패({db_id})" if client.failed else "")

    return _read


# ── 수집 ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _MemberIndex:
    """서비스 구성원 키 색인 — 귀속 조회를 걸 기록 고르기 · 이름 · 존."""

    sms: frozenset[str]
    apm: frozenset[str]
    apm_domain: frozenset[str]
    hosts: Mapping[str, Mapping[str, Any]]
    by_key: Mapping[str, Mapping[str, Any]]


def _member_index(service: Mapping[str, Any]) -> _MemberIndex:
    members = [m for m in service.get("members") or () if isinstance(m, Mapping)]
    by_kind: dict[str, set[str]] = {KIND_SMS_SERVER: set(), KIND_APM_INSTANCE: set(),
                                    KIND_APM_DOMAIN: set()}
    hosts: dict[str, Mapping[str, Any]] = {}
    for m in members:
        kind = _text(m.get("kind"))
        if kind in by_kind:
            by_kind[kind].add(_text(m.get("member_key")))
        if kind == KIND_SMS_SERVER:
            for raw in (m.get("hostname"), m.get("server_name")):
                hk = canonical_host_key(_text(raw))
                if hk:
                    hosts.setdefault(hk, m)
    return _MemberIndex(
        frozenset(by_kind[KIND_SMS_SERVER]), frozenset(by_kind[KIND_APM_INSTANCE]),
        frozenset(by_kind[KIND_APM_DOMAIN]), hosts,
        {_text(m.get("member_key")): m for m in members if _text(m.get("member_key"))},
    )


def _may_belong(ident: Any, idx: _MemberIndex) -> bool:
    """귀속 입력이 이 서비스 구성원 키와 하나라도 겹치는가(안 겹치면 이 서비스로 귀속될 수 없다)."""
    targets, apm, host, _sms_db = ident
    if any(sms_member_key(d, r) in idx.sms for d, r in targets):
        return True
    if apm is not None:
        for scope in (SCOPE_INSTANCE, SCOPE_WORKLOAD):
            key = apm_instance_member_key(apm[0], apm[1], scope, match_key_for(scope, apm[2]))
            if key in idx.apm:
                return True
        if apm_domain_member_key(apm[0], apm[1]) in idx.apm_domain:
            return True
    return bool(host) and canonical_host_key(host) in idx.hosts


def _severity_key(raw: Any) -> str:
    sev, _ = coerce_severity(raw)
    return SEVERITY_KEYS.get(sev, "warning")


class BizEventTimeline:
    """통합 이벤트 타임라인 수집기 + 10초 메모(프로세스 안 1개 — 라우트가 `app.state`에 둔다)."""

    def __init__(
        self,
        *,
        cache: Any,  # RedisBizServiceCache | None
        decision_store: Any,  # DecisionStore | None
        investigation_store: Any,  # InvestigationStore | None
        repo: Any,  # PostgresBizServiceRepository | None
        change_reader: ChangeReader | None = None,
        engine_of: Callable[[str], str] | None = None,
        change_lookback_minutes: int = 60,
        ttl_seconds: float = MEMO_TTL_SECONDS,
        change_ttl_seconds: float = CHANGE_MEMO_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        """의존성을 받는다 — `change_reader`가 None이면(변경 플래그 꺼짐) 변경 이력을 안 읽는다."""
        self._cache = cache
        self._decisions = decision_store
        self._investigations = investigation_store
        self._repo = repo
        self._change_reader = change_reader
        self._engine_of = engine_of or (lambda _db: "postgresql")
        self.change_lookback_minutes = max(1, int(change_lookback_minutes))
        self._ttl = float(ttl_seconds)
        self._change_ttl = float(change_ttl_seconds)
        self._monotonic = monotonic
        self._wall = wall
        self._lock = asyncio.Lock()
        self._memo: dict[tuple[int, int], tuple[float, EventSet]] = {}
        self._change_memo: dict[
            tuple[int, int], tuple[float, tuple[list[EventFact], list[str]]]
        ] = {}

    @property
    def changes_enabled(self) -> bool:
        """변경 이력 읽기기가 주입됐는가(= 변경 플래그 켜짐)."""
        return self._change_reader is not None

    async def collect(self, service: Mapping[str, Any], *, window: int) -> EventSet:
        """메모가 살아 있으면 그대로, 아니면 다시 모은다(동시 요청은 1회만)."""
        key = (int(service.get("id") or 0), int(window))
        hit = self._memo.get(key)
        if hit is not None and self._monotonic() - hit[0] < self._ttl:
            return hit[1]
        async with self._lock:
            hit = self._memo.get(key)
            now_m = self._monotonic()
            if hit is not None and now_m - hit[0] < self._ttl:
                return hit[1]
            result = await self._compute(service, int(window))
            self._memo = {k: v for k, v in self._memo.items() if now_m - v[0] < self._ttl}
            self._memo[key] = (self._monotonic(), result)
            return result

    async def _compute(self, service: Mapping[str, Any], window: int) -> EventSet:
        now = self._wall()
        since = now - window
        sid = int(service.get("id") or 0)
        idx = _member_index(service)
        notices: list[str] = []
        avail: dict[str, dict[str, Any]] = {}
        alarms, alarm_members, truncated = await self._alarm_facts(
            service, idx, window, since, notices, avail)
        states, anomaly = await self._state_facts(sid, since, notices, avail)
        investigations = await self._investigation_facts(idx, alarm_members, since, notices, avail)
        if anomaly is None:
            first = min((f.at for f in alarms if f.handling == HANDLING_DELIVERED), default=None)
            if first is not None:
                anomaly = {"at": iso_of(first), "basis": "first_delivered_alarm"}
        changes = await self._change_facts(sid, idx, window, now, anomaly, notices, avail)
        facts = tuple(alarms + states + investigations + changes)
        return EventSet(now, window, facts, tuple(notices), truncated, avail, anomaly)

    async def _alarm_facts(
        self, service: Mapping[str, Any], idx: _MemberIndex, window: int, since: float,
        notices: list[str], avail: dict[str, dict[str, Any]],
    ) -> tuple[list[EventFact], dict[str, Mapping[str, Any]], bool]:
        store = self._decisions
        if store is None or getattr(store, "enabled", True) is False:
            reason = "결정 이력 꺼짐 — 알람 행 없음"
            for src in (SOURCE_POLESTAR, SOURCE_APM):
                avail[src] = {"available": False, "reason": reason}
            notices.append(reason)
            return [], {}, False
        for src in (SOURCE_POLESTAR, SOURCE_APM):
            avail[src] = {"available": True, "reason": ""}

        def _scan() -> tuple[list[dict[str, Any]], bool]:
            picked: list[dict[str, Any]] = []
            for rec in store.iter_recent_decisions(window):
                if not isinstance(rec, dict):
                    continue
                if _may_belong(_identity(rec), idx):
                    if len(picked) >= RAW_ALARM_CAP:
                        return picked, True
                    picked.append(rec)
            return picked, False

        try:
            records, truncated = await asyncio.to_thread(_scan)
        except Exception as exc:  # noqa: BLE001 — 읽기 실패는 고지(침묵 금지)
            logger.warning("업무 현황 이벤트 — 결정 이력 읽기 실패: %s", exc)
            notices.append("결정 이력 읽기 실패 — 알람 행 없음")
            return [], {}, False
        if truncated:
            notices.append(f"알람 최신 {RAW_ALARM_CAP}건만 모았습니다")
        if records and self._cache is None:
            notices.append("업무 서비스 사본 없음 — 알람을 서비스에 귀속하지 못했습니다")
            return [], {}, truncated
        sid = int(service.get("id") or 0)
        memo: dict[Any, Any] = {}
        facts: list[EventFact] = []
        members: dict[str, Mapping[str, Any]] = {}
        for rec in records:
            ident = _identity(rec)
            if ident not in memo:
                targets, apm, host, sms_db = ident
                memo[ident] = await self._cache.attribute(
                    polestar_link_targets=list(targets) or None, apm=apm, hostname=host or None,
                    db_id=sms_db or None)
            hit = memo[ident]
            if getattr(hit, "service_id", None) != sid:
                continue
            at = _epoch(rec.get("ts"))
            if at is None or at < since:
                continue
            member = _member_of(service, _text(getattr(hit, "basis", "")), ident)
            db_id = _text(rec.get("db_id"))
            alarm_id = _text(rec.get("alarm_id"))
            if member is not None and alarm_id:
                members.setdefault(alarm_id, member)
            facts.append(EventFact(
                at=at,
                source=(SOURCE_APM if (rec.get("apm_ref") or is_apm_db_id(db_id))
                        else SOURCE_POLESTAR),
                summary=_text(rec.get("alarm_name")) or "알람",
                member_key=_text(member.get("member_key")) if member is not None else "",
                zone=member_zone(member) if member is not None else _alarm_zone(db_id),
                severity=_severity_key((rec.get("signals") or {}).get("severity")),
                handling=_TIER_HANDLING.get(_text(rec.get("tier")).lower(), HANDLING_OTHER),
                group_key=_text(rec.get("fingerprint")) or _text(rec.get("alarm_name")) or alarm_id,
                alarm_id=alarm_id,
            ))
        return facts, members, truncated

    async def _state_facts(
        self, sid: int, since: float, notices: list[str], avail: dict[str, dict[str, Any]],
    ) -> tuple[list[EventFact], dict[str, Any] | None]:
        if self._repo is None:
            avail[SOURCE_STATE] = {"available": False, "reason": "상태 전이 저장소 없음"}
            notices.append("상태 전이 저장소 없음 — 상태 전이 행 없음")
            return [], None
        avail[SOURCE_STATE] = {"available": True, "reason": ""}
        try:
            rows = await self._repo.list_state_log(
                sid, since=datetime.fromtimestamp(since, UTC), limit=STATE_LOG_LIMIT)
        except Exception:  # noqa: BLE001 — 사유로 돌려준다
            logger.warning("업무 현황 이벤트 — 상태 전이 조회 실패: service=%s", sid, exc_info=True)
            notices.append("상태 전이 조회 실패 — 상태 전이 행 없음")
            return [], None
        if len(rows) >= STATE_LOG_LIMIT:
            notices.append(f"상태 전이 최신 {STATE_LOG_LIMIT}건만")
        facts: list[EventFact] = []
        anomaly: dict[str, Any] | None = None
        bad = {STATE_RISK, STATE_CAUTION}
        for row in rows:  # 최신부터
            at = _epoch(row.get("at"))
            if at is None:
                continue
            to_state = _text(row.get("to_state"))
            from_state = _text(row.get("from_state"))
            label_to = STATE_LABELS.get(to_state, "판정 불가")
            label_from = STATE_LABELS.get(from_state, "") if from_state else ""
            facts.append(EventFact(
                at=at, source=SOURCE_STATE,
                summary=f"{label_from} → {label_to}" if label_from else f"→ {label_to}",
                severity={STATE_RISK: "critical", STATE_CAUTION: "warning"}.get(to_state),
                service_level=True,
            ))
            if anomaly is None and to_state in bad and from_state not in bad:
                anomaly = {"at": iso_of(at), "basis": "state_log"}
        return facts, anomaly

    async def _investigation_facts(
        self, idx: _MemberIndex, alarm_members: Mapping[str, Mapping[str, Any]], since: float,
        notices: list[str], avail: dict[str, dict[str, Any]],
    ) -> list[EventFact]:
        store = self._investigations
        if store is None:
            avail[SOURCE_INVESTIGATION] = {"available": False, "reason": "조사 저장소 없음"}
            return []
        avail[SOURCE_INVESTIGATION] = {"available": True, "reason": ""}

        def _scan() -> list[dict[str, Any]]:
            return list(store.list_requests(kinds=tuple(_INVESTIGATION_KIND_LABELS)))

        try:
            records = await asyncio.to_thread(_scan)
        except Exception as exc:  # noqa: BLE001 — 읽기 실패는 고지
            logger.warning("업무 현황 이벤트 — 조사 저장소 읽기 실패: %s", exc)
            notices.append("조사 저장소 읽기 실패 — 조사 행 없음")
            return []
        facts: list[EventFact] = []
        for rec in records:
            inv_id = _text(rec.get("investigation_id"))
            if not inv_id:
                continue
            alarm_ids = [_text(a) for a in rec.get("alarm_ids") or () if _text(a)]
            member = next((alarm_members[a] for a in alarm_ids if a in alarm_members), None)
            if member is None:
                raw_subject = rec.get("subject")
                subject: Mapping[str, Any] = raw_subject if isinstance(raw_subject, Mapping) else {}
                for raw in (subject.get("hostname"), subject.get("server_name")):
                    hk = canonical_host_key(_text(raw))
                    if hk and hk in idx.hosts:
                        member = idx.hosts[hk]
                        break
            if member is None:
                continue  # 구성원 서버의 조사만
            kind = _text(rec.get("kind")) or "alarm"
            user_only = kind in _USER_REQUEST_KINDS and not alarm_ids
            owner = _text(rec.get("owner_sub")) if user_only else ""
            base: dict[str, Any] = {
                "source": SOURCE_INVESTIGATION,
                "member_key": _text(member.get("member_key")),
                "zone": member_zone(member),
                "investigation_id": inv_id,
                "owner_sub": owner,
            }
            kind_label = _INVESTIGATION_KIND_LABELS.get(kind, "조사")
            started = _epoch(rec.get("submitted_at"))
            if started is not None and started >= since:
                facts.append(EventFact(at=started, summary=f"조사 시작 · {kind_label}", **base))
            end_label = _INVESTIGATION_END_LABELS.get(_text(rec.get("status")))
            ended = _epoch(rec.get("ts"))
            if end_label and ended is not None and ended >= since:
                facts.append(EventFact(at=ended, summary=f"{end_label} · {kind_label}", **base))
        return facts

    async def _change_facts(
        self, sid: int, idx: _MemberIndex, window: int, now: float,
        anomaly: Mapping[str, Any] | None, notices: list[str],
        avail: dict[str, dict[str, Any]],
    ) -> list[EventFact]:
        if self._change_reader is None:
            avail[SOURCE_CHANGE] = {"available": False, "reason": "변경 이력 꺼짐"}
            return []
        lookback = self.change_lookback_minutes * 60
        key = (sid, window)
        hit = self._change_memo.get(key)
        if hit is not None and self._monotonic() - hit[0] < self._change_ttl:
            facts, reasons = hit[1]
        else:
            facts, reasons = await self._read_changes(idx, window + lookback, now)
            self._change_memo[key] = (self._monotonic(), (facts, reasons))
        avail[SOURCE_CHANGE] = {"available": True, "reason": ""}
        notices.extend(reasons)
        start = _epoch(anomaly.get("at")) if anomaly else None
        out: list[EventFact] = []
        for f in facts:
            pinned = start is not None and start - lookback <= f.at <= start
            if not pinned and f.at < now - window:
                continue
            change = dict(f.change or {})
            change["pinned"] = pinned
            out.append(EventFact(
                at=f.at, source=SOURCE_CHANGE, summary=f.summary, member_key=f.member_key,
                zone=f.zone, change=change,
            ))
        return out

    async def _read_changes(
        self, idx: _MemberIndex, window: int, now: float
    ) -> tuple[list[EventFact], list[str]]:
        """존(db_id)별 고정 SELECT 1회 — PostgreSQL만 · 자원 id 정수만 · 존마다 ≤ 상한."""
        assert self._change_reader is not None
        by_db: dict[str, dict[str, Mapping[str, Any]]] = {}
        for key, m in idx.by_key.items():
            if _text(m.get("kind")) != KIND_SMS_SERVER:
                continue
            rid = _text(m.get("resource_id"))
            if _DIGITS.match(rid):
                by_db.setdefault(_text(m.get("db_id")), {})[rid] = m
        facts: list[EventFact] = []
        reasons: list[str] = []
        for db_id in sorted(by_db):
            if not db_id:
                continue
            engine = _text(self._engine_of(db_id)).lower()
            if engine != "postgresql":
                reasons.append(f"변경 이력 미지원 — {db_id}({engine or '엔진 미상'})")
                continue
            members = by_db[db_id]
            ids = sorted(members, key=int)
            if len(ids) > CHANGE_RESOURCE_CAP:
                reasons.append(f"변경 이력 — {db_id} 구성원 {CHANGE_RESOURCE_CAP}대만 조회")
                ids = ids[:CHANGE_RESOURCE_CAP]
            try:
                rows, reason = await self._change_reader(db_id, ids, int(window), int(now))
            except Exception as exc:  # noqa: BLE001 — 사유로 돌려준다
                logger.warning("업무 현황 이벤트 — 변경 이력 조회 실패: db_id=%s: %s", db_id, exc)
                rows, reason = [], f"변경 이력 조회 실패({db_id})"
            if reason:
                reasons.append(reason)
            for ev in rows:
                member = members.get(_text(getattr(ev, "resource_id", "")))
                if member is None:
                    continue
                change_type = _text(getattr(ev, "change_type", ""))
                facts.append(EventFact(
                    at=float(getattr(ev, "event_time", 0) or 0),
                    source=SOURCE_CHANGE,
                    summary=change_type or "변경",
                    member_key=_text(member.get("member_key")),
                    zone=member_zone(member),
                    change={
                        "change_type": change_type,
                        "resource_type": _text(getattr(ev, "resource_type", "")),
                        "description": _text(getattr(ev, "description", "")),
                    },
                ))
        return facts, reasons


# ── 응답 조립 ───────────────────────────────────────────────────────────
def _row_of(f: EventFact, names: Mapping[str, str], mask: Callable[[str], str] | None,
            count: int) -> dict[str, Any]:
    def _m(value: str) -> str:
        return mask(value) if mask is not None and value else value

    row: dict[str, Any] = {
        "at": iso_of(f.at),
        "source": f.source,
        "source_label": SOURCE_LABELS[f.source],
        "member_key": f.member_key,
        "member_name": names.get(f.member_key, ""),
        "summary": _m(f.summary),
        "severity": f.severity,
        "severity_label": SEVERITY_LABELS.get(f.severity or "", ""),
        "handling": f.handling,
        "handling_label": HANDLING_LABELS.get(f.handling or "", ""),
        "count": count,
    }
    if f.alarm_id:
        row["alarm_id"] = f.alarm_id
    if f.investigation_id:
        row["investigation_id"] = f.investigation_id
    if f.change is not None:
        change = dict(f.change)
        row["pinned"] = bool(change.pop("pinned", False))
        change["description"] = _m(_text(change.get("description")))
        row["change"] = change
    return row


def build_events(
    events: EventSet,
    *,
    service_id: int,
    window: str,
    visible_members: Sequence[Mapping[str, Any]],
    hidden_member_count: int,
    scope: Collection[str] | None,
    sources: Collection[str],
    include_suppressed: bool,
    operator: bool,
    user_sub: str,
    mask: Callable[[str], str] | None,
    change_lookback_minutes: int,
) -> dict[str, Any]:
    """요청 1건의 타임라인 응답 — 존 필터 · 억제 규칙 · 묶음 · 행 상한 · 마스킹(`mask`가 있으면).

    억제 판정 행은 `operator`이고 `include_suppressed`일 때만 싣는다(사용자 경로 기본 = 없음).
    권한 밖 구성원 행은 `hidden_row_count`로만 센다. 서비스 단위 행(상태 전이)은 존 범위가 제한된
    요청자에게 권한 밖 구성원이 있으면 보이지 않는다.
    """
    names: dict[str, str] = {}
    for m in visible_members:
        key = _text(m.get("member_key"))
        names[key] = _text(m.get("name")) or _text(m.get("server_name")) or key
    show_suppressed = bool(operator and include_suppressed)
    restricted_state = scope is not None and hidden_member_count > 0
    notices = list(events.notices)
    hidden_rows = 0
    kept: list[EventFact] = []
    for f in events.facts:
        if f.source not in sources:
            continue
        if f.handling == HANDLING_SUPPRESSED and not show_suppressed:
            continue
        if f.owner_sub and not operator and f.owner_sub != user_sub:
            continue
        if f.service_level:
            if restricted_state:
                continue
        elif f.member_key:
            if f.member_key not in names:
                hidden_rows += 1
                continue
        elif not zone_visible(scope, f.zone):
            hidden_rows += 1
            continue
        kept.append(f)
    if restricted_state and SOURCE_STATE in sources and any(f.service_level for f in events.facts):
        notices.append(STATE_HIDDEN_NOTICE.format(n=hidden_member_count))
    # 같은 분 · 같은 구성원 · 같은 알람(· 같은 소스 · 같은 판정) → 1행 + count(최신 시각·알람 id)
    groups: dict[tuple[Any, ...], list[EventFact]] = {}
    singles: list[tuple[EventFact, int]] = []
    for f in kept:
        if f.source in _ALARM_SOURCES and f.group_key:
            gkey = (f.source, int(f.at // 60), f.member_key, f.group_key, f.handling)
            groups.setdefault(gkey, []).append(f)
        else:
            singles.append((f, 1))
    rank = {k: i for i, k in enumerate(("clear", "attention", "warning", "critical"))}
    for group in groups.values():
        latest = max(group, key=lambda g: g.at)
        worst = max((g.severity for g in group), key=lambda s: rank.get(s or "", -1))
        singles.append((replace(latest, severity=worst), len(group)))
    pinned = [(f, n) for f, n in singles if f.change is not None and f.change.get("pinned")]
    rest = [(f, n) for f, n in singles if not (f.change is not None and f.change.get("pinned"))]
    pinned.sort(key=lambda p: -p[0].at)
    rest.sort(key=lambda p: -p[0].at)
    ordered = pinned + rest
    total = len(ordered)
    rows = [_row_of(f, names, mask, n) for f, n in ordered[:MAX_ROWS]]
    avail = {
        src: {"label": SOURCE_LABELS[src], **events.availability.get(
            src, {"available": False, "reason": ""})}
        for src in SOURCES
    }
    return {
        "generated_at": iso_of(events.computed_at),
        "service_id": int(service_id),
        "window": window,
        "window_seconds": events.window_seconds,
        "sources_available": avail,
        "rows": rows,
        "total_rows": total,
        "truncated": total > MAX_ROWS or events.alarms_truncated,
        "hidden_member_count": int(hidden_member_count),
        "hidden_row_count": hidden_rows,
        "include_suppressed": show_suppressed,
        "anomaly_start": dict(events.anomaly_start) if events.anomaly_start else None,
        "change_lookback_minutes": int(change_lookback_minutes),
        "pinned_note": PINNED_NOTE.format(m=int(change_lookback_minutes)) if pinned else "",
        "notices": notices,
    }


__all__ = [
    "CHANGE_MEMO_TTL_SECONDS",
    "CHANGE_RESOURCE_CAP",
    "DEFAULT_WINDOW",
    "MAX_ROWS",
    "MEMO_TTL_SECONDS",
    "RAW_ALARM_CAP",
    "SOURCES",
    "SOURCE_APM",
    "SOURCE_CHANGE",
    "SOURCE_INVESTIGATION",
    "SOURCE_LABELS",
    "SOURCE_POLESTAR",
    "SOURCE_STATE",
    "WINDOWS",
    "BizEventTimeline",
    "ChangeReader",
    "EventFact",
    "EventSet",
    "build_events",
    "change_reader_from_executor",
    "parse_sources",
    "window_seconds",
]
