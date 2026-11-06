"""업무 현황 M2 알람 집계 · 현황 보기 조립 (plans/215 §4.2 · §4.4 · §5.2 ①② · D-368).

M2(조회 추가 0): 열린 사건(`IncidentStore.list_open`) + 결정 이력 최근 창
(`DecisionStore.iter_recent_decisions` · `NOISE_BIZ_MONITOR_ALARM_WINDOW_MINUTES`)을 업무 서비스
Redis 사본(`RedisBizServiceCache.attribute` — 폴스타 연계 → APM 인스턴스 → APM 도메인 → 호스트 ·
첫 성립에서 멈춤)으로 귀속한다. 관측 시스템(MCP·DB·게이트웨이)은 부르지 않는다.

- 집계는 요청 시 계산하고 **10초 메모**(프로세스 안 · 보드가 여럿이어도 1회)로 나눠 쓴다. 메모에는
  존 필터 전 전체 결과를 두고, 존 필터는 요청마다 조립 단계에서 건다.
- 「서비스 미상」 알람은 건수로 낸다. 결정 기록에 `resource_ancestry`가 있으면 S7 트리 그룹을 뽑아
  그 그룹 서비스(`tree:` 키) 1개에 대응할 때 「트리 그룹 추정」으로 **표시만** 한다
  (집계에 넣지 않는다).
- 워커 경로·판정은 바꾸지 않는다(읽기만).

현황 보기 조립(`build_board`·`build_detail`)은 M2 결과 + M3 최신값(`bizmon:svc:{id}` — W7이
기록)으로 `judge_service_state`(§4.4)를 부르고 존 권한 밖 구성원은 이름·값 없이 건수만 남긴다.

계층: application — domain 판정·키 + infrastructure 사본(덕 타이핑 주입) · 파일 읽기는 스레드로.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from src.domain.host_names import canonical_host_key

from noise_gate.application.biz_investigation_context import _link_targets, member_zone, sms_db_of
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
from noise_gate.domain.biz_service_model import (
    ServerAttr,
    SmsServer,
    SmsThreshold,
    TreeFolder,
    norm_name,
)
from noise_gate.domain.biz_service_signals import tree_group_of, tree_service_key
from noise_gate.domain.biz_service_state import (
    STATE_CAUTION,
    STATE_LABELS,
    STATE_NO_SIGNAL,
    STATE_RISK,
    STATE_UNKNOWN,
    ApmSignalFact,
    ApmValueFact,
    IncidentFact,
    ServiceStateJudgement,
    SmsMetricFact,
    judge_service_state,
)
from noise_gate.domain.host_link import apm_source_id
from noise_gate.domain.severity import coerce_severity

logger = logging.getLogger(__name__)

#: 집계 메모 유효 시간(초 · §4.2).
MEMO_TTL_SECONDS = 10.0
#: 열린 사건 조회 상한(넘으면 고지).
OPEN_INCIDENT_LIMIT = 500

#: M3 수요 키(heartbeat가 `SET EX 120` · W7 수집기가 읽는다).
VIEWERS_KEY = "bizmon:viewers"
VIEWERS_TTL_SECONDS = 120
#: M3 최신값 키 접두(`bizmon:svc:{id}` — W7이 기록 · 여기서는 읽기만).
MONITOR_SVC_PREFIX = "bizmon:svc:"
#: M3 수집 상태 키(W7이 기록 · 없으면 「꺼짐」).
MONITOR_STATUS_KEY = "bizmon:status"

FACT_INCIDENT = "incident"
FACT_RECENT = "recent"

HANDLING_DELIVERED = "delivered"
HANDLING_SUPPRESSED = "suppressed"
HANDLING_DEMOTED = "demoted"
HANDLING_OTHER = "other"
_TIER_HANDLING = {
    "page": HANDLING_DELIVERED,
    "ticket": HANDLING_DELIVERED,
    "suppress": HANDLING_SUPPRESSED,
    "dashboard": HANDLING_DEMOTED,
}
_HANDLINGS = (HANDLING_DELIVERED, HANDLING_SUPPRESSED, HANDLING_DEMOTED, HANDLING_OTHER)
#: 심각도 0~3 → 응답 키.
SEVERITY_KEYS: Mapping[int, str] = {3: "critical", 2: "warning", 1: "attention", 0: "clear"}

#: 수집 상태 코드 → 표시 글자(「정상」 어휘 금지 — D-335 ②).
METRICS_OFF = "off"
METRICS_COLLECTING = "collecting"
METRICS_PARTIAL = "partial"
METRICS_FAILED = "failed"
METRICS_LABELS: Mapping[str, str] = {
    METRICS_OFF: "꺼짐",
    METRICS_COLLECTING: "수집 중",
    METRICS_PARTIAL: "일부만",
    METRICS_FAILED: "조회 실패",
}
METRICS_OFF_NOTE = "지표 수집 꺼짐 — 알람 현황만"
REASON_METRICS_OFF = "지표 수집 꺼짐"
REASON_METRICS_MISSING = "지표 수집 결과 없음"

#: 목록 정렬 — 상태 나쁜 순(위험 → 주의 → 판정 불가 → 이상 신호 없음).
_STATE_ORDER = {STATE_RISK: 0, STATE_CAUTION: 1, STATE_UNKNOWN: 2, STATE_NO_SIGNAL: 3}

#: 귀속 입력 `(폴스타 연계 대상, APM (source, domain, instance), 호스트, SMS DB id)`.
_Identity = tuple[tuple[tuple[str, str], ...], tuple[str, str, str] | None, str, str]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _epoch(raw: Any) -> float | None:
    """ISO 8601 → epoch 초(시간대 없으면 UTC · 실패는 None)."""
    text = _text(raw)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def iso_of(epoch: float | None) -> str | None:
    """epoch 초 → ISO 8601(UTC) · None은 None."""
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC).isoformat()


def _alarm_zone(db_id: str) -> str:
    """알람 db_id → 존 코드(레지스트리 · 미상은 빈 문자열)."""
    from src.routing.zones import db_id_to_zone

    try:
        return db_id_to_zone(db_id) or ""
    except Exception:  # noqa: BLE001 — 레지스트리 실패는 존 미상(제한 범위 사용자에게는 숨김)
        logger.debug("알람 존 해석 실패: db_id=%s", db_id, exc_info=True)
        return ""


def zone_visible(scope: Collection[str] | None, zone: str) -> bool:
    """존 범위가 이 존을 허용하는가 — None은 전 존 · 존 미상은 제한 범위에서 숨김."""
    return scope is None or (bool(zone) and zone in scope)


# ── M2 사실 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AlarmFact:
    """귀속을 마친 알람 1건 — 열린 사건(`incident`) 또는 최근 창 결정(`recent`).

    `service_id`가 None이면 「서비스 미상」이고, `estimate_service_id`는 트리 그룹 추정
    (표시 전용)이다.
    `zone`은 귀속 구성원의 존(구성원을 못 찾으면 알람 db_id의 존)이다.
    """

    kind: str
    alarm_id: str
    db_id: str
    severity: int
    tier: str
    at: float | None
    service_id: int | None = None
    basis: str = ""
    member_key: str = ""
    zone: str = ""
    estimate_service_id: int | None = None
    incident_id: str = ""


@dataclass(frozen=True)
class AlarmSnapshot:
    """M2 집계 1회분(존 필터 전) — 서비스 사본 · 사실 · 고지."""

    computed_at: float
    window_minutes: int
    services: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    facts: tuple[AlarmFact, ...] = ()
    notices: tuple[str, ...] = ()


def _identity(rec: Mapping[str, Any]) -> _Identity:
    """결정 기록 → 귀속 입력 `(폴스타 연계 대상, APM (source, domain, instance), 호스트, SMS DB)`.

    폴스타 연계는 기록 최상위 `polestar_link`(있을 때만)를, APM은 `apm_ref`(210 §3.8)를 쓴다.
    SMS DB는 APM 기록이 아닐 때 `sms_db_of(db_id)` — 호스트 귀속을 그 DB 구성원으로 좁힌다.
    """
    targets = tuple(_link_targets(rec))
    apm: tuple[str, str, str] | None = None
    ref = rec.get("apm_ref")
    if isinstance(ref, Mapping):
        source = apm_source_id(_text(rec.get("db_id")), ref.get("source_id"))
        domain = _text(ref.get("domain_id"))
        instance = _text(ref.get("instance_name")) or _text(rec.get("resource_name"))
        if source and domain and instance:
            apm = (source, domain, instance)
    host = _text(rec.get("hostname")) or _text(rec.get("server_name"))
    db_id = _text(rec.get("db_id"))
    is_apm = bool(ref) or is_apm_db_id(db_id)
    return targets, apm, host, "" if is_apm else sms_db_of(db_id)


def _member_of(
    service: Mapping[str, Any], basis: str, ident: _Identity
) -> Mapping[str, Any] | None:
    """귀속 근거 → 서비스 사본의 구성원 1개(못 찾으면 None — 서비스 단위로만 센다).

    호스트 근거는 SMS DB가 있으면 그 db_id 구성원만 보고, 맞는 서버가 2대 이상이면 고르지 않는다
    (D-224 ④ · `idx:host` 1대 규칙과 같다).
    """
    targets, apm, host, sms_db = ident
    members = [m for m in service.get("members") or () if isinstance(m, Mapping)]
    keys: set[str] = set()
    kind = ""
    if basis == "polestar_link":
        kind, keys = KIND_SMS_SERVER, {k for k in (sms_member_key(d, r) for d, r in targets) if k}
    elif basis == "apm_instance" and apm is not None:
        kind = KIND_APM_INSTANCE
        keys = {
            k for k in (
                apm_instance_member_key(apm[0], apm[1], s, match_key_for(s, apm[2]))
                for s in (SCOPE_INSTANCE, SCOPE_WORKLOAD)
            ) if k
        }
    elif basis == "apm_domain" and apm is not None:
        kind, keys = KIND_APM_DOMAIN, {k for k in (apm_domain_member_key(apm[0], apm[1]),) if k}
    elif basis == "host":
        hk = canonical_host_key(host)
        hits = [
            m for m in members
            if m.get("kind") == KIND_SMS_SERVER and hk
            and (not sms_db or _text(m.get("db_id")) == sms_db)
            and hk in {
                canonical_host_key(m.get("hostname")), canonical_host_key(m.get("server_name"))
            }
        ]
        return hits[0] if len({_text(m.get("member_key")) for m in hits}) == 1 else None
    for m in members:
        if m.get("kind") == kind and _text(m.get("member_key")) in keys:
            return m
    return None


def _ancestry_tree_service(
    rec: Mapping[str, Any], services_by_key: Mapping[str, int], generic: Collection[str]
) -> int | None:
    """「서비스 미상」 결정 기록 → S7 트리 그룹 서비스 id(표시 전용 · 없으면 None).

    `resource_ancestry`(`/루트/…/서버/하위 자원`)에서 서버 이름과 같은 마디 앞의 폴더(루트 제외)를
    트리 경로로 보고 `tree_group_of`로 그룹을 뽑는다. 서버 마디를 못 찾으면 추정하지 않는다.
    """
    ancestry = _text(rec.get("resource_ancestry"))
    if not ancestry or not services_by_key:
        return None
    parts = [p for p in ancestry.split("/") if p.strip()]
    server_name = _text(rec.get("server_name"))
    hostname = _text(rec.get("hostname"))
    own = {norm_name(v) for v in (server_name, hostname) if v}
    idx = next((i for i in range(len(parts) - 1, -1, -1) if norm_name(parts[i]) in own), None)
    if idx is None or idx < 2:
        return None
    folders = tuple(TreeFolder("", p) for p in parts[1:idx])
    server = SmsServer(_text(rec.get("db_id")), "", server_name or hostname, hostname)
    group = tree_group_of(server, ServerAttr(server.db_id, "", folders), generic)
    if group is None:
        return None
    return services_by_key.get(tree_service_key(group.norm))


class BizAlarmAggregator:
    """M2 알람 집계 + 10초 메모(프로세스 안 1개 — 라우트가 `app.state`에 둔다)."""

    def __init__(
        self,
        *,
        cache: Any,  # RedisBizServiceCache | None
        incident_store: Any,  # IncidentStore | None
        decision_store: Any,  # DecisionStore | None
        window_minutes: int,
        ttl_seconds: float = MEMO_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        settings_loader: Callable[[], Any] | None = None,
    ) -> None:
        """의존성을 받는다 — `settings_loader`는 `load_biz_service_config` 대체(테스트용)."""
        self._cache = cache
        self._incidents = incident_store
        self._decisions = decision_store
        self.window_minutes = max(1, int(window_minutes))
        self._ttl = float(ttl_seconds)
        self._monotonic = monotonic
        self._wall = wall
        self._settings_loader = settings_loader
        self._settings: Any = None
        self._lock = asyncio.Lock()
        self._memo: AlarmSnapshot | None = None
        self._memo_at = 0.0

    @property
    def cache(self) -> Any:
        """업무 서비스 사본(없으면 None)."""
        return self._cache

    def _loaded_settings(self) -> Any:
        if self._settings is None:
            if self._settings_loader is not None:
                self._settings = self._settings_loader()
            else:
                from noise_gate.application.biz_service_config import load_biz_service_config

                self._settings = load_biz_service_config()
        return self._settings

    def thresholds(self) -> Mapping[str, SmsThreshold]:
        """SMS 임계(`config/knowledge/biz_service.yaml` — 비면 「판정 불가 · 임계 미설정」)."""
        thresholds: Mapping[str, SmsThreshold] = self._loaded_settings().config.sms_thresholds
        return thresholds

    async def snapshot(self) -> AlarmSnapshot:
        """메모가 살아 있으면 그대로, 아니면 다시 계산한다(동시 요청은 1회만 계산)."""
        if self._memo is not None and self._monotonic() - self._memo_at < self._ttl:
            return self._memo
        async with self._lock:
            if self._memo is not None and self._monotonic() - self._memo_at < self._ttl:
                return self._memo
            snap = await self._compute()
            self._memo, self._memo_at = snap, self._monotonic()
            return snap

    async def _services(self, notices: list[str]) -> dict[int, dict[str, Any]]:
        if self._cache is None:
            notices.append("업무 서비스 사본 없음 — 모든 알람이 「서비스 미상」")
            return {}
        out: dict[int, dict[str, Any]] = {}
        for sid in await self._cache.list_service_ids():
            svc = await self._cache.get_service(sid)
            if isinstance(svc, dict):
                out[sid] = svc
        return out

    async def _recent(self, notices: list[str]) -> list[dict[str, Any]]:
        store = self._decisions
        if store is None or getattr(store, "enabled", True) is False:
            notices.append("결정 이력 꺼짐 — 최근 알람 0")
            return []
        seconds = self.window_minutes * 60

        def _scan() -> list[dict[str, Any]]:
            return [r for r in store.iter_recent_decisions(seconds) if isinstance(r, dict)]

        try:
            return await asyncio.to_thread(_scan)
        except Exception as exc:  # noqa: BLE001 — 읽기 실패는 고지(침묵 금지)
            logger.warning("업무 현황 결정 이력 읽기 실패: %s", exc)
            notices.append("결정 이력 읽기 실패 — 최근 알람 0")
            return []

    async def _open(self, notices: list[str]) -> list[dict[str, Any]]:
        if self._incidents is None:
            notices.append("열린 사건 추적 꺼짐 — 열린 사건 0")
            return []
        rows = await self._incidents.list_open(limit=OPEN_INCIDENT_LIMIT)
        if len(rows) >= OPEN_INCIDENT_LIMIT:
            notices.append(f"열린 사건 최신 {OPEN_INCIDENT_LIMIT}건만 집계")
        return [r for r in rows if isinstance(r, dict)]

    async def _compute(self) -> AlarmSnapshot:
        notices: list[str] = []
        services = await self._services(notices)
        recent = await self._recent(notices)
        incidents = await self._open(notices)
        by_alarm: dict[str, dict[str, Any]] = {}
        for rec in recent:  # 최신부터 — 같은 알람은 최신 기록만
            by_alarm.setdefault(_text(rec.get("alarm_id")), rec)
        tree_keys = {
            _text(s.get("service_key")): sid for sid, s in services.items()
            if _text(s.get("service_key")).startswith("tree:")
        }
        attributions: dict[_Identity, Any] = {}
        fallback = 0

        async def _attribute(ident: _Identity) -> Any:
            if self._cache is None:
                return None
            if ident not in attributions:
                targets, apm, host, sms_db = ident
                attributions[ident] = await self._cache.attribute(
                    polestar_link_targets=list(targets) or None, apm=apm, hostname=host or None,
                    db_id=sms_db or None,
                )
            return attributions[ident]

        async def _fact(kind: str, rec: Mapping[str, Any], src: Mapping[str, Any]) -> AlarmFact:
            nonlocal fallback
            raw_sev = (
                src.get("severity") if kind == FACT_INCIDENT
                else (src.get("signals") or {}).get("severity")
            )
            sev, why = coerce_severity(raw_sev)
            if why is not None:
                fallback += 1
            db_id = _text(src.get("db_id")) or _text(rec.get("db_id"))
            ident = _identity(rec)
            hit = await _attribute(ident)
            sid = getattr(hit, "service_id", None)
            basis = _text(getattr(hit, "basis", ""))
            member = _member_of(services[sid], basis, ident) if sid in services else None
            zone = member_zone(member) if member is not None else _alarm_zone(db_id)
            estimate = None
            if sid is None and rec:
                estimate = _ancestry_tree_service(
                    rec, tree_keys, self._loaded_settings().generic_terms if tree_keys else ()
                )
            return AlarmFact(
                kind=kind,
                alarm_id=_text(src.get("alarm_id")),
                db_id=db_id,
                severity=sev,
                tier=_text(src.get("tier")).lower(),
                at=_epoch(src.get("created_at") if kind == FACT_INCIDENT else src.get("ts")),
                service_id=sid if sid in services else None,
                basis=basis if sid in services else "",
                member_key=_text(member.get("member_key")) if member is not None else "",
                zone=zone,
                estimate_service_id=estimate,
                incident_id=_text(src.get("id")) if kind == FACT_INCIDENT else "",
            )

        facts = [await _fact(FACT_RECENT, rec, rec) for rec in recent]
        for inc in incidents:
            joined = by_alarm.get(_text(inc.get("alarm_id")))
            rec = joined if joined is not None else {
                "db_id": inc.get("db_id"), "server_name": inc.get("server_name"),
            }
            facts.append(await _fact(FACT_INCIDENT, rec, inc))
        if fallback:
            notices.append(f"심각도 미상 {fallback}건 — 경고로 셈")
        return AlarmSnapshot(self._wall(), self.window_minutes, services, tuple(facts),
                             tuple(notices))


# ── M3 최신값 · 수집 상태 읽기(W7이 기록) ────────────────────────────────
@dataclass(frozen=True)
class MonitorValue:
    """`bizmon:svc:{id}` 1건(가정 모양 — W7이 정한다 · 모르는 칸은 무시)."""

    updated_at: str | None = None
    in_window: bool = True
    fetch_failures: tuple[str, ...] = ()
    sms_stat_time: str | None = None
    apm_signals: tuple[ApmSignalFact, ...] = ()
    apm_values: tuple[ApmValueFact, ...] = ()
    sms_metrics: tuple[SmsMetricFact, ...] = ()


def _float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rows(raw: Any) -> list[Mapping[str, Any]]:
    return [r for r in raw if isinstance(r, Mapping)] if isinstance(raw, list) else []


def _loads(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", errors="replace")
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def parse_monitor_value(raw: Any) -> MonitorValue | None:
    """`bizmon:svc:{id}` 값 → `MonitorValue`(없음·손상이면 None)."""
    value = _loads(raw)
    if value is None:
        return None
    return MonitorValue(
        updated_at=_text(value.get("updated_at")) or None,
        in_window=value.get("in_window") is not False,
        fetch_failures=tuple(_text(f) for f in value.get("fetch_failures") or () if _text(f)),
        sms_stat_time=_text(value.get("sms_stat_time")) or None,
        apm_signals=tuple(
            ApmSignalFact(_text(r.get("member_key")), _text(r.get("signal")), _text(r.get("level")))
            for r in _rows(value.get("apm_signals"))
        ),
        apm_values=tuple(
            ApmValueFact(_text(r.get("member_key")), _text(r.get("name")), _float(r.get("value")))
            for r in _rows(value.get("apm_values"))
        ),
        sms_metrics=tuple(
            SmsMetricFact(_text(r.get("member_key")), _text(r.get("metric")).lower(),
                          _float(r.get("value")))
            for r in _rows(value.get("sms_metrics"))
        ),
    )


async def read_monitor_values(redis: Any, service_ids: Iterable[int]) -> dict[int, MonitorValue]:
    """감시 서비스들의 M3 최신값(키 없음·손상·Redis 실패는 빠진다 — 호출부가 사유를 낸다)."""
    out: dict[int, MonitorValue] = {}
    if redis is None:
        return out
    for sid in service_ids:
        try:
            raw = await redis.get(f"{MONITOR_SVC_PREFIX}{int(sid)}")
        except Exception:  # noqa: BLE001 — 그 서비스만 「지표 수집 결과 없음」
            logger.debug("M3 최신값 조회 실패: service=%s", sid, exc_info=True)
            continue
        value = parse_monitor_value(raw)
        if value is not None:
            out[int(sid)] = value
    return out


async def read_metrics_status(redis: Any, *, active: bool) -> dict[str, Any]:
    """지표 수집 상태 칸 — 꺼짐 / 수집 중 / 일부만 / 조회 실패 · 마지막 성공 시각 · 사유.

    `bizmon:status`(W7 · JSON `{state, last_success_at, reasons}`)가 없거나 플래그가 꺼져 있으면
    꺼짐.
    """
    def _view(state: str, last: str | None, reasons: Sequence[str]) -> dict[str, Any]:
        return {"state": state, "label": METRICS_LABELS[state], "last_success_at": last,
                "reasons": list(reasons)}

    if not active:
        return _view(METRICS_OFF, None, [METRICS_OFF_NOTE])
    raw: Any = None
    if redis is not None:
        try:
            raw = await redis.get(MONITOR_STATUS_KEY)
        except Exception:  # noqa: BLE001 — 상태 미상은 사유로
            logger.debug("M3 수집 상태 조회 실패", exc_info=True)
            return _view(METRICS_FAILED, None, ["수집 상태 조회 실패"])
    value = _loads(raw)
    if value is None:
        return _view(METRICS_OFF, None, ["수집 상태 없음 — 수집기가 아직 돌지 않았다"])
    state = _text(value.get("state")).lower()
    reasons = [_text(r) for r in value.get("reasons") or () if _text(r)]
    if state not in METRICS_LABELS:
        return _view(METRICS_OFF, None, [f"수집 상태 값 미상: {state or '(빈 값)'}"])
    return _view(state, _text(value.get("last_success_at")) or None, reasons)


async def touch_viewers(redis: Any) -> None:
    """보드 수요 기록 — `bizmon:viewers` `SET EX 120`(값은 기록 시각 · Redis 오류는 올린다)."""
    await redis.set(VIEWERS_KEY, datetime.now(UTC).isoformat(), ex=VIEWERS_TTL_SECONDS)


# ── 현황 보기 조립 ──────────────────────────────────────────────────────
def _member_name(m: Mapping[str, Any]) -> str:
    for key in ("server_name", "hostname", "instance_name", "domain_name", "member_key"):
        if _text(m.get(key)):
            return _text(m.get(key))
    return ""


def _alarm_counts(facts: Sequence[AlarmFact]) -> dict[str, Any]:
    open_sev = dict.fromkeys(SEVERITY_KEYS.values(), 0)
    recent_sev = dict.fromkeys(SEVERITY_KEYS.values(), 0)
    handling = dict.fromkeys(_HANDLINGS, 0)
    open_n = recent_n = 0
    last: float | None = None
    for f in facts:
        key = SEVERITY_KEYS.get(f.severity, "warning")
        if f.kind == FACT_INCIDENT:
            open_n += 1
            open_sev[key] += 1
        else:
            recent_n += 1
            recent_sev[key] += 1
            handling[_TIER_HANDLING.get(f.tier, HANDLING_OTHER)] += 1
        if f.at is not None and (last is None or f.at > last):
            last = f.at
    return {
        "open_incidents": open_n, "open_by_severity": open_sev,
        "recent_total": recent_n, "recent_by_severity": recent_sev,
        "recent_by_handling": handling, "last_alarm_at": iso_of(last),
    }


@dataclass(frozen=True)
class _ServiceView:
    service: Mapping[str, Any]
    visible: tuple[Mapping[str, Any], ...]
    hidden: int
    facts: tuple[AlarmFact, ...]
    monitor: MonitorValue | None
    judgement: ServiceStateJudgement
    reasons: tuple[str, ...]


def _judge(
    *,
    monitored: bool,
    facts: Sequence[AlarmFact],
    keys: Collection[str],
    monitor: MonitorValue | None,
    metrics_active: bool,
    thresholds: Mapping[str, SmsThreshold],
) -> tuple[ServiceStateJudgement, tuple[str, ...]]:
    """§4.4 판정(보이는 구성원의 사실만) + 앞에 붙일 수집 사유."""
    m = monitor if metrics_active else None
    judgement = judge_service_state(
        monitored=monitored,
        incidents=[IncidentFact(f.member_key, f.severity, incident_id=f.incident_id)
                   for f in facts if f.kind == FACT_INCIDENT],
        apm_signals=[s for s in (m.apm_signals if m else ()) if s.member_key in keys],
        sms_metrics=[s for s in (m.sms_metrics if m else ()) if s.member_key in keys],
        apm_values=[v for v in (m.apm_values if m else ()) if v.member_key in keys],
        thresholds=thresholds,
        metrics_in_window=m.in_window if m else True,
        fetch_failures=m.fetch_failures if m else (),
    )
    extra: list[str] = []
    if not metrics_active:
        extra.append(REASON_METRICS_OFF)
    elif monitored and m is None:
        extra.append(REASON_METRICS_MISSING)
    return judgement, tuple(extra) + judgement.reasons


def _service_view(
    snapshot: AlarmSnapshot,
    sid: int,
    *,
    scope: Collection[str] | None,
    monitor: MonitorValue | None,
    metrics_active: bool,
    thresholds: Mapping[str, SmsThreshold],
) -> _ServiceView | None:
    """서비스 1개의 존 필터 결과(보이는 구성원이 없으면 None — 건수만 센다)."""
    svc = snapshot.services.get(sid)
    if svc is None:
        return None
    members = [m for m in svc.get("members") or () if isinstance(m, Mapping)]
    visible = tuple(m for m in members if zone_visible(scope, member_zone(m)))
    if scope is not None and not visible:
        return None
    keys = {_text(m.get("member_key")) for m in visible}
    facts = tuple(f for f in snapshot.facts
                  if f.service_id == sid and zone_visible(scope, f.zone))
    judgement, reasons = _judge(
        monitored=svc.get("monitored") is True, facts=facts, keys=keys, monitor=monitor,
        metrics_active=metrics_active, thresholds=thresholds,
    )
    return _ServiceView(svc, visible, len(members) - len(visible), facts, monitor, judgement,
                        reasons)


def _max_by(rows: Iterable[tuple[str, float | None]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for name, value in rows:
        if name and value is not None and (name not in out or value > out[name]):
            out[name] = value
    return out


def _metrics_view(view: _ServiceView, metrics_active: bool) -> dict[str, Any] | None:
    """타일 지표 칸 — 보이는 구성원의 최악값(SMS 지표별 최대 · APM 값 이름별 최대 · 표시 전용)."""
    m = view.monitor
    if not metrics_active or m is None:
        return None
    keys = {_text(x.get("member_key")) for x in view.visible}
    return {
        "updated_at": m.updated_at,
        "sms_stat_time": m.sms_stat_time,
        "sms_max": _max_by((s.metric, s.value) for s in m.sms_metrics if s.member_key in keys),
        "apm_max": _max_by((v.name, v.value) for v in m.apm_values if v.member_key in keys),
    }


def _service_item(view: _ServiceView, metrics_active: bool) -> dict[str, Any]:
    svc, j = view.service, view.judgement
    return {
        "id": svc.get("id"),
        "display_name": svc.get("display_name"),
        "tier": svc.get("tier"),
        "monitored": svc.get("monitored") is True,
        "verified": svc.get("verified") is True,
        "zones": sorted({z for z in (member_zone(m) for m in view.visible) if z}),
        "member_count": len(view.visible),
        "hidden_member_count": view.hidden,
        "state": j.state,
        "state_label": j.label,
        "reasons": list(view.reasons),
        "alarms": _alarm_counts(view.facts),
        "metrics": _metrics_view(view, metrics_active),
    }


def _sort_key(item: Mapping[str, Any]) -> tuple[int, int, str]:
    tier = item.get("tier")
    return (
        _STATE_ORDER.get(_text(item.get("state")), 9),
        tier if isinstance(tier, int) and not isinstance(tier, bool) else 99,
        _text(item.get("display_name")),
    )


def build_board(
    snapshot: AlarmSnapshot,
    *,
    scope: Collection[str] | None,
    monitor_values: Mapping[int, MonitorValue],
    metrics_status: Mapping[str, Any],
    metrics_active: bool,
    thresholds: Mapping[str, SmsThreshold],
) -> dict[str, Any]:
    """요약(GET 목록) — 상단 요약 띠 + 서비스 타일 + 「서비스 미상」(트리 그룹 추정은 표시만)."""
    items: list[dict[str, Any]] = []
    hidden = 0
    for sid in sorted(snapshot.services):
        view = _service_view(snapshot, sid, scope=scope, monitor=monitor_values.get(sid),
                             metrics_active=metrics_active, thresholds=thresholds)
        if view is None:
            hidden += 1
            continue
        items.append(_service_item(view, metrics_active))
    items.sort(key=_sort_key)
    visible_ids = {item["id"] for item in items}
    counts = dict.fromkeys(STATE_LABELS, 0)
    for item in items:
        counts[item["state"]] += 1
    unknown = [f for f in snapshot.facts if f.service_id is None and zone_visible(scope, f.zone)]
    estimates: dict[int, int] = {}
    for f in unknown:
        est = f.estimate_service_id
        if f.kind == FACT_RECENT and est is not None and est in visible_ids:
            estimates[est] = estimates.get(est, 0) + 1
    return {
        "generated_at": iso_of(snapshot.computed_at),
        "window_minutes": snapshot.window_minutes,
        "summary": {
            "state_counts": counts,
            "service_count": len(items),
            "open_incidents": sum(i["alarms"]["open_incidents"] for i in items),
        },
        "metrics_status": dict(metrics_status),
        "services": items,
        "hidden_service_count": hidden,
        "unattributed": {
            "recent_alarms": sum(1 for f in unknown if f.kind == FACT_RECENT),
            "open_incidents": sum(1 for f in unknown if f.kind == FACT_INCIDENT),
            "tree_estimates": [
                {"service_id": sid,
                 "display_name": snapshot.services[sid].get("display_name"),
                 "recent_alarms": n}
                for sid, n in sorted(estimates.items())
            ],
        },
        "notices": list(snapshot.notices),
    }


def _evidence(j: ServiceStateJudgement, names: Mapping[str, str]) -> list[dict[str, Any]]:
    return [
        {"state": e.state, "state_label": STATE_LABELS[e.state], "member_key": e.member_key,
         "member_name": names.get(e.member_key, ""), "rule": e.rule, "metric": e.metric,
         "value": e.value, "threshold": e.threshold}
        for e in j.evidence
    ]


def build_detail(
    snapshot: AlarmSnapshot,
    service_id: int,
    *,
    scope: Collection[str] | None,
    monitor: MonitorValue | None,
    metrics_status: Mapping[str, Any],
    metrics_active: bool,
    thresholds: Mapping[str, SmsThreshold],
) -> dict[str, Any] | None:
    """상세(GET /{id}) — 머리 + 구성원 상태 표(없음·권한 밖 서비스 전체면 None)."""
    view = _service_view(snapshot, service_id, scope=scope, monitor=monitor,
                         metrics_active=metrics_active, thresholds=thresholds)
    if view is None:
        return None
    svc = view.service
    names = {_text(m.get("member_key")): _member_name(m) for m in view.visible}
    head = _service_item(view, metrics_active)
    head.update({
        "service_key": svc.get("service_key"),
        "description": svc.get("description"),
        "owner_org": svc.get("owner_org"),
        "origin": svc.get("origin"),
        "evidence": _evidence(view.judgement, names),
        "sms_notes": dict(view.judgement.sms_notes),
    })
    m = monitor if metrics_active else None
    rows: list[dict[str, Any]] = []
    for member in view.visible:
        key = _text(member.get("member_key"))
        facts = [f for f in view.facts if f.member_key == key]
        j, reasons = _judge(
            monitored=svc.get("monitored") is True, facts=facts, keys={key}, monitor=monitor,
            metrics_active=metrics_active, thresholds=thresholds,
        )
        rows.append({
            "id": member.get("id"),
            "kind": member.get("kind"),
            "member_key": key,
            "name": _member_name(member),
            **{f: member.get(f) for f in (
                "server_name", "hostname", "ip", "db_id", "resource_id", "source_id",
                "domain_id", "domain_name", "instance_name", "layer", "confidence", "origin",
            )},
            "zone": member_zone(member),
            "state": j.state,
            "state_label": j.label,
            "reasons": list(reasons),
            "alarms": _alarm_counts(facts),
            "sms": {s.metric: s.value for s in (m.sms_metrics if m else ()) if s.member_key == key},
            "apm": {v.name: v.value for v in (m.apm_values if m else ()) if v.member_key == key},
            "apm_signals": sorted({s.signal for s in (m.apm_signals if m else ())
                                   if s.member_key == key}),
        })
    return {
        "generated_at": iso_of(snapshot.computed_at),
        "window_minutes": snapshot.window_minutes,
        "metrics_status": dict(metrics_status),
        "service": head,
        "members": rows,
        "hidden_member_count": view.hidden,
        "unattributed_service_alarms": sum(1 for f in view.facts if not f.member_key),
        "notices": list(snapshot.notices),
    }


__all__ = [
    "FACT_INCIDENT",
    "FACT_RECENT",
    "MEMO_TTL_SECONDS",
    "METRICS_LABELS",
    "MONITOR_STATUS_KEY",
    "MONITOR_SVC_PREFIX",
    "OPEN_INCIDENT_LIMIT",
    "SEVERITY_KEYS",
    "VIEWERS_KEY",
    "VIEWERS_TTL_SECONDS",
    "AlarmFact",
    "AlarmSnapshot",
    "BizAlarmAggregator",
    "MonitorValue",
    "build_board",
    "build_detail",
    "iso_of",
    "parse_monitor_value",
    "read_metrics_status",
    "read_monitor_values",
    "touch_viewers",
    "zone_visible",
]
