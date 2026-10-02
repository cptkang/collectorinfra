# Spec: 라우터 intent/DB 2단 분리 (WU-D2 / `plans/79` 트랙 B)

> **보류 해제**: 트랙 B는 2026-08-27 인터뷰에서 **이월 유지**로 결정됐다(D-170 후속).
> 같은 날 사용자 지시로 **보류를 해제**하고 착수한다. 해제의 대가는 §「미검증으로 남는 것」에
> 명시하며, 이것이 이 문서의 가장 중요한 절이다.

## Objective

라우터가 한 번의 LLM 호출로 `intent`와 `databases`를 함께 정하는 구조를 **두 단계로 나눈다**.

```
1단계  intent 분류   → 라벨 하나 (label-only 성립 · 첫 토큰이 라벨)
2단계  DB 선택       → {"databases":[…]} 배열 (멀티 DB 불변식 유지)
```

**왜**(79 B-0): ① 단일 책임 ② **신뢰도를 두 축으로 분리 측정** ③ 프롬프트 축소
④ **`label-only` 이득을 §1.1 불변식 위배 없이 취득하는 유일한 경로**(1단계에 한해 순수
label-only가 성립하고, 2단계가 배열로 멀티 DB를 유지한다).

**이번 범위**: 위 구조 + 플래그 + 신뢰도 2축(자기보고) + 조기 차단(기본 off).
**이번 범위 밖**: logprob 신뢰도(트랙 C · vLLM) · 임계 정산(C-4) · 실 LLM 정확도 측정(S-1/G-BILL).

## Tech Stack

기보유만 — 신규 라이브러리 **0건**. Python 3.12/3.13 · pydantic v2 · pydantic-settings ·
LangChain `BaseChatModel` · 기존 `instructor` 어댑터(E-3) · pytest.

## Commands

```bash
# 대상 스위트
python -m pytest -q tests/test_semantic_routing tests/test_routing_eval tests/test_structured_output

# 전체 회귀 (e2e는 playwright 미설치 — 기준선과 동일하게 제외)
python -m pytest -q --ignore=tests/e2e

# 계층 규칙
python scripts/arch_check.py --ci

# 골든셋 하네스(실 호출은 D-127 승인 후 — 지금은 실행하지 않는다)
python scripts/eval_routing.py --dry-run
```

## Project Structure

| 경로 | 역할 | 이 작업에서 |
|---|---|---|
| `src/prompts/semantic_router.py` | 프롬프트 정본(79 단독 소유) | **절을 명명 상수로 추출** + 1단·2단 템플릿 신설 |
| `src/routing/semantic_router.py` | 분류 경로(79 단독 소유) | `_llm_classify_two_stage()` 신설 · 플래그 위임 |
| `src/routing/schemas.py` | 출력 계약 | `IntentDecision`·`DatabaseSelection` 추가 |
| `src/config.py` | 설정 | `RouterConfig`(env_prefix `ROUTER_`) 신설 |
| `tests/test_semantic_routing/test_two_stage.py` | 2단 경로 검증 | **신규** |
| `tests/test_semantic_routing/test_prompt_byte_identity.py` | 기존 렌더 불변 고정 | **신규** |

## Code Style

기존과 동일 — 한국어 docstring(Args/Returns), 결정적 가드에 **왜**를 주석으로 남긴다.

```python
def _intent_confidence(raw: object, *, source: str) -> Optional[float]:
    """1단계 의도 신뢰도를 얻는다.

    `source="self_report"`는 **잠정**이다 — 모델이 스스로 매긴 값이라 교정 기반이 없다
    (S-3가 `MIN_RELEVANCE_SCORE=0.3`에 대해 고정한 문제와 같은 성격).
    라우터 평면 이동 후 `source="logprob"`으로 갈아끼우면 **이 함수 하나만** 바뀐다 — 교체점을
    한 곳에 모아 두는 것이 트랙 C 재개 비용을 줄인다.
    """
```

- 신규 플래그는 **기본값이 현행 동작**이고 **기동 시 1회 해석**(78 P14 — 요청 시점에 출력 형식을
  바꾸면 프롬프트 접두부가 흔들려 KV 캐시가 무효화된다).
- 프롬프트 텍스트는 **사본을 만들지 않는다**(D-053) — 절을 명명 상수로 추출해 양쪽이 조립한다.
- 강등·탈락·차단은 **구조화된 사유**로 남긴다.

## Testing Strategy

pytest. 신규 테스트는 `tests/test_semantic_routing/`.

| 층위 | 무엇 | 이 작업에서 |
|---|---|---|
| 계약 | 스키마·바이트 동일·플래그 off 비트동일 | **전부 여기** |
| 단위 | 2단 호출 순서·불변식 보존·조기 차단·신뢰도 2축 | **전부 여기** (mock LLM) |
| e2e | 실 LLM 정확도·지연·토큰·KV 캐시 | **하지 않는다** — G-BILL · D-127 |

- LLM은 **mock 전용**. 호출 횟수를 단언한다(조기 차단 시 2단계 **0회**).
- **`tests/mocks/fabrix_kbgenai_mock.py`를 재사용**한다(WU-05 하네스 자산 · 사본 금지).
- 회귀 판정은 **전체 실패 수가 기준선(41 failed)과 동일**함으로 한다.

## Boundaries

**Always**
- `ROUTER_TWO_STAGE_ENABLED=false`(기본) → **기존 경로가 비트동일**
- `_build_router_prompt()` 렌더 결과가 **골든 문자열과 바이트 동일**
- **멀티 DB 선택·`sub_query_context` 분리 보존**(79 §1.1 불변식 — 축소는 회귀)
- `python scripts/arch_check.py --ci` exit 0
- 2단계도 **LLM**이 판단한다(D-004)

**Ask first**
- 조기 차단 임계 기본값을 0이 아닌 값으로 두는 것
- 기존 프롬프트 **문구** 변경(현재는 추출만 하고 문구 무수정)

**Never**
- 실 LLM 호출 · `RUN_E2E=1` 설정 (**D-127** — 건별 승인)
- **위치 힌트로 DB를 결정적으로 고르기**(`db_registry.yaml:22-25` · D-004) — 폴백·보강만
- 멀티 DB를 단일 선택으로 축소 (§1.1)
- 78 소유 자산 수정 (`plans/80` §6)

## Success Criteria

`plans/79` 트랙 B 수용 기준 7항 중 **이번에 검증 가능한 것**:

| # | 기준 | 이번 |
|---|---|---|
| 1 | 2단 경로가 단일 경로와 동일 분류 결과를 내는 비율 | ✗ **실 LLM 필요** — 3-B |
| 2 | **멀티 DB 선택·`sub_query_context` 분리 보존** | ✅ mock으로 단언 |
| 3 | 사용자 DB 직접 지정이 **intent와 무관하게** 반영 | ✅ 2단계 프롬프트가 intent와 독립임을 단언 |
| 4 | intent·DB **신뢰도가 각각 산출**되고 임계가 각각 적용 | ◐ **자기보고로 산출**·임계 각각 적용. logprob은 ✗ |
| 5 | **조기 차단 동작** — 저신뢰 시 2단계 호출 없이 중단 | ✅ mock 호출 0회 단언 (**기본 off**) |
| 6 | 컨텍스트 대역폭 손실 측정 | ✗ **실 LLM 필요** — 3-B |
| 7 | 지연·토큰·KV 캐시 계측 / **플래그 off 회귀 0** | 계측 ✗ / 회귀 0 ✅ |

**추가(이 스펙 고유)**
- `_build_router_prompt()` 렌더가 **추출 전후 바이트 동일**하다.
- 1단계 출력의 **첫 토큰이 라벨**이다(C-0 요구 — 라우터 평면 이동 시 그 자리의 logprob이 곧 신뢰도).
- 신뢰도 소스 교체점이 **함수 하나**다(`ROUTER_CONFIDENCE_SOURCE`).

## 미검증으로 남는 것 — **보류 해제의 대가** ★

이 절이 이 문서에서 가장 중요하다. 구현이 끝나도 **아래는 여전히 모른다**.

| # | 미검증 항목 | 왜 |
|---|---|---|
| M-1 | **트랙 B가 이득인지 손해인지** | 존재 이유(B-0 ④ label-only)의 근거가 **모델 크기 종속**이다 — 논문 실측 1.5B **−33.6** / 9B **+11.2**. FabriX 모델이 어느 쪽인지 모른다 |
| M-2 | **2단 분리로 분류가 나빠지는지** | B-2 「컨텍스트 대역폭 손실」 — 2단계가 1단계의 내부 표현을 못 본다. 완화책(intent별 절 전달)을 넣었으나 **효과 미측정** |
| M-3 | **조기 차단 임계값** | 자기보고 값에 근거가 없다(S-3와 동일). 그래서 **기본 off**로 두었다 |
| M-4 | **비용** | 호출 1회 → 2회. 지연·토큰·KV 캐시 접두부 분기 영향 미측정 |

**따라서 `ROUTER_TWO_STAGE_ENABLED=true`는 S-1·S-2 이후에만 켠다.** 이 스펙은 *구조를 세우는 것*이지
*켜도 된다고 판정하는 것이 아니다.* 플래그를 켜는 판정은 3-B(WU-05·06 이후)에 속한다.

## Open Questions

| # | 질문 | 잠정 처리 |
|---|---|---|
| Q1 | 1단계를 **순수 label-only**(라벨만)로 할지, **라벨 선행 하이브리드**(라벨 + 다음 줄 JSON)로 할지 | **하이브리드**로 간다. 순수 label-only는 자기보고 신뢰도를 실을 자리가 없어 B-1-5·B-2-1이 불성립한다. 첫 토큰은 여전히 라벨이므로 C-0(logprob 자리)은 충족된다 |
| Q2 | 2단계에 **어느 intent 절**을 넣을지 | 해당 intent의 절만(B-2 완화). `data_query`는 절이 없으므로 DB 선택 절만 |
| Q3 | `general_inference`·`cache_management`는 DB가 불필요하다 — 2단계를 부를 것인가 | **부르지 않는다.** 호출 2회 비용의 상당분이 여기서 절감된다(B-2-1 조기 차단과 같은 취지, 다만 신뢰도가 아니라 **의도 종류**로 판정하므로 근거가 확실하다) |
