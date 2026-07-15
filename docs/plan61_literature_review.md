# Plan 61 문헌 정합성·성능개선 타당성 검토

> 검토일: 2026-07-13
> 대상: `plans/61-text2sql-candidate-selection.md` (v4)
> 근거: 초록 검증 완료 문헌 8편(§ Plan 61 §10), `docs/text2sql_quality_research.md`, `docs/deterministic_sql_composition_review.md`, `docs/synonym_management_analysis.md`
> 목적: 계획이 문헌에 적절히 근거하는가, 실제로 성능(EX·환각·비용)을 개선하는가

---

## 0. 종합 판정

**적절함 — 단, 4개 지점의 보정이 필요.** 계획의 골격(E1 측정 선행 → 트랙 C 결정적 조합 주력 → 트랙 A 조건부 폴백 → 트랙 B 링킹 병행)은 검증된 문헌에 정확히 근거하며, 무회귀 옵트인 설계로 리스크가 통제된다. 성능 개선 기대는 **구조적으로 타당**하다.

그러나 다음 4개는 문헌 근거를 과대적용하거나 누락한 지점으로, 착수 전 보정해야 개선 효과가 계획대로 실현된다:
1. 트랙 C "환각 0"은 **구조적 오류에 한정**되며 SMQ 선택 오류는 남는다 (효과 과대 서술).
2. NL→SMQ 생성 자체가 **핵심 난이도**인데 리스크로 미분리 (RYANSQL 슬롯/값 예측 약점).
3. 트랙 C의 **리터럴 정확도가 트랙 B(E5-2)에 의존**하는데 그 의존이 명시 안 됨.
4. "Death of Schema Linking" 문헌이 **표에만 있고 설계에 미반영** — E5-3/D-051 과잉가지치기와 충돌 가능.

---

## 1. 문헌에 잘 근거한 부분 (개선 기여 인정)

| 계획 요소 | 문헌 근거 | 타당성 |
|-----------|-----------|--------|
| **E1 EX 하네스 선행** | Spider·BIRD 전 문헌이 EX로 평가; 측정 없는 개선 주장 불가 | 강함. 모든 후속 판단의 데이터 근거 확보 |
| **트랙 C 주력(결정적 조합)** | 2606.31041(SMQ+결정적 컴파일러), 2604.25149(시맨틱문서 +17~23%p) | 강함. 안정적·반복적 도메인(폴스타)에 구조적 적합 |
| **E2 다양성 > 개수** | CHASE-SQL·XiYan-SQL(다중 추론경로 앙상블) | 강함. `temperature`만 쓰는 흔한 실수를 회피 |
| **E4 실행기반 선택(결정적 투표 1차)** | CHASE-SQL 선택 에이전트, execution-guided decoding | 타당. 파인튜닝 부재 제약을 결과일관성으로 우회 |
| **E3 복잡도 분기** | DIN-SQL query classification | 타당. 비용 통제 목적 정합 |
| **무회귀 옵트인 플래그** | (엔지니어링 원칙) | 강함. 실패 시 즉시 현행 복귀 |

이 6개는 문헌·프로젝트 원칙(D-035 결정적 우선, D-003 읽기전용) 모두와 정합하며, 계획대로면 EX 향상·환각 감소·비용 통제를 동시에 얻을 구조다.

---

## 2. 보정이 필요한 지점 (효과 실현의 전제)

### 2-1. "환각 0"은 구조적 오류에 한정 — SMQ 선택 오류는 남는다
- **문제**: §7 트랙 C 검증기준의 "**환각 0**(미존재 컬럼/금지 조인 발생 불가)"은 **결정적 컴파일 단계**에만 참이다. 컴파일러가 조인·집계·컬럼을 정의된 부품으로만 조립하므로 *구조적* 환각은 0이 맞다. 그러나 **LLM이 어느 dimension/measure를 고를지(SMQ 생성)**는 여전히 추론이며, 잘못된 dimension 선택은 "문법적으로 정상이나 의미상 틀린 SQL"을 낳는다.
- **문헌 근거**: 2606.31041도 순수 결정적이 아니라 "제약된 think-act loop로 SMQ를 조합"한다 — 선택 단계에 오류 여지가 있음을 전제한다. EX 94.15%가 100%가 아닌 이유가 이것이다.
- **보정**: §7 문구를 "**구조적 환각 0**(조인·집계·미존재 컬럼) + SMQ 선택 정확도는 별도 측정"으로 정정. 트랙 C가 없애는 것은 오류의 *일부*(구조)이지 전부가 아님을 명확히.

### 2-2. NL→SMQ 생성이 핵심 난이도 — 독립 리스크로 분리 필요
- **문제**: E6-2는 LLM 역할을 "컬럼 이름 선택만"으로 축소한다고 서술하나, **필터 조건·값·시간창·집계 종류 선택**은 단순 선택이 아니라 난이도 있는 추론이다. 계획은 이를 "선택만"으로 과소평가한다.
- **문헌 근거**: RYANSQL(2004.03125)은 스케치는 잘 맞혀도 **슬롯(조건값) 예측이 약점**이라 명시한다. 템플릿/슬롯 계열의 공통 병목이 바로 값·조건 채우기다.
- **보정**: E6 리스크 표에 "**SMQ 생성 정확도**(필터·값·시간창·집계 선택 오류)" 항목 추가. E1 하네스에 SMQ 생성 정확도(골드 SMQ 대비)를 컴파일 결과 EX와 분리해 측정하는 축 추가.

### 2-3. 트랙 C의 리터럴 정확도 → 트랙 B(E5-2) 의존 명시
- **문제**: E6-2 컴파일러는 구조를 조립하지만 WHERE 리터럴(`resource_type='server.Server'`, EAV `NAME='Hostname'`)은 **정확한 실측 값**이어야 한다. 이 값 공급이 곧 트랙 B의 E5-2(값 검색 승격)인데, 계획은 트랙 C와 E5-2의 의존을 끊어서 서술한다.
- **함의**: 트랙 C를 트랙 B E5-2 없이 단독 착수하면 리터럴 환각(Plan 25 유형)이 컴파일러를 우회해 재발한다.
- **보정**: E6-2에 "**리터럴은 E5-2 값 검색으로 검증된 값만 사용**(미검증 값이면 커버리지 밖 처리)" 의존을 명시. §9 실행순서에서 "트랙 C 착수 시 E5-2 동반 필수"를 권고.

### 2-4. "Death of Schema Linking"을 설계에 반영 — 과잉 가지치기 경계
- **문제**: 2408.07702는 "추론형 모델은 무관 컬럼을 스스로 걸러내므로 **공격적 스키마 링킹/필터가 오히려 필요한 컬럼을 제거해 해가 될 수 있다**"고 지적한다. 계획은 이를 §0.1 표에만 넣고 설계에 반영하지 않았다.
- **충돌 지점**: E5-3(사용빈도·신뢰도 상위만 선별 주입)과 D-051(유사어 테이블 상한 15)은 **정확히 공격적 가지치기**다. 필요한 dimension/테이블을 임계 미달로 잘라내면 트랙 C 커버리지 판정이 "밖"으로 오분류되거나 트랙 A 후보 입력이 빈약해진다.
- **보정**: E5-3·D-051 상한을 **고정 규칙이 아니라 E1 하네스로 튜닝하는 파라미터**로 전환. "상한을 낮추면 토큰↓이나 리콜↓ — EX로 최적점 탐색" 문구 추가. 트랙 C 커버리지 판정에서 임계 미달이라도 **후보 dimension으로 남겨 판정에 포함**.

---

## 3. 경미한 정합성 지적 (선택적)

- **XiYan M-Schema 미조작화**: §0.1이 XiYan을 "E2 앙상블·스키마 표현"에 대응시키나, 스키마 표현(M-Schema) 변경 단계는 계획에 없다. → 범위 밖임을 명시하거나 E1에서 스키마 표현 A/B를 옵션으로 추가.
- **DIN-SQL 자기수정 매핑**: §0.1이 DIN-SQL을 "E4 자기수정"에 대응시키나, E4는 **에러기반 재시도**이지 능동적 자기비판(self-critique)이 아니다. → "에러기반 재시도(자기수정의 축소형)"로 표기 정정.
- **골드셋 대표성**: E1 골드셋을 `sqls/act`(실행 이력)에서 뽑으면 **이미 현행이 처리하던 질의로 편향**될 수 있어 커버리지율이 낙관적으로 측정된다. → 실패 로그·미처리 질의도 골드셋에 포함하는 표집 원칙 추가.

---

## 4. 성능 개선 전망 (정직한 기대치)

- **트랙 C(주력)**: 커버리지 내 정형 질의에서 **구조적 환각 제거 + 비용 절감**은 문헌·구조상 확실. 단 실제 EX 향상폭은 (a) NL→SMQ 생성 정확도(2-2)와 (b) 리터럴 공급(2-3)에 의해 결정되며, 이 둘이 부실하면 기대만큼 안 오른다.
- **트랙 A(폴백)**: 커버리지 밖 EX를 올리나 silent-wrong(모든 후보가 같은 오답)은 원리상 못 잡는다(2-1과 대칭). 비용 N배 대비 이득은 E1 측정 후 판단이 옳다.
- **트랙 B(병행)**: E5-1·E5-2는 저비용으로 링킹·리터럴 환각을 직접 개선 — 트랙 C 성능의 **전제조건**이므로 사실상 필수.
- **총평**: 계획대로면 개선은 실현된다. 단 "개선폭"은 위 4개 보정과 E1 측정에 달려 있으며, 보정 없이는 **트랙 C 효과가 과대평가**될 위험이 있다.

---

## 5. 권고 (착수 전 반영 순서)

1. **E1 하네스에 측정 축 2개 추가**: SMQ 생성 정확도(구조 EX와 분리), 커버리지율(대표성 있는 골드셋).
2. **§7 트랙 C 기준 정정**: "환각 0" → "구조적 환각 0 + 선택 정확도 별도 측정".
3. **트랙 C ← E5-2 의존 명시**: 트랙 C 착수 시 값 검색 동반 필수.
4. **E5-3·D-051 상한을 튜닝 파라미터화**: Death-of-Schema-Linking 경고 반영, EX로 최적점 탐색.
5. 경미 지적(§3)은 문구 정정 수준으로 반영.

이상 5개를 반영하면 계획은 문헌 정합성과 성능개선 타당성 양면에서 완결된다.

---
---

# [부록] 수정 내용 전체 테스트 프롬프트 (2026-07-15)

> 2026-07-15 세션에서 수정·구현된 전체 항목을 사용자 관점에서 검증하기 위한 프롬프트 모음.
> 각 항목: **플래그(사전 조건) → 입력 프롬프트(복붙용) → 합격 기준 → 확인 포인트(로그)**.
> 대상 변경: 트랙 A(E2~E4 다중 후보)·트랙 C(시맨틱 컴파일)·E5-1(퍼지)·E5-2(값 검색)·E5-3(거버넌스)·
> 유사어 시딩·B4 페어 정렬·E1 하네스/config/e2e 버그 수정.

## T0. 사전 준비 (모든 시나리오 공통)

```bash
# 1) 인프라 확인: polestar_pg(:5434)·collectorinfra-redis(:6380)·DBHub(:9099) 구동
docker ps | grep -E "polestar_pg|redis"

# 2) 픽스처 적용 (멱등 — 재실행 안전)
docker exec -i polestar_pg psql -U polestar_user -d infradb < testdata/pg/init/07_plan61_text2sql_gold_fixtures.sql
docker exec -i polestar_pg psql -U polestar_user -d infradb < testdata/pg/init/08_plan61_population_pairing.sql

# 3) 유사어 시드 생성·로드
python scripts/synonym_seeds.py derive --db all
python scripts/synonym_seeds.py load --db polestar

# 4) 플래그는 .env에서 설정(주석은 반드시 별도 줄) 후 서버 재기동
#    TEXT2SQL_SEMANTIC_COMPOSE / TEXT2SQL_MULTI_CANDIDATE / SYNONYM_FUZZY_MATCH /
#    SYNONYM_VALUE_RETRIEVAL / SYNONYM_GOVERNANCE
```

## T1. 트랙 C — 시맨틱 결정적 컴파일 (D-076)

**플래그**: `TEXT2SQL_SEMANTIC_COMPOSE=true` (나머지 OFF)

| # | 입력 프롬프트 | 합격 기준 |
|---|--------------|-----------|
| T1-1 | `호스트명, OS종류, 벤더를 조회해줘` | 서버 50행(NULL hostname 4행 = Plan52 노이즈 포함), 패턴 A 피벗 SQL |
| T1-2 | `서버 수를 조회해줘` | **50** (B4 페어 정렬 후 두 규약 동일값) |
| T1-3 | `서버별 호스트명, IP, OS버전, 시리얼번호를 조회해줘` | 50행, EAV 속성은 30대만 값 보유(나머지 NULL 정상) |
| T1-4 | `현재 발생 중인 심각(severity 3) 알람 목록을 조회해줘` | 패턴 C(CA↔D↔ACTIVE 조인) 컴파일 또는 커버리지 밖 폴백 — 환각 조인 없어야 함 |

- **확인 포인트**: 서버 로그에 `시맨틱 결정적 컴파일 성공(패턴 A)` + `시맨틱 결정적 컴파일 SQL(LLM 우회)`. 커버리지 밖이면 `시맨틱 커버리지 밖(폴백)` — 둘 중 하나는 반드시 찍혀야 함(아무 로그도 없으면 게이트 미진입 = 플래그 미반영 의심 → **config 임포트 고정 버그 수정(B2)이 무효화된 것이므로 회귀**).

## T2. B4 페어 정렬 — 두 규약 등가성

**플래그**: 동일 질의를 `TEXT2SQL_SEMANTIC_COMPOSE` **true/false 각각**으로 실행

| # | 입력 프롬프트 | 합격 기준 |
|---|--------------|-----------|
| T2-1 | `호스트명, OS종류, 벤더를 조회해줘` (ON→OFF 2회) | **두 실행의 서버 집합·값 동일**(ON=server.Server 피벗, OFF=LLM 자유생성 — platform.server% 예시를 따라도 같은 결과) |
| T2-2 | `서버 수를 조회해줘` (ON→OFF 2회) | 둘 다 50 |

```bash
# DB 수준 등가 검증(질의 없이):
docker exec polestar_pg psql -U polestar_user -d infradb -t -c "
SELECT (SELECT COUNT(*) FROM polestar.cmm_resource WHERE resource_type LIKE 'platform.server%' AND dtime IS NULL)
     = (SELECT COUNT(*) FROM polestar.cmm_resource WHERE resource_type='server.Server' AND dtime IS NULL);"
# → t (true)여야 함
```

## T3. 트랙 A — 다중 후보 생성·선택 (D-073/D-074)

**플래그**: `TEXT2SQL_MULTI_CANDIDATE=true`, `TEXT2SQL_SEMANTIC_COMPOSE=false`

| # | 입력 프롬프트 | 합격 기준(로그) |
|---|--------------|----------------|
| T3-1 | `호스트명, OS종류, 벤더를 조회해줘` | `다중 후보 생성: 요청 3 · 고유 1~3 (전략=multi_prompt)` → 고유 1이면 `다중 후보 선택: method=single conf=1.00` |
| T3-2 | `'DB-ORA-023' 서버와 동일한 CPU 코어 수 및 메모리 용량을 가진 서버를 조회해줘` | 고유 2~3 후보 → `method=consistency`/`llm_pairwise`/`hybrid` 중 하나 + conf 값 |
| T3-3 | `최근 3개월간 장애 알람이 가장 많이 발생한 상위 10개 서버를 조회해줘` | 전 후보 실행 실패 시 `method=all_exec_failed conf=0.00` 후 기존 재시도 루프로 강등(응답은 정상 흐름) |
| T3-4 | (`TEXT2SQL_COMPLEXITY_GATE=true` 추가) `SV-WEB-001 서버의 IP를 알려줘` | 단순 질의로 분류 → 후보 생성 **미진입**(단일 경로 로그) |

- **확인 포인트**: 선택 근거 audit(후보별 실행 결과·투표)이 로그/감사에 기록. OFF로 되돌리면 후보 로그가 전혀 없어야 함(회귀 0).

## T4. E5-1 퍼지 매칭 + 유사어 시딩 효과

**플래그**: `SYNONYM_FUZZY_MATCH=true` + 시드 로드 완료(T0-3)

| # | 입력 프롬프트 | 검증 대상 |
|---|--------------|-----------|
| T4-1 | `메모리 사용률이 가장 높은 서버를 알려줘` | 시딩 효과 — "메모리 사용률"이 `cmm_metric_stat_*` 테이블을 스키마 보충으로 올림(시딩 전엔 미매칭이던 어휘) |
| T4-2 | `메모리사용률 높은 서버` (붙여쓰기 변형) | 퍼지 — 공백 변형 흡수 |
| T4-3 | `심각 알람이 몇 건인지 알려줘` | 시딩 — "심각"→`cmm_alarm.alarmseverity` 게이팅 + 값 매핑(=3) |
| T4-4 | `호스트네임과 아이피 목록 조회` | 시딩(호스트네임) + 퍼지(아이피↔IP) |
| T4-5 | `디스크 아이오가 높은 서버` | 시딩 — 패턴 B MaxIORate aliases |

```bash
# 정량 검증(질의 없이): 시딩 후 잔여 미매칭율 — 질의 수준 0%가 기준선
python <세션 scratchpad>/measure_e51_residual.py   # 또는 가이드 §5 참조
# 사전 규모 확인
redis-cli -p 6380 -a $REDIS_PASSWORD HLEN schema:polestar:synonyms   # ≥75
```

## T5. E5-2 값 검색 / E5-3 거버넌스

**플래그**: `SYNONYM_VALUE_RETRIEVAL=true` / `SYNONYM_GOVERNANCE=true`

| # | 입력 프롬프트 | 합격 기준 |
|---|--------------|-----------|
| T5-1 | `비정상 서버 목록과 IP를 조회해줘` | `avail_status != 0` 변환(1건: SV-BATCH-009 계열 + 페어) — "1=정상" 류 환각 없음 |
| T5-2 | `심각(severity 3) 알람 목록` | 시맨틱 ON 시 SMQ 필터 리터럴이 value_index로 검증됨(미검증 리터럴이면 커버리지 밖 폴백) |
| T5-3 | (거버넌스 ON에서 T4 질의 반복 후) | `redis-cli HGET schema:polestar:synonyms <키>` 의 meta에 usage_count 증가·last_used 갱신. `prune` 호출 시 **operator(시드) 단어는 보존** |

## T6. 회귀 — 전 플래그 OFF (기본값)

**플래그**: Plan 61 계열 전부 false (`.env`의 로컬 검증용 true 값들을 false로)

| # | 입력 프롬프트 | 합격 기준 |
|---|--------------|-----------|
| T6-1 | `호스트명, OS종류, 벤더를 조회해줘` | 기존 단일 LLM 생성 경로(시맨틱/후보 로그 0건), 정상 응답 |
| T6-2 | `SV-WEB-001 서버의 가용성 상태와 IP를 조회해줘` | 기존 동작과 동일 |
| T6-3 | (폼필) 서버 현황 양식 업로드 + `공동존 폴스타의 모든 서버` | 기존 폼필 경로(D-068 피벗) 무회귀 |

## T7. 자동화 검증 명령 모음 (프롬프트 없이 일괄)

```bash
# 단위·통합 (기준선: 49 failed / 2298 passed — 전부 기존 환경 의존 실패, Plan 61 무관)
python -m pytest tests/ -q

# Plan 61 신규 스위트만
python -m pytest tests/text2sql/ tests/test_schema_cache/test_synonym_seeds.py \
  tests/test_schema_cache/test_synonym_metadata.py tests/test_utils/test_synonym_governance.py \
  tests/test_nodes/test_query_generator_multi_candidate.py tests/test_config_env_reload.py -q

# E1 하네스 실측 A/B — 반드시 프로세스 분리(export)로. in-process 플래그 플립 금지(B2 이력)
TEXT2SQL_SEMANTIC_COMPOSE=false python scripts/eval_text2sql.py --db gp --path orchestration --json
TEXT2SQL_SEMANTIC_COMPOSE=true  python scripts/eval_text2sql.py --db gp --path orchestration --json
# 기대: server_config 카테고리 ON 7/7 (B4 정비 후 기준)

# 계층·아키텍처
python scripts/arch_check.py --ci    # exit 0

# e2e(playwright)는 기본 제외 — 명시 실행만
RUN_E2E=1 python -m pytest tests/e2e/ -q
```

## 판정 기준 요약

| 영역 | 핵심 합격 신호 | 실패 시 의심 지점 |
|------|---------------|------------------|
| 트랙 C | `시맨틱 결정적 컴파일 성공` 로그 + 서버 50 | config 임포트 고정 회귀(B2)·시맨틱 모델 로드 실패 |
| 트랙 A | `다중 후보 생성/선택` 로그 + method/conf | 플래그 미반영·candidate_* 모듈 임포트 |
| E5-1+시딩 | 성능지표·알람 어휘 질의가 올바른 테이블 게이팅 | 시드 미로드(HLEN<75)·Redis 접속 |
| B4 | 두 규약 서버 수 50=50 | 픽스처 08 미적용(컨테이너 재생성 시 재적용 필요) |
| 회귀 | OFF에서 Plan 61 로그 0건 + 기존 응답 동일 | 게이트 조건 누락 |
