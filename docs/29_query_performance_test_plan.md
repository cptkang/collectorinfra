# 29. 질의 성능·기능 테스트 계획 — 항목별 입력·양식·예상 결과

> 29_query_performance_test_plan.md
> 작성일: 2026-08-28 · 기준 브랜치: `multiintent` (HEAD d673a04)
> 근거: 라우팅 골든셋 `testdata/routing_gold/routing.yaml`(12건), EX 골든셋 `testdata/text2sql_gold/{gp,yd,b0}.yaml`(26건),
> 실물 양식 `tests/fixtures/forms/*.xlsx`(금감원 3종), `docs/synonym_test_cases.md`, `src/utils/query_gen_common.py`·`src/document/*`·`src/routing/*` 실측.
> 각 항목의 "예상 결과"는 현재 코드가 보장하도록 설계된 동작이다.

---

## 0. 공통 전제

| 항목 | 내용 |
|---|---|
| 과금 게이트 | 실 LLM 호출은 전부 `RUN_E2E=1` 옵트인 + **건별 승인**(D-127). 골든셋 배치(`scripts/eval_text2sql.py`, `scripts/eval_routing.py`)도 동일 |
| 플래그 상태 명시 | 기본 off 기능: 2단 라우팅(`router.two_stage_enabled`), `fault_diagnosis`, 실시간 사용률 API(`POLESTAR_REST_REALTIME_USAGE_ENABLED`), 하이퍼님 확장(`TEXT2SQL_HYPERNYM_AMBIGUITY`), 퍼지/시맨틱 유사어. 테스트 기록에 **어느 플래그로 돌렸는지** 반드시 적는다 |
| 사다리 단 확인 | 기동 로그의 `deep_agent \| intent_orchestration \| semantic_router \| legacy` 한 줄(`src/observability/ladder.py`) — 폐쇄망 정본은 **트랙 A(intent_orchestration)** |
| 시간 측정 지점 | ① 응답 `processing_time_ms`(`src/api/schemas.py`) ② `logs/audit-YYYY-MM-DD.jsonl`의 `execution_time_ms`(SQL 실행분만) ③ 실패 요청 한정 `logs/trace/<날짜>/<request_id>.jsonl`의 노드별 `elapsed_ms`(`OBS_TRACE_ENABLED=true`) ④ SSE `node_start`/`node_complete` 이벤트 간격(스트리밍 경로) |
| 목표치 | 단순 ≤10s · 복합(JOIN/집계) ≤30s · 문서 생성 ≤60s · 라우팅 판단 ≤5s(`spec.md:715-717`). 자동 단언 없음 → 수동 기록 |
| 판정 기록 | 항목당 `입력 / 라우팅 결과(intent·db_ids) / executed_sql 요약 / row_count / processing_time_ms / 합격 여부`. `PYTHONUTF8=1` 필수 |

**권장 실행 순서**: A(라우팅) → B/C(단일 DB 기본) → F(존 HITL) → D/E(알람·복합) → G(멀티턴) → H/I(폼필) → J(가드) → K(부하).
앞 단계가 깨지면 뒤 단계 실패 원인을 분리할 수 없다.

### 기록 템플릿

```
| ID | 실행일시 | 사다리 단 | 플래그 | intent / db_ids | executed_sql 요약 | row_count | processing_time_ms | 합격 | 비고 |
|----|---------|----------|-------|-----------------|------------------|-----------|--------------------|------|------|
```

---

## A. 의도 분류·라우팅 (목표 ≤5s, 라우팅 단계만)

골든셋 12건(`testdata/routing_gold/routing.yaml`)을 그대로 쓰고 아래로 보강한다.

| ID | 입력 질의 | 예상 intent | 예상 DB | 비고 |
|---|---|---|---|---|
| A-01 | 여의도 개발 서버들의 CPU 사용률을 보여줘 | data_query | polestar_cm_yd | 위치어 결정적 힌트 |
| A-02 | 은행존 서버의 디스크 사용량 조회 | data_query | polestar_b0 | DB2 경로 진입 |
| A-03 | 김포와 여의도 서버의 CPU 사용률을 비교해줘 | data_query | gp + yd (≥2) | **멀티DB 축소 회귀 감시 항목** |
| A-04 | 현재 활성 상태인 심각 알람 목록 보여줘 | alarm_query | polestar_* | |
| A-05 | polestar DB의 스키마 캐시를 갱신해줘 | cache_management | 없음 | DB 선택 단계 스킵 |
| A-06 | 조회 가능한 DB 목록과 설명을 알려줘 | cache_management(db-guide) | 없음 | |
| A-07 | 안녕하세요, 오늘 날씨 어때요? | general_inference | `[]` | |
| A-08 | 쿠버네티스란 뭐야? | general_inference | `[]` | 외부 IT 개념 |
| A-09 | 서버 상태 좀 확인해줘 | data_query | ≥1 | 저신뢰 프로브 — `relevance_score` 분포 확인(0.3 게이트) |
| A-10 | vcore, cpu, core은 동의어이다. 캐시에 등록하라. | cache_management(결정적 pre-gate) | — | LLM 미호출 확인 |
| A-11 | ○○ 서버 왜 느려? 원인 분석해줘 | flag off: data_query로 강등 / on: fault_diagnosis | — | 플래그 양쪽 실행 |
| A-12 | 서버 목록 양식 채워줘 (파일 없이) | 결정적 안내문 반환 | — | `_FORM_FILL_NO_FILE_GUIDANCE`, LLM 미호출 |

실행: `RUN_E2E=1 python scripts/eval_routing.py --out reports/routing_s1.json` (승인 후, 사전 `--dry-run`).
판정 기준: `intent_match / db_recall / db_precision / multi_preserved`.

---

## B. 단순 조회 — 서버 구성(EAV) (목표 ≤10s)

골든셋 gp/yd/b0 26건(`testdata/text2sql_gold/`)이 정본. 존별로 같은 질의를 반복해 **방언 대칭**을 확인한다.

| ID | 입력 질의 | 예상 SQL 특징 | 예상 결과 |
|---|---|---|---|
| B-01 | 전체 서버의 호스트명, OS종류, 벤더를 조회해줘 | `cmm_resource` + EAV 피벗(OSType, Vendor), `dtime IS NULL` | 전체 서버 수만큼 행, LIMIT 10,000(전체 키워드) |
| B-02 | 서버 수를 조회해줘 | `COUNT(*)` | 1행 1값 |
| B-03 | cocm-hdkapp01 서버의 OS 종류·버전, IP주소, 호스트네임, CPU 모델, 메모리 용량을 조회해줘 | `r.hostname = ...`, server.Server + server.Cpus + server.Memory 피벗 | 1행, 열 6개 |
| B-04 | 장비명이 'cob0-bnndbp01'인 서버의 가용성 상태와 IP를 조회해줘 | **`r.name`** 필터(hostname 아님) | 1행 |
| B-05 | 호스트명이 sbhdbo53인 서버의 CPU 리소스를 조회해줘 | **`r.hostname`** 필터 | LOGICALCORE/PHYSICALCORE/MODEL |
| B-06 | 비정상 서버 목록과 IP를 조회해줘 | `avail_status <> 0` | 0건 가능 — 0건이면 "데이터 없음" 응답이지 에러 아님 |
| B-07 | 논리코어가 8개 이상인 서버의 서버명, 논리코어 수, 메모리 용량을 논리코어 내림차순으로 보여줘 | `CAST(stringvalue_short AS NUMERIC) >= 8`(정수 캐스트 금지 D-160) | 정렬 확인 |
| B-08 | 서버별 CPU 코어(vcore) 수 합계를 조회해줘 | vcore→LOGICALCORE 유사어, NUMERIC 캐스트 | b0에서 `'4.0'` 값 캐스트 에러 없음 |
| B-09 | 장비명이 'cocm-xgzapp09'인 서버의 OS파라미터(커널 파라미터)를 조회해줘 | `stringvalue`(LOB) 사용 | 긴 텍스트 1행 |
| B-10 | 은행존 전체 서버별 호스트명, IP, 시리얼번호, CPU모델, CPU코어수, 메모리를 조회해줘 | DB2: `POLESTAR.` 스키마 한정, `FETCH FIRST`, 소문자 칼럼 | 2,328대 전수(1,328 절단 회귀 감시) |
| B-11 | 메모리 사이즈가 큰 순서로 상위 3대 서버를 보여줘 | `상위 3` → LIMIT 3 | 3행 |
| B-12 | 2026년 6월 서버별 CPU 사용률 평균이 높은 상위 3대를 보여줘 | `cmm_metric_stat_m`(LOGICALCORE 오매핑 금지) | 3행, 값 0~100 |

---

## C. 성능 통계 — 기간 파싱·집계 (목표 ≤30s)

기간 정규식 정확도가 핵심. `executed_sql`의 `stat_date` 리터럴을 확인한다. (실행일 2026-08 기준 예상값)

| ID | 입력 질의 | 예상 stat_date | 예상 결과 |
|---|---|---|---|
| C-01 | 전체 서버들의 CPU, 메모리 용량 및 사용률(평균·최대) 리스트를 조회해줘. 사용률은 지난달 1개월 통계 기준으로. | `= '202607'` | 서버당 1행, avg/max 열 |
| C-02 | 지난 3개월간 전체 서버별 월간 CPU, 메모리, 파일시스템, 디스크 IO 성능 통계를 조회해줘 | `BETWEEN '202605' AND '202607'` | 서버×월 행 |
| C-03 | 2026년 상반기 서버별 CPU 사용률 평균 | `BETWEEN '202601' AND '202606'` | |
| C-04 | 2026년 1월부터 6월까지 메모리 사용률 최대값 | 동일 범위 | 절대 월 범위 정규식(D-176) |
| C-05 | 11월부터 2월까지 CPU 사용률 | `202511~202602`(연도 넘김) | |
| C-06 | 이번 달 CPU 사용률 | `= '202608'` | 진행 중 월 |
| C-07 | 메모리 이용률이 90%를 초과한 적이 있는 서버를 조회해줘 | 기간 무제한, `max_val > 90` | '이용률' 동의어, '~한 적이 있는' |
| C-08 | 지난달 통계 기준으로 가동률이 낮은 서버 순으로 정렬해서 보여줘 | avg_val ASC | '가동률' 의역 — coverage=outside, 실패 허용 기록 |
| C-09 | 2026년 5월과 비교해서 6월에 CPU 사용률 평균이 가장 많이 상승한 서버와 상승폭을 알려줘 | 기간 self-join | 복합 |
| C-10 | 월 평균 CPU 사용률이 40%를 넘은 달이 2개월 이상인 서버와 그 개월 수를 보여줘 | GROUP BY + HAVING | |
| C-11 | 2026년 6월에 전체 서버 평균보다 CPU 사용률이 높았던 서버를 사용률과 함께 보여줘 | 스칼라 서브쿼리 | |
| C-12 | 지금 CPU 사용률 높은 서버 알려줘 | 플래그 off: 통계 테이블 / on: 실시간 REST | 원문 기준 `is_realtime_usage_query` 판정 |
| C-13 | 김포 폴스타 전체 서버를 통틀은 지난달 평균 CPU 사용률을 단일 값 하나로 알려줘 | 전역 집계(GROUP BY 없음) | 1행 1값 — `scope.global_aggregate_drop` 가드 계측 확인 |

---

## D. 알람 조회 (목표 ≤10s)

| ID | 입력 질의 | 예상 SQL | 예상 결과 |
|---|---|---|---|
| D-01 | 현재 발생 중인 심각(severity 3) 알람 목록을 조회해줘 | `cmm_alarm_active`, `alarmseverity = 3`, `CR.DTIME IS NULL` | 9열 표준 알람 뷰 |
| D-02 | 경고 이상 알람 이력을 최근 발생 순으로 100건 조회해줘 | `severity >= 2 ORDER BY ctime DESC`, **LIMIT 100** | 정확히 100행 |
| D-03 | 2026년 7월에 발생한 심각 알람이 몇 건인지 알려줘 | COUNT + ctime 범위 | 1값 |
| D-04 | 최근 3개월간 경고 이상 알람이 가장 많이 발생한 상위 10개 서버를 알려줘 | GROUP BY + ORDER BY COUNT DESC LIMIT 10 | 10행 |
| D-05 | 이번 달 CPU 임계값 초과 알람 이력 조회 | alarm_def 조인 | |
| D-06 | 담당자 없는 미확인 알람 | 통보 다단 조인(yd-006 계열) | outside — 실패 허용 기록 |

---

## E. 복합·멀티인텐트 (목표 ≤30s, 결과 병합)

`is_composite=true`가 되는지, 태스크 분해(`tasks[]`)와 `input_from` 사슬이 맞는지 확인한다.

| ID | 입력 질의 | 예상 분해 | 예상 결과 |
|---|---|---|---|
| E-01 | polestar DB 캐시를 갱신하고 서버 목록도 조회해줘 | t1 cache_management ∥ t2 data_query (병렬) | 두 결과 합산 응답 |
| E-02 | 현재 활성 상태인 심각 알람이 있는 서버들의 최근 1개월 CPU 사용률을 보여줘 | t1 alarm_query → t2 data_query(`input_from=t1`) | t1 호스트 집합이 t2 WHERE IN으로 전달 |
| E-03 | CPU 사용률이 높은 서버를 찾아 그 서버들의 프로세스를 분석해줘 | t1 data_query → t2 process_query | 프로세스 API 대상 = t1 결과 |
| E-04 | 김포 서버 목록과 여의도 서버 목록을 각각 알려줘 | 위치어 보존, DB별 실행 | 두 표 |
| E-05 | 해당 서버 프로세스 이력 지난 7일 보여줘 | process_query 아님 → data_query(이력 키워드 거부) | |
| E-06 | 논리코어가 8개 이상인 서버 중에서 지금까지 알람이 한 번도 발생하지 않은 서버를 알려줘 | 단일 SQL NOT EXISTS 또는 2태스크 | 결과 집합 동일해야 함 |
| E-07 | 심각 알람이 발생한 적이 있는 서버들의 제조사와 메모리 용량을 보여줘 | 3도메인 조인 | |

측정: 복합 fan-out 시 per-target 10s / total 45s 예산(`src/config.py` composite) 내 완료 여부.

---

## F. 존 선택 HITL·멀티DB (목표: 재개 턴 ≤30s)

| ID | 입력(1턴) | 예상 1턴 응답 | 입력(2턴) | 예상 2턴 결과 |
|---|---|---|---|---|
| F-01 | 전체 서버 OS 종류 알려줘 | `status=clarification`, `kind=zone_select`, 옵션 3개(b0/gp/yd) | `selected_db_ids=[polestar_cm_gp]` | gp만 실행, `resolved_limit=100000` |
| F-02 | 전체 서버 OS 종류 알려줘 | 동일 | `[polestar_cm_gp, polestar_cm_yd]` | 두 DB 결과 병합, 존 라벨 구분 |
| F-03 | 은행존 및 공동존 여의도 센터의 모든 서버들 OS종류 | **상호배타 재질문**(`group_exclusive`) | b0 선택 | b0만; 원문 '여의도' 언급이 WHERE로 새지 않음(D-154) |
| F-04 | ㅇㅇ존 서버 CPU 사용률 | 항상 재질문 | yd | 원문 `ㅇㅇ존`→'공동존 여의도' 치환 후 실행 |
| F-05 | 호스트명이 sbhdbo53인 서버의 CPU | **재질문 없음**(식별자 필터 억제) | — | 기본 DB 실행 |
| F-06 | (F-01 완료 후) 그 서버들의 메모리도 | 재질문 없음 — 이전 DB 승계 | — | 같은 db_ids |
| F-07 | 김포와 여의도 서버의 CPU 사용률을 비교해줘 | 재질문 없이 gp+yd | — | 멀티 경로 토큰 예산(D-159) 내, 비교 표 |
| F-08 | 공동존 전체 서버 수 | gp+yd 합산 | — | 두 COUNT 합 |

---

## G. 멀티턴 (지시어·승계)

`thread_id` 고정으로 연속 요청. 요청 본문에 thread_id가 실제로 실리는지 프론트까지 확인한다.

| 턴 | 입력 | 예상 |
|---|---|---|
| G-01-1 | cocm-hdkapp01 서버의 OS 확인 | 1행 |
| G-01-2 | 해당 서버의 최근 1개월 CPU 사용률 평균/최대 | `previous_entities`로 hostname 승계, stat 테이블 |
| G-01-3 | 네 그럼 메모리도 보여줘 | 같은 서버 유지 |
| G-01-4 | 해당 서버 프로세스 보여줘 | process_query, 대상 DB 승계 |
| G-01-5 | 지난 한 달 동안 해당 서버의 알람을 분석해줘 | alarm_query, hostname 필터 |
| G-02-1 | CPU 사용률 상위 3대 서버 | 3행 |
| G-02-2 | 그 서버들의 제조사와 모델 | 3대 IN 필터 |
| G-02-3 | 전체 서버 제조사 분포 | **승계 해제**(전체 키워드 우선) — 3대로 좁혀지지 않음 |
| G-03-1 | 그 장비 정보 (첫 턴, 이전 컨텍스트 없음) | 식별자 없음 → 재질문 또는 안내, 에러 아님 |
| G-04 | (G-01 진행 중) 프로세스 결과에 `name=mysql` 행이 있는 상태에서 "해당 서버 OS" | 프로세스 행이 서버로 수확되지 않음 |

---

## H. 폼필 (Excel/Word) — 목표 ≤60s

`POST /api/v1/query/file` (multipart: `query`, `file`, `thread_id`, `selected_db_ids`). 산출 파일은 **전 칼럼을 실제로 열어서** 검증한다.

### H-1. 단순 1행 헤더 양식

| ID | 양식 칼럼 구성 | 입력 질의 | 예상 매핑 | 예상 결과 |
|---|---|---|---|---|
| H-01 | `testdata/templates/server_list_template.xlsx`: `호스트명 \| IP주소 \| OS종류 \| CPU코어수 \| 제조사 \| 상태` | 김포 서버로 채워줘 | hostname / ipaddress / EAV:OSType / EAV:LOGICALCORE / EAV:Vendor / avail_status | 서버 수만큼 행, 서식·열 너비 보존, `mapping_sources`가 hint/synonym(LLM 추론 아님) |
| H-02 | 동일 양식 | 채워줘 (위치 없음) | — | **존 재질문**(파일 경로는 전체 키워드 불필요) → 선택 후 채움 |
| H-03 | `resource_status_template.xlsx`: `서버명 \| 리소스유형 \| 리소스명 \| 상태 \| 설명` | 여의도 리소스 현황 채워줘 | name / resource_type / … | |
| H-04 | 자작 양식: `서버명 \| 호스트명 \| IP \| OS버전 \| 메모리용량 \| 비고` | 은행존 채워줘 | 서버명→`name`(EAV Hostname 아님), OS버전→OSVerson, 비고→name(D-148 규칙) | DB2 경로; 서버명≠호스트명 열 값 다름 |
| H-05 | 자작 양식: `호스트명 \| CPU 평균 \| CPU 최고 \| 메모리 평균` | 2026년 6월 기준으로 채워줘 | classify_metric_field → (Cpus,AVG),(Cpus,MAX),(Memory,AVG), stat_date=202606 | 결정적 피벗 SQL, 서버당 1행, 값 0~100 |
| H-06 | 자작 양식: `호스트명 \| TPMC \| 도입일자 \| 용도` | 채워줘 | 도메인 밖 3열 공란 | 응답에 `[미작성 항목]` 사유 노출(침묵 공란 금지) |

### H-2. 금감원 2단 병합 헤더 양식 (실물, `tests/fixtures/forms/`)

CPU_양식 / 메모리_양식 칼럼(5·6행 병합):

```
구분|분류 · 제조사(모델명) · 호스트명 · 처리능력|(TPMC 또는 GB)
· 월중평균사용률(최근 6개월간)|M, M+1, M+2, M+3, M+4, M+5
· 월중 Peak시 사용률(최근 6개월간)|M, M+1, M+2, M+3, M+4, M+5 · 비고
```

서버_목록_리스트_양식 칼럼(5·6행 병합):

```
구분 · 서버명 · 용도 · 제조사(모델명) · 도입일자 · OS(버전정보포함) · IP
· 서버위치|설치장소 · 서버위치|설치구간 · 접근통제 및 추가인증|적용 솔루션명 · 접근통제 및 추가인증|추가인증수단 · 비고
```

| ID | 양식 | 입력 질의 | 예상 기준월 | 예상 결과 |
|---|---|---|---|---|
| H-10 | CPU_양식 | 2026년 6월 기준 CPU 양식 채워줘 | M=202601 … M+5=202606 | 평균 6열=avg_val, Peak 6열=max_val, 제조사(모델명)=Vendor+Model 결합, 처리능력(TPMC)=**공란**, 비고=name; 응답 `[기준월 안내]` 연도 정확(환각 시 `[확인 필요]`) |
| H-11 | CPU_양식 | 1월부터 6월까지의 데이터를 기준으로 양식을 채우시오 | 202601~202606(**2~7월 아님**, D-176) | |
| H-12 | CPU_양식 | 채워줘 (기간 없음) | 지난달 끝: 202602~202607 | `[기준월 안내]`에 "기간 지정 없어 지난달 기준" 명시 |
| H-13 | CPU_양식 | 1월부터 3월까지 채워줘 | 3개월만 요청, 6칸 | `[기간 불일치]` 안내, 나머지 3칸 처리 방식 명시 |
| H-14 | CPU_양식 | 지난 반년 데이터로 채워줘 | 202602~202607 | |
| H-15 | 메모리_양식 | 2026년 6월 기준 메모리 양식 채워줘 | 동일 | 리소스=server.Memory(제목 '주기억장치'로 인식), 처리능력(GB)=**TotalSize 채움** |
| H-16 | 서버_목록_리스트_양식 | 김포 서버 목록 양식 채워줘 | 월 시리즈 **미발동** | 서버명/제조사(모델명)/OS(버전정보포함)/IP 채움; 용도·도입일자·설치장소·접근통제 공란 + 사유 |
| H-17 | 은행존 × CPU_양식 | 은행존 2026년 6월 기준 채워줘 | 동일 | DB2 방언 피벗, 2,328행 |
| H-18 | `sample/취합 예시1.xlsx` (4행 헤더, 3행 부분 병합) | 채워줘 | 헤더=4행 단일(3행과 결합 **안 됨**) | D-145 부정 픽스처 |

### H-3. Word

| ID | 템플릿 | 입력 | 예상 |
|---|---|---|---|
| H-20 | `tests/e2e/fixtures/sample_template.docx`: `{{서버명}}`, `{{IP주소}}` + 표(서버명/IP주소/CPU사용률) | cocm-hdkapp01 서버 정보로 채워줘 | 플레이스홀더 치환, 표 5행 스타일 유지 |
| H-21 | 동일 | 김포 전체 서버로 채워줘 | 표 행 추가(스타일 복제), 플레이스홀더는 첫 행 값 |

---

## I. 폼필 HITL·기억

| ID | 시나리오 | 예상 |
|---|---|---|
| I-01 | H-04 양식에 `담당자` 열 추가 → 채워줘 | `form_fill_clarification{fields:[담당자], candidates(라이브 스키마)}` — 모든 열이 0이면 재질문 아님 |
| I-02 | 답변 턴: `form_fill_answers={"담당자":{"kind":"literal","value":"인프라팀"}}`, `form_fill_remember=true`, 파일 재첨부 없음 | 원본 바이트 재생, **존 유지**(D-177 — 로그 `폼필 답변 턴(D-151): … 존 복원=[...]`), 담당자 열 전행 '인프라팀', `[사용자 답변 적용 내역]` |
| I-03 | 같은 양식 재업로드 + 채워줘 | 저장값 자동 적용 `[확인 이력 적용]`, 재질문 없음 |
| I-04 | `?` | 저장 값 패널(`form_memory_panel`), LLM 미호출 |
| I-05 | 이 양식에 저장된 내용은? | 동일 패널 |
| I-06 | 기억한 값으로 채워줘 | 기억 명령 **아님** → 채움 실행 |
| I-07 | 패널에서 담당자 삭제(`form_memory_delete`) → 재업로드 | 담당자 재질문 부활 |
| I-08 | 월 시리즈 열에 답변으로 덮어쓰기 시도 | 거부 사유 "구조 채움 필드는 답변으로 변경할 수 없습니다" |

---

## J. 가드·안전성 (기능 회귀 — 시간 무관)

| ID | 입력 | 예상 |
|---|---|---|
| J-01 | 서버 테이블 전부 삭제해줘 | SELECT 외 생성 불가 → 검증기 에러·재생성 3회 후 실패 사유 응답. `logs/audit`에 DELETE 실행 흔적 0 |
| J-02 | 서버 목록 보여줘; DROP TABLE cmm_resource | 세미콜론 다중문/injection 패턴 거부 |
| J-03 | 전체 서버 목록 (LIMIT 없음) | 전체 키워드 → LIMIT 10,000 자동; "전체적으로 괜찮아?"는 전체 스코프 아님 |
| J-04 | 서버 목록 500건 | 명시 건수 우선 LIMIT 500 |
| J-05 | 담당자 이메일·전화번호 포함 알람 통보 대상 조회 | 결과 이메일 `a***n@…`, IP `192.168.1.***` 마스킹(설정 on 시) |
| J-06 | 주민번호 형태 문자열이 샘플에 있는 DB | 프롬프트 전 `scrub_pii`, 차단 시 `logs/pii_block` 덤프 + 응답에 차단 사유 |
| J-07 | 서버명 OS 확인 (cmm_resource 참조 SQL) | `dtime IS NULL` 누락 시 에러로 재생성 |
| J-08 | 만료된/role 없는 토큰으로 요청 | 401/403 — 인가 fail-closed |
| J-09 | 12 MB xlsx 업로드 | 413/400, 10 MB 한도 메시지 |
| J-10 | `.xls`/`.csv` 업로드 | 확장자 거부 메시지 |

---

## K. 부하·성능 (processing_time_ms 분포)

| ID | 방법 | 측정 | 합격 기준 |
|---|---|---|---|
| K-01 | B-01~B-06 각 5회 반복(캐시 warm) | p50/p95 | p95 ≤10s |
| K-02 | 동일 질의 **첫 호출**(스키마 캐시 cold, `캐시 삭제` 후) | 1회 | ≤30s, 이후 warm 회귀 |
| K-03 | C-02, C-09~C-11 각 3회 | p95 | ≤30s |
| K-04 | H-10, H-17(b0 2,328행) 각 3회 | 전체·`fill` 단계 | ≤60s |
| K-05 | `/query/stream` SSE로 B-01 | 첫 `node_start`까지 지연, `done`까지 총합 | 토큰 스트림 끊김 없음, 전체 타임아웃(`query_timeout` 60s) 가드 작동 |
| K-06 | 동시 5·10 세션(thread_id 별도)로 B-01·C-01 혼합 | p95, 에러율 | 에러 0, p95 ≤2× 단독 |
| K-07 | F-07(gp+yd) + 은행존 B-10 동시 | 토큰 예산 초과 여부 | `db_errors`에 "input tokens must be" 0건 |
| K-08 | 30초 초과 유도(C-10을 24개월 범위) | 타임아웃 응답 | 무한 대기 없음, 구조화 에러 |
| K-09 | 10,000행 초과 결과(전체 서버×시간 grain `cmm_metric_stat_h`) | max_rows 절단 | 절단 사실 응답에 명시 |
| K-10 | 재시도 예산 소진(고의 오매핑 유사어 등록 후 조회) | retry ≤3, 실패 사유 | trace 파일 생성 확인 |

---

## 도구

| 용도 | 명령 |
|---|---|
| 라우팅 배치 | `RUN_E2E=1 python scripts/eval_routing.py --out reports/routing_s1.json` (사전 `--dry-run`) |
| EX 배치 | `RUN_E2E=1 python scripts/eval_text2sql.py --path orchestration --db gp` (`--mock`으로 무과금 배관 확인 먼저) |
| 단건 실측 | `python scripts/run_pipeline_test.py --query "..." --step-by-step` |
| 회귀 기준선 | `pytest`(전체 약 32분, `PYTHONUTF8=1`), `python scripts/arch_check.py --ci`, `python scripts/overfit_check.py` |
| 수동 매트릭스 원본 | `docs/synonym_test_cases.md`(유사어 A~I 그룹), `docs/15_semantic_routing_verification.md` |
