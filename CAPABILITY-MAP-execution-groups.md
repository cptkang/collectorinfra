# Capability Map: 복합 질의 실행 그룹 (plans/82 · D-176 예약)

> 작성일: 2026-08-28 | **개정**: v5(2026-08-28 — Wave 5·6.5 구현 완료 반영)
> **상태**: 1차 4개 모듈 **구현 완료**(D-176) · `empty-answer-diagnosis`·`spike-condition` **구현 완료**(D-176 후속1·후속2, 기본 **ON** 전환) · `host-discovery`·`scope-select` **구현 완료**(D-176 후속3·후속4, 기본 ON) · 잔여 3개(`group-artifacts`·`group-ui`·`solution-pipeline`)는 **U1·U2·U3·U8 대기**
> 근거 계획: `plans/82-multi-zone-sequential-query-and-solution-routing.md` (v6)
> 실측 기준: 현 브랜치 `multiintent` HEAD `c7d47e8` + 미커밋 동시 작업(§충돌 표 참조)

## 왜 분해하는가

`plans/82`의 요구 4건은 **독립적으로 검증·출시 가능한 역량 여러 개**를 묶고 있다.
Phase 0 판정 기준 3항 전부에 해당한다:

- 서로 다른 소비처·데이터를 갖는다 (검증기 / 레지스트리 / 실행 루프 / 산출물 / 탐색 / UI / 0건 진단)
- 수용 기준이 **따로 출시·검증 가능한 그룹**으로 뭉친다 (`plans/82` §7 Wave가 이미 그 형태다)
- 하나를 잘라도 나머지 요구사항을 다시 쓸 필요가 없다 (예: 탐색을 빼도 존 순차 조회는 성립)

## 모듈 지도

| Module id | 책임 | 의존 | plans/82 | Tier | 승인 |
|---|---|---|---|---|---|
| `multi-dialect-guard` | 멀티 경로 SQL 검증에 **엔진 방언 그물** 복원 + 실행오류 재생성 1회 | — | Wave 1 | 1 (V) | 불요 |
| `group-registry` | `solutions`·`zone_groups` 레지스트리 축 + 파생 API + `partition_execution_groups` | — | Wave 2 | 0 | 불요 |
| `prior-scope-wiring` | 선행 결과 **출처(`_source_db`) → 대상 스코프(`TargetRef.db_id`)** 배관 결손 수정 | — | Wave 3.5 | 0 | 불요 |
| `group-runner` | 실행 그룹 순차 루프 + 그룹별 결과 수집 + **그룹 계측(O)** + **부분 결과 노출** | `group-registry` | Wave 3 | 0~1 | 불요 |
| `group-artifacts` | kind별 산출물 정책 — peer는 그룹별 파일 N개, dependent는 1개 · `output_files` | `group-runner` | Wave 4 | 0 | **U3** |
| `host-discovery` | 인가된 존 순회 **대상 소재 확정** + 가용성 병기 + 0건/다중 히트 처리 | `group-registry`, `prior-scope-wiring` | Wave 5 | 0~1(+G) | ✅ **구현 완료**(D-176 후속3 · U4·U5·U6·U7·U12) |
| `group-ui` | peer 그룹 섹션 · 부분 결과 점진 렌더 · 다중 다운로드 · 탐색 경과 · **플래그 전환** | `group-artifacts`, `host-discovery` | Wave 6 | — | **U1 · §10.3 PII** |
| `scope-select` | 범위 사전 선택 역질문 — **존 축 실발동** · 미조회 범위 기록 · 재확장 패널 | `group-runner`(※`group-ui` 불요 — 기존 clarification 렌더러 재사용) | Wave 6.5 | 2 (L) | ✅ **구현 완료**(D-176 후속4 · U9·U10·U11·U13) |
| `solution-pipeline` | 솔루션 축 라우터 출력 계약 확장 + `capabilities`/`requires` 파이프라인 전개 | `group-registry`, `host-discovery` | Wave 7 | — | **U7·U8** |
| `empty-answer-diagnosis` | **0건 원인 진단** — 조건 퍼널(MFS/XSS) · 표현 불가 조건 노출 · 0건 재생성 판정 | **1차만**(`group-runner`) | Wave 8 | 1 (V) | ✅ **구현 완료**(D-176 후속1) |
| `spike-condition` | **급증 조건 표현** — 기간 대비 차분(%p) 비교 SQL **결정적 조립**(엔진 분기) · 파일시스템 단위 유지 · 한계 3건 표기 | `empty-answer-diagnosis`(어휘 선언 공유) | Wave 9 | 0~1 | ✅ **구현 완료**(D-176 후속2 · 파일시스템 축 한정) |

**의존 방향은 단방향이며 순환이 없다.** `group-registry`가 축을 선언하고 `group-runner`가 그것을
소비하며, `scope-select`는 `group-runner`의 계측 없이는 임계를 정할 수 없다(P13).

## 착수 순서

```
1차 (승인 불요 · 회귀 0 · 즉시 이득)
    multi-dialect-guard ─┐
    prior-scope-wiring ──┤   서로 독립 — 순서 무관
    group-registry ──────┴─→ group-runner

2차 (사용자 확정 필요)
    group-runner ─→ group-artifacts(U3) ─┐
    group-registry ─→ host-discovery(U4·U5·U6) ─┴─→ group-ui(U1·PII) ─→ scope-select(U9~U13)
    host-discovery ─→ solution-pipeline(U7·U8)

1.5차 (2차와 독립 — 1차 자산만 필요 · U14 권고=즉시)
    group-runner ─→ empty-answer-diagnosis ─→ spike-condition
                    ↑ 요구 4 (0건 진단)        ↑ 요구 5 (급증). U1~U13을 기다리지 않는다
```

> **v3 추가(2026-08-28)**: 요구 5(**급증 조건 표현**)로 `spike-condition`이 신설됐다. 이 모듈은
> `empty-answer-diagnosis`가 만드는 `config/change_terms.yaml`(변화 어휘 선언)을 **확장**하고, 급증을
> §6.13에서 **퍼널의 정식 단계로 합성**한다 — 그래서 의존 방향이 `empty-answer-diagnosis → spike-condition`
> 단방향이다(역방향 없음: Wave 8은 Wave 9 없이 완결되며 급증은 "미반영" 표기로 처리된다).
> **판정 근거는 §6.9 실측 4건** — 데이터·표현 가능성은 있고 **파이프라인이 3겹으로 막고 있다.**

> **v2 추가(2026-08-28)**: 사용자 요구 4(**빈 결과 원인 진단**)로 `empty-answer-diagnosis` 모듈이
> 신설됐다. 이 모듈은 **1차 자산만 의존**하고 U1~U13과 무관해 **2차 대기 중 먼저 착지 가능한 유일한
> 항목**이다(`plans/82` §6 · Wave 8 · U14). 1차 4개 모듈은 이미 구현 완료(D-176 등재)다.

**1차 4개 모듈이 이번 구현 범위다.** 근거: ①`plans/82` §10.3이 *"Wave 1~5는 PII 이슈와 무관 —
승인 없이 진행 가능"* 으로 판정 ②Wave 4·5는 U3~U6 답에 따라 산출물·탐색 정책이 갈린다
③Wave 6은 계획서 자체가 **사용자 승인 게이트**로 명시.

## 모듈별 독립 검증 가능성 (분해가 정당한 근거)

| Module | 이 모듈만으로 검증되는 것 | 다른 모듈 없이 출시 가능? |
|---|---|---|
| `multi-dialect-guard` | DB2 대상에 `LIMIT`이 나오면 보정/재생성된다 | ✅ 단독 조회에도 이득 |
| `group-registry` | `partition_execution_groups([b0,gp,yd]) == [bank, common]` 순서 | ✅ 소비처 없으면 no-op |
| `prior-scope-wiring` | 팬아웃 결과의 `TargetRef.db_id`가 **행별로** 다르게 매겨진다 | ✅ 어떤 파이프라인이든 이득 |
| `group-runner` | 그룹 1개면 바이트 동일 / 2개면 `query_order` 순서 · 실패 격리 | ✅ 플래그 off 기본 |
| `empty-answer-diagnosis` | 0건 질의에 단계별 잔존 표·끊긴 지점·미반영 조건 경고가 나온다 | ✅ 플래그 off 기본 · 0건일 때만 발동 |
| `spike-condition` | 기간 대비 비교 SQL이 **엔진별로** 조립되고 **파일시스템 단위 행**이 유지된다 | ✅ 플래그 off 기본 · 퍼널 없이도 단독 이득(급증 질의가 표현된다) |

## 동시 작업 충돌 표 (착수 전 실측 · `plans/82` §11.1)

이 작업 트리에 **`plans/81`·`plans/83` 구현이 미커밋 상태로 진행 중**이다(30+ 파일).

| 82가 수정할 파일 | 동시 변경 | 충돌 판정 |
|---|---|---|
| `src/nodes/multi_db_executor.py` | clean | 안전 |
| `src/utils/prior_targets.py` | clean | 안전 |
| `src/orchestration/subagents.py` | clean | 안전 |
| `src/utils/query_gen_common.py` | clean | 안전 |
| `src/routing/registry.py` · `config/db_registry.yaml` | clean | 안전 |
| `src/state.py` · `src/nodes/result_merger.py` | clean | 안전 |
| `src/orchestration/process_query.py` | **M** | **낮음** — 동시 hunk는 `_resolve_hostname`·`_collect_one_target`·`_fanout`. 82의 대상 `_resolve_db_id`(84~143)에는 hunk 없음 |
| `src/config.py` | **M** | **중간** — 동시 hunk가 `CompositeConfig`(:970)에 있고 82도 같은 클래스에 추가한다. **행 단위로는 분리 가능** |

**대응**(`plans/82` §11.1 + Known Mistakes "병렬 작업 트리 책임 소재"):
- 구현은 공유 트리에서 진행한다(8/10 파일이 clean이므로 격리 비용이 이득을 넘는다).
- **검증은 격리 worktree에서 자기 파일만 얹어 수행**한다 — 공유 트리 테스트 실패를 내 변경으로
  오귀속하지 않기 위함. `git worktree add <dir> HEAD` + 내 변경 파일만 복사.
- `src/config.py`는 **`MultiDBConfig`에만 추가**하고 `CompositeConfig`는 1차 범위에서 건드리지 않는다
  (탐색·범위선택 플래그는 2차 모듈 소관 → 충돌 회피).

## 이 지도가 승인되면

각 모듈에 `SPEC-<module-id>.md`를 쓰고(1차 4개), `tasks/plan-82.md`·`tasks/todo-82.md`에 계획·태스크를
남긴 뒤 의존 순서로 구현한다. **지도 승인 전에는 어떤 모듈 SPEC도 쓰지 않는다**(skill Phase 0 게이트).
