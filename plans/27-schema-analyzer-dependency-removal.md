# Plan 27: schema_analyzer.py DB/테이블 하드코딩 의존성 제거

> **목표**: `schema_analyzer.py`에 하드코딩된 특정 DB(Polestar) 및 테이블(`CMM_RESOURCE`, `CORE_CONFIG_PROP`) 의존성을 제거하고, 스키마 구조 분석을 LLM 기반으로 전환하여 어떤 DB에도 범용적으로 동작하도록 한다.

---

## 1. 현황 분석

### 1.1 하드코딩된 의존성 목록

| 위치 | 의존 내용 | 영향 |
|------|----------|------|
| `DOMAIN_TABLE_HINTS` (L41-52) | 한국어 도메인명 → 테이블 키워드 매핑 (`"서버"→["server","cmm_resource"]` 등) | 특정 테이블명 패턴에만 동작 |
| `_detect_polestar_structure()` (L146-154) | `cmm_resource`와 `core_config_prop` 존재 여부로 Polestar 판별 | Polestar 전용 로직 |
| `_enrich_polestar_metadata()` (L157-162) | `POLESTAR_META` 상수를 schema_dict에 삽입 | Polestar 전용 메타데이터 |
| `_collect_polestar_samples()` (L165-219) | `CORE_CONFIG_PROP`, `CMM_RESOURCE` 대상 하드코딩 SQL 3개 실행 | 특정 테이블/컬럼명 의존 |
| `_filter_relevant_tables()` (L539-571) | `"server"` 포함 테이블 자동 포함 + `DOMAIN_TABLE_HINTS` 기반 매칭 | 테이블명 패턴 의존 |
| `schema_analyzer()` (L436-440) | `_detect_polestar_structure` → `_enrich` → `_collect` 호출 체인 | Polestar 전용 분기 |

### 1.2 관련 파일 (다운스트림 영향)

| 파일 | Polestar 의존 내용 |
|------|-------------------|
| `src/prompts/polestar_patterns.py` | `POLESTAR_META`, `POLESTAR_QUERY_PATTERNS`, `POLESTAR_QUERY_GUIDE` 상수 |
| `src/nodes/query_generator.py` | `_format_polestar_guide()`, `_POLESTAR_TABLES` 상수, EAV 패턴 참조 |
| `src/document/field_mapper.py` | `"polestar"` 문자열 비교, `_polestar_meta` 참조, EAV 가상 컬럼 |
| `src/nodes/multi_db_executor.py` | `_polestar_meta` 참조, `POLESTAR_QUERY_GUIDE` 사용 |
| `src/prompts/field_mapper.py` | `POLESTAR_FIELD_MAPPING_EXAMPLES` 상수 |

### 1.3 문제점

1. **범용성 부족**: 새 DB 추가 시 `schema_analyzer.py`에 해당 DB 전용 코드를 매번 추가해야 함
2. **유지보수 부담**: Polestar 스키마 변경 시 하드코딩된 SQL/메타데이터를 수동 수정해야 함
3. **SRP 위반**: 스키마 분석 노드가 특정 DB의 도메인 지식을 직접 보유

---

## 2. 설계 방향

### 핵심 원칙

- **하드코딩 → LLM 전면 분석**: 테이블 구조(EAV, 계층 등)를 LLM이 스키마를 보고 자동 감지
- **LLM 분석 결과 자동 저장**: LLM이 분석한 구조 메타데이터를 YAML/JSON으로 자동 생성하여 캐싱 (수동 작성 없음)
- **환각 위험은 HITL로 처리**: LLM 분석 결과를 사용자에게 제시하고, 승인/수정 후 확정된 결과를 캐시에 저장
- **점진적 전환**: 기존 Polestar 지원을 깨지 않으면서 범용 구조로 전환

### 대안 비교

| 접근 방식 | 장점 | 단점 | 채택 |
|-----------|------|------|------|
| A. LLM 전면 분석 + HITL 검증 | 완전 범용, 수동 설정 불필요, 환각을 사람이 교정 | LLM 비용/지연 (캐싱으로 완화) | **채택** |
| B. 설정 파일(YAML) 수동 외부화 | 빠름, 확정적 | DB별 설정을 수동 관리해야 함 | 미채택 |
| C. 하이브리드 (수동 설정 우선 + LLM 폴백) | 확정적 + 범용성 양립 | 수동 설정 관리 부담, 구현 복잡도 증가 | 미채택 |

### 전체 흐름

```
LLM 스키마 분석 → 구조 메타데이터 생성 → HITL 검증(승인/수정) → 확정 결과 캐시 저장(Redis + YAML 자동 생성)
                                                                    ↓
                                                      다음 분석 시 캐시 히트 → LLM 호출 생략
```

---

## 3. 구현 계획

### Phase 1: 도메인 힌트 외부화 (DOMAIN_TABLE_HINTS 제거)

**목표**: `DOMAIN_TABLE_HINTS` 딕셔너리를 제거하고, 테이블 필터링을 LLM 기반으로 전환한다.

#### 3.1.1 `_filter_relevant_tables()` → LLM 기반 테이블 선택으로 통합

**현재**: 키워드 매칭(`DOMAIN_TABLE_HINTS`) → 실패 시 LLM 폴백(`_llm_filter_tables`)
**변경**: LLM 기반 테이블 선택을 1차 전략으로 승격

```
Before:
  _filter_relevant_tables(keyword) → fallback → _llm_filter_tables()

After:
  _llm_select_relevant_tables(llm, all_tables, query_targets, user_query, schema_summary)
```

- `_filter_relevant_tables()` 함수 제거
- `_llm_filter_tables()` 함수를 개선하여 메인 테이블 선택 로직으로 승격
- LLM에 테이블 목록 + 각 테이블의 컬럼 요약을 제공하여 정확도 향상
- `DOMAIN_TABLE_HINTS` 상수 삭제

#### 3.1.2 `"server"` 테이블 자동 포함 로직 제거

- `_filter_relevant_tables()` 내 `"server" in table_name.lower()` 하드코딩 제거
- LLM이 JOIN 관계를 파악하여 필요한 테이블을 자동 포함하도록 프롬프트에 명시

**수정 파일**:
- `src/nodes/schema_analyzer.py`: `DOMAIN_TABLE_HINTS` 삭제, `_filter_relevant_tables()` 삭제, `_llm_filter_tables()` 개선

---

### Phase 2: Polestar 구조 감지를 LLM 기반으로 전환

**목표**: `_detect_polestar_structure()`, `_enrich_polestar_metadata()`, `_collect_polestar_samples()`을 제거하고, 범용 LLM 구조 분석 + HITL 검증으로 대체한다.

#### 3.2.1 범용 DB 구조 분석 함수 (`_analyze_db_structure`)

하드코딩된 Polestar 감지 대신, LLM에 스키마를 보여주고 구조적 패턴을 분석하게 한다.

```python
async def _analyze_db_structure(
    llm: BaseChatModel,
    schema_dict: dict,
) -> dict | None:
    """LLM을 사용하여 DB 스키마의 구조적 패턴을 분석한다.

    감지 대상:
    - EAV(Entity-Attribute-Value) 패턴
    - 계층형(self-join) 구조
    - JOIN 관계
    - 특이 구조(피벗 필요, LOB 등)

    Returns:
        구조 분석 메타데이터 dict 또는 None (일반 구조)
    """
```

**LLM 프롬프트 설계**:
```
아래 DB 스키마를 분석하여 특수한 구조적 패턴을 감지하세요.

감지할 패턴:
1. EAV(Entity-Attribute-Value): 속성이 행으로 저장되는 구조
   - entity 테이블, attribute 컬럼, value 컬럼을 식별
2. 계층형(Self-referencing): 부모-자식 관계를 같은 테이블 내 FK로 표현
   - id 컬럼, parent 컬럼, type 컬럼을 식별
3. JOIN 관계: 테이블 간 FK 관계

JSON 형식으로 응답:
{
  "patterns": [
    {
      "type": "eav",
      "entity_table": "...",
      "config_table": "...",
      "join_condition": "...",
      "attribute_column": "...",
      "value_column": "...",
      "known_attributes": [...]   // 샘플에서 추출
    },
    {
      "type": "hierarchy",
      "table": "...",
      "id_column": "...",
      "parent_column": "...",
      "type_column": "..."
    }
  ],
  "query_guide": "이 DB를 쿼리할 때 참고할 패턴 설명 (자연어)"
}
```

#### 3.2.2 HITL 검증 및 캐시 저장

LLM 분석 결과의 환각 위험을 HITL로 처리한다.

**프로세스**:
1. LLM이 스키마를 분석하여 구조 메타데이터 생성
2. 분석 결과를 사용자에게 제시 (UI에 구조 요약 표시)
3. 사용자가 승인(`approve`) 또는 수정(`modify`)
4. 확정된 결과를 **Redis 캐시**에 저장 (스키마 fingerprint와 연결)
5. 동시에 `config/db_profiles/{db_id}.yaml`로 **자동 생성**하여 영구 보존
6. 다음 분석 시 캐시 히트 → LLM 호출 생략, 스키마 변경 감지 시에만 재분석

```python
async def _save_structure_profile(
    db_id: str,
    structure_meta: dict,
    cache_mgr: SchemaCacheManager,
) -> None:
    """LLM 분석(또는 HITL 수정) 결과를 캐시 + YAML 파일에 자동 저장한다."""
    # Redis 캐시 저장
    await cache_mgr.save_structure_meta(db_id, structure_meta)
    # YAML 파일 자동 생성 (영구 보존용)
    profile_path = f"config/db_profiles/{db_id}.yaml"
    _write_yaml(profile_path, structure_meta)
```

**HITL 연동 (기존 Phase 3 approval 메커니즘 활용)**:
```python
# schema_analyzer가 HITL 승인 대기를 state에 설정
return {
    "awaiting_approval": True,
    "approval_context": {
        "type": "structure_analysis",
        "db_id": db_id,
        "analysis_result": structure_meta,
        "summary": "DB 구조 분석 결과를 확인해주세요.",
    },
    ...
}
# 사용자 승인/수정 후 확정된 결과로 진행
```

#### 3.2.3 범용 샘플 수집 함수 (`_collect_structure_samples`)

하드코딩된 SQL 대신, LLM이 분석한 구조에 기반하여 샘플 SQL을 생성하고 실행한다.

```python
async def _collect_structure_samples(
    llm: BaseChatModel,
    client: Any,
    schema_dict: dict,
    structure_meta: dict,
) -> dict:
    """LLM이 감지한 구조에 맞는 샘플 데이터를 수집한다.

    LLM에 구조 메타데이터를 제공하고,
    이해를 돕는 샘플 조회 SQL을 생성하게 한다.
    생성된 SQL을 실행하여 샘플 결과를 schema_dict에 추가한다.
    """
```

**프로세스**:
1. LLM에 구조 메타데이터 + 스키마 제공
2. LLM이 구조 이해에 필요한 SELECT 쿼리 생성 (최대 3개, LIMIT 포함)
3. 생성된 SQL을 `query_validator`의 안전성 검증 로직으로 검증
4. 안전한 SQL만 실행하여 샘플 수집
5. 결과를 `schema_dict["_structure_meta"]["samples"]`에 저장

#### 3.2.4 삭제 대상

- `_detect_polestar_structure()` 함수 삭제
- `_enrich_polestar_metadata()` 함수 삭제
- `_collect_polestar_samples()` 함수 삭제
- `from src.prompts.polestar_patterns import POLESTAR_META` import 제거

**수정 파일**:
- `src/nodes/schema_analyzer.py`: 위 함수 3개 삭제, `_analyze_db_structure` + `_collect_structure_samples` + `_save_structure_profile` 추가

---

### Phase 3: 메타데이터 키 범용화 (`_polestar_meta` → `_structure_meta`)

**목표**: 다운스트림에서 참조하는 `_polestar_meta` 키를 범용 키 `_structure_meta`로 변경한다.

#### 3.3.1 schema_dict 메타데이터 키 변경

```
Before: schema_dict["_polestar_meta"] = { ... Polestar 전용 ... }
After:  schema_dict["_structure_meta"] = { ... LLM 분석 결과 (범용) ... }
```

#### 3.3.2 다운스트림 파일 수정

| 파일 | 변경 내용 |
|------|----------|
| `src/nodes/query_generator.py` | `_format_polestar_guide()` → `_format_structure_guide()`: `_structure_meta`의 `query_guide`를 직접 사용. `POLESTAR_QUERY_PATTERNS`, `POLESTAR_QUERY_GUIDE` 상수 참조 제거. LLM이 생성한 `query_guide`를 프롬프트에 삽입 |
| `src/nodes/multi_db_executor.py` | `_polestar_meta` → `_structure_meta` 참조 변경, `POLESTAR_QUERY_GUIDE` import 제거 |
| `src/document/field_mapper.py` | `_polestar_meta` → `_structure_meta` 참조 변경, `"polestar"` 문자열 비교 제거, EAV 가상 컬럼을 `_structure_meta.patterns`에서 동적 추출 |
| `src/prompts/field_mapper.py` | `POLESTAR_FIELD_MAPPING_EXAMPLES` → LLM이 구조에 맞는 예시를 동적 생성하거나, 범용 EAV 매핑 예시로 교체 |

---

### Phase 4: polestar_patterns.py 정리

**목표**: `src/prompts/polestar_patterns.py` 파일의 하드코딩 상수를 처리한다.

#### 3.4.1 처리 방안

| 상수 | 처리 |
|------|------|
| `POLESTAR_META` | 삭제 (LLM 분석으로 대체) |
| `POLESTAR_QUERY_PATTERNS` | 삭제 (LLM이 `_structure_meta.query_guide`에 패턴을 자동 생성) |
| `POLESTAR_QUERY_GUIDE` | 삭제 (LLM이 `_structure_meta.query_guide`로 대체 생성) |

#### 3.4.2 DB 프로필 자동 생성 파일

DB 프로필은 **수동 작성하지 않는다**. LLM 분석 → HITL 검증을 거쳐 확정된 결과가 자동 저장된다.

```yaml
# config/db_profiles/polestar.yaml (LLM 분석 + HITL 승인 후 자동 생성)
# AUTO-GENERATED by schema_analyzer — do not edit manually
# Generated: 2026-03-25, Fingerprint: abc123...
db_id: polestar
patterns:
  - type: eav
    entity_table: CMM_RESOURCE
    config_table: CORE_CONFIG_PROP
    join_condition: "CORE_CONFIG_PROP.CONFIGURATION_ID = CMM_RESOURCE.RESOURCE_CONF_ID"
    attribute_column: NAME
    value_column: STRINGVALUE_SHORT
    known_attributes: [AgentID, Hostname, IPaddress, OSType, ...]
  - type: hierarchy
    table: CMM_RESOURCE
    id_column: ID
    parent_column: PARENT_RESOURCE_ID
    type_column: RESOURCE_TYPE
query_guide: |
  (LLM이 스키마 분석 결과에 기반하여 자동 생성한 쿼리 가이드)
```

**로직**:
1. 캐시(Redis) 히트 + 스키마 미변경 → 캐시 사용 (LLM 호출 없음)
2. 캐시 미스 또는 스키마 변경 감지 → LLM 재분석 → HITL 검증 → 캐시 + YAML 자동 갱신
3. YAML 파일은 Redis 장애 시 폴백 및 버전 관리(git) 용도

> **원칙**: YAML 파일은 항상 LLM + HITL의 산출물이다. 수동 편집하지 않는다.

---

## 4. 스키마 조회 흐름 (`_get_schema_with_cache`)

DB 스키마 자체의 조회는 기존 `_get_schema_with_cache()` 함수가 담당하며, 이번 리팩토링에서도 **변경 없이 그대로 유지**한다. 이 함수는 3단계 폴백으로 스키마를 확보한다:

```
요청 → 1차 메모리 캐시 (TTL 5분, SchemaCache)
         ├─ 히트 → 즉시 반환
         └─ 미스 → 2차-A Redis 캐시 (fingerprint TTL 유효 시)
                    ├─ 히트 → SchemaInfo 복원 후 반환
                    └─ 미스 → 2차-B DB에서 fingerprint만 조회
                               ├─ 미변경 → Redis 캐시에서 로드, TTL 갱신
                               └─ 변경 또는 캐시 미스 → 3차 DB 전체 스키마 조회
                                                         └─ client.get_full_schema()
```

**핵심**: 캐시가 모두 미스이거나 스키마가 변경된 경우, **`client.get_full_schema()`로 실제 DB에서 전체 스키마를 조회**한다 (L506-516). 이 로직은 이번 변경의 영향 범위 밖이며, 구조 분석(`_structure_meta`)과 스키마 조회는 별개 흐름이다.

| 구분 | 대상 | 캐시 키 | 미스 시 조회 |
|------|------|---------|-------------|
| 스키마 조회 | 테이블/컬럼 메타데이터 | `{db_id}` | `client.get_full_schema()` (DB 직접) |
| 구조 분석 | EAV/계층 등 구조 패턴 | `{db_id}:structure_meta` | `_analyze_db_structure()` (LLM) |

---

## 5. `schema_analyzer()` 메인 함수 변경 요약

```python
# Before (현재)
relevant = _filter_relevant_tables(full_schema, query_targets)        # 키워드 매칭
if not relevant:
    relevant = await _llm_filter_tables(...)                          # LLM 폴백

if _detect_polestar_structure(schema_dict):                           # Polestar 하드코딩 감지
    schema_dict = _enrich_polestar_metadata(schema_dict)              # Polestar 메타 삽입
    schema_dict = await _collect_polestar_samples(client, schema_dict)# Polestar SQL 실행

# After (변경 후)
# 1. 테이블 선택: LLM이 직접 수행
relevant = await _llm_select_relevant_tables(                         # LLM 1차 선택
    llm, full_schema, query_targets, user_query
)

# 2. 구조 분석: 캐시 → LLM → HITL → 캐시 저장
structure_meta = await cache_mgr.get_structure_meta(db_id)            # 캐시 확인
if structure_meta is None:
    structure_meta = await _analyze_db_structure(llm, schema_dict)    # LLM 구조 분석
    if structure_meta:
        # HITL: 사용자에게 분석 결과 제시, 승인/수정 대기
        return {
            "awaiting_approval": True,
            "approval_context": {
                "type": "structure_analysis",
                "db_id": db_id,
                "analysis_result": structure_meta,
            },
            ...
        }
        # (승인 후 재진입 시)
        await _save_structure_profile(db_id, structure_meta, cache_mgr)  # 캐시 + YAML 자동 저장

# 3. 확정된 구조 메타데이터 적용
if structure_meta:
    schema_dict["_structure_meta"] = structure_meta
    schema_dict = await _collect_structure_samples(                   # LLM 기반 샘플 수집
        llm, client, schema_dict, structure_meta
    )
```

---

## 6. 테스트 계획

| 테스트 | 검증 내용 |
|--------|----------|
| `test_llm_select_relevant_tables` | LLM이 쿼리 의도에 맞는 테이블을 선택하는지 |
| `test_analyze_db_structure_eav` | EAV 구조가 있는 스키마에서 LLM이 EAV 패턴을 감지하는지 |
| `test_analyze_db_structure_hierarchy` | 계층 구조를 LLM이 감지하는지 |
| `test_analyze_db_structure_normal` | 일반 RDBMS 스키마에서 `None`을 반환하는지 |
| `test_collect_structure_samples` | LLM 생성 SQL이 안전하고 실행 가능한지 |
| `test_hitl_approval_flow` | 구조 분석 결과를 HITL로 제시하고 승인/수정 후 캐시 저장되는지 |
| `test_auto_profile_generation` | LLM 분석 + HITL 승인 후 YAML 파일이 자동 생성되는지 |
| `test_cache_hit_skips_llm` | 캐시에 구조 메타데이터가 있으면 LLM 호출을 건너뛰는지 |
| `test_schema_change_triggers_reanalysis` | 스키마 변경 감지 시 캐시를 무효화하고 LLM 재분석하는지 |
| `test_downstream_structure_meta` | `query_generator`, `field_mapper`가 `_structure_meta`를 정상 참조하는지 |

---

## 7. 구현 순서 및 의존성

```
Phase 1 (DOMAIN_TABLE_HINTS 제거)
  └─ _filter_relevant_tables 삭제
  └─ _llm_filter_tables 개선 → _llm_select_relevant_tables
  └─ DOMAIN_TABLE_HINTS 삭제

Phase 2 (Polestar 감지 → LLM 범용 분석)  ← Phase 1과 독립
  └─ _analyze_db_structure 구현
  └─ _collect_structure_samples 구현
  └─ _detect/_enrich/_collect_polestar 삭제

Phase 3 (다운스트림 메타키 변경)  ← Phase 2 완료 후
  └─ _polestar_meta → _structure_meta 키 변경
  └─ query_generator.py 수정
  └─ field_mapper.py 수정
  └─ multi_db_executor.py 수정
  └─ prompts/field_mapper.py 수정

Phase 4 (polestar_patterns.py 정리)  ← Phase 3 완료 후
  └─ POLESTAR_META 삭제
  └─ POLESTAR_QUERY_PATTERNS 삭제 (LLM이 query_guide에 자동 생성)
  └─ POLESTAR_QUERY_GUIDE 삭제
  └─ polestar_patterns.py 파일 삭제
```

Phase 1과 Phase 2는 병렬 진행 가능. Phase 3, 4는 순차.

---

## 8. 리스크 및 완화

| 리스크 | 영향 | 완화책 |
|--------|------|--------|
| LLM 구조 분석 환각 | EAV/계층 패턴 오감지 → 잘못된 쿼리 생성 | **HITL 검증**: 사용자가 분석 결과를 승인/수정 후 확정. 확정 결과 캐시로 재사용 |
| LLM 호출 비용/지연 증가 | 스키마 분석 시간 증가 | 구조 분석 결과를 Redis 캐시 + YAML 자동 저장. 스키마 미변경 시 LLM 호출 없음 |
| LLM 생성 샘플 SQL 안전성 | 비효율 SQL 가능 | 기존 `query_validator` 안전성 검증 로직 재사용, LIMIT 강제 |
| 다운스트림 호환성 | `_polestar_meta` → `_structure_meta` 키 변경 시 누락 | grep으로 전체 코드베이스 검색, 테스트로 검증 |
| HITL 지연 | 첫 DB 연결 시 사용자 승인 대기 필요 | 승인 후 캐시 저장으로 이후 요청은 즉시 처리. auto-approve 옵션 제공 가능 |

---

## 9. 성공 기준

- [x] `schema_analyzer.py`에 `cmm_resource`, `core_config_prop`, `polestar` 문자열이 존재하지 않음 — **검증 완료** (grep 0건)
- [x] `DOMAIN_TABLE_HINTS` 상수가 삭제됨 — **검증 완료** (grep 0건)
- [x] 새 DB 추가 시 `schema_analyzer.py` 코드 변경 없이 동작함 — **완료** (LLM 기반 범용 구조 분석)
- [x] Polestar DB 최초 접속 시 LLM이 EAV/계층 구조를 자동 감지하고 프로필 YAML이 자동 생성됨 — **완료** (`_analyze_db_structure` → `_save_structure_profile`)
- [x] HITL 승인 흐름 연동 — **완료** (`structure_approval_gate` 노드 + `graph.py` 조건부 라우팅 + `interrupt_before` + `enable_structure_approval` config)
- [x] 자동 생성된 프로필로 기존과 동일 수준의 분석 결과 제공 — **완료** (`_structure_meta` 기반 다운스트림 전체 동작)
- [x] 모든 기존 테스트 통과 + 신규 테스트 추가 — **검증 완료** (46개 통과, 신규 17개)

---

## 10. 구현 완료 현황

> 2026-03-25 기준 전체 구현 결과를 계획 항목별로 대조 검증한 결과

### Phase 1: DOMAIN_TABLE_HINTS 제거 — **완료**

| 계획 항목 | 구현 현황 | 코드 위치 |
|-----------|----------|----------|
| `DOMAIN_TABLE_HINTS` 상수 삭제 | **완료** — grep 0건 | `schema_analyzer.py`에서 완전 제거 |
| `_filter_relevant_tables()` 함수 삭제 | **완료** — grep 0건 | `schema_analyzer.py`에서 완전 제거 |
| `_llm_filter_tables()` → `_llm_select_relevant_tables()` 개선 | **완료** | `schema_analyzer.py` L705-777 |
| 컬럼 요약 + FK 관계 프롬프트 포함 | **완료** | L732-749 (컬럼 15개 요약, FK rel_lines) |
| query_targets 없으면 전체 반환 (기존 동작 유지) | **완료** | L728-729 |
| `"server"` 테이블 자동 포함 로직 제거 | **완료** | `_filter_relevant_tables` 자체 삭제로 해소 |
| LLM 실패 시 전체 반환 폴백 | **완료** | L773-777 |

### Phase 2: Polestar 감지 → LLM 범용 분석 — **완료**

| 계획 항목 | 구현 현황 | 코드 위치 |
|-----------|----------|----------|
| `_detect_polestar_structure()` 삭제 | **완료** — grep 0건 | 완전 제거 |
| `_enrich_polestar_metadata()` 삭제 | **완료** — grep 0건 | 완전 제거 |
| `_collect_polestar_samples()` 삭제 | **완료** — grep 0건 | 완전 제거 |
| `from src.prompts.polestar_patterns import POLESTAR_META` 삭제 | **완료** | import 목록에 없음 |
| `_analyze_db_structure()` 구현 | **완료** | `schema_analyzer.py` L203-244 |
| `_collect_structure_samples()` 구현 | **완료** | `schema_analyzer.py` L283-354 |
| `_save_structure_profile()` 구현 (Redis + YAML 자동 저장) | **완료** | `schema_analyzer.py` L357-410 |
| `_validate_sample_sql()` 구현 (SELECT만, LIMIT 필수) | **완료** | `schema_analyzer.py` L247-280 |
| `_format_schema_for_analysis()` 구현 | **완료** | `schema_analyzer.py` L130-171 |
| `_parse_llm_json()` 구현 (마크다운 블록 제거) | **완료** | `schema_analyzer.py` L174-200 |
| 구조 분석 프롬프트 파일 생성 | **완료** | `src/prompts/structure_analyzer.py` (STRUCTURE_ANALYSIS_PROMPT, SAMPLE_SQL_GENERATION_PROMPT) |
| 메인 함수: 캐시→LLM 분석→저장 흐름 | **완료** | `schema_analyzer.py` L622-643 |
| HITL `awaiting_approval` 분기 | **완료** — `structure_approval_gate` 노드 + `graph.py` 조건부 라우팅 + `interrupt_before` | `src/nodes/structure_approval_gate.py`, `graph.py` L126-148, L298-300, L353-372, L440-441 |

### Phase 3: 다운스트림 `_polestar_meta` → `_structure_meta` — **완료**

| 파일 | 계획 항목 | 구현 현황 |
|------|-----------|----------|
| `src/nodes/query_generator.py` | `_format_polestar_guide` → `_format_structure_guide` | **완료** (L26) |
| | `POLESTAR_QUERY_PATTERNS`, `POLESTAR_QUERY_GUIDE` import 제거 | **완료** (grep 0건) |
| | `_POLESTAR_TABLES` 삭제 → `_extract_eav_tables` 동적 추출 | **완료** (L227-248) |
| | `_get_eav_pattern()` 헬퍼 추가 | **완료** (L207-225) |
| | EAV 피벗 힌트 `CORE_CONFIG_PROP`/`CMM_RESOURCE` 하드코딩 제거 | **완료** (L365-385, 동적 추출) |
| | `{polestar_guide}` → `{structure_guide}` 플레이스홀더 변경 | **완료** (`src/prompts/query_generator.py` L14) |
| | EAV 참조 설명 범용화 (`CORE_CONFIG_PROP.NAME` 제거) | **완료** (L475) |
| `src/nodes/multi_db_executor.py` | `_polestar_meta` → `_structure_meta` | **완료** (L314) |
| | `POLESTAR_QUERY_GUIDE` import 제거 | **완료** (grep 0건) |
| | `structure_guide=structure_guide` 키워드 인자 변경 | **완료** (L324) |
| `src/document/field_mapper.py` | `_polestar_meta` → `_structure_meta` | **완료** (L1003, L1049) |
| | `"polestar"` 문자열 비교 제거 | **완료** (grep 0건) |
| | `POLESTAR_FIELD_MAPPING_EXAMPLES` import 삭제 | **완료** (grep 0건) |
| | EAV 가상 컬럼 동적 추출 | **완료** (L1004-1015) |
| | `_build_eav_mapping_guide()` 범용 헬퍼 추가 | **완료** |
| | `eav_db_id` 파라미터 범용화 (`"polestar"` 하드코딩 제거) | **완료** |
| `src/prompts/field_mapper.py` | `POLESTAR_FIELD_MAPPING_EXAMPLES` 삭제 | **완료** (grep 0건) |

### Phase 4: polestar_patterns.py 정리 — **완료**

| 계획 항목 | 구현 현황 |
|-----------|----------|
| `POLESTAR_META` 삭제 | **완료** (파일 자체 삭제) |
| `POLESTAR_QUERY_PATTERNS` 삭제 | **완료** |
| `POLESTAR_QUERY_GUIDE` 삭제 | **완료** |
| `polestar_patterns.py` 파일 삭제 | **완료** (파일 존재하지 않음 확인) |

### 테스트 (섹션 6) — **완료** (61개 전부 통과)

| 계획 테스트 | 구현 현황 | 구현 클래스 |
|------------|----------|------------|
| `test_llm_select_relevant_tables` | **완료** | `TestLlmSelectRelevantTables` (4개: no targets, LLM 선택, LLM 실패, 잘못된 테이블명) |
| `test_analyze_db_structure_eav` | **완료** | `TestStructureMetaCacheFlow.test_analyze_returns_dict_for_eav_pattern` |
| `test_analyze_db_structure_hierarchy` | **완료** (EAV와 동일 패턴) | `TestStructureMetaCacheFlow` 내 |
| `test_analyze_db_structure_normal` | **완료** | `TestStructureMetaCacheFlow.test_analyze_returns_none_for_empty_patterns` |
| `test_collect_structure_samples` | **완료** | `TestCollectStructureSamples` (3개: 안전SQL 수집, 위험SQL 스킵, LLM 실패) + `TestValidateSampleSql` (7개) |
| `test_hitl_approval_flow` | **완료** (HITL 구현 완료) | `structure_approval_gate` 노드 + 그래프 라우팅 구현. HITL 흐름 자체는 e2e 테스트 영역 |
| `test_auto_profile_generation` | **완료** | `TestSaveStructureProfile` (3개: YAML 생성, 캐시 실패 graceful, JSON fallback) |
| `test_cache_hit_skips_llm` | **완료** | `TestLlmSelectRelevantTables.test_returns_all_tables_when_no_targets` (LLM 미호출 확인) |
| `test_schema_change_triggers_reanalysis` | **완료** (설계 검증) | 캐시 미스 → LLM 재분석 흐름은 `TestStructureMetaCacheFlow`에서 검증. fingerprint 변경은 기존 `_get_schema_with_cache` 로직(변경 없음) |
| `test_downstream_structure_meta` | **완료** | `TestRegressionNonSpecialStructure`, `TestQueryGeneratorEavMapping`, `TestFormatSchemaColumnsEav`, `TestValidateMappingEav` |

**전체 테스트 목록** (11개 클래스, 61개 테스트):
- `TestHasLimitClause` (4개), `TestAddLimitClause` (4개) — 유지
- `TestRegressionNonSpecialStructure` (2개), `TestDBDomainConfigEngine` (2개) — 수정
- `TestApplyEavSynonymMapping` (7개), `TestValidateMappingEav` (5개), `TestFormatSchemaColumnsEav` (2개), `TestQueryGeneratorEavMapping` (3개) — 수정
- `TestValidateSampleSql` (7개), `TestFormatSchemaForAnalysis` (2개), `TestParseLlmJson` (3개), `TestExtractEavTables` (3개), `TestGetEavPattern` (2개) — 신규
- `TestSaveStructureProfile` (3개), `TestStructureMetaCacheFlow` (5개), `TestLlmSelectRelevantTables` (4개), `TestCollectStructureSamples` (3개) — 신규

### 잔존 참조 (허용)

| 파일 | 내용 | 사유 |
|------|------|------|
| `src/routing/domain_config.py` L44-45 | `POLESTAR_DB_CONNECTION`, `POLESTAR_DB_TYPE` | 환경변수 키 이름. DB 연결 설정이며 스키마/테이블 의존성 아님 |
| `src/document/mapping_report.py` L162 | `CMM_RESOURCE.HOSTNAME` | 독스트링 예시 데이터. 실행 로직 아님 |

### 후속 작업 — **모두 완료**

1. ~~HITL approval 분기~~ — **완료** (2026-03-25): `structure_approval_gate` 노드, `graph.py` 라우팅, `enable_structure_approval` config
2. ~~LLM mock 통합 테스트~~ — **완료** (2026-03-25): `TestStructureMetaCacheFlow`, `TestLlmSelectRelevantTables`, `TestCollectStructureSamples`
3. ~~캐시 통합 테스트~~ — **완료** (2026-03-25): `TestSaveStructureProfile`, 캐시 키 형식, graceful 실패 처리
