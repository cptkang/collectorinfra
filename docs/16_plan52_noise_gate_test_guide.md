# 16. 알람 노이즈 캔슬링(Plan 52) 테스트 가이드

> 작성일: 2026-07-02
> 대상: `plans/52-alarm-noise-cancellation.md` (E1~E5 구현 완료, D-048 계열)
> 관련: Plan 52 §10(단계별 verify)·§11(테스트 계획)·§11.1(baseline 정책), D-048, D-049

노이즈 게이트(4-티어 발송 판단) 기능의 검증 방법을 정리한다. 테스트는 **3계층**으로 나뉜다:

| 계층 | 대상 | 외부 의존 | CI 동작 |
|------|------|----------|---------|
| **단위/그래프 테스트** | 결정 파이프라인·노드·라우팅 (모킹) | 없음 | 항상 실행 |
| **통합 테스트** | 실 폴스타 DB(도커) 스키마 대상 end-to-end | polestar_pg 컨테이너 | 미가용 시 자동 skip |
| **라이브 테스트** | Gemini API 실호출(E5 트랙 B agentic 경로) | Gemini API 키 | 키 없으면 자동 skip |
| **시나리오 E2E 검증** | **임의 이벤트를 직접 주입** → 실 LLM 분석·게이트 판단·티어 라우팅을 운영 경로 그대로 관찰 (§0 Quick Guide, §6) | 실행 중인 서버 + Redis + LLM + 신호 DB | `scripts/noise_gate_scenario_test.py`로 자동 실행 (CI 밖 수동 트리거) |

---

## 0. Quick Guide — 5분 만에 시나리오 E2E 돌리기

임의 이벤트 주입 → 노이즈 캔슬링·LLM 연동 검증(§6)을 최단 경로로 수행하는 절차.
각 단계의 상세·주의사항은 괄호의 절을 참고한다.

```bash
# ① .env 설정 (§6.1) — 게이트·워커 활성
ALARM_ENABLED=true
ALARM_MIN_SEVERITY=1
NOISE_ENABLE_NOISE_GATE=true
# 매트릭스 시나리오용 (인라인 주석 금지 — pydantic-settings가 값에 포함시킴)
NOISE_IMPORTANCE_VALUE_MAP_CSV=1=낮음,2=보통,3=높음
# LLM_PROVIDER=(ollama|fabrix|gemini) + 접속 정보/키(.encenv)가 유효해야 함

# ② 인프라 기동 확인
redis-cli -p ${REDIS_PORT:-6379} ping                # PONG (.env REDIS_PORT와 일치)
cd testdata/pg && docker compose up -d && cd -       # 폴스타 픽스처(noise-test-*)

# ③ 서버 실행 (포트는 .env API_PORT — 기본 8000)
python -m src.main --server
# 기동 로그 확인: "알람 워커 시작 (... min_severity=1)" / "인증 DB 초기화 완료"

# ④ 시나리오 실행 — 토큰 발급·이벤트 주입·티어 판정 자동 (§6.6)
python scripts/noise_gate_scenario_test.py --register --mode api      # 서버+LLM만 필요
python scripts/noise_gate_scenario_test.py --mode redis \
  --redis-url redis://localhost:${REDIS_PORT:-6379}/0                 # 워커 운영 경로

# ⑤ 결과 확인 (§6.5)
tail -5 logs/alarm_decisions.jsonl | jq '{tier, reason}'
```

| 단계에서 막히면 | 원인/조치 |
|----------------|----------|
| 로그인 503 | 인증 DB 미초기화 — `AUTH_AUTH_DB_URL`(또는 `DB_CONNECTION_STRING`) 접속 확인 (§6.1) |
| 결정 레코드 없음 | `NOISE_ENABLE_NOISE_GATE`/`ALARM_ENABLED`/`ALARM_MIN_SEVERITY=1` 확인. 재실행 dedup은 스크립트가 run_id로 회피 |
| 매트릭스 시나리오 전부 PAGE | 신호 DB(DBHub로 `db_id` 조회) 미가용 → 수집실패 보수화(정상 폴백, §6.2). DBHub·`ACTIVE_DB_IDS`·중요도 매핑 확인 |

실측(2026-07-03, gemini): `--mode api` 3/3 PASS, `worker-dedup`·`worker-self-heal` 2/2 PASS.

---

## 1. 기본 실행 (단위/그래프 — 외부 의존 없음)

```bash
# 알람 스위트 전체 (Plan 52 전 Phase 포함)
pytest tests/test_alarm/ -q

# 아키텍처 계층 위반 검사 (합격 기준 ⓒ)
python scripts/arch_check.py --ci
```

- 2026-07-02 실측: **`tests/test_alarm/` 398 passed**(통합·라이브는 조건 미충족 시 skip),
  arch_check 위반(error) 0 (warning 3건은 orchestration→prompts 직접 참조 — Plan 52 무관 기존 경고).
- 통합·라이브 테스트는 조건 미충족 시 skip되므로 위 명령만으로 CI-safe하게 돌아간다.

### 1.1 합격 기준 (Plan 52 §11.1 baseline 정책)

회귀 판정은 **"repo 전체 그린"이 아니라 "baseline 대비 신규 실패 0"** 이다. 현 브랜치
(multiintent)에는 Plan 52와 무관한 기존 실패(약 46건 — `_llm_classify` 반환구조,
`output_generator` 시그니처, API 인증 fixture 등 multiintent 부채)가 존재한다.

Plan 52 합격 기준:

- ⓐ `tests/test_alarm/` 전부 통과 (신규 `test_notification_policy` / `test_decision_store` /
  `test_polestar_noise_context` 포함)
- ⓑ `AppConfig` 정상 생성 (`.env` 파싱 에러 없음)
- ⓒ `python scripts/arch_check.py --ci` 위반 0
- ⓓ 게이트 오프 회귀 0 — `enable_noise_gate=false`면 기존 발송 경로 무변경
  (`test_gate_off_regression.py`, `test_e2_flags_off_regression.py`)

---

## 2. Phase별 테스트 파일 매핑

모두 `tests/test_alarm/` 아래에 있다. Phase 경계는 Plan 52 §10 기준.

### E1 — 신호 수집 + 매트릭스 MVP (결정 파이프라인 코어)

| 파일 | 검증 |
|------|------|
| `test_notification_policy.py` | 심각도×중요도 매트릭스, **심각도3 절대 PAGE**, 유지보수 SUPPRESS, 자가복구 상관, 수집 실패 시 보수적 PAGE, `signals` 키 스키마(§8.2), 독립 해소 SUPPRESS/`resolved_to_dashboard` |
| `test_polestar_noise_context.py` | 고정 SQL 수집(모킹) — 중요도/유지보수/의존성/알림정책. **한쪽 조회만 실패하는 부분 실패 케이스 포함**(Known Mistakes 2026-06-29: resource/noti를 개별 try로 부분 반환) |
| `test_decision_store.py` / `test_decision_store_persist.py` | `NotificationDecision` JSONL 적재(tier·reason·priority·signals·fingerprint·ts), **DASHBOARD/SUPPRESS도 기록**(억제≠삭제) |
| `test_notifier_tier_routing.py` | PAGE=workB/webhook, TICKET=배치 큐, DASHBOARD=SSE만, SUPPRESS=감사만 |
| `test_graph_wiring_flags.py` | 플래그 조합 배선(§8.1) — history off + gate on이면 **enricher 강제 포함** |
| `test_min_severity_gate_handoff.py` | §4.8 역할 분리 — severity 1이 게이트 도달, severity 0 자가복구용 전달, 심각도3 워커·게이트 드롭 금지 |
| `test_gate_off_regression.py` | `enable_noise_gate=false` → 기존 경로 무변경 |
| `test_noise_gate_graph_integration.py` | 게이트 포함 그래프 end-to-end(모킹) 티어 산출 |

### E2 — 연쇄/스톰/플래핑

| 파일 | 검증 |
|------|------|
| `test_dependency_suppress.py` | 부모 비정상 → 자식 억제·부모 PAGE, stale 시 보수적 PAGE |
| `test_inhibition.py` | 동일 서버 상위 심각도 발생 중 → 하위 음소거, 자기억제 금지 |
| `test_flapping.py` / `test_flapping_gate.py` | Nagios 가중 %-state-change, 히스테리시스(high 20%/low 5%) |
| `test_storm_grouping.py` | 사건창 내 다발 → 1건화, 대표 외 억제 |
| `test_sev3_repeat_interval.py` | 심각도3 전용 재통보 간격(`sev3_repeat_interval_seconds`) |
| `test_e2_flags_off_regression.py` | E2 플래그 전부 off → E1 동작 무변경 |

### E3 — AI 심각도 보강 + 메타모니터링 + 지표

| 파일 | 검증 |
|------|------|
| `test_severity_signatures.py` | Plan 51 부록 A.1 결정적 시그니처 스캔(OOM·soft lockup 등 → 상향) |
| `test_ai_severity_escalate_only.py` | AI 심각도는 **상향만**(하향으로 SUPPRESS 절대 불가), 심각도3 PAGE 불변 |
| `test_meta_monitoring.py` | 억제율 임계 초과·이벤트 무수신 메타경보 |
| `test_metrics_endpoint.py` | `GET /alarm/metrics` — 티어별 건수·액션가능 비율·억제율, 미산출 지표는 `unavailable_metrics` 명시 |
| `test_ticket_batch_queue.py` | TICKET 일배치 요약 큐 적재 |
| `test_sse_bridge.py` | 워커→UI Redis pub/sub SSE 브리지(D-048.10, TICKET/DASHBOARD) |
| `test_auto_recovery_mttr.py`, `test_incident_*.py` | (D-049) ack/incident 라이프사이클 계측 — MTTA/MTTR/사건전환율 |

### E4 — LLM 액션가능성 (피드백 few-shot)

| 파일 | 검증 |
|------|------|
| `test_feedback_store.py` | 운영자 "노이즈/유효" 피드백 JSONL 저장(graceful) |
| `test_llm_actionability.py` | few-shot 주입·결정적 step 9 반영(actionable→승격/noise→강등, `severity≤suppress_max` 가드·승격 우선), 추가 LLM 호출 없음, 심각도3 PAGE 불변 |

### E5 — agentic Advisory Enricher

| 파일 | 검증 |
|------|------|
| `test_agentic_enricher.py` | 3경로 자동 선택(`_select_backend`: 트랙 B/트랙 A/결정적 only), 승격 전용, 심각도3 미개입, 메시지형 알람 한정, 임계형 호출 생략, 도구 읽기전용·ReAct 호출 상한(fake bound LLM) |
| `test_agentic_enricher_gemini_live.py` | 실 Gemini tool-calling으로 트랙 B 완주 (§4 참고) |

---

## 3. 통합 테스트 — 실 폴스타 DB (도커)

`test_polestar_noise_context_integration.py`는 모킹이 아닌 **실행 중인 polestar_pg 컨테이너의
실제 스키마 + Plan 52 픽스처**를 대상으로 고정 SQL → `decide_notification` → 4-티어 라우팅을
end-to-end 검증한다. 모킹 테스트가 못 잡는 결함(테이블 부재·타입·권한)을 여기서 잡는다
(Known Mistakes 2026-06-29 — 신호 수집류 통합 검증은 실 환경에서 수행).

```bash
# 1) 폴스타 테스트 DB 기동 (상세: docs/10_polestar_test_env_setup.md)
cd testdata/pg && docker compose up -d

# 2) 통합 테스트 실행 (컨테이너 미가용 시 자동 skip)
pytest tests/test_alarm/test_polestar_noise_context_integration.py -q
```

- 접속 정보: `POLESTAR_PG_CONNECTION` 환경변수
  (기본 `postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb`)
- 픽스처: `testdata/pg/init/06_plan52_noise_fixtures.sql`
  (`noise-test-high/med/low` 등 중요도·유지보수·알림정책 표본 서버) — init 04~06 적용 필요
- 중요도 코드 매핑은 테스트 정책상 `1=낮음, 2=보통, 3=높음`으로 가정
  (실 인스턴스별 확정은 §13.1 #1 — 운영 적용 시 `NOISE_IMPORTANCE_VALUE_MAP_CSV` 재확인)

---

## 4. 라이브 테스트 — Gemini 트랙 B (E5)

vLLM 인프라 없이 **Gemini 네이티브 tool-calling**으로 agentic 경로(트랙 B)를 실검증한다.
Gemini API 키가 없으면 모듈 전체 skip(CI 안전). LLM 응답이 비결정적이므로 "상향이 일어났다면
반드시 시그니처 상향값(=3)"이라는 **불변식을 조건부 단언**한다.

```bash
# 사전 조건: gemini extra 설치 + 키 설정
pip install -e ".[gemini]"
# .env 또는 환경변수: LLM_GEMINI_API_KEY(또는 GOOGLE_API_KEY, ORCHESTRATOR_API_KEY)

pytest tests/test_alarm/test_agentic_enricher_gemini_live.py -q
```

- 키 해석은 `AppConfig`(`.env`/`.encenv` 포함)를 그대로 사용한다.
- **vLLM 실서빙 트랙 B 라이브 검증은 후속**(vLLM 서빙 환경 준비 후) — 현재는 fake bound LLM
  단위 테스트 + Gemini 라이브로 갈음(Plan 52 상단 상태 참고).

---

## 5. 수동/운영 검증 (기능 활성 후 관찰)

옵트인 플래그를 켠 뒤 실제 워커 경로에서 확인한다. 전 플래그 기본값은 off(회귀 0)이며
`.env.example` 182~236행에 주석과 함께 정리돼 있다.

1. **활성화**: `.env`에 `NOISE_ENABLE_NOISE_GATE=true` + `ALARM_MIN_SEVERITY=1` 권장(§4.8 —
   severity 1이 게이트에 도달해야 TICKET/DASHBOARD/SUPPRESS 행이 동작). E2/E3/E4/E5 기능은
   각 플래그(`NOISE_FLAPPING_ENABLED`, `NOISE_ENABLE_AI_SEVERITY_BOOST`,
   `NOISE_ENABLE_LLM_ACTIONABILITY`, `NOISE_ENABLE_AGENTIC_ENRICHER` 등)로 개별 옵트인.
2. **결정 감사 확인**: `logs/alarm_decisions.jsonl`(=`NOISE_DECISION_STORE_PATH`)에 알람마다
   tier·reason·priority·signals·fingerprint가 적재되는지 확인. **SUPPRESS/DASHBOARD도 기록**되어야
   정상(억제≠삭제).
3. **운영 지표**: `GET /alarm/metrics` — 티어별 건수·억제율·액션가능 비율. 산출 불가 지표는
   `null` + `unavailable_metrics`에 사유가 명시되는지 확인(환각 수치 금지).
4. **메타모니터링**: 억제율이 `NOISE_META_ALERT_SUPPRESS_RATIO`(기본 0.9) 초과 또는 집계 창 내
   무수신이면 메타경보가 발생하는지 확인.
5. **UI**: 알람 카드(SSE 라이브피드, `NOISE_SSE_BRIDGE_ENABLED=true` 시 워커 TICKET/DASHBOARD도
   표시), 피드백 버튼(노이즈/유효 → `POST /alarm/feedback` → E4 few-shot 입력), incident ack
   (D-049, `NOISE_INCIDENT_TRACKING_ENABLED=true`).

---

## 6. 시나리오 E2E 검증 — 임의 이벤트 주입으로 노이즈 캔슬링·LLM 연동 확인

pytest 스위트(§1~§4)는 각 단계를 격리 검증한다. 이 절은 **테스터가 임의 알람 이벤트를 발생시켜,
에이전트가 실제 운영 경로에서 (a) 노이즈를 적절히 캔슬링하는지, (b) LLM 분석(alarm_analyzer)과
잘 연동되어 판단하는지**를 end-to-end로 확인하는 방법이다.

### 6.1 사전 준비 및 서버 실행

```bash
# ① .env 핵심 설정
# 서버 lifespan에서 AlarmWorker 자동 기동 (※ .env에 인라인 주석 금지 — 값에 포함되어 파싱 에러)
ALARM_ENABLED=true
# severity 1이 게이트에 도달하도록 (§4.8)
ALARM_MIN_SEVERITY=1
NOISE_ENABLE_NOISE_GATE=true
# 시나리오별 추가 옵트인: NOISE_INHIBITION_ENABLED / NOISE_STORM_GROUPING_ENABLED /
#   NOISE_FLAPPING_ENABLED / NOISE_DEPENDENCY_SUPPRESSION / NOISE_ENABLE_AI_SEVERITY_BOOST 등
# 매트릭스 시나리오용(§6.3 주의사항 참고): NOISE_IMPORTANCE_VALUE_MAP_CSV=1=낮음,2=보통,3=높음

# ② 인프라 확인 — Redis(워커 소비·필수), 폴스타 테스트 DB(신호 조회용)
redis-cli -p ${REDIS_PORT:-6379} ping                 # PONG 확인 (.env REDIS_PORT와 일치해야 함)
cd testdata/pg && docker compose up -d && cd -        # noise-test-* 픽스처 포함 (docs/10 참고)

# ③ 서버 실행 (uvicorn + AlarmWorker lifespan 기동, 포트는 .env API_PORT — 기본 8000)
python -m src.main --server
# 기동 로그에서 확인: "알람 워커 시작 (stream=alarm:raw ... min_severity=1)"
#                    "인증 DB 초기화 완료"  ← 없으면 로그인이 503 (아래 주의)
```

- **LLM**: `LLM_PROVIDER`(ollama | fabrix | gemini) + 해당 접속 정보 — alarm_analyzer가 실호출된다.
  운영은 fabrix, 개발은 ollama 로컬 또는 gemini+API 키.
- **인증 DB 주의**: 로그인/analyze-test는 `require_user`라 인증 DB가 필요하다.
  `AUTH_AUTH_DB_URL`(미설정 시 `DB_CONNECTION_STRING` 폴백)이 접속 가능한 PostgreSQL을
  가리켜야 하며, 실패 시 기동 로그에 "인증 DB 초기화 실패"가 남고 로그인이 503을 반환한다.
- **신호 DB(매트릭스 시나리오)**: 중요도/유지보수 신호는 워커 enricher가 이벤트 `dbId`
  프로필로 조회한다 — **`db_id`가 `ACTIVE_DB_IDS`에 등록**되어 있고 해당 백엔드(DBHub MCP)가
  살아 있어야 한다. 미가용이면 게이트는 수집실패 보수화로 전부 PAGE(그 자체가 §6.3 #10 검증).
  개발 환경은 도커 폴스타 픽스처 서버명(`noise-test-high/med/low/maint`)을 `serverName`으로 사용.
- **인증 토큰** (API 주입 경로용 — §6.6 스크립트를 쓰면 자동 처리):

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<계정>","password":"<비밀번호>"}' | jq -r .access_token)
# 계정이 없으면: POST /api/v1/auth/register {"user_id","username","password"} (즉시 가입)
```

### 6.2 이벤트 주입 경로 — 2가지 (검증 목적에 따라 선택)

| 경로 | 방법 | 커버 범위 | 용도 |
|------|------|----------|------|
| **A. 테스트 API** | `POST /api/v1/alarm/analyze-test`(구조화 필드) 또는 `/alarm/analyze-test/raw`(폴스타 원문 JSON) | LLM 분석 + (dry_run=false 시) 게이트·notifier. **워커 in-memory 상태 미경유** | LLM 연동·매트릭스·신호 조회 시나리오를 빠르게 반복 |
| **B. Redis Stream 주입** | `XADD alarm:raw` → 워커가 소비 | **운영 경로 100%** — 워커 dedup·자가복구 상관·인히비션·플래핑·스톰(전부 워커 in-memory 상태) 포함 | 상태 기반 시나리오(4~8번)와 최종 인수 검증 |

> ⚠️ 경로 구분이 중요하다 (실측 2026-07-03):
> - **핑거프린트 dedup·self-heal·인히비션·플래핑·스톰은 `AlarmWorker`의 in-memory 상태로 판정**
>   되므로 경로 A(요청 단위, 상태 없음)로는 재현되지 않는다. 반드시 경로 B로 검증한다.
> - **경로 A는 enricher를 거치지 않아 `noise_context`(중요도/유지보수/알림정책)를 수집하지 않는다**
>   → 게이트는 수집실패 보수화로 **severity≥1이면 전부 PAGE**. 따라서 경로 A로 게이트까지 검증할
>   수 있는 것은 심각도3 단락·보수적 PAGE·LLM 연동뿐이고, **매트릭스/유지보수 시나리오는 경로 B 전용**.
> - 반대로 경로 A는 응답 본문에 LLM 분석 결과가 바로 담겨 LLM 연동 확인에 편하다.

**경로 A 예시** (원문 JSON 방식 — 게이트·발송까지 실행하려면 `dry_run:false`):

```bash
curl -s -X POST http://localhost:8000/api/v1/alarm/analyze-test/raw \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{
  "message": "{\"dbId\":\"polestar_pg\",\"serverName\":\"noise-test-low\",\"hostname\":\"noise-test-low\",\"ipAddress\":\"10.0.0.1\",\"resourceAncestry\":\"\",\"alarmId\":\"SIM-001\",\"severity\":1,\"alarmStatus\":\"NOT_ACK\",\"resourceType\":\"server.Server\",\"resourceName\":\"CPU\",\"alarmName\":\"CPU 사용률 임계 초과\",\"alarmTime\":\"20260702120000\",\"conditions\":\"cpu>90\",\"conditionLog\":\"cpu=95\"}",
  "dry_run": false, "send_notification": true, "channels": [],
  "query_history": false
}' | jq '{analysis, notification_channels, error}'
```

- `dry_run:true`(기본)는 **LLM 분석+미리보기만 — 게이트가 실행되지 않는다.** 게이트·티어
  라우팅·decision_store 기록까지 보려면 `dry_run:false, send_notification:true`로 호출한다.
  실발송이 부담되면 `channels:[]`(빈 배열)로 외부 채널을 차단하거나 webhook을 로컬 수신기로 돌린다.
- `simulated_history`로 과거 이력을 임의 구성해 패턴(주기적/급증) → `is_routine` 시나리오를
  DB 없이 재현할 수 있다(형식은 Swagger `/docs` 참고).

**경로 B 예시** (운영 경로 그대로 — 메시지 필드는 `data`, 값은 폴스타 단일행 JSON):

```bash
redis-cli XADD alarm:raw '*' data '{"dbId":"polestar_pg","serverName":"noise-test-low","hostname":"noise-test-low","ipAddress":"10.0.0.1","resourceAncestry":"","alarmId":"SIM-002","severity":1,"alarmStatus":"NOT_ACK","resourceType":"server.Server","resourceName":"CPU","alarmName":"CPU 사용률 임계 초과","alarmTime":"20260702120500","conditions":"cpu>90","conditionLog":"cpu=95"}'
```

> `alarmId`는 발생건마다 고유하게(폴스타 실동작과 동일), 재발생 시나리오는 **alarmId만 바꾸고
> 서버명·알람명·resource를 동일하게** 주입한다(핑거프린트 = db_id+server+alarm_name+resource).

### 6.3 시나리오 매트릭스 (주입 → 기대 판정)

각 행을 순서대로 주입하고, 기대 티어를 `logs/alarm_decisions.jsonl`(§6.5)에서 확인한다.
경로 열의 B는 워커 상태 기반이라 Redis 주입 필수.

| # | 시나리오 (주입 방법) | 기대 판정 | 검증하는 규칙 | 경로 |
|---|---------------------|----------|--------------|------|
| 1 | `severity:3` + 아무 서버 (유지보수 서버 `noise-test-*`여도) | **PAGE** (reason에 심각도3 단락) | 심각도3 절대 PAGE (§6 step 0) | A/B |
| 2 | `severity:1` + 저중요도 서버(`noise-test-low`) | DASHBOARD (E3 결정: sev1×낮음 셀 SUPPRESS→DASHBOARD) | 매트릭스 (§3.2) | **B** |
| 3 | `severity:2` + 고중요도 서버(`noise-test-high`) | PAGE | 매트릭스 | **B** |
| 4 | `severity:2` + 유지보수 서버(`noise-test-maint`, IS_MAINTENANCE=1) | SUPPRESS (감사엔 기록) | 유지보수 억제 (§3.5) | **B** |
| 5 | 동일 서버·알람명·resource로 2건 연속(alarmId만 상이) | 2건째는 **결정 자체가 미기록**(워커가 게이트 이전에 재통보 억제) | 핑거프린트 dedup (§6.1) | **B** |
| 6 | `severity:2` 발생 → 5분 내 같은 핑거프린트 `severity:0` | 해소는 SUPPRESS(자가복구 매칭) + MTTR 기록 | 자가복구 상관 (§3.7) | **B** |
| 7 | 같은 서버에 `severity:3` 발생 중 `severity:1` 주입 (`NOISE_INHIBITION_ENABLED=true`) | 하위 음소거 | 인히비션 (§3.4) | **B** |
| 8 | 같은 서버로 60초 내 6건(임계 초과) (`NOISE_STORM_GROUPING_ENABLED=true`) | 대표 1건 외 억제 | 스톰 그룹핑 (§3.8) | **B** |
| 9 | 같은 핑거프린트로 firing(2)/clear(0) 교차 반복 (`NOISE_FLAPPING_ENABLED=true`) | 플래핑 판정 후 보류 | 플래핑 (§3.7) | **B** |
| 10a | **미등록 서버명** + `severity:2` (조회는 성공, 행 없음) | TICKET (미식별 중요도→**보통 취급**, R-4) | 보수적 매핑 (§6.3) | **B** |
| 10b | **신호 수집 실패**(DB/DBHub 미가용) + `severity:2` — 경로 A는 구조상 항상 이 경우 | **PAGE** (reason "신호 수집 실패 — 보수적 PAGE") | 재현율 우선 (§6.3) | A/B |

> 매트릭스 시나리오(2·3·4·10a) 주의: `NOISE_IMPORTANCE_VALUE_MAP_CSV`가 비어 있으면 모든
> 중요도가 "보통" 취급되어 기대 티어가 달라진다. 도커 픽스처 기준 `1=낮음,2=보통,3=높음` 설정.
> 재실행 시 같은 서버·알람명 조합은 dedup(TTL 4h)에 걸리므로 **알람명을 실행마다 바꿔야 한다**
> (§6.6 스크립트는 run_id로 자동 처리).

### 6.4 LLM 연동 검증 시나리오 (분석이 판단에 반영되는가)

LLM은 판단자가 아니라 **보조 입력**(D-048.1)이므로, "LLM 출력이 생성되는가"와 "그 출력이
게이트 signals에 정확히 전달·반영되는가"를 나눠 확인한다.

| # | 시나리오 | 확인 포인트 |
|---|---------|------------|
| L1 | **기본 분석 생성**: 아무 알람이나 경로 A(`dry_run:true`)로 주입 | 응답 `analysis`에 summary/probable_cause/recommended_action이 `conditionLog` 근거로 한국어 생성. `error=null` |
| L2 | **is_routine 보조 입력**: 경로 A + `simulated_history`로 주기적 패턴(등간격 다수 발생) 구성, `severity:2`·보통중요도 | `analysis.is_routine=true` → (dry_run:false 재호출 시) decision의 `signals.is_routine=true`, 티어 강등 확인. 같은 이벤트를 이력 없이 주입하면 강등 없음(대조군) |
| L3 | **AI 심각도 상향(§3.11)**: `NOISE_ENABLE_AI_SEVERITY_BOOST=true` + `conditionLog`에 OOM 로그(`kernel: Out of memory: Killed process ...`) + `severity:1` 메시지형 알람 | `signals.ai_severity=3`·`effective_severity=3` → **PAGE로 승격**. 시그니처가 결정적 상향(LLM 무관 동작)인지 로그로 확인 |
| L4 | **상향 전용 불변**: L3 반대로 `severity:3` + "무해한" 메시지 | LLM이 뭐라 하든 **하향·억제 없음** — PAGE 유지 (R-10) |
| L5 | **E4 피드백 few-shot**: `NOISE_ENABLE_LLM_ACTIONABILITY=true`, ① 알람 주입 → ② UI 피드백 버튼 또는 `POST /api/v1/alarm/feedback`(label=noise) → ③ **유사 알람**(같은 알람명·서버) 재주입 | ③의 분석 프롬프트에 few-shot 주입(`logs/alarm_feedback.jsonl` 적재 확인) → `severity≤suppress_max`면 강등 반영. `severity:3`은 피드백 무관 PAGE |
| L6 | **E5 agentic(옵션)**: `NOISE_ENABLE_AGENTIC_ENRICHER=true` + gemini/vLLM, 경계 사례 메시지형 알람 주입 | 도구 호출(읽기전용) 후 `signals` 승격 전용 반영. 임계형(CPU 수치) 알람은 enricher 호출 생략 |
| L7 | **LLM 장애 내성**: LLM 키를 일부러 잘못 설정(또는 엔드포인트 차단) 후 `severity:2` 주입 | 분석 실패해도 **보수적 PAGE**(억제 방향으로 가지 않음), 워커 비정지. `error` 필드에 원인 |

### 6.5 결과 확인 방법

```bash
# ① 결정 감사(핵심) — 알람마다 티어·근거·신호 스냅샷
tail -f logs/alarm_decisions.jsonl | jq '{tier, reason, priority,
  sev: .signals.severity, ai: .signals.ai_severity, routine: .signals.is_routine,
  importance: .signals.importance, maint: .signals.maintenance, fingerprint}'

# ② 운영 지표 — 티어별 건수·억제율·메타경보
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/alarm/metrics" | jq '{by_tier, suppress_ratio, meta_alerts}'
```

- ③ **웹 UI**: 알람 라이브피드 카드(SSE)에서 DASHBOARD 티어 표시·피드백 버튼 동작 확인
  (워커 경로 TICKET/DASHBOARD는 `NOISE_SSE_BRIDGE_ENABLED=true` 필요).
- ④ **TICKET 큐**: `logs/alarm_ticket_queue.jsonl` 적재 확인.
- **판정 기준**: 시나리오 표의 기대 티어와 ①의 `tier`가 일치하고, `reason`이 해당 규칙
  (예: "심각도3", "maintenance", "dedup")을 명시하며, LLM 산출(`analysis`)과 `signals`의
  is_routine/ai_severity가 일치하면 합격. SUPPRESS인데 ①에 기록 자체가 없으면 **실패**(억제≠삭제 위반).

### 6.6 자동 실행 스크립트 — `scripts/noise_gate_scenario_test.py`

§6.1~§6.5(토큰 발급→이벤트 주입→결정 감사 판정)를 자동화한 실행기. 서버가 §6.1대로 떠
있으면 명령 한 줄로 시나리오를 돌리고 PASS/FAIL을 판정한다(리포지토리 루트에서 실행 —
`logs/alarm_decisions.jsonl`을 직접 읽으므로 서버와 같은 호스트 전제).

```bash
# 시나리오 목록
python scripts/noise_gate_scenario_test.py --list

# API 경로 시나리오 (서버+LLM만 필요, 계정 없으면 --register로 자동 가입)
python scripts/noise_gate_scenario_test.py --base-url http://localhost:8000 --register --mode api

# 워커(운영 경로) 시나리오 — redis-url은 .env REDIS_PORT와 일치시킬 것
python scripts/noise_gate_scenario_test.py --base-url http://localhost:8000 \
  --mode redis --redis-url redis://localhost:6379/0

# 전체 / 특정 시나리오만
python scripts/noise_gate_scenario_test.py --mode all
python scripts/noise_gate_scenario_test.py --only worker-dedup,worker-self-heal
python scripts/noise_gate_scenario_test.py --only api-ai-boost   # opt 시나리오는 --only로만
```

| 시나리오 id | 경로 | 검증 (§6.3/§6.4 대응) |
|-------------|------|----------------------|
| `api-sev3-page` | A | 심각도3 절대 PAGE (#1) |
| `api-conservative-page` | A | noise_context 미수집→수집실패 보수적 PAGE (#10b) |
| `api-llm-analysis` | A | LLM 분석 생성 — summary/probable_cause (L1) |
| `api-ai-boost` (opt) | A | OOM 시그니처 sev1→ai_severity=3→PAGE 승격 (L3, boost 플래그 필요) |
| `worker-high-sev2-page` | B | 매트릭스 sev2×높음→PAGE (#3) |
| `worker-low-sev1-dashboard` | B | 매트릭스 sev1×낮음→DASHBOARD (#2) |
| `worker-maint-suppress` | B | 유지보수 SUPPRESS (#4) |
| `worker-unknown-server-ticket` | B | 미등록 서버→보통 취급→TICKET (#10a) |
| `worker-dedup` | B | 핑거프린트 재발생 억제 — 2건째 미기록 (#5) |
| `worker-self-heal` | B | 자가복구 상관 — 해소 SUPPRESS (#6) |

- 실행마다 `run_id`로 알람명을 유일화해 dedup TTL(4h) 충돌을 피하고, `channels:[]`로 외부
  채널 실발송을 차단한다(게이트 판단·감사 기록은 그대로).
- 매트릭스 시나리오(worker-high/low/maint/unknown)는 §6.1 신호 DB 전제(DBHub로 `db_id` 조회
  가능 + `NOISE_IMPORTANCE_VALUE_MAP_CSV` 설정)가 충족돼야 기대 티어가 나온다. 미충족이면
  전부 "수집실패 보수적 PAGE"로 떨어진다(그 자체는 #10b 동작).
- 실측(2026-07-03, gemini): `--mode api` 3/3 PASS, `--only worker-dedup,worker-self-heal`
  2/2 PASS. 매트릭스 4종은 DBHub 미기동 환경이라 보수적 PAGE 폴백만 확인됨 — DBHub 포함
  환경에서 `--mode redis` 전체 실행 필요.

---

## 7. 불변식 체크리스트 (모든 계층 공통)

어떤 테스트를 추가·수정하든 아래 불변식이 깨지면 실패로 간주한다 (Plan 52 §1.4·§6.3, D-035/D-048):

- [ ] **심각도 3은 어떤 신호 조합에서도 PAGE** (유지보수·플래핑·AI·피드백 무관)
- [ ] 신호 수집 실패·미식별 중요도·모호 → **보수적 PAGE** (재현율 우선)
- [ ] AI/agentic/영향 신호는 **승격(상향) 전용** — 하향으로 억제 불가 (R-10/R-12)
- [ ] 억제·강등도 **감사 기록 유지** (decision_store에 SUPPRESS 포함)
- [ ] `enable_noise_gate=false` → 기존 발송 경로 바이트 단위 무변경 (회귀 0)
- [ ] 판단은 결정적 `decide_notification` — LLM 출력은 보조 입력만

## 8. 주의사항 (Known Mistakes 계승)

- **결정적 매트릭스 셀·티어 상수를 변경하면**, 그 값을 단언하는 테스트가 여러 파일에 흩어져
  있으므로(`test_notification_policy` / `test_noise_gate_graph_integration` /
  `test_polestar_noise_context_integration` 등) **repo 전체 grep으로 전수 갱신** 후
  `pytest tests/test_alarm/` 전체 실행으로 누락을 포착한다 (2026-06-30 D-048.8 교훈).
- `BaseSettings` 계열 config를 테스트에서 직접 생성할 때는 **검증 대상 필드를 명시**해 로컬
  `.env` 값 누수를 차단한다 (2026-06-17 교훈). 게이트 테스트들이 `SimpleNamespace` 덕 타이핑
  config를 쓰는 이유.
- 노이즈 신호 수집 mock 테스트에는 **한쪽 조회만 실패하는 케이스**(resource/noti 개별 실패)를
  반드시 포함한다 (2026-06-29 교훈 — 실 DB에서만 발현되는 부분 실패 결함 방지).
