# LLM 기반 DB 쿼리 생성(Text-to-SQL) 품질 향상 연구 보고서

> 대상: `collectorinfra` — 폴스타(Polestar) 인프라 모니터링 DB에 대한 자연어→SQL 에이전트
> 작성일: 2026-07-13
> 범위: (1) 현행 쿼리 생성 기능 검토, (2) Text-to-SQL 학술 문헌 조사, (3) 격차 분석 및 개선 방안, (4) 우선순위 로드맵

---

## 0. 요약 (Executive Summary)

`collectorinfra`의 핵심 기능 중 하나는 **모니터링 솔루션(Polestar) DB에 대해 한국어 자연어를 SQL로 변환·실행**하는 것이다. 현재 구현은 LangGraph 7-노드 파이프라인 위에 스키마 캐시, 유사어 사전, EAV 값기반 조인 지침, 규칙기반 검증, 실행에러 기반 재시도를 갖춘 **견고한 단일-후보(single-candidate) 생성기**다.

학술 문헌(Spider/BIRD/Spider 2.0 벤치마크 기반 SOTA 기법)과 대조하면, 현행 구현이 이미 잘 반영한 부분(스키마 표현, 도메인 지식 주입, 자기수정 루프)과 **아직 도입하지 않아 품질을 크게 끌어올릴 여지가 있는 부분**이 명확히 갈린다. 가장 효과가 큰 미도입 기법은 다음 3가지다:

1. **다중 후보 생성 + 선택/투표(Candidate Generation & Selection)** — 단일 후보 대신 여러 SQL을 생성하고 실행결과·선택 에이전트로 최적안을 고름. CHASE-SQL·XiYan-SQL·DAIL-SQL이 공통으로 채택한 SOTA의 핵심 레버.
2. **동적 예시 선택(Dynamic Few-shot Selection)** — 현재 하드코딩된 템플릿을 질의 유사도(skeleton similarity) 기반으로 골라 주입.
3. **실행 기반 검증·선택(Execution-guided Selection & Correction)** — 규칙기반 검증에 더해, 후보를 실제 실행한 결과의 일관성으로 선택하고 오류를 교정.

이들은 모두 **폐쇄망·읽기전용·EAV 스키마**라는 이 프로젝트의 제약과 양립하며, 대부분 프롬프트/오케스트레이션 레이어 변경만으로 도입 가능하다(모델 파인튜닝 불필요).

---

## 1. 현행 쿼리 생성 기능 검토

### 1.1 파이프라인 구조

`src/graph.py`의 LangGraph 상태머신은 다음 순서로 동작한다:

```
input_parser → schema_analyzer → query_generator → query_validator
             → query_executor → result_organizer → output_generator
```

회귀(retry) 라우팅 (`route_after_*`, 각 최대 3회):
- `query_validator` 검증 실패 → `query_generator` 회귀
- `query_executor` 실행 에러 → `query_generator` 회귀 (에러 컨텍스트 첨부)
- `result_organizer` 데이터 부족 → `query_generator` 회귀

상위에는 `semantic_router`(DB 라우팅) → `intent_planner`/`deep_agent`(멀티 인텐트·멀티 DB 오케스트레이션)가 존재한다.

### 1.2 쿼리 생성 노드 (`src/nodes/query_generator.py`, 701 LOC)

| 구성 요소 | 구현 방식 |
|-----------|-----------|
| **스키마 표현** | 테이블별 컬럼 목록 + 타입 + `[PK]`/`[FK→…]`/`NOT NULL` + 컬럼 설명 + `[유사: …]` + 3건 샘플 데이터 + `-- JOIN 금지(사유)` 주석 |
| **프롬프트 템플릿** | 3종: 범용 / Polestar 전용 / Polestar-알람 전용 — `active_db_id`와 `routing_intent`로 선택 |
| **Few-shot 예시** | 프롬프트 내부에 **하드코딩**된 Template A(EAV 피벗), Template B(성능지표 통계) |
| **도메인 지식 주입** | `column_synonyms`, `resource_type_synonyms`, `eav_name_synonyms` (한국어↔DB값 매핑) |
| **EAV 조인 지침** | `_structure_meta`의 `value_joins`로 FK 없는 테이블 간 **값기반 브릿지 조인** 패턴 강제 |
| **재시도 컨텍스트** | 이전 SQL + 에러 메시지를 프롬프트에 주입("위 에러를 수정한 새 SQL 생성") |
| **멀티턴 맥락** | 직전 SQL·결과 요약을 참조용으로 주입 |

### 1.3 스키마 분석 노드 (`src/nodes/schema_analyzer.py`, 1188 LOC)

- **3단계 캐시**: 메모리(TTL 5분) → Redis(fingerprint) → 파일 폴백 → DB 전체 조회
- **LLM 기반 테이블 선택**: `parsed_requirements`로 관련 테이블/컬럼 선별
- **유사어 기반 `allowed_tables` 동적 보완** — 단, 실측 407 테이블 규모의 polestar_b0에서 유사어 전량 유입 시 system prompt가 104K > FabriX 한도 95K로 폭증한 이력이 있어, **이번 질의에 실제 등장한 유사어의 테이블만** 게이트하고 상한(`_MAX_SYNONYM_SUPPLEMENT_TABLES=15`)을 둔다.

### 1.4 쿼리 검증 노드 (`src/nodes/query_validator.py`, 763 LOC)

**LLM 미사용, 순수 규칙기반**:
1. sqlparse 파싱 가능성 → 2. SELECT 문 여부(DML/DDL 차단) → 3. 금지 키워드 → 4. 인젝션 패턴 → 5. 참조 테이블 존재 → 6. 참조 컬럼 존재 → 7. LIMIT 절(자동 보정) → 8. 성능 위험 패턴

### 1.5 강점과 관찰된 한계

**강점**
- 읽기전용·인젝션 방어·감사 로깅 등 **프로덕션 안전성**이 탄탄하다.
- 스키마 표현에 타입·PK/FK·샘플·설명·유사어를 이미 포함 → 학술 문헌의 "풍부한 스키마 표현" 권고와 정합.
- 도메인 지식(유사어·EAV 조인)을 명시적으로 주입 → BIRD가 강조하는 "external knowledge evidence"와 동일 철학.
- 자기수정 루프와 멀티턴 맥락이 존재.

**한계 (문헌 대비 미도입 영역)**
- **단일 후보 생성**: `temperature` 샘플링·후보 다양화·투표/선택 로직이 전혀 없다(코드 확인).
- **정적 few-shot**: 예시가 템플릿 종류별로 고정 — 질의별 적응 선택이 없다.
- **SQL 복잡도 분기 부재**: intent 수준 분해(`intent_planner`)는 있으나, 단일 질의를 easy/nested-complex로 분류해 프롬프트를 바꾸는 로직은 없다.
- **자기수정이 실행에러 트리거에 한정**: 실행은 되지만 의미가 틀린 SQL(silent wrong result)을 잡는 critic/reflection·실행결과 일관성 검사가 없다.
- **필터 리터럴의 값 검색 부재**: WHERE 조건에 쓸 실제 DB 셀 값(예: `resource_type`, EAV `NAME` 값)을 체계적으로 검색해 주입하지 않는다(유사어·샘플로 간접 보완).
- **Text-to-SQL 전용 평가 하네스 부재**: AIOps 노이즈/RCA 벤치마크는 있으나, 쿼리 생성 품질을 실행정확도(EX)로 측정하는 골드셋·회귀 테스트가 없다.

---

## 2. Text-to-SQL 학술 문헌 조사

최근 LLM 기반 Text-to-SQL 연구는 Spider(200 DB, 10K 질의)와 BIRD(95개 실제 DB, 33.4GB, 12,751 질의쌍)를 중심으로, 최근에는 기업급 스키마를 다루는 Spider 2.0으로 무게중심이 이동했다. 핵심 기법을 6개 축으로 정리한다.

### 2.1 태스크 분해 (Decomposition)

**DIN-SQL** (Pourreza & Rafiei, NeurIPS 2023, arXiv:2304.11015)은 Text-to-SQL을 (1) 스키마 링킹, (2) 질의 분류·분해, (3) SQL 생성, (4) 자기수정의 4개 모듈로 분해하고, 질의를 easy/non-nested-complex/nested-complex로 분류해 클래스별 프롬프트를 사용한다. 복잡 질의에는 NatSQL 중간표현을 쓴다. 논문 초록 기준, 이 분해만으로 단순 few-shot 대비 성능이 유의미하게(초록 표현: "roughly" 수준) 향상된다(널리 인용되는 Spider 테스트 EX 85.3%는 리더보드 보고치로, 도입 판단 시 원문 표를 직접 확인할 것).

**MAC-SQL** (COLING 2025)은 Selector(스키마 축소)·Decomposer(질의 분해)·Refiner(실행기반 교정) 3개 전문 에이전트로 협업 구조를 만든다.

### 2.2 스키마 링킹 & 스키마 표현 (Schema Linking & Representation)

- **RESDSQL** (Li et al., 2023)은 스키마 링킹과 SQL 스켈레톤 파싱을 분리하고, 랭킹 강화 cross-encoder로 스키마 항목을 필터링.
- **C3** (Dong et al., 2023)은 Calibration 모듈로 LLM이 불필요/오류 컬럼을 고르는 경향을 줄여 그런 오류를 11% 감소.
- **M-Schema** (XiYan-SQL, arXiv:2411.08599)는 DDL보다 풍부한 반구조화 스키마 표현으로, 특수 토큰(`【DB_ID】`, `# Table`, `【Foreign Keys】`)과 **데이터 타입·PK 표시·컬럼 설명·예시 값**을 포함한다. DDL은 설명·예시값이 없어 유사 컬럼을 구분하지 못한다는 것이 M-Schema의 출발점.
- **"The Death of Schema Linking?"** (Maamari et al., NeurIPS 2024 workshop, arXiv:2408.07702)는 반전 관점을 제시: 최신 추론형 모델은 문맥 내 무관 컬럼(noise)을 잘 걸러내므로, **스키마가 컨텍스트에 들어가면 링킹을 생략하고 전체 스키마를 넣는 편이** 과잉필터링으로 필수 컬럼을 누락하는 위험보다 낫다. 단, 스키마가 컨텍스트를 초과하는 실제 데이터웨어하우스에서는 여전히 다단계 검색이 필요하며, 그 경우 컨텍스트를 **최대한 활용해 top-K 컬럼을 남기라**고 권고. 대안으로 Augmentation(컬럼 설명·힌트 보강)·Selection(다중 후보 중 일관 후보 선택)·Correction(실행 피드백 교정)을 제시.

### 2.3 In-Context Learning & 예시 선택 (Example Selection)

**DAIL-SQL** (Gao et al., 2023, arXiv:2308.15363)은 질문 표현·예시 선택·예시 구성을 체계적으로 비교한 뒤, (a) 구조 지식을 SQL문으로 인코딩, (b) **스켈레톤 유사도 기반 예시 선택**, (c) 토큰 효율을 위해 예시에서 도메인 지식 제거를 통합해 Spider 리더보드 실행정확도 86.6%(초록 명시)를 달성. 질문 스켈레톤이 원 질문보다 의도를 더 잘 포착한다는 것이 핵심 통찰.

### 2.4 다중 후보 생성 + 선택 (Candidate Generation & Selection)

**CHASE-SQL** (Pourreza et al., Google Cloud·Stanford, ICLR 2025, arXiv:2410.01943)은 test-time compute를 활용한 멀티에이전트 프레임워크로 4개 구성요소를 둔다: **Value Retrieval → Candidate Generation → Query Fixing → Selection Agent**. 후보 생성에 (1) divide-and-conquer, (2) 실행계획 기반 CoT, (3) 인스턴스별 합성 few-shot 예시의 3가지 다른 방식을 써 다양한 후보를 만들고, **파인튜닝된 이진 선택 LLM의 쌍대비교**로 최적안을 고른다. 이 선택 방식이 단순 self-consistency 투표보다 견고하며 BIRD 테스트 EX 73.0%로 제출 시점 1위.

**XiYan-SQL** (arXiv:2411.08599)은 M-Schema + 스키마 링킹 + **다중 생성기 앙상블**(ICL 생성기 + 파인튜닝 생성기) → self-refine → 후보 선택 에이전트 구조로 Spider 89.65%, BIRD dev 최대 75.63%.

### 2.5 자기수정 & 자기일관성 (Self-Correction & Consistency)

- **Self-Refine / Self-Debugging**: LLM이 실패한 실행의 자연어 설명으로 스스로 오류를 교정.
- **실행 기반 self-consistency**: 여러 SQL을 생성·실행하고 결과가 가장 일치하는 후보를 투표로 선택.
- **SD+SA+Voting**: 투표 전에 스키마 인지 규칙(유효 조인, 절 순서)으로 후보를 먼저 필터링.
- **CSC-SQL** (IJCNLP-AACL 2025): 강화학습으로 교정형 self-consistency를 학습.
- 서베이는 self-correction의 'refine'(교정)은 성숙했으나 'critic'(코드 설명·정답 판정)은 개선 여지가 크다고 지적. self-consistency는 적응성·성능이 좋지만 LLM 호출 비용이 늘어난다는 트레이드오프도 명시.

### 2.6 값 검색 & 도메인 지식 (Value Retrieval & External Knowledge)

BIRD는 12,751 질의쌍과 함께 **external knowledge evidence**(도메인 힌트, 값 매핑, 유사어 정의)를 제공하며, 이 의미 문맥이 정확도에 직접 기여함을 보였다. CHASE-SQL의 첫 단계도 Value Retrieval로, WHERE 조건에 필요한 실제 DB 값을 추출하는 것이다.

> 참고: 위 수치·순위는 각 논문/리더보드의 발표 기준이며 모델·데이터·시점에 따라 달라진다. 절대 수치보다 **기법의 상대적 기여**를 설계 근거로 삼는다.

---

## 3. 격차 분석 — 현행 구현 vs 문헌 SOTA

| 축 | 문헌 SOTA 기법 | 현행 `collectorinfra` | 격차 | 도입 난이도 |
|----|----------------|----------------------|------|------------|
| 스키마 표현 | M-Schema(타입·PK·설명·예시값·FK 토큰) | 타입·PK/FK·설명·유사어·3건 샘플·JOIN금지 주석 | **작음** (이미 대부분 반영) | — |
| 스키마 링킹 | 양방향/멀티패스 검색, 과잉필터 회피 | LLM 선택 + 유사어 보완(상한 15) | 중간 (407테이블 → 필터 불가피, 검색 정밀도 개선 여지) | 중 |
| 예시 선택 | 스켈레톤 유사도 동적 선택(DAIL-SQL), 인스턴스별 합성(CHASE) | 템플릿별 **하드코딩** | **큼** | 저~중 |
| 태스크 분해 | easy/complex 분류·분해(DIN-SQL), divide&conquer | intent 수준만 분해, SQL 복잡도 분기 없음 | 중간 | 중 |
| 다중 후보+선택 | 다양한 생성기 + 선택 에이전트/투표(CHASE·XiYan) | **단일 후보** | **가장 큼** | 중 |
| 자기수정 | self-refine, 실행기반 재교정, critic | 실행에러 트리거 재시도만 | 중간 | 저 |
| 실행기반 선택 | 결과 일관성 투표, EX 기반 랭킹 | 없음 | 중간 | 중 |
| 값 검색 | DB 셀 값 검색(LSH/키워드)로 WHERE 리터럴 확보 | 유사어·샘플로 간접 | 중간 | 중 |
| 평가 하네스 | Spider/BIRD EX 자동측정 | Text-to-SQL 전용 골드셋·EX 측정 없음 | **큼(측정 불가 = 개선 검증 불가)** | 저 |

**핵심 진단**: 현행 구현의 스키마 표현·도메인 지식 주입·안전성은 이미 문헌 권고 수준에 근접하다. 반면 **"한 번에 하나의 SQL만 생성하고, 그것이 실행에러가 나야만 재시도한다"**는 구조가 품질 상한을 규정한다. 문헌 SOTA가 공통적으로 넘어선 지점이 바로 이 단일-후보·에러기반-재시도의 한계이며, 여기가 이 프로젝트의 가장 큰 개선 레버다.

---

## 4. 개선 방안 (프로젝트 코드 매핑)

각 방안을 현행 파일/노드에 매핑하고, 폐쇄망·읽기전용·EAV 제약과의 양립성을 명시한다.

### R1. Text-to-SQL 실행정확도(EX) 평가 하네스 구축 — *선행 필수*
- **근거**: Spider/BIRD가 EX(Execution Accuracy)를 표준 지표로 삼은 이유 — 개선을 측정 못 하면 개선을 검증할 수 없다.
- **구현**: `sqls/act/` 로그와 `testdata/`를 활용해 폴스타 대표 질의 50~150건의 (질의, 골드 SQL, 골드 결과) 셋을 큐레이션. 파이프라인을 배치 실행해 EX·재시도 횟수·토큰·지연을 리포트하는 하네스를 `tests/`에 추가.
- **효과**: 이후 모든 개선(R2~R7)의 A/B 근거 확보. **가장 먼저** 해야 한다.
- **난이도**: 낮음 (기존 실행 인프라 재사용).

### R2. 다중 후보 생성 + 실행기반 선택 (최우선 품질 레버)
- **근거**: CHASE-SQL·XiYan-SQL·DAIL-SQL의 공통 SOTA 요소. "The Death of Schema Linking"의 Selection 전략과도 일치.
- **구현**:
  - `query_generator`에서 `temperature>0`로 N개(예: 3~5) 후보 생성, 또는 서로 다른 프롬프트 전략(범용/divide-and-conquer/실행계획 CoT)으로 다양화.
  - `query_validator`(규칙기반)로 후보를 사전 필터링(SD+SA 방식) → 통과 후보를 읽기전용으로 **실행**해 결과 일관성 투표 또는 LLM 선택 에이전트로 최적안 선택.
  - 그래프에 `candidate_generator → candidate_selector` 노드를 삽입하거나, 기존 `query_generator`를 다중호출로 확장.
- **제약 양립**: 모두 SELECT·읽기전용 → 후보 실행 안전. 비용은 LIMIT·타임아웃(기존 30s/10K행)으로 통제.
- **효과**: 문헌상 가장 큰 EX 향상 기여. **비용↑**(LLM 호출·실행 N배) → 복잡 질의에만 조건부 적용 권장.
- **난이도**: 중간.

### R3. 동적 Few-shot 예시 선택 (하드코딩 템플릿 대체)
- **근거**: DAIL-SQL의 스켈레톤 유사도 선택 — 고정 예시보다 질의 적응 예시가 우수.
- **구현**: 검증 통과된 (질의→SQL) 이력을 예시 풀로 축적(감사 로그 활용). 신규 질의의 스켈레톤/임베딩 유사도로 top-k 예시를 뽑아 프롬프트에 주입. Redis 캐시(기존 인프라)에 예시 임베딩 저장.
- **제약 양립**: 폴스타 EAV 특유의 피벗/브릿지조인 패턴을 실제 성공사례로 재사용 → 환각 감소.
- **효과**: 토큰 효율 + 정확도. 기존 하드코딩 Template A/B는 폴백으로 유지.
- **난이도**: 낮음~중간.

### R4. 실행에러 외 자기수정 강화 (critic / self-refine)
- **근거**: 서베이가 지적한 'critic' 개선 여지. Self-Debugging·Self-Refine.
- **구현**: 재시도 프롬프트를 단순 에러 첨부에서 **구조화 진단**으로 확장 — (a) 실행되었으나 0행/의심결과일 때 LLM에 "이 SQL이 질의 의도를 충족하는가"를 판정시키는 reflection 단계, (b) 오류 유형별(조인오류·컬럼오류·집계오류) 맞춤 힌트. DESEM식 종료조건으로 무한루프 방지(기존 max 3 유지).
- **제약 양립**: `result_organizer`의 "데이터 부족→회귀" 경로를 강화하는 자연스러운 확장.
- **효과**: silent wrong result(실행되지만 의미 오류) 포착.
- **난이도**: 낮음.

### R5. 값 검색(Value Retrieval)으로 필터 리터럴 정확화
- **근거**: CHASE-SQL 1단계, BIRD external-knowledge.
- **구현**: WHERE에 쓰일 후보 값(예: `resource_type='server.Server'`, EAV `NAME='Hostname'`)을 질의 키워드로 인덱싱된 실제 DB distinct 값에서 검색해 프롬프트에 주입. 폴스타는 이 값 집합이 유한·안정적이므로 캐싱 용이.
- **제약 양립**: 기존 `resource_type_synonyms`/`eav_name_synonyms`를 **실측 값 검색**으로 승격.
- **효과**: 존재하지 않는 resource_type 환각(Plan 25에서 관찰된 실패 유형) 직접 억제.
- **난이도**: 중간.

### R6. SQL 복잡도 분기 (질의 분류)
- **근거**: DIN-SQL의 query classification — easy엔 단순 few-shot, complex/nested엔 분해 CoT.
- **구현**: `query_generator` 진입 전 경량 분류(단순 조회 vs 다중조인·피벗·중첩집계)로 프롬프트 전략 선택. 단순 질의는 단일 후보(저비용), 복잡 질의만 R2 다중후보 활성화 → **비용 통제**.
- **제약 양립**: 기존 `intent_planner` 분해와 계층 분리(인텐트 vs SQL 복잡도).
- **효과**: 비용 대비 품질 최적화.
- **난이도**: 중간.

### R7. 스키마 표현·링킹 정밀화
- **근거**: M-Schema, "Death of Schema Linking"의 top-K 최대활용 권고.
- **구현**: (a) 현행 스키마 텍스트를 M-Schema식 구조 토큰으로 정규화(이미 타입·PK·샘플 보유 → 형식만 표준화). (b) 407테이블 규모에선 링킹이 불가피하나 **과잉필터 회피** — 유사어 보완 상한(15)이 필수 테이블을 누락시키지 않는지 R1 하네스로 검증하고, 양방향/2단계(PreSQL 기반) 검색 도입 검토.
- **효과**: 유사 컬럼 혼동·필수 테이블 누락 감소.
- **난이도**: 낮음(표현) ~ 중간(링킹).

---

## 5. 우선순위 로드맵

| 단계 | 항목 | 목적 | 예상 효과 | 비용 |
|------|------|------|-----------|------|
| **0** | R1 EX 평가 하네스 | 측정 기반 확보 | 개선 검증 가능 | 낮음 |
| **1** | R4 자기수정 강화 + R3 동적 예시 | 저비용·고효율 개선 | 중~높음 | 낮음 |
| **2** | R5 값 검색 + R7 스키마 정밀화 | 환각·컬럼오류 억제 | 중간 | 중간 |
| **3** | R6 복잡도 분기 → R2 다중후보+선택 | 최상위 품질(복잡 질의) | 높음 | 높음(조건부) |

**권고 순서의 논리**: 먼저 측정 수단(R1)을 만들고, 코드 변경이 가장 작은 자기수정·동적예시(R4·R3)로 빠른 이득을 확보한 뒤, 환각을 직접 억제하는 값검색·스키마(R5·R7)를 넣고, 마지막으로 비용이 큰 다중후보+선택(R2)을 복잡도 분기(R6)와 함께 조건부로 적용해 **비용을 통제하면서 품질 상한을 끌어올린다**.

이 순서는 프로젝트가 이미 강한 부분(안전성·도메인 지식·스키마 표현)을 그대로 두고, 문헌 SOTA가 넘어선 유일한 미도입 축(단일후보·에러기반재시도)을 단계적으로 해소한다.

---

## 6. 참고 문헌

1. Li et al., 2023. *Can LLM Already Serve as A Database Interface? A BIg Bench for Large-Scale Database Grounded Text-to-SQLs (BIRD)*. NeurIPS 2023.
2. Yu et al., 2018. *Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL*.
3. Pourreza & Rafiei, 2023. *DIN-SQL: Decomposed In-Context Learning of Text-to-SQL with Self-Correction*. NeurIPS 2023. arXiv:2304.11015.
4. Gao et al., 2023. *Text-to-SQL Empowered by Large Language Models: A Benchmark Evaluation (DAIL-SQL)*. arXiv:2308.15363.
5. Li et al., 2023. *RESDSQL: Decoupling Schema Linking and Skeleton Parsing for Text-to-SQL*.
6. Dong et al., 2023. *C3: Zero-shot Text-to-SQL with ChatGPT*.
7. Wang et al., 2024/2025. *MAC-SQL: A Multi-Agent Collaborative Framework for Text-to-SQL*. COLING 2025.
8. Pourreza et al., 2024. *CHASE-SQL: Multi-Path Reasoning and Preference Optimized Candidate Selection in Text-to-SQL*. ICLR 2025. arXiv:2410.01943.
9. XiYan-SQL: *A Multi-Generator Ensemble Framework for Text-to-SQL*. arXiv:2411.08599 (M-Schema).
10. Maamari et al., 2024. *The Death of Schema Linking? Text-to-SQL in the Age of Well-Reasoned Language Models*. NeurIPS 2024 TRL Workshop. arXiv:2408.07702.
11. Sheng & Shuai, 2025. *CSC-SQL: Corrective Self-Consistency in Text-to-SQL via Reinforcement Learning*. Findings of IJCNLP-AACL 2025.
12. Talaei et al., 2025. *CHESS: Contextual Harnessing for Efficient SQL Synthesis*. ICML 2025 Workshop.
13. *Next-Generation Database Interfaces: A Survey of LLM-based Text-to-SQL*. arXiv:2406.08426.
14. *A Survey on Employing Large Language Models for Text-to-SQL Tasks*. ACM Computing Surveys, 2025.
15. Madaan et al., 2023. *Self-Refine: Iterative Refinement with Self-Feedback*. NeurIPS 2023.

> **인용 검증 노트**: 위 문헌의 제목·arXiv 식별자·발표 지면은 웹 검색으로 확인했다. 본문의 벤치마크 수치 중 DAIL-SQL(Spider EX 86.6%), CHASE-SQL(BIRD EX 73.0%), XiYan-SQL(Spider 89.65% / BIRD dev 75.63%)는 arXiv 초록에서 직접 확인했다. DIN-SQL의 "Spider EX 85.3%"는 초록에 명시되지 않은 리더보드 보고치이므로 본문에서 완화 표기했다. 모든 수치는 각 논문/리더보드의 발표 기준이며 모델·시점에 따라 달라지므로, 채택 의사결정 시 원문 결과표를 직접 대조할 것.
