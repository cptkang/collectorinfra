# 146. ITAM 내부망 3회차 결과 교정 — 백틱 식별자 검증 누락 · 선별↔생성 테이블 불일치 · 서비스 연결 탐침 · 4회차 정답 판정 준비

> **작성일**: 2026-10-08 · **v1.4**(완료 — 잔여 이관 · v1.3 W5 (3) · v1.2 착지 — §10 착지 보고)
> **상태**: **완료(2026-10-08 · 잔여 이관)** — W1·W3·W4·W5 (1)(2)(3) 완료(계획 끝 회귀 rc=0 · W5 (3) 회귀 rc=0 · 모듈 단위) · 내부망 4회차 실행됨(`20261008-133613` — 오라클 7/7 · 1054 오류 0) · **이관**: W2 선별 보강 → `plans/149` W2(4회차 재계수 5→8턴 · G-2 (b)) · W7 내부망 체크리스트 → `plans/149` W6(5회차) · F12 「열#N」 정책 동기화 → `plans/149` W5(closed 정책 재키잉) · W6(F9)은 145에서 교정 · L1(3·4단 엔진 폴백) → `plans/103` 소관
> **회귀 대상 파일**: `src/sql_validation.py` · `src/nodes/{table_selection,schema_analyzer,multi_db_executor,context_resolver,output_generator}.py` · `src/utils/empty_antecedent.py`(신규) · `src/orchestration/{intent_planner,result_aggregator}.py` · `src/routing/semantic_router.py` · 매뉴얼 3 · `config/{db_profiles/itam,knowledge/itam/column_descriptions,knowledge/itam/prompt_template,synonym_seeds/itam}.yaml` · `testdata/itam_bench/closed/knowledge/{guide,prompt_section,descriptions,examples}.yaml` · `testdata/itam_bench/scenarios.closed{,.probe}.yaml` · 오라클·탐침 SQL 17 · 테스트 7 · (v1.3) `scripts/itam_bench/__main__.py` · `tests/test_scripts/test_plan146_w5_oracle_out.py`
> **게이트 답(사용자 2026-10-08)**: *"권고에 맞게 진행하라."* — G-1 (b) · G-2 (c) · G-3 (a) · G-4 (a). W5 (3) `--check-oracle --out`(선택)은 `scripts/itam_bench/__main__.py`를 145가 미커밋 편집 중이라 **보류**(145 통지)
> **요청(사용자 2026-10-08)**: *"「20261007-174622」 폴더에 itam_bench결과가 있다. 이를 분석하여 수정계획 파일을 작성하라."*
> **입력**: 내부망 3회차 run `results/itam_bench/20261007-174622/`(closed · `tier2_intent` · 워커 fabrix / 오케스트레이터 vllm · 시나리오 18 · 턴 19 · SQL 관측 16 · 누출 관문 통과) · 외부망 실행 B1 `--sync` · B2 `--evidence`(`knowledge_evidence/` 생성 · 관문 통과) · C1 `--compare 20261007-152223 20261007-174622` · 코드 재현 2건(§3 F1·F9). LLM 호출 0 · DB 호출 0
> **계획 시작 SHA**: `89773af`(작업 트리에 132·143·145 미커밋 편집이 있다 — 145는 `scripts/itam_bench/redact.py`를 편집 중)
> **관련 계획**: `plans/143`(지식 자산 사이클 — 이 계획이 3회차 사이클 처리분) · `plans/137`(백틱 컬럼 검사 누락 잔여 → **승격**) · `plans/139`(정의 기반 선별) · `plans/132`(ITAM-102·105 소스 선별) · `plans/145`(반출 값 치환 — `redact.py` 동시 편집) · `plans/135`(벤치 · 오라클 형식)
> **관련 결정**: D-003(검증 3중 방어) · D-004(LLM 출력은 정합성 근거 아님) · D-297(DB별 한글 식별자 정책) · D-301(반출 값 0 · 식별자 남김 부기) · D-308(정의 선별 · 사용률 정본은 관측 DB) · D-316(지식 자산 작성) · D-319(132 3회차 전 교정) · D-321(예약 · 145 — D-320에서 재부여) · D-303(회귀)
> **D-번호**: 없음 — G-1이 (b)면 D-297 부기, G-3이 바뀌면 D-319 부기로 처리한다(신규 번호 불필요 권고)

---

## 0. 증거 규칙

- 수치는 반출물(`run.json`·`report.md`·`trace.jsonl`·`schema_catalog.yaml`)과 근거 묶음(`knowledge_evidence/`) 기준이다. 실행 SQL 원문은 반출본(값 가림)만 봤다.
- 「재현」은 외부망 저장소 코드로 같은 입력을 돌려 확인한 것이다. 「추정」은 검증하지 않은 추론이다.
- 내부망 값은 모른다. 서비스↔서버 연결 위치처럼 값이 있어야 판정되는 것은 §5 W5 탐침으로 넘긴다.

## 1. run 개요

| 항목 | 값 | 비고 |
|---|---|---|
| 빌드 | 내부망 커밋 `33053bc`(외부망에 없음) | 자산 지문으로 역추적: `query_guide` `d1408b1d708a` · `query_rules` `fbd6a554e971` · `table_definitions` `0eaf4ac741a5` = **`25a4aa5` 산출물과 일치** |
| 반입 범위 | 132 v1.4(D-319 — 사용률 차단 · 명시 무존 소스 존 게이트 생략 · 「N개월 안에 … 종료」) **들어감** · 132 v1.5(2단 다중 시스템 존 게이트 task 위임) **안 들어감**(run 17:46 · v1.5 회귀 18:22) · 143 v1.7(`89773af` — 회차 이력 주석 이동 · 정의 notes) **안 들어감** | |
| P1 | 초안 `ac5e55a4e73d`(09:17:39) — **2회차와 같은 구버전 초안** | 143 §9.1 체크리스트 ① 미이행 → 프로파일·코드 컬럼 0 · 관계 0 · `code_samples.yaml` 0 · `### 코드값` 블록 없음 |
| 오라클 | 0/0 | 폐쇄망 시나리오 18건이 전부 `observe` — 143 §9.1 「3회차 = 정답 판정 가능 회차」는 **3회차 SQL로 정답 SQL을 만들어 4회차부터 대조**하는 뜻이다(이 계획 W5) |
| 실행 SQL 식별자 | 보임(143 §9.1 ② 효과) | 예외: ITAM-112·116의 `FROM` 테이블 이름이 `<가림>`(F9) |
| 지연 | 중앙값 47.3s · 최대 82.9s(2회차 45.4s · 117.0s) | 143 W0 소관 — 이 계획 범위 밖 |

### 1.1 2회차 대비(C1)

| 분류 | 2회차 | 3회차 | 원인 |
|---|---|---|---|
| `routing_miss` | 5 | 2 | 132 v1.4: ITAM-103·109(기간 거짓 되물음)·118(명시 무존 소스) 해소. 남은 102·105는 v1.5 미반입 — 4회차 확인 |
| `fabricated` | 1 | 0 | ITAM-114 사용률 추이 → ITAM SQL 없이 안내(D-319 ③) |
| `dialect_silent` | 1 | 0 | 큰따옴표 별칭 소멸(143 K4 s01) |
| SQL 실행 실패 | 0 | 2 | ITAM-108·110 첫 시도 — `tcdmsif80`에 없는 컬럼(F1) |
| 0행 SQL 턴 | — | 7/16 | 서비스 개념 6턴(F3) + 108 |

## 2. 턴별 진단

| 시나리오 | 질문 요지 | 결과 | 진단 | 발견 |
|---|---|---|---|---|
| ITAM-101 | 통합인증 서비스 서버리스트 | `tcdmsif72` 3칸(`그룹경로내용`·`용도내용`·`구성항목설명내용`)에 질문 문구 「통합인증 서비스」 전체 부분 일치 · 0행 | 사용자 문구를 그대로 건다 · 연결 위치 미확정 | F3 |
| ITAM-102 | 통합인증 서비스 서버들 담당 부서 | `zone_select` 되물음(폴스타 3존) | v1.5 미반입 | F11 |
| ITAM-103 | 인터넷뱅킹 서버 중 6개월 안 EOS | `tcdmsif72`↔`tcdmsif79` 세 칸 조인 + `용도내용` 부분 일치 · 0행 | 기간 되물음은 해소 · 서비스 연결이 0행 | F3 |
| ITAM-104 | 서비스별 서버 수 | `tcdmsgt82.어플리케이션명`을 서버 3칸에 부분 일치 LEFT JOIN · 417행(활성 칸 NULL 327) | **어플리케이션명 일부는 서버 자유 기재 칸에 나타난다**(유일한 행 반환 서비스 턴) · 선별 밖 `tcdmsif72` 사용 | F2·F3 |
| ITAM-105 | 서비스 서버 유지보수 계약 만료일 | `zone_select` 되물음 | v1.5 미반입 | F11 |
| ITAM-106 t1 | 통합인증 서비스 서버 목록 | 101과 같은 모양 · 0행 | | F3 |
| ITAM-106 t2 | 그 서버들 지원 종료일 | `tcdmsif79`↔`tcdmsif72` 조건 없는 조인 · **3,748행(전체)** | 앞 턴 조건 미승계 — 앞 턴 0행인데 전체 서버를 답함(침묵 오답) | F6 |
| ITAM-107 | 등록 서버 몇 대 | `COUNT(*)` `tcdmsif72` · 1행 | 활성 거름 없음(코드값 없음 — K6대로) | 오라클 후보 |
| ITAM-108 | 이번 분기 유지보수 계약 끝나는 서버 | ①`tcdmsif80`에 `활성화여부` 선택 → DB 오류 1054 ②재시도 `tcdmsif72`↔`tcdmsif43`(물품 두 칸) · 0행 | ① 검증기가 못 잡음 ② 가이드가 미확정이라 한 연결 키로 갈아탐 · 선별(41·43·72·78)에 `tcdmsif80` 없음 | F1·F2·F5 |
| ITAM-109 | 6개월 안 EOS(지목 없음) | `tcdmsif79` 단독 · HW/SW OR · 148행 | 모양 적정(날짜 리터럴은 실행일 계산값) | 오라클·K2 후보 |
| ITAM-110 | 5년 이상 노후 서버 | ①`tcdmsif80`에 `자산상태구분명` 선택 → DB 오류 1054 ②`tcdmsif80`↔`tcdmsif72`(두 칸) · 2,220행 | ① F1 · 선별(41·46)에 80·72 없음 · 조인은 활성 칸 때문(80만으로 답 가능 — 추정) | F1·F2 |
| ITAM-111 | 취득금액 상위 3대 | `tcdmsif41` · `자산분류구분`·`세부자산분류구분` 코드 리터럴 조건 · 3행 | 가이드는 서버 단위 금액을 `tcdmsif80`으로 안내 · 리터럴 출처 미확정(표본 행이 프롬프트에 실렸다 — 지어냄 단정 불가) | F8 |
| ITAM-112 | OS별 서버 수 | `tcdmsif72` `운영체제타입내용` 집계 · 5행 | 적정 · 반출 SQL `FROM` 가림 | F9 · 오라클·K2 후보 |
| ITAM-113 | 가상 서버로 등록된 서버 목록 | `tcdmsif92` 전체(19,476 추정) · 10,000행 절단 · 사용률 칸 3개 선택 | 「가상 서버」 범위 해석 미확정 · ITAM 사용률 칸을 목록에 실음(D-308 취지와 어긋남) | F7 |
| ITAM-114 | 지난주 CPU 사용률 추이 | SQL 없이 안내 · `db_ids` [] | **정상**(D-319 ③) | — |
| ITAM-115 | 용도에 통합인증 적힌 서버 | `용도내용` 부분 일치 「통합인증」 · 0행 | **「통합인증」은 `tcdmsif72.용도내용`에 없다**(값 관측) | F3 |
| ITAM-116 | 서버 분류 경로별 서버 수 | `그룹경로내용` 집계 · 749행 | 적정 · 경로 길이 2~52자 · `FROM` 가림 | F9 · 오라클 후보 |
| ITAM-117 | 통합인증 업무 할당 스토리지 | ①`tcdmsif51.용도구분명` 등호 + 활성 항진 조건(`= … OR IS NOT NULL`) · 0행 ②`tcdmsif52.업무명` `IN`(등호) · 0행 | 업무 이름을 등호로 건다 · 선별(41·51·57)에 52 없음 | F2·F4 |
| ITAM-118 | 통합인증 업무 스토리지 쓰는 서버 OS | `tcdmsif52.업무명 =` 질문 문구 전체 · `호스트명`↔`서버호스트명` · 0행 | 존 되물음 해소(D-319) · 이름 등호 | F4 |

## 3. 발견

| # | 발견 | 근거 | 처분 |
|---|---|---|---|
| **F1** | **SQL 검증기가 백틱 식별자를 읽지 못해 참조 테이블·컬럼 실존 검사를 통째로 건너뛴다.** `_extract_table_names`가 `` FROM `tcdmsif80` AS t ``에서 빈 집합 · `_extract_alias_map` 빈 사전 · `_validate_columns`의 `(\w+)\.(\w+)`는 `` t.`활성화여부` ``를 못 잡는다. `check_hangul_tokens`(D-297)는 「선별 스키마의 어느 테이블에든 있는 한글 이름」만 보므로(`_schema_identifiers` — 테이블 구분 없음) 선별된 다른 테이블에 있는 `활성화여부`(108 · `tcdmsif72`)·`자산상태구분명`(110 · `tcdmsif41`)이 통과하고, 테이블 이름 `tcdmsif80`은 한글이 아니라 검사 대상이 아니다 | **재현**: `validate_sql("SELECT t.`서버호스트명`, t.`활성화여부` FROM `tcdmsif80` AS t …", {tcdmsif72만})` → 오류 0 · 같은 SQL 무백틱은 컬럼 오류 검출. `plans/137` §1.2·§5가 「백틱 컬럼 검사 누락 — 실측 오답이 나오면 승격」으로 남긴 잔여의 **실측 사례**(108·110 첫 시도 DB 1054) | **W1** |
| **F2** | **선별 테이블과 생성 SQL 테이블이 어긋난다** — 5턴(104·106 t2·108·110·117)이 선별(=프롬프트에 컬럼이 실린) 밖 테이블을 썼다. 컬럼 목록 없이 가이드 문장만 보고 쓴 테이블에서 F1 오류가 났다. 선별 프롬프트 후보 줄은 정의의 `kind`·`manages`·`key_columns`·`related`만 싣고 `notes`(예: `tcdmsif80` 「서버 단위 질문은 이 테이블 하나로」)와 K1 가이드는 싣지 않는다 | trace `schema_context.presented_tables` ↔ `sql_analysis.tables` · `src/nodes/table_selection.py` `_candidate_line` · `src/prompts/table_selection.py` | **W1(G-1)** · **W2(G-2)** |
| **F3** | **서비스↔서버 연결 위치가 여전히 미확정이다.** 질문 문구를 서버 원장 자유 기재 3칸·용도·스토리지 업무명에 그대로 건 6턴이 모두 0행 · `용도내용`에는 「통합인증」 자체가 없다(115) · 반면 `tcdmsgt82.어플리케이션명`을 서버 3칸에 부분 일치로 댄 집계는 행을 돌려준다(104) → **질문 문구 → 어플리케이션명 → 서버 칸**의 두 단계 해석이 후보(추정) | 101·103·106·115·117·118 vs 104 · 정의 notes(`tcdmsgt82`·`tcdmsif52`) | **W4**(K1·K4) · **W5**(탐침) |
| **F4** | 업무·용도 이름을 **등호·IN·질문 문구 전체**로 건다(117·118) · 활성 칸에 항진 조건(117) | 실행 SQL | **W4**(K4) |
| **F5** | `tcdmsif72`↔`tcdmsif43`을 물품 두 칸(+그룹회사코드)으로 이은 조인 0행(108 재시도 · 날짜 조건 동반) — 2회차 `tcdmsif72`↔`tcdmsif41` 0행에 이어 서버 원장의 물품 키 연결은 두 회차 모두 0행. 매핑 테이블 `tcdmsif78`(물품 두 칸 보유) 경유가 후보(추정) | 108 · 143 §9 F2 | **W4**(K1 g05) · **W5**(탐침) |
| **F6** | **멀티턴 지시어 승계 실패** — 앞 턴이 0행인데 「그 서버들」을 전체 서버로 풀어 3,748행을 답했다(침묵 오답) | 106 t2 | **W3(G-3)** |
| **F7** | 「가상 서버로 등록된 서버」를 `tcdmsif92` 전체로 답하고(10,000행 절단) 사용률 칸을 실었다 · 서버 원장 쪽 판별 칸(`서버유형구분`·`tcdmsif80.가상화서버매핑여부`)은 코드값이 없어 못 씀 | 113 · P1 코드값 0 | **W4**(K4) · W7(P1) |
| **F8** | 서버 단위 금액 질문을 자산 원장 `tcdmsif41` + 분류 코드 리터럴로 답함 — 가이드(g03)는 `tcdmsif80` · 선별에 80 없음 | 111 | W2 · **W5**(오라클로 판정) |
| **F9** | **반출 가림이 `FROM` 테이블 이름을 가린다** — 별칭 없는 테이블 뒤(같은 조각)에 정책 밖 카탈로그 컬럼(`GROUP BY` 등)이 오면 `` FROM `<가림>` `` | **재현**(현 작업 트리): `redact_sql("SELECT `운영체제타입내용` AS os_type, COUNT(*) AS c FROM `tcdmsif72` GROUP BY `운영체제타입내용`", catalog_columns=[…])` → `` FROM `<가림>` `` · 별칭 `AS t`가 있어도 같음 · 컬럼이 앞에만 있으면 남음. 145(치환)가 들어가면 **가짜 테이블 이름**이 나가 더 오도한다 | **W6**(145 통지) |
| **F10** | P1 재실행 2회 연속 미이행 — 코드값·관계 근거 0이 K6 활성 규칙·여부 코드 질문(107·113)·K8 `code` 슬롯을 계속 막는다 | `run.json` `assets.p1` | **W7**(내부망) |
| F11 | ITAM-102·105 `routing_miss` | v1.5 미반입(§1) — 코드 작업 없음 | W7에서 4회차 확인 |
| F12 | 리포트 §8 「분류 필요 컬럼」 `CPU`·`IP`(원 컬럼 아님 — 식 해석 조각 추정) · 미분류 결과 열 39개(`열#N`) — 결과 열 단위 분석이 막힌다 | `report.md` §8 · trace `result.columns` | 145 통지(반출 정책 소관) |

## 4. 결정 충돌 검토(작업 전 필수)

| 결정 | 이 계획과의 관계 | 충돌 |
|---|---|---|
| D-003 | W1은 「참조 테이블·컬럼 존재」 검증을 MariaDB 백틱에도 적용 — 결정 취지 이행 | 없음 |
| D-297 | 허용 DB 한글 식별자 검사는 그대로 · W1은 테이블 단위 실존 검사를 더한다 | 없음(137 잔여 승격 · 부기) |
| D-301 부기 | 식별자 남김 원칙 — F9는 그 원칙의 결함 교정 | 없음 |
| D-308 | 선별 사다리(정의 → LLM 상한 8)는 유지 · W2 (a)는 후보 줄에 칸 하나 추가 | G-2 답에 따라 부기 |
| D-316 | 자산은 스킬로 작성 · 검증 통과분만 빌드 | 없음 |
| D-319 | F6 교정은 132 소관 밖(멀티턴) · 102·105는 v1.5로 이미 교정 | 없음 |
| D-321(예약 · 구 D-320) | F9는 145가 고치는 파일과 같다 | **조율 필요** — W6을 145 범위로 넘긴다 |

## 5. 작업 단위

### W1 — SQL 검증기 MariaDB 백틱 식별자 인식(본체 · F1 · F2 일부)

| 단계 | 내용 | 확인 |
|---|---|---|
| 1 | `src/sql_validation.py` `_extract_table_names`·`_extract_alias_map`·`_validate_columns`가 백틱 인용 식별자(`` `t` ``·`` `tcdmsif80` ``·`` t.`컬럼` ``·`` `t`.`컬럼` ``)를 읽게 한다. 엔진 분기는 기존 `_uses_backtick_quotes(engine)`를 쓴다 — PostgreSQL·DB2 경로는 바이트 동일 | 폴스타 `validate_sql` 기존 테스트 무변경 통과 |
| 2 | 별칭 없는 단일 테이블의 비한정 백틱 컬럼(`` SELECT `운영체제타입내용` FROM `tcdmsif72` ``)도 그 테이블 컬럼과 대조한다(현재는 무백틱도 비한정 컬럼 검사 없음 — 범위는 MariaDB 백틱으로 한정) | |
| 3 | **G-1** 선별 밖 테이블 처리 — 권고 (b): 실존 판정을 선별 스키마가 아니라 **조회 대상 전체 카탈로그**로 하고, 없는 컬럼이면 오류 문구에 **그 테이블의 실제 컬럼 이름 목록**(상한 있음 · 이름만)을 싣는다 → 재생성 1회에 고쳐지게 한다. 착수 첫 단계에서 검증 시점에 전체 카탈로그(선별 전 `schema_info`)가 상태에 남아 있는지 실측한다 — 단일(`query_validator`)·멀티(`multi_db_executor`) **양쪽 대칭** | 108·110 모양 입력이 실행 전 오류 + 컬럼 목록 · 104·117 모양(선별 밖 · 실존 컬럼)은 통과 |
| 4 | 테스트 `tests/test_nodes/test_plan146_backtick_validation.py`(신규) — 백틱 테이블·별칭·한정/비한정 컬럼 · 선별 밖 허용 테이블 · 조회 대상 밖 테이블 · PostgreSQL 비영향 · 한글 식별자 검사(D-297)와 중복 오류 없음 | |

### W2 — 선별 보강(F2 · F8 · G-2)

권고 (c): **W1-(b) 먼저 넣고 4회차에서 「선별 밖 테이블 사용 턴」을 다시 센다.** 그래도 남으면 (a)·(b) 중 고른다.

| 안 | 내용 | 비용 |
|---|---|---|
| (a) | 선별 후보 줄에 정의 `notes`를 싣는다(`_candidate_line`) | 108테이블 후보 목록이 길어진다(선별 프롬프트는 앞부분 KV 캐시 재사용 구조라 지연 영향은 첫 질문 위주 — 추정) · 폴스타는 정의 없음 → 비영향 |
| (b) | 데이터만 — `tcdmsif80` 정의 `manages`에 질문 유형(노후·경과 연수·취득금액·유지보수 계약 만료)을 드러낸다(`testdata/itam_bench/closed/table_definitions.yaml` → 빌드) | 코드 0 · 정의는 사람 검토 대상(D-308) |
| (c) | W1-(b)만 | 0 |

### W3 — 멀티턴 지시어 승계(F6 · G-3)

1. **조사**: 106 t2 경로(2단 `intent_planner` → `agent_orchestrator` → ITAM task)에서 앞 턴 SQL 조건·결과 행 수가 다음 턴 계획에 들어가는지 로그·상태로 확정한다(`context_resolver` 승계 신호 · 체크포인터 델타 병합 — Known Mistakes 「멀티턴 / 상태」). 끊긴 지점을 먼저 확정한 뒤 교정한다.
2. **교정(G-3 권고 (a))**: 지시어가 앞 턴 대상을 가리키고 앞 턴이 **0행**이면 전체로 넓히지 않는다 — 앞 턴 조건을 승계해 다시 0행이면 「앞 질문에서 찾은 서버가 없다」를 그대로 알린다. 앞 턴이 행을 돌려줬으면 종전대로.
3. 테스트: 목 LLM 2턴(앞 턴 0행 · 행 있음) · 폴스타 경로 대칭 확인(D-066).

### W4 — 지식 자산(스킬 `/itam-knowledge results/itam_bench/20261007-174622` · F3·F4·F5·F7)

근거 run을 `20261007-174622`로 올려 다시 쓴다. 회차·run 서술은 `text`가 아니라 YAML 주석에 둔다(143 v1.7 ⑨).

| 자산 | 항목 | 고칠 내용(값 없이) |
|---|---|---|
| K1 | g03-server | `tcdmsif80`에는 `활성화여부`·`자산상태구분명`이 **없다**(이름 칸 없이 `자산상태구분`만) — 이 테이블만으로 답하는 질문에 서버 원장을 조인하지 않는다 |
| K1 | g05-asset-maintenance | 서버 원장의 물품 두 칸으로 자산 원장·유지보수 계약에 이은 조인은 조건 유무와 무관하게 0행이 관측됐다 — 서버 단위 유지보수 계약은 `tcdmsif80`의 계약 칸으로 답한다 · 물품 키 연결은 매핑 `tcdmsif78` 경유가 후보(추정 · W5 탐침으로 확정) |
| K1 | g06-service | 질문의 서비스 문구를 서버 칸에 그대로 건 조회는 0행이다 · 서비스 이름은 먼저 어플리케이션 목록 `tcdmsgt82`의 `어플리케이션명`에서 핵심어 부분 일치로 찾고, 찾은 이름을 서버 자유 기재 칸에 부분 일치로 댄다(추정 — 104 모양만 행 반환) · `tcdmsif72.용도내용`은 서비스 단서로 쓰지 않는다(관측 0행) |
| K4 | 이름 일치 규칙(신규) | 업무명·용도·어플리케이션명 같은 이름 칸은 등호·`IN`·질문 문구 전체가 아니라 **핵심어 부분 일치**로 건다 · 「서비스」「업무」「시스템」「스토리지」 같은 일반어는 검색어에서 뺀다 |
| K4 | 활성 조건(보강) | 코드값 블록이 없으면 활성 칸 조건을 쓰지 않는다 — 항진 조건(같은 칸에 등호 OR NULL 아님)도 쓰지 않는다 |
| K4 | 사용률 칸(보강) | 목록·현황 질문의 선택 목록에 ITAM 사용률 칸을 싣지 않는다(정본은 관측 DB — D-308) |
| K3 | 설명 | `tcdmsif52.업무명`(부분 일치 · 현행 할당) · `tcdmsif80.자산상태구분`(이름 칸 없음 — 코드값 블록이 없으면 그대로 보여 준다) · `tcdmsgt82.어플리케이션명`(서비스 이름 해석의 1단계) |
| K2 | **G-4** | 권고 (a): 관측 성공 SQL 중 활성 의미와 무관하거나 집계 축이 확정된 3건 — 109(EOS 기간 · `tcdmsif79` 단독) · 112(OS별 집계) · 116(그룹 경로별 집계). 모의 DB(3308) **실행 검증까지**(`--static-only` 아님) |
| K8 | 보류 | 서비스 연결 미확정 · 코드값 0. EOS 기간 템플릿(`date_range` · `yyyymmdd`)은 4회차 109 오라클 통과 뒤 |

절차: 143 §4.2.1 B3(모의 DB 행 생성 — 3회차 카탈로그) → B4(스킬) → B5(`--validate-knowledge` 실행 검증) → B6 빌드(재기저 여부는 143 §9.1 ⑤ 조건대로 사용자 판단) → B7 `prompt_render_diff --ci` 차이 0.

### W5 — 4회차 정답 판정 준비(시나리오 · 오라클 · 탐침)

**(1) 오라클 전환 후보** — 정답 SQL은 `testdata/scenarios/oracles/ITAM-1NN.mariadb.sql`(값 리터럴 없음 · 날짜는 `:today` 자리표). 내부망 `--check-oracle`에서 0행·오류가 나면 그 시나리오는 `observe`로 되돌린다.

| 시나리오 | 정답 SQL 요지 | 비교 | 비고 |
|---|---|---|---|
| ITAM-107 | `tcdmsif72` 전체 건수 | `count` | 활성 거름 없음 — P1 코드값 승인 뒤 개정 |
| ITAM-109 | `tcdmsif79` HW·SW 지원 종료일 중 하나가 오늘~6개월 | `keyset`(`서버호스트명`) | |
| ITAM-112 | `tcdmsif72` `운영체제타입내용`별 건수 | `keyset`(`운영체제타입내용` — 정책 등급 general 확인 뒤) | |
| ITAM-116 | `tcdmsif72` `그룹경로내용`별 건수 | `count`(경로 값에 업무 이름이 섞일 수 있어 키로 쓰지 않는다) | |
| ITAM-108 | `tcdmsif80` `유지보수계약종료년월일`이 이번 분기 | `keyset`(`서버호스트명`) | `tcdmsif80` 마트 가설 — 탐침 P07과 함께 확인 |
| ITAM-110 | `tcdmsif80` 취득일 기준 5년 이상(경계 포함) | `keyset` | **G-4**: 기준 칸 `취득년월일` vs `경과년수` |
| ITAM-111 | `tcdmsif80` `취득금액` 상위 3 | `keyset` | 41(자산 원장) 해석과 갈림 — G-4 |

서비스 개념 시나리오(101~106·115·117·118)와 113은 `observe` 유지 — W5 (2) 탐침 결과로 5회차 오라클을 쓴다.

**(2) 연결 위치 탐침** — `testdata/itam_bench/scenarios.closed.probe.yaml`(신규 · `--run` 대상 아님) + 탐침 SQL을 내부망에서 `python -m scripts.itam_bench --check-oracle --env closed --scenarios testdata/itam_bench/scenarios.closed.probe.yaml`로만 돌린다(LLM 0 · 출력은 행 수만). 행 수가 정보가 되도록 `COUNT(*)` 한 행이 아니라 행을 돌려주는 모양(`SELECT DISTINCT 키 … LIMIT N`)으로 쓴다.

| # | 묻는 것 | 대상 |
|---|---|---|
| P01 | 서비스 핵심어가 어플리케이션명에 있나 | `tcdmsgt82.어플리케이션명` 부분 일치 |
| P02~P04 | 서비스 핵심어가 서버 자유 기재 칸·스토리지 업무명에 있나 | `tcdmsif72.그룹경로내용`·`구성항목설명내용` · `tcdmsif52.업무명` 부분 일치 |
| P05 | P01에서 찾은 어플리케이션명이 서버 칸에 나타나나 | 104 모양(두 단계) |
| P06 | 서버 원장 물품 키 ↔ 자산 원장 겹침 | `tcdmsif72`↔`tcdmsif41`(조건 없음) |
| P07 | 매핑 경유 | `tcdmsif72`↔`tcdmsif78`↔`tcdmsif41` · `tcdmsif80`↔`tcdmsif72` 행 배수 |
| P08~P09 | 스토리지·VM ↔ 서버 원장 | `tcdmsif52.호스트명`·`tcdmsif92.서버호스트명` ↔ `tcdmsif72.서버호스트명` |

탐침 핵심어는 시나리오 원문에 이미 있는 말(「통합인증」·「인터넷뱅킹」)만 쓴다. 

**(3) 결과 회수** — **완료(v1.3)**: `--check-oracle --out <run 디렉터리>` → `<run>/oracle_check/oracle_check.yaml` + 하위 전용 `leak_check.json`(run 루트 산출물 무손상 · §10.8). 원안: `--check-oracle`은 화면에만 찍는다. 반출을 쉽게 하려면 `--out <run 디렉터리>`로 `oracle_check.yaml`(오라클 ID · 판정 · 행 수 · 시간 — 값 0)을 남기고 누출 관문 대상에 넣는다(작은 CLI 변경 · 선택 — 안 하면 사용자가 화면 출력을 옮긴다).

### W6 — 반출 가림의 `FROM` 테이블 이름(F9 · 145 통지)

`redact.py` `_identifier_judge`에서 `FROM`·`JOIN` 바로 뒤 테이블 자리 식별자는 값 자리로 보지 않는다. **`plans/145`가 같은 파일을 미커밋 편집 중이라 이 계획에서 직접 고치지 않고 145 범위에 넣도록 통지한다**(재현 입력 3종 · 145가 치환을 켜면 가짜 테이블 이름이 나가는 위험 포함). F12(리포트 `CPU`·`IP`·`열#N`)도 함께 통지한다.

### W7 — 내부망 4회차 체크리스트(사용자)

- [ ] 반입 커밋에 132 v1.5 · 143 v1.7(`89773af`) · 이 계획 W1·W3·W4·W5가 들어갔는지 확인 — 벤치 리포트 자산 지문이 외부망 빌드와 같은지 대조
- [ ] **「DB 구조」 탭 P1 재실행·승인**(2회 연속 미이행 · 리포트 첫머리 「구버전 빌드」 경고가 사라져야 한다) · 2c에서 `활성화여부`·`서버유형구분`·`가상화서버매핑여부` 계열 코드값 승인
- [ ] 탐침 `--check-oracle --scenarios testdata/itam_bench/scenarios.closed.probe.yaml --out results/itam_bench/<4회차 run>` → `<run>/oracle_check/oracle_check.yaml`(ID·판정 범주·행 수·ms — 값 0) 반출(W5 (2)·(3)) · `--out` 상대 경로는 그대로 쓰이므로 `results/itam_bench/…`까지 적는다
- [ ] 오라클 `--check-oracle --out results/itam_bench/<4회차 run>`(기본 시나리오) → 0행·오류 시나리오 통보 — 탐침과 같은 `--out`이면 나중 실행이 `oracle_check.yaml`을 교체하므로 **탐침·오라클은 서로 다른 디렉터리**에 남긴다(예: 탐침은 별도 `results/itam_bench/<run>-probe`)
- [ ] 4회차 run(`--env closed`) → 반출
- [ ] **145의 오라클 키 누출 교정이 반입 커밋에 함께 들어갔는지** 확인(v45 H1 — 145 미커밋 코드에서 교정됨. 빠지면 오라클 시나리오 반출이 관문에서 막히거나 키가 샌다)
- [ ] 탐침 판독: 0행이면 `--check-oracle` 종료 코드 1이어도 **정상 정보**다(그 연결 위치에 없음). P06·P07A(조건 없는 대형 조인)는 시간 초과 가능 — 초과면 「시간 초과」로 옮긴다
- [ ] 오라클 판정 「보류 — key 열을 찾지 못했다」는 시스템 결과에 키 칸이 없다는 뜻이므로 **오답 신호**로 읽는다(`host_key` 별칭 `sevrHostName`·`server_hostname`·`server_host` 외 이름)
- [ ] 리포트에서 K2 예시가 덮는 시나리오(109·112·116)는 「K2 덮음」으로 구분해 읽는다 — 정답이 예시 복사일 수 있다
- [ ] 빌드 재기저(1회차 `20261006-152938` → 3회차) 여부 결정(143 §9.1 ⑤ — 이번에는 재기저하지 않았다)
- [ ] 오라클 상세 값은 145 `judge.sanitize_detail`로 general 아닌 칸(등급 미상 포함)의 `oracle_top`·`value_diffs`·비교 스칼라가 run마다 새 가짜 값으로 반출된다 — 107(value)·111(argmax)은 반출물에서 **판정 결과만** 믿는다(판정은 원값으로 계산). 탐침은 행 수만이라 영향 없음
- [ ] 3회차 trace에서 109·111·106 t2 결과 열이 `열#N`으로 가려져 있었다 — 4회차에서 「key 열 못 찾음」 보류가 나면 `host_key` 별칭 누락 여부를 먼저 본다
- [ ] 145 반입 확인 ①: 오라클 키 열을 해석하지 못하면 키를 싣지 않는다(fail-closed · `keys_allowed=False`)
- [ ] 145 반입 확인 ②: `run.json`에 `registry_fallback` 칸이 **없다**(있으면 레지스트리 로드 실패로 per_db 태그를 전부 치환한 run)
- [ ] 145 반입 확인 ③: per_db 태그는 등록 DB id 화이트리스트만 원문으로 남는다
- [ ] 3회차 반출 실물 대조 포인트(4회차 반출물에서 확인): `substitutions.yaml` 존재 · `report.md` 첫머리 「형식 보존 치환값 — 원값 아님」 · `leak_check.json` 통과 · OID나 점 4덩이 이상 버전 문자열이 산출물에 있으면 관문이 run을 막는다(닫힌 쪽 — 막히면 위치만 보고 해당 칸을 확인)

## 6. 게이트 — 답(2026-10-08 · 권고안 전부)

| # | 질문 | 선택지와 결과 | 권고 |
|---|---|---|---|
| G-1 | SQL이 선별(프롬프트에 컬럼이 실린) 밖 테이블을 쓰면? | (a) 「존재하지 않는 테이블」 오류로 재생성 — 폴스타와 같은 동작이지만 104·117처럼 맞게 쓴 턴도 재시도로 간다 · (b) 조회 대상 전체로 실존만 검사하고, 없는 컬럼이면 그 테이블 실제 컬럼 목록을 오류에 실어 재생성 · (c) 검사 없음(현행) | **(b)** |
| G-2 | 선별 보강 | (a) 후보 줄에 정의 `notes` · (b) `tcdmsif80` 정의 문구만 보강 · (c) W1-(b) 뒤 4회차 관찰 | **(c)** |
| G-3 | 앞 턴이 0건일 때 「그 서버들」 | (a) 앞 턴 조건 승계 — 다시 0건이면 「앞에서 찾은 서버 없음」 안내 · (b) 조회 없이 「앞 결과 0건」 안내로 종료 | **(a)** |
| G-4 | 정답(K2·오라클) 범위 | (a) K2 3건(109·112·116) 지금 · 오라클 7건은 `--check-oracle` 확인 뒤 확정 · 110은 `취득년월일` · 111은 `tcdmsif80` 기준 · (b) K2는 4회차 오라클 통과 뒤 | **(a)** |

## 7. 검증

- W1·W3: 신규 테스트 + 계획 끝 1회 `python scripts/regress.py --base 89773af --files <바꾼 파일…>`(D-303) · 폴스타 비영향은 기존 `validate_sql` 테스트와 `prompt_render_diff --ci`로 확인 · 실 LLM은 목 우선(MLX는 W3 대표 2턴 1회만 — D-240).
- W4: `--validate-knowledge` 실행 검증(모의 DB 3308) 오류 0 · 빌드 「검증 오류 0」 · `git status --short results testdata/itam_closed_sim/generated` 빈 출력.
- W5: 오라클·탐침 SQL을 모의 DB에서 `--check-oracle`(로컬)로 구문·실행 확인 → 시나리오 린트(`--dry-run --env closed`) 통과.
- 매뉴얼(D-255): 대상 아님(검증기·벤치·내부 지식) — W3이 사용자에게 보이는 안내 문구를 더하면 그때 판단한다.

## 8. 위험

| 위험 | 대응 |
|---|---|
| W1이 MariaDB 생성 SQL을 새로 거절해 재시도·지연이 늘 수 있다 | G-1 (b)는 실존하는 선별 밖 테이블을 통과시킨다 · 4회차 `retries` 평균 대조(3회차 0.12) |
| 오라클 정답 SQL이 우리 해석일 뿐이다(80 마트 가설 · 활성 거름 없음) | `--check-oracle` 0행·오류면 `observe` 복귀 · P1 코드값 뒤 개정 |
| 탐침 핵심어가 업무 이름이다 | 시나리오 원문에 이미 있는 말만 · 출력은 행 수만(D-301) |
| 145와 `redact.py` 동시 편집 | W6은 145에 넘긴다 |

## 9. 버전 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-08 | 신규 — 3회차 run `20261007-174622` 분석(턴 19 진단 · 발견 F1~F12 · 재현 2건) · W1~W7 · 게이트 G-1~G-4 |
| v1.1 | 2026-10-08 | 게이트 답 — 권고안 전부(G-1 (b) · G-2 (c) · G-3 (a) · G-4 (a)) · W5 (3) 보류(145 파일 충돌) · 착수 |
| v1.2 | 2026-10-08 | 착지 — W1(백틱 카탈로그 실존 검사 · D-297 부기) · W3(0행 앞 턴 승계 + 안내 · 행 숨김 기각 · D-055 부기) · W4(K1·K3·K4 · K2 3건 · 모의 DB 실행 검증 81/81) · W5 (1)(2)(오라클 7 · 탐침 10) · 계획 끝 회귀 rc=0 · §10 착지 보고 · 상태 WIP(파일명 태그 유지) |
| v1.3 | 2026-10-08 | W5 (3) — `--check-oracle --out` → `<run>/oracle_check/oracle_check.yaml`(값 0 · 누출 관문 · run 루트 무손상) · W7 회수 문구·145 반입 확인 3·반출 실물 대조 포인트 · 회귀 rc=0 |
| v1.4 | 2026-10-08 | 완료 — 4회차 실행 확인 · 잔여 이관(W2·W7·F12 → `plans/149` W2·W6·W5 · L1 → 103) · 파일명 태그 제거(`146-itam-run3-results-correction.md`) |

## 10. 착지 보고(v1.2 · 2026-10-08)

### 10.1 W1 — 백틱 카탈로그 실존 검사(G-1 (b) · D-297 부기)

- `src/sql_validation.py` `check_catalog_references` 신설 — **백틱 엔진(`_uses_backtick_quotes`)만** `validate_sql` 5·6단계에서 호출. 공용 `_extract_table_names`·`_extract_alias_map`·`_validate_columns`는 그대로라 PostgreSQL·DB2 판정은 비트 동일(`prompt_render_diff --ci` 차이 0).
- 실존 판정 기준은 선별 스키마가 아니라 상태에 실은 조회 대상 전체 카탈로그(`_catalog_columns`). 없는 컬럼이면 그 테이블 실제 컬럼 이름 목록(상한 80)을 오류에 싣는다. 선별 밖이지만 실존하는 테이블·컬럼은 통과. 조회 대상 밖 테이블은 「존재하지 않는 테이블 참조」.
- 리터럴·주석을 걷은 본문으로 스캔 · 암시 별칭 · 파생 원천 · 비한정 한글 컬럼 · 다중 테이블 합집합 대조 · 별칭 붙은 콤마 조인(교정 2).
- 카탈로그 부착: 단일 `schema_analyzer`(정의 선별 AND 백틱 엔진) · 멀티 `multi_db_executor._analyze_schema` — 검사 호출도 `_validate_sql_simple`에 대칭. 헬퍼 `table_selection.catalog_columns`(선별 로직 무변경 — G-2 (c)).
- 테스트 `tests/test_nodes/test_plan146_backtick_validation.py` 129 통과 · xfail 1(L1).

### 10.2 W3 — 0행 앞 턴 승계(G-3 (a) · D-055 부기)

- **끊긴 지점**(체크포인터 붙인 2턴 재현으로 확정): ① 2단 경로는 `generated_sql`을 top-level로 올리지 않아 `context_resolver`의 `previous_sql`이 빈 문자열 ② 앞 턴이 0행이라 결과 요약·엔티티도 빔 → 2턴 계획 맥락(`intent_planner._build_context_block`)에 앞 턴 질의·조건이 전혀 닿지 않음 → 조건 없는 조인(3회차 3,748행 모양). 3·4단은 이전 SQL은 붙지만 결정적 가드 없음.
- **교정**: `src/utils/empty_antecedent.py`(신규). 0행으로 끝난 턴의 원 질의·실행 SQL을 `conversation_context["zero_row_turn"]`에 기록(2단 `result_aggregator` · 3·4단 `output_generator` · 3단 존 턴 `semantic_router`) → 다음 턴에 지시어가 있고 앞 턴이 서버를 직접 지목하지 않았으면 `context_resolver`가 `empty_antecedent`를 싣고 계획 맥락에 앞 질의·SQL을 넣는다 → 실행 뒤 결정적 판정: 다시 0행이면 「앞 질문(「…」)에서 찾은 서버가 없습니다 — 그 조건을 이어 다시 조회했지만 해당하는 서버가 없습니다」 뒤에 붙임 · 앞 조건 값을 하나도 잇지 않고 넓혔으면 행은 그대로 두고 「… 앞에서 찾은 서버가 아니라 전체 대상일 수 있습니다」를 앞에 붙임.
- **기각한 설계**: 넓힌 결과 행을 숨기는 가드(구현자가 범위 밖으로 넣음) — 라우트 폴백으로 SSE `row_count`·CSV에 행이 새고(H-1), 비교 값 추출 오탐으로 맞는 행을 숨김(H-2) → 행 유지 + 안내로 전환(D-055 부기에 기록). 되돌리는 비용: 안내 문구 2종과 판정 함수 1개 — 숨김으로 바꾸려면 라우트 3곳 대칭 처리가 필요.
- 지시어 판정은 단어 경계 요구(「로그서버」 배제) · 「전체/모든」이 앞에 오면 배제(교정 2). 비교 값은 LIKE 우선(`CONCAT`·`||` 포함) → 없으면 `=`·`<>`·`IN` · 날짜·숫자·형식 문자열 제외.
- MLX(두 평면 mlx 확인 · 1회 · 계획 분해 프롬프트만 전·후): 전 「직전 조회 결과의 서버들에 대해 지원 종료일 조회」(조건 없음) → 후 앞 턴 조건 재조회 + 지원 종료일 조회. 전체 앱 2턴은 미실행(MCP·Redis 미기동 · 로컬 ITAM 2테이블).
- 매뉴얼(D-255): 사용자 매뉴얼 U-34 주의 문구 추가 · `build` · `tests/test_manual` 555 통과.
- 테스트 `tests/test_orchestration/test_plan146_demonstrative_zero_rows.py` 55 통과.

### 10.3 W4 — 지식 자산(K8 보류)

- 원천 `testdata/itam_bench/closed/knowledge/` — `guide.yaml`(g03 · g05 · g06) · `prompt_section.yaml`(s04 · s07 · s09 · 신규 s10 이름 일치 규칙) · `descriptions.yaml` · **신규 `examples.yaml`(K2 3건: `k2-eos-6months` · `k2-server-count-by-os` · `k2-server-count-by-group-path`)**.
- **모의 DB 실행 검증 수행**: 3회차 카탈로그로 모의 DB(3308 · 108테이블 · 합성 2,160행) → 별도 포트 MCP(127.0.0.1:9199 · 프로세스 환경변수만 — 공유 9099·`mcp_server/.env` 무변경) → `--validate-knowledge` 81/81 통과 · db_unverified 0. 행 값은 합성이라 실행 가능 여부만 증명한다.
- 빌드 기준은 1회차 `20261006-152938` 유지(재기저 안 함 — W7 사용자 결정) · `prompt_render_diff --ci` 차이 0 · `test_plan140_w3_build_assets.py`는 `KnowledgeDeps(client_factory=_no_db, sql_checker=…)`로 DB 무접속 고정.

### 10.4 W5 (1)(2) — 오라클 7 · 탐침 10

| 오라클 | 비교 | 비고 |
|---|---|---|
| ITAM-107 | `value` + `count_rows_ok` | `count`는 COUNT 1행 정답을 「1 대 N」으로 떨어뜨려 이탈(ITAM-19 정본과 같은 모양) |
| ITAM-108 | `keyset`(`서버호스트명`) | `tcdmsif80` 이번 분기 유지보수 종료 |
| ITAM-109 | `keyset` | `tcdmsif79` HW·SW 지원 종료 6개월 |
| ITAM-110 | `keyset` | `취득년월일` 5년 이상(경계 포함) |
| ITAM-111 | `argmax` | `tcdmsif80` `취득금액` 상위 3 — 3위 동점 흡수(샌드박스 정본 ITAM-13 모양)로 이탈 |
| ITAM-112 | `keyset`(`운영체제타입내용`) | |
| ITAM-116 | `count` | 경로 값은 키로 쓰지 않음 |

- `host_key` 별칭에 `sevrHostName`(로더 린트의 general 이름 요건)·`server_hostname`·`server_host` 추가. 폐쇄망 `서버호스트명`은 unclassified라 키 값은 건수로만 남는다(`keys_recorded: false` — 샌드박스와 같은 방식).
- 탐침 `testdata/itam_bench/scenarios.closed.probe.yaml` + `ITAM-146-P01`~`P06`·`P07A`·`P07B`·`P08`·`P09`(10건 — P07은 두 질문이라 분할). 핵심어는 「통합인증」만(행 수로 핵심어를 가르려고).
- 반출 영향(145 `judge.sanitize_detail`): oracle이 된 7건의 상세 값 중 general 아닌 칸은 가짜 값으로 반출 — 107·111은 판정 결과만 신뢰(판정은 원값) · 탐침은 행 수만이라 무영향.
- 모의 DB `--check-oracle` 기본·탐침 DB 오류 0 · `--dry-run --env closed` 오라클 7턴 · 관측 12턴 통과.

### 10.5 검증

- 계획 끝 회귀 1회: `python scripts/regress.py --base 89773af --files <회귀 대상 파일 29>` → rc=0 · 본체 통과 4,708 · 실패 0 · 에러 0 · 건너뜀 11 · arch·overfit 통과 · ruff·mypy 이번 diff 줄 위반 0 · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행`(산출물 `logs/regress/20261008-110335-16181`).
- verifier: W1(Critical 0 · H1 (a)~(d)·M1·L2 교정 1 · H2 교정 2) · W3(H-1·H-2 → 숨김 기각 · M-1·M-2·M-4·L-3 교정 1 · M-R1·R-L2 교정 2) · W4·W5(Critical 0 · M1 별칭·L1 g05 과장 교정 · H1은 145 소관 → W7).

### 10.6 팀 리드 판단(검토 요청 사항)

| 사항 | 판단 · 근거 |
|---|---|
| W1 비한정 컬럼 대조를 별칭 있는 단일·다중 테이블(합집합)까지 확대(지시 밖) | **수용** — verifier가 실제 ITAM SQL 43개 모양으로 재확인해 오탐은 H2(콤마 조인) 하나였고 교정 2로 해소. 되돌리기는 `check_catalog_references`의 비한정 분기를 단일 무별칭으로 좁히면 된다 |
| W1 체크포인트에 `_catalog_columns` 동반 저장(수십 KB) | **수용** — 백틱 엔진·정의 선별일 때만 싣는다(폴스타 0). 크기가 문제되면 노드 지역 조회로 옮긴다 |
| W1 L2: 카탈로그 부착 조건을 `active_db_engine`(백틱)으로 — 3단 ITAM은 미부착 | L1(3·4단 엔진 폴백)과 같은 묶음 잔여(103 소관) |
| W3 넓힌 결과 행 숨김 → 행 유지 + 앞 안내 | G-3 (a)의 「넓히지 않음」은 **계획 단계**에서 앞 턴 조건을 승계해 지킨다(계획 맥락에 앞 질의·SQL 주입). 실행이 그래도 넓혔을 때 행을 숨기면 라우트 폴백 누출(H-1)·오탐 숨김(H-2)이 나서, 침묵 대신 사용자에게 넓혔음을 명시하는 쪽을 택했다 — D-055 부기 |
| W3 `src/routing/semantic_router.py` +6줄(지시 밖) | 3단 존 턴도 0행 기록을 잇게 하는 대칭 배선 — 수용 · 회귀 대상에 포함 |
| W4 K3 `tcdmsif72.용도내용` 재서술(지시 밖) | **수용** — 새 g06(서비스 단서로 쓰지 않음)과의 모순 해소 |
| W4 빌드 기준 run | 3회차로 빌드하면 재기저(유사어 +258줄 · 스키마 99테이블 변경)가 일어나 143 §9 기록대로 1회차 `20261006-152938` + 지식 오버레이로 빌드. 145가 기준 run을 바꾸면 그 결정을 따른다(W7 재기저 결정) |
| `test_plan140_w3_build_assets.py` 실패 귀속 | K2 3건 추가 → 정적 전용 재빌드에서 `asset_sql_checker` 없이 K2가 거절돼 `query_examples`만 달랐다. deps에 `asset_sql_checker`를 넘기는 최소 수정(145 동의) · 읽는 1회차 run에 `substitutions.yaml` 없음 실측 |
| `test_itam_bench_catalog.py`·`test_itam_bench_closed_kit.py` 3건 | 오라클·탐침 추가로 기대값 변경 — 정본 파일 수는 증가 사유 반영 · observe 단언은 「observe는 사용자 질문만」 의도 유지 + oracle 7건 명시 목록(145 비편집 확인) |
| W5 `host_key`(108~110 keyset · 키 열 `서버호스트명` unclassified) | **keyset 유지** — 판정은 원값으로 내부망 안에서 하고, 키 열이 general이 아니면 `keys_recorded: false`로 반출은 건수·가짜 값만 남는다(`__main__.py`·`judge.py`) → 「반출 키는 general만」 규칙과 충돌 없음. count로 내리면 판정력만 잃는다. 엄격 적용 여부는 사용자가 뒤집을 수 있다 |
| W5 탐침 핵심어 「통합인증」 하나 | **충분(이번 회차)** — 행 수만으로는 핵심어를 가를 수 없어 하나로 둔다. 다른 서비스(103 「인터넷뱅킹」)는 P01~P05 복사로 5회차에 |

### 10.7 잔여

- W2: 4회차에서 「선별 밖 테이블 사용 턴」 재계수 → 남으면 G-2 (a)/(b).
- ~~W5 (3) `--check-oracle --out`~~ — v1.3 완료(§10.8).
- W6(FROM 테이블 이름 가림)·F12: 145 소관.
- keyset 오라클의 빈 머리 경계 사례(키 열 해석 실패): plans/145에서 교정.
- W3 L-1(`sequential_runner`)·L-2(1단 `deep_agent`) 경로는 범위 밖.
- L1: 3·4단 단일 그래프는 엔진 폴백(postgresql)으로 W1 검사가 돌지 않는다(103 소관).
- W3: 2단 토큰 스트림에 앞 안내 없음(R-L1) · `=`만 쓴 앞 턴 일부 미탐 · 공용 `refers_to_demonstrative_server`의 「로그서버」 오탐(별건) · 지시어 명사구가 여럿이면 첫 명사구 앞만 본다.
- W1 미검사 형태(놓칠 뿐 오탐 없음): 하위 질의 안 별칭 · `db.tbl.col` 3단 한정 · 단어 문자가 아닌 백틱 식별자.

### 10.8 W5 (3) — `--check-oracle --out`(v1.3)

- `scripts/itam_bench/__main__.py`: `oracle_check_document`(값 0 본문) · `write_oracle_check`(`LeakGate` + `rd.write_gated`) · `cmd_check_oracle`은 판정 범주를 모아 `--out`이 있을 때만 쓴다 · 기존 `--out` 인자(`--validate-knowledge`)를 재사용하고 help를 두 용도로.
- 위치: `<out>/oracle_check/oracle_check.yaml` + 하위 전용 `leak_check.json` — run 루트 `run.json`·`leak_check.json`을 덮지 않는다(`knowledge_evidence/`와 같은 방식). 쓰기 전 옛 결과를 지워 관문 실패 때 옛 결과가 새것처럼 남지 않는다.
- 내용: `kind`·`run_id`·`env`·`scenario_file`·`anchor_at`·`summary`(oracles·ok·zero_rows·error·failed)·`oracles[]`(id·scenario·turn·verdict·rows·elapsed_ms). 판정은 고정 어휘(`ok`/`zero_rows`/`error`) — DB 오류 문구는 원문·가린 문구 모두 싣지 않는다(화면은 종전대로 가린 문구).
- 관문 대상: 관문 검사 대상은 `write_gated`에 넘기는 staged 묶음 자체(`checked_files == ["oracle_check.yaml"]`) · `.yaml` 잎 단위 5규칙. 별도 반출 허용 목록은 없고 `--sync`는 `schema_catalog.yaml`·`trace.jsonl`만 읽어 `redact.py` 변경 불필요.
- 145의 `PendingTurn`·`finish_turns` 2단계 발급은 `--run` 경로에만 있고 `--check-oracle`은 `run_oracle`만 부른다 — 비해당.
- 종료 코드는 종전(실패·0행이면 1) · `--out` 관문 실패도 1. `--out` 없으면 화면·종료 코드 바이트 동일(편집 전 코드로 기대값을 먼저 고정).
- 테스트 `tests/test_scripts/test_plan146_w5_oracle_out.py` 6건(신규). 탐침 머리 주석에 회수 방법 1줄(`testdata/itam_bench/scenarios.closed.probe.yaml`).
- 회귀: `python scripts/regress.py --base 89773af --files scripts/itam_bench/__main__.py tests/test_scripts/test_plan146_w5_oracle_out.py testdata/itam_bench/scenarios.closed.probe.yaml` → rc=0 · 본체 통과 904 · 실패 0 · arch·overfit 통과 · ruff 위반 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록(145의 `run_turn` 반환 주석 변경 사유 — 이번 diff 아님): `[전체 회귀 권고] 사유: 공개 시그니처·반환 형태 변경(--wide 자동 적용) — scripts.itam_bench.__main__.run_turn(반환 주석 변경 dict[str, Any]→PendingTurn)` / `→ 사용자가 요청하면: python scripts/regress.py --full`.
- 잔여: `--out` 상대 경로는 `RESULTS_ROOT`로 풀지 않는다(경로를 끝까지 적는다) · 같은 `--out`에 탐침·오라클을 차례로 쓰면 나중 것이 교체한다(W7에 분리 지시).
