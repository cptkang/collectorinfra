# 구현 계획 — Plan 82 Wave 5·6.5 (존 순회 탐색 · 범위 사전 선택)

> 지도: `CAPABILITY-MAP-execution-groups.md` v4 · 계획: `plans/82` v6 §4.3~§4.4 · §5.3~§5.5
> SPEC: `SPEC-host-discovery.md` · `SPEC-scope-select.md` · 예약 결정 **D-176 후속3·후속4**
> 태스크: `tasks/todo-82-w56.md`

## 범위 (사용자 확정 2026-08-28 · 인터뷰 3라운드)

| 항목 | 확정 | 계획서 권고와의 관계 |
|---|---|---|
| 다음 모듈 | **host-discovery + scope-select** | — |
| U4 조기 종료 | **전수 순회**(조기종료는 옵트인 플래그) | 권고 채택 |
| U5 다중 히트 | **되묻기** — 선택지를 발견된 존으로 좁힘 | 권고 채택 |
| U6 0건 | **순회한 존 목록 명시 안내** | 권고 채택 |
| U7 종합 응답 | **LLM 합성 1회 + 결정적 폴백** | 권고 채택 |
| U9 축 | **솔루션 + 존 축 통합** | ★**계획서 개정**(§5.3 불변식 1이 존 축을 금지했었다) |
| U10 미응답 | **전체 조회로 진행** | 권고 채택 |
| U11 임계 | **임계 없음 — 그룹 2개 이상이면 항상** | ★**계획서 개정**(30초 잠정값 권고였다) |
| U12 탐색 캐시 | **60초 TTL 기본 on** | ★**계획서 개정**(TTL 0 옵트인 권고였다) |
| U13 | **지금 구현** | ★**계획서 개정**(부분 결과 먼저 관측 권고였다) |

**U1·U2·U3·U8은 이번 범위 밖** — `group-artifacts`·`group-ui`·`solution-pipeline` 소관이다.

## ★ 착수 전 실측으로 뒤집은 전제 2건

1. **"scope-select는 발동 불가"는 틀렸다.** 내가 `ZONE_GROUP_EXCLUSIVE`의 발동 조건을 결정
   문서 요약만 보고 일반화한 오류다. 실측: 그 게이트는 **혼합 `selected_db_ids`·혼합 텍스트**
   에만 발동하므로 존 미지정 질의는 전 존 팬아웃되고, `partition_execution_groups`가
   **2 그룹**을 만든다. 사용자 지적(*"운영환경에는 폴스타가 여러개다"*)이 정확했다.
2. **계획서 §4.4 발동 조건 1(`backend != "sql"` 또는 `requires: [host_location]`)은 오늘 항상
   거짓**이다 — 등록 solution이 `polestar`(backend=sql) 하나뿐. 그대로 구현하면 탐색이 영원히
   발동하지 않아, **"단일 대상 API 경로 + 존 미해소"** 로 좁혀 읽는다(SPEC §이탈 참조).

## 기준선 (착수 전 실측)

| 대상 | 값 |
|---|---|
| 대상 영역(`test_nodes`·`test_utils`·`test_db_adapters`·`test_middleware`·`test_orchestration`·`test_semantic_routing`·`test_empty_answer`·`test_spike`·`test_api`·`test_composite`·`test_state*`·`test_config_env_reload`) | **0 failed** — 사전존재 실패 13건을 정리 작업에서 전부 해소 |
| `arch_check --ci` / `overfit_check --ci` | exit 0 / 신규 유입 0 |
| 전체 저장소 | `test_e2e_polestar`(DBHub 필요)·`test_plan33`(프로필 의존) 잔여 — **이번 범위 밖** |

> **판정 기준**: 대상 영역 **0 failed를 유지**하고 신규 테스트를 더한다.

## 의존 순서

```
W5-1 host_discovery(순수 판정) ─┬─→ W5-3 배선(process_query ⑤) ─→ W5-4 응답·플래그
W5-2 host_sweep(순회+TTL 캐시) ─┘

W65-1 scope_select(순수 게이트) ─→ W65-2 라우트 배선 ─→ W65-3 프론트 ─→ W65-4 미조회 기록·재확장
```

- **두 모듈은 서로 독립**이다(공유 파일 0) — 순서 무관하게 병행 가능.
- W5-1·W5-2는 서로 독립(순수 판정 vs I/O). W65-1은 순수라 프론트 없이 검증된다.
- 배선(W5-3·W65-2)은 각 레인 마지막 — 플래그 off 기본이라 회귀 0.

## 파일 소유권

| 파일 | 소유 | 현재 상태 |
|---|---|---|
| `src/domain/host_discovery.py` (신규) | W5-1 | — |
| `src/orchestration/host_sweep.py` (신규) | W5-2 | — |
| `src/orchestration/process_query.py` | W5-3 | **M**(1차 D-176 변경분) — 추가만 |
| `src/domain/scope_select.py` (신규) | W65-1 | — |
| `src/api/routes/query.py` | W65-2 | **M** — `_zone_clarification_or_none` 인접, 추가만 |
| `src/static/js/app.js` | W65-3 | **M**(Plan 83 변경분) — `renderZoneClarification` 확장 |
| `src/config.py` | W5-4 · W65-2 | **M** — `CompositeConfig`만. `Text2SQLConfig`(Wave 8·9)와 hunk 분리 |
| `src/state.py` | W5-4 · W65-4 | **M** — 추가만 |

## 위험과 완화

| 위험 | 완화 |
|---|---|
| **권한 밖 존 존재 여부 누출** | 순회 대상을 `allowed_db_ids` ∩ 활성 폴스타 존으로 **먼저** 좁히고, 테스트가 호출 목록으로 단언 |
| 0건 캐시로 신규 서버가 60초간 안 보임 | **0건은 캐시하지 않는다**(U12 채택의 필수 가드) |
| 탐색 실패를 "서버 없음"으로 강등 | `SweepOutcome.errors`로 존별 사유 분리 · 응답에서 **"확인 실패"** 로 구분 표기 |
| 탐색이 앞 순위를 덮어씀 | `_resolve_db_id` ①~④가 성립하면 **미진입**(호출 수 0 단언) |
| 범위 질문이 진행을 막음 | 미응답 = 전체 조회(U10) · 모호성 해소 대기 시 비발동 · 비대화 채널 비발동 |
| 반복 노출 습관화(U11 임계 없음의 대가) | **발동률 관측 카운터 필수** — 계획서 P15. 못 보면 통제 불가 |
| `src → noise_gate` 결합 확대 | 재사용은 **resolver 1개**로 한정하고 주입점(콜백)을 둬 테스트가 대역으로 대체 |

## 검증 체크포인트

| 시점 | 확인 |
|---|---|
| 각 태스크 | 해당 스위트 통과 + `arch_check --ci` |
| 각 레인 완료 | 대상 영역 전체 재실행 → **0 failed 유지** |
| 전체 완료 | 대상 영역 0 failed + 신규 전량 통과 · `overfit_check` 0 · 플래그 off 골든 |

## 산출물

- 코드: 위 소유권 표 (신규 3 · 수정 5)
- 테스트: `tests/test_discovery/` · `tests/test_scope_select/`
- 문서: `docs/02_decision.md` **D-176 후속3·후속4 등재**(계획서 개정 4건 근거 포함) ·
  `plans/82` 상태 · `plans/INDEX.md` · `CAPABILITY-MAP` v5
