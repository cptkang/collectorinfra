# Capability Map: 복합 질의 순차 의존 처리 (`plans/88` · D-203 예약)

> **작성일**: 2026-09-09 · **근거**: `plans/88-sequential-dependent-composite-query.md` v2
> **상태**: 1차 4모듈 **구현 완료**(2026-09-09 · D-203 본문 등재 · 신규 테스트 78건 · 플래그 3종 기본 off) ·
> **2차 4모듈 구현 완료**(2026-09-09 재개 지시 · §8 권고안 가정 — `sequential-decompose`(프롬프트 무변경: 예시 3이 이미 data→data) ·
> `plan-dag-validation` · `sequential-fallback-runner`(HITL on이면 미진입) · `sequential-eval`(dry-run만 — 실 실행은 D-127) ·
> 신규 테스트 31건 · 플래그 3종 기본 off). 잔여: `.env` parity(G-3 · 사용자) · D-127 실 평가.
> **§8 게이트 전건 사용자 확정(2026-09-10 인터뷰 · `plans/88` §11.1)** — 가정 1~5 전부 승인. 운영 `.env` = 1차 3종 on · 2차 3종 off · `PATH_PARITY=false`(적용은 실 실행 단계 2·3·6 통과 후). G-2 선별 기준 표기 편입·구현(`extract_selection_basis`) + off 관측 로그 대칭(신규 테스트 16건). G-3 on은 82 2차 이관. 실 검증 Gemini 11건 완료 · 운영 `.env` 반영 완료 · 1단 차단어 축 한정(`_is_global_scope`) · 샌드박스 프로필 복원. **계획 완료 · 태그 해제(2026-09-10) — 코드 잔여 0 · 후속 전건 종결**: 단계 4·5·7 실 실행 11건 → 2차 3종 운영 on 확정 · W4 5/5 · R-E (c) 구현·재검증 통과(`LATEST_ONLY=true`).
> **실측 기준**: `multiintent` HEAD `9945284` + 사용자 미커밋 스테이징(30+ 파일 — 본 지도가 여는 `src/` 파일은 전부 clean)
> **기준선**: `pytest tests/test_orchestration tests/test_composite tests/test_nodes/test_multi_db_group_loop.py tests/test_state.py`
> → **618 passed · 1 skipped**(68s · 2026-09-09 공유 트리)

## ASSUMPTIONS I'M MAKING (사용자 지시 *"수립된 계획을 검토하여 구현을 진행하라"* 의 해석)

1. **1차 = 사용자 확정 게이트가 없는 모듈만** 먼저 구현한다 — 플래그 기본 off · off면 비트 동일 · 회귀 0.
   `plans/82`가 1차(승인 불요)·2차(U1~U13)로 나눠 진행한 선례와 같다.
2. **2차(G-3~G-7 종속) 모듈은 SPEC을 쓰지 않는다** — 지도 승인 전 모듈 SPEC 금지(skill Phase 0 게이트).
   G-4(프롬프트 골든)·G-5(79 소유 파일)·G-6(3·4단 러너)·G-3(.env)은 사용자 답이 있어야 한다.
3. **G-1은 권고안 (a)로 가정**한다 — 선행 0건이면 후속 **미실행 + 사유**. (c) 현행 유지는 침묵 오류라
   구현할 가치가 없고, (b) 되묻기는 `plans/82` §6 실측(세션 시간 ~2배)에 반한다. 다만 **플래그 off가 기본**이라
   가정이 틀려도 운영 동작은 변하지 않는다.
4. **G-2는 "현행 유지 + 표기"로 가정**한다 — 1차 경과 블록은 선행 대수·컬럼·절단만 표기하고 "높은"의 해석값
   추출은 하지 않는다(t1 SQL 파싱은 2차).
5. `task_plan[].status="skipped"` 도입은 소비처 전수 grep 결과(§검증)로 안전하다고 판단했다 — UI 배지 1줄 추가.
→ 틀린 가정은 플래그를 끈 채로 되돌릴 수 있다. 2차 착수 전에 G-1~G-7 답을 받는다.

## 모듈

| Module id | 책임 | 소비자 | Depends on | 차수 |
|---|---|---|---|---|
| `prior-dependency-gate` | `input_from` 선행 결과를 **실행 전** 결정적으로 판정(실패·0건·식별 컬럼 부재·절단) → 후속 미실행 + 사유. 1단·2단 호출부 대칭 · `prior_targets` 소비자에도 동일 적용 | `agent_orchestrator` · `deepagents_tools` | — | **1차** |
| `dependency-notes` | 사유 채널 단일화 — `AgentState.dependency_notes`(요청 스코프) · 게이트/대조/절단/충족도 미달 합류 · 응답 말미 **결정적 경과 블록** · `/query` 응답 노출 · 죽은 `sufficiency_shortfalls` 병기 | `result_aggregator` · `deep_agent` · `routes/query.py` · 운영자 | `prior-dependency-gate`(노트 생산자) | **1차** |
| `scope-postcheck` | 후속 조회 결과의 **실행 후** 대조 — 스코프 밖 서버 행 제거(outside) · 스코프 안 미조회 서버 표기(missing). 결정적 컴파일 경로는 no-op | `agent_orchestrator` · `deepagents_tools` | `prior-dependency-gate`(스코프 정본) · `dependency-notes`(노트) | **1차** |
| `prior-scope-by-db` | `prior_rows`에 `_source_db` 패스스루 → DB별 스코프 분할 → 선별 0대 DB 미조회 + 노트. 실행 그룹(82 2차) 계약만 확정 | `multi_db_executor` | `dependency-notes` | **1차** |
| `sequential-decompose` | 플래너 예시 3-2 + 1단 지시문 + 순차 표지 감지 + 재분해 1회 + 미적용 사유 | `intent_planner` · `orchestrator.py` 지시문 | `dependency-notes` · **G-4** | 2차 |
| `plan-dag-validation` | `validate_plan_dag` — backend 무관 DAG 검증 · 자동 보정 · 되먹임(예산은 `sequential-decompose`와 공유) · 폴백 `degraded` 사유 | `intent_planner` | `sequential-decompose` · **G-5** | 2차 |
| `sequential-fallback-runner` | 3·4단 빌드 전용 `sequential_runner` 2-pass 노드 · HITL 승인 on이면 미진입 | `graph.py` | `prior-dependency-gate` · `scope-postcheck` · `dependency-notes` · **G-6** | 2차 |
| `sequential-eval` | 라우팅/SQL 골드 순차 케이스 · `eval_routing` `input_from` 채점 | 평가 하네스 | `sequential-decompose` · D-127 | 2차 |

`.env`의 `TEXT2SQL_PATH_PARITY` 명시(W5 · G-3)는 모듈이 아니라 운영 설정 변경이다 — 지도에 넣지 않는다.

**Build order**
```
1차 (승인 불요 · 플래그 off 기본 · 회귀 0)
    prior-dependency-gate ─→ dependency-notes ─┬─→ scope-postcheck
                                               └─→ prior-scope-by-db
2차 (사용자 확정 후)
    dependency-notes ─→ sequential-decompose(G-4) ─→ plan-dag-validation(G-5) ─→ sequential-eval(D-127)
    gate + postcheck + notes ─→ sequential-fallback-runner(G-6)
```

## 경계가 이렇게 그어진 이유

- **게이트와 노트를 떼는 이유**: 게이트는 *판정*(순수 함수 · utils 계층)이고 노트는 *전달과 렌더*(상태·집계기·
  라우트)다. 판정을 상태 배선 없이 단독 테스트할 수 있어야 1단·2단 대칭을 같은 함수로 단언할 수 있다.
- **노트가 게이트 뒤에 오는 이유**: 노트 채널은 생산자가 있어야 검증된다. 현행 `sufficiency_shortfalls`가 정확히
  "채널만 있고 소비처 0건"인 죽은 상태라, 이번엔 생산자→채널→렌더를 한 줄로 잇고 **응답 본문에 실제로 나타나는가**를
  테스트로 고정한다.
- **사후 대조가 게이트에 의존하는 이유**: "스코프 밖"의 정본은 게이트가 계산한 `(scope_col, values)`다. 두 번 계산하면
  컬럼 종류(hostname vs name)가 어긋날 수 있다(D-061).
- **DB별 분할이 노트에만 의존하는 이유**: 분할은 `multi_db_executor` 내부 문제이고 게이트와 독립이다. 선별 0대 DB를
  미조회했다는 사실만 노트로 남긴다.
- **순환 없음**: 2차 모듈은 1차의 함수·채널을 소비만 하고 1차는 2차를 모른다.

## 모듈별 독립 검증 가능성

| Module | 이 모듈만으로 검증되는 것 | 다른 모듈 없이 출시 가능? |
|---|---|---|
| `prior-dependency-gate` | 선행 0건 → 후속 미실행(현행 결함 재현 테스트가 먼저 빨갛다) | ✅ 사유는 로그·task error에 실려 최소 노출 |
| `dependency-notes` | 충족도 미달 사유가 **응답 본문**에 나타난다(현행 0건) | ✅ 게이트 없이도 죽은 채널 복구만으로 이득 |
| `scope-postcheck` | LLM 폴백 SQL이 목록 밖 서버를 내면 제거된다 | ✅ 플래그 off 기본 |
| `prior-scope-by-db` | b0/gp 혼재 선행 행 → DB별 IN 목록이 **SQL 문자열에서** 분리된다 | ✅ 태그 없는 행은 비트 동일 |

## `status="skipped"` 소비처 전수 실측 (가정 5의 근거)

| 소비처 | 동작 | 판정 |
|---|---|---|
| `agent_orchestrator.py:71` `pending = [status=="pending"]` | skipped는 재실행되지 않는다 | 안전 |
| `result_aggregator._finalize_task` | `res["error"]`로 부분 실패 문구 — skipped 결과에 `error`를 실어 같은 경로 | 안전 |
| `replanner.py:223` 프롬프트에 `status=` 문자열 나열 | 문자열 그대로 노출(LLM이 읽음) | 안전 |
| `routes/query.py:_summarize_tasks` | `status` 그대로 전달 | 안전 |
| `src/static/js/app.js:2488-2495` | 알려지지 않은 status → "대기" 배지 | **"건너뜀" 배지 1줄 추가**(W1) |
| `deepagents_tools.py:280` collector status | completed/failed만 — skipped 추가 | 안전 |

## 동시 작업 충돌 표 (착수 전 실측)

사용자 스테이징 분(`git status --short` 2026-09-09): `noise_gate/*`·`docs/*`·`plans/5x`·`CLAUDE.md` — 본 지도가 여는
`src/orchestration/*`·`src/nodes/multi_db_executor.py`·`src/utils/query_gen_common.py`·`src/state.py`·`src/config.py`·
`src/api/routes/query.py`·`src/static/js/app.js`는 **전부 clean**. `docs/02_decision.md`·`plans/INDEX.md`는 병행 세션
(`plans/89`·`90`)과 겹치므로 **행 단위 추가만** 한다.

## 이 지도가 승인되면

1차 4개 모듈에 `SPEC-<module-id>.md`를 쓰고(`SPEC-prior-dependency-gate.md` · `SPEC-dependency-notes.md` ·
`SPEC-scope-postcheck.md` · `SPEC-prior-scope-by-db.md`), `tasks/plan-88.md`·`tasks/todo-88.md`에 순서·태스크를 남긴 뒤
의존 순서로 구현한다. 2차 SPEC은 G-3~G-7 답 이후.
