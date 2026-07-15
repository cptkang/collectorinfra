# Synonym 테스트 케이스 — 로컬 샌드박스 수동 검증

> 대상 DB: `polestar` (docker `polestar_pg`, localhost:5434 / infradb / 스키마 `polestar`)
> 대상 계층: 프로필 EAV synonym · column_synonyms · 유연(flex) 매칭(D-075) · 등록 흐름(D-012) · 오매칭 방어 · **시드 사전(그룹 F)** · **시드×거버넌스(그룹 G)**
> 관련: `docs/synonym_management_analysis.md`, Plan 61 트랙 B, `config/db_profiles/polestar.yaml`,
> `docs/synonym_seed_migration_guide.md`(시드 절차), `docs/plan61_bugfix_plan.md`(B2 config·B4 페어링)
> 작성일: 2026-07-15 · 갱신: 2026-07-15 — 그룹 F·G 신설(시드 시스템), 픽스처 08 전제·Redis 키 형식 반영

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

## 결과 확인 방법

1. 응답 하단 처리 현황(D-039)의 **생성 SQL**과 사용 유사어 표시 확인
2. `logs/audit-<날짜>.jsonl`의 `query_execution` 이벤트에서 실제 실행 SQL 확인
3. 필요 시 ground truth 직접 조회:
   `docker exec polestar_pg psql -U polestar_user -d infradb -c "<SQL>"`
4. 유사어 사전 직접 확인 (키 형식: `schema:{db_id}:synonyms` — DB별 Hash):
   `docker exec collectorinfra-redis redis-cli HGET schema:polestar:synonyms "<schema.table.column>"`

---

## 그룹 A — 프로필 EAV synonym (known_attributes, 정확일치)

플래그 무관, 기본 상태에서 모두 통과해야 하는 기준선.

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-A-01 | `DB-ORA-023 서버의 커널 파라미터를 보여줘` | "커널 파라미터"→`OSParameter`. LOB이므로 `stringvalue` 사용(`stringvalue_short`면 빈 값 회귀). `is_lob=1` 조건이 SQL에 있으면 실패(Known Mistakes 2026-06-10) | `kernel.shmmax = 137438953472` 등 커널 설정 텍스트 | ☐ |
| SYN-A-02 | `SV-WEB-001 서버의 sysctl 설정을 알려줘` | 영문 동의어 "sysctl"→`OSParameter` | `kernel.shmmax = 68719476736` 등 | ☐ |
| SYN-A-03 | `전체 서버의 제조사와 일련번호를 조회해줘` | "제조사"→`Vendor`, "일련번호"→`SerialNumber` | Dell/IBM/HPE/Dell Inc., KR2023ORA0023·KR2024WEB0001·DFZLCM2 등 | ☐ |
| SYN-A-04 | `hostapo01 서버의 타임존을 알려줘` | "타임존"→`GMT` | `GMT+09:00` | ☐ |
| SYN-A-05 | `전체 서버의 논리코어 수와 메모리 용량을 보여줘` | "논리코어"→`LOGICALCORE`(server.Cpus), "메모리 용량"→`TotalSize`(server.Memory). **자식 리소스 EAV 피벗(D-068)** — resource_type 구분 CASE WHEN + `platform_resource_id` GROUP BY. 값이 NULL이면 server.Server 행에만 조인한 회귀 | 서버당 1행, 코어 수·메모리 용량 모두 채워짐 | ☐ |

## 그룹 B — column_synonyms: name vs hostname 구분 (D-061 계열)

샌드박스는 `name`(DB-ORA-023)과 `hostname`(dbora023)이 다르게 적재되어 있어 구분 검증 가능.

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-B-01 | `장비명이 DB-ORA-023인 서버의 정보를 알려줘` | SQL이 `c.name = 'DB-ORA-023'` (hostname으로 가면 0건) | 1건 조회됨 | ☐ |
| SYN-B-02 | `호스트네임이 dbora023인 서버를 찾아줘` | SQL이 `hostname = 'dbora023'` (name으로 가면 0건) | 1건 조회됨 | ☐ |
| SYN-B-03 | `전체 서버의 서버 이름과 호스트명을 나란히 보여줘` | "서버 이름"→`name`, "호스트명"→`hostname` 분리 매핑 | 두 컬럼 값이 다른 행(DB-ORA-023/dbora023 등) 확인 | ☐ |

## 그룹 C — 유연(flex) 매칭 (Plan 61 트랙 B, D-075)

**플래그 절차**: ① 기본 상태(OFF)에서 아래 4건 실행 → 기준선 기록. ② `.env`에 `SYNONYM_FUZZY_MATCH=true` 추가(인라인 주석 금지 — 주석은 별도 줄) 후 서버 재시작. ③ 동일 4건 재실행 → 전후 비교.
프롬프트의 표현은 모두 **사전 미등록 변형**이므로 OFF에서는 매핑 실패/부정확, ON에서는 정확 매핑이 기대 동작.

**전제 확인(시드 로드 후)**: 그룹 F의 시드 로드가 아래 4건 표현을 등재하지 않았는지 확인 — "파라메터"·"시리얼 넘버"·"메모리 사이즈"·"하이퍼 스레딩"이 사전에 있으면 정확일치로 통과해 버려 flex 검증이 무효(2026-07-15 시드 기준 4건 모두 미등재 확인됨).
**판정 주의**: ON 전환 후에도 OFF와 완전 동일하면 플래그 미반영 — nested config 임포트 고정 회귀(B2, `docs/plan61_bugfix_plan.md`) 의심. 반영은 반드시 **서버 재시작**으로(프로세스 내 env 플립은 측정 무효 이력 있음).

| ID | 프롬프트 | 미등록 변형 → 매칭 단계 | ON 기대 결과 | OFF 판정 | ON 판정 |
|----|---------|----------------------|-------------|:---:|:---:|
| SYN-C-01 | `DB-ORA-023의 커널 파라메터를 보여줘` | "파라메터"(오타) → 자모 편집거리 | `OSParameter` 조회(SYN-A-01과 동일 결과) | ☐ | ☐ |
| SYN-C-02 | `전체 서버의 시리얼 넘버를 알려줘` | "시리얼 넘버" → "시리얼" 부분어 포함(0.85~0.95) | `SerialNumber` 조회(SYN-A-03과 동일) | ☐ | ☐ |
| SYN-C-03 | `전체 서버의 메모리 사이즈를 조회해줘` | "메모리 사이즈" → "메모리크기/메모리용량" 근사 | `TotalSize`(server.Memory) 조회 | ☐ | ☐ |
| SYN-C-04 | `hostapo01의 하이퍼 스레딩 설정을 알려줘` | "하이퍼 스레딩" → 구분자 제거 동등(0.97, 등록형 "하이퍼스레딩"). HYPERTHREADING 데이터는 hostapo01/02에만 존재(SV-WEB-001 등 P61 서버엔 없음) | `HYPERTHREADING` = `on` | ☐ | ☐ |

## 그룹 D — 등록 흐름 (pending_synonym_registrations, D-012) — 멀티턴 시나리오

양식 업로드 경로가 트리거. 텍스트 질의로는 재현 불가.

**시나리오 D-01**
1. Excel 양식에 사전 미등록 헤더(예: `장비 S/N 번호`, `OS 커널값`)를 넣어 업로드 질의
2. 응답에 LLM 추론 매핑이 **등록 후보 목록**으로 제시되는지 확인 → ☐
3. 후속 턴에 `전체 등록` 응답 → Redis 반영 메시지 확인 → ☐
   (부분 등록 변형: `1번만 등록` / 거부 변형: `건너뛰기`)
4. 등록한 표현으로 **텍스트 질의** 재실행(예: `전체 서버의 장비 S/N 번호를 알려줘`) → 이번엔 synonym 정확일치로 매핑되는지 확인 → ☐
5. Redis 확인(선택): `docker exec collectorinfra-redis redis-cli HGET schema:polestar:synonyms "<매핑된 schema.table.column>"`
   (키는 `schema:{db_id}:synonyms` 형식 — `polestar:synonyms`가 아님)

**주의**: 폼업로드 턴 직후 텍스트 질의는 요청-스코프 상태 초기화(D-064) 대상 — 옛 template_structure가 재출력되면 별도 회귀.

## 그룹 E — 오매칭 방어 (부정 케이스)

synonym이 있어서 오히려 잘못 갈 수 있는 함정 검증.

| ID | 프롬프트 | 함정 | 기대 동작 | 판정 |
|----|---------|-----|----------|:---:|
| SYN-E-01 | `전체 서버의 CPU 사용률을 알려줘` | "CPU"가 `LOGICALCORE` synonym에 포함 | 코어 수(EAV)가 아닌 **사용률 metric**(`cmm_metric_stat_m`, Utilization) 경로로 조회. 트랙 C(semantic compiler) 활성 시 결정적 피벗 SQL | ☐ |
| SYN-E-02 | `디스크 용량이 큰 서버를 알려줘` | "디스크용량"이 `TotalSize` synonym에 있으나 TotalSize는 server.Memory/server.Disks 양쪽 속성 | `server.Disks`의 TotalSize 기준 정렬(메모리 용량으로 정렬되면 실패). P61 서버 기대 순서: DB-ORA-023(4194304) > SV-BATCH-009(3145728) > cocm-hdkapp01(2097152) > SV-WEB-001(1048576). 주의: hostapo01/02는 구세대 텍스트값(`977.3 GB`)이라 문자열 정렬 시 최상단에 끼거나 숫자 캐스트 시 오류/제외될 수 있음(판정에서 제외하고 P61 4대 순서만 확인) | ☐ |
| SYN-E-03 | `가용성 상태가 정상이 아닌 서버를 알려줘` | avail_status 값 매핑(규칙 13) | `avail_status != 0` (특정 값 `= 1` 매핑이면 실패) | ☐ |

---

## 그룹 F — 시드 사전 커버리지 (E5-1 시딩, 2026-07-15 신규)

시맨틱 모델→시드 파일→Redis 시딩(`scripts/synonym_seeds.py`, `docs/synonym_seed_migration_guide.md`)으로 **시딩 전 사전에 없던 성능지표·알람 어휘**가 등재됐는지 검증.
시드 히트는 **정확일치 경로**이므로 **전 플래그 OFF(기본 상태)에서 실행** — 그룹 C와 달리 fuzzy 불필요. 시딩 전 이 계열이 질의 수준 미매칭 38.5%의 주원인이었음(시딩 후 0%).

**사전 절차**: 사전 조건의 시드 로드 확인(HLEN ≥ 75). 원천-생성물 정합(드리프트) 검사:
```bash
python scripts/synonym_seeds.py derive --db all && git diff --exit-code config/synonym_seeds/
# diff 0 = 시맨틱 모델과 시드 파일 정합
```

| ID | 프롬프트 | 검증 포인트 | 기대 결과 | 판정 |
|----|---------|-----------|----------|:---:|
| SYN-F-01 | `전체 서버의 메모리 사용률을 알려줘` | "메모리 사용률"→`cmm_metric_stat_*.avg_val`(시드 패턴 B). 시딩 전엔 metric 어휘 부재로 E5-1 게이트 미스 | metric 경로(Utilization, server.Memory) 조회 — EAV `TotalSize`(용량)로 가면 실패. 처리 현황에 metric 테이블 표시 | ☐ |
| SYN-F-02 | `심각 알람이 몇 건인지 알려줘` | "심각"→`cmm_alarm.alarmseverity` + column_values `심각=3`(시드 패턴 C) | SQL `alarmseverity = 3`, **4건**. `= 1` 등 다른 리터럴이면 column_values 미주입/환각 | ☐ |
| SYN-F-03 | `경고 알람 목록을 보여줘` | "경고"→`alarmseverity = 2` | **3건** | ☐ |
| SYN-F-04 | `디스크 아이오가 높은 서버를 알려줘` | 표기 변형 "디스크 아이오"가 시드에 **직접 등재** — fuzzy OFF에서도 정확일치 | 디스크 IO metric(`MaxIORate`) 경로 조회, 0건 아님 | ☐ |
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

## 판정 기록

| 실행일 | 실행자 | 플래그 상태 | 결과 요약 |
|-------|-------|-----------|----------|
|  |  |  |  |
