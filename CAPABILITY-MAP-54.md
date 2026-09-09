# Capability Map: 알람 노이즈 캔슬링 관제 대시보드 (`plans/54` · D-195 예정)

> **작성일**: 2026-09-03 · **근거**: `plans/54-noise-cancellation-dashboard.md` · 시안 `plans/54-...-mockup.html`
> **범위 확정(사용자, 2026-09-03)**: Phase F1+F2+F3 전부 · 퍼널 단계는 **`stage` 필드 신설** ·
> 정책은 **읽기 전용 표시 + 설정 화면 딥링크**(쓰기는 기존 `/admin/settings` 단일 경로 유지) ·
> 테마는 **기존 테마 시스템 정합**(D-180 — 다크 고정 아님).

## 계획서와 코드의 어긋남 (실측 2026-09-03)

계획서는 2026-06-29 작성본이라 경로(`src/alarm/…`)와 "미구현" 판정이 현행과 다르다. 실측 결과:

| 계획서 §  | 계획서 기술 | 실측 |
|---|---|---|
| §6 결정 적재 | "신설 `src/alarm/infrastructure/decision_store.py`" | **이미 있음** — `noise_gate/infrastructure/decision_store.py`(386줄, `record`/`aggregate`/`meta_alerts`) |
| §5 `/noise/summary`·`/health` | 신설 | **부분 존재** — `/api/v1/alarm/metrics`가 KPI·메타경보를 이미 노출 |
| §6 SSE 확장 | "페이로드에 tier/reason 추가" | **이미 됨** — `alarm_notifier._tier_sse_payload`가 `tier`·`tier_reason` 포함, 존 RBAC·SUPPRESS 가시성까지 적용 |
| §5 `/noise/feedback` | 신설 | **이미 있음** — `/alarm/feedback`·`/retract`·`/summary`(Plan 83) + `FeedbackStore` |
| §5 `/noise/policy` PUT | 신설 | **중복** — `/admin/settings` PUT이 `NOISE_GATE_*` 전 키를 검증·감사·리로드까지 편집(Plan 68) |
| §6 퍼널 stage tag | "각 단계가 억제/통과 기록" | **없음** — `NotificationDecision`에 stage 필드 없음, reason 문자열만 단계별로 고유 |
| §5 침묵 CRUD | 신설 | **없음** — `grep -i silence` 0건 |
| §10 UI | 신설 | **없음** — `admin/noise.html`·`js/noise.js` 0건 |

따라서 본 맵은 **남은 것만** 다룬다. 이미 있는 것은 재사용하고 다시 만들지 않는다.

## 모듈

| Module id | 책임 | 소비자 | 패키지 | Depends on |
|---|---|---|---|---|
| `decision-stage` | 결정이 **어느 단계에서 났는지**를 레코드에 남긴다 — `NotificationDecision.stage` 신설 + 각 결정 지점 라벨 + 저장소 기록. tier·reason·priority·signals는 **비트 동일**(판정 회귀 0) | `decision-analytics` · `decision-lookup` | `noise_gate` | — |
| `decision-analytics` | 결정 저장소에서 **퍼널·시계열·상위 억제 유형**을 결정적으로 집계한다. 항등식(`raw − Σtier = Σcut`)이 수용 기준 | `noise-console-api` | `noise_gate` | `decision-stage` |
| `decision-lookup` | 결정 **개별 레코드 조회**(목록 필터·페이지·상세) + 표시 전 **마스킹**. 집계와 반대 축 — 숫자가 아니라 근거를 돌려준다 | `noise-console-api` | `noise_gate` | `decision-stage` |
| `noise-silence` | 운영자가 건 **침묵 규칙**의 정의·저장·매칭·만료 + 워커 신호 산출 + 게이트 단계 편입. 기본 off, 심각도3 불가침 | `noise-console-api` · 게이트 | `noise_gate` | `decision-stage` |
| `noise-console-api` | `/admin/noise/*` 운영자 API — 집계·조회·헬스·침묵 CRUD·정책 읽기·**관제 SSE**. 운영자 JWT + 변경 감사 | `noise-console-ui` | `src` | `decision-analytics` · `decision-lookup` · `noise-silence` |
| `noise-console-ui` | 관제 화면 — KPI·퍼널·추이·메타헬스·실시간 피드·결정추적 드로어·관리 탭. 기존 테마·폰트·정적 서빙 정합 | 운영자 | `src/static` | `noise-console-api` |

**Build order**: `decision-stage` → (`decision-analytics` · `decision-lookup` · `noise-silence` — **서로 독립·병렬 가능**)
→ `noise-console-api` → `noise-console-ui`

> **구현 완료(2026-09-03 · D-196)** — 6모듈 전부. 구현 중 확정된 설계 변경 4건:
> ①**정책 쓰기 미구현**(`/admin/settings`와 이중화 회피 — 사용자 확정 G-3)
> ②**결정 레코드에 `alarm_name`·`server_name` 추가**(지문 해시로는 "무엇이 억제됐나"를 못 보여준다)
> ③**침묵 판정을 도메인으로**(규칙의 심각도 상한은 실효 심각도와 대조돼야 하는데 그 값은 게이트만 안다)
> ④**PAGE는 SSE에 실리지 않아** 피드가 SSE + 30초 재동기화 병행(PAGE를 버스에 얹으면 사용자 UI 동작 변경).
> 검증 수치와 남은 일은 `docs/02_decision.md` D-196.

## 경계가 이렇게 그어진 이유

- **`decision-stage`가 맨 앞이고 아무것도 의존하지 않는 이유**: 퍼널은 이 계획의 기억점(centerpiece)인데,
  **어느 단계가 무엇을 잘랐는지가 데이터에 없으면 퍼널은 그릴 수 없다.** reason 문자열 역추정으로도
  값은 나오지만 사유 문구를 한 글자 고치면 조용히 깨진다 — 사용자가 `stage` 필드 신설을 택한 이유다.
  이 모듈만 `noise_gate/domain`을 건드리므로 **판정 회귀 0의 입증 책임**도 여기서 끝낸다.
- **`decision-analytics`와 `decision-lookup`을 나누는 이유**: 같은 JSONL을 읽지만 **수용 기준이 다르다**.
  전자는 합계 항등식(숫자가 맞는가), 후자는 필터·페이지·마스킹(근거가 새지 않는가). 전자가 틀려도
  후자는 맞을 수 있고 그 반대도 된다. 마스킹 결함은 집계 테스트로 절대 잡히지 않는다.
- **`noise-silence`가 별도 모듈인 이유**: 이 계획에서 **유일하게 게이트 판정을 바꾸는** 모듈이다
  (나머지는 전부 읽기). 알람을 실제로 억제하므로 안전 가드(심각도3 불가침·기본 off·만료 강제)의
  검증이 별도로 서야 하고, 그 실패는 관제 화면 결함과 성격이 완전히 다르다.
- **`noise-console-api`를 UI와 떼는 이유**: API는 인가·감사·계약(JSON)의 축이고 UI는 표현의 축이다.
  API는 UI 없이도 값이 있다(운영 스크립트·점검). 반대로 UI 결함은 억제 동작에 영향이 0이다.
- **정책 쓰기가 모듈이 아닌 이유**: `/admin/settings`가 이미 검증·dry-run·백업·감사·리로드를 하는
  단일 쓰기 경로다. 두 번째 쓰기 경로를 내면 **안전 가드와 감사가 두 곳으로 갈라진다** —
  `noise-console-api`는 정책을 **읽어서 보여주고** 변경은 설정 화면으로 딥링크한다.
- **순환 없음**: UI → API → (집계·조회·침묵) → 단계 태깅. 역방향 참조 0.

## 인터페이스 (경계 계약)

### ① 결정 레코드 JSONL — `decision-stage`가 정의, 나머지가 소비

```jsonc
{ "ts": "2026-09-03T14:03:22+00:00", "alarm_id": "...", "tier": "suppress",
  "reason": "플래핑 — 상태 진동(Nagios), 안정화까지 통보 보류",
  "stage": "flapping",          // ← 신설. 기존 레코드에는 없음(하위호환: 폴백 매핑)
  "priority": 20, "fingerprint": "...", "signals": {...} }
```

`stage` 값은 **닫힌 집합**(`noise_gate/domain/notification_policy.py` 상수):
`non_alarm` · `severity3` · `self_heal` · `resolved` · `collection_failed` · `maintenance` ·
`silence` · `dependency` · `inhibition` · `flapping` · `storm` · `correlation` · `annotation` · `matrix`

### ② 퍼널 — `decision-analytics` → `noise-console-api`

```jsonc
{ "raw": 3186, "tiers": {"page":214,"ticket":408,"dashboard":769,"suppress":1795},
  "suppress_ratio": 0.563, "actionable_pct": 0.38,
  "stages": [ {"stage":"maintenance","label":"유지보수","residual":2710,"cut":476}, ... ] }
```

**항등식**: `raw == Σtiers` 이고 `Σstages[].cut == suppress + dashboard 강등분`.
`residual`은 해당 단계 진입 시점의 잔여(= raw − 앞 단계 cut 누적).

### ③ 침묵 규칙 — `noise-silence`가 정의

```jsonc
{ "id": "slc_...", "matcher": {"db_id":"", "server_name":"WEB-*", "alarm_name":"", "resource_name":""},
  "max_severity": 2, "reason": "월간 배포 점검", "created_by": "kim.op",
  "created_at": "...", "expires_at": "...", "revoked_at": null }
```

- 매처는 **글롭 1종**(`fnmatch`)만 쓴다(정규식 금지 — ReDoS·오작성 억제 위험).
- 빈 문자열 필드는 "무조건 일치"다. **전 필드가 빈 규칙은 거부**(전체 침묵 방지).
- `max_severity`는 `suppress_max_severity` 상한을 넘을 수 없고, **심각도 3은 어떤 규칙으로도 침묵되지 않는다**.

### ④ 관제 SSE — `noise-console-api` → `noise-console-ui`

기존 `/alarm/notifications/stream`의 페이로드(`tier`·`tier_reason` 포함)를 **그대로** 쓰고,
운영자 전용 채널로 `stage`·`fingerprint`를 덧붙인다. 사용자 피드(존 RBAC)와 분리된 별도 구독이다.
