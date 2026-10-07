# 143. ITAM 지식 자산 폴스타 동등화 — 폴스타 자산 전수 대응표 · 반출 근거로 Claude Code가 자산을 쓰고 외부망에서 바로 커밋 · ITAM 결정적 조립(데이터 템플릿) · 벤치 폐루프로 효과 판정

> **작성일**: 2026-10-07 · **v1.1**(게이트 답 반영)
> **상태**: **WIP — 외부망 구현 완료(2026-10-07 · 작업 트리 · 커밋 없음)** · 완료 W1·W2·W3(첫 원천 K1·K4·K3 — 정적 검증만)·W4·W5·W6·W7·W8 코드·W9 · **잔여(내부망)**: W0 폴스타 기준선 · 2·3회차 반출 · 모의 DB(3308) 상대 `--validate-knowledge` 실행 검증 · `--verify-assets` · K2·K8 원천(2회차 뒤) · W8 효과 판정·절제 run · 착수 조건 `plans/140` 커밋은 충족(`b1fabf4`에 포함 — 메인 세션 확인) — 상세 D-316 「구현」 부기
> **요청(사용자 2026-10-07)**: *"폴스타 db와 관련되어 정리되어 있는 정보들(config/db_prfiles/polestar_b0.yaml, knowledge/catalog.yaml, semantic_model/polestar_b0.yaml, synonym/polestar_b0.yaml 등)을 모두 리스트업하고 itam db에 맞게 생성하기 위한 정보를 itam_bench를 구동시 수집하고 저장된 로그를 외부망으로 반출하여 llm에서 자동으로 생성할 수 있게 해야 한다. 또는 관련 정보를 관리자 페이지의 DB 구조 탭에서 생성할 수 있도록 하여 itam의 조회성능을 polestar의 조회성능 방큼 높일려고 한다. 이 요건에 맞게 계획을 수립하라."*
> **게이트 답(2026-10-07)**: G-1 *외부망에서 바로 커밋* · G-2/G-3 *"claude code 내에서 돌린다."* · G-4 *폴스타 기준선과 비교* · G-5 *이번에 포함*
> **입력**: 저장소 `b1fabf4` 실측(서브에이전트 2갈래 — 폴스타 자산 전수 · 기존 생성 경로) · 1회차 반출 `results/itam_bench/20261006-152938/`
> **관련 계획**: `plans/140`(외부망 빌더 · P1 근거 반출 — **선행**) · `plans/139`(테이블 정의·선별) · `plans/133`(「DB 구조」 탭 P1·P2 — 내부망 경로는 그대로 둔다) · `plans/135`(ITAM 벤치·반출 · 부록 A v1.5 = 2회차 반출 형식) · `plans/132`(소스 선별·되물음) · `plans/67`(지식 카탈로그 R1)
> **관련 결정**: **D-311 ③ 부분 개정**(G-1) · **D-294**(G-1 Python 코드 생성 기각 유지 — 템플릿은 데이터) · **D-301 ②③**(값 반출 금지 · 벤치 DB 조회 0 — 검증 모드는 부기) · D-227(질의 경로 읽기만) · D-308(정의 선별 · 존재 기반 발동) · D-142(유사어 오염 차단) · D-133(질의 이력) · D-066(단일·멀티 대칭) · D-004(LLM 출력은 정합성 근거 아님) · D-255(매뉴얼) · D-303(회귀) · D-312(스킬 형식)
> **D-번호**: **D-316**(`docs/decisions/D-316.md` · 확정 · 외부망 구현 완료)

---

## 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0. 이 계획서의 수치는 저장소 파일·코드 읽기와 1회차 반출물 기준이다.
- 내부망 ITAM의 실제 값·주석·기본키는 모른다(1회차 반출은 이름·타입·NULL·조회 대상뿐 — `plans/140` §1.1). 2회차 반출이 오면 §1 표의 ITAM 칸을 다시 잰다.
- 「성능 기여」 순위는 소비 위치로 판단한 **추정**이다 — 실제 기여는 W8 자산별 켜고 끄기로 확정한다.

## 1. 폴스타 정보 자산 전수 목록과 ITAM 현황

### 1.1 설정 파일 자산

| # | 자산 | 폴스타 위치 · 규모 | 생성 방식 | 소비처 | ITAM 현황 | 성능 기여(추정) |
|---|---|---|---|---|---|---|
| A1 | `query_guide` | `config/db_profiles/polestar*.yaml` · b0 8,993자 · gp/yd/polestar 약 7.7K자 — 스키마 한정·서버명/호스트명 구분·LIKE 금지·통계 입도 규칙 | 수동 | `query_generator.py:188`(단일) · `multi_db_executor.py:1978`(멀티) | **없음**(HEAD) · 구 샌드박스판은 LLM 「JOIN 관계 부재」 오안내 | **상** — 모든 LLM 경로 공통 주입 |
| A2 | `query_examples`(few-shot) | 같은 파일 · b0 8건 · 그 밖 12건(질문·SQL·설명) | 수동 | `prompt_blocks.build_query_examples` → 단일 `:233`·멀티 `:2002` · 질의 이력 시드 | **없음** · P2 LLM 생성 경로는 있음(`run_asset_llm` · 빌더 `--p2`) | **상** |
| A3 | `patterns`(EAV·계층) | 같은 파일 614~659줄 · `known_attributes` 28 · `value_joins`·`excluded_join_columns` | 수동 | `schema_analyzer.py:363` · `prompt_blocks` · `sql_validation.py:939` · `assembler` · `value_index` | 없음 — ITAM은 EAV 아님 | 해당 없음 |
| A4 | `allowed_tables` | 5개 | 수동 | `schema_analyzer`·`table_selection`·멀티 게이트 | **있음** 98개(빌더) | 상(범위) |
| A5 | `alarm_allowed_tables` | 12개 | 수동 | 알람 경로 | 해당 없음 | — |
| A6 | `column_synonyms`(프로필) | 2건 | 수동 | `cache_manager.load_synonyms_with_global_fallback` | 없음 | 중 |
| A7 | `entity_keys` | hostname(casefold)·ip | 수동 | `key_bridge`·`entity_locator`(교차 시스템) | 없음 · 픽스처만(`testdata/itam/entity_keys.local.yaml`) | 중(교차 질의) |
| A8 | `relationships`·`query_rules`·`code_values`·`code_labels` | 폴스타도 없음(D-294 자산 키) | P1 결정적(내부망) | `build_profile_rules_block` 단일 `:228`·멀티 `:2001` · `table_selection` 다리 테이블 | 없음 — 1회차 근거 0 · 2회차 반출 대기(`plans/140`) | **상**(ITAM: 조인·코드 의미) |
| A9 | `table_definitions` | 폴스타 없음 | 시드·가져오기·주석·LLM 초안 → 승인(D-308) | 선별·「테이블 용도」 블록 | **있음** 108(origin `import` · 「(추정)」 14) | 상(ITAM 전용) |
| A10 | 지식 카탈로그 | `config/knowledge/_base/catalog.yaml` + `polestar_b0` 오버레이 · `pattern_a/b/c`·`taxonomy`·`measures` | 수동(plans/67 R1) | `catalog_builder` → `semantic_compiler.load_semantic_model` · 폴스타 프롬프트 렌더 | **없음** — 틀 자체가 EAV·지표·알람 전제 | 상(폴스타 결정적 경로) |
| A11 | 시맨틱 모델 | `config/semantic_models/polestar*.yaml` 약 9.5KB — R1 이후 **폴백 사본** | 수동 → 사본 | 정본 생성 실패 시만 | 없음 | (A10 종속) |
| A12 | 유사어 시드 | `config/synonym_seeds/polestar*.yaml` · `column_synonyms`·`eav_names`·`column_values` | 스크립트 파생(`scripts/synonym_seeds.py derive`) | `SynonymLoader.load_seed_yaml` → Redis | **없음** · 빌더는 주석 근거 있을 때만 | 중 |
| A13 | DB 전용 규칙 섹션 | 폴스타는 어댑터 코드(B1)가 대신함 | P2 LLM(D-294 ③) | `GeneratedTemplateAdapter`(`src/db_adapters/generated.py`) ← `config/knowledge/{db}/prompt_template.yaml` | **없음** — 파일이 없어 미발동 | 상(ITAM의 B1 대체) |
| A14 | DB 레지스트리 | `config/db_registry.yaml` 709~860 · family·zone·engine·schema·aliases | 수동 | 라우팅·엔진·스키마 주입 | **있음**(engine mariadb · `allow_hangul_identifiers` · capabilities 4종) | 상(라우팅) |
| A15 | 글로벌 유사어 | `config/global_synonyms.yaml` 303줄 | 수동 | `schema_analyzer`·`field_mapper` 폴백 | 사실상 폴스타 전용 — 한글 컬럼과 안 맞음 | 하 |
| A16 | 변화 어휘 | `config/change_terms.yaml` | 수동 | 급증 조립(폴스타 어댑터 게이트) | 해당 없음 | — |
| A17 | 기타 | `query_target_surfaces`·`source_memory_seeds`(itam 2건)·`middleware_signatures`(text2sql 무관) | 수동 | 라우팅 | 라우팅 수준만 있음 | 하 |

### 1.2 코드에 박힌 폴스타 지식 (`src/db_adapters/polestar/` — ITAM 이식 불가 · D-294 G-1)

| # | 자산 | 하는 일 | ITAM 대체 |
|---|---|---|---|
| B1 | 전용 시스템 템플릿 `prompts.py`(1,421줄) | 지표 카탈로그·EAV 속성·알람 조인 렌더 | **A13 섹션**(데이터 파일 · 범용 어댑터) |
| B2 | 결정적 조립기 `assembler.py`(1,832줄) | EAV 피벗·폼필·월 시리즈·알람 SQL — LLM 우회 | 없음 → G-5 |
| B3 | 시맨틱 컴파일러(트랙 C) | SMQ → 결정적 SQL(카탈로그 A10 필요) | 없음 → G-5 |
| B4 | 검증기 14종 `validators.py` | 폴스타 특유 오류 → 재생성 회귀 | 범용 `validate_sql`만 → K6(데이터 기반 규칙) |
| B5 | 엔티티 탐침·급증 SQL·기간 투영 | 0건 진단·hostname 변환·급증 비교 | 해당 없음(교차 질의는 A7) |

### 1.3 런타임 저장소

| # | 자산 | 저장 | 생성 | ITAM 현황 |
|---|---|---|---|---|
| C1 | 컬럼 설명 | Redis `schema:{db}:descriptions` + `.cache/structure/{db}/descriptions.yaml` | LLM `DescriptionGenerator`(표본 행 사용) · 탭 O-4 | 내부망 Redis 698/698 실림(1회차 서버 상태) · 반출 파일 의미 0/1,988 · 품질 미확인 |
| C2 | LLM·운영자 유사어 | Redis | O-4 LLM · 운영자 | 반출은 건수만 |
| C3 | 값 인덱스 | Redis `column_value_index` | EAV `attribute` 컬럼만 | 빈 인덱스(EAV 아님) — 코드값은 A8 블록이 대신 |
| C4 | 질의 이력 few-shot | Redis `query_history:{db}` | 골드셋 + 프로필 예시 시드 · 사람 확인분(D-133) | 원천 없음 · 플래그 `query_history_fewshot` 기본 off |
| C5 | 구조 적용본 | Redis `structure_meta` · `versions/` | 관리자 승인 | 로컬 샌드박스판만 |

### 1.4 평가 자산

| 대상 | 자산 | 규모 |
|---|---|---|
| 폴스타 | `testdata/text2sql_gold/{gp,yd,b0}.yaml` · 샌드박스 골드 · `testdata/scenarios/*`(오라클 22) · `time_gold` | 26 + 51 + 시나리오 a~r |
| ITAM | `testdata/itam_bench/scenarios.yaml`(22) · `scenarios.closed.yaml`(18) · 오라클 `ITAM-*.mariadb.sql` 16 · `--check-oracle`(정답 SQL 읽기 전용 실행) | 1회차 SQL 관측 0턴(plans/139) |

## 2. 격차 분석

| 격차 | 원인 | 만들 수 있는 곳 |
|---|---|---|
| **값이 필요한 자산**(A8 코드값·라벨 · A12 `column_values` · A7·관계의 값 겹침 근거) | 값 반출 금지(D-301 ②) | **내부망 P1만** — `plans/140`이 근거를 반출해 빌더가 쓰는 길을 만들었다. 단, ITAM은 선언 기본키 0이라 공통코드 라벨 짝짓기가 빈다(`plans/140` §8) → K5 |
| **사람 지식이었던 자산**(A1 · A2 · A13 · C1 · A6/A12 유사어) | 폴스타는 사람이 썼다. ITAM은 쓴 사람이 없고, 생성기는 탭 P2(A2·A13)·O-4(C1)뿐 · 외부망 빌더에는 LLM 생성기가 없다 | **Claude Code 작성**(G-2/G-3) — 근거 = 구조 카탈로그 + 정의 + P1 근거 + **벤치 실패 증거**. 외부망에서 작성·검증·커밋(이 계획의 본체) |
| **코드 자산**(B1~B5) | 폴스타 의미론 하드코딩 · Python 생성 기각(D-294 G-1) | 데이터로 대체 — B1→A13 · B4→K6 · B2/B3→G-5 |
| **효과 근거** | 자산을 넣어도 무엇이 정답률을 올렸는지 모른다 · ITAM SQL 관측 0턴 | 벤치 폐루프(W1·W8) — 자산 사용 표지 · 자산별 켜고 끄기 |

**판단**: 폴스타 수준의 핵심은 「사람이 쓴 지식(A1·A2·B1)」 + 「결정적 경로(B2·B3)」 + 「검증기(B4)」다. ITAM은 소비 코드가 이미 범용이라(A1·A2·A8·A13·C1·C4) **값만 채우면 바로 효과가 나는 자산이 대부분**이다. 그래서 이 계획은 새 소비 코드보다 **작성 근거·검증·반입·효과 측정**과 결정적 조립(G-5) 하나에 집중한다.

## 3. 목표 · 비목표

**목표**
1. §1 자산마다 ITAM 대응 자산·작성 방법·검증 방법을 확정한다(§4.1 대응표).
2. **작성자 = Claude Code**(G-2/G-3): 반출 로그로 만든 **근거 묶음**을 Claude Code가 읽고, 자산별 출력 계약에 맞춰 **원천 파일**을 쓴다. 앱 안에서 LLM을 부르는 생성기는 만들지 않는다(과금·MLX 0).
3. **결정적 검증 → 빌더 → 바로 커밋**(G-1): 원천 파일은 검증 CLI를 통과해야만 빌더가 설정 파일로 옮긴다. 통과분은 외부망에서 바로 커밋하고 반입한다(내부망 승인 단계 없음 — D-311 ③ 개정).
4. **ITAM 결정적 조립**(G-5): 자주 쓰는 질의형을 **검증된 SQL 템플릿(데이터 파일)**으로 두고, 질문이 템플릿에 맞으면 코드가 SQL을 조립해 LLM SQL 생성을 우회한다(폴스타 B2·B3 대응 · Python 코드 생성 아님 — D-294 G-1 유지).
5. 벤치가 자산별 효과를 기록하고(자산 사용 표지 · 켜고 끄기), 내부망 실행 결과(값 없음)로 틀린 항목을 다음 사이클에 걷어낸다.
6. **성능 목표(G-4)**: 같은 내부망 환경(FabriX)에서 ①ITAM 벤치 SQL 관측 턴 정답률 ≥ 폴스타 하네스 정답률 ②단순 질의 지연 < 10s ③실패 분류 `wrong_table`·`wrong_column`·`non_sql` 감소. 폴스타 기준선은 W0에서 같은 환경으로 잰다.
7. 폴스타 자산·경로는 바이트 동일 — 새 자산은 ITAM 파일이 있을 때만 발동한다(D-308과 같은 존재 기반 발동).

**비목표**: Python 어댑터·조립기·검증기 코드 생성(D-294 G-1) · 값 반출(D-301 ②) · 앱 내 LLM 지식 생성기·「DB 구조」 탭 생성 진입점(G-2/G-3 — 탭의 기존 P1·P2는 내부망에서 그대로 쓸 수 있다) · 폴스타 자산 변경 · `eav_names`·값 인덱스(D-294 ⑧) · `plans/140` 범위(P1 근거 반출·빌더의 근거 자산) 재구현.

## 4. 설계

### 4.1 자산 대응표 — 폴스타 → ITAM

| K | ITAM 자산(대응 폴스타) | 작성 | 근거 입력(반출물) | 결정적 검증(검증 CLI) | 실행 검증 | 출력 위치 |
|---|---|---|---|---|---|---|
| **K1** | `query_guide`(A1) | Claude Code | 구조 카탈로그 · 정의(`kind`·`notes`) · P1 근거 · 실패 분류·가린 실패 SQL | 길이 ≤ 8,000자 · 중괄호·코드 펜스 금지 · 언급 식별자 실존 · 조회 대상 밖 테이블 언급 금지 · 사용률을 ITAM에서 답하라는 규칙 금지(D-308 G-6) | — | `itam.yaml`(빌더 경유) |
| **K2** | `query_examples`(A2) + 질의 이력 시드(C4) | Claude Code | 시나리오 질문 · 정의 · 관계 · 정답 SQL | `SQLGuard` · `validate_sql`(실존·한글 식별자) · 치환 리터럴 금지 | 모의 DB(외부망 · 커밋 전 필수) · 내부망 검증 모드(반입 뒤 · 실패분 다음 사이클 제거) | `itam.yaml` · 질의 이력 시드 |
| **K3** | 컬럼 설명(C1) · 유사어 시드(A12 `column_synonyms`) | Claude Code | 한글 컬럼 이름 · 정의 · DDL 주석(반출분) · 시나리오 어휘 | 설명 ≤ 200자 · 값 금지 · 유사어 쓰기 가드(다른 컬럼 이름과 충돌 · 다의어 · 2자 미만 거절 — D-142) | — | 설명 정본 파일(신설 · §4.5) · `config/synonym_seeds/itam.yaml` |
| **K4** | DB 전용 규칙 섹션(B1 대체 · A13) | Claude Code | K1과 같음 + 실패 분류별 반례 | D-294 ③ 섹션 검증(≤ 6,000자 · 펜스 SQL 전부 검사 · 블록 ≤ 5) | 모의 DB(섹션 안 SQL) | `config/knowledge/itam/prompt_template.yaml` |
| **K5** | 공통코드 라벨(A8 `code_labels`) | **P1 보강**(내부망 · LLM 0) | 정의 `kind: 기준·코드`(시드 15)를 공통코드 후보로 · 기본키 없이 코드/이름 컬럼 쌍 판정 | 값 덮음 ≥ 0.9(D-294 ②) | P1 자체 | 내부망 탭 승인(값이라 반출 불가 — 직접 커밋 대상 아님) |
| **K6** | 데이터 기반 쿼리 규칙(B4 대체 · `query_rules` 확장) | 정의 `kind`에서 결정적 파생 | `현행`(활성화여부) · `수집이력`(기준년월일 형식) · `수집적재`(선택 금지) | 규칙 형식 검사 | — | `itam.yaml`(빌더) |
| **K7** | `relationships`·`entity_keys`·`query_rules`(P1) | `plans/140` 그대로 | 2회차 P1 근거 | D-294 ② | — | 빌더(D-311 ③) |
| **K8** | **결정적 조립 템플릿**(B2·B3 대응) | Claude Code | 시나리오·정답 SQL·실패 분포에서 빈도 높은 질의형 | 템플릿 계약 검사(§4.6) · 슬롯 형식 · 조립 결과 `SQLGuard`·`validate_sql` | 모의 DB 전 슬롯 조합 실행 · 내부망 검증 모드 | `config/knowledge/itam/query_templates.yaml`(신설) |
| — | A3·A5·A15·A16·C3 | 만들지 않음 | EAV·알람·폴스타 어휘 전용 | — | — | — |

### 4.2 흐름 — 반출 → Claude Code 작성 → 검증 → 빌더 → 커밋 → 반입 → 재측정

```
[내부망 run N]  itam_bench --run --env closed → 반출 6파일(plans/140) + 신규: 자산 사용 표지(trace) · 검증 결과 파일(W5)
                                                   │ (값 0 · 누출 관문 통과)
[외부망]  ① --evidence <run_dir>  → 근거 묶음(knowledge_evidence/ · 자산별 입력 요약 · 결정적)
          ② Claude Code(스킬 itam-knowledge) → 원천 파일 testdata/itam_bench/closed/knowledge/*.yaml 작성
          ③ --validate-knowledge  → 결정적 검증 + 모의 DB 실행(K2·K4·K8) — 실패 항목은 사유와 함께 거절
          ④ --build-assets(plans/140 빌더 + 지식 오버레이) → itam.yaml · knowledge/itam/* · synonym_seeds/itam.yaml
          ⑤ 커밋(G-1 — 외부망에서 바로)
[내부망]  반입 → (검증 모드 run: K2·K4·K8 SQL 실행 결과만 기록) → run N+1 → 반출 → [외부망] W8 효과 판정 · 실패 항목 원천에서 제거
```

#### 4.2.1 실행 절차 — 따라 하는 명령어

> 저장소 루트에서 실행한다. `<run>`은 반출 run 디렉터리 이름(예: `20261006-152938`), `<run_N>`·`<run_N1>`은 연속한 두 회차다. **표지**: ✅ 지금 쓸 수 있음(v1.3 외부망 구현 기준 — 옵션 이름은 `python -m scripts.itam_bench --help`와 대조했다). 과금 API·`RUN_E2E=1`은 쓰지 않는다. 내부망 단계는 `plans/135` 부록 A(0~10)를 그대로 따르고, 아래는 143이 더하는 것만 적는다.

**A. 내부망 — run N (사용자 실행)**

| # | 할 일 | 명령 · 화면 | 확인 | 표지 |
|---|---|---|---|---|
| A0 | 외부망 커밋 반입 | 기존 반입 방식으로 저장소 갱신 — 반입분: `config/db_profiles/itam.yaml` · `config/knowledge/itam/*` · `config/synonym_seeds/itam.yaml` · `testdata/itam_bench/closed/knowledge/*` | 반입은 내부망 `itam.yaml`·시드를 덮는다(되돌리기: 「DB 구조」 탭 버전 기록) | ✅ |
| A1 | 본체·MCP 재기동 | 본체 `python -m src.main --server` · MCP `cd mcp_server && python -m mcp_server` | 설명 정본 파일은 스키마 첫 로드 때 Redis에 없는 컬럼만 채운다(§4.5) | ✅ |
| A2 | 「DB 구조」 탭 | `plans/135` 부록 A 1 → 2b(P1 · **필수**) → 2c(코드값·코드 라벨만 승인 — K5 공통코드 후보 포함) → 2a(정의 확인) | P1 초안이 있어야 반출에 근거가 실린다 | ✅ |
| A3 | 과금 평면 확인 | `python -m scripts.bench --show-env` | 워커 FabriX · 오케스트레이터 vllm 등 비과금 | ✅ |
| A4 | 반입 자산 검증 모드 | `python -m scripts.itam_bench --verify-assets --env closed` | 반입한 K2(`itam.yaml` 예시)·K4(섹션 펜스 SQL)·K8(템플릿 × 슬롯 대표값) SQL을 읽기 전용 실행 → `results/itam_bench/<run>/asset_verification.yaml`(항목 ID·지문·성공·오류 범주·행 수 구간 · 값·SQL 원문 0 · 누출 관문 통과분만) · `DB_BACKEND=direct`면 멈춤 | ✅ |
| A5 | 점검 | `python -m scripts.itam_bench --dry-run --env closed` | 린트 통과 | ✅ |
| A6 | 벤치 실행 | `python -m scripts.itam_bench --run --env closed --user <계정>` | 사다리 `intent_orchestration` · 끝에 「산출물 기록」 · 턴별 자산 사용 표지 | ✅ |
| A7 | (선택) 자산 끄고 다시 실행 | `python -m scripts.itam_bench --run --env closed --user <계정> --asset-ablation <키>` | 같은 시나리오 · 그 자산만 꺼짐(벤치 서버 프로세스에서만 · `run.json`에 기록) — 키마다 run 하나 | ✅ |
| A8 | (첫 사이클만) 폴스타 기준선 | `python scripts/eval_text2sql.py --dry-run` → `python scripts/eval_text2sql.py --db all --path orchestration` | 같은 내부망 FabriX에서 폴스타 정답률(G-4 · W0) | ✅ |
| A9 | 반출 | `results/itam_bench/<run>/`의 파일만 — `plans/140` 6파일 + `asset_verification.yaml`(A4를 돌렸으면) | 누출 관문 통과(`leak_check.json`) · 제품 로그·`.cache/` 반출 금지 | ✅ |

**B. 외부망 — 근거 → 작성 → 검증 → 빌드 → 커밋 (우리 · Claude Code)**

| # | 할 일 | 명령 | 확인 | 표지 |
|---|---|---|---|---|
| B0 | 반출물 배치 | `results/itam_bench/<run>/`에 둔다 | `git check-ignore results/itam_bench/<run>` 출력 있음(추적 안 됨) | ✅ |
| B1 | 차이 점검 | `python -m scripts.itam_bench --sync results/itam_bench/<run>` | 전사본 밖 테이블·컬럼 · 정책 밖 컬럼 | ✅ |
| B2 | 근거 묶음 | `python -m scripts.itam_bench --evidence results/itam_bench/<run>` | `results/itam_bench/<run>/knowledge_evidence/` 생성 · 종료 0 · 누출 관문 통과(실패면 `leak_check.json`만) · 다시 돌려도 바이트 동일 | ✅ |
| B3 | 모의 DB 준비(2회차부터 행 생성) | `.venv/bin/python testdata/itam_closed_sim/generate.py ddl results/itam_bench/<run>/` → `.venv/bin/python testdata/itam_closed_sim/generate.py rows results/itam_bench/<run>/ --rows 20 --seed 140` → `cd testdata/itam_closed_sim && docker compose down -v && docker compose up -d` → `mcp_server/.env`의 `ITAM_CONNECTION`을 `mariadb://itam_sim_ro:itam_sim_ro_pass@127.0.0.1:3308/INST1`로(원래 줄은 적어 둔다) → MCP 9099 재기동 | 테이블 수 조회(README 「기동 · 종료」) · 1회차는 빈 스키마만 | ✅ |
| B4 | 원천 파일 작성 | Claude Code 세션에서 `/itam-knowledge results/itam_bench/<run>` | `testdata/itam_bench/closed/knowledge/*.yaml` · 항목마다 `origin: claude_code`·`evidence: <run>`·`status` · 이름만 보고 쓴 의미는 `(추정)` | ✅(스킬 `.claude/skills/itam-knowledge/SKILL.md` · K2·K8은 2회차 반출 뒤) |
| B5 | 결정적 검증(**커밋 전 필수**) | `python -m scripts.itam_bench --validate-knowledge --catalog results/itam_bench/<run> --out testdata/itam_bench/closed/knowledge/validation.yaml` | 거절 0 — 거절이면 사유를 보고 B4로 돌아간다 · 모의 DB를 못 쓰면 `db_unverified`로 실패(`--static-only`는 사전 점검용 — 그 결과로는 커밋하지 않는다) | ✅(K8 템플릿 포함) |
| B6 | 빌드 | `python -m scripts.itam_bench --build-assets results/itam_bench/<run>` (지식 원천 디렉터리를 바꾸려면 `--knowledge <DIR>` — 기본 `testdata/itam_bench/closed/knowledge` · 스키마 캐시도 갱신하려면 `--install-cache`) | 「검증 오류 0」 · B5 통과 항목만 `itam.yaml`(K1·K2·K6) · `config/knowledge/itam/`(K3 설명 · K4 섹션 · K8 템플릿) · `config/synonym_seeds/itam.yaml`(K3 유사어)에 실림 · 치환값 검출 시 거부 · 철회로 비게 된 산출 파일은 삭제 | ✅ |
| B7 | 폴스타 무변경 확인 | `python scripts/prompt_render_diff.py --ci` | 차이 0 | ✅ |
| B8 | 커밋 | `git status --short results testdata/itam_closed_sim/generated`(빈 출력이어야 함) → `git add testdata/itam_bench/closed/knowledge config/db_profiles/itam.yaml config/knowledge/itam config/synonym_seeds/itam.yaml` → `git commit` | 값·`results/` 원본·모의 DB 생성물 미포함(G-6) | ✅ |
| B9 | 모의 DB 되돌리기 | `mcp_server/.env`를 B3에서 적어 둔 줄로 복원 → MCP 재기동 → 필요하면 `cd testdata/itam_closed_sim && docker compose down -v` | 로컬 샌드박스(3307) 복귀 | ✅ |

**C. 효과 판정 — run N+1 반출 뒤 (외부망)**

| # | 할 일 | 명령 | 확인 | 표지 |
|---|---|---|---|---|
| C1 | 회차 비교 | `python -m scripts.itam_bench --compare results/itam_bench/<run_N> results/itam_bench/<run_N1>` | 시나리오별 전이 · SQL 관측 턴 정답률 · 실패 분류(`wrong_table`·`wrong_column`·`non_sql`) · 지연 | ✅ |
| C2 | 자산별 효과 | `python -m scripts.itam_bench --ablation-report results/itam_bench/<run_N1> results/itam_bench/<끈 run…>`(첫 run이 기준 · A7 실행분) | 자산별 효과 표·유지 판정(§4.7) — 켠 쪽이 정답률을 낮추지 않고 상위 실패 분류를 줄일 때만 유지 | ✅ |
| C3 | 실패 항목 철회 | 원천 파일 해당 항목 `status: withdrawn` + 사유(`asset_verification.yaml` 실패 · C2 철회) → B5 → B6 → B8 | 철회 항목은 지우지 않고 이력으로 남는다 | ✅(원천 형식) |
| C4 | G-4 판정 | C1 정답률 · 지연 ↔ A8 폴스타 기준선 | ITAM ≥ 폴스타 · 단순 질의 < 10s | ✅ |

### 4.3 근거 묶음과 Claude Code 작성 절차

- **근거 묶음(evidence)** — `python -m scripts.itam_bench --evidence <run_dir>`가 반출 파일(`schema_catalog.yaml`·`trace.jsonl`·`report.md`·`code_samples.yaml`)과 `itam_schema.json`·현행 정의에서 자산별 입력을 결정적으로 뽑는다: 테이블 군별 컬럼·정의 · P1 근거(관계 채택·형식 비율·코드 판정 — 값 없음) · 실패 턴(분류·가린 SQL·선별 테이블·자산 사용 표지) · 시나리오 질문 · 이전 사이클 검증 실패 목록. 값은 없다(반출물에 없으므로).
- **작성 스킬** `.claude/skills/itam-knowledge/SKILL.md`(D-312 형식 — 새 세션 스킬 목록에서 로드 확인): 자산별 출력 계약(키·길이·금지 사항) · 작성 순서(K6 → K1 → K4 → K3 → K2 → K8) · 「근거 묶음에 없는 사실은 쓰지 않는다 · 이름만 보고 판단한 것은 `(추정)` 표기」 · 끝에 반드시 검증 CLI 실행. 모델·위임은 CLAUDE.md 규칙(정의 파일 고정)을 따른다.
- **원천 파일**(git 추적 · 사람이 읽고 고칠 수 있는 단일 출처): `testdata/itam_bench/closed/knowledge/{guide,examples,descriptions,synonyms,prompt_section,query_templates}.yaml`. 각 항목에 `origin: claude_code` · `evidence: <run_id>` · `status: active|withdrawn`(내부망 실패분은 `withdrawn` + 사유 — 삭제하지 않고 이력 보존).
- **빌더 경유**(병행 세션 요청): `itam.yaml`은 빌더가 통째로 다시 쓰므로 K1·K2·K6은 원천 → 빌더 오버레이로만 들어간다. 빌더 변경은 `plans/140` 커밋 뒤 그 세션과 통지하고 한다.

### 4.4 반출 확장 (D-301 부기 · 값 0 원칙 유지)

| 추가 | 내용 | 목적 |
|---|---|---|
| 자산 사용 표지(trace 턴 필드) | 이번 턴 프롬프트에 실린 자산 키·버전 지문(해시 앞 12자)·건수 · 템플릿 적중(ID·슬롯 이름 — 값 없음) · 결정적 조립/LLM 폴백 구분 | W8 효과 귀속 |
| 검증 결과 파일(7번째) `asset_verification.yaml` | `--verify-assets` 모드: 반입된 K2·K4·K8 SQL을 읽기 전용 실행(`--check-oracle`과 같은 경로) → 항목 ID별 성공/오류 범주/행 수 구간(0 · 1~10 · 11~100 · 100+) | 실패 항목 걷어내기 — 값·결과 행 없음 |
| 실패 증거 | 턴별 실패 분류 + 가린 SQL(D-301 ② 규칙 그대로) + 선별 테이블 — 있는 칸 재사용 | K1·K4·K8 근거 |

### 4.5 설명 정본 파일 (K3)

- 현재 컬럼 설명 정본은 Redis(+ 백업 `.cache/structure/{db}/descriptions.yaml`)라 git 커밋으로 반입할 길이 없다. `config/knowledge/itam/column_descriptions.yaml`을 신설하고, 기동·스키마 로드 시 **Redis에 없는 컬럼만** 채운다(내부망 Redis에 이미 있는 설명 — 1회차 698건 · 실표본 기반 — 과 운영자 편집 `manual`이 우선). 출처 표지 `claude_code`.
- 소비 코드는 바꾸지 않는다(`cache_manager.get_descriptions` 그대로) — 적재 지점 하나만 추가.

### 4.6 ITAM 결정적 조립 — 데이터 템플릿 (K8 · G-5)

- **템플릿 파일** `config/knowledge/itam/query_templates.yaml` — 항목: `id` · `intent`(설명 한 줄) · `triggers`(질문 표면어 — 선택 힌트) · `slots`(이름·형식: `center`·`dept_code`·`date`·`date_range`·`hostname`·`code`(허용 코드 목록 키) · 필수 여부) · `sql`(MariaDB · 슬롯 자리표 `:slot` · 백틱 한글 식별자) · `tables`(검증용) · `notes`.
- **선택**: 폴스타 시맨틱 컴파일러와 같은 2단계 — ①LLM이 질문을 보고 템플릿 ID와 슬롯 값을 **JSON으로만** 고른다(템플릿 목록 프롬프트 · 짧음) ②코드가 슬롯을 형식 검증하고 **바인딩·이스케이프해 조립**한다(문자열 연결 금지 · 슬롯 형식 밖이면 거절). 선택 실패·슬롯 거절·조립 SQL 검증 실패 → 기존 LLM SQL 생성으로 폴백(구조화 사유 로그 — 침묵 폴백 금지).
- **배선**: `query_generator._try_deterministic` 계열 자리(단일)와 `multi_db_executor`의 같은 단계(멀티)에 **같은 함수** — D-066 대칭. 신규 모듈 `src/db_adapters/template_assembler.py`(application · 어댑터 계층 — DB 특화 로직 격리 D-089) · 프롬프트 `src/prompts/template_selection.py`.
- **발동**: 그 DB에 템플릿 파일이 있을 때만(존재 기반 · 폴스타 비트 동일). 전역 끄기 플래그 `TEXT2SQL_TEMPLATE_ASSEMBLY`(기본 on — 파일 없으면 무동작이라 「기본 off = 현행 동일」 원칙과 같은 효과 · 예외 근거를 config 주석에).
- **검증**: 템플릿 계약 검사 · 모든 슬롯 조합 대표값으로 모의 DB 실행 · 내부망 검증 모드. 실패 템플릿은 `withdrawn`.
- **초기 범위**: W0 실패 분포·시나리오 18건에서 빈도 상위 질의형 5~10개(예: 센터·부서별 서버 목록 · 유지보수 계약 만료 · 담당자별 자산 · 서비스↔서버 연결). 수치는 W0 뒤 확정.

### 4.7 자산별 켜고 끄기 (W8)

- 벤치 `--asset-ablation <키>`: 해당 자산만 끈 run을 같은 시나리오로 돌려 비교(요청 단위 오버라이드 — 불가하면 run 단위).
- 유지 규칙: 켠 쪽이 SQL 관측 턴 정답률을 낮추지 않고, 실패 분류 상위 2종 중 하나를 줄일 때만 유지. 아니면 원천 항목 `withdrawn` → 빌더 재실행 → 커밋.

### 4.8 작업 단위

| W | 내용 | 파일 | 결정 |
|---|---|---|---|
| **W0** | **기준선** — 2회차 반출(`plans/140` 잔여)로 ITAM 정답률·실패 분포·지연 · 같은 내부망에서 폴스타 하네스 기준선(시나리오 · `eval_text2sql`) — 사용자 실행 | 리포트 | G-4 |
| **W1** | 근거 묶음 `--evidence` + 반출 확장(자산 사용 표지 · 실패 증거) | `src/domain/knowledge_evidence.py`(신규 · 순수) · `scripts/itam_bench/{catalog,__main__,redact}.py` | D-316 · D-301 부기 |
| **W2** | 원천 파일 형식 + 검증 CLI `--validate-knowledge`(K1·K3·K4·K6 결정적 검증 · K2·K4·K8 모의 DB 실행) | `src/domain/knowledge_assets.py`(신규 · 검증 순수 함수) · `scripts/itam_bench/knowledge.py` | D-316 |
| **W3** | 작성 스킬 `.claude/skills/itam-knowledge/` + 1회차 반출로 첫 원천(K6·K1·K4·K3) 작성 — Claude Code | 스킬 · `testdata/itam_bench/closed/knowledge/*` | D-312 형식 |
| **W4** | 빌더 지식 오버레이 + 설명 정본 파일 적재 지점(§4.5) | `scripts/itam_bench/build_assets.py`(140 세션 통지) · `src/schema_cache/*` 적재 1곳 | D-311 ③ 개정 |
| **W5** | 검증 모드 `--verify-assets` + 7번째 반출 파일 + K2 예시·질의 이력 시드 연결 | `scripts/itam_bench/*` · `scripts/query_history_seed.py` | D-301 부기 |
| **W6** | K8 결정적 조립 — 템플릿 계약·선택 프롬프트·조립기·단일/멀티 배선·폴백 사유 | `src/db_adapters/template_assembler.py` · `src/prompts/template_selection.py` · `src/nodes/{query_generator,multi_db_executor}.py` · `src/config.py` | D-316 |
| **W7** | K5 공통코드 후보(정의 `kind` 단서 · 기본키 없는 코드/이름 쌍) — 내부망 P1 보강 · 모의 DB 검증 | `src/domain/schema_inference.py` · `asset_generation_service.py` | D-294 부기 |
| **W8** | 효과 측정 `--asset-ablation` · 리포트 자산별 표 · 유지 규칙 | `scripts/itam_bench/report.py` | G-4 |
| **W9** | 문서 — D-316 구현 기록 · D-301·D-294 부기 · INDEX · 매뉴얼(관리자: 반입 절차 · 사용자: 영향 없음 확인 — D-255) | 문서 | — |

**순서**: `plans/140` 커밋 → W1 → W2 → W3 → W4 → W5 ∥ W6 → W7 → (내부망 2·3회차) → W8 → W9. W0은 내부망 2회차와 함께. W3 첫 원천은 1회차 반출물로 시작하고 2회차 반출이 오면 근거를 갱신한다.

**병행 세션 경계**: `plans/140`(collectorinfra-b5)이 `scripts/itam_bench/{build_assets,code_samples,catalog,redact,__main__,report}.py`·`config/db_profiles/itam.yaml`·시드를 소유한다(미커밋). 143은 140 커밋 통지 뒤 착수하고, `itam.yaml`은 빌더(오버레이)로만 바꾼다.

## 5. 게이트 — 답 (2026-10-07)

| G | 질문 | 답 | 반영 |
|---|---|---|---|
| **G-1** | 외부망에서 만든 지식 자산을 내부망에 어떻게 넣나 | **외부망에서 바로 커밋** | D-311 ③ 「근거 없는 짐작은 직접 커밋하지 않는다(예외: 테이블 정의 초안)」 → **예외에 Claude Code 작성 지식 자산(K1·K2·K3·K4·K6·K8) 추가 — 단, 검증 CLI 통과분만**. 내부망 승인 단계 없음 · 안전장치는 결정적 검증 · 모의 DB 실행 · 내부망 검증 모드 · `withdrawn` 되돌리기 |
| **G-2/G-3** | 작성 LLM 위치 | *"claude code 내에서 돌린다."* | 앱 내 LLM 생성기·탭 생성 진입점 없음 · 외부 과금 0 · MLX 0 · 작성 = Claude Code 스킬 |
| **G-4** | 「폴스타만큼」 기준 | **폴스타 기준선과 비교** | §3 목표 6 · W0 |
| **G-5** | ITAM 결정적 조립 | **이번에 포함** | K8 · §4.6 · W6(데이터 템플릿 — D-294 G-1 유지) |
| G-6 | 원천·산출 git 추적 | (묻지 않음 — G-1 직접 커밋의 결과로 추적) | 원천 `testdata/itam_bench/closed/knowledge/` · 산출 `config/` 추적 · 값·`results/` 원본 제외(메모리 「반출 스키마 이름 추적 허용」과 같은 선) |

## 6. 검증

| 범위 | 방법 | 기준 |
|---|---|---|
| W1 | 1회차 반출물 → 근거 묶음 | 값 칸 0(누출 관문 5규칙 재사용) · 자산별 입력 결정적(두 번 실행 바이트 동일) |
| W2 | 정상·환각 식별자·펜스·중괄호·사용률 규칙·충돌 유사어 원천 픽스처 | 거절 항목·사유 · 정상만 통과 · 모의 DB 실행 실패 항목 거절 |
| W3 | 스킬 로드 확인(새 세션 목록) · 첫 원천이 검증 CLI 통과 | 오류 0 |
| W4 | 빌더 오버레이 → `itam.yaml` · 설명 적재(Redis 있음/없음/manual) | 기존 값 우선 · 폴스타 비트 동일 |
| W5 | 모의 DB(3308) 검증 모드 | 성공·오류 범주·행 수 구간만 · 값 0 · 쓰기 SQL 거절 |
| W6 | 가짜 LLM 선택(정상·없는 ID·슬롯 형식 위반·주입 시도 `'; DROP`) · 단일·멀티 같은 SQL · 폴백 사유 | 조립 SQL 바이트 고정 · 주입 거절 · 템플릿 없는 DB 무동작(`prompt_render_diff --ci` 차이 0) |
| W7 | 모의 DB 기본키 없는 공통코드 테이블 | 라벨 쌍 채택 · 겹침 0.5 기각 · 폴스타·로컬 샌드박스 비트 동일 |
| W8 | 가짜 run 두 벌 | 자산별 표 · 유지/철회 판정 |
| MLX | K8 템플릿 선택 프롬프트 대표 소수 1회(두 평면 mlx 확인 뒤) | 형식만 |
| 폐쇄망(사용자) | 2회차(W0) → 반입 → 검증 모드 → 3회차 | G-4 기준 |
| 회귀 | `regress.py --base <세션 시작 SHA>` 모듈 단위 | — |

## 7. 위험

- **승인 없는 지식이 모든 질의에 실린다**(G-1의 대가): 구 샌드박스 `query_guide`의 「JOIN 관계 부재」 같은 오안내가 그대로 반입될 수 있다 → 검증 CLI(식별자 실존·금지 규칙) · 모의 DB 실행 · 내부망 검증 모드 · W8 켜고 끄기 · `withdrawn` 되돌리기. 내부망 관리자는 「DB 구조」 탭 버전 기록에서 이전 프로필로 되돌릴 수 있다(D-311 주의 ②).
- **근거 없는 추정**: Claude Code는 값을 보지 못한다 — 코드 의미·업무 규칙은 이름과 실패 증거로만 쓴다 → `(추정)` 표기 · 값이 필요한 자산(K5 코드 라벨)은 내부망 P1로 남긴다.
- **유사어 오염**(D-142): 쓰기 가드 · 적중 표지 추적.
- **템플릿 오적중**(K8): 비슷한 질문에 다른 의도의 템플릿이 걸리면 그럴듯한 오답이 나온다 → 슬롯 필수 검사 · 「템플릿 범위 밖이면 고르지 않음」 선택지 · 적중 표지로 W8 측정 · 오적중 템플릿 철회.
- **내부망 설명 덮어쓰기**: 설명 정본 파일은 Redis에 없는 컬럼만 채운다(§4.5).
- **반입마다 재승인**(D-311 주의 ②): 빌더 산출이 내부망 승인본(`manual`·P1 코드값)을 덮지 않도록 탭 병합 규칙을 따른다.

## 8. 버전 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-07 | 신규 — 폴스타 자산 전수(설정 17 · 코드 5 · 런타임 5 · 평가) 대조 · 격차 3종 · K1~K8 대응표 · 생성기 하나·진입점 둘 · 반출 확장 · 자산별 켜고 끄기 · 게이트 G-1~G-6 · D-316 예약 |
| v1.1 | 2026-10-07 | 게이트 답 반영 — G-1 외부망 직접 커밋(D-311 ③ 개정) · G-2/G-3 Claude Code 작성(앱 내 생성기·탭 진입점 제거 · 스킬 + 원천 파일 + 검증 CLI + 빌더 오버레이) · G-4 폴스타 기준선 비교 · G-5 결정적 조립 포함(K8 데이터 템플릿 · §4.6 · W6) · 설명 정본 파일(§4.5) · 착수 조건 = `plans/140` 커밋 뒤(병행 세션 요청) · D-316 확정 |
| v1.2 | 2026-10-07 | §4.2.1 실행 절차 신설 — 내부망 A0~A9 · 외부망 B0~B9 · 효과 판정 C1~C4 명령어(✅ 지금 사용 · ⏳ W3·W4·W5·W6·W8 구현 뒤 — 옵션 이름은 구현 결과로 확정) |
| v1.3 | 2026-10-07 | 외부망 구현(`-TODO`→`-WIP`) — 근거 묶음 `--evidence` · 원천 검증 `--validate-knowledge` · 빌더 지식 오버레이(`--knowledge` · 철회 시 산출 삭제) · 설명 정본 파일 적재(Redis HSETNX) · K8 템플릿 조립(`src/db_adapters/template_assembler.py` · 단일·멀티 공용 · `TEXT2SQL_TEMPLATE_ASSEMBLY`) · 자산 사용 표지 · `--verify-assets` · `--asset-ablation` · W7 정의 기반 공통코드 쌍 · 첫 원천 4종(K1 10 · K4 9 · K3 설명 54·유사어 3) · 잔여는 내부망 |
| v1.4 | 2026-10-07 | §4.2.1 명령을 구현 옵션과 대조해 ⏳ 전부 ✅로(`--verify-assets` · `--asset-ablation` · `--ablation-report` · 빌더 `--knowledge` · 스킬) · 머리 상태의 착수 조건 문구 정정(140은 `b1fabf4`에 커밋돼 있었음) |
