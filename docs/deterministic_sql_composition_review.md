# 결정적 SQL 생성(예시·컬럼 사전정의 + 조합) 기법 검토 및 Plan 61 비교

> 대상: `collectorinfra` — Polestar 인프라 모니터링 DB 자연어→SQL
> 작성일: 2026-07-13
> 관련: `docs/text2sql_quality_research.md`, `docs/synonym_management_analysis.md`, `plans/61-text2sql-candidate-selection.md`
> 질문: "잘 만들어진 예시 쿼리로 컬럼을 미리 정의하고, 사용자 요청에 맞게 컬럼을 조합하여 최적 쿼리를 생성하는 결정적 기법"의 정체·효과·현행 계획 대비 이점

---

## 0. 요약

사용자가 설명한 기법은 학술·산업 문헌에서 **두 갈래의 확립된 접근**에 정확히 대응한다:

1. **템플릿/스케치 + 슬롯필링(slot filling)** — 미리 정의된 SQL 골격(sketch)에 슬롯(컬럼·조건값)을 두고, LLM/모델은 "어느 템플릿 + 어느 슬롯값"만 고르며 SQL은 **결정적으로 복원**된다. (Finegan-Dollak 2018 템플릿 베이스라인, RYANSQL, SeaD, RingSQL)
2. **시맨틱 레이어 / 메트릭 레이어(semantic layer)** — 컬럼을 **dimension·measure·metric으로 미리 정의**하고, LLM은 자연어를 이 구성요소의 **조합으로만 분해**하며, 결정적 컴파일러(dbt MetricFlow, Cube, arXiv 2606.31041의 SMQ 방식)가 SQL을 생성한다. LLM이 조인·집계를 직접 쓰지 않으므로 **잘못된 조인·집계 자체가 원천 불가능**하다.

**핵심 발견**: `collectorinfra`는 이 기법의 **선언적 원재료를 이미 80% 보유**하고 있다. `config/db_profiles/*.yaml`의 `known_attributes`(dimension 카탈로그), `value_joins`/`direct_join`(조인 정의), `query_examples`(검증된 예시 쿼리), 그리고 하드코딩 Template A/B(컬럼 열거형 CASE-WHEN 피벗)가 그것이다. **빠진 것은 단 하나 — 이 선언들을 LLM이 텍스트로 모방하는 대신, "컬럼 선택"만 LLM이 하고 SQL을 조립하는 결정적 조합 엔진이다.**

**판정**: 이 기법은 **폴스타처럼 스키마가 안정적이고(EAV·resource_type 값집합이 유한) 질의 패턴이 반복적인** 단일 도메인에 매우 효과적이며, 프로젝트의 D-035(결정적 규칙=판단·LLM=보조) 철학과 가장 정합하는 접근이다. Plan 61의 트랙 A(다중 후보 생성)가 "여러 개를 만들어 고르는" 통계적 접근이라면, 이 기법은 "애초에 틀릴 수 없게 조립하는" 구조적 접근으로 **상호 보완**된다. 따라서 Plan 61에 **트랙 C**로 추가할 것을 권고한다.

---

## 1. 문헌 근거

### 1.1 템플릿 + 슬롯필링 (결정적 복원)

- **Finegan-Dollak et al. 2018** 템플릿 베이스라인: 데이터셋에서 가장 흔한 *템플릿*(조건 피연산자를 슬롯으로 치환한 프로그램 스케치)을 추출하고, 모델은 (a) 어떤 템플릿을 쓸지, (b) 질문의 어떤 토큰으로 슬롯을 채울지 두 가지만 예측한다. 한계로 **학습에서 본 템플릿 구조로만 일반화**된다는 점이 지적된다.
- **RYANSQL** (Choi et al., Computational Linguistics 2021): 중첩 쿼리를 비중첩 SELECT 집합으로 변환하고 **스케치 기반 슬롯필링**으로 각 SELECT를 합성. Spider exact-match 58.2% 달성(제출 시점 리더보드 1위).
- **SeaD**: 슬롯필링 하위작업 결과에서 **전체 SQL을 결정적으로 복원**한다.
- **RingSQL** (2026): 스키마 독립 템플릿을 스키마별 값으로 채운 뒤 **결정적으로 SQL로 변환**하고, LLM은 자연스러운 질문 생성에만 사용.

핵심 트레이드오프(문헌 공통): 슬롯필링은 **구조 정확도·안전성이 높지만 커버리지가 템플릿 집합에 갇힌다**. 조건값(리터럴) 예측이 약점으로 지적된다.

### 1.2 시맨틱 레이어 / 메트릭 레이어 (LLM은 조합만, 컴파일은 결정적)

- **Semantic-Layer-Mediated NL2SQL Agent** (arXiv 2606.31041, "A Semantic-Layer-Mediated Agent for Natural Language to SQL over Heterogeneous Enterprise Databases"): 시맨틱 의도와 물리적 SQL 실행을 분리한다. 에이전트는 원시 스키마 위에서 SQL을 직접 생성하지 않고, 큐레이션된 시맨틱 레이어에 대해 컴팩트한 중간표현(**SMQ, Semantic Model Query**)으로 추론하며, **결정적 컴파일러가 각 SMQ를 방언별 SQL로 변환**하여 검증된 빌딩블록을 제공하고 에이전트가 이를 최종 쿼리로 조합한다. SQLite·BigQuery·Snowflake 백엔드를 지원하며, 초록 기준 Gemini 3 Pro로 **547-task Spider2-snow 벤치마크에서 실행정확도 94.15%, 공식 리더보드 3위**를 보고한다.
- **dbt Semantic Layer(MetricFlow)**: 온톨로지(metric·dimension·entity·관계)를 정의하면 LLM의 일은 자연어를 **올바른 metric·dimension 조합으로 분해**하는 것으로 축소되고, 쿼리 생성은 MetricFlow가 **결정적으로 처리**한다. dbt는 "**모델이 옳은 metric·dimension만 고르면 쿼리는 정의상 정확**하며, 잘못된 조인·집계를 만들 수 없다"고 서술한다.
- **시맨틱 레이어 페어 벤치마크**(arXiv 2604.25149, "Semantic Layers for Reliable LLM-Powered Data Analytics"): ClickHouse 상 Contoso 소매 데이터셋 100문항에 대해 3개 프론티어 모델을 (a) 스키마만, (b) 스키마 + 4KB 시맨틱 문서(측정·규약·모호성 해소 규칙) 두 조건으로 페어 평가. 문서 추가 시 정확도가 **+17~23%p** 향상되며(초록 명시), 문서가 있을 때 세 모델이 통계적으로 구분 불가(67.7~68.7%)해진다 — 개선이 "모델을 더 똑똑하게" 만든 게 아니라 스키마가 인코딩하지 못한 비즈니스 시맨틱을 컨텍스트로 공급한 구조적 결과임을 시사.
- 산업 벤더 서술(Wren AI, Denodo, Dremio/Snowflake Cortex 등): 시맨틱 모델(정의된 metric·dimension·조인·**동의어**·설명)을 경유하면 커버리지 내 질의는 **결정적으로** 답하고, 커버리지 밖은 사람 검토 또는 **우아한 거부**로 처리 — "그럴듯하지만 틀린 SQL을 조용히 신뢰된 답으로 내놓지 않는다".

> 수치는 각 논문/벤더 발표 기준(데이터셋·모델·시점 의존)이며 절대 목표로 이식하지 않는다. 설계 근거는 **기법의 구조적 이점**(틀린 조인/집계 원천 차단)이다.

---

## 2. 현행 `collectorinfra`가 이미 가진 것 (실측)

| 시맨틱 레이어 구성요소 | 현행 자산 | 위치 |
|----------------------|-----------|------|
| **Dimension/속성 카탈로그** | `known_attributes`(name·description·synonyms) — 예: `OSType`, `MODEL`, `LOGICALCORE`, `Hostname`… | `config/db_profiles/*.yaml` |
| **Measure(성능지표)** | Template B의 cpu/mem/fs `min/avg/max` × `Utilization`/`MaxIORate` | `src/prompts/query_generator.py` |
| **조인 정의** | `value_joins`(값기반 브릿지), `direct_join`(resource_conf_id=configuration_id) | `config/db_profiles/*.yaml` |
| **검증된 예시 쿼리** | `query_examples`(question·sql·explanation) | `config/db_profiles/*.yaml` |
| **컬럼 열거형 골격** | Template A(EAV 피벗), Template B(성능통계) — 컬럼별 CASE-WHEN | `src/prompts/query_generator.py` |
| **동의어(business glossary)** | Redis `synonyms:*` + `known_attributes.synonyms` | `redis_cache.py`, db_profiles |
| **시간 분기 규칙** | 시/일/월 → `cmm_metric_stat_[h,d,m]`, stat_date 포맷 | 프롬프트 |

**결론**: 폴스타 도메인의 dimension·measure·join·example·glossary가 **선언적으로 이미 정의**되어 있다. 이는 시맨틱 레이어의 정의부(定義部)에 해당한다.

**빠진 것(격차)**: 이 선언이 전부 **LLM 프롬프트에 텍스트로 주입되어 LLM이 자유 모방**할 뿐이다. 즉 —
- LLM이 Template A 전체를 복사하며 요청되지 않은 컬럼까지 SELECT하거나(과다), 필요한 CASE-WHEN을 누락하거나(과소),
- `value_joins`를 무시하고 금지된 id 조인을 생성(Plan 33에서 반복 관찰),
- 존재하지 않는 resource_type/속성명을 지어냄(Plan 25에서 관찰).

**결정적 조합 엔진**(LLM은 dimension 이름만 고르고, 엔진이 정의된 CASE-WHEN·조인·GROUP BY를 조립)이 있으면 위 오류들은 **구조적으로 발생 불가능**해진다.

---

## 3. Plan 61과의 비교

| 관점 | Plan 61 트랙 A (다중 후보+선택) | 결정적 조합(트랙 C 후보) |
|------|-------------------------------|--------------------------|
| 접근 성격 | **통계적** — 여러 후보 생성 후 실행결과로 최적 선택 | **구조적** — 틀릴 수 없게 조립 |
| 오류 처리 | 사후(생성된 오류를 선택 단계에서 걸러냄) | 사전(오류를 애초에 생성 불가) |
| 커버리지 | 넓음(LLM 자유 생성) | 정의된 dimension/example 범위 내 |
| 비용 | 높음(LLM·실행 N배) | 낮음(LLM 1회 조합 판단 + 결정적 컴파일) |
| 환각(미존재 컬럼/조인) | 규칙필터·실행으로 사후 차단 | **원천 차단** |
| 커버리지 밖 질의 | LLM이 시도(품질 불확실) | 우아한 거부 또는 LLM 폴백 |
| D-035 정합성 | 부분(선택은 결정적, 생성은 LLM) | **완전(생성 자체가 결정적)** |
| 구현 재료 | 신규 노드 다수 | **기존 db_profiles·Template 재활용** |

**상호 보완**: 두 접근은 배타적이지 않다. 이상적 구성은 —
1. **커버리지 내 질의**(서버 설정/성능지표 조회 — 폴스타 질의의 대다수) → **트랙 C 결정적 조합**으로 저비용·무환각 처리.
2. **커버리지 밖·복합 질의** → **트랙 A 다중 후보 생성**으로 폴백.
3. 두 경로 모두 **트랙 B 동의어**로 용어→dimension 링킹을 강화하고, **E1 하네스**로 EX를 측정.

즉 **트랙 C가 1차 방어선(대부분의 반복 질의를 결정적으로), 트랙 A가 2차(나머지를 통계적으로)** 역할을 하며, 커버리지·비용·안전성을 동시에 최적화한다.

---

## 4. 권고 — Plan 61에 트랙 C 추가

### C1. 시맨틱 모델 스키마 정식화 (선언부 승격)
- `known_attributes`·`value_joins`·Template A/B를 **기계가독 시맨틱 모델**(dimension: 이름·CASE-WHEN 표현식·resource_type·값컬럼 / measure: 집계식·definition_name / join: 정의 / 시간축)로 정규화. 대부분 기존 YAML 재구조화.

### C2. 중간표현(IR) + 결정적 컴파일러
- 폴스타판 **SMQ**(선택 dimension/measure 목록 + 필터 + 시간단위)를 정의. LLM은 자연어 → SMQ(**컬럼 이름 선택**)만 담당. `src/nodes/`에 결정적 컴파일러(SMQ → 방언별 SQL: PostgreSQL LIMIT / DB2 FETCH FIRST, EAV 피벗·value_joins 자동 조립, GROUP BY 자동)를 신설.
- **효과**: 잘못된 조인·집계·미존재 컬럼 원천 차단. Template A/B의 "전체 복사" 문제 해소(요청된 dimension만 조립).

### C3. 커버리지 판정 + 폴백 라우팅
- 질의가 시맨틱 모델 커버리지 내인지 결정적 판정 → 내부면 C2 컴파일, 밖이면 트랙 A(다중 후보) 또는 사람 검토로 라우팅(문헌의 "우아한 거부" 원칙).

### C4. 예시 쿼리 → 조합 검증 셋
- `query_examples`를 C2 컴파일러의 **골든 회귀 테스트**로 재활용(E1 하네스 연동): "이 질문 → 이 dimension 조합 → 이 SQL"이 결정적으로 재현되는지 검증.

### 리스크·통제
- **커버리지 한계**(템플릿 갇힘, 문헌 공통 지적) → C3 폴백으로 완화, dimension 카탈로그를 사람 승인 루프(D-012)로 점진 확장.
- **초기 정의 비용** → 기존 db_profiles 재활용으로 대폭 절감. 3개 폴스타 프로필에 이미 정의부 존재.
- **회귀** → 기본 OFF 플래그(`TEXT2SQL_SEMANTIC_COMPOSE=off`), 커버리지 내에서만 진입.

### 우선순위 제언
트랙 C의 **저비용·고안전** 특성과 **기존 자산 재활용** 가능성을 고려하면, 폴스타 질의의 반복 패턴(서버 설정·성능지표 조회)에 대해 **트랙 A보다 먼저 또는 병행 착수**하는 것이 비용 대비 효과가 크다. 트랙 A(다중 후보)는 커버리지 밖 복합 질의의 2차 방어선으로 유지한다.

---

## 부록. 인용 검증 노트

- 본문 수치는 arXiv 초록에서 직접 확인했다: RYANSQL(Spider 58.2%, 이전 SOTA 대비 +3.2%p, 제출 시점 리더보드 1위 — arXiv 2004.03125), 시맨틱-레이어 에이전트(Gemini 3 Pro, 547-task Spider2-snow EX 94.15%, 리더보드 3위 — arXiv 2606.31041, **2026-07-13 재확인**), 시맨틱 문서 페어 벤치마크(+17~23%p, 문서 有 시 67.7~68.7% — arXiv 2604.25149, **2026-07-13 재확인**).
- 초기 초안에서 arXiv 2606.31041을 "spider2-daquv-quvi"로 지칭한 것은 실재하지 않는 명칭이라 **삭제**했다(정식 명칭은 "Semantic-Layer-Mediated NL2SQL Agent", 중간표현은 **SMQ, Semantic Model Query**). 반면 "547-task Spider2-snow·리더보드 3위"는 앞선 창에서 미검증으로 보고 삭제했으나, **2026-07-13 초록 재확인 결과 실제로 초록에 명시된 내용이어서 복원**했다(과잉 정정 교정).
- Finegan-Dollak 2018·SeaD·RingSQL·dbt MetricFlow·Cube는 기법의 존재·구조 서술 근거로 인용하며, 개별 정량 수치는 본 문서의 설계 판단 근거가 아니다(설계 근거는 "틀린 조인/집계 원천 차단"이라는 구조적 이점).
- 모든 수치는 각 논문 발표 기준(데이터셋·모델·시점 의존)이며 폴스타 목표로 이식하지 않는다. 실제 목표는 Plan 61의 E1 하네스로 폴스타에서 측정 후 설정한다.
