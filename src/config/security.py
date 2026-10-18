"""보안·인증·감사 설정 — SQL 보안·운영자/사용자 인증·감사 로그·호스트 인가.

plans/166 W2 I4 — 옛 `src/config.py`에서 클래스를 그대로 옮겼다. 공개 경로는 `src.config`
그대로다(패키지 `__init__`이 이 모듈의 클래스를 재노출한다 · D-337 ①).
"""

# 옮긴 주석·문자열의 긴 줄은 이동 전부터 있던 것이다 — 바이트 그대로 두려고 파일 단위로 끈다.
# ruff: noqa: E501

from __future__ import annotations

from pydantic import AliasChoices, Field, PrivateAttr
from pydantic_settings import BaseSettings


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

    # FabriX 개인정보(PII) 필터 차단 감지 로깅 on/off (SECURITY_PII_FILTER_LOG_ENABLED).
    # ON이면 FabriX가 프롬프트/응답을 PII로 차단할 때 어떤 유형·어떤 텍스트(마스킹)·filter_log_id를
    # 경고 1건으로 남긴다. 판정 규칙 변경은 src/security/pii_filter.py의 PII_RULES에서 한다
    # (근거: docs/pii_filtering_rules.md). 기본 ON(차단 원인 가시화용, 차단 시에만 발화).
    pii_filter_log_enabled: bool = True
    # 감지된 문자열을 로그에 **원문 그대로** 남길지 (SECURITY_PII_FILTER_LOG_UNMASK).
    # 기본 False = 마스킹(형태만 노출). True로 켜면 오탐(타임스탬프 등) 판정을 위해 실제 값을
    # 그대로 로그에 남긴다 — 로그에 실 개인정보가 남을 수 있으니 진단 시에만 한시적으로 켠다.
    pii_filter_log_unmask: bool = False
    # 프롬프트 주입 전 샘플 데이터(라이브 DB 행)의 PII를 마스킹 스크럽할지 (SECURITY_PII_SCRUB_SAMPLES).
    # 기본 True. schema 샘플(SELECT * ... LIMIT N)에 섞인 이메일·연락처·타임스탬프 등이
    # FabriX 개인정보 필터에 오탐 차단되는 것을 예방한다. 형식 보존 마스킹이라 컬럼·형식 추론
    # 신호는 유지된다(pii_filter.scrub_pii). False면 원문 그대로 주입(현행 동작).
    pii_scrub_samples: bool = True
    # 날짜·타임스탬프 무해화 스크럽 (SECURITY_PII_SCRUB_SUSPECT_DATES). 기본 False.
    # FabriX 계좌번호(851) 룰이 숫자 많은 라인의 날짜형(2026-06-17 02:30:45, DB2
    # 2026-08-05-14.30.45)까지 광폭 매칭하는 정황(D-155 후속3) — 진단으로 확정되면
    # 이 플래그만 켠다(코드 재배포 불요). 마스킹이 아니라 구분자 점 치환이라 값·자릿수
    # 형식 신호는 보존된다("2026-06-17 02:30:45" → "2026.06.17.02:30:45").
    pii_scrub_suspect_dates: bool = False
    # PII 필터 차단 시 전송 프롬프트·응답 전문을 파일로 덤프할지 (SECURITY_PII_BLOCK_DUMP_ENABLED).
    # 기본 True. 로컬 규칙 무매칭 차단(서버측 정책이 더 넓은 경우)은 "무엇이 걸렸는지"를
    # 로그 발췌만으로 특정할 수 없다(D-155 후속1) — 전송 원문 전체를 서버 로컬 파일로 남겨
    # 운영자가 직접 대조·이등분 재현으로 트리거를 확정한다. 덤프는 서버 밖으로 나가지 않는다
    # (FabriX로 이미 전송한 것과 동일한 텍스트). 경로: logs/pii_block/.
    pii_block_dump_enabled: bool = True

    model_config = {"env_prefix": "SECURITY_", "env_file": ".env", "extra": "ignore"}


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
    # 신규 가입자의 관측 소스(APM 등 DB 없는 소스) 초기 허용 목록 — 쉼표 구분 시스템 코드 ·
    # 빈 값이면 없음(plans/125 A-7 · D-272 ⑩). 기존 사용자(NULL)는 전체 허용 그대로다.
    default_allowed_sources: str = ""

    _jwt_secret_explicit: bool = PrivateAttr(default=False)

    model_config = {"env_prefix": "AUTH_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """JWT 시크릿이 비어있으면(개발 모드) 자동 생성한다."""
        import secrets

        self._jwt_secret_explicit = bool(self.jwt_secret)
        if not self.jwt_secret:
            self.jwt_secret = secrets.token_hex(32)


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


class HostAuthzConfig(BaseSettings):
    """호스트 인가 설정 (Plan 78 W3-5 · R-9 확정 2026-08-27).

    `admin_only` = admin 역할만 조사 진입 허용. **미설정·미상 값도 차단**(fail-closed) —
    판정은 `src/domain/host_authz.py`가 하고 여기서는 모드 문자열만 나른다.

    env_prefix를 두지 않는 이유: 78 §6.2가 확정한 환경변수명이 `HOST_AUTHZ_MODE`라
    접두 없는 단일 필드가 그대로 매핑된다.
    """

    mode: str = Field(default="admin_only", validation_alias=AliasChoices("HOST_AUTHZ_MODE"))

    model_config = {"env_file": ".env", "extra": "ignore"}
