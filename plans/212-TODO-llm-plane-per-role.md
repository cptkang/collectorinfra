# 212. 용도별 LLM 평면 분리 — 질의 응답·시맨틱 라우팅·의도 계획·최종 응답·딥 에이전트의 모델·엔드포인트를 각자 지정

> **작성일**: 2026-11-04 · **v1.0** · 원 plans/208·D-359 → 2026-11-04 main 병합 시 plans/212·D-365로 재부여(main이 208·D-359를 먼저 사용)
> **상태**: **TODO** — 코드 0 · §7 사용자 확인(Q1~Q7) 대기 · 권고안으로 진행 시 W0부터 착수
> **계획 시작 SHA**: `2b43f658`
> **회귀 대상 파일**: (구현 후 기입)
> **성격**: 기능 추가 · **기본 off = 현행 비트 동일**(역할 설정을 하나도 넣지 않으면 모든 호출이 지금과 같은 LLM 인스턴스를 쓴다). 구조 리팩터와 섞지 않는다(D-337 ⑥).
> **관련 결정**: **D-365**(이 계획의 정본 · 예약 2026-11-04) · **D-174**(LLM 평면 운영 정책 — 「라우터 평면 이동」은 별도 결정으로 예고됨 · 이 계획이 그 기반) · D-037(오케스트레이터 평면) · D-222·D-340(평면별 비과금 집합) · D-194(FabriX 하이퍼파라미터 프로파일 `purpose`) · D-169(라우터 구조화 출력) · D-004(LLM 전용 시맨틱 라우팅) · D-251(사다리 기준 2단) · D-127·D-240(과금 승인·로컬 MLX)
> **패키지**: 본체 `src/`(config·llm·graph 배선) · `scripts/scenario`·`scripts/bench`(과금 판정) · 문서·매뉴얼

---

## 0. 요약

지금 LLM은 **평면 2개**뿐이다 — 워커(`LLM_*` · `create_llm`)와 오케스트레이터(`ORCHESTRATOR_*` · `create_orchestrator_llm`, 딥 에이전트·noise_gate 트랙 B 전용). 질의 응답·시맨틱 라우팅·의도 계획·최종 응답 합성은 모두 **같은 워커 인스턴스 1개**를 쓴다(`src/graph.py:969`에서 1회 생성 후 `partial(llm=llm)`로 주입).

이 계획은 워커 평면 안에 **역할(role)** 을 두어, 역할마다 provider·엔드포인트·모델·키·타임아웃을 따로 지정하게 한다. **역할 설정이 비어 있으면 워커를 그대로 승계**한다(같은 객체 — 현행 비트 동일). 기존 provider 팩토리(`_create_fabrix`·`_create_vllm`·`_create_mlx`…)는 고치지 않고, **역할 값으로 덮어쓴 설정 사본을 기존 팩토리에 넘기는 방식**으로 provider별 처리(D-042 thinking 가드·D-060 SSL·D-222 스트림 청크 타임아웃·D-194 프로파일)를 전부 재사용한다.

---

## 1. 요구

사용자 지시(2026-11-04): *"질의 응답과 시멘틱라우팅, 딥에이전트 등의 LLM모델과 엔드포인트를 각자 다르게 하려고 한다."*

해석(§7 Q1에서 확정):

| 요구 표현 | 대응 역할 | 현행 |
|---|---|---|
| 질의 응답 | `worker`(기본 평면 — NL→SQL·결과 정리 등 나머지 전부) | `LLM_*` |
| 시멘틱 라우팅 | `router` — 3단 `semantic_router` | 워커 공유 |
| (「등」 — 2단 기준 경로의 라우팅 상당) | `planner` — 2단 `intent_planner`·`replanner` · 3단 계획 루프 `plan`·`replan` | 워커 공유 |
| (「등」 — 사용자에게 보이는 최종 응답) | `answer` — `purpose="answer"` 호출 지점 전부 | 워커 공유(FabriX만 하이퍼파라미터 프로파일 분리 · D-194) |
| 딥 에이전트 | `orchestrator` | **이미 분리됨**(`ORCHESTRATOR_*`) — 키 이름·동작 그대로 두고 기동 로그·과금 판정·문서의 평면 표에만 편입 |

**왜 `planner`가 필요한가**: 기준 경로는 2단 `intent_orchestration`이다(D-251). 3단 `semantic_router`는 비교 arm이라, 라우터만 분리하면 기준 경로에서는 아무것도 바뀌지 않는다. 2단의 대상 DB·도구 결정은 `intent_planner`가 한다.

---

## 2. 현황 실측 (2026-11-04)

### 2.1 팩토리

| 함수 | 평면 | provider | 비고 |
|---|---|---|---|
| `create_llm(config, *, provider_override, purpose)` (`src/llm.py:156`) | 워커 | `ollama`·`fabrix`·`gemini`·`mlx`·`vllm` | `purpose`는 FabriX 프로파일만 바꾼다 · `provider_override`는 테스트 전용(`worker_provider_override`) |
| `create_orchestrator_llm(config)` (`src/llm.py:197`) | 오케스트레이터 | `vllm`·`gemini`·`mlx` | 소비처: `src/orchestration/deep_agent.py:123` · `noise_gate/infrastructure/noise_signal_tools.py:271` |

### 2.2 워커 소비처 (`grep -rn "create_llm(" src noise_gate` · tests 제외 · 36곳)

| 묶음 | 지점 | 역할 후보 |
|---|---|---|
| 그래프 1회 생성·주입 | `src/graph.py:969` → `partial(llm=llm)` 30여 노드 | 노드별 배정(아래) |
| 라우팅 | `src/routing/semantic_router.py:155`(주입 없을 때 자체 생성) · `graph.py:680` 주입 | `router` |
| 계획 | `src/orchestration/intent_planner.py:666` · `replanner.py:115` · `graph.py:586·607·716·727` 주입 | `planner` |
| 최종 응답(`purpose="answer"`) | `general_inference.py:341` · `output_generator.py:540`(스트리밍 최종만) · `result_aggregator.py:2055` · `doc_query.py:138` · `portal_help.py:122` · `api/routes/doc_search.py:118` · `api/routes/result_viz.py:200` · `semantic/result_explainer.py`(주입 람다) | `answer` |
| 딥 에이전트 도구 워커 | `deep_agent.py:124`(`worker_llm or create_llm`) | `worker`(Q6) |
| 나머지(입력 해석·필드 매핑·스키마·SQL 생성·결과 정리·유사어·승인 분류·관리자 설명 생성·알람 라우트) | `input_parser`·`field_mapper`·`schema_analyzer`·`query_generator`·`result_organizer`·`multi_db_executor`·`cache_management`·`synonym_registrar`·`approval.py`·`schema_cache`·`alarm.py` 등 | `worker` |
| noise_gate | `alarm_analyzer.py:297` · `agentic_enricher.py:95` · `annotation_classifier.py:194` | `worker`(Q3 — 범위 밖 권고) |

> 정확한 배정은 W0에서 확정해 이 표를 갱신한다(특히 `agent_orchestrator.py:84`·`sequential_runner.py:64`·`result_aggregator.py:122`의 비-answer 합성 호출).

### 2.3 같이 움직여야 하는 곳

| 대상 | 현행 | 영향 |
|---|---|---|
| 과금 판정 `scripts/scenario/preflight.py` `external_planes(worker, orchestrator)` · 집합 `WORKER_INTERNAL_PROVIDERS`·`ORCHESTRATOR_INTERNAL_PROVIDERS` | 평면 2개만 본다 | **역할 provider를 빼면 D-127 우회가 생긴다**(예: `LLM_ROUTER_PROVIDER=gemini`가 승인 없이 실행). 소비처 3곳(시나리오 기본·`--run`·벤치 `approval_policy`) |
| MLX 실행 전 점검 `preflight.mlx_run_blockers` | 워커·오케스트레이터의 mlx 서버만 | 역할이 다른 mlx 포트를 쓰면 그 서버도 점검 |
| 설정 화면 `src/api/settings_catalog.py` | `RELOADABLE_KEYS`·시크릿 목록·필드 수 단언 테스트 | 신규 필드 등재 · 도움말 커버리지 게이트(`config/settings_help/*.yaml`) |
| 리로드 `POST /admin/settings/reload` | `build_graph` 재조립 후 원자 교체 | 역할 인스턴스도 재조립에 포함되면 리로드로 반영 가능 |
| 관리자 표시 `src/schema_cache/admin_base.py` `provider_info` | 워커 모델만 | 역할별 표시(W4) |
| 기동 로그 | 사다리 1줄(`record_ladder_resolution`) · 딥 에이전트 조립 1줄 | 평면 해석 1줄 추가 |

---

## 3. 기존 결정과의 충돌 검토

| 결정 | 내용 | 판정 |
|---|---|---|
| **D-174** | 운영 정책 「vLLM은 deep agents 전용 · 나머지는 FabriX」 · *"향후 시멘틱 라우팅 등을 vLLM으로 분리할 예정"* · ③ 라우터 평면 이동 결정이 서면 트랙 C·트랙 B 발효·`pydantic-ai` **3건을 한 번에 재평가** · ② `MIN_RELEVANCE_SCORE` 정산은 **모델·시점과 함께 기록**하고 라우터 평면이 바뀌면 재정산 | **충돌 아님(기반 마련)** — 이 계획은 기본 off 메커니즘이다. **운영에서 라우터를 실제로 옮기는 것은 별도 사용자 결정(Q5)** 이고, 그 결정이 서면 D-174 ②·③을 같은 자리에서 처리한다(§6 W6). D-174에 부기 1을 단다 |
| D-037 | 딥 에이전트 = 오케스트레이터(vLLM) + 워커(FabriX) | 유지 — 오케스트레이터 키·동작 무변경 |
| D-222 ③ · D-340 ④ | 과금 판정은 평면별 집합 · 정의 1곳(`preflight`) | **확장** — 역할 평면도 같은 집합으로 판정(워커 집합 적용). 정의 위치 1곳 유지 |
| D-194 | `purpose`는 FabriX 하이퍼파라미터만 고른다 | 유지 — `answer` 역할은 엔드포인트를, `purpose`는 프로파일을 고른다(직교). `answer` 역할이 fabrix면 `LLM_FABRIX_ANSWER_LLM_CONFIG`가 그대로 적용된다 |
| D-169 | 라우터 구조화 출력(instructor 모드는 클라이언트 클래스로 결정) | 유지 — 역할 provider가 바뀌면 모드도 클래스에 따라 바뀐다. W1에서 provider별 모드 매트릭스 테스트 |
| D-340 ③ | vllm 필수값 누락은 기동 실패 · 조용한 폴백 금지 | **동일 규칙 적용** — 역할 provider를 지정했는데 엔드포인트·모델이 없으면 `ValueError`(워커로 침묵 폴백하지 않는다) |
| D-251 | 기준 경로 2단 | `planner` 역할 신설의 근거 |

---

## 4. 설계

### 4.1 역할 해석 규칙

```
create_llm(config, role="router")
  └ role 설정에 provider가 없다          → 워커와 같은 인스턴스(그래프) / 같은 인자(직접 생성)  ← 현행 비트 동일
  └ role provider가 있다                  → config 사본의 llm 필드를 역할 값으로 덮어쓴 뒤 기존 팩토리 호출
        └ 덮어쓰지 않은 provider 고유 노브 → 워커 설정의 같은 provider 값 승계
        └ 필수값(엔드포인트·모델) 비면      → ValueError (그래프 조립에서 기동 실패)
```

- `purpose="answer"` 호출은 **`role="answer"`를 자동 적용**한다 — 호출부 8곳 무변경(Q2). 단 `output_generator`의 중간 합성(`purpose="deterministic"`)은 `worker` 그대로다(D-194 주석의 구분 유지).
- `worker_provider_override`(테스트 전용)는 워커에만 적용되고, 역할 미설정이면 역할도 그 워커를 승계한다(현행 deepagent 경로 전체 gemini 테스트 동작 유지).

### 4.2 설정 형식 (Q4)

역할별로 **공통 5필드**만 둔다. 이름은 워커 접두(`LLM_`) 아래로 묶는다 — `ROUTER_*`(라우터 신뢰도 설정 `RouterConfig`)와 섞이지 않게.

| 키 | 의미 | 덮어쓰는 워커 필드(provider별) |
|---|---|---|
| `LLM_<ROLE>_PROVIDER` | `ollama`·`fabrix`·`gemini`·`mlx`·`vllm` · 빈 값 = 워커 승계 | `provider` |
| `LLM_<ROLE>_BASE_URL` | 엔드포인트 | `ollama_base_url`·`fabrix_base_url`·`mlx_base_url`·`vllm_base_url` (gemini 무시) |
| `LLM_<ROLE>_MODEL` | 모델 | `model`(ollama)·`fabrix_chat_model`·`mlx_model`·`vllm_model`·`gemini_model` |
| `LLM_<ROLE>_API_KEY` | 키(`.encenv` · 시크릿) | `ollama_api_key`·`fabrix_api_key`·`vllm_api_key`·`gemini_api_key` |
| `LLM_<ROLE>_TIMEOUT` | 초 · 0 = 승계 | `ollama_timeout`·`fabrix_total_timeout`·`mlx_timeout`·`vllm_timeout` |

`<ROLE>` = `ROUTER`·`PLANNER`·`ANSWER`. 대응표(provider → 필드명)는 `src/llm.py` 한 곳의 상수로 둔다. 설정 클래스는 기존 nested config 패턴(`Field(default_factory=...)`)을 따른다.

**provider 고유 노브는 승계**한다(FabriX `client_key`·`llm_config` · vllm `verify_ssl`·`enable_thinking` · mlx `max_tokens`·`enable_thinking`). 역할마다 다르게 둘 필요가 확인되면 그때 필드를 더한다(Q4 대안).

### 4.3 배선

- `src/graph.py`: 워커 1개 생성 뒤 `router`·`planner`·`answer` 역할 인스턴스를 해석한다. 역할 미설정이면 **워커 객체 그대로**(`is` 동일). 노드 주입은 §2.2 배정표대로 바꾼다 — `semantic_router`←router · `intent_planner`·`replanner`·`plan`·`replan`←planner · 나머지 무변경.
- 노드 내부 생성 경로(`llm is None`일 때): `create_llm(app_config, role=...)`로 바꾼다(라우터·계획 노드 4곳 정도).
- `answer`는 대부분 내부 생성 경로라 §4.1 자동 적용으로 끝난다. 주입 경로의 최종 합성(`result_aggregator` `synthesize=True` 등)은 W0 실측 후 배정한다.
- 딥 에이전트: 오케스트레이터 무변경 · 도구 워커는 `worker`(Q6).

### 4.4 과금 판정 (D-127 우회 차단 — 필수)

- `external_planes`를 평면→provider 매핑을 받게 일반화한다. 역할 평면은 워커 비과금 집합으로 판정한다. 하나라도 집합 밖이면 외부.
- `--show-env`·`--preflight` 출력에 역할 평면을 행으로 더한다(미설정 역할은 「워커 승계」로 표시).
- `mlx_run_blockers`: 해석된 평면들 중 mlx인 것의 **서로 다른 base_url마다** 도달·1토큰 생성 점검.
- 시그니처 변경 소비처(시나리오·벤치) 전수 grep 갱신(D-222 주의 ⑤ 선례).

### 4.5 관측·보안

- 기동 로그 1줄: `LLM 평면: worker=fabrix(<model>) router=vllm(<model>@<host>) planner=승계 answer=승계 orchestrator=vllm(<model>)` — 키 미출력, 호스트만.
- `LLM_<ROLE>_API_KEY`는 설정 화면 시크릿 목록·로그 비밀값 가림(D-339) 대상에 등재. `.encenv.example`에만 예시.
- LLM 호출 계측(있다면)의 provider/model 라벨에 역할을 더할지는 W0 실측 후 결정한다(없으면 추가하지 않는다).

---

## 5. 범위

| 포함 | 범위 밖 |
|---|---|
| 역할 `router`·`planner`·`answer` 설정·해석·배선 · 과금 판정 확장 · 기동 로그 · 설정 화면·도움말·리로드 · 문서·관리자 매뉴얼 | `ORCHESTRATOR_*` 키 개명·통합 · noise_gate 알람 분석 역할(Q3) · `sre_agent` LLM(별도 venv·자체 설정) · `mcp_server`·`apm_gateway`(LLM 없음) · 역할별 provider 고유 노브 · 역할별 폴백 체인(장애 시 다른 엔드포인트로 전환) · 운영 `.env` 실제 전환(Q5) · D-174 ③ 3건 재평가 자체(Q5 후 별도) |

---

## 6. Wave

| # | 내용 | 담당 · 대상 | verify |
|---|---|---|---|
| **W0** | 실측 — ① §2.2 배정표 확정(36곳 전수 · 주입 경로/내부 생성 경로 구분) ② provider별 구조화 출력 모드(`src/clients/instructor_adapter.py`) ③ LLM 호출 계측의 라벨 유무 ④ `settings_catalog` 필드 수·enum·시크릿을 단언하는 테스트 위치 ⑤ 역할 키가 `LLMConfig`(`extra="ignore"`)에 새어 드는지 `inspect`/실 로드로 확인 | implementer · 이 계획서 §2 갱신 | 배정표 행 수 = grep 결과 수 · 미배정 0 |
| **W1** | 설정·팩토리 — 역할 설정 클래스 · `create_llm(..., role=)` · provider→필드 대응표 · `purpose="answer"` 자동 적용 · 필수값 누락 `ValueError` · 기동 로그 | implementer · `src/config/llm.py`(+`__init__.py` nested 등재) · `src/llm.py` · `tests/test_llm_roles.py`(신규) | ① 역할 미설정 시 5 provider 모두 생성 인자가 현행과 동일 ② 역할별 요청이 자기 base_url·model로 간다(httpx MockTransport · vllm·mlx·fabrix OpenAI 호환) ③ 누락 시 `ValueError` ④ 로그에 키 없음 ⑤ `regress.py --no-tests` · **`[웨이브 회귀 필요]`(`src/config/`) → W1 끝 모듈 단위 회귀** |
| **W2** | 배선 — `graph.py` 역할 인스턴스 해석·주입 · 라우터·계획 노드 내부 생성 경로 `role=` | implementer · `src/graph.py` · `src/routing/semantic_router.py` · `src/orchestration/intent_planner.py`·`replanner.py` · 3단 계획 루프 노드 · `tests/test_graph_llm_roles.py`(신규) | ① 역할 미설정 `build_graph` → 모든 노드 주입 객체가 워커와 `is` 동일 ② `LLM_ROUTER_*` 설정 → `semantic_router`만 다른 객체 · 2단 `LLM_PLANNER_*` → `intent_planner`·`replanner`만 ③ `prompt_render_diff.py` 무변경 ④ `arch_check`·`overfit_check` 0 |
| **W3** | 과금·하네스 — `external_planes` 일반화 · `--show-env`/`--preflight` 역할 행 · `mlx_run_blockers` 서버별 · 벤치 `approval_policy` | implementer · `scripts/scenario/preflight.py`·`__main__.py` · `scripts/bench/__main__.py` · 해당 테스트 | ① 워커 mlx + `LLM_ROUTER_PROVIDER=gemini` → 외부 판정(승인 요구) ② 역할 미설정 시 판정·출력이 현행과 동일 ③ mlx 서버 2개(포트 다름) → 점검 2회 |
| **W4** | 설정 화면·문서 — `settings_catalog`(`RELOADABLE_KEYS`·시크릿) · `config/settings_help/llm.yaml` · `admin_base.provider_info` 역할 표시 · `.env.example`·`.encenv.example` · `docs/03_setup_guide.md` §7(역할 평면 절) · `docs/21_orchestration_ladder.md` §3(평면 표) · `CLAUDE.md` Tech Stack 1줄 · **관리자 매뉴얼**(D-255 — 설정 항목 신설) | implementer · 위 파일 · `tests/test_settings_catalog.py` · `tests/test_manual` | ① 도움말 커버리지 게이트 통과 ② 리로드 후 역할 변경이 새 그래프에 반영(가짜 LLM) ③ `pytest tests/test_manual` 통과 |
| **W5** | 독립 검증·회귀·기록 — verifier 리뷰(침묵 폴백·과금 우회·시크릿 노출 중점) · `regress.py --base 2b43f658 --files …` · **MLX 대표 1회**(아래) · D-365 본문·색인·CHANGELOG · D-174 부기 1 · INDEX | verifier · team-lead | 모듈 단위 회귀 통과 · 범위 줄 보고에 그대로 옮김 · MLX 1회에서 기동 로그의 역할 평면과 실제 요청 포트가 일치 |
| **W6**(폐쇄망 · Q5 후) | 운영 전환 — 폐쇄망 `.env`에 역할 지정(예: router·planner → 사내 vLLM) · 대표 질의 응답·지연 비교 · **D-174 ② `MIN_RELEVANCE_SCORE` 재정산(모델·시점 기록)** · **D-174 ③ 3건 일괄 재평가** | 사용자·내부망 | 응답시간 목표(단순 <10s · 복합 <30s) 유지 · 라우팅 골드셋(`eval_routing`) 비열화 |

**MLX 검증(W5 · 최소 1회)**: 두 평면 모두 루프백 MLX일 때만. `scripts/mlx_server.sh`로 워커 서버(8080)를 띄우고, 같은 캐시 모델로 두 번째 서버를 다른 포트(예: 8081)에 띄워 `LLM_ROUTER_*`·`LLM_PLANNER_*`를 8081로 지정 → 대표 질의 1건(2단)과 3단 비교 arm 1건. 메모리(M1 Max 32GB · 9B 4bit 2개)는 W0에서 여유 확인 후 진행하고, 부족하면 가짜 OpenAI 서버 2개로 대체한다. 지연 수치는 결론에 쓰지 않는다(D-174 부기).

---

## 7. 사용자 확인 (권고안 표시)

| # | 질문 | 권고 | 대안 |
|---|---|---|---|
| **Q1** | 분리할 역할 범위 | **`router`·`planner`·`answer` 3종 + 기존 `orchestrator`** — 기준 경로(2단)에 효과가 있으려면 `planner`가 필요하다(§1) | (b) `router` 1개에 3단 라우터와 2단 계획 노드를 묶는다 — 키는 줄지만 2단·3단 비교 arm을 다른 모델로 대조할 수 없다 · (c) 요청 문구 그대로 `router`만 |
| **Q2** | `answer` 역할을 `purpose="answer"`에 자동 연결 | **예** — 호출부 8곳 무변경, 최종 응답 정의가 D-194와 한 곳으로 맞는다 | 호출부마다 `role="answer"` 명시(변경 8곳+) |
| **Q3** | noise_gate 알람 분석(`alarm_analyzer`·`agentic_enricher`·`annotation_classifier`)도 역할로 분리 | **이번 범위 밖** — 요청에 없고, 필요 시 역할 1행 추가로 끝나는 구조다 | `alarm` 역할 추가(W2에 noise_gate 배선 3곳) |
| **Q4** | 역할 설정 형식 | **공통 5필드 + provider 고유 노브 승계**(§4.2) | 역할마다 provider 전체 필드 복제(필드 수 3배 · 화면·도움말 부담) · 이름 붙인 엔드포인트 묶음을 JSON으로 정의하고 역할이 이름 참조(키가 JSON 안에 들어가 `.encenv` 시크릿 처리와 맞지 않음) |
| **Q5** | 운영(폐쇄망)에서 실제로 라우터·계획을 vLLM 등으로 옮길지 | **이 계획은 메커니즘까지** · 운영 전환은 W5 뒤 사용자가 정한다. 정하면 D-174 정책 개정 + ②·③ 처리(W6) | 계획 안에서 운영 전환까지 확정 |
| **Q6** | 딥 에이전트 도구 실행 워커(`deep_agent.py:124`)도 별도 역할로 | **아니오 — `worker` 그대로**(도구 내부가 SQL 생성·결과 정리 등 질의 응답 경로 자체) | `deep_worker` 역할 추가 |
| **Q7** | 역할 설정 변경의 반영 시점 | **설정 화면 리로드로 반영**(리로드가 그래프를 재조립하므로) | 재시작 필요(보수적) |

---

## 8. 리스크

| 리스크 | 대응 |
|---|---|
| 역할 provider를 과금 판정이 못 보면 Gemini가 승인 없이 불린다(D-127) | W3을 W1·W2와 같은 계획 안에서 끝낸다 · 판정 정의 1곳 유지 · 테스트 ① |
| 역할 엔드포인트 누락이 워커로 조용히 폴백 | 금지 — `ValueError` 기동 실패(D-340 ③ 규칙) |
| 구조화 출력 모드가 역할 provider에 따라 달라져 라우터 출력 계약(D-169)이 깨짐 | W1 provider별 모드 테스트 · 실패 시 기존 폴백 경로(구조화 출력 `None` → 기존 파싱) 확인 |
| 서로 다른 모델이 같은 프롬프트에 다르게 반응(라우팅 정확도·관련도 점수 스케일) | 운영 전환(W6) 전 `eval_routing` 비교 · `MIN_RELEVANCE_SCORE` 재정산(D-174 ②) |
| 엔드포인트가 늘어 지연·가용성 지점이 늘어남 | 역할별 타임아웃 · 기동 로그로 판독 · 응답시간 목표 비교(W6) |
| 단일 MLX 서버에 다른 모델을 번갈아 요청하면 재적재(D-222 ⑦) | 로컬은 포트를 나눠 띄우거나 같은 모델로 경로만 검증 |
| `settings_catalog` 필드 수 단언 등 상수 단언 테스트 | 변경 시 저장소 전체 grep으로 갱신 |

---

## 9. 완료 기준

1. 역할 설정 0개 상태에서 그래프의 모든 노드가 현행과 같은 LLM 객체를 받는다(테스트로 고정).
2. 역할마다 지정한 엔드포인트·모델로만 요청이 간다(가짜 서버 테스트 + MLX 1회).
3. 역할 provider가 과금 평면이면 하네스가 승인을 요구한다.
4. 기동 로그 1줄로 역할별 평면을 판독할 수 있고, 키는 어디에도 출력되지 않는다.
5. 설정 화면·도움말·`.env.example`·`docs/03`·`docs/21`·관리자 매뉴얼이 새 키를 설명한다.
6. D-365 등재 · D-174 부기 1 · INDEX 갱신.
