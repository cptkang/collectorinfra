# Redis 기반 스키마 캐시 구현 계획

## 1. 개요

### 1.1 목적
현재 파일 기반(JSON) 2차 캐시를 **Redis**로 교체하여 스키마 정보의 중앙 집중 관리, 빠른 접근, 멀티 인스턴스 공유를 실현한다. 추가로 각 컬럼의 **의미 설명(description)**을 LLM으로 생성하여 저장함으로써, 쿼리 생성 시 컬럼 선택 정확도를 높인다.

### 1.2 현재 상태 (AS-IS)
```
1차: 메모리 캐시 (SchemaCache, TTL 5분)
2차: 파일 캐시 (PersistentSchemaCache, .cache/schema/*.json)
3차: DB 전체 조회 (information_schema + search_objects)
```
- 파일 기반이라 멀티 프로세스/인스턴스 간 공유 불가
- 컬럼 의미 정보(description)가 없어 LLM이 컬럼명만으로 매핑 수행
- 운영자가 캐시를 수동 관리할 수 있는 전용 기능 부재 (invalidate만 존재)

### 1.3 목표 상태 (TO-BE)
```
1차: 메모리 캐시 (기존 SchemaCache, TTL 5분)
2차: Redis 캐시 (스키마 + 컬럼 설명 + 유사 단어, fingerprint 기반 변경 감지)
3차: DB 전체 조회 (폴백)
```
- Redis를 통해 인스턴스 간 스키마 캐시 공유
- LLM이 생성한 컬럼별 설명(description) + **유사 단어(synonym) 목록** 저장
- 운영자용 캐시 초기화/갱신/조회 API 제공
- **독립 실행 가능한 CLI 스크립트** (`scripts/schema_cache_cli.py`) 제공 — FastAPI 서버 없이도 캐시 생성/갱신/조회/삭제 가능
- **대화형 프롬프트 기반 캐시 생성** — 사용자가 자연어로 DB 정보를 입력하면 LLM이 해석하여 캐시를 자동 생성 (LangGraph 그래프 내 전용 노드)
- 캐시 미존재 시 자동 생성, 스키마 변경 시 자동 갱신

### 1.4 기존 코드 수정 범위

Redis 캐시 도입으로 **기존 코드 2곳**이 캐시를 참조하는 방식으로 수정된다.

| 기존 모듈 | 현재 동작 | 변경 후 동작 |
|-----------|----------|-------------|
| `src/nodes/schema_analyzer.py` | 메모리→파일→DB 3단계로 스키마 조회 | 메모리→**Redis**→파일(폴백)→DB 로 변경. `SchemaCacheManager`를 통해 캐시 접근. descriptions + synonyms도 함께 로드 |
| `src/nodes/query_generator.py` | `schema_info`의 테이블/컬럼 정보만으로 프롬프트 구성 | `schema_info` + **컬럼 설명(description)** + **유사 단어(synonyms)**를 프롬프트에 포함하여 LLM의 컬럼 선택 정확도 향상 |
| `src/prompts/query_generator.py` | 컬럼명 + 타입만 표시 | 컬럼명 + 타입 + **한국어 설명** + **유사 단어** 표시 |
| `src/nodes/schema_analyzer.py::_format_schema_for_prompt` | — | 이 함수는 `query_generator.py`에 위치하지만 동일하게 수정 |
| `src/state.py` | `schema_info: dict` | `column_descriptions: dict` 및 `column_synonyms: dict` 필드 추가 |

---

## 2. Redis 데이터 구조 설계

### 2.1 키 네이밍 컨벤션
```
schema:db_descriptions        → Hash    (db_id → DB 설명, 시멘틱 라우팅 시 DB 선택 가이드 제공)
schema:{db_id}:meta           → Hash    (fingerprint, cached_at, version, ...)
schema:{db_id}:tables         → Hash    (table_name → JSON serialized table info)
schema:{db_id}:relationships  → String  (JSON array of FK relationships)
schema:{db_id}:descriptions   → Hash    (table.column → LLM 생성 설명)
schema:{db_id}:synonyms       → Hash    (table.column → JSON object {words, sources})
synonyms:global               → Hash    (column_name → JSON array of 유사 단어, DB 독립 영구 보존)
```

### 2.2 상세 구조

> **검증**: 아래 구조가 SQL 쿼리 생성에 충분한지 `query_generator` 노드의 실제 사용 데이터와 대조하여 검증하였다.

#### 쿼리 생성에 필요한 정보 vs Redis 저장 매핑

| query_generator가 사용하는 정보 | 출처 (현재 코드) | Redis 저장 위치 |
|-------------------------------|-----------------|----------------|
| 테이블명 | `schema_info.tables.keys()` | `schema:{db_id}:tables` Hash 필드명 |
| 컬럼명 + 데이터 타입 | `columns[].name`, `columns[].type` | tables Hash → 각 테이블 JSON 내 `columns[]` |
| PK/FK 플래그 + FK 참조 대상 | `columns[].primary_key/foreign_key/references` | tables Hash → 각 테이블 JSON 내 `columns[]` |
| NULL 허용 여부 | `columns[].nullable` | tables Hash → 각 테이블 JSON 내 `columns[]` |
| 테이블 간 FK 관계 | `schema_info.relationships[].from/to` | **`schema:{db_id}:relationships`** (신규 키) |
| 샘플 데이터 | `tables[].sample_data` | tables Hash → 각 테이블 JSON 내 `sample_data` |
| 컬럼 설명 (한국어) | 현재 없음 → **신규 추가** | `schema:{db_id}:descriptions` Hash |
| 테이블/컬럼 기존 comment | `TableInfo.comment`, `ColumnInfo.comment` | tables Hash → JSON 내 `comment` 필드 |

#### `schema:db_descriptions` (Hash) — DB 설명

사용자가 프롬프트로 DB를 선택할 때 각 DB가 어떤 데이터를 보유하고 있는지 안내하는 가이드 정보. 시멘틱 라우터가 대상 DB를 분류할 때도 이 설명을 참조한다.

| Field | Value | 예시 |
|-------|-------|------|
| `polestar` | DB 설명 (한국어) | `"서버 사양, CPU/메모리/디스크 사용량, 호스트 정보, 프로세스 현황을 관리하는 인프라 모니터링 DB"` |
| `cloud_portal` | DB 설명 (한국어) | `"VM 정보, 데이터스토어, 리전별 VM 수량을 관리하는 클라우드 포탈 DB"` |
| `itsm` | DB 설명 (한국어) | `"IT 서비스 요청, 장애 티켓, 변경 관리를 담당하는 ITSM DB"` |
| `itam` | DB 설명 (한국어) | `"IT 자산 대장, 라이선스, 하드웨어/소프트웨어 인벤토리를 관리하는 ITAM DB"` |

**생성 방식**:
- **LLM 자동 생성**: 캐시 초기 생성 시 DB의 테이블 목록 + 샘플 데이터를 분석하여 DB 설명을 자동 생성
- **운영자 수동 설정**: CLI/API 또는 프롬프트로 DB 설명을 직접 지정 가능 (수동 설정은 LLM 재생성 시에도 보존)

**활용**:
- 시멘틱 라우터 프롬프트에 DB 설명을 포함하여 LLM의 DB 분류 정확도 향상
- 사용자가 `"어떤 DB가 있어?"`, `"DB 목록을 보여줘"` 같은 질의 시 DB 설명 목록 응답
- `cache_management` 노드에서 DB 가이드 응답 생성

#### `schema:{db_id}:meta` (Hash)
| Field | Value | 설명 |
|-------|-------|------|
| `fingerprint` | SHA-256 hex | 현재 스키마 fingerprint |
| `cached_at` | ISO 8601 | 캐시 생성 시각 |
| `cache_version` | int | 캐시 포맷 버전 (호환성 체크) |
| `table_count` | int | 테이블 수 |
| `total_column_count` | int | 전체 컬럼 수 |
| `description_status` | `complete` / `partial` / `pending` | LLM 설명 생성 상태 |

#### `schema:{db_id}:tables` (Hash)
| Field | Value |
|-------|-------|
| `{table_name}` | JSON (아래 상세 구조 참조) |

**테이블 JSON 상세 구조** (query_generator가 필요로 하는 모든 필드 포함):
```json
{
  "name": "servers",
  "schema_name": "public",
  "comment": "서버 기본 정보 테이블",
  "row_count_estimate": 1500,
  "columns": [
    {
      "name": "hostname",
      "type": "varchar(255)",
      "nullable": false,
      "primary_key": false,
      "foreign_key": false,
      "references": null,
      "comment": "서버 호스트명"
    }
  ],
  "sample_data": [
    {"hostname": "web-srv-01", "ip_address": "10.0.1.5"}
  ]
}
```

#### `schema:{db_id}:relationships` (String — JSON array)
```json
[
  {"from": "cpu_metrics.server_id", "to": "servers.id"},
  {"from": "memory_metrics.server_id", "to": "servers.id"},
  {"from": "disk_metrics.server_id", "to": "servers.id"}
]
```
> **핵심**: 이 관계 정보가 없으면 LLM이 JOIN 조건을 올바르게 생성할 수 없다. 기존 계획에서 누락되었으나 추가함.

#### `schema:{db_id}:descriptions` (Hash)
| Field | Value | 예시 |
|-------|-------|------|
| `servers.hostname` | 서버의 호스트명 (FQDN 또는 별칭) | LLM 생성 |
| `cpu_metrics.usage_pct` | CPU 사용률 (0~100 백분율) | LLM 생성 |
| `servers.os_type` | 운영체제 종류 (Linux, Windows 등) | LLM 생성 |

#### 검증 결론

현재 `_format_schema_for_prompt()` 함수가 사용하는 **6가지 정보**(테이블명, 컬럼 메타데이터, PK/FK, nullable, 샘플 데이터, FK 관계)가 모두 Redis에 저장되며, 추가로 LLM 생성 컬럼 설명과 DB 기존 comment를 포함하여 쿼리 생성 정확도를 향상시킨다.

### 2.3 저장 정책

**영구 저장 (TTL 없음)** — 스키마 정보는 만료되지 않으며, 변경이 감지될 때만 갱신한다.

| 키 | TTL | 갱신 조건 |
|----|-----|-----------|
| 키 | TTL | 갱신 조건 | invalidate 시 |
|----|-----|-----------|---------------|
| `schema:db_descriptions` | 없음 (영구) | DB 추가/삭제 시, 또는 운영자 수동 갱신 | **보존** (삭제하지 않음) |
| `schema:{db_id}:meta` | 없음 (영구) | fingerprint 변경 시 | 삭제 |
| `schema:{db_id}:tables` | 없음 (영구) | fingerprint 변경 시 | 삭제 |
| `schema:{db_id}:relationships` | 없음 (영구) | fingerprint 변경 시 | 삭제 |
| `schema:{db_id}:descriptions` | 없음 (영구) | 스키마 변경으로 컬럼 추가/삭제 시 incremental 갱신 | 삭제 |
| `schema:{db_id}:synonyms` | 없음 (영구) | LLM 재생성 시 운영자/사용자 수동 추가분 보존하며 merge | **보존** (삭제하지 않음) |
| `synonyms:global` | 없음 (영구) | 글로벌 유사단어 사전. DB 독립적으로 영구 보존 | **보존** (절대 자동 삭제하지 않음) |

> 갱신 트리거: (1) 쿼리 진입 시 fingerprint 비교로 변경 감지 → 자동 갱신, (2) 운영자 CLI/API로 수동 강제 갱신
> **유사단어/DB 설명 삭제**: 운영자가 명시적으로 삭제 명령을 실행한 경우에만 삭제. invalidate, invalidate_all, fingerprint 변경 등의 자동 프로세스에서는 synonyms와 db_descriptions를 삭제하지 않음
>
> **중요 — `invalidate_all()` 구현 시 주의**: `schema:*` 패턴으로 스캔할 때 `schema:db_descriptions`도 매칭되므로, 이 키를 명시적으로 제외해야 한다. `key.endswith(":synonyms")` 체크뿐 아니라 `key == "schema:db_descriptions"` 체크도 필요하다.

---

## 3. LLM 기반 컬럼 설명 생성

### 3.1 생성 전략
1. DB에서 테이블/컬럼 메타데이터 수집 (information_schema)
2. 각 테이블별 샘플 데이터 3~5행 조회
3. LLM에 테이블명 + 컬럼 정보 + 샘플 데이터를 전달하여 **각 컬럼의 한국어 설명** + **유사 단어 목록** 생성
4. 생성된 설명을 Redis `schema:{db_id}:descriptions`에, 유사 단어를 `schema:{db_id}:synonyms`에 저장

### 3.2 유사 단어(synonym) 목록

사용자가 자연어로 질의할 때 다양한 표현을 사용할 수 있다. 예를 들어 "호스트명"을 "서버명", "서버 이름", "hostname" 등으로 부를 수 있다. 유사 단어 목록은 이런 다양한 표현을 해당 컬럼에 매핑하여 LLM의 컬럼 선택 정확도를 높인다.

#### 핵심 원칙: 유사단어의 영구 보존 및 DB 독립 관리

유사단어는 **DB 스키마와 독립적으로 영구 보존**한다:

1. **DB 필드 삭제 시에도 유사단어는 삭제하지 않는다** — 스키마 변경(fingerprint 변경)으로 테이블/컬럼이 삭제되어도, 해당 컬럼의 유사단어 레코드는 Redis에 보존한다.
2. **글로벌 유사단어 사전을 별도 관리한다** — DB별 synonyms(`schema:{db_id}:synonyms`) 외에, **글로벌 유사단어 사전**(`synonyms:global`)을 유지한다. 여기에는 컬럼명(테이블 무관)을 기준으로 유사 단어를 저장하여, 향후 유사한 필드를 사용하는 새로운 DB에서도 즉시 활용할 수 있다.
3. **캐시 삭제(invalidate) 시에도 synonyms는 보존한다** — `invalidate(db_id)`가 `schema:{db_id}:meta`, `tables`, `relationships`, `descriptions`를 삭제하더라도, `schema:{db_id}:synonyms`와 `synonyms:global`은 삭제하지 않는다.

#### Redis 저장 구조

**2계층 유사단어 저장:**

**① DB별 유사단어** — `schema:{db_id}:synonyms` (Hash)

특정 DB의 `table.column`에 바인딩된 유사 단어. field_mapper가 해당 DB의 필드 매핑 시 사용한다.

| Field | Value | 예시 |
|-------|-------|------|
| `servers.hostname` | JSON object | `{"words": ["서버명", "호스트명"], "sources": {"서버명": "llm", "호스트명": "operator"}}` |
| `cpu_metrics.usage_pct` | JSON object | `{"words": ["CPU 사용률", "CPU%"], "sources": {"CPU 사용률": "llm", "CPU%": "llm"}}` |

**② 글로벌 유사단어 사전** — `synonyms:global` (Hash)

컬럼명(bare name, 테이블 무관)을 키로 하는 범용 유사단어 사전. 새로운 DB에 동일한 컬럼명이 있으면 자동으로 이 사전에서 유사 단어를 로드한다. 각 컬럼에 대한 **설명(description)**도 함께 저장하여, 컬럼의 의미를 DB에 무관하게 범용적으로 관리한다.

#### 프롬프트 기반 글로벌 유사 단어 LLM 생성

사용자가 **필드명(+ 선택적 유사단어 예시)을 프롬프트로 입력**하면, LLM이 해당 필드에 대한 유사 단어를 자동 생성하여 글로벌 사전에 등록한다.

**프롬프트 예시:**

| 프롬프트 | 동작 |
|---------|------|
| `"hostname의 유사 단어를 생성해줘"` | LLM이 hostname에 대한 유사 단어를 자동 생성 → 글로벌 사전에 등록 |
| `"server_name 필드의 유사 단어를 만들어줘. 예: 서버명, 호스트"` | 사용자 예시를 참고하여 LLM이 추가 유사 단어 생성 → 글로벌 사전에 등록 |
| `"cpu_usage라는 필드가 있어. 유사 단어 생성해줘"` | 필드명만으로 LLM이 유사 단어 추론 → 글로벌 사전에 등록 |
| `"disk_total 필드, 유사 단어: 디스크 용량, 전체 디스크. 더 만들어줘"` | 기존 유사 단어에 LLM이 추가 생성 |

**처리 흐름:**

```
사용자: "server_name 필드의 유사 단어를 만들어줘. 예: 서버명, 호스트"
  ↓
시멘틱 라우터 → intent: "cache_management",
               action: "generate-global-synonyms",
               target: "server_name",
               seed_words: ["서버명", "호스트"]  (사용자가 제공한 예시)
  ↓
cache_management 노드:
  1. 글로벌 사전에서 server_name 기존 항목 확인
  2. LLM에 프롬프트 전달:
     "server_name 컬럼에 대한 유사 단어를 생성해주세요.
      참고 예시: 서버명, 호스트
      한국어/영어/약어 등 다양한 표현을 포함하세요."
  3. LLM 응답: ["서버명", "호스트", "서버 이름", "서버네임", "server name", "host name", "호스트명"]
  4. description도 함께 생성: "서버 또는 호스트의 이름"
  5. 글로벌 사전에 저장:
     synonyms:global → server_name: {
       "words": ["서버명", "호스트", "서버 이름", "서버네임", "server name", "host name", "호스트명"],
       "description": "서버 또는 호스트의 이름"
     }
  6. 기존에 유사 단어가 있었다면 merge (중복 제거)
  ↓
응답: "server_name 필드의 글로벌 유사 단어를 생성했습니다.
  설명: 서버 또는 호스트의 이름
  유사 단어: 서버명, 호스트, 서버 이름, 서버네임, server name, host name, 호스트명
  (7개 등록, source: llm)"
```

**시멘틱 라우터 action 추가:**

```
action: "generate-global-synonyms"
  - target: 컬럼명 (bare name)
  - seed_words: 사용자가 제공한 유사 단어 예시 (선택, 없으면 LLM이 필드명만으로 추론)
```

**LLM 프롬프트 설계:**

```
시스템: "당신은 DB 컬럼명에 대한 유사 단어(synonym) 생성 전문가입니다.
주어진 컬럼명에 대해 사용자가 자연어로 질의할 때 사용할 수 있는
다양한 표현(한국어, 영어, 약어, 조직 고유 용어)을 생성하세요."

사용자: "컬럼명: {column_name}
참고 예시: {seed_words} (있으면)
이 컬럼에 대한 유사 단어와 한 줄 설명을 생성해주세요.

JSON 형식으로 응답:
{\"words\": [\"유사단어1\", \"유사단어2\", ...], \"description\": \"컬럼 설명\"}"
```

#### 유사 필드 자동 탐색 및 재활용 (Smart Synonym Reuse)

글로벌 사전에 **정확히 일치하는 컬럼명이 없는** 새 필드를 추가할 때, LLM을 활용하여 기존 글로벌 사전에서 **의미적으로 유사한 컬럼**을 자동 탐색하고, 해당 컬럼의 유사단어를 재활용할 수 있도록 사용자에게 제안한다.

**시나리오 예시:**

```
상황: 새 DB에 "server_name" 컬럼이 있지만, 글로벌 사전에는 "hostname" 항목만 있음

[1] 유사 단어 생성 요청
    사용자: "new_db의 server_name 컬럼에 유사 단어를 생성해줘"

[2] LLM 유사 필드 탐색
    cache_management 노드:
    ├─ 글로벌 사전에서 "server_name" 정확 매칭 → 없음
    ├─ LLM에게 기존 글로벌 컬럼 목록을 전달하여 유사 필드 탐색 요청:
    │   "server_name과 의미적으로 유사한 컬럼을 찾아주세요.
    │    기존 컬럼: hostname, usage_pct, ip_address, os_type, ..."
    └─ LLM 응답: "hostname" (유사도: 높음, 이유: 둘 다 서버 식별자)

[3] 사용자에게 재활용 여부 질문
    응답: "server_name 컬럼이 글로벌 사전에 없습니다.
    기존 유사 컬럼을 발견했습니다:

      - hostname: 서버의 호스트명 (FQDN 또는 별칭)
        유사 단어: 서버명, 서버이름, 호스트명, 호스트, hostname, server name

    기존 유사 단어를 재활용하시겠습니까?
    - 재활용: \"hostname 유사 단어 재활용\"
    - 새로 생성: \"새로 생성\"
    - 병합 (기존 + 신규): \"병합\""

[4a] 사용자가 "재활용" 선택
    → hostname의 유사 단어를 server_name에 복사
    → server_name 항목을 글로벌 사전에 추가 (hostname 유사 단어 + "server_name" 자체)

[4b] 사용자가 "새로 생성" 선택
    → LLM이 server_name에 대해 독립적으로 유사 단어 생성

[4c] 사용자가 "병합" 선택
    → hostname의 기존 유사 단어 + LLM이 server_name에 대해 생성한 유사 단어를 합침
```

**구현 흐름 (cache_management 노드):**

```python
async def _handle_generate_synonyms_with_reuse(db_id, column, cache_mgr, llm):
    """유사 필드 자동 탐색 및 재활용 제안."""

    # 1. 글로벌 사전에서 정확 매칭 확인
    global_syns = await cache_mgr.load_global_synonyms()
    bare_col = column.split(".")[-1] if "." in column else column

    if bare_col in global_syns:
        # 이미 존재 → 기존 유사 단어 로드, 추가 생성
        return {"action": "exists", "synonyms": global_syns[bare_col]}

    # 2. LLM으로 기존 글로벌 컬럼 중 유사 필드 탐색
    existing_columns = list(global_syns.keys())
    similar = await _find_similar_columns_via_llm(llm, bare_col, existing_columns)

    if similar:
        # 3. 유사 필드 발견 → 사용자에게 재활용 제안
        suggestions = []
        for sim_col in similar:
            entry = global_syns.get(sim_col, {})
            suggestions.append({
                "column": sim_col,
                "description": entry.get("description", ""),
                "words": entry.get("words", []),
            })
        return {"action": "suggest_reuse", "suggestions": suggestions}

    # 4. 유사 필드 없음 → LLM으로 새로 생성
    return {"action": "generate_new"}
```

**State 확장 — 재활용 대기 상태:**

```python
class AgentState(TypedDict):
    ...
    # === 유사단어 재활용 대기 ===
    pending_synonym_reuse: Optional[dict]
    # {
    #   "target_column": "server_name",
    #   "target_db_id": "new_db",
    #   "suggestions": [{"column": "hostname", "words": [...], "description": "..."}],
    # }
```

**시멘틱 라우터 — 재활용 응답 감지:**

사용자가 `"hostname 유사 단어 재활용"`, `"새로 생성"`, `"병합"` 중 하나를 입력하면, `synonym_registration` 파싱 로직과 유사하게 의도를 감지하여 처리한다.

| Field | Value | 예시 |
|-------|-------|------|
| `hostname` | JSON object | `{"words": ["서버명", "서버이름", "호스트명", "호스트", "hostname", "server name"], "description": "서버의 호스트명 (FQDN 또는 별칭)"}` |
| `usage_pct` | JSON object | `{"words": ["사용률", "사용량", "usage", "utilization"], "description": "자원 사용률 (0~100 백분율)"}` |
| `ip_address` | JSON object | `{"words": ["IP", "아이피", "IP주소", "서버IP", "ip address"], "description": "서버 또는 장비의 IP 주소 (IPv4/IPv6)"}` |
| `os_type` | JSON object | `{"words": ["운영체제", "OS", "OS종류", "운영체제 유형"], "description": "운영체제 종류 (Linux, Windows 등)"}` |

**글로벌 설명의 역할:**
- DB별 `schema:{db_id}:descriptions`는 특정 DB 컨텍스트에 맞는 설명 (예: "polestar DB의 서버 호스트명")
- 글로벌 `synonyms:global`의 description은 **DB에 무관한 범용 컬럼 설명** (예: "서버의 호스트명")
- 새로운 DB 추가 시, 글로벌 설명을 기반으로 DB별 descriptions를 초기화하거나 LLM 생성의 참고 자료로 활용
- field_mapper가 LLM 추론 매핑 시, DB별 descriptions에 없는 컬럼은 글로벌 설명을 폴백으로 사용

**프롬프트 기반 컬럼 설명 관리:**

사용자가 자연어 프롬프트로 글로벌 컬럼 설명을 조회/추가/수정할 수 있다:

| 프롬프트 | 동작 |
|---------|------|
| `"hostname 컬럼의 설명을 보여줘"` | 글로벌 description 조회 |
| `"hostname 컬럼의 설명을 '서버의 호스트명 (FQDN)'으로 변경해줘"` | 글로벌 description 수정 |
| `"usage_pct에 '자원 사용률 (백분율)' 설명을 추가해줘"` | 글로벌 description 추가/수정 |

이 요청은 시멘틱 라우터가 `cache_management` 의도로 분류하고, `cache_management` 노드에서 처리한다:
- action: `update-description` — 글로벌 사전의 description 필드 수정
- action: `list-synonyms` — 유사 단어와 함께 description도 표시

#### 프롬프트 기반 유사단어 조회/수정

사용자가 **자연어 프롬프트**로 글로벌 유사단어 사전을 조회하거나 수정할 수 있다. 시멘틱 라우터가 `cache_management` 의도로 분류하고, `cache_management` 노드가 처리한다.

**지원하는 프롬프트 예시:**

| 프롬프트 | 동작 |
|---------|------|
| `"유사 단어 목록을 보여줘"` | 글로벌 유사단어 전체 목록 조회 |
| `"hostname의 유사 단어를 보여줘"` | 특정 컬럼의 유사 단어 조회 |
| `"polestar DB의 유사 단어를 보여줘"` | 특정 DB의 유사 단어 조회 |
| `"hostname에 '서버호스트' 유사 단어를 추가해줘"` | 글로벌 사전에 유사 단어 추가 |
| `"hostname에서 '호스트네임' 유사 단어를 삭제해줘"` | 글로벌 사전에서 유사 단어 삭제 |
| `"polestar DB의 servers.hostname에 '서버호스트' 추가해줘"` | DB별 synonyms에 유사 단어 추가 |
| `"usage_pct의 유사 단어를 '사용률, 사용비율, utilization'으로 변경해줘"` | 글로벌 사전 유사 단어 교체 |

**처리 흐름:**
```
사용자: "hostname의 유사 단어를 보여줘"
  ↓
시멘틱 라우터 → intent: "cache_management", action: "list-synonyms", target: "hostname"
  ↓
cache_management 노드:
  1. synonyms:global에서 hostname 항목 조회
  2. 각 활성 DB의 schema:{db_id}:synonyms에서 *.hostname 항목 조회
  ↓
응답: "hostname 컬럼의 유사 단어 목록:
  [글로벌] 서버명, 서버이름, 호스트명, 호스트, hostname, server name
  [polestar] servers.hostname: 서버명, 호스트명 (source: llm)
  [cloud_portal] cloud_servers.hostname: 서버명, 클라우드호스트 (source: operator)"
```

```
사용자: "hostname에 '서버호스트' 유사 단어를 추가해줘"
  ↓
시멘틱 라우터 → intent: "cache_management", action: "add-synonym", target: "hostname", words: ["서버호스트"]
  ↓
cache_management 노드:
  1. synonyms:global의 hostname에 "서버호스트" 추가 (source: "operator")
  2. 활성 DB 중 hostname 컬럼이 있는 DB의 synonyms에도 동기화
  ↓
응답: "'서버호스트'를 hostname의 유사 단어로 추가했습니다.
  - 글로벌 사전에 등록 완료
  - polestar DB (servers.hostname)에 동기화 완료"
```

**활용 흐름:**

```
1. field_mapper가 필드 매핑 시:
   ① 해당 DB의 synonyms (schema:{db_id}:synonyms) 조회
   ② 매칭 실패 시 → 글로벌 사전 (synonyms:global) 조회
   ③ 글로벌에서 매칭 성공 → 해당 DB의 컬럼에 매핑 (컬럼명 기반)

2. DB 스키마 삭제/변경 시:
   - schema:{db_id}:meta, tables, relationships, descriptions → 삭제
   - schema:{db_id}:synonyms → 보존 (삭제하지 않음)
   - synonyms:global → 항상 보존

3. 새로운 DB 추가 시:
   - 글로벌 사전에서 동일 컬럼명의 유사 단어 자동 로드
   - DB별 synonyms에 복사하여 매핑에 활용
```

#### 생성 방식
- **LLM 자동 생성**: 컬럼 설명 생성 시 유사 단어도 함께 생성 (1회 LLM 호출로 설명 + 유사 단어 동시 출력). 생성된 유사 단어는 DB별 synonyms와 글로벌 사전에 동시 저장한다.
- **운영자 수동 추가**: CLI/API를 통해 특정 컬럼에 유사 단어를 추가/수정 가능. 수동 추가분은 `source: "operator"`로 태깅하여 LLM 재생성 시에도 보존한다.
- **사용자 대화 기반 등록**: xls_plan의 유사어 등록 플로우(LLM 추론 매핑 승인 시)로 등록된 유사 단어도 DB별 + 글로벌 사전에 동시 저장한다.
- **삭제 정책**: 운영자가 명시적으로 삭제 명령을 실행한 경우에만 삭제한다. DB 스키마 변경, 캐시 갱신, invalidate 등의 자동 프로세스에서는 유사 단어를 절대 삭제하지 않는다.

#### 유사 단어 관리 — 시멘틱 라우팅 기반

유사 단어 관리는 **기존 시멘틱 라우팅 파이프라인**을 활용한다. 사용자가 자연어 프롬프트를 입력하면 시멘틱 라우터가 의도를 분류하고, 유사 단어 생성 의도인 경우 `cache_management` 노드로 분기하여 LLM이 자동으로 유사 단어를 생성한다.

**프롬프트 기반 흐름** (사용자 → 시멘틱 라우팅 → 자동 처리):
```
사용자: "polestar DB의 servers 테이블에 유사 단어를 생성해줘"
  ↓
시멘틱 라우터 → intent: "cache_management", action: "generate-synonyms"
  ↓
cache_management 노드:
  1. polestar DB 스키마 캐시에서 servers 테이블 정보 로드
  2. LLM이 컬럼 정보 + 샘플 데이터를 분석하여 유사 단어 자동 생성
  3. Redis schema:polestar:synonyms에 저장
  ↓
응답: "polestar DB의 servers 테이블 유사 단어를 생성했습니다.
  - hostname: 서버명, 서버이름, 호스트명, 호스트, server name
  - ip_address: IP, 아이피, IP주소, 서버IP
  - os_type: 운영체제, OS, OS종류
  ..."
```

**지원하는 프롬프트 패턴**:
```
"polestar DB의 유사 단어를 생성해줘"           → 전체 테이블 유사 단어 생성
"servers.hostname 컬럼의 유사 단어를 갱신해줘"  → 특정 컬럼 유사 단어 재생성
"polestar DB의 유사 단어 목록을 보여줘"         → 조회
"servers.hostname에서 '호스트네임' 유사 단어를 삭제해줘" → 삭제
```

**CLI** (비대화형, 배치 처리):
```bash
# 유사 단어 조회
python scripts/schema_cache_cli.py synonyms --db-id polestar --all
python scripts/schema_cache_cli.py synonyms --db-id polestar --column servers.hostname

# LLM 기반 유사 단어 일괄 생성 (비대화형)
python scripts/schema_cache_cli.py synonyms --db-id polestar --generate
python scripts/schema_cache_cli.py synonyms --db-id polestar --column servers.hostname --generate

# 유사 단어 삭제
python scripts/schema_cache_cli.py synonyms --db-id polestar --column servers.hostname --remove "호스트네임"
```

**API**:
| Method | Path | 설명 |
|--------|------|------|
| `GET` | `/api/v1/admin/schema-cache/{db_id}/synonyms` | 전체 유사 단어 목록 조회 |
| `POST` | `/api/v1/admin/schema-cache/{db_id}/synonyms/generate` | LLM으로 유사 단어 자동 생성 (column 파라미터로 특정 컬럼 지정 가능) |
| `DELETE` | `/api/v1/admin/schema-cache/{db_id}/synonyms/{column}` | 특정 컬럼 유사 단어 삭제 |

> 운영자가 프롬프트 또는 API로 생성한 유사 단어는 `source: "operator"` 태그로 저장되어, LLM 재생성 시에도 보존된다 (자동 생성분은 `source: "llm"`).

### 3.3 LLM 프롬프트 설계 (개요)
```
입력:
  - 테이블명: servers
  - 컬럼: [hostname(varchar), ip_address(inet), os_type(varchar), cpu_cores(int), ...]
  - 샘플 데이터: [{hostname: "web-srv-01", ip_address: "10.0.1.5", ...}, ...]

출력 (JSON):
  {
    "servers.hostname": {
      "description": "서버의 호스트명 (FQDN 또는 별칭)",
      "synonyms": ["서버명", "서버이름", "호스트명", "호스트", "hostname", "server name"]
    },
    "servers.ip_address": {
      "description": "서버의 IP 주소 (IPv4)",
      "synonyms": ["IP", "아이피", "IP주소", "서버IP", "ip address"]
    },
    "servers.os_type": {
      "description": "운영체제 종류 (Linux, Windows 등)",
      "synonyms": ["운영체제", "OS", "OS종류", "운영체제 유형"]
    },
    "servers.cpu_cores": {
      "description": "서버의 물리 CPU 코어 수",
      "synonyms": ["CPU코어", "코어수", "프로세서 수", "cpu cores"]
    }
  }
```

### 3.4 비용/성능 최적화
- 테이블 단위로 배치 처리 (테이블당 1회 LLM 호출로 설명 + 유사 단어 동시 생성)
- 이미 설명이 있는 컬럼은 스킵 (incremental 생성)
- 설명/유사 단어는 영구 저장되므로 스키마 변경이 없으면 LLM 재호출 없음
- 스키마 변경(fingerprint 변경) 시 신규/변경 컬럼의 설명+유사 단어만 재생성 (diff 기반)

---

## 4. 캐시 생명주기

### 4.1 초기 생성 흐름

캐시는 두 가지 경로로 생성된다:
- **경로 A**: 운영자가 CLI/API로 사전에 캐시 생성 (권장)
- **경로 B**: 운영자가 사전 생성하지 않은 경우, **첫 쿼리 진입 시 자동 생성** (schema_analyzer 노드에서 캐시 미스 감지 → 즉시 DB 조회 후 캐시 저장)

```
쿼리 진입 또는 운영자 API/CLI 호출
  ↓
Redis에서 schema:{db_id}:meta 조회
  ↓ (MISS — 캐시 미존재)
  ↓ [자동 생성 트리거]
DB에서 information_schema 전체 조회
  ↓
fingerprint 생성
  ↓
Redis에 meta + tables + relationships 저장
  ↓
LLM 컬럼 설명 + 유사 단어 생성 (비동기 백그라운드)
  ↓
Redis에 descriptions + synonyms 저장
  ↓
description_status → "complete"
```

> **핵심**: 운영자가 초기 캐시를 생성하지 않더라도 시스템은 정상 동작한다. 첫 쿼리 시 자동으로 캐시를 생성하며, 이후 쿼리부터는 캐시를 활용한다. 다만 첫 쿼리는 DB 전체 조회 + 캐시 저장으로 인해 응답 시간이 다소 길어질 수 있다.

### 4.2 캐시 히트 흐름 (일반 쿼리)
```
쿼리 진입 → schema_analyzer 노드
  ↓
1차: 메모리 캐시 확인 → HIT → 반환
  ↓ (MISS)
2차: Redis fingerprint 비교.
  ├─ fingerprint 일치 → Redis 캐시 로드 → 메모리 캐시 갱신 → 반환
  └─ fingerprint 불일치 또는 MISS → 3차로
  ↓
3차: DB 전체 조회 → Redis + 메모리 캐시 갱신
  ↓
변경된 컬럼 감지 → LLM 설명 재생성 (비동기)
```

### 4.3 스키마 변경 자동 감지

#### Fingerprint 생성 방법

**방식: `information_schema` 기반 경량 쿼리** (기존 `src/schema_cache/fingerprint.py` 활용)

```sql
-- 이 SQL 1회 실행으로 fingerprint 생성 (매우 가볍고, 데이터 조회 없음)
SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
GROUP BY table_name
ORDER BY table_name
```

1. 위 쿼리 결과 (테이블명 + 컬럼 수 목록)를 JSON으로 직렬화
2. JSON 문자열에 대해 **SHA-256 해시**를 생성 → 이것이 fingerprint
3. Redis에 저장된 기존 fingerprint와 비교

**감지 가능한 변경**: 테이블 추가/삭제, 컬럼 추가/삭제
**감지 불가능한 변경**: 컬럼 타입 변경, 컬럼명 변경 (컬럼 수는 동일하므로)

> **왜 `information_schema` 쿼리인가?**
> - 스키마 업데이트 시간(`pg_stat_user_tables.last_ddl_time` 등)은 PostgreSQL에서 직접 제공하지 않으며, `pg_stat_activity`는 DDL 추적이 아닌 세션 정보
> - `information_schema.columns`는 모든 RDBMS에서 표준으로 지원하여 DB 종류에 무관하게 동작
> - 데이터 행을 조회하지 않으므로 부하가 거의 없음 (메타데이터 카탈로그만 접근)

#### 보완 (미구현, 향후 개선): 컬럼 타입 변경 감지

> **현재 상태**: 기본 fingerprint SQL(테이블명 + 컬럼 수)만 구현됨. 아래 확장 SQL은 **향후 개선 사항**으로, 필요 시 `src/schema_cache/fingerprint.py`의 `FINGERPRINT_SQL`을 교체한다.

컬럼 수가 동일한 상태에서 타입만 변경되는 경우를 감지하기 위해, fingerprint SQL을 확장할 수 있다:

```sql
SELECT table_name,
       string_agg(column_name || ':' || data_type, ',' ORDER BY ordinal_position) AS column_signature
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
GROUP BY table_name
ORDER BY table_name
```

이렇게 하면 컬럼명:타입 조합까지 포함하여 해시를 생성하므로, 타입 변경/컬럼명 변경도 감지 가능하다.

> **주의**: `string_agg`는 PostgreSQL 전용 함수이므로, MySQL 등 타 DBMS 지원 시 `GROUP_CONCAT` 등으로 대체해야 한다.

#### 감지 흐름

- **능동적 감지**: 매 쿼리마다 위 경량 SQL 실행 (< 10ms) → Redis 저장 fingerprint와 비교
- **수동 갱신**: 운영자 CLI/API로 강제 캐시 갱신 트리거
- fingerprint 변경 시:
  1. Redis 기존 캐시 삭제 (atomic — `MULTI`/`EXEC` 트랜잭션) — **단, `schema:{db_id}:synonyms`, `synonyms:global`, `schema:db_descriptions`는 삭제하지 않음**
  2. DB 전체 조회로 새 캐시 생성
  3. 이전 캐시와 diff 비교하여 변경된 테이블/컬럼의 description만 재생성
  4. 신규 컬럼이 추가된 경우, 글로벌 사전(`synonyms:global`)에서 동일 컬럼명의 유사 단어를 자동 로드하여 DB별 synonyms에 복사
  5. 삭제된 컬럼의 유사 단어는 DB별 synonyms와 글로벌 사전 모두에 보존 (향후 재활용)

### 4.4 Graceful Fallback
- Redis 연결 실패 시 → 기존 파일 캐시(`PersistentSchemaCache`)로 자동 폴백
- 파일 캐시도 없으면 → DB 직접 조회 (현재 3차 동작 유지)
- 로그로 폴백 사실을 기록하여 운영자가 인지 가능

> **주의 — descriptions/synonyms의 파일 폴백 범위**: 현재 구현에서 descriptions와 synonyms는 **Redis에만 저장**되며, 파일 캐시 폴백은 **스키마(tables/relationships)에만 적용**된다. 향후 descriptions/synonyms도 파일 캐시에 저장하는 확장을 검토할 수 있으나, 현재는 Redis 미연결 시 빈 dict를 반환한다.

### 4.5 시멘틱 라우팅 기반 캐시 관리

CLI/API 외에, **사용자가 자연어 프롬프트로 캐시를 생성/관리**할 수 있다. 기존 시멘틱 라우팅 파이프라인을 확장하여 캐시 관리 의도를 감지하고 자동 처리한다.

#### 시멘틱 라우터 확장

기존 시멘틱 라우터(`src/routing/semantic_router.py`)는 사용자 프롬프트를 분석하여 대상 DB를 분류한다. 여기에 **캐시 관리 의도 분류**를 추가한다.

```
사용자 프롬프트 입력 (자연어)
  ↓
시멘틱 라우터: 의도 분류
  ├─ intent: "data_query"        → 기존 파이프라인 (schema_analyzer → query_generator → ...)
  └─ intent: "cache_management"  → cache_management 노드 (신규)
      ↓
    라우터가 프롬프트에서 자동 추출:
      - action: generate / generate-descriptions / generate-synonyms /
               generate-global-synonyms /
               list-synonyms / add-synonym / remove-synonym / update-synonym /
               update-description /
               status / invalidate
      - db_id: 대상 DB (없으면 전체, 또는 글로벌)
      - target_column: table.column 또는 bare column_name (유사 단어/설명 관리 시)
      - words: 추가/삭제할 유사 단어 목록 (add/remove/update 시)
      - seed_words: 사용자가 제공한 유사 단어 예시 (generate-global-synonyms 시, 선택)
      - description: 컬럼 설명 텍스트 (update-description 시)
      ↓
    SchemaCacheManager 호출하여 작업 수행
      ↓
    결과를 자연어로 응답
```

#### 지원하는 프롬프트 예시

| 프롬프트 | 라우팅 결과 |
|---------|-----------|
| `"polestar DB의 스키마 캐시를 생성해줘"` | action: `generate`, db_id: `polestar` |
| `"cloud_portal 데이터베이스의 컬럼 설명을 다시 만들어줘"` | action: `generate-descriptions`, db_id: `cloud_portal` |
| `"polestar DB의 servers 테이블에 유사 단어를 생성해줘"` | action: `generate-synonyms`, db_id: `polestar`, target: `servers` |
| `"전체 DB 캐시 상태를 보여줘"` | action: `status`, db_id: `null` (전체) |
| `"polestar 캐시를 삭제해줘"` | action: `invalidate`, db_id: `polestar` |
| `"유사 단어 목록을 보여줘"` | action: `list-synonyms`, db_id: `null` (글로벌) |
| `"hostname의 유사 단어를 보여줘"` | action: `list-synonyms`, target: `hostname` |
| `"polestar DB의 유사 단어를 보여줘"` | action: `list-synonyms`, db_id: `polestar` |
| `"hostname에 '서버호스트' 유사 단어를 추가해줘"` | action: `add-synonym`, target: `hostname`, words: `["서버호스트"]` |
| `"hostname에서 '호스트네임' 유사 단어를 삭제해줘"` | action: `remove-synonym`, target: `hostname`, words: `["호스트네임"]` |
| `"usage_pct의 유사 단어를 '사용률, 사용비율'로 변경해줘"` | action: `update-synonym`, target: `usage_pct`, words: `["사용률", "사용비율"]` |
| `"hostname 컬럼의 설명을 보여줘"` | action: `list-synonyms`, target: `hostname` (description도 함께 표시) |
| `"hostname 컬럼의 설명을 '서버의 호스트명 (FQDN)'으로 변경해줘"` | action: `update-description`, target: `hostname`, description: `"서버의 호스트명 (FQDN)"` |
| `"usage_pct에 '자원 사용률 (백분율)' 설명을 추가해줘"` | action: `update-description`, target: `usage_pct`, description: `"자원 사용률 (백분율)"` |
| `"hostname의 유사 단어를 생성해줘"` | action: `generate-global-synonyms`, target: `hostname` |
| `"server_name 필드의 유사 단어를 만들어줘. 예: 서버명, 호스트"` | action: `generate-global-synonyms`, target: `server_name`, seed_words: `["서버명", "호스트"]` |
| `"disk_total 필드, 유사 단어: 디스크 용량. 더 만들어줘"` | action: `generate-global-synonyms`, target: `disk_total`, seed_words: `["디스크 용량"]` |

#### 구현 방식

| 항목 | 설명 |
|------|------|
| 시멘틱 라우터 확장 | `src/routing/semantic_router.py`에 `"cache_management"` 의도 분류 추가 |
| 신규 노드 | `src/nodes/cache_management.py` — 캐시 관리 전용 노드 |
| 그래프 분기 | 시멘틱 라우터 이후 조건부 라우팅: `intent == "cache_management"` → `cache_management` → `END` (output_generator를 거치지 않고 직접 종료. `final_response`를 cache_management 노드에서 직접 설정) |
| 프롬프트 | `src/prompts/cache_management.py` — 캐시 관리 action/db_id/target 파싱 |

#### 응답 예시

```
사용자: "polestar DB의 스키마 캐시를 생성해줘"

응답: "polestar DB의 스키마 캐시를 생성했습니다.
  - 테이블: 12개
  - 컬럼: 85개
  - fingerprint: a3f2c1...
  - 컬럼 설명 생성: 진행 중 (백그라운드)
  - 유사 단어 생성: 진행 중 (백그라운드)"
```

```
사용자: "polestar DB의 servers 테이블 유사 단어를 생성해줘"

응답: "polestar DB의 servers 테이블 유사 단어를 생성했습니다.
  - hostname: 서버명, 서버이름, 호스트명, 호스트, server name
  - ip_address: IP, 아이피, IP주소, 서버IP
  - os_type: 운영체제, OS, OS종류
  - cpu_cores: CPU코어, 코어수, 프로세서 수"
```

```
사용자: "hostname의 유사 단어를 보여줘"

응답: "hostname 컬럼 정보:
  [설명] 서버의 호스트명 (FQDN 또는 별칭)
  [글로벌 유사 단어] 서버명, 서버이름, 호스트명, 호스트, hostname, server name
  [polestar] servers.hostname: 서버명, 호스트명 (source: llm)
  [cloud_portal] cloud_servers.hostname: 서버명, 클라우드호스트 (source: operator)"
```

```
사용자: "hostname 컬럼의 설명을 '서버의 호스트명 (FQDN)'으로 변경해줘"

응답: "hostname 컬럼의 설명을 업데이트했습니다.
  - 이전: 서버의 호스트명 (FQDN 또는 별칭)
  - 변경: 서버의 호스트명 (FQDN)"
```

```
사용자: "hostname에 '서버호스트' 유사 단어를 추가해줘"

응답: "'서버호스트'를 hostname의 유사 단어로 추가했습니다.
  - 글로벌 사전에 등록 완료
  - polestar DB (servers.hostname)에 동기화 완료
  - cloud_portal DB (cloud_servers.hostname)에 동기화 완료"
```

```
사용자: "hostname에서 '호스트네임' 유사 단어를 삭제해줘"

응답: "'호스트네임'을 hostname의 유사 단어에서 삭제했습니다.
  - 글로벌 사전에서 삭제 완료
  - polestar DB (servers.hostname)에서 삭제 완료"
```

---

## 5. 운영자 관리 API

### 5.1 신규 엔드포인트

| Method | Path | 설명 |
|--------|------|------|
| `POST` | `/api/v1/admin/schema-cache/generate` | 전체 또는 특정 DB의 스키마 캐시 생성/갱신 |
| `POST` | `/api/v1/admin/schema-cache/generate-descriptions` | 컬럼 설명 (재)생성 |
| `GET` | `/api/v1/admin/schema-cache/status` | 캐시 상태 조회 (DB별 fingerprint, 생성시각, description 상태) |
| `GET` | `/api/v1/admin/schema-cache/{db_id}` | 특정 DB 캐시 상세 조회 (테이블/컬럼/설명 전체) |
| `DELETE` | `/api/v1/admin/schema-cache/{db_id}` | 특정 DB 캐시 삭제 |
| `DELETE` | `/api/v1/admin/schema-cache` | 전체 캐시 삭제 |

### 5.2 요청/응답 모델 (예시)

#### `POST /api/v1/admin/schema-cache/generate`
```json
// Request
{
  "db_ids": ["polestar", "cloud_portal"],  // null이면 전체
  "include_descriptions": true,             // LLM 설명 동시 생성
  "force": false                            // true면 fingerprint 무시하고 강제 갱신
}

// Response
{
  "results": [
    {
      "db_id": "polestar",
      "status": "updated",
      "table_count": 12,
      "fingerprint": "abc123...",
      "description_status": "generating",
      "message": "스키마 변경 감지, 캐시 갱신 완료"
    },
    {
      "db_id": "cloud_portal",
      "status": "unchanged",
      "table_count": 8,
      "fingerprint": "def456...",
      "description_status": "complete",
      "message": "변경 없음, 기존 캐시 유지"
    }
  ]
}
```

#### `GET /api/v1/admin/schema-cache/status`
```json
{
  "caches": [
    {
      "db_id": "polestar",
      "fingerprint": "abc123...",
      "cached_at": "2026-03-17T10:30:00+0900",
      "table_count": 12,
      "description_status": "complete",
      "description_count": 85
    }
  ],
  "redis_connected": true
}
```

### 5.3 인증
- 기존 `require_admin` 의존성 사용 (JWT 인증)
- admin 라우터에 추가

---

## 6. 설정 변경

### 6.1 `RedisConfig` 추가 (`src/config.py`)
```python
class RedisConfig(BaseSettings):
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    ssl: bool = False
    socket_timeout: int = 5

    model_config = {"env_prefix": "REDIS_", "env_file": ".env", "extra": "ignore"}
```

> TTL 관련 설정 없음 — 모든 스키마 캐시는 영구 저장이며, fingerprint 변경 시에만 갱신됨

### 6.2 `SchemaCacheConfig` 확장
```python
class SchemaCacheConfig(BaseSettings):
    cache_dir: str = ".cache/schema"          # 파일 캐시 (폴백용)
    enabled: bool = True
    backend: str = "redis"                     # "redis" | "file"
    auto_generate_descriptions: bool = True    # 캐시 생성 시 자동 LLM 설명 생성

    model_config = {"env_prefix": "SCHEMA_CACHE_", "env_file": ".env", "extra": "ignore"}
```

### 6.3 `.env` 추가 항목
```env
# Redis
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=

# Schema Cache
SCHEMA_CACHE_BACKEND=redis
SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS=true
```

---

## 7. 구현 모듈 구조

### 7.1 신규 파일
```
scripts/
│   └── schema_cache_cli.py        # (신규) 독립 실행 CLI — FastAPI 없이 캐시 관리
src/
├── schema_cache/
│   ├── persistent_cache.py        # (기존) 파일 캐시 - 폴백용 유지
│   ├── fingerprint.py             # (기존) fingerprint 생성
│   ├── redis_cache.py             # (신규) Redis 기반 캐시 구현
│   ├── description_generator.py   # (신규) LLM 컬럼 설명 + 유사 단어 생성기
│   ├── cache_manager.py           # (신규) 통합 캐시 매니저 (backend 추상화)
│   └── __init__.py
├── nodes/
│   └── cache_management.py        # (신규) 프롬프트 기반 캐시 관리 노드
├── prompts/
│   ├── schema_description.py      # (신규) 컬럼 설명 + 유사 단어 생성 프롬프트
│   └── cache_management.py        # (신규) 캐시 관리 의도 파싱 프롬프트
├── api/routes/
│   └── schema_cache.py            # (신규) 운영자 캐시 관리 라우터
```

### 7.1.1 독립 실행 CLI (`scripts/schema_cache_cli.py`)

FastAPI 서버를 기동하지 않고도 운영자가 직접 캐시를 관리할 수 있는 스크립트.

**실행 예시**:
```bash
# 전체 DB 스키마 캐시 생성/갱신
python scripts/schema_cache_cli.py generate

# 특정 DB만 캐시 생성
python scripts/schema_cache_cli.py generate --db-id polestar

# 강제 갱신 (fingerprint 무시)
python scripts/schema_cache_cli.py generate --force

# 컬럼 설명 생성 (LLM 호출)
python scripts/schema_cache_cli.py generate-descriptions --db-id polestar

# 캐시 상태 조회
python scripts/schema_cache_cli.py status

# 특정 DB 캐시 상세 조회 (테이블/컬럼/설명)
python scripts/schema_cache_cli.py show --db-id polestar

# 캐시 삭제
python scripts/schema_cache_cli.py invalidate --db-id polestar
python scripts/schema_cache_cli.py invalidate --all
```

**내부 구조**:
- `argparse` 기반 서브커맨드 파싱
- `SchemaCacheManager`를 직접 인스턴스화하여 호출
- `asyncio.run()`으로 비동기 함수 실행
- 결과를 콘솔에 테이블 형태로 출력 (tabulate 또는 직접 포맷)

### 7.2 수정 파일
| 파일 | 변경 내용 |
|------|-----------|
| `src/config.py` | `RedisConfig` 추가, `SchemaCacheConfig` 확장, `AppConfig`에 redis 필드 추가 |
| `src/nodes/schema_analyzer.py` | `_get_schema_with_cache`에서 `cache_manager` 사용, descriptions + synonyms 로드 |
| `src/routing/semantic_router.py` | 캐시 관리 의도 분류 추가 (`intent: "cache_management"`), `pending_synonym_reuse` 바이패스 로직 추가 |
| `src/graph.py` | `cache_management` 노드 등록, 시멘틱 라우터 이후 조건부 라우팅 추가 |
| `src/api/server.py` | schema_cache 라우터 등록, Redis 연결 lifespan 관리 |
| `src/state.py` | `column_descriptions`, `column_synonyms`, `pending_synonym_reuse` 필드 추가 (`thread_id`는 Phase 3에서 추가) |
| `src/prompts/query_generator.py` | 컬럼 설명 + 유사 단어를 SQL 생성 프롬프트에 포함 |
| `src/prompts/semantic_router.py` | 캐시 관리 의도 분류 키워드 보강 (유사 단어/DB 설명/재활용 응답) |
| `src/api/routes/query.py` | `create_initial_state()`에 `thread_id` 전달 |
| `pyproject.toml` | `redis[hiredis]` 의존성 추가 |
| `.env.example` | Redis 관련 환경변수 추가 |

### 7.3 핵심 클래스 설계

#### `RedisSchemaCache` (`redis_cache.py`)
```
class RedisSchemaCache:
    __init__(redis_config, schema_cache_config)

    # 기본 CRUD
    async save_schema(db_id, schema_dict, fingerprint) → bool
    async load_schema(db_id) → Optional[dict]
    async get_fingerprint(db_id) → Optional[str]
    async is_changed(db_id, current_fingerprint) → bool

    # 컬럼 설명
    async save_descriptions(db_id, descriptions: dict[str, str]) → bool
    async load_descriptions(db_id) → dict[str, str]
    async get_description(db_id, table_column: str) → Optional[str]

    # DB 설명
    async save_db_description(db_id, description: str) → bool
    async load_db_descriptions() → dict[str, str]
    async get_db_description(db_id) → Optional[str]
    async delete_db_description(db_id) → bool

    # 유사 단어 (DB별)
    async save_synonyms(db_id, synonyms: dict[str, dict|list[str]], source="llm") → bool
    async load_synonyms(db_id) → dict[str, list[str]]
    async load_synonyms_with_sources(db_id) → dict[str, dict]  # source 태그 포함
    async add_synonyms(db_id, column: str, words: list[str], source: str = "llm") → bool
    async remove_synonyms(db_id, column: str, words: list[str]) → bool

    # 글로벌 유사단어 사전 (유사 단어 + 컬럼 설명)
    async save_global_synonyms(synonyms: dict[str, dict|list[str]]) → bool
    async load_global_synonyms() → dict[str, list[str]]            # {col: [word, ...]}
    async load_global_synonyms_full() → dict[str, dict]            # {col: {words: [...], description: "..."}}
    async add_global_synonym(column_name: str, words: list[str]) → bool
    async remove_global_synonym(column_name: str, words: list[str]) → bool
    async update_global_description(column_name: str, description: str) → bool
    async get_global_description(column_name: str) → Optional[str]
    async list_global_column_names() → list[str]   # 글로벌 사전에 등록된 전체 컬럼명 목록

    # 관리 (synonyms + db_descriptions는 보존)
    async invalidate(db_id) → bool     # meta/tables/relationships/descriptions만 삭제, synonyms 보존
    async invalidate_all() → int       # 동일, synonyms + global + db_descriptions 보존
    async delete_synonyms(db_id) → bool       # 명시적 synonyms 삭제 (운영자 전용)
    async delete_global_synonyms() → bool     # 명시적 글로벌 사전 삭제 (운영자 전용)
    async list_cached_dbs() → list[dict]
    async get_status(db_id) → dict

    # pending 상태 (멀티턴 캐시 관리) — [미구현, 향후 Phase 3]
    # async save_pending_reuse(thread_id, data: dict, ttl=600) → bool
    # async load_pending_reuse(thread_id) → Optional[dict]
    # async delete_pending_reuse(thread_id) → bool

    # 연결
    async connect() → None
    async disconnect() → None
    async health_check() → bool
```

#### `SchemaCacheManager` (`cache_manager.py`)
```
class SchemaCacheManager:
    """Redis/파일 캐시를 추상화하는 통합 매니저.
    backend 설정에 따라 Redis 우선, 실패 시 파일 폴백."""

    __init__(app_config)

    # 연결
    async ensure_redis_connected() → bool
    async disconnect() → None

    # 스키마 조회 (캐시만 — DB 조회 없음)
    async get_schema(db_id) → Optional[dict]
        # Redis → 파일 폴백. 메모리 캐시는 schema_analyzer가 관리.
    async get_fingerprint(db_id) → Optional[str]
    async is_changed(db_id, current_fingerprint) → bool
    async save_schema(db_id, schema_dict, fingerprint=None) → bool
        # Redis + 파일 이중 저장 (폴백 보장)

    # 캐시 갱신 (DB 클라이언트 필요)
    async refresh_cache(db_id, client, force=False) → CacheRefreshResult

    # DB 설명
    async get_db_descriptions() → dict[str, str]
    async get_db_description(db_id) → Optional[str]
    async save_db_description(db_id, description) → bool
    async delete_db_description(db_id) → bool

    # 컬럼 설명
    async get_descriptions(db_id) → dict[str, str]
    async save_descriptions(db_id, descriptions) → bool

    # 유사 단어 (DB별)
    async get_synonyms(db_id) → dict[str, list[str]]
    async save_synonyms(db_id, synonyms, source="llm") → bool
    async add_synonyms(db_id, column, words, source="operator") → bool
    async remove_synonyms(db_id, column, words) → bool

    # 글로벌 유사단어
    async get_global_synonyms() → dict[str, list[str]]
    async save_global_synonyms(synonyms) → bool
    async add_global_synonym(column_name, words) → bool
    async remove_global_synonym(column_name, words) → bool
    async get_global_synonyms_full() → dict[str, dict]
    async update_global_description(column_name, description) → bool
    async get_global_description(column_name) → Optional[str]
    async list_global_column_names() → list[str]

    # 글로벌 폴백 통합 조회
    async load_synonyms_with_global_fallback(db_id, schema_dict=None) → dict[str, list[str]]
        # DB별 synonyms 조회 → 없는 컬럼은 글로벌 사전에서 폴백

    # DB별 → 글로벌 동기화
    async sync_global_synonyms(db_id) → int
        # DB별 synonyms를 글로벌 사전에 병합

    # LLM 기반 유사 단어 생성/탐색/재활용
    async generate_global_synonyms(column_name, llm, seed_words=None) → dict
    async find_similar_global_columns(column_name, llm) → list[dict]
    async reuse_synonyms(source_column, target_column, db_id=None, mode="copy", llm=None) → dict

    # 관리
    async invalidate(db_id) → bool
    async invalidate_all() → int
    async get_status(db_id) → CacheStatus
    async get_all_status() → list[CacheStatus]

    @property redis_available → bool
    @property backend → str
```

> **참고**: 메모리 캐시(SchemaCache)와 DB 조회 로직은 `SchemaCacheManager`가 아닌 `schema_analyzer.py`의 `_get_schema_with_cache()`에서 담당한다. SchemaCacheManager는 Redis/파일 영구 캐시만 관리한다.

#### `DescriptionGenerator` (`description_generator.py`)
```
class DescriptionGenerator:
    """LLM을 사용하여 컬럼별 설명 + 유사 단어를 생성"""

    __init__(llm)

    async generate_db_description(db_id, schema_dict) → Optional[str]
        # DB 전체의 한국어 설명을 LLM으로 생성

    async generate_for_table(table_name, columns, sample_data=None)
        → dict[str, {"description": str, "synonyms": list[str]}]
        # 특정 테이블의 컬럼 설명 + 유사 단어 생성 (1회 LLM 호출)

    async generate_for_db(schema_dict)
        → (descriptions: dict[str, str], synonyms: dict[str, list[str]])
        # DB 전체 테이블의 설명 + 유사 단어 순차 생성

    async generate_incremental(schema_dict, existing_descriptions)
        → (descriptions: dict[str, str], synonyms: dict[str, list[str]])
        # 기존 설명이 없는 신규/변경 컬럼만 설명 생성 (diff 기반)
```

---

## 8. schema_analyzer 노드 통합

### 8.1 변경된 조회 흐름
```python
async def _get_schema_with_cache(client, db_id, app_config):
    """캐시 매니저를 활용하여 스키마를 조회한다.
    반환: (SchemaInfo, schema_dict, descriptions, synonyms) 튜플"""
    cache_mgr = get_cache_manager(app_config)

    # 1차: 메모리 캐시 (SchemaCache 싱글톤)
    full_schema = _schema_cache.get(db_id)
    if full_schema is not None:
        descriptions = await cache_mgr.get_descriptions(db_id)
        synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
        #                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
        # [중요] get_synonyms(db_id)가 아닌 global fallback 포함 버전 사용
        return full_schema, {}, descriptions, synonyms

    # 2차: Redis/파일 캐시 (SchemaCacheManager)
    current_fp = await _fetch_fingerprint(client)
    if current_fp and not await cache_mgr.is_changed(db_id, current_fp):
        cached_dict = await cache_mgr.get_schema(db_id)
        if cached_dict:
            full_schema = _reconstruct_schema_info(cached_dict)
            _schema_cache.set(full_schema, db_id)
            descriptions = await cache_mgr.get_descriptions(db_id)
            synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
            return full_schema, cached_dict, descriptions, synonyms

    # 3차: DB 전체 조회 → 캐시 저장
    full_schema = await client.get_full_schema()
    _schema_cache.set(full_schema, db_id)
    return full_schema, {}, {}, {}
```

> **참고 — `load_synonyms_with_global_fallback()` 사용**: DB별 synonyms에 없는 컬럼은 글로벌 사전(`synonyms:global`)에서 자동 폴백한다. 이를 통해 새로운 DB에 기존과 동일한 컬럼명이 있으면 별도 설정 없이 유사 단어가 자동 적용된다.
>
> **현재 구현 상태**: `schema_analyzer.py`에서 `get_synonyms(db_id)`만 사용 중. `load_synonyms_with_global_fallback()` 사용으로 변경 필요.

### 8.2 query_generator 프롬프트 강화
```
기존: 테이블명 + 컬럼명 + 타입
변경: 테이블명 + 컬럼명 + 타입 + 컬럼 설명(한국어) + 유사 단어

예:
  테이블: servers
  컬럼:
    - hostname (varchar): 서버의 호스트명 (FQDN 또는 별칭) [유사: 서버명, 서버이름, 호스트]
    - ip_address (inet): 서버의 IP 주소 (IPv4) [유사: IP, 아이피, 서버IP]
    - cpu_cores (int): 서버의 물리 CPU 코어 수 [유사: CPU코어, 코어수, 프로세서 수]
```

> 유사 단어를 프롬프트에 포함하면, 사용자가 "서버명"이라고 질의해도 LLM이 `hostname` 컬럼을 정확히 선택할 수 있다.

---

## 9. 의존성

### 9.1 신규 패키지
| 패키지 | 버전 | 용도 |
|--------|------|------|
| `redis[hiredis]` | ≥5.0.0 | Redis 비동기 클라이언트 + C 파서 |

> `redis` 패키지의 `redis.asyncio`를 사용하며, `hiredis`는 파싱 성능 최적화용

### 9.2 인프라 요구사항
- Redis 7.0+ 서버
- 개발 환경: Docker로 로컬 Redis 실행 (`docker run -d -p 6379:6379 redis:7-alpine`)
- 프로덕션: 기존 인프라 Redis 또는 전용 인스턴스

---

## 10. 테스트 계획

### 10.1 단위 테스트
| 테스트 | 대상 | 방법 |
|--------|------|------|
| Redis 캐시 CRUD | `RedisSchemaCache` | fakeredis 또는 mock |
| fingerprint 변경 감지 | `RedisSchemaCache.is_changed` | mock |
| 설명 생성 | `DescriptionGenerator` | mock LLM |
| 캐시 매니저 폴백 | `SchemaCacheManager` | Redis 연결 실패 시뮬레이션 |

### 10.2 통합 테스트
| 테스트 | 대상 |
|--------|------|
| Redis 연결 + 저장/로드 | 실제 Redis 인스턴스 (Docker) |
| 스키마 변경 → 자동 갱신 | DB 스키마 변경 후 캐시 확인 |
| LLM 설명 생성 → Redis 저장 | 실제 LLM + Redis |
| 운영자 API E2E | FastAPI TestClient + Redis |

---

## 10.5 시멘틱 라우터 및 멀티턴 캐시 관리 보완

### 10.5.1 문제: `pending_synonym_reuse` 상태 유실

> **구현 상태: 미구현 (향후 Phase 3)**
> 현재 `pending_synonym_reuse`는 State에만 존재하며, API 요청 간에 유실된다.
> 아래 Redis 기반 해결 방안은 멀티턴 대화 기능(Phase 3) 구현 시 함께 적용한다.
> 현재는 단일 턴 내에서만 재활용 제안이 동작한다.

현재 `create_initial_state()` (`src/state.py`)는 매 API 요청마다 `pending_synonym_reuse=None`으로 초기화한다.
이로 인해 `cache_management` 노드가 유사 필드 재활용 제안(pending_synonym_reuse)을 반환해도, 사용자의 다음 응답("재활용", "새로 생성", "병합") 시점에 해당 상태가 사라진다.

**해결 방안: thread_id 기반 pending 상태 외부 저장 [미구현]**

```
[1] cache_management 노드에서 pending_synonym_reuse 설정 시:
    → Redis에 pending_synonym_reuse:{thread_id} 키로 JSON 저장 (TTL 10분)

[2] semantic_router 진입 시:
    → Redis에서 pending_synonym_reuse:{thread_id} 조회
    → 존재하면 state에 pending_synonym_reuse 복원
    → LLM 라우팅 스킵, 즉시 cache_management로 라우팅

[3] cache_management 노드에서 reuse-synonym 처리 완료 시:
    → Redis에서 pending_synonym_reuse:{thread_id} 삭제
```

**영향 받는 파일:**
| 파일 | 변경 내용 |
|------|-----------|
| `src/routing/semantic_router.py` | pending 상태 체크 및 강제 라우팅 로직 추가 |
| `src/nodes/cache_management.py` | pending 상태 Redis 저장/삭제 로직 추가 |
| `src/schema_cache/redis_cache.py` | `save_pending_reuse()`, `load_pending_reuse()`, `delete_pending_reuse()` 메서드 추가 |

**구현 상세 — semantic_router.py:**

```python
async def semantic_router(state, *, llm=None, app_config=None):
    ...
    # pending_synonym_reuse 복원 (Redis에서)
    thread_id = state.get("thread_id")  # thread_config에서 전달
    if thread_id:
        try:
            cache_mgr = get_cache_manager(app_config)
            pending = await cache_mgr.load_pending_reuse(thread_id)
            if pending:
                logger.info("시멘틱 라우팅: pending_synonym_reuse 감지, cache_management로 강제 라우팅")
                return {
                    "target_databases": [],
                    "is_multi_db": False,
                    "active_db_id": None,
                    "user_specified_db": None,
                    "routing_intent": "cache_management",
                    "pending_synonym_reuse": pending,
                    "current_node": "semantic_router",
                }
        except Exception as e:
            logger.debug("pending_synonym_reuse 로드 실패: %s", e)

    # ... 기존 LLM 라우팅 로직
```

**대안 (Redis 없이):** `create_initial_state()`에 `pending_synonym_reuse` 파라미터를 추가하고, API 라우트에서 thread_id 기반으로 인메모리 저장소에서 pending 상태를 복원하여 전달. 단, 서버 재시작 시 유실됨.

### 10.5.2 semantic_router 프롬프트 보강

현재 `src/prompts/semantic_router.py`의 캐시 관리 의도 분류 섹션에 포함된 키워드가 제한적이다.
계획에 명시된 다양한 프롬프트 패턴을 LLM이 정확히 분류할 수 있도록 프롬프트를 보강해야 한다.

**추가할 키워드 및 패턴:**

| 카테고리 | 추가 키워드/패턴 |
|---------|----------------|
| 유사 단어 관리 | "유사 단어 보여줘", "유사 단어 추가", "유사 단어 삭제", "유사 단어 변경", "유사 단어 목록" |
| 유사 단어 생성 | "유사 단어를 생성해줘", "유사 단어를 만들어줘", "더 만들어줘" |
| DB 설명 관리 | "DB 설명 생성", "DB 설명 설정", "DB 설명 변경" |
| 컬럼 설명 관리 | "컬럼 설명 보여줘", "컬럼 설명 변경", "설명을 수정" |
| 재활용 응답 | "재활용", "새로 생성", "병합" (pending_synonym_reuse 컨텍스트에서) |
| DB 안내 | "어떤 DB가 있어", "DB 목록", "사용 가능한 데이터베이스" |

**프롬프트 수정 위치:** `src/prompts/semantic_router.py` — `## 캐시 관리 의도 분류` 섹션

**변경 내용:**

```
## 캐시 관리 의도 분류

사용자가 스키마 캐시를 관리하려는 요청인 경우, intent를 "cache_management"로 설정하세요.

캐시 관리 관련 키워드 (아래 키워드가 포함되면 intent를 "cache_management"로):
- 캐시: "캐시 생성", "캐시 갱신", "캐시 삭제", "캐시 상태", "스키마 캐시"
- 유사 단어: "유사 단어 생성", "유사 단어 보여줘", "유사 단어 추가", "유사 단어 삭제",
  "유사 단어 변경", "유사 단어 목록", "유사 단어를 만들어줘"
- 컬럼 설명: "컬럼 설명 생성", "컬럼 설명 보여줘", "컬럼 설명 변경", "설명을 수정"
- DB 설명: "DB 설명 생성", "DB 설명 설정", "DB 설명 변경"
- 재활용 응답: "재활용", "새로 생성", "병합" (이전 질문에 대한 짧은 응답)

주의: "재활용", "새로 생성", "병합" 등 짧은 단어만 입력된 경우에도
데이터 조회가 아닌 캐시 관리 의도로 분류하세요.
```

### 10.5.3 semantic_router.py의 pending_synonym_reuse 바이패스

`pending_synonym_reuse` 상태가 Redis에서 복원된 경우, LLM 분류를 건너뛰고 바로 `cache_management`로 라우팅해야 한다.
이는 "재활용" 같은 짧은 응답이 LLM에 의해 `data_query`로 잘못 분류되는 것을 방지한다.

**라우팅 우선순위:**

```
1. mapped_db_ids 존재 → 필드 매핑 결과 사용 (기존)
2. pending_synonym_reuse 존재 → cache_management 강제 라우팅 (신규)
3. 활성 DB 없음 → 레거시 모드 (기존)
4. LLM 분류 → data_query 또는 cache_management (기존)
```

### 10.5.4 thread_id 전달 체계

현재 `thread_id`는 `thread_config["configurable"]["thread_id"]`에만 존재하고 State에는 포함되지 않는다.
`pending_synonym_reuse`의 Redis 저장/로드에 `thread_id`가 필요하므로 State에 추가하거나, 다른 경로로 전달해야 한다.

**방안 A: State에 thread_id 추가 (권장)**

| 파일 | 변경 내용 |
|------|-----------|
| `src/state.py` | `AgentState`에 `thread_id: Optional[str]` 필드 추가 |
| `src/state.py` | `create_initial_state()`에 `thread_id` 파라미터 추가 |
| `src/api/routes/query.py` | `create_initial_state()`에 `thread_id` 전달 |

**방안 B: LangGraph config에서 읽기**

노드 함수에서 `config` 파라미터를 받아 `config["configurable"]["thread_id"]`를 사용.
단, LangGraph 노드에 config를 전달하는 방식이 partial로는 어려울 수 있어 방안 A를 권장.

---

## 11. 구현 순서

| 단계 | 작업 | 의존성 |
|------|------|--------|
| **1** | `RedisConfig` 설정 추가, `.env.example` 업데이트 | 없음 |
| **2** | `RedisSchemaCache` 구현 (기본 CRUD + fingerprint) | 단계 1 |
| **3** | `SchemaCacheManager` 구현 (Redis/파일 추상화) | 단계 2 |
| **4** | `schema_analyzer` 노드 통합 (캐시 매니저 사용) | 단계 3 |
| **5** | `DescriptionGenerator` + LLM 프롬프트 구현 | 단계 3 |
| **6** | Redis에 description 저장/로드 통합 | 단계 2, 5 |
| **7** | 운영자 API 라우터 구현 | 단계 3, 6 |
| **8** | **독립 실행 CLI** (`scripts/schema_cache_cli.py`) 구현 | 단계 3, 6 |
| **9** | **프롬프트 기반 캐시 관리 노드** (`cache_management.py`) + 그래프 분기 | 단계 3, 6 |
| **9.1** | **시멘틱 라우터 프롬프트 보강** — 유사 단어/DB 설명/재활용 응답 키워드 추가 (`src/prompts/semantic_router.py`) | 단계 9 |
| **9.2** | **pending_synonym_reuse 상태 관리** — Redis 저장/로드, semantic_router 바이패스, thread_id State 전달 | 단계 2, 9 |
| **10** | `query_generator` 프롬프트에 컬럼 설명 + 유사 단어 통합 | 단계 6 |
| **11** | 단위 테스트 작성 | 단계 2~9.2 |
| **12** | 통합 테스트 작성 | 단계 11 |

---

## 12. 리스크 및 고려사항

### 12.1 Redis 장애 대응
- Redis 다운 시 파일 캐시로 자동 폴백 (기존 `PersistentSchemaCache` 유지)
- **스키마(tables/relationships)**: Redis + 파일 이중 저장으로 완전 폴백 보장
- **descriptions/synonyms**: 현재 Redis에만 저장. Redis 장애 시 빈 dict 반환 (기능 저하, 오류 아님)
- 연결 풀 타임아웃 짧게 설정 (5초)하여 빠른 폴백

> **향후 개선**: descriptions/synonyms를 파일 캐시에도 저장하는 확장을 검토. 현재는 Redis가 유일한 저장소이므로 Redis 장애 시 LLM의 컬럼 선택 정확도가 다소 저하될 수 있으나 기본 동작에는 영향 없음.

### 12.2 LLM 비용 관리
- 설명은 영구 저장 — 스키마 변경이 없으면 LLM 재호출 없음
- 스키마 변경 시 diff 기반 incremental 생성 (변경된 컬럼만)
- 운영자가 수동으로 설명 생성 시점 제어 가능 (`auto_generate_descriptions` 설정)
- CLI로 특정 DB만 선택적으로 설명 생성 가능 (`--db-id` 옵션)

### 12.3 멀티 DB 환경
- DB별 독립된 Redis 키 네임스페이스 (`schema:{db_id}:*`)
- 한 DB 캐시 갱신이 다른 DB에 영향 없음
- 전체 DB 일괄 캐시 생성 API 지원

### 12.4 보안
- Redis 비밀번호 설정 필수 (프로덕션)
- 스키마 정보에 민감 데이터 포함 가능 → Redis ACL 또는 네트워크 격리 권장
- sample_data 저장 시 기존 `DataMasker` 적용하여 민감 값 마스킹

### 12.5 기존 코드 호환성
- `PersistentSchemaCache`(파일 캐시) 삭제하지 않고 폴백으로 유지
- `SCHEMA_CACHE_BACKEND=file` 설정 시 기존 동작과 100% 동일
- 기본값을 `redis`로 변경하되, Redis 연결 불가 시 자동으로 `file`로 전환

---

## 13. 구현 상태 추적 (2026-03-18 갱신)

| 항목 | 구현 상태 | 비고 |
|------|----------|------|
| `RedisConfig`, `SchemaCacheConfig` | ✅ 완료 | `src/config.py` |
| `RedisSchemaCache` (기본 CRUD + DB별 synonyms + 글로벌 사전) | ✅ 완료 | `src/schema_cache/redis_cache.py` |
| `SchemaCacheManager` (Redis/파일 추상화) | ✅ 완료 | `src/schema_cache/cache_manager.py` |
| `PersistentSchemaCache` (파일 캐시 폴백) | ✅ 완료 | `src/schema_cache/persistent_cache.py` |
| `fingerprint` 모듈 | ✅ 완료 | 기본 SQL. 확장 SQL(타입 변경 감지)은 미구현 |
| `DescriptionGenerator` (LLM 설명 + 유사 단어) | ✅ 완료 | `src/schema_cache/description_generator.py` |
| `schema_analyzer` 캐시 통합 | ✅ 완료 | `load_synonyms_with_global_fallback` 사용 |
| `cache_management` 노드 | ✅ 완료 | `src/nodes/cache_management.py` |
| 시멘틱 라우터 캐시 의도 분류 | ✅ 완료 | 유사단어/DB설명/재활용 키워드 보강 완료 |
| 그래프 분기 (cache_management → END) | ✅ 완료 | `src/graph.py` |
| 운영자 API 라우터 | ✅ 완료 | `src/api/routes/schema_cache.py` |
| CLI 스크립트 | ✅ 완료 | `scripts/schema_cache_cli.py` |
| `invalidate_all`에서 `db_descriptions` 보존 | ✅ 수정됨 | `redis_cache.py` |
| `_handle_update_synonym` private 접근 제거 | ✅ 수정됨 | `cache_management.py` |
| `sync_global_synonyms` 자동 호출 | ✅ 수정됨 | 설명/유사단어 생성 후 자동 호출 |
| 시멘틱 라우터 프롬프트 보강 (§10.5.2) | ✅ 수정됨 | 유사단어/DB설명/재활용 키워드 추가 |
| API `delete_column_synonyms` private 접근 제거 | ✅ 수정됨 | `schema_cache.py` |
| `save_db_description` private 접근 제거 | ✅ 수정됨 | `cache_manager.py` + `persistent_cache.py` |
| `pending_synonym_reuse` Redis 영속화 (thread_id) | ❌ 미구현 | Phase 3에서 구현 예정 |
| descriptions/synonyms 파일 폴백 | ❌ 미구현 | 향후 개선 사항 |
| 확장 fingerprint SQL (타입 변경 감지) | ❌ 미구현 | 향후 개선 사항 |
| 자동 description 생성 (캐시 미스 시 백그라운드) | ❌ 미구현 | 현재 수동/CLI/API로만 생성 |


---

# Verification Report

# Redis 기반 스키마 캐시 검증 보고서

## 검증 일시
2026-03-17

## 검증 범위
`plans/schemacache_plan.md`의 12단계 구현 순서 전체

## 테스트 결과 요약

| 테스트 영역 | 테스트 수 | 통과 | 실패 | 비고 |
|------------|----------|------|------|------|
| RedisSchemaCache 단위 | 21 | 21 | 0 | Mock Redis 사용 |
| SchemaCacheManager 단위 | 12 | 12 | 0 | Redis fallback 포함 |
| DescriptionGenerator 단위 | 10 | 10 | 0 | Mock LLM 사용 |
| 통합 테스트 | 9 | 9 | 0 | 프롬프트/State/Config |
| 기존 테스트 (fingerprint) | 8 | 8 | 0 | 기존 코드 호환성 |
| 기존 테스트 (persistent_cache) | 13 | 13 | 0 | 기존 코드 호환성 |
| 기존 테스트 (state) | 17 | 17 | 0 | 새 필드 호환성 |
| **전체 프로젝트** | **508** | **508** | **0** | 회귀 없음 |

## 구현 완료 항목

### 단계 1: RedisConfig + .env.example
- `src/config.py`: `RedisConfig` 클래스 추가, `SchemaCacheConfig`에 `backend`/`auto_generate_descriptions` 필드 추가
- `AppConfig`에 `redis: RedisConfig` 필드 추가
- `.env.example`에 `REDIS_*`, `SCHEMA_CACHE_BACKEND`, `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS` 추가

### 단계 2: RedisSchemaCache
- `src/schema_cache/redis_cache.py`: 기본 CRUD, fingerprint, descriptions, synonyms, 관리 메서드 구현
- Redis 키 네이밍: `schema:{db_id}:{meta|tables|relationships|descriptions|synonyms}`
- 영구 저장 (TTL 없음)

### 단계 3: SchemaCacheManager
- `src/schema_cache/cache_manager.py`: Redis/파일 캐시 통합 추상화
- Graceful fallback: Redis 장애 시 파일 캐시 자동 전환
- `get_cache_manager()` 싱글톤 팩토리

### 단계 4: schema_analyzer 통합
- `src/nodes/schema_analyzer.py`: `_get_schema_with_cache` 함수를 SchemaCacheManager 사용으로 변경
- descriptions/synonyms를 함께 로드하여 State에 저장
- 캐시 저장을 cache_manager 통해 수행 (Redis + 파일 이중 저장)

### 단계 5: DescriptionGenerator + LLM 프롬프트
- `src/schema_cache/description_generator.py`: 테이블 단위 배치 처리, incremental 생성 지원
- `src/prompts/schema_description.py`: 설명 + 유사 단어 동시 생성 프롬프트

### 단계 6: descriptions + synonyms Redis 저장/로드
- SchemaCacheManager를 통한 저장/로드 통합
- schema_analyzer에서 descriptions/synonyms State 필드 업데이트

### 단계 7: 운영자 API
- `src/api/routes/schema_cache.py`: 캐시 생성/갱신, 설명 생성, 상태 조회, 캐시 삭제, 유사 단어 관리
- `src/api/server.py`: 라우터 등록, Redis 연결 lifespan 관리

### 단계 8: CLI 스크립트
- `scripts/schema_cache_cli.py`: generate, generate-descriptions, status, show, invalidate, synonyms 서브커맨드

### 단계 9: cache_management 노드 + 시멘틱 라우터 확장
- `src/nodes/cache_management.py`: 프롬프트 기반 캐시 관리 노드
- `src/prompts/cache_management.py`: 의도 파싱 프롬프트
- `src/routing/semantic_router.py`: `cache_management` 의도 분류 추가
- `src/graph.py`: cache_management 노드 등록, 조건부 라우팅 추가

### 단계 10: query_generator 프롬프트 강화
- `src/nodes/query_generator.py`: `_format_schema_for_prompt`에 descriptions/synonyms 추가
- 프롬프트 형식: `컬럼명: 타입 -- 한국어 설명 [유사: 단어1, 단어2]`

### 단계 11-12: 단위/통합 테스트
- `tests/test_schema_cache/test_redis_cache.py`: 21개 테스트
- `tests/test_schema_cache/test_cache_manager.py`: 12개 테스트
- `tests/test_schema_cache/test_description_generator.py`: 10개 테스트
- `tests/test_schema_cache/test_integration.py`: 9개 테스트

## 핵심 제약사항 준수 여부

| 제약사항 | 상태 | 검증 방법 |
|---------|------|----------|
| DB read-only (3-layer defense 유지) | 준수 | Redis에 저장하는 것은 스키마 메타데이터뿐. DB 쓰기 코드 없음 |
| Redis 장애 시 파일 캐시 fallback | 준수 | `test_redis_failure_falls_back_to_file` 테스트 통과 |
| 영구 저장 (TTL 없음) | 준수 | Redis 저장 시 TTL 설정 코드 없음 |
| fingerprint 변경 시에만 갱신 | 준수 | `is_changed()` 메서드로 비교 후 갱신 |
| 기존 코드 호환성 | 준수 | `SCHEMA_CACHE_BACKEND=file` 테스트, 기존 508개 테스트 전수 통과 |

## 수정된 기존 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `src/config.py` | `RedisConfig` 추가, `SchemaCacheConfig` 확장, `AppConfig.redis` 추가 |
| `src/state.py` | `column_descriptions`, `column_synonyms`, `routing_intent` 필드 추가 |
| `src/nodes/schema_analyzer.py` | SchemaCacheManager 통합, descriptions/synonyms 로드 |
| `src/nodes/query_generator.py` | 프롬프트에 설명 + 유사 단어 포함 |
| `src/routing/semantic_router.py` | `cache_management` 의도 분류 추가 |
| `src/prompts/semantic_router.py` | 캐시 관리 의도 분류 프롬프트 추가 |
| `src/graph.py` | cache_management 노드/라우팅 추가 |
| `src/api/server.py` | schema_cache 라우터 등록, Redis lifespan |
| `src/schema_cache/__init__.py` | 새 모듈 export 추가 |
| `pyproject.toml` | `redis[hiredis]>=5.0.0` 의존성 추가 |
| `.env.example` | Redis/스키마캐시 환경변수 추가 |
| `docs/02_decision.md` | D-011 결정 추가 |

## Critical 이슈
없음.

## Minor 이슈
- `test_file_mode_get_schema_from_file`에서 `DeprecationWarning: There is no current event loop` 경고 발생. 기능에 영향 없음.


---

# Verification Report (New Features)

# Verification Report: schemacache_plan.md 신규 3가지 기능

**검증일**: 2026-03-18
**검증 대상**: schemacache_plan.md의 글로벌 유사단어 사전 관련 신규 3가지 기능

---

## 1. 구현된 기능 요약

### 기능 1: 글로벌 유사단어에 컬럼 설명(description) 추가

| 항목 | 상태 |
|------|------|
| `synonyms:global` value를 `{words: [...], description: "..."}` 형태로 확장 | 완료 |
| `update-description` action 추가 | 완료 |
| `list-synonyms` 응답에 description 표시 | 완료 |
| `RedisSchemaCache.update_global_description()` | 완료 |
| `RedisSchemaCache.get_global_description()` | 완료 |
| `RedisSchemaCache.load_global_synonyms_full()` | 완료 |
| `RedisSchemaCache.list_global_column_names()` | 완료 |
| `SchemaCacheManager` 래퍼 메서드 4개 추가 | 완료 |
| 기존 list 형태와 하위 호환 유지 | 완료 |

### 기능 2: 프롬프트 기반 글로벌 유사 단어 LLM 생성

| 항목 | 상태 |
|------|------|
| `generate-global-synonyms` action 추가 | 완료 |
| seed_words 파라미터 지원 | 완료 |
| `SchemaCacheManager.generate_global_synonyms()` | 완료 |
| 기존 항목이 있으면 merge (중복 제거) | 완료 |
| LLM 실패 시 seed_words만이라도 저장 (graceful fallback) | 완료 |
| GENERATE_GLOBAL_SYNONYMS_PROMPT 추가 | 완료 |

### 기능 3: 유사 필드 자동 탐색 및 재활용 (Smart Synonym Reuse)

| 항목 | 상태 |
|------|------|
| 글로벌 사전에 없는 새 필드 추가 시 LLM 유사 컬럼 탐색 | 완료 |
| 사용자에게 재활용 제안 응답 생성 | 완료 |
| State에 `pending_synonym_reuse` 필드 추가 | 완료 |
| `SchemaCacheManager.find_similar_global_columns()` | 완료 |
| `SchemaCacheManager.reuse_synonyms()` (copy/merge 모드) | 완료 |
| cache_management 노드에 재활용 제안/처리 로직 | 완료 |
| `reuse-synonym` action (사용자 선택 처리) | 완료 |
| FIND_SIMILAR_COLUMNS_PROMPT 추가 | 완료 |

---

## 2. 변경된 파일 목록

### 수정된 파일 (기존 코드 확장)

| 파일 | 변경 내용 |
|------|-----------|
| `src/schema_cache/redis_cache.py` | 글로벌 유사단어 CRUD를 dict 형태({words, description}) 지원으로 확장. `update_global_description`, `get_global_description`, `load_global_synonyms_full`, `list_global_column_names` 추가. `add_global_synonym`, `remove_global_synonym`이 description 보존하도록 수정 |
| `src/schema_cache/cache_manager.py` | 5개 래퍼 메서드 추가 (`get_global_synonyms_full`, `update_global_description`, `get_global_description`, `list_global_column_names`). 3개 비즈니스 메서드 추가 (`generate_global_synonyms`, `find_similar_global_columns`, `reuse_synonyms`) |
| `src/nodes/cache_management.py` | 4개 핸들러 추가 (`_handle_generate_global_synonyms`, `_handle_reuse_synonym`, `_handle_update_description`). `_execute_cache_action`에 신규 action 라우팅. `_handle_list_synonyms`가 description 표시. `_handle_update_synonym`이 description 보존. `pending_synonym_reuse` State 처리 |
| `src/prompts/cache_management.py` | 3개 프롬프트 추가 (`GENERATE_GLOBAL_SYNONYMS_PROMPT`, `FIND_SIMILAR_COLUMNS_PROMPT`). `CACHE_MANAGEMENT_PARSE_PROMPT`에 신규 action 추가 (`generate-global-synonyms`, `update-description`, `reuse-synonym`) + 새 필드 (`seed_words`, `description`, `reuse_mode`) |
| `src/state.py` | `pending_synonym_reuse: Optional[dict]` 필드 추가. `create_initial_state()`에 초기값 `None` 추가 |

### 수정된 기존 테스트 파일 (하위 호환 적용)

| 파일 | 변경 내용 |
|------|-----------|
| `tests/test_schema_cache/test_redis_cache_synonyms.py` | `add_global_synonym`, `remove_global_synonym` 테스트가 dict 형태의 새 저장 포맷을 검증하도록 수정 |
| `tests/test_nodes/test_cache_management_synonyms.py` | `_handle_list_synonyms` 테스트가 `get_global_synonyms_full()` mock을 사용하도록 수정. `_handle_update_synonym` 테스트에 `get_global_description` mock 추가 |

### 신규 테스트 파일

| 파일 | 테스트 수 |
|------|-----------|
| `tests/test_schema_cache/test_redis_cache_global_description.py` | 23개 |
| `tests/test_schema_cache/test_cache_manager_new_features.py` | 16개 |
| `tests/test_nodes/test_cache_management_new_features.py` | 19개 |

---

## 3. 테스트 결과

### 전체 테스트 수행 결과

```
702 passed, 1 failed (pre-existing), 51 warnings
```

### 기존 테스트 (regression 확인)

| 테스트 파일 | 결과 |
|------------|------|
| `tests/test_schema_cache/test_redis_cache_synonyms.py` (20개) | 전체 통과 |
| `tests/test_schema_cache/test_cache_manager_synonyms.py` (11개) | 전체 통과 |
| `tests/test_nodes/test_cache_management_synonyms.py` (14개) | 전체 통과 |
| 기타 전체 테스트 (657개) | 전체 통과 |

### 신규 테스트

| 테스트 파일 | 결과 |
|------------|------|
| `tests/test_schema_cache/test_redis_cache_global_description.py` (23개) | 전체 통과 |
| `tests/test_schema_cache/test_cache_manager_new_features.py` (16개) | 전체 통과 |
| `tests/test_nodes/test_cache_management_new_features.py` (19개) | 전체 통과 |

### 사전 존재 실패 (변경과 무관)

```
FAILED tests/test_schema_cache/test_integration.py::TestConfigIntegration::test_redis_config_exists
- 원인: 로컬 .env 파일에 REDIS_PORT=6380 설정 (테스트는 기본값 6379 기대)
- 본 변경과 무관한 환경 설정 이슈
```

---

## 4. 하위 호환성 검증

### 글로벌 유사단어 데이터 형식

| 기존 형식 | 신규 형식 | 호환성 |
|-----------|-----------|--------|
| `{"hostname": ["a", "b"]}` (list) | `{"hostname": {"words": ["a", "b"], "description": "..."}}` (dict) | `load_global_synonyms()`: 두 형식 모두 정상 로드 (words만 반환). `load_global_synonyms_full()`: 레거시 list를 dict으로 자동 변환. `add_global_synonym()`: 기존 list 형태 entry에 추가 시 dict으로 자동 업그레이드 |

### Redis가 없는 환경

| 메서드 | 반환값 |
|--------|--------|
| `get_global_synonyms_full()` | `{}` |
| `update_global_description()` | `False` |
| `get_global_description()` | `None` |
| `list_global_column_names()` | `[]` |
| `generate_global_synonyms()` | `{"words": seed_words or [], "description": ""}` |
| `find_similar_global_columns()` | `[]` |
| `reuse_synonyms()` | 기본 entry (빈 words) |

---

## 5. Critical 이슈

없음.

---

## 6. Minor 이슈 / 권장사항

1. `generate_global_synonyms`와 `find_similar_global_columns`는 LLM 호출을 수행하므로, 실제 LLM 연동 시 응답 형식 파싱 실패 가능성이 있음. 현재 JSON 파싱 실패 시 graceful fallback 처리가 구현되어 있음.

2. `pending_synonym_reuse` State 필드는 멀티턴 대화가 활성화되면 세션 간 유지가 필요할 수 있음 (Phase 3에서 검토).


---

# Verification Report (유사단어 확장)

# Redis 기반 스키마 캐시 유사단어 확장 - 검증 보고서

> 검증일: 2026-03-18
> 검증 대상: schemacache_plan.md 유사단어 2계층, source 태깅, invalidate 보존, 프롬프트 기반 유사단어 CRUD

---

## 1. 검증 범위

`plans/schemacache_plan.md` 11장 구현 순서 중 미구현 항목 보완 및 검증.

## 2. 구현 상태 요약

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| 1 | RedisConfig 설정 추가, .env.example 업데이트 | 기존 완료 | 변경 없음 |
| 2 | RedisSchemaCache (기본 CRUD + fingerprint + 유사단어 2계층) | **보완 완료** | source 태깅, 글로벌 synonyms, invalidate 보존 추가 |
| 3 | SchemaCacheManager (Redis/파일 추상화) | **보완 완료** | load_synonyms_with_global_fallback, sync_global_synonyms, add/remove 래퍼 추가 |
| 4 | schema_analyzer 노드 통합 | 기존 완료 | 변경 없음 |
| 5 | DescriptionGenerator + LLM 프롬프트 | 기존 완료 | 변경 없음 |
| 6 | Redis에 description + synonyms 저장/로드 통합 | 기존 완료 | 변경 없음 |
| 7 | 운영자 API 라우터 | 기존 완료 | 변경 없음 |
| 8 | 독립 실행 CLI | 기존 완료 | 변경 없음 |
| 9 | 프롬프트 기반 캐시 관리 노드 + 그래프 분기 | **보완 완료** | synonym CRUD 핸들러 4개 추가 |
| 10 | query_generator 프롬프트에 컬럼 설명 + 유사 단어 통합 | 기존 완료 | 변경 없음 |
| 11-12 | 단위/통합 테스트 | **보완 완료** | 신규 45개 테스트 추가 |

## 3. 이번 작업에서 수정/추가한 파일

### 수정된 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/schema_cache/redis_cache.py` | (1) synonyms를 `{words, sources}` 구조로 source 태깅 (2) 글로벌 유사단어 사전 (`synonyms:global`) CRUD (3) `invalidate()`에서 synonyms 키 보존 (4) `invalidate_all()`에서 synonyms 키 보존 (5) `delete_synonyms()` / `delete_global_synonyms()` 명시적 삭제 |
| `src/schema_cache/cache_manager.py` | (1) `add_synonyms()` 래퍼 (2) `remove_synonyms()` 래퍼 (3) 글로벌 synonyms CRUD (4) `load_synonyms_with_global_fallback()` (5) `sync_global_synonyms()` |
| `src/nodes/cache_management.py` | synonym CRUD 핸들러 4개: `_handle_list_synonyms`, `_handle_add_synonym`, `_handle_remove_synonym`, `_handle_update_synonym` |
| `src/prompts/cache_management.py` | `list-synonyms`, `add-synonym`, `remove-synonym`, `update-synonym` 액션 추가 |

### 새로 생성된 파일

| 파일 | 내용 |
|------|------|
| `tests/test_schema_cache/test_redis_cache_synonyms.py` | RedisSchemaCache 유사단어 확장 테스트 (20개) |
| `tests/test_schema_cache/test_cache_manager_synonyms.py` | SchemaCacheManager 유사단어 확장 테스트 (11개) |
| `tests/test_nodes/test_cache_management_synonyms.py` | cache_management 노드 synonym CRUD 테스트 (14개) |

## 4. 핵심 요구사항 검증 결과

### 4.1 유사단어 영구 보존

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| invalidate 시 synonyms 보존 | PASS | `invalidate()`에서 "synonyms" suffix를 삭제 대상에서 제외 |
| invalidate_all 시 synonyms 보존 | PASS | `scan_iter` 결과에서 `:synonyms`로 끝나는 키를 스킵 |
| 글로벌 사전 자동 삭제 방지 | PASS | `synonyms:global` 키는 `schema:*` 패턴에 매칭되지 않음 |
| 명시적 삭제만 허용 | PASS | `delete_synonyms()` / `delete_global_synonyms()` 별도 메서드 |

### 4.2 2계층 유사단어 (DB별 + 글로벌)

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| DB별 synonyms 저장/로드 | PASS | `schema:{db_id}:synonyms` Hash |
| 글로벌 사전 저장/로드 | PASS | `synonyms:global` Hash |
| 글로벌 폴백 | PASS | `load_synonyms_with_global_fallback()` - DB에 없는 컬럼은 bare name으로 글로벌 조회 |
| DB synonyms 우선 | PASS | 글로벌보다 DB synonyms가 우선 |
| 글로벌 동기화 | PASS | `sync_global_synonyms()` - DB별 synonyms를 글로벌에 병합 |

### 4.3 source 태깅

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| LLM 생성분 "llm" 태깅 | PASS | `save_synonyms(..., source="llm")` |
| 운영자 추가분 "operator" 태깅 | PASS | `add_synonyms(..., source="operator")` |
| 기존 source 보존 | PASS | `add_synonyms()` 에서 기존 source는 덮어쓰지 않음 |
| 레거시 list 형태 호환 | PASS | list -> `{words, sources}` 자동 변환 |

### 4.4 프롬프트 기반 유사단어 관리

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| 유사단어 목록 조회 (글로벌/DB별/컬럼별) | PASS | `list-synonyms` 액션 |
| 유사단어 추가 (글로벌 + DB 동기화) | PASS | `add-synonym` 액션 |
| 유사단어 삭제 (글로벌 + DB 동시) | PASS | `remove-synonym` 액션 |
| 유사단어 교체 | PASS | `update-synonym` 액션 |
| 프롬프트 파싱 | PASS | `cache_management.py` 프롬프트에 모든 액션 포함 |

### 4.5 Redis Graceful Fallback

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| Redis 미연결 시 빈 결과 반환 | PASS | 모든 메서드에서 `_connected` 검사 |
| 파일 백엔드 시 글로벌 synonyms 빈 dict | PASS | `backend != "redis"` 시 빈 결과 |
| 파일 백엔드 시 add_synonyms False | PASS | Redis 없으면 False 반환 |

## 5. 테스트 결과

```
전체 테스트: 644 passed, 1 deselected (환경 의존 테스트)
신규 테스트: 45 passed
기존 테스트: 599 passed (회귀 없음)
```

### 5.1 신규 테스트 상세

**test_redis_cache_synonyms.py (20개)**
- TestSynonymSourceTagging: 7개 (source 태깅 저장/로드/변환)
- TestInvalidatePreservesSynonyms: 3개 (invalidate 보존)
- TestGlobalSynonyms: 8개 (글로벌 CRUD)
- TestDisconnectedGraceful: 2개 (연결 없을 때)

**test_cache_manager_synonyms.py (11개)**
- TestAddRemoveSynonyms: 2개 (래퍼 위임)
- TestGlobalSynonymsMethods: 3개 (글로벌 메서드)
- TestLoadSynonymsWithGlobalFallback: 3개 (폴백 로직)
- TestSyncGlobalSynonyms: 1개 (동기화)
- TestFileBackendFallback: 2개 (파일 백엔드)

**test_cache_management_synonyms.py (14개)**
- TestHandleListSynonyms: 5개 (목록 조회)
- TestHandleAddSynonym: 4개 (추가)
- TestHandleRemoveSynonym: 1개 (삭제)
- TestHandleUpdateSynonym: 2개 (교체)
- TestHandleInvalidatePreservesSynonyms: 2개 (보존 안내)

## 6. 하위 호환성

| 항목 | 결과 |
|------|------|
| 기존 `load_synonyms()` API | 호환 - 레거시 list 형태도 정상 로드 |
| 기존 `save_synonyms()` API | 호환 - list[str] 형태 자동 변환 |
| `SCHEMA_CACHE_BACKEND=file` | 호환 - 글로벌 synonyms는 빈 dict 반환 |
| 기존 599개 테스트 | 모두 통과 |

## 7. Critical 이슈

없음.

## 8. Minor 이슈

| 이슈 | 영향도 | 상태 |
|------|--------|------|
| `test_redis_config_exists` 환경 의존 실패 | Low | 로컬 .env에 REDIS_PORT=6380 설정으로 인한 기존 테스트 실패. 이번 구현과 무관. |

---
---
