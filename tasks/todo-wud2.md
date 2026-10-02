# Todo — WU-D2 (라우터 2단 분리)

계획: `tasks/plan-wud2.md` · 명세: `SPEC-router-two-stage.md`
**공통 완료 기준**: `plans/80` §5.4 6항 + `tasks/plan-wud2.md` §4 체크포인트 5항.

---

- [x] **T0** 기준선 확인 — 41 failed / 4793 passed / 5 errors
  - Verify: `python -m pytest -q --ignore=tests/e2e`

- [x] **T1 ★** 프롬프트 절을 명명 상수로 추출 + **골든 바이트 동일 고정**
  - Acceptance: `_build_router_prompt()` 렌더가 추출 **전과 바이트 동일**. 텍스트 사본 0(D-053)
  - Verify: `pytest -q tests/test_semantic_routing/test_prompt_byte_identity.py`
  - Files: `src/prompts/semantic_router.py`, `tests/.../test_prompt_byte_identity.py`

- [x] **T2** 1단(intent)·2단(DB) 템플릿 신설
  - Acceptance: 1단 출력은 **첫 줄이 라벨**(C-0) · 2단은 `{"databases":[…]}`.
    두 템플릿이 T1 상수를 **재사용**하고 새 텍스트는 출력 형식 지시로 한정
  - Verify: `pytest -q tests/test_semantic_routing/test_two_stage.py -k prompt`
  - Files: `src/prompts/semantic_router.py`

- [x] **T3** 출력 계약 — `IntentDecision` · `DatabaseSelection`
  - Acceptance: intent는 `allowed_intents()` 정본과 대조. `databases`는 **기존 `RouterDatabase` 재사용**
  - Verify: 같은 파일 `-k schema`
  - Files: `src/routing/schemas.py`

- [x] **T4** `RouterConfig` 신설 (env_prefix `ROUTER_`)
  - Acceptance: `two_stage_enabled=False` · `confidence_source="self_report"` ·
    `early_stop_enabled=False` · `min_confidence=None`. **미설정 시 현행 비트동일**
  - Verify: `pytest -q tests/test_config* tests/test_semantic_routing/test_two_stage.py -k config`
  - Files: `src/config.py`

- [x] **T5** 2단 분류 경로 `_llm_classify_two_stage()`
  - Acceptance: 플래그 on이면 **호출 2회**(1단 intent → 2단 DB), off면 **기존 함수 그대로**.
    `general_inference`·`cache_management`는 **2단계를 부르지 않는다**(Q3).
    2단계도 **LLM**이 판단한다(D-004 — 위치 힌트로 결정적 선택 금지)
  - Verify: 같은 파일 `-k two_stage or dispatch`
  - Files: `src/routing/semantic_router.py`

- [x] **T6** 신뢰도 2축 + 조기 차단
  - Acceptance: intent·DB 신뢰도가 **각각** 산출되고 임계가 **각각** 적용된다.
    조기 차단 시 2단계 호출 **0회**. **기본 off** · 임계 미설정 시 차단하지 않는다.
    소스 교체점이 **함수 하나**(`ROUTER_CONFIDENCE_SOURCE`)
  - Verify: 같은 파일 `-k confidence or early_stop`
  - Files: `src/routing/semantic_router.py`

- [x] **T7** 불변식·회귀 고정
  - Acceptance: **멀티 DB 다중 선택·`sub_query_context` 분리 보존**(§1.1) ·
    사용자 DB 직접 지정이 **intent와 무관**하게 반영 · 플래그 off **비트동일**
  - Verify: 같은 파일 `-k invariant or regression`
  - Files: `tests/test_semantic_routing/test_two_stage.py`

- [x] **T8** 마감 — 문서·결정 기록
  - Acceptance: `plans/79` 트랙 B 상태 갱신(보류 → 구조 구현 완료·**미검증 4건 명시**) ·
    `plans/80` 이월 축 갱신 · `docs/02_decision.md` 신규 결정 · INDEX 동기화
  - Verify: 전체 회귀 + `arch_check --ci`
