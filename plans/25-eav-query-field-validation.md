# Plan 25: EAV 쿼리 생성 시 존재하지 않는 필드 참조 문제

- 상태: 검증 완료 / 수정 대기
- 작성일: 2026-03-25
- 관련: Polestar EAV 구조, field_mapper, query_generator, query_validator

---

## 1. 문제 현상

Polestar DB(polestar_pg) 대상으로 EAV 쿼리를 생성할 때, **실제 DB에 존재하지 않는 테이블과 컬럼**을 참조하는 SQL이 생성되어 실행 실패한다.

### 생성된 잘못된 SQL (실제 발생 사례)

```sql
SELECT
    s."id" AS "servers.id",
    s.hostname AS "servers.hostname",
    s.ip_address AS "servers.ip_address",   -- 존재하지 않는 컬럼
    s.os AS "servers.os",                   -- 존재하지 않는 컬럼
    s.location AS "servers.location",       -- 존재하지 않는 컬럼
    s.purpose AS "servers.purpose",         -- 존재하지 않는 컬럼
    s.cpu_cores AS "servers.cpu_cores",     -- 존재하지 않는 컬럼
    c.core_count AS "cpu_metrics.core_count", -- 존재하지 않는 테이블/컬럼
    c.usage_pct AS "cpu_metrics.usage_pct",   -- 존재하지 않는 테이블/컬럼
    MAX(CASE WHEN p.NAME = 'Vendor' THEN p.STRINGVALUE_SHORT END) AS "EAV.Vendor",
    MAX(CASE WHEN p.NAME = 'Model' THEN p.STRINGVALUE_SHORT END) AS "EAV.Model"
FROM servers s                             -- 존재하지 않는 테이블
LEFT JOIN cpu_metrics c ON s.id = c.server_id       -- 존재하지 않는 테이블
LEFT JOIN disk_metrics d ON s.id = d.server_id      -- 존재하지 않는 테이블
LEFT JOIN memory_metrics m ON s.id = m.server_id    -- 존재하지 않는 테이블
LEFT JOIN network_metrics n ON s.id = n.server_id   -- 존재하지 않는 테이블
LEFT JOIN CORE_CONFIG_PROP p ON s.id = p.CONFIGURATION_ID  -- JOIN 조건 틀림
GROUP BY ...
```

### Polestar DB 실제 스키마

| 테이블 | 설명 | 주요 컬럼 |
|--------|------|-----------|
| `polestar.cmm_resource` | 계층형 리소스 (59컬럼) | id, hostname, ipaddress, resource_type, parent_resource_id, resource_conf_id |
| `polestar.core_config_prop` | EAV 설정 (12컬럼) | id, name, stringvalue_short, configuration_id |

### 올바른 EAV JOIN 조건

```sql
CMM_RESOURCE r LEFT JOIN CORE_CONFIG_PROP p ON p.CONFIGURATION_ID = r.RESOURCE_CONF_ID
```

---

## 2. 근본 원인 분석

### 원인 요약

field_mapper가 다른 DB(infra_db)의 테이블에 매핑 → query_generator가 검증 없이 프롬프트에 삽입 → query_validator가 별칭 때문에 컬럼 검증 우회 → 잘못된 SQL이 실행됨.

### 문제 코드 상세 (5곳)

#### 문제 1: 컬럼 검증 우회 (별칭 미해석)

- **파일**: `src/nodes/query_validator.py:214-247`
- **함수**: `_validate_columns()`

```python
col_refs = re.findall(r"(\w+)\.(\w+)", sql)

for table_ref, col_ref in col_refs:
    if table_ref in available_columns:   # 별칭 's', 'c' 등은 매칭 불가
        if col_ref not in available_columns[table_ref] and col_ref != "*":
            errors.append(...)
```

- **문제**: SQL에서 `s.hostname`이면 `table_ref='s'`인데, `available_columns`에는 풀 테이블명(`servers`)만 있어서 검증이 스킵됨.
- **영향**: 모든 별칭 참조 컬럼이 검증을 우회.

#### 문제 2: 테이블 소속 DB 미확인

- **파일**: `src/nodes/query_validator.py:86-92`
- **함수**: `query_validator()`

```python
available_tables_lower = {t.lower() for t in available_tables}
unknown_tables = {t for t in referenced_tables if t.lower() not in available_tables_lower}
```

- **문제**: schema_info에 여러 DB의 테이블이 섞여 있으면, 현재 대상 DB에 없는 테이블도 유효로 판정.
- **영향**: `servers`, `cpu_metrics` 등이 schema_info에 포함되어 있으면 통과.

#### 문제 3: LLM 매핑이 잘못된 DB의 테이블 참조

- **파일**: `src/document/field_mapper.py:461-598`
- **함수**: `_apply_llm_mapping_with_synonyms()`

```python
for db_id in ordered_db_ids:
    descs = all_db_descriptions.get(db_id, {})
    # ... 모든 DB의 스키마를 LLM에 전달
```

- **문제**: LLM이 전체 DB 스키마를 보고 매핑하므로, Polestar 양식의 "호스트명"을 `servers.hostname`(infra_db)으로 매핑 가능.
- **영향**: column_mapping에 현재 대상 DB와 무관한 테이블.컬럼이 포함됨.
- **수정 방향**: LLM에 few-shot 프롬프트를 제공하여 Polestar DB의 올바른 테이블(`CMM_RESOURCE`, `CORE_CONFIG_PROP`)로 매핑하도록 유도. DB 범위를 코드로 제한하는 대신, 프롬프트 품질로 정확도를 확보한다.

#### 문제 4: column_mapping을 검증 없이 프롬프트에 삽입

- **파일**: `src/nodes/query_generator.py:276-310`
- **함수**: `_build_user_prompt()`

```python
if column_mapping:
    regular_entries = [
        (field, col) for field, col in column_mapping.items()
        if col and not col.startswith("EAV:")
    ]
    # ...
    parts.append(
        f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
        "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
    )
```

- **문제**: column_mapping의 테이블이 현재 schema_info에 존재하는지 대조하지 않음. `servers.hostname` 같은 매핑이 "반드시 포함" 지시로 전달됨.
- **영향**: LLM이 존재하지 않는 테이블/컬럼으로 SQL 생성.

#### 문제 5: EAV JOIN과 비-Polestar 매핑 충돌

- **파일**: `src/nodes/query_generator.py:299-309`
- **함수**: `_build_user_prompt()`

```python
if eav_entries:
    parts.append(
        "조인 조건: CMM_RESOURCE r LEFT JOIN CORE_CONFIG_PROP p "
        "ON p.CONFIGURATION_ID = r.RESOURCE_CONF_ID\n"
    )
```

- **문제**: EAV JOIN 가이드는 CMM_RESOURCE 기준이지만, column_mapping에 `servers.hostname` 등이 동시 존재하면 LLM이 `servers`와 `CORE_CONFIG_PROP`을 직접 JOIN 시도.
- **영향**: `s.id = p.CONFIGURATION_ID` 같은 잘못된 JOIN 생성.

---

## 3. 데이터 흐름 (오류 전파 경로)

```
field_mapper (문제3)
  └─ column_mapping: {"호스트명": "servers.hostname", "벤더": "EAV:Vendor"}
      │
      ▼
query_generator (문제4, 문제5)
  └─ 프롬프트: "servers.hostname을 반드시 SELECT에 포함 + EAV JOIN은 CMM_RESOURCE 기준"
      │
      ▼
LLM 혼란 → servers + CMM_RESOURCE + CORE_CONFIG_PROP 혼합 SQL 생성
      │
      ▼
query_validator (문제1, 문제2)
  └─ 별칭 때문에 컬럼 검증 우회, 테이블도 schema에 존재하므로 통과
      │
      ▼
query_executor → SQL 실행 실패 (테이블/컬럼 미존재)
```

---

## 4. 수정 방안

### 수정 A: query_generator에서 column_mapping 필터링 (최우선)

- **위치**: `src/nodes/query_generator.py` `_build_user_prompt()`
- **내용**: column_mapping의 각 항목에서 테이블명을 추출하여, 현재 schema_info에 존재하는지 확인. 존재하지 않으면 해당 매핑을 제외하고 경고 로그 출력.

```python
# column_mapping 필터링
tables_in_schema = set(schema_info.get("tables", {}).keys())
tables_lower = {t.lower() for t in tables_in_schema}

filtered_mapping = {}
for field, col in column_mapping.items():
    if col and not col.startswith("EAV:"):
        table_part = col.split(".")[0] if "." in col else ""
        if table_part.lower() in tables_lower:
            filtered_mapping[field] = col
        else:
            logger.warning("column_mapping 필터링: '%s' -> '%s' (테이블 '%s' 미존재)", field, col, table_part)
    else:
        filtered_mapping[field] = col
```

### 수정 B: query_validator 별칭 해석 추가

- **위치**: `src/nodes/query_validator.py` `_validate_columns()`
- **내용**: SQL에서 별칭-테이블 매핑을 추출하여 (`FROM servers s` → `s → servers`), 별칭으로 참조된 컬럼도 실제 테이블 기준으로 검증.

```python
# 별칭 매핑 구축
alias_map = _extract_alias_map(sql)  # {'s': 'servers', 'c': 'cpu_metrics', ...}

for table_ref, col_ref in col_refs:
    actual_table = alias_map.get(table_ref, table_ref)
    if actual_table in available_columns:
        if col_ref not in available_columns[actual_table] and col_ref != "*":
            errors.append(...)
```

### 수정 C: field_mapper LLM에 few-shot 프롬프트 제공

- **위치**: `src/document/field_mapper.py` `_apply_llm_mapping_with_synonyms()` 및 `src/prompts/field_mapper.py`
- **내용**: Polestar DB 구조에 특화된 few-shot 예시를 LLM 프롬프트에 삽입하여, 올바른 테이블로 매핑하도록 유도한다.

**추가할 few-shot 예시:**

```
# Polestar DB 매핑 예시 (반드시 참고)
#
# 양식 필드명 → DB 매핑
# ─────────────────────────────────────────
# "호스트명"     → cmm_resource.hostname
# "IP주소"       → cmm_resource.ipaddress
# "리소스타입"   → cmm_resource.resource_type
# "상태"         → cmm_resource.avail_status
# "설명"         → cmm_resource.description
# "OS종류"       → EAV:OSType        (CORE_CONFIG_PROP.NAME='OSType')
# "OS버전"       → EAV:OSVerson      (CORE_CONFIG_PROP.NAME='OSVerson')
# "벤더"         → EAV:Vendor        (CORE_CONFIG_PROP.NAME='Vendor')
# "모델"         → EAV:Model         (CORE_CONFIG_PROP.NAME='Model')
# "시리얼번호"   → EAV:SerialNumber  (CORE_CONFIG_PROP.NAME='SerialNumber')
# "에이전트ID"   → EAV:AgentID       (CORE_CONFIG_PROP.NAME='AgentID')
#
# 주의: servers, cpu_metrics, disk_metrics 등은 Polestar DB에 존재하지 않음.
#       Polestar에서는 모든 리소스가 cmm_resource에 계층적으로 저장됨.
```

- **적용 방식**: `_apply_llm_mapping_with_synonyms()`에서 Polestar DB(db_id에 "polestar" 포함)가 대상일 때 위 few-shot 예시를 프롬프트에 삽입.
- **효과**: LLM이 `servers.hostname` 대신 `cmm_resource.hostname`으로, `servers.os` 대신 `EAV:OSType`으로 매핑하도록 유도.

### 수정 D: EAV 쿼리 시 테이블 일관성 검증

- **위치**: `src/nodes/query_generator.py` `_build_user_prompt()`
- **내용**: eav_entries가 존재할 때, regular_entries의 테이블이 CMM_RESOURCE 계열인지 확인. 비-Polestar 테이블이 섞여 있으면 제외하고 CMM_RESOURCE 컬럼으로 대체 안내.

---

## 5. 수정 우선순위

| 순위 | 수정 | 효과 | 난이도 |
|------|------|------|--------|
| 1 | A: query_generator column_mapping 필터링 | 잘못된 테이블이 프롬프트에 유입되는 것을 차단 | 낮음 |
| 2 | C: field_mapper few-shot 프롬프트 | 근본 원인 차단 (LLM이 올바른 테이블로 매핑) | 낮음 |
| 3 | D: EAV 테이블 일관성 검증 | EAV + 비-Polestar 혼합 방지 | 낮음 |
| 4 | B: query_validator 별칭 해석 | 잘못된 컬럼 참조를 정확히 탐지 (안전망) | 중간 |

수정 A → C → D를 먼저 적용하면 즉각적인 효과가 있다.
- A: 잘못된 매핑이 query_generator에 도달해도 필터링
- C: few-shot으로 LLM이 애초에 올바른 테이블로 매핑하도록 유도 (근본 원인)
- D: EAV 엔트리와 비-Polestar 엔트리 혼합 차단
- B: 최종 안전망으로 후속 적용 권장

---

## 6. 검증 결과 (2026-03-25)

### 6.1 수정 A~D 구현 상태

| 수정 | 파일:라인 | 상태 |
|------|-----------|------|
| A: column_mapping 필터링 | `query_generator.py:285-302` | 구현 완료 |
| B: 별칭 해석 | `query_validator.py:214-259, 280-303` | 구현 완료 |
| C: few-shot 프롬프트 | `prompts/field_mapper.py:188-219`, `document/field_mapper.py:556-559` | 구현 완료 |
| D: EAV 테이블 일관성 | `query_generator.py:315-330` | 구현 완료 |

### 6.2 심층 검증에서 발견된 이슈

#### Critical: multi_db_executor KeyError (수정 E — 신규)

- **위치**: `src/nodes/multi_db_executor.py:292`
- **문제**: `_generate_sql()`에서 `QUERY_GENERATOR_SYSTEM_TEMPLATE.format()`을 호출할 때 `polestar_guide`와 `db_engine_hint` 변수를 전달하지 않아 `KeyError` 발생
- **원인**: Plan 20에서 `QUERY_GENERATOR_SYSTEM_TEMPLATE`에 `{polestar_guide}`와 `{db_engine_hint}` 플레이스홀더를 추가했지만, `multi_db_executor`의 `_generate_sql()`은 업데이트하지 않음
- **영향**: 멀티 DB 실행 경로에서 Polestar DB 대상 SQL 생성이 모든 경우에 실패
- **수정**: `_generate_sql()`에 `db_engine` 파라미터 추가, `polestar_guide`/`db_engine_hint` 생성 로직 추가, column_mapping 필터링(수정 A) 동일 적용
- **상태**: 2026-03-25 수정 완료

#### Minor 이슈 목록

| # | 항목 | 영향 | 비고 |
|---|------|------|------|
| 1 | few-shot에 EAV 속성 4개 누락 (GMT, Hostname, IPaddress, OSParameter) | 극히 낮음 | 중복/보조 속성이라 양식 매핑에 영향 없음 |
| 2 | `has_polestar` 감지가 substring 방식 | 낮음 | 현재 db_id="polestar" 또는 "polestar_pg"로 고정 |
| 3 | 멀티 DB에서 few-shot 주의사항이 다른 DB 매핑에 간섭 가능 | 낮음 | 수정 A 안전망이 잘못된 SQL 생성을 방지 |
| 4 | 스키마 접두사(`polestar.cmm_resource r`) 별칭 추출 실패 | 낮음 | 현재 코드에서 스키마 접두사 미사용 |
| 5 | CROSS JOIN 별칭 미추출 | 극히 낮음 | 프로젝트에서 CROSS JOIN 미사용 |
| 6 | `schema_info={}`(빈 dict)일 때 필터링 스킵 | 극히 낮음 | schema_analyzer 정상 동작 시 발생 불가 |
| 7 | "OSVerson" 표기 | 없음 | 원본 Polestar 제품의 오탈자가 DB에 그대로 반영된 것. 실제 값과 일치하므로 수정 불가 |
| 8 | `_extract_alias_map` 테스트 미작성 | 낮음 | 코드 경로 분석으로 정상 동작 확인됨. 향후 단위 테스트 추가 권장 |

### 6.3 최종 평가

수정 A~D는 올바르게 구현되어 있으며, Plan 25에서 지적한 5가지 문제 코드 중 query_generator/query_validator 경로는 모두 해결되었다. 검증 과정에서 발견된 **multi_db_executor의 Critical 버그(수정 E)**는 즉시 수정 완료하였다.

잔여 Minor 이슈는 모두 현재 환경에서 실질적 영향이 없으며, 향후 환경 변경(멀티 DB 확대, 스키마 접두사 도입 등) 시 재검토할 수 있다.
