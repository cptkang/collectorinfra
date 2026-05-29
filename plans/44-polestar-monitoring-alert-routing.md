# Plan 44: 폴스타 모니터링 Alert 조회 의도 추가

## 개요

**목표**: 알람/모니터링 관련 질의가 들어왔을 때 `routing_intent = "alarm_query"`를 독립 의도로 분류하고,
이를 `query_generator`까지 전파하여 알람 전용 테이블(`CMM_ALARM` 계열)과 샘플 쿼리를
`POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`에 조건부로 주입한다.

**설계 방향 (방안 2)**: `routing_intent`를 semantic_router → state → query_generator로
결정론적으로 전달하여, LLM의 자율 선택이 아닌 **의도 기반 강제 주입**으로 올바른 쿼리 패턴을 보장한다.

**변경 파일**: 5개

| 파일 | 변경 유형 |
|------|---------|
| `src/prompts/semantic_router.py` | `alarm_query` 의도 분류 규칙 + 예시 추가 |
| `src/routing/domain_config.py` | polestar 4개 도메인 description에 alert 정보 추가 |
| `src/nodes/query_generator.py` | `routing_intent` 파라미터 전달 경로 추가 |
| `src/prompts/query_generator.py` | 테이블 화이트리스트 확장 + Template C(알람) 추가 |
| `src/state.py` | **없음** (routing_intent 필드 이미 존재) |
| `src/routing/semantic_router.py` | **없음** (코드 변경 불필요) |
| `src/graph.py` | **없음** (변경 불필요) |

---

## 1. 현재 상태 및 문제점

### 1.1 polestar 도메인 설명 (domain_config.py)

모든 polestar 도메인이 **사양/사용량 데이터** 위주로 정의되어 있어,
"알람", "alert", "임계값 초과" 질의 시 LLM이 polestar DB를 낮은 관련도로 평가한다.

```python
# 현재 (공통 패턴)
description=(
    "서버 물리 사양 및 사용량 데이터. "
    "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), ..."
),
```

### 1.2 semantic_router가 alarm 의도를 별도 분류하지 않음

`src/routing/semantic_router.py`의 `_llm_classify()`는 LLM 응답에서 `routing_intent`를
파싱하지만, 현재 프롬프트(`src/prompts/semantic_router.py`)에는 alarm 관련 의도 분류 규칙이 없다.

```python
# src/state.py:118 — 필드는 존재하나 값이 항상 "data_query"
routing_intent: Optional[str]  # "data_query" | "cache_management"
# "alarm_query" 값이 없음
```

### 1.3 routing_intent가 query_generator에 전달되지 않음 (핵심 문제)

`src/nodes/query_generator.py:181-191`에서 `_build_system_prompt()` 호출 시
`state["routing_intent"]`를 전달하지 않는다.

```python
# 현재 코드 (query_generator.py:181-191)
system_prompt = _build_system_prompt(
    schema_info=state["schema_info"],
    default_limit=limit_value,
    column_descriptions=state.get("column_descriptions", {}),
    column_synonyms=state.get("column_synonyms", {}),
    resource_type_synonyms=state.get("resource_type_synonyms"),
    eav_name_synonyms=state.get("eav_name_synonyms"),
    active_db_id=state.get("active_db_id"),
    polestar_db_ids=app_config.get_polestar_db_ids() or None,
    active_db_engine=state.get("active_db_engine"),
    # ← routing_intent 없음
)
```

### 1.4 POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE의 테이블 화이트리스트가 알람 테이블 차단

`src/prompts/query_generator.py:60`:
```
사용 가능한 테이블: cmm_resource, core_config_prop, cmm_metric_stat_[h,d,m] 만 사용한다.
```

알람 쿼리에 필수인 `CMM_ALARM`, `CMM_ALARM_DEF`, `CMM_ALARM_ACTIVE` 등이 목록에 없으므로,
LLM이 이 테이블을 사용하는 SQL을 생성하면 Hallucination으로 간주하여 거부할 수 있다.

### 1.5 Template A/B만 존재 — 알람 Template C 없음

`POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`에는 현재:
- **Template A**: EAV 피벗 (cmm_resource + core_config_prop) — 서버 설정 정보
- **Template B**: 성능 지표 (cmm_metric_stat_[h,d,m]) — CPU/메모리/파일시스템 사용률

알람 조회용 **Template C**가 없어 LLM이 알람 쿼리를 생성할 근거가 없다.

---

## 2. 변경 범위 상세

### 2.1 변경 파일 목록

| 파일 | 변경 유형 | 핵심 내용 |
|------|---------|---------|
| `src/prompts/semantic_router.py` | 수정 | `alarm_query` 의도 분류 규칙, 예시 5건 추가 |
| `src/routing/domain_config.py` | 수정 | polestar 4개 DB description에 알람 정보 추가 |
| `src/nodes/query_generator.py` | 수정 | `_build_system_prompt()` 호출부 및 시그니처에 `routing_intent` 추가 |
| `src/prompts/query_generator.py` | 수정 | 테이블 화이트리스트 조건부 확장 + Template C 추가 |

### 2.2 불변 파일

| 파일 | 이유 |
|------|------|
| `src/state.py` | `routing_intent: Optional[str]` 필드 이미 존재 |
| `src/routing/semantic_router.py` | LLM 응답에서 `routing_intent`를 파싱하는 로직 이미 구현됨 |
| `src/graph.py` | 그래프 흐름 변경 없음 |

---

## 3. 구현 계획

### Step 1: `src/routing/domain_config.py` — 도메인 설명 보강

각 polestar 도메인의 `description`에 모니터링/알람 데이터 및 관련 테이블명을 추가한다.

#### polestar (메인 DB, db2)

```python
description=(
    "서버 물리 사양, 사용량 및 모니터링 데이터. "
    "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
    "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
    "서버 정보(hostname, IP, gateway), "
    "프로세스 정보(서버에서 동작 중인 프로세스 종류), "
    "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
    "알람 심각도(1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
    "알람 담당자(ACKUSERNAME), 알람 상태(CURRENTALARMSTATUS), "
    "알람 대상 장비(CMM_RESOURCE), 알람 유형(CMM_ALARM_DEF)"
),
```

#### polestar_b0 (은행 레거시, db2)

```python
description=(
    "은행 레거시 및 K리전(은행존) 서버 물리 사양, 사용량 및 모니터링 데이터. "
    "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
    "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
    "서버 정보(hostname, IP, gateway), "
    "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
    "알람 심각도(1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
    "알람 담당자(ACKUSERNAME), 알람 대상 장비(CMM_RESOURCE)"
),
```

#### polestar_cm_gp (김포 운영/DR, postgresql)

```python
description=(
    "K리전(공동존) 김포 운영 및 DR 서버 물리 사양, 사용량 및 모니터링 데이터. "
    "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
    "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
    "서버 정보(hostname, IP, gateway), "
    "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
    "알람 심각도(1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
    "알람 담당자(ACKUSERNAME), 알람 상태(CURRENTALARMSTATUS), "
    "알람 대상 장비(CMM_RESOURCE), 알람 유형(CMM_ALARM_DEF)"
),
```

#### polestar_cm_yd (여의도 개발/스테이징, postgresql)

```python
description=(
    "K리전(공동존) 여의도 개발 및 스테이징 서버 물리 사양, 사용량 및 모니터링 데이터. "
    "서버 사양(CPU, Core 수, Memory 크기, Disk 크기), "
    "서버 사용량(월 평균/최고 CPU 사용률, Disk 사용용량), "
    "서버 정보(hostname, IP, gateway), "
    "모니터링 알람(alert) 정보: 현재 발생 알람(CMM_ALARM), 알람 이력, "
    "알람 심각도(1=주의/2=경고/3=심각), 알람 발생 시각(CTIME), "
    "알람 담당자(ACKUSERNAME), 알람 상태(CURRENTALARMSTATUS), "
    "알람 대상 장비(CMM_RESOURCE), 알람 유형(CMM_ALARM_DEF)"
),
```

---

### Step 2: `src/prompts/semantic_router.py` — `alarm_query` 의도 분류 추가

#### 2-1. LLM 출력 JSON의 `intent` 필드 확장

기존 `"data_query" | "cache_management"` 에 `"alarm_query"` 추가.
`SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE` 내 출력 형식 섹션을 아래와 같이 수정한다:

```
## 출력 형식

반드시 아래 JSON 형식으로만 응답하세요.

{
    "intent": "data_query" | "alarm_query" | "cache_management",
    "databases": [...]
}

- data_query: 서버 사양, 성능 지표, 프로세스 등 일반 인프라 데이터 조회
- alarm_query: 알람 현황, 알람 이력, 임계값 초과, 모니터링 alert 조회
- cache_management: 캐시 생성/갱신/삭제, 유사어 관리, 컬럼 설명 변경
```

#### 2-2. `## 알람 조회 판단` 섹션 추가

기존 `## 판단 규칙` 섹션 뒤에 삽입:

```
## 알람 조회 판단 (alarm_query)

사용자가 알람 현황, 알람 이력, 임계값 초과 등 모니터링 이벤트 정보를 요청하는 경우:
- intent를 "alarm_query"로 설정하고 해당 polestar DB를 선택합니다.
- 지역/환경이 명시된 경우: "김포 알람" → polestar_cm_gp, "여의도 알람" → polestar_cm_yd

alarm_query로 분류할 질의 패턴:
- 알람 목록: "현재 발생 중인 알람", "알람 목록", "alert 현황", "알람 조회"
- 심각도 필터: "critical 알람", "warning 알람", "심각도별 알람", "심각 알람"
- 임계값/이상: "임계값 초과 서버", "CPU 알람", "디스크 알람", "메모리 알람"
- 알람 이력: "알람 이력", "지난주 알람", "알람 발생 횟수", "이번 달 알람"
- 모니터링 현황: "모니터링 정보", "모니터링 현황", "서버 모니터링", "모니터링 상태"
- 담당자 조회: "미확인 알람", "처리되지 않은 알람", "담당자 없는 알람"
```

#### 2-3. 예시 섹션에 alarm_query 사례 5건 추가

```json
입력: "현재 발생 중인 서버 알람 목록을 보여줘"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar", "relevance_score": 0.95,
         "reason": "서버 알람 목록 조회 — alarm_query 의도",
         "sub_query_context": "현재 활성 알람 목록 조회", "user_specified": false}
    ]
}

입력: "김포 폴스타에서 critical 알람 이력을 조회해줘"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar_cm_gp", "relevance_score": 1.0,
         "reason": "사용자가 김포 폴스타 직접 지정, critical 알람 이력 조회",
         "sub_query_context": "critical 심각도 알람 이력 조회", "user_specified": true}
    ]
}

입력: "여의도 개발 서버 중 CPU 임계값 초과 알람이 발생한 서버 목록"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar_cm_yd", "relevance_score": 0.95,
         "reason": "여의도 개발 환경 CPU 알람 조회",
         "sub_query_context": "CPU 임계값 초과 알람 발생 서버 목록 조회", "user_specified": false}
    ]
}

입력: "이번 달 경고 이상 알람 발생 횟수를 장비별로 집계해줘"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar", "relevance_score": 0.9,
         "reason": "월별 알람 발생 횟수 집계 — alarm_query 의도",
         "sub_query_context": "이번 달 경고/심각 알람 장비별 집계 조회", "user_specified": false}
    ]
}

입력: "미확인(NOT_ACK) 알람 목록과 담당자 정보를 보여줘"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar", "relevance_score": 0.95,
         "reason": "미확인 알람 및 담당자 조회 — alarm_query 의도",
         "sub_query_context": "미확인 알람 목록과 담당자 정보 조회", "user_specified": false}
    ]
}

입력: "5월 한 달간 서버에서 발생한 알람 목록을 보여줘"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar", "relevance_score": 0.9,
         "reason": "서버 장비 대상 특정 기간 알람 이력 조회 — alarm_query 의도",
         "sub_query_context": "서버 알람 2026-05-01~2026-05-31 이력 조회", "user_specified": false}
    ]
}

입력: "이번 달 서버의 CPU 및 메모리 알람 이력을 보여줘"
출력:
{
    "intent": "alarm_query",
    "databases": [
        {"db_id": "polestar", "relevance_score": 0.95,
         "reason": "서버 CPU/메모리 키워드 알람 이력 조회 — alarm_query 의도",
         "sub_query_context": "서버 CPU 및 메모리 알람 이번 달 이력 조회", "user_specified": false}
    ]
}
```

---

### Step 3: `src/nodes/query_generator.py` — `routing_intent` 전달 경로 추가

#### 3-1. `query_generator()` → `_build_system_prompt()` 호출부 수정

**변경 위치**: `query_generator.py:181-191`

```python
# 변경 전
system_prompt = _build_system_prompt(
    schema_info=state["schema_info"],
    default_limit=limit_value,
    column_descriptions=state.get("column_descriptions", {}),
    column_synonyms=state.get("column_synonyms", {}),
    resource_type_synonyms=state.get("resource_type_synonyms"),
    eav_name_synonyms=state.get("eav_name_synonyms"),
    active_db_id=state.get("active_db_id"),
    polestar_db_ids=app_config.get_polestar_db_ids() or None,
    active_db_engine=state.get("active_db_engine"),
)

# 변경 후
system_prompt = _build_system_prompt(
    schema_info=state["schema_info"],
    default_limit=limit_value,
    column_descriptions=state.get("column_descriptions", {}),
    column_synonyms=state.get("column_synonyms", {}),
    resource_type_synonyms=state.get("resource_type_synonyms"),
    eav_name_synonyms=state.get("eav_name_synonyms"),
    active_db_id=state.get("active_db_id"),
    polestar_db_ids=app_config.get_polestar_db_ids() or None,
    active_db_engine=state.get("active_db_engine"),
    routing_intent=state.get("routing_intent"),   # ← 신규 추가
)
```

#### 3-2. `_build_system_prompt()` 시그니처 수정

**변경 위치**: `query_generator.py:227-237`

```python
# 변경 전
def _build_system_prompt(
    schema_info: dict,
    default_limit: int,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    active_db_id: str | None = None,
    polestar_db_ids: set[str] | None = None,
    active_db_engine: str | None = None,
) -> str:

# 변경 후
def _build_system_prompt(
    schema_info: dict,
    default_limit: int,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    active_db_id: str | None = None,
    polestar_db_ids: set[str] | None = None,
    active_db_engine: str | None = None,
    routing_intent: str | None = None,   # ← 신규 추가
) -> str:
```

#### 3-3. `_build_system_prompt()` 내부 — 템플릿 선택 분기 수정

**변경 위치**: `query_generator.py:276-287`

```python
# 변경 전
if polestar_db_ids and active_db_id in polestar_db_ids:
    template = POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
else:
    template = QUERY_GENERATOR_SYSTEM_TEMPLATE

return template.format(
    schema=schema_text,
    default_limit=default_limit,
    structure_guide=structure_guide,
    db_engine_hint=db_engine_hint,
)

# 변경 후
if polestar_db_ids and active_db_id in polestar_db_ids:
    if routing_intent == "alarm_query":
        template = POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE   # ← 신규 상수
    else:
        template = POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
else:
    template = QUERY_GENERATOR_SYSTEM_TEMPLATE

return template.format(
    schema=schema_text,
    default_limit=default_limit,
    structure_guide=structure_guide,
    db_engine_hint=db_engine_hint,
)
```

**설계 근거**: `routing_intent == "alarm_query"` 시 기존 Template A/B 프롬프트 대신
알람 전용 프롬프트(`POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE`)를 사용한다.
Template A/B의 EAV 피벗 규칙이 알람 쿼리 생성을 방해하는 것을 원천 차단한다.

---

### Step 4: `src/prompts/query_generator.py` — `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE` 추가

기존 `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE` 다음에 새 상수를 추가한다.

```python
POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE = """Role: 당신은 POLESTAR 인프라 모니터링 DB의 알람(Alert) 쿼리 생성 전문가이다.
지시사항: 주어진 스키마와 아래 규칙을 엄격히 준수하여 알람 조회 SQL을 작성하라.
스키마에 없는 테이블·컬럼을 임의로 추측하거나 생성(Hallucination)하는 것을 엄격히 금지한다.

[사용 가능한 핵심 테이블]
- CMM_ALARM (CA): 알람 레코드 — ALARMSEVERITY, CTIME, CONDITIONLOGTEXT, CURRENTALARMSTATUS
- CMM_RESOURCE (CR): 장비 정보 — NAME, HOSTNAME, IPADDRESS, RESOURCE_TYPE, PARENT_RESOURCE_ID,
                                   PLATFORM_RESOURCE_ID, SERVICE_RESOURCE_ID, DTIME
- CMM_ALARM_DEF (D): 알람 정의 — NAME(이벤트명), MASTERDEFINITION_ID
- CMM_ALARM_ACTIVE: 현재 활성 알람 필터 (ALARM_ID 컬럼으로 CMM_ALARM과 조인)
- CMM_ALARM_DEF_NOTI (DN): 알림 정의 (담당자/그룹/역할 조회 시에만 추가)
- CMM_ALARM_DEF_NOTI_USER (DNU): 알림 사용자
- CMM_ALARM_DEF_NOTI_GROUP (DNG): 알림 그룹
- CMM_ALARM_DEF_NOTI_ROLE (DNL): 알림 역할
- CMM_ALARM_DEF_NOTI_RMTYPE (DNR): 알림 리소스 관리 타입
- ACC_ACL_RESOURCE_MANAGER_TYPE (RMT): 리소스 관리자 타입
- ACC_ROLE (ACR): 역할
- ACC_USER_GROUP (AUG): 사용자 그룹

[필수 WHERE 조건 — 반드시 포함 (innermost 서브쿼리 안)]
- CR.DTIME IS NULL                  -- 삭제된 리소스 제외
- CA.ALARMSEVERITY IN (1, 2, 3)     -- 유효한 심각도만 (1=주의, 2=경고, 3=심각)

[심각도 매핑]
- 심각/critical/CRITICAL → ALARMSEVERITY = 3
- 경고/warning/WARNING   → ALARMSEVERITY = 2
- 주의/info/INFO/notice  → ALARMSEVERITY = 1
- 미지정 시 → IN (1, 2, 3) 전체 포함

[리소스 타입 매핑 — innermost 서브쿼리 WHERE에 추가]
- 서버/server → CR.RESOURCE_TYPE = 'server.Server'
- 네트워크 장비/NMS → CR.RESOURCE_TYPE = 'network.NMSNode'
- 인터페이스 → CR.RESOURCE_TYPE IN ('network.Interface', 'network.VirtualInterface')
- 미지정 시 → RESOURCE_TYPE 조건 없음 (전체 장비)

[현재 활성 알람 vs 알람 이력 분기]
- "현재 알람", "발생 중인 알람" → innermost 서브쿼리에 CMM_ALARM_ACTIVE JOIN 반드시 포함
  JOIN CMM_ALARM_ACTIVE A ON A.ALARM_ID = CA.ID
- "알람 이력", "지난 N일/월 알람", "특정 기간 알람" → CMM_ALARM_ACTIVE JOIN 제외, 외부 WHERE에 기간 조건만 적용

[CONDITIONLOGTEXT 키워드 필터 — innermost 서브쿼리 WHERE에 추가]
- CPU 알람 → AND UPPER(CA.CONDITIONLOGTEXT) LIKE '%CPU%'
- 메모리 알람 → AND UPPER(CA.CONDITIONLOGTEXT) LIKE '%MEMORY%' OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%메모리%'
- 디스크 알람 → AND UPPER(CA.CONDITIONLOGTEXT) LIKE '%DISK%' OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%디스크%'
- 사용률 → AND UPPER(CA.CONDITIONLOGTEXT) LIKE '%사용률%'

[시간 범위 지정 — 외부 WHERE에 적용]
- TO_TIMESTAMP('YYYY-MM-DD HH24:MI:SS', 'YYYY-MM-DD HH24:MI:SS') 사용
- 특정 기간: A.CTIME BETWEEN TO_TIMESTAMP(...) AND TO_TIMESTAMP(...)
- 이번 달: A.CTIME >= DATE_TRUNC('month', CURRENT_DATE)
- 하드코딩 날짜 절대 사용 금지 — CURRENT_DATE 기반 동적 계산 사용

[GROUP_PATH 계층 경로 구성]
- 장비의 계층 경로(소속 그룹 경로)를 표시할 때 C2~C10 셀프 조인 사용
- LTRIM(CONCAT_WS('>', C10.NAME, C9.NAME, ..., C2.NAME, A.PARENT_NAME), '>') AS GROUP_PATH
- GROUP_PATH가 불필요한 경우 C2~C10 조인 전체 생략 (쿼리 단순화)

[담당자 정보 조회 시]
사용자가 담당자, 알림 수신자, 역할/그룹 정보를 요청하는 경우에만 아래 조인 추가:
  LEFT JOIN CMM_ALARM_DEF_NOTI DN ON DN.DEFINITION_ID = D.MASTERDEFINITION_ID
  LEFT JOIN CMM_ALARM_DEF_NOTI_USER DNU ON DNU.ALARMNOTIFICATION_ID = DN.ID
  LEFT JOIN CMM_ALARM_DEF_NOTI_GROUP DNG ON DNG.ALARMNOTIFICATION_ID = DN.ID
  LEFT JOIN CMM_ALARM_DEF_NOTI_ROLE DNL ON DNL.ALARMNOTIFICATION_ID = DN.ID
  LEFT JOIN CMM_ALARM_DEF_NOTI_RMTYPE DNR ON DNR.ALARMNOTIFICATION_ID = DN.ID
  LEFT JOIN ACC_ACL_RESOURCE_MANAGER_TYPE RMT ON RMT.ID = DNR.TARGETRESOURCEMANAGERTYPES
  LEFT JOIN ACC_ROLE ACR ON ACR.ID = DNL.TARGETROLES
  LEFT JOIN ACC_USER_GROUP AUG ON AUG.ID = DNG.TARGETGROUPS
담당자 정보 불필요 시 위 조인 전체 생략하여 쿼리를 단순화한다.

---

[Template C-1 — 알람 목록 조회: 기본 패턴 (현재 활성 알람 또는 기간 이력)]

현재 발생 중인 알람 목록 또는 알람 이력을 조회할 때 사용한다.
GROUP_PATH(계층 경로)를 포함하는 실제 운영 패턴이다.

```sql
SELECT
    A.ALARMSEVERITY AS "등급",
    TO_CHAR(A.CTIME, 'YYYY-MM-DD HH24:MI:SS') AS "발생시간",
    CR.NAME AS "장비명",
    CR.IPADDRESS AS "IP",
    LTRIM(
        CONCAT_WS('>',
            C10.NAME, C9.NAME, C8.NAME, C7.NAME, C6.NAME,
            C5.NAME, C4.NAME, C3.NAME, C2.NAME, A.PARENT_NAME
        ), '>'
    ) AS "GROUP_PATH",
    CR.HOSTNAME AS "호스트명",
    A.RESOURCE_NAME AS "리소스명",
    A.ALARM_NAME AS "이벤트",
    A.CONDITIONLOGTEXT AS "상세내용",
    A.ID
FROM (
    SELECT
        AR.ALARMSEVERITY,
        AR.CTIME,
        AR.ALARM_NAME,
        AR.RESOURCE_NAME,
        AR.CONDITIONLOGTEXT,
        AR.ID,
        AR.HOSTNAME,
        C.NAME AS PARENT_NAME,
        C.PARENT_RESOURCE_ID
    FROM (
        SELECT
            CASE
                WHEN CA.ALARMSEVERITY = 1 THEN '주의'
                WHEN CA.ALARMSEVERITY = 2 THEN '경고'
                WHEN CA.ALARMSEVERITY = 3 THEN '심각'
                ELSE ''
            END AS ALARMSEVERITY,
            CA.CTIME,
            CR.NAME AS RESOURCE_NAME,
            D.NAME AS ALARM_NAME,
            UPPER(CA.CONDITIONLOGTEXT) AS CONDITIONLOGTEXT,
            COALESCE(
                CR.PLATFORM_RESOURCE_ID,
                COALESCE(CR.SERVICE_RESOURCE_ID, CR.ID)
            ) AS ID,
            CR.HOSTNAME,
            CR.PARENT_RESOURCE_ID
        FROM CMM_RESOURCE CR
        JOIN CMM_ALARM CA ON CA.RESOURCE_ID = CR.ID
        JOIN CMM_ALARM_DEF D ON CA.DEFINITION_ID = D.ID
        JOIN CMM_ALARM_ACTIVE A ON A.ALARM_ID = CA.ID  -- 현재 활성 알람만 (이력 조회 시 이 행 제거)
        WHERE CR.DTIME IS NULL
          AND CA.ALARMSEVERITY IN (1, 2, 3)
          -- 리소스 타입 필터 예시: AND CR.RESOURCE_TYPE = 'server.Server'
          -- 키워드 필터 예시: AND UPPER(CA.CONDITIONLOGTEXT) LIKE '%CPU%'
    ) AR
    LEFT JOIN CMM_RESOURCE C ON AR.PARENT_RESOURCE_ID = C.ID
) A
LEFT JOIN CMM_RESOURCE CR  ON A.ID = CR.ID
LEFT JOIN CMM_RESOURCE C2  ON A.PARENT_RESOURCE_ID = C2.ID
LEFT JOIN CMM_RESOURCE C3  ON C2.PARENT_RESOURCE_ID = C3.ID
LEFT JOIN CMM_RESOURCE C4  ON C3.PARENT_RESOURCE_ID = C4.ID
LEFT JOIN CMM_RESOURCE C5  ON C4.PARENT_RESOURCE_ID = C5.ID
LEFT JOIN CMM_RESOURCE C6  ON C5.PARENT_RESOURCE_ID = C6.ID
LEFT JOIN CMM_RESOURCE C7  ON C6.PARENT_RESOURCE_ID = C7.ID
LEFT JOIN CMM_RESOURCE C8  ON C7.PARENT_RESOURCE_ID = C8.ID
LEFT JOIN CMM_RESOURCE C9  ON C8.PARENT_RESOURCE_ID = C9.ID
LEFT JOIN CMM_RESOURCE C10 ON C9.PARENT_RESOURCE_ID = C10.ID
-- 기간 필터 예시 (알람 이력 조회 시):
-- WHERE A.CTIME BETWEEN
--     TO_TIMESTAMP('2026-05-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')
-- AND TO_TIMESTAMP('2026-05-31 23:59:59', 'YYYY-MM-DD HH24:MI:SS')
ORDER BY A.CTIME DESC
LIMIT {default_limit};
```

---

[Template C-2 — 서버 알람 이력: 특정 기간 조회 패턴]

"서버에 대한 특정 기간동안의 알람 확인" 질의에 사용한다.
- innermost WHERE에 `CR.RESOURCE_TYPE = 'server.Server'` 추가
- CMM_ALARM_ACTIVE JOIN 제거 (이력 조회)
- 외부 WHERE에 기간 조건 추가

```sql
SELECT
    A.ALARMSEVERITY AS "등급",
    TO_CHAR(A.CTIME, 'YYYY-MM-DD HH24:MI:SS') AS "발생시간",
    CR.NAME AS "장비명",
    CR.IPADDRESS AS "IP",
    LTRIM(
        CONCAT_WS('>',
            C10.NAME, C9.NAME, C8.NAME, C7.NAME, C6.NAME,
            C5.NAME, C4.NAME, C3.NAME, C2.NAME, A.PARENT_NAME
        ), '>'
    ) AS "GROUP_PATH",
    CR.HOSTNAME AS "호스트명",
    A.RESOURCE_NAME AS "리소스명",
    A.ALARM_NAME AS "이벤트",
    A.CONDITIONLOGTEXT AS "상세내용",
    A.ID
FROM (
    SELECT
        AR.ALARMSEVERITY,
        AR.CTIME,
        AR.ALARM_NAME,
        AR.RESOURCE_NAME,
        AR.CONDITIONLOGTEXT,
        AR.ID,
        AR.HOSTNAME,
        C.NAME AS PARENT_NAME,
        C.PARENT_RESOURCE_ID
    FROM (
        SELECT
            CASE
                WHEN CA.ALARMSEVERITY = 1 THEN '주의'
                WHEN CA.ALARMSEVERITY = 2 THEN '경고'
                WHEN CA.ALARMSEVERITY = 3 THEN '심각'
                ELSE ''
            END AS ALARMSEVERITY,
            CA.CTIME,
            CR.NAME AS RESOURCE_NAME,
            D.NAME AS ALARM_NAME,
            UPPER(CA.CONDITIONLOGTEXT) AS CONDITIONLOGTEXT,
            COALESCE(
                CR.PLATFORM_RESOURCE_ID,
                COALESCE(CR.SERVICE_RESOURCE_ID, CR.ID)
            ) AS ID,
            CR.HOSTNAME,
            CR.PARENT_RESOURCE_ID
        FROM CMM_RESOURCE CR
        JOIN CMM_ALARM CA ON CA.RESOURCE_ID = CR.ID
        JOIN CMM_ALARM_DEF D ON CA.DEFINITION_ID = D.ID
        -- CMM_ALARM_ACTIVE JOIN 없음 (이력 조회)
        WHERE CR.DTIME IS NULL
          AND CA.ALARMSEVERITY IN (1, 2, 3)
          AND CR.RESOURCE_TYPE = 'server.Server'  -- 서버만 필터
    ) AR
    LEFT JOIN CMM_RESOURCE C ON AR.PARENT_RESOURCE_ID = C.ID
) A
LEFT JOIN CMM_RESOURCE CR  ON A.ID = CR.ID
LEFT JOIN CMM_RESOURCE C2  ON A.PARENT_RESOURCE_ID = C2.ID
LEFT JOIN CMM_RESOURCE C3  ON C2.PARENT_RESOURCE_ID = C3.ID
LEFT JOIN CMM_RESOURCE C4  ON C3.PARENT_RESOURCE_ID = C4.ID
LEFT JOIN CMM_RESOURCE C5  ON C4.PARENT_RESOURCE_ID = C5.ID
LEFT JOIN CMM_RESOURCE C6  ON C5.PARENT_RESOURCE_ID = C6.ID
LEFT JOIN CMM_RESOURCE C7  ON C6.PARENT_RESOURCE_ID = C7.ID
LEFT JOIN CMM_RESOURCE C8  ON C7.PARENT_RESOURCE_ID = C8.ID
LEFT JOIN CMM_RESOURCE C9  ON C8.PARENT_RESOURCE_ID = C9.ID
LEFT JOIN CMM_RESOURCE C10 ON C9.PARENT_RESOURCE_ID = C10.ID
WHERE A.CTIME BETWEEN
    TO_TIMESTAMP('{{시작시간}}', 'YYYY-MM-DD HH24:MI:SS')
AND TO_TIMESTAMP('{{끝시간}}', 'YYYY-MM-DD HH24:MI:SS')
ORDER BY A.CTIME DESC
LIMIT {default_limit};
```

---

[Template C-3 — 서버 CPU/메모리 알람 이력: 특정 기간 조회 패턴]

"서버의 CPU 및 메모리에 대한 특정 기간동안의 알람 확인" 질의에 사용한다.
- Template C-2에서 CONDITIONLOGTEXT 키워드 필터 추가
- CPU: `UPPER(CA.CONDITIONLOGTEXT) LIKE '%CPU%'`
- 메모리: `UPPER(CA.CONDITIONLOGTEXT) LIKE '%MEMORY%' OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%메모리%'`
- CPU + 메모리 동시: OR 조합

```sql
SELECT
    A.ALARMSEVERITY AS "등급",
    TO_CHAR(A.CTIME, 'YYYY-MM-DD HH24:MI:SS') AS "발생시간",
    CR.NAME AS "장비명",
    CR.IPADDRESS AS "IP",
    LTRIM(
        CONCAT_WS('>',
            C10.NAME, C9.NAME, C8.NAME, C7.NAME, C6.NAME,
            C5.NAME, C4.NAME, C3.NAME, C2.NAME, A.PARENT_NAME
        ), '>'
    ) AS "GROUP_PATH",
    CR.HOSTNAME AS "호스트명",
    A.RESOURCE_NAME AS "리소스명",
    A.ALARM_NAME AS "이벤트",
    A.CONDITIONLOGTEXT AS "상세내용",
    A.ID
FROM (
    SELECT
        AR.ALARMSEVERITY,
        AR.CTIME,
        AR.ALARM_NAME,
        AR.RESOURCE_NAME,
        AR.CONDITIONLOGTEXT,
        AR.ID,
        AR.HOSTNAME,
        C.NAME AS PARENT_NAME,
        C.PARENT_RESOURCE_ID
    FROM (
        SELECT
            CASE
                WHEN CA.ALARMSEVERITY = 1 THEN '주의'
                WHEN CA.ALARMSEVERITY = 2 THEN '경고'
                WHEN CA.ALARMSEVERITY = 3 THEN '심각'
                ELSE ''
            END AS ALARMSEVERITY,
            CA.CTIME,
            CR.NAME AS RESOURCE_NAME,
            D.NAME AS ALARM_NAME,
            UPPER(CA.CONDITIONLOGTEXT) AS CONDITIONLOGTEXT,
            COALESCE(
                CR.PLATFORM_RESOURCE_ID,
                COALESCE(CR.SERVICE_RESOURCE_ID, CR.ID)
            ) AS ID,
            CR.HOSTNAME,
            CR.PARENT_RESOURCE_ID
        FROM CMM_RESOURCE CR
        JOIN CMM_ALARM CA ON CA.RESOURCE_ID = CR.ID
        JOIN CMM_ALARM_DEF D ON CA.DEFINITION_ID = D.ID
        WHERE CR.DTIME IS NULL
          AND CA.ALARMSEVERITY IN (1, 2, 3)
          AND CR.RESOURCE_TYPE = 'server.Server'
          AND (
              UPPER(CA.CONDITIONLOGTEXT) LIKE '%CPU%'
              OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%MEMORY%'
              OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%메모리%'
          )
    ) AR
    LEFT JOIN CMM_RESOURCE C ON AR.PARENT_RESOURCE_ID = C.ID
) A
LEFT JOIN CMM_RESOURCE CR  ON A.ID = CR.ID
LEFT JOIN CMM_RESOURCE C2  ON A.PARENT_RESOURCE_ID = C2.ID
LEFT JOIN CMM_RESOURCE C3  ON C2.PARENT_RESOURCE_ID = C3.ID
LEFT JOIN CMM_RESOURCE C4  ON C3.PARENT_RESOURCE_ID = C4.ID
LEFT JOIN CMM_RESOURCE C5  ON C4.PARENT_RESOURCE_ID = C5.ID
LEFT JOIN CMM_RESOURCE C6  ON C5.PARENT_RESOURCE_ID = C6.ID
LEFT JOIN CMM_RESOURCE C7  ON C6.PARENT_RESOURCE_ID = C7.ID
LEFT JOIN CMM_RESOURCE C8  ON C7.PARENT_RESOURCE_ID = C8.ID
LEFT JOIN CMM_RESOURCE C9  ON C8.PARENT_RESOURCE_ID = C9.ID
LEFT JOIN CMM_RESOURCE C10 ON C9.PARENT_RESOURCE_ID = C10.ID
WHERE A.CTIME BETWEEN
    TO_TIMESTAMP('{{시작시간}}', 'YYYY-MM-DD HH24:MI:SS')
AND TO_TIMESTAMP('{{끝시간}}', 'YYYY-MM-DD HH24:MI:SS')
ORDER BY A.CTIME DESC
LIMIT {default_limit};
```

---

[Template C-4 — 알람 집계 패턴]

장비별/심각도별 알람 발생 횟수 집계 시 사용한다. GROUP_PATH 불필요 시 C2~C10 조인 생략.

```sql
SELECT
    CR.NAME AS "장비명",
    CR.HOSTNAME AS "호스트명",
    CR.IPADDRESS AS "IP",
    COUNT(*) AS "총_알람_수",
    COUNT(CASE WHEN A.ALARMSEVERITY = '심각' THEN 1 END) AS "심각_수",
    COUNT(CASE WHEN A.ALARMSEVERITY = '경고' THEN 1 END) AS "경고_수",
    COUNT(CASE WHEN A.ALARMSEVERITY = '주의' THEN 1 END) AS "주의_수",
    MAX(A.CTIME) AS "최근_발생시각"
FROM (
    SELECT
        CASE
            WHEN CA.ALARMSEVERITY = 1 THEN '주의'
            WHEN CA.ALARMSEVERITY = 2 THEN '경고'
            WHEN CA.ALARMSEVERITY = 3 THEN '심각'
            ELSE ''
        END AS ALARMSEVERITY,
        CA.CTIME,
        COALESCE(
            CR.PLATFORM_RESOURCE_ID,
            COALESCE(CR.SERVICE_RESOURCE_ID, CR.ID)
        ) AS ID
    FROM CMM_RESOURCE CR
    JOIN CMM_ALARM CA ON CA.RESOURCE_ID = CR.ID
    JOIN CMM_ALARM_DEF D ON CA.DEFINITION_ID = D.ID
    WHERE CR.DTIME IS NULL
      AND CA.ALARMSEVERITY IN (1, 2, 3)
      -- 서버만 집계 예시: AND CR.RESOURCE_TYPE = 'server.Server'
) A
LEFT JOIN CMM_RESOURCE CR ON A.ID = CR.ID
-- 기간 조건 예시: WHERE A.CTIME >= DATE_TRUNC('month', CURRENT_DATE)
GROUP BY CR.NAME, CR.HOSTNAME, CR.IPADDRESS
ORDER BY "총_알람_수" DESC
LIMIT {default_limit};
```

---

[Template C-5 — 전체 장비 알람 이력: 특정 기간 조회 패턴]

"특정 기간동안의 모든 알람 조회" 질의에 사용한다.
- CMM_ALARM_ACTIVE JOIN 없음 (이력 조회)
- RESOURCE_TYPE 필터 없음 (서버·네트워크 등 전체 장비)
- 외부 WHERE에 기간 조건 필수

```sql
SELECT
    A.ALARMSEVERITY AS "등급",
    TO_CHAR(A.CTIME, 'YYYY-MM-DD HH24:MI:SS') AS "발생시간",
    CR.NAME AS "장비명",
    CR.IPADDRESS AS "IP",
    LTRIM(
        CONCAT_WS('>',
            C10.NAME, C9.NAME, C8.NAME, C7.NAME, C6.NAME,
            C5.NAME, C4.NAME, C3.NAME, C2.NAME, A.PARENT_NAME
        ), '>'
    ) AS "GROUP_PATH",
    CR.HOSTNAME AS "호스트명",
    A.RESOURCE_NAME AS "리소스명",
    A.ALARM_NAME AS "이벤트",
    A.CONDITIONLOGTEXT AS "상세내용",
    A.ID
FROM (
    SELECT
        AR.ALARMSEVERITY,
        AR.CTIME,
        AR.ALARM_NAME,
        AR.RESOURCE_NAME,
        AR.CONDITIONLOGTEXT,
        AR.ID,
        AR.HOSTNAME,
        C.NAME AS PARENT_NAME,
        C.PARENT_RESOURCE_ID
    FROM (
        SELECT
            CASE
                WHEN CA.ALARMSEVERITY = 1 THEN '주의'
                WHEN CA.ALARMSEVERITY = 2 THEN '경고'
                WHEN CA.ALARMSEVERITY = 3 THEN '심각'
                ELSE ''
            END AS ALARMSEVERITY,
            CA.CTIME,
            CR.NAME AS RESOURCE_NAME,
            D.NAME AS ALARM_NAME,
            UPPER(CA.CONDITIONLOGTEXT) AS CONDITIONLOGTEXT,
            COALESCE(
                CR.PLATFORM_RESOURCE_ID,
                COALESCE(CR.SERVICE_RESOURCE_ID, CR.ID)
            ) AS ID,
            CR.HOSTNAME,
            CR.PARENT_RESOURCE_ID
        FROM CMM_RESOURCE CR
        JOIN CMM_ALARM CA ON CA.RESOURCE_ID = CR.ID
        JOIN CMM_ALARM_DEF D ON CA.DEFINITION_ID = D.ID
        -- CMM_ALARM_ACTIVE JOIN 없음 (이력 조회)
        -- RESOURCE_TYPE 조건 없음 (전체 장비)
        WHERE CR.DTIME IS NULL
          AND CA.ALARMSEVERITY IN (1, 2, 3)
    ) AR
    LEFT JOIN CMM_RESOURCE C ON AR.PARENT_RESOURCE_ID = C.ID
) A
LEFT JOIN CMM_RESOURCE CR  ON A.ID = CR.ID
LEFT JOIN CMM_RESOURCE C2  ON A.PARENT_RESOURCE_ID = C2.ID
LEFT JOIN CMM_RESOURCE C3  ON C2.PARENT_RESOURCE_ID = C3.ID
LEFT JOIN CMM_RESOURCE C4  ON C3.PARENT_RESOURCE_ID = C4.ID
LEFT JOIN CMM_RESOURCE C5  ON C4.PARENT_RESOURCE_ID = C5.ID
LEFT JOIN CMM_RESOURCE C6  ON C5.PARENT_RESOURCE_ID = C6.ID
LEFT JOIN CMM_RESOURCE C7  ON C6.PARENT_RESOURCE_ID = C7.ID
LEFT JOIN CMM_RESOURCE C8  ON C7.PARENT_RESOURCE_ID = C8.ID
LEFT JOIN CMM_RESOURCE C9  ON C8.PARENT_RESOURCE_ID = C9.ID
LEFT JOIN CMM_RESOURCE C10 ON C9.PARENT_RESOURCE_ID = C10.ID
WHERE A.CTIME BETWEEN
    TO_TIMESTAMP('{{시작시간}}', 'YYYY-MM-DD HH24:MI:SS')
AND TO_TIMESTAMP('{{끝시간}}', 'YYYY-MM-DD HH24:MI:SS')
ORDER BY A.CTIME DESC
LIMIT {default_limit};
```

---

{db_engine_hint}

{structure_guide}

## 스키마 정보

{schema}
"""
```

---

## 4. 의도 전파 흐름 (변경 후)

```
사용자 입력: "현재 발생 중인 서버 알람 보여줘"
        ↓
[1] semantic_router
    LLM 분류 (alarm_query 판단 규칙 적용)
    → routing_intent = "alarm_query"
    → target_databases = [{"db_id": "polestar", ...}]
    state["routing_intent"] = "alarm_query"   ← State에 저장
        ↓
[2] schema_analyzer
    polestar DB 스키마 탐색
    (CMM_ALARM, CMM_RESOURCE 등 알람 테이블 포함)
        ↓
[3] query_generator
    _build_system_prompt(..., routing_intent="alarm_query")
      ↓
    polestar_db_ids에 포함 AND routing_intent == "alarm_query"
      → POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE 선택
      → Template C 패턴 + 알람 테이블 규칙이 프롬프트에 주입
      → LLM이 CMM_ALARM 기반 SQL 생성
        ↓
[4] query_validator
    SQL 검증 (읽기 전용 확인, 테이블 존재 여부)
        ↓
[5] query_executor → result_organizer → output_generator
```

**기존 data_query 흐름과의 비교**:

```
routing_intent = "data_query"
  → POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE 선택 (Template A/B)
  → EAV 피벗, 성능 지표 패턴 적용

routing_intent = "alarm_query"
  → POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE 선택 (Template C)  ← 신규
  → 알람 테이블 규칙, Template C 패턴 적용
```

---

## 5. 주의사항

| 항목 | 내용 |
|------|------|
| **backward compatibility** | routing_intent가 None(레거시 단일 DB 모드)이거나 "data_query"이면 기존 템플릿 그대로 사용. 변경 전 동작 유지 |
| **템플릿 format 인수 통일** | `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE`도 `{schema}`, `{default_limit}`, `{structure_guide}`, `{db_engine_hint}` 4개 인수를 동일하게 사용하여 `template.format()` 호출부를 수정 없이 재사용 |
| **DB2 지원 여부** | polestar/polestar_b0는 DB2 엔진. Template C는 PostgreSQL 문법(`TO_CHAR`, `DATE_TRUNC`)으로 작성되어 있으므로 `{db_engine_hint}`에서 DB2 문법 분기 안내 추가 필요. 우선 PostgreSQL 대상(polestar_cm_gp, polestar_cm_yd)만 검증 |
| **캐시 갱신** | 알람 테이블이 Redis 스키마 캐시에 없으면 schema_analyzer가 탐색하지 못할 수 있음. 변경 후 해당 polestar DB의 Redis 캐시 갱신 필요 |
| **SQL 안전성** | 기존 `sql_guard.py`가 INSERT/UPDATE/DELETE/DDL 차단. 별도 조치 불필요 |
| **sub_query_context 규칙** | "김포", "여의도" 등 위치 정보는 sub_query_context에 포함하지 않는 기존 규칙 유지 |

---

## 6. 작업 체크리스트

**Step 1 — domain_config.py 수정**
- [ ] `src/routing/domain_config.py`: polestar 4개 도메인 description에 alert 정보 추가 (7.8 참고)

**Step 2 — prompts/semantic_router.py 수정**
- [ ] 출력 JSON의 `intent` 값에 `"alarm_query"` 추가 (출력 형식 섹션)
- [ ] `## 알람 조회 판단` 섹션 추가 (판단 패턴 목록 포함)
- [ ] 예시 섹션에 alarm_query 사례 7건 추가 (서버 알람 기간 조회, CPU/메모리 알람 포함)

**Step 3 — nodes/query_generator.py 수정**
- [ ] `query_generator()` 내 `_build_system_prompt()` 호출부에 `routing_intent=state.get("routing_intent")` 추가
- [ ] `_build_system_prompt()` 시그니처에 `routing_intent: str | None = None` 파라미터 추가
- [ ] 템플릿 선택 분기 수정: `routing_intent == "alarm_query"` 시 `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE` 사용
- [ ] `from src.prompts.query_generator import POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE` import 추가

**Step 4 — prompts/query_generator.py 수정**
- [ ] `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE` 상수 추가 (Template C-1~C-5 포함)
- [ ] Template C-1: 기본 패턴 (3단 서브쿼리 + C2~C10 GROUP_PATH + CMM_ALARM_ACTIVE 조건)
- [ ] Template C-2: 서버 알람 기간 이력 패턴 (RESOURCE_TYPE 서버 필터 + 기간 조건)
- [ ] Template C-3: 서버 CPU/메모리 알람 기간 이력 패턴 (CONDITIONLOGTEXT 키워드 필터 추가)
- [ ] Template C-4: 알람 집계 패턴 (GROUP BY, GROUP_PATH 제외)
- [ ] Template C-5: 전체 장비 기간 이력 패턴 (CMM_ALARM_ACTIVE 없음, RESOURCE_TYPE 무관, 기간 조건)

**검증**
- [ ] "현재 발생 중인 알람 보여줘" → `routing_intent = "alarm_query"` 확인
- [ ] `POLESTAR_ALARM_QUERY_GENERATOR_SYSTEM_TEMPLATE` 선택 여부 확인 (로그)
- [ ] 생성된 SQL에 `CMM_ALARM`, `CMM_ALARM_ACTIVE` 포함 여부 확인 (Template C-1)
- [ ] "5월 한 달간 전체 알람 보여줘" → CMM_ALARM_ACTIVE 없음 + 기간 조건 포함 확인 (Template C-5)
- [ ] "5월 한 달간 서버 알람 보여줘" → RESOURCE_TYPE = 'server.Server' + 기간 조건 포함 확인 (Template C-2)
- [ ] "이번 달 서버 CPU/메모리 알람" → CONDITIONLOGTEXT LIKE 필터 포함 확인 (Template C-3)
- [ ] 기존 "서버 CPU 사용률 보여줘" → `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE` 유지 확인 (회귀 없음)
- [ ] (선택) polestar Redis 스키마 캐시 갱신 후 CMM_ALARM 테이블 탐색 여부 확인

---

## 7. 알람 쿼리 테이블 구조 레퍼런스

### 7.1 핵심 테이블 목록

| 테이블명 | 별칭 | 역할 |
|---------|------|------|
| `CMM_ALARM` | CA | 알람 레코드 (심각도, 발생시간, 상태, 담당자) |
| `CMM_RESOURCE` | CR | 장비 정보 (장비명, IP, hostname, 리소스 타입, 부모 리소스 ID) |
| `CMM_ALARM_DEF` | D | 알람 정의 (알람명, 마스터 정의 ID, 모니터 템플릿 ID) |
| `CMM_ALARM_ACTIVE` | A | 현재 활성 알람 필터 |
| `CMM_ALARM_DEF_NOTI` | DN | 알람 알림 정의 |
| `CMM_ALARM_DEF_NOTI_GROUP` | DNG | 알림 그룹 |
| `CMM_ALARM_DEF_NOTI_RMTYPE` | DNR | 알림 리소스 관리 타입 |
| `CMM_ALARM_DEF_NOTI_ROLE` | DNL | 알림 역할 |
| `CMM_ALARM_DEF_NOTI_USER` | DNU | 알림 사용자 |
| `ACC_ACL_RESOURCE_MANAGER_TYPE` | RMT | 리소스 관리자 타입 |
| `ACC_ROLE` | ACR | 역할 |
| `ACC_USER_GROUP` | AUG | 사용자 그룹 |

### 7.2 CMM_ALARM 주요 컬럼

| 컬럼명 | 의미 | 비고 |
|-------|------|------|
| `ALARMSEVERITY` | 알람 심각도 | 1=주의, 2=경고, 3=심각 |
| `CTIME` | 알람 발생 시각 | TIMESTAMP |
| `DEFINITION_ID` | 알람 정의 ID | CMM_ALARM_DEF 조인 키 |
| `RESOURCE_ID` | 리소스 ID | CMM_RESOURCE 조인 키 |
| `CONDITIONLOGTEXT` | 알람 상세 내용 | |
| `ACKUSERNAME` | 알람 담당자 (확인자) | NULL이면 미확인 |
| `CURRENTALARMSTATUS` | 알람 현재 상태 | 'NOT_ACK', 'ACK' 등 |

### 7.3 CMM_RESOURCE 주요 컬럼

| 컬럼명 | 의미 | 비고 |
|-------|------|------|
| `NAME` | 장비명 (표시명) | |
| `HOSTNAME` | 호스트명 | |
| `IPADDRESS` | IP 주소 | |
| `RESOURCE_TYPE` | 리소스 타입 | 'server.Server', 'network.NMSNode' 등 |
| `PARENT_RESOURCE_ID` | 부모 리소스 ID | 그룹 계층 경로 구성 |
| `DTIME` | 삭제 시각 | NULL이면 유효한 리소스 |

### 7.4 현재 활성 알람 vs 알람 이력 구분

| 질의 유형 | 구분 조건 | 설명 |
|----------|---------|------|
| 현재 활성 알람 | `JOIN CMM_ALARM_ACTIVE A ON A.ALARM_ID = CA.ID` | 현재 발생 중인 알람만 |
| 전체 알람 이력 | CMM_ALARM_ACTIVE JOIN 없이 기간 조건 적용 | 발생/복구 포함 전체 이력 |
| 미확인 알람 | `AND CA.CURRENTALARMSTATUS = 'NOT_ACK'` | 담당자 미배정 알람 |

### 7.5 심각도 코드 변환

| 사용자 입력 | ALARMSEVERITY 값 |
|------------|----------------|
| 심각, critical | 3 |
| 경고, warning | 2 |
| 주의, info, notice | 1 |

### 7.6 리소스 타입 분류

| 사용자 표현 | RESOURCE_TYPE 값 |
|-----------|----------------|
| 서버, server | `'server.Server'` |
| 네트워크 장비, NMS | `'network.NMSNode'` |
| 인터페이스, interface | `'network.Interface'`, `'network.VirtualInterface'` |

### 7.7 GROUP_PATH 계층 경로 구성

CMM_RESOURCE는 `PARENT_RESOURCE_ID`로 계층 구조를 표현한다.
알람이 발생한 장비의 소속 그룹 경로를 표시하려면 C2~C10 셀프 조인으로 상위 계층을 순차적으로 따라 올라간다.

| 변수 | 역할 | 조인 조건 |
|------|------|---------|
| `A.PARENT_NAME` | 직속 부모 그룹명 (middle 서브쿼리에서 계산) | `LEFT JOIN CMM_RESOURCE C ON AR.PARENT_RESOURCE_ID = C.ID` |
| `C2` | 2단계 부모 | `LEFT JOIN CMM_RESOURCE C2 ON A.PARENT_RESOURCE_ID = C2.ID` |
| `C3~C10` | 3~10단계 부모 | 순차 체인 |

```sql
LTRIM(CONCAT_WS('>', C10.NAME, ..., C2.NAME, A.PARENT_NAME), '>') AS "GROUP_PATH"
```
- `CONCAT_WS('>', ...)`: NULL인 상위 레벨은 자동으로 건너뜀
- `LTRIM(..., '>')`: 모든 상위가 NULL이면 앞에 '>'가 붙는 것을 제거

**GROUP_PATH가 필요 없는 경우**: C2~C10 조인 및 PARENT_NAME 계산 전체 생략 → 쿼리 성능 향상

### 7.8 ID COALESCE 패턴

```sql
COALESCE(
    CR.PLATFORM_RESOURCE_ID,
    COALESCE(CR.SERVICE_RESOURCE_ID, CR.ID)
) AS ID
```

플랫폼 가상화/서비스 계층이 있는 경우 실제 리소스 ID가 다를 수 있으므로 우선순위에 따라 선택한다.
외부 조인 `LEFT JOIN CMM_RESOURCE CR ON A.ID = CR.ID`는 이 COALESCE'd ID를 사용하여 실제 장비 정보를 조인한다.

### 7.9 알람 질의 유형별 Template 선택 가이드

| 사용자 질의 패턴 | Template | 핵심 변경 사항 |
|---------------|---------|-------------|
| 현재 발생 중인 알람 전체 | C-1 (기본) | CMM_ALARM_ACTIVE JOIN 포함, 기간 조건 없음 |
| 전체 장비 특정 기간 알람 이력 | C-5 | CMM_ALARM_ACTIVE 없음, RESOURCE_TYPE 무관, 기간 조건 필수 |
| 서버 알람 특정 기간 | C-2 | C-5 + `CR.RESOURCE_TYPE = 'server.Server'` |
| 서버 CPU/메모리 알람 특정 기간 | C-3 | C-2 + CONDITIONLOGTEXT LIKE 키워드 필터 |
| 장비별 알람 발생 횟수 집계 | C-4 | GROUP BY 집계, GROUP_PATH 불필요 |

### 7.10 CONDITIONLOGTEXT 키워드 필터 패턴

| 알람 유형 | 필터 조건 |
|---------|---------|
| CPU | `UPPER(CA.CONDITIONLOGTEXT) LIKE '%CPU%'` |
| 메모리 | `UPPER(CA.CONDITIONLOGTEXT) LIKE '%MEMORY%' OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%메모리%'` |
| 디스크 | `UPPER(CA.CONDITIONLOGTEXT) LIKE '%DISK%' OR UPPER(CA.CONDITIONLOGTEXT) LIKE '%디스크%'` |
| 사용률 | `UPPER(CA.CONDITIONLOGTEXT) LIKE '%사용률%'` |
| CPU + 메모리 | 위 CPU, 메모리 조건을 OR로 결합 |
