# Spec: 사건 구간 앵커 조회 도구 (`plans/50` G1·G2·G3)

> 요구·실측 근거의 정본은 **`plans/50` §0.3 G1·G2·G3 · §3.4 · §4.1**이다. 배경을 복사하지 않는다.
> 모듈 id: **`incident-window-tools`** (`CAPABILITY-MAP-50.md`) · 패키지: `mcp_server`(읽기 경계, D-119).
> 의존: **없음** · 이 모듈에 의존: `incident-scope` · `evidence-correlation`.

## Objective

폴스타·PromQL 조회 도구가 전부 **now 앵커**라 *"어제 14시쯤 장애"* 의 증거를 그 구간에서 가져올 수
없다(§3.4 *"now() 금지"* 가 도구 계약 수준에서 깨져 있음). 도구에 **기준시각·구간** 인자를 더하고,
구간 내 **전 알람** 도구와 **baseline** 구간 조회를 추가한다.

**핵심 제약**: 인자를 **지정하지 않으면 생성 SQL이 종전과 문자열 동일**하다(회귀 0의 검증 형태).

### 실측 근거(2026-09-02)

| 도구 | 현행 | 컬럼 타입(실측 `testdata/pg/init`·`db_profiles`) |
|---|---|---|
| `polestar_alarm_history` | `CA.CTIME >= NOW() - INTERVAL 'h hours'` | `cmm_alarm.ctime` **timestamp** |
| `polestar_metric_trend` | `ORDER BY stat_date DESC LIMIT n` = 최신 N | `stat_date` **varchar** — h=`YYYYMMDDHH` · d=`YYYYMMDD` · m=`YYYYMM` |
| `prom_metric_range` | `end = time.time()` | epoch 초 |
| 구간 내 전 알람 | **도구 없음**(`alarm_name` 필수·완전 일치) | — |

## 계약 결정

### ① 인자 — 스펙 ③(맵)과 동일

```
polestar_alarm_history(..., reference_time=None, lookback_minutes=None)
polestar_metric_trend(..., reference_time=None, lookback_minutes=None, baseline_periods=None)
polestar_incident_alarms(source, server_name, reference_time, lookback_minutes=60, alarm_name=None)   # 신설
prom_metric_range(..., reference_time=None)
```

- `reference_time`: **ISO 8601** 문자열. `datetime.fromisoformat`으로 파싱되지 않으면 **거부**(ValueError →
  `{error}`) — SQL 리터럴로 보간되므로 자유 문자열을 받지 않는다.
- 폴스타 SQL은 기준시각을 **DB 벽시계(naive)** 로 해석한다. 오프셋이 붙어 있으면 오프셋을 떼고 벽시계 값을 쓴다
  (변환하지 않는다 — 폴스타 DB의 tz를 서버가 모르며, `alarmTime`·질의 파싱 모두 벽시계로 온다).
  PromQL은 epoch가 필요하므로 tz-aware면 그대로, naive면 서버 로컬 tz로 `timestamp()`한다.
- `lookback_minutes` 미지정 시 alarm_history는 `hours*60`, metric_trend는 granularity 1단위(h=60·d=1440·m=43200).
- **구간 경계는 파이썬이 계산해 리터럴로 넣는다** — 엔진별 interval 산술(DB2 `- N MINUTES` vs PG `INTERVAL`)을
  SQL에 두지 않는다. timestamp 리터럴만 방언 분기: PG `TIMESTAMP 'YYYY-MM-DD HH:MM:SS'` · DB2 `TIMESTAMP('…')`.
- `stat_date`는 varchar이므로 granularity 포맷으로 문자열 비교한다(`>= 'YYYYMMDDHH' AND <= '…'`).

### ② baseline (G2)

`baseline_periods=N`이면 사건 구간 **직전** N granularity 단위를 같은 쿼리에 포함한다:
`[incident_from − N단위, reference_time]`. 응답 JSON에 `window`를 실어 소비자가 쪼갠다:

```json
"window": {"reference_time": "…", "incident_from": "…", "baseline_from": "…|null",
           "stat_date_to": "2026090114", "stat_date_incident_from": "2026090113", "stat_date_from": "2026083113"}
```

앵커 모드의 행 제한은 `periods + baseline_periods`(max_rows 상한) — 최신 N이 아니라 구간 안이 대상이다.

### ③ `polestar_incident_alarms` (G3)

`build_alarm_history_sql`과 같은 조인·필터(COALESCE 조인 · severity 0 포함 · RESOURCE_CONF_ID 미조인)에서
`D.NAME` 필터를 **선택**으로 두고 `alarm_name`·`resource_type` 컬럼을 더 돌려준다. **`ORDER BY CA.CTIME ASC`**
(타임라인 병합용 시간순).

## Commands

```bash
cd mcp_server && ../.venv/bin/python -m pytest tests/test_incident_window.py tests/test_polestar_tools.py tests/test_promql_tools.py tests/test_tool_gates.py -q
python scripts/overfit_check.py --ci     # polestar_tools.py는 EXCLUDE — 신규 리터럴은 그 파일 안에만
```

## Project Structure

```
mcp_server/mcp_server/polestar_tools.py   수정 — parse_reference_time · incident_window · 빌더 3종 · 도구 인자 · 신설 도구
mcp_server/mcp_server/promql_tools.py     수정 — run_metric_range(reference_time) · prom_metric_range 인자
mcp_server/tests/test_incident_window.py  신규 — 스냅샷 동일성 · 앵커 SQL · 거부 · 신설 도구 · promql end
```

## Testing Strategy

| 테스트 | 검증 |
|---|---|
| `test_default_sql_unchanged_*` | 인자 미지정 시 생성 SQL == **2026-09-02 스냅샷 리터럴**(4종) |
| `test_anchored_alarm_history_*` | `NOW()`·`CURRENT TIMESTAMP` 부재 · 상·하한 리터럴 · 방언 |
| `test_anchored_metric_trend_*` | granularity별 `stat_date` 포맷 · baseline 하한 · 월 경계 |
| `test_reference_time_rejected` | 비 ISO·인젝션 문자열 → ValueError |
| `test_incident_alarms_sql` | `D.NAME` 선택 · `alarm_name` 컬럼 · `CTIME ASC` |
| `test_incident_alarms_tool_*` | 등록·호출·`window` 메타 · 잘못된 시각은 `{error}` |
| `test_prom_range_end_anchored` | `end` == 기준시각 epoch · `start = end − window` |

## Boundaries

- **Always**: 미지정 경로 SQL 문자열 동일 · 리터럴 보간 전 파싱 검증 · SELECT 단일문 유지(D-003).
- **Ask first**: `_METRIC_KIND_MAP` 확장 · 폴스타 REST 도구에 시각 인자 추가.
- **Never**: 실 DB 호출 테스트(Docker IT는 기존 옵트인 그대로) · `mcp_server → src/sre_agent` import.

## Success Criteria

1. 4개 기본 경로 SQL이 스냅샷과 **문자열 동일**. 2. 앵커 경로에 now 표현이 없다. 3. 잘못된 시각은 SQL 생성 전에 거부.
4. `polestar_incident_alarms`가 `list_tools()`에 노출되고 게이트(`expose_polestar_tools`) 대상이다.
5. 기존 `mcp_server/tests` 무회귀 · `overfit_check --ci` 0.
