# 61. Text-to-SQL 쿼리 품질 고도화 — 다중 후보 생성 + 실행기반 선택 + 동의어 매칭 + 결정적 조합 (Candidate Selection + Synonym + Deterministic Composition)

> 작성일: 2026-07
> **대상 기능**: 자연어→SQL 생성(`src/nodes/query_generator.py`), 검증(`query_validator.py`), 실행(`query_executor.py`), 그래프 라우팅(`src/graph.py`), **SQL 생성 우회 경로(`src/orchestration/subagents.py` 인라인 파이프라인, `src/nodes/multi_db_executor.py` 멀티 DB 자체 생성 — §2.1)**, **동의어 매칭(`src/document/field_mapper.py::_synonym_match`, `src/schema_cache/redis_cache.py`, `src/nodes/schema_analyzer.py`), 시맨틱 모델 정의(`config/db_profiles/*.yaml`, `src/prompts/query_generator.py` Template A/B)**
> **선행/근거 문서**: `docs/text2sql_quality_research.md`(R2 = 최우선 품질 레버, R1 = 측정 선행), `docs/synonym_management_analysis.md`(동의어 관리 적정성 판정 + IP-1~IP-5), `docs/deterministic_sql_composition_review.md`(결정적 조합 기법 검토 + 트랙 C 근거)
> **관련 결정**: D-003(읽기전용 — 후보 실행 안전성 근거), D-035(결정적 규칙=판단·LLM=보조 — 트랙 C의 핵심 정합), D-012(매핑-우선 — 동의어/dimension 사람 승인 루프), D-019(Redis 캐시), D-051(유사어/토큰 스케일 가드)
> **신규 결정(본 계획에서 부여, 착수 시 등재)**: **D-072**(Text-to-SQL EX 평가 하네스), **D-073**(다중 후보 생성 — 복잡도 조건부), **D-074**(실행기반 후보 선택 — 규칙필터→결과일관성 투표→LLM 선택 폴백), **D-075**(동의어 매칭 고도화), **D-076**(시맨틱 모델 기반 결정적 SQL 조합 — 커버리지 내 결정적 컴파일, 밖은 폴백)
> ※ 번호 규칙(Known Mistakes 2026-06-25·06-29): `grep -roE "D-0[0-9]{2}" docs/ plans/`. 본 계획은 **D-072~D-076** 예약. 구현·등재 직전 `## D-` 헤더와 「변경 이력」 표를 재확인하여 충돌 시 다음 빈 번호로 재조정하고 사유를 명시한다. 계획 번호도 `ls plans/` 최댓값(60) +1 = **61**.
> **✅ D-번호 재확인(2026-07-13 실측, v12 정정)**: `docs/02_decision.md` **등재** 최댓값 = **D-068**(D-067 폼필 헬퍼 단일출처·D-068 다중리소스 피벗). **예약**은 Plan 61 = D-072~076, Plan 60 = D-077~081(Plan 60이 D-072~076을 Plan 61 예약으로 존중해 그 위 블록으로 이동, Plan 62 로드맵 B-5가 추적). ⇒ **본 계획의 D-072~076 예약은 유효하며 등재분과 충돌 없음.** 착수 시점에 `## D-` 헤더를 한 번 더 확인해 그 사이 신규 등재가 없었는지만 점검한다.
> **상태**: **대부분 구현 — 트랙 A·B·C 착수 완료(2026-07-15, §12 참조)**. E1 하네스(D-072)·트랙 B(E5-1·E5-2 인프라+**런타임 주입**·E5-3 상한 config화+**거버넌스**)·**트랙 C 전체**(E6-1~4, D-076)·**트랙 A 전체**(E2 다중후보·E3 복잡도 게이트·E4 실행기반 선택·3단 폴백, D-073·D-074)** 구현 완료. D-072·D-073·D-074·D-075·D-076 등재. **E5-4(임베딩)만 미착수**(오프라인 모델 반입 게이트). 전 단계 **회귀 없는 옵트인 증분**(전 기능 기본 OFF, 신규 테스트 45건 통과·기존 회귀 0). **E1 실측(2026-07-15, 하네스 실접속 최초 성공)**: 선언 커버리지 76.9% vs **런타임 판정(coverage_router 실측) 34.6%**(선언 inside인데 판정 outside 11건=dimension 확장 후보), **SMQ 생성 정확도 1/6**(§7 예측 슬롯 약점 실증), 실측 EX baseline **LLM 자유생성 42.9% vs semantic ON 28.6~35.7%**(ON 저하는 품질 아닌 골드셋↔시맨틱모델 규약 모순: gp-001~003 gold_sql=`platform.server%` vs gold_smq=`server.Server` 서로소 모집단). performance·alarm 카테고리 EX 0%가 실개선 타깃.
> **개정 이력**:
> - v5(2026-07-13) — `docs/plan61_literature_review.md` 검토 5개 권고 반영: ①트랙 C "환각 0"→"구조적 환각 0 + SMQ 선택 정확도 별도 측정"(§7), ②SMQ 생성 정확도·리터럴 우회 리스크 신설(E6), ③트랙 C←E5-2 값 검색 의존 명시(E6-2), ④E5-3·D-051 상한을 EX 튜닝 파라미터화(Death of Schema Linking 반영), ⑤E1에 SMQ 정확도·커버리지율 측정 축 + 골드셋 대표성 원칙 추가.
> - v6(2026-07-13) — 트랙 A §9 확인항목 사용자 결정 반영: 범위=E1+E2~E4 전체, 다양화=multi_prompt, 선택=hybrid, 비용=무제한(§5 기본값·§6·§7 정합 갱신). arXiv 2606.31041·2604.25149 초록 재확인(94.15%/Spider2-snow 547-task/리더보드 3위, +17~23%p) — §10 보정.
> - v7(2026-07-13) — E5-4 의미검색을 **폐쇄망 확정 설계**로 구체화: ①경량 사내 임베딩 모델 CPU 상주(질의 실시간 임베딩 필수 → 사전계산 반입만으로는 불완전), ②FAISS/numpy 인프로세스 인덱스 우선·Redis 무변경(Redis Stack은 대규모·공유 예외 시), ③정확→퍼지→임베딩 계단식+신뢰도 후보제시, ④E5-1 후 E1 잔여율 측정 게이트. §7·§8·§9-6 정합 갱신.
> - v8(2026-07-13) — 트랙 C §9 확인항목 사용자 결정 반영: ⑦선언부 = 별도 `config/semantic_models/`로 분리(db_profiles 불변·입력소스로만 참조), ⑧SMQ 범위 = 알람까지 확장(패턴 A 서버설정/B 성능지표/**C 알람 정규화조인**), ⑨커버리지 밖 폴백 = **신뢰도 기반 3단**(트랙 A→신뢰도 게이트→사람검토). E6-1·E6-2 패턴 C(CA↔D↔ACTIVE·severity 규칙·alarm_allowed_tables)·E6-3 3단 폴백·§5 플래그·§8 산출물 정합 갱신.
> - v9(2026-07-13) — **§11 구현 참고 자료** 신설: 트랙별 실무 라이브러리·레퍼런스 구현·설계 참조(트랙 A: CHASE-SQL/DIN-SQL/self-consistency/EX 채점, 트랙 B: rapidfuzz/jamo/sentence-transformers/faiss, 트랙 C: sqlglot/MetricFlow·Cube/SMQ Pydantic). 라이선스·폐쇄망 반입·버전 호환은 착수 시 재확인 원칙 명시.
> - v10(2026-07-13) — 정정: ①§11-1 arXiv 2203.11171·1807.03100 제목 arXiv API 확인 표기(미검증 식별자 기재 교정), ②§7 트랙 C 검증기준에 **폴백 신뢰도 게이트** 항목 실제 추가(v8에서 changelog만 수정하고 누락했던 것 반영).
> - v11(2026-07-13) — **구현 적정성 실측 검토 반영(코드 대조)**: ①SQL 생성 경로 4종 실측 신설(§2.1) + **삽입 지점 원칙**(그래프 엣지가 아닌 노드 함수/공유 헬퍼 레벨 — orchestration 인라인 경로·`multi_db_executor` 커버) 확립(§3·E2·E4·§8), ②E6-3 커버리지 판정 노드명 `semantic_router`→**`coverage_router`**(기존 `src/routing/semantic_router.py` 의도 라우터와 이름 충돌), ③E6-2가 기존 결정적 조립 자산 `src/utils/query_gen_common.py`(D-068 `build_multi_resource_pivot_sql` 계열)를 **흡수·일반화**하도록 명시(이중 조립 엔진 병립 금지 — D-067 단일 출처), ④E5-1 적용 지점을 `_synonym_match` 단독→**공유 유틸 + 분산 매칭 지점(최소 폼필+schema_analyzer)**으로 정정(§4-B 실측 갱신 — `_synonym_match`는 폼필 전용, 텍스트 질의는 별도 지점), ⑤실측 보정: validator 규칙 약 11종, 유사어 Redis Hash는 TTL 없는 영구 저장, D-051 상한 15는 모듈 상수 하드코딩, §7에 경로 커버리지 검증 축 추가, sqlglot 반입 불가 시 대안 명시.
> - v12(2026-07-13) — 리뷰 정정: ①중복된 v10 라벨을 v11로 교정(이력 가독성), ②**D-번호 재확인**: 실측 결과 D-072~076은 Plan 61이 예약한 상태로 **미등재·충돌 없음**(Plan 60은 이를 존중해 D-077~081로 이동, Plan 62 로드맵이 추적) — v11 ⑤의 "D-번호 연쇄 충돌 확정" 표현을 "충돌 없음(예약 유효)"으로 정정.
> - v13(2026-07-13) — 재검토 잔여 문구 정합 4건: ①§11-1 "후보 생성/선택 **노드 추가** 활용" 잔존 문구를 §3 삽입 지점 원칙(함수 캡슐화)에 맞게 교정(그래프 분기는 HITL 한정), ②본문 "(v10)" 태그를 changelog 개명(v12 ①)에 맞춰 v11로 일괄 교체, ③E3 "schema_analyzer→query_generator 사이" 문구를 "query_generator 진입부(공유 헬퍼)"로 교정, ④§6 퍼지 과잉매칭 통제의 "가드 유지"→"공유 유틸에 가드 신설"로 교정(E5-1 정합).
> - v14(2026-07-14) — **구현 상태 실측 반영**: §12 신설(항목별 구현/미구현 실측표). E1+트랙 B(E5-1 완료·E5-2/E5-3 부분) 구현 확인, 트랙 A·트랙 C·E5-4 미착수 확인. 상태 라인 "계획(미구현)"→"부분 구현"으로 갱신. D-072~076은 아직 `docs/02_decision.md` 미등재(구현 착수분 D-072·D-075 등재 필요 — §12 잔여 작업).
>
> **구성**: 본 계획은 세 트랙으로 이루어진다 — **트랙 A(E1~E4)** 다중 후보 생성·선택(통계적, 커버리지 밖 2차 방어선), **트랙 B(E5)** 동의어 매칭 보강(질의어→dimension 링킹), **트랙 C(E6)** 시맨틱 모델 기반 결정적 SQL 조합(구조적, 커버리지 내 1차 방어선). 세 트랙은 독립 활성화 가능하며 E1 평가 하네스를 공유한다. 트랙 A(통계적: 여러 개 만들어 고름)와 트랙 C(구조적: 틀릴 수 없게 조립)는 **상호 보완**이며, 이상적으로 트랙 C가 반복 질의 대다수를 결정적으로 처리하고 트랙 A가 나머지를 폴백 처리한다(`deterministic_sql_composition_review.md` §3).

---

## 0. 문헌 기반 심화 검토 (2026-07 업데이트)

> LLM 기반 쿼리 생성 문헌을 재조사·검증하여 본 계획의 트랙 우선순위와 설계 판단을 재점검했다. 아래 수치는 각 논문 **초록에서 직접 확인**한 것이며(데이터셋·모델·시점 의존), 폴스타 목표로 이식하지 않는다 — 근거로 삼는 것은 절대 수치가 아니라 **기법의 구조적 성격**이다.

### 0.1 검증된 문헌 근거

| 문헌 | 핵심 기여 | 본 계획 대응 |
|------|-----------|-------------|
| DIN-SQL (arXiv 2304.11015) | 태스크 분해 + 질의 난이도 분류 + 자기수정 | E3 복잡도 분기, E4 에러기반 재시도(자기수정의 축소형) |
| DAIL-SQL (arXiv 2308.15363, Spider EX 86.6% 초록) | 스켈레톤 유사도 예시 선택, 도메인지식 인코딩 | E2 예시 시드, 트랙 B 링킹 |
| CHASE-SQL (arXiv 2410.01943, BIRD EX 73.0% 초록) | 다중 후보(divide&conquer·실행계획 CoT·합성예시) + 선택 에이전트 | E2 다양화 3방식, E4 선택 |
| XiYan-SQL (arXiv 2411.08599, Spider 89.65%/BIRD dev 75.63% 초록) | M-Schema 표현 + 다중 생성기 앙상블 + self-refine | E2 앙상블(M-Schema 스키마 표현 변경은 본 계획 범위 밖 — E1 옵션으로만 검토) |
| Death of Schema Linking (arXiv 2408.07702) | 추론형 모델은 무관 컬럼 필터링 능함 → 컨텍스트 내면 링킹 생략 가능 | 트랙 B 과잉필터 경계 |
| RYANSQL (arXiv 2004.03125, Spider 58.2%/+3.2%p/리더보드 1위 초록) | 스케치+슬롯필링으로 SQL **결정적 복원** | 트랙 C(템플릿 계열) |
| Semantic-Layer Agent (arXiv 2606.31041, EX 94.15% 초록) | SMQ 중간표현 + **결정적 컴파일러**로 검증된 빌딩블록 조합 | 트랙 C 핵심 설계 |
| 시맨틱 문서 페어 벤치마크 (arXiv 2604.25149, +17~23%p 초록) | 스키마+시맨틱문서가 정확도 유의 향상 | 트랙 C·B 정당화 |

### 0.2 문헌이 확정하는 세 가지 설계 판단

1. **후보 생성은 "다양성"이 핵심이지 "많음"이 아니다** (CHASE-SQL·XiYan-SQL). 단순 `temperature` 재샘플링보다 **서로 다른 추론 경로**(divide&conquer, 실행계획 CoT)가 후보 다양성을 만들어 선택 이득을 낳는다 → E2를 `temperature` 우선이 아니라 **다중 프롬프트 전략 우선**으로 재정렬(§4 E2 개정).
2. **선택은 단순 self-consistency 투표보다 견고한 선택기가 낫다** (CHASE-SQL은 파인튜닝 선택기의 쌍대비교가 투표보다 우수하다고 보고). 단 폐쇄망·파인튜닝 부재 제약상 본 계획은 **결정적 결과일관성 투표를 1차, LLM 쌍대비교를 폴백**으로 두되, 실행 불가/동수 시의 LLM 판정 품질이 병목이 될 수 있음을 명시(§6 리스크 추가).
3. **시맨틱 레이어(트랙 C)는 정확도 문제를 "작업 재정의"로 푼다** (arXiv 2604.25149·2606.31041). LLM에게 "SQL을 쓰라"가 아니라 "검증된 dimension을 조합하라"를 시키면 잘못된 조인·집계가 원천 불가능해진다. 이는 다중 후보(사후 필터)보다 **근본적**이며, 폴스타처럼 정형·반복 질의 도메인에서 특히 효과적이다.

### 0.3 트랙 우선순위 재정의 (직전 검토 반영)

초기 계획은 세 트랙을 대등하게 서술했으나, 문헌 심화 검토와 폴스타 도메인 특성(안정적 스키마·유한 값집합·반복 질의)을 종합하면 **트랙 C를 주력, 트랙 A를 조건부 폴백**으로 두는 것이 옳다:

- **트랙 C = 1차 방어선(주력)** — 커버리지 내 정형 질의(서버 설정·성능지표 = 폴스타 질의 대다수)를 결정적·무환각·저비용으로 처리. 기존 자산(`known_attributes`·`value_joins`·`query_examples`·Template A/B) 재활용으로 구축비용 낮음. D-035와 완전 정합.
- **트랙 B = 링킹 계층(병행)** — 트랙 C의 dimension 선택·트랙 A의 후보 입력 품질을 동시에 좌우. 저비용·무회귀.
- **트랙 A = 2차 방어선(조건부)** — 커버리지 밖 복합 질의만 다중 후보로 처리. **E1 하네스로 커버리지 밖 비율을 측정한 뒤**, 그 비율·중요도가 비용(LLM·실행 N배)을 정당화할 때 착수. 작으면 현행 단일 생성 폴백으로 충분하며 트랙 A는 보류.

> **판단 근거**: 트랙 A와 C를 지금 둘 다 투기적으로 구현하는 것은 비효율이다. C를 먼저 세워 커버리지를 측정하면, A의 필요성과 적정 투자 규모가 데이터로 결정된다(과설계 방지 + D-035 결정적 근거 우선).

---

## 1. 왜 이 항목을 우선하는가 (효과 판단)

`docs/text2sql_quality_research.md`의 격차 분석 결과, 현행 쿼리 생성기의 품질 상한을 규정하는 구조적 한계는 **"한 번에 SQL 하나만 생성하고, 실행에러가 나야만 재시도한다"**는 단일-후보·에러기반-재시도 구조다. 이 한계를 넘는 데는 두 가지 상보적 접근이 있다:

- **통계적 접근(트랙 A)** — 여러 후보를 만들어 실행결과로 최적안을 고름. 문헌 SOTA(CHASE-SQL·XiYan-SQL·DAIL-SQL)의 주 레버. 커버리지가 넓지만 비용(LLM·실행 N배)이 크고 silent wrong이 잔존할 수 있다.
- **구조적 접근(트랙 C)** — 검증된 dimension/example을 결정적으로 조합해 애초에 틀릴 수 없게 만듦. 문헌의 시맨틱 레이어·슬롯필링 계열. 저비용·무환각이나 커버리지가 정의 범위에 갇힌다.

**우선순위 판단(§0.3)**: 폴스타는 스키마가 안정적이고 질의가 정형·반복적이므로 **트랙 C(구조적)를 주력**으로, **트랙 A(통계적)를 커버리지 밖 조건부 폴백**으로 둔다. 두 접근 모두 **E1 EX 하네스**로 측정하고, **트랙 B 동의어**로 링킹을 강화한다.

- **E1(EX 평가 하네스)**: 개선을 *측정*하는 수단 — 그 자체로 품질을 올리지 않으나, 트랙 C 커버리지율과 트랙 A 필요성을 데이터로 결정하는 **모든 판단의 선행 근거**다.
- **트랙 B(동의어)**: 질의어→dimension 링킹 계층. 트랙 C의 dimension 선택과 트랙 A의 후보 입력 품질을 동시에 좌우한다(잘못 링크된 컬럼·미존재 리터럴은 후보를 늘려도 정답이 없다). 저비용·무회귀이므로 **병행 착수**한다.

이 구성은 D-035(결정적 근거 우선)와 정합하며, 사용자 요청("개선 효과가 좋은 항목 우선")에 대해 **비용 대비 효과가 가장 높은 트랙 C를 우선**하고 트랙 A는 측정으로 정당화될 때만 투자하는 순서를 취한다.

---

## 2. 현행 구현 실측 (변경 대상)

| 자산 | 위치 | 현행 동작 |
|------|------|-----------|
| SQL 생성 | `src/nodes/query_generator.py::query_generator` | **단일 후보** 1회 생성(LLM `ainvoke` 1회), 재시도 시 이전SQL+에러 주입. 예외: 폼필 자식 EAV는 `_try_build_form_fill_pivot_sql`로 **LLM 우회 결정적 조립**(D-068) |
| 프롬프트 | `src/prompts/query_generator.py` | 범용/Polestar/Polestar-알람 템플릿 3종, 하드코딩 Template A(EAV)·B(성능지표) |
| 검증 | `src/nodes/query_validator.py::query_validator` | 규칙기반 **약 11종** — docstring 8종(파싱·SELECT-only·금지어·인젝션·테이블/컬럼 존재·LIMIT·성능패턴) + 미문서화 3종(금지 JOIN 컬럼 경고·EAV 프로필 금지조인·라우팅 필터 오용). **단일 SQL** 대상 |
| 실행 | `src/nodes/query_executor.py::query_executor` | 단일 SQL 실행, 에러 시 error_message 세팅 |
| 라우팅 | `src/graph.py::route_after_validation / route_after_execution` | 실패 시 `query_generator` 회귀(최대 retry_count 3) |
| 상태 | `src/state.py::AgentState` | `generated_sql`(단수), `retry_count`, `error_message` |

**관찰(코드 확정)**: 후보 다양화·투표·선택 로직은 **전혀 없다**(`temperature`·`n_candidates`·voting 부재. `src/llm.py`는 temperature 0.0 하드코딩).

### 2.1 SQL 생성 경로 실측 — 4종 (v11 신설, 삽입 지점 판단의 근거)

초판 §2 표는 그래프 단일 경로만 실측했으나, 실제 SQL 생성 경로는 4종이며 **폴스타 운영(멀티 DB) 기본 경로는 그래프 엣지를 타지 않는다**:

| 경로 | 진입 조건 | 생성/검증/실행 | 그래프 엣지 경유 |
|------|-----------|----------------|------------------|
| (A) 그래프 단일 DB(레거시) | orchestration·시맨틱 라우팅 off | `query_generator`→`query_validator`→`query_executor` **그래프 노드** | ✅ |
| (B) **orchestration 단일 DB** | 멀티 DB 환경 **자동 기본 ON**(`config.py::model_post_init` — `enable_deepagent_orchestration=None`→True) | `src/orchestration/subagents.py::_run_single_db_pipeline`이 동일 노드를 **함수로 직접 호출**(재시도 3 루프를 자체 재현 — 그래프 라우팅 함수 미사용) | ❌ |
| (C) **멀티 DB** | (B)에서 대상 DB 복수, 또는 시맨틱 라우팅 멀티 분기 | `multi_db_executor._generate_sql`(**자체 LLM 호출**) + `_validate_sql_simple`(**간이 검증** — 테이블/컬럼·EAV 금지조인·라우팅 필터 검사 없음) + `execute_sql` 직접. query_generator/validator/executor **미사용** | ❌ |
| (D) 결정적 폼필 피벗 | 폼필 자식 EAV | `src/utils/query_gen_common.py::build_multi_resource_pivot_sql`(LLM 0회, D-068) — (A)(B)(C) 내부에서 LLM 전 단락 | — |

**함의(삽입 지점 원칙 — §3에 반영)**: 트랙 A·C를 그래프 노드/엣지로만 추가하면 (A)에만 적용되고 **운영 기본 경로 (B)·(C)는 전부 우회**한다 — Known Mistakes에 반복 기록된 단일/멀티 경로 비대칭(D-066 계열)과 동일 실수 패턴. 따라서 본 계획의 신규 로직은 **노드 함수 내부 또는 공유 헬퍼**에 배치해 (A)(B)가 자동 공유하게 하고, (C)는 **별도 이식 항목**으로 §8에 명시 관리한다.

---

## 3. 목표 아키텍처 (증분)

```
                    ┌─ [트랙 C: 커버리지 내] ─► semantic_compiler ──────────────────► query_executor
                    │      (LLM: 자연어→SMQ 조합 선택 → 결정적 SQL 컴파일)
schema_analyzer ─► [E6 커버리지 판정 + E3 복잡도 분기]
                    │      ┌─ (단순 질의) ─────────────────► query_generator (단일, 현행)
                    └─ [밖]┤
                           └─ (복잡 질의) ─► candidate_generator ─► candidate_selector ─► query_executor
                                               (N개 후보)          (규칙필터→실행→결과일관성 투표→LLM 선택)
```

- 기본 경로(모든 플래그 OFF)는 **현행과 완전히 동일**(회귀 0).
- **트랙 C ON**: 커버리지 내 질의는 결정적 컴파일(저비용·무환각) → 1차 방어선.
- **트랙 A ON**: 커버리지 밖 + 복잡 질의만 다중 후보 경로 → 2차 방어선, 비용 통제.
- **삽입 지점 원칙(v11, §2.1 실측 근거)**: 위 그림은 **논리 흐름**이며, 구현은 그래프 엣지 추가가 아니라 **노드 함수 레벨 캡슐화**로 한다 — E6 커버리지 판정·E3 복잡도 분기·E2 후보 생성·E4 선택을 `query_generator` 함수 진입부(공유 헬퍼)에서 수행하여, 그래프 경로(A)와 orchestration 인라인 경로(B)가 **코드 변경 없이 자동 공유**되게 한다. 멀티 DB 경로(C)의 `multi_db_executor._generate_sql`에는 동일 헬퍼를 **명시 이식**한다(§8 산출물). `src/graph.py` 엣지 변경은 그래프 고유 기능(사람 검토 회부 등 HITL 분기)에 한정한다.

---

## 4. 트랙 A — 다중 후보 생성·선택 (E1~E4, 회귀 없는 옵트인)

### E1. Text-to-SQL EX 평가 하네스 (선행, D-072)
- **목적**: R2 효과 측정·A/B 근거. 본 계획 전 단계의 검증 수단.
- **구현**:
  - 골드셋 큐레이션: `sqls/act/*.sql`(실제 실행 이력)과 `testdata/`를 활용해 폴스타 대표 질의 50~150건의 `(질의, 골드 SQL, 골드 결과행집합)`을 YAML/JSON으로 정리(`testdata/text2sql_gold/`). **대표성 원칙**: 실행 이력만 뽑으면 현행이 이미 처리하던 질의로 편향되어 커버리지율이 낙관적으로 측정된다 → **실패 로그·미처리(재질문) 질의도 반드시 포함**한다.
  - 러너: 파이프라인을 배치 실행해 **EX(Execution Accuracy = 결과집합 동치)**, 재시도 횟수, LLM 호출 수, 토큰, 지연을 리포트(`tests/text2sql/test_ex_harness.py` + `scripts/eval_text2sql.py`).
  - 계측 축: `docs/synonym_management_analysis.md` IP-4와 연동 — 유사어 on/off, 후보수 N, 선택전략별 EX 비교 축 포함.
  - **트랙 C 전용 축(2개 신설)**: (1) **SMQ 생성 정확도** — LLM의 자연어→SMQ 변환을 골드 SMQ와 비교(dimension·measure·필터·값·시간창·집계별 일치율), 컴파일 후 EX와 **분리** 측정. (2) **커버리지율** — 시맨틱 모델로 결정적 처리 가능한 질의 비율(위 대표성 골드셋 기준) → 트랙 A 착수 필요성과 dimension 확장 우선순위의 데이터 근거.
- **산출물**: `scripts/eval_text2sql.py`, 골드셋, EX 리포트(baseline 확보).
- **회귀 경계**: 순수 신규 파일·오프라인 배치. 런타임 경로 무변경.

### E2. 다중 후보 생성 (D-073)
- **문헌 근거(§0.2-1)**: 후보의 이득은 "많음"이 아니라 **"다양성"**에서 온다(CHASE-SQL·XiYan-SQL). 단순 `temperature` 재샘플링은 유사 오류를 반복하기 쉬우므로 **서로 다른 추론 경로를 1순위**로 둔다.
- **구현**:
  - `src/nodes/candidate_generator.py` 신설. **호출 지점은 그래프 노드가 아니라 `query_generator` 함수 내부**(§3 삽입 지점 원칙 — 그래프 경로 A와 orchestration 인라인 경로 B를 자동 커버). 멀티 DB 경로(C)는 `multi_db_executor._generate_sql`에 동일 헬퍼를 별도 이식. 후보 다양화(우선순위 재정렬):
    1. **서로 다른 프롬프트 전략(1순위)** — (a) 현행 템플릿, (b) divide-and-conquer(복잡 질의를 서브쿼리로 분해), (c) 실행계획 기반 CoT. CHASE-SQL의 다중 생성기 대응.
    2. `temperature`>0 샘플링(2순위, 전략 내 추가 다양화·저비용 보완).
  - N은 설정값(`TEXT2SQL_CANDIDATE_COUNT`, 기본 3). Polestar EAV 특성상 Template A/B는 후보 시드로 재사용.
  - `AgentState`에 `sql_candidates: list[dict]`(sql, 전략, 신뢰도) 추가(기존 `generated_sql`은 선택 결과로 유지 — 하위호환).
- **회귀 경계**: 플래그 `TEXT2SQL_MULTI_CANDIDATE=off` 기본. OFF 시 기존 단일 경로.

### E3. SQL 복잡도 분기 (비용 통제)
- **구현**: `query_generator` **진입부(공유 헬퍼)**에서 경량 분류(단순 조회 vs 다중조인·EAV피벗·중첩집계 — §3 삽입 지점 원칙: 그래프 사이 삽입 아님). 단순=단일 후보(현행), 복잡=E2 다중 후보. 분류는 결정적 규칙(조인 수·피벗 필요·집계 중첩 추정) 우선, 모호 시 경량 LLM.
- **근거**: DIN-SQL query classification. 기존 `intent_planner`(인텐트 분해)와 계층 분리.
- **회귀 경계**: 플래그 OFF 시 전부 단순 경로로 취급.

### E4. 실행기반 후보 선택 (D-074) — *핵심*
- **구현**: `src/nodes/candidate_selector.py` 신설. 호출 지점은 E2와 동일하게 **`query_generator` 함수 내부**(candidate_generator 직후 — §3 삽입 지점 원칙). 선택 파이프라인(D-035 결정적 우선):
  1. **규칙 사전필터** — 기존 `query_validator`를 후보별로 적용. 실측상 검사는 약 11종으로 폴스타 고유 규칙(EAV 금지조인·라우팅 필터 오용)까지 후보 필터에 작동한다(§2 실측 — 트랙 A에 유리). 통과 후보만 잔류(SD+SA 방식).
  2. **읽기전용 실행** — 잔류 후보를 **LIMIT·타임아웃(기존 30s/10K행)** 하에 실행. D-003(읽기전용)으로 부작용 없음.
  3. **결과 일관성 투표** — 실행 결과집합이 최다로 일치하는 후보 선택(execution-based self-consistency).
  4. **동수/전패 폴백** — LLM 선택 에이전트가 (질의, 후보 SQL, 결과 샘플)로 최적안 판정. 전 후보 실패 시 기존 에러기반 재시도 루프로 강등.
- **감사**: 선택 근거(후보별 실행결과·투표수·최종 선택)를 audit 로깅(기존 감사 인프라 재사용).
- **회귀 경계**: 후보 1개면 즉시 반환(=현행). 플래그 OFF 시 노드 미진입.

---

## 4-B. 트랙 B — 동의어 매칭 보강 (E5, D-075)

> 근거: `docs/synonym_management_analysis.md`. 동의어 관리는 목적·거버넌스(LLM 발견→사람 승인 D-012, 결정적 룩업 D-035, 폐쇄망 적합)가 문헌 기준으로 **적정하므로 유지**한다. 취약점은 오직 **매칭 정밀도(정확일치 한정)·값 검색 미흡·측정 부재** 세 축이며, 아래 E5는 Redis(`redis:7-alpine`)·모델 교체 없이 애플리케이션 계층에서 옵트인·무회귀로 보강한다. IP-2(의미 검색)만 인프라 판단이 필요하여 측정 후 결정한다.

**현행 실측(변경 대상, v11 갱신)**:
- **질의어→동의어 매칭 지점은 5곳에 분산**되어 있고 서로 독립 코드다: ① `src/document/field_mapper.py::_synonym_match`(**폼필/문서 필드 매핑 전용** — 정규화(소문자·공백제거) 후 정확 동등만) ② 같은 파일 `_apply_eav_synonym_mapping`(EAV 속성명, 별도 인라인 정확 동등) ③ `_apply_llm_synonym_discovery`(LLM 발견 경로) ④ `src/nodes/schema_analyzer.py::_synonym_tables_matching_query`(**텍스트 질의 경로의 실제 매칭 지점** — 부분어 포함 방식) ⑤ `src/utils/synonym_usage.py::_match_user_terms`(SQL 사후 역대조). 편집거리·의미 유사도·한글 자모 변형은 **전 지점 미지원**.
- 빈문자열·1글자 가드는 ④(`len(s) >= 2`)·⑤에만 있고 `_synonym_match`에는 **없다**(정확 동등 방식이라 실질 무해했으나, 부분어 매칭 도입 시 가드 신설 필요).
- `src/schema_cache/redis_cache.py` — Hash 키 `synonyms:global`·`:resource_types`·`:eav_names`·`:column_values` + DB별 `schema:{db}:synonyms`. 벡터/RediSearch 모듈 없음. **유사어 Hash는 TTL 없이 영구 저장**(캐시 클리어 시에도 글로벌 사전 보존) — TTL은 CSV 캐시(`ex=`)에만, 변경감지는 fingerprint freshness(D-019)로 수행.
- `src/nodes/schema_analyzer.py::_synonym_tables_matching_query` — 질의 등장 유사어 테이블만 게이트 + 상한 15(D-051). 상한은 **모듈 상수 `_MAX_SYNONYM_SUPPLEMENT_TABLES=15` 하드코딩**(config 미노출).
- `src/utils/synonym_usage.py::extract_synonym_usage` — SQL↔사전 역대조(현재 UI 표시 용도) → 계측 소스로 재활용 가능.

### E5-1. 유연 매칭 (IP-1) — *저비용, 인프라 무변경*
- **문제**: "메모리 사용률"은 잡지만 "메모리 이용률/점유율", "MEM 사용률", 오탈자는 놓친다.
- **구현(v11 — 적용 지점 정정)**: 유연 매칭을 **공유 유틸(`src/utils/flex_match.py` 신설)**로 구현하고 **최소 2개 지점에 적용** — (a) `field_mapper._synonym_match`(폼필 경로), (b) `schema_analyzer._synonym_tables_matching_query`(텍스트 질의 경로). `_synonym_match`는 폼필 전용이므로 **여기에만 넣으면 텍스트 질의는 전혀 개선되지 않는다**(§4-B 실측 ①vs④). 폴백 단계: (1) 현행 정확 동등(최우선, 무변경), (2) 한글 자모 분해·NFC 정규화 비교, (3) 편집거리(Levenshtein) 임계 근사일치, (4) 토큰/부분어 포함 — **빈문자열·1글자 가드는 공유 유틸에 신설**(기존 가드는 ④·⑤ 지점에만 존재, `_synonym_match`엔 없음). 각 매칭에 **신뢰도 점수** 부여, 임계 이하는 확정 매핑이 아닌 **후보 제시**(기존 `pending_synonym_registrations`→`synonym_registrar` 승인 루프에 회부 — 흐름 실재 확인).
- **회귀 경계**: 플래그 `SYNONYM_FUZZY_MATCH=off` 기본. OFF 시 현행 정확일치만.

### E5-2. 동의어 → 값 검색(Value Retrieval) 승격 (IP-3) — *R5/보고서와 통합*
- **문제**: WHERE 리터럴(`resource_type='server.Server'`, EAV `NAME='Hostname'`)이 사전에 없으면 환각(Plan 25에서 관찰).
- **구현**: 안정·유한한 폴스타 값집합(distinct `resource_type`, EAV `NAME`)을 주기적으로 인덱싱해 질의 키워드로 검색·주입. 기존 `synonyms:column_values`를 **실측 값 검색**으로 승격하고 캐시. ※ 실측(v11): 현행 유사어 Hash는 **TTL 없이 영구 저장**이므로 "D-019 TTL 재사용"은 값 검색 캐시에 **CSV 캐시의 `ex=` TTL 패턴 + fingerprint freshness를 신규 적용**하는 것을 뜻한다. 후보 생성(E2) 프롬프트에 검증된 리터럴로 주입.
- **회귀 경계**: 플래그 `SYNONYM_VALUE_RETRIEVAL=off`. 캐시 갱신은 읽기전용(D-003).

### E5-3. 사전 위생·거버넌스 (IP-5) — *저비용*
- **구현**: 각 유사어에 메타(등록출처, 사용횟수, 최종사용일, 신뢰도) 부여 → (a) 프롬프트 주입 시 **사용빈도·신뢰도 상위만** 선별(D-051 상한 정책의 상위화), (b) 장기 미사용 감쇠/정리, (c) 동일 용어가 여러 컬럼에 매핑되는 충돌 시 우선순위 규칙.
- **과잉 가지치기 경계(Death of Schema Linking, arXiv 2408.07702)**: 공격적 필터링은 필요한 컬럼/테이블을 제거해 오히려 해가 될 수 있다. 따라서 선별 임계와 **D-051 상한(15)을 고정 규칙이 아니라 E1 하네스로 튜닝하는 파라미터**로 전환한다(실측: 현행은 `schema_analyzer.py` 모듈 상수 `_MAX_SYNONYM_SUPPLEMENT_TABLES=15` 하드코딩 — config 노출 작업 포함) — 상한을 낮추면 토큰↓이나 리콜↓이므로 EX로 최적점을 탐색한다. 특히 **트랙 C 커버리지 판정에서는 임계 미달 dimension도 후보로 남겨** 판정 입력에 포함(잘라내어 "커버리지 밖" 오분류 방지).
- **효과**: 스케일 안정성(토큰↓)과 정밀도를, 리콜 손실 없이 EX 최적점에서 확보.

### E5-4. 선택적 의미(임베딩) 검색 (IP-2) — *폐쇄망 확정 설계, 측정 후 착수*
- **문제**: 정확/퍼지로도 못 잡는 의역·동의개념("가동률"↔"이용률"↔"사용률", "장애"↔"이상 징후").
- **폐쇄망 지배 제약**: air-gapped 환경은 런타임에 외부 임베딩 API 호출 불가. **들어오는 질의어는 미리 알 수 없어 사전계산으로 못 덮으므로**, 질의를 실시간 임베딩할 모델이 **폐쇄망 내부에 반드시 상주**해야 한다. ⇒ "사전계산 벡터 반입"만으로는 불완전(사전 부분만 커버).

**확정 설계(폐쇄망 최대 성능 권고, 4개 결정)**:

1. **임베딩 모델 — 경량 사내 모델 CPU 상주**
   - 다국어(한국어 포함) 문장 임베딩 모델(multilingual sentence-transformer 계열, 384~768차원, 수백 MB급)을 **오프라인 반입해 CPU로 상주**(현 환경 GPU 없음 전제).
   - 사전(동의어·컬럼 설명·`resource_type`·EAV `NAME`)은 **부팅/갱신 시 배치 임베딩**(수천 벡터 규모 → CPU로 빠름), 질의는 **런타임 1회 임베딩**.

2. **벡터 인덱스 — 인프로세스(FAISS/numpy) 우선, Redis Stack은 조건부**
   - 폴스타 어휘 규모(테이블 수백·동의어 합쳐 수천 벡터)에서는 **FAISS 인메모리(또는 numpy 코사인)로 충분**(ms 지연). **Redis 무변경**(기존 `redis:7-alpine`·D-019·D-051 불변) → 무회귀·저비용.
   - **Redis Stack/RediSearch 정당화 조건**: (a) 벡터 수십만↑, (b) 다중 인스턴스가 인덱스 공유 필요, (c) 조직 표준이 이미 Redis Stack. 폴스타 규모에선 초기 도입 이유 약함(폐쇄망 이미지 반입·운영표준 변경 비용 큼).

3. **하이브리드 계단식 검색(성능 핵심)** — 임베딩 단독 금지. **정확일치 → E5-1 퍼지 → 임베딩** 순 계단식, 각 단계 **신뢰도 점수** 부여. 임계 이하는 확정 매핑이 아닌 **후보 제시**(사람 승인 D-012)로 회부. 결정적 단계를 앞세우고 임베딩을 마지막 보루로 두어 오매칭 방어(BIRD external knowledge·CHASE-SQL value retrieval의 검증-우선 원칙 정합).

4. **착수 게이트(과설계 방지)** — E1 하네스로 **E5-1(퍼지)의 잔여 미매칭율을 먼저 측정**하고, 그 잔여가 크고 중요할 때만 착수. 순서: E5-1 → E1 측정 → 정당화 시 E5-4.

- **산출물**: `src/schema_cache/synonym_semantic.py`(임베딩 로더 + FAISS/numpy 인덱스 + 계단식 통합), 사전 임베딩 배치 스크립트, 반입 모델 아티팩트(오프라인).

### E5-계측. 유사어 적중률 (IP-4) — E1 하네스에 통합
- E1 EX 하네스에 **유사어 on/off·매칭방식(정확/퍼지/의미)·값검색 on/off** A/B 축을 추가하여 각 E5 항목의 EX 기여를 정량화. `extract_synonym_usage`를 계측 소스로 재활용(질의별 히트 수·소스·신뢰도·최종 SQL 성공 여부 로깅).

---

## 4-C. 트랙 C — 시맨틱 모델 기반 결정적 SQL 조합 (E6, D-076)

> 근거: `docs/deterministic_sql_composition_review.md`. 사용자 요청 기법("검증된 예시로 컬럼을 미리 정의하고, 요청에 맞게 조합하여 최적 쿼리를 결정적으로 생성")은 문헌의 **템플릿+슬롯필링**(Finegan-Dollak 2018, RYANSQL, SeaD, RingSQL) 및 **시맨틱/메트릭 레이어**(dbt MetricFlow, Cube, spider2 SMQ)에 대응한다. LLM이 조인·집계를 직접 쓰지 않고 dimension **선택만** 하며 결정적 엔진이 SQL을 조립하므로, **잘못된 조인·집계·미존재 컬럼이 원천 불가능**해진다. D-035(결정적=판단·LLM=보조)와 가장 정합한다.

**핵심 발견(실측)**: 폴스타는 이 기법의 **선언적 원재료를 이미 보유**한다 — `config/db_profiles/*.yaml`의 `known_attributes`(dimension 카탈로그: name·description·synonyms), `value_joins`/`direct_join`(조인 정의), `query_examples`(검증된 질문·SQL·설명), `src/prompts/query_generator.py`의 Template A(EAV 피벗)·Template B(성능통계 measure). **빠진 것은 결정적 조합 엔진 하나** — 현재 이 선언들은 LLM 프롬프트에 텍스트로 주입되어 자유 모방될 뿐이라, 전체 복사(과다 SELECT)·CASE-WHEN 누락(과소)·금지 id 조인(Plan 33)·미존재 resource_type 생성(Plan 25)이 반복된다.

### E6-1. 시맨틱 모델 스키마 정식화 (선언부 승격)
- **배치(사용자 결정 §9-7)**: 별도 `config/semantic_models/{db_id}.yaml` 신설. 기존 `config/db_profiles/*.yaml`은 **불변**(무회귀), 그 `known_attributes`·`value_joins`·`query_examples`를 **입력 소스로만 참조**해 시맨틱 모델을 작성.
- **3패턴 커버(사용자 결정 §9-8)** — 기계가독 시맨틱 모델로 정규화:
  - **패턴 A (서버 설정, EAV 피벗)** — dimension: {이름, CASE-WHEN 표현식 또는 컬럼, resource_type, 값컬럼(stringvalue_short 등), 동의어}. join: `value_joins`/`direct_join`.
  - **패턴 B (성능지표, 통계 measure)** — measure: {집계식(min/avg/max), definition_name(Utilization/MaxIORate), 대상 resource_type}. 시간축: 시/일/월 → `cmm_metric_stat_[h,d,m]`·stat_date 포맷.
  - **패턴 C (알람, 정규화 관계형 조인)** — 알람은 EAV가 아니다. entity: `CMM_ALARM`(CA), `CMM_ALARM_DEF`(D), `CMM_ALARM_ACTIVE`(활성 필터), 알림계열(`CMM_ALARM_DEF_NOTI[_USER/_GROUP/_ROLE/_RMTYPE]`). join: CA.ALARM_ID↔ACTIVE, CA↔D(MASTERDEFINITION_ID). dimension: ALARMSEVERITY·CTIME·CONDITIONLOGTEXT·CURRENTALARMSTATUS·NAME(이벤트명). **규칙 인코딩**: 활성 알람 `ALARMSEVERITY IN (1,2,3)` + ACTIVE 조인 / 이력 `IN (0,1,2,3)`. `alarm_allowed_tables` 화이트리스트 준수. 알림계열은 담당자/그룹/역할 조회 시에만 조인.
- 알람 SMQ는 dimension/measure 조합이 아니라 **알람 엔터티·필터·조인 선택**이 되므로, 컴파일러(E6-2)는 패턴별 조립 규칙을 분기한다.

### E6-2. 중간표현(SMQ) + 결정적 컴파일러 — *핵심*
- 폴스타판 **SMQ**(Semantic Model Query) 정의: {선택 dimension 목록, 선택 measure 목록, 필터, 시간단위, 정렬/제한}. LLM은 자연어 → SMQ(**컬럼 이름 선택만**) 담당.
- `src/nodes/semantic_compiler.py`(신설): SMQ → 방언별 SQL 결정적 컴파일. **패턴별 조립 분기**: A=EAV 피벗(CASE-WHEN)+`value_joins`, B=`cmm_metric_stat_[h,d,m]` 통계 measure+시간축, **C=알람 정규화 조인**(CA↔D↔ACTIVE, severity 규칙 자동 주입, `alarm_allowed_tables` 강제). 공통: GROUP BY 자동 도출, PostgreSQL `LIMIT`/DB2 `FETCH FIRST` 분기, 요청 항목만 조립(Template 전체 복사 문제 해소).
- **기존 결정적 조립 자산 흡수(v11 필수 — D-067 단일 출처)**: 트랙 C의 축소판이 **이미 가동 중**이다 — `src/utils/query_gen_common.py`의 `build_multi_resource_pivot_sql`(D-068 3차: 스키마 한정·엔진별 방언(LIMIT/FETCH FIRST)·집계 전 소수 캐스트·EAV 다중리소스 피벗·`resolve_stat_month` 기간필터를 **LLM 우회로 직접 조립**)과 `decimal_cast_example`·`classify_metric_field`·`eav_attr_resource_types`, 그리고 `src/routing/db_schema.py`(스키마 한정 헬퍼). `semantic_compiler`는 이들을 **재사용·일반화(흡수)**하며, 별도의 조립 로직을 병립시키지 않는다(**이중 조립 엔진 금지**). 폼필 피벗 경로(D-068)는 장기적으로 semantic_compiler의 패턴 A+B 복합 케이스로 수렴시킨다.
- **리터럴 정확성(트랙 B 의존)**: WHERE 리터럴(`resource_type='server.Server'`, EAV `NAME='Hostname'` 등)은 **E5-2(값 검색)로 검증된 실측 값만 사용**한다. 미검증 값이 필요한 질의는 커버리지 밖으로 처리(폴백). ⇒ **트랙 C 착수 시 트랙 B E5-2 동반이 사실상 필수**(그렇지 않으면 리터럴 환각(Plan 25 유형)이 컴파일러를 우회해 재발).
- **효과**: 잘못된 조인·집계·미존재 컬럼(구조적 환각) **원천 차단**. 결과 SQL은 기존 `query_validator`도 통과(이중 안전). ※ SMQ *선택* 오류는 별도 리스크(아래 표).

### E6-3. 커버리지 판정 + **신뢰도 기반 3단 폴백** (사용자 결정 §9-9)
- 질의가 시맨틱 모델 커버리지 내(정의된 dimension/measure/알람엔터티로 표현 가능)인지 **결정적 판정** → 내부면 E6-2 컴파일, 밖이면 아래 3단 폴백.
- **3단 폴백(커버리지 밖)**:
  1. **1차 — 트랙 A(다중 후보 + hybrid 선택)**: 다중 후보 생성·실행검증·결과일관성으로 최적안 선별. (§9-4 비용 무제한 결정으로 커버리지 밖에 고효과 구성 적용)
  2. **2차 — 신뢰도 게이트**: 트랙 A 선택 신뢰도(후보 간 결과 일치도·실행 성공 여부·LLM 판정 확신도)가 **임계 이상이면 결과 제시**.
  3. **3차 — 사람 검토/우아한 거부**: 신뢰도 **임계 미달**(전 후보 실행 실패·결과 불일치·저확신)이면 결과를 신뢰 답으로 내지 않고 **사람 검토 회부** 또는 "자동 처리 범위를 벗어남" 안내. 문헌(arXiv 2604.25149·2606.31041)의 "confident hallucination 회피" 원칙.
- **LLM 자유생성**은 **트랙 A 미착수 과도기의 임시 폴백**으로만 허용(트랙 A 착수 후 비활성). 트랙 C가 못 푼 질의를 현행 단일 생성이 풀 가능성은 낮으므로 상시 기본값으로 두지 않는다.
- 라우팅(v11 — 명명·배치 정정): 커버리지 판정 컴포넌트는 **`coverage_router`로 명명**한다 — `semantic_router`는 **기존 의도 라우터**(`src/routing/semantic_router.py`, `graph.py`에 배선: schema_analyzer/multi_db_executor/cache_management/synonym_registrar/general_inference 분기)와 **이름 충돌**하므로 사용 금지. 배치는 §3 삽입 지점 원칙에 따라 그래프 엣지가 아니라 `query_generator` 진입부(공유 헬퍼)에서 판정 → 내부면 `semantic_compiler`, 밖이면 `candidate_generator`(1차)→신뢰도 게이트→(제시 | 사람 검토 회부). 사람 검토 회부만 그래프 고유 분기(HITL)로 배선.

### E6-4. 예시 쿼리 → 조합 골든 회귀셋
- `query_examples`를 E6-2 컴파일러의 **골든 회귀 테스트**로 재활용(E1 하네스 연동): "질문 → dimension 조합 → SQL"이 결정적으로 재현되는지 검증. 신규 dimension 추가 시 회귀 방지.

### E6-리스크·통제
| 리스크 | 통제 |
|--------|------|
| 커버리지 갇힘(템플릿 한계, 문헌 공통 지적) | E6-3 폴백(트랙 A), dimension 카탈로그를 사람 승인 루프(D-012)로 점진 확장 |
| **SMQ 생성 정확도(RYANSQL 슬롯 예측 약점)** | LLM의 dimension·measure·**필터·값·시간창·집계** 선택 오류 → "문법상 정상·의미상 오답". E1에서 SMQ 정확도를 EX와 분리 측정, 예시 기반 few-shot(E6-4)로 보강, 신뢰도 낮으면 사람 확인 |
| **리터럴 환각 우회** | E6-2가 미검증 값을 조립하면 컴파일러를 우회해 환각 재발 → E5-2 값 검색 검증값만 사용, 미검증 시 커버리지 밖 처리 |
| 초기 정의 비용 | 기존 db_profiles·Template 재활용으로 대폭 절감 |
| 회귀 | 기본 OFF(`TEXT2SQL_SEMANTIC_COMPOSE`), 커버리지 내에서만 진입, 결과 SQL도 `query_validator` 통과 |
| 컴파일러 버그 | E6-4 골든 회귀셋으로 지속 검증 |

---

## 5. 설정 플래그 (기본 OFF, 무회귀)

| 플래그 | 기본 | 의미 |
|--------|------|------|
| `TEXT2SQL_MULTI_CANDIDATE` | off | 다중 후보 경로 전체 스위치 |
| `TEXT2SQL_CANDIDATE_COUNT` | 3 | 후보 수 N |
| `TEXT2SQL_CANDIDATE_STRATEGIES` | **multi_prompt** | `temperature`\|`multi_prompt`(다양화 방식). 사용자 결정: multi_prompt |
| `TEXT2SQL_COMPLEXITY_GATE` | off | 복잡 질의만 다중 후보(품질 목적 — 비용 상한 아님, §9-4 무제한 결정) |
| `TEXT2SQL_SELECTION` | **hybrid** | `consistency`\|`llm`\|`hybrid`(선택 전략). 사용자 결정: hybrid |
| `SYNONYM_FUZZY_MATCH` | off | E5-1 유연 매칭(자모·편집거리·부분어) |
| `SYNONYM_VALUE_RETRIEVAL` | off | E5-2 실측 값 검색·주입 |
| `SYNONYM_SEMANTIC_MATCH` | off | E5-4 임베딩 의미 검색(인프라 확정 후) |
| `SYNONYM_MATCH_CONFIDENCE_MIN` | (튜닝) | 퍼지/의미 매칭 신뢰도 임계(이하는 후보 제시만) |
| `TEXT2SQL_SEMANTIC_COMPOSE` | off | E6 결정적 조합 경로 전체 스위치 |
| `TEXT2SQL_SEMANTIC_FALLBACK` | **candidate_then_human** | 커버리지 밖 라우팅(사용자 결정 §9-9, 3단): `candidate_then_human`(트랙 A→저신뢰 시 사람검토) \| `llm`(과도기 임시) \| `human`(항상 검토) |
| `TEXT2SQL_FALLBACK_CONFIDENCE_MIN` | (튜닝) | 3단 폴백 2차 게이트 — 트랙 A 선택 신뢰도 임계(미달 시 사람검토 강등) |

---

## 6. 비용·리스크 및 통제

| 항목 | 리스크 | 통제 |
|------|--------|------|
| LLM 호출 N배 | 지연·토큰 비용↑ | (§9-4 사용자 결정: **비용 무제한**) E3 게이트는 품질 목적으로만 유지(단순 질의에 불필요한 후보 억제), 비용 컷오프는 두지 않음. N은 E1으로 이득 곡선 측정해 상향 |
| 후보 실행 N배 | DB 부하 | 읽기전용(D-003)·LIMIT·타임아웃, 규칙필터로 실행 전 후보 감축 |
| 프롬프트 토큰 폭증 | FabriX 95K 한도(D-051 이력) | 후보별 스키마 재사용(중복 주입 회피), 유사어 상한(15) 유지 |
| 선택 오판 | 잘못된 후보 채택 | 결정적 결과일관성 우선(D-035), LLM은 동수 폴백에 한정, 감사 로깅 |
| **선택기 품질 병목(§0.2-2)** | 후보가 전부 실행실패·동수일 때 LLM 쌍대비교 판정이 부정확 | CHASE-SQL은 파인튜닝 선택기가 투표보다 우수하나 폐쇄망·파인튜닝 부재 제약 → 결과일관성 우선, LLM 판정엔 결과 샘플·스키마 근거를 함께 제공, 신뢰도 낮으면 사람 검토로 강등 |
| **모든 후보 동일 오류(§0.2-1)** | `temperature`만 쓰면 유사 오류 반복 → 투표가 틀린 답에 수렴 | E2 다중 프롬프트 전략 우선(추론 경로 다양화)으로 완화 |
| 무한 루프 | 재시도 폭주 | 기존 max retry_count=3 유지 |
| 퍼지 과잉매칭(E5-1) | 오매핑으로 잘못된 컬럼 참조 | 신뢰도 임계 이하는 확정 아닌 후보 제시(사람 승인), 공유 유틸(`flex_match`)에 **신설**하는 빈문자열·1글자 제외 가드 적용(기존 가드는 schema_analyzer/synonym_usage에만 존재 — §4-B 실측) |
| 값 검색 캐시 노후(E5-2) | 오래된 리터럴 주입 | D-019 TTL·fingerprint 재사용, 읽기전용 갱신 |
| 사전 비대(E5-3) | 프롬프트 토큰↑ | 사용빈도·신뢰도 상위 선별 + 감쇠, D-051 상한 유지 |

---

## 7. 검증 기준 (E1 하네스로 측정)

**공통 — 경로 커버리지 (v11 신설)**
- 각 트랙 ON의 효과를 그래프 단일 경로(A)뿐 아니라 **orchestration 인라인 경로(B — 멀티 DB 운영 기본)·멀티 DB 경로(C)에서 실측**한다(§2.1). 한 경로만 검증하면 D-066 계열 단일/멀티 비대칭 회귀를 놓친다. E1 러너는 세 경로를 각각 구동할 수 있어야 한다.

**트랙 A(다중 후보)**
- **1차 게이트**: 다중 후보(E2+E4) ON이 baseline(단일) 대비 **EX 향상** + 회귀 없음(단순 질의 EX 불변).
- **비용 측정(게이트 아님)**: §9-4 결정으로 비용 상한은 없으나, 평균 LLM 호출·지연·토큰은 **모니터링 지표로 계속 리포트**(운영 가시성).
- **선택 전략**: 기본값 `hybrid`(사용자 결정). `consistency` 단독 대비 EX 이득을 리포트해 hybrid의 LLM 병용 비용이 정당한지 사후 확인.
- **후보수 N**: 기본 3에서 N 증가 시 EX 이득 곡선을 측정해 상향 여지 판단(비용 무제한 전제).

**트랙 B(동의어)**
- **매칭 커버리지**: E5-1(퍼지) ON이 유사어 히트율↑·미매핑 재질문↓, EX 향상 또는 불변(회귀 없음).
- **환각 억제**: E5-2(값 검색) ON이 미존재 resource_type/EAV명 생성 오류(Plan 25 유형)를 감소.
- **의미 검색 판단**: E5-4(임베딩)는 계단식(정확→퍼지→임베딩)에서 **E5-1 이후 잔여 미매칭에 대한 추가 히트율**을 측정 → 그 이득이 폐쇄망 모델 상주·인덱스 운영 비용을 정당화할 때만 채택. 채택 시에도 임베딩 매칭은 신뢰도 임계 이하면 후보 제시(사람 승인)로만 반영해 오매칭 회귀 방지.

**트랙 C(결정적 조합)**
- **커버리지 내 EX**: E6 컴파일 경로가 커버리지 내 질의(서버 설정·성능지표)에서 트랙 A/현행 대비 **EX 동등 이상** 달성.
- **구조적 환각 0**: 조인·집계·미존재 컬럼 오류는 컴파일 단계에서 **발생 불가**(부품만 조립). ※ 단 이는 오류의 *일부*이며, **SMQ 선택 오류**(LLM이 잘못된 dimension/measure를 고른 "문법상 정상·의미상 오답")는 남는다 — 아래 SMQ 생성 정확도로 별도 측정한다(2606.31041도 EX 94.15%로 100%가 아님).
- **SMQ 생성 정확도**: LLM의 자연어→SMQ 변환(dimension·measure·필터·값·시간창·집계 선택)이 골드 SMQ와 일치하는 비율을, 컴파일 후 EX와 **분리해 측정**. RYANSQL이 지적한 슬롯(조건값) 예측 약점이 여기 병목이 될 수 있다.
- **비용**: 커버리지 내에서 LLM 호출/지연이 트랙 A 대비 대폭 감소(1회 조합 판단 + 결정적 컴파일).
- **커버리지율**: `query_examples`·실사용 로그 대비 결정적 처리 가능 비율을 측정 → dimension 카탈로그 확장 우선순위 결정. 골드셋은 **실행 이력뿐 아니라 실패·미처리 질의도 포함**해 낙관 편향을 방지.
- **골든 회귀**: E6-4 셋이 100% 재현(컴파일러 버그 0).
- **폴백 신뢰도 게이트(E6-3 3단)**: 커버리지 밖 질의에서 (1) 트랙 A 1차 처리율, (2) 신뢰도 게이트 통과율(2차 제시), (3) 사람검토 강등율(3차)을 계측. 강등된 저신뢰 질의가 **틀린 SQL을 신뢰 답으로 제시하지 않는지**(confident hallucination 0) 확인하고, 신뢰도 임계(`TEXT2SQL_FALLBACK_CONFIDENCE_MIN`)를 EX·오답 제시율 트레이드오프로 튜닝.
- 목표 수치는 baseline 측정(E1) 후 설정한다(문헌 수치는 데이터셋·모델 의존이므로 절대 목표로 이식하지 않음).

---

## 8. 산출물

- **트랙 A 신규**: `scripts/eval_text2sql.py`, `testdata/text2sql_gold/`, `src/nodes/candidate_generator.py`, `src/nodes/candidate_selector.py`, `tests/text2sql/`.
- **트랙 A 변경(v11 — 삽입 지점 원칙 반영)**: `src/nodes/query_generator.py`(후보 생성·선택 진입 캡슐화 — 경로 A·B 자동 공유), `src/nodes/multi_db_executor.py`(경로 C에 동일 헬퍼 이식), `src/graph.py`(HITL 분기 한정), `src/state.py`(`sql_candidates`), `src/config.py`(플래그). `src/orchestration/subagents.py`는 노드 함수 캡슐화 시 **무변경**(변경이 필요해지면 삽입 지점 설계가 잘못된 것 — 재검토 신호).
- **트랙 B 신규**: `src/utils/flex_match.py`(공유 유연 매칭 유틸 — 자모·편집거리·부분어+가드+신뢰도).
- **트랙 B 변경(동의어)**: `src/document/field_mapper.py`(`_synonym_match`에 공유 유틸 적용 — 폼필 경로), `src/nodes/schema_analyzer.py`(`_synonym_tables_matching_query`에 공유 유틸 적용 — 텍스트 질의 경로 + 선별 주입 정책 + 상한 15 config 노출), `src/schema_cache/redis_cache.py`(값 검색 캐시·유사어 메타), `src/utils/synonym_usage.py`(계측 소스화), `src/config.py`(SYNONYM_* 플래그).
- **트랙 B 신규(조건부, E5-4 채택 시)**: `src/schema_cache/synonym_semantic.py`(임베딩 로더 + FAISS/numpy 인덱스 + 정확→퍼지→임베딩 계단식 통합), 사전 배치 임베딩 스크립트, 오프라인 반입 임베딩 모델 아티팩트. (폐쇄망 확정 설계 = 인프로세스 인덱스·Redis 무변경; Redis Stack은 대규모·공유 예외 시에만)
- **트랙 C 신규**: `src/nodes/semantic_compiler.py`(SMQ→SQL 컴파일러, 패턴 A/B/C 분기), **`config/semantic_models/{db_id}.yaml`(신설·분리 — db_profiles 불변)**, SMQ 정의(패턴 A/B/C), `tests/text2sql/test_semantic_golden.py`. `db_profiles`는 입력 소스로만 참조(무변경).
- **트랙 C 변경(v11)**: `src/nodes/query_generator.py`(진입부 `coverage_router` 판정 — 경로 A·B 공유), `src/nodes/multi_db_executor.py`(경로 C 이식), `src/utils/query_gen_common.py`·`src/routing/db_schema.py`(semantic_compiler로 흡수·일반화 리팩터 — D-067 단일 출처), `src/graph.py`(사람 검토 회부 HITL 분기 한정), `src/prompts/`(자연어→SMQ 프롬프트), `src/config.py`(SEMANTIC 플래그).
- 문서: 착수 시 `docs/02_decision.md`에 D-072~076 등재, `docs/17_future_improvements.md`의 관련 항목 상태 갱신, 본 계획 상태 갱신.

## 9. 착수 전 사용자 확인 필요 (CLAUDE.md 의사결정 규칙)

**트랙 A(다중 후보) — 사용자 결정 완료(2026-07-13)**
1. **범위**: ✅ **E1(측정)+E2~E4(다중후보) 전체** 착수.
2. **후보 다양화 방식**: ✅ **`multi_prompt`(divide&conquer·실행계획 CoT 등, 고비용·고효과)** 우선. `temperature`는 전략 내 보조로만.
3. **선택 전략 기본값**: ✅ **`hybrid`(결정적 결과일관성 + LLM 병용)**. 결과일관성으로 1차 정렬 후 LLM 쌍대비교를 상시 병용해 최종 판정.
4. **비용 허용치**: ✅ **무제한**(질의당 LLM 호출·지연 상한 없음). ⇒ E3 복잡도 게이트는 품질 목적(단순 질의에 불필요한 후보 억제)으로만 유지하고, 비용 상한 컷오프는 두지 않는다.

> **결정 반영**: 위 결정에 따라 §5 플래그 기본값을 조정했다(`TEXT2SQL_CANDIDATE_STRATEGIES=multi_prompt`, `TEXT2SQL_SELECTION=hybrid`). `hybrid`·`multi_prompt`는 문헌 최고효과 구성(CHASE-SQL 다중 생성기 + 선택 에이전트)에 해당하며, 비용 무제한 전제로 후보수 N도 상향 여지가 있다(초기 N=3, E1으로 N↑ 이득 곡선 측정). ⚠️ 단 트랙 우선순위(§0.3)는 유지 — 트랙 C가 커버리지 내를 결정적으로 처리하고, 트랙 A는 **커버리지 밖**에 이 고효과·고비용 구성을 적용한다.

**트랙 B(동의어)**
5. **적용 범위**: E5-1(퍼지)+E5-2(값검색)+E5-3(위생)까지의 저비용·무회귀 세트만 우선인가, E5-4(의미검색)까지 포함인가.
6. **E5-4 인프라 — 폐쇄망 권고 확정(E5-4 §확정 설계)**: 경량 사내 임베딩 모델 CPU 상주 + FAISS/numpy 인프로세스 인덱스(Redis 무변경) + 정확→퍼지→임베딩 계단식. 남은 확인만: (a) **반입 가능한 사내/오프라인 임베딩 모델**이 있는가(한국어 포함 다국어), (b) 벡터 규모가 인프로세스 한계를 넘어 Redis Stack이 필요한 예외 상황인가.

**트랙 C(결정적 조합)**
7. **선언부 위치 — 사용자 결정: ✅ 별도 `config/semantic_models/`로 분리.** 기존 `config/db_profiles/*.yaml`은 불변 유지(무회귀), 시맨틱 모델은 신규 디렉터리에 DB별 파일(`config/semantic_models/{db_id}.yaml`)로 작성. `db_profiles`의 `known_attributes`·`value_joins`·`query_examples`는 시맨틱 모델 생성의 **입력 소스**로 참조하되 원본은 건드리지 않는다.
8. **SMQ 범위 — 사용자 결정: ✅ 알람 질의까지 확장(3패턴).** 서버 설정(Template A: EAV 피벗) + 성능지표(Template B: 통계 measure) + **알람(Template C: 정규화 관계형 조인)**. 알람은 EAV가 아니라 `CMM_ALARM`↔`CMM_ALARM_DEF`↔활성/알림 테이블의 조인이며 별도 라우팅(`routing_intent=alarm_query`)·`alarm_allowed_tables`가 이미 있으므로, 시맨틱 모델에 **알람 전용 엔터티·조인·severity 규칙**을 별도 섹션으로 정식화한다.
9. **커버리지 밖 폴백 — 사용자 결정: ✅ 신뢰도 기반 3단 폴백.** (1차) 트랙 A 다중 후보+hybrid 선택 → (2차) 선택 신뢰도 게이트 → (3차) 임계 미달 시 사람 검토/우아한 거부. LLM 자유생성은 트랙 A 미착수 과도기 임시값으로만. 근거: 트랙 A도 silent-wrong 가능(모든 후보 동일 오답) → 무조건 결과 제시는 confident hallucination이므로 저신뢰 시 사람 강등. 문헌 우아한 거부 + §9-4 비용 무제한 + D-035(신뢰도 측정값 기반 라우팅) 정합.

**트랙 실행 순서 (종합)**
10. 세 트랙의 우선순위·병행 여부. — **권고(§0.3)**: (a) E1 하네스 선행(공유 측정), (b) **트랙 C(E6)와 트랙 B(E5-1~3)를 우선**(둘 다 기존 자산 재활용·저비용·무회귀이며 환각을 구조적으로 억제), (c) 트랙 A(다중 후보)는 **커버리지 밖 비율 측정 후** 그 필요성이 입증될 때만 착수. 트랙 C가 폴스타 반복 질의 대다수를 결정적으로 처리하면 트랙 A의 고비용 적용 범위가 줄어 전체 비용도 최적화된다.

---

## 10. 참고 문헌 (초록 검증 완료)

> 아래 수치는 각 논문 arXiv 초록에서 직접 확인. 데이터셋·모델·시점 의존이므로 폴스타 목표로 이식하지 않으며, 근거는 기법의 구조적 성격이다.

- DIN-SQL — arXiv:2304.11015 (태스크 분해·난이도 분류·자기수정)
- DAIL-SQL — arXiv:2308.15363 (스켈레톤 유사도 예시 선택, Spider EX 86.6%)
- CHASE-SQL — arXiv:2410.01943 (다중 후보 + 선택 에이전트, BIRD EX 73.0%)
- XiYan-SQL — arXiv:2411.08599 (M-Schema + 다중 생성기 앙상블, Spider 89.65%/BIRD dev 75.63%)
- The Death of Schema Linking? — arXiv:2408.07702 (추론형 모델의 무관 컬럼 필터링)
- RYANSQL — arXiv:2004.03125 (스케치+슬롯필링 결정적 복원, Spider 58.2%/+3.2%p/리더보드 1위)
- Semantic-Layer-Mediated NL2SQL Agent — arXiv:2606.31041 (SMQ 중간표현 + 결정적 컴파일러; Gemini 3 Pro로 547-task Spider2-snow에서 EX 94.15%, 공식 리더보드 3위 — 초록 재확인 2026-07-13)
- Semantic Layers for Reliable LLM-Powered Data Analytics — arXiv:2604.25149 (시맨틱 문서 추가 시 3개 프론티어 모델 정확도 +17~23%p — 초록 재확인 2026-07-13)

> 상세 검토·비교는 `docs/text2sql_quality_research.md`, `docs/synonym_management_analysis.md`, `docs/deterministic_sql_composition_review.md` 참조.

---

## 11. 구현 참고 자료 (코드 착수 시 참조)

> 컴포넌트별 실무 라이브러리·레퍼런스 구현·설계 참조. **버전·API·라이선스·폐쇄망 반입 가능 여부는 착수 시점에 재확인**한다(아래는 방향 제시이며 고정 의존이 아님). 폐쇄망 원칙상 pip/conda 패키지는 사내 미러 또는 오프라인 휠 반입을 전제한다.

### 11-1. 트랙 A — 다중 후보 생성·선택 (E1~E4)
- **레퍼런스 구현**: CHASE-SQL(arXiv 2410.01943) — divide&conquer·실행계획 CoT·선택 에이전트의 원 설계. DIN-SQL(arXiv 2304.11015) — 난이도 분류·자기수정 프롬프트 구조. 공식/커뮤니티 코드 공개 여부는 착수 시 확인.
- **self-consistency 개념**: "Self-Consistency Improves Chain of Thought Reasoning in Language Models"(arXiv 2203.11171 — 제목 arXiv API 확인 2026-07-13) — E4 결과일관성 투표의 이론 근거.
- **실행기반 선택**: "Robust Text-to-SQL Generation with Execution-Guided Decoding"(arXiv 1807.03100 — 제목 arXiv API 확인 2026-07-13). 후보 생성/선택은 §3 삽입 지점 원칙에 따라 **그래프 노드가 아니라 `query_generator` 함수 내부(공유 헬퍼)**로 편입 — `src/graph.py` 조건부 분기는 HITL(사람 검토 회부) 배선에만 활용.
- **골드셋·EX 평가(E1)**: Spider(yale-lily/spider)·BIRD(BIRD-bench) 공개 하네스의 **EX(결과집합 동치) 채점 로직**을 폴스타 골드셋 러너 설계에 참조(데이터셋 자체 반입이 아니라 채점 방식만 차용).

### 11-2. 트랙 B — 동의어 매칭 (E5)
- **편집거리/부분어(E5-1)**: `rapidfuzz`(고속 Levenshtein·부분문자열 유사도, MIT). 순수 파이썬 대안 `python-Levenshtein`.
- **한글 자모 정규화(E5-1)**: `jamo`(한글 음절↔자모 분해), 파이썬 표준 `unicodedata`(NFC/NFD 정규화). 자모 분해 후 편집거리로 "메모리"↔"메모으리" 류 변형 흡수.
- **임베딩 모델(E5-4, 폐쇄망 상주)**: `sentence-transformers`(UKPLab) — 다국어 모델(예: `paraphrase-multilingual-MiniLM-L12-v2`, `distiluse-base-multilingual-cased`) CPU 추론. 한국어 특화로는 KLUE/KoSimCSE 계열. **오프라인 반입**: 모델 가중치를 아티팩트로 사전 다운로드 후 로컬 로드(`SentenceTransformer(local_path)`).
- **벡터 인덱스(E5-4)**: `faiss-cpu`(Facebook, 인메모리 KNN) 우선. 소규모는 `numpy` 코사인으로도 충분. 대규모·공유 필요 시에만 Redis Stack/RediSearch.
- **값 검색(E5-2)**: distinct 값 인덱싱은 기존 `redis_cache.py`(D-019 TTL) 패턴 재사용 — 신규 의존 없음.

### 11-3. 트랙 C — 시맨틱 모델·결정적 컴파일 (E6)
- **SQL 방언 컴파일(E6-2 핵심)**: `sqlglot`(tobymao/sqlglot, MIT) — AST 기반 SQL 생성·**방언 트랜스파일**(PostgreSQL↔DB2 등). SMQ→SQL 조립 시 문자열 접합 대신 AST를 구성해 방언별(`LIMIT`/`FETCH FIRST`) 안전 렌더링. 현행 `sqlparse`(검증용)와 역할 분리. **반입 불가 시 대안(v11)**: sqlglot은 권장이지 필수가 아니다 — 현행 `query_gen_common.py`가 이미 쓰는 엔진 분기 문자열 조립 방식(D-068·D-053 검증됨)으로 컴파일러 구현 가능. 이 경우 방언 분기는 `get_domain_by_id(db_id).db_engine` 결정적 주입(D-066 후속6)을 그대로 따른다.
- **시맨틱 레이어 레퍼런스**: dbt **MetricFlow**(metric·dimension·entity 정의 스키마와 컴파일 개념), **Cube**(`cube.dev` 데이터 모델 — measure/dimension/join 선언 형식)。 `config/semantic_models/*.yaml` 스키마 설계 시 이들의 **선언 구조를 참고**(직접 도입이 아니라 YAML 모델 형태의 벤치마크).
- **SMQ 중간표현 설계**: arXiv 2606.31041(SMQ + 결정적 컴파일러)의 IR 개념. 폴스타판 SMQ는 {dimensions, measures, filters, time_grain, order/limit, pattern(A/B/C)} 스키마로 정의 — Pydantic 모델로 스키마 강제·검증 권장.
- **패턴 C(알람) 근거**: 현행 `src/prompts/query_generator.py`의 `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE`와 `config/db_profiles/*.yaml`의 `alarm_allowed_tables`·severity 규칙 — 시맨틱 모델 알람 섹션의 1차 소스.
- **골든 회귀(E6-4)**: `pytest` 파라미터라이즈로 `query_examples`를 "질문→SMQ→SQL" 케이스화. sqlglot의 AST 정규화 비교로 공백·별칭 차이에 강한 동치 검증.

### 11-4. 공통·운영
- **플래그·설정**: 현행 `src/config.py` 패턴 확장(§5 플래그). 전 기능 기본 OFF.
- **감사·관측**: 후보별 실행결과·투표·선택 근거, SMQ·컴파일 SQL, 폴백 단계, 매칭 신뢰도를 기존 감사 로깅 인프라로 기록(E1 계측 소스와 공유).
- **한국어 NLP 일반**: 형태소 분석이 필요할 경우 `KoNLPy`(Okt/Mecab) — 단, 폐쇄망 Mecab 사전 반입 부담이 있어 E5-1은 자모·편집거리 우선, 형태소는 필요성 확인 후.

> **원칙**: 위 자료는 **설계 방향의 근거**이며, 실제 채택 전 (1) 라이선스(사내 정책 부합), (2) 폐쇄망 오프라인 반입 가능성, (3) 기존 스택과의 충돌·버전 호환을 검증한다. 신규 의존 추가는 최소화하고, 가능한 현행 스택(`sqlparse`·`redis`·`langgraph`) 재사용을 우선한다.

---

## 12. 구현 상태 (2026-07-14 실측 · 트랙 C 착수 반영)

> 코드베이스 대조 실측 결과. Plan 61 관련 테스트 **153건 전체 통과**(기존 106 + 트랙 C 골든 `test_semantic_golden.py` 34 + pivot 13)(`tests/text2sql/`, `tests/test_utils/`, `tests/test_document/test_field_mapper_flex.py`, `tests/test_nodes/test_schema_analyzer_synonym_flex.py`, `tests/test_schema_cache/test_value_index.py`). arch_check exit 0, 골드셋 스키마 위반 0.
>
> **2026-07-14 업데이트(트랙 C 착수)**: D-072·D-075·D-076 `docs/02_decision.md` 등재 완료. 트랙 C(E6) 전체 구현(E6-1 시맨틱 모델 3종·E6-2 semantic_compiler·E6-3 coverage_router 3경로 배선·E6-4 골든 34건)·E5-2 소비 지점 연결 완료. 전 기능 `TEXT2SQL_SEMANTIC_COMPOSE=off` 기본, OFF 시 회귀 0(트랙 C 격리 후 test_nodes 실패 diff 0 실측).
>
> **2026-07-15 업데이트(트랙 A·E5-3·E5-2 런타임 착수)**: D-073·D-074 등재. 트랙 A(E2~E4·3단 폴백)·E5-3 거버넌스·E5-2 런타임 적재 구현(전 기능 기본 OFF). 신규 테스트 45건 통과. **최종 전체 스위트 검증(팀리드 수정 후): `pytest tests/` = 49 failed / 2298 passed / 5 errors**(수정 전 800/1536) — **Plan 61 신규 테스트 전부 전체-스위트 맥락에서 통과**, 잔존 49+5는 전부 수정 전부터 있던 환경 의존 실패(test_e2e_polestar 17·test_api 6·test_plan33 5 등, Plan 61 무관). 대량 실패 실체는 tests/e2e(playwright) 이벤트 루프 오염 단일 원인 → `RUN_E2E=1` 옵트인 제외(팀리드). config 임포트 고정 버그(nested → `Field(default_factory=...)`)·하네스 실접속 3버그도 팀리드 수정 완료·Known Mistakes 등재. **B4(골드셋 gp-001~003 규약 모순)는 실 DB 검증 필요 — 사용자 결정 대기(§12.3-7 백로그).**

### 12.1 항목별 상태

| 항목 | 상태 | 실측 근거 |
|------|------|-----------|
| **E1. EX 평가 하네스 (D-072)** | ✅ **구현** | `scripts/eval_text2sql.py`(EX 결과집합 동치 채점, 3경로 `--path {graph,orchestration,multidb}`, A/B 축 `--synonym-fuzzy`/`--value-retrieval`/`--candidate-count`/`--selection`/`--semantic-compose`(미구현 축은 전방호환 env 세팅만), `--dry-run`/`--mock`/실접속 graceful 스킵). 골드셋 `testdata/text2sql_gold/` 26건(gp 15·yd 6·b0 5, `coverage`(inside/outside)·`gold_smq` 필드 포함 — 트랙 C 축 §7 대비). `tests/text2sql/test_ex_harness.py` |
| **E2. 다중 후보 생성 (D-073)** | ✅ **구현** | `src/nodes/candidate_generator.py`(multi_prompt 전략 N개·중복제거·전략 태깅)+`src/prompts/candidate_strategies.py`(base/분할정복/실행계획 CoT suffix). `AgentState.sql_candidates` 추가. 플래그 `TEXT2SQL_MULTI_CANDIDATE`/`CANDIDATE_COUNT`(3)/`CANDIDATE_STRATEGIES`(multi_prompt) 노출. 삽입: `query_generator` 내부(경로 A·B)+`multi_db_executor._generate_sql`(경로 C 이식), subagents.py 무변경 |
| **E3. 복잡도 분기** | ✅ **구현** | `candidate_generator.classify_complexity`(결정적 — 다중target·집계/순위/범위·EAV 다중속성→complex). `TEXT2SQL_COMPLEXITY_GATE`(off) ON 시 complex만 다중후보(단순 억제) |
| **E4. 실행기반 후보 선택 (D-074)** | ✅ **구현** | `src/nodes/candidate_selector.py`(규칙필터→읽기전용 실행→결과일관성 투표→hybrid LLM 쌍대비교→전패 all_failed 강등). validate·execute 주입으로 경로 비대칭 차단. `run_candidate_pipeline` 공유 헬퍼. `TEXT2SQL_SELECTION`(hybrid) 노출. 3단 폴백: `text2sql_fallback` state·`_decide_fallback_tier`·기존 approval_gate HITL 재사용 |
| **E5-1. 유연 매칭** | ✅ **구현** | `src/utils/flex_match.py` 신설(자모 분해·편집거리·부분어 + 빈문자열/1글자 가드 + 신뢰도 점수). 적용 2지점 완료 — ① `field_mapper._synonym_match(fuzzy=...)`(폼필 경로, 임계 이하는 확정 아닌 `pending_synonym_registrations` 후보 회부) ② `schema_analyzer._synonym_tables_matching_query(fuzzy=...)`(텍스트 질의 경로). `SYNONYM_FUZZY_MATCH=false` 기본 OFF·OFF 시 기존 경로 무변경 |
| **E5-2. 값 검색 승격** | ✅ **런타임 주입 완료** | 인프라 `value_index.py`+`redis_cache` + **소비 연결**(`check_coverage._validate_literals`). **런타임 적재(2026-07-15)**: `value_index.load_or_build_value_index`(load 우선·spec 도출 build+save)를 `schema_analyzer`가 `value_retrieval` ON 시 `state.column_value_index` 적재, `query_gen_common.build_value_index_block`로 검증 리터럴을 생성 프롬프트(경로 A/B)에 주입. `SYNONYM_VALUE_RETRIEVAL=false` 기본 OFF. 잔여: 경로 C 프롬프트 주입은 후속(경로 C는 자체 스키마 분석) |
| **E5-3. 사전 위생·거버넌스** | ✅ **구현** | 상한 config화 + **거버넌스(2026-07-15)**: 유사어 메타(등록출처·사용횟수·최종사용일·신뢰도) Redis 확장(`{words,sources,meta}` 하위호환)·`increment_synonym_usage`·`prune_stale_synonyms`(operator·레거시 보존·strictly-older 경계)·충돌 우선순위 `src/utils/synonym_governance.py`(순수 랭킹, utils 계층). `SYNONYM_GOVERNANCE=false` 기본 OFF=저장 무변경 |
| **E5-4. 의미(임베딩) 검색** | ❌ **미착수(자리예약)** | `SYNONYM_SEMANTIC_MATCH` 플래그만 config에 자리예약(주석에 "보류 — 미구현" 명시). `src/schema_cache/synonym_semantic.py` 부재. 설계대로 E5-1 후 E1 잔여 미매칭율 측정 게이트 대기 |
| **E5-계측 (IP-4)** | ✅ **구현(하네스 축)** | E1 러너의 A/B 축(`--ab synonym_fuzzy` 등)으로 유사어 on/off EX 비교 가능 |
| **E6-1. 시맨틱 모델 스키마** | ✅ **구현** | `config/semantic_models/{polestar_cm_gp,polestar_cm_yd,polestar_b0}.yaml` 신설(패턴 A/B/C 카탈로그). db_profiles 불변·입력소스로만 참조. db_engine/db_schema는 저장 안 함(get_domain_by_id 주입, D-066후속6). +2026-07-14: 로컬 샌드박스 `polestar.yaml` 추가(gp 복제·DB_DOMAINS 재등재 — 로컬 트랙 C 검증 환경 편입, D-076 후속 변경이력 참조) |
| **E6-2. SMQ + 결정적 컴파일러 (D-076)** | ✅ **구현** | `src/nodes/semantic_compiler.py`(SMQ Pydantic gold_smq 계약 일치·로더·커버리지·컴파일). 패턴 A/B는 **`build_multi_resource_pivot_sql`(D-068) 재사용**(`query_gen_common`에 `explicit_measures` 후방호환 추가, 폼필 콜러 바이트무변경 실측), 패턴 C는 알람 정규화조인 전용. Model/MODEL 대소문자충돌 정확이름 우선 해소. `src/prompts/semantic_compiler.py`(NL→SMQ 프롬프트). +2026-07-14: LLM SMQ의 dimensions=[] 선택 누락(측정만 선택 → 식별 컬럼 없는 값 나열)을 `pattern_b.default_dimensions`(name/hostname) 결정적 주입으로 보정(D-035 — 프롬프트 유도 아님, 명시 dimension은 존중. D-076 후속2) |
| **E6-3. coverage_router + 폴백** | ✅ **구현(3경로 배선)** | `compile_from_nl`(coverage_router)을 `query_generator` 진입부(경로 A·B 자동공유)+`multi_db_executor._generate_sql`(경로 C 명시이식). 커버리지 밖은 현행 LLM 폴백(`semantic_fallback=llm` 과도기 — 트랙 A 착수 시 candidate_then_human 전환). `TEXT2SQL_SEMANTIC_COMPOSE=false` 기본 OFF·OFF 시 진입가드 False로 바이트무변경. 3단 폴백의 신뢰도 게이트·사람검토(HITL)는 트랙 A 착수 시 |
| **E6-4. 골든 회귀셋** | ✅ **구현** | `tests/text2sql/test_semantic_golden.py` 34건(gold_smq 라운드트립 하네스 `smq_match`·패턴 A/B/C 구조·엔진 방언 DB2·커버리지 판정·E5-2 리터럴·결정성·NL→SMQ mock LLM·플래그 OFF). 골드셋 gold_smq 6건(gp-001/004/009/012·yd-001·b0-004)으로 A/B/C·3DB 커버 |

### 12.2 config·플래그 실측 (§5 대비)

- 구현·노출됨(기본 OFF): `SYNONYM_FUZZY_MATCH`, `SYNONYM_VALUE_RETRIEVAL`(E5-2 런타임 적재+주입), `SYNONYM_SEMANTIC_MATCH`(자리예약), `SYNONYM_MATCH_CONFIDENCE_MIN`(0.85), `SYNONYM_MAX_SYNONYM_SUPPLEMENT_TABLES`(15), **`SYNONYM_GOVERNANCE`(E5-3 거버넌스)·`SYNONYM_DECAY_DAYS`(180)**, `TEXT2SQL_SEMANTIC_COMPOSE`, `TEXT2SQL_SEMANTIC_FALLBACK`(기본 **`candidate_then_human`** — 트랙 A 착수로 전환), `TEXT2SQL_FALLBACK_CONFIDENCE_MIN`(0.0), **`TEXT2SQL_MULTI_CANDIDATE`·`TEXT2SQL_CANDIDATE_COUNT`(3)·`TEXT2SQL_CANDIDATE_STRATEGIES`(multi_prompt)·`TEXT2SQL_COMPLEXITY_GATE`·`TEXT2SQL_SELECTION`(hybrid)**. `.env.example` 반영 완료(별도 줄 주석 규칙 준수).
- config 클래스: `SynonymMatchConfig`(env_prefix `SYNONYM_`)·`Text2SQLConfig`(env_prefix `TEXT2SQL_`) — 트랙 A 필드·E5-3 거버넌스 필드 추가.

### 12.3 잔여 작업 (우선순위 = §0.3 트랙 순서)

1. ~~**D-번호 등재**~~ **완료(2026-07-14)**: D-072(E1 하네스)·D-075(동의어 매칭)·D-076(시맨틱 조합) `docs/02_decision.md` 등재(`## D-` 헤더+변경이력 표 재확인, 등재 최댓값 D-068→D-076). D-073/074는 트랙 A 착수 시.
2. ~~**트랙 C(E6) 착수**~~ **완료(2026-07-14)**: `config/semantic_models/` 3종 + `semantic_compiler`(D-068 자산 재사용) + `coverage_router` 3경로 배선 + 골든 34건. 기본 OFF·회귀 0.
3. ~~**E5-2 소비 지점 연결**~~ **완료(2026-07-14)**: `check_coverage._validate_literals`가 value_index로 SMQ 필터 리터럴 검증. 잔여: 값 인덱스 **런타임 주입(state 적재)** — 트랙 A(E2 프롬프트 주입)와 함께 착수.
4. ~~**E5-3 잔여**~~ **완료(2026-07-15)**: 유사어 메타·감쇠(`prune_stale_synonyms`)·충돌 우선순위(`synonym_governance.py`). `SYNONYM_GOVERNANCE=false` 기본 OFF.
5. ~~**트랙 A(E2~E4)**~~ **완료(2026-07-15)**: E1 측정 커버리지 밖 23.1% + §9-1 사용자 기결정으로 착수. candidate_generator/selector·복잡도 게이트·`semantic_fallback` 기본값 `candidate_then_human` 전환·3단 폴백(신뢰도 게이트·approval_gate HITL 재사용). D-073·D-074 등재.
6. **E5-4**(미착수 — 착수 근거 소멸): 오프라인 임베딩 모델 반입 게이트 대기였으나 **시딩으로 근거 소멸**. E1 실측(2026-07-15) E5-1 잔여 미매칭 38.5%의 원인이 의역이 아니라 **Redis 사전에 성능지표·알람 동의어 부재**임을 규명 → 사용자 지시로 **시맨틱 모델 aliases→Redis 시딩 체계 구현**(팀리드): `scripts/synonym_seeds.py`(derive/load/export CLI), `config/synonym_seeds/{4종}.yaml`(시맨틱모델+프로필 결정적 생성·git 마이그레이션 아티팩트), `synonym_loader.load_seed_yaml`(per-DB 합집합·source=operator 태깅으로 E5-3 감쇠 보호). **효과 실측: E5-1 질의 잔여 미매칭 38.5%→0%**(정확 히트 12→22/26, 퍼지 포함 26/26). ⇒ **현 골드셋 기준 E5-4(임베딩) 착수 근거 소멸** — 최종 판단은 실사용 질의 로그 재측정 후. 운영 절차 `docs/synonym_seed_migration_guide.md`(신규 DB 편입 체크리스트 ⑤시드 생성·로드 추가).
7. ~~**골드셋·픽스처 정비**~~ **B4-(b) 완료(2026-07-15, 사용자 결정=페어 정렬)**: E1 실측 골드셋↔시맨틱모델 규약 모순(gp-001~003 gold_sql=`platform.server%` 40행 vs gold_smq=`server.Server` 10행 서로소 모집단)을 **양방향 쌍둥이 픽스처로 해소** — `testdata/pg/init/08_plan61_population_pairing.sql` 신설(멱등, ID 9670001~9671999, resource_conf_id 공유, 06/07 불변), 골드셋 gp-001 gold_smq 모순 필터 제거. **검증: 두 장부 50=50·hostname 대칭차 0·gp-001 두 규약 EX 동치. E1 v6 재측정 server_config semantic ON 7/7 만점(2회 재현, 정비 전 4/7) — 교란 해소 실증.** test_e2e_polestar 17 failed 불변(회귀 0), 골든 36건 통과. 스키마 판정: 실캡처(05_insert)는 server.Server 앵커만·platform.% 0행 → 실 폴스타 형상은 server.Server. 상세 `docs/plan61_bugfix_plan.md` B4. **⚠ 잔여(프로필 영역·실 DB 확인 필요)**: gp/yd/b0 프로필 `query_examples`의 `LIKE 'platform.server%'` 술어(각 7곳)가 실 DB에서 0행일 가능성(실캡처상 platform.% 부재) → 실 gp 접속 시 1회 실측 후 프로필 예시 정비 검토. (c) 표기 변형 질의 항목 추가(fuzzy EX 측정용)는 미착수.
8. **잔여(후속)**: (a) 경로 C(multi_db) 값 인덱스 프롬프트 주입(현재 경로 A/B만), (b) 커버리지 확장(HAVING 서버필터·동적 날짜·집계 상위 N·LOB) — D-012 승인루프, (c) 실 DB 접속 환경에서 트랙 A EX 이득·후보수 N 이득 곡선 실측(현재 mock/정적). **config 임포트 고정 버그(nested config 인스턴스 기본값 → `Field(default_factory=...)`)는 팀리드가 별도 수정·Known Mistakes 등재**(in-process A/B·env 조작 테스트 무효화 원인). E1 하네스 실접속 경로 버그 3건도 팀리드 수정 완료(§12.1 E1 실접속 최초 성공).
