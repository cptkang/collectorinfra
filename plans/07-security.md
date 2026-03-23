# 07. 보안: SQL 검증, 민감 데이터 마스킹, 감사 로그

> 기존 `src/security/` 분석 및 강화 계획

---

## 1. 현재 구현 분석

### 1.1 SQL Guard (src/security/sql_guard.py)

- `FORBIDDEN_SQL_KEYWORDS`: DML/DDL/DCL/관리 명령 30개 금지 키워드
- `INJECTION_PATTERNS`: 6개 인젝션 패턴 정규식
- `SQLGuard.detect_forbidden_keywords()`: 토큰화 후 금지 키워드 탐지
- `SQLGuard.detect_injection_patterns()`: 정규식 기반 인젝션 탐지
- `SQLGuard.is_safe_select()`: 종합 검사 (금지 키워드 + 인젝션 + 다중 SQL)

### 1.2 Data Masker (src/security/data_masker.py)

- 컬럼명 기반 마스킹: `password`, `secret`, `token`, `api_key` (부분 매칭 포함)
- 값 패턴 기반 마스킹: API 키 (`sk-`), Base64 시크릿, bcrypt 해시, JWT 토큰
- 설정 가능: `SecurityConfig.sensitive_columns`, `SecurityConfig.mask_pattern`

### 1.3 Audit Logger (src/security/audit_logger.py)

- JSONL 파일 기반 (`logs/audit.jsonl`)
- `log_query_execution()`: SQL, 결과 건수, 실행 시간, 성공/실패 기록
- `log_user_request()`: 사용자 질의, 출력 형식, 파일 여부 기록
- `structlog` 기반 구조화된 로깅
- `setup_logging()`: structlog 설정 (JSON 출력)

---

## 2. SQL 검증 강화

### 2.1 주석 내 키워드 오탐 해결

**현재 문제:** `query_generator`가 SQL에 주석(`-- 서버별 CPU 사용률을 조회합니다`)을 추가하도록 요구한다. 주석 내의 `DELETE`, `CREATE` 같은 일반 영어 단어가 금지 키워드로 오탐될 수 있다.

**해결:**

```python
# src/security/sql_guard.py (개선)

import sqlparse

class SQLGuard:
    def detect_forbidden_keywords(
        self,
        sql: str,
        forbidden: frozenset[str] | None = None,
    ) -> list[str]:
        """금지 키워드를 탐지한다. 주석을 제거한 후 검사한다."""
        if forbidden is None:
            forbidden = FORBIDDEN_SQL_KEYWORDS

        # 주석 제거
        sql_clean = sqlparse.format(sql, strip_comments=True)

        # 문자열 리터럴 내부도 제거 (안전을 위해)
        sql_clean = re.sub(r"'[^']*'", "''", sql_clean)

        tokens = re.findall(r'\b([A-Z_]+)\b', sql_clean.upper())
        return [t for t in tokens if t in forbidden]
```

### 2.2 인젝션 패턴 강화

**추가할 패턴:**

```python
INJECTION_PATTERNS: list[str] = [
    # 기존 패턴 유지
    r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE)",
    r"UNION\s+(ALL\s+)?SELECT",
    r"/\*.*?\*/",
    r"(xp_|sp_)\w+",
    r"INTO\s+(OUTFILE|DUMPFILE)",
    r"\bsys\.\w+",

    # [신규] 추가 패턴
    r"BENCHMARK\s*\(",            # MySQL 시간 기반 인젝션
    r"SLEEP\s*\(",                # 시간 지연 공격
    r"LOAD_FILE\s*\(",            # 파일 읽기 시도
    r"@@\w+",                     # 시스템 변수 접근
    r"INFORMATION_SCHEMA\.",      # 스키마 직접 접근 시도 (쿼리 생성기가 아닌 사용자 입력에서)
    r"CHAR\s*\(\s*\d+",          # 문자열 인코딩 우회
    r"CONCAT\s*\(.+SELECT",      # CONCAT으로 감싼 서브쿼리
]
```

### 2.3 다중 문장 검증 강화

현재 `is_safe_select()`에서 세미콜론으로 다중 SQL을 감지하지만, 문자열 리터럴 안의 세미콜론을 오탐할 수 있다:

```python
def is_safe_select(self, sql: str) -> tuple[bool, str]:
    # 기존 검사 유지

    # 다중 문장 검증 개선: sqlparse로 파싱하여 문장 수 확인
    statements = sqlparse.parse(sql)
    non_empty = [s for s in statements if s.get_type() is not None]
    if len(non_empty) > 1:
        return False, f"다중 SQL 문이 감지됨 ({len(non_empty)}개)"

    return True, "안전한 SELECT 문"
```

### 2.4 SQL 검증 레이어 구조 (이중 방어)

```
Layer 1: DBHub TOML 설정
  └── readonly = true (DB 레벨 읽기 전용)

Layer 2: query_validator 노드
  ├── sqlparse 기반 문법/타입 검증
  ├── SQLGuard.detect_forbidden_keywords() (주석 제거 후)
  ├── SQLGuard.detect_injection_patterns()
  ├── 참조 테이블/컬럼 존재 검증
  ├── LIMIT 절 자동 추가
  └── 성능 위험 패턴 경고

Layer 3: DB 레벨 제한
  └── max_rows, query_timeout 설정
```

---

## 3. 민감 데이터 마스킹 강화

### 3.1 추가 민감 컬럼 패턴

```python
# SecurityConfig 기본값 확장
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
```

### 3.2 추가 값 패턴

```python
class DataMasker:
    SENSITIVE_VALUE_PATTERNS: list[re.Pattern] = [
        # 기존 패턴 유지
        re.compile(r"^sk-[a-zA-Z0-9]{20,}$"),        # API 키
        re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$"),    # Base64 시크릿
        re.compile(r"^\$2[aby]\$\d{2}\$.{53}$"),       # bcrypt 해시
        re.compile(r"^eyJ[a-zA-Z0-9_-]+\."),           # JWT 토큰

        # [신규] 추가 패턴
        re.compile(r"^AKIA[0-9A-Z]{16}$"),             # AWS Access Key
        re.compile(r"^ghp_[a-zA-Z0-9]{36}$"),          # GitHub Personal Token
        re.compile(r"^glpat-[a-zA-Z0-9_-]{20,}$"),     # GitLab Token
        re.compile(r"^\d{3}-\d{2}-\d{4}$"),            # SSN (미국)
        re.compile(r"^\d{6}-\d{7}$"),                  # 주민번호 (한국)
        re.compile(r"^4[0-9]{12}(?:[0-9]{3})?$"),      # Visa 카드번호
        re.compile(r"^5[1-5][0-9]{14}$"),              # Mastercard
    ]
```

### 3.3 IP/이메일 부분 마스킹

특정 컬럼의 경우 전체 마스킹이 아닌 부분 마스킹이 필요할 수 있다:

```python
def _partial_mask_ip(self, ip: str) -> str:
    """IP 주소의 마지막 옥텟을 마스킹한다.
    예: 192.168.1.100 -> 192.168.1.***
    """
    parts = ip.split(".")
    if len(parts) == 4:
        parts[-1] = "***"
        return ".".join(parts)
    return self._mask

def _partial_mask_email(self, email: str) -> str:
    """이메일의 로컬 파트를 부분 마스킹한다.
    예: admin@company.com -> a***n@company.com
    """
    if "@" not in email:
        return self._mask
    local, domain = email.rsplit("@", 1)
    if len(local) <= 2:
        masked_local = "*" * len(local)
    else:
        masked_local = local[0] + "***" + local[-1]
    return f"{masked_local}@{domain}"
```

### 3.4 마스킹 전략 설정

```python
# src/config.py (확장)
class SecurityConfig(BaseSettings):
    sensitive_columns: list[str] = ["password", "secret", "token", "api_key"]
    mask_pattern: str = "***MASKED***"
    partial_mask_columns: list[str] = []  # [신규] 부분 마스킹 대상 컬럼
    mask_ip: bool = False                 # [신규] IP 마스킹 여부
    mask_email: bool = False              # [신규] 이메일 마스킹 여부
```

---

## 4. 감사 로그 강화

### 4.1 현재 구조 (Phase 1: 유지)

```
logs/audit.jsonl
├── {"timestamp": "...", "event": "user_request", "user_query": "...", ...}
├── {"timestamp": "...", "event": "query_execution", "sql": "...", "success": true, ...}
└── ...
```

### 4.2 감사 로그 필드 확장

```python
async def log_query_execution(
    sql: str,
    row_count: int,
    execution_time_ms: float,
    success: bool,
    error: Optional[str] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    # [신규] 추가 필드
    validation_warnings: Optional[list[str]] = None,  # SQL 검증 경고 목록
    retry_attempt: int = 0,                           # 재시도 횟수
    source_name: Optional[str] = None,                # DB 소스명
    masked_columns: Optional[list[str]] = None,       # 마스킹된 컬럼 목록
) -> None:
    """쿼리 실행을 감사 로그에 기록한다."""
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="query_execution",
        sql=sql,
        row_count=row_count,
        execution_time_ms=round(execution_time_ms, 2),
        success=success,
        error=error,
        user_id=user_id,
        thread_id=thread_id,
        validation_warnings=validation_warnings,
        retry_attempt=retry_attempt,
        source_name=source_name,
        masked_columns=masked_columns,
    )
    logger.info("query_executed", **entry.to_dict())
    await _write_audit_file(entry)
```

### 4.3 로그 로테이션

현재 `logs/audit.jsonl`에 무한정 추가된다. 로테이션을 추가한다:

```python
import os
from datetime import datetime

AUDIT_LOG_DIR = Path("logs")
MAX_LOG_SIZE_MB = 100


def _get_audit_log_path() -> Path:
    """날짜 기반 감사 로그 파일 경로를 반환한다."""
    today = datetime.now().strftime("%Y-%m-%d")
    return AUDIT_LOG_DIR / f"audit-{today}.jsonl"


async def _write_audit_file(entry: AuditEntry) -> None:
    """감사 로그를 날짜별 JSONL 파일에 추가한다."""
    try:
        log_path = _get_audit_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 파일 크기 체크 (선택적)
        if log_path.exists() and log_path.stat().st_size > MAX_LOG_SIZE_MB * 1024 * 1024:
            # 순번 추가
            counter = 1
            while True:
                rotated = log_path.with_suffix(f".{counter}.jsonl")
                if not rotated.exists():
                    log_path.rename(rotated)
                    break
                counter += 1

        with log_path.open("a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
    except Exception as e:
        logging.getLogger(__name__).error(f"감사 로그 파일 쓰기 실패: {e}")
```

### 4.4 Phase 3: DB 기반 감사 로그

```python
# 감사 로그 테이블 DDL
"""
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event VARCHAR(50) NOT NULL,
    user_id VARCHAR(100),
    thread_id VARCHAR(100),
    user_query TEXT,
    sql TEXT,
    row_count INTEGER,
    execution_time_ms FLOAT,
    success BOOLEAN,
    error TEXT,
    retry_attempt INTEGER DEFAULT 0,
    source_name VARCHAR(100),
    masked_columns TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_timestamp ON audit_log (timestamp);
CREATE INDEX idx_audit_log_user_id ON audit_log (user_id);
CREATE INDEX idx_audit_log_event ON audit_log (event);
"""


class DBAuditLogger:
    """Phase 3: DB 기반 감사 로그."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=3)

    async def log(self, entry: AuditEntry) -> None:
        if not self._pool:
            return
        data = entry.to_dict()
        await self._pool.execute(
            """
            INSERT INTO audit_log (
                timestamp, event, user_id, thread_id, user_query, sql,
                row_count, execution_time_ms, success, error,
                retry_attempt, source_name, masked_columns
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
            """,
            data.get("timestamp"),
            data.get("event"),
            data.get("user_id"),
            data.get("thread_id"),
            data.get("user_query"),
            data.get("sql"),
            data.get("row_count"),
            data.get("execution_time_ms"),
            data.get("success"),
            data.get("error"),
            data.get("retry_attempt", 0),
            data.get("source_name"),
            data.get("masked_columns"),
        )
```

---

## 5. structlog 설정 개선

### 5.1 현재 설정 분석

```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ],
    ...
)
```

### 5.2 개선: 환경별 포맷 분기

```python
def setup_logging(log_level: str = "INFO", env: str = "dev") -> None:
    """로깅을 설정한다.

    Args:
        log_level: 로그 레벨
        env: 환경 ("dev" | "prod")
    """
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if env == "prod":
        # 운영: JSON 포맷 (로그 수집 시스템 연동용)
        shared_processors.append(
            structlog.processors.JSONRenderer(ensure_ascii=False)
        )
    else:
        # 개발: 컬러 콘솔 포맷
        shared_processors.append(
            structlog.dev.ConsoleRenderer(colors=True)
        )

    structlog.configure(
        processors=shared_processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level),
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
```

---

## 6. Phase 3: 인증/인가

### 6.1 API 키 기반 인증 (Phase 3 초기)

```python
# src/api/dependencies.py

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader

API_KEY_HEADER = APIKeyHeader(name="X-API-Key")


async def verify_api_key(
    api_key: str = Security(API_KEY_HEADER),
) -> str:
    """API 키를 검증한다."""
    # Phase 3: API 키를 DB 또는 환경변수에서 검증
    valid_keys = set(os.getenv("API_KEYS", "").split(","))
    if api_key not in valid_keys:
        raise HTTPException(status_code=403, detail="유효하지 않은 API 키입니다.")
    return api_key
```

```python
# 라우트에 적용
@router.post("/query", dependencies=[Depends(verify_api_key)])
async def process_query(...):
    ...
```

---

## 7. 보안 체크리스트

### Phase 1 (현재 구현 + 보강)

- [x] DB 접근 읽기 전용 (DBHub TOML `readonly=true`)
- [x] SQL 검증 레이어 (query_validator + SQLGuard)
- [x] SELECT 외 SQL 차단 (금지 키워드 30개)
- [x] SQL 인젝션 패턴 탐지 (6개 패턴)
- [ ] **주석 내 키워드 오탐 해결** (sqlparse strip_comments)
- [ ] **인젝션 패턴 확장** (BENCHMARK, SLEEP 등 추가)
- [x] 민감 데이터 마스킹 (컬럼명 + 값 패턴)
- [ ] **마스킹 패턴 확장** (AWS 키, GitHub 토큰 등)
- [x] 감사 로그 기록 (JSONL 파일)
- [ ] **로그 로테이션** (날짜별 분리)
- [x] 연결 문자열 환경변수 관리

### Phase 3 (계획)

- [ ] 사용자 인증 체계 (API 키 / JWT)
- [ ] DB 기반 감사 로그
- [ ] RBAC (역할 기반 접근 제어)
- [ ] 감사 로그 조회 API
- [ ] 민감 데이터 정의 DB 관리 (동적 설정)

---

## 8. 기존 코드 대비 변경 사항 요약

| 항목 | 현재 | 변경 |
|------|------|------|
| 금지 키워드 검사 | 주석 포함하여 검사 (오탐 가능) | `sqlparse.format(strip_comments=True)` 후 검사 |
| 인젝션 패턴 | 6개 | 13개로 확장 (BENCHMARK, SLEEP, @@변수 등) |
| 다중 SQL 검증 | 세미콜론 기반 | `sqlparse.parse()` 문장 수 기반 |
| 민감 값 패턴 | 4개 (API 키, Base64, bcrypt, JWT) | 11개로 확장 (AWS, GitHub, 주민번호, 카드번호 등) |
| 민감 컬럼 | 4개 | 16개로 확장 |
| 감사 로그 필드 | 기본 7개 | validation_warnings, retry_attempt 등 추가 |
| 로그 파일 | 단일 `audit.jsonl` | 날짜별 `audit-YYYY-MM-DD.jsonl` + 로테이션 |
| structlog | JSON 전용 | 환경별 분기 (dev: 컬러 콘솔, prod: JSON) |
| 인증 | 미구현 | Phase 3에서 API 키 기반 인증 설계 포함 |
