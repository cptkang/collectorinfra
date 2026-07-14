# 59. 어드민 접근 체계 정합화(RBAC) 및 채팅 UX 개선

> 본 문서는 세 갈래를 다룬다. 세 파트는 서로 독립적으로 착수·배포 가능하다(단 Part C는 Part A의 RBAC에 의존).
>
> - **Part A — 어드민 접근 체계 정합화(§1~10)**: 죽은 `UserRole.ADMIN` 통합(RBAC) · 권한 상승 보안 결함 수정 · 크레덴셜 하드닝
> - **Part B — 채팅 UX 개선(§11~16)**: 응답 중단(Stop) 버튼 · 중단 안내 문구 · 다운로드 버튼 간격 · 첨부 파일 카드+원본 다운로드 · 최초 로딩 플래시/favicon · 진행상황 패널 스크롤
> - **Part C — 알림 인가(§17)**: 지역 스코프 RBAC(관리자/공동존 운영자/은행존 운영자/일반, 중복 할당) · 알림 구독 존 필터 · 수신 토글 — **Part A의 통합 RBAC를 소비**
>
> 작성일: 2026-07-01 · 최종 개정: 2026-07-13(3차 요청 편입 및 확정 사항 반영·문서 정돈)
> 상위/관련 계획: `plans/39-user-authentication.md`(사용자 인증), `plans/40-audit-logging-enhancement.md`, `plans/41-prompt-access-control.md`(접근 제어)

---

## 0. 확정 사항 요약(한눈에)

여러 차례 사용자 확인을 거쳐 아래가 **확정**되었다. 본문은 이 확정 상태를 전제로 기술한다(대체된 대안·미결 선택지는 문서에서 제거).

| 항목 | 확정 내용 | 근거 |
|------|-----------|------|
| **RBAC 방향** | **통합 RBAC(방향 A)** 채택 — 어드민 접근을 DB `user.role==ADMIN`으로 판정 | §7, 사용자 회신("1안이 좋겠음") |
| **시크릿 분리** | 사용자/운영자 토큰 시크릿 분리 채택. **배포 시 재로그인 수용**(한시적 이중 검증 불필요) | §8.2, 사용자 회신 |
| **크레덴셜 하드닝** | 운영 환경에서 크레덴셜·시크릿 미설정 시 **강한 기동 거부**. 단 **JWT 시크릿은 사전 환경변수 기입 권장** | §8.3, 사용자 회신 |
| **알림 인가** | 단일 "어드민 전용"이 아니라 **지역 스코프 다중 역할**(관리자 전존 / 공동존·은행존 운영자 / 일반 미수신), 중복 할당 가능 | §17 |
| **첨부 파일 표시** | 첨부를 **말풍선 "위" 카드**로 분리 + **클릭 시 원본 양식 다운로드** | §14 |

**신규 결정(D-번호, 등재 완료 2026-07-14)**: **D-069**(통합 RBAC), **D-070**(토큰 `type` 검증 + 시크릿 분리 — 권한 상승 차단), **D-071**(기본 크레덴셜·시크릿 하드닝), **D-072**(지역 스코프 알림 RBAC + 쿠키 SSE 인증). ※ 최초 제안 번호(D-064~066)는 폼필 작업이 선점하여 등재 직전 grep으로 D-069~072 재부여(§20).

**보안 핫픽스 우선순위**: §4의 권한 상승 결함은 **방향 선택과 무관하게 즉시 수정**해야 한다(§8.1의 한 줄 + 회귀 테스트를 P0로 선반영).

---

# Part A — 어드민 접근 체계 정합화(통합 RBAC)

## 1. 배경 — 보고된 증상

사용자 회원가입/로그인은 구현되어 있으나, 어드민 페이지 접근 체계가 사용자 직관과 어긋난다.

| # | 증상 | 사용자 관찰 |
|---|------|------------|
| **A** | 어드민 페이지 진입에 **별도 계정 로그인**이 또 필요 | 사용자 계정으로 로그인해도 어드민 페이지는 못 들어감 |
| **B** | 회원 리스트에서 **"어드민 권한 부여"**를 해도 그 사용자가 어드민 페이지에 **접근되지 않음** | 권한을 줬는데 아무 효과 없음 |

분석 결과 표면 증상(A·B) 외에 **반대 방향의 권한 상승 보안 결함(C)**이 추가로 발견되었다(§4). 본 계획은 A·B 정합화와 C의 즉시 수정을 함께 다룬다.

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

1. **어드민 로그인은 DB 사용자 테이블을 보지 않는다.** `admin_login`(`admin_auth.py:161`)은 오직 `config.admin.username/password`(`.env` 고정 운영자 계정)와만 대조한다. → **증상 A의 원인이자 부분적으로 의도된 설계**(§3.1).
2. **`UserRole.ADMIN`은 죽은 필드다.** `src/domain/user.py:21`에 정의만 있고 어드민 접근을 부여하는 소비 지점이 코드 전역에 **0건**. 어드민 UI의 "권한 부여"는 `user.role`만 바꿀 뿐 어떤 게이트도 열지 않는다. → **증상 B의 원인**.
3. **사용자 토큰과 운영자 토큰이 동일 시크릿으로 서명된다.** → §4 권한 상승 결함의 전제.

## 3. 원인 분석 — 무엇이 의도이고 무엇이 결함인가

- **증상 A(별도 로그인) — 부분적으로 의도된 설계.** 운영자 계정을 DB 사용자와 분리한 것은 합리적인 **break-glass / bootstrap 패턴**이다. `admin_login`은 `user_repo`에 의존하지 않아 DB가 죽었거나 사용자가 한 명도 없어도 운영자가 진입해 `.env`·DB 연결·감사 로그·사용자 관리를 수행할 수 있다. 즉 "어드민 진입에 별도 계정이 필요"한 성질 **자체는 유지할 가치**가 있다(→ break-glass seed로 축소해 보존, §9.2).
- **증상 B(권한 부여 무효) — 미완성/부정합(수정 대상).** `UserRole.ADMIN`은 무언가를 게이팅하려던 흔적이나 현재 죽은 필드다. "어드민 권한 부여" UI가 노출되는데 효과가 없어 운영자에게 명백한 오해를 유발한다.
- **근본 원인:** 두 체계가 **하나의 멘탈 모델로 봉합되지 않은 채** 병존한다.

## 4. 권한 상승 보안 결함(증상 C) [P0·즉시]

사용자 관찰(B, "권한 줘도 안 됨")의 **정반대 방향**에 실제 구멍이 있다.

`require_admin`(`admin_auth.py:77-102`, `verify_admin_token`)은 토큰이 **admin 시크릿으로 서명되었고 `sub`가 존재하는지만** 검사한다. **`type=="admin"`도 `role`도 검증하지 않는다.** 그런데 사용자 토큰도 **같은 시크릿**(`config.admin.jwt_secret`)으로 서명된다(`user_auth.py:53`).

```python
# admin_auth.py — 현재(취약)
def verify_admin_token(token, secret):
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    username = payload.get("sub")          # type 미검증, role 미검증
    if username is None: raise HTTPException(401, ...)
    return username                        # ← 사용자 토큰의 sub도 그대로 통과
```

**결과**: 로그인한 **임의의 일반 사용자**가 자신의 user JWT를 `Authorization: Bearer <user_token>`로 넣으면 `require_admin`을 통과하여 **모든 `/api/v1/admin/*` 및 `schema_cache` 운영자 API**를 호출할 수 있다 — `PUT /admin/settings`(.env 수정), `PUT /admin/db-config`(DB 접속정보 변경), `DELETE /admin/users/{id}`, `POST /admin/users/{id}/reset-password`, `GET /admin/audit/*` 등. 프론트에 별도 로그인 폼이 있을 뿐 **API 계층은 이미 뚫려 있다.**

> **검증 재현(구현 전 라이브 확인)**: `POST /api/v1/auth/login`으로 일반 사용자 토큰 발급 → 그 토큰으로 `GET /api/v1/admin/users` 호출 → 200 응답이면 취약점 확정.

이는 방향 선택과 무관한 즉시 수정 대상이다(D-065). 수정은 §8.1.

## 5. 영향 범위(코드 인벤토리)

| 파일 | 역할 | 변경 성격 |
|------|------|-----------|
| `src/api/routes/admin_auth.py` | `verify_admin_token`/`require_admin`/`admin_login` | **핵심 수정**(type 검증, seed 로그인) |
| `src/api/dependencies.py` | `require_user`/`get_current_user` | 시크릿 분리 검증, `require_admin_user`(신규) 추가 |
| `src/config.py` | `AdminConfig`/`AuthConfig` | 기본 크레덴셜 제거, 시크릿 분리 필드 |
| `src/api/routes/admin.py` | 15개 운영자 엔드포인트 | 의존성 교체(`require_admin`→`require_admin_user`) |
| `src/api/routes/schema_cache.py` | 14개 운영자 엔드포인트 | 동일 의존성 교체 |
| `src/domain/user.py` | `UserRole` | 소비처 신설(role이 게이트를 여는 실 의미 부여) |
| `src/infrastructure/user_repository.py` | 사용자 저장소 | seed admin 부트스트랩 훅 |
| `src/static/admin/login.html`, `src/static/js/admin.js` | 어드민 화면·토큰 처리 | 로그인 흐름/라벨 정리 |
| `src/static/js/app.js` | 사용자 화면 | role=admin 시 어드민 링크 노출 |
| `tests/test_api/…` | 인증 테스트 | 회귀 테스트 신규 |

## 6. 목표

1. **[필수·즉시] 권한 상승 차단**: 사용자 토큰으로 운영자 API 접근 불가.
2. **[정합화] 증상 B 해소**: "권한 부여" UI 동작을 실제와 일치(연결).
3. **[하드닝] 기본 크레덴셜(`admin/admin123`) 제거** 및 시크릿 강제.
4. 기존 break-glass 진입성(DB 장애 시에도 운영자 진입) 보존.
5. `AUTH_ENABLED=false` 개발 모드 동작 불변(Plan 39 원칙 유지).

## 7. 채택 방향 — 통합 RBAC(확정)

**어드민 접근을 DB `user.role == ADMIN`으로 판정**한다. 고정 운영자 계정은 **최초 부트스트랩 seed 1개**로 축소한다.

- 회원 리스트에서 어드민 권한을 부여하면 **그 사용자가 즉시 어드민 페이지 접근 가능** → 증상 B가 근본 해소되고, 이미 만들어둔 UI가 비로소 동작한다(죽은 필드 부활).
- 어드민 화면은 유지하되 인증만 **사용자 토큰+role**로 판정(사용자 로그인 후 role=admin이면 `/admin` 접근). 계정 이원화가 사라져 사용자 멘탈 모델과 일치.
- **break-glass 보존**: DB/`user_repo` 장애 시 진입 경로 확보를 위해 **seed admin(env)만 예외**로 남긴다(하이브리드, §9.2).

§4 보안 수정(D-065)과 §6-3 하드닝(D-066)은 방향과 무관하게 무조건 수행하며(§8), 그 위에 통합 RBAC(D-064)를 얹는다(§9).

## 8. 구현 계획 — 공통 보안·하드닝(무조건)

### 8.1 D-065 ①: `require_admin` 토큰 `type` 검증 [P0]

```python
# admin_auth.py — verify_admin_token
payload = jwt.decode(token, secret, algorithms=["HS256"])
if payload.get("type") != "admin":                       # ← 추가
    raise HTTPException(401, "관리자 토큰이 아닙니다.")
username = payload.get("sub")
...
```
- 이 한 줄만으로 사용자 토큰(`type:"user"`) 통과가 즉시 차단된다. §8.4 회귀 테스트와 함께 **선반영 가능**.

### 8.2 D-065 ②: 사용자/운영자 토큰 시크릿 분리

- `AuthConfig`에 별도 `jwt_secret`(env `AUTH_JWT_SECRET`) 도입. `_create_user_token`은 `config.auth.jwt_secret`으로 서명, `dependencies._verify_user_token`은 동일 시크릿으로 검증. 운영자 토큰은 `config.admin.jwt_secret` 유지 → 교차 서명 자체가 불가.
- **마이그레이션(확정)**: 배포 시점에 기존 사용자 토큰은 무효화된다(재로그인 필요). **사용자 확인 결과 배포마다 재로그인 수용** → 한시적 이중 검증은 도입하지 않는다. 릴리스 노트에 명시.

### 8.3 D-066: 기본 크레덴셜·시크릿 하드닝

- `AdminConfig.username/password` 기본값 `admin`/`admin123` **제거**. 운영 모드에서 미설정 시 **기동 거부**(확정, 강한 거부).
- `jwt_secret` 미설정 시 랜덤 생성(`config.py:184-189`)은 다중 워커/재시작 간 토큰 불연속 문제가 있으므로, 운영 환경에서는 **명시적 설정을 강제**한다. **JWT 시크릿은 사전에 환경변수(`ADMIN_JWT_SECRET`/`AUTH_JWT_SECRET`)로 기입**하도록 배포 가이드에 명시(사용자 요청). 개발 모드는 현행 유지.

### 8.4 회귀 테스트(필수)

- `test_admin_auth_rejects_user_token`: 사용자 토큰으로 `GET /admin/users` → **401**.
- `test_admin_auth_accepts_admin_token`: 정상 운영자 토큰 → 200.
- `test_user_secret_separated`: 사용자 토큰이 admin 시크릿으로 검증되지 않음.

## 9. 구현 계획 — 통합 RBAC(D-064)

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
- 애플리케이션 기동 시 사용자 테이블에 admin이 0명이면 env 기반 seed admin을 1회 생성(**멱등**)하는 훅을 `user_repository`/기동 시퀀스에 추가.

### 9.3 프론트

- `app.js`: `auth/status`의 `role==admin`일 때 어드민 진입 링크 노출.
- `admin/login.html`·`admin.js`: 사용자 로그인 토큰으로 어드민 진입 허용. 별도 폼 유지 시 문구를 "관리자 계정으로 로그인"으로 정리하거나, 어드민 로그인 폼을 사용자 로그인으로 리다이렉트.

### 9.4 `UserRole.ADMIN` 소비 확정

- 이제 role이 실제 게이트를 여므로 `update_user`(`admin.py:566`)의 role 변경이 곧 어드민 접근 부여/회수가 된다. **최소 1 admin 유지 가드**(자기 자신의 마지막 admin 권한 회수 방지)를 추가한다.

## 10. 리스크

| 리스크 | 완화 |
|--------|------|
| 시크릿 분리로 운영 중 사용자 전원 로그아웃 | 배포창 공지(재로그인 수용 확정) |
| seed admin 미생성으로 어드민 진입 불가 | 기동 멱등 훅 + break-glass env 계정 병행 |
| role 실시간 반영으로 권한 회수 즉시 적용 → 운영자 자기 잠금 | 최소-1-admin 가드, self-demotion 방지 |
| `require_admin`→`require_admin_user` 일괄 교체 누락 | `Depends(require_admin` grep로 잔존 확인, 라우터 단위 테스트 |

---

# Part B — 채팅 UX 개선

> ※ Part B는 채팅 프론트엔드 UX 개선으로 Part A와 독립적으로 착수·배포 가능하다. UI 계층 변경이 주이므로 신규 `D-` 결정은 부여하지 않는다(§14의 원본 다운로드만 백엔드 소량 변경 동반). 단일/스트리밍 두 경로에 **동일하게 적용됐는지 실제 화면으로 확인**할 것(경로 비대칭 주의).

**관련 파일(공통)**
- `src/static/js/app.js` — 채팅 UI/SSE(`executeStreamingQuery` 758~, `executeFileQuery` 1044~/1170~, `renderUserMessage` 529~549, `createStreamingMessage` ~754, 응답 버튼 렌더 717~718 / 943~967, 스크롤 로직 1315~1352·리스너 340~343)
- `src/static/index.html` — 입력 영역/전송 버튼(`#sendBtn` 109~114, `#attachBtn`/`#fileInput` 102~108), 진행상황 패널(`#progressPanel`/`#progressPanelBody` 128~137), `#scrollToBottomBtn` 76, `<head>`(favicon 링크 부재)
- `src/static/css/style.css` — 말풍선/버튼/파일 배지 스타일(`.message-download` 1133~, `--csv` 1171~, `.mapping-report-actions` 1164~, `.message-file-badge`)
- `src/api/routes/query.py` — SSE 제너레이터(`process_query_stream` 414~, `process_file_query_stream` 796~), 파일 질의 저장(`_store_result` 792~797), 다운로드 엔드포인트(`/download` 1225, `/download-csv` 1253)
- `src/api/server.py` — 정적 서빙/페이지 라우트(favicon 라우트 부재)

## 11. 응답 중단(Stop) 버튼

### 11.1 목표
진행 중인 LLM 응답이 너무 길어질 때 사용자가 **즉시 중단**할 수 있게 한다. 다른 AI 어시스턴트처럼 **전송 버튼을 중단 버튼으로 토글**한다(메시지 전송 후 `#sendBtn`이 정지 아이콘으로 변경, 클릭 시 진행 중 응답 중단).

### 11.2 현재 구조(사실)
- `executeStreamingQuery`(`app.js:758`)는 `fetch("/api/v1/query/stream")` 후 `response.body.getReader()` 루프(`app.js:814~`)로 SSE 토큰을 수신한다.
- 전송 중에는 `isProcessing = true; sendBtn.disabled = true`(`app.js:759-760`, 파일 경로 1044/1170 동일)로 **버튼을 비활성화만** 한다 → **중단 수단이 없다**.
- `fetch`에 **`AbortController`가 연결돼 있지 않다** → 클라이언트가 요청을 끊을 방법이 없다.
- 백엔드 `StreamingResponse`(`query.py:669`, `1047`)의 `event_generator`는 `graph.astream_events(...)`를 `async for`로 소비한다. 클라이언트가 연결을 끊으면 Starlette가 응답 태스크를 취소 → 제너레이터에 `CancelledError`/`GeneratorExit` 전파 → 진행 중 LLM 호출이 중단된다(클라이언트 abort → 서버 취소). 단, 현재 코드는 이 경로를 명시적으로 검증/정리(finally 로깅)하지 않는다.

### 11.3 구현 계획

**프론트(`app.js`)**
1. 모듈 스코프에 `var currentAbortController = null;` 추가.
2. `executeStreamingQuery`/`executeFileQuery(파일 스트림)` 진입 시:
   ```js
   currentAbortController = new AbortController();
   fetch(url, { ..., signal: currentAbortController.signal });
   setSendButtonMode("stop");   // 아이콘/타이틀/상태 토글 (disabled 대신)
   ```
3. `#sendBtn` 클릭 핸들러(`handleSend`)를 모드 분기: `isProcessing`이면 **중단 동작**(`currentAbortController.abort()` + `reader.cancel()` 시도) 후 return, 아니면 기존 전송.
4. `AbortError` 처리: reader 루프 `try/catch`에서 `err.name === "AbortError"`면 정상 중단으로 간주(에러 토스트 금지) → §12 안내 문구 렌더.
5. 종료 정리(성공/에러/중단 공통 `finally`): `isProcessing=false; currentAbortController=null; setSendButtonMode("send");`
6. `setSendButtonMode(mode)`: `#sendBtn`의 SVG를 전송(paper-plane, `index.html:110-113`) ↔ 정지(`■`)로 교체, `title`/`aria-label` 갱신, `disabled`는 **해제 유지**(중단 클릭을 받아야 함).

**백엔드(`query.py`) — 선택적 보강(권장)**
- `event_generator`에 `try/except asyncio.CancelledError` + `finally` 로깅 추가(어느 노드에서 중단됐는지 감사/디버깅). 기능상 취소는 이미 전파되므로 정확성 필수는 아님.
- 조기 감지 강화가 필요하면 루프 내 `if await request.is_disconnected(): break`를 주기적으로 확인(과도한 호출은 피함).

**HTML/CSS**
- `#sendBtn`에 정지 아이콘 마크업 추가(또는 JS로 innerHTML 교체). `.input-btn--stop` 스타일(빨강 계열) 추가.

### 11.4 확인 필요 항목
- 중단 시 **이미 스트리밍된 부분 텍스트를 화면에 보존**할지(권장: 보존) vs 제거.
- 비스트리밍 폴백(`/query`, `/query/file` — `app.js:1015/1174`)에도 중단을 적용할지. `fetch` abort는 가능하나 서버 측 `asyncio.wait_for` 작업은 응답 폐기만 되고 그래프 실행은 완주할 수 있음 → **우선 스트리밍 경로에 한정 권장**.

## 12. 응답 중단 안내 문구

### 12.1 목표
중단되면 "사용자가 응답을 중단했습니다"류 안내를 표시한다. 사용자 질문에 대한 응답 종료이므로 위치는 **에이전트(왼쪽) 말풍선**이 자연스럽다.

### 12.2 구현
- 현재 스트리밍 말풍선(`createStreamingMessage`가 만든 `.message--agent`, `#streamingText`) **하단에 회색 시스템 안내 라인**을 append: 예) `⏹ 응답이 중단되었습니다`.
- 부분 텍스트가 있으면 그 아래에, 없으면(토큰 0개 상태에서 중단) 안내만 단독 표시.
- 스타일: `.message-interrupted-note`(작은 회색 텍스트). 타이핑 커서(`#streamingCursor`)는 제거.
- 중단은 오류가 아니므로 `showError`(빨간 토스트) 경로를 타지 않게 한다(§11.3-4).
- (검토) 왼쪽 말풍선 하단 라인 vs 채팅 중앙 시스템 메시지 중 **왼쪽 말풍선 하단** 채택, 필요 시 조정.

## 13. 다운로드 버튼(엑셀·CSV) 간격 개선

### 13.1 증상
양식 파일 업로드 결과 응답 하단의 **"〜 다운로드"(엑셀/양식)** 버튼과 **"CSV 다운로드"** 버튼이 **가로로 딱 붙어** 있어 오클릭·시각적 답답함이 있다.

### 13.2 현재 구조(사실)
- 두 버튼은 `.message-bubble` 안에 `downloadHtml + csvHtml`로 연속 append된다(`app.js:717-718`, 스트리밍 경로 943~967 동일).
- 두 버튼 모두 `.message-download`(+`--csv`)로 `display:inline-flex; margin-top:10px`만 있고(`style.css:1133-1147, 1171`), **버튼 사이 가로 간격(gap/margin)이 없다**.
- 반면 매핑 보고서 버튼군은 이미 `.mapping-report-actions { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap }`(`style.css:1164-1169`) 래퍼로 간격을 확보한다 → **동일 패턴 재사용**.

### 13.3 구현 계획(권장 A)
- 다운로드/CSV 버튼을 `.message-download-actions`(신규, `.mapping-report-actions`와 동일한 `flex; gap:8~12px; margin-top:10px; flex-wrap:wrap`) **래퍼로 감싼다**. `app.js`의 두 경로(비스트리밍 710~720, 스트리밍 943~967)에서 `downloadHtml`·`csvHtml`을 래퍼 안에 배치.
  - 스트리밍 경로는 현재 `insertAdjacentHTML("beforeend", ...)`로 개별 append하므로, 래퍼 div를 먼저 삽입하고 그 안에 넣거나 두 버튼을 한 번에 조립해 삽입.
- (최소 변경 대안 B) 래퍼 없이 CSS만 — `.message-download--csv { margin-left: 8px; }`. 가장 작은 diff지만 줄바꿈 시 정렬이 어색할 수 있어 A 권장.

### 13.4 확인 필요 항목
- 버튼 간격 값(8px vs 12px)과 좁은 화면에서 `flex-wrap` 줄바꿈 허용 여부.

## 14. 첨부 파일 카드 + 클릭 시 원본 다운로드 (카카오톡 스타일)

### 14.1 목표
1. 파일 첨부 후 텍스트를 입력해 질의하면 현재 첨부 배지가 **사용자 말풍선 "안"**에 들어가 파일이 말풍선에 포함된 것처럼 보인다. 이를 **메신저(카카오톡)처럼 말풍선 "위"의 별도 파일 카드**로 분리한다.
2. 추가로 **첨부 파일 카드를 클릭하면 사용자가 올린 원본 양식 파일을 그대로 다운로드**할 수 있게 한다.

### 14.2 현재 구조(사실)
- `renderUserMessage`(`app.js:529-549`)가 `fileHtml`(`.message-file-badge`, 537)을 **말풍선 내부**(`'<div class="message-bubble">' + escapeHtml(msg.content) + fileHtml + '</div>'`, 543)에 넣는다.
- 사용자 메시지의 파일 객체는 **`{name, size}`만 보유**(`app.js:509`) — **원본 파일 바이트나 다운로드 URL이 없다**.
- 백엔드는 업로드 원본(`file_bytes`)을 그래프 입력(`create_initial_state(uploaded_file=file_bytes …)`, `query.py:750/853`)으로만 전달하고, **`_store_result`에는 저장하지 않는다**(`query.py:792-797`은 `output_file`/`mapping_report_md`/`query_results`만 보관). 다운로드 엔드포인트(`/query/{id}/download` 1225)는 **생성 결과 파일**을 서빙하지 **원본 양식을 서빙하지 않는다**. → 원본 다운로드를 위해선 **원본 보관 + 신규 엔드포인트**가 필요.

### 14.3 구현 계획 — (1) 말풍선 위 카드 (프론트)
- `renderUserMessage`에서 `fileHtml`을 말풍선 밖·위로 이동. `.message-content` 안에서 `.message-bubble`보다 **앞(위)**에 `.message-file-card`로 배치:
  ```js
  '<div class="message-content">' +
      fileCardHtml +                                    // ← 말풍선 위 파일 카드
      '<div class="message-bubble">' + escapeHtml(msg.content) + '</div>' +
      '<div class="message-time">' + formatTime(msg.time) + '</div>' +
  '</div>'
  ```
- 텍스트가 비어 있고 파일만 있으면 **빈 말풍선을 렌더하지 않는다**(파일 카드만).
- CSS: `.message-file-card`를 사용자 메시지에서 **우측 정렬**, 파일 아이콘+파일명(+용량) 카드형. 기존 `.message-file-badge`는 정리/대체.

### 14.4 구현 계획 — (2) 클릭 시 원본 다운로드 (백엔드+프론트)

**백엔드(원본 보관·서빙)**
1. 파일 질의 처리 시 **업로드 원본을 `query_id`로 보관**한다. `_store_result`(`query.py:792`, 파일 스트림 경로 853 이후 저장부)에 `"uploaded_file": file_bytes`, `"uploaded_file_name": file.filename` 추가.
   - **주의(메모리)**: `_results_store`는 인메모리 dict다. 원본까지 보관하면 사용량이 커지므로 **TTL/최대 건수 정리**(기존 보관 정책 준용, 없으면 도입)와 함께 적용. 대안: 디스크/오브젝트 스토리지 임시 저장 후 경로만 보관.
2. 신규 엔드포인트 `GET /api/v1/query/{query_id}/attachment`: `stored.get("uploaded_file")` 바이트를 `Content-Disposition: attachment; filename="<원본명>"`로 서빙(확장자별 content-type — `.xlsx`/`.docx`). 없으면 404. (기존 `/download` 1225 로직 재사용.)
   - **접근 제어**: 파일 질의는 `require_user`로 보호되므로 원본 다운로드도 최소 로그인 사용자로 제한하고, 가능하면 **본인 `query_id`만**(소유자 user_id 확인) 접근 + 감사 로그.

**프론트(카드에 다운로드 링크 연결)**
3. `renderUserMessage` 시점엔 `query_id`가 없으므로(전송 직후), 응답 수신 후 `query_id`로 **카드에 `href`를 사후 주입**한다(카드 DOM에 데이터 속성으로 임시 마킹 → 응답의 `query_id`로 링크 확정). (대안: 파일 전송을 먼저 등록해 upload id 선발급 — 변경량이 커서 사후 주입 권장.)
4. 파일 카드를 `<a class="message-file-card" href="/api/v1/query/<id>/attachment">`(또는 클릭 핸들러)로 만들어 클릭 시 원본 다운로드. hover 시 다운로드 아이콘 힌트 추가.

### 14.5 확인 필요 항목
- 원본 보관 위치(**인메모리 vs 디스크/스토리지**)와 **보관 기간(TTL)** — 인메모리면 서버 재시작/다중 워커 간 유실 가능. 다중 워커 환경이면 공유 스토리지 권장.
- 원본 다운로드 접근 범위(로그인 사용자 전체 vs 본인 `query_id`만) — 권장: 본인만 + 감사 로그.
- 파일 카드 용량/확장자 아이콘 표기 수준(`msg.file.size` 보유, `app.js:509`).

## 15. 최초 로딩 인증 플래시(FOUC)·favicon 404 개선

### 15.1 증상
최초 웹 접속 시 로그에 `GET /api/v1/auth/status 401`, `GET /favicon.ico 404`가 출력되고, UI상 **검색(채팅) 화면이 잠깐 보였다가 로그인 창으로 전환**된다(인증 미완료 화면의 순간 노출 = FOUC).

### 15.2 원인(사실)
- `index.html`이 먼저 서빙·렌더된 뒤, `checkAuthOnLoad`(`app.js:48`)가 **비동기로** `auth/status`를 확인하고 나서야 `redirectToLogin`(`app.js:41`)으로 넘어간다 → 그 사이 채팅 화면 노출.
- `auth/status`의 **401**은 무토큰이 아니라 **localStorage의 만료/무효 토큰**이 검증(`_verify_user_token`)에서 401을 유발하는 경우다(무토큰이면 `get_current_user`가 `None` → 200). 정상 흐름이지만 로그가 시끄럽고 UX 플래시를 만든다.
- `favicon.ico` 라우트/링크가 없어 브라우저 기본 요청이 404.

### 15.3 구현 계획
- **플래시 제거(핵심)**: 앱 셸을 **인증 확정 전까지 감춘다**. `index.html`에 초기 로딩 게이트(예: `<body class="auth-pending">`로 본문 숨김 + 스플래시/스피너) → `checkAuthOnLoad` 완료 시 해제하여 노출, 미인증이면 노출 없이 곧장 리다이렉트. (서버는 토큰이 localStorage에 있어 SSR 판단 불가 → 클라이언트 게이트가 현실적.)
- **favicon 404 제거**: `index.html`/`login.html`/`register.html` `<head>`에 `<link rel="icon">` 추가하고 `src/static/favicon.ico`(또는 SVG) 배치, 필요 시 `server.py`에 `/favicon.ico` 라우트.
- **로그 소음 완화(선택)**: 만료 토큰 상황을 `auth/status`가 401 대신 **200 + `user:null`(+`token_expired:true` 힌트)** 로 응답하도록 조정하고 프론트가 그 힌트로 리다이렉트. 전환 시 기존 401 분기(`app.js:53`)와의 정합성 유지.

### 15.4 확인 필요 항목
- 스플래시 형태(간단 스피너 vs 브랜드 로고)와 인증 확정까지 **최대 대기/타임아웃** 처리.
- 만료 토큰 응답을 401 유지 vs 200+힌트로 변경할지(로그 정책).

## 16. 진행상황 패널 스크롤 컨트롤(대화창과 동일)

### 16.1 목표
진행상황 패널도 대화창과 동일한 스크롤 UX를 갖게 한다: 새 진행 메시지 발생 시 스크롤이 **맨 아래면 계속 팔로잉**, **위로 올리면 팔로잉 중단** + **"맨 아래로" 버튼 활성화**(신규 출력 강조 포함).

### 16.2 현재 구조(사실)
- 대화창(`chatMessages`)에는 이미 스티키-팔로잉 로직이 있다: `stickToBottom`(`app.js:201`), scroll 리스너(`340-343`), `isNearBottom`(`1315`), `scrollToBottomIfSticky`(`1343`), `updateScrollToBottomBtn`(`1320`), 전용 버튼 `#scrollToBottomBtn`(`index.html:76`, 핸들러 `346-349`).
- **진행상황 패널(`#progressPanelBody`, `index.html:137`)은 별도 스크롤 컨테이너**이지만 위 로직이 적용돼 있지 않다.

### 16.3 구현 계획
대화창 로직을 진행상황 패널용으로 **일반화·복제**한다(전역 단일 상태를 재사용하지 말고 패널 전용 상태를 둘 것):
1. 패널 전용 상태 `progressStickToBottom`(기본 true)과 임계값 재사용(`BOTTOM_THRESHOLD_PX`).
2. `#progressPanelBody`에 `scroll` 리스너: `progressStickToBottom = isNearBottom(progressPanelBody)`. → `isNearBottom`을 **컨테이너 인자를 받도록 리팩터**(현재 `chatMessages` 하드코딩, `app.js:1316`).
3. 진행 append 지점(`updateProcessingStage` `app.js:608`, `handleNodeStart`/`handleNodeComplete`)에서 `progressStickToBottom`이면 패널을 맨 아래로 이동.
4. 패널 내 **"맨 아래로" 버튼** 신설(`#progressScrollBtn`) + 표시/강조 토글(대화창 `updateScrollToBottomBtn`의 패널판). 신규 출력 미확인 강조(`has-new`) 동일 적용.
5. 공통화: `isNearBottom(el)`, `scrollElToBottom(el, smooth)`, `updateScrollBtn(btn, el, hasNew)`를 **컨테이너 파라미터화**하여 대화창/패널이 공유(중복 최소화).

### 16.4 확인 필요 항목
- 패널이 접힘(`panelToggle`, `app.js:335`) 상태일 때 버튼/팔로잉 처리(접힘 시 무시).
- 패널 스크롤 버튼의 위치/스타일(패널 우하단 고정 등).

---

# Part C — 알림 인가(지역 스코프 RBAC)

> ※ 인가 변경으로 **Part A의 통합 RBAC를 소비**한다(선행 요건: Part A §9 적용). 신규 결정(D-번호) 등재 대상(§20).
>
> **관련 파일(추가)**
> - `src/api/routes/alarm.py` — 알림 SSE(`alarm_notifications_stream` 908~926, `alarm_bus` 전체 브로드캐스트), 이벤트에 `db_id` 포함(820), `analyze-test`(`require_user` 573)
> - `src/domain/user.py` — `UserRole`(17~21, 현재 user/admin 2값), `User.allowed_db_ids`(42), `to_auth_dict`(54~62, `allowed_db_ids` 이미 노출)
> - `src/static/js/app.js` — 알림 구독(`new EventSource(...)` ~2072), 초기 인증 게이트(`checkAuthOnLoad` 48~95)
> - `src/config.py` — 존↔db_id 매핑 근거(도메인 프로필: 공동존=`polestar_cm_gp`/`polestar_cm_yd`, 은행존=`polestar_b0`)

## 17. 지역 스코프 RBAC 및 알림 존 필터

### 17.1 배경/목표
현재 알림(알람) 스트림은 **웹에 접속한 모든 사용자**가 구독·수신할 수 있다. 이를 **지역 스코프 역할**로 차별화한다.

| 역할(중복 할당 가능) | 어드민 페이지 | 알림 수신 범위 |
|---|---|---|
| **관리자(admin)** | 접근(full) | **전 존**(공동존+은행존) |
| **공동존 운영자** | 미접근(관리 아님) | **공동존만**(김포 `polestar_cm_gp` + 여의도 `polestar_cm_yd`) |
| **은행존 운영자** | 미접근 | **은행존만**(`polestar_b0`) |
| **일반(user)** | 미접근 | **수신 안 함** |

- **중복 할당**: 한 사용자가 "공동존 운영자 + 은행존 운영자"를 동시에 가질 수 있어야 함(두 존 모두 수신). 관리자와의 중복도 무해(관리자면 전 존).
- **SSO 연동 불변**: 본 변경은 **기존 SSO 연동을 해치거나 영향을 주면 안 된다**(§17.6).
- **UX**: 화면 상단에 **알림 수신 여부 토글(체크박스)** — 권한이 있어도 사용자가 수신을 끌 수 있게 한다(인가와 별개의 개인 표시 설정).

### 17.2 현재 구조(사실) — 왜 확장이 필요한가
- SSE 엔드포인트 `GET /api/v1/alarm/notifications/stream`(`alarm.py:908-926`)은 **인증 의존성이 없다**(`request: Request`만). → 누구나 구독 가능. 반면 알람 분석 API(`analyze-test`, `alarm.py:573`)는 `require_user`로 보호된다 — 스트림만 무방비.
- 브라우저 **`EventSource`는 `Authorization` 헤더를 실을 수 없어**(`app.js:~2072`) 토큰 기반 인증을 붙이기 어렵다(무인증으로 열려 있던 실질적 이유).
- `UserRole`은 **`user`/`admin` 2값 단일 enum**(`user.py:17-21`)이라 "공동존/은행존 운영자"를 표현할 수 없고 중복 할당도 불가(단일 필드). 단, `User`에는 이미 **`allowed_db_ids: list[str]`**(`user.py:42`)가 있고 `to_auth_dict`가 이를 **토큰/인증 dict로 노출**한다(`user.py:54-62`) → **존 스코프를 db_id 집합으로 표현**하기에 적합한 기존 훅.
- 알람 이벤트에는 **`db_id`가 포함**(`alarm.py:820`)되고, SSE는 `alarm_bus`가 **전 구독자에게 무차별 브로드캐스트**한다(`alarm.py:908-926`) → 구독자별 존 필터가 없다.

### 17.3 데이터 모델 — 역할·존 표현(모델 1 채택)
> 핵심 제약: **중복 할당 가능** + **어드민 접근(Part A)과 알림 존은 서로 다른 축**(공동존 운영자는 어드민 아님).

**모델 1(권장·채택): `UserRole`(어드민 게이트) 유지 + 별도 알림 존 스코프**
- Part A의 `UserRole.ADMIN`(=어드민 페이지 접근, D-064)은 **그대로** 유지. 어드민 판정은 종전대로.
- 알림 존은 **`alarm_zones: list[str]`**(예: `["gongjon","bankjon"]`) 신규 필드 또는 **기존 `allowed_db_ids` 재사용**(존→db_id 전개)으로 표현. 중복 할당은 리스트로 자연 표현. 일반=빈 리스트=수신 안 함. 관리자=전 존(admin이면 존 무관 전체 허용).
- **장점**: Part A 변경 최소, 두 축(관리자/존) 분리, 기존 `allowed_db_ids` 인프라(토큰 노출·DB 컬럼) 재사용.

> (기각) 모델 2 — `roles: list[UserRole]`로 일반화: 표현력은 높으나 Part A의 단일 `role` 소비처(`require_admin_user`, `to_auth_dict`, 어드민 UI, DB 스키마)를 광범위 변경 → 회귀 위험↑. 존만 필요한 현 요구엔 과함.

> 존 코드(`gongjon`/`bankjon`)는 **단일 출처 상수**로 정의하고 `zone→[db_id...]` 매핑 헬퍼(`routing`/`config` 계층)를 두어 알람 필터·프론트·seed가 공유한다(하드코딩 분산 금지 — CLAUDE.md 존 라우팅 교훈).

### 17.4 구현 계획 — 백엔드(인가 = 실질 강제)
1. **모델/저장소**: `User`에 `alarm_zones`(또는 `allowed_db_ids` 활용) 추가 — 도메인 필드 + `user_repository` 스키마/마이그레이션 + `to_auth_dict` 노출. 어드민 사용자 관리 API/화면에서 **존 역할을 체크박스(중복 가능)로 부여**.
2. **존↔db_id 매핑 헬퍼**(단일 출처): `zone_to_db_ids(zone) -> list[str]`, `db_id_to_zone(db_id) -> zone`. `gongjon=[polestar_cm_gp, polestar_cm_yd]`, `bankjon=[polestar_b0]`. (신규 존/DB 편입 시 여기만 갱신.)
3. **SSE 인증 + 존 필터**:
   - `alarm_notifications_stream`(`alarm.py:908`)에 **인증 의존성** 추가. 브라우저 `EventSource`가 헤더를 못 실으므로 **인증 수단 택1**:
     - **(A) 쿼리 파라미터 토큰**(간단): `EventSource(".../stream?token="+userToken)`. 장점 변경 최소, **단점 URL/액세스 로그에 토큰 노출**(내부망 전제 수용 또는 단시간 티켓으로 완화).
     - **(B) 쿠키 기반**(권장·정공법): 로그인 시 JWT를 `HttpOnly` 쿠키로도 세팅 → `EventSource`가 쿠키 자동 전송 → 엔드포인트가 쿠키에서 토큰 추출·검증. 다른 SSE(`/query/stream`) 인증 일관성도 개선. 단점 로그인/의존성 흐름 변경.
     - 인증에서 **사용자의 존 집합** 산출: admin→전 존, 운영자→해당 존, 일반→빈 집합→**구독 자체 거부 403**. (`role==admin` 판정은 Part A의 `require_admin_user`/D-064 재사용.)
   - `event_generator` 내에서 이벤트를 **구독자 존으로 필터**: `db_id_to_zone(event["db_id"])`가 사용자 존 집합에 없으면 skip(yield 안 함). admin은 무조건 통과. 브로드캐스트는 유지하되 구독자별 게이트.
   - (성능 대안) `alarm_bus`가 존별 토픽을 갖도록 확장. 우선은 구독자측 필터로 충분(트래픽 소규모).
4. **회귀 테스트**: (a) 공동존 운영자 → b0 이벤트 **미수신**, gp/yd 수신. (b) 은행존 운영자 → 반대. (c) 일반 → 구독 403. (d) 관리자 → 전 존 수신. (e) 중복(공동존+은행존) → 전 존 수신.

### 17.5 구현 계획 — 프론트(UX)
- `checkAuthOnLoad`에서 얻은 **존 집합/역할**로 알림 UI를 조건부 렌더. **일반(존 없음)은 `EventSource` 자체를 열지 않음**(백엔드 403과 이중 방어, 재연결 루프 방지).
- **알림 수신 토글(체크박스)**: 화면 상단(헤더/알림 패널 상단)에 "알림 수신" 체크박스. 해제 시 `EventSource`를 닫고(구독 중단), 체크 시 재구독. 설정은 **localStorage(+선택적으로 사용자 프로필)** 에 개인 표시 설정으로 저장. **인가(권한)와 독립** — 권한 없는 사용자에겐 토글 자체를 숨김/비활성.
- 토큰 만료/403 시 스트림 정리(재연결 루프 방지).
- (선택) 어느 존의 알림인지 카드에 **존 배지**(공동존/은행존) 표기 — 중복 할당 사용자 구분 편의.

### 17.6 SSO 연동 보존(필수 제약)
- 본 변경은 **로그인/인증 흐름 자체를 바꾸지 않는다** — 역할/존은 **인증 성공 후의 인가(스코프) 계층**에만 추가된다. SSO 로그인 사용자도 동일하게 `alarm_zones`(또는 `allowed_db_ids`)를 부여받아 동작.
- SSO 사용자 프로비저닝 시 **존 매핑 규칙**(SSO 그룹/부서 → 존)을 정의하거나, 미매핑이면 **일반(수신 안 함)으로 안전 기본값**. SSO 콜백/토큰 파싱 경로는 **불변** 원칙, 존 부여는 사용자 저장소 측에서 처리.
- 회귀: SSO 로그인 경로 e2e가 기존과 동일하게 성공(인증 불변, 인가만 추가).

---

# 통합 작업 순서 및 관리

## 18. 통합 작업 순서(제안)

우선순위 순. Part 간 독립이므로 병렬 착수 가능(단 §17은 Part A §9 선행).

1. **[P0·즉시]** §8.1 권한 상승 핫픽스(토큰 `type` 검증 한 줄) + §8.4 회귀 테스트 — **방향 무관 선반영**.
2. **[P1]** §8.2 시크릿 분리 · §8.3 하드닝 → §9 통합 RBAC(가드·seed·프론트·최소-1-admin).
3. **[P1·독립·저위험]** Part B 프론트: §13 다운로드 버튼 간격 → §14-(1) 첨부 말풍선-위 카드 → §15 FOUC/favicon.
4. **[P1]** §14-(2) 원본 양식 다운로드(백엔드 원본 보관+엔드포인트 → 프론트 링크 사후 주입).
5. **[P1]** §17 지역 스코프 RBAC — **§9 확정 위에서**: 모델 1 필드 추가 → 존 매핑 헬퍼 → SSE 인증(수단 B 권장)+존 필터 → 프론트 조건부 렌더·수신 토글 → SSO 보존 회귀.
6. **[P2]** Part B 나머지: §16 진행상황 스크롤(컨테이너 파라미터화) → §11 중단 버튼 → §12 중단 안내(§11 종속) → (선택) §11.3 백엔드 취소 로깅.
7. **[마무리]** §20 결정 등재 + CLAUDE.md Known Mistakes 갱신.

## 19. 남은 확인 필요 항목(요약)

착수 전 결정된 사항은 §0에 정리됨. 아래는 각 절에 남은 세부 확인 항목이다.

- **§11.4** 중단 시 부분 텍스트 보존 여부(권장 보존) · 비스트리밍 폴백 적용 여부(권장 스트리밍 한정).
- **§13.4** 버튼 간격 값(8/12px) · 좁은 화면 줄바꿈 허용.
- **§14.5** 원본 보관 위치(인메모리/디스크·스토리지)·TTL · 다운로드 접근 범위(전체 vs 본인만, 권장 본인+감사).
- **§15.4** 스플래시 형태·대기 타임아웃 · 만료 토큰 응답(401 유지 vs 200+힌트).
- **§16.4** 패널 접힘 시 처리 · 스크롤 버튼 위치.
- **§17** ① 존 정의 확장성(공동존=gp/yd, 은행존=b0 외 추가 존/DB 예정 여부) ② SSE 인증 수단 최종(권장 B) ③ SSO 존 자동 매핑 규칙 vs 수동 부여 ④ 수신 토글 저장 위치(localStorage만 vs 프로필 동기화).

## 20. 결정(D-번호) 관리

- **본 계획 부여**: D-064(통합 RBAC), D-065(토큰 `type` 검증 + 시크릿 분리), D-066(크레덴셜·시크릿 하드닝), **§17 지역 스코프 알림 RBAC = 신규(후보 D-069~)**.
- **번호 규칙(CLAUDE.md Known Mistakes 2026-06-25)**: 신규 D-번호는 **`docs/02_decision.md`의 `## D-` 헤더와 「변경 이력」 표 + CLAUDE.md Known Mistakes까지 grep**하여 실제 최댓값+1로 부여한다(헤더만 grep하면 표 행 선점 번호를 놓침).
  - 등재 시점 확인: 현 Known Mistakes에 **D-067·D-068**까지 소비 확인 → §17 후보 **D-069~**. Plan 58이 예약한 **D-057~D-059** 등재 여부도 함께 재확인해 충돌 시 다음 빈 번호로 조정하고 사유를 본문에 명시.
- **등재 항목**: 구현 시 `docs/02_decision.md`에 D-064~D-066 및 §17 결정을 정식 등재하고, CLAUDE.md Known Mistakes에 다음 교훈을 기록한다.
  - "동일 시크릿·`type` 미검증으로 인한 권한 상승" (Part A).
  - "SSE 무인증 스트림 + 구독자별 존 필터 부재, 중복 역할·SSO 보존" (§17).
