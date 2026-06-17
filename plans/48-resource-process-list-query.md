# Plan 48: 특정 자원 실시간 프로세스 리스트 조회 + 현황 분석

> **작성일**: 2026-06-16
> **관련 Plan**: Plan 47-1 (알람 영향 프로세스 보강 — 폴스타 프로세스 API 클라이언트/도메인 선별·마스킹을 본 계획에서 **재사용**)
> **관련 결정**: D-036 (Plan 47-1, 프로세스 API hostname 조회·마스킹), D-004(LLM 전용 라우팅), D-013(멀티턴/HITL), D-020(LLM 범용 스키마) / **신규 D-037 예정**
> **상태**: 계획 수립 (미구현)

---

## 1. 목표 / 배경

현재 메인 에이전트(자연어→SQL 파이프라인)는 **특정 자원에 대한 실시간 프로세스 리스트를 출력하지 못한다**. 프로세스 정보는 DB(EAV/메트릭 테이블)에 저장되지 않고, 폴스타 실시간 프로세스 API에만 존재하기 때문이다.

Plan 47-1에서 **알람 발생 시점**에 한해 이 API(`/rest/server/process/listByhostname`)로 영향 프로세스를 조회하는 경로를 구현했다. 본 계획은 이 자산(API 클라이언트·선별·마스킹)을 재사용하여, **사용자가 직접 "특정 서버의 프로세스 목록/현황"을 질의할 때** API를 호출해 결과를 받아오고 **간단한 현황 분석**(상위 점유 프로세스, 점유율, 사용자/이름별 집계 등)을 함께 제공하는 기능을 메인 에이전트에 추가한다.

| 구분 | 현재 | 변경 후 (Plan 48) |
|------|------|-------------------|
| "saisvd01 프로세스 보여줘" | 처리 불가 (DB에 프로세스 없음 → 빈 결과/오답) | 프로세스 API 조회 → 결정적 표 + 현황 분석 |
| "그 서버에서 CPU 많이 먹는 프로세스" | 처리 불가 | CPU 상위 N 프로세스 + 점유율 분석 |
| 출력 | — | 결정적 프로세스 표(UI/엑셀) + LLM 현황 요약 |

**예시 질의**:
- "saisvd01 서버에서 실행 중인 프로세스 목록 보여줘"
- "cop-was01 의 메모리 많이 쓰는 프로세스 상위 10개"
- "여의도 polestar의 xxx 장비 프로세스 현황 분석해줘"

---

## 2. 재사용 자산 (Plan 47-1)

| 자산 | 위치 | 본 계획에서의 용도 |
|------|------|--------------------|
| `PolestarProcessApiClient.list_by_hostname()` | `src/alarm/infrastructure/polestar_process_api.py` | 그대로 재사용 (hostname 기준 GET, http, 인증 불필요, 타임아웃, None degradation) |
| `ProcessApiResult` (captured_at, processes) | 동 파일 | 그대로 재사용 |
| `ProcessInfo` (마스킹·정규화 1건) | `src/alarm/domain/alarm.py` | **공유 도메인으로 승격** (§3.2) |
| `mask_args()` / `select_top_processes()` | `src/alarm/domain/process_rank.py` | **공유 도메인으로 승격** 후 재사용 |
| base_url 매핑 / 타임아웃 | `AlarmConfig.get_process_api_base_url()`, `process_api_timeout_seconds` | 동일 폴스타 엔드포인트 → **공유 설정으로 승격**(§5.5) |

> 프로세스 API의 엔드포인트·조회 키(hostname)·인증(불필요)·scheme(http)·민감정보 마스킹 규칙은 모두 47-1 §2·§9와 동일하다. 본 계획은 이를 **알람 문맥이 아닌 사용자 질의 문맥**으로 확장하는 것이다.

---

## 3. 설계 핵심 결정

### 3.1 신규 라우팅 의도 `process_query` + 전용 노드 (SQL 파이프라인 우회)

프로세스 조회는 DB SQL이 아니라 외부 HTTP API 호출이므로, 기존 `schema_analyzer → query_generator → … → output_generator` SQL 파이프라인을 타지 않는다. `general_inference`와 동일한 패턴으로 **시멘틱 라우터가 `process_query` 의도로 분류 → 전용 노드로 분기**한다.

```
semantic_router ──(intent=process_query)──▶ process_query_node ──▶ (조건부) output_generator | END
                 ──(data_query)───────────▶ schema_analyzer → … (기존)
                 ──(general_inference)─────▶ general_inference
                 ──(cache_management)──────▶ cache_management
```

- `_INTENT_ROUTE_MAP`(`src/graph.py`)에 `"process_query": "process_query"` 추가.
- 노드는 분석 텍스트(`final_response`) + 결정적 프로세스 표(UI/엑셀용 구조)를 State에 채운다.
- 출력 형식이 `xlsx`/`docx`이면 `output_generator`로 진행해 문서 생성을 **재사용**, 텍스트면 END.

### 3.2 프로세스 선별·마스킹 도메인 원시 함수를 공유 계층으로 승격 (메인↔알람 결합 회피)

메인 에이전트(`src/nodes/`, `src/domain/`)가 알람 서브시스템(`src/alarm/*`)을 import 하면 두 기능이 결합된다. 대신 **알람·메인 양쪽이 의존할 공유 도메인 모듈**로 원시 함수를 옮긴다.

- 신규 `src/domain/process.py` 로 이동: `ProcessInfo`, `mask_args()`, `select_top_processes()`
- `src/alarm/domain/process_rank.py` 는 이를 **re-export/import** 하여 기존 동작 보존(`classify_alarm_kind`만 알람 도메인에 잔류 — `AlarmEvent` 의존이라 알람 전용).
- `src/alarm/domain/alarm.py` 의 `ProcessInfo` import 출처를 `src/domain/process.py`로 변경. `ProcessSnapshot`은 알람 전용이므로 알람 도메인에 잔류.
- 계층: `src/domain/process.py` 는 domain(최내곽). domain→domain import만 발생하여 `arch_check` 통과. **(저위험 이동 리팩터링 — 동작 변경 없음)**

> 대안(직접 cross-import `from src.alarm...`)은 메인 기능이 알람 서브시스템에 의존하게 되어 기각.

### 3.3 통계/선별은 결정적, LLM은 해석만 (Plan 47-1 §3.3 원칙 계승)

상위 프로세스 선별·정렬·집계·마스킹은 **순수 Python**으로 결정적 처리하고, LLM에는 마스킹된 요약 텍스트만 주입해 **현황 분석 문장**만 생성하게 한다. 표·수치는 결정적 데이터, 해석 문장만 LLM. (LLM이 수치를 재계산·환각하지 않도록 프롬프트로 고정)

### 3.4 hostname 해석 (서버명/hostname → 정규 hostname) — 본 계획의 핵심 난점

프로세스 API는 **hostname**으로만 조회되지만(47-1 §2.1, §9), 사용자는 보통 **서버명/장비명**(=`cmm_resource.name`)으로 질의한다. 47-1 §9 실데이터처럼 `serverName`과 `hostname`은 **완전히 다른 문자열**이라(`cop0-aisapd02` vs `saisvd01`), 서버명을 그대로 hostname으로 넣으면 0건이 난다. 변환은 **필수**다.

**핵심 원칙 — 서버명/hostname을 구분하려 시도하지 않는다.** 둘 다 임의 문자열이라 패턴(정규식 등)으로 판별하면 오판이 난다. 대신 `cmm_resource`에 `name`·`hostname` 컬럼이 **모두** 존재한다는 사실(프로필 확인 완료)을 이용해, **두 컬럼을 동시에 조회**하여 입력이 어느 쪽이든 정규 hostname으로 해석한다.

해석 순서(`resolve_target_host`) — **DB 해석 우선, 직접 hostname 폴백**:

1. **(1순위) DB 해석**: 활성 db_id 프로필 기준 경량 read-only 단건 조회로 `cmm_resource`에서 정규 hostname을 얻는다. 입력이 서버명이든 hostname이든 한 번에 처리된다:
   ```sql
   -- 이름·호스트명 동시 매칭. 정확한 hostname 매칭을 서버명 매칭보다 우선.
   SELECT hostname FROM cmm_resource
   WHERE name = :id OR hostname = :id
   ORDER BY CASE WHEN hostname = :id THEN 0 ELSE 1 END
   FETCH FIRST 2 ROWS ONLY   -- 모호성 감지를 위해 2건까지 (엔진별 LIMIT 문법은 query_validator 규칙 준수)
   ```
   - 기존 MCP DB 클라이언트로 수행(application→infrastructure 허용), **읽기 전용·바인딩**.
   - **정확히 1개 hostname** → 그 값으로 API 호출.
   - **서로 다른 hostname이 2건 이상**(모호) → 추측하지 말고 사용자에게 **되물음**(HITL: "어느 서버를 말씀하시는지 — A(hostname x) / B(hostname y)"). 멀티턴 미지원 컨텍스트면 후보를 안내하고 종료.
   - **0건**(cmm_resource에 없음) → 2순위로.
2. **(2순위, 폴백) 직접 hostname 가정**: DB가 미해석/조회 실패인 경우에 한해, 사용자 토큰을 그대로 hostname으로 보고 API를 시도한다(인벤토리 누락 자원·DB 일시 장애 대비). 결과가 있으면 사용.
3. **(db 라우팅 오류 폴백 — 사용자가 db를 명시하지 않은 경우에 한함)** 위가 모두 0건이고 매핑된 폴스타 db가 여럿이면, **다른 폴스타 db에서 1회 재해석**을 시도한다(서버명만 준 질의에서 라우터가 db를 잘못 고른 경우 대비). 상한: 매핑된 폴스타 db 수만큼, 각 단건 조회.
   - **단, `user_specified_db`가 세팅된 경우(예: "김포 폴스타의 ### 서버")엔 이 폴백을 건너뛴다.** 사용자가 db를 명시했으면 그 db에서 0건일 때 타 db를 뒤지지 않고 "해당 폴스타에 그 서버가 없다"고 안내하는 것이 의도에 맞다. 명시 질의는 `semantic_router`가 `domain_config.py` alias(`"김포"/"김포 폴스타"→polestar_cm_gp`, `"여의도"→polestar_cm_yd` 등)로 `active_db_id`·`user_specified`를 이미 확정하므로, ③ 폴백 자체가 정상 경로에서 발동하지 않는다(추가 조회 비용 0).
4. 모두 실패하면 "해당 자원의 hostname을 특정하지 못했다(서버명·db 확인 요청)"는 안내로 graceful 종료(추측·임의 선택 금지).

> **왜 DB 우선인가**: ①번 한 번의 조회가 서버명·hostname 두 입력을 모두 흡수하고 정규 hostname을 돌려주므로, "API 먼저 시도 후 실패하면 DB" 순서(서버명 질의마다 무용한 API 1회 낭비)보다 효율적이고 정확하다.
>
> **키 차이 주의(47-1 §9·D-036 계승)**: DB는 `name`(서버명)/`hostname` 둘 다 보유, API는 `hostname`만. 본 노드가 변환·모호성 해소를 책임진다.

---

## 4. 변경 후 아키텍처 / State

```
START → context_resolver → input_parser → field_mapper → semantic_router
                                                              │
                              ┌───────────────────────────────┤
                   (process_query)                      (data_query / 기타)
                              ▼                                ▼
                     process_query_node                  schema_analyzer → … → output_generator
                              │
                  ① resolve_target_host (token→hostname, 필요 시 cmm_resource 조회)
                  ② PolestarProcessApiClient.list_by_hostname (재사용)
                  ③ build_process_overview (결정적 선별·집계·마스킹)
                  ④ LLM 현황 분석 (마스킹 요약만 주입)
                              │
              (output_format=xlsx/docx) ─▶ output_generator ─▶ END
              (text) ────────────────────────────────────────▶ END
```

`AgentState` 확장(`src/state.py`):

```python
# Plan 48: 프로세스 조회
process_query_target: Optional[dict]      # {"identifier": str, "metric": "cpu"|"memory"|"both", "top_n": int}
process_overview: Optional[dict]          # build_process_overview 결과(결정적, 마스킹 완료) — UI/엑셀/LLM 공용
```

`routing_intent` 주석에 `"process_query"` 추가.

---

## 5. 컴포넌트 상세 설계

### 5.1 `src/domain/process.py` — 공유 프로세스 도메인 (승격 + 신규)

```python
# (47-1에서 이동) ProcessInfo, mask_args, select_top_processes  ← 동작 불변

@dataclass
class ProcessOverview:
    """사용자 프로세스 조회 결과의 결정적 현황(마스킹 완료)."""
    source_host: str                 # 실제 조회에 사용한 hostname
    captured_at: Optional[datetime]  # API 응답 date
    total_count: int                 # 전체 프로세스 수
    metric: str                      # "cpu" | "memory" | "both"
    top_by_cpu: list[ProcessInfo]    # p100cpu 내림차순 상위 N (metric in cpu/both)
    top_by_mem: list[ProcessInfo]    # pmem 내림차순 상위 N (metric in memory/both)
    cpu_top_share: float             # 상위 N의 p100cpu 합(전체 점유 직관용)
    mem_top_share: float             # 상위 N의 pmem 합
    distinct_users: int              # 고유 실행 계정 수
    top_user_by_count: Optional[tuple[str, int]]  # 가장 많은 프로세스를 띄운 계정

def build_process_overview(
    raw_list: list[dict], metric: str, top_n: int,
    *, source_host: str, captured_at: Optional[datetime],
) -> ProcessOverview:
    """원시 list → 마스킹·정규화·정렬·집계. 순수 함수(외부 의존 없음).
    - cpu/both: p100cpu(없으면 pcpu) 내림차순 상위 N → top_by_cpu
    - memory/both: pmem 내림차순 상위 N → top_by_mem
    - 각 ProcessInfo.args 는 mask_args()로 마스킹·절단 (필수)
    - 빈 list/누락 필드/0건 안전 처리
    """

def resolve_metric_from_text(text: str) -> str:
    """질의 텍스트에서 정렬 기준 추론(폴백용). 'cpu'|'메모리/mem'|그 외→'both'.
       1순위는 input_parser가 채운 process_query_target.metric."""
```

> 집계는 "과한 해석 방지"(47-1 §9)를 위해 **단순 합/카운트**만. name별 합산 등 추가 해석은 하지 않는다.

### 5.2 `src/infrastructure/polestar_host_resolver.py` — hostname 해석 (신규)

```python
@dataclass
class HostResolution:
    hostname: Optional[str]            # 단일 확정 시 정규 hostname
    candidates: list[str]              # 서로 다른 hostname 후보(모호 시 2건 이상)
    # hostname is not None  → 확정
    # candidates 2건 이상     → 모호(HITL 되물음/안내)
    # 둘 다 비어있음          → 미해석(폴백 대상)

class PolestarHostResolver:
    """서버명/hostname(token) → cmm_resource 정규 hostname 해석 (읽기 전용 조회)."""
    def __init__(self, db_client, *, query_cfg) -> None: ...

    async def resolve(self, db_id: str, identifier: str) -> HostResolution:
        """cmm_resource 에서 name OR hostname == identifier 인 행의 hostname 해석.
        - hostname 정확 매칭을 name 매칭보다 우선(ORDER BY CASE), 모호성 감지 위해 2건까지 조회
        - identifier 는 바인딩/이스케이프하여 인젝션 차단, read-only SELECT
        - 1건 → HostResolution(hostname=...) / 2건 이상(서로 다름) → candidates
        - 0건·비폴스타 db(cmm_resource 부재) → 빈 HostResolution (호출부에서 §3.4 폴백)
        """
```

- 기존 MCP DB 클라이언트(`src/dbhub` / `src/infrastructure/db_connector`)를 주입받아 재사용.
- **읽기 전용 보장**: 단일 SELECT, 프로젝트 read-only 제약 준수. `query_validator`의 안전 규칙(SELECT 한정, LIMIT) 동일 적용.
- 모호(다중 hostname)는 노드가 HITL 되물음/후보 안내로 처리(임의 선택 금지). 비폴스타/0건은 §3.4 폴백(직접 hostname → 타 폴스타 db 재해석)으로 위임.

### 5.3 `src/nodes/process_query_node.py` — 전용 노드 (신규, application 계층)

```python
async def process_query_node(state, *, llm=None, app_config=None) -> dict:
    # 0) 게이팅: app_config.process_query_enabled, base_url 매핑 존재 여부
    # 1) target = state["process_query_target"] (없으면 user_query에서 보강 추출)
    # 2) host 해석 (§3.4 순서): resolver.resolve → 확정 hostname
    #    - 모호(다중 후보) → 후보 안내/HITL 되물음으로 종료
    #    - 0건/비폴스타 → 직접 hostname 폴백 → 타 폴스타 db 재해석 폴백
    #    - 끝내 미해석 → "hostname 특정 불가" 안내 종료(추측 금지)
    # 3) result = process_client.list_by_hostname(active_db_id, host)  # 47-1 재사용
    # 4) overview = build_process_overview(result.processes, metric, top_n, ...)  # §5.1
    # 5) LLM 현황 분석 (마스킹 요약 주입) → final_response
    # 6) return {process_overview, final_response, routing_intent:"process_query", ...}
    #    output_format in (xlsx,docx) 이면 organized_data에 rows 채워 output_generator로 위임
```

- 클라이언트·리졸버는 Plan 47-1과 동일하게 `config["configurable"]`(또는 graph partial 주입)로 주입; 미주입/미설정 시 안내 메시지로 graceful 종료.
- 각 외부 호출(DB 해석, API)은 자체 try/except + 타임아웃. 실패해도 다른 단계/응답을 막지 않음(degradation, 47-1 원칙).
- **모든 출력 경로(LLM·UI·엑셀)에는 마스킹된 `args`만** 사용.

### 5.4 input_parser / semantic_router 연동

**input_parser** (`src/nodes/input_parser.py`, `src/prompts/input_parser.py`):
- parsed_requirements에 프로세스 조회 신호 추출 규칙 추가:
  ```json
  "process_query": {
    "identifier": "<서버명 또는 hostname 원문>",
    "metric": "cpu | memory | both",
    "top_n": <정수 또는 null>
  }
  ```
- "프로세스/process/실행 중인/돌고 있는/ps/프로세스 목록/현황" + 자원 식별자 패턴을 신호로 판정.

**semantic_router** (`src/routing/semantic_router.py`, `src/prompts/semantic_router.py`):
- `_llm_classify` 의도 집합에 `process_query` 추가. parsed_requirements에 `process_query`가 있으면 우선 분기(캐시/유사어 등록과 동일한 early-return 패턴, §40~205 라인 스타일).
- 분기 시 `active_db_id`(자원이 속한 폴스타 db)는 라우터가 결정한 값을 그대로 사용. 미지정 시 활성 폴스타 db 추론.

### 5.5 설정 (`src/config.py`)

공유 폴스타 프로세스 API 설정을 **AppConfig 레벨에서 접근 가능하게** 정리한다(알람·메인 공용):

```python
# ── Plan 48: 사용자 프로세스 조회 ──
process_query_enabled: bool = True
process_query_top_n: int = 10        # 사용자 조회는 알람(5)보다 많이 노출
process_query_resolve_timeout_seconds: int = 3   # hostname 해석 DB 조회 상한
# base_url 매핑/타임아웃은 폴스타 프로세스 API 공통값을 재사용
#  → AlarmConfig.process_api_base_urls_csv / get_process_api_base_url / process_api_timeout_seconds
#    (동일 엔드포인트. 향후 ProcessApiConfig로 추출 가능 — D-037 비고)
```

`.env.example` 추가(스칼라만 — list/dict JSON 필드 없음, Known Mistakes 2026-03-23 회피):

```ini
# ── 사용자 프로세스 조회 (Plan 48) ──
PROCESS_QUERY_ENABLED=true
PROCESS_QUERY_TOP_N=10
PROCESS_QUERY_RESOLVE_TIMEOUT_SECONDS=3
# base_url은 ALARM_PROCESS_API_BASE_URLS_CSV / ALARM_PROCESS_API_TIMEOUT_SECONDS 재사용
```

### 5.6 프롬프트 (`src/prompts/process_query.py` 신규)

현황 분석용 시스템 프롬프트:
- 제공된 결정적 요약(상위 프로세스·점유율·집계)만 **인용/해석**하고 수치를 재계산하지 말 것.
- 마스킹된(`***`) 인자의 내용을 추정·복원하지 말 것.
- 분석 항목: ① 자원을 가장 많이 점유하는 프로세스(이름·pid·점유율) ② 상위 N의 전체 점유 비중 ③ 특이사항(동일 이름 다수/특정 계정 집중 등) ④ 데이터가 부족하거나 0건이면 그 사실만 명시(추측 금지).
- 조회 시각(`captured_at`)을 "현재 시점 스냅샷"으로 전달.

### 5.7 출력 채널

| 위치 | 변경 |
|------|------|
| `src/static/js/app.js` | `process_overview` 수신 시 **프로세스 표** 렌더(컬럼: 프로세스/PID/CPU(100%)/MEM/사용자 + 각 행 아래 `args`(마스킹) 보조 줄). 캡션에 hostname·스냅샷 시각·전체 건수. 47-1 `.alarm-proc-table`/`.alarm-proc-args` 스타일 재사용 |
| `src/api/routes/query.py` | SSE/응답에 `process_overview`(=`_overview_to_dict`, args 마스킹 포함) 전달. `routing_intent="process_query"` 표기 |
| `src/static/js/app.js` intentMap | `process_query` 라벨 추가 |
| `output_generator` (xlsx/docx 경로) | `process_overview` rows를 organized_data로 받아 기존 문서 생성 재사용(별도 분기 최소화) |

### 5.8 그래프 배선 (`src/graph.py`)

- `process_query_node` 등록(semantic routing 활성 시), `PolestarProcessApiClient`·`PolestarHostResolver`를 partial/config로 주입.
- `_INTENT_ROUTE_MAP`에 `"process_query": "process_query"` 추가.
- `process_query` 노드 후 조건부 엣지: output_file 요청 시 `output_generator`, 아니면 `END`.

---

## 6. 구현 단계

### Phase 1: 도메인 승격 + 공유화 (동작 불변 리팩터링)
- [ ] `src/domain/process.py` 신규: `ProcessInfo`, `mask_args`, `select_top_processes` 이동 + `ProcessOverview`, `build_process_overview`, `resolve_metric_from_text` 추가
- [ ] `src/alarm/domain/process_rank.py` / `alarm.py`: import 출처를 `src/domain/process.py`로 변경(re-export). 47-1 단위 테스트 그대로 통과 확인
- [ ] 단위 테스트: `build_process_overview`(cpu/memory/both 정렬·집계), **마스킹(패스워드·토큰·접속문자열 비노출)**, 빈 list/누락 필드/0건 (`tests/test_process_overview.py`)

### Phase 2: hostname 해석 + 노드 + 설정
- [ ] `src/config.py`: `process_query_*` 필드, `.env.example` 갱신
- [ ] `src/infrastructure/polestar_host_resolver.py`: `PolestarHostResolver`(read-only 단건 SELECT, 바인딩, 비폴스타 db None)
- [ ] `src/nodes/process_query_node.py`: 게이팅 + 해석 + API 재사용 + overview + LLM 분석 + degradation
- [ ] `src/prompts/process_query.py`: 현황 분석 프롬프트(인용 규칙·환각 금지·마스킹 보존)
- [ ] 단위 테스트: 서버명→hostname 해석/직접 hostname/미해석 폴백, API 실패 시 graceful 안내, 비폴스타 db 스킵 (`tests/test_process_query_node.py`)

### Phase 3: 라우팅 + 출력 채널
- [ ] `input_parser`(+프롬프트): `process_query` 신호 추출
- [ ] `semantic_router`(+프롬프트): `process_query` 의도 분기(early-return)
- [ ] `src/graph.py`: 노드 등록·주입·`_INTENT_ROUTE_MAP`·조건부 엣지
- [ ] `query.py` + `app.js`(+`style.css`): 프로세스 표 렌더, intent 라벨, `_overview_to_dict`
- [ ] (선택) xlsx/docx: `output_generator` 위임으로 프로세스 표 문서 출력
- [ ] 통합 테스트: 모의 API 응답으로 "CPU 상위/메모리 상위/전체" 시나리오 LLM 인용·마스킹 확인. 실 API end-to-end는 운영 환경에서 수동 확인

---

## 7. 검증 체크리스트

- [ ] `PROCESS_QUERY_ENABLED=false` 시 해당 질의가 기존 동작(일반 추론/데이터 쿼리)으로 처리되고 API 미호출
- [ ] **API 조회에 hostname 사용**(서버명 아님) — 서버명 질의 시 `cmm_resource`로 hostname 해석 후 조회
- [ ] 입력이 서버명이든 hostname이든 `name OR hostname` 동시 매칭으로 정규 hostname 해석(문자열 패턴 판별에 의존하지 않음)
- [ ] 다중 hostname 매칭(모호) 시 임의 선택하지 않고 후보 안내/HITL 되물음
- [ ] DB 미해석·0건 시 직접 hostname 폴백, 라우터 db 오선택 대비 타 폴스타 db 재해석 폴백 동작
- [ ] db_id별 base_url 매핑(gp→polestar, yd→yd-polestar) 재사용 정확, 미매핑/비폴스타 db는 안내 후 종료
- [ ] CPU 질의는 p100cpu, 메모리 질의는 pmem, 미지정은 both 기준 상위 N
- [ ] **프로세스 args의 패스워드/토큰/접속문자열이 LLM·UI·엑셀 어디에도 평문 노출 안 됨**
- [ ] hostname 해석 SELECT가 read-only·단건·바인딩(인젝션 차단), 엔진별 LIMIT 문법 준수
- [ ] API/해석 타임아웃·비200·네트워크 오류 시 추측 없이 graceful 안내
- [ ] LLM이 제공된 결정적 수치만 인용(재계산·환각 없음), 0건/미해석 시 사실만 명시
- [ ] 0건·수백 개 프로세스(상위 N만 보존)·동일 이름 다수 안전 처리
- [ ] `python scripts/arch_check.py` 계층 위반 없음 (process.py=domain, host_resolver=infrastructure, process_query_node=application)
- [ ] 모든 query 실행/외부 호출 audit 로깅 정책 부합 (프로젝트 제약)

---

## 8. 의사결정 기록 (구현 후 `docs/02_decision.md` D-037 기재)

| 항목 | 내용 |
|------|------|
| **결정** | 사용자가 특정 자원의 프로세스 목록/현황을 질의하면 신규 `process_query` 라우팅 의도로 전용 노드를 분기, 폴스타 실시간 프로세스 API(47-1 클라이언트 재사용)를 **hostname으로 조회**하여 결정적 선별·집계·마스킹 후 LLM이 현황만 해석. 프로세스 선별 원시 함수(`ProcessInfo`/`mask_args`/`select_top_processes`)를 `src/domain/process.py`로 승격해 알람·메인이 공유. 서버명/hostname은 구분하지 않고 `cmm_resource`의 `name`·`hostname` 동시 매칭(read-only)으로 정규 hostname 해석, 모호 시 HITL·미해석 시 직접 hostname 폴백 |
| **근거** | 프로세스는 DB가 아닌 실시간 API에만 존재 → SQL 파이프라인으로 불가. 47-1 자산 재사용으로 중복 제거. 결정적 표+LLM 해석 분리로 환각·토큰·민감정보 위험 차단(47-1 §3.3 계승) |
| **대안** | ① SQL 파이프라인 내 특수 분기 — 외부 HTTP를 SQL 노드에 섞어 응집 저하로 기각 ② 메인이 `src/alarm` 직접 import — 서브시스템 결합으로 기각(공유 도메인 승격 채택) ③ 원시 프로세스를 LLM에 그대로 — 토큰·환각·민감정보로 기각 |
| **키 차이 주의** | API는 hostname, DB(cmm_resource)는 name/hostname 둘 다 보유 → 노드가 name→hostname 변환 수행(47-1 §9·D-036 연계) |

---

## 9. 주의 사항 / 엣지 케이스

| 항목 | 내용 |
|------|------|
| **민감정보 마스킹 (필수)** | `args`에 접속문자열·`--password`·토큰 노출 가능 — `mask_args()` 후에만 사용. 누락은 보안 사고. 단위 테스트로 회귀 고정 (47-1 §9 동일) |
| **hostname vs serverName (구분 불가)** | 문자열만으로 서버명/hostname을 신뢰성 있게 판별 불가 → 구분 시도 금지. `cmm_resource`의 `name`·`hostname`을 **동시 매칭**하여 해석. API는 hostname, 사용자는 서버명 입력 빈번 |
| **다중 hostname 매칭(모호)** | `name OR hostname` 조회가 서로 다른 hostname 2건 이상 반환 시 임의 선택은 오조회. 후보 안내/HITL 되물음으로 해소 |
| **db 라우팅 오선택** | 서버명만 준 질의에서 라우터가 잘못된 폴스타 db 선택 시 cmm_resource 0건 → 타 폴스타 db 재해석 폴백(상한: 매핑 db 수). 그래도 0건이면 안내 종료 |
| **사용자 db 명시 시** | "김포 폴스타의 ### 서버"처럼 명시하면 `semantic_router`가 `domain_config.py` alias로 `active_db_id`·`user_specified_db` 확정 → 단일 db 해석으로 충분, ③ 다중-db 폴백 미발동. `user_specified_db` 세팅 시 ③ 폴백은 의도적으로 건너뛰고 해당 db 기준으로만 안내 |
| **read-only 보장** | 해석 조회는 단일 SELECT·바인딩·LIMIT — 프로젝트 read-only 제약 및 query_validator 안전 규칙 준수. INSERT/UPDATE/DDL 생성 금지 |
| **비폴스타/미매핑 db** | base_url 매핑 없거나 cmm_resource 없는 db는 즉시 안내 종료(불필요 외부 호출 방지) |
| **CPU 정규화(pcpu vs p100cpu)** | 표시·랭킹은 p100cpu 기본, 없으면 pcpu 폴백·표기 구분 (47-1 §9) |
| **응답 시각(date) 신뢰** | 실시간 스냅샷 시각으로 표시만; 질의 시각과 수 초 차이 정상 |
| **대형/중복 프로세스** | 상위 N만 보존·전체 건수 표기. 동일 이름 다수는 pid로 개별 행(과한 합산 해석 금지) |
| **외부 호출 SSRF** | base_url은 설정 고정값(사용자 입력 아님), hostname만 인코딩 부착(47-1 클라이언트가 처리). 설정 외 URL 호출 없음 |
| **`.env` 신규 필드** | 스칼라만 — list/dict JSON 없음 (Known Mistakes 2026-03-23 비해당) |
| **47-1 리팩터링 회귀** | 도메인 승격은 import 출처 변경뿐 — 47-1 테스트 전수 통과로 무회귀 확인 후 다음 Phase 진행 |

---

## 10. 후속 개선 (2026-06-17) — 스트리밍 출력 / args 전달 / CSV 제공

사용자 피드백 3건을 반영한다. 핵심 파이프라인(§3·§5)은 불변이며, **출력 채널 배선**만 보강한다.

### 10.1 스트리밍 출력 (한 번에 → 토큰 단위)

**증상**: `process_query` 응답이 토큰 스트리밍 없이 완성본으로 한 번에 표시됨.

**원인**: `src/api/routes/query.py`의 SSE 스트리밍이 `process_query`를 화이트리스트에서 누락.
- `_known_nodes`(2곳)에 `process_query` 없음 → `node_start`/`node_complete` 미발행 → 진행 패널·프로세스 표(`renderProcessOverview`)가 스트리밍 모드에서 렌더 안 됨.
- `on_chat_model_stream` 노드 필터가 `("output_generator", "general_inference")`뿐 → 노드의 LLM 토큰이 스트리밍되지 않고, `on_chain_end` 폴백이 `final_response`를 단일 청크로 전송.

**조치**: `/query/stream`·`/query/file/stream` 두 제너레이터의
- `_known_nodes` 집합에 `"process_query"` 추가(2곳)
- `on_chat_model_stream` 노드 필터에 `"process_query"` 추가(2곳) → `("output_generator", "general_inference", "process_query")`

노드는 기존대로 `llm.ainvoke`를 쓰지만, `output_generator`와 동일하게 `astream_events`가 모델 토큰을 `on_chat_model_stream`으로 포착하므로 노드 코드 변경 없이 토큰 스트리밍된다.

### 10.2 args 전달 확인 + "데이터 부족" 오프레이밍 교정

**검증 결과(버그 아님)**: API 응답 필드는 `args`(47-1 §2.2)이고 `_to_process_info`가 `item.get("args")`를 `mask_args()`로 마스킹해 보관하며, `_build_summary_text._fmt`가 각 상위 프로세스에 `args: …`를 포함한다. 즉 **마스킹된 args는 이미 LLM에 전달**된다.

**실제 원인**: 프롬프트가 "상위 N 현황 요약"을 **불완전 데이터로 오해**하여, `total_count>0`이고 상위 목록이 있어도 "데이터 부족/추가 확인 필요"로 출력. (전체 목록이 아닌 상위 N만 보이므로 LLM이 보수적으로 판단.)

**조치**:
- `src/prompts/process_query.py`: "상위 N + 전체 건수 + 집계는 **현황 분석에 충분한 데이터**다. `total_count>0`이고 상위 목록이 있으면 '데이터 부족'이라고 말하지 말 것. '데이터 부족/추가 확인'은 0건·hostname 미해석 등 **진짜 데이터가 없을 때만**" 명시. args(실행 인자)는 서비스 식별 근거로 해석하되 `***` 복원 금지 재확인.
- `_build_summary_text`: 상위 N이 전체의 일부임을 명확히("전체 N건 중 상위 K개")하고, 전체 목록은 CSV로 제공됨을 요약 끝에 한 줄로 안내(LLM이 "전체는 CSV 참조"로 안내 가능).

### 10.3 프로세스 조회 결과 CSV 제공

**현황**: `/query/{id}/download-csv`는 저장소의 `query_results`를 CSV로 변환하고, UI CSV 버튼은 `row_count>0`일 때 노출. `process_query` 노드는 `query_results`를 채우지 않아 CSV 불가(404)·버튼 미표시.

**조치(기존 인프라 재사용, 엔드포인트/프런트 무변경)**: `process_query_node`가 성공 시 **전체 프로세스(마스킹된 args 포함)**를 기본 지표 내림차순으로 정렬해 `query_results`(평면 dict 행)로 반환.
- 결과: `download-csv`가 자동 동작(전체 목록 CSV), UI CSV 버튼 자동 노출("CSV 다운로드 (N건)"), `row_count`=전체 프로세스 수.
- 상한: 프로젝트 max rows(10,000) 준수 — 초과 시 상위 10,000행만(현실 프로세스 수는 수백 수준).
- 모든 행 `args`는 `mask_args()` 적용분만(평문 비노출 — §9 보안 제약 유지). LLM 주입 요약(top N)과 별개로, CSV는 결정적 전체 행.
- `_text_only` 조기 종료(게이팅/모호/미해석/0건)는 `query_results` 미설정 → CSV 버튼 미표시(정상).

### 10.4 검증 추가 항목

- [ ] 스트리밍 질의에서 `process_query` 응답이 토큰 단위로 출력되고, 진행 패널·프로세스 표가 렌더됨
- [ ] `total_count>0`·상위 목록 존재 시 LLM이 "데이터 부족"이라 하지 않음(0건/미해석에서만 그렇게 안내)
- [ ] 마스킹된 args가 요약(LLM)·CSV 양쪽에 포함되고 평문 비밀번호/토큰 비노출
- [ ] 성공 조회 시 CSV 다운로드가 전체 프로세스 목록을 반환, 조기 종료(안내문)에는 CSV 버튼 미표시

### 10.5 의사결정

`docs/02_decision.md` **D-040**에 기재: process_query 출력 채널 보강(스트리밍 화이트리스트 등록 / CSV용 query_results 채움 / 프롬프트 충분성 교정). 핵심 결정(D-039)·파이프라인 불변, 출력 배선만 변경.
