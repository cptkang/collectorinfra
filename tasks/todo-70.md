# Task List: 코드베이스 경로·설정 부채 정리

> 계획: `tasks/plan-70.md` · 스펙: `SPEC-codebase-path-debt.md`
> 공통 DoD는 계획서 참조. **게이트 필요 항목은 제목에 표시.**

---

## Phase R — 규칙 고정 (선행 · 코드 변경 0)

### R1: D-143 등재 + 채번 안내 라인 갱신

**Description:** 승격-폐기 동반 원칙과 폐기 전 4항 실측 의무를 `docs/02_decision.md`에
`## D-143`으로 등재한다. 원 계획이 D-140을 예약했으나 소진돼 재채번한 건이다.

**Acceptance:**
- [ ] `## D-143` 헤더 1건 + 「변경 이력」 표 1행
- [ ] ②-3항에 **브랜치 한정 조회 의무** 포함(`git branch -a --contains` / `merge-base --is-ancestor`)
- [ ] 채번 안내 라인이 "현재 최대 D-143 → 다음 D-144"로 갱신
- [ ] D-140~142가 plans/70 예약분이 **아니었음**을 안내 라인에 부기(재발 방지)

**Verify:**
- [ ] `grep -c "^## D-143" docs/02_decision.md` = 1
- [ ] 헤더·변경이력 양쪽 grep 최댓값이 143으로 일치

**Files:** `docs/02_decision.md`
**Scope:** XS

---

### R2: CLAUDE.md에 폐기 전 4항 실측 의무 반영

**Description:** 신규 세션이 CLAUDE.md만 읽고 이 규칙을 적용할 수 있게 1~2줄 추가.

**Acceptance:**
- [ ] "Known Mistakes 핵심 원칙"에 폐기 제안 4항 실측 의무 등재
- [ ] 기존 원칙 문구 변경 0 (추가만)

**Verify:**
- [ ] `git diff CLAUDE.md`가 추가 라인만 보여줌

**Files:** `CLAUDE.md`
**Dependencies:** R1
**Scope:** XS

---

## ✅ Checkpoint R
- [x] D-143 등재 확인, 이후 모든 폐기 제안이 이 규칙 아래 실행됨

---

## Phase 병렬 (R 완료 후 동시 착수 가능 · 파일 안 겹침)

### O1: 기동 단 확정 로그

**Description:** 사다리 어느 단으로 확정됐는지와 강등 사유를 기동 로그 1줄로 판독 가능하게
한다. 빌드 타임 배타이므로 요청별 카운터가 아니라 빌드 완료 로그에 필드를 더한다.

**Acceptance:**
- [x] 빌드 완료 로그(`src/graph.py` `"에이전트 그래프 빌드 완료"`)에 `use_deep_agent`·`degraded_reason` 추가
- [x] 강등 사유가 `package_missing`/`orchestrator_unavailable`/`flag_off`/`none`으로 구분
- [x] tri-state 자동 해석 발동 여부가 `resolved_by="auto_multidb"|"explicit_env"`로 기록
- [x] **새 플래그 추가 0** (AD-3)

**Verify:**
- [x] 4단 각 조건으로 빌드 시 로그가 해당 단·사유를 출력함을 테스트로 단언
- [x] `pytest tests/test_graph.py tests/test_graph_routing_gaps.py tests/test_orchestration/ -q`
- [x] 골든 스냅샷 무갱신

**Files:** `src/graph.py`, `src/config.py`, `tests/test_observability/` 또는 `tests/test_graph*.py`
**Scope:** S

---

### O2: 강등 관측 상설화

**Description:** O1의 기동 판정을 `src/observability/`에 지표로 얹는다. **신규 패키지 신설 없음**(AD-2).

**Acceptance:**
- [x] 확정 단·강등 사유가 `src/observability/`의 기존 구조로 조회 가능
- [x] 정본(1단) 미확정 시 경고 **1회만**(스팸 없음)
- [x] 임계값은 상수 (새 `enable_*` 없음)

**Verify:**
- [x] 4단 각 조건 주입 시 지표가 독립적으로 반영됨을 단위 테스트로 단언
- [x] `arch_check --ci` 0 (`src.observability`는 infrastructure)

**Files:** `src/observability/`, `src/graph.py`
**Dependencies:** O1
**Scope:** S

---

### F1: 플래그 43개 전수 감사표

**Description:** `enable_*`/`use_*`/`*_enabled` 전수를 판정과 함께 표로 만든다. 코드 변경 0.

**Acceptance:**
- [x] `docs/flag_audit.md`에 **43행** 누락 0
- [x] 열 = 플래그 · 생성 D-번호 · 코드 기본값 · **`.env` 실제값** · 프로덕션 참조 수 · 최종 변경일(브랜치 한정) · 판정
- [x] 판정은 존치/상수화/삭제/기한부 중 하나로 **전 행 확정**
- [x] 참조 0건·1~2건 플래그가 별도 표시

**Verify:**
- [x] 계획서 §4 재현 명령 결과와 표의 참조 수가 일치 — **불일치 2건 실측**(벤더 venv 오염:
      `trace_enabled` 52→5, `enable_thinking` 7→4). 계획서 명령에 `.venv` 제외가 없음 → E1에서 반영
- [x] `.env` 실제값 칸이 전부 채워짐

**Files:** `docs/flag_audit.md`
**Scope:** S

---

### E1: stale 설치본 정리 + 측정 재현성

**Description:** `.venv/site-packages/src/`의 7/23자 비-editable 사본을 정리하고, 규모 측정
재현 명령에 venv 제외를 넣는다. **스펙 §0 N1·N2 — 원 계획에 없던 발견.**

**Acceptance:**
- [ ] 프로젝트 밖 cwd에서 `import src.config` 시 프로젝트 사본이 잡힘
- [ ] `plans/70` §1.1 재현 명령에 `-not -path "*venv*"` 포함
- [ ] 규모 수치가 재측정값으로 갱신

**Verify:**
- [ ] `cd /tmp && python -c "import src.config as c; print(c.__file__)"` → 프로젝트 경로
- [ ] `pytest` 전체 무회귀 · `python -c "import src.api.server"` 성공

**Files:** `.venv/`(정리), `plans/70-codebase-scale-and-path-debt.md`
**Scope:** XS
**주의:** editable 재설치(`pip install -e .`)가 다른 경로에 영향을 줄 수 있으니 전체 스위트로 확인

---

### D1: 계획 인덱스 신설

**Description:** `plans/README.md`는 "구현 계획서 목차"이나 Plan 68·69·70이 미등재다.
갱신이 아니라 **전건 인덱스를 신설**한다.

**Acceptance:**
- [ ] `plans/INDEX.md`에 **75건 전건** 등재 (번호·제목·상태·최종 수정일)
- [ ] 기존 `plans/README.md`는 변경하지 않음(성격이 다름 — 초기 구현 목차)
- [ ] 상태는 파일 내 표기에서 기계적으로 추출(추정 금지)

**Verify:**
- [ ] `ls plans/*.md | wc -l`과 인덱스 행 수 일치
- [ ] 상호 참조 링크 유효성 검사

**Files:** `plans/INDEX.md`
**Scope:** S

---

### D2: 채번 안내 라인 축약

**Description:** `docs/02_decision.md:8`이 1,200자+ 단일 라인이라 매 세션 컨텍스트 비용이 크고
읽기 어렵다. 예약·결번·재부여 이력을 표로 분리한다.

**Acceptance:**
- [x] 안내 라인은 "채번 규칙 + 현재 최댓값"만 남김
- [x] 예약/결번/재부여 이력은 표로 분리 (정보 손실 0)
- [x] 기존 정보가 전부 표로 이관됐음을 확인

**Verify:**
- [x] 이관 전후 예약 번호 집합이 동일 (`grep -oE "D-[0-9]{3}"` 비교)

**Files:** `docs/02_decision.md`
**Dependencies:** R1
**Scope:** XS

---

### V1: 평가 하네스 정본 경로 추가

**Description:** `PATHS`에 `deep_agent`가 없어 `.env` 활성 정본의 실행정확도를 측정할 수단이
없다. P3의 "현행 이상" 판정 기준을 세우려면 선행되어야 한다.

**Acceptance:**
- [ ] `--path deep_agent`가 인자로 수용됨
- [ ] 오케스트레이터 미가용 시 graceful 스킵(기존 규약 재사용)
- [ ] 기존 3경로 동작 불변

**Verify:**
- [ ] `.venv/bin/python scripts/eval_text2sql.py --dry-run` 그린
- [ ] `--path deep_agent --mock` 정상 종료

**Files:** `scripts/eval_text2sql.py`
**Scope:** S
**주의:** 실 구동은 Gemini 호출 → **D-127 건별 승인 후**. `--dry-run`·`--mock`은 대상 아님

---

### V2: 골드셋 확충

**Description:** 현 26건은 커버리지 76.9%의 표본으로 얇다. 특히 `unhandled` 1건은
거부 경로 설계 근거로 부족하다.

**Acceptance:**
- [ ] 카테고리별 최소 표본 목표를 정하고 충족
- [ ] `unhandled`·`outside` 보강
- [ ] **기존 26건 불변**(추가만)

**Verify:**
- [ ] `--dry-run` 스키마 위반 0건 유지
- [ ] 카테고리 분포 갱신 확인

**Files:** `testdata/text2sql_gold/{b0,gp,yd}.yaml`
**Scope:** S
**주의:** 골드 SQL은 SELECT 전용. `--check-gold`는 실 DB 접속 필요 → 별도 판단

---

### V3: 렌더 골든 2단 시나리오

**Description:** 사다리 2단(orchestration) 시나리오를 프롬프트 렌더 매트릭스에 추가.
1단(deep_agent)은 패키지 내부 조립이라 이 매트릭스로 채록 불가 → V1이 담당.

**Acceptance:**
- [ ] 2단 시나리오 키 추가
- [ ] **기존 12키 값 불변**(추가만·변경 0)
- [ ] 연속 2회 바이트 동일

**Verify:**
- [ ] 최초 `UPDATE_PROMPT_SNAPSHOT=1`로 채록, 재실행은 **갱신 없이** 그린

**Files:** `tests/test_prompt_render_matrix.py`, `tests/snapshots/prompt_render_sha256.json`
**Scope:** S

---

## ✅ Checkpoint 병렬
- [x] O1 기동 로그로 **확정 단과 사유를 실제 확인** → 게이트 6 판정 근거 확보
      실측(2026-08-20, 운영 `.env`): `tier=deep_agent degraded_reason=none resolved_by=explicit_env`
      → 정본 1단 확정 · 강등 없음 · 레거시 4단 **미도달**
- [ ] `pytest` 전체 무회귀 · `arch_check --ci` 0
- [ ] **사람 검토 후 Phase L 진행**

---

## Phase L — 사다리 명시화 (O 완료 후)

### L1: 사다리 단일 출처 문서

**Description:** 정본·폴백 단·강등 조건·모듈 의존 방향을 한 장으로 고정한다.
원 계획 v1이 트랙 A/B 폐기를 권고했다가 오판으로 판명된 것의 **직접 재발 방지책**이다.

**Acceptance:**
- [ ] `docs/21_orchestration_ladder.md` 신설 — 4단 구조·각 단 활성 조건·강등 트리거·
      모듈 의존 방향·각 단 담당 질의 유형·삭제 금지 근거(D-037)
- [ ] `graph.py` 분기 주석과 `.env` 주석이 이 문서를 가리킴
- [ ] 라인 참조에 함수·상수명 병기 (AD-4)

**Verify:**
- [ ] **합격 기준**: 이 문서만 읽고 "트랙 A를 지우면 트랙 B가 깨진다"를 알 수 있는가
- [ ] `grep -n "21_orchestration_ladder" src/graph.py .env` 결과 존재

**Files:** `docs/21_orchestration_ladder.md`, `src/graph.py`, `.env`
**Dependencies:** O1
**Scope:** S

---

### L2: 명명 부채 해소

**Description:** `enable_deepagent_orchestration`의 실체는 **트랙 A**인데 이름이
`enable_deepagents_package`(트랙 B)와 혼동을 일으킨다. 원 계획 v1 오독의 원인 중 하나다.

**Acceptance:**
- [ ] `enable_intent_orchestration`으로 개명
- [ ] 구 이름 `AliasChoices` 하위호환 유지 (제거 아님 — 폐기 기한만 D-143에 부여)
- [ ] 설정 카탈로그(D-129) 항목 반영 · 필드 수 단언 갱신

**Verify:**
- [ ] **두 환경변수명 모두**로 기동 확인 (pydantic 필드로 판정 — `os.getenv` 금지)
- [ ] `pytest tests/test_config_env_reload.py tests/test_graph.py tests/test_api/test_settings_catalog.py -q`
- [ ] 골든 무갱신

**Files:** `src/config.py`, `.env`, `.env.example`, `src/graph.py`, 소비처
**Dependencies:** L1, R1
**Scope:** M

---

### L3: tri-state 암묵 활성 경고

**Description:** "멀티 DB 등록 시 자동 활성" 해석은 운영 경로를 DB 상태에 종속시킨다.
발동 시 경고를 남겨 관측 가능하게 한다.

**Acceptance:**
- [ ] 자동 해석 발동 시 경고 로그
- [ ] `.env` 명시값이 있을 때 자동 해석이 **개입하지 않음**을 테스트로 단언

**Verify:**
- [ ] `.env` 명시값/미설정 두 조건 기동 확인
- [ ] `pytest tests/test_config_env_reload.py -q`

**Files:** `src/config.py`
**Dependencies:** O1
**Scope:** XS

---

### L4: 레거시 4단 정리 — **게이트 6**

**Description:** `else → schema_analyzer` 분기 제거. **목적은 절감이 아니라 사다리
4단→3단 단순화**다(실체 3줄, -5~-20줄).

**Acceptance:**
- [x] O1 기동 로그로 **4단 미확정 확인** 후에만 착수 — 확인 완료(`tier=deep_agent`), 단 관측은 현 운영 `.env` 1종 한정
- [ ] 단일 DB 처리는 3단(`semantic_router`)이 담당함을 확인
- [ ] 4단으로 확정되는 설정 조합이 실제로 쓰이면 **폐기하지 않고 존치**

**Verify:**
- [ ] 제거 분기를 겨냥한 기존 테스트가 **먼저 실패하는 것을 확인한 뒤** 정리(경로 소멸의 증거)
- [ ] `pytest tests/test_graph*.py -q` · 골든 전건 대조

**Files:** `src/graph.py`
**Dependencies:** O1, L1, **게이트 6 승인**
**Scope:** XS

---

## ✅ Checkpoint L
- [ ] `docs/21_orchestration_ladder.md`만 읽고 트랙 의존을 이해할 수 있음을 사람이 확인
- [ ] 구 환경변수명 기동 회귀 통과
- [ ] `pytest` 전체 무회귀 · `arch_check --ci` 0

---

## Phase S — 시맨틱 수렴 (**게이트 4 별도 승인** · 이번 범위 밖)

스펙 §9 `semantic-convergence` 참조. V1·V2·V3 완료가 전제다.
