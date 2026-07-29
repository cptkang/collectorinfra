# 표현·명칭 표준화(canonicalization) 문헌 조사 (Standardization Literature Review)

> 작성일: 2026-07-29
> **목적**: 사용자 질의 표현·컬럼명·엔티티 명칭을 표준화하여 쿼리 생성에 활용하는 기법의 기술 문헌·자료 존재 여부와 적용 가능성 확인. Plan 67 트랙 N의 근거 문서.
> **방법**: 웹 문헌 조사(WebSearch/WebFetch). 실 LLM 호출 검증 없음.
> **내부 선행 문서**: `docs/synonym_management_analysis.md`(IP-1~IP-5 — 현행 동의어 관리 적정 판정), `docs/text2sql_quality_research.md`, `docs/deterministic_sql_composition_review.md`
> **채택 결과(2026-07-29 사용자 인터뷰)**: Plan 67 **트랙 N 신설** — **N2 질의 이력 검색 + N4 계층 taxonomy 채택**, N1 임베딩 후보 생성기 재배치·N3 공급원 교체는 **미채택**, 임베딩 모델은 **측정(IP-4) 선행 후 결정**.

---

## 1. 총평

2025~2026 문헌의 일관된 결론: **"표준화 지식을 어디에 두느냐"보다 "선언 지점을 하나로 만들고, 커버리지 밖에서는 조용히 틀리지 말 것"이 더 큰 효과 변수.** 우리 진단("별칭 지식 4곳 분산" — Plan 67 R1)은 문헌이 지목하는 실패 모드와 일치하고, "LLM 자동 등록 금지"(Known Mistakes) 방침도 근거가 탄탄하다.

## 2. 실증 수치 종합

| 기법 | 효과 | 출처 | 신뢰도 |
|---|---|---|---|
| 시맨틱 레이어 문서 제공 | **+17~23%p** (45.5~50.5→67.7~68.7), 모델 3종 간 차이 통계적 무의미 | arXiv:2604.25149 (Cube 벤치마크) | 논문 |
| dbt SL vs Text-to-SQL | 90.0→98.2 (Sonnet 4.6) / 84.1→100 (GPT-5.3) | dbt 2026 벤치마크 | 벤더 |
| 데이터 모델 3개 추가 | Text-to-SQL 64.5→84.1 (모델링 개선은 양쪽 모두 도움) | dbt 2026 | 벤더 |
| **질의 이력 검색 편입** | **+40.2pt** (52.1→92.3, SEDE 857문항) — 이번 조사 단일 기법 최대 | arXiv:2606.28387 (Schema-First Retrieval) | 논문 |
| 시맨틱 검색 vs 어휘 매칭(BM25) | +32.8pt (테이블 recall@5) | arXiv:2606.28387 | 논문 |
| entity+context retrieval | +4.76%p (59.86→64.62 ablation) | arXiv:2405.16755 (CHESS) | 논문 |
| DB 콘텐츠 기반 질문 재작성 | +12.41%/+5.38% EX (DAIL-SQL/C3 대비) | DART-SQL, Findings ACL 2024 | 논문 |
| 스키마 링킹(대규모 DB 한정) | 3,000+ 컬럼에서 recall <40%→~90% | arXiv:2511.17190 (AutoLink) | 논문 |
| 선택적 사람 개입(되묻기) | +9.51% (Spider) | HLR-SQL, Information Systems 2026 | 논문 |

## 3. 항목별 요지

### 3.1 스키마 링킹 — "Death of Schema Linking" 이후
- arXiv:2408.07702(2024): 스키마가 컨텍스트에 들어가면 링킹 생략이 낫다(BIRD 71.83%). arXiv:2511.17190(2025)가 "소규모 한정"으로 반박 — 컬럼 3,000개 초과에서 recall 절벽. **우리 스키마 규모면 링킹보다 별칭 품질이 병목.**
- 사전 vs 임베딩 직접 비교 논문은 부재 — arXiv:2606.28387의 베이스라인은 BM25이므로 "어휘 매칭보다 임베딩 우위"까지만 읽을 것.

### 3.2 시맨틱 레이어의 명칭 표준화 — 실증 최강
- 상용 5종(Snowflake Cortex semantic view `synonyms`+Verified Query Repository / Databricks Genie agent metadata / Cube `meta.ai_context` / dbt MetricFlow YAML+Git 거버넌스 / DataHub·OpenMetadata glossary) **예외 없이 "선언 1곳, 소비 N곳"** — Plan 67 R1의 문헌 근거.
- Databricks 공식 권고 우선순위: **SQL expression > example SQL > text instruction(최후수단)** — D-035 결정적 조립 원칙과 동일 결론.
- dbt 벤치마크의 핵심 발견: 커버리지 밖에서 Text-to-SQL은 "신호 없이 그럴듯한 오답", SL은 "답할 수 없음 명시" — **침묵적 폴백 금지 원칙의 실증**.

### 3.3 질의 전처리 정규화
- 계보: Berant & Liang(ACL 2014) → Overnight(ACL 2015, canonical→natural 방향 주의). 최신 실증 DART-SQL(Findings ACL 2024): **DB 실값·실명을 근거로** 질문 재작성 + 실행 기반 정제 — "실측 우선" 원칙과 정합. Plan 67 트랙 S 루프의 도구 탐색과 효과 중복 가능성 있어 별도 채택하지 않음.

### 3.4 값 표준화·엔티티 링킹
- BRIDGE(EMNLP 2020) anchor text가 원형. CHESS(arXiv:2405.16755) `retrieve_entity` 레시피: LSH 인덱스 → **임베딩 top-10 후보** → 임계 컷 → **편집거리 최소 1개 결정적 선택**. **임베딩=후보 생성기, 결정=결정적 규칙** 배치는 D-035를 지키며 임베딩 이득을 취하는 구조(N1 제안의 근거 — 이번엔 미채택).
- 모호성 되묻기: AmbiSQL(arXiv:2508.15276)·BIRD-INTERACT(arXiv:2510.05318, 수치 미확보)·HLR-SQL(+9.51%).

### 3.5 한국어 특화 — 문헌 공백
- **한국어 text2sql 의역 동의어 정규화 정면 논문 없음** — 우리가 선례가 될 영역. Spider-KO(번역 데이터셋), 학습 증강 방향 국내 논문 1건(대한산업공학회지 2022).
- 도구: kiwipiepy(음운 이형태 통합·사용자 단어 점수), Nori(user_dictionary), KURE(한국어 검색 특화 임베딩, MTEB-ko 리더보드로 비교 가능).
- "가동률→사용률"은 어간이 달라 **형태소·자모 퍼지로 원리적 도달 불가** — 수동 등재 또는 임베딩만이 해법.

### 3.6 동의어 자동 구축의 안전장치 — 자동 등록 금지 방침을 지지
- 위험 근거: query drift(arXiv:2605.00560 — 재작성 누적의 해로운 임계점, 랭커 피드백 게이팅 제안), LLM 확장의 도메인 오류(arXiv:2509.07794), Tunkelang의 precision 붕괴 사례(`glasses↔eyeglasses` → "wine glasses"에 안경 — 우리 도메인이면 "메모리 사용률"↔"디스크 사용률" 오연결 사고).
- 안전 패턴 5종: ①생성은 자동·등재는 사람 라벨 게이트(SIGIR eCom 2019) ②쿼리 로그 마이닝(WWW 2016 — 실사용자 표현이 근거라 오염 루프 원천 부재) ③검증된 쿼리(VQR)를 원천으로(Snowflake 2025-12 프리뷰) ④되묻기 응답을 사전 성장 채널로 ⑤평면 동의어 대신 계층 taxonomy(OpenSource Connections — **N4 채택 근거**).

### 3.7 표준화의 역설 3가지
- **(가) precision-recall 역설**: 사전이 클수록 오탐 표면적 증가 → 완화 = 계층 taxonomy + 필드 부스팅.
- **(나) ontology drift**: "업데이트 못 하는 메타데이터는 자산이 아니라 부채"(분기당 1/3 변경 보고 — 원문 미대조, 신뢰도 중간). **핵심 용어 우선 안정화, 전면 조화는 뒤로** — N4 단계화 근거.
- **(다) 커버리지 절벽**: 표준화 강화 → "정확하거나 답 못 하거나"로 양극화. dbt·Cube 벤치마크 공통 판단은 "이 양극화가 바람직" — 조용한 오답보다 명시 실패가 낫다.

## 4. 미확인·주의 (가공 인용 방지)

- arXiv:2408.07702 링킹 recall 세부, AmbiSQL·BIRD-INTERACT 정확도, SIGIR eCom 2019 분류기 precision, RASL(arXiv:2507.23104) 세부 수치: **미확보**
- "분기당 온톨로지 1/3 변경": 검색 집계 출처, 원문 미대조
- Cortex Analyst 85~90%, dbt 수치: 벤더 자체 측정
- BGE-M3 한국어 수치: 개인 블로그 출처, 신뢰도 낮음

## 5. 주요 출처

- 스키마 링킹: arxiv.org/abs/2408.07702 · arxiv.org/html/2511.17190v1 · arxiv.org/pdf/2606.28387 · arxiv.org/abs/2405.16755
- 시맨틱 레이어: arxiv.org/abs/2604.25149 (github.com/cubedevinc/semantic-layer-benchmark) · docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026 · docs.snowflake.com(cortex-analyst VQR·optimization) · docs.databricks.com(agent-metadata·genie best-practices) · docs.cube.dev(ai-context) · docs.datahub.com(glossaryterm) · docs.open-metadata.org(glossary)
- 질의 재작성: aclanthology.org/2024.findings-acl.120/ · nlp.stanford.edu/pubs/wang-berant-liang-acl2015.pdf
- 값 링킹·모호성: aclanthology.org/2020.findings-emnlp.438/ · arxiv.org/pdf/2508.15276 · arxiv.org/pdf/2510.05318 · sciencedirect.com/science/article/pii/S0306437925001565
- 한국어: huggingface.co/datasets/huggingface-KREW/spider-ko · jkiie.org/xml/32143/32143.pdf · bab2min.github.io/kiwipiepy · github.com/nlpai-lab/KURE · github.com/su-park/mteb_ko_leaderboard
- 안전장치: sigir-ecom.github.io/ecom2019/ecom19Papers/paper20.pdf · microsoft.com/en-us/research/wp-content/uploads/2016/06/p1429-he-1.pdf · arxiv.org/pdf/2605.00560 · arxiv.org/pdf/2509.07794 · dtunkelang.medium.com/real-talk-about-synonyms-and-search-bb5cf41a8741 · opensourceconnections.com/blog/2016/12/23/elasticsearch-synonyms-patterns-taxonomies/
