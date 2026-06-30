"""설정 로드 모듈.

환경변수에서 애플리케이션 설정을 읽어온다.
pydantic-settings를 사용하여 타입 안전한 설정 관리를 제공한다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal, Optional

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class LLMConfig(BaseSettings):
    """LLM 관련 설정."""

    provider: Literal["ollama", "fabrix", "gemini"] = "ollama"
    model: str = "llama3.1:8b"

    # Ollama 설정
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_timeout: int = 180

    # Gemini 설정
    gemini_api_key: str = ""
    gemini_model: str = ""

    # FabriX 설정
    fabrix_base_url: str = ""
    fabrix_api_key: str = ""
    fabrix_client_key: str = ""
    fabrix_chat_model: str = ""

    model_config = {"env_prefix": "LLM_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """환경변수를 직접 읽어 보정한다."""
        import os

        # FabriX 환경변수 (LLM_ 접두사 또는 직접)
        if not self.fabrix_base_url:
            self.fabrix_base_url = os.getenv("FABRIX_BASE_URL", "")
        if not self.fabrix_api_key:
            self.fabrix_api_key = os.getenv("FABRIX_API_KEY", "")
        if not self.fabrix_client_key:
            self.fabrix_client_key = os.getenv("FABRIX_CLIENT_KEY", "")
        if not self.fabrix_chat_model:
            self.fabrix_chat_model = os.getenv("FABRIX_CHAT_MODEL", "")

        # Gemini API 키
        if not self.gemini_api_key:
            self.gemini_api_key = os.getenv("GOOGLE_API_KEY", "")

        # Ollama API 키 (게이트웨이용)
        if not self.ollama_api_key:
            self.ollama_api_key = os.getenv("LLM_API_KEY", "")


class OrchestratorConfig(BaseSettings):
    """deepagents 오케스트레이터(vLLM, OpenAI 호환) 설정 (Plan 49 / D-037, 트랙 B).

    vLLM이 tool-calling(제어 평면)을 담당하고, FabriX(LLMConfig)는 워커(실질 응답처리)를 담당한다.
    base_url 미설정 또는 vLLM 미서빙 시 트랙 B를 사용하지 않고 semantic_router로 회귀한다
    (가용성 분기 — deep_agent.select_orchestration_backend).
    """

    # provider: vllm(운영, OpenAI 호환) | gemini(테스트/PoC 전용 — egress 필요, Plan 49 §4.7)
    provider: Literal["vllm", "gemini"] = "vllm"
    base_url: str = ""            # vLLM /v1 엔드포인트 (예: http://vllm-host:8000/v1)
    model: str = "Qwen3.5-9B"     # vLLM 서빙 모델 (gemini 사용 시 gemini 모델명으로 설정)
    api_key: str = ""             # vLLM은 보통 불필요 / gemini는 미설정 시 LLM_GEMINI_API_KEY 폴백
    timeout: int = 120
    health_timeout: int = 3       # 가용성 health check 타임아웃(초, vLLM)

    # ── Plan 50 / D-040 (B6): 제어 평면 컨텍스트 예산 노브 ──
    # 모델 교체(예: 9B → 대형) 시 코드 수정 없이 .env(ORCHESTRATOR_*)만 조정하면 예산이 확장된다.
    # 단순 int/float이므로 .env JSON 파싱 이슈 없음(Known Mistakes 2026-03-23).
    # 제어 평면 입력 토큰 안전 상한. 서버 max_model_len(=16384 상향 진행) − 출력 여유(~4000) = 12000.
    # 초과 예상 시 트리밍/요약/강등(B2) 트리거. 모델 교체 시 서버 max_model_len 상향과 함께 올린다.
    max_input_tokens: int = 12000
    # 상한 대비 트리밍 시작 임계 비율(80% 도달 시 오래된 도구 결과 쌍을 요약). 보통 유지.
    context_budget_ratio: float = 0.8
    # 제어 평면으로 반환하는 도구 결과 1건 요약 상한(B1). 원본은 collector에만 보관한다.
    # 모델 교체로 컨텍스트가 커지면 상향 가능.
    max_tool_result_tokens: int = 2000
    # 제어 평면에 유지할 멀티턴 압축 맥락 턴 수(B3). 데이터 평면 MAX_HISTORY_TURNS=10과 별도.
    # 모델 교체로 컨텍스트가 커지면 상향 가능.
    max_history_turns: int = 6

    # ── Plan 50 / D-040 (B7): Qwen 계열 no-think(추론 비활성) 모드 ──
    # 제어 평면 추론 모드. Qwen 계열은 false(no-think) 권장 — 추론 토큰으로 인한 한계 압박과
    # tool_call JSON 파싱 불안정을 회피한다. 추론이 유리한 큰 모델로 교체 시 .env로 true 전환.
    enable_thinking: bool = False

    model_config = {
        "env_prefix": "ORCHESTRATOR_",
        "env_file": [".env", ".encenv"],
        "extra": "ignore",
    }


class DBHubConfig(BaseSettings):
    """MCP 서버 접속 설정.

    DB 연결 정보는 포함하지 않는다 (MCP 서버 VM이 관리).
    클라이언트는 서버 URL만 보유한다.
    """

    server_url: str = "http://localhost:9099/sse"   # MCP 서버 SSE 엔드포인트
    source_name: str = ""                              # 기본 쿼리 대상 소스 (DBHUB_SOURCE_NAME으로 설정)
    mcp_call_timeout: int = 60                       # MCP 호출 전체 대기시간 (초)

    model_config = {"env_prefix": "DBHUB_", "env_file": ".env", "extra": "ignore"}


class QueryConfig(BaseSettings):
    """클라이언트 측 쿼리 정책.

    DB 레벨 제한(query_timeout, max_rows)은 MCP 서버에서 관리한다.
    클라이언트는 재시도 횟수와 SQL 생성 기본 LIMIT만 관리한다.
    """

    max_retry_count: int = 3   # MCP 호출 재시도 횟수
    default_limit: int = 1000  # SQL 생성 시 기본 LIMIT

    # 데이터 충분성 검사 임계값 (0.0 ~ 1.0)
    sufficiency_required_threshold: float = 0.7   # hint/synonym 매핑
    sufficiency_optional_threshold: float = 0.5   # llm_inferred 매핑

    model_config = {"env_prefix": "QUERY_", "env_file": ".env", "extra": "ignore"}


class SecurityConfig(BaseSettings):
    """보안 관련 설정."""

    sensitive_columns: list[str] = [
        "password", "passwd", "pwd",
        "secret", "secret_key",
        "token", "access_token", "refresh_token",
        "api_key", "apikey",
        "private_key", "priv_key",
        "credential", "credentials",
        "ssn", "social_security",
        "credit_card", "card_number",
        "pin", "pin_code",
        "auth", "authorization",
    ]
    mask_pattern: str = "***MASKED***"
    partial_mask_columns: list[str] = []
    mask_ip: bool = False
    mask_email: bool = False

    model_config = {"env_prefix": "SECURITY_", "env_file": ".env", "extra": "ignore"}


class ServerConfig(BaseSettings):
    """API 서버 설정."""

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    query_timeout: int = 60
    file_query_timeout: int = 120

    model_config = {"env_prefix": "API_", "env_file": ".env", "extra": "ignore"}


class AdminConfig(BaseSettings):
    """운영자 인증 설정."""

    username: str = "admin"
    password: str = "admin123"
    jwt_secret: str = ""
    jwt_expire_hours: int = 24

    model_config = {"env_prefix": "ADMIN_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """JWT 시크릿이 비어있으면 자동 생성한다."""
        import secrets

        if not self.jwt_secret:
            self.jwt_secret = secrets.token_hex(32)


class AuthConfig(BaseSettings):
    """사용자 인증 설정.

    AUTH_ENABLED=false (기본값): 개발 단계에서 인증 없이 모든 기능 동작.
    AUTH_ENABLED=true: 사용자 로그인 필수.
    """

    enabled: bool = False
    auth_db_url: str = ""
    jwt_expire_hours: int = 8
    max_login_attempts: int = 5
    lockout_minutes: int = 30
    password_min_length: int = 8
    default_allowed_db_ids: str = ""

    model_config = {"env_prefix": "AUTH_", "env_file": [".env", ".encenv"], "extra": "ignore"}


class MultiDBConfig(BaseSettings):
    """멀티 DB 라우팅 설정.

    연결 문자열은 MCP 서버 VM이 관리한다.
    클라이언트는 활성 DB 목록만 관리하여 시멘틱 라우팅에 사용한다.
    활성 DB 목록은 MCP 서버의 list_sources 도구로 동적 조회하거나,
    환경변수 ACTIVE_DB_IDS로 명시적으로 설정할 수 있다.
    """

    # 활성 DB ID 목록 (쉼표 구분, 환경변수로 설정)
    # 예: ACTIVE_DB_IDS=polestar,cloud_portal,itsm,itam
    active_db_ids_csv: str = Field(
        default="",
        validation_alias=AliasChoices("ACTIVE_DB_IDS", "MULTI_DB_ACTIVE_DB_IDS_CSV"),
    )

    model_config = {
        "env_prefix": "MULTI_DB_",
        "env_file": ".env",
        "extra": "ignore",
        "populate_by_name": True,
    }

    def get_active_db_ids(self) -> list[str]:
        """활성 DB 식별자 목록을 반환한다.

        Returns:
            활성 DB 식별자 목록
        """
        if not self.active_db_ids_csv:
            return []
        return [
            db_id.strip()
            for db_id in self.active_db_ids_csv.split(",")
            if db_id.strip()
        ]


class RedisConfig(BaseSettings):
    """Redis 관련 설정."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    ssl: bool = False
    socket_timeout: int = 5

    model_config = {"env_prefix": "REDIS_", "env_file": [".env", ".encenv"], "extra": "ignore"}


class AuditConfig(BaseSettings):
    """감사 로그 설정."""

    jsonl_enabled: bool = True
    db_enabled: bool = True
    retention_days: int = 90
    sensitive_tables: list[str] = []
    alert_on_failed_login: int = 5
    alert_on_large_result: int = 5000
    night_alert_start: int = 2
    night_alert_end: int = 6

    model_config = {"env_prefix": "AUDIT_", "env_file": ".env", "extra": "ignore"}


class SchemaCacheConfig(BaseSettings):
    """스키마 캐시 관련 설정."""

    cache_dir: str = ".cache/schema"
    enabled: bool = True
    backend: str = "redis"  # "redis" | "file"
    auto_generate_descriptions: bool = True
    fingerprint_ttl_seconds: int = 1800  # fingerprint 검증 주기 (기본 30분)

    model_config = {"env_prefix": "SCHEMA_CACHE_", "env_file": ".env", "extra": "ignore"}


class AlarmConfig(BaseSettings):
    """에이전트 서버의 알람 분석·발송 설정.

    소켓 수신 설정은 alarm_server/config.py에서 관리.
    에이전트 서버는 Redis Stream 소비 및 알림 발송만 담당.
    """

    enabled: bool = False
    redis_stream_key: str = "alarm:raw"
    redis_consumer_group: str = "alarm-workers"
    min_severity: int = 2                 # 처리할 최소 심각도 (0=해소, 1=주의, 2=경고, 3=심각)
    dedup_ttl_seconds: int = 300          # 중복 알람 억제 TTL (초)
    # 현재 지원 채널: workb만 사용 가능.
    # 추후 "slack,workb" 등 복수 지정 가능하도록 CSV 구조를 유지한다.
    notification_channels_csv: str = "workb"
    # Generic Webhook 채널 설정 (비어있으면 webhook 채널 자동 무시)
    webhook_url: str = ""
    webhook_timeout_seconds: int = 10

    # ── Plan 47: 폴스타 DB 이력 기반 패턴 분석 ──
    history_enabled: bool = True              # 이력 조회 + 패턴 분석 활성화
    history_lookback_days: int = 90           # 패턴 분석 조회 기간 — 일·주·월 주기 3회 관측 가능한 최소 기간 (Plan 47 §3.2)
                                              # 월 주기 작업이 많은 환경은 180까지 확장 가능
    history_max_rows: int = 2000              # 조회 행 수 상한 (일 10건 빈발 알람 × 90일 = 900건 수용, truncated 플래그 연동)
    history_cache_ttl_seconds: int = 300      # 조회 결과 단기 캐시 TTL (0이면 캐시 비활성)
    enrich_timeout_seconds: int = 5           # enricher 전체 타임아웃
    burst_threshold_24h: int = 5              # 급증 판정 24h 최소 건수

    # ── Plan 47-1: 영향 프로세스 보강 (CPU/메모리 알람) ──
    process_enrich_enabled: bool = True
    # db_id=base_url 매핑 (CSV — .env JSON 회피, notification_channels_csv 패턴과 동일).
    # 내부망 시스템이라 scheme는 http:// (TLS 없음). 인증 불필요 (Plan 47-1 §2/§9).
    process_api_base_urls_csv: str = (
        "polestar_cm_gp=http://polestar.kbonecloud.com,"
        "polestar_cm_yd=http://yd-polestar.kbonecloud.com"
    )
    process_api_timeout_seconds: int = 3      # 추가 외부 호출 — 이력보다 짧게
    process_top_n: int = 5                     # 표시할 상위 프로세스 수
    # 인증·TLS 설정 없음 — 내부 시스템 http, 비로그인 조회 (Plan 47-1 §9)

    model_config = {"env_prefix": "ALARM_", "env_file": ".env", "extra": "ignore"}

    def get_notification_channels(self) -> list[str]:
        """활성 알림 채널 목록을 반환한다."""
        return [c.strip() for c in self.notification_channels_csv.split(",") if c.strip()]

    def get_process_api_base_url(self, db_id: str) -> Optional[str]:
        """db_id에 매핑된 프로세스 API base_url을 반환한다 (없으면 None — Plan 47-1).

        매핑 형식: "db_id1=http://host1,db_id2=http://host2" (CSV, '=' 구분).
        잘못된 항목(= 미포함)은 무시한다.
        """
        for pair in self.process_api_base_urls_csv.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            key, _, url = pair.partition("=")
            if key.strip() == db_id:
                return url.strip() or None
        return None


class WorkbConfig(BaseSettings):
    """KB One 클라우드 포탈 worKB(사내메신저) 쪽지 발송 설정.

    민감 정보(bearer_token)는 .encenv 파일에 저장 권장.
    """

    base_url: str = ""                    # 예: http://kbone-portal.internal:28080
    bearer_token: str = ""               # Bearer 인증 토큰 (.encenv 권장)
    system_div: str = ""                 # 시스템 구분자 (worKB 관리자로부터 발급)
    send_id: str = ""                    # 발송자 사번
    user_ids_csv: str = ""              # 기본 수신자 사번 목록 (쉼표 구분)
    alias: str = "[인프라알람]"          # 쪽지 제목 접두어 (실제 제목 = alias + " " + msgTitle)
    # 심각도별 수신자 오버라이드 (비어있으면 user_ids_csv 공통 사용)
    critical_user_ids_csv: str = ""      # 심각도 3 전용 수신자
    warning_user_ids_csv: str = ""       # 심각도 2 전용 수신자
    timeout_seconds: int = 10

    model_config = {"env_prefix": "WORKB_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def get_user_ids(self, severity: int) -> str:
        """심각도에 맞는 수신자 목록을 반환한다.

        Args:
            severity: 알람 심각도 (1=주의, 2=경고, 3=심각)

        Returns:
            쉼표 구분 수신자 사번 문자열
        """
        if severity == 3 and self.critical_user_ids_csv:
            return self.critical_user_ids_csv
        if severity == 2 and self.warning_user_ids_csv:
            return self.warning_user_ids_csv
        return self.user_ids_csv


class NoiseGateConfig(BaseSettings):
    """알람 노이즈 캔슬링 게이트 설정 (Plan 52 §8.5).

    AlarmConfig/WorkbConfig와 동일하게 AppConfig의 **형제 필드**로 분리한다.
    AlarmConfig 안에 중첩하지 않는다(이중 중첩 시 env 로딩이 복잡해지고 충돌 위험).
    접근 경로: cfg.noise_gate.* (env_prefix="NOISE_").

    전 기능 기본 비활성(enable_noise_gate=False) — 활성 전에는 기존 알람 발송 경로가
    바이트 단위로 무변경이어야 한다(회귀 0). E1에서 미사용인 필드(E2/E3)도 향후
    자리예약으로 정의해 둔다.
    """

    enable_noise_gate: bool = False           # 전체 게이트 옵트인 (E1)
    suppress_max_severity: int = 2            # 억제 허용 상한 (심각도 3은 항상 PAGE)
    importance_value_map_csv: str = ""        # IMPORTANCE_ID 코드→라벨 매핑 CSV ("1=낮음,2=보통,3=높음"), 빈값=전부 보통
    self_heal_window_seconds: int = 300       # 자가복구 상관 창 (E1)
    debounce_seconds: int = 0                 # (E2) 상태 안정화 (0=미사용)
    flap_high_threshold: float = 20.0         # (E2) 플래핑 시작 % (Nagios 기본)
    flap_low_threshold: float = 5.0           # (E2) 플래핑 종료 %
    flapping_enabled: bool = False            # (E2) 플래핑 억제 on/off (상태 진동 보류)
    dependency_suppression: bool = False      # (E2) 의존성 억제 on/off
    inhibition_enabled: bool = False          # (E2) 인히비션 on/off (상위 심각도 음소거)
    inhibition_window_seconds: int = 300      # (E2) 상위 심각도 활성 간주 창
    storm_grouping_enabled: bool = False      # (E2) 스톰 그룹핑 on/off (동일 서버 다발 억제)
    storm_window_seconds: int = 60            # (E2) 스톰 사건창 (초)
    storm_threshold: int = 5                  # (E2) 창 내 이 수 초과 시 스톰(대표 외 억제)
    business_hours_csv: str = ""              # (E3) 업무시간 (시간대 강등용)
    repeat_interval_seconds: int = 14400      # 재발생 재통보 간격 (4h, E1 dedup TTL)
    sev3_repeat_interval_seconds: int = 14400  # (§6.1) 심각도3 재통보 간격(기본=공통, 운영서 단축)
    noise_context_timeout_seconds: float = 3.0
    noise_context_cache_ttl_seconds: int = 300
    meta_alert_suppress_ratio: float = 0.9    # (E3) 억제율 이 값 초과 시 메타경보
    meta_alert_window_seconds: int = 3600     # (E3) 메타경보·운영지표 집계 창 (1h)
    meta_alert_min_events: int = 1            # (E3) 창 내 이벤트 수가 이 값 미만이면 무수신 메타경보
    enable_ai_severity_boost: bool = False    # (E3) AI 메시지 심각도 보강 (상향 전용)
    ai_severity_escalate_only: bool = True    # (E3) True=상향만 (하향 억제 금지) — 안전 고정 권장
    enable_llm_actionability: bool = False    # (E3) LLM 피드백 few-shot 액션가능성 판단
    resolved_to_dashboard: bool = False       # 독립 해소(severity 0)를 DASHBOARD로 표시할지 (E1)
    decision_store_path: str = "logs/alarm_decisions.jsonl"
    decision_store_enabled: bool = True
    ticket_batch_queue_path: str = "logs/alarm_ticket_queue.jsonl"   # (E3) TICKET 일배치 요약 큐
    ticket_batch_queue_enabled: bool = True   # (E3) TICKET 티어를 일배치 요약 큐에 적재할지
    # ── E3 후속: 워커→UI 실시간 SSE Redis pub/sub 브리지 (D-048.9 한계 해소) ──
    # 워커는 cross-process라 API의 in-memory alarm_bus를 공유 못 함 → Redis pub/sub로 중계.
    # 기본 off면 워커 경로 티어 SSE는 로그 폴백(E3 무변경, 회귀 0). 스칼라라 .env JSON 회피.
    sse_bridge_enabled: bool = False          # (E3 후속) 워커→UI 실시간 SSE 브리지 on/off
    sse_bridge_channel: str = "alarm:sse"     # (E3 후속) SSE 브리지 Redis pub/sub 채널명

    model_config = {"env_prefix": "NOISE_", "env_file": ".env", "extra": "ignore"}

    @property
    def importance_value_map(self) -> dict[str, str]:
        """importance_value_map_csv → {코드: 라벨} 파싱. 잘못된 항목은 무시한다.

        형식: "1=낮음,2=보통,3=높음" (CSV, '=' 구분). 빈 값이면 빈 dict.
        decide_notification이 map_importance에 넘기는 매핑이며, 미매핑 코드는
        정책 계층에서 '보통'으로 보수 처리된다(§6.3).
        """
        result: dict[str, str] = {}
        for pair in self.importance_value_map_csv.split(","):
            pair = pair.strip()
            if not pair or "=" not in pair:
                continue
            code, _, label = pair.partition("=")
            code = code.strip()
            label = label.strip()
            if code and label:
                result[code] = label
        return result


class AppConfig(BaseSettings):
    """애플리케이션 전체 설정을 통합 관리한다."""

    llm: LLMConfig = LLMConfig()
    orchestrator: OrchestratorConfig = OrchestratorConfig()
    dbhub: DBHubConfig = DBHubConfig()
    query: QueryConfig = QueryConfig()
    security: SecurityConfig = SecurityConfig()
    server: ServerConfig = ServerConfig()
    admin: AdminConfig = AdminConfig()
    auth: AuthConfig = AuthConfig()
    multi_db: MultiDBConfig = MultiDBConfig()
    redis: RedisConfig = RedisConfig()
    schema_cache: SchemaCacheConfig = SchemaCacheConfig()
    audit: AuditConfig = AuditConfig()
    alarm: AlarmConfig = AlarmConfig()
    workb: WorkbConfig = WorkbConfig()
    noise_gate: NoiseGateConfig = NoiseGateConfig()  # Plan 52: 알람 노이즈 캔슬링 게이트 (형제 필드)
    checkpoint_backend: Literal["sqlite", "postgres"] = "sqlite"
    checkpoint_db_url: str = "checkpoints.db"

    # DB 직접 연결 설정 (DBHub 대안 / 레거시 단일 DB)
    db_backend: Literal["dbhub", "direct"] = "direct"
    db_connection_string: str = ""

    # 시멘틱 라우팅 활성화 여부
    enable_semantic_routing: bool = False

    # deepagents 기반 의도 분해 오케스트레이션 활성화 여부 (Plan 48 / D-037)
    # None(미입력) = 멀티 DB 환경이면 신규 경로를 기본 활성화(신규 경로가 기본 동작), 단일/레거시면 비활성
    # True/False = 명시적 강제(.env·OS env 모두 반영). semantic_routing과 상호 배타 — 둘 다 활성이면 orchestration 우선 (graph.py 분기)
    enable_deepagent_orchestration: bool | None = None

    # 결과 기반 재계획 최대 반복 (무한 루프 방지, R-A3/R-11)
    max_replan: int = 3

    # Plan 49 / D-037 트랙 B: deepagents 실제 패키지(vLLM 오케스트레이터 + FabriX 워커) 활성화 여부.
    # 명시적 opt-in(기본 False) — vLLM 인프라가 필요하므로 자동 활성화하지 않는다.
    # True + vLLM 가용 시 트랙 B, 그 외(False/미서빙)는 semantic_router 사용
    # (가용성 분기 — deep_agent.select_orchestration_backend).
    enable_deepagents_package: bool = False

    # 테스트 전용 — deepagent(트랙 B) 경로 검증 시 워커(데이터 평면) LLM provider를 강제 교체.
    # 미설정(None)=운영 그대로(config.llm.provider, 보통 fabrix). "gemini" 지정 시 deepagent
    # 경로 전체(input_parser/field_mapper + deep_agent 워커)가 gemini로 동작 → FabriX 없이 검증.
    # 오케스트레이터(제어 평면)는 ORCHESTRATOR_PROVIDER로 별도 지정 (Plan 49 §4.7 / D-037).
    worker_provider_override: Literal["ollama", "fabrix", "gemini"] | None = None

    # Polestar 전용 프롬프트를 적용할 DB ID (콤마 구분으로 복수 지정 가능)
    # .env에서 POLESTAR_DB_IDS=polestar,polestar2 로 설정하면
    # active_db_id가 이 목록에 포함될 때 Polestar 전용 시스템 프롬프트를 사용한다.
    # 비어있으면 전용 프롬프트를 사용하지 않음 (범용 프롬프트 적용).
    polestar_db_ids: str = ""

    def get_polestar_db_ids(self) -> set[str]:
        """Polestar 전용 프롬프트를 적용할 DB ID 집합을 반환한다."""
        if not self.polestar_db_ids:
            return set()
        return {x.strip() for x in self.polestar_db_ids.split(",") if x.strip()}

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Phase 3: 멀티턴 대화 / Human-in-the-loop
    enable_sql_approval: bool = False         # SQL 승인 기능 활성화
    enable_structure_approval: bool = True    # 구조 분석 HITL 승인 (기본 활성화)
    conversation_max_turns: int = 20          # 대화 최대 턴 수
    conversation_ttl_hours: int = 24          # 대화 세션 유효 시간

    model_config = {"env_file": ".env", "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """시멘틱 라우팅 및 오케스트레이션 활성화를 자동 판단한다."""
        import os

        env_val = os.getenv("ENABLE_SEMANTIC_ROUTING", "")
        if env_val.lower() in ("true", "1", "yes"):
            self.enable_semantic_routing = True
        elif not env_val and self.multi_db.get_active_db_ids():
            # 멀티 DB 연결이 하나라도 설정되어 있으면 자동 활성화
            self.enable_semantic_routing = True

        # Plan 48 / D-037: 플래그 미입력(None)이면 멀티 DB 환경에서 신규 오케스트레이션 경로를
        # 기본 활성화한다(신규 경로가 기본 동작). 명시적 true/false는 pydantic-settings가 .env·OS env에서
        # 필드로 직접 읽어 그대로 존중한다(os.getenv 미사용 — .env-only 설정도 반영, Known Mistakes 2026-06-10).
        if self.enable_deepagent_orchestration is None:
            self.enable_deepagent_orchestration = bool(self.multi_db.get_active_db_ids())


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    """설정을 로드하여 반환한다.

    싱글톤 패턴으로 동일한 설정 인스턴스를 재사용한다.

    Returns:
        애플리케이션 설정
    """
    config = AppConfig()
    logger.info("애플리케이션 설정 로드 완료")
    return config
