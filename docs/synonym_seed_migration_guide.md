# 유사어 시드 마이그레이션 가이드 (Plan 61 트랙 B)

> 대상 독자: 운영자·개발자. 설계 배경은 `docs/synonym_seed_migration_review.md` 참조.
> 구현: `scripts/synonym_seeds.py`(derive/load/export CLI), `src/schema_cache/synonym_loader.py`(load_seed_yaml/export_seed_yaml), `config/synonym_seeds/{db_id}.yaml`(시드 파일 4종).

## 1. 개요 — 무엇을 하는 도구인가

자연어 질의의 용어(예: "메모리 사용률", "심각 알람")를 DB 컬럼·테이블로 연결하는 **유사어 사전**은 Redis에 저장된다(`schema:{db_id}:synonyms` 등). 이 사전이 비어 있으면 스키마 보충 게이트(E5-1)가 성능지표·알람 질의를 놓친다(실측: 시딩 전 질의 수준 미매칭 38.5%).

본 도구는 **시맨틱 모델(단일 출처)에서 사전 내용을 결정적으로 생성한 시드 파일**을 만들어, 어떤 환경(개발망·폐쇄망 운영)에서든 같은 사전을 재현·이관할 수 있게 한다.

```
config/semantic_models/{db_id}.yaml ──derive──► config/synonym_seeds/{db_id}.yaml ──load──► Redis
config/db_profiles/{db_id}.yaml (known_attributes)     (git 커밋 = 마이그레이션 아티팩트)      ▲
                                                              └──────── export(운영 누적분) ◄──┘
```

| 명령 | 역할 | Redis 필요 |
|------|------|-----------|
| `python scripts/synonym_seeds.py derive [--db {id}\|all]` | 시맨틱 모델+프로필 → 시드 파일 생성(결정적) | 불필요 |
| `python scripts/synonym_seeds.py load [--db {id}\|all]` | 시드 파일 → Redis 병합 로드(무손실·멱등) | 필요 |
| `python scripts/synonym_seeds.py export --db {id} -o {file}` | Redis per-DB 사전 → 시드 형식 스냅샷 | 필요 |

## 2. 시드 파일이 Redis에 채우는 것

| 시드 섹션 | Redis 대상 | 소비처 |
|-----------|-----------|--------|
| `column_synonyms` ({schema.table.column: [단어]}) | `schema:{db_id}:synonyms` (per-DB) | **E5-1 텍스트 질의 매칭 게이트**(schema_analyzer 보충, D-051), 폼필 매핑 |
| `eav_names` ({속성명: [단어]}) | `synonyms:eav_names` + `synonyms:global` | EAV 속성 인식, 프롬프트 참조 |
| `column_values` ({컬럼: {단어: {op, value}}}) | `synonyms:column_values` | 값 표현→리터럴 변환(예: "심각"→ALARMSEVERITY=3) |

매핑 규칙(derive가 자동 적용): 패턴 A direct→`cmm_resource.{col}`, 패턴 A EAV→`eav_names`+`core_config_prop.name`, 패턴 B measures→`cmm_metric_stat_[h,d,m]`, 패턴 C severity_map→`cmm_alarm.alarmseverity`+column_values. 스키마 접두사는 도메인 config(`db_schema`, D-057)에서 자동 결정(b0=DB2는 `POLESTAR.` 대문자).

## 3. 운영 절차

### 3.1 신규 환경 초기 시딩 (폐쇄망 프로덕션 최초 구축 포함)
```bash
# 1) 시드 파일은 git에 커밋돼 있으므로 반입된 리포지토리에 이미 존재
ls config/synonym_seeds/            # polestar, polestar_b0, polestar_cm_gp, polestar_cm_yd

# 2) Redis 접속 정보(.env/.encenv REDIS_*) 확인 후 전 DB 로드
python scripts/synonym_seeds.py load --db all

# 3) 검증 (§5)
```

### 3.2 어휘 추가·수정 (일상 운영)
시드 파일을 **직접 편집하지 않는다**(생성물 — 재생성 시 소실). 절차:
```bash
# 1) 원천에 추가: config/semantic_models/{db_id}.yaml 의 해당 dimension/measure aliases
#    (또는 EAV 속성은 config/db_profiles/{db_id}.yaml known_attributes[].synonyms)
# 2) 재생성 — 동형 DB 전파 누락 방지를 위해 all 권장 (Known Mistakes 2026-07-09)
python scripts/synonym_seeds.py derive --db all
# 3) diff 리뷰 후 git 커밋 (시드 파일 diff = 사전 변경 리뷰)
git diff config/synonym_seeds/
# 4) 각 환경에서 반영
python scripts/synonym_seeds.py load --db all
```

### 3.3 운영 누적분 이관 (환경 A → 환경 B)
운영 중 LLM 발견→사람 승인(D-012)으로 누적된 단어는 시드 파일에 없다. 이관 절차:
```bash
# 환경 A(원본)에서 스냅샷 추출
python scripts/synonym_seeds.py export --db polestar_cm_gp -o /반출/polestar_cm_gp_export.yaml
# 파일 반입(폐쇄망 절차) 후 환경 B에서 로드 — load_seed_yaml은 경로 무관 동작하므로
# 반입 파일을 config/synonym_seeds/에 두거나, 임시로 해당 이름으로 복사 후 load
cp /반입/polestar_cm_gp_export.yaml config/synonym_seeds/polestar_cm_gp.yaml  # 또는 별도 보관
python scripts/synonym_seeds.py load --db polestar_cm_gp
```
> export 파일은 derive 생성물과 같은 스키마이므로 load로 그대로 반입된다. 단 **export본으로 git의 derive 생성물을 덮어쓰지 말 것**(원천-생성물 관계 파괴) — 이관 후에는 derive를 재실행해 git 상태를 복원한다.

### 3.4 신규 DB 편입 체크리스트에 추가
기존 체크리스트(①위치 힌트 ②base_url ③엔진 방언 ④db_schema — D-053/D-057)에 더해:
⑤ `config/semantic_models/{new_db}.yaml` 작성 → `derive --db {new_db}` → `load --db {new_db}`.

## 4. 병합·안전 규칙 (무손실 보장)

- **삭제 없음**: load는 추가 전용(합집합). 기존 LLM 발견·운영자 등록 단어를 절대 지우지 않는다. 재실행 안전(멱등).
- **source 태깅**: 시드 단어는 `source: operator`로 등록 → E5-3 감쇠(`prune_stale_synonyms`)가 **llm 소스만** 정리하므로 시드는 감쇠에서 보호된다.
- **column_values 충돌**: 동일 단어가 이미 등록돼 있으면 **기존 값 우선**(시드는 보충만).
- 사전 정리(단어 삭제)는 시드 도구 소관이 아니다 — E5-3 감쇠 또는 운영자 삭제 API(`remove_synonyms`) 사용.

## 5. 검증 방법

```bash
# 사전 규모 확인 (시딩 전후 비교)
redis-cli -a $REDIS_PASSWORD HLEN schema:polestar:synonyms

# 매칭 커버리지 정량 측정 (E5-1 잔여 미매칭율)
# — 골드셋 질의가 유사어 사전과 얼마나 연결되는지. E5-4(임베딩) 착수 게이트 지표.
python scripts/eval_text2sql.py --dry-run   # 골드셋 자체 검증
# (E1 하네스 A/B: --ab synonym_fuzzy 축으로 EX 영향도 측정 가능)
```

**측정 기록 (2026-07-15, 로컬 샌드박스 polestar 26건 골드셋)**:

| 지표 | 시딩 전 | 시딩 후 |
|------|--------|--------|
| 사전 규모(column keys) | 71 | 75(+시드 9키 병합) |
| 질의 수준 유사어 히트(정확) | 12/26 | **22/26** |
| 질의 수준 히트(퍼지 포함) | 16/26 | **26/26** |
| **질의 수준 잔여 미매칭** | **38.5%** | **0%** |
| 토큰 수준 잔여율 | 47.1% | 36.8% |

→ **E5-4(임베딩 의미 검색) 착수 근거가 현 골드셋 기준 소멸** — 잔여 토큰은 조사 결합형(리스트를·수를 — 토크나이저 한계)과 서버 고유명(sv-web-001 — 사전이 아닌 E5-2 값 검색 영역)뿐. E5-4 판단은 실사용 질의 로그 기준 재측정 후로 유지.

## 6. 한계·후속

- **derive 커버리지는 시맨틱 모델 선언에 종속** — 모델에 없는 어휘(예: "알람"이라는 단독 표면어)는 생성되지 않는다. 어휘 확장은 시맨틱 모델 aliases에 추가(§3.2). dimension 카탈로그 확장은 사람 승인 루프(D-012)로 점진 진행.
- 서버 고유명·IP 등 **인스턴스 값**은 사전이 아니라 값 검색(E5-2 value_index)의 영역.
- 시드 파일과 시맨틱 모델의 불일치 검출: `derived_from.sha256`이 원천 지문 — CI에서 `derive --db all` 후 `git diff --exit-code config/synonym_seeds/`로 드리프트 검사 가능(선택).
