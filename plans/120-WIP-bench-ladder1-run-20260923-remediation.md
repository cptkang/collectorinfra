# 120. 벤치 캠페인 `run-closed` 사다리 단 구간(`ladder-1`) 분석 — 판정 오염 3건과 폼필 월 시리즈 무력화(유사어 오염 사슬) 수정 계획

> **작성일**: 2026-09-28
> **상태**: **코드 단계 구현 완료(v2 · 2026-09-28 · D-269 · 커밋 없음)** — 게이트 G-1~G-6 사용자 확정(권고안 전건) · V-1~V-6 · S-1(+S-1b) · F-1~F-6 · U-1~U-6 · PL-1 구현 · V-7 보류 · F-7 조사 후 미착수(단위 선언 없음) · T3는 `plans/103` §4.2 이관. **잔여 = 사용자 폐쇄망 작업**(F-0 audit → prune · I군 확인 이력 삭제 · G-3 확인 · 표적 재측정 · 새 캠페인 — §6.2) → 파일명 `-WIP`
> 원 상태(v1): 계획(코드 0건) · 게이트 G-1~G-6 대기 — 트랙 V(판정 신뢰성 · 하네스) · S(스트림 스코프 · 제품) · F(폼필 유사어 사슬 · 제품) · U(단언 교정) · PL(정책 결정) · T3(`plans/103` 이관 · 구현 안 함) · 착수 순서 §6
> **대상 산출물**: `results/bench/run-closed-20260928-triage.tar/` — 이름은 `.tar`지만 **디렉터리**다.
> - 구간 run `20260923-140539` — `ladder-1` · 축 `LADDER_TIER` · 226턴 · 5.61시간 · `raw.jsonl` · `report.md` · 분석기 산출 7종 · `logs/server-*.log` 4개 · 산출 파일 48건
> - 캠페인 상태 `run-closed/campaign.json`(완료 1 · 미완 18) · `campaign_verdicts.md` · `proposals/` 4종 · `logs/segment-20260923-140032.log`
>
> **요청 원문(2026-09-28)**: *"run-closed-20260928-triage.tar 에 bench 를 실행한 로그가 있다. 이를 심도있게 분석하여 수정 계획을 수립하라."*
>
> **선행·관련**: `plans/114`(1구간 · M-0 단 축 설계) · `plans/118`(2구간 · D-266) · `plans/119`(같은 커밋의 시나리오 run 지연 분석 · D-267·D-268 · **v4 구현분 미커밋**) · `plans/110`(`98·CU-6`·`98·CU-9`·`98·J-1`·`108·CU-B3`) · `plans/103`(3단 동등성 P5) · `plans/104`(G-11 (b) DB별 LLM 매핑 등록) · `plans/116`
> **관련 결정**: D-146(월 시리즈) · D-147(미작성 사유) · D-148(서버명 = `name`) · D-151(폼필 역질문·확인 이력) · D-162(신규 동작 기본 off) · D-185(기간 불일치) · D-205(스레드 DB 스코프 4경로 대칭) · D-216(자동응답) · D-220(② 자유 서술 열 · ③ 옵트인 프로파일 이동 선례) · D-241(판정 분모) · D-250(단 축 첫 구간) · D-251(기준 경로 = 2단 · ⑦) · D-266(⑤ `run-closed` 종결) · D-267(① 3단 비교 기준선 분리) · D-268

---

## 0. 증거 규칙

- run 커밋 `072bd8ee`는 이 저장소에 없다(`git cat-file` 실패 · `dirty=true`). 산출물은 **관측**으로만 쓰고, 코드 앵커는 현 작업 트리(`multiintent` HEAD `48b0eae` + 미커밋)에서 다시 쟀다. 미커밋분에는 병행 세션의 `plans/119` v4 구현(D-268)이 들어 있다.
- 같은 커밋·같은 날의 시나리오 run `20260923-103638`을 `plans/119`가 지연 중심으로 이미 분석했다. **이 문서는 지연을 다시 분해하지 않는다.** 119와 겹치지 않는 것 — 판정 신뢰성 · 기능 결함 · 2단 대 3단 — 만 다룬다(§2.9는 차이점만).
- **과금 호출 0회**다. 로컬에서 한 것은 다음뿐이다.
  - 산출물 집계 · 서버 로그 grep · 코드 읽기
  - 벤치 판정 함수(`scripts.bench.sweep.read_observations` · `scripts.bench.compare.paired_accuracy`·`paired_binary`) 재실행 — 보정은 `raw.jsonl` **사본**(세션 scratchpad)에서만 했다
  - 월 시리즈 인식기 로컬 재현 — 저장소 양식 픽스처 3종 · LLM 0 · DB 0(§2.4)
- **측정하지 못한 것**
  - 폐쇄망 Redis 유사어 저장소의 실제 항목·출처 태그·등록 시점. 오염 항목의 **존재**는 로그의 "정확 매칭"으로 추론했다(`_synonym_match`는 정규화 문자열 완전 일치만 본다 · `src/document/field_mapper.py:815-848`). 등록 경로는 **추정**이다(§2.4).
  - 3단 `multi_db_executor`·`result_merger`의 노드 시간(16행 미수집 · §2.8).
  - LLM 호출 수·토큰(`plans/56` 소유).
  - 반복 1회라 지연 회귀는 판정하지 않는다.

---

## 1. 결론

> **사다리 판정(2단 `tier2_intent` 승)은 보정 뒤에도 유지된다(정확도 −30.4%p → −19.6%p · p=0.004). 그러나 벤치가 낸 수치와 실패 분류는 하네스 결함으로 오염됐고, 2단 기준선의 비-타임아웃 불합격 20턴 중 11턴은 제품 결함이 아니다. 제품 쪽 최대 결함은 하나의 사슬이다 — 양식의 월 사용률 열은 필드명에 CPU·메모리 명사가 없어 매핑 스킵 규칙을 빠져나가고, 폐쇄망 유사어 저장소의 오염 항목에 정확 매칭되어 월 시리즈 폼필(D-146)이 16회 중 16회 미발동했다. H군 기계 합격 0%와 `plans/110`의 미규명 잔여(`98·CU-6`·`98·CU-9`·`98·J-1`)의 원인이 이것이다.**

| # | 발견 | 근거 | 처방 |
|---|---|---|---|
| ① | **3단 arm의 `db_ids`가 전 행 비어 사다리 판정과 실패 분류를 오염했다** | 3단 111/111행(두 프로파일) `db_ids=[]`인데 `row_counts_by_db`는 88행에 있다. `db_ids` 불합격 12턴 **전부** 실제 조회 DB가 기대값과 같다. 분석기의 `routing` 12건도 전부 이것이다. 원인: 스트림 경로가 `done.db_scope`를 **마지막 노드의 델타**로 만든다(`src/api/routes/query.py:2337`·`:3084`). 2단 `result_aggregator` 델타에는 `active_db_id`·`target_databases`가 있고(`result_aggregator.py:514-515`) 3단 `output_generator` 델타에는 없다 | **S-1**(제품) · **V-1**(하네스) |
| ② | **완주율 신호가 기능 불합격을 미완주로 센다** | `scripts/bench/sweep.py:1033` — `verdict in ("error","fail")`이면 `completed=False`. 3단 완주율을 −14.6%p로 보고했지만 올바른 정의(응답 모드·타임아웃만)로는 **+6.8%p**(2:9 · p=0.065 · 비유의)다. **방향이 뒤집힌다** | **V-2** |
| ③ | **폼필 월 시리즈 16/16 미발동 — 유사어 오염 사슬** | §2.4. 로컬 재현: 오염 매핑이면 인식기 `None`, 매핑이 비면 정상 발동 | **F-1~F-6** |
| ④ | **I-01~I-06은 폼필 기억 오염이다**(114 진단 재확인) — **sliding TTL로 영구화** | `폼필 확인 이력 적용(D-151): … (사용 42회, TTL 7일 연장)` → `폼필 답변 오버라이드 적용: ['담당자']` → 역질문 후보가 `['비고']`뿐이다. 적용할 때마다 TTL이 늘어 벤치를 돌리는 한 만료되지 않는다(`src/schema_cache/form_memory.py:71-102`) | **V-4**(= `108·CU-B3` 우선순위 상향) |
| ⑤ | **단언·카탈로그 결함 5건** — 그중 하나는 **오답만 통과시킨다** | B-03·G-01 t1의 `hostname\s*=`는 D-148 이후 정답(`name` 필터)을 떨어뜨리고, 은행존 SQL이 `hostname =`으로 잘못 걸러 0행을 낸 턴만 통과시킨다. B-09(COALESCE 오탐) · C-11(CTE 오탐) · H-06(자동응답 계약 충돌) · F-03(플래그 의존) | **U-1~U-6**(G-2) |
| ⑥ | **3단 전용 결함 8종** — 조용한 오답 1건 포함 | 알람·프로세스 테이블 미선택 · **D-03: 서버 수 1,690을 "심각 알람 건수"로 답함** · 은행존 DB2 SQL에 `LIMIT` · 멀티턴 지시어 승계 유실 등(§2.7) | **T3-1~T3-8**(`plans/103` 이관) |
| ⑦ | **벤치 캠페인 잠재 결함** | `tier_decision.tier = null`(생략된 대조군 레벨의 단을 못 찾는다 — 판정이 「차이 없음」·「판정 불가」였다면 **캠페인이 차단됐다**) · `LADDER_TIER` "카탈로그에 없는 축" 오보 · 미측정 29축을 "검정력 부족"으로 표기 · 3단 멀티 DB 노드 시간 미수집 | **V-3·V-5·V-6** |
| ⑧ | **이 run의 위상** | D-251 ⑦ · D-267 ①이 "3단 비교용"으로 분리한 `--arm tier2_intent --arm tier3_router` 기준선의 **완료 기록**이다. D-266 주의 ①의 *"완료 기록이 저장소에 없다"*를 이 산출물이 해소한다. 다만 D-266 ⑤로 이 캠페인은 잇지 않는다 | **G-1** |

**2단 기준선 불합격 31턴의 귀속**(§2.3): 타임아웃 11(`plans/119` T 트랙 소관) · 폼필 기억 오염 6 · 단언·카탈로그 5 · 폼필 유사어 사슬 7 · 정책 결정 2.

---

## 2. 측정 사실

### 2.1 run 개요

| 항목 | 값 |
|---|---|
| 캠페인 | `run-closed`(생성 2026-09-23 14:03 · 모드 run · 환경 closed · 반복 1 · 예산 10시간) — 완료 1구간 · 미완 18구간. 1회 구동 = 1구간이 설계다(중단 아님) |
| 구간 | `ladder-1` · 축 `LADDER_TIER` · 5.61시간 · 226턴 · 시도 1회 |
| arm | `baseline` = 2단 `intent_orchestration` · `S2-LADDER_TIER-tier3_router` = 3단 `semantic_router` · `S2-LADDER_TIER-tier2_intent`는 실효 설정 지문이 기준선과 같아 **실행 생략**(기준선 관측으로 대체) |
| 프로파일 | `baseline` 106턴 · `baseline+tier3` 103턴 · `optin_alarm+baseline` 9턴 · `optin_alarm+tier3` 8턴 — 4개 모두 기동 유효 · 설정 에코 일치 |
| 프로바이더 | 워커 fabrix · 오케스트레이터 vllm(구간 로그 표기) |
| 상한 | `server.query_timeout` 180 · `file_query_timeout` **600**(`config_snapshot.json`) — D-266 ① 주입값(180/180)과 파일 상한이 다르다 |
| 판정 | 기능 합격률(D-241 분모) **30.5%**(64/210) · 타임아웃 15턴 · 역질문 차단 1턴 · 자동응답 118턴(52%) |
| 지연 | 완주 턴 wall p50 — 2단 66.5초 · 3단 57.3초 |

### 2.2 사다리 판정 재계산

보정 방법: `raw.jsonl` 사본에서 **기대 `db_ids` = 그 턴의 `row_counts_by_db` 키**인 `db_ids` 단언만 지우고, 벤치 판정 함수를 그대로 다시 돌렸다. 완주 정의 교정(V-2)은 `completed = 응답 모드가 error·crash·hang이 아니고 타임아웃 미평가가 아님`으로 바꿔 계산했다.

| 신호(쌍 수) | 벤치 보고 | `db_ids` 보정 | + 완주 정의 교정 |
|---|---|---|---|
| 정확도(46쌍) | **−30.4%p** · 불일치 14:0 · p=0.0001 | **−19.6%p** · 9:0 · p=0.004 | 같음 |
| 완주율(103쌍) | −14.6%p · 23:8 | −3.9%p · 14:10 | **+6.8%p** · 2:9 · p=0.065 |
| SQL 생성률(103쌍) | −2.9%p · 6:3 | 같음 | 같음 |
| 지연 | arm 평균 −15.4초 | — | 둘 다 완주 94쌍: 2단 − 3단 p50 **+5.0초** · 평균 +6.5초 |

- **판정 결론은 유지된다.** 남은 9쌍은 3단의 실결함이다(§2.7 T3-1·T3-3·T3-4·T3-6 · D-02·D-03 등).
- 3단은 **더 빠르고 완주도 약간 더 하지만(비유의) 정확도가 낮다.** 2단 추가 비용의 대부분은 `replanner`(평균 3.8초)와 `intent_planner`(1.7초)다 — `plans/119` N-2가 겨냥한 부분이다.
- `campaign_verdicts.md`·`campaign.json` `tier_decision.sentence`에 남은 −30.4%p는 V-1 뒤 재생성해 부기한다.

### 2.3 2단 기준선 불합격 31턴 귀속

| 귀속 | 턴 | 시나리오 | 비고 |
|---|---:|---|---|
| 타임아웃(180초) | 11 | A-11 · C-02 · E-02 · E-07 · F-07 · G-03 · J-05 · J-06 · K-08 · D-06 · D-08 t2 | 서술 단계 6 · 재계획/SQL 루프(SQL 0건) 3 · 오케스트레이터 안 2 — `plans/119` §2.9와 같은 분포 → **119 T 트랙 소관**. J-06은 DB2 `REGEXP_LIKE` 전수 스캔이 MCP 60초 상한에 걸렸다(그룹 125.9초) |
| 폼필 기억 오염(하네스) | 6 | I-01 ~ I-06 | §2.5 |
| 단언·카탈로그 | 5 | B-03 · B-09 · C-11 · H-06 · F-03 | §2.6 |
| **폼필 유사어 사슬(제품)** | 7 | H-03 · H-10 · H-11 · H-12 · H-13 · H-15 · I-07 t2 | §2.4. 수동 판정에 가려진 H-14 · H-17도 같은 원인이다(SQL에 지표 테이블 0 · 월 12열 공란) |
| 정책 결정 필요 | 2 | C-06(진행 중 월의 통계 테이블) · G-01 t3(생략형 후속 승계) | §2.6 |

비-타임아웃 20턴 중 **11턴(55%)이 하네스·단언**, 7턴이 한 사슬, 2턴이 정책이다.

### 2.4 폼필 월 시리즈 무력화 — 유사어 오염 사슬 (③)

H-10(`CPU_양식.xlsx` · "2026년 6월 기준 CPU 양식 채워줘") 서버 로그 `15:49:35~15:51:08`을 따라간 사슬이다.

```
양식 헤더 '월중평균사용률(최근 6개월간)|M' … '|M+5' (12열)
  · 명사(CPU·주기억장치)는 양식 제목과 질의에만 있고 필드명에는 없다
① field_mapper 사용률 스킵 규칙 미발동                                            [F-1]
     _is_metric_usage_field는 필드명 안의 명사를 요구한다(field_mapper.py:41-56·:335-346)
② Pass 1(핵심 테이블) 불일치 → Pass 2(서브 테이블) 정확 매칭                       [F-2]
     '월중평균사용률(최근 6개월간)|M' -> MON_HW_20260806.MEM_RATIO (db=polestar_b0)
     (복합 필드명이 그대로 유사어로 들어 있다 — 과거 양식 LLM 매핑의 자동 등록으로 추정 · F-4)
③ 매핑 우선 DB는 질의 텍스트 힌트뿐 — 존 선택(polestar_cm_gp)을 모른다               [F-3]
     field_mapper 완료: 17/17 매핑, DB=['polestar_b0','polestar_cm_yd'] ↔ 실행 DB = gp
④ recognize_month_series: 매핑된 비지표 컬럼은 건너뜀(assembler.py:260) → None       [F-1b]
     로그 사유는 "집계어/서브(M+k·절대월) 패턴 불충족"(assembler.py:286-295) — 오보
⑤ 결정적 피벗: 스키마에 없는 매핑 칼럼 12건 제외(환각 매핑 차단 · query_generator.py:404)
⑥ 폼필 역질문(D-151) 12필드 → 하네스 자동응답 "전 필드 공란"(D-216)
⑦ 월 12열 공란 · [기준월 안내]·[기간 불일치] 미발화(anchor 없음)                    [F-6]
     [확인 필요]도 미발화(월 필드 집합이 비어 있어서 · output_generator.py:1184-1221)
⑧ 서술: "2026년 6월 기준으로 총 1,690건의 CPU(처리능력) 데이터가 조회되었습니다"
     — 사용률 값이 하나도 없는데 기간 데이터라고 말한다(조용한 오답 · 기계 판정 밖)
```

**로컬 재현**(저장소 픽스처 · LLM 0 · DB 0 · 기준일 2026-09-23)

| 양식 · 질의 | 월 필드 | `_is_metric_usage_field` 적중 | 오염 매핑일 때 | 매핑이 비었을 때 |
|---|---:|---:|---|---|
| `CPU_양식.xlsx` · "채워줘 (기간 없음)" | 12 | **0** | `None` | `server.Cpus` · 202603~202608 · default |
| `CPU_양식.xlsx` · "1월부터 3월까지 채워줘" | 12 | **0** | `None` | `server.Cpus` · 202510~202603 · query |
| `메모리_양식.xlsx` · "2026년 6월 기준 메모리 양식 채워줘" | 12 | **0** | `None` | `server.Memory` · 202601~202606 · query |

→ **①이 막히면 ②~⑧이 전부 사라진다.** 월 필드가 매핑 단계에서 `None`으로 남으면 오염 사전이 있어도 인식기가 발동한다.

**이 run의 서브 테이블 정확 매칭 전량**(2단 로그 · 필드 → 컬럼 · 횟수) — 전부 결정적 피벗이 나중에 버리는 매핑이다.

| 필드 | 매핑된 컬럼(DB) | 회 | 올바른 대상 |
|---|---|---:|---|
| `월중평균사용률(최근 6개월간)\|M*` | `MON_HW_20260806.MEM_RATIO`(b0) | 96 | 월 시리즈(`cmm_metric_stat_m`) |
| `월중 Peak시 사용률(최근 6개월간)\|M*` | `MON_HW_20260806.MEM_RATIO`(b0) | 96 | 월 시리즈 |
| `비고` | `polestar.ip_info.description`(gp) · `IPAM_INFO.DESCRIPTION`(b0) | 14 | 자유 서술(공란 허용 · D-220 ②) |
| `담당자` | `polestar.rep_inst_send.manual_input_user`(gp) | 6 | 수집 항목 없음(역질문 대상) |
| `리소스유형` | `polestar.cmm_custom_mon_m_src.custom_monitor_resource_type`(yd) | 2 | `cmm_resource.resource_type` — **H-03 759행 공란의 원인** |
| `도입일자` | `polestar.sap_profile.create_date`(yd) | 2 | 수집 항목 없음 |
| `용도` | `SERVER_DEFAULT_PORT_PERMISSION.PORT_DESC`(b0) | 2 | 수집 항목 없음 |
| `구분\|분류` | `CMM_ALARM_DEF_C_CON.DTYPE`(b0) | 2 | — |
| `구분` | `polestar.cmm_resource_type.category`(gp) | 2 | — |

- 이 유사어들은 저장소 시드(`config/synonym_seeds/*.yaml`)에 **없다**(grep 0건). 폐쇄망 Redis에만 있다.
- 이 run 안에서는 새 등록이 없었다(`Redis 즉시 등록` 등 0건 · 매핑 요약 전부 `LLM=0`). **과거 run이 남긴 항목**이다. 등록 경로 후보는 양식 LLM 매핑의 즉시 등록 — 구조 정보가 없다고 판정된 DB는 DB별 캐시에 `source=llm`으로 쓴다(`document/field_mapper.py:1113-1146` · `plans/104` G-11 (b)) — 이고, 시드 `load`는 합집합 병합이라 지우지 않는다. **확인은 F-0(폐쇄망 · 읽기 전용)으로 한다.**
- `plans/114` §2.7은 H-10을 *"LLM이 `MON_HW_20260806.MEM_RATIO`에 매핑했다"*고 적었다. 이 run 로그로는 **LLM이 아니라 유사어 정확 매칭**이다. 과거 LLM 매핑이 등록돼 굳은 것으로 보인다(Known Mistakes "LLM 자동 등록은 오염 자기강화 루프").
- **덧붙여 본 것**(같은 H-10 턴): `처리능력|(TPMC)` → `EAV:TotalSize`(메모리 총량 GB)로 매핑돼 CPU 양식의 TPMC 칸에 메모리 용량이 들어갔다. 메모리 양식(H-15)의 `처리능력|(GB)`에는 맞는 매핑이다. 단위 서브 헤더가 둘을 가른다(F-7).

### 2.5 폼필 기억 오염 (④)

- I-01 로그(`16:15:27`): `폼필 확인 이력 적용(D-151): '서버명, 호스트명, IP 외 4개 양식' 1개 항목 (사용 42회, TTL 7일 연장)` → `폼필 답변 오버라이드 적용(D-151): ['담당자']` → 역질문 `미해결 1건 — ['비고']`.
- 원인은 `plans/114` §2.7 진단과 같다 — 과거 run의 I-02 턴 2가 `form_fill_remember: true`로 `담당자`를 저장했다.
- **새 사실**: 확인 이력은 적용할 때마다 TTL을 연장한다(`form_memory.py:76` "touch=True면 sliding TTL 연장"). 벤치가 7일 안에 한 번이라도 돌면 만료되지 않는다. 42회 사용이 그 증거다.
- 격리 수단(`108·CU-B3` `forget_form_memory` teardown)은 아직 없다 — `scripts/scenario/runner.py:670` `SUPPORTED_TEARDOWN = {"drop_thread", "unregister_synonym"}`.

### 2.6 단언·카탈로그 결함과 정책 결정 항목 (⑤)

| 시나리오 | 단언 | 관측 | 판단 |
|---|---|---|---|
| **B-03 · G-01 t1** | `sql_must_match (?i)hostname\s*=` | 김포·여의도 SQL은 `name = 'cocm-hdkapp01'`(1행). `cocm-*`는 장비명이다(F-03 응답: 서버명 `cob0-bnndbs01` ↔ 호스트명 `mcsdbs01`) | **단언이 오답을 정답으로 굳혔다.** G-01 t1은 은행존 SQL `r.hostname = 'cocm-hdkapp01'`(0행)이 걸려 통과했고, 3단 B-03도 은행존 `svr.hostname =` 덕에 통과했다. D-148 · CU-17(장비명 few-shot 교정) 이후 정답은 `name` 필터다 |
| **B-09** | `sql_must_not_match (?i)stringvalue_short` | `COALESCE(cc.stringvalue, cc.stringvalue_short)` — LOB 우선 · 1행 | 과엄격. 이 run의 `silent_wrong` 6건 중 **유일한 2단 건이 거짓 양성**이다 |
| **C-11** | `(?i)select[\s\S]*\(\s*select` | `WITH overall_cpu AS ( /* 주석 */ SELECT …)` — 스칼라 서브쿼리와 같은 결과(475행) | 구문 형태 단언(114 G-02와 같은 종류) |
| **H-06** | `response_must_contain [미작성 항목]` | 제품이 D-151 역질문 → 하네스 "공란" → `[사용자 답변 적용 내역] TPMC: 공란 유지 적용`. 사용자 지정 공란은 미작성 목록에서 뺀다(`output_generator.py:1243-1264`) | **계약 충돌**(D-151 × D-216). 제품 동작은 설계대로다 |
| **F-03** | 턴 1 `status: clarification`(존 그룹 상호배타) | 이 run `ZONE_GROUP_EXCLUSIVE=false` → 두 존을 조회해 답했다 | **플래그 의존 단언이 기본 프로파일에 있다.** 기본값에서는 반드시 떨어진다 |
| D-03(3단) | `period_covers` | 3단이 `SELECT COUNT(*) AS server_count FROM cmm_resource`로 "심각 알람 1,690건" | 단언은 떨어뜨렸지만 **조용한 오답으로 분류되지 않았다** — 알람 테이블 단언을 추가한다(U-6) |
| C-06 | `cmm_metric_stat_m` · `'20\d{4}'` | "이번 달 CPU 사용률"을 일간 통계로 집계(1,688행) | **정책**: 진행 중 월에 월 통계 행이 있는지는 폐쇄망 데이터 사실이다(G-3) |
| G-01 t3 | `cocm-hdkapp01` | "네 그럼 메모리도 보여줘" — `prev_entities=1`인데 지시어가 없어 승계 안 함 → 전체 4,782행 | **정책**: 현 규칙은 지시어("해당·그 서버")만 승계한다(G-4) |

### 2.7 3단 전용 결함 (⑥ · `plans/103` 이관)

3단은 비교 arm이고 기능 동등성은 `plans/103` P5 진행 중이다. 이 문서는 구현하지 않고 증거만 넘긴다.

| ID | 시나리오 | 관측 | 2단 |
|---|---|---|---|
| T3-1 | A-04 · D-01 · D-02 · **D-03** | 스키마 분석 허용 테이블이 `cmm_resource`·`core_config_prop`·`cmm_metric_stat_*`뿐이라 알람 테이블이 없다 → `error_response`("알람 테이블이 없다"). **D-03은 서버 수를 알람 건수로 답했다(조용한 오답)** | 결정적 교정(`알람 조회 결정적 교정` 10회)으로 정상 |
| T3-2 | E-05 | "프로세스 이력" 테이블 미선택 → `error_response` | 정상(1,689행) |
| T3-3 | A-02 · B-10 · H-04 · H-17 | 은행존(DB2) SQL에 `LIMIT 10000` — 폼필 결정적 피벗 포함. DB2가 실행은 했다(행 수 같음) | 같은 자리에서 `FETCH FIRST` — 3단 은행존 경로에 엔진 정보가 안 간다(단일/멀티 대칭 · Known Mistakes) |
| T3-4 | A-12 | 양식 미첨부 요청이 `general_inference` 자유 응답 | 결정적 안내(D-264) |
| T3-5 | E-01 | "캐시 갱신 + 서버 목록" → `cache_management` 단독(목록 누락) | 두 task |
| T3-6 | G-02 t2 · G-04 t2 | "해당 서버 …" 지시어 승계 유실 → 4,787행 전체 | 승계 |
| T3-7 | I-07 t2 | 폼필 답변 턴이 `general_inference` | 폼필 경로 |
| T3-8 | B-09 | 장비명 질의를 은행존 단독으로 라우팅 → 0행 | 3-DB 팬아웃 1행 |

### 2.8 하네스·리포트 결함 (①②⑦)

| 결함 | 영향 | 위치 |
|---|---|---|
| `db_ids`를 `done.db_scope`에서만 읽는다 — 스트림 `db_scope`가 마지막 노드 델타 기준(①) | 3단 `db_ids` 불합격 12턴 허위 · 분석기 `routing` 12건 허위 · 사다리 정확도 10.8%p 과장 | `scripts/scenario/client.py:159-161` · `src/api/routes/query.py:2337`·`:3084` |
| 완주 신호에 기능 불합격이 섞인다(②) | 완주율 방향 역전(−14.6 → +6.8%p) | `scripts/bench/sweep.py:1033` |
| **생략된 대조군 레벨의 단을 못 찾는다** | 이번엔 `tier: null`(표기 "단 `?`"·"단 `None`")로 끝났지만, 판정이 「레벨 간 차이 없음」·「판정 불가」면 기준 경로 레벨을 `measured`에서 못 찾아 **`blocked`** 가 된다 — 실행 생략 arm은 `run.json` 프로파일에 없어 `tier_by_arm()`에 없다 | `scripts/bench/__main__.py:738-746`·`:755-766`·`:772-775` · `sweep.py:1184-1193` |
| `LADDER_TIER`를 "카탈로그에 없는 축(키 삭제·개명 추정)"으로 보고 | 합성 축에 대한 오보(구간 로그 2회) | `scripts/bench/__main__.py:263-268` |
| 미측정 29축을 "검정력 부족 — 정적 규칙으로 폴백"으로 처분 | "재 봤는데 약하다"로 읽힌다 — 실제는 구간 미완 | `scripts/bench/optimize.py:127` |
| 3단 `multi_db_executor`·`result_merger` 노드 시간 미수집 | 16행(B-03: wall 127.8초 · 노드 합 32.7초) — 리포트 §7 노드 분해에서 3단 멀티 DB 비용이 통째로 빠진다 | 서버 SSE `node_complete` 누락인지 러너 집계인지 미확정 |
| 2단 알람 "해석 제외" 고지 | 이 run은 2단 로그에 교정 증거 10회 — 고지가 틀렸다 | **`plans/119` H-2로 해소(작업 트리 · 미커밋)** — 재생성만 |
| 리포트 헤더·서버 WARNING의 "기준 경로 3단(D-225)" | — | **작업 트리에서 D-251로 교정됨**(118 B-5 · `ladder.py:9`·`report.py:1280`) — 확인만 |

### 2.9 지연 — 119와 다른 점만

- 2단 대 3단(둘 다 완주 94쌍): 2단이 p50 **+5.0초** 느리다. 노드 평균(초) — 2단 `agent_orchestrator` 24.7 · `result_aggregator` 30.6 · `replanner` 3.8 · `intent_planner` 1.7 / 3단 `schema_analyzer` 5.1 · `query_generator` 7.8 · `output_generator` 27.9 · `semantic_router` 2.5(+ 멀티 DB 미수집).
- 타임아웃 15턴: 서술 단계 10(3단 4 포함) · 루프 3 · 오케스트레이터 2 — 119 §2.9와 같은 구조다.
- 폼필 역질문까지 66초(H-10): 엑셀 생성 뒤 역질문 발행까지 33초 — 119 N-1 ④(구현됨)의 대상이다.
- 관측만(처방 없음): ⓐ위치 없는 엔티티 질의는 은행존을 먼저 돈다(D-206) — G-01 t1 은행존 그룹 67초(SQL 0.13초). ⓑ G-01 t3의 `'메모'` 부분 일치 보완 매칭 18건이 은행존 허용 테이블을 429→19로 부풀렸다.

### 2.10 측정 갭

§0 「측정하지 못한 것」과 같다. 추가로 `llm_calls`·`tokens`는 전 행 null이다(`plans/56`).

---

## 3. 기존 계획 잔여와의 대응

| 잔여(소유) | 이 run이 준 근거 | 처분 |
|---|---|---|
| **`98·J-1`**(110) H-10 월 피벗 진입/폴백 원인 | **원인 확정**: 매핑 스킵 규칙이 필드명 명사만 봄 + 오염 유사어 매칭 → 인식기가 필드를 건너뜀. 로컬 재현 | F-1 · 110 행 갱신 대상 |
| **`98·CU-6`**(110) 월 피벗이 `cmm_metric_stat_m` 안 씀 | J-1과 같은 원인 | F-1 → F-6 |
| **`98·CU-9`**(110) 안내 3종 미발화(원인 미규명) | `[기준월 안내]`·`[기간 불일치]` = anchor 부재(F-1 하류) · `[미작성 항목]` = 자동응답 공란이 대체(D-151 × D-216) | F-1 + U-4로 **원인 규명 완료** |
| **`108·CU-B3`**(110) 폼필 기억 격리 | I-01~I-06 · sliding TTL 영구화 | V-4 — 재측정 전 필수로 상향 |
| `plans/114` §2.7 H-10 "LLM이 매핑" | 이 run 로그상 LLM 0 · 유사어 정확 매칭 | 114 기술은 과거 등록분의 결과로 읽는다(이 문서 §2.4) |
| `plans/114` §4.2 "날짜 접미사 테이블 제외" 처방 후보 | 오염 10종 중 날짜 접미사 테이블은 1개(2종)뿐이다 | F-2(구조 선언 한정)·F-4(등록 차단)에 흡수 |
| `plans/119` Q-4 Layer 1 완전 일치 | I-01 Layer 2가 `'서버명' → '호스트명'`으로 풀어 서버명 칸에 호스트명이 들어갔다(119가 본 10건과 같은 현상) | **구현됨(미커밋 · `src/utils/column_matcher.py` `match_field_name_key`)** — 재측정에서 확인만. 부수 효과로 `서버명` 열이 등록명(`c.name`)으로 풀려 **H·I군 셀 값이 이 run과 달라질 수 있다**(119 세션 통지 2026-09-28) |
| `plans/119` H-2 알람 고지 증거 기반 | 이 run 2단 증거 10회 | **구현됨** — 리포트 재생성으로 해소 |
| `plans/119` T 트랙 · N-1 | 타임아웃 15턴 · 폼필 서술 33초 | 119 소관 |
| `plans/103` P5 | T3-1 ~ T3-8 | 103 잔여 장부에 추가 |
| `plans/104` G-11 (b) DB별 LLM 매핑 등록 | 오염 등록 경로 후보 | F-4가 쓰기 가드를 더한다(G-11 (b)의 격리 의도는 유지) |
| D-266 ⑤ `run-closed` 종결 | `ladder-1` 구간 자체는 유효 | G-1 |

---

## 4. 수정 단위

### 4.1 트랙 V — 판정 신뢰성 (하네스 · 무과금 · 제품 동작 불변)

| ID | 내용 | 위치 | 검증 |
|---|---|---|---|
| **V-1 ★** | **`db_ids` 관측 폴백** — `done.db_scope.db_ids`가 비면 `executed_sqls[].source`(실행 DB 집합)로 판정하고 행에 `db_ids_source`(`scope`·`executed`)를 남긴다. S-1 뒤에도 옛 run 재판정용으로 둔다. 이 run을 재판정해 보정 수치(§2.2)를 캠페인 기록에 부기한다 | `scripts/scenario/client.py:159` · `runner.py:638` · `assertions.py` | 이 run 재판정 → 3단 `db_ids` 불합격 12 → 0(A-02는 LIMIT 단언으로 불합격 유지) · 정확도 −19.6%p 재현 |
| **V-2 ★** | **완주 신호 정의 교정** — 완주 = 응답 모드가 `error`·`crash`·`hang`이 아니고 타임아웃 미평가가 아님. 기능 불합격은 완주다 | `scripts/bench/sweep.py:1033` | 이 run 3단 완주율 +6.8%p 재현 · 완주율을 단언하는 테스트 repo 전체 grep 일괄 갱신 |
| **V-3 ★** | **생략 대조군 레벨의 단 = 기준선의 관측 단** — `tier_by_arm()`이 `substituted` arm에 기준선 단을 싣는다. 세 분기(최적 레벨 · 차이 없음 · 판정 불가) 모두 | `scripts/bench/__main__.py:738-775` · `sweep.py:1184` | 대조군 생략 + 「차이 없음」·「판정 불가」 모의 판정에서 `blocked` 없이 `tier = intent_orchestration` |
| **V-4 ★** | **폼필 기억 격리** = `108·CU-B3`(`forget_form_memory` teardown · 미지원 teardown 오염 턴 `invalid`) + **run 시작 시 벤치 계정의 확인 이력 전체 삭제** 옵션(sliding TTL 대응 · 사용자 스코프 한정 확인 선행) | `runner.py:670` · `src/schema_cache/form_memory.py` API 재사용 | 사전 적재한 이력이 I-01 역질문 후보에서 `담당자`를 빼지 않는다 |
| V-5 | `LADDER_TIER` 등 합성 축을 "카탈로그에 없는 축" 검사에서 제외 · 미측정 축 처분 문구를 "미측정 — 구간 미완"으로 | `__main__.py:263` · `optimize.py:127` | 구간 로그·`knob_disposition.md` 재생성 |
| V-6 | 3단 `multi_db_executor`·`result_merger` 노드 시간 수집 — 서버 SSE 누락인지 러너 집계인지 먼저 확정 | `query.py` 스트림 · `client.py` | 3단 멀티 DB 모의 턴에서 노드 합 ≈ wall |
| V-7 | 조용한 오답 분류 — 거짓 양성(B-09)은 U-2로, 미탐(3단 D-03)은 U-6 단언으로 처리한다. 분류 규칙 신설은 **보류**(관측 1회) | — | — |

### 4.2 트랙 S — 스트림 스코프 (제품)

**S-1 ★ 스트림 `done.db_scope`를 전체 상태로 만든다.**

- 현재 스트림 경로는 `on_chain_end` 이벤트의 `output`(최종 응답을 낸 **노드의 델타**)으로 `build_db_scope`를 부른다(`query.py:2337`·`:3084`). 비스트림 경로는 `ainvoke` 결과(전체 상태)를 쓴다(`:2022`·`:2418`·`:2656`·`:3158`) — **D-205 "4경로 대칭"의 실제 결함**이다.
- 사용자 영향: 3단 스트림에서 스코프 칩(D-205)과 다음 턴 승계 보고가 비어 나간다. 2단도 `result_aggregator`가 승격하지 않는 경로(`result_aggregator.py:500` "승격할 db_id가 없으면 빈 dict")에서 비는지 확인한다.
- 안: 스트림 루프가 노드 델타를 누적하거나, 종료 시 체크포인터 상태(`aget_state`)로 `build_db_scope`를 부른다. `_store_result`에 싣는 값도 같게 한다.
- 검증: 2단·3단 모의 그래프 스트림에서 `done.db_scope.db_ids` = 실행 DB. 비스트림과 같은 값.
- 매뉴얼(D-255): 기능 설명은 그대로이고 3단에서도 칩이 나타나게 되는 교정이다 — 사용자 매뉴얼 갱신 대상인지 확인만 한다.

### 4.3 트랙 F — 폼필 유사어 사슬 (제품)

| ID | 내용 | 위치 | 검증 |
|---|---|---|---|
| **F-1 ★** | **월 구조 필드는 매핑하지 않는다.** 사용률 스킵 판정에 **구조 판정**을 더한다 — 그룹(`|` 앞)에 '사용률'과 집계어가 있고 서브가 M+k·절대월이면, 필드명에 명사가 없어도 스킵한다(리소스 명사는 인식기처럼 양식 제목·질의에서 찾는다). 규칙은 한 곳에 둔다: `assembler.py`의 서브 헤더 파서(`_REL_MONTH_RE`·`_ABS_*`·`_parse_month_sub`)와 집계어 목록을 **두 소비처의 공통 하위 계층 `src/utils/`** 로 옮기고, 값 컬럼 대응(`avg_val`·`max_val`)은 assembler에 남긴다(overfit_check · D-089). 효과: 오염 사전과 무관하게 월 필드가 `None`으로 남아 인식기가 발동한다(§2.4 재현) | `src/document/field_mapper.py:41-56`·`:335-346` · `src/db_adapters/polestar/assembler.py:154-216` | 픽스처 3종 × 오염 항목을 주입한 모의 유사어 사전으로 field_mapper → `recognize_month_series` 발동 · 필드명에 명사가 있는 기존 스킵은 비트 동일 · `arch_check`·`overfit_check` |
| F-1b | **인식기 견고화 · 로그 사유 분리** — 실행 DB의 허용 테이블 밖 컬럼으로 매핑된 월 구조 필드는 `None`과 같게 본다. 미발동 로그를 "비지표 컬럼 매핑 N건"과 "패턴 불충족"으로 나눈다(침묵·오보 금지) | `assembler.py:258-295` · 호출부 `query_generator.py` · `multi_db_executor.py` | 오염 매핑 입력에서 발동 · 로그 사유 단언 |
| **F-2 ★** | **Pass 2(서브 테이블) 매칭을 구조 선언 안으로 한정** — EAV 피벗 스키마(`_schema_uses_eav_metric_pivot`)인 DB에서는 Pass 2 후보를 그 DB 구조 선언(`db_profiles`)의 테이블로 제한하고, 밖이면 매칭 실패로 보아 다음 단계(EAV 유사어 · LLM · 역질문)로 넘긴다. 결정적 피벗이 어차피 버릴 매핑(`query_generator.py:404`)을 앞에서 만들지 않는 것이다. EAV 선언이 없는 DB(itam 등)는 종전 동작 | `document/field_mapper.py:638-655` | §2.4 표의 서브 테이블 매칭 10종이 전부 거부 · 핵심·EAV 매칭 비트 동일 |
| F-3 | **매핑 우선 DB = 이번 턴 대상 DB** — `selected_db_ids`(존 선택)·폼필 고정 DB를 질의 텍스트 힌트보다 앞에 둔다. 2·3단 공통 전단이라 대칭이다 | `src/nodes/field_mapper.py:139-142` · `_load_db_cache_data` | `selected_db_ids=[gp]`에서 `mapped_db_ids = [gp]` |
| **F-4 ★** | **등록 지점 결정적 차단**(Known Mistakes — 쓰기 지점) — 양식 LLM 매핑 즉시 등록·DB별 등록이 ⓐ월 구조 필드명(F-1 판정) ⓑEAV 피벗 DB의 구조 선언 밖 테이블 ⓒ날짜 접미사 스냅샷 테이블(`*_YYYYMMDD`)을 등록하지 않는다. `plans/104` G-11 (b)의 격리 의도(전역 오염 방지)는 유지한다 | `document/field_mapper.py:1113-1146`·`:1458-1572`·`:2146` | 모의 캐시 매니저로 세 유형 등록 0건 · 정상 매핑 등록 비트 동일 |
| F-5 | **오염 진단·정리 도구** — `scripts/synonym_seeds.py`에 `audit`(읽기 전용: Redis 유사어 중 F-4 규칙 위반 항목을 DB·컬럼·단어·출처 태그로 출력)와 `prune --apply`(백업 JSON 선행 · 위반 항목만 제거 · 운영자 등록 `source=operator` 불가침)를 더한다 | `scripts/synonym_seeds.py` | fakeredis 또는 모의로 audit 목록 · prune 전후 비교 · 백업 복원 |
| F-0 | **(사용자 폐쇄망 · 읽기 전용)** `audit` 실행 — 오염 규모·출처 태그를 확인하고 G-5를 정한다 | — | 출력 첨부 |
| F-6 | **월 구조 필드 미채움 고지** — anchor가 없어도 F-1 구조 판정으로 월 필드 집합을 계산해 ⓐD-151 역질문 후보에서 빼고 ⓑ전부 0건이면 `[확인 필요]`를 낸다. 119 N-1(미커밋) 이후 **폼필 역질문 턴은 요약 LLM 0회 · 결정적 안내만**이므로, 서술 가드는 역질문 없이 끝나는 폼필 턴의 요약 입력에만 건다 — **채운 열 목록**을 넘겨 "기간 데이터 N건"류 서술을 막는다 | `output_generator.py:1171-1276` · `_build_form_fill_hitl` | 인식기 실패(명사 없음) 모의에서 역질문 0 · `[확인 필요]` 1 |
| F-7 | (후순위 · 조사 먼저) **단위 서브 헤더 불일치 거부** — `처리능력|(TPMC)` → `EAV:TotalSize`(GB). 속성 단위 선언이 `db_profiles`에 있는지 먼저 확인하고, 없으면 착수하지 않는다 | `document/field_mapper.py` EAV 매칭 | H-10 TPMC 칸 공란 |

- **I-07 t2**("구조 채움 필드는 답변으로 변경할 수 없습니다")는 F-1 하류다 — 월 필드가 구조 필드로 인식돼야 발화한다. 답변 키가 그룹명(`월중평균사용률(최근 6개월간)`)이고 필드는 `…|M`~`…|M+5`인 점도 F-1 검증에서 함께 본다.
- 매뉴얼(D-255): F-6은 양식 응답의 고지 문구가 늘어나는 변화다. 사용자 매뉴얼에 폼필 고지 설명이 없다(grep 0건) — 추가 여부만 판단한다.

### 4.4 트랙 U — 단언·카탈로그 교정 (G-2)

| ID | 대상 | 교정 |
|---|---|---|
| U-1 | B-03 · G-01 t1 | `hostname\s*=` → 장비명(`name`) 필터 단언 + `row_count_total: {eq: 1}` |
| U-2 | B-09 | `stringvalue_short` 금지 → "값을 LOB(`stringvalue`) 우선으로 읽는가"로(COALESCE 허용) |
| U-3 | C-11 | 스칼라 서브쿼리 **또는** CTE 허용 |
| U-4 | H-06 | `[미작성 항목]` **또는** 해당 3열의 `[사용자 답변 적용 내역] … 공란 유지` — 러너에 `response_must_contain_any` 단언 종류 추가(소규모) |
| U-5 | F-03 | `ZONE_GROUP_EXCLUSIVE=true` 옵트인 프로파일로 이동(D-220 ③ 선례 · 서버 기동 1회 추가). 기본 프로파일에서는 두 존 조회가 정답이다 |
| U-6 | D-03 | `sql_must_match (?i)\bcmm_alarm` 추가 — 단과 무관하게 "서버 수를 알람 건수로" 답하는 오답을 잡는다 |
| U-7 | C-06 | G-3 결과에 따른다(그 전까지 `manual_review` 로 둔다) |

- 결정적 상수·단언 변경 시 그 값을 단언하는 테스트를 repo 전체 grep으로 함께 고친다(Known Mistakes).

### 4.5 트랙 PL — 정책 결정이 필요한 제품 항목

| ID | 내용 | 게이트 |
|---|---|---|
| PL-1 | **생략형 후속 턴 승계**(G-01 t3) — 직전 턴이 단일 엔티티 스코프 · 이번 턴에 새 식별자·위치어·"전체"가 없음 · 첨가 표지("~도"·"그럼")일 때 직전 엔티티를 승계하고 응답에 "직전 서버 X 기준 — 전체는 '전체 서버'로 다시 요청" 고지를 싣는다(침묵 승계 금지). 멀티턴 매뉴얼(`scripts/manual/content/user.md:889-899`) 갱신 동반 | G-4 |
| PL-2 | **진행 중 월 통계 테이블 선택**(C-06) — 코드가 아니라 `db_profiles`/`knowledge` 규칙·예시 대상이다 | G-3 |

### 4.6 트랙 T3 — `plans/103` 이관 (이 문서에서 구현하지 않음)

§2.7 T3-1~T3-8을 103 P5 잔여 장부에 증거와 함께 올린다. 특히 **T3-1 D-03(조용한 오답)** 과 **T3-3(3단 은행존 경로에 엔진 미전달)** 은 3단을 비교 arm으로 쓰는 동안에도 판정을 흔든다.

---

## 5. 사용자 확정 게이트

| ID | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** | 새 캠페인(D-266 ⑤)의 사다리 단 구간을 어떻게 할까 | (a) `ladder-1` 재측정(약 5.6시간) · (b) 이 run의 **보정 판정을 인용**하고 단 축 구간을 생략(하네스에 "결정 고정 + 근거 run 인용" 경로 필요) · (c) 인용 없이 D-251 결정만으로 고정 | **(b)** — 보정 뒤에도 −19.6%p · p=0.004로 결론이 같다. 남은 9쌍은 3단 실결함이라 103 P5가 옮기기 전에는 재측정해도 바뀌지 않는다. 인용 기록에 이 run의 상한(파일 600초)이 새 주입값(180/180)과 다르다는 사실을 남긴다. 103 P5 완료 시 재측정한다 |
| **G-2** | 단언 교정 U-1~U-6을 채택할까 | 전건 · 일부 · 보류 | **전건** — U-1은 오답만 통과시키는 단언이라 가장 급하다 |
| **G-3** | "이번 달" 사용률의 기준 테이블(C-06) | (a) 폐쇄망 읽기 전용 확인(월 통계에 진행 중 월 행이 있는가) 뒤 결정 · (b) 월 통계 고정 · (c) 일간 허용 | **(a)** — 데이터 사실에 따라 knowledge 규칙이나 단언 중 하나를 고친다. 그 전까지 C-06은 수동 검토 |
| **G-4** | 생략형 후속 승계(PL-1) | (a) 도입 · 고지 동반 · 플래그 없음(D-162 예외 — 시나리오가 이미 기대하는 동작의 결함 교정으로 본다) · (b) 도입하되 플래그 기본 off · (c) 도입 안 함(G-01 t3 기대 수정) | **(a)** — 고지가 있어 침묵 승계가 아니다. D-162 예외로 보기 어렵다고 판단하면 (b) |
| **G-5** | 폐쇄망 오염 유사어 정리 방식 | (a) F-5 `audit` → 사용자 검토 → `prune --apply`(백업 선행) · (b) 유사어 저장소 전체 재적재(운영자·LLM 등록분 전손) · (c) 정리 안 함(F-1·F-2·F-4 우회만) | **(a)** — (c)만으로도 H군은 살지만 오염 항목은 다른 양식의 매핑을 계속 흔든다 |
| **G-6** | F-2 적용 범위 | (a) EAV 피벗 DB에서 Pass 2를 구조 선언 테이블로 한정 · (b) 날짜 접미사 스냅샷 테이블만 제외 · (c) 둘 다 매칭 경로에 | **(a)** — 매칭은 구조 선언으로 한정하고, 날짜 접미사 규칙은 등록 차단(F-4)에만 둔다. 오염 10종 중 날짜 접미사 테이블은 1개(2종)뿐이라 (b)만으로는 부족하다 |

> **확정(2026-09-28 · D-269)** — 사용자가 「권고대로 확정 후 전부 구현」을 골랐다. G-1 (b) · G-2 전건(U-7은 G-3 전까지 `manual_review`) · G-3 (a) · G-4 (a) · G-5 (a) · G-6 (a) — 전부 위 권고 열 그대로다.

### 5.1 G-3 확인 방법 (사용자 · 폐쇄망 · 읽기 전용)

**묻는 것**: 진행 중인 달(실행 시점의 당월)에 월 통계 `cmm_metric_stat_m` 행이 있는가, 있으면 값이 채워져 있는가.
**왜 필요한가**: 프로필 규칙(`config/db_profiles/polestar_cm_gp.yaml` 「이번 달」 항목 · 2026-09-07 결정)은 *"월간 통계는 직전월까지만 집계되므로 현재 월을 월 통계로 조회하면 전부 null"*이라 보고 일간 통계(`cmm_metric_stat_d`, 당월 1일~어제)로 집계하게 한다. C-06 단언은 반대로 월 통계를 요구한다. 둘 중 무엇이 틀렸는지는 폐쇄망 데이터 사실이다.

```sql
-- 공동존(PostgreSQL · polestar_cm_gp / polestar_cm_yd) — 직전월·당월 두 달만 본다
SELECT stat_date,
       COUNT(*)        AS row_cnt,
       COUNT(avg_val)  AS avg_non_null,
       COUNT(max_val)  AS max_non_null
FROM polestar.cmm_metric_stat_m
WHERE stat_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMM')
GROUP BY stat_date
ORDER BY stat_date;

-- 은행존(DB2 · polestar_b0) — 스키마 대문자 · 날짜 연산 방언
SELECT STAT_DATE,
       COUNT(*)        AS ROW_CNT,
       COUNT(AVG_VAL)  AS AVG_NON_NULL,
       COUNT(MAX_VAL)  AS MAX_NON_NULL
FROM POLESTAR.CMM_METRIC_STAT_M
WHERE STAT_DATE >= TO_CHAR(CURRENT DATE - 1 MONTH, 'YYYYMM')
GROUP BY STAT_DATE
ORDER BY STAT_DATE;
```

| 결과 | 판단 | 고칠 곳 |
|---|---|---|
| 당월 행 없음 · 또는 있어도 `avg_non_null = 0` | 프로필 규칙이 맞다(일간 집계가 정답) | C-06 단언을 `cmm_metric_stat_d` + 당월 범위로 고치고 `manual_review` 해제 |
| 당월 행이 있고 값이 채워짐 | 프로필 규칙이 틀렸다(월 통계가 진행 중 월을 갱신) | 프로필 `이번 달` 규칙·예시를 월 통계로 고치고(`db_profiles` — 공용 코드 아님) C-06 종전 단언 복원(`c_metric.yaml` 주석에 원문 보존) |

- 세 DB의 결과가 다르면 DB별 프로필을 따로 고친다(DB별 특화는 프로필에 — D-089).
- 매월 1일에는 당월 행이 원래 없다. 확인은 2일 이후에 한다.

---

## 6. 착수 순서 · 재측정

```
0. 게이트     G-1 ~ G-6
1. 무과금     V-1 → V-2 → V-3 → V-5            (하네스 — 이 run 재판정·보정 수치 부기)
2. 무과금     F-1 → F-1b → F-6 → F-3 → F-2 → F-4 → F-5   (각각 커밋 · 로컬 재현 테스트 먼저)
              S-1                                (따로 커밋)
3. 무과금     V-4(= 108·CU-B3) · V-6 · U-1~U-6(G-2 뒤)
4. 사용자     F-0 audit → G-5 → prune(백업) · 벤치 계정 폼필 확인 이력 삭제      (폐쇄망)
5. 표적 재측정 --only H-10,H-11,H-12,H-13,H-15,H-17,I-01,I-07,B-03 (2단 · 폐쇄망)
6. 새 캠페인   G-1 결정대로 · D-266 ① 가드 · 180초 주입
```

- **119 구현분(미커밋)과 커밋 경계를 나눈다.** 119 N-1이 응답 모양(일반 조회 = 코드 렌더 표 + LLM 요약 2~3줄 · 폼필 역질문 턴 = 결정적 안내만)을 바꿔 H군 문구 단언이 흔들릴 수 있다. U-4 등 문구 단언은 **이 구조를 기준으로** 쓴다. 119 Q-4로 `서버명` 셀 값도 바뀐다. 표적 재측정에서 119 효과와 120 효과를 따로 읽으려면 120 커밋을 섞지 않는다.
- 119 세션 통지(2026-09-28): 119 v4는 `field_mapper`를 **수정하지 않았고 계획도 없다** — F 트랙과 파일 충돌이 없다. Q-5는 `schema_analyzer.py`(플래그 기본 off)라 120과 무관하다. 119는 team-lead 통합 중이라 추가 변경이 있으면 다시 통지받기로 했다.
- F 트랙은 픽스처·모의 사전으로 **LLM 없이** 검증된다. 로컬 MLX 스모크(D-240)는 필요하지 않다. S-1은 모의 그래프 스트림으로 확인한다.
- 단계 4의 Redis 쓰기(prune · 이력 삭제)는 **사용자가 폐쇄망에서** 실행한다. 백업이 먼저다.

### 성공 기준 — 이 run 대비

| 지표 | 이 run | 목표 |
|---|---:|---:|
| 3단 `db_ids` 허위 불합격 | 12 | 0 (V-1 · S-1) |
| 사다리 정확도 차 보고값 | −30.4%p | 보정값 재생성(−19.6%p) |
| 완주율 신호 | −14.6%p(정의 오류) | 교정 정의 값 |
| 월 시리즈 발동 | 0 / 16 | 16 / 16 (표적) |
| H-10·H-11·H-12·H-13·H-15 기계 판정 | 전부 불합격 | 합격 |
| I-01~I-06 역질문 후보 | `['비고']` | `담당자` 포함 |
| I-07 t2 | 불합격 | 구조 필드 고지 발화 |
| 서브 테이블 오매칭(§2.4 10종) | 로그 222회 | 0 |
| `tier_decision.tier` | `null` | `intent_orchestration` |
| 2단 비-타임아웃 불합격 중 하네스·단언 귀속 | 11 / 20 | 0 |
| 기능 합격률(D-241 분모) | 30.5% | **회귀 감시선**(하락 없음 · 단언 교정분은 따로 적는다) |

- I-01~I-06 행은 **폐쇄망의 `담당자` 이력을 지운 뒤** 목표가 성립한다. 지우기 전에는 V-4 선적재 탐지가 턴을 보내지 않고 첫 턴을 `invalid`(판정 제외)로 적재한다 — 불합격이 아니다.

### 6.1 구현 현황 (v2 · 2026-09-28 — 사용자 지시 *"119번 계획을 고려하여 120번 계획을 구현하라."* · 게이트 「권고대로 확정 후 전부 구현」 · D-269)

코드로 할 수 있는 항목을 전부 구현했다. **커밋은 하지 않았다.** 119 미커밋분과 파일이 겹치는 곳(`query.py` · `output_generator.py` · `subagents.py` · `multi_db_executor.py` · `input_parser.py` · `state.py` 주석)은 부분 편집으로 얹었다. 과금 호출 0 · 실 LLM 0 · DB 0.

| ID | 상태 | 구현 요지 | 주요 파일 · 테스트 |
|---|---|---|---|
| V-1 ★ | 완료 | `done.db_scope.db_ids`가 비면 `executed_sqls[].source`로 판정 · 행에 `db_ids_source`(`scope`·`executed`) · 옛 행은 재판정 시 같은 폴백. 이 run 사본 재판정: 3단 88행이 `executed` 출처 · **3단 `db_ids` 허위 불합격 12 → 0** · 정확도 **−19.57%p · 9:0 · p=0.0039**(팀 리드 독립 재현) | `scripts/scenario/{client,runner,assertions}.py` · `tests/test_scenario/test_plan120_harness.py` |
| V-2 ★ | 완료 | 완주 = 응답 모드가 `error`·`crash`·`hang`이 아니고 타임아웃 미평가 아님 — 기능 불합격은 완주. 원본 사본에서 3단 완주율 **+6.8%p · 2:9 · p=0.065** 재현(단언 보정과 무관) | `scripts/bench/sweep.py` · `tests/test_scripts/test_plan120_bench.py` |
| V-3 ★ | 완료 | `tier_by_arm()`이 실행 생략 arm에 기준선 관측 단을 싣는다 — 세 분기(최적·차이 없음·판정 불가) 모두 `blocked` 없이 `intent_orchestration` | `sweep.py` · `__main__.py` |
| V-4 ★ | **범위 한정 구현** | **확인 이력 키 `formfill:memory:{signature}`에 사용자 스코프가 없다**(`src/schema_cache/form_memory.py`) → 「벤치 계정 이력 전체 삭제」는 전역 삭제라 **구현하지 않았다**(지시). 대신 ①teardown `forget_form_memory`(턴 전후 스냅샷 차이 중 `forget_form_fields` 선언 필드만 · 조회 `touch=False` · D-217 ⑪ 선례 · I-02~I-06 선언) ②업로드 시나리오 실행 전 이력 선적재 탐지 → **이력에 판정이 달린 시나리오**(폼필 역질문 기대 · `send.form_*` · `forget_form_memory` — `form_memory_dependent`)만 첫 턴 `invalid` + 뒤 턴 건너뜀, **턴을 보내지 않는다**(보내면 서버가 `touch=True`로 TTL 연장) · 그 밖의 업로드 시나리오(H군)는 선적재 사실만 기록(코드 리뷰 반영 — 실사용자 기억까지 H군을 무효로 돌리면 표적 재측정 불성립) ③러너가 Redis를 못 읽으면 판정을 바꾸지 않고 「미확인」 기록. 시그니처는 서버와 같은 파서로 로컬 계산(LLM 0) | `runner.py` · `catalog.py` · `testdata/scenarios/i_formfill_hitl.yaml` |
| V-5 | 완료 | 합성 축(`LADDER_TIER` 등)을 「카탈로그에 없는 축」 검사에서 제외 · 미측정 축 처분 「미측정 — 구간 미완, 재측정 대기」 | `__main__.py` · `optimize.py` |
| V-6 | 완료(원인 = 서버) | `_extract_node_progress`에 분기가 없는 노드는 None → `node_complete` 미발행이었다(raw: 두 노드 `node_path` 16행 · `node_calls` 0건 · 3단 `cache_management` 4행도 같은 유형). 추적 노드는 이제 항상 완료 이벤트(`data: {}`). 러너 수정 불필요 | `src/api/routes/query.py` · `tests/test_api/test_plan120_stream_scope.py` |
| V-7 | 보류 | 조용한 오답 분류 규칙 신설은 관측 1회라 보류 — 거짓 양성(B-09)은 U-2, 미탐(3단 D-03)은 U-6로 처리 | — |
| G-1 (b) | 완료 | 캠페인 `--cite-tier <인용 기록>` — 새 캠페인에서만 사다리 단을 인용으로 고정(구간을 돈 캠페인·판정 있는 캠페인 거부 · `--campaign` 필수). 인용 축은 얼리되 미측정·미완으로 세지 않음. 판정 문장·계획 표·합산 리포트에 「인용 — 재지 않았다」 + 보정·원 보고 수치 · 보정 사유 · 상한 차이 경고 · 재측정 조건 | `campaign.py` · `__main__.py` · 신규 `scripts/bench/citations/ladder-1-20260923-140539.json` |
| S-1 ★ | 완료 | 스트림 두 경로가 `_scope_state`(체크포인트 + 입력 + 루트 직속 노드 델타 누적)로 `build_db_scope` — `done`과 `_store_result` 같은 값 · 모의 3단 멀티 DB `[]` → `[gp, yd]`(비스트림과 같음). 계약 테스트가 노드 델타(`build_db_scope(output,`)를 허용해 결함을 굳히고 있어 좁혔다(`docs/18`) | `query.py` · `tests/test_api/test_db_scope_contract.py` |
| S-1b | 완료(팀 리드) | S-1 확인 중 발견 — 2단 실시간 사용률 결과(Plan 71)에 `target_db_ids`·`db_origin`이 없어 승격 불가(이 run C-12 `db_ids []`). SQL 경로와 대칭으로 추가 · 기준선에서 `KeyError`로 실패하던 테스트가 통과 | `src/orchestration/subagents.py` · `tests/test_orchestration/test_plan120_realtime_db_promotion.py` |
| F-1 ★ | 완료 | 월 구조 판정(서브 헤더 파서·집계어)을 `src/utils/month_structure.py`로 이동(어댑터는 값 컬럼 대응만) · 명사 없는 월 필드도 매핑 스킵. 로컬 재현(오염 사전 주입): CPU 양식 기간 없음 → `server.Cpus` 202603~202608 default · "1월부터 3월까지" → 202510~202603 query · 메모리 양식 "2026년 6월 기준" → `server.Memory` 202601~202606 query. 명사 있는 기존 스킵 비트 동일 | `src/utils/month_structure.py` · `assembler.py` · `src/document/field_mapper.py` · `tests/test_nodes/test_plan120_month_structure.py` |
| F-1b | 완료 | 인식기가 실행 DB 허용 테이블 밖 매핑을 미매핑으로 봄 · 미발동 로그 3사유 분리 · 단일·멀티 호출부 대칭 배선 · **I-07 t2**: 그룹명 답변 키도 구조 필드 보호 | `assembler.py` · `query_generator.py` · `multi_db_executor.py` |
| F-2 ★ | 완료 | EAV 피벗 DB의 Pass 2~4 후보를 구조 선언 테이블로 한정(거부 시 INFO 로그 후 다음 단계) · §2.4 10종 전부 거부 · 선언 없으면 종전 | `src/document/field_mapper.py` · `tests/test_document/test_plan120_sub_table_scope.py` |
| F-3 | 완료 | 매핑 우선 DB = `selected_db_ids`·폼필 고정 DB > 질의 텍스트 힌트 · `selected_db_ids=[gp]` → `mapped_db_ids=[gp]` | `src/nodes/field_mapper.py` · `tests/test_nodes/test_plan120_field_mapper_priority.py` |
| F-4 ★ | 완료(4유형) | 쓰기 가드 `synonym_registration_block_reason` — ⓐ월 구조 필드명 ⓑEAV 피벗 DB 구조 선언 밖 ⓒ날짜 접미사 스냅샷 ⓓ**EAV 대상 「그룹\|서브」 복합명**(팀 리드 판단 — `처리능력\|(TPMC)` → `EAV:TotalSize` 28회 · D-148 ③). 전역·EAV·DB별 세 저장 공간 공통 · 정상 등록 비트 동일 | 신규 `src/document/synonym_write_guard.py` · `field_mapper.py` · `tests/test_document/test_plan120_synonym_write_guard.py` |
| F-5 | 완료 | `synonym_seeds.py audit`(읽기 전용 · `--json`) · `prune`(기본 dry-run · `--apply` 백업 선행 · `source=operator` 불가침 · 태그 없는 항목은 `--include-untagged` 없이 불가침) · `restore <backup.json>`(기본 dry-run) · 판정 = F-4 함수 | `scripts/synonym_seeds.py` · `tests/test_scripts/test_plan120_synonym_audit.py` |
| F-6 | 완료 | 앵커 없어도 월 구조 열을 역질문 후보·`[미작성 항목]`에서 제외 · 전부 0건이면 `[확인 필요]`(대상 자원·기간 재요청 안내) · 역질문 없는 폼필 턴 요약 입력에 「양식 채움 결과」 | `src/nodes/output_generator.py` |
| F-7 | 조사 → 미착수 | `config/db_profiles/*.yaml`에 속성 단위 선언이 없다. 이 run의 TPMC 칸 오기재는 D-152 ① 용량 규칙이 `if month_series:` 안에서만 돌아서다 — 월 시리즈가 미발동한 턴에는 규칙도 돌지 않았다(F-1로 H-10은 해소 · 월 열 없는 양식은 잔여) | — |
| U-1~U-6 | 완료 | U-1 `\bname\b … = 'cocm-hdkapp01'` + `row_count_total: {eq: 1}` · U-2 LOB 우선(짧은 값 먼저 COALESCE만 금지) · U-3 서브쿼리 또는 CTE · U-4 `response_must_contain_any` 신설 · U-5 `optin_zone_exclusive` 프로파일 · U-6 `\bcmm_alarm` — 이 run 실제 SQL·응답으로 정답 통과·오답 불합격 확인 | `testdata/scenarios/*.yaml` · `config/scenarios/profiles.yaml` · `catalog.py`·`assertions.py` |
| U-7 | `manual_review` | C-06 — G-3 확인 전까지(종전 단언은 카탈로그 주석에 원문 보존) | `c_metric.yaml` |
| PL-1 | 완료(플래그 없음) | 판정 `query_gen_common.elliptical_succession_filter` · 주입 `input_parser`(일반·Q-2 재사용 경로 멱등) · 판정 입력 `context_resolver.previous_entities_complete` · **리뷰 뒤 제외 조건 추가** — 원문이 서버 집합을 스스로 지목(대상 명사)하거나 선택·집계 신호(식별 외 필터·행 수·집계·정렬·순위/집합어)가 있으면 불발동(탐침 14건 과잉 발동 교정 · 음성 테스트 추가) · 고지 `**[직전 서버 기준]** 직전 서버 \`{값}\` 기준으로 조회했습니다 — 전체 서버는 '전체 서버 …'로 다시 요청하세요.` · 2·3단 모의 경로 모두 · 기존 지시어 경로와 중복 없음 | `input_parser.py` · `context_resolver.py` · `query_gen_common.py` · `output_generator.py` · `tests/test_nodes/test_plan120_elliptical_succession.py` |
| T3 | 이관 | T3-1~T3-8 증거와 함께 `plans/103` §4.2 잔여 장부 | `plans/103` |

**검증**: 신규 테스트 10개 파일 **293건** 통과(코드 리뷰 교정분 — PL-1 28건 · V-4 2건 포함) · 전체 `pytest` **10,720 통과 / 실패 1**(`test_plan104_local_sandbox_profile_gate` — 세션 시작 기준선 사본에서도 실패 · 이번 변경과 무관) · `arch_check --ci` 위반 0 · `overfit_check --ci` 신규 유입 0 · mypy 변경 src 13파일 기준선과 같은 수(신규 0) · ruff 변경 파일 22개 중 20개 기준선 이하, `scripts/scenario/runner.py`(+4)·`assertions.py`(+3)는 전부 UP045(`Optional[X]` 표기 — 두 파일의 기존 표기를 따름) · 매뉴얼 빌드 + `tests/test_manual` 479 통과. 기준선 대조는 세션 시작 상태 사본(HEAD `48b0eae` + 미커밋 · `.env`는 심볼릭 링크)에서 했다.

### 6.2 잔여 · 사용자 할 일 (폐쇄망)

1. **F-0 · G-5 오염 유사어 진단 → 정리**(백업 선행):
   - `python scripts/synonym_seeds.py audit` (읽기 전용 · `--json` 가능 · `--db <db_id>`로 한정) — 출력(DB·저장 공간·컬럼·단어·출처 태그)을 검토한다.
   - `python scripts/synonym_seeds.py prune` (dry-run — 지울 목록만) → 확인 뒤 `python scripts/synonym_seeds.py prune --apply` (백업 JSON 경로가 먼저 출력된다 · `source=operator`는 지우지 않는다). 태그 없는 항목(전역·EAV 속성명·레거시 목록형)까지 지우려면 audit 결과를 보고 `--include-untagged`를 더한다.
   - 되돌리기: `python scripts/synonym_seeds.py restore <백업 JSON>` (dry-run) → `--apply`.
2. **I군 폼필 확인 이력 삭제**(V-4 — 전역 일괄 삭제 도구는 두지 않았다): 웹 UI에서 `testdata/scenarios/fixtures/adhoc_owner_column.xlsx`를 첨부하고 입력창에 `?`만 보낸다 → 「저장 값 관리」 패널에서 **`담당자`만 선택 삭제**(다른 사람이 기억시킨 필드는 남긴다 · 매뉴얼 U-30). 지우기 전에는 I-01~I-06이 `invalid`로 적재된다.
3. **G-3 확인**(읽기 전용 SQL — §5.1) → 결과를 알려 주면 C-06 단언 또는 프로필 규칙을 고친다(U-7 해제).
4. **표적 재측정**(2단 · 폐쇄망): `python -m scripts.scenario --run --only H-10,H-11,H-12,H-13,H-15,H-17,I-01,I-07,B-03` — 월 시리즈 발동(SQL에 `cmm_metric_stat_m`) · H군 기계 판정 · I-01 역질문 후보에 `담당자` · I-07 t2 구조 필드 고지 · B-03 `name` 필터 합격을 본다. 119 효과와 따로 읽으려면 120 변경을 별도 커밋·배포로 나눈다(§6 첫 주의).
5. **새 캠페인**(G-1 (b) · D-266 ①⑤): `python -m scripts.bench --segment next --mode dry --campaign <새 이름> --cite-tier scripts/bench/citations/ladder-1-20260923-140539.json`으로 계획 표(사다리 단 = 「인용」 · 180초 주입)를 확인한 뒤 같은 인자로 `--mode run`. 옛 `run-closed`는 잇지 않는다.
6. **이번 범위 밖 잔여(후보)**: D-152 ①·② 규칙의 폼필 턴 전체 적용(월 열 없는 양식의 TPMC 칸 · 새 결정 필요) · PL-1 고지가 빠지는 경로(1단 합성·`general_inference`·`process_query` 빈 결과) · `INTENT_FRAME_ENABLED` 경로 PL-1 병합 · 2단 `intent_planner` 문맥 블록 PL-1 엔티티 줄 · 스트림 `turn_count`의 노드 델타 기준 · 일반 컬럼 대상 복합 필드명 등록(F-4 ⓓ 확장 여부) · I-07 t2 시나리오 수준 확인 · V-7 조용한 오답 분류 규칙. **코드 리뷰(2026-09-28) 잔여 Medium**: F-4 ⓓ는 올바른 복합 → EAV 매핑(`처리능력|(GB)`)도 캐시하지 않아 매 run LLM 매핑(D-148 의도 · D-152 ① 적용 범위 공백과 연결) · S-1 뒤 승격 없는 후속 턴은 `db_scope`가 승계 값이라 V-1 폴백 미발동(러너가 실행 DB를 우선할지 미결) · PL-1 — ~~레지스트리 전용 환경어(「개발도」「DR도」) 발동~~ **해소(D-271 — 운영 = 김포 · 개발·스테이징·DR = 여의도 배타 위치어 · 「운영체제」 제외 · 사용자 확정 2026-09-28)** · 파서가 필터를 비운 「80% 넘는 것도」 발동 · 3단 비조회 턴 뒤 2턴 전 엔티티 사용 · 비서버 이름 1행(알람명) 주입 가능(종전 `previous_entities` 추출 공유) · 승인 적용본(StructureStore) `allowed_tables` shape 미확인(없으면 F-2 제한 해제 · 안전 쪽).

---

## 7. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-09-28 (v2.1) | 사용자 지시 *"DR은 여의도이다. 수정하라."* — 코드 리뷰 잔여 PL-1 「개발도」「DR도」 오승계를 **D-271**로 해소: 운영은 김포, 개발·스테이징·DR은 여의도 배타 위치어로 레지스트리 등록(「운영체제」는 위치 아님 — 사용자 *"운영은 김포로 매핑하면 된다."*) · 김포 표기에서 DR 제거 · 위치 표면어 공용 판정 `term_in_text`(라틴 표면어 단어 경계) · 소비처 15곳 전환 · 테스트 36건 |
| 2026-09-28 (v2) | 사용자 지시 *"119번 계획을 고려하여 120번 계획을 구현하라."* · 게이트 G-1~G-6 「권고대로 확정 후 전부 구현」 → **D-269** 등재 · §5 확정 표기 · §5.1 G-3 확인 SQL · §6.1 구현 현황(V-1~V-6 · G-1 인용 경로 · S-1 + S-1b · F-1~F-6 · U-1~U-6 · PL-1 · V-4는 사용자 스코프 부재로 범위 한정 · F-7 조사 후 미착수 · V-7 보류) · §6.2 잔여·사용자 할 일 · T3 → `plans/103` §4.2 · `plans/110` 네 행 · `plans/114` §2.7 정정 주석 · 매뉴얼 U-34·U-29 · `docs/18` 3건 · 파일명 `-TODO` → `-WIP`(잔여 = 폐쇄망 작업) · 커밋 없음 |
| 2026-09-28 | 신설 — 벤치 구간 run `20260923-140539`(`ladder-1` · 226턴) 분석. 판정 오염 3건(스트림 `db_scope` 노드 델타 → 3단 `db_ids` 전 행 공란 · 완주 신호 정의 · 생략 대조군 단 미해석)과 보정 재계산(정확도 −30.4 → −19.6%p · 완주율 −14.6 → +6.8%p) · 2단 기준선 불합격 31턴 귀속 · 폼필 월 시리즈 16/16 미발동 사슬(스킵 규칙 · 오염 유사어 · 매핑 우선 DB · 인식기 로그 오보)과 로컬 재현 · 폼필 기억 sliding TTL · 단언 결함 5건 · 3단 전용 결함 8종(103 이관) · 트랙 V·S·F·U·PL·T3 · 게이트 G-1~G-6 · 코드 0건이라 `-TODO` · D-번호 예약 없음 |
