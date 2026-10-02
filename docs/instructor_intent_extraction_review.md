# Instructor 기반 의도 추출 개선 — 심층 검토 보고서

> 작성일: 2026-08-26 · 요구(사용자): *"Instructor를 이용하여 의도 추출하는 부분을 개선하는 것에
> 대해 심도 있게 검토하여 보고하라."*
> 관련: `plans/79`(시멘틱 라우팅 개선) · `plans/78` §4.7.5·**R-13** · D-035(결정적=판단·LLM=보조) ·
> D-127(과금 외부 API 승인 게이트) · Plan 69 P2(KBGenAI 판정 단일 출처)
> **검토 방식**: 문서 추정이 아니라 **격리 venv에 `instructor` 1.15.4를 설치해 직접 실행**하여
> 동작을 실측했다(과금 API 호출 없음 — 가짜 `create`로 대역).

---

## 0. 결론 요약

| 질문 | 답 |
|---|---|
| 우리 LLM(FabriX KBGenAI, 평문·tool-calling 없음)에 **붙는가** | **붙는다 — 실험으로 증명**(§2). `pydantic-ai`와 결정적으로 다른 점 |
| 검증 오류를 모델에 **되먹이는가** | **되먹인다 — pydantic `ValidationError` 원문 그대로**(§2.2) |
| 우리 코드의 **실재 결함을 고치는가** | **고친다 — 4개 표면에 확인된 결함 5종**(§3) |
| **지금 바로 도입**해야 하는가 | **아니다.** 번들된 영어 스키마 프롬프트 주입이 **`plans/79` 트랙 A가 막 안정화한 라우터 프롬프트를 건드리고**, 그 영향은 **D-127 승인 없이는 측정 불가**(§5.1) |

**권고: 조건부 2단계 도입.**
**1단계** — `intent_planner._llm_decompose`(78 **R-13**)에 적용. 재시도가 아예 없고 침묵 폴백이며,
스키마가 중첩(`TaskSpec`)이라 자동 JSON Schema의 값이 가장 크고, **프롬프트가 현재 변경 중이 아니다.**
**2단계** — `semantic_router`는 **79 트랙 A 분류 정확도 측정이 끝난 뒤** 판단(§5.1).

---

## 1. 검토 대상 — "의도 추출"은 한 곳이 아니다

전수 확인 결과 **4개 표면**이다. 성격이 서로 달라 **한 판정으로 묶으면 안 된다.**

| # | 표면 | 파일:행 | 산출물 | 현행 재시도 | 실패 시 |
|---|---|---|---|---|---|
| **S1** | 의도·DB 분류 | `routing/semantic_router.py:432` | `{intent, databases[]}` | **없음** | 첫 활성 DB 폴백 |
| **S2** | **DAG 분해** | `orchestration/intent_planner.py:542` | `{tasks[], clarification_needed}` | **없음** | **단일 `data_query` 폴백** |
| **S3** | 요구사항 추출 | `nodes/input_parser.py:218·318` | 요구사항 dict(12필드) | **2회**(고정 힌트) | `setdefault` 기본값 |
| **S4** | 의사 분류 | `routing/intent_confirm.py:55` | 승인/거부 의도 | **없음** | 결정적 폴백 |

공통점: **전부 `extract_json_from_response`(정규식) → `dict.get()` 수동 보정** 패턴이다
(전체 25곳/16파일 중 이 4곳).

---

## 2. Instructor 실측 — 문서가 아니라 실행으로 확인

### 2.1 평문 모델에 붙는가 — **붙는다**

`inspect`로 확인한 생성자:

```
Instructor.__init__(self, client: Any | None, create: Callable[..., Any],
                    mode: Mode = Mode.TOOLS, provider: Provider = Provider.OPENAI, ...)
```

**`client`가 `None` 허용이고 `create`는 임의 콜러블**이다. 즉 OpenAI SDK 없이도 쓸 수 있다.
(주의 — 실측으로 잡은 함정: `create`는 **`instructor.patch(create=..., mode=...)`로 감싼 것**이어야
한다. raw 콜러블을 넣으면 파싱 없이 원본 응답이 그대로 반환된다.)

`Mode` 38종 중 평문 모델용은 **`Mode.MD_JSON`**(`markdown_json_mode`) — 스키마를 프롬프트에 넣고
```json 블록을 파싱한다. tool-calling·JSON 모드가 **필요 없다.**

**실험**: KBGenAI 대역(평문 반환) `create`로 중첩 스키마(`Plan{tasks: list[TaskSpec]}`)를 요청.
1차 응답을 일부러 오염시켰다 — `agent: "prosess_query"`(오타 enum) · `order: "첫째"`(한국어 서수).

```
결과 타입: Plan          ← 파싱·검증 성공
총 LLM 호출: 2           ← 1차 실패 → 재질의 → 2차 성공
결과: {'tasks': [{'task_id':'t1','agent':'process_query','sub_query':'프로세스',
                  'depends_on': [], 'order': 1}]}
```

**한국어 프롬프트 환경에서 실제로 날 법한 두 오류를 둘 다 잡았다.**

### 2.2 재질의에 무엇을 되먹이는가 — **pydantic 오류 원문**

캡처한 2차 호출 추가 메시지(원문 그대로):

```
[user] Correct your JSON ONLY RESPONSE, based on the following errors:
2 validation errors for Plan
tasks.0.agent
  Input should be 'data_query', 'process_query', 'fault_diagnosis' or 'alarm_query'
  [type=literal_error, input_value='prosess_query', input_type=str]
tasks.0.order
  Input should be a valid integer [type=int_type, input_value='첫째', input_type=str]
```

**모델에 "무엇이 왜 틀렸는지"가 전달된다.** 현행 `input_parser`의 고정 힌트
(*"반드시 유효한 JSON만 출력하세요"*)와 질이 다르다.

### 2.3 기존 한국어 system 프롬프트와의 상호작용 — **⚠ 주의 지점**

실측 결과, Instructor는 **기존 system 메시지를 지우지 않고 그 뒤에 영어 블록을 이어붙인다**:

```
[system] 당신은 인프라 질의 라우터입니다. / ## intent 판단 우선순위 / … /
         As a genius expert, your task is to understand the content and provide
         the parsed objects in json that match the following json_schema: {…}
[user]   CPU 높은 서버 알려줘
         Return the correct JSON response within a ```json codeblock. not the JSON_SCHEMA
```

**두 가지가 확인된다** — ⓐ 한국어 프롬프트는 **보존**된다(덮어쓰지 않음)
ⓑ **마지막 user 메시지도 변형**된다(꼬리 문장 추가). §5.1의 리스크 근거다.

### 2.4 기타 실측값

| 항목 | 실측 |
|---|---|
| `max_retries` 의미 | **재시도 횟수** — `max_retries=2` → **총 3회 호출**(1 + 2) |
| 소진 시 예외 | `InstructorRetryException`(`n_attempts`, `failed_attempts` 보유) |
| async | **지원** — `AsyncInstructor` + async `create` 동작 확인 |
| 버전·Python | `instructor` **1.15.4** · `>=3.9,<4.0` (프로젝트 `>=3.11` ✔) |

---

## 3. 현행 결함 — Instructor가 실제로 고치는 것

정적 읽기가 아니라 코드 경로를 따라 확인한 것만 적는다.

| # | 결함 | 위치 | 현재 무슨 일이 나는가 | 타입 계약이 고치는가 |
|---|---|---|---|---|
| **F1** | **`intent`가 알려진 클래스 집합과 대조되지 않는다** | `semantic_router.py:439` `parsed.get("intent","data_query")` | 가드는 `fault_diagnosis`→`data_query` 강등 **하나뿐**(옵트인 보호용). 그 외 환각·오타 intent는 그대로 흘러 `cache_management`/`general_inference` 분기를 못 타고 **조용히 DB 조회 경로로 낙하** | **고친다** — `Literal[...]`로 즉시 검출 |
| **F2** | **`float()` 직접 호출** | `semantic_router.py:452` `float(db_entry.get("relevance_score", 0.5))` | LLM이 `"높음"`·`"0.9(높음)"` 반환 시 `ValueError` → 호출부 `except`가 삼켜 **분류 전체가 `active_db_ids[0]` 폴백**. **한 항목의 형식 오류가 분류 전체를 버린다** | **고친다** — 항목 단위 검증으로 격리, 나머지 DB는 생존 |
| **F3** | **무효 `db_id` 침묵 탈락** | `semantic_router.py:449` `if db_id in valid_db_ids` | 환각 db_id는 **로그 없이 사라진다**. 전부 무효면 `databases=[]` → *"라우팅 결과 없음"* 경고만 남고 기본 DB. **원인이 구분되지 않는다**(LLM이 못 골랐나 / 환각했나) | 부분 — 검증기로 사유 노출 가능 |
| **F4** | **DAG 분해 무재시도 침묵 폴백** | `intent_planner.py:542~550` | 파싱 실패·무효 시 **재시도 없이** 단일 `data_query`로 폴백. 사유는 로그에만 — **사용자 응답에 드러나지 않는다**. Known Mistakes *"침묵적 폴백 금지"* 저촉 | **고친다** — 되먹임 재시도 + 소진 시 사유 구조화 |
| **F5** | **`TaskSpec` 타입 계약 부재** | `state.py:246` `task_plan: list[dict]` | 키 오타·타입 불일치가 **런타임까지 생존**. `agent` 값이 오타면 소비처에서 미매칭 | **고친다**(78 R-13) |

> **F1·F2는 이번 검토에서 새로 확인한 것**이며 `plans/79`에도 `plans/78`에도 등재돼 있지 않다.

---

## 4. 통합 설계 — 어댑터 한 겹

FabriX 프로토콜을 **재구현하지 않는다.** 기존 `BaseChatModel`에 **위임**한다.

```
instructor(AsyncInstructor, Mode.MD_JSON, client=None)
   └─ create = patch(_lc_create)        # instructor.patch 로 감싼다(§2.1 함정)
        └─ _lc_create(messages=[dict], **kw) -> OpenAI 형 응답 대역
             ├─ dict 메시지 → LangChain 메시지 변환
             │    └─ is_kbgenai(llm) 이면 System 다음 빈 AIMessage 삽입 ← 8곳 중복을 여기로 흡수
             ├─ await llm.ainvoke(...)   # 기존 KBGenAIChat / FabriXAPIClient 그대로
             └─ AIMessage.content → .choices[0].message.content 형태로 감싸기
```

**요지**: 어댑터가 하는 일은 *메시지 형식 변환*뿐이다. 페이로드·인증·PII 훅
(`log_filter_block_if_any`)·`remove_llm_junk`·SSE는 **기존 클라이언트가 계속 담당**한다.
`pydantic-ai`가 요구했던 **프로토콜 재구현이 여기서는 발생하지 않는다.**

부수 효과: `is_kbgenai` 분기가 어댑터 한 곳으로 모여 **Plan 69 P2의 단일 출처 원칙에 오히려 부합**한다.

---

## 5. 리스크

### 5.1 ★ 최대 리스크 — 79 트랙 A와의 충돌 (측정 불가)

`plans/79` 트랙 A는 **바로 직전에** 라우터 프롬프트 구조를 고정했다 — 저신뢰 표현 허용(A-1),
신뢰도 3대역 예시(A-2), 클래스 정의 일원화(A-3), 예시 편중 완화(A-4), 절 배치(A-5).
**전부 한국어 few-shot 예시 기반**이다.

Instructor는 그 뒤에 **영어 지시문 + JSON Schema를 이어붙이고 user 메시지 꼬리도 바꾼다**(§2.3).
Known Mistakes가 정확히 이 실패 유형을 기록하고 있다 — *"프롬프트 강제가 프로필 few-shot 예시와
경쟁해 반복 실패하면 그 쿼리 형태는 결정적 조립 대상"*.

**이 영향은 실 LLM 호출 없이는 판정할 수 없고, 실 호출은 D-127 건별 승인 대상이다.**
→ **`semantic_router` 적용은 79 트랙 A 정확도 측정이 끝난 뒤로 미룬다.**
(79 트랙 A 자체가 아직 "구조 고정"까지만 완료돼 정확도 미측정 상태다.)

### 5.2 부차 리스크

| 리스크 | 내용 | 완화 |
|---|---|---|
| 토큰 증가 | 매 호출에 JSON Schema 동봉. 스키마가 정적이라 **KV 캐시 접두는 유지**되지만 입력 토큰은 는다 | 스키마를 작게(필드명 축약 금지 — 의미 손실). S1처럼 프롬프트가 이미 긴 곳은 이득/비용 대비 재검토 |
| 영어 재질의문 | 되먹임 문구가 영어(§2.2) | 한국어 튜닝 모델에 영향 여부는 **실측 필요**(D-127) |
| 재시도 = 지연·과금 | `max_retries=2` → 최대 3회 호출 | **`max_retries=1`(총 2회)로 시작**. 응답시간 목표(단순 <10s) 준수 확인 |
| 폐쇄망 반입 | 기반 의존 11종 중 **10종 기설치**(`openai 2.26.0`·`pydantic 2.12.5`·`tenacity 9.1.4`·`jinja2`·`jiter`·`rich`·`typer`·`requests`·`docstring-parser`·`pydantic-core`). **신규는 `aiohttp` 계열뿐** | 실측: `instructor` + `aiohttp`(+aiosignal·frozenlist·multidict·propcache·yarl·attrs·aiohappyeyeballs) ≈ **8~9 wheel** |

### 5.3 도입하지 않을 경우의 대안 — 자체 구현

Instructor의 핵심 가치는 **① 타입 검증 ② 오류 되먹임 재시도 ③ 스키마→프롬프트 자동 생성** 셋이다.
①②는 기보유 `pydantic`으로 **약 40~60줄**이면 된다. **③이 유일한 진짜 차별점**이고,
그것이 §5.1에서 **리스크이기도 하다** — 즉 우리가 가장 원하지 않는 기능이 유일한 차별점이다.

| 판단 기준 | 자체 구현 유리 | Instructor 유리 |
|---|---|---|
| 적용 표면이 1~2곳 | ✔ | |
| 프롬프트가 이미 정교하게 튜닝됨(S1) | ✔ | |
| 스키마가 중첩·복잡(S2 `TaskSpec`) | | ✔ 자동 JSON Schema가 프롬프트 수기 작성보다 정확 |
| 4개 표면 전부 통일 | | ✔ |

---

## 6. `pydantic-ai` 검토(78 §4.7.5)와의 대조 — 왜 판정이 다른가

| 축 | pydantic-ai | **Instructor** |
|---|---|---|
| 평문 모델 지원 | `PromptedOutput` 가능하나 | `Mode.MD_JSON` **가능** |
| 커스텀 클라이언트 | **`Model` ABC 서브클래스 필수** = 세 번째 FabriX 프로토콜 구현 | **`client=None` + 임의 `create`** = 형식 변환 어댑터만 |
| 에이전트 루프 | 프레임워크가 소유 → LangGraph와 경합 | **없음** — 라이브러리일 뿐 |
| 그래프 런타임 | `pydantic-graph` 필수 동반(LangGraph 중복) | 없음 |
| 신규 wheel | ~9(httpx2·httpcore2·truststore 등) | ~8~9(대부분 `aiohttp` 계열) |
| **판정** | **미채택** | **조건부 채택 가능** |

**차이의 본질**: pydantic-ai는 *프레임워크*라 LLM 접근 계층을 자기 것으로 요구했고,
Instructor는 *얇은 라이브러리*라 우리 계층 위에 얹힌다.

---

## 7. 권고

> **v2 정정(사용자 지적 2026-08-26)**: *"78번보다 79번 계획에 적용하는 것이 더 적절하냐?"*
> — **주제·파일 소유 기준으로는 79가 맞다.** v1이 **①** F1·F2를 소속 없이 "별도"로 분류하고
> **②** 1단계를 *"(78 R-13)"* 로 표기한 것은 부정확했다. 아래 §7.0에서 소유를 확정한다.

### 7.0 소유 확정 — 세 갈래이며 답이 서로 다르다

`plans/79` §6.1 파일 배치 실측 결과:

| 대상 | 파일 | 소유 계획 | 지금 착수 가능? |
|---|---|---|---|
| **F1**(intent 미검증)·**F2**(`float()` 전체 폐기) | `src/routing/semantic_router.py` | **79** ✔ (§6.1 명시) | **가능** — 프롬프트 미변경 · 신규 의존 0 · **D-127 불요** |
| **Instructor → 라우터**(S1) | `routing/` + `prompts/semantic_router.py` | **79** | **불가** — `plans/80` **Phase 0 미착수**(§7.1) |
| **Instructor → DAG 분해**(S2·F4·F5) | `src/orchestration/intent_planner.py` | **없음** ★ | **주인 결정 선행** |

**★ 세 번째가 진짜 미결이다.** `plans/78`은 **R-13으로 명시 배제**했고("어느 Wave도
`intent_planner`를 수정하지 않는다"), `plans/79` §6.1이 가진 것은
**`src/prompts/intent_planner.py`**(프롬프트 파일 · A-7)이지 **`src/orchestration/intent_planner.py`**
(노드 구현)가 **아니다**. 즉 **양쪽 계획 어디에도 소속이 없다** → §8 **U-5**.

### 7.1 왜 "79 소관"이 "지금 79에 넣는다"가 아닌가

`plans/80` §5가 착수 순서의 단일 출처이고 **Phase 0(지금) = 79 트랙 A 마감 — S-1·S-2·S-3**이며
**미착수**다. 그리고 §8 ⑧이 이를 *"리스크가 아니라 현재 상태"* 로 격상해 두었다 —
A-1으로 게이트가 처음 실동작하는데 **`relevance_score` 분포 실측(S-2)과 C-4 임계 정산이 없다.**

여기에 Instructor를 얹으면 **A-1의 효과·게이트 실동작·Instructor 영향 셋이 한 덩어리가 되어
원인 귀책이 불가능**해진다. 이것은 78이냐 79냐의 문제가 **아니라 측정 순서의 문제**다.

> S-1·S-2는 **실 LLM 호출 → D-127 건별 승인** 대상이다(plan 80 §3.3). 승인 없이 실행 금지.

### 7.2 ★ 판을 바꾸는 변수 — 라우터 평면 이동 (79 §8 ⑪ · plan 80 §9 J-8)

`plans/79` §8 ⑪(v11 신규)이 지적한다 — 라우터는 `create_llm()` = **워커 평면(FabriX)** 을 쓰는데,
**제어 평면은 vLLM**이다. 라우터를 제어 평면으로 옮기면:

| 풀리는 것 | 이유 |
|---|---|
| 트랙 C(logprob 신뢰도) | vLLM은 logprobs를 낸다 — 79 §8 ⑪ 명시 |
| **Instructor의 최대 리스크(§5.1) 소멸** ★ | vLLM은 **네이티브 tool-calling 지원** → `Mode.TOOLS` 사용 가능 → **영어 스키마 프롬프트 주입이 아예 없어진다.** §5.1은 `Mode.MD_JSON`(평문 모델용) 때문에 생긴 리스크다 |
| `pydantic-ai`(78 §4.7.5 ⑤) | 같은 트리거 — `OpenAIChatModel` 직결 |

**즉 결정 하나가 세 건을 동시에 푼다.** Instructor 도입을 79에서 판단할 때
**평면 이동 결정과 분리해서 보면 잘못된 비용 계산을 하게 된다.**

### 7.3 그래서 실제 권고 — 순서

| 순서 | 조치 | 소유 | 게이트 |
|---:|---|---|---|
| **①** | **F1·F2 결정적 가드** — intent 허용 집합 대조 + `float()` 항목 단위 격리 | **79** | 없음 — **지금 가능** |
| **②** | plan 80 **Phase 0**(S-1·S-2·S-3) 완료 | 79/80 | **D-127 승인** |
| **③** | **라우터 평면 이동 결정**(79 §8 ⑪) | 사용자 | 사용자 판단 |
| **④** | Instructor 적용 — **③의 결과로 모드가 갈린다**: vLLM이면 `Mode.TOOLS`(리스크 낮음), FabriX 유지면 `Mode.MD_JSON`(§5.1 리스크 유효) | 79 / U-5 | ②·③ 선행 |

### 7.4 ~~1단계~~ — `intent_planner._llm_decompose` · **소유 미정으로 보류**

- **가장 결함이 크다**: 재시도 0회 + 침묵 폴백(F4) + 타입 계약 없음(F5)
- **자동 JSON Schema의 값이 가장 크다**: `TaskSpec`이 중첩 구조라 프롬프트 수기 기술보다 정확
- **프롬프트가 변경 중이 아니다** — 79 트랙 A 충돌 없음(이 장점은 유효하다)
- 수용 기준: `max_retries=1` · 소진 시 **폴백 사유를 응답에 구조화 노출**(침묵 폴백 해소) ·
  기존 폴백 동작은 유지(회귀 0) · `TaskSpec` `BaseModel` 승격

### 7.5 `semantic_router`(S1) · **보류** — 근거는 §7.1

- **선행조건**: 79 트랙 A 분류 정확도 측정(D-127 건별 승인)
- 측정 전 적용하면 **79의 개선분과 Instructor의 영향이 분리되지 않는다** — 원인 귀책 불가

### 7.6 F1·F2 — **79 소관이며 지금 가능** (v1의 "별도" 분류를 정정)

- **F1**: `intent`를 알려진 클래스 집합과 대조하고 미상은 `data_query` 강등 + **로그**
- **F2**: `float()` 변환 실패를 **항목 단위로 격리**(해당 DB만 탈락, 분류 전체 폐기 금지)
- 둘 다 **신규 의존 0 · 수 줄**이며, 프롬프트를 건드리지 않으므로 79와 충돌하지 않는다

### 7.7 S3·S4 — 현행 유지

S3는 이미 2회 재시도가 있고, S4는 결정적 폴백이 설계 의도다. 통일을 위한 통일은 하지 않는다.

---

## 8. 미해결 · 사용자 결정 필요

| # | 항목 | 필요한 결정 |
|---|---|---|
| U-1 | **1단계 착수 여부** | R-13을 Instructor로 풀 것인가, 자체 구현(§5.3) 40~60줄로 풀 것인가 |
| U-2 | **폐쇄망 반입** | `instructor` + `aiohttp` 계열 8~9 wheel 보안 반입 절차 진행 여부 |
| U-3 | **정확도 측정** | 79 트랙 A 및 본 건 검증 모두 **실 LLM 호출 = D-127 건별 승인** 필요 |
| U-4 | **D 번호** | 채택 시 `docs/02_decision.md` 「채번 이력」 등재 — **D-170은 `plans/80`이 이미 예약**했으므로 채번 전 재확인 |
| **U-5** | **`src/orchestration/intent_planner.py`의 소유 계획** ★ | 78은 **R-13으로 명시 배제**, 79 §6.1은 **프롬프트 파일만** 보유 → **양쪽 어디에도 없다**. (a) 79 범위 확장 (b) 별도 계획 신설 (c) 78 R-13 환원 중 택일 |
| **U-6** | **라우터 평면 이동**(79 §8 ⑪ · plan 80 §9 J-8) | Instructor 모드(`TOOLS` vs `MD_JSON`)·트랙 C 재개·pydantic-ai 판정이 **모두 이 결정에 종속**(§7.2) |

## 9. 이 검토가 확인하지 못한 것 (명시)

- **실 LLM에서의 정확도 변화** — 전부 대역(fake `create`)으로 검증했다. 스키마 주입이 한국어
  few-shot과 경쟁하는지, 영어 재질의문이 효과적인지는 **측정하지 않았다**(D-127).
- **FabriX 실 응답 형태와 어댑터의 정합** — `AIMessage.content`가 콘텐츠 블록 리스트로 오는 경우
  (`coerce_content_text`가 다루는 사례)를 어댑터에서 재현해야 한다. 미검증.
- **토큰·지연 실측** — 스키마 동봉에 따른 증가분 미측정.
