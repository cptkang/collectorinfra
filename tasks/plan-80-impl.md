# Plan: Plan 80 실행 — 차수 0 + 차수 2 (구현)

> Map: `CAPABILITY-MAP-intent-extraction.md` · 상위: `plans/80` §5 WU 표
> 승인(2026-08-27): 차수 0 + **G-WHEEL 해제**(instructor 반입 완료) · todo-80 재조정 완료
> ⚠ `tasks/{plan,todo}.md`는 79 트랙 A가 점유 → 파일명 분리(`-80-impl`). `todo-80.md`와도 별개다
> (저쪽은 **문서 정정 축**, 이쪽은 **구현 축**).

## 구현 순서와 근거

```
① router-output-contract   (WU-01·02·03)   ← 먼저: E-2가 S-1 감시 대상 리스크를 미리 줄인다
② structured-output-backend (WU-07)         ← 병렬 가능하나 ①과 같은 파일을 안 건드림
③ composite-gap-tests       (WU-04)         ← 완전 독립(tests/test_orchestration/)
④ intent-extraction-typing  (WU-08·09·10)   ← ② 랜딩 후 스펙 작성 → 구현
```

**병렬 가능하지만 순차로 간다** — 각 단계 후 전체 회귀를 돌려 **책임 소재를 확정**하기 위해서다
(Known Mistakes: 병렬 작업 트리의 실패는 귀속이 어렵다).

## 검증 체크포인트

| 시점 | 확인 |
|---|---|
| 각 태스크 후 | 해당 스위트 통과 |
| 각 모듈 후 | **전체 회귀 기준선 대조** + `arch_check --ci` |
| 전체 후 | 기준선 동일 + 신규 테스트 수만큼 `passed` 증가 |

**기준선 확보 방식**: 착수 전 `pytest -q`를 1회 돌려 실패/에러 수를 기록한다.
불일치 시 `git worktree add <dir> HEAD` 격리 사본으로 대조한다(`git stash` 금지).

## 리스크

| # | 리스크 | 완화 |
|---|---|---|
| R1 | 프롬프트 텍스트가 의도치 않게 바뀌어 **S-1 측정 기준이 흔들린다** | 상수 신설만 하고 본문 미변경 · **바이트 동일 단언**(S8) |
| R2 | 허용 집합이 프롬프트 정의와 **드리프트** | 코드가 정본을 import(사본 금지) + **동기화 단언**(S3) |
| R3 | 옵트인 계약 파손 — off인데 `fault_diagnosis` 허용 | 플래그 종속 집합 + **양쪽 상태 단언**(S2) |
| R4 | `instructor` 도입이 기존 의존을 깬다 | `pip check` 통과 확인 완료 · `openai` 2.26.0 유지 확인 |
| R5 | 어댑터가 실 응답 shape에서 깨진다(콘텐츠 블록 리스트) | `coerce_content_text` 경유 **강제 + 단언**(S7) |
| R6 | xfail 테스트가 갭 해소 후 방치 | `strict=True` — 해소 시 XPASS로 **실패**시켜 강제 인지 |
