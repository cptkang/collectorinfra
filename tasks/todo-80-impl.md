# TODO: Plan 80 실행 — 차수 0 + 차수 2

> Plan: `tasks/plan-80-impl.md` · Specs: `SPEC-router-output-contract.md` ·
> `SPEC-structured-output-backend.md` · `SPEC-composite-gap-tests.md`

## 선행

- [x] **P0. 기준선 기록** — 착수 전 `pytest -q` 실패/에러 수
- [x] **P1. instructor 반입** — `.venv` 설치 · `pip check` 통과 · `openai` 2.26.0 유지 확인

## 모듈 ① `router-output-contract`

- [x] **T1. 계약 테스트 작성 (TDD)** — S1~S8을 단언, **현행에서 의도대로 실패**
  - Verify: `pytest tests/test_semantic_routing/test_router_output_contract.py -v`
  - Files: `tests/test_semantic_routing/test_router_output_contract.py`(신규)
- [x] **T2. WU-02 · 허용 집합 정본 상수 신설** (프롬프트 본문 미변경)
  - Acceptance: S3·S8. 정본은 `src/prompts/semantic_router.py`, 라우팅은 import
  - Files: `src/prompts/semantic_router.py`
- [x] **T3. WU-02 · intent 대조 + 강등 + 사유 로그**
  - Acceptance: S1·S2. 플래그 종속 · `warning` 레벨
  - Files: `src/routing/semantic_router.py`
- [x] **T4. WU-03 · `relevance_score` 항목 단위 격리**
  - Acceptance: S4·S5·S6. 기본값 부여 금지(`None`=판정 불가)
  - Files: `src/routing/semantic_router.py`
- [x] **T5. WU-01 · 임계 잠정값 주석 고정** (값 변경 없음)
  - Acceptance: S7 · `plans/79` §8 ⑧ 1줄
  - Files: `src/routing/semantic_router.py` · `plans/79-semantic-routing-improvement.md`
- [x] **T6. 모듈 ① 회귀** — 전체 회귀 기준선 대조 + `arch_check --ci`

> **모듈 ① 완료(2026-08-27)** — 신규 테스트 **11건 통과** · 전체 회귀 **41 failed / 5 errors = 기준선 동일** ·
> passed 4566 → **4577**(+11) · `arch_check --ci` exit 0.
> 부수 발견: `from src.routing import semantic_router`는 패키지 `__init__`이 동명 함수를 re-export해
> **함수를 준다** — 모듈이 필요하면 `importlib.import_module`(테스트 주석에 기록).

## 모듈 ② `structured-output-backend`

- [x] **T7. 어댑터 테스트 작성 (TDD)** — S1~S9 대역 검증
  - Files: `tests/test_clients/test_instructor_adapter.py`(신규)
- [x] **T8. 어댑터 + 한국어 핸들러 + 모드 선택 구현**
  - Files: `src/clients/instructor_adapter.py`(신규)
- [x] **T9. 설정 · optional extra 배선**
  - Files: `src/config.py` · `pyproject.toml`
- [x] **T10. 모듈 ② 회귀** — 전체 회귀 + `arch_check --ci`(S10·S11)

## 모듈 ③ `composite-gap-tests`

- [x] **T11. WU-04 · G2·G3 재현 테스트(xfail strict)**
  - Acceptance: S1~S5. **`src/` 변경 0** · 라우팅/intent 단언 0
  - Files: `tests/test_orchestration/test_composite_host_scope.py`(신규)
- [x] **T12. 모듈 ③ 회귀**

## 마감

- [x] **T13. 전체 회귀 + 기준선 대조 + `arch_check --ci`**
- [x] **T14. 계획서 상태 반영** — `plans/80` §5 WU 상태 · `plans/79` 트랙 E 현황
- [x] **T15. 다음 모듈 스펙 작성** — `SPEC-intent-extraction-typing.md`(어댑터 확정 후)


---

## 완료 요약 (2026-08-27)

| 모듈 | 신규 테스트 | 결과 |
|---|---|---|
| ① `router-output-contract` | 11 | 전부 통과 (S1~S8) |
| ② `structured-output-backend` | 11 | 전부 통과 (S1~S9) |
| ③ `composite-gap-tests` | 3 | **2 xfail(갭 재현) + 1 passed**(현행 기록) |

| 검증 | 결과 |
|---|---|
| 전체 회귀 | **41 failed / 5 errors — 기준선 동일**. passed 4566 → **4589**(+23 = 11+11+1) · **2 xfailed** |
| `arch_check --ci` | exit 0 |
| `pip check` | 충돌 없음 · `openai` 2.26.0 유지 |

**변경 파일 8** — `src/prompts/semantic_router.py`(정본 상수) · `src/routing/semantic_router.py`
(E-1·E-2·임계 주석) · `src/clients/instructor_adapter.py`(신규) · `src/config.py`(플래그 2종) ·
`pyproject.toml`(optional extra) · 테스트 3종(신규)

## 모듈 ④ `intent-extraction-typing` (WU-08·09·10) — 완료

- [x] **T15.** `SPEC-intent-extraction-typing.md` 작성
- [x] **T16.** 스키마 3종 — `orchestration/schemas.py` · `routing/schemas.py` · `nodes/schemas.py`
- [x] **T17.** WU-08 `_llm_decompose` 배선 + **F4 강등 사유(`degraded`)**
- [x] **T18.** WU-09 `_llm_classify` 배선 (E-1·E-2 가드 **유지**)
- [x] **T19.** WU-10 `input_parser` 두 함수 **대칭** 배선 + 비대칭 해소
- [x] **T20.** 검증 12건 + 전체 회귀

| 검증 | 결과 |
|---|---|
| 신규 테스트 | **12건 통과**(S1~S8) |
| 전체 회귀 | **41 failed / 5 errors — 기준선 동일** · passed 4589 → **4601**(+12) · 2 xfailed |
| `arch_check --ci` | exit 0 |

**최종 누계**: 신규 테스트 **35건**(11+11+3+12 중 xfail 2 제외 시 33 passed) ·
passed 4566 → **4601**(+35) · 기준선 실패/에러 **불변**.


---

## 추가 진행 (2026-08-27) — 게이트 없는 작업

- [x] **S-1 하네스 구축** — `testdata/routing_gold/routing.yaml`(13건) · `scripts/eval_routing.py`
- [x] **FabriX 목업** — `tests/mocks/fabrix_kbgenai_mock.py`(HTTP 경계 교체 · 결함 주입 5종)
- [x] **하네스 검출력 검증** — `tests/test_routing_eval/`(13건). 결함 5종 **전부 검출**
- [x] **하네스 결함 2건 교정** — 목업이 발견: `bad_intent`·`error_status`가 exit 0으로 거짓 통과
- [x] **D-169·D-170 본문 등재** — `docs/02_decision.md`(헤더 2 · 변경 이력 2행 · 채번 이력 전환 · 안내 라인 D-162→D-170)

| 검증 | 결과 |
|---|---|
| 전체 회귀 | **41 failed / 5 errors — 기준선 동일** · passed 4601 → **4614**(+13) · 2 xfailed |
| `arch_check --ci` | exit 0 |
| 목업 실 호출 | **0건** (D-127 무관) |

**차단**: WU-05/06은 **FabriX 접속 정보 부재**로 실행 불가. G-BILL 승인만으로는 풀리지 않는다
(Gemini 측정치는 모델 종속이라 운영 기준선이 못 됨 — 79 §8 ③).
