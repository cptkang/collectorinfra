# 131. 카탈로그 v2 이관이 드러낸 제품 오답 — 폼필 값·대상 계약 · 멀티 DB 존 대조 · 집계 의미 가드 (`plans/122` §14.4 이관)

> **작성일**: 2026-09-30
> **상태**: **계획 · 구현 0건 · 게이트 G-1~G-7 사용자 확정 대기(§5)** — 파일명 `-TODO`
> **요청(사용자 확정 · 팀 리드 경유 2026-09-30)**: `plans/122` §14.8 ⑤ 「§14.4 드러난 오답의 소관」에 대한 답 *"전부 신규 계획 하나로"*. 원 번호 지정은 130이었으나 작성 직전 실측에서 130을 다른 세션(`plans/130-WIP-apm-instance-and-business-name-targeting.md`)이 먼저 써 **131**로 썼다.
> **범위 규칙**: 이미 다른 계획이 처리했거나 처리 중인 오답은 **중복 구현하지 않고** 처리 주체·상태로 연결만 한다(§2). 이 계획이 직접 맡는 것은 §3의 신규 트랙뿐이다.
> **관련 결정**: D-147(미작성 항목 공란+사유) · D-148(서버명·호스트명) · D-149(폼필 결정적 조립 · 채움 제외) · D-151(폼필 역질문) · D-152(용량·비고 규칙) · D-203(순차 의존 · 선행 스코프 분할) · D-066 후속6(동일 스키마 SQL 재사용) · D-089(DB 특화 격리) · D-162(신규 동작 기본 off) · D-216(자동응답) · D-246(113 존 종합) · D-255(매뉴얼) · D-268(119) · D-269(120) · D-279·D-280(123) · D-289(카탈로그 10건 제한)
> **D-번호**: 신규 없음 — 이 계획으로 모은다는 결정은 **D-291**(2026-09-30 · `plans/122` 사용자 확정 반영)에 기록했다. 게이트가 확정되면 등재 직전 3곳 grep으로 채번한다.

---

## 0. 증거 규칙

- **과금 호출 0 · 서버 기동 0 · DB 호출 0 · 저장소 쓰기 0**(조사 단계). 읽기 전용 에이전트가 과거 run 산출물을 판독하고, HEAD 함수를 스크래치 스크립트로 직접 불러 재현했다(LLM 0 · DB 0).
- run: `results/scenario/20260922-093837` · `results/scenario/20260923-103638` · `results/bench/run-closed-20260928-triage.tar/20260923-140539`(2단·3단). **세 run 모두 119·120 교정(`2e635a9`)과 123 W 트랙(`f4501b2`) 이전 코드**다.
- 기준 코드: HEAD `cce30f3`. `git merge-base --is-ancestor` 확인 — `2e635a9`(119·120)·`048c2be`(113)는 재측정 빌드 `f51cd49`(D-280 ⑭)에 들어 있고, `f4501b2`(123 W)는 없다(첫 실측 R5).
- 행 원문·PII는 싣지 않는다(개수·형태만).

## 1. 결론

| 항목 | 원인(한 줄) | HEAD 상태 | 소유 |
|---|---|---|---|
| H-04 서버명 = 호스트명 2,337행 | 폐쇄망 Redis EAV 유사어 `서버명→Hostname` 정확 매칭 → SQL 교정은 사본에만 → 결과 키 해석 Layer 2 LLM이 `서버명→호스트명` | **교정됨**(119 Q-4 · D-268 ⑥) — 실 run 미확인 · 잔여 가드 2건 | 119(확인) · 잔여 **131 FV-2** |
| H-10 TPMC = 메모리 용량 · 월 12열 공란 | 월 시리즈 미발동(120 F-1 사슬) → D-152 ① 용량 규칙이 `if month_series:` 안이라 미적용 | **교정됨**(120 F-1·F-3·F-6 · D-269) — 실 run 미확인 | 120(확인) |
| H-16 도입일자 = epoch 밀리초 | Step 2.8 LLM 유사어 `도입일자→cmm_resource.ctime`(BIGINT epoch ms) · D-149 채움 제외가 `llm_inferred`만 봄 · 작성기에 날짜 변환 없음 | **미교정**(재현) | **131 FV-1** |
| H-20 docx 1,690개 서버 · 자리표 첫 서버 | 폼필 결정적 피벗에 대상 한정 인자 없음 · Word 자리표가 여러 행이면 말없이 `rows[0]` | **미교정**(재현) | **131 FT** |
| F-02(0922) db_id 표지 | 113 S-2 이전 코드 | **교정됨**(113 S-2 · D-246) | 113(연결) |
| F-07 두 존 미대조 | ⓐ 행 단위 결과의 존별 대조 수단 없음 ⓑ 선행 스코프 IN 목록이 동일 스키마 SQL 재사용으로 다른 DB에 샌다 ⓒ 타임아웃 | ⓐⓑ **미교정** · ⓒ 119 T 랜딩(실 run 미확인) | **131 MZ**(ⓐⓑ) · 119(ⓒ) |
| K-10 사용률 3종을 한 평균 | LLM SQL이 CPU·메모리·파일시스템 Utilization을 한 AVG로 섞음 · 막는 검증기 없음 | **미교정** | **131 AG** |
| J-01 사유 없는 강등 | 2단 실패가 「데이터 없음」으로 끝남 | **교정됨**(123 W-6 · D-279) · 3단 산문 거절 잔여 | 123(확인) · 3단 103 |
| G-03 첫 턴 지시어 → 전 서버 | 첫 턴 지시어 되묻기 게이트 없음 | **미교정**(트리거 키만 · 소비처 0) | 106 H1(D-280 ⑥) |
| K-09 시간 단위 → 월 통계 | 시간 입도·기간 해석 오류 | **미교정**(122 T 해석기 미배선) | 122 T · 절단 문구 123 W-1 |
| D-05(3단) 알람 대신 stat_d | 3단 허용 테이블에 알람 없음 · **이 오답 SQL이 종전 D-05 단언을 통과했다(거짓 합격)** | **미교정**(3단) · 거짓 합격은 **차단 완료**(카탈로그 D-05 `(?i)\bcmm_alarm` · 122 v7 · D-291) | 103 §4.2 T3-1·T3-10 |
| 2단 절단 고지 0건 | `_build_output_state` 허용목록이 `query_attempts` 등을 떨어뜨림 | **교정됨**(123 W-1 · D-279) · 첫 실측 R5 | 123(확인) |

→ 131이 직접 맡는 것: **FV-1(H-16) · FV-2(H-04 잔여) · FT(H-20) · MZ(F-07 ⓐⓑ) · AG(K-10)** 과 조사 중 드러난 부수 결함(§3.5).

> **(v1.1 · 팀 리드 지시 2026-09-30) 조사 중 추가 발견 2건 — 따로 짚는다**
> 1. **D-05 3단 거짓 합격** — 3단이 알람 대신 성능 통계(`cmm_metric_stat_d`)를 조회한 오답 SQL이 종전 D-05 단언(`resource_type`·`server.Cpus`)을 통과했다(0923·벤치 3단 실측). 카탈로그 D-05에 `(?i)\bcmm_alarm`을 더해 거짓 합격은 막았고(D-03 U-6 선례 · D-291), 3단 교정 자체는 `plans/103` T3-10 소유다 → §2 D-05 행.
> 2. **`composite.prior_scope_by_db_enabled` 실효값 False** — 벤치 설정 스냅샷 4벌의 baseline·전 arm이 False다(`results/bench/run-closed-triage.tar/20260922-112010`·`20260922-162132` · `results/bench/run-closed-20260928-triage.tar/20260923-140539` · `results/run-closed-seg1-triage.tar/20260922-112010` 각 `config_snapshot.json` `…effective.composite.prior_scope_by_db_enabled`). 코드 기본값(`src/config.py:1235`)·`.env.example:452`·로컬 개발 `.env:476`은 모두 true다. D-203 선행 스코프 DB별 분할이 운영에서 꺼져 있을 수 있다 — F-07 ⓑ(MZ-2)의 판정 전제가 달라진다 → §3.3 MZ-3 · G-5(사용자 폐쇄망 `.env` 확인). 시나리오 run에는 설정 스냅샷이 없어 0923 scenario run의 실효값은 측정하지 못했다.

## 2. 연결만 하는 항목 (중복 구현 금지)

| 항목 | 처리 주체 | 상태 · 확인 수단 | 131이 할 일 |
|---|---|---|---|
| H-04(주 교정) | `plans/119` Q-4 `match_field_name_key`(`src/utils/column_matcher.py:163-189`) | 코드·단위 테스트(`tests/test_nodes/test_plan119_mapping_exact.py`) · 재측정 R1/R2(`f51cd49` 포함) | R2 산출물에서 `columns_differ` 합격 확인. 잔여 가드는 FV-2 |
| H-10 | `plans/120` F-1·F-3·F-4 ⓓ·F-6 | `TestF7CapacityRuleGateEvidence` · R1/R2 | 확인만. D-269 주의 ⑥(월 열 없는 양식의 `처리능력|(비GB)`) 잔여는 120 |
| F-02 | `plans/113` S-2 | 0923·벤치 run에서 교정 확인 | 없음 |
| F-07 ⓒ | `plans/119` T(마감 전파 · D-268) | 실 run 미확인 | 없음 |
| J-01 | `plans/123` W-6(`result_aggregator.py` `_failure_disclosure` · `src/domain/disclosure.py` `failure_text`) | R5 첫 실측 · 카탈로그 `_any`에 새 문구 병기됨 | 3단 산문 거절은 `plans/103` |
| G-03 | `plans/106` H1(게이트) · 방침 D-280 ⑥(되묻기) | 트리거 키 `demonstrative_without_antecedent`만 있음(소비처 0) | **순서 주의 통지**: 0923에서는 존 역질문이 먼저 떴다 — 되묻기 게이트가 존 역질문보다 앞서야 한다. 카탈로그는 `sql_must_not_match select`로 오답을 이미 불합격 처리 |
| K-09 | `plans/122` 트랙 T(T-4 배선 · R3′/R4) · 절단 문구 `plans/123` W-1 | T 모듈 미배선 | 없음 — T-4 전까지 불합격이 정상 |
| D-05(3단) | `plans/103` §4.2 T3-1 | 미교정 · T3-1 증거 목록에 D-05 없음 103 장부 T3-10으로 부기 완료(2026-09-30) · **거짓 합격 위험**: 현 D-05 단언(`resource_type`·`server.Cpus`)을 3단 오답 SQL(stat_d)이 통과했다(실측) → `testdata/scenarios/d_alarm.yaml` D-05에 `(?i)\bcmm_alarm` 추가 완료(2026-09-30 · D-03 U-6 선례) |
| 2단 절단 고지 | `plans/123` W-1 | R5 첫 실측 | 없음 |

## 3. 신규 트랙

### 3.1 FV — 폼필 값 계약

| ID | 내용 | 원인 위치(HEAD) | verify |
|---|---|---|---|
| **FV-1** | **H-16 — 감사 타임스탬프 열(ctime·mtime·dtime)을 일자형 필드에 채우지 않는다.** ① D-149 폼필 채움 제외를 `llm_synonym` 출처까지 넓힌다(D-149 부기) ② Step 2.8 즉시 등록에 120 F-4 쓰기 가드를 적용하고 「감사 타임스탬프 컬럼 ↔ 일자형 필드」 유형을 더한다 ③ epoch 값 처리(G-1): 변환 또는 채움 거부 + `[미작성 항목]` 사유 | `src/document/field_mapper.py:1028` `_apply_llm_synonym_discovery`(출처 `llm_synonym` · 즉시 Redis 등록 :1305) · `src/nodes/query_generator.py:292-294` · `src/nodes/multi_db_executor.py:2278`(제외는 `llm_inferred`만) · `assembler.py:411` `filter_pivot_regular_entries`(실존 컬럼 통과) · `src/document/excel_writer.py:28-52`(날짜 변환 없음) | `_try_build_form_fill_pivot_sql` 순수 함수 테스트(출처 `llm_synonym`·`synonym`에서 `c.ctime` 미포함) · writer epoch 행 1개 · 폐쇄망 `audit`로 등록분 확인(사용자) |
| **FV-2** | **H-04 잔여 가드** — ① `_apply_eav_synonym_mapping` 정확 일치 본문에 `is_servername_to_hostname` 가드(Pass 3·4·등록 경로와 같게) ② D-152 ④ 교정 결과를 `mapping_updates`에도 싣는다(단일·멀티 대칭 — 지금은 SQL 사본에만 적용) ③ 120 F-5 `audit` 규칙에 「서버명 ↔ Hostname」 유형 추가 | `src/document/field_mapper.py:966`(:987-1024) · `src/utils/query_gen_common.py:1222-1239` · `src/nodes/query_generator.py:281-283` · `src/nodes/multi_db_executor.py:2213` | `eav_name_synonyms={"Hostname":["서버명"]}` 순수 함수 테스트 · `mapping_updates` 단언 · 픽스처 `testdata/scenarios/fixtures/adhoc_server_info.xlsx` |
| FV-3 | **(부수) Step 2.8 LLM 유사어가 폼필 턴마다 Redis에 쌓인다** — F-4 4유형 밖이라 등록이 막히지 않는다(오염 자기강화 루프 · Known Mistakes 「LLM 자동 등록」) | 같음(:1305) | 폐쇄망 등록 로그 집계(사용자) · 등록 가드 단위 테스트 |

### 3.2 FT — 폼필 대상 한정

| ID | 내용 | 원인 위치(HEAD) | verify |
|---|---|---|---|
| **FT-1** | **H-20 — 폼필 피벗에 대상 한정(`server_scope`)을 전달한다(단일·멀티 대칭).** 식별자 추출은 `src/domain/empty_answer.py` `identifier_only_values` 재사용 후보 · 매칭 규칙은 123 S-4a `entity_probe`와 같게(이름·호스트명·IP · 대소문자 무시) | `src/db_adapters/polestar/assembler.py:1134-1177` `build_form_fill_pivot_sql`(인자 없음 — 같은 파일 `build_semantic_pivot_sql`은 `server_scope` 받음 :1192) · 호출 `src/nodes/query_generator.py:248`(:433) · `src/nodes/multi_db_executor.py:2135` | `tests/e2e/fixtures/sample_template.docx` + `parsed_requirements.filter_conditions` → WHERE에 대상 · 1행 |
| **FT-2** | **Word 자리표 — 채울 행이 2개 이상이면 고지**(말없이 첫 행 금지 · 침묵 폴백 금지) | `src/document/word_writer.py:57` `fill_row = single_row or rows[0]` | `fill_word_template(rows=[r1, r2])` → 고지 |
| FT-3 | **(부수) 집계어 없는 사용률 필드(「CPU사용률」)의 미작성 사유가 사실과 다르다**(「수집 데이터에 없음」) — 사유 문구 교정 또는 기본 집계 정책(G-3) | `assembler.py:127-140` `classify_metric_field`(명사 + 집계어 모두 요구) | 순수 함수 테스트 |
| FT-4 | **(부수) 응답과 산출물 불일치** — 응답은 「레코드가 조회되지 않았다」인데 파일에는 대상이 있다(재계획기가 표본만 보고 단정) | 121 TP-4.2·4.4(표본 외삽)와 같은 부류 | 121 연결 — 131에서 구현하지 않는다 |

### 3.3 MZ — 멀티 DB 존 대조

| ID | 내용 | 원인 위치(HEAD) | verify |
|---|---|---|---|
| **MZ-1** | **F-07 ⓐ — 행 단위 결과의 존별 결정적 요약**(`_source_db`별 건수·최소·최대·행 평균) · 「서버 평균의 평균」임을 문구로 밝힌다 | `src/nodes/result_merger.py:723` `plan_aggregate_synthesis`(113 S-3 — DB마다 1행 스칼라만) · `src/nodes/output_generator.py:605-642` `_numeric_summary_lines`(전체 한 벌) | `tests/test_nodes/test_merge_ranking.py` 틀 재사용 |
| **MZ-2** | **F-07 ⓑ — 선행 스코프 리터럴이 실린 SQL은 동일 스키마 재사용에서 뺀다**(D-203 분할 × D-066 후속6 재사용 충돌) | `src/nodes/multi_db_executor.py:832-838`(재사용) · `:376` `_prior_for_db` 우회(코드 판독 — 추정) | `tests/test_nodes/test_prior_scope_by_db.py` `_run`·`TAGGED`로 gp 스코프 SQL → yd 재사용 금지 단언 |
| MZ-3 | **운영 실효값 확인**: 벤치 설정 스냅샷에서 `composite.prior_scope_by_db_enabled=False`(코드 기본값·`.env.example`은 true) — 폐쇄망 `.env`에서 끈 것으로 추정 | 벤치 `config_snapshot.json` 4벌(§1 추가 발견 2 — 경로 전부) · 코드 기본값 `src/config.py:1235` | 사용자 확인(G-5) |

### 3.4 AG — 집계 의미 가드

| ID | 내용 | 원인 위치(HEAD) | verify |
|---|---|---|---|
| **AG-1** | **K-10 — Utilization 값 집계가 2개 이상 resource_type을 타입별 구분(CASE) 없이 한 AVG/MAX로 묶으면 반려 · 재생성**(폴스타 검증기 1종 · `validators.py` · D-089) | `src/db_adapters/polestar/adapter.py:94-106`(검증기 11종) · `validators.py`(해당 규칙 없음) · 촉진 요인(추정): few-shot `src/db_adapters/polestar/prompts.py:224` · `config/db_profiles/polestar_cm_gp.yaml:582`의 `resource_type IN (4종)` | 0923 raw K-10 `executed_sqls[0].sql`로 반려 단언 · few-shot 원문(타입별 CASE)은 통과(반례) |
| AG-2 | **K-10 시나리오 재설계** — 심은 오매핑 유사어를 LLM이 무시해 「재시도 소진」을 더는 유도하지 못한다 | 카탈로그 `k_load.yaml` K-10 | 122 카탈로그 몫(연결) |

### 3.5 조사 중 드러난 부수 결함(다른 소유 — 통지)

- 123 S-8 자기 고백 어휘(`src/domain/sql_disclosure.py:91` 「스키마에 없」)가 3단 D-05 SQL 주석 「스키마에 존재하지 않으므로 … 제외」를 잡지 못한다 → 123 통지.
- 벤치 3단 F-02 t2 두 존 모두 0행 「데이터 없음」 → 103 통지.

## 4. 착수 순서 · 재측정

- 131은 **제품 변경**이다. 재측정 빌드 R2~R4(`f51cd49` 고정 · D-280 ⑭)에 섞지 않는다 — 123 run R5 뒤 별도 run(R8 가칭 · 한 run 한 트랙 · D-251 ⑦). 125의 R7(D-285 ②)과도 겹치지 않게 순서를 사용자가 정한다(G-7).
- 순서 초안: FV-2 · FT-2 · AG-1(단위 테스트로 닫히는 교정 · 결정 필요 적음) → FV-1 · FT-1 · FT-3(G-1~G-3) → MZ-1 · MZ-2(G-4·G-5) → FV-3.
- 품질 게이트: `arch_check --ci` · `overfit_check --ci`(폴스타 특화는 `src/db_adapters/polestar/`에만 — D-089) · 영향 영역 pytest · 사용자 응답이 바뀌는 FT-2·FT-3·MZ-1은 D-255 매뉴얼 판정.

## 5. 게이트 초안

| # | 물음 | 선택지 | 권고 |
|---|---|---|---|
| G-1 | H-16 epoch 값 | (a) 채우지 않고 `[미작성 항목]` 사유(D-147 정답 정의 — 도입일자는 수집 항목 없음) (b) 날짜로 변환해 채움 | **(a)** — D-147이 도입일자를 「수집 항목 없음 → 공란+사유」로 정했고 `ctime`은 자원 등록 시각이지 도입일자가 아니다 |
| G-2 | H-20 대상 한정 신호 | (a) 파서 `filter_conditions`·식별자만(LLM 0) (b) 원문 식별자 정규식 | **(a)** — D-004 · 123 S-4a와 같은 규칙 |
| G-3 | 집계어 없는 사용률 필드 | (a) 사유 문구만 교정(공란 유지) (b) 기본 집계(평균) 적용 + 고지 | **(a)** 먼저 — 기본 집계는 양식 의미 결정이라 사용자 확정 뒤 |
| G-4 | F-07 존별 요약 형식 | (a) `_source_db`별 결정적 수치 요약 줄 (b) 존별 표 분리 | (a) — 113 `[존별 결과]` 형식 확장 |
| G-5 | `prior_scope_by_db_enabled` 운영값 | 사용자 폐쇄망 `.env` 확인 | — |
| G-6 | AG-1 도입 방식 | (a) 결함 교정 · 기본 on(D-162 예외) (b) 섀도(경고 로그) → 측정 → on | **(b)** — 반려가 재생성을 부른다(LLM +1) · 오탐률을 먼저 잰다 |
| G-7 | 131 run 순서 | 123 R5 · 125 R7과의 순서 | 사용자 |

## 6. 위험

| 위험 | 대응 |
|---|---|
| FV-1 제외 확대가 정상 매핑까지 뺀다 | 출처 `llm_synonym` + 감사 타임스탬프 유형에 한정 · 기존 폼필 테스트 불변 |
| FT-1 대상 한정이 「전체 서버」 양식까지 좁힌다 | 식별자가 있을 때만 · 없으면 종전 전량(D-149) |
| AG-1 오탐(정상 타입별 CASE 집계 반려) | 섀도 먼저(G-6) · few-shot 원문을 반례 테스트로 고정 |
| 폐쇄망 Redis 오염이 코드 교정 뒤에도 남는다 | 120 F-5 `audit`·prune(사용자 작업) |

## 7. 측정 못 한 것

- HEAD 교정(119 Q-4 · 120 F-1/F-3/F-6 · 123 W-1/W-6)의 실 run 효과 — 교정 뒤 run 없음.
- 폐쇄망 Redis 오염 실태(`서버명`→Hostname · `처리능력|(TPMC)`→TotalSize · `도입일자`→ctime).
- Step 2.8 등록본이 다음 run에서 `synonym`으로 정확 매칭되는지(등록·조회 저장소가 다를 수 있다 — 추정).
- H-20 파서 `filter_conditions`에 대상 서버가 실렸는지(체크포인트 미디코드).
- 폐쇄망 `cmm_resource.ctime`의 형(샌드박스 DDL은 BIGINT).
- F-07 ⓑ가 분할 on 상태에서 실제 재현되는지(코드 판독만).

## 8. 매뉴얼(D-255)

계획 단계라 대상 없음. 구현 때 FT-2(자리표 고지)·FT-3(사유 문구)·MZ-1(존별 요약 줄)은 사용자 응답이 바뀌므로 매뉴얼을 함께 판정한다.

---

## 변경 이력

| 날짜 | 버전 | 내용 |
|---|---|---|
| 2026-09-30 | v1 | 신설 — `plans/122` §14.4 오답 11행을 모은다(사용자 확정 「전부 신규 계획 하나로」 · D-291). 읽기 전용 원인 실측(과거 run 3개 · HEAD 함수 재현 · LLM 0 · DB 0) · 연결 9건 · 신규 트랙 FV·FT·MZ·AG · 게이트 G-1~G-7 초안. 번호는 130 선점(다른 세션)으로 131. 코드 0 |
| 2026-09-30 | v1.1 | 팀 리드 지시 — 조사 중 추가 발견 2건(D-05 3단 거짓 합격 · `prior_scope_by_db_enabled` 실효 False)을 §1에 따로 명시 · D-05 행 상태 갱신(거짓 합격 차단 완료) · MZ-3 증거 경로 4벌 실측 기재. 코드 0 |
