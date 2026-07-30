"""알람 텍스트·운영자 주석 신호 추출 (Plan 60 E7-a · D-116 — 결정적 순수 도메인).

폴스타 알람의 conditionLog·description 등 자유 텍스트에서 '계획작업/해소/운영자 접수'
마커를 정규식으로 추출한다. 결정적(정규식·stdlib-only)이며 LLM에 의존하지 않는다.
마커가 없으면 빈 신호(하위호환)를 반환한다.

**(Plan 67 R3-(v) · D-132) LLM 분류 전환**: 운영자 손글 한국어는 정규식 어휘를 벗어난다
("이상무"·"문제없음" 등 미매칭). LLM 분류는 **application 계층**
(`src/alarm/application/annotation_classifier.py`)이 수행하고, 이 모듈은 그 결과를
`AnnotationLabel` enum으로 받아 `signal_from_labels`로 신호를 조립한다 — domain에 LLM
의존을 넣지 않는다(D-035 경계 유지). `extract_annotation_signal`은 삭제하지 않고 분류
실패·타임아웃 시의 **강등 폴백**이자 플래그 OFF 기본 경로로 남는다.

**억제≠삭제의 텍스트 확장(§17.3)**: `compute_fingerprint`는 주석 텍스트를 포함하지 않으므로
(notification_policy.py L51~64) 운영자 주석 재발신은 같은 지문으로 E1 dedup에 억제되고,
억제는 그래프 진입 이전에 ACK된다 — 그 결과 재발신이 실어 온 계획작업·해소 신호가 폐기된다.
본 모듈은 그 텍스트 신호를 결정적으로 추출해 "노이즈는 억제, 신호는 하베스트"를 가능케 한다.

이 모듈은 domain 계층이므로 표준 라이브러리만 의존한다(src 내 다른 모듈 import 금지).
정책 모듈(notification_policy)은 이 모듈을 import하지 않는다 — 워커가 신호를 산출해
정책·감사 경로에 **값으로 주입**한다(§17.7 정책 순수성). LLM은 개입하지 않는다(annotate-only
보조는 기존 alarm_analyzer 재사용, 본 추출은 결정적 1차, D-035 경계 준수).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

# ── 마커 정규식(§17.3, 결정적·재현율 우선) ────────────────────────────────
# 계획작업 마커: 예정된 작업/계획정지/IPL/점검/작업 예정/정기 점검 등.
_PLANNED_WORK = re.compile(
    r"예정된\s*작업|계획\s*정지|정기\s*점검|작업\s*예정|점검\s*예정|점검|IPL"
)
# 해소·무영향 마커: 서비스 영향 없음/이상 없음/정상 확인/복구 완료 등.
# 주의: resolution은 is_clear(sev0)와 동일 취급하지 않는다(폴스타 SSOT 보존·자동 클로즈 없음, D-003).
_RESOLUTION = re.compile(
    r"서비스\s*영향\s*없음|영향\s*없음|이상\s*없음|정상\s*확인|복구\s*완료|서비스\s*이상없음"
)
# 운영자 접수 마커: '=> 담당자'/통화/확인 후 연락 등.
_OPERATOR_ACK = re.compile(r"=>\s*담당자|통화|확인\s*후\s*연락")


class AnnotationLabel(str, Enum):
    """주석 분류 라벨 (Plan 67 R3-(v) · D-132 — 3분류 고정).

    application 계층 분류기(LLM)와 domain 사이의 계약이다. 값은 `AnnotationSignal` 필드명과
    동일해 감사 dict 키와 일치한다. **신규 라벨을 추가하지 않는다** — 게이트(코로보레이션·
    하베스팅)가 소비하는 신호 집합이 3종으로 고정되어 있다(§17.3).
    """

    PLANNED_WORK = "planned_work"
    RESOLUTION = "resolution"
    OPERATOR_ACK = "operator_ack"


@dataclass(frozen=True)
class AnnotationSignal:
    """알람 텍스트에서 추출한 결정적 주석 신호(감사·게이팅 입력).

    세 마커는 상호 배타가 아니며 동시 참일 수 있다(예: 재발신에 operator_ack + resolution).
    빈 신호(전부 False)는 '마커 없음'(하위호환)을 뜻한다.
    """

    planned_work: bool = False
    resolution: bool = False
    operator_ack: bool = False

    def has_signal(self) -> bool:
        """마커가 하나라도 있으면 True(빈 신호 판별)."""
        return self.planned_work or self.resolution or self.operator_ack

    def to_dict(self) -> dict:
        """감사·주입용 dict 스냅샷(record_recurrence annotation 필드·게이트 인자)."""
        return {
            "planned_work": self.planned_work,
            "resolution": self.resolution,
            "operator_ack": self.operator_ack,
        }


def extract_annotation_signal(text: str) -> AnnotationSignal:
    """자유 텍스트에서 계획작업/해소/운영자접수 마커를 결정적으로 추출한다.

    None·빈 문자열·마커 부재 시 빈 신호(AnnotationSignal())를 반환한다(하위호환). 동일 입력은
    항상 동일 출력(결정적). 정규식 검색만 수행하며 부작용·외부 의존이 없다(§17.7).

    Args:
        text: 알람 자유 텍스트(예: event.condition_log 또는 event.description).

    Returns:
        추출된 AnnotationSignal(마커 없으면 전부 False).
    """
    if not text:
        return AnnotationSignal()
    t = str(text)
    return AnnotationSignal(
        planned_work=bool(_PLANNED_WORK.search(t)),
        resolution=bool(_RESOLUTION.search(t)),
        operator_ack=bool(_OPERATOR_ACK.search(t)),
    )


def signal_from_labels(labels: Iterable[AnnotationLabel]) -> AnnotationSignal:
    """분류 라벨 집합을 AnnotationSignal로 조립한다 (Plan 67 R3-(v) · D-132).

    domain은 분류 **결과(enum)만** 소비하며 분류 수단(정규식/LLM)을 알지 않는다. 빈 집합은
    빈 신호(마커 없음)와 같다. `AnnotationLabel`이 아닌 항목은 무시한다 — 분류기 환각·
    프로토콜 오차가 domain 불변식을 깨지 못하게 한다(모르는 라벨은 신호 없음과 동일 취급).

    Args:
        labels: 분류기가 판정한 라벨들(중복·순서 무관).

    Returns:
        해당 라벨만 True인 AnnotationSignal.
    """
    known = {label for label in labels if isinstance(label, AnnotationLabel)}
    return AnnotationSignal(
        planned_work=AnnotationLabel.PLANNED_WORK in known,
        resolution=AnnotationLabel.RESOLUTION in known,
        operator_ack=AnnotationLabel.OPERATOR_ACK in known,
    )
