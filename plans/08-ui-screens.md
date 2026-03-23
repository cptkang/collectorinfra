# 08. UI 화면 구현 계획서

> 작성일: 2026-03-16
> 관련 기능 요건: F-16, F-17, F-18, F-19, F-20

---

## 1. 개요

사용자 화면과 운영자 화면을 FastAPI 정적 파일 서빙 + 순수 HTML/CSS/JS로 구현한다.
별도의 프론트엔드 빌드 도구(React, Vue 등) 없이 경량으로 구현하여 의존성을 최소화한다.

---

## 2. 디렉토리 구조

```
src/
├── api/
│   ├── routes/
│   │   ├── query.py          # 기존 (파일 업로드 엔드포인트 추가)
│   │   ├── health.py         # 기존
│   │   ├── admin.py          # 새로 추가 - 운영자 API
│   │   └── admin_auth.py     # 새로 추가 - 운영자 인증 API
│   ├── schemas.py            # 운영자 관련 스키마 추가
│   └── server.py             # 정적 파일 서빙 + 라우트 등록
├── static/                   # 새로 추가 - 프론트엔드 정적 파일
│   ├── index.html            # 사용자 화면
│   ├── admin/
│   │   ├── login.html        # 운영자 로그인 화면
│   │   └── dashboard.html    # 운영자 대시보드 (설정 + DB)
│   ├── css/
│   │   └── style.css         # 공통 스타일
│   └── js/
│       ├── app.js            # 사용자 화면 로직
│       └── admin.js          # 운영자 화면 로직
└── config.py                 # AdminConfig 추가
```

---

## 3. 백엔드 API 설계

### 3.1 운영자 인증 API

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| POST | `/api/v1/admin/login` | 로그인 (JWT 토큰 발급) | 불필요 |
| GET | `/api/v1/admin/me` | 현재 로그인 상태 확인 | JWT 필요 |

### 3.2 환경변수 설정 API

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/v1/admin/settings` | .env 설정값 목록 조회 | JWT 필요 |
| PUT | `/api/v1/admin/settings` | .env 설정값 수정 | JWT 필요 |

### 3.3 DB 연결 설정 API

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| GET | `/api/v1/admin/db-config` | DB 연결 정보 조회 | JWT 필요 |
| PUT | `/api/v1/admin/db-config` | DB 연결 정보 수정 | JWT 필요 |
| POST | `/api/v1/admin/db-config/test` | DB 연결 테스트 | JWT 필요 |

### 3.4 파일 업로드 API (기존 확장)

| 메서드 | 경로 | 설명 | 인증 |
|--------|------|------|------|
| POST | `/api/v1/query/file` | 양식 파일 + 질의 처리 | 불필요 |

---

## 4. 구현 상세

### 4.1 운영자 인증 (admin_auth.py)

- 환경변수: `ADMIN_USERNAME`, `ADMIN_PASSWORD`
- JWT 토큰: `python-jose` 또는 `PyJWT` 사용
- 토큰 만료: 24시간
- 비밀키: `ADMIN_JWT_SECRET` 환경변수 (없으면 자동 생성)

```python
# 인증 의존성
async def require_admin(authorization: str = Header(...)) -> str:
    """JWT 토큰을 검증하고 관리자 사용자명을 반환한다."""
    token = authorization.replace("Bearer ", "")
    payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    return payload["sub"]
```

### 4.2 환경변수 설정 (admin.py)

- .env 파일을 파싱하여 키-값 목록으로 반환
- 민감 키워드(PASSWORD, SECRET, KEY, TOKEN) 포함 값은 마스킹
- 수정 시 .env 파일을 직접 업데이트
- `load_config` 캐시 무효화

### 4.3 DB 연결 설정 (admin.py)

- 입력 필드: db_type, host, port, database, username, password
- 연결 문자열 자동 생성: `{db_type}://{username}:{password}@{host}:{port}/{database}`
- 저장 시 .env의 `DB_CONNECTION_STRING` 업데이트 + dbhub.toml 재생성
- 연결 테스트: 실제 DB 연결 시도 후 성공/실패 반환

### 4.4 사용자 Web UI (index.html + app.js)

- 반응형 레이아웃 (모바일 지원)
- 프롬프트 입력: textarea (여러 줄)
- 파일 첨부: 드래그앤드롭 + 클릭 선택, .xlsx/.docx만 허용
- 실행 버튼 클릭 시:
  - 파일 없으면 `/api/v1/query` POST
  - 파일 있으면 `/api/v1/query/file` POST (multipart/form-data)
- 로딩 인디케이터: 스피너 + 진행 메시지
- 결과 표시: 마크다운 또는 일반 텍스트
- 파일 다운로드: `/api/v1/query/{id}/download` 링크

### 4.5 운영자 Web UI (login.html + dashboard.html + admin.js)

- 로그인 폼: ID/PW 입력 -> JWT 토큰 발급 -> localStorage 저장
- 대시보드 탭 구성:
  1. 환경변수 설정 탭
  2. DB 연결 설정 탭
- 인증 체크: 페이지 로드 시 JWT 유효성 확인, 만료 시 로그인 리디렉션

---

## 5. config.py 변경사항

```python
class AdminConfig(BaseSettings):
    """운영자 인증 설정."""
    username: str = "admin"
    password: str = "admin123"    # 운영 시 반드시 변경
    jwt_secret: str = ""          # 비어있으면 자동 생성
    jwt_expire_hours: int = 24

    model_config = {"env_prefix": "ADMIN_", "env_file": ".env", "extra": "ignore"}
```

AppConfig에 `admin: AdminConfig = AdminConfig()` 추가.

---

## 6. server.py 변경사항

```python
from fastapi.staticfiles import StaticFiles

# 라우트 등록
application.include_router(admin_auth.router, prefix="/api/v1", tags=["admin-auth"])
application.include_router(admin.router, prefix="/api/v1", tags=["admin"])

# 정적 파일 서빙 (라우트 등록 후에 마운트)
application.mount("/static", StaticFiles(directory="src/static"), name="static")

# HTML 페이지 라우트
@application.get("/")
async def user_page():
    return FileResponse("src/static/index.html")

@application.get("/admin/login")
async def admin_login_page():
    return FileResponse("src/static/admin/login.html")

@application.get("/admin")
async def admin_dashboard_page():
    return FileResponse("src/static/admin/dashboard.html")
```

---

## 7. query.py 파일 업로드 엔드포인트 추가

기존 query.py에 `/api/v1/query/file` POST 엔드포인트를 추가한다.
plans/06-api-server.md의 3.2절 설계를 따른다.

---

## 8. 의존성 추가

| 패키지 | 용도 |
|--------|------|
| `PyJWT` | JWT 토큰 생성/검증 |
| `python-multipart` | 파일 업로드 (FastAPI) |

---

## 9. 구현 순서

1. `src/config.py`에 `AdminConfig` 추가
2. `src/api/routes/admin_auth.py` 인증 API 구현
3. `src/api/routes/admin.py` 설정/DB API 구현
4. `src/api/routes/query.py` 파일 업로드 엔드포인트 추가
5. `src/api/server.py` 라우트 등록 + 정적 파일 서빙
6. `src/static/` 프론트엔드 파일 작성
7. 통합 테스트
