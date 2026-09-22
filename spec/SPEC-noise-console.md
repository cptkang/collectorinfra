# Spec: 알람 노이즈 캔슬링 관제 대시보드 (Plan 54 · F1~F3)

> 요구·배경의 정본은 **`plans/54-noise-cancellation-dashboard.md`**, 시안은 같은 폴더의
> `54-noise-cancellation-dashboard-mockup.html`이다. 배경을 복사하지 않는다.
> 모듈 맵: **`CAPABILITY-MAP-54.md`**(6모듈) · 착수 결정: **D-195**(등재 예정).
> **계획서와 코드의 어긋남 8건은 능력 맵 冒頭 표에 실측으로 정리**했다 — 계획서의 "미구현" 표기를
> 그대로 믿지 말 것(결정 저장소·SSE tier·피드백 API는 이미 있다).

## Objective

노이즈 게이트는 지금 **알람을 실제로 억제하면서 그 사실을 보여주는 화면이 없다.** 오억제 확인
수단은 `logs/alarm_decisions.jsonl`을 tail 하는 것뿐이다(`docs/28` 지적). 이 스펙이 만드는 것:

1. **운영자가 억제를 눈으로 본다** — 티어 분포·억제율·**단계별 억제량(퍼널)**·실시간 결정 피드.
2. **왜 억제됐는지 되짚는다** — 알람 1건의 결정 추적(어느 단계가 무슨 신호로 결정했는가).
3. **오억제를 교정한다** — 피드백 라벨(기존 API 재사용) + **침묵 규칙 CRUD**(신규).
4. **억제기 자체를 감시한다** — 억제율 임계·무수신 워치독(기존 `meta_alerts` 재사용).

**하지 않는 것**: 정책(매트릭스·임계·토글) **쓰기** · 게이트 판정 규칙 변경 · 기존 사용자 채팅
UI 변경 · 기존 운영자 대시보드 개편 · 기간 비교·CSV 내보내기(계획서 F4).

## 확정된 게이트 (2026-09-03 사용자)

| # | 사항 | 확정 | 귀결 |
|---|---|---|---|
| G-1 | 범위 | **F1+F2+F3 전부** | 침묵은 게이트 판정을 바꾸는 유일한 모듈 — 안전 가드 검증을 별도로 세운다 |
| G-2 | 퍼널 단계 식별 | **`NotificationDecision.stage` 신설** | reason 문구 변경에 퍼널이 깨지지 않는다. 대신 domain 변경이므로 **판정 비트 동일**의 입증 책임이 생긴다 |
| G-3 | 정책 편집 | **읽기 전용 표시 + 설정 화면 딥링크** | 쓰기 경로·안전 가드·감사가 `/admin/settings` 한 곳에만 존재. `/noise/policy`는 **GET만** |
| G-4 | 테마 | **기존 테마 시스템 정합**(D-180) | 시안의 다크 팔레트는 `[data-theme="dark"]` 블록으로, 라이트 대응색을 새로 정의한다 |

## 모듈 1 — `decision-stage` (결정 단계 태깅)

**대상**: `noise_gate/domain/notification_policy.py` · `noise_gate/infrastructure/decision_store.py`

`decide_notification`의 결정 지점 14곳 각각에 닫힌 집합의 단계 라벨을 붙인다.

```python
STAGE_NON_ALARM = "non_alarm"          # step 0.5  비운영 알람 사전분류
STAGE_SEVERITY3 = "severity3"          # step 3    심각도3 단락(억제 불가)
STAGE_SELF_HEAL = "self_heal"          # step 4    자가복구 상관
STAGE_RESOLVED = "resolved"            # step 4    독립 해소
STAGE_COLLECTION_FAILED = "collection_failed"  # step 5
STAGE_MAINTENANCE = "maintenance"      # step 6
STAGE_SILENCE = "silence"              # step 6.2  (모듈 4에서 신설)
STAGE_DEPENDENCY = "dependency"        # step 6.4
STAGE_INHIBITION = "inhibition"        # step 6.5
STAGE_FLAPPING = "flapping"            # step 6
STAGE_STORM = "storm"                  # step 7
STAGE_CORRELATION = "correlation"      # step 7.5
STAGE_ANNOTATION = "annotation"        # step 7.7
STAGE_MATRIX = "matrix"                # step 8~9  매트릭스 + 보조 조정(최종 관문)
```

- `NotificationDecision`에 `stage: str = ""` 필드를 **맨 뒤에 기본값으로** 추가한다(기존 위치 인자 호출 무영향).
- 내부 헬퍼를 `_decision(tier, reason, stage)`로 바꾼다 — **tier·reason·priority·signals 산출식은 한 글자도 바꾸지 않는다.**
- `DecisionStore.record()`가 `stage`를 레코드에 기록한다. **`signals` 동결 스키마 밖 최상위 필드**다
  (`recurrence`·`correlation_meta`의 전례를 따른다).
- **구현 중 발견해 추가**: 결정 레코드에 `alarm_name`·`server_name`이 **없었다**(`alarm_id`와 지문
  해시뿐). "상위 억제 알람 유형"도 드로어 헤더도 지문 해시로는 만들 수 없으므로, 같은 방식(동결
  스키마 밖 최상위 필드·빈 값이면 키 미기록)으로 함께 기록한다. 구 레코드는 `(미기록)`으로 모은다.

**수용 기준**
1. 14개 결정 지점 각각에 대해 stage 값이 고정된다(단계별 단위 테스트).
2. **판정 회귀 0**: 동일 입력에 대해 `(tier, reason, priority, signals)` 4-튜플이 변경 전과 **완전히 동일**하다.
   기존 `noise_gate/tests` 전량 통과가 그 증거다.
3. `stage` 없는 **기존 JSONL 레코드도** 집계에서 탈락하지 않는다(모듈 2의 폴백 매핑이 처리).

**plans/112 확장 (2026-09-22 · D-247)** — 판정(tier·reason·priority·signals·fingerprint·stage)은 비트 동일하고, 레코드에 **최상위 가산 필드**만 더한다.
- `NotificationDecision.evidence: dict`(맨 뒤 기본값) — 결정 지점이 이미 아는 근거: 비운영 마커(`non_alarm_markers` — `is_operational_alarm`과 같은 정규식) ·
  침묵 규칙 스냅샷(`rule_id`·`matcher_summary`·`expires_at`·`created_by`) · 의존성 모드(`multi_hop`/`one_hop`·`root_notified`·`root_resource_name`) ·
  주석 코로보레이션 출처 · 매트릭스(`base_tier`·`promote`·`demote`).
- 워커 탐지 근거(자가복구 소요·인히비터·플래핑 %·스톰 창)는 탐지 함수 반환형을 바꾸지 않고 `_last_*_evidence`에 남겨 그래프 입력 `detection_evidence`로 싣는다.
  **`AlarmState`에 선언 필수** — LangGraph는 TypedDict에 없는 입력 키를 노드에 넘기지 않는다.
- 게이트 노드가 **결정 단계의 근거만** 합쳐 `record(stage_evidence=…)`로 넘긴다. 같은 호출로 `db_id`·`resource_name`·`condition_log`(**200자 상한**)도 기록한다. 빈 값이면 키 없음.
- 단계 설명 정본 `STAGE_DESCRIPTIONS`(14단계 + `unknown`)를 `STAGE_LABELS` 옆에 둔다 — overfit 게이트가 이 파일의 문자열을 스캔하므로 스키마 리터럴·제품명을 쓰지 않는다.
- 수용 기준 추가: ④ 대표 입력 57건의 판정 6값 스냅샷이 변경 전과 바이트 동일(`noise_gate/tests/test_decision_snapshot.py`) ⑤ 결정 단계가 아닌 단계의 탐지값은 기록되지 않는다 ⑥ `detection_evidence` 선언을 빼면 도달 테스트가 실패한다.
- **모듈 4 배선 결함 수정(D-247 · 사용자 결정)**: 워커가 싣던 `silence_rules`도 `AlarmState`에 선언돼 있지 않아 게이트에 닿지 않았다 — 선언을 보강했다(`noise_gate/tests/test_silence_graph_wiring.py`). **침묵 on 구성에서만 판정이 바뀐다**(의도 동작 최초 발효 · 기본 off는 비트 동일).

## 모듈 2 — `decision-analytics` (퍼널·시계열·상위 억제)

**대상**: `noise_gate/infrastructure/decision_store.py`(메서드 추가)

```python
def funnel(self, *, window_seconds: int) -> dict          # raw·tiers·stages[{stage,label,residual,cut}]
def timeseries(self, *, window_seconds: int, bucket_seconds: int) -> list[dict]  # [{bucket_ts, page, ticket, dashboard, suppress}]
def top_suppressed(self, *, window_seconds: int, limit: int = 10) -> list[dict]  # [{alarm_name, stage, count}]
```

- **폴백 매핑**: `stage`가 없는 레코드는 `reason` 접두 → stage 역매핑 테이블로 채운다.
  매핑 실패는 `"unknown"` 단계로 모은다(**버리지 않는다** — 합계 항등식이 깨지면 안 된다).
- 퍼널 `residual`은 파이프라인 **선언 순서**로 누적한다. 단계 순서는 `notification_policy`의
  상수 튜플 `STAGE_ORDER` 하나에서 온다(집계가 순서를 따로 알지 않는다 — 어긋남 방지).
- 시계열 버킷 경계는 **UTC 고정 격자**(`floor(ts / bucket) * bucket`)다. 요청 시각에 따라 경계가
  흔들리면 새로고침마다 막대가 춤춘다.

**수용 기준**
1. **항등식**(구현 중 확정): 모든 결정은 정확히 한 단계에서 종결되므로 `Σ stages[].terminated == raw`이고
   `raw == Σ tiers`. `cut`은 그중 **통보되지 않은 것**(SUPPRESS·DASHBOARD)만 센 별도 지표다.
2. 빈 파일·손상 줄·`type` 필드 보유 레코드(resolution/recurrence)는 기존 `aggregate()`와 **동일하게** 제외된다.
3. 창 밖 레코드는 집계되지 않는다(`window_seconds` 경계 테스트).
4. 전량 stage 없는 구파일만으로도 퍼널이 그려진다(폴백 경로).

**plans/112 확장 (D-247)** — `timeseries(..., tz_offset_minutes=None)`: 주면 버킷 경계를 로컬 시각 격자로 정렬한다(`(b + offset) % bucket == 0` — +540이면 2시간 구간이 KST 짝수 시, 1일 구간이 KST 자정).
**미지정이면 종전 UTC 격자와 비트 동일**. `bucket_ts`는 어느 경우든 UTC 표기다.
`timeseries_report()`는 같은 값에 `excluded_no_ts`(창 안 결정 중 시각으로 구간을 정할 수 없어 막대에서 뺀 건수)를 더해 돌려준다 — 4티어 레코드면 `Σ구간 + excluded_no_ts == funnel.raw`(같은 창). `timeseries()` 반환 형태는 종전 그대로.

## 모듈 3 — `decision-lookup` (결정 조회 + 마스킹)

**대상**: `noise_gate/infrastructure/decision_store.py`(메서드 추가)

```python
def list_decisions(self, *, window_seconds, tier=None, stage=None, q=None, page=1, size=50) -> dict
def get_decision(self, alarm_id: str) -> dict | None     # 최신 1건
```

- 파일 **끝에서부터** 역순 스캔한다(최근이 먼저). 스캔 상한은 `decision_store_max_lines`(신설, 기본 20000)로
  `FeedbackStore`의 회전 상한 전례를 따른다.
- `q`는 `alarm_name`·`server_name`·`reason` 부분일치(대소문자 무시)다. 정규식이 아니다.
- **마스킹**: 반환 직전 `src.security.data_masker`가 아니라 **`noise_gate` 안의 순수 함수**로 처리한다
  (`noise_gate → src` 역방향 import 금지 — D-139). `signals` 값 중 문자열만 대상이며, 마스킹 규칙은
  API 계층에서 주입한다(저장소는 `mask_fn: Callable[[str], str] | None`을 받는다).

**수용 기준**
1. 필터 조합(tier×stage×q)이 AND로 동작하고 페이지 경계가 어긋나지 않는다.
2. `get_decision`은 동일 `alarm_id` 다중 레코드 중 **가장 최근** 것을 돌려준다.
3. `mask_fn` 주입 시 `signals`의 문자열 값에 적용되고, 미주입이면 원문 그대로다(저장소 단독 테스트 가능).
4. 파일 부재·비활성이면 빈 결과(예외 아님).

**plans/112 확장 (D-247)** — 전부 가산(새 인자 미지정이면 items·total 종전과 같음). 목록·facets·related는 **파일 1회 읽기**로 만든다.
- `tier`는 티어 **집합**도 받는다(단일 문자열은 종전 그대로). `alarm_name` 정확 일치 · `since`/`until`(`since <= ts < until`, 창과 AND).
- 응답 `facets`: `tiers`는 **tier 필터만 뺀** 집합에서 4키 항상, `stages`는 **stage 필터만 뺀** 집합에서 0보다 큰 키만 — 분포 칩이 자기 선택에 줄어들지 않게.
- `related=True`: 상관 행은 `correlation_meta.representative_fp`의 대표 판단(자기 제외 · ts ≤ 행), 자가복구 행은 같은 지문의 직전 발생(`signals.severity >= 1`) 판단. 범위는 tail 전체(창 밖 포함), 못 찾으면 `found: False`.
- `_view(..., operator_view=False)`: **기본은 사용자 뷰**라 운영자 전용 키(`condition_log` · `stage_evidence.created_by` — 침묵 규칙을 만든 운영자 계정)를 뺀다(fail-closed). 마스킹 대상에 `resource_name`·`condition_log`·`stage_evidence`(중첩 문자열)·`related`의 알람명·서버명을 더한다.
- `get_decision`은 계약 불변(가장 최근 1건) — 목록 행의 과거 판단은 화면이 행 레코드로 그린다(plans/112 F-5).

## 모듈 4 — `noise-silence` (침묵 규칙)

**대상**: `noise_gate/domain/silence.py`(신규) · `noise_gate/infrastructure/silence_store.py`(신규) ·
`noise_gate/application/alarm_worker.py`(신호 산출) · `noise_gate/domain/notification_policy.py`(단계 편입) · `src/config.py`(플래그)

### 도메인 (순수 함수 · stdlib만)

```python
@dataclass(frozen=True)
class SilenceRule:
    id: str; db_id: str; server_name: str; alarm_name: str; resource_name: str
    max_severity: int; reason: str; created_by: str
    created_at: datetime; expires_at: datetime; revoked_at: datetime | None = None

def is_active(rule, now) -> bool
def matches(rule, event, *, effective_severity: int) -> bool   # fnmatch 글롭, 빈 문자열 = 무조건 일치
def match_rules(rules, event, *, effective_severity, now) -> SilenceRule | None
```

### 안전 가드 (전부 서버 강제 — UI 토글은 표시일 뿐)

| 가드 | 강제 지점 |
|---|---|
| **심각도 3은 어떤 규칙으로도 침묵되지 않는다** | `decide_notification` step 3 단락이 step 6.2보다 앞 + `matches()`가 `effective_severity > max_severity`면 False |
| `max_severity`는 `suppress_max_severity` 이하 | API 생성 시 검증(400) |
| **전 필드 공백 규칙 금지**(전체 침묵) | API 생성 시 검증(400) + 도메인 `matches()`가 전 필드 공백이면 False |
| 만료 필수 | `expires_at` 없거나 `silence_max_duration_seconds`(기본 7일) 초과면 400 |
| 기본 off | `NOISE_SILENCE_ENABLED=false`면 워커가 조회조차 하지 않는다(회귀 0) |

### 저장소 (append-only JSONL · `FeedbackStore` 전례)

`{"op":"create", ...rule}` / `{"op":"revoke","id":...,"revoked_at":...,"revoked_by":...}` 2종 레코드를
append 하고, 조회 시 재생(replay)해 활성 목록을 만든다. **파일 재작성 없음**(감사 무결).

### 배선 (구현 중 정정)

- 워커는 저장소에서 **활성 규칙 목록**을 읽어 그래프 state(`silence_rules`)에 넣고, **매칭은
  도메인이 한다**. `flapping`·`storm`처럼 bool을 넘기려 했으나 — 규칙의 심각도 상한은 **실효
  심각도**(AI 보강 반영 후)와 대조돼야 하고 그 값은 `decide_notification` 안에서만 알 수 있다.
  워커에서 재계산하면 보강 로직이 두 곳으로 갈라진다.
- `notification_policy → silence`는 **같은 domain 계층 내 import**라 계층 규칙 위반이 아니다.
  저장소·파일은 도메인이 모른다(순수 함수만 쓴다).
- 저장소 조회는 TTL 캐시(기본 10초)로 hot-path를 보호한다.
- `decide_notification(..., silence_rules=None, now=None)` 신규 인자. 단계 위치는 **step 6(유지보수)
  다음, step 6.4(의존성) 앞**. 유지보수는 시스템 사실, 침묵은 운영자 의도 — 사실이 앞선다.
- 침묵 결정의 reason에는 **규칙 id와 사유**가 들어간다(`"침묵 규칙(slc_ab12) — 월간 배포 점검"`).

**수용 기준**
1. `silence_enabled=false`(기본)면 `decide_notification` 결과가 변경 전과 **비트 동일**하다.
2. 매칭된 규칙이 있으면 tier=`suppress`·stage=`silence`, 규칙 id가 reason과 `silence_meta`에 남는다.
3. **심각도 3 알람은 어떤 규칙에도 침묵되지 않는다**(직접 테스트 — 규칙이 `max_severity=3`이어도).
4. 만료·해제된 규칙은 매칭에서 빠지고, 해제 후에도 **생성 이력은 파일에 남는다**.
5. 전 필드 공백 규칙은 생성이 거부되고, 저장소에 억지로 넣어도 `matches()`가 False다.

## 모듈 5 — `noise-console-api` (`/admin/noise/*`)

**대상**: `src/api/routes/noise_dashboard.py`(신규) · `src/api/server.py`(라우터 등록) ·
`src/security/audit_logger.py`(침묵 변경 감사 함수 추가)

| 메서드·경로 (`/api/v1` 접두) | 인가 | 용도 |
|---|---|---|
| `GET /admin/noise/summary?range=24h` | 운영자 | KPI + 퍼널(모듈 2) |
| `GET /admin/noise/timeseries?range=24h&bucket=2h` | 운영자 | 티어 추이 |
| `GET /admin/noise/top-suppressed?range=24h&limit=10` | 운영자 | 상위 억제 유형 |
| `GET /admin/noise/health` | 운영자 | 억제율 게이지·워치독(기존 `meta_alerts` 재사용) |
| `GET /admin/noise/decisions?tier=&stage=&q=&page=&size=` | 운영자 | 결정 목록(모듈 3) |
| `GET /admin/noise/decisions/{alarm_id}` | 운영자 | 결정 추적 상세 |
| `GET /admin/noise/stream` (SSE) | 운영자 | 관제 스트림 |
| `GET /admin/noise/silences` · `POST` · `DELETE /{id}` | 운영자 | 침묵 CRUD(**변경은 감사**) |
| `GET /admin/noise/policy` | 운영자 | 매트릭스·토글·임계 **읽기 전용** + 설정 화면 딥링크 키 목록 |

- 인가는 **`require_admin_user`**(`src/api/dependencies.py:188`) 하나로 통일한다.
  SSE는 헤더를 못 실으므로 쿠키 우선 + 쿼리 토큰 폴백(`resolve_stream_user` 전례)이되,
  **운영자 판정은 따로 한다**(사용자 토큰으로는 관제 스트림을 구독할 수 없다).
- **구현 중 발견 — PAGE는 SSE에 실리지 않는다**: PAGE는 즉시 통보 경로(WorkB)라 `alarm_bus`를
  타지 않는다(`alarm_notifier`는 non-PAGE 3티어만 publish). 스트림만 믿으면 **가장 중요한 알람이
  관제 피드에서 빠진다.** PAGE를 버스에 얹으면 사용자 UI에도 새 카드가 뜨는 동작 변경이 되므로,
  화면이 **SSE(즉시성) + 30초 주기 `/decisions` 재동기화(완전성)** 로 보완한다. 회귀 0.
- **침묵 생성/해제는 감사 기록**: 행위자·시각·규칙 내용(이전/이후). `log_drm_decrypt`와 같은 형태의
  전용 함수를 `audit_logger`에 추가한다.
- `range`는 **닫힌 집합**(`1h`·`24h`·`7d`·`30d`)만 받는다. 임의 초 입력 금지(스캔 비용 가드).
- `policy` 응답에는 **값과 함께 `env_key`를 실어** UI가 설정 화면으로 딥링크할 수 있게 한다.

**수용 기준**
1. 미인증·비운영자 접근이 401/403이다(**SSE 포함**).
2. 침묵 생성/해제가 감사 파일에 남는다(행위자·이전/이후).
3. 안전 가드 위반(전체 침묵·심각도3·만료 초과·상한 초과)이 400이고 저장소에 아무것도 쓰이지 않는다.
4. `summary`의 항등식이 API 응답 수준에서도 성립한다.
5. 게이트 비활성·저장소 부재에도 200과 빈 집계를 준다(대시보드가 깨지지 않는다).

**D-245 · plans/112 개정** — 읽기 5종은 공용 `read_router`로 `/admin/noise/*`(운영자)와 `/noise/*`(로그인 사용자 · 전 존)에 같이 걸린다(D-245).
plans/112(D-247)가 더한 계약: `GET /decisions`의 `tier` 쉼표 복수값(닫힌 집합 밖 **400**)·`facets`·`related`·`alarm_name`·`since`/`until` ·
`GET /summary`의 `stages[].description`·`enabled`·`enable_key`(8단계만 키가 있고 게이트 off면 전 단계 false) · `GET /timeseries`의 `tz_offset_minutes`(−720~+840) ·
`GET /admin/noise/policy`의 env 키 접두 교정(`NOISE_GATE_*` → **`NOISE_*`** — 설정 카탈로그에 실재하는 키, 정책 키·단계 활성 키가 한 헬퍼에서 나온다) · 정책 항목 = 기존 14개 ∪ 단계 활성 필드(순서 보존 · 추가만) · `GET /timeseries`의 가산 필드 `excluded_no_ts`.
`condition_log`와 침묵 근거의 `created_by`는 **운영자 경로 응답에만** 싣는다 — 운영자 `include_router`에 뷰 모드 표시 의존성을 걸고, 표시가 없으면 사용자 뷰다(핸들러 공유 유지).

## 모듈 6 — `noise-console-ui` (관제 화면)

**대상**: `src/static/admin/noise.html`(신규) · `src/static/js/noise.js`(신규) ·
`src/static/css/style.css`(티어 토큰 추가) · `src/static/admin/dashboard.html`(진입 링크 1줄)

- 시안(`plans/54-...-mockup.html`)의 레이아웃·모션을 그대로 옮기되, **색은 테마 변수로 치환**한다.
  티어 토큰 4종(`--tier-page`·`--tier-ticket`·`--tier-dash`·`--tier-suppress`)을 `:root`(라이트)와
  `:root[data-theme="dark"]`(다크) 양쪽에 정의한다. SUPPRESS는 **회색조 유지**(계획서 §3.2 —
  "안전하다"는 오해 방지).
- `theme.js`를 `<head>`에서 **동기 로드**한다(FOUC 방지 — 기존 화면과 동일).
- 외부 라이브러리 0. 차트는 CSS(`flex` 스택 막대)와 `conic-gradient` 게이지로 그린다(폐쇄망).
- SSE 재연결은 지수 백오프(1s→30s 상한). 헤더 상태 pill이 연결/끊김을 표시한다.
- 결정 추적 드로어: 파이프라인 세로 타임라인에서 **통과 / 결정(강조) / 단락(회색)** 3상태를 구분한다.
  단락 판정은 `STAGE_ORDER`상 결정 단계 **뒤에 오는 단계**를 단락으로 표시한다.

**수용 기준**
1. 운영자 로그인 뒤에만 열리고, 라이트/다크 양쪽에서 대비가 유지된다.
2. KPI·퍼널·추이·메타·피드가 실제 API 응답으로 채워진다(하드코딩 샘플 0).
3. 피드 항목 클릭 → 드로어에 그 알람의 단계 타임라인과 signals 표가 뜬다.
4. 침묵 추가·해제가 화면에서 되고, 실패 사유(400)가 사용자에게 그대로 보인다.
5. 정책 탭은 **읽기 전용**이며 각 항목에서 설정 화면으로 이동할 수 있다.

**D-245 · plans/112 개정** — 화면은 두 벌(`admin/noise.html` · 사용자 `noise.html`)이 `js/noise.js`·`css/noise-console.css`를 공유하고 `data-noise-mode`로 운영자 전용 호출을 가른다(D-245).
plans/112(D-247)가 더한 것:
- **세부 리스트 패널 1개 + 열 프로파일** — KPI 6·티어 카드 4·퍼널 15행·상위 억제 행·추이 막대가 같은 패널을 연다(항목별 화면 금지). 패널·팝오버·툴팁 DOM은 `noise.js`가 만들고 HTML에는 `data-drill`·`data-help` 표지만 둔다.
- **(!) 설명 팝오버** — 글리프는 느낌표, 원형 외곽선·중립색(경고 pill과 다른 모양). 화면 문구는 `js/noise-help.js`, 단계 설명은 API `description`(복제 금지).
- **추이 그래프** — 축·눈금·시각 라벨·읽는 법·막대 합계·호버/포커스 툴팁·범례 합계·건수/비율 전환 · 높이는 축과 정확히 비례.
- **오버레이 층** — 상단 바(sticky) 1 · 패널 스크림 30 · 패널 31 · 드로어 스크림 40 · 드로어 41 · 팝오버·툴팁 50. 오버레이는 전부 헤더보다 위 · `position: fixed` · body 직계 — 헤더 z를 다시 올리면 표 전체를 그 위로 옮긴다.
- **좁은 화면** — 그리드 칸 `min-width: 0`(한 열 전환 시 가로 넘침 방지) · 480px 이하 퍼널 칸 축소·KPI 2열 · 표는 `.tbl-wrap` 가로 스크롤.
- 사용자 화면은 운영자 전용 API(health·silences·policy·stream)를 호출하지 않고, 침묵 버튼·`condition_log` 칸이 없다.
- **범례 토글** — 범례 항목(버튼 · `aria-pressed`)으로 티어를 숨기면 막대·합계·툴팁·비율 분모·축 최대에서 빠진다(범례 기간 합계는 원값 · 마지막 한 티어는 잠금 · 범위 전환에도 유지). `excluded_no_ts > 0`이면 범례 옆에 안내 한 줄.
- **포커스 복귀** — 진입점에 `data-focus-key`를 달아, 닫을 때 원 노드가 다시 그려져 빠졌으면 같은 키의 새 노드로, 없으면 가장 가까운 카드 제목으로 돌린다.
- **인증 실패** — 401이면 실제로 보낸 토큰의 키를 지우고 로그인으로(D-245 결함 수정), 403이면 토큰을 두고 첫 화면으로.

## Commands

```bash
# 단위 테스트 (모듈 1~4)
pytest noise_gate/tests/test_decision_stage.py noise_gate/tests/test_decision_analytics.py -q
pytest noise_gate/tests/test_silence.py -q
pytest noise_gate/tests/test_notification_policy.py noise_gate/tests/test_decision_store.py -q   # 판정 회귀

# API 테스트 (모듈 5)
pytest tests/test_api/test_noise_dashboard_api.py -q

# 전량 회귀 (판정 비트 동일의 증거)
pytest noise_gate/tests -q
pytest -q

# 품질 게이트
python scripts/arch_check.py --ci
python scripts/overfit_check.py --ci
ruff check src/ noise_gate/ tests/ && mypy src/

# 수동 확인
python -m src.main --server   # → http://localhost:8000/static/admin/noise.html
```

## Project structure

```
신규
  noise_gate/domain/silence.py                     침묵 규칙·매처(순수·stdlib)
  noise_gate/infrastructure/silence_store.py       침묵 append-only JSONL
  src/api/routes/noise_dashboard.py                /admin/noise/* 운영자 API
  src/static/admin/noise.html                      관제 화면
  src/static/js/noise.js                           집계 fetch·SSE·드로어·침묵 관리
  noise_gate/tests/test_decision_stage.py          단계 라벨·판정 회귀
  noise_gate/tests/test_decision_analytics.py      퍼널 항등식·시계열·조회·마스킹
  noise_gate/tests/test_silence.py                 침묵 도메인·저장소·게이트 통합
  tests/test_api/test_noise_dashboard_api.py                API 인가·계약·안전 가드

수정
  noise_gate/domain/notification_policy.py         stage 상수·필드·단계 라벨 + 침묵 단계
  noise_gate/infrastructure/decision_store.py      stage 기록 + funnel/timeseries/top_suppressed/list/get
  noise_gate/application/alarm_worker.py           침묵 신호 산출·state 주입
  src/config.py                                    NOISE_SILENCE_*(4건) · decision_store_max_lines
  noise_gate/application/nodes/notification_gate.py  알람명·서버명 기록 전달
  noise_gate/application/nodes/alarm_notifier.py     SSE 페이로드에 stage 추가
  src/api/server.py                                라우터 등록
  src/security/audit_logger.py                     침묵 변경 감사 함수
  src/static/css/style.css                         티어 토큰(라이트/다크)
  src/static/admin/dashboard.html                  관제 화면 진입 링크
  docs/02_decision.md                              D-195 등재
  plans/54-...-TODO.md                             상태 접미사 갱신(-TODO 해제)

건드리지 않음
  게이트 판정 규칙(tier/reason/priority/signals 산출식) · 사용자 채팅 UI ·
  기존 운영자 대시보드 탭 · /admin/settings 쓰기 경로 · FeedbackStore
```

## Code style

- 프로젝트 규약 그대로: 한국어 docstring(무엇을·왜) · 타입힌트 · `from __future__ import annotations`.
- **계층**: `noise_gate/domain`은 stdlib만(`fnmatch`·`datetime`·`dataclasses`). `infrastructure`는
  domain만 import. `src → noise_gate` 단방향(역방향 금지 — D-139).
- **overfit 게이트**: `noise_gate/domain`·`infrastructure`는 스캔 대상이다 — **독스트링에도**
  폴스타 스키마 리터럴(`cmm_*`·`POLESTAR`)을 쓰지 않는다(D-179 실사례).
- 신규 플래그는 **기본 off = 현행 동작 비트 동일**(`plans/80` §5.4-③). 예외 없음.
- 프런트는 바닐라 JS·외부 CDN 0. 기존 `admin.js` 패턴(fetch 래퍼·에러 배너)을 따른다.

## Testing strategy

| 층 | 대상 | 형태 |
|---|---|---|
| 도메인 | stage 라벨 14종 · 침묵 매처·안전 가드 | 순수 함수 단위 테스트(픽스처 이벤트) |
| **회귀** | 판정 비트 동일 | 기존 `noise_gate/tests` 전량 + 대표 입력의 `(tier,reason,priority,signals)` 스냅샷 대조 |
| 저장소 | 퍼널 항등식·창 경계·폴백 매핑·페이지·마스킹 | tmp_path JSONL 픽스처 |
| API | 인가(401/403) · 안전 가드(400) · 감사 기록 · 빈 집계 | FastAPI TestClient |
| UI | 로드·테마·드로어 | 수동 확인 + (선택) Playwright — `plans/24` 프레임워크 재사용 |

**실 LLM 호출 0** — 이 계획의 어느 경로도 LLM을 부르지 않는다(D-127 승인 불필요).

**회귀 판정은 클린 워크트리 대조로 한다**(구현 중 실측): `git worktree add <dir> HEAD`로 만든 사본에는
**`.env`가 없다**(gitignore). `.env`에 의존하는 테스트(`test_llm_gemini`의 "기본값 빈 문자열" 단언 등)는
그 차이만으로 결과가 갈리므로, 차분에 남은 항목은 **클린 사본에 `.env`를 복사해 재현되는지**까지
확인해야 자기 변경 탓인지 확정된다(확인 후 사본은 즉시 삭제 — 시크릿).

## Boundaries

**항상 한다**
- 신규 플래그는 기본 off, off일 때 현행과 비트 동일함을 테스트로 고정한다.
- 억제는 기록한다 — 침묵으로 억제된 알람도 `decision_store`에 남는다(억제 ≠ 삭제).
- 침묵 생성·해제는 감사에 남긴다(행위자·시각·내용).
- 안전 가드는 서버에서 강제한다(클라이언트 신뢰 0).

**먼저 묻는다**
- 게이트 판정 결과가 바뀌는 변경(단계 순서·매트릭스·임계 기본값).
- `signals` 동결 스키마 수정.
- 기존 `/api/v1/alarm/*` 응답 계약 변경(사용자 UI가 소비 중).

**절대 하지 않는다**
- 심각도 3 알람의 억제 경로를 만드는 일(어떤 규칙·토글로도).
- 전 필드 공백 침묵 규칙의 허용(전체 침묵).
- 결정 JSONL의 재작성·삭제(append-only 감사 무결).
- 정책 쓰기 경로의 이중화(`/admin/settings` 외 두 번째 쓰기 API).
- `noise_gate → src` 역방향 import.
