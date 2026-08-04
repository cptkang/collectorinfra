# 69. 쿼리 생성 영역 구조 리팩토링 — 중복 통합·경로 대칭·계층 정리

> 작성일: 2026-07-30
> **성격**: 구조(코드 형상) 리팩토링 계획. Plan 67(기능 트랙 S·R·N — 단계적 도출·경직성 해소·표준화)과 **상보 관계**이며 기능 변경을 목표하지 않는다. Plan 67이 "무엇을 생성하는가"를 바꾸는 트랙이라면, 본 계획은 "생성 코드가 어떤 형상인가"(중복·비대칭·거대 함수·죽은 코드·계층 위반)를 바꾼다.
> **대상 파일(실측 규모 — v2 재검증 커밋 `0f71d2a` 기준)**: `src/nodes/query_generator.py`(1146L) · `src/nodes/semantic_compiler.py`(1585L) · `src/nodes/query_validator.py`(966L) · `src/nodes/multi_db_executor.py`(1053L) · `src/nodes/query_executor.py`(185L) · `src/nodes/column_deriver.py`(473L) · `src/nodes/candidate_{generator,selector}.py`(160L/292L) · `src/utils/query_gen_common.py`(650L — R3에서 +145L) · `src/db_adapters/polestar/assembler.py`(476L) · `src/prompts/{query_generator,semantic_compiler,column_deriver,candidate_strategies}.py` · `src/graph.py`(라우팅) · `src/state.py`
> † **라인 번호 기준**: §1의 라인은 v1 분석 시점(S3 진행 중 워킹트리) 기준이라 **S3·R3 커밋(263d2f7·322f17e) 이후 표류**했다. v2에서 P0 관련 라인은 HEAD 기준으로 재실측·갱신했고(§1.1), 그 외 §1.2~§1.8의 라인은 방향 지시용 — 착수 시 재실측한다.
> **선행/근거**: plans/67(§1.2 경직성 진단·§5 Phase — R1·R2·N2·S1·S2 완료, S3 진행 중), plans/63(3계층 원칙·overfit_check), plans/61(4경로·삽입 지점 원칙)
> **관련 결정**: D-035(결정적=판단·LLM=보조), D-066(단일/멀티 경로 대칭 — 본 계획이 잔여 비대칭의 수용처), D-067(이중 조립 엔진 금지·드리프트 가드), D-076(SMQ 결정적 컴파일 보존), D-088(공용 계층 DB-agnostic·overfit_check), D-089(어댑터 레지스트리 디스패치), D-099(가드 울타리→조립 편입), D-128(stepwise 루프 — "기존 비대칭 2건은 필요 시 별건" → 본 계획이 그 별건), D-131(지식 정본·DB 레지스트리)
> **신규 결정 예약**: **D-134** — `docs/02_decision.md:8` 안내 라인에 예약 등재 확인됨(2026-07-30 실측: "D-134는 plans/69 예약·미등재·재사용 금지", 현 최대 **D-136**). 등재 직전 `## D-` 헤더·「변경 이력」 표 재확인.
> **상태**: v6 — **P0~P5 전 단계 구현 완료·D-134 등재 완료(2026-08-04)**. 잔여는 문구 통일 7건(사용자 건별 승인 대기)과 후속 기록 4건뿐(v6 개정 이력 참조).
> **개정 이력**:
> - v6(2026-08-04) — **재개 후 잔여 전량 실행 완료 + D-134 등재**. 커밋: P0-⑨(9c98edb — SMQ 프롬프트 count/sum·확장 필드·filterable 노출, S-IR1 도달성 실증 테스트) · P1(8ea4523 — sha256 스냅샷 매트릭스 12키 `tests/test_prompt_render_matrix.py`) · P2 a~d(c1685c7·9b9cfdb·195a80a·a05c180 — 죽은 코드·`sql_dialect`·`llm_compat`·`_ALL_QUERY_LIMIT` 10,000 하향) · P0-⑪(7d4237e — approval_context None 가드) · P3(716b465 — `prompt_blocks.py` 공유 빌더 13종+`TEXT2SQL_PATH_PARITY` 기본 OFF; c6ef528 — 2단계 문구 diff 승인 문서; a0fe224 — W-8 멀티 EAV 규칙 게이트 해제) · P4(bdd5d3f 기록 헬퍼·감사 대칭, a03a1ef 멀티 검증 옵트인 `TEXT2SQL_MULTI_FULL_VALIDATION`, 0ccc501 폴백 로그·파싱 1회화) · P5(75f369e — `src/semantic/` 계층 분리·nodes↔tools 순환 소멸·semantic_compiler 1844→1095L; abbdf8c — overfit 스캔 편입; 43d3822 — 거대 함수 분해 80줄 초과 10→4·피벗 진입점 form_fill/semantic 분리·classify_metric_field 레지스트리 경유 5곳). 검증 = 전 커밋 게이트 4종 그린(스냅샷 12키 무갱신·영역 스위트 실패 집합 = 사전 실패 6건 기준선 완전 일치·arch/overfit --ci·골든 바이트 보존), gold_smq 18건 SQL 바이트 동일, 격리 worktree 대조. **잔여 기록**: ①문구 통일 W-1·W-3·W-4·W-5·W-7·U-1·S-1은 멀티 프롬프트 바이트 변경 수반 — `docs/plan69_p3_wording_diff.md` 기준 사용자 건별 승인 후 적용(W-2·W-6·W-8은 종결 — W-6은 별건 확인, W-8은 a0fe224) ②피벗 wrapper(`build_multi_resource_pivot_sql`)는 테스트 4곳 소비 잔존으로 유지 — 신 진입점 전환 후 제거 가능 ③`tools/validation.py`→`nodes.query_validator` 단방향 임포트 잔존(순환은 소멸 — nodes→tools는 함수 지역 임포트로 차단) ④arch_check application 내부 경고(63건)는 현행 warning 정책 유지 ⑤§1.6 `strip_db_prefix`는 미발현 정정(실측 결과 소비처 존재).
> - v5(2026-07-30) — **P0 실행 기록**: 커밋 9건 — ①(546f89e 튜플 렌더) ③(e854d47 EAV 규칙) ⑥(1899547 prior_rows) ④(b98f68a 감사 경고 — validation_result에 warnings 구조화 노출 추가) ⑤(a23aa57 멀티 실패 감사) ⑦(50d714f 서브쿼리 LIMIT — 문자열 리터럴 존중 괄호 스트리퍼) ⑧(4025627 재시도 예산 — 라우팅 4함수 max_retry 파라미터+partial 배선, config 주석 정정, grep 단언 테스트) ②(c7b7b87 no_db 오분류 — AsyncExitStack 분리, 파이프라인 실패는 pipeline_error로 가시화·재생성은 재시도 루프 위임) ⑩(e90bf35 stepwise scope_note 배선 — 스코프 부재 시 바이트 불변). 검증 = 항목별 재현 테스트 + 영역 스위트(text2sql·test_nodes·test_utils 771건) 기준선 대조 회귀 0 + 설정 카탈로그 46건 그린. **주의**: 워킹트리에 타 세션(Plan 68) 미커밋분 존재로 사전 실패 6+3건은 기준선 고정 처리(스크래치 기록). ⑨는 semantic ON 경로 프롬프트 바이트를 바꾸는 작업이라 sha256 골든 일괄 갱신과 함께 별도 착수가 적절해 이월.
> - v4(2026-07-30) — **Plan 67 잔여 3웨이브 커밋(171b23b N4·ccc82be D-132·0d6c1e3 R1 잔여, v15 "코드 작업 전량 소진") 대조 재검증**: ①**⑨ 잔존 재확인** — R1 잔여는 폴스타 어댑터 프롬프트(`polestar/prompts.py` 16블록 마커화)만 렌더 전환, 1방 SMQ 프롬프트·`render_catalog`(1146→**1171**로 이동, 여전히 "avg, max, min")는 미변경 — P0 편입 유지 ②어댑터 훅 **7종→7+옵트인 1종**(`check_value_column_join` — `TEXT2SQL_PROMPT_KNOWLEDGE_RENDER` 게이트 공유, adapter.py:74) — §0.3-4·P4-3 반영 ③**N4 taxonomy가 semantic_compiler에 유입**(`GUARD_HYPERNYM_EXPAND`·`_expand_hypernym_ambiguity`·`_contested_parent_aliases`) — P5-1 이동 목록에 편입 ④신규 플래그 7종(hypernym_ambiguity·prompt_knowledge_render·annotation 4종 등) — P3-2 플래그 명명 충돌 없음 확인 ⑤**프롬프트 렌더가 2모드**(마커 원문 vs 정본 렌더, 기본 OFF)가 됨 — P1 sha256 골든과 P3 검증을 2모드 매트릭스로 확장 ⑥D-132 등재 완료 반영(예약 잔존은 D-134뿐) ⑦P0-3 ⓓ(Plan 67 잔여와 파일 조율) 해소 — Plan 67 잔여는 사용자 측 2건(DB 적재→E1·Redis→질의 이력)뿐.
> - v3(2026-07-30) — **§1.1-⑨(S-IR1/5 도달성 갭) P0 편입 확정(사용자 결정)** + **잔여 게이트 4건 권고안 확정(§0.3 신설)**: ②문구 통일은 "동작 불변 통합 선행 → diff 일괄 승인" 2단계 ③경로 대칭 확대는 (a)~(d) 채택·(e) 현행 유지, 단일 플래그+갭별 발동 로그 ④멀티 검증 강화 채택(옵트인→위양성 계측→기본 전환 별도 판단), 멀티 재시도 예산 정렬 동반 ⑤`_ALL_QUERY_LIMIT`은 10,000 하향 우선 권고(하향 전 초과 반환 이력 실측 게이트 — 실수요 확인 시 스펙 개정으로 반전). ②~⑤는 사용자 승인 대기.
> - v1(2026-07-30) — 최초 작성. Explore 3방향 전수 분석(S3 진행 중 워킹트리 기준).
> - v2(2026-07-30) — **Plan 67 S3 코어·R3 커밋 완료(구현 전체 종결, v14) 반영 + P0 전건 HEAD 재검증**: ①게이트 §0.2-1 해소(워킹트리 잔여는 Plan 68 웹UI — 본 계획 대상 파일과 무겹침) ②P0-⑦(_YYYYMM_RE) **S3에서 해소 확인**(`\d{4}(?:0[1-9]|1[0-2])` 실측) → validator `_has_limit_clause` 서브쿼리 오매칭으로 교체 ③stepwise `server_scope` 미전달 **잔존 확정**(`_select_smq_stepwise:1388` 시그니처 실측) — P0 편입 확정 ④**신규 기능 갭 발견**: S-IR1/5(count·전역 집계)가 "즉시 활성"(Plan 67 v13)인데 1방 SMQ 프롬프트·`render_catalog:1146`("avg, max, min"만)·normalize 승격 가드 어디에도 count 경로가 없어 **기본(1방) 경로에서 도달 불가** — §1.1 하단 ⑨ ⑤P0-② 오분류 범위 정밀화(내부 `_execute`는 예외 포획 — `run_candidate_pipeline` 자체 예외만 오분류) ⑥P1-1을 기존 `scripts/prompt_render_diff.py`·`catalog_diff.py`(D-131) 확장으로 정정 ⑦P4-4를 동작 변경으로 강등(옵트인) ⑧효율 항목 신설(validator 중복 파싱 — §1.9·P4-5) ⑨D-134 예약이 결정 문서에 이미 반영됨 확인.

---

## 0. 핵심 결론과 착수 게이트

### 0.1 진단 요약 — 문제는 "기능 부족"이 아니라 "형상 부채"다

쿼리 생성 영역(~6,300L)을 전수 분석한 결과, 결함 다수가 이미 알려진 패턴(Known Mistakes "단일/멀티 비대칭이 반복 원인")의 코드 형상 버전이다:

1. **단일/멀티 경로가 사실상 2벌 구현** — 공유 헬퍼(D-066)로 8개 함수만 공유하고, 프롬프트 조립·스키마 텍스트화·검증·감사 로그는 각자 재구현. 바이트 동일 42줄 복제 1건 포함, 준-동일 블록 9건(§1.2).
2. **비대칭이 기능 갭으로 축적** — 어댑터 전용 템플릿·값 인덱스·prior_scope 결정적 전달은 단일에만, 스키마 한정 규칙(D-057)·DB2 명시 힌트는 멀티에만(§1.3). 멀티 경로는 폴스타 어댑터 검증 훅(기본 7종+옵트인 1종 — v4)이 전부 미적용이고 실패 시 감사 로그가 남지 않는다.
3. **거대 함수 8개**(80줄 초과, 최대 434줄)가 트랙 분기·프롬프트 조립·계측을 한 몸에 담아 수정 비용을 키운다(§1.4).
4. **계층·의존 위반** — `nodes ↔ tools` 런타임 순환(지연 임포트로 은폐), private 심볼 교차 임포트 4건, 어댑터 레지스트리 우회 직접 임포트(§1.5).
5. **실결함 10건(⑨·⑩ 편입 확정 포함)** — 리팩토링과 무관하게 현재 동작이 잘못되었거나 기능 취지가 미달성인 것(프롬프트에 튜플 repr 렌더, 감사 경고 유실, 예외 오분류, S-IR1 기본 경로 도달 불가 등, §1.1 — 전건 HEAD 재검증 완료). 리팩토링 전에 분리 수정한다.

### 0.2 착수 전 사용자 확인 항목 (게이트)

| # | 항목 | 배경 | 권고(상세 §0.3) |
|---|---|---|---|
| 1 | ~~S3와의 시퀀싱~~ → **해소됨(v2 실측·v4 재확인)** | S3·R3에 이어 잔여 3웨이브(N4·D-132·R1 잔여)까지 커밋 — Plan 67 v15 "**코드 작업 전량 소진**". 잔여는 사용자 측 2건(DB 적재→E1 평가·Redis→질의 이력 적재)뿐. 현 워킹트리 미커밋분은 Plan 68 웹UI(무겹침) | 게이트 통과. §1.1-⑨는 **P0 편입 확정(v3 사용자 결정, v4 잔존 재확인)** — 파일 충돌 요인 소멸 |
| 2 | **프롬프트 문구 통일 범위** | 단일/멀티의 준-동일 블록은 문구가 미세하게 다르다(§1.2). 통일하면 프롬프트 바이트가 바뀌어 LLM 출력 분포가 달라질 수 있다 | **권고: 2단계** — 1차 동작 불변 통합만(sha256 동일), 문구 통일은 P3-1 완료 후 diff 리포트 일괄 승인(§0.3-2) |
| 3 | **비대칭 해소의 방향** | "단일에만 있는 주입을 멀티에 확대"는 동작 변경(예: 멀티에 어댑터 템플릿 주입 시 폴스타 멀티 질의 프롬프트가 전면 교체됨) | **권고: (a)~(d) 확대 채택·(e) 현행 유지** — 단일 옵트인 플래그+갭별 발동 로그(§0.3-3) |
| 4 | **멀티 경로 검증 강화** | `_validate_sql_simple`은 테이블·컬럼 존재, EAV 금지 조인, 어댑터 7훅을 검사하지 않는다. full validator로 바꾸면 기존에 통과하던 SQL이 거부될 수 있다 | **권고: 채택** — 옵트인→위양성 계측→기본 전환 별도 판단, 멀티 재시도 예산 정렬 동반(§0.3-4) |
| 5 | **행 수 상한 스펙 모순** | `spec.md:721` "최대 반환 행 수 10,000행" vs `_ALL_QUERY_LIMIT = 100_000`(`query_gen_common.py`, "전체" 질의·명시 건수 clamp) | **권고: 10,000 하향 우선** — 단 하향 전 초과 반환 이력 실측 게이트, 실수요 확인 시 스펙 개정으로 반전(§0.3-5) |

### 0.3 잔여 게이트 권고안 상세 (v3)

**0.3-2. 프롬프트 문구 통일 — "동작 불변 선행, 통일은 일괄 승인" 2단계 (권고)**
- 1차(P3-1): 준-동일 블록을 공유 빌더로 추출하되 현행 문구를 경로별 파라미터로 보존 — P1 sha256 골든으로 바이트 동일 실증. 선례: D-131 R1의 프롬프트 렌더 전환이 같은 방식("바이트 동일 실증")으로 회귀 0을 달성했다.
- 2차(별도 승인): 통합 후 남는 문구 차이는 자동으로 목록화된다(공유 빌더의 경로별 파라미터가 곧 차이 목록). 이 diff 리포트를 전건 사유와 함께 일괄 승인받아 통일 — 특히 실질 기능 차이 3건(자동 매핑 실패 블록의 한글 alias 강제 여부·금지 JOIN 경고 문구·스키마 헤더 표기)은 채택안을 각각 명시해 제시한다.
- 기각한 대안: 처음부터 문구 통일 — 어느 쪽 문구가 LLM 성능에 유리한지 근거 없이 바이트를 바꾸는 것이라 회귀 원인 분리가 불가능해진다.

**0.3-3. 경로 대칭 확대 — (a)~(d) 채택, (e) 현행 유지 (권고)**
- **(a) 멀티 어댑터 디스패치 주입: 채택(최우선)** — D-066 원형 결함("멀티가 축소 재구현이라 열화")과 동형. 폴스타 DB가 멀티 대상일 때 알람/성능 전용 템플릿(D-131 정본 렌더)이 통째로 빠지는 것은 의도로 볼 근거가 없다(단일과 같은 `get_adapter` 디스패치가 정답).
- **(b) 스키마 한정 규칙(D-057) 단일 주입: 채택** — b0(DB2, 레지스트리 실측) 단일 경로 대비. 폴스타 게이트 안이라 리스크 낮음.
- **(c) value_index·pivot_block 멀티 주입: 채택** — D-128이 "필요 시 별건"으로 기록한 멀티 value_index 미전달의 수용처가 본 계획이다.
- **(d) prior_scope 결정적 전달 멀티 대칭: 채택** — D-099 ④가 단일 경로의 우회(D-086)만 해제했고 멀티의 "prior_block 있으면 컴파일 우회"는 그 잔재다(대칭 미완). 착수 시 커밋 이력으로 의도 여부 최종 확인 후 진행.
- **(e) 한글 alias 지시 모순: 현행 유지** — 멀티의 한글 강제는 폼필 헤더 매칭(D-066 후속4·5)에서 나온 의도된 차이. 통일 강행 금지, 사유 주석만 명시.
- 방식: 개별 플래그 4개는 과설계 — **단일 플래그(`TEXT2SQL_PATH_PARITY`류, 기본 OFF) + 갭별 발동 로그**로 어느 갭이 실제 발동했는지 관측(Known Mistakes "실제 주입 실측"). OFF 시 바이트 동일. 기본 ON 전환은 폴스타 멀티 시나리오 스모크(D-127 승인 별도) 후 판단.

**0.3-4. 멀티 검증 강화 — 채택 (권고)**
- 근거: 어댑터 훅(D-085·D-096~D-099 기본 7종 + `check_value_column_join` 옵트인 1종 — v4 실측, `prompt_knowledge_render` 게이트 공유)은 전부 **실측 사고에서 나온 가드**인데 멀티 경로만 전부 미적용 — 같은 폴스타 DB를 단일로 조회하면 차단되는 결함 SQL이 멀티로 조회하면 통과한다. 방어 비대칭을 정당화할 근거가 없고, full validator는 순수 파이썬(LLM 0회)이라 비용도 미미하며 그 비용마저 P4-5(파싱 1회화)가 상쇄한다. 멀티 적용 시 훅 집합은 단일과 동일 소스(`adapter.validation_checks()`)를 소비해 옵트인 게이트까지 자동 대칭.
- 리스크·완화: 위양성 거부로 멀티 실패율이 오를 수 있다(멀티는 재시도 1회 고정이라 민감). ①옵트인 기간에 거부 사유 로그 계측 → 위양성 실측 0 확인 후 기본 전환 별도 판단 ②검증 강화와 함께 **멀티 재시도 예산을 단일과 같은 `max_retry_count` 참조로 정렬**(P0-⑧과 연결 — 1회 고정의 근거가 없다면 상수 통일).
- 기각한 대안: 간이 검증 유지 + 훅만 선별 추가 — 검증 로직 2벌 유지가 본 계획이 없애려는 형상 부채 그 자체다.

**0.3-5. 행 수 상한 — 10,000 하향 우선, 실측 게이트 후 확정 (권고)**
- 근거: `spec.md:721`이 계약 문서 정본이고 CLAUDE.md도 "max rows: 10,000"을 명시 — 코드가 스펙을 따르는 것이 기본값. `_ALL_QUERY_LIMIT=100_000`은 D-066에서 "전체 조회가 default_limit(1000)에 절단되는 결함"을 고치며 도입된 값으로, 10,000이어도 같은 목적(절단 방지)을 달성한다(폼필 피벗은 서버당 1행 — 인프라 서버 수 규모상 10,000 초과 개연성 낮음).
- 하향 전 게이트: 감사 로그·`query_attempts` 이력에서 **10,000행 초과 반환 실적이 있는지 실측** — 실적이 있으면 실수요이므로 반대로 스펙 개정(10,000→상향)을 사용자에게 제안한다. 실적 조회가 불가하면(로그 미보존) 하향 후 절단 발생 시 `truncated` 사유를 응답에 노출(침묵 절단 금지)하는 안전장치를 동반한다.
- 부수: 이 상수를 단언하는 테스트 repo 전체 grep 일괄 갱신(Known Mistakes) + D-066 구현 세부 변경이므로 D-134 등재 시 부기. `config.py:140` 주석("DB 레벨 제한은 MCP 서버에서 관리")의 MCP 측 하드 캡 존재 여부도 착수 시 확인해 이중 방어 관계를 문서화.

---

## 1. 실측 진단 (전수 분석 — 2026-07-30)

> 라인 번호는 분석 시점 기준. †표시 파일(semantic_compiler·assembler)은 S3 진행분 포함 워킹트리 기준이며 표류 가능.

### 1.1 실결함 — 리팩토링 이전에 수정할 것 (P0) ※전건 HEAD `0f71d2a` 재검증(v2)

| # | 결함 | 위치(HEAD 기준) | 근거 |
|---|---|---|---|
| ① | **`build_generic_period_hint`가 튜플 repr을 프롬프트에 렌더** — `월(YYYYMM) '('202604', '202606')'` 실행 실측. D-102에서 `resolve_stat_month_range` 반환이 str→tuple로 바뀌었는데 이 함수만 미갱신(시그니처 `stat_month: str | None` 그대로). R3(D-136)의 2단 폴백 확장에서도 미교정 — 테스트 0건이라 미검출 지속 | `query_gen_common.py:216-234`, 호출부 `query_generator.py:395`·`multi_db_executor.py:639` | `GENERIC_LLM_MAPPING` 옵트인 경로라 폴스타 미발현 |
| ② | **다중 후보의 `run_candidate_pipeline` 자체 예외가 "DB 연결 실패"로 오분류** — per-SQL 실행 예외는 내부 `_execute`가 포획하나(650-651), 파이프라인 본체(LLM 호출·선택 로직) 예외는 외곽 except(660)로 흘러 `method="no_db"`·`confidence=0.0`으로 위장 | `query_generator.py:642-670` | 침묵적 오분류 — Known Mistakes "예외 삼키는 폴백 가시화" 위반 |
| ③ | **`query_guide` 빈 프로필에서 EAV 조인 규칙 통째 누락** — `if eav_patterns and guide:` 조건이 guide 빈 문자열이면 prepend 자체를 건너뜀 | `query_generator.py:86` | 유사어·value_joins는 빈 문자열 뒤에 이어붙어 출력됨(부분 렌더) |
| ④ | **validator 경고가 감사 로그에 유실** — `log_query_execution`의 `validation_warnings`·`masked_columns` 인자를 어느 호출자도 전달하지 않음(v2 grep 재확인 — 정의부 외 등장 0) | `security/audit_logger.py:72,102` vs 전 호출부 | "모든 쿼리 실행 감사 로깅" 제약의 부분 공백 |
| ⑤ | **멀티 실행 실패 시 `QueryAttempt.sql=""` 유실 + 감사 로그 미기록** — 성공 경로만 감사, 실패는 attempt에 SQL조차 없음 | `multi_db_executor.py:287` 일대 | 단일 경로(query_executor 4경로 전부 감사)와 비대칭 |
| ⑥ | **`prior_rows`가 `create_initial_state` 초기화 목록에서 누락**(v2 grep 재확인 — `state.py` 내 등장은 선언 1곳뿐) | `state.py:199` 선언 vs 초기화 목록 | Known Mistakes "요청 스코프 상태 명시 초기화" 위반 소지(멀티턴 승계 오염) |
| ⑦ | **`_has_limit_clause`가 서브쿼리 내부 LIMIT에도 매칭** — 외곽 행 제한이 없는데 서브쿼리에 LIMIT이 있으면 자동 보정(`_add_limit_clause`)이 억제되어 무제한 반환 가능 | `query_validator.py:897-903` | "행 제한 절" 제약(§spec)의 우회 구멍. 수정 시 생성 SQL이 바뀌므로(외곽 LIMIT 부가) 재현 테스트 동반 |
| ⑧ | **재시도 상한 하드코딩 7곳** — `QueryConfig.max_retry_count=3`(`config.py:144`)이 존재하는데 `graph.py:60,72,100,114`·`subagents.py:472,485`·`result_organizer.py:71`이 리터럴 `3`(v2 grep 재확인 — `>= 3` 5곳·`< 3` 2곳) | 좌기 | 상수 변경 시 7곳 동기화 필요 — 결정적 상수 일괄 갱신 원칙의 예방 대상 |

**해소 확인(v2)**: (구)⑦ `_YYYYMM_RE` 범위 무검증 — **S3에서 해소됨**(`semantic_compiler.py:66` = `\d{4}(?:0[1-9]|1[0-2])` 실측). 목록에서 제거.

**P0 편입 확정 2건 (v2 재실측 — 둘 다 S3·R3 이후에도 잔존 확정, ⑨는 v3 사용자 결정으로 편입)**:
- **⑨ S-IR1/5 기본 경로 도달 불가(기능 갭) — P0 편입 확정(v3, v4 잔존 재확인)**: `_AGG_FN`은 count/sum 지원·`SMQ.entity_count/global_aggregate` 필드 존재·**커버리지 확장 즉시 활성**(Plan 67 v13 인터뷰 결정)인데, ⓐ1방 SMQ 프롬프트(`prompts/semantic_compiler.py`)에 count/sum·entity_count·global_aggregate 안내 **0건**(grep 실측 — R1 잔여 렌더 전환은 폴스타 어댑터 프롬프트만 대상이라 미해소) ⓑ`render_catalog`(`semantic_compiler.py:1171`)는 여전히 "집계(agg): avg, max, min"만 안내 ⓒnormalize 승격 가드도 time 계열 2종뿐(count 계열 승격 없음 — `GUARD_*` 14종 전수 확인). 안내는 stepwise 프롬프트(`prompts/column_deriver.py:56,67`)에만 있는데 stepwise는 기본 OFF — **즉 기본(1방) 경로에서 "서버 수"류 질의는 LLM이 해당 IR을 산출할 수 없어 결정적 컴파일이 발동하지 않는다**(S-IR1 편입 취지 미달성).
- **⑩ stepwise 경로 `server_scope` 미전달**: `_select_smq_one_shot:1561`은 `server_scope`를 받아 스코프 노트를 주입하지만 `_select_smq_stepwise:1630` 시그니처에는 없음(v2 실측·v4 재확인 — 라인은 잔여 웨이브 반영 갱신). 선행 스코프 + stepwise ON 조합에서 D-099 ⑤(커버리지 진입 보정)가 무력화될 수 있다. stepwise가 기본 OFF라 현행 미발현 — **P0 편입 확정**(D-128 활성화 판단 전 선행 수정).

### 1.2 중복 (단일 ↔ 멀티 경로 = query_generator.py ↔ multi_db_executor.py)

| 중복 블록 | 단일 | 멀티 | 성격 |
|---|---|---|---|
| `_get_eav_pattern` + `_extract_eav_tables` | qg:753-794 | mdb:57-98 | **42줄 바이트 동일**(후행 공백 1줄 차). 후자는 양쪽 다 죽은 코드(§1.6) |
| EAV 조인 규칙 + value_joins 가이드 | qg:79-125 | mdb:518-554 | 문구 동일, 변수명만 다름 |
| column_mapping 스키마 필터링 | qg:852-892 | mdb:639-670 | 로직 동일(단일만 클로버링 방지 변수 분리) |
| 정규/EAV 매핑 분리 | qg:895-903 | mdb:681-689 | 동일 |
| `child_eav`/피벗 판정 | qg:913-925 | mdb:713-725 | 동일 |
| EAV 피벗 매핑 프롬프트 블록 | qg:964-1001 | mdb:778-817 | 거의 동일(format vs f-string) |
| 자동 매핑 실패 필드 블록 | qg:1003-1013 | mdb:822-835 | 문구 상이(멀티가 한글 alias 강제 추가) |
| 스키마 텍스트 포맷 | `_format_schema_for_prompt` qg:1035-1127 | `_format_schema` mdb:952-993 | 멀티는 설명·유사어·NOT NULL·참조 섹션 없는 축약판, 헤더 문자열도 상이 |
| stepwise 재료 조립 | qg:501-535 | mdb:379-426 | 유사어 출처·value_index 주입 여부 상이 |
| 이력 few-shot 어댑터 | qg:544-570 | mdb:429-452 | db_id 출처만 다름 |
| KBGenAI 더미 AIMessage 조립 | qg:428-435 | mdb:881-886 | 형태 상이 동일 목적 |

**기타 중복**: ⓐ KBGenAIChat 판정 3형태 6곳 — `isinstance`(qg:431)·클래스명 문자열 비교(semantic_compiler:1313†, column_deriver:281·369)·bool 인자 주입(candidate_generator:111, candidate_selector:94). ⓑ 한국어 지표 키워드 테이블 2벌 — `query_gen_common.py:177-178` ↔ `assembler.py:66-81`(집합·판정 규칙 완전 동일, D-088 계층 가드 사유로 의도 분리 주석 있음 — 수동 동기화 의존). ⓒ "모든/전체" 키워드 2벌 — `query_validator.py:179` 인라인 vs `query_gen_common.py:137`. ⓓ CTE 허용 판정 2벌 — `query_validator._get_statement_type` vs `multi_db_executor:908-912` 복제. ⓔ 위험 키워드 목록 2벌 — validator의 `FORBIDDEN_SQL_KEYWORDS`(15종) vs mdb:915(7종 별도 목록). ⓕ qg 내부: 폼필 결정적 경로(qg:202-228)와 LLM 폴백 프롬프트(qg:913-929)가 EAV 분해를 각각 재수행하고 `cmm_metric_stat` 제외 처리는 전자에만 있음(후자는 "반드시 SELECT 포함"으로 강제 — RC2 취지 역행). ⓖ `query_executor.py`는 성공/Timeout/ExecutionError/Exception 4블록이 경과시간→QueryAttempt→감사→반환 동일 구조 반복(185줄 중 4×~30줄).

### 1.3 경로 비대칭 (기능 갭 — D-066의 잔여)

**단일 경로에만 존재**: ①어댑터 디스패치(`get_adapter` → 폴스타 알람/성능 전용 시스템 템플릿, qg:736-743) — 멀티는 공통 템플릿 무조건 사용(mdb:603, `get_adapter` grep 0건). ②값 인덱스 리터럴 주입(E5-2, qg:383-385 — `build_value_index_block` 호출처 단독). ③스키마 주석(설명·유사어·NOT NULL·RESOURCE_TYPE/EAV 참조 섹션). ④멀티턴 이전 SQL 참조 블록. ⑤`prior_scope`의 결정적 컴파일 전달(qg:306 → HAVING) — 멀티는 `prior_block` 있으면 컴파일 자체를 우회(mdb:496). ⑥HITL 저신뢰 회부·3단 폴백(`_decide_fallback_tier`). ⑦폼필 피벗 프롬프트 블록(`build_multi_resource_pivot_block` 호출 qg:931 단독 — 멀티는 `_sql`만 임포트).

**멀티 경로에만 존재**: ①스키마 한정 규칙 D-057(`get_schema_prefix`, mdb:584-597). ②DB2 방언 명시 힌트(mdb:598-601). ③성능 지표 매핑 분리 안내(RC2, mdb:697-704·763-776). ④동일 스키마 SQL 재사용 캐시. ⑤한글 alias 강제 — 단일은 정반대(`"테이블명.컬럼명"` 형식 지시, qg:957-960 vs mdb:757-760).

**검증·감사 비대칭**: 단일 = full validator(14단계+어댑터 7훅) + 4경로 전부 감사 로그(user_id·thread_id·retry_attempt 포함). 멀티 = `_validate_sql_simple`(인젝션·테이블/컬럼 존재·EAV 금지 조인·어댑터 훅 전부 생략) + 성공 경로만 감사(`retry_attempt=0` 하드코딩). 재시도 루프도 3벌 — 그래프 조건부 엣지(상한 3)·subagents while(상한 3+스텝 10)·멀티 내부 1회 고정.

**엔진 정보 흐름 결함**: `active_db_engine` 쓰기는 `subagents.py:837` 한 곳뿐 — deepagents 미경유 순수 그래프 경로에서는 항상 None이라 `or "postgresql"` 폴백(5곳)이 무조건 적용된다. 멀티 경로는 state를 안 쓰고 `get_domain_by_id(db_id).db_engine`을 직접 읽는다(mdb:191-192). 방언 판정식도 3형태 혼재 — `== "db2"` / `.lower() == "db2"` / `"db2" in eng`.

### 1.4 거대 함수 (80줄 초과, 분기·중첩 실측)

| 함수 | 위치 | 규모 | 내부 단계 |
|---|---|---|---|
| `_generate_sql` | mdb:455-889 | **~434줄** | 스키마 텍스트→방언 힌트→매핑 분리→피벗 판정→프롬프트 7블록→LLM→추출 |
| `query_generator` | qg:241-498 | 258줄·분기22·중첩5 | 준비→결정적 해석→트랙1 폼필→트랙C 시맨틱→트랙A LLM 폴백(100줄)→계측→반환. `sql` 할당 지점 4곳 |
| `_build_user_prompt` | qg:797-1032 | 236줄·분기25·**중첩6** | 조건부 섹션 9종 문자열 누적 |
| `build_multi_resource_pivot_sql` | assembler:249-413† | 165줄·**파라미터 17개** | 폼필(한글 라벨→분류)과 시맨틱(`explicit_measures`) 겸용이 팽창 원인 |
| `_run_loop` | column_deriver:236-349 | ~113줄 | tool-calling while |
| `select_candidate` | candidate_selector:134-247 | ~113줄 | 4단 선택 파이프라인 |
| `_compile_ab` / `_compile_c` / `compile_from_nl` | semantic_compiler:690/945/1447† | 108/93/105줄 | §1.5 참조 |
| `_format_structure_guide` / `_format_schema_for_prompt` | qg:56-159/1035-1127 | 104/93줄 | 조립 순서 8단계/스키마 주석 |
| `_validate_forbidden_joins` | query_validator:641-806 | 166줄 | EAV 금지 조인 3패턴+역방향 |

### 1.5 계층·의존 위반

- **`nodes ↔ tools` 런타임 순환**: `column_deriver.py:127` → `tools/binding.py:22` → `from src.nodes.semantic_compiler import render_catalog`, `tools/catalog.py:14` → `SMQ, check_coverage`. 전부 함수 내부 지연 임포트로 은폐된 순환. 원인: **IR 모델(SMQ)·커버리지 판정·카탈로그 렌더가 노드 파일에 정의**되어 있어 하위 계층(tools)이 상위(nodes)를 역참조.
- **private 심볼 교차 임포트 4건**: `assembler`(application)가 `query_gen_common`의 `_normalize_stat_month`·`_utilization_guard` 역참조, mdb가 `query_validator._check_left_join_where_demotion`(+`_find_bare_hangul_tokens` 지연), qg가 `_collect_prior_identity_values`.
- **어댑터 레지스트리 우회**: qg:38·mdb:43이 `db_adapters.polestar.assembler`를 직접 임포트(같은 파일이 검증 훅은 `get_adapter` 레지스트리 경유 — 불일치, D-089 취지 역행).
- **패턴 C 조립 지식 중복(D-067 관점)**: `_compile_c`†가 LIMIT/FETCH FIRST 분기·세미콜론 종결·ORDER BY(NULLS LAST 누락)·리터럴 처리를 assembler와 별도로 재구현. 공용 노드 계층에 폴스타 알람 리터럴(`CA` alias 무검증 가정, severity 매직 넘버 `IN (1,2,3)`) 상존.
- **`semantic_compiler.py`의 폴스타 리터럴**: `LOGICALCORE`/`PHYSICALCORE`/`TotalSize`(normalize_smq†), 폴백 기본값 `cmm_resource`/`server.Server`/`cmm_metric_stat_m` 등 — Plan 67 §1.2-(f)가 "Plan 63 후속, 신규 유입만 overfit_check 차단"으로 범위 판정한 영역이므로 본 계획도 **이동 리팩토링에 수반되는 것만** 처리(전면 소거는 범위 밖 유지).

### 1.6 죽은 코드·미사용 표면 (프로덕션 호출 0건 grep 실측)

| 항목 | 위치 | 비고 |
|---|---|---|
| `_extract_eav_tables` ×2벌 | qg:773-794, mdb:77-98 | 테스트의 임포트·hasattr 존재 검사만 참조 — 테스트 갱신과 함께 삭제 |
| `SQLGuard.is_safe_select` | sql_guard.py:117-142 | 테스트 전용 |
| `_validate_sql_simple`의 `schema_info` 인자 + no-op `if … : pass` | mdb:892, 944-947 | 본문 미참조/완전 무동작 |
| `Awaitable` 임포트 | candidate_generator:18 | AST 실측 |
| `UTILIZATION_VALID_RANGE`·`is_servername_field`·`is_hostname_target` | query_gen_common:224/248/253 | 외부 참조 0(모듈 내부·테스트만) — private화 또는 정리 |
| `log_query_execution(validation_warnings=, masked_columns=)` | 전 호출부 미전달 | P0-④와 동일 건(활용이 정답이면 죽은 인자 아님) |
| `accessed_tables` state 필드 | state.py:187 | 초기화 2곳 외 쓰기 0 |
| deepagents ON 시 도달 불가 노드 배선 | graph.py:431-464·541-614 | 등록·엣지는 있으나 진입 엣지 없음(상호 배타 설계) — 주석 명시 대상 |

### 1.7 상수·매직 스트링 산포

재시도 상한 `3` 7곳(§1.1-⑧) · `validate_sql(default_limit=100)`(query_validator:61, tools/validation:22) vs `QueryConfig.default_limit=1000` 불일치 · `StepwiseLimits` 기본값(column_deriver:85-87)이 config(:240-244)와 값 중복 정의 · SQL 절단 길이 5종(`[:100]`/`[:200]`/`[:500]`/`[:1000]`) · DB id 센티널 `"_default"`/`"default"` 산포 · `"EAV:"` 접두사와 슬라이스 `[4:]` 7곳 · `"postgresql"` 폴백 리터럴 5곳 · 방언 판정식 3형태(§1.3) · `_MAX_PIPELINE_STEPS=10`·샘플 3건·경고 노출 5건 등 매직 넘버 다수.

### 1.8-b 효율 관찰 (v2 신설)

- **validator 중복 파싱**: `validate_sql` 1회 호출에 `sqlparse.parse` 2회(:90, `_get_statement_type` 내부), `sqlparse.format(strip_comments=True)`가 테이블 추출·LEFT JOIN 강등 검사·폴스타 어댑터 7훅에서 **각각 재실행(9회+)**, `_extract_table_names` 3회 호출(:137, :944, :960). 재시도 루프(최대 3회)·다중 후보(후보 수×)에서 배수로 증폭. SQL 길이가 짧아 절대 비용은 작으나, 파싱 산출물(파스 트리·주석 제거 텍스트·테이블 목록)을 1회 계산해 전 검사 단계와 어댑터 훅에 전달하는 구조가 정확성에도 유리(단계 간 동일 입력 보장). → P4-5.
- **동일 프롬프트 재조립**: 재시도 시 `_build_system_prompt`(스키마 텍스트 포함)가 매회 전체 재조립 — 스키마 텍스트는 재시도 간 불변이므로 조립 1회면 충분(P3 섹션 빌더로 자연 해소, 별도 캐시 신설은 하지 않음 — 과설계 방지).

### 1.8 테스트 공백 (리팩토링 안전망 부족 지점)

validator 본체는 촘촘(8개 파일+어댑터 7훅 전부 커버). 공백: **mdb 노드 본체(101-305) 무테스트**(미등록 DB 스킵·`_sql_by_schema` 재사용·1회 재검증·부분 실패 누적) · `query_executor` 일반 Exception 경로("DB 연결 에러" — 소스·테스트 통틀어 grep 0) · `_extract_alias_map` 직접 테스트 없음 · `_validate_forbidden_joins` 패턴 3·역방향 직접 테스트 없음 · graph 라우팅 4함수(`route_after_validation_with_approval` 등) 무테스트 · `subagents` `_MAX_PIPELINE_STEPS` 소진 경로 미커버 · `build_generic_period_hint` 테스트 0건(§1.1-① 미검출 원인).

---

## 2. 목표 구조

```
[프롬프트 조립]   src/utils/query_gen_common.py + (신설) 프롬프트 섹션 빌더
                  단일·멀티가 같은 빌더를 경로별 파라미터로 호출 (문구는 1차 보존)
[방언]            (신설) src/utils/sql_dialect.py — 행 제한 절·판정식·캐스트 스니펫·리터럴
                  assembler / semantic_compiler / query_validator / mdb 가 전부 이것만 소비
[IR·커버리지]     semantic_compiler에서 SMQ 모델·check_coverage·render_catalog 를
                  하위 계층 모듈로 분리 → nodes ↔ tools 순환 소멸 (지연 임포트 해제)
[실행·감사]       QueryAttempt 생성 + 감사 로그를 단일 기록 헬퍼로 — 4중 블록·경로 비대칭 해소
[검증]            멀티 경로가 S1 추출 함수 validate_sql(+어댑터 훅)을 소비 (옵트인 단계 적용)
[상수]            재시도 상한 = config.max_retry_count 단일 소비 · 센티널/접두사 상수화
```

원칙: ①**동작 불변 단계와 동작 변경 단계를 커밋 단위로 분리**(전자는 프롬프트 sha256 골든으로, 후자는 옵트인 플래그+diff 사유로 검증). ②이동은 `git worktree` 클린 기준선 대조. ③D-067 드리프트 가드·골든 테스트가 심볼 위치를 단언하면 repo 전체 grep으로 일괄 갱신.

---

## 3. 단계별 계획

> 공통 검증 게이트(전 Phase): ①전체 스위트 기준선 대조(실패 집합 완전 동일 — HEAD worktree 격리 사본, **`.env` 복사 필수** — known mistakes 2026-07-30 f6cd88f) ②`tests/text2sql/` 골든(폼필 13건 바이트 보존 포함) ③`python scripts/overfit_check.py --ci` ④`python scripts/arch_check.py --ci` ⑤LLM 실 호출 없음(전부 오프라인 — D-127 무관) ⑥프롬프트 문자열에 닿는 단계는 렌더 sha256 전후 대조.
>
> 의존: `P0 → P1 → P2 → {P3, P4 병행 가능} → P5`

### P0 — 실결함 수정 (동작 교정 — 리팩토링과 분리 커밋)

1. ~~게이트: S3 커밋 대기~~ → **해소(v2)**. 착수 시 워킹트리에 본 계획 대상 파일 미커밋분 없음만 확인(현재 Plan 68 웹UI 잔여는 무겹침).
2. §1.1 ①~⑧ + ⑩(stepwise `server_scope` 전달 — 1방 경로와 대칭, `SEMANTIC_SMQ_SCOPE_NOTE`에 상응하는 도출 프롬프트 주입 또는 스코프 노트 전달) 수정 — 각 건 재현 테스트 선행(①은 튜플 입력 렌더 단언, ⑦은 서브쿼리 LIMIT 시나리오, ⑧은 config 값 변경 시 라우팅 반영 단언 등). ⑦은 생성 SQL이 바뀌는 수정이므로 골든 영향 확인 동반.
3. **⑨(S-IR1/5 도달성) — P0 편입 확정(v3 사용자 결정)**: 1방 SMQ 프롬프트(`prompts/semantic_compiler.py`)의 SMQ 스키마 서술에 `agg: count/sum`·`entity_count`·`global_aggregate` 필드 안내 추가 + `render_catalog`(`semantic_compiler.py:1171`) 집계 안내를 `_AGG_FN`과 정합("avg, max, min, count, sum" + 전역 집계·엔티티 수 사용 지침 — 하드코딩이 아니라 `_AGG_FN` 키에서 파생해 재발 차단). **주의**: ⓐ이 변경은 semantic ON 경로의 프롬프트 바이트를 바꾸므로 D-128 검증이 고정한 "1방 프롬프트 sha256 동일" 단언 테스트를 사유와 함께 갱신(Known Mistakes "결정적 상수 일괄 갱신" — repo 전체 grep) ⓑ`render_catalog` 출력을 단언하는 골든(`test_semantic_golden.py`)·tools `search_catalog` 경유 테스트도 함께 갱신 ⓒ"서버 수" 골드 유형(gp-003·gp-013·gp-015)이 coverage inside로 판정되어 결정적 컴파일이 발동하는 회귀 테스트를 추가(S-IR1/5 취지의 도달성 실증) ⓓ~~Plan 67 잔여와 파일 조율~~ → **해소(v4)** — 잔여 3웨이브 커밋 완료로 충돌 요인 소멸. stepwise 프롬프트(`prompts/column_deriver.py`)와의 안내 문구 정합만 확인.
- **검증**: 신규 테스트 그린 + 공통 게이트. 항목별 독립 커밋(원인 분리).

### P1 — 리팩토링 안전망 (테스트 공백 보강 — 코드 무변경)

1. **프롬프트 렌더 골든 — 기존 자산 확장(신설 아님, v2 정정)**: D-131이 만든 `scripts/prompt_render_diff.py`·`catalog_diff.py`와 `tests/test_polestar_prompt_render.py`가 이미 존재 — 이를 단일(`_build_system_prompt`+`_build_user_prompt`)·멀티(`_generate_sql` 프롬프트 구간) 대표 시나리오(일반/폼필/EAV 피벗/재시도/prior_rows/스키마한정)로 확장해 sha256 스냅샷 확보 — P2~P5의 "동작 불변" 판정 기준. **v4 추가**: R1 잔여로 `prompt_render_diff.py`가 이미 +199L 확장됐고 폴스타 프롬프트가 `TEXT2SQL_PROMPT_KNOWLEDGE_RENDER`로 **2모드**(마커 원문/정본 렌더, 기본 OFF)가 됐으므로, sha256 매트릭스는 **렌더 모드 2 × 경로(단일/멀티)** 조합으로 채록한다.
2. §1.8 공백 보강: mdb 노드 본체(스킵·캐시·재검증·부분 실패), `query_executor` 일반 예외, graph 라우팅 4함수, `_extract_alias_map`·`_validate_forbidden_joins` 직접 테스트.
- **검증**: 신규 테스트 전부 현행 동작 그린(버그 고정 아님 — P0에서 교정된 상태 기준).

### P2 — 저위험 기계적 정리 (동작 불변)

1. **죽은 코드 제거**(§1.6): `_extract_eav_tables` 2벌 + 참조 테스트 갱신, `is_safe_select`(테스트 이관 또는 동반 삭제), `Awaitable`, no-op if, `schema_info` 미사용 인자. `accessed_tables`는 삭제가 아니라 미소비 주석 명시(Plan 68 카탈로그 UI와 동일 방침).
2. **상수 일원화**(§1.7): `default_limit` 100/1000 정합(실측 후 의도 확인 — validator 기본은 안전 하한일 수 있음), `StepwiseLimits` 기본값을 config 단일 출처로, SQL 절단 길이·센티널(`_default`)·`"EAV:"` 접두사 상수화. §0.2-5 결정 반영(`_ALL_QUERY_LIMIT`).
3. **방언 유틸 단일화**: `src/utils/sql_dialect.py` 신설 — `is_db2(engine)`(판정식 1형태), `row_limit_clause(engine, n)`, `order_by_clause(…, nulls_last=)`, `sql_literal(value)`, 캐스트 스니펫. 소비처 교체: assembler 3곳·semantic_compiler 2곳·query_validator 1곳·value_index 1곳(동작 불변 — 산출 문자열 동일성 테스트). **판정식 통일 무해 근거(v2 실측)**: 실 engine 값은 `config/db_registry.yaml` 정본에서 `"db2"`·`"postgresql"` 정확 문자열뿐 — `"db2" in eng`(value_index:54)를 `== "db2"`로 통일해도 현행 레지스트리 값에서는 동작 동일. 교체 시 레지스트리 engine 값 전수 grep을 테스트로 단언.
4. **KBGenAI 판정 헬퍼**: `src/llm.py`(또는 clients)에 `is_kbgenai(llm)`·`kbgenai_message_scaffold(...)` — 6곳 3형태 교체.
5. **private 교차 임포트 공개화**: `_normalize_stat_month`·`_utilization_guard`·`_collect_prior_identity_values`·`_check_left_join_where_demotion` — 공개 이름 부여(재배치는 P5).
- **검증**: 공통 게이트 + P1 sha256 골든 전부 동일 + 조립 SQL 문자열 동일성.

### P3 — 프롬프트 조립 통합 (단일/멀티 중복 해소 — D-066 확장)

1. **동작 불변 통합**: §1.2 표의 준-동일 블록을 공유 빌더로 추출하되 **현행 문구를 경로별 파라미터로 보존**(sha256 골든 동일 유지). 대상: EAV 조인 규칙 블록, column_mapping 필터링, 정규/EAV 분리, 피벗 판정, EAV 피벗 블록, 자동 매핑 실패 블록, 이력 few-shot 어댑터, stepwise 재료 조립. 스키마 텍스트화는 `_format_schema_for_prompt`를 옵션화(설명·유사어·참조 섹션 on/off)해 `_format_schema`를 흡수.
2. **비대칭 갭 해소(동작 변경 — 갭별 옵트인·§0.2-3 승인 후)**: 우선순위 순 —
   - (a) **멀티 어댑터 디스패치**: `_generate_sql`에 `get_adapter` 경유 시스템 템플릿 선택(단일 qg:736-743과 대칭). 폴스타 멀티 질의의 전용 템플릿 미주입은 D-066 원형 결함과 동형. **v4 주의**: 어댑터 템플릿은 이제 `prompt_knowledge_render` 2모드(마커/정본 렌더)라 멀티 주입도 단일과 같은 `system_template()` 진입점을 그대로 소비(모드 게이트 재구현 금지 — 자동 대칭).
   - (b) **단일 스키마 한정 규칙(D-057)**: `get_schema_prefix` 주입을 단일에도(현재 멀티 전용) — DB2 단일 경로 대비.
   - (c) **`build_value_index_block`·`build_multi_resource_pivot_block` 멀티 주입** (D-128 기록의 "멀티 value_index 미전달" 별건 수용).
   - (d) **prior_scope 결정적 전달 멀티 대칭**: mdb의 "prior_block 있으면 컴파일 우회"를 단일과 같은 `server_scope` 전달로(D-099 대칭).
   - (e) 한글 alias 지시 모순(단일 `테이블.컬럼` vs 멀티 한글 강제)은 **의도 차이 실측 후** 판정(폼필 헤더 매칭 요구가 원인 — 통일 강행 금지).
   - 각 갭은 독립 플래그 또는 단일 플래그+갭별 로그로 발동 실측(Known Mistakes "양쪽 실제 주입 실측").
3. `_build_user_prompt`(236줄)·`_generate_sql` 프롬프트 구간을 섹션 빌더 목록 순회로 재구성(선언적 조립 — 섹션 추가 시 양 경로 자동 대칭).
- **검증**: 1·3은 sha256 동일. 2는 갭별 렌더 diff 전건 사유 명시 + 골든 회귀 + 플래그 OFF 시 바이트 동일.

### P4 — 실행·검증·감사 대칭 (P3와 병행 가능)

1. **`query_executor` 4중 블록 통합**: 경과시간→`QueryAttempt`→감사→반환을 단일 기록 헬퍼로(결과 분류만 분기) — 185줄 → 실질 절반.
2. **멀티 감사 대칭**: 실패 경로 감사 기록 + `sql`·`execution_time_ms` 보존 + `user_id`/`thread_id`/`retry_attempt` 전달(P0-⑤의 구조화 마무리). 기록 헬퍼를 mdb도 소비.
3. **멀티 검증 강화(옵트인 — §0.2-4)**: `_validate_sql_simple` → S1 추출 `validate_sql`(어댑터 훅 포함) 소비로 교체. 플래그 ON 시 거부 사유 로그 계측 → 안정 확인 후 기본 전환 별도 판단. CTE·위험 키워드 2벌(§1.2-ⓓⓔ)은 이 교체로 자연 소멸.
4. **엔진 정보 결정적 주입 — 동작 변경으로 취급(v2 강등)**: `active_db_engine` 쓰기가 `subagents.py:837` 한 곳뿐이라 **순수 그래프 경로는 현재 항상 `"postgresql"` 폴백으로 동작 중**이다. 레지스트리 실측상 `polestar_b0`가 `engine: db2`이므로, 그래프 단일 경로로 b0을 조회하면 현행은 잘못된 방언(PG)으로 프롬프트·validator 자동 LIMIT 절이 만들어진다 — **주입은 이를 고치는 것이지만 산출 SQL·프롬프트가 바뀌는 동작 변경**이다. 처리: ①폴백 발동 로그를 먼저 부착해 그래프 경로에서 실제로 b0(DB2)이 흐르는지 실측 ②실측상 흐르면 결정적 주입(수정 성격 — 사유와 함께), 흐르지 않으면 주입 + "도달 시 정합" 테스트만. `get_domain_by_id(active_db_id).db_engine` 단일 출처(D-131 레지스트리 정합), 쓰기 지점은 라우팅/스키마 노드 1곳.
5. **validator 파싱 1회화(§1.8-b, 동작 불변 효율)**: `validate_sql` 진입부에서 파스 트리·주석 제거 텍스트·테이블 목록을 1회 계산해 내부 검사 단계와 어댑터 훅 시그니처에 전달(훅 시그니처 확장은 하위호환 유지 — 기존 훅은 원문 인자만 받아도 동작). 산출 검증 결과 불변을 기존 validator 테스트 8파일 + 어댑터 훅 테스트로 확인.
- **검증**: 공통 게이트 + 감사 레코드 스키마 테스트 + 3(옵트인)은 OFF 시 동작 불변 + 4는 발동 로그 실측 선행.

### P5 — 컴파일러·계층 구조 정리 (S3 안정화 후)

1. **IR 계층 분리**: `SMQ`·`SMQOrderBy`·`SMQFilter`·`CoverageResult`·`check_coverage`·`render_catalog` + **N4 taxonomy 헬퍼**(`_expand_hypernym_ambiguity`·`_contested_parent_aliases` — v4에서 유입 확인)를 `src/nodes/semantic_compiler.py`에서 하위 모듈(제안: `src/semantic/` — arch_check 계층 맵 등재: utils↔prompts 사이)로 이동. `tools/binding.py`·`tools/catalog.py`의 nodes 역참조 소멸 → 지연 임포트 순환 해제. 노드 파일에는 오케스트레이션(`compile_from_nl`·`_select_smq_*`)만 잔존.
2. **패턴 C 조립 정리(D-067)**: `_compile_c`의 행 제한·세미콜론·ORDER BY(NULLS LAST 누락 정합 여부 실측 — 의도적 차이일 수 있음)·리터럴 처리를 P2-3 `sql_dialect` 소비로 교체. `CA` alias 무검증 가정은 카탈로그 joins 검증 추가.
3. **`build_multi_resource_pivot_sql` 파라미터 정리**: 폼필 경로(`metric_fields` 분류)와 시맨틱 경로(`explicit_measures`)를 진입 함수 2개로 분리하고 조립 코어 공유(17개 파라미터 → 경로별 필수만). 폼필 골든 13건 바이트 보존이 판정 기준.
4. **거대 함수 분해**: `query_generator`(258줄)를 트랙 분기 구조 그대로 `_prepare`/`_try_deterministic`/`_try_semantic`/`_llm_fallback`/`_instrument`로, `_generate_sql`(~434줄)을 P3 섹션 빌더 기반으로 분해. `synonym_usage` 계측의 단일 try(qg:445-486)를 독립 신호별 try로 분리(Known Mistakes 원칙).
5. **어댑터 직접 임포트 정리**: qg·mdb의 assembler 직접 임포트를 레지스트리(`get_adapter`) 훅 경유로 — 단 "두 번째 어댑터 전 추상화 확장 금지"(Plan 63 §9) 준수: 기존 훅 표면으로 충분한 것만 이동, 새 훅 신설은 하지 않음(부족分은 직접 임포트 잔존+사유 주석).
- **검증**: 공통 게이트 + 이동 전후 공개 심볼 재수출(호환 shim — 기존 테스트 무수정 그린 확인 후 임포트 경로 일괄 갱신) + 골든·sha256 전부 동일.

---

## 4. 성공 기준 (전건 리허설·grep 단언으로 자동 검증)

| 지표 | baseline (2026-07-30 실측) | 목표 |
|---|---|---|
| 바이트 동일 중복 블록 | 42줄 1건 + 준-동일 9건(§1.2) | **0건** (공유 빌더 grep 단언) |
| KBGenAI 판정 정의처 | 3형태 6곳 | **1곳** |
| 방언 분기 판정식 | 3형태 11곳+ | **1형태 1모듈**(sql_dialect) |
| 재시도 상한 리터럴 | 7곳 | **0곳**(config 단일 소비) |
| nodes↔tools 런타임 순환 | 2건(binding·catalog) | **0건**(지연 임포트 해제 후 arch_check 통과) |
| private 교차 임포트 | 4건 | **0건** |
| 죽은 코드(§1.6 확정분) | 8항목 | 소거(또는 사유 주석) |
| 80줄 초과 함수 | 11개(§1.4) | **≤4개**(validator `_validate_forbidden_joins` 등 응집 단일 책임은 허용) |
| 프롬프트 렌더(동작 불변 단계) | — | sha256 **전건 동일** |
| 폼필 피벗 골든 | 13건 | **바이트 보존** |
| 실결함 §1.1 | 10건(①~⑧ + ⑨·⑩ 편입 확정) | 0건(각 재현 테스트 동반, ⑨는 도달성 실증 테스트) |
| 멀티 경로 실패 감사 기록 | 미기록 | 기록(레코드 스키마 테스트) |
| validator 파싱 횟수/호출 | parse 2회·strip_comments 9회+·테이블 추출 3회 | **각 1회**(검증 결과 불변 전제) |

## 5. 리스크

1. ~~S3 충돌~~ → **해소(v2)** — S3·R3 커밋 완료로 소멸. 잔여 리스크는 Plan 67 잔여 작업(E1 평가·프롬프트 잔여 블록·D-132)이 착수 후 재개될 경우의 파일 겹침 — P0에서 착수 시점 워킹트리 확인 + Plan 67 잔여와 §1.1-⑨ 소관을 먼저 합의(§0.2-1).
2. **프롬프트 바이트 변화 → LLM 출력 분포 변화** — 완화: 동작 불변/변경 커밋 분리, sha256 골든, 변경분은 diff 전건 사유+옵트인.
3. **멀티 검증 강화의 위양성 거부** — 기존 통과 SQL이 어댑터 훅에 걸릴 수 있음. 완화: 옵트인+거부 사유 계측 후 기본 전환 별도 판단(§0.2-4).
4. **이동 리팩토링의 테스트·임포트 파손** — D-067 드리프트 가드가 심볼 위치 단언 가능. 완화: 재수출 shim 선적용→전체 그린 확인→경로 일괄 갱신(repo 전체 grep — Known Mistakes 원칙), worktree 격리 기준선 대조.
5. **비대칭의 일부는 의도된 차이** — 한글 alias(폼필 헤더 매칭)·컴파일 우회 등. 완화: 갭별 "의도 여부 실측 판정" 단계를 P3-2에 명시, 통일 강행 금지.
6. **`_ALL_QUERY_LIMIT` 하향 시 폼필 대량 조회 회귀** — §0.2-5 사용자 결정 전 착수 금지.

## 6. 결정 등재 — 완료

- **D-134 등재 완료(2026-08-04)**: `docs/02_decision.md`에 `## D-134` 본문(§D-133~D-135 사이)과 「변경 이력」 표 최상단 행으로 등재. 등재 직전 재확인 결과 등재 최댓값 D-136·D-134 결번 상태 확인 후 예약분 회수.

## 7. 참조

- 분석 원천: 본 문서 §1 전수 실측(2026-07-30, Explore 에이전트 3방향 — query_generator 계열 / semantic_compiler·조립 경로 / validator·executor·배선·테스트)
- 코드: §0 대상 파일 목록 · `src/security/{sql_guard,audit_logger}.py` · `src/db_adapters/polestar/{adapter,validators}.py` · `src/tools/{binding,catalog,validation,metrics,interpretation}.py` · `src/orchestration/subagents.py`
- 문서: `plans/67-stepwise-llm-query-composition.md`(§1.2 경직성 — 기능 트랙과의 경계), `plans/63`(3계층·overfit), `docs/02_decision.md`(D-035·066·067·076·088·089·099·128·131), `docs/18_known_mistakes.md`(비대칭·초기화·일괄 갱신·worktree 기준선 원칙), `spec.md:721`(행 수 상한 — §0.2-5 모순)
