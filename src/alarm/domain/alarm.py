"""알람 도메인 모델.

AlarmEvent: 폴스타로부터 수신된 알람 이벤트 (템플릿 변수와 1:1 대응)
AlarmAnalysisResult: LLM 분석 결과 및 발송 내역
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AlarmEvent:
    """폴스타 알람 이벤트.

    폴스타 템플릿 변수와 1:1 대응하도록 설계한다.
    Redis Stream 'alarm:raw'에서 소비된 JSON 페이로드를 변환하여 생성한다.
    """

    # --- 폴스타 템플릿 변수 직접 매핑 ---
    alarm_id: str                        # ${alarmId}           — 중복 제거 키
    severity: int                        # ${severity}          — 0=해소, 1=주의, 2=경고, 3=심각
    alarm_name: str                      # ${alarmName}         — 알람 이름
    alarm_description: str               # ${alarmDescription}  — 알람 설명
    alarm_definition: str                # ${alarmDefinition}   — 알람 정의
    hostname: str                        # ${hostname}          — 대상 호스트명
    resource_name: str                   # ${resourceName}      — 대상 자원 이름
    resource_description: str            # ${resourceDescription} — 자원 설명
    resource_type: str                   # ${resourceType}      — 'server.Server' 등
    condition_log: str                   # ${conditionLog}      — 컨디션 로그
    # --- 수신 메타 ---
    source_db_id: str = ""               # 발신 DB ID (polestar, polestar_cm_gp 등)
    raw_payload: dict = field(default_factory=dict)   # 원본 페이로드 보존
    # --- 해소 알람 지원 (Phase 2 이후 완전 구현 예정) ---
    is_clear: bool = False               # True이면 해소 알람 (severity=0 등)
    triggered_at: Optional[datetime] = None  # 원본 발생 시각 (해소 매칭 후 채워짐)


@dataclass
class AlarmAnalysisResult:
    """LLM 알람 분석 결과 및 채널별 발송 내역."""

    alarm_event: AlarmEvent
    severity_label: str                  # "심각" / "경고" / "주의"
    summary: str                         # LLM 생성 요약 (1~2문장)
    probable_cause: str                  # 추정 원인
    recommended_action: str             # 권고 조치
    notification_channels: list[str]     # ["workb"] 등 복수 채널
    notifications_sent: dict[str, bool] = field(default_factory=dict)  # 채널별 발송 결과
    error: Optional[str] = None
