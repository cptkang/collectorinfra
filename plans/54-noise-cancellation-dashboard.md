# 54. 알람 노이즈 캔슬링 모니터링·관리 대시보드 (Noise Cancellation Dashboard)

> 작성일: 2026-06-29
> **상위 계획**: `plans/52-alarm-noise-cancellation.md` (노이즈 게이트 — 본 대시보드가 모니터링·관리하는 대상)
> **관련**: `plans/51-...` 부록 A.1(시그니처), Plan 47(패턴), 기존 운영자 대시보드(`src/static/admin/`)
> **UI 시안(Claude 디자인, 실동작)**: `plans/54-noise-cancellation-dashboard-mockup.html` (브라우저로 열어 확인)
> **상태**: 계획 (미구현)

---

## 1. 개요 및 목표

### 1.1 배경

Plan 52의 노이즈 게이트는 알람을 **PAGE/TICKET/DASHBOARD/SUPPRESS** 4티어로 결정적으로 라우팅하고
근거(reason)·신호(signals)를 감사 기록한다. 그러나 운영자가 **(1) 게이트가 제대로 동작하는지 모니터링**하고
**(2) 억제 정책을 안전하게 관리**할 화면이 없으면, 노이즈 캔슬링은 "블랙박스"가 되어 신뢰를 얻지 못한다.
특히 게이트는 *실제 알람을 억제*하므로, **"왜 이 알람이 억제됐는가"를 투명하게 보여주고 오억제를 즉시
교정·피드백**할 수 있어야 한다(Plan 52 §1.4 설명가능성·메타모니터링).

### 1.2 목표

운영자용(운영자 JWT) **단일 관제 대시보드**로:
1. **모니터링** — 티어 분포·억제율·액션가능 비율·MTTA/MTTR, **노이즈 캔슬링 퍼널**(게이트 단계별 억제량),
   티어 추이, 실시간 결정 피드, **억제기 메타모니터링**(억제율 임계·무수신 워치독).
2. **투명성(설명가능성)** — 알람별 **결정 추적**: §6 파이프라인 중 어느 단계가 어떤 신호로 결정했는지,
   단락(short-circuit)된 단계까지 시각화.
3. **관리** — 침묵(silence) CRUD, **피드백(노이즈/유효) 라벨**(LLM few-shot 입력 — ML 미사용),
   정책 편집(심각도×중요도 매트릭스·임계값·AI 심각도 보강 토글·중요도 매핑).

### 1.3 성공 기준

1. 실시간 SSE로 결정 피드가 흐르고, 클릭 시 결정 추적(파이프라인+신호)이 표시된다.
2. 억제율이 임계 초과거나 무수신이면 대시보드 상단 헬스가 경보 상태로 바뀐다(메타모니터링 §9).
3. 운영자가 침묵 생성/해제, 피드백 라벨, 정책 변경을 할 수 있고 **모든 변경이 감사 기록**된다.
4. **심각도 3 행은 매트릭스에서 잠금**(억제 불가), AI 심각도 토글은 **상향 전용 잠금**(안전 가드 시각화).
5. 기존 스택(바닐라 HTML/CSS/JS, FastAPI 정적 서빙, Pretendard/D2Coding) 정합·신규 빌드툴 없음.
6. 운영자 인증(JWT) 뒤에서만 접근, 정책 변경은 권한·감사 적용.

### 1.4 설계 원칙

- **투명성 우선** — 억제는 곧 "보여주지 않음"이므로, 대시보드는 그 반대로 **억제 내역을 가장 잘 보여주는 곳**.
- **기존 스택·자산 재사용** — 운영자 대시보드(`admin/`)·SSE(`alarm_bus`)·정적 서빙·폰트 그대로.
- **읽기 우선, 변경은 명시적** — 모니터링은 읽기, 정책/침묵 변경만 쓰기(감사·확인 모달).

---

## 2. 현재 자산 / 통합 지점 (실측)

| 자산 | 위치 | 재사용 |
|------|------|--------|
| 운영자 대시보드 | `src/static/admin/dashboard.html` + `src/static/js/admin.js` | 동일 패턴으로 `admin/noise.html` 신설 |
| 정적 서빙·인증 | FastAPI static + 운영자 JWT(login) | 그대로 |
| **실시간 SSE** | `/api/v1/alarm/notifications/stream`(`alarm.py:755`) + `app.state.alarm_bus`(`server.py:101`) · 프런트 `app.js:1902` `EventSource` | **결정(tier/reason/signals) 포함하도록 확장**해 피드로 사용 |
| 폰트 | `src/static/fonts/PretendardVariable.woff2`, `D2Coding.woff2` | UI/mono |
| CSS | `src/static/css/style.css` | 변수·컴포넌트 추가 |
| 결정 산출 | Plan 52 `NotificationDecision`(tier·reason·priority·signals) | **감사+집계 저장소에 적재**(§6 백엔드) |

> 핵심: SSE(`alarm_bus`)는 이미 DASHBOARD 티어 푸시에 쓰인다(Plan 52 §7). 대시보드는 **모든 티어의 결정을
> 모니터링 스트림으로** 받아야 하므로, 페이로드에 `tier`/`reason`/`signals`를 포함하도록 확장한다(통보가 아니라
> 관제용 — §5.3).

---

## 3. 디자인 방향 & 디자인 시스템 (Claude 디자인)

> 실동작 시안: **`plans/54-noise-cancellation-dashboard-mockup.html`**. 아래는 그 디자인 토큰·원칙 요약.

### 3.1 컨셉

**다크 운영관제(NOC) 인스트루먼트 콘솔.** 절제된 정보 밀도 + 4티어 색상 신호 체계.
**기억점(centerpiece) = 노이즈 캔슬링 퍼널**: 수신 알람이 게이트 단계(§6)를 지나며 잔여 신호 막대가
좁아지고 억제분이 빗금으로 "흡수"되어, 노이즈가 캔슬되는 과정을 한눈에 보여준다(파이프라인 시각화 겸용).
헤더에는 노이즈→평탄 파형 모티프(노이즈 캔슬링 은유).

### 3.2 디자인 토큰

| 토큰 | 값 | 용도 |
|------|-----|------|
| `--bg` | `#080b10` | 베이스(딥 슬레이트) + 라디얼 글로우·미세 그리드·그레인 |
| `--panel` / `--line` | `#0f1620` / `#1b2632` | 카드 / 보더 |
| `--page` | `#ff5c5c` | PAGE(즉시 통보) |
| `--ticket` | `#ffb020` | TICKET(대기열) |
| `--dash` | `#4d9fff` | DASHBOARD(표시만) |
| `--suppress` | `#6f8398` | SUPPRESS(억제·기록) — **회색조: "안전하다"는 오해 방지** |
| `--accent` | `#36d6c3` | 인터랙션(버튼·포커스) |
| ok/warn/crit | `#36d6c3`/`#ffb020`/`#ff5c5c` | 메타헬스 |

- **타이포**: `Pretendard`(UI, 가변), `D2Coding`(mono — ID·카운트·타임스탬프·SQL). 큰 수치는 800 weight +
  `tabular-nums`. (프로젝트 동봉 폰트 — 일반 폰트(Arial/Inter) 미사용.)
- **모션**: KPI 카운트업, 퍼널 막대 스태거 채우기, 피드 슬라이드인, 결정추적 드로어 슬라이드, 억제율 conic
  게이지, 헤더 파형 캔슬 애니메이션. (전부 CSS/소량 JS — 외부 라이브러리 0, 폐쇄망 정합.)
- **분위기**: 미세 그리드 + SVG 그레인 오버레이 + 라디얼 글로우(관제실 질감).
- **반응형**: 1440 max, <1080 단일 컬럼.

---

## 4. 화면 구성 (레이아웃 · 컴포넌트 · 데이터 매핑)

```
┌──────────────────────────────────────────────────────────────────────┐
│ HEADER  [파형]노이즈캔슬링관제   SSE연결됨  [억제기 정상]  1H 24H 7D 30D │
├──────────────────────────────────────────────────────────────────────┤
│ KPI  수신 | PAGE | TICKET | DASHBOARD | SUPPRESS | 억제율/액션가능       │
├───────────────────────────────────┬──────────────────────────────────┤
│ ◆ 노이즈 캔슬링 퍼널 (centerpiece) │  실시간 결정 피드 (SSE)            │
│   단계별 잔여/억제 막대(§6)        │   sev배지·알람명·티어배지          │
│   └ 4티어 출력 카드                │   reason·AI↑·시각                  │
│ ─────────────────────────────────  │   (클릭 → 결정추적 드로어)         │
│ 티어 분포 추이 (스택 막대, 2h버킷) │                                   │
├───────────────────────────────────┴──────────────────────────────────┤
│ META  억제율 게이지(임계90%) | 워치독(무수신120s) | 상위 억제 유형      │
├──────────────────────────────────────────────────────────────────────┤
│ 관리  [침묵] [피드백 큐(12)] [정책·매트릭스]                            │
│   침묵: 매처·사유·만료·해제   피드백: 유효/노이즈   정책: 매트릭스+토글  │
└──────────────────────────────────────────────────────────────────────┘
        결정추적 드로어(우측 슬라이드): §6 파이프라인 단계 + 결정지점 + 신호 스냅샷
```

| 컴포넌트 | 표시 데이터 | 소스 API(§5) |
|---------|------------|-------------|
| 헤더 상태 | SSE 연결·evt/h, **억제기 헬스(정상/경고)** | `/noise/health`, SSE |
| KPI 스트립 | 수신·티어별 건수·억제율·액션가능% | `/noise/summary` |
| **노이즈 캔슬링 퍼널** | 단계별 잔여/억제 수(dedup→…→티어) + 4티어 출력 | `/noise/summary`(funnel) |
| 티어 추이 | 버킷별 티어 스택 | `/noise/timeseries` |
| 실시간 결정 피드 | 알람 + tier + reason + AI상향 | SSE `/noise/stream` |
| 결정 추적 드로어 | §6 파이프라인 단계·결정지점·signals | `/noise/decisions/{id}` |
| 메타: 억제율 게이지 | 억제율 vs 임계, 7일 추세 | `/noise/health` |
| 메타: 워치독 | 마지막 이벤트 경과·무수신 임계 | `/noise/health` |
| 메타: 상위 억제 유형 | 억제 사유별 상위 알람유형 | `/noise/top-suppressed` |
| 관리: 침묵 | 활성 침묵 목록·만료 | `/noise/silences` |
| 관리: 피드백 큐 | 경계 결정 + 유효/노이즈 버튼 | `/noise/feedback` |
| 관리: 정책·매트릭스 | 심각도×중요도(심각3 잠금)·토글 | `/noise/policy` |

**결정 추적 드로어(핵심 — 설명가능성)**: 피드 항목 클릭 시 §6 12단계를 세로 타임라인으로 그려,
**통과한 단계 / 결정한 단계(강조) / 단락된 단계**를 구분하고, 하단에 신호 스냅샷(폴스타 심각도·AI 상향·
중요도·유지보수·부모 가용상태·패턴·알림정책)을 표로 보여준다. "유효/노이즈 표시", "이 패턴 침묵" 액션 포함.

---

## 5. 데이터 · API 명세 (운영자 JWT)

기존 운영자 API(`/admin/...`) 패턴 계승. 모두 **읽기 우선**, 변경(침묵/피드백/정책)만 쓰기·감사.

| 메서드·경로 | 용도 |
|------------|------|
| `GET /admin/noise/summary?range=24h` | KPI + 퍼널 단계별 억제 수 + 티어 합계 |
| `GET /admin/noise/timeseries?range=24h&bucket=2h` | 티어 분포 추이 |
| `GET /admin/noise/top-suppressed?range=24h` | 억제 사유별 상위 알람 유형 |
| `GET /admin/noise/decisions?tier=&q=&page=` | 결정 감사 목록(피드·이력) |
| `GET /admin/noise/decisions/{id}` | 결정 추적(파이프라인 단계 + signals) |
| `GET /admin/noise/stream` (SSE) | 실시간 결정 스트림(관제용 — tier/reason/signals 포함) |
| `GET /admin/noise/health` | 메타모니터링(억제율·임계·무수신 워치독) |
| `GET/POST/DELETE /admin/noise/silences` | 침묵 CRUD |
| `POST /admin/noise/feedback` | `{alarm_fp, label: noise|actionable}` → LLM few-shot 저장 |
| `GET/PUT /admin/noise/policy` | 매트릭스·임계·토글·중요도 매핑(심각도3 잠금 강제) |

응답 예 — `summary.funnel`:
```json
{ "raw": 3186, "tiers": {"page":214,"ticket":408,"dashboard":769,"suppress":1795},
  "suppress_ratio": 0.563, "actionable_pct": 0.38,
  "stages": [ {"name":"dedup","ref":"§3.3","residual":2710,"cut":476}, ... ] }
```

---

## 6. 백엔드 통합 (집계 가능한 결정 저장소)

- **결정 적재**: Plan 52 `notification_gate`가 산출하는 `NotificationDecision`(tier·reason·priority·signals·
  fingerprint·ts)을 **집계 가능 저장소**에 기록한다. JSONL 감사는 추적용, 대시보드 집계용은
  **Redis(정렬셋/카운터) 또는 경량 테이블**로 별도 적재(시계열·퍼널 집계 빠름). 기존 감사 인프라 재사용 + 확장.
- **퍼널 집계**: 각 게이트 단계가 "억제/통과"를 기록(stage tag) → 단계별 cut 수 집계.
- **SSE 확장**: `alarm_bus.publish` 페이로드에 `tier/reason/signals` 추가(통보가 아닌 관제 스트림). 운영자
  대시보드는 `/admin/noise/stream` 구독(기존 사용자 피드와 분리, 권한 적용).
- **피드백 저장**: `feedback` → Plan 52 §3.9 LLM few-shot 예시 저장소에 적재(라벨+알람 컨텍스트).
- **정책 반영**: `policy` PUT → Plan 52 `NoiseGateConfig` 런타임 반영(또는 설정 저장소). 심각도3 잠금·
  escalate-only 잠금은 서버에서도 강제(클라이언트 토글 우회 방지).

---

## 7. 보안 · 권한 · 안전 가드

- **운영자 JWT 필수** — 대시보드 페이지·API 모두 인증 뒤. 사용자(채팅) UI와 분리.
- **변경 감사** — 침묵/피드백/정책 변경은 행위자·시각·이전/이후 값 감사 기록(기존 감사 강화 D-027 계승).
- **서버 측 안전 가드(클라이언트 신뢰 금지)** — 심각도3 PAGE 잠금, AI 상향 전용 잠금, 억제 상한
  (`suppress_max_severity`)을 **서버에서 검증**. UI 토글은 표시일 뿐.
- **마스킹** — signals/로그 시그니처에 비밀 포함 가능 → 표시 전 마스킹(`data_masker` 재사용).
- **읽기 모니터링은 비침습** — 집계/스트림은 조회만.

---

## 8. 단계별 구현

- **Phase F1 — 모니터링 읽기 전용 (MVP)**
  - 결정 저장소 적재(§6) + `/noise/summary`·`/timeseries`·`/top-suppressed`·`/health` + `admin/noise.html`
    (KPI·퍼널·추이·메타) + SSE 결정 스트림·피드.
  - verify: 시안 레이아웃 재현, 집계 정확, 억제율 임계·무수신 헬스 전환, JWT 보호.
- **Phase F2 — 결정 추적 + 피드백**
  - `/decisions`·`/decisions/{id}` + 드로어(파이프라인·signals) + `/feedback`(노이즈/유효).
  - verify: 결정지점·단락 단계 정확 표기, 피드백 적재→few-shot 저장.
- **Phase F3 — 관리(침묵·정책)**
  - `/silences` CRUD + `/policy`(매트릭스·토글·임계·중요도 매핑) + 서버 측 안전 가드.
  - verify: 침묵 매칭 억제·만료, 심각도3 잠금·escalate-only 서버 강제, 변경 감사.
- **Phase F4 — 운영 고도화(선택)**
  - 기간 비교·내보내기(CSV), 알림(억제율 급변 시 운영자 통지), 다크/라이트 토글.

---

## 9. 테스트 (Playwright + API)

| 테스트 | 검증 |
|--------|------|
| `test_summary_aggregation` | 퍼널 단계 cut 합 = raw − 티어합, 억제율·액션가능% |
| `test_sse_decision_feed` | 결정 스트림 수신·티어 색상·reason 표시 |
| `test_decision_trace` | 드로어 파이프라인 결정지점·단락 단계·signals |
| `test_health_meta` | 억제율>임계/무수신 시 헬스 경고 전환 |
| `test_silence_crud` | 침묵 생성·매칭 억제·만료·해제 |
| `test_feedback_label` | 유효/노이즈 라벨 → few-shot 저장 |
| `test_policy_guards` | **심각도3 잠금·escalate-only 잠금 서버 강제**(우회 거부) |
| `test_authz` | 미인증 접근 거부, 변경 감사 기록 |
| `test_ui_e2e` | Playwright: 로드·카운트업·퍼널·탭·드로어(Plan 24 프레임워크 재사용) |

---

## 10. 변경 범위

### 신규 파일
- `src/static/admin/noise.html` — 대시보드 페이지(시안 기반)
- `src/static/js/noise.js` — 집계 fetch·SSE·드로어·탭·정책 편집
- `src/api/routes/noise_dashboard.py` — `/admin/noise/*` 엔드포인트
- (집계) `src/alarm/infrastructure/decision_store.py` — 결정 적재·집계(Redis/경량 테이블)
- `tests/test_dashboard/...`, `plans/54-...-mockup.html`(시안)

### 수정 파일
- `src/static/css/style.css` — 디자인 토큰·컴포넌트(또는 noise.html 인라인)
- `src/alarm/application/nodes/notification_gate.py` — 결정 저장소 적재 호출
- `src/alarm/infrastructure/notification_bus.py` 또는 `alarm.py` — SSE 페이로드에 tier/reason/signals 확장
- `src/api/server.py` — noise 라우터 등록·정적 라우트
- `docs/02_decision.md` — D-040 하위(대시보드) 또는 D-043 등재

### 변경하지 않는 파일
- 노이즈 게이트 결정 로직(Plan 52)·사용자 채팅 UI·기존 운영자 대시보드.

---

## 11. 의사결정 / 사용자 확인

- **D 등재**: 본 대시보드는 Plan 52(D-040)의 운영 도구 → D-040 하위 또는 **D-043(노이즈 대시보드)** 등재.
- 확인 필요:
  1. **결정 집계 저장소** — Redis vs 경량 테이블(SQLite/Postgres)? (시계열·보존기간)
  2. **정책 변경 권한 범위** — 누가 매트릭스/임계를 바꿀 수 있는가(운영자 등급 분리?).
  3. **사용자 피드 vs 운영자 관제 스트림 분리** — 기존 `alarm_bus`를 확장 vs 별도 채널.
  4. **다크 테마 고정 여부**(시안은 다크 — 라이트 토글 필요 시 F4).

---

## 12. 참고

- 상위: `plans/52-alarm-noise-cancellation.md` (티어·결정 파이프라인·메타모니터링·signals)
- UI 시안(Claude 디자인): `plans/54-noise-cancellation-dashboard-mockup.html`
- 기존 UI: `src/static/admin/dashboard.html`·`js/admin.js`, SSE `src/api/routes/alarm.py`·`notification_bus.py`
- UI 테스트: `plans/24-ui-playwright-test-plan.md`
- 시그니처(결정 추적 표시): `plans/51-...` 부록 A.1
