# 04. 폴스타 MCP 연동 — `mcp_server` 고수준 도구 확장 (Polestar MCP Integration)

> 작성일: 2026-07-24 · **이관일: 2026-07-24** (SREAgent → collectorinfra `plans/sre-agent/`, 통합 결정: collectorinfra D-118 / SREAgent D-021)
> **결정 배경**: 사용자 지시(2026-07-24) — "폴스타와 연동은 MCP를 통해 연동한다"(D-013). collectorinfra의 MCP 구현(`mcp_server/` 패키지, `plans/15-mcp-server.md` — 구현 완료 상태)을 분석해 이식·확장한다.
> **소비 계획**: `plans/01-event-noise-gate.md` §6(게이트 신호 수집)과 `plans/02-incident-investigation-holmesgpt.md` §5(HolmesGPT 조사 도구)가 본 계획의 MCP 서버를 공용 소비한다. `plans/06-remote-vm-access.md` §4(원격 조사의 폴스타 축)도 동일한 `Config.mcp_servers` 등록(§7.2)을 소비한다.
> **관련 결정**: D-001(HolmesGPT SDK — `mcp_servers` 필드 실측), D-013(폴스타 연동 MCP 일원화)
> **신규 결정(본 계획 예약, 구현 착수 시 등재)**: D-014(고수준 폴스타 도구 노출 정책), D-015(MCP 전송 구간 인증 보강). ※ 이관 후 등재는 **collectorinfra `docs/02_decision.md` 번호 체계**를 grep해 그쪽 최댓값+1로 부여한다(이 D-번호들은 SREAgent 체계의 예약 인용).
> **상태**: 계획(미구현) — **통합 갱신(2026-07-24)**: 통합으로 "이식"이 불필요해졌다 — 본 저장소의 `mcp_server/`가 원본 그 자체이므로, 계획의 실체는 **기존 `mcp_server`에 대한 직접 확장**이다(§3 표의 "그대로 이식" 행은 전부 불요, "수정·신규 확장" 행만 유효). §4.1 게이트용 집약 도구 `polestar_noise_signals`는 소비자(SREAgent 자체 게이트)가 Plan 01 대체로 소멸하여 **폐기** — collectorinfra 게이트는 기존 자체 신호 수집 경로(`polestar_noise_context.py` 등)를 유지한다. **갱신(2026-07-27 · collectorinfra D-119)**: 본 서버의 성격을 폴스타 전용에서 **관측 데이터 읽기 접근 경계**로 재정의 — Prometheus PromQL 도구를 추가 노출한다(§4.4 신설, 도구 명세·hostname 앵커 규약은 Plan 06 §3·§5 관할).
> **번호 체계 주의**: 본 문서의 D-번호는 SREAgent(이관 전) 결정 체계의 인용 — collectorinfra D-번호와 무관(폴더 README 참조).

---

## 1. 개요 및 목적

폴스타(POLESTAR) DB·REST 접근을 **자체 MCP 서버 한 곳으로 일원화**한다. DB 접속 정보·읽기 전용 강제·방언 분기·마스킹이 전부 MCP 서버 경계 안에 있고, 소비자는 두 종류다:

```
폴스타 PG(gp/yd) · DB2(b0) · REST(프로세스 API)
        ▲  (읽기 전용 SELECT/GET만)
┌──────────────────────────────────────────────┐
│  polestar MCP 서버  (FastMCP · SSE · 독립 패키지) │
│  - 저수준: execute_sql·get_table_schema·health_check·list_sources │
│  - 고수준(신규): 폴스타 고정 SQL 도구 8종 + noise_signals 집약 도구 │
└──────────────────────────────────────────────┘
        ▲ MCP(SSE)                    ▲ MCP(SSE)
  [소비자 1] 노이즈 게이트(Plan 01)      [소비자 2] HolmesGPT(Plan 02)
  결정적 MCP 클라이언트(mcp SDK)         Config.mcp_servers 등록
  — LLM 미개입, 고수준 도구 직접 호출      — RemoteMCPToolset이 도구 자동 발견
```

※ **통합 갱신**: 소비자 1(SREAgent 자체 게이트)은 Plan 01 대체로 소멸 — 실 소비자는 HolmesGPT 조사(Plan 02/06, `sre_agent/` 패키지)다. collectorinfra 게이트는 종전 자체 신호 수집 경로를 유지하며 본 서버의 신규 소비자가 아니다.

SREAgent 코어(`src/sre_agent/`)에는 DB 드라이버(asyncpg/ibm_db)가 들어가지 않는다 — 드라이버·연결 문자열은 MCP 서버 패키지에만 존재한다.

> **스코프 주의(D-119 재정의)**: 본 서버는 **관측 데이터 읽기 접근 경계**(하향 방향 — 폴스타 + Prometheus §4.4)다. 향후 관측 소스 추가도 별도 서버가 아니라 이 경계의 확장으로 편입한다(소스별 별도 서버·holmesgpt 내장 toolset 직결은 미채택 — D-119 대안 기각). SREAgent 기능(조사)을 외부에 노출하는 MCP 서비스는 별도이며 Plan 05가 다룬다. collectorinfra 연동 배치에서는 그쪽이 운영 중인 동일 기원 `mcp_server/` 인스턴스와의 공유 옵션이 있다(Plan 05 §6).

## 2. collectorinfra 구현 분석 요약 (이식 원본 실측)

collectorinfra는 기성 DBHub(npm)를 폐기하고 자체 MCP 서버로 전환을 완료했다. 이식 대상 자산:

| 자산 | 위치(원본) | 내용 |
|---|---|---|
| 서버 골격 | `mcp_server/mcp_server/{server,tools,security,db,config}.py` | FastMCP(`mcp[cli]`), `[server] host=0.0.0.0 port=9099 transport=sse`, lifespan에서 풀 초기화 |
| 도구 5종 | `tools.py` `@mcp.tool()` | `search_objects`·`execute_sql(source, sql)`·`get_table_schema`·`health_check(source)`·`list_sources()` — 전부 JSON 문자열 반환 |
| 읽기 전용 강제 | `security.py::validate_readonly` | 주석 제거(sqlparse)+리터럴 마스킹 후 금지 키워드(DML/DDL/DCL/EXEC/CALL/MERGE) 단어 경계 매칭, 다중 문장 차단, 세미콜론 인젝션 차단. **클라이언트와 서버 양쪽 독립 구현(이중 방어)** |
| DB 풀·방언 | `db.py::DBPoolManager` | PG=asyncpg 풀(command_timeout), DB2=동기 ibm_db를 `asyncio.to_thread` 래핑(요청별 연결). `_normalize_row`(datetime→iso, Decimal→float, DB2 대문자 칼럼→소문자) |
| 소스 라우팅 | `config.toml [[sources]]` | **`source` 파라미터 = db_id = 소스명 3중 일치**. 연결 문자열은 서버 `.env`의 `{NAME_UPPER}_CONNECTION`, 빈 값이면 해당 소스 자동 비활성 |
| 클라이언트 패턴 | `src/dbhub/client.py` | `mcp` SDK `sse_client`→`ClientSession`→`call_tool`, `asyncio.wait_for(60s)`, 재연결 최대 3회(2s·4s·6s), `.content[].text` JSON 파싱, `{"error":...}` → 예외 승격, SQL 파일 로깅 |
| 호출부 계약 | `polestar_history.py` 등 | 코드가 runnable 고정 SQL 조립(LLM 우회), `_sql_literal`(널바이트 제거+`'` 이중화) 이스케이프, 미등록 db_id 선확인 후 graceful degradation |

원본 상태: 코드·테스트(서버 34, 클라이언트 통합 57) 완료, 단 **PostgreSQL/DB2 실 연결 런타임 검증은 미완**(Docker 미실행·ibm-db 미설치) — SREAgent 이식 시 이 검증 부채를 승계하므로 §9 수용 기준에 명시한다.

## 3. 이식 범위와 SREAgent 확장 (D-014 예약)

**통합 갱신**: 이식은 불요 — 본 저장소의 기존 `mcp_server/`(독립 최상위 패키지)에 직접 확장한다. 아래 표의 "그대로 이식" 행은 **기존 자산 그대로 사용**으로 읽는다. `mcp_server`와 `sre_agent/` 패키지는 서로 import하지 않는다(통신은 MCP뿐 — 폴더 경계 원칙, README).

| 구분 | 항목 | 조치 |
|---|---|---|
| 그대로 이식 | 서버 골격·SSE transport·`validate_readonly`·`DBPoolManager`·`_normalize_row`·config.toml 소스 정의 | 원본 복사 후 소스명을 폴스타 3종으로 정리 |
| 수정 | `execute_sql` 노출 정책 | **기본 비노출**(고수준 도구의 내부 함수로만 사용). LLM에 raw SQL 작성을 열면 방언 오류·금지 조인·D-035 위반 리스크 — 탐색적 조사가 필요할 때만 `expose_execute_sql=true` 옵트인, 이때 §6의 도메인 deny 검증이 추가로 걸린다 |
| 신규 확장 | **고수준 폴스타 도구**(§4) | 고정 SQL·REST를 서버에 내장, 도구 인자는 값(server_name 등)만 — SQL 텍스트가 MCP 경계를 넘지 않음 |
| 신규 확장 | 도메인 deny 검증 | `validate_readonly`에 폴스타 금지 패턴 추가: `RESOURCE_CONF_ID` = `CONFIGURATION_ID` 조인, `cmm_vendor`/`cmm_os`/`cmm_os_param` 참조 (collectorinfra D-022/D-028 인용) |
| 신규 확장 | 프로세스 REST 프록시 도구 | httpx GET → **`args` 마스킹을 서버 측에서 강제**(어떤 소비자도 마스킹을 우회 불가) |
| 신규 확장 | 전송 인증(§6, D-015 예약) | 원본은 SSE 무인증(네트워크 격리 전제 — 실측 확인). 운영 연동 전 보강 필수 |

## 4. 도구 명세 (서버가 노출)

### 4.1 게이트용 집약 도구 — **폐기(통합 갱신)** (구 Plan 01 소비 전제)

| 도구 | 인자 | 반환 |
|---|---|---|
| `polestar_noise_signals` | `source, server_name, alarm_name` | `{importance, is_maintenance, noti_policy_count, parent_avail, signals_source}` — 서버 내부에서 신호별 **개별 try/except**로 부분 반환, 실패 신호는 `"unavailable"` 표기(게이트 step2/5 보수화 입력) |

### 4.2 조사용 고수준 도구 (Plan 02/06 HolmesGPT 자동 발견 — 통합 갱신: 게이트 소비 전제 삭제)

| 도구 | 인자 | 소스 |
|---|---|---|
| `polestar_alarm_history` | `source, server_name, alarm_name, hours, exclude_alarm_id` | `cmm_alarm ⋈ cmm_alarm_def ⋈ cmm_resource` (COALESCE(PLATFORM_RESOURCE_ID, ID) 승격) |
| `polestar_metric_trend` | `source, server_name, kind(cpu\|memory\|filesystem\|disk_io), granularity(h\|d\|m), periods` | `cmm_metric_stat_h/d/m` — `definition_name` 매핑은 서버 상수 |
| `polestar_resource_status` | `source, server_name` | `cmm_resource` 서브리소스별 `AVAIL_STATUS`·중요도·유지보수 |
| `polestar_topology` | `source, server_name, max_hops` | `AVAIL_DEPEND_RESOURCE_ID(_2)` 조상/자손 + 가용 상태. b0(DB2)는 1홉 폴백 |
| `polestar_process_snapshot` | `hostname, top_n, sort(cpu\|mem)` | REST `listByhostname` — `p100cpu`/`pmem` 랭킹, **args 마스킹 후** 반환, "실시간 단면" 명시 |
| `polestar_os_config` | `source, hostname` | `core_config_prop` EAV 피벗(OSType/커널/sysctl/벤더) — HOSTNAME 브릿지(`NAME='Hostname'` 동반) |
| `polestar_change_history` | `source, server_name, hours` | `cmm_resource_lifecycle_history` (gp/yd만 — b0는 빈 결과+사유) |
| `polestar_condition_log` | `source, alarm_id` | `cmm_alarm.CONDITIONLOGTEXT` |

공통: 반환은 JSON 문자열 `{data..., queried_at, source_kind}`(인용 의무 지원), 오류는 `{"error": ...}`(예외 비전파 — holmesgpt `RemoteMCPTool`이 ERROR status로 변환), 각 고정 SQL에 **LIMIT/FETCH FIRST를 명시**(원본의 max_rows는 사후 슬라이스라 DB 전량 fetch 함정 있음 — SQL 자체 제한이 1차).

- **후보(비확정 · 통합 델타 2026-07-24)**: `polestar_host_snapshot(hostname, kind)` — collectorinfra **Plan 60 §18 E8(D-117)** 이 확정한 폴스타 에이전트 read-only 스냅샷 채널(REST/ES 경유·kind별 USE 프로파일·변경명령 물리 제외)이 구현되면 이를 고수준 도구로 노출한다. 원격 배치의 조사에서 dmesg/journal·USE 명령 원문이 가용해져 Plan 02 §6 시그니처 표의 로그 원문 행이 원격에서도 동작한다(그 전에는 미노출 — Plan 02 §6의 Prometheus 카운터 대체 경로 유지). E8 착수 시 노출 여부·마스킹 정책을 결정한다.
### 4.3 운영 도구 (원본 유지)

`list_sources()`(인자 없음 — **HolmesGPT `health_check_tool`로 지정**), `health_check(source)`, `get_table_schema(source, table)`(테이블명 정규식 화이트리스트 `^[a-zA-Z_][a-zA-Z0-9_.]*$`).

### 4.4 PromQL 도구 (D-119 신규 — 명세 관할: Plan 06 §3)

Prometheus 접근을 본 서버로 일원화한다(holmesgpt 내장 `prometheus/metrics` toolset 직결 미채택 — collectorinfra D-119):

- **고수준(기본 노출)**: hostname 앵커 — 서버가 `hostname(=server_name)` 인자에서 `{nodename="<hostname>"}` 필터를 결정적으로 조립(LLM이 라벨 미취급 — Plan 06 §5-0). 예: `prom_metric_range`·`prom_metric_instant`.
- **원시(옵트인)**: instant/range/labels/metadata/series 패스스루 — `execute_sql` 기본 숨김 전례(`expose_raw_promql=true`). Prometheus HTTP API는 태생 읽기 전용이라 SQL형 검증 계층 불요, 쿼리 timeout은 서버가 강제.
- **설정·감사**: `PROMETHEUS_URL`·인증 헤더는 서버 설정에만 존재(소비자 미보유), 도구 호출 감사 로깅은 폴스타 도구와 동일 파이프(§6-5). 전송 인증(§6-4)도 동일 적용.
- **구현·검증 wave**: Plan 06 R-B(= Plan 66 2-B′) 관할 — 코드 소재는 본 서버지만 시퀀스·품질 게이트(내장 toolset 대비 열화 없음 실측·열화 시 A안 복귀)는 그쪽에 있다.

## 5. db_id 라우팅과 방언 (원본 계약 계승)

- **3중 일치 체크리스트**: MCP `source` 인자 = 이벤트 `db_id` = `config.toml [[sources]].name` = 서버 `.env {NAME_UPPER}_CONNECTION` prefix. 하나라도 어긋나면 "알 수 없는 소스" — 기동 시 검증 로그로 노출한다.
- 소스 정의: `polestar_cm_gp`(postgresql)·`polestar_cm_yd`(postgresql)·`polestar_b0`(db2) + 로컬 개발용 `polestar`(postgresql, Docker 픽스처).
- **방언 분기는 전부 서버 내부**: 스키마 한정(`polestar.` vs `POLESTAR.`), LIMIT vs FETCH FIRST, 집계 전 `CAST(... AS DECIMAL)`(`::numeric` 금지), DB2 칼럼 소문자 정규화. 소비자(게이트·HolmesGPT)는 방언을 모른다.
- 다홉 토폴로지·변경 이력 등 PG 전용 기능은 서버가 b0에 대해 **축소 결과+사유**를 반환(침묵 강등 금지).

## 6. 보안 (D-015 예약)

1. **읽기 전용 이중 방어**: 서버 `validate_readonly`(이식) + 고수준 도구는 애초에 SQL을 받지 않음(값 인자만). `execute_sql` 옵트인 시에만 도메인 deny 검증 추가.
2. **이스케이프 계약**: MCP는 파라미터 바인딩이 없다(원본 실측 — `{source, sql}` 뿐). 고수준 도구의 값 인자는 서버가 `_sql_literal`(널바이트 제거 + `'` 이중화)로 보간한다. 이 계약을 단위 테스트로 고정.
3. **마스킹**: 프로세스 `args`의 접속문자열·`--password`·토큰 마스킹을 서버 측 강제. `/proc/*/environ`류 미수집.
4. **전송 인증**: 원본은 SSE 무인증(네트워크 격리 전제). SREAgent 운영 연동 전 보강 — 1차: 네트워크 ACL + 정적 Bearer 토큰 헤더 검증(FastMCP/Starlette 미들웨어. holmesgpt `MCPConfig.headers`가 Authorization 헤더 전달을 지원함을 실측 확인, 게이트 클라이언트도 동일 헤더 전송). mTLS는 인프라 확정 후 후속.
5. **감사**: 서버가 실행 SQL·도구 호출·소요시간·행수·오류를 파일 로깅(원본 sql_file_logger 패턴 이식).

## 7. 소비자 연동

### 7.1 노이즈 게이트 결정적 클라이언트 — **폐기(통합 갱신, Plan 01 대체)**

`src/sre_agent/infrastructure/polestar_mcp_client.py` (infrastructure 계층, `MODULE_LAYER_MAP` 등록):

- `mcp` SDK 직접 사용(원본 패턴): `sse_client(url)` → `ClientSession` → `call_tool("polestar_noise_signals", {...})`, `asyncio.wait_for(noise_context_timeout_seconds)`, 재연결 최대 3회(2s·4s·6s).
- **미등록 db_id는 호출 전 선확인**(`list_sources` 캐시) — 빈 결과와 "소스 없음"을 구분해야 "이력 0건=첫 발생" 오판을 막는다(원본 주석 경고 계승).
- 서버 다운·타임아웃 → `NoiseContext(source="unavailable")` 반환(게이트가 보수화 처리). 침묵 폴백 금지 — warning 로그 + 감사 표기.

### 7.2 HolmesGPT (Plan 02 — `Config.mcp_servers` 등록, 실측 근거)

holmesgpt 0.36.0 실측: `mcp_servers` 항목은 `type=mcp`로 스탬프되어 toolset dict에 병합 → `RemoteMCPToolset`이 `list_tools`로 도구 자동 발견, SSE/streamable-http/stdio 지원, 동일 서버 호출 직렬화, `MCP_TOOL_CALL_TIMEOUT_SEC` 환경변수 타임아웃, `health_check_tool` 지정 가능.

```python
mcp_servers = {
    "polestar": {
        "config": {
            "mode": "sse",
            "url": "http://<mcp-host>:9099/sse",
            "headers": {"Authorization": "Bearer <token>"},   # §6-4
        },
        "health_check_tool": "list_sources",   # 인자 없는 읽기 전용 도구
        "llm_instructions": "<폴스타 도메인 지침 — Plan 02 §5.3>",
    },
}
Config(..., mcp_servers=mcp_servers)
```

- `llm_instructions` 등 Toolset 필드가 dict로 전달되는 경로는 확인했으나 **착수 시 실 런타임 반영을 실측**한다(프롬프트에 실제 포함되는지 — mock 통과 ≠ 프로덕션 원칙).
- 도구 이름 충돌 시 holmesgpt가 `polestar__<tool>` prefix를 자동 부여함(실측) — 도구명은 이미 `polestar_` prefix라 충돌 여지 낮음.

## 8. 설정

- **서버**: `mcp_server/config.toml`(host/port/transport, `[[sources]]` name·type·readonly·query_timeout·max_rows) + 서버 `.env`(`POLESTAR_CM_GP_CONNECTION` 등 — 빈 값 소스 자동 비활성, 인라인 주석 금지).
- **클라이언트(`sre_agent/` 패키지)**: `AgentSettings`에 `polestar_mcp_url`, `polestar_mcp_token`(SecretStr), `active_db_ids`(JSON 배열), `mcp_call_timeout_seconds`. pydantic 필드로만 판정(os.getenv 금지). (구 `NoiseGateConfig`는 Plan 01 대체로 불요)

## 9. 구현 순서·테스트·수용 기준

| Wave | 내용 |
|---|---|
| **M-A** | (통합 갱신 — 이식 불요) 기존 `mcp_server` 로컬 PG 픽스처 기동·기존 도구 회귀 확인 |
| **M-B** | 고수준 도구 8종 + 프로세스 프록시(마스킹) + 도메인 deny (`polestar_noise_signals`는 폐기) |
| **M-C** | HolmesGPT `mcp_servers` 연동(§7.2) — Plan 02/06 착수 지점과 합류 (§7.1 게이트 클라이언트는 폐기) |
| **M-D** | 전송 인증(§6-4) + 실 폴스타 인스턴스 런타임 검증 |

※ (D-119) PromQL 도구(§4.4)의 구현·검증은 Plan 06 R-B 시퀀스 관할(코드는 본 서버에 추가) — M-D의 전송 인증은 PromQL 도구에도 동일 적용된다.

- **단위**: `validate_readonly`(우회 시도 픽스처: 주석·리터럴 내 키워드·다중문·세미콜론), 도메인 deny(금지 조인 SQL), `_sql_literal` 이스케이프, 방언 SQL 생성(엔진별 스키마 한정·행 제한), 마스킹.
- **통합**: 로컬 Docker PG 픽스처(폴스타 스키마 서브셋 — collectorinfra `testdata/pg` 참조)에 대해 고수준 도구 end-to-end. PromQL 도구(§4.4)는 **Docker Prometheus 픽스처(`testdata/prometheus/` — Plan 06 §8.1**: prometheus+node_exporter+mock_exporter, nodename 라벨=PG 픽스처 server_name 정렬)로 동일하게 검증. HolmesGPT `RemoteMCPToolset` 도구 발견은 `PrerequisiteCacheMode.DISABLED`로 검증(캐시 히트 시 파싱 생략 함정 — 기실측).
- **수용 기준**: ① 소비자 코드(src/)에 DB 드라이버 의존성 0, ② 읽기 전용 위반 SQL이 서버에서 차단되고 `{"error":...}`로 반환, ③ 게이트가 MCP 서버 다운 상태에서도 보수적 PAGE로 동작(가용성 비의존), ④ HolmesGPT가 폴스타 도구를 자동 발견·호출(도구 목록 실측 로그), ⑤ **원본에서 미완이던 실 DB 런타임 검증(PG·DB2 각 1회 이상) 완료** — mock 통과를 완료로 치지 않는다, ⑥ `arch_check --ci` 통과(신규 클라이언트 모듈 등록).
