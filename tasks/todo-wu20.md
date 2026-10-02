# TODO: WU-20 — 미들웨어 OS 근사 식별

> Plan: `tasks/plan-wu20.md` · Spec: `SPEC-middleware-os-identification.md`

- [x] **T1. 판정 계약 테스트 (TDD)** — S1~S5 단언, 현행에서 의도대로 실패
  - Verify: `.venv/bin/python -m pytest tests/test_middleware/ -v`
  - Files: `tests/test_middleware/__init__.py` · `test_identification.py`(신규)
- [x] **T2. 선언 파일 — 스키마 + 기본 규칙 세트**
  - Acceptance: YAML 로딩·스키마 검증 통과 · 규칙이 데이터로만 존재
  - Files: `config/middleware_signatures.yaml`(신규)
- [x] **T3. 결정적 매처 구현**
  - Acceptance: S1(종류·인스턴스·기동 인자) · S2(결정성·LLM 0) · S4(미식별 사유)
  - Files: `src/domain/middleware.py`(신규)
- [x] **T4. 하드코딩 0 · 선언적 확장 실증**
  - Acceptance: S3(소스에 미들웨어명 0건) · S5(규칙 추가만으로 신규 식별)
  - Files: 테스트 보강
- [x] **T5. `profile="middleware"` 요구 명세 + 선행 실측 문서화**
  - Acceptance: S6(allowlist 확장 0 근거 명시) · S7(선행 실측 2항)
  - Files: `docs/24_middleware_profile_spec.md`(신규)
- [x] **T6. 전체 회귀 + 아키텍처 검사**
  - Acceptance: S8(`sre_agent` import 0) · S9(기준선 동일)

## v2 추가 — `sre_agent` 구현 편입 (사용자 지시 2026-08-27)

- [x] **T7. 프로파일 계약 테스트 (TDD)** — S10·S11, 현행에서 실패
  - Verify: `cd sre_agent && .venv/bin/python -m pytest tests/test_middleware_profile.py -v`
  - Files: `sre_agent/tests/test_middleware_profile.py`(신규)
- [x] **T8. `middleware_profile()` + 조사 초점 지침 구현**
  - Acceptance: allowlist **확장 0**(vm_profile과 동일) · 수집 순서·부하 가드·판정 위임 명시
  - Files: `sre_agent/sre_agent/toolset_profiles.py`
- [x] **T9. `sre_agent` 스위트 회귀** — S12


---

## 완료 요약 (2026-08-27)

| 산출물 | 내용 |
|---|---|
| `config/middleware_signatures.yaml` | 선언 규칙 **13종**(WAS 5 · 웹서버 3 · 캐시/메시징 4 · TP 1) |
| `src/domain/middleware.py` | 결정적 매처 — LLM 0 · 미식별 사유 구조화 |
| `sre_agent/.../toolset_profiles.py` | `middleware_profile()` + `MIDDLEWARE_FOCUS_NOTE` |
| `docs/24_middleware_profile_spec.md` | 요구 명세 + **선행 실측 2항** |

| 검증 | 결과 |
|---|---|
| 본체 신규 테스트 | **11건 통과** |
| `sre_agent` 신규 테스트 | **11건 통과** (164 → **175 passed**) |
| 본체 전체 회귀 | **41 failed / 5 errors — 기준선 동일** · passed 4614 → **4625** |
| `arch_check --ci` | 본체 exit 0 · `sre_agent` exit 0 |
| D-118 경계 | 본체 → `sre_agent` import **0건** |
