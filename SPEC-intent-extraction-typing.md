# Spec: intent-extraction-typing

> Module id: `intent-extraction-typing` · Map: `CAPABILITY-MAP-intent-extraction.md`
> WU-08·09·10 (`plans/80` §5.2 차수 2) · 설계: `plans/79` E-3.5
> **Depends on**: `structured-output-backend`(완료) · `router-output-contract`(완료 — WU-09 전제)
> **공통 사항**(Tech Stack · Commands · Structure · Style · Boundaries)은 맵 참조.

## Objective

WU-07이 만든 어댑터를 **세 개의 의도 추출 표면**에 적용해, LLM 출력을 타입 계약으로 받고
검증 실패를 모델에 되먹인다. 세 표면 모두 현재 **정규식 JSON 추출 + `dict.get()` 수동 보정**이다.

| WU | 표면 | 현행 결함 |
|---|---|---|
| **WU-08** | `orchestration/intent_planner.py::_llm_decompose` | **재시도 0회** · 실패 시 단일 `data_query`로 **침묵 폴백**(F4) · `TaskSpec` 타입 계약 없음(F5) |
| **WU-09** | `routing/semantic_router.py::_llm_classify` | 수동 보정. E-1·E-2가 코드 가드로 막았으나 **계약이 없다** |
| **WU-10** | `nodes/input_parser.py` 2개 함수 | 재시도가 **고정 힌트 한 문장** · 판정이 `query_targets` 존재 여부뿐 · **두 함수 비대칭** |

## 설계 결정

**D1. 스키마는 소유 모듈 옆에 둔다** — `src/orchestration/schemas.py` · `src/routing/schemas.py` ·
`src/nodes/schemas.py`. 공용 `src/schemas/` 신설은 계층을 가로지르므로 하지 않는다.

**D2. 열거형은 기존 정본에서 파생한다**(D-053 사본 금지).
`TaskSpec.agent`는 **`SUBAGENT_REGISTRY` 키**가 정본이다(실측: `alarm_query` · `cache_management` ·
`data_query` · `general_inference` · `process_query` · `synonym_registration`).
`fault_diagnosis`는 **그 집합에 없다** — 서브에이전트가 아니라 그래프 노드이기 때문이다.
`RouterDecision.intent`는 **`allowed_intents()`**(WU-02 정본)에서 파생한다.

**D3. 중첩 항목은 느슨하게 둔다.** `filter_conditions`·`query_targets` 등은 `list[dict]`로 받는다.
현재 자유 형식이라 엄격 모델을 씌우면 **지금 통과하던 출력이 거부**된다. 이번 목표는
*상위 필드의 존재·타입 계약*이지 전면 스키마화가 아니다(회귀 0 우선).

**D4. 플래그 off면 코드 경로가 종전과 동일하다.** `try_structured_call`이 `None`을 돌려주면
기존 파싱으로 내려간다. **기존 경로를 지우지 않는다** — off가 상시 존재한다.

**D5. F4(침묵 폴백) 해소 범위** — `StructuredOutputError` 발생 시 ⓐ `warning` 로그
ⓑ 반환 dict에 **구조화 사유**(`degraded: [{stage, reason, attempts}]`) 부착 ⓒ 기존 폴백 유지.
`state.py:55`의 `unresolved: [{field, reason}]` 관례를 따른다.
**최종 사용자 응답 문구까지 싣는 것은 범위 밖**(`output_generator` 변경 필요 → Open Questions).

**D6. WU-10은 두 함수에 같은 스키마를 쓴다.** 이것이 `synonym_registration` 기본값 비대칭
(`_parse_natural_language`에는 있고 `_parse_natural_language_with_csv`에는 없다 — 실측)을
**구조적으로 해소**한다. `_sheet_name`은 LLM 출력이 아니므로 모델 밖에서 부여한다.

> **정정**: `plans/79` v14가 이 표면을 "12필드"로 적었으나 실측은 **10키**
> (`original_query` + `setdefault` 9종)다. 논지(라우터보다 스키마가 크다)는 유지된다.

## Scope

| 파일 | 계층 | 변경 |
|---|---|---|
| `src/orchestration/schemas.py` **신규** | orchestration | `TaskSpec` · `DecomposedPlan` |
| `src/orchestration/intent_planner.py` | orchestration | `_llm_decompose` 구조화 경로 + 강등 사유 |
| `src/routing/schemas.py` **신규** | infrastructure | `RouterDecision` · `RouterDatabase` |
| `src/routing/semantic_router.py` | infrastructure | `_llm_classify` 구조화 경로 |
| `src/nodes/schemas.py` **신규** | application | `ParsedRequirements` |
| `src/nodes/input_parser.py` | application | 두 함수 구조화 경로(**대칭**) |

**범위 밖**: 프롬프트 텍스트 변경 · `output_generator` · 나머지 22개 `extract_json` 호출부.

## Testing Strategy

`tests/test_structured_output/` 신설. **대역 LLM으로 전부 검증**(실 LLM 0 · D-127 무관).
각 표면에 대해 ①플래그 off 동등성 ②정상 파싱 ③검증 실패→되먹임→복구 ④소진→사유 노출.

## Success Criteria

| # | 조건 |
|---|---|
| **S1** | `STRUCTURED_OUTPUT_BACKEND=none`에서 세 표면 모두 **기존 경로와 결과 동일** |
| **S2** | `TaskSpec.agent` 허용값 == `SUBAGENT_REGISTRY` 키 집합 (사본 금지 단언) |
| **S3** | `RouterDecision.intent` 허용값 == `allowed_intents()` (플래그 양쪽 상태) |
| **S4** | 백엔드 on에서 잘못된 `agent`(오타)가 **되먹임 재질의로 교정**된다 |
| **S5** | `_llm_decompose` 소진 시 **`degraded` 사유가 반환 dict에 실린다**(F4 — 침묵 금지) |
| **S6** | `_llm_decompose` 소진 시에도 **기존 단일 `data_query` 폴백은 유지**(가용성) |
| **S7** | `input_parser` 두 함수가 **같은 스키마**를 쓴다 — `synonym_registration` 키가 **양쪽 모두** 존재 |
| **S8** | 라우터 구조화 경로가 **멀티 DB 2건 이상을 보존**한다(§1.1 불변식) |
| **S9** | 전체 회귀 기준선 동일 · `arch_check --ci` exit 0 |

## Open Questions

| # | 항목 |
|---|---|
| Q1 | **강등 사유의 사용자 노출** — 이번엔 `degraded` 필드까지. 응답 문구 반영은 `output_generator` 변경이 필요해 별건으로 남긴다 |
| Q2 | 나머지 22개 `extract_json_from_response` 호출부 적용 여부 — 효과 측정(D-127) 후 판단 |
