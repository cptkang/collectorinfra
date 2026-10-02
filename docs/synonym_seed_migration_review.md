# Redis 유사어 시딩 — 별도 파일 정리·마이그레이션 방안 검토 (2026-07-15)

> 요청: "Redis에 등록할 내용을 마이그레이션할 수 있도록 별도 파일로 정리하여 재활용하는 방법 검토"
> 배경: E1 측정에서 E5-1 잔여 미매칭 38.5%의 실제 원인 = **성능지표·알람 동의어가 Redis 사전에 부재**
> (최다 잔여 토큰: 메모리 6·사용률 3·디스크·알람·severity·비정상). E5-4(임베딩) 착수 판단은 시딩 후 재측정으로 보류(권고 유지).

## 1. 현행 자산 실측 (재사용 가능한 것)

| 자산 | 현행 능력 | 한계 |
|------|-----------|------|
| `src/schema_cache/synonym_loader.py` | YAML/JSON → Redis 로드(`synonyms:global`·`:resource_types`·`:eav_names`), `load_auto`/`check_and_reload`(변경 감지 재로드), **`export_to_yaml/json`(역방향 내보내기)** | **per-DB 사전(`schema:{db}:synonyms`) 미취급** — 로드도 export도 안 됨 |
| `config/global_synonyms.yaml` | 시드 파일 전례(3계층: columns/resource_type_values/eav_name_values, 버전 필드, "수정 후 로더 재실행" 운영 규약) | 글로벌 계층만. bare 컬럼명 키(테이블 무관) |
| `config/semantic_models/{db_id}.yaml` | **누락 동의어의 원천 보유** — 패턴 A dimensions `aliases`(메모리 용량·논리코어…), 패턴 B measures `aliases`(**CPU 사용률·메모리 사용률·메모리·파일시스템**…), 패턴 C `severity_map`(**심각/경고/주의/해소**) + 알람 dimensions | Redis와 미동기 — E5-1 매칭 표면에 안 들어감 |
| E5-3 거버넌스(금일 구현) | 유사어 메타(source/usage_count/last_used/confidence), `prune_stale_synonyms`가 **source="llm"만 감쇠, operator·레거시 보존** | — (시드 보호에 그대로 활용 가능) |

**핵심 격차**: 텍스트 질의의 실제 매칭 지점(`schema_analyzer._synonym_tables_matching_query`, D-051 게이트)은 **per-DB 사전만** 읽는다(실측: 글로벌 109키는 이 게이트에 미참여). 그런데 기존 로더·시드 파일·export는 전부 글로벌 계층만 다룬다. → **"별도 파일 + 로더" 골격은 이미 있고, per-DB 계층으로의 확장이 빠져 있는 것**이 문제의 전부다.

## 2. 제안 설계 — "생성(derive) + 시드 파일(아티팩트) + 로더 확장 + export 확장"

```
  [단일 출처]                    [마이그레이션 아티팩트]              [Redis]
config/semantic_models/*.yaml ──derive──► config/synonym_seeds/     ──load──► schema:{db}:synonyms
config/db_profiles/*.yaml                  {db_id}.yaml (버전·출처 태깅)        synonyms:eav_names 등
(known_attributes synonyms)                     ▲                                   │
                                                └──────── export(운영 누적분) ◄─────┘
```

### 2.1 시드 파일 (`config/synonym_seeds/{db_id}.yaml`) — 마이그레이션·재활용 단위
- **생성 방식**: 수기 작성이 아니라 `scripts/derive_synonym_seeds.py`가 시맨틱 모델·프로필에서 **결정적으로 생성**(단일 출처 원칙 D-067 — 사전을 손으로 이중 관리하지 않음). 생성물은 git 커밋 → 리뷰·버전 관리·폐쇄망 반입이 그대로 가능.
- **스키마(안)**:
  ```yaml
  version: "1.0"
  db_id: polestar_cm_gp
  derived_from:            # 재현성 — 출처 파일과 지문
    - {file: config/semantic_models/polestar_cm_gp.yaml, sha256: "..."}
  source_tag: operator     # E5-3 감쇠 보호(운영자 승인 취급 — 시맨틱 모델은 사람이 작성한 config)
  column_synonyms:         # per-DB 사전(schema:{db}:synonyms) 로드 대상 — E5-1 게이트 참여
    polestar.cmm_resource.hostname: [Hostname, 호스트명, 호스트네임]
    polestar.cmm_metric_stat_m.avg_val: [CPU 사용률, 메모리 사용률, 사용률, 파일시스템 사용률]
    polestar.cmm_alarm.alarmseverity: [심각도, severity, 알람 등급]
    ...
  eav_names:               # synonyms:eav_names 병합 대상
    TotalSize: [메모리, 메모리크기, 메모리용량, 메모리 용량]
    ...
  column_values:           # synonyms:column_values / 값 검색(E5-2) 병합 대상
    cmm_alarm.alarmseverity: {"심각": 3, "경고": 2, "주의": 1, "해소": 0}   # severity_map 승격
  ```
- **매핑 규칙(derive 로직의 핵심, 결정적)**:
  | 시맨틱 모델 원천 | 시드 대상 키 |
  |---|---|
  | 패턴 A `source: direct` dimension | `{schema}.cmm_resource.{column}` |
  | 패턴 A `source: eav` dimension | `eav_names[{attribute}]` + `{schema}.core_config_prop.name`(게이트용) |
  | 패턴 B measures aliases | `{schema}.cmm_metric_stat_[h,d,m].avg_val`(3키 — 사용률 질의가 통계 테이블을 게이트에 올리도록) |
  | 패턴 C dimensions·`severity_map` | `{schema}.cmm_alarm.{column}` / `cmm_alarm_def.name` + column_values |
  | 프로필 `known_attributes[].synonyms` | eav_names 병합(중복 시 합집합) |

### 2.2 로더 확장 (`synonym_loader.py`)
- `load_seed_file(db_id, path)` 추가: `column_synonyms` → `save_synonyms(db_id, ..., source="operator")`(기존 cache_manager API 재사용), eav/values → 기존 병합 경로.
- **병합 정책**: 단어 합집합(기존 LLM 발견·운영자 등록분 **절대 삭제 안 함**), 동일 단어 재등록 시 메타 source 상향(llm→operator)만 허용. E5-3 충돌 우선순위(`rank_synonym_candidates`)와 정합 — 시드는 operator 소스라 충돌 시 우선.
- 멱등: 시드 파일 sha256을 Redis 메타에 기록, `check_and_reload` 패턴 재사용(변경 시에만 재로드).

### 2.3 export 확장 — 운영 누적분의 역방향 마이그레이션
- 현행 export는 글로벌 계층만 → **per-DB 사전 + E5-3 메타 포함 export** 추가(`export_seed_file(db_id)`).
- 운영 중 LLM 발견→사람 승인으로 누적된 사전을 파일로 내려 다른 환경(폐쇄망 프로덕션, 신규 DB 편입)에 반입하는 왕복이 완성됨: **seed(derive) = 초기 반입, export = 운영 누적분 이관**.

## 3. 기대 효과 (측정 근거)
- 잔여 미매칭 상위 토큰 전부가 시맨틱 모델 aliases·severity_map에 이미 존재함을 실측 확인(패턴 B: "메모리 사용률"·"메모리"·"CPU 사용률", 패턴 A: "메모리 용량", 패턴 C: "심각"). → 시딩만으로 질의 수준 잔여 38.5%의 상당 부분 해소 예상. **시딩 후 `measure_e51_residual.py` 재실행으로 정량 확인 → E5-4 착수 여부 판단**(게이트 유지).

## 4. 리스크·통제
| 리스크 | 통제 |
|--------|------|
| 사전 비대 → 프롬프트 토큰↑ (D-051) | supplement 상한 config(15) 유지 + E5-3 선별. 시딩 후 E1로 EX·토큰 재측정 |
| bare 공통 컬럼명 대량 매칭(Known Mistakes 2026-06-11) | 시드는 **table.column 정규화 키만** 생성, 2글자 미만·빈 단어 제외(E5-1 가드 재사용) |
| 이중 출처(수기 시드 편집) | 시드 파일 상단에 "생성물 — 직접 편집 금지, semantic_models 수정 후 derive 재실행" 명시. 수기 추가는 semantic model aliases에 하도록 유도 |
| 시딩이 기존 등록분 훼손 | 합집합 병합·삭제 금지·prune은 llm+stale만(기구현 규칙 그대로) |
| gp/yd/b0 동형 전파 누락(Known Mistakes 2026-07-09) | derive가 **전 DB 프로필을 일괄 생성** — 단일 DB만 갱신되는 실수 구조적 차단 |

## 5. 작업량 추정
- `scripts/derive_synonym_seeds.py`(신규, 결정적 파서·생성기) + `synonym_loader.py` 확장(load/export per-DB) + 시드 파일 4종 생성 + 단위 테스트(병합·멱등·감쇠 보호) ≈ **소규모(파일 3~4개)**. 인프라 변경 없음(Redis 스키마 기존 그대로), 전 기능 옵트인(로더는 명시 실행).

## 6. 결론
- **타당** — 프로젝트에 이미 "시드 파일 + 로더 + export" 전례(`global_synonyms.yaml`/`synonym_loader.py`)가 있고, 빠진 것은 per-DB 계층과 시맨틱 모델 파생뿐이라 **기존 패턴의 확장으로 저비용 구현 가능**. 시드 파일이 곧 마이그레이션 아티팩트가 되어 폐쇄망 반입·환경 간 이관·신규 DB 편입에 재활용된다. E5-3 거버넌스(source 태깅·감쇠 보호)와도 자연 정합.
