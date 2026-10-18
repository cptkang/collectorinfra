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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timezone
from typing import Any

from noise_gate.domain.cross_source import ACTION_DEMOTE, MODE_ENFORCE
from noise_gate.domain.silence import match_rules

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

# (plans/144 §4.1) 제니퍼 유형 정책 공급자가 noise_ctx["source"]에 싣는 값. 이 출처의
# `importance_id`는 코드가 아니라 **라벨**(높음|보통|낮음)이다 — importance_value_map을
# 거치지 않는다.
NOISE_SOURCE_APM_POLICY = "apm_policy"

# (plans/144 §4.2) 지속 조건 미달 강등 사유 — 이 사유만으로 강등할 때 하한은 DASHBOARD다(G-1 (b)).
_PERSISTENCE_UNMET = "지속 조건 미달(제니퍼 지표형)"

# ── 결정 단계 라벨 (Plan 54 모듈 1 — 퍼널의 근거 데이터) ─────────────────
# "어느 단계가 이 알람을 잘랐는가"를 레코드에 남기기 위한 **닫힌 집합**이다.
# reason 문자열 역추정 대신 이 라벨을 쓰는 이유: 사유 문구를 고치면 퍼널이 조용히 깨진다.
# 라벨은 티어·사유·우선순위 산출에 **일절 관여하지 않는다**(관측 전용 — 판정 회귀 0).
STAGE_NON_ALARM = "non_alarm"                  # step 0.5 비운영(승인/안내성) 사전분류
STAGE_SEVERITY3 = "severity3"                  # step 3   심각도3 단락(억제 불가)
STAGE_SELF_HEAL = "self_heal"                  # step 4   자가복구 상관
STAGE_RESOLVED = "resolved"                    # step 4   독립 해소
STAGE_COLLECTION_FAILED = "collection_failed"  # step 5   신호 수집 실패 보수화
STAGE_MAINTENANCE = "maintenance"              # step 6   유지보수 모드
STAGE_SILENCE = "silence"                      # step 6.2 운영자 침묵 규칙(Plan 54 모듈 4)
STAGE_DEPENDENCY = "dependency"                # step 6.4 의존성 연쇄 억제
STAGE_INHIBITION = "inhibition"                # step 6.5 인히비션
STAGE_FLAPPING = "flapping"                    # step 6   플래핑
STAGE_STORM = "storm"                          # step 7   스톰 그룹핑
STAGE_CORRELATION = "correlation"              # step 7.5 크로스-호스트 상관
STAGE_CROSS_SOURCE = "cross_source"            # step 7.6 크로스소스 사건 상관(plans/144 W3)
STAGE_ANNOTATION = "annotation"                # step 7.7 계획-무해 주석 강등
STAGE_MATRIX = "matrix"                        # step 8~9 매트릭스 + 보조 조정(최종 관문)
# (plans/162 · D-330) 마지막 관문 — PAGE만 결정적 규칙으로 다시 보고 사건 반복이면
# TICKET으로 내린다. decide_notification 밖(게이트 노드 · page_review.review_page)에서 붙는다.
STAGE_PAGE_REVIEW = "page_review"

# 파이프라인 **선언 순서** — 퍼널의 단계 배열이 이 튜플 하나에서 온다.
# 집계 계층이 순서를 따로 알면 어긋나므로, 순서의 정본은 판정 코드와 같은 파일에 둔다.
STAGE_ORDER: tuple[str, ...] = (
    STAGE_NON_ALARM,
    STAGE_SEVERITY3,
    STAGE_SELF_HEAL,
    STAGE_RESOLVED,
    STAGE_COLLECTION_FAILED,
    STAGE_MAINTENANCE,
    STAGE_SILENCE,
    STAGE_DEPENDENCY,
    STAGE_INHIBITION,
    STAGE_FLAPPING,
    STAGE_STORM,
    STAGE_CORRELATION,
    STAGE_CROSS_SOURCE,
    STAGE_ANNOTATION,
    STAGE_MATRIX,
    STAGE_PAGE_REVIEW,
)

# 단계 표시명(관제 화면 라벨) — UI가 영문 키를 그대로 노출하지 않게 한다.
STAGE_LABELS: dict[str, str] = {
    STAGE_NON_ALARM: "비운영 알람",
    STAGE_SEVERITY3: "심각도3 단락",
    STAGE_SELF_HEAL: "자가복구 상관",
    STAGE_RESOLVED: "독립 해소",
    STAGE_COLLECTION_FAILED: "수집 실패 보수화",
    STAGE_MAINTENANCE: "유지보수",
    STAGE_SILENCE: "침묵 규칙",
    STAGE_DEPENDENCY: "의존성 연쇄",
    STAGE_INHIBITION: "인히비션",
    STAGE_FLAPPING: "플래핑",
    STAGE_STORM: "스톰",
    STAGE_CORRELATION: "크로스-호스트 상관",
    STAGE_CROSS_SOURCE: "크로스소스 사건 상관",
    STAGE_ANNOTATION: "계획-무해 주석",
    STAGE_MATRIX: "우선순위 매트릭스",
    STAGE_PAGE_REVIEW: "PAGE 재판정",
}


# 사유 접두 → 단계 폴백 매핑 (Plan 54 모듈 2).
# `stage` 필드 도입 **이전**에 쌓인 감사 레코드도 퍼널에서 탈락하지 않게 한다.
# 사유 문자열의 정본이 이 파일이므로 매핑도 여기 둔다 — 문구를 고치면 이 표가 같이 보인다.
_REASON_STAGE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("비운영 알람", STAGE_NON_ALARM),
    ("심각도3", STAGE_SEVERITY3),
    ("자가복구 상관", STAGE_SELF_HEAL),
    ("독립 해소", STAGE_RESOLVED),
    ("신호 수집 실패", STAGE_COLLECTION_FAILED),
    ("유지보수 모드", STAGE_MAINTENANCE),
    ("침묵 규칙", STAGE_SILENCE),
    ("의존성 억제", STAGE_DEPENDENCY),
    ("의존성 연쇄", STAGE_DEPENDENCY),
    ("인히비션", STAGE_INHIBITION),
    ("플래핑", STAGE_FLAPPING),
    ("스톰", STAGE_STORM),
    ("크로스-호스트 상관", STAGE_CORRELATION),
    ("크로스소스 사건 상관", STAGE_CROSS_SOURCE),
    ("계획-무해 주석", STAGE_ANNOTATION),
    ("매트릭스", STAGE_MATRIX),
    ("PAGE 재판정", STAGE_PAGE_REVIEW),
)

# 매핑 실패 레코드가 모이는 자리 — **버리지 않는다**(합계 항등식이 깨지면 퍼널이 거짓말을 한다).
STAGE_UNKNOWN = "unknown"

# 단계 설명(관제 화면 (!) 팝오버 · plans/112 S4). 판정 순서·라벨과 같은 파일에 두어, 판정을
# 고치는 사람이 설명을 함께 보게 한다. **판정에 관여하지 않는다**(관측 전용). 문구는 아래
# RULES 표의 규칙(_rule_*)·_matrix_decision의 결정 지점과 대조해 썼다 — 판정을 바꾸면
# 이 표도 같이 고친다.
STAGE_DESCRIPTIONS: dict[str, str] = {
    STAGE_NON_ALARM: (
        "알람 표지(가용성·사용률·임계·초과·장애·경고 등)가 하나도 없고 승인·요청·안내·공지·"
        "문의성 문구가 있는 메시지를 억제합니다. 애매하면 알람으로 봅니다(재현율 우선). "
        "심각도3 단락보다 앞에서 평가합니다."
    ),
    STAGE_SEVERITY3: (
        "실효 심각도(수신 심각도와, 켜져 있을 때 AI 상향 등급 중 큰 값)가 3이면 뒤 단계를 "
        "거치지 않고 PAGE입니다. 침묵 규칙·억제 단계 어느 것으로도 억제되지 않습니다"
        "(앞 단계인 비운영 알람 판정에 걸린 메시지만 예외). 해소 알람에는 AI 상향을 적용하지 "
        "않습니다. 제니퍼 정책 심각도3 설정을 켜면 제니퍼 유형 정책 표를 쓰는 알람은 통보(page) "
        "유형만 PAGE이고 그 밖 유형은 TICKET입니다(이 단계에서 끝납니다)."
    ),
    STAGE_SELF_HEAL: (
        "해소 이벤트가 자가복구 창 안의 같은 알람 발생(억제 상한 이하 심각도)과 짝지어지면 "
        "스스로 복구된 것으로 보고 억제합니다."
    ),
    STAGE_RESOLVED: (
        "짝지을 발생이 없는 해소 이벤트입니다. 기본은 기록만(SUPPRESS), 설정하면 화면 "
        "표시(DASHBOARD)입니다."
    ),
    STAGE_COLLECTION_FAILED: (
        "중요도·유지보수 등 부가 신호를 가져오지 못하면, 심각도 1 이상은 보수적으로 "
        "PAGE입니다(재현율 우선). 「수집 실패 시 심각도3만 PAGE」 설정을 켜면 이 단계는 "
        "PAGE를 내지 않습니다 — 심각도 3은 심각도3 단락에서 PAGE이고, 그 밖은 중요도 "
        "보통으로 다음 단계를 탑니다."
    ),
    STAGE_MAINTENANCE: (
        "수집원이 유지보수 모드로 알려준 자원의 알람은 신규 발송을 억제합니다"
        "(감사 기록은 남습니다)."
    ),
    STAGE_SILENCE: (
        "운영자가 건 침묵 규칙(존·서버·알람·자원 글롭, 심각도 상한, 만료 필수)에 걸리면 "
        "억제합니다. 규칙의 심각도 상한은 실효 심각도와 대조합니다."
    ),
    STAGE_DEPENDENCY: (
        "상위 자원이 비정상이면 하위 알람을 연쇄 노이즈로 봅니다. 다홉 모드에서는 근본원인이 "
        "이미 통보됐으면 억제, 아니면 화면 표시로 낮춥니다. 상위 상태를 모르면 억제하지 않습니다."
    ),
    STAGE_INHIBITION: (
        "같은 서버에서 더 높은 심각도의 다른 알람이 창 안에 활성이면 하위 알람을 누릅니다."
    ),
    STAGE_FLAPPING: (
        "발생·해소가 진동(가중 상태 변화율이 상한 초과)하면 안정될 때까지 통보를 보류합니다. "
        "억제 상한 이하 심각도만 해당합니다."
    ),
    STAGE_STORM: (
        "같은 서버에서 창 안 발생이 임계를 넘으면, 임계 건수까지는 통상대로 판단하고 그 뒤 "
        "건은 억제합니다."
    ),
    STAGE_CORRELATION: (
        "같은 존의 여러 호스트 알람을 유사도로 묶어, 먼저 온 대표 외 알람을 억제합니다"
        "(같은 서버 다발인 스톰과 구분)."
    ),
    STAGE_CROSS_SOURCE: (
        "같은 존·같은 호스트의 인프라 알람(원인)과 APM 알람(증상)을 한 사건으로 묶습니다. "
        "규칙 표의 방향(원인 → 증상)과 시간 창이 맞고, 양쪽 호스트 정합이 강하며, 원인이 이미 "
        "PAGE·TICKET으로 통보돼 해소되지 않았고, 증상 심각도가 3 미만일 때만 증상을 화면 "
        "표시(DASHBOARD)로 낮춥니다. 실제로 낮추는 것은 강제 모드에서 규칙 단위로 켠 행뿐이며, "
        "티어를 올리지 않고 앞 단계 억제가 우선합니다. 그 밖에는 사건 묶음과 「했을 조치」만 "
        "근거로 남깁니다."
    ),
    STAGE_ANNOTATION: (
        "계획 작업 주석이 있고 해소·상관·변경 근접 중 하나가 함께 뒷받침하면 화면 표시로 "
        "낮춥니다. 주석만으로는 낮추지 않습니다."
    ),
    STAGE_MATRIX: (
        "앞 단계를 모두 지난 알람의 최종 관문입니다. 심각도×중요도 표로 기본 티어를 정하고, "
        "보조 신호(통보 정책·일상 패턴·LLM 판단·변경 근접)로 최대 한 단계 올리거나 내립니다. "
        "둘이 충돌하면 올립니다. 앱 영향 승격이 켜져 있으면 같은 호스트에 사건창 안 APM fatal "
        "이벤트가 있을 때 DASHBOARD·TICKET을 PAGE로 올립니다(SUPPRESS는 그대로). 제니퍼 유형 "
        "정책이 켜져 있으면 지표형 경고(심각도 2)가 창 안에 반복되지 않았을 때 한 단계 내립니다"
        "(이 사유만으로는 DASHBOARD 아래로 내리지 않습니다)."
    ),
    STAGE_PAGE_REVIEW: (
        "앞 단계에서 PAGE로 정해진 알람만 다시 봅니다. 이미 통보된 사건의 반복이면 TICKET으로 "
        "내립니다 — 같은 알람의 열린 사건 반복(해소 뒤 짧은 유예 안의 재발 포함), 같은 서버에서 "
        "먼저 통보된 사건이 진행 중이고 이번 심각도가 그보다 높지 않을 때, 같은 존·같은 알람이 "
        "여러 서버에서 동시에 울릴 때의 대표 외 서버입니다. 새 사건의 첫 PAGE · 심각도 상승 · "
        "앱 영향(APM fatal) · 오래 해소되지 않은 사건의 리마인더 · 운영자가 지정한 알람은 그대로 "
        "PAGE입니다. 억제가 아니라 강등이며 기록은 남습니다. 끈 상태(기본)에서는 평가하지 않고, "
        "기록 모드에서는 판정을 바꾸지 않고 「내렸을 것」만 남깁니다."
    ),
    STAGE_UNKNOWN: (
        "단계 라벨이 없고 사유 문구로도 판별되지 않은 옛 레코드입니다. 버리지 않고 모아 "
        "둡니다(합계가 맞도록)."
    ),
}


def stage_from_reason(reason: str) -> str:
    """사유 문자열에서 결정 단계를 역추정한다(구 레코드 폴백 전용).

    `stage` 필드가 있는 레코드에는 쓰지 않는다. 어느 접두에도 걸리지 않으면
    `STAGE_UNKNOWN`을 돌려준다 — 집계에서 제외하지 않기 위해서다.

    Args:
        reason: 결정 사유 문자열.

    Returns:
        단계 라벨 또는 `STAGE_UNKNOWN`.
    """
    text = (reason or "").strip()
    for prefix, stage in _REASON_STAGE_PREFIXES:
        if text.startswith(prefix):
            return stage
    return STAGE_UNKNOWN


@dataclass
class NotificationDecision:
    """발송 판단 결과(감사·설명가능성용 스냅샷 포함)."""

    tier: str          # page | ticket | dashboard | suppress
    reason: str        # 결정 근거(한국어) — 어느 단계에서 무슨 신호로 판단했는지
    priority: int      # 산출 우선순위(높을수록 시급)
    signals: dict      # 사용된 신호 스냅샷(§8.2 키 스키마)
    fingerprint: str = ""
    # (Plan 54 모듈 1) 결정이 난 단계 — 관측 전용. 맨 뒤 기본값이라 기존 위치 인자 호출은 무영향.
    stage: str = ""
    # (plans/112 S6) 결정 단계에서 도메인이 아는 구체 근거(비알람 마커·침묵 규칙·의존성 모드·
    # 주석 코로보레이션·매트릭스 조정) — 감사 전용. 위 판정 필드 산출에 관여하지 않는다.
    evidence: dict[str, Any] = field(default_factory=dict)


def compute_fingerprint(event: Any, *, alarm_key: str | None = None) -> str:
    """재발생 dedup용 안정 식별자를 산출한다.

    `f(db_id, server_name|hostname, alarm_name, resource_name)` 조합의 SHA-1 해시.
    server_name을 우선 사용하고, 없으면 hostname으로 대체한다(§6.1).

    alarm_key(plans/144 §4.3)가 주어지면 alarm_name 대신 쓴다 — 제니퍼 정책 경로가 정규화 유형을
    넘겨 `ERROR_X` 발생과 `X` 회복이 같은 지문이 되게 한다. 미지정(기본)이면 현행과 비트 동일.
    """
    db_id = str(getattr(event, "db_id", "") or "")
    server = str(getattr(event, "server_name", "") or "") or str(
        getattr(event, "hostname", "") or ""
    )
    alarm_name = (
        alarm_key if alarm_key is not None else str(getattr(event, "alarm_name", "") or "")
    )
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


def non_alarm_markers(event: object) -> list[str]:
    """이벤트 텍스트에 걸린 **비알람 마커 단어**를 등장 순서대로(중복 제거) 돌려준다 (plans/112 S6).

    `is_operational_alarm`과 **같은 정규식·같은 텍스트 조합**을 쓴다 — 비운영 판정의 근거를
    감사에 남기기 위한 것이며 판정 자체는 `is_operational_alarm`이 한다(이 함수는 판정 무관).

    Args:
        event: 알람 이벤트(alarm_name/condition_log/conditions/resource_name/resource_type 속성).

    Returns:
        걸린 비알람 마커 단어 목록(없으면 빈 목록).
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
    return list(dict.fromkeys(_NON_ALARM_MARKERS.findall(text)))


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


def _silence_evidence(rule: object) -> dict[str, str]:
    """걸린 침묵 규칙의 판단 시점 스냅샷을 만든다 (plans/112 S6 · 감사 전용).

    규칙이 나중에 해제·만료돼도 "무엇이 조용히 시켰나"가 레코드에 남게 한다. 근거 추출이
    판정을 깨면 안 되므로 속성은 전부 안전 접근한다(덕 타이핑 규칙도 예외 없이 통과).
    """
    expires_at = getattr(rule, "expires_at", None)
    return {
        "rule_id": str(getattr(rule, "id", "") or ""),
        "matcher_summary": str(getattr(rule, "matcher_summary", "") or ""),
        "expires_at": (
            expires_at.isoformat() if isinstance(expires_at, datetime) else str(expires_at or "")
        ),
        "created_by": str(getattr(rule, "created_by", "") or ""),
    }


def _priority(tier: str, effective_severity: int, importance: str) -> int:
    """티어·실효심각도·중요도로 정수 우선순위를 산출한다(높을수록 시급)."""
    return (
        _TIER_RANK[tier] * 100
        + max(effective_severity, 0) * 10
        + _IMPORTANCE_WEIGHT.get(importance, 2)
    )


# (plans/162 R4 · D-330 ③) 수집 실패 근거 키 — 감사·메타경보(`collection_failed_ratio`)·
# W0 스크립트가 센다. EVIDENCE_COLLECTION_FAILED는 플래그 on일 때 「off였으면 step 5 보수 PAGE가
# 됐을 판정」(step 5 통과 뒤 판정)에만 True로 싣고 — off의 stage==collection_failed와 같은
# 모집단이다 —
# EVIDENCE_COLLECTION_FAILED_SHADOW는 플래그 off + page_review_mode=shadow일 때 step 5 PAGE에
# 「플래그 on이었으면 나왔을 티어」를 싣는다(판정 불변).
EVIDENCE_COLLECTION_FAILED = "collection_failed"
EVIDENCE_COLLECTION_FAILED_SHADOW = "collection_failed_sev3_only"
# (plans/162 W5 · D-336) 제니퍼 정책 경로 심각도3 근거 키 — 플래그 on일 때 step 3이 유형 정책 표로
# 판정한 건에만 {"notify": "page"|"ticket", "policy_row": 적용 행 키}를 싣는다(감사·W0 스크립트용).
EVIDENCE_APM_SEV3_POLICY = "apm_sev3_policy"
_PAGE_REVIEW_SHADOW = "shadow"  # page_review.MODE_SHADOW와 같은 값(page_review가 이 모듈을 import)


class _ConfigOverride:
    """config 속성 일부만 덮어쓴 읽기 전용 보기(shadow 「했을 판정」 재계산용 · 순수).

    나머지 속성은 원 config에서 읽는다 — `getattr(보기, 이름, 기본값)` 안전 접근이 그대로 통한다.
    """

    def __init__(self, base: object, **overrides: Any) -> None:
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        overrides = self.__dict__.get("_overrides", {})
        if name in overrides:
            return overrides[name]
        return getattr(self.__dict__.get("_base"), name)


@dataclass(frozen=True)
class GateInputs:
    """`decide_notification` 한 번의 판정 입력 묶음(원 인자 + 읽은 플래그 + 사전 계산 파생값).

    공개 시그니처는 그대로 두고 함수 안에서 `_gate_inputs`로 조립한다. 사전 계산은 어느 경로든
    항상 계산되던 값(step 1 실효 심각도 · step 2 수집 실패 보수화 · 보조 입력)까지만이다 —
    조건부로만 부르던 판정 함수(비알람 마커·침묵 매칭·매트릭스·앱 영향·지문)는 해당 규칙 안에서
    부른다.
    """

    # ── 원 인자(step 5 shadow 재귀가 그대로 다시 넘긴다) ──
    event: Any
    history_stats: Any
    analysis: Any
    noise_ctx: Any
    config: Any
    self_heal: bool
    inhibited: bool
    flapping: bool
    storm: bool
    correlated: bool
    annotation: dict[str, Any] | None
    silence_rules: Any
    now: datetime | None
    apm_policy: dict[str, Any] | None
    cross_source: dict[str, Any] | None
    # ── config 플래그(getattr 안전 접근 — 기본값이면 현행 동작) ──
    suppress_max_severity: int
    resolved_to_dashboard: bool
    dependency_suppression: bool
    inhibition_enabled: bool
    flapping_enabled: bool
    storm_grouping_enabled: bool
    cross_host_correlation_enabled: bool
    non_alarm_filter_enabled: bool
    annotation_planned_suppress: bool
    collection_failed_sev3_only: bool
    apm_sev3_policy_enabled: bool
    # ── step 1·2 · 보조 입력 ──
    severity: int
    ai_severity: Any
    effective_severity: int
    collection_failed: bool
    importance: str
    maintenance: bool
    noti_policy: Any
    pattern: str
    is_routine: Any
    llm_actionability: Any
    apm_alarm_key: str | None
    cross_source_cap: bool
    # (plans/162 R4) 수집 실패 표지 — 규칙 표 「표지」 열이 True인 행·매트릭스가 받는 보기에서만
    # `collection_failed and effective_severity >= 1`이다(그 밖은 False).
    mark_collection_failed: bool = False


def _effective_severity(event: Any, analysis: Any, config: Any) -> tuple[int, Any, Any]:
    """step 1 — (수신 심각도, AI 상향 등급, 실효 심각도)를 돌려준다.

    실효심각도 = max(수신 severity, AI 상향등급). max()가 하향 불가를 보장한다(상향 전용·R-10).
    보강 비활성(enable_ai_severity_boost=False)이면 AI 값을 무시한다(이중 안전·E2 회귀 0).
    step3의 심각도3 단락은 effective_severity 기준이므로 AI가 2→3 상향 시에도 올바르게 PAGE 단락.
    해소(is_clear) 알람에는 상향을 적용하지 않는다 — 조건 로그에 3급 시그니처가 남은 해소 알림이
    심각도3 단락(PAGE)에 걸리면 안 되고 step4(해소·자가복구)로 가야 한다. 상향 원천(시그니처 스캔·
    LLM 해석·동적 baseline·agentic 보강)이 모두 이 슬롯 하나로 들어오므로 여기서 한 번에 막는다.
    """
    severity = int(getattr(event, "severity", 0) or 0)
    ai_severity = getattr(analysis, "ai_message_severity", None)
    if not getattr(config, "enable_ai_severity_boost", False) or bool(
        getattr(event, "is_clear", False)
    ):
        ai_severity = None
    effective_severity = max(severity, ai_severity) if ai_severity is not None else severity
    return severity, ai_severity, effective_severity


def _context_signals(
    noise_ctx: Any, importance_value_map: dict[str, str]
) -> tuple[bool, str, bool, Any]:
    """step 2 — (수집 실패, 중요도, 유지보수, 통보 정책)을 돌려준다(수집 실패 시 보수화)."""
    collection_failed = noise_ctx is None or noise_ctx.get("source") == "unavailable"
    if collection_failed:
        return collection_failed, "보통", False, None
    if noise_ctx.get("source") == NOISE_SOURCE_APM_POLICY:
        # (plans/144 §4.1) 제니퍼 유형 정책 — importance_id가 이미 라벨이다(미지 값은 보통 보수).
        label = noise_ctx.get("importance_id")
        importance = label if label in _VALID_IMPORTANCE else "보통"
    else:
        importance = map_importance(noise_ctx.get("importance_id"), importance_value_map)
    return (
        collection_failed,
        importance,
        bool(noise_ctx.get("maintenance")),
        noise_ctx.get("noti_policy"),
    )


def _gate_inputs(
    event: Any,
    history_stats: Any,
    analysis: Any,
    noise_ctx: Any,
    config: Any,
    *,
    self_heal: bool,
    inhibited: bool,
    flapping: bool,
    storm: bool,
    correlated: bool,
    annotation: dict[str, Any] | None,
    silence_rules: Any,
    now: datetime | None,
    apm_policy: dict[str, Any] | None,
    cross_source: dict[str, Any] | None,
) -> GateInputs:
    """원 인자에서 플래그를 읽고 step 1·2·보조 입력을 계산해 `GateInputs`를 만든다.

    읽는 순서는 리팩터 전 `decide_notification` 본문과 같다.
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
    # (plans/162 R4 · D-330 ③) 옵트인 — 기본 off면 step 5 보수 PAGE 그대로(비트 동일).
    collection_failed_sev3_only = bool(
        getattr(config, "collection_failed_page_sev3_only", False)
    )
    # (plans/162 W5 · D-336) 옵트인 — 기본 off면 제니퍼 정책 경로 심각도3도 단락 PAGE(비트 동일).
    apm_sev3_policy_enabled = bool(getattr(config, "apm_sev3_policy_enabled", False))

    # ── step 1: 실효 심각도 (E3 — AI 메시지 심각도 상향 전용 보강) ──
    severity, ai_severity, effective_severity = _effective_severity(event, analysis, config)

    # ── step 2: 신호 수집 실패 시 보수화 ─────────────────────
    collection_failed, importance, maintenance, noti_policy = _context_signals(
        noise_ctx, importance_value_map
    )

    # LLM 보조 입력(패턴·is_routine)은 수집 실패와 무관하게 해석에 사용
    pattern = ""
    if analysis is not None:
        pattern = str(getattr(analysis, "pattern_type", "") or "")
    if not pattern and history_stats is not None:
        pattern = str(getattr(history_stats, "pre_classification", "") or "")
    is_routine = getattr(analysis, "is_routine", None) if analysis is not None else None

    # LLM 액션가능성(피드백 few-shot·E4) — 보조 신호일 뿐 판단은 결정적 규칙(step 9)이 내린다.
    # enable off(기본)면 값을 읽지 않고 None으로 무시한다(E3 회귀 0). 심각도3은 step3에서 이미
    # 단락되어 이 신호와 무관하다. _signals()가 모든 결정(sev3 포함)에서 참조하므로 미리 확정한다.
    enable_actionability = bool(getattr(config, "enable_llm_actionability", False))
    llm_actionability = (
        getattr(analysis, "llm_actionability", None) if analysis is not None else None
    )
    if not enable_actionability:
        llm_actionability = None

    # (plans/144 §4.3) 제니퍼 정책 경로면 정규화 유형으로 지문을 만든다(None = 현행 alarm_name).
    apm_alarm_key: str | None = None
    if apm_policy and apm_policy.get("event_type_norm"):
        apm_alarm_key = str(apm_policy["event_type_norm"])

    # (plans/144 W3) 크로스소스 상한 조건 — 모드·결합 판정·규칙 단위 enforce가 모두 맞을 때만.
    cross_source_cap = bool(
        cross_source
        and cross_source.get("mode") == MODE_ENFORCE
        and cross_source.get("action") == ACTION_DEMOTE
        and cross_source.get("enforce") is True
    )

    return GateInputs(
        event=event,
        history_stats=history_stats,
        analysis=analysis,
        noise_ctx=noise_ctx,
        config=config,
        self_heal=self_heal,
        inhibited=inhibited,
        flapping=flapping,
        storm=storm,
        correlated=correlated,
        annotation=annotation,
        silence_rules=silence_rules,
        now=now,
        apm_policy=apm_policy,
        cross_source=cross_source,
        suppress_max_severity=suppress_max_severity,
        resolved_to_dashboard=resolved_to_dashboard,
        dependency_suppression=dependency_suppression,
        inhibition_enabled=inhibition_enabled,
        flapping_enabled=flapping_enabled,
        storm_grouping_enabled=storm_grouping_enabled,
        cross_host_correlation_enabled=cross_host_correlation_enabled,
        non_alarm_filter_enabled=non_alarm_filter_enabled,
        annotation_planned_suppress=annotation_planned_suppress,
        collection_failed_sev3_only=collection_failed_sev3_only,
        apm_sev3_policy_enabled=apm_sev3_policy_enabled,
        severity=severity,
        ai_severity=ai_severity,
        effective_severity=effective_severity,
        collection_failed=collection_failed,
        importance=importance,
        maintenance=maintenance,
        noti_policy=noti_policy,
        pattern=pattern,
        is_routine=is_routine,
        llm_actionability=llm_actionability,
        apm_alarm_key=apm_alarm_key,
        cross_source_cap=cross_source_cap,
    )


def _signals(g: GateInputs) -> dict[str, Any]:
    """§8.2 동결 스키마(모든 키 필수)로 신호 스냅샷을 구성한다."""
    noise_ctx = g.noise_ctx
    return {
        "severity": g.severity,
        "ai_severity": g.ai_severity,
        "effective_severity": g.effective_severity,
        "importance": g.importance,
        "maintenance": g.maintenance,
        # 수집 실패(noise_ctx None)·미수집(E1, collect_dependency=False)이면 None.
        # E1(dependency_suppression=False)은 컬렉터가 항상 None을 반환하므로 무변경(회귀 0).
        "parent_avail_status": (
            noise_ctx.get("parent_avail_status") if noise_ctx else None
        ),
        "pattern": g.pattern,
        "is_routine": g.is_routine,
        "noti_policy": g.noti_policy,
        "flapping": bool(g.flapping),
        "self_heal": bool(g.self_heal),
        "storm": bool(g.storm),
        # E4: LLM 액션가능성 자문(actionable|noise|None). enable off면 None(무시).
        "llm_actionability": g.llm_actionability,
        # Plan 60 E4: 다홉 연쇄 신호(미산출·1홉 모드면 None).
        "cascaded": (noise_ctx.get("cascaded") if noise_ctx else None),
        "root_resource": (noise_ctx.get("root_resource") if noise_ctx else None),
        # Plan 60 E2: 크로스-호스트 상관(워커 산출·step7.5 소비). off면 항상 False.
        "correlated": bool(g.correlated),
    }


def _decision(
    g: GateInputs,
    tier: str,
    reason: str,
    stage: str,
    evidence: dict[str, Any] | None = None,
) -> NotificationDecision:
    """티어·사유·**결정 단계**로 판단 결과를 만든다.

    stage는 관측 전용(Plan 54 퍼널)이며 tier/reason/priority/signals 산출에 관여하지 않는다.
    evidence(plans/112 S6)도 같다 — 결정 지점이 이미 계산한 값을 옮겨 담을 뿐이다.
    evidence 키 순서: 전달 evidence → 수집 실패 표지 → apm_policy → episode_id → cross_source.
    """
    merged = dict(evidence or {})
    if g.mark_collection_failed:
        # (plans/162 R4) 수집 실패 표지 — 단계가 매트릭스 등으로 바뀌어도 셀 수 있게 싣는다.
        merged[EVIDENCE_COLLECTION_FAILED] = True
    if g.apm_policy:
        # (plans/144 §6.2) 제니퍼 정책 근거 — 결정 단계와 무관하게 싣는다(감사 전용).
        merged["apm_policy"] = dict(g.apm_policy)
    cross_source = g.cross_source
    if cross_source:
        # (plans/144 §6.2 · W3) 사건 id와 크로스소스 근거 — 결정 단계와 무관하게 싣는다.
        # applied는 이 판단에서 상한이 실제로 티어를 낮췄을 때만 True다.
        if cross_source.get("episode_id"):
            merged["episode_id"] = cross_source["episode_id"]
        merged["cross_source"] = {
            **{k: v for k, v in cross_source.items() if k != "episode_id"},
            "applied": stage == STAGE_CROSS_SOURCE,
        }
    return NotificationDecision(
        tier=tier,
        reason=reason,
        priority=_priority(tier, g.effective_severity, g.importance),
        signals=_signals(g),
        fingerprint=compute_fingerprint(g.event, alarm_key=g.apm_alarm_key),
        stage=stage,
        evidence=merged,
    )


# ── 조기 종결 규칙 — 각각 (GateInputs) -> 결정 | None(통과). 평가 순서는 아래 RULES 표가 정한다 ──


def _rule_non_alarm(g: GateInputs) -> NotificationDecision | None:
    """step 0.5(E7-b): 비알람 사전분류 — 승인/안내성 메시지 억제(§17.4).

    non_alarm_filter_enabled=False(기본)면 평가하지 않아 심각도3 단락(step3) 포함 비트동일(회귀 0).
    활성 시 심각도3 단락보다 앞이지만 — 비알람은 severity 신뢰 불가하므로 마커 판정을 우선한다
    (§17.4). is_operational_alarm은 알람 마커 부재+비알람 마커 존재일 때만 False(애매하면 알람).
    """
    if g.non_alarm_filter_enabled and not is_operational_alarm(g.event):
        return _decision(
            g,
            TIER_SUPPRESS,
            "비운영 알람 — 승인/안내성 메시지(마커 기반 사전 억제)",
            STAGE_NON_ALARM,
            {"markers": non_alarm_markers(g.event)},
        )
    return None


def _rule_severity3(g: GateInputs) -> NotificationDecision | None:
    """step 3: 심각도 3 → 즉시 PAGE(단락, 억제 금지 D-035).

    (plans/162 W5 · D-336) 플래그 on + 제니퍼 정책 경로면 유형 정책 표를 본다 — 통보(page)
    유형만 PAGE, 그 밖(표 밖 유형·default 행)은 TICKET. 어느 쪽이든 여기서 끝난다(뒤 억제
    단계 미경유 · DASHBOARD 아래로 내리지 않는다 D-317 ①). 수집 실패·폴스타는 현행 단락.
    """
    if g.effective_severity != 3:
        return None
    if (
        g.apm_sev3_policy_enabled
        and g.noise_ctx is not None
        and g.noise_ctx.get("source") == NOISE_SOURCE_APM_POLICY
    ):
        policy_page = g.noti_policy == "notify"
        sev3_evidence = {
            EVIDENCE_APM_SEV3_POLICY: {
                "notify": "page" if policy_page else "ticket",
                "policy_row": (g.apm_policy or {}).get("policy_row"),
            }
        }
        if policy_page:
            return _decision(
                g,
                TIER_PAGE,
                "심각도3 — 제니퍼 유형 정책(PAGE 유형) · 항상 통보",
                STAGE_SEVERITY3,
                sev3_evidence,
            )
        return _decision(
            g,
            TIER_TICKET,
            "심각도3 — 제니퍼 유형 정책(비PAGE 유형) · TICKET",
            STAGE_SEVERITY3,
            sev3_evidence,
        )
    return _decision(
        g, TIER_PAGE, "심각도3 — 항상 통보(억제 단계 미경유)", STAGE_SEVERITY3
    )


def _rule_clear(g: GateInputs) -> NotificationDecision | None:
    """step 4: 해소(severity 0)/자가복구 상관(매트릭스 미경유)."""
    if not bool(getattr(g.event, "is_clear", False)):
        return None
    if g.self_heal:
        return _decision(
            g,
            TIER_SUPPRESS,
            "자가복구 상관 — 발생 알람과 해소 매칭으로 억제",
            STAGE_SELF_HEAL,
        )
    if g.resolved_to_dashboard:
        return _decision(
            g, TIER_DASHBOARD, "독립 해소 — 대시보드 표시(통보 없음)", STAGE_RESOLVED
        )
    return _decision(
        g, TIER_SUPPRESS, "독립 해소 — 매칭 발생 없음, 감사 기록만", STAGE_RESOLVED
    )


def _rule_collection_failed(g: GateInputs) -> NotificationDecision | None:
    """step 5: 수집 실패 보수 처리(심각도/해소 규칙 다음).

    (plans/162 R4 · D-330 ③) 플래그 on이면 여기서 PAGE를 내지 않는다 — 심각도3은 step 3에서 이미
    PAGE이고, 그 밖은 중요도 「보통」으로 뒤 단계를 그대로 탄다(이때 뒤 판정에 수집 실패 표지가
    실린다 — RULES 「표지」 열).
    """
    if not (
        g.collection_failed
        and g.effective_severity >= 1
        and not g.collection_failed_sev3_only
    ):
        return None
    shadow_evidence: dict[str, Any] | None = None
    if str(getattr(g.config, "page_review_mode", "off") or "off") == _PAGE_REVIEW_SHADOW:
        # 같은 입력·같은 now로 플래그 on 판정을 한 번 더 계산한다(재귀는 on 경로라 1단계뿐).
        would = decide_notification(
            g.event,
            g.history_stats,
            g.analysis,
            g.noise_ctx,
            _ConfigOverride(g.config, collection_failed_page_sev3_only=True),
            self_heal=g.self_heal,
            inhibited=g.inhibited,
            flapping=g.flapping,
            storm=g.storm,
            correlated=g.correlated,
            annotation=g.annotation,
            silence_rules=g.silence_rules,
            now=g.now or datetime.now(UTC),
            apm_policy=g.apm_policy,
            cross_source=g.cross_source,
        )
        shadow_evidence = {
            EVIDENCE_COLLECTION_FAILED_SHADOW: {
                "mode": _PAGE_REVIEW_SHADOW,
                "would_tier": would.tier,
            }
        }
    return _decision(
        g,
        TIER_PAGE,
        "신호 수집 실패 — 보수적 PAGE",
        STAGE_COLLECTION_FAILED,
        shadow_evidence,
    )


def _rule_maintenance(g: GateInputs) -> NotificationDecision | None:
    """step 6: 유지보수 모드 → SUPPRESS(기록 유지)."""
    if g.maintenance:
        return _decision(
            g, TIER_SUPPRESS, "유지보수 모드 — 신규 발송 억제(감사 기록)", STAGE_MAINTENANCE
        )
    return None


def _rule_silence(g: GateInputs) -> NotificationDecision | None:
    """step 6.2(Plan 54): 침묵 — 운영자가 건 규칙에 걸리면 억제(감사 기록 유지).

    silence_enabled=False(기본)면 워커가 silenced를 산출하지 않아 평가 자체가 없다(회귀 0).
    심각도3은 step3에서 이미 단락되어 이 단계에 도달하지 않는다 — 규칙으로 뚫을 수 없다.
    """
    if not g.silence_rules:
        return None
    matched_rule = match_rules(
        g.silence_rules,
        g.event,
        effective_severity=g.effective_severity,
        now=g.now or datetime.now(timezone.utc),  # noqa: UP017 — 리팩터 전 그대로(동작 불변)
    )
    if matched_rule is None:
        return None
    return _decision(
        g,
        TIER_SUPPRESS,
        f"침묵 규칙({matched_rule.id}) — {matched_rule.reason or '운영자 지정 기간 억제'}",
        STAGE_SILENCE,
        _silence_evidence(matched_rule),
    )


def _rule_dependency(g: GateInputs) -> NotificationDecision | None:
    """step 6.4(E2·E4): 의존성 억제 — 조상 비정상 시 자식 연쇄 노이즈(§3.6·§6.2).

    dependency_suppression=False(기본)면 단계 자체를 평가하지 않아 E1 무변경.
    """
    if not g.dependency_suppression:
        return None
    noise_ctx = g.noise_ctx
    # (plans/112 S6) 근본원인 자원명은 다홉 조상 탐색이 채울 때만 싣는다(1홉이면 보통 없음).
    root_name = noise_ctx.get("root_resource_name") if noise_ctx else None
    root_name_evidence = (
        {"root_resource_name": root_name} if isinstance(root_name, str) and root_name else {}
    )
    # E4 다홉 하이브리드(§6.2): cascaded(다홉 조상 비정상)면 root 통보 여부로 억제 강도 분기.
    # cascaded 미제공(1홉 모드·수집 실패)이면 아래 현행 parent_avail_status 판정으로 폴백.
    if noise_ctx and noise_ctx.get("cascaded"):
        root_notified = bool(noise_ctx.get("root_notified"))
        multi_hop_evidence = {
            "mode": "multi_hop",
            "root_notified": root_notified,
            **root_name_evidence,
        }
        if noise_ctx.get("root_notified"):
            return _decision(
                g,
                TIER_SUPPRESS,
                "의존성 억제(다홉) — 근본원인 노드 통보됨",
                STAGE_DEPENDENCY,
                multi_hop_evidence,
            )
        return _decision(
            g,
            TIER_DASHBOARD,
            "의존성 연쇄(다홉) — 근본원인 미통보, 대시보드 강등",
            STAGE_DEPENDENCY,
            multi_hop_evidence,
        )
    # 1홉 폴백(현행 무변경): parent_avail_status 0=정상, ≠0=비정상, None=미수집(보수적 비억제·R-3).
    parent_avail_status = noise_ctx.get("parent_avail_status") if noise_ctx else None
    if parent_avail_status is not None and parent_avail_status != 0:
        return _decision(
            g,
            TIER_SUPPRESS,
            f"의존성 억제 — 부모 리소스 비정상(AVAIL_STATUS={parent_avail_status}),"
            " 자식 연쇄 노이즈",
            STAGE_DEPENDENCY,
            {"mode": "one_hop", **root_name_evidence},
        )
    return None


def _rule_inhibition(g: GateInputs) -> NotificationDecision | None:
    """step 6.5(E2): 인히비션 — 동일 서버 상위 심각도 발생 중 하위 음소거(§3.4).

    inhibited는 워커가 결정적으로 탐지(자기억제 금지·window)하여 인자로 전달.
    inhibition_enabled=False(기본)면 평가하지 않아 E1 무변경.
    """
    if g.inhibition_enabled and g.inhibited:
        return _decision(
            g,
            TIER_SUPPRESS,
            "인히비션 — 동일 서버 상위 심각도 발생 중, 하위 음소거",
            STAGE_INHIBITION,
        )
    return None


def _rule_flapping(g: GateInputs) -> NotificationDecision | None:
    """step 6(§6 파이프라인·E2): 플래핑 — 상태 진동 시 안정화까지 보류(§3.7).

    flapping은 워커가 도메인 순수함수(flapping.py)로 산출하여 인자로 전달.
    저심각도(effective_severity<=suppress_max_severity)만 억제 — 심각도3은 위에서 단락(불변).
    flapping_enabled=False(기본)면 평가하지 않아 E1 무변경.
    """
    if (
        g.flapping_enabled
        and g.flapping
        and g.effective_severity <= g.suppress_max_severity
    ):
        return _decision(
            g,
            TIER_SUPPRESS,
            "플래핑 — 상태 진동(Nagios), 안정화까지 통보 보류",
            STAGE_FLAPPING,
        )
    return None


def _rule_storm(g: GateInputs) -> NotificationDecision | None:
    """step 7(§6 파이프라인·E2): 스톰 — 동일 서버 다발 시 대표만 통보(§3.8).

    storm은 워커가 사건창(동일 db_id|server) 카운트로 산출하여 인자로 전달.
    storm_grouping_enabled=False(기본)면 평가하지 않아 E1 무변경.
    """
    if g.storm_grouping_enabled and g.storm:
        return _decision(
            g, TIER_SUPPRESS, "스톰 — 동일 서버 다발, 대표 1건만 통보", STAGE_STORM
        )
    return None


def _rule_correlation(g: GateInputs) -> NotificationDecision | None:
    """step 7.5(E2): 크로스-호스트 상관 — 클러스터 대표 외 억제(§4.2).

    correlated는 워커가 온라인 그리디 군집(correlation.py)으로 산출하여 인자로 전달.
    cross_host_correlation_enabled=False(기본)면 평가하지 않아 회귀 0. 심각도3은 step3에서
    이미 단락되어 이 단계에 도달하지 않는다(군집돼도 각각 PAGE, 불변). storm(동일 서버 다발)과
    사유를 구분한다 — storm 경로 재사용 시 "동일 서버 다발" 사유가 감사를 오도하기 때문.
    """
    if g.cross_host_correlation_enabled and g.correlated:
        return _decision(
            g, TIER_SUPPRESS, "크로스-호스트 상관 — 클러스터 대표 외 억제", STAGE_CORRELATION
        )
    return None


def _rule_cross_source_cap(g: GateInputs) -> NotificationDecision | None:
    """step 7.6(plans/144 W3): 크로스소스 사건 상관 — 증상 DASHBOARD 상한(§5.2 · G-2).

    cross_source는 워커가 사건 추적기로 산출해 넘긴다(None·off면 미평가 → 비트동일). 상한은
    강제 모드 + 결합 조건 전부 충족 + 규칙 단위 enforce일 때만 건다. **티어를 올리지 않는다** —
    매트릭스 결과가 DASHBOARD보다 높을 때만 낮추고, 이미 DASHBOARD 이하면 통과(None)해 다음
    단계(7.7·8)로 그대로 간다(7.7처럼 terminal DASHBOARD를 반환하면 매트릭스 SUPPRESS가
    DASHBOARD로 올라간다). 앞 단계 억제가 우선이고 심각도3은 step3에서 이미 단락되어 이 단계에
    도달하지 않는다. 매트릭스는 순수 계산이라 여기서 먼저 한 번 보고 끝에 다시 만들어도 같다.
    """
    if not g.cross_source_cap:
        return None
    matrix_decision = _matrix_decision(g)
    if _TIER_RANK[matrix_decision.tier] > _TIER_RANK[TIER_DASHBOARD]:
        cs = g.cross_source or {}
        return _decision(
            g,
            TIER_DASHBOARD,
            f"크로스소스 사건 상관 — 원인 {cs.get('cause_alarm_id', '')} 통보 중, 증상 "
            f"대시보드 강등(규칙 {cs.get('rule_id', '')} · 호스트 {cs.get('host_key', '')} · "
            f"시차 {cs.get('lag_seconds', '')}초 · 매트릭스 {matrix_decision.tier})",
            STAGE_CROSS_SOURCE,
            {"capped_from": matrix_decision.tier},
        )
    return None


def _rule_annotation(g: GateInputs) -> NotificationDecision | None:
    """step 7.7(E7-a·B-9): 계획-무해 주석 코로보레이션 게이팅 DASHBOARD 강등(§17.3).

    텍스트 단독으로는 절대 억제강화 금지 — planned_work **AND** (resolution 또는 E2 클러스터
    소속(correlated) 또는 E5 change_nearby)가 동시 충족될 때만 DASHBOARD 강등(SUPPRESS 아님·
    E4 하이브리드 정합). 주석 단독(코로보레이션 없음)이면 강등하지 않고 통상 매트릭스로
    진행(첨부만). annotation_planned_suppress=False(기본)면 평가하지 않아 비트동일(회귀 0).
    심각도3은 step3에서 이미 단락되어 이 단계에 도달하지 않는다(불변). SUPPRESS는 위 단계가
    우선(더 강한 억제 보존).
    """
    annotation = g.annotation
    if not (g.annotation_planned_suppress and annotation and annotation.get("planned_work")):
        return None
    noise_ctx = g.noise_ctx
    corroborated = (
        bool(annotation.get("resolution"))
        or bool(g.correlated)
        or bool(noise_ctx and noise_ctx.get("change_nearby"))
    )
    if not corroborated:
        return None
    # (plans/112 S6) 강등을 뒷받침한 출처 — 위 corroborated와 같은 세 조건을 따로 적는다.
    corroborated_by = [
        source
        for source, hit in (
            ("resolution", bool(annotation.get("resolution"))),
            ("correlation", bool(g.correlated)),
            ("change_nearby", bool(noise_ctx and noise_ctx.get("change_nearby"))),
        )
        if hit
    ]
    return _decision(
        g,
        TIER_DASHBOARD,
        "계획-무해 주석(코로보레이션) — 대시보드 강등(계획작업+해소/상관/변경근접)",
        STAGE_ANNOTATION,
        {"corroborated_by": corroborated_by},
    )


# ── step 8~9.5: 우선순위 매트릭스 · 보조 조정 · 앱 영향 승격(규칙 표를 모두 통과한 최종 관문) ──


def _adjustment_signals(g: GateInputs) -> tuple[list[str], list[str]]:
    """step 9 보조 조정 신호 — (승격 사유, 강등 사유) 목록을 수집한다."""
    effective_severity = g.effective_severity
    promote: list[str] = []
    demote: list[str] = []
    if g.noti_policy == "notify":
        promote.append("폴스타 통보 정책(notify)")
    if g.noti_policy == "suppress":
        demote.append("폴스타 비통보 정책(suppress)")
    if g.is_routine is True and effective_severity <= g.suppress_max_severity:
        demote.append("일상 반복 패턴(is_routine)")
    # (D-318) is_routine=False는 승격 근거가 아니다 — 분석 프롬프트가 이력 부재 시 false를
    # 지시해 사실상 상시 참이고, 운영 24h 매트릭스 PAGE 66건 전부가 이 승격 하나로 났다.
    # E4: LLM 액션가능성(피드백 few-shot) — 승격 비대칭(재현율 우선).
    # actionable → promote(항상 안전). noise → demote(is_routine과 동일하게 SUPPRESS 하한 가드).
    # 승격우선 기계(_adjusted_tier)가 promote 신호와 공존 시 noise demote를 무시한다.
    if g.llm_actionability == "actionable":
        promote.append("LLM 액션가능성(피드백)")
    if g.llm_actionability == "noise" and effective_severity <= g.suppress_max_severity:
        demote.append("LLM 노이즈 판단(피드백)")
    # Plan 60 E5(§7.2): 변경 근접 알람은 **억제가 아니라 승격** — 원인성 판단·PAGE 근거 보강
    # (재현율 우선). change_correlation off·미근접이면 change_nearby 키 부재/None → 무영향
    # (회귀 0).
    # 이 모듈은 change 모듈을 import하지 않고 noise_ctx의 bool만 소비한다(순수성).
    if g.noise_ctx and g.noise_ctx.get("change_nearby"):
        promote.append("변경 근접(원인성)")
    # plans/144 §4.2: 제니퍼 지표형 경고가 지속 조건(창 안 반복)을 못 채우면 강등 후보. 워커가
    # 지표형·정책 행 persistence 보유일 때만 persistence를 싣는다(off·폴스타면 None → 무영향).
    persistence = g.apm_policy.get("persistence") if g.apm_policy else None
    if (
        isinstance(persistence, dict)
        and persistence.get("met") is False
        and effective_severity == 2
    ):
        demote.append(_PERSISTENCE_UNMET)
    return promote, demote


def _adjusted_tier(base_tier: str, promote: list[str], demote: list[str]) -> tuple[str, str]:
    """step 9 누적 조정 — 1단계 이내·승격 우선 기계와 하한을 적용해 (티어, 사유 조각)을 돌려준다."""
    if promote:  # 승격/강등 충돌 시 승격 우선(재현율 우선)
        adjusted = min(_TIER_RANK[base_tier] + 1, _TIER_RANK[TIER_PAGE])
        adjust_note = " · 승격: " + ", ".join(promote)
        if demote:
            adjust_note += f" (강등 신호 {', '.join(demote)}는 승격 우선으로 무시)"
        return _RANK_TIER[adjusted], adjust_note
    if demote:
        # 지속 조건 미달만으로 내릴 때는 DASHBOARD가 하한이다(G-1 (b) — 완전 억제 미확정).
        # 다른 강등 사유가 함께 있으면 현행 하한(SUPPRESS) 그대로다.
        floor = TIER_DASHBOARD if set(demote) == {_PERSISTENCE_UNMET} else TIER_SUPPRESS
        adjusted = max(_TIER_RANK[base_tier] - 1, _TIER_RANK[floor])
        return _RANK_TIER[adjusted], " · 강등: " + ", ".join(demote)
    return base_tier, ""


def _app_impact_promotion(g: GateInputs, tier: str) -> tuple[str, str, dict[str, Any]]:
    """step 9.5(plans/87 J4 · R-7 · D-195 ②): 앱 영향 승격 — **승격 전용 비대칭**.

    noise_ctx["app_impact"](Plan 55 예약키)는 게이트 노드가 APM 게이트웨이 `apm_events`로
    채운다(같은 hostname·사건창의 fatal 이벤트 1건 이상). 위 조정 결과가 DASHBOARD·TICKET일
    때만 PAGE로 올린다 — SUPPRESS(강등 결과 포함)는 되살리지 않고 PAGE는 그대로다. 억제
    단계와 심각도3 단락은 이 지점 앞에서 끝나므로 영향이 없다. 키 부재·None(off·미수집·
    agentic 예약값)이면 무영향(회귀 0).
    signals 동결 스키마는 확장하지 않는다(change_nearby 전례 — 근거는 reason·evidence).

    Returns:
        (최종 티어, 사유 조각, evidence 조각) — 승격이 없으면 (tier, "", {}).
    """
    app_impact: Any = g.noise_ctx.get("app_impact") if g.noise_ctx else None
    fatal_events = _app_impact_fatal_events(app_impact)
    if fatal_events and tier in (TIER_DASHBOARD, TIER_TICKET):
        return (
            TIER_PAGE,
            f" · 앱 영향 승격: APM fatal {fatal_events}건",
            {
                "app_impact_fatal_events": fatal_events,
                "app_impact_event_types": list(app_impact.get("event_types") or []),
                "app_impact_was_signals": list(app_impact.get("was_signals") or []),
                "app_impact_source": str(app_impact.get("source") or ""),
            },
        )
    return tier, "", {}


def _matrix_decision(g: GateInputs) -> NotificationDecision:
    """step 8~9.5 결과(매트릭스 단계 판단)를 만든다.

    순수 계산이라 호출 시점·횟수와 무관하게 같은 결과다(step 7.6이 먼저 한 번 본다).
    """
    # ── step 8: 우선순위 매트릭스(§3.2) ─────────────────────
    base_tier = _matrix_tier(g.effective_severity, g.importance)
    # ── step 9: 보조 조정(1단계 이내·보수적, 승격 우선) ──────
    promote, demote = _adjustment_signals(g)
    tier, adjust_note = _adjusted_tier(base_tier, promote, demote)
    # ── step 9.5: 앱 영향 승격 ──
    tier, app_impact_note, app_impact_evidence = _app_impact_promotion(g, tier)

    reason = (
        f"매트릭스(심각도{g.effective_severity}×중요도{g.importance}) → {base_tier}{adjust_note}"
        f"{app_impact_note} → 최종 {tier}"
    )
    # (plans/112 S6) 사유 문자열에 합쳐 있던 산식 조각을 구조화해 함께 남긴다(판정 무관).
    return _decision(
        g,
        tier,
        reason,
        STAGE_MATRIX,
        {
            "base_tier": base_tier,
            "promote": list(promote),
            "demote": list(demote),
            **app_impact_evidence,
        },
    )


_Rule = Callable[[GateInputs], NotificationDecision | None]

# ── 판정 규칙 표 — 조기 종결 단계의 **평가 순서 단일 출처** ──────────────────
# decide_notification은 이 표를 위에서부터 평가해 첫 결정(None이 아닌 값)을 돌려주고, 전부 통과하면
# step 8~9.5 매트릭스(_matrix_decision)로 끝낸다. 첫째 열 단계 번호는 문서·결정이 참조하는 기존
# 라벨 그대로다 — 번호의 크기가 아니라 **표의 행 순서**가 평가 순서다(예: 플래핑 「6」은 6.5 뒤).
# 셋째 열 「표지」(plans/162 R4 · D-330 ③): True인 행과 끝의 매트릭스는 step 5를 통과한 뒤의
# 판정이라, 수집 실패 · 실효 심각도>=1이면 evidence에 EVIDENCE_COLLECTION_FAILED=True를 싣는다
# (off의 step 5 PAGE와 같은 모집단). False인 행(0.5·3·4·5)의 결정에는 싣지 않는다.
# 관측용 단계 선언 순서(STAGE_ORDER)와는 별개다 — 이 표는 판정 평가 순서다.
RULES: tuple[tuple[str, _Rule, bool], ...] = (
    ("0.5", _rule_non_alarm, False),  # 비알람 사전분류 — 심각도3 단락보다 앞(§17.4)
    ("3", _rule_severity3, False),  # 심각도3 단락(D-048 · D-336)
    ("4", _rule_clear, False),  # 해소·자가복구
    ("5", _rule_collection_failed, False),  # 수집 실패 보수적 PAGE(D-330 ③ on이면 통과)
    ("6", _rule_maintenance, True),  # 유지보수
    ("6.2", _rule_silence, True),  # 침묵 규칙
    ("6.4", _rule_dependency, True),  # 의존성(다홉/1홉)
    ("6.5", _rule_inhibition, True),  # 인히비션
    # 플래핑 — 번호 「6」은 유지보수와 겹치는 옛 §6 파이프라인 번호다(문서·결정이 참조해 유지).
    ("6", _rule_flapping, True),
    ("7", _rule_storm, True),  # 스톰
    ("7.5", _rule_correlation, True),  # 크로스-호스트 상관
    ("7.6", _rule_cross_source_cap, True),  # 크로스소스 상한(매트릭스 > DASHBOARD일 때만)
    ("7.7", _rule_annotation, True),  # 계획-무해 주석
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
    silence_rules=None,  # noqa: ANN001 — list[SilenceRule] | None (Plan 54 모듈 4)
    now: datetime | None = None,
    apm_policy: dict[str, Any] | None = None,
    cross_source: dict[str, Any] | None = None,
) -> NotificationDecision:
    """E1 결정 파이프라인(순서형·결정적, 첫 종착 확정) + E2 의존성/인히비션/플래핑/스톰 단계.

    평가 순서의 정본은 모듈 수준 `RULES` 표다(plans/171) — 입력을 `GateInputs`로 모아 표의 규칙을
    차례로 평가하고, 첫 결정을 돌려준다. 전부 통과하면 step 8~9.5 매트릭스(`_matrix_decision`)다.

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

    plans/162 R4 · D-330 ③(collection_failed_page_sev3_only 뒤 — 기본 False면 위 동작 그대로):
        - on이면 step 5가 PAGE를 내지 않는다. 심각도3은 step 3에서 이미 PAGE이고, 그 밖은 중요도
          「보통」(step 2 보수화 값)으로 뒤 단계(유지보수·침묵·…·매트릭스)를 그대로 탄다
          (sev2 → TICKET).
        - on이면 step 5를 통과한 판정(수집 실패 · 실효 심각도>=1 · step 0.5/3/4에서 끝나지 않음)의
          evidence에만 EVIDENCE_COLLECTION_FAILED=True를 싣는다 — off의 step 5 PAGE와 같은 모집단.
        - off이고 page_review_mode=="shadow"면 step 5 PAGE는 그대로 두고 evidence에
          {EVIDENCE_COLLECTION_FAILED_SHADOW: {"mode": "shadow", "would_tier": …}}만 싣는다.
          would_tier는 같은 입력·플래그 on으로 한 번 더 판정한 티어다(같은 now — 결정적).

    Plan 54 침묵(step 6.2 — silence_enabled 뒤, 규칙 미주입이면 무변경):
        - silence_rules(인자)는 워커가 저장소에서 읽어 넘기는 **활성 규칙 목록**이다. 매칭은
          이 모듈이 한다 — 규칙의 심각도 상한이 **실효 심각도**(AI 보강 반영 후)와 대조돼야
          하는데 그 값은 여기서만 알 수 있기 때문이다(flapping·storm의 bool 주입과 다른 이유).
          저장소·파일은 import하지 않는다(도메인 순수성 유지 — 순수 함수 silence.match_rules만 사용).
        - **유지보수(step 6) 다음**에 둔다: 유지보수는 시스템이 알려준 사실이고 침묵은 운영자의
          의도라, 사실이 먼저 사유로 남아야 감사가 정확하다.
        - 심각도3은 step3에서 이미 단락되므로 **어떤 침묵 규칙으로도 조용해지지 않는다**.
        - now는 규칙 만료 판정 기준 시각이다(미지정이면 현재 UTC).

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

    plans/144 제니퍼 유형 정책(apm_noise_policy_enabled 뒤 — 워커가 제니퍼 알람에만 apm_policy를
    넘긴다. None(기본·폴스타·off)이면 아래 전부 미평가로 비트동일):
        - noise_ctx["source"]=="apm_policy"면 importance_id를 라벨로 읽는다(미지 라벨은 보통).
        - 지문은 apm_policy["event_type_norm"]으로 만든다(워커 지문·해소 짝맞춤과 같은 값).
        - step 9: apm_policy["persistence"]["met"] is False AND 실효 심각도 2 → demote에
          "지속 조건 미달". 승격 우선 규칙 그대로이며, 강등 사유가 이것뿐이면 하한은 DASHBOARD다.
          카운트는 워커가 계산한다 — 이 모듈은 bool·숫자만 소비한다(D-109 sig_label 전례).
        - evidence["apm_policy"]에 정규화 유형·적용 행·지속 조건 카운트를 싣는다(감사 전용).

    plans/162 W5 · D-336 제니퍼 심각도3(apm_sev3_policy_enabled 뒤 — 기본 False면 단락 PAGE 그대로):
        - on이고 noise_ctx["source"]=="apm_policy"면 step 3이 유형 정책 표를 본다 —
          noti_policy=="notify"(표 `notify: page` 행)면 PAGE, 그 밖(표 밖 유형·default)은 TICKET.
          단계는 severity3 그대로이고 뒤 억제 단계로 가지 않는다. evidence에
          EVIDENCE_APM_SEV3_POLICY({notify, policy_row})를 싣는다.
        - 폴스타·수집 실패(None·unavailable)는 현행 단락 PAGE.

    plans/144 W3 크로스소스 사건 상관(step 7.6 — cross_source_mode≠off일 때 워커가 사건에 붙은
    알람에만 cross_source를 넘긴다. None(기본·off)이면 아래 전부 미평가로 비트동일):
        - cross_source는 워커가 사건 추적기(cross_source.py)로 산출한 신호다 — 이 모듈은 값만
          읽는다.
        - 상한 조건: mode=="enforce" AND action=="demote"(결합 조건 전부 충족) AND 규칙 enforce
          is True. 이때 step 8~9.5 결과가 DASHBOARD보다 높을 때만 DASHBOARD로 **낮춘다**(티어를
          올리지 않는다 — 매트릭스 결과가 DASHBOARD 이하면 그대로 다음 단계로 간다). 앞 단계
          억제(유지보수·침묵·의존성·인히비션·플래핑·스톰·상관)가 우선이고 심각도3은 step3에서
          끝난다.
        - 그 밖(shadow·annotate·enforce인데 규칙 enforce false·조건 미충족·link)은 판정 불변이다.
        - evidence에 결정 단계와 무관하게 episode_id·cross_source(applied=실제 상한 여부)를 싣는다.
    """
    g = _gate_inputs(
        event,
        history_stats,
        analysis,
        noise_ctx,
        config,
        self_heal=self_heal,
        inhibited=inhibited,
        flapping=flapping,
        storm=storm,
        correlated=correlated,
        annotation=annotation,
        silence_rules=silence_rules,
        now=now,
        apm_policy=apm_policy,
        cross_source=cross_source,
    )
    # RULES 「표지」 열이 True인 행과 매트릭스가 받는 보기 — 그 행들은 step 5를 통과해야만 닿는다.
    marked = replace(
        g, mark_collection_failed=g.collection_failed and g.effective_severity >= 1
    )
    for _step, rule, mark in RULES:
        decision = rule(marked if mark else g)
        if decision is not None:
            return decision
    return _matrix_decision(marked)


def _app_impact_fatal_events(app_impact: object) -> int:
    """`app_impact` 예약값에서 fatal 이벤트 수를 읽는다(dict가 아니거나 값이 이상하면 0)."""
    if not isinstance(app_impact, dict):
        return 0
    try:
        return max(int(app_impact.get("fatal_events") or 0), 0)
    except (TypeError, ValueError):
        return 0
