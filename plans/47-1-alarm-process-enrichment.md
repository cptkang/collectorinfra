# Plan 47-1: CPU/메모리 알람 영향 프로세스 보강 (Plan 47 확장)

> **작성일**: 2026-06-15
> **부모 Plan**: Plan 47 (알람 이력 기반 패턴 분석) — 본 계획은 47의 `alarm_context_enricher`에 **실시간 프로세스 조회**를 더하는 확장이므로 48이 아닌 47-1로 번호를 둔다.
> **관련 결정**: D-035 (Plan 47), D-032 (AlarmEvent 필드), D-022 (조인 규칙 — 본 계획은 DB 미사용) / **D-036** 기재 완료 (2026-06-16)
> **상태**: 구현 완료 (2026-06-16, D-036 기재)

---

## 1. 목표

CPU·메모리 사용량 알람이 발생하면, 패턴 분석(빈도/주기)에 더해 **그 시점에 실제로 자원을 점유 중인 프로세스**를 폴스타 실시간 프로세스 API로 조회하여 함께 제공한다.

| 구분 | 현재 (Plan 47) | 확장 후 (47-1) |
|------|----------------|----------------|
| 분석 입력 | 알람 이벤트 + 이력 통계 | + **현재 영향 프로세스 스냅샷 (CPU/메모리 알람 한정)** |
| 출력 | 요약·원인·권고 + 패턴 근거표 | + **영향 프로세스 표 (상위 N)** + 원인/권고에 프로세스 인용 |
| 운영자 가치 | "늘 발생하나 / 지금 확인할 알람인가" | + "**무엇이 지금 자원을 먹고 있는가**" |

**예시**: 메모리 95% 심각 알람 → "java(pid 12345)가 물리메모리 38% 점유 중, 상위 3개가 전체의 70%" 를 표로 제시하고, 권고 조치에 해당 프로세스를 인용.

---

## 2. 폴스타 프로세스 API

### 2.1 엔드포인트

```
GET {base_url}/rest/server/process/listByhostname?hostname={hostname}
```

- **base_url (db_id별)**: 김포 `polestar_cm_gp` → `http://polestar.kbonecloud.com`, 여의도 `polestar_cm_yd` → `http://yd-polestar.kbonecloud.com`
- **인증 불필요**: 내부 시스템이며 인증 없이 조회된다(크롬 시크릿창 비로그인 상태에서 데이터 반환 확인). 인증 헤더·토큰을 다루지 않는다.
- **scheme는 `http://`**: 내부망 시스템이라 TLS를 쓰지 않는다(https 아님).
- **조회 키는 `hostname`** — `server_name`(=`${platformName}`)이 아니라 **`AlarmEvent.hostname`**(`${hostname}`)을 사용한다.
  > 주의: 실수신 데이터에서 `serverName="cop0-aisapd02"` vs `hostname="saisvd01"`처럼 둘이 완전히 다르다 (Plan 47 §9). 이 API는 반드시 hostname으로 조회한다. Plan 47의 DB 이력 조회가 `r.name`(=serverName)을 쓰는 것과 **정반대 키**임에 유의.

### 2.2 응답 구조 (요약)

```json
{
  "date": "YYYY-mm-DD HH:MM:SS",      // 스냅샷 시각
  "data": { "list": [ {
      "name": "<프로세스명>", "pid": <int>, "ppid": <int>, "user": "<계정>",
      "pcpu": <float>,        // 코어 합산 CPU% (4코어 100% = p100cpu 25%)
      "p100cpu": <float>,     // 100% 기준 정규화 CPU% (표시·랭킹용)
      "pmem": <float>,        // 물리 메모리 %
      "rss": <int>, "vsz": <int>, "thcnt": <int>, "handlecnt": <int>,
      "args": "<실행 인자>",  // ★ 민감정보 포함 가능 — 마스킹 필수
      "host": "<대상 IP>", "platformName": "<서버명>", "platformAncestry": "...",
      "stime": <epoch ms>, "ctime": "<HH:MM:SS>", "iobyte": <int>,
      "platformId": <int>
  }, ... ] },
  "id": "server"
}
```

- **CPU 랭킹**: `p100cpu` 내림차순 (정규화 값이 직관적). 없으면 `pcpu` 폴백.
- **메모리 랭킹**: `pmem` 내림차순 (보조로 `rss` 표기).
- `args`에 DB 접속 문자열·`--password`·토큰 등이 노출될 수 있다 → **출력·LLM 주입 전 반드시 마스킹** (프로젝트 제약: 민감 데이터 마스킹).

---

## 3. 설계 핵심 결정

### 3.1 조회 대상 게이팅 — CPU/메모리 발생 알람만

| 조건 | 판정 |
|------|------|
| `is_clear == False` (발생 알람) | 해소 알람은 프로세스 조회 안 함 |
| 알람 종류 = CPU 또는 메모리 | `resource_type`/`alarm_name` 키워드로 판정 (§5.2) |
| db_id에 base_url 매핑 존재 | 미매핑 인스턴스(예: `polestar`, `polestar_b0`)는 건너뜀 |

그 외 알람(디스크/네트워크 등)은 프로세스 조회를 하지 않는다 — 의미가 없고 불필요한 외부 호출이다.

### 3.2 배치 위치 — `alarm_context_enricher` 노드 확장 (그래프 노드 수 불변)

프로세스 조회도 "분석용 컨텍스트 수집"이므로 별도 노드를 만들지 않고 기존 enricher에 **독립 단계**로 추가한다. 그래프는 3-노드 그대로 유지한다.

- 이력 조회(폴스타 DB)와 프로세스 조회(폴스타 HTTP API)는 **서로 독립**이므로 `asyncio.gather`로 **동시 실행**한다 → 총 지연 = max(둘) ≈ 합산 아님.
- 각각 자체 try/except + 타임아웃 → 한쪽 실패가 다른 쪽·전체 분석을 막지 않는다 (graceful degradation, Plan 47 원칙 계승).
- 노드 전체는 기존대로 `enrich_timeout_seconds`로 감싸 상한을 둔다.

### 3.3 통계/선별은 결정적, LLM은 해석만 (Plan 47 원칙 계승)

상위 프로세스 선별·정렬·마스킹은 **순수 Python 함수**로 결정적 처리하고, LLM에는 마스킹된 상위 N 요약 텍스트를 주입해 원인/권고에 **인용만** 하게 한다. 표는 결정적 데이터, 문장은 LLM 해석.

---

## 4. 변경 후 아키텍처

```
START → alarm_context_enricher → alarm_analyzer → alarm_notifier → END
            │  ├─ (always)  폴스타 DB 이력 조회 → history_stats   (Plan 47)
            │  └─ (CPU/메모리 발생 알람만) 폴스타 프로세스 API → process_snapshot  (47-1)
            │       두 조회는 asyncio.gather로 동시 실행, 각자 독립 degradation
            ▼
       alarm_analyzer  : history_section + process_section 프롬프트 주입
            ▼
       alarm_notifier  : workb/webhook/UI 에 패턴 근거표 + 영향 프로세스표 추가
```

`AlarmState` 확장:

```python
class AlarmState(TypedDict):
    alarm_event: AlarmEvent
    history_stats: Optional[AlarmHistoryStats]      # Plan 47
    process_snapshot: Optional[ProcessSnapshot]     # 47-1 (신규)
    analysis_result: Optional[AlarmAnalysisResult]
    error: Optional[str]
```

---

## 5. 컴포넌트 상세 설계

### 5.1 `src/alarm/domain/alarm.py` — 도메인 모델 추가

```python
@dataclass
class ProcessInfo:
    """프로세스 1건 (마스킹·정규화 완료)."""
    name: str
    pid: int
    ppid: int
    user: str
    p100cpu: float       # 100% 기준 CPU% (표시·랭킹 기본)
    pcpu: float          # 코어 합산 CPU%
    pmem: float          # 물리 메모리 %
    rss: int             # resident set size (bytes, 0 허용)
    args: str            # 마스킹·절단된 실행 인자

@dataclass
class ProcessSnapshot:
    """알람 시점 영향 프로세스 스냅샷."""
    alarm_kind: str                   # "cpu" | "memory"
    captured_at: Optional[datetime]   # 응답 date
    top: list[ProcessInfo]            # 해당 지표 내림차순 상위 N
    total_count: int                  # 조회된 전체 프로세스 수
    source_host: str                  # 조회에 사용한 hostname
```

`AlarmAnalysisResult`에는 별도 LLM 필드를 추가하지 않는다 — 프로세스 정보는 결정적 표로 출력하고, LLM은 기존 `probable_cause`/`recommended_action`에서 인용한다.

### 5.2 `src/alarm/domain/process_rank.py` — 선별·마스킹 (신규, 순수 함수)

DB/HTTP/LLM 의존 없는 순수 로직 → **domain 계층** (Plan 47 `alarm_pattern.py`와 동일 원칙).

```python
def classify_alarm_kind(event: AlarmEvent) -> Optional[str]:
    """알람이 CPU/메모리인지 판정한다. 아니면 None.

    판정 키워드(대소문자 무시):
    - cpu:    resource_type/alarm_name 에 'cpu'
    - memory: resource_type/alarm_name 에 'memory' | '메모리' | 'mem'
    """

def select_top_processes(
    raw_list: list[dict], alarm_kind: str, top_n: int
) -> tuple[list[ProcessInfo], int]:
    """원시 list → 마스킹·정규화 후 지표 내림차순 상위 N + 전체 건수.

    - cpu: p100cpu(없으면 pcpu) 내림차순
    - memory: pmem 내림차순
    - 각 항목 args는 mask_args()로 마스킹·절단
    """

def mask_args(args: str, max_len: int = 120) -> str:
    """실행 인자에서 민감정보를 마스킹하고 길이를 제한한다.

    마스킹 패턴(대소문자 무시, 값만 ***로 치환, 키는 보존):
      (password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|credential)
      [=: ]\\s*<값>
    그 외 매우 긴 인자는 max_len에서 절단 후 '…' 부가.
    """
```

> **마스킹은 선택이 아니라 필수** — 프로젝트 제약(민감 데이터 마스킹). LLM 주입·UI·workb·webhook 모든 출력에 마스킹된 `args`만 사용한다.

### 5.3 `src/alarm/infrastructure/polestar_process_api.py` — API 클라이언트 (신규)

```python
class PolestarProcessApiClient:
    """폴스타 실시간 프로세스 조회 API 클라이언트 (httpx, 읽기 전용 GET)."""

    def __init__(self, alarm_cfg) -> None: ...   # base_url 매핑/타임아웃 보유

    async def list_by_hostname(self, db_id: str, hostname: str) -> Optional[list[dict]]:
        """{base_url}/rest/server/process/listByhostname?hostname=... 호출.

        - base_url = alarm_cfg.get_process_api_base_url(db_id); None이면 즉시 None 반환
        - hostname은 urllib.parse.quote로 URL 인코딩 (쿼리 인젝션 방지)
        - 타임아웃 process_api_timeout_seconds
        - 비200/파싱 실패/타임아웃 → None 반환 (예외는 호출부에서 degradation)
        - 반환: data.list 배열 (없으면 [])
        """
```

- `httpx.AsyncClient` 사용 (notifier와 동일 스택).
- scheme는 `http://` (내부망, TLS 없음). base_url에 scheme 포함.
- **인증 불필요** — 헤더/토큰 없음 (내부 시스템, 비로그인 조회 확인). 인증 관련 설정·코드를 두지 않는다.

### 5.4 `alarm_context_enricher` 노드 확장

```python
async def alarm_context_enricher_node(state, config) -> dict:
    # 기존: history_stats (Plan 47)
    # 추가: process_snapshot — CPU/메모리 발생 알람 + base_url 매핑 존재 시에만
    #
    # history 조회와 process 조회를 asyncio.gather로 동시 실행.
    # 각 작업은 자체 try/except로 실패 시 None. 노드 전체는 enrich_timeout_seconds 상한.
    ...
    return {"history_stats": ..., "process_snapshot": ...}
```

게이팅 흐름:
1. `kind = classify_alarm_kind(event)` → None이면 process 조회 스킵
2. `event.is_clear`이면 스킵
3. `cfg.alarm.process_enrich_enabled`가 False이면 스킵
4. `get_process_api_base_url(event.db_id)`가 None이면 스킵 (로그 debug)
5. `client.list_by_hostname(db_id, event.hostname)` → `select_top_processes(raw, kind, top_n)` → `ProcessSnapshot`

리포지토리/클라이언트는 Plan 47과 동일하게 `config["configurable"]`로 주입(미주입 시 스킵). 테스트 API 경로(§5.8)는 별도 처리.

### 5.5 프롬프트 확장 (`src/alarm/prompts/alarm_analyzer.py`)

유저 템플릿에 `{process_section}` 추가 (이력 섹션 다음). 노드에서 `ProcessSnapshot` → 텍스트 렌더:

```
[영향 프로세스 — 메모리 상위 (2026-06-05 09:33:59 기준, 전체 142개)]
1. java        pid 12345 user appusr  메모리 38.1% · CPU 12.0%  args: -Xmx8g -jar app.jar
2. python3     pid 23456 user root    메모리  9.2% · CPU  1.1%  args: worker.py
3. postgres    pid 3401  user pgsql   메모리  7.8% · CPU  0.4%  args: ***(마스킹)
```

시스템 프롬프트 규칙 추가:
- `[영향 프로세스]` 섹션이 있으면 `probable_cause`/`recommended_action`에 **상위 프로세스(이름·pid)를 구체적으로 인용**할 것.
- 프로세스 수치를 새로 계산하지 말고 제공된 값만 인용할 것.
- 섹션이 없으면(조회 불가/비대상 알람) 프로세스를 추측하지 말 것.
- 마스킹된(`***`) 인자의 내용을 추정·복원하지 말 것.

`process_snapshot=None`이면 `process_section=""`로 주입(degradation).

### 5.6 출력 채널 확장

**출력 순서 (2026-06-16 조정)**: 영향 프로세스 표를 **패턴 분석보다 먼저** 출력한다 — "무엇이 지금 문제를 일으키는가(프로세스) → 이 문제가 얼마나 잦은가(패턴)" 순이 운영자 확인 흐름에 자연스럽다. UI(`app.js`)·workb(`build_workb_body`) 모두 동일 순서.

| 위치 | 변경 |
|------|------|
| `src/static/js/app.js` | `renderHistoryEvidence` 옆에 **영향 프로세스 표** 렌더 — 컬럼: 프로세스 / PID / CPU(100%) / MEM / 사용자 + **각 행 아래 실행 파라미터(args, 마스킹됨) 전체폭 보조 줄**(서비스 추적용, 2026-06-16 추가). 캡션에 스냅샷 시각·전체 건수. CPU 알람은 CPU열, 메모리 알람은 MEM열 우선. 패턴 표보다 먼저 배치. Plan 47 `.alarm-evidence` 스타일 재사용 |
| `src/static/css/style.css` | `.alarm-proc-table` 미세 조정 + `.alarm-proc-args`(args 보조 줄 — monospace·muted·word-break) |
| `src/api/routes/alarm.py` UI push 2곳 | `process_snapshot`(=`_process_to_dict`, args 마스킹 포함) + (기존)`alarm_time` 추가 |
| `alarm_notifier.build_workb_body()` | `<b>영향 프로세스</b>` 텍스트 표(상위 N) + **각 프로세스 args 보조 줄**(html.escape, 마스킹값). 패턴 분석 섹션보다 먼저 배치. `process_snapshot` 없으면 생략 |
| `alarm_notifier._send_webhook()` | `process_snapshot`(top 리스트, args 마스킹 포함) 키 추가 |

### 5.7 설정 (`src/config.py` AlarmConfig 추가 필드)

```python
# ── Plan 47-1: 영향 프로세스 보강 ──
process_enrich_enabled: bool = True
# db_id=base_url 매핑 (CSV — .env JSON 회피, WorkbConfig 패턴과 동일).
# 내부망 시스템이라 scheme는 http:// (TLS 없음). 인증 불필요.
process_api_base_urls_csv: str = (
    "polestar_cm_gp=http://polestar.kbonecloud.com,"
    "polestar_cm_yd=http://yd-polestar.kbonecloud.com"
)
process_api_timeout_seconds: int = 3      # 추가 외부 호출 — 이력보다 짧게
process_top_n: int = 5                     # 표시할 상위 프로세스 수
# 인증·TLS 설정 없음 — 내부 시스템 http, 비로그인 조회 (§9)

def get_process_api_base_url(self, db_id: str) -> Optional[str]:
    """db_id에 매핑된 base_url 반환 (없으면 None)."""
```

`.env.example` 추가 (스칼라 + CSV — list/dict JSON 필드 없음):

```ini
# ── 알람 영향 프로세스 보강 (Plan 47-1) ─────────
# 내부망 시스템 — http://, 인증 불필요
ALARM_PROCESS_ENRICH_ENABLED=true
ALARM_PROCESS_API_BASE_URLS_CSV=polestar_cm_gp=http://polestar.kbonecloud.com,polestar_cm_yd=http://yd-polestar.kbonecloud.com
ALARM_PROCESS_API_TIMEOUT_SECONDS=3
ALARM_PROCESS_TOP_N=5
```

### 5.8 테스트 API 확장 (`/alarm/analyze-test`, `/raw`)

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `query_process: bool` | `True` | 폴스타 프로세스 API 호출 여부 (hostname 기준). 테스트 엔드포인트는 기본 True. CPU/메모리 발생 알람만 실제 조회(그 외 자동 생략), simulated_processes 지정 시 그쪽 우선 (2026-06-16 기본값 False→True 변경) |
| `simulated_processes: Optional[list[dict]]` | `None` | 지정 시 API 호출 없이 이 목록으로 `select_top_processes` 수행 — 시나리오 검증·마스킹 확인용 |

응답(`AlarmTestResponse`)에 `process_snapshot` 요약(상위 N + 전체 건수 + 스냅샷 시각)을 포함해 디버깅 가능하게 한다.

---

## 6. 구현 단계

### Phase 1: 도메인 + API 클라이언트 + 설정
- [x] `src/alarm/domain/alarm.py`: `ProcessInfo`, `ProcessSnapshot` 추가
- [x] `src/alarm/domain/process_rank.py`: `classify_alarm_kind`, `select_top_processes`, `mask_args` (순수 함수)
- [x] `src/alarm/infrastructure/polestar_process_api.py`: `PolestarProcessApiClient` (httpx GET, hostname URL 인코딩, 타임아웃, None degradation) — 응답 `date`를 `ProcessApiResult.captured_at`으로 함께 반환
- [x] `src/config.py`: `AlarmConfig` 필드 + `get_process_api_base_url()`, `.env.example` 갱신
- [x] 단위 테스트: 알람 종류 판정, CPU/메모리 정렬, **마스킹(패스워드·토큰·접속문자열 비노출)**, 빈 list/누락 필드 처리 (`tests/test_alarm_process_rank.py`)

### Phase 2: 그래프 통합
- [x] `src/alarm/orchestration/alarm_graph.py`: `AlarmState.process_snapshot` 추가
- [x] `alarm_context_enricher.py`: process 조회 단계 추가 — 게이팅 + history와 `asyncio.gather` 동시 실행 + 독립 degradation (노드는 항상 두 키 반환), `alarm_worker.py`에 `process_client` 주입
- [x] `alarm_analyzer` 프롬프트/노드: `{process_section}` + `_render_process_section()` + 인용 규칙 4개
- [x] 단위 테스트: 비대상 알람(디스크)·해소 알람·미매핑 db_id 스킵, API 실패 시 `process_snapshot=None`으로 분석 계속, history와 동시 실행 검증 (`tests/test_alarm_process_enrich.py`)

### Phase 3: 출력 채널 + 테스트 API
- [x] `alarm.py`: UI push 2곳 `process_snapshot` 추가, `_process_to_dict`, `query_process`/`simulated_processes` 파라미터, 응답 요약, 미리보기·실발송 연동
- [x] `alarm_notifier.py`: workb 텍스트 표 + webhook 필드
- [x] `app.js` (+ `style.css`): 영향 프로세스 표 렌더
- [x] 통합 테스트: `simulated_processes`로 CPU/메모리 시나리오 LLM 인용 확인 (단위 테스트로 검증). `query_process=true` 실 API end-to-end는 운영 환경에서 `/alarm/analyze-test`로 수동 확인 필요

---

## 7. 검증 체크리스트

- [ ] `ALARM_PROCESS_ENRICH_ENABLED=false` 시 기존 Plan 47 동작과 동일 (process_section 미주입)
- [ ] **API 조회에 `hostname`을 사용** (server_name 아님) — gp/yd 실데이터(hostname≠serverName)로 확인
- [ ] db_id별 base_url 매핑 정확 (gp→polestar, yd→yd-polestar), 미매핑 db_id는 스킵
- [ ] CPU 알람은 p100cpu, 메모리 알람은 pmem 기준 정렬 상위 N
- [ ] 디스크/네트워크 등 비대상 알람·해소 알람은 프로세스 조회 안 함
- [ ] **프로세스 args의 패스워드/토큰/접속문자열이 LLM·UI·workb·webhook 어디에도 평문 노출 안 됨**
- [ ] hostname URL 인코딩으로 쿼리 인젝션 방지
- [ ] API 타임아웃/비200/네트워크 오류 시 분석·발송 정상 진행 (process 정보만 생략)
- [ ] history 조회와 process 조회 동시 실행, 노드 총 지연이 `enrich_timeout_seconds` 내
- [ ] LLM이 상위 프로세스를 원인/권고에 인용, 미조회 시 추측 안 함
- [ ] `python scripts/arch_check.py` 계층 위반 없음 (process_api=infrastructure, process_rank=domain, enricher=application)

---

## 8. 의사결정 기록 (구현 완료 후 `docs/02_decision.md` D-036 기재)

| 항목 | 내용 |
|------|------|
| **결정** | CPU/메모리 발생 알람에 한해 폴스타 실시간 프로세스 API(`/rest/server/process/listByhostname`)를 **hostname으로 조회**, 상위 프로세스를 결정적으로 선별·마스킹하여 패턴 근거에 더해 제공. `alarm_context_enricher`에 프로세스 조회 단계를 추가(그래프 노드 수 불변)하고 history 조회와 `asyncio.gather`로 동시 실행 |
| **근거** | "왜 자원이 높은가"의 직접 근거가 프로세스 점유율. DB 이력(패턴)과 보완 관계. 별도 노드 없이 enricher 확장으로 응집. 외부 HTTP 의존은 타임아웃+graceful degradation으로 격리 |
| **대안** | ① 별도 노드 분리 — 그래프 복잡도만 증가로 기각 ② 모든 알람에 프로세스 조회 — 디스크/네트워크엔 무의미, 외부 호출 낭비로 기각 ③ 프로세스 원시 데이터를 LLM에 그대로 — 토큰·환각·민감정보 노출로 기각(결정적 선별+마스킹 채택) |
| **키 차이 주의** | DB 이력 조회는 `r.name`(=serverName), 프로세스 API는 `hostname` — 동일 알람에서 **서로 다른 식별자**를 사용 (D-032/Plan 47 §9 연계) |

---

## 9. 주의 사항 / 엣지 케이스

| 항목 | 내용 |
|------|------|
| **민감정보 마스킹 (필수)** | `args`에 DB 접속문자열·`--password`·토큰 노출 가능 — `mask_args()`로 마스킹 후에만 사용. 마스킹 누락은 보안 사고. 단위 테스트로 회귀 고정 |
| **hostname vs serverName** | API는 hostname, DB 이력은 serverName(r.name). 혼동 시 0건 또는 오조회. AlarmEvent.hostname을 그대로 사용 |
| **CPU 정규화(pcpu vs p100cpu)** | `pcpu`는 코어 합산(4코어 100%=실제 25%), `p100cpu`는 100% 정규화. 표시·랭킹은 p100cpu 기본, 없으면 pcpu 폴백하되 표기를 구분 |
| **API 인증 (확인 완료 — 불필요)** | 내부 시스템으로 **인증 없이 조회됨**(크롬 시크릿창 비로그인 상태 데이터 반환 확인). 토큰/세션/헤더를 다루지 않는다. 향후 인증이 도입되면 그때 추가 |
| **base_url scheme (확인 완료 — http)** | 내부망 시스템이라 **`http://`** 사용(TLS 없음). 별도 인증서·`verify_ssl` 설정 불필요 |
| **응답 시각(date) 신뢰** | `date`는 스냅샷 시각으로 표시만; 알람 시각과 수 초~수십 초 차이 가능(실시간 조회라 정상). LLM에 "현재 시점 스냅샷"으로 전달 |
| **대형 프로세스 목록** | 수백 개 프로세스 응답 가능 — 상위 N만 보존, 전체 건수만 표기 |
| **이름 충돌 다수 프로세스** | 동일 name 다수(워커 등)는 개별 행으로 표기(pid로 구분). 집계 합산은 하지 않음(과한 해석 방지) |
| **`.env` 신규 필드** | 스칼라 + `=`구분 CSV — list/dict JSON 필드 없음 (Known Mistakes 2026-03-23 비해당) |
| **외부 호출 SSRF** | base_url은 설정 고정값(사용자 입력 아님), hostname만 인코딩하여 쿼리에 부착 — 경로/호스트 조작 불가. 설정 외 URL 호출 없음 |
