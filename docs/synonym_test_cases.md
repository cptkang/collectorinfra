# Synonym 테스트 케이스 — 로컬 샌드박스 수동 검증

> 대상 DB: `polestar` (docker `polestar_pg`, localhost:5434 / infradb / 스키마 `polestar`)
> 대상 계층: 프로필 EAV synonym · column_synonyms · 유연(flex) 매칭(D-075) · 등록 흐름(D-012) · 오매칭 방어 · **시드 사전(그룹 F)** · **시드×거버넌스(그룹 G)** · **크로스도메인 복합 쿼리(그룹 H)** · **고난도 SQL 골격(그룹 I)**
> 관련: `docs/synonym_management_analysis.md`, Plan 61 트랙 B, `config/db_profiles/polestar.yaml`,
> `docs/synonym_seed_migration_guide.md`(시드 절차), `docs/plan61_bugfix_plan.md`(B2 config·B4 페어링)
> 작성일: 2026-07-15 · 갱신: 2026-07-16 — **전 그룹 프롬프트를 복합 쿼리(다중 조건·집계·정렬·TOP-N·조인) 골격으로 상향**, 그룹 H(크로스도메인) 신설. 기대값은 라이브 샌드박스 실측(2026-07-16) 기준
> 갱신: 2026-07-18 — 그룹 I(고난도 SQL 골격: 다중 metric 피벗·셀프조인·HAVING·안티조인·스칼라 서브쿼리·다단 체인) 신설. 기대값은 라이브 샌드박스 실측(2026-07-18) 기준

## 사전 조건

- API 서버 기동(8050), Redis(6380)·polestar_pg(5434) 컨테이너 실행 중
- 아래 기대값은 `testdata/pg/init` 픽스처 기준 — 픽스처 변경 시 기대값 재확인 필요
- 그룹 C(flex)는 기본 OFF — 케이스에 명시된 플래그 절차를 따를 것
- **픽스처 08(모집단 페어링, B4) 적용 확인** — 두 장부가 각 50대여야 함:
  ```bash
  docker exec polestar_pg psql -U polestar_user -d infradb -tAc \
    "SELECT resource_type LIKE 'platform.%', count(*) FROM polestar.cmm_resource \
     WHERE resource_type='server.Server' OR resource_type LIKE 'platform.server%' GROUP BY 1"
  # 기대: f|50 / t|50
  ```
  적용 후 두 장부의 쌍둥이는 hostname·EAV 값(resource_conf_id 공유)이 동일하므로 기대값은 **hostname 단위**로 판정.
  "전체 서버" 질의에서 같은 hostname이 2행 나오면 resource_type 미한정 SQL(회귀가 아니라 생성 품질 문제로 분류)
- **시드 로드 상태 확인(그룹 F·G 전제)**: `docker exec collectorinfra-redis redis-cli HLEN schema:polestar:synonyms` → **75 이상**.
  미달이면 `python scripts/synonym_seeds.py load --db polestar` 선행(합집합 병합 — 기존 등록분 무손실)
- **복합 쿼리 판정 공통 원칙**: 프롬프트가 유사어 매핑 + SQL 골격(조건·집계·정렬·조인)을 동시에 검증하므로,
  실패 시 ①유사어 미매핑(잘못된 칼럼/테이블)인지 ②매핑은 맞았으나 SQL 골격 오류(조건 누락, 정렬 방향, 조인 누락)인지 **원인을 분리 기록**할 것.
  hostapo01/02는 구세대 텍스트 EAV 값(`16.0`, `62.1 GB`, `977.3 GB`)이라 수치 캐스트·정렬 결과가 비결정적 — 수치 조건/정렬 케이스에서는 **판정 대상에서 제외**(포함·제외·오류 모두 회귀 아님)

## 결과 확인 방법

1. 응답 하단 처리 현황(D-039)의 **생성 SQL**과 사용 유사어 표시 확인
2. `logs/audit-<날짜>.jsonl`의 `query_execution` 이벤트에서 실제 실행 SQL 확인
3. 필요 시 ground truth 직접 조회:
   `docker exec polestar_pg psql -U polestar_user -d infradb -c "<SQL>"`
4. 유사어 사전 직접 확인 (키 형식: `schema:{db_id}:synonyms` — DB별 Hash):
   `docker exec collectorinfra-redis redis-cli HGET schema:polestar:synonyms "<schema.table.column>"`

---

## 그룹 A — 프로필 EAV synonym (known_attributes, 정확일치)

플래그 무관, 기본 상태에서 모두 통과해야 하는 기준선. 2026-07-16부터 단순 단건 조회가 아니라 **복수 서버 IN·다속성 동시 피벗·수치 조건·정렬**을 포함한 복합 골격으로 검증한다.

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-A-01 | `DB-ORA-023과 SV-WEB-001 두 서버의 커널 파라미터를 비교해서 보여줘` | "커널 파라미터"→`OSParameter` + **복수 서버 IN 조건**. LOB이므로 `stringvalue` 사용(`stringvalue_short`면 빈 값 회귀). `is_lob=1` 조건이 SQL에 있으면 실패(Known Mistakes 2026-06-10) | 2행 — DB-ORA-023: `kernel.shmmax = 137438953472` 등, SV-WEB-001: `kernel.shmmax = 68719476736` 등 | ☐ |
| SYN-A-02 | `SV-WEB-001 서버의 sysctl 설정에서 vm.swappiness 값이 얼마인지 알려줘` | 영문 동의어 "sysctl"→`OSParameter` + **LOB 텍스트 내 특정 키 추출**(SQL LIKE 또는 결과 후처리 — 어느 쪽이든 최종 답이 맞으면 통과) | `vm.swappiness = 10` | ☐ |
| SYN-A-03 | `SV-WEB-001, DB-ORA-023, cocm-hdkapp01 세 서버의 제조사, 일련번호, 모델명을 표로 보여줘` | "제조사"→`Vendor`, "일련번호"→`SerialNumber`, "모델명"→`MODEL` — **3개 서버 IN × 3개 속성 동시 피벗** | SV-WEB-001: HPE/KR2024WEB0001/ProLiant DL380 Gen10 · DB-ORA-023: Dell/KR2023ORA0023/PowerEdge R750 · cocm-hdkapp01: HPE/KR2024APP0001/ProLiant DL360 Gen10 | ☐ |
| SYN-A-04 | `hostapo01과 hostapo02의 타임존과 제조사를 함께 보여줘` | "타임존"→`GMT`, "제조사"→`Vendor` — **복수 서버 × 복수 속성 동시 매핑** | 2행 모두 `GMT+09:00` / `Dell Inc.` | ☐ |
| SYN-A-05 | `논리코어가 8개 이상인 서버의 서버명, 논리코어 수, 메모리 용량을 논리코어 내림차순으로 보여줘` | "논리코어"→`LOGICALCORE`(server.Cpus), "메모리 용량"→`TotalSize`(server.Memory). **자식 리소스 EAV 피벗(D-068) + EAV 값 수치 조건 + 정렬**. 값이 NULL이면 server.Server 행에만 조인한 회귀 | DB-ORA-023(16/65536)·cocm-hdkapp01(16/65536) → SV-WEB-001(8/32768) 순. SV-BATCH-009(4코어)는 **제외**돼야 함. hostapo01/02(`16.0`/`62.1 GB`)는 판정 제외 | ☐ |

## 그룹 B — column_synonyms: name vs hostname 구분 (D-061 계열)

샌드박스는 `name`(DB-ORA-023)과 `hostname`(dbora023)이 다르게 적재되어 있어 구분 검증 가능.
정확일치 단건에서 **LIKE 패턴·칼럼 간 비교**로 상향 — 어느 칼럼을 선택했는지가 결과 건수로 그대로 드러난다.

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-B-01 | `장비명이 SV-로 시작하는 서버의 장비명과 IP 주소를 보여줘` | "장비명"→`name` + **LIKE 전방일치**. SQL이 `c.name LIKE 'SV-%'` (hostname으로 가면 svweb001·svbatch009는 패턴 불일치로 0건) | 2건 — SV-WEB-001(10.61.0.1), SV-BATCH-009(10.61.0.4) | ☐ |
| SYN-B-02 | `호스트네임에 batch가 포함된 서버의 장비명과 IP를 찾아줘` | "호스트네임"→`hostname` + **LIKE 부분일치**. SQL이 `hostname LIKE '%batch%'` (name 칼럼 + 대소문자 구분 LIKE면 'SV-BATCH-009'가 매칭되지 않아 0건) | 1건 — SV-BATCH-009 / 10.61.0.4 | ☐ |
| SYN-B-03 | `서버 이름과 호스트명이 서로 다른 서버 목록을 보여줘` | "서버 이름"→`name`, "호스트명"→`hostname` 분리 매핑 + **칼럼 간 비교 조건**(`name <> hostname`) | 5건(hostname 단위) — DB-ORA-023, SV-WEB-001, SV-BATCH-009, hostapo01, hostapo02 | ☐ |

## 그룹 C — 유연(flex) 매칭 (Plan 61 트랙 B, D-075)

**플래그 절차**: ① 기본 상태(OFF)에서 아래 4건 실행 → 기준선 기록. ② `.env`에 `SYNONYM_FUZZY_MATCH=true` 추가(인라인 주석 금지 — 주석은 별도 줄) 후 서버 재시작. ③ 동일 4건 재실행 → 전후 비교.
프롬프트의 표현은 모두 **사전 미등록 변형**이므로 OFF에서는 매핑 실패/부정확, ON에서는 정확 매핑이 기대 동작.
**미등록 변형 단어("파라메터"·"시리얼 넘버"·"메모리 사이즈"·"하이퍼 스레딩")는 flex 검증의 핵심이므로 프롬프트 개편 후에도 그대로 유지** — 쿼리 골격(IN·정렬·LIMIT·다속성)만 복합화했다.

**전제 확인(시드 로드 후)**: 그룹 F의 시드 로드가 위 4건 표현을 등재하지 않았는지 확인 — 사전에 있으면 정확일치로 통과해 버려 flex 검증이 무효(2026-07-15 시드 기준 4건 모두 미등재 확인됨).
**판정 주의**: ON 전환 후에도 OFF와 완전 동일하면 플래그 미반영 — nested config 임포트 고정 회귀(B2, `docs/plan61_bugfix_plan.md`) 의심. 반영은 반드시 **서버 재시작**으로(프로세스 내 env 플립은 측정 무효 이력 있음).

| ID | 프롬프트 | 미등록 변형 → 매칭 단계 | ON 기대 결과 | OFF 판정 | ON 판정 |
|----|---------|----------------------|-------------|:---:|:---:|
| SYN-C-01 | `DB-ORA-023과 SV-WEB-001의 커널 파라메터를 비교해서 보여줘` | "파라메터"(오타) → 자모 편집거리. **복수 서버 IN** 골격 | `OSParameter` 2행(SYN-A-01과 동일 결과) | ☐ | ☐ |
| SYN-C-02 | `전체 서버의 시리얼 넘버를 제조사와 함께 보여줘` | "시리얼 넘버" → "시리얼" 부분어 포함(0.85~0.95). **미등록 변형("시리얼 넘버") + 등록어("제조사") 혼합 매핑** — 한쪽만 매핑되는 비대칭 확인 | `SerialNumber`+`Vendor` 동시 조회(SYN-A-03 서버들 값 포함) | ☐ | ☐ |
| SYN-C-03 | `메모리 사이즈가 큰 순서로 상위 3대 서버를 보여줘` | "메모리 사이즈" → "메모리크기/메모리용량" 근사. **정렬 + LIMIT(TOP-N)** 골격 | `TotalSize`(server.Memory) 내림차순 — DB-ORA-023(65536)·cocm-hdkapp01(65536) → SV-WEB-001(32768). hostapo01/02(`62.1 GB` 텍스트)는 판정 제외 | ☐ | ☐ |
| SYN-C-04 | `hostapo01과 hostapo02의 하이퍼 스레딩 설정을 비교해줘` | "하이퍼 스레딩" → 구분자 제거 동등(0.97, 등록형 "하이퍼스레딩"). **복수 서버 IN**. HYPERTHREADING 데이터는 hostapo01/02에만 존재(SV-WEB-001 등 P61 서버엔 없음) | 2행 모두 `HYPERTHREADING` = `on` | ☐ | ☐ |

## 그룹 D — 등록 흐름 (pending_synonym_registrations, D-012) — 멀티턴 시나리오

양식 업로드 경로가 트리거. 텍스트 질의로는 재현 불가.

**시나리오 D-01**
1. Excel 양식에 사전 미등록 헤더(예: `장비 S/N 번호`, `OS 커널값`)를 넣어 업로드 질의
2. 응답에 LLM 추론 매핑이 **등록 후보 목록**으로 제시되는지 확인 → ☐
3. 후속 턴에 `전체 등록` 응답 → Redis 반영 메시지 확인 → ☐
   (부분 등록 변형: `1번만 등록` / 거부 변형: `건너뛰기`)
4. 등록한 표현으로 **복합 텍스트 질의** 재실행(예: `장비 S/N 번호가 KR로 시작하는 서버를 제조사와 함께 보여줘`) → synonym 정확일치 매핑 + LIKE 조건·다속성 골격이 함께 동작하는지 확인 → ☐
5. Redis 확인(선택): `docker exec collectorinfra-redis redis-cli HGET schema:polestar:synonyms "<매핑된 schema.table.column>"`
   (키는 `schema:{db_id}:synonyms` 형식 — `polestar:synonyms`가 아님)

**주의**: 폼업로드 턴 직후 텍스트 질의는 요청-스코프 상태 초기화(D-064) 대상 — 옛 template_structure가 재출력되면 별도 회귀.

## 그룹 E — 오매칭 방어 (부정 케이스)

synonym이 있어서 오히려 잘못 갈 수 있는 함정 검증. **함정 + 복합 골격(기간 필터·TOP-N·단위 변환·조인)** 동시 검증으로 상향.

| ID | 프롬프트 | 함정 | 기대 동작 | 판정 |
|----|---------|-----|----------|:---:|
| SYN-E-01 | `2026년 6월 서버별 CPU 사용률 평균이 높은 상위 3대를 보여줘` | "CPU"가 `LOGICALCORE` synonym에 포함 | 코어 수(EAV)가 아닌 **사용률 metric**(`cmm_metric_stat_m`, Utilization, server.Cpus) 경로 + `stat_date='202606'` + 내림차순 + TOP-3. 기대 순서: **DB-ORA-023(72.1) > cocm-hdkapp01(48.9) > SV-WEB-001(42.8)** (4위 SV-BATCH-009 18.3은 잘려야 함). 트랙 C(semantic compiler) 활성 시 결정적 피벗 SQL | ☐ |
| SYN-E-02 | `디스크 용량이 2TB 이상인 서버를 용량이 큰 순서로 보여줘` | "디스크용량"이 `TotalSize` synonym에 있으나 TotalSize는 server.Memory/server.Disks 양쪽 속성 | `server.Disks`의 TotalSize 기준 + **단위 변환(2TB=2097152MB) 수치 조건** + 정렬. 기대: DB-ORA-023(4194304) > SV-BATCH-009(3145728) > cocm-hdkapp01(2097152), SV-WEB-001(1048576)은 제외. 메모리 TotalSize로 가면(최대 65536) 0건 — 즉시 실패 판정. hostapo01/02(`977.3 GB` 텍스트)는 판정 제외 | ☐ |
| SYN-E-03 | `가용성 상태가 정상이 아닌 서버의 서버명과 제조사를 보여줘` | avail_status 값 매핑(규칙 13) + EAV 조인 결합 | `avail_status != 0` (특정 값 `= 1` 매핑이면 실패 — svbatch009는 2라 누락됨). 기대 8건: SV-BATCH-009(IBM), svr-app-03(HPE), svr-bat-02(VMware, Inc.), svr-db-04(Dell Inc.), svr-was-03(HPE), svr-was-07(Dell Inc.), svr-web-05(VMware, Inc.), svr-web-08(HPE) | ☐ |

---

## 그룹 F — 시드 사전 커버리지 (E5-1 시딩, 2026-07-15 신규)

시맨틱 모델→시드 파일→Redis 시딩(`scripts/synonym_seeds.py`, `docs/synonym_seed_migration_guide.md`)으로 **시딩 전 사전에 없던 성능지표·알람 어휘**가 등재됐는지 검증.
시드 히트는 **정확일치 경로**이므로 **전 플래그 OFF(기본 상태)에서 실행** — 그룹 C와 달리 fuzzy 불필요. 시딩 전 이 계열이 질의 수준 미매칭 38.5%의 주원인이었음(시딩 후 0%).
프롬프트는 **기간 필터·임계 조건·조인·TOP-1** 복합 골격으로 상향 — 시드 어휘 매핑과 SQL 골격을 동시 검증한다.

**사전 절차**: 사전 조건의 시드 로드 확인(HLEN ≥ 75). 원천-생성물 정합(드리프트) 검사:
```bash
python scripts/synonym_seeds.py derive --db all && git diff --exit-code config/synonym_seeds/
# diff 0 = 시맨틱 모델과 시드 파일 정합
```

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-F-01 | `2026년 6월에 메모리 사용률이 90%를 넘은 적이 있는 서버를 알려줘` | "메모리 사용률"→`cmm_metric_stat_*`(시드 패턴 B) + **"넘은 적이 있는"→`max_val` 선택 + 임계 조건 + 월 필터**. EAV `TotalSize`(용량)로 가면 실패. `avg_val > 90`으로 가면 0건(최고 82.6) — max/avg 칼럼 선택까지 판정 | **DB-ORA-023 1건**(202606 max_val 95.2). 처리 현황에 metric 테이블 표시 | ☐ |
| SYN-F-02 | `2026년 7월에 발생한 심각 알람이 몇 건인지 알려줘` | "심각"→`cmm_alarm.alarmseverity` + column_values `심각=3`(시드 패턴 C) + **ctime 날짜 범위 결합** | SQL `alarmseverity = 3` + 7월 범위 → **2건**(7/10, 7/13). `= 1` 등 다른 리터럴이면 column_values 미주입/환각. 날짜 조건 누락 시 4건으로 초과 | ☐ |
| SYN-F-03 | `경고 알람 목록을 발생 서버명과 함께 최신순으로 보여줘` | "경고"→`alarmseverity = 2` + **cmm_resource 조인 + ctime 내림차순** | 서버명 조인 시 2건: DB-ORA-023(2026-06-25) → SV-BATCH-009(2026-06-05) 순. LEFT JOIN이면 3건(더미 1건은 서버명 NULL) — 2·3건 모두 허용, `alarmseverity = 2` 리터럴과 정렬 방향이 판정 기준 | ☐ |
| SYN-F-04 | `2026년 6월 디스크 아이오가 가장 높았던 서버의 제조사와 일련번호를 알려줘` | 표기 변형 "디스크 아이오"가 시드에 **직접 등재** — fuzzy OFF에서도 정확일치. **metric TOP-1 → EAV 속성 크로스도메인 조인** | 디스크 IO metric(`MaxIORate`, 202606) 최고 서버 = **DB-ORA-023** → 제조사 `Dell`, 일련번호 `KR2023ORA0023` | ☐ |
| SYN-F-05 | (프롬프트 아님) `python scripts/synonym_seeds.py load --db polestar` **재실행** | 멱등성·무손실(합집합 병합) | HLEN 불변, 기존 단어 소실 없음 | ☐ |

## 그룹 G — 시드×거버넌스 연동 (E5-3, source=operator 보호)

시드 단어가 `source: operator`로 태깅되어 E5-3 감쇠(`prune_stale_synonyms`)에서 보호되는지 검증.
**플래그**: SYN-G-02만 `.env`에 `SYNONYM_GOVERNANCE=true` 필요(주석은 별도 줄) — prune은 오프라인 스크립트가 `.env`를 읽으므로 서버 재시작 불필요. 종료 후 원복할 것.

**SYN-G-01 — source 태그 확인** (플래그 무관):
```bash
docker exec collectorinfra-redis redis-cli HGET schema:polestar:synonyms "polestar.cmm_alarm.alarmseverity"
# 기대: JSON의 "sources"에서 시드 단어(심각/경고/critical 등)가 전부 "operator"
```
판정 ☐

**SYN-G-02 — 감쇠(prune)에서 시드 보존**:
```bash
# ① 스테일 llm 더미 심기 (last_used_ts=2001년 — 감쇠 대상)
docker exec collectorinfra-redis redis-cli HSET schema:polestar:synonyms "polestar.zz_prune_test.dummy" \
  '{"words": ["감쇠테스트어"], "sources": {"감쇠테스트어": "llm"}, "meta": {"감쇠테스트어": {"usage_count": 0, "last_used_ts": 1000000000.0, "confidence": 0.5}}}'
# ② prune 실행 (.env에 SYNONYM_GOVERNANCE=true 상태)
python - <<'EOF'
import asyncio
from src.config import load_config
from src.schema_cache.redis_cache import RedisSchemaCache
async def main():
    cfg = load_config()
    cache = RedisSchemaCache(cfg.redis, cfg.schema_cache)
    await cache.connect()
    print(await cache.prune_stale_synonyms(decay_days=180, db_id="polestar"))
asyncio.run(main())
EOF
# ③ 판정: removed에 zz_prune_test.dummy(감쇠테스트어) 포함,
#    시드 키는 무변(SYN-G-01 명령 재실행으로 확인)
```
주의: prune은 대상 db의 **다른 스테일 llm 단어도 함께 제거**할 수 있음 — 샌드박스 한정 실행. 판정 ☐

**SYN-G-03 — column_values 기존값 우선(비침습 변형)**: `HGET synonyms:column_values CMM_ALARM.ALARMSEVERITY` 스냅샷 → `load --db polestar` 재실행 → 동일(기존 등록 우선, 시드는 보충만). 판정 ☐

**SYN-G-04 — export 왕복(운영 누적분 이관)**:
```bash
python scripts/synonym_seeds.py export --db polestar -o /tmp/polestar_export.yaml
# 판정: 파일에 db_id: polestar + column_synonyms/eav_names/column_values 섹션 존재
```
주의: export본으로 git의 derive 생성물(`config/synonym_seeds/`)을 **덮어쓰지 말 것**(가이드 §3.3). 판정 ☐

---

## 그룹 H — 크로스도메인 복합 쿼리 (2026-07-16 신규)

단일 도메인 케이스로는 잡히지 않는 **알람×서버×EAV×metric 결합** 검증. 유사어 매핑(심각/제조사/메모리 용량/CPU 사용률/논리코어 — 전부 시드·프로필 등재어)이 전제이므로 **전 플래그 OFF(기본 상태)에서 실행**.
개별 유사어는 그룹 A/F에서 이미 검증되므로, 여기서는 **조인 경로와 집계 스코프**가 판정의 중심이다.

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-H-01 | `심각 알람이 발생한 적이 있는 서버들의 제조사와 메모리 용량을 보여줘` | **알람(column_values 심각=3) → cmm_resource → 자식 EAV 피벗** 3도메인 조인. 알람 4건 중 1건(더미)은 resource 미존재 — 서버 결과에 나타나면 안 됨 | 3건: SV-WEB-001(HPE/32768), DB-ORA-023(Dell/65536), SV-BATCH-009(IBM/16384) | ☐ |
| SYN-H-02 | `2026년 6월 CPU 사용률 평균이 40%를 넘은 서버의 서버명과 논리코어 수를 보여줘` | **metric 조건(cmm_metric_stat_m, avg_val>40, 202606) → EAV(LOGICALCORE) 역방향 결합** — E-01 함정의 양방향 버전: 같은 질의 안에서 "CPU 사용률"은 metric, "논리코어"는 EAV로 분리 매핑돼야 함. 2026-07-16 실측 회귀: LEFT JOIN한 metric 필터를 WHERE에 둬 서버명 전체 NULL(LEFT JOIN 강등) → validator 6.7 가드+프롬프트 규칙 추가(D-085). 서버명 NULL 재발 시 D-085 가드 미작동 회귀로 분류 | 3건: DB-ORA-023(16), cocm-hdkapp01(16), SV-WEB-001(8). SV-BATCH-009(18.3%)는 제외 | ☐ |
| SYN-H-03 | `제조사별 서버 대수를 집계해서 많은 순으로 보여줘` | **EAV 값 GROUP BY + 모집단 페어링(B4) 함정** — resource_type 미한정이면 두 장부가 함께 잡혀 전 그룹이 정확히 2배(34/32/22/2/2)로 나옴 | VMware, Inc. 17 > HPE 16 > Dell Inc. 11 > Dell 1 = IBM 1 (합 46 — Vendor 미보유 4대 제외). 2배 값이면 resource_type 미한정 실패 | ☐ |
| SYN-H-04 | `현재 활성 상태인 심각 알람이 있는 서버들의 2026년 7월 CPU 사용률을 보여줘` | **알람 상태 필터(ACTIVE + 심각=3) × metric 결합**. 멀티인텐트 분해 시 t1(alarm_query, 서버 선별)→t2(data_query, 지표) + **prior_rows 스코프 주입**(D-086)이 정답 경로 — t2 SQL에 `name IN ('SV-WEB-001','SV-BATCH-009')`가 있고 알람 테이블/환각 조건이 없어야 함. 2026-07-18 실측 회귀: prior_rows 죽은 배선으로 t2가 `resource_type='alarm.Alarm'` 환각 → 0건(CPU 미조회). 재발 시 t2 SQL의 IN 스코프 유무부터 확인 | 2건: SV-WEB-001(202607 avg 38.5), SV-BATCH-009(202607 avg 16.9). CLEARED인 DB-ORA-023이 포함되면 상태 필터 누락. **변형** `…최근 1개월 CPU 사용률…`(실행일 2026-07 기준 202606): SV-WEB-001 42.8, SV-BATCH-009 18.3 | ☐ |

---

## 그룹 I — 고난도 SQL 골격 복합 쿼리 (2026-07-18 신규)

유사어 어휘는 **전부 기존 등재어**(CPU 사용률/메모리 사용률/논리코어/제조사/일련번호/심각 — 그룹 A·F·H에서 매핑 검증 완료)만 사용하고,
여기서는 **SQL 골격의 난도**(동일 테이블 다중 참조, 기간 셀프조인, HAVING, NOT EXISTS 안티조인, 스칼라 서브쿼리, 4단 도메인 체인, 2단 집계)를 올려 검증한다.
**전 플래그 OFF(기본 상태)에서 실행.** 실패 시 사전 조건의 공통 원칙대로 ①유사어 미매핑 ②골격 오류를 분리 기록할 것 — 이 그룹은 ②가 판정의 중심이다.

CPU 사용률 월별 기준값(202605/202606/202607, avg_val): DB-ORA-023 68.4/72.1/70.5 · cocm-hdkapp01 45.6/48.9/47.2 · SV-WEB-001 35.2/42.8/38.5 · SV-BATCH-009 15.8/18.3/16.9

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-I-01 | `2026년 6월 서버별 CPU 사용률과 메모리 사용률 평균을 CPU 사용률이 높은 순으로 함께 보여줘` | **동일 metric 테이블(Utilization) 2회 참조 — 자식 리소스 타입(server.Cpus/server.Memory) 분리 피벗**. 202606에는 server.FileSystems Utilization도 존재하므로 resource_type 미한정이면 세 값이 합산 평균으로 뭉개짐 | 4행: DB-ORA-023(72.1/82.6) > cocm-hdkapp01(48.9/64.3) > SV-WEB-001(42.8/58.7) > SV-BATCH-009(18.3/40.7). DB-ORA-023이 75.4 등 제3의 값이면 FileSystems 혼입 실패 | ☐ |
| SYN-I-02 | `2026년 5월과 비교해서 6월에 CPU 사용률 평균이 가장 많이 상승한 서버와 상승폭을 알려줘` | **동일 테이블 기간 셀프조인(또는 stat_date 조건부 집계) + 산술 + TOP-1** — 두 stat_date를 한 쿼리에서 결합 | SV-WEB-001, +7.6%p(35.2→42.8, 소수 반올림 허용). DB-ORA-023(+4.3)이면 메모리 사용률 오매핑, 상승폭이 전부 음수면 6→7월 기간 오독 | ☐ |
| SYN-I-03 | `월 평균 CPU 사용률이 40%를 넘은 달이 2개월 이상인 서버와 그 개월 수를 보여줘` | **WHERE(행 조건 avg_val>40) + GROUP BY + HAVING(그룹 조건 count≥2) 2단 필터** | 2건: DB-ORA-023(3개월)·cocm-hdkapp01(3개월). SV-WEB-001(202606 한 달만 42.8)이 포함되면 HAVING 누락 | ☐ |
| SYN-I-03b | `월 평균 CPU 사용률이 40%를 넘은 달이 2개월 이상인 서버와 그 개월 수를 보여줘. cpu 월 평균 사용률, 메모리 월 평균 사용률, cpu 최고 사용률을 같이 보여줘.` | I-03 + **다중 metric 동반 출력 → CTE(WITH) 2단 집계 유도**. 2026-07-18 실측 회귀: LLM은 올바른 CTE SQL을 생성했으나 validator `_extract_cte_names`가 선두 주석(`-- 설명`) 때문에 `^WITH` 앵커 실패 → CTE를 미존재 테이블로 오거부, 3회 소진 후 "데이터 없음" 강등(executed_sql 공백) → 주석 제거 후 판정으로 수정(D-087). 재발 시 audit에 user_request만 있고 query_execution이 없는지부터 확인 | 2건: DB-ORA-023(3개월)·cocm-hdkapp01(3개월) + 사용률 3종. 동반 값은 생성 SQL의 해석에 따라 최신월(70.5/80.1/88.9 · 47.2/63.1/73.1) 또는 전월 평균(70.3/80.3/91.6 · 47.2/63.1/75.4) — 두 해석 모두 허용, 개월 수 2건이 판정 핵심 | ☐ |
| SYN-I-04 | `논리코어가 8개 이상인 서버 중에서 지금까지 알람이 한 번도 발생하지 않은 서버를 알려줘` | **EAV 수치 조건 + NOT EXISTS 안티조인**(알람 부재 증명, 조인 키 `cmm_alarm.resource_id` = 서버 id). 더미 알람(resource_id 1~5)은 실서버 미매핑이라 무영향 | 1건: cocm-hdkapp01(16코어). DB-ORA-023·SV-WEB-001이 나오면 EXISTS 방향 반전, svr-* 서버가 나오면 platform 장부 미한정(B4 — platform 쪽 LOGICALCORE는 `8.0` 등 텍스트로 34대 존재). hostapo01/02 판정 제외 | ☐ |
| SYN-I-05 | `2026년 6월에 전체 서버 평균보다 CPU 사용률이 높았던 서버를 사용률과 함께 보여줘` | **스칼라 서브쿼리 임계(전체 평균 45.5 — 데이터에서 도출)** — 리터럴 임계 환각 방지 | 2건: DB-ORA-023(72.1)·cocm-hdkapp01(48.9). SV-WEB-001 포함 3건이면 임계를 40 안팎으로 환각, 1건이면 50 이상으로 환각 | ☐ |
| SYN-I-06 | `현재 활성 상태인 심각 알람이 있는 서버 중 2026년 6월 CPU 사용률 평균이 가장 높았던 서버의 제조사와 일련번호를 알려줘` | **알람 상태 필터(ACTIVE+심각=3) → 서버 스코프 → metric TOP-1 → EAV 속성 4단 체인**(H-04 스코프에 TOP-1·EAV 확장). 전체 TOP-1(DB-ORA-023)과 스코프 내 TOP-1(SV-WEB-001)이 다르므로 스코프 누락이 결과로 즉시 드러남 | SV-WEB-001(42.8) → HPE / KR2024WEB0001. DB-ORA-023/Dell이면 알람 스코프(ACTIVE 또는 심각=3) 누락, SV-BATCH-009(18.3)면 정렬 방향 반전 | ☐ |
| SYN-I-07 | `제조사별 2026년 6월 CPU 사용률 평균을 높은 순으로 보여줘` | **EAV 값 GROUP BY × metric 집계 2단(서버→제조사)** — H-03(대수 집계)의 metric 결합판 | 3행: Dell 72.1 > HPE 45.9(42.8·48.9의 평균) > IBM 18.3. HPE 서버 2대가 개별 행으로 나오면 GROUP BY 미작동. metric 없는 제조사(VMware, Inc./Dell Inc. 등)의 NULL 행은 LEFT JOIN 여부에 따른 차이로 허용 — 값 있는 3개 벤더의 순서·값이 판정 기준 | ☐ |

---

## 판정 기록

| 실행일 | 실행자 | 플래그 상태 | 결과 요약 |
|-------|-------|-----------|----------|
|  |  |  |  |
