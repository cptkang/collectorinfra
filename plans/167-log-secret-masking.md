# 167. 로그 비밀값 가림 필터 — 로그 출력 단계에서 자격증명·토큰을 가린다 (기록)

> **작성일**: 2026-10-16 · **v2.0**(2026-10-18 완료)
> **상태**: **완료 · 커밋 전** — 2026-10-17 착수(사용자 지시 「확인할 사항은 직접 확인하고 다음 작업을 진행하라」 · §4 결정은 메인 확정) · W0 실측(§6) · W1 구현 + 교정 4라운드(§7) · D-339 · 장기 실행 프로세스(MCP 9099·9097 · 게이트웨이 9096 · 본체)는 **재기동해야 반영**.
> **계획 시작 SHA**: `4b39b4e9`
> **회귀 대상 파일**: apm_gateway/apm_gateway/__main__.py · apm_gateway/apm_gateway/adapters/log_masking.py · apm_gateway/tests/test_log_masking_entry.py · fabrix_proxy/fabrix_proxy/__main__.py · fabrix_proxy/fabrix_proxy/log_masking.py · fabrix_proxy/tests/test_config.py · fabrix_proxy/tests/test_log_masking_entry.py · mcp_server/mcp_server/__main__.py · mcp_server/mcp_server/log_masking.py · mcp_server/tests/test_log_masking_entry.py · noise_gate/alarm_server/__main__.py · noise_gate/tests/test_alarm_server_log_masking.py · scripts/manual/content/admin.md · src/main.py · src/security/audit_logger.py · src/static/manual/{admin.html,help_index.json,user.html} · src/utils/log_masking.py · sre_agent/sre_agent/infrastructure/log_masking.py · sre_agent/sre_agent/run_service.py · sre_agent/tests/test_log_masking_entry.py · tests/test_api/test_plan163_s3_admin_error_detail.py · tests/test_api/test_plan163_s3_user_errors.py · tests/test_orchestration/test_plan184_apm_meta.py · tests/test_security/test_log_masking_setup.py · tests/test_utils/test_log_masking.py · tests/test_utils/test_log_masking_copies.py · tests/test_utils/test_log_masking_verify.py · tests/test_utils/test_log_masking_verify_r2.py · tests/test_utils/test_log_masking_verify_r3.py · tests/test_utils/test_log_masking_verify_r4.py
> **출처**: `plans/163` W1 security-auditor 잔여 Low 권고 「로그 비밀 마스킹 필터」(2026-10-14). 163은 화면·응답에서 예외 원문을 걷어냈지만(S3·S3+) **원문은 의도적으로 서버 로그에 남긴다**(진단용 — 163 S3 테스트가 caplog에 원문이 있음을 단언). 그 원문에 자격증명이 섞일 수 있다는 지적이다.
> **실측 기준**: RCA `3a0a1101`. `file:line`은 이 시점 값이다.
> **관련 결정**: D-337 ⑥(행동 변경은 구조 리팩터와 분리 — 이 계획은 로그 문자열이 바뀌는 행동 변경) · D-332 부기(사내망 `verify=False`는 결함 아님 — 이 계획 범위 밖) · D-070(사용자/운영자 시크릿 분리) · `docs/pii_filtering_rules.md`(PII 덤프 `logs/pii_block/`은 서버 밖으로 나가지 않음)
> **적용 스킬**: `.claude/skills/secure-coding/`

---

## 1. 현황 (2026-10-16 실측)

| 항목 | 실측 |
|---|---|
| 로깅 진입점 | 본체 `src/security/audit_logger.py:643` `setup_logging`(`logging.basicConfig(force=True)` + structlog) · `noise_gate/alarm_server/__main__.py:30` · `mcp_server/mcp_server/__main__.py:20` · `apm_gateway/apm_gateway/__main__.py:94` — 각자 `basicConfig` |
| 기존 로그 필터 | 0 (`logging.Filter`·`addFilter` 사용처 없음 — `tests/` 제외) |
| 비밀값을 직접 찍는 호출 | 0 — `token`·`api_key`·`password`·`secret`·`Authorization`이 든 로그 호출은 `mcp_server/__main__.py:51` 1건이며 on/off만 찍는다 |
| 남는 위험 | **예외 원문**: DB 드라이버·HTTP 클라이언트 예외 메시지에 DSN(`user:pw@host`)·URL 쿼리 키·`Bearer` 헤더가 실릴 수 있다. 163 S3/S3+ 이후 이 원문은 응답에서는 빠졌고 `logger.*(…, exc_info=…)`·`%s` 인자로 로그에만 남는다 |
| 응답 경로 | 163에서 처리 완료 — 범위 밖 |

## 2. 목표

로그 레코드가 핸들러에 닿기 전에 자격증명 형태 문자열을 가린다. 진단에 필요한 나머지 원문(예외 타입·호스트·SQL 상태 코드)은 남긴다.

## 3. 범위 초안

- **W0 실측**: 예외 원문에 비밀값이 실리는 경로를 드라이버별로 재현(asyncpg·ibm_db·aiomysql·httpx·redis — 로컬 샌드박스는 **읽기만** · 쓰기 시도 금지). 가릴 패턴 목록 확정(DSN `://user:pw@` · `Bearer …` · `api_key=`·`token=` 쿼리 · `.env` 키 이름 `*_API_KEY`·`*_SECRET*`·`*_PASSWORD` 값).
- **W1 구현**: 공용 `logging.Filter`(레코드 `msg`·`args`·`exc_text` 렌더 결과를 가림) 1개 → 진입점 4곳에 부착. 패키지 경계(D-139 — `mcp_server`·`apm_gateway`는 본체와 import 0)를 지키려면 패키지마다 사본을 두거나 패턴만 공유하는 방식을 §4에서 정한다.
- **검증**: 패턴별 가림 단언 · 163 S3 caplog 단언(원문 존재)이 비밀값이 아닌 부분으로 계속 통과하는지 · 성능(핫 경로 로그 비용) 측정.

## 4. 착수 전 결정할 것

| # | 질문 | 선택지 |
|---|---|---|
| 1 | 가림 위치 | (a) 핸들러 필터(전역 · 누락 없음) · (b) 예외 원문을 찍는 지점마다 헬퍼(정밀 · 누락 위험) |
| 2 | 패키지 경계 | (a) 패키지마다 필터 사본 + 사본 일치 테스트 · (b) 본체만 적용하고 `mcp_server`·`apm_gateway`는 기존 계약(비밀값 미로그) 유지 |
| 3 | 기존 로그 파일 | (a) 소급 처리 없음 · (b) 보관 로그 일괄 가림 스크립트 |

**확정(2026-10-17 · 메인)**: ① (a) 전역 가림 — 레코드 렌더 결과(`msg % args`)·`exc_text`·`stack_info` + structlog 경로 · ② (a) 패키지마다 사본 + 사본 일치 테스트(`mcp_server` 필수) · ③ (a) 소급 없음. 부착 방식은 §6.3.

## 5. 범위 밖

- 사내망 클라이언트 `verify=False`(D-332 부기 — 의도)
- 응답·화면의 예외 원문(163에서 완료)
- `logs/pii_block/` 덤프(서버 밖 반출 없음 · 별도 규칙 `docs/pii_filtering_rules.md`)
- 로깅을 거치지 않는 파일 싱크 — 감사 JSONL(`audit_logger._write_audit_file_sync`)·SQL 로그(`mcp_server/sql_log.py` · `logs/sql/`)·`print(…, file=sys.stderr)`

## 6. W0 실측 (2026-10-17 · 루트 venv Python 3.12.11)

### 6.1 드라이버·클라이언트 예외 문자열

네트워크 없는 예외 생성 우선 · 로컬 샌드박스는 연결 시도만(MariaDB 3307 잘못된 비밀번호 1회 · 쓰기 0). 비밀값 `S3cretPw!x` 꼴을 넣고 `str(e)`·`repr`·`traceback.format_exception` 전체에서 원문 검색.

| 대상(버전) | 시도 | 오류 문자열 | 비밀값 노출 |
|---|---|---|---|
| asyncpg 0.31.0 | DSN `postgresql://alice:<pw>@127.0.0.1:1/db` 연결 거부 · 잘못된 IPv6 · 잘못된 sslmode · 포트 비숫자 | `[Errno 61] Connect call failed ('127.0.0.1', 1)` · `Invalid IPv6 URL` · `` `sslmode` parameter must be one of … `` · `invalid literal for int() with base 10: 'notaport'` | 없음 |
| aiomysql 0.3.2 | 연결 거부 · **3307 잘못된 비밀번호** | `(2003, "Can't connect to MySQL server on '127.0.0.1'")` · `(1045, "Access denied for user 'probe_user'@'…' (using password: YES)")` | 없음 |
| redis 8.1.0 | `redis://:<pw>@127.0.0.1:1/0` ping | `Error 61 connecting to 127.0.0.1:1. …` · `repr(client/pool)`은 `password=<REDACTED>` | 없음 |
| httpx 0.28.1 | 401 응답 `raise_for_status` (쿼리 `api_key=`·`token=` · `Authorization: Bearer`) | `Client error '401 Unauthorized' for url 'https://…/x?api_key=<pw>&token=<pw>'` · `repr(Request)`에도 URL 원문 · `repr(headers)`는 `'authorization': '[secure]'` | **있음 — URL 쿼리** |
| httpx 0.28.1 | `str(httpx.URL("http://bob:<pw>@h/"))` | `http://bob:<pw>@h/`(repr은 `[secure]`) | **있음 — URL userinfo를 `%s`로 찍을 때** |
| ibm_db | 루트 venv 미설치(163 W0와 같음) — 실측 불가 | 연결 문자열 `DATABASE=…;UID=…;PWD=…;`(`mcp_server/db.py:205` `ibm_db.connect(conn_str, "", "")`) | 연결 문자열이 로그·예외에 실리면 `PWD=` 노출 — 패턴으로 덮는다 |

결론: DB 드라이버 자체 예외 문구에는 비밀번호가 없다(드라이버들이 이미 가림). **실 노출 경로는 ① httpx 상태 오류·Request repr의 URL 쿼리 키 ② URL·DSN·연결 문자열을 애플리케이션이 `%s`로 찍는 경우 ③ 트레이스백 소스 줄의 리터럴**이다. 키가 쿼리로 가는 API(Gemini `?key=`)가 ①에 해당한다.

### 6.2 로그 경로 실측

| 경로 | 실측 | 루트 핸들러 필터로 덮이나 |
|---|---|---|
| 본체 `setup_logging`(`src/security/audit_logger.py:643`) | `basicConfig(force=True)` + structlog `PrintLoggerFactory`(JSONRenderer → stdout 직행 · stdlib 우회) · 앱 lifespan(`src/api/server.py:373`)·관리자 설정 리로드에서 재호출 | structlog 경로는 **아니오** |
| uvicorn(본체 `src/main.py:102` `reload=True` · `mcp_server/__main__.py:52` · `apm_gateway/__main__.py:149` · `sre_agent/run_service.py:66`) | uvicorn `LOGGING_CONFIG`가 `uvicorn`·`uvicorn.access` 로거에 **자기 핸들러**를 달고 `propagate=False` — `Exception in ASGI application` 트레이스백·접근 로그(경로+쿼리)가 루트를 거치지 않는다 | **아니오** |
| `noise_gate/alarm_server/__main__.py:30` | `basicConfig` 단독 | 예 |
| `sre_agent/run_service.py:33` | `basicConfig(level=INFO)` + uvicorn · LLM `api_key` 보유(`diagnosis.py:326`) · 정적 Bearer | uvicorn 경로는 아니오 |

### 6.3 부착 방식 결정 (§4 ① 구체화)

루트 핸들러에 `logging.Filter`를 다는 방식은 uvicorn 핸들러(트레이스백·접근 로그)를 놓친다(§6.2). 「누락 없는 쪽」이라는 결정 취지에 맞춰 **`logging.setLogRecordFactory` 감싸기**로 레코드 생성 시점에 가린다 — 어떤 로거·핸들러로 가든 같은 레코드를 쓰므로 누락이 없다. 비밀 꼴이 없는 레코드는 손대지 않는다(`msg`·`args` 비트 동일). structlog는 JSONRenderer 앞 프로세서로 덮는다. `sre_agent`도 같은 위험(LLM 키·uvicorn·httpx)이라 사본을 넣는다(비용: 파일 1개 + 진입점 2줄).

## 7. W1 결과 (2026-10-18)

### 7.1 부착과 사본 (D-339)
| 진입점 | 설치 지점 | 가림 모듈 |
|---|---|---|
| 본체 | `src/security/audit_logger.py` `setup_logging`(basicConfig 직후 + structlog `mask_event_dict`를 JSONRenderer 앞에) · `src/main.py` `main()` 첫 줄(load_config·uvicorn reload 부모보다 먼저) | 정본 `src/utils/log_masking.py` |
| `noise_gate/alarm_server` | `__main__.py` basicConfig 직후 | 정본 import(D-139 부기) |
| `mcp_server` · `apm_gateway` · `sre_agent` · `fabrix_proxy` | 각 `__main__`/`run_service` basicConfig 직후 | 바이트 동일 사본(`mcp_server/log_masking.py` · `apm_gateway/adapters/` · `sre_agent/infrastructure/` · `fabrix_proxy/log_masking.py`) — 일치는 `tests/test_utils/test_log_masking_copies.py` |

`fabrix_proxy`(FabriX 토큰 보유)는 1차 감사 M3로 추가했다. 사용자·메인 범위 4곳에 `sre_agent`·`fabrix_proxy`가 더해졌다.

### 7.2 가림 규칙(최종)
후보 낱말 1차 검사(`://`·bearer·passw·pwd·token·key·secret·credential·authorization·cookie·ai-client·`eyJ`·`AIza` 중 하나도 없으면 원문 그대로) → ① URL userinfo 비밀번호(사용자명·호스트 유지) ② 민감 키·값(키가 `secret` 포함 또는 password·passwd·pwd·passphrase·token·api_key·access_key·private_key·client_key·client_secret·credential(s)로 끝남 · `=` 뒤 공백 불허 · `:` 뒤 공백은 따옴표 꼴/헤더형 키만 · 따옴표·이스케이프·`b''`·ODBC `{…}` · 값이 스킴 낱말이면 뒤 토큰을 가림) ③ `Authorization`·`Proxy-Authorization`·`x-generative-ai-client` 헤더(스킴 유지) ④ 쿠키 헤더 ⑤ `Bearer <토큰>`(앞이 `/`·식별자면 제외 · 뒤 `=`·`:\S` 제외) ⑥ 쿼리 `?key=`/`&key=` ⑦ JWT ⑧ Google API 키. 레코드: 비밀이 없으면 `msg`·`args` 그대로 · `uvicorn.access`는 인자 단위만(args 5-튜플 유지) · 나머지는 인자만 가려 같으면 args 유지, 아니면 가린 메시지 + `args=None` · `exc_text`는 비밀이 있을 때만 선설정 · 실패 시 원 레코드 + 표준 오류 1회 경고. structlog: 문자열·중첩 컨테이너·비원시값 repr 가림, 민감 키 이름이면 값 전체, 깊이 20 · 이벤트당 `seen`으로 순환 차단 · 값 단위 실패 격리.

보존(음성 테스트): 호스트·포트·DB 이름·사용자명·SQLSTATE·예외 타입 · `max_tokens`·`prompt_tokens`·`token_count`·`stream_key`·`token_type=bearer` · `(using password: YES)` · `token: expired` · `GET /auth/bearer HTTP/1.1`.

### 7.3 성능 (implementer 미세 벤치 · logger.info → StreamHandler · µs/건 · 설치 전 → 후)
| 경우 | 전 | 후 |
|---|---:|---:|
| 비밀 없음(인자 3) | 7.53 | 11.29 |
| 후보 낱말만(`token_count`) | 7.50 | 14.39 |
| 비밀 있음(DSN) | 7.49 | 27.51 |
| exc_info(비밀 없음 · 트레이스백 2회 포맷) | 44.11 | 114.44 |
| exc_info(비밀 있음) | 45.75 | 119.79 |

ReDoS: 적대 입력 40여 꼴 64KB 각 0.08s 미만 · 256KB·1MB 선형(최악 1MB 0.38s — 상수 ~0.37µs/자). 입력 절단 없음. 벤치 스크립트 scratchpad `bench_log_masking.py`.

### 7.4 고친 기존 단언
「원문은 서버 로그에 남는다」 caplog 단언을 비밀 아닌 부분(비밀번호 뒤 `@호스트…`)으로 좁혔다 — `tests/test_api/test_plan163_s3_user_errors.py` 22곳(`SECRET_LOGGED`) · `tests/test_orchestration/test_plan184_apm_meta.py` 4곳 · `tests/test_api/test_plan163_s3_admin_error_detail.py` `_assert_logged` 1곳. 「응답에 원문 없음」 단언은 전체 SECRET 그대로. `fabrix_proxy/tests/test_config.py`는 실 `main()`이 팩토리를 전역 설치하므로 finalizer로 복원.

### 7.5 검증
- 계획 끝 회귀 `regress.py --base 4b39b4e9 --files <29파일>` rc=0 — 본체 통과 936·실패 0 · apm_gateway 1,631·0 · mcp_server 981·0 · sre_agent 1,118·실패 7·에러 44(51건 전부 원래 실패) · arch·overfit·config_fallback 통과 · ruff·mypy diff 줄 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록 없음. fabrix_proxy 패키지 전체 375 passed(regress 패키지 목록 밖이라 별도).
- 교정 3·4라운드 뒤 같은 모듈 재회귀(정본·사본 5 + 테스트 4) rc=0 — 본체 350·0 · 패키지 수치 동일 · 원래 실패 51 · `범위: 모듈 단위 — 전체 미실행`. verifier r4 테스트 8 passed·1 xfailed.
- verifier 4회·security-auditor 2회. 1차 Major 2(uvicorn 접근 로그 소실 · ReDoS)·auditor Medium 3(ReDoS · structlog 비문자열 · fabrix_proxy 부착 누락) → 해소. 2차 Major 1(헤더 키 값의 `Bearer` 뒤 토큰 잔존 — FabriX `x-openapi-token` 꼴)·auditor Medium 1(structlog 예외 전파) → 해소. 3차 Minor(팬아웃 순환 지수 시간) → 해소. Critical 0.

### 7.6 잔여 (알려진 한계 · 기록만)
- 따옴표 없는 `password = abc`·YAML `password: abc`(공백 규칙의 상충 — `token: expired` 보존과 맞바꿈) · bare 값 종료 문자 `)`·`]`·`,` 뒤 꼬리 · 튜플 쌍 repr(`('x-api-key', 'v')` · httpcore DEBUG 헤더) · `Bearer token: abc`의 `abc` · 스킴 뒤 따옴표 토큰(`x-openapi-token: Bearer 'abc'`) · ODBC `{…}`의 개행·`}}` 꼬리
- URL 비밀번호 안 비인코딩 `/ ? #` · 경로 세그먼트 비밀(`/ws/token/abc`) · `sk-`·`ghp_`·`AKIA`·PEM 본문 · `?sig=`·`?auth=`
- 과잉 가림: `-bearer` 꼴 경로(비 access 로거) · `has_token=` · 셸 `PWD=/home/app` · `token=None`
- `extra=` 필드(팩토리 뒤 대입) · 실패 경고 1회 후 침묵 · 다른 코드가 팩토리를 교체하면 조용히 풀림 · `reload=True` 자식 프로세스의 lifespan 전 로그
- structlog: 공유 참조만으로 깊이 20을 넘는 DAG는 지수 시간(JSONRenderer 자체도 같음 — 메인 판정 「자리표시로 바꾸지 않음」) · 커스텀 Mapping이 접근마다 새 객체를 내면 `seen` id 재사용으로 값 오염(누출 아님 · verifier r4 strict xfail로 고정)
- exc_info 레코드 트레이스백 2회 포맷 비용

---

## 변경 이력

- 2026-10-16 v1.0 — 최초 작성(기록만 · 코드 0). 163 auditor Low 권고를 별건으로 분리 — 사용자 결정 「별도 계획으로 기록만」.
- 2026-10-18 v2.0 — 완료(커밋 전). §4 확정 기록 · §6 W0 실측 · §7 W1 결과(부착·규칙·성능·단언·검증·잔여) · D-339 · 범위 밖에 비로깅 파일 싱크 명시.
