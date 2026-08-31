"""알람 도메인 모델.

AlarmEvent: 폴스타로부터 수신된 알람 이벤트 (폴스타 템플릿 변수와 1:1 대응)
AlarmHistoryEntry: 폴스타 DB에서 조회된 과거 알람 1건 (Plan 47)
AlarmHistoryStats: 이력 통계 + 1차 분류 결과 (Plan 47)
ProcessInfo / ProcessSnapshot: 알람 시점 영향 프로세스 스냅샷 (Plan 47-1)
AlarmAnalysisResult: LLM 분석 결과 및 발송 내역
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ServerIdentity:
    """폴스타 등록 기준 서버 식별 정보 (D-179) — hostname → 폴스타 등록 서버 테이블 역조회 결과.

    템플릿이 `${platformName}`·`${ipAddress}`를 지원하지 않아(EL1008E) 이벤트에는 hostname만
    실린다. 웹 UI·통보 본문이 폴스타 등록 서버명·IP·존을 표시할 수 있도록 별도로 붙인다.
    """

    name: str = ""            # cmm_resource.name — 폴스타 등록 서버명(공동존은 hostname과 다름)
    hostname: str = ""
    ip_address: str = ""      # cmm_resource.ipaddress
    os_type: str = ""         # core_config_prop OSType(EAV, 스칼라 서브쿼리) — 값이 있을 때만 UI 배지
    os_version: str = ""      # core_config_prop OSVerson(EAV 원본 철자) — 배지 툴팁의 상세 OS 버전
    zone: str = ""            # 존 코드(gongjon/bankjon) — config/db_registry.yaml 파생
    zone_label: str = ""      # 존 라벨
    site_label: str = ""      # 사이트 라벨(김포/여의도/은행존)
    source_label: str = ""    # 소스 배지 라벨 — 레지스트리 family 제품명("폴스타"); 소스 확장 시 family만 등록
    source_detail: str = ""   # 소스 배지 툴팁 — "폴스타 — 공동존 김포; polestar_cm_gp"
    source: str = ""          # "polestar_db" | "cache" | "event"(조회 실패·존 라벨만)
    ambiguous: bool = False   # 동일 hostname server.Server 행 2건 이상 → 승격 생략

    def to_dict(self) -> dict:
        """SSE/JSON 직렬화용 dict."""
        return asdict(self)


@dataclass
class AlarmEvent:
    """폴스타 알람 이벤트.

    폴스타 단일행 JSON 템플릿 변수와 1:1 대응하도록 설계한다.
    Redis Stream 'alarm:raw'에서 소비된 JSON 페이로드를 변환하여 생성한다.

    폴스타 등록 템플릿 형식:
        {"dbId":"<상수>","serverName":"${platformName}","hostname":"${hostname}",
         "ipAddress":"${ipAddress}","resourceAncestry":"${resourceAncestry}",
         "alarmId":"${alarmId}","severity":"${severity}","alarmStatus":"${alarmStatus}",
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
                                        #   폴스타 원문은 한글 라벨(해제/주의/경고/심각)로 도착한다(D-175)
                                        #   → domain/severity.parse_severity로 정규화한 뒤 담는다
    alarm_status: str                   # ${alarmStatus}   — 폴스타 UI 인지(ACK) 상태 (NOT_ACK 등).
                                        #                    해소 여부와 무관하므로 판정에 사용하지 않음 (Plan 47 §9)
    resource_type: str                  # ${resourceType}  — 'server.Server' 등
    resource_name: str                  # ${resourceName}  — 자원 이름
    alarm_name: str                     # ${alarmName}     — 알람 이름
    alarm_time: datetime                # ${formatAlarmDate('yyyyMMddHHmmss')} 파싱
    conditions: str                     # ${conditions}    — 발생/해소 임계 조건 정의
    condition_log: str                  # ${conditionLog}  — 이 알람이 울린 실제 값
    # --- 파생 필드 ---
    is_clear: bool = False              # severity == 0 단독 기준 (alarmStatus는 ACK 상태로 무관)
    raw_payload: dict = field(default_factory=dict)  # 원본 JSON dict 보존
    # (D-179) hostname 역조회로 부착되는 서버 식별 정보 — application/server_identity가 채운다.
    server_identity: Optional[ServerIdentity] = None
    # (D-179 부기) 워커/API가 이벤트를 구성한 시각 — UI '수신' 표시·지연 진단용(폴스타 alarm_time과 대비)
    received_at: Optional[datetime] = None


@dataclass
class AlarmHistoryEntry:
    """폴스타 DB에서 조회된 과거 알람 1건."""

    alarm_id: str
    severity: int              # 발생/해소 구분 기준 (0=해소)
    alarm_status: str          # CURRENTALARMSTATUS 매핑 — ACK 상태(참고용), 통계 판정에 사용하지 않음
    resource_name: str
    alarm_time: datetime       # CTIME


@dataclass
class AlarmHistoryStats:
    """alarm_context_enricher가 계산한 이력 통계 (LLM 프롬프트 주입용).

    발생 횟수는 다중 윈도우(24h/7일/30일/전체)로 산출한다 (Plan 47 §3.2).
    모든 시간 윈도우는 event.alarm_time을 기준 시각으로 계산한다 (처리 시점 now 기준 아님).
    """

    total_count: int                      # lookback(기본 90일) 전체 발생 건수 (해소 제외, 현재 이벤트 제외)
    count_24h: int
    count_7d: int
    count_30d: int
    same_resource_count: int              # 동일 resource_name 발생 건수 (전체 기간)
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]         # 직전 발생 시각 (현재 이벤트 제외)
    hour_histogram: dict[int, int]        # 시간대(0~23)별 발생 분포 — 최근 30일 한정
    median_interval_minutes: Optional[float]   # 발생 간격 중앙값 — 전체 기간 (주·월 주기 감지용)
    interval_cv: Optional[float]          # 간격 변동계수 (주기성 지표)
    period_label: str                     # "일 주기"|"주 주기"|"월 주기"|"기타 주기"|"" (주기적 판정 시 부여)
    truncated: bool                       # max_rows 도달로 이력 일부만 반영됨
    pre_classification: str               # "첫 발생"|"주기적"|"급증"|"산발적"
    source: str                           # "polestar_db" | "cache" (테스트 경로는 "simulated")


@dataclass
class ProcessInfo:
    """프로세스 1건 (마스킹·정규화 완료 — Plan 47-1).

    폴스타 실시간 프로세스 API 응답의 단일 프로세스를 선별·마스킹·정규화한 값.
    args는 반드시 mask_args()로 민감정보(비밀번호·토큰·접속문자열)를 제거한 값만 보관한다.
    """

    name: str
    pid: int
    ppid: int
    user: str
    p100cpu: float       # 100% 기준 CPU% (표시·랭킹 기본)
    pcpu: float          # 코어 합산 CPU%
    pmem: float          # 물리 메모리 %
    rss: int             # resident set size (bytes, 0 허용)
    args: str            # 마스킹·절단된 실행 인자


@dataclass
class ProcessSnapshot:
    """알람 시점 영향 프로세스 스냅샷 (Plan 47-1).

    CPU/메모리 발생 알람에 한해 폴스타 실시간 프로세스 API를 hostname으로 조회하여
    해당 지표(cpu=p100cpu, memory=pmem) 내림차순 상위 N개를 결정적으로 선별한 결과.
    """

    alarm_kind: str                   # "cpu" | "memory"
    captured_at: Optional[datetime]   # 응답 date (스냅샷 시각)
    top: list["ProcessInfo"]          # 해당 지표 내림차순 상위 N
    total_count: int                  # 조회된 전체 프로세스 수
    source_host: str                  # 조회에 사용한 hostname


@dataclass
class MessageEnrichment:
    """메시지 기반 L1 컨텍스트 보강 블록 (Plan 60 E6 §16).

    통보 대상(생존) 알람의 kind별 L1 컨텍스트를 통보 본문에 **별도 첨부**하기 위한
    서술 블록이다. cpu/memory는 기존 ProcessSnapshot("영향 프로세스" 표)로 처리하므로
    이 블록은 disk/network/process/log 에만 생성된다(§16.2).

    - title/signals: enrichment_profile(순수 도메인)이 kind로 산출한 사람이 읽는 요지.
    - snapshot: 데이터 소스가 확정된 kind(disk/network)에 한해 host-wide 프로세스
      스냅샷을 참고로 첨부한다(list_by_hostname 재사용, 신규 SQL 없음). 소스 미확정
      kind(process/log) 또는 수집 실패 시 None(요지 제목만 첨부 — graceful).
    """

    kind: str                              # "disk" | "network" | "process" | "log"
    title: str                             # 프로파일 요지 제목 (사람이 읽는 한국어)
    signals: tuple[str, ...]               # 서술하는 L1 신호 라벨
    snapshot: Optional["ProcessSnapshot"] = None   # host-wide 참고 스냅샷 (disk/network)


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
    # --- Plan 47: 이력 기반 패턴 분석 ---
    pattern_type: str = ""                # "첫 발생"|"주기적"|"급증"|"산발적"|"" (이력 분석 불가 시 빈 값)
    is_routine: Optional[bool] = None     # True=일상적 반복 알람, None=판단 불가
    pattern_analysis: str = ""            # LLM 패턴 해석 (1~3문장)
    # (Plan 83 T6) 결정적 사전분류 — 피드백 저장·조회 키의 단일 원천.
    # pattern_type(LLM 산출)과 값 도메인은 같지만 어긋날 수 있어 분리한다.
    pre_classification: str = ""          # "첫 발생"|"주기적"|"급증"|"산발적"|""
    # --- Plan 52 E3: AI 메시지 심각도 보강 (상향 전용) ---
    ai_message_severity: Optional[int] = None   # E3 AI 상향 심각도(상향 전용, 미상향이면 None)
    ai_severity_reason: str = ""                # 상향 근거(시그니처/문구 인용)
    # --- Plan 52 E4: LLM 액션가능성 판단(피드백 few-shot 보조) ---
    llm_actionability: Optional[str] = None   # "actionable" | "noise" | None(판단 없음/비활성)
    actionability_reason: str = ""            # LLM 자문 근거(1문장) 또는 ""
