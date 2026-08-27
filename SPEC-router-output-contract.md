# Spec: router-output-contract

> Module id: `router-output-contract` · Map: `CAPABILITY-MAP-intent-extraction.md`
> WU-01·02·03 (`plans/80` §5.2 차수 0 — **게이트 없음**) · 설계: `plans/79` §3.6 · 트랙 E-1·E-2
> **공통 사항**(Tech Stack · Commands · Project Structure · Code Style · Boundaries)은 맵 참조.

## Objective

라우터가 LLM 응답을 **받은 뒤의 처리**에 계약을 세운다. 현재 두 결함이 실측됐다.

- **F1** — `intent`가 알려진 클래스 집합과 대조되지 않는다(`semantic_router.py:439`).
  가드는 `fault_diagnosis`→`data_query` 강등 하나뿐(옵트인 보호용)이고, 그 외 오타·환각 intent는
  **어느 분기에도 걸리지 않고 조용히 DB 조회 경로로 낙하**한다.
- **F2** — `float(db_entry.get("relevance_score", 0.5))`(`:452`)가 raw로 걸려 있어, LLM이
  `"높음"`을 주면 `ValueError` → 호출부 `except`가 삼켜 **분류 전체를 버리고 단일 DB 폴백**.
  **임계와 무관하게 멀티 DB(§1.1 불변식)가 축소되는 경로**이며, A-1로 후보 수가 늘어 표면이 커졌다.

**사용자**: 라우팅 결과를 소비하는 하류 노드(`subagents`·`multi_db_executor`)와 최종 사용자.
**성공**: 위 두 경로가 닫히고, **왜 탈락했는지가 사유로 남는다.**

## 왜 지금인가

WU-05(S-1 골든셋 회귀)가 **G-BILL 대기 중**이다. E-1·E-2는 **프롬프트를 건드리지 않아** S-1 측정과
간섭하지 않고 신규 의존이 0이다. 그리고 **E-2가 막는 것이 곧 S-1이 감시할 불변식**이므로,
대기 중에 하면 **승인 대상 리스크가 줄어든 상태로 S-1을 맞을 수 있다.**

## Scope

| WU | 작업 | 파일 |
|---|---|---|
| WU-01 | `MIN_RELEVANCE_SCORE`가 **잠정값**임을 코드 주석·계획서에 고정 (값 변경 없음) | `src/routing/semantic_router.py` · `plans/79` §8 ⑧ |
| WU-02 | **E-1** intent 허용 집합 대조 + 미상 강등 + 사유 로그 | `src/prompts/semantic_router.py`(상수) · `src/routing/semantic_router.py` |
| WU-03 | **E-2** `relevance_score` 항목 단위 격리 + 탈락 사유 | `src/routing/semantic_router.py` |

**범위 밖**: 프롬프트 **텍스트** 변경 · 임계 **값** 변경 · `routing_intent` 출력값 체계 변경.

## 설계 결정

**D1. 허용 집합의 단일 출처는 `src/prompts/semantic_router.py`에 둔다.**
프롬프트가 클래스를 정의하는 곳이므로 정본도 거기다. 라우팅 코드는 **import 해서 쓴다**(사본 금지 · D-053).

**D2. 프롬프트 텍스트는 바이트 동일을 유지한다.**
정본 상수는 **신설**하고, 프롬프트 본문의 클래스 나열은 **손대지 않는다**. 동기화는
**테스트로 강제**한다(`_defined_classes(prompt) == 허용 집합`). 이유: S-1 미실행 상태에서
프롬프트가 바뀌면 트랙 A 측정 기준이 흔들린다(맵 Boundaries "Ask first").

**D3. 검증 대상은 LLM이 산출한 `intent`뿐이다.**
노드가 반환하는 `routing_intent`에는 코드가 만드는 값(`zone_clarification`)이 있고, 그건 대조 대상이 아니다.

**D4. 허용 집합은 `fault_diagnosis_enabled`에 종속된다.**
off면 집합에서 빠진다 — 넣으면 옵트인 계약(Plan 64 CW-B · `plans/80` C-A)이 깨진다.

**D5. `relevance_score` 변환 실패에 기본값을 주지 않는다.**
`None`(판정 불가)으로 두고 **해당 항목만 탈락**시킨다. `0.5`를 주면 게이트를 그냥 통과한다.

## Testing Strategy

`tests/test_semantic_routing/test_router_output_contract.py` **신설**. 실 LLM 없이 검증한다
(가짜 `llm.ainvoke`로 응답을 주입). 기존 `test_router_prompt_structure.py`와 역할이 다르다 —
저쪽은 *프롬프트 구조*, 이쪽은 *응답 처리*.

## Success Criteria

| # | 조건 |
|---|---|
| **S1** | 알려지지 않은 `intent`(예: `"prosess_query"`)가 **`data_query`로 강등**되고 **사유가 로그로 남는다** |
| **S2** | `fault_diagnosis_enabled=False`에서 허용 집합에 `fault_diagnosis`가 **없다**; `True`면 있다 |
| **S3** | 허용 집합 == 프롬프트 「출력 형식」 정의 클래스 집합 (양쪽 플래그 상태 모두) |
| **S4** | 한 DB 항목의 `relevance_score`가 `"높음"`이어도 **나머지 DB가 후보로 생존**한다 |
| **S5** | 탈락 항목(형식 오류·무효 `db_id`)의 **사유가 구조화되어 남는다** |
| **S6** | 전체 단일-DB 폴백은 **LLM 호출 자체가 실패했을 때만** 발생한다 |
| **S7** | `MIN_RELEVANCE_SCORE` 정의부에 **잠정값 근거 주석**이 있고 **값은 0.3 그대로**다 |
| **S8** | 프롬프트 렌더 결과가 **변경 전과 바이트 동일**하다 |
| **S9** | 전체 회귀 기준선 동일 · `arch_check --ci` exit 0 |

## Open Questions

- 미상 intent 강등 로그 레벨: 기존 `fault_diagnosis` 강등이 `logger.debug`다. **환각 신호는
  운영에서 보여야 하므로 `warning`으로 둔다** — 이견 있으면 지적 요망.
