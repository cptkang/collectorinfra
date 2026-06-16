# Plan 47: 알람 이력 기반 패턴 분석 고도화 — 폴스타 DB 조회 방식

> **작성일**: 2026-06-11
> **개정**: 2026-06-11 — 초안의 Redis 자체 이력 적재 방식을 **폴스타 DB 직접 조회 방식**으로 변경 (사용자 결정: 별도 저장소 불필요, 폴스타 DB에 이력이 이미 존재)
> **관련 Plan**: Plan 46 (알람 소켓 수신, §12.1 일상 알람 판별 초안), Plan 44 (alarm_query 의도), Plan 45 (해소 알람)
> **관련 결정**: D-022(조인 규칙), D-030(해소 이력 포함), D-031, D-032 / 구현 완료 후 **D-035**로 기재 예정
> **상태**: 구현 완료 (2026-06-11, D-035 기재)

---

## 1. 목표

현재 알람 분석(`alarm_analyzer`)은 **단일 알람 이벤트만** 보고 요약·추정 원인·권고 조치를 생성한다.
이를 고도화하여, **폴스타 DB의 알람 이력을 조회하고 패턴을 분석**하는 항목을 추가한다.

| 구분 | 현재 | 고도화 후 |
|------|------|----------|
| 분석 입력 | 알람 이벤트 1건 (conditions, conditionLog 등) | 알람 이벤트 + **동일 알람의 최근 이력 통계 (폴스타 DB)** |
| 분석 출력 | severity_label, summary, probable_cause, recommended_action | + **pattern_type, is_routine, pattern_analysis** |
| 운영자 가치 | "무슨 알람인가" | + "**늘 발생하는 알람인가, 지금 확인해야 하는 알람인가**" |

**예시 시나리오**

- CPU 알람이 매일 새벽 2시대에 배치 작업으로 반복 발생 → "주기적 알람, 기존 패턴과 일치" → 운영자 부담 경감
- 같은 CPU 알람이 낮 14시에 발생 → "평소 새벽 시간대 패턴과 다름, 확인 필요"
- 동일 알람이 24시간 내 급증 → "급증 패턴, 즉시 확인 필요"
- 이력이 전혀 없는 알람 → "첫 발생, 신규 이슈 가능성"

> Plan 46 §12.1(일상 알람 판별)의 초안을 본 계획으로 **구체화·대체**한다.

---

## 2. 현재 구조 요약 (구현 기준)

```
[폴스타] ─TCP→ alarm_server/ ─XADD→ Redis Stream "alarm:raw"
                                        │ XREADGROUP
                                        ▼
                          AlarmWorker._process()
                          parse → dedup(in-memory TTL) → min_severity 필터
                                        │ graph.ainvoke()
                                        ▼
                          alarm_analyzer ──→ alarm_notifier
                          (LLM 단건 분석)    (workb / webhook / UI push)
```

분석에 쓸 수 있는 "최근 이력"은 폴스타 DB(`CMM_ALARM`)에 이미 존재하며,
기존 DBHub(MCP) 경로(`DBRegistry.get_client(db_id)` → `execute_sql`)로 조회 가능하다.
알람 이벤트의 `db_id`는 D-032에 따라 DB 레지스트리의 `db_id`와 1:1 대응한다.

---

## 3. 설계 핵심 결정

### 3.1 이력 소스: 폴스타 DB 직접 조회 (자체 저장소 신설하지 않음)

| 기준 | 폴스타 DB 조회 (채택) | 자체 Redis 적재 (기각 — 초안) |
|------|---------------------|------------------------------|
| 데이터 범위 | **폴스타 보존 전체 기간** — 배포 즉시 패턴 분석 가능 | 기능 배포 시점 이후 수신분만 (도입 초기 공백) | 
| 데이터 정합성 | 원천 데이터 그대로 (단일 진실 원천) | 수신 누락·워커 다운 시 이력 결손 |
| 신규 컴포넌트 | 없음 (기존 DBHub 인프라 재사용) | 이력 저장소 + 적재 로직 + 보존 정책 신설 |
| 지연 | DBHub 경유 수백 ms~수 초 | 수 ms |
| 외부 의존 | 폴스타 DB 가용성에 의존 | 없음 |

**결정**: 폴스타 DB 조회 단일 방식으로 구현한다. 별도 알람 이력 저장소는 만들지 않는다.

**지연·의존 리스크는 다음 3중 보호 장치로 완화한다** (§5.4 상세):

1. **고정 SQL** — LLM 생성이 아닌 사전 정의 SELECT 1회. 검증 파이프라인 불필요, 읽기 전용.
2. **타임아웃 + graceful degradation** — 조회 전체에 `enrich_timeout_seconds`(기본 5초) 적용. 실패/타임아웃 시 이력 없이 기존 분석을 그대로 진행 (알람 발송 절대 차단 금지).
3. **단기 조회 캐시 (Redis, TTL 기반)** — 동일 (db_id, 서버, 알람명) 키의 조회 결과를 짧은 TTL(기본 5분)로 캐시. 알람 폭주(급증) 시 같은 쿼리가 폴스타 DB를 반복 타격하지 않도록 보호. *이력 저장소가 아니라 부하 보호 장치*이며, 캐시 미스 시 항상 DB가 원천이다.

### 3.2 조회 기간(lookback): 기본 90일(3개월)

운영 환경의 반복 알람은 대부분 **일/주/월 주기** 작업(백업, 주간 배치, 월말 결산 등)에서 발생한다.
주기 판정 규칙(§5.2: 발생 ≥ 3건 + 간격 일정)이 성립하려면 해당 주기를 **3회 이상 관측**해야 하므로,
가장 긴 월 주기까지 잡으려면 최소 3개월의 이력이 필요하다.

| 주기 유형 | 예시 | 3회 관측 필요 기간 | 7일 | 30일 | **90일** | 180일 |
|----------|------|------------------|-----|------|---------|-------|
| 일 주기 | 새벽 백업 CPU 알람 | 약 3일 | 가능 | 가능 | 가능 | 가능 |
| 주 주기 | 주말 풀백업, 주간 배치 | 약 3주 | 불가 | 4회 관측 | 가능 | 가능 |
| 월 주기 | 월말 결산, 월초 정산 배치 | 약 3개월 | 불가 | 불가 | **3회 관측** | 6회 관측 |

**기본값 90일 채택, 180일은 설정으로 확장** (`ALARM_HISTORY_LOOKBACK_DAYS`):

- 90일이면 일·주·월 주기가 모두 3회 이상 관측되어 주기 판정이 성립한다.
- 6개월(180일)은 월 주기 관측이 6회로 늘어 신뢰도는 높아지지만, ① 조회 범위·전송 행수가 2배,
  ② 서버 용도 변경·배치 일정 변경 등으로 더 이상 유효하지 않은 과거 패턴이 현재 판단을 희석할 수 있어
  기본값으로는 과하다. 월 주기 작업이 많은 환경에서만 설정으로 늘린다.
- 분기 주기는 3회 관측에 9개월 이상이 필요해 판정 범위 외로 한다 (프롬프트에도 전제 명시).

**장기 조회의 파생 영향과 대응** (각 절에 반영):

| 영향 | 대응 |
|------|------|
| 빈발 알람의 행수 증가 (예: 일 10건 × 90일 = 900건) | `history_max_rows` 기본 2,000으로 설정 (§5.8). 상한 도달 시 `truncated=True`로 통계에 표기하고 LLM에 "이력 일부만 반영됨" 명시 (§5.2) — 프로젝트 쿼리 상한 10,000행 이내 |
| 통계의 최신성 | **다중 윈도우 집계** — 발생 횟수를 24h / 7일 / 30일 / 전체로 구분 산출. 시간대 히스토그램은 최근 30일 한정(오래된 시간대 패턴 희석 방지), 간격 분석은 전체 기간 대상(주·월 주기 감지용) (§5.2) |
| DB 부하 | CTIME 범위 조건 + FETCH FIRST 상한 + 단기 캐시(TTL 5분). `CMM_ALARM.CTIME` 인덱스 존재 여부를 구현 시 확인 (§9) |
| 캐시 항목 크기 | 행당 약 50B × 2,000행 ≈ 100KB/키 — Redis 부담 미미, TTL 5분 유지 |

### 3.3 패턴 판정: 통계는 Python이 계산, 해석만 LLM이 수행

발생 횟수·시간대 분포·발생 간격 같은 수치 계산을 LLM에 맡기면 환각·계산 오류 위험이 있다.
**조회 결과 → 통계 계산 → 1차 분류(첫 발생/주기적/급증/산발적)는 순수 Python 함수로 결정적으로 수행**하고,
LLM에는 계산된 통계 요약 텍스트를 주입하여 **해석과 권고만** 맡긴다.

### 3.4 이력 식별 단위

동일 알람의 "반복"은 다음 3요소로 식별한다:

- `db_id` — 조회 대상 폴스타 인스턴스 선택 (`DBRegistry.get_client(db_id)`)
- 서버 — 알람 이벤트의 `hostname`/`server_name`으로 `CMM_RESOURCE` 측 서버 식별 (프로필별 매칭 컬럼 상이, §5.3 주의)
- 알람 정의 — `alarm_name` = `CMM_ALARM_DEF.NAME`

`alarm_id`는 발생 건마다 새로 발급되므로 식별 키가 아니라 **현재 이벤트를 이력에서 제외**하는 용도로만 사용한다
(폴스타가 TCP 액션 발송 전에 DB에 먼저 기록할 수 있어, 조회 결과에 현재 알람 자신이 포함될 수 있음).

---

## 4. 변경 후 아키텍처

### 4.1 그래프: 2-노드 → 3-노드

```
START → alarm_context_enricher → alarm_analyzer → alarm_notifier → END
```

- `alarm_context_enricher`: 캐시 확인 → 폴스타 DB 이력 조회 → 통계 계산 + 1차 분류. **실패해도 분석은 계속 진행** (`history_stats=None`).
- `alarm_analyzer`: 통계 요약이 있으면 프롬프트에 주입, 응답 스키마에 패턴 필드 추가.
- `alarm_notifier`: workb 본문 / webhook payload / UI push에 패턴 분석 결과 추가.

`AlarmWorker._process()`의 처리 순서는 **변경 없음** (적재 단계가 없으므로).
min_severity 미만 알람은 기존대로 분석 자체를 건너뛴다 — 이력은 폴스타 DB에 있으므로 누락 우려 없음.

### 4.2 데이터 흐름

```
alarm_context_enricher
  ├─ 1. Redis GET alarm:histcache:{db_id}:{server_name}:{alarm_name}
  │      └─ HIT → 캐시된 이력 행으로 통계 계산 (DB 미접근)
  ├─ 2. MISS → DBRegistry.get_client(event.db_id) → execute_sql(고정 SELECT)
  │      └─ 결과를 Redis SETEX (TTL = history_cache_ttl_seconds)
  └─ 3. compute_history_stats(이력, 현재 이벤트) → AlarmState.history_stats
```

---

## 5. 컴포넌트 상세 설계

### 5.1 `src/alarm/domain/alarm.py` — 도메인 모델 확장

```python
@dataclass
class AlarmHistoryEntry:
    """폴스타 DB에서 조회된 과거 알람 1건."""
    alarm_id: str
    severity: int              # 발생/해소 구분 기준 (0=해소)
    alarm_status: str          # CURRENTALARMSTATUS 매핑 — ACK 상태(참고용), 통계 판정에 사용하지 않음
    resource_name: str
    alarm_time: datetime       # CTIME


@dataclass
class AlarmHistoryStats:
    """alarm_context_enricher가 계산한 이력 통계 (LLM 프롬프트 주입용).

    발생 횟수는 다중 윈도우(24h/7일/30일/전체)로 산출한다 (§3.2).
    """
    total_count: int                      # lookback(기본 90일) 전체 발생 건수 (해소 제외, 현재 이벤트 제외)
    count_24h: int
    count_7d: int
    count_30d: int
    same_resource_count: int              # 동일 resource_name 발생 건수 (전체 기간)
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]         # 직전 발생 시각 (현재 이벤트 제외)
    hour_histogram: dict[int, int]        # 시간대(0~23)별 발생 분포 — 최근 30일 한정
    median_interval_minutes: Optional[float]   # 발생 간격 중앙값 — 전체 기간 (주·월 주기 감지용)
    interval_cv: Optional[float]          # 간격 변동계수 (주기성 지표)
    period_label: str                     # "일 주기"|"주 주기"|"월 주기"|"기타 주기"|"" (주기적 판정 시 부여)
    truncated: bool                       # max_rows 도달로 이력 일부만 반영됨
    pre_classification: str               # "첫 발생"|"주기적"|"급증"|"산발적"
    source: str                           # "polestar_db" | "cache"


@dataclass
class AlarmAnalysisResult:
    ...  # 기존 필드 유지
    # --- Plan 47 추가 ---
    pattern_type: str = ""                # "첫 발생"|"주기적"|"급증"|"산발적"|"" (이력 분석 불가 시 빈 값)
    is_routine: Optional[bool] = None     # True=일상적 반복 알람, None=판단 불가
    pattern_analysis: str = ""            # LLM 패턴 해석 (1~3문장)
```

### 5.2 `src/alarm/domain/alarm_pattern.py` — 통계 계산 (신규, 순수 함수)

이력 리스트 → `AlarmHistoryStats` 변환. DB/Redis/LLM 의존 없는 순수 로직이므로 **domain 계층**에 둔다
(Known Mistakes 2026-03-23: 데이터 모델 변환 함수는 모델이 있는 계층에 배치).

```python
def compute_history_stats(
    event: AlarmEvent,
    entries: list[AlarmHistoryEntry],
    burst_threshold_24h: int,
    lookback_days: int,
) -> AlarmHistoryStats:
    """발생(severity>=1) 이벤트 기준 통계 계산 + 1차 분류.

    - entries에서 event.alarm_id와 동일한 행은 제외한다 (현재 알람 자신).
    - 모든 시간 윈도우(24h/7일/30일/lookback)는 **event.alarm_time을 기준 시각**으로
      계산한다 — 서버 처리 시각(now) 기준이 아님. 수신 지연·재처리로 알람 발생 시점과
      처리 시점이 벌어져도 발생 시점 기준의 패턴 판단을 유지하기 위함.
      (이력 조회 SQL의 :lookback_start도 동일하게 alarm_time - lookback_days)
    """
```

**1차 분류 규칙** (위에서부터 먼저 매칭되는 항목 적용):

| pre_classification | 조건 |
|--------------------|------|
| `첫 발생` | lookback(기본 90일) 내 발생 이력 0건 (현재 이벤트 제외) |
| `급증` | count_24h ≥ burst_threshold_24h **이고** count_24h가 **최근 30일 일평균**의 3배 이상 (90일 평균 대비가 아닌 30일 기준 — 최근에 시작된 반복 알람을 과거 무발생 기간이 희석하지 않도록) |
| `주기적` | 발생 ≥ 3건 **이고** 간격 변동계수(CV) < 0.5 — 간격이 일정함. `period_label` 부여 |
| `산발적` | 그 외 |

**주기 라벨(`period_label`) 산정** — 주기적 판정 시 간격 중앙값 기준:

| median interval | period_label |
|-----------------|--------------|
| 20~28시간 | `일 주기` |
| 5.5~8.5일 | `주 주기` |
| 26~35일 | `월 주기` |
| 그 외 | `기타 주기` (중앙값을 그대로 표기) |

- 시간대 패턴 일치 여부(예: "평소 02시대 vs 이번 14시")는 규칙으로 단정하지 않고, `hour_histogram`과 현재 발생 시각을 LLM에 전달하여 해석을 맡긴다.
- 시간대 히스토그램은 **최근 30일 한정**, 간격 분석은 **전체 기간** 대상 (§3.2 다중 윈도우 원칙).
- 조회가 `max_rows`로 잘린 경우(`truncated=True`) 최초 발생·전체 건수가 부정확할 수 있으므로 통계 렌더링에 "이력 일부만 반영됨"을 명시한다 — `첫 발생` 판정은 truncated 시 불가능(잘릴 만큼 이력이 많음)하므로 영향 없음.

### 5.3 `src/alarm/infrastructure/polestar_history.py` — 이력 조회 리포지토리 (신규)

```python
class PolestarAlarmHistoryRepository:
    """폴스타 DB에서 동일 (서버, 알람명) 이력을 고정 SQL로 조회한다."""

    def __init__(self, registry: DBRegistry, alarm_cfg) -> None: ...

    async def fetch(self, event: AlarmEvent) -> list[AlarmHistoryEntry]:
        """DBRegistry.get_client(event.db_id)로 SELECT 1회 실행 후 변환.

        - event.db_id가 레지스트리 미등록이면 빈 리스트 반환 (warning 로그)
        - 행 수 상한 FETCH FIRST {history_max_rows} ROWS ONLY
        """
```

**조회 SQL (기준 골격 — 구현 시 폴스타 프로필 query_guide Template C-2/C-3과 대조 확정)**:

```sql
SELECT CA.ID, CA.CTIME, CA.ALARMSEVERITY, CA.CURRENTALARMSTATUS, CR.NAME AS RESOURCE_NAME
FROM CMM_ALARM CA
JOIN CMM_ALARM_DEF D ON CA.DEFINITION_ID = D.ID
JOIN CMM_RESOURCE CR ON CA.RESOURCE_ID = CR.ID
WHERE D.NAME = :alarm_name
  AND (서버 매칭 조건 — 아래 주의 참고)
  AND CA.CTIME >= :lookback_start
ORDER BY CA.CTIME DESC
FETCH FIRST :max_rows ROWS ONLY
```

**서버 매칭 조건 주의 (프로필별 상이)**:

- D-022 hostname 브릿지 조인 규칙을 준수한다 (`RESOURCE_CONF_ID` JOIN 절대 금지).
- **공동존 폴스타(김포 `polestar_cm_gp` / 여의도 `polestar_cm_yd`)는 장비 식별에 `r.name`을 사용**한다
  (Known Mistakes 2026-06-10: hostname 매핑 오류 사례). 알람 이벤트의 `server_name`(=`${platformName}`)이
  폴스타 등록 서버명이므로 이 값과 매칭하는 것을 기본으로 하되, 프로필별 query_guide로 매칭 컬럼을 확정한다.
- 구현 시 db_id별 서버 매칭 식을 분기할 수 있도록 SQL 조립부를 분리해 둔다.

**파라미터 바인딩**: `alarm_name` 등 문자열은 SQL에 직접 보간하지 말고, DBHub `execute_sql`이 바인딩을 지원하지
않으면 작은따옴표 이스케이프 등 안전한 리터럴 처리 유틸을 거친다 (값이 폴스타 알람 정의명이라 위험도는 낮으나 규칙으로 명시).

**조회 캐시** (동일 모듈 또는 enricher에서 처리):

```
키:   alarm:histcache:{db_id}:{server_name}:{alarm_name}
값:   AlarmHistoryEntry 리스트 직렬화 JSON (ensure_ascii=False)
TTL:  history_cache_ttl_seconds (기본 300)
```

- 캐시는 **조회 행 원본**을 저장한다 (통계가 아니라). 통계는 현재 시각·현재 이벤트에 상대적이므로 매번 재계산하되, 계산 비용은 무시 가능한 수준.
- Redis는 AlarmWorker가 이미 보유한 클라이언트를 주입받아 재사용한다.
- Redis 캐시 실패는 무시하고 DB 조회로 진행 (캐시는 순수 최적화).

### 5.4 `src/alarm/application/nodes/alarm_context_enricher.py` — 신규 노드

```python
async def alarm_context_enricher_node(state, config) -> dict:
    """폴스타 DB 이력 조회 → 통계 계산 → history_stats 반환.

    - cfg.alarm.history_enabled=False 또는 조회/계산 실패 시 {"history_stats": None} 반환
      (분석 파이프라인을 절대 차단하지 않는다)
    - 캐시 확인 → DB 조회 → 캐시 적재 → 통계 계산 전체에
      asyncio.wait_for(enrich_timeout_seconds) 적용
    - 해소 알람(is_clear=True)은 이력 조회를 건너뛴다 (패턴 분석은 발생 알람 대상)
    """
```

`AlarmState`(`src/alarm/orchestration/alarm_graph.py`) 확장:

```python
class AlarmState(TypedDict):
    alarm_event: AlarmEvent
    history_stats: Optional[AlarmHistoryStats]   # 신규
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]
```

> `PolestarAlarmHistoryRepository`(및 Redis 캐시 클라이언트)는 `config["configurable"]`로 주입한다.
> AlarmWorker가 생성하여 전달하며, 테스트 API 경로에서는 미주입 시 이력 조회를 건너뛴다.

### 5.5 프롬프트 확장 (`src/alarm/prompts/alarm_analyzer.py`)

**시스템 프롬프트** — 응답 스키마에 3개 필드 추가:

```
{
    "severity_label": "심각" | "경고" | "주의" | "해소",
    "summary": "...",
    "probable_cause": "...",
    "recommended_action": "...",
    "pattern_type": "첫 발생" | "주기적" | "급증" | "산발적",
    "is_routine": true | false,
    "pattern_analysis": "이력 통계 근거 패턴 해석 (1~3문장, 한국어)"
}

규칙 추가:
- [알람 이력 통계] 섹션이 주어지면 이를 근거로 pattern_type / is_routine / pattern_analysis를 판단할 것
- 사전 분류(pre_classification)는 참고용 — 시간대 분포와 현재 발생 시각이 평소 패턴과 다르면
  is_routine=false로 판단하고 그 이유를 pattern_analysis에 명시할 것
- is_routine=true는 "주기적 패턴과 일치하고 확인 우선순위가 낮음"을 의미. 단, 심각도 3(심각)은
  주기적이어도 recommended_action에 확인 절차를 유지할 것
- 이력 통계 섹션이 없으면 pattern_type="첫 발생" 단정 금지 — "이력 정보 없음"으로 간주하고
  pattern_analysis에 "이력 데이터 조회 불가로 패턴 판단 불가"를 기재, is_routine=false
- 이력 통계는 최근 90일 기준 — 분기(3개월) 이상의 장주기 패턴은 판단 대상이 아니며 추측하지 말 것
- "(이력 일부만 반영)" 표기가 있으면 최초 발생·전체 건수 해석에 그 한계를 반영할 것
- 통계 수치를 새로 계산하지 말 것 — 제공된 수치만 인용할 것
```

**유저 템플릿** — 기존 알람 정보 아래에 이력 섹션 추가 (`history_section` 단일 포맷 변수):

```
{history_section}
```

`alarm_analyzer` 노드에서 `AlarmHistoryStats` → 텍스트 렌더링 (`_render_history_section()`):

```
[알람 이력 통계 — 최근 90일, 동일 서버·동일 알람 (폴스타 DB 조회)]
- 발생 횟수: 88건 (24시간: 1건 / 7일: 7건 / 30일: 30건)
- 동일 자원(svr-001-CPU): 88건
- 최초/직전 발생: 2026-03-14 02:05 / 2026-06-10 02:11 (33시간 전)
- 시간대 분포(최근 30일): 02시 27건, 03시 2건, 14시 1건
- 발생 간격: 중앙값 24.1시간, 변동계수 0.12 (간격 일정 — 일 주기)
- 사전 분류: 주기적 (일 주기)
- 이번 발생 시각: 2026-06-11 14:35  ← 시간대 비교용
```

`truncated=True`인 경우 첫 줄에 `(이력 일부만 반영 — 최근 2,000건 한정)`을 덧붙인다.

`history_stats=None`이면 `history_section=""`(빈 문자열)로 주입한다.

### 5.6 `alarm_analyzer` 노드 변경

- `state.get("history_stats")` 렌더링 후 템플릿 주입.
- `_extract_json` 파싱 결과에서 신규 필드는 **`parsed.get(...)` 기본값 처리** — LLM이 누락해도 기존 분석 결과 생성에 실패하지 않도록 한다 (`pattern_type=""`, `is_routine=None`, `pattern_analysis=""`).

### 5.7 출력 채널 확장

| 위치 | 변경 |
|------|------|
| `alarm_notifier.build_workb_body()` | `<b>패턴 분석</b>` 섹션 추가: `[주기적 · 일상 알람]` 또는 `[급증 · 확인 필요]` 배지 + pattern_analysis 본문. `pattern_type=""`이면 섹션 생략 |
| `alarm_notifier._send_webhook()` payload | `pattern_type`, `is_routine`, `pattern_analysis` 키 추가 |
| `src/api/routes/alarm.py` UI push (`alarm_bus.publish`, 2곳) | 동일 3개 필드 추가 |
| `src/api/routes/alarm.py` `AlarmAnalysisOutput` | 동일 3개 필드 추가 (Optional) |
| `src/static/js/app.js` 알람 말풍선 | 패턴 배지 표시 — is_routine=true는 회색(일상), false는 강조색(확인 필요) |

### 5.8 설정 (`src/config.py` AlarmConfig 추가 필드)

```python
# ── Plan 47: 폴스타 DB 이력 기반 패턴 분석 ──
history_enabled: bool = True              # 이력 조회 + 패턴 분석 활성화
history_lookback_days: int = 90           # 패턴 분석 조회 기간 — 일·주·월 주기 3회 관측 가능한 최소 기간 (§3.2)
                                          # 월 주기 작업이 많은 환경은 180까지 확장 가능
history_max_rows: int = 2000              # 조회 행 수 상한 (일 10건 빈발 알람 × 90일 = 900건 수용, truncated 플래그 연동)
history_cache_ttl_seconds: int = 300      # 조회 결과 단기 캐시 TTL (0이면 캐시 비활성)
enrich_timeout_seconds: int = 5           # enricher 전체 타임아웃
burst_threshold_24h: int = 5              # 급증 판정 24h 최소 건수
```

`.env.example` 추가 (모두 스칼라 — list 필드 아님, JSON 배열 불필요):

```ini
# ── 알람 이력 패턴 분석 (Plan 47) ─────────
ALARM_HISTORY_ENABLED=true
# 조회 기간(일): 90=일·주·월 주기 판정 가능(기본), 월 주기 작업이 많으면 180
ALARM_HISTORY_LOOKBACK_DAYS=90
ALARM_HISTORY_MAX_ROWS=2000
ALARM_HISTORY_CACHE_TTL_SECONDS=300
ALARM_ENRICH_TIMEOUT_SECONDS=5
ALARM_BURST_THRESHOLD_24H=5
```

### 5.9 테스트 API (`/alarm/analyze-test`, `/analyze-test/raw`) 확장

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `query_history: bool` | `True` | 폴스타 DB 이력 조회 수행 여부. 테스트 엔드포인트는 기본 True로 패턴 근거표를 바로 확인. simulated_history 지정 시 그쪽 우선, false로 주면 생략 (2026-06-16 기본값 False→True 변경: 테스트 시 매번 깜빡하는 문제 해소) |
| `simulated_history: Optional[list]` | `None` | `[{"alarm_time":"yyyyMMddHHmmss","severity":3,"alarm_status":"발생","resource_name":"..."}]` 형식. 지정 시 DB 조회 대신 이 목록으로 통계 계산 — 이력 시나리오(주기/급증/첫 발생)를 임의 구성하여 LLM 응답 검증 가능 |

테스트 경로는 그래프 대신 노드를 직접 호출하므로, `simulated_history` → `AlarmHistoryEntry` 변환 → `compute_history_stats()` → `state["history_stats"]` 주입 후 `alarm_analyzer_node` 호출 순으로 구성한다.
응답(`AlarmTestResponse`)에 `history_stats` 요약(통계 + 사전 분류 + source)을 포함하여 디버깅 가능하게 한다.

---

## 6. 구현 단계

### Phase 1: 이력 조회 + 통계 계산

- [x] `src/alarm/domain/alarm.py`: `AlarmHistoryEntry`, `AlarmHistoryStats` 추가, `AlarmAnalysisResult`에 패턴 필드 3개 추가
- [x] `src/alarm/domain/alarm_pattern.py`: `compute_history_stats()` + 1차 분류 규칙 구현 (현재 alarm_id 제외 처리 포함)
- [x] 폴스타 프로필 query_guide(Template C-2/C-3)와 대조하여 이력 조회 SQL 확정 — **db_id별 서버 매칭 컬럼 확인** (공동존 gp/yd는 `r.name`, Known Mistakes 2026-06-10 참고)
      → 서버 매칭은 Template C-6 패턴(`SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)` + `SVR.NAME = server_name`)으로 확정 — 하위 자원(server.Cpus 등) 알람 포함. CTIME은 timestamp 타입(TO_TIMESTAMP 비교). db_id별 분기는 `_SERVER_MATCH_BY_DB_ID`로 분리
- [x] `src/alarm/infrastructure/polestar_history.py`: `PolestarAlarmHistoryRepository` 구현 (고정 SQL, 행 수 상한, 미등록 db_id 처리, 문자열 리터럴 안전 처리)
- [x] `src/config.py`: `AlarmConfig`에 Plan 47 필드 6개 추가, `.env.example` 갱신
- [x] 단위 테스트: 분류 규칙 4종(첫 발생/주기적/급증/산발적) 각각 고정 이력으로 검증, 주기 라벨 3종(일/주/월) 산정 검증, truncated 플래그 검증, 현재 이벤트 제외 검증 (`tests/test_alarm_pattern.py`, `tests/test_alarm_history_repo.py`)

### Phase 2: 그래프 통합

- [x] `src/alarm/application/nodes/alarm_context_enricher.py`: enricher 노드 구현 (캐시 → DB 조회 → 통계, 타임아웃, graceful degradation, 해소 알람 스킵)
- [x] Redis 단기 캐시 구현 (`history_cache_ttl_seconds`, 캐시 실패 무시)
- [x] `src/alarm/orchestration/alarm_graph.py`: `AlarmState.history_stats` 추가, 3-노드 그래프로 변경 (history_enabled=false 시 기존 2-노드 유지)
- [x] `src/alarm/application/alarm_worker.py`: `DBRegistry`/Redis 클라이언트 기반 리포지토리 생성 후 graph config로 주입
- [x] `src/alarm/prompts/alarm_analyzer.py`: 응답 스키마·규칙·`{history_section}` 확장
- [x] `src/alarm/application/nodes/alarm_analyzer.py`: `_render_history_section()` + 신규 필드 `parsed.get()` 파싱
- [x] (기존 코드 정정) is_clear 판정을 `severity == 0` 단독 기준으로 정리 — `alarm_worker.py`·`src/api/routes/alarm.py`의 `alarm_status == "해소"` 조건 제거, `AlarmEvent` 주석 갱신, 프롬프트의 "해소 알람(alarmStatus=해소)" 규칙을 "severity=0" 기준으로 수정 (alarmStatus는 폴스타 UI 인지(ACK) 상태로 해소 여부와 무관 — §9)
- [x] 단위 테스트: enricher 타임아웃/DB 실패 시 `history_stats=None`으로 분석 계속 진행 검증 (`tests/test_alarm_enricher.py`)

### Phase 3: 출력 채널 + 테스트 API

- [x] `alarm_notifier.py`: workb 본문 패턴 섹션, webhook payload 필드 추가
- [x] `src/api/routes/alarm.py`: UI push 2곳 + `AlarmAnalysisOutput` + `query_history`/`simulated_history` 파라미터 + 응답 `history_stats` 요약
- [x] `src/static/js/app.js`: 알람 말풍선 패턴 배지
- [x] 통합 테스트: `simulated_history`로 주기적/급증 시나리오 LLM 응답 확인, `query_history=true`로 실 DB end-to-end 확인
      → simulated 변환·통계·프롬프트 주입·LLM 응답 파싱은 단위 테스트로 검증 완료(`tests/test_alarm_enricher.py`). 실 LLM·실 폴스타 DB end-to-end는 운영 환경에서 `/alarm/analyze-test` (`simulated_history`/`query_history=true`)로 수동 확인 필요

---

## 7. 검증 체크리스트

- [ ] `ALARM_HISTORY_ENABLED=false` 시 기존 2-노드 동작과 완전 동일 (회귀 없음, `history_section` 미주입)
- [ ] 폴스타 DB 미가용/타임아웃 상태에서 알람 분석·발송이 차단되지 않음 (이력 없이 진행)
- [ ] `event.db_id`가 레지스트리 미등록인 경우 이력 조회 건너뛰고 분석 계속
- [ ] 조회 결과에서 현재 알람 자신(alarm_id 동일)이 통계에서 제외됨
- [ ] 주기적 이력(simulated)에서 `pattern_type="주기적"`, 이력 0건에서 `"첫 발생"`, 24h 다건에서 `"급증"` 도출 확인
- [ ] 주 주기(매주 일요일)·월 주기(매월 말) simulated 이력에서 90일 lookback으로 `주기적` + 올바른 `period_label` 도출 확인
- [ ] `history_max_rows` 초과 이력에서 `truncated=True`가 통계 렌더링에 표기됨
- [ ] 동일 알람 단시간 반복 수신 시 캐시 적중으로 폴스타 DB 조회가 TTL당 1회로 제한됨
- [ ] Redis 캐시 미가용 시에도 DB 조회로 정상 진행
- [ ] 공동존 폴스타(gp/yd)에서 서버 매칭이 올바른 컬럼으로 수행됨 (`r.name` — hostname 오매핑 재발 방지)
- [ ] 해소 알람(is_clear=True)은 이력 조회를 건너뜀
- [ ] `severity=0` 수신 시 alarmStatus 값과 무관하게 is_clear=True 판정 (alarmStatus는 ACK 상태로 무시)
- [ ] workb 쪽지·webhook·UI 말풍선에 패턴 분석 표시 확인
- [ ] LLM이 패턴 필드를 누락 응답해도 기존 분석 결과는 정상 생성
- [ ] enricher 처리 시간이 `enrich_timeout_seconds` 내로 제한됨 (알람 응답 지연 < 5초 추가)
- [ ] 이력 조회 SQL이 읽기 전용 SELECT 단일문이며 `RESOURCE_CONF_ID` JOIN 미포함 (D-022)
- [ ] `python scripts/arch_check.py` 계층 위반 없음 (polestar_history=infrastructure, alarm_pattern=domain, enricher=application)

---

## 8. 의사결정 기록 (구현 완료 후 `docs/02_decision.md` D-035 기재)

| 항목 | 내용 |
|------|------|
| **결정** | 알람 패턴 분석의 이력 소스를 **폴스타 DB 직접 조회**(고정 SQL, DBHub 경유, **기본 lookback 90일**)로 구현. 통계 계산·1차 분류는 Python 결정적 수행, LLM은 해석만 담당. 그래프를 3-노드(`alarm_context_enricher` 추가)로 확장. 알람 폭주 대비 조회 결과 단기 Redis 캐시(TTL 5분) 적용 |
| **근거** | 폴스타 DB에 전체 알람 이력이 이미 존재(단일 진실 원천) — 별도 저장소 신설은 중복 저장·도입 초기 이력 공백·정합성 관리 부담만 추가. DB 의존 리스크는 타임아웃 + graceful degradation + 단기 캐시로 완화. lookback 90일은 일·주·월 주기를 각 3회 이상 관측할 수 있는 최소 기간(월 주기 3회 = 약 3개월), 180일은 과거 패턴 희석·조회량 2배로 기본값에서 제외하고 설정 확장으로 제공 |
| **대안** | ① 자체 Redis 이력 적재 (Plan 47 초안) — 배포 시점 이후 데이터만 보유, 중복 저장, 사용자 결정으로 기각 ② LLM에 원시 이력 직접 주입 — 토큰 비용·계산 환각으로 기각 |

---

## 9. 주의 사항 / 엣지 케이스

| 항목 | 내용 |
|------|------|
| **알람 폭주 시 DB 부하** | 급증 상황에서 알람마다 DB 조회가 발생할 수 있음 — 단기 캐시(TTL 5분)로 (서버, 알람명)당 조회를 TTL당 1회로 제한. 캐시 비활성(`TTL=0`) 운용은 비권장 |
| **폴스타 DB 장애 = 패턴 분석 불가** | 이력 소스가 단일이므로 DB 장애 시 패턴 분석만 생략되고 기존 분석·발송은 유지 — 프롬프트 규칙으로 "이력 조회 불가" 명시 |
| **is_routine 오판 위험** | 일상 알람 판정이 실제 장애를 가리면 안 됨 — 심각도 3은 is_routine과 무관하게 권고 조치 유지(프롬프트 규칙), workb 발송 자체는 기존과 동일하게 수행 (패턴은 부가 정보일 뿐 발송 억제에 사용하지 않음 — 발송 억제는 별도 계획으로 분리) |
| **서버 매칭 컬럼 프로필별 상이** | 공동존(gp/yd)은 `r.name`, 그 외는 hostname 계열 — Known Mistakes 2026-06-10 재발 방지를 위해 구현 시 프로필별 query_guide 대조 필수. 실수신 데이터에서 `serverName="cop0-aisapd02"` vs `hostname="saisvd01"`처럼 두 값이 완전히 다른 사례 확인됨 — hostname으로 매칭하면 이력 0건 → 전부 "첫 발생" 오판 |
| **alarmStatus는 ACK 상태 (해소 여부 아님)** | `alarmStatus`는 발생/해소가 아니라 **폴스타 UI의 인지(ACK) 버튼 클릭 여부**(`NOT_ACK` 등)이며, 운영상 거의 사용하지 않으므로 분석에서 무시한다. **해소 판정(is_clear)은 `severity == 0` 단독 기준**이 옳다. 구현 시 worker·API의 `alarm_status == "해소"` 조건과 프롬프트의 "alarmStatus=해소" 규칙을 severity 기준으로 정리(§6 Phase 2). 통계의 발생/해소 구분도 `ALARMSEVERITY` 기준으로만 처리. 기존 D-032에 기술된 alarmStatus='발생'/'해소'는 실측과 다르므로 D-035 기재 시 정정 |
| **테이블 스키마 한정 필수 (구현 중 확인)** | 폴스타 PostgreSQL DB의 테이블은 `public`이 아니라 **`polestar` 스키마**에 존재하며 연결 search_path에 포함되지 않는다. 미한정 `FROM cmm_alarm`은 `relation "cmm_alarm" does not exist`로 실패한다 — 반드시 `polestar.cmm_alarm`처럼 스키마 한정(메트릭 템플릿 `polestar.cmm_resource`와 동일). `polestar_history.py`의 `_table()`/`_SCHEMA_BY_DB_ID`로 처리. (참고: query_generator의 알람 템플릿 C-1~C-5는 미한정 `CMM_ALARM`을 사용 — 동일 PostgreSQL 인스턴스에서 실패 가능성이 있어 별도 점검 필요) |
| **CTIME 시간대/형식** | 폴스타 DB의 CTIME 컬럼 타입(epoch ms vs DATE)과 시간대를 실제 스키마에서 확인 후 `AlarmHistoryEntry.alarm_time` 변환 확정 (Template C-2의 변환식 재사용) |
| **90일 범위 조회 성능** | `CMM_ALARM.CTIME` 인덱스 존재 여부를 구현 시 확인 — 인덱스 부재로 90일 범위 스캔이 느리면 폴스타 DB 운영 영향이 생기므로 lookback 축소(30일) 또는 인덱스 협의 후 확대. enricher 타임아웃(5초)이 1차 방어선 |
| **현재 알람의 DB 선반영** | 폴스타가 DB 기록 후 TCP 발송하는 경우 조회 결과에 현재 알람 포함 가능 — alarm_id로 제외 처리 (§5.2) |
| **테스트 mojibake** | 한글 단정문 작성 시 소스 실제 메시지와 대조 (메모리: test_dbhub_integration.py 사례) |
| **`.env` 신규 필드** | 모두 스칼라 타입 — list 필드 없음 (Known Mistakes 2026-03-23 비해당 확인) |
