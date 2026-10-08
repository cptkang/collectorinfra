"""크로스소스 사건(episode) 상관 — 규칙·결합 판정·사건 추적 (plans/144 §5.1~§5.4).

인프라 모니터링 알람(원인)과 APM 알람(증상)을 같은 존·같은 호스트 키의 **사건**으로 묶고,
규칙 표의 방향·창이 맞으면 증상을 DASHBOARD로 강등할 근거(`action="demote"`)를, 조건이 하나라도
빠지면 묶음만(`action="link"`) 산출한다. 실제 티어 상한은 정책 계층(`notification_policy` step
7.6)이 모드·규칙 단위 enforce를 보고 건다 — 이 모듈은 판정 근거만 만든다(SUPPRESS 없음 · G-2).

결합 조건(전부 만족해야 demote — §5.2):
    ① 같은 존(빈 값이면 미충족) ② 같은 호스트 키 · 양쪽 신뢰도 strong · 한 사건 안에 서로 다른
    인프라 db_id가 섞이지 않음(같은 존 다른 사이트의 동명 호스트 — 모호) ③ 규칙 방향 일치 + 원인
    **발생 시각** t_c ∈ [t_s − cause_before, t_s + cause_after](t_s = 증상 발생 시각) ④ 원인의 최종
    티어가 page·ticket이고 아직 해소되지 않음(Alertmanager inhibit의 「source firing」) ⑤ 증상
    심각도 < 3. 규칙 방향 불일치는 후보 자체가 아니다(묶을 원인이 없다).

도착 순서(§5.4 · G-4 (a)): 증상을 붙잡지 않는다. 원인은 이미 도착·판정된 멤버만 본다. 증상이
먼저 왔으면 그 증상은 그대로 두고, 나중에 온 원인의 신호에 「이미 통보된 연관 증상」 id를 싣는다.

사건 상태(G-8 (a))는 호출자(워커·리플레이 하네스) 메모리에만 있다. 시각은 전부 인자로 받는다 —
`now`(도착 시계 · idle 종료·만료 sweep 기준)와 `occurred_at`(발생 시계 · 창 기준)을 구분한다.

이 모듈은 domain 계층이라 표준 라이브러리만 쓴다. 이벤트 → `EpisodeAlarm` 변환과 규칙 YAML
적재는 infrastructure(`noise_gate/infrastructure/cross_source_rules.py`)가 한다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

# ── 소스 · 모드 · 조치 어휘 ─────────────────────────────────────────────
SOURCE_POLESTAR = "polestar"
SOURCE_JENNIFER = "jennifer"
SOURCES = frozenset({SOURCE_POLESTAR, SOURCE_JENNIFER})

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ANNOTATE = "annotate"
MODE_ENFORCE = "enforce"

# 규칙 표 `action` 값.
RULE_ACTION_LINK = "link"
RULE_ACTION_DEMOTE = "demote_effect"

# 판정 결과 `action` 값.
ACTION_LINK = "link"
ACTION_DEMOTE = "demote"

# `was_kinds` 와일드카드 — 그 소스의 알람이면 kind와 무관하게 맞는다.
WILDCARD = "*"

# 인프라 알람 kind 어휘 — `process_rank.classify_alarm_kind` 반환값(apm 제외). 규칙 검증용.
POLESTAR_KINDS = frozenset({"cpu", "memory", "disk", "network", "process", "log"})

# 원인 통보로 인정하는 최종 티어(§5.2 ④). notification_policy 티어 문자열과 같다.
NOTIFIED_TIERS = frozenset({"page", "ticket"})

# (W4 · §5.6) 앱 영향으로 세는 APM 이벤트 레벨 — 게이트 노드의 게이트웨이 조회와 같은 기준
# (fatal·critical → 심각도 3 매핑과 같다). 사건 저장소 판정과 게이트웨이 판정이 같은 집합을 쓴다.
APP_IMPACT_LEVELS = frozenset({"fatal", "critical"})
APP_IMPACT_SOURCE_EPISODE = "episode"

# (W4 · §5.6 G-6) 사후 승격 대상 최종 티어 — 승격 전용(SUPPRESS는 되살리지 않고 PAGE는 그대로).
LATE_PROMOTION_TIERS = frozenset({"dashboard", "ticket"})

# 빠진 조건 라벨(감사 `missing` 값 — 닫힌 집합).
MISSING_ZONE = "zone"
MISSING_HOST_KEY = "host_key"
MISSING_HOST_KEY_STRENGTH = "host_key_strength"
MISSING_DB_AMBIGUOUS = "db_ambiguous"
MISSING_WINDOW = "window"
MISSING_CAUSE_TIER = "cause_tier"
MISSING_CAUSE_RESOLVED = "cause_resolved"
MISSING_SEVERITY = "severity"

_STRONG = "strong"

# 메모리 가드(R-6) — 열린 사건 수 · 사건당 멤버 수 상한. 넘치면 오래된 것부터 정리한다.
MAX_EPISODES = 5000
MAX_MEMBERS_PER_EPISODE = 200


@dataclass(frozen=True)
class SideSpec:
    """규칙의 원인·증상 한쪽 — 소스와 선택자(kind · WAS kind · 알람명 중 하나라도 맞으면 해당)."""

    source: str
    kinds: frozenset[str] = frozenset()
    was_kinds: frozenset[str] = frozenset()
    alarm_names: frozenset[str] = frozenset()

    def matches(self, alarm: EpisodeAlarm) -> bool:
        """알람이 이 쪽에 해당하는가."""
        if alarm.source != self.source:
            return False
        if alarm.kind and alarm.kind in self.kinds:
            return True
        if WILDCARD in self.was_kinds or self.was_kinds.intersection(alarm.was_kinds):
            return True
        return bool(alarm.alarm_name) and alarm.alarm_name in self.alarm_names


@dataclass(frozen=True)
class CrossSourceRule:
    """규칙 표 한 행(적재·검증 완료 · 비활성 행은 적재기가 이미 걸렀다)."""

    id: str
    cause: SideSpec
    effect: SideSpec
    cause_before_seconds: int
    cause_after_seconds: int
    action: str = RULE_ACTION_LINK
    enforce: bool = False


@dataclass(frozen=True)
class EpisodeAlarm:
    """사건에 붙는 알람 한 건의 판정 재료(이벤트에서 값만 옮긴 것)."""

    alarm_id: str
    source: str
    zone: str
    host_key: str
    host_key_strength: str
    db_id: str
    occurred_at: float          # 발생 시각(epoch 초) — 창 판정 기준
    severity: int
    kind: str = ""              # 인프라 알람 kind(없으면 "")
    was_kinds: tuple[str, ...] = ()   # APM 게이트웨이가 실어 보낸 WAS kind 값
    alarm_name: str = ""
    fingerprint: str = ""       # 해소 짝맞춤 키
    level: str = ""             # APM 이벤트 레벨(소문자 · 인프라 알람은 "") — 앱 영향 판정(W4)


@dataclass
class EpisodeMember:
    """사건 멤버 — 알람 + 판정 뒤 기록되는 최종 티어 · 해소 여부."""

    alarm: EpisodeAlarm
    arrived_at: float
    tier: str | None = None
    resolved: bool = False
    late_promoted: bool = False  # (W4 · G-6) 사후 승격 통보 완료 — 알람당 1회 보장


@dataclass(frozen=True)
class CrossSourceLink:
    """증상 한 건과 원인 한 건의 결합 판정 결과."""

    rule_id: str
    cause_alarm_id: str
    host_key: str
    lag_seconds: float          # t_s − t_c (양수 = 원인이 먼저 발생)
    action: str                 # "demote" | "link"
    enforce: bool               # 규칙 단위 enforce 열
    missing: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """감사·state 전달용 dict."""
        return {
            "rule_id": self.rule_id,
            "cause_alarm_id": self.cause_alarm_id,
            "host_key": self.host_key,
            "lag_seconds": self.lag_seconds,
            "action": self.action,
            "enforce": self.enforce,
            "missing": list(self.missing),
        }


def episode_key(zone: str, host_key: str) -> str:
    """사건 키 `zone|host_key`(Q-2 (a) — 같은 호스트 키의 소스 간 묶음은 존 경계)."""
    return f"{zone}|{host_key}"


def episode_id_for(zone: str, host_key: str, first_occurred_at: float) -> str:
    """결정적 사건 id — 존·호스트 키·첫 알람 발생 시각(초 단위)의 해시."""
    raw = f"{zone}|{host_key}|{int(first_occurred_at)}"
    return "ep-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def in_window(rule: CrossSourceRule, cause: EpisodeAlarm, effect: EpisodeAlarm) -> bool:
    """원인 발생 시각이 증상 기준 창 [t_s − before, t_s + after] 안인가."""
    lag = effect.occurred_at - cause.occurred_at
    return -rule.cause_after_seconds <= lag <= rule.cause_before_seconds


def evaluate_link(
    rule: CrossSourceRule,
    cause: EpisodeMember,
    effect: EpisodeAlarm,
    *,
    db_ambiguous: bool = False,
) -> CrossSourceLink | None:
    """원인 멤버 → 증상 알람 결합 판정(순수 함수 · §5.2).

    규칙 방향이 맞지 않으면(원인·증상 선택자 불일치) None — 묶을 근거가 없다. 그 밖에는 빠진
    조건을 모두 모아 하나도 없고 규칙이 `demote_effect`일 때만 `action="demote"`다.

    Args:
        rule: 규칙 행.
        cause: 이미 도착·판정된 원인 후보 멤버.
        effect: 지금 판정하는 증상 알람.
        db_ambiguous: 사건 안에 서로 다른 인프라 db_id가 섞였는가(호출자 산출).
    """
    c = cause.alarm
    if not rule.cause.matches(c) or not rule.effect.matches(effect):
        return None
    missing: list[str] = []
    if not c.zone or not effect.zone or c.zone != effect.zone:
        missing.append(MISSING_ZONE)
    if not c.host_key or c.host_key != effect.host_key:
        missing.append(MISSING_HOST_KEY)
    if c.host_key_strength != _STRONG or effect.host_key_strength != _STRONG:
        missing.append(MISSING_HOST_KEY_STRENGTH)
    if db_ambiguous:
        missing.append(MISSING_DB_AMBIGUOUS)
    if not in_window(rule, c, effect):
        missing.append(MISSING_WINDOW)
    if cause.tier not in NOTIFIED_TIERS:
        missing.append(MISSING_CAUSE_TIER)
    if cause.resolved:
        missing.append(MISSING_CAUSE_RESOLVED)
    if effect.severity >= 3:
        missing.append(MISSING_SEVERITY)
    action = (
        ACTION_DEMOTE if not missing and rule.action == RULE_ACTION_DEMOTE else ACTION_LINK
    )
    return CrossSourceLink(
        rule_id=rule.id,
        cause_alarm_id=c.alarm_id,
        host_key=effect.host_key,
        lag_seconds=round(effect.occurred_at - c.occurred_at, 3),
        action=action,
        enforce=rule.enforce,
        missing=tuple(missing),
    )


def best_link(links: Iterable[CrossSourceLink]) -> CrossSourceLink | None:
    """후보 중 하나를 결정적으로 고른다 — demote 우선 → 빠진 조건 수 → |시차| → 입력 순서."""
    best: CrossSourceLink | None = None
    best_key: tuple[int, int, float] | None = None
    for link in links:
        key = (0 if link.action == ACTION_DEMOTE else 1, len(link.missing), abs(link.lag_seconds))
        if best_key is None or key < best_key:
            best, best_key = link, key
    return best


@dataclass
class Episode:
    """열린 사건 하나."""

    id: str
    zone: str
    host_key: str
    opened_at: float
    last_seen: float
    members: list[EpisodeMember] = field(default_factory=list)
    representative_id: str = ""
    # 규칙 방향상 원인으로 쓰인 멤버(대표 선출용 · 먼저 쓰인 순).
    cause_ids: list[str] = field(default_factory=list)
    # (W4 · §4.5) 이 사건에서 APM 알람이 제출한 조사 id — 사건당 1회 제출의 기준(빈 값 = 없음).
    apm_investigation_id: str = ""

    def member(self, alarm_id: str) -> EpisodeMember | None:
        """alarm_id로 멤버를 찾는다(같은 id가 여럿이면 마지막)."""
        for m in reversed(self.members):
            if m.alarm.alarm_id == alarm_id:
                return m
        return None

    def infra_db_ids(self) -> set[str]:
        """멤버 중 인프라 알람의 db_id 집합(모호 판정용)."""
        return {
            m.alarm.db_id for m in self.members
            if m.alarm.source == SOURCE_POLESTAR and m.alarm.db_id
        }

    def mark_cause(self, alarm_id: str) -> None:
        """원인으로 쓰인 멤버를 기록하고, 대표가 아직 원인이 아니면 그 멤버로 바꾼다."""
        if alarm_id not in self.cause_ids:
            self.cause_ids.append(alarm_id)
        if self.representative_id not in self.cause_ids:
            self.representative_id = self.cause_ids[0]

    def to_dict(self) -> dict[str, Any]:
        """조회·감사용 스냅샷."""
        return {
            "episode_id": self.id,
            "zone": self.zone,
            "host_key": self.host_key,
            "opened_at": self.opened_at,
            "last_seen": self.last_seen,
            "representative_id": self.representative_id,
            "apm_investigation_id": self.apm_investigation_id,
            "members": [
                {
                    "alarm_id": m.alarm.alarm_id,
                    "source": m.alarm.source,
                    "db_id": m.alarm.db_id,
                    "severity": m.alarm.severity,
                    "occurred_at": m.alarm.occurred_at,
                    "tier": m.tier,
                    "resolved": m.resolved,
                    "late_promoted": m.late_promoted,
                }
                for m in self.members
            ],
        }


def apm_alarms_in_window(
    ep: Episode, reference: float, window_seconds: float
) -> list[EpisodeAlarm]:
    """사건의 APM 멤버 중 발생 시각이 [reference − window, reference]인 것(도착 순 · W4 §5.6).

    게이트웨이 조회(`lookback_minutes` = 기준 시각 이전 창)와 같은 창이다. 해소 여부는 보지
    않는다 — 게이트웨이도 창 안에 발생한 이벤트를 해소와 무관하게 돌려준다.
    """
    lo = reference - window_seconds
    return [
        m.alarm for m in ep.members
        if m.alarm.source == SOURCE_JENNIFER and lo <= m.alarm.occurred_at <= reference
    ]


def app_impact_from_alarms(alarms: Iterable[EpisodeAlarm]) -> dict[str, Any] | None:
    """APM 알람 목록 → `app_impact` 예약값(게이트 노드 게이트웨이 경로와 같은 모양 · W4 §5.6).

    레벨이 `APP_IMPACT_LEVELS`인 것만 센다. 없으면 None(승격 근거 없음 — 호출부가 게이트웨이로
    넘어간다). `source`는 `APP_IMPACT_SOURCE_EPISODE`로 게이트웨이 경로와 구분한다.
    """
    items = list(alarms)
    fatal = [a for a in items if a.level in APP_IMPACT_LEVELS]
    if not fatal:
        return None
    return {
        "source": APP_IMPACT_SOURCE_EPISODE,
        "fatal_events": len(fatal),
        "event_types": sorted({a.alarm_name for a in fatal if a.alarm_name}),
        "was_signals": sorted({k for a in items for k in a.was_kinds if k}),
    }


def late_promotion_targets(
    ep: Episode, trigger: EpisodeAlarm, window_seconds: float
) -> list[EpisodeMember]:
    """APM 심각 이벤트 `trigger`가 붙은 사건에서 사후 승격 대상 인프라 멤버(W4 §5.6 · G-6).

    대상: 인프라 알람 · 최종 티어 dashboard·ticket · 심각도 < 3 · 미해소 · 아직 사후 승격 안 됨 ·
    발생 시각 차 |t_trigger − t_member| ≤ window. trigger가 APM 심각 레벨이 아니면 빈 목록.
    """
    if trigger.source != SOURCE_JENNIFER or trigger.level not in APP_IMPACT_LEVELS:
        return []
    out: list[EpisodeMember] = []
    for m in ep.members:
        a = m.alarm
        if (
            a.source == SOURCE_POLESTAR
            and m.tier in LATE_PROMOTION_TIERS
            and a.severity < 3
            and not m.resolved
            and not m.late_promoted
            and abs(trigger.occurred_at - a.occurred_at) <= window_seconds
        ):
            out.append(m)
    return out


class EpisodeTracker:
    """사건 추적기 — 워커·리플레이 하네스가 같은 클래스를 쓴다(시각은 전부 인자).

    `observe`가 비해소 알람을 사건에 붙이고 크로스소스 신호를 돌려준다. 호출자는 판정 뒤
    `record_tier`로 최종 티어를 기록한다(원인 조건 ④의 재료). 해소 알람은 `resolve`.
    종료: 소속 전부 해소 · 마지막 알람 뒤 `idle_seconds` 경과(`sweep`). 사건 수·멤버 수 상한을
    넘으면 오래된 것부터 정리하고 `on_evict(종류, 건수)`를 부른다(경고 로그는 호출자 몫).
    """

    def __init__(
        self,
        rules: Iterable[CrossSourceRule],
        *,
        mode: str,
        idle_seconds: float,
        max_episodes: int = MAX_EPISODES,
        max_members: int = MAX_MEMBERS_PER_EPISODE,
        on_evict: Callable[[str, int], None] | None = None,
    ) -> None:
        self.rules: tuple[CrossSourceRule, ...] = tuple(rules)
        self.mode = mode
        self.idle_seconds = float(idle_seconds)
        self.max_episodes = max_episodes
        self.max_members = max_members
        self._on_evict = on_evict
        self._episodes: dict[str, Episode] = {}
        self._by_id: dict[str, str] = {}

    # ── 조회(W4·W5) ────────────────────────────────────────────────
    def __len__(self) -> int:
        return len(self._episodes)

    def episode_for(self, zone: str, host_key: str) -> Episode | None:
        """존·호스트 키로 열린 사건을 찾는다."""
        return self._episodes.get(episode_key(zone, host_key))

    def get(self, episode_id: str) -> Episode | None:
        """사건 id로 열린 사건을 찾는다."""
        key = self._by_id.get(episode_id)
        return self._episodes.get(key) if key is not None else None

    def episodes(self) -> list[Episode]:
        """열린 사건 목록(열린 순)."""
        return list(self._episodes.values())

    # ── 갱신 ───────────────────────────────────────────────────────
    def observe(self, alarm: EpisodeAlarm, now: float) -> dict[str, Any] | None:
        """비해소 알람을 사건에 붙이고 크로스소스 신호를 돌려준다.

        호스트 키가 빈 알람은 묶지 않는다(None). 신호 키:
            episode_id · mode · host_key — 항상
            rule_id · cause_alarm_id · lag_seconds · action · enforce · missing · applied(False)
                — 이 알람이 규칙의 증상이고 사건에 원인 후보가 있을 때
            related_effects — 이 알람이 원인이고, 이미 통보된(page·ticket) 연관 증상이 있을 때
                (id 목록)
        """
        self.sweep(now)
        if not alarm.host_key:
            return None
        key = episode_key(alarm.zone, alarm.host_key)
        ep = self._episodes.get(key)
        if ep is None:
            ep = self._open(key, alarm, now)

        infra_dbs = ep.infra_db_ids()
        if alarm.source == SOURCE_POLESTAR and alarm.db_id:
            infra_dbs.add(alarm.db_id)
        db_ambiguous = len(infra_dbs) > 1

        signal: dict[str, Any] = {
            "episode_id": ep.id,
            "mode": self.mode,
            "host_key": alarm.host_key,
        }
        links: list[CrossSourceLink] = []
        for rule in self.rules:
            if not rule.effect.matches(alarm):
                continue
            for member in ep.members:
                link = evaluate_link(rule, member, alarm, db_ambiguous=db_ambiguous)
                if link is not None:
                    links.append(link)
        chosen = best_link(links)
        if chosen is not None:
            signal.update(chosen.to_dict())
            signal["applied"] = False
            ep.mark_cause(chosen.cause_alarm_id)

        related = self._related_effects(ep, alarm)
        if related:
            signal["related_effects"] = related
            ep.mark_cause(alarm.alarm_id)

        ep.members.append(EpisodeMember(alarm=alarm, arrived_at=now))
        ep.last_seen = now
        if len(ep.members) > self.max_members:
            overflow = len(ep.members) - self.max_members
            del ep.members[:overflow]
            # 잘린 멤버의 원인 기록도 뺀다(상한 ≤ max_members + 1). 대표는 남겨 mark_cause가
            # 대표를 다른 원인으로 바꾸지 않게 한다(「먼저 쓰인 원인」 의미 유지).
            kept = {m.alarm.alarm_id for m in ep.members}
            ep.cause_ids = [
                c for c in ep.cause_ids if c in kept or c == ep.representative_id
            ]
            self._evicted("members", overflow)
        return signal

    def record_tier(self, episode_id: str, alarm_id: str, tier: str | None) -> None:
        """판정 뒤 멤버의 최종 티어를 기록한다(사건이 이미 닫혔으면 무시)."""
        ep = self.get(episode_id)
        member = ep.member(alarm_id) if ep is not None else None
        if member is not None:
            member.tier = tier

    def resolve(self, alarm: EpisodeAlarm, now: float) -> str | None:
        """해소 알람 — 같은 사건의 같은 지문 멤버를 해소 처리하고, 전부 해소면 사건을 닫는다.

        Returns:
            해소 처리가 있었던 사건 id(없으면 None).
        """
        self.sweep(now)
        if not alarm.host_key or not alarm.fingerprint:
            return None
        key = episode_key(alarm.zone, alarm.host_key)
        ep = self._episodes.get(key)
        if ep is None:
            return None
        hit = False
        for m in ep.members:
            if not m.resolved and m.alarm.fingerprint == alarm.fingerprint:
                m.resolved = True
                hit = True
        if not hit:
            return None
        ep.last_seen = now
        if all(m.resolved for m in ep.members):
            self._close(key)
        return ep.id

    def sweep(self, now: float) -> int:
        """마지막 알람 뒤 idle_seconds가 지난 사건을 닫는다 → 닫은 수."""
        expired = [k for k, ep in self._episodes.items() if now - ep.last_seen > self.idle_seconds]
        for k in expired:
            self._close(k)
        return len(expired)

    # ── 내부 ───────────────────────────────────────────────────────
    def _open(self, key: str, alarm: EpisodeAlarm, now: float) -> Episode:
        if len(self._episodes) >= self.max_episodes:
            overflow = len(self._episodes) - self.max_episodes + 1
            oldest = sorted(self._episodes, key=lambda k: self._episodes[k].last_seen)[:overflow]
            for k in oldest:
                self._close(k)
            self._evicted("episodes", overflow)
        ep = Episode(
            id=episode_id_for(alarm.zone, alarm.host_key, alarm.occurred_at),
            zone=alarm.zone,
            host_key=alarm.host_key,
            opened_at=now,
            last_seen=now,
            representative_id=alarm.alarm_id,
        )
        self._episodes[key] = ep
        self._by_id[ep.id] = key
        return ep

    def _close(self, key: str) -> None:
        ep = self._episodes.pop(key, None)
        if ep is not None and self._by_id.get(ep.id) == key:
            del self._by_id[ep.id]

    def _evicted(self, what: str, count: int) -> None:
        if self._on_evict is not None:
            self._on_evict(what, count)

    def _related_effects(self, ep: Episode, cause: EpisodeAlarm) -> list[str]:
        """이 알람을 원인으로 하는 규칙의 증상 중 이미 통보된(page·ticket) 멤버 id(§5.4)."""
        out: list[str] = []
        for rule in self.rules:
            if not rule.cause.matches(cause):
                continue
            for m in ep.members:
                if (
                    m.tier in NOTIFIED_TIERS
                    and rule.effect.matches(m.alarm)
                    and in_window(rule, cause, m.alarm)
                    and m.alarm.alarm_id not in out
                ):
                    out.append(m.alarm.alarm_id)
        return out
