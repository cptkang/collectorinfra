# 135. ITAM 자산 질의 벤치마크 — 사용자 프롬프트 시나리오를 실제 사용자 경로로 돌리고, 생성 SQL·실행 결과·스키마 맥락을 개인정보 없이 기록해 ITAM 프롬프트 개선 근거로 쓴다

> **작성일**: 2026-10-06 · **v1.0** · **v1.1**(2026-10-06 — 사용자 지시 *"테스트 시나리오는 사용자 프롬프트로 진행해야 한다"* 반영: 시나리오 = 사용자 프롬프트 · 실행 = 로그인한 사용자 경로(`/api/v1/query/stream`) · 대상 DB 고정 주입 폐기 · 정답 대조 = 실 DB 오라클 · 게이트 G-7 신설) · **v1.2**(2026-10-06 — 사용자 지시 *"135번 계획을 검토하여 업데이트하고 구현을 시작하라."* · 현 코드 대조 재실측 · 인용 교정 · 판정 방식 교정 · 하네스 변경 2곳 — 변경 이력) · **v1.3**(2026-10-06 — W0~W6 구현 · 진행 기록 §9 · D-301 본문 등재) · **v1.4**(2026-10-06 — 사용자 인터뷰 답 반영: G-1~G-7 확정 · 카탈로그 = 「DB 구조」 탭 산출물 전체 · 폐쇄망 1회차 키트(관찰) · `pii_filter` 이메일 탐색 선형화 · W7 = 스모크 정의만)
> **상태**: **WIP — W0~W6 완료 · W8 1회차 키트 완료(작업 트리 · 커밋 없음)** · 잔여: W7 스모크(사용자 「LLM 보류」 — 정의만) · 내부망 1회차 실행(사용자) → 반출 로그로 정답 SQL(2회차)(§9). 게이트 G-1~G-7 전건 답(2026-10-06 인터뷰 · §6). 결정 **D-301**(§8).
> - **통지(2026-10-06 · `plans/139` W6·W7 · D-308 · D-301 부기 · 상태 불변)**: 1회차 run `20261006-152938`은 ITAM 단일 DB 12턴이 FabriX 입력 한도 초과로 SQL 0건이라 연결 위치를 찾지 못했다 — 139가 정의 기반 선별로 고쳤다. 벤치 변경(설명 출처 Redis → 파일 · DB별 프롬프트 크기·선별 칸 · 되물음 기록 · 분류 `backend_limit`·`selection_none` · §9 지연 문구 평면별 · closed 카나리아 「해당 없음」)과 2회차 관찰 4건(ITAM-115~118)은 §9 「139 연동」 행 · 부록 A 2a·4 참조
> **성격**: 현행 실측 + 설계 + 작업 분해 + 진행 기록.
> **2026-10-06 — D-303 회귀 정책 반영**: 수용 기준 7의 「기존 테스트 전체 통과」 → 모듈 단위 회귀(전체는 사용자 요청 시). 벤치·MLX 절차는 범위 밖이라 그대로 둔다.
> **요청 원문(2026-10-06)**:
> ① *"자산관리 포탈은 itam db를 이용하여 정보를 조회할 수 있다. mcp에 연결된 itam db의 자산정보 관련 정보를 조회하는 테스트 시나리오를 실행하고 생성된 쿼리 실행결과 그리고 스키마 관련된 정보를 로그로 남겨서 itam에서 관리하는 정보와 실행결과를 보고 itam을 이용하는 프롬프트의 성능을 높일 수 있도록 하려고 한다. 벤치마크 테스트 코드 작성 계획을 수립하라. 로그 정보에는 사용자 정보는 마스킹하고 개인정보는 포함되지 말아야 한다. 또한 스키마 정보는 스키마 쿼리 형식이 아닌 다른 방식으로 로그를 남겨야 한다."*
> ② *"테스트 시나리오는 사용자 프롬프트로 진행해야 한다. 사용자 프롬프트에서 예를 들어 "자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘" 등의 프롬프트로 시나리오가 작성되어야 한다."*
> **상위·인접 계획**: `plans/95`(ITAM 연동 — 샌드박스·오라클 3종·G-9 노출 방침) · `plans/133`(스키마 자산 자동 생성 A1~A8 — 이 벤치가 효과를 재는 대상) · `plans/104`(관리자 「DB 구조」 승인본) · `plans/94`·`plans/122`(시나리오 하네스 · 실 DB 읽기 전용 오라클 — 부품 재사용) · `plans/132`(소스 선별 · 되묻기) · `plans/76`(실행 관측 로깅) · `plans/97`(폐쇄망 산출물 반출)
> **관련 결정**: D-003(읽기 전용 3중 방어) · D-127·D-240(실 LLM = 로컬 MLX · 과금 평면 건별 승인) · D-214(ITAM MariaDB 편입) · D-216(하네스 내장 테스트 계정) · D-217(감사 로그 SQL 수집) · D-219(SQL 관측 0 = 사고 · 폐쇄망 산출물 축소 반출) · D-232(사용자별 DB 조회 인가) · D-251(기준 경로 = 2단) · D-275(실 DB 읽기 전용 오라클) · D-294(133 자산) · D-300 ④(식별자 가림 `앞 1자 + ***`)
> **실측 기준**: 2026-10-06 `main` HEAD `da3ea4f` + 작업 트리. `file:line`은 이 시점 값이다. v1.2에서 §2·§3의 인용을 다시 대조했다(어긋난 곳은 본문에서 고치고 「v1.2 교정」으로 표시).

---

## 0. 요약

**목표** — 자산관리 포탈 사용자가 실제로 칠 법한 **사용자 프롬프트**(예: *"자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘"*)로 시나리오를 만들고, **로그인한 사용자와 똑같은 경로**(인증 → `/api/v1/query/stream`)로 보낸다. 턴마다 **①시스템이 만든 SQL ②사용자가 받은 결과 ③LLM에게 제시된 스키마 맥락(구조화 형식)**을 한 레코드로 남기고, **ITAM에 실제로 있는 값(정답 SQL을 읽기 전용으로 돌린 결과)**과 대조해 실패 원인을 분류한다. 분류가 곧 ITAM 프롬프트 자산(133 A1~A8) 중 무엇을 고칠지 가리킨다.

| 만드는 것 | 위치 | 한 줄 |
|---|---|---|
| 벤치 CLI | `scripts/itam_bench/` (신규 패키지) | `python -m scripts.itam_bench --run` — 벤치 서버 기동·로그인·프롬프트 송신·정답 대조·로그 |
| 사용자 프롬프트 시나리오 | `testdata/itam_bench/scenarios.yaml` | 샌드박스 22건(+멀티턴) · 운영 시나리오는 W8 |
| 정답 SQL(오라클) | `testdata/scenarios/oracles/ITAM-NN.mariadb.sql` | 시나리오 하네스 오라클 위치·규약 그대로(읽기 전용 · 행 상한 필수) |
| 컬럼 기록 정책 | `testdata/itam_bench/column_policy.yaml` | 컬럼별로 「값을 로그에 남겨도 되는가」 — **검토된 컬럼만 값 기록, 나머지는 기본 거부** |
| 측정 연결점 | `src/observability/run_capture.py` + 호출 1줄 | 2단 task가 LLM에 준 스키마 맥락을 벤치 서버가 받아 간다(설치 안 하면 아무 일도 안 함) |
| 산출물 | `results/itam_bench/<run_id>/` (git 제외) | `run.json` · `schema_catalog.yaml` · `trace.jsonl` · `report.md` · `leak_check.json` |
| 테스트 | `tests/test_scripts/test_itam_bench_*.py` | 위생·분류·카탈로그·시나리오 로더 단위 테스트 (W7 스모크는 `live_llm` pytest가 아니라 드라이버 `--run --only ITAM-01,ITAM-06,ITAM-16` 1회 — 드라이버가 사다리 단·누출 관문·SQL 관측 턴을 스스로 검사해 exit 1 한다. 세 문항 구성은 `TestW7Smoke`가 고정 — §4 W7) |

**한 줄 권고 ①** — 시나리오 프롬프트에는 **테이블·컬럼·SQL 용어를 넣지 않고 DB도 고정 주입하지 않는다.** 소스 선별 → 스키마 선택 → SQL 생성까지 운영과 같은 길을 타야 「ITAM 프롬프트가 사용자 말을 알아듣는가」를 잴 수 있다.
**한 줄 권고 ②** — 로그 위생은 「찾아서 가리기」가 아니라 **「허락된 것만 남기기(기본 거부)」**다. 운영 ITAM은 108테이블(인사 계열 `TCDMSHR##` 포함)이고 대부분의 컬럼 의미를 아직 모른다. 검토하지 않은 컬럼의 값은 로그에 쓰지 않고, 정답 대조는 메모리에서 끝내 판정과 건수만 남긴다.

---

## 1. 요구 분해와 해석

### 1.1 요구 → 수용 기준

| # | 원문 구절 | 요구 | 수용 기준(§5에서 검증) |
|---|---|---|---|
| R1 | "mcp에 연결된 itam db … 조회하는 테스트 시나리오를 실행" | 운영과 같은 서버 · MCP `itam` 소스로 실행 | 2단 확정 서버에서 전 시나리오 실행 · 조회 DB에 `itam` 포함 여부 기록 |
| **R7** | ②"테스트 시나리오는 사용자 프롬프트로 진행" | 사용자 말투의 프롬프트 · 로그인한 사용자 경로 · 대상 DB 고정 주입 없음 | 프롬프트 린트(§3.2.2) 위반 0 · 요청 본문은 `query`(와 되묻기 응답)뿐 |
| R2 | "생성된 쿼리 실행결과" | 시스템이 실행한 SQL 전문 + 사용자가 받은 결과 | 턴마다 감사 로그의 실행 SQL · `download-csv` 결과 요약(가림 처리) · 실패 사유 |
| R3 | "스키마 관련된 정보를 로그로" | ITAM이 관리하는 정보 목록 + 턴별로 LLM이 본 스키마 맥락 | `schema_catalog.yaml`(run당 1개) + 레코드별 `schema_context` |
| R4 | "itam에서 관리하는 정보와 실행결과를 보고 … 프롬프트의 성능을 높일 수 있도록" | 정답 대조 + 실패 원인 분류 + 고칠 자산 지목 + 개선 전후 비교 | 오라클 판정 · 실패 분류 14종(§3.3) · 분류→자산 대응표 · `--compare` |
| R5 | "사용자 정보는 마스킹하고 개인정보는 포함되지 말아야" | 로그인 계정·실행자 식별자 가림 + 결과 속 개인정보 0건 | 누출 관문(§3.5.5) 통과 · 카나리아 성명 0건 · 위반 시 산출물 미기록 |
| R6 | "스키마 정보는 스키마 쿼리 형식이 아닌 다른 방식으로" | DDL·`information_schema` 조회문이 아닌 구조화 표현 | 스키마 절에 `CREATE TABLE`·`SHOW CREATE`·`information_schema` 문자열 0건 |

### 1.2 해석 — 확인이 필요한 세 가지 (게이트 G-3·G-2로 확정)

1. **"스키마 쿼리 형식이 아닌 방식"** → 스키마를 **테이블·컬럼 목록 형태의 구조화 문서(YAML)**로 남긴다. 예: `TCDMSIF79 → 컬럼 hWSportEndYmd · 의미 "하드웨어 지원 종료일자" · 날짜문자열(YYYYMMDD) · 키 아님`. `CREATE TABLE …` 같은 DDL이나 `SELECT … FROM information_schema …` 같은 조회문은 남기지 않는다. 덧붙여 **벤치는 스키마를 얻으려고 DB에 조회문을 따로 날리지 않는다** — 서버가 이미 쓰는 스키마 캐시와 승인 자산(또는 `scripts/itam_erd.py` 스냅숏 JSON)을 읽는다.
2. **"사용자 정보"** → ①**시나리오를 보내는 로그인 계정** — 하네스 내장 테스트 계정 `user_id`(`scripts/scenario/runner.py:88` — 사번 형태)나 `--user`로 준 계정, 그 JWT, 감사 로그의 사용자 필드 ②**벤치를 돌린 사람·접속 계정** — OS 사용자명, 머신 이름, 홈 디렉터리 경로, DB 접속 계정(운영 `SDQ000` 등)·비밀번호. 가림 규칙은 D-300 ④와 같게(`앞 1자 + ***`) · 토큰은 가린 형태로도 남기지 않는다.
3. **"개인정보"** → ITAM 데이터 안의 사람 정보: 담당자 성명(`rspblPsnEmnm`)·직원번호(`rspblPsnEmpid`)·시스템 사용자번호(`sysRegiUno`·`sysLastUno`)와 운영 108테이블에 있을 연락처·이메일·주소 등. 이 값은 **가린 형태로도 남기지 않는다**(성씨 1자도 · 해시도 — 이름은 경우의 수가 작아 해시가 역산된다).

> **`plans/95` G-9와의 관계** — G-9는 *"조회 결과에 성명·금액을 마스킹 없이 보여준다"*로 확정됐다(2026-09-17). 이것은 **사용자 화면 응답**에 대한 결정이고, 이 계획은 **벤치 로그 파일**에 대한 규칙이다. 제품 응답 동작은 바꾸지 않는다. 바로 그 G-9 때문에 벤치가 받는 결과 CSV에는 성명이 그대로 들어온다 — 이 벤치가 별도 위생 계층을 갖는 이유다.

---

## 2. 현행 실측 (2026-10-06)

### 2.1 재사용할 것 — 사용자 경로 부품은 시나리오 하네스에 이미 있다

| 부품 | 위치 | 쓰는 방법 |
|---|---|---|
| 벤치 서버 기동(리로드 없음 · 프로파일 환경 주입 · 사다리 판독) | `scripts/scenario/server.py:172` **`ServerHandle`**`(profile, env_overrides, port, log_path, mock=False)`(v1.2 교정 — v1.1의 `ServerProcess`는 없는 이름) · `:265` `ladder`(속성 — 서버 로그의 사다리 확정 줄) · `:351` `verify_profile(handle, overrides, admin_token, expected_tier)`(헬스·사다리·설정 에코 3종 대조 — 설정 에코는 운영자 토큰 필요 · `runner.py:1042` `resolve_admin_credentials`) | 프로파일 `tier2_intent`(`config/scenarios/profiles.yaml:91` — 1단 끄고 2단 켬 · `scripts/scenario/catalog.py:345` `load_profiles`)로 기동. **진입 모듈은 `start()`(`:233`)에 하드코딩**(`scripts.scenario._serve`/모의 서버)이라 벤치 진입(§3.6)을 쓰려면 생성자 인자 1개가 필요하다 → 하네스 변경 ②(§3.1) |
| 로그인·질의·결과 수신 | `scripts/scenario/client.py:292` `login` · `:330` `send`(stream 정본) · `:603` `download_csv`(사용자가 받은 결과 행 · 서버가 화면과 같은 규칙으로 마스킹) | 사용자 프롬프트를 그대로 보낸다. 내장 계정은 `runner.py:88` `DEFAULT_USER_ID`(D-216) |
| 계정의 DB 조회 인가 | `GET /api/v1/auth/me`(`src/api/routes/user_auth.py:380` — `allowed_db_ids`·`role`) + `src/routing/db_authz.py:60` `authorized_db_ids`(`None` = 전체 · `[]` = 없음 · 관리자 = 전체) | 사전 점검(M5). 응답의 `username`(성명)은 읽지 않는다 |
| 실행 SQL 수집 | `scripts/scenario/runner.py:1518` `SqlAuditTail` — 서버 감사 로그 `query_executed`를 thread_id로 거른다(D-217). **v1.2 교정**: 수집 항목은 `sql·source·row_count·success·retry_attempt`뿐이고 **실패 사유(`error`)를 버린다** | 2단은 done에 SQL을 싣지 않으므로 이 경로가 유일하다. `dialect_error` 판정에 오류 문구가 필요해 벤치가 하위 클래스로 `error`까지 읽는다(위생 §3.5.3 거침 · 러너 산출물 불변) |
| 실 DB 읽기 전용 오라클 | `scripts/scenario/oracle.py:505` `run_oracle`(정본 SQL · SELECT 전용 검사 · 행 상한 필수 · 직렬 · 타임아웃 · 행 원문 미기록 — 단 `log_path`에 **오라클 SQL·사유를 적는 `oracle_log.jsonl`**을 남긴다 → 벤치는 세션 임시 디렉터리로 보낸다) · `:797` `evaluate_oracle`(count·keyset·rowset·argmax·value · 불일치 상세는 키만 — **단 argmax 상세 `oracle_top`·`value_diffs`와 value 상세는 값 열의 값을 싣는다**) · `render_sql`(날짜 자리표) | 「ITAM이 관리하는 정보」와 「사용자가 받은 결과」의 대조 그 자체. **MariaDB 접미사만 추가**(`:102` `ENGINE_SUFFIX`에 `mariadb` — 행 상한은 이미 `LIMIT` 분기 `:242` · 엔진은 레지스트리 `config/db_registry.yaml:558` `engine: mariadb`). 판정 상세는 기록 전에 컬럼 등급으로 다시 거른다(§3.5.6) |
| 활성 소스·과금 판정 | `scripts/scenario/runner.py:453` `resolve_active_sources` · `scripts/scenario/preflight.py:49` `external_planes` · `:244` `mlx_run_blockers` | `itam`이 활성인지 · 두 평면이 비과금인지 먼저 본다(D-240) |
| 파일 스키마 캐시 | `src/schema_cache/persistent_cache.py` `PersistentSchemaCache` — `.cache/schema/itam_schema.json`(실측: 2테이블 77컬럼 · `sample_data` 0행 · `_descriptions` 필드 · 타입은 길이 없는 `char`) | 카탈로그 기본 입력(§3.4) — 파일을 읽을 뿐 DB를 조회하지 않는다 |
| 정규식 개인정보 규칙 | `src/security/pii_filter.py:365` `scrub_pii` · `:288` `scan_pii`(연락처·계좌·이메일·주민번호·카드) | 자유 문구 위생 + 누출 관문의 정규식 단계 |
| 비교 리터럴 추출 | `src/utils/synonym_usage.py:218` `_extract_column_literals`(`col = '…'`·`LIKE`·`IN (…)`) | SQL 리터럴 가림(§3.5.3) |
| SQL 테이블 추출 | `src/sql_validation.py:440` `_extract_table_names` | 사용 테이블 분석(백틱은 먼저 벗긴다 — 식별자 패턴이 `[\w]+`) |
| 스키마 스냅숏(데이터 0행) | `scripts/itam_erd.py:176` `build_snapshot` | 운영 카탈로그 입력(`--schema-snapshot`) · **비SQL 스키마 표현의 선례** |
| 산출물 git 제외 | `.gitignore:63` `results/` | 산출물 위치 `results/itam_bench/` |

### 2.2 그대로 쓰면 개인정보·사용자 정보가 새는 곳

| # | 지점 | 실측 | 이 벤치의 처리 |
|---|---|---|---|
| L1 | **시나리오 러너 `raw.jsonl`** | `scripts/scenario/runner.py:801` 응답 원문(`response_text`, 4,000자)을 위생 없이 기록 · 판정 재료(재판정)라 러너에서 빼기 어렵다 | **ITAM 시나리오를 시나리오 러너로 돌리지 않는다.** 부품(§2.1)만 쓰는 별도 드라이버 · 시나리오 파일도 러너가 읽는 `testdata/scenarios/*.yaml` 밖에 둔다(실수로 섞여 돌지 않게) |
| L2 | **서버 표준출력 로그** | 감사 줄(`query_executed` — 사용자 ID·SQL)·앱 로그가 섞인다. 하네스는 run 디렉터리 `logs/server-*.log`로 남긴다 | 벤치는 세션 임시 디렉터리에 두고 SQL 수집·사다리 판독에만 쓴 뒤 **실행 종료 시 지운다** — 산출물이 아니다 |
| L3 | **LLM에 주는 스키마에 실 데이터 표본이 섞인다** | `src/nodes/schema_analyzer.py:323`·`:789` `sample_data` — ITAM이면 담당자 성명·직원번호 | 스키마 맥락은 **이름·의미 유무만** 수집하고 `sample_data`는 "있었다/없었다" 불린 |
| L4 | **task 상태에 요청 사용자 정보가 실린다** | `src/orchestration/subagents.py:1137-1139` 격리 상태에 `thread_id`·`user_id`·`user_department` | 측정 연결점의 수신 측은 `thread_id`(결합 키)만 복사하고 사용자 필드는 읽지 않는다 |
| L5 | 기본 마스킹 대상이 개인정보가 아니다 | `src/config.py:470` `sensitive_columns`는 비밀·토큰 계열 · `src/security/data_masker.py:97`은 **컬럼명 부분 일치**(→ `sevrHostName`의 `Name` 같은 오탐 구조 · `docs/18_known_mistakes.md` 2026-09-11 사례) | 카탈로그 분류(§3.5.1)로 판정 |
| L6 | 정규식 규칙에 **성명 규칙이 없다** | `pii_filter.PII_RULES`는 연락처·계좌·이메일·주민번호·카드 | 성명은 **컬럼 분류 + 실제 결과에서 모은 값 대조**로 막는다 |
| L7 | DSN 가림이 계정명을 남긴다 | `scripts/itam_erd.py:89` `mask_dsn`은 비밀번호만 가림 | 계정·비밀번호 둘 다 가린다 |

### 2.3 측정이 닿지 못하는 곳

| # | 실측 | 영향 |
|---|---|---|
| M1 | 2단 task 결과에 스키마 맥락이 없다 — `src/orchestration/subagents.py:1788` `_pack_pipeline_result`가 결과·SQL만 접는다. 스키마 분석은 `:909`에서 task 내부 상태로만 존재하고, 사용자 경로에서는 **서버 프로세스 안**에 있다 | 「정답 테이블을 LLM이 봤는데 틀렸나 / 아예 못 봤나」를 구별 못 한다 → §3.6 측정 연결점 |
| M2 | 단계 트레이스는 값을 일부러 안 담는다 — `src/observability/trace_collector.py:233` `_summarize_delta` | 재사용 불가(설계 의도가 반대) |
| M3 | 시나리오 오라클의 엔진 접미사가 PG·DB2뿐 — `scripts/scenario/oracle.py:102` | MariaDB 정본 파일을 못 읽는다 → 접미사 1줄 추가(W4) |
| M4 | 로컬 `.env`는 사다리 1·2·3단 플래그가 모두 true | 프로파일 `tier2_intent` 주입 + `ServerProcess.ladder`로 확정 단 검사, 불일치면 중단 |
| M5 | 사용자별 DB 조회 인가(D-232) — 벤치 계정이 `itam`을 조회할 권한이 없으면 전 시나리오가 「권한 없음」으로 끝난다 | 사전 점검에서 계정 권한 확인 · 없으면 실행 0·사유 출력 |
| M6 | **(v1.2 실측)** `_pack_pipeline_result`를 쓰는 곳은 2단 핸들러(`subagents.py:1720`·`:1732`)와 3단 **계획 루프** task 서브그래프(`tier3_plan.py:400` — `TIER3_PLAN_LOOP_ENABLED`)뿐이다. 3단 기본 경로(라우터 → `schema_analyzer` 그래프 노드)는 이 함수를 지나지 않는다 | 연결점은 2단 전부 + 3단 계획 루프만 덮는다. 3단 기본 경로 run은 `schema_context: null`로 남고 `schema_miss`·`meaning_absent`는 「판정 불가」로 집계한다(리포트 고지) — 노드 쪽 연결점 추가는 비범위 |
| M7 | **(v1.2 실측)** 2026-10-06 로컬 상태 — 두 평면 `mlx`(`python -m scripts.bench --show-env`) · `ACTIVE_DB_IDS=polestar,itam` · `DB_BACKEND=dbhub` · **Docker 데몬 꺼짐**(ITAM 3307·PG·Redis 없음) · MCP 9099 미기동 · MLX 8080 기동 중 | W7 실측은 Docker·MCP 기동이 전제 — 같은 장비 다른 세션 사용 여부를 먼저 본다 |

### 2.4 ITAM 쪽 사실

- **로컬 샌드박스**(`testdata/itam/`): MariaDB 11.4 · 3307 · `INST1` · 테이블 2개(`TCDMSIF80` 68컬럼 30행 · `TCDMSIF79` 9컬럼 29행) · **컬럼 주석(COMMENT) 0건** · 합성 성명 3종(`generate_init.py:141`)·합성 직원번호 `T000000…T000029`(`:210` — `T{i:06d}`)·감사 사용자번호 `T000000` — **누출 카나리아로 바로 쓸 수 있다.** 사람이 쓴 오라클 SQL 3종이 있다(`testdata/itam/README.md` ①지원 종료 6개월 ②이번 분기 유지보수 만료 ③경과년수 5년 이상).
- **(v1.2 실측) 코드 컬럼은 생성기가 강제로 표식을 넣는다** — `_fill`(`generate_init.py:240`)이 `code:`가 붙은 컬럼에 `Z`·`Z9…`를 쓰고 `test_code_columns_are_synthetic_markers`가 고정한다. 가상화 여부(`vrtlSevrMapngYn` — 코드 도메인 「여부」·값 목록 미확보 G-5)를 `Y`/`N`으로 채우면 **코드값을 지어내는 것**이 된다 → 시드 보강에서 뺀다(ITAM-21은 `observe`).
- **★샌드박스로는 사용자 프롬프트 다수를 답할 수 없다** — 시드 기준값(`generate_init.py:200-226` `_base_main`)이 CPU 8·메모리 64·스토리지 500 고정이고, 운영체제·모델·가상화 여부·**서비스명은 비어 있다.** *"운영체제별 서버 수"* · *"메모리가 제일 큰 서버 5대"*는 값이 같거나 비어서 정답 대조가 성립하지 않고, *"통합인증 서비스의 서버리스트"*는 **서비스와 서버를 잇는 데이터가 샌드박스에 없다.** → W0에서 **기존 2테이블의 기존 컬럼에만** 합성 값을 넣어 보강한다(새 테이블·새 컬럼 금지 — 전사본에 없는 구조를 지어내지 않는다 · `testdata/itam/schema.yaml` 머리 주석).
- **서비스명이 ITAM 어디에 있는지 모른다** — 저장소 전체에서 「통합인증」 0건 · ITAM 서비스/업무명 출처 0건(`plans/130` §1이 *"자산관리 스키마의 「업무」는 문서 이름이라 서버 업무명의 출처가 아니다"*라고 기록). 운영 108테이블 중 어느 테이블이 서비스↔서버를 잇는지는 운영 스냅숏(`itam_erd`)이나 포탈 화면으로 확인해야 한다 → **게이트 G-7**.
- **운영**: 테이블 108개, 접두 6군(`TCDMSAM`·`BR`·`CM`·`GT`·`HR`·`IF` — `plans/95` G-4 v15) · database `INST1` · SELECT 전용 계정·방화벽 확인(`plans/95` G-2·G-3 ✅).
- **현재 ITAM 프롬프트 자산의 상태**: `config/db_profiles/itam.yaml`(104 로컬 승인본 · 20줄)의 `query_guide`는 *"감지할 수 있는 특수한 구조적 패턴은 없습니다"*라는 LLM 서술문이고 실제 규칙(날짜 문자열 · 3열 복합 조인 · 방언)은 0건이다. `config/knowledge/itam/`·`config/synonym_seeds/itam.yaml`은 없다. → **이 벤치의 첫 run이 "자산 없는 상태"의 기준선**이 되고, 133 자산 승인 뒤 run과 비교하면 효과가 숫자로 나온다. **(v1.2 실측)** 파일 스키마 캐시의 컬럼 설명은 9건(`TCDMSIF79` 전 컬럼 · LLM 생성 캐시 설명)이다. 프로필 머리 주석에 **승인자 칸**(`… · anonymous · env=…`)이 있다 — 운영에서는 사람 ID가 될 수 있으므로 카탈로그는 머리 주석을 옮기지 않고 지문·`source`·`environment`만 남긴다.
- **방언 함정(샌드박스 리허설 · `testdata/itam/README.md`)**: `::numeric`·`INTERVAL '1 day'`는 구문 오류, **`||`는 OR로 해석돼 0, `"col"`은 문자열 리터럴로 해석돼 0행** — 오류 없는 오답이라 정적 검출이 필요하다.

---

## 3. 설계

### 3.1 구성과 흐름

```
python -m scripts.itam_bench --run [--env sandbox|closed] [--profile tier2_intent|tier3_router]
                                    [--repeat N] [--only ID,...] [--user ID --password PW]
                                    [--schema-snapshot itam_schema.json]
  │
  ├─ 사전 점검   과금 평면 0 · MLX 생성 가능 · itam 활성 · 시나리오·프롬프트 린트 · 컬럼 정책 로드
  ├─ 벤치 서버   ServerProcess(프로파일 주입 · 진입 = scripts.itam_bench._serve → 측정 수신기 설치)
  │              → 사다리 확정 단 확인 · 로그인 · 계정의 itam 조회 권한 확인
  ├─ 카탈로그     스키마 캐시(또는 스냅숏) + 승인 자산 → ITAM 정보 목록(구조화) ─────────────▶ schema_catalog.yaml
  ├─ 시나리오     턴마다 ┌ 감사 로그 위치 표시 → 사용자 프롬프트 송신(stream) → 되묻기면 선언된 응답 턴
  │   루프              ├ 결과 수신(download-csv · 메모리) · 실행 SQL 수집(감사 로그) · 스키마 맥락 결합(thread_id)
  │                     ├ 오라클 실행(MCP itam · 읽기 전용) → 대조 · SQL 분석 · 실패 분류
  │                     └ 위생 처리(기본 거부) ───────────────────────────────────────────────▶ trace.jsonl(임시)
  ├─ 누출 관문    카나리아 · 수집한 개인정보 값 · 정규식 규칙 · 스키마 형식 · 사용자 정보 ── 실패 시 미기록, exit 1
  ├─ 리포트       점수 · 분류 · 고칠 자산 · 분류 필요 컬럼 ─────────────────────────────────▶ report.md · run.json
  └─ 정리         서버 종료(자기 PID만) · 서버 원시 로그·측정 수신 임시 파일 삭제
python -m scripts.itam_bench --compare <run_a> <run_b>     # 개선 전후 시나리오별 전이 (LLM·DB 0)
python -m scripts.itam_bench --check-oracle                # 정답 SQL만 실행해 오라클 점검 (LLM 0)
python -m scripts.itam_bench --dry-run                     # 시나리오·프롬프트 린트·실행 계획만 (LLM·DB 0)
```

**모듈** — 단일 사용 코드에 추상화를 만들지 않는다.

| 모듈 | 책임 |
|---|---|
| `__main__.py` | CLI · 사전 점검 · 서버 기동/종료 · 시나리오 루프 · 원자 기록 |
| `_serve.py` | 벤치 서버 진입 — 측정 수신기(이름만 기록)를 설치하고 `scripts/scenario/_serve.py`와 같은 방식으로 앱을 띄운다 |
| `catalog.py` | 시나리오·컬럼 정책 로드·검증 · 프롬프트 린트 · 스키마 카탈로그 생성 |
| `judge.py` | 오라클 호출·대조 · SQL 분석(테이블·컬럼·방언 함정) · 실패 분류 |
| `redact.py` | 값 기록 정책 · SQL 리터럴·오류 문구 가림 · 계정·실행자·DSN·경로 가림 · 누출 관문 |
| `report.py` | `report.md` · `--compare` |

`scripts/scenario/runner.py`는 고치지 않는다(§2.2 L1). 하네스 쪽 변경은 **두 곳**이다(v1.2 교정 — v1.1은 ①만 적었다): ① `oracle.py` `ENGINE_SUFFIX`에 `mariadb` 1줄(W0 — 오라클 정본 검증이 W0에서 필요해 W4에서 앞당김) ② `server.py` `ServerHandle`에 진입 모듈 키워드 인자(기본값 = 현행 `scripts.scenario._serve` — 기존 호출부 동작 불변 · W4). 진입 모듈이 `start()`에 하드코딩돼 있어 ②가 없으면 벤치가 `start()`를 통째로 복제해야 한다.

### 3.2 사용자 프롬프트 시나리오

#### 3.2.1 형식 (`testdata/itam_bench/scenarios.yaml`)

시나리오 하네스 카탈로그의 `turns`·`send`·`oracle` 모양을 그대로 따른다(`testdata/scenarios/_schema.yaml`) — 익숙한 형식이고 오라클 판정기를 그대로 쓸 수 있다.

```yaml
version: 1
scenarios:
  - id: ITAM-07
    title: "이번 분기 유지보수 계약 만료 서버 (오라클 ②)"
    category: maintenance          # 아래 범주 표
    env: [sandbox]                 # sandbox | closed
    traps: [date_text]             # 이 시나리오가 재는 함정(리포트 집계용)
    turns:
      - send: {query: "이번 분기에 유지보수 계약이 끝나는 서버 있어?"}
        expect:
          db_ids: [itam]           # 조회 DB에 itam이 들어갔는가(라우팅은 따로 분류)
          oracle:                  # 하네스 오라클 명세 그대로 — 정본 testdata/scenarios/oracles/ITAM-07.mariadb.sql
            id: ITAM-07
            db_ids: [itam]
            compare: keyset
            key: [[sevrHostName, 호스트명, 서버명, hostname, 서버]]   # 결과 열 이름 후보(사용자 결과는 별칭이 제각각)
            match: equal
    key_columns: [manmenCtrcEndYmd]   # 정답에 꼭 필요한 컬럼(분석용 · 프롬프트에는 안 나간다)
    gold_tables: [TCDMSIF80]
```

- **판정 방식** — `oracle`(정답 대조) 또는 `observe: <무엇을 보는가>`(정답이 데이터로 정해지지 않는 경우 — 범위 밖 질문 · 샌드박스에 데이터가 없는 질문). `observe`도 SQL·결과·스키마 맥락은 똑같이 남는다.
- **오라클 키는 `general` 등급 컬럼만** — 불일치 상세가 키 값을 싣기 때문이다(`oracle.py` `evaluate_oracle` — "값은 키만"). 사람 이름 컬럼을 키로 쓰는 명세는 로더가 거부한다. (v1.2) 키 참조의 별칭 후보 중 카탈로그 컬럼 이름이 최소 하나 있어야 하고, 들어 있는 카탈로그 컬럼은 전부 `general`이어야 한다. 값 참조(`value`)는 `general`·`amount`만 받는다.
- **되묻기** — 시스템이 되물을 것으로 예상되면 응답 턴을 선언한다(`send: {selected_db_ids: [itam]}` 등 하네스와 같은 구조화 응답). 선언 없이 되물으면 자동으로 답하지 않고 `asked_back`으로 기록하고 끝낸다 — 되물음 자체가 프롬프트·라우팅 품질 신호다.
- **정답 SQL 날짜** — 샌드박스 시드 날짜가 init 시점 상대값이므로 오라클 SQL은 `CURDATE()` 기준으로 쓰거나 하네스 자리표(`render_sql`)를 쓴다. 기대값을 파일에 고정하지 않는다.

#### 3.2.2 프롬프트 작성 규칙 (로더가 린트로 강제)

1. **사용자가 채팅창에 치는 말 그대로** — 반말·존댓말·축약("서버리스트", "몇 대야?")을 섞는다.
2. **테이블·컬럼·SQL 용어 금지** — 카탈로그의 테이블·컬럼 식별자(`TCDMSIF80`·`manmenCtrcEndYmd` 등)와 `SELECT`·`JOIN`·`WHERE`·`조인`·`컬럼`·`테이블` 같은 말이 들어가면 로더가 거부한다.
3. **업무 말** — 서비스·시스템 이름, 서버리스트, 담당자·담당 부서, 유지보수 계약, 지원 종료(EOS/EOL), 노후·도입 연수, 취득금액, 자산 상태, 가상/물리, 사양.
4. **소스 지칭 변형을 짝으로** — 같은 의도를 「자산관리 시스템에서 …」·「ITAM에서 …」·생략형으로 나눠, 소스 선별 실패(`routing_miss`)와 ITAM 프롬프트 실패를 갈라 본다.
5. **사람 이름·사번을 프롬프트에 넣지 않는다** — 프롬프트는 로그에 그대로 남으므로 그 자체가 개인정보 통로가 된다(카나리아·`scan_pii`로 검사). 「사람 이름으로 담당 서버 찾기」는 비범위(§7).
6. **멀티턴 포함** — 후속 질문("그 서버들 지원 종료일도 알려줘")으로 승계 동작을 함께 본다.

#### 3.2.3 시나리오 초안

**샌드박스(22건 · W0에서 확정)** — ★표는 W0 시드 보강 후에 답이 정해지는 건이다.

| ID | 사용자 프롬프트 | 의도 | 판정 | 재는 함정 |
|---|---|---|---|---|
| ITAM-01 | 자산관리 시스템에서 통합인증 서비스의 서버리스트를 보여줘 | 서비스 → 서버 | **observe**(샌드박스에 서비스 데이터 없음 — 없는 정보를 꾸며내지 않는가 · 어느 컬럼으로 찾으려 했는가) · 운영은 keyset(G-7) | 서비스 개념 대응 |
| ITAM-02 | 자산관리 시스템에 등록된 서버 목록 보여줘 | 전체 목록 | count | 기본 조회 |
| ITAM-03 | 자산관리에서 svr-web-01 서버 정보 알려줘 | 단건 상세 | keyset | 축약 컬럼명 해석(주석 0건) |
| ITAM-04 | SVR-WEB-02 서버 자산 정보 조회해줘 | 단건(대소문자 변형) | keyset | 호스트 키 대소문자 |
| ITAM-05 | svr-web-03 서버 시리얼 번호 알려줘 | 단건(FQDN 저장) | keyset(키 = 시리얼 번호 — v1.2: 묻는 값이 시리얼이라 호스트 열이 결과에 없을 수 있다) | 호스트 키 FQDN |
| ITAM-06 | svr-db-03 담당자가 누구야? | 담당자 | count(v1.2: 결과가 담당자 열만 낼 수 있고 사람 열은 키가 될 수 없다) | **결과에 성명 — 누출 관문 카나리아** |
| ITAM-07 | 이번 분기에 유지보수 계약이 끝나는 서버 있어? | 계약 만료 | keyset(오라클 ②) | **CHAR(8) 날짜 문자열** · 분기 경계 |
| ITAM-08 | ITAM에서 유지보수 계약 끝난 지 얼마 안 된 서버 보여줘 | 모호한 기간 | observe(되묻는가 · 어떤 기간을 가정하는가) | 모호성 처리 |
| ITAM-09 | 6개월 안에 지원 종료(EOS)되는 서버 알려줘 | EOS(생략형) | keyset(오라클 ①) | **3열 복합 조인** · OR |
| ITAM-10 | 자산관리 시스템에서 하드웨어 지원이 이미 끝난 서버는? | 지원 종료 경과 | keyset | 과거 날짜 비교 |
| ITAM-11 | 도입한 지 5년 이상 된 노후 서버 리스트 보여줘(v1.2: 「넘은」은 초과로 읽혀 경계 포함 오라클 ③과 어긋난다) | 노후 | keyset(오라클 ③) | 경계 포함 · NULL |
| ITAM-12 | 서버별 담당 부서 알려줘 | 담당 부점 | count | 부점(조직) ≠ 개인 |
| ITAM-13 | 자산관리에서 취득금액 제일 큰 서버 3대랑 금액 보여줘 | 금액 상위 | argmax | 금액(로그는 집계만) |
| ITAM-14 | 전체 서버 취득금액 합계 얼마야? | 금액 합계 | value | 집계 |
| ITAM-15 | svr-was-06 지원 종료일 알려줘 | 짝 없는 행 | observe(「정보 없음」을 말하는가) | `TCDMSIF79` 미등록 |
| ITAM-16 | 자산관리 시스템에서 호스트명이랑 IP를 '호스트(IP)' 형태로 붙여서 보여줘(v1.2: 소스 명시 — 관측 DB 와 겹치는 질문) | 문자열 결합 | rowset | **`\|\|` 침묵 오답** |
| ITAM-17 | 자산관리에서 지난주 CPU 사용률 추이 보여줘 | 범위 밖 | observe(스냅숏 값으로 추이를 꾸며내지 않는가) | T5 경계 |
| ITAM-18 ★ | 자산관리 기준으로 메모리가 제일 큰 서버 5대 | 사양 상위 | argmax | 정렬+행 제한 |
| ITAM-19 ★ | 자산관리에서 CPU가 16개 이상인 서버 몇 대야?(v1.2: 컬럼 정의가 「CPU 개수」 · 소스 명시) | 사양 조건 | value(`n`) + 행 목록이면 count(v1.2: `count`는 결과 **행 수**를 비교해 「몇 대」 답 1행이 불합격이 된다) | 수치 비교 |
| ITAM-20 ★ | 자산관리 시스템에서 운영체제별 서버 수 알려줘(v1.2: 소스 명시) | 분포 | rowset(v1.2: 분포는 스칼라가 아니다 — `value`는 1행 전용) | 그룹 집계 |
| ITAM-21 | 자산관리에서 가상 서버로 등록된 서버 목록 보여줘 | 가상화 | **observe**(v1.2: 가상화 여부는 코드 컬럼 · 값 목록 미확보 G-5 — 시드에 넣으면 코드값을 지어낸다. 코드값을 지어내 비교하는가를 본다) | 여부 코드값 |
| ITAM-22 | (1턴) 이번 분기에 유지보수 계약 끝나는 서버 보여줘 → (2턴) 그 서버들 지원 종료일도 알려줘 | 멀티턴 승계 | 2턴 keyset | 승계 + 조인 |

**운영(W8)** — 서비스·시스템 단위 질문이 중심이다(*"통합인증 서비스의 서버리스트"*, *"인터넷뱅킹 시스템 서버 중 EOS 도래 서버"*, *"○○ 서비스 서버 담당 부서"* 등). 서비스↔서버 연결 위치(G-7)와 포탈 사용자들의 실제 질문(G-1)을 받아 쓴다.

### 3.3 실행·채점·분석

**경로 고정** — 벤치 서버를 프로파일 `tier2_intent`(`ENABLE_DEEPAGENTS_PACKAGE=false` · `ENABLE_INTENT_ORCHESTRATION=true`)로 띄우고, `ServerProcess.ladder`가 요청 단(`intent_orchestration` 기본 · `--profile tier3_router`면 `semantic_router` 비교 arm)이 아니면 **실행하지 않고 중단**한다. 확정 단은 `run.json`에 남긴다.

**과금 게이트** — 두 평면(워커·오케스트레이터)이 비과금(`mlx`·`fabrix`·`ollama` / `mlx`·`vllm`)일 때만 실행한다. 하나라도 과금 평면이면 실행하지 않는다(`RUN_E2E=1` + 건별 승인 전에는 열지 않음 — D-127). MLX면 `mlx_run_blockers()`로 실제 생성 가능 여부를 먼저 본다.

**턴 처리**(반복 `--repeat N`, 기본 1):
1. 감사 로그 위치 표시(`SqlAuditTail.mark`) → 프롬프트 송신(stream · 시나리오마다 새 `thread_id`)
2. done 수신: 상태(answer·clarification·error) · 조회 DB(`db_scope`) · 고지 코드(`disclosures`의 종류만 — 문구 제외)
3. 결과 행 수신(`download_csv` — 메모리) · 실행 SQL 수집(`SqlAuditTail.collect`) · 스키마 맥락 결합(측정 수신 파일에서 같은 `thread_id`)
4. 오라클 실행(`run_oracle` — 계측 밖 · 직렬) → `evaluate_oracle`
5. SQL 분석 → 실패 분류 → 위생 처리 → 레코드

**판정 어댑터(v1.2 — 하네스 판정기는 그대로 두고 벤치가 입력만 맞춘다)**
- **1×1 값 열 이름** — `value` 비교에서 시스템 결과가 1행 1열인데 열 이름(시스템이 붙인 별칭)이 명세 후보에 없으면, 그 열을 명세 첫 후보 이름으로 바꾼 사본으로 판정한다(합계·건수 질문은 별칭이 제각각이다 · ITAM-14·19).
- **건수 질문의 목록 응답** — 시나리오 `expect.count_rows_ok: true`이고 오라클이 `n` 1행인데 시스템이 여러 행(목록)으로 답했으면 같은 오라클을 `count`(행 수)로 판정한다(ITAM-19). 어느 쪽으로 판정했는지 레코드에 남긴다.
- **실행 SQL 수집** — `SqlAuditTail` 하위 클래스로 감사 줄의 `error`까지 읽는다(§2.1). 오류 문구는 레코드에 쓰기 전에 §3.5.3 가림을 거친다.

**SQL 분석(결정적 · 파서 없음)** — 문자열 리터럴·주석을 지운 뒤
- 사용 테이블: `_extract_table_names`(백틱 제거 후)
- 사용 컬럼: 카탈로그 컬럼명의 단어 경계 일치(대소문자 구분 — 샌드박스 `lower_case_table_names=0`)
- 방언 함정(샌드박스 리허설로 **실측 확인된 4종만** — 추정 패턴으로 오탐을 만들지 않는다): `||` · 큰따옴표 식별자 · `::타입` 캐스트 · `INTERVAL '<n> <단위>'`

**실패 분류 → 고칠 곳** (판정 규칙은 전부 결정적 · 한 턴에 여러 개 가능 · 리포트는 첫 원인 기준으로도 집계)

| 분류 | 판정 규칙 | 고칠 곳 |
|---|---|---|
| `routing_miss` | 조회 DB에 `itam` 없음 | ITAM 프롬프트 밖(`plans/132` 소스 선별) — **분리 집계** |
| `asked_back` | 선언 없는 되물음으로 끝남 | 프롬프트 모호성 · 132 되묻기 규칙 — 분리 집계 |
| `schema_miss` | 정답 테이블이 LLM에 제시된 테이블(`schema_context.presented_tables`)에 없음 | 133 A2 조회 대상 · A1 관계 |
| `column_misread` | 정답 테이블은 제시됐는데 `key_columns`를 안 쓰거나 다른 컬럼을 씀 | 133 A3 컬럼 설명·유사어 |
| `meaning_absent` | `key_columns` 중 제시 시점에 의미(주석·설명)가 없던 컬럼이 있음 — `column_misread`의 원인 표지 | 133 A3 |
| `date_text` | CHAR(8) 날짜 컬럼에 날짜 함수 비교·DATE 리터럴 직접 비교 | 133 A6 쿼리 규칙 |
| `join_key_partial` | `TCDMSIF80`↔`TCDMSIF79` 조인 조건이 정답 조인 키 3열 미만 | 133 A1 · A6 |
| `dialect_error` | 실행이 MariaDB 구문 오류로 실패 | 133 A8 DB 전용 프롬프트 섹션 · 프로필 방언 규칙 |
| `dialect_silent` | 방언 함정 4종 정적 검출(실행은 성공) | 133 A8 · 반복되면 결정적 교정 후보(`plans/95` §4.2) |
| `code_value` | 코드 컬럼 비교값이 카탈로그 `code_values`(133 A4)에 없음 — A4 미승인이면 판정 보류 | 133 A4 |
| `key_mismatch` | 오라클 keyset·count·argmax·value 불일치(위 원인에 안 걸리는 나머지) | 수동 검토 |
| `no_sql` | 실행 SQL 0건으로 답함 | 133 A8 · 프로필 `query_guide` |
| `fabricated` | `observe` 범위 밖·데이터 없음 시나리오에서 값을 지어내 답함(SQL 없이 수치·목록 제시 · 무관 컬럼으로 대체) | 133 A8 · 고지 규칙 |
| `permission_denied` | 계정 인가(D-232)로 거부 | 벤치 환경 문제 — 판정 제외 |

**지표** — 오라클 통과율(전체·범주별) · 실행 성공률 · 재시도 수 · 테이블 정밀도/재현율 · 핵심 컬럼 적중률 · **정답 테이블 제시율**(스키마 선택 품질) · **핵심 컬럼 의미 보유율**(설명 자산 품질) · 소스 선별률(`db_ids`에 itam) · 되물음률 · 결정성(`--repeat`>1일 때 시나리오별 서로 다른 SQL 수) · 지연(참고값 — MLX 지연으로 성능 결론을 내지 않는다, D-240). **SQL 관측 턴이 0이면 run을 판정하지 않고 exit 1**(D-219 ③).

### 3.4 스키마 로그 — 조회문이 아닌 구조화 카탈로그

**두 층으로 남긴다.**

**(1) ITAM이 관리하는 정보 — `schema_catalog.yaml`(run당 1개)**

```yaml
db_id: itam
source: schema_cache            # schema_cache | snapshot:<파일명> | transcript — 벤치가 스키마 조회문을 따로 실행하지 않는다
assets:                         # 이 run에 적용된 ITAM 프롬프트 자산 지문(개선 전후 비교의 기준)
  profile: {fingerprint: 3f2a9c01b7de, source: manual, environment: local_sandbox}   # 머리 주석(승인자 칸)은 옮기지 않는다
  prompt_template: null         # config/knowledge/itam/prompt_template.yaml 없음
  synonym_seeds: null
  column_descriptions: 0        # 의미가 있는 컬럼 수
tables:
  TCDMSIF79:
    meaning: "서버 하드웨어·소프트웨어 지원 종료일"
    meaning_source: none        # db_comment | cache_description | none
    rows_estimate: 29
    key: [groupCoCd, sevrHostName, iPCtnt]
    relations:
      - {to: TCDMSIF80, on: [groupCoCd, sevrHostName, iPCtnt], kind: inferred_key_match}
    columns:
      - {name: hWSportEndYmd, meaning: null, value_kind: date_text_yyyymmdd, type: "CHAR(8)", nullable: true, log_policy: general}
      - {name: sysRegiUno,    meaning: null, value_kind: code_text,          type: "CHAR(7)", nullable: false, log_policy: pii}
```

- `value_kind`는 원 타입을 질문 작성·분석에 쓰는 말로 옮긴 것이다(`date_text_yyyymmdd`·`datetime_text`·`number`·`amount`·`code_text`·`flag_yn`·`free_text`·`identifier`).
- **(v1.2) 입력 출처 3종** — `schema_cache`(기본 · `.cache/schema/itam_schema.json` 파일 캐시를 읽는다 — 타입에 길이가 없어 `value_kind`는 이름 토큰(`Ymd`·`YMS`·`Amt`·`Yn` 등)으로 보강) · `snapshot:<파일>`(`scripts/itam_erd.py` 스냅숏 — `column_type`에 길이가 있다 · 운영 W8) · `transcript`(샌드박스 전사본 `testdata/itam/schema.yaml` — 캐시가 없을 때 · DDL의 원천이라 조회문 없이 같은 구조를 준다). 의미 출처는 `db_comment`(스냅숏 주석) · `cache_description`(파일 캐시 `_descriptions` — LLM 생성 설명이라 「승인」과 구별) · `none`. 관계는 `itam_erd.infer_relations`를 재사용한다(동일 기본키 군은 `same_key_groups`).
- **(v1.2) 자산 지문**은 파일 내용 해시 앞 12자 + `source`·`environment` 키만 남긴다 — 프로필 머리 주석에는 승인자 칸이 있다(§2.4).
- **(v1.4) 「DB 구조」 탭 산출물 전체**(G-7 답 — 내부망 정의서 = 이 탭이 만든 스키마). 실측: 탭의 「등록(schema)」·「DDL 등록」이 `.cache/schema/{db_id}_schema.json`(`persistent_cache.py:149` — 컬럼 `name·type·nullable·primary_key·foreign_key·references` · `row_count_estimate` · `sample_data`(빈 목록) · `schema.relationships`(선언 FK — `dbhub/client.py:593`·`:631`) · `_descriptions` · `_synonyms` · `_db_description`·`_db_description_origin`(`cache_manager.py:538`))을 쓰고, 「구조 분석」 승인본은 `config/db_profiles/{db_id}.yaml`(patterns·query_guide·code_values + 자산 키 allowed_tables·entity_keys·relationships·query_rules·query_examples·code_labels — `profile_merge.py:46`)과 `.cache/structure/{db_id}/versions/vN.yaml`(승인자 `by` 포함 — `structure_store.py:475`)에 쌓인다. 스키마·프로필을 내려받는 API 는 없다. 카탈로그 반영:
  - 관계 3종 — `declared`(선언 FK · `schema.relationships`·컬럼 `references` 합집합) · `inferred`(기본키 일치) · `approved_profile`(승인 프로필 `relationships` — 출처·겹침 비율). 항목 = `{from, to, columns: [[자식, 부모]…], kind}`
  - 승인 프로필은 **구조만**: `allowed_tables`·`entity_keys`(키 컬럼·종류)·`relationships`는 그대로, `code_values`·`code_labels`는 컬럼별 건수(값·라벨은 데이터), `query_rules`·`query_examples`·`patterns`는 건수, `query_guide`는 글자 수, 그 밖 키는 이름만. 버전은 파일 이름 번호만(버전 파일 내용·`by` 미독) · 머리 주석은 YAML 로 읽어 들어오지 않는다. 테이블마다 `allowed`(허용 목록에 있는가 — **거르지 않고 표시만**) · `entity_key_table`
  - `_synonyms`는 **컬럼별 건수만** — 운영자 등록어는 무엇이든 들어올 수 있고(사람 이름·값), 개선에 필요한 것은 「유사어가 있는가」라서 낱말은 반출하지 않는다
  - `_db_description`은 글과 출처(manual·llm)를 싣는다(관문을 거친다)
  - 의미 출처 `cache_description`은 **DB 주석 유래인지 LLM 생성인지 파일에 출처가 없어 구별할 수 없다** — 카탈로그 머리 `meaning_sources`에 그대로 적는다(DB 주석은 자산 프로파일링 잡 `schema_probe.read_catalog`만 읽고, 설명 초안을 적용하면 출처 태그 없이 `_descriptions`에 들어간다)
  - **테이블을 거르지 않는다** — 운영 108테이블 전부(정책·허용 목록과 무관 · 테스트 고정)
- `meaning`이 비어 있는 컬럼이 곧 133 A3(설명) 작업 목록이다 — 리포트가 「정답에 필요했는데 의미가 없던 컬럼」을 따로 뽑는다.

**(2) 턴별로 LLM에 제시된 맥락 — 레코드의 `schema_context`**

```json
"schema_context": {
  "tasks": 1,
  "presented_tables": ["TCDMSIF80"],
  "presented_column_count": 68,
  "columns_with_meaning": 0,
  "sample_rows_presented": true,
  "structure_meta_present": false,
  "gold_tables_presented": false,
  "key_columns_presented": ["manmenCtrcEndYmd"],
  "key_columns_with_meaning": []
}
```

**금지 형식** — 스키마 절(카탈로그 전체 · `schema_context`)에 `CREATE TABLE`·`SHOW CREATE`·`SHOW COLUMNS`·`information_schema`·`DESCRIBE` 문자열이 있으면 누출 관문이 실패시킨다. `sample_data`는 값이 아니라 `sample_rows_presented` 불린만 남긴다. (`executed_sqls`·오라클 SQL은 R2가 요구한 「생성된 쿼리」라 스키마 절이 아니다 — 다만 시스템이 `information_schema`를 조회하는 SQL을 만들었다면 그대로 기록하고 따로 표시한다.)

### 3.5 로그 위생 — 개인정보·사용자 정보

#### 3.5.1 컬럼 기록 등급 (기본 거부)

| 등급 | 뜻 | 로그에 남는 것 |
|---|---|---|
| `general` | 검토를 거쳐 「값을 남겨도 된다」고 정한 컬럼(호스트명·OS·모델·날짜·코드·사양 수치·부점명 등) | 값 — 표본 최대 5행 · 범주형은 상위 값 10개 |
| `network` | IP | 끝자리 가림(`10.1.2.***`) · 호스트 대조에는 호스트명을 쓴다 |
| `amount` | 금액(취득금액·유지보수계약금액·잔존장부금액) | 합계·최소·최대·건수만(개인정보는 아니나 행 단위 금액은 남기지 않는다 — G-2) |
| `free_text` | 서술형(계약명·구성항목 설명·사용용도) — 사람 이름·연락처가 섞일 수 있다 | 건수·빈값 수·길이 범위만 |
| `pii` | 사람 정보(성명·직원번호·사용자번호·연락처·이메일·주소·주민번호) | **건수·빈값 수만**(값·가린 값·해시 모두 없음) |
| `unclassified` | 정책 파일에 없는 컬럼 · 결과 열이 별칭이라 원 컬럼을 못 찾은 경우 | `free_text`와 같음 + 리포트 「분류 필요 컬럼」 목록에 등재 |

**분류 출처의 우선순위** — ①검토된 정책 파일 `testdata/itam_bench/column_policy.yaml` ②휴리스틱 **제안**(주석·설명에 `성명·이름·직원·사번·사용자번호·담당자·연락처·전화·휴대·이메일·주소·주민·생년` · camelCase를 **토큰 단위로 쪼갠** 이름에 `Emnm·Empid·Uno·Email·Phone·Tel·Addr·Rrn`) — 휴리스틱은 `pii` 쪽으로만 올릴 수 있고 **`general`은 정책 파일로만 준다** ③그 밖은 `unclassified`. 컬럼명 부분 문자열 일치는 쓰지 않는다(§2.2 L5).

**샌드박스 정책(W0에서 77컬럼 전수)** — `schema.yaml`의 `grp`·`sensitive`를 재료로: `rspblPsnEmpid`·`rspblPsnEmnm`·`sysRegiUno`·`sysLastUno` = `pii` · `rspblBrncd`·`rspblBrnName`(부점 = 조직) = `general` · 금액 3종 = `amount` · `byCtrcName`·`manmenCtrcName`·`cnfgItemDescCtnt`·`cmdtsUseUsagCtnt` = `free_text` · `iPCtnt` = `network` · 나머지 = `general`. 정책 파일에 `canaries`(합성 성명 3종 · 합성 직원번호 패턴)를 함께 둔다.

**결과 열 → 원 컬럼 해석** — 사용자 결과(CSV)의 열 이름은 시스템이 붙인 별칭(「담당자」·「호스트명」)이다. ①카탈로그 이름과 정확 일치 ②실행 SQL의 `<컬럼> AS <별칭>` 대응(단순 컬럼 참조일 때만) ③실패 시 `unclassified`. 집계식(`SUM(acqsiAmt) AS 합계`)처럼 원 컬럼이 하나로 정해지면 그 등급을, 여럿이면 가장 엄격한 등급을 쓴다.

#### 3.5.2 값 대조 차단

턴 처리 중 `pii` 등급 열에서 나온 값(길이 2 이상)을 **메모리에만** 모은다(run 종료 시 폐기 · 파일 기록 없음). 이 값이 `general` 열 값·SQL 리터럴·오류 문구 어디에 나타나면 그 칸을 등급 강등(→ 값 미기록)하거나 `'<가림:pii>'`로 바꾼다. 개인정보 컬럼을 일부러 따로 조회해서 값을 모으지는 않는다(그 조회 자체가 개인정보 처리다).

#### 3.5.3 SQL·문구 가림

- **SQL 리터럴**(v1.2 — 기본 거부로 뒤집음): 문자열 리터럴은 **남겨도 되는 근거가 있을 때만** 남긴다 — ①`general`·`network` 컬럼과 비교된 리터럴(`= '…'`·`LIKE '…'`·`IN ('…')` — `_extract_column_literals` 재사용 · `network`는 끝자리 가림) ②날짜·숫자 모양(`^[0-9:./ -]+$`) ③서식 문자열(`%Y%m%d` 류) ④그 턴의 사용자 프롬프트에 그대로 있는 말(`%`·`_` 제외 — 프롬프트는 린트를 거쳐 사람 정보가 없다). 나머지는 `'<가림>'`이다. `_extract_column_literals`는 `<>`·`!=`·`NOT LIKE`·`'값' = 컬럼` 꼴을 못 잡으므로 v1.1처럼 「pii 컬럼 비교만 가림」으로 두면 그 꼴로 이름이 샌다. 남긴 리터럴도 §3.5.2 값·카나리아·`scan_pii`와 대조한다. MariaDB 기본 모드에서 큰따옴표는 문자열 리터럴이라 같은 규칙을 쓰되, 카탈로그 식별자와 같은 내용(`"sevrHostName"` — 방언 함정의 증거)은 남긴다. SQL 구조(테이블·컬럼·조건 형태)는 그대로 남는다 — 프롬프트 개선에 필요한 것은 구조다.
- **(v1.3 보강 · 보안 리뷰)** SQL 은 주석·문자열·백틱을 한 번에 토큰으로 나눠 처리한다(주석 속 아포스트로피가 짝을 뒤집던 구멍). 주석은 가리고, 리터럴은 같은 술어 조각(AND·OR·WHERE 등 경계)에 `pii`·`free_text`·미분류 컬럼이 있으면 모양과 무관하게 가린다(프롬프트 말만 예외) · 수 모양은 4자리 이하·날짜·연월·시각만 통과 · 서식은 `%`지시자+구분자 40자 이하 · 따옴표 없는 비ASCII 낱말은 프롬프트나 결과 요약이 남긴 열 이름이 아니면 가린다 · IP 는 어디서든 끝자리 가림. 결과 열 이름(별칭)도 근거(카탈로그 이름·프롬프트 말·ASCII 식별자·일반/IP/금액 컬럼만 가리키는 별칭)가 있을 때만 남기고 아니면 `열#n`.
- **오류 문구**(DB 오류·시스템 사유): 같은 대조 + 500자 절단. MariaDB 오류는 문제 구문 일부를 되돌려주므로 리터럴이 섞인다. **(v1.3)** `near '…' at line N` 조각은 통째로 가리고, 그 턴 SQL 들의 가린 리터럴·주석 내용을 문구에서 지운다.
- **응답 본문**: 기록하지 않는다(길이·고지 종류만 — G-5).
- **정규식 상한**: 입력 길이 상한과 백트래킹 없는 패턴만 쓴다(`docs/18` 2026-08-19 ReDoS 사례 · 20KB 입력 50ms 이내 단언).

#### 3.5.4 사용자 정보(로그인 계정·실행자)

| 대상 | 처리 |
|---|---|
| 로그인 계정 `user_id` | `앞 1자 + ***`(D-300 ④와 같은 규칙) — `run.json`에 1회만 |
| JWT·비밀번호 | 어떤 형태로도 기록하지 않는다 |
| 감사 로그의 사용자·부서 필드 | 실행 SQL만 꺼내고 사용자 필드는 읽지 않는다 · 서버 원시 로그는 실행 종료 시 삭제(§2.2 L2) |
| task 상태의 `user_id`·`user_department` | 측정 수신기가 복사하지 않는다(§2.2 L4) |
| OS 사용자명 · 머신 이름 | `앞 1자 + ***` |
| 경로 | 저장소 기준 상대 경로 · 홈 디렉터리는 `~` |
| DB 접속 문자열 | `mariadb://***:***@<호스트>:<포트>/<database>` — 계정·비밀번호 모두 가림(§2.2 L7) |
| `thread_id` | 벤치가 만드는 `itam-bench-<시나리오>-<난수>` — 사용자 정보 없음 |

#### 3.5.5 누출 관문 (기록 전 마지막 단계)

산출물은 임시 디렉터리에 쓴 뒤 아래를 **모두** 통과해야 `results/itam_bench/<run_id>/`로 옮긴다.

1. **카나리아** — 정책 파일 `canaries`(샌드박스: 합성 성명 3종·합성 직원번호 패턴)가 어느 파일에도 0건
2. **수집 값** — §3.5.2에서 모은 개인정보 값이 0건
3. **정규식** — `scan_pii` 규칙(연락처·이메일·주민번호·계좌·카드) 0건
4. **스키마 형식** — §3.4 금지 문자열이 스키마 절에 0건
5. **사용자 정보** — 로그인 계정 ID·실행자 이름·홈 경로·DSN 계정이 0건(가린 형태만 허용)

실패하면 산출물을 옮기지 않고 `leak_check.json`만 남긴다 — **어느 파일·어느 레코드·어느 필드·어느 규칙인지만 적고 걸린 값은 적지 않는다** — 그리고 exit 1. 조용히 지우고 성공 처리하지 않는다.

#### 3.5.6 판정 상세 거르기 (v1.2 신설)

`evaluate_oracle`의 상세는 「값은 키만」이지만 키 자체가 값이고, `argmax`는 `oracle_top`·`value_diffs`에 **값 열의 행별 값**을, `value`는 스칼라를 싣는다(§2.1). 벤치는 상세를 그대로 쓰지 않고 등급으로 다시 거른다.

| 상세 칸 | 기록 |
|---|---|
| 키 값(`missing`·`extra`·`oracle_top`·`system_top`의 키 부분) | 키 열이 `general`이면 값(로더가 키를 `general`로 강제 — §3.2.1) · 그 밖이면 건수만 |
| 값 열의 값(`argmax`의 수치 · `value`의 `oracle`·`system`) | 값 열이 `amount`면 **행별 값 제거**(일치 여부·건수만 · `value` 스칼라 합계는 `amount` 규칙대로 남김) · `general`이면 그대로 |
| `header`(열 이름 목록) | 열 이름은 남긴다(값 아님) |
| 오라클 SQL · `oracle_log.jsonl` | 세션 임시 디렉터리 — 산출물 아님 |

### 3.6 측정 연결점 (본체 최소 변경)

사용자 경로에서는 파이프라인이 **서버 프로세스 안**에서 돈다. 2단 task가 LLM에 준 스키마 맥락(§2.3 M1)을 받으려면 본체에 연결점이 하나 필요하다(G-4).

- **신규** `src/observability/run_capture.py`(infrastructure 계층): 프로세스 전역 수신 함수 하나를 꽂는 `install(sink)`·`uninstall()`과 `emit(kind, payload)`. 수신 함수가 없으면 즉시 반환 — **기본 동작 비트 동일**. 수신 함수의 예외는 삼키고 debug 로그만 남긴다(측정이 제품 흐름을 깨지 않는다).
- **호출 1곳** `src/orchestration/subagents.py` `_pack_pipeline_result` — `emit("task_pipeline_state", s)`. 이 함수는 2단 핸들러와 3단 task 서브그래프가 함께 쓴다(같은 함수 docstring) — 한 곳으로 2단 전부와 3단 **계획 루프**를 덮는다. **v1.2 교정**: 3단 기본 경로(라우터 → 그래프 노드)는 이 함수를 지나지 않는다(§2.3 M6) — 그 run의 스키마 맥락은 `null`이다.
- **설치는 벤치 서버 진입에서만** — `scripts/itam_bench/_serve.py`가 앱 import 전에 수신기를 설치한다. 운영 기동(`python -m src.main --server`)·시나리오 하네스 기동(`scripts/scenario/_serve.py`)에는 설치 코드가 없다.
- **수신기 규칙**: 받은 상태 객체를 보관하지 않는다. 그 자리에서 `thread_id`·`relevant_tables`·스키마 컬럼 **이름**·컬럼별 의미 보유 여부·`sample_data` 유무(불린)·`_structure_meta` 유무만 복사해 임시 파일(세션 임시 디렉터리)에 한 줄 쓰고 놓는다. **값·사용자 필드는 읽지 않는다.**
- 스키마 리터럴 0(`overfit_check` 스캔 대상 `src/orchestration`) · 계층 방향 orchestration → infrastructure(허용 · `arch_check`).

### 3.7 산출물

| 파일 | 내용 |
|---|---|
| `run.json` | run_id · 커밋 SHA·dirty · 실행 환경(sandbox/closed) · 프로파일·확정 사다리 단 · 두 평면 프로바이더 이름 · 로그인 계정(가림) · 실행자·머신(가림) · MCP 엔드포인트·ITAM DSN(가림) · 시나리오·반복·턴 수 · SQL 관측 턴 · ITAM 자산 지문 · 수집 사람 값 개수 · 결과 원문 카나리아 건수(관문 시험 성립 판단) — 누출 관문 결과는 `leak_check.json`(v1.2: `run.json`도 관문 대상이다) |
| `schema_catalog.yaml` | §3.4 (1) |
| `trace.jsonl` | 시나리오×턴×반복 1행 — 아래 |
| `report.md` | 점수 요약(범주별) · 실패 분류 집계와 고칠 자산 · 정답 테이블 제시율·핵심 컬럼 의미 보유율 · 소스 선별률·되물음률 · 시나리오별 표 · 분류 필요 컬럼 · 방언 함정 검출 · (`--compare` 시) 전이 |
| `leak_check.json` | 관문 5단계 결과(위반 위치만) |

`trace.jsonl` 레코드(샌드박스 ITAM-06 — 결과에 성명이 있어도 값이 남지 않는다):

```json
{"run_id": "20261006-150210", "id": "ITAM-06", "turn": 1, "repeat": 0, "category": "owner",
 "prompt": "svr-db-03 담당자가 누구야?",
 "tier": "intent_orchestration", "status": "answer", "db_ids": ["itam"], "disclosure_kinds": [],
 "executed_sqls": [{"sql": "SELECT sevrHostName AS 호스트명, rspblBrnName AS 담당부점, rspblPsnEmnm AS 담당자 FROM TCDMSIF80 WHERE sevrHostName = 'svr-db-03' LIMIT 1000",
                    "source": "itam", "success": true, "row_count": 1, "retry_attempt": 0}],
 "result": {"total_rows": 1, "columns": [
    {"name": "호스트명", "source": "sevrHostName", "log_policy": "general", "sample": ["svr-db-03"]},
    {"name": "담당부점", "source": "rspblBrnName", "log_policy": "general", "top_values": {"합성부점": 1}},
    {"name": "담당자", "source": "rspblPsnEmnm", "log_policy": "pii", "nulls": 0, "distinct": 1}]},
 "oracle": {"compare": "keyset", "verdict": "pass", "oracle_rows": 1, "detail": null},
 "taxonomy": [],
 "sql_analysis": {"tables": ["TCDMSIF80"], "key_columns_used": ["rspblPsnEmnm"], "dialect_hazards": []},
 "schema_context": {"presented_tables": ["TCDMSIF80"], "columns_with_meaning": 0, "sample_rows_presented": true, "gold_tables_presented": true},
 "response_chars": 214, "latency_ms": 41250.3}
```

(부점 값 `합성부점`은 샌드박스 시드 실측(`generate_init.py:212` 전 행 동일). 생성 SQL·지연·응답 길이는 형식 설명용이다.)

---

## 4. 작업 분해

순서는 W0 → W1·W2(병행 가능) → W3 → W4 → W5 → W6 → W7 → W8. 각 WU의 verify를 통과해야 다음으로 간다.

| WU | 내용 | 산출물 | verify |
|---|---|---|---|
| **W0** | 사용자 프롬프트 시나리오 형식·로더·**프롬프트 린트** · 샌드박스 22건 · 오라클 정본 SQL · **샌드박스 시드 보강**(기존 2테이블의 기존 비코드 컬럼에만 합성 값 — 운영체제·모델·CPU 코어·메모리 편차 · v1.2: 가상화 여부는 코드 컬럼이라 제외) · **오라클 MariaDB 접미사**(`oracle.py:102` 1줄 — v1.2: 정본 검증이 W0에 필요해 W4에서 앞당김) · 컬럼 정책(77컬럼 + 카나리아) · `--check-oracle` · `--dry-run` | `testdata/itam_bench/{scenarios,column_policy}.yaml` · `testdata/scenarios/oracles/ITAM-*.mariadb.sql` · `testdata/itam/generate_init.py` 보강분 · `catalog.py`(로더) · `scripts/scenario/oracle.py` 1줄 | 단위: 테이블·컬럼 식별자·SQL 용어가 든 프롬프트 거부 · 사람 이름·사번 든 프롬프트 거부 · 오라클 키가 `general` 등급이 아닌 열이면 거부(v1.2 — 상세가 키 값을 싣는다 §3.5.6) · **샌드박스 `--check-oracle` 전건 실행 성공·0행 0건(`observe` 제외)** · 시드 보강 후 `tests/test_testdata/test_itam_generate_init.py` 재생성 diff 0 · **README 오라클 3종과 M군 오라클(`m_cross_system_oracle.yaml`) 결과 불변** |
| **W1** | 스키마 카탈로그 생성(스키마 캐시 또는 `--schema-snapshot`) · 승인 자산 지문 · `value_kind` 판정 | `catalog.py` | 샌드박스 2테이블 77컬럼 · 스냅숏 입력과 캐시 입력의 카탈로그 동형 · 금지 문자열 0 · `sample_data` 키 0 |
| **W2** | 로그 위생 계층(§3.5 전부) — **테스트 먼저** | `redact.py` | 합성 성명·직원번호·전화·이메일을 ①결과 행 ②별칭 열 ③SQL 리터럴 ④DB 오류 문구 ⑤`unclassified` 열로 각각 흘려 산출 파일 0건 · 로그인 계정 ID·JWT·DSN·홈 경로·OS 사용자 0건 · 관문 실패 시 산출물 미이동·exit 1·`leak_check.json`에 값 없음 · 20KB 입력 50ms 이내 |
| **W3** | 측정 연결점(§3.6) + 벤치 서버 진입 `_serve.py` | `src/observability/run_capture.py` · `subagents.py` 1줄 · `scripts/itam_bench/_serve.py` | 수신기 미설치 시 기존 `tests/test_orchestration` 무회귀 · 2단 그래프 목 LLM 구동에서 task당 1건 수신 · 수신 레코드에 `thread_id`가 있고 `user_id`·`user_department`·값이 없음 · 수신기 예외가 결과를 바꾸지 않음 · `arch_check --ci`·`overfit_check --ci` exit 0 |
| **W4** | 사용자 경로 드라이버(§3.3) — 서버 기동·사다리 확인·로그인·계정 권한 확인·턴 루프·되묻기 응답 턴·결과/SQL/맥락 결합 · 판정 어댑터(§3.3 v1.2) · `SqlAuditTail` 하위 클래스(`error`) · 하네스 `ServerHandle` 진입 모듈 인자(v1.2) · SQL 분석 · 실패 분류 | `__main__.py`·`judge.py` · `scripts/scenario/server.py` 인자 1개 | 가짜 클라이언트·가짜 감사 로그 주입으로 턴 루프 단위 테스트(v1.2: 모의 서버는 하네스 카탈로그만 읽어 벤치 시나리오를 모른다) · 분류 14종 각각 합성 사례 1개 이상 · 사다리 불일치·과금 평면·`itam` 비활성·권한 없음이면 실행 0·사유 출력 · 기존 `tests/test_scenario` 무회귀(진입 모듈 인자 기본값 = 현행) |
| **W5** | 산출물·리포트·원자 기록 · 서버 원시 로그·수신 임시 파일 삭제 | `report.py` | 고정 입력 → `report.md` 스냅숏 테스트 · `trace.jsonl` 필드 계약 테스트 · 실행 후 임시 파일 잔존 0 |
| **W6** | `--compare` — 시나리오별 전이(통과↔실패)·분류 증감·자산 지문 차이 | `report.py` | 합성 run 2개로 전이 표 단언 · 자산 지문이 같으면 「프롬프트 자산 변화 없음」 고지 |
| **W7** | **(v1.4) 로컬 MLX 스모크 — 정의만 · 실행 보류**(사용자 「DB 대조만, LLM 보류」 · CLAUDE.md 「MLX 검증은 최소로」 2026-10-06 — 대표 문항 소수 1회 · Wave마다 반복 금지 · 정확도·지연은 내부망 측정 잔여). **대표 3문항 1회**(사용자 확정 2026-10-06 — *「대표 3문항 1회 스모크」* · 목적은 벤치 서버 → 로그인 → 위생 → 누출 관문이 끝까지 도는지뿐 · 정확도·지연 결론은 내부망 FabriX 측정 잔여): ITAM-01(사용자 예시 원문 · 통합인증 — **관찰**: 실제 경로에서 지어내지 않고 도는지) · ITAM-06(누출 관문 카나리아 · count) · **ITAM-16**(정답 대조 1건 · rowset). 16을 고른 근거 — ①목적이 경로 확인이라 회귀 루프를 덜 타는 문항이 낫다: 16의 `\|\|`는 오류 없이 도는 침묵 오답이라 SQL 오류 회귀(예산 3)를 부르지 않는다. 09의 3열 조인·OR는 정확도 함정이라 MLX에서 회귀 예산을 쓸 공산이 크고 소요만 는다 ②16만 프롬프트에 소스(「자산관리 시스템에서」)가 있다 — 07·09는 소스를 말하지 않아 `ACTIVE_DB_IDS=polestar,itam`에서 소스 선별이 흔들리면 대조가 ITAM 행끼리가 아니게 된다 ③01·06이 지나지 않는 위생 갈래를 실 SQL 꼴로 지난다: 결합 계산 열(원 컬럼 `sevrHostName`+`iPCtnt` → 가장 엄격한 `network` 등급 · 문자열 안 IP 끝자리 가림 `svr-web-01(10.0.1.***)`) · `CONCAT`/`\|\|`의 형식 리터럴(`'('`) 허용. 07의 CHAR(8) 날짜 리터럴 갈래는 단위 테스트(`test_itam_bench_redact.py`)가 고정한다 — `python -m scripts.itam_bench --run --only ITAM-01,ITAM-06,ITAM-16`(구성은 `test_itam_bench_driver.py::TestW7Smoke`가 고정 — 환경 필터는 모르는 ID와 달리 조용히 빠진다). 예상 소요 **약 10~20분 · 회귀 예산을 다 쓰면 30분 안팎**(**추정·실측 아님** — 근거: `plans/100` Phase 4 실측(2026-09-17 · MLX 9B) 3단 단일 DB 단순 질의 종단 44~79초에 2단의 계획·오케스트레이터·종합 호출을 더해 문항당 2~4분 가정 + 벤치 서버 기동·설정 에코·로그인 1~3분 · 오라클은 문항당 1초 미만). v1.3의 `--run --repeat 3` 전수는 폐기 | 실행 시: 진행 기록(통과·분류 상위 3) | 누출 관문 통과(ITAM-06 성명이 결과에 실제로 나왔는데도 0건 — `run.json` `canary_in_results` > 0 이어야 관문 시험 성립 · 0이면 리포트가 「불성립」을 고지하고 스모크는 미완으로 본다) · SQL 관측 턴 > 0(관찰 턴 ITAM-01은 정답 판정이 없다 — 결정적 분류 `routing_miss`·`fabricated`·`dialect_error`·`dialect_silent`만 남는다) · 확정 단 `intent_orchestration` |
| **W8** | **(v1.4) 폐쇄망 1회차 키트 = 관찰** — 카탈로그 입력 = 「DB 구조」 탭 산출물 전체(선언 FK·`schema.relationships`·설명·유사어 건수·DB 설명 + 승인 프로필 자산 키의 **구조만**) · 운영 108테이블 전부 · 운영 컬럼 정책 1회차(검토된 두 테이블 + 나머지 기본 거부) · 운영 시나리오 초안 14건(전부 observe · 사용자 검토 대기) · 실행 시 운영 카탈로그 기준 프롬프트 재린트 · 반출 후 싱크 보조 `--sync`(반출 카탈로그 ↔ 전사본·정책 차이 + 서비스 연결 후보 + 관찰 턴 조회처) · 절차서(부록 A). 2회차: 반출 로그로 정답 SQL 작성 → `ITAM-1NN.mariadb.sql` + 시나리오 `oracle` 전환 | `testdata/itam_bench/{scenarios,column_policy}.closed.yaml` · `catalog.py`·`report.py`·`__main__.py` · 부록 A | 운영 실행은 사용자(FabriX 내부망 · D-216) · 반출물은 `results/itam_bench/<run_id>/` 5파일뿐(제품 로그·체크포인트 반출 금지 · D-219 ②) |

**매뉴얼(D-255)** — 사용자·관리자 화면·채팅 기능이 아니라 개발 하네스라 대상 아님.

---

## 5. 수용 기준

1. **사용자 프롬프트** — 시나리오 프롬프트가 린트(§3.2.2)를 전건 통과하고, 요청 본문에 `query`·되묻기 응답 외의 대상 DB·스키마 지정이 0건이다.
2. **실행** — 샌드박스 22건이 2단 확정 서버에서 로그인한 사용자 경로로 끝까지 돌고, 턴마다 실행 SQL·결과 요약·스키마 맥락이 있는 레코드가 남는다(SQL 관측 0이면 실패 처리).
3. **대조** — `oracle` 시나리오마다 판정이 있고, 실패 분류와 고칠 133 자산을 리포트가 가리킨다. 라우팅·되물음 실패는 ITAM 프롬프트 수치와 분리 집계된다.
4. **스키마 형식** — 스키마 절에 DDL·`information_schema` 문자열 0건 · 벤치가 스키마 조회문을 직접 실행한 횟수 0.
5. **개인정보 0** — 누출 관문 5단계 통과. 샌드박스 카나리아가 ITAM-06 결과에 실제로 나왔는데도 산출 파일 5종에 0건(나오지 않았다면 관문 시험이 성립하지 않은 것 — 리포트에 고지).
6. **사용자 정보** — 로그인 계정 ID·실행자명·홈 경로·DB 계정 0건, 가린 형태(`5***`)만 존재 · 서버 원시 로그 잔존 0.
7. **제품 무회귀** — 측정 연결점 미설치 상태에서 바꾼 모듈의 모듈 단위 회귀 통과(`python scripts/regress.py` — D-303 · 범위 명시 · 전체 회귀는 사용자 요청 시) · `arch_check --ci`·`overfit_check --ci`·`ruff`·`mypy`(기준선 대비 신규 0).
8. **개선 루프** — 같은 시나리오로 두 run을 `--compare` 하면 시나리오별 전이와 자산 지문 차이가 나온다.

---

## 6. 사용자 확정 게이트

**전건 답(2026-10-06 사용자 인터뷰 · 리드 경유 · 정본).** 「답」 칸이 정본이고 「질문」 칸은 v1.3 원문 요지다.

| # | 질문 | 답(2026-10-06) | 이 계획에 미치는 것 |
|---|---|---|---|
| **G-1** | 자산관리 포탈 사용자들이 실제로 묻는 질문 목록·조회 화면을 받을 수 있는가? | **우리가 운영 시나리오 초안(사용자 프롬프트)을 쓰고 사용자가 검토한다** | `testdata/itam_bench/scenarios.closed.yaml` 14건(★초안 · 검토 대기 · 전부 observe) |
| **G-2** | 로그에 결과 값을 어디까지 남길 것인가? | **기본안 그대로** — 사람 정보 건수만 · 금액 합계·최소·최대만 · IP 끝자리 가림 · 일반 컬럼 표본 5행 | §3.5.1 표 확정 |
| **G-3** | 「스키마 쿼리 형식이 아닌 방식」을 §1.2-1처럼 이해해도 되는가? | **현 해석 확정** — 구조화 YAML 카탈로그 + 턴별 요약 · DDL·`information_schema` 금지 | §3.4 확정 |
| **G-4** | 본체에 측정용 연결점 1곳을 넣어도 되는가? | **기본 가정 그대로**(이미 구현된 연결점 유지) | §3.6 유지 |
| **G-5** | 자연어 답변 원문도 남길 것인가? | **기본안 그대로** — 남기지 않음(길이·고지 종류만) | §3.5.3 확정 |
| **G-6** | 운영(폐쇄망) 실행 시점 | **기본 가정 그대로** — 로컬 확인 뒤 사용자가 내부망에서 실행 | 부록 A 절차서로 넘긴다 |
| **G-7** | ★「통합인증」 같은 서비스와 서버의 연결은 ITAM 어느 테이블·화면에 있는가? | **사용자 원문**: *"내부망에는 테이블 정의서가 있다. 이 내용을 이용하여 테스트한 후 로그를 통해 반출하여 싱크를 맞출 예정이다. 이에 맞게 진행하라."* · 정의서 형식: *"관리자 페이지의 "DB구조" 탭에서 생성한 스키마이다."* · 정답 대조: **1회차 내부망 실행은 관찰만 → 로그 반출 → 반출 로그로 우리가 연결 위치를 찾아 정답 SQL 작성 → 2회차부터 자동 대조** · 반출 카탈로그 범위: **운영 108테이블 전부(구조·의미만 · 데이터 값 0)** | 카탈로그 입력 = 「DB 구조」 탭 산출물 전체(§3.4 v1.4) · 1회차 키트(부록 A) · 싱크 보조 `--sync`(§9 W8) · 「기본 가정 없음」 해소 |
| (추가) | 로컬 실행 범위 | **「DB 대조만, LLM 보류」** — W7(MLX 실행) 하지 않음 | W7 = 대표 3문항 스모크 정의만(§4 v1.4 · 실행 0) |
| (추가) | W7 MLX 규모 | **대표 3문항 1회 스모크**(지금은 실행하지 않음) — 목적은 벤치 서버 → 로그인 → 위생 → 누출 관문 종단 확인뿐 · 정확도·지연 결론은 내부망 FabriX 측정 잔여 · `--repeat 3` 전수 일정 제외 · 문항 선택은 우리 몫(근거를 계획에) | §4 W7 — `--only ITAM-01,ITAM-06,ITAM-16` · 추정 소요 |
| (추가) | `pii_filter` 지연(§7 발견) | **「지금 135에서 같이 수정」** | `src/security/pii_filter.py` 이메일 규칙 탐색기 교체(§9 · 결과 비트 동일) |

## 7. 위험 · 비범위

**위험**

| 위험 | 대응 |
|---|---|
| 별칭·계산식 때문에 결과 열의 원 컬럼을 못 찾는다 | 기본 거부라 새지 않는다(값 미기록). 대신 가시성이 줄어드므로 리포트 「분류 필요 컬럼」으로 정책 보강 |
| 휴리스틱이 개인정보 컬럼을 못 알아본다(운영 108테이블) | `general`은 검토로만 부여 → 못 알아본 컬럼은 `unclassified`(값 미기록). 관문이 2차 방어 |
| 소스 선별이 ITAM을 고르지 않거나 되묻는다 | `routing_miss`·`asked_back`으로 분리 집계 — ITAM 프롬프트 수치에 섞지 않는다. 명시형·생략형 프롬프트 쌍으로 차이를 본다 |
| 사용자 결과 CSV의 열 이름이 매번 달라 오라클 키를 못 찾는다 | 키에 별칭 후보 목록을 둔다(하네스 오라클 규약) · 못 찾으면 보류(불합격 아님)로 기록해 키 후보를 보강 |
| 시드 보강이 기존 오라클(README 3종·M군)을 바꾼다 | 보강은 기존 오라클이 쓰지 않는 컬럼에만 · W0 verify에서 결과 불변 단언 |
| 공유 MLX 서버(8080) 동시 사용 시 메모리 부족·좀비 | 단독 실행 · `scripts/mlx_server.sh -- --decode-concurrency 1 --prompt-concurrency 1` · 생존은 1토큰 생성으로 판정(`mlx_run_blockers`) |
| (v1.2 실측 · **v1.4 해소**) `scan_pii`·`scrub_pii`가 긴 영숫자 연속열에서 제곱 시간 — 20KB 한 줄 5.7초(이메일 규칙의 시작 위치별 재탐색) | v1.4에서 제품 쪽을 고쳤다(`pii_filter.ChainStartSearch` · 일치 비트 동일 · §9) — 벤치 우회는 걷어냄 |
| 누출 관문 수집 값 대조가 짧은 한국어 이름과 리포트 문장의 우연한 겹침으로 실패 | 오탐이면 위치만 보고하고 미기록 — 조용히 통과시키지 않는 쪽을 택했다(D-301 주의 ③) |
| (v1.3 · 보안 리뷰 #8) 벤치 서버도 제품의 정상 기록을 남긴다 — 감사 파일 `logs/audit-*.jsonl`(원 SQL·사용자 ID)·앱 DB 감사 미러·SQL 파일 로그·트레이스 | 벤치가 받은 표준출력 로그·체크포인트·수신·오라클 로그만 세션 임시로 지운다. 제품 기록 경로를 바꾸려면 제품 설정 변경이 필요해 이 계획 범위 밖이다 — 폐쇄망 run(W8) 절차서에 「반출은 벤치 산출물 5파일뿐 · 제품 로그는 반출 금지」를 명시한다 |
| 샌드박스 결과를 운영 품질로 오인 | 샌드박스는 전사본 파생 합성 데이터라 **로직 확인용**이다(`plans/95` §4.6.2). 운영 결론은 W8 run으로만 |
| 샌드박스 날짜 경계가 늙는다 | 실행 전 `setup.sh` 재생성(W7 절차) · 오라클은 실행 시점 계산이라 기대값 고정 없음 |
| 측정 수신기가 큰 상태 객체를 붙잡아 메모리가 는다 | 필요한 이름·불린만 복사하고 놓는다(§3.6) · 단위 테스트로 보관 0 확인 |

**비범위**

- **제품 프롬프트·자산 수정** — 이 계획은 근거를 만든다. 고치는 일은 `plans/133` 승인 흐름(A1~A8)과 `plans/95`의 몫이다.
- **사람 이름으로 찾는 질문**(*"홍길동 담당 서버"*) — 프롬프트 자체가 로그에 남아 개인정보 통로가 된다(§3.2.2-5).
- 기존 앱 로그(`logs/`의 실행 SQL 파일 로그 D-140·감사 로그·`logs/pii_block/`) — 벤치 서버도 평소처럼 쌓는다. **폐쇄망 반출 대상은 벤치 산출물 5파일뿐**이다.
- LLM에 `sample_data`(실 행 표본)를 주는 현행 동작(§2.2 L3) — 지적만 하고 바꾸지 않는다.
- 시나리오 러너(`scripts/scenario/runner.py`)·판정 계약 변경 · 폴스타↔ITAM 교차 질의(M군 담당) · 답변 문장 품질 평가 · 지연 성능 결론(D-240).

---

## 8. 의사결정 정합 · 신규 결정 예약

- **충돌 검토**: D-003(읽기 전용 — 시스템 SQL은 MCP `readonly` · 오라클은 하네스 안전 5종) · D-127/D-240(비과금 평면에서만 자동 실행) · D-216(내장 테스트 계정 — 로그에는 가림) · D-232(계정 인가 사전 점검) · D-251(2단 기준 · 3단은 `--profile tier3_router` 비교 arm) · D-219(SQL 관측 0 = 사고 · 반출은 축소본) · D-275(실 DB 읽기 전용 오라클 — 엔진 접미사만 확장) · `plans/95` G-9(응답 노출 방침 불변 — §1.2) · D-214 ⑥(로컬 산출물 커밋 금지 — 벤치 산출물은 `results/` git 제외) — **충돌 없음**.
- **D-301 예약**(2026-10-06 · 「채번 이력」 표 등재): **ITAM 질의 벤치마크 계약** — ①시나리오 = 사용자 프롬프트(테이블·컬럼·SQL 용어 금지) · 로그인한 사용자 경로 실행 · 대상 DB·스키마 고정 주입 금지 ②결과 값 기본 거부(검토된 일반 컬럼만 값 기록 · 사람 정보는 건수만 · 해시 금지) ③스키마는 구조화 카탈로그(조회문·DDL 금지 · 벤치의 별도 스키마 조회 0) ④로그인 계정·실행자·접속 계정 가림(D-300 ④ 규칙) · 토큰 미기록 · 서버 원시 로그 미보존 ⑤누출 관문 실패 = 산출물 미기록·exit 1 ⑥측정 연결점(벤치 서버에서만 설치 · 기본 no-op · 2단·3단 공용 1곳). 본문 등재는 W2·W3 랜딩 시.
  - 채번 실측(2026-10-06): `## D-` 헤더 최댓값 **D-300** · 「채번 이력」 표 최댓값 D-300 · `plans/`·`spec/`의 D-30x 참조는 D-300뿐 → **D-301**.

---

## 9. 진행 기록

(2026-10-06 · 작업 트리 · 커밋 없음 · LLM·DB 호출 0)

| WU | 상태 | 내용 · 검증 |
|---|---|---|
| W0 | **완료** | `testdata/itam_bench/scenarios.yaml`(22건 · 23턴 — 오라클 18턴·관측 5턴) · `column_policy.yaml`(77항목 · 카나리아 3+1패턴) · 오라클 정본 16개(`testdata/scenarios/oracles/ITAM-*.mariadb.sql` — DB 현재시각 함수 금지 · 앵커 자리표 `:today`·`:today_ymd`) · `oracle.py` `ENGINE_SUFFIX` mariadb · 시드 보강 3컬럼 · `catalog.py` 로더·린트 · `--dry-run`·`--check-oracle`. 검증: `tests/test_scripts/test_itam_bench_catalog.py` 62 · `tests/test_testdata/test_itam_generate_init.py` 16(보강 컬럼 ∩ README 오라클·M군 정답표 컬럼 = ∅ 단언 신설) · HEAD 생성기와 행별 대조 — 달라진 칸은 보강 3컬럼뿐. 하네스 테스트 2건 기대값 갱신(itam 이 「대상 엔진 아님」에서 「정본 파일 없음」으로 — `test_plan122_oracle.py`·`test_plan122_integration_oracle.py`) · MariaDB 정본은 PG·DB2 짝 규칙에서 뺐다. **잔여**: 샌드박스 실 실행 `--check-oracle`(전건 성공·0행 0건)과 README 오라클 3종 실행 불변 — Docker 데몬·MCP 9099 미기동 · 피어 세션이 공유 MLX 로 측정 중이라 Docker VM 기동을 미뤘다 **(v1.4) 실 DB 대조 완료 — 리드 실측 2026-10-06**: `testdata/itam/setup.sh` 재생성(13:19 KST · MariaDB 11.4.13 · TCDMSIF80 30행·TCDMSIF79 29행 · `itam_ro` SELECT 전용) → MCP 9099(리드 기동 · PID 26868) → `python -m scripts.itam_bench --check-oracle` **16건 전부 ok · 실패·0행 0건**(앵커 2026-10-06T13:19:54+09:00) · README 오라클 3종(`itam_ro` 읽기 전용) **불변** — ① svr-was-01·02·03 ② svr-db-01·02·03 ③ svr-db-07·08 |
| W1 | **완료** | `catalog.py` 카탈로그 — 입력 3종(파일 스키마 캐시 · `itam_erd` 스냅숏 · 전사본) 정규화 · 값 종류 · 의미 출처(`db_comment`·`cache_description`) · 관계(`infer_relations` 재사용 — 테이블 2개면 공통 컬럼 비율 1.0 · 동일 키 군 `[[TCDMSIF79, TCDMSIF80]]`) · 자산 지문(머리 주석 미복사). 검증: `test_itam_bench_schema_catalog.py` 7(세 입력 동형 · 표본 행 0 · 금지 형식 0 · 승인자 칸 미복사) · 로컬 `.cache/schema/itam_schema.json`으로 생성 확인(77컬럼 · 의미 9) |
| W2 | **완료(TDD)** | `redact.py` — 테스트 먼저(모듈 없음으로 실패 확인 후 구현). 결과 요약 등급 6종 · 사람 값 메모리 수집 · 일반 칸 강등 · SQL 리터럴 기본 가림 · 오류 문구(그 SQL 의 가린 리터럴 내용 제거 + 작은따옴표 조각 규칙) · 사용자 정보(D-300 ④ 규칙) · 누출 관문 5단계(위치만 · 값일 수 있는 키는 순번) · 메모리 검사 후 기록(`write_gated`). 검증: `test_itam_bench_redact.py` 68(①결과 행 ②별칭 열 ③SQL 리터럴 ④오류 문구 ⑤미분류 열 · 20KB 입력 건당 50ms 이내 5종 + **독립 보안 리뷰 재현 26건**). **보안 리뷰(2026-10-06 · 읽기 전용 감사 에이전트)**: High 3·Medium 5·Low 3을 재현 스크립트로 확인 — 별칭 해석(첫 SQL 채택·카탈로그 이름 우선·정책 밖 컬럼 무시)으로 사람 값이 일반 표본에 기록 · 관문 20K 절단과 직렬화 텍스트 사전 검사의 fail-open · 주석/백틱/따옴표 없는 값/사번 숫자/IP 리터럴 구멍 · 관문 경로에 값 키 · 시스템 키 열이 일반이 아닐 때 오라클 상세 키 · SQL 없는 `near` 조각 · 값인 별칭 · `_FORMAT` 이차 시간 · `mask_dsn` fail-open · 짧은 수집 값 오탐. 실패 테스트를 먼저 넣고(24 실패 확인) 교정 — 별칭 식 우선·합집합·미분류 표지 · 토큰 단위 SQL 가림(주석 포함)·술어 조각 등급·짧은 수만 통과 · 디코드 값 단위 무절단 검사 + 닫힌 쪽 실패 · 경로 키 규칙 · 키 건수화 · `near` 통째 가림 · 열 이름 근거 규칙 · 선형 서식 패턴 · 마지막 `@` 분리 · ASCII 수집 값 단어 경계(숫자만은 5자리 이상). **남긴 것**: 제품 감사 파일(`logs/audit-*.jsonl` 등)은 벤치 서버도 남긴다(§7) **실측 발견**: `src/security/pii_filter.scan_pii`·`scrub_pii`가 긴 영숫자 연속열에서 제곱 시간(20KB 한 줄 5.7초) — 벤치는 65자 이상 연속열을 검사 전에 접고 숫자·`@` 없는 입력은 건너뛴다(제품 함수는 범위 밖 · §7) |
| W3 | **완료** | `src/observability/run_capture.py` · `subagents.py` `_pack_pipeline_result` 첫 줄 `emit` · `scripts/itam_bench/_serve.py`(환경변수 경로가 있을 때만 설치 · 이름·불린만 복사). 검증: `tests/test_observability/test_run_capture.py` 8(미설치 no-op · 수신 예외 삼킴 · 결과 동일 · 2단 핸들러 task 당 1건 · 사용자 칸·값·설명 문구 0) · `tests/test_orchestration`+`tests/test_observability` 1507 통과 · `arch_check --ci` 위반 0 |
| W4 | **완료(실 서버 미실행)** | `judge.py`(SQL 분석 · 방언 함정 4종 · 날짜 문자열 오용(쉼표로 술어를 자르지 않음) · 조인 키 부족 · 분류 14종 · 판정 어댑터 2종 · 상세 거르기) · `__main__.py` 드라이버(사전 점검 — 과금 평면은 `RUN_E2E=1` 없으면 거부·키 존재로 열지 않음 · MLX 1토큰 · itam 활성 · direct 거부 / 벤치 서버 `ServerHandle(entry_module=…)` · 체크포인트·서버 로그·수신·오라클 로그는 세션 임시 디렉터리 / 설정 에코·사다리 대조 / 로그인·`/auth/me` 인가 / 턴 루프 · 되묻기 정지 / 원값 판정 후 가림) · `server.py` 진입 모듈 인자. 검증: `test_itam_bench_judge.py` 40 · `test_itam_bench_driver.py` 15(가짜 클라이언트·감사·수신·오라클 주입) · `tests/test_scenario` 무회귀 |
| W5 | **완료** | `report.py` `render_report` · 산출물 4종 + `leak_check.json` · 세션 임시 디렉터리 삭제(예외 경로 포함). 관문 시험 성립 여부(결과 원문 카나리아 건수)를 리포트 머리에 고지. 검증: 스냅숏(`tests/test_scripts/fixtures/itam_bench_report.md`) · 필드 계약 · 실 레코드 관문 통과 · 임시 디렉터리 잔존 0 |
| W6 | **완료** | `--compare` — 반복 합친 턴 결과 전이 · 분류 증감 · 자산 지문 차이(같으면 「프롬프트 자산 변화 없음」) · 확정 단·환경이 다르면 주의. 검증: 합성 run 2개 |
| W7 | **정의만 · 실행 보류(v1.4)** | 사용자 답 「DB 대조만, LLM 보류」 + CLAUDE.md 「MLX 검증은 최소로」(2026-10-06)에 따라 v1.3의 `--repeat 3` 전수를 대표 3문항 1회 스모크로 바꿨다(§4 W7 행 — ITAM-01·06·16 · 규모 사용자 확정 · 선택 근거 §4 · 추정 약 10~20분). 실행하지 않았다. 구성 고정 테스트 `TestW7Smoke` 1건(드라이버 16 통과). 종전 미실행 사유(Docker·MCP·공유 MLX 사용 중)는 W0 대조로 Docker·MCP 쪽이 해소됐다 |
| W8 | **1회차 키트 완료(v1.4)** | ①카탈로그 = 「DB 구조」 탭 산출물 전체(§3.4 v1.4) — 선언 FK·`schema.relationships`·설명·유사어 건수·DB 설명·승인 프로필 자산 키(구조만)·버전 번호 · 108테이블 전부 ②`testdata/itam_bench/column_policy.closed.yaml`(검토된 두 테이블 = 샌드박스 정책과 같음 · 카나리아 없음 · 나머지 기본 거부) ③`testdata/itam_bench/scenarios.closed.yaml` 운영 초안 14건(15턴 · 전부 observe · 서비스 질문 6건 포함 · ★사용자 검토 대기) ④`--env closed`면 위 둘이 기본값 ⑤실행 시 운영 카탈로그 기준 프롬프트 재린트(5자 이상 식별자 · 걸린 낱말 미출력) ⑥정책 밖 컬럼과 같은 술어의 리터럴도 프롬프트 말이 아니면 가림(운영 106테이블 기본 거부) ⑦리포트 관찰 절에 테이블·컬럼·행 수 · ⑧싱크 보조 `--sync <run>`(반출 카탈로그 ↔ 전사본·정책 차이 · 사람 정보 휴리스틱 제안 · 서비스 연결 후보 · 관찰 턴 조회처 — 파일을 쓰지 않는다) ⑨절차서 부록 A. 역추적 확인: 관찰 턴도 `sql_analysis.tables/columns`(카탈로그 컬럼 이름) · 가린 SQL(프롬프트의 서비스 이름 리터럴은 남음) · SQL 별 행 수 · 결과 행 수가 남는다 — 보강은 리포트 관찰 절 컬럼·행 수 표시 하나. 검증: `test_itam_bench_closed_kit.py` 6 · `test_itam_bench_schema_catalog.py` 10(탭 산출물 3건 추가 — 선언 FK·유사어 건수·승인 프로필 구조·108테이블) |
| pii_filter | **완료(v1.4 · 제품 코드)** | 사용자 「지금 135에서 같이 수정」. TDD — `tests/test_security/test_pii_filter_linear.py`를 먼저 써서 20KB 한 줄 지연 8건 실패를 확인(73초)한 뒤 고쳤다. 원인은 이메일(853) 정규식 하나(9규칙 중 나머지는 20KB 5ms 미만 실측). **정규식 원문은 그대로** 두고 `ChainStartSearch`로 감싸 「앞 위치에서 이미 성립하는 시작점」(바로 앞이 영숫자 · 바로 앞이 구분자이고 그 앞이 영숫자 — 둘 다 탐색 시작점 이후)만 건너뛴다 — 원 정규식의 왼쪽부터 시도 의미를 그대로 따르므로 일치 위치·범위·순서가 같다. 대조: 무작위 2만 건·문서 예시 단위 테스트 + `scan_pii`·`scrub_pii` 출력 전체를 원 정규식 규칙과 비교 · 수동 대량 대조 저장소 텍스트 811,617줄(원 일치 66건)·무작위 20만 건 차이 0. `tests/test_security` 208 통과(신규 13 포함 · `test_pii_probe_reduction`·`test_pii_regex_check` 포함 — `scripts/pii_probe.py`는 FabriX 호출 도구라 실행하지 않음 · `pii_regex_check.py`는 임의 정규식 도구라 영향 없음). 의미 변경 없음 → 새 D-번호 대신 D-301 부기 · 근거 문서 `docs/pii_filtering_rules.md` 변경 이력 행. **벤치 우회(65자 접기·숫자열 접기·두 갈래 조각 검사)는 걷어냈다** — 제품 함수가 선형이 돼 필요 없고, 접기는 64자 넘는 연속열 안의 값을 제품과 다르게 판정하는 차이를 남긴다 |
| 139 연동 | **반영(2026-10-06 · `plans/139` W6·W7 · 사용자 커밋 `9cc8c6b`·`8f9b1c4`·`bac814c`)** | W6: `catalog.py` `apply_server_annotations`(설명·유사어를 서버와 같은 순서 Redis → 파일로 · 읽은 곳 `annotation_sources`) · 테이블 의미는 승인 `table_definitions.manages` 우선 · `_serve.py` `schema_context.dbs.<db>`에 `prompt_tokens_est`·`budget_stage`·`backend_reported_tokens`·`selection_source`·`selected_count`·종결 사유(숫자·짧은 열거만) · 되물음 `clarification` 기록(종류·칩 여부·후보 소스 id만) · 분류 16종(`backend_limit`·`selection_none` 추가) · §9 지연 문구 평면별 · closed 카나리아 「해당 없음」 · `PersistentSchemaCache.save()` 부가 필드 보존(G-5 유실 재현). W7: `scenarios.closed.yaml`에 2회차 관찰 4건 — ITAM-115(서버 용도 기재) · 116(서버 분류 경로별 분포) · 117(스토리지 할당의 업무 이름) · 118(스토리지 업무 → 서버 연결) — 서비스↔서버 연결 후보(139 §4.2)를 하나씩 건드린다(18건 · 19턴 · 전부 관찰 · 운영 108테이블 식별자 린트 0). ITAM-114 기대값 유지(139 G-6 — 사용률 정본은 관측 DB). 2회차 전 내부망 관리자: 시드 정의(`testdata/itam_bench/closed/table_definitions.yaml`) 가져오기 → 내부망 정의서와 대조·수정 → 테이블 단위 승인 · `tcdmsif81`(계정 비밀번호 칸) 조회 대상 제외 권고(부록 A 2a). 2회차 반입 뒤 우리: 정답 SQL·`gold_tables`(선별 재현율) · `[토큰예산]` 로그로 추정 계수 대조 |

**계획 대비 이탈(근거)**: ① 판정 키 규칙을 「pii 아님」에서 「general 만」으로 좁힘(상세가 키 값을 싣는다) ② SQL 리터럴 가림을 기본 거부로 뒤집음(추출기가 못 잡는 비교 꼴) ③ 하네스 변경 2곳(진입 모듈 하드코딩) ④ 오라클 접미사를 W0 로 앞당김 ⑤ ITAM-05·06·19·20·21 판정 방식 교정(§3.2.3) · ITAM-11 프롬프트 「5년 넘은」 → 「5년 이상 된」(넘은 = 초과로 읽혀 경계 포함 오라클 ③과 어긋남) · ITAM-16·19·20 에 「자산관리」 명시(관측 DB 와 겹치는 질문이라 소스 선별이 섞인다) · ITAM-19 「CPU 코어」 → 「CPU가」(컬럼 정의가 「CPU 개수」) ⑥ 드라이버 단위 시험은 가짜 주입(모의 서버는 하네스 카탈로그만 안다) ⑦ 누출 관문 결과는 `run.json`이 아니라 `leak_check.json`에 둔다(`run.json`도 관문 대상이라 자기 결과를 담을 수 없다) ⑧ 산출물은 임시 디렉터리가 아니라 **메모리**에서 관문을 거친다(실패 시 지울 파일 자체가 없다).

---

## 부록 A. 폐쇄망 실행·반출 절차 (W8 1회차 · 2회차부터 `plans/140` 개정 · 사용자 실행)

**목적**: 내부망 「DB 구조」 탭이 만든 스키마로 1회차(관찰)를 돌리고, 반출한 로그로 우리가 서비스↔서버 연결 위치를 찾아 2회차 정답 SQL 을 쓴다(G-7 답).

**2회차부터 순서(`plans/140` W6 · D-311)**: 0 반입 → 1 → 2b 「DB 구조」 탭 자산 자동 생성 1단계(**필수**) → 2c 코드값·코드 라벨만 승인 → 2a 테이블 정의 확인·필요 시 편집 → 3~6 `--run --env closed` → 7 반출 **6파일** → 10 외부망 빌더. 내부망에서 정의를 고친 뒤에는 「편집 → 벤치 → 반출」 순서를 지킨다(반입이 내부망 `itam.yaml`·시드를 덮는다 — 되돌리기는 버전 보관소 `.cache/structure/itam/versions`).

| # | 단계 | 확인 |
|---|---|---|
| 0 | (2회차부터 · `plans/140` W3) **반입** — 외부망 빌더가 쓴 `config/db_profiles/itam.yaml`(조회 대상 98 · 테이블 정의 108 · 근거 있는 식별 키·관계·쿼리 규칙만 · **코드값·코드 라벨 없음** · `source: manual`)과 (있으면) `config/synonym_seeds/itam.yaml` | 반입은 내부망 `itam.yaml`·시드를 덮는다 · 내부망에서만 승인한 코드값은 반입마다 2c에서 다시 승인한다 |
| 1 | 관리자 화면 「DB 구조」 탭 → `itam` **등록(schema 단계)** 또는 **DDL 등록** | `.cache/schema/itam_schema.json` 생성 — 108테이블 |
| 2 | 같은 탭에서 **설명 적용**(필요하면 자산 자동 생성 승인 — `plans/133`) | 설명·관계가 많을수록 카탈로그 의미·관계가 채워진다(없어도 1회차는 돈다 — 「자산 없는 기준선」) |
| 2b | (2회차부터 · `plans/140` W1·W2 · **필수**) 같은 탭에서 **자산 자동 생성 1단계(P1 · LLM 0)** 실행 — 한글 컬럼도 코드 후보(`코드·구분·여부·상태·유형·종류·등급·분류·단계`)·같은 이름 식별자 관계 후보를 본다 | 벤치(`--env closed` 기본 `--schema-source structure_store`)가 이 P1 초안·스냅샷·DDL 주석을 Redis에서 읽는다(벤치 DB 조회 0). 초안이 없으면 리포트 첫머리에 「P1 근거 없음 — 자산 없는 기준선」 |
| 2c | (2회차부터 · `plans/140` W6) P1 초안에서 **코드값·코드 라벨만** 골라 승인(직접 커밋 프로필에는 코드값이 없다 · 그 밖 자산은 반입본 유지) | 코드값·라벨의 정본은 내부망 P1 승인이다(D-311 ③) |
| 2a | (2회차부터 · `plans/139` W7) 같은 탭에서 **테이블 정의** 확인 — 반입본에 시드 정의 108건이 이미 있다(`plans/140` W3). 내부망 테이블 정의서와 대조해 관리 정보·주의를 고침 → 테이블 단위 **승인**. `tcdmsif81`(가상화 관리 서버 — 계정 비밀번호 칸)은 반입본에서 조회 대상 기본 제외 | 위쪽 줄 「승인된 정의 수 · 조회 대상 중 정의된 수」(매뉴얼 A-67). 정의가 승인돼야 질의가 정의로 테이블을 고른다(D-308 ②) — 승인 전이면 1회차처럼 조회 대상 99개가 실리고, 예산 사다리 강등·명시 실패만 달라진다 |
| 3 | 두 평면이 FabriX(워커)·vllm(오케스트레이터) 등 **비과금**인지: `python -m scripts.bench --show-env` | 과금 평면이면 드라이버가 거부한다(`RUN_E2E=1` 없이 열리지 않음) |
| 4 | 점검만: `python -m scripts.itam_bench --dry-run --env closed` | 18건 · 관측 19턴 · 린트 통과(2회차 관찰 ITAM-115~118 포함 — `plans/139` W7) |
| 5 | 실행: `python -m scripts.itam_bench --run --env closed`(계정은 `--user`, 비밀번호는 `--password` — 산출물에는 남지 않고 관문이 원값을 대조한다 · 셸 기록·프로세스 목록에는 남을 수 있다) | 사다리 `intent_orchestration` 확정 · 로그인·`itam` 인가 · 실행 카탈로그 기준 프롬프트 재린트 통과 · 끝에 「산출물 기록」 |
| 6 | 누출 관문 실패면(「미기록」) `results/itam_bench/<run_id>/leak_check.json`의 **위치만** 보고 정책·시나리오를 고친 뒤 다시 돈다 | 값은 파일에 없다 |
| 7 | **반출은 `results/itam_bench/<run_id>/`의 파일뿐** — `run.json`·`schema_catalog.yaml`·`trace.jsonl`·`report.md`·`leak_check.json` + (2회차부터 · P1 초안이 있으면) **`code_samples.yaml`**(코드 컬럼 형식 보존 치환값 · 대응표 없음 · 외부망 쿼리 생성 테스트 전용 — `plans/140` W2 · D-311 ②) = **6파일** | **제품 로그 반출 금지** — `logs/audit-*.jsonl`(원 SQL·사용자 ID)·`logs/` SQL 파일 로그·트레이스·`logs/pii_block/`·체크포인트 DB·`.cache/` 는 반출하지 않는다(D-219 ② · D-301 ④) |
| 8 | (반입 후 · 우리) `python -m scripts.itam_bench --sync <반입 경로>` | 전사본에 없는 테이블·컬럼 · 정책 밖 컬럼(사람 정보 제안) · 서비스 연결 후보 · 관찰 턴 조회처 |
| 9 | (우리) 연결 위치 확정 → `testdata/scenarios/oracles/ITAM-1NN.mariadb.sql` 정답 SQL · `scenarios.closed.yaml` 해당 턴을 `oracle`로 · `column_policy.closed.yaml`에 검토한 `general` 추가 · 사용자 검토 | 2회차부터 자동 대조(`--check-oracle --env closed`로 정답 SQL 먼저 점검) |
| 10 | (우리 · `plans/140` W3) `python -m scripts.itam_bench --build-assets <반입 경로>` → `config/db_profiles/itam.yaml`·(근거 있으면) 유사어 시드·시드 스키마 캐시를 쓴다 → 사용자 커밋 → 다음 회차 0 반입. 치환값은 설정 파일에 넣지 않는다(빌더가 검출 시 거부). 모의 DB(`testdata/itam_closed_sim/` · 3308)·P2 초안(`--p2` · 비과금 평면만)은 2회차 반출 뒤 | 출력 「검증 오류 0」 · 거부면 위치만 나온다 |

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-06 | 최초 작성 — 사용자 지시 ① · 실측(§2) · 설계(§3) · W0~W8 · 게이트 G-1~G-6 · D-301 예약. 코드 0 |
| v1.1 | 2026-10-06 | 사용자 지시 ② *"테스트 시나리오는 사용자 프롬프트로 진행해야 한다"* 반영 — **시나리오 = 사용자 프롬프트**(작성 규칙·린트 §3.2.2 · 사용자 예시 ITAM-01 포함 22건 §3.2.3) · **실행 = 로그인한 사용자 경로**(v1.0의 파이프라인 직접 구동·대상 DB 고정 주입 폐기 → 시나리오 하네스 부품 `ServerProcess`·`ScenarioClient`·`SqlAuditTail`·오라클 재사용 §2.1) · 정답 대조를 EX(결과집합 완전 동치)에서 **실 DB 오라클(keyset·count·argmax·value)**로 — 사용자 결과는 열 구성이 시스템 재량이라 · 시나리오 러너는 응답 원문을 남겨 쓰지 않음(§2.2 L1) · 사용자 정보에 **로그인 계정·JWT·감사 로그 사용자 필드·task 상태의 사용자 필드** 추가(§3.5.4) · 측정 수신기를 벤치 서버 진입에서 설치(§3.6) · 샌드박스 시드 보강(기존 컬럼만) · 실패 분류 `asked_back`·`fabricated`·`permission_denied` 추가 · **G-7(서비스↔서버 연결 위치) 신설**. 코드 0 |
| v1.2 | 2026-10-06 | 사용자 지시 *"135번 계획을 검토하여 업데이트하고 구현을 시작하라."* — 현 코드 대조 재실측. **인용 교정**: 서버 부품 이름 `ServerProcess` → `ServerHandle`(`server.py:172`) · 진입 모듈이 `start()`에 하드코딩 → 하네스 변경을 1곳에서 **2곳**으로(오라클 접미사 + `ServerHandle` 진입 모듈 인자 · 기본값 현행) · `SqlAuditTail`이 실패 사유를 버림 → 벤치 하위 클래스 · `run_oracle`이 `oracle_log.jsonl`에 SQL을 남김 · `evaluate_oracle` 상세가 argmax·value 값 열의 값을 실음 → §3.5.6 판정 상세 거르기 신설 · `_pack_pipeline_result`는 3단 기본 경로를 지나지 않음(M6 — 2단 전부 + 3단 계획 루프만) · 로컬 상태 M7(Docker 꺼짐 · 두 평면 mlx). **판정 교정**: ITAM-05 키 = 시리얼 · ITAM-06 count · ITAM-19 value(`n`)+목록이면 count · ITAM-20 rowset · ITAM-21 observe(코드 컬럼 · G-5 — 시드에 코드값을 지어내지 않는다) · 판정 어댑터 2종(1×1 값 열 이름 · 건수 질문의 목록 응답). **위생 교정**: SQL 리터럴을 「pii 컬럼 비교만 가림」에서 **근거 있을 때만 남김(기본 가림)**으로 · 프로필 머리 주석의 승인자 칸은 옮기지 않음 · 오라클 키는 `general`만. 카탈로그 입력 출처 3종(`schema_cache`·`snapshot`·`transcript`). W0에 오라클 접미사 앞당김 · W4 단위 테스트는 가짜 클라이언트 주입. 상태 `TODO` → `WIP` |
| v1.3 | 2026-10-06 | **W0~W6 구현**(작업 트리 · 커밋 없음 · LLM·DB 호출 0) — `scripts/itam_bench/`(catalog·redact·judge·report·_serve·__main__) · `src/observability/run_capture.py` + `subagents.py` 연결점 · 하네스 2곳 · 시나리오 22·정책 77·오라클 정본 16 · 시드 보강 3컬럼 · 신규 테스트 203(벤치 192 · 연결점 8 · 시드 보강 3) · 독립 보안 리뷰 1회(재현 26건 → 회귀 테스트 · 교정). D-301 본문 등재. 잔여: W0 샌드박스 실 대조(Docker·MCP) · W7(피어 세션 공유 MLX 사용 중 — 단독 조건 미성립) · W8. 이탈 8건은 §9 |
| v1.4 | 2026-10-06 | **사용자 인터뷰 답 반영**(리드 경유) — §6 게이트 전건 답(G-1 초안 작성·검토 · G-2·G-5 기본안 · G-3 현 해석 · G-4 연결점 유지 · G-6 기본 가정 · **G-7 = 내부망 「DB 구조」 탭 스키마로 1회차 관찰 → 반출 → 우리가 정답 SQL → 2회차 대조 · 반출 카탈로그 108테이블 전부** · 로컬 「DB 대조만, LLM 보류」 · `pii_filter` 「같이 수정」). W0 실 대조 완료(리드 실측 — 오라클 16/16 ok · README 3종 불변). 카탈로그 = 탭 산출물 전체(관계 3종 · 승인 프로필 구조만 · 유사어 건수만 · 의미 출처 구별 불가 명시 · 108테이블 무필터). W8 1회차 키트(운영 정책·초안 14건·`--env closed` 기본값·실행 시 재린트·카탈로그 인지 가림·`--sync`·부록 A 절차). `pii_filter` 이메일 탐색 선형화(정규식 불변 · 결과 비트 동일 · 벤치 우회 제거). W7 → 대표 3문항 스모크 정의만(ITAM-01 관찰 · 06 카나리아 · 16 정답 대조 — 규모 사용자 확정 · 선택 근거 §4 · 추정 10~20분 · 실행 보류 · 같은 날 4문항 초안을 교정) |
| v1.5 | 2026-10-07 | **부록 A 개정(`plans/140` W6 · D-311 · 상태 불변)** — 2회차부터 0 반입 · 2b 「DB 구조」 탭 자산 자동 생성 1단계 필수 · 2c 코드값·코드 라벨만 승인 · 2a 정의 확인(반입본 시드 108건 · `tcdmsif81` 기본 제외) · 7 반출 6파일(`code_samples.yaml`) · 10 외부망 빌더 `--build-assets` |
