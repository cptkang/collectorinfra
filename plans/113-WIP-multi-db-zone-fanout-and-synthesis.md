# 113. 멀티 DB 조회 — 존 단위 위치어의 전 DB 팬아웃과 DB별 결과 종합 (공동존 김포·여의도 동시 조회 결함)

> **작성일**: 2026-09-22
> **상태**: **구현(2026-09-22 v3)** — 게이트 확정·변경(§7.1 · G-2 → 함께 구현 · G-5 → S-3 포함) · F-1·F-2·F-3·S-1·S-2·S-3·V-1 랜딩(§9) · **F-4는 측정 결과 기각(보류 — 재측정 조건 §9 · 부록 C)** · D-246 · 파일명 `-WIP`(잔여 = F-4 재측정)
> **이력**: v1 2026-09-22 분석·계획(코드 0건 · `-TODO`) → v2 2026-09-22 구현(사용자 지시 *"권고대로 구현을 진행하라."*) → v3 2026-09-22 F-2·S-3 구현 · F-4 측정 후 기각(사용자 지시 *"2단도 같이 고려하여 구현을 진행하고 범위 밖에 남은 작업들도 구현을 진행하라."*)
> **요청 취지(사용자 지시 원문, 2026-09-22)**:
> ① *"「공동존의 vm 중 최대 cpu와 최대 memory 서버 top 10을 정리해줘」라고 프롬프트를 입력하면 조회는 하지만 김포와 여의도를 동시에 조회하지 못하고 있다. 원인을 파악해봐라."*
> ② *"분석한 내용을 별도의 계획파일로 정리하라. 추가로 여러개의 DB를 분석해야 한다면 조사해야되는 내용을 각각의 DB에서 조사하여 종합하여 결과를 보여주도록 동작되는게 정상적인 동작으로 보인다. 이렇게 계획이 정리되어 있냐?"*
>
> **이 문서의 위치 — 정본이 아니라 델타다(D-053).**
> - 3단 위치 힌트 고정(F-1)은 **`plans/103` 격차표 D-1**(77행)·**WU P1-2**(249·282행, 미착수)의 **D-1 부분만 먼저 떼어** 닫는다. P1-2의 나머지(D-2 승계 · D-3 지시어 · D-4 실시간 사용률 · D-6 태스크 존 게이트)는 103에 남는다.
> - 결과 종합(S-1)은 **D-202 잔여 ①**(*"멀티 DB 순위 질의의 전역 재정렬·재LIMIT — 별도 승인 대상"*, `docs/02_decision.md:1941`)을 인수한다. 이 잔여를 소유한 계획서는 지금까지 **0건**이었다(`plans/`·`spec/`·`tasks/` grep 실측).
>
> **관련 결정**: D-004(원문 키워드 분류 금지) · D-005(멀티 DB 순차 독립 실행·부분 실패 허용) · D-035·D-068(판단은 결정적) · D-065(바 "공동존" → gp+yd) · D-066(단일/멀티 경로 대칭) · D-176·D-206(존 그룹 순차 실행) · D-202 잔여 ① · D-225(3단 기준) · `plans/108` G-2(2단 전용 결함 보류)
> **실측 기준**: 작업 트리 `multiintent` HEAD `cddac70`(미커밋 파일은 타 세션 작업 — 이 계획과 무관). 로컬 MLX `Qwen3.5-9B-OptiQ-4bit`(워커·오케스트레이터 둘 다 `mlx` — D-222 비과금, `scripts.bench --show-env` 확인). 재현 스크립트 안에서 `ACTIVE_DB_IDS=polestar_b0,polestar_cm_gp,polestar_cm_yd`로 덮어썼고 **DB 연결은 하지 않았다**(라우팅·분해 판정만). 코드 수정 0.

---

## 0. 요약

### 0.1 한 줄

**"공동존"(= 김포 `polestar_cm_gp` + 여의도 `polestar_cm_yd`)을 두 DB로 펼치는 결정적 장치가 1·2단에만 있고, 기준 경로인 3단(`semantic_router`)은 LLM 분류만 쓰는데 LLM은 DB를 하나만 고른다.**
두 DB가 조회되는 경우에도 결과 종합은 **이어 붙이기(concat)** 뿐이라, "top 10" 같은 순위 질의가 전체 기준으로 다시 정렬되지 않는다.

### 0.2 "DB별로 조사해 종합하는 동작이 계획으로 정리돼 있는가"에 대한 답 — **절반만**

| 단계 | 정리돼 있는 것 | 빠진 것 |
|---|---|---|
| **① 조사 대상 선정**(어느 DB를 볼지) | D-065 — 원문 위치어를 `target_db_hints`로 결정적 보강(`input_parser.py:134`)하고 1·2단은 그것으로 DB 집합을 고정(`subagents.py:405-463`) | **3단은 이 고정이 없다** — `plans/103` D-1로 등재만 됐고 P1-2 **미착수**. 2단도 복합 분해 시 고정을 끈다 |
| **② DB별 조사**(각 DB에서 실행) | D-005(순차 독립·부분 실패 허용) · D-176·D-206(존 그룹 순차) · 동일 스키마 SQL 공유(`multi_db_executor.py:705-739`) — **구현돼 있다** | — |
| **③ 종합**(합쳐서 답하기) | D-005는 병합을 `_source_db` 태깅 + 이어 붙이기로만 정의. 존별 **0행·실패 각주**(`output_generator.py:756`)만 있다 | **전역 재정렬·재LIMIT · 존별 요약 · 집계 재계산이 결정·계획 어디에도 없다** — D-202 잔여 ① 한 줄뿐 |

즉 사용자가 말한 정상 동작("각 DB에서 조사 → 종합해서 보여준다")은 ②만 성립한다. ①·③을 이 계획에서 **처음 명세**한다(§3).

### 0.3 권고

**F-1(3단 위치 힌트 고정) → S-1(순위 질의 전역 재정렬·재LIMIT) → S-2(응답 미리보기 DB 균형)** 순으로 먼저 한다. 세 작업 모두 각자 한 지점(라우터 노드 · `result_merger` · `output_generator`)만 고치면 된다 — 뒤의 두 지점은 사다리 전 단이 함께 지나는 합류점이다.
2단 결함(F-2)은 `plans/108` G-2(*"2단 전용 결함 보류 — 기준은 3단"*, 사용자 확정) 원칙대로 **보류**한다.

---

## 1. 증상과 재현

### 1.1 증상

질의 *"공동존의 vm 중 최대 cpu와 최대 memory 서버 top 10을 정리해줘"* — 조회는 되지만 김포와 여의도 중 **한쪽만** 조회된다(사용자 보고 · 내부망). 로컬 로그(`logs/sql/2026-09-22.sql`·`logs/audit-2026-09-22.jsonl`)에는 이 질의 기록이 없다. 로컬 `.env`는 `ACTIVE_DB_IDS=polestar,itam`이고 gp·yd 연결이 없다.

### 1.2 실측 (로컬 MLX · 2026-09-22)

| # | 대상 | 결과 |
|---|---|---|
| 1 | `_ensure_location_hints`(입력 파서 결정적 보강) | `target_db_hints=['공동존']` — **정상** |
| 2 | `resolve_priority_db_ids(['공동존'])` | `['polestar_cm_gp','polestar_cm_yd']` — **정상**(`'공동존 vm'`·`'공동존의 vm'`·`['공동존','폴스타']`도 같음) |
| 3 | 라우터 LLM 분류 `_llm_classify` — 원문 ×2 | 2회 모두 **`polestar_cm_gp` 1곳** · `user_specified=false` |
| 4 | 〃 — "공동존 VM 중 최대 CPU 사용률 상위 10개 서버 조회" ×2 | 2회 모두 `polestar_cm_gp` |
| 5 | 〃 — "공동존 VM 중 최대 메모리 사용률 상위 10개 서버 조회" ×2 | 2회 모두 **`polestar_cm_yd`** |
| 6 | 〃 — "공동존 서버의 최대 CPU 사용률 상위 10개" ×2 | 2회 모두 `polestar_cm_gp` |
| 7 | **3단 `semantic_router` 노드 전체 실행**(파서 보강된 `parsed_requirements` 전달) | `['polestar_cm_gp']` · `is_multi_db=False` · `db_scope_source=classified` → **단일 DB 경로(김포만)** |
| 8 | 2단 플래너 `_llm_decompose` — 원문 ×2 | 2회 모두 **task 1개**(9B 기준 — 복합 분해 안 함) |
| 9 | `_apply_turn_hint_pinning`(분류 = `[gp]` 가정) · `is_composite=False` | `[gp, yd]` · 고정됨 |
| 10 | 〃 · `is_composite=True` | `[gp]` · **고정 안 됨** |

MLX 결과는 로직 확인용이다(D-240). 단 #7의 결론은 **모델과 무관하다** — 3단에는 두 DB 조회를 보장하는 결정적 장치가 없다.

### 1.3 재현 방법 (부록 A)

부록 A 스크립트를 루트 venv로 실행한다. 로컬 MLX 서버(127.0.0.1:8080)가 떠 있고 두 평면이 `mlx`여야 한다. 공유 서버이므로 동시 실행을 피한다.

---

## 2. 원인

### 2.1 층 A — 조사 대상 선정

**A-1 · 3단 라우터는 LLM 분류만으로 대상 DB를 확정한다(주원인).**
`semantic_router`는 `_llm_classify` 결과(`semantic_router.py:271`)를 관련도 필터·정렬(`:367-371`)만 거쳐 그대로 `target_databases`로 쓴다(`:431-450`). 입력 파서가 계산한 `target_db_hints`를 **읽는 코드가 3단에 없다** — `target_db_hints` 소비처는 `field_mapper`(양식 업로드 전용 · 텍스트 질의는 `:84-90`에서 스킵) · `context_resolver`(승계 신호) · `subagents`(1·2단)뿐이다(grep 실측).
LLM은 레지스트리 별칭에 "공동존"이 gp·yd **양쪽에** 있어도 하나만 고른다(§1.2 #3~#6). 사용자 지정 표시(`user_specified`)도 붙이지 않는다.

**A-2 · 3단 계획 루프도 같은 결과를 이어받는다.**
`TIER3_PLAN_LOOP_ENABLED`(기본 off) 경로의 데이터 task는 라우터의 대상 키(`_ROUTING_KEYS`, `tier3_plan.py:86`)를 그대로 받는다(`:260`). task별 재분류·힌트 고정이 없다는 사실은 `plans/103` §4.1 P1-2 행(282행)에 이미 적혀 있다.

**A-3 · 2단은 복합 분해 시 힌트 고정을 끈다.**
`_apply_turn_hint_pinning`은 `is_composite`면 즉시 반환한다(`subagents.py:434-435`). `is_composite`는 플래너가 task 2개 이상일 때 켜고(`intent_planner.py:419`) 격리 입력으로 전파된다(`subagents.py:843`). 그러면 task마다 `classify_dbs`(LLM)가 DB를 고른다 — 재현상 **CPU task → 김포 · 메모리 task → 여의도**(§1.2 #4·#5)로 갈라진다. 로컬 9B 플래너는 이 질의를 쪼개지 않았지만(§1.2 #8) 운영 모델은 미측정이다.

**A-4 · 1단은 구조상 두 DB를 조회한다 — 다만 설계 의도가 아니라 부수 효과다.**
1단 도구 입력은 `_AMBIENT_KEYS`(`deep_agent.py:141-162`)만 싣는데 `is_composite`가 없어 항상 고정이 걸린다. 이것은 `plans/103` N-1("1단 ambient 결손")의 부수 효과다. 1단을 정답으로 삼지 않는다(103 §1.2 N-1).

**A-5 · 라우터 프롬프트에 존 단위 예시가 없고, 위치가 어긋난 예시가 있다(보조 원인).**
렌더된 위치 예시는 `"김포 알람" → polestar_cm_gp, "여의도 알람" → polestar_cm_yd, "은행 알람" → polestar_b0`(DB당 1개)뿐이다 — "공동존 → 두 DB" 예시는 0건이다(렌더 실측). 또 `"김포 영역의 VM …"` 예시가 은행존 DB `polestar_b0`로 연결돼 있다(`src/prompts/semantic_router.py:164-170`). 이 질의에는 "vm"이 들어 있다.

**A-6 · (부수 관찰 · 정적 읽기) 1·2단 고정은 힌트에 없는 분류 결과를 전부 버린다.**
`_apply_turn_hint_pinning`은 고정 집합 밖의 대상을 떨어뜨린다(`subagents.py:458`). 그래서 존이 없는 DB(`itam`)도 빠진다 — 예: "공동존 서버의 유지보수 계약" 같은 교차 질의. 실행 확인은 하지 않았다. **3단 이식(F-1)에서는 이 동작을 복제하지 않는다**(`plans/95` W-10 `_keep_zoneless_targets`와 같은 방향).

### 2.2 층 B — 결과 종합

**B-1 · 병합은 이어 붙이기다.**
`_merge_results`(`multi_db_executor.py:2662-2703`)는 DB 실행 순서대로 행을 잇고 `_source_db`만 붙인다. `result_merger`는 이 값을 그대로 쓴다(`result_merger.py:45`). DB마다 `LIMIT 10`이면 결과는 "김포 10행 + 여의도 10행"이고 전체 top 10으로 다시 정렬되지 않는다 — **D-202 잔여 ①**이 이것이다.

**B-2 · 응답 LLM은 앞 20행만 보는데, 그 20행이 DB 순서대로 잘린다.**
`_build_response_prompt`는 `rows[:20]`만 싣는다(`output_generator.py:491-495`). 앞 DB의 행이 20행 이상이면 뒤 DB(여의도) 행은 **응답 LLM에게 하나도 보이지 않는다.** DB를 둘 다 조회해도 "한쪽만 조회한 것처럼" 보이는 두 번째 경로다. 이 질의에서 DB당 결과 행 수는 측정하지 못했다(조건부 원인).
수치 요약은 전수 기준이라(`:512`) 최대·최소값은 맞다. 하지만 표와 서술은 한쪽으로 치우칠 수 있다.

**B-3 · "전체 top N" 판단을 응답 LLM이 20행 미리보기 안에서 한다.**
재정렬이 없어서, 결과를 순위로 제시하는 일을 LLM이 대신한다. 이는 비결정 경로다(Known Mistakes — LLM 비결정성).

**B-4 · 이미 있는 것.**
`_append_zone_coverage_notes`(`output_generator.py:756`)가 **실패 존과, 일부 존만 0행인 경우의 존별 건수**를 각주로 단다. 정상 조회된 존들의 요약(존별 건수·대표값)은 없다.

---

## 3. 정상 동작 명세 — 이 계획이 세우는 불변식

사용자 기준(②): *"여러 DB를 분석해야 한다면, 조사할 내용을 각각의 DB에서 조사하고 종합해서 보여준다."*

| # | 불변식 | 판정 |
|---|---|---|
| **I-1 대상** | 원문 위치어가 여러 DB를 포괄하면(레지스트리 `locations`의 `db_ids` ≥ 2) **그 DB 전부**가 조사 대상이다. 사다리 **단과 무관**하다(1·2·3단 · 3단 계획 루프). 좁히는 표현("공동존 김포")은 좁힌다(D-065 기존 규칙 그대로) | 라우터·핸들러 출력의 `target_databases` |
| **I-2 조사** | 각 DB에서 **같은 조사 내용**을 실행한다. 동일 스키마면 검증된 SQL을 공유하고(기존), DB별 실패는 격리해 사유를 표기한다(D-005) | `db_results` 키 = 대상 전부 · 실패는 `db_errors` |
| **I-3 종합** | 질의 형태별로 합친다 — **순위**(top N · 최대/최소 N): 전 DB 결과를 같은 정렬 키로 재정렬한 뒤 N행으로 자른다. 전역 top N ⊆ ∪(DB별 top N)이므로, DB별 SQL에 `LIMIT N`이 있으면 누락이 없다 · **목록**: 합치되 출처 표기 · **집계**(합계·건수·최대·최소): DB별 값 + 전체 값을 코드가 계산 · **평균**: DB별로만 표시(행 수 가중 정보 없이 재집계 불가 — G-5) | `query_results` 행 순서·행 수 · 응답 표 |
| **I-4 출처** | 모든 행에 존/DB 표시명을 남긴다(`_source_db` → 레지스트리 표시명) | 응답 표·CSV 칼럼 |
| **I-5 균형** | 응답 LLM에 넘기는 미리보기가 한 DB로 치우치지 않는다 | 미리보기에 대상 DB가 전부 1행 이상 |
| **I-6 비범위** | DB 간 JOIN(D-005 주의 — 계속 미지원) · 교차 시스템 키 브리지(`plans/102`) · 복합 분해 품질(`plans/103`·`111`) | — |

---

## 4. 기존 계획·결정과의 관계

| 항목 | 소유 | 상태 | 이 계획과의 관계 |
|---|---|---|---|
| 바 "공동존" → gp+yd 결정적 보강 | D-065 · `input_parser._ensure_location_hints` | ✅ 구현 | 입력으로 **재사용**(새 판단 로직 0) |
| 원문 위치 힌트 고정(1·2단) | `subagents._apply_turn_hint_pinning` | ✅(2단은 복합 시 해제) | F-1이 3단에 이식. F-2(2단 해제)는 보류 |
| 3단 위치 힌트 고정 | `plans/103` D-1 · P1-2 | ❌ 미착수 | **F-1이 D-1 부분만 선행** — 완료 시 103 격차표 D-1 행 갱신 |
| 멀티 DB 순차 실행·부분 실패 | D-005 · D-176 · D-206 | ✅ | 전제(I-2) |
| 멀티 DB 결과 병합 | D-005(`_source_db` concat) | ✅(concat) | S-1이 **순위 질의에 한해** 재정렬·재LIMIT를 더한다(D-005 개정 아님 — 확장) |
| 전역 재정렬·재LIMIT | D-202 잔여 ① | **미계획** | **S-1이 인수** |
| 존별 0행·실패 각주 | `_append_zone_coverage_notes` | ✅ | 유지. S-2의 존 표기와 중복되지 않게 배치 |
| 2단 전용 결함 처리 원칙 | `plans/108` G-2(사용자 확정) | 확정 | F-2 보류 근거 |
| 운영 3단 전환 | `plans/102` L-5 | 대기(103 P5 뒤) | **F-1을 L-5 선행 조건에 추가 권고** — 지금 전환하면 공동존 질의가 현행 1단보다 퇴행한다 |

---

## 5. 작업 분해

| WU | 작업 | 선행 | verify |
|---|---|---|---|
| **F-1** | **3단 라우터 위치 힌트 결정적 고정** — 아래 §5.1 | G-1 | §5.1 |
| F-2 | 2단 복합 분해 시 힌트 고정 해제 교정 — **보류**(`plans/108` G-2). 착수 조건: 2단을 계속 쓰기로 할 때. 방향: 전역 해제 대신 *"task 질의에 자기 위치어가 없으면 원문 힌트로 고정"* | G-2 | 2단 복합 task 2개가 모두 `[gp, yd]` |
| F-3 | 3단 계획 루프 — F-1로 자동 해소(라우터 대상 상속). **확인만** 한다 | F-1 | `TIER3_PLAN_LOOP_ENABLED` on에서 복합 task가 `[gp, yd]`를 받음(대역 LLM) |
| F-4 | 라우터 프롬프트 보강 — ① 존 단위 예시 1줄(예: "공동존 서버 목록" → gp·yd 둘 다) ② VM 예시의 `polestar_b0`를 위치가 일치하는 DB로 교정. 대상 결정은 F-1이 보장하므로 이 작업은 **보조**다(`sub_query_context` 품질용) | F-1 | 골든(`tests/test_semantic_routing/goldens/`) · `test_prompt_byte_identity` · `scripts/prompt_render_diff.py` 갱신 · routing gold에 공동존 케이스 추가(`testdata/routing_gold/routing.yaml` — 현재 **0건** 실측) |
| **S-1** | **순위 질의 전역 재정렬·재LIMIT** — §5.2 | G-3·G-4 | §5.2 |
| **S-2** | **응답 미리보기 DB 균형** — 멀티 DB이면서 S-1이 적용되지 않은 턴은 20행 미리보기를 DB별 라운드로빈으로 구성한다(대상 DB마다 ≥1행). 표에 존 표시명 칼럼을 둔다. 단일 DB는 바이트 동일(`output_generator.py:491`) | S-1 | gp 25행 + yd 5행 → 미리보기에 yd 행 존재 · 단일 DB 프롬프트 바이트 동일 |
| S-3 | (선택 · G-5) 집계 종합 — 합계·건수·최대·최소는 DB별 + 전체를 코드가 계산해 「수치 요약」에 존별 줄을 더한다(`_numeric_summary_lines` 확장). 평균은 존별만 | G-5 | 존 2곳 COUNT → 전체 합이 코드 계산값 · 평균 전체값 미생성 |
| V-1 | 검증·문서 — §5.3 | 전건 | §5.3 |

### 5.1 F-1 설계

- **위치**: `semantic_router`의 관련도 필터·정렬(`semantic_router.py:367-371`) 뒤, 소유 검증(`:393-395`) 앞.
  1·2단 순서(분류 → 힌트 고정 → 승계 → 소유 제한, `subagents.py:1143-1177`)와 대칭이다 — 소유 검증은 고정된 집합 위에서 동작한다.
- **재사용**: `resolve_priority_db_ids`(`field_mapper.py:355`) — 폼필·1·2단과 같은 해소 함수다(경로별 사본 금지 · D-053). 입력은 `parsed_requirements.target_db_hints`다.
- **규칙**
  1. 힌트가 활성 DB로 1개 이상 해소되면, **존 그룹에 속한 대상**을 해소 집합으로 **교체**한다. 기존 분류 항목이 있으면 그 `sub_query_context`를 재사용하고, 없으면 위치어를 걷어낸 질의로 채운다(`_strip_location_terms`와 같은 규칙).
  2. **존 그룹이 없는 대상**(`itam` 등)은 분류 결과를 그대로 둔다(A-6 복제 금지).
  3. 고정된 항목에는 `user_specified=True`, 반환에는 `db_scope_source="hint"`를 싣는다 — 존 역질문 후단 게이트(`:407`)는 `user_specified`로 자동 비발동한다.
  4. 힌트가 없거나 해소 0건이면 **종전과 동작·반환이 같다.**
- **D-004 경계**: 라우터 안에서 원문 문자열을 새로 스캔하지 않는다. 입력 파서가 이미 만든 `target_db_hints`(사용자가 명시한 위치어의 결정적 보강 — D-065와 같은 용도)만 읽는다. 의도(intent) 분류에는 쓰지 않는다.
- **`ZONE_GROUP_EXCLUSIVE=true`에서 은행존+공동존 혼합 힌트**: 라우트 pre-gate(`src/api/routes/query.py:1141` 부근)가 먼저 처리한다. 라우터 고정은 그 뒤에 온다 — 이 순서를 테스트로 확인한다.
- **verify**
  - 단위(LLM 대역이 `[gp]`만 반환): "공동존 …" → `[gp, yd]` · `is_multi_db=True` · `db_scope_source="hint"` / "공동존 김포 …" → `[gp]` / "은행존과 공동존 …"(개방) → `[b0, gp, yd]` / 힌트 없음 → 종전과 동일 / `itam` 분류 결과 보존 / `selected_db_ids`·`mapped_db_ids` 조기 반환 경로 무영향.
  - `tests/test_orchestration/test_turn_hint_pinning.py` 케이스를 3단 라우터로 재실행한다(`plans/103` P1-2 verify 목록과 같다).
  - 로컬 MLX: 부록 A의 3단 노드 실행이 `['polestar_cm_gp','polestar_cm_yd']`를 낸다.

### 5.2 S-1 설계

- **위치**: `result_merger`. 모든 경로의 멀티 DB 합류점이다 — 3단 그래프(`multi_db_executor → result_merger`), 1·2단 핸들러(`subagents.py:1287-1288`), 3단 계획 루프 `task_run` 서브그래프. **한 지점 수정 = 경로 대칭**(D-066).
- **정렬 키와 N — LLM 판단 없이(D-035·D-068) 실행한 SQL에서 읽는다**
  - 각 DB가 실제로 실행한 SQL의 최외곽 `ORDER BY` 첫 키(결과 칼럼 alias)와 `LIMIT n`·`FETCH FIRST n ROWS`를 `sqlparse`(기존 의존 — `src/sql_validation.py:25`)로 읽는다.
  - **적용 조건**: 모든 대상 DB의 키·방향·N이 같아야 한다. 동일 스키마 SQL 공유면 자명하다. DB2(b0)는 결과 칼럼을 소문자로 반환하므로 `_merge_results` 정규화 키(소문자·공백/언더스코어 제거)로 대조한다.
  - **불일치·파싱 실패**: 재정렬하지 않고 종전 concat을 유지하되, 사유를 로그와 `dependency_notes`로 남긴다(침묵 강등 금지).
- **정렬**: 같은 키로 재정렬(NULL은 뒤로 — `ensure_ranking_nulls_last`와 같은 의미)하고 동률은 레지스트리 DB 선언 순으로 둔 뒤 N행으로 자른다. `_source_db`는 유지한다.
- **적용 제외**: 한 SQL이 여러 순위 목록을 내는 형태(UNION ALL + 구분 칼럼 등) — G-3 해석에 따른다.
- **원본 보존**: DB별 전체 행(N × DB 수)을 CSV 등에 남길지 — G-4.
- **verify**: gp top10 + yd top10(yd 값이 큰 행 포함) → 전역 top10에 yd 행 포함 / 키 불일치 → 미적용 + 노트 / 단일 DB → no-op·반환 동일 / b0 소문자 alias 대조 / 결정적 조립 SQL(알람 서버별 집계 등)에도 `LIMIT`이 있는지 형태별 확인.

### 5.3 V-1 검증·문서

- 단위 테스트(F-1·S-1·S-2) · routing gold 공동존 케이스 · `RUN_LOCAL_LLM=1` 로컬 MLX(D-240 — 두 평면 `mlx` 확인 뒤).
- 품질 게이트: `scripts/arch_check.py --ci` · `scripts/overfit_check.py --ci` — `result_merger`·`output_generator`는 스캔 대상이다. 존·DB 표시명은 **레지스트리에서** 읽고 폴스타 리터럴을 새로 넣지 않는다 · `ruff`·`mypy`(오프라인 uvx).
- **내부망 재현 체크리스트**(사용자 몫):
  - 기동 로그 `오케스트레이션 사다리 확정: tier=…`
  - `시멘틱 라우팅 완료: targets=['polestar_cm_gp', 'polestar_cm_yd'], multi_db=True`(3단)
  - `결과 병합 완료: 2개 DB에서 총 N건`
  - 응답 표에 김포·여의도 행이 모두 있고, 순위가 전체 기준이다.
- 문서: D-번호 등재(§7) · `plans/103` 격차표 D-1 행 갱신 · `docs/21`(3단 대상 선정 규칙 한 줄) · `plans/INDEX.md`.

---

## 6. 순서 불변식

- **F-1 → (F-3 확인) → S-1 → S-2 → (S-3)**. F-4는 F-1 뒤에 한 번에 한다(프롬프트 골든 변경을 한 차수로 묶는다).
- **F-1을 `plans/102` L-5(운영 3단 전환)의 선행 조건으로 둔다.** 운영이 1단인 지금은 1단 부수 효과(A-4)로 두 DB가 조회되지만, 3단으로 전환하면 곧바로 김포만 조회된다.
- **병행 세션**: `semantic_router.py`·`tier3_plan.py`·`subagents.py`는 오늘 커밋 `4f3a252`(plans/103·111)에서 수정됐다. 착수 전 `git status`·`ListAgents`로 확인한다.
- **실 LLM**: 로컬 MLX만 쓴다(D-240). 공유 8080 서버는 동시 사용 시 Metal OOM 위험이 있어 호출을 순차로 소량만 한다.

---

## 7. 게이트 (사용자 확정 필요)

| # | 물음 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** | F-1을 플래그 뒤에 두는가 | (가) 플래그 없이 적용 · (나) 신규 플래그 기본 off | **(가)** — 1·2단은 이미 무조건 적용하고 3단만 없는 **비대칭 결함 수정**이다. D-065 보강도 무조건이다. 기본 off 원칙(`plans/80` §5.4-③)을 따르면 결함이 그대로 남는다 — 예외 근거를 config 주석 대신 이 계획과 D-번호에 남긴다 |
| **G-2** | F-2(2단 복합 해제)를 같이 고치는가 | (가) 보류 · (나) 함께 | **(가)** — `plans/108` G-2와 같은 잣대(2단 전용 결함 보류 · 기준은 3단) |
| **G-3** | "최대 cpu**와** 최대 memory 서버 top 10"을 어떻게 해석하는가 | (가) 지표별 top 10 두 목록 · (나) 한 목록(정렬 기준 하나 + 다른 지표 병기) · (다) 되묻기 | 사용자 판단. 지금은 SQL 생성 LLM이 임의로 정한다. S-1은 단일 정렬 키만 다루므로 (가)는 복합 분해(`plans/103`·`111`) 쪽 과제가 된다 |
| **G-4** | 종합 결과를 어떻게 보여주는가 | (가) 전역 top N 한 표 + 존 칼럼 + 존별 건수 1줄 · (나) 존별 top N 섹션 + 전역 top N · (다) 존별만 / CSV: 원본 전체 유지 vs 종합 결과만 | **(가)** + CSV는 원본 전체 유지 — 사용자가 말한 "종합"에 가장 가깝고, 원본은 검증용으로 남긴다 |
| **G-5** | S-3(집계 종합)을 이번 범위에 넣는가 · 평균은 어떻게 하는가 | (가) 넣음 · 평균은 존별만 · (나) 뺌 | 사용자 판단 — 순위 질의(S-1)보다 발생 빈도는 미측정 |

**D-번호**: 게이트 확정 시 채번 규칙(`## D-` 헤더 · 「변경 이력」 · 「채번 이력」 최댓값 +1)대로 부여한다. `docs/02_decision.md`는 작성 시점에 타 세션이 미커밋 수정 중이라 예약하지 않았다.

### 7.1 확정 기록 (2026-09-22 · 사용자 지시 *"권고대로 구현을 진행하라."*)

| # | 처분 | 근거 |
|---|---|---|
| **G-1** | **(가) 플래그 없이 적용** | 권고 채택. 1·2단은 이미 무조건 고정하고 3단만 없는 **비대칭 결함 수정**이다. 신규 플래그 기본 off 원칙(`plans/80` §5.4-③)의 예외 근거는 config 주석이 아니라 D-246 본문에 남긴다 |
| **G-2** | **(가) F-2 보류** | 권고 채택 — `plans/108` G-2와 같은 잣대(2단 전용 결함 보류 · 기준은 3단) |
| **G-3** | **S-1은 단일 정렬 키 결과만 재정렬 · 한 SQL이 여러 순위 목록을 내는 형태(UNION 등)는 적용 제외(현행 유지)** | 계획서에 권고가 없었다 → **범위를 넓히지 않는 보수적 기본 처분**(사용자 재확인 가능). "지표별 top 10 두 목록"(가)은 복합 분해(`plans/103`·`111`) 과제로 남는다 |
| **G-4** | **(가) 전역 top N 한 표 + 존(출처) 칼럼 + 존별 건수 1줄 · CSV는 원본(DB별 전체 행) 유지** | 권고 채택 |
| **G-5** | **S-3(집계 종합)은 이번 범위에서 제외** | 계획서에 권고가 없었다 → **보수적 기본 처분**(사용자 재확인 가능). S-3은 잔여로 남는다 |

**게이트 변경 (2026-09-22 v3 · 사용자 지시 *"2단도 같이 고려하여 구현을 진행하고 범위 밖에 남은 작업들도 구현을 진행하라."*)**

| # | 변경 | 근거 |
|---|---|---|
| **G-2** | **(가) 보류 → (나) F-2 함께 구현** | **사용자가 직접 정한 예외**다 — `plans/108` G-2("2단 전용 결함 보류 · 기준은 3단")는 **개정하지 않으며** 이 항목에 한정한다. 결정적 고정 규칙은 3단 라우터와 같은 함수를 쓰므로(D-053·D-066) 2단만의 별도 경로가 생기지 않는다 |
| **G-5** | **제외 → (가) S-3 포함 · 평균은 존별로만 표시**(전체 평균 생성 금지) | 사용자 결정. 평균은 DB별 행 수 가중 정보가 없어 코드가 재집계할 수 없다(I-3) |
| G-1·G-3·G-4 | 종전 처분 유지 | — |

---

## 8. 측정하지 못한 것 · 가정

- **내부망 실행 경로(사다리 몇 단인지) 미확인** — 로그가 없다. 확인 방법은 §5.3 체크리스트.
  운영 `.env` 기록(2026-08-31)은 1·2·3단 플래그가 모두 true라 1단으로 확정되지만, 오케스트레이터가 미가용이면 2단으로 내려간다.
- **운영 LLM(FabriX·Gemini)의 라우터·플래너 출력 미측정** — 로컬 9B만 측정했다. 3단 결론(A-1)은 모델과 무관하고, 2단 분해 여부(A-3)는 모델에 따라 다르다.
- **이번 질의의 실제 SQL·DB당 결과 행 수 미측정** — B-2(미리보기 절단)는 조건부 원인이다.
- **A-6**(1·2단 고정이 `itam`을 떨어뜨림)은 정적 읽기이고 실행하지 않았다.
- **S-1의 정당성은 DB별 SQL에 `LIMIT N`이 실제로 있을 때만 성립한다.** LLM 생성·결정적 조립 SQL 형태별로 확인이 필요하다(§5.2 verify).

---

## 9. 구현 현황 (v2·v3 · 2026-09-22 · D-246)

v2 사용자 지시 *"권고대로 구현을 진행하라."* · v3 사용자 지시 *"2단도 같이 고려하여 구현을 진행하고 범위 밖에 남은 작업들도 구현을 진행하라."* 게이트 처분·변경은 §7.1. 커밋하지 않았다(작업 트리에 둠).

| WU | 상태 | 소재 | 비고 |
|---|---|---|---|
| **F-1** | ✅ | `src/routing/semantic_router.py` `_pin_turn_location_hints`(호출은 관련도 정렬 직후 · 소유 검증·존 역질문 게이트 앞) · 해소 함수 `src/routing/location_hints.py` `resolve_priority_db_ids`·`strip_location_terms` | 규칙 §5.1 기준. **이탈 3건**(§9.2): ① 해소 함수를 `field_mapper.py:355`에서 그대로 import하면 infrastructure→application 계층 위반이라 본문을 `src/routing/location_hints.py`로 **옮기고** `field_mapper`·`subagents`는 같은 이름으로 재노출(동작 불변 · 사본 0) ② 분류 결과에서 재사용한 항목에는 `user_specified=True`를 **달지 않는다**(채운 항목만) — 고정 사실은 반환 bool로 알려 존 역질문 게이트를 끄고 `db_scope_source="hint"`를 싣는다 ③ `ZONE_GROUP_EXCLUSIVE=true`에서 해소가 두 존 그룹에 걸치면(제품명 단독 "폴스타" 등) 고정하지 않는다. 존 없는 DB만 지목한 힌트(예: ITAM)는 그 DB를 더하고 존 그룹 대상은 좁히지 않는다 |
| **F-2** (v3) | ✅ | 공용 판정 `src/routing/location_hints.py` `pin_targets_to_hints`·`task_hint_scope` · 1·2단 `subagents._apply_turn_hint_pinning`(위임) · 3단 `semantic_router._pin_turn_location_hints`(위임) | **1·2단과 3단이 같은 함수·같은 규칙**(D-053·D-066): 분류 항목 재사용(표지 그대로) · 채운 항목만 직접 지정 표지 · **존 없는 DB 분류 결과 보존**(A-6 — 1·2단의 전량 탈락 교정) · 상호배타 2그룹 해소 미고정(호출부가 설정 전달). **복합 계획은 task 단위 고정**: task 질의에 원문 힌트(지역 토큰 포함)가 그대로 있으면 그 힌트들 → 없으면 원문 힌트 속 레지스트리 위치 표면어 중 task 질의에 있는 것 → 둘 다 없으면 원문 힌트 전체. 결과는 원문 전체 해소 집합 **안에서만** 좁힌다(넓히지 않음) · 원문에 없는 위치어는 task 질의에 있어도 무시(2026-07-16 방어 유지). **단일 task는 좁히지 않는다**(§9.2 #6). 3단 계획 루프도 같은 규칙: `tier3_plan._narrow_routed_targets_to_task`가 라우터 고정(`db_scope_source="hint"`) 집합 안에서 복합 task를 좁힌다(§9.2 #7) |
| **F-3** | ✅ 확인 | `tier3_plan.task_payload`(`_ROUTING_KEYS` 상속) | v2: 라우터가 `[gp, yd]`로 고정하면 복합 task 2개가 모두 `[gp, yd]`·`multi_db_executor` 진입. v3: 위치별 task는 자기 위치로 좁혀진다(F-2) |
| F-4 | **기각(보류)** | — (코드 원복 · 프롬프트·골든 HEAD 바이트) | 존 단위 위치어 규칙·few-shot 예시(레지스트리 렌더) + "김포 영역 VM" 예시의 서버 DB 교정(은행존 → 김포)을 구현해 로컬 MLX로 재측정 → **목표 질의(§1.2 #3~#6) 효과 0**(8/8 여전히 한 DB) · **골드 r-073(교차 시스템) 결정적 회귀**(itam 탈락 — HEAD 프롬프트 통제 실행으로 확인 · **VM 예시 교정 단독으로도 발생**). 기본 off 플래그로 남기는 안은 효과 없는 노브를 늘리므로(plans/109 노브 축소 중) 착지하지 않았다(코디네이터 판단). 대상 선정은 F-1·F-2 결정적 고정이 보장한다. 측정 원자료·구현 요지·재측정 조건: 부록 C. routing gold `r-013`(공동존)은 `_llm_classify` 단독 채점이라 **실 LLM에서 실패가 계속 예상된다**(골드 note에 명시) |
| **S-1** | ✅ | `src/nodes/result_merger.py` `plan_global_ranking`·`parse_rank_spec` · 실행 SQL 기록 `multi_db_executor._MultiRun.db_sqls` → state `db_executed_sqls` · 응답 행 교체 `result_organizer._current_merge_ranking` | 정렬 키·방향·N은 실행 SQL의 최외곽 ORDER BY 첫 키와 `LIMIT`/`FETCH FIRST`(sqlparse · 괄호 안 무시 · DB2 `WITH UR` 허용). 적용: 행 있는 DB ≥2 + 전 DB 같은 기준(정규형 키 대조) + 키 값이 수치·날짜(ISO 문자열 포함). 미적용 사유 7종(기준 불일치 · 판독 불가 · UNION 등 · OFFSET · 키가 결과에 없음/다른 별칭과 충돌 · 키 값 비수치 · **NULL 선두 절단** — NULL을 앞에 두는 정렬로 N행에서 잘린 DB에 NULL 키가 있으면 값 있는 상위 행이 빠졌을 수 있다)은 로그 + 존별 건수 줄. **원본 `query_results`는 불변**(CSV·row_count·다음 턴 맥락) — 재정렬 행은 `merged_ranking` → `organized_data.rows`, 같은 턴 선행 결과 전달은 `_pack_pipeline_result`가 `rows`에 재정렬 행을 실어 전체 상위 N을 쓴다. 대상 외(노트 없음): 양식 턴 · 기본 안전 상한(`QUERY_DEFAULT_LIMIT` 이상) · 순위 형태 아님 · **DB·존별 순위 요청 표현**(각각 · 존별 · DB마다 · N개씩 — 표면어 결정적 가드). 결정적 조립 SQL 형태 확인: 알람 서버별 집계 `ORDER BY alarm_count DESC NULLS LAST LIMIT n` · 알람 목록 `ORDER BY a.ctime DESC LIMIT n`(→ select 별칭 `alarm_time` · ISO 시각) · 폼필 피벗 `ORDER BY "<alias>" … NULLS LAST` + 행 상한 — 모두 판독 가능 |
| **S-2** | ✅ | `src/nodes/output_generator.py` `_preview_rows`·`_display_row`·`_ranking_clause`·`_rows_are_merged`·`_append_zone_coverage_notes` · `src/state.py` `create_followup_input` | 재정렬 미적용 멀티 DB 턴은 20행 미리보기 DB 라운드로빈 · `_source_db` → 「출처」(레지스트리 표시명) · `[존별 결과]` 줄을 멀티 DB 정상 턴 전체로(표시명 · 재정렬 시 상위 N 분포 · 미적용 사유). 잔존 방어: 후속 턴 델타가 `db_result_summary`·`db_executed_sqls`·`merged_ranking`을 비우고(종전엔 단일 DB 후속 턴에 직전 멀티 DB 요약이 남았다), 이번 행에 출처 태그가 없으면 줄을 싣지 않는다(같은 요청 안 단일 경로 재시도). 단일 DB 프롬프트·기존 각주는 HEAD와 바이트 동일(해시 대조 8건) |
| **S-3** (v3) | ✅ | 판정 `src/nodes/result_merger.py` `plan_aggregate_synthesis`·`parse_aggregate_items`(→ state `merged_aggregates`) · 운반 `result_organizer._current_merge_aggregates`(→ `organized_data.merge_aggregates`) · 표시 `output_generator._aggregate_summary_lines`·`_aggregate_skip_clause` | 적용: 행 있는 DB ≥2 · **DB마다 정확히 1행** · 각 DB 실행 SQL이 최외곽 GROUP BY 없는 집계 질의(sqlparse — LLM 0) · 항목 수·집계 종류·칼럼(정규형)이 전 DB에서 같음. 전체 값: 건수·합계는 더함 · 최대·최소는 그중 최대·최소 — **평균·중복 제거 건수·집계를 감싼 식은 DB별 값만**(전체 값 없음). 응답 「수치 요약」을 DB별·전체 코드 계산값으로 바꾸고(DB별 값끼리의 최소·최대·평균 같은 무의미한 통계는 싣지 않음) 결정적 `**[존별 집계]**` 줄을 붙인다. 미적용 사유(판독 불가 · DB별 형태 불일치)는 존별 결과 줄에. S-1과 **배타**(집계가 적용되면 재정렬하지 않음) · 양식 턴 제외 · 원본 `query_results` 불변(G-4) · 단일 DB 바이트 동일 |
| **V-1** | ✅ | 본 절 · §7.1 · `plans/103` D-1 행·P1-2 부기 · `docs/21` §1 · D-246(+ v3 부기) · D-202 잔여 ① 부기 · `docs/18` 2행 · routing gold `r-013` | — |

**검증(2026-09-22)**

- 신규 단위: `tests/test_semantic_routing/test_router_hint_pinning.py`(24건 — **HEAD에 얹으면 15건 실패** = 결함 재현, 불변 동작 9건은 HEAD에서도 통과) · `tests/test_nodes/test_merge_ranking.py`(42건 — S-1·S-2·리뷰 반영 가드).
- 독립 코드 리뷰(서브에이전트 · 읽기 전용): 지적 8건 중 6건 반영(후속 턴 요약 잔존 · 같은 턴 선행 결과 불일치 · 존별 요청 · NULL 선두 절단 · 재사용 항목 표지와 소유 교정 · 상호배타 2그룹 해소) + 줄바꿈된 `ORDER\nBY` 판독 · 2건은 잔여(둘째 정렬 키 동률은 §5.2의 레지스트리 순 규칙대로 · 다음 턴 맥락). 기존 테스트 수정 2건: `test_output_generator.py::TestZoneCoverageNotes::test_all_zones_have_rows_*`(G-4로 의도 변경) · `test_registry_config.py` 허용 사본 파일 경로(`field_mapper` → `location_hints`).
- 품질 게이트: `arch_check --ci` error 0(경고 85 — HEAD 동일) · `overfit_check --ci` 신규 유입 0 · ruff 신규 E/F 0(UP045 4건은 기존 `Optional` 표기 준수) · mypy 신규 0(274→273).
- 로컬 MLX(`scripts.bench --show-env` = 워커·오케스트레이터 `mlx` · 공유 8080 서버 순차 호출): 부록 A 재실행 → **`3단: ['polestar_cm_gp', 'polestar_cm_yd'] True hint`**(라우터 LLM 분류 자체는 여전히 `polestar_cm_gp` 0.95 1곳) · 입력 파서는 "공동존 김포 …"를 `['공동존 김포']` 한 힌트로 뽑아 `[gp]`로 좁혔다(1회).
- 전체 pytest: §9.1.

**검증 추가(v3 · 2026-09-22)**

- F-2: `tests/test_orchestration/test_turn_hint_pinning.py`(11 → 21건) — 기존 10건 무회귀(단, 종전 `test_composite_plan_skips_pinning`은 A-3 결함 자체를 단언해 **새 규칙으로 교체**: 원문 힌트 ["은행존"] + task 질의 "은행존 알람과 김포 서버 현황" → [b0], 김포는 원문에 없어 무시) + 신규 10건(위치 없는 두 task → 둘 다 [gp, yd] · 위치별 task → [gp]/[yd] · 한 덩어리 원문 힌트 "김포와 여의도"도 task 지역어로 가름 · 원문보다 넓히지 않음 · 원문 안 존을 LLM이 임의로 쪼갠 task는 원문 전체 · 단일 task는 좁히지 않음 · itam 보존 · 상호배타 2그룹 미고정 · 3단과 같은 함수). `agent_orchestrator` 통합 2건(`tests/test_orchestration/test_composite_hint_pinning_flow.py` — A-3 재현(분류가 CPU → 김포 · 메모리 → 여의도) → 두 task 모두 [gp, yd] · 위치별 → [gp]/[yd]). 3단 계획 루프 2건(`test_router_hint_pinning.py::TestTier3PlanLoopTaskScope` — 파일 24 → 26건). HEAD에 얹으면 F-2 관련 11건이 실패한다(결함 재현). 교차 시스템 1건 갱신(`test_ownership_task.py::test_x_t13_location_hint_zone_is_kept` — itam을 이제 고정이 아니라 소유 제한이 빼고 **노트를 남긴다**).
- S-3: `tests/test_nodes/test_merge_ranking.py::TestAggregateSynthesis` 10건(파일 42 → 52건 · gp COUNT 12 + yd 8 → 전체 20 · SUM 160 · MAX/MIN · AVG는 DB별만·전체 없음 · COUNT DISTINCT·감싼 식은 전체 없음 · 형태 불일치·판독 불가 → 미적용+사유 · 단일 DB no-op · GROUP BY 결과 제외 · 양식 턴 제외 · 프롬프트·존별 집계 줄 · 단일 DB 바이트 동일).
- 결과 종합 경로 대칭(1·2단): `tests/test_orchestration/test_merge_synthesis_aggregator_path.py` 3건 — 2단 `run_data_query_pipeline`(실물 `result_merger`·`result_organizer`) → `result_aggregator` → `output_generator`(실물)까지 돌려 **응답 LLM 프롬프트에 S-1 재정렬 행·요약·「출처」, 최종 응답에 존별 결과 줄**, S-3 「수치 요약」·`[존별 집계]` 줄이 실리는 것을 확인 · 1단 도구 결과 요약(`deepagents_tools._serialize_for_tool` 50행 상한)도 DB 순 절단 대신 같은 균형 규칙.

**측정하지 못한 것 · 남은 것**

- 내부망(FabriX·운영 DB) 재현 — §5.3 체크리스트. 실제 SQL에 행 상한이 붙는지·DB당 행 수는 여전히 미측정.
- **힌트가 따로 떨어진 "공동존 김포"**: 파서 LLM이 `["김포"]`만 내거나 비우면 결정적 보강이 `"공동존"`을 따로 더해 `['김포','공동존']` → `[gp, yd]`(좁혀지지 않음). 1·2단 고정도 같은 해소 함수라 **종전부터 같다** — 과포함(원하지 않은 존 추가)이지 누락은 아니다. 로컬 9B 1회는 `['공동존 김포']`로 정상이었다. 고치려면 D-065 해소 규칙 자체를 바꿔야 해(폼필·1·2단 공통) 이번 범위 밖이다.
- **DB·존별 순위 요청 가드는 표면어 판정**이다(각각 · 각 존/DB · 존별/DB별/센터별/지역별/위치별 · 존·DB마다 · N개씩). 오탐 방향은 종전 이어 붙이기라 하방 안전하지만, 표현 목록 밖의 존별 요청("김포 따로 여의도 따로")은 전역 한 표로 잘린다(CSV·존별 건수 줄은 남는다).
- **다음 턴 맥락("그 서버들")**은 원본 병합(`query_results`) 기준이라 DB별 top N 합집합을 가리킨다(종전과 같음 — 과포함이지 누락은 아니다). 같은 턴 선행 결과 전달은 재정렬 행으로 맞췄다.
- 동률의 N번째 경계는 계획 §5.2대로 레지스트리 DB 선언 순으로 가른다 — 둘째 정렬 키는 보지 않는다.
- 후속 턴 델타가 `db_errors`는 여전히 비우지 않는다(종전부터 — 「일부 존 조회 실패」 각주 잔존 가능 · 이번 범위 밖).
- **F-4 재측정**(보류 — 부록 C의 재시도 조건) · `plans/103` P1-2의 나머지(D-2 승계 · D-3 지시어 · D-4 실시간 사용률 · D-6 태스크 존 게이트 — 113 소관 아님).
- S-3은 **스칼라 집계만**(DB마다 1행 · GROUP BY 없음)이다. 그룹별 집계("OS별 서버 수")의 DB 간 그룹 대조·합산은 하지 않는다 — 그룹 키 정규화·누락 그룹 처리 규칙이 따로 필요하다(§9.2 #9).
- 1단 합성(`_synthesize_finalized` — LLM이 task별 응답 텍스트를 합친다)은 task 응답에 붙은 결정적 각주(존별 결과·존별 집계)를 LLM이 옮겨 적지 않으면 잃을 수 있다 — 종전부터의 1단 합성 성질이며 이번에 바꾸지 않았다.

### 9.1 전체 pytest (2026-09-22 · `-p no:cacheprovider --tb=line`)

| 대상 | 결과 | 귀속 |
|---|---|---|
| 클린 HEAD `cddac70`(`git worktree add … HEAD` — 작업 트리 `git stash` 미사용) | **8635 passed · 31 skipped · 0 failed** | 기준선 |
| 클린 HEAD + **이 작업 파일만** 얹은 사본(최종본) | **8701 passed · 31 skipped · 0 failed** | +66 = 신규 24(라우터) + 42(병합) |
| 공유 작업 트리(타 세션 미커밋 포함 · 최종본) | **8889 passed · 31 skipped · 0 failed** | 타 세션 신규 테스트 포함 |
| **v3** 클린 HEAD + 이 작업 파일만(최종본 · 새 worktree) | **8728 passed · 31 skipped · 0 failed** | 기준선 8635 대비 +93 = 라우터 26 + 병합·집계 52 + 힌트 고정 +10 + 2단 통합 2 + 종합 경로 3 |
| **v3** 공유 작업 트리(타 세션 미커밋 포함) | **8995 passed · 31 skipped · 0 failed** | F-4 원복 뒤 실행 |

중간 실행에서 본 실패는 전부 이 작업 결함이 아니었다 — ① 작업 트리 1차: `test_noise_dashboard_api` 5건(`STAGE_DESCRIPTIONS` ImportError — 타 세션 `plans/112` 미커밋 편집 도중 · 최종 실행에선 해소) + `test_eval_routing_harness` 3건(실행 **도중** 골드 `r-013`을 추가해, 수집 시점에 이미 import된 목업 대본과 어긋남 — 단독 재실행 36 passed) ② 얹은 사본 2차: `test_two_stage::test_stage2_is_llm_not_deterministic_rules` 1건(실행 도중 `semantic_router.py`를 덮어써 `inspect.getsource`가 어긋난 줄을 읽음 — 단독 재실행 통과). 최종 두 실행은 편집 없이 돌렸다.

### 9.2 계획 설계에서 이탈한 점 (근거 · 되돌리는 비용)

| # | 이탈 | 근거 | 되돌리는 비용 |
|---|---|---|---|
| 1 | 해소 함수 재사용 위치 — `field_mapper.py:355` import 대신 **본문을 `src/routing/location_hints.py`로 이동**(+ `strip_location_terms`) | 라우터(infrastructure) → nodes(application) import는 `arch_check` error(함수 지역 import도 `ast.walk`가 잡는다). 이동 + 재노출이면 사본 0 | 되돌리면 계층 위반 — 사실상 불가. 소비처 import 경로는 재노출로 그대로라 비용은 파일 1개 |
| 2 | §5.1 규칙 3 "고정 항목 `user_specified=True`" → **채운 항목만** 표지 · 재사용 항목은 분류 그대로 · 고정 여부는 반환 bool | 코드 리뷰 지적 — 소유 교정(`enforce_target_ownership`)이 직접 지정 항목을 건너뛰어, 재사용 항목에 표지를 달면 소유 플래그 on에서 3단만 교정이 꺼진다(1·2단은 재사용 항목 표지를 바꾸지 않는다 · D-066 대칭). 존 역질문 비발동·`db_scope_source`는 bool로 같은 효과 | 표지 한 줄 복원 — 소유 플래그 on에서 3단·1·2단 비대칭이 돌아온다 |
| 3 | `ZONE_GROUP_EXCLUSIVE=true`에서 두 존 그룹에 걸친 해소는 **고정하지 않음** | 원문 혼합 지목은 라우트 pre-gate가 먼저 끝내므로 여기 오는 것은 제품명 단독·부분 별칭 힌트뿐 — 결정적으로 펼치면 D-143 후속3 위반(1·2단은 같은 경우 펼친다 — 종전 결함, 3단은 새로 만들지 않았다). 개방(운영 `.env` · D-206)에서는 1·2단과 같다 | 조건 1개 삭제 |
| 4 | S-1 원본 보존 방식 — `query_results` 교체 대신 **별도 키 `merged_ranking`** + 결과 정리가 응답 행으로 사용 · 같은 턴 선행 결과는 `rows`로 재정렬 행 | CSV(G-4 원본)·row_count가 `query_results`를 읽고, 다른 방식(교체 + CSV 원천 키 신설)은 라우트 5곳·집계기 경로를 모두 고쳐야 했다. 선행 결과 판독처 3곳은 `rows`를 1순위로 읽어 한 곳 수정으로 맞는다 | 키 교체형으로 바꾸려면 라우트 `_store_result` 5곳 + 집계기 CSV 누적 경로 수정 |
| 5 | 적용 조건 추가 — 기본 안전 상한 이상 제외 · DB·존별 요청 표현 제외 · NULL 선두 절단 제외 · 문자열 키 제외 | 계획 §5.2의 "모든 DB가 같은 키·방향·N"만으로는 "전체 기준 상위 N" 보장이 깨지는 형태가 실측·리뷰로 나왔다(안전 상한 1000은 순위 요청이 아니다 · DB collation ≠ 파이썬 순서 · PG/DB2 DESC 기본 NULLS FIRST) — 전부 **적용하지 않는 쪽**(종전 동작)으로만 좁혔다 | 조건별 삭제 1~3줄 |
| 6 | (v3 F-2) **단일 task는 task 질의로 좁히지 않는다**(복합 계획만 task 단위) — 코디네이터 권장 규칙은 `is_composite`와 무관한 적용이었다 | 단일 task에서 재작성 질의가 원문 위치어 하나를 빠뜨리면(예: 원문 "김포와 여의도 CPU 비교" → task "김포 CPU 비교") 한 존이 **조용히 빠진다** — 이 계획이 고치는 결함("한쪽만 조회")과 같은 부류다. 과포함(좁혀야 할 때 안 좁힘)보다 누락이 나쁘다. 그래서 **1단(도구 호출마다 단일 task · `is_composite` 없음)은 종전처럼 원문 힌트 전체로 고정**되고 "위치별로 쪼갠 도구 호출"의 과포함은 해소되지 않는다 | `_apply_turn_hint_pinning`의 `task_query=` 조건 1개(`if isolated.get("is_composite")` 제거) |
| 7 | (v3 F-2) 3단 계획 루프(`tier3_plan.task_payload`)에도 같은 task 단위 좁힘을 넣었다 — 계획서 범위는 1·2단(F-2)과 상속 확인(F-3)이었다 | 같은 규칙을 2단에만 두면 3단 계획 루프의 "김포 CPU와 여의도 메모리" 두 task가 모두 두 존을 조회한다(D-066 비대칭). 라우터가 힌트로 고정한 집합(`db_scope_source="hint"`) 안에서만 좁히므로 새 대상을 만들지 않는다. `plans/103` P1-2의 task별 **재분류**는 여전히 103 소관이다 | `_narrow_routed_targets_to_task` 호출 1줄 |
| 8 | (v3 F-4) **착지하지 않음**(측정 후 기각 · 보류) | 부록 C — 효과 0 · r-073 결정적 회귀. 기본 off 플래그 안은 노브만 늘린다(코디네이터 판단) | 부록 C의 요지를 다시 적용 |
| 9 | (v3 S-3) 스칼라 집계(DB마다 1행 · GROUP BY 없음)만 종합 · 집계를 감싼 식(`ROUND(AVG(x),2)`)·중복 제거 건수는 전체 값 없음 | 그룹 결과의 "전체"는 그룹 키 대조 없이는 의미가 없고, 감싼 식·DISTINCT 건수의 전체 값은 코드가 보장할 수 없다 — 틀린 전체 값을 내는 것보다 DB별만 싣는 쪽 | 조건별 1~2줄 |

---

## 부록 A. 재현 스크립트 (로컬 MLX · DB 연결 없음)

```python
# 루트 venv로 실행: .venv/bin/python repro_113.py  (저장소 루트에서)
import asyncio, os, importlib
os.environ["ACTIVE_DB_IDS"] = "polestar_b0,polestar_cm_gp,polestar_cm_yd"
from src.config import load_config
from src.llm import create_llm
from src.nodes.input_parser import _ensure_location_hints
sr = importlib.import_module("src.routing.semantic_router")
sa = importlib.import_module("src.orchestration.subagents")

Q = "공동존의 vm 중 최대 cpu와 최대 memory 서버 top 10을 정리해줘"

async def main():
    cfg = load_config()
    parsed = _ensure_location_hints({"original_query": Q, "query_targets": ["cpu", "memory"], "target_db_hints": []}, Q)
    out = await sr.semantic_router({"user_query": Q, "parsed_requirements": parsed, "conversation_context": None},
                                   llm=create_llm(cfg), app_config=cfg)
    print("3단:", [t["db_id"] for t in out["target_databases"]], out["is_multi_db"])
    cls = [{"db_id": "polestar_cm_gp", "relevance_score": 0.95, "sub_query_context": "q", "user_specified": False}]
    for comp in (False, True):
        tg, pinned = sa._apply_turn_hint_pinning(list(cls), {"parsed_requirements": parsed, "is_composite": comp},
                                                 Q, cfg.multi_db.get_active_db_ids())
        print(f"1·2단 고정 is_composite={comp}:", [t["db_id"] for t in tg], pinned)

asyncio.run(main())
```

2026-09-22 실측 출력:

```
3단: ['polestar_cm_gp'] False
1·2단 고정 is_composite=False: ['polestar_cm_gp', 'polestar_cm_yd'] True
1·2단 고정 is_composite=True: ['polestar_cm_gp'] False
```

## 부록 B. 참조 (`file:line` · 2026-09-22 작업 트리)

| 주제 | 위치 |
|---|---|
| 입력 파서 위치어 보강 | `src/nodes/input_parser.py:46-70` · 호출 `:134` |
| 위치어 → DB 해소(단일 출처) | `src/nodes/field_mapper.py:288-350` · 공개 별칭 `:355` |
| 3단 라우터 LLM 분류·확정 | `src/routing/semantic_router.py:271` · `:367-371` · 소유 `:393-395` · 존 게이트 `:407` · 반환 `:431-456` |
| 1·2단 힌트 고정 | `src/orchestration/subagents.py:405-463`(복합 해제 `:434-435` · 탈락 `:458`) · 호출 `:1157` · `is_composite` 전파 `:843` |
| 2단 복합 판정 | `src/orchestration/intent_planner.py:419` |
| 1단 ambient 키 | `src/orchestration/deep_agent.py:141-162` |
| 3단 계획 루프 대상 상속 | `src/orchestration/tier3_plan.py:86` · `:260` |
| 동일 스키마 SQL 공유 | `src/nodes/multi_db_executor.py:705-739` |
| 병합(concat) | `src/nodes/multi_db_executor.py:2662-2703` · `src/nodes/result_merger.py:45` |
| 응답 미리보기 20행 · 수치 요약 | `src/nodes/output_generator.py:491-495` · `:512` |
| 존별 0행·실패 각주 | `src/nodes/output_generator.py:756-800` |
| 라우터 프롬프트 VM 예시 | `src/prompts/semantic_router.py:164-170` · 위치 예시 슬롯 `:129` |
| 결정 | `docs/02_decision.md` D-005(`:105`) · D-065(`:528`) · D-202 잔여 ①(`:1941`) · D-225(`:2404`) |
| 계획 | `plans/103` 격차표 D-1(77행) · P1-2(249·282행) · `plans/108` G-2(387행) |

## 부록 C. F-4 측정 원자료와 구현 요지 (v3 · 2026-09-22 · 측정 후 기각)

**측정 조건**: 로컬 MLX `Qwen3.5-9B-OptiQ-4bit`(`scripts.bench --show-env` = 워커·오케스트레이터 `mlx` · 공유 8080 서버 순차 호출 · 과금 0). 대상은 라우터 LLM 분류 단독(`_llm_classify`)이다 — 노드 출력(결정적 고정 후)이 아니다.

**① §1.2 질의 — 활성 DB b0·gp·yd(부록 A와 같은 조건) · 질의당 2회**

| 질의 | before(HEAD 프롬프트) | after(규칙 7 + VM 교정) | after(+ few-shot 예시) |
|---|---|---|---|
| #3 "공동존의 vm 중 최대 cpu와 최대 memory 서버 top 10을 정리해줘" | gp · gp | gp · gp | gp · gp |
| #4 "공동존 VM 중 최대 CPU 사용률 상위 10개 서버 조회" | gp · gp | gp · gp | gp · gp |
| #5 "공동존 VM 중 최대 메모리 사용률 상위 10개 서버 조회" | yd · yd | gp · gp | gp · gp |
| #6 "공동존 서버의 최대 CPU 사용률 상위 10개" | gp · gp | gp · gp | gp · gp |

few-shot 예시와 **같은 꼴**의 질의("공동존 서버 목록 보여줘")만 [gp, yd]를 냈다(원시 응답 확인 · 1회) — 일반화하지 않았다.

**② routing gold 전체(`scripts/eval_routing.py` · 26건 · `DB_DOMAINS` 전체)**: before 24/26 통과 · 멀티 보존 3/4 → after(세 변경 모두) 23/26 · 멀티 보존 4/4. 변화: r-013 [gp] → [gp, cloud_portal](여의도는 여전히 없음 · 실패 유지) · **r-073 [b0, itam] → [b0](통과 → 실패)** · r-012 [itsm, itam, b0, gp, yd] → [itsm, itam, b0](통과 유지) · r-076 [b0] → [b0, gp, yd, polestar](통과 유지 · 넓어짐).

**③ 통제 실행(요소별 분리 · 1회씩 · HEAD 프롬프트는 3회 모두 같은 결과로 결정성 확인)**

| 변형 | r-073 | r-013 | r-076 |
|---|---|---|---|
| **HEAD 프롬프트 재구성(통제 — 골든과 바이트 동일 확인)** | **[b0, itam]** | [gp] | [b0] |
| VM 예시 교정만 | **[b0]** | [gp] | [b0, gp, yd, polestar] |
| VM 교정 + 규칙 7 | [b0] | [gp, cloud_portal] | [b0, gp, yd, polestar] |
| VM 교정 + few-shot 예시 | [b0] | [gp, cloud_portal] | [b0, gp, yd, polestar] |
| 규칙 7 + 예시(VM 교정 없음) | [b0] | [gp] | [b0] |
| 세 변경 모두 | [b0] | [gp, cloud_portal] | [b0, gp, yd, polestar] |

→ **VM 예시 교정 단독으로도 r-073이 회귀**하고, 규칙·예시 단독으로도 회귀한다(9B의 프롬프트 민감도). 목표 질의에는 어느 조합도 효과가 없다.

**구현 요지(원복함 — 다시 적용할 때의 핵심)**

- `src/prompts/semantic_router.py`: `_S_JUDGE_RULES`의 6번 뒤에 `{zone_scope_rule}` 슬롯, `_S_EXAMPLES`의 "은행 폴스타" 예시 뒤에 `{zone_scope_example}` 슬롯, VM 예시의 서버 줄을 `{vm_example_server_row}` 슬롯으로(off면 종전 줄 그대로 — 바이트 동일).
  ```python
  SEMANTIC_ROUTER_ZONE_SCOPE_RULE = (
      "7. 위치어 하나가 여러 DB를 포괄하면(존 단위 위치어) 그 DB를 **모두** 선택하고 DB마다 "
      "sub_query_context를 적으세요 — 하나만 고르지 마세요. 예: {zone_examples}\n"
  )
  SEMANTIC_ROUTER_VM_EXAMPLE_SERVER_ROW = (  # 종전: "polestar_b0" · "VM이 설치된 서버 스펙 조회"
      '        {"db_id": "polestar_cm_gp", "relevance_score": 0.8, '
      '"reason": "김포 영역 VM이 설치된 서버 스펙 조회", '
      '"sub_query_context": "VM이 설치된 서버의 CPU, Memory, Disk 사양 조회", "user_specified": false}'
  )
  ```
- `src/routing/semantic_router.py`: 레지스트리 `locations` 중 **프롬프트에 실린(활성) DB를 둘 이상** 포괄하는 위치어만 렌더(`_zone_locations(domains)` → 규칙 예시 `"공동존 서버 목록" → polestar_cm_gp, polestar_cm_yd 모두` · few-shot 1건). 단일 호출 템플릿과 2단 DB 템플릿이 같은 슬롯을 받는다(`**_zone_scope_slots(domains)`) — 1·2단 `classify_dbs`도 같은 프롬프트다. 기본 off 설정 `ROUTER_ZONE_SCOPE_PROMPT_ENABLED`(RouterConfig) + `config/settings_help/router.yaml` 항목까지 만들었으나, 설정 필드 추가가 타 세션 소유 `tests/test_api/test_settings_catalog.py`의 필드 수 단언(350)을 바꿔야 하고 효과 없는 노브를 늘려 착지하지 않았다.

**재측정 조건(하나라도 성립하면 다시 본다)**: ① 운영 모델(FabriX)로 routing 골든셋을 이 변경 on/off 모두 돌릴 수 있을 때(내부망) ② 답변 영역 소유표 플래그(`ROUTER_CAPABILITY_OWNERSHIP_ENABLED`) on 환경 — r-073은 교차 시스템(자산→폴스타) 케이스라 소유표가 있으면 프롬프트 민감도가 달라질 수 있다 ③ 로컬에서 더 큰 모델(27B 이상 캐시 모델)로 같은 표를 재현할 때. 판정 기준: 목표 질의 4건이 두 DB를 내고 골드 통과 수가 줄지 않을 것.
