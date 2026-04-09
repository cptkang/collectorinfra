# Plan 43: Polestar 메모리 사용률 쿼리 실패 분석 및 해결

> 작성일: 2026-04-06
> 수정일: 2026-04-06
> 상태: 분석 완료 / 구현 대기
> 관련: Plan 34 (Polestar 전용 시스템 프롬프트), Plan 42 (불필요 JOIN 차단, D-028)
> 선행 조건: Plan 42 구현 완료 (confirmed: allowed_tables, excluded_join_columns, validator 패턴 3 모두 반영됨)

---

## 1. 문제 정의

### 1.1 사용자 프롬프트

> "메모리 사용률이 80% 이상인 서버 목록을 조회합니다"

### 1.2 LLM이 생성한 쿼리 (2차 시도)

```sql
SELECT
  C.platform_resource_id AS server_id,
  MAX(CASE WHEN C.resource_type IN ('server.Server','platform.server') THEN C.hostname END) AS hostname,
  MAX(CASE WHEN C.resource_type IN ('server.Server','platform.server') THEN C.ipaddress END) AS ipaddress,
  MAX(CASE WHEN C.resource_type = 'server.Memory' THEN C.resourcestatus END) AS memory_resource_status
FROM polestar.cmm_resource C
WHERE C.dtime IS NULL
GROUP BY C.platform_resource_id
HAVING MAX(CASE WHEN C.resource_type = 'server.Memory' THEN C.resourcestatus END) >= 2
  AND MAX(CASE WHEN C.resource_type IN ('server.Server','platform.server') THEN C.hostname END) IS NOT NULL
ORDER BY hostname
LIMIT 1000;
```

### 1.3 결과

0행 반환 -- 조회 실패.

---

## 2. 실패 원인 분석

### 원인 1: `core_config_prop` JOIN 누락

Polestar DB는 **EAV(Entity-Attribute-Value) 구조**로, `cmm_resource`만으로는 상세 속성을 조회할 수 없다. 반드시 `core_config_prop`과 조인해야 한다:

```sql
FROM polestar.cmm_resource C
LEFT JOIN polestar.core_config_prop CC
  ON C.resource_conf_id = CC.configuration_id
```

실패한 쿼리는 `cmm_resource` 단독으로만 조회하면서 `C.hostname`, `C.ipaddress`, `C.resourcestatus` 같은 엔티티 테이블의 직접 컬럼만 사용했다.

**정상 동작하는 다른 쿼리와의 비교:**

| 쿼리 | JOIN 여부 | 결과 |
|------|-----------|------|
| 디스크 사용량 상위 10개 (10:37:44) | `LEFT JOIN core_config_prop CC ON C.resource_conf_id = CC.configuration_id` | 10행 성공 |
| 메모리 사용률 80% 이상 (10:54:55) | JOIN 없음 (`cmm_resource`만 사용) | 0행 실패 |

### 원인 2: `resourcestatus` 컬럼 오용 (Hallucination)

LLM이 `resourcestatus`를 메모리 사용률로 **임의 해석**했다.

- 1차 시도: `M.resourcestatus >= 80` -- 0행 (resourcestatus는 %가 아닌 상태 코드)
- 2차 시도: `C.resourcestatus >= 2` (80%로 가정) -- 0행 (여전히 임의 해석)

`resourcestatus`는 리소스 상태 코드(0, 1, 2 등 정수)이지, 사용률(%)이 아니다. LLM이 스키마에 없는 의미를 추측하여 쿼리를 생성한 전형적인 hallucination 사례이다.

**근본 원인: Redis synonym 데이터에서 `polestar.cmm_resource.resourcestatus`가 "리소스 상태", "상태" 등의 유사 단어와 매핑되어 있어, LLM이 "메모리 사용률"과 "상태"를 혼동할 여지가 있다.**

### 원인 3: 메모리 사용률 EAV 속성이 DB에 존재하지 않음

**확인 결과 (테스트 데이터 및 운영 데이터 분석):**

`polestar.yaml`의 `known_attributes`와 테스트 데이터(`testdata/pg/05_insert_excel_data.sql`)를 교차 검증한 결과, `server.Memory` 리소스의 EAV 속성은 다음뿐이다:

| EAV 속성명 | 설명 | 예시 값 |
|-----------|------|---------|
| `TotalSize` | 총 물리 메모리 크기 | "62.1 GB" |
| `TotalPhysicalMemory` | 총 물리 메모리 (바이트) | "8589934592" |

**실시간 메모리 사용률(%)을 나타내는 EAV 속성이 존재하지 않는다.** 이것은 Polestar가 CMDB(Configuration Management Database), 즉 **정적 인프라 인벤토리** 시스템이기 때문이다. 실시간 메트릭(CPU 사용률, 메모리 사용률, 디스크 I/O 등)은 모니터링 시스템(Prometheus, Zabbix, Datadog 등)에서 관리한다.

LLM이 사용할 수 있는 데이터가 없으므로, `resourcestatus` 같은 의미가 불분명한 컬럼에 의존하게 되었다.

### 시도 이력 (SQL 로그)

| 시도 | 시각 | 조건 | JOIN | 행 수 | 문제 |
|------|------|------|------|-------|------|
| 1차 | 10:49:25 | `M.resourcestatus >= 80` | self-join (cmm_resource 간) | 0 | resourcestatus는 %가 아닌 상태 코드 |
| 2차 | 10:54:55 | `C.resourcestatus >= 2` (80%로 가정) | 없음 (cmm_resource 단독) | 0 | 임의 해석 + core_config_prop JOIN 누락 |

---

## 3. 해결 방법

### 3.0 전제: 운영 DB에서 EAV 속성 확인 (선행 작업)

구현 전 운영 DB에서 `server.Memory`의 실제 EAV 속성 목록을 반드시 확인한다. 테스트 데이터에는 `TotalSize`와 `TotalPhysicalMemory`만 존재하지만, 운영 DB에 추가 속성이 있을 수 있다.

```sql
-- 실행 대상: 운영 Polestar DB
SELECT DISTINCT CC.name, COUNT(*) AS cnt
FROM polestar.cmm_resource C
JOIN polestar.core_config_prop CC
  ON C.resource_conf_id = CC.configuration_id
WHERE C.resource_type = 'server.Memory'
  AND C.dtime IS NULL
GROUP BY CC.name
ORDER BY CC.name
LIMIT 100;
```

**결과에 따른 분기:**
- **Case A**: `MemoryUsage`, `MemUtilization` 등 사용률 속성이 발견되면 -> 3.1A + 4.1A 진행
- **Case B**: `TotalSize`/`TotalPhysicalMemory`만 존재하면 -> 3.1B + 4.1B 진행 (현재 예상 시나리오)

아울러 `resourcestatus`의 실제 값 분포도 확인한다:

```sql
SELECT
  C.resource_type,
  C.resourcestatus,
  COUNT(*) AS cnt
FROM polestar.cmm_resource C
WHERE C.resource_type = 'server.Memory'
  AND C.dtime IS NULL
GROUP BY C.resource_type, C.resourcestatus
ORDER BY C.resourcestatus
LIMIT 50;
```

### 3.1A 메모리 사용률 속성이 존재하는 경우

`known_attributes`에 해당 속성을 추가하고, few-shot 예시를 작성한다. (아래 4.1A, 4.2A 참조)

### 3.1B 메모리 사용률 속성이 존재하지 않는 경우 (예상 시나리오)

LLM이 사용자에게 **데이터 한계를 명확히 안내**하도록 시스템을 변경한다. 구체적으로:

1. **`query_guide`에 데이터 한계 명시** -- LLM이 쿼리를 생성하기 전에 "이 데이터는 조회 불가"를 판단할 수 있도록 한다
2. **Polestar 전용 프롬프트에 CMDB 특성 명시** -- 실시간 메트릭이 아닌 정적 인벤토리임을 강조
3. **`resourcestatus` 컬럼의 의미를 프롬프트에 명시** -- hallucination 방지

---

## 4. 재발 방지 대책 (구체적 구현)

### 4.1A `polestar.yaml` known_attributes 보강 (Case A: 속성 존재 시)

> Case A에서만 실행. Case B에서는 이 단계를 건너뛴다.

**수정 파일**: `config/db_profiles/polestar.yaml`, `config/db_profiles/polestar_pg.yaml`

기존 `server.Memory` 속성 아래에 발견된 속성을 추가한다:

```yaml
      # --- server.Memory 속성 (resource_type = 'server.Memory') ---
      - name: TotalSize
        description: "총 물리 메모리 크기 (예: 62.1 GB) [resource_type: server.Memory] / 전체 디스크 용량 [resource_type: server.Disks]"
        synonyms: ["메모리", "MEMORY", "메모리크기", "총메모리", "메모리용량", "MEM", "디스크용량", "디스크크기"]
      - name: MemoryUsage       # <-- 실제 속성명으로 교체. 예시임.
        description: "메모리 사용률 (%) [resource_type: server.Memory]"
        synonyms: ["메모리사용률", "메모리사용량", "MEM사용률", "메모리 사용률"]
```

### 4.1B `polestar.yaml` query_guide에 데이터 한계 명시 (Case B: 속성 미존재 시)

**수정 파일**: `config/db_profiles/polestar.yaml` (line 165~), `config/db_profiles/polestar_pg.yaml` (line 165~)

`query_guide` 끝에 다음 섹션을 추가한다:

```yaml
query_guide: |
  Polestar DB는 cmm_resource(리소스 계층)와 core_config_prop(EAV 설정) 2개 테이블로 구성됩니다.

  ... (기존 내용 유지) ...

  [데이터 한계 -- Polestar DB의 특성]
  Polestar는 CMDB(Configuration Management Database)로, 인프라 자산의 정적 구성 정보(스펙, 설정)를 관리합니다.
  실시간 성능 메트릭(CPU 사용률, 메모리 사용률, 디스크 I/O, 네트워크 트래픽 등)은 저장하지 않습니다.

  조회 가능한 데이터:
    - 서버 정보: 호스트명, IP, OS, 벤더, 모델, 시리얼번호 등
    - CPU 스펙: 모델, 코어수, 소켓수, 클럭속도
    - 메모리 용량: TotalSize (예: 62.1 GB) -- 총 물리 메모리 크기만 저장
    - 디스크 용량: TotalSize, DiskCount
    - 네트워크 인터페이스: IP, MAC, 대역폭, MTU

  조회 불가능한 데이터 (사용자에게 안내 필요):
    - CPU 사용률 (%)
    - 메모리 사용률 (%)
    - 디스크 사용률 (%)
    - 네트워크 트래픽량
    - 기타 실시간 모니터링 메트릭

  사용자가 조회 불가능한 데이터를 요청하면, SQL을 생성하지 말고 다음과 같이 안내하세요:
  "Polestar DB는 인프라 자산 구성 정보(CMDB)만 저장합니다. 요청하신 [메트릭명]은 실시간 모니터링 데이터로, Polestar에 저장되어 있지 않습니다. 모니터링 시스템에서 확인해주세요."

  [resourcestatus 컬럼 주의]
  cmm_resource.resourcestatus는 리소스의 관리 상태 코드(0, 1, 2 등 정수)이며, 사용률(%)이 아닙니다.
  이 컬럼을 CPU/메모리/디스크 사용률 조건에 절대 사용하지 마세요.
```

**변경 근거**: LLM은 `query_guide` 텍스트를 시스템 프롬프트의 일부로 받는다(`_format_structure_guide()` -> `_build_system_prompt()`). 데이터 한계를 여기에 명시하면 LLM이 쿼리 생성 전에 "조회 불가" 판단을 할 수 있다.

### 4.2A query_examples에 메모리 조회 예시 추가 (Case A: 속성 존재 시)

**수정 파일**: `config/db_profiles/polestar.yaml`, `config/db_profiles/polestar_pg.yaml`

`query_examples` 배열에 다음을 추가한다:

```yaml
  - question: "메모리 사용률이 80% 이상인 서버를 조회해줘"
    sql: |
      SELECT
        C.platform_resource_id AS server_id,
        MAX(CASE WHEN C.resource_type IN ('server.Server','platform.server')
                 AND CC.name = 'Hostname' THEN CC.stringvalue_short END) AS hostname,
        MAX(CASE WHEN C.resource_type IN ('server.Server','platform.server')
                 AND CC.name = 'IPaddress' THEN CC.stringvalue_short END) AS ipaddress,
        MAX(CASE WHEN C.resource_type = 'server.Memory'
                 AND CC.name = 'TotalSize' THEN CC.stringvalue_short END) AS mem_total_size,
        CAST(MAX(CASE WHEN C.resource_type = 'server.Memory'
                      AND CC.name = '실제속성명' THEN CC.stringvalue_short END) AS NUMERIC) AS mem_usage_pct
      FROM polestar.cmm_resource C
      LEFT JOIN polestar.core_config_prop CC
        ON C.resource_conf_id = CC.configuration_id
      WHERE C.dtime IS NULL
      GROUP BY C.platform_resource_id
      HAVING CAST(MAX(CASE WHEN C.resource_type = 'server.Memory'
                           AND CC.name = '실제속성명' THEN CC.stringvalue_short END) AS NUMERIC) >= 80
        AND MAX(CASE WHEN C.resource_type IN ('server.Server','platform.server')
                     AND CC.name = 'Hostname' THEN CC.stringvalue_short END) IS NOT NULL
      ORDER BY mem_usage_pct DESC
      LIMIT 100;
    explanation: |
      메모리 사용률은 core_config_prop EAV에서 조회한다.
      resourcestatus 컬럼은 상태 코드(정수)이므로 사용률 조건에 사용하지 않는다.
      반드시 resource_conf_id = configuration_id 조인을 사용한다.
```

> NOTE: `'실제속성명'` 부분은 3.0의 운영 DB 확인 결과로 교체해야 한다.

### 4.2B query_examples에 "조회 불가" 안내 예시 추가 (Case B: 속성 미존재 시)

**수정 파일**: `config/db_profiles/polestar.yaml`, `config/db_profiles/polestar_pg.yaml`

`query_examples` 배열에 **"조회 불가" 시나리오** 예시를 추가한다. 이 예시는 LLM에게 SQL을 생성하지 않고 사용자에게 한계를 안내하는 패턴을 학습시킨다:

```yaml
  # 조회 불가 시나리오 -- LLM이 SQL 대신 안내 메시지를 생성하도록 학습
  - question: "메모리 사용률이 80% 이상인 서버를 조회해줘"
    sql: ""
    explanation: |
      Polestar DB는 CMDB로, 메모리 사용률(%) 데이터를 저장하지 않습니다.
      조회 가능한 메모리 정보는 TotalSize(총 메모리 크기, 예: 62.1 GB)뿐입니다.
      메모리 사용률은 모니터링 시스템(예: Prometheus, Zabbix)에서 확인해주세요.
      대안: "메모리 크기가 64GB 이상인 서버를 조회해줘"와 같이 TotalSize 기반 조건으로 변경하면 조회 가능합니다.
```

**주의**: `sql` 필드가 빈 문자열인 경우를 `_format_structure_guide()` (`src/nodes/query_generator.py:119-132`)가 올바르게 처리하는지 확인 필요. 현재 코드는 `sql_example = ex.get("sql", "").rstrip()`으로 읽으므로 빈 문자열이면 빈 코드블록이 표시된다. 빈 SQL이 부자연스러우면 `sql` 필드를 생략하고 `explanation`만 두는 방식으로 변경할 수 있다.

**`_format_structure_guide` 수정 (선택적)**: 빈 SQL 예시를 자연스럽게 표시하기 위한 수정:

```python
# src/nodes/query_generator.py, _format_structure_guide() 내 query_examples 루프 (line 119~132)
# 기존:
for i, ex in enumerate(query_examples, 1):
    question = ex.get("question", "")
    sql_example = ex.get("sql", "").rstrip()
    explanation = ex.get("explanation", "")
    guide += f"\n### 예시 {i}: \"{question}\""
    guide += f"\n```sql\n{sql_example}\n```"
    if explanation:
        guide += f"\n설명: {explanation}"
    guide += "\n"

# 수정:
for i, ex in enumerate(query_examples, 1):
    question = ex.get("question", "")
    sql_example = ex.get("sql", "").rstrip()
    explanation = ex.get("explanation", "")
    guide += f"\n### 예시 {i}: \"{question}\""
    if sql_example:
        guide += f"\n```sql\n{sql_example}\n```"
    else:
        guide += "\n[이 질문은 SQL을 생성하지 않습니다 -- 아래 설명을 사용자에게 안내하세요]"
    if explanation:
        guide += f"\n설명: {explanation}"
    guide += "\n"
```

### 4.3 Polestar 전용 프롬프트에 CMDB 특성 및 금지 규칙 추가

**수정 파일**: `src/prompts/query_generator.py`

`POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE` (line 52~)의 `[Strict Constraints - 절대 위반 불가]` 섹션에 다음 규칙을 추가한다:

```python
# 현재 (line 57~59):
# [Strict Constraints - 절대 위반 불가]
# 1. 환각 금지: ...
# 2. 조인 금지: ...
# 3. 필터링 규칙: ...

# 추가할 규칙:
# 4. CMDB 한계: Polestar는 CMDB(정적 구성 정보)이므로 실시간 메트릭(CPU 사용률, 메모리 사용률, 디스크 사용률, 네트워크 트래픽)을 저장하지 않는다. 사용자가 이러한 메트릭을 요청하면 SQL을 생성하지 말고, 조회 불가 사유와 대안을 안내하라.
# 5. resourcestatus 금지: cmm_resource.resourcestatus 컬럼은 리소스의 관리 상태 코드(0, 1, 2 등 정수)이다. CPU/메모리/디스크 사용률(%)이 아니므로 절대 사용률 조건에 사용하지 않는다.
```

**구체적 코드 변경 (diff)**:

```python
# src/prompts/query_generator.py, POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE

# 기존 (line 57~59):
[Strict Constraints - 절대 위반 불가]
1. 환각 금지: 스키마에 없는 테이블, 컬럼, 리소스 타입(예: resource_type = 'platform.server')을 절대 지어내지 않는다.
2. 조인 금지: CMM_RESOURCE.ID와 CORE_CONFIG_PROP.CONFIGURATION_ID를 직접 조인하지 않는다.
3. 필터링 규칙: 서버 자원 조회 시 반드시 `R.DTYPE = 'ServiceResource'` 조건과 `R.PARENT_RESOURCE_ID IS NULL` 조건을 사용한다.

# 수정 후:
[Strict Constraints - 절대 위반 불가]
1. 환각 금지: 스키마에 없는 테이블, 컬럼, 리소스 타입(예: resource_type = 'platform.server')을 절대 지어내지 않는다.
2. 조인 금지: CMM_RESOURCE.ID와 CORE_CONFIG_PROP.CONFIGURATION_ID를 직접 조인하지 않는다.
3. 필터링 규칙: 서버 자원 조회 시 반드시 `R.DTYPE = 'ServiceResource'` 조건과 `R.PARENT_RESOURCE_ID IS NULL` 조건을 사용한다.
4. CMDB 한계: Polestar는 CMDB(정적 인프라 구성 정보)이다. CPU 사용률(%), 메모리 사용률(%), 디스크 사용률(%), 네트워크 트래픽량 등 실시간 모니터링 메트릭은 이 DB에 저장되지 않는다. 사용자가 이러한 메트릭을 요청하면 SQL을 생성하지 말고 "Polestar는 CMDB로 해당 데이터를 저장하지 않습니다"라고 안내하라.
5. resourcestatus 금지: cmm_resource.resourcestatus는 리소스 관리 상태 코드(0=정상, 1=비정상 등 정수)이다. 사용률(%)이 아니므로, CPU/메모리/디스크 사용률 조건에 절대 사용하지 않는다.
```

### 4.4 query_validator에 cmm_resource 단독 EAV 쿼리 경고 추가

**수정 파일**: `src/nodes/query_validator.py`

Polestar DB에서 `cmm_resource`의 EAV 의존 속성(hostname/ipaddress 외의 속성)을 조회하면서 `core_config_prop` JOIN이 없는 쿼리에 대해 경고를 추가한다.

**수정 위치**: `query_validator()` 함수 내, 6.6 단계(`_validate_forbidden_joins`) 이후 (line 139 부근)

**로직**:
1. `schema_info["_structure_meta"]`에 EAV 패턴이 존재하는지 확인
2. EAV 패턴의 `entity_table`이 SQL의 FROM/JOIN에 있는지 확인
3. `config_table`이 SQL의 FROM/JOIN에 없는지 확인
4. SQL이 단순 집계(`SELECT COUNT(*)` 등)가 아닌지 확인
5. 조건 충족 시 warning 추가: "EAV 구조에서 config 테이블 JOIN 없이 entity 테이블만 사용했습니다. 속성 조회 시 core_config_prop JOIN이 필요합니다."

**주의**: 이 규칙은 `warning`(경고)으로 구현한다. `error`로 만들면 `SELECT COUNT(*) FROM cmm_resource` 같은 단순 집계까지 실패하게 된다. 단순 집계는 예외로 처리한다.

```python
# src/nodes/query_validator.py, query_validator() 내 line 139 이후에 추가

# 6.7. EAV 패턴에서 config 테이블 JOIN 누락 경고
eav_join_warnings = _check_eav_config_join_missing(sql, schema_info)
warnings.extend(eav_join_warnings)
```

**새 함수 정의**:

```python
def _check_eav_config_join_missing(sql: str, schema_info: dict) -> list[str]:
    """EAV 패턴에서 config 테이블 JOIN 누락을 감지한다.

    cmm_resource만 사용하고 core_config_prop JOIN이 없는 쿼리에 대해
    경고를 발생시킨다. 단순 집계(COUNT, EXISTS 등)는 예외로 한다.

    Args:
        sql: SQL 쿼리
        schema_info: 스키마 정보

    Returns:
        경고 메시지 목록
    """
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return []

    eav_patterns = [
        p for p in structure_meta.get("patterns", [])
        if p.get("type") == "eav"
    ]
    if not eav_patterns:
        return []

    warnings: list[str] = []
    sql_upper = sql.upper()

    # 단순 집계 쿼리 예외: SELECT 절에 집계 함수만 있고 일반 컬럼이 없는 경우
    select_match = re.search(
        r"SELECT\s+(.*?)\s+FROM\b",
        sql_upper,
        re.IGNORECASE | re.DOTALL,
    )
    if select_match:
        select_clause = select_match.group(1).strip()
        # COUNT(*), COUNT(1), EXISTS 등만 있으면 예외
        if re.match(
            r"^(COUNT|SUM|AVG|MIN|MAX|EXISTS)\s*\(",
            select_clause,
            re.IGNORECASE,
        ):
            return []

    for eav_pat in eav_patterns:
        entity_table = eav_pat.get("entity_table", "").lower()
        config_table = eav_pat.get("config_table", "").lower()
        if not entity_table or not config_table:
            continue

        # 스키마 접두사 제거
        entity_bare = entity_table.rsplit(".", 1)[-1]
        config_bare = config_table.rsplit(".", 1)[-1]

        # entity 테이블이 FROM/JOIN에 있는지
        if not re.search(rf"\b{re.escape(entity_bare)}\b", sql, re.IGNORECASE):
            continue
        # config 테이블이 FROM/JOIN에 없는지
        if re.search(rf"\b{re.escape(config_bare)}\b", sql, re.IGNORECASE):
            continue

        warnings.append(
            f"EAV 구조에서 {entity_bare} 테이블만 사용하고 {config_bare} JOIN이 누락되었습니다. "
            f"속성(Hostname, IPaddress 등)은 {entity_bare}의 직접 컬럼으로 조회 가능하지만, "
            f"EAV 속성(OSType, Vendor, TotalSize 등)은 반드시 {config_bare} JOIN이 필요합니다."
        )

    return warnings
```

### 4.5 `resourcestatus` synonym 명확화

**수정 대상**: Redis 캐시의 synonym 데이터

현재 Redis에 `polestar.cmm_resource.resourcestatus`가 "리소스 상태", "상태", "resource status", "status" 등의 유사 단어와 매핑되어 있다. "상태"라는 단어가 너무 광범위하여 LLM이 "메모리 상태" -> "메모리 사용률" 같은 잘못된 연상을 할 수 있다.

**조치**: 다음 Redis 캐시 무효화 시 column description을 명확히 업데이트한다.
- description: "리소스 관리 상태 코드 (0=정상, 1=비정상). 사용률(%)이 아님"
- synonyms에서 "상태" 제거, "관리상태코드"로 교체

이 변경은 Redis 캐시 재생성 시 `polestar.yaml`의 스키마 설명이 자동 반영되므로, `query_guide`의 `[resourcestatus 컬럼 주의]` 섹션이 추가되면 자연스럽게 반영된다.

---

## 5. 수정 파일 목록

| # | 파일 | 변경 유형 | 내용 | Case |
|---|------|----------|------|------|
| 1 | `config/db_profiles/polestar.yaml` | 수정 | `query_guide`에 데이터 한계/resourcestatus 주의 섹션 추가 | A+B 공통 |
| 2 | `config/db_profiles/polestar_pg.yaml` | 수정 | 동일 (polestar.yaml과 동기) | A+B 공통 |
| 3 | `src/prompts/query_generator.py` | 수정 | `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`에 규칙 4(CMDB 한계), 규칙 5(resourcestatus 금지) 추가 | A+B 공통 |
| 4 | `src/nodes/query_validator.py` | 수정 | `_check_eav_config_join_missing()` 함수 추가 + `query_validator()`에서 호출 | A+B 공통 |
| 5 | `src/nodes/query_generator.py` | 수정 | `_format_structure_guide()`에서 빈 SQL 예시 처리 | B only |
| 6 | `config/db_profiles/polestar.yaml` | 수정 | `known_attributes`에 메모리 사용률 속성 추가 | A only |
| 7 | `config/db_profiles/polestar_pg.yaml` | 수정 | 동일 | A only |
| 8 | `config/db_profiles/polestar.yaml` | 수정 | `query_examples`에 메모리 조회/조회불가 예시 추가 | A or B |
| 9 | `config/db_profiles/polestar_pg.yaml` | 수정 | 동일 | A or B |

---

## 6. 실행 순서

| 단계 | 작업 | 선행 조건 | 우선순위 |
|------|------|----------|----------|
| 0 | 운영 DB에서 `server.Memory` EAV 속성 확인 + `resourcestatus` 값 분포 확인 (3.0) | 없음 | **즉시** (수동) |
| 1 | `polestar.yaml` / `polestar_pg.yaml`의 `query_guide`에 데이터 한계 + resourcestatus 주의 섹션 추가 (4.1B) | 단계 0 결과 | 높음 |
| 2 | `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`에 규칙 4, 5 추가 (4.3) | 없음 | 높음 |
| 3 | `query_validator`에 `_check_eav_config_join_missing()` 추가 (4.4) | 없음 | 높음 |
| 4 | `query_examples`에 메모리 조회 또는 조회불가 예시 추가 (4.2A 또는 4.2B) | 단계 0 결과 | 높음 |
| 5 | `_format_structure_guide()`에서 빈 SQL 예시 처리 (4.2B 관련) | 단계 4에서 Case B 확정 시 | 중간 |
| 6 | (Case A only) `known_attributes`에 메모리 사용률 속성 추가 (4.1A) | 단계 0에서 속성 발견 시 | 높음 |
| 7 | Redis synonym 캐시 무효화 후 재생성 (4.5) | 단계 1, 2 완료 후 | 낮음 |

**병렬화 가능**: 단계 1, 2, 3은 독립적이므로 동시 진행 가능.

---

## 7. 기존 방어 체계와의 관계

이 계획은 Plan 42 (D-028)에서 구축한 방어 체계와 **상호 보완** 관계이다:

```
Plan 42의 방어 대상:              Plan 43의 방어 대상:
  불필요 테이블 JOIN                 조회 불가 데이터 hallucination
  (cmm_vendor, cmm_os 등)           (resourcestatus 오용)
  
  [해결] allowed_tables 필터링       [해결] query_guide 데이터 한계 명시
  [해결] excluded_join_columns       [해결] 프롬프트 규칙 4, 5 추가
  [해결] validator 패턴 3            [해결] validator EAV JOIN 누락 경고
```

**Plan 42와의 중복 없음**: Plan 42는 "어떤 테이블을 사용할 수 있는가"를 제어하고, Plan 43은 "어떤 데이터가 존재하는가"를 LLM에 알린다.

---

## 8. 테스트 계획

| # | 테스트 | 입력 | 기대 결과 | 검증 방법 |
|---|--------|------|----------|----------|
| 1 | 메모리 사용률 조회 요청 (Case B) | "메모리 사용률이 80% 이상인 서버를 조회해줘" | SQL 생성 안함, 데이터 한계 안내 메시지 출력 | E2E: LLM 응답에 "CMDB" 또는 "저장하지 않" 포함 확인 |
| 2 | 메모리 크기 조회 요청 | "메모리가 64GB 이상인 서버를 조회해줘" | `core_config_prop` JOIN 포함된 정상 SQL 생성 | SQL에 `CC.name = 'TotalSize'` + `resource_conf_id = configuration_id` 포함 |
| 3 | resourcestatus 사용 방지 | "서버의 메모리 상태를 조회해줘" | `resourcestatus` 컬럼 미사용 | 생성된 SQL에 `resourcestatus` 미포함 |
| 4 | EAV JOIN 누락 경고 | `cmm_resource`만 사용 + EAV 속성 참조 SQL | validator warning 발생 | `_check_eav_config_join_missing()` 반환값 비어있지 않음 |
| 5 | 단순 집계 예외 | `SELECT COUNT(*) FROM cmm_resource WHERE ...` | validator warning 미발생 | `_check_eav_config_join_missing()` 반환값 빈 리스트 |
| 6 | CPU 사용률 조회 요청 | "CPU 사용률이 90% 이상인 서버" | SQL 생성 안함, 한계 안내 | E2E: CMDB 한계 안내 확인 |
| 7 | 디스크 사용량 조회 (정상) | "디스크 용량이 1TB 이상인 서버" | 정상 SQL (TotalSize 기반) | SQL에 `CC.name = 'TotalSize'` 포함 |

---

## 9. 핵심 교훈

1. **EAV 구조에서는 반드시 config 테이블을 JOIN해야 한다** -- `cmm_resource` 단독 쿼리로는 의미 있는 속성을 조회할 수 없다.
2. **`resourcestatus`는 상태 코드이지 메트릭 값이 아니다** -- LLM이 컬럼명으로 의미를 추측하는 hallucination을 방지하려면 known_attributes를 완전하게 유지해야 한다.
3. **known_attributes에 없는 데이터는 조회할 수 없다** -- LLM이 "없는 데이터"를 요청받았을 때 임의로 쿼리를 생성하지 않고, 사용자에게 한계를 안내하도록 프롬프트를 설계해야 한다.
4. **CMDB vs 모니터링 시스템의 구분이 중요하다** -- Polestar는 정적 구성 정보만 저장하며, 실시간 메트릭은 저장하지 않는다. 이 구분을 LLM에 명시적으로 전달해야 잘못된 쿼리 생성을 근본적으로 방지할 수 있다.
5. **방어 체계의 계층적 적용** -- 같은 문제를 여러 레이어(YAML 가이드, 프롬프트 규칙, validator 경고, few-shot 예시)에서 중복 방어하면 LLM의 hallucination을 효과적으로 억제할 수 있다.
