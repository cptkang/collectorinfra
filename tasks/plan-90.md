# Plan 90 실행 계획 — 채팅창 폴스타(존) 스코프 표시·선택·해제

> `plans/90` v2 · `CAPABILITY-MAP-90.md` · `SPEC-thread-db-scope.md` · `SPEC-scope-chip-ui.md` · D-205
> 게이트 적용값: G-1 승계 끊기 · G-2 1회 전송 후 승계 · G-3 입력창 위 · G-4 mapped_db_ids도 비움(계획서 권고안)

## 구현 순서와 그 이유

```
T0 1단 결손 고정 테스트(실패) + T2 _AMBIENT_KEYS 보정   ← 독립·1줄. 없으면 칩 선택이 운영에서 무시된다
      ↓ (병렬 가능)
T1 db_scope.py — resolve/build/options + context_resolver 위임   ← 표시·해제·계약이 전부 이 함수에 의존
      ↓
T1b source 신호 — semantic_router · subagents db_origin · _collect_db_promotion 승격
      ↓
T4 reset — state/context_resolver/pre-gate(ⓒ는 reset 턴 한정, SPEC Open Q1)
      ↓
T3 contract — schemas · 4경로 db_scope · GET /scope/options · 라우터 등록
      ↓
T5 프론트 칩 — 마크업·CSS·렌더·팝오버·전송/수신 배선 · v=10
      ↓
T6 문서 — D-205 본문 · D-143 후속4 부기 · INDEX · 계획서 상태 · Known Mistakes(있으면)
      ↓
T7 실 브라우저·실 파이프라인(과금 — D-127 건별 승인 대기)
```

**되돌림 위험 지점**: T4 pre-gate 판정 변경이 기존 `test_followup_turn_passes`와 충돌할 수 있어 SPEC
Open Q1로 범위를 reset 턴에 한정했다. T1b는 `target_databases` shape를 바꾸지 않는다(Open Q3).

## 검증 체크포인트

| 뒤 | 확인 |
|---|---|
| T0/T2 | `pytest tests/test_orchestration/test_deep_agent_wiring.py -q` |
| T1/T1b/T4 | `pytest tests/test_routing/test_db_scope.py tests/test_multiturn -q` + `tests/test_orchestration/test_result_aggregator.py` |
| T3 | `pytest tests/test_api/test_db_scope_contract.py tests/test_orchestration/test_zone_selection.py tests/test_scope_select -q` |
| T5 | `node --check` + `pytest tests/test_api/test_ui_zone_scope.py tests/test_api/test_ui_scope_select.py -q` |
| 전체 | `pytest tests/test_orchestration tests/test_scope_select tests/test_api tests/test_nodes tests/test_multiturn tests/test_routing -q` · `arch_check --ci` · `overfit_check --ci` |

## 실행 결과 (2026-09-09)

**T0~T6 완료 · T7만 승인 대기.** 계획한 순서를 그대로 탔다. 옆길 2건: ①구현 도중 다른 세션의 `test_update`
병합 + 스태시 복원이 6개 파일 충돌을 만들었고 D-200이 선점돼 **D-205로 재부여**(협업 세션이 치환·충돌 해소,
본 세션은 그동안 편집 보류) ②기준선 대조에 `git stash`를 써서 `docs/18`에 실수로 등재. 이탈 3건의 근거는
`SPEC-thread-db-scope.md` Open Questions. 회귀는 병합 전 1809 · 병합 후 1932 passed(실패 0).
