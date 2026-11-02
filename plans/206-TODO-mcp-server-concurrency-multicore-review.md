# 206. MCP 서버(`mcp_server`) 동시성 — 멀티코어 전환 검토 결과 · DB2 연결 풀 · 측정 판정 · 소스별 프로세스 분리

> **작성일**: 2026-11-02 · v3(W1 DB2 연결 풀 기본 on — 사용자 결정) · v2(W1을 확정 Wave로 — 사용자 지시)
> **상태**: **TODO** — 코드 0건 · W1(DB2 연결 풀 · 기본 on)은 확정 · W2·W3는 W0(폐쇄망 측정) 판정으로 착수 여부를 정한다 · 결정 D-357
> **계획 시작 SHA**: `16ea44fa`
> **관련 결정**: **D-357(이 계획 · W1 기본 on — D-162 예외)** · D-347 ③(소스별 동시 상한 = 풀 크기 · DB2 4 — 유지) · D-356 ④(다중 워커 미채택 — 같은 근거) · D-162(신규 플래그 기본 off) · D-139(패키지 경계 · `mcp_server` 양방향 import 0) · D-181(`mcp<2`) · D-003(읽기 전용) · D-303(회귀)
> **회귀 대상 파일**: W1 `mcp_server/mcp_server/db.py` · `config.py` · `config_admin_adapter.py` · `mcp_server/config.toml` 주석 / W2 `src/config/`(DBHub 설정) · `src/routing/db_registry.py` · `src/dbhub/client.py` · `sre_agent/sre_agent/settings.py` · `sre_agent/sre_agent/infrastructure/mcp_tool_client.py` · `.env.example`

---

## 1. 배경 · 실측 (2026-11-02)

사용자 제기: *"mcp agent를 멀티 코어 프로세스로 구현하여 동시성을 높일 수 있는지 검토하라."* 같은 날 APM 게이트웨이는 프로세스 풀을 채택했다(`plans/205` · D-356). 이 계획은 `mcp_server/`를 같은 기준으로 검토한 결과다.

### 1.1 현 구조

- 프로세스 1개 · 이벤트 루프 1개 — `__main__.py`가 SSE 앱을 `uvicorn.run`(워커 1)으로 서빙한다.
- DB 대기는 이미 동시다: PostgreSQL `asyncpg` 풀 · MariaDB `aiomysql` 풀(비동기) · DB2 `ibm_db`는 `asyncio.to_thread`(`db.py:249`).
- 동시 수를 묶는 것은 **의도된 상한**이다(D-347 ③): 소스별 `asyncio.Semaphore` = 풀 크기(기본 5) · DB2 `DB2_DEFAULT_MAX_CONCURRENT` 4(`config.py:193`) · 대기 초과는 `QueryQueueTimeoutError`.
- DB2는 **요청마다 `ibm_db.connect`**(`db.py:279`) — 접속 비용이 매 질의에 붙는다.
- 호출자 3곳이 SSE 세션으로 붙는다: `src/dbhub/client.py`(본체 SQL) · `src/clients/source_mcp_client.py`(호출마다 세션) · `sre_agent/.../mcp_tool_client.py`(조사 · `POLESTAR_MCP_URL` 1개).

### 1.2 이벤트 루프 위 CPU 비용 (맥북 실측 · 합성 입력 · 최소값 5회)

| 구간 | 비용 |
|---|---:|
| 행 정규화(`_normalize_row`) + `json.dumps` · 1,000행 × 12열(221KiB) | 4.9ms |
| 같음 · 10,000행 = `max_rows` 상한(2.2MiB) | 49ms |
| `validate_readonly`(1.7KB SQL) | 1.9ms |
| `parse_exposition` 0.0.4(197KiB · 3,000샘플) | 44ms |

요청 1건이 루프를 쥐는 시간은 최악 약 50ms(+ MCP 프레임워크 직렬화 — 미측정)다. APM 게이트웨이의 응답 1건 검사 600ms(`plans/205` §1)보다 한 자릿수 이상 작다. **코어 1개가 병목이라는 근거가 없다** — 지연의 후보는 DB 실행 시간·상한 대기·DB2 접속 비용이다.

측정 스크립트는 저장소에 넣지 않았다(세션 임시). W0에서 `mcp_server/scripts/bench_cpu.py`로 옮겨 재현 가능하게 한다.

### 1.3 채택하지 않는 것

| 안 | 기각 사유 |
|---|---|
| uvicorn 다중 워커 | ① SSE 세션이 프로세스 메모리 — `GET /sse`와 `POST /messages?session_id=`가 다른 워커로 가면 끊긴다 ② 소스별 상한이 워커 수만큼 곱해져 조회 대상 부하가 N배(D-347 ③ 위반) — 상한을 N으로 나누면 이득 0 ③ D-356 ④와 같은 근거 |
| stateless streamable-http 전환 후 다중 워커 | 설치된 mcp 1.29.1은 `streamable_http_app`·`stateless_http`를 지원한다(실측). 그러나 호출자 3곳 + HolmesGPT 도구 설정을 모두 바꿔야 하고, ②(상한 곱셈)는 그대로 남는다 |
| 요청 처리 전체를 프로세스 풀로 | 위 1.2 비용으로는 pickle 왕복·워커 RSS 대비 이득이 없다 — 큰 결과 한정 안은 §3 W3(보류) |

## 2. 설계 원칙

1. **측정 먼저** — W2·W3는 W0 판정 없이 착수하지 않는다. W1(DB2 연결 풀)은 엔진 간 정비라 판정과 독립이다(착수 전 드라이버 실측만 필요).
2. **D-347 ③ 상한 불변** — 어떤 안도 소스별 동시 상한의 합을 늘리지 않는다. 소스 하나는 프로세스 하나가 맡는다.
3. **신규 플래그 기본 off = 현행 비트 동일**(D-162). 예외: W1 DB2 연결 풀은 **기본 on**(사용자 결정 2026-11-02 · D-357) — 되돌리기 값 `pool_enabled=false`가 현행과 비트 동일하다. W2 맵은 기본 빈 값(현행).
4. **패키지 경계 불변**(D-139) — `mcp_server` ↔ `src`·`sre_agent` import 0. W2는 설정·배선만 바꾼다.

## 3. Wave

### W0 — 폐쇄망 측정 · 판정 (코드 0 · 측정 스크립트만)

- 수집 항목(운영 부하 시간대 1회 이상):
  ① `mcp_server` 로그의 `조회 대기 초과` WARNING 건수(소스별) — 많으면 **상한 대기**가 원인
  ② `logs/sql/`의 `execution_time_ms` 분포(소스별 p50·p95) — 크면 **DB 실행**이 원인
  ③ 프로세스 CPU 사용률(`top -p <pid>` · 1초 간격) — 100%에 붙어 있으면 **코어 포화**
  ④ DB2 접속 1회 비용 — `ibm_db.connect` 단독 소요(10회 평균) vs 같은 SQL 실행 소요
  ⑤ 호출자 측 도구 왕복(`src` 로그 · sre_agent 도구 감사) − ② = 전송·직렬화·대기 몫
- 판정표:

| 관측 | 원인 | 다음 |
|---|---|---|
| ④가 ②의 p50 대비 유의(예: 30% 이상) | DB2 접속 비용 | W1(기본 on) 효과 확인 — 배포 전후 비교 |
| ③ 포화 ∧ ① 적음 | 코어 포화 | W2 |
| ① 많음 | 상한 대기(설계상 보호) | 상한 조정은 D-347 개정 사안 — 사용자 결정 |
| ② 큼 | DB 쿼리 자체 | 이 계획 밖(SQL·인덱스) |

- 산출: §5 측정표 · 판정 · `mcp_server/scripts/bench_cpu.py`(§1.2 재현 — 개발용).

### W1 — DB2 연결 풀 (확정 · W0과 독립)

PostgreSQL(`asyncpg.create_pool`)·MariaDB(`aiomysql.create_pool`)는 이미 풀이고 DB2만 요청마다 `connect`/`close`한다(`db.py:277-295`). 세 엔진을 같은 풀 방식으로 맞추는 정비라 W0 판정을 기다리지 않는다. W0 ④는 착수 판정이 아니라 **전후 효과 측정**으로 쓴다.

- **풀 구현(`db.py`)**: 소스별 `_Db2Pool` — 동기 `queue.LifoQueue`에 유휴 연결을 담고 `to_thread` 안에서 꺼내 쓴다(연결 1개를 두 스레드가 동시에 쓰지 않는다 — 꺼낸 스레드만 쓰고 돌려놓는다).
  - 최대 크기 = `effective_max_concurrent()`(DB2 기본 4) — 소스 세마포어가 먼저 걸리므로 풀 대기는 생기지 않고, 연결 수 상한이 D-347 ③ 상한과 같다.
  - 지연 생성: 기동 시 연결하지 않는다(현행 「DB2 소스 등록: 요청별 연결」과 같은 기동 동작 — DB2가 내려가 있어도 서버 기동 불변).
  - 꺼낼 때 검사: `ibm_db.active(conn)` 거짓 또는 유휴 `pool_idle_seconds`(기본 300) 초과면 닫고 새로 연결한다.
  - 실행 중 예외: 그 연결은 돌려놓지 않고 닫는다(통신 장애·세션 상태 오염 차단). 문장 핸들은 매 실행 뒤 `ibm_db.free_stmt`로 푼다.
  - 종료: `close_all`이 유휴 연결을 모두 닫는다(대여 중 연결은 반납 시 닫힘 표시로 닫는다).
- **설정(`config.py`)**: 소스 키 `pool_enabled`(DB2에만 의미 · 기본 **true** · D-357 — `false` = 현행 요청별 연결로 되돌림) · `pool_idle_seconds`(기본 300). 기존 `pool_min_size`·`pool_max_size`는 DB2에 쓰지 않는다 — DB2 연결 수는 동시 상한이 정한다(`pool_max_size`를 쓰면 기본 5가 되어 상한 4와 어긋난다). 설정 관리 키 목록(`config_admin_adapter.py`) · `config.toml` 주석 등재.
  - 기본 on은 사용자 결정(2026-11-02 「기본값을 처음부터 켜라」 · D-162 예외 — D-356 전례). 기존 `config.toml`의 DB2 소스는 키가 없으므로 배포 즉시 풀로 동작한다 — 폐쇄망 첫 배포 때 W0 ④ 전후 비교와 연결 수(DB2 `LIST APPLICATIONS` 등 DBA 확인)를 함께 본다. 이상 시 소스별 `pool_enabled = false`로 되돌린다.
- **착수 전 폐쇄망 실측(필수)** — 로컬 venv에 `ibm_db`가 없다(plans/163 W0 · plans/194 W4a와 같은 제약):
  ① 연결 핸들을 다른 스레드에서 차례로 쓸 수 있는지(기본 executor 스레드가 매번 다르다) ② `ibm_db.active`·`free_stmt` 시그니처(`inspect`) ③ `ibm_db.pconnect`(드라이버 영속 연결)의 동작 — 스레드 간 공유 여부를 확인해 직접 풀보다 단순하면 그것을 고른다 ④ 연결 1회 비용(W0 ④와 같은 값).
- 문장 타임아웃 `SQL_ATTR_QUERY_TIMEOUT`은 문장 옵션이라 재사용과 무관 — 현행 유지. DB2 세션 읽기 전용 속성(plans/163 W0 미적용)은 풀이 생기면 연결당 1회 걸 수 있으나 **이 계획 밖**이다(보안 동작 변경 — 별도 계획 · D-337 ⑥).
- **테스트**(가짜 `ibm_db` 모듈 주입): ⓪ 키 미기재 = on(기본값 단언) ① `pool_enabled=false`면 connect/close 호출 순서·횟수가 현행과 같음 ② on이면 동시 N ≤ 상한에서 누적 connect 횟수 ≤ 상한 · 순차 반복은 connect 1회 ③ 실행 예외 연결은 반납되지 않고 닫힘 ④ `active` 거짓·유휴 초과 연결은 재연결 ⑤ `close_all` 후 유휴 0 · 대여 중 연결은 반납 시 닫힘 ⑥ 행 결과(소문자 키·정규화)가 off와 같음 ⑦ 세마포어 대기 초과(`QueryQueueTimeoutError`) 동작 불변.

### W2 — 소스별 프로세스 분리 (W0 ③ 판정 시)

- **`mcp_server` 코드 변경 0** — 운영 배치만 바꾼다: 소스마다 `config.toml`을 따로 두고(그 소스의 `[[sources]]`만) 포트를 달리해 프로세스를 여럿 띄운다. 소스 하나 = 프로세스 하나라 상한 합은 그대로다(D-347 ③).
- 본체 배선: DBHub 설정에 `source_urls`(`DBHUB_SOURCE_URLS` · JSON `{"polestar_b0": "http://…:9101/sse", …}` · 기본 빈 값 = 전부 `DBHUB_SERVER_URL` — 비트 동일). `db_registry.py:136`의 `model_copy(update={"source_name": db_id})`에 `server_url`을 함께 갈아 끼운다. `DBHubClient`의 기본 클라이언트 경로(`src/db/__init__.py:54`)도 같은 해석을 쓴다.
- sre_agent 배선: `POLESTAR_MCP_URL` 1개 → 소스별 URL 맵(`POLESTAR_MCP_SOURCE_URLS` · 기본 빈 값 = 현행). 도구 인자의 소스로 URL을 고른다. 소스 인자가 없는 도구(PromQL·OpenMetrics·소스 목록)는 기본 URL로 간다.
- 소스와 무관한 도구(PromQL·OpenMetrics·폴스타 브리지 `/metrics`)는 **한 프로세스에만** 켠다(브리지 캐시·스크레이프 중복 방지) — 운영 절차에 명시.
- **테스트**: ① 맵 비면 모든 db_id가 `DBHUB_SERVER_URL`(현행) ② 맵에 있는 db_id만 그 URL ③ `bearer_token` 등 나머지 필드 보존(plans/104 C-5 사례) ④ sre_agent 소스별 라우팅 · 소스 없는 도구 기본 URL ⑤ `.env` JSON 형식(인라인 주석 금지) — `test_env_example_coverage` 갱신.
- 매뉴얼(D-255): 관리자 배치 절차가 바뀌므로 `admin` 매뉴얼 해당 절 갱신 여부를 W2 착수 시 판단한다.

### W3 — (보류) 큰 결과 직렬화 프로세스 풀

W0 ③이 포화이고 그 원인이 큰 결과(수천 행 이상) 직렬화로 확인될 때만 연다. `execute_sql`류 결과 행 수가 임계(예: 5,000행) 이상이면 정규화+`json.dumps`를 spawn 프로세스 풀에서 한다(`plans/205` W1 `ParsePool` 형태 참고 — 코드 공유는 하지 않는다, D-139). 지금 수치(최악 49ms)로는 착수하지 않는다.

## 4. 회귀

- 각 Wave: implementer가 자기 테스트(`mcp_server`는 `cd mcp_server && ../.venv/bin/python -m pytest tests/<파일>` · sre_agent는 자체 `.venv`) + `python scripts/regress.py --no-tests --files <바꾼 파일…>`.
- 계획 끝: team-lead가 `python scripts/regress.py --base 16ea44fa --files <계획이 바꾼 파일…>` + 바꾼 독립 패키지의 패키지 테스트. W2는 `src/config/`를 바꾸므로 `[웨이브 회귀 필요]` 대상이다. 전체 회귀는 사용자 요청 시만.

## 5. W0 측정표

(폐쇄망 측정 후 채운다)

## 6. 운영 절차 (W2 채택 시)

(소스별 `config.toml` 예시 · 포트 배정 · 기동 순서 · 소스 무관 도구 배치 · 되돌리기 = 맵 비우기)

## 7. 결정 등재

**D-357**로 등재했다(2026-11-02) — §1.3 기각 사유 · W1 DB2 연결 풀 기본 on(D-162 예외). W2 채택 여부는 W0 판정 뒤 D-357에 부기한다.

## 8. 결과

(완료 시 기록)
