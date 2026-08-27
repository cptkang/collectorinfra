# TODO: 시멘틱 라우터 분류 지시문 개선 (Plan 79 트랙 A)

> Spec: `SPEC-semantic-router-instruction.md` · Plan: `tasks/plan.md`

- [x] **T1. 프롬프트 구조 테스트 작성 (TDD)**
  - Acceptance: S1~S6를 단언하는 테스트가 존재하고, **현행 코드에서 의도대로 실패**한다
  - Verify: `.venv/bin/python -m pytest tests/test_semantic_routing/test_router_prompt_structure.py -v`
  - Files: `tests/test_semantic_routing/test_router_prompt_structure.py` (신규)

- [x] **T2. A-1 — 규칙 5 제거 (저신뢰 표현 가능화)**
  - Acceptance: S1 통과. "0.3 미만 … 포함하지 마세요" 제거, 제외 판단이 시스템 몫임을 명시
  - Verify: 위 테스트 S1 + `pytest tests/test_semantic_routing/`
  - Files: `src/prompts/semantic_router.py`

- [x] **T3. A-2 — 신뢰도 3대역 예시 보강**
  - Acceptance: S2 통과. 0.8+/0.5~0.8/0.3~0.5 각 대역에 예시 ≥1
  - Verify: 위 테스트 S2 + 회귀
  - Files: `src/prompts/semantic_router.py`

- [x] **T4. A-3 — 클래스 정의 일원화**
  - Acceptance: S4 통과. 「출력 형식」 정의 집합 = 예시 등장 클래스 집합
  - Verify: 위 테스트 S4 + 회귀
  - Files: `src/prompts/semantic_router.py`

- [x] **T5. A-4 — `alarm_query` 편중 완화**
  - Acceptance: S3 통과. 최다 클래스 예시 비중 ≤ 30%
  - Verify: 위 테스트 S3 + 회귀
  - Files: `src/prompts/semantic_router.py`

- [x] **T6. A-5 — `fault_diagnosis` 절 배치 정정**
  - Acceptance: S5 통과. 「intent 판단 우선순위」보다 앞에 위치
  - Verify: 위 테스트 S5 + 회귀
  - Files: `src/prompts/semantic_router.py` (+ 필요 시 `src/routing/semantic_router.py` 조립부)

- [x] **T7. A-7 — 검토 후 불필요 판정**
  - **판정**: 상충 없음 — 두 우선순위가 `cache > alarm > (process) > data > general`로 순서 일관.
    `process_query`는 2단(intent_orchestration)에만 존재하는 agent이고 두 노드는 사다리에서
    **배타적으로 등록**된다(`docs/21_orchestration_ladder.md` §2). 중복이 아니라 단별 capability 차이.
  - **부수 조치**: 라우터 우선순위 4항의 "위 **세 가지** 중" → "위 **항목** 중"으로 일반화
    (fault_diagnosis 옵트인 시 네 항목이 되므로 실제 불일치였다)
  - Files: `src/prompts/semantic_router.py`

- [x] **T8. 전체 회귀 + 아키텍처 검사**
  - Acceptance: S7·S8 통과
  - Verify: `pytest -q` · `python scripts/arch_check.py --ci`
  - Files: —

- [x] **T9. 계획서 정정 반영**
  - Acceptance: `plans/79` §3.5③(synonym_registration 오판) · §8②(logprobs = KBGenAI 원천 불가) 정정
  - Verify: 문서 확인
  - Files: `plans/79-semantic-routing-improvement.md`

---

## 완료 요약 (2026-08-26)

| 항목 | 결과 |
|---|---|
| 신규 테스트 | **12건** 전부 통과 (S1~S6 + 옵트인 보호 4건) |
| 라우팅 회귀 | `tests/test_semantic_routing/` + `test_routing/` **170 passed** |
| 전체 회귀 | 41 failed / 5 errors — **기준선과 동일**(클린 원복 대조로 확정). passed 4554 → **4566**(+12 = 신규 테스트) |
| arch_check | exit 0 |

**변경 파일 4** — `src/prompts/semantic_router.py` · `src/routing/semantic_router.py` ·
`tests/test_semantic_routing/test_fault_diagnosis.py`(검사 강화) ·
`tests/test_semantic_routing/test_router_prompt_structure.py`(신규)

**범위 밖으로 남긴 것**: A-6(`unknown` 도입 — 의미 매핑 미확정) · 트랙 B·C·D · 분류 정확도 측정(D-127)
