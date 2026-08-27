# Spec: structured-output-backend

> Module id: `structured-output-backend` · Map: `CAPABILITY-MAP-intent-extraction.md`
> WU-07 (`plans/80` §5.2 차수 2 · **G-WHEEL 해제됨** — 사용자 승인 2026-08-27)
> 설계: `plans/79` E-3.2~3.4·3.6 · 실측: `docs/instructor_intent_extraction_review.md`

## Objective

LLM 응답을 **타입 계약**으로 받고, 검증 실패 시 **오류를 모델에 되먹여 재질의**하는 백엔드를 만든다.
소비처(라우터·DAG 분해·요구사항 추출)는 **같은 인터페이스**를 쓰고 백엔드 교체에 영향받지 않는다.

**해결하는 것**: 현행은 정규식 JSON 추출 + `dict.get()` 수동 보정이고(16파일 25곳),
검증 오류를 모델에 알려주지 않는다. `intent_planner`는 재시도조차 없이 **침묵 폴백**한다.

## 왜 instructor인가 (기각 후보와의 차이)

`pydantic-ai`는 `Model` ABC 재구현을 요구해 **세 번째 FabriX 프로토콜 구현**이 생긴다(78 §4.7.5).
`instructor`는 `Instructor(client=None, create=...)`로 **임의 콜러블**을 받으므로 어댑터 한 겹이면
되고, **기존 `BaseChatModel`에 위임**한다 — 프로토콜 재구현이 0이다.

## ★ 핵심 설계 — 프롬프트 주입을 한국어로 대체 (실측 완료)

`Mode.MD_JSON` 기본 핸들러는 한국어 프롬프트 뒤에 **영어 스키마 블록**을 붙이고 **추가 user 메시지**를
덧붙인다. 트랙 A가 고정한 few-shot 구조와 경쟁할 수 있다.

→ `@register_mode_handler(Provider.OPENAI, Mode.MD_JSON)`로 **커스텀 핸들러를 등록**해 제거한다.
**격리 venv 실측 확인**: `prepare_request`·`handle_reask` 오버라이드 모두 동작하고
**영어 잔존 0**(`"genius expert"`·`"Correct your JSON"` 둘 다 False), 파싱·재질의는 정상.

## 구조

```
build_structured_client(llm) -> AsyncInstructor
  └ create = instructor.patch(_lc_create, mode=...)   ← patch 필수(raw 콜러블이면 파싱 안 됨)
      └ _lc_create(messages=[dict], **kw)
          ├ dict → LangChain 메시지 (is_kbgenai면 System 다음 빈 AIMessage)
          ├ await llm.ainvoke(...)      ← 기존 KBGenAIChat / FabriXAPIClient / ChatOpenAI 그대로
          └ AIMessage.content → coerce_content_text → OpenAI 응답 shape 래핑
```

**모드 자동 선택**

| LLM | 모드 |
|---|---|
| `KBGenAIChat` · `FabriXAPIClient` (평문 · tool-calling 부재) | `Mode.MD_JSON` + 한국어 핸들러 |
| `ChatOpenAI`(vLLM · 네이티브 지원) | `Mode.TOOLS` — 스키마 주입 자체가 불요 |

이 분기 덕에 **평면 이동이 선행조건이 아니라 최적화 항목**이 된다(80 J-9).

## Scope

| 파일 | 계층 | 내용 |
|---|---|---|
| `src/clients/instructor_adapter.py` **신규** | infrastructure | 어댑터 · 한국어 핸들러 · 모드 선택 · 팩토리 · 예외 |
| `src/config.py` | config | `STRUCTURED_OUTPUT_BACKEND` · `STRUCTURED_OUTPUT_MAX_RETRIES` |
| `pyproject.toml` | — | `[project.optional-dependencies] structured = ["instructor>=1.15.4"]` |

**범위 밖**: 소비처 적용(→ `intent-extraction-typing`) · 프롬프트 텍스트 변경.

## 설계 결정

**D1. 기본값은 `none`** — 켜기 전까지 현행 동작 비트동일. 기동 시 1회 해석(78 P14).
**D2. lazy import + graceful 강등** — `instructor` 미설치 시 앱은 기동되고 기존 경로로 내려가되
**강등 사실을 로그로 남긴다**(침묵 금지). optional extra 전례: `semantic`·`stl`·`deepagents`.
**D3. `coerce_content_text` 재사용 필수** — 실 모델이 content를 **콘텐츠 블록 리스트**로 주는
사례가 실측돼 있다(`utils/json_extract.py` · 2026-08-04 E1). `str` 가정 시 여기서 깨진다.
**D4. `is_kbgenai` 규약을 어댑터로 흡수** — 현재 8곳 산재. 소비처가 늘어도 한 곳만 안다(Plan 69 P2).
**D5. 오류 본문은 번역하지 않는다** — pydantic 생성 영어(`Input should be a valid number`)에
필드 경로·받은 값이 실려 교정에 충분하다. 과도한 가공은 정보를 잃는다.

## Testing Strategy

`tests/test_clients/test_instructor_adapter.py` **신설**. **대역 `create`/대역 LLM으로 전부 검증**
— 실 LLM 호출 0(D-127 무관). 대역은 `docs/instructor_intent_extraction_review.md`의 probe 구성을 따른다.

## Success Criteria

| # | 조건 |
|---|---|
| **S1** | `STRUCTURED_OUTPUT_BACKEND=none`(기본)에서 **어댑터가 관여하지 않는다** |
| **S2** | `instructor` **미설치 시뮬레이션**에서 임포트가 앱을 죽이지 않고 **강등 로그**가 남는다 |
| **S3** | 전송 메시지에 `"genius expert"`·`"Correct your JSON"`이 **둘 다 없다** |
| **S4** | 스키마 블록이 **한국어 지시문과 함께 system 말미**에 온다(기존 system 내용 보존) |
| **S5** | 검증 실패 시 재질의 메시지가 **한국어**이고 **실패 필드 경로 + 받은 값**을 포함한다 |
| **S6** | KBGenAI 대역에서 **System 다음 빈 `AIMessage`** 가 삽입된다 |
| **S7** | content가 **콘텐츠 블록 리스트**로 와도 파싱된다(`coerce_content_text` 경유) |
| **S8** | `ChatOpenAI` 계열이면 `Mode.TOOLS`, 평문 계열이면 `Mode.MD_JSON`이 선택된다 |
| **S9** | 재시도 소진 시 **구조화된 예외**(시도 횟수·마지막 오류)가 오르고 삼켜지지 않는다 |
| **S10** | `arch_check --ci` exit 0 (`src.clients` = infrastructure 정합) |
| **S11** | 전체 회귀 기준선 동일 |

## Open Questions

- `max_retries` 기본값은 **1**(총 2회 호출)로 시작한다. 라우터 지연 목표(단순 <10s) 때문이며,
  실측 후 조정한다. 값 변경은 「Ask first」 대상.
