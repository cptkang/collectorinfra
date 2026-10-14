# 163. 보안 강화 분리 건 — mcp_server 읽기 전용 단일 지점 · 사용자 토큰 `type` 검증 · 예외 문자열 비노출 · LLM 클라이언트 env 폴백

> **작성일**: 2026-10-14 · **v1.4**(후속 교정 — auditor 최종 판정의 판별기 미경유 노출 6건·부작용 함수 7종 · §5) · v1.3(W1 완료 — S1·S1+·S2·S3·S3+·X6·X6+ 구현 · D1~D6 판정(D2 의도 · 나머지 결함 수정) · 단계 1 스냅샷 4종 재채록 · v1.2: 단계 1 끝 사용자 확정 §2.1a)
> **상태**: **완료(2026-10-14 · 단계 1·2 일괄 커밋)** — W0(단계 1) **완료** · **W1(단계 2) 구현·검증 완료(2026-10-14)** — verifier·security-auditor 1라운드 Critical 0 · 교정 4차 반영 · 계획 끝 회귀 exit 1(이번 변경 3건은 교정 · 원래 실패 2건) → 교정분 모듈 선택 회귀 exit 0 · v1.4 후속 교정(커밋 뒤 · 미커밋) 회귀 exit 0. **남은 것: 폐쇄망 확인 항목(§5 v1.3)뿐 — 코드 잔여 없음.** 4건(S1·S2·S3·X6). `plans/162` 분석에서 나온 **행동이 바뀌는 보안 항목**을 구조 리팩터와 분리한 계획(U-6 확정 · D-330 ⑥). **진행 위치(`plans/162` §0.1 단계표가 정본)**: W0 = **단계 1**(162 W0과 병행) → W1 = **단계 2**(단계 1 전부 종료 뒤 · 162 W1 착수 전에 끝낸다).
> **요청 근거(2026-10-14)**: 사용자 지시 「전체적인 코드와 계획에 맞게 결정사항은 권고에 맞게 정리하라」 → `plans/162` §7 U-6 권고 ① 「별도 보안 계획(security-auditor 동반)」 채택.
> **성격**: **행동 변경**(차단·응답 문구·기동 실패 조건이 바뀐다). `plans/162`의 동작 불변 게이트(스냅샷 무갱신)를 적용하지 않는다 — 대신 건마다 「바뀌는 동작」을 성공 기준에 적고 그 동작을 단언하는 테스트를 새로 둔다.
> **관련 결정**: D-003(3중 읽기 전용 방어 — 절대 변경 불가 · S1은 **강화** 방향) · D-070(사용자/운영자 JWT 시크릿 분리) · D-330(행동 변경 분리 원칙) · D-303(회귀 정책) · D-255(매뉴얼 동반 — S3 문구 변경이 화면에 보이면 대상 여부 판단) · D-139·D-274(패키지 경계 — `mcp_server`는 양방향 import 0)
> **상위·인접 계획**: `plans/162`(구조 리팩터 — §3.8에서 이관 · **같은 파일을 고치면 한쪽을 먼저 끝낸다**: S1 ↔ 162 M5(`mcp_server` 분할), S3 ↔ 162 A1·A3(`query.py` 스트림 공통화), S2 ↔ 162 A5(`type` 검증 공용 함수), X6은 162에서 뺐다)
> **적용 스킬**: `.claude/skills/secure-coding/` (D-332) — 위임 프롬프트에 「`secure-coding` 스킬을 따른다」를 적는다.
> **분석 기준 SHA**: `7ba5c96`. 아래 `file:line`은 2026-10-14 이 시점 재확인 값이다.
> **계획 시작 SHA**: `08863d6`(브랜치 RCA · W0 착수 2026-10-14) · **회귀 대상 파일**: `mcp_server/tests/test_readonly_fixed_sql.py`(W0) · W1: `mcp_server/mcp_server/db.py` `mcp_server/mcp_server/polestar_tools.py` `mcp_server/mcp_server/security.py` `mcp_server/mcp_server/tools.py` `mcp_server/tests/test_db_mariadb.py` `mcp_server/tests/test_mariadb_integration.py` `mcp_server/tests/test_plan163_s1_readonly_hardening.py` `mcp_server/tests/test_plan163_w1_verify_readonly.py` `noise_gate/tests/test_agentic_enricher_gemini_live.py` `src/api/dependencies.py` `src/api/error_detail.py` `src/api/middleware/audit_middleware.py` `src/api/routes/admin.py` `src/api/routes/alarm.py` `src/api/routes/apm_jobs.py` `src/api/routes/conversation.py` `src/api/routes/query.py` `src/api/routes/schema_cache.py` `src/api/server.py` `src/api/settings_catalog.py` `src/api/stream_failure.py` `src/api/thread_history.py` `src/clients/fabrix_client.py` `src/clients/ollama_client.py` `src/config.py` `src/graph.py` `src/llm.py` `src/nodes/cache_management.py` `src/nodes/multi_db_executor.py` `src/nodes/output_generator.py` `src/nodes/query_validator.py` `src/orchestration/apm_jobs.py` `src/orchestration/apm_query.py` `src/orchestration/replanner.py` `src/orchestration/result_aggregator.py` `src/orchestration/task_progress.py` `src/schema_cache/cache_manager.py` `src/schema_cache/redis_cache.py` `src/static/js/admin.js` `src/static/js/app.js` `src/utils/user_error.py` `tests/test_api/test_noise_dashboard_api.py` `tests/test_api/test_plan104_settings_readiness.py` `tests/test_api/test_plan122_t4_time_resolution_route.py` `tests/test_api/test_plan134_w0b_verify.py` `tests/test_api/test_plan163_d_track.py` `tests/test_api/test_plan163_s2_user_token_type.py` `tests/test_api/test_plan163_s3_admin_error_detail.py` `tests/test_api/test_plan163_s3_user_errors.py` `tests/test_api/test_plan163_w1_verify_s3_d6.py` `tests/test_api/test_query_stream_progress.py` `tests/test_clients/test_plan163_x6_env_fallback.py` `tests/test_dbhub_integration.py` `tests/test_graph_extended.py` `tests/test_llm_gemini.py` `tests/test_orchestration/test_plan119_replanner.py` `tests/test_orchestration/test_plan119_sql_loop.py` `tests/test_orchestration/test_plan121_result_writing.py` `tests/test_orchestration/test_plan123_disclosures.py` `tests/test_orchestration/test_plan134_w0b_verify.py` `tests/test_orchestration/test_plan163_d1_tier_exclusive_nodes.py` `tests/test_orchestration/test_task_progress.py` `tests/test_pipeline.py` `tests/test_schema_cache/test_plan163_d3_d4_cache_invalidation.py` `tests/test_scope_select/test_v7_audit_and_estimate.py` `tests/test_scripts/test_bench_plan114_gates.py`

---

## 0. 요약

| ID | 문제 | 지금 동작 | 바뀌는 동작 | 위험 |
|---|---|---|---|---|
| **S1** | `mcp_server` 읽기 전용 강제가 한 지점이 아니다 | `validate_readonly`는 `execute_sql` 도구가 readonly 소스를 받을 때만 실행(`tools.py:119-122`). 폴스타 도구·스키마 헬퍼·`polestar_exporter.py`는 `pool.execute`(`db.py:90`)로 바로 간다. 세션 수준 읽기 전용 설정이 없다(PG `db.py:44-49` · MariaDB는 `autocommit=True`·`max_statement_time`만 `db.py:71-75` · DB2 `ibm_db.connect` `db.py:172`) | 모든 SQL이 실행 전 한 지점(`PoolManager.execute` 또는 그 앞)에서 읽기 전용 검사를 받고, PG(가능하면 MariaDB·DB2도) 세션이 읽기 전용으로 열린다 | 중 — 고정 SQL 빌더 출력이라 즉시 위험은 낮지만 D-003 (1) 「DB 계층 readonly」의 실체가 도구 하나에만 있다 |
| **S2** | 사용자 토큰 검증이 `type` 클레임을 보지 않는다 | `_verify_user_token`(`src/api/dependencies.py:31-53`)은 서명·만료·`sub`만 확인. 호출 3곳(`:80`·`:156`·`:249`). 발급 쪽은 `type: "user"`를 넣는다(`user_auth.py:46-56`) | `type != "user"`이면 401 | 낮음 — 시크릿 분리(D-070: `auth.jwt_secret` ↔ `admin.jwt_secret`)로 운영자 토큰은 이미 서명 단계에서 실패. 두 시크릿을 같은 값으로 설정한 환경에서만 교차 통과 가능 |
| **S3** | 내부 예외 문자열을 사용자 응답에 싣는다 | 아래 §3 표의 사용자 경로 6곳 · 관리자 경로 7곳 | 사용자 응답은 고정 문구 + 상관 ID(로그에서 원문 조회), 원문은 서버 로그에만 | 중 — 예외 문자열에 DSN·SQL·경로·외부 API 응답이 섞일 수 있다 |
| **X6** | LLM 클라이언트가 설정 대신 프로세스 env를 읽는다 | `fabrix_client.py:54-56`: `base_url or os.getenv("FABRIX_BASE_URL", "http://localhost:8002/v1")`(API_KEY·CHAT_MODEL 같은 방식) · `ollama_client.py:51-52`: `api_key or os.getenv("LLM_API_KEY")`, **`os.getenv("LLM_OLLAMA_BASE_URL", base_url)` — env가 인자보다 우선** | 클라이언트는 받은 인자만 쓴다. 비어 있으면 생성 시 `ValueError`(fail-fast) | 낮음~중 — `.env`는 `os.environ`에 주입되지 않으므로(Known Mistakes) 평시엔 무해하나, 셸 env가 남아 있으면 설정과 다른 엔드포인트로 조용히 붙는다. `fabrix_base_url` 기본값 `""`(`config.py:51`)이면 지금은 `localhost:8002`로 조용히 폴백 |

**한 계획에 묶는 이유**: 네 건 모두 「구조 리팩터의 스냅샷 불변 게이트로는 검증할 수 없는 동작 변경」이고 보안 감사 관점이 같다. 파일은 서로 겹치지 않아(§4) Wave 안에서 병렬로 진행할 수 있다.

---

## 1. 코드 근거 (2026-10-14 재확인)

### 1.1 S1 — `mcp_server` 읽기 전용

- `mcp_server/mcp_server/tools.py:119-122` — `execute_sql`에서 `if src_config.readonly: validate_readonly(sql)`. 저장소 안 `validate_readonly` 호출은 이 한 곳뿐(`security.py:68` 정의 · `tools.py:25` import · `:324` 주석).
- `mcp_server/mcp_server/db.py:90` `PoolManager.execute` — 소스 타입별 runner로 바로 넘긴다(검사 없음). `polestar_tools.py`·`polestar_exporter.py`·`tools.py`의 실행 호출 22곳이 여기로 모인다.
- 세션 설정: PG `asyncpg.create_pool(dsn, min_size, max_size, command_timeout)`(`db.py:44-49`) — `server_settings`·`default_transaction_read_only` 없음 / MariaDB `autocommit=True` · `init_command="SET SESSION max_statement_time=…"`(`db.py:71-75`) — 읽기 전용 없음 / DB2 `ibm_db.connect(conn_str, "", "")` + `exec_immediate`(`db.py:168-181`) — 읽기 전용 없음.
- `validate_readonly`가 고정 SQL의 `WITH`·`FETCH FIRST`·주석 등에 걸리는지는 **미실측** — 가드를 옮기는 순간 폴스타 도구가 차단될 수 있어 W0에서 먼저 잰다.

### 1.2 S2 — 사용자 토큰

- 검증: `src/api/dependencies.py:31-53` `_verify_user_token(token, secret)` — `jwt.decode(..., algorithms=["HS256"])` 후 `sub`만 확인.
- 호출: `dependencies.py:80`·`:156`·`:249`(모두 `config.auth.jwt_secret`).
- 발급: 사용자 `src/api/routes/user_auth.py:46-56`(`"type": "user"` · `auth.jwt_secret`) / 운영자 `src/api/routes/admin_auth.py:66-73`(`"type": "admin"` · `admin.jwt_secret`).
- 운영자 쪽은 이미 `type == "admin"`을 검사한다(`dependencies.py:113-116` break-glass · `admin_auth.py:94-97`). 사용자 쪽만 비대칭.
- 시크릿 분리: `src/config.py` `AuthConfig.jwt_secret`(`:621`) ↔ `AdminConfig.jwt_secret`(`:591`) — 둘 다 비면 각자 랜덤 생성. **두 값이 같게 설정됐는지 막는 가드는 없다**(grep 0건).

### 1.3 S3 — 예외 문자열 노출

| 경로 | 위치 | 형태 |
|---|---|---|
| 사용자 · 비스트림 질의 | `src/api/routes/query.py:2605` | `HTTPException(500, detail=f"처리 중 오류가 발생했습니다: {str(e)}")` |
| 사용자 · 파일 비스트림 질의 | `query.py:3357` | 같음 |
| 사용자 · SSE 질의 | `query.py:3181` | `_stream_error_payload(f"처리 중 오류가 발생했습니다: {str(e)}", …)` |
| 사용자 · 파일 SSE 질의 | `query.py:4007` | 같음 |
| 사용자 · APM 작업 API | `src/api/routes/apm_jobs.py:57` | `HTTPException(502, detail=f"작업 정보를 가져오지 못했습니다: {e}")` |
| 사용자 · 대화 세션 조회 | `src/api/routes/conversation.py:71` | `HTTPException(500, detail=str(e))` |
| 관리자 | `src/api/routes/admin.py:774`·`889`·`910`·`935`·`1108` | 설정 저장·로드·검증·그래프 재빌드·DB 설정 저장 실패에 `{e}` |
| 관리자 · 입력 오류 | `admin.py:1749`·`1798` | `400 날짜 형식 오류: {e}` — 입력 파싱 메시지 |

**대상이 아닌 것**(의도된 사용자 메시지): `apm_jobs.py:51·53·55`(도메인 예외 `JobNotFound`·`JobForbidden`·`JobConflict`의 메시지) · `result_viz.py:266·298`(`ValueError` 입력 검증 메시지). W0에서 이 예외들이 내부 문자열을 담지 않는지만 확인한다.

### 1.4 X6 — LLM 클라이언트 env 폴백

- `src/clients/fabrix_client.py:54-56` · `src/clients/ollama_client.py:51-52`(위 §0).
- 운영 호출부: `src/llm.py:336-342`(`LLMAPIClient(base_url=config.llm.ollama_base_url, chat_model=config.llm.model, api_key=config.llm.ollama_api_key or None, …)`) · `src/llm.py:477-482`(`FabriXAPIClient(base_url=config.llm.fabrix_base_url, chat_model=model, api_key=config.llm.fabrix_api_key, …)`) — **인자를 항상 넘긴다**. 복제 경로 `bind_tools`(`fabrix_client.py:263` · `ollama_client.py:292`)도 자기 값을 넘긴다.
- 그래도 행동이 바뀌는 경우: ① `fabrix_base_url`·`fabrix_api_key`·`fabrix_chat_model` 기본값이 `""`(`config.py:51-54`)라 빈 값이면 지금은 env → 하드코딩 기본값으로 간다 ② `ollama_client`는 셸 env `LLM_OLLAMA_BASE_URL`이 있으면 설정값을 덮는다(`.env`에도 같은 키가 있지만 pydantic이 읽은 값과 셸 env가 다를 때만 차이).
- `fabrix_client_key`가 있으면 `KBGenAIChat`로 가므로(`llm.py:455-471`) `FabriXAPIClient`는 OpenAI 호환 모드에서만 쓰인다.

---

## 2. Wave

### 2.0 공통 규약

1. **security-auditor를 검증 라운드에 함께 띄운다**(verifier와 같은 라운드 · 병렬).
2. 건마다 「바뀌는 동작」을 단언하는 테스트를 먼저 쓰고(실패 확인) 고친다.
3. D-003은 **강화만** 한다 — 쓰기 허용 방향의 변경은 어떤 이유로도 하지 않는다.
4. 회귀(D-303): Wave 중간은 자기 테스트 + `python scripts/regress.py --base <계획 시작 SHA> --no-tests --files <바꾼 파일…>`. 계획 끝(마지막 Wave)에서 1회 `regress.py --base <계획 시작 SHA> --files <누적…>`. `mcp_server` 테스트는 `cd mcp_server && ../.venv/bin/python -m pytest`. 전체 회귀는 사용자 요청 시만.
5. 실 LLM·과금 API 불필요. MCP 서버 실프로세스 확인이 필요하면 로컬 도커 PG(`testdata/pg/init`)로 한다. 로컬 MCP 9099·9097은 코드 변경 뒤 재기동해야 반영된다.

### 2.1 W0 — 실측 (제품 코드 0건)

| 산출 | 내용 |
|---|---|
| S1 고정 SQL 통과표 | 폴스타 도구·스키마 헬퍼·exporter의 모든 `build_*_sql`·고정 SQL을 `validate_readonly`에 넣어 통과/차단 목록을 만든다. **이 계획 W0이 단독 소유**(v1.1 — `plans/162` W0은 만들지 않는다). 차단이 0이면 「전부 통과」 단언 테스트로 남기고, 차단이 있으면 목록만 기록한다(W1 S1 입력) |
| S1 세션 읽기 전용 수단 | PG `server_settings={"default_transaction_read_only": "on"}` · MariaDB `SET SESSION TRANSACTION READ ONLY` · DB2 연결 속성(읽기 전용·`CURRENTSCHEMA` 등) 중 설치된 드라이버 실물로 되는 것을 `inspect.signature()`·로컬 도커로 실측 |
| S2 영향 | `type` 클레임이 없는 사용자 토큰을 발급하는 경로가 있는지(테스트 픽스처 포함) grep · 발급 경로가 1곳(`user_auth.py`)인지 확인 |
| S3 프런트 소비 | 웹 UI(`src/static`)가 오류 문구 원문을 파싱하거나 표시하는지 · 상관 ID를 이미 내려주는지(`query_id`·`trace`) |
| X6 영향 | `FABRIX_*`·`LLM_OLLAMA_BASE_URL`·`LLM_API_KEY`를 셸 env로 주는 운영 기동 스크립트·문서가 있는지(`scripts/`·`docs/`·`deploy` 계열 grep) |

- **성공 기준**: 실측표 기록(이 계획서 부록) · 제품 코드 diff 0.
- **W0 끝 사용자 확인(단계 1의 유일한 확인 지점)**: S3 관리자 경로 7곳의 문구 방침. 질문에 실측(관리자 화면이 오류 원문을 어떻게 보여 주는지)과 선택지별 결과를 표로 붙이고, 기본안 「예외 타입명 + 상관 ID, 원문은 서버 로그」을 맨 앞에 둔다. 답이 오기 전에는 W1의 S3 관리자 분만 보류하고 나머지 트랙은 진행한다.

#### W0 실측 부록 (2026-10-14 · HEAD `08863d6` · 제품 코드 diff 0 · verifier 1라운드 독립 재확인)

**S1-a 고정 SQL 통과표** — 빌더·헬퍼·상수 26종 · 147케이스(엔진 PG/DB2/MariaDB × 선택 인자 분기) **차단 0건**. 고정 테스트 `mcp_server/tests/test_readonly_fixed_sql.py`(148 passed · 커버리지 가드 1 포함). 실행 호출부 22곳 전수 역추적(polestar_tools 8 · exporter 3 · tools 11) — 누락 0. `db.py:204-221 health_check` 상수 3종은 `DBPoolManager.execute`(`db.py:23`·`:90` — 클래스명 정정: `PoolManager` 아님)를 거치지 않고 `_execute_*`를 직접 부른다. exporter는 자체 `DBPoolManager`(`polestar_exporter.py:463`)를 만든다 → 세션 RO는 `DBPoolManager.initialize` 안에 넣어야 두 풀에 다 걸린다. `polestar_tools` 빌더는 PG/DB2 2분기이고 MariaDB 소스는 PG 분기를 받는다(`_resolve_engine` `:689`).

**S1-b W0에서 새로 드러난 결함 (W1 S1 범위에 추가)**
- **[Critical] 스키마 헬퍼 SQL 주입**: `tools.py:380-515` `_pg_get_{columns,primary_keys,foreign_keys}`·`_db2_get_*`가 도구 인자 `table_name`을 `'{schema}'`·`'{bare}'`·`'{table_name.upper()}'`로 이스케이프 없이 보간한다(MariaDB만 `_mariadb_literal` `:528`). `get_table_schema`는 `@mcp.tool()` 무조건 등록(`:176`) — `expose_execute_sql=false` 배치에서도 열린다. verifier가 로컬 PG에서 `UNION SELECT current_user …` 주입이 `validate_readonly` 통과·실행됨을 SELECT만으로 재현. **§0의 「고정 SQL 빌더 출력이라 즉시 위험 낮음」 전제는 틀렸다** — 이 9개 헬퍼는 고정 SQL이 아니라 도구 입력이 들어가는 SQL이다(통과표의 해당 행은 무해 대표 인자 기준선일 뿐).
- **[Major] `validate_readonly`는 금지 목록 방식**이라 비-SELECT 다수를 통과시킨다: `SET TRANSACTION READ WRITE`·`SET SESSION CHARACTERISTICS … READ WRITE`·`SELECT set_config('default_transaction_read_only','off',false)`·`COPY (…) TO PROGRAM`·`COPY … TO STDOUT`·`SELECT … INTO`·`LOCK TABLE`·`DO $$`·`VACUUM`·`REINDEX`·`REFRESH MATERIALIZED VIEW`·`PREPARE`·`SET ROLE`·`BEGIN` · MariaDB `LOAD DATA INFILE`·`INSTALL PLUGIN`·`SET GLOBAL read_only=0`·`SELECT … INTO OUTFILE` · 부작용 함수(`pg_terminate_backend`·`pg_read_file`·`lo_import`·`nextval`·`dblink_exec`). 막히는 것은 `SELECT … FOR UPDATE`·다중 문장. 본체 `SQLGuard.is_safe_select`도 COPY/LOAD/SET을 통과(본체는 `src/sql_validation.py:875` 문장 유형 허용 목록이 막는다). 로컬 `mcp_server/config.toml:10 expose_execute_sql = true`.
- **[Minor]** `polestar_tools.py:83-90 _sql_literal`은 백슬래시 미처리(MariaDB 기본 sql_mode에서 탈출 가능 — PG 분기 SQL이라 itam에선 테이블 부재로 실행 전 실패, 잠재) · aiomysql은 항상 `CLIENT.MULTI_STATEMENTS`를 켠다(MariaDB 다중 문장 방어는 검사기 하나) · `tools.py:119-120` 검사가 소스 `readonly=false`로 꺼지는 스위치(`config.py:134·257`).

**S1-c 세션 읽기 전용 수단** (설치 드라이버 실물 · 로컬 도커 실연결 · 쓰기는 0행 시도 후 롤백)

| 엔진 | 드라이버(실측) | 수단 | 실연결 결과 | 되돌림 |
|---|---|---|---|---|
| PG | asyncpg 0.31.0 — `connect(server_settings=)` 있음 · `create_pool(**connect_kwargs)` 전달 | `server_settings={"default_transaction_read_only":"on"}` | **가능** — 쓰기 `ReadOnlySQLTransactionError`(25006) | 같은 트랜잭션 `SET TRANSACTION READ WRITE`로 쓰기 실행됨 · `set_config(…,false)`로 세션 해제 가능하나 풀 반납 시 `RESET ALL`로 `on` 원복 |
| MariaDB | aiomysql 0.3.2 — `connect(init_command=)` 있음(`create_pool`은 kwargs 전달) | `init_command="SET SESSION max_statement_time=…, SESSION transaction_read_only=1"`(현행 `max_statement_time`과 **한 문장 결합** 필요 · 실측 동작) | **가능** — 권한 있는 계정에서 1792(25006) 거부 | `SET SESSION TRANSACTION READ WRITE`로 쓰기 실행됨 |
| DB2 | `ibm_db` **루트 venv 미설치** | 연결 속성(`SQL_ATTR_ACCESS_MODE` 등) | **미실측** — 서버·드라이버 없음 | — |

- 접속 계정: 로컬 PG 3소스 `polestar_user` = **superuser**(INSERT/UPDATE/DELETE 권한) · 로컬 MariaDB `itam_ro` = `SELECT ON INST1.*`만(쓰기 1142 거부). 세션 RO는 보조 수단이다 — 되돌릴 수 있고, PG 부작용 함수·`COPY TO`는 RO 트랜잭션에서도 돈다. **본질은 SELECT 전용 계정**(운영 계정 권한은 미확인 — 폐쇄망 확인 필요).

**S2 영향** — 사용자 토큰 발급 제품 경로 `user_auth.py:56`(`_create_user_token`) **1곳**, `"type":"user"`는 파일 최초 커밋 `6617625`(2026-04-09)부터 존재 → 무효화될 구 토큰 없음. 테스트 `jwt.encode` 22곳/16파일 전수: `type` 없는 사용자 토큰으로 **통과를 기대하는 테스트 0건**(type 없는 토큰은 전부 거부 기대 — `test_plan144_w5_episode_feedback.py:140`·`test_noise_dashboard_api.py:1012`·`test_plan139_table_definitions_api.py:275`). `_verify_user_token` 직접 호출 테스트 0건.

**S3 프런트 소비**
- 오류 문구 파싱 0건 — 받은 `detail`/`message`를 그대로 토스트(`app.js:2671`·`:2884` `showError`)·실패 카드(`renderStreamFailure` `app.js:1583-1613`, `escapeHtml`)로 표시. 프런트 기본 문구는 `tests/test_api/test_ui_stream_status.py:183·195`가 단언.
- **상관 ID 없음**: `request_id`(uuid 8자)는 감사 미들웨어 `audit_middleware.py:39-53`의 state·structlog에만 있고 응답 헤더(`X-Request-ID`)·SSE error 페이로드(`query.py:391-415`)·500 detail 어디에도 안 나간다. `query_id`는 성공 응답·`done`에만. → W1 S3는 ID를 오류 응답에 **새로 실어야** 한다.
- **원문 노출 경로가 라우트 6곳보다 넓다**: SSE error의 `last_error`·`steps[].detail`(`stream_failure.py:76·104-107`) · 2단 기준 경로 답 본문 `result_aggregator.py:1414`(`결과 생성 중 오류가 발생했습니다: {e}`)·`:1425`(`{res['error']}`) · `apm_jobs` 409 `JobConflictError`(`src/orchestration/apm_jobs.py:463-467` 게이트웨이 `reason` 무제한 포함). `conversation.py:71` 엔드포인트는 프런트가 호출하지 않는다. `result_viz.py`의 `ValueError`·`JobNotFound/Forbidden`은 입력 에코·고정 문구 — 대상 아님 확인.
- **관리자 경로는 7곳보다 많다**: `admin.py:458`(준비도 `_error` → `admin.js:1301`) · `:620`(warnings → `admin.js:282`) · `:1168`(DB 연결 테스트 `details=str(e)` → `admin.js:1453-1457`) · `schema_cache.py:184·242` · `alarm.py:968·1054·2018·2075·2159`(알람 테스트 응답 `error` · 모두 `require_admin_user`). 기존 7곳 줄 번호는 HEAD에서 그대로. 표시: 설정 저장·리로드·DB 설정 저장은 `detail` 원문을 5초 오류 박스(`textContent`)로 · 감사 로그·통계 400(`:1749`·`:1798`)은 **화면에 표시하지 않음**(`admin.js:2077` 고정 문구 폴백 · `:2147` 조용히 return).
- 깨질 테스트 후보: `test_query_stream_progress.py:370`(`"DB 연결 끊김" in err["message"]` · 2파라미터) · `test_orchestration/test_plan121_result_writing.py:166`(aggregator 원문 단언) · aggregator·409 범위를 넓히면 `test_plan134_w0b_verify.py:392`. 하네스(`scripts/scenario/client.py:471`·`assertions.py:386`)는 문구가 아니라 HTTP 코드로 판정 → 영향 없음. `plans/162` W0 스냅샷은 SSE error 페이로드를 고정하지 않는다.

**X6 영향** — 셸 env로 `FABRIX_*`·`LLM_OLLAMA_BASE_URL`·`LLM_API_KEY`를 주는 기동 스크립트·systemd·compose·Dockerfile·Makefile **0건**(git ls-files 전수). 다만 **클라이언트 밖에 같은 폴백이 하나 더 있다**: `src/config.py:86-106 LLMConfig.model_post_init`이 접두 없는 `FABRIX_BASE_URL`·`FABRIX_API_KEY`·`FABRIX_CLIENT_KEY`·`FABRIX_CHAT_MODEL`·`GOOGLE_API_KEY`(과금)·`LLM_API_KEY`를 `os.getenv`로 읽는다(pydantic 키는 `LLM_` 접두 — 접두 없는 키는 셸 env로만 유효 · `docs/03_setup_guide.md:159·213`이 이를 안내). `fabrix_client.py:54-56` 폴백은 팩토리 경로에서 도달 불가(`llm.py:441-449`가 빈 값이면 먼저 `ValueError`). 문서 오기: `docs/03_setup_guide.md:848-856`·`docs/09_db_configuration_guide.md:405-409`·`.env.example:60`·`llm.py:442-448` 오류 메시지가 `.env`/`.encenv`에 접두 없는 `FABRIX_*`를 쓰라고 안내(실제로는 무시) · `src/api/settings_catalog.py:235` 주석도 사실과 다름. 기타 문서 `fabrix_proxy/POC_RUNBOOK.md:87-88`·`docs/23_plan66_mvp_test_guide.md:986`.

**W1 계획 수정 필요(W0 끝 사용자 확인 대상)** — ① S1에 스키마 헬퍼 주입 수정(리터럴 이스케이프 또는 파라미터 바인딩) + 주입 거부 테스트 추가 · §0 위험 판정 정정 ② S1 검사기를 공용 지점으로 옮기기 **전에** 문장 유형 허용 목록(SELECT·WITH…SELECT)으로 강화 — 「검사기를 좁히지 말 것」은 완화 금지 뜻이라 허용 목록화(강화)와 충돌하지 않음. W0 고정 SQL 147건은 모두 SELECT/WITH 시작 → 양립 예상(W1 테스트로 재확인) · `readonly=false` 소스 스위치 유지 여부 결정 · DB2 세션 RO는 미실측이라 계정 권한 의존으로 기록 · 운영 계정 SELECT 전용 확인을 운영 체크리스트로 ③ S3 범위: 사용자 경로 6곳 → +aggregator 2곳·SSE `last_error`·`steps[].detail`·409 reason, 관리자 7곳 → 약 14곳, 상관 ID를 오류 응답에 신설 ④ X6에 `config.py` `model_post_init` 폴백을 넣을지 결정 — 넣으면 `src/config.py` 변경(`[웨이브 회귀 필요]`)이고 §4 X6 파일 목록이 바뀐다 · 문서 오기 정정 동반.

### 2.1a 사용자 확정 (2026-10-14 · 단계 1 끝 — W1 범위)

W0 실측 뒤 사용자 확인 4건의 답이다. **아래가 §2.2 표보다 우선한다.**

| # | 질문 | 확정 | W1 반영 |
|---|---|---|---|
| 1 | W0이 새로 찾은 보안 항목을 W1에 넣을지 | **4건 모두 포함** | **S1+** 스키마 헬퍼 주입 수정(리터럴 이스케이프 또는 바인딩 · PG·DB2 · 주입 거부 테스트) · **S1** 검사기를 금지 목록 → 문장 유형 허용 목록(SELECT · WITH…SELECT)으로 강화한 **뒤** 공용 실행 지점으로 이동(D-003 강화 · DB2 세션 RO는 드라이버 부재로 계정 권한 의존이라고 기록 · 운영 계정 SELECT 전용 확인은 폐쇄망 확인 항목으로 기록) · **S3+** 노출 범위 확대(SSE `last_error`·`steps[].detail` · `result_aggregator.py:1414·1425` · APM 409) + 오류 응답에 요청 번호(감사 미들웨어 `request_id`) 신설 · **X6+** `src/config.py:86-106` `model_post_init`의 접두 없는 env 폴백(`FABRIX_*`·`GOOGLE_API_KEY`·`LLM_API_KEY`)도 제거 → `src/config.py` 변경이라 `[웨이브 회귀 필요]` |
| 2 | S3 관리자 화면 문구 | **A · 약 14곳 전부** — 「{작업} 실패했습니다 ({예외 종류} · 요청 {request_id}) — 상세는 서버 로그」, 원문은 서버 로그 | 기존 7곳 + `admin.py:458·620·1168` · `schema_cache.py:184·242` · `alarm.py:968·1054·2018·2075·2159`. `admin.py:910`(운영 필수 설정 누락 — 키 이름만)은 원문 유지. `:1749·:1798`은 화면 미표시라 문구만 같은 형식 |
| 3 | W0 테스트가 찾은 기존 동작 이상(리팩터 범위 밖) | **이 계획에 넣어 지금 고친다** | 아래 D 트랙. 보안 항목은 아니지만 「행동 변경」이라 162(동작 불변)가 아니라 이 계획에서 처리한다(D-330 ⑥과 정합) |
| 4 | 단계 1 결과 커밋 시점 | **단계 2 끝나고 함께** | 메인 세션이 단계 2 검토 뒤 일괄 커밋 |
| 5 | (W1 중 추가 · 2026-10-14) 멀티 DB 실패 각주·작업 카드 DB별 오류 — 예외 원문과 D-153 후속2 「LLM 산출 발췌」가 섞임 | **(a) 예외 원문만 머리말로 축약 · 발췌·고정 문구·마감 문구는 유지** | `db_error_text`(`src/utils/user_error.py`) — 존 각주(`output_generator`)·작업 카드(`_summarize_tasks`)·전 DB 실패 요약(`safe_error_text`) · D-153 부기 |

**D 트랙 — W0이 고정한 현행 동작 중 결함 후보** (출처: `plans/162` §5.2 「W0 실측 부록」 · 각 항목은 **먼저 의도인지 결정·커밋 이력으로 확인**하고, 결함이면 고치고, 의도면 근거를 이 표에 적고 고치지 않는다. **기존 결정과 충돌하면 고치지 말고 보고한다**(CLAUDE.md 의사결정 규칙))

| ID | 현행 | 기대 | 확인할 결정 |
|---|---|---|---|
| **D1** | 2·3단 플래그를 함께 켜면 2단 빌드에 3단 노드 6개(+`fault_diagnosis`)가 엣지 없이 등록된다(현 로컬 `.env` 구성) · 1단 빌드에 단일 DB 경로 노드 7개가 도달 불가로 등록된다 | 상위 단이 성립하면 하위 단 노드는 등록하지 않는다(CLAUDE.md 「배타성은 빌드 타임」) | D-225 · D-251 · D-161 · `docs/21_orchestration_ladder.md` — 1단 폴백·관리자 경로가 그 노드를 쓰는지 실측 |
| **D2** | 파일 질의 2경로(스트림·비스트림)가 그래프 출력 `zone_clarification`(후단 존 게이트)을 보지 않아 `completed`로 응답 | 텍스트 경로와 같은 역질문 응답 | 단일/멀티 대칭 D-066 |
| **D3** | `invalidate_all()`이 관리자 자산 키 `asset_drafts`·`ddl_comments`를 지운다(`ADMIN_ASSET_SUFFIXES` 누락) | 관리자 자산은 보존 | D-294 |
| **D4** | `invalidate(db_id)`가 `descriptions`는 지우고 `knowledge_seeded`는 남겨 지식 설명이 재시드되지 않는다 | 함께 지우거나 재시드 | 지식 시드 관련 결정 grep |
| **D5** | `_STREAM_KNOWN_NODES`에 `entity_locator`·`sequential_runner`가 없어 진행 이벤트가 빠진다 | 등록 노드면 진행 이벤트 송출 | — |
| **D6** | SSE·응답 형태 비대칭: `awaiting_approval`이 ainvoke 폴백 경로에 없음 · 파일 스트림은 `clarification`을 기간 되묻기 때만·`source_switch` 없음 · 처리 상한 초과 부분 결과 done 키 8개(`db_scope`·`executed_sql`·`turn_count` 등 누락) · 파일 사전 게이트 응답 `thread_id` null | 경로 간 같은 키 집합(값이 없으면 null) · `thread_id`는 텍스트 경로처럼 채움 | D-066 · 프런트 소비(`src/static`) 영향 확인 |

**D 트랙 판정 (W1 · 2026-10-14)**

| ID | 판정 | 근거 | 처리 |
|---|---|---|---|
| D1 | **결함** | 배제 조건 누락 커밋 `475960b`·`f91fd1b` · 노드 이름을 쓰는 런타임 경로 0건(`Command`·`goto`·`Send`·`update_state(as_node)` 없음 · 2단 서브에이전트는 노드 함수를 직접 호출) · D-225 ② 「빌드 타임 배타」 | `src/graph.py` `single_db_tail` 가드 — 1·2단 빌드에 3단 노드·단일 DB 꼬리 7개·`approval_gate` interrupt 미등록(2단의 도달 불가 꼬리도 포함 · 되돌림 1줄). 3·4단 빌드 비트 동일 |
| D2 | **의도 — 미수정** | 파일 라우트는 `zone_clarification_allowed=False`라 후단 존 게이트가 발동하지 않는다(D-143 후속2 「발동 = 대화형 채널, 텍스트 라우트만」 · 파일 경로는 후속1의 사전 게이트 `_file_zone_clarification_or_none` · 커밋 `a101fe7`) | 없음 |
| D3 | **결함** | D-227 ⑥ 「`invalidate_all`은 관리자 자산 키 보존」 · D-294 커밋 `d1f4b2a`가 두 키를 보존 목록에 넣지 않음 | `ADMIN_ASSET_SUFFIXES`에 `asset_drafts`·`ddl_comments` (D-294 부기) |
| D4 | **결함** | D-316 부기 ② — 설명만 지우고 적재 기록 집합을 남기면 정본 파일 설명이 다시 채워지지 않음 · `invalidate(db_id)` 호출부 3곳 모두 운영자 명시 경로 | `invalidate(db_id)`가 `knowledge_seeded`도 삭제 · 프로세스 적재 표시 discard/clear (D-316 부기) |
| D5 | **결함** | 그래프가 등록하는 옵트인 노드가 화이트리스트에서 빠짐(W0 정합 테스트가 차집합으로 고정) | `_STREAM_KNOWN_NODES`·`app.js` 라벨에 `entity_locator`(서버 소재 확인)·`sequential_runner`(순차 실행) |
| D6 | **결함** | D-066 대칭 · 프런트는 키를 참·거짓으로만 읽어 null 추가가 동작을 바꾸지 않음(확인) | ainvoke 폴백 done `awaiting_approval` · 파일 스트림 done `clarification`·`source_switch` 키 상시(파일 경로 `source_switch`는 항상 null — 소스 전환 칩이 양식 파일 없이 재질의하는 부작용 차단) · 처리 상한 부분 결과 done 기본 키(`_PARTIAL_DONE_DEFAULTS`) · 파일 사전 게이트 `thread_id` = 요청값 또는 `query_id`(화면이 이 턴을 대화로 기록 — 동작 변화) |

- 범위 밖(W0이 「의도로 보임」으로 분류): `decide_notification`의 심각도 0 보수적 PAGE · 비알람 필터의 심각도3 해소 SUPPRESS(§17.4) · 실효 심각도 0 플래핑 억제 · `classify_metric_field`의 평균/최대 없는 「CPU 사용률」. `_zone_label` 3벌 차이는 `plans/162` U-1대로 별건.
- **스냅샷 갱신**: D·S3가 `plans/162` 단계 1 스냅샷(`tests/snapshots/plan162_w0/*` 등)을 바꾸면 이 계획이 `UPDATE_PLAN162_SNAPSHOT=1`로 재채록하고, 바뀐 키·값과 사유를 §5에 적는다(162 §0.1 운영 규칙 4 — 단계 2만 허용).
- **매뉴얼(D-255)**: 관리자·사용자 화면 오류 문구가 바뀌므로 매뉴얼에 해당 문구·화면이 있는지 확인하고 있으면 갱신한다.

### 2.2 W1 — 구현 (4트랙 병렬 가능 · 단계 2)

**진행 순서**: ① 트랙마다 「바뀌는 동작」 단언 테스트를 먼저 쓰고 실패를 확인 → ② 구현 → ③ verifier + security-auditor 같은 라운드 병렬 검증 → ④ team-lead 계획 끝 회귀. 트랙(파일 겹침 0): S1(`mcp_server`) / S2(`dependencies.py`) / S3(`query.py`·`apm_jobs.py`·`conversation.py`·`admin.py`) / X6(`src/clients/`). S3가 `plans/162` 단계 1 스냅샷(SSE 오류 페이로드 등)에 걸리면 **이 계획이 그 스냅샷을 갱신**하고 사유를 §5에 적는다 — 스냅샷을 갱신할 수 있는 계획은 이 계획뿐이다(162 §0.1 운영 규칙 4).

| ID | 구현 | 성공 기준 |
|---|---|---|
| **S1** | ① 읽기 전용 검사를 `PoolManager.execute`(또는 그 바로 앞 공용 실행 함수) 한 곳으로 모은다 — readonly 소스면 모든 경로가 검사를 받는다. W0에서 차단되는 고정 SQL이 있으면 **검사기를 좁히지 말고** 그 SQL을 고치거나, 근거와 함께 검사기의 허용 구문을 최소로 넓힌다 ② W0에서 확인된 세션 읽기 전용 설정을 풀·연결 생성에 넣는다 | 쓰기 SQL(`INSERT`·`UPDATE`·`DELETE`·DDL)을 폴스타 도구 경로·exporter 경로로 흘려도 차단되는 테스트 · PG 세션에서 쓰기가 DB 오류로 거부되는 테스트(로컬 도커) · 기존 폴스타 도구 테스트 통과 · `overfit_check` 0(`mcp_server` 스캔 대상 — 독스트링 리터럴 주의) |
| **S2** | `_verify_user_token`에 `payload.get("type") != "user"` → 401 · 운영자 쪽과 같은 형태로 맞춤(공용 함수 추출은 `plans/162` A5와 조율 — 먼저 끝난 쪽 형태를 따른다) · 선택: 기동 시 `auth.jwt_secret == admin.jwt_secret`이면 경고 로그(명시 설정일 때만) | `type` 없음·`admin` 토큰 → 401 테스트 · 정상 사용자 토큰 통과 · 인증 테스트 전수 통과 |
| **S3** | 사용자 경로 6곳: 응답은 고정 문구 + 상관 ID(`query_id` 등 기존 값 재사용 우선), 원문은 `logger.exception`으로 서버 로그에만. 관리자 경로 약 14곳: **확정 A안**(§2.1a #2 — 예외 종류 이름 + 요청 번호, 원문은 서버 로그) · 사용자 경로 확대분(SSE 상세·aggregator·APM 409)과 요청 번호 신설은 §2.1a #1 | 사용자 경로에서 예외 원문이 응답·SSE 본문에 나오지 않는 테스트 · 로그에는 남는 테스트 · 웹 UI 오류 표시 확인 · 화면 문구가 바뀌면 매뉴얼 대상 여부 판단(D-255) |
| **X6** | 두 클라이언트에서 `os.getenv` 폴백 제거 · 필수 인자가 비면 생성 시 `ValueError`(어떤 설정 키가 비었는지 메시지에 명시) · `ollama_client`는 인자 우선 | 빈 `fabrix_base_url`로 생성 시 `ValueError` 테스트 · 셸 env를 줘도 인자가 쓰이는 테스트 · `create_llm` 경로 테스트 통과 · W0에서 env 의존 기동 경로가 나오면 그 경로를 설정 키로 옮긴 뒤 제거 |

- **계획 끝 회귀**(W1이 마지막 Wave): `python scripts/regress.py --base <계획 시작 SHA> --files <바꾼 파일…>` 1회 + `mcp_server` 자체 테스트. 범위 줄과 `[전체 회귀 권고]` 블록을 보고에 그대로 옮긴다.

### 2.3 기록

- S1 구현 뒤 **D-003 부기** 후보: 「(1) DB 계층 readonly의 실체 = MCP 서버 공용 실행 지점 검사 + 세션 읽기 전용」. D-003은 「절대 변경 불가」라 부기는 **강화 사실 기록**으로만 쓴다.
- S2는 CLAUDE.md 「토큰 서명 검증 뒤 `type`·role 클레임을 명시 검증」의 이행 — 새 결정 불필요.
- S3 관리자 경로 문구 방침을 사용자가 정하면 그 답을 이 계획서 §5에 적는다(저장소 전반 오류 노출 규약으로 넓히면 D-번호 후보).
- **W1 기록 결과(2026-10-14)**: D-003 부기 완료(강화 사실만 — MCP 서버 단일 실행 지점 검사·허용 목록·세션 RO · 결정 본문 무변경) · D-294 부기(D3) · D-316 부기(D4) · 색인 D-003 상태 칸 · CHANGELOG 1행. S2는 새 결정 없음.
- **S3 문구 방침(확정 A안의 구현 규약)**: 요청 번호 = 감사 미들웨어가 요청마다 만드는 `request_id`(uuid4 8자리 · 응답 헤더 `X-Request-ID`로도 실림 · 서버 로그 검색 키). 관리자 = `src/api/error_detail.py` `admin_error_detail()` 「{작업} 실패했습니다 ({예외 종류} · 요청 {번호}) — 상세는 서버 로그」(입력 오류 2곳은 WARNING·스택 없음). 사용자 = `user_error_detail()` 고정 문구 + 요청 번호, 답 본문·SSE 상세·작업 카드는 `src/utils/user_error.py`의 결정적 허용 목록 판별(고정 사유·머리말·LLM 산출 발췌는 유지 · 예외 원문은 제거 — D-153 후속2 발췌 노출과 정합). 저장소 전반 규약으로 넓히지 않았다(D-번호 미발행 — 넓히면 후보).

---

## 3. 수정 금지 · 범위 밖

- 쓰기 기능 허용·readonly 완화(D-003).
- `plans/162`의 구조 리팩터(이동·분할) — 같은 파일을 만지면 한쪽을 먼저 끝낸다.
- `except Exception` 일괄 정리 · 로그 수준 조정.
- `apm_gateway`·`sre_agent`의 예외 노출(이번 분석 범위 밖 — 필요하면 별건).
- 사용자/운영자 시크릿 자동 분리 강제(기동 실패) — 경고 로그까지만.

---

## 4. 파일 겹침

| ID | 고치는 파일 | `plans/162`와 겹침 |
|---|---|---|
| S1 | `mcp_server/mcp_server/db.py` · `tools.py` · `security.py` (+ 테스트) | M5(`mcp_server` 응답 헬퍼·빌더 분리 — W2) |
| S2 | `src/api/dependencies.py` (+ 테스트) | A5(`type` 검증 공용 함수 — W1~W3 사이 미정) |
| S3 | `src/api/routes/query.py` · `apm_jobs.py` · `conversation.py` · `admin.py` (+ 테스트) | A1·A3(W3 별도 계획) · A6(W1 SSE 상수) · A7(W2 `admin.py` 라우터 분할) |
| X6 | `src/clients/fabrix_client.py` · `ollama_client.py` (+ 테스트) | 없음(162에서 X6 제외) |
| S1+ (v1.2) | `mcp_server/mcp_server/tools.py`(스키마 헬퍼) · `security.py`(허용 목록) · `db.py`(공용 실행 지점·세션 RO) | M5(W2) |
| S3+ (v1.2) | 위 S3 + `src/api/routes/schema_cache.py` · `alarm.py` · `src/api/stream_failure.py` · `src/orchestration/result_aggregator.py`(1414·1425) · `src/api/middleware/audit_middleware.py`(요청 번호 전달) · 프런트 오류 표시(`src/static`) | A1·A3·A6·A7 · X1(`query.py`) |
| X6+ (v1.2) | `src/config.py`(`LLMConfig.model_post_init`) · 문서 오기(`docs/03`·`docs/09`·`.env.example`·`src/llm.py` 주석) | X1·I4(`config.py` — W1·W2) · **`[웨이브 회귀 필요]`** |
| D1~D6 (v1.2) | `src/graph.py`(D1) · `src/api/routes/query.py`(D2·D5·D6) · `src/schema_cache/`(D3·D4) | O1·O2(W3~W4 `graph.py`) · A1·A3 · I3 — **D·S3가 `query.py`를 함께 고치므로 같은 트랙에서 순차** |
| W1 신규 공용 모듈 (v1.3) | `src/utils/user_error.py`(사용자 화면 오류 문구 판별 — `safe_error_text`·`user_error_text`·`validation_reason_text`·`db_error_text`·`request_id` · utils 계층) · `src/api/error_detail.py`(라우트 오류 응답 — `user_error_detail`·`admin_error_detail`) | 162 W1 SSOT·상수 작업(A6 SSE 상수 등)이 오류 문구·요청 번호를 다루면 **이 두 모듈을 정본으로 재사용**한다(새 헬퍼 중복 금지) · 이동·분할은 162 쪽에서 동작 불변으로 |

확정 순서(v1.1 · `plans/162` §0.1): **W0은 162 W0과 병행(단계 1), W1은 162 W1 착수 전에 끝낸다(단계 2).** 근거: S3·S1이 고칠 파일을 162 W1(A6 SSE 상수·X1 `query.py`)과 W2(A7 `admin.py`·M5 `mcp_server`)가 먼저 고치거나 쪼개면 diff가 커지고, 162 A5(`type` 검증 공용 함수 · W1)는 S2가 넣은 사용자 검증을 그대로 옮기는 형태로만 성립한다. 162 W1의 X5(`fabrix_client.py:116` 로그)도 X6 뒤다.

---

## 5. 이력

- 2026-10-14 v1.4 **후속 교정**(커밋 `a409f482` 뒤 · 메인 지시 — 사용자 확정 #1과 같은 종류라 별건으로 미루지 않음 · 162 단계 3 착수 전) — security-auditor 교정분 최종 판정(Critical 0 · 1차 M1~M3·L2 교정 확인)이 찾은 판별기 미경유 사용자 노출 경로를 고쳤다. ① 호스트 조사 `inspect_failed`: `host_inspect.py`가 `error`·`final_response`를 고정 문구+요청 번호로 짓는다(`degraded_reason` 신뢰 규칙 `user_error.py`·`dbhub/client.py`는 무변경) · metric_trend 부분 실패 줄도 요청 번호 ② `result_aggregator` 통합 결과 정리 오류 ③ `field_mapper`·`synonym_registrar` 유사어 등록 오류 ④ `output_generator` 양식 채우기 사유 ⑤ `apm_query` 접수 답·요약의 실패 대상(`_failed_targets_text`)과 첫 홉·결과 파일 청크·대상 검색·인스턴스 정합 step error(`_public_call_error` · 코드·예외 종류 이름·고정 사유만 보임 · `_GATEWAY_CODE_RE`가 URL 스킴을 코드로 읽던 결함 수정) ⑥ `user_error` 마감 문구·선별 0개 안내를 전체 꼴로만 통과 ⑧ `mcp_server` 부작용 함수 7종(`pg_stat_statements_reset`·`txid_current`·`pg_current_xact_id`·`brin_summarize_new_values`·`brin_summarize_range`·`brin_desummarize_range`·`gin_clean_pending_list` · `*_if_assigned`·스냅숏 읽기 함수는 허용). 실패 단언 테스트 먼저(S3 17건·S1 7건 실패 확인 — `_shown_call_error` 일부 케이스는 규칙과 함께 작성). auditor 해당 항목 재확인 1~6·8 모두 교정 확인. 회귀 `regress.py --base a409f482 --files <13파일>` 본체 통과 3,469 · 실패 0 · mcp_server 980 통과 · 게이트 통과 · exit 0 · 권고 블록 없음(범위: 모듈 단위 — 전체 미실행). ruff I001(`mcp_server/tests/test_plan163_w1_verify_readonly.py`) 정리. 매뉴얼 `user.md` 오류 표 1행(작업별 실패 문구+요청 번호) · `tests/test_manual` 619 통과.
  - **잔여(기록만)**: ⑦ `apm_query.py` `source_status.reason`의 `str(e)` — `query.py`가 `system:status`만 내보내 사용자 미노출 · `apm_query.py:1523` `_retry_selection`의 `info["error"]`(예외 원문 120자)가 선택 메타에 남음 — 사용자 문구 아님, 메타를 응답·감사로 넓힐 때 거를 것 · **구조적 차단 아님 — `degraded_reason`에 원문 금지 규약**: `user_error.py`는 `degraded_reason`이 붙은 결과의 문구를 처리기 문구로 신뢰한다. 앞으로 처리기가 `degraded_reason`을 붙이며 예외 원문을 넣으면 같은 구멍이 다시 열린다 → `secure-coding` 스킬 규칙 후보(「`degraded_reason`을 붙이는 처리기의 `error`·`final_response`는 고정 문구+요청 번호, 원문은 로그」 · 이번 계획에서는 스킬을 고치지 않음) · S1 목록에 넣지 않은 함수: `pg_import_system_collations`(카탈로그 쓰기지만 superuser 전용) · `pg_prewarm`(확장 함수 · 버퍼 캐시만 변경) — 운영 계정 권한으로 막힘 · **162 SSOT 후보**: `apm_query.py`의 `_call_failure_text`·`_shown_call_error`·`_public_call_error`가 같은 「오류 코드 → 화면 문구」 규칙을 나눠 갖는다 — 162에서 동작 불변으로 하나로 합친다(합칠 때 첫 홉 고지 「부하 순위를 받지 못해(api_error (요청 rid))」의 이중 괄호를 「(api_error · 요청 rid)」 꼴로 다듬는 UX Minor도 함께 — 이번에는 문구를 바꾸지 않음 · 매뉴얼 `user.md`는 `(…)` 자리표시라 영향 없음).
  - **162 SSOT 후보**: `apm_query.py`의 호출 오류 문구 함수 3종(`_call_failure_text`·`_shown_call_error`·`_public_call_error`)이 같은 규칙(안내 코드는 사유까지 · 그 밖 코드만 · 원문은 로그)을 나눠 가진다 — 162 단계 3 이후 한 함수로 합칠 후보(동작 불변). 첫 홉 고지 「(api_error (요청 rid))」 이중 괄호는 문구 정리 후보로 남긴다.
- 2026-10-14 종결 — 메인 세션 검토(신규 테스트 242 passed·1 skipped · mcp_server 971 passed · apm_gateway W0 21 passed · arch·overfit 0) 뒤 단계 1·2 일괄 커밋 · 사용자 확정 #5(§2.1a — 멀티 DB 실패 사유 (a)안) 기록 · 파일명 태그 제거.
- 2026-10-14 v1.3 / **W1 완료** — 트랙별 구현: **S1·S1+** `mcp_server` 검사기 허용 목록화(3방언 본문 동시 검사 · 금지어·단일 문장·SELECT/WITH 시작·INTO·행 잠금·부작용 함수·`U&` 리터럴·`:=`·NEXT VALUE FOR 등) · 스키마 헬퍼 9곳 식별자 검사+따옴표 이중화 · 검색 패턴·폴스타 리터럴 백슬래시 거부 · `DBPoolManager.execute`·`health_check` 단일 실행 지점 검사(설정 없는 소스 차단) · 세션 RO(PG `server_settings` · MariaDB `init_command` · DB2는 계정 권한 의존 주석) / **S2** 사용자 토큰 `type!="user"` → 401 · 같은 시크릿 기동 경고(명시 설정일 때만) / **S3·S3+** 사용자 경로 고정 문구+요청 번호 · SSE `last_error`·`steps[].detail`·작업 카드·aggregator·APM 409·오류 응답 노드(`graph.py` `_error_response_node`)까지 원문 제거 · 관리자 A안 14곳(+`cache_manager` 갱신 실패) · `admin.py:910` 원문 유지 / **X6·X6+** 클라이언트 env 폴백 제거(빈 필수 인자 `ValueError`) · `LLMConfig.model_post_init` 삭제 · 문서 오기 정정 / **D 트랙** §2.1a 판정 표(D2 의도·미수정, 나머지 결함 수정). 테스트를 먼저 쓰고 구현.
  - **스냅샷 재채록**(`UPDATE_PLAN162_SNAPSHOT=1` · 162 §0.1 운영 규칙 4): `redis_keyspace.json` — `invalidate_all` 뒤 `asset_drafts`·`ddl_comments` 잔존(D3) · `invalidate(db_a)` 뒤 `knowledge_seeded` 제거(D4) / `graph_topology.json` — 53조합 중 1·2단 14조합의 노드 집합 축소(D1 — 3단 노드·단일 DB 꼬리·`approval_gate` 미등록 · 3·4단 비트 동일) / `done_payload_keys.json` — ainvoke 폴백 `awaiting_approval` · 파일 스트림 `clarification`·`source_switch` · 처리 상한 부분 결과 기본 키(D6) / `stream_node_names.json` — `entity_locator`·`sequential_runner`(D5) + D1 노드 축소 반영. 이전본은 세션 scratchpad에만 보관(커밋 대상 아님).
  - **검증**: verifier·security-auditor 같은 라운드 병렬 — Critical 0. Major 3(SSE 원문 잔여 → 교정 · 매뉴얼 → 반영 · 폐쇄망 `FABRIX_*` 셸 env → 확인 항목) · auditor M1~M3·L2 교정. 교정 4차까지(오류 응답 노드·APM 전체 실패·존 각주·작업 카드 포함).
  - **회귀**: 계획 끝 1회 `regress.py --base 08863d6 --files <누적 54>` — 본체 통과 15,125 · 실패 5(이번 변경 3: `test_dbhub_integration`·`test_v7_audit_and_estimate` 교정, `test_plan145_correction1` 부하 타이밍 — 단독 통과 / 원래 실패 2: `test_plan160_chart_api …[True]`·`test_settings_help::test_t2_curated_groups_have_full_coverage`) · mcp_server 920 통과 · `[전체 회귀 권고]`(허브 `src.config` 88% · `LLMConfig.model_post_init` 삭제 · 직접 선택 48%). 교정분 모듈 선택 회귀(26파일) exit 0 — 본체 통과 6,944 · 원래 실패 1 · mcp_server 971 통과. 범위: 모듈 단위 — 전체 미실행.
  - **매뉴얼(D-255)**: `admin.md`(설정 저장·로드·재빌드·준비도 오류 표 · 요청 번호 안내) · `user.md`(처리 중 오류·오류 응답 「에러 내용」·제니퍼 전체 실패·마지막 사유 문구) 갱신 · 빌드 · `tests/test_manual` 619 통과.
  - **잔여(이탈 근거 포함)**: 검사기 3방언 동시 판독 오거부(`'C:\Windows\'…delete`·`SELECT "into"` — verifier strict xfail 2건 고정) · 값 안의 백슬래시 거부(호스트명·알람명에 백슬래시가 있으면 조회 불가) · PG `f(a := 1)` 거부 · 멀티 경로 replanner 안내의 「(마지막 사유: …)」 생략(`regen_stops` detail에 머리말 없음) · `cache_management` 상태 `str(e)`(화면 미노출) · `invalidate_all`의 `_knowledge_done` clear는 지시 밖 확장(되돌림 1줄) · aiomysql `MULTI_STATEMENTS`는 0.3.2에서 끌 수 없음(검사기 단일 문장 검사로 보완).
  - **폐쇄망 확인 항목**: ① 운영 DB 계정 SELECT 전용 여부(DB2 세션 RO 미실측 — 계정 권한이 최종 방어) ② 셸 env `FABRIX_*`·`GOOGLE_API_KEY`·`LLM_API_KEY`(접두 없음) 사용 여부 — 쓰면 X6+ 이후 기동 `ValueError` → `LLM_FABRIX_*` 등으로 이관 ③ 호스트명·알람명에 백슬래시 존재 여부 ④ 로컬 `.encenv`의 `LLM_API_KEY`는 원래 무시되던 키 — 쓰려면 `LLM_OLLAMA_API_KEY`.
- 2026-10-14 v1.2 — 단계 1 끝 사용자 확정 4건(§2.1a): W0 신규 보안 항목 4건 모두 W1 포함 · S3 관리자 문구 A안(예외 종류 + 요청 번호) 약 14곳 · W0 발견 기존 동작 결함 후보 D1~D6을 이 계획에서 처리(의도 확인 뒤) · 단계 1 결과는 단계 2 끝에 함께 커밋. 코드 변경 0건.
- 2026-10-14 W0 완료 — 실측 부록(§2.1) 기록 · 고정 SQL 26종/147케이스 `validate_readonly` 차단 0 → `mcp_server/tests/test_readonly_fixed_sql.py` 고정 · 세션 RO PG·MariaDB 가능(되돌림 가능 · 보조 수단)·DB2 미실측 · verifier 독립 재확인에서 스키마 헬퍼 SQL 주입(Critical)·금지 목록 검사기 우회 구문(Major)·S3 범위 과소(관리자 ~14곳·aggregator 답 본문·SSE 상세)·X6 `config.py` 폴백 발견 → W1 계획 수정 4건을 W0 끝 사용자 확인에 올림. 제품 코드 0건.
- 2026-10-14 v1.1 — 단계 배치 확정(사용자 지시 「계획을 단계적으로 진행하도록 계획을 정리하라」): W0 = `plans/162` §0.1 단계 1(162 W0과 병행) · W1 = 단계 2(162 W1 전). S1 고정 SQL 통과표를 이 계획 W0 단독 소유로 · S3 관리자 문구 확인 지점을 W0 끝으로 고정(답 전에는 S3 관리자 분만 보류) · W1 진행 순서와 스냅샷 갱신 권한 명시. 코드 변경 0건.
- 2026-10-14 v1.0 — 최초 작성. `plans/162` §3.8(S1~S3)과 X6을 이관(U-6 확정 · D-330 ⑥). 코드 근거 재확인: S3 사용자 경로 6곳(종전 「query.py 2605 근방 외」에서 `conversation.py:71` 추가 확인) · S2 시크릿 분리(D-070) 확인과 동일 값 가드 부재 · X6 운영 호출부 전수(`src/llm.py` 2곳 + `bind_tools` 2곳 모두 인자 전달). 코드 변경 0건.
