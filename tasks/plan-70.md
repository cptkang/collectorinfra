# Implementation Plan: 코드베이스 경로·설정 부채 정리

> ⚠️ **재채번(2026-08-24)**: 이 문서가 D-143·D-144로 부르는 결정은 병합(`8e95dff`) 번호 충돌 해소로 **D-161**(경로 승격-폐기 동반 원칙)·**D-162**(사다리 관측·플래그 감사)로 재부여됐다 — `docs/02_decision.md` 「채번 이력」 표. 본문은 새 번호로 갱신했고, 등재 당시 번호를 남겨야 하는 기록은 그 자리에 별도 표기했다.

> **스펙**: `SPEC-codebase-path-debt.md` · **원 계획**: `plans/70-codebase-scale-and-path-debt.md`(v4)
> **태스크**: `tasks/todo-70.md`
> **상태**: 승인됨 (2026-08-20). Q1 = **D-143 신규 채번**(현행 **D-161**), Q2 = **기동 로그 확인 후 4단 정리**
>
> ⚠️ 기존 `tasks/{plan,todo}.md`는 D-140~142 작업이 점유 중이라 파일명을 분리했다.

## Overview

`plans/70` v4를 전수 재측정해 전제 5건의 무효를 확인하고, 요건을 8개 모듈로 재구성했다
(스펙 §0·§2). 목표는 코드량 감축이 아니라 **"살아있는데 설명이 없는 구조"를 읽을 수 있게
만드는 것**이다.

## Architecture Decisions

### AD-1. 규칙(`promotion-rule`)을 최우선에 둔다

D-161(승격-폐기 동반 + 폐기 전 4항 실측)을 **다른 모든 모듈보다 먼저** 등재한다.
규칙을 나중에 만들면 이미 내린 폐기 판단을 소급 검증하게 되고, 그 순간 규칙은
"지켜야 할 것"이 아니라 "사후 정당화"가 된다.

원 계획은 P4(마지막)에 뒀으나 §2.6 착수 권고에서 스스로 4순번으로 앞당겼다 —
그 판단을 1순번까지 밀어 올린다.

### AD-2. 관측은 신규 모듈을 만들지 않고 `src/observability/`에 얹는다

원 계획 P2-5는 배치처를 고민했다(`llm_call_counter.py`가 타 브랜치 소속). 그 사이
D-141이 `src/observability/`를 신설했으므로 **재사용이 정답**이다. 신규 패키지를 또 만들면
관측 코드가 두 곳으로 갈린다.

### AD-3. 새 `enable_*` 플래그를 만들지 않는다

플래그 부채를 줄이는 작업이 플래그를 늘리면 자기모순이다(원 계획 §0.2). 관측 on/off가
필요하면 기존 `OBS_*`(D-141)를 재사용하고, 임계값은 상수로 둔다.

### AD-4. 문서에 라인 번호를 적을 때 함수·상수명을 병기한다

이번 재측정에서 `graph.py:375→381`, `539→546`, `640→645`가 밀린 것이 확인됐다.
라인만 적힌 참조는 14일 만에 썩는다.

## Dependency Graph

```
R (promotion-rule)  ← 선행 · 코드 변경 0
 │
 ├── O (path-observability)   기동 로그 → 강등 관측
 ├── F (flag-audit)           43개 전수표
 ├── D (doc-hygiene)          INDEX 신설 · 채번 라인 축약
 ├── E (env-hygiene)          stale 정리 · 측정 재현성
 └── V (eval-baseline)        --path deep_agent · 골드셋 · 렌더 골든
                   ↓
              L (ladder-docs)  사다리 문서 · 개명 · tri-state · 4단 정리
                   ↓
              S (semantic-convergence)  ← 게이트 4 · 별도 승인
```

**병렬 가능**: O · F · D · E · V (파일 안 겹침)
**반드시 순차**: R → 나머지 / L은 O 완료 후 / S는 V·L 완료 + 게이트 4 후

## Task List

### Phase R: 규칙 고정 (선행)
- [ ] R1: D-161 등재 + 채번 안내 라인 갱신
- [ ] R2: CLAUDE.md에 폐기 전 4항 실측 의무 반영

### Checkpoint R

### Phase 병렬 (R 완료 후 동시 착수 가능)
- [ ] O1: 기동 단 확정 로그 (2필드 + 강등 사유 구분)
- [ ] O2: 강등 관측을 `src/observability/`에 상설화
- [ ] F1: `docs/flag_audit.md` 43행 전수 감사표
- [ ] E1: stale 설치본 정리 + 측정 재현 명령 정정
- [ ] D1: `plans/INDEX.md` 75건 신설
- [ ] D2: 채번 안내 라인 축약 (예약 목록을 표로 분리)
- [ ] V1: `--path deep_agent` 지원
- [ ] V2: 골드셋 확충 (`unhandled`·`outside` 보강)
- [ ] V3: 렌더 골든 2단 시나리오 추가

### Checkpoint 병렬

### Phase L: 사다리 명시화 (O 완료 후)
- [ ] L1: `docs/21_orchestration_ladder.md` 단일 출처
- [ ] L2: `enable_deepagent_orchestration` 개명 + AliasChoices
- [ ] L3: tri-state 자동 해석 경고 로그
- [ ] L4: 레거시 4단 정리 **(게이트 6 — 기동 로그 확인 후)**

### Checkpoint L

### Phase S: 시맨틱 수렴 — **게이트 4 별도 승인 필요**
- [ ] S1~S4: 스펙 §9 `semantic-convergence` 참조 (이번 범위 밖)

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| L2 개명이 구 환경변수명 기동을 깨뜨림 | **High** — 운영 `.env`가 구 이름 사용 중 | `AliasChoices` 하위호환 + 두 이름 모두 기동 확인을 DoD에 명시. `.env` 로딩은 `os.environ`에 주입되지 않으므로 판정은 pydantic 필드로 |
| E1 stale 정리가 다른 실행 경로를 깨뜨림 | Med | 제거 전 `pip show`·`dist-info` 확인, 제거 후 전체 스위트 + 서버 import 확인 |
| L4가 실제로 쓰이는 경로를 지움 | Med | O1 기동 로그로 4단 미확정 확인이 선행 조건. 확정되면 **존치** |
| D1 아카이빙이 참조 링크를 끊음 | Med | 이번 범위는 **INDEX 신설까지만**. 실제 이동은 Q4 확정 후 별건 |
| V2 골드셋 확충이 기존 26건을 변형 | Low | "추가만·기존 불변"을 DoD로 고정, `--dry-run` 스키마 위반 0 유지 |

## Definition of Done (전 태스크 공통)

- [ ] `pytest` 전체 무회귀 (실패 집합 diff — 기준선은 `git worktree add` + **`.env`·`.encenv` 복사**)
- [ ] `python scripts/arch_check.py --ci` 0
- [ ] 골든 스냅샷 **무갱신** (동작 불변 태스크)
- [ ] Step 1개 = 커밋 1개
- [ ] 문서의 라인 참조에 함수·상수명 병기 (AD-4)

## Open Questions

**~~Q1~~ 해소** — D-143 신규 채번. 착수 직전 재확인 완료(헤더 최대 142, 변경이력의 143은
안내 문구의 "다음 번호" 예고일 뿐 실제 등재 없음). *(2026-08-24 병합 충돌로 **D-161** 재부여)*

**~~Q2~~ 해소** — 기동 로그로 4단 미확정 확인 시 제거. 감축(-5~-20줄)이 아니라
사다리 단순화가 목적임을 L4에 명시.

**Q3 (비블로킹)** — `prometheus_enabled`(참조 0건) 처리. **기본: 기한부 존치** —
D-161의 첫 적용 사례로 삼는다(만료일 부여 → 기한 도래 시 삭제 또는 사유 붙인 연장).

**Q4 (비블로킹)** — 완결 계획서 아카이빙 범위. 이번엔 **INDEX 신설까지만** 하고 실제 이동은
보류한다. 인덱스가 있으면 이동 판단의 근거가 생긴다.
