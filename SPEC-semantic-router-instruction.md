# Spec: 시멘틱 라우터 분류 지시문 개선 (Plan 79 트랙 A)

> **출처 계획**: `plans/79-semantic-routing-improvement.md` v9 — 트랙 A
> **관련 결정**: D-004(LLM 전용 시멘틱 라우팅) · D-035(결정적=판단·LLM=보조) · D-127(과금 API 승인 게이트)
> **신규 결정 예약**: D-169(계획 79 소관)
> **작성일**: 2026-08-26 | **상태**: **승인 완료 — 구현 중**(v3)

---

## 0. Scope Check — 단일 capability

79 전체는 트랙 A~D로 나뉘지만, **이번 구현 대상은 트랙 A 하나**다. capability map은 만들지 않는다.

**왜 A만인가 — 실측 근거:**

| 트랙 | 착수 가능 | 근거 |
|---|---|---|
| **A** 지시문 개선 | **가능** | 프롬프트·테스트만. 외부 의존 없음 |
| B intent/DB 2단 분리 | 보류 | C 선행(조기 차단이 신뢰도에 종속) |
| C 신뢰도 인프라 | **불가 — 라우터 평면 이동 후 이월**(v3 확정) | **사용자 확인: FabriX = KBGenAI 모드**. 요청·응답 스펙에 logprobs 자리 없음(§0.1) |
| D label-only | 배제 | 79 §1.1 멀티 DB 불변식 위배 |

### 0.1 신뢰도 소스(logprobs) 가용성 — **FabriX 기준 재실측**(v2)

> **v1 오류 정정**: 사용자 확인(2026-08-26) — *"gemini를 사용하지 않는다. fabrix를 이용한다."*
> `.env`의 `LLM_PROVIDER=gemini`만 보고 Gemini로 판단했으나 **운영은 FabriX**다. 재실측한다.

FabriX는 **두 모드**가 있고(`src/llm.py:246-287`), `fabrix_client_key` 유무로 갈린다.

| 모드 | 요청 스펙 | 응답 | logprobs |
|---|---|---|---|
| **KBGenAIChat**(SDS 전용 · `client_key` **있을 때**) | `{modelId, contents, isStream, isRagOn, executeRagFinalAnswer, executeRagStandaloneQuery, systemPrompt}` (`fabrix_kbgenai.py:100-108`) | `{status, content}` — **텍스트만**(`:132-135`) | **원천 불가** — 요청에 파라미터 자리가 없고 응답이 문자열뿐. SDS 자체 추상화 API지 OpenAI 스펙이 아니다 |
| **FabriXAPIClient**(OpenAI 호환 · `client_key` **없을 때**) | `{model, messages, temperature, stream}` — `_build_payload`가 **`**kwargs`를 받지만 무시**(`fabrix_client.py:120-136`) | `data["choices"]` — **OpenAI 형태**(`:143`) | **현재 미지원, 확장 가능성 있음** — 클라이언트가 전달·파싱을 안 할 뿐 스펙은 OpenAI 호환. 백엔드 지원 여부는 **실호출로만** 확인(D-127 승인 대상) |

**Gemini와의 결정적 차이**: Gemini는 **라이브러리 레벨에 파라미터가 없어**(`ChatGoogleGenerativeAI`
logprob 관련 필드 0건) 원천 불가였다. FabriX **OpenAI 호환 모드는 클라이언트 확장으로 시도할 수
있다** — 즉 트랙 C가 "완전 불가"에서 **"조건부 가능"** 으로 바뀐다.

**⚠ 어느 모드인지 확인할 수 없다.** `.encenv`(암호화)에 `LLM_FABRIX_CLIENT_KEY` **키가 존재**하나
값을 볼 수 없다. 값이 있으면 KBGenAI(불가), 비어 있으면 OpenAI 호환(조건부 가능).
→ **Q0 확정(사용자 2026-08-26): KBGenAI 모드.** 따라서 **트랙 C의 logprob 신뢰도는 현행 경로에서
불가능**하며 라우터 평면 이동 후로 이월한다. 이번 구현 범위에서 제외.

> **참고 — 라우터가 쓰는 LLM**: `semantic_router`는 `create_llm()`을 호출하고, 그 docstring은
> *"**워커(데이터 평면)** LLM 인스턴스를 생성한다"* 이다. D-037상 데이터 평면 = FabriX,
> 제어 평면 = vLLM(Qwen3.5-9B). **논문 벤치마크가 쓴 `top_logprobs`는 vLLM 것**이므로,
> 제어 평면에서는 되지만 **라우터가 쓰는 워커에서 되는지는 별개 문제**다.

---

## 1. Objective

`src/prompts/semantic_router.py`(367줄)의 분류 지시문을 정비해 **저신뢰 신호가 시스템에 도달**하게 하고,
**클래스 정의·예시의 구조적 결함**을 제거한다.

**사용자**: 이 에이전트에 질의하는 운영자. 라우팅이 틀리면 엉뚱한 DB를 조회한 결과를 받는다.

**성공의 모습**: 라우터가 *"이 DB는 관련이 약하다"* 를 **표현할 수 있게** 되고, 클래스별 예시 편중이
해소되며, 이 상태가 **테스트로 고정**된다.

### 1.1 해결하는 문제 (79 §3.5 감사 결과)

| # | 발견 | 조치 |
|---|---|---|
| ① | **저신뢰가 구조적으로 표현 불가** — 규칙 5가 *"0.3 미만은 포함하지 마세요"* 로 모델에게 미리 버리게 지시 → 코드 게이트 `>= 0.3`가 **설계상 no-op** | **A-1** |
| ② | 예시 `relevance_score`가 **0.8~1.0만**(1.0×3 · 0.95×5 · 0.9×5 · 0.8×1). 규칙 4가 정의한 3개 대역 중 상위 하나만 존재 | **A-2** |
| ③ | 「출력 형식」 클래스 정의 **3개** vs 전용 판단 절 **5개** vs 예시 등장 **5개** | **A-3** |
| ④ | `alarm_query` 편중 — 예시 **7/19(37%)** + 유일한 전용 패턴 절 | **A-4** |
| ⑤ | `fault_diagnosis`가 *"최우선 검토"* 인데 파일 맨 끝(`:339`), 우선순위 절(`:253`)보다 뒤 | **A-5** |
| ⑦ | `intent_planner` 프롬프트(211줄)와 우선순위 규칙 중복·상충 | **A-7** |

**범위 밖(이번에 하지 않는다)**:

- **A-6 `unknown` 클래스 도입** — 79 §8 미해결 ①(의미 매핑) 미확정. **사용자 결정 선행**
- **A-3의 `synonym_registration` 항목** — 실측 결과 **결함이 아니다**. 이 intent는 LLM이 산출하지
  않고 `routing/semantic_router.py:90-107`이 `parsed_requirements`를 보고 **결정적으로 라우팅**한다
  (멀티턴 승인 흐름). 프롬프트에 없는 것이 정상 → **79 §3.5③ 서술을 정정**해야 한다
- **분류 정확도 측정** — 실 LLM 호출은 D-127 과금 게이트(건별 승인). 이번은 **구조 고정**까지

---

## 2. Tech Stack

| 항목 | 값 |
|---|---|
| 언어 | Python 3.11+ |
| LLM 프레임워크 | LangChain 1.x · LangGraph 1.x |
| 운영 LLM | **FabriX**(사용자 확인) — 모드는 Q0 미확정. `.env`의 `LLM_PROVIDER=gemini`는 개발값으로 보이며 운영과 다르다 |
| 테스트 | pytest |
| 대상 파일 | `src/prompts/semantic_router.py` · `src/prompts/intent_planner.py` |

**신규 의존성 없음.**

---

## 3. Commands

```bash
# 라우팅 테스트 (핵심 회귀)
.venv/bin/python -m pytest tests/test_semantic_routing/ -q

# 신규 프롬프트 구조 테스트
.venv/bin/python -m pytest tests/test_semantic_routing/test_router_prompt_structure.py -v

# 전체 회귀 (본체 + noise_gate)
.venv/bin/python -m pytest -q

# 아키텍처 검사
.venv/bin/python scripts/arch_check.py --ci

# 분류 정확도 평가 — ⚠ D-127 건별 승인 필요 (이번 범위 밖)
# RUN_E2E=1 .venv/bin/python scripts/eval_text2sql.py
```

---

## 4. Project Structure

```
src/prompts/semantic_router.py        → 대상 프롬프트 (367줄)
src/prompts/intent_planner.py         → 중복 정리 대상 (211줄)
src/routing/semantic_router.py        → 소비자 (MIN_RELEVANCE_SCORE 게이트 :276)
src/orchestration/subagents.py        → 소비자 (동일 게이트 :166)
tests/test_semantic_routing/          → 기존 라우팅 테스트 (24개)
  └ test_router_prompt_structure.py   → 신규: 프롬프트 구조 불변식
```

---

## 5. Code Style

이 저장소의 프롬프트는 **모듈 상수 + 한국어 마크다운 본문**이고, 변경 이유를 주석으로 남긴다.

```python
# 판단 규칙 4의 세 대역(확실/가능/약함)에 각각 예시를 둔다 — 예시가 지시문을 이기므로
# 상위 대역만 보여주면 모델이 중·저 신뢰를 산출하지 않는다(Plan 79 §3.5②).
ROUTER_SYSTEM_TEMPLATE = """...
## 판단 규칙

3. relevance_score는 0.0~1.0 사이의 관련도 점수입니다.
4. 확실한 매칭이면 0.8 이상, 가능성 있는 매칭이면 0.5~0.8, 약한 연관이면 0.3~0.5를 부여합니다.
5. 관련도가 낮아도 **그대로 값을 부여해 포함하세요** — 제외 판단은 시스템이 합니다.
..."""
```

**규약**: 한국어 서술 · 변경 근거를 계획 번호와 함께 주석 · few-shot은 JSON 코드펜스 · 기존 들여쓰기 유지.

---

## 6. Testing Strategy

**실 LLM 없이 검증한다**(폐쇄망 CI 전제 · D-127). 프롬프트는 **문자열**이므로 구조를 직접 단언한다.

| 레벨 | 대상 | 위치 |
|---|---|---|
| 단위 | 프롬프트 구조 불변식(아래 §8) | `tests/test_semantic_routing/test_router_prompt_structure.py` (신규) |
| 단위 | 기존 라우터 동작 24개 | `tests/test_semantic_routing/test_semantic_router.py` (무회귀) |
| 통합 | 전체 스위트 | `pytest -q` |
| **범위 밖** | 분류 정확도 | `scripts/eval_text2sql.py` — D-127 승인 후 별도 |

**커버리지 기대**: 신규 테스트는 §8 성공 기준 전 항목을 1:1로 단언한다.

---

## 7. Boundaries

**Always do**
- 프롬프트 변경 시 **예시도 함께** 갱신한다 — 예시가 지시문을 이긴다(Known Mistakes)
- 변경마다 `pytest tests/test_semantic_routing/` 통과 확인
- 계층 규칙 준수 — `arch_check.py --ci` exit 0
- 변경 근거를 **계획 번호와 함께** 주석으로 남긴다

**Ask first**
- **A-1 적용** — 후보 수가 늘고 코드 게이트가 처음 실제 작동한다(blast radius). 79 §6.4에 따라
  **임계 재검토(C-4)와 묶어야** 하는데 C가 보류 상태이므로 **단독 적용 승인 필요**
- `MIN_RELEVANCE_SCORE` 값 변경
- 클래스 추가·삭제(A-6 `unknown` 포함)
- `.env` 플래그 신설

**Never do**
- **실 LLM 호출**(D-127 — 건별 승인 없이 금지). `RUN_E2E=1` 설정도 승인 후
- 키워드 기반 사전 분류 재도입(**D-004 위배**)
- 멀티 DB 선택·`sub_query_context` 분리 축소(79 §1.1 **불변식**)
- 기존 테스트를 승인 없이 삭제·완화

---

## 8. Success Criteria

**모두 자동 검증 가능하다.**

| # | 기준 | 검증 |
|---|---|---|
| S1 | 프롬프트에 *"0.3 미만의 관련도를 가진 DB는 포함하지 마세요"* 류 **제외 지시가 없다** | 문자열 부재 단언 |
| S2 | 규칙 4의 **세 대역 각각에 최소 1개 예시**가 존재한다(0.8+ / 0.5~0.8 / 0.3~0.5) | 예시 `relevance_score` 값 파싱 → 대역별 카운트 ≥ 1 |
| S3 | 클래스별 few-shot 개수 **편차가 상한 이내**(최다 클래스 ≤ 전체의 30%) | 예시 `intent` 값 카운트 |
| S4 | 「출력 형식」의 **클래스 정의 집합 = 예시 등장 클래스 집합** | 두 집합 비교 |
| S5 | `fault_diagnosis` 절이 「intent 판단 우선순위」 절보다 **앞에 위치**한다 | 문자열 인덱스 비교 |
| S6 | **멀티 DB 예시가 보존**된다 — `databases` 배열 길이 ≥ 2인 예시가 최소 1개(79 §1.1 불변식) | 예시 파싱 |
| S7 | 기존 라우팅 테스트 **24개 전부 통과** | `pytest tests/test_semantic_routing/` |
| S8 | 전체 스위트 **무회귀** · `arch_check --ci` exit 0 | `pytest -q` |

> **측정 못 하는 것(명시)**: 이 스펙은 **분류 정확도 향상을 검증하지 않는다.** 논문이 보고한
> +9.2~+17.6%p는 실 LLM 평가가 필요하며 D-127 승인 대상이다. 여기서 고정하는 것은
> **"저신뢰를 표현할 수 있는 구조"** 이지 그 효과가 아니다.

---

## 9. Open Questions

| # | 질문 | 영향 |
|---|---|---|
| ~~Q0~~ | **확정 — KBGenAI 모드**(사용자 2026-08-26) → 트랙 C logprob은 **라우터 평면 이동 후 이월** | 해소 |
| ~~Q1~~ | **확정 — 즉시 적용(플래그 없이)**(사용자 2026-08-26). 저신뢰 값이 바로 출력되고 코드 게이트(0.3)가 처음 실제 작동한다. **회귀 테스트로 영향을 고정**한다 | 해소 |
| ~~Q2~~ | **30%로 확정**(구현자 판단 — 5클래스이므로 균등 시 20%, 여유 10%p) | 해소 |
| **Q3** | A-6(`unknown` 도입) — **이번 범위 밖**으로 확정. 79 §8① 매핑 정의가 선행 | 후속 |

**Q0·Q1 확정으로 범위가 고정됐다**: 트랙 A 전체(A-1 포함, A-6 제외)를 **플래그 없이** 구현한다.
남은 것은 Q2(편중 상한)뿐이며 **30%로 확정**하고 진행한다(현행 `alarm_query` 37% → 목표 ≤30%).

---

## 10. 계획서 정정 사항 (이 스펙 작성 중 발견)

| 대상 | 정정 |
|---|---|
| `plans/79` §3.5 발견 ③ | **`synonym_registration` 누락은 결함이 아니다** — LLM 산출 intent가 아니라 코드가 결정적으로 라우팅(`routing/semantic_router.py:90-107`). "클래스 정의 3 vs 전용 절 5" 부분만 유효 |
| `plans/79` §8 미해결 ② | **재작성 필요** — 전제가 Gemini였으나 **운영은 FabriX**(사용자 확인). 모드별 판정: **KBGenAI = 원천 불가**(요청·응답 스펙에 자리 없음) / **OpenAI 호환 = 클라이언트 확장 시 조건부 가능**. 어느 모드인지는 Q0 |
| `plans/79` §3.1 · §3.2 | 운영 LLM 표기를 **Gemini → FabriX**로 정정 필요(§3.1② "모델 계열이 다르다" 논지는 유지 — FabriX도 논문의 Qwen 로컬 모델군이 아니다) |
