# 59-a. 역할 기반 어드민 접근 정정 + 알림그룹 UI + 보호 root 계정 + 부서 편집 + 감사 로그 로테이션

> 본 문서는 **Plan 59의 후속 정정·완성 문서**다. Plan 59가 백엔드에 깐 통합 RBAC(D-069)와 지역 스코프
> 알림 RBAC(D-072)를 사용자 관점의 최종 동작으로 확정하고, 비어 있는 어드민 UI를 채우며, 운영에 필요한
> 안전장치(보호 root 계정·감사 로그 로테이션)를 추가한다.
>
> - **개선 1 — 역할 기반 어드민 접근 [정정]**: `role==admin`이면 재로그인 없이 대시보드, `role==user`면
>   ADMIN 버튼 미노출. **※ 실테스트에서 미작동 확인 → 원인·수정 확정(§1~4).**
> - **개선 2 — 알림그룹(존) 사용자 관리 UI**: 사용자 관리 탭에 K리전(공동존)·K리전(은행존) 체크박스(§5~8).
> - **개선 3 — 보호 root(솔루션 관리) 계정**: 삭제·강등 불가한 상시 관리자 1개 보장(§9).
> - **개선 4 — 부서(department) 편집**: 관리자가 사용자 부서를 화면에서 수정(§10).
> - **개선 5 — 감사 로그 로테이션**: n일 지난 로그 자동 삭제(현재 기능은 있으나 **호출되지 않음**)(§11).
>
> 작성일: 2026-07-14
> 상위/관련 계획: `plans/59-admin-rbac-and-chat-ux-improvements.md`
> 관련 결정: **D-069**(어드민 통합 RBAC), **D-072**(지역 스코프 알림 RBAC). 본 문서는 이 둘의 UI/UX 완성 +
> 운영 안전장치이며 신규 D-번호는 §12에서 판단한다.

---

## 0. 요약 — 현 상태와 할 일

| 개선 | 백엔드 | 프론트/UI | 상태 | 본 문서에서 할 일 |
|---|---|---|---|---|
| **1** 역할 기반 진입 | ✅ D-069 (`require_admin_user`) | ⚠️ **미인증 시 `/admin/login`(break-glass)로 잘못 유도** | **미작동(정정 필요)** | admin.js 리다이렉트 대상을 `/login`으로 수정 + 진입 규약 확정 |
| **2** 알림그룹 체크박스 | ✅ D-072 (`alarm_zones`·API·SSE) | ❌ 미구현 | 신규 UI | dashboard.html/admin.js 체크박스 |
| **3** 보호 root 계정 | 부분(`_seed_admin_user`) | ❌ | 신규 | `is_protected` + 수정/삭제/PW초기화 차단(백+프론트) |
| **4** 부서 편집 | ✅ (`UpdateUserRequest.department`) | ❌ 표시 전용 | 신규 UI | admin.js 인라인 편집 |
| **5** 감사 로그 로테이션 | ⚠️ `cleanup_old_logs` **존재하나 호출부 없음** | — | 배선 필요 | 기동 시 + 주기 실행(설정 기반) |

---

# 개선 1 — 역할 기반 어드민 접근 [정정]

## 1. 목표(사용자 의도)

- 사용자 관리에서 지정한 **역할(user/admin)이 곧 어드민 대시보드 접근 권한**.
- `role==admin` 사용자는 로그인 후 **ADMIN 버튼 → 재로그인 없이 대시보드**.
- `role==user`는 **ADMIN 버튼 미노출**.

## 2. 원인 분석 — 왜 지금 로그인이 안 되나 (실테스트 정정)

사용자 테스트: **`/admin/login`에서 일반 사용자 계정으로 로그인 → 실패.** 원인은 **설계상 정상 거동 + 잘못된
유도**의 조합이다.

1. **`/admin/login`은 DB 사용자를 인증하지 않는다.** `admin_login`(`admin_auth.py`)은 오직
   `config.admin.username/password`(=env break-glass 계정)만 대조해 `type:admin` 토큰을 발급한다.
   → **DB에 있는 일반/관리자 사용자 계정은 이 창구로 로그인할 수 없다**(원래 그런 용도).
2. **미인증 상태로 `/admin`에 오면 admin.js가 `/admin/login`으로 보낸다.** `admin.js:19-22`:
   ```js
   var token = localStorage.getItem("admin_token") || localStorage.getItem("user_token");
   if (!token) { window.location.href = "/admin/login"; return; }   // ← 문제 지점
   ```
   → 로그인 안 한 사용자가 `/admin`(또는 ADMIN 버튼)에 도달하면 **break-glass 로그인 페이지로 유도**되고,
   거기서 자기 계정으로 로그인하려다 실패한다. **이것이 실테스트에서 본 증상.**

**정리**: Plan 59의 "로그인(`/login`) → ADMIN 버튼 → `/admin`" 경로는 **로그인을 먼저 했을 때만** 동작한다.
로그인 전 `/admin` 진입 시 break-glass 창구로 잘못 유도하는 **리다이렉트 버그**가 남아 있었다.

## 3. 수정 — 정정 계획

### 3.1 미인증 진입은 정상 로그인(`/login`)으로 유도 (핵심 수정)

**`src/static/js/admin.js`**
- 토큰이 없으면 `/admin/login`이 아니라 **`/login`(일반 로그인)**으로 보낸다:
  ```js
  if (!token) { window.location.href = "/login?next=/admin"; return; }
  ```
- `redirectUnauthorized()`를 상태코드로 분기: **401/토큰 없음 → `/login`**, **403(로그인했으나 admin 아님)
  → `/`(메인) + 안내 토스트**.
  ```js
  async function verifyToken() {
    var res = await apiRequest("GET", "/api/v1/admin/users");
    if (res.status === 403) { window.location.href = "/"; return; }   // 관리자 아님
    if (!res.ok) { localStorage.removeItem("admin_token"); window.location.href = "/login?next=/admin"; }
  }
  ```
- (선택) `/login`에 `next` 쿼리 지원: 로그인 성공 후 `next`로 복귀(없으면 `/`). `login.html`의 성공 핸들러에서
  `URLSearchParams`로 `next`를 읽어 리다이렉트. → admin 사용자가 `/admin` 직행 시 매끄럽게 진입.

### 3.2 `/admin/login`(break-glass)의 위치 규약 확정

- `/admin/login`은 **DB 장애 시 env 운영자 계정(root)만** 쓰는 **비상 창구로 유지**하되, **어디에도 링크하지
  않는다**(ADMIN 버튼은 이미 `/admin` 직행). 페이지 상단에 "비상(break-glass) 운영자 로그인 — 일반 사용자는
  메인에서 로그인하세요" 안내를 넣어 혼동을 없앤다.
- **정상 진입 경로는 오직 하나**: `/login`에서 계정 로그인 → `role==admin`이면 ADMIN 버튼 → `/admin`.

### 3.3 이미 되어 있는 부분(유지)

`index.html:33`(`#adminEntryLink` 기본 숨김)·`app.js:86-90`(role==admin/개발모드일 때 노출)·
`admin.js:12`(user_token 재사용)·`server.py:543`(`/admin` 무조건 서빙, JS가 인증)·`require_admin_user`(D-069)는
그대로 둔다. 본 개선은 **리다이렉트 대상 수정 + 진입 규약 확정**이 핵심이다.

## 4. 개선 1 검증 체크리스트(수동 E2E)

- [ ] **로그인 안 한 상태**로 `/admin` 진입 → **`/login`**으로 이동(← 기존엔 `/admin/login`).
- [ ] `/login`에서 role==admin 계정 로그인 → 자동으로 `/admin` 복귀(또는 ADMIN 버튼) → **재로그인 없이 대시보드**.
- [ ] role==user 로그인 → ADMIN 버튼 미노출, `/admin` 직접 진입 시 **`/`로 리다이렉트 + 안내**.
- [ ] `/admin/login`은 env root 계정으로만 로그인됨(break-glass), 일반 계정은 거부(정상).

---

# 개선 2 — 알림그룹(존) 사용자 관리 UI

## 5. 목표

사용자 관리 탭에서 각 사용자에게 **알림그룹**을 체크박스로 할당한다.

| 알림그룹(표시명) | 존 코드 | 수신 범위 |
|---|---|---|
| **K리전(공동존)** | `gongjon` | 김포 `polestar_cm_gp` + 여의도 `polestar_cm_yd` |
| **K리전(은행존)** | `bankjon` | `polestar_b0` |

- 중복 체크 가능. **둘 다 미체크 = 일반(수신 안 함)**. 관리자(role==admin)는 알림그룹 무관 **전 존 수신**(D-072).

## 6. 현재 구조(사실) — 백엔드만 있고 UI 없음

Plan 59 D-072가 이미 구현: `User.alarm_zones`·`to_auth_dict`·`user_repository`(+DDL `ALTER TABLE ... alarm_zones
TEXT[]`)·`PUT /admin/users/{id}`의 `alarm_zones`(`normalize_zones`)·`routing/zones.py`·SSE 필터. **미구현: 어드민
사용자 관리 테이블의 체크박스 UI**(`dashboard.html:131-144` 컬럼, `admin.js:402-423` 렌더).

## 7. 구현 계획

### 7.1 `src/static/admin/dashboard.html`
- `#usersTable` `<thead>`에 **`<th>알림그룹</th>`** 컬럼 추가(부서 앞/뒤 적절 위치).

### 7.2 `src/static/js/admin.js` — `loadUsers()` 렌더/바인딩
- 각 행에 체크박스 셀:
  ```js
  var zones = u.alarm_zones || [];
  var zoneCell =
    "<td style='white-space:nowrap'>" +
      "<label style='margin-right:8px'><input type='checkbox' class='zone-chk' data-uid='" +
        escapeHtml(u.user_id) + "' data-zone='gongjon'" + (zones.indexOf('gongjon')>=0?" checked":"") +
        "> K리전(공동존)</label>" +
      "<label><input type='checkbox' class='zone-chk' data-uid='" + escapeHtml(u.user_id) +
        "' data-zone='bankjon'" + (zones.indexOf('bankjon')>=0?" checked":"") + "> K리전(은행존)</label>" +
    "</td>";
  ```
- 변경 시 그 행의 체크 상태를 모아 저장(둘 다 해제면 `[]` **명시 전송**):
  ```js
  usersBody.querySelectorAll(".zone-chk").forEach(function(chk){
    chk.addEventListener("change", function(){
      var uid = chk.dataset.uid;
      var checked = usersBody.querySelectorAll(".zone-chk[data-uid='"+CSS.escape(uid)+"']:checked");
      var zones = Array.prototype.map.call(checked, function(c){ return c.dataset.zone; });
      updateUser(uid, { alarm_zones: zones });
    });
  });
  ```
- **관리자 행**: admin은 전 존 수신이므로 체크박스를 `disabled` + 툴팁("관리자는 전 존 수신")으로 처리(권장).

### 7.3 백엔드 — 확인만
- `admin.py`는 `if body.alarm_zones is not None:`라 **`[]`도 저장**(둘 다 해제=일반). ✅ 프론트는 생략 없이 `[]` 전송.
- `normalize_zones()`가 정의된 존만 통과. ✅ 응답에 `alarm_zones` 노출(재렌더용). ✅

## 8. 알림그룹 → 수신 매트릭스(D-072와 일치)

| 상태 | alarm_zones | 어드민 접근 | 알림 수신 |
|---|---|---|---|
| 관리자 | (무관) | ✅ | 전 존 |
| 공동존만 | `["gongjon"]` | ❌ | 공동존만 |
| 은행존만 | `["bankjon"]` | ❌ | 은행존만 |
| 둘 다 | `["gongjon","bankjon"]` | ❌ | 두 존 |
| 둘 다 해제 | `[]` | ❌ | 수신 안 함 |

---

# 개선 3 — 보호 root(솔루션 관리) 계정

## 9. 목표 / 설계

**문제**: 통합 RBAC에서 관리자가 **자기 역할을 user로 강등하거나 자기 계정을 삭제·비활성**하면, 솔루션에
들어갈 관리자가 **0명**이 될 수 있다. Plan 59의 최소-1-admin 가드(D-069)가 "마지막 활성 관리자"를 막지만,
"항상 존재하는 불변의 root 1개"라는 **더 강하고 명시적인 보장**이 필요하다.

**설계**: env 운영자 계정(`ADMIN_USERNAME`/`ADMIN_PASSWORD`)을 **DB 사용자(role=admin)로 seed**하여
**일반 로그인(`/login`)으로도 접속 가능한 root 계정**으로 만들고, 이 계정을 **불변(보호)** 처리한다.

- **seed는 이미 존재**: `server._seed_admin_user`가 활성 관리자 0명일 때 env 크레덴셜로 role=admin 사용자를
  생성한다(Plan 59 D-069). 즉 "기존 admin 계정+비밀번호를 일반 유저로 로그인"은 이 seed로 충족된다.
- **보호 표식**: `User.is_protected: bool`(신규) + DDL `ALTER TABLE auth_users ADD COLUMN IF NOT EXISTS
  is_protected BOOLEAN NOT NULL DEFAULT FALSE`(멱등). seed 시 `is_protected=True`로 생성.
  (대안: `user_id == config.admin.username`로 판정 — 간단하나 username 변경에 취약 → **컬럼 방식 권장**.)
- **불변 강제(백엔드, 실질 방어)** — `routes/admin.py`:
  - `update_user`: 대상이 `is_protected`면 **role·status 변경 거부(403)**. (부서 등 비권한 필드는 허용 가능 —
    §11.1 확인.)
  - `reset_user_password`: `is_protected`면 **거부(403)**.
  - `delete_user`: `is_protected`면 **거부(403)**.
  - 응답 스키마 `UserInfoResponse.is_protected` 노출(프론트 렌더용).
- **불변 강제(프론트)** — `admin.js` 사용자 관리 렌더: `u.is_protected`면 역할/상태 select, PW초기화·삭제
  버튼을 **disabled + 자물쇠 아이콘/툴팁("솔루션 root 계정 — 변경 불가")**.
- **root의 알림그룹**: root는 관리자이므로 전 존 수신(알림그룹 체크박스는 disabled).

> 결과: 어떤 관리자가 실수로 자신을 강등/삭제해도 **보호 root가 최소 1명 상주** → 솔루션 잠김 원천 차단.
> 최소-1-admin 가드(D-069)는 그대로 유지(보완 관계).

---

# 개선 4 — 부서(department) 편집

## 10. 목표 / 구현

관리자가 사용자 **부서 텍스트를 화면에서 수정**(정책 위반 입력 교정 등).

- **백엔드**: `UpdateUserRequest.department` + `update_user`의 `if body.department is not None:`가 **이미 지원**. ✅
- **프론트(`admin.js` 사용자 관리)**: 현재 부서는 표시 전용(`<td>부서</td>`). 이를 **인라인 편집**으로:
  - `<td>`에 작은 `<input class='dept-input' data-uid=... value=부서>` + `blur`/`Enter`에 저장:
    ```js
    usersBody.querySelectorAll(".dept-input").forEach(function(inp){
      inp.addEventListener("change", function(){ updateUser(inp.dataset.uid, { department: inp.value.trim() }); });
    });
    ```
  - `is_protected` 계정은 부서 편집 허용 여부를 §11.1에서 확정(기본: 허용).
- 값 검증(길이 100자, 스키마 제약)·빈 문자열 처리는 기존 스키마/백엔드 규칙 준용.

---

# 개선 5 — 감사 로그 로테이션(보관 기간 경과분 삭제)

## 11. 현재 구조(사실) 및 구현

**사실**: 보관 기능은 있으나 **아무도 호출하지 않는다.**
- 설정 `AuditConfig.retention_days = 90`(`config.py:294`, env `AUDIT_RETENTION_DAYS`).
- 구현 `PostgresAuditRepository.cleanup_old_logs(retention_days)`(`audit_repository.py:473`, 경과 로그 DELETE).
- **호출부 없음**: `cleanup_old_logs(` 호출이 코드 전역에 0건(스케줄러·엔드포인트·기동 훅 모두 없음)
  → 실제로는 **오래된 로그가 영영 삭제되지 않는다.**

**구현 계획** — 다음을 조합(권장 A+C):
- **(A) 기동 시 1회 정리**: `server.lifespan` 시작부(감사 DB 초기화 후)에서 `audit_repo.cleanup_old_logs(
  config.audit.retention_days)` 호출(예외는 warning으로 삼켜 기동 무차단). `retention_days<=0`이면 skip(비활성).
- **(B) 주기 정리(백그라운드 태스크)**: lifespan에서 `asyncio.create_task`로 **하루 1회** 루프 실행
  (`while True: cleanup; await asyncio.sleep(86400)`), 종료 시 cancel. JSONL 감사 파일도 쓰면 파일 로테이션은
  별도(§11 확인)로 다룬다.
- **(C) 수동 트리거(어드민 엔드포인트/버튼)**: `POST /api/v1/admin/audit/cleanup`(`require_admin_user`) →
  즉시 정리 + 삭제 건수 반환. dashboard 감사 로그 탭에 "오래된 로그 정리" 버튼 + 보관일수 표시.
- **설정 노출**: `.env.example`에 `AUDIT_RETENTION_DAYS`(주석 별도 줄) 문서화. `retention_days`가 실제로
  적용됨을 검증(설정 n일 → n일 경과 로그 삭제).

> 주의: JSONL 감사(`AuditConfig.jsonl_enabled`)와 DB 감사(`db_enabled`)가 병존한다. `cleanup_old_logs`는 **DB**
> 로그를 지운다. JSONL 파일 로테이션이 필요한지(파일 크기·일자별 분할) §12 확인에서 결정.

---

## 12. 결정(D-번호) · 등재

- 개선 1(리다이렉트 정정)·2·4는 D-069/D-072의 **UI/UX 완성** → 신규 D 불필요, 해당 changelog에 후속 기록.
- **개선 3(보호 root)·5(감사 로테이션)**는 정책·스키마 변경이라 **D-073으로 등재 완료(2026-07-15)** —
  보호 root 계정 + 감사 로그 로테이션 배선 + 어드민 진입 규약 정정. (`docs/02_decision.md` `## D-073`.)
- **구현 완료(2026-07-15)**: 개선 1~5 전부. 회귀 `tests/test_api/test_plan59a.py` 10건 통과, arch exit 0,
  신규 실패 0(기존 6 test_routes=MagicMock 픽스처 이슈).
- CLAUDE.md Known Mistakes 후보: (a) "`/admin/login`은 env break-glass 전용 — DB 사용자는 `/login`으로.
  미인증 `/admin` 진입은 `/login`으로 유도(관리자 로그인 창구 이원화 금지)." (b) "기능(cleanup_old_logs)이
  있어도 **호출부가 없으면 무효** — 배선·스케줄 확인."

## 13. 작업 순서(제안)

1. **[개선 1·핵심]** `admin.js` 리다이렉트 대상 `/login`으로 수정 + `login.html` `next` 복귀 + `/admin/login`
   안내 문구 → §4 E2E.
2. **[개선 3]** `is_protected` 컬럼(DDL)·도메인/repo/스키마·seed 시 True·update/reset/delete 백엔드 가드 →
   admin.js 보호 행 컨트롤 disabled.
3. **[개선 2]** dashboard.html 컬럼 + admin.js 알림그룹 체크박스(§7).
4. **[개선 4]** admin.js 부서 인라인 편집(§10).
5. **[개선 5]** lifespan 기동 정리(A) + 주기 태스크(B) [+ 수동 엔드포인트(C)] + `.env.example` 문서화.
6. **[등재]** D-073 등재 + Known Mistakes + 회귀 테스트(보호 계정 가드·존 저장·감사 정리).

## 14-B. 테스트 피드백 후속 정정 (구현 완료 2026-07-15)

실환경 테스트에서 확인된 3건. 신규 D-번호 없음(D-069/D-072/D-073 UI 마무리).

1. **어드민 로그아웃이 계정을 로그아웃하지 않음**: `admin.js` `logoutBtn`이 `admin_token`만 지우고
   `/admin/login`으로 보냈다 → 통합 RBAC 사용자(`user_token`)는 세션이 살아있고 break-glass 화면이 떠
   "별도 계정" 인상을 줌. **수정**: 로그아웃을 컨텍스트 인지로 — 일반 세션은 `POST /auth/logout`(쿠키 정리)+
   `user_token` 제거 → **`/login`**, break-glass(admin_token) 세션만 `/admin/login` 복귀. `/admin/login`은
   숨긴 비상용으로 유지(사용자 확정).
2. **사용자 관리 테이블 가독성**: 폭 제한으로 긴 문자열이 개행 → `#usersTable`을 `.users-table-scroll`
   (`overflow-x:auto`) 래퍼로 감싸고 셀 `white-space:nowrap` → **가로 스크롤**.
3. **이름(username) 편집 불가**: 표시명 `username`을 인라인 편집(부서와 동형). 백엔드는 기존
   `UpdateUserRequest.username` 재사용. 로그인 ID(`user_id`)는 불변이라 비대상. 보호 root도 이름/부서/
   알림그룹은 편집 허용(역할·상태·PW·삭제만 잠금).

## 14. 확인 필요 항목 (사용자 확정 2026-07-14)

1. **보호 root의 부서 편집 허용?** → **확정: 허용.** 역할/상태/PW초기화/삭제만 잠그고 **부서는 편집 가능**.
2. **보호 표식 방식** → **확정: `is_protected` 컬럼 방식**(username 변경에도 보호가 계정에 고정, 안정적).
3. **감사 로테이션 방식** → **확정: 전부 구현** — A(기동 시 1회) + B(하루 1회 주기) + C(어드민 수동 버튼).
4. **root 계정 비밀번호 변경 경로**(미확정): PW초기화(관리자)는 잠그되, 본인은 `/auth/password`로 변경 허용할지.
   → 기본: **본인 변경 허용**(관리자 강제 초기화만 차단).
5. **권한/역할 변경 즉시 반영 vs 재로그인**(미확정): role은 로그인 시 토큰 클레임 → 즉시 반영하려면 재로그인
   필요. → 기본: **알림그룹은 대상자 재구독 시, role은 재로그인 시 반영**으로 명세.
6. **알림그룹 명칭**(미확정): "K리전(공동존)"/"K리전(은행존)" 그대로 사용 가정.
7. **JSONL 감사 파일 로테이션**(미확정): `cleanup_old_logs`는 **DB** 로그만 삭제. JSONL 파일 분할/삭제가
   필요하면 별도 처리 — 기본: **DB 로테이션만**(JSONL은 추후).
