# 41. 프롬프트 기반 접근 제어 (Access Control)

> 사용자의 프롬프트 내용을 분석하여 허용된 기능만 실행할 수 있도록 제한하는 시스템을 구축한다.

---

## 1. 현재 상태 분석

### 1.1 기존 보안 레이어

```
Layer 1: MCP 서버 readonly 설정 (DML/DDL 차단)
Layer 2: SQLGuard (금지 키워드 + 인젝션 패턴 탐지)
Layer 3: query_validator (SQL 검증)
```

### 1.2 부족한 점

1. **기능 수준 제한 없음**: SQL 안전성만 검증하며, 사용자가 어떤 종류의 조회를 할 수 있는지 제한하지 않음
2. **테이블/DB 수준 접근 제어 없음**: 모든 사용자가 모든 DB의 모든 테이블 조회 가능
3. **프롬프트 의도 분류 없음**: 악의적 의도(프롬프트 인젝션, 시스템 프롬프트 탈취 등) 감지 미구현
4. **사용자별 권한 차등 없음**: 부서/역할에 따른 접근 범위 제한 불가

---

## 2. 접근 제어 계층 설계

기존 3개 보안 레이어 위에 **2개 계층**을 추가한다.

```
Layer 0 (신규): 프롬프트 가드 (Prompt Guard)
    └── 프롬프트 인젝션/악의적 의도 감지
    └── 허용 기능 범위 검증
    └── 금지어/패턴 필터링

Layer 0.5 (신규): 접근 제어 (Access Control)
    └── 사용자별 허용 DB 제한
    └── 사용자별 허용 테이블 제한
    └── 기능별 권한 검증

Layer 1: MCP 서버 readonly (기존)
Layer 2: SQLGuard (기존)
Layer 3: query_validator (기존)
```

---

## 3. 프롬프트 가드 (Prompt Guard)

### 3.1 역할

사용자 프롬프트가 에이전트 파이프라인에 진입하기 전에 안전성과 허용 범위를 검증한다.

### 3.2 검증 규칙

#### 3.2.1 프롬프트 인젝션 감지

```python
# src/security/prompt_guard.py

class PromptGuard:
    """프롬프트 안전성을 검증한다."""

    # 프롬프트 인젝션 패턴
    INJECTION_PATTERNS: list[re.Pattern] = [
        re.compile(r"ignore\s+(previous|above|all)\s+(instructions?|prompts?)", re.I),
        re.compile(r"(system|assistant)\s*prompt", re.I),
        re.compile(r"you\s+are\s+(now|a)\s+", re.I),
        re.compile(r"forget\s+(everything|all|your)", re.I),
        re.compile(r"(reveal|show|print|output)\s+(your|the|system)\s+(prompt|instructions?)", re.I),
        re.compile(r"act\s+as\s+(if|a)\s+", re.I),
        re.compile(r"do\s+not\s+follow\s+(the|your)\s+(rules?|instructions?)", re.I),
        re.compile(r"override\s+(your|the|all)\s+", re.I),
        re.compile(r"new\s+instructions?\s*:", re.I),
        re.compile(r"```\s*(system|prompt)", re.I),
    ]

    # 금지된 의도 패턴 (한국어)
    FORBIDDEN_INTENT_PATTERNS: list[re.Pattern] = [
        re.compile(r"(삭제|제거|drop|delete|truncate)\s*(해|하|해줘|해라)", re.I),
        re.compile(r"(수정|변경|update|insert|alter)\s*(해|하|해줘|해라)", re.I),
        re.compile(r"(비밀번호|패스워드|password|secret)\s*(알려|보여|출력)", re.I),
        re.compile(r"(권한|permission|grant)\s*(변경|수정|부여)", re.I),
        re.compile(r"(시스템|system)\s*(프롬프트|prompt)\s*(보여|알려|출력)", re.I),
        re.compile(r"(테이블|table)\s*(생성|만들|create)", re.I),
    ]

    def check(self, prompt: str) -> PromptCheckResult:
        """프롬프트를 검증한다.

        Returns:
            PromptCheckResult(is_safe, violations, risk_level)
        """
        violations = []

        # 1. 프롬프트 인젝션 검사
        for pattern in self.INJECTION_PATTERNS:
            if pattern.search(prompt):
                violations.append(PromptViolation(
                    type="injection",
                    pattern=pattern.pattern,
                    severity="critical",
                ))

        # 2. 금지 의도 검사
        for pattern in self.FORBIDDEN_INTENT_PATTERNS:
            if pattern.search(prompt):
                violations.append(PromptViolation(
                    type="forbidden_intent",
                    pattern=pattern.pattern,
                    severity="high",
                ))

        # 3. 과도한 길이 검사
        if len(prompt) > 10000:
            violations.append(PromptViolation(
                type="excessive_length",
                severity="medium",
            ))

        risk_level = self._calculate_risk(violations)
        return PromptCheckResult(
            is_safe=risk_level != "blocked",
            violations=violations,
            risk_level=risk_level,
        )

    def _calculate_risk(self, violations: list) -> str:
        """위험 수준을 계산한다."""
        if any(v.severity == "critical" for v in violations):
            return "blocked"
        if any(v.severity == "high" for v in violations):
            return "blocked"
        if violations:
            return "warning"
        return "safe"
```

### 3.3 결과 모델

```python
@dataclass
class PromptViolation:
    type: str         # "injection" | "forbidden_intent" | "excessive_length"
    pattern: str = ""
    severity: str = "medium"  # "low" | "medium" | "high" | "critical"
    message: str = ""

@dataclass
class PromptCheckResult:
    is_safe: bool
    violations: list[PromptViolation]
    risk_level: str   # "safe" | "warning" | "blocked"
```

---

## 4. 접근 제어 (Access Control)

### 4.1 권한 모델

```python
# src/security/access_control.py

@dataclass
class UserPermission:
    """사용자 권한 정의."""

    # 허용 DB (None = 전체 허용)
    allowed_db_ids: Optional[list[str]] = None

    # 허용 테이블 패턴 (None = 전체 허용)
    # 예: ["servers", "cpu_*", "mem_*"]  → 와일드카드 지원
    allowed_table_patterns: Optional[list[str]] = None

    # 금지 테이블 (블랙리스트, 우선 적용)
    denied_tables: Optional[list[str]] = None

    # 허용 기능
    can_query: bool = True          # SQL 조회
    can_upload_file: bool = True    # 파일 업로드 (양식)
    can_download_file: bool = True  # 결과 파일 다운로드
    can_use_multiturn: bool = True  # 멀티턴 대화
    can_manage_cache: bool = False  # 캐시 관리 (기본 비활성)
    can_register_synonym: bool = False  # 유사어 등록

    # 제한
    max_queries_per_hour: Optional[int] = None   # 시간당 최대 질의
    max_rows_per_query: Optional[int] = None     # 쿼리당 최대 행
    max_file_size_mb: Optional[int] = 10         # 최대 파일 크기
```

### 4.2 권한 설정 방식

**역할 기반 기본 권한 + 사용자별 오버라이드**

```python
# config/permissions.json

{
    "role_defaults": {
        "user": {
            "can_query": true,
            "can_upload_file": true,
            "can_download_file": true,
            "can_use_multiturn": true,
            "can_manage_cache": false,
            "can_register_synonym": false,
            "max_queries_per_hour": 100,
            "max_rows_per_query": 5000
        },
        "admin": {
            "can_query": true,
            "can_upload_file": true,
            "can_download_file": true,
            "can_use_multiturn": true,
            "can_manage_cache": true,
            "can_register_synonym": true,
            "max_queries_per_hour": null,
            "max_rows_per_query": null
        }
    },
    "user_overrides": {
        "user001": {
            "allowed_db_ids": ["polestar"],
            "denied_tables": ["users", "credentials"]
        },
        "team_infra": {
            "allowed_db_ids": null,
            "max_rows_per_query": 10000
        }
    }
}
```

### 4.3 AccessControlService

```python
# src/security/access_control.py

class AccessControlService:
    """접근 제어 서비스."""

    def __init__(self, permissions_path: str = "config/permissions.json"):
        self._permissions = self._load_permissions(permissions_path)

    def get_user_permission(self, user_id: str, role: str) -> UserPermission:
        """사용자의 효과적 권한을 계산한다.

        역할 기본 권한에 사용자별 오버라이드를 병합한다.
        """
        base = self._permissions["role_defaults"].get(role, {})
        override = self._permissions["user_overrides"].get(user_id, {})
        merged = {**base, **override}
        return UserPermission(**merged)

    def check_db_access(
        self,
        permission: UserPermission,
        target_db: str,
    ) -> AccessCheckResult:
        """DB 접근 권한을 검증한다."""
        if permission.allowed_db_ids is not None:
            if target_db not in permission.allowed_db_ids:
                return AccessCheckResult(
                    allowed=False,
                    reason=f"DB '{target_db}'에 대한 접근 권한이 없습니다.",
                )
        return AccessCheckResult(allowed=True)

    def check_table_access(
        self,
        permission: UserPermission,
        tables: list[str],
    ) -> AccessCheckResult:
        """테이블 접근 권한을 검증한다."""
        # 블랙리스트 우선 검사
        if permission.denied_tables:
            denied = [t for t in tables if t in permission.denied_tables]
            if denied:
                return AccessCheckResult(
                    allowed=False,
                    reason=f"접근이 금지된 테이블: {', '.join(denied)}",
                )

        # 화이트리스트 검사
        if permission.allowed_table_patterns:
            for table in tables:
                if not self._matches_any(table, permission.allowed_table_patterns):
                    return AccessCheckResult(
                        allowed=False,
                        reason=f"테이블 '{table}'에 대한 접근 권한이 없습니다.",
                    )

        return AccessCheckResult(allowed=True)

    def check_feature_access(
        self,
        permission: UserPermission,
        feature: str,
    ) -> AccessCheckResult:
        """기능 접근 권한을 검증한다."""
        feature_map = {
            "query": permission.can_query,
            "upload_file": permission.can_upload_file,
            "download_file": permission.can_download_file,
            "multiturn": permission.can_use_multiturn,
            "cache_management": permission.can_manage_cache,
            "synonym_registration": permission.can_register_synonym,
        }
        allowed = feature_map.get(feature, False)
        if not allowed:
            return AccessCheckResult(
                allowed=False,
                reason=f"'{feature}' 기능에 대한 권한이 없습니다.",
            )
        return AccessCheckResult(allowed=True)

    def check_rate_limit(
        self,
        permission: UserPermission,
        user_id: str,
    ) -> AccessCheckResult:
        """시간당 질의 횟수 제한을 검증한다."""
        if permission.max_queries_per_hour is None:
            return AccessCheckResult(allowed=True)
        # 최근 1시간 내 질의 수를 감사 로그에서 조회
        ...
```

---

## 5. 파이프라인 통합

### 5.1 적용 지점

```
사용자 요청
    ↓
[API Layer]
    ├── (1) 인증 (Plan 39) → user_id, role
    ├── (2) 프롬프트 가드 → 안전성 검증
    ├── (3) 기능 권한 검사 → can_query, can_upload_file 등
    └── (4) Rate limit 검사
    ↓
[LangGraph Pipeline]
    ├── semantic_router → (5) DB 접근 권한 검사
    ├── schema_analyzer → (6) 테이블 접근 권한 검사
    ├── query_validator → (7) 행 수 제한 적용 (max_rows_per_query)
    └── output_generator → (8) 다운로드 권한 검사
```

### 5.2 API 레이어 통합

```python
# src/api/routes/query.py (수정)

@router.post("/query")
async def process_query(
    request: Request,
    current_user: dict = Depends(require_user),
    prompt_guard: PromptGuard = Depends(get_prompt_guard),
    access_control: AccessControlService = Depends(get_access_control),
):
    query_text = body.query

    # (2) 프롬프트 가드
    check = prompt_guard.check(query_text)
    if not check.is_safe:
        # 감사 로그에 보안 경고 기록
        await audit_service.log_security_alert(
            event_detail=f"프롬프트 차단: {[v.type for v in check.violations]}",
            user_id=current_user["sub"],
            client_ip=request.state.client_ip,
            severity="warning",
        )
        raise HTTPException(
            status_code=403,
            detail="요청이 보안 정책에 의해 차단되었습니다.",
        )

    # (3) 기능 권한 검사
    permission = access_control.get_user_permission(
        current_user["sub"], current_user["role"]
    )
    feature = "query"
    if body.file:
        feature = "upload_file"
    access = access_control.check_feature_access(permission, feature)
    if not access.allowed:
        raise HTTPException(status_code=403, detail=access.reason)

    # (4) Rate limit
    rate = access_control.check_rate_limit(permission, current_user["sub"])
    if not rate.allowed:
        raise HTTPException(status_code=429, detail=rate.reason)

    # State에 권한 정보 전달
    state["user_permission"] = asdict(permission)
    ...
```

### 5.3 노드 레이어 통합

```python
# src/nodes/semantic_router.py (수정 예시)

async def semantic_router(state: AgentState, config: AppConfig) -> dict:
    ...
    # DB 접근 권한 검사
    permission = UserPermission(**state.get("user_permission", {}))
    for db in target_databases:
        check = access_control.check_db_access(permission, db["db_id"])
        if not check.allowed:
            return {"error_message": check.reason, ...}
    ...
```

---

## 6. 설정

```python
# src/config.py (추가)

class AccessControlConfig(BaseSettings):
    """접근 제어 설정."""

    enabled: bool = True                      # 접근 제어 활성화
    permissions_path: str = "config/permissions.json"  # 권한 설정 파일
    prompt_guard_enabled: bool = True         # 프롬프트 가드 활성화
    rate_limit_enabled: bool = True           # Rate limit 활성화
    default_max_queries_per_hour: int = 100   # 기본 시간당 최대 질의
    default_max_rows: int = 5000              # 기본 최대 행 수

    model_config = {"env_prefix": "ACL_", "env_file": ".env", "extra": "ignore"}
```

---

## 7. 관리자 API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/v1/admin/permissions` | 전체 권한 설정 조회 |
| PUT | `/api/v1/admin/permissions/roles/{role}` | 역할 기본 권한 수정 |
| GET | `/api/v1/admin/permissions/users/{user_id}` | 사용자 권한 조회 |
| PUT | `/api/v1/admin/permissions/users/{user_id}` | 사용자 권한 수정 |
| DELETE | `/api/v1/admin/permissions/users/{user_id}` | 사용자 오버라이드 삭제 |
| GET | `/api/v1/admin/prompt-guard/rules` | 프롬프트 가드 규칙 조회 |
| PUT | `/api/v1/admin/prompt-guard/rules` | 프롬프트 가드 규칙 수정 |

---

## 8. 구현 파일 목록

| 파일 | 작업 | 계층 |
|------|------|------|
| `src/security/prompt_guard.py` | 신규 | infrastructure |
| `src/security/access_control.py` | 신규 | infrastructure |
| `src/security/access_models.py` | 신규 (PromptCheckResult, UserPermission 등) | domain |
| `src/api/routes/query.py` | 수정 (가드/ACL 적용) | interface |
| `src/api/routes/admin.py` | 수정 (권한 관리 API) | interface |
| `src/api/dependencies.py` | 수정 (가드/ACL 의존성) | interface |
| `src/nodes/semantic_router.py` | 수정 (DB 접근 검사) | application |
| `src/nodes/schema_analyzer.py` | 수정 (테이블 접근 검사) | application |
| `src/nodes/query_validator.py` | 수정 (행 수 제한) | application |
| `src/nodes/output_generator.py` | 수정 (다운로드 권한 검사) | application |
| `src/state.py` | 수정 (user_permission 필드) | domain |
| `src/config.py` | 수정 (AccessControlConfig) | config |
| `config/permissions.json` | 신규 (권한 설정 파일) | config |
| `src/static/admin/dashboard.html` | 수정 (권한 관리 UI) | static |

---

## 9. 사용자 에러 응답

접근 제어로 차단 시 사용자에게 명확한 안내를 제공한다.

```python
# 프롬프트 차단 시
{
    "error": "요청이 보안 정책에 의해 차단되었습니다.",
    "detail": "데이터 변경 요청은 이 시스템에서 지원하지 않습니다. 조회 관련 질의만 가능합니다.",
    "code": "PROMPT_BLOCKED"
}

# DB 접근 권한 없음
{
    "error": "접근 권한이 없습니다.",
    "detail": "DB 'cloud_portal'에 대한 조회 권한이 없습니다. 관리자에게 문의하세요.",
    "code": "DB_ACCESS_DENIED"
}

# Rate limit 초과
{
    "error": "요청 횟수를 초과했습니다.",
    "detail": "시간당 최대 100건의 질의가 가능합니다. 잠시 후 다시 시도해주세요.",
    "code": "RATE_LIMIT_EXCEEDED"
}
```

---

## 10. 구현 순서

1. `PromptGuard` 구현 (인젝션 패턴 + 금지 의도 감지)
2. `UserPermission`, `AccessCheckResult` 모델 정의
3. `AccessControlService` 구현 (DB/테이블/기능 접근 검사)
4. `config/permissions.json` 기본 설정 생성
5. `query.py`에 프롬프트 가드 + 접근 제어 적용
6. 노드에 DB/테이블/행 수 제한 적용
7. Rate limit 구현 (감사 로그 기반 또는 Redis 카운터)
8. 관리자 권한 관리 API 구현
9. 관리자 UI 권한 관리 탭 추가
10. 프롬프트 가드 규칙 동적 관리 기능

---

## 11. Plan 39, 40과의 연관

| 의존 관계 | 설명 |
|-----------|------|
| **Plan 39 → 41** | 사용자 인증이 있어야 사용자별 권한 적용 가능. `user_id`, `role` 필요 |
| **Plan 40 → 41** | 감사 로그가 있어야 rate limit (시간당 질의 횟수) 계산 가능 |
| **Plan 41 → 40** | 접근 제어 차단 이벤트를 감사 로그에 기록 |

**권장 구현 순서**: Plan 39 (인증) → Plan 40 (감사 로깅) → Plan 41 (접근 제어)

단, 프롬프트 가드는 인증 없이도 독립적으로 구현/적용 가능하다.
