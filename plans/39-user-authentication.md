# 39. 사용자 로그인 및 인증 시스템

> 일반 사용자(운영자가 아닌)가 로그인하여 에이전트를 사용할 수 있도록 인증 체계를 구축한다.
> **개발 단계에서는 인증 없이 모든 기능이 동작**하며, 추후 ID/PW 인증을 활성화하는 방식으로 구현한다.

---

## 1. 현재 상태 분석

### 1.1 기존 인증 구조

- **운영자 인증**: `src/api/routes/admin_auth.py`에 JWT 기반 관리자 로그인 구현 완료
  - `AdminConfig`에서 단일 username/password 관리 (환경변수)
  - JWT 토큰 발급/검증 (`PyJWT`, HS256)
  - `require_admin` 의존성으로 관리자 API 보호
  - 토큰 payload: `sub`, `exp`, `iat`, `type: "admin"`
- **사용자 인증**: 없음. `query.py`의 질의 API는 인증 없이 접근 가능
- **사용자 식별**: 감사 로그에 `user_id` 필드 존재하지만 항상 None
- **DB 접근**: `asyncpg` 기반 `PostgresClient`가 이미 존재 (`src/db/client.py`)
- **감사 로그**: 파일 기반 JSONL (`src/security/audit_logger.py`), DB 확장 예정 상태

### 1.2 문제점

1. 누구나 에이전트에 접근하여 DB 질의 가능 → 내부 인프라 데이터 노출 위험
2. 사용자 식별 불가 → 감사 로그에서 행위자 추적 불가능
3. 사용자별 권한 차등 불가 (Plan 41 접근 제어의 전제 조건)

---

## 2. 설계 원칙

### 2.1 핵심 원칙

| 원칙 | 설명 |
|------|------|
| **인증 비활성화 기본** | `AUTH_ENABLED=false` (기본값). 개발 단계에서는 인증 없이 모든 기능 동작 |
| **DB 기반 저장** | 사용자/감사 정보를 PostgreSQL에 저장. 향후 DB2 전환 가능하도록 추상화 |
| **자유 가입 + 관리자 권한 부여** | 사용자가 직접 가입 (승인 불필요), 관리자가 사후에 권한 부여 |
| **SAML SSO 확장 기반** | 인증 프로바이더를 추상화하여 ID/PW 외 SAML SSO 연동 가능 구조 |

### 2.2 인증 비활성화 모드 동작

인증이 비활성화(`AUTH_ENABLED=false`)된 상태에서:
- 모든 API는 인증 없이 접근 가능 (기존과 동일)
- `require_user` 의존성은 **익명 사용자**(anonymous) 정보를 반환
- 감사 로그의 `user_id`는 `"anonymous"`로 기록
- 회원가입/로그인 API는 존재하지만 인증 검사를 하지 않음

```python
# 인증 비활성화 시 반환되는 기본 사용자
ANONYMOUS_USER = {
    "sub": "anonymous",
    "name": "Anonymous",
    "role": "user",
    "department": None,
    "allowed_db_ids": None,  # 전체 접근 허용
}
```

---

## 3. 인증 프로바이더 추상화 (SAML SSO 기반 마련)

향후 SAML SSO 연동을 위해 인증 방식을 추상화한다.

```python
# src/domain/auth.py (신규, domain 계층)

from abc import ABC, abstractmethod
from typing import Optional
from enum import Enum

class AuthMethod(str, Enum):
    """인증 방식."""
    LOCAL = "local"      # ID/PW (기본)
    SAML = "saml"        # SAML SSO (향후)

class AuthProvider(ABC):
    """인증 프로바이더 인터페이스.

    ID/PW 인증(LocalAuthProvider)과 향후 SAML SSO 인증(SamlAuthProvider)을
    동일한 인터페이스로 처리할 수 있게 추상화한다.
    """

    @abstractmethod
    async def authenticate(self, credentials: dict) -> Optional[dict]:
        """인증을 수행하고 사용자 정보를 반환한다.

        Args:
            credentials: 인증 정보 (방식에 따라 구조 다름)
                - LOCAL: {"user_id": "...", "password": "..."}
                - SAML: {"saml_response": "..."}

        Returns:
            인증된 사용자 정보 dict 또는 None (인증 실패)
        """
        ...

    @abstractmethod
    def get_method(self) -> AuthMethod:
        """인증 방식을 반환한다."""
        ...
```

```python
# src/infrastructure/auth_provider.py (신규, infrastructure 계층)

class LocalAuthProvider(AuthProvider):
    """ID/PW 기반 로컬 인증 프로바이더.

    UserRepository에서 사용자를 조회하고 bcrypt 비밀번호를 검증한다.
    """

    def __init__(self, user_repo: UserRepository):
        self._user_repo = user_repo

    async def authenticate(self, credentials: dict) -> Optional[dict]:
        user = await self._user_repo.get_by_user_id(credentials["user_id"])
        if not user or not user.is_active:
            return None
        if not verify_password(credentials["password"], user.hashed_password):
            return None
        return user.to_auth_dict()

    def get_method(self) -> AuthMethod:
        return AuthMethod.LOCAL


# 향후 SAML 연동 시 추가할 클래스 (현재 미구현, 구조만 예시)
# class SamlAuthProvider(AuthProvider):
#     """SAML SSO 기반 인증 프로바이더."""
#     def __init__(self, idp_metadata_url: str, sp_entity_id: str): ...
#     async def authenticate(self, credentials: dict) -> Optional[dict]: ...
#     def get_method(self) -> AuthMethod: return AuthMethod.SAML
```

---

## 4. 사용자 도메인 모델

```python
# src/domain/user.py (신규, domain 계층)

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
from datetime import datetime

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class UserStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    LOCKED = "locked"       # 로그인 시도 초과

@dataclass
class User:
    user_id: str                  # 로그인 ID
    username: str                 # 표시 이름
    hashed_password: str          # bcrypt 해시
    role: UserRole = UserRole.USER
    status: UserStatus = UserStatus.ACTIVE
    department: Optional[str] = None
    allowed_db_ids: Optional[list[str]] = None  # 접근 허용 DB (None=전체)
    auth_method: str = "local"    # "local" | "saml" (확장 대비)
    login_fail_count: int = 0     # 연속 로그인 실패 횟수
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE

    def to_auth_dict(self) -> dict:
        """인증 의존성에서 사용할 사용자 정보 dict."""
        return {
            "sub": self.user_id,
            "name": self.username,
            "role": self.role.value,
            "department": self.department,
            "allowed_db_ids": self.allowed_db_ids,
        }
```

---

## 5. DB 기반 저장소 ==> DB구성을 위한 docker-compose.yaml을 작성한다.



### 5.1 저장소 인터페이스 (domain 계층)

```python
# src/domain/user.py (계속)

from abc import ABC, abstractmethod

class UserRepository(ABC):
    """사용자 저장소 인터페이스.

    Clean Architecture: 인터페이스는 domain에, 구현체는 infrastructure에 배치.
    DB 엔진(PostgreSQL/DB2)에 독립적인 인터페이스.
    """

    @abstractmethod
    async def get_by_user_id(self, user_id: str) -> Optional[User]: ...

    @abstractmethod
    async def create(self, user: User) -> None: ...

    @abstractmethod
    async def update(self, user: User) -> None: ...

    @abstractmethod
    async def list_all(self) -> list[User]: ...

    @abstractmethod
    async def delete(self, user_id: str) -> None: ...

    @abstractmethod
    async def exists(self, user_id: str) -> bool: ...


class AuditRepository(ABC):
    """감사 로그 저장소 인터페이스.

    기존 파일 기반 감사 로그를 DB로 확장하기 위한 인터페이스.
    """

    @abstractmethod
    async def log_event(self, event: dict) -> None: ...

    @abstractmethod
    async def query_logs(
        self, user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]: ...
```

### 5.2 PostgreSQL 구현체 (infrastructure 계층)

기존 `src/db/client.py`의 `asyncpg` 패턴을 재활용한다. 향후 DB2 전환 시 구현체만 교체하면 되도록 **raw SQL + asyncpg**를 사용하며, ORM은 도입하지 않는다 (DB2 호환성 확보를 위해).

```python
# src/infrastructure/user_repository.py (신규, infrastructure 계층)

import asyncpg
from src.domain.user import User, UserRepository

class PostgresUserRepository(UserRepository):
    """PostgreSQL 기반 사용자 저장소.

    asyncpg를 직접 사용하여 DB2 전환 시 SQL만 교체하면 되도록 한다.
    ORM 미사용: DB2 드라이버(ibm_db_sa 등) 호환 문제 방지.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def get_by_user_id(self, user_id: str) -> Optional[User]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM auth_users WHERE user_id = $1", user_id
            )
        if not row:
            return None
        return self._row_to_user(row)

    async def create(self, user: User) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO auth_users
                    (user_id, username, hashed_password, role, status,
                     department, allowed_db_ids, auth_method)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """, user.user_id, user.username, user.hashed_password,
                user.role.value, user.status.value,
                user.department, user.allowed_db_ids, user.auth_method)

    async def update(self, user: User) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                UPDATE auth_users SET
                    username = $2, role = $3, status = $4,
                    department = $5, allowed_db_ids = $6,
                    login_fail_count = $7, last_login_at = $8,
                    updated_at = NOW()
                WHERE user_id = $1
            """, user.user_id, user.username, user.role.value,
                user.status.value, user.department,
                user.allowed_db_ids, user.login_fail_count,
                user.last_login_at)

    async def list_all(self) -> list[User]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM auth_users ORDER BY created_at DESC"
            )
        return [self._row_to_user(row) for row in rows]

    async def delete(self, user_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM auth_users WHERE user_id = $1", user_id
            )

    async def exists(self, user_id: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM auth_users WHERE user_id = $1", user_id
            )
        return row is not None

    @staticmethod
    def _row_to_user(row: asyncpg.Record) -> User:
        return User(
            user_id=row["user_id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            role=UserRole(row["role"]),
            status=UserStatus(row["status"]),
            department=row["department"],
            allowed_db_ids=row["allowed_db_ids"],
            auth_method=row["auth_method"],
            login_fail_count=row["login_fail_count"],
            last_login_at=row["last_login_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
```

```python
# src/infrastructure/audit_repository.py (신규, infrastructure 계층)

class PostgresAuditRepository(AuditRepository):
    """PostgreSQL 기반 감사 로그 저장소.

    기존 파일 기반 감사 로그와 병행 운영.
    AUTH_ENABLED=true일 때 DB 감사 로그도 기록.
    """

    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def log_event(self, event: dict) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO audit_logs
                    (event_type, user_id, detail, ip_address)
                VALUES ($1, $2, $3, $4)
            """, event.get("event_type"), event.get("user_id"),
                json.dumps(event.get("detail", {}), ensure_ascii=False),
                event.get("ip_address"))

    async def query_logs(self, user_id=None, event_type=None, limit=100):
        ...
```

### 5.3 DB 스키마 (DDL) ==> 운영환경 설치를 위해 DDL, DML을 정리하고 마이그레이션 방법을 포함한다.

```sql
-- ddl/auth_tables.sql

-- 사용자 테이블
CREATE TABLE IF NOT EXISTS auth_users (
    user_id         VARCHAR(50) PRIMARY KEY,
    username        VARCHAR(100) NOT NULL,
    hashed_password VARCHAR(256) NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'user',
    status          VARCHAR(20) NOT NULL DEFAULT 'active',
    department      VARCHAR(100),
    allowed_db_ids  TEXT[],            -- PostgreSQL 배열 (DB2 전환 시 JSON 컬럼으로 변경)
    auth_method     VARCHAR(20) NOT NULL DEFAULT 'local',
    login_fail_count INTEGER NOT NULL DEFAULT 0,
    last_login_at   TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 감사 로그 테이블
CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,    -- login, logout, query, register, ...
    user_id         VARCHAR(50),
    detail          JSONB,                    -- 이벤트 상세 (SQL, 에러 등)
    ip_address      VARCHAR(45),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX idx_audit_logs_created_at ON audit_logs(created_at DESC);
```

> **DB2 전환 참고**: `TEXT[]` → `CLOB` (JSON 배열 문자열), `JSONB` → `CLOB`, `BIGSERIAL` → `BIGINT GENERATED ALWAYS AS IDENTITY`. 구현체(`PostgresUserRepository`)만 `Db2UserRepository`로 교체하면 됨.

---

## 6. 비밀번호 보안

```python
# src/utils/password.py (신규, utils 계층)
#
# src/security/는 arch_check.py에서 infrastructure 계층이므로
# 범용 유틸리티인 비밀번호 해싱은 utils에 배치.

import bcrypt

def hash_password(plain: str) -> str:
    """비밀번호를 bcrypt로 해시한다."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()

def verify_password(plain: str, hashed: str) -> bool:
    """비밀번호를 검증한다."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())
```

---

## 7. JWT 토큰 구조

**JWT 시크릿은 기존 `AdminConfig.jwt_secret`을 공유**, `type` 클레임으로 운영자/사용자를 구분한다.

**토큰에는 최소한의 식별 정보만 포함**. 권한 정보(`allowed_db_ids`, `department`)는 토큰에 넣지 않고, 요청 시 DB에서 조회하여 실시간 권한 변경을 즉시 반영한다.

```python
# 사용자 JWT payload (최소 정보)
{
    "sub": "user_id",     # 사용자 식별자
    "name": "홍길동",      # 표시 이름 (UI용)
    "role": "user",        # "user" | "admin"
    "exp": 1711929600,
    "iat": 1711900800,
    "type": "user"         # 운영자("admin")과 구분
}
```

---

## 8. API 설계

### 8.1 사용자 인증 엔드포인트

| Method | Path | 설명 | 인증 |
|--------|------|------|------|
| POST | `/api/v1/auth/register` | 사용자 가입 (승인 불필요) | 없음 |
| POST | `/api/v1/auth/login` | 사용자 로그인 | 없음 |
| POST | `/api/v1/auth/logout` | 로그아웃 (감사 로그 기록) | Bearer |
| GET | `/api/v1/auth/me` | 현재 사용자 정보 | Bearer |
| PUT | `/api/v1/auth/password` | 비밀번호 변경 | Bearer |

> **회원가입 정책**: 가입 시 기본 역할은 `USER`, `allowed_db_ids`는 `None`(전체 접근 불가 — 관리자가 부여). 관리자 승인 없이 즉시 가입 완료.

> **로그아웃**: Phase 1은 클라이언트 측 토큰 삭제 + DB 감사 로그 기록. 향후 Redis 블랙리스트 확장 가능.

### 8.2 사용자 관리 엔드포인트 (관리자 전용)

| Method | Path | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/v1/admin/users` | 사용자 목록 조회 | Admin |
| PUT | `/api/v1/admin/users/{user_id}` | 사용자 수정 (역할/권한 부여) | Admin |
| DELETE | `/api/v1/admin/users/{user_id}` | 사용자 삭제 | Admin |
| POST | `/api/v1/admin/users/{user_id}/reset-password` | 비밀번호 초기화 | Admin |
| PUT | `/api/v1/admin/users/{user_id}/permissions` | DB 접근 권한 부여/수정 | Admin |

> **관리자 사용자 등록**: 관리자 전용 `POST /admin/users`는 제거. 사용자는 스스로 `/auth/register`로 가입하고, 관리자는 `PUT /admin/users/{user_id}`로 역할/권한만 수정.

### 8.3 요청/응답 모델

```python
# src/api/schemas.py (추가)

class UserRegisterRequest(BaseModel):
    """사용자 가입 요청. 승인 없이 즉시 가입."""
    user_id: str = Field(..., min_length=3, max_length=50, pattern=r'^[a-zA-Z0-9_]+$')
    username: str = Field(..., min_length=1, max_length=100, description="표시 이름")
    password: str = Field(..., min_length=8, description="최소 8자")
    department: Optional[str] = Field(None, max_length=100)

class UserLoginRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)

class UserLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfoResponse

class UserInfoResponse(BaseModel):
    user_id: str
    username: str
    role: str
    department: Optional[str]
    allowed_db_ids: Optional[list[str]]
    status: str
    last_login_at: Optional[str]

class ChangePasswordRequest(BaseModel):
    """비밀번호 변경. 현재 비밀번호 확인 필수."""
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8)

class UpdateUserRequest(BaseModel):
    """관리자용 사용자 수정. 변경할 필드만 포함."""
    username: Optional[str] = Field(None, min_length=1, max_length=100)
    role: Optional[str] = Field(None, pattern=r'^(user|admin)$')
    department: Optional[str] = None
    status: Optional[str] = Field(None, pattern=r'^(active|inactive|locked)$')

class UpdatePermissionsRequest(BaseModel):
    """관리자용 DB 접근 권한 수정."""
    allowed_db_ids: Optional[list[str]] = Field(None, description="접근 허용 DB 목록 (null=전체)")
```

---

## 9. 인증 의존성

### 9.1 핵심: 인증 비활성화 시 anonymous 반환

```python
# src/api/dependencies.py (신규, interface 계층)

ANONYMOUS_USER = {
    "sub": "anonymous",
    "name": "Anonymous",
    "role": "user",
    "department": None,
    "allowed_db_ids": None,
}

async def get_current_user(
    request: Request,
    authorization: str = Header(None, description="Bearer {token}"),
) -> Optional[dict]:
    """현재 인증된 사용자 정보를 반환한다.

    - AUTH_ENABLED=false: ANONYMOUS_USER 반환
    - 토큰이 없으면 None, 있으면 검증 후 DB에서 최신 정보 조회
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER
    if not authorization:
        return None
    # JWT 검증 → UserRepository에서 조회
    ...

async def require_user(
    request: Request,
    authorization: str = Header(None, description="Bearer {token}"),
) -> dict:
    """인증된 사용자를 필수로 요구한다.

    - AUTH_ENABLED=false: ANONYMOUS_USER 반환 (인증 우회)
    - AUTH_ENABLED=true: JWT 토큰 필수, DB에서 사용자 조회
    """
    config = request.app.state.config
    if not config.auth.enabled:
        return ANONYMOUS_USER

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "인증이 필요합니다.")

    token = authorization[7:]
    payload = verify_token(token, config.admin.jwt_secret)
    user_repo = request.app.state.user_repo
    user = await user_repo.get_by_user_id(payload["sub"])
    if not user or not user.is_active:
        raise HTTPException(401, "비활성 사용자입니다.")
    return user.to_auth_dict()
```

### 9.2 기존 API에 인증 적용

```python
# src/api/routes/query.py (수정)
# /query/stream은 POST이므로 Authorization 헤더 사용 가능 (GET EventSource 문제 해당 없음)

@router.post("/query")
async def process_query(
    request: Request,
    current_user: dict = Depends(require_user),  # AUTH_ENABLED=false면 anonymous
    ...
):
    state["user_id"] = current_user["sub"]
    state["user_department"] = current_user.get("department")
    state["allowed_db_ids"] = current_user.get("allowed_db_ids")
    ...

@router.post("/query/stream")
async def process_query_stream(
    request: Request,
    body: QueryRequest,
    current_user: dict = Depends(require_user),
) -> StreamingResponse:
    state["user_id"] = current_user["sub"]
    ...
```

```python
# src/api/routes/conversation.py (수정)

@router.get("/conversation/{thread_id}")
async def get_conversation(
    request: Request, thread_id: str,
    current_user: dict = Depends(require_user),
) -> ConversationResponse:
    ...

@router.delete("/conversation/{thread_id}")
async def delete_conversation(
    request: Request, thread_id: str,
    current_user: dict = Depends(require_user),
) -> dict:
    ...
```

---

## 10. 설정 확장

```python
# src/config.py (수정)

class AuthConfig(BaseSettings):
    """사용자 인증 설정."""

    enabled: bool = False                        # 기본 비활성화 (개발 환경)
    auth_db_url: str = ""                        # 인증 전용 DB URL (비어있으면 db_connection_string 공유)
    jwt_expire_hours: int = 8                    # 사용자 토큰 만료 시간
    max_login_attempts: int = 5                  # 최대 로그인 실패 횟수
    lockout_minutes: int = 30                    # 잠금 시간
    password_min_length: int = 8                 # 최소 비밀번호 길이
    default_allowed_db_ids: str = ""             # 신규 가입자 기본 DB 권한 (빈 문자열=없음)

    model_config = {"env_prefix": "AUTH_", "env_file": [".env", ".encenv"], "extra": "ignore"}


# AppConfig에 추가
class AppConfig(BaseSettings):
    ...
    auth: AuthConfig = AuthConfig()
    ...
```

> **JWT 시크릿**: `AdminConfig.jwt_secret`을 공유. 사용자 토큰 발급/검증 시 `config.admin.jwt_secret` 참조.
>
> **인증 DB**: `AUTH_DB_URL`이 설정되면 별도 DB 사용, 비어있으면 기존 `db_connection_string` 공유.

---

## 11. AgentState 확장

```python
# src/state.py (수정)

class AgentState(TypedDict):
    ...
    # === 사용자 컨텍스트 (인증 시스템에서 주입) ===
    user_id: Optional[str]                   # "anonymous" 또는 실제 user_id
    user_department: Optional[str]
    allowed_db_ids: Optional[list[str]]      # None=전체 허용
```

`create_initial_state()`에도 해당 필드 추가:

```python
def create_initial_state(
    ..., user_id=None, user_department=None, allowed_db_ids=None
):
    return AgentState(
        ...
        user_id=user_id,
        user_department=user_department,
        allowed_db_ids=allowed_db_ids,
    )
```

---

## 12. 서버 초기화

```python
# src/api/server.py (수정)

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_config()
    ...

    # 인증 DB 초기화 (AUTH_ENABLED 여부와 무관하게 테이블은 생성)
    auth_db_url = config.auth.auth_db_url or config.db_connection_string
    if auth_db_url:
        auth_pool = await asyncpg.create_pool(auth_db_url, min_size=1, max_size=5)
        app.state.auth_pool = auth_pool
        app.state.user_repo = PostgresUserRepository(auth_pool)
        app.state.audit_repo = PostgresAuditRepository(auth_pool)

        # DDL 자동 실행 (테이블이 없으면 생성)
        await _ensure_auth_tables(auth_pool)
    else:
        app.state.user_repo = None
        app.state.audit_repo = None

    # 인증 프로바이더 설정
    if app.state.user_repo:
        app.state.auth_provider = LocalAuthProvider(app.state.user_repo)
    ...

    yield

    # 종료 시 인증 DB 풀 정리
    if hasattr(app.state, "auth_pool") and app.state.auth_pool:
        await app.state.auth_pool.close()
```

---

## 13. UI 변경

### 13.1 인증 비활성화 모드 (기본)

- 기존 채팅 화면 그대로 동작
- 로그인 화면 없이 바로 채팅 가능
- 우측 상단에 "Anonymous" 표시

### 13.2 인증 활성화 모드

#### 사용자 로그인 화면
- `src/static/login.html` 신규 생성
- 로그인 폼 + **회원가입 링크**
- 루트(`/`) 접근 시 토큰 없으면 로그인 화면으로 리다이렉트
- 로그인 성공 → `localStorage`에 토큰 저장 → 채팅 화면으로 이동

#### 사용자 가입 화면
- `src/static/register.html` 신규 생성
- ID, 이름, 비밀번호, 부서(선택) 입력
- 가입 즉시 완료 (승인 불필요)
- 가입 후 로그인 화면으로 이동

#### 기존 채팅 화면 수정
- `src/static/index.html`, `src/static/js/app.js` 수정
- 페이지 로드 시 `AUTH_ENABLED` 확인 → 활성화면 토큰 검증
- API 호출 시 `Authorization: Bearer {token}` 헤더 자동 첨부
- 우측 상단에 사용자 정보 + 로그아웃 버튼

#### 관리자 대시보드
- `src/static/admin/dashboard.html`에 사용자 관리 탭 추가
- 사용자 목록 / 역할 변경 / DB 권한 부여 / 비밀번호 초기화
- 감사 로그 조회 UI (DB 기반)

---

## 14. 구현 파일 목록

| 파일 | 작업 | 계층 |
|------|------|------|
| `src/domain/auth.py` | 신규 (AuthProvider 인터페이스, AuthMethod) | domain |
| `src/domain/user.py` | 신규 (User 모델, UserRepository, AuditRepository) | domain |
| `src/utils/password.py` | 신규 (bcrypt 해싱) | utils |
| `src/infrastructure/auth_provider.py` | 신규 (LocalAuthProvider) | infrastructure |
| `src/infrastructure/user_repository.py` | 신규 (PostgresUserRepository) | infrastructure |
| `src/infrastructure/audit_repository.py` | 신규 (PostgresAuditRepository) | infrastructure |
| `src/api/dependencies.py` | 신규 (인증 의존성, anonymous 모드) | interface |
| `src/api/routes/user_auth.py` | 신규 (가입/로그인/인증 API) | interface |
| `src/api/routes/admin.py` | 수정 (사용자 관리/권한 API 추가) | interface |
| `src/api/routes/query.py` | 수정 (인증 적용) | interface |
| `src/api/routes/conversation.py` | 수정 (인증 적용) | interface |
| `src/api/server.py` | 수정 (라우터 등록, DB 풀/저장소 초기화) | entry |
| `src/api/schemas.py` | 수정 (사용자 관련 스키마 추가) | interface |
| `src/config.py` | 수정 (AuthConfig 추가) | config |
| `src/state.py` | 수정 (user_id 등 필드 추가) | domain |
| `ddl/auth_tables.sql` | 신규 (인증/감사 DDL) | - |
| `src/static/login.html` | 신규 | static |
| `src/static/register.html` | 신규 | static |
| `src/static/index.html` | 수정 (인증 연동) | static |
| `src/static/js/app.js` | 수정 (Authorization 헤더, anonymous 모드) | static |
| `src/static/admin/dashboard.html` | 수정 (사용자 관리 탭) | static |
| `src/static/js/admin.js` | 수정 (사용자 관리 로직) | static |
| `scripts/arch_check.py` | 수정 (domain 계층에 auth, user 모듈 매핑) | - |

### 계층 규칙 준수 확인

| 모듈 | 계층 | 의존 대상 | 위반 여부 |
|------|------|----------|----------|
| `src.domain.auth` (AuthProvider ABC) | domain | 없음 (stdlib만) | OK |
| `src.domain.user` (User, UserRepository, AuditRepository) | domain | 없음 (stdlib만) | OK |
| `src.utils.password` | utils | bcrypt (외부) | OK |
| `src.infrastructure.auth_provider` | infrastructure | domain | OK |
| `src.infrastructure.user_repository` | infrastructure | domain, asyncpg (외부) | OK |
| `src.infrastructure.audit_repository` | infrastructure | domain, asyncpg (외부) | OK |
| `src.api.dependencies` | interface | config, domain, infrastructure | OK |

---

## 15. 의존성 추가

```toml
# pyproject.toml
[project.dependencies]
bcrypt = ">=4.0.0"   # 비밀번호 해싱 (기존 PyJWT, asyncpg는 이미 포함)
```

> `asyncpg`는 이미 포함되어 있으므로 추가 불필요.

---

## 16. 구현 순서

### Phase A: 인프라 준비 (인증 비활성화 상태로 동작 보장)

1. `AuthConfig` 설정 추가 (`enabled=False` 기본값)
2. `User` 도메인 모델 + `UserRepository`/`AuditRepository` 인터페이스 정의
3. `AuthProvider` 인터페이스 + `AuthMethod` 정의
4. `password.py` 비밀번호 유틸 구현
5. DDL 작성 (`ddl/auth_tables.sql`)
6. `PostgresUserRepository`, `PostgresAuditRepository` 구현
7. `LocalAuthProvider` 구현
8. `dependencies.py` 인증 의존성 구현 (**anonymous 모드 포함**)
9. `query.py`, `conversation.py`에 `Depends(require_user)` 적용 (anonymous 모드로 동작)
10. `state.py`에 사용자 컨텍스트 필드 추가
11. `server.py` 수정 (인증 DB 초기화, 라우터 등록)
12. `arch_check.py` 모듈 매핑 추가

### Phase B: 인증 활성화 기능

13. `user_auth.py` — 가입/로그인/로그아웃/비밀번호 변경 API
14. 관리자 사용자 관리/권한 API 추가 (`admin.py`)
15. 사용자 로그인/가입 UI 구현
16. 기존 채팅 UI에 인증 연동 (조건부)
17. 관리자 대시보드에 사용자 관리 탭 추가

---

## 17. 보안 고려사항

- 비밀번호는 반드시 bcrypt 해시로 저장 (평문 저장 금지)
- JWT 시크릿은 `AdminConfig.jwt_secret` 공유, `.encenv`에서 관리 (Git 제외)
- 로그인 시도 5회 실패 시 계정 잠금 (`status=locked`), 관리자가 해제 또는 `lockout_minutes` 후 자동 해제
- 토큰 만료 시 재인증 필수
- HTTPS 환경에서만 운영 권장
- 비밀번호 변경 시 현재 비밀번호 확인 필수
- 관리자가 사용자 권한 변경 시 토큰 재발급 불요 (매 요청마다 DB 조회)
- 회원가입 시 기본 권한은 최소 (관리자가 부여)

---

## 18. DB2 전환 가이드

향후 DB2 전환 시 변경 범위:

| 변경 대상 | 내용 |
|----------|------|
| `src/infrastructure/user_repository.py` | `Db2UserRepository` 구현체 추가 (SQL 방언 변경) |
| `src/infrastructure/audit_repository.py` | `Db2AuditRepository` 구현체 추가 |
| `ddl/auth_tables.sql` | DB2 DDL 버전 추가 |
| `src/config.py` | `auth_db_url` 연결 문자열 변경 |
| `src/api/server.py` | DB 풀 생성 라이브러리 변경 (`asyncpg` → `aioodbc` 또는 `ibm_db`) |
| `pyproject.toml` | DB2 드라이버 의존성 추가 |

도메인 계층(`domain/user.py`, `domain/auth.py`)은 변경 불필요.

---

## 19. SAML SSO 확장 가이드

향후 SAML SSO 연동 시 확장 범위:

| 변경 대상 | 내용 |
|----------|------|
| `src/infrastructure/auth_provider.py` | `SamlAuthProvider` 구현체 추가 |
| `src/config.py` | `AuthConfig`에 SAML 관련 설정 추가 (`saml_idp_metadata_url`, `saml_sp_entity_id` 등) |
| `src/api/routes/user_auth.py` | SAML 콜백 엔드포인트 추가 (`/api/v1/auth/saml/callback`) |
| `src/api/server.py` | `auth_method` 설정에 따라 AuthProvider 선택 |
| `pyproject.toml` | `python3-saml` 또는 `pysaml2` 의존성 추가 |
| UI | 로그인 화면에 "SSO 로그인" 버튼 추가 |

`AuthProvider` 인터페이스 덕분에 기존 `LocalAuthProvider` 코드 변경 없이 추가만 하면 됨.
