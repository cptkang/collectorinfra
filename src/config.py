"""설정 로드 모듈.

환경변수에서 애플리케이션 설정을 읽어온다.
pydantic-settings를 사용하여 타입 안전한 설정 관리를 제공한다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal, Optional

from pydantic import AliasChoices, Field, PrivateAttr, SecretStr
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
    # SSL 인증서 검증 여부(D-060). 목적지 vLLM이 443을 listen하되 유효 인증서를 쓰지 않는
    # 폐쇄망 환경에서 False로 두면 health check·tool-calling 요청의 SSL 검증을 건너뛴다.
    # 미설정 시 True(안전 기본값) — `.env`에 ORCHESTRATOR_VERIFY_SSL=false로 비활성화.
    verify_ssl: bool = True
    # 제어 평면 그래프 순회 상한(LangGraph recursion_limit). 미지정 시 LangGraph 기본값 25에
    # 암묵 의존하므로 명시 노브로 노출한다(Plan 67 Phase 0 ③ — 기본값은 동작 불변).
    # 도구 호출 단계가 많은 복합 질의에서 GraphRecursionError가 나면 .env로 상향한다.
    recursion_limit: int = 25

    # ── Plan 50 / D-040 (B6): 제어 평면 컨텍스트 예산 노브 ──
    # 모델 교체(예: 9B → 대형) 시 코드 수정 없이 .env(ORCHESTRATOR_*)만 조정하면 예산이 확장된다.
    # 단순 int/float이므로 .env JSON 파싱 이슈 없음(Known Mistakes 2026-03-23).
    # ※ 미소비(미배선) 주의 — 이 블록에서 실제로 읽히는 값은 max_tool_result_tokens 하나뿐이다
    #   (deepagents_tools._max_chars). 나머지 3개는 소비 지점이 없다(Plan 67 Phase 0 ⑦ 실측).
    #   삭제하지 않고 유지한다 — 설정 웹UI 카탈로그가 "미소비" 필드로 노출하는 대상이기 때문
    #   (settings_catalog.UNCONSUMED_KEYS / D-129, 2026-07-29 확정). 단 max_history_turns는
    #   아직 그 목록에 없어 UI가 "소비 중"으로 표시한다(카탈로그 누락 — 별건).
    #   배선하거나 삭제할 때는 카탈로그 등재도 함께 갱신한다.
    # 제어 평면 입력 토큰 안전 상한. 서버 max_model_len(=16384 상향 진행) − 출력 여유(~4000) = 12000.
    # 초과 예상 시 트리밍/요약/강등(B2) 트리거. 모델 교체 시 서버 max_model_len 상향과 함께 올린다.
    max_input_tokens: int = 12000        # 미소비
    # 상한 대비 트리밍 시작 임계 비율(80% 도달 시 오래된 도구 결과 쌍을 요약). 보통 유지.
    context_budget_ratio: float = 0.8    # 미소비
    # 제어 평면으로 반환하는 도구 결과 1건 요약 상한(B1). 원본은 collector에만 보관한다.
    # 모델 교체로 컨텍스트가 커지면 상향 가능.
    max_tool_result_tokens: int = 2000
    # 제어 평면에 유지할 멀티턴 압축 맥락 턴 수(B3). 데이터 평면 MAX_HISTORY_TURNS=10과 별도.
    # 모델 교체로 컨텍스트가 커지면 상향 가능.
    max_history_turns: int = 6           # 미소비

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
    bearer_token: str = ""                           # 전송 인증 Bearer 토큰 (DBHUB_BEARER_TOKEN, 빈 값이면 무헤더 — 서버 무인증 전제)

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


class SynonymMatchConfig(BaseSettings):
    """동의어 매칭 보강 설정 (Plan 61 트랙 B / D-075).

    전 기능 기본 OFF — 활성 전에는 기존 정확일치 매칭 경로가 바이트 단위로
    무변경이어야 한다(회귀 0). 접근 경로: cfg.synonym.* (env_prefix="SYNONYM_").
    """

    fuzzy_match: bool = False            # E5-1 유연 매칭(자모·편집거리·부분어) on/off
    value_retrieval: bool = False        # E5-2 실측 값 검색·주입 on/off
    # E5-4 임베딩 의미 검색(D-084): 정확→퍼지(E5-1)→임베딩 계단의 마지막 단. ON이어도
    # 백엔드 요건(아래) 미충족·의존성(pip install .[semantic]) 미반입이면 경고 1회 후 무매칭.
    semantic_match: bool = False
    # 임베딩 백엔드: local(기본 — sentence-transformers 모델 인프로세스 CPU 상주) |
    # vllm(별도 vLLM 서버에 임베딩 모델을 로딩해 OpenAI 호환 /v1/embeddings로 호출)
    semantic_backend: Literal["local", "vllm"] = "local"
    # local 백엔드: 오프라인 반입한 sentence-transformers 다국어 모델 경로. 빈 값 = 미가용.
    semantic_model_path: str = ""
    # vllm 백엔드: vLLM /v1 엔드포인트(예: http://vllm-host:8000/v1)와 서빙 임베딩 모델명.
    # 둘 중 하나라도 빈 값이면 미가용.
    semantic_vllm_base_url: str = ""
    semantic_vllm_model: str = ""
    # vllm 백엔드 SSL 인증서 검증(D-060 계열 — 사설 인증서 vLLM 대응 시 false)
    semantic_vllm_verify_ssl: bool = True
    # 임베딩 코사인 확정 임계(미만은 확정 매핑 아닌 후보 제시/LLM 위임)
    semantic_confidence_min: float = 0.65
    match_confidence_min: float = 0.85   # 퍼지 매칭 신뢰도 임계(이하는 확정 매핑 아닌 후보 제시)
    # E5-3: D-051 유사어 보완 테이블 상한(기존 schema_analyzer 모듈 상수 하드코딩을 config로 노출).
    # 낮추면 토큰↓·리콜↓ — 고정 규칙이 아니라 E1 하네스로 튜닝하는 파라미터(Death of Schema Linking).
    max_synonym_supplement_tables: int = 15
    # E5-3 사전 위생·거버넌스(D-075): 유사어 메타(등록출처·사용횟수·최종사용일·신뢰도) 추적 +
    # 동일 용어 다중 컬럼 충돌 우선순위 규칙. 기본 OFF — 활성 전 유사어 저장/매칭 경로 무변경(회귀 0).
    governance: bool = False
    # 장기 미사용 감쇠/정리 임계(일). governance ON + 명시적 prune 호출 시에만 적용.
    decay_days: int = 180

    model_config = {"env_prefix": "SYNONYM_", "env_file": ".env", "extra": "ignore"}


class Text2SQLConfig(BaseSettings):
    """Text-to-SQL 결정적 조합 설정 (Plan 61 트랙 C / D-076).

    전 기능 기본 OFF — 활성 전에는 기존 SQL 생성 경로가 바이트 단위로 무변경이어야
    한다(회귀 0). 접근 경로: cfg.text2sql.* (env_prefix="TEXT2SQL_").

    트랙 A(다중 후보 E2~E4) 착수(Plan 61 D-073/D-074)로 semantic_fallback 기본값을
    `candidate_then_human`으로 전환했다(계획 §5·§9-9). multi_candidate가 OFF이면
    candidate_then_human/human은 현행 LLM 자유생성으로 우아하게 강등한다(회귀 0 — 커버리지
    밖 라우팅은 semantic_compose가 ON일 때만 발동하므로 기본 OFF 상태 경로는 무변경).
    """

    semantic_compose: bool = False       # E6 결정적 조합 경로 전체 스위치
    # 커버리지 밖 라우팅(3단 폴백, §9-9). multi_candidate OFF이면 candidate_then_human/human은
    # 현행 LLM 자유생성으로 강등(회귀 0).
    semantic_fallback: Literal["candidate_then_human", "llm", "human"] = "candidate_then_human"
    fallback_confidence_min: float = 0.0  # 3단 폴백 2차 게이트(트랙 A 선택 신뢰도 임계, 미달 시 사람검토 강등)

    # === 트랙 A: 다중 후보 생성·선택 (E2~E4, D-073/D-074) — 기본 OFF, 옵트인 증분 ===
    multi_candidate: bool = False        # E2/E4 다중 후보 경로 전체 스위치
    candidate_count: int = 3             # 후보 수 N (비용 무제한 §9-4 — E1로 이득 곡선 측정 후 상향)
    # 다양화 방식. multi_prompt(현행/divide&conquer/실행계획 CoT — §9-2 사용자 결정) | temperature(보조)
    candidate_strategies: Literal["temperature", "multi_prompt"] = "multi_prompt"
    complexity_gate: bool = False        # E3 복잡 질의만 다중 후보(품질 목적 — 비용 컷 아님 §9-4)
    # E4 선택 전략. hybrid(결과일관성 1차 + LLM 쌍대비교 병용 — §9-3) | consistency | llm
    selection: Literal["consistency", "llm", "hybrid"] = "hybrid"

    # === Plan 63 P3(D-090): 무선언(프로필/시맨틱 모델 없음) DB의 LLM 어휘 매핑 폴백 ===
    # 폴스타 등 프로필 보유 DB는 선언 우선(어댑터/프로필)으로 동작 불변. 프로필이 없는 DB는
    # 공통 LLM 경로가 SQL을 직접 생성한다(P4-2 검증). 이 플래그가 ON이면 무선언 DB의 기간
    # 표현에 **범용 기간 해석 힌트**(스키마의 시간 컬럼으로 매핑 — 폴스타 리터럴 없음)를 추가
    # 주입한다. 기본 OFF = 추가 주입 없음(호출 증가 0, 현행 동작 무변경).
    generic_llm_mapping: bool = False

    # === Plan 67 트랙 N / N2(D-133): 질의 이력 검색 기반 few-shot 동적 선택 ===
    # ON이면 폴백(LLM 1방) 프롬프트의 고정 few-shot(프로필 query_examples) 대신 검증된
    # 질의-SQL 이력에서 유사도 상위 예시를 골라 주입한다. 기본 OFF = 프롬프트 바이트 무변경.
    query_history_fewshot: bool = False
    query_history_top_k: int = 3           # 주입할 상위 예시 수(토큰 증가 상한)
    # 어휘·퍼지 유사도 확정 임계(미만은 무적중 → 기존 고정 few-shot 유지). 임베딩 승격은
    # IP-4 계측 후 별도 판단(계획서 §3.3-N2 "측정 선행").
    query_history_min_score: float = 0.35

    model_config = {"env_prefix": "TEXT2SQL_", "env_file": ".env", "extra": "ignore"}


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
    """운영자 인증 설정.

    D-071 하드닝: 기본 크레덴셜(admin/admin123)을 제거했다. 미설정 시 값이 비며,
    운영 모드(AUTH_ENABLED=true)에서는 기동 시 거부된다(server._validate_production_secrets).
    개발 모드에서는 jwt_secret이 비면 임시 랜덤 생성한다(재시작 간 불연속 감수).
    """

    username: str = ""
    password: str = ""
    jwt_secret: str = ""
    jwt_expire_hours: int = 24

    # jwt_secret이 .env/OS env에서 명시적으로 주입됐는지 여부(자동 생성과 구분).
    # os.getenv는 .env/.encenv 값을 못 봐(Known Mistakes 2026-06-10) 이 플래그로 판정한다.
    _jwt_secret_explicit: bool = PrivateAttr(default=False)

    model_config = {"env_prefix": "ADMIN_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """JWT 시크릿이 비어있으면(개발 모드) 자동 생성한다."""
        import secrets

        self._jwt_secret_explicit = bool(self.jwt_secret)
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_hex(32)


class AuthConfig(BaseSettings):
    """사용자 인증 설정.

    AUTH_ENABLED=false (기본값): 개발 단계에서 인증 없이 모든 기능 동작.
    AUTH_ENABLED=true: 사용자 로그인 필수.

    D-070 시크릿 분리: 사용자 토큰은 admin.jwt_secret이 아닌 auth.jwt_secret으로 서명·검증한다
    (운영자/사용자 토큰 교차 서명 차단).
    """

    enabled: bool = False
    auth_db_url: str = ""
    jwt_secret: str = ""
    jwt_expire_hours: int = 8
    max_login_attempts: int = 5
    lockout_minutes: int = 30
    password_min_length: int = 8
    default_allowed_db_ids: str = ""

    _jwt_secret_explicit: bool = PrivateAttr(default=False)

    model_config = {"env_prefix": "AUTH_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """JWT 시크릿이 비어있으면(개발 모드) 자동 생성한다."""
        import secrets

        self._jwt_secret_explicit = bool(self.jwt_secret)
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_hex(32)


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
    # 알람 분석 테스트 API(POST /alarm/test)에서 db_id를 생략했을 때 쓸 기본 인스턴스
    # 식별자. 라우트에 상수로 박혀 있던 값을 설정으로 옮긴 것이다(Plan 67 Phase 0 ⑫).
    default_test_db_id: str = "polestar_b0"

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
    # 운영 호스트는 코드에 두지 않는다 — `.env`의 ALARM_PROCESS_API_BASE_URLS_CSV로만 주입한다
    # (폴스타 편향 검토 §2-4 / Plan 67 Phase 0 ⑪). 미설정이면 base_url 조회가 None이라
    # 프로세스 보강이 대상 db_id에서 비활성된다.
    process_api_base_urls_csv: str = ""
    process_api_timeout_seconds: int = 3      # 추가 외부 호출 — 이력보다 짧게
    process_top_n: int = 5                     # 표시할 상위 프로세스 수
    # 인증·TLS 설정 없음 — 내부 시스템 http, 비로그인 조회 (Plan 47-1 §9)

    # Prometheus(node_exporter 메트릭) 조회 — 프로세스 API와 동일 db_id→base_url CSV 패턴.
    # node_exporter는 직접 조회하지 않고 앞단 Prometheus HTTP Query API를 read-only GET (D-003).
    # Plan 60 E3 baseline / Plan 64 §4.5 / docs/aiops_benchmark/l3_host_collection_mechanism.md.
    prometheus_enabled: bool = False           # 옵트인 — 비활성 시 조회 경로 미진입(회귀 0)
    prometheus_base_urls_csv: str = ""         # "db_id=http://prom:9090,..." (미설정 시 전부 None)
    prometheus_timeout_seconds: int = 3        # 추가 외부 호출 — 이력보다 짧게(프로세스 API와 동일)

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

    def get_prometheus_base_url(self, db_id: str) -> Optional[str]:
        """db_id(존)에 매핑된 Prometheus base_url을 반환한다 (없으면 None).

        매핑 형식: "db_id1=http://prom1:9090,db_id2=http://prom2:9090" (CSV, '=' 구분).
        get_process_api_base_url와 동일 규칙 — 존별 Prometheus 분리를 지원한다.
        """
        for pair in self.prometheus_base_urls_csv.split(","):
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
    # ── Plan 60 E4: 토폴로지 의존성 그래프 다홉 연쇄 억제 (D-080) ──
    # dependency_suppression과 AND — 다홉은 의존성 억제의 상위 모드(둘 다 True여야 다홉 조회 유발).
    # off(기본)면 게이트·enricher·repo 경로 비트 동일(회귀 0). gp/yd(PostgreSQL)만 지원, b0(DB2)는 1홉 폴백.
    multi_hop_cascade_enabled: bool = False   # (E4) 다홉 연쇄 억제 on/off
    topology_cache_ttl_seconds: int = 86400   # (E4) 정적 엣지 그래프 캐시 TTL(24h — 변경 드묾)
    topology_max_hops: int = 5                # (E4) BFS 홉 상한(순환 방어 이중 안전 겸 비용 가드)
    inhibition_enabled: bool = False          # (E2) 인히비션 on/off (상위 심각도 음소거)
    inhibition_window_seconds: int = 300      # (E2) 상위 심각도 활성 간주 창
    storm_grouping_enabled: bool = False      # (E2) 스톰 그룹핑 on/off (동일 서버 다발 억제)
    storm_window_seconds: int = 60            # (E2) 스톰 사건창 (초)
    storm_threshold: int = 5                  # (E2) 창 내 이 수 초과 시 스톰(대표 외 억제)
    # ── Plan 60 E2: 크로스-호스트 이벤트 상관 (D-078) — db_id(존) 경계 내 상관(§8 B-6) ──
    # 존 간(gp↔yd) 상관은 금지(공통 원인 실증 후 확장). storm(동일 서버)과 독립 병존 플래그.
    # off(기본)면 detection 미수행 → _detect_storm 비트 동일·게이트 무변경(회귀 0).
    cross_host_correlation_enabled: bool = False  # (E2) 크로스-호스트 상관 on/off
    correlation_sim_threshold: float = 0.5    # (E2) Jaccard 필드유사도 임계(이상이면 동일 클러스터)
    correlation_window_seconds: int = 120     # (E2) 상관 사건창(초) — 밖 클러스터 만료 sweep
    correlation_min_cluster_size: int = 2     # (E2) 대표 포함 이 수 이상 멤버부터 억제 개시
    correlation_buffer_max: int = 1000        # (E2) 스코프별 활성 클러스터 상한(메모리 가드)
    correlation_field_weights_csv: str = ""   # (E2) 필드 가중(예약 — 위상 가중은 E4 이후 단계적)
    # ── Plan 60 E2 위상 가중(E4 토폴로지 인접성) — 옵트인 하위 플래그 ──
    # cross_host_correlation_enabled의 하위 정밀화. off(기본)면 adjacent 미주입·topo_weight=0.0
    # → match_cluster 점수가 현행 필드 Jaccard와 **비트동일**(회귀 0). on이면 워커가 event.db_id로
    # 그래프를 로드해 인접 클러스터에 보너스 주입(존 내 매칭 정밀화). correlation.py는 topology 미의존.
    correlation_topology_weight_enabled: bool = False  # (E2) 위상 가중 on/off
    correlation_topology_weight: float = 0.2  # (E2) 인접 토폴로지 노드 유사도 보너스 크기
    business_hours_csv: str = ""              # (E3) 업무시간 (시간대 강등용)
    repeat_interval_seconds: int = 14400      # 재발생 재통보 간격 (4h, E1 dedup TTL)
    sev3_repeat_interval_seconds: int = 14400  # (§6.1) 심각도3 재통보 간격(기본=공통, 운영서 단축)
    recurrence_audit_every_n: int = 1         # (Plan 60 E1) 재발생 억제 감사 적재 샘플링(1=매번, count%N==0만 적재)
    noise_context_timeout_seconds: float = 3.0
    noise_context_cache_ttl_seconds: int = 300
    meta_alert_suppress_ratio: float = 0.9    # (E3) 억제율 이 값 초과 시 메타경보
    meta_alert_window_seconds: int = 3600     # (E3) 메타경보·운영지표 집계 창 (1h)
    meta_alert_min_events: int = 1            # (E3) 창 내 이벤트 수가 이 값 미만이면 무수신 메타경보
    enable_ai_severity_boost: bool = False    # (E3) AI 메시지 심각도 보강 (상향 전용)
    ai_severity_escalate_only: bool = True    # (E3) True=상향만 (하향 억제 금지) — 안전 고정 권장
    # ── Plan 60 E3: 동적 baseline 이상탐지 (D-079) — 순수 Python Holt-Winters(stdlib only) ──
    # baseline 이탈(잔차 z-score)을 ai_message_severity **상향 후보**로 공급한다(게이트 무변경).
    # 기존 enable_ai_severity_boost와 **AND 조건**(둘 다 True여야 analyzer 후처리가 상향 반영).
    # off(기본)면 enricher gather 태스크·키셋 불변·analyzer 무변경(회귀 0). CPU·메모리만 1차 범위.
    dynamic_baseline_enabled: bool = False    # (E3) 동적 baseline 이상탐지 on/off
    anomaly_z_high: float = 3.0               # (E3) 잔차 z-score 상향 임계(이상이면 상향 후보)
    anomaly_min_periods: int = 3              # (E3) 적합 최소 주기 수(미만이면 계산 skip→None)
    anomaly_baseline_cache_ttl_seconds: int = 3600  # (E3) baseline 파라미터 Redis 캐시 TTL
    anomaly_stl_enabled: bool = False         # (E3 2차) STL 분해 이상탐지, statsmodels optional·폴백 HW
    # ── Plan 60 B-7 로컬 임베딩(§15.3 L-2/L-4) — §15.4 D-035 경계: 주석 전용·판정 불변·폐쇄망 local-only ──
    # 임베딩은 관측성·주석·설명 전용이며 결정적 게이트 판정(SUPPRESS/PAGE)·억제 지문(compute_fingerprint)을
    # 절대 바꾸지 않는다. 전부 옵트인(기본 off). embedding_model_path는 폐쇄망 오프라인 반입 모델의 로컬
    # 디렉토리 경로여야 하며(미설정·비디렉토리=hub 이름→inert·런타임 다운로드 금지), 미가용 시 호출부는
    # 임베딩 주석을 건너뛰고 기존 경로가 비트 동일하게 동작한다(회귀 0).
    semantic_dedup_annotation_enabled: bool = False   # (B-7 L-2) 의미적 근접중복 주석 on/off
    topology_text_fusion_enabled: bool = False        # (B-7 L-4) 토폴로지+텍스트 융합 주석 on/off
    embedding_model_path: str = ""            # (B-7) 로컬 모델 디렉토리(미설정/비디렉토리→inert·다운로드 금지)
    embedding_similarity_threshold: float = 0.87  # (B-7 L-2·D-114) 근접중복 주석 임계 — 확정 모델 multilingual-e5-small 실측 분포(이질 max 0.852 < 0.87 < 근접 min 0.893) 기준. 모델 교체 시 재튜닝(bge-m3는 ~0.65).
    embedding_timeout_seconds: float = 2.0    # (B-7) 임베딩 hot-path 예산(초)
    enable_llm_actionability: bool = False    # (E4) LLM 피드백 few-shot 액션가능성 판단
    feedback_store_path: str = "logs/alarm_feedback.jsonl"   # (E4) 운영자 피드백 저장
    feedback_store_enabled: bool = True                      # (E4) 피드백 적재 on/off
    actionability_fewshot_count: int = 3                     # (E4) few-shot 예시 최대 개수
    # ── E5: deepagents Advisory Enricher (agentic 보조 분석기, §8.5/§8.7, D-048.7) ──
    # 전부 옵트인(기본 off) — enable_agentic_enricher=False면 E1~E4 배선·발송 판단 무변경.
    # 판단은 결정적 notification_policy가 하고, enricher는 signals(승격 전용)만 보강한다.
    enable_agentic_enricher: bool = False     # (E5) 보조 분석기 옵트인
    agentic_enricher_fallback: str = "semantic_routing"  # (E5) vLLM 미서빙 시: "semantic_routing" | "deterministic_only"
    agentic_enricher_timeout_seconds: float = 8.0  # (E5) enricher 전체 타임아웃(초, 초과 시 no-op)
    agentic_enricher_max_tool_calls: int = 5  # (E5) 트랙 B ReAct 루프 도구 호출 상한
    agentic_enricher_message_alarms_only: bool = True  # (E5) LogMonitor/보안/앱 로그 한정
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
    # ── D-049: ack/incident 라이프사이클 계측 (PostgreSQL 단일 저장소) ──
    # 기본 off면 incident 트래커 미기동 → /alarm/metrics는 기존 null 동작 유지(회귀 0).
    # 활성 시 워커는 incident 이벤트를 Redis로 발행, API 단일 라이터가 PG에 영속한다.
    incident_tracking_enabled: bool = False   # (D-049) incident 계측 on/off
    incident_event_channel: str = "alarm:incident"  # (D-049) incident 이벤트 Redis pub/sub 채널명
    # ── Plan 60 E6: 통보 컨텍스트 보강 — 메시지 기반 L1 조회·첨부 (D-105, Plan 64 §4.8 공유) ──
    # 기본 off면 신규 kind(disk/network/process/log) 보강 미수집·미첨부 → 통보 비트동일(회귀 0).
    # 기존 process_enrich_enabled(CPU/메모리) 경로와 독립 — cpu/메모리 표는 불변.
    message_enrichment_enabled: bool = False  # (E6) 메시지 기반 L1 보강 첨부 옵트인
    enrichment_min_tier: str = "PAGE"         # (E6) 이 티어 이상 통보 결정 시에만 보강 첨부
    enrichment_l1_timeout_seconds: float = 3.0  # (E6) L1 추가 조회(host-wide 프로세스) 상한
    enrichment_profile_map_csv: str = ""      # (E6) kind→요지 제목 오버라이드 ("disk=...,log=...")
    # ── Plan 60 E5: 변경/구성 이벤트 상관 (D-081 초안 → 착수 시 D-109 재부여) ──
    # 기본 off면 변경 피드 조회·오버레이 미수행 → noise_ctx 신규 키 None·게이트 step9 비트동일(회귀 0).
    # 억제가 아니라 승격 — 변경 근접 알람은 promote 신호로만 추가된다(원인성 판단·재현율 우선, §7.2).
    # 1차 소스 = 폴스타 cmm_resource_lifecycle_history(읽기전용, gp/yd PostgreSQL). b0(DB2)는 미조회.
    change_correlation_enabled: bool = False  # (E5) 변경 상관 on/off (변경 피드 조회·오버레이)
    change_window_seconds: int = 3600         # (E5) 알람 직전 이 창(초) 내 변경만 원인 후보로 오버레이
    # ── Plan 60 E7: 실측 ITSM 사례 기반 텍스트·주석 신호 보완 (D-116, §17) ──
    # 전부 옵트인(기본 off) → off면 현행 게이트·워커·파서 비트동일(회귀 0). 결정적 1차·LLM
    # annotate-only(기존 alarm_analyzer 재사용·신규 모델 반입 없음)·읽기전용·심각도3 단락 불변.
    # E7-a 주석 하베스팅(§17.3): dedup 억제 시 ACK 이전에 계획작업/해소/운영자접수 주석을 추출해
    #   record_recurrence(annotation=…) 최상위 필드로 원 인시던트에 보존(재통보 0·억제≠삭제 텍스트 확장).
    annotation_harvest_enabled: bool = False  # (E7-a) 주석 하베스팅 on/off (재발신 억제 시 주석 추출·감사)
    # E7-a 코로보레이션 게이팅(B-9): planned_work AND (resolution OR E2 클러스터 소속 OR E5 change_nearby)
    #   동시 충족 시에만 후속 동종 알람 DASHBOARD 강등(SUPPRESS 아님·E4 하이브리드 정합). 주석 단독은
    #   강등 없이 첨부만 — 텍스트 단독으로 억제강화 금지(재현율 우선). annotation_harvest_enabled와 독립.
    annotation_planned_suppress: bool = False  # (E7-a) 계획-무해 주석 DASHBOARD 강등 on/off (코로보레이션 게이팅)
    # E7-b 비알람 사전분류(§17.4): 알람 마커 부재 + 비알람 마커(승인/요청/바랍니다 등) 존재 시 SUPPRESS.
    #   애매하면 알람 간주(재현율 우선). 게이트 step0.5(step1 이전)에서 결정적 마커로 판정·감사.
    non_alarm_filter_enabled: bool = False    # (E7-b) 비알람(승인/안내성) 사전 억제 on/off
    # E7-c 파서 견고성(§17.5): 이질 포맷(호스트 접두 없음·네트워크 장비) graceful 폴백 — 침묵 드롭·크래시
    #   금지, 미식별 severity는 보수적 처리(드롭 방지). 알려진 포맷 파싱 결과는 비트동일(신규 폴백만 추가).
    format_tolerant_parsing_enabled: bool = False  # (E7-c) 이질 포맷 graceful 폴백 파싱 on/off
    # E7-d 사이트 상관 차원(§17.6): correlation.signature_tokens에 사이트/위치 토큰을 가중 차원으로 추가
    #   (워커가 site 토큰 산출·주입, domain은 값만 소비·순수성 유지). 존 경계(B-6) 불변. off면 extra=""로
    #   현행 필드 Jaccard와 비트동일. chattering(fleeting/repeating) 감사 라벨은 annotation_harvest_enabled 하.
    correlation_site_dimension_enabled: bool = False  # (E7-d) E2 사이트/위치 상관 차원 on/off
    # ── Plan 64 CW-A: 자동 조사 트리거 훅 (D-118 · sre_agent MCP submit/poll · Plan 60 §14.2) ──
    # 게이트가 tier ≥ investigation_trigger_min_tier(기본 PAGE)로 결정한 직후, notification_gate와
    # 분리된 트리거 노드가 sre_agent 조사 서비스에 비차단 submit → poll(전체 타임아웃 내) → 브리핑을
    # 통보에 첨부 + decision_store 감사한다. 게이트 판정·라우팅은 무변경(별도 노드·전체 타임아웃 가드).
    # 기본 off면 트리거 노드 미배선 → 게이트·통보 경로 비트동일(회귀 0)·브리핑 미생성. 서비스 다운/
    # 타임아웃/거부는 graceful 실패(통보·판정 정상 완료·사유 구조화 감사, 침묵 금지). 조사는 읽기전용·
    # 조치 없음(D-003) — 브리핑 수신·첨부만.
    investigation_trigger_enabled: bool = False   # (CW-A) 자동 조사 트리거 옵트인
    investigation_trigger_min_tier: str = "PAGE"  # (CW-A) 이 티어 이상 결정 시에만 트리거(enrichment_min_tier와 동일 규칙)
    investigation_service_url: str = "http://localhost:9098/sse"  # (CW-A) sre_agent 조사 서비스 MCP SSE 엔드포인트
    investigation_service_token: SecretStr = SecretStr("")  # (CW-A) 정적 Bearer 토큰(없으면 무헤더 — 3-D에서 강제)
    investigation_mcp_call_timeout_seconds: float = 10.0   # (CW-A) submit/poll 개별 호출 타임아웃(초)
    investigation_poll_interval_seconds: float = 1.0       # (CW-A) poll 재조회 간격(초)
    investigation_total_timeout_seconds: float = 45.0      # (CW-A) 전체(submit+poll) 타임아웃 가드(per-call 아님·§3.2)
    # ── Plan 64 CW-B: pull 위임 (deepagents/시멘틱 라우팅 fault_diagnosis 의도 → sre_diagnose) ──
    # 사용자가 "○○ 서버 원인 분석해줘"류로 장애 진단을 요청하면(pull), 시멘틱 라우터가
    # fault_diagnosis 의도로 분류하고 신규 노드가 sre_agent에 sre_diagnose(question, server?, host?,
    # db?)를 위임 → poll(전체 타임아웃 내) → 자연어 진단 응답을 반환한다(sre-agent/05 §3·§7).
    # 연결 설정은 CW-A의 investigation_service_url/token/타임아웃을 재사용한다(단일 서비스).
    # 기본 off면 라우터 프롬프트에 fault_diagnosis 미노출·노드 미배선 → 라우팅 비트동일(회귀 0).
    # 서비스 다운/타임아웃/거부는 graceful — 침묵 폴백 없이 사유를 담은 자연어 응답을 돌려준다(D-003 읽기전용).
    fault_diagnosis_enabled: bool = False   # (CW-B) 장애 진단 pull 위임 옵트인
    # ── Plan 64 CW-C: escalate-only 후속 통보 승격 (poll verdict.escalate 소비) ──
    # investigation_trigger의 poll 결과 구조화 verdict(ImportanceVerdict: level/confidence/escalate/
    # signals — sre-agent/02 §6)에서 escalate=True면, 통보에 "중요도 상향(자동 조사)" 안내 블록을
    # 첨부(승격)한다. **escalate-only** — 게이트 판정(tier/routing/decision)은 소급 변경·하향하지
    # 않고 상향 신호만 안내한다(Plan 64 §5.1 역방향 계약). 기본 off면 escalation 미생성 → 통보
    # 본문 비트동일(회귀 0). investigation_trigger_enabled가 켜져 poll이 돌 때만 verdict가 존재한다.
    fault_escalation_enabled: bool = False  # (CW-C) escalate-only 후속 통보 승격 옵트인

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

    # nested config는 반드시 default_factory로 선언한다 — 인스턴스 기본값(`= LLMConfig()`)은
    # 클래스 정의(모듈 임포트) 시점의 env로 고정되어, 이후 os.environ 변경 +
    # load_config.cache_clear() 재로드가 반영되지 않는다(2026-07-15 E1 하네스 A/B 무효화 실측).
    llm: LLMConfig = Field(default_factory=LLMConfig)
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    dbhub: DBHubConfig = Field(default_factory=DBHubConfig)
    query: QueryConfig = Field(default_factory=QueryConfig)
    synonym: SynonymMatchConfig = Field(default_factory=SynonymMatchConfig)   # Plan 61 트랙 B: 동의어 매칭 보강
    text2sql: Text2SQLConfig = Field(default_factory=Text2SQLConfig)          # Plan 61 트랙 C: 결정적 SQL 조합
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    admin: AdminConfig = Field(default_factory=AdminConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    multi_db: MultiDBConfig = Field(default_factory=MultiDBConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    schema_cache: SchemaCacheConfig = Field(default_factory=SchemaCacheConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    alarm: AlarmConfig = Field(default_factory=AlarmConfig)
    workb: WorkbConfig = Field(default_factory=WorkbConfig)
    noise_gate: NoiseGateConfig = Field(default_factory=NoiseGateConfig)  # Plan 52: 알람 노이즈 캔슬링 게이트 (형제 필드)
    checkpoint_backend: Literal["sqlite", "postgres"] = "sqlite"
    checkpoint_db_url: str = "checkpoints.db"

    # DB 직접 연결 설정 (DBHub 대안 / 레거시 단일 DB)
    db_backend: Literal["dbhub", "direct"] = "direct"
    db_connection_string: str = ""

    # 시멘틱 라우팅 활성화 여부
    # None(미입력) = 멀티 DB 환경이면 자동 활성화, 단일/레거시면 비활성
    # True/False = 명시적 강제(.env·OS env 모두 반영). 종전 os.getenv 판정은 `.env`의 false를
    # 무시해 ACTIVE_DB_IDS가 있으면 강제 활성화되는 버그가 있었다(Known Mistakes 2026-06-10).
    enable_semantic_routing: bool | None = None

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
        # 플래그 미입력(None)이면 멀티 DB 연결이 하나라도 설정된 경우 자동 활성화한다.
        # 명시적 true/false는 pydantic-settings가 .env·OS env에서 필드로 직접 읽어 존중한다
        # (enable_deepagent_orchestration과 동일 방식 — os.getenv 미사용).
        if self.enable_semantic_routing is None:
            self.enable_semantic_routing = bool(self.multi_db.get_active_db_ids())

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
