"""알람 발송 판단 정책 (Plan 52 Phase E1 — 결정적 순수함수).

4-티어 라우팅(PAGE/TICKET/DASHBOARD/SUPPRESS)을 결정적 규칙으로 산출한다.
LLM(`is_routine`)은 보조 입력일 뿐이며, 하드 규칙·심각도3 PAGE·재현율 우선은 불변이다(D-035/D-041).

설계 원칙:
    - 재현율 우선·비용 비대칭: 불확실·신호 수집 실패·미식별 중요도 → 보수적 PAGE.
    - 심각도 3은 어떤 억제 단계도 거치지 않고 항상 PAGE.
    - 억제 ≠ 삭제: 억제·강등 결정도 감사 기록 대상(decision_store).

이 모듈은 domain 계층에 위치하므로 표준 라이브러리만 의존한다(src 내 다른 모듈 import 금지).
event/history_stats/analysis/config는 덕 타이핑으로 소비하며 타입에 결합하지 않는다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

# ── 4-티어 상수 ─────────────────────────────────────────────
TIER_PAGE = "page"
TIER_TICKET = "ticket"
TIER_DASHBOARD = "dashboard"
TIER_SUPPRESS = "suppress"

# 티어 순위(높을수록 시급) — 보조 조정의 승격/강등 1단계 이동 기준
_TIER_RANK: dict[str, int] = {
    TIER_SUPPRESS: 0,
    TIER_DASHBOARD: 1,
    TIER_TICKET: 2,
    TIER_PAGE: 3,
}
_RANK_TIER: dict[int, str] = {rank: tier for tier, rank in _TIER_RANK.items()}

# 중요도 라벨 → 우선순위 가중치
_IMPORTANCE_WEIGHT: dict[str, int] = {"낮음": 1, "보통": 2, "높음": 3}
_VALID_IMPORTANCE = frozenset(_IMPORTANCE_WEIGHT.keys())


@dataclass
class NotificationDecision:
    """발송 판단 결과(감사·설명가능성용 스냅샷 포함)."""

    tier: str          # page | ticket | dashboard | suppress
    reason: str        # 결정 근거(한국어) — 어느 단계에서 무슨 신호로 판단했는지
    priority: int      # 산출 우선순위(높을수록 시급)
    signals: dict      # 사용된 신호 스냅샷(§8.2 키 스키마)
    fingerprint: str = ""


def compute_fingerprint(event) -> str:
    """재발생 dedup용 안정 식별자를 산출한다.

    `f(db_id, server_name|hostname, alarm_name, resource_name)` 조합의 SHA-1 해시.
    server_name을 우선 사용하고, 없으면 hostname으로 대체한다(§6.1).
    """
    db_id = str(getattr(event, "db_id", "") or "")
    server = str(getattr(event, "server_name", "") or "") or str(
        getattr(event, "hostname", "") or ""
    )
    alarm_name = str(getattr(event, "alarm_name", "") or "")
    resource_name = str(getattr(event, "resource_name", "") or "")
    raw = "\x1f".join([db_id, server, alarm_name, resource_name])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def map_importance(importance_id, importance_value_map: dict[str, str]) -> str:
    """IMPORTANCE_ID 원값을 '낮음'|'보통'|'높음'으로 매핑한다.

    미매핑·None·인스턴스별 코드 미확정 → '보통'(보수적 기본값, §6.3·R-4).
    """
    if importance_id is None:
        return "보통"
    label = (importance_value_map or {}).get(str(importance_id))
    if label in _VALID_IMPORTANCE:
        return label
    return "보통"


def _matrix_tier(effective_severity: int, importance: str) -> str:
    """심각도×중요도 우선순위 매트릭스(§3.2)로 기본 티어를 산출한다.

    심각도 2 {높음:PAGE, 보통:TICKET, 낮음:DASHBOARD}
    심각도 1 {높음:TICKET, 보통:DASHBOARD, 낮음:SUPPRESS}
    그 외(정의되지 않은 심각도)는 보수적으로 PAGE.
    """
    if effective_severity == 2:
        return {"높음": TIER_PAGE, "보통": TIER_TICKET, "낮음": TIER_DASHBOARD}[importance]
    if effective_severity == 1:
        return {"높음": TIER_TICKET, "보통": TIER_DASHBOARD, "낮음": TIER_SUPPRESS}[importance]
    return TIER_PAGE


def _priority(tier: str, effective_severity: int, importance: str) -> int:
    """티어·실효심각도·중요도로 정수 우선순위를 산출한다(높을수록 시급)."""
    return (
        _TIER_RANK[tier] * 100
        + max(effective_severity, 0) * 10
        + _IMPORTANCE_WEIGHT.get(importance, 2)
    )


def decide_notification(
    event,
    history_stats,
    analysis,
    noise_ctx,
    config,
    *,
    self_heal: bool = False,
) -> NotificationDecision:
    """E1 결정 파이프라인(순서형·결정적, 첫 종착 확정).

    심각도 3은 모든 억제 단계를 단락(short-circuit)하고 항상 PAGE.
    config 속성은 안전 접근(getattr 기본값)한다:
        suppress_max_severity(기본 2), importance_value_map(dict), resolved_to_dashboard(기본 False).

    noise_ctx 계약: {importance_id, maintenance, noti_policy, parent_avail_status, source}
    수집 실패(None 또는 source=="unavailable") 시 보수화 후 effective_severity>=1이면 PAGE.
    """
    suppress_max_severity = int(getattr(config, "suppress_max_severity", 2))
    importance_value_map = getattr(config, "importance_value_map", {}) or {}
    resolved_to_dashboard = bool(getattr(config, "resolved_to_dashboard", False))

    # ── step 1: 실효 심각도 (E1은 AI 보강 없음) ──────────────
    severity = int(getattr(event, "severity", 0) or 0)
    effective_severity = severity  # E1: ai_severity = None

    # ── step 2: 신호 수집 실패 시 보수화 ─────────────────────
    collection_failed = noise_ctx is None or noise_ctx.get("source") == "unavailable"
    if collection_failed:
        importance = "보통"
        maintenance = False
        noti_policy = None
    else:
        importance = map_importance(noise_ctx.get("importance_id"), importance_value_map)
        maintenance = bool(noise_ctx.get("maintenance"))
        noti_policy = noise_ctx.get("noti_policy")

    # LLM 보조 입력(패턴·is_routine)은 수집 실패와 무관하게 해석에 사용
    pattern = ""
    if analysis is not None:
        pattern = str(getattr(analysis, "pattern_type", "") or "")
    if not pattern and history_stats is not None:
        pattern = str(getattr(history_stats, "pre_classification", "") or "")
    is_routine = getattr(analysis, "is_routine", None) if analysis is not None else None

    def _signals() -> dict:
        """§8.2 동결 스키마(모든 키 필수)로 신호 스냅샷을 구성한다."""
        return {
            "severity": severity,
            "ai_severity": None,
            "effective_severity": effective_severity,
            "importance": importance,
            "maintenance": maintenance,
            "parent_avail_status": None,
            "pattern": pattern,
            "is_routine": is_routine,
            "noti_policy": noti_policy,
            "flapping": False,
            "self_heal": bool(self_heal),
            "storm": False,
        }

    def _decision(tier: str, reason: str) -> NotificationDecision:
        return NotificationDecision(
            tier=tier,
            reason=reason,
            priority=_priority(tier, effective_severity, importance),
            signals=_signals(),
            fingerprint=compute_fingerprint(event),
        )

    # ── step 3: 심각도 3 → 즉시 PAGE(단락, 억제 금지 D-035) ──
    if effective_severity == 3:
        return _decision(TIER_PAGE, "심각도3 — 항상 통보(억제 단계 미경유)")

    # ── step 4: 해소(severity 0)/자가복구 상관(매트릭스 미경유) ─
    if bool(getattr(event, "is_clear", False)):
        if self_heal:
            return _decision(TIER_SUPPRESS, "자가복구 상관 — 발생 알람과 해소 매칭으로 억제")
        if resolved_to_dashboard:
            return _decision(TIER_DASHBOARD, "독립 해소 — 대시보드 표시(통보 없음)")
        return _decision(TIER_SUPPRESS, "독립 해소 — 매칭 발생 없음, 감사 기록만")

    # ── step 5: 수집 실패 보수 처리(심각도/해소 규칙 다음) ────
    if collection_failed and effective_severity >= 1:
        return _decision(TIER_PAGE, "신호 수집 실패 — 보수적 PAGE")

    # ── step 6: 유지보수 모드 → SUPPRESS(기록 유지) ──────────
    if maintenance:
        return _decision(TIER_SUPPRESS, "유지보수 모드 — 신규 발송 억제(감사 기록)")

    # ── step 7: 우선순위 매트릭스(§3.2) ─────────────────────
    base_tier = _matrix_tier(effective_severity, importance)

    # ── step 8: 보조 조정(1단계 이내·보수적, 승격 우선) ──────
    promote: list[str] = []
    demote: list[str] = []
    if noti_policy == "notify":
        promote.append("폴스타 통보 정책(notify)")
    if noti_policy == "suppress":
        demote.append("폴스타 비통보 정책(suppress)")
    if is_routine is True and effective_severity <= suppress_max_severity:
        demote.append("일상 반복 패턴(is_routine)")
    if is_routine is False:
        promote.append("비일상 패턴(is_routine=False)")

    tier = base_tier
    adjust_note = ""
    if promote:  # 승격/강등 충돌 시 승격 우선(재현율 우선)
        adjusted = min(_TIER_RANK[base_tier] + 1, _TIER_RANK[TIER_PAGE])
        tier = _RANK_TIER[adjusted]
        adjust_note = " · 승격: " + ", ".join(promote)
        if demote:
            adjust_note += f" (강등 신호 {', '.join(demote)}는 승격 우선으로 무시)"
    elif demote:
        adjusted = max(_TIER_RANK[base_tier] - 1, _TIER_RANK[TIER_SUPPRESS])
        tier = _RANK_TIER[adjusted]
        adjust_note = " · 강등: " + ", ".join(demote)

    reason = (
        f"매트릭스(심각도{effective_severity}×중요도{importance}) → {base_tier}{adjust_note}"
        f" → 최종 {tier}"
    )
    return _decision(tier, reason)
