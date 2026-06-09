"""알람 도메인 모델.

AlarmEvent: 폴스타로부터 수신된 알람 이벤트 (폴스타 템플릿 변수와 1:1 대응)
AlarmAnalysisResult: LLM 분석 결과 및 발송 내역
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AlarmEvent:
    """폴스타 알람 이벤트.

    폴스타 단일행 JSON 템플릿 변수와 1:1 대응하도록 설계한다.
    Redis Stream 'alarm:raw'에서 소비된 JSON 페이로드를 변환하여 생성한다.

    폴스타 등록 템플릿 형식:
        {"dbId":"<상수>","serverName":"${platformName}","hostname":"${hostname}",
         "ipAddress":"${ipAddress}","resourceAncestry":"${resourceAncestry}",
         "alarmId":"${alarmId}","severity":${severity},"alarmStatus":"${alarmStatus}",
         "resourceType":"${resourceType}","resourceName":"${resourceName}","alarmName":"${alarmName}",
         "alarmTime":"${formatAlarmDate('yyyyMMddHHmmss')}",
         "conditions":"${conditions}","conditionLog":"${conditionLog}"}
    """

    # --- 식별자 ---
    db_id: str                          # 상수 (직접 기입) — 폴스타 인스턴스 식별자, DB의 db_id와 매핑
    server_name: str                    # ${platformName}  — 폴스타에 등록된 서버명, DB의 server_name과 매핑
    # --- 네트워크 정보 ---
    hostname: str                       # ${hostname}      — 호스트네임
    ip_address: str                     # ${ipAddress}     — IP 주소
    resource_ancestry: str              # ${resourceAncestry} — 폴스타 트리 전체 경로
    # --- 알람 상세 ---
    alarm_id: str                       # ${alarmId}       — 중복 제거 키
    severity: int                       # ${severity}      — 0=해소, 1=주의, 2=경고, 3=심각
    alarm_status: str                   # ${alarmStatus}   — '발생' / '해소'
    resource_type: str                  # ${resourceType}  — 'server.Server' 등
    resource_name: str                  # ${resourceName}  — 자원 이름
    alarm_name: str                     # ${alarmName}     — 알람 이름
    alarm_time: datetime                # ${formatAlarmDate('yyyyMMddHHmmss')} 파싱
    conditions: str                     # ${conditions}    — 발생/해소 임계 조건 정의
    condition_log: str                  # ${conditionLog}  — 이 알람이 울린 실제 값
    # --- 파생 필드 ---
    is_clear: bool = False              # alarm_status == '해소' 또는 severity == 0
    raw_payload: dict = field(default_factory=dict)  # 원본 JSON dict 보존


@dataclass
class AlarmAnalysisResult:
    """LLM 알람 분석 결과 및 채널별 발송 내역."""

    alarm_event: AlarmEvent
    severity_label: str                  # "심각" / "경고" / "주의" / "해소"
    summary: str                         # LLM 생성 요약 (1~2문장)
    probable_cause: str                  # 추정 원인
    recommended_action: str             # 권고 조치
    notification_channels: list[str]     # ["workb"] 등 복수 채널
    notifications_sent: dict[str, bool] = field(default_factory=dict)  # 채널별 발송 결과
    error: Optional[str] = None
