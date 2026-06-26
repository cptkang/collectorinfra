# 50. 멀티턴 컨텍스트 전파 개선 + 제어 평면(vLLM) 토큰 한계 대응

> 작성일: 2026-06-25
> 상위/관련 계획: `plans/48-deepagents-intent-orchestration.md`, `plans/49-phase2-dynamic-replanning.md`, `plans/multiturn_plan.md`
> 관련 결정: D-013(멀티턴+HITL), D-037(deepagents 이원 백엔드), D-009(SSE), D-047-1/Plan 47-1(폴스타 프로세스 API)
> 신규 결정(기록 완료): **D-041**(멀티턴 컨텍스트 전파·엔티티 보존), **D-042**(제어 평면 컨텍스트 예산·평면 분리·Qwen no-think). ※ 당초 D-039/D-040을 의도했으나 두 번호는 `docs/02_decision.md` **변경 이력 표에서 이미 선점**(2026-06-23/24 처리현황·replanner 작업, 교차참조 존재)되어 다음 빈 번호 D-041/D-042를 부여함.

---

## 1. 배경 — 검증으로 확인된 문제

테스트 시나리오:

1. (턴1) "김포 운영 ### 서버의 CPU 코어 수, 메모리 용량 및 사용률" → 정상 응답
2. (턴2) "해당 서버에 대한 현재 프로세스 리스트를 확인해줘" → **"조건에 해당하는 서버, 프로세스 데이터가 없습니다."**

코드 기반 검증 결과 후속 턴이 실패하는 **구조적 단절점 5가지**를 확인했다.

### 1.1 멀티턴 단절점

| # | 단절점 | 근거(코드) | 증상 |
|---|--------|-----------|------|
| M1 | **`intent_planner`가 이전 맥락을 전혀 받지 않음** | `intent_planner._llm_decompose`는 `SystemMessage(템플릿)+HumanMessage(user_query)`만 전달 (`src/orchestration/intent_planner.py:164-169`) | "해당 서버"를 맨몸으로 분해 → "김포" DB 식별 신호가 sub_query에 없음 → DB 라우팅이 모든 폴스타 후보(`polestar_b0`, `polestar_cm_gp`, `polestar_cm_yd`)로 fan-out |
| M2 | **이전 턴 DB가 데이터 조회 경로에서 재사용 안 됨** | `previous_db_id`는 `context_resolver`가 채우지만 소비처가 `cache_management.py:65-66` 하나뿐. `data_query`는 매 턴 새로 DB 선택 | "이전 턴이 김포였으니 이번도 김포"라는 로직 부재 |
| M3 | **"해당 서버" 엔티티가 구조적으로 보존 안 됨** | `conversation_context`는 `previous_sql`(SQL 텍스트)·`previous_results_summary`("N건 조회됨, 컬럼:…")만 담음. 실제 hostname 값·위치값 없음 (`context_resolver.py:69-88`) | 후속 턴이 직전 대상 서버를 식별 불가 |
| M4 | **실시간 프로세스 API 경로가 메인 그래프에 없음** | `polestar_process_api.py`는 `alarm` 모듈 전용. `intent_planner` agent 타입은 `data_query/alarm_query/cache_management/synonym_registration/general_inference` 5종뿐 | 프로세스 요청이 `data_query`(DB 조회)로 오분류 → 없는 테이블 `SDQ000.MON_CF_WAIT_TIME` 조회 → `SQL0204N` |
| M5 | **토큰 폭증** | `context_resolver._trim_messages`는 10턴(20메시지) 기준 — 단일 턴 내 재계획 누적은 못 막음 | `Input tokens must be <=95232. Given: 197986` |

### 1.2 제어 평면 토큰 한계 (M5 심층 분석)

관찰된 오류:
```
An exception occurred in GptOssAdapter.llm_call: Input tokens must be <=95232. Given: 197986.
Error occurred from orchestrator.
```

> **모델 정정**: 오류 문자열의 `GptOssAdapter`는 **모델 정체가 아니라 vLLM 서버 측 어댑터 클래스명**이다. **현재 vLLM이 서빙하는 실제 모델은 Qwen3.5-9B**(`config.py:76` `OrchestratorConfig.model="Qwen3.5-9B"`와 일치)다. 따라서 `~95K` 한계는 gpt-oss가 아니라 **Qwen3.5-9B 서빙 설정(`max_model_len`)** 의 컨텍스트 창이다. (어댑터 이름이 gpt-oss로 보이더라도 모델은 Qwen — 혼동 주의)

`GptOssAdapter`는 **본 레포에 없다** — 폐쇄망 내부 vLLM(OpenAI 호환 `/v1`) 서버 측 어댑터다. 즉 이 오류는 **제어 평면(오케스트레이터 = vLLM Qwen3.5-9B)** 호출에서 발생한다. 사용자 가설이 정확하며 D-037/Plan 49의 설계와 정합한다:

| 평면 | 구성 | tool-calling | 컨텍스트 한계 | 근거 |
|------|------|-------------|--------------|------|
| **제어(control)** | vLLM **Qwen3.5-9B** (`ChatOpenAI`→vLLM) | **필수** | **작음(~95K, 서빙 `max_model_len`)** | `src/llm.py:117-138`, `config.py:76`, `deep_agent.py:114` |
| **데이터(data)** | FabriX `KBGenAIChat` REST | **불가** | 큼(대용량 허용) | `src/clients/fabrix_kbgenai.py`, Plan 49 §1.2 |

**근본 원인**: tool-calling(동적 재계획·도구 위임)은 vLLM에서만 가능하지만(FabriX는 `bind_tools`가 레지스트리에 저장만 할 뿐 REST API가 tool_calls를 내지 못함 — `fabrix_kbgenai.py:240-245`), 현재 서빙 모델 **Qwen3.5-9B(소형)의 컨텍스트 창(~95K)이 작다**. tool-calling 오케스트레이터는 **매 반복마다 전체 메시지 히스토리**(system + 이전 tool_calls + ToolMessage 결과 전부)를 컨텍스트에 누적한다. 멀티턴 히스토리 + 다중 task + 재계획이 겹치며 197,986까지 폭증했다.

> **딜레마(사용자 지적 그대로)**: FabriX로 바꾸면 토큰 한계는 극복하지만 tool-calling이 안 되어 DeepAgent(제어 평면)를 구동할 수 없다. 따라서 "FabriX로 통째 교체"는 답이 아니며, **제어 평면에 들어가는 컨텍스트를 줄이고**, **제어가 꼭 필요 없는 일은 FabriX(대용량) 평면으로 내리는** 방향이 정답이다.

---

## 2. 목표 / 성공 기준

1. (M1) 후속 턴 "해당 서버…"가 직전 턴의 **DB(김포)·대상 서버(### )를 승계**하여 같은 DB·같은 서버로 조회된다.
2. (M2) `data_query` 경로가 이전 턴 DB를 **기본 후보로 우선**한다(명시적 새 위치 신호가 없을 때).
3. (M3) 직전 턴의 **해소된 엔티티(위치/DB/대상 서버 식별자)** 가 구조적으로 보존되어 후속 턴 분해에 주입된다.
4. (M4) "프로세스 리스트" 류 **실시간 조회**가 DB 조회가 아닌 **프로세스 API 경로**로 라우팅된다(없는 테이블 조회·`SQL0204N` 재발 방지).
5. (M5) 제어 평면(vLLM Qwen3.5-9B) 입력 토큰이 **상한 내로 유지**된다(원시 도구 결과·스키마·대용량 데이터가 오케스트레이터 컨텍스트로 새지 않음).
6. **회귀 없음**: 첫 턴/단일 의도/`semantic_router` 폴백 경로는 무변경 동작.

---

## 3. 개선 설계

### 3.1 [M1·M3] 멀티턴 컨텍스트를 intent_planner에 주입 + 엔티티 구조 보존

**핵심 원칙**: 단절점은 `intent_planner`다. 여기에 직전 턴의 *해소된 신호*를 넣어야 후속 분해가 올바른 sub_query를 만든다. 그리고 이 보강은 **FabriX(대용량) 평면의 JSON 프롬프트 planner**에서 수행하므로 토큰 한계와 무관하다(B 파트와 시너지).

(1) **`context_resolver` 확장 — 엔티티 보존 (M3)**
`conversation_context`에 다음 필드를 추가한다.

| 신규 필드 | 출처 | 용도 |
|-----------|------|------|
| `previous_db_ids` | 직전 턴 `target_databases`/`active_db_id`/`mapped_db_ids` 통합 | 후속 턴 DB 승계 |
| `previous_entities` | 직전 턴 `parsed_requirements.filter_conditions` 중 식별 키(hostname/name/ip/장비명) + 결과 첫 행 식별 컬럼 값 | "해당 서버" 지시어 해소 |
| `previous_location` | 직전 턴 `parsed_requirements.target_db_hints`/원문에서 추출한 폴스타 위치(김포/여의도/은행/공동존) | DB 식별 신호 승계 |

- 결과 요약(`previous_results_summary`)은 현재 "건수+컬럼명"만 담아 값이 없다 — 식별 키 컬럼은 **값까지** 소량(상한 N행) 보존한다. (대량 보존 금지 — Known Mistakes 2026-06-11 사전 순회 상한 원칙 준수)

(2) **`intent_planner`에 맥락 주입 (M1)**
`_llm_decompose(llm, user_query, app_config)` 시그니처에 `conversation_context`를 추가하고, `turn_count > 1`이면 HumanMessage 앞에 **압축 맥락 블록**을 삽입한다.

```
## 이전 대화 맥락 (후속 턴 분해 시 활용)
- 직전 대상 DB/위치: 김포 운영 폴스타 (polestar_cm_gp)
- 직전 대상 서버: ### (hostname=###)
- 직전 작업 요약: CPU/메모리 조회 N건
지시어("해당 서버", "그 장비", "위 결과") 해소 규칙:
- 사용자가 새 위치/DB/대상을 명시하지 않으면 위 직전 값을 sub_query에 그대로 보존하라.
```

- `INTENT_PLANNER_SYSTEM_TEMPLATE`에 "지시어 해소 + 직전 DB 신호 승계" 규칙과 예시(예시 5: 후속 턴) 추가.
- **주의**: 압축 블록만 주입한다(원시 메시지 히스토리 금지). planner는 FabriX(대용량)지만, 제어 평면 일관성·비용을 위해 동일 원칙 적용.

(3) **`graph.py` 배선**: `intent_planner` partial에 `conversation_context`가 state로 이미 흐르므로 노드 내부에서 `state.get("conversation_context")`만 읽으면 된다(시그니처 무변경, 내부 참조 추가).

### 3.2 [M2] data_query 경로의 이전 턴 DB 승계

- `subagents._make_isolated_input`는 이미 `conversation_context`를 subagent state로 전달한다(`subagents.py:300`). DB 선택 로직(semantic_router/db_registry/field_mapper)이 **새 위치 신호가 없을 때** `conversation_context.previous_db_ids`를 **우선 후보**로 사용하도록 보강한다.
- 우선순위: ① 이번 턴 명시 위치/DB > ② `mapped_db_ids`(양식) > ③ `previous_db_ids`(멀티턴 승계) > ④ 전체 후보 fan-out.
- 승계가 적용되면 처리현황 UI에 "이전 턴 DB(김포) 승계" 한 줄을 노출(투명성).

### 3.3 [M4] 실시간 프로세스 API를 1급 intent agent로 승격

- **신규 agent 타입 `process_query`** 를 `intent_planner` 프롬프트 분류 목록에 추가:
  - 분류 규칙: "현재/실시간 프로세스 리스트·실행 중 프로세스·top 프로세스" → `process_query` (DB 이력이 아닌 실시간 API).
  - 기존 `data_query`(DB)와 구분: "프로세스 **이력/추세**"는 data_query, "**현재** 프로세스"는 process_query.
- **신규 subagent `process_query`** 를 `SUBAGENT_REGISTRY`에 등록. 내부는 alarm 모듈의 `polestar_process_api.py`(Plan 47-1)를 **재사용**한다. db_id→base_url 매핑은 `AlarmConfig.get_process_api_base_url`(`config.py:314`)가 이미 제공.
  - 대상 db_id는 §3.2의 DB 승계로 결정(김포=`polestar_cm_gp`).
  - 대상 hostname은 §3.1 `previous_entities`로 결정(### ).
  - 마스킹·상위 N 선별은 Plan 47-1의 결정적 처리(`process_rank.py`) 재사용 — LLM에 원시 주입 금지(D-047-1 / Known Mistakes 정합).
- **아키텍처 주의**: `process_query` subagent는 application/orchestration 계층, `polestar_process_api`는 infrastructure 계층 → 의존 방향 정합. `scripts/arch_check.py`로 검증.

### 3.4 [M5 / B] 제어 평면 컨텍스트 예산 + 평면 분리 (토큰 한계 대응)

사용자 맥락(FabriX=대용량/무 tool-calling, vLLM=tool-calling/소용량)을 그대로 반영한 **3중 방어**:

**B1. 제어 평면을 얇게 유지 — 원시 결과 차단**
- 오케스트레이터(vLLM)에는 **계획 신호만** 전달한다: task 목록, 압축 상태(`completed/failed`, 건수), 식별 키 요약. **원시 SQL 결과·스키마·문서 데이터는 절대 오케스트레이터 컨텍스트로 보내지 않는다.**
- collector 패턴(`deep_agent.py:195` / Plan 49 §4.3 step6)은 이미 원본 결과를 별도 보관한다. 그러나 deepagents 런타임은 ToolMessage(직렬화본)를 오케스트레이터 메시지에 누적한다 → **도구 반환 페이로드를 압축 요약본으로 축소**하여 반환(원본은 collector에만 적재).

**B2. 제어 평면 컨텍스트 예산(트리밍/요약)**
- 재계획 반복 간 오케스트레이터 메시지를 **상한 토큰 예산**으로 관리: 오래된 tool_call/ToolMessage 쌍을 요약 1줄로 축약(deepagents Summarization 미들웨어 활용 또는 pre-call 트리머).
- 상한 도달 시 **데이터 평면(FabriX)로 강등**: 추가 분해가 불필요하면 tool-calling 루프를 종료하고 FabriX `result_aggregator`로 마무리.

**B3. 멀티턴 히스토리를 제어 평면에 원문 주입 금지**
- `context_resolver`의 압축 `conversation_context`(§3.1)만 오케스트레이터로 전달. 원시 `messages` 누적분은 데이터 평면에만 둔다.
- `_trim_messages` 상한을 **턴 수 + 누적 토큰** 이중 기준으로 강화(현재는 턴 수만).

**B4. 평면 분리 원칙 (근본 대응) — "SQL 생성은 데이터 평면에서만"**
- 오류가 "SQL 생성 위치"에서 났다는 점은 **SQL 생성이 제어 평면 컨텍스트에 얹혀 돌았을** 가능성을 시사한다. SQL 생성·스키마 분석은 **반드시 FabriX(데이터 평면)** 에서 수행하고, 그 입력에 오케스트레이터 누적 히스토리가 섞이지 않도록 isolated input(`_make_isolated_input`)의 대용량 필드 초기화(`subagents.py:320-354`)가 제어 평면 경로에도 동일 적용되는지 점검·보강한다.
- **결과적으로** tool-calling이 꼭 필요한 "어떤 순서로 무엇을"(제어, 소용량)만 vLLM이, "자연어→SQL→데이터→응답"(대용량)은 FabriX가 담당하는 D-037 설계를 **런타임에서 실제로 강제**한다.

**B5. vLLM 서버 기동 파라미터 (서버 측 — §3.7 B8에서 상술)**
- **이것이 1차 병목**이다. 코드 측 트리밍(B1~B4)은 서버 `max_model_len`이 충분할 때만 의미가 있다. 현재 `max_model_len=4096`은 tool-calling 오케스트레이터 구동에 절대적으로 부족하다 → §3.7 참조. 코드 대응(B1~B4)과 **병행** 필요(둘 중 하나로 부족).

### 3.5 [B6] 제어 평면 예산을 환경변수로 조정 — 모델 교체 대응

현재 오케스트레이터 모델은 **Qwen3.5-9B**(`config.py:76` 기본값과 일치)이나, **추후 더 큰 모델로 교체 가능성**이 있다. B1~B4의 상한값을 코드에 **하드코딩하지 않고** `OrchestratorConfig`(env_prefix `ORCHESTRATOR_`, `config.py:65-85`)에 노브로 노출하여, 모델 교체 시 `.env`만 바꾸면 예산이 확장되도록 한다.

> **서버 ↔ 클라이언트 정합성(필수)**: 클라이언트 `ORCHESTRATOR_MAX_INPUT_TOKENS`는 **서버 `max_model_len` − 출력 토큰 여유** 이하여야 한다. 둘 중 작은 값이 실제 한계다. 따라서 B6 기본값은 §3.7에서 정한 서버 `max_model_len`에 맞춰 잡는다.
> **(2026-06-25 갱신)** 인프라에서 서버를 **`max_model_len=16384`, `gpu_memory_utilization=0.85`** 로 상향 진행 중이다(B8). 따라서 아래 표 기본값은 **서버 `max_model_len=16384` 기준**으로 확정한다(이전 32768 가정값은 무효). 입력 예산 = 16384 − 출력 여유(~4000) = **12000**.

**(1) 단일 주노브 + 파생 예산 (one-knob scaling)**
모델 컨텍스트 창은 `ORCHESTRATOR_MAX_INPUT_TOKENS` 하나로 표현하고, 나머지 하위 예산은 이 값의 **비율로 파생**한다. → 큰 모델로 바꿀 때 주노브 하나만 올리면 전체가 스케일.

| 신규 설정 (env) | 기본값 | 의미 | 모델 교체 시 |
|-----------------|--------|------|-------------|
| `ORCHESTRATOR_MAX_INPUT_TOKENS` | `12000` | 제어 평면 입력 토큰 **안전 상한**(서버 `max_model_len=16384` − 출력 여유 ~4K). 초과 예상 시 트리밍/요약/강등(B2) 트리거 | 서버 max_model_len 상향과 **함께** 올림 |
| `ORCHESTRATOR_CONTEXT_BUDGET_RATIO` | `0.8` | 상한 대비 트리밍 시작 임계 비율(80% 도달 시 오래된 쌍 요약) | 보통 유지 |
| `ORCHESTRATOR_MAX_TOOL_RESULT_TOKENS` | `2000` | 제어 평면으로 반환하는 **도구 결과 1건** 요약 상한(B1). 원본은 collector 보관 | 큰 모델이면 상향 가능 |
| `ORCHESTRATOR_MAX_HISTORY_TURNS` | `6` | 제어 평면에 유지할 멀티턴 압축 맥락 턴 수(B3, 데이터 평면 `MAX_HISTORY_TURNS=10`과 **별도**) | 큰 모델이면 상향 |
| `ORCHESTRATOR_ENABLE_THINKING` | `false` | Qwen 계열 no-think(추론 비활성). 추론 모델 교체 시 `true` (B7) | 추론형 대형 모델이면 `true` |
| `ORCHESTRATOR_MAX_REPLAN` | (기존 `AppConfig.max_replan=3` 재사용) | 재계획 반복 상한(누적 증가의 1차 방어) | 유지 |

- 미설정 시 기본값은 **현재 9B 모델 기준 안전값**. 하위 예산을 명시하지 않으면 `MAX_INPUT_TOKENS × RATIO`에서 파생(예: 결과 요약 상한 = 주노브의 일정 비율)하여 한 노브만으로도 일관 동작.

**(2) (선택) 자동 탐지 + env 오버라이드**
- `select_orchestration_backend`가 이미 호출하는 vLLM `/v1/models`(`deep_agent.py:24-46`) 응답에 `max_model_len`이 포함되면 이를 읽어 `MAX_INPUT_TOKENS` 기본을 **자동 보정**한다(모델 교체 시 무설정으로도 추종). **단 env가 설정되면 env 우선**(명시 오버라이드). 자동 탐지는 보조이며 실패해도 정적 기본값으로 안전 동작.

**(3) 적용 지점**
- 트리밍/요약(B2), 도구 결과 축소(B1), 멀티턴 압축 턴 수(B3)는 모두 위 설정을 읽어 동작. 하드코딩 상수(`context_resolver.MAX_HISTORY_TURNS` 등)는 데이터 평면용으로 유지하되, **제어 평면 전용 상한은 `OrchestratorConfig`에서만** 읽는다(평면 분리 일관성).
- 토큰 계측은 정확한 토크나이저 대신 **보수적 근사**(문자수/4 등)로 충분(상한 트리거용). 정밀 계측이 필요하면 `tiktoken` 가용 시에만 사용하고 폐쇄망 미반입 시 근사 폴백.

**(4) Known Mistakes 정합**: `list[str]` 아닌 단순 int/float 설정이므로 `.env` JSON 파싱 이슈 없음(2026-03-23). `os.getenv()` 대신 `OrchestratorConfig` 필드로 읽어 systemd EnvironmentFile 미설정 문제 회피(2026-06-10).

### 3.6 [B7] Qwen 계열 vLLM no-think(추론 비활성) 모드 적용

Qwen3.5 등 **하이브리드 추론 모델**은 기본적으로 `<think>...</think>` 추론 블록을 생성한다. 제어 평면(오케스트레이터)은 **tool-calling 제어**만 담당하므로 추론 토큰은 (1) **출력/입력 토큰을 불필요하게 키워 95K 한계 압박**(B1~B6과 직접 충돌)하고 (2) tool_call JSON 앞에 think 텍스트가 섞여 **파싱 불안정**을 유발할 수 있다. → 제어 평면 vLLM 호출 시 **no-think를 기본 적용**한다.

**(1) 적용 방식 — `ChatOpenAI(model_kwargs.extra_body)`**
vLLM(OpenAI 호환)은 `extra_body.chat_template_kwargs.enable_thinking`로 Qwen think 토글을 제어한다. 현재 생성부 `_create_orchestrator_vllm`(`src/llm.py:132-138`)를 다음과 같이 보강한다.

```python
model_kwargs: dict = {}
if config.orchestrator.enable_thinking:        # 신규 노브 (아래 (2))
    model_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": True}}
else:
    model_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

return ChatOpenAI(
    base_url=config.orchestrator.base_url,
    api_key=config.orchestrator.api_key or "EMPTY",
    model=config.orchestrator.model,
    temperature=0.0,
    timeout=config.orchestrator.timeout,
    model_kwargs=model_kwargs,                  # ← 추가
)
```

- `extra_body`는 ChatOpenAI가 OpenAI 호환 요청 바디에 그대로 실어 보내므로 vLLM이 chat template에 전달한다. (OpenAI 본가에는 없는 vLLM 확장 필드 → 반드시 `model_kwargs.extra_body` 경로 사용)

**(2) 환경변수 노브 — 모델별 토글**
`OrchestratorConfig`(env `ORCHESTRATOR_`)에 추가:

| 신규 설정 (env) | 기본값 | 의미 |
|-----------------|--------|------|
| `ORCHESTRATOR_ENABLE_THINKING` | `false` | 제어 평면 추론 모드. **Qwen 계열은 false(no-think) 권장**. 추론이 유리한 큰 모델로 교체 시 `true`로 전환 |

- 기본 `false`(no-think) — 현재 Qwen3.5-9B 기준 토큰 절약·파싱 안정 우선.
- **비-Qwen / non-think 미지원 모델 안전성**: `enable_thinking`을 인식하지 못하는 모델·서버는 해당 키를 **무시**하는 것이 일반적이나, 일부 vLLM 빌드에서 미지원 chat_template_kwargs가 오류를 낼 수 있다. → 모델 계열 가드를 둔다: `config.orchestrator.model`이 Qwen 계열일 때만 `extra_body`를 부착(예: 소문자 비교 `"qwen" in model.lower()`)하고, 그 외 모델은 `extra_body` 미부착으로 호환성 보존. (env로 강제 부착이 필요하면 `ORCHESTRATOR_ENABLE_THINKING` 명시값 우선)
- 워커(FabriX/KBGenAIChat)는 OpenAI 호환이 아니며 think 개념이 없으므로 **무관**(데이터 평면 미적용).

**(3) 검증**: no-think 적용 시 오케스트레이터 응답에 `<think>` 블록이 없고 tool_call이 정상 왕복하는지 단위 테스트(요청 바디에 `extra_body.chat_template_kwargs.enable_thinking=false` 포함 검증 — 실제 vLLM 호출 없이 `ChatOpenAI` 구성 인자 단언).

### 3.7 [B8] vLLM 서버 기동 파라미터 — **1차 병목 해소**

> 코드(레포) 변경이 아니라 **운영 배포 설정**이지만, 본 계획의 토큰 문제에서 **가장 결정적인 단일 요인**이므로 함께 문서화한다.

**(1) 현재 설정과 문제**

| 파라미터 | 현재 | 문제 |
|----------|------|------|
| `--gpu-memory-utilization` | `0.5` (L40S 46068MiB 중 ~22.5GiB) | Qwen3.5-9B BF16 가중치 ~18GiB 점유 후 **KV 캐시 여유 ~4.5GiB뿐** → 큰 컨텍스트 불가 |
| `--max-model-len` | `4096` | tool-calling 오케스트레이터에 **절대 부족**. system prompt + 도구 스키마(subagent 5종)만으로도 근접/초과. DeepAgent 제어 평면 구동 불가 |

**(2) 수치 모순 — 먼저 실측 확인**
- 오류는 `Input tokens must be <=95232`인데 `max_model_len=4096`이면 한계가 ~4096이어야 한다. → **오류 시점 설정이 현재와 달랐거나(95232 ≈ Qwen3.5-9B 네이티브 컨텍스트), `GptOssAdapter` 래퍼가 vLLM과 별개의 자체 입력 한계를 가진다.**
- **실측 절차**: `GET {ORCHESTRATOR_BASE_URL}/models`(이미 `deep_agent.vllm_healthy`가 치는 엔드포인트) 응답의 `max_model_len`으로 **현재 실제 값을 먼저 확인**한 뒤 (3) 적용.

**(3) 권장 변경 (L40S 46GB 기준)**

| 파라미터 | 권장값 | 근거 |
|----------|--------|------|
| `--gpu-memory-utilization` | **0.85** (확정, 진행 중) | 가중치 18GiB 제외 후 KV 여유 확보 |
| `--max-model-len` | **16384** (확정, 진행 중; 대형 모델 교체 시 32768로 상향) | 보수적 시작값. 클라이언트 입력 예산 12000(B6)과 정합 |
| `--kv-cache-dtype` | (선택) **fp8** | KV 절반 → 동일 메모리로 컨텍스트 2배 또는 동시성 확보 |
| `--max-num-seqs` | 환경 동시성에 맞게(예: 4~8) | 동시 시퀀스 수 × max_model_len 만큼 KV 필요 — 과대 설정 시 OOM |
| `--enable-chunked-prefill` | 활성 권장 | 긴 프롬프트 prefill 메모리 피크 완화 |

- 개략 메모리 계산(확인용, 정밀치 아님): 0.9×46 ≈ 41GiB − 18GiB(가중치) ≈ 23GiB KV. 32768 토큰 × ~150KB ≈ 4.9GiB/시퀀스 → 동시 ~4시퀀스까지 안전.
- **Qwen3.5-9B 네이티브 컨텍스트가 32K 미만이면** YaRN 등 rope-scaling 없이는 그 이상 못 늘린다 → 모델 카드의 실제 최대 컨텍스트 확인 후 `max_model_len` 상한 결정(네이티브 초과 설정 시 품질 저하/오류).

**(4) 정합성 — 서버 ↔ 클라이언트(B6) ↔ no-think(B7)**
- 서버 `max_model_len` ≥ 클라이언트 `ORCHESTRATOR_MAX_INPUT_TOKENS` + 출력 여유. 셋을 **한 세트로 조정**.
- **(2026-06-25 확정값)** 인프라 상향 진행: 서버 `max_model_len=16384`, `gpu_memory_utilization=0.85` → 클라이언트 입력 예산 `12000`(B6 기본값), 출력 여유 ~4000. 차후 대형 모델 교체 시 서버 `max_model_len`(예: 32768)·클라이언트 `MAX_INPUT_TOKENS`(예: 24000)를 함께 상향.
- no-think(B7)는 추론 토큰을 제거해 **같은 max_model_len에서 더 많은 실작업 토큰**을 확보 → B8과 상승작용.
- 대형 모델 교체 시: 서버 `max_model_len`↑ + 클라이언트 `ORCHESTRATOR_MAX_INPUT_TOKENS`↑(B6) + 필요시 `ENABLE_THINKING`↑(B7)를 함께 변경.

**(5) 산출물**: 서버 기동 파라미터 권장값과 실측 절차를 `docs/`(예: 배포 운영 노트) 또는 기동 스크립트 주석에 기록. 레포 코드 변경 없음.

---

## 4. 변경 파일 (예상)

| 파일 | 변경 |
|------|------|
| `src/nodes/context_resolver.py` | `previous_db_ids`/`previous_entities`/`previous_location` 추출 추가, `_trim_messages` 이중 기준 |
| `src/config.py` | `OrchestratorConfig`에 `max_input_tokens`/`context_budget_ratio`/`max_tool_result_tokens`/`max_history_turns` 노브 추가 (B6), `enable_thinking` 추가 (B7) |
| `src/llm.py` | `_create_orchestrator_vllm`에 `model_kwargs.extra_body.chat_template_kwargs.enable_thinking` 부착 (B7, Qwen no-think) |
| `src/state.py` | `conversation_context` 신규 키 문서화(타입 주석) |
| `src/orchestration/intent_planner.py` | `_llm_decompose`에 `conversation_context` 주입, `process_query` 분류 반영 |
| `src/prompts/intent_planner.py` | 지시어 해소·DB 승계 규칙 + `process_query` agent + 예시 5 추가 |
| `src/orchestration/subagents.py` | `process_query` subagent 등록, DB 승계 우선순위, 도구 반환 페이로드 압축(B1) |
| `src/routing/db_registry.py` 또는 `semantic_router.py` / `field_mapper.py` | `previous_db_ids` 우선 후보 반영(M2) |
| `src/orchestration/deep_agent.py` / `deepagents_tools.py` | 제어 평면 도구 반환 축소·요약, 컨텍스트 예산(B1·B2·B4) |
| `src/alarm/infrastructure/polestar_process_api.py` | (재사용, 변경 최소) process_query subagent에서 호출 |
| `docs/02_decision.md` | D-041, D-042 추가 (D-039/D-040은 변경 이력 표에 선점됨) |
| `tests/test_multiturn/*`, `tests/test_orchestration/*` | 후속 턴 승계·process_query 라우팅·제어 평면 토큰 상한 회귀 |

---

## 5. 단계별 작업

| 단계 | 내용 | 의존 | 검증 |
|------|------|------|------|
| 1 | `context_resolver` 엔티티/DB/위치 보존 (M3) | — | 단위: 후속 턴 context에 previous_db_ids/entities 포함 |
| 2 | `intent_planner` 맥락 주입 + 프롬프트 규칙 (M1) | 1 | 단위: "해당 서버" → sub_query에 김포·### 보존 |
| 3 | data_query DB 승계 우선순위 (M2) | 1 | 단위: 새 위치 없을 때 previous_db_ids 우선 |
| 4 | `process_query` agent+subagent, 프로세스 API 연결 (M4) | 2,3 | 통합: "현재 프로세스" → API 경로, SQL0204N 미발생 |
| 5 | 제어 평면 컨텍스트 예산·도구 반환 축소 (M5/B1~B4) + `OrchestratorConfig` 노브 (B6) + Qwen no-think (B7) | — | 통합: 누적 시 상한 내 유지, `.env` 노브로 상한 조정, no-think 요청 바디 검증 |
| 0 (선행) | **vLLM 서버 기동 파라미터 실측·상향 (B8)** — `/v1/models`로 현재 `max_model_len` 확인 → `gpu_memory_utilization=0.85~0.9`, `max_model_len=16384~32768` | — | 서버 응답 `max_model_len` 상향 확인, OOM 없이 기동, 클라이언트 예산과 정합 |
| 6 | `arch_check`, 회귀(폴백 경로 무변경), D-041/D-042 기록 | 1-5 | `python scripts/arch_check.py --ci` |

---

## 6. 기록할 의사결정 (docs/02_decision.md 반영 — 기록 완료)

> 번호 정정: 당초 D-039/D-040 의도 → 두 번호가 변경 이력 표에 선점되어 **D-041/D-042로 기록**(`docs/02_decision.md` §D-041/§D-042).

- **D-041. 멀티턴 컨텍스트 전파 및 엔티티 보존**: 후속 턴 분해(`intent_planner`)에 직전 턴의 해소된 DB/위치/대상 엔티티를 압축 주입하고, data_query가 이전 DB를 승계한다. D-013(멀티턴) 확장이며 충돌 없음.
- **D-042. 제어 평면 컨텍스트 예산 · 평면 분리 강제 · Qwen no-think**: tool-calling 제어 평면(vLLM Qwen3.5-9B, 소용량)에는 계획 신호만, 대용량 작업(SQL/데이터/응답)은 FabriX 평면으로 강제. 원시 도구 결과는 collector에만 보관하고 오케스트레이터 컨텍스트에는 요약본만 노출. **상한값은 하드코딩하지 않고 `OrchestratorConfig`(env `ORCHESTRATOR_*`) 노브로 노출하여 모델 교체(예: 9B→대형) 시 `.env`만으로 예산을 확장**한다. 또한 **Qwen 계열 vLLM 모델은 no-think(`enable_thinking=false`)를 기본 적용**하여 추론 토큰으로 인한 한계 압박·tool_call 파싱 불안정을 회피하며, 이 역시 `ORCHESTRATOR_ENABLE_THINKING` 노브로 모델 교체 시 전환 가능하다. D-037 운영 보강이며 충돌 없음.

> 본 계획은 기존 결정(D-013·D-037)을 **확장·보강**하며 되돌리지 않는다. 착수 전 team-lead가 `docs/02_decision.md`를 재확인하고 충돌 없음을 확정한다.

---

## 7. 리스크 / 주의

| 리스크 | 대응 |
|--------|------|
| 엔티티 보존이 과도하게 누적되어 다시 토큰 증가 | 식별 키 컬럼·상한 N행만, 압축 1블록(Known Mistakes 2026-06-11) |
| "현재 vs 이력" 프로세스 오분류 | 프롬프트에 시간성 키워드(현재/실시간 ↔ 이력/추세) 구분 규칙 + 예시 |
| previous_db_id 승계가 사용자의 새 의도(다른 위치)를 덮어씀 | 이번 턴 명시 신호를 항상 최우선, 승계는 신호 부재 시에만. UI에 승계 사실 노출 |
| 제어 평면 요약이 재계획 판단에 필요한 정보를 누락 | 식별 키·건수·실패 사유는 보존, 원시 값만 축소 |
| 프로세스 API 무인증·http (Plan 47-1) | 기존 graceful degradation·짧은 타임아웃 재사용, 마스킹 필수 |
| 서버 `max_model_len`↑ 시 KV 메모리 부족(OOM) | `gpu_memory_utilization`·`max_num_seqs`와 함께 산정, fp8 KV 옵션, 보수적 시작값(16384) 후 단계 상향 |
| Qwen3.5-9B 네이티브 컨텍스트가 목표보다 작음 | 모델 카드 실제 최대 컨텍스트 확인, 초과 시 rope-scaling(YaRN) 필요성 별도 판단 |
| 서버/클라이언트 한계 불일치로 여전히 초과 | 서버 `max_model_len` ≥ 클라이언트 `MAX_INPUT_TOKENS`+출력여유 정합을 배포 체크리스트로 고정 |
