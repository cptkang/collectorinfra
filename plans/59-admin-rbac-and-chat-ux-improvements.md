# 59. 어드민 접근 체계 정합화(RBAC·권한 상승 수정) 및 채팅 UX 개선(응답 중단·파일 첨부 표시)

> 본 문서는 세 갈래를 함께 다룬다.
> - **Part A — 어드민 접근 체계 정합화(§1~13)**: 죽은 `UserRole.ADMIN` 통합(RBAC) · 권한 상승 결함 수정 · 크레덴셜 하드닝
> - **Part B — 채팅 UX 개선(§14~17)**: 응답 중단(Stop) 버튼 · 중단 안내 문구 · 파일 첨부 표시 위치
> - **Part C — 추가 요청 항목(§18~20)**: 알림 시스템 어드민 전용화(인가, Part A와 연동) · 최초 로딩 인증 플래시·favicon 개선(UX) · 진행상황 패널 스크롤 컨트롤(UX)
>
> 세 파트는 서로 독립적으로 착수·배포 가능하다(단 §18은 Part A의 RBAC(role==admin)에 의존).
>
> 작성일: 2026-07-01
> 상위/관련 계획: `plans/39-user-authentication.md`(사용자 인증), `plans/40-audit-logging-enhancement.md`, `plans/41-prompt-access-control.md`(접근 제어)
> 관련 결정: D-039 사용자 인증 도입(추정), D-041(멀티턴 전파와 무관하나 토큰 payload 공유 맥락)
> 신규 결정(본 계획에서 부여): **D-064**(어드민 접근 판정을 DB `user.role==ADMIN` 기반 RBAC로 통합, 고정 운영자 계정은 부트스트랩 seed로 축소), **D-065**(`require_admin` 토큰 `type` 검증 + 사용자/운영자 시크릿 분리 — 권한 상승 차단, **방향과 무관한 즉시 수정**), **D-066**(최초 운영자 부트스트랩 정책 및 기본 크레덴셜 하드코딩 제거)
> ※ 번호 규칙(Known Mistakes 2026-06-25): `grep -oE "D-0[0-9]{2}" docs/02_decision.md` 현재 최댓값 **D-056**. 단, Plan 58이 **D-057~D-059를 예약**(등재 대기)했으므로 충돌 회피를 위해 본 계획은 **D-064~D-066** 부여. 구현 시 `docs/02_decision.md`에 정식 등재하며, Plan 58 등재 여부를 재확인해 번호 재조정한다.
> ※ 본 계획은 **인증 정책 변경**을 포함한다. §7 결정 선택지 중 방향을 **사용자 확인 후** 구현에 착수한다(CLAUDE.md 의사결정 규칙).

---

# Part A — 어드민 접근 체계 정합화

## 1. 배경 — 보고된 증상

현재 프로젝트는 사용자 회원가입/로그인이 구현되어 있으나, 어드민 페이지 접근 체계가 사용자 직관과 어긋난다.

| # | 증상 | 사용자 관찰 |
|---|------|------------|
| **A** | 어드민 페이지 진입에 **별도 계정 로그인**이 또 필요 | 사용자 계정으로 로그인해도 어드민 페이지는 못 들어감 |
| **B** | 어드민 페이지의 회원 리스트에서 **"어드민 권한 부여"**를 해도, 그 사용자가 어드민 페이지에 **접근되지 않음** | 권한을 줬는데 아무 효과 없음 |

분석 결과 **표면 증상(A·B) 외에 반대 방향의 권한 상승 보안 결함(C)이 추가로 발견**되었다. 본 계획은 A·B의 정합화와 C의 즉시 수정을 함께 다룬다.

---

## 2. 현재 구조(사실관계) — 두 개의 독립 인증 체계

| 구분 | 사용자(user) 인증 | 운영자(admin) 인증 |
|---|---|---|
| 코드 | `src/api/routes/user_auth.py`, `src/api/dependencies.py` | `src/api/routes/admin_auth.py` |
| 계정 저장소 | **DB** (`user_repo`, 회원가입으로 생성) | **환경변수 고정 1계정** (`AdminConfig`, `src/config.py:174-178`) |
| 기본 크레덴셜 | — | `ADMIN_USERNAME`/`ADMIN_PASSWORD` 기본값 `admin`/`admin123` |
| 로그인 | `POST /api/v1/auth/login` | `POST /api/v1/admin/login` (`admin_auth.py:161`) |
| 토큰 payload | `type:"user"`, `sub`, `name`, `role` | `type:"admin"`, `sub` |
| 서명 시크릿 | **`config.admin.jwt_secret`** (`user_auth.py:53`) | **`config.admin.jwt_secret`** (동일) |
| 보호 의존성 | `require_user` / `get_current_user` | `require_admin` (`admin_auth.py:105`) |
| 어드민 화면 | — | 정적 서빙 `/admin/login`, `/admin` (`server.py:283-291`), 클라이언트 JS가 토큰 보관 |

**핵심 사실 3가지**

1. **어드민 로그인은 DB 사용자 테이블을 보지 않는다.** `admin_login`(`admin_auth.py:161`)은 오직 `config.admin.username/password`(=`.env` 고정 운영자 계정)와만 대조한다. → **증상 A의 원인이자, 부분적으로 의도된 설계**(아래 §3).
2. **`UserRole.ADMIN`은 죽은 필드다.** `src/domain/user.py:21`에 정의만 되어 있고, 어드민 접근을 부여하는 소비 지점이 코드 전역에 **0건**(`grep UserRole.ADMIN` 결과 없음). 어드민 UI의 "권한 부여"는 `user.role`만 바꿀 뿐 어떤 게이트도 열지 않는다. → **증상 B의 원인**.
3. **사용자 토큰과 운영자 토큰이 동일 시크릿으로 서명된다.** → §4 권한 상승 결함의 전제.

---

## 3. 원인 분석 — 무엇이 의도이고 무엇이 결함인가

### 3.1 증상 A (별도 로그인) — **부분적으로 의도된 설계**

운영자 계정을 DB 사용자와 분리한 것은 합리적인 **break-glass / bootstrap 패턴**이다.

- `admin_login`은 `user_repo`에 **의존하지 않는다** → DB가 죽었거나 사용자가 한 명도 없어도 운영자가 진입해 `.env`·DB 연결·감사 로그·사용자 관리를 수행할 수 있다(`admin.py`의 관리 엔드포인트군).
- 즉 "어드민 진입에 별도 계정이 필요하다"는 것 **자체는 유지할 가치가 있는 성질**이다.

### 3.2 증상 B (권한 부여 무효) — **미완성/부정합 (수정 대상)**

`UserRole.ADMIN`은 원래 무언가를 게이팅하려던 흔적이나 현재 죽은 필드다. 어드민 UI에 "어드민 권한 부여"가 노출되는데 실제 효과가 없어 **운영자에게 명백한 오해를 유발**한다. 정합화 필요.

### 3.3 원인 요약

- A는 설계 의도(유지) + UX 혼란(정리 필요).
- B는 반쪽 구현(연결 또는 제거 필요).
- 두 체계가 **하나의 멘탈 모델로 봉합되지 않은 채** 병존하는 것이 근본 원인이다.

---

## 4. 추가 발견 — 권한 상승 보안 결함(증상 C) [확정]

사용자 관찰(B, "권한 줘도 안 됨")의 **정반대 방향**에 실제 구멍이 있다.

`require_admin`(`admin_auth.py:77-102`, `verify_admin_token`)은 토큰이 **admin 시크릿으로 서명되었고 `sub`가 존재하는지만** 검사한다. **`type=="admin"`도 `role`도 검증하지 않는다.** 그런데 사용자 토큰도 **같은 시크릿**(`config.admin.jwt_secret`)으로 서명된다(`user_auth.py:53`).

```python
# admin_auth.py — 현재
def verify_admin_token(token, secret):
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    username = payload.get("sub")          # type 미검증, role 미검증
    if username is None: raise HTTPException(401, ...)
    return username                        # ← 사용자 토큰의 sub도 그대로 통과
```

**결과**: 로그인한 **임의의 일반 사용자**가 자신의 user JWT를 `Authorization: Bearer <user_token>`로 넣으면 `require_admin`을 통과하여 **모든 `/api/v1/admin/*` 및 `schema_cache` 운영자 API**를 호출할 수 있다:

- `PUT /admin/settings`(.env 수정), `PUT /admin/db-config`(DB 접속정보 변경), `DELETE /admin/users/{id}`(사용자 삭제), `POST /admin/users/{id}/reset-password`(임시 비밀번호 발급/노출), `GET /admin/audit/*`(감사 로그 열람) 등.

프론트엔드에 별도 로그인 폼이 있을 뿐, **API 계층은 이미 뚫려 있다.** 이는 방향 선택(§7)과 **무관하게 즉시 수정해야 하는 권한 상승 취약점**이다(D-065).

> 검증 재현(구현 전 라이브 확인): `POST /api/v1/auth/login`으로 일반 사용자 토큰 발급 → 그 토큰으로 `GET /api/v1/admin/users` 호출 → 200 응답이면 취약점 확정.

---

## 5. 영향 범위(코드 인벤토리)

| 파일 | 역할 | 변경 성격 |
|------|------|-----------|
| `src/api/routes/admin_auth.py` | `verify_admin_token`/`require_admin`/`admin_login` | **핵심 수정**(type 검증, RBAC 분기, seed 로그인) |
| `src/api/dependencies.py` | `require_user`/`get_current_user` | 시크릿 분리 시 검증 시크릿 조정, `require_admin_user`(신규) 추가 후보 |
| `src/config.py` | `AdminConfig`/`AuthConfig` | 기본 크레덴셜 제거, 시크릿 분리 필드 |
| `src/api/routes/admin.py` | 15개 운영자 엔드포인트 | 의존성 교체(`require_admin`→통합 가드) |
| `src/api/routes/schema_cache.py` | 14개 운영자 엔드포인트 | 동일 의존성 교체 |
| `src/domain/user.py` | `UserRole` | 방향 A: 소비처 생김 / 방향 B: 정리 |
| `src/infrastructure/user_repository.py` | 사용자 저장소 | seed admin 부트스트랩 훅 |
| `src/static/admin/login.html`, `src/static/js/admin.js` | 어드민 화면·토큰 처리 | 로그인 흐름/라벨 정리 |
| `src/static/js/app.js` | 사용자 화면 | (방향 A) role=admin 시 어드민 링크 노출 |
| `tests/test_api/…` | 인증 테스트 | 회귀 테스트 신규 |

---

## 6. 목표

1. **[필수·즉시] 권한 상승 차단**: 사용자 토큰으로 운영자 API 접근 불가.
2. **[정합화] 증상 B 해소**: "권한 부여" UI의 동작을 실제와 일치시킨다(연결 or 제거).
3. **[하드닝] 기본 크레덴셜 하드코딩(`admin/admin123`) 제거** 및 시크릿 강제.
4. 기존 break-glass 진입성(DB 장애 시에도 운영자 진입) 보존.
5. `AUTH_ENABLED=false` 개발 모드 동작 불변(Plan 39 원칙 유지).

---

## 7. 결정 선택지 — 방향 A(권장) vs 방향 B

§4의 보안 수정(D-065)과 §목표 3(D-066)은 **양 방향 공통·무조건 수행**. 갈라지는 것은 "권한 부여" UI의 의미다.

### 방향 A — 통합 RBAC (권장, 사용자 기대와 일치)

- 어드민 접근을 **DB `user.role == ADMIN`**으로 판정. 고정 운영자 계정은 **최초 부트스트랩 seed 1개**로 축소.
- 회원 리스트에서 어드민 권한을 부여하면 **그 사용자가 즉시 어드민 페이지 접근 가능** → 증상 B가 근본 해소되고, 이미 만들어둔 UI가 비로소 동작.
- 로그인 창구를 통합(사용자 로그인 후 role=admin이면 `/admin` 접근)하거나, 어드민 화면은 유지하되 인증만 사용자 토큰+role로 판정.

**장점**: 사용자 멘탈 모델과 일치, 죽은 필드 부활, 계정 이원화 제거. **유의**: DB/`user_repo` 장애 시 진입 경로 확보를 위해 **break-glass seed admin**(env)만 예외로 남긴다(하이브리드).

### 방향 B — 분리 유지 + 정합성만 수리

- 운영자 고정 계정 분리를 유지. 대신 `UserRole.ADMIN`을 어드민 UI에서 **제거하거나** "DB 접근 범위" 용도로 라벨을 바꿔 오해를 없앤다(어드민 접근 권한과 무관함을 명시).
- 보안 수정(D-065)으로 `type=="admin"` 강제 + 시크릿 분리.

**장점**: 변경 최소, break-glass 명확. **단점**: "권한 부여로 어드민 접근"이라는 사용자 기대는 **충족 못 함**(설계 의도로 남김).

> **권장: 방향 A + 공통 보안 수정.** 단, 인증 정책은 운영 정책이므로 §11 확인 항목대로 **사용자 승인 후 착수**.

---

## 8. 구현 계획 — 공통(양 방향 무조건)

### 8.1 D-065 ①: `require_admin` 토큰 `type` 검증

```python
# admin_auth.py — verify_admin_token
payload = jwt.decode(token, secret, algorithms=["HS256"])
if payload.get("type") != "admin":                       # ← 추가
    raise HTTPException(401, "관리자 토큰이 아닙니다.")
username = payload.get("sub")
...
```
- 이 한 줄만으로도 사용자 토큰(`type:"user"`) 통과는 즉시 차단된다.

### 8.2 D-065 ②: 사용자/운영자 토큰 시크릿 분리

- `AuthConfig`에 별도 `jwt_secret`(env `AUTH_JWT_SECRET`) 도입. `_create_user_token`은 `config.auth.jwt_secret`으로 서명, `dependencies._verify_user_token`은 동일 시크릿으로 검증.
- 운영자 토큰은 `config.admin.jwt_secret` 유지. → 시크릿 분리로 교차 서명 자체가 불가.
- **마이그레이션 주의**: 배포 시점에 기존 발급된 사용자 토큰은 무효화된다(재로그인 필요) — 릴리스 노트 명시. 무중단이 필요하면 검증 단계에서 한시적으로 두 시크릿 모두 시도 후 후속 릴리스에서 제거.

### 8.3 D-066: 기본 크레덴셜·시크릿 하드닝

- `AdminConfig.username/password` 기본값 `admin`/`admin123` **제거**. 미설정 시 기동 로그 경고 또는(운영 모드에서) 기동 거부.
- `jwt_secret` 미설정 시 랜덤 생성(`config.py:184-189`)은 **다중 워커/재시작 간 토큰 불연속** 문제가 있으므로, 운영 환경에서는 **명시적 설정을 강제**(미설정 경고 강화). 개발 모드는 현행 유지.

### 8.4 회귀 테스트(필수)

- `test_admin_auth_rejects_user_token`: 사용자 토큰으로 `GET /admin/users` → **401**.
- `test_admin_auth_accepts_admin_token`: 정상 운영자 토큰 → 200.
- `test_user_secret_separated`: 사용자 토큰이 admin 시크릿으로 검증되지 않음.

---

## 9. 구현 계획 — 방향 A 전용(권장 채택 시)

### 9.1 통합 관리자 가드 신설

```python
# src/api/dependencies.py (신규 require_admin_user)
async def require_admin_user(request, authorization=Header(None)) -> dict:
    user = await require_user(request, authorization)     # 사용자 토큰 검증(+DB 실시간 role)
    if user.get("role") != UserRole.ADMIN.value:
        raise HTTPException(403, "관리자 권한이 필요합니다.")
    return user
```
- `get_current_user`/`require_user`는 이미 **DB에서 최신 role을 실시간 반영**한다(`dependencies.py:78-83`) → 권한 회수도 즉시 반영.
- `admin.py`·`schema_cache.py`의 `Depends(require_admin)` → `Depends(require_admin_user)`로 교체.

### 9.2 break-glass seed admin

- `AdminConfig`(env) 계정은 **부트스트랩 전용**으로 유지하되, 어드민 접근 판정에서는 "role=admin인 가상 사용자"로 취급. `/admin/login`은 남겨 DB 장애 시 진입 경로 확보(별도 `type:"admin"` 토큰 발급, §8.1 검증 통과).
- 애플리케이션 기동 시 사용자 테이블에 admin이 0명이면 env 기반 seed admin을 1회 생성(멱등)하는 훅을 `user_repository`/기동 시퀀스에 추가.

### 9.3 프론트

- `app.js`: `auth/status`의 `role==admin`일 때 어드민 진입 링크 노출.
- `admin/login.html`·`admin.js`: 사용자 로그인 토큰으로 어드민 진입 허용(별도 폼 유지 시 문구를 "관리자 계정으로 로그인"으로 정리). 통합 시 어드민 로그인 폼을 사용자 로그인으로 리다이렉트.

### 9.4 `UserRole.ADMIN` 소비 확정

- 이제 role이 실제 게이트를 여므로 `update_user`(`admin.py:566`)의 role 변경이 곧 어드민 접근 부여/회수가 된다. 자기 자신의 마지막 admin 권한 회수 방지(최소 1 admin 유지) 가드 추가 권장.

---

## 10. 구현 계획 — 방향 B 전용(분리 유지 채택 시)

- §8 공통 수정만 적용.
- `admin.js`/어드민 사용자 관리 화면에서 role=admin 부여 UI를 **제거**하거나, "이 role은 DB 조회 범위 표식이며 어드민 페이지 접근과 무관"이라는 안내를 명시.
- `docs/02_decision.md`에 "운영자 계정과 사용자 role은 별개 체계"를 D-064(변형)으로 확정 등재.

---

## 11. 사용자 확인 필요 항목(착수 전)

1. **방향 A(통합 RBAC) / 방향 B(분리 유지)** 중 선택. (권장: A) ==> 1안이 좋겠음. 
2. §8.2 시크릿 분리로 인한 **기존 사용자 토큰 무효화(재로그인)** 수용 여부, 또는 한시적 이중 검증 필요 여부. ==> 배포할 때마다 재로그인이 필요하다면, 상관 없음.
3. §8.3 운영 환경에서 크레덴셜·시크릿 미설정 시 **기동 거부** 강도. ==> 강한 거부 가능. 단, jwt 토큰을 추천하여 사전 환경변수에 기입해두시오. 

---

## 12. 작업 순서(제안)

1. **[P0·즉시]** §8.1 `type` 검증 한 줄 + 회귀 테스트 → 권한 상승 긴급 차단(방향 무관, 선반영 가능).
2. [P1] §8.2 시크릿 분리, §8.3 하드닝.
3. [P1] 방향 확정 후 §9(A) 또는 §10(B) 적용.
4. [P2] 프론트 정리, 최소-1-admin 가드.
5. `docs/02_decision.md`에 D-064~D-066 정식 등재(Plan 58의 D-057~059 등재 여부 재확인 후 번호 확정), CLAUDE.md Known Mistakes에 "동일 시크릿·type 미검증으로 인한 권한 상승" 교훈 기록.

---

## 13. 리스크

| 리스크 | 완화 |
|--------|------|
| 시크릿 분리로 운영 중 사용자 전원 로그아웃 | 배포창 공지 / 한시적 이중 검증 후 제거 |
| seed admin 미생성으로 어드민 진입 불가 | 기동 멱등 훅 + break-glass env 계정 병행 |
| role 실시간 반영으로 권한 회수 즉시 적용 → 운영자 자기 잠금 | 최소-1-admin 가드, self-demotion 방지 |
| `require_admin`→`require_admin_user` 일괄 교체 누락 | `Depends(require_admin` grep로 잔존 확인, 라우터 단위 테스트 |

---

# Part B — 채팅 UX 개선

> ※ 아래 §14~17은 채팅 프론트엔드 UX 개선으로 Part A(어드민 RBAC)와 주제가 다르며, Part A와 **독립적으로 착수·배포 가능**하다. UI 계층 변경이 주이므로 신규 `D-` 결정은 부여하지 않는다(정책 변경 없음).

관련 파일(공통):
- `src/static/js/app.js` — 채팅 UI/SSE 스트리밍(`executeStreamingQuery` 758~, `executeFileQuery` 1044~/1170~, `renderUserMessage` 529~, `createStreamingMessage` ~754)
- `src/static/index.html` — 입력 영역/전송 버튼(`#sendBtn` 109~114, `#attachBtn`/`#fileInput` 102~108)
- `src/static/css/style.css` — 말풍선/버튼/파일 배지 스타일
- `src/api/routes/query.py` — SSE 제너레이터(`process_query_stream` 414~, `process_file_query_stream` 796~)

---

## 14. LLM 응답 중단(Stop) 버튼

### 14.1 배경/목표

진행 중인 LLM 응답이 너무 길어질 때 사용자가 **즉시 중단**할 수 있게 한다. 다른 AI 어시스턴트처럼 **전송 버튼을 중단 버튼으로 토글**하는 방식을 채택한다(메시지 전송 후 `#sendBtn`이 정지 아이콘으로 변경, 클릭 시 진행 중 응답 중단).

### 14.2 현재 구조(사실)

- `executeStreamingQuery`(`app.js:758`)는 `fetch("/api/v1/query/stream")` 후 `response.body.getReader()` 루프(`app.js:814~`)로 SSE 토큰을 수신한다.
- 전송 중에는 `isProcessing = true; sendBtn.disabled = true`(`app.js:759-760`, 파일 경로 1044/1170도 동일)로 **버튼을 비활성화만** 한다 → **중단 수단이 없다**.
- `fetch`에 **`AbortController`가 연결돼 있지 않다** → 클라이언트가 요청을 끊을 방법이 없다.
- 백엔드 `StreamingResponse`(`query.py:669`, `1047`)의 `event_generator`는 `graph.astream_events(...)`를 `async for`로 소비한다. 클라이언트가 연결을 끊으면 Starlette가 응답 태스크를 취소 → 제너레이터에 `CancelledError`/`GeneratorExit` 전파 → `astream_events` 순회 취소로 **진행 중 LLM 호출이 중단**된다(=클라이언트 abort가 서버 취소로 이어짐). 단, 현재 코드는 이 경로를 **명시적으로 검증/정리(finally 로깅 등)하지 않는다**.

### 14.3 구현 계획

**프론트(`app.js`)**
1. 모듈 스코프에 `var currentAbortController = null;` 추가.
2. `executeStreamingQuery`/`executeFileQuery(파일 스트림)` 진입 시:
   ```js
   currentAbortController = new AbortController();
   fetch(url, { ..., signal: currentAbortController.signal });
   setSendButtonMode("stop");   // 아이콘/타이틀/상태 토글 (disabled 대신)
   ```
3. `#sendBtn` 클릭 핸들러(`handleSend`)를 모드 분기:
   - `isProcessing`이면 **중단 동작**(`currentAbortController.abort()` + `reader.cancel()` 시도) 수행하고 return.
   - 아니면 기존 전송 수행.
4. `AbortError` 처리: reader 루프의 `try/catch`에서 `err.name === "AbortError"`면 정상 중단으로 간주(에러 토스트 금지) → §15 안내 문구 렌더.
5. 종료 정리(성공/에러/중단 공통 `finally`): `isProcessing=false; currentAbortController=null; setSendButtonMode("send");`
6. `setSendButtonMode(mode)`: `#sendBtn`의 SVG를 전송(paper-plane, `index.html:110-113`) ↔ 정지(사각형/`■`)로 교체, `title`/`aria-label` 갱신, `disabled`는 **해제 상태 유지**(중단 클릭을 받아야 하므로).

**백엔드(`query.py`) — 선택적 보강(권장)**
- `event_generator`에 `try/except asyncio.CancelledError` + `finally` 로깅 추가(어느 노드에서 중단됐는지 감사/디버깅). 기능상 취소는 이미 전파되므로 **정확성 필수는 아님**.
- 조기 감지 강화가 필요하면 루프 내 `if await request.is_disconnected(): break`를 주기적으로 확인(FastAPI `Request` 주입). 과도한 호출은 피한다.

**HTML/CSS**
- `#sendBtn`에 정지 아이콘 마크업 추가(토글용) 또는 JS로 innerHTML 교체. `.input-btn--stop` 스타일(빨강 계열) 추가.

### 14.4 확인 필요 항목
- 중단 시 **이미 스트리밍된 부분 텍스트를 화면에 보존**할지(권장: 보존) vs 제거.
- 비스트리밍 폴백(`/query`, `/query/file` — `app.js:1015/1174`)에도 중단을 적용할지. `fetch` abort는 가능하나 서버 측 `asyncio.wait_for` 작업은 응답 폐기만 되고 그래프 실행은 완주할 수 있음(스트리밍 경로만큼 즉시성 없음) → 우선 스트리밍 경로에 한정 권장.

---

## 15. 응답 중단 안내 문구

### 15.1 목표
중단되면 "사용자가 응답을 중단했습니다"류 안내를 표시한다. **사용자 질문에 대한 응답 종료**이므로, 위치는 **에이전트(왼쪽) 말풍선**이 자연스럽다(검토 결과 권장).

### 15.2 구현
- 현재 스트리밍 말풍선(`createStreamingMessage`가 만든 `.message--agent`, `#streamingText`)의 **하단에 회색 시스템 안내 라인**을 append: 예) `⏹ 응답이 중단되었습니다`.
- 부분 텍스트가 있으면 그 아래에, 없으면(토큰 0개 상태에서 중단) 안내만 단독 표시.
- 스타일: `.message-interrupted-note`(작은 회색 텍스트). 타이핑 커서(`#streamingCursor`)는 제거.
- 중단은 오류가 아니므로 `showError`(빨간 토스트) 경로를 타지 않게 한다(§14.3-4).

### 15.3 검토 포인트
- 왼쪽 말풍선 하단 라인 vs 채팅 중앙 시스템 메시지(구분선형) 중 택1 — 우선 **왼쪽 말풍선 하단** 채택, 필요 시 조정.

---

## 16. 파일 첨부의 시각적 위치 개선 (카카오톡 스타일)

### 16.1 배경/목표
현재 파일을 첨부해 전송하면 첨부 배지가 **사용자 말풍선 "안"**에 들어간다. 이를 **말풍선 "위"에 별도 파일 카드**로 띄워, 메신저(카카오톡)처럼 파일과 텍스트를 시각적으로 분리한다.

### 16.2 현재 구조(사실)
`renderUserMessage`(`app.js:529-549`)가 파일 배지를 **말풍선 내부**에 넣는다:
```js
'<div class="message-bubble">' + escapeHtml(msg.content) + fileHtml + '</div>'
//                                                          ^^^^^^^^ 말풍선 안에 포함
```
`fileHtml`은 `.message-file-badge`(`app.js:537`).

### 16.3 구현 계획
- `renderUserMessage`에서 `fileHtml`을 **말풍선 밖·위로** 이동. `.message-content`(우측 정렬) 안에서 `.message-bubble`보다 **앞(위)**에 별도 `.message-file-card`로 배치:
  ```js
  '<div class="message-content">' +
      fileCardHtml +                                   // ← 말풍선 위 파일 카드
      '<div class="message-bubble">' + escapeHtml(msg.content) + '</div>' +
      '<div class="message-time">' + formatTime(msg.time) + '</div>' +
  '</div>'
  ```
- 텍스트가 비어 있고 파일만 있는 경우 빈 말풍선을 렌더하지 않도록 분기(파일 카드만 표시).
- CSS(`style.css`): `.message-file-card`를 사용자 메시지에서 **우측 정렬**, 파일 아이콘+파일명(+용량) 카드형 스타일. 기존 `.message-file-badge` 스타일은 정리/대체.
- 첨부 프리뷰(전송 전 입력창 위 프리뷰가 있다면)와 시각적 일관성 유지.

### 16.4 확인 필요 항목
- 파일 카드에 **용량/확장자 아이콘** 표기 수준(`msg.file.size`는 이미 보유, `app.js:509`).
- 다운로드/재클릭 동작 필요 여부(현재는 표시 전용).

---

## 17. Part B 작업 순서(제안)
1. §16 파일 첨부 위치(가장 독립적·저위험, 순수 프론트) → 2. §14 중단 버튼(프론트 AbortController+버튼 토글) → 3. §15 중단 안내 문구(§14에 종속) → 4. (선택) §14.3 백엔드 취소 로깅 보강.

---

# Part C — 추가 요청 항목

> ※ 아래 §18~20은 2차 요청(2026-07-01)으로 편입한다. **§18(알림 접근 제어)은 인가 변경으로 Part A(RBAC)에 속하며 `role==admin` 판정에 의존**한다. §19·§20은 프론트엔드 UX로 Part B 성격이다. §19·§20은 UI 계층 변경이 주이므로 신규 `D-` 결정을 부여하지 않으며, §18은 Part A의 D-064(RBAC)을 소비한다.

관련 파일(추가):
- `src/api/routes/alarm.py` — 알림 SSE 스트림(`alarm_notifications_stream` 754~781), `analyze-test`(`require_user` 573)
- `src/static/js/app.js` — 알림 구독(`new EventSource(...)` ~2072), 초기 인증 게이트(`checkAuthOnLoad` 48~95), 채팅 스크롤 로직(`scrollToBottomIfSticky`/`updateScrollToBottomBtn`/`isNearBottom` 1315~1352, scroll 리스너 340~343, 버튼 346~349)
- `src/static/index.html` — 진행상황 패널(`#progressPanel`/`#progressPanelBody` 128~137), 채팅 스크롤 버튼(`#scrollToBottomBtn` 76), `<head>`(favicon 링크 부재)
- `src/api/server.py` — 정적 서빙/페이지 라우트(favicon 라우트 부재)

---

## 18. 알림 시스템 어드민 전용화 (인가 변경)

### 18.1 배경/목표
현재 알림(알람) 스트림은 **웹에 접속한 모든 사용자**가 구독·수신할 수 있다. 이를 **어드민 권한 사용자만** 확인하도록 제한한다.

### 18.2 현재 구조(사실)
- SSE 엔드포인트 `GET /api/v1/alarm/notifications/stream`(`alarm.py:754-781`)은 **인증 의존성이 없다**(`request: Request`만). → 누구나 구독 가능.
- 프론트는 `new EventSource("/api/v1/alarm/notifications/stream")`(`app.js:~2072`)로 구독한다. **브라우저 `EventSource`는 `Authorization` 헤더를 실을 수 없어**, 지금 구조상 토큰 기반 인증을 붙이기 어렵다(이것이 무인증으로 열려 있는 실질적 이유).
- 반면 알람 분석 API(`analyze-test`, `alarm.py:573`)는 `require_user`로 보호된다 — 스트림만 무방비.

### 18.3 구현 계획(백엔드 = 실질 강제)
브라우저 제약상 헤더 인증이 불가하므로 **택1**:
- **(A) 쿼리 파라미터 토큰 방식(간단)**: `EventSource("/api/v1/alarm/notifications/stream?token="+userToken)`. 엔드포인트에 의존성 신설:
  ```python
  async def require_admin_sse(request: Request, token: str | None = None) -> dict:
      # 헤더가 없으면 쿼리 토큰 사용 (EventSource 제약)
      user = await _resolve_user_from_token(request, token)   # 사용자 토큰 검증(+DB role)
      if user.get("role") != UserRole.ADMIN.value:
          raise HTTPException(403, "관리자 권한이 필요합니다.")
      return user
  ```
  - 장점: 변경 최소. **단점: URL/액세스 로그에 토큰 노출** → 내부망 도구 전제에서 수용하거나, 단시간 유효 티켓(one-time SSE ticket) 발급으로 완화.
- **(B) 쿠키 기반 인증(권장·정공법)**: 로그인 시 JWT를 `HttpOnly` 쿠키로도 세팅 → `EventSource`가 쿠키를 자동 전송 → 엔드포인트가 쿠키에서 토큰 추출·검증·`role==admin` 확인. 다른 SSE(`/query/stream`)와 인증 일관성도 개선. 단점: 로그인/의존성 흐름 변경 필요.

> 어느 쪽이든 **`role==admin` 판정은 Part A의 통합 RBAC(D-064/`require_admin_user`)를 재사용**한다. Part A 방향 B(분리 유지) 선택 시엔 "운영자 토큰(type=admin)"만 허용으로 정의.

### 18.4 구현 계획(프론트)
- `checkAuthOnLoad`에서 얻은 `role`이 `admin`일 때만: 알림 UI(패널/뱃지)를 렌더하고 `EventSource`를 연다. 비어드민은 **구독 자체를 시작하지 않음**(백엔드 403과 이중 방어).
- 토큰 만료/403 시 스트림 정리(재연결 루프 방지).

### 18.5 확인 필요 항목
- 인증 방식 **(A) 쿼리 토큰 vs (B) 쿠키** 선택(권장: B, 단 구현량↑).
- "어드민 전용"의 범위: 스트림 수신만인지, 알람 분석/발송(`analyze-test`)도 어드민 전용으로 승격할지.

---

## 19. 최초 로딩 인증 플래시(FOUC) · favicon 404 개선

### 19.1 배경/증상
최초 웹 접속 시:
- 로그에 `GET /api/v1/auth/status 401`, `GET /favicon.ico 404` 출력.
- UI상 **검색(채팅) 화면이 잠깐 보였다가 로그인 창으로 전환**된다(인증 미완료 화면의 순간 노출 = FOUC).

### 19.2 원인(사실)
- `index.html`이 먼저 서빙·렌더된 뒤, `checkAuthOnLoad`(`app.js:48`)가 **비동기로** `auth/status`를 확인하고 나서야 `redirectToLogin`(`app.js:41`)으로 넘어간다 → 그 사이 채팅 화면이 노출된다.
- `auth/status`의 **401**은 무토큰이 아니라 **localStorage의 만료/무효 토큰**이 검증(`_verify_user_token`)에서 401을 유발하는 경우다(무토큰이면 `get_current_user`가 `None` 반환 → 200). 즉 **정상 흐름이지만 로그가 시끄럽고 UX 플래시**를 만든다.
- `favicon.ico` 라우트/링크가 없어 브라우저 기본 요청이 404.

### 19.3 구현 계획
- **플래시 제거(핵심)**: 앱 셸을 **인증 확정 전까지 감춘다**. `index.html`에 초기 로딩 게이트(예: `<body class="auth-pending">`로 본문 숨김 + 스플래시/스피너) → `checkAuthOnLoad` 완료 시 `auth-pending` 해제하여 노출, 미인증이면 노출 없이 곧장 리다이렉트. (서버는 토큰이 localStorage에 있어 SSR 판단 불가하므로 **클라이언트 게이트가 현실적**.)
- **favicon 404 제거**: `index.html`/`login.html`/`register.html` `<head>`에 `<link rel="icon">` 추가하고 `src/static/favicon.ico`(또는 SVG) 배치, 필요 시 `server.py`에 `/favicon.ico` 라우트. 
- **로그 소음 완화(선택)**: 만료 토큰 상황을 `auth/status`가 401 대신 **200 + `user:null`(+`token_expired:true` 힌트)** 로 응답하도록 조정하고 프론트가 그 힌트로 리다이렉트. 401→200 전환 시 기존 401 분기(`app.js:53`)와의 정합성 유지 필요.

### 19.4 확인 필요 항목
- 스플래시 형태(간단 스피너 vs 브랜드 로고)와, 인증 확정까지 **최대 대기/타임아웃** 처리.
- 만료 토큰 응답을 401 유지 vs 200+힌트로 변경할지(로그 정책).

---

## 20. 진행상황 패널 스크롤 컨트롤 (대화창과 동일)

### 20.1 배경/목표
진행상황 패널도 **대화창과 동일한 스크롤 UX**를 갖게 한다:
- 새 진행 메시지 발생 시, 스크롤이 **맨 아래에 있으면 계속 아래로 팔로잉**.
- 스크롤을 **위로 올리면 팔로잉 중단**하고, **"맨 아래로" 버튼을 활성화**(신규 출력 강조 포함).

### 20.2 현재 구조(사실)
- 대화창(`chatMessages`)에는 이미 완성된 스티키-팔로잉 로직이 있다: `stickToBottom` 상태(`app.js:201`), scroll 리스너(`340-343`), `isNearBottom`(`1315`), `scrollToBottomIfSticky`(`1343`), `updateScrollToBottomBtn`(`1320`), 전용 버튼 `#scrollToBottomBtn`(`index.html:76`, 클릭 핸들러 `346-349`).
- **진행상황 패널(`#progressPanelBody`, `index.html:137`)은 별도 스크롤 컨테이너**이지만, 위 스티키/버튼 로직이 **적용돼 있지 않다**. 노드/스테이지가 append될 때 자동 팔로잉·되돌아가기 버튼이 없다.

### 20.3 구현 계획
대화창 로직을 **진행상황 패널용으로 일반화·복제**한다(전역 단일 상태를 재사용하지 말고 패널 전용 상태를 둘 것):
1. 패널 전용 상태 `progressStickToBottom`(기본 true)과 임계값 재사용(`BOTTOM_THRESHOLD_PX`).
2. `#progressPanelBody`에 `scroll` 리스너: `progressStickToBottom = isNearBottom(progressPanelBody)`. → `isNearBottom`을 **컨테이너 인자를 받도록 리팩터**(현재 `chatMessages` 하드코딩, `app.js:1316`).
3. 진행 append 지점(`updateProcessingStage` `app.js:608`, `handleNodeStart`/`handleNodeComplete`)에서 `progressStickToBottom`이면 패널을 맨 아래로 이동.
4. 패널 내 **"맨 아래로" 버튼** 신설(`#progressScrollBtn`) + 표시/강조 토글 함수(대화창 `updateScrollToBottomBtn`의 패널판). 신규 출력 미확인 강조(`has-new`)도 동일 적용.
5. 공통화: `isNearBottom(el)`, `scrollElToBottom(el, smooth)`, `updateScrollBtn(btn, el, hasNew)`를 **컨테이너 파라미터화**하여 대화창/패널이 공유(중복 최소화).

### 20.4 확인 필요 항목
- 패널이 접힘(`panelToggle`, `app.js:335`) 상태일 때 버튼/팔로잉 처리(접힘 시 무시).
- 패널 스크롤 버튼의 위치/스타일(패널 우하단 고정 등).

---

## 21. 통합 작업 순서(제안, 전체)
1. **[P0]** §8.1 권한 상승 핫픽스(Part A) — 방향 무관 즉시.
2. [P1] Part A 방향 확정 후 §8/§9(or §10) → 그 위에서 **§18 알림 어드민 전용화**(role==admin 소비).
3. [P1] §19 초기 로딩 플래시·favicon(독립·저위험).
4. [P2] §20 진행상황 스크롤(스크롤 로직 컨테이너 파라미터화) → §16 파일 첨부 위치 → §14/§15 중단 버튼·안내.
5. `docs/02_decision.md` 등재 및 CLAUDE.md Known Mistakes 갱신(§12 참조).
