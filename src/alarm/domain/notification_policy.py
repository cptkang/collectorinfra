"""알람 발송 판단 정책 (Plan 52 Phase E1 — 결정적 순수함수).

4-티어 라우팅(PAGE/TICKET/DASHBOARD/SUPPRESS)을 결정적 규칙으로 산출한다.
LLM(`is_routine`)은 보조 입력일 뿐이며, 하드 규칙·심각도3 PAGE·재현율 우선은 불변이다(D-035/D-048).

설계 원칙:
    - 재현율 우선·비용 비대칭: 불확실·신호 수집 실패·미식별 중요도 → 보수적 PAGE.
    - 심각도 3은 어떤 억제 단계도 거치지 않고 항상 PAGE.
    - 억제 ≠ 삭제: 억제·강등 결정도 감사 기록 대상(decision_store).

이 모듈은 domain 계층에 위치하므로 표준 라이브러리만 의존한다(src 내 다른 모듈 import 금지).
event/history_stats/analysis/config는 덕 타이핑으로 소비하며 타입에 결합하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

# ── E7-b 비알람 사전분류 마커(§17.4, 결정적·재현율 우선) ──────────────────
# 알람 마커: 운영 알람이면 통상 아래 중 하나를 텍스트에 담는다.
_ALARM_MARKERS = re.compile(
    r"가용성|사용률|임계|초과|미만|경고|장애|Down|DOWN|down|Fail|fail|Error|error|Critical"
)
# 비알람 마커: 승인/안내/공지성 메시지(알람 마커 부재 시에만 억제 근거).
_NON_ALARM_MARKERS = re.compile(r"승인|요청|바랍니다|안내|공지|문의")

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


def is_operational_alarm(event) -> bool:
    """이벤트가 운영 알람인지 결정적 마커로 판정한다 (Plan 60 E7-b · §17.4).

    알람 마커(가용성/사용률/임계/DOWN/장애/경고 등)가 **부재**하고 비알람 마커(승인/요청/
    바랍니다/안내/공지/문의)가 **존재**할 때만 False(비알람)로 판정한다. 그 외에는 True —
    **애매하면 알람 간주**(재현율 우선, 비알람 오분류가 실알람을 삼키지 않도록). 결정적 규칙이며
    LLM은 이 판정을 뒤집어 억제하지 못한다(억제는 결정적 마커가 확정, D-035·SOC 한계연구 정합).

    event의 자유 텍스트 필드(alarm_name·condition_log·conditions·resource_name·resource_type)를
    덕 타이핑으로 소비하며 stdlib(정규식)만 사용한다. 표준 라이브러리만 의존한다(§17.7 domain 순수성).

    Args:
        event: 알람 이벤트(alarm_name/condition_log/conditions/resource_name/resource_type 속성).

    Returns:
        운영 알람으로 판정되면 True, 비알람(승인/안내성)이면 False.
    """
    parts = [
        str(getattr(event, "alarm_name", "") or ""),
        str(getattr(event, "condition_log", "") or ""),
        str(getattr(event, "conditions", "") or ""),
        str(getattr(event, "resource_name", "") or ""),
        str(getattr(event, "resource_type", "") or ""),
        str(getattr(event, "description", "") or ""),
    ]
    text = " ".join(parts)
    has_alarm_marker = bool(_ALARM_MARKERS.search(text))
    has_non_alarm_marker = bool(_NON_ALARM_MARKERS.search(text))
    if not has_alarm_marker and has_non_alarm_marker:
        return False
    return True  # 애매하면 알람(재현율 우선)


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
    심각도 1 {높음:TICKET, 보통:DASHBOARD, 낮음:DASHBOARD}
    그 외(정의되지 않은 심각도)는 보수적으로 PAGE.
    """
    if effective_severity == 2:
        return {"높음": TIER_PAGE, "보통": TIER_TICKET, "낮음": TIER_DASHBOARD}[importance]
    if effective_severity == 1:
        # (E3 결정: 저중요도 주의알람은 묵살 아닌 대시보드 강등) "낮음" SUPPRESS→DASHBOARD
        return {"높음": TIER_TICKET, "보통": TIER_DASHBOARD, "낮음": TIER_DASHBOARD}[importance]
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
    inhibited: bool = False,
    flapping: bool = False,
    storm: bool = False,
    correlated: bool = False,
    annotation: dict | None = None,
) -> NotificationDecision:
    """E1 결정 파이프라인(순서형·결정적, 첫 종착 확정) + E2 의존성/인히비션/플래핑/스톰 단계.

    심각도 3은 모든 억제 단계를 단락(short-circuit)하고 항상 PAGE.
    config 속성은 안전 접근(getattr 기본값)한다:
        suppress_max_severity(기본 2), importance_value_map(dict), resolved_to_dashboard(기본 False),
        dependency_suppression(기본 False·E2), inhibition_enabled(기본 False·E2),
        flapping_enabled(기본 False·E2), storm_grouping_enabled(기본 False·E2),
        enable_llm_actionability(기본 False·E4).

    noise_ctx 계약: {importance_id, maintenance, noti_policy, parent_avail_status,
        cascaded, root_resource, root_resource_name(=E4), root_notified(=E4 enricher 산출),
        change_nearby, change_candidates(=E5), source}
    수집 실패(None 또는 source=="unavailable") 시 보수화 후 effective_severity>=1이면 PAGE.

    E5 추가(step 9 보조 조정 — §7.2): noise_ctx["change_nearby"]가 True면 promote에
        "변경 근접(원인성)"을 추가한다(**억제 아님·승격만** — 원인성 판단·PAGE 근거 보강,
        재현율 우선). change_correlation off·미근접이면 무영향. change 모듈 import 없이 bool만 소비.

    E4 다홉 하이브리드(step6.4, §6.2 — multi_hop_cascade_enabled·dependency_suppression AND):
        - dependency_suppression + noise_ctx["cascaded"](다홉 조상 비정상)일 때:
          root_notified=True(근본원인 노드 통보됨) → SUPPRESS(정밀 억제),
          root_notified=False(근본원인 미통보) → DASHBOARD 강등(재현율 우선 하이브리드).
        - cascaded 미제공(1홉 모드·수집 실패)이면 현행 parent_avail_status 판정으로 폴백(무변경).
        - 이 모듈은 topology 그래프를 import하지 않고 noise_ctx의 bool/id만 소비한다(순수성).

    E2 크로스-호스트 상관(step7.5 — cross_host_correlation_enabled 뒤, 기본 False면 무변경):
        - correlated(인자)는 워커가 온라인 그리디 군집(correlation.py)으로 산출해 전달한다.
          cross_host_correlation_enabled + correlated → SUPPRESS(사유 "크로스-호스트 상관 —
          클러스터 대표 외 억제"). storm(동일 서버)과 사유·경로를 구분한다(감사 오도 방지).
        - step7(스톰) 다음·step8(매트릭스) 이전에 위치하므로 심각도3은 step3에서 이미 단락되어
          도달하지 않는다(군집돼도 각각 PAGE, 불변). 이 모듈은 correlation.py를 import하지 않는다.

    E2 추가(모두 플래그 뒤 — 기본값 False면 E1 동작 무변경):
        - dependency_suppression + parent_avail_status≠0(비정상) → SUPPRESS(연쇄 노이즈, §3.6).
          parent_avail_status가 None(미수집·매핑없음·stale)이면 억제 안 함(보수적, R-3).
        - inhibition_enabled + inhibited(인자) → SUPPRESS(상위 심각도 음소거, §3.4).
        - flapping_enabled + flapping(인자) + effective_severity<=suppress_max_severity
          → SUPPRESS(상태 진동 안정화까지 보류, §3.7). 플래핑 판정은 워커가 도메인
          순수함수(flapping.py)로 산출하여 인자로 전달한다(이 모듈은 flapping을 import하지 않음).
        - storm_grouping_enabled + storm(인자) → SUPPRESS(동일 서버 다발, 대표 1건만 통보, §3.8).
        - 모든 단계가 maintenance SUPPRESS 다음·매트릭스 이전에 위치하며, 심각도3은 위에서
          이미 단락되어 이 단계에 도달하지 않는다(불변).

    E4 추가(step 9 보조 조정 — enable_llm_actionability 뒤, 기본 False면 무변경):
        - analysis.llm_actionability("actionable"|"noise"|None)를 보조 신호로 소비한다.
          "actionable" → promote(승격은 항상 안전·재현율 우선), "noise" → demote(단
          effective_severity<=suppress_max_severity 가드, is_routine demote와 동일). 승격우선
          기계가 promote 공존 시 noise demote를 무시한다. 1단계 이내 이동·SUPPRESS 하한은 불변.
        - signals["llm_actionability"]에 소비값을 스냅샷한다(enable off면 None).

    E7 추가(Plan 60 §17 — 전부 옵트인·기본 off면 비트동일):
        - (E7-b, step0.5) non_alarm_filter_enabled + is_operational_alarm(event)=False →
          SUPPRESS("비운영 알람"). 심각도3 단락(step3)보다 앞이다(§17.4 — 비알람은 severity
          신뢰 불가). is_operational_alarm은 domain 순수함수(마커 기반·애매하면 알람).
        - (E7-a·B-9, step7.7) annotation_planned_suppress + annotation.planned_work AND
          (annotation.resolution OR correlated OR noise_ctx.change_nearby) → DASHBOARD 강등
          (SUPPRESS 아님·코로보레이션 게이팅). 주석 단독은 강등 없이 통상 매트릭스로 진행.
          annotation(dict|None)은 워커가 extract_annotation_signal로 산출해 주입한다 — 이 모듈은
          annotation_signal을 import하지 않고 값만 소비한다(정책 순수성·§17.7). signals 동결
          스키마는 확장하지 않는다(change_nearby 전례 — 감사는 reason·record_recurrence가 담당).
    """
    suppress_max_severity = int(getattr(config, "suppress_max_severity", 2))
    importance_value_map = getattr(config, "importance_value_map", {}) or {}
    resolved_to_dashboard = bool(getattr(config, "resolved_to_dashboard", False))
    dependency_suppression = bool(getattr(config, "dependency_suppression", False))
    inhibition_enabled = bool(getattr(config, "inhibition_enabled", False))
    flapping_enabled = bool(getattr(config, "flapping_enabled", False))
    storm_grouping_enabled = bool(getattr(config, "storm_grouping_enabled", False))
    cross_host_correlation_enabled = bool(
        getattr(config, "cross_host_correlation_enabled", False)
    )
    # (Plan 60 E7) 옵트인 — 기본 off면 아래 신규 단계를 평가하지 않아 비트동일(회귀 0).
    non_alarm_filter_enabled = bool(getattr(config, "non_alarm_filter_enabled", False))
    annotation_planned_suppress = bool(
        getattr(config, "annotation_planned_suppress", False)
    )

    # ── step 1: 실효 심각도 (E3 — AI 메시지 심각도 상향 전용 보강) ──
    # 실효심각도 = max(폴스타 severity, AI 상향등급). max()가 하향 불가를 보장한다(상향 전용·R-10).
    # 보강 비활성(enable_ai_severity_boost=False)이면 AI 값을 무시한다(이중 안전·E2 회귀 0).
    # step3의 심각도3 단락은 effective_severity 기준이므로 AI가 2→3 상향 시에도 올바르게 PAGE 단락.
    severity = int(getattr(event, "severity", 0) or 0)
    ai_severity = getattr(analysis, "ai_message_severity", None)
    if not getattr(config, "enable_ai_severity_boost", False):
        ai_severity = None
    effective_severity = max(severity, ai_severity) if ai_severity is not None else severity

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

    # LLM 액션가능성(피드백 few-shot·E4) — 보조 신호일 뿐 판단은 결정적 규칙(step 9)이 내린다.
    # enable off(기본)면 값을 읽지 않고 None으로 무시한다(E3 회귀 0). 심각도3은 step3에서 이미
    # 단락되어 이 신호와 무관하다. _signals()(sev3 포함 첫 _decision 경로)가 참조하므로
    # 여기서(첫 _decision 이전) 확정해야 클로저 참조 시 NameError가 없다.
    enable_actionability = bool(getattr(config, "enable_llm_actionability", False))
    llm_actionability = (
        getattr(analysis, "llm_actionability", None) if analysis is not None else None
    )
    if not enable_actionability:
        llm_actionability = None

    def _signals() -> dict:
        """§8.2 동결 스키마(모든 키 필수)로 신호 스냅샷을 구성한다."""
        return {
            "severity": severity,
            "ai_severity": ai_severity,
            "effective_severity": effective_severity,
            "importance": importance,
            "maintenance": maintenance,
            # 수집 실패(noise_ctx None)·미수집(E1, collect_dependency=False)이면 None.
            # E1(dependency_suppression=False)은 컬렉터가 항상 None을 반환하므로 무변경(회귀 0).
            "parent_avail_status": (
                noise_ctx.get("parent_avail_status") if noise_ctx else None
            ),
            "pattern": pattern,
            "is_routine": is_routine,
            "noti_policy": noti_policy,
            "flapping": bool(flapping),
            "self_heal": bool(self_heal),
            "storm": bool(storm),
            # E4: LLM 액션가능성 자문(actionable|noise|None). enable off면 None(무시).
            "llm_actionability": llm_actionability,
            # Plan 60 E4: 다홉 연쇄 신호(미산출·1홉 모드면 None).
            "cascaded": (noise_ctx.get("cascaded") if noise_ctx else None),
            "root_resource": (noise_ctx.get("root_resource") if noise_ctx else None),
            # Plan 60 E2: 크로스-호스트 상관(워커 산출·step7.5 소비). off면 항상 False.
            "correlated": bool(correlated),
        }

    def _decision(tier: str, reason: str) -> NotificationDecision:
        return NotificationDecision(
            tier=tier,
            reason=reason,
            priority=_priority(tier, effective_severity, importance),
            signals=_signals(),
            fingerprint=compute_fingerprint(event),
        )

    # ── step 0.5(E7-b): 비알람 사전분류 — 승인/안내성 메시지 억제(§17.4) ──
    # non_alarm_filter_enabled=False(기본)면 평가하지 않아 심각도3 단락(step3) 포함 비트동일(회귀 0).
    # 활성 시 심각도3 단락보다 앞이지만 — 비알람은 severity 신뢰 불가하므로 마커 판정을 우선한다
    # (§17.4). is_operational_alarm은 알람 마커 부재+비알람 마커 존재일 때만 False(애매하면 알람).
    if non_alarm_filter_enabled and not is_operational_alarm(event):
        return _decision(
            TIER_SUPPRESS, "비운영 알람 — 승인/안내성 메시지(마커 기반 사전 억제)"
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

    # ── step 6.4(E2·E4): 의존성 억제 — 조상 비정상 시 자식 연쇄 노이즈(§3.6·§6.2) ──
    # dependency_suppression=False(기본)면 단계 자체를 평가하지 않아 E1 무변경.
    if dependency_suppression:
        # E4 다홉 하이브리드(§6.2): cascaded(다홉 조상 비정상)면 root 통보 여부로 억제 강도 분기.
        # cascaded 미제공(1홉 모드·수집 실패)이면 아래 현행 parent_avail_status 판정으로 폴백.
        if noise_ctx and noise_ctx.get("cascaded"):
            if noise_ctx.get("root_notified"):
                return _decision(
                    TIER_SUPPRESS, "의존성 억제(다홉) — 근본원인 노드 통보됨"
                )
            return _decision(
                TIER_DASHBOARD,
                "의존성 연쇄(다홉) — 근본원인 미통보, 대시보드 강등",
            )
        # 1홉 폴백(현행 무변경): parent_avail_status 0=정상, ≠0=비정상, None=미수집(보수적 비억제·R-3).
        parent_avail_status = noise_ctx.get("parent_avail_status") if noise_ctx else None
        if parent_avail_status is not None and parent_avail_status != 0:
            return _decision(
                TIER_SUPPRESS,
                f"의존성 억제 — 부모 리소스 비정상(AVAIL_STATUS={parent_avail_status}),"
                " 자식 연쇄 노이즈",
            )

    # ── step 6.5(E2): 인히비션 — 동일 서버 상위 심각도 발생 중 하위 음소거(§3.4) ──
    # inhibited는 워커가 결정적으로 탐지(자기억제 금지·window)하여 인자로 전달.
    # inhibition_enabled=False(기본)면 평가하지 않아 E1 무변경.
    if inhibition_enabled and inhibited:
        return _decision(
            TIER_SUPPRESS, "인히비션 — 동일 서버 상위 심각도 발생 중, 하위 음소거"
        )

    # ── step 6(§6 파이프라인·E2): 플래핑 — 상태 진동 시 안정화까지 보류(§3.7) ──
    # flapping은 워커가 도메인 순수함수(flapping.py)로 산출하여 인자로 전달.
    # 저심각도(effective_severity<=suppress_max_severity)만 억제 — 심각도3은 위에서 단락(불변).
    # flapping_enabled=False(기본)면 평가하지 않아 E1 무변경.
    if flapping_enabled and flapping and effective_severity <= suppress_max_severity:
        return _decision(
            TIER_SUPPRESS, "플래핑 — 상태 진동(Nagios), 안정화까지 통보 보류"
        )

    # ── step 7(§6 파이프라인·E2): 스톰 — 동일 서버 다발 시 대표만 통보(§3.8) ──
    # storm은 워커가 사건창(동일 db_id|server) 카운트로 산출하여 인자로 전달.
    # storm_grouping_enabled=False(기본)면 평가하지 않아 E1 무변경.
    if storm_grouping_enabled and storm:
        return _decision(
            TIER_SUPPRESS, "스톰 — 동일 서버 다발, 대표 1건만 통보"
        )

    # ── step 7.5(E2): 크로스-호스트 상관 — 클러스터 대표 외 억제(§4.2) ──
    # correlated는 워커가 온라인 그리디 군집(correlation.py)으로 산출하여 인자로 전달.
    # cross_host_correlation_enabled=False(기본)면 평가하지 않아 회귀 0. 심각도3은 step3에서
    # 이미 단락되어 이 단계에 도달하지 않는다(군집돼도 각각 PAGE, 불변). storm(동일 서버 다발)과
    # 사유를 구분한다 — storm 경로 재사용 시 "동일 서버 다발" 사유가 감사를 오도하기 때문.
    if cross_host_correlation_enabled and correlated:
        return _decision(
            TIER_SUPPRESS, "크로스-호스트 상관 — 클러스터 대표 외 억제"
        )

    # ── step 7.7(E7-a·B-9): 계획-무해 주석 코로보레이션 게이팅 DASHBOARD 강등(§17.3) ──
    # 텍스트 단독으로는 절대 억제강화 금지 — planned_work **AND** (resolution 또는 E2 클러스터
    # 소속(correlated) 또는 E5 change_nearby)가 동시 충족될 때만 DASHBOARD 강등(SUPPRESS 아님·
    # E4 하이브리드 정합). 주석 단독(코로보레이션 없음)이면 강등하지 않고 통상 매트릭스로 진행(첨부만).
    # annotation_planned_suppress=False(기본)면 평가하지 않아 비트동일(회귀 0). 심각도3은 step3에서
    # 이미 단락되어 이 단계에 도달하지 않는다(불변). SUPPRESS는 위 단계가 우선(더 강한 억제 보존).
    if annotation_planned_suppress and annotation and annotation.get("planned_work"):
        corroborated = (
            bool(annotation.get("resolution"))
            or bool(correlated)
            or bool(noise_ctx and noise_ctx.get("change_nearby"))
        )
        if corroborated:
            return _decision(
                TIER_DASHBOARD,
                "계획-무해 주석(코로보레이션) — 대시보드 강등(계획작업+해소/상관/변경근접)",
            )

    # ── step 8: 우선순위 매트릭스(§3.2) ─────────────────────
    base_tier = _matrix_tier(effective_severity, importance)

    # ── step 9: 보조 조정(1단계 이내·보수적, 승격 우선) ──────
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
    # E4: LLM 액션가능성(피드백 few-shot) — 승격 비대칭(재현율 우선).
    # actionable → promote(항상 안전). noise → demote(is_routine과 동일하게 SUPPRESS 하한 가드).
    # 아래 승격우선 기계가 promote 신호와 공존 시 noise demote를 무시한다.
    if llm_actionability == "actionable":
        promote.append("LLM 액션가능성(피드백)")
    if llm_actionability == "noise" and effective_severity <= suppress_max_severity:
        demote.append("LLM 노이즈 판단(피드백)")
    # Plan 60 E5(§7.2): 변경 근접 알람은 **억제가 아니라 승격** — 원인성 판단·PAGE 근거 보강
    # (재현율 우선). change_correlation off·미근접이면 change_nearby 키 부재/None → 무영향(회귀 0).
    # 이 모듈은 change 모듈을 import하지 않고 noise_ctx의 bool만 소비한다(순수성).
    if noise_ctx and noise_ctx.get("change_nearby"):
        promote.append("변경 근접(원인성)")

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
