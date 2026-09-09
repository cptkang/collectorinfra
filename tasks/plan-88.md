# Plan 88 실행 계획 — 복합 질의 순차 의존 처리 (1차)

> `plans/88` v2 · `CAPABILITY-MAP-88.md` · D-203 예약 · 1차 = 사용자 확정 게이트 없는 4개 모듈

## 구현 순서와 그 이유

```
T1 prior-dependency-gate (판정 함수 + 테스트)      ← 현행 결함(선행 0건 → 전체 조회)을 먼저 빨갛게
      ↓
T2 gate 배선 (2단 orchestrator · 1단 tools · UI 배지) ← 판정이 있어야 배선할 대상이 있다
      ↓
T3 dependency-notes (state · 렌더 · 집계기 · deep_agent · 라우트 · 충족도 병기)
      ↓                                            ← 채널이 있어야 T2·T4·T5의 사유가 응답에 닿는다
T4 scope-postcheck (판정 + 2단·1단 배선)
      ↓
T5 prior-scope-by-db (분할 + multi_db_executor)     ← T3 노트 채널 소비
      ↓
T6 문서 (계획서 상태 -TODO→-WIP · INDEX · D-203 본문 등재 · 지도 상태)
```

**병렬 가능**: T4와 T5는 파일이 겹치지 않는다(T4=orchestration, T5=nodes/utils). 단 둘 다 T3의 노트 채널을 쓴다.
순차로 둔다 — 한 사람이 하는 작업이라 병렬 이득이 없다.

## 검증 체크포인트

| 단계 | 통과 조건 |
|---|---|
| T1 | 신규 테스트 전부 통과 · `arch_check --ci` 0 |
| T2 | "플래그 off = 현행 동일" 케이스 통과 · `test_orchestrator`·`test_prior_targets_wiring`·`test_deep_agent` 회귀 0 |
| T3 | 충족도 미달 사유가 응답 본문에 나타나는 테스트 통과(현행 0건) · `tests/test_api` 회귀 0 |
| T4·T5 | 각 SPEC 성공 기준 · 기준선 스위트(618 passed) 회귀 0 |
| T6 | `overfit_check --ci` 신규 유입 0 · 격리 worktree 전체 스위트 |

## 기준선

공유 트리 2026-09-09: `tests/test_orchestration tests/test_composite tests/test_nodes/test_multi_db_group_loop.py tests/test_state.py`
→ 618 passed · 1 skipped(68s). 최종 대조는 `git worktree add`(스태시 금지)로 자기 파일만 얹어 수행한다.

## 실행 결과 (2026-09-09)

**T1~T6 완료.** 계획한 순서(게이트 → 채널 → 대조 → 분할 → 문서)를 그대로 탔고 되돌아간 단계는 없다. 옆길 두 번:

- **테스트 대역 설정**: 기존 `test_sufficiency`(SimpleNamespace 설정)·`test_multi_gate_token_budget`(SimpleNamespace run)이
  새 속성 접근(`app_config.composite`, `run.prior_scope_by_db`)에서 깨졌다 → `getattr`+`isinstance` 방어 읽기로 수정.
  `multi_relevant_gate`·`path_parity_enabled`가 같은 이유로 `is True` 비교를 쓰고 있었다.
- **동시 편집**: 같은 날 두 세션(plans/89 진행 이벤트 · plans/90 DB 스코프)이 `agent_orchestrator.py`·`deepagents_tools.py`·
  `query.py`·`config.py`·`schemas.py`를 함께 고쳤다. 89 세션이 내 `_gate_level` 반환(`runnable`) 위에 진행 이벤트를 얹어 갔고
  충돌은 없었다. 설정 카탈로그 개수 단언은 세 세션 합산 실측(322)으로 확정.

검증: 신규 4파일 78건 통과 · `tests/test_orchestration tests/test_composite tests/test_nodes tests/test_api tests/test_state.py
tests/test_graph.py` 1,850+ passed(공유 트리) · `arch_check --ci` 0 · `overfit_check --ci` 신규 유입 0.
`test_prompt_render_matrix` 스냅샷 실패는 격리 worktree(HEAD 단독)에서도 재현 — 이 작업과 무관. ruff는 venv 미설치로 미실행.

## 2차 실행 결과 (2026-09-09 · "중단된 작업을 재개하라")

게이트 답 없이 §8 권고안을 가정으로 두고 진행했다(G-5 본 계획이 배선 · G-6 (a) · G-7 표기만 · G-3 `.env`는 미변경).
순서: T7 `validate_plan_dag`+되먹임(W8) → T8 표지 재분해(W2, W8과 같은 함수 `_enforce_plan_contract`) → T9 `sequential_runner`(W9)
→ T10 분해 골든셋+`--decomposition`(W4). **G-4는 불필요로 판정** — 플래너 프롬프트 예시 3이 이미 data→data 순차라 프롬프트·골든 무변경.

옆길: 작업 중 사용자 `stash pop`이 원격 6커밋(b45ac9e)과 충돌 → 6파일 UU. 마커를 해소하고(query.py는 plans/89 세션의
`_graph_event_stream` 채택 — 원격 `_next_event_or_timeout`과 같은 문제의 다른 구현이라 **사용자/89 세션 판단 필요**), 원격이
D-198~D-202를 선점해 D-198→**D-203** 재부여. 병행 세션 collectorinfra-99가 docs/02·docs/18·test_settings_catalog 마무리를 맡아 그
세 파일은 보류(카탈로그 실측 327 전달).

검증: 신규 2파일 31건 · `tests/test_orchestration tests/test_composite tests/test_nodes/*(관련) tests/test_graph.py tests/test_state.py`
792 passed · `test_settings_help` 커버리지 통과 · arch/overfit 0 · `eval_routing.py --decomposition --dry-run` 정합 OK.

## 잔여

`.env` `TEXT2SQL_PATH_PARITY` 명시(G-3 · 운영 설정) · `eval_routing.py --decomposition` 실 실행(D-127 건별 승인) · docs/02 D-203 본문에 2차 완료 반영(보류 해제 후).
