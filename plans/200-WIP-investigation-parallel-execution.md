# 200. 장애 조사·진단 실행 병렬화 — 플레이북 의존 기반 스케줄링 · 스텝 안 축 동시 조회 · HolmesGPT 턴당 다중 도구 호출

> **작성일**: 2026-10-30
> **상태**: **WIP** — W0~W5 구현 완료(2026-10-30 · 커밋 없음 · 플래그 4종 기본 off) · 계획 끝 모듈 단위 회귀 통과(`범위: 모듈 단위 — 전체 미실행` · 본체 1,013 · sre_agent 2,950 통과 · 실패 0) · verifier Critical 0 · Major 1(DAG 단계 끝 자기 판독 소실) 교정 · D-353 등재 · **잔여**: 내부망 측정(§4 기준 5 · `max_tools_per_turn`·`collect_s` off/on) · W4 MLX 대표 2건 확인(이 체크아웃 두 평면이 mlx 아님 → 내부망 이월) · 아래 「구현 부기」 잔여
> **v1.1 사용자 결정(2026-10-30)**: *"SRE_PLAYBOOK_ENABLED 은 true이고 나머지는 권고에 맞게 계획수립하라. 팀리더는 계획을 분석하여 구현하라."* — 폐쇄망 `SRE_PLAYBOOK_ENABLED=true`(A·B 효과 유효) · **Q1** D-341 실행기 조항 개정 승인 → D-353 등재 · 플래그 기본 off · **Q2** 「앞 단계 결과」 줄은 조상 스텝 결과로 한정(결정적) · **Q3** `SRE_PLAYBOOK_MAX_WORKERS` 기본 4 유지 · **Q4** X-View 조각 동시화(I-6)는 W0 내부망 측정 뒤 판단 — 이 계획에서는 착수하지 않음
> **분석 기준 HEAD**: `7cf31eb6` (계획 시작 SHA는 W0 착수 때 이 줄 아래에 적는다)
> **계획 번호**: 당초 199 → **200으로 재번호**(2026-10-30 · 병행 세션 collectorinfra-da가 `plans/199-investigation-result-summary-view.md`를 origin/main에 선푸시). 테스트 `test_plan200_*` · 픽스처 `tests/fixtures/plan200/`.
> **계획 시작 SHA**: `7cf31eb6` (2026-10-30 · team-lead 착수 · 작업 트리의 plans/200·INDEX 미커밋 변경 위에서 진행)
> **회귀 대상 파일**: `sre_agent/.env.example` · `sre_agent/sre_agent/{settings,diagnosis}.py` · `sre_agent/sre_agent/application/{investigation_dispatcher,investigation_guidance,playbook_runner,playbook_schedule,playbook_steps}.py` · `sre_agent/sre_agent/domain/playbook_readings.py` · `sre_agent/scripts/investigation_bench/{playbook_timeline,runner}.py` · `sre_agent/tests/test_{bench_runner,plan154_w1_instrumentation,plan187_w13_activity,plan187_w20a_flow}.py` · `sre_agent/tests/test_plan200_{w0_instrumentation,w1_deps,w2_dag,w3_axis,w4_guidance,verify}.py` · `scripts/manual/{content/admin.md,features.yaml}` · `src/static/manual/{admin.html,user.html,help_index.json}`
> **사용자 제기(2026-10-30)**: *"장애 진단 기능의 playbook이나 holmesgpt 등이 병렬처리가 없어서 분석 속도가 너무 느리다."* · *"플래이북의 조사 항목들이 순차적으로 진행되는 것으로 보인다. 또한 홈즈gpt의 도구 호출이 순차적으로 되고 있는 것으로 보인다. 이런 것을들 병렬 처리하여 응답 속도를 높일 수 있지 않냐?"* · *"검토한 내용을 현재 코드를 전체적으로 분석하여 수정, 추가, 변경 해야 될 사항이 있는지 확인하여 계획 파일로 만들어라."*
> **관련 결정**: D-341(플레이북 실행기 — 단계 묶음·㊳ 배치 동시 4·㊴ 스텝별 타임아웃·㊵ 타임아웃 중단·㊹ 시간 사슬·⑮ 심층 조사 운영 모드) · D-347(부하 상한 — 대기열 4·전역 도구 8·apm in-flight 8·시간당 예산 20) · D-343(Gemma 4 도구 호출 마크업 가드) · D-344(X-View 사전 확인) · D-350(측정 먼저 · 신규 동작 옵트인) · D-162(신규 플래그 기본 off) · D-255(매뉴얼 동반) · D-035(표 밖 수치 0)
> **관련 계획**: 187(플레이북 실행기) · 190 · 194 · 198(RCA 개선 — 측정 먼저 원칙)
> **결정 번호**: **D-353**(2026-10-30 등재 · 실행 설계 변경 — D-341 부기 25와 함께). 당초 D-351로 예약했으나 origin/main에 D-351(노이즈 관제 결정 이력), 병행 세션 collectorinfra-da가 D-352를 선점해 D-353으로 재채번했다.

---

## 0. 요약

"병렬이 전혀 없다"는 사실과 다르다. 이미 병렬로 도는 것이 다섯 겹 있다(§1.1). 실제로 느린 원인은 아래 네 가지다.

1. **플레이북 단계 장벽(가장 큼)** — `plan_stages`는 YAML 순서대로 연속 구간을 끊는다. 의존이 없는 스텝도 앞 단계가 끝날 때까지 기다린다. 예를 들어 host.cpu의 S0r(WAS 대상 확정)은 S4·S6 결과를 쓰지 않는데 S4·S6가 끝나야 시작하고, was.latency의 S4~S8은 S0만 필요한데 S1·S2 단계를 기다린다.
2. **스텝 안 직렬 축** — S5 `related_alarms`(폴스타 알람 → APM 이벤트)와 S7 `recent_changes`(APM 변경 → 폴스타 변경 이력)는 서로 독립인 두 축을 차례로 부른다.
3. **HolmesGPT 턴당 1호출 경향** — holmes 0.36.0은 한 응답의 도구 호출을 16스레드로 동시에 실행한다. 그러나 우리 지침(`investigation_guidance.py`)이 "①…⑥ 순서", "먼저 확보하라"처럼 순서를 지시해 모델이 한 턴에 하나씩 부르기 쉽다.
4. **측정 부재** — 턴당 도구 수, 단계별 소요 같은 계측이 없어 운영에서 어디가 느린지 판독할 수 없다.

개선은 **D-347 부하 상한(전역 도구 8 등)은 그대로 두고 그 안에서 스케줄만 바꾼다**. 모든 신규 동작은 플래그 기본 off로 둔다(D-162). 측정을 먼저 한다(W0 · D-350).

지연 주입 실측(도구 0.5s·LLM 1s, `case3_cpu_loop` · was.latency)으로 본 기대 효과:

- 단계 장벽 제거로 S4~S8이 S1·S2와 겹친다.
- S5·S7은 축 동시화로 스텝 소요가 각각 약 절반이 된다.
- 넘을 수 없는 상한(제니퍼 소스당 5/s, X-View 41조각, 의존 사슬 S0r→S2→S3a→S3b, HolmesGPT 최대 600s)은 §1.4에 적는다. **조사 전체 완료 시각은 HolmesGPT 종료에 묶이므로**, 플레이북 단축은 주로 `partial_briefing`과 플레이북 섹션이 먼저 보이는 시각을 앞당긴다.

---

## 1. 현행 분석 (HEAD `7cf31eb6` · 코드와 실측 근거)

### 1.1 이미 병렬인 것

| 층 | 장치 | 근거 |
|---|---|---|
| 플레이북 ∥ HolmesGPT | `SRE_DEEP_DIVE_MODE=parallel` 기본(시간당 예산 초과 시 conditional 강등 · rerun 힌트면 off) · YAML `deep_dive.mode` 재정의 0건 | `sre_agent/sre_agent/settings.py:306` |
| 같은 단계 안 스텝 | `ThreadPoolExecutor` · `DEFAULT_MAX_WORKERS=4`(dispatcher는 값을 넘기지 않아 기본값) | `application/playbook_runner.py:119` |
| 배치 안 도구 호출 | `SRE_TOOL_BATCH_CONCURRENCY` 4 · 전역 `SRE_TOOL_GLOBAL_CONCURRENCY` 8(D-347) · Semaphore + gather | `mcp_tool_client.run_tool_batch_async` |
| 단계 끝 소견 LLM | `StepOpinionStream` · `NarratorLimits.max_workers=4` — 실행 기록(`sre_activity_log_enabled`, 기본 False)이 켜지면 수집과 겹치고, 꺼지면 수집이 끝난 뒤 일괄 | narrator |
| HolmesGPT 한 응답의 도구들 | holmes 0.36.0 16스레드 동시 실행 · Gemma 마크업 가드는 블록 단위(D-343) · `_CachedToolInvoke`에 전역 잠금 없음 | `holmes/core/tool_calling_llm.py:1295` · `infrastructure/tool_call_markup_guard.py` · `application/diagnosis.py` |
| 서버 측 | apm_gateway httpx Async · `PriorityThrottle` 소스당 5/s · `max_inflight_per_source` 8 / mcp_server asyncpg·aiomysql · ibm_db `to_thread` | `apm_gateway/` · `mcp_server/` |

### 1.2 플레이북 단계 분할 — 현행 `plan_stages`(`playbook_runner.py:467`)

장벽 조건: 대상 확정 액션(`SCOPE_ACTIONS`) · 직전 단계에 대상 확정 스텝 · `when`이 현재 단계 스텝을 참조 · `when`이 현재 단계 스텝이 담당하는 판독(`READING_ACTIONS`)을 참조.

| 플레이북 | 현행 단계 | 실제 의존만 따르면 |
|---|---|---|
| host.cpu | `S4,S6 \| S0r \| S1,S2 \| S3a \| S3b,S5,S7` | S0r는 t=0에 시작 가능(S4·S6 결과 불사용) · S5·S7은 S0r만 필요 |
| host.process | `S4r,S4,S6,S5,S5a \| S0r \| S1,S2 \| S3a \| S3b,S7` | S0r t=0 가능 · S7은 S0r만 필요 |
| host.memory | `S4,S6 \| S0r \| S2,S5` | S0r t=0 가능 |
| was.latency | `S0 \| S1,S2 \| S3..S8` | S4~S8은 S0만 필요(S3만 S1 필요) |
| was.sql | `S0 \| S3,S3a \| S3b,S5,S7,S8` | S5·S7·S8은 S0만 필요 |
| 나머지 | 1~2단계 | 효과 작음 |

실측(scratchpad `pb_timeline.py` · 실제 dispatcher `scripts/investigation_bench/playbook_fixture_eval.py::evaluate_one` + 녹화 픽스처 + 도구 0.5s·LLM 1s 지연 주입, `case3_cpu_loop`):

- S0 내부 사슬: instance_map → instance_map×2 → app_health×5(의존 직렬 — 정상).
- 이후 단계는 4병렬이다.
- **S5 `polestar_incident_alarms` → `apm_events` 직렬**, **S7 `apm_source_changes,apm_change_impact` → `polestar_change_history` 직렬**이다(서로 독립).
- 픽스처(`sre_agent/tests/fixtures/plan187/*.json`)는 모두 WAS 계열이라 host.cpu는 재현하지 못했다(W0에서 픽스처 보강).

### 1.3 실행 루프 `_run`(`playbook_runner.py:961~`)의 계약 — DAG 전환 시 지켜야 할 것

- 단계마다 새 executor를 만든다(W21 교정: 타임아웃 스레드가 워커를 붙잡지 않게).
- `when_ctx = _when_context(results, _readings_of(...))`를 **단계 시작 시** 계산한다. Tri UNKNOWN이면 실행한다.
- 스텝 상한은 실제 시작 시각부터 잰다(`step_wait_s`).
- `prior=dict(results)`로 **앞 결과 전체**를 넘긴다.
- SCOPE_ACTIONS 결과로 scope를 교체한다.
- 예산 소진 시 `skipped(budget)`로 처리한다.
- rerun 힌트면 비폴스타 스텝은 `_reused_section`으로 처리한다.
- 이후 `retry_timed_out` 재시도(직렬)와 `_discriminate`가 이어진다.

**숨은 의존 — 현재 단계 구조 덕분에 우연히 맞는 것(DAG에서는 깨진다)**

1. `read_cpu_owner`(`domain/playbook_readings.py:580`)가 `_tps_change_pct`로 `apm.golden_signals`(S1)를 읽는다. 그런데 `READING_ACTIONS["cpu_owner"]`(`playbook_readings.py:44`)에는 S1이 빠져 있다. `_alarm_instance`는 `scope.resolve_was_targets`도 읽는다.
2. `ctx.prior` 독자:
   - `_was_cpu_top`(`playbook_steps.py:2233`) — `active_services`의 `target: was_cpu_top` → `apm.runtime_and_pool`
   - `active_sql`(`playbook_steps.py:2317`) → `apm.active_services`
3. **진척 ID 뒤바뀜 위험** — `investigation_dispatcher.py` `_progress_library.wrap()`은 `ctx.step_id`가 없으면 액션별 `pending` 대기열에서 `ids.pop(0)`으로 sid를 붙인다. 시작 순서가 YAML 순서와 달라지면 ID가 뒤바뀐다. 현재 플레이북에는 중복 액션이 없어 드러나지 않을 뿐이다.
4. `job.progress.current_step`은 단일 값이라 동시에 도는 스텝을 하나만 보여 준다.
5. 단계 끝 해석의 "앞 단계 결과" 줄(narrator `finish()`의 `prior` = 완료된 findings)은 완료 시점에 따라 달라진다. DAG에서는 그 변동이 커져 LLM 입력이 달라진다(→ Q2).

### 1.4 병렬화로 넘을 수 없는 상한 (계획 범위 밖 · 명시)

| 상한 | 값 | 영향 |
|---|---|---|
| 제니퍼 소스당 호출 속도 | 5/s(`JENNIFER_RATE_LIMIT_PER_SEC`) | 과거 시각 `apm_app_health`·`apm_slow_transactions`는 X-View 1분 조각 41호출 — 대상 5개면 205호출, 최소 41초 |
| X-View 조각 순차 루프 | `apm_gateway/apm_gateway/application/apm_core.py:243 xview()` · `instance_tools.py:510` | 동시화는 X-View 도구가 단독으로 돌 때만 효과 — 속도 상한이 같으므로 측정 뒤 조건부(Q4) |
| 전역 도구 동시 | 8(D-347 ②) | **바꾸지 않는다** |
| 의존 사슬 | S0r → S2 → S3a → S3b | 구조적 직렬 |
| LLM 서버 동시 처리 | vLLM 가능 · MLX 미확인 | 소견 LLM 동시 4가 실제로 겹치는지는 서버에 달림 |
| 조사 완료 | HolmesGPT 종료(최대 600s)에 묶임 | 플레이북 결과는 `partial_briefing`으로 먼저 보인다 |
| 플레이북 활성 | `.env.example`의 `SRE_PLAYBOOK_ENABLED` 기본 false | **폐쇄망 `.env` 값 확인 필요** — off면 A·B 효과 0 |

### 1.5 HolmesGPT 지침의 순서 지시 (`sre_agent/sre_agent/application/investigation_guidance.py`)

- holmes intro 프롬프트(`generic_ask.jinja2` — "Ask for multiple tool calls at the same time")는 `ENABLED_PROMPTS`가 없으면 켜져 있다.
- 우리 지침이 이와 경쟁한다:
  - `APM_FOCUS_NOTE_TEMPLATE`(:407) — "APM(제니퍼 게이트웨이) 조사 순서 — ①…⑥"
  - `INCIDENT_SCOPE_NOTE_TEMPLATE`(:78) — "polestar_incident_alarms 로 먼저 확보하라"
  - `PLAYBOOK_NOTES` cpu — "먼저 판정"
  - `SOP_ANCHOR_HEADER`(:845) — "이 순서로 확인하되"(옵트인 `SOP_ANCHOR_ENABLED`)
- 운영 모델은 vLLM 위의 Gemma 4다(D-343). 프롬프트 접두가 바이트 동일해야 KV 캐시가 유지되므로 문구를 바꾸면 기본 경로의 캐시 적중과 출력이 바뀐다. 그래서 **플래그로만** 바꾼다.

---

## 2. 개선안

### I-0 측정 (W0 · 먼저)

- **HolmesGPT 턴 계측**: `_StreamDrain.run`(`application/diagnosis.py` 약 410행)이 이미 턴별 `start_tool_count`를 센다. 결과와 `_instrumentation`(`investigation_dispatcher.py:2129` — 현재 `steps`·`tool_calls_n`)에 가산 키를 더한다: `tool_batch_sizes`(턴별 도구 수 목록), `max_tools_per_turn`, `turns_n`.
- **플레이북 단계 계측**: 플레이북 경로 instrumentation에 `stage_wall_s`(단계별 벽시계), `collect_s`, `opinion_wait_s`, `synthesis_s`, `deep_dive_wait_s`를 가산한다. 키 추가만 하므로 응답 계약은 가산형이다.
- **타임라인 하네스**: scratchpad `pb_timeline.py`·`pb_timeline_act.py`를 `sre_agent/scripts/investigation_bench/playbook_timeline.py`로 정식화한다. 픽스처 이름·도구 지연·LLM 지연·`--dag on/off`를 인자로 받아 스텝별 시작·종료 표와 총 벽시계를 출력한다. 오프라인이다(LLM 가짜 · MCP 녹화).
- **픽스처 보강**: host.cpu·host.process 녹화 픽스처 1건씩(기존 `tests/fixtures/plan187/` 형식 · 합성). 1.2의 host 계열 이득을 재현하려면 필요하다.
- 수용: 계측 키가 기존 키를 바꾸지 않는다(스냅숏 `tests/fixtures/plan187/snapshots/*` 불변) · 하네스가 현행 실측과 같은 직렬 구간을 재현한다.

### I-1 의존 표와 가드 (W1 · 동작 불변)

- `playbook_readings.py`에 **`READING_INPUTS`** 표를 둔다 — 판독 이름 → 실제로 읽는 액션 집합(`cpu_owner`에 `apm.golden_signals`·`scope.resolve_was_targets` 포함). `READING_ACTIONS`는 다른 출력(판독 귀속)에 쓰이므로 **바꾸지 않는다**.
- `playbook_steps.py`에 **`PRIOR_INPUTS`** 표를 둔다 — 액션 → `ctx.prior`에서 읽는 액션(`apm.active_services` → `apm.runtime_and_pool` · `apm.active_sql` → `apm.active_services`).
- **가드 테스트**:
  - read_* 함수에 기록 프록시 결과를 넣어 실제로 접근한 액션 ⊆ `READING_INPUTS[r]`인지 단언한다.
  - 스텝 함수가 `ctx.prior`에서 꺼낸 액션 ⊆ `PRIOR_INPUTS[action]`인지 단언한다.
  - 새 판독이나 prior 독자가 표 없이 추가되면 실패한다.
- **step_id 선지정**: 실행기가 모든 스텝의 `StepContext`에 `step_id`를 미리 달아 `_progress_library.wrap()`의 `ids.pop(0)` 경로를 타지 않게 한다. 이 변경만으로도 현행 동작은 같다(중복 액션 0).
- `deps_of(step, playbook) -> frozenset[str]` 순수 함수를 추가한다. 의존은 다음의 합집합이다:
  - when 스텝 참조
  - when 판독의 `READING_INPUTS` 중 앞 스텝
  - `PRIOR_INPUTS` 중 앞 스텝
  - 앞선 대상 확정 스텝(대상 확정 스텝 자신은 앞 스텝에 의존하지 않음)
- 수용: 1.2 표의 "실제 의존"을 플레이북별로 단언하는 테스트가 통과한다 · `plan_stages` 결과는 그대로다.

### I-2 DAG 실행기 (W2 · 플래그 `SRE_PLAYBOOK_DAG_SCHEDULING_ENABLED` 기본 off)

- `_run`에 분기를 둔다. off면 현행 `plan_stages` 루프를 그대로 탄다(비트 동일).
- on이면 다음과 같이 동작한다:
  - 의존이 모두 끝난 스텝을 준비 큐에 넣는다. 슬롯은 `SRE_PLAYBOOK_MAX_WORKERS`(기본 4 · 1~8)이다.
  - 마감 기반 `FIRST_COMPLETED` 대기를 쓴다.
  - **결정성 유지**: when 평가와 `prior`는 **조상 스텝 결과로 한정**한다. 동시에 준비된 스텝은 YAML 순서로 제출한다. 결과 조립(섹션·findings 순서)은 YAML 순서로 한다.
  - **타임아웃 슬롯 반환**: 스텝별 스레드와 슬롯 카운터 방식으로, 상한을 넘긴 스레드가 슬롯을 계속 붙잡지 않게 한다(W21 교정과 같은 의도 · ㊵ 중단 신호 유지).
  - 예산 검사는 제출 시점에 한다(`skipped(budget)` 의미 유지).
  - scope 교체는 대상 확정 스텝 완료 시점에 하고, 그 후손 스텝만 새 scope로 제출한다.
  - rerun `_reused_section`, `retry_timed_out`, `_discriminate`는 DAG가 끝난 뒤 현행대로 진행한다.
- `max_workers=1`이면 YAML 순서 실행과 같아야 한다(기존 결정적 테스트를 그대로 통과).
- (선택) `job.progress`에 `running_steps` 목록을 가산한다. `current_step`은 마지막으로 시작한 스텝으로 유지한다(UI 무변경).
- 시간 사슬(D-341 ㊹: 제니퍼 30 < MCP 60 < 스텝 90 ≤ S0 120 ≤ 수집 240 < 조사 600)은 그대로 둔다. `timechain_warnings()`(`settings.py` 약 404행)에 `MAX_WORKERS`×도구 배치 4 > 전역 8일 때 경고를 넣을지는 W2에서 판단한다. 경고는 정보성이고, 전역 Semaphore가 실제 상한이다.
- 리스크:
  - 동시 스텝이 늘면 전역 8 Semaphore 대기가 늘어 스텝 시간 측정값(시작 시각 기준)이 커질 수 있다. 상한 판정이 바뀌지 않는지 W0 하네스로 확인한다.
  - 단계 끝 해석 묶음은 단계가 사라지므로 "스텝 완료 즉시 소견"으로 바뀐다. narrator `StepOpinionStream`이 이미 스텝 단위인지 W2 착수 때 확인하고, 단계 개념에 묶인 곳(`stage` 필드 등)은 DAG 깊이(위상 레벨)로 대체한다.

### I-3 스텝 안 축 동시 조회 (W3 · 플래그 `SRE_PLAYBOOK_AXIS_CONCURRENCY_ENABLED` 기본 off)

- 대상: S5 `related_alarms`(`playbook_steps.py:1815`), S7 `recent_changes`(`playbook_steps.py:2016`). 두 축이 독립임을 코드로 확인했다(apm 인스턴스는 targets/scope에서 온다).
- `StepContext.call()`(`playbook_steps.py` 약 430행)은 `tools`·`calls`·`outputs`를 잠금 없이 append한다. 따라서 **호출만 동시에 하고 기록과 append는 고정된 축 순서로 하는** 헬퍼 `ctx.call_axes([(axis, calls), ...])`를 둔다:
  - ~~내부에서 한 번의 `run_tool_batch_async`로 모아 보낸다.~~ (구현 정정) 축별 호출 콜러블을 스레드로 동시에 실행한다 — 각 축은 종전 `call()` 경로(`run_tool_batch_async` 포함)를 그대로 타므로 배치 동시 4·전역 8을 그대로 따른다.
  - 반환 뒤 축 순서대로 기록한다.
  - cancel 확인, 진행 계수, activity `tool_start`/`tool_end` 이벤트를 현행 `call()`과 같은 순서·개수로 낸다.
- 다른 다중 호출 스텝(`resolve_was_targets`·`slow_tx_breakdown`·`active_sql`·`host_to_was`)은 실제 의존 사슬이므로 대상이 아니다.
- (선택 · 낮은 우선순위) S0 `_baseline_calls`는 첫 instance_map 결과만 필요하므로 2번째 배치로 앞당길 수 있다. W0 측정에서 S0이 임계 경로로 확인될 때만 착수한다.
- 수용: off/on 섹션·findings·호출 목록(순서 포함)이 같다 · 지연 주입 시 S5·S7 스텝 소요가 줄어든다.

### I-4 HolmesGPT 턴당 다중 도구 호출 유도 (W4 · 플래그 `SRE_GUIDANCE_PARALLEL_TOOLS_ENABLED` 기본 off)

- off면 지침 문자열이 바이트 동일하다(KV 캐시·프롬프트 렌더 불변).
- on이면 순서형 문구를 **"확정 → 동시 요청"** 형태로 바꾼 변형을 쓴다. 예: "① `apm_instance_map`으로 대상을 확정한 뒤 `apm_events`·`apm_app_health`·`apm_runtime_health`·`polestar_metric_trend`를 **한 턴에 함께** 요청하라." 대상은 `APM_FOCUS_NOTE_TEMPLATE`·`INCIDENT_SCOPE_NOTE_TEMPLATE`·`PLAYBOOK_NOTES`(cpu)·`SOP_ANCHOR_HEADER`다. 의존이 있는 순서(대상 확정 → 조회)는 남긴다.
- 효과는 모델 순응도에 달려 있다. W0의 `max_tools_per_turn`·`tool_batch_sizes`로 off/on을 비교한다.
- MLX 최소 확인: 두 평면 `mlx`를 `--preflight`로 확인한 뒤, 대표 2건만 1회 돌려 턴당 도구 수가 늘고 Gemma 마크업 가드(D-343)에 위반이 없는지 본다. 결론은 내부망 vLLM 측정으로 낸다.
- 영향 테스트: `test_plan134_w7_guidance.py`·`test_plan198_w5_sop_toolset.py`·`test_plan188_toolset_availability.py`·`test_plan87_j8_source_id.py`·`test_plan198_fix_sre.py`. off 경로 문자열 불변을 단언하는 테스트를 추가한다.

### I-5 설정·문서·매뉴얼 (W5)

- 신규 설정을 sre_agent `settings.py`와 `.env.example`에 추가하고, `interface/config_admin_adapter.py`(D-345 설정 API)에 등록한다:
  - `SRE_PLAYBOOK_DAG_SCHEDULING_ENABLED`
  - `SRE_PLAYBOOK_MAX_WORKERS`
  - `SRE_PLAYBOOK_AXIS_CONCURRENCY_ENABLED`
  - `SRE_GUIDANCE_PARALLEL_TOOLS_ENABLED`
  - (검토) `SRE_NARRATOR_MAX_WORKERS` 노출(현행 기본 4 고정)
- 관리자 설정 화면에 노출되면 D-255 대상이다. `config/settings_help/` 도움말과 admin 매뉴얼(25절 부근 · `scripts/manual/` 원천 → build → `pytest tests/test_manual`)을 갱신한다. 노출하지 않으면 미대상으로 보고한다.
- D-353을 등재하고(D-341 실행기 조항과의 관계 · D-347 상한 불변 명시) D-341에 부기 1줄을 단다. 실행기·동시성 설정은 현재 D-341 본문에만 기술돼 있고 `docs/26_sre_agent_guide.md`에는 해당 절이 없다(실측 grep) — 운영 가이드에 플래그 4종 표를 새로 넣을지는 W5에서 판단한다.

### I-6 (조건부 · 낮은 우선순위) X-View 조각 동시화 — Q4

- `apm_core.py:243 xview()` 순차 루프를 소스당 속도 상한 안에서 동시화하는 안이다. 상한이 5/s로 같으므로 이득은 HTTP 왕복 지연이 1/5s보다 클 때만 생긴다. W0 내부망 측정에서 X-View 단독 구간이 임계 경로로 확인될 때만 별도 계획으로 뺀다.

---

## 3. Wave 구성

| Wave | 내용 | 산출 | 검증 |
|---|---|---|---|
| **W0** | I-0 계측·하네스·host 픽스처 · D-353 예약 행 | 계측 키 가산 · `playbook_timeline.py` | 스냅숏 불변 · 하네스가 1.2 직렬 구간 재현 |
| **W1** | I-1 의존 표·가드·`deps_of`·step_id 선지정 | 표 2종 · 가드 테스트 | `plan_stages` 결과·기존 테스트 불변 · 플레이북별 의존 단언 |
| **W2** | I-2 DAG 실행기(플래그 off) | `_run` DAG 분기 | **동등성**: 전 픽스처 × off/on 섹션·findings·판독 동일 · `max_workers=1` = 순차 · 지연 주입 벽시계 감소 · 타임아웃·예산·cancel·rerun 경로 |
| **W3** | I-3 축 동시(플래그 off) | `ctx.call_axes` · S5·S7 | 호출 목록·기록 순서 동일 · 지연 주입 S5·S7 단축 |
| **W4** | I-4 지침 변형(플래그 off) + MLX 최소 확인 | 지침 변형 | off 바이트 동일 · `prompt_render_diff` · MLX 대표 2건 |
| **W5** | I-5 설정·문서·매뉴얼·D-353 | 설정 등록 · 문서 | `pytest tests/test_manual`(노출 시) |
| 내부망 | 운영 측정 | off/on 벽시계 · 턴당 도구 수 | §4 수용 기준 4·5 |

- Wave 중간 회귀: implementer가 자기 테스트만 돌리고 `regress.py --no-tests --files …`(D-303)를 돌린다. sre_agent 테스트는 `sre_agent/.venv/bin/python -m pytest`를 쓴다.
- 계획 끝: `python scripts/regress.py --base <계획 시작 SHA> --files <계획이 바꾼 파일…>`. 보고에는 범위 줄을 그대로 옮긴다.
- W2는 `settings.py`·공개 시그니처를 바꾸므로 `[웨이브 회귀 필요]`가 뜨면 그 Wave 끝에 모듈 단위 회귀를 돌린다.

---

## 4. 수용 기준

1. **비트 동일(off)**: 신규 플래그가 모두 off면 플레이북 결과·스냅숏·지침 문자열·진척 이벤트가 HEAD와 같다.
2. **동등성(on)**: DAG·축 동시가 on이어도 전 녹화 픽스처에서 섹션·findings·판독·가설 순위(D-341 ④ 결정적)가 off와 같다. 단 단계 끝 해석의 "앞 단계 결과" 줄은 Q2 결정을 따른다.
3. **상한 불변**: 전역 도구 동시 8·배치 4·apm in-flight 8·시간당 예산 20(D-347)과 시간 사슬(D-341 ㊹) 값이 바뀌지 않는다.
4. **단축(오프라인)**: 도구 0.5s·LLM 1s 지연 주입 하네스에서 was.latency·was.sql·host.cpu(보강 픽스처)의 플레이북 수집 벽시계가 off 대비 줄어든다. 감소량은 W0 기준선으로 확정해 이 줄에 적는다. **실측(2026-10-30 · DAG on · 실행 기록 off/on)**: host.cpu 4.05~4.10 → 3.06 / 3.08s · host.process 4.55~4.60 → 3.55 / 3.60s · was.latency `case3_cpu_loop` 4.04~4.09 → 3.54 / 3.60s(`case1_jdbc_acquire` 4.05~4.08 → 3.54~3.57). was.sql은 녹화 픽스처가 없어 미측정(내부망 이월). 축 동시 단독(지연 0.2s) S5 415→216ms · S7 422→213ms.
5. **운영 측정(내부망)**: `max_tools_per_turn` 분포와 플레이북 `collect_s`의 off/on 비교를 기록한다. 개선이 없으면 플래그를 off로 남기고 사유를 기록한다.

---

## 5. 미결 질문 (사용자 결정)

> **2026-10-30 확정 — Q1~Q4 모두 아래 권고대로**(머리 v1.1 결정 참조).

- **Q1 — D-341 실행기 조항 개정 승인**: 현행은 "YAML 순서대로 연속 구간을 단계로 묶어 같은 단계만 병렬"이다. 이를 "의존이 끝난 스텝부터 실행(플래그 opt-in)"으로 바꾸는 것을 D-353으로 등재할지 결정이 필요하다. 권고: **승인 · 기본 off**.
- **Q2 — 단계 끝 해석의 "앞 단계 결과" 줄**: DAG에서는 완료 시점에 따라 이 줄의 내용이 흔들린다. 선택지는 (a) **조상 스텝 결과로 한정**(결정적 · 권고), (b) 현행처럼 완료된 전부(비결정 · LLM 입력 변동)다.
- **Q3 — 플레이북 스텝 동시 기본값**: `SRE_PLAYBOOK_MAX_WORKERS` 기본 4 유지(권고 · 전역 8 Semaphore가 실제 상한) 또는 6~8로 상향. 상향하면 전역 대기가 늘어 스텝 시간 측정값이 커질 수 있다.
- **Q4 — X-View 조각 동시화(I-6) 착수 여부**: 권고는 **W0 내부망 측정 뒤 판단**이다(소스당 5/s 상한이 같아 이득이 불확실).
- (확인 완료) 폐쇄망 `.env`의 `SRE_PLAYBOOK_ENABLED=true` — A·B 개선이 운영에 유효하다.

## 6. 하지 않는 것

- **투기 실행**: when 판정 전에 스텝을 미리 돌리는 방식. 부하가 늘고 D-347 취지와 충돌한다.
- **가설별 다중 HolmesGPT 동시 실행**: LLM·도구 부하가 배로 늘고 시간당 예산 20(D-347)을 소진한다.
- **부하 상한 변경**: 전역 8·배치 4·in-flight 8·소스당 5/s는 바꾸지 않는다.
- **HolmesGPT 내부(holmes 패키지) 수정**: 이미 응답 단위 16스레드 병렬이다. 지침만 바꾼다.
- **기본 경로 프롬프트 변경**: 플래그 off면 바이트 동일하다(KV 캐시 보존).

---

## 구현 부기 (2026-10-30 · team-lead)

- **W0 계측**: `DiagnosisResult.tool_batch_sizes`·`turns_n` · 조사 `done` instrumentation 가산 키 `tool_batch_sizes`·`max_tools_per_turn`·`turns_n` · 플레이북 `stage_wall_s`·`collect_s`·`opinion_wait_s`·`synthesis_s`·`deep_dive_wait_s` · 하네스 `scripts/investigation_bench/playbook_timeline.py`(`--dag`·`--axis`·`--max-workers`·`--activity`) · host 픽스처 `tests/fixtures/plan200/`(plan187 픽스처 이름 집합 고정 단언 때문에 별도 폴더).
- **W1 의존 표**: `READING_INPUTS`(heap_shape의 숨은 golden_signals 의존 발견) · `PRIOR_INPUTS` · `application/playbook_schedule.py`(`deps_of`·`ancestors`·`reading_refs`·`narration_basis`).
- **W2 DAG**: 스텝별 daemon 스레드 + 슬롯 · 마감 대기 · 조상 한정 when/prior/scope · 제출 시점 예산(슬롯 대기 중 예산 소진 = `skipped(budget)` — on 전용) · 결과 YAML 순서 · `stage_wall_s`는 위상 레벨별.
- **W2 교정(verifier Major)**: DAG on에서 단계 끝 판독을 조상으로 좁혀 자기 판독이 off와 달라졌다(host.cpu S2 `cpu_owner`). 1차로 판독 입력을 실행 의존에 넣었으나 was.latency 단축이 0이 돼 되돌렸다. 최종안은 **실행은 그대로, 단계 끝 처리 묶음만 해석 기준(`narration_basis`) 정착까지 미루고 수집 직후 flush**한다(D-353 ②). 픽스처 12건에서 결과 한 줄·소견 요청 판독이 off와 같다.
- **W3 축 동시**: `StepContext.call_axes` · S5·S7. 교정 2건 — off 경로 호출 준비 순서 원복(예외 경로 비트 동일) · on에서 실제 호출한 축은 예외가 나도 모두 기록(따라서 앞 축 예외 시 on의 `ctx.calls`가 off와 다름 — on 전용 차이).
- **W4 지침 변형**: `*_PARALLEL` 상수 4종 · off는 HEAD와 바이트 동일(10조합 sha256). `prompt_render_diff`는 sre_agent 지침이 대상 밖이라 미적용.
- **W5**: 설정 4필드(설정 관리 화면은 `AgentSettings` 인트로스펙션으로 자동 노출) · 관리자 매뉴얼 A-77 4행(D-255 반영) · D-353 · D-341 부기 25.
- **잔여**: W1 테스트 일부 동어반복 단언(runner `_reading_refs`가 schedule 것의 별칭) · 하네스 Minor(threading.local 귀속·`wait_workers` 원복) · `SRE_NARRATOR_MAX_WORKERS` 미노출 · `running_steps` 진척 키 미구현(선택) · `timechain_warnings()` 경고 미추가(정보성 · 전역 Semaphore가 실제 상한) · `latency_sql`·`case2_ldap_external` 픽스처 미녹화 14건(기존) · mypy 미실행(venv에 mypy 없음) · DAG에서 조상 묶음이 미뤄진 동안 `because_of` 인용이 빠질 수 있음(현 플레이북은 when 참조 스텝의 기준이 모두 조상이라 영향 0).
