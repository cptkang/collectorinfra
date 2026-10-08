# 148. FabriX 도구 호출 프록시 — OpenAI Chat Completions(+`tools`)로 받아 FabriX KBGenAI로 변환·호출하고 응답을 OpenAI 형식(`tool_calls`)으로 되돌리는 독립 서버

> **작성일**: 2026-10-08 · **v5.2**(1단계 PoC 진행 가이드 부록 D · 판정 기록 양식 부록 C · v5: 도구 루프 흐름·역할 분담 §3.0 · 결과 수신 후 행동 규칙 · 턴별 입력 길이 기록 · v4: few-shot 형식 예시 · v3: 판정 테스트를 내부망 FabriX 실행으로 재편 · v2: 1단계 PoC 신설 · `plans/128`과 분리)
> **상태**: **WIP — 1단계 내부망 1차 실행 완료(2026-10-08) · 판정 보류(S5 반영 = 러너 결함 · S6 = 기준 결함) → 재측정 R1 대기**(부록 C) · 이전: 1단계 개발 맥 완료(D.1~D.3 · 2026-10-08) — `fabrix_proxy/` PoC 서버·러너·탐침·런북 · 테스트 A·B 270 통과 · C 3 통과/1 skip(litellm 미설치) · 드라이런·런북 리허설 완주 · 1-2 길이 = 부록 C · 커밋 없음 · 이전: TODO — 계획만(코드 0) · 착수 = **1단계 PoC**(§5 — PoC 서버 + 테스트 A·B·C·D로 동작 판정 · **판정 테스트는 내부망 FabriX에서 실행**) · 판정(G-4) 통과 시에만 2단계 이후 · **G-4 합격선 확정**(사용자 2026-10-08 「권고대로」) · 내부망 FabriX 호출량 약 445회 순차 실행 허용(사용자 2026-10-08 「가능하다」) · 잔여 G-5(PoC 측정) · G-6 · G-7
> **사용자 확정(2026-10-08)**: *"148번 1단계에서 proxy서버를 poc 할 수 있는 테스트 코드를 만들어 동작여부를 테스트하는 단계를 넣어라. 148번 계획은 128번 계획과 다른 계획이다. 이게 성공되면 128번은 사용하지 않을 계획이다. 1단계에서 성공하면 deepagents 등에서 활용 예정이다."*
> - G-1 → **(b) 128과 별개의 독립 패키지**. 148 1단계가 성공하면 `plans/128`은 사용하지 않는다(128은 미착수 그대로 · 폐기 표기는 1단계 판정 때).
> - G-2 → **(a) 1단계 성공 후 deepagents 등(1단 · noise_gate 트랙 B · sre_agent 조사)에서 활용**.
> - 1단계 = **PoC 서버 + PoC 테스트 코드로 동작 여부를 판정**하는 단계(§5 1단계).
> - (추가 지시) *"테스트는 내부망 fabrix환경에서 진행할 예정이다. 계획을 업데이트하라."* → 1단계 판정 테스트는 **전부 내부망 FabriX에서 사용자가 실행**한다. 개발 맥은 코드·오프라인 테스트만(실 LLM 0 · MLX 대조 없음) · 내부망 무수정 실행(옵션화) · 런북 · 반출은 판정값만(§5 1단계 「실행 환경 원칙」).
>
> **요청 원문(2026-10-08)**: *"fabrix는 tools 호출이 안되고 있다. 이를 해결하기 위해 별도의 proxy api 서버를 두고 그 서버에 호출할때는 openai 표준 api로 호출하고 proxy서버에서 fabrix형식으로 변환하여 호출하고 응답이 오면 proxy 서버가 다시 openai형식의 api 응답으로 변환하여 리턴해 주는 방식으로 툴 호출을 제공하려고 한다. 이 요건에 대해 검토하고 계획파일을 작성하라."*
>
> **해석(추정 — 원문 기준)**
> - "tools 호출이 안 된다" = FabriX 운영 클라이언트 `KBGenAIChat`가 `tools`를 요청에 싣지 못하고 응답에 `tool_calls`도 없다는 사실(§1.1). 그래서 tool-calling이 필요한 소비자 3곳(deep agents 1단 · noise_gate 트랙 B · sre_agent HolmesGPT)이 FabriX로는 돌지 못하고 vLLM·Gemini에 묶여 있다(§1.2).
> - "proxy api 서버" = 본체 밖 **별도 프로세스**. 호출자는 OpenAI 규격만 알고, FabriX 규격·자격증명은 프록시만 안다.
> - "툴 호출을 제공" = 호출자가 보낸 `tools`에 대해 프록시가 **OpenAI 규격의 `tool_calls` 응답**을 돌려준다. 실제 도구 실행은 지금처럼 호출자가 한다(프록시는 도구를 실행하지 않는다).
>
> **실측 기준**: `main` HEAD `a95d8f5` + 미커밋 작업 트리(2026-10-08). 코드는 **읽기만** 했다(수정 0 · LLM 호출 0 · 서버 기동 0 · 과금 0). 이 작업 트리에는 `.env`·`.venv`·`sre_agent/.venv`가 없어 운영 설정값과 holmes 소스는 직접 보지 못했다 — 그 부분은 `plans/128` 실측 인용 또는 **실측 필요**로 표기한다.
> **근거 표기**: `파일:라인` = 현 작업 트리에서 직접 확인 · **추정** = 검증하지 않은 추론 · **실측 필요** = 1단계 PoC에서 확인 · **내부망 실측** = FabriX가 있는 내부망에서만 확인 가능(사용자 수행 — 1단계 판정 테스트 전부가 여기 해당).
> **관련 계획**: **`plans/128`**(LLM 게이트웨이 `llm_gateway/` — **별개 계획 · 148 성공 시 미사용 예정**(사용자 2026-10-08) · 설계 참고만: FabriX 어댑터 이식 범위 §1.1 · 소비자 배선 §2.13) · `plans/48`·`49`(deep agents 트랙 B) · `plans/50`(제어 평면 토큰) · `plans/52`(noise_gate 트랙 B) · `plans/66` §7-1(조사 LLM — FabriX 불가 확정) · `plans/79` E-3(instructor 모드) · `plans/100`(MLX)
> **관련 결정**: **D-037**(vLLM 오케스트레이터 · **KBGenAIChat 프롬프트 에뮬레이션 기각**) · **D-174**(평면 정책: vLLM = deep agents 전용 · 나머지 FabriX) · D-042(Qwen no-think) · D-060(SSL 검증 해제) · D-139·**D-274**(독립 패키지 · 자격증명은 게이트웨이에만) · D-155(PII 차단 진단) · D-194(`llmConfig` 프로파일) · D-198(FabriX 총 소요 상한) · D-268(마감 전파 · SDK 재시도 0) · D-222·D-127·D-240(과금 판정 · 외부 차단 · 실 LLM은 MLX) · D-120·D-213·D-229·D-230(sre_agent 조사 LLM) · D-225·D-251(사다리) · D-161(경로 폐기 실측) · D-255(매뉴얼 동반) · D-303(회귀)
> **번호 변경**: 146 → 148(2026-10-08 · 다른 작업 트리가 `plans/146`을 먼저 써서 사용자 결정으로 재번호 — 사용자 인용문 안의 자기 번호도 새 번호로 고쳤다)
> **계획 시작 SHA**: `a95d8f5fef9a91bea21adf41f4920ed307657d0b`(2026-10-08 1단계 착수)
> **회귀 대상 파일**: `fabrix_proxy/` 전체(신규 · 미추적) · `tests/test_fabrix_proxy_poc.py` · `plans/148-WIP-…` · `plans/INDEX.md` · `plans/INDEX-CHANGELOG.md` · `plans/128-TODO-…`(재번호 참조만) — W4 모듈 회귀 1회(2026-10-08): 본체 통과 193 · 실패 0 · 건너뜀 1 · arch·overfit 통과 · ruff E501 1건(팀 리드 편집) 교정 후 통과 · `범위: 모듈 단위 — 전체 미실행` · `fabrix_proxy` 자체 테스트는 regress 밖(`cd fabrix_proxy && python -m pytest`)
> **D-번호**: 예약하지 않는다. **1단계 판정 때** 확정값(G-1·G-2·G-4 결과 · 128 미사용 여부 · D-037·D-174 부기)을 묶어 1건으로 등재한다 — 등재 직전 `ls docs/decisions/ | tail -3`으로 최댓값을 확인한다. 판정 전에 등재하지 않는 이유: 128 미사용·D-174 개정이 모두 「성공하면」 조건부다.

---

## 0. 검토 결론

### 0.1 한 줄

**프록시는 FabriX에 도구 호출 능력을 만들어 주지 않는다. 프록시가 하는 일은 "프롬프트로 도구 호출을 흉내 내는 로직(에뮬레이션)"을 본체 밖 표준 인터페이스 뒤로 옮기는 것이다.** FabriX KBGenAI 요청에는 도구 필드가 없으므로(§1.1), 프록시는 도구 정의를 프롬프트에 넣고 모델의 텍스트 출력을 파싱해 `tool_calls`로 바꿔야 한다. 이 방식의 신뢰도는 **FabriX 뒤 모델의 지시 이행력에 달려 있고**, D-037이 바로 이 방식을 *"다중 tool call·긴 ReAct 루프에서 불안정"*으로 기각했다. 따라서 이 계획은 **1단계에서 PoC 서버와 PoC 테스트 코드로 동작·신뢰도를 먼저 판정(G-4)**하고, 통과해야만 정식화·소비자 연동으로 넘어간다.

### 0.2 그래도 할 만한 이유

| 이득 | 근거 |
|---|---|
| 소비자 3곳이 **코드 변경 없이** 붙는다 | 세 곳 모두 이미 OpenAI 호환 엔드포인트를 설정으로 받는다(§1.3) — 본체·noise_gate는 `ORCHESTRATOR_BASE_URL`, sre_agent는 `API_BASE` |
| 에뮬레이션 로직이 **한 곳**에 모인다 | 지금은 `FabriXAPIClient`의 few-shot 모사(`src/clients/fabrix_client.py:210-237` · 도구 1개만 · 결과를 user 턴으로 변환)가 본체 안에만 있고 sre_agent·deepagents는 쓸 수 없다 |
| FabriX 자격증명이 프록시에만 남는다 | D-274(`apm_gateway`) 전례와 같은 경계 |
| vLLM이 없거나 약한 배포에서 1단·트랙 B·조사를 FabriX로 돌릴 **선택지**가 생긴다 | D-174 ①은 정책이지 능력 제약이 아니다 — 백엔드 선택지가 하나 늘 뿐이다 |

### 0.3 먼저 확인할 것 — 더 싼 길이 있을 수 있다

1. **FabriX에 네이티브 tool-calling 엔드포인트가 있는가**(1단계 PoC 시나리오 F1 — 폐쇄망). `FabriXAPIClient`는 `/v1/chat/completions` 형식을 가정한 폴백이다(`src/clients/fabrix_client.py:1-4`). 내부망 FabriX 버전이 OpenAI 호환 엔드포인트를 열고 `tools`를 받는다면 **에뮬레이션이 필요 없고**, 프록시는 인증 헤더만 바꿔 주는 투명 전달이 된다(또는 프록시 자체가 불필요). 이 확인 없이 에뮬레이터를 만들면 안 된다.
2. **`plans/128`과의 관계 — 별개(사용자 확정)**. 128은 OpenAI 호환 서버 + FabriX 어댑터를 설계했지만 tools를 버린다(`plans/128` §2.3 표 `tools/tool_choice` 행 · §2.4 · G-9). 148은 128과 별개로 진행하고, **148 1단계가 성공하면 128은 사용하지 않는다** — 따라서 OpenAI 호환 FabriX 서버가 둘 생기는 일은 없다. 1단계가 실패하면 128은 지금 상태(미착수 TODO) 그대로 남는다.

### 0.4 기존 결정·계획과의 충돌 (CLAUDE.md 「충돌 시 사용자 문의」)

| 대상 | 충돌 내용 | 처리 |
|---|---|---|
| **D-037** 대안 기각 | *"KBGenAIChat 프롬프트 에뮬레이션(다중 tool call·긴 ReAct 루프에서 불안정)"* — 이 계획은 같은 기법을 프록시로 옮긴다 | **1단계 PoC로 재판정**(G-4). 통과하면 D-037에 부기, 미달이면 이 계획은 sre_agent 조사 등 짧은 루프로 범위를 좁히거나 중단 |
| **D-174** ① | *"vLLM은 deep agents를 활용할 때만"* — 오케스트레이터를 프록시(=FabriX)로 돌리면 deep agents가 FabriX 위에서 돈다 | 사용자 방향 확정(G-2 = deepagents 등 활용) — **1단계 성공 시** 「deep agents 오케스트레이터 = vLLM 또는 프록시(FabriX)」로 부기(G-3). 프록시는 선택지를 더할 뿐 vLLM 경로를 지우지 않는다 |
| `plans/128` | 같은 영역(OpenAI 호환 FabriX 서버)을 다른 계약(tools 버림)으로 설계 | **별개 계획 · 148 성공 시 128 미사용**(사용자 확정). 128 머리에 그 사실만 1줄 부기(이번 개정) · 판정 뒤 128 상태 갱신 |
| `docs/23` §2.2.1 · `plans/66` §7-1 | *"FabriX로 HolmesGPT 구동 불가 — 확정 사실"* | 프로토콜 사실은 그대로 맞다. 프록시 경유 시 「가능(에뮬레이션) — 신뢰도 실측 대상」으로 부기 |

---

## 1. 현행 실측

### 1.1 FabriX 클라이언트 두 종 — 어느 쪽도 도구를 전송하지 않는다

| 클라이언트 | 선택 조건 | 도구 처리 |
|---|---|---|
| `KBGenAIChat`(`src/clients/fabrix_kbgenai.py`) — **운영** | `fabrix_client_key` 있음(`src/llm.py:455`) | 요청 `{modelId, contents:[문자열], isStream, isRagOn, executeRag*, systemPrompt, llmConfig?}`(`:104-126`) — **도구 자리 없음**. `bind_tools`(`:377-382`)는 `tool_registry`에 저장만 하고 읽는 코드 0 · 응답은 `AIMessage(content=…)`뿐(`:153`·`:202`) · **role 소실**(`_convert_messages_to_prompts:73-84` — System 제외, 나머지 content만 문자열 배열로) |
| `FabriXAPIClient`(`src/clients/fabrix_client.py`) — 폴백 | `fabrix_client_key` 없음(`src/llm.py:474`) | `_build_payload`(`:120`)가 `tools`를 싣지 않는다. 대신 **마지막 user 턴에 도구 JSON + 응답 형식을 주입**(`:210-237`)하고 응답에서 `{"tool_name","arguments"}` JSON을 파싱(`:168-194`). 한계: assistant `tool_calls`는 **첫 1개만** 직렬화(`:84-90`) · `tool` 결과는 user 턴으로(`:92-98`) · 병렬 호출·`tool_choice`·스트림 미지원 |

KBGenAI 요청·응답 규약 표는 `docs/23_plan66_mvp_test_guide.md` §2.2.1에 있다. 운영 입력 한도는 **95,232 토큰**(`docs/18` 2026-08-21 항목 — 136,707tok 초과 사고).

**모르는 것(폐쇄망 실측 필요)**: ① `contents` 배열 원소가 어떤 대화 턴으로 해석되는지(user/assistant 교대인지 · 첫 원소의 역할) — 본체는 "System 다음 빈 AIMessage" 규약(`src/utils/llm_compat.py:16` · 삽입 지점 다수)으로 경험적으로 맞춰 왔다 ② FabriX 뒤 모델(`asset_id`)의 계열 — `LLAMA_JUNK_TOKENS`(`fabrix_kbgenai.py:34-39`)로 보아 Llama 3 계열 템플릿이 섞여 나온 적이 있다(**추정**) ③ 응답에 토큰 사용량이 오는지.

### 1.2 tool-calling 소비자

| 소비자 | 평면 · 진입 | 요구 | 현재 백엔드 | 프록시 대상 |
|---|---|---|---|---|
| deep agents 1단(`src/orchestration/deep_agent.py:123` `create_orchestrator_llm` → `create_deep_agent`) | 오케스트레이터 | 네이티브 `tools` · 다중 턴 ReAct · `write_todos`·`task` 등 내장 도구 · `ainvoke`(`:234`·`:383`) | vLLM / MLX / Gemini | **예** |
| noise_gate 트랙 B(`noise_gate/infrastructure/noise_signal_tools.py:246-247` `bind_tools` · 루프 `:283-297`) | 오케스트레이터 | 네이티브 `tools` · 호출 상한 5(`src/config.py:1006`) | 같음 | **예** |
| sre_agent HolmesGPT(`sre_agent/sre_agent/diagnosis.py:171-173` · litellm) | 조사 LLM | 매 호출 `tools`/`tool_choice` · 폴백 없음(`sre_agent/sre_agent/settings.py:66-69` 주석) · 장기 루프 | vLLM / Gemini | **예** |
| `column_deriver`(`src/nodes/column_deriver.py:278` `bind_tools`) | 워커 · 옵트인(`stepwise_derivation` 기본 off · `src/config.py:389`) | 네이티브 `tools` | FabriX(무효 — 도구가 안 실림) | 아니오(비범위 · §8) |
| instructor TOOLS 모드(`src/clients/instructor_adapter.py:41` · `:270`) | 워커 | 클래스명 `ChatOpenAI`일 때만 | FabriX는 MD_JSON | 아니오(비범위) |

**기준 경로(2단 `intent_orchestration`)는 tool-calling을 쓰지 않는다**(프롬프트+JSON — D-037 트랙 A). 즉 이 프록시의 효과는 1단·트랙 B·조사 세 곳에 한정된다.

### 1.3 소비자 배선 — 설정만으로 프록시를 가리킬 수 있다

- **본체 오케스트레이터**: `ORCHESTRATOR_PROVIDER=vllm`이면 `ChatOpenAI(base_url=ORCHESTRATOR_BASE_URL, api_key=…, model=…)`(`src/llm.py:219-297`). 가용성은 `GET {base_url}/models` 200(`deep_agent.py:26-50`) — **인증 헤더를 보내지 않는다**(`:45-47`).
  - 모델명에 `qwen`이 들어가면 `extra_body.chat_template_kwargs`가 붙는다(`src/llm.py:245-250`) → 프록시는 모르는 필드를 무시해야 한다.
  - `max_retries=0`(`:268`) · `timeout=config.orchestrator.timeout`.
- **noise_gate 트랙 B**: 같은 오케스트레이터 설정을 쓴다. provider가 `vllm`이면 `vllm_healthy`(`noise_signal_tools.py:56` · 인증 없음 · SSL 인자 없음)로 트랙 B를 고른다(`agentic_enricher.py:149-175`).
- **sre_agent**: `MODEL=openai/<별칭>` · `API_BASE=<프록시>/v1` · `API_KEY=<프록시 토큰>` · `INVESTIGATION_LLM_ENABLED=true`(D-230). served name이 litellm 표에 없으면 holmes가 매 요청 `max_tokens=64000`·컨텍스트 200000을 쓴다(`settings.py:73-85` 주석) → `OVERRIDE_MAX_CONTENT_SIZE`·`OVERRIDE_MAX_OUTPUT_TOKEN`을 **프로세스 환경변수로** 줘야 한다(D-213 · D-229 ③).
- **과금 판정**: 오케스트레이터 provider `vllm`은 비과금 평면이다(D-222). 프록시 뒤가 사내 FabriX이므로 판정 결과는 맞다. 다만 로그·사다리 기록에 "vLLM"으로 찍히는 표기 문제가 남는다(G-7).

---

## 2. 대안 비교

| 안 | 내용 | 장점 | 단점 | 판단 |
|---|---|---|---|---|
| **A. 프록시(요청안)** | 별도 서버가 OpenAI ↔ KBGenAI 변환 + 도구 에뮬레이션 | 소비자 3곳 코드 0 · 로직 1곳 · 자격증명 격리 | 신뢰도가 모델 지시 이행력에 의존 · 홉 1개 추가 · 스트림은 도구 턴에서 버퍼링 | **채택(1단계 판정 조건부)** |
| B. 본체 클라이언트 에뮬레이션 | `KBGenAIChat.bind_tools`를 실제로 구현(LangChain `bind_tools` 계약 충족) | 홉 없음 | sre_agent(litellm)는 못 씀 · deepagents가 같은 프로세스 클라이언트를 써야 함 · 에뮬레이션 로직이 본체 클라이언트에 갇힌다 | 기각 |
| C. FabriX 네이티브 엔드포인트 | 내부망 FabriX가 OpenAI 호환 `tools`를 받으면 그대로 사용 | 에뮬레이션 0 · 신뢰도 최고 | 존재 여부 미확인 | **1단계 F1에서 먼저 확인** — 있으면 A는 인증 변환만 하는 얇은 프록시로 축소 |
| D. 현행 유지 | tool-calling은 vLLM(Qwen3.5-9B)·트랙 A로 | 변경 0 | FabriX 단독 배포에서 1단·트랙 B·조사 불가 | 대조군 |
| E. LangChain `LLMToolEmulator`류 | deepagents 미들웨어 수준 에뮬레이션 | — | 도구 *실행*을 LLM으로 흉내 내는 도구라 목적이 다르다(**추정** — 설치본 실측 필요) | 기각 |

---

## 3. 설계

### 3.0 도구 루프 전체 흐름과 역할 분담 (v5)

원 질문 하나에 HTTP 요청이 여러 번 오간다(OpenAI 표준). **루프는 호출자가 돌리고, 프록시는 매 요청을 1회 변환·중계만 한다.**

```
호출자(deepagents · LangChain · holmes)                 프록시(상태 없음)                FabriX
 ① messages=[user 원 질문] + tools ──────────────────▶ §3.3 변환 ───────────────────▶ 모델
                                    ◀─ tool_calls[A(...)] ◀─ §3.4 파싱·검증 ◀────────── "<tool_call>…"
 ② 호출자가 도구 A를 직접 실행(DB · MCP · 파일 도구)
 ③ messages=[user 원 질문, assistant(tool_calls A), tool(A 결과)] + tools ─▶ 변환 ─────▶ 모델
                                    ◀─ tool_calls[B(...)] → ②로 돌아감(필요한 만큼)
 ④ …                                ◀─ 평문 최종 답(finish_reason: stop) — 원 질문 + 누적 도구 결과를 조합
```

| 책임 | 호출자 | 프록시 |
|---|---|---|
| 루프 진행 · 종료 판단 · 반복 상한 | ✅ (트랙 B 5회 · deepagents `recursion_limit` 25) | — |
| 도구 실행 | ✅ | — (§8 비범위) |
| 원 질문·이전 호출·도구 결과 보존 | ✅ 매 요청에 전체 이력을 다시 보냄 | — (상태 없음 · `tool_call_id`만 발급) |
| 이력을 모델이 읽는 형태로 바꿈 | — | ✅ §3.3 (`<tool_call>` · `<tool_response name id>`) |
| 「다음 도구 호출 / 최종 답」 유도 | — | ✅ 규약 문구(§3.3) + few-shot 패턴 ③(§3.3.1) |
| 모델 출력 → `tool_calls` 또는 평문 | — | ✅ §3.4 |

- **프록시가 루프를 돌리지 않는 이유**: 도구 실체가 호출자 프로세스 안에 있다. 프록시가 루프를 돌리면 도구를 프록시가 가져야 하고, 응답이 OpenAI 표준에서 벗어난다. 그러면 「소비자는 설정만 바꿔 붙는다」(§3.9)가 깨진다.
- **루프가 길어질수록 입력이 커진다** — 매 요청에 누적 도구 결과가 실린다. 1단계는 턴별 입력 길이를 기록만 한다(§5 D 러너). 한도 처리(`context_length_exceeded`)는 2단계 §3.6이다(위험 R-2).

### 3.1 위치와 형태 (G-1 확정 = 독립 패키지)

- 최상위 패키지 **`fabrix_proxy/`** 를 새로 만든다. `plans/128`(`llm_gateway/`)과 **별개**다 — 128의 코드·설정 이름을 쓰지 않는다.
- `apm_gateway` 전례: 자체 `pyproject.toml` · 자체 cwd · **루트 venv 공유** · 2단 중첩(`fabrix_proxy/fabrix_proxy/`) · 독립 프로세스 · 자체 `tests/`·`scripts/`·`testdata/`.
- 경계: `src`·`noise_gate`·`sre_agent`와 **양방향 import 0**(경계 테스트 `fabrix_proxy/tests/test_boundary.py` — `apm_gateway` 전례). FabriX 호출 코드는 `src/clients/fabrix_kbgenai.py`를 **이식**한다(2단계에서 골든으로 페이로드 동치 고정).
- 서버: FastAPI + uvicorn(루트 venv에 이미 있음 — `pyproject.toml:30-31`). 기본 바인드 `127.0.0.1:9095`(저장소 내 사용 포트 9096·9098·9099와 겹치지 않음 — grep 확인).

### 3.2 API 계약 — OpenAI Chat Completions 부분집합

| 엔드포인트 | 인증 | 동작 |
|---|---|---|
| `POST /v1/chat/completions` | `Authorization: Bearer <토큰>` 필수 | 아래 변환 · `stream` 지원 |
| `GET /v1/models` | **없음** — 본체·noise_gate 가용성 판정이 헤더 없이 부른다(§1.3) | 별칭 목록 + 메타(`backend: fabrix` · `capabilities: {tools: emulated, stream: true}`). 자격증명·업스트림 URL은 싣지 않는다 |
| `GET /health` | 없음 | 프로세스 생존만(업스트림 호출 0) |

- **받는 필드**: `model`(별칭) · `messages`(`system`·`user`·`assistant`(+`tool_calls`)·`tool`(+`tool_call_id`)) · `tools`(`type: function`만) · `tool_choice`(`none`·`auto`·`required`·`{"type":"function","function":{"name"}}`) · `parallel_tool_calls` · `stream` · `temperature`·`top_p`(→ `llmConfig`, D-194 규약 필드만).
- **무시하는 필드**(DEBUG 로그 1줄): `max_tokens`·`max_completion_tokens`(KBGenAI `llmConfig`에 확인된 필드가 아니다 — `src/llm.py:46-47`) · `extra_body`·`chat_template_kwargs` · `stream_options` · `user` · 그 밖 모르는 필드. 모르는 필드로 400을 내지 않는다(소비자 SDK가 버전마다 필드를 더한다).
- **오류는 OpenAI 오류 봉투**(`{"error":{"message","type","code"}}`)로 낸다 — SDK가 예외 계열을 그대로 매핑한다.

  | 상황 | HTTP | `code` |
  |---|---:|---|
  | 토큰 없음·불일치 | 401 | `invalid_api_key` |
  | 모르는 별칭 · 형식 오류 · `function` 외 도구 타입 | 400 | `invalid_request` |
  | 입력 추정 토큰 > 별칭 한도(§3.6) | 400 | `context_length_exceeded` — holmes·LangChain이 아는 코드라 압축·절단 경로로 간다(**실측 필요** — 1단계 C 시나리오) |
  | FabriX PII 필터 차단(D-155 판정 재사용) | 400 | `content_filter` — 진단 덤프는 프록시 호스트 `logs/pii_block/`에만 |
  | 도구 호출 파싱·검증 재시도 소진(§3.4) | 502 | `tool_call_invalid` |
  | FabriX `status != SUCCESS` · HTTP 오류 | 502 | `upstream_error` |
  | 총 소요 상한 초과(§3.6) | 504 | `upstream_timeout` |

### 3.3 요청 변환 — OpenAI messages → KBGenAI 페이로드

```
system 메시지들 ──────────────┐
tools + tool_choice ─────────┼─▶ systemPrompt = [원 system] + [도구 규약 블록] + [few-shot 형식 예시 블록]
                             │
user / assistant / tool ─────┴─▶ contents = 턴 문자열 배열 (1단계 F2 규칙)
                                   assistant(tool_calls) → <tool_call>{…}</tool_call> 블록으로 직렬화
                                   tool(결과)           → <tool_response name id>…</tool_response>를 user 턴에
```

- **도구 규약 블록**(`tools`가 있고 `tool_choice != none`일 때만): 도구마다 `name` · `description` · `parameters`(JSON Schema — `title`·`$defs` 등 장식 제거 후 압축 직렬화) + 출력 형식 규칙.
  - 형식: 도구를 부를 때는 `<tool_call>{"name": "...", "arguments": {...}}</tool_call>` 블록만 출력한다. 여러 개를 부를 수 있으면(`parallel_tool_calls != false`) 블록을 여러 개 쓴다. 도구가 필요 없으면 평문으로 답한다.
  - **결과 수신 후 행동(v5)**: `<tool_response>`를 받으면 원 질문에 비추어 판단한다. 정보가 더 필요하면 다음 도구를 부르고, 충분하면 받은 결과를 근거로 원 질문에 평문으로 최종 답한다. 이미 결과를 받은 호출(같은 이름·같은 인자)은 다시 부르지 않는다.
  - Hermes/Qwen 계열이 학습한 표기와 같아 오픈 모델에 유리하다(**추정** — 1단계에서 FabriX 모델로 재측정). 규약 문구는 한 곳(`fabrix_proxy/fabrix_proxy/tool_protocol.py`)의 상수이며 영어/한국어 선택은 1단계 ⑤b 측정으로 정한다.
  - `tool_choice=required` → "반드시 하나 이상 호출" · 이름 지정 → "반드시 `<name>`을 호출".
- **few-shot 형식 예시 블록**(§3.3.1) — 규약 문구만으로는 형식을 강제하기 어렵다. 예시가 있어야 FabriX 모델이 `tool_calls`로 바꿀 수 있는 형식으로 답한다.
- **이력 직렬화는 출력 형식·few-shot과 같은 표기**를 쓴다. 이전 assistant 턴의 `tool_calls`는 같은 `<tool_call>` 블록으로 다시 들어간다. 이력은 2번째 턴부터 추가 예시 역할을 하지만 **few-shot을 대신하지 않는다** — 첫 턴에는 이력이 없다.
- **`contents` 배열 규칙은 1단계 F2 실측으로 확정한다(G-5).** 후보:
  - (a) 턴 교대 유지 — 연속 같은 역할 턴은 합치고, 본체 KBGenAI 규약(첫 원소 처리)을 그대로 따른다.
  - (b) 전체 대화를 역할 표지가 붙은 단일 문자열 1개로.
  - 측정 기준: 같은 3턴 도구 대화 대본에서 모델이 마지막 `tool_response`를 인식하고 다음 행동을 고르는 비율.
- `tool_call_id`는 프록시가 `call_<uuid4 앞 24자>`로 발급한다. 호출자가 이력으로 되돌려 보내므로 **프록시는 상태를 갖지 않는다**.

#### 3.3.1 few-shot 형식 예시 (v4 — 사용자 지적 반영)

**왜 필요한가.**
- FabriX 모델은 tool-calling 학습 여부를 모른다(§1.1 ②). 지시문만으로 형식을 강제하는 방식은 약하다.
- 이 저장소의 경험도 같다. Known Mistakes는 *"프롬프트 강제가 few-shot 예시와 경쟁해 반복 실패"*를 기록했다(`docs/34` §11). 모델은 지시문보다 예시를 따른다는 뜻이다. v3는 이 항목을 few-shot을 넣지 않는 근거로 잘못 읽었다.
- 이력에 기댈 수도 없다. 첫 턴에는 이력이 없다 — S1~S4 전부, 그리고 모든 루프의 첫 호출이 예시 0개로 나간다.
- 현행 `FabriXAPIClient`는 응답 형식 템플릿 한 줄만 주입한다(`src/clients/fabrix_client.py:229-235` — 예시 없는 zero-shot).

**예시 패턴 — 네 가지 모두 넣는다.** 빠지는 패턴이 있으면 그 행동이 약해진다.

| # | 패턴 | 막는 실패 |
|---|---|---|
| ① | 질문 → 도구 1개 호출 블록만 | 형식 위반 · 블록 밖 군더더기 |
| ② | 질문 → 블록 2개(병렬) — `parallel_tool_calls=false`면 넣지 않는다 | 병렬 누락 |
| ③ | `<tool_response>` 수신 → **다음 도구 호출** 또는 **최종 평문 답** | 같은 호출 반복 · 루프 미종료(S5) |
| ④ | 도구가 필요 없는 질문 → 평문 답(블록 없음) | 오탐(S4) |

**예시 도구는 실제 요청 도구와 겹치지 않는 가상 도구로 둔다(정적 예시 — 기본값).**
- 이 저장소에서 예시가 가르친 이름과 값을 모델이 그대로 베낀 사고가 있었다. Known Mistakes 2026-10-06(카탈로그에 없는 `tps`를 예시가 가르침 · MLX가 무효 지표명을 냄)이다.
- 실제 도구 이름에 자리표시 값을 넣은 동적 예시는 베낀 값이 **스키마 검증을 통과해 조용히 틀린다**. 문자열 자리표시가 대표적이다.
- 가상 도구 이름을 베끼면 파서가 `name ∉ tools`로 **결정적으로 잡는다**. 교정 재질의로 가고, 그래도 실패하면 502다.
- 그래서 기본은 정적 예시이고, 동적 예시는 비교군으로만 잰다(아래 측정).
- 가상 도구는 업무와 무관한 중립 이름이다(예: `example_lookup_item` · `example_get_status`). 폴스타·제니퍼·ITAM 용어를 쓰지 않는다.

**한 출처로 렌더한다 — 표기 표류 금지.**
- 예시는 문자열로 쓰지 않는다. 구조화 데이터(메시지 목록 + 기대 `tool_calls`)로 두고, **이력 직렬화와 같은 함수**로 렌더한다. 기본 예시는 패키지 데이터 파일 `fabrix_proxy/fabrix_proxy/fewshot_default.json`이다. 내부망에서는 `--fewshot-file`로 바꿀 수 있다(코드 재반입 없음).
- 그러면 few-shot · 이력 · 파서가 같은 표기를 공유하고, 한쪽만 바뀌는 일이 생기지 않는다.
- 결정적 가드(1단계 테스트 A)는 세 가지다.
  - 렌더된 예시를 파서에 넣으면 기대 `tool_calls`가 그대로 나온다(왕복).
  - 예시 도구 이름 ∩ 요청 도구 이름 = ∅. 겹치면 예시 쪽 이름에 접미사를 붙인다.
  - 예시 블록에 업무 도메인 어휘가 없다.

**배치.**
- 기본은 `systemPrompt` 안 「형식 예시」 절이다. `contents` 역할 해석(F2)과 무관하게 동작한다.
- 비교군은 `contents` 앞에 가짜 대화 턴으로 끼우는 방식이다. 실제 대화와 같은 모양이라 효과가 클 수 있으나 F2 결과에 의존한다.
- 둘 다 실행 옵션 `--fewshot-placement system|contents`로 고른다.

**비용.**
- 예시 블록은 매 호출 고정으로 붙는다. 길이는 1단계 1-2에서 규약 블록과 함께 잰다.
- 장기 루프에서는 이력이 커지므로, 예시는 패턴 4개를 짧게 유지한다(패턴당 1회).

**측정(1단계 ⑤a).**
- `--fewshot none|static|dynamic`을 S1·S4·S5로 비교한다.
- 지표는 형식 유효율 · 도구 선택 정답률 · 오탐률 · 루프 완주율 · **예시 도구 오호출 수**다.
- `none`이 대조군이다. few-shot의 효과를 숫자로 확인한 뒤 본 측정 설정을 정한다.

### 3.4 응답 변환 — 텍스트 → `tool_calls` (결정적 파서 + 1회 교정)

1. FabriX 응답 텍스트에서 junk 토큰 제거(이식한 `remove_llm_junk`).
2. `<tool_call>…</tool_call>` 블록을 모두 추출한다. 블록이 없으면 보조 형식 2종만 인식한다: 응답 전체가 ```` ```json ```` 펜스 하나이고 내용이 `{"name","arguments"}` 또는 구형 `{"tool_name","arguments"}`(`FabriXAPIClient` 형식) 객체일 때. 그 밖은 평문 답으로 본다.
3. 블록마다 검증: JSON 파싱 → `name ∈ tools` → `arguments`가 해당 `parameters` 스키마를 만족(`jsonschema` — 루트 venv 설치 여부 **실측 필요**, 미설치면 `required`·최상위 타입만 검사하는 축소 검증).
4. 결과 판정

   | 상황 | 처리 |
   |---|---|
   | 유효 블록 ≥1 | `choices[0].message = {role: assistant, content: <블록 밖 텍스트 또는 null>, tool_calls: […]}` · `finish_reason: tool_calls`. `parallel_tool_calls=false`인데 2개 이상이면 교정 대상 |
   | 블록 없음 · `tool_choice ∈ {none, auto}` | 평문 답 · `finish_reason: stop` |
   | 블록 없음 · `required`/이름 지정 | 교정 대상 |
   | 블록이 있으나 JSON·이름·인자 오류 | 교정 대상 |

5. **교정 재질의**: 원 대화 + 모델 원출력(assistant) + 오류 사유 1문단(user)으로 **1회**(`TOOL_REPAIR_MAX=1`, 별칭 설정) 다시 부른다. 그래도 실패하면 **502 `tool_call_invalid`** — 메시지에 사유(파싱 실패/모르는 도구/누락 인자 이름)를 싣고, 원출력은 프록시 로그에 남기지 않는다(§3.7 우선 — 2026-10-08 1단계 구현 정리: PoC는 `FABRIX_PROXY_POC_MODE`일 때만 응답 `fabrix_proxy_diag.raw_heads`(시도별 앞 500자)로 러너에 넘기고, 러너가 내부망 `failures/`에만 저장한다). **절대 평문으로 강등해 성공처럼 돌려주지 않는다**(Known Mistakes "침묵 폴백 금지" — 에이전트 루프가 깨진 출력을 최종 답으로 받는다).
6. 응답 헤더: `X-Proxy-Backend: fabrix` · `X-Proxy-Tool-Emulation: parsed|repaired|none`. `usage`는 FabriX가 주면 옮기고, 주지 않으면 생략한다(1단계 F5 · 생략 시 소비자 동작은 1단계 C 시나리오에서 확인).

### 3.5 스트리밍

- `stream=true` + 도구 없음(또는 `tool_choice=none`): FabriX SSE(`data:` 라인 · `null` 라인 · `STATUS/SYNC/FINISH` 건너뜀 — `fabrix_kbgenai.py:310-358` 이식)를 `chat.completion.chunk`의 `delta.content`로 **중계**한다.
- `stream=true` + 도구 있음: 끝까지 받아야 도구 호출 여부를 판정할 수 있으므로 **내부적으로 모아서**(§3.4) 판정한 뒤 합성 청크를 낸다 — 역할 청크 → `delta.tool_calls`(인자 전체를 한 조각에) 또는 `delta.content` → 종료 청크 → `data: [DONE]`.
  - 사용자 체감 손실은 작다: 오케스트레이터 턴은 사용자에게 토큰 스트림으로 나가지 않는다(최종 답은 워커 평면 `USER_RESPONSE_TAG` — `src/llm.py:33-36`).
  - 소비자 SDK의 청크 간격 상한에 걸리지 않게, 모으는 동안 SSE 주석 하트비트(`: keep-alive`)를 보낸다(**실측 필요** — 소비자가 주석 라인을 무시하는지 2단계).

### 3.6 마감·한도

- **총 소요 상한**: 호출 1건(교정 재질의 포함) 벽시계 상한 `FABRIX_TOTAL_TIMEOUT`(D-198 동치 · 기본 300s). 초과 시 업스트림 연결을 끊고 504. 소비자 쪽 마감(D-268)은 소비자 HTTP 타임아웃이 끊는다 — 소비자가 끊으면 프록시도 업스트림 호출을 취소한다(요청 연결 끊김 감지).
- **입력 한도**: 별칭별 `max_input_tokens`(기본 95,232 — 운영 실측 한도). 도구 규약 블록을 포함한 추정 토큰(문자 수 기반 보수 추정 — 계수는 1단계 F3 실측)이 넘으면 FabriX를 부르지 않고 400 `context_length_exceeded`. **프록시는 이력을 임의로 자르지 않는다**(무엇을 버릴지는 소비자 책임 — deepagents 요약·holmes 압축).
- 업스트림 재시도 0(D-268과 같은 이유 — 재시도는 소비자 루프가 마감을 보며 한다). 교정 재질의 1회는 재시도가 아니라 프로토콜 단계다.

### 3.7 보안·관측

- FabriX 자격증명(`FABRIX_BASE_URL` · `FABRIX_API_KEY` · `FABRIX_CLIENT_KEY` · `asset_id`)은 **프록시 `.env`/`.encenv`에만**(D-274 원칙). 소비자에는 프록시 토큰만.
- 프록시 토큰 없이 기동 금지. 비교는 상수 시간.
- 바인드 기본 `127.0.0.1`. 다른 호스트에서 부르려면 G-6.
- FabriX TLS 검증 해제(`verify=False`)는 현행 동작 이식이다 — 설정값 `FABRIX_VERIFY_SSL`(기본 `false` = 현행 동치)로 드러낸다.
- **로그에 프롬프트·응답 본문을 싣지 않는다**(PII 덤프 제외). 감사 1줄: 시각 · request id · 별칭 · stream · 도구 수 · 에뮬레이션 결과(`parsed|repaired|none|invalid`) · 상태 · 소요 · 입출력 문자 수.
- 에뮬레이션 품질 계측: 별칭별 누적 카운터(`parsed`·`repaired`·`invalid`)를 `/v1/models` 메타가 아니라 **`GET /metrics/emulation`(인증 필요)** 으로 노출 — 1단계 판정 이후 운영 감시 근거.

### 3.8 패키지 구조

```
fabrix_proxy/
  pyproject.toml            # 루트 venv 공유 · 의존: fastapi · uvicorn · httpx · pydantic · pydantic-settings (· jsonschema)
  .env.example              # FABRIX_PROXY_TOKEN · FABRIX_* · 별칭 설정 (인라인 주석 금지)
  fabrix_proxy/
    __main__.py             # uvicorn 기동
    config.py               # pydantic-settings (list/dict는 JSON)
    app.py                  # 라우트 3종 + 인증 + 오류 봉투
    fabrix_client.py        # KBGenAI 호출 이식(1단계: 비스트림 · 2단계: SSE·총상한·PII 진단)
    convert.py              # OpenAI messages ↔ KBGenAI 페이로드 (§3.3)
    tool_protocol.py        # 규약 블록 · few-shot 예시 렌더(이력 직렬화와 같은 함수) · 파서 · 검증 · 교정 메시지 (§3.3·§3.3.1·§3.4)
    fewshot_default.json    # 정적 few-shot 예시 — 가상 도구 · 패턴 ①~④ (§3.3.1)
    sse.py                  # 청크 합성 (§3.5)
  tests/                    # 1단계 A·B 묶음 · 경계 · (2단계) 골든
  testdata/scenarios/       # 1단계 시나리오 정의(합성 도구·합성 데이터만 — 실데이터 0)
  scripts/
    fake_kbgenai.py         # 가짜 KBGenAI 서버 — 대본 모드(오프라인 테스트 B·C · 러너 --dry-run)
    poc_run.py              # 1단계 러너 — env-check(E0) · run(S1~S6·F5) · 반출물 생성 (내부망 실 FabriX)
    probe_fabrix.py         # 1단계 F1·F2 탐침(내부망 · FabriX 직접)
  POC_RUNBOOK.md            # 내부망 실행 순서 ①~⑦ · 옵션 · 반출물 · 실패 시 조치 (부록 D.4~D.6을 옮긴 것)
  POC_VERSION               # 반입 시점 git SHA 1줄 — 러너가 summary.md에 옮겨 적는다
```

### 3.9 소비자 측 변경 — 설정만 (G-7 = (a)일 때)

```dotenv
# 본체 .env — 오케스트레이터 평면을 프록시로 (noise_gate 트랙 B도 같이 따라간다)
ORCHESTRATOR_PROVIDER=vllm
ORCHESTRATOR_BASE_URL=http://127.0.0.1:9095/v1
ORCHESTRATOR_MODEL=fabrix-tools
# 본체 .encenv
ORCHESTRATOR_API_KEY=<FABRIX_PROXY_TOKEN과 같은 값>

# sre_agent .env — 조사 LLM을 프록시로 (OVERRIDE_*는 셸 환경변수로 — .env는 holmes가 못 읽는다)
MODEL=openai/fabrix-tools
API_BASE=http://127.0.0.1:9095/v1
INVESTIGATION_LLM_ENABLED=true
# sre_agent .encenv
API_KEY=<같은 토큰>
```

- 별칭 이름에 `qwen`을 넣지 않는다(§1.3 — `extra_body` 부착 조건). 넣어도 프록시가 무시하므로 동작 차이는 없다.
- **사다리 영향**: `ENABLE_DEEPAGENTS_PACKAGE=true`인 배포에서 프록시가 떠 있으면 1단이 확정된다(`/v1/models` 200). 롤백은 `ORCHESTRATOR_BASE_URL`을 vLLM으로 되돌리거나 프록시를 내리면 된다(2단으로 내려감 — `orchestrator_unavailable`).

---

## 4. 결정 게이트

| G | 질문 | 선택지 | 상태 | 근거 |
|---|---|---|---|---|
| **G-1** | `plans/128`과의 관계 | (a) 128의 첫 조각 (b) 별개 독립 패키지 | **확정 (b)** — 사용자 2026-10-08 · 148 성공 시 128 미사용 | 패키지 `fabrix_proxy/` |
| **G-2** | 목적·대상 소비자 | (a) deepagents 등(1단 · 트랙 B · 조사)에서 활용 (b) sre_agent 조사만 (c) 비교 arm | **확정 (a)** — 사용자 2026-10-08 · **1단계 성공 후** | 3단계 연동 순서: deepagents 1단 → noise_gate 트랙 B → sre_agent |
| **G-3** | D-174 ① 개정 | (a) 「deep agents 오케스트레이터 = vLLM **또는** 프록시(FabriX)」로 부기 (b) 유지 | G-2 (a)에 따라 **(a) — 1단계 성공 시 부기** | 성공 전에는 정책 불변 |
| **G-4** | 1단계 합격선 | §5 1단계 「판정」 표 | **확정** — 사용자 2026-10-08 「권고대로」(v5 판정표 그대로 · 도구 결과 반영률 포함) | 미달이면 D-037 기각 사유 재확인 → 범위 축소 또는 중단 |
| G-5 | `contents` 직렬화 | (a) 턴 교대 (b) 단일 전사본 | 1단계 F2 측정으로 확정 | 규약 미문서화 — 실측만이 근거 |
| G-6 | 배치 | (a) 본체와 같은 호스트 · 루프백 (b) 별도 호스트 | **(a)** 권고 | sre_agent는 본체 API와 같은 서버(`plans/66` §3.3) · (b)면 TLS·방화벽 방향표 추가 |
| G-7 | 본체 provider 표기 | (a) 설정만(`ORCHESTRATOR_PROVIDER=vllm` 재사용) (b) provider 값 `fabrix_proxy` 신설 | **(a)** 1차 권고 · (b)는 3단계에서 재검토 | 코드 0 · 과금 판정 결과도 맞다(§1.3). 표기 혼동은 프록시 기동 로그·응답 헤더로 판독 |
| G-8 | 워커 평면(`column_deriver` · instructor TOOLS) | (a) 비범위 (b) 포함 | **(a)** 권고 | D-174 ②(워커 = FabriX 직접) · `column_deriver`는 옵트인 off |

---

## 5. 작업 분해

> 회귀는 D-303을 따른다 — Wave 중간은 자기 테스트 + `regress.py --no-tests`, 단계 끝에 모듈 단위 1회. `fabrix_proxy` 자체 테스트는 루트 수집 밖이라 `cd fabrix_proxy && ../.venv/bin/python -m pytest`로 돈다.
> **2단계 이후는 1단계 판정이 「통과」일 때만 착수한다.**

```
1단계 PoC ──판정(G-4)──┬─ 통과 → 2단계 정식화 → 3단계 소비자 연동(deepagents 등) → 4단계 폐쇄망 운영 전환 → 5단계 문서
                      └─ 미달 → 규약 조정 후 재측정 1회 → 그래도 미달이면 중단(D-037 재확인 부기 · 128은 TODO 유지)
```

### 1단계 — PoC: 프록시 동작 여부를 테스트 코드로 판정

> **실제 진행 순서는 부록 D(1단계 PoC 진행 가이드)를 따른다** — 개발 맥 Wave · 반입 · 내부망 설정·실행·선택 규칙 · 반출 점검 · 판정 뒤 처리.

**목표.** 세 가지를 테스트 코드로 판정한다.
- ⓐ **기계적 동작**: OpenAI 표준 요청(`tools`) → 프록시 → KBGenAI 형식 변환·호출 → 응답 → OpenAI `tool_calls` 변환이 맞게 돈다.
- ⓑ **소비자 호환**: 실제 클라이언트(openai SDK · LangChain `ChatOpenAI` · deepagents)가 코드 변경 없이 그 응답을 받아 도구 루프를 돈다.
- ⓒ **실 FabriX 신뢰도**: FabriX 모델이 규약대로 도구 호출을 내는 비율이 합격선을 넘는다.

**PoC 서버 범위(최소).** 정식 기능은 2단계로 미룬다.

| 포함 | 2단계로 미룸 |
|---|---|
| `POST /v1/chat/completions` 비스트림 · **스트림(도구 턴 합성 포함)** — LangChain은 스트리밍 콜백이 붙으면 `ainvoke`도 스트림 API로 부른다(`BaseChatModel._should_stream` · langchain-core 1.4.7 실측 — 본체 SSE 경로의 `astream_events`가 이 조건을 만든다 · **추정**, 1-1 녹화로 확정) | 업스트림 SSE 중계(PoC는 업스트림을 비스트림으로 부르고 결과를 청크로 합성) |
| `GET /v1/models`(인증 없음) · `GET /health` · Bearer 인증 | 입력 한도 추정(§3.6) · 하트비트 · `/metrics/emulation` |
| §3.3 변환 · §3.4 파서·검증·교정 1회 · §3.2 오류 봉투(401·400·502·504) | PII 진단 덤프 이식(PoC는 차단 판정 → 400 `content_filter`만) · 감사 로그 정식화 |
| FabriX 비스트림 호출(`KBGenAIChat._agenerate` 이식 · 총 소요 상한) | 페이로드 골든 동치 테스트 |

**실행 환경 원칙(사용자 2026-10-08 — *"테스트는 내부망 fabrix환경에서 진행할 예정이다."*).**
- **동작 여부 판정 테스트는 전부 내부망 FabriX 환경에서 사용자가 실행한다.** 개발 맥에는 FabriX가 없으므로 개발 맥에서는 코드 작성과 가짜 업스트림 기반 오프라인 테스트(A·B·C)만 하고 **실 LLM 호출은 0건**이다(MLX 대조 측정도 하지 않는다).
- **내부망에서 코드를 고치지 않고 돌 수 있어야 한다.** 측정 중 고를 선택지는 전부 실행 옵션으로 연다 — `--contents-mode turns|transcript`(G-5) · `--protocol-lang en|ko` · `--protocol-file <경로>`(규약 문구 교체) · `--fewshot none|static|dynamic` · `--fewshot-placement system|contents` · `--fewshot-file <경로>`(예시 교체 — §3.3.1) · `--repair-max N` · `--passthrough`(F1이 네이티브 지원일 때 에뮬레이션 끄기).
- 판정은 반출한 요약으로 한다. **반출물은 판정값만**(아래 1-7) — 원출력·자격증명·업스트림 URL은 내부망에 남긴다.

**작업 순서.**

| # | 작업 | 산출 | 위치 |
|---|---|---|---|
| 1-1 | **소비자 요청 녹화** — 가짜 OpenAI 서버에 deepagents 미니 에이전트 · `ChatOpenAI.bind_tools(...).ainvoke/astream` · noise_gate 트랙 B 도구 바인딩 · (가능하면) litellm `openai/` 요청을 보내 바디를 저장. 필드 목록(`tool_choice` 값 · `parallel_tool_calls` · `stream` · `stream_options` · `max_tokens` 등) | `fabrix_proxy/testdata/consumer_requests/*.json` · §3.2 받는/무시 필드 확정 | 개발 맥(LLM 0) |
| 1-2 | **도구 규약 블록 비용** — deepagents 내장 도구 + 본체 도구 전체를 규약 블록으로 렌더한 길이 | 95,232 한도 대비 잔여 예산(수치) | 개발 맥 |
| 1-3 | **PoC 서버 구현**(위 「포함」) + `scripts/fake_kbgenai.py`(대본 모드) + 러너 `scripts/poc_run.py` + 런북 `fabrix_proxy/POC_RUNBOOK.md` | `fabrix_proxy/` | 개발 맥 |
| 1-4 | **테스트 A·B·C 작성·통과**(반입 전 조건) · 러너는 가짜 KBGenAI(대본)로 `--dry-run` 완주 확인 · 정적 게이트(`regress.py --no-tests --files …`) | pytest 결과 · 러너 드라이런 요약 | 개발 맥 |
| 1-5 | **반입 묶음 구성** — `fabrix_proxy/` 전체 · 루트 `tests/test_fabrix_proxy_poc.py` · `POC_RUNBOOK.md` · `.env.example`. 자격증명은 넣지 않는다(내부망에서 `fabrix_proxy/.encenv`에 기입) | 반입 목록 | 개발 맥 |
| 1-6 | **내부망 실행(사용자 수행 · 런북 순서)** — 아래 「내부망 실행 순서」 | `logs/fabrix_proxy_poc/<시각>/` | **내부망** |
| 1-7 | **반출·판정** — 반출물 `summary.md`(지표·판정·권장 옵션) + `results.jsonl`(호출별 판정값 — 본문 없음 · 턴 번호와 입력 길이(문자 수·추정 토큰) 포함) + `env_check.md`. 개발 쪽에서 판정표로 판정하고 §부록 C에 기록 | 통과/미달 | 개발 맥 |

**내부망 실행 순서(런북 요약).**

| 순서 | 명령(요지) | 확인 | 실패 시 |
|---|---|---|---|
| ① E0 환경 점검 | `poc_run.py env-check` | python·패키지 버전(langchain-core · langchain-openai · openai · deepagents · fastapi · uvicorn · httpx · jsonschema) · FabriX 도달(짧은 호출 1회) · 프록시 기동·`/v1/models` 200 · **1-1 녹화 필드와 내부망 설치 버전의 요청 필드 대조** | 누락 wheel·버전 차이를 `env_check.md`로 반출 → 개발 쪽 보완 후 재반입 |
| ② 오프라인 재실행 | `pytest fabrix_proxy/tests` · `pytest tests/test_fabrix_proxy_poc.py` | 내부망 설치 버전에서 A·B·C 통과 | 실패 목록 반출 |
| ③ F1 | `probe_fabrix.py native` | 네이티브 엔드포인트 유무 | 있으면 이후 전 시나리오를 `--passthrough`로 추가 실행 |
| ④ F2 | `probe_fabrix.py contents` | 두 모드 점수 · 러너가 권장 `--contents-mode`를 `summary.md`에 적음 | — |
| ⑤a few-shot 비교 | `poc_run.py run --only S1,S4,S5 --fewshot none` / `static` / `dynamic` (S1 10 · S4 10 · S5 5) | 형식 유효율 · 선택 정답률 · 오탐률 · 완주율 · 예시 도구 오호출 수 → 권장 `--fewshot` 기록. 정적 예시가 기본이며, 동적 예시는 형식 유효율이 더 높고 오호출이 0일 때만 고른다 | 세 모드 모두 형식 유효율이 낮으면 `--fewshot-placement contents`로 static만 1회 더 |
| ⑤b 문구 언어 비교 | `poc_run.py run --only S1,S5 --fewshot <⑤a> --protocol-lang en` / `ko` (S1 10 · S5 5) | 나은 쪽을 권장값으로 기록 | — |
| ⑥ 본 측정 | `poc_run.py run --contents-mode <④> --fewshot <⑤a> --fewshot-placement <⑤a> --protocol-lang <⑤b>` | S1~S6 · F5 | 개별 시나리오 실패는 기록하고 다음으로 진행(중단하지 않음) |
| ⑦ 반출 | `logs/fabrix_proxy_poc/<시각>/`의 반출물 3종 | 본문·자격증명 미포함 확인(러너가 반출물 생성 시 자기 검사) | — |

- **호출량·소요(추정)**: ⑥ 본 측정 약 220회(S1·S2·S4 각 20 · S3 20 · S5 10×5턴 · S6 10×약 6턴) + ③④ 약 20회 + ⑤a 약 135회(3모드 × (S1 10 + S4 10 + S5 5×5턴)) + ⑤b 약 70회(2언어 × (S1 10 + S5 5×5턴)). 총 약 445회 · 호출당 5~15초로 잡으면 40~110분. 기본은 **순차 실행**(`--concurrency 1`) · 호출 간 `--sleep`. **이 호출량을 순차로 실행하는 것은 내부망 FabriX 사용 정책상 가능하다**(사용자 2026-10-08 확인).
- **데이터**: 시나리오의 도구·도구 결과는 전부 합성(`testdata/scenarios/`)이다 — 실 DB·실 알람 데이터를 FabriX에 보내지 않는다. F5의 호스트명·IP도 합성값이다.
- **재측정 사이클(미달 시 1회)**: 반출 요약으로 실패 유형을 보고 개발 쪽에서 규약 문구·few-shot 예시 파일만 고쳐 재반입 → `--protocol-file`·`--fewshot-file`로 ⑥만 다시 돈다(코드 재반입 없음이 원칙).

**테스트 묶음.**

| 묶음 | 내용 | 위치 · 실행 | LLM |
|---|---|---|---|
| **A. 단위** | 변환 표(system → `systemPrompt` · assistant `tool_calls` → `<tool_call>` 블록 · `tool` → `<tool_response>` · 연속 같은 역할 병합 · `contents` 두 모드) · 파서 표(§3.4 ④ 전 행) · `tool_choice` 4종 · `parallel_tool_calls=false` · id 발급 · 교정 1회 성공/실패(502) · 오류 봉투 형식 · 경계(import 0) · **few-shot**(렌더한 예시를 파서에 넣으면 기대 `tool_calls`가 그대로 나옴 · 예시 도구 이름 ∩ 요청 도구 이름 = ∅(겹치면 접미사) · 업무 도메인 어휘 없음 · `parallel_tool_calls=false`면 패턴 ② 제외 · `--fewshot none`이면 블록 없음) | `fabrix_proxy/tests/` · 개발 맥 + 내부망 ② | 없음 |
| **B. 계약** | 프록시 + 가짜 KBGenAI(대본 모드)를 실프로세스로 띄우고: openai SDK `chat.completions.create(tools=…)` 비스트림·스트림 → `tool_calls` · `ChatOpenAI(base_url=프록시).bind_tools([…])` `ainvoke`·`astream` → `AIMessage.tool_calls`(스트림 조각 조립 포함) · 401/400/502/504가 SDK 예외 계열로 매핑 · 가짜 KBGenAI가 받은 페이로드에 도구 규약 블록·이력 직렬화가 들어 있음 | `fabrix_proxy/tests/test_contract_*.py` · 개발 맥 + 내부망 ② | 없음(대본) |
| **C. 소비자 종단** | 프록시를 **하위 프로세스로** 띄우고(import 0 유지) 본체 쪽 실제 조립 함수로: ① deepagents `create_deep_agent`(모델 = `ChatOpenAI`→프록시 · 가짜 도구 1개) 미니 에이전트가 도구 호출 → 결과 → 최종 답까지 완주 ② `create_orchestrator_llm`(provider `vllm` · base_url 프록시) + `orchestrator_available` = True ③ noise_gate `_select_backend` = `track_b` · 도구 루프 1회 ④ litellm `openai/<별칭>` + `api_base` 1회 왕복(`sre_agent/.venv` 있을 때만 — 없으면 skip 사유 출력) | 루트 `tests/test_fabrix_proxy_poc.py` · 프록시·deepagents 미설치면 skip(사유 출력) · 개발 맥 + 내부망 ② | 없음(대본) |
| **D. 실 FabriX 시나리오 러너** | `scripts/poc_run.py run --repeat N` — 아래 시나리오를 프록시 경유로 N회씩 돌려 지표 산출. 결과는 판정값 JSONL + `summary.md`, **실패 건만** 원출력 앞 500자를 내부망 로그에 저장(반출 제외) | `fabrix_proxy/scripts/` · 내부망 ③~⑥ | **실 FabriX(내부망)** |

**D 러너 시나리오.**

| ID | 시나리오 | 기본 N | 지표 |
|---|---|---:|---|
| F1 | (프록시 미경유) FabriX에 OpenAI 호환 `/v1/chat/completions` + `tools` 1개 요청 — 엔드포인트 존재 · `tool_calls` 반환 여부 | 1 | 존재/부재 · 응답 형태 |
| F2 | (프록시 미경유) `contents` 2·3·4원소 대본 — 모델이 각 원소를 누구의 말로 보는지 · 빈 원소 효과 · 두 직렬화 모드 비교 | 각 3 | G-5 결정 |
| S1 | 도구 3개 중 맞는 1개를 단발 호출(`auto`) | 20 | 형식 유효 · 인자 유효 · 도구 선택 정답 · 예시 도구 오호출 |
| S2 | 병렬 2개 호출이 필요한 요청 | 20 | 블록 2개 · 각 유효 |
| S3 | `tool_choice=required` · 이름 지정 | 각 10 | 지정 준수 |
| S4 | 도구가 필요 없는 질문(평문 답이어야 함) | 20 | **오탐률**(불필요한 호출) |
| S5 | 5턴 ReAct — 러너가 합성 도구 결과를 돌려주고 모델이 다음 행동을 고름 | 10 | 루프 완주 · 같은 호출 반복 여부 · 최종 답이 도구 결과를 반영했는지(합성 결과 속 표지 값이 답에 나오는지) · 턴별 입력 길이 |
| S6 | deepagents 미니 에이전트(`write_todos` + 합성 도구 2개) 완주 | 10 | **반영(도메인 도구 사용 + 최종 답에 표지 값 — v5.4)** · 도구 미사용 종료(`no_tool_use`) · 완주 · 단계 수 · 호출당 지연 p50/p95 · 턴별 입력 길이 |
| F5 | 응답 `usage` 유무 · 합성 호스트명·IP가 든 도구 결과에서 PII 필터 차단 여부 | 각 3 | 위험 R-4 근거 |

> F1이 「네이티브 지원」이면 `--passthrough`로 S1~S6을 추가로 돌려 같은 기준으로 판정한다 — 이 경우 2단계 범위가 「얇은 프록시」로 줄어든다.

**판정(G-4 확정 — 사용자 2026-10-08 「권고대로」).**

| 항목 | 합격선 |
|---|---|
| 오프라인 테스트 A·B·C(개발 맥 · 내부망 ② 모두) | 전부 통과 |
| S1·S2·S3 형식·인자 유효율(교정 포함) | **≥95%** |
| S1 도구 선택 정답률 | ≥90% |
| S4 오탐률 | ≤5% |
| S5 완주율 | **≥90%** |
| S5 최종 답의 도구 결과 반영률 | ≥90% |
| **S6 반영률**(도메인 도구를 1회 이상 쓰고 최종 답에 표지 값이 모두 들어감 · 분모 = S6 전체 케이스) | **≥90%** — v5.4 사용자 결정(2026-10-08 「S6도 반영률로 판정」). S6 완주율(마지막이 평문)은 기록만 한다 — 도구를 하나도 안 쓰고 끝나도 완주로 세지기 때문이다 |

> **반영 판정은 유니코드 변종을 접어 비교한다**(NFKC · 하이픈·공백·따옴표 변종 → ASCII · 소문자 — `poc_run.reflects`, 커밋 `925360c`). 실 FabriX(GptOss)는 최종 답에 U+2011 하이픈·U+202F 공백을 섞어 쓴다(`docs/18` 2026-10-08).
| 턴별 입력 길이 | 기록만(판정 아님) — 턴당 증가량으로 장기 루프의 한도 도달 시점을 추정해 2단계 §3.6 근거로 쓴다 |
| 교정 의존률(교정 후 성공 / 전체 성공) | ≤15% |
| few-shot 예시 도구 오호출(교정 전 원응답 기준 · 전 시나리오) | **0건**이 목표 — 1건 이상이면 R-8 완화 후 재측정 대상 |
| 호출당 지연 | 기록만(판정 아님 — 응답시간 목표 대조용) |

- **통과** → 결정 1건 등재(G-1·G-2·G-4 결과 · `plans/128` 미사용 · D-037·D-174 부기) · 128 상태 갱신 · 2단계 착수.
- **미달** → 실패 유형 분류(형식 / 도구 선택 / 이력 해석 / 한도·차단) → 규약 문구·few-shot 예시·`contents` 모드만 바꿔 **1회 재측정**(위 재측정 사이클) → 그래도 미달이면 중단: D-037 「재측정으로 기각 재확인」 부기 · 이 계획 상태 「중단」 · 128은 TODO 그대로.

**반입 의존.** 러너·탐침·테스트는 루트 venv 의존만 쓴다(fastapi · uvicorn · httpx · openai · langchain-openai · deepagents · pytest · jsonschema) — 내부망 wheel 보유 여부는 ① E0가 판정한다. FabriX 자격증명은 `fabrix_proxy/.encenv`에만 둔다.

### 2단계 — 정식화 (1단계 통과 시)

- FabriX 어댑터 완전 이식: 업스트림 SSE 중계(`fabrix_kbgenai.py:310-358`) · 벽시계 총상한(D-198) · junk 제거 · PII 진단 덤프(D-155 · 프록시 호스트 `logs/pii_block/`).
- **골든**: 도구 없는 요청의 이식 페이로드 = `KBGenAIChat._get_payload` 결과(JSON 동치). 골든은 루트 쪽 생성 스크립트가 파일로 고정하고 `fabrix_proxy` 테스트는 파일만 읽는다(import 0 유지).
- §3.5 하트비트 · §3.6 입력 한도(`context_length_exceeded`) · §3.7 감사 로그·`/metrics/emulation`·토큰 상수 시간 비교.
- 소비자 실물 확인: 하트비트 주석 라인 · `context_length_exceeded`를 `ChatOpenAI`·litellm·holmes가 어떻게 다루는지.

### 3단계 — 소비자 연동 (G-2 = deepagents 등)

순서대로 하나씩 켠다. 각 소비자는 **설정만** 바꾼다(§3.9).

| 순서 | 소비자 | 확인 |
|---|---|---|
| 3-1 | **deepagents 1단** | 가짜 KBGenAI(대본) + 프록시 + 본체 서버 → 사다리 1단 확정 로그 · 도구 질의 1건 완주 · 내부망 FabriX 1회 |
| 3-2 | noise_gate 트랙 B | `_select_backend` = `track_b` · 알람 1건 도구 루프 · 내부망 FabriX 1회 |
| 3-3 | sre_agent 조사 | `MODEL=openai/fabrix-tools` 조사 1건 완주(대본) · `OVERRIDE_*` 환경변수 적용 확인 · 내부망 FabriX 1회 |
| 3-4 | (G-7 재검토) | provider 표기 혼동이 실제 운영 분석에 문제가 되면 `fabrix_proxy` provider 값 신설(설정 화면·매뉴얼 동반) |

### 4단계 — 폐쇄망 운영 전환 (사용자 수행)

- 프록시 배포 → 소비자 설정 전환(§3.9) → 표본 질의(1단 3건 · 트랙 B 알람 3건 · 조사 1건) → `/metrics/emulation` 카운터 확인.
- 롤백: 소비자 설정 원복 또는 프록시 중지(§3.9 사다리 영향).

### 5단계 — 문서·결정

- 1단계 판정에서 등재한 결정에 운영 전환 결과 부기.
- `docs/23` §2.2.1 · `plans/66` §7-1 부기(「프록시 경유 시 가능 — 신뢰도 실측 결과」).
- `docs/34` §9 실행 명령(프록시 기동) · CLAUDE.md 저장소 지도·패키지 경계 표에 `fabrix_proxy/` 1행.
- 매뉴얼(D-255): **관리자 매뉴얼에 운영 절차 1절**(기동·설정·롤백). 사용자 화면 변화는 없다. G-7 (b)로 설정 화면에 provider가 추가되면 그때 화면 캡처 갱신.

---

## 6. 수용 기준

**1단계(PoC)** — §5 1단계 「판정」 표 전 항목.

**최종(2~4단계)**
1. 가짜 KBGenAI 대본에서 OpenAI 요청(`tools` 포함) → `tool_calls` 응답이 OpenAI 스키마로 나온다(비스트림·스트림 모두 · 업스트림 SSE 중계 포함).
2. 파서 표(§3.4 ④)의 모든 행과 교정 1회 성공·실패가 테스트로 고정된다. 실패는 502 `tool_call_invalid`로 드러나고 평문 강등이 없다.
3. 이식 페이로드가 `KBGenAIChat` 골든과 JSON 동치다(도구 없는 요청).
4. deepagents 1단 · noise_gate 트랙 B · sre_agent가 **코드 변경 없이** 설정만으로 프록시를 통해 도구 루프를 완주한다(대본 · 내부망 FabriX 각 1회).
5. 입력 한도 초과는 FabriX 호출 없이 400 `context_length_exceeded`. 총상한 초과는 504.
6. 프록시 로그에 프롬프트·응답 본문이 없다(PII 덤프 제외). FabriX 자격증명은 프록시 설정에만 있다.
7. `fabrix_proxy` ↔ `src`·`noise_gate`·`sre_agent` import 0(경계 테스트).

---

## 7. 위험과 완화

| R | 위험 | 영향 | 완화 |
|---|---|---|---|
| R-1 | 에뮬레이션 신뢰도 부족(D-037 기각 사유 재현) | 1단·조사 루프가 깨진 도구 호출로 실패 | 1단계 판정 관문 · 교정 1회 · 502로 드러냄 · 미달 시 중단 |
| R-2 | 도구 규약 블록과 누적 도구 결과가 입력 예산을 잠식 | 장기 루프에서 한도 초과 빈발 | 1단계 1-2 측정 · S5·S6 턴별 입력 길이 기록 · 스키마 압축 · `context_length_exceeded`로 소비자 압축 유도 |
| R-3 | `contents` 규약 오해로 이력이 잘못 해석 | 모델이 도구 결과를 못 보고 같은 호출 반복 | 1단계 F2 · S5 완주율 지표 · 소비자 호출 상한(트랙 B 5 · deepagents `recursion_limit` 25) |
| R-4 | FabriX PII 필터가 도구 결과(호스트명·IP)에 걸림 | 조사·트랙 B 차단 | 1단계 F5 측정 · 400 `content_filter`로 드러냄 · 차단 덤프(D-155) — 완화책(마스킹)은 별도 결정 |
| R-5 | 도구 턴 버퍼링으로 스트림 청크 간격 상한 초과 | 소비자 쪽 타임아웃 | 하트비트 주석 라인 · 2단계 실물 확인 |
| R-6 | 프록시 단일 장애점 | 1단·트랙 B·조사 동시 불가 | 1단·트랙 B는 가용성 판정으로 2단·트랙 A로 강등(기존 경로) · 조사는 스텁 사유 노출 |
| R-7 | 표기 혼동(`provider=vllm`인데 실제는 FabriX) | 장애 분석 오판 | 프록시 기동 로그 · 응답 헤더 `X-Proxy-Backend` · 3단계 3-4(G-7 재검토) |
| R-8 | few-shot 예시 누출 — 예시 도구 이름이나 인자 값을 베낌(`docs/18` 2026-10-06 선례) | 무효 호출, 또는 검증을 통과한 틀린 인자(동적 예시) | 정적 예시 · 업무와 무관한 가상 도구 이름 → 파서가 `name ∉ tools`로 결정적으로 거부 · 이름 겹침 단언(테스트 A) · ⑤a에서 오호출 수 측정 · 예시는 패턴당 1회로 짧게 |

---

## 8. 비범위 · 후속

- 워커 평면 전환(`LLM_PROVIDER` → 프록시) · `column_deriver` · instructor TOOLS 모드(G-8).
- FabriX 외 백엔드(vLLM·Gemini·MLX·Ollama) 중계 · 본체 FabriX 직접 클라이언트 퇴역 — 이 계획 범위 아님. `plans/128`은 148 성공 시 사용하지 않는다(사용자 2026-10-08).
- 응답 캐시·레이트 리밋·부하 분산·자동 장애 조치.
- 도구 실행(프록시는 도구를 실행하지 않는다) · MCP 노출.

---

## 부록 A. 실측에 쓴 명령

```bash
grep -rln -i 'fabrix' src noise_gate sre_agent mcp_server apm_gateway --include='*.py'
grep -rn -E 'bind_tools|with_structured_output|tool_calls' src noise_gate sre_agent --include='*.py'
grep -rn -E '\.bind_tools\(|create_orchestrator_llm\(|create_deep_agent|tool_choice' src noise_gate sre_agent/sre_agent --include='*.py'
grep -rn -i -E 'fabrix.{0,60}(tool.?call|도구 호출|툴 호출|tools)' docs plans spec --include='*.md'
sed -n 1,140p src/llm.py ; sed -n 420,482p src/llm.py
cat src/clients/fabrix_kbgenai.py src/clients/fabrix_client.py
sed -n 20,100p src/orchestration/deep_agent.py ; sed -n 140,175p noise_gate/application/nodes/agentic_enricher.py
```

## 부록 B. 개정 이력

| 판 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-10-08 | 신규 — 요청 검토(프록시 = 에뮬레이션 이전 · D-037·D-174·128 충돌 정리 · 네이티브 엔드포인트 선확인) + 설계·게이트 G-1~G-8·작업 P0~P6 |
| v2 | 2026-10-08 | 사용자 지시 *"148번 1단계에서 proxy서버를 poc 할 수 있는 테스트 코드를 만들어 동작여부를 테스트하는 단계를 넣어라. 148번 계획은 128번 계획과 다른 계획이다. 이게 성공되면 128번은 사용하지 않을 계획이다. 1단계에서 성공하면 deepagents 등에서 활용 예정이다."* — G-1 확정 (b) 독립 패키지 `fabrix_proxy/`(128과 별개 · 148 성공 시 128 미사용) · G-2 확정 (a) deepagents 등 · 작업을 1~5단계로 재편: **1단계 PoC**(PoC 서버 최소 범위 · 테스트 A 단위/B 계약/C 소비자 종단/D 실 LLM 시나리오 러너 · F1·F2 탐침 · 판정표 · 미달 시 재측정 1회 후 중단) → 2단계 정식화 → 3단계 소비자 연동(deepagents 1단 → 트랙 B → sre_agent) → 4단계 운영 전환 → 5단계 문서 · PoC 스트림 필수 근거(langchain-core `_should_stream` 실측) · D-번호는 1단계 판정 때 등재 |
| v3 | 2026-10-08 | 사용자 지시 *"테스트는 내부망 fabrix환경에서 진행할 예정이다. 계획을 업데이트하라."* — 1단계를 「개발 맥 = 코드·오프라인 테스트(실 LLM 0 · MLX 대조 제거)」 / 「내부망 = 판정 테스트 전부(사용자 실행)」로 분리 · 내부망 무수정 실행 옵션(`--contents-mode`·`--protocol-lang`·`--protocol-file`·`--repair-max`·`--passthrough`) · E0 환경 점검(버전·도달·녹화 필드 대조) · 런북 ①~⑦ · 호출량·소요 추정 · 반출물 = 판정값만 · 재측정은 문구 파일만 재반입 · 3단계·수용 기준의 MLX 중계 → 내부망 FabriX |
| v4 | 2026-10-08 | 사용자 지적 *"fabrix의 형식으로 변환할때는 tools호출 형식으로 변환될 수 있는 형식으로 응답을 받을 수 있도록 요청시 few shot형식으로 가이드가 되야될 것으로 보인다. 확인해봐."* — 지적 수용. v3 §3.3은 Known Mistakes 「프롬프트 강제가 few-shot과 경쟁」을 few-shot을 넣지 않는 근거로 읽었으나 오독이었다(이 항목은 예시가 지시문보다 강하다는 뜻). 첫 턴에는 이력 예시가 0개다 → §3.3.1 신설: 패턴 ①단일 ②병렬 ③결과 수신 후 다음 호출/최종 답 ④도구 불필요 · 정적 가상 도구가 기본(이름을 베끼면 파서가 결정적으로 잡음 · 동적 예시는 값 복사가 검증을 통과해 조용히 틀림) · 이력 직렬화와 같은 함수로 렌더 · 배치는 systemPrompt가 기본. 실행 옵션 `--fewshot`·`--fewshot-placement`·`--fewshot-file` · 테스트 A에 few-shot 가드 · 런북 ⑤a(few-shot 비교)/⑤b(언어) · 호출량 약 445회 · 판정에 예시 오호출 0건 · R-8 |
| v5 | 2026-10-08 | 사용자 질의 *"툴 호출을 통해 일부 정보를 작성하여 최초 요청과 조합하여 최종응답을 주는 방식… 같이 고려가 되어 있냐?"* → 지시 *"반영하라."* — §3.0 신설(도구 루프 전체 흐름 · 루프·도구 실행·이력 보존은 호출자, 변환·파싱은 프록시 · 프록시가 루프를 돌리지 않는 이유) · 규약 문구에 결과 수신 후 행동 규칙(더 필요하면 다음 호출 · 충분하면 원 질문에 최종 답 · 같은 호출 재호출 금지) · S5에 최종 답의 도구 결과 반영 지표(합격 ≥90%) · S5·S6 턴별 입력 길이 기록(판정 아님) · R-2 보강 |
| v5.1 | 2026-10-08 | 사용자 확정 *"1: 권고대로, 2: 가능하다."* — G-4 합격선 확정(v5 판정표 그대로) · 내부망 FabriX 호출량 약 445회 순차 실행 허용. 착수 전 사용자 확인 항목은 없다 |
| v5.2 | 2026-10-08 | 사용자 지시 *"1단계 poc 진행 가이드를 계획 파일에 정리하라."* — 부록 D 신설(흐름·역할 · 개발 맥 착수 준비 · 구현 Wave W1~W4와 반입 전 게이트 · 반입 묶음(`POC_VERSION`) · 내부망 설정·기동 · 실행 ①~⑦ 소요·인계값 · ⑤a/⑤b 선택 규칙 · 중단 시 재개 · 반출 점검 · 실패 시 조치 · 판정·기록 · 판정 뒤 처리) · 부록 C를 판정 기록 양식으로 · §5 1단계 머리와 §3.8에 안내 |
| v5.3 | 2026-10-08 | 1단계 개발 맥 구현 완료(부록 D.1~D.3 · 사용자 지시 *"1단계까지 구현하라."*) — `fabrix_proxy/` 독립 패키지(PoC 서버: 변환·규약·few-shot·파서·교정 1회·502·스트림 합성·총상한 504·POC_MODE 진단) · 가짜 KBGenAI · 러너 `poc_run.py`(env-check·run·권장값 `recommend.env`·반출물 자기 검사) · 탐침 `probe_fabrix.py` · 시나리오 · `POC_RUNBOOK.md`(사용자 지시 *"1단계의 poc 실행 명령어를 명확히 가이드에 작성하라."* — 리허설로 확인) · 테스트 A·B·C · 1-2 길이 부록 C 기입 · D.4~D.6을 런북 명령으로 갱신 · §3.4-5 원출력 로그 문구를 §3.7에 맞춰 정리 · 계획 번호 146 → 148(사용자 결정) |
| v5.4 | 2026-10-08 | 내부망 1차 결과(`results/fabrix_proxy_poc_20261008-141615`) 판정 — 부록 C.1 기록. 단발 지표 합격(형식 100% · 인자 98.6% · 선택 96.7% · 오탐 0% · 교정 의존 0.6%) · 권장 옵션 turns·none·system·ko · S5 반영률은 러너의 ASCII 비교 결함으로 무효(사용자 수정 `925360c` — 유니코드 접기) · **S6 「완주」가 도구 미사용 종료도 세는 결함 → 사용자 결정 「S6도 반영률로 판정」: 판정표 S6 행을 「S6 반영률 ≥90%」로 교체 · `no_tool_use` 실패 유형** · 사용자 결정 「S5 + S6 few-shot 비교」 재측정 R1(한도 미차감) |

## 부록 C. 1단계 판정 기록

(1단계 1-7에서 채운다 — 부록 D.7 절차.)

### C.1 1차 내부망 실행 (2026-10-08 14:26~15:01 · 반출 `results/fabrix_proxy_poc_20261008-141615` — `summary.md`·`results.jsonl`)

| 항목 | 값 | 비고 |
|---|---|---|
| 반입 코드 SHA (`POC_VERSION`) | `a95d8f5…-dirty` · `fabrix_proxy-sha256:3ad4c53d3a56` | 미커밋 상태로 반입했다. 반영 판정 결함이 있는 러너다(아래) |
| 내부망 실행 일시 · 실행자 | 2026-10-08 14:26~14:58 · run 6회 · 사용자 | |
| E0 환경 점검 | **반출 누락**(`env_check.md` 없음) | ①은 통과로 보고 다음 단계로 진행했다(②~⑥이 돌았음) |
| 규약 블록 + few-shot 길이(1-2) | **deepagents 1단(내장 8 + 본체 7 = 도구 15개) en: 규약 20,335자 + few-shot 1,505자 = 21,842자 · 추정 5,575토큰 · 95,232 대비 5.85%** (ko 21,228자 · 5,655토큰 · 5.94%) · 트랙 B(도구 3개) en 3,053자 · 867토큰 · 0.91%(ko 2,439자 · 948토큰 · 1.00%) · 참고: deepagents 내장만 en 5.40% · 녹화된 deepagents system 메시지 7,391자(약 1,861토큰) 별도 | 개발 맥 2026-10-08 · `fabrix_proxy/scripts/measure_protocol_cost.py` → `testdata/consumer_requests/protocol_cost.json` · 추정 토큰 = ASCII/4 + 비ASCII/1(보수 추정 — F3 실측 전) · 비용 대부분은 deepagents 내장 도구 설명(약 19K자) |
| F1 네이티브 엔드포인트 | **미기록**(summary 탐침 표 빈칸 · `probe_*.json`은 내부망 보관) | ⑥+ passthrough 측정 없음 → 네이티브 지원은 확인되지 않았다. 사용자 확인 필요 |
| F2 → `--contents-mode` | **turns**(turns·transcript 모두 다음 행동 정답 3/3 · 역할 인식 n2·n3·n4 빈 첫 원소 3/3 · `n4_assistant_said` 0/3) | 모델이 「자기 이전 답」을 지목하는 질문에는 약하다 — 도구 이력 해석에는 영향이 없었다(S5 체인 30/35 정상) |
| ⑤a → `--fewshot` · `--fewshot-placement` | **none · system** — 형식 유효율(S1·S4·S5) none 100%(55/55) · static 94.7%(36/38) · dynamic 100%(40/40) · 예시 도구 오호출 전 모드 0건 | **few-shot은 짧은 프롬프트에서 이득이 없었다**(static은 S1 선택 70% — n=10). 긴 deepagents 프롬프트(S6)에서는 재측정 R1에서 비교 |
| ⑤b → `--protocol-lang` | **ko** — en S5 완주 80%(업스트림 오류 3건 포함) · ko 100% | |
| S1·S2·S3 형식·인자 유효율 | **100% (70/70) · 98.6% (69/70)** | ⑥(ko·none) 라벨 · 합격 ≥95% ✅ |
| S1 도구 선택 정답률 | **96.7% (29/30)** | 합격 ≥90% ✅ |
| S4 오탐률 | **0% (0/20)** | 합격 ≤5% ✅ |
| S5 완주율 | **100% (15/15)** · 호출 순서 `get_ticket > get_person > get_office > 평문`이 전 라벨 30/35 | 합격 ≥90% ✅ |
| S5 도구 결과 반영률 | 집계 6.7%(1/15) — **무효(러너 결함)** | 반영 판정이 ASCII 부분 문자열 비교라 U+2011 하이픈 등 변종을 놓쳤다. 사용자가 내부망 덤프로 최종 답에 값이 들어 있음을 확인했다 → 접기 판정으로 수정(`925360c`) · R1에서 재측정 |
| S6 | 완주(마지막 평문) 100%(10/10)였으나 **실제로는 10건 중 6건이 도구 0개로 바로 평문, 2건은 `write_todos`만, 도메인 도구까지 쓴 것은 2건** | 「완주」 정의 결함 → v5.4에서 **S6 반영률 ≥90%**로 판정 기준 변경(사용자 결정) · R1에서 few-shot 3모드 비교 |
| 교정 의존률 | **0.6% (1/176)** | 합격 ≤15% ✅ |
| 예시 도구 오호출 | **0건** | ✅ |
| 지연 p50/p95 · 턴별 입력 길이 증가량 | 호출 p50 1,093ms · p95 1,889ms(⑥) · S6 실행 p50 1,938ms · p95 13,906ms · S5 턴당 +230자(+57토큰) · S6 턴당 +500자(+125토큰), S6 첫 턴 26,738자(추정 6,887토큰) | 기록만 |
| F5 `usage` 유무 · PII 차단 | usage **없음**(0/3) · PII 차단 0/3 | usage 미제공 → 프록시 응답에서 `usage` 생략(§3.4-6) |
| 인프라 실패 | en·none 라벨 3건(`upstream_error` — S5 1턴) | 판정 분모에서 제외(단발) · 완주율에는 포함 |
| **판정** | **보류** — 단발 지표(S1~S4)·교정·예시 오호출은 합격. S5 반영(러너 결함)·S6(기준 결함)은 판정 불가 | D.8 「코드 결함 발견」 — 고친 뒤 재측정하며 **재측정 1회 한도에 넣지 않는다** |
| 재측정 여부 · 바꾼 파일 | **R1 예정**: S5 15건(turns·none·system·ko) + S6 10건 × few-shot none/static/dynamic · 러너 `poc_run.py`(반영 접기 `925360c` + S6 반영 판정·`no_tool_use`) · 런북 「재측정 R1」 절 | 반입 전 커밋하고 `POC_VERSION`을 커밋 SHA로 교체 |

### C.2 재측정 R1

(R1 반출물을 받은 뒤 기록한다.)

## 부록 D. 1단계 PoC 진행 가이드

> 1단계를 처음부터 판정까지 실제로 진행하는 순서다. §5 1단계의 표(작업 1-1~1-7 · 런북 ①~⑦ · 테스트 묶음 · 판정)를 실행 순서로 풀어 쓴 것이다. 기준이 다르면 §5가 정본이다. `fabrix_proxy/POC_RUNBOOK.md`는 D.4~D.6을 내부망용으로 옮긴 것이다.

### D.0 흐름과 역할

```
[개발 맥 · 실 LLM 0]                      [내부망 · 사용자 실행]                  [개발 맥]
 D.1 착수 준비                              D.4 설정·기동                          D.7 판정·기록
 D.2 구현 Wave 1~4  ──▶ D.3 반입 묶음 ──▶   D.5 실행 ①~⑦  ──▶ 반출물 3종 ──▶      D.8 통과 / 재측정 / 중단
                                            D.6 실패 시 조치
```

| 역할 | 하는 일 |
|---|---|
| 개발 쪽(team-lead → implementer · verifier) | D.1~D.3 · D.7 · 재측정 시 문구·예시 파일 수정 |
| 사용자(내부망) | 반입 · D.4~D.6 실행 · 반출 · 단계별 선택값 확인 |

### D.1 개발 맥 — 착수 준비

1. 세션 시작 SHA를 적는다: `git rev-parse HEAD` → 모듈 회귀 `--base`에 쓴다(D-303).
2. 관련 결정만 읽는다: D-037(에뮬레이션 기각 사유) · D-174(평면 분리) · D-198(총상한) · D-155(PII 진단) · D-194(`llmConfig`) · D-268(마감) · D-274(자격증명 위치).
3. 계획서 상태를 `TODO` → `WIP`로 바꾼다(파일명 · INDEX 행 · INDEX-CHANGELOG 1줄).
4. team-lead 하나에 1단계를 위임한다. 위임 프롬프트에는 §3.0~§3.8 · §5 1단계 · 이 부록의 해당 Wave를 직접 적는다(문서 전체를 읽히지 않는다).

### D.2 개발 맥 — 구현 Wave

| Wave | 작업 | 산출 | 완료 조건 |
|---|---|---|---|
| W1 | 1-1 소비자 요청 녹화 · 1-2 규약 블록 비용 | `testdata/consumer_requests/*.json` · 길이 수치 | 녹화 필드 목록으로 §3.2 받는/무시 필드 확정 · 규약 블록 + few-shot 길이가 95,232 대비 몇 %인지 수치로 기록 |
| W2 | 서버 핵심 — `config` · `app` · `convert` · `tool_protocol`(+ `fewshot_default.json`) · `fabrix_client`(비스트림) · `sse` | `fabrix_proxy/fabrix_proxy/` | 테스트 A 전부 통과(변환 표 · 파서 표 · `tool_choice` 4종 · 교정 1회 · 오류 봉투 · few-shot 가드 · 경계) |
| W3 | 가짜 KBGenAI(대본 모드) · 러너 `poc_run.py`(env-check · run · 반출물 생성 · 자기 검사) · 탐침 `probe_fabrix.py` · 시나리오 `testdata/scenarios/` · 런북 | `scripts/` · `POC_RUNBOOK.md` | 테스트 B(계약) · C(소비자 종단) 통과 |
| W4 | 1-4 드라이런 · 정적 게이트 · 모듈 회귀 · 1-5 반입 묶음 | 드라이런 요약 · 반입 목록 | 아래 「반입 전 게이트」 전부 ✅ |

- Wave 중간: implementer는 자기 테스트 + `regress.py --no-tests --files …`만 돌린다. W4에서 team-lead가 `regress.py --base <D.1 SHA> --files <1단계가 바꾼 파일…>`을 1회 돌린다.
- `fabrix_proxy` 자체 테스트는 루트 수집 밖이다: `cd fabrix_proxy && ../.venv/bin/python -m pytest`.

**반입 전 게이트(W4).**

- [ ] `fabrix_proxy` 테스트 A·B 전부 통과
- [ ] 루트 `tests/test_fabrix_proxy_poc.py`(C) 통과 — skip이 있으면 사유를 보고에 적는다
- [ ] `poc_run.py run --dry-run`(가짜 KBGenAI 대본)이 F1·F2·S1~S6·F5와 ⑤a·⑤b 조합까지 완주하고 `summary.md` · `results.jsonl` · `env_check.md`를 만든다
- [ ] 반출물 자기 검사가 동작한다 — 대본 응답에 심은 표지 문자열(가짜 키 · 가짜 URL · 본문 조각)이 반출물에 들어가면 러너가 실패한다(테스트로 고정)
- [ ] 개발 맥에서 실 LLM 호출 0건 — 업스트림은 가짜 KBGenAI만
- [ ] 모듈 회귀 결과의 범위 줄을 보고에 옮긴다(`범위: 모듈 단위 — 전체 미실행`)
- [ ] 1-2 길이 수치를 부록 C에 적는다

### D.3 반입 묶음 (1-5)

| 넣는 것 | 넣지 않는 것 |
|---|---|
| `fabrix_proxy/` 전체(코드 · `tests/` · `testdata/` · `scripts/` · `fewshot_default.json` · 규약 문구 파일 en/ko) · `POC_RUNBOOK.md` · `.env.example` | `.env` · `.encenv` · `logs/` · 자격증명 일체 |
| 루트 `tests/test_fabrix_proxy_poc.py` | 실데이터(시나리오는 합성만) |
| `fabrix_proxy/POC_VERSION` — 반입 시점 `git rev-parse HEAD` 1줄. 러너가 `summary.md`에 옮겨 적는다 | |

- 내부망 venv에 없는 wheel은 반입 전에는 알 수 없다. ① env-check가 누락 목록을 `env_check.md`로 내면 그때 보완해 재반입한다.

### D.4 내부망 — 설정과 기동

> 전문(복사해 붙여 넣는 명령 · 설정 파일 전문 템플릿 · 성공 시 출력 예)은 **`fabrix_proxy/POC_RUNBOOK.md`**다. 아래는 같은 명령의 요약이다. 런북 명령은 개발 맥에서 가짜 KBGenAI로 처음부터 끝까지 그대로 돌려 확인했다(2026-10-08 · 런북 10절).

1. 반입 묶음을 내부망 저장소 작업 트리의 같은 경로에 둔다. 루트 venv(`$REPO/.venv`)를 그대로 쓴다.
2. 변수 블록(런북 2절) — `REPO=`만 고친다. 새 터미널에서는 `CURRENT` 파일로 같은 `RUN_DIR`을 이어 쓴다.
   ```bash
   REPO=/path/to/collectorinfra
   PY="$REPO/.venv/bin/python"
   RUN_DIR="$REPO/logs/fabrix_proxy_poc/$(date +%Y%m%d-%H%M%S)"
   mkdir -p "$RUN_DIR" && echo "$RUN_DIR" > "$REPO/logs/fabrix_proxy_poc/CURRENT"
   ```
3. 설정 파일 두 개(런북 3절 — 키 정본 `fabrix_proxy/.env.example` · 인라인 주석 금지 · list/dict는 JSON). 셸의 `FABRIX_*` 환경변수가 파일보다 우선하므로 먼저 `env | grep '^FABRIX_'` 0줄 확인.

   | 파일 | 키 | 값의 출처 |
   |---|---|---|
   | `fabrix_proxy/.encenv` | `FABRIX_PROXY_TOKEN` | 새로 만든다 — `"$PY" -c "import secrets;print(secrets.token_hex(32))"` |
   | 〃 | `FABRIX_API_KEY` · `FABRIX_CLIENT_KEY` | 본체 `.encenv`의 `LLM_FABRIX_API_KEY` · `LLM_FABRIX_CLIENT_KEY` |
   | `fabrix_proxy/.env` | `FABRIX_BASE_URL` · `FABRIX_MODEL` · `FABRIX_TOTAL_TIMEOUT` | 본체 `.env`의 `LLM_FABRIX_BASE_URL` · `LLM_FABRIX_CHAT_MODEL`(비면 `LLM_MODEL`) · `LLM_FABRIX_TOTAL_TIMEOUT` |
   | 〃 | `FABRIX_PROXY_POC_MODE` | **`true`** (PoC 필수 — 아니면 러너가 멈춘다) |
   | 〃 | 나머지(`FABRIX_PROXY_HOST=127.0.0.1` · `_PORT=9095` · `_MODEL_ALIASES=["fabrix-tools"]` · `FABRIX_VERIFY_SSL=false` · `FABRIX_TIMEOUT=300` · `FABRIX_LLM_CONFIG={}` · `FABRIX_NATIVE_URL`·`_MODEL` 빈 값 · 에뮬레이션 선택지 기본값) | 기본값 유지 |

4. 프록시 기동(터미널 A · 런북 4절): `cd "$REPO/fabrix_proxy" && nohup "$PY" -m fabrix_proxy > "$RUN_DIR/proxy.log" 2>&1 & echo $! > "$RUN_DIR/proxy.pid"`.
5. 확인: 로그 1줄 `fabrix_proxy start backend=fabrix … poc_mode=True` · `curl -s --noproxy '*' http://127.0.0.1:9095/v1/models`가 무인증 200이고 `fabrix-tools`가 보인다. 토큰이 비면 기동 거부가 정상이다.
6. 종료(런북 9절): `kill "$(cat "$RUN_DIR/proxy.pid")"` — 자기 PID만.

### D.5 내부망 — 실행 ①~⑦

모든 명령은 `cd "$REPO/fabrix_proxy"`에서 `"$PY" scripts/…`로 돌고, 결과는 전부 `--out "$RUN_DIR"` 한 디렉터리에 덧붙는다(`summary.md`·`recommend.env`는 디렉터리 전체로 다시 만든다). 기본은 순차 실행(`--concurrency 1`)이다 — 약 445회 순차 실행은 사용 정책상 가능하다(사용자 2026-10-08).

| 순서 | 명령(런북과 같음) | 소요(추정) | 확인 | 넘기는 값 |
|---|---|---|---|---|
| ① | `poc_run.py env-check --out "$RUN_DIR"` | 2분 | `env_check.md`의 `누락` · `결과: ok` · `POC_MODE: on` · 녹화 필드 `차이` | 누락이 있으면 **여기서 멈추고** `env_check.md`만 반출 |
| ② | `"$PY" -m pytest tests -q` · `cd "$REPO" && "$PY" -m pytest tests/test_fabrix_proxy_poc.py -rs -q` | 3분 | A·B·C 통과(litellm skip 1건은 정상) | 실패가 있으면 목록 반출 후 멈춤 |
| ③ | `probe_fabrix.py native --out "$RUN_DIR" [--url <후보 URL>]` | 1분 | `summary.md`의 `ADVISE PASSTHROUGH` 줄 유무 | 있으면 ⑥ 뒤 ⑥+ |
| ④ | `probe_fabrix.py contents --out "$RUN_DIR"` | 5분 | `RECOMMEND CONTENTS_MODE=…` | `CONTENTS_MODE` |
| ⑤a | `poc_run.py run --only S1,S4,S5 --repeat S1=10,S4=10,S5=5 --fewshot none --out "$RUN_DIR"` → `static` → `dynamic` | 10~35분 | `RECOMMEND FEWSHOT=…` · `RECOMMEND FEWSHOT_PLACEMENT=…` · `ADVISE FEWSHOT_RERUN` 유무 | `FEWSHOT` · `FEWSHOT_PLACEMENT` |
| ⑤b | `poc_run.py run --only S1,S5 --repeat S1=10,S5=5 --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang en --out "$RUN_DIR"` → `ko` | 5~20분 | `RECOMMEND PROTOCOL_LANG=…` | `PROTOCOL_LANG` |
| ⑥ | `poc_run.py run --contents-mode "$CONTENTS_MODE" --fewshot "$FEWSHOT" --fewshot-placement "$FEWSHOT_PLACEMENT" --protocol-lang "$PROTOCOL_LANG" --out "$RUN_DIR"` | 20~55분 | S1~S6·F5 지표 · 판정 초안 | — |
| ⑥+ | (③ 권고 시) 프록시를 `FABRIX_NATIVE_URL='<후보>' nohup "$PY" -m fabrix_proxy …`로 재기동 → ⑥ 명령에 `--passthrough` | 20~55분 | `passthrough=true` 라벨 | 같은 세션이 어려우면 다음 세션 |
| ⑦ | `export/`에 반출 3종 복사 → 아래 점검 | 5분 | grep 0줄 | — |

**값 넘기기.** 러너가 `summary.md`에 고정 줄(`RECOMMEND CONTENTS_MODE=…` · `RECOMMEND FEWSHOT=…` · `RECOMMEND FEWSHOT_PLACEMENT=…` · `RECOMMEND PROTOCOL_LANG=…` · `ADVISE …`)을 쓰고, 같은 키를 `recommend.env`에 쓴다. 단계마다 `grep -E '^(RECOMMEND|ADVISE)' "$RUN_DIR/summary.md"`로 보고 `. "$RUN_DIR/recommend.env"`로 셸 변수에 실은 뒤 위 명령의 `"$FEWSHOT"` 등에 그대로 쓴다.

**⑤a 선택 규칙.** 러너가 계산한다 — 사용자는 `summary.md` 「근거:」 줄과 맞는지만 본다.
1. 예시 도구 오호출이 1건 이상인 모드는 제외한다.
2. 남은 모드 중 형식 유효율(S1·S4·S5)이 가장 높은 쪽. 차이가 2%p 이내면 `static`(기본값).
3. 세 모드 모두 90% 미만이면(`ADVISE FEWSHOT_RERUN`) `--fewshot static --fewshot-placement contents`로 ⑤a를 1회 더 돌린 뒤 같은 규칙. 그래도 `FEWSHOT`이 비면(`ADVISE FEWSHOT=undecided`) 기본값 `static`·`system`으로 계속하고 반출 때 알린다.

**⑤b 선택 규칙.** 형식 유효율(S1·S5)이 높은 쪽 · 2%p 이내면 S5 완주율 · 같으면 `en`.

**중단됐을 때.** 새 터미널에서 런북 2절 두 번째 블록(`RUN_DIR="$(cat "$REPO/logs/fabrix_proxy_poc/CURRENT")"` · `. "$RUN_DIR/recommend.env"`) → 끝나지 않은 시나리오만 같은 `--out`으로: 예 `poc_run.py run --only S5,S6 --contents-mode "$CONTENTS_MODE" … --out "$RUN_DIR"`.

**⑦ 반출 점검.** 러너의 자기 검사와 별도로 사용자가 한 번 더 본다.
- 반출 대상은 `summary.md` · `results.jsonl` · `env_check.md` 세 개뿐이다(`mkdir -p "$RUN_DIR/export" && cp …`). `failures/`·`proxy*.log`·`recommend.env`·`probe_*.json`은 내부망에 남긴다.
- `grep -n -E 'https?://|Bearer' …` 와 `.encenv`에서 뽑은 API 키·client 키·프록시 토큰 앞 6자의 `grep -n -F -e … ` 결과가 모두 0줄이어야 한다(키 추출 명령은 런북 6.9절).

### D.6 내부망 — 실패 시 조치

전체 표는 런북 8절. 요지:

| 증상 | 원인 후보 | 조치 |
|---|---|---|
| 프록시 기동 거부 · `설정 오류: <키>` | 토큰 빈 값 · 인라인 주석 · JSON 아닌 list/dict | `.encenv`·`.env` 해당 줄을 런북 3절 템플릿대로 |
| `address already in use` | 9095 사용 중 | `nohup "$PY" -m fabrix_proxy --port 9195 …` + 러너·탐침마다 `--proxy-url http://127.0.0.1:9195` |
| `/v1/models` 401 | 인증을 잘못 붙인 판 | `/v1/models`는 무인증이어야 한다 — 코드 결함으로 반출 |
| 러너 `프록시가 POC_MODE가 아니다` | `FABRIX_PROXY_POC_MODE` 누락 | `true`로 고치고 프록시 재기동 |
| ① `결과:`가 `ok` 아님 | 주소 · 망 · TLS · 키 | 본체 `.env`/`.encenv` 값과 대조 · `FABRIX_VERIFY_SSL` · 셸 `HTTPS_PROXY` 확인 |
| 400 `content_filter` | FabriX PII 필터 | F5와 같은 현상 — 기록하고 계속 진행한다(R-4) |
| 502 `tool_call_invalid` 다발 | 모델이 규약을 못 따름 | **측정 결과 자체다** — 멈추지 않고 계속 진행한다 |
| 502 `upstream_error` · 504 연속 | FabriX 장애 · 과부하 | 5분 쉬고 해당 시나리오만 `--only` + 같은 `--out`으로 재실행 · `--sleep` 증가 |
| ② 테스트 실패 | 내부망 패키지 버전 차이 | 실패 목록과 `env_check.md` 반출 → 개발 쪽 보완 후 재반입 |
| 러너 종료 코드 3(반출물 자기 검사) | 반출 파일에 URL·키·본문 표지 | 그 파일은 쓰이지 않았다 — 규칙 이름만 알리고 멈춘다(코드 결함) |

### D.7 개발 맥 — 판정과 기록

1. 반출물을 `logs/` 밖 임시 경로에 받는다. 저장소에는 넣지 않는다 — 판정값만 부록 C로 옮긴다.
2. `summary.md`의 지표를 §5 1단계 판정표와 대조해 부록 C를 채운다. 러너의 판정 초안은 참고로만 쓰고, 판정은 개발 쪽이 내린다.
3. 실패 건은 `results.jsonl`의 실패 유형(형식 / 도구 선택 / 이력 해석 / 한도·차단)으로 센다.

### D.8 판정 뒤 처리

| 판정 | 처리 |
|---|---|
| **통과** | ① D-번호 등재 — `ls docs/decisions/ \| tail -3`으로 최댓값 확인 · G-1·G-2·G-4 결과 · 128 미사용 · D-037·D-174 부기 ② `plans/128` 머리·INDEX 행 갱신 ③ 이 계획 1단계 완료 표기 · 2단계 착수 |
| **미달(1차)** | 실패 유형을 보고 **규약 문구 파일 · few-shot 예시 파일만** 고친다(코드 재반입 없음이 원칙) → 두 파일 반입 → 내부망에서 `--protocol-file` · `--fewshot-file`로 ⑥만 재실행 → D.7 |
| **미달(재측정 후)** | 중단 — D-037에 「재측정으로 기각 재확인」 부기 · 이 계획 상태 「중단」 · 128은 TODO 그대로 |
| 코드 결함 발견 | 측정 판정과 분리한다 — 고친 뒤 D.3부터 다시(재측정 1회 한도에 넣지 않는다) |

- 진행 중 생긴 에이전트 실수는 `docs/18`에 바로 적는다.
- **일정(추정)**: 개발 맥 W1~W4 · 내부망 1세션 약 1.5~2.5시간(설정 20분 + ①~⑦ 50~120분) · 미달 시 재측정 세션 1회(⑥만 20~55분).
