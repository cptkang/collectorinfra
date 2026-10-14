# 163. 보안 강화 분리 건 — mcp_server 읽기 전용 단일 지점 · 사용자 토큰 `type` 검증 · 예외 문자열 비노출 · LLM 클라이언트 env 폴백

> **작성일**: 2026-10-14 · **v1.1**(단계 배치 확정 — `plans/162` §0.1 단계 1·2 · 구현 전)
> **상태**: **WIP** — W0(단계 1) 진행 중(2026-10-14 착수). 제품 코드 0건. 4건(S1·S2·S3·X6). `plans/162` 분석에서 나온 **행동이 바뀌는 보안 항목**을 구조 리팩터와 분리한 계획(U-6 확정 · D-330 ⑥). **진행 위치(`plans/162` §0.1 단계표가 정본)**: W0 = **단계 1**(162 W0과 병행) → W1 = **단계 2**(단계 1 전부 종료 뒤 · 162 W1 착수 전에 끝낸다).
> **요청 근거(2026-10-14)**: 사용자 지시 「전체적인 코드와 계획에 맞게 결정사항은 권고에 맞게 정리하라」 → `plans/162` §7 U-6 권고 ① 「별도 보안 계획(security-auditor 동반)」 채택.
> **성격**: **행동 변경**(차단·응답 문구·기동 실패 조건이 바뀐다). `plans/162`의 동작 불변 게이트(스냅샷 무갱신)를 적용하지 않는다 — 대신 건마다 「바뀌는 동작」을 성공 기준에 적고 그 동작을 단언하는 테스트를 새로 둔다.
> **관련 결정**: D-003(3중 읽기 전용 방어 — 절대 변경 불가 · S1은 **강화** 방향) · D-070(사용자/운영자 JWT 시크릿 분리) · D-330(행동 변경 분리 원칙) · D-303(회귀 정책) · D-255(매뉴얼 동반 — S3 문구 변경이 화면에 보이면 대상 여부 판단) · D-139·D-274(패키지 경계 — `mcp_server`는 양방향 import 0)
> **상위·인접 계획**: `plans/162`(구조 리팩터 — §3.8에서 이관 · **같은 파일을 고치면 한쪽을 먼저 끝낸다**: S1 ↔ 162 M5(`mcp_server` 분할), S3 ↔ 162 A1·A3(`query.py` 스트림 공통화), S2 ↔ 162 A5(`type` 검증 공용 함수), X6은 162에서 뺐다)
> **적용 스킬**: `.claude/skills/secure-coding/` (D-332) — 위임 프롬프트에 「`secure-coding` 스킬을 따른다」를 적는다.
> **분석 기준 SHA**: `7ba5c96`. 아래 `file:line`은 2026-10-14 이 시점 재확인 값이다.
> **계획 시작 SHA**: *(W0 착수 시 기록)* · **회귀 대상 파일**: *(Wave마다 누적 — 착수 후 기록)*

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

### 2.2 W1 — 구현 (4트랙 병렬 가능 · 단계 2)

**진행 순서**: ① 트랙마다 「바뀌는 동작」 단언 테스트를 먼저 쓰고 실패를 확인 → ② 구현 → ③ verifier + security-auditor 같은 라운드 병렬 검증 → ④ team-lead 계획 끝 회귀. 트랙(파일 겹침 0): S1(`mcp_server`) / S2(`dependencies.py`) / S3(`query.py`·`apm_jobs.py`·`conversation.py`·`admin.py`) / X6(`src/clients/`). S3가 `plans/162` 단계 1 스냅샷(SSE 오류 페이로드 등)에 걸리면 **이 계획이 그 스냅샷을 갱신**하고 사유를 §5에 적는다 — 스냅샷을 갱신할 수 있는 계획은 이 계획뿐이다(162 §0.1 운영 규칙 4).

| ID | 구현 | 성공 기준 |
|---|---|---|
| **S1** | ① 읽기 전용 검사를 `PoolManager.execute`(또는 그 바로 앞 공용 실행 함수) 한 곳으로 모은다 — readonly 소스면 모든 경로가 검사를 받는다. W0에서 차단되는 고정 SQL이 있으면 **검사기를 좁히지 말고** 그 SQL을 고치거나, 근거와 함께 검사기의 허용 구문을 최소로 넓힌다 ② W0에서 확인된 세션 읽기 전용 설정을 풀·연결 생성에 넣는다 | 쓰기 SQL(`INSERT`·`UPDATE`·`DELETE`·DDL)을 폴스타 도구 경로·exporter 경로로 흘려도 차단되는 테스트 · PG 세션에서 쓰기가 DB 오류로 거부되는 테스트(로컬 도커) · 기존 폴스타 도구 테스트 통과 · `overfit_check` 0(`mcp_server` 스캔 대상 — 독스트링 리터럴 주의) |
| **S2** | `_verify_user_token`에 `payload.get("type") != "user"` → 401 · 운영자 쪽과 같은 형태로 맞춤(공용 함수 추출은 `plans/162` A5와 조율 — 먼저 끝난 쪽 형태를 따른다) · 선택: 기동 시 `auth.jwt_secret == admin.jwt_secret`이면 경고 로그(명시 설정일 때만) | `type` 없음·`admin` 토큰 → 401 테스트 · 정상 사용자 토큰 통과 · 인증 테스트 전수 통과 |
| **S3** | 사용자 경로 6곳: 응답은 고정 문구 + 상관 ID(`query_id` 등 기존 값 재사용 우선), 원문은 `logger.exception`으로 서버 로그에만. 관리자 경로 7곳: 관리자 화면은 원인 파악이 필요하므로 **문구 유지 여부를 사용자에게 묻는다**(기본안: 예외 타입명 + 상관 ID만) | 사용자 경로에서 예외 원문이 응답·SSE 본문에 나오지 않는 테스트 · 로그에는 남는 테스트 · 웹 UI 오류 표시 확인 · 화면 문구가 바뀌면 매뉴얼 대상 여부 판단(D-255) |
| **X6** | 두 클라이언트에서 `os.getenv` 폴백 제거 · 필수 인자가 비면 생성 시 `ValueError`(어떤 설정 키가 비었는지 메시지에 명시) · `ollama_client`는 인자 우선 | 빈 `fabrix_base_url`로 생성 시 `ValueError` 테스트 · 셸 env를 줘도 인자가 쓰이는 테스트 · `create_llm` 경로 테스트 통과 · W0에서 env 의존 기동 경로가 나오면 그 경로를 설정 키로 옮긴 뒤 제거 |

- **계획 끝 회귀**(W1이 마지막 Wave): `python scripts/regress.py --base <계획 시작 SHA> --files <바꾼 파일…>` 1회 + `mcp_server` 자체 테스트. 범위 줄과 `[전체 회귀 권고]` 블록을 보고에 그대로 옮긴다.

### 2.3 기록

- S1 구현 뒤 **D-003 부기** 후보: 「(1) DB 계층 readonly의 실체 = MCP 서버 공용 실행 지점 검사 + 세션 읽기 전용」. D-003은 「절대 변경 불가」라 부기는 **강화 사실 기록**으로만 쓴다.
- S2는 CLAUDE.md 「토큰 서명 검증 뒤 `type`·role 클레임을 명시 검증」의 이행 — 새 결정 불필요.
- S3 관리자 경로 문구 방침을 사용자가 정하면 그 답을 이 계획서 §5에 적는다(저장소 전반 오류 노출 규약으로 넓히면 D-번호 후보).

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

확정 순서(v1.1 · `plans/162` §0.1): **W0은 162 W0과 병행(단계 1), W1은 162 W1 착수 전에 끝낸다(단계 2).** 근거: S3·S1이 고칠 파일을 162 W1(A6 SSE 상수·X1 `query.py`)과 W2(A7 `admin.py`·M5 `mcp_server`)가 먼저 고치거나 쪼개면 diff가 커지고, 162 A5(`type` 검증 공용 함수 · W1)는 S2가 넣은 사용자 검증을 그대로 옮기는 형태로만 성립한다. 162 W1의 X5(`fabrix_client.py:116` 로그)도 X6 뒤다.

---

## 5. 이력

- 2026-10-14 v1.1 — 단계 배치 확정(사용자 지시 「계획을 단계적으로 진행하도록 계획을 정리하라」): W0 = `plans/162` §0.1 단계 1(162 W0과 병행) · W1 = 단계 2(162 W1 전). S1 고정 SQL 통과표를 이 계획 W0 단독 소유로 · S3 관리자 문구 확인 지점을 W0 끝으로 고정(답 전에는 S3 관리자 분만 보류) · W1 진행 순서와 스냅샷 갱신 권한 명시. 코드 변경 0건.
- 2026-10-14 v1.0 — 최초 작성. `plans/162` §3.8(S1~S3)과 X6을 이관(U-6 확정 · D-330 ⑥). 코드 근거 재확인: S3 사용자 경로 6곳(종전 「query.py 2605 근방 외」에서 `conversation.py:71` 추가 확인) · S2 시크릿 분리(D-070) 확인과 동일 값 가드 부재 · X6 운영 호출부 전수(`src/llm.py` 2곳 + `bind_tools` 2곳 모두 인자 전달). 코드 변경 0건.
