# 40. 사용자 행위 감사 로깅 강화

> 누가 어떤 내용을 조회했는지 기록하고 관리하는 종합 감사 로깅 시스템을 구축한다.

---

## 1. 현재 상태 분석

### 1.1 기존 감사 로그 (`src/security/audit_logger.py`)

- **저장소**: JSONL 파일 (`logs/audit-YYYY-MM-DD.jsonl`)
- **기록 이벤트**:
  - `user_request`: 사용자 질의, 출력 형식, 파일 여부
  - `query_execution`: SQL, 결과 건수, 실행 시간, 성공/실패
- **사용자 식별**: `user_id` 필드 존재하지만 항상 `None` (인증 미구현)
- **로테이션**: 날짜별 분리 + 100MB 초과 시 순번 로테이션

### 1.2 부족한 점

1. **사용자 미식별**: 누가 조회했는지 알 수 없음 (Plan 39 구현 후 해결)
2. **이벤트 범위 부족**: 로그인/로그아웃, 파일 다운로드, 관리 작업 미기록
3. **조회 기능 없음**: 로그 파일을 직접 읽어야 함 → 관리자 API/UI 필요
4. **통계 없음**: 사용자별/기간별/DB별 사용 통계 미제공
5. **경보 없음**: 이상 행위 감지 기능 없음

---

## 2. 설계

### 2.1 감사 이벤트 확장

기존 2개 이벤트에서 10개 이벤트로 확장한다.

| 이벤트 | 설명 | 기록 시점 |
|--------|------|----------|
| `user_login` | 사용자 로그인 (성공/실패) | 로그인 API 호출 시 |
| `user_logout` | 사용자 로그아웃 | 로그아웃 API 호출 시 |
| `user_request` | 자연어 질의 요청 | 질의 API 호출 시 (기존) |
| `query_execution` | SQL 실행 | SQL 실행 완료 시 (기존) |
| `file_upload` | 양식 파일 업로드 | 파일 첨부 질의 시 |
| `file_download` | 결과 파일 다운로드 | 파일 다운로드 시 |
| `data_access` | 데이터 접근 요약 | 결과 반환 시 (조회 대상 테이블/건수) |
| `admin_action` | 관리자 작업 | 설정 변경, 사용자 관리 시 |
| `cache_operation` | 캐시 관리 | 캐시 생성/갱신/삭제 시 |
| `security_alert` | 보안 경고 | 금지 SQL 시도, 로그인 실패 반복 등 |

### 2.2 감사 로그 엔트리 구조

```python
# src/security/audit_logger.py (확장)

@dataclass
class AuditLogEntry:
    """확장된 감사 로그 엔트리."""

    # 공통 필드
    timestamp: str              # ISO 8601
    event: str                  # 이벤트 유형
    user_id: Optional[str]      # 사용자 ID
    username: Optional[str]     # 사용자 표시 이름
    department: Optional[str]   # 부서
    client_ip: Optional[str]    # 클라이언트 IP
    session_id: Optional[str]   # 세션/thread ID
    request_id: Optional[str]   # 요청 고유 ID (추적용)

    # 질의 관련
    user_query: Optional[str]           # 자연어 질의
    generated_sql: Optional[str]        # 생성된 SQL
    target_tables: Optional[list[str]]  # 접근한 테이블 목록
    target_db: Optional[str]            # 대상 DB
    row_count: Optional[int]            # 결과 행 수
    execution_time_ms: Optional[float]  # 실행 시간
    success: Optional[bool]             # 성공 여부
    error: Optional[str]                # 에러 메시지

    # 파일 관련
    file_name: Optional[str]            # 파일명
    file_type: Optional[str]            # 파일 유형
    file_size_bytes: Optional[int]      # 파일 크기

    # 보안 관련
    masked_columns: Optional[list[str]] # 마스킹된 컬럼
    security_flags: Optional[list[str]] # 보안 경고 플래그

    # 메타데이터
    extra: Optional[dict]               # 추가 정보
```

### 2.3 저장소 계층

```
┌─────────────────────────────────────┐
│         AuditService                │  ← 통합 인터페이스
├─────────────────────────────────────┤
│  ┌──────────┐  ┌──────────────────┐ │
│  │ JSONL    │  │ SQLite (Phase 2) │ │  ← 저장소 구현체
│  │ (기존)   │  │ (조회/통계용)    │ │
│  └──────────┘  └──────────────────┘ │
└─────────────────────────────────────┘
```

### 2.4 SQLite 기반 감사 로그 DB (Phase 2)

JSONL은 쓰기 전용으로 유지하고, 조회/통계를 위해 SQLite DB를 병행한다.

```sql
-- audit.db

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    event TEXT NOT NULL,
    user_id TEXT,
    username TEXT,
    department TEXT,
    client_ip TEXT,
    session_id TEXT,
    request_id TEXT,
    user_query TEXT,
    generated_sql TEXT,
    target_tables TEXT,  -- JSON array
    target_db TEXT,
    row_count INTEGER,
    execution_time_ms REAL,
    success INTEGER,     -- 0/1
    error TEXT,
    file_name TEXT,
    file_type TEXT,
    file_size_bytes INTEGER,
    masked_columns TEXT, -- JSON array
    security_flags TEXT, -- JSON array
    extra TEXT,          -- JSON object
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_user_id ON audit_log(user_id);
CREATE INDEX idx_audit_event ON audit_log(event);
CREATE INDEX idx_audit_target_db ON audit_log(target_db);
CREATE INDEX idx_audit_success ON audit_log(success);
```

---

## 3. AuditService 구현

```python
# src/security/audit_service.py

class AuditService:
    """통합 감사 로그 서비스."""

    def __init__(
        self,
        jsonl_enabled: bool = True,
        sqlite_enabled: bool = True,
        sqlite_path: str = "logs/audit.db",
    ):
        self._jsonl_enabled = jsonl_enabled
        self._sqlite_enabled = sqlite_enabled
        self._sqlite_path = sqlite_path
        ...

    async def log(self, entry: AuditLogEntry) -> None:
        """감사 이벤트를 기록한다."""
        if self._jsonl_enabled:
            await self._write_jsonl(entry)
        if self._sqlite_enabled:
            await self._write_sqlite(entry)

    async def log_login(
        self,
        user_id: str,
        success: bool,
        client_ip: str,
        error: Optional[str] = None,
    ) -> None:
        """로그인 이벤트를 기록한다."""
        ...

    async def log_data_access(
        self,
        user_id: str,
        query: str,
        sql: str,
        tables: list[str],
        db: str,
        row_count: int,
        execution_time_ms: float,
        client_ip: str,
    ) -> None:
        """데이터 접근을 기록한다."""
        ...

    async def log_file_download(
        self,
        user_id: str,
        file_name: str,
        file_type: str,
        file_size: int,
        client_ip: str,
    ) -> None:
        """파일 다운로드를 기록한다."""
        ...

    async def log_security_alert(
        self,
        event_detail: str,
        user_id: Optional[str],
        client_ip: str,
        severity: str = "warning",  # "info" | "warning" | "critical"
    ) -> None:
        """보안 경고를 기록한다."""
        ...
```

---

## 4. 감사 로그 조회 API

### 4.1 엔드포인트 (관리자 전용)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/v1/admin/audit/logs` | 감사 로그 목록 조회 (페이지네이션) |
| GET | `/api/v1/admin/audit/logs/{request_id}` | 특정 요청의 전체 로그 체인 |
| GET | `/api/v1/admin/audit/stats` | 사용 통계 |
| GET | `/api/v1/admin/audit/users/{user_id}/activity` | 특정 사용자 활동 이력 |
| GET | `/api/v1/admin/audit/alerts` | 보안 경고 목록 |

### 4.2 조회 필터

```python
class AuditLogFilter(BaseModel):
    """감사 로그 조회 필터."""

    start_date: Optional[str] = None     # ISO 날짜 (시작)
    end_date: Optional[str] = None       # ISO 날짜 (끝)
    user_id: Optional[str] = None        # 특정 사용자
    event: Optional[str] = None          # 이벤트 유형
    target_db: Optional[str] = None      # 대상 DB
    success: Optional[bool] = None       # 성공/실패 필터
    keyword: Optional[str] = None        # 질의/SQL 키워드 검색
    page: int = 1
    page_size: int = 50
```

### 4.3 통계 응답

```python
class AuditStatsResponse(BaseModel):
    """감사 통계 응답."""

    period: str                           # 조회 기간
    total_requests: int                   # 총 요청 수
    unique_users: int                     # 고유 사용자 수
    success_rate: float                   # 성공률
    avg_execution_time_ms: float          # 평균 실행 시간

    # 상위 항목
    top_users: list[dict]                 # 사용량 상위 사용자
    top_tables: list[dict]               # 접근 빈도 상위 테이블
    top_queries: list[dict]              # 빈번 질의 패턴

    # 일별 추이
    daily_counts: list[dict]             # [{date, count, unique_users}]

    # 보안
    security_alerts_count: int           # 보안 경고 수
    failed_login_count: int              # 로그인 실패 수
```

---

## 5. 로그 수집 지점 (노드별)

기존 노드에 감사 로그 호출을 추가한다.

| 노드 | 기록 내용 |
|------|----------|
| `input_parser` | `user_request`: 질의 내용, 파일 여부, 사용자 정보 |
| `query_executor` | `query_execution`: SQL, 결과 건수, 실행 시간 (기존) |
| `query_executor` | `data_access`: 접근 테이블, DB, 행 수 |
| `result_organizer` | 마스킹된 컬럼 정보 |
| `output_generator` | `file_download` (파일 생성 시) |
| `query_validator` | `security_alert` (금지 SQL 감지 시) |

### 5.1 미들웨어로 요청 메타 자동 수집

```python
# src/api/middleware/audit_middleware.py

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import uuid

class AuditMiddleware(BaseHTTPMiddleware):
    """요청별 감사 컨텍스트를 자동 설정하는 미들웨어."""

    async def dispatch(self, request: Request, call_next):
        # 요청 ID 생성
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id
        request.state.client_ip = request.client.host if request.client else "unknown"

        # structlog 컨텍스트에 바인딩
        import structlog
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            client_ip=request.state.client_ip,
        )

        response = await call_next(request)

        # 컨텍스트 해제
        structlog.contextvars.unbind_contextvars("request_id", "client_ip")
        return response
```

---

## 6. 보안 경고 규칙

### 6.1 자동 감지 규칙

| 규칙 | 조건 | 심각도 |
|------|------|--------|
| 로그인 실패 반복 | 동일 user_id로 5회 연속 실패 | critical |
| 금지 SQL 시도 | SQLGuard에서 DML/DDL 감지 | warning |
| 대량 데이터 조회 | 결과 > 5,000행 | info |
| 비정상 시간 접근 | 새벽 2~6시 접근 | info |
| 민감 테이블 접근 | 설정된 민감 테이블 조회 시 | warning |
| 허용되지 않은 DB 접근 시도 | allowed_db_ids 외 DB 요청 | critical |

---

## 7. AgentState 확장

```python
# src/state.py (추가 필드)

class AgentState(TypedDict):
    ...
    # === 감사 로깅 ===
    user_id: Optional[str]           # 인증된 사용자 ID
    user_department: Optional[str]   # 사용자 부서
    client_ip: Optional[str]         # 클라이언트 IP
    request_id: Optional[str]        # 요청 추적 ID
    accessed_tables: list[str]       # 실제 접근한 테이블 목록
```

---

## 8. 관리자 UI

### 8.1 감사 로그 조회 화면

관리자 대시보드에 **감사 로그** 탭을 추가한다.

- **필터**: 날짜 범위, 사용자, 이벤트 유형, DB, 성공/실패
- **목록**: 타임스탬프, 사용자, 이벤트, 질의 요약, DB, 결과
- **상세**: 특정 요청의 전체 이벤트 체인 (요청→SQL→실행→결과)
- **통계**: 일별 사용량 차트, 사용자별 통계, 테이블 접근 빈도

---

## 9. 구현 파일 목록

| 파일 | 작업 | 계층 |
|------|------|------|
| `src/security/audit_service.py` | 신규 | infrastructure |
| `src/security/audit_logger.py` | 수정 (AuditService 연동) | infrastructure |
| `src/security/audit_models.py` | 신규 (AuditLogEntry 등) | domain |
| `src/api/middleware/audit_middleware.py` | 신규 | interface |
| `src/api/routes/admin.py` | 수정 (감사 로그 API 추가) | interface |
| `src/api/server.py` | 수정 (미들웨어 등록) | entry |
| `src/nodes/query_executor.py` | 수정 (감사 로그 확장) | application |
| `src/nodes/input_parser.py` | 수정 (감사 로그 확장) | application |
| `src/nodes/query_validator.py` | 수정 (보안 경고 기록) | application |
| `src/state.py` | 수정 (감사 필드 추가) | domain |
| `src/config.py` | 수정 (AuditConfig 추가) | config |
| `src/static/admin/dashboard.html` | 수정 (감사 로그 탭) | static |

---

## 10. 설정 추가

```python
# src/config.py

class AuditConfig(BaseSettings):
    """감사 로그 설정."""

    jsonl_enabled: bool = True            # JSONL 파일 기록
    sqlite_enabled: bool = True           # SQLite DB 기록
    sqlite_path: str = "logs/audit.db"    # SQLite 파일 경로
    retention_days: int = 90              # 로그 보관 기간 (일)
    sensitive_tables: list[str] = []      # 민감 테이블 (접근 시 경고)
    alert_on_failed_login: int = 5        # N회 실패 시 경고
    alert_on_large_result: int = 5000     # N행 초과 시 경고

    model_config = {"env_prefix": "AUDIT_", "env_file": ".env", "extra": "ignore"}
```

---

## 11. 구현 순서

1. `AuditLogEntry` 모델 및 `AuditConfig` 정의
2. `AuditService` 구현 (JSONL + SQLite 이중 기록)
3. `AuditMiddleware` 구현 (request_id, client_ip 자동 수집)
4. 기존 `audit_logger.py`를 `AuditService` 위임 방식으로 리팩토링
5. 각 노드에 감사 로그 호출 추가
6. `user_auth.py`에서 로그인/로그아웃 이벤트 기록
7. 감사 로그 조회/통계 API 구현
8. 보안 경고 규칙 구현
9. 관리자 UI에 감사 로그 탭 추가
10. 로그 보관 기간 관리 (retention 정책)

---

## 12. Plan 39 (사용자 인증)과의 연관

- **의존**: Plan 39 구현 후 `user_id`, `department` 등이 실제 값으로 채워짐
- **독립 구현 가능**: 이벤트 확장, SQLite 저장소, 조회 API는 Plan 39 없이도 구현 가능
- **통합**: Plan 39의 로그인 API에서 `audit_service.log_login()` 호출
