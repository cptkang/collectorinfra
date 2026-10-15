"""PAGE 필요성 재판정 (plans/162 · D-330 — 결정적 순수 함수 · 노이즈 게이트 마지막 관문).

PAGE는 「새 사건의 첫 알림」이다. 기존 15단계가 PAGE로 판정한 건만 다시 보고, 이미 통보된
사건의 반복으로 보이면 TICKET으로 내린다(억제 아님 — 감사 기록은 그대로 남는다 · D-048.4).
LLM은 쓰지 않는다.

강등 규칙(R1 → R2 → R3, 첫 적중):
    - R1 열린 사건의 반복: 같은 지문의 대표 PAGE가 미해소(또는 해소 뒤 재발 유예 안 재발)이고
      마지막 대표 PAGE로부터 리마인더 시간 미만.
    - R2 같은 호스트 진행 중 사건: 같은 호스트 키의 다른 지문 대표 PAGE가 호스트 창 안에 있고
      미해소이며, 이번 실효 심각도가 그 PAGE 이하.
    - R3 다수 호스트 동시 발생: 같은 동시발생 키(존·알람명)에 창 안 대표 PAGE 호스트가 상한 이상이고
      이번 호스트는 그 안에 없다.

보존 규칙(강등보다 우선): 새 사건의 첫 PAGE(R1~R3 불성립과 같은 뜻) · 심각도 상승(적중
규칙의 대표 심각도 초과) · 앱 영향 fatal · 미해소 리마인더(리마인더 시간 경과) · 운영자 지정
항상 PAGE(알람명 글롭).

해소 대용 규칙(R1 안 · 기본 꺼짐): 해소 이벤트가 오지 않는 출처의 사건은 레지스트리가 「무소식
닫힘」으로 닫는다 — 그 지문을 마지막으로 본 뒤(판정·지문 중복 재발 포함) 무소식 시간이 지나면
그 항목은 없는 것과 같다(닫혔고 재발 유예도 지난 상태). 그래서 다음 PAGE는 새 사건이다.

모드: off(평가 안 함 — 같은 객체를 그대로 돌려준다) · shadow(판정 불변, 근거 `page_review` 블록만) ·
enforce(TICKET으로 강등). 레지스트리 갱신은 shadow에서도 「했을 결과」 기준이다 — 그래야 shadow의
「했을 PAGE 수」가 enforce와 같다.

이 모듈은 domain 계층이라 표준 라이브러리와 `noise_gate.domain.*`만 쓴다. 설정은 덕 타이핑으로
읽는다.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, TypedDict

from noise_gate.domain.alarm import normalize_host_key
from noise_gate.domain.notification_policy import (
    STAGE_PAGE_REVIEW,
    STAGE_RESOLVED,
    STAGE_SELF_HEAL,
    TIER_PAGE,
    TIER_TICKET,
    NotificationDecision,
    _priority,
)

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
_REVIEW_MODES = frozenset({MODE_SHADOW, MODE_ENFORCE})

# 강등 규칙 식별자(근거 블록 `rule`).
RULE_OPEN_REPEAT = "R1"
RULE_SAME_HOST = "R2"
RULE_FANOUT = "R3"

# 보존 사유 식별자(근거 블록 `keep`) — 적중 규칙이 있어도 PAGE를 유지한 이유.
KEEP_ALWAYS_PAGE = "always_page"
KEEP_APP_IMPACT = "app_impact"
KEEP_REMINDER = "reminder"
KEEP_SEVERITY_RISE = "severity_rise"

# 결정 근거(`evidence`)와 판정 레코드(`stage_evidence`)에서 쓰는 블록 키.
EVIDENCE_KEY = "page_review"

# 레지스트리 키 상한(지문·동시발생 키 각각) — 넘치면 오래된 것부터 버린다(경고 콜백).
_REGISTRY_MAX_KEYS = 10000
# 창 밖 항목 전수 정리의 최소 간격(초) — 메모리 위생용이라 판정과 무관하다(규칙은 시각을 다시 본다).
_SWEEP_INTERVAL_SECONDS = 60.0
# 미해소 대표 PAGE를 기억하는 최소 시간(시) — 복원 창 max(reminder_hours, 24)와 같은 하한.
_MIN_RETENTION_HOURS = 24

_RESOLUTION_STAGES = frozenset({STAGE_RESOLVED, STAGE_SELF_HEAL})

HostKey = tuple[str, str]
FanoutKey = tuple[str, str]


class PageReviewSignals(TypedDict):
    """워커가 그래프 진입 전에 레지스트리에서 뽑은 재판정 재료(dict 직렬화 가능 · 상태로 실린다).

    시각은 전부 epoch 초다. `fingerprint_page`는 같은 지문의 대표 PAGE(없으면 None),
    `host_pages`는 같은 호스트 키의 **다른 지문** 미해소 대표 PAGE, `fanout_members`는 같은 동시발생
    키에 기록된 호스트(대표 여부 포함)다. 창·임계 판정은 `review_page`가 설정으로 한다.
    """

    now: float
    alarm_name: str
    host: str
    fingerprint_page: dict[str, Any] | None
    host_pages: list[dict[str, Any]]
    fanout_members: list[dict[str, Any]]


def host_key_of(db_id: str, server: str) -> HostKey:
    """호스트 키 = (db_id, 정규화 호스트). `server`는 server_name(없으면 hostname)이다.

    판정 레코드의 `server_name`도 같은 규칙(server_name 우선, 없으면 hostname)으로 기록되므로
    복원에서도 같은 키가 나온다.
    """
    return (str(db_id or ""), normalize_host_key(str(server or "")))


def fanout_key_of(db_id: str, alarm_name: str) -> FanoutKey:
    """동시발생 키 = (db_id, 알람명)."""
    return (str(db_id or ""), str(alarm_name or "").strip())


def is_resolution(signals: dict[str, Any] | None, stage: str) -> bool:
    """해소 결정인가 — 신호 심각도 0 또는 해소 단계(독립 해소·자가복구).

    심각도3 해소는 별도 해소 레코드가 남지 않으므로 결정 자체로 판별한다.
    """
    sev = (signals or {}).get("severity")
    return sev == 0 or stage in _RESOLUTION_STAGES


def _hhmm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _review_block(evidence: dict[str, Any] | None) -> dict[str, Any]:
    block = (evidence or {}).get(EVIDENCE_KEY)
    return block if isinstance(block, dict) else {}


# ── 재판정 ───────────────────────────────────────────────────────────────


def review_page(
    decision: NotificationDecision, signals: PageReviewSignals | dict[str, Any] | None, config: Any
) -> NotificationDecision:
    """PAGE 판정을 다시 본다(plans/162 §4.1~§4.3).

    `decision.tier`가 PAGE가 아니거나, 모드가 off이거나, 신호가 없으면 **같은 객체를 그대로**
    돌려준다. 그 밖에는 근거 `page_review` 블록을 더한 사본을 돌려준다 — shadow는 티어·사유·단계
    불변, enforce에서 강등이면 TICKET(`stage=page_review`, 원 단계·사유는 근거에 보존)이다.
    `signals`(§8.2 동결 스키마)와 `fingerprint`는 바꾸지 않는다.

    Args:
        decision: 앞 15단계(+ 앱 영향 재판정)의 최종 판정.
        signals: 워커가 만든 `PageReviewSignals`.
        config: `page_review_*` 필드를 가진 설정(NoiseGateConfig).

    Returns:
        판정(원본 또는 사본).
    """
    mode = str(getattr(config, "page_review_mode", MODE_OFF) or MODE_OFF)
    if decision.tier != TIER_PAGE or mode not in _REVIEW_MODES or not signals:
        return decision

    now = float(signals["now"])
    eff = _int((decision.signals or {}).get("effective_severity"))
    reminder_s = _int(getattr(config, "page_review_reminder_hours", 24), 24) * 3600
    quiet_s = _int(getattr(config, "page_review_reopen_quiet_minutes", 30), 30) * 60
    host_window_s = _int(getattr(config, "page_review_host_window_minutes", 30), 30) * 60
    fanout_window_s = _int(getattr(config, "page_review_fanout_window_minutes", 10), 10) * 60
    fanout_max = _int(getattr(config, "page_review_fanout_page_max", 1), 1)

    block: dict[str, Any] = {"mode": mode, "rule": None, "would_demote": False, "keep": None}
    rule: str | None = None
    rep_severity = 0
    reason = ""

    # R1 — 같은 지문의 열린 사건 반복 · 보존 ④ 리마인더 재료
    reminder_due = False
    fp_page = signals.get("fingerprint_page")
    if isinstance(fp_page, dict):
        resolved_ts = fp_page.get("resolved_ts")
        since_last = now - float(fp_page["last_page_ts"])
        is_open = resolved_ts is None or (now - float(resolved_ts)) <= quiet_s
        if is_open and since_last < reminder_s:
            rule = RULE_OPEN_REPEAT
            rep_severity = _int(fp_page.get("severity"))
            first_ts = float(fp_page["first_page_ts"])
            block.update(
                page_alarm_id=str(fp_page.get("alarm_id") or ""),
                last_page_alarm_id=str(fp_page.get("last_alarm_id") or ""),
                first_page_ts=_iso(first_ts),
                repeat_count=_int(fp_page.get("repeat_count")),
            )
            reason = f"PAGE 재판정 — 진행 중 사건 반복(최초 PAGE {_hhmm(first_ts)})"
        elif resolved_ts is None and since_last >= reminder_s:
            reminder_due = True
            block["unresolved_hours"] = int((now - float(fp_page["first_page_ts"])) // 3600)

    # R2 — 같은 호스트의 다른 사건 진행 중(이번 실효 심각도가 그 PAGE 이하)
    if rule is None and signals.get("host"):
        candidates = [
            p for p in signals.get("host_pages") or []
            if now - float(p["page_ts"]) <= host_window_s and eff <= _int(p.get("severity"))
        ]
        if candidates:
            hit = max(candidates, key=lambda p: float(p["page_ts"]))
            rule = RULE_SAME_HOST
            rep_severity = _int(hit.get("severity"))
            block.update(
                host_page_alarm_id=str(hit.get("alarm_id") or ""),
                host_page_ts=_iso(float(hit["page_ts"])),
            )
            reason = "PAGE 재판정 — 동일 호스트 사건 진행 중"

    # R3 — 다수 호스트 동시 발생의 대표 외
    if rule is None and signals.get("host") and signals.get("alarm_name"):
        in_window = [
            h for h in signals.get("fanout_members") or []
            if now - float(h["page_ts"]) <= fanout_window_s
        ]
        reps = [h for h in in_window if h.get("representative")]
        rep_hosts = {str(h.get("host") or "") for h in reps}
        if reps and len(reps) >= fanout_max and signals["host"] not in rep_hosts:
            first = min(reps, key=lambda h: float(h["page_ts"]))
            hosts = {str(h.get("host") or "") for h in in_window} | {signals["host"]}
            rule = RULE_FANOUT
            rep_severity = _int(first.get("severity"))
            block.update(
                fanout_hosts=len(hosts),
                representative_alarm_id=str(first.get("alarm_id") or ""),
            )
            reason = f"PAGE 재판정 — 동시 발생 {len(hosts)}대 중 대표 외"

    keep: str | None = None
    if rule is not None:
        globs = getattr(config, "page_review_always_page_globs", None) or []
        alarm_name = str(signals.get("alarm_name") or "")
        if alarm_name and any(fnmatchcase(alarm_name, g) for g in globs):
            keep = KEEP_ALWAYS_PAGE
        elif _int((decision.evidence or {}).get("app_impact_fatal_events")) > 0:
            keep = KEEP_APP_IMPACT
        elif reminder_due:
            keep = KEEP_REMINDER
        elif eff > rep_severity:
            keep = KEEP_SEVERITY_RISE
    elif reminder_due:
        keep = KEEP_REMINDER

    would_demote = rule is not None and keep is None
    block.update(rule=rule, would_demote=would_demote, keep=keep)
    evidence = {**(decision.evidence or {}), EVIDENCE_KEY: block}

    if mode != MODE_ENFORCE or not would_demote:
        return dataclasses.replace(decision, evidence=evidence)

    evidence["original_stage"] = decision.stage
    evidence["original_reason"] = decision.reason
    importance = str((decision.signals or {}).get("importance") or "보통")
    return dataclasses.replace(
        decision,
        tier=TIER_TICKET,
        reason=reason,
        priority=_priority(TIER_TICKET, eff, importance),
        stage=STAGE_PAGE_REVIEW,
        evidence=evidence,
    )


# ── 워커 상태 ────────────────────────────────────────────────────────────


@dataclasses.dataclass
class _PageEntry:
    """지문 하나의 대표 PAGE 상태.

    `alarm_id`는 그 사건의 **최초** PAGE id로 고정한다(`first_page_ts`와 같은 기준). 같은 사건
    안의 심각도 상승·리마인더 PAGE는 `last_alarm_id`·`last_page_ts`만 바꾼다.
    """

    first_page_ts: float
    last_page_ts: float
    severity: int
    alarm_id: str
    host_key: HostKey
    resolved_ts: float | None = None
    repeat_count: int = 0
    last_alarm_id: str = ""
    # 해소 대용 규칙 — 해소 이벤트가 오지 않는 출처 표시 · 그 지문을 마지막으로 본 시각.
    silent: bool = False
    last_seen_ts: float = 0.0


class PageRegistry:
    """대표 PAGE 레지스트리(plans/162 §4.5 — 워커 메모리 · 순수 · 시각은 호출자가 넣는다).

    - 지문 → 대표 PAGE(최초·마지막 시각 · 심각도 · 알람 id · 해소 시각 · 반복 수)
    - 호스트 키 → 그 호스트의 미해소 대표 PAGE 지문
    - 동시발생 키 → 창 안 호스트(대표 여부 · 시각 · 알람 id · 심각도)

    창 밖 항목은 주기적으로 정리하고(`_SWEEP_INTERVAL_SECONDS`), 키 수가 상한을 넘으면 오래된 것부터
    버린다(`on_evict(무엇, 건수)` 경고 콜백).

    `idle_close_minutes` > 0이면 해소 이벤트가 오지 않는 출처로 표시된 지문을 마지막으로 본 뒤
    그 시간이 지난 항목을 없는 것으로 본다(무소식 닫힘). 0(기본)이면 현행과 같다.
    """

    def __init__(
        self,
        *,
        host_window_minutes: int = 30,
        fanout_window_minutes: int = 10,
        reminder_hours: int = 24,
        reopen_quiet_minutes: int = 30,
        idle_close_minutes: int = 0,
        max_keys: int = _REGISTRY_MAX_KEYS,
        on_evict: Callable[[str, int], None] | None = None,
    ) -> None:
        self._host_window_s = max(int(host_window_minutes), 0) * 60
        self._fanout_window_s = max(int(fanout_window_minutes), 0) * 60
        self._quiet_s = max(int(reopen_quiet_minutes), 0) * 60
        self._idle_close_s = max(int(idle_close_minutes), 0) * 60
        self._retention_s = 2 * max(int(reminder_hours), _MIN_RETENTION_HOURS) * 3600
        self._max_keys = max(int(max_keys), 1)
        self._on_evict = on_evict
        self._pages: dict[str, _PageEntry] = {}
        self._by_host: dict[HostKey, dict[str, None]] = {}
        self._fanout: dict[FanoutKey, dict[str, dict[str, Any]]] = {}
        self._swept_at = float("-inf")

    @staticmethod
    def restore_window_seconds(reminder_hours: int) -> int:
        """기동 복원 창(초) = max(reminder_hours, 24)시간."""
        return max(int(reminder_hours), _MIN_RETENTION_HOURS) * 3600

    def __len__(self) -> int:
        return len(self._pages)

    # ── 조회 ──

    def signals_for(
        self, fingerprint: str, host_key: HostKey, fanout_key: FanoutKey, now: float
    ) -> PageReviewSignals:
        """이 알람의 재판정 재료를 만든다(레지스트리 상태 불변 — 주기 정리만 할 수 있다)."""
        self._maybe_sweep(now)
        entry = self._pages.get(fingerprint)
        fp_page = None
        if entry is not None and not self._idle_closed(entry, now):
            fp_page = {
                "first_page_ts": entry.first_page_ts,
                "last_page_ts": entry.last_page_ts,
                "severity": entry.severity,
                "alarm_id": entry.alarm_id,
                "last_alarm_id": entry.last_alarm_id,
                "resolved_ts": entry.resolved_ts,
                "repeat_count": entry.repeat_count,
            }
        host_pages: list[dict[str, Any]] = []
        if host_key[1]:
            for fp in self._by_host.get(host_key, {}):
                other = self._pages.get(fp)
                if (
                    fp == fingerprint
                    or other is None
                    or other.resolved_ts is not None
                    or self._idle_closed(other, now)
                ):
                    continue
                host_pages.append({  # 그 사건의 최신 PAGE(id·시각 같은 기준)
                    "alarm_id": other.last_alarm_id,
                    "page_ts": other.last_page_ts,
                    "severity": other.severity,
                })
        fanout_members = [
            {"host": host, **meta} for host, meta in self._fanout.get(fanout_key, {}).items()
        ]
        return {
            "now": float(now),
            "alarm_name": fanout_key[1],
            "host": host_key[1],
            "fingerprint_page": fp_page,
            "host_pages": host_pages,
            "fanout_members": fanout_members,
        }

    # ── 갱신 ──

    def observe(
        self,
        decision: NotificationDecision,
        *,
        fingerprint: str,
        host_key: HostKey,
        fanout_key: FanoutKey,
        alarm_id: str,
        now: float,
        silent_source: bool = False,
    ) -> None:
        """그래프가 낸 최종 판정을 반영한다.

        해소 결정이면 그 지문의 사건을 닫는다. 「했을 결과」가 PAGE면 대표로 등록·갱신하고, 강등
        (enforce의 `page_review` 단계 또는 shadow의 `would_demote`)이면 대표의 반복 수만 올린다.
        티어와 무관하게 그 지문 항목의 마지막 관측 시각을 올리고, `silent_source`면 그 항목을 해소
        이벤트가 오지 않는 출처로 표시한다.
        """
        self._apply(
            tier=str(decision.tier),
            stage=str(decision.stage or ""),
            signals=decision.signals,
            evidence=decision.evidence,
            fingerprint=fingerprint,
            host_key=host_key,
            fanout_key=fanout_key,
            alarm_id=alarm_id,
            now=now,
            silent=silent_source,
        )
        self._maybe_sweep(now)

    def touch(self, fingerprint: str, now: float) -> None:
        """지문 중복으로 버려진 재발을 「봤음」으로 통지한다 — 기존 항목의 마지막 관측 시각만
        올린다.

        항목이 없거나 이미 무소식 닫힘이면 아무것도 하지 않는다.
        """
        entry = self._pages.get(fingerprint)
        if entry is not None and not self._idle_closed(entry, now):
            entry.last_seen_ts = max(entry.last_seen_ts, float(now))

    def restore(
        self,
        records: Iterable[dict[str, Any]],
        now: float,
        *,
        silent_source: Callable[[dict[str, Any]], bool] | None = None,
    ) -> int:
        """판정 레코드(dict, 시간순) 꼬리로 레지스트리를 다시 채운다(기동 시 1회).

        `observe`와 같은 규칙을 쓴다 — `tier=="page"`이면서
        `stage_evidence.page_review.would_demote`가 아닌 레코드만 대표, `stage=="page_review"`·
        would_demote는 대표의 반복 수만, 해소 결정은 닫힘.
        복원 창(`restore_window_seconds`) 밖·시각 불명 레코드는 건너뛴다.

        무소식 닫힘이 켜져 있으면(`idle_close_minutes` > 0) `silent_source(레코드)`로 해소 이벤트가
        오지 않는 출처를 판정하고, `type=="recurrence"` 레코드(지문 중복 재발)는 같은 창 규칙으로
        `touch`한다. 그 밖의 `type` 레코드는 건너뛴다. 꺼져 있으면 현행과 같다.

        Returns:
            반영한 레코드 수.
        """
        horizon = now - self._retention_s
        applied = 0
        for rec in records:
            if not isinstance(rec, dict):
                continue
            rec_type = rec.get("type")
            if rec_type and not (rec_type == "recurrence" and self._idle_close_s > 0):
                continue
            ts = _record_epoch(rec.get("ts"))
            if ts is None or ts < horizon or ts > now:
                continue
            if rec_type:
                self.touch(str(rec.get("fingerprint") or ""), ts)
                applied += 1
                continue
            db_id = str(rec.get("db_id") or "")
            self._apply(
                tier=str(rec.get("tier") or ""),
                stage=str(rec.get("stage") or ""),
                signals=rec.get("signals") if isinstance(rec.get("signals"), dict) else {},
                evidence=(
                    rec.get("stage_evidence") if isinstance(rec.get("stage_evidence"), dict) else {}
                ),
                fingerprint=str(rec.get("fingerprint") or ""),
                host_key=host_key_of(db_id, str(rec.get("server_name") or "")),
                fanout_key=fanout_key_of(db_id, str(rec.get("alarm_name") or "")),
                alarm_id=str(rec.get("alarm_id") or ""),
                now=ts,
                silent=bool(silent_source(rec)) if silent_source is not None else False,
            )
            applied += 1
        self._sweep(now)
        return applied

    def _apply(
        self,
        *,
        tier: str,
        stage: str,
        signals: dict[str, Any] | None,
        evidence: dict[str, Any] | None,
        fingerprint: str,
        host_key: HostKey,
        fanout_key: FanoutKey,
        alarm_id: str,
        now: float,
        silent: bool = False,
    ) -> None:
        if not fingerprint:
            return
        entry = self._pages.get(fingerprint)
        if entry is not None and self._idle_closed(entry, now):
            # 무소식 닫힘 — 없는 항목과 같으므로 지우고 새로 판단한다.
            del self._pages[fingerprint]
            self._unlink_host(fingerprint, entry.host_key)
        self._apply_decision(
            tier=tier, stage=stage, signals=signals, evidence=evidence,
            fingerprint=fingerprint, host_key=host_key, fanout_key=fanout_key,
            alarm_id=alarm_id, now=now,
        )
        entry = self._pages.get(fingerprint)
        if entry is not None:
            entry.last_seen_ts = max(entry.last_seen_ts, float(now))
            if silent:
                entry.silent = True

    def _apply_decision(
        self,
        *,
        tier: str,
        stage: str,
        signals: dict[str, Any] | None,
        evidence: dict[str, Any] | None,
        fingerprint: str,
        host_key: HostKey,
        fanout_key: FanoutKey,
        alarm_id: str,
        now: float,
    ) -> None:
        if is_resolution(signals, stage):
            entry = self._pages.get(fingerprint)
            if entry is not None and entry.resolved_ts is None:
                entry.resolved_ts = now
                self._unlink_host(fingerprint, entry.host_key)
            return

        block = _review_block(evidence)
        if stage == STAGE_PAGE_REVIEW or block.get("would_demote") is True:
            self._count_repeat(block, fingerprint, host_key, fanout_key, now)
            return
        if tier != TIER_PAGE:
            return

        severity = _int((signals or {}).get("effective_severity"))
        entry = self._pages.get(fingerprint)
        same_incident = entry is not None and (
            entry.resolved_ts is None or now - entry.resolved_ts <= self._quiet_s
        )
        if entry is not None and same_incident:
            entry.last_page_ts = now
            entry.severity = severity
            entry.last_alarm_id = alarm_id  # 최초 PAGE id(`alarm_id`)는 고정
            entry.resolved_ts = None
        else:
            self._pages.pop(fingerprint, None)
            self._pages[fingerprint] = _PageEntry(
                first_page_ts=now, last_page_ts=now, severity=severity,
                alarm_id=alarm_id, host_key=host_key, last_alarm_id=alarm_id,
            )
        if host_key[1]:
            self._by_host.setdefault(host_key, {})[fingerprint] = None
        if host_key[1] and fanout_key[1]:
            self._fanout.setdefault(fanout_key, {})[host_key[1]] = {
                "page_ts": now, "alarm_id": alarm_id, "severity": severity,
                "fingerprint": fingerprint, "representative": True,
            }
        self._cap()

    def _count_repeat(
        self,
        block: dict[str, Any],
        fingerprint: str,
        host_key: HostKey,
        fanout_key: FanoutKey,
        now: float,
    ) -> None:
        """강등(또는 했을 강등) 1건을 대표의 반복 수에 더한다."""
        rule = block.get("rule")
        target: _PageEntry | None = None
        if rule == RULE_OPEN_REPEAT:
            target = self._pages.get(fingerprint)
            if target is not None and target.resolved_ts is not None:
                # 해소 뒤 재발 유예 안의 재발 — 같은 사건이 다시 열렸다.
                target.resolved_ts = None
                if target.host_key[1]:
                    self._by_host.setdefault(target.host_key, {})[fingerprint] = None
        elif rule == RULE_SAME_HOST:
            wanted = str(block.get("host_page_alarm_id") or "")
            for fp in self._by_host.get(host_key, {}):
                entry = self._pages.get(fp)
                if entry is not None and entry.last_alarm_id == wanted:
                    target = entry
                    break
        elif rule == RULE_FANOUT:
            wanted = str(block.get("representative_alarm_id") or "")
            hosts = self._fanout.get(fanout_key, {})
            for meta in hosts.values():
                if meta.get("representative") and meta.get("alarm_id") == wanted:
                    target = self._pages.get(str(meta.get("fingerprint") or ""))
                    break
            if host_key[1] and fanout_key[1] and host_key[1] not in hosts:
                self._fanout.setdefault(fanout_key, {})[host_key[1]] = {
                    "page_ts": now, "alarm_id": "", "severity": 0,
                    "fingerprint": fingerprint, "representative": False,
                }
        if target is not None:
            target.repeat_count += 1

    # ── 정리 ──

    def _unlink_host(self, fingerprint: str, host_key: HostKey) -> None:
        fps = self._by_host.get(host_key)
        if fps is None:
            return
        fps.pop(fingerprint, None)
        if not fps:
            del self._by_host[host_key]

    def _idle_closed(self, entry: _PageEntry, now: float) -> bool:
        """무소식 닫힘인가 — 켜져 있고, 해소 이벤트가 오지 않는 출처이며, 마지막 관측 뒤 시간
        초과."""
        return (
            self._idle_close_s > 0
            and entry.silent
            and now - entry.last_seen_ts > self._idle_close_s
        )

    def _maybe_sweep(self, now: float) -> None:
        if now - self._swept_at >= _SWEEP_INTERVAL_SECONDS:
            self._sweep(now)

    def _sweep(self, now: float) -> None:
        """창 밖 항목을 지운다 — 해소 뒤 재발 유예가 지난 지문 · 보존 시간이 지난 미해소 지문 ·
        무소식 닫힘 지문 · 호스트 창이 지난 호스트 연결 · 동시발생 창이 지난 호스트."""
        self._swept_at = now
        for fp, entry in list(self._pages.items()):
            stale = (
                now - entry.resolved_ts > self._quiet_s
                if entry.resolved_ts is not None
                else now - entry.last_page_ts > self._retention_s
            ) or self._idle_closed(entry, now)
            if stale:
                del self._pages[fp]
                self._unlink_host(fp, entry.host_key)
        for key, fps in list(self._by_host.items()):
            for fp in list(fps):
                linked = self._pages.get(fp)
                if (
                    linked is None
                    or linked.resolved_ts is not None
                    or now - linked.last_page_ts > self._host_window_s
                ):
                    fps.pop(fp, None)
            if not fps:
                del self._by_host[key]
        for fkey, hosts in list(self._fanout.items()):
            for host, meta in list(hosts.items()):
                if now - float(meta["page_ts"]) > self._fanout_window_s:
                    del hosts[host]
            if not hosts:
                del self._fanout[fkey]

    def _cap(self) -> None:
        """키 상한 — 지문은 마지막 PAGE가, 동시발생 키는 최근 기록이 가장 이른 것부터 버린다.

        무소식 닫힘이 켜져 있으면 지문은 마지막 PAGE와 마지막 관측 중 늦은 시각으로 잰다.
        """
        overflow = len(self._pages) - self._max_keys
        if overflow > 0:
            def _recency(k: str) -> float:
                entry = self._pages[k]
                if self._idle_close_s > 0:  # 무소식 닫힘이 켜져 있으면 마지막 관측도 최근성이다
                    return max(entry.last_page_ts, entry.last_seen_ts)
                return entry.last_page_ts

            for fp in sorted(self._pages, key=_recency)[:overflow]:
                entry = self._pages.pop(fp)
                self._unlink_host(fp, entry.host_key)
            if self._on_evict is not None:
                self._on_evict("지문", overflow)
        overflow = len(self._fanout) - self._max_keys
        if overflow > 0:
            def _latest(k: FanoutKey) -> float:
                return max(float(m["page_ts"]) for m in self._fanout[k].values())

            for fkey in sorted(self._fanout, key=_latest)[:overflow]:
                del self._fanout[fkey]
            if self._on_evict is not None:
                self._on_evict("동시발생 키", overflow)


def _record_epoch(raw: Any) -> float | None:
    """판정 레코드 ts(ISO8601)를 epoch 초로 바꾼다(실패 시 None)."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).timestamp()
    except (TypeError, ValueError):
        return None
