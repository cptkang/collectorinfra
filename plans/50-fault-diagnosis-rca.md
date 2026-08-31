# 50. 장애진단 · 원인분석 (Fault Diagnosis & Root Cause Analysis)

> 작성일: 2026-06-26
> **상위 로드맵**: **Plan 62(AIOps 전체 역량 마스터 로드맵) — Phase P2(진단·RCA, 핵심)**. 선행자산: Plan 60 E4 토폴로지 그래프(P1). 대응 벤치마크 역량 C4(RCA 상관→인과).
> **관련 Plan**: Plan 44(alarm_query 의도), Plan 46(알람 소켓 수신), Plan 47(알람 이력 패턴 분석),
> Plan 47-1(영향 프로세스 보강), Plan 48/49(의도 분해 오케스트레이션)
> **관련 결정**: D-029(알람 의도 분리), D-030(해소 이력 포함), D-031(알람 파이프라인), D-032(알람 메시지 포맷),
> D-035(알람 이력 패턴 분석), D-036(영향 프로세스 보강), D-037(오케스트레이션) /
> 구현 착수 시 **D-038**로 등재 예정 (§14)
> **상태**: **부분 구현 — 재판정(2026-08-31 · `plans/85` §4 A-1)**. **조사 실행·증거 수집·LLM 인과·리포트·pull/push 트리거는 `sre_agent` 위임 방식으로 구현 완료** — `src/nodes/fault_diagnosis.py`(pull · D-124 CW-B) · `noise_gate/.../investigation_trigger.py`(push · D-124 CW-A) · `mcp_server` 조사 도구 8종 + PromQL 7종(D-122·D-119) · `sre_agent` `DiagnosisAgent`·`briefing_builder`(D-118·D-123). **`src/diagnosis/` 자체 서브그래프는 만들지 않는다**(D-118 위임 — 재편은 이미 실행됐다). **진짜 잔여 = 결정적 상관 축 5건**: §6.1 통합 타임라인 병합 · §6.2 metric_anomaly(z-score·지속성·선행성) · §6.4 CorrelationResult(leading_signal·notes) · §7.2 복수 가설 rank·confidence · §9.1 상대시각 타임라인. 공통점은 **LLM이 못 하는 결정적 계산**이라는 것이며(D-035 취지), 착수 범위는 **`sre_agent` 도구 출력을 입력으로 받는 순수 함수 계층**으로 좁혀진다. 절별 대조표는 `plans/85` §4 A-1 참조.

---

## 1. 개요 및 목표

### 1.1 배경

본 에이전트는 폴스타(Polestar) SMS와 연동하여 (1) 자연어→SQL로 인프라/성능 데이터를 조회하고,
(2) 알람 소켓을 수신하여 LLM으로 **단건 알람**을 분석·발송하는 기능을 이미 갖추고 있다.

현재 알람 분석(`alarm_analyzer`)은 단건 알람 + 동일 알람의 이력 통계(Plan 47) + 실시간 프로세스 스냅샷
(Plan 47-1)을 입력으로 `probable_cause`(추정 원인) / `recommended_action`(권고 조치)을 생성한다.
이는 **사실상 단일 신호 기반의 1차 진단**이다.

그러나 실제 운영의 "장애진단·원인분석"은 다음을 요구한다:

| 구분 | 현재 (단건 알람 분석) | 본 계획 (장애진단·원인분석) |
|------|----------------------|---------------------------|
| 입력 신호 | 알람 1건 + 동일알람 이력 + 현재 프로세스 | **사건 구간(window)의 다중 신호** — 알람 다발 + 성능 시계열 추이 + 프로세스 + 토폴로지(연관 리소스) |
| 분석 단위 | 알람 이벤트 1건 | **서버/리소스 × 시간 구간**의 사건(incident) |
| 시간 관점 | 발생 시점 단면 | **사건 전/중/후 타임라인** 복원 |
| 인과 관점 | 단일 추정 원인 1줄 | **근거 인용 + 신뢰도가 붙은 원인 가설 순위** |
| 연관 관점 | 단일 리소스 | **부모/자식/동일서버 리소스 간 연쇄(cascade)** 식별 |
| 호출 방식 | push(소켓 수신 시 자동) | push(자동) **+ pull(사용자가 "원인 분석해줘"로 요청)** |

### 1.2 목표

폴스타 모니터링 데이터(성능 시계열)와 이벤트 데이터(알람 이력)를 **사건 단위로 결합**하여,
LLM과 연동한 **장애진단(무엇이 문제인가)** 과 **원인분석(왜 발생했는가, 근거는 무엇인가)** 기능을 추가한다.

핵심 산출물: **구조화된 진단 리포트(DiagnosisReport)** — 타임라인 / 수집 증거 / 원인 가설 순위(근거·신뢰도) /
권고 조치. 자연어 응답 + (선택) Excel/Word 문서 + (push 시) 알림 채널로 제공.

### 1.3 설계 원칙 (기존 프로젝트 원칙 계승)

1. **읽기 전용 절대 불변** — 진단도 SELECT만 사용. 3중 읽기 전용 방어(D-003) 유지.
2. **수치·상관은 Python(결정적), 인과 해석만 LLM** — Plan 47 §3.3 원칙 계승. 발생 횟수·메트릭 이상·
   타임라인 정렬·연쇄 판정은 순수 함수로 결정적 계산하고, LLM에는 **계산된 증거 요약**만 주입하여
   환각·계산 오류를 차단한다.
3. **재구축이 아니라 조합(composition over rebuild)** — 기존 알람 이력 조회·메트릭 쿼리·프로세스 API·
   마스킹·알림·문서 생성·오케스트레이션 자산을 **재사용**하고, "사건 범위 설정 → 다중 증거 수집 →
   상관분석 → 인과 추론 → 리포트"의 얇은 조정 계층만 신설한다.
4. **graceful degradation** — 일부 증거(프로세스/메트릭/토폴로지) 수집 실패 시에도 가용 증거로
   진단을 진행한다. 폴스타 DB/API 의존이 진단 전체를 차단하지 않는다(Plan 47 §3.1 계승).
5. **근거 없는 단정 금지** — 원인 가설은 반드시 수집된 증거를 인용하고 신뢰도(확신도)를 함께 제시한다.
   증거가 부족하면 "추가 확인 필요"로 명시한다(환각 방지 — Known Mistakes 계승).
6. **폐쇄망 호환 / tool-calling 비의존** — 진단 서브그래프는 고정 파이프라인(LangGraph)으로 동작하며
   LLM tool-calling을 요구하지 않는다. 워커 LLM(FabriX/KBGenAIChat)로 동작 가능(D-037, Plan 49 계승).

### 1.4 성공 기준

1. 사용자가 자연어로 "X 서버 어제 14시쯤 장애 원인 분석해줘"라고 요청하면, **사건 구간**을 해석하여
   다중 신호(알람·메트릭·프로세스)를 수집하고 **원인 가설 순위 + 근거 + 권고**를 자연어로 반환한다.
2. 발생 횟수·메트릭 이상·타임라인·연쇄 판정 등 **수치는 Python이 결정적으로 계산**하고 LLM은 해석만 한다
   (LLM 응답에 계산값 환각 0건).
3. 고심각도(또는 첫 발생/급증) 알람 수신 시(push), 진단 서브그래프가 자동 실행되어 알림에
   **원인 가설·근거**가 첨부된다(기존 단건 분석 대체가 아니라 고도화, opt-in 플래그).
4. 진단 기능 비활성/실패 시 기존 알람 분석·데이터 조회 경로가 **무변경**으로 동작한다(회귀 없음).
5. `arch_check --ci` 통과(계층 위반 0), 신규 단위/통합 테스트 통과, 읽기 전용 검증 유지.
6. 증거 수집은 **고정/파라미터 SQL + 타임아웃 + 단기 캐시**로 폴스타 DB 부하를 보호한다(Plan 47 계승).

### 1.5 추가 수집 데이터 및 분석 방법 (명세)

> "무엇을 추가로 수집하고, 어떻게 분석하는가"의 한눈 요약. 상세는 §4(수집)·§6(상관분석)·§7(인과추론).

| # | 추가 수집 데이터 | 소스 · 수집 방법 | 분석 방법 (결정적 Python) | 산출 |
|---|-----------------|-----------------|--------------------------|------|
| 1 | **사건구간 알람 타임라인** (다중 알람, 해소 포함) | `cmm_alarm`+`cmm_alarm_def`+`cmm_resource` 사건구간 고정 SQL (§4.1) | 타임라인 병합·severity 추이·선후 판정 (§6.1) | 알람 발생 순서·다발 여부 |
| 2 | **성능 메트릭 추이** (CPU/메모리/FS/디스크IO, 전·중·후) | `cmm_metric_stat_h/d/m` 고정 SQL, 시간정밀도 분기 (§4.2) | baseline 대비 이상탐지(z-score·지속성)·**메트릭 선행성** (§6.2) | 이상 지표·선행 신호 |
| 3 | **실시간 프로세스 Top** | 폴스타 프로세스 API (§4.3) | top N 선별·마스킹 (한계: 현재 단면) | 자원 소비 주체 |
| 4 | **토폴로지** (부모/자식/동일서버) | `cmm_resource` 계층 고정 SQL (§4.4) | 서브리소스/연관 식별·연쇄(cascade) 판정 (§6.3) | 사건 시작점·연쇄 후보 |
| 5 | (Phase C) **변경/구성 이벤트** | 폴스타/ITSM 변경이력(가용성 선조사) (§4.5) | 타임라인 오버레이(변경기반 RCA) | 변경 용의 후보 |

**분석 절차**: ①~⑤를 `evidence_collector`가 동시 수집(부분실패 허용) → **`correlation_engine`(결정적)** 이
`CorrelationResult`(타임라인·메트릭 이상·선행 신호·연쇄·데이터 한계 notes) 산출 → **`causal_reasoner`(LLM)** 가
요약 텍스트만 받아 **원인 가설 순위 + 근거 인용 + 신뢰도 + 권고**를 생성(§7). 수치는 Python, 해석만 LLM.

---

## 2. 현재 자산 분석 (재사용 지도)

진단 기능은 아래 기존 자산 위에 조립한다. **신규 비즈니스 로직은 상관분석·인과추론·사건범위 설정뿐**이다.

### 2.1 알람/이벤트 파이프라인 (재사용)

| 자산 | 경로 | 진단에서의 재사용 |
|------|------|------------------|
| 알람 도메인 모델 | `src/alarm/domain/alarm.py` (`AlarmEvent`, `AlarmHistoryEntry`, `AlarmHistoryStats`, `ProcessSnapshot`, `AlarmAnalysisResult`) | 사건 트리거·증거 구성의 입력 타입으로 재사용 |
| 알람 이력 조회 | `src/alarm/infrastructure/polestar_history.py` (`PolestarAlarmHistoryRepository`, `build_history_sql`) | 사건 구간·다중 알람으로 **확장 조회**(§4.1). 조인 패턴(C-2/C-6, COALESCE PLATFORM_RESOURCE_ID), `_sql_literal` 이스케이프, 서버명 매칭 규칙 재사용 |
| 패턴 통계 (결정적) | `src/alarm/domain/alarm_pattern.py` (`compute_history_stats`) | 사건 내 알람 빈도/주기/급증 판정에 재사용 |
| 실시간 프로세스 | `src/alarm/infrastructure/polestar_process_api.py` (`PolestarProcessApiClient.list_by_hostname`) | CPU/메모리 사건의 프로세스 증거. **한계: 실시간 단면만 제공**(§4.3, R-2) |
| 프로세스 선별/마스킹 | `src/alarm/domain/process_rank.py` (`select_top_processes`, `mask_args`, `classify_alarm_kind`) | 그대로 재사용 (마스킹 보장) |
| 증거 동시 수집 | `src/alarm/application/nodes/alarm_context_enricher.py` (`enrich_history`, `enrich_processes`, `asyncio.gather` + `wait_for` 타임아웃) | **다중 증거 fan-out 수집기**의 설계 템플릿(§5.2) |
| LLM 단건 분석 | `src/alarm/application/nodes/alarm_analyzer.py` | 인과추론 노드의 프롬프트 구성·JSON 파싱 패턴 참고 |
| 알림 발송 | `src/alarm/application/nodes/alarm_notifier.py` (WorkB/webhook), `infrastructure/notification_bus.py` (SSE) | push 진단 결과 발송에 재사용 |
| 알람 서브그래프 | `src/alarm/orchestration/alarm_graph.py` (`build_alarm_graph`, `AlarmState`) | **진단 서브그래프(diagnosis_graph)의 설계 템플릿**(§5) |
| 워커/수신 | `alarm_server/` (TCP→Redis), `src/alarm/application/alarm_worker.py` | push 트리거 연결점(§8.2) |

### 2.2 성능 시계열 메트릭 (재사용 — 진단의 핵심 신규 신호)

폴스타 성능 통계 테이블 `cmm_metric_stat_[h,d,m]` (시/일/월 집계). 프로필 query_guide
(`config/db_profiles/polestar_cm_gp.yaml:210-257`, `polestar_cm_yd.yaml`)에 조회 구조가 정의되어 있다.

| 컬럼 | 의미 |
|------|------|
| `resource_id` | `cmm_resource.id`와 조인 |
| `definition_name` | 지표 종류 — `'Utilization'`(사용률), `'MaxIORate'`(디스크 IO) |
| `stat_date` | 시: `YYYYMMDDHH` / 일: `YYYYMMDD` / 월: `YYYYMM` (문자열) |
| `min_val`, `avg_val`, `max_val` | 기간 내 최소/평균/최대 |

조회 가능 지표(resource_type × definition_name):
- `server.Cpus` + `Utilization` → CPU 사용률(%)
- `server.Memory` + `Utilization` → 메모리 사용률(%)
- `server.FileSystems` + `Utilization` → 파일시스템 사용률(%)
- `server.Disks` + `MaxIORate` → 디스크 IO

조인 구조: `cmm_resource r`(서브리소스) → `cmm_resource svr`(`svr.id = r.platform_resource_id AND
svr.resource_type='server.Server'`) → `cmm_metric_stat_? s`(`r.id = s.resource_id`).

**진단 관점 의미**: 알람 발생 시점 전/중/후의 메트릭 추이를 조회하여 "알람보다 먼저 CPU가 상승했는가",
"이상이 지속/급등인가"를 결정적으로 판정할 수 있다. **시간 정밀도 분기**(§4.2, R-3):
최근 사건은 `_h`(시 단위), 과거 사건은 `_d`/`_m`만 존재.

### 2.3 토폴로지 (재사용)

`cmm_resource` 단일 테이블에 모든 리소스 계층 표현:
- 부모-자식: `parent_resource_id = id`
- 동일 서버 소속: `platform_resource_id` 동일
- `resource_type`로 종류 구분(`server.Server`, `server.Cpus`, `server.Memory`, `server.FileSystem`,
  `server.NetworkInterface` 등), `dtime IS NULL`로 삭제 제외

**진단 관점 의미**: 한 서버 내 어떤 서브리소스(CPU/디스크/NW)에서 사건이 시작됐는지, 또는 연관 서버로
번졌는지(연쇄)를 토폴로지로 좁힐 수 있다.

### 2.4 오케스트레이션 / 라우팅 (재사용 — pull 트리거 연결점)

| 자산 | 경로 | 재사용 |
|------|------|--------|
| 의도 라우팅 | `src/routing/semantic_router.py`, `src/routing/domain_config.py` | 신규 `fault_diagnosis` 의도 추가(§8.1) |
| SubAgent 레지스트리 | `src/orchestration/subagents.py` (`SubAgentSpec`, `SUBAGENT_REGISTRY`, `run_data_query_pipeline`) | 신규 `fault_diagnosis` subagent 등록 → Track A(의도분해)·Track B(deepagents tool) 양쪽에서 자동 노출 |
| 의도 분해 플래너 | `src/orchestration/intent_planner.py`, `src/prompts/intent_planner.py` | 진단 의도 인식 프롬프트 보강 |
| 결과 종합 | `src/orchestration/result_aggregator.py` | 멀티 의도 질의에서 진단 결과 통합 |
| 데이터 조회 파이프라인 | `run_data_query_pipeline` (NL→SQL 전체) | 진단 중 **임시/후속 ad-hoc 조회**가 필요할 때 호출 |

### 2.5 공통 인프라 (재사용)

- DB 접근: `src/routing/db_registry.py` (`DBRegistry.get_client(db_id)` → `execute_sql`, 읽기 전용)
- 출력: `src/nodes/output_generator.py` (자연어/Excel/Word)
- 마스킹: `src/security/data_masker.py`
- 설정: `src/config.py` (pydantic-settings)
- 캐시: Redis (단기 조회 캐시)

> **결론**: 진단 서브시스템은 `src/diagnosis/`로 신설하되, 데이터 접근·통계·알림·출력은 **대부분 재사용**한다.
> 신규 코드는 (a) 사건 범위 설정, (b) 다중 증거 수집 조정, (c) **결정적 상관분석 엔진**, (d) **인과추론
> 프롬프트**, (e) 리포트 조립, (f) 트리거 연결(의도/subagent/알람훅)에 집중된다.

---

## 3. 핵심 설계 결정

각 결정은 §14에서 D-038 하위 항목으로 등재한다. 작업 전 `docs/02_decision.md`의 기존 결정과의 충돌 검토
결과: **충돌 없음**(추가적·읽기전용·기존 패턴 재사용). 단, 아래 결정은 사용자 확인이 필요할 수 있어 §16에 명시.

### 3.1 진단은 "조합(composition)"으로 구현 — 별도 거대 엔진 신설 금지

기존 알람/메트릭/프로세스/토폴로지 자산을 증거원(evidence source)으로 묶고, 그 위에 상관·추론·리포트
계층만 얹는다. 새 데이터 저장소·새 수집 데몬을 만들지 않는다(Plan 47 §3.1 "자체 저장소 미신설" 계승).

### 3.2 증거 수집 SQL은 고정/파라미터 템플릿 (LLM 생성 아님)

진단 증거(알람 타임라인·메트릭 추이·토폴로지)는 **사전 정의된 파라미터 SQL**로 조회한다.

| 기준 | 고정 템플릿 SQL (채택) | NL→SQL 파이프라인 (비채택, 보조만) |
|------|----------------------|--------------------------------|
| 결정성 | 동일 입력 → 동일 쿼리 (감사·재현 용이) | 매 호출 LLM 생성 (편차·환각 위험) |
| 지연 | 검증 루프 불필요, 1회 SELECT | 생성→검증→(재시도) 루프 |
| 안전 | 읽기 전용 고정, 인젝션은 `_sql_literal` 이스케이프 | 검증 파이프라인 의존 |
| 부하 | 타임아웃+캐시 통제 용이 | 통제 복잡 |

→ **증거 수집은 고정 템플릿**(Plan 47 `build_history_sql` 방식 계승). 단, 사용자가 **추가 임의 조회**를
요청하거나 추론 중 보강이 필요하면 기존 `run_data_query_pipeline`(NL→SQL)을 **보조 경로**로 호출한다.

### 3.3 수치는 Python, 해석은 LLM (재확인)

상관분석 엔진(§6)은 순수 함수로 메트릭 이상·타임라인·연쇄를 계산한다. LLM은 그 **요약 텍스트**만 받아
인과 가설을 서술한다. (Plan 47 §3.3, Known Mistakes "output_generator avail_status 환각" 계승)

### 3.4 진단 단위 = (db_id, 대상 리소스, 사건 시간 구간)

- `db_id`: 폴스타 인스턴스 선택 (`DBRegistry.get_client`)
- 대상 리소스: 서버명(`server_name`/장비명 `r.name`) 또는 hostname → `cmm_resource` 식별
  (프로필별 매칭 컬럼 상이 — Plan 47 §5.3, Known Mistakes 2026-06-10 "공동존 서버명 매핑" 주의 계승)
- 사건 시간 구간: 알람 시각(push) 또는 사용자 지정 시각(pull)을 **기준 시각**으로 ±lookback 구간 설정.
  **현재 시각(now) 기준 금지** — 지연 처리에도 일관되게(Plan 47 시간창 규칙, Known Mistakes 계승)

### 3.5 트리거 이중화 — pull + push

- **pull**: 신규 의도 `fault_diagnosis` + subagent. 사용자가 명시적으로 요청할 때.
- **push**: 알람 파이프라인에서 고심각도/첫발생/급증 알람에 한해 진단 서브그래프 자동 실행(플래그 opt-in).
  기존 단건 `alarm_analyzer`를 **대체하지 않고**, 진단이 활성일 때 분석 결과를 고도화한다.

### 3.6 단계적 범위 — 단일서버 심층 RCA 먼저, 다중서버 연쇄는 후속

Phase A는 **단일 대상 리소스/서버**의 심층 진단(MVP). 다중 서버 연쇄(cascade) RCA는 토폴로지·시차
상관이 복잡하므로 Phase C로 분리한다(R-5).

---

## 4. 데이터 소스 및 증거 모델

진단은 4종 증거를 사건 구간으로 수집한다. 각 증거는 **독립 수집(부분 실패 허용)**.

### 4.1 알람 타임라인 증거 (이벤트)

기존 `PolestarAlarmHistoryRepository`를 **사건 구간·다중 알람**으로 확장한 조회.

- 현재 `build_history_sql`은 (server_name, **단일 alarm_name**, lookback_start)로 동일 알람 이력만 조회.
- 진단용 `build_incident_alarms_sql`(신규)은 (server_name, **window_start~window_end**, alarm_name 미지정)로
  **해당 서버의 사건 구간 내 모든 알람**을 시간순 조회. 선택적으로 연관 리소스(부모/자식/동일
  platform_resource) 알람 포함(Phase C).
- 조인 패턴(C-2/C-6, COALESCE PLATFORM_RESOURCE_ID), `ALARMSEVERITY IN (0,1,2,3)`(해소 포함, D-030),
  `CR.DTIME IS NULL`, `_sql_literal` 이스케이프, 서버명 매칭 규칙 모두 재사용.
- 산출: 시간순 알람 이벤트 리스트 → 결정적으로 "알람 순서/선후, severity 추이, 해소 여부, 다발 여부" 계산.

### 4.2 성능 메트릭 추이 증거 (모니터링)

신규 `PolestarMetricRepository`(고정 템플릿)로 사건 구간 전/중/후 메트릭을 조회.

- 대상: 대상 서버의 `server.Cpus`/`server.Memory`/`server.FileSystems`(Utilization), `server.Disks`(MaxIORate).
- **시간 정밀도 분기**(§2.2):
  - 사건이 최근(예: `_h` 보존 기간 내) → `cmm_metric_stat_h`로 **시간 단위 추이**(이상 탐지 정밀).
  - 과거 사건 → `cmm_metric_stat_d`(일) 또는 `_m`(월)만 가능 → 정밀도 한계 명시(R-3).
- 베이스라인: 사건 직전 N기간(예: 직전 7일 동시간대) 평균/표준편차를 함께 조회 → 이상 판정 기준.
- 산출: 지표별 (구간 전 baseline, 구간 중 min/avg/max, 구간 후) 시계열 → §6 이상 탐지 입력.

```
-- 예시(개념): 사건 시각 기준 시간단위 CPU/메모리 추이 (PostgreSQL/공동존 프로필 기준)
-- stat_date(YYYYMMDDHH)가 [window_start, window_end] 범위인 행을 시간순 조회
SELECT svr.name AS server_name, r.resource_type, s.definition_name,
       s.stat_date, s.min_val, s.avg_val, s.max_val
FROM polestar.cmm_resource r
JOIN polestar.cmm_resource svr
  ON svr.id = r.platform_resource_id AND svr.resource_type = 'server.Server'
JOIN polestar.cmm_metric_stat_h s ON r.id = s.resource_id
WHERE svr.name = :server_name
  AND r.resource_type IN ('server.Cpus','server.Memory','server.FileSystems','server.Disks')
  AND s.stat_date BETWEEN :window_start_hh AND :window_end_hh
  AND r.dtime IS NULL
ORDER BY s.stat_date;
-- :window_*_hh 는 기준 시각 ± lookback 에서 Python이 계산한 YYYYMMDDHH 리터럴(이스케이프 적용)
```

> 주의: 프로필별 서버 식별 컬럼(`svr.name` vs hostname)과 DB 엔진(DB2 `FETCH FIRST` vs PG `LIMIT`)
> 분기는 기존 프로필/엔진 분기 규칙을 따른다. 하드코딩 날짜 금지(프로필 규칙) — 기준 시각은 입력값에서 계산.

### 4.3 프로세스 증거 (실시간 단면)

기존 `PolestarProcessApiClient.list_by_hostname` + `select_top_processes`/`mask_args` 재사용
(CPU/메모리 사건 한정 게이팅도 재사용).

- **중대한 한계(R-2)**: 폴스타 프로세스 API는 **실시간 단면만** 제공(과거 시점 조회 불가).
  - push(알람 직후) 또는 "방금/현재" pull → 사건 시점에 근접 → 유효 증거.
  - 과거 사건 pull → 프로세스 증거는 "현재 상태"이며 사건 시점과 다를 수 있음 → 리포트에
    **"현재 시점 참고용"** 으로 명시하고 인과 단정에 사용하지 않는다(증거 신뢰도 차등).

### 4.4 토폴로지 증거 (구조)

신규 `PolestarTopologyRepository`(고정 템플릿)로 대상 리소스의 부모/자식/동일서버 리소스를 조회.

- Phase A: 대상 서버의 서브리소스(어느 CPU/디스크/NW에서 사건이 시작됐는지 좁히기).
- Phase C: 동일 platform_resource/연관 서버로 확장(연쇄 후보군).

### 4.5 (후속) 변경/구성 이벤트 증거

배포·구성 변경·패치 이벤트가 폴스타/연관 DB에 있다면 "변경 직후 장애" 인과에 강력하나, 현재 스키마에서
표준 위치가 불확실 → **Phase C 조사 항목**으로 분리(현 계획 범위 외, 데이터 가용성 선확인 필요).

---

## 5. 아키텍처 — 진단 서브그래프 (diagnosis_graph)

알람 서브그래프(`build_alarm_graph`)와 동형의 LangGraph 서브그래프를 신설한다.
tool-calling 비의존 고정 파이프라인 → 워커 LLM(FabriX)로 동작(폐쇄망 호환).

```
              ┌──────────────────────────────────────────────────────────┐
 (pull/push)  │ DiagnosisState                                            │
   trigger ─► │  incident_scoper                                          │
              │     │ 대상 리소스·기준시각·구간·관심신호 확정              │
              │     ▼                                                      │
              │  evidence_collector  ── asyncio.gather(타임아웃) ──┐       │
              │     ├─ 알람 타임라인 (§4.1, PolestarAlarmHistory*)  │       │
              │     ├─ 메트릭 추이   (§4.2, PolestarMetricRepo)     │       │
              │     ├─ 프로세스 단면 (§4.3, PolestarProcessApi)     │       │
              │     └─ 토폴로지      (§4.4, PolestarTopologyRepo)   │       │
              │     ▼ EvidenceBundle (부분 실패 허용)               ◄┘      │
              │  correlation_engine (결정적 Python, §6)                    │
              │     │ 타임라인 병합·메트릭 이상·선후/연쇄 판정         │
              │     ▼ CorrelationResult                                    │
              │  causal_reasoner (LLM, §7)                                 │
              │     │ 원인 가설 순위 + 근거 인용 + 신뢰도 + 권고        │
              │     ▼ DiagnosisReport                                      │
              │  diagnosis_reporter                                        │
              │     │ 자연어/문서 + (push 시) 알림                       │
              └─────┴────────────────────────────────────────────────────┘
```

### 5.1 DiagnosisState (TypedDict)

```python
class DiagnosisState(TypedDict, total=False):
    # 입력/범위
    db_id: str
    target: dict                 # {server_name, hostname, resource_id?, resource_type?}
    reference_time: str          # 기준 시각(ISO) — now() 금지(§3.4)
    window: dict                 # {start, end, lookback_minutes, granularity: h|d|m}
    signals_of_interest: list[str]  # ["cpu","memory","filesystem","disk","alarm"]
    trigger: str                 # "pull" | "push"
    source_alarm: Optional[dict] # push 트리거 알람(AlarmEvent dict)

    # 증거
    evidence: dict               # EvidenceBundle (알람/메트릭/프로세스/토폴로지)

    # 상관분석(결정적)
    correlation: dict            # CorrelationResult (타임라인·이상·선후·연쇄)

    # 결과
    report: dict                 # DiagnosisReport (가설 순위·근거·권고)
    error: Optional[str]
```

> AgentState(메인 그래프)와는 분리된 서브그래프 상태(알람 서브그래프 `AlarmState`와 동일 정책).
> pull 경로에서 subagent handler가 결과를 메인 `organized_data`/`final_response`로 변환(§8.1).

### 5.2 노드별 책임

| 노드 | 계층 | 책임 | 재사용/신규 |
|------|------|------|-----------|
| `incident_scoper` | application | 대상 리소스·기준시각·구간·정밀도(h/d/m)·관심신호 확정. pull은 NL 파싱(LLM 소폭) / push는 알람에서 직접 도출 | 신규(얇음). 서버명 매칭은 프로필 규칙 재사용 |
| `evidence_collector` | application | 4종 증거 동시 수집(`asyncio.gather` + `wait_for` 타임아웃, 부분실패 허용) | `alarm_context_enricher` 패턴 재사용 |
| `correlation_engine` | application→domain | EvidenceBundle → 타임라인 병합·메트릭 이상탐지·선후/연쇄 판정(순수 함수 호출) | 신규(domain 순수함수 §6) |
| `causal_reasoner` | application | CorrelationResult 요약 텍스트 → LLM 인과 가설 순위·근거·권고 | `alarm_analyzer` 프롬프트/파싱 패턴 재사용 |
| `diagnosis_reporter` | application | DiagnosisReport 조립 → 자연어/문서/알림 | `output_generator`·`alarm_notifier` 재사용 |

### 5.3 Clean Architecture 배치 (계층 규칙 준수)

기존 `src/alarm/` 구조를 미러링하여 `src/diagnosis/` 신설:

```
src/diagnosis/
├── domain/
│   ├── models.py            # IncidentScope, EvidenceBundle, MetricSeries,
│   │                        #   CorrelationResult, RootCauseHypothesis, DiagnosisReport
│   ├── correlation.py       # 결정적: 타임라인 병합·선후 판정·연쇄 판정 (순수 함수)
│   └── metric_anomaly.py    # 결정적: baseline 대비 이상탐지 (z-score/임계) (순수 함수)
├── infrastructure/
│   ├── polestar_metric.py   # PolestarMetricRepository (cmm_metric_stat_[h,d,m] 고정 SQL)
│   ├── polestar_topology.py # PolestarTopologyRepository (cmm_resource 계층 고정 SQL)
│   └── polestar_incident_alarms.py  # build_incident_alarms_sql (이력 repo 확장)
├── application/
│   └── nodes/
│       ├── incident_scoper.py
│       ├── evidence_collector.py
│       ├── correlation_engine.py
│       ├── causal_reasoner.py
│       └── diagnosis_reporter.py
├── orchestration/
│   └── diagnosis_graph.py   # build_diagnosis_graph, DiagnosisState
└── prompts/
    ├── incident_scoper.py   # (pull) NL→사건범위 파싱 프롬프트
    └── causal_reasoner.py    # 인과 추론 시스템/유저 프롬프트
```

의존 방향: `domain → infrastructure → application → orchestration` (정방향). `arch_check.py`로 검증.
프로세스 증거는 `src/alarm/infrastructure`·`src/alarm/domain`을 재사용(동일 application 계층 이하 의존 OK).

---

## 6. 결정적 상관분석 엔진 (correlation_engine)

`src/diagnosis/domain/correlation.py` + `metric_anomaly.py` — **순수 함수, DB/LLM 비의존**(테스트·감사 용이).

### 6.1 통합 타임라인 병합

- 입력: 알람 이벤트(시각·severity·alarm_name·resource), 메트릭 이상점(시각·지표·값).
- 처리: 모든 증거를 **기준 시각** 좌표계로 정렬(`reference_time` 기준 상대 분). now() 미사용.
- 산출: `[{t_offset_min, kind, detail}]` 시간순 — "무엇이 먼저 일어났는가"의 결정적 근거.

### 6.2 메트릭 이상 탐지 (`metric_anomaly.py`)

- baseline: 사건 직전 동시간대 N기간의 `avg_val` 평균(μ)·표준편차(σ).
- 이상 판정(택1, 보수적 결합):
  - z-score: `(window_max - μ) / σ ≥ z_threshold`(기본 3.0) → "급등".
  - 절대 임계: 알람 임계(가능 시) 또는 사용률 ≥ 고정선(예: CPU 90%, FS 90%) 초과.
  - 지속성: 연속 K구간 이상 → "지속적", 1구간 → "스파이크".
- 선행성: 메트릭 이상 시각이 알람 발생보다 앞서면 "메트릭 선행"(인과 후보 강화 신호).
- 산출: 지표별 `{is_anomalous, kind(spike|sustained), peak_value, peak_time, lead_lag_vs_alarm}`.

### 6.3 선후/연쇄 판정

- 단일 서버(Phase A): 어느 서브리소스 이상이 먼저였는지(예: 디스크 IO 급등 → CPU iowait 상승 →
  CPU 알람) 시차로 순서화. **상관 ≠ 인과**임을 명시(가설 신뢰도에 반영).
- 다중 서버(Phase C): 토폴로지상 연관 서버 간 알람 시차로 연쇄 후보 산출.

### 6.4 출력: CorrelationResult

```python
@dataclass
class CorrelationResult:
    timeline: list[dict]              # 정렬된 사건 타임라인
    metric_findings: dict             # 지표별 이상 판정
    alarm_summary: dict               # 사건 구간 알람 빈도/severity 추이/해소 여부
    leading_signal: Optional[str]     # 가장 먼저 이상을 보인 신호(인과 후보)
    cascade: list[dict]               # (Phase C) 연쇄 후보
    notes: list[str]                  # 데이터 한계(정밀도 부족/프로세스 단면 등) 결정적 기록
```

`notes`에 "메트릭 월단위만 존재 → 시간 정밀도 없음", "프로세스는 현재 단면" 등 한계를 **결정적으로** 기록 →
LLM이 이를 그대로 신뢰도에 반영(환각 방지).

---

## 7. LLM 인과 추론 (causal_reasoner)

`alarm_analyzer`의 프롬프트/파싱 패턴을 계승. **계산된 증거 요약 텍스트만** 입력.

### 7.1 입력 (LLM에 주입)

- 사건 범위(대상·기준시각·구간·정밀도)
- 알람 요약(빈도·severity 추이·주요 알람명, Plan 47 통계 렌더 재사용)
- 메트릭 findings 요약(지표별 이상/선행 여부·피크)
- 프로세스 단면(있으면, 마스킹된 top N — "현재 시점" 라벨)
- 토폴로지 요약(서브리소스/연관)
- 상관 notes(데이터 한계)

### 7.2 출력 스키마 (JSON)

```json
{
  "incident_summary": "사건 한줄 요약",
  "root_cause_hypotheses": [
    {
      "rank": 1,
      "cause": "추정 원인",
      "confidence": "high|medium|low",
      "evidence": ["인용한 증거(타임라인/메트릭/프로세스 항목)"],
      "reasoning": "왜 이 근거가 이 원인을 가리키는가"
    }
  ],
  "recommended_actions": ["권고 조치(우선순위순)"],
  "further_investigation": ["증거 부족으로 추가 확인이 필요한 항목"],
  "data_limitations": ["정밀도/단면 등 한계"]
}
```

### 7.3 프롬프트 규칙 (환각 차단 — Known Mistakes 계승)

- 주입된 수치/시각만 사용. **새 수치·시각을 생성·추정 금지**.
- 모든 가설은 `evidence`에 실제 주입된 증거 항목을 인용. 인용 불가하면 `further_investigation`으로.
- `data_limitations`(상관 notes)를 반드시 신뢰도에 반영(예: 월단위 메트릭만 → confidence ≤ medium).
- 프로세스 단면은 "현재 시점" — 과거 사건이면 인과 단정 금지(참고로만).
- 상관 ≠ 인과 — 선행 신호도 "유력 후보"로 서술(단정 금지).
- 폴스타 도메인 지식 주입(재사용): severity(3=심각,2=경고,1=주의,0=해소), avail_status(0=정상, ≠0=비정상).

---

## 8. 트리거 통합

### 8.1 pull — 의도 + subagent (메인 그래프 / 오케스트레이션)

1. **의도 추가** (`src/routing/semantic_router.py`, `domain_config.py`): `fault_diagnosis` 의도.
   - 트리거 표현: "원인 분석", "장애 진단", "왜 ~ 알람", "무슨 일이 있었는지", "장애 분석".
   - `data_query`/`alarm_query`(단순 조회)와 구분: 진단은 **사건 구간 + 인과**를 요구.
2. **subagent 등록** (`src/orchestration/subagents.py`):
   ```python
   "fault_diagnosis": SubAgentSpec(
       "fault_diagnosis", "장애 진단·원인 분석(사건 구간 다중신호 상관·인과)", run_fault_diagnosis
   ),
   ```
   - `run_fault_diagnosis(task, isolated, *, llm, app_config)`:
     - `task["sub_query"]`에서 incident_scoper 입력 도출 → `build_diagnosis_graph(...).ainvoke(...)`
     - DiagnosisReport → `{organized_data, query_results, source}` 형태로 변환(메인 응답 계약 준수).
   - 등록만으로 **Track A(의도분해)** 위임 대상이 되고, **Track B(deepagents)** 에서는
     `deepagents_tools.build_tools`가 레지스트리를 `@tool`로 노출하므로 자동 편입(Plan 49 §4.2).
3. **플래너 프롬프트 보강** (`src/prompts/intent_planner.py`): 복합 질의에서 진단 task 분해 인식
   (예: "A 서버 사양 알려주고, 어제 장애 원인도 분석해줘" → data_query + fault_diagnosis 2 task).
4. **(폴백) semantic_router 단일 경로**: 오케스트레이션 미활성 시 `fault_diagnosis` 의도를 진단
   서브그래프로 직접 라우팅(`general_inference`처럼 고정 노드 연결).

### 8.2 push — 알람 파이프라인 훅 (opt-in)

1. `src/alarm/orchestration/alarm_graph.py`: `enable_diagnosis_on_alarm=true`이고 알람이
   **고심각도(severity≥경고) 또는 첫발생/급증(`AlarmHistoryStats.pre_classification`)** 이면,
   `alarm_analyzer` 후 진단 서브그래프를 호출(또는 enricher 결과를 DiagnosisState로 넘겨 재사용).
2. 진단 결과(원인 가설·근거)를 `AlarmAnalysisResult`에 병합 → 기존 `alarm_notifier`가 알림에 첨부.
3. **기존 단건 분석 대체 아님** — 플래그 off거나 저심각도면 기존 경로 무변경(회귀 없음, 성공기준 4).
4. 부하 보호: push 진단은 enricher가 이미 수집한 알람이력·프로세스를 **재사용**하고 메트릭/토폴로지만
   추가 수집(중복 조회 최소화).

### 8.3 API 엔드포인트 (선택)

`src/api/routes/diagnosis.py`(신규, `alarm.py` 테스트 엔드포인트 패턴 계승):
- `POST /diagnosis/analyze` — {db_id, target, reference_time, lookback} → DiagnosisReport(dry-run 지원).
- (재사용) 진단 결과 SSE는 기존 `alarm_bus`/notification stream 재활용 가능.

---

## 9. 출력 (DiagnosisReport)

### 9.1 구조화 리포트

```
[장애 진단 리포트]
대상: <서버/리소스>   사건 시각: <기준시각>   구간: <start~end> (정밀도: 시/일/월)

■ 사건 요약: <한줄>

■ 타임라인:
  T-15m  메트릭  디스크 IO 급등 (MaxIORate baseline 대비 +320%)
  T-12m  메트릭  CPU iowait 상승 (avg 35%→88%)
  T-10m  알람    [심각] CPU Utilization Critical
  T-2m   알람    [해소] CPU Utilization Clear

■ 원인 가설(신뢰도):
  1) (high)  디스크 IO 폭주로 인한 CPU iowait 상승 — 근거: 디스크 IO가 CPU 알람 12분 선행
  2) (medium) 배치 작업 동시 실행 — 근거: 동시간대 주기적 패턴 이력

■ 권고 조치: ...
■ 추가 확인 필요: ...
■ 데이터 한계: 프로세스는 현재 시점 단면(사건 시점 아님)
```

### 9.2 채널

- 자연어 응답(기본) — pull/push 공통.
- (선택) Excel/Word — 기존 `output_generator` 재사용(양식 업로드 시).
- (push) WorkB/webhook/SSE — 기존 `alarm_notifier`/`notification_bus` 재사용.

### 9.3 (Phase C) 진단 이력/피드백

진단 결과·운영자 피드백("실제 원인은 X였다")을 저장하여 추후 재현/학습 입력으로 활용(범위 외, 후속).

---

## 10. 상태 / 설정 / API 변경

### 10.1 설정 (`src/config.py`) — 신규 `DiagnosisConfig`

```python
class DiagnosisConfig(BaseSettings):           # env_prefix="DIAGNOSIS_"
    enabled: bool = False                       # 진단 기능 전체 게이트
    enable_on_alarm: bool = False               # push(알람 자동 진단) opt-in
    on_alarm_min_severity: int = 2              # 자동 진단 최소 심각도(경고 이상)
    default_lookback_minutes: int = 120         # 기본 사건 구간(기준시각 ±)
    metric_baseline_days: int = 7               # 이상탐지 baseline 기간
    anomaly_z_threshold: float = 3.0            # z-score 임계
    collect_timeout_seconds: float = 8.0        # 증거 수집 전체 타임아웃
    evidence_cache_ttl_seconds: int = 300       # 단기 조회 캐시 TTL
    metric_max_rows: int = 5000                 # 메트릭 조회 상한(10,000 이내)
    include_topology_neighbors: bool = False     # Phase C 연쇄 토폴로지 확장
```

- `.env.example`에 `DIAGNOSIS_*` 추가. 기본 비활성(opt-in) → 회귀 없음.

### 10.2 상태

- 진단 서브그래프는 자체 `DiagnosisState`(§5.1) 사용 — 메인 `AgentState`에 대량 필드 추가 불필요.
- pull subagent 결과는 기존 `organized_data`/`final_response` 계약으로 변환(추가 필드 최소).

### 10.3 API

- (선택) `POST /diagnosis/analyze` 추가(§8.3). 인증/마스킹/감사 로그는 기존 미들웨어 재사용.

---

## 11. 단계별 구현 계획

각 단계는 verify 기준을 동반(목표 기반 실행 — CLAUDE.md §4).

### Phase A — pull 단일서버 심층 진단 (MVP)

1. domain 모델·순수함수: `models.py`, `correlation.py`, `metric_anomaly.py`
   → verify: 단위 테스트(타임라인 병합·이상탐지·선후 판정, now() 미사용 검증), `arch_check` 통과.
2. infrastructure repo: `polestar_metric.py`, `polestar_topology.py`, `polestar_incident_alarms.py`
   → verify: 고정 SQL 생성·`_sql_literal` 이스케이프·읽기전용·프로필별 서버명 컬럼 분기 단위 테스트.
3. application 노드 5종 + `diagnosis_graph.py`
   → verify: 증거 일부 실패 시 graceful 진행, 타임아웃 동작, 모의 데이터 E2E(LLM 모킹).
4. causal_reasoner 프롬프트 + 파싱
   → verify: 주입 외 수치 환각 없음(증거 인용 강제), data_limitations 반영, JSON 파싱 견고성.
5. pull 트리거: 의도(`fault_diagnosis`) + subagent 등록 + 플래너 프롬프트 보강
   → verify: "원인 분석" 질의가 진단 경로로 라우팅, 단순 조회는 기존 경로 유지(분류 회귀).
6. 출력: 자연어 리포트
   → verify: 타임라인·가설·근거·권고·한계 포함, 마스킹 적용.

### Phase B — push 알람 자동 진단 (opt-in)

7. `alarm_graph` 훅 + `enable_on_alarm` 게이트 + enricher 증거 재사용
   → verify: 고심각도/첫발생/급증만 진단, 저심각도/off는 기존 단건 분석 무변경(회귀), 알림 첨부.

### Phase C — 다중서버 연쇄 RCA + 문서/이력 (후속)

8. 토폴로지 연쇄 상관(`include_topology_neighbors`) + 연관 서버 알람/메트릭 확장.
9. Excel/Word 리포트, 진단 이력/피드백 저장, 변경/구성 이벤트 증거(데이터 가용성 선조사).

---

## 12. 테스트 계획

| 테스트 | 검증 내용 |
|--------|----------|
| `test_correlation_timeline` | 타임라인 병합·정렬, 기준시각 좌표계, now() 미사용 |
| `test_metric_anomaly` | z-score/임계/지속성 판정, baseline 계산, 선후(lead/lag) |
| `test_cascade_single_server` | 단일서버 서브리소스 선후 순서화(Phase A) |
| `test_metric_repo_sql` | 고정 SQL 생성·이스케이프·정밀도 분기(h/d/m)·프로필 서버명 컬럼 |
| `test_incident_alarms_sql` | 사건구간 다중알람 조회 SQL(해소 포함 D-030, COALESCE 조인) |
| `test_evidence_collector_partial_fail` | 증거 일부 실패/타임아웃 시 graceful 진행 |
| `test_causal_reasoner_no_hallucination` | 주입 외 수치 미생성, 증거 인용 강제, 한계 반영 |
| `test_diagnosis_graph_e2e` | (LLM 모킹) scoper→…→reporter 전체 흐름 |
| `test_pull_intent_routing` | `fault_diagnosis` 의도 분류·subagent 위임, 단순조회 회귀 |
| `test_push_gate` | `enable_on_alarm` on/off·심각도 게이트, off 시 알람경로 무변경 |
| `test_readonly_guard` | 진단 전 경로 SELECT only 유지 |
| `test_arch_check` | `src/diagnosis` 계층 위반 0 |

> 폴스타 DB/프로세스 API 실연동 테스트는 통합 마커로 분리(CI 스킵, 자원 가용 시 실행) — Plan 49 정책 계승.

---

## 13. 리스크 및 대응

| # | 리스크 | 심각도 | 대응 |
|---|--------|--------|------|
| R-1 | LLM 인과 환각(근거 없는 단정) | High | 수치는 Python 결정(§6), 증거 인용 강제·신뢰도·한계 반영(§7.3), no-hallucination 테스트 |
| R-2 | 프로세스 API 실시간 단면뿐 → 과거 사건 부정합 | High | "현재 시점" 라벨, 과거 사건 인과 단정 금지(참고만), push/최근 pull에서만 강증거(§4.3) |
| R-3 | 메트릭 시간 정밀도(과거는 일/월만) | Med | 정밀도 분기(h/d/m), 한계를 correlation notes·리포트에 명시, confidence 상한 |
| R-4 | 폴스타 DB 부하(메트릭 대량 조회) | Med | 고정 SQL + 행수 상한(`metric_max_rows`) + 타임아웃 + 단기 캐시(TTL) — Plan 47 계승 |
| R-5 | 다중서버 연쇄 상관 복잡·오탐 | Med | Phase C로 분리, 단일서버 MVP 우선, 상관≠인과 명시 |
| R-6 | 프로필별 서버 식별 컬럼 상이(공동존 r.name) | Med | 기존 프로필 규칙·Plan 47 §5.3 재사용, 프로필별 분기 테스트(Known Mistakes 2026-06-10) |
| R-7 | 진단 도입이 기존 경로 회귀 유발 | Med | 전 기능 opt-in 플래그(`DIAGNOSIS_*` 기본 off), 별도 서브그래프/서브패키지, 회귀 테스트 |
| R-8 | 의도 분류 혼동(단순 조회 vs 진단) | Low | 의도 설명·few-shot로 "사건 구간+인과"만 진단, 모호 시 조회로 보수적 폴백 |
| R-9 | DB 엔진 분기(DB2/PG) 문법 차이 | Low | 기존 엔진 분기 규칙 재사용(LIMIT/FETCH FIRST, 날짜 함수), 프로필 기반 |
| R-10 | 기준 시각/타임존 오류 | Low | now() 금지·입력 기준시각 사용(§3.4), 타임라인 좌표계 단위 테스트 |

---

## 14. 의사결정 영향 (`docs/02_decision.md`)

작업 착수 시 **D-038. 장애진단·원인분석 서브시스템 도입**을 신규 등재한다(번호 체계 D-NNN 준수).
핵심 결정 사항(§3):

- **D-038.1** 진단은 기존 자산 조합으로 구현(별도 저장소·데몬 미신설). 근거: Plan 47 §3.1 계승, 정합성·비용.
- **D-038.2** 증거 수집은 고정/파라미터 SQL(LLM 생성 아님). 근거: 결정성·지연·안전(§3.2).
- **D-038.3** 수치는 Python 결정, 인과 해석만 LLM. 근거: 환각 차단(§3.3, Plan 47 §3.3).
- **D-038.4** 트리거 이중화(pull 의도/subagent + push 알람훅 opt-in), 기존 경로 무변경.
- **D-038.5** 단일서버 심층 RCA 우선, 다중서버 연쇄는 Phase C.

기존 결정과의 정합:
- D-003(읽기 전용) 유지 — 진단도 SELECT만.
- D-029/D-030/D-031/D-032/D-035/D-036(알람 계열) 재사용·확장 — 충돌 없음.
- D-037(오케스트레이션) — subagent 등록으로 Track A/B에 자연 편입, tool-calling 비의존(폐쇄망 호환).

> **사용자 확인 필요 항목**(§16)에 대한 결정이 내려지면 D-038 본문에 반영한다.

---

## 15. 변경 범위 요약

### 15.0 기존 코드 통합 분석 (백엔드 배선 — deepagents/Track A/semantic_router)

> 실측(2026-06-26, `src/graph.py`·`orchestration/*`·`routing/*` 코드 분석) 기반 통합 지점.

**핵심 판단 — "deepagents로 의도 분석하고 LangGraph를 새로 구성해야 하는가?"**

1. **의도 분석/위임은 신규 구현 불필요.** 시스템은 이미 의도 처리를 3중 백엔드로 수행한다
   (`src/graph.py:294-476` 빌드 시 1회 백엔드 확정):
   - **Track B(deepagents)**: `enable_deepagents_package` + vLLM 가용 → `deep_agent` 노드. vLLM 오케스트레이터가
     tool-calling으로 위임/재계획(Plan 49).
   - **Track A(의도분해)**: `enable_deepagent_orchestration` → `intent_planner → agent_orchestrator ↔ replanner`.
   - **semantic_router(폴백)**: 그 외. LLM intent 분류 → 고정 노드 라우팅.
2. **진단은 "subagent 1개"로 추가하면 Track A·B에 자동 편입된다.** 두 트랙 모두 **`SUBAGENT_REGISTRY`로
   디스패치**한다(Track A: `agent_orchestrator._run_agent` `subagents.py` 레지스트리 lookup; Track B:
   `deepagents_tools.build_tools`가 `for ... in SUBAGENT_REGISTRY.items()`로 **자동 순회·@tool 노출**).
   → **레지스트리에 `fault_diagnosis` 핸들러 1개 등록이면 두 트랙은 코드 추가 없이 동작.**
3. **진단 서브그래프 자체는 deepagents/tool-calling을 쓰지 않는다.** `diagnosis_graph`는 **고정 LangGraph
   파이프라인**으로 subagent handler 내부(FabriX 워커)에서 실행된다. deepagents는 "바깥 의도분석·위임"만
   담당하고, 진단 내부는 결정적 서브그래프(Plan 49 원칙: 도구 내부는 tool-calling 미강요).
4. **새로 만드는 LangGraph는 `diagnosis_graph` 하나뿐**(alarm_graph와 동형). 메인 `src/graph.py` 본체는
   **semantic_router 폴백 경로에만** `fault_diagnosis` 노드를 추가하면 된다(Track A/B는 handler가 서브그래프 호출).

**백엔드별 추가 작업 매트릭스**

| 백엔드 | 디스패치(자동) | 추가로 손볼 곳 |
|--------|---------------|---------------|
| Track A | `agent_orchestrator`→`SUBAGENT_REGISTRY`(자동) | `src/prompts/intent_planner.py` — agent 목록(L22-26)·분류 우선순위(L32-35)·예시에 `fault_diagnosis` 추가 |
| Track B | `deepagents_tools.build_tools`→레지스트리 순회(자동) | `deepagents_tools.py:30` `_TOOL_NAMES`에 도구명 + `src/prompts/orchestrator.py:17-22` "사용 가능한 도구" 목록 추가 |
| semantic_router | 수동 라우팅 | `src/prompts/semantic_router.py`(intent 추가) + `routing/semantic_router.py`(intent 반환 분기) + `src/graph.py:117 _INTENT_ROUTE_MAP`·`:480 조건부엣지 맵`·`fault_diagnosis` 노드 등록·import |

**공통**: `src/orchestration/subagents.py`에 `run_fault_diagnosis(task, isolated, *, llm, app_config)` +
`SUBAGENT_REGISTRY["fault_diagnosis"]` 등록(핸들러 시그니처·`_make_isolated_input` 재사용 — Known Mistakes 준수).
push 경로는 §8.2(alarm_graph 훅). 모든 변경 후 `arch_check --ci` 통과 확인.

### 신규 파일
- `src/diagnosis/domain/models.py`, `correlation.py`, `metric_anomaly.py`
- `src/diagnosis/infrastructure/polestar_metric.py`, `polestar_topology.py`, `polestar_incident_alarms.py`
- `src/diagnosis/application/nodes/{incident_scoper,evidence_collector,correlation_engine,causal_reasoner,diagnosis_reporter}.py`
- `src/diagnosis/orchestration/diagnosis_graph.py`
- `src/diagnosis/prompts/{incident_scoper,causal_reasoner}.py`
- `src/api/routes/diagnosis.py` (선택)
- `tests/test_diagnosis/...`

### 수정 파일 (최소·게이트)
- `src/config.py` — `DiagnosisConfig` 추가
- `src/routing/semantic_router.py`, `src/routing/domain_config.py` — `fault_diagnosis` 의도
- `src/orchestration/subagents.py` — `run_fault_diagnosis` + `SUBAGENT_REGISTRY` 항목
- `src/prompts/intent_planner.py` — 진단 의도 분해 인식
- `src/alarm/orchestration/alarm_graph.py` — push 진단 훅(opt-in)
- `.env.example` — `DIAGNOSIS_*`
- `docs/02_decision.md` — D-038 등재
- (선택) `src/api/server.py` — diagnosis 라우터 등록

### 변경하지 않는 파일 (재사용·보존)
- 알람 이력 repo·패턴 통계·프로세스 API/선별·마스킹·알림 버스·기존 노드/그래프·NL→SQL 파이프라인·
  result_aggregator·output_generator (그대로 호출만).

---

## 16. 사용자 확인 필요 항목 (착수 전 결정)

계획 자체는 추가적·읽기전용이라 기존 결정과 충돌하지 않으나, 다음은 범위/우선순위에 영향이 커 확인을 권장한다.

1. **우선 트리거**: pull(사용자 요청형) 먼저 vs push(알람 자동) 먼저? — 본 계획은 **pull(Phase A) 우선**을 권장
   (검증 용이·회귀 위험 최소). push는 Phase B.
2. **증거 SQL 방식**: 고정 템플릿(권장) 확정 여부 — NL→SQL 보조 경로 허용 범위.
3. **다중서버 연쇄 RCA 필요 시점**: Phase C로 분리(권장) vs 초기 포함.
4. **변경/구성 이벤트 증거**: 폴스타/연관 DB에 배포·구성변경 이력 테이블이 있는지(있다면 인과력 큼) —
   데이터 가용성 선조사 필요.
5. **메트릭 시간 정밀도(`_h`) 보존 기간**: 폴스타 환경에서 시간단위 통계 보존 기간 확인(이상탐지 정밀도 직결).

---

## 17. 참고

- 상위/연관 계획: Plan 44, 46, 47, 47-1, 48, 49 (`plans/`)
- 의사결정: `docs/02_decision.md` D-029~D-037 (→ 본 계획으로 D-038 등재)
- 데이터 모델: `config/db_profiles/polestar_cm_gp.yaml`(메트릭/EAV/토폴로지 query_guide), `polestar_cm_yd.yaml`
- 알람 자산: `src/alarm/`, `alarm_server/`
- 처리 흐름: `docs/07_processing_flow.md`, 아키텍처: `docs/05_system_architecture.md`
- Clean Architecture 검사: `python scripts/arch_check.py --ci`
