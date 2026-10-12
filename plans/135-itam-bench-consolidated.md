# ITAM 벤치마크 계획 통합본 — 135 · 139 · 140 · 143 · 145 · 146 · 149 · 150 (종결)

> **통합일**: 2026-10-12 · **요청(사용자)**: *"itam 벤치마크 관련된 종료처리된 계획파일을 통합하여 1개의 파일로 만들어라."*
> **상태**: **완료(잔여 이관)** — 8편 모두 종결. 남은 일은 `plans/151`(6회차 교정 · ITAM 벤치 잔여 단일 장부) §10에 있다.
> **읽는 법**:
> - 각 편은 원 계획서 본문을 **그대로** 옮겼다. 바꾼 것은 제목 수준(한 단계 내림)뿐이다.
> - 저장소 곳곳의 원 번호 참조(`plans/139` §9.4 · `plans/135` 부록 A 등)는 이 파일의 해당 편 같은 절로 읽는다.
> - **폐쇄망 실행·반출 절차서는 「135편 부록 A」가 정본이다.**
> - 원 파일명은 아래 목차의 「원 파일」 칸에 남긴다(git 이력 추적용).

## 목차

| 편 | 제목 | 원 파일 |
|---|---|---|
| 135 | 135. ITAM 자산 질의 벤치마크 — 사용자 프롬프트 시나리오를 실제 사용자 경로로 돌리고, 생성 SQL·실행 결과·스키마 맥락을 개인정보 없이 기록해 ITAM 프롬프트 개선 근거로 쓴다 | `135-itam-query-benchmark-trace-log.md` |
| 139 | 139. ITAM 내부망 1회차 결과 교정 — 테이블 관리 정보 정의 기반 조회 대상 선별 · 입력 한도 초과 · 오류 응답 오표면화 | `139-itam-run1-schema-scope-token-limit.md` |
| 140 | 140. ITAM 자산 외부망 생성 — 반출 로그로 자산을 만들고, 다음 반출부터 생성 근거를 담는다 | `140-itam-offline-asset-build-and-export-evidence.md` |
| 143 | 143. ITAM 지식 자산 폴스타 동등화 — 폴스타 자산 전수 대응표 · 반출 근거로 Claude Code가 자산을 쓰고 외부망에서 바로 커밋 · ITAM 결정적 조립(데이터 템플릿) · 벤치 폐루프로 효과 판정 | `143-itam-knowledge-asset-parity.md` |
| 145 | 145. ITAM 벤치 반출 값 치환 — 가리던 값을 run 안 일관 형식 보존 가짜 값으로 · 관계 등가류로 조인 보존 · 관문 확장 | `145-itam-export-format-preserving-substitution.md` |
| 146 | 146. ITAM 내부망 3회차 결과 교정 — 백틱 식별자 검증 누락 · 선별↔생성 테이블 불일치 · 서비스 연결 탐침 · 4회차 정답 판정 준비 | `146-itam-run3-results-correction.md` |
| 149 | 149. ITAM 내부망 4회차 결과 교정 — 서비스 연결 가이드 전제 오류 · 선별 밖 테이블 증가 · 이름 칸 등호 · 반출 판독 결함 | `149-itam-run4-results-correction.md` |
| 150 | 150. ITAM 내부망 5회차 결과 교정 — 지시어 표본 승계 · 근거 묶음 관문 실패 · 마트 단독 질문의 불필요 조인 · 반입 지문 줄바꿈 | `150-itam-run5-results-correction.md` |

---

## 135. ITAM 자산 질의 벤치마크 — 사용자 프롬프트 시나리오를 실제 사용자 경로로 돌리고, 생성 SQL·실행 결과·스키마 맥락을 개인정보 없이 기록해 ITAM 프롬프트 개선 근거로 쓴다

> **작성일**: 2026-10-06 · **v1.0** · **v1.1**(2026-10-06 — 사용자 지시 *"테스트 시나리오는 사용자 프롬프트로 진행해야 한다"* 반영: 시나리오 = 사용자 프롬프트 · 실행 = 로그인한 사용자 경로(`/api/v1/query/stream`) · 대상 DB 고정 주입 폐기 · 정답 대조 = 실 DB 오라클 · 게이트 G-7 신설) · **v1.2**(2026-10-06 — 사용자 지시 *"135번 계획을 검토하여 업데이트하고 구현을 시작하라."* · 현 코드 대조 재실측 · 인용 교정 · 판정 방식 교정 · 하네스 변경 2곳 — 변경 이력) · **v1.3**(2026-10-06 — W0~W6 구현 · 진행 기록 §9 · D-301 본문 등재) · **v1.4**(2026-10-06 — 사용자 인터뷰 답 반영: G-1~G-7 확정 · 카탈로그 = 「DB 구조」 탭 산출물 전체 · 폐쇄망 1회차 키트(관찰) · `pii_filter` 이메일 탐색 선형화 · W7 = 스모크 정의만)
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — W0~W6 완료 · W8 1회차 키트 완료(작업 트리 · 커밋 없음)** · 잔여: W7 스모크(사용자 「LLM 보류」 — 정의만) · 내부망 1회차 실행(사용자) → 반출 로그로 정답 SQL(2회차)(§9). 게이트 G-1~G-7 전건 답(2026-10-06 인터뷰 · §6). 결정 **D-301**(§8).
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

### 0. 요약

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

### 1. 요구 분해와 해석

#### 1.1 요구 → 수용 기준

| # | 원문 구절 | 요구 | 수용 기준(§5에서 검증) |
|---|---|---|---|
| R1 | "mcp에 연결된 itam db … 조회하는 테스트 시나리오를 실행" | 운영과 같은 서버 · MCP `itam` 소스로 실행 | 2단 확정 서버에서 전 시나리오 실행 · 조회 DB에 `itam` 포함 여부 기록 |
| **R7** | ②"테스트 시나리오는 사용자 프롬프트로 진행" | 사용자 말투의 프롬프트 · 로그인한 사용자 경로 · 대상 DB 고정 주입 없음 | 프롬프트 린트(§3.2.2) 위반 0 · 요청 본문은 `query`(와 되묻기 응답)뿐 |
| R2 | "생성된 쿼리 실행결과" | 시스템이 실행한 SQL 전문 + 사용자가 받은 결과 | 턴마다 감사 로그의 실행 SQL · `download-csv` 결과 요약(가림 처리) · 실패 사유 |
| R3 | "스키마 관련된 정보를 로그로" | ITAM이 관리하는 정보 목록 + 턴별로 LLM이 본 스키마 맥락 | `schema_catalog.yaml`(run당 1개) + 레코드별 `schema_context` |
| R4 | "itam에서 관리하는 정보와 실행결과를 보고 … 프롬프트의 성능을 높일 수 있도록" | 정답 대조 + 실패 원인 분류 + 고칠 자산 지목 + 개선 전후 비교 | 오라클 판정 · 실패 분류 14종(§3.3) · 분류→자산 대응표 · `--compare` |
| R5 | "사용자 정보는 마스킹하고 개인정보는 포함되지 말아야" | 로그인 계정·실행자 식별자 가림 + 결과 속 개인정보 0건 | 누출 관문(§3.5.5) 통과 · 카나리아 성명 0건 · 위반 시 산출물 미기록 |
| R6 | "스키마 정보는 스키마 쿼리 형식이 아닌 다른 방식으로" | DDL·`information_schema` 조회문이 아닌 구조화 표현 | 스키마 절에 `CREATE TABLE`·`SHOW CREATE`·`information_schema` 문자열 0건 |

#### 1.2 해석 — 확인이 필요한 세 가지 (게이트 G-3·G-2로 확정)

1. **"스키마 쿼리 형식이 아닌 방식"** → 스키마를 **테이블·컬럼 목록 형태의 구조화 문서(YAML)**로 남긴다. 예: `TCDMSIF79 → 컬럼 hWSportEndYmd · 의미 "하드웨어 지원 종료일자" · 날짜문자열(YYYYMMDD) · 키 아님`. `CREATE TABLE …` 같은 DDL이나 `SELECT … FROM information_schema …` 같은 조회문은 남기지 않는다. 덧붙여 **벤치는 스키마를 얻으려고 DB에 조회문을 따로 날리지 않는다** — 서버가 이미 쓰는 스키마 캐시와 승인 자산(또는 `scripts/itam_erd.py` 스냅숏 JSON)을 읽는다.
2. **"사용자 정보"** → ①**시나리오를 보내는 로그인 계정** — 하네스 내장 테스트 계정 `user_id`(`scripts/scenario/runner.py:88` — 사번 형태)나 `--user`로 준 계정, 그 JWT, 감사 로그의 사용자 필드 ②**벤치를 돌린 사람·접속 계정** — OS 사용자명, 머신 이름, 홈 디렉터리 경로, DB 접속 계정(운영 `SDQ000` 등)·비밀번호. 가림 규칙은 D-300 ④와 같게(`앞 1자 + ***`) · 토큰은 가린 형태로도 남기지 않는다.
3. **"개인정보"** → ITAM 데이터 안의 사람 정보: 담당자 성명(`rspblPsnEmnm`)·직원번호(`rspblPsnEmpid`)·시스템 사용자번호(`sysRegiUno`·`sysLastUno`)와 운영 108테이블에 있을 연락처·이메일·주소 등. 이 값은 **가린 형태로도 남기지 않는다**(성씨 1자도 · 해시도 — 이름은 경우의 수가 작아 해시가 역산된다).

> **`plans/95` G-9와의 관계** — G-9는 *"조회 결과에 성명·금액을 마스킹 없이 보여준다"*로 확정됐다(2026-09-17). 이것은 **사용자 화면 응답**에 대한 결정이고, 이 계획은 **벤치 로그 파일**에 대한 규칙이다. 제품 응답 동작은 바꾸지 않는다. 바로 그 G-9 때문에 벤치가 받는 결과 CSV에는 성명이 그대로 들어온다 — 이 벤치가 별도 위생 계층을 갖는 이유다.

---

### 2. 현행 실측 (2026-10-06)

#### 2.1 재사용할 것 — 사용자 경로 부품은 시나리오 하네스에 이미 있다

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

#### 2.2 그대로 쓰면 개인정보·사용자 정보가 새는 곳

| # | 지점 | 실측 | 이 벤치의 처리 |
|---|---|---|---|
| L1 | **시나리오 러너 `raw.jsonl`** | `scripts/scenario/runner.py:801` 응답 원문(`response_text`, 4,000자)을 위생 없이 기록 · 판정 재료(재판정)라 러너에서 빼기 어렵다 | **ITAM 시나리오를 시나리오 러너로 돌리지 않는다.** 부품(§2.1)만 쓰는 별도 드라이버 · 시나리오 파일도 러너가 읽는 `testdata/scenarios/*.yaml` 밖에 둔다(실수로 섞여 돌지 않게) |
| L2 | **서버 표준출력 로그** | 감사 줄(`query_executed` — 사용자 ID·SQL)·앱 로그가 섞인다. 하네스는 run 디렉터리 `logs/server-*.log`로 남긴다 | 벤치는 세션 임시 디렉터리에 두고 SQL 수집·사다리 판독에만 쓴 뒤 **실행 종료 시 지운다** — 산출물이 아니다 |
| L3 | **LLM에 주는 스키마에 실 데이터 표본이 섞인다** | `src/nodes/schema_analyzer.py:323`·`:789` `sample_data` — ITAM이면 담당자 성명·직원번호 | 스키마 맥락은 **이름·의미 유무만** 수집하고 `sample_data`는 "있었다/없었다" 불린 |
| L4 | **task 상태에 요청 사용자 정보가 실린다** | `src/orchestration/subagents.py:1137-1139` 격리 상태에 `thread_id`·`user_id`·`user_department` | 측정 연결점의 수신 측은 `thread_id`(결합 키)만 복사하고 사용자 필드는 읽지 않는다 |
| L5 | 기본 마스킹 대상이 개인정보가 아니다 | `src/config.py:470` `sensitive_columns`는 비밀·토큰 계열 · `src/security/data_masker.py:97`은 **컬럼명 부분 일치**(→ `sevrHostName`의 `Name` 같은 오탐 구조 · `docs/18_known_mistakes.md` 2026-09-11 사례) | 카탈로그 분류(§3.5.1)로 판정 |
| L6 | 정규식 규칙에 **성명 규칙이 없다** | `pii_filter.PII_RULES`는 연락처·계좌·이메일·주민번호·카드 | 성명은 **컬럼 분류 + 실제 결과에서 모은 값 대조**로 막는다 |
| L7 | DSN 가림이 계정명을 남긴다 | `scripts/itam_erd.py:89` `mask_dsn`은 비밀번호만 가림 | 계정·비밀번호 둘 다 가린다 |

#### 2.3 측정이 닿지 못하는 곳

| # | 실측 | 영향 |
|---|---|---|
| M1 | 2단 task 결과에 스키마 맥락이 없다 — `src/orchestration/subagents.py:1788` `_pack_pipeline_result`가 결과·SQL만 접는다. 스키마 분석은 `:909`에서 task 내부 상태로만 존재하고, 사용자 경로에서는 **서버 프로세스 안**에 있다 | 「정답 테이블을 LLM이 봤는데 틀렸나 / 아예 못 봤나」를 구별 못 한다 → §3.6 측정 연결점 |
| M2 | 단계 트레이스는 값을 일부러 안 담는다 — `src/observability/trace_collector.py:233` `_summarize_delta` | 재사용 불가(설계 의도가 반대) |
| M3 | 시나리오 오라클의 엔진 접미사가 PG·DB2뿐 — `scripts/scenario/oracle.py:102` | MariaDB 정본 파일을 못 읽는다 → 접미사 1줄 추가(W4) |
| M4 | 로컬 `.env`는 사다리 1·2·3단 플래그가 모두 true | 프로파일 `tier2_intent` 주입 + `ServerProcess.ladder`로 확정 단 검사, 불일치면 중단 |
| M5 | 사용자별 DB 조회 인가(D-232) — 벤치 계정이 `itam`을 조회할 권한이 없으면 전 시나리오가 「권한 없음」으로 끝난다 | 사전 점검에서 계정 권한 확인 · 없으면 실행 0·사유 출력 |
| M6 | **(v1.2 실측)** `_pack_pipeline_result`를 쓰는 곳은 2단 핸들러(`subagents.py:1720`·`:1732`)와 3단 **계획 루프** task 서브그래프(`tier3_plan.py:400` — `TIER3_PLAN_LOOP_ENABLED`)뿐이다. 3단 기본 경로(라우터 → `schema_analyzer` 그래프 노드)는 이 함수를 지나지 않는다 | 연결점은 2단 전부 + 3단 계획 루프만 덮는다. 3단 기본 경로 run은 `schema_context: null`로 남고 `schema_miss`·`meaning_absent`는 「판정 불가」로 집계한다(리포트 고지) — 노드 쪽 연결점 추가는 비범위 |
| M7 | **(v1.2 실측)** 2026-10-06 로컬 상태 — 두 평면 `mlx`(`python -m scripts.bench --show-env`) · `ACTIVE_DB_IDS=polestar,itam` · `DB_BACKEND=dbhub` · **Docker 데몬 꺼짐**(ITAM 3307·PG·Redis 없음) · MCP 9099 미기동 · MLX 8080 기동 중 | W7 실측은 Docker·MCP 기동이 전제 — 같은 장비 다른 세션 사용 여부를 먼저 본다 |

#### 2.4 ITAM 쪽 사실

- **로컬 샌드박스**(`testdata/itam/`): MariaDB 11.4 · 3307 · `INST1` · 테이블 2개(`TCDMSIF80` 68컬럼 30행 · `TCDMSIF79` 9컬럼 29행) · **컬럼 주석(COMMENT) 0건** · 합성 성명 3종(`generate_init.py:141`)·합성 직원번호 `T000000…T000029`(`:210` — `T{i:06d}`)·감사 사용자번호 `T000000` — **누출 카나리아로 바로 쓸 수 있다.** 사람이 쓴 오라클 SQL 3종이 있다(`testdata/itam/README.md` ①지원 종료 6개월 ②이번 분기 유지보수 만료 ③경과년수 5년 이상).
- **(v1.2 실측) 코드 컬럼은 생성기가 강제로 표식을 넣는다** — `_fill`(`generate_init.py:240`)이 `code:`가 붙은 컬럼에 `Z`·`Z9…`를 쓰고 `test_code_columns_are_synthetic_markers`가 고정한다. 가상화 여부(`vrtlSevrMapngYn` — 코드 도메인 「여부」·값 목록 미확보 G-5)를 `Y`/`N`으로 채우면 **코드값을 지어내는 것**이 된다 → 시드 보강에서 뺀다(ITAM-21은 `observe`).
- **★샌드박스로는 사용자 프롬프트 다수를 답할 수 없다** — 시드 기준값(`generate_init.py:200-226` `_base_main`)이 CPU 8·메모리 64·스토리지 500 고정이고, 운영체제·모델·가상화 여부·**서비스명은 비어 있다.** *"운영체제별 서버 수"* · *"메모리가 제일 큰 서버 5대"*는 값이 같거나 비어서 정답 대조가 성립하지 않고, *"통합인증 서비스의 서버리스트"*는 **서비스와 서버를 잇는 데이터가 샌드박스에 없다.** → W0에서 **기존 2테이블의 기존 컬럼에만** 합성 값을 넣어 보강한다(새 테이블·새 컬럼 금지 — 전사본에 없는 구조를 지어내지 않는다 · `testdata/itam/schema.yaml` 머리 주석).
- **서비스명이 ITAM 어디에 있는지 모른다** — 저장소 전체에서 「통합인증」 0건 · ITAM 서비스/업무명 출처 0건(`plans/130` §1이 *"자산관리 스키마의 「업무」는 문서 이름이라 서버 업무명의 출처가 아니다"*라고 기록). 운영 108테이블 중 어느 테이블이 서비스↔서버를 잇는지는 운영 스냅숏(`itam_erd`)이나 포탈 화면으로 확인해야 한다 → **게이트 G-7**.
- **운영**: 테이블 108개, 접두 6군(`TCDMSAM`·`BR`·`CM`·`GT`·`HR`·`IF` — `plans/95` G-4 v15) · database `INST1` · SELECT 전용 계정·방화벽 확인(`plans/95` G-2·G-3 ✅).
- **현재 ITAM 프롬프트 자산의 상태**: `config/db_profiles/itam.yaml`(104 로컬 승인본 · 20줄)의 `query_guide`는 *"감지할 수 있는 특수한 구조적 패턴은 없습니다"*라는 LLM 서술문이고 실제 규칙(날짜 문자열 · 3열 복합 조인 · 방언)은 0건이다. `config/knowledge/itam/`·`config/synonym_seeds/itam.yaml`은 없다. → **이 벤치의 첫 run이 "자산 없는 상태"의 기준선**이 되고, 133 자산 승인 뒤 run과 비교하면 효과가 숫자로 나온다. **(v1.2 실측)** 파일 스키마 캐시의 컬럼 설명은 9건(`TCDMSIF79` 전 컬럼 · LLM 생성 캐시 설명)이다. 프로필 머리 주석에 **승인자 칸**(`… · anonymous · env=…`)이 있다 — 운영에서는 사람 ID가 될 수 있으므로 카탈로그는 머리 주석을 옮기지 않고 지문·`source`·`environment`만 남긴다.
- **방언 함정(샌드박스 리허설 · `testdata/itam/README.md`)**: `::numeric`·`INTERVAL '1 day'`는 구문 오류, **`||`는 OR로 해석돼 0, `"col"`은 문자열 리터럴로 해석돼 0행** — 오류 없는 오답이라 정적 검출이 필요하다.

---

### 3. 설계

#### 3.1 구성과 흐름

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

#### 3.2 사용자 프롬프트 시나리오

##### 3.2.1 형식 (`testdata/itam_bench/scenarios.yaml`)

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

##### 3.2.2 프롬프트 작성 규칙 (로더가 린트로 강제)

1. **사용자가 채팅창에 치는 말 그대로** — 반말·존댓말·축약("서버리스트", "몇 대야?")을 섞는다.
2. **테이블·컬럼·SQL 용어 금지** — 카탈로그의 테이블·컬럼 식별자(`TCDMSIF80`·`manmenCtrcEndYmd` 등)와 `SELECT`·`JOIN`·`WHERE`·`조인`·`컬럼`·`테이블` 같은 말이 들어가면 로더가 거부한다.
3. **업무 말** — 서비스·시스템 이름, 서버리스트, 담당자·담당 부서, 유지보수 계약, 지원 종료(EOS/EOL), 노후·도입 연수, 취득금액, 자산 상태, 가상/물리, 사양.
4. **소스 지칭 변형을 짝으로** — 같은 의도를 「자산관리 시스템에서 …」·「ITAM에서 …」·생략형으로 나눠, 소스 선별 실패(`routing_miss`)와 ITAM 프롬프트 실패를 갈라 본다.
5. **사람 이름·사번을 프롬프트에 넣지 않는다** — 프롬프트는 로그에 그대로 남으므로 그 자체가 개인정보 통로가 된다(카나리아·`scan_pii`로 검사). 「사람 이름으로 담당 서버 찾기」는 비범위(§7).
6. **멀티턴 포함** — 후속 질문("그 서버들 지원 종료일도 알려줘")으로 승계 동작을 함께 본다.

##### 3.2.3 시나리오 초안

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

#### 3.3 실행·채점·분석

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

#### 3.4 스키마 로그 — 조회문이 아닌 구조화 카탈로그

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

#### 3.5 로그 위생 — 개인정보·사용자 정보

##### 3.5.1 컬럼 기록 등급 (기본 거부)

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

##### 3.5.2 값 대조 차단

턴 처리 중 `pii` 등급 열에서 나온 값(길이 2 이상)을 **메모리에만** 모은다(run 종료 시 폐기 · 파일 기록 없음). 이 값이 `general` 열 값·SQL 리터럴·오류 문구 어디에 나타나면 그 칸을 등급 강등(→ 값 미기록)하거나 `'<가림:pii>'`로 바꾼다. 개인정보 컬럼을 일부러 따로 조회해서 값을 모으지는 않는다(그 조회 자체가 개인정보 처리다).

##### 3.5.3 SQL·문구 가림

- **SQL 리터럴**(v1.2 — 기본 거부로 뒤집음): 문자열 리터럴은 **남겨도 되는 근거가 있을 때만** 남긴다 — ①`general`·`network` 컬럼과 비교된 리터럴(`= '…'`·`LIKE '…'`·`IN ('…')` — `_extract_column_literals` 재사용 · `network`는 끝자리 가림) ②날짜·숫자 모양(`^[0-9:./ -]+$`) ③서식 문자열(`%Y%m%d` 류) ④그 턴의 사용자 프롬프트에 그대로 있는 말(`%`·`_` 제외 — 프롬프트는 린트를 거쳐 사람 정보가 없다). 나머지는 `'<가림>'`이다. `_extract_column_literals`는 `<>`·`!=`·`NOT LIKE`·`'값' = 컬럼` 꼴을 못 잡으므로 v1.1처럼 「pii 컬럼 비교만 가림」으로 두면 그 꼴로 이름이 샌다. 남긴 리터럴도 §3.5.2 값·카나리아·`scan_pii`와 대조한다. MariaDB 기본 모드에서 큰따옴표는 문자열 리터럴이라 같은 규칙을 쓰되, 카탈로그 식별자와 같은 내용(`"sevrHostName"` — 방언 함정의 증거)은 남긴다. SQL 구조(테이블·컬럼·조건 형태)는 그대로 남는다 — 프롬프트 개선에 필요한 것은 구조다.
- **(v1.3 보강 · 보안 리뷰)** SQL 은 주석·문자열·백틱을 한 번에 토큰으로 나눠 처리한다(주석 속 아포스트로피가 짝을 뒤집던 구멍). 주석은 가리고, 리터럴은 같은 술어 조각(AND·OR·WHERE 등 경계)에 `pii`·`free_text`·미분류 컬럼이 있으면 모양과 무관하게 가린다(프롬프트 말만 예외) · 수 모양은 4자리 이하·날짜·연월·시각만 통과 · 서식은 `%`지시자+구분자 40자 이하 · 따옴표 없는 비ASCII 낱말은 프롬프트나 결과 요약이 남긴 열 이름이 아니면 가린다 · IP 는 어디서든 끝자리 가림. 결과 열 이름(별칭)도 근거(카탈로그 이름·프롬프트 말·ASCII 식별자·일반/IP/금액 컬럼만 가리키는 별칭)가 있을 때만 남기고 아니면 `열#n`.
- **오류 문구**(DB 오류·시스템 사유): 같은 대조 + 500자 절단. MariaDB 오류는 문제 구문 일부를 되돌려주므로 리터럴이 섞인다. **(v1.3)** `near '…' at line N` 조각은 통째로 가리고, 그 턴 SQL 들의 가린 리터럴·주석 내용을 문구에서 지운다.
- **응답 본문**: 기록하지 않는다(길이·고지 종류만 — G-5).
- **정규식 상한**: 입력 길이 상한과 백트래킹 없는 패턴만 쓴다(`docs/18` 2026-08-19 ReDoS 사례 · 20KB 입력 50ms 이내 단언).

##### 3.5.4 사용자 정보(로그인 계정·실행자)

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

##### 3.5.5 누출 관문 (기록 전 마지막 단계)

산출물은 임시 디렉터리에 쓴 뒤 아래를 **모두** 통과해야 `results/itam_bench/<run_id>/`로 옮긴다.

1. **카나리아** — 정책 파일 `canaries`(샌드박스: 합성 성명 3종·합성 직원번호 패턴)가 어느 파일에도 0건
2. **수집 값** — §3.5.2에서 모은 개인정보 값이 0건
3. **정규식** — `scan_pii` 규칙(연락처·이메일·주민번호·계좌·카드) 0건
4. **스키마 형식** — §3.4 금지 문자열이 스키마 절에 0건
5. **사용자 정보** — 로그인 계정 ID·실행자 이름·홈 경로·DSN 계정이 0건(가린 형태만 허용)

실패하면 산출물을 옮기지 않고 `leak_check.json`만 남긴다 — **어느 파일·어느 레코드·어느 필드·어느 규칙인지만 적고 걸린 값은 적지 않는다** — 그리고 exit 1. 조용히 지우고 성공 처리하지 않는다.

##### 3.5.6 판정 상세 거르기 (v1.2 신설)

`evaluate_oracle`의 상세는 「값은 키만」이지만 키 자체가 값이고, `argmax`는 `oracle_top`·`value_diffs`에 **값 열의 행별 값**을, `value`는 스칼라를 싣는다(§2.1). 벤치는 상세를 그대로 쓰지 않고 등급으로 다시 거른다.

| 상세 칸 | 기록 |
|---|---|
| 키 값(`missing`·`extra`·`oracle_top`·`system_top`의 키 부분) | 키 열이 `general`이면 값(로더가 키를 `general`로 강제 — §3.2.1) · 그 밖이면 건수만 |
| 값 열의 값(`argmax`의 수치 · `value`의 `oracle`·`system`) | 값 열이 `amount`면 **행별 값 제거**(일치 여부·건수만 · `value` 스칼라 합계는 `amount` 규칙대로 남김) · `general`이면 그대로 |
| `header`(열 이름 목록) | 열 이름은 남긴다(값 아님) |
| 오라클 SQL · `oracle_log.jsonl` | 세션 임시 디렉터리 — 산출물 아님 |

#### 3.6 측정 연결점 (본체 최소 변경)

사용자 경로에서는 파이프라인이 **서버 프로세스 안**에서 돈다. 2단 task가 LLM에 준 스키마 맥락(§2.3 M1)을 받으려면 본체에 연결점이 하나 필요하다(G-4).

- **신규** `src/observability/run_capture.py`(infrastructure 계층): 프로세스 전역 수신 함수 하나를 꽂는 `install(sink)`·`uninstall()`과 `emit(kind, payload)`. 수신 함수가 없으면 즉시 반환 — **기본 동작 비트 동일**. 수신 함수의 예외는 삼키고 debug 로그만 남긴다(측정이 제품 흐름을 깨지 않는다).
- **호출 1곳** `src/orchestration/subagents.py` `_pack_pipeline_result` — `emit("task_pipeline_state", s)`. 이 함수는 2단 핸들러와 3단 task 서브그래프가 함께 쓴다(같은 함수 docstring) — 한 곳으로 2단 전부와 3단 **계획 루프**를 덮는다. **v1.2 교정**: 3단 기본 경로(라우터 → 그래프 노드)는 이 함수를 지나지 않는다(§2.3 M6) — 그 run의 스키마 맥락은 `null`이다.
- **설치는 벤치 서버 진입에서만** — `scripts/itam_bench/_serve.py`가 앱 import 전에 수신기를 설치한다. 운영 기동(`python -m src.main --server`)·시나리오 하네스 기동(`scripts/scenario/_serve.py`)에는 설치 코드가 없다.
- **수신기 규칙**: 받은 상태 객체를 보관하지 않는다. 그 자리에서 `thread_id`·`relevant_tables`·스키마 컬럼 **이름**·컬럼별 의미 보유 여부·`sample_data` 유무(불린)·`_structure_meta` 유무만 복사해 임시 파일(세션 임시 디렉터리)에 한 줄 쓰고 놓는다. **값·사용자 필드는 읽지 않는다.**
- 스키마 리터럴 0(`overfit_check` 스캔 대상 `src/orchestration`) · 계층 방향 orchestration → infrastructure(허용 · `arch_check`).

#### 3.7 산출물

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

### 4. 작업 분해

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

### 5. 수용 기준

1. **사용자 프롬프트** — 시나리오 프롬프트가 린트(§3.2.2)를 전건 통과하고, 요청 본문에 `query`·되묻기 응답 외의 대상 DB·스키마 지정이 0건이다.
2. **실행** — 샌드박스 22건이 2단 확정 서버에서 로그인한 사용자 경로로 끝까지 돌고, 턴마다 실행 SQL·결과 요약·스키마 맥락이 있는 레코드가 남는다(SQL 관측 0이면 실패 처리).
3. **대조** — `oracle` 시나리오마다 판정이 있고, 실패 분류와 고칠 133 자산을 리포트가 가리킨다. 라우팅·되물음 실패는 ITAM 프롬프트 수치와 분리 집계된다.
4. **스키마 형식** — 스키마 절에 DDL·`information_schema` 문자열 0건 · 벤치가 스키마 조회문을 직접 실행한 횟수 0.
5. **개인정보 0** — 누출 관문 5단계 통과. 샌드박스 카나리아가 ITAM-06 결과에 실제로 나왔는데도 산출 파일 5종에 0건(나오지 않았다면 관문 시험이 성립하지 않은 것 — 리포트에 고지).
6. **사용자 정보** — 로그인 계정 ID·실행자명·홈 경로·DB 계정 0건, 가린 형태(`5***`)만 존재 · 서버 원시 로그 잔존 0.
7. **제품 무회귀** — 측정 연결점 미설치 상태에서 바꾼 모듈의 모듈 단위 회귀 통과(`python scripts/regress.py` — D-303 · 범위 명시 · 전체 회귀는 사용자 요청 시) · `arch_check --ci`·`overfit_check --ci`·`ruff`·`mypy`(기준선 대비 신규 0).
8. **개선 루프** — 같은 시나리오로 두 run을 `--compare` 하면 시나리오별 전이와 자산 지문 차이가 나온다.

---

### 6. 사용자 확정 게이트

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

### 7. 위험 · 비범위

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

### 8. 의사결정 정합 · 신규 결정 예약

- **충돌 검토**: D-003(읽기 전용 — 시스템 SQL은 MCP `readonly` · 오라클은 하네스 안전 5종) · D-127/D-240(비과금 평면에서만 자동 실행) · D-216(내장 테스트 계정 — 로그에는 가림) · D-232(계정 인가 사전 점검) · D-251(2단 기준 · 3단은 `--profile tier3_router` 비교 arm) · D-219(SQL 관측 0 = 사고 · 반출은 축소본) · D-275(실 DB 읽기 전용 오라클 — 엔진 접미사만 확장) · `plans/95` G-9(응답 노출 방침 불변 — §1.2) · D-214 ⑥(로컬 산출물 커밋 금지 — 벤치 산출물은 `results/` git 제외) — **충돌 없음**.
- **D-301 예약**(2026-10-06 · 「채번 이력」 표 등재): **ITAM 질의 벤치마크 계약** — ①시나리오 = 사용자 프롬프트(테이블·컬럼·SQL 용어 금지) · 로그인한 사용자 경로 실행 · 대상 DB·스키마 고정 주입 금지 ②결과 값 기본 거부(검토된 일반 컬럼만 값 기록 · 사람 정보는 건수만 · 해시 금지) ③스키마는 구조화 카탈로그(조회문·DDL 금지 · 벤치의 별도 스키마 조회 0) ④로그인 계정·실행자·접속 계정 가림(D-300 ④ 규칙) · 토큰 미기록 · 서버 원시 로그 미보존 ⑤누출 관문 실패 = 산출물 미기록·exit 1 ⑥측정 연결점(벤치 서버에서만 설치 · 기본 no-op · 2단·3단 공용 1곳). 본문 등재는 W2·W3 랜딩 시.
  - 채번 실측(2026-10-06): `## D-` 헤더 최댓값 **D-300** · 「채번 이력」 표 최댓값 D-300 · `plans/`·`spec/`의 D-30x 참조는 D-300뿐 → **D-301**.

---

### 9. 진행 기록

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

### 부록 A. 폐쇄망 실행·반출 절차 (W8 1회차 · 2회차부터 `plans/140` 개정 · 사용자 실행)

**목적**: 내부망 「DB 구조」 탭이 만든 스키마로 1회차(관찰)를 돌리고, 반출한 로그로 우리가 서비스↔서버 연결 위치를 찾아 2회차 정답 SQL 을 쓴다(G-7 답).

**2회차부터 순서(`plans/140` W6 · D-311)**: 0 반입 → 1 → 2b 「DB 구조」 탭 자산 자동 생성 1단계(**필수**) → 2c 코드값·코드 라벨만 승인(**`plans/151` W13부터 전 자산 승인** — 0 확인 칸) → 2a 테이블 정의 확인·필요 시 편집 → 3~6 `--run --env closed` → 7 반출 **6파일** → 10 외부망 빌더. 내부망에서 정의를 고친 뒤에는 「편집 → 벤치 → 반출」 순서를 지킨다(반입이 내부망 `itam.yaml`·시드를 덮는다 — 되돌리기는 버전 보관소 `.cache/structure/itam/versions`).

| # | 단계 | 확인 |
|---|---|---|
| 0 | (2회차부터 · `plans/140` W3) **반입** — 외부망 빌더가 쓴 `config/db_profiles/itam.yaml`(조회 대상 98 · 테이블 정의 108 · 근거 있는 식별 키·관계·쿼리 규칙만 · **코드값·코드 라벨 없음** · `source: manual`)과 (있으면) `config/synonym_seeds/itam.yaml` | 반입은 내부망 `itam.yaml`·시드를 덮는다 · 내부망에서만 승인한 코드값은 반입마다 2c에서 다시 승인한다 · **(`plans/151` W13 · G-9 (a)) 반입(전체 덮어쓰기) 뒤 P1을 다시 돌려(2b) 전 자산(관계·코드값·쿼리 규칙·교차 질의 식별 키·테이블 정의)을 승인한다** — 반입은 `config/db_profiles/itam.yaml`을 외부망 파일로 바꿔 내부망 P1 승인 자산을 지운다 · 이미 승인된 초안은 다시 승인할 수 없다(`pending`만) · 확인: 리포트 §0 「승인 프로필 P1 자산」의 관계·코드값 칸·쿼리 규칙 수가 모두 0이 아니고, 「교차 질의 식별 키 테이블」이 「—」가 아니며, 경고 줄이 없다(`plans/151` W11 — 경고는 관계 0 **그리고** 코드값 0일 때만 나므로 코드값만 승인하면 경고 없이도 관계·규칙·식별 키가 빠질 수 있다) |
| 1 | 관리자 화면 「DB 구조」 탭 → `itam` **등록(schema 단계)** 또는 **DDL 등록** | `.cache/schema/itam_schema.json` 생성 — 108테이블 |
| 2 | 같은 탭에서 **설명 적용**(필요하면 자산 자동 생성 승인 — `plans/133`) | 설명·관계가 많을수록 카탈로그 의미·관계가 채워진다(없어도 1회차는 돈다 — 「자산 없는 기준선」) |
| 2b | (2회차부터 · `plans/140` W1·W2 · **필수**) 같은 탭에서 **자산 자동 생성 1단계(P1 · LLM 0)** 실행 — 한글 컬럼도 코드 후보(`코드·구분·여부·상태·유형·종류·등급·분류·단계`)·같은 이름 식별자 관계 후보를 본다 | 벤치(`--env closed` 기본 `--schema-source structure_store`)가 이 P1 초안·스냅샷·DDL 주석을 Redis에서 읽는다(벤치 DB 조회 0). 초안이 없으면 리포트 첫머리에 「P1 근거 없음 — 자산 없는 기준선」 |
| 2c | (2회차부터 · `plans/140` W6) P1 초안에서 **코드값·코드 라벨만** 골라 승인(직접 커밋 프로필에는 코드값이 없다 · 그 밖 자산은 반입본 유지) — **`plans/151` W13부터는 0의 「전 자산 승인」으로 읽는다**(테이블 정의는 출처 순위로 반입본 큐레이션이 보존된다 · `plans/151` W1) | 코드값·라벨의 정본은 내부망 P1 승인이다(D-311 ③) |
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

### 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-06 | 최초 작성 — 사용자 지시 ① · 실측(§2) · 설계(§3) · W0~W8 · 게이트 G-1~G-6 · D-301 예약. 코드 0 |
| v1.1 | 2026-10-06 | 사용자 지시 ② *"테스트 시나리오는 사용자 프롬프트로 진행해야 한다"* 반영 — **시나리오 = 사용자 프롬프트**(작성 규칙·린트 §3.2.2 · 사용자 예시 ITAM-01 포함 22건 §3.2.3) · **실행 = 로그인한 사용자 경로**(v1.0의 파이프라인 직접 구동·대상 DB 고정 주입 폐기 → 시나리오 하네스 부품 `ServerProcess`·`ScenarioClient`·`SqlAuditTail`·오라클 재사용 §2.1) · 정답 대조를 EX(결과집합 완전 동치)에서 **실 DB 오라클(keyset·count·argmax·value)**로 — 사용자 결과는 열 구성이 시스템 재량이라 · 시나리오 러너는 응답 원문을 남겨 쓰지 않음(§2.2 L1) · 사용자 정보에 **로그인 계정·JWT·감사 로그 사용자 필드·task 상태의 사용자 필드** 추가(§3.5.4) · 측정 수신기를 벤치 서버 진입에서 설치(§3.6) · 샌드박스 시드 보강(기존 컬럼만) · 실패 분류 `asked_back`·`fabricated`·`permission_denied` 추가 · **G-7(서비스↔서버 연결 위치) 신설**. 코드 0 |
| v1.2 | 2026-10-06 | 사용자 지시 *"135번 계획을 검토하여 업데이트하고 구현을 시작하라."* — 현 코드 대조 재실측. **인용 교정**: 서버 부품 이름 `ServerProcess` → `ServerHandle`(`server.py:172`) · 진입 모듈이 `start()`에 하드코딩 → 하네스 변경을 1곳에서 **2곳**으로(오라클 접미사 + `ServerHandle` 진입 모듈 인자 · 기본값 현행) · `SqlAuditTail`이 실패 사유를 버림 → 벤치 하위 클래스 · `run_oracle`이 `oracle_log.jsonl`에 SQL을 남김 · `evaluate_oracle` 상세가 argmax·value 값 열의 값을 실음 → §3.5.6 판정 상세 거르기 신설 · `_pack_pipeline_result`는 3단 기본 경로를 지나지 않음(M6 — 2단 전부 + 3단 계획 루프만) · 로컬 상태 M7(Docker 꺼짐 · 두 평면 mlx). **판정 교정**: ITAM-05 키 = 시리얼 · ITAM-06 count · ITAM-19 value(`n`)+목록이면 count · ITAM-20 rowset · ITAM-21 observe(코드 컬럼 · G-5 — 시드에 코드값을 지어내지 않는다) · 판정 어댑터 2종(1×1 값 열 이름 · 건수 질문의 목록 응답). **위생 교정**: SQL 리터럴을 「pii 컬럼 비교만 가림」에서 **근거 있을 때만 남김(기본 가림)**으로 · 프로필 머리 주석의 승인자 칸은 옮기지 않음 · 오라클 키는 `general`만. 카탈로그 입력 출처 3종(`schema_cache`·`snapshot`·`transcript`). W0에 오라클 접미사 앞당김 · W4 단위 테스트는 가짜 클라이언트 주입. 상태 `TODO` → `WIP` |
| v1.3 | 2026-10-06 | **W0~W6 구현**(작업 트리 · 커밋 없음 · LLM·DB 호출 0) — `scripts/itam_bench/`(catalog·redact·judge·report·_serve·__main__) · `src/observability/run_capture.py` + `subagents.py` 연결점 · 하네스 2곳 · 시나리오 22·정책 77·오라클 정본 16 · 시드 보강 3컬럼 · 신규 테스트 203(벤치 192 · 연결점 8 · 시드 보강 3) · 독립 보안 리뷰 1회(재현 26건 → 회귀 테스트 · 교정). D-301 본문 등재. 잔여: W0 샌드박스 실 대조(Docker·MCP) · W7(피어 세션 공유 MLX 사용 중 — 단독 조건 미성립) · W8. 이탈 8건은 §9 |
| v1.4 | 2026-10-06 | **사용자 인터뷰 답 반영**(리드 경유) — §6 게이트 전건 답(G-1 초안 작성·검토 · G-2·G-5 기본안 · G-3 현 해석 · G-4 연결점 유지 · G-6 기본 가정 · **G-7 = 내부망 「DB 구조」 탭 스키마로 1회차 관찰 → 반출 → 우리가 정답 SQL → 2회차 대조 · 반출 카탈로그 108테이블 전부** · 로컬 「DB 대조만, LLM 보류」 · `pii_filter` 「같이 수정」). W0 실 대조 완료(리드 실측 — 오라클 16/16 ok · README 3종 불변). 카탈로그 = 탭 산출물 전체(관계 3종 · 승인 프로필 구조만 · 유사어 건수만 · 의미 출처 구별 불가 명시 · 108테이블 무필터). W8 1회차 키트(운영 정책·초안 14건·`--env closed` 기본값·실행 시 재린트·카탈로그 인지 가림·`--sync`·부록 A 절차). `pii_filter` 이메일 탐색 선형화(정규식 불변 · 결과 비트 동일 · 벤치 우회 제거). W7 → 대표 3문항 스모크 정의만(ITAM-01 관찰 · 06 카나리아 · 16 정답 대조 — 규모 사용자 확정 · 선택 근거 §4 · 추정 10~20분 · 실행 보류 · 같은 날 4문항 초안을 교정) |
| v1.5 | 2026-10-07 | **부록 A 개정(`plans/140` W6 · D-311 · 상태 불변)** — 2회차부터 0 반입 · 2b 「DB 구조」 탭 자산 자동 생성 1단계 필수 · 2c 코드값·코드 라벨만 승인 · 2a 정의 확인(반입본 시드 108건 · `tcdmsif81` 기본 제외) · 7 반출 6파일(`code_samples.yaml`) · 10 외부망 빌더 `--build-assets` |
| v1.6 | 2026-10-12 | **부록 A 부기(`plans/151` W13 · G-9 (a) · 상태 불변)** — 0 반입 확인 칸에 「반입(전체 덮어쓰기) 뒤 P1 재실행 → 전 자산 승인 · 확인은 리포트 §0 P1 자산 수 0 아님 · 식별 키 테이블 있음 · 경고 없음(151 W11)」 · 2c·순서 요약·143편 런북 A0·A2를 전 자산 승인으로 읽는다는 부기(근거: 내부망 확인 run `20261012-141107`에서 반입이 P1 승인 자산을 지움 — 151 F13) |

---

## 139. ITAM 내부망 1회차 결과 교정 — 테이블 관리 정보 정의 기반 조회 대상 선별 · 입력 한도 초과 · 오류 응답 오표면화

> **작성일**: 2026-10-06 · **v1.2** · **v1.3**(구현 기록 §9 · 2026-10-06)
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — W1~W7 구현 완료(사용자 커밋 `9cc8c6b`·`8f9b1c4`·`bac814c` · W7 문서·시드·시나리오는 작업 트리) · 독립 검증·보안 감사 1라운드 교정 완료 · 잔여: 내부망 2회차(시드 정의 가져오기 → 정의서 대조 → 승인 → `--run --env closed`) · §9** · ~~TODO — 계획 확정(코드 0)~~ · 게이트 G-1~G-7·상한 답 받음(인터뷰 2026-10-06 · §5) · 시드 자산 2파일 git 추적
> - **통지(2026-10-07 · `plans/140` · D-311 · 상태 불변)**: 외부망 빌더가 1회차 반출로 `config/db_profiles/itam.yaml`을 썼다(조회 대상 98 — `tcdmsif81` 기본 제외 · 시드 정의 108 · 검증 오류 0 · 코드값 없음 · 로컬 승인본은 `testdata/itam/db_profile.local_sandbox.yaml`). 사용자 커밋 뒤 반입하면 2회차 잔여의 「시드 정의 가져오기」는 반입본으로 대신하고, 순서는 반입 → 「DB 구조」 탭 P1 실행(필수) → 코드값·코드 라벨만 승인 → 정의서 대조·승인 → `--run --env closed`(`plans/135` 부록 A v1.5)
> **요청(사용자 2026-10-06)**:
> ① *"「20261006-152938」 폴더에 135번 실행 결과가 있다. 수정계획을 수립하라."* — 화면 보고: ITAM 질의가 *"An exception occurred in GptOssAdapter.llm_call: Input tokens must be <= 95232. Given: 96858."* 와 *"존재하지 않는 테이블 참조: orchestrator"* 로 끝남(v1.0).
> ② *"테이블에 따라 해당 테이블에서 관리하는 정보를 먼저 정의하여 관리하고 LLM을 통해 사용자의 프롬프트에 맞는 조회대상 테이블을 선별하고 해당 테이블을 통해 쿼리를 생성하는 방식으로 동작해야 한다. 이 방식으로 계획을 업데이트하라."* (v1.1 — §4)
> ③ *"수집된 결과 파일(20261006-152938)의 정보를 기준으로 itam_schema.json과 테이블별 정보를 정리하여 관리하도록 파일을 생성하거나 정보를 생성하라."* → §4.2 시드 자산
> **입력**: `results/itam_bench/20261006-152938/`(반출 5파일 — `run.json`·`report.md`·`trace.jsonl`·`schema_catalog.yaml`·`leak_check.json` · 누출 관문 통과 · `.gitignore` 대상) — `~/Downloads/20261006-152938`과 동일(`diff -rq` 0).
> **관련 계획**: `plans/135`(ITAM 벤치 — 이 run의 생산자) · `plans/133`(스키마 자산 자동 생성 — A2 「조회 대상 테이블」이 99개를 승인 · 정의 생성은 그 흐름에 붙인다) · `plans/104`(「DB 구조」 탭 — 질의 경로는 읽기만) · `plans/137`(한글 식별자 — 잔여 「폐쇄망 ITAM 실 질의 검증」이 같은 결함으로 막힘) · `plans/119` Q-5(테이블 선택 생략 플래그) · `plans/114` P-4①(강제 보충) · `plans/132`·`plans/122`(통지) · `plans/57`(같은 증상의 선례)
> **관련 결정**: **D-159**(토큰 예산·백엔드 예외 감지 — FIX-B 「단일 경로는 강등만」·대안 기각 「단일 `_llm_select_relevant_tables`를 멀티에 이식」 두 조항을 D-308로 부분 개정) · **D-227**(질의 경로는 구조를 읽기만 — 정의 생성은 관리자 탭) · **D-294**(자산 자동 생성 — 새 자산 키·병합 규칙) · D-051 · D-066(경로 대칭) · D-153 후속2·D-155 · D-301(벤치 계약 ③⑥ · 부기 G-7 「정의서로 싱크」) · D-255(매뉴얼) · D-303(회귀)
> **D-번호**: **D-308**(`docs/decisions/D-308.md` · 확정 · 구현 완료 · 내부망 확인 대기 · 검증 교정 부기) · D-159 부분 개정 · D-294 부기 · D-301 부기(W6)

---

### 0. 증거 규칙

- 과금 호출 0 · 서버 기동 0 · DB 호출 0. 실측은 반출 파일 읽기와 스크래치 스크립트(LLM 0 · DB 0)뿐이다.
- run 커밋 `879b99654fc6`(dirty)은 **로컬 저장소에 없다**(폐쇄망 반입 사본의 커밋). 코드 근거는 로컬 `main`(`71d7ff6`) 기준이며, 폐쇄망 사본과 해당 함수가 같다고 가정한다 — 재실행 전 사용자 반입본이 이 계획의 수정분을 포함하는지 확인한다.
- 응답 원문은 반출되지 않는다(D-301). 화면 문구는 사용자 사진으로만 확인했다.

### 1. 실행 결과 요약 (run `20261006-152938`)

환경 `closed` · 2단 `intent_orchestration` · 워커 `fabrix` / 오케스트레이터 `vllm` · 시나리오 14건 15턴 · 전부 관찰(observe).

| 묶음 | 턴 | 결과 | 비고 |
|---|---:|---|---|
| ITAM 단일 DB 턴 | 12 | **전부 SQL 0건** — `disclosure_kinds: non_sql` · 재시도 1 · 결과 `empty` · 턴당 36~52초 | 12턴 모두 **같은 99테이블·698컬럼 이름**을 받음(`schema_context.presented_tables`) · 표본 행 있음 · 의미 보유 698/698 |
| 되물음 | 2 (ITAM-102·105) | `clarification` · DB 없음 · 23ms | `routing_miss` — 「자산관리」를 말하지 않은 질문 |
| 멀티 DB | 1 (ITAM-114) | SQL 4건 성공(ITAM `tcdmsif90` 7행 + 폴스타 3존) · 132초 | `fabricated` 판정 — §2.5 |

결과적으로 **1회차의 목적(서비스↔서버 연결 위치를 반출 로그로 찾기 — `plans/135` G-7)은 SQL 관측 턴이 ITAM 0건이라 달성되지 않았다.** 재실행이 필요하다.

### 2. 원인 사슬 (실측)

#### 2.1 조회 대상 테이블 99개가 매 질의 프롬프트에 전부 실린다 — **근본 원인**

- 반출 카탈로그 `approved_profile.allowed_tables` = **99개**(108테이블 중 · 제외 9 = `tcdmsam53·55·59·60·61·64`·`tcdmscm12·34·55`). `plans/133` A2(「조회 대상 테이블」)로 승인된 값이다 — 자산 생성 P1은 행 수가 0이 아닌 테이블을 전부 후보로 낸다(`asset_generation_service._assemble`). 폴스타 프로필은 5개다.
- 단일 경로 `schema_analyzer`는 `allowed_tables`가 있으면 ①LLM 선택 중 허용 밖을 제거하고 ②**허용 목록 중 LLM이 고르지 않은 테이블을 전부 보충한다**(`src/nodes/schema_analyzer.py:653-719` · `_supplement_bare = set(_allowed)` — plans/114 P-4①). 99개면 **LLM 선택이 무력화되고 항상 99개**가 된다.
- 이어서 99개 각각에 실데이터 표본 5행을 붙인다(`_collect_live_samples` · `:290` · 총량 20초). 서버 상태에는 컬럼 설명도 698/698 실렸다(Redis 설명 — §2.4).

#### 2.2 단일 경로 토큰 예산 가드가 이 크기를 통과시킨다

`query_generator.py:769-806`(D-159 FIX-B의 단일 경로 몫)은 **시스템 프롬프트만** 추정하고, 예산(90,000) 초과 시 **유사어·설명만** 빼며, 그래도 넘으면 **로그만 남기고 그대로 보낸다**. D-159는 "단일 경로는 relevant 게이트로 이미 좁아 발동이 이례적"이라는 전제로 정했다 — §2.1이 그 전제를 깼다.

반출 카탈로그 구조로 시스템 프롬프트를 조립한 추정치(스크래치 · LLM 0 · DB 0 · 표본 값은 한글 8자 자리표 · 설명은 고정 문장 자리표):

| 테이블 | 표본 | 설명 | 추정 토큰 |
|---|---|---|---:|
| 99 | ✓ | ✓ | 119,507 |
| 99 | ✓ | — | **82,215** ← 가드 1단(설명 제거) 뒤 · 예산 90K **통과** |
| 99 | — | — | 17,937 |
| 10 | ✓ | ✓ | 13,318 |
| 3 | ✓ | ✓ | 3,019 |

→ FabriX 실보고는 **96,858 > 95,232**였다(사용자 프롬프트 미포함 · 추정 계수 과소 — 자리표 기반이라 비율은 참고치). **테이블을 좁히면 3~13K**다.

#### 2.3 FabriX 한도 초과 문구가 SQL로 검증되어 엉뚱한 안내가 나간다

- FabriX는 한도 초과를 HTTP 오류가 아니라 **응답 본문**으로 돌려준다(`prompt_blocks.py:672` 주석 · D-159).
- 멀티 경로는 이 문구를 감지해 「LLM 백엔드 입력 토큰 한도 초과」로 구분하고 재시도를 멈춘다(`multi_db_executor.py:153-166`·`:2820-2832` · D-159 FIX-C). **단일 경로 검증(`src/sql_validation.py` `validate_sql`)에는 이 감지가 없다** — 경로 비대칭(D-066 위반). `query_generator.py:771` 주석은 단일 경로도 "백엔드 예외 감지가 사후 방어한다"고 적었지만 실재하지 않는다.
- 그래서 오류 문구가 SQL로 검증됐다: `…Error occurred from orchestrator…`의 `from orchestrator`를 FROM 절로 읽어 「존재하지 않는 테이블 참조: orchestrator」, 산문 판정(`is_non_sql_prose`)으로 「조회 엔진이 남긴 설명」 안내, 산문 재시도 1회로 **같은 크기 프롬프트를 한 번 더 호출**.

#### 2.4 벤치 카탈로그의 의미 보유 0 ↔ 실행 상태 698 — 측정 불일치

- 카탈로그 `summary.columns_with_meaning: 0` — 카탈로그는 파일 캐시 `_descriptions`만 읽는다(`scripts/itam_bench/catalog.py:704-711`). 서버는 설명을 **Redis 우선**으로 읽는다(`cache_manager.get_descriptions` `:589-605`).
- 파일에 설명이 없는 이유 후보(**추정 — W6에서 재현 테스트로 확정**): `PersistentSchemaCache.save()`(`persistent_cache.py:149-205`)는 파일 전체를 `schema`만으로 다시 써서 `_descriptions`·`_synonyms`가 사라진다.

#### 2.5 그 밖의 관찰 (이 계획은 통지만)

| 턴·대상 | 관찰 | 처분 |
|---|---|---|
| ITAM-102·105 | 「자산관리」 없이 「담당 부서」·「유지보수 계약 만료일」 → 소스 되물음 | `plans/132` 통지 · W6-e |
| ITAM-114 | 「**자산관리에서** 지난주 CPU 사용률 추이」 → ITAM + 폴스타 3존 조회 | `plans/132` 통지 |
| ITAM-114 | ITAM `tcdmsif90`(가상화 호스트 수집 이력)에 `기준년월일`·`CPU사용률`이 실재 — 「자산 DB엔 추이가 없다」는 시나리오 전제가 성립하지 않음 | **G-6** |
| ITAM-114 | 폴스타 쪽이 「지난주」를 월 통계로 조회 | `plans/122` 통지 |
| 리포트 | §9 「로컬 MLX 값이라」 문구가 FabriX run에도 고정 · closed 정책에 「누출 관문 시험 불성립」 경고 | W6-b·c |
| 조회 대상 | `tcdmsif81`(가상화 관리 서버)에 `계정명`·`계정비밀번호EC`(암호화 비밀번호) — 조회 대상 99개에 포함 | 내부망 관리자에게 조회 대상 제외 권고(W7) |

#### 2.6 테이블 단위 의미가 없어 LLM이 고를 근거가 컬럼 이름뿐이다

- 반출 카탈로그: 테이블 의미 0/108 · 파일 컬럼 의미 0/1,988 · 관계 0 · 같은 기본키 군 0 · 기본키 표시 0(108테이블 모두).
- 테이블 이름은 코드형(`tcdms` + 군 접두 `if` 51 · `am` 25 · `cm` 25 · `gt` 3 · `br` 2 · `hr` 2 + 번호)이다. 반면 컬럼 이름은 한글 의미어다(평균 18.4개 · 최대 68).
- 현행 선택 프롬프트(`_llm_select_relevant_tables` · `schema_analyzer.py:951-1039`)는 「테이블명: [컬럼 15개]」만 싣는다. 서버·네트워크·가상화는 **현행 / 수집 적재본 / 날짜별 수집 이력**이 거의 같은 컬럼의 3종 쌍둥이다(예: `tcdmsif72`·`74`·`73`). 그래서 컬럼 15개로는 「지금 상태」와 「추이」를 가릴 근거가 없다.
- 선택이 실패하면 **전체 테이블을 반환**한다(`:1029-1039`). 결정적 후처리로 줄이는 장치가 없다.

### 3. 목표 · 비목표

**목표**
1. **테이블마다 「관리하는 정보」를 정의해 자산으로 관리한다** — 관리자 「DB 구조」 탭에서 초안(가져오기·LLM) → 검토·수정 → 승인 · 버전·되돌리기(D-227 ⑤ · D-294 ④와 같은 규칙).
2. **질의 시 LLM이 그 정의를 보고 질문에 맞는 조회 대상 테이블을 고른다** — `allowed_tables`는 후보 범위(필터)로만 쓰고 강제 보충하지 않는다.
3. **고른 테이블(과 그 정의)만으로 SQL을 생성한다** — 단일·멀티 경로 대칭(D-066).
4. 그래도 한도를 넘으면 **보내기 전에** 줄이거나 멈춘다(W2). 백엔드 오류 응답은 원인 그대로 알리고 재시도하지 않는다(W1).
5. 벤치가 선별 결과·프롬프트 크기·설명 출처를 서버가 실제 쓴 것과 같게 기록한다(W6).
6. **정의가 없는 DB(폴스타 4종)는 비트 동일**(G-1).

**비목표**: 질의 경로에서 정의를 만들거나 고치기(D-227 ①⑧ — 질의 경로는 읽기만) · 컬럼 설명 생성(`plans/133` A3) · 토큰 추정 계수 변경(W6-d 기록 뒤) · 소스 선별(`plans/132`) · FabriX 한도 상향.

### 4. 설계

#### 4.1 흐름 — 사용자 지시 3단계와 작업 단위

```
[관리자 「DB 구조」 탭 — W3]                               [질의 경로 — 읽기만]
스키마 스냅샷 + P1 근거 ──→ 정의 초안 ──→ 검토·수정 ──→ 승인 ──→ 프로필 table_definitions
 (가져오기 · LLM 묶음 초안)     (결정적 검증)   (테이블 단위)      (버전·되돌리기)        │
                                                                                         ▼
질문 ─→ 후보 = 스키마 ∩ allowed_tables ─→ ①정의 목록으로 LLM 선별(W4) ─→ 검증·조인 보완·상한
     ─→ ②고른 테이블의 스키마·표본·설명 + 정의 블록만으로 SQL 생성(W5) ─→ 검증·실행
                      (안전망: 예산 사다리 W2 · 백엔드 오류 감지 W1)
```

| 사용자 지시 | 작업 |
|---|---|
| 테이블별 관리 정보를 먼저 정의하여 관리 | **W3**(자산·생성·승인) · 시드 §4.2 |
| LLM으로 프롬프트에 맞는 조회 대상 테이블 선별 | **W4** |
| 선별한 테이블로 쿼리 생성 | **W5** |

#### 4.2 시드 자산 (요청 ③ · 2026-10-06 작성 · git 추적 — G-7 · 미커밋)

| 파일 | 내용 | 만든 방법 · 검증 |
|---|---|---|
| `testdata/itam_bench/closed/itam_schema.json` | 내부망 ITAM 스키마 108테이블·1,988컬럼 — **앱 스키마 캐시 형식**(`_cache_version` 1 · 지문 · `schema.tables` · `_db_description`) | 반출 카탈로그를 앱 저장 함수 `PersistentSchemaCache.save()`로 기록 → 같은 클래스 `load()`로 다시 읽어 108·1,988 확인. 카탈로그에 없는 정보는 비어 있다: 타입 길이(`char`·`varchar`·`decimal`만) · 기본키 · 관계 · 행 수 · 표본 · 컬럼 설명 |
| `testdata/itam_bench/closed/table_definitions.yaml` | 테이블별 관리 정보 **초안**(status `draft`) — 24개 업무 영역 · 108테이블마다 `group`·`kind`·`manages`·`key_columns`·`related`(157건)·`notes` · 조회 대상 밖 9개 `allowed: false` | 컬럼 이름만 보고 작성(테이블 주석·값 미확인 · 이름만으로 판단한 것은 「(추정)」). 스크래치 대조 오류 0: 108테이블 일치 · 대표 컬럼 실존 · 연결 후보 테이블·컬럼 실존 · `allowed` 표시가 프로필과 일치 |

- `kind`는 §2.6의 쌍둥이 문제를 직접 다룬다: `현행`(`활성화여부` 있음) · `수집적재`(같은 구성 · `활성화여부` 없음 — 적재본 추정) · `수집이력`(`기준년월일` + 수집이력ID).
- `related`는 **이름이 같은 컬럼으로 추정한 연결 후보**다. 값 겹침은 확인하지 않았다(D-294 ② 채택 기준 ≥ 0.9 미측정) — 승인 관계 자산이 아니다.
- `notes`에 SQL 생성에 필요한 표기 차이를 적었다. 예: 메모리 사용률 컬럼은 `tcdmsif72`·`73`에서 `서버메모리사용율`(율), `tcdmsif80`에서 `서버메모리사용률`(률)이다. 비밀값·개인정보 컬럼도 표시했다.
- **서비스↔서버 연결 단서(`plans/135` G-7 1회차 목적)**: 서버 테이블에는 어플리케이션코드 컬럼이 없다. 연결 후보는 서버의 `그룹경로내용`·`용도내용`·`구성항목설명내용`과 스토리지 할당의 `업무명`이다 → 2회차 관찰 질문·정답 SQL의 출발점.
- 실측: 이 정의로 만든 선별 후보 목록(조회 대상 99개 · 「이름: 관리 정보 [대표 컬럼]」)은 추정 **6,257토큰**이다. 현행 「컬럼 15개」 목록은 6,949토큰(99개)·7,608토큰(108개)이다. 정의를 써도 선별 호출이 커지지 않는다.

#### 4.3 정의 자산 형식 (W3 · 런타임)

프로필 키 `table_definitions`(설계 기본값 — §4.5). 테이블당:

| 필드 | 필수 | 프롬프트 사용 | 결정적 검증 |
|---|---|---|---|
| `manages` | ✓ | 선별(W4) · 생성 정의 블록(W5) | 1~300자 · 중괄호·코드 펜스·백틱 SQL 금지 · 빈 값 거절 |
| `kind` | — | 선별 | 위 목록 값만 |
| `key_columns` | — | 선별 | 그 테이블에 실존 · ≤ 10 |
| `related` | — | 선별(조인 상대 힌트) | 상대 테이블 실존 |
| `notes` | — | 생성 정의 블록(선별된 테이블만) | ≤ 300자 · 금지 문자 동일 |
| `origin` | ✓ | — | `import`·`llm`·`comment`·`manual` |

`group`은 관리 화면 묶음용이다(프롬프트 미사용). **값(표본·코드값)은 정의에 넣지 않는다** — LLM 초안 입력에도 표본을 넣지 않는다(코드값 라벨은 D-294 자산 범위 안에서만).

#### 4.4 작업 단위

| W | 내용 | 파일 | 결정 |
|---|---|---|---|
| **W1** | **백엔드 오류 응답 감지 — 단일 경로 대칭**(v1.0 그대로). 멀티의 마커 3종·구분 접두·판정을 공용 함수 하나로 옮기고(후보 `src/sql_validation.py`) 멀티는 그 함수를 쓴다(비트 동일). `validate_sql`은 SELECT 검사 **전에** 판정해 사유 하나만 낸다. `query_validator`는 `backend_error` 표지 · `is_non_sql_prose` 제외 · 그래프 경로·2단 단일 루프(`subagents.py:934-963`)는 **재생성 0회** · 종결 사유 `regen_stop.reason = "backend_limit"` → `replanner._REGEN_STOP_REASON_TEXT` 문구 · `[토큰예산]` 로그에 FabriX `Given`·한도와 자기 추정치. **W4의 선별 호출에도 같은 판정을 쓴다.** | `src/sql_validation.py` · `src/nodes/{multi_db_executor,query_validator,query_generator}.py` · `src/graph.py` · `src/orchestration/{subagents,replanner}.py` | D-159 FIX-C 단일 확장 |
| **W2** | **단일 경로 예산 사다리 — 멀티와 같게**(G-4 · v1.0 W3). 시스템 + 사용자 프롬프트 추정 · 초과 시 ①유사어·설명 ②**표본** 제거 ③**호출 없이** `PromptBudgetExceeded` → W1과 같은 종결. 예산 안이면 바이트 무변경. 상태 `prompt_budget`(수치·단계·테이블 수·표본 유무). **정의가 승인되기 전에도 ITAM 재실행이 돌게 하는 안전망**(99테이블·표본 없음 추정 17,937). | `src/nodes/query_generator.py` · (필요 시) `src/nodes/prompt_blocks.py` | D-308(G-4 · D-159 개정) |
| **W3** | **테이블 정의 자산 — 정의·생성·검토·승인**. ①프로필 키 `table_definitions`(§4.3) · `profile_merge`에 테이블 단위 병합 규칙(사람 값 `manual` 보존 · `import`/`llm`/`comment`만 교체 — D-227 S5와 같은 원칙). ②「DB 구조」 탭 자산 화면에 **「테이블 정의」 표**(영역·테이블·관리 정보·대표 컬럼·성격·출처 · 편집 가능 · 테이블 단위 선택 승인). ③초안 경로 3가지: **가져오기**(§4.2 형식 YAML — 결정적 검증 뒤 초안) · **주석**(P1 카탈로그 테이블 주석 → `comment`) · **LLM 묶음 초안**(주석 없는 테이블만 · 입력 = 테이블명·컬럼명/타입·컬럼 설명·코드 라벨·승인 관계·DB 설명 · 군 접두 단위 10개씩 · 동시성 `admin_llm_concurrency`(=2) · 예상 호출 수 사전 표시 · 실패 묶음만 재실행 · ITAM 99개 ≈ 10~12회). ④검증 실패 행은 승인 불가(409). ⑤준비도 경고 1항목: 조회 대상 > 10인데 정의가 없거나 정의 범위가 조회 대상의 80% 미만이면 경고만(G-7 (a) 방식). ⑥매뉴얼(D-255): 관리자 「스키마 자산 자동 생성」 절에 「테이블 정의」 · 캡처 갱신. | `src/schema_cache/asset_generation_service.py` · `src/domain/profile_merge.py` · `src/domain/table_definitions.py`(신규 · 순수 검증·렌더) · `src/prompts/asset_generation.py` · `src/domain/db_readiness.py` · `src/api/routes/db_structure.py` · `src/static/js/admin-db-structure.js` · `scripts/manual/{features.yaml,content/admin.md,captures.yaml}` | D-308(D-294 부기 — 새 자산 키) |
| **W4** | **정의 기반 LLM 테이블 선별 — 단일·멀티 공용**. 발동: 그 DB 프로필에 `table_definitions`가 있음(G-1). ①후보 = 스키마 ∩ `allowed_tables`(있으면) — **강제 보충 없음**. 정의가 없는 후보는 「이름 [컬럼 앞 10개]」 줄로 함께 싣는다. ②프롬프트(`src/prompts/table_selection.py` 신규): 질문(멀티는 `sub_query_context` 덧붙임) + DB 설명 + 후보 목록(이름순 — KV 캐시 · TP-11.10) + `kind` 안내(「추이·과거 시점 = 수집이력 · 지금 상태 = 현행 · 수집적재는 고르지 않음」) + 규칙(필요한 최소 테이블 · 조인 상대 포함 · 최대 K개) → JSON `{"tables": [...]}`(쉼표 목록도 받음). ③결정적 후처리: 실존 ∩ 후보(맨 이름 비교) · `related`·승인 관계로 **선별 테이블 둘을 잇는 중간 테이블만** 보완 · 상한 K(설정 `cfg.text2sql.schema_table_select_max` · `TEXT2SQL_SCHEMA_TABLE_SELECT_MAX`, 기본 8 — 넘으면 LLM 순서대로 자르고 로그). ④실패(예외·W1 백엔드 오류·유효 0개) → **G-2**(기본: 정의·컬럼명 어휘 매칭 상위 K로 대체 + `[테이블선별] 폴백` 로그 → 0개면 SQL 생성 없이 안내 종결 · 전체 반환 금지). ⑤Q-5 생략(`_table_select_skip_enabled`)·EAV 보충·강제 보충은 정의 모드에서 건너뛴다. alarm_query 분기는 현행. ⑥상태 `table_selection` = {mode, source(`llm`/`lexical`/`none`), candidates, selected, bridged} — 값 없음. ⑦멀티: `_gate_schema_tables`(`multi_db_executor.py:1373`) 자리에서 같은 함수(G-3). | `src/nodes/table_selection.py`(신규 · application) · `src/nodes/schema_analyzer.py` · `src/nodes/multi_db_executor.py` · `src/prompts/table_selection.py` · `src/domain/table_definitions.py` · `src/config.py`(K) · `src/state.py` | D-308(G-1~G-3 · D-159 대안 기각 「단일 LLM 선택의 멀티 이식」 개정 · plans/114 P-4①은 정의 모드에서만 미적용) |
| **W5** | **선별 테이블로만 SQL 생성**. ①스키마·표본·설명·유사어 재료를 선별 테이블로만 만든다(현행 구조 — `relevant`가 좁아지면 따라 좁아짐 · 표본 수집도 선별 테이블만). ②**정의 블록**: 선별 테이블의 `manages`·`notes`를 「테이블 용도」 블록으로 스키마 앞에 싣는다 — 공용 빌더(`prompt_blocks`) 하나를 단일 `_build_system_prompt`·멀티 프롬프트 같은 자리에서 호출(D-066). 정의 없는 DB는 빈 블록 → 바이트 불변. ③선별 밖 테이블을 참조한 SQL은 기존 검증(「존재하지 않는 테이블 참조」)이 재생성으로 돌린다 — 재선별은 하지 않는다(위험 §7). | `src/nodes/prompt_blocks.py` · `src/nodes/query_generator.py` · `src/nodes/multi_db_executor.py` | D-308 |
| **W6** | **벤치 보강**(`plans/135` 소관 · D-301 부기). a) 설명·유사어 출처를 서버와 같은 순서(Redis → 파일)로 · 파일 설명 유실 재현 테스트 → 재현되면 `save()`가 부가 필드 보존(G-5) b) §9 지연 문구 평면별 c) closed 정책 카나리아 「해당 없음」 d) 측정 연결점이 `prompt_budget`·`table_selection`도 복사(호출 1곳 유지) → `trace.jsonl` `schema_context.prompt_tokens_est`·`budget_stage`·`backend_reported_tokens`·`selection_source`·`selected_count` e) 되물음 종류(칩 여부·후보 소스 id) f) 실패 분류 `backend_limit`·`selection_none` g) 카탈로그 `tables.*.meaning` ← 승인 `table_definitions.manages`(출처 표기) | `scripts/itam_bench/{catalog,report,judge,_serve,__main__}.py` · `src/schema_cache/persistent_cache.py`(G-5 조건부) | D-301 부기 |
| **W7** | **통지·시나리오·반입 안내**. `plans/132`(되물음·명시 소스 4곳) · `plans/122`(「지난주」→ 월 통계) · `plans/137` 잔여는 재실행으로 확인 · ITAM-114 기대값 유지(G-6 — 사용률 정본은 관측 DB · ITAM 수집 이력 답은 오답 → 기대 응답 「자산관리에는 사용률 추이가 없다 — 관측에서 조회할지」 · 라우팅 교정은 `plans/132`에 통지) · 시드 정의의 사용률 컬럼 보유 13테이블(`tcdmsif72`·`73`·`74`·`80`·`85`~`93`) `notes`에 「사용률 정본 아님 — 관측 DB」 표기 · 2회차 관찰 질문에 서비스↔서버 연결 단서(§4.2) 반영 · 내부망 관리자 절차 안내: 시드 정의 가져오기 → 내부망 정의서와 대조·수정 → 승인 · `tcdmsif81` 조회 대상 제외 권고 | 계획서 3개 부기 · `testdata/itam_bench/scenarios.closed.yaml` · `plans/135` §9 | — |

**순서**: W1 → W2 → W3 → W4 → W5 → W6 → W7. W1·W2만 반입해도 오표면화·헛재시도가 사라지고 ITAM 단일 턴이 표본 없는 99테이블(≈18K)로 돈다 — 정의 승인 전 2회차를 먼저 돌릴 수 있다. W3~W5가 사용자 지시의 본 방식이다.

#### 4.5 설계 기본값 (상한은 사용자 확정 · 나머지는 기본값 — 이의가 있으면 알려 주세요)

| 항목 | 기본값 | 다른 안 |
|---|---|---|
| 정의 저장 위치 | 프로필 키 `table_definitions` — 두 경로가 이미 읽는 `_load_manual_profile`·`apply_profile` 버전·되돌리기를 그대로 쓴다 | 지식 파일 `config/knowledge/{db_id}/table_definitions.yaml`(`AssetFileStore`) — 프로필이 길어지지 않지만 로더·배선 추가 |
| 선별 상한 K | **8 (설정 키 · 사용자 확정 2026-10-06)** | 5 · 상한 없음(사다리만) |
| 승인 단위 | 테이블 단위 선택(D-227 G-8 (a)와 같음) | 전체 일괄 |
| LLM 초안 실행 | 관리자 명시 실행만(D-227 ②) | — |

### 5. 게이트 — 사용자 결정 (인터뷰 2026-10-06 · 전부 답 받음 → D-308)

| G | 질문 | 선택지 (권고 ★) · **답** |
|---|---|---|
| **G-1** | 정의 기반 선별을 **어느 DB에** 적용할까? | ★(a) **정의가 승인된 DB만**(지금은 ITAM) — 폴스타 4종은 정의가 없으니 현행(강제 보충 포함) 비트 동일 · 폴스타도 나중에 정의를 승인하면 같은 방식으로 바뀜 / (b) 폴스타도 이번에 정의를 써서 함께 전환 — 폴스타 응답이 바뀌므로 시나리오 재측정 필요 / (c) 조회 대상이 10개 넘는 DB에만 → **답: (a) 정의 승인 DB만** |
| **G-2** | LLM 선별이 실패하면(오류·엉뚱한 이름만 반환) 어떻게 할까? | ★(a) **질문 단어를 정의·컬럼 이름과 맞춰 상위 K개로 대체**(로그·표지 남김) → 그것도 0개면 SQL을 만들지 않고 「질문에 맞는 테이블을 찾지 못했습니다 — 무엇(자산·계약·장애 등)을 찾는지 알려 주세요」 안내 / (b) 대체 없이 바로 안내 / (c) 후보 전체로 진행 + 예산 사다리(지금과 비슷 — 비권고) → **답: (a) 어휘 매칭 대체 → 0개면 안내** |
| **G-3** | 여러 DB를 함께 조회할 때(멀티 경로)도 ITAM은 같은 방식으로 고를까? | ★(a) **함께** — 같은 함수 · D-159 대안 기각 「단일 LLM 선택의 멀티 이식」(근거: 프로필 DB는 조회 대상 목록이 지배적이라 결정적 필터로 같은 결과 — ITAM 99개에서 성립 안 함) 개정 · 멀티 질의당 ITAM 선별 호출 1회 추가 / (b) 단일만 — 멀티는 지금처럼 99개 필터 + 사다리 → **답: (a) 함께(단일·멀티 같은 함수)** |
| **G-4** | 단일 경로도 한도를 넘으면 보내지 않고 멈출까?(D-159 개정) | ★(a) **멀티와 같은 사다리**(설명 → 표본 제거 → 호출 없이 명시 실패) + 사용자 프롬프트 포함 추정 · 예산 90K 유지 / (b) (a) + FabriX가 한도 초과로 답하면 표본을 빼고 1회만 재호출 / (c) 현행 유지 → **답: (a) 멀티와 같은 사다리** |
| **G-5** | 벤치 카탈로그의 설명 출처를 서버와 맞출까? | ★(a) **Redis → 파일 순으로 읽고(조회문 0), 파일 설명 유실이 재현되면 제품 `save()`도 부가 필드 보존** / (b) 카탈로그만 / (c) 리포트 경고만 → **답: (a) 벤치 + 앱 `save()`(재현 시)** |
| **G-6** | 「자산관리에서 지난주 CPU 사용률 추이」에 ITAM 가상화 호스트 수집 이력(`기준년월일`·`CPU사용률`)으로 답하면 정답인가? | (a) **자산관리를 명시했으면 ITAM 답도 정상** → 시나리오의 「데이터 없음」 기대 제거 / (b) 사용률 정본은 관측 DB → ITAM 답은 오답 · 별도 라우팅 교정 — **사용자 업무 판단** → **답: (b) 오답 — 사용률 정본은 관측 DB** → ITAM-114 기대값 유지 · 라우팅 교정은 `plans/132` |
| **G-7** | 반출 유래 시드 2파일(§4.2 — 내부망 108테이블 이름·컬럼 이름 · 값 없음)을 **git으로 관리**할까? 저장소 원격은 GitHub이고, 지금 추적 중인 closed 파일 2개(`scenarios.closed.yaml`·`column_policy.closed.yaml`)에는 운영 테이블 이름이 없다 | (a) 추적 — 반입·대조 이력이 남고 W3 가져오기 테스트가 실제 모양을 쓴다 / (b) **로컬만**(`.gitignore`에 `testdata/itam_bench/closed/`) — 운영 구조가 원격에 올라가지 않음 · 반입은 파일 복사 / (c) 정의 파일만 추적 · 스키마 JSON은 로컬만 — **보안 판단은 사용자 몫이라 권고 없음** → **답: (a) 둘 다 추적** — 값·반출 원본(`results/`)은 넣지 않음 · W3·W4 테스트에 실제 모양 사용 가능 |

### 6. 검증

| 단계 | 방법 | 기준 |
|---|---|---|
| W1 단위 | 가짜 LLM이 FabriX 오류 문구를 돌려줌(단일 그래프 · 2단 단일 루프 · 멀티 · W4 선별 호출) | 사유 = 「LLM 백엔드 입력 토큰 한도 초과…」 · 재생성 0 · 「orchestrator」·「조회 엔진이 남긴 설명」 없음 · `tests/test_nodes/test_multi_gate_token_budget.py` 그대로 통과 |
| W2 단위 | 예산 초과 합성 스키마 | 사다리 순서 · 3단 LLM 호출 0 · 예산 안 바이트 동일(`prompt_render_diff.py`) |
| W3 단위 | 가짜 LLM · 가짜 자산 DB | 가져오기·주석·LLM 3경로 초안 · 검증 실패 409 · `manual` 보존 병합 · 되돌리기 바이트 동일 · 폴스타 프로필 5종 병합 불변 · 준비도 경고 · `tests/test_manual` |
| W4 단위 | §4.2 시드 2파일(G-7 — 실제 모양) 또는 합성 99테이블 + 정의 + 가짜 선별 | 정의 모드: 선별 3 → relevant 3(강제 보충 없음) · 범위 밖 제거 · 다리 테이블 보완 · K 자르기 · 실패 → 어휘 대체 → 0이면 안내 · Q-5 미적용 · 정의 없는 DB = 기존 테스트 비트 동일 · 단일·멀티 같은 결과 |
| W5 단위 | 같은 합성 | 정의 블록이 단일·멀티 같은 자리 · 정의 없는 DB 바이트 불변 · 표본 수집 대상 = 선별 테이블 |
| W6 단위 | 기존 벤치 테스트 + 새 필드 | 누출 관문 통과 · 새 필드 값 없음 · `save()` 뒤 `_descriptions` 유지(G-5 (a)) |
| 반출 구조 재현 | 스크래치(LLM 0 · DB 0) — §4.2 시드 사용 | 선별 후보 목록 ≈ 6.3K · 선별 3~8개 경로 시스템 프롬프트 ≤ 13K |
| 회귀 | `python scripts/regress.py --base <세션 시작 SHA> --files <자기 파일>` (D-303) | 모듈 단위 · 정적 게이트 통과 · 보고에 범위 명시 |
| MLX | **선별 4문항 + 정의 초안 1묶음 · 1회 · 예상 약 5분** — 바뀐 프롬프트 2종(선별·정의 초안)만. 샌드박스 2테이블이라 선별 정확도가 아니라 **출력 형식(JSON 파싱)·지시 준수만** 본다 | 두 평면 `mlx` 확인 뒤(`--show-env`) · 승인 불요(D-240) · 정확도·지연 결론은 내부망 |
| 폐쇄망(사용자) | 반입 → ①W1·W2만으로 2회차 가능 ②관리자: 시드 정의 가져오기 → 정의서 대조·수정 → 승인 → `python -m scripts.itam_bench --run --env closed` | ITAM 단일 턴 `non_sql`·`backend_limit` 0 · SQL 관측 턴 ≥ 10 · `selection_source=llm` 비율 · `presented_tables`와 2회차 `gold_tables` 대조(선별 재현율) · 턴당 지연 감소 |

### 7. 위험

- **선별 누락**: LLM이 필요한 테이블을 빼면 SQL 검증이 재생성만 반복한다(재선별 없음 · W5 ③). 2회차 `gold_tables` 대조로 재현율을 재고, 낮으면 「검증이 선별 밖 테이블을 지목하면 1회 재선별」을 후속으로 검토한다.
- **초안 정의의 오류**: §4.2 정의는 컬럼 이름만 보고 썼다(「(추정)」 다수 · `수집적재` 성격은 가설). 내부망 정의서 대조 전에는 선별을 잘못 이끌 수 있다 → W7 절차에서 대조 후 승인한다.
- **연결 후보 오탐**: `related`는 이름 일치 추정이다. 값 겹침(D-294 ②) 확인 전에는 다리 테이블 보완(W4 ③)에 승인 관계를 우선하고, `related`는 선별 힌트로만 쓴다.
- 추정 계수(ASCII 4자 · 비ASCII 1.5자/토큰)는 이번 run에서 과소로 보이나 근거가 1건이다 — W6-d 기록 뒤에 판단한다(D-159 「주의」).
- 작업 트리에 병행 세션 변경이 있다(`plans/122`·`136`·INDEX·`src/config.py`·`src/state.py` 등) — 구현 시 `git status` 재확인 · 자기 파일만 회귀 기준(`--files`).

### 8. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-06 | 작성 — run `20261006-152938` 반출물 분석 · 원인 사슬 §2 · W1~W5 · G-1~G-5 · D-308 예약 |
| v1.1 | 2026-10-06 | 사용자 지시 ②③ 반영 — **테이블 관리 정보 정의 → LLM 선별 → 선별 테이블로 생성** 구조로 개편: §2.6(테이블 의미 부재) · §4.1 흐름 · §4.2 시드 2파일(`itam_schema.json` · `table_definitions.yaml` — 대조 오류 0 · 선별 목록 6,257토큰 실측) · §4.3 자산 형식 · W3 정의 자산 · W4 정의 기반 선별(단일·멀티 공용) · W5 선별 테이블 생성 · W2(예산 사다리)는 안전망으로 앞당김. **v1.0 G-1(강제 보충 상한 10)은 폐기** — 정의 모드에서는 강제 보충 자체가 없다. 게이트 재편 G-1~G-7(G-7 시드 파일 git 추적 신설) · `tcdmsif81` 비밀번호 컬럼 관찰 추가 |
| v1.2 | 2026-10-06 | 인터뷰 답 반영 — G-1 (a) · G-2 (a) · G-3 (a) · G-4 (a) · G-5 (a) · **G-6 (b) 사용률 정본은 관측 DB**(ITAM-114 기대값 유지 · W7 통지·시드 `notes`) · **G-7 (a) 시드 2파일 git 추적** · 상한 K=8 확정 · D-308 등재(D-159 부분 개정 · D-294 부기) · 「G-7」 표기 중 `plans/135` 소관 2곳을 구분 |
| v1.3 | 2026-10-06 | **구현**(W1~W7 · §9) — 사용자 지시 *"138번 계획을 구현하라."*(병합 재부여 전 번호) · Wave별 구현 에이전트 6 · 독립 검증·보안 감사 1라운드(발견 F-1~F-8 → F-5 제외 교정) · MLX 선별 4문항 + 정의 초안 1묶음 1회 · W7: 132·122·135 통지 · 시드 `notes` 13테이블 · 2회차 관찰 4건(ITAM-115~118) · 파일명 `-TODO` → `-WIP`(잔여 = 내부망 2회차) |
| v1.4 | 2026-10-07 | **W2 안전망 교정** — 사용자 보고 *"itam 조회시 아직도 토큰 한도 초과 오류가 발생"* · 원인 = 추정기 과소(ITAM 프롬프트 o200k 실측/추정 1.28~1.47)로 사다리가 1단에서 멈춤 → `estimate_prompt_tokens` 조각 단위 교체 (D-159·D-308 부기 · 같은 프롬프트가 2단 약 22.7K로 전송) · `tcdmsif81` 시드 notes 정리 · `docs/18` 항목 |

### 9. 구현 기록 (v1.3 · 2026-10-06 · 세션 시작 `71d7ff6` · 사용자 커밋 `9cc8c6b`(W1~W6·검증) · `42deda2`(병합 — 138 → 139 · D-305 → D-308 재부여) · `8f9b1c4`·`bac814c`(검증 교정) · W7은 작업 트리)

#### 9.1 Wave별 결과

| W | 결과 | 주요 파일 · 테스트 |
|---|---|---|
| W1 | **완료** — 공용 판정 `detect_llm_backend_error`·`LLMBackendError`(멀티 문구 비트 동일) · `validate_sql`이 SELECT 검사 전에 사유 하나만 낸다 · `query_validator` `backend_error` 표지·`backend_limit_hit`·`[토큰예산]` 로그(보고 토큰·한도·자체 추정) · 그래프·2단 단일 루프·멀티 모두 재생성 0 · 종결 사유 `backend_limit` | `src/sql_validation.py` · `src/nodes/{query_validator,multi_db_executor}.py` · `src/graph.py` · `src/orchestration/{subagents,replanner}.py` · `test_plan139_backend_error.py` 22 |
| W2 | **완료** — `_fit_single_prompt_budget`(시스템+사용자 추정 · ①유사어·설명 ②표본 ③`PromptBudgetExceeded` → LLM 0) · state `prompt_budget`(두 생성자에서 초기화) · 예산 안 바이트 동일 | `src/nodes/{query_generator,prompt_blocks}.py` · `src/state.py` · `test_plan139_single_budget_ladder.py` 13 |
| W3 | **완료** — 도메인 `table_definitions`(종류 12 · 한도 · 가져오기 파싱) · 테이블 단위 병합(`manual` 보존) · 준비도 C11 · 초안 3경로(가져오기·주석·LLM 묶음 — 추정 먼저 · 실패 묶음 재실행) · 편집 → `manual` · 승인 409 · API 4경로(관리자 전용) · 화면 · **매뉴얼 A-67**(캡처 `dbs-table-definitions`) | `src/domain/{table_definitions,profile_merge,db_readiness}.py` · `src/schema_cache/{asset_generation_service,db_structure_service}.py` · `src/prompts/asset_generation.py` · `src/api/routes/db_structure.py` · `src/static/js/admin-db-structure.js` · 테스트 46 + 16 + 37 |
| W4 | **완료** — `select_tables`(단일 `schema_analyzer`·멀티 `_analyze_schema` 공용 · 후보 = 스키마 ∩ `allowed_tables` · 강제 보충·Q-5·EAV 보충 없음 · 중간 테이블은 승인 관계 → `related` 순 · 상한 8 · 실패 → 어휘 대체(가중 manages 3·대표 컬럼 2·notes 1·컬럼 1 · 수집적재 제외) → 0이면 `selection_none` 안내 종결) · state `table_selection` · 설정 `TEXT2SQL_SCHEMA_TABLE_SELECT_MAX` | `src/nodes/table_selection.py` · `src/prompts/table_selection.py` · `src/config.py` · `.env.example` · `config/settings_help/text2sql.yaml` · `test_plan139_table_selection.py` 44 |
| W5 | **완료** — 「테이블 용도」 블록(`build_table_purpose_block` · 선별 테이블의 `manages`·`notes`) · 단일·멀티 `schema=` 맨 앞 같은 자리 · 정의 없는 DB 빈 블록 | `src/nodes/prompt_blocks.py` · `test_plan139_definition_block.py` 33 |
| W6 | **완료** — §6 W6 a~f 전부 · G-5 유실 **재현됨** → `save()`가 `schema` 밖 키 보존 · D-301 부기 | `src/schema_cache/persistent_cache.py` · `scripts/itam_bench/{catalog,__main__,report,_serve,judge}.py` · `test_plan139_w6_persistent_extras.py` · `test_itam_bench_plan139_w6.py` 38 |
| 검증·감사 | **1라운드** — 수용 기준 §6 충족(폐쇄망 제외) · 경로 대칭 실제 노드 3경로 · 요청 스코프 2턴 체크포인터 · 발견 8건 중 7건 교정(F-1 한글 민감 컬럼 표본 마스킹 · F-2 YAML 앵커/별칭·깊은 중첩 422 · F-3 NFKC·서식 문자·`~~~`·영역 30자 · F-4 질의 경로 정의 재정제 · F-6 블록 머리말 · F-7 어휘 대체에서 수집적재 제외 · F-8 LLM 대표 컬럼 10개 절단) · F-5는 범위 밖(§9.4) | `src/security/data_masker.py` 외 · `test_plan139_verify_paths.py` 9 · `test_plan139_findings_fix.py` 35 · `test_plan139_findings_fix_assets.py` 31 · 매뉴얼 `admin.md` 검증 문단 · `config/settings_help/security.yaml` |
| W7 | **완료** — 132·122·135 통지 · ITAM-114 기대값 유지 · 시드 `notes` 13테이블 「사용률 정본 아님 — 관측 DB」 · 2회차 관찰 4건(ITAM-115~118 — 용도 기재 · 분류 경로 · 스토리지 업무 이름 · 스토리지 → 서버) · 내부망 관리자 절차(135 부록 A 2a) | `testdata/itam_bench/closed/table_definitions.yaml` · `testdata/itam_bench/scenarios.closed.yaml` · `tests/test_scripts/test_itam_bench_closed_kit.py`(개수 14 → 18) |

#### 9.2 검증

- **정의 없는 DB 비트 동일(G-1)**: `prompt_render_diff --ci` 차이 0(데이터 조회 13,959자 · 알람 24,103자) · 6프로필(polestar 4종·itam·test_db) × 2문항 × 단일/멀티 × 선별/생성 하네스 바이트 동일(`PYTHONHASHSEED=0` · 기준 worktree 대조).
- **회귀**(D-303 · 모듈 단위): 검증 라운드 본체 파일·폴더 342 — 통과 8,365 · 실패 1 · 에러 19 · 건너뜀 49 · 실패 귀속 전부 「원래 실패」(`test_e2e_polestar.py` 19 실 DB 픽스처 · `test_plan104_local_sandbox_profile_gate` 1) · 정적 게이트 diff 줄 위반은 모두 `plans/122` 줄. 교정 라운드 통과 2,194 · 실패 0(ruff 1줄 `query_generator.py:1003` UP045는 `1ac2f93` 줄). W7 통과 349 · 실패 0 · 정적 게이트 통과. 각 라운드 마지막 줄 `범위: 모듈 단위 — 전체 미실행`. W4·검증 라운드는 `[전체 회귀 권고]`(허브 모듈 `src.config` 87% · 직접 선택 55%)를 냈고 전체는 돌리지 않았다.
- **정적 게이트**: arch_check 위반 0 · 경고 106(기준 101 · +5는 `table_selection` application→application import) · overfit_check 신규 유입 0(공용 계층에 ITAM 테이블명 리터럴 0).
- **MLX**(D-240 · 두 평면 `mlx` 확인 · 검증자가 캐시 모델 `Qwen3.5-9B-OptiQ-4bit`를 127.0.0.1:8080에 직접 띄우고 끝나고 종료): 선별 4문항 — JSON 파싱 4/4 · 후보 안 이름 4/4 · 상한 이하 4/4 · 출처 `llm` · 문항당 25~28초. 정의 초안 1묶음 — 응답 10/10 파싱 · 유효 4 · 무효 6(전부 대표 컬럼 11~12개 → F-8로 절단 처리). 합계 2분 27초. 정확도·지연 결론은 내지 않는다.
- **반출 구조 재현**(LLM 0 · DB 0): 선별 후보 목록 **7,437토큰**(§6 기준 ≈6.3K보다 18% 큼 — 렌더러가 줄마다 `[성격]`·`연결:`을 싣는다 · 계획 형식으로 줄이면 5,919) · 선별 3/5/8개 시스템 프롬프트 1,881/3,570/4,693(≤13K · 표본 5행 가정 3,496/6,888/9,997).

#### 9.3 계획과 다르게 한 것

- **용도 블록이 정의를 읽는 함수**: `table_selection.definitions_of` 대신 같은 위치(`_structure_meta[PROFILE_KEY]`)를 도메인 키로 직접 읽는다 — 노드 간 import가 arch 경고를 1건 더 늘려서다. 같은 위치·같은 의미는 대조 테스트로 고정했다.
- **한글 민감 표현은 설정이 아니라 `data_masker` 내장**(F-1): 운영 `.env`가 `SECURITY_SENSITIVE_COLUMNS`를 영문 6종으로 덮어써 설정 기본값을 바꿔도 운영에 닿지 않는다. 폴스타 조회 대상 컬럼과 대조해 걸리는 컬럼 0건을 확인한 뒤 적용했다.
- **질의 경로 정제는 테이블 단위로 뺀다**(F-4): 한 칸이라도 어긋나면 그 테이블 정의를 통째로 뺀다(필드 단위 아님) — 승인 검증과 W5 테스트가 이미 그 방식이다.
- **시드 `manages` 2곳 문구 정리**(W7): `tcdmsif73` 「사용률 추이·과거 시점 질문용」 → 「과거 시점 질문용」 · `tcdmsif90` 「호스트 사용률 추이 질문용」 삭제 — G-6 (b)와 반대로 선별을 이끄는 문구라 `notes` 추가와 함께 고쳤다. `tcdmsif90` `notes`는 1회차 오답 사실을 적었다.
- **설정 카탈로그 개수 단언**(`test_settings_catalog.py` 391 → 392): 병행 `plans/122` 세션도 같은 줄을 쓰는 공유 단언이다 — `plans/122`에 통지했다.

#### 9.4 잔여 · 내부망

| 잔여 | 처분 |
|---|---|
| **내부망 2회차**(사용자) | 반입 → 관리자 「DB 구조」 탭 시드 정의 가져오기 → 내부망 정의서와 대조·수정 → 테이블 단위 승인(`tcdmsif81` 조회 대상 제외 권고) → `python -m scripts.itam_bench --run --env closed`(18건 · 19턴). 기준: ITAM 단일 턴 `non_sql`·`backend_limit` 0 · SQL 관측 턴 ≥ 10 · `selection_source=llm` 비율 · 턴당 지연 |
| 2회차 반입 뒤(우리) | 정답 SQL·`gold_tables` 작성 → 선별 재현율 · `[토큰예산]` 로그로 토큰 추정 계수 대조(D-159 주의 · 2026-10-07 조각 단위 추정기로 교체됨 — 교체 뒤 값과 FabriX `Given` 대조) · 재현율이 낮으면 「검증이 선별 밖 테이블을 지목하면 1회 재선별」 검토(§7) · `plans/137` 잔여(폐쇄망 ITAM 실 질의)도 같은 run으로 확인 |
| 선별 프롬프트 7.4K | 예산 안이라 그대로 — 2회차 지연을 보고 줄 형식 축소(5.9K) 검토 |
| F-5 기존 인증 결함 | `src/api/dependencies.py` `_verify_user_token`이 `type` 클레임을 보지 않는다(공용 인증 · 이 계획 전부터) — 별도 작업 |
| 마스킹 범위 밖 | 관리자 설명 생성기 표본 · `structure_meta.samples` · EAV 값 수준 비밀(속성명이 비밀번호인 행의 값) · 「암호」 부분 매칭으로 「암호화여부」 같은 컬럼도 가려짐 |
| `selection_none` 안내 | 안내 문구가 정제하지 않은 원본 `group`을 읽는다 — 파일을 직접 고친 경우에만 해당(승인 경로는 30자·금지 검사) |
| 분류 중복 | 벤치에서 `backend_limit` 턴이 `no_sql`로도 함께 잡힐 수 있다 — 2회차 리포트에서 확인 |
| 카탈로그 DB 설명 | 벤치 카탈로그의 DB 설명은 아직 파일만 읽는다(컬럼 설명·유사어만 Redis → 파일) |
| 기존 비결정성 | `schema_analyzer` 강제 보충의 set 순회로 폴스타 relevant 순서가 `PYTHONHASHSEED`에 따라 바뀐다(이 계획 전부터 · KV 캐시 재사용에 불리 — `plans/121` TP-11.10) |
| arch 경고 +5 | 노드 4곳(`schema_analyzer`·`query_generator`·`query_validator`·`multi_db_executor`)이 `table_selection`을, `table_selection`이 `sql_validation`을 import(application → application) — 선별은 노드 안 호출이라 그래프 라우팅으로 풀 구조가 아니어서 그대로 둔다 |

#### 9.5 측정 못 한 것

- 내부망 FabriX/vllm에서의 선별 정확도·재현율·지연 — MLX 9B는 형식만 봤다.
- 실제 108테이블 정의(내부망 정의서 대조 후)로 만든 선별 목록 크기 — 시드 초안 기준 수치만 있다.
- `related`의 값 겹침(D-294 ② ≥ 0.9) — 반출물에 값이 없다.

---

## 140. ITAM 자산 외부망 생성 — 반출 로그로 자산을 만들고, 다음 반출부터 생성 근거를 담는다

> **작성일**: 2026-10-07 · **v1.0**
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — W1~W4 구현 · W5 빌더 경로·가짜 LLM 시험 · W6 문서 완료(작업 트리 · 커밋 없음 · MLX·과금 0) · 독립 검증·보안 감사 1라운드 교정 완료 · 잔여: 2회차 반출 뒤 — 빌더 재실행(근거 자산) · 모의 DB 행 생성·기동 · `--p2` MLX 1회 · §8** · ~~TODO — 계획 확정(코드 0)~~ · 게이트 G-1~G-4 답 받음(인터뷰 2026-10-07 · §2)
> **요청(사용자 2026-10-07)**: *"반출한 데이터를 이용하여 외부망에서 만들 수 있는 것들은 모두 만들고 itam bench 에서 생성하는 로그 정보를 이용하여 외부망에서도 자체적으로 생성할 수 있도록 로그에 담는 정보를 수정하라."*
> **입력**: `results/itam_bench/20261006-152938/`(1회차 반출 5파일 · `.gitignore` 대상)
> **관련 계획**: `plans/133`(자산 자동 생성 P1·P2 — D-294) · `plans/135`(ITAM 벤치·반출 — D-301) · `plans/139`(테이블 정의·선별 — D-308 · 시드 2파일) · `plans/137`(한글 식별자 — D-305)
> **D-번호**: **D-311**(신규 · `docs/decisions/D-311.md`) · D-301 부기(② 예외 · 반출 파일 목록) · D-294 부기(한글 단서 · 직접 커밋 경로 · 반입 뒤 코드값 재승인)

---

### 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0. 실측은 반출 파일·코드 읽기와 스크래치 스크립트(LLM 0)뿐이다.
- 내부망 실제 DB의 기본키·주석 유무는 **모른다** — 반출물이 파일 스키마 캐시에서 왔고 그 캐시는 기본키를 싣지 않았을 수 있다. 2회차 반출(W2 이후)로 확인한다.

### 1. 현황 (실측)

#### 1.1 1회차 반출물에 있는 것 · 없는 것

| 항목 | 상태 | 근거 |
|---|---|---|
| 테이블·컬럼 이름 · 타입(길이 없음) · NULL 허용 | **있음** — 108테이블 · 1,988컬럼 | `schema_catalog.yaml` `tables` |
| 조회 대상 테이블 · DB 설명 | **있음** — 99개 · LLM 설명 1문장 | `approved_profile.allowed_tables` · `db_description` |
| 실행 기록 | 15턴 · SQL 실행 4건 | `trace.jsonl` |
| 기본키 · 행 수 · 관계 | **전부 비어 있음** — `key: []` 108/108 · `rows_estimate: null` · `relations: {}` | 카탈로그 입력이 파일 스키마 캐시(`schema_source: schema_cache`) |
| 컬럼 의미 · 유사어 | **0** | `summary.columns_with_meaning: 0` |
| 코드값 · 값 형식 · 관계 겹침 | **없음** — 반출 계약상 건수만(D-301 부기 ②) · 내부망에서 P1을 돌리지 않음 | `assets`: `prompt_template`·`knowledge_catalog` null · 프로필 규칙·예시·관계 0 |

#### 1.2 이미 만든 것(`plans/139` §4.2 · 2026-10-07)

- `testdata/itam_bench/closed/itam_schema.json`(앱 캐시 형식 · 108·1,988) · `testdata/itam_bench/closed/table_definitions.yaml`(테이블 관리 정보 초안 108)
- 로컬 `.cache/schema/itam_schema.json`을 108테이블본으로 교체(기존 2테이블본은 `.cache/schema/itam_schema.json.bak-sandbox-20260917`)

#### 1.3 내부망에서 P1을 돌려도 ITAM 자산이 거의 안 나오는 이유(코드 실측)

| 원인 | 위치 | 반출 1,988컬럼 재현 |
|---|---|---|
| 코드 컬럼 이름 단서가 **라틴 접미사만** 본다(`cd`·`yn`·`type`…). 한글 단서(`코드`·`구분`·`여부`·`상태`…)는 **주석에만** 적용 | `src/domain/schema_inference.py:50·285` `_CODE_NAME_RE`·`is_code_candidate` | 현행 판정 **0개** · 한글 이름 단서로 보면 **371개** |
| 날짜·호스트 형식 단서는 한글을 이미 본다 | `src/schema_cache/asset_generation_service.py:134-135` | 날짜 343 · 호스트·IP 72 |
| 관계 후보 = 선언 FK · 기본키 일치 추론 · 같은 기본키 군 — 기본키가 없으면 후보 0 | `_relationships` | 반출 기준 기본키 0(실 DB 미확인) |
| 조회 예산 `DEFAULT_PROBE_BUDGET` 400회 | `asset_generation_service` | 후보만 ~790개 → 대부분 「예산 초과」 |

#### 1.4 내부망으로 들어가는 경로

설정 파일은 반입 사본에 실려 들어간다. 1회차 내부망 프로필의 `query_guide`가 643자로, 로컬 샌드박스 승인본(`config/db_profiles/itam.yaml` · 「JOIN 관계 부재」 문구 — D-294 배경의 오정보)과 길이가 같다.

### 2. 사용자 결정 (인터뷰 2026-10-07)

| G | 질문 | 답 |
|---|---|---|
| G-1 | 반출 로그의 통계(기본키·주석·행 수·관계 겹침·코드 판정) 출처 | **「DB 구조」 탭 결과 읽기** — 내부망 관리자가 자산 자동 생성 1단계(P1 · LLM 0)를 먼저 돌리고, 벤치는 그 결과를 Redis에서 읽는다. 벤치 DB 조회 0 유지(D-301 ③ 불변) |
| G-2 | 코드값 반출 | **코드 컬럼만** — *"1번으로 진행하되 데이터는 가명, 익명, 치환하여 원본 데이터와 유사한 값으로 변경하라."* |
| G-3 | 외부망 자산을 내부망에 넣는 경로 | **설정 파일에 직접 커밋**(`config/db_profiles/itam.yaml` 등) — 로컬 샌드박스와 같은 파일이라는 충돌과 승인·실행 검증 우회를 고지한 뒤 선택 |
| G-4 | 치환 코드값의 쓰임 | *"코드값 등은 외부망에서 쿼리 자동 생성의 테스트 용으로 사용 예정이다. 별도의 대응표는 필요 없다."* |

**귀결**:
1. 치환 코드값은 **외부망 테스트 자료로만** 쓴다. 내부망으로 돌아가는 설정 파일에는 넣지 않는다(실제 코드와 달라 조건 조회가 틀린다).
2. 코드값·코드 라벨 자산의 정본은 계속 **내부망 P1 승인**이다. 직접 커밋한 프로필이 반입 때 내부망 파일을 덮으므로, 반입 뒤 P1을 다시 돌려 코드값·라벨만 골라 승인한다(§4 W6 절차).
3. 치환 비밀키는 run 메모리에만 두고 버린다 — 되돌릴 수 없다(익명화).
4. 직접 커밋은 내부망 검토를 건너뛰므로, 빌더는 **근거가 있는 자산만** 쓴다(이름만 보고 짐작한 식별 키·관계·규칙은 쓰지 않는다). 예외는 테이블 정의 초안(D-308 — 이름 기반 「(추정)」 표기 포함)이다.

### 3. 범위 — 무엇을 언제 어디에 만드나

| 자산 | 1회차 반출로 지금 | 2회차 반출(W2 이후) | 저장 자리 |
|---|---|---|---|
| 스키마 캐시 시드 | 있음 | 기본키·타입 길이 반영해 재생성 | `testdata/itam_bench/closed/itam_schema.json` (+ 선택 `.cache/schema/`) |
| 조회 대상 테이블 | 99 | 반출 그대로 | `config/db_profiles/itam.yaml` `allowed_tables` |
| 테이블 정의 | 초안 108 | 내부망 편집분 반영(내부망 `manual` 우선) | 프로필 `table_definitions` |
| 식별 키 | — (값 형식 근거 없음) | P1 값 형식 비율 근거 | 프로필 `entity_keys` |
| 관계 | — | 값 겹침 ≥ `OVERLAP_MIN` 채택분만 | 프로필 `relationships` |
| 쿼리 규칙 | — | P1 결정적 규칙 중 값을 담지 않은 것 | 프로필 `query_rules` |
| 유사어 시드 | — | 주석 라벨 근거 | `config/synonym_seeds/itam.yaml` |
| 코드값·코드 라벨 | — | 치환본 → **외부망 테스트 자료만** | 커밋 안 함(내부망 P1 승인) |
| 쿼리 예시 · DB 전용 규칙 섹션(P2) | — | 외부망 MLX 초안 → 모의 DB 실행 검증 · 치환 코드 리터럴 포함분 제외 | 프로필 `query_examples` · `config/knowledge/itam/prompt_template.yaml` |
| 모의 DB(외부망 테스트) | 108테이블 빈 스키마 | 치환 코드값·형식·채택 관계로 합성 행 | 저장소에는 생성기·compose만(값 0) |

### 4. 작업 (Wave)

#### W1 — P1 한글 식별자 대응 (`src/domain/schema_inference.py` · `src/schema_cache/asset_generation_service.py` · `src/schema_cache/schema_probe.py`)

1. 코드 후보 이름 단서에 한글 접미(`코드|구분|여부|상태|유형|종류|등급|분류|단계`로 끝남)를 더한다. 라틴 이름은 판정이 바뀌지 않는다 — 폴스타 4종·로컬 샌드박스 자산 초안이 비트 동일함을 테스트로 고정한다.
2. **기본키 없는 테이블 쌍의 관계 후보**: 같은 이름·호환 타입 컬럼 중 식별자형(이름 끝 `ID`·`번호`·`코드`·라틴 `id`류)이고 범위 테이블의 50% 미만에 나오는 컬럼을 후보로 삼아 양방향 값 겹침을 잰다. 채택은 종전처럼 `OVERLAP_MIN` 이상이고, 부모 쪽 유일성(고유값 수 = 비NULL 행 수)을 확인한다. 후보 상한·예산을 넘으면 「예산 초과」로 표시한다(침묵 생략 금지). 선언 PK·FK가 있는 테이블 쌍은 종전 그대로다.
3. 조회 예산: 후보 수에 맞춰 예산을 늘리되 상한을 두고, 상한과 사용량을 `evidence.budget`에 남긴다.
4. 검증: 반출 카탈로그 1,988컬럼으로 후보 수 재현(코드 후보 ≥ 300) · 가짜 클라이언트로 관계 채택·유일성 실패·예산 초과 경로 · 폴스타 비트 동일.

#### W2 — 벤치 반출 확장 (`scripts/itam_bench/catalog.py` · `__main__.py`·`_serve.py` · `redact.py` · `report.py`)

1. **카탈로그 입력 `structure_store`**: `StructureStore`의 스냅샷(기본키·선언 FK·타입 원문) · DDL 주석 · 최신 자산 초안(P1 · 대기/승인 무관 최신 1건) · 현행 프로필을 Redis·파일에서 읽는다(DB 조회 0 — D-301 ③). `--env closed` 기본값은 `structure_store`이다. 없으면 `schema_cache`로 내려가고, 리포트 첫머리에 「P1 근거 없음 — 자산 없는 기준선」을 경고한다.
2. **`schema_catalog.yaml` 칸 채움**: 테이블 `key`(기본키) · `rows_estimate` · `relations`(후보 · 겹침 비율 · 채택 여부 · 근거 종류) · 컬럼 `type`(길이 포함 원문) · 의미(주석) · `profile`(값 형식 종류와 비율 · 코드 판정 · 고유값 수 · 예산 초과·오류 사유). 숫자와 짧은 열거만 싣는다.
3. **승인 프로필 반출 확대**: `table_definitions` 전체 칸(`manages`·`kind`·`key_columns`·`related`·`notes`·`origin`) — 사람이 쓴 정의로 값이 없다 · 누출 관문 통과분만. 규칙·예시·코드값은 종전대로 건수만.
4. **치환 코드값 `code_samples.yaml`(6번째 반출 파일)**:
   - 대상: P1이 코드로 판정한 컬럼 + 공통코드 테이블의 코드·라벨. 사람 정보 휴리스틱(D-301 ② 이름 토큰)에 걸리거나 정책 등급이 `pii`·`free_text`·`amount`·`network`인 컬럼은 뺀다.
   - 치환: run마다 새 비밀키(메모리 · 기록 안 함)로 값마다 결정적·형식 보존 치환 — 숫자→숫자 · 대문자→대문자 · 소문자→소문자 · 한글 음절→한글 음절 · 그 밖 문자 유지 · 길이 동일.
   - 같은 원값은 run 안에서 어디서나 같은 치환값(조인·코드표 일관). 치환값은 원 코드 집합의 어떤 값과도 다르고 서로 겹치지 않는다(재추첨). 경우의 수가 모자라면(예: 한 자리 숫자 10개 전부) 그 컬럼은 값 없이 고유값 수만 싣고 `substitution: exhausted`로 표시한다.
   - 칸: 컬럼 · 고유값 수 · 치환값 목록(정렬) · 라벨 치환 쌍(공통코드 테이블). 빈도·원값·해시는 싣지 않는다.
5. **누출 관문**: 기존 5단계 그대로 + `code_samples.yaml`에 「원 코드값 출현 0」 단계(원 집합은 메모리). 다른 파일의 관문 기준은 낮추지 않는다. 치환값이 우연히 수집 값과 같으면 재추첨으로 피한다(관문 완화 금지).
6. `run.json` `assets`에 P1 초안 지문·생성 시각·예산 사용량을 더한다.
7. 검증: 가짜 `StructureStore`(스냅샷·초안) → 카탈로그·치환 파일 · 원값 0 · 같은 원값 같은 치환 · 형식 보존 · 고갈 표시 · 관문 실패 경로 · 입력 없음 → 폴백·경고.

#### W3 — 외부망 자산 빌더 `python -m scripts.itam_bench --build-assets <반출 경로>`

1. 반출 카탈로그 + 시드 정의 → 출력:
   - `testdata/itam_bench/closed/itam_schema.json` — `PersistentSchemaCache.save` · 기본키·타입 반영. `--install-cache`면 `.cache/schema/itam_schema.json`에도 쓴다(기존 파일 백업).
   - `config/db_profiles/itam.yaml` — `allowed_tables`(반출) · `table_definitions`(시드 + 반출된 내부망 편집분 · 내부망 `manual` 우선) · 근거 있는 `entity_keys`·`relationships`·`query_rules`(P1 조립 함수의 순수 부분을 뽑아 공유 — 사본 금지) · `source`·`environment` · 머리말(출처 run · 빌더 · 「직접 커밋 경로 — D-311」). `query_guide`는 비운다(로컬 샌드박스 오정보 — §1.4).
   - `config/synonym_seeds/itam.yaml` — 주석 라벨 근거가 있을 때만.
   - **치환 코드값은 어디에도 넣지 않는다** — 쓰기 전 검사: 커밋 대상 파일에 `code_samples` 치환값이 SQL 리터럴·코드값 칸으로 나타나면 거부한다.
2. 1회차 반출로 지금 실행한다. 나오는 것: 스키마 캐시 · `allowed_tables` · `table_definitions`. `tcdmsif81`(계정 비밀번호 칸이 있는 테이블)은 `plans/139` 권고대로 조회 대상에서 기본 제외한다(빌더 옵션으로 끌 수 있음).
3. **로컬 샌드박스 영향(G-3)**: 현행 `config/db_profiles/itam.yaml`(로컬 샌드박스 승인본)은 `testdata/itam/db_profile.local_sandbox.yaml`로 보존한다. 로컬 2테이블 샌드박스로 돌릴 때 되돌리는 방법을 `testdata/itam/README.md`에 적는다. 이 파일을 읽는 테스트(`tests/test_scripts/test_itam_bench_schema_catalog.py` 등)를 함께 고친다.
4. 검증: 1회차 반출로 실제 생성 → 프로필 로드(`StructureStore.read_current_profile` · `schema_analyzer` 정의 모드 진입) · 정의 검증 오류 0 · 폴스타 프로필 바이트 불변 · 치환값 차단 시험.

#### W4 — 외부망 모의 DB (쿼리 자동 생성 테스트용 · G-4)

1. `testdata/itam_closed_sim/`: docker-compose(MariaDB · 포트 3308 · DB `INST1` · 127.0.0.1 바인딩) + DDL 생성기(반출 카탈로그 → `CREATE TABLE`). 타입 길이가 없으면 보수적 기본값을 쓰고 그 표를 README에 적는다. 1회차 반출로 108테이블 빈 스키마를 만든다.
2. 합성 행 생성기: `code_samples` 치환값 · 값 형식 비율 · 채택 관계(조인 키 일관)로 테이블당 N행. 주석에 코드 열거가 있는 컬럼은 주석 값을 쓴다(정의 유래 — §5-3). 1회차 반출에는 형식 근거가 없으므로 생성기와 테스트만 만들고, 실데이터 생성은 2회차 반출 뒤에 한다.
3. 저장소에는 생성기·DDL 생성 코드·compose만 둔다. 치환값·합성 행은 커밋하지 않는다(생성 산출물은 `.gitignore` 경로).
4. 레지스트리(`config/db_registry.yaml`)와 MCP `.env`는 고치지 않는다 — 로컬 `itam` 대상을 모의 DB로 바꾸는 방법만 README에 적는다(사용자 실행).

#### W5 — (2회차 반출 뒤) P2 외부망 실행 — 보류

MLX로 쿼리 예시·DB 전용 규칙 섹션 초안 → 모의 DB 실행 검증 → 치환 코드 리터럴 포함분 제외 → 커밋. 이번에는 빌더 경로와 가짜 LLM 시험만 만든다. MLX는 2회차 반출 뒤 1회(대표 3문항 · 약 5분).

#### W6 — 문서·절차

1. `plans/135` 부록 A 개정(2회차부터):
   1. 반입
   2. 「DB 구조」 탭 자산 자동 생성 1단계 실행 — **필수**
   3. 코드값·코드 라벨만 골라 승인(직접 커밋 프로필에는 코드값이 없다)
   4. 테이블 정의 확인·필요 시 편집
   5. `--run --env closed`
   6. 반출 **6파일**(`code_samples.yaml` 추가)
2. D-311 구현 부기 · D-301 부기 · D-294 부기 · `plans/139` 통지 · INDEX · CHANGELOG.
3. 매뉴얼: 관리자 화면·사용법 변화 없음(P1 판정 범위만 넓어짐) — 대상 아님(D-255). 완료 보고에 적는다.

### 5. 위험·주의

1. **검토 우회**: 직접 커밋은 내부망 승인·실행 검증을 건너뛴다(G-3). 빌더는 근거 있는 자산만 쓰고 근거 종류를 머리말에 남긴다.
2. **반입 덮어쓰기**: 반입이 내부망 `itam.yaml`·시드 파일을 덮는다. 내부망 승인 이력은 버전 보관소(`.cache/structure/itam/versions`)에 남아 되돌릴 수 있다. 내부망에서만 승인한 코드값은 반입마다 다시 승인해야 한다. 반출 뒤 반입 전에 내부망에서 고친 정의는 다음 반입에 덮인다 — 절차에 「편집 → 벤치 → 반출」 순서를 적는다.
3. **주석 속 원 코드**: 주석에 코드 열거(`1:정상, 2:장애`)가 있으면 원 코드가 주석으로 반출된다. 주석은 DB 정의라 D-301 ③대로 싣지만, 같은 컬럼의 치환값과 어긋난다. 리포트에 「주석 열거 보유 컬럼 수」를 남긴다.
4. **짧은 코드의 경우의 수**: 1~2자 코드는 치환 공간이 작다 — W2-4의 고갈 표시로 처리한다.
5. **로컬 샌드박스**: 2테이블 샌드박스는 `itam` 설정을 잃는다 — W3-3 보존 · W4 모의 DB가 대신한다.
6. **병행 세션**: `collectorinfra-9b`가 `src/config.py`·`config/db_registry.yaml` 등을 편집 중이다. 이 계획은 두 파일을 건드리지 않는다(설정 키가 필요하면 모듈 상수·CLI 옵션으로 둔다).
7. **내부망 DB 부하**: P1 예산 확대 — 조회는 `SELECT DISTINCT` + 행 제한 · 잡 단위 진행 표시(종전 방식).

### 6. 검증 계획

- 모듈 단위 회귀(D-303 · `scripts/regress.py --base <세션 시작 SHA> --files <바꾼 파일>`) · 정적 게이트 · `overfit_check`(공용 계층의 한글 접미 정규식이 스키마 리터럴로 걸리지 않는지).
- 프롬프트 렌더 diff: 폴스타 4종 차이 0.
- MLX: 이번 Wave 0회(W5는 2회차 반출 뒤 1회).
- 실 산출: 1회차 반출로 빌더 실행 → 생성 파일 대조(테이블 108 · 조회 대상 98 또는 99 · 정의 검증 오류 0).

### 7. 순서

W1 → W2(W1의 evidence 모양을 읽는다) → W3 → W4 → W6. W5는 2회차 반출 대기.

### 8. 구현 기록 (2026-10-07 · 작업 트리 · 커밋 없음)

| W | 결과 | 산출 |
|---|---|---|
| W1 | 완료 | `schema_inference` 한글 코드 접미·식별자형 이름·공개 순수 함수 · `snapshot_identifier`(한글 부분만 인용) · `check_unique` · P1 이름 일치 관계(기본키 없는 테이블 · 부모 유일성 → 겹침 · 컬럼당 30 · 합계 600) · 예산 필요량 사전 계산(상한 2000). 1회차 카탈로그: 코드 후보 0 → 371 · 이름 일치 80컬럼 · 예상 조회 약 1,383. PG·DB2·로컬 MariaDB 픽스처 비트 동일 |
| W2 | 완료 | `--schema-source structure_store`(closed 기본) · 카탈로그 `p1`·`key`·`rows_estimate`·`kind: p1` 관계·`profile`·`comment_enum` · 오류 범주만 · 정의 반출(행별 관문 · 원 코드 포함 행 보류) · `code_samples.yaml`(`scripts/itam_bench/code_samples.py`) · 관문 `code_original` · `run.json` `assets.p1` · 리포트 「P1 근거」 절·경고 |
| W3 | 완료 | `scripts/itam_bench/build_assets.py` · `--build-assets RUN [--install-cache] [--keep-excluded]`. 1회차 실행: 테이블 108 · 컬럼 1,988 · 조회 대상 98 · 정의 108(시드) · 검증 오류 0 · 근거 자산 0 · 유사어 시드 없음 · 시드 스키마 캐시 불변 · `source: manual`(`environment` 없음). 로컬 승인본 → `testdata/itam/db_profile.local_sandbox.yaml` |
| W4 | 완료(생성기) | `testdata/itam_closed_sim/`(compose 3308 · `generate.py ddl|rows` · 출력 가드 · `generated/` 무시) — 1회차 DDL 108테이블 생성·재파싱 일치. MariaDB 실제 적재·합성 행은 2회차 반출 뒤 |
| W5 | 빌더 경로만 | `--build-assets RUN --p2` — P2 조각 재사용(사본 없음) · 조회 대상 스키마만 · 치환 리터럴 예시 제외·섹션 폐기 · 비과금 평면만(`RUN_E2E` 예외 없음) · DB 연결 실패 종료 2. 가짜 LLM 시험만 — MLX 0회 |
| W6 | 완료 | `plans/135` 부록 A v1.5 · D-311 구현·부기 · D-301·D-294 부기 · `plans/139` 통지 · INDEX · CHANGELOG. 매뉴얼: 관리자 화면·사용법 무변경(P1 판정 범위만 넓어짐) — 대상 아님(D-255) |

- **감사 교정(리드 기본값 — 짧은 코드 고갈 2배는 2026-10-07 사용자 확정 · 원 코드 정의 행 보류는 같은 날 사용자 번복으로 폐지: 정의 글은 그대로 반출, 누출 관문 5규칙 보류만 유지)**: 짧은 코드 여집합 누출 → 남은 치환 공간 < 2 × 고유값 수면 고갈 · 사람이 쓴 정의 글 속 원 코드 → 그 행 보류 · 빌더 차단 전체 잎·키 토큰 대조(반출 카탈로그 식별자·글과의 우연 일치 제외) · P1 비율 내림 · 정의 반출 `group` · 비밀 보유 객체 피클 거부.
- **정의 선별 근거(병행 세션 d9 실측 · o200k · 2026-10-07)**: 생성 프로필(`table_definitions` 108 · 조회 대상 98)로 정의 기반 선별 프롬프트 약 9.9K · 생성 프롬프트(컬럼 많은 8개·표본·설명) 약 20K 토큰 — 정의가 없으면 98개가 전부 실려 FabriX 입력 한도(95,232)를 넘는다(`plans/139`). 빌더 머리말에는 날짜 없는 일반 설명만 둔다. `tcdmsif81` notes 문구(「조회 대상에서 뺀다(외부망 빌더 기본 제외 · plans/139)」)는 d9가 시드에 반영 — 재생성해도 유지(확인).
- **남은 한계**: 카탈로그 컬럼 타입에 길이가 없다(스냅샷 정규화 표기 — 모의 DB는 보수 기본 길이) · 코드 테이블 판정이 코드 컬럼의 기본키를 요구해 ITAM(선언 PK 0)은 공통코드 라벨 쌍이 비게 된다 · 이름 일치는 겹침 문턱 이상 부모를 모두 채택 · 예산 상한 적정성은 내부망 P1로 확인.

---

## 143. ITAM 지식 자산 폴스타 동등화 — 폴스타 자산 전수 대응표 · 반출 근거로 Claude Code가 자산을 쓰고 외부망에서 바로 커밋 · ITAM 결정적 조립(데이터 템플릿) · 벤치 폐루프로 효과 판정

> **작성일**: 2026-10-07 · **v1.8**(3회차 반출 전 후속 사용자 결정 기록 — 2026-10-08)
> **2회차 사이클 시작 SHA**: `c161bfd` · **회귀 대상 파일**(2회차): `src/domain/knowledge_assets.py` · `tests/test_domain/test_plan143_knowledge_assets.py` · `tests/test_domain/test_plan143_k6_active_rule_verify.py` · `testdata/itam_bench/closed/knowledge/{guide,prompt_section,descriptions}.yaml` · `config/db_profiles/itam.yaml` · `config/knowledge/itam/{prompt_template,column_descriptions}.yaml` · `config/synonym_seeds/itam.yaml`(머리 주석만) · (3회차 준비 추가) `scripts/itam_bench/redact.py` · `scripts/itam_bench/catalog.py` · `tests/test_scripts/test_itam_bench_redact_identifiers.py` · `tests/test_scripts/test_plan143_p1_outdated_warning.py` · (3회차 후속 · 시작 `a95d8f5`) `testdata/itam_bench/closed/table_definitions.yaml` · `tests/test_scripts/test_plan143_history_out_of_prompt.py` · `.claude/skills/itam-knowledge/SKILL.md`
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — 외부망 구현 완료 · 2회차 사이클 외부망분 완료 · 3회차 반출 준비 외부망분 완료(2026-10-07 · 커밋 `89773af` — §9.1: 반출 SQL 식별자 남김(D-301 부기) · 구버전 P1 초안 경고 · `### 코드값` 표현 통일 · 후속 v1.7: P1 초안 형식 손상 경고 후 진행 · 회차 이력 문장 프롬프트 밖 이동)** · 2회차(`20261007-152223` · fabrix/vllm · 1회차 원천 반입 확인 — query_guide 지문 일치) 근거로 K1·K4·K3 갱신 + K6 활성 여부 문장 개정(안내 없으면 거르지 않음) · 정적 검증만 · 빌더 기본 산출은 1회차 기준 유지(재기저는 사용자 판단) · **K2·K8 미작성**(정답 판정 0 · 실행 SQL 컬럼 가림 · P1 근거 0) · **잔여**: P1 재실행(구버전 초안 — `b1fabf4` 이후 빌드) · 3회차 반출 · W0 · 모의 DB 실행 검증 · `--verify-assets` · K2·K8 · W8 효과 판정 — 상세 §9
> **요청(사용자 2026-10-07)**: *"폴스타 db와 관련되어 정리되어 있는 정보들(config/db_prfiles/polestar_b0.yaml, knowledge/catalog.yaml, semantic_model/polestar_b0.yaml, synonym/polestar_b0.yaml 등)을 모두 리스트업하고 itam db에 맞게 생성하기 위한 정보를 itam_bench를 구동시 수집하고 저장된 로그를 외부망으로 반출하여 llm에서 자동으로 생성할 수 있게 해야 한다. 또는 관련 정보를 관리자 페이지의 DB 구조 탭에서 생성할 수 있도록 하여 itam의 조회성능을 polestar의 조회성능 방큼 높일려고 한다. 이 요건에 맞게 계획을 수립하라."*
> **게이트 답(2026-10-07)**: G-1 *외부망에서 바로 커밋* · G-2/G-3 *"claude code 내에서 돌린다."* · G-4 *폴스타 기준선과 비교* · G-5 *이번에 포함*
> **입력**: 저장소 `b1fabf4` 실측(서브에이전트 2갈래 — 폴스타 자산 전수 · 기존 생성 경로) · 1회차 반출 `results/itam_bench/20261006-152938/`
> **관련 계획**: `plans/140`(외부망 빌더 · P1 근거 반출 — **선행**) · `plans/139`(테이블 정의·선별) · `plans/133`(「DB 구조」 탭 P1·P2 — 내부망 경로는 그대로 둔다) · `plans/135`(ITAM 벤치·반출 · 부록 A v1.5 = 2회차 반출 형식) · `plans/132`(소스 선별·되물음) · `plans/67`(지식 카탈로그 R1)
> **관련 결정**: **D-311 ③ 부분 개정**(G-1) · **D-294**(G-1 Python 코드 생성 기각 유지 — 템플릿은 데이터) · **D-301 ②③**(값 반출 금지 · 벤치 DB 조회 0 — 검증 모드는 부기) · D-227(질의 경로 읽기만) · D-308(정의 선별 · 존재 기반 발동) · D-142(유사어 오염 차단) · D-133(질의 이력) · D-066(단일·멀티 대칭) · D-004(LLM 출력은 정합성 근거 아님) · D-255(매뉴얼) · D-303(회귀) · D-312(스킬 형식)
> **D-번호**: **D-316**(`docs/decisions/D-316.md` · 확정 · 외부망 구현 완료)

---

### 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0. 이 계획서의 수치는 저장소 파일·코드 읽기와 1회차 반출물 기준이다.
- 내부망 ITAM의 실제 값·주석·기본키는 모른다(1회차 반출은 이름·타입·NULL·조회 대상뿐 — `plans/140` §1.1). 2회차 반출이 오면 §1 표의 ITAM 칸을 다시 잰다.
- 「성능 기여」 순위는 소비 위치로 판단한 **추정**이다 — 실제 기여는 W8 자산별 켜고 끄기로 확정한다.

### 1. 폴스타 정보 자산 전수 목록과 ITAM 현황

#### 1.1 설정 파일 자산

| # | 자산 | 폴스타 위치 · 규모 | 생성 방식 | 소비처 | ITAM 현황 | 성능 기여(추정) |
|---|---|---|---|---|---|---|
| A1 | `query_guide` | `config/db_profiles/polestar*.yaml` · b0 8,993자 · gp/yd/polestar 약 7.7K자 — 스키마 한정·서버명/호스트명 구분·LIKE 금지·통계 입도 규칙 | 수동 | `query_generator.py:188`(단일) · `multi_db_executor.py:1978`(멀티) | **없음**(HEAD) · 구 샌드박스판은 LLM 「JOIN 관계 부재」 오안내 | **상** — 모든 LLM 경로 공통 주입 |
| A2 | `query_examples`(few-shot) | 같은 파일 · b0 8건 · 그 밖 12건(질문·SQL·설명) | 수동 | `prompt_blocks.build_query_examples` → 단일 `:233`·멀티 `:2002` · 질의 이력 시드 | **없음** · P2 LLM 생성 경로는 있음(`run_asset_llm` · 빌더 `--p2`) | **상** |
| A3 | `patterns`(EAV·계층) | 같은 파일 614~659줄 · `known_attributes` 28 · `value_joins`·`excluded_join_columns` | 수동 | `schema_analyzer.py:363` · `prompt_blocks` · `sql_validation.py:939` · `assembler` · `value_index` | 없음 — ITAM은 EAV 아님 | 해당 없음 |
| A4 | `allowed_tables` | 5개 | 수동 | `schema_analyzer`·`table_selection`·멀티 게이트 | **있음** 98개(빌더) | 상(범위) |
| A5 | `alarm_allowed_tables` | 12개 | 수동 | 알람 경로 | 해당 없음 | — |
| A6 | `column_synonyms`(프로필) | 2건 | 수동 | `cache_manager.load_synonyms_with_global_fallback` | 없음 | 중 |
| A7 | `entity_keys` | hostname(casefold)·ip | 수동 | `key_bridge`·`entity_locator`(교차 시스템) | 없음 · 픽스처만(`testdata/itam/entity_keys.local.yaml`) | 중(교차 질의) |
| A8 | `relationships`·`query_rules`·`code_values`·`code_labels` | 폴스타도 없음(D-294 자산 키) | P1 결정적(내부망) | `build_profile_rules_block` 단일 `:228`·멀티 `:2001` · `table_selection` 다리 테이블 | 없음 — 1회차 근거 0 · 2회차 반출 대기(`plans/140`) | **상**(ITAM: 조인·코드 의미) |
| A9 | `table_definitions` | 폴스타 없음 | 시드·가져오기·주석·LLM 초안 → 승인(D-308) | 선별·「테이블 용도」 블록 | **있음** 108(origin `import` · 「(추정)」 14) | 상(ITAM 전용) |
| A10 | 지식 카탈로그 | `config/knowledge/_base/catalog.yaml` + `polestar_b0` 오버레이 · `pattern_a/b/c`·`taxonomy`·`measures` | 수동(plans/67 R1) | `catalog_builder` → `semantic_compiler.load_semantic_model` · 폴스타 프롬프트 렌더 | **없음** — 틀 자체가 EAV·지표·알람 전제 | 상(폴스타 결정적 경로) |
| A11 | 시맨틱 모델 | `config/semantic_models/polestar*.yaml` 약 9.5KB — R1 이후 **폴백 사본** | 수동 → 사본 | 정본 생성 실패 시만 | 없음 | (A10 종속) |
| A12 | 유사어 시드 | `config/synonym_seeds/polestar*.yaml` · `column_synonyms`·`eav_names`·`column_values` | 스크립트 파생(`scripts/synonym_seeds.py derive`) | `SynonymLoader.load_seed_yaml` → Redis | **없음** · 빌더는 주석 근거 있을 때만 | 중 |
| A13 | DB 전용 규칙 섹션 | 폴스타는 어댑터 코드(B1)가 대신함 | P2 LLM(D-294 ③) | `GeneratedTemplateAdapter`(`src/db_adapters/generated.py`) ← `config/knowledge/{db}/prompt_template.yaml` | **없음** — 파일이 없어 미발동 | 상(ITAM의 B1 대체) |
| A14 | DB 레지스트리 | `config/db_registry.yaml` 709~860 · family·zone·engine·schema·aliases | 수동 | 라우팅·엔진·스키마 주입 | **있음**(engine mariadb · `allow_hangul_identifiers` · capabilities 4종) | 상(라우팅) |
| A15 | 글로벌 유사어 | `config/global_synonyms.yaml` 303줄 | 수동 | `schema_analyzer`·`field_mapper` 폴백 | 사실상 폴스타 전용 — 한글 컬럼과 안 맞음 | 하 |
| A16 | 변화 어휘 | `config/change_terms.yaml` | 수동 | 급증 조립(폴스타 어댑터 게이트) | 해당 없음 | — |
| A17 | 기타 | `query_target_surfaces`·`source_memory_seeds`(itam 2건)·`middleware_signatures`(text2sql 무관) | 수동 | 라우팅 | 라우팅 수준만 있음 | 하 |

#### 1.2 코드에 박힌 폴스타 지식 (`src/db_adapters/polestar/` — ITAM 이식 불가 · D-294 G-1)

| # | 자산 | 하는 일 | ITAM 대체 |
|---|---|---|---|
| B1 | 전용 시스템 템플릿 `prompts.py`(1,421줄) | 지표 카탈로그·EAV 속성·알람 조인 렌더 | **A13 섹션**(데이터 파일 · 범용 어댑터) |
| B2 | 결정적 조립기 `assembler.py`(1,832줄) | EAV 피벗·폼필·월 시리즈·알람 SQL — LLM 우회 | 없음 → G-5 |
| B3 | 시맨틱 컴파일러(트랙 C) | SMQ → 결정적 SQL(카탈로그 A10 필요) | 없음 → G-5 |
| B4 | 검증기 14종 `validators.py` | 폴스타 특유 오류 → 재생성 회귀 | 범용 `validate_sql`만 → K6(데이터 기반 규칙) |
| B5 | 엔티티 탐침·급증 SQL·기간 투영 | 0건 진단·hostname 변환·급증 비교 | 해당 없음(교차 질의는 A7) |

#### 1.3 런타임 저장소

| # | 자산 | 저장 | 생성 | ITAM 현황 |
|---|---|---|---|---|
| C1 | 컬럼 설명 | Redis `schema:{db}:descriptions` + `.cache/structure/{db}/descriptions.yaml` | LLM `DescriptionGenerator`(표본 행 사용) · 탭 O-4 | 내부망 Redis 698/698 실림(1회차 서버 상태) · 반출 파일 의미 0/1,988 · 품질 미확인 |
| C2 | LLM·운영자 유사어 | Redis | O-4 LLM · 운영자 | 반출은 건수만 |
| C3 | 값 인덱스 | Redis `column_value_index` | EAV `attribute` 컬럼만 | 빈 인덱스(EAV 아님) — 코드값은 A8 블록이 대신 |
| C4 | 질의 이력 few-shot | Redis `query_history:{db}` | 골드셋 + 프로필 예시 시드 · 사람 확인분(D-133) | 원천 없음 · 플래그 `query_history_fewshot` 기본 off |
| C5 | 구조 적용본 | Redis `structure_meta` · `versions/` | 관리자 승인 | 로컬 샌드박스판만 |

#### 1.4 평가 자산

| 대상 | 자산 | 규모 |
|---|---|---|
| 폴스타 | `testdata/text2sql_gold/{gp,yd,b0}.yaml` · 샌드박스 골드 · `testdata/scenarios/*`(오라클 22) · `time_gold` | 26 + 51 + 시나리오 a~r |
| ITAM | `testdata/itam_bench/scenarios.yaml`(22) · `scenarios.closed.yaml`(18) · 오라클 `ITAM-*.mariadb.sql` 16 · `--check-oracle`(정답 SQL 읽기 전용 실행) | 1회차 SQL 관측 0턴(plans/139) |

### 2. 격차 분석

| 격차 | 원인 | 만들 수 있는 곳 |
|---|---|---|
| **값이 필요한 자산**(A8 코드값·라벨 · A12 `column_values` · A7·관계의 값 겹침 근거) | 값 반출 금지(D-301 ②) | **내부망 P1만** — `plans/140`이 근거를 반출해 빌더가 쓰는 길을 만들었다. 단, ITAM은 선언 기본키 0이라 공통코드 라벨 짝짓기가 빈다(`plans/140` §8) → K5 |
| **사람 지식이었던 자산**(A1 · A2 · A13 · C1 · A6/A12 유사어) | 폴스타는 사람이 썼다. ITAM은 쓴 사람이 없고, 생성기는 탭 P2(A2·A13)·O-4(C1)뿐 · 외부망 빌더에는 LLM 생성기가 없다 | **Claude Code 작성**(G-2/G-3) — 근거 = 구조 카탈로그 + 정의 + P1 근거 + **벤치 실패 증거**. 외부망에서 작성·검증·커밋(이 계획의 본체) |
| **코드 자산**(B1~B5) | 폴스타 의미론 하드코딩 · Python 생성 기각(D-294 G-1) | 데이터로 대체 — B1→A13 · B4→K6 · B2/B3→G-5 |
| **효과 근거** | 자산을 넣어도 무엇이 정답률을 올렸는지 모른다 · ITAM SQL 관측 0턴 | 벤치 폐루프(W1·W8) — 자산 사용 표지 · 자산별 켜고 끄기 |

**판단**: 폴스타 수준의 핵심은 「사람이 쓴 지식(A1·A2·B1)」 + 「결정적 경로(B2·B3)」 + 「검증기(B4)」다. ITAM은 소비 코드가 이미 범용이라(A1·A2·A8·A13·C1·C4) **값만 채우면 바로 효과가 나는 자산이 대부분**이다. 그래서 이 계획은 새 소비 코드보다 **작성 근거·검증·반입·효과 측정**과 결정적 조립(G-5) 하나에 집중한다.

### 3. 목표 · 비목표

**목표**
1. §1 자산마다 ITAM 대응 자산·작성 방법·검증 방법을 확정한다(§4.1 대응표).
2. **작성자 = Claude Code**(G-2/G-3): 반출 로그로 만든 **근거 묶음**을 Claude Code가 읽고, 자산별 출력 계약에 맞춰 **원천 파일**을 쓴다. 앱 안에서 LLM을 부르는 생성기는 만들지 않는다(과금·MLX 0).
3. **결정적 검증 → 빌더 → 바로 커밋**(G-1): 원천 파일은 검증 CLI를 통과해야만 빌더가 설정 파일로 옮긴다. 통과분은 외부망에서 바로 커밋하고 반입한다(내부망 승인 단계 없음 — D-311 ③ 개정).
4. **ITAM 결정적 조립**(G-5): 자주 쓰는 질의형을 **검증된 SQL 템플릿(데이터 파일)**으로 두고, 질문이 템플릿에 맞으면 코드가 SQL을 조립해 LLM SQL 생성을 우회한다(폴스타 B2·B3 대응 · Python 코드 생성 아님 — D-294 G-1 유지).
5. 벤치가 자산별 효과를 기록하고(자산 사용 표지 · 켜고 끄기), 내부망 실행 결과(값 없음)로 틀린 항목을 다음 사이클에 걷어낸다.
6. **성능 목표(G-4)**: 같은 내부망 환경(FabriX)에서 ①ITAM 벤치 SQL 관측 턴 정답률 ≥ 폴스타 하네스 정답률 ②단순 질의 지연 < 10s ③실패 분류 `wrong_table`·`wrong_column`·`non_sql` 감소. 폴스타 기준선은 W0에서 같은 환경으로 잰다.
7. 폴스타 자산·경로는 바이트 동일 — 새 자산은 ITAM 파일이 있을 때만 발동한다(D-308과 같은 존재 기반 발동).

**비목표**: Python 어댑터·조립기·검증기 코드 생성(D-294 G-1) · 값 반출(D-301 ②) · 앱 내 LLM 지식 생성기·「DB 구조」 탭 생성 진입점(G-2/G-3 — 탭의 기존 P1·P2는 내부망에서 그대로 쓸 수 있다) · 폴스타 자산 변경 · `eav_names`·값 인덱스(D-294 ⑧) · `plans/140` 범위(P1 근거 반출·빌더의 근거 자산) 재구현.

### 4. 설계

#### 4.1 자산 대응표 — 폴스타 → ITAM

| K | ITAM 자산(대응 폴스타) | 작성 | 근거 입력(반출물) | 결정적 검증(검증 CLI) | 실행 검증 | 출력 위치 |
|---|---|---|---|---|---|---|
| **K1** | `query_guide`(A1) | Claude Code | 구조 카탈로그 · 정의(`kind`·`notes`) · P1 근거 · 실패 분류·가린 실패 SQL | 길이 ≤ 8,000자 · 중괄호·코드 펜스 금지 · 언급 식별자 실존 · 조회 대상 밖 테이블 언급 금지 · 사용률을 ITAM에서 답하라는 규칙 금지(D-308 G-6) | — | `itam.yaml`(빌더 경유) |
| **K2** | `query_examples`(A2) + 질의 이력 시드(C4) | Claude Code | 시나리오 질문 · 정의 · 관계 · 정답 SQL | `SQLGuard` · `validate_sql`(실존·한글 식별자) · 치환 리터럴 금지 | 모의 DB(외부망 · 커밋 전 필수) · 내부망 검증 모드(반입 뒤 · 실패분 다음 사이클 제거) | `itam.yaml` · 질의 이력 시드 |
| **K3** | 컬럼 설명(C1) · 유사어 시드(A12 `column_synonyms`) | Claude Code | 한글 컬럼 이름 · 정의 · DDL 주석(반출분) · 시나리오 어휘 | 설명 ≤ 200자 · 값 금지 · 유사어 쓰기 가드(다른 컬럼 이름과 충돌 · 다의어 · 2자 미만 거절 — D-142) | — | 설명 정본 파일(신설 · §4.5) · `config/synonym_seeds/itam.yaml` |
| **K4** | DB 전용 규칙 섹션(B1 대체 · A13) | Claude Code | K1과 같음 + 실패 분류별 반례 | D-294 ③ 섹션 검증(≤ 6,000자 · 펜스 SQL 전부 검사 · 블록 ≤ 5) | 모의 DB(섹션 안 SQL) | `config/knowledge/itam/prompt_template.yaml` |
| **K5** | 공통코드 라벨(A8 `code_labels`) | **P1 보강**(내부망 · LLM 0) | 정의 `kind: 기준·코드`(시드 15)를 공통코드 후보로 · 기본키 없이 코드/이름 컬럼 쌍 판정 | 값 덮음 ≥ 0.9(D-294 ②) | P1 자체 | 내부망 탭 승인(값이라 반출 불가 — 직접 커밋 대상 아님) |
| **K6** | 데이터 기반 쿼리 규칙(B4 대체 · `query_rules` 확장) | 정의 `kind`에서 결정적 파생 | `현행`(활성화여부) · `수집이력`(기준년월일 형식) · `수집적재`(선택 금지) | 규칙 형식 검사 | — | `itam.yaml`(빌더) |
| **K7** | `relationships`·`entity_keys`·`query_rules`(P1) | `plans/140` 그대로 | 2회차 P1 근거 | D-294 ② | — | 빌더(D-311 ③) |
| **K8** | **결정적 조립 템플릿**(B2·B3 대응) | Claude Code | 시나리오·정답 SQL·실패 분포에서 빈도 높은 질의형 | 템플릿 계약 검사(§4.6) · 슬롯 형식 · 조립 결과 `SQLGuard`·`validate_sql` | 모의 DB 전 슬롯 조합 실행 · 내부망 검증 모드 | `config/knowledge/itam/query_templates.yaml`(신설) |
| — | A3·A5·A15·A16·C3 | 만들지 않음 | EAV·알람·폴스타 어휘 전용 | — | — | — |

#### 4.2 흐름 — 반출 → Claude Code 작성 → 검증 → 빌더 → 커밋 → 반입 → 재측정

```
[내부망 run N]  itam_bench --run --env closed → 반출 6파일(plans/140) + 신규: 자산 사용 표지(trace) · 검증 결과 파일(W5)
                                                   │ (값 0 · 누출 관문 통과)
[외부망]  ① --evidence <run_dir>  → 근거 묶음(knowledge_evidence/ · 자산별 입력 요약 · 결정적)
          ② Claude Code(스킬 itam-knowledge) → 원천 파일 testdata/itam_bench/closed/knowledge/*.yaml 작성
          ③ --validate-knowledge  → 결정적 검증 + 모의 DB 실행(K2·K4·K8) — 실패 항목은 사유와 함께 거절
          ④ --build-assets(plans/140 빌더 + 지식 오버레이) → itam.yaml · knowledge/itam/* · synonym_seeds/itam.yaml
          ⑤ 커밋(G-1 — 외부망에서 바로)
[내부망]  반입 → (검증 모드 run: K2·K4·K8 SQL 실행 결과만 기록) → run N+1 → 반출 → [외부망] W8 효과 판정 · 실패 항목 원천에서 제거
```

##### 4.2.1 실행 절차 — 따라 하는 명령어

> 저장소 루트에서 실행한다. `<run>`은 반출 run 디렉터리 이름(예: `20261006-152938`), `<run_N>`·`<run_N1>`은 연속한 두 회차다. **표지**: ✅ 지금 쓸 수 있음(v1.3 외부망 구현 기준 — 옵션 이름은 `python -m scripts.itam_bench --help`와 대조했다). 과금 API·`RUN_E2E=1`은 쓰지 않는다. 내부망 단계는 `plans/135` 부록 A(0~10)를 그대로 따르고, 아래는 143이 더하는 것만 적는다.

**A. 내부망 — run N (사용자 실행)**

| # | 할 일 | 명령 · 화면 | 확인 | 표지 |
|---|---|---|---|---|
| A0 | 외부망 커밋 반입 | 기존 반입 방식으로 저장소 갱신 — 반입분: `config/db_profiles/itam.yaml` · `config/knowledge/itam/*` · `config/synonym_seeds/itam.yaml` · `testdata/itam_bench/closed/knowledge/*` | 반입은 내부망 `itam.yaml`·시드를 덮는다(되돌리기: 「DB 구조」 탭 버전 기록) · **반입 뒤 P1 재실행 → 전 자산 승인**(`plans/151` W13 · A2) | ✅ |
| A1 | 본체·MCP 재기동 | 본체 `python -m src.main --server` · MCP `cd mcp_server && python -m mcp_server` | 설명 정본 파일은 스키마 첫 로드 때 Redis에 없는 컬럼만 채운다(§4.5) | ✅ |
| A2 | 「DB 구조」 탭 | `plans/135` 부록 A 1 → 2b(P1 · **필수**) → 2c(코드값·코드 라벨만 승인 — K5 공통코드 후보 포함 · **`plans/151` W13부터 관계·코드값·쿼리 규칙·식별 키·테이블 정의 전 자산 승인** — 확인은 부록 A 0) → 2a(정의 확인) | P1 초안이 있어야 반출에 근거가 실린다 | ✅ |
| A3 | 과금 평면 확인 | `python -m scripts.bench --show-env` | 워커 FabriX · 오케스트레이터 vllm 등 비과금 | ✅ |
| A4 | 반입 자산 검증 모드 | `python -m scripts.itam_bench --verify-assets --env closed` | 반입한 K2(`itam.yaml` 예시)·K4(섹션 펜스 SQL)·K8(템플릿 × 슬롯 대표값) SQL을 읽기 전용 실행 → `results/itam_bench/<run>/asset_verification.yaml`(항목 ID·지문·성공·오류 범주·행 수 구간 · 값·SQL 원문 0 · 누출 관문 통과분만) · `DB_BACKEND=direct`면 멈춤 · **SQL 자산이 0건이면 「[중단] 검증할 자산 SQL이 없다」(종료 1)로 끝나는 것이 정상** — 1회차 반입본(K1 가이드 · K3 설명·유사어 · K4 산문 섹션 · K6 규칙)에는 SQL이 없다. K2 예시·K8 템플릿은 2회차 반출 뒤 작성 → 그때부터 실행 대상이 생긴다. 이 경우 A4는 건너뛴다 | ✅ |
| A5 | 점검 | `python -m scripts.itam_bench --dry-run --env closed` | 린트 통과 | ✅ |
| A6 | 벤치 실행 | `python -m scripts.itam_bench --run --env closed --user <계정>` | 사다리 `intent_orchestration` · 끝에 「산출물 기록」 · 턴별 자산 사용 표지 | ✅ |
| A7 | (선택) 자산 끄고 다시 실행 | `python -m scripts.itam_bench --run --env closed --user <계정> --asset-ablation <키>` | 같은 시나리오 · 그 자산만 꺼짐(벤치 서버 프로세스에서만 · `run.json`에 기록) — 키마다 run 하나 | ✅ |
| A8 | (첫 사이클만) 폴스타 기준선 | ① `python scripts/eval_text2sql.py --dry-run`(골드셋 점검만 · LLM·DB 0 — 「총 항목 26건 · 검증 통과」) ② `python scripts/eval_text2sql.py --path graph --db all > results/eval_polestar_baseline.txt` — **`--path graph`**: 본체 그래프를 `.env` 사다리대로 돈다(내부망 2단 = ITAM 벤치 `--profile tier2_intent`와 같은 단). 기본값 `orchestration`은 단일 DB 파이프라인 직접 구동이라 사다리 단과 무관해 기준선으로 쓰지 않는다 | 기동 로그 사다리 줄이 `intent_orchestration` · 리포트 「EX 정확도」(골드 26건 — b0 5 · gp 15 · yd 6) · 「스킵」 0이어야 함(접속 실패는 조용히 스킵돼 정확도 분모에서 빠진다) · A3 비과금 확인 뒤 실행(G-4 · W0) | ✅ |
| A9 | 반출 | `results/itam_bench/<run>/`의 파일만 — `plans/140` 6파일 + `asset_verification.yaml`(A4를 돌렸으면) | 누출 관문 통과(`leak_check.json`) · 제품 로그·`.cache/` 반출 금지 | ✅ |

**B. 외부망 — 근거 → 작성 → 검증 → 빌드 → 커밋 (우리 · Claude Code)**

| # | 할 일 | 명령 | 확인 | 표지 |
|---|---|---|---|---|
| B0 | 반출물 배치 | `results/itam_bench/<run>/`에 둔다 | `git check-ignore results/itam_bench/<run>` 출력 있음(추적 안 됨) | ✅ |
| B1 | 차이 점검 | `python -m scripts.itam_bench --sync results/itam_bench/<run>` | 전사본 밖 테이블·컬럼 · 정책 밖 컬럼 | ✅ |
| B2 | 근거 묶음 | `python -m scripts.itam_bench --evidence results/itam_bench/<run>` | `results/itam_bench/<run>/knowledge_evidence/` 생성 · 종료 0 · 누출 관문 통과(실패면 `leak_check.json`만) · 다시 돌려도 바이트 동일 | ✅ |
| B3 | 모의 DB 준비(2회차부터 행 생성) | `.venv/bin/python testdata/itam_closed_sim/generate.py ddl results/itam_bench/<run>/` → `.venv/bin/python testdata/itam_closed_sim/generate.py rows results/itam_bench/<run>/ --rows 20 --seed 140` → `cd testdata/itam_closed_sim && docker compose down -v && docker compose up -d` → `mcp_server/.env`의 `ITAM_CONNECTION`을 `mariadb://itam_sim_ro:itam_sim_ro_pass@127.0.0.1:3308/INST1`로(원래 줄은 적어 둔다) → MCP 9099 재기동 | 테이블 수 조회(README 「기동 · 종료」) · 1회차는 빈 스키마만 | ✅ |
| B4 | 원천 파일 작성 | Claude Code 세션에서 `/itam-knowledge results/itam_bench/<run>` | `testdata/itam_bench/closed/knowledge/*.yaml` · 항목마다 `origin: claude_code`·`evidence: <run>`·`status` · 이름만 보고 쓴 의미는 `(추정)` | ✅(스킬 `.claude/skills/itam-knowledge/SKILL.md` · K2·K8은 2회차 반출 뒤) |
| B5 | 결정적 검증(**커밋 전 필수**) | `python -m scripts.itam_bench --validate-knowledge --catalog results/itam_bench/<run> --out testdata/itam_bench/closed/knowledge/validation.yaml` | 거절 0 — 거절이면 사유를 보고 B4로 돌아간다 · 모의 DB를 못 쓰면 `db_unverified`로 실패(`--static-only`는 사전 점검용 — 그 결과로는 커밋하지 않는다) | ✅(K8 템플릿 포함) |
| B6 | 빌드 | `python -m scripts.itam_bench --build-assets results/itam_bench/<run>` (지식 원천 디렉터리를 바꾸려면 `--knowledge <DIR>` — 기본 `testdata/itam_bench/closed/knowledge` · 스키마 캐시도 갱신하려면 `--install-cache`) | 「검증 오류 0」 · B5 통과 항목만 `itam.yaml`(K1·K2·K6) · `config/knowledge/itam/`(K3 설명 · K4 섹션 · K8 템플릿) · `config/synonym_seeds/itam.yaml`(K3 유사어)에 실림 · 치환값 검출 시 거부 · 철회로 비게 된 산출 파일은 삭제 | ✅ |
| B7 | 폴스타 무변경 확인 | `python scripts/prompt_render_diff.py --ci` | 차이 0 | ✅ |
| B8 | 커밋 | `git status --short results testdata/itam_closed_sim/generated`(빈 출력이어야 함) → `git add testdata/itam_bench/closed/knowledge config/db_profiles/itam.yaml config/knowledge/itam config/synonym_seeds/itam.yaml` → `git commit` | 값·`results/` 원본·모의 DB 생성물 미포함(G-6) | ✅ |
| B9 | 모의 DB 되돌리기 | `mcp_server/.env`를 B3에서 적어 둔 줄로 복원 → MCP 재기동 → 필요하면 `cd testdata/itam_closed_sim && docker compose down -v` | 로컬 샌드박스(3307) 복귀 | ✅ |

**C. 효과 판정 — run N+1 반출 뒤 (외부망)**

| # | 할 일 | 명령 | 확인 | 표지 |
|---|---|---|---|---|
| C1 | 회차 비교 | `python -m scripts.itam_bench --compare results/itam_bench/<run_N> results/itam_bench/<run_N1>` | 시나리오별 전이 · SQL 관측 턴 정답률 · 실패 분류(`wrong_table`·`wrong_column`·`non_sql`) · 지연 | ✅ |
| C2 | 자산별 효과 | `python -m scripts.itam_bench --ablation-report results/itam_bench/<run_N1> results/itam_bench/<끈 run…>`(첫 run이 기준 · A7 실행분) | 자산별 효과 표·유지 판정(§4.7) — 켠 쪽이 정답률을 낮추지 않고 상위 실패 분류를 줄일 때만 유지 | ✅ |
| C3 | 실패 항목 철회 | 원천 파일 해당 항목 `status: withdrawn` + 사유(`asset_verification.yaml` 실패 · C2 철회) → B5 → B6 → B8 | 철회 항목은 지우지 않고 이력으로 남는다 | ✅(원천 형식) |
| C4 | G-4 판정 | C1 정답률 · 지연 ↔ A8 폴스타 기준선 | ITAM ≥ 폴스타 · 단순 질의 < 10s | ✅ |

#### 4.3 근거 묶음과 Claude Code 작성 절차

- **근거 묶음(evidence)** — `python -m scripts.itam_bench --evidence <run_dir>`가 반출 파일(`schema_catalog.yaml`·`trace.jsonl`·`report.md`·`code_samples.yaml`)과 `itam_schema.json`·현행 정의에서 자산별 입력을 결정적으로 뽑는다: 테이블 군별 컬럼·정의 · P1 근거(관계 채택·형식 비율·코드 판정 — 값 없음) · 실패 턴(분류·가린 SQL·선별 테이블·자산 사용 표지) · 시나리오 질문 · 이전 사이클 검증 실패 목록. 값은 없다(반출물에 없으므로).
- **작성 스킬** `.claude/skills/itam-knowledge/SKILL.md`(D-312 형식 — 새 세션 스킬 목록에서 로드 확인): 자산별 출력 계약(키·길이·금지 사항) · 작성 순서(K6 → K1 → K4 → K3 → K2 → K8) · 「근거 묶음에 없는 사실은 쓰지 않는다 · 이름만 보고 판단한 것은 `(추정)` 표기」 · 끝에 반드시 검증 CLI 실행. 모델·위임은 CLAUDE.md 규칙(정의 파일 고정)을 따른다.
- **원천 파일**(git 추적 · 사람이 읽고 고칠 수 있는 단일 출처): `testdata/itam_bench/closed/knowledge/{guide,examples,descriptions,synonyms,prompt_section,query_templates}.yaml`. 각 항목에 `origin: claude_code` · `evidence: <run_id>` · `status: active|withdrawn`(내부망 실패분은 `withdrawn` + 사유 — 삭제하지 않고 이력 보존).
- **빌더 경유**(병행 세션 요청): `itam.yaml`은 빌더가 통째로 다시 쓰므로 K1·K2·K6은 원천 → 빌더 오버레이로만 들어간다. 빌더 변경은 `plans/140` 커밋 뒤 그 세션과 통지하고 한다.

#### 4.4 반출 확장 (D-301 부기 · 값 0 원칙 유지)

| 추가 | 내용 | 목적 |
|---|---|---|
| 자산 사용 표지(trace 턴 필드) | 이번 턴 프롬프트에 실린 자산 키·버전 지문(해시 앞 12자)·건수 · 템플릿 적중(ID·슬롯 이름 — 값 없음) · 결정적 조립/LLM 폴백 구분 | W8 효과 귀속 |
| 검증 결과 파일(7번째) `asset_verification.yaml` | `--verify-assets` 모드: 반입된 K2·K4·K8 SQL을 읽기 전용 실행(`--check-oracle`과 같은 경로) → 항목 ID별 성공/오류 범주/행 수 구간(0 · 1~10 · 11~100 · 100+) | 실패 항목 걷어내기 — 값·결과 행 없음 |
| 실패 증거 | 턴별 실패 분류 + 가린 SQL(D-301 ② 규칙 그대로) + 선별 테이블 — 있는 칸 재사용 | K1·K4·K8 근거 |

#### 4.5 설명 정본 파일 (K3)

- 현재 컬럼 설명 정본은 Redis(+ 백업 `.cache/structure/{db}/descriptions.yaml`)라 git 커밋으로 반입할 길이 없다. `config/knowledge/itam/column_descriptions.yaml`을 신설하고, 기동·스키마 로드 시 **Redis에 없는 컬럼만** 채운다(내부망 Redis에 이미 있는 설명 — 1회차 698건 · 실표본 기반 — 과 운영자 편집 `manual`이 우선). 출처 표지 `claude_code`.
- 소비 코드는 바꾸지 않는다(`cache_manager.get_descriptions` 그대로) — 적재 지점 하나만 추가.

#### 4.6 ITAM 결정적 조립 — 데이터 템플릿 (K8 · G-5)

- **템플릿 파일** `config/knowledge/itam/query_templates.yaml` — 항목: `id` · `intent`(설명 한 줄) · `triggers`(질문 표면어 — 선택 힌트) · `slots`(이름·형식: `center`·`dept_code`·`date`·`date_range`·`hostname`·`code`(허용 코드 목록 키) · 필수 여부) · `sql`(MariaDB · 슬롯 자리표 `:slot` · 백틱 한글 식별자) · `tables`(검증용) · `notes`.
- **선택**: 폴스타 시맨틱 컴파일러와 같은 2단계 — ①LLM이 질문을 보고 템플릿 ID와 슬롯 값을 **JSON으로만** 고른다(템플릿 목록 프롬프트 · 짧음) ②코드가 슬롯을 형식 검증하고 **바인딩·이스케이프해 조립**한다(문자열 연결 금지 · 슬롯 형식 밖이면 거절). 선택 실패·슬롯 거절·조립 SQL 검증 실패 → 기존 LLM SQL 생성으로 폴백(구조화 사유 로그 — 침묵 폴백 금지).
- **배선**: `query_generator._try_deterministic` 계열 자리(단일)와 `multi_db_executor`의 같은 단계(멀티)에 **같은 함수** — D-066 대칭. 신규 모듈 `src/db_adapters/template_assembler.py`(application · 어댑터 계층 — DB 특화 로직 격리 D-089) · 프롬프트 `src/prompts/template_selection.py`.
- **발동**: 그 DB에 템플릿 파일이 있을 때만(존재 기반 · 폴스타 비트 동일). 전역 끄기 플래그 `TEXT2SQL_TEMPLATE_ASSEMBLY`(기본 on — 파일 없으면 무동작이라 「기본 off = 현행 동일」 원칙과 같은 효과 · 예외 근거를 config 주석에).
- **검증**: 템플릿 계약 검사 · 모든 슬롯 조합 대표값으로 모의 DB 실행 · 내부망 검증 모드. 실패 템플릿은 `withdrawn`.
- **초기 범위**: W0 실패 분포·시나리오 18건에서 빈도 상위 질의형 5~10개(예: 센터·부서별 서버 목록 · 유지보수 계약 만료 · 담당자별 자산 · 서비스↔서버 연결). 수치는 W0 뒤 확정.

#### 4.7 자산별 켜고 끄기 (W8)

- 벤치 `--asset-ablation <키>`: 해당 자산만 끈 run을 같은 시나리오로 돌려 비교(요청 단위 오버라이드 — 불가하면 run 단위).
- 유지 규칙: 켠 쪽이 SQL 관측 턴 정답률을 낮추지 않고, 실패 분류 상위 2종 중 하나를 줄일 때만 유지. 아니면 원천 항목 `withdrawn` → 빌더 재실행 → 커밋.

#### 4.8 작업 단위

| W | 내용 | 파일 | 결정 |
|---|---|---|---|
| **W0** | **기준선** — 2회차 반출(`plans/140` 잔여)로 ITAM 정답률·실패 분포·지연 · 같은 내부망에서 폴스타 하네스 기준선(시나리오 · `eval_text2sql`) — 사용자 실행 | 리포트 | G-4 |
| **W1** | 근거 묶음 `--evidence` + 반출 확장(자산 사용 표지 · 실패 증거) | `src/domain/knowledge_evidence.py`(신규 · 순수) · `scripts/itam_bench/{catalog,__main__,redact}.py` | D-316 · D-301 부기 |
| **W2** | 원천 파일 형식 + 검증 CLI `--validate-knowledge`(K1·K3·K4·K6 결정적 검증 · K2·K4·K8 모의 DB 실행) | `src/domain/knowledge_assets.py`(신규 · 검증 순수 함수) · `scripts/itam_bench/knowledge.py` | D-316 |
| **W3** | 작성 스킬 `.claude/skills/itam-knowledge/` + 1회차 반출로 첫 원천(K6·K1·K4·K3) 작성 — Claude Code | 스킬 · `testdata/itam_bench/closed/knowledge/*` | D-312 형식 |
| **W4** | 빌더 지식 오버레이 + 설명 정본 파일 적재 지점(§4.5) | `scripts/itam_bench/build_assets.py`(140 세션 통지) · `src/schema_cache/*` 적재 1곳 | D-311 ③ 개정 |
| **W5** | 검증 모드 `--verify-assets` + 7번째 반출 파일 + K2 예시·질의 이력 시드 연결 | `scripts/itam_bench/*` · `scripts/query_history_seed.py` | D-301 부기 |
| **W6** | K8 결정적 조립 — 템플릿 계약·선택 프롬프트·조립기·단일/멀티 배선·폴백 사유 | `src/db_adapters/template_assembler.py` · `src/prompts/template_selection.py` · `src/nodes/{query_generator,multi_db_executor}.py` · `src/config.py` | D-316 |
| **W7** | K5 공통코드 후보(정의 `kind` 단서 · 기본키 없는 코드/이름 쌍) — 내부망 P1 보강 · 모의 DB 검증 | `src/domain/schema_inference.py` · `asset_generation_service.py` | D-294 부기 |
| **W8** | 효과 측정 `--asset-ablation` · 리포트 자산별 표 · 유지 규칙 | `scripts/itam_bench/report.py` | G-4 |
| **W9** | 문서 — D-316 구현 기록 · D-301·D-294 부기 · INDEX · 매뉴얼(관리자: 반입 절차 · 사용자: 영향 없음 확인 — D-255) | 문서 | — |

**순서**: `plans/140` 커밋 → W1 → W2 → W3 → W4 → W5 ∥ W6 → W7 → (내부망 2·3회차) → W8 → W9. W0은 내부망 2회차와 함께. W3 첫 원천은 1회차 반출물로 시작하고 2회차 반출이 오면 근거를 갱신한다.

**병행 세션 경계**: `plans/140`(collectorinfra-b5)이 `scripts/itam_bench/{build_assets,code_samples,catalog,redact,__main__,report}.py`·`config/db_profiles/itam.yaml`·시드를 소유한다(미커밋). 143은 140 커밋 통지 뒤 착수하고, `itam.yaml`은 빌더(오버레이)로만 바꾼다.

### 5. 게이트 — 답 (2026-10-07)

| G | 질문 | 답 | 반영 |
|---|---|---|---|
| **G-1** | 외부망에서 만든 지식 자산을 내부망에 어떻게 넣나 | **외부망에서 바로 커밋** | D-311 ③ 「근거 없는 짐작은 직접 커밋하지 않는다(예외: 테이블 정의 초안)」 → **예외에 Claude Code 작성 지식 자산(K1·K2·K3·K4·K6·K8) 추가 — 단, 검증 CLI 통과분만**. 내부망 승인 단계 없음 · 안전장치는 결정적 검증 · 모의 DB 실행 · 내부망 검증 모드 · `withdrawn` 되돌리기 |
| **G-2/G-3** | 작성 LLM 위치 | *"claude code 내에서 돌린다."* | 앱 내 LLM 생성기·탭 생성 진입점 없음 · 외부 과금 0 · MLX 0 · 작성 = Claude Code 스킬 |
| **G-4** | 「폴스타만큼」 기준 | **폴스타 기준선과 비교** | §3 목표 6 · W0 |
| **G-5** | ITAM 결정적 조립 | **이번에 포함** | K8 · §4.6 · W6(데이터 템플릿 — D-294 G-1 유지) |
| G-6 | 원천·산출 git 추적 | (묻지 않음 — G-1 직접 커밋의 결과로 추적) | 원천 `testdata/itam_bench/closed/knowledge/` · 산출 `config/` 추적 · 값·`results/` 원본 제외(메모리 「반출 스키마 이름 추적 허용」과 같은 선) |

### 6. 검증

| 범위 | 방법 | 기준 |
|---|---|---|
| W1 | 1회차 반출물 → 근거 묶음 | 값 칸 0(누출 관문 5규칙 재사용) · 자산별 입력 결정적(두 번 실행 바이트 동일) |
| W2 | 정상·환각 식별자·펜스·중괄호·사용률 규칙·충돌 유사어 원천 픽스처 | 거절 항목·사유 · 정상만 통과 · 모의 DB 실행 실패 항목 거절 |
| W3 | 스킬 로드 확인(새 세션 목록) · 첫 원천이 검증 CLI 통과 | 오류 0 |
| W4 | 빌더 오버레이 → `itam.yaml` · 설명 적재(Redis 있음/없음/manual) | 기존 값 우선 · 폴스타 비트 동일 |
| W5 | 모의 DB(3308) 검증 모드 | 성공·오류 범주·행 수 구간만 · 값 0 · 쓰기 SQL 거절 |
| W6 | 가짜 LLM 선택(정상·없는 ID·슬롯 형식 위반·주입 시도 `'; DROP`) · 단일·멀티 같은 SQL · 폴백 사유 | 조립 SQL 바이트 고정 · 주입 거절 · 템플릿 없는 DB 무동작(`prompt_render_diff --ci` 차이 0) |
| W7 | 모의 DB 기본키 없는 공통코드 테이블 | 라벨 쌍 채택 · 겹침 0.5 기각 · 폴스타·로컬 샌드박스 비트 동일 |
| W8 | 가짜 run 두 벌 | 자산별 표 · 유지/철회 판정 |
| MLX | K8 템플릿 선택 프롬프트 대표 소수 1회(두 평면 mlx 확인 뒤) | 형식만 |
| 폐쇄망(사용자) | 2회차(W0) → 반입 → 검증 모드 → 3회차 | G-4 기준 |
| 회귀 | `regress.py --base <세션 시작 SHA>` 모듈 단위 | — |

### 7. 위험

- **승인 없는 지식이 모든 질의에 실린다**(G-1의 대가): 구 샌드박스 `query_guide`의 「JOIN 관계 부재」 같은 오안내가 그대로 반입될 수 있다 → 검증 CLI(식별자 실존·금지 규칙) · 모의 DB 실행 · 내부망 검증 모드 · W8 켜고 끄기 · `withdrawn` 되돌리기. 내부망 관리자는 「DB 구조」 탭 버전 기록에서 이전 프로필로 되돌릴 수 있다(D-311 주의 ②).
- **근거 없는 추정**: Claude Code는 값을 보지 못한다 — 코드 의미·업무 규칙은 이름과 실패 증거로만 쓴다 → `(추정)` 표기 · 값이 필요한 자산(K5 코드 라벨)은 내부망 P1로 남긴다.
- **유사어 오염**(D-142): 쓰기 가드 · 적중 표지 추적.
- **템플릿 오적중**(K8): 비슷한 질문에 다른 의도의 템플릿이 걸리면 그럴듯한 오답이 나온다 → 슬롯 필수 검사 · 「템플릿 범위 밖이면 고르지 않음」 선택지 · 적중 표지로 W8 측정 · 오적중 템플릿 철회.
- **내부망 설명 덮어쓰기**: 설명 정본 파일은 Redis에 없는 컬럼만 채운다(§4.5).
- **반입마다 재승인**(D-311 주의 ②): 빌더 산출이 내부망 승인본(`manual`·P1 코드값)을 덮지 않도록 탭 병합 규칙을 따른다.

### 8. 버전 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-07 | 신규 — 폴스타 자산 전수(설정 17 · 코드 5 · 런타임 5 · 평가) 대조 · 격차 3종 · K1~K8 대응표 · 생성기 하나·진입점 둘 · 반출 확장 · 자산별 켜고 끄기 · 게이트 G-1~G-6 · D-316 예약 |
| v1.1 | 2026-10-07 | 게이트 답 반영 — G-1 외부망 직접 커밋(D-311 ③ 개정) · G-2/G-3 Claude Code 작성(앱 내 생성기·탭 진입점 제거 · 스킬 + 원천 파일 + 검증 CLI + 빌더 오버레이) · G-4 폴스타 기준선 비교 · G-5 결정적 조립 포함(K8 데이터 템플릿 · §4.6 · W6) · 설명 정본 파일(§4.5) · 착수 조건 = `plans/140` 커밋 뒤(병행 세션 요청) · D-316 확정 |
| v1.2 | 2026-10-07 | §4.2.1 실행 절차 신설 — 내부망 A0~A9 · 외부망 B0~B9 · 효과 판정 C1~C4 명령어(✅ 지금 사용 · ⏳ W3·W4·W5·W6·W8 구현 뒤 — 옵션 이름은 구현 결과로 확정) |
| v1.3 | 2026-10-07 | 외부망 구현(`-TODO`→`-WIP`) — 근거 묶음 `--evidence` · 원천 검증 `--validate-knowledge` · 빌더 지식 오버레이(`--knowledge` · 철회 시 산출 삭제) · 설명 정본 파일 적재(Redis HSETNX) · K8 템플릿 조립(`src/db_adapters/template_assembler.py` · 단일·멀티 공용 · `TEXT2SQL_TEMPLATE_ASSEMBLY`) · 자산 사용 표지 · `--verify-assets` · `--asset-ablation` · W7 정의 기반 공통코드 쌍 · 첫 원천 4종(K1 10 · K4 9 · K3 설명 54·유사어 3) · 잔여는 내부망 |
| v1.4 | 2026-10-07 | §4.2.1 명령을 구현 옵션과 대조해 ⏳ 전부 ✅로(`--verify-assets` · `--asset-ablation` · `--ablation-report` · 빌더 `--knowledge` · 스킬) · 머리 상태의 착수 조건 문구 정정(140은 `b1fabf4`에 커밋돼 있었음) |
| v1.5 | 2026-10-07 | 2회차 사이클(외부망) — run `20261007-152223` 검토 · K1 g03~g06·g08·g09 · K4 s01·s03~s05·s07·s09 · K3 3건 갱신 · K6 활성 여부 문장 개정 · §9 신설(발견·남은 것·사용자 판단) |
| v1.6 | 2026-10-07 | 3회차 반출 준비(외부망 · 사용자 인터뷰) — 3회차 = 정답 판정 가능 회차 · 반출 SQL 테이블·컬럼 식별자·별칭 남김(D-301 부기 · F8 해소) · 카탈로그 구버전 P1 초안 경고(F7) · 원천 규칙 표현 「### 코드값」 통일 · 내부망 P1 재실행 체크리스트 · 재기저 조건 기록 · 사용률 차단·명시 소스 되물음은 `plans/132` 이관 · §9.1 신설 |
| v1.7 | 2026-10-07 | 3회차 반출 전 후속(외부망 · 사용자 「처리하라」) — ⑧ P1 초안 형식 방어(손상 초안 고정 문구 경고 후 버리고 진행 · 설명 초안 손상 분리 · `draft_id` 관문 형식) · ⑨ 렌더 text의 회차 이력 18곳 → YAML 주석 14블록 · 빌더 재산출 · D-301 ② 잔여 위험 사용자 수용 · §9.1 갱신 |
| v1.8 | 2026-10-08 | 사용자 결정 기록(인터뷰) — ⑨ 「반출 가림→미확인」 문구 치환 수용 · 검증기 회차 이력 규칙 미도입(스킬 지침 + 테스트) · Low 잔여 유지 · 코드 변경 없음 |

### 9. 2회차 사이클 — run `20261007-152223` (2026-10-07 · 외부망)

**입력**: 내부망 run `20261007-152223`(closed · `tier2_intent` · 워커 fabrix / 오케스트레이터 vllm · 시나리오 18 · 턴 19 · SQL 관측 14 · 실행 성공 17/17 · 오라클 판정 0/0 · 누출 관문 통과). 턴 자산 표지의 `query_guide` 지문 `6296dc04ec9b`가 1회차 원천 빌드본과 같다 — 1회차 반입이 실렸다. 외부망 B1(`--sync`)·B2(`--evidence`)·C1(`--compare 20261006-152938 20261007-152223`) 실행.

**발견(근거 = 근거 묶음 `turns.yaml` · `report.md`)**

| # | 발견 | 처분 |
|---|---|---|
| F1 | 현행 원장 조건에 코드 리터럴을 턴마다 다르게 지어냄(두 값 혼용) — K6 「이 칸으로 활성 행만 고른다」가 코드값 안내 없는 상태에서 조건을 강요(추정 · 조건 칸은 반출 가림) | K6 문장 개정(안내에 활성 값이 있을 때만 거름) · K1 g08 · K4 s04·s05 · K3 |
| F2 | `tcdmsif72`↔`tcdmsif41` 세 칸 조인이 조건 없이도 0행(ITAM-111) · 같은 턴 `tcdmsif80` 단독은 행 반환 · `tcdmsif72`↔`tcdmsif79` 세 칸 조인은 행 반환(ITAM-106 2턴) | K1 g03·g04·g05 · K4 s03 — 서버 단위는 `tcdmsif80` · 연결 키 미확정 |
| F3 | 서비스 이름 등호 조건·부분 일치 조건 모두 0행인 턴이 있고(101·106·115·117), 어플리케이션명 부분 일치 집계(104)는 행 반환 | K1 g06 · K4 s07 (추정 유지) |
| F4 | 사용률 추이(ITAM-114)를 `tcdmsif73`으로 ITAM SQL 생성 — 1회차(`tcdmsif90`)에 이어 fabricated 반복. 프롬프트 지식만으로는 막지 못함 | K1 g09 · K4 s09 보강 · 결정적 차단은 사용자 판단 |
| F5 | 큰따옴표 별칭(`AS "…"`) 4턴 · dialect_silent 1 | K4 s01 |
| F6 | routing_miss 2 → 5 — 「자산관리에서」를 명시한 ITAM-103·118도 되물음(103·109는 1회차 completed → 2회차 clarification) | ITAM 지식 자산 밖(`plans/132` 소스 선별) — 사용자 판단 |
| F7 | P1 근거 0(프로파일 컬럼 0 · 코드 컬럼 0 · 관계 0) — P1 초안(09:17:39)이 `b1fabf4`(09:32:57) 이전 서버로 생성돼 evidence `columns`·`budget.requested` 없음. 108테이블 기본키 0이라 구버전은 관계 후보도 0 | 내부망 P1 재실행 필요 · 카탈로그에 「구버전 초안」 경고가 없음(`catalog.py` p1_warnings — 후속 후보) |
| F8 | 실행 SQL의 컬럼 식별자가 반출 가림(정책 밖 컬럼 → `<가림>`) — 어떤 칸을 썼는지 몰라 K2·K8 정답 SQL 근거가 되지 못함 | D-301 ② 범위 — 카탈로그 실존 식별자 보존은 사용자 판단 |
| F9 | 지연 중앙값 45.4s · 최대 117.0s(FabriX) — G-4 단순 질의 <10s와 거리 큼 | W0·W8에서 판정 |

**이번에 한 것**: K1 g03·g04·g05·g06·g08·g09 · K4 s01·s03·s04·s05·s07·s09 · K3 `tcdmsif72` 3건(근거 run `20261007-152223`) · K6 활성 여부 문장(`src/domain/knowledge_assets.py`) · 정적 검증(`--static-only` — 모의 DB 3308 미기동 · SQL 항목 0) · 빌드는 **1회차 run 기준 기본 산출 + 새 지식 오버레이**(2회차 재기저는 시드 +127(조회 대상 밖 `tcdmsif81` 계정 비밀번호 칸 유사어 포함) · `itam_schema.json` `_db_description` 소실 · `test_first_export_reproduces_committed_files` 기준 run 고정 문제를 동반해 보류).

**K2·K8 미작성 사유**: 정답 판정 0(폐쇄망 시나리오 관측 전용) · 실행 SQL 컬럼 가림(F8) · 코드값 0(F7) — 스킬 규칙 「정답 SQL 근거가 없으면 만들지 않는다」.

**다음 회차 전 내부망 할 일**: `b1fabf4` 이후 빌드로 기동 → 「DB 구조」 탭 P1 재실행·승인(새 초안 `budget.requested`·`cap`이 채워졌는지 확인 · 2c에서 `활성화여부` 계열 코드값 승인) → A4는 SQL 자산 0이라 생략 → 3회차 run 반출.

#### 9.1 3회차 반출 준비 (2026-10-07 · 외부망 · v1.6)

**목표(사용자 인터뷰 2026-10-07)**: 3회차는 **정답 판정 가능 회차**다 — 3회차 반출 SQL로 정답 SQL·K2·K8을 만들고 4회차부터 대조한다.

| # | 항목 | 처분 |
|---|---|---|
| ② | 반출 SQL 식별자 가림 완화(F8) — 사용자 원문 *"데이터만 가리면된다. 테이블, 컬럼 이름은 가리지 않아도 된다."* | **완료** — `scripts/itam_bench/redact.py` `_identifier_judge`: 백틱 이름·`AS` 뒤 별칭·따옴표 없는 비ASCII 낱말을 카탈로그 실존과 무관하게 남긴다. **식별자 자리에 쓴 값은 가림** — 식별자 모양 아님 · 값 자리(비교·`LIKE`·`BETWEEN`·`IN` 목록·`THEN`/`ELSE`/`WHEN` 직후·값 비교 뒤 하위 질의 SELECT 목록) · 같은 조각에 사람·서술형 또는 정책 밖 카탈로그 칼럼 · 별칭은 가린 리터럴과 겹침·상수 단독·사람 칼럼 단독 항목일 때. 리터럴·주석·사람 정보·결과 값·`safe_label`·`redact_text`·누출 관문 불변. 수용 잔여: 값 리터럴 없는 별칭 · 선택 목록·ORDER BY·OFFSET 무따옴표 낱말 · D-301 부기. 보안 감사 3라운드(최종 Critical·High 0) · 회귀 테스트 45건 |
| ⑥ | 카탈로그 구버전 P1 초안 경고(F7) | **완료** — `scripts/itam_bench/catalog.py` `P1_OUTDATED_DRAFT`·`p1_outdated()` — evidence `columns`·`budget.requested`·`budget.cap` 중 하나라도 없으면(`b1fabf4`가 더한 키) `p1_warnings`에 추가 · 리포트 첫머리 `> 주의 — …` |
| ⑦ | 원천 규칙 문장 「코드값 안내(블록)」 → 실제 머리말 `### 코드값` | **완료** — `src/domain/knowledge_assets.py` `CODE_VALUES_HEADING` · K6 문장 · 원천 K4 2·K1 1·K3 10건 · 빌더 재산출(1회차 run 기준 + 오버레이 · `--knowledge-static-only` · 차이는 문구 줄·생성 시각뿐) · `validate-knowledge --static-only` 76/76 · `prompt_render_diff --ci` 차이 0 |
| ⑤ | 빌더 기본 산출 2회차 재기저 | **보류 — 3회차 반출 뒤** 재기저한다. 그때 함께: 조회 대상 밖 테이블(예: `tcdmsif81` 계정 비밀번호 칸) 유사어 시드 제외 · 스키마 캐시 `_db_description` 보존 · `test_first_export_reproduces_committed_files` 기준 run 갱신 |
| ⑧ | P1 초안 형식 방어(v1.6 verifier Low) | **완료(v1.7)** — `catalog.py` `p1_malformed()` · 손상(초안·evidence·budget·assets·code_values/labels 비매핑 · 목록 칸 비목록 · `draft_id` 관문 형식 아님)이면 초안을 통째로 버리고 `P1_MALFORMED_DRAFT`(고정 문구) 경고 후 진행 · 목록의 비매핑 항목은 건너뜀 · 설명 초안만 손상이면 `P1_DESCRIPTION_DRAFT_MALFORMED`(스냅샷·P1 유지) · 정상·구버전 초안 산출물 sha256 동일 · 이전엔 AttributeError 중단 · 스냅샷 폐기 · 관문 위반 exit 1 |
| ⑨ | 회차 이력 문장 프롬프트 밖 이동(v1.6 verifier Low) | **완료(v1.7)** — 렌더 text·정의 notes 18곳(K1 8 · K4 6 · K3 3 · `table_definitions` tcdmsif90 notes 1 — `build_table_purpose_block`이 렌더) → 항목 위 YAML 주석 14블록 · 관측 사실은 회차 없이 유지 · 「쓴 칸은 반출 가림」→「미확인」 · 빌더 재산출(1회차 기준 · `--knowledge-static-only`) 차이는 이력 문구·생성 시각뿐 · validate 76/76 · 토큰 추정 감소 query_guide −132 · K4 −40 · 설명 3건 각 −8 · notes −41 · 스킬 1줄 · **사용자 결정(2026-10-08)**: 「반출 가림→미확인」 치환 수용(*"가리지 않기로 했다."*) · 원천 검증기에 「text 안 회차 이력 금지」 규칙은 넣지 않는다 — 스킬 지침 + `tests/test_scripts/test_plan143_history_out_of_prompt.py`로 막는다 · Low 잔여(`_column_profile`·`_p1_relations` 스칼라 형 미검사) 유지 |
| ① | 내부망 P1 재실행 | 사용자 몫 — 아래 체크리스트 |
| ③④ | 사용률 질문 ITAM 결정적 차단(F4) · 「자산관리에서」 명시 질문 되물음 회귀(F6 · ITAM-103·118) | **`plans/132`로 이관**(병행 진행 — D-319) · 132 lead 실측: 되물음 원인은 143 지식 자산이 아니다(118·102·105 = 라우트 존 게이트가 명시 「자산관리」 무시 · 103·109 = 시간 해석기 「N개월 안에 … 종료」 거짓 되물음) · 132 가드가 `knowledge_assets.mentions_utilization`을 import한다(이름·판정 정규식 변경 시 132에 통지) |

**3회차 전 내부망 체크리스트(①)**
- [ ] 반입 커밋(이번 3회차 준비분 포함)으로 빌드 · 기동 — `b1fabf4` 이후 빌드인지 확인
- [ ] 「DB 구조」 탭에서 ITAM P1 재실행 → 새 초안의 `budget.requested`·`budget.cap`이 채워졌는지 확인(벤치 카탈로그에 「구버전 빌드」 경고가 없어야 한다)
- [ ] 2c 코드값 단계에서 `활성화여부`·`활성여부` 계열 코드값 승인(K6 활성 여부 규칙이 `### 코드값` 블록을 근거로 동작)
- [ ] 3회차 run 반출(`--env closed`) — trace의 실행 SQL에 테이블·컬럼 이름이 보이는지 확인

**검증(3회차 준비분)**: `regress --base c161bfd --files`(2회차+3회차 준비 14파일) 본체 통과 917 · 실패 0 · arch·overfit·ruff·mypy 통과 · 권고 블록 없음(범위: 모듈 단위 — 전체 미실행) · verifier(⑥⑦) Critical·High·Medium 0(Low: `catalog.py` `p1_outdated`에 매핑 가드 없음 — evidence·budget이 dict가 아닐 때 AttributeError · 원천·산출물에 회차 이력 서술이 프롬프트로 섞임 → 머리 주석 이동 검토 — 둘 다 잔여) · security-auditor 3라운드(최종 Critical·High 0) · 실 LLM 호출 0 · 매뉴얼(D-255) 대상 아님(벤치 하네스·내부 프롬프트 지식).

**검증(v1.7 후속 ⑧⑨)**: `regress --base a95d8f5 --files`(12파일) 본체 통과 1,146 · 실패 0 · arch·overfit·ruff 통과 · mypy 대상 없음 · 권고 블록 없음(범위: 모듈 단위 — 전체 미실행) · verifier 1라운드 Critical·High 0 · Medium 2(`draft_id` 비문자열 관문 위반 · 설명 초안 손상 시 스냅샷 폐기) 교정 · Low 잔여(`_column_profile`·`_p1_relations` 스칼라 칸 무검사 — 이상형 18종 크래시 0) · 수정 전 실패 확인(a95d8f5 worktree) · 실 LLM 호출 0 · 매뉴얼 대상 아님.

---

## 145. ITAM 벤치 반출 값 치환 — 가리던 값을 run 안 일관 형식 보존 가짜 값으로 · 관계 등가류로 조인 보존 · 관문 확장

> **작성일**: 2026-10-08 · **v1.1**(W1~W4 + 교정 3라운드 구현 · 커밋 없음)
> **계획 시작 SHA**: `89773af` · **회귀 대상 파일**: `scripts/itam_bench/substitute.py`(신규) · `redact.py` · `judge.py` · `__main__.py` · `asset_verify.py` · `code_samples.py` · `report.py` · `build_assets.py` · `knowledge.py` · `tests/test_scripts/test_itam_bench_substitute.py`·`test_itam_bench_substitution_apply.py`·`test_itam_bench_substitutions_downstream.py`·`test_plan145_correction1~3.py`·`test_plan145_verify.py`·`verify2`·`verify3`(신규) · `test_plan140_w2_export.py`·`test_plan140_audit_security.py`·`test_itam_bench_redact.py`(갱신)
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — 구현 완료 · 3회차 반출 확인 대기 · U-1 = A 현행 유지(사용자 2026-10-08)** — 보안 감사 4라운드 Critical·High·Medium 0 · verifier Critical·High·Medium 0(A1~A9 충족 · A10 해당 없음) · 계획 끝 모듈 회귀 `--base 89773af` 21파일 본체 1,151 통과·실패 0 · arch·overfit·ruff 통과(`범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` `run_turn` 반환 변경) · 함께 고친 기준선 결함: 반출 SQL FROM 테이블 가림(146 F9) · 끝 마침표 IP 원값 통과 · 판정 키 열 미해석 fail-open · 「분류 필요 컬럼」 한글 조각(146 F12) — 상세 §8
> **요청(사용자 2026-10-08 인터뷰 · 확정 「진행하라」 · 팀 리드 경유)**: *"가리지 않기로 했다. 그리고 만약 가리더라도 다른 유사한 데이터로 치환하라."* · 범위 *"모두 적용하라."* · 일관성 *"a로 하고 데이터는 FK를 참고하여 조인이 가능한 가짜데이터가 들어가야 한다."* · 시점 a(3회차 반출 전)
> **관련 결정**: **D-321**(이 계획 · 원 D-320 → 원격 `apm_noise` D-320과 겹쳐 재부여) · D-301 ②④⑤(값 기본 거부 → 기본 치환 · 사용자 정보 · 관문) · D-311 ②(코드값 형식 보존 치환 — 생성기 통합) · D-316(근거 묶음·빌더 — 치환값 차단) · D-308(반출 이름 git 추적·값 제외) · D-303(회귀)
> **관련 실수**: `docs/18` 2026-10-07 「반출 가림 완화 … 정책 밖 한글 칼럼 곁 값이 새는 안」 — 운영 정책이 비어 있다(정책 밖 = unclassified)는 조건으로 설계하고, 기준선 대비 「전에는 가렸는데 이제 다르게 나가는 입력」을 감사 재현으로 고정한다.

---

### 0. 증거 규칙

- 저장소 `89773af` 코드 읽기 기준. LLM·DB·과금 호출 0. 벤치 실 run 없음(테스트·`--dry-run`만).
- 반출 계약 변경이라 **독립 보안 감사(security-auditor) High 이상 0**이 승인 조건이다.

### 1. 현행 (실측)

| 지점 | 위치 | 현행 반출 |
|---|---|---|
| L1 SQL 리터럴 | `redact.py` `redact_sql` — `_literal_plan` 처분 `mask` | `'<가림>'` |
| L2 SQL 주석 | 같은 함수 `comment` | `/*<가림>*/` · `-- <가림>` |
| L3 SQL 식별자 자리 값 | `_redact_plain`·`ident` 처분 `mask`(`_identifier_judge`) | `<가림>` |
| L4 사람 값·카나리아 | `PiiVault.scrub`(redact_sql·redact_text·scrub_tree 마지막 단계) | `<가림:pii>` |
| L5 남긴 리터럴의 사람 값 | `redact_sql` `unsafe` | `'<가림:pii>'` |
| L6 오류 문구 | `redact_text` — `near '…'` · 가린 리터럴 내용 · 근거 없는 따옴표 조각 | `<가림>` |
| L7 결과 값 | `summarize_result` — `pii`(고유 수) · `amount`(합·최소·최대) · `free_text`·`unclassified`(길이) · 강등 `general`(값 없음) | 값 없음 |
| L8 판정 상세 | `judge.sanitize_detail` — 키 비허용 시 키 목록 건수 · 금액 행별 값 삭제 | 건수·삭제 |
| L9 사용자 정보 | `mask_identifier` — `run.json` `login_user`·`operator`·`host` · `asset_verify.py` `operator`·`host` | 앞 1자 + `***` |
| L10 코드값 | `code_samples.py` — run 비밀키 HMAC 결정적 치환(D-311 ②) | 치환값 |
| L11 IP 끝자리 | `mask_ip` 어디서나 | `a.b.c.***` |
| L12 정규식 PII | `_scrub_free_text`(`scrub_pii`) | 앞뒤 1자 + `*` + 자릿수 |

운영(폐쇄망) 정책은 2테이블뿐이라 106테이블 컬럼은 `unclassified`다 — 결과 값 대부분이 L7에 걸린다(`docs/18` 2026-10-07 교훈).

### 2. 설계

#### 2.1 생성기 하나 — `scripts/itam_bench/substitute.py` `FakeValues` (run 스코프)

- **난수**: 후보 글자는 `secrets`(`SystemRandom`)로만 뽑는다. 원값의 **내용**(글자 값·해시·키 기반 변환)에서 파생하지 않고 **모양**만 본다 — 위치별 문자 군(숫자 · 라틴 대/소 · 한글 음절 · 그 밖 문자 유지)과 길이 · 여러 자리 숫자열 첫 자리 0 아님. HMAC·시드·주입 난수원은 두지 않는다(테스트는 성질로 단언).
- **대응(메모)**: 원값 → 가짜 값 사전은 이 객체 안 메모리에만 있다. 피클 거부 · `repr`에 값 없음 · 예외 문구에 값 없음 · 파일·로그 기록 0 · run 끝나면 객체째 버린다.
- **메모 키**: 원값 문자열에서 끝 공백을 떼고 **ASCII 대문자만** 소문자로 접은 글(길이 불변). 가짜 값은 키 기준으로 한 번 뽑고, 호출마다 원값의 위치별 ASCII 대소문자·끝 공백을 다시 입힌다 — MariaDB 기본 콜레이션(대소문자 무시 · PAD SPACE)에서 같은 값끼리 같은 가짜 값이 되어 조인이 유지된다.
- **전역 일관**: 메모는 컬럼과 무관하게 값 하나에 가짜 값 하나다(같은 run 안 SQL 리터럴 · 결과 표본 · 판정 상세 키 · 오류 문구 · 코드값 파일이 전부 같은 가짜 값). FK가 선언되지 않은 조인도 유지된다.
- **날짜 모양**(`YYYY-MM-DD` · 뒤에 ` `/`T` + `HH:MM[:SS[.f]]`): 고정 구간(2000-01-01~2030-12-31)에서 무작위 유효 날짜·시각을 같은 서식으로 낸다(문자 군 치환은 13월·47일을 만든다).
- **후보 거절(재추첨 `MAX_DRAWS`)**: ①자기 원값과 같음(키 기준) ②알려진 원값 집합(P1 `CodeOriginals` + 이 run에서 `fake()`에 들어온 원값 전부 + `PiiVault` 수집 값·카나리아)과 같음 · 3자 이상 원값을 부분 문자열로 품음(`CodeOriginals.hit` 규칙) ③이미 낸 가짜 값과 같음(키 기준 — 서로 다른 원값이 한 가짜 값으로 합쳐지지 않게) ④카탈로그 식별자(테이블·컬럼 이름)와 같음 ⑤누출 관문 규칙(`LeakGate.rules`)에 걸림.
- **작은 공간 여집합 차단(D-311 감사 M-1 일반화)**: 값의 치환 공간(모양으로 만들 수 있는 값 수)에서 그 공간에 드는 알려진 원값 수를 뺀 남은 공간이 `MIN_SPACE_FACTOR(2) × (그 공간에서 이미 낸 가짜 값 수 + 1)` 미만이면 치환하지 않는다.
- **치환 불가 처분**: 바꿀 글자가 없음 · 공간 부족 · 재추첨 소진이면 `fake()`는 현행 가림 표지(`<가림>`)를 돌려주고 사유별 수만 센다(사유 열거 · 값 없음). 코드값 파일 쪽 API(`fake_or_none`)는 `None`을 돌려 기존 `exhausted` 처분을 유지한다.
- **등록부(관문용 · 메모리)**: 낸 가짜 값 집합 · 원값 집합 · 대응 쌍 — 관문(§2.5)만 읽는다. 반출 파일에는 가짜 값 목록과 수만 나간다(§2.6).
- **코드값 파일 통합(D-311 ②)**: `code_samples.py`의 `_Substituter`(HMAC)를 `FakeValues`로 바꾼다 — 같은 run의 생성기 인스턴스를 그대로 써서 SQL·결과에 나온 코드값과 `code_samples.yaml` 치환값이 같다. 컬럼 단위 `MIN_SPACE_FACTOR`·이어 붙인 글 관문 검사·`exhausted`·`flag`·제외 사유는 그대로.

#### 2.2 관계 등가류 — FK·P1 관계로 이어진 컬럼은 같은 처분

- 카탈로그 관계(`tables.*.relations[].columns` 쌍 — 종류 `declared`·`inferred`·`p1`·`approved_profile` 전부) + `same_key_groups`로 `table.column` 합집합-찾기(union-find)를 만든다.
- 등가류 안 가장 엄격한 등급을 그 류의 모든 컬럼 등급으로 올린 **유효 정책**을 run 시작 시 한 번 만든다(`ColumnPolicy` 사본 · 내리기 없음). `general` 컬럼이 `pii`·`unclassified` 컬럼과 관계로 이어져 있으면 그 `general` 컬럼 값도 치환된다 — 한쪽은 원값·다른 쪽은 가짜 값이 되어 조인이 깨지거나 원값↔가짜 값 짝이 드러나는 것을 막는다.
- 같은 값은 전역 메모로 같은 가짜 값이라 등가류 안 조인 결과(자산.담당자사번 = 사원.사번)가 유지된다. 등가류는 **치환 여부**를 맞추는 데만 쓴다(가짜 값 공간은 나누지 않는다).

#### 2.3 적용 지점 (전 → 후)

| 지점 | 후 |
|---|---|
| L1 SQL 리터럴 `mask` | `'<가짜>'`(리터럴 내용 전체를 키로) |
| L2 주석 | 주석 내용 → 가짜 글(형식 보존) |
| L3 식별자 자리 값 | 낱말 → 가짜 값 |
| L4·L5 사람 값·카나리아 | `PiiVault.scrub`가 표지 대신 가짜 값 |
| L6 오류 문구 | `near '<가짜>'` · 가린 리터럴 내용 → 같은 가짜 값 · 근거 없는 따옴표 조각 → 가짜 값 |
| L7 결과 값 | 비`general` 등급(+강등 `general`) 열에도 `general`과 같은 모양(`sample` 5 · 범주형 `top_values` 10 — 빈도는 실제 값)으로 **가짜 값**을 채우고 열 항목에 `substituted: true`. 기존 통계 칸(`distinct`·`sum/min/max`·`length`)은 유지 |
| L8 판정 상세 | 키 비허용 시 키 목록 → 같은 가짜 키 · 금액 행별 값 → 가짜 수 |
| L9 사용자 정보 | `run.json`·`asset_verification` 머리의 계정·OS 사용자·머신 이름 → 가짜 값(원값 첫 글자도 남기지 않음). 콘솔 출력(반출 아님)은 현행 `mask_identifier` 유지 |
| L10 코드값 | §2.1 통합 |
| L11 IP | 끝자리 `***` → 가짜 끝자리(1~254 · 전체 IP 키 메모 · 원값과 다름). 앞 세 자리는 현행 계약(D-301 ②) 그대로 |
| L12 정규식 PII | **변경 없음(현행 모양 가림)** — §5 사용자 판단 U-1 |

- 생성기는 `PiiVault`가 품는다(`PiiVault(..., fakes=FakeValues)`) — 가림 함수들의 공개 시그니처를 늘리지 않는다. 생성기가 없는 `PiiVault`(근거 묶음·검증 모드)는 현행 표지 그대로다.
- 그대로 두는 것: 남김 근거(프롬프트 말 · 일반 컬럼 비교 · 날짜/짧은 수 · 서식 · 식별자) · 식별자 남김(D-301 부기 2026-10-07) · 결과 열 이름(`safe_label`) · 카탈로그(값 0) · `general` 값.

#### 2.4 run 배선 (`__main__.py`)

- run 시작에 `FakeValues`를 만든다 — 재료: P1 `CodeOriginals`(있으면) · 카탈로그 식별자 · 관문 규칙(관문을 run 시작에 만들거나 규칙 함수만 먼저 만든다 — 수집 값은 그때그때 반영).
- `RunContext`의 vault·정책을 생성기 보유 vault·유효 정책(§2.2)으로 바꾼다. `stage_gated`는 같은 생성기로 `code_samples.yaml`을 만든다.

#### 2.5 누출 관문 확장 (기존 6규칙 기준 불변 — 규칙 추가만)

- 새 규칙 `substitution`(전 파일):
  ① **충돌** — 등록부의 가짜 값 중 하나라도 등록된 원값(키 기준 · 길이 `MIN_VALUE_LEN` 이상 · 숫자만이면 `MIN_DIGIT_VALUE_LEN` 이상) 또는 수집 값과 같으면 위반(나중에 본 원값과 앞서 낸 가짜 값의 우연 일치 — 닫힌 쪽 실패).
  ② **양성 검사** — `trace.jsonl` 결과 열 중 `substituted: true` 항목의 `sample` 원소·`top_values` 키는 전부 등록부 가짜 값이거나 가림 표지여야 한다(원값 통과 차단).
  ③ `substitutions.yaml`의 값 목록은 전부 등록부 가짜 값이고 머리 문구가 고정 문구와 같아야 한다.
- 생성기·관문은 원값·대응을 위반 기록에 싣지 않는다(위치·규칙만 — 현행 `leak_check.json` 형식).

#### 2.6 반출 표기와 하류 차단

- 고정 문구 `SUBSTITUTION_NOTE` = 「형식 보존 치환값 — 원값 아님 · 대응표 없음 · run마다 새 난수 · 외부망 테스트 전용 · 설정 파일에 넣지 않는다」 — `run.json` `substitution_note` · `report.md` 첫머리 · `code_samples.yaml` `note`(현행 문구 유지 또는 같은 뜻으로 통일 — 관문 고정 문구 검증 동반).
- 새 반출 파일 `substitutions.yaml`: `note` · `summary`(가짜 값 수 · 치환 불가 사유별 수) · `values`(이 run의 가짜 값 정렬 목록 — 원값·컬럼 연결 없음). 반출 파일 수가 하나 늘어난다.
- 하류(외부망): `knowledge.py`(`--evidence`·`--validate-knowledge`)와 `build_assets.py`의 치환값 차단 대상에 근거 run의 `substitutions.yaml` 값을 더한다(현행 `code_samples.yaml` 차단과 같은 토큰 경계 규칙 · 짧은 값 하한은 `PiiVault`와 같게 · 건너뛴 수는 요약에). `run_literals`의 `<가림` 제외 조건은 그대로 두되 가짜 리터럴이 근거 리터럴로 잡히는 것(자산에서 거절)은 의도된 동작이다. 1·2회차 반출(파일 없음)은 현행대로 읽는다.

### 3. 위협 모델

| 위협 | 대응 | 잔여 |
|---|---|---|
| 역산(가짜 → 원값) | 내용 파생 없음(`secrets` 난수) · 대응 메모리 전용·직렬화 거부 · run마다 새 객체 | 모양(길이·문자 군·대소문자·구분자·숫자 첫 자리) — 「형식 보존」 요구 그대로 |
| 우연 일치(가짜 = 원값) | 후보 거절 ①②⑤ · 관문 `substitution` ①(나중에 본 원값까지) · 기존 `pii_value`·`code_original`·`user_info` | 관문 실패 = run 미기록(닫힌 쪽) — 확률 낮음(짧은 값은 P1 원값을 먼저 싣는다) |
| 작은 공간 여집합 | 공간 하한(§2.1) → 표지 | 짧은 값 일부는 여전히 `<가림>` |
| 빈도·고유 수 | — | 사용자 수용(현행 고유 수 반출과 같은 수준) · `top_values` 빈도 · 컬럼 사이 같은 값 여부(전역 메모의 결과 — 조인 요구) |
| 원값↔가짜 값 짝 노출 | 등가류 등급 올림(§2.2) · 수집 값 강등 경로 유지 | 프롬프트 말(공개 시나리오 글)은 원값으로 남는다 — 공개 값 |
| 관문 우회 | 규칙 추가만 · 양성 검사 · 기존 테스트 기대 불변(표지 단언만 갱신) | — |
| 대응 유출 | 로그·예외·repr·파일 0 · 피클 거부 · 콘솔은 현행 가림 | 프로세스 메모리(run 중) |
| 실데이터처럼 보이는 가짜 값의 오용 | 고정 문구 · `substitutions.yaml` 차단 목록 · 하류 빌더 차단 | 사람이 반출본을 손으로 옮기면 막을 수 없다 |

### 4. Wave

| Wave | 범위 | 담당 파일 | 검증 |
|---|---|---|---|
| W1 | §2.1 생성기 · §2.2 등가류·유효 정책 · `code_samples.py` 통합 | `scripts/itam_bench/substitute.py`(신규) · `code_samples.py` · `catalog.py`(유효 정책 도우미가 필요하면) · `tests/test_scripts/test_itam_bench_substitute.py`(신규) · 코드값 기존 테스트 | 자기 테스트 · `regress.py --no-tests` |
| W2 | §2.3 적용 지점 · §2.4 배선 · §2.5 관문 · §2.6 표기·`substitutions.yaml` | `redact.py` · `judge.py` · `__main__.py` · `report.py` · `asset_verify.py` · `tests/test_scripts/test_itam_bench_redact*.py` 갱신 · 신규 테스트 | 자기 테스트 · `--no-tests` |
| W3 | §2.6 하류 차단 | `knowledge.py` · `build_assets.py` · 해당 테스트 | 자기 테스트 · `--no-tests` (W2와 병렬 — 파일 형식은 §2.6 고정) |
| W4 | 독립 검증 + 보안 감사(한 라운드) → 교정 | — | High 이상 0 · 계획 끝 `regress.py --base 89773af --files …` 1회 |

### 5. 사용자 판단 필요 (기본값으로 진행 · 보고)

- **U-1 정규식 PII(전화·이메일·주민·계좌·카드)**: 형식 보존 가짜 값은 정규식 관문(`pii_regex`)에 그대로 걸린다(관문 약화 금지와 정면 충돌) · 무작위 전화번호는 실존 제3자 번호일 수 있다. 기본값 **현행 유지**(앞뒤 1자 + 자릿수). → **2026-10-08 사용자 결정 A(현행 유지)** — D-321 「U-1 결정」.

### 6. 수용 기준

- A1 §1의 L1~L9·L11 각 지점에서 원값이 출력에 없고, 출력이 모양 보존 가짜 값이거나(치환 불가 시) 표지다 — 지점별 테스트.
- A2 run 안 같은 원값(ASCII 대소문자·끝 공백 차이 포함)은 SQL·결과·판정 상세·오류 문구·코드값 파일에서 같은 가짜 값이다.
- A3 등가류: `general` 컬럼이 비`general` 컬럼과 관계로 이어지면 둘 다 치환되고, 한쪽 SQL 리터럴과 다른 쪽 결과 표본의 가짜 값이 같다.
- A4 두 생성기 인스턴스는 같은 원값(충분히 긴 값)에 다른 가짜 값을 낸다 · 생성기 피클 거부 · `repr`에 값 없음 · 생성기·관문 모듈에 파일 쓰기·로그 호출 0.
- A5 가짜 값 ≠ 자기 원값 · 알려진 원값·식별자·수집 값과 다름 · 관문 `substitution`이 충돌 주입·원값 통과·목록 오염을 잡는다.
- A6 기존 6규칙 단언·「원값 0」 단언은 약화 없음(표지 문자열 단언만 갱신 — 갱신 목록을 보고).
- A7 `substitutions.yaml`·고정 문구 · 하류 빌더·근거 묶음이 가짜 값을 자산에서 거절.
- A8 작은 공간 하한 → 표지(사유 수만).
- A9 20KB 병적 입력 성능이 기준선 수준(기존 선형 단언 통과).
- A10 매뉴얼(D-255): 벤치 하네스라 사용자·관리자 화면·사용법 변화 없음 → 대상 아님.

### 7. 범위 밖

회차 간 일관성 · 프로세스 조회 존 질문 · 사용률 가드 1·3단 확장 · 제품 쪽(`src/`) 가림(감사 파일 등 — D-301 ④ 단서) · 정규식 PII 치환(U-1 답 전).

### 8. 구현 결과 (2026-10-08)

| 항목 | 결과 |
|---|---|
| W1 생성기 | `FakeValues` — `secrets` 난수·문자군 매핑(자모·한자·가나·키릴·그리스 포함 · 그 밖 ASCII) · 256자 초과 1회 추첨 · IPv4 계층 메모(전각 점 접기 · 점 연쇄 앞 4덩이) · 작은 공간 표지 · `code_samples.py` HMAC 제거 |
| W2 적용 | run 단위 2단계(`PendingTurn`·`finish_turns` — 전 원값 등록 → `collecting()` 1회 버림 → 기록) · SQL 리터럴·비교 자리 숫자(괄호·CAST·COALESCE) · 오류 문구 · 결과 비`general` 표본 · 판정 상세 키·값(미확인=최엄격 · 키 열 미해석=건수만 · 등록 DB id 태그만 원문) · `run.json` 사용자 칸 · 관문 `substitution`·IPv4 잔존 검사·`oracle.substituted_fields`·run.json 검사 · 레지스트리 실패 폴백 표지 |
| W3 하류 | `substitutions.yaml` 차단 — 빌더·지식 검증(철회 항목 포함) · 숫자 하한 4 · `SUBSTITUTIONS_FILE` 단일 정의 · P2 문구 분리 |
| 교정 | 감사: HIGH-1(`is_fake` 단락) · HIGH-2(판정 상세 raw) · MED 3 · M-A·M-A2(IP 경계) · M-B(SQL 숫자) · M-3c-1(레지스트리) / verifier: High(우연 일치 run 실패 — 단일 단계 최대 96.7% → 2단계 0) · Minor-1·A·C(철회)·D |
| 몬테카를로 | 2단계 경로 전 조건(`[A-Z][0-9]` 10~40 · `[A-Z]{2}` 20~40 · 3자 40 · 날짜 150 · 6자리 3000) run 실패 0 · 원값 반출 0 · 표지 폴백 0 |
| 갱신한 기존 단언 | `test_plan140_w2_export`(HMAC 결정성·매핑 노출 → 난수 성질 · 2자 원값은 같음만 — `code_original` 3자 기준) · `test_itam_bench_substitution_apply`(IP 계층 · 재가림 멱등성 2건 삭제 — 실행 경로 1회 가림 · `a.b.c.***` 허용형 삭제) · `test_itam_bench_redact`(20KB 성능 50ms → 200ms — 부하 지터 · 제곱 시간 5.7초와 25배 차) · 「원값 0」 단언 약화 0(verifier 확인) |
| 매뉴얼(D-255) | 대상 아님 — 벤치 하네스(외부망 반출 도구)라 사용자·관리자 화면·사용법 변화 없음 |
| 수용 잔여 | D-321 「수용 잔여」·「주의」 — 빈도·고유 수 · IP 대역 묶음 · 테이블 자리 낱말 · SQL 숫자 틈(산술 둘째·왼쪽 리터럴 등) · 짧은 비교값 표지 · 레지스트리 경고 1회 · OID 꼴 run 정지 등 |
| 남은 일 | 3회차 반출 실물 확인(`--dry-run` 먼저) |

---

## 146. ITAM 내부망 3회차 결과 교정 — 백틱 식별자 검증 누락 · 선별↔생성 테이블 불일치 · 서비스 연결 탐침 · 4회차 정답 판정 준비

> **작성일**: 2026-10-08 · **v1.4**(완료 — 잔여 이관 · v1.3 W5 (3) · v1.2 착지 — §10 착지 보고)
> **상태**: **완료(2026-10-08 · 잔여 이관)** — W1·W3·W4·W5 (1)(2)(3) 완료(계획 끝 회귀 rc=0 · W5 (3) 회귀 rc=0 · 모듈 단위) · 내부망 4회차 실행됨(`20261008-133613` — 오라클 7/7 · 1054 오류 0) · **이관**: W2 선별 보강 → `plans/149` W2(4회차 재계수 5→8턴 · G-2 (b)) · W7 내부망 체크리스트 → `plans/149` W6(5회차) · F12 「열#N」 정책 동기화 → `plans/149` W5(closed 정책 재키잉) · W6(F9)은 145에서 교정 · L1(3·4단 엔진 폴백) → `plans/103` 소관
> **회귀 대상 파일**: `src/sql_validation.py` · `src/nodes/{table_selection,schema_analyzer,multi_db_executor,context_resolver,output_generator}.py` · `src/utils/empty_antecedent.py`(신규) · `src/orchestration/{intent_planner,result_aggregator}.py` · `src/routing/semantic_router.py` · 매뉴얼 3 · `config/{db_profiles/itam,knowledge/itam/column_descriptions,knowledge/itam/prompt_template,synonym_seeds/itam}.yaml` · `testdata/itam_bench/closed/knowledge/{guide,prompt_section,descriptions,examples}.yaml` · `testdata/itam_bench/scenarios.closed{,.probe}.yaml` · 오라클·탐침 SQL 17 · 테스트 7 · (v1.3) `scripts/itam_bench/__main__.py` · `tests/test_scripts/test_plan146_w5_oracle_out.py`
> **게이트 답(사용자 2026-10-08)**: *"권고에 맞게 진행하라."* — G-1 (b) · G-2 (c) · G-3 (a) · G-4 (a). W5 (3) `--check-oracle --out`(선택)은 `scripts/itam_bench/__main__.py`를 145가 미커밋 편집 중이라 **보류**(145 통지)
> **요청(사용자 2026-10-08)**: *"「20261007-174622」 폴더에 itam_bench결과가 있다. 이를 분석하여 수정계획 파일을 작성하라."*
> **입력**: 내부망 3회차 run `results/itam_bench/20261007-174622/`(closed · `tier2_intent` · 워커 fabrix / 오케스트레이터 vllm · 시나리오 18 · 턴 19 · SQL 관측 16 · 누출 관문 통과) · 외부망 실행 B1 `--sync` · B2 `--evidence`(`knowledge_evidence/` 생성 · 관문 통과) · C1 `--compare 20261007-152223 20261007-174622` · 코드 재현 2건(§3 F1·F9). LLM 호출 0 · DB 호출 0
> **계획 시작 SHA**: `89773af`(작업 트리에 132·143·145 미커밋 편집이 있다 — 145는 `scripts/itam_bench/redact.py`를 편집 중)
> **관련 계획**: `plans/143`(지식 자산 사이클 — 이 계획이 3회차 사이클 처리분) · `plans/137`(백틱 컬럼 검사 누락 잔여 → **승격**) · `plans/139`(정의 기반 선별) · `plans/132`(ITAM-102·105 소스 선별) · `plans/145`(반출 값 치환 — `redact.py` 동시 편집) · `plans/135`(벤치 · 오라클 형식)
> **관련 결정**: D-003(검증 3중 방어) · D-004(LLM 출력은 정합성 근거 아님) · D-297(DB별 한글 식별자 정책) · D-301(반출 값 0 · 식별자 남김 부기) · D-308(정의 선별 · 사용률 정본은 관측 DB) · D-316(지식 자산 작성) · D-319(132 3회차 전 교정) · D-321(예약 · 145 — D-320에서 재부여) · D-303(회귀)
> **D-번호**: 없음 — G-1이 (b)면 D-297 부기, G-3이 바뀌면 D-319 부기로 처리한다(신규 번호 불필요 권고)

---

### 0. 증거 규칙

- 수치는 반출물(`run.json`·`report.md`·`trace.jsonl`·`schema_catalog.yaml`)과 근거 묶음(`knowledge_evidence/`) 기준이다. 실행 SQL 원문은 반출본(값 가림)만 봤다.
- 「재현」은 외부망 저장소 코드로 같은 입력을 돌려 확인한 것이다. 「추정」은 검증하지 않은 추론이다.
- 내부망 값은 모른다. 서비스↔서버 연결 위치처럼 값이 있어야 판정되는 것은 §5 W5 탐침으로 넘긴다.

### 1. run 개요

| 항목 | 값 | 비고 |
|---|---|---|
| 빌드 | 내부망 커밋 `33053bc`(외부망에 없음) | 자산 지문으로 역추적: `query_guide` `d1408b1d708a` · `query_rules` `fbd6a554e971` · `table_definitions` `0eaf4ac741a5` = **`25a4aa5` 산출물과 일치** |
| 반입 범위 | 132 v1.4(D-319 — 사용률 차단 · 명시 무존 소스 존 게이트 생략 · 「N개월 안에 … 종료」) **들어감** · 132 v1.5(2단 다중 시스템 존 게이트 task 위임) **안 들어감**(run 17:46 · v1.5 회귀 18:22) · 143 v1.7(`89773af` — 회차 이력 주석 이동 · 정의 notes) **안 들어감** | |
| P1 | 초안 `ac5e55a4e73d`(09:17:39) — **2회차와 같은 구버전 초안** | 143 §9.1 체크리스트 ① 미이행 → 프로파일·코드 컬럼 0 · 관계 0 · `code_samples.yaml` 0 · `### 코드값` 블록 없음 |
| 오라클 | 0/0 | 폐쇄망 시나리오 18건이 전부 `observe` — 143 §9.1 「3회차 = 정답 판정 가능 회차」는 **3회차 SQL로 정답 SQL을 만들어 4회차부터 대조**하는 뜻이다(이 계획 W5) |
| 실행 SQL 식별자 | 보임(143 §9.1 ② 효과) | 예외: ITAM-112·116의 `FROM` 테이블 이름이 `<가림>`(F9) |
| 지연 | 중앙값 47.3s · 최대 82.9s(2회차 45.4s · 117.0s) | 143 W0 소관 — 이 계획 범위 밖 |

#### 1.1 2회차 대비(C1)

| 분류 | 2회차 | 3회차 | 원인 |
|---|---|---|---|
| `routing_miss` | 5 | 2 | 132 v1.4: ITAM-103·109(기간 거짓 되물음)·118(명시 무존 소스) 해소. 남은 102·105는 v1.5 미반입 — 4회차 확인 |
| `fabricated` | 1 | 0 | ITAM-114 사용률 추이 → ITAM SQL 없이 안내(D-319 ③) |
| `dialect_silent` | 1 | 0 | 큰따옴표 별칭 소멸(143 K4 s01) |
| SQL 실행 실패 | 0 | 2 | ITAM-108·110 첫 시도 — `tcdmsif80`에 없는 컬럼(F1) |
| 0행 SQL 턴 | — | 7/16 | 서비스 개념 6턴(F3) + 108 |

### 2. 턴별 진단

| 시나리오 | 질문 요지 | 결과 | 진단 | 발견 |
|---|---|---|---|---|
| ITAM-101 | 통합인증 서비스 서버리스트 | `tcdmsif72` 3칸(`그룹경로내용`·`용도내용`·`구성항목설명내용`)에 질문 문구 「통합인증 서비스」 전체 부분 일치 · 0행 | 사용자 문구를 그대로 건다 · 연결 위치 미확정 | F3 |
| ITAM-102 | 통합인증 서비스 서버들 담당 부서 | `zone_select` 되물음(폴스타 3존) | v1.5 미반입 | F11 |
| ITAM-103 | 인터넷뱅킹 서버 중 6개월 안 EOS | `tcdmsif72`↔`tcdmsif79` 세 칸 조인 + `용도내용` 부분 일치 · 0행 | 기간 되물음은 해소 · 서비스 연결이 0행 | F3 |
| ITAM-104 | 서비스별 서버 수 | `tcdmsgt82.어플리케이션명`을 서버 3칸에 부분 일치 LEFT JOIN · 417행(활성 칸 NULL 327) | **어플리케이션명 일부는 서버 자유 기재 칸에 나타난다**(유일한 행 반환 서비스 턴) · 선별 밖 `tcdmsif72` 사용 | F2·F3 |
| ITAM-105 | 서비스 서버 유지보수 계약 만료일 | `zone_select` 되물음 | v1.5 미반입 | F11 |
| ITAM-106 t1 | 통합인증 서비스 서버 목록 | 101과 같은 모양 · 0행 | | F3 |
| ITAM-106 t2 | 그 서버들 지원 종료일 | `tcdmsif79`↔`tcdmsif72` 조건 없는 조인 · **3,748행(전체)** | 앞 턴 조건 미승계 — 앞 턴 0행인데 전체 서버를 답함(침묵 오답) | F6 |
| ITAM-107 | 등록 서버 몇 대 | `COUNT(*)` `tcdmsif72` · 1행 | 활성 거름 없음(코드값 없음 — K6대로) | 오라클 후보 |
| ITAM-108 | 이번 분기 유지보수 계약 끝나는 서버 | ①`tcdmsif80`에 `활성화여부` 선택 → DB 오류 1054 ②재시도 `tcdmsif72`↔`tcdmsif43`(물품 두 칸) · 0행 | ① 검증기가 못 잡음 ② 가이드가 미확정이라 한 연결 키로 갈아탐 · 선별(41·43·72·78)에 `tcdmsif80` 없음 | F1·F2·F5 |
| ITAM-109 | 6개월 안 EOS(지목 없음) | `tcdmsif79` 단독 · HW/SW OR · 148행 | 모양 적정(날짜 리터럴은 실행일 계산값) | 오라클·K2 후보 |
| ITAM-110 | 5년 이상 노후 서버 | ①`tcdmsif80`에 `자산상태구분명` 선택 → DB 오류 1054 ②`tcdmsif80`↔`tcdmsif72`(두 칸) · 2,220행 | ① F1 · 선별(41·46)에 80·72 없음 · 조인은 활성 칸 때문(80만으로 답 가능 — 추정) | F1·F2 |
| ITAM-111 | 취득금액 상위 3대 | `tcdmsif41` · `자산분류구분`·`세부자산분류구분` 코드 리터럴 조건 · 3행 | 가이드는 서버 단위 금액을 `tcdmsif80`으로 안내 · 리터럴 출처 미확정(표본 행이 프롬프트에 실렸다 — 지어냄 단정 불가) | F8 |
| ITAM-112 | OS별 서버 수 | `tcdmsif72` `운영체제타입내용` 집계 · 5행 | 적정 · 반출 SQL `FROM` 가림 | F9 · 오라클·K2 후보 |
| ITAM-113 | 가상 서버로 등록된 서버 목록 | `tcdmsif92` 전체(19,476 추정) · 10,000행 절단 · 사용률 칸 3개 선택 | 「가상 서버」 범위 해석 미확정 · ITAM 사용률 칸을 목록에 실음(D-308 취지와 어긋남) | F7 |
| ITAM-114 | 지난주 CPU 사용률 추이 | SQL 없이 안내 · `db_ids` [] | **정상**(D-319 ③) | — |
| ITAM-115 | 용도에 통합인증 적힌 서버 | `용도내용` 부분 일치 「통합인증」 · 0행 | **「통합인증」은 `tcdmsif72.용도내용`에 없다**(값 관측) | F3 |
| ITAM-116 | 서버 분류 경로별 서버 수 | `그룹경로내용` 집계 · 749행 | 적정 · 경로 길이 2~52자 · `FROM` 가림 | F9 · 오라클 후보 |
| ITAM-117 | 통합인증 업무 할당 스토리지 | ①`tcdmsif51.용도구분명` 등호 + 활성 항진 조건(`= … OR IS NOT NULL`) · 0행 ②`tcdmsif52.업무명` `IN`(등호) · 0행 | 업무 이름을 등호로 건다 · 선별(41·51·57)에 52 없음 | F2·F4 |
| ITAM-118 | 통합인증 업무 스토리지 쓰는 서버 OS | `tcdmsif52.업무명 =` 질문 문구 전체 · `호스트명`↔`서버호스트명` · 0행 | 존 되물음 해소(D-319) · 이름 등호 | F4 |

### 3. 발견

| # | 발견 | 근거 | 처분 |
|---|---|---|---|
| **F1** | **SQL 검증기가 백틱 식별자를 읽지 못해 참조 테이블·컬럼 실존 검사를 통째로 건너뛴다.** `_extract_table_names`가 `` FROM `tcdmsif80` AS t ``에서 빈 집합 · `_extract_alias_map` 빈 사전 · `_validate_columns`의 `(\w+)\.(\w+)`는 `` t.`활성화여부` ``를 못 잡는다. `check_hangul_tokens`(D-297)는 「선별 스키마의 어느 테이블에든 있는 한글 이름」만 보므로(`_schema_identifiers` — 테이블 구분 없음) 선별된 다른 테이블에 있는 `활성화여부`(108 · `tcdmsif72`)·`자산상태구분명`(110 · `tcdmsif41`)이 통과하고, 테이블 이름 `tcdmsif80`은 한글이 아니라 검사 대상이 아니다 | **재현**: `validate_sql("SELECT t.`서버호스트명`, t.`활성화여부` FROM `tcdmsif80` AS t …", {tcdmsif72만})` → 오류 0 · 같은 SQL 무백틱은 컬럼 오류 검출. `plans/137` §1.2·§5가 「백틱 컬럼 검사 누락 — 실측 오답이 나오면 승격」으로 남긴 잔여의 **실측 사례**(108·110 첫 시도 DB 1054) | **W1** |
| **F2** | **선별 테이블과 생성 SQL 테이블이 어긋난다** — 5턴(104·106 t2·108·110·117)이 선별(=프롬프트에 컬럼이 실린) 밖 테이블을 썼다. 컬럼 목록 없이 가이드 문장만 보고 쓴 테이블에서 F1 오류가 났다. 선별 프롬프트 후보 줄은 정의의 `kind`·`manages`·`key_columns`·`related`만 싣고 `notes`(예: `tcdmsif80` 「서버 단위 질문은 이 테이블 하나로」)와 K1 가이드는 싣지 않는다 | trace `schema_context.presented_tables` ↔ `sql_analysis.tables` · `src/nodes/table_selection.py` `_candidate_line` · `src/prompts/table_selection.py` | **W1(G-1)** · **W2(G-2)** |
| **F3** | **서비스↔서버 연결 위치가 여전히 미확정이다.** 질문 문구를 서버 원장 자유 기재 3칸·용도·스토리지 업무명에 그대로 건 6턴이 모두 0행 · `용도내용`에는 「통합인증」 자체가 없다(115) · 반면 `tcdmsgt82.어플리케이션명`을 서버 3칸에 부분 일치로 댄 집계는 행을 돌려준다(104) → **질문 문구 → 어플리케이션명 → 서버 칸**의 두 단계 해석이 후보(추정) | 101·103·106·115·117·118 vs 104 · 정의 notes(`tcdmsgt82`·`tcdmsif52`) | **W4**(K1·K4) · **W5**(탐침) |
| **F4** | 업무·용도 이름을 **등호·IN·질문 문구 전체**로 건다(117·118) · 활성 칸에 항진 조건(117) | 실행 SQL | **W4**(K4) |
| **F5** | `tcdmsif72`↔`tcdmsif43`을 물품 두 칸(+그룹회사코드)으로 이은 조인 0행(108 재시도 · 날짜 조건 동반) — 2회차 `tcdmsif72`↔`tcdmsif41` 0행에 이어 서버 원장의 물품 키 연결은 두 회차 모두 0행. 매핑 테이블 `tcdmsif78`(물품 두 칸 보유) 경유가 후보(추정) | 108 · 143 §9 F2 | **W4**(K1 g05) · **W5**(탐침) |
| **F6** | **멀티턴 지시어 승계 실패** — 앞 턴이 0행인데 「그 서버들」을 전체 서버로 풀어 3,748행을 답했다(침묵 오답) | 106 t2 | **W3(G-3)** |
| **F7** | 「가상 서버로 등록된 서버」를 `tcdmsif92` 전체로 답하고(10,000행 절단) 사용률 칸을 실었다 · 서버 원장 쪽 판별 칸(`서버유형구분`·`tcdmsif80.가상화서버매핑여부`)은 코드값이 없어 못 씀 | 113 · P1 코드값 0 | **W4**(K4) · W7(P1) |
| **F8** | 서버 단위 금액 질문을 자산 원장 `tcdmsif41` + 분류 코드 리터럴로 답함 — 가이드(g03)는 `tcdmsif80` · 선별에 80 없음 | 111 | W2 · **W5**(오라클로 판정) |
| **F9** | **반출 가림이 `FROM` 테이블 이름을 가린다** — 별칭 없는 테이블 뒤(같은 조각)에 정책 밖 카탈로그 컬럼(`GROUP BY` 등)이 오면 `` FROM `<가림>` `` | **재현**(현 작업 트리): `redact_sql("SELECT `운영체제타입내용` AS os_type, COUNT(*) AS c FROM `tcdmsif72` GROUP BY `운영체제타입내용`", catalog_columns=[…])` → `` FROM `<가림>` `` · 별칭 `AS t`가 있어도 같음 · 컬럼이 앞에만 있으면 남음. 145(치환)가 들어가면 **가짜 테이블 이름**이 나가 더 오도한다 | **W6**(145 통지) |
| **F10** | P1 재실행 2회 연속 미이행 — 코드값·관계 근거 0이 K6 활성 규칙·여부 코드 질문(107·113)·K8 `code` 슬롯을 계속 막는다 | `run.json` `assets.p1` | **W7**(내부망) |
| F11 | ITAM-102·105 `routing_miss` | v1.5 미반입(§1) — 코드 작업 없음 | W7에서 4회차 확인 |
| F12 | 리포트 §8 「분류 필요 컬럼」 `CPU`·`IP`(원 컬럼 아님 — 식 해석 조각 추정) · 미분류 결과 열 39개(`열#N`) — 결과 열 단위 분석이 막힌다 | `report.md` §8 · trace `result.columns` | 145 통지(반출 정책 소관) |

### 4. 결정 충돌 검토(작업 전 필수)

| 결정 | 이 계획과의 관계 | 충돌 |
|---|---|---|
| D-003 | W1은 「참조 테이블·컬럼 존재」 검증을 MariaDB 백틱에도 적용 — 결정 취지 이행 | 없음 |
| D-297 | 허용 DB 한글 식별자 검사는 그대로 · W1은 테이블 단위 실존 검사를 더한다 | 없음(137 잔여 승격 · 부기) |
| D-301 부기 | 식별자 남김 원칙 — F9는 그 원칙의 결함 교정 | 없음 |
| D-308 | 선별 사다리(정의 → LLM 상한 8)는 유지 · W2 (a)는 후보 줄에 칸 하나 추가 | G-2 답에 따라 부기 |
| D-316 | 자산은 스킬로 작성 · 검증 통과분만 빌드 | 없음 |
| D-319 | F6 교정은 132 소관 밖(멀티턴) · 102·105는 v1.5로 이미 교정 | 없음 |
| D-321(예약 · 구 D-320) | F9는 145가 고치는 파일과 같다 | **조율 필요** — W6을 145 범위로 넘긴다 |

### 5. 작업 단위

#### W1 — SQL 검증기 MariaDB 백틱 식별자 인식(본체 · F1 · F2 일부)

| 단계 | 내용 | 확인 |
|---|---|---|
| 1 | `src/sql_validation.py` `_extract_table_names`·`_extract_alias_map`·`_validate_columns`가 백틱 인용 식별자(`` `t` ``·`` `tcdmsif80` ``·`` t.`컬럼` ``·`` `t`.`컬럼` ``)를 읽게 한다. 엔진 분기는 기존 `_uses_backtick_quotes(engine)`를 쓴다 — PostgreSQL·DB2 경로는 바이트 동일 | 폴스타 `validate_sql` 기존 테스트 무변경 통과 |
| 2 | 별칭 없는 단일 테이블의 비한정 백틱 컬럼(`` SELECT `운영체제타입내용` FROM `tcdmsif72` ``)도 그 테이블 컬럼과 대조한다(현재는 무백틱도 비한정 컬럼 검사 없음 — 범위는 MariaDB 백틱으로 한정) | |
| 3 | **G-1** 선별 밖 테이블 처리 — 권고 (b): 실존 판정을 선별 스키마가 아니라 **조회 대상 전체 카탈로그**로 하고, 없는 컬럼이면 오류 문구에 **그 테이블의 실제 컬럼 이름 목록**(상한 있음 · 이름만)을 싣는다 → 재생성 1회에 고쳐지게 한다. 착수 첫 단계에서 검증 시점에 전체 카탈로그(선별 전 `schema_info`)가 상태에 남아 있는지 실측한다 — 단일(`query_validator`)·멀티(`multi_db_executor`) **양쪽 대칭** | 108·110 모양 입력이 실행 전 오류 + 컬럼 목록 · 104·117 모양(선별 밖 · 실존 컬럼)은 통과 |
| 4 | 테스트 `tests/test_nodes/test_plan146_backtick_validation.py`(신규) — 백틱 테이블·별칭·한정/비한정 컬럼 · 선별 밖 허용 테이블 · 조회 대상 밖 테이블 · PostgreSQL 비영향 · 한글 식별자 검사(D-297)와 중복 오류 없음 | |

#### W2 — 선별 보강(F2 · F8 · G-2)

권고 (c): **W1-(b) 먼저 넣고 4회차에서 「선별 밖 테이블 사용 턴」을 다시 센다.** 그래도 남으면 (a)·(b) 중 고른다.

| 안 | 내용 | 비용 |
|---|---|---|
| (a) | 선별 후보 줄에 정의 `notes`를 싣는다(`_candidate_line`) | 108테이블 후보 목록이 길어진다(선별 프롬프트는 앞부분 KV 캐시 재사용 구조라 지연 영향은 첫 질문 위주 — 추정) · 폴스타는 정의 없음 → 비영향 |
| (b) | 데이터만 — `tcdmsif80` 정의 `manages`에 질문 유형(노후·경과 연수·취득금액·유지보수 계약 만료)을 드러낸다(`testdata/itam_bench/closed/table_definitions.yaml` → 빌드) | 코드 0 · 정의는 사람 검토 대상(D-308) |
| (c) | W1-(b)만 | 0 |

#### W3 — 멀티턴 지시어 승계(F6 · G-3)

1. **조사**: 106 t2 경로(2단 `intent_planner` → `agent_orchestrator` → ITAM task)에서 앞 턴 SQL 조건·결과 행 수가 다음 턴 계획에 들어가는지 로그·상태로 확정한다(`context_resolver` 승계 신호 · 체크포인터 델타 병합 — Known Mistakes 「멀티턴 / 상태」). 끊긴 지점을 먼저 확정한 뒤 교정한다.
2. **교정(G-3 권고 (a))**: 지시어가 앞 턴 대상을 가리키고 앞 턴이 **0행**이면 전체로 넓히지 않는다 — 앞 턴 조건을 승계해 다시 0행이면 「앞 질문에서 찾은 서버가 없다」를 그대로 알린다. 앞 턴이 행을 돌려줬으면 종전대로.
3. 테스트: 목 LLM 2턴(앞 턴 0행 · 행 있음) · 폴스타 경로 대칭 확인(D-066).

#### W4 — 지식 자산(스킬 `/itam-knowledge results/itam_bench/20261007-174622` · F3·F4·F5·F7)

근거 run을 `20261007-174622`로 올려 다시 쓴다. 회차·run 서술은 `text`가 아니라 YAML 주석에 둔다(143 v1.7 ⑨).

| 자산 | 항목 | 고칠 내용(값 없이) |
|---|---|---|
| K1 | g03-server | `tcdmsif80`에는 `활성화여부`·`자산상태구분명`이 **없다**(이름 칸 없이 `자산상태구분`만) — 이 테이블만으로 답하는 질문에 서버 원장을 조인하지 않는다 |
| K1 | g05-asset-maintenance | 서버 원장의 물품 두 칸으로 자산 원장·유지보수 계약에 이은 조인은 조건 유무와 무관하게 0행이 관측됐다 — 서버 단위 유지보수 계약은 `tcdmsif80`의 계약 칸으로 답한다 · 물품 키 연결은 매핑 `tcdmsif78` 경유가 후보(추정 · W5 탐침으로 확정) |
| K1 | g06-service | 질문의 서비스 문구를 서버 칸에 그대로 건 조회는 0행이다 · 서비스 이름은 먼저 어플리케이션 목록 `tcdmsgt82`의 `어플리케이션명`에서 핵심어 부분 일치로 찾고, 찾은 이름을 서버 자유 기재 칸에 부분 일치로 댄다(추정 — 104 모양만 행 반환) · `tcdmsif72.용도내용`은 서비스 단서로 쓰지 않는다(관측 0행) |
| K4 | 이름 일치 규칙(신규) | 업무명·용도·어플리케이션명 같은 이름 칸은 등호·`IN`·질문 문구 전체가 아니라 **핵심어 부분 일치**로 건다 · 「서비스」「업무」「시스템」「스토리지」 같은 일반어는 검색어에서 뺀다 |
| K4 | 활성 조건(보강) | 코드값 블록이 없으면 활성 칸 조건을 쓰지 않는다 — 항진 조건(같은 칸에 등호 OR NULL 아님)도 쓰지 않는다 |
| K4 | 사용률 칸(보강) | 목록·현황 질문의 선택 목록에 ITAM 사용률 칸을 싣지 않는다(정본은 관측 DB — D-308) |
| K3 | 설명 | `tcdmsif52.업무명`(부분 일치 · 현행 할당) · `tcdmsif80.자산상태구분`(이름 칸 없음 — 코드값 블록이 없으면 그대로 보여 준다) · `tcdmsgt82.어플리케이션명`(서비스 이름 해석의 1단계) |
| K2 | **G-4** | 권고 (a): 관측 성공 SQL 중 활성 의미와 무관하거나 집계 축이 확정된 3건 — 109(EOS 기간 · `tcdmsif79` 단독) · 112(OS별 집계) · 116(그룹 경로별 집계). 모의 DB(3308) **실행 검증까지**(`--static-only` 아님) |
| K8 | 보류 | 서비스 연결 미확정 · 코드값 0. EOS 기간 템플릿(`date_range` · `yyyymmdd`)은 4회차 109 오라클 통과 뒤 |

절차: 143 §4.2.1 B3(모의 DB 행 생성 — 3회차 카탈로그) → B4(스킬) → B5(`--validate-knowledge` 실행 검증) → B6 빌드(재기저 여부는 143 §9.1 ⑤ 조건대로 사용자 판단) → B7 `prompt_render_diff --ci` 차이 0.

#### W5 — 4회차 정답 판정 준비(시나리오 · 오라클 · 탐침)

**(1) 오라클 전환 후보** — 정답 SQL은 `testdata/scenarios/oracles/ITAM-1NN.mariadb.sql`(값 리터럴 없음 · 날짜는 `:today` 자리표). 내부망 `--check-oracle`에서 0행·오류가 나면 그 시나리오는 `observe`로 되돌린다.

| 시나리오 | 정답 SQL 요지 | 비교 | 비고 |
|---|---|---|---|
| ITAM-107 | `tcdmsif72` 전체 건수 | `count` | 활성 거름 없음 — P1 코드값 승인 뒤 개정 |
| ITAM-109 | `tcdmsif79` HW·SW 지원 종료일 중 하나가 오늘~6개월 | `keyset`(`서버호스트명`) | |
| ITAM-112 | `tcdmsif72` `운영체제타입내용`별 건수 | `keyset`(`운영체제타입내용` — 정책 등급 general 확인 뒤) | |
| ITAM-116 | `tcdmsif72` `그룹경로내용`별 건수 | `count`(경로 값에 업무 이름이 섞일 수 있어 키로 쓰지 않는다) | |
| ITAM-108 | `tcdmsif80` `유지보수계약종료년월일`이 이번 분기 | `keyset`(`서버호스트명`) | `tcdmsif80` 마트 가설 — 탐침 P07과 함께 확인 |
| ITAM-110 | `tcdmsif80` 취득일 기준 5년 이상(경계 포함) | `keyset` | **G-4**: 기준 칸 `취득년월일` vs `경과년수` |
| ITAM-111 | `tcdmsif80` `취득금액` 상위 3 | `keyset` | 41(자산 원장) 해석과 갈림 — G-4 |

서비스 개념 시나리오(101~106·115·117·118)와 113은 `observe` 유지 — W5 (2) 탐침 결과로 5회차 오라클을 쓴다.

**(2) 연결 위치 탐침** — `testdata/itam_bench/scenarios.closed.probe.yaml`(신규 · `--run` 대상 아님) + 탐침 SQL을 내부망에서 `python -m scripts.itam_bench --check-oracle --env closed --scenarios testdata/itam_bench/scenarios.closed.probe.yaml`로만 돌린다(LLM 0 · 출력은 행 수만). 행 수가 정보가 되도록 `COUNT(*)` 한 행이 아니라 행을 돌려주는 모양(`SELECT DISTINCT 키 … LIMIT N`)으로 쓴다.

| # | 묻는 것 | 대상 |
|---|---|---|
| P01 | 서비스 핵심어가 어플리케이션명에 있나 | `tcdmsgt82.어플리케이션명` 부분 일치 |
| P02~P04 | 서비스 핵심어가 서버 자유 기재 칸·스토리지 업무명에 있나 | `tcdmsif72.그룹경로내용`·`구성항목설명내용` · `tcdmsif52.업무명` 부분 일치 |
| P05 | P01에서 찾은 어플리케이션명이 서버 칸에 나타나나 | 104 모양(두 단계) |
| P06 | 서버 원장 물품 키 ↔ 자산 원장 겹침 | `tcdmsif72`↔`tcdmsif41`(조건 없음) |
| P07 | 매핑 경유 | `tcdmsif72`↔`tcdmsif78`↔`tcdmsif41` · `tcdmsif80`↔`tcdmsif72` 행 배수 |
| P08~P09 | 스토리지·VM ↔ 서버 원장 | `tcdmsif52.호스트명`·`tcdmsif92.서버호스트명` ↔ `tcdmsif72.서버호스트명` |

탐침 핵심어는 시나리오 원문에 이미 있는 말(「통합인증」·「인터넷뱅킹」)만 쓴다. 

**(3) 결과 회수** — **완료(v1.3)**: `--check-oracle --out <run 디렉터리>` → `<run>/oracle_check/oracle_check.yaml` + 하위 전용 `leak_check.json`(run 루트 산출물 무손상 · §10.8). 원안: `--check-oracle`은 화면에만 찍는다. 반출을 쉽게 하려면 `--out <run 디렉터리>`로 `oracle_check.yaml`(오라클 ID · 판정 · 행 수 · 시간 — 값 0)을 남기고 누출 관문 대상에 넣는다(작은 CLI 변경 · 선택 — 안 하면 사용자가 화면 출력을 옮긴다).

#### W6 — 반출 가림의 `FROM` 테이블 이름(F9 · 145 통지)

`redact.py` `_identifier_judge`에서 `FROM`·`JOIN` 바로 뒤 테이블 자리 식별자는 값 자리로 보지 않는다. **`plans/145`가 같은 파일을 미커밋 편집 중이라 이 계획에서 직접 고치지 않고 145 범위에 넣도록 통지한다**(재현 입력 3종 · 145가 치환을 켜면 가짜 테이블 이름이 나가는 위험 포함). F12(리포트 `CPU`·`IP`·`열#N`)도 함께 통지한다.

#### W7 — 내부망 4회차 체크리스트(사용자)

- [ ] 반입 커밋에 132 v1.5 · 143 v1.7(`89773af`) · 이 계획 W1·W3·W4·W5가 들어갔는지 확인 — 벤치 리포트 자산 지문이 외부망 빌드와 같은지 대조
- [ ] **「DB 구조」 탭 P1 재실행·승인**(2회 연속 미이행 · 리포트 첫머리 「구버전 빌드」 경고가 사라져야 한다) · 2c에서 `활성화여부`·`서버유형구분`·`가상화서버매핑여부` 계열 코드값 승인
- [ ] 탐침 `--check-oracle --scenarios testdata/itam_bench/scenarios.closed.probe.yaml --out results/itam_bench/<4회차 run>` → `<run>/oracle_check/oracle_check.yaml`(ID·판정 범주·행 수·ms — 값 0) 반출(W5 (2)·(3)) · `--out` 상대 경로는 그대로 쓰이므로 `results/itam_bench/…`까지 적는다
- [ ] 오라클 `--check-oracle --out results/itam_bench/<4회차 run>`(기본 시나리오) → 0행·오류 시나리오 통보 — 탐침과 같은 `--out`이면 나중 실행이 `oracle_check.yaml`을 교체하므로 **탐침·오라클은 서로 다른 디렉터리**에 남긴다(예: 탐침은 별도 `results/itam_bench/<run>-probe`)
- [ ] 4회차 run(`--env closed`) → 반출
- [ ] **145의 오라클 키 누출 교정이 반입 커밋에 함께 들어갔는지** 확인(v45 H1 — 145 미커밋 코드에서 교정됨. 빠지면 오라클 시나리오 반출이 관문에서 막히거나 키가 샌다)
- [ ] 탐침 판독: 0행이면 `--check-oracle` 종료 코드 1이어도 **정상 정보**다(그 연결 위치에 없음). P06·P07A(조건 없는 대형 조인)는 시간 초과 가능 — 초과면 「시간 초과」로 옮긴다
- [ ] 오라클 판정 「보류 — key 열을 찾지 못했다」는 시스템 결과에 키 칸이 없다는 뜻이므로 **오답 신호**로 읽는다(`host_key` 별칭 `sevrHostName`·`server_hostname`·`server_host` 외 이름)
- [ ] 리포트에서 K2 예시가 덮는 시나리오(109·112·116)는 「K2 덮음」으로 구분해 읽는다 — 정답이 예시 복사일 수 있다
- [ ] 빌드 재기저(1회차 `20261006-152938` → 3회차) 여부 결정(143 §9.1 ⑤ — 이번에는 재기저하지 않았다)
- [ ] 오라클 상세 값은 145 `judge.sanitize_detail`로 general 아닌 칸(등급 미상 포함)의 `oracle_top`·`value_diffs`·비교 스칼라가 run마다 새 가짜 값으로 반출된다 — 107(value)·111(argmax)은 반출물에서 **판정 결과만** 믿는다(판정은 원값으로 계산). 탐침은 행 수만이라 영향 없음
- [ ] 3회차 trace에서 109·111·106 t2 결과 열이 `열#N`으로 가려져 있었다 — 4회차에서 「key 열 못 찾음」 보류가 나면 `host_key` 별칭 누락 여부를 먼저 본다
- [ ] 145 반입 확인 ①: 오라클 키 열을 해석하지 못하면 키를 싣지 않는다(fail-closed · `keys_allowed=False`)
- [ ] 145 반입 확인 ②: `run.json`에 `registry_fallback` 칸이 **없다**(있으면 레지스트리 로드 실패로 per_db 태그를 전부 치환한 run)
- [ ] 145 반입 확인 ③: per_db 태그는 등록 DB id 화이트리스트만 원문으로 남는다
- [ ] 3회차 반출 실물 대조 포인트(4회차 반출물에서 확인): `substitutions.yaml` 존재 · `report.md` 첫머리 「형식 보존 치환값 — 원값 아님」 · `leak_check.json` 통과 · OID나 점 4덩이 이상 버전 문자열이 산출물에 있으면 관문이 run을 막는다(닫힌 쪽 — 막히면 위치만 보고 해당 칸을 확인)

### 6. 게이트 — 답(2026-10-08 · 권고안 전부)

| # | 질문 | 선택지와 결과 | 권고 |
|---|---|---|---|
| G-1 | SQL이 선별(프롬프트에 컬럼이 실린) 밖 테이블을 쓰면? | (a) 「존재하지 않는 테이블」 오류로 재생성 — 폴스타와 같은 동작이지만 104·117처럼 맞게 쓴 턴도 재시도로 간다 · (b) 조회 대상 전체로 실존만 검사하고, 없는 컬럼이면 그 테이블 실제 컬럼 목록을 오류에 실어 재생성 · (c) 검사 없음(현행) | **(b)** |
| G-2 | 선별 보강 | (a) 후보 줄에 정의 `notes` · (b) `tcdmsif80` 정의 문구만 보강 · (c) W1-(b) 뒤 4회차 관찰 | **(c)** |
| G-3 | 앞 턴이 0건일 때 「그 서버들」 | (a) 앞 턴 조건 승계 — 다시 0건이면 「앞에서 찾은 서버 없음」 안내 · (b) 조회 없이 「앞 결과 0건」 안내로 종료 | **(a)** |
| G-4 | 정답(K2·오라클) 범위 | (a) K2 3건(109·112·116) 지금 · 오라클 7건은 `--check-oracle` 확인 뒤 확정 · 110은 `취득년월일` · 111은 `tcdmsif80` 기준 · (b) K2는 4회차 오라클 통과 뒤 | **(a)** |

### 7. 검증

- W1·W3: 신규 테스트 + 계획 끝 1회 `python scripts/regress.py --base 89773af --files <바꾼 파일…>`(D-303) · 폴스타 비영향은 기존 `validate_sql` 테스트와 `prompt_render_diff --ci`로 확인 · 실 LLM은 목 우선(MLX는 W3 대표 2턴 1회만 — D-240).
- W4: `--validate-knowledge` 실행 검증(모의 DB 3308) 오류 0 · 빌드 「검증 오류 0」 · `git status --short results testdata/itam_closed_sim/generated` 빈 출력.
- W5: 오라클·탐침 SQL을 모의 DB에서 `--check-oracle`(로컬)로 구문·실행 확인 → 시나리오 린트(`--dry-run --env closed`) 통과.
- 매뉴얼(D-255): 대상 아님(검증기·벤치·내부 지식) — W3이 사용자에게 보이는 안내 문구를 더하면 그때 판단한다.

### 8. 위험

| 위험 | 대응 |
|---|---|
| W1이 MariaDB 생성 SQL을 새로 거절해 재시도·지연이 늘 수 있다 | G-1 (b)는 실존하는 선별 밖 테이블을 통과시킨다 · 4회차 `retries` 평균 대조(3회차 0.12) |
| 오라클 정답 SQL이 우리 해석일 뿐이다(80 마트 가설 · 활성 거름 없음) | `--check-oracle` 0행·오류면 `observe` 복귀 · P1 코드값 뒤 개정 |
| 탐침 핵심어가 업무 이름이다 | 시나리오 원문에 이미 있는 말만 · 출력은 행 수만(D-301) |
| 145와 `redact.py` 동시 편집 | W6은 145에 넘긴다 |

### 9. 버전 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1.0 | 2026-10-08 | 신규 — 3회차 run `20261007-174622` 분석(턴 19 진단 · 발견 F1~F12 · 재현 2건) · W1~W7 · 게이트 G-1~G-4 |
| v1.1 | 2026-10-08 | 게이트 답 — 권고안 전부(G-1 (b) · G-2 (c) · G-3 (a) · G-4 (a)) · W5 (3) 보류(145 파일 충돌) · 착수 |
| v1.2 | 2026-10-08 | 착지 — W1(백틱 카탈로그 실존 검사 · D-297 부기) · W3(0행 앞 턴 승계 + 안내 · 행 숨김 기각 · D-055 부기) · W4(K1·K3·K4 · K2 3건 · 모의 DB 실행 검증 81/81) · W5 (1)(2)(오라클 7 · 탐침 10) · 계획 끝 회귀 rc=0 · §10 착지 보고 · 상태 WIP(파일명 태그 유지) |
| v1.3 | 2026-10-08 | W5 (3) — `--check-oracle --out` → `<run>/oracle_check/oracle_check.yaml`(값 0 · 누출 관문 · run 루트 무손상) · W7 회수 문구·145 반입 확인 3·반출 실물 대조 포인트 · 회귀 rc=0 |
| v1.4 | 2026-10-08 | 완료 — 4회차 실행 확인 · 잔여 이관(W2·W7·F12 → `plans/149` W2·W6·W5 · L1 → 103) · 파일명 태그 제거(`146-itam-run3-results-correction.md`) |

### 10. 착지 보고(v1.2 · 2026-10-08)

#### 10.1 W1 — 백틱 카탈로그 실존 검사(G-1 (b) · D-297 부기)

- `src/sql_validation.py` `check_catalog_references` 신설 — **백틱 엔진(`_uses_backtick_quotes`)만** `validate_sql` 5·6단계에서 호출. 공용 `_extract_table_names`·`_extract_alias_map`·`_validate_columns`는 그대로라 PostgreSQL·DB2 판정은 비트 동일(`prompt_render_diff --ci` 차이 0).
- 실존 판정 기준은 선별 스키마가 아니라 상태에 실은 조회 대상 전체 카탈로그(`_catalog_columns`). 없는 컬럼이면 그 테이블 실제 컬럼 이름 목록(상한 80)을 오류에 싣는다. 선별 밖이지만 실존하는 테이블·컬럼은 통과. 조회 대상 밖 테이블은 「존재하지 않는 테이블 참조」.
- 리터럴·주석을 걷은 본문으로 스캔 · 암시 별칭 · 파생 원천 · 비한정 한글 컬럼 · 다중 테이블 합집합 대조 · 별칭 붙은 콤마 조인(교정 2).
- 카탈로그 부착: 단일 `schema_analyzer`(정의 선별 AND 백틱 엔진) · 멀티 `multi_db_executor._analyze_schema` — 검사 호출도 `_validate_sql_simple`에 대칭. 헬퍼 `table_selection.catalog_columns`(선별 로직 무변경 — G-2 (c)).
- 테스트 `tests/test_nodes/test_plan146_backtick_validation.py` 129 통과 · xfail 1(L1).

#### 10.2 W3 — 0행 앞 턴 승계(G-3 (a) · D-055 부기)

- **끊긴 지점**(체크포인터 붙인 2턴 재현으로 확정): ① 2단 경로는 `generated_sql`을 top-level로 올리지 않아 `context_resolver`의 `previous_sql`이 빈 문자열 ② 앞 턴이 0행이라 결과 요약·엔티티도 빔 → 2턴 계획 맥락(`intent_planner._build_context_block`)에 앞 턴 질의·조건이 전혀 닿지 않음 → 조건 없는 조인(3회차 3,748행 모양). 3·4단은 이전 SQL은 붙지만 결정적 가드 없음.
- **교정**: `src/utils/empty_antecedent.py`(신규). 0행으로 끝난 턴의 원 질의·실행 SQL을 `conversation_context["zero_row_turn"]`에 기록(2단 `result_aggregator` · 3·4단 `output_generator` · 3단 존 턴 `semantic_router`) → 다음 턴에 지시어가 있고 앞 턴이 서버를 직접 지목하지 않았으면 `context_resolver`가 `empty_antecedent`를 싣고 계획 맥락에 앞 질의·SQL을 넣는다 → 실행 뒤 결정적 판정: 다시 0행이면 「앞 질문(「…」)에서 찾은 서버가 없습니다 — 그 조건을 이어 다시 조회했지만 해당하는 서버가 없습니다」 뒤에 붙임 · 앞 조건 값을 하나도 잇지 않고 넓혔으면 행은 그대로 두고 「… 앞에서 찾은 서버가 아니라 전체 대상일 수 있습니다」를 앞에 붙임.
- **기각한 설계**: 넓힌 결과 행을 숨기는 가드(구현자가 범위 밖으로 넣음) — 라우트 폴백으로 SSE `row_count`·CSV에 행이 새고(H-1), 비교 값 추출 오탐으로 맞는 행을 숨김(H-2) → 행 유지 + 안내로 전환(D-055 부기에 기록). 되돌리는 비용: 안내 문구 2종과 판정 함수 1개 — 숨김으로 바꾸려면 라우트 3곳 대칭 처리가 필요.
- 지시어 판정은 단어 경계 요구(「로그서버」 배제) · 「전체/모든」이 앞에 오면 배제(교정 2). 비교 값은 LIKE 우선(`CONCAT`·`||` 포함) → 없으면 `=`·`<>`·`IN` · 날짜·숫자·형식 문자열 제외.
- MLX(두 평면 mlx 확인 · 1회 · 계획 분해 프롬프트만 전·후): 전 「직전 조회 결과의 서버들에 대해 지원 종료일 조회」(조건 없음) → 후 앞 턴 조건 재조회 + 지원 종료일 조회. 전체 앱 2턴은 미실행(MCP·Redis 미기동 · 로컬 ITAM 2테이블).
- 매뉴얼(D-255): 사용자 매뉴얼 U-34 주의 문구 추가 · `build` · `tests/test_manual` 555 통과.
- 테스트 `tests/test_orchestration/test_plan146_demonstrative_zero_rows.py` 55 통과.

#### 10.3 W4 — 지식 자산(K8 보류)

- 원천 `testdata/itam_bench/closed/knowledge/` — `guide.yaml`(g03 · g05 · g06) · `prompt_section.yaml`(s04 · s07 · s09 · 신규 s10 이름 일치 규칙) · `descriptions.yaml` · **신규 `examples.yaml`(K2 3건: `k2-eos-6months` · `k2-server-count-by-os` · `k2-server-count-by-group-path`)**.
- **모의 DB 실행 검증 수행**: 3회차 카탈로그로 모의 DB(3308 · 108테이블 · 합성 2,160행) → 별도 포트 MCP(127.0.0.1:9199 · 프로세스 환경변수만 — 공유 9099·`mcp_server/.env` 무변경) → `--validate-knowledge` 81/81 통과 · db_unverified 0. 행 값은 합성이라 실행 가능 여부만 증명한다.
- 빌드 기준은 1회차 `20261006-152938` 유지(재기저 안 함 — W7 사용자 결정) · `prompt_render_diff --ci` 차이 0 · `test_plan140_w3_build_assets.py`는 `KnowledgeDeps(client_factory=_no_db, sql_checker=…)`로 DB 무접속 고정.

#### 10.4 W5 (1)(2) — 오라클 7 · 탐침 10

| 오라클 | 비교 | 비고 |
|---|---|---|
| ITAM-107 | `value` + `count_rows_ok` | `count`는 COUNT 1행 정답을 「1 대 N」으로 떨어뜨려 이탈(ITAM-19 정본과 같은 모양) |
| ITAM-108 | `keyset`(`서버호스트명`) | `tcdmsif80` 이번 분기 유지보수 종료 |
| ITAM-109 | `keyset` | `tcdmsif79` HW·SW 지원 종료 6개월 |
| ITAM-110 | `keyset` | `취득년월일` 5년 이상(경계 포함) |
| ITAM-111 | `argmax` | `tcdmsif80` `취득금액` 상위 3 — 3위 동점 흡수(샌드박스 정본 ITAM-13 모양)로 이탈 |
| ITAM-112 | `keyset`(`운영체제타입내용`) | |
| ITAM-116 | `count` | 경로 값은 키로 쓰지 않음 |

- `host_key` 별칭에 `sevrHostName`(로더 린트의 general 이름 요건)·`server_hostname`·`server_host` 추가. 폐쇄망 `서버호스트명`은 unclassified라 키 값은 건수로만 남는다(`keys_recorded: false` — 샌드박스와 같은 방식).
- 탐침 `testdata/itam_bench/scenarios.closed.probe.yaml` + `ITAM-146-P01`~`P06`·`P07A`·`P07B`·`P08`·`P09`(10건 — P07은 두 질문이라 분할). 핵심어는 「통합인증」만(행 수로 핵심어를 가르려고).
- 반출 영향(145 `judge.sanitize_detail`): oracle이 된 7건의 상세 값 중 general 아닌 칸은 가짜 값으로 반출 — 107·111은 판정 결과만 신뢰(판정은 원값) · 탐침은 행 수만이라 무영향.
- 모의 DB `--check-oracle` 기본·탐침 DB 오류 0 · `--dry-run --env closed` 오라클 7턴 · 관측 12턴 통과.

#### 10.5 검증

- 계획 끝 회귀 1회: `python scripts/regress.py --base 89773af --files <회귀 대상 파일 29>` → rc=0 · 본체 통과 4,708 · 실패 0 · 에러 0 · 건너뜀 11 · arch·overfit 통과 · ruff·mypy 이번 diff 줄 위반 0 · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행`(산출물 `logs/regress/20261008-110335-16181`).
- verifier: W1(Critical 0 · H1 (a)~(d)·M1·L2 교정 1 · H2 교정 2) · W3(H-1·H-2 → 숨김 기각 · M-1·M-2·M-4·L-3 교정 1 · M-R1·R-L2 교정 2) · W4·W5(Critical 0 · M1 별칭·L1 g05 과장 교정 · H1은 145 소관 → W7).

#### 10.6 팀 리드 판단(검토 요청 사항)

| 사항 | 판단 · 근거 |
|---|---|
| W1 비한정 컬럼 대조를 별칭 있는 단일·다중 테이블(합집합)까지 확대(지시 밖) | **수용** — verifier가 실제 ITAM SQL 43개 모양으로 재확인해 오탐은 H2(콤마 조인) 하나였고 교정 2로 해소. 되돌리기는 `check_catalog_references`의 비한정 분기를 단일 무별칭으로 좁히면 된다 |
| W1 체크포인트에 `_catalog_columns` 동반 저장(수십 KB) | **수용** — 백틱 엔진·정의 선별일 때만 싣는다(폴스타 0). 크기가 문제되면 노드 지역 조회로 옮긴다 |
| W1 L2: 카탈로그 부착 조건을 `active_db_engine`(백틱)으로 — 3단 ITAM은 미부착 | L1(3·4단 엔진 폴백)과 같은 묶음 잔여(103 소관) |
| W3 넓힌 결과 행 숨김 → 행 유지 + 앞 안내 | G-3 (a)의 「넓히지 않음」은 **계획 단계**에서 앞 턴 조건을 승계해 지킨다(계획 맥락에 앞 질의·SQL 주입). 실행이 그래도 넓혔을 때 행을 숨기면 라우트 폴백 누출(H-1)·오탐 숨김(H-2)이 나서, 침묵 대신 사용자에게 넓혔음을 명시하는 쪽을 택했다 — D-055 부기 |
| W3 `src/routing/semantic_router.py` +6줄(지시 밖) | 3단 존 턴도 0행 기록을 잇게 하는 대칭 배선 — 수용 · 회귀 대상에 포함 |
| W4 K3 `tcdmsif72.용도내용` 재서술(지시 밖) | **수용** — 새 g06(서비스 단서로 쓰지 않음)과의 모순 해소 |
| W4 빌드 기준 run | 3회차로 빌드하면 재기저(유사어 +258줄 · 스키마 99테이블 변경)가 일어나 143 §9 기록대로 1회차 `20261006-152938` + 지식 오버레이로 빌드. 145가 기준 run을 바꾸면 그 결정을 따른다(W7 재기저 결정) |
| `test_plan140_w3_build_assets.py` 실패 귀속 | K2 3건 추가 → 정적 전용 재빌드에서 `asset_sql_checker` 없이 K2가 거절돼 `query_examples`만 달랐다. deps에 `asset_sql_checker`를 넘기는 최소 수정(145 동의) · 읽는 1회차 run에 `substitutions.yaml` 없음 실측 |
| `test_itam_bench_catalog.py`·`test_itam_bench_closed_kit.py` 3건 | 오라클·탐침 추가로 기대값 변경 — 정본 파일 수는 증가 사유 반영 · observe 단언은 「observe는 사용자 질문만」 의도 유지 + oracle 7건 명시 목록(145 비편집 확인) |
| W5 `host_key`(108~110 keyset · 키 열 `서버호스트명` unclassified) | **keyset 유지** — 판정은 원값으로 내부망 안에서 하고, 키 열이 general이 아니면 `keys_recorded: false`로 반출은 건수·가짜 값만 남는다(`__main__.py`·`judge.py`) → 「반출 키는 general만」 규칙과 충돌 없음. count로 내리면 판정력만 잃는다. 엄격 적용 여부는 사용자가 뒤집을 수 있다 |
| W5 탐침 핵심어 「통합인증」 하나 | **충분(이번 회차)** — 행 수만으로는 핵심어를 가를 수 없어 하나로 둔다. 다른 서비스(103 「인터넷뱅킹」)는 P01~P05 복사로 5회차에 |

#### 10.7 잔여

- W2: 4회차에서 「선별 밖 테이블 사용 턴」 재계수 → 남으면 G-2 (a)/(b).
- ~~W5 (3) `--check-oracle --out`~~ — v1.3 완료(§10.8).
- W6(FROM 테이블 이름 가림)·F12: 145 소관.
- keyset 오라클의 빈 머리 경계 사례(키 열 해석 실패): plans/145에서 교정.
- W3 L-1(`sequential_runner`)·L-2(1단 `deep_agent`) 경로는 범위 밖.
- L1: 3·4단 단일 그래프는 엔진 폴백(postgresql)으로 W1 검사가 돌지 않는다(103 소관).
- W3: 2단 토큰 스트림에 앞 안내 없음(R-L1) · `=`만 쓴 앞 턴 일부 미탐 · 공용 `refers_to_demonstrative_server`의 「로그서버」 오탐(별건) · 지시어 명사구가 여럿이면 첫 명사구 앞만 본다.
- W1 미검사 형태(놓칠 뿐 오탐 없음): 하위 질의 안 별칭 · `db.tbl.col` 3단 한정 · 단어 문자가 아닌 백틱 식별자.

#### 10.8 W5 (3) — `--check-oracle --out`(v1.3)

- `scripts/itam_bench/__main__.py`: `oracle_check_document`(값 0 본문) · `write_oracle_check`(`LeakGate` + `rd.write_gated`) · `cmd_check_oracle`은 판정 범주를 모아 `--out`이 있을 때만 쓴다 · 기존 `--out` 인자(`--validate-knowledge`)를 재사용하고 help를 두 용도로.
- 위치: `<out>/oracle_check/oracle_check.yaml` + 하위 전용 `leak_check.json` — run 루트 `run.json`·`leak_check.json`을 덮지 않는다(`knowledge_evidence/`와 같은 방식). 쓰기 전 옛 결과를 지워 관문 실패 때 옛 결과가 새것처럼 남지 않는다.
- 내용: `kind`·`run_id`·`env`·`scenario_file`·`anchor_at`·`summary`(oracles·ok·zero_rows·error·failed)·`oracles[]`(id·scenario·turn·verdict·rows·elapsed_ms). 판정은 고정 어휘(`ok`/`zero_rows`/`error`) — DB 오류 문구는 원문·가린 문구 모두 싣지 않는다(화면은 종전대로 가린 문구).
- 관문 대상: 관문 검사 대상은 `write_gated`에 넘기는 staged 묶음 자체(`checked_files == ["oracle_check.yaml"]`) · `.yaml` 잎 단위 5규칙. 별도 반출 허용 목록은 없고 `--sync`는 `schema_catalog.yaml`·`trace.jsonl`만 읽어 `redact.py` 변경 불필요.
- 145의 `PendingTurn`·`finish_turns` 2단계 발급은 `--run` 경로에만 있고 `--check-oracle`은 `run_oracle`만 부른다 — 비해당.
- 종료 코드는 종전(실패·0행이면 1) · `--out` 관문 실패도 1. `--out` 없으면 화면·종료 코드 바이트 동일(편집 전 코드로 기대값을 먼저 고정).
- 테스트 `tests/test_scripts/test_plan146_w5_oracle_out.py` 6건(신규). 탐침 머리 주석에 회수 방법 1줄(`testdata/itam_bench/scenarios.closed.probe.yaml`).
- 회귀: `python scripts/regress.py --base 89773af --files scripts/itam_bench/__main__.py tests/test_scripts/test_plan146_w5_oracle_out.py testdata/itam_bench/scenarios.closed.probe.yaml` → rc=0 · 본체 통과 904 · 실패 0 · arch·overfit 통과 · ruff 위반 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록(145의 `run_turn` 반환 주석 변경 사유 — 이번 diff 아님): `[전체 회귀 권고] 사유: 공개 시그니처·반환 형태 변경(--wide 자동 적용) — scripts.itam_bench.__main__.run_turn(반환 주석 변경 dict[str, Any]→PendingTurn)` / `→ 사용자가 요청하면: python scripts/regress.py --full`.
- 잔여: `--out` 상대 경로는 `RESULTS_ROOT`로 풀지 않는다(경로를 끝까지 적는다) · 같은 `--out`에 탐침·오라클을 차례로 쓰면 나중 것이 교체한다(W7에 분리 지시).

---

## 149. ITAM 내부망 4회차 결과 교정 — 서비스 연결 가이드 전제 오류 · 선별 밖 테이블 증가 · 이름 칸 등호 · 반출 판독 결함

> **작성일**: 2026-10-08 · **v1.2**(착지 — §10 착지 보고)
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — 외부망 W1~W5 완료 · 내부망 5회차 대기(W6)** · 계획 끝 회귀 `--base 38adcc6` rc=0(본체 통과 4,115 · 실패 0 · 모듈 단위) · arch·overfit 통과 · verifier Critical·High 0 · 보안 감사 2라운드 Critical·High 0 · D-297·D-301 부기 · 잔여: W6 사용자 체크리스트 · 사용자 확인 2건(§10.7)
> **회귀 대상 파일**: `src/nodes/{name_match_retry(신규),result_organizer}.py` · `src/orchestration/subagents.py` · `scripts/itam_bench/{__main__,catalog,judge,redact,report}.py` · `config/{db_profiles/itam,knowledge/itam/column_descriptions,knowledge/itam/prompt_template,synonym_seeds/itam}.yaml` · `testdata/itam_bench/closed/knowledge/{guide,prompt_section,descriptions,validation(신규)}.yaml` · `testdata/itam_bench/closed/table_definitions.yaml` · `testdata/itam_bench/{column_policy.closed,scenarios.closed}.yaml` · 오라클·탐침 SQL 17(머리 주석) · 테스트 10(신규 4)
> **요청(사용자 2026-10-08)**: *"20261008-133613, verify-20261008-133551 은 itam_bench 구동 결과이다. 분석하여 수정 계획을 작성하라."*
> **입력**:
> - 내부망 4회차 run `results/itam_bench/20261008-133613/`(closed · `tier2_intent` · 워커 fabrix / 오케스트레이터 vllm · 시나리오 18 · 턴 19 · SQL 관측 18 · 오라클 7/7 · 누출 관문 통과)
> - 자산 검증 run `results/itam_bench/verify-20261008-133551/`(K2 3건 모두 성공 · 관문 통과)
> - 외부망 코드 재현 2건(§3 F7·F8). LLM 호출 0 · DB 호출 0
> **계획 시작 SHA**: `38adcc6`(`origin/main` `ee319ad` 병합 커밋 — 작성 당시 기준 `81d2657`)
> **관련 계획**: `plans/146`(3회차 교정 — 이 계획이 4회차 결과로 146 잔여 W2·W7을 이어받는다) · `plans/143`(지식 자산 사이클) · `plans/145`(반출 값 치환) · `plans/139`(정의 기반 선별) · `plans/135`(벤치)
> **관련 결정**: D-003 · D-004(LLM 출력은 정합성 근거 아님) · D-297(DB별 한글 식별자) · D-301(반출 값 0 · 식별자 남김) · D-308(정의 선별) · D-316(지식 자산 작성) · D-321(반출 값 치환)
> **게이트 답(사용자 2026-10-08)**: *"권고에 맞게 정리하라."* — G-1 (b) · G-2 (b) · G-3 (a) · G-4 (b)(§6)
> **D-번호**: 신규 없음 — 착지 때 **D-301 부기**(W5 · G-1 (b) 정책 재키잉과 식별자류 치환)와 **D-297 부기**(W3 · 이름 칸 0행 재생성)를 등재한다

---

### 0. 증거 규칙

- 수치는 반출물(`run.json`·`report.md`·`trace.jsonl`·`substitutions.yaml`) 기준이다. 결과 값은 형식 보존 가짜 값이므로 **행 수·열 이름·SQL 모양만** 근거로 쓴다.
- 「재현」은 외부망 코드로 같은 입력을 돌려 확인한 것이고, 「추정」은 검증하지 않은 추론이다.
- 반출된 값으로는 정답 여부를 판정할 수 없다. 연결 위치는 탐침(146 W5 (2))으로 확정한다.

### 1. run 개요

| 항목 | 값 | 비고 |
|---|---|---|
| 빌드 | 내부망 커밋 `1ec5ec6b2a58`(외부망에 없음) | 145 반입(`substitutions.yaml` 있음 · `registry_fallback` 칸 없음) · 146 W1·W3·W4 반입(K2 `query_examples` 3건 · 108·110 DB 오류 소멸 · 106 t2 전체 확장 소멸) — 추정: `aa69985` 이후 판 |
| P1 | 초안 `ac5e55a4e73d`(10-07 09:17) — **3회 연속 구버전** | 프로파일 컬럼·코드 컬럼·관계 모두 0 · `code_samples.yaml` 0건 |
| 오라클 | **7/7 통과**(107·108·109·110·111·112·116) | 146 W5 (1) 전환분이 모두 섰다 |
| 탐침 · `oracle_check/` | **없음** | 146 W7의 탐침 P01~P09 미실행(또는 미반출) |
| 되물음 · `routing_miss` | 0 · 0 | 3회차 102·105 존 되물음 해소(132 v1.5 반입) |
| SQL 실행 | 20/20 성공 | 3회차의 DB 1054 오류 2건 소멸 |
| 지연 | 중앙값 46.2s · 최대 79.5s | 범위 밖(143 W0) |

#### 1.1 3회차 → 4회차

| 항목 | 3회차 | 4회차 | 판단 |
|---|---|---|---|
| 오라클 | 0/0 | 7/7 | 146 W5 효과 |
| 실행 오류(1054) | 2 | 0 | 146 W1 효과(검증기 백틱 인식 · 실존 검사) |
| 106 t2 전체 서버 확장(3,748행) | 있음 | 없음(0행) | 146 W3 효과 |
| `FROM` 테이블 이름 가림 | 있음 | 없음 | 145 F9 효과 |
| **선별 밖 테이블 사용 턴** | 5 | **8** | 146 W2 「4회차에 다시 센다」 → **늘었다**(§3 F2) |
| 서비스 개념 턴 0행 | 6(102·105는 되물음) | 9턴 중 8턴(102만 85행) | §3 F1 |

### 2. 턴별 진단

| 시나리오 | 질문 요지 | 실행 SQL 요지 | 행 | 진단 | 발견 |
|---|---|---|---|---|---|
| ITAM-101 | 통합인증 서비스 서버리스트 | task 2개 모두 `tcdmsgt82.어플리케이션명 LIKE '%통합인증%'` → 서버 두 칸에 `LIKE CONCAT('%', 어플리케이션명, '%')`(두 단계) | 0 · 0 | 가이드대로 두 단계를 탔고 0행이다 · 같은 SQL을 task 2개로 나눠 79s | **F1** · F2 |
| ITAM-102 | 통합인증 서비스 서버 담당 부서 | `tcdmsif72` `그룹경로내용` OR `구성항목설명내용` `LIKE '%통합인증%'`(**한 단계 직접**) + `tcdmsbr01` LEFT JOIN | **85** | **서비스 턴에서 유일하게 행이 나왔다** · `담당부점코드`가 85행 전부 NULL이다 → 부서 답이 비었다 | **F1** · F6 |
| ITAM-103 | 인터넷뱅킹 서버 중 6개월 안 EOS | `tcdmsif72.용도내용 LIKE '%인터넷뱅킹%'` + `tcdmsif79` 세 칸 조인 | 0 | 가이드가 금한 `용도내용`을 썼다(3회차 115 관측 — 서비스 단어 없음) | F1 · F3 |
| ITAM-104 | 서비스별 서버 수 | `tcdmsgt82` × `tcdmsif72` 두 칸 부분 일치 집계 | 76 | 어플리케이션명 기준 집계는 행이 나온다(3회차와 같은 모양) | F2 |
| ITAM-105 | 통합인증 서비스 서버 유지보수 계약 만료일 | 102와 같은 직접 조건 + `72→78→41→43` 세 칸 복합 조인 4단 | 0 | 직접 조건은 85행인데 물품 키 조인 사슬에서 0행 · 108은 `tcdmsif80` 계약 칸으로 통과 · 선별에 80 없음 | F2 · F4 |
| ITAM-106 t1 | 통합인증 서비스 서버 목록 | 101과 같은 두 단계 | 0 | | **F1** |
| ITAM-106 t2 | 그 서버들 지원 종료일 | t1 조건 승계 + `tcdmsif79` | 0 | 승계는 정상(146 W3) · 반출 SQL의 검색어가 가짜 값(`'%몽즑츣즁%'`)으로 바뀌어 판독 불가 | F1 · **F8** |
| ITAM-107 | 등록 서버 몇 대 | `COUNT(*)` `tcdmsif72` | 1 | **pass** · 결과 열은 원값(general), 오라클 상세 스칼라는 가짜 값 → 같은 수가 두 값으로 보인다 | F10 |
| ITAM-108 | 이번 분기 유지보수 계약 끝나는 서버 | `tcdmsif80` 단독 · 분기 BETWEEN | 52 | **pass** · 선별(41·43·72·78)에 80이 없는데 썼다 | F2 |
| ITAM-109 | 6개월 안 EOS | `tcdmsif79` 단독 HW/SW OR | 148 | **pass**(K2 덮음) | — |
| ITAM-110 | 5년 이상 노후 서버 | `tcdmsif80` 단독 · `취득년월일` 비교 | 1,762 | **pass**(키 1,559 = 오라클) · 행 1,762 ≠ 키 1,559 — 키당 여러 행(추정: 서버당 IP별 행) | F2 · F11 |
| ITAM-111 | 취득금액 상위 3대 | ①`tcdmsif72↔tcdmsif41` 물품 키 조인 0행 ②`tcdmsif80` 단독 | 0 → 3 | **pass**(두 번째 SQL) · 물품 키 조인 0행은 3회 연속 | F2 · F4 |
| ITAM-112 | OS별 서버 수 | `tcdmsif72` 집계 | 5 | **pass**(K2 덮음) | — |
| ITAM-113 | 가상 서버 목록 | `tcdmsif92` 전체 · `LIMIT 10000` | 10,000(절단) | 3회차와 같은 해석 · 사용률 칸은 이번에 없다(용량 칸만) | 관측 유지 |
| ITAM-114 | 지난주 CPU 사용률 추이 | SQL 없음 | — | **정상**(D-319 ③) | — |
| ITAM-115 | 용도에 통합인증 적힌 서버 | `용도내용 = '통합인증'`(**등호**) | 0 | 이름 칸 부분 일치 규칙(K4) 위반 · 3회차 부분 일치도 0행이라 결과는 같다 | **F5** |
| ITAM-116 | 분류 경로별 서버 수 | `그룹경로내용` 집계 | 749 | **pass**(K2 덮음) | — |
| ITAM-117 | 통합인증 업무 할당 스토리지 | `tcdmsif52.업무명 = '통합인증'`(**등호**) | 0 | K4 위반 · 부분 일치는 시도하지 않았다 | **F5** |
| ITAM-118 | 통합인증 업무 스토리지 쓰는 서버 OS | `tcdmsif52.업무명 = '통합인증'`(**등호**) · `호스트명`↔`서버호스트명` | 0 | K4 위반 | **F5** |

### 3. 발견

| # | 발견 | 근거 | 처분 |
|---|---|---|---|
| **F1** | **서비스 연결 가이드의 전제가 틀렸다.** 146 W4가 넣은 규칙(`prompt_template` · `column_descriptions` `tcdmsgt82.어플리케이션명`)은 「질문 문구를 서버 칸에 직접 건 부분 일치는 0행 → `tcdmsgt82` 두 단계로 건다」이다. 그러나 3회차의 직접 일치 0행은 **문구 전체**(「통합인증 서비스」)를 건 결과였다. 4회차 102는 **핵심어**(「통합인증」)를 두 칸(`그룹경로내용`·`구성항목설명내용`)에 직접 걸어 85행을 얻었다. 반면 규칙대로 두 단계를 탄 101(task 2개)·106 t1·t2는 모두 0행이다 → 「핵심어 직접 부분 일치」가 1순위다(추정 — 85행의 정답 여부는 탐침·오라클로 확정) | 101·106 vs 102 실행 SQL · `config/knowledge/itam/prompt_template.yaml` 35~37행 · `testdata/itam_bench/closed/knowledge/prompt_section.yaml` 74~83행 · `descriptions.yaml` 322~326행 | **W1** · G-3 |
| **F2** | **선별 밖 테이블 사용이 5턴에서 8턴으로 늘었다**(101·104·106 t1·106 t2 → `tcdmsgt82` · 105 → `tcdmsif78` · 108·110·111 → `tcdmsif80`). 146 W1-(b)(전체 카탈로그 실존 검사)로 실행 오류는 막았지만, 가이드가 지목하는 `tcdmsgt82`·`tcdmsif80`을 선별이 한 번도 고르지 않는다. 105는 선별된 테이블(41·43·72)만으로 4단 조인을 짜 0행이 났다. 선별에 80이 있었으면 108처럼 답했을 것이다(추정) | `schema_context.presented_tables` ↔ `sql_analysis.tables`(계수 스크립트 §7) · 146 G-2 (c) 조건 충족 | **W2** · G-2 |
| F3 | `용도내용`은 서비스 단서로 쓰지 말라는 규칙(146 K1 g06)을 103이 어겼다 | 103 | W1(규칙 문구 위치 · 강도) |
| F4 | 서버 원장 물품 키(`물품분류번호`·`물품고유번호`)로 자산·계약에 잇는 조인이 3회 연속 0행이다(111 ①·105) | 2·3·4회차 | W1(K1 g05 강화) · W2 · 탐침 P06·P07 |
| **F5** | **이름 칸에 등호를 쓴다**(115·117·118 — K4 부분 일치 규칙이 반입됐는데도). 프롬프트 규칙만으로는 3턴 중 0턴 지켜졌다. 0행이어도 재생성되지 않는다(`retries` 0) | 실행 SQL · `retries` | **W3** · G-4 |
| F6 | 102 `담당부점코드`가 85행 전부 NULL — 서버 원장의 부점 칸은 비어 있고 부서는 다른 원장에 있을 수 있다(추정 — 자산 원장 관리 부점 등) | 102 결과 열 `nulls` 85/85 | W1(K3 설명) · 탐침 추가(P10) |
| **F7** | **반출 가림이 LIKE 와일드카드를 가린다** — `LIKE CONCAT('%', a.`어플리케이션명`, '%')`가 `CONCAT('<가림>', …, '<가림>')`로 나간다. 101·104·106 SQL의 핵심 모양이 판독 불가 | **재현**: `redact_sql` 위 SQL → `'<가림>'` · `_trivially_safe`가 `%` 단독을 형식 문자열로 보지 않는다(`_FORMAT`은 `%`+글자) | **W4** (1) |
| **F8** | **프롬프트 낱말 허용이 그 턴 프롬프트만 본다** — 106 t2 SQL의 검색어는 같은 시나리오 1턴 프롬프트에 있는 말인데(추정 「통합인증」) 2턴 프롬프트에 없어서 가짜 값으로 나갔다. 같은 시나리오 앞 턴 프롬프트는 이미 `trace.jsonl`에 원문으로 반출된다 | **재현**: 같은 SQL을 2턴 프롬프트로 `redact_sql` → `'<가림>'` · `__main__._redacted_sqls`에 `turn.query`만 넘긴다 | **W4** (2) |
| **F9** | **closed 컬럼 정책과 실제 DB 컬럼 이름이 다르다** — 정책(`testdata/itam_bench/column_policy.closed.yaml`)은 벤더 시트 영문 이름(`sevrHostName` 등 77개 · `TCDMSIF80`·`79`)인데, 운영 DB 컬럼은 한글 이름(`서버호스트명` — closed 스키마 사본 `testdata/itam_bench/closed/itam_schema.json`)이다. 정책이 한 칸도 맞지 않아 **결과 열 30개가 전부 미분류**(리포트 §8 19개 이름)이고, 그 값은 모두 가짜 값이나 `<가림>`이 된다 → 집계 수(104 `server_count`)·1자 코드(110 `자산상태구분`)·경과 연수(110 `age_years`)를 판독할 수 없다 | 정책 컬럼 77개 중 한글 0 · 스키마 사본 `tcdmsif80` 컬럼 순서가 정책 영문 순서와 1:1(추정 — 같은 시트) | **W5** · G-1 |
| **F10** | `COUNT(DISTINCT 컬럼)` 결과는 그 컬럼 등급을 따라 미분류로 치환된다(104) · 107 오라클 상세 스칼라는 가짜 값인데 같은 수의 결과 열(`COUNT(*)` → general)은 원값이라 같은 수가 두 값으로 보인다 | `redact._column_grade`(COUNT 식 → 원 컬럼 등급) · `judge.sanitize_detail` | **W4** (4)(5) · G-1 |
| **F11** | 벤치 SQL 분석기가 한글·백틱 식별자를 못 읽는다 — `sql_columns`·`date_text_misuse`는 `[A-Za-z_]` 토큰만, `join_key_partial`은 `` t.`컬럼` ``을 못 잡는다 → 4회차 모든 턴 `columns: []` · 날짜 문자열 오용·조인 키 부족 탐지가 항상 거짓(침묵) | `scripts/itam_bench/judge.py` 91·120·141행 | **W4** (3) |
| F12 | 110 행 수 1,762 ≠ 키 1,559 — keyset 비교는 중복 행을 보지 않는다 | 110 | W4 (6)(선택) |
| F13 | 첫 턴 0행 응답이 22자 안팎(101·106 t1·117) — 단일 경로의 0건 진단(`result_organizer._diagnose_empty_result`)이 2단 경로 응답에 붙는지 미확정(추정: 안 붙는다) | `response_chars` | W3 조사 1단계 |
| F14 | P1 재실행 3회 연속 미이행 · 탐침 미실행 | `run.json` `assets.p1` · `oracle_check/` 없음 | **W6**(내부망) |

검증 run(`verify-20261008-133551`)은 K2 3건(EOS 6개월 · OS별 · 그룹 경로별)이 모두 실행 성공이다. 조치는 없다.

### 4. 결정 충돌 검토(작업 전 필수)

| 결정 | 이 계획과의 관계 | 충돌 |
|---|---|---|
| D-004 | F5는 LLM 출력(등호)에 정합성을 맡긴 결과 — W3은 결정적 신호(0행 + 이름 칸 등호)로 재생성을 건다 | 없음(취지 이행) |
| D-297 | W3은 허용 DB의 이름 칸 목록을 데이터(정의·설명)에서 읽는다 · 엔진 분기 없음 | 없음 · 착지 때 부기 |
| D-301 ② | W5는 사용자 검토를 마친 등급(77항목)을 실제 컬럼 이름에 다시 붙인다 — 등급 자체는 바꾸지 않는 것이 (a), 식별자류를 치환 쪽으로 내리는 것이 (b) G-1 (b) — 검토 등급 중 식별자류(호스트명·IP·ID)만 치환 쪽으로 내린다 · 착지 때 D-301 부기 |
| D-301 부기(식별자 남김) | W4 (1)은 값이 아닌 구조 기호(`%`·`_`)만 남긴다 | 없음 |
| D-308 | W2는 정의 데이터만 고친다(G-2 (b)) · 선별 사다리 무변경 | 없음 |
| D-316 | 지식 자산은 스킬(`/itam-knowledge`)로 쓴다 | 없음 |
| D-321 | W4 (2)는 이미 반출되는 문자열(같은 시나리오 프롬프트)만 남긴다 · 대응표·난수 규칙은 그대로 · 누출 관문 무변경 | 없음 |

### 5. 작업 단위

#### W1 — 지식 자산 교정(스킬 `/itam-knowledge results/itam_bench/20261008-133613` · F1·F3·F4·F6 · G-3 (a))

| 자산 | 항목 | 고칠 내용(값 없이) |
|---|---|---|
| K1·K4 | 서비스 연결(g06 · 규칙 섹션) | 순서를 뒤집는다. **1순위**: 질문의 고유 핵심어를 서버 원장 `그룹경로내용`·`구성항목설명내용`에 OR 부분 일치로 직접 건다(4회차 관측: 행 반환). **2순위**: 1순위가 0행이면 `tcdmsgt82.어플리케이션명`을 거친다(서비스별 집계는 처음부터 2순위 모양 — 104). 「문구 전체 부분 일치 0행」 근거 문장은 「문구 전체」로 범위를 고친다. 이력은 YAML 주석으로 둔다(143 v1.7 ⑨) |
| K1 | g05 · g03 | 서버 단위 유지보수 계약·취득금액·취득일은 `tcdmsif80` 한 테이블로 답한다. 서버 원장 물품 키로 `tcdmsif41`·`43`에 잇는 조인은 3회 연속 0행이었다 — 쓰지 않는다. 서비스 조건이 필요하면 `tcdmsif80`↔`tcdmsif72`를 `서버호스트명`(+`그룹회사코드`)으로 잇는다(추정 — 탐침 P07로 행 배수 확인) |
| K4 | 용도 칸 | `용도내용`은 「용도」라고 지목한 질문(115)에만 쓴다 — 서비스 단서로 쓰지 않는다(위반 103) |
| K3 | 설명 | `tcdmsif72.담당부점코드`(관측: 서비스 서버 85대 모두 비어 있음 — 부서 질문은 다른 원장 후보) · `tcdmsgt82.어플리케이션명`(2순위로 내림) |

절차: 143 §4.2.1 B3~B7과 같다(모의 DB 실행 검증 → 빌드 → `prompt_render_diff --ci` 차이 0).

#### W2 — 선별 보강(146 W2 확정 · F2 · G-2 (b) — 데이터만)

| 단계 | 내용 | 확인 |
|---|---|---|
| 1 | `testdata/itam_bench/closed/table_definitions.yaml` — `tcdmsif80` `manages`에 서버 단위 질문 유형(취득금액·취득일·경과 연수·유지보수 계약 만료·EOS)을, `tcdmsgt82` `manages`에 「서비스(어플리케이션) 목록 · 서비스별 집계」를 드러낸다(값 없이 · 정의는 사람 검토 대상 — D-308) | 정의 검증기 통과 |
| 2 | 빌드 → `prompt_render_diff --ci`로 선별 후보 줄의 두 테이블 문구만 바뀌었는지 확인 | 차이 = 두 줄 |
| 3 | 코드 변경 없음 — `_candidate_line`에 `notes`를 싣는 안(a)은 5회차 계수로도 부족할 때만 다시 연다 | |

완료 판정: 5회차에서 선별 밖 사용 턴을 다시 센다(§7 계수 스크립트). 목표: 108·110·111·105가 `tcdmsif80`을, 101·104·106이 `tcdmsgt82`(2순위로 갈 때)를 선별 안에서 쓴다.

#### W3 — 이름 칸 등호 0행 교정(F5 · F13 · G-4 (b))

1. **조사**: 2단 경로(`agent_orchestrator` → task → `result_aggregator`)에서 0행 task가 재생성·0건 진단을 받는지 확정한다(`text2sql.empty_diagnosis_enabled` 기본 on · 단일 경로 `result_organizer`). 끊긴 지점부터 확정한다.
2. **교정(G-4 (b))**: task SQL이 0행이고 WHERE에 **이름 칸**(정의·설명이 「부분 일치」로 표시한 칸 — 데이터에서 읽는다)에 대한 문자열 등호·`IN`이 있으면, 「이 칸은 핵심어 부분 일치로 다시 써라」 힌트를 붙여 1회만 재생성한다. 재생성도 0행이면 그대로 0행을 답한다(침묵 확장 금지). 폴스타는 이름 칸 표시가 없어 비트 동일.
3. 테스트: 목 LLM — 등호 0행 → LIKE 재생성 1회 · 재생성 0행이면 끝 · 행이 있으면 재생성 없음 · 이름 칸이 아니면 재생성 없음 · 단일·멀티 대칭.

#### W4 — 벤치 반출·판정 교정(`scripts/itam_bench/` · 본체 코드 0)

| # | 내용 | 파일 | 확인 |
|---|---|---|---|
| (1) | LIKE 와일드카드만으로 된 리터럴(`'%'`·`'_'`·`'%%'`)은 값이 아니라 구조로 남긴다(F7) | `redact.py` `_trivially_safe` | 101 모양 SQL → `CONCAT('%', …, '%')` · 값이 섞인 `'%통합인증%'` 판정은 종전대로 |
| (2) | 프롬프트 낱말 허용 범위 = **같은 시나리오의 현재 턴까지 프롬프트 전부**(F8) — 앞 턴 프롬프트는 `trace.jsonl`에 원문으로 이미 반출된다 | `__main__.py` `_redacted_sqls` 호출부 · 결과 요약·오류 문구 호출부 같은 범위 | 106 t2 모양 → 검색어 원문 · 다른 시나리오 프롬프트 낱말은 여전히 치환 |
| (3) | SQL 분석기가 한글·백틱 식별자를 읽는다(F11) | `judge.py` `sql_columns`·`date_text_misuse`·`join_key_partial` | 4회차 SQL로 `columns`가 채워진다 · 영문 샌드박스 기존 테스트 무변경 |
| (4) | `COUNT(…)` 결과 열은 원 컬럼 등급과 무관하게 general(F10 · G-1 (b) 묶음) | `redact.py` `_expression_sources`·`_column_grade` | 104 `server_count` 원값 · `COUNT` 밖 식은 종전대로 |
| (5) | 오라클 상세 스칼라 등급을 시스템 결과 열 등급과 맞춘다 — 결과 열이 general이면 스칼라도 원값(F10) | `judge.py` `sanitize_detail` | 107 상세 = 결과 값 |
| (6) | (선택) keyset 판정에 「행 수 ≠ 키 수」 표지(F12) | `judge.py` · `report.py` | 110 표지 |

누출 관문(`LeakGate`)은 바꾸지 않는다. 각 항목은 관문 통과 테스트를 함께 둔다.

#### W5 — closed 컬럼 정책을 실제 컬럼 이름에 다시 붙인다(F9 · G-1 (b))

1. 영문 정책 이름 ↔ 한글 컬럼 이름 대응을 **결정적으로** 만든다. 근거는 벤더 시트 전사본(`testdata/itam/schema.yaml`)의 논리명이나 컬럼 순서이고, LLM은 쓰지 않는다. 착수 첫 단계에서 77개 전부 1:1인지 실측하고, 어긋나는 항목은 사용자 검토로 넘긴다.
2. 등급(G-1 (b)): 검토 등급을 한글 이름에 그대로 옮기되, **식별자류(호스트명·IP·각종 ID 칸)는 general에서 빼** 치환 대상으로 둔다(run 일관 가짜 값 — 조인·키 비교 유지 · D-321). OS·벤더·모델·용량·건수·코드·날짜 칸은 general 그대로. 내린 칸 목록은 계획서 착지 보고와 D-301 부기에 이름만 적는다.
3. 가드 테스트(`tests/test_scripts/test_itam_bench_closed_kit.py`): **closed 정책의 모든 컬럼 이름이 closed 스키마 사본에 실존**해야 한다. 이번처럼 정책이 한 칸도 맞지 않는 상태를 다시 막는다.

#### W6 — 내부망 5회차 체크리스트(사용자)

- [ ] 반입 커밋에 이 계획 W1~W5가 들어갔는지 확인한다 — 리포트 자산 지문(`query_guide`·`prompt_template`)이 외부망 빌드와 같은지 대조
- [ ] **「DB 구조」 탭 P1 재실행·승인**(3회 연속 미이행 — 리포트 첫머리 「구버전 빌드」 경고가 사라져야 한다)
- [ ] **탐침 실행**(146 W5 (2) — 4회차 미실행): `--check-oracle --env closed --scenarios testdata/itam_bench/scenarios.closed.probe.yaml --out results/itam_bench/<run>-probe` → `oracle_check.yaml` 반출. 이번 결과로 **P02·P03(핵심어 직접 부분 일치)·P05(두 단계)**가 가장 중요하다
- [ ] 오라클 `--check-oracle --out results/itam_bench/<run>`(기본 시나리오)
- [ ] 5회차 run(`--env closed`) → 반출
- [ ] 판독: 101·106 SQL에 `CONCAT('%', …)`가 보이는지(W4 (1)) · 106 t2 검색어가 원문인지(W4 (2)) · 리포트 §8 「분류 필요 컬럼」이 줄었는지(W5)

### 6. 게이트 — 답(2026-10-08 · 권고안 전부)

| # | 질문 | 답 | 반영 |
|---|---|---|---|
| G-1 | closed 정책을 한글 컬럼에 다시 붙일 때 식별자 칸(호스트명·IP·ID) | **(b) 식별자류만 치환 쪽으로 내린다** — OS·벤더·용량·건수·코드·날짜는 원값, 식별자는 run 일관 가짜 값 | W5 · W4 (4)(5) · D-301 부기 |
| G-2 | 선별이 `tcdmsif80`·`tcdmsgt82`를 고르게 하는 방법 | **(b) 정의 `manages` 문구만 고친다(코드 0)** — 5회차 계수로 부족하면 (a) 후보 줄 `notes`를 다시 연다 | W2 |
| G-3 | 서비스 연결 가이드를 탐침 전에 고칠지 | **(a) 지금 고친다** — 핵심어 직접 부분 일치 1순위 · `tcdmsgt82` 두 단계 2순위 · 탐침으로 다시 확인 | W1 · W6 |
| G-4 | 이름 칸 등호 0행 | **(b) 0행 + 이름 칸 문자열 등호·`IN`이면 부분 일치 힌트로 1회 재생성** — 재생성도 0행이면 그대로 0행 | W3 · D-297 부기 |

기각: G-1 (a)(실제 호스트명 원값 반출) · (c)(판독 불가 유지) · G-2 (a)·(c)(이번에는 코드 없이) · G-3 (b)(5회차도 0행 가능성) · G-4 (a)(4회차 3턴 모두 위반) · (c)(SQL 의미 변경 위험).

### 7. 검증

- 각 W의 자기 테스트 + `regress.py --no-tests --files …`(Wave 중간) · 계획 끝 `regress.py --base <착수 SHA> --files …` 1회(D-303).
- 선별 밖 계수: `trace.jsonl`의 `schema_context.presented_tables` ↔ `sql_analysis.tables` 차집합이 있는 턴 수(3회차 5 · 4회차 8).
- W4는 4회차 trace의 SQL 모양(값 없는 재구성 입력)으로 회귀 입력을 만든다 — 반출물 원본은 테스트 데이터로 커밋하지 않는다(D-308).
- W1은 `prompt_render_diff --ci`로 의도한 블록만 바뀌었는지 확인한다.

### 8. 위험

| 위험 | 대응 |
|---|---|
| 102의 85행이 정답이 아닐 수 있다(그룹 경로에 「통합인증」이 다른 뜻으로 섞임) | G-3 (a)는 순서만 바꾼다 · 탐침·5회차 오라클로 판정 |
| W3 재생성이 지연을 늘린다(현 중앙값 46s) | 0행 + 이름 칸 등호일 때만 · 1회 상한 |
| W5 대응이 1:1이 아니면 잘못된 칸에 general이 붙는다 | 결정적 대응 + 1:1 실측 + 어긋남은 사용자 검토 · 실존 가드 테스트 |
| `origin/main`이 앞서 있다(147·148·D-322) | 착수 전 병합 · 신규 D-번호는 색인 최댓값+1 |

### 9. 버전 이력

- v1.2(2026-10-08) — 착지 — W1~W5 구현 · 모의 DB 검증 81/81 · 계획 끝 회귀 rc=0 · verifier·보안 감사 Critical·High 0 · D-297·D-301 부기 · §10 착지 보고 · 상태 WIP(W6 내부망 대기)
- v1.1(2026-10-08) — 게이트 답 반영(권고안 전부 — G-1 (b) · G-2 (b) · G-3 (a) · G-4 (b)) · W2를 데이터 단독으로 확정 · W5 등급 규칙 확정 · 착지 때 D-301·D-297 부기
- v1.0(2026-10-08) — 4회차 run·검증 run 분석 · 발견 F1~F14 · W1~W6 · 게이트 G-1~G-4

### 10. 착지 보고(v1.2 · 2026-10-08)

#### 10.1 W1·W2 — 지식 자산·정의(스킬 `/itam-knowledge`)
- 원천: `guide.yaml` g03·g05·g06 · `prompt_section.yaml` s07 · `descriptions.yaml`(`tcdmsif72` 그룹경로내용·구성항목설명내용·용도내용·담당부점코드 · `tcdmsgt82.어플리케이션명`) — evidence `20261008-133613` · 서비스 연결 1순위 = 핵심어를 72 두 칸 OR 부분 일치 직접 · 2순위 = 0행일 때만 `tcdmsgt82` 두 단계 · 서버 단위 계약·취득은 `tcdmsif80` 단독(물품 키 41·43 조인 금지) · `용도내용`은 용도 지목 질문에만.
- W2: `tcdmsgt82`·`tcdmsif80` `manages`에 질문 유형 문장. `tcdmsif80`에 「EOS」는 넣지 않았다(이 테이블에 지원 종료 칸이 없다 — EOS는 `tcdmsif79`).
- 이름 칸 표시(W3 신호): 72 세 칸 · `tcdmsif52.업무명` · `tcdmsgt82.어플리케이션명`.
- 검증: 모의 DB 실행 검증 81/81(db_unverified 0 · 3회차 run 모의 DB · 전용 MCP 9198 env 오버라이드 — 9099·`.env` 무변경 · 끝나고 내림) → `validation.yaml` · 빌드 기준 1회차 `20261006-152938`(재기저 없음 · 스키마 캐시 차이 0) · 선별 후보 줄 차이 2줄(80·gt82). `prompt_render_diff --ci`는 폴스타만 렌더해 ITAM 블록 차이 0(도구 범위 밖).
- 함께 고침: `--evidence` 누출 관문 오탐 — 오라클·탐침 SQL 17개 머리 주석의 루프백 IP 표기를 「포트 3308」로(SQL 무변경).

#### 10.2 W3 — 이름 칸 등호 0행 1회 재생성(G-4 (b) · D-297 부기)
- 조사: 0행은 두 경로 모두 「충분」이라 재생성이 없었다(`result_organizer` 비집계 0행 · 2단 루프는 실행 오류만 재생성). F13 「2단에 0건 진단 배선 없음」 추정은 **반증** — 배선은 있고, 퍼널 프로브가 수치 비교만 사용자 조건으로 봐 문자열 조건은 진단이 None이 된다(22자 = 진단 없는 0행 문구). 115(71자)는 진단이 생겨야 하는 모양이라 내부망 `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED` off 가능성(미확정 · W6).
- 구현: `src/nodes/name_match_retry.py`(판정 1개) — 2단 `_run_single_db_pipeline`·그래프 `result_organizer` 공용 · 이름 칸 = 설명 정본 「부분 일치」 표시 · CASE 구간 제외 · 1회 상한 · 2단은 재생성 실패 시 0행 답 복원 · 그래프는 남은 재시도 2회 이상일 때만 · 신규 플래그 없음 · 폴스타 비트 동일.

#### 10.3 W4 — 벤치 반출·판정 교정(본체 0)
- (1) `%`·`_`만으로 된 리터럴 구조 유지(fullmatch) (2) 프롬프트 낱말 허용 = 같은 시나리오 현재 턴까지 (3) judge 한글·백틱 식별자 인식(영문 토큰화 동일) (4) 순수 `COUNT(…)` 결과 열 general(위장 식·UNION·백틱 저장 함수 제외) (5) 오라클 스칼라 = 엄격 등급(순수 COUNT만 예외) (6) keyset 「행≠키」 표지.

#### 10.4 W5 — closed 정책 재키잉(G-1 (b) · D-301 부기)
- 77항목 → 한글 컬럼(시트 순서 77/77 · 논리명 63/77) · 새 등급 `identifier` 12칸(D-301 부기에 이름) · network는 생성기 없으면 길이만 · 프롬프트 린트는 한글 이름을 코드 꼴일 때만 · 가드 테스트(정책 칼럼 실존 · 샌드박스 등급 동기).

#### 10.5 검증
- 계획 끝 회귀: `python scripts/regress.py --base 38adcc6 --files <49파일>` → rc=0 · 본체 통과 4,115 · 실패 0 · 건너뜀 7 · arch·overfit 통과 · ruff·mypy 이번 diff 줄 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록 없음(`logs/regress/20261008-154831-25424`).
- verifier: Critical·High 0 · M-1(재생성 실패가 0행 답을 실패로 바꿈) 교정 · L-2·L-4 교정 · L-1·L-3 수용 · 테스트 2파일(xfail 3 — 그래프 M-1 잔여·L-1·L-3).
- 보안 감사: 1차 Medium 1(스칼라 등급)·Info(와일드카드 끝 개행) 교정 → 재감사 Critical·High·Medium 0 · Low 2(주석 낀 백틱 count · UNION) 교정.

#### 10.6 팀 리드 판단
| 항목 | 판단 · 되돌리는 비용 |
|---|---|
| W3 신호를 런타임 Redis 설명이 아니라 정본 파일에서 읽음 | 정본은 Redis에 없는 키만 채운다(HSETNX) — 내부망 Redis 설명 1,988건이라 표시가 닿지 않을 수 있다. 되돌리기는 `_knowledge_columns` 한 곳 |
| `identifier` 등급 신설(계획은 「식별자류 치환」만 적음) | 기존 등급으로는 별칭·`oracle.key`가 가려지거나 거부됐다. 되돌리기는 정책 12칸을 general로 · 등급 상수 제거 |
| 번호류 7칸도 identifier | G-1 (b) 「ID 칸」을 보수적으로 읽음. 되돌리기는 정책 7줄 |
| 오라클 SQL 머리 주석 IP 표기 교체 | 범위 밖이나 근거 묶음 생성이 막혀 교정(SQL 무변경) |

#### 10.7 잔여 · 사용자 확인
- **사용자 확인 1**: 논리명 불일치 14칸을 시트 순서 대응으로 넣었다(서버호스트명·IP주소내용 80·79 · 서버유형구분 · 자산유지보수구분 · 서버운영환경구분 · 노후교체기기구분 · 자산상태구분 · 자산분류구분 · 세부자산분류구분 · 시리얼번호 · 하드웨어/소프트웨어지원종료년월일 — 같은 자리·타입·NULL).
- **사용자 확인 2**: 원값 반출로 바뀌는 것 — general 50칸(OS·벤더·모델·용량·건수·코드·날짜·부점·물품명·설치지역 등) · amount 3칸의 합계·최소·최대 · general 술어 리터럴·비교 수 · `%`/`_` 리터럴 · 같은 시나리오 앞 턴 프롬프트 낱말 · 순수 COUNT 결과 · 한글 정책 이름의 산문 사용(린트). general은 이름 단위라 정책 밖 테이블의 같은 이름 칸에도 붙는다(감사 Low).
- 그래프 경로(3·4단) M-1 잔여 · 후속 턴 덜 발동(L-1) · 큰따옴표 리터럴 미탐(L-3) · `multi_db_executor` 미적용.
- `tcdmsif80` 정의 `related`가 여전히 41·43 물품 키 연결을 싣는다(verifier L-5) — 5회차 선별 계수에서 41·43이 끌려오면 다룬다.
- 0건 진단을 문자열 조건까지 넓히는 일(F13)은 범위 밖 — 별건.
- 2단 0행 집계 질의의 `data_insufficient`가 task 실패로 새는 추정(W3 조사 부수 관찰 · 미실측).

#### 10.8 W6 추가 확인(5회차)
- 내부망 로그 `설명 정본 파일 적재: db_id=itam … kept=… added=…` — K3 설명 교정이 Redis에 이미 있는 칸에는 적재되지 않는다(kept). kept가 크면 「DB 구조」 탭에서 설명을 다시 적재해야 K3가 프롬프트에 닿는다.
- 내부망 `.env`의 `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED` 값(115 71자 응답).
- 판독: 117·118 재생성(로그 `이름 칸 등호 0행 — 부분 일치 힌트로 1회 재생성`) · 리포트 §8 분류 필요 컬럼 감소 · 104 `server_count` 원값.

---

## 150. ITAM 내부망 5회차 결과 교정 — 지시어 표본 승계 · 근거 묶음 관문 실패 · 마트 단독 질문의 불필요 조인 · 반입 지문 줄바꿈

> **작성일**: 2026-10-08 · **v1.5**(외부망 구현 완료 — W1~W7·W9 · 검토 권고 반영)
> **종결(2026-10-12)**: **완료(잔여 이관)** — 남은 일은 `plans/151` §10으로 옮겼다(사용자 지시 *"기존 itam 벤치마크 테스트 관련 계획 … 잔여 작업은 151번에 정리하여 통합시켜라."*). 아래 상태 줄은 종결 직전 기록이다.
> **상태(종결 전)**: **WIP — 외부망 W1~W7·W9 완료 · 내부망 6회차(W8) 대기** · 회귀 rc=0(`범위: 모듈 단위 — 전체 미실행`) · verifier Critical/High 0(Medium 7 중 5 교정 · 의견 2는 사용자 검토) · 보안 감사 Critical/High/Medium 0(Low 3 — L1 교정 · L2·L3 수용 잔여)
> **착지 요약(2026-10-08)**:
> - W1 표본 앞 턴 승계: `sampled_turn`→`sampled_antecedent` · 계획 맥락에 표본 값 대신 「결과 N건 — 표본만 보존」+조건 승계 지시 · 표본 IN 「일부만 조회」·넓힘 결정적 고지(행 유지 · 맨 앞) · 2·3·4단 대칭 · 매뉴얼 U-34 1줄(D-255 반영) · MLX는 계획 단계만 확인(MCP 9099 미기동 — 교정 전 표본 5대로 좁힘 재현 → 교정 후 조건 재조회 계획)
> - W2 벤치: 가짜 값 등록부(`FakeValues.registry`) → 5회차 `--evidence` 관문 통과 · 날짜 형식 리터럴(지시자 2+ · 숫자 ≤4 · 띄운 연쇄 금지) · 지문 CRLF 정규화 v2 표지 · `retry_attempt` = 턴 안 실행 순번(+`server_retry_attempt` · 리포트 범례) · 「승인 코드값 없음」 관측 · `join_keys`/`join_key_miss`(별칭·USING·[NOT] EXISTS·[NOT] IN 하위 질의·함수 감싼 등호·인스턴스 단위) · `## 10. 조인 판정` · 판정 칸 이름 = 정책 ∪ 스냅숏 · `require_judgement` · `--compare` 「프롬프트 세트 교체」 고지+범주별 판정 표
> - W3 지식(스킬 `itam-knowledge`): K1 g03·g05 · K4 s11 마트 단독(+s10 「서버」 정렬) · K2 변형 2건(45일 · 7년) · K3 `담당부점코드` · `--validate-knowledge` 84/84(모의 DB 3308 실행 · 임시 MCP 9198 경유) · 빌드 산출(`config/db_profiles/itam.yaml` 등) 반영
> - W4 정의 41·43·52 `manages`(후보 줄 차이 3줄뿐) · W5 핵심어 후보(D-297 부기) · W6 정책 10칸(87칸 · D-301 부기) · W7 탐침 44파일(측정마다 1 SQL — §5 W7 착지 교정) · W9 r6 32건 35턴(오라클 10턴 · 겹침 가드 Jaccard 0.6 · 기존 세트 내부 최대 0.714 · r6 최대 0.5)
> - **권고 반영(사용자 2026-10-12 「권고에 맞게 진행하라.」 · v1.5)**: 201 문구 「다음 달 말까지」(해석 하나 — 오라클 불변) · 221 문구 교체(118 치환꼴 → 설치 지역별 대수) + 겹침 가드에 서비스 이름 자리표(213·232 t1도 걸려 문구 교체) · count 오라클 206·207·227 NULL·빈 그룹 제외(218과 통일) · 203 경과년수 경계 함정 메모 · 서버 수 기준 탐침 P19A·B(80 COUNT(*) 대 고유 호스트 — 202·203·212 DISTINCT 기준 확인) · `tcdmsif72.용도내용` general → free_text(보안 감사 L2 · D-301 부기)
> - 잔여(사용자): W8 내부망 체크리스트 · W4 완료 판정은 6회차 선별 밖 계수
> - **후속(2026-10-12)**: 6회차(`20261012-092859`)는 r6가 아니라 기존 세트로 돌았고 P1 승인이 정의를 덮어 W4 판정 불가 → 미이행 W8과 W4 판정은 `plans/151` W9로 잇는다
> **회귀 대상 파일**: `src/utils/empty_antecedent.py` · `src/nodes/context_resolver.py` · `src/orchestration/intent_planner.py` · `src/orchestration/result_aggregator.py` · `src/nodes/output_generator.py` · `src/nodes/name_match_retry.py` · `src/orchestration/subagents.py` · `scripts/itam_bench/{__main__,catalog,judge,knowledge,redact,report,substitute}.py` · `scripts/manual/content/user.md` · `src/static/manual/{user,admin}.html` · `config/db_profiles/itam.yaml` · `config/knowledge/itam/{column_descriptions,prompt_template}.yaml` · `config/synonym_seeds/itam.yaml` · `testdata/itam_bench/{column_policy.closed,scenarios.closed.probe,scenarios.closed.r6}.yaml` · `testdata/itam_bench/closed/{table_definitions.yaml,knowledge/*}` · `testdata/scenarios/oracles/ITAM-150-P*·ITAM-2NN` · 테스트(`test_plan150_*` · `test_itam_bench_plan150_*` · 갱신 6파일)
> **요청(사용자 2026-10-08)**:
> - *"\"20261008-163344\", \"verify-20261008-163152' 폴더에 itam_bench결과가 있다. 이를 분석하여 수정계획 파일을 작성하라."*
> - *"다음 테스트에서는 기존 프롬프트 말고 새로운 사용자 프롬프트를 통해 테스트가 되도록 계획에 포함시켜라."* → W9 · G-5
> - *"현재 파악한 db 테이블을 확인해 보고 여러개의 테이블 조회와 join 조건 등을 테스트 할 수 있도록 시나리오를 개선하라."* → F14 · W2 (6)(7) · W7 P09~P18 · W9 조인 지도·조인 군(ITAM-219~232)
> **게이트 답(사용자 2026-10-08)**: *"a: 무인공과금 , 법원공탁금, 그린뱅크  그리고 G-1~4 는 권고대로 하라."* — G-1 (a) · G-2 (b) · G-3 (a) · G-4 권고안 · G-5 (a) 서비스 이름 3개(§6)
> **입력**:
> - 내부망 5회차 run `results/itam_bench/20261008-163344/`
>   - 실행 조건: closed · `tier2_intent` · 워커 fabrix / 오케스트레이터 vllm
>   - 규모: 시나리오 18 · 턴 19 · SQL 관측 18
>   - 결과: 오라클 7/7 · 누출 관문 통과
> - 자산 검증 run `results/itam_bench/verify-20261008-163152/`(K2 3건 모두 성공 · 관문 통과)
> - 외부망 재현 3건(§3 F1·F2·F9). LLM 호출 0 · DB 호출 0
> **계획 시작 SHA**: `c61e601`
> **관련 계획**:
> - `plans/149`(4회차 교정): 이 계획이 5회차 결과로 149 W6 잔여와 §10.7 잔여를 이어받는다.
> - `plans/146`(3회차 · W3 0행 앞 턴 승계)
> - `plans/143`(지식 자산 사이클) · `plans/145`(반출 값 치환) · `plans/139`(정의 기반 선별)
> **관련 결정**:
> - D-004(LLM 출력은 정합성 근거 아님) · D-055(지시어 승계) · D-066(단일/멀티 동등)
> - D-297(한글 식별자 · 이름 칸 재생성) · D-301(반출 값 0 · 등급 검토) · D-308(정의 선별)
> - D-316(지식 자산 작성) · D-321(반출 값 치환)
> **D-번호**: 신규 없음. 착지 때 **D-055 부기**(W1 표본 승계)와 **D-297 부기**(W5 핵심어 힌트)를 등재한다. **D-301 부기**(G-4 — 정책 10칸 추가)도 등재한다.

---

### 0. 증거 규칙

- 수치는 반출물(`run.json`·`report.md`·`trace.jsonl`·`substitutions.yaml`)을 기준으로 한다. 결과 값은 형식 보존 가짜 값이므로 **행 수·열 이름·SQL 모양만** 근거로 쓴다.
- 「재현」은 외부망 코드로 같은 입력을 돌려 확인한 것이다. 「추정」은 검증하지 않은 추론이다.
- 반출된 값으로는 정답 여부를 판정할 수 없다. 연결 위치·0행 원인은 탐침으로 확정한다.

### 1. run 개요

| 항목 | 값 | 비고 |
|---|---|---|
| 빌드 | 내부망 커밋 `74dc51df2c86` dirty(외부망에 없음) | **자산 지문 3종(profile·prompt_template·seeds)이 `8afb4a9`(149 W1~W5) 파일의 CRLF 변환본과 일치한다(재현)** → 149 반입 확인. 4회차 지문은 `aa69985`의 CRLF 변환본과 일치한다(§3 F9) |
| P1 | 초안 `ac5e55a4e73d`(10-07 09:17) — **4회 연속 구버전** | 프로파일 컬럼·코드 컬럼·관계 모두 0 · `code_samples.yaml` 0건 |
| 오라클 | **7/7 통과**(107·108·109·110·111·112·116) | 110은 「행≠키(2226/1559)」 |
| 탐침 · `oracle_check/` | **없음**(2회 연속) | 146 P01~P09 미실행 |
| SQL 실행 | 21/21 성공 · 재시도 평균 0.17 | |
| 지연 | 중앙값 42.6s · 최대 100.2s(105) | 범위 밖(143 W0) |
| 근거 묶음(`--evidence`) | **누출 관문 실패 1건** — `turns.yaml · turns[6].executed_sqls[0].sql · pii_regex` | §3 F2 — 스킬 `/itam-knowledge` 1단계가 막힌다 |
| 검증 run | K2 3건(EOS 6개월 · OS별 · 그룹 경로별) 실행 성공 · `query_examples` 지문 `d79eca7c4c29` | 조치 없음 |

#### 1.1 4회차 → 5회차

`--compare 20261008-133613 20261008-163344`로 비교하면 판정 전이는 「바뀐 턴 없음」이다(pass 7 · observe 12 그대로). 아래는 관측 턴의 실질 변화다.

| 항목 | 4회차 | 5회차 | 판단 |
|---|---|---|---|
| 0행 관측 턴 | 8(101·103·105·106 t1·106 t2·115·117·118) | **4**(103·115·117·118) | 149 W1(핵심어 직접 부분 일치 1순위) 효과 — 101·106 t1·105 85행 |
| 선별 밖 테이블 사용 턴 | 8 | **4**(108·110·111 → `tcdmsif80` · 117 → `tcdmsif52`) | 149 W2 일부 효과(104 gt82 · 105 80이 선별 안으로 들어옴) |
| 이름 칸 등호 0행 재생성(149 W3) | — | **발동 2턴**(115·118 · `retries` 1) | 동작 확인. 117은 처음부터 LIKE를 써서 발동 대상이 아니다 |
| 리포트 §8 분류 필요 컬럼 · 미분류 결과 열 | 19 · 30 | **10 · 14** | 149 W5 효과 |
| 110 행 · 키 | 1,762 · 1,559(`tcdmsif80` 단독) | **2,226** · 1,559(`80`+`72` 조인) | 행 증가 — §3 F3 |
| 106 t2 | t1 0행 → 0행 | t1 85행 → **10행** | **표본 승계** — §3 F1 |
| 근거 묶음 | 생성됨 | **관문 실패** | §3 F2 |

### 2. 턴별 진단

| 시나리오 | 질문 요지 | 실행 SQL 요지 | 행 | 진단 | 발견 |
|---|---|---|---|---|---|
| ITAM-101 | 통합인증 서비스 서버리스트 | `tcdmsif72` `그룹경로내용` OR `구성항목설명내용` `LIKE '%통합인증%'` | **85** | 149 W1의 1순위 모양 그대로다. 85행의 정답 여부는 탐침 P02·P03으로 확정해야 한다 | — |
| ITAM-102 | 통합인증 서비스 서버 담당 부서 | 101 조건 + `tcdmsbr01` LEFT JOIN(`담당부점코드`=`부점코드` · `그룹회사코드`) | 85 | 4회차와 같다. `담당부점코드`·`부점명`이 85행 모두 NULL이라 부서 답이 비었다 | F10 |
| ITAM-103 | 인터넷뱅킹 서버 중 6개월 안 EOS | 1순위 두 칸 `LIKE '%인터넷뱅킹%'` + `tcdmsif79` 세 칸 조인(`그룹회사코드`·`서버호스트명`·`IP주소내용`) + HW/SW 날짜 범위 | 0 | 4회차의 `용도내용`을 1순위 칸으로 바꿨는데도 0행이다. 원인 후보는 셋이다: 「인터넷뱅킹」 서버가 없음 · 72↔79 세 칸 조인 불일치 · 기간 안 EOS 없음. 109(79 단독)는 148행, 106 t2(79 IP 조건)는 10행이 나왔다 | **F5** |
| ITAM-104 | 서비스별 서버 수 | `tcdmsgt82` × `tcdmsif72` 두 칸 `LIKE CONCAT('%', 어플리케이션명, '%')` 집계 | 76 | 2순위 모양 그대로다. `server_count`는 general(149 W4 (4)) 등급이지만 분포는 반출되지 않았다 | — |
| ITAM-105 | 통합인증 서버 유지보수 계약 만료일 | ① 101 조건 + `tcdmsif80` 조인 + **`활성화여부 = '0'`** → 0행 ② 조건을 빼고 다시 → 85행 | 0 → **85** | 80이 선별 안으로 들어와 4단 조인이 사라졌다. 다만 ①은 코드값을 지어냈다(코드값 블록 없음 — P1 미재실행 · K4 활성 조건 규칙 위반). ②를 만든 재시도 경로는 미확정이다(149 W3 판정을 재현해 보니 이름 칸이 아니어서 발동 대상이 아니다). trace `retry_attempt` 표기가 실행 순서와 반대다(①=1 · ②=0) | F7 |
| ITAM-106 t1 | 통합인증 서비스 서버 목록 | 101과 같다 | **85** | — | — |
| ITAM-106 t2 | 그 서버들 지원 종료일 | `tcdmsif79` `WHERE IP주소내용 IN (IP 10개)` | **10** | **85대 중 IP 10개만 조회했다**(침묵 부분 답). 10개는 t1 결과 앞쪽 행의 IP와 같은 가짜 값이다(표본 5개 대조). 앞 턴 조건(서비스 핵심어)을 승계하지 않았다 | **F1** · F2 |
| ITAM-107 | 등록 서버 몇 대 | `COUNT(*)` `tcdmsif72` | 1 | **pass** | — |
| ITAM-108 | 이번 분기 유지보수 계약 만료 | `tcdmsif80` ⨝ `tcdmsif72`(호스트·그룹회사) + 분기 경계 CROSS JOIN | 52 | **pass**(키 52 = 오라클). 72 조인은 불필요하다(가이드 g03 「조인하지 않는다」 위반 · 무해). 선별(41·43·72·78)에 80이 없다. 반출 SQL의 날짜 형식 리터럴이 가짜 값 `'%P%f46'`으로 바뀌었다(§3 F8) | F3 · F4 · F8 |
| ITAM-109 | 6개월 안 EOS | `tcdmsif79` 단독 HW/SW OR | 148 | **pass**(K2 덮음) | — |
| ITAM-110 | 5년 이상 노후 서버 | `tcdmsif80` ⨝ `tcdmsif72`(호스트·그룹회사) · `취득년월일` 비교 | **2,226** | **pass**(키 1,559 = 오라클). 4회차는 80 단독 1,762행이었다. 불필요한 72 조인이 행을 더 불렸다. 선별(41·71·72)에 80이 없다 | **F3** · F4 |
| ITAM-111 | 취득금액 상위 3대 | `tcdmsif80` 단독 `ORDER BY 취득금액 DESC LIMIT 3` | 3 | **pass**(한 번에 · 4회차 물품 키 조인 0행 소멸). 선별(41·46·71·72)에 80이 없다 | F4 |
| ITAM-112 | OS별 서버 수 | `tcdmsif72` 집계 | 5 | **pass** | — |
| ITAM-113 | 가상 서버 목록 | `tcdmsif92` 전체 `LIMIT 10000` | 10,000(절단) | 해석은 3·4회차와 같다. `활성화여부`가 미분류(`<가림>`)라 코드값 판독은 불가하다 | F11 |
| ITAM-114 | 지난주 CPU 사용률 추이 | SQL 없음 | — | **정상**(D-319 ③) | — |
| ITAM-115 | 용도에 통합인증 적힌 서버 | ① `용도내용 = '통합인증'` 0행 → ② **W3 재생성** `LIKE '%통합인증%'` 0행 | 0 | W3이 정상 발동했다. 101과 대조하면 「용도 칸에는 없음」이 정답일 가능성이 높다(추정 — 탐침으로 확정). 응답 71자 · 진단 없음 | F12 |
| ITAM-116 | 분류 경로별 서버 수 | `그룹경로내용` 집계 | 749 | **pass** | — |
| ITAM-117 | 통합인증 업무 할당 스토리지 | `tcdmsif51` ⨝ `tcdmsif52`(`그룹회사코드`·`시리얼번호`) `업무명 LIKE '%통합인증%'` | 0 | 4회차 등호 → 5회차 LIKE로 바뀌었는데 그대로 0행이다. 52에 핵심어가 없는지, 51↔52 조인이 안 맞는지는 탐침 P04(146)와 신규 탐침으로 가른다. 선별(41·51·57)에 52가 없다. 응답 30자 | **F6** · F4 |
| ITAM-118 | 통합인증 업무 스토리지 쓰는 서버 OS | ① `업무명 = '통합인증 업무 스토리지'` 0행 → ② W3 재생성 `LIKE '%통합인증 업무 스토리지%'` 0행 | 0 | **W3 재생성이 문구 전체를 그대로 LIKE에 넣었다** — 힌트의 「핵심어」 지시를 LLM이 지키지 않았다 | **F6** |

### 3. 발견

| # | 발견 | 근거 | 처분 |
|---|---|---|---|
| **F1** | **지시어 후속 턴이 앞 턴 결과의 표본으로 좁혀진다(침묵 부분 답).** 106 t2가 85대 중 IP 10개만 `IN`으로 조회했다. 끊긴 지점은 셋이다(코드 실측). ① `context_resolver`는 엔티티를 결과 앞쪽에서 최대 20개(`_MAX_ENTITY_ROWS`)만 모은다. 식별 힌트(`hostname`·`ip` 등 영문)가 한글 `서버호스트명`에는 걸리지 않아 ITAM에서는 IP만 모였다. ② 2단 `intent_planner._build_context_block`은 지시어 턴에 `entities[:10]`을 「직전 대상 서버/장비」로 그대로 싣는다. 이것이 표본인지 표시하지 않고, 앞 턴 조건도 싣지 않는다. ③ 노드 경로의 `demonstrative_entity_scope`(`src/nodes/prompt_blocks.py`)는 `previous_result_count > 엔티티 수`이면 표본이라 보고 승계하지 않는다. 그런데 2단 계획 맥락에는 같은 가드가 없다 → **경로 비대칭(D-066)**. 146 W3은 **앞 턴 0행**일 때만 앞 질의·SQL을 계획 맥락에 실었다. 앞 턴에 행이 있는 경우는 다루지 않았다. 폴스타 공용 코드라 폴스타 「그 서버들」 후속에도 같은 모양이 날 수 있다(추정) | 106 t2 SQL(IP 10개) · `report.md` §7 「SQL 10 · 결과 10」 · `src/orchestration/intent_planner.py` 1083~1113행 · `src/nodes/context_resolver.py` 44·124·326행 · `src/nodes/prompt_blocks.py` 867~910행 | **W1** · G-1 |
| **F2** | **근거 묶음 누출 관문이 이 run의 가짜 IP를 실 IP로 본다.** 외부망 `run_evidence`(`scripts/itam_bench/knowledge.py`)는 `PiiVault.from_policy(policy)`로 **가짜 값 등록부 없이** 관문을 만든다. 그래서 `LeakGate._ip_left`가 106 t2 SQL의 IP 리터럴(반출 때 치환된 가짜 IP — `substitutions.yaml` 등재)을 잔존 IPv4로 막는다. run 반출 관문은 같은 trace를 통과시켰다. 스킬 절차 1단계(근거 묶음)가 막혀 W3(지식 자산)을 시작할 수 없다 | **재현**: `--evidence results/itam_bench/20261008-163344` → `turns[6].executed_sqls[0].sql · pii_regex` · `redact.py` 1928~1952행(`_ip_left` — `fakes is None or not fakes.is_fake`) · `substitutions.yaml` `values`에 해당 IP 등재 | **W2** (1) |
| **F3** | **`tcdmsif80`만으로 답할 질문에 서버 원장 `tcdmsif72`를 조인한다**(108·110). 가이드 g03은 「서비스 조건이 있을 때만 잇는다」고 적었는데도 그렇다. 110은 72의 호스트당 여러 행 때문에 행이 2,226으로 불었다(4회차 80 단독 1,762 · 키 1,559). keyset 판정은 통과지만 사용자에게는 중복 행이 보인다. 149 W1이 g05에 넣은 「서비스 조건이 필요하면 80↔72」 문장이 서비스 조건 없는 질문에도 조인을 부른 것으로 보인다(추정) | 108·110 SQL · 4회차 110 SQL(80 단독) · `testdata/itam_bench/closed/knowledge/guide.yaml` g03·g05 | **W3** · G-3 |
| **F4** | **선별 밖 사용 4턴** — 108·110·111은 `tcdmsif80`을, 117은 `tcdmsif52`를 선별 밖에서 썼다. 149 W2(80·gt82 `manages` 문구)로 104·105는 선별 안으로 들어왔다. 그러나 「유지보수 계약」「취득」「노후」 질문에서는 선별이 여전히 `tcdmsif41`·`43`·`71`을 고른다. 149 G-2의 「5회차 계수로 부족하면 다시 연다」 조건이 충족됐다. 다만 SQL 생성기가 가이드 문장으로 80을 바로 써서 오라클은 모두 통과했다 → 피해는 「열 목록 없이 가이드 문장에 기대는 생성」으로 한정된다 | `schema_context.presented_tables` ↔ `sql_analysis.tables` 차집합(§7) | **W4** · G-2 |
| **F5** | 103 0행의 원인이 미확정이다 — 4회차는 `용도내용`, 5회차는 1순위 두 칸을 썼고 둘 다 0행이다. 79와 세 칸(`그룹회사코드`·`서버호스트명`·`IP주소내용`)으로 잇는 조인이 맞는지 확인된 적이 없다 | 103 SQL 2회 · 109·106 t2 대조 | **W7** 탐침 |
| **F6** | 스토리지 업무 턴 0행이 2회 연속이다(117·118). ① 117은 핵심어 LIKE로도 0행이다 → 52 `업무명`에 핵심어가 없는지, 51↔52(`시리얼번호`) 조인이 안 맞는지 미확정이다(146 P04·P08 미실행). ② 118의 W3 재생성 힌트는 「핵심어를 뽑아라」고만 지시한다. LLM이 문구 전체(「통합인증 업무 스토리지」)를 LIKE에 넣어 **재생성이 무력화됐다**(D-004 — LLM 출력에 기댄 지점) | 117·118 SQL · `src/nodes/name_match_retry.py` `build_name_match_hint` | **W5**(힌트 결정적 핵심어) · **W7** 탐침 |
| F7 | 105가 코드값 블록 없이 `활성화여부 = '0'`을 지어냈다. K4 「코드값 블록이 없으면 활성 칸 조건을 쓰지 않는다」 위반이다. 근본 원인은 P1 미재실행(코드값 0)이다. 벤치 판정기(`unknown_code_value`)는 승인 코드값이 하나도 없으면 탐지하지 못한다(침묵). 같은 턴의 `retry_attempt` 표기가 실행 순서와 반대다 | 105 SQL · `sql_analysis.unknown_code_value: null` | **W2** (4)(5) · W8(P1) |
| **F8** | **반출 SQL의 날짜 형식 리터럴이 가짜 값으로 바뀐다**(108 `DATE_FORMAT(…, '%P%f46')` — `substitutions.yaml` 첫 항목). `_FORMAT`(`redact.py` 169행)은 `%`+글자와 구분자만 형식으로 본다. 그래서 숫자가 섞인 형식(추정 `'%Y%m01'` — 분기 첫날)은 값으로 가려진다. 반출 SQL을 판독할 수 없고, 다시 실행하면 의미가 바뀐다 | 108 SQL · `substitutions.yaml` · `redact.py` 169·895행 | **W2** (2) |
| **F9** | **반입 확인 지문이 줄바꿈(CRLF)에 민감하다.** 내부망 자산 파일은 CRLF 변환본이다. 그래서 `asset_fingerprints`(파일 바이트 SHA-256)가 외부망 커밋 지문과 한 번도 같지 않았다. 149 W6 「지문 대조」를 수행할 수 없었다. 외부망 커밋 파일을 CRLF로 바꿔 계산하면 5회차 = `8afb4a9`, 4회차 = `aa69985`로 맞는다 | **재현**: `git show <c>:<path> \| sed 's/$/\r/' \| shasum -a 256` · `scripts/itam_bench/catalog.py` `_fingerprint_file` | **W2** (3) |
| F10 | 102 부서 칸 85/85 NULL이 2회 연속이다. 149 F6의 탐침 P10은 계획에만 있고 파일이 추가되지 않았다 | 102 결과 열 `nulls` · `testdata/scenarios/oracles/`에 P10 없음 | **W7** 탐침 |
| F11 | 분류 필요 컬럼이 10개 남았다(`tcdmsif72` `그룹경로내용`·`용도내용`·`활성화여부` · `tcdmsif92` `가상화서버명`·`CPU용량`·`메모리용량`·`잠정스토리지용량`·`실행상태내용` · `tcdmsbr01.부점명` · `tcdmsgt82.어플리케이션명`). closed 정책은 80·79 두 테이블만 검토됐다. `활성화여부`가 `<가림>`이라 105·113의 코드값 사용을 판독할 수 없다 | `report.md` §8 · `column_policy.closed.yaml` | **W6** · G-4 |
| F12 | 0행 응답이 30~71자다(103·115·117·118) — 진단이 없다. 149 §10.8의 내부망 `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED` 확인은 회신되지 않았다. 문자열 조건 0건 진단은 149에서 별건으로 남겼다 | `response_chars` | W8 확인 · 별건 유지 |
| **F14** | **벤치가 조인을 판정하지 못한다**(v1.3 · 조인 시나리오 보강 중 실측). ① 5회차 카탈로그는 관계 0건 · `same_key_groups` 0개 · 테이블 `key` 0개다. 그래서 `join_key_partial`(`judge.py` 155행)은 폐쇄망에서 한 번도 켜질 수 없다. ② `key_columns`는 SQL이 그 칸을 **썼는지**만 본다(`judge.py` 643행). `52.호스트명 = 72.서버호스트명`처럼 칸 이름이 다른 조인 쌍을 맞게 이었는지는 볼 수 없다. ③ 기존 18건 모두 `gold_tables`가 비어 테이블 정밀도·재현율이 「—」다. ④ 로더는 `gold_tables`·`key_columns` 이름을 **컬럼 정책**으로 검사한다(`catalog.py` 453~477행). closed 정책에는 80·79 두 테이블만 있다. 그래서 51·52·58·89·92·11·12 등을 쓰는 조인 시나리오는 테이블을 선언하는 순간 린트에서 막힌다. ⑤ 모의 DB(3308)는 채택 관계(`kind: p1`)가 있을 때만 조인 키 값을 맞춰 만든다. 폐쇄망 관계는 0건이라 모의 DB에서 조인 SQL은 **실행 여부만** 보이고 건수는 근거가 안 된다(`testdata/itam_closed_sim/README.md` 「합성 행 값 규칙」 1) | `results/itam_bench/20261008-163344/schema_catalog.yaml` `summary.relations: {}` · `same_key_groups: []` · `scripts/itam_bench/judge.py` 155·643행 · `catalog.py` 453~477행 · `column_policy.closed.yaml` 테이블 2개 | **W2** (6)(7) · **W7** P09~P18 · **W9** 조인 군 |
| F13 | **P1 재실행 4회 연속·탐침 2회 연속 미이행** — F5·F6·F7·F10의 원인 확정이 모두 여기에 걸려 있다 | `run.json` `assets.p1` · `oracle_check/` 없음 | **W8**(내부망) |

### 4. 결정 충돌 검토(작업 전 필수)

| 결정 | 이 계획과의 관계 | 충돌 |
|---|---|---|
| D-004 | W1은 표본 판정을 결정적 신호(`previous_result_count` > 엔티티 수)로 한다. W5는 핵심어 후보를 코드가 뽑아 힌트에 싣는다 | 없음(취지 이행) |
| D-055(146 W3 부기) | W1은 0행 앞 턴 승계 기록(`zero_row_turn`)을 「행이 있는데 표본인 앞 턴」으로 넓힌다. 고지 문구는 같은 부류다 | 없음 · 착지 때 부기 |
| D-066 | W1은 노드 경로 가드(`demonstrative_entity_scope`)와 같은 판정을 2단 계획 맥락에 넣어 비대칭을 없앤다 | 없음(대칭 회복) |
| D-153 후속1 | 「엔티티는 표본이지 스코프가 아니다」(같은 함수 주석)의 지시어 턴판 | 없음 |
| D-297(149 W3 부기) | W5는 재생성 힌트 문구만 바꾼다. 발동 조건·1회 상한은 그대로다 | 없음 · 착지 때 부기 |
| D-301 ② | W6 등급은 사용자 검토(G-4)를 거친다. W2 (1)은 관문 규칙을 바꾸지 않는다 — 이미 반출된 가짜 값 목록을 등록부로 읽을 뿐이다 | 없음 · G-4 답에 따라 부기 |
| D-308 | W4는 정의 데이터(G-2 (b)) 또는 후보 줄(G-2 (a))만 바꾼다. 선별 사다리는 바꾸지 않는다 | 없음 |
| D-316 | 지식 자산은 스킬(`/itam-knowledge`)로 쓴다. K2는 오라클 근거가 있을 때만 쓴다 | 없음 |
| D-321 | W2 (1)은 `substitutions.yaml`(대응표 없는 가짜 값 목록)을 읽기만 한다. 대응표·난수 규칙은 바꾸지 않는다 | 없음 |

### 5. 작업 단위

순서:
- **W2 (1) → W3**: 근거 묶음이 있어야 스킬을 시작할 수 있다.
- **W9 → W3 K2**: 신규 프롬프트를 먼저 확정해야 K2 예시가 그 프롬프트와 겹치지 않게 쓸 수 있다.
- **W2 (6)(7) → W9**: 조인 군 시나리오는 로더가 정책 밖 테이블 이름을 받아야 쓸 수 있고(7), 조인 판정 칸이 있어야 의미가 있다(6).
- W1·W4·W5·W6·W7은 서로 독립이다.

#### W1 — 지시어 후속 턴 표본 승계 교정(F1 · G-1 · 본체)

1. **조사(재현 먼저)**: 체크포인터를 붙인 목 LLM 2턴으로 재현한다(1턴 결과 21행 이상 → 2턴 「그 서버들 …」). 그 뒤 2단 계획 맥락에 엔티티 표본이 「직전 대상」으로 실리는지 확인한다. 노드 경로(3·4단)는 `demonstrative_entity_scope` 가드로 승계를 안 하는데, 그때 앞 턴 조건을 다시 거는 수단이 있는지도 같이 확정한다.
2. **교정(G-1 (a) — 확정)**:
   - (a-1) `context_resolver`는 앞 턴 결과가 엔티티 상한을 넘으면(표본) 앞 턴 원 질의·실행 SQL을 기록한다. 146 W3의 `zero_row_turn` 기록을 행이 있는 표본 턴까지 넓힌다(키는 같은 부류 · 이름은 착수 때 정한다).
   - (a-2) `intent_planner._build_context_block`은 지시어 턴에 표본 엔티티를 「직전 대상 서버/장비」로 싣지 않는다. 대신 「직전 결과 N건(표본만 보존)」과 앞 질의 조건 승계 지시를 싣는다. 146 W3 0행 분기와 같은 문형이다.
   - (a-3) 실행 뒤 결정적 고지를 붙인다. 실행 SQL이 앞 턴 조건을 하나도 잇지 않았으면 기존 고지(146 W3 「넓힘」)를 낸다. 앞 턴 표본 값만 `IN`으로 걸었으면 「앞 결과 N건 중 일부만 조회했다」를 낸다.
3. **엔티티 힌트**: 식별 힌트를 한글 식별 칸(`서버호스트명`)까지 넓힐지는 착수 때 판단한다. 표본 판정이 맞으면 결과는 같다 — 넓히면 단수 지시어 승계(`_inject_demonstrative_hostname`)에도 영향이 있다. 이번 범위에서는 **변경하지 않는 것**을 기본으로 둔다.
4. 테스트: 목 LLM 2턴 3종(앞 턴 0행 · 20행 이하 · 21행 이상) × 2단·노드 경로. 폴스타 대칭 1종(같은 모양 · D-066). 21행 이상에서 계획 맥락에 표본 값이 「직전 대상」으로 실리지 않음을 단언한다.
5. 매뉴얼(D-255): 고지 문구가 사용자에게 보이므로 사용자 매뉴얼 U-34(146 W3 주의 문구) 옆에 1줄 추가 여부를 판단한다.

#### W2 — 벤치 반출·근거·판정 교정(`scripts/itam_bench/` · 본체 0)

| # | 내용 | 파일 | 확인 |
|---|---|---|---|
| (1) | `run_evidence`가 `<run>/substitutions.yaml`의 `values`를 가짜 값 등록부(발급된 값으로만 표시 · 원값 0)로 읽어 `PiiVault`에 넘긴다. 파일이 없으면 종전대로 둔다(닫힌 쪽). 등록부에 넣는 생성자 하나(`FakeValues` · 착수 때 시그니처를 정한다)를 둔다 | `knowledge.py` `run_evidence` · `substitute.py` | 5회차 `--evidence` 관문 통과 · 등록부에 없는 IP를 넣은 픽스처는 여전히 `pii_regex`로 막힘 · 4회차 묶음 비트 동일 |
| (2) | 날짜 형식 리터럴 인식을 넓힌다. 아래 조건을 모두 만족하면 형식으로 남긴다. 그 밖은 종전대로 가린다(F8) — 4자리 이상 숫자는 값일 수 있어 막는다 | `redact.py` `_FORMAT` · `_trivially_safe` | 108 모양 SQL → 형식 원문 · `'20261001'`·`'%통합인증%'` 판정은 종전대로 |
| (3) | 자산 지문은 줄바꿈을 정규화(CRLF → LF)한 바이트로 계산한다. 리포트 머리에 「지문 정규화 v2」 표지를 단다. 4·5회차 run과 비교할 때는 1회 불연속이 생긴다 — `--compare`가 표지가 다르면 「지문 방식 변경」으로 적는다 | `catalog.py` `_fingerprint_file` · `report.py` · `__main__.py` 비교부 | 외부망 `8afb4a9` 파일의 LF·CRLF 지문이 같아짐 |
| (4) | `retry_attempt` 표기가 실행 순서와 어긋나는 원인을 확정하고 고친다(105). 원천이 서버 이벤트면 벤치 쪽 정렬만 고친다 | `__main__.py` trace 추출부 | 105 모양 → ①=0 · ②=1 |
| (5) | 판정기: 승인 코드값이 없는 칸(정의·설명이 코드·여부로 표시한 칸)에 리터럴 등호를 쓰면 `unknown_code_value`에 「승인 코드값 없음」을 표시한다(관측 · 판정 불변) | `judge.py` | 105 ① → 표시 · 113(조건 없음) → 없음 |
| (6) | **조인 키 판정(F14 ①②)**: 시나리오·턴 `expect.join_keys`(신규 · 선택)를 받는다. 형식은 `[["표.칸", "표.칸"], …]` 쌍 목록이다. 판정기는 SQL이 쌍의 두 테이블을 함께 쓸 때 그 쌍을 잇는 등호(`a.칸 = b.칸` · 이름이 다른 칸 포함 · `USING` · `EXISTS` 하위 질의 안 등호 포함)가 없으면 `join_key_miss`를 단다. 별칭은 `FROM`·`JOIN` 절의 `표 [AS] 별칭`으로 테이블까지 해석한다. 해석하지 못하면 칸 이름 쌍으로만 판정하고 `join_alias_unresolved`를 표시한다. `그룹회사코드`는 모든 테이블에 있으나 정의 `related`에 없으므로 요구하지 않는다. 대신 이었는지를 `join_group_company`(불린)로 기록만 한다. 리포트에 「조인 판정」 절을 둔다(턴별 테이블 정밀도·재현율 · `join_key_miss` · 선별 밖 테이블). 모두 관찰 표지이고 오라클 판정은 바꾸지 않는다 | `catalog.py` 로더 · `judge.py`(`_JOIN_EQUAL` 재사용) · `report.py` | 221 모양(이름 다른 칸) 맞음/빠짐 · 222 모양(세 테이블 · 같은 이름 칸 `시리얼번호`) 별칭으로 쌍 구별 · 229 모양(`NOT EXISTS`) 인식 · `join_keys` 없는 기존 18건 리포트 비트 동일 |
| (7) | **로더 이름 검사 출처(F14 ④)**: `gold_tables`·`key_columns`·`join_keys`의 이름을 「정책 ∪ closed 스키마 스냅숏(`testdata/itam_bench/closed/itam_schema.json` — git 추적 · 이름만)」으로 검사한다. 정책 밖 칸은 반출에서 종전대로 `unclassified`로 가려지므로 반출 범위는 변하지 않는다(D-301). 판정은 이름과 행 수만 쓴다. 파일 머리 `require_judgement: true`(r6 전용)이면 `gold_tables`·`key_columns`가 빈 턴을 거절한다(`no_data: true` 턴 제외) | `catalog.py` `load_scenarios` · `__main__.py` `_load` | 정책 밖 테이블 `tcdmsif52`를 쓴 시나리오 통과 · 스냅숏에 없는 이름은 여전히 거절 · `require_judgement` 파일의 빈 판정 칸 거절 · 기존 세트 비트 동일 |

(2)의 형식 판정 조건:
- `%`+글자 지시자가 2개 이상이다.
- 나머지는 구분자와 2자리 이하 숫자다.

누출 관문(`LeakGate`) 규칙은 바꾸지 않는다. 각 항목은 관문 통과 테스트를 함께 둔다.

#### W3 — 지식 자산 교정(스킬 `/itam-knowledge results/itam_bench/20261008-163344` · F3 · G-3 · W2 (1) 뒤)

| 자산 | 항목 | 고칠 내용(값 없이) |
|---|---|---|
| K1 | g03 · g05 | 서비스 조건 없는 서버 단위 취득·계약·경과 연수 질문은 `tcdmsif80` 하나로 답하고 `tcdmsif72`를 조인하지 않는다. 이 문장을 g05 「서비스 조건이 필요하면 …」 앞에 둔다. 「72를 이으면 호스트당 행이 불어난다(관측)」를 사실 문장으로 남긴다(회차 서술은 YAML 주석) |
| K4 | 마트 단독 규칙(신규 1문장) | 위 규칙을 규칙 섹션에도 둔다(펜스 없이) |
| K2 | 변형 예시 2건(G-3 (a)) | 오라클 108·110 모양을 **질문 문구·기간·연수를 바꿔** 쓴다(80 단독 · 서버별 한 행). 기존 시나리오와 W9 신규 프롬프트 어느 쪽의 문구·기간·연수와도 겹치지 않게 쓴다 — 오라클 턴을 예시가 그대로 덮으면 벤치가 일반화 대신 회상을 잰다. W9 겹침 가드 테스트가 K2 질문도 함께 검사한다 |
| K3 | 설명 | `tcdmsif72.담당부점코드`(관측: 서비스 서버에서 비어 있음 — 부서는 `tcdmsif80` `담당부점명` 후보(추정 · 탐침 W7 P05)) |

절차: 143 §4.2.1 B3~B7과 같다(모의 DB 실행 검증 → 빌드 → 선별 후보 줄·프롬프트 차이 확인). 내부망 Redis에 이미 있는 설명은 덮지 않는다(149 §10.8) → K3는 W8에서 kept 로그로 확인한다.

#### W4 — 선별 보강(F4 · G-2)

- **G-2 (b) — 확정**:
  - `testdata/itam_bench/closed/table_definitions.yaml` `tcdmsif41`·`tcdmsif43`의 `manages`에 「서버 단위 취득·계약 질문은 `tcdmsif80`」 한 구절을 넣는다(값 없이 · 정의는 사람 검토 대상 — D-308).
  - `tcdmsif52`의 `manages`에 「업무에 할당된 스토리지」 질문 유형을 드러낸다.
  - 빌드한 뒤 선별 후보 줄 차이가 이 세 줄뿐인지 확인한다.
- (a)를 고르면 `_candidate_line`에 `notes` 첫 문장을 싣는다(`src/nodes/table_selection.py` · 정의 없는 DB 비트 동일 · 폴스타 영향 확인).
- 완료 판정: 6회차에서 선별 밖 사용 턴을 다시 센다(5회차 4).

#### W5 — 이름 칸 재생성 힌트에 결정적 핵심어(F6 ② · 본체)

1. `build_name_match_hint`에 등호·`IN` 리터럴에서 뽑은 **핵심어 후보**를 싣는다. 뽑는 규칙은 아래와 같다.
   - 일반어(서비스·업무·시스템·스토리지·서버 — K4 문장과 같은 목록 · 모듈 상수 1곳)와 공백 경계 조사를 뺀다.
   - 남은 가장 긴 토큰 하나를 고른다.
   - 리터럴은 사용자 질문에서 온 값이다. 힌트는 LLM 프롬프트에만 실리고 로그에는 칸 이름만 남긴다(기존과 같다).
   - 후보를 뽑지 못하면 종전 문구를 쓴다.
2. 발동 조건·1회 상한·실패 복원은 그대로다.
3. 테스트: 118 모양(문구 전체) → 후보 「통합인증」 · 일반어만 있는 리터럴 → 후보 없음(종전 문구) · 폴스타(이름 칸 표시 없음) 비트 동일.

#### W6 — closed 정책 칸 보강(F11 · G-4 권고안 확정)

리포트 §8의 10칸에 아래 등급을 붙인다(G-4 — 권고안대로 확정).

| 칸 | 권고 등급 | 이유 |
|---|---|---|
| `tcdmsif72.활성화여부` · `tcdmsif92.실행상태내용` | general | 1자·상태 코드 — 105·113 코드값 판독에 필요 |
| `tcdmsif92` `CPU용량`·`메모리용량`·`잠정스토리지용량` | general | 용량 수치(80의 용량 칸과 같은 부류) |
| `tcdmsbr01.부점명` · `tcdmsgt82.어플리케이션명` | general | 조직·서비스 이름(80 `담당부점명`은 이미 general) — 104·102 판독 |
| `tcdmsif72.용도내용` | ~~general~~ → **free_text**(v1.5) | 짧은 용도 분류어(관측 길이 2~4) — 그러나 같은 이름의 if73·74 값은 관측이 없어 확인 전까지 free_text(보안 감사 L2 권고 · 사용자 2026-10-12) |
| `tcdmsif72.그룹경로내용` | identifier | 경로 안에 업무 이름이 들어간다 — run 일관 가짜 값(구분자 `>` 보존) |
| `tcdmsif92.가상화서버명` | identifier | 호스트 이름류 |

가드 테스트(정책 칼럼 실존)는 그대로 통과해야 한다. 착지 때 D-301 부기에 칸 이름만 적는다.

#### W7 — 탐침 추가(F5 · F6 · F10 · `testdata/scenarios/oracles/ITAM-150-PNN.mariadb.sql` + `scenarios.closed.probe.yaml`)

146 탐침 규칙을 따른다:
- 머리 주석을 단다. SELECT 전용 · 키 행을 반환하고 · `LIMIT 10000`을 둔다.
- 핵심어는 시나리오 원문의 말만 쓴다.
- 루프백 IP 표기를 쓰지 않는다(149 §10.1).

| ID | 묻는 것 | 대상 |
|---|---|---|
| P01 | 「인터넷뱅킹」이 72 `그룹경로내용`·`구성항목설명내용`에 부분 일치로 있는 서버 수 | 103 |
| P02 | P01 서버 중 79와 세 칸(`그룹회사코드`·`서버호스트명`·`IP주소내용`)으로 이어지는 수 · 날짜 조건 없음 | 103 |
| P03 | 72 전체 ↔ 79의 연결 수 — 세 칸 대 `서버호스트명`만(키 선택 판정) | 103 · 106 t2 |
| P04 | 「통합인증」 서버(101 조건) 중 79로 이어지는 서버 수 | 106 t2 정답 범위(오라클 전환 재료) |
| P05 | 「통합인증」 서버 중 `tcdmsif80` `담당부점명`이 비지 않은 수 | 102(149 P10 대체) |
| P06 | `tcdmsif51` ↔ `tcdmsif52`(`그룹회사코드`·`시리얼번호`) 연결 수 | 117 |
| P07A·B·C | 「무인공과금」「법원공탁금」「그린뱅크」가 72 `그룹경로내용`·`구성항목설명내용`에 부분 일치로 있는 서버 수(이름마다 1개) | W9 서비스 턴의 0행 판별 |
| P08 | 「그린뱅크」가 `tcdmsif52` `업무명`에 부분 일치로 있는 호스트 수 | W9 ITAM-211 |

**조인 연결 탐침(v1.3 · F14 · W9 조인 군).** 모두 **건수만** 돌려준다(값 0). 연결 수는 「양쪽 키가 같은 쌍 수」와 「한쪽 테이블 전체 수」를 함께 낸다. 정의 키 전부와 일부(또는 `가상화관리서버ID`·`그룹회사코드` 추가)를 나란히 세어 **키 선택 판정 재료**로 쓴다. 키가 조인 쌍으로 확정되면 그 조인 턴을 7회차에 오라클로 올린다.

| ID | 연결 | 세는 것 | 대상 |
|---|---|---|---|
| P09 | 79 ↔ 80 | `서버호스트명`·`IP주소내용` 연결 수 · 79 전체 | 219(마트로 지원 종료 잇기) |
| P10 | 51 ↔ 58 | `시리얼번호` 연결 수 · 58 전체 · `그룹회사코드` 추가 시 | 220 · 222 |
| P11 | 52 ↔ 72 | `호스트명` = `서버호스트명` 연결 호스트 수 · 52 고유 호스트 수 | 221(이름 다른 칸) |
| P12 | 92 ↔ 89 | `호스트참조ID`만 대 `가상화관리서버ID` 추가 · 89 안에서 `호스트참조ID`가 관리서버 여럿에 걸친 수 | 223 · 224(복합 키 함정) |
| P13 | 89 ↔ 86 | `군집참조ID`만 대 `가상화관리서버ID` 추가 | 224 |
| P14 | 11 ↔ 12 | `계약번호` 연결 수 · 11 전체 | 225 |
| P15 | 41 ↔ 46 | `물품분류번호` 연결 수 · 41 전체 | 226 |
| P16 | 31 ↔ 41 | `물품분류번호`·`물품고유번호` 연결 수 · 31 전체 · 80 ↔ 41 같은 키 연결 수(72 ↔ 41 0행 3회 대조) | 228 |
| P17 | 21 ↔ 22 | `IP주소`·`IP주소일련번호` 연결 수 · 21 전체 | 231 |
| P18 | 72 ↔ 75 | `서버호스트명`·`IP주소내용` 연결 수 · 75 고유 서버 수 | 232 |

72 ↔ 79는 기존 P03(세 칸 대 `서버호스트명`만)이 맡는다. 72 ↔ `tcdmsbr01`은 P05가 맡는다.

**(착지 교정 — 파일 분할)** `--check-oracle`은 오라클마다 행 수만 남기므로 「COUNT 여러 칸 1행」으로는 내부망에서 건수가 사라진다. 그래서 측정마다 SQL 하나로 나눴다 — P03A(세 칸)·P03B(호스트만) · P05A(80 `담당부점명`)·P05B(72 → `tcdmsbr01` `부점명`) · P09~P18은 A·B(키 전부·일부 또는 키 추가)·C(분모 전체)(P12·P16은 D까지). 건수 탐침은 행마다 상수 1(값 칸 없음 · 행 수 = 건수)이고, 연결 수는 분모 테이블 행 중 짝이 있는 행 수(`EXISTS`)다(조인 배수는 P12C가 직접 센다). P01~P08은 146 선례대로 키 행(`SELECT DISTINCT 키`)이다. 모두 44파일. v1.5에 P19A(`tcdmsif80` 전체 행)·P19B(고유 `서버호스트명`)를 더해 46파일이다 — r6 202·203·212 오라클(`COUNT(DISTINCT 서버호스트명)`)과 시스템이 흔히 쓰는 `COUNT(*)`가 같은지 확인한다(다르면 서버 수 정의를 문서화).

모의 DB(3308)에서 구문·실행만 확인한다. `--evidence` 관문 통과도 함께 확인한다.

#### W9 — 6회차 신규 사용자 프롬프트 세트(사용자 지시 2026-10-08 · G-5)

**목적**: 기존 18건(ITAM-101~118)은 5회 반복됐다. 지식 자산(K1·K2·K4)과 정의 문구도 그 18건의 실행 결과를 근거로 고쳐 왔다. 그래서 같은 프롬프트의 통과율은 「일반화」보다 「그 질문에 맞춘 교정」을 잰다. 6회차는 **처음 보는 사용자 프롬프트**로만 돌린다.

1. **파일**: `testdata/itam_bench/scenarios.closed.r6.yaml`(신규) · ID `ITAM-2NN` · 오라클 SQL `testdata/scenarios/oracles/ITAM-2NN.mariadb.sql`. 기존 `scenarios.closed.yaml`(101~118)은 지우지 않고 회귀 세트로 보존한다. **6회차 run에는 쓰지 않는다.**
2. **작성 원칙**:
   - 기존 18건·K2 질문과 문구가 겹치지 않는다. 같은 범주는 기간·연수·건수·집계 축을 바꾼다. 새 의도도 섞는다.
   - 135 G-1 규칙을 따른다. 자산관리 포탈 사용자가 칠 법한 말로 쓴다. 테이블·컬럼·SQL 용어·사람 이름을 넣지 않는다(로더 린트).
   - 서비스 이름은 G-5 답 3개(무인공과금·법원공탁금·그린뱅크)만 쓴다. 기존 「통합인증」「인터넷뱅킹」은 새 세트에 쓰지 않는다. 시나리오 파일은 커밋 대상이므로 이 세 이름 밖의 업무 이름은 넣지 않는다(D-301).
   - 이번 계획의 교정점을 겨냥한 함정을 넣는다:
     - 21건 이상 앞 결과에 대한 지시어 후속(W1)
     - 서비스 조건 없는 마트 단독 질문(W3)
     - 스토리지 업무(W5)
     - 코드값 지어냄(F7)
     - 범위 밖 사용률(D-308)
     - **조인**(v1.3 — 아래 「조인 지도」): 두 테이블 조인 · 세 테이블 사슬 · 조인 뒤 집계 · 없는 쪽 찾기(`NOT EXISTS`·`LEFT JOIN … IS NULL`) · 이름이 다른 키 칸 · 복합 키 일부만 잇기 · 조인이 필요 없는 마트 단독 대조군 · 다중 턴 조인
3. **오라클**:
   - 정답 모양이 이미 정해진 범주만 오라클로 쓴다 — `tcdmsif80` 단독 계약·취득·노후 · `tcdmsif79` 지원 종료 · `tcdmsif72` 집계.
   - 날짜는 앵커 자리표를 쓰고 값 리터럴은 쓰지 않는다. 비교 방식은 기존 4종(`value`·`keyset`·`count`·`argmax`)만 쓴다.
   - 서비스 개념·코드값·다중 턴 1턴은 관찰(`observe`)로 둔다.
   - **조인 턴은 6회차에 관찰로 둔다.** 키 연결이 내부망에서 한 번도 확정되지 않았고(72 ↔ 79 0행 · 72 ↔ 41 0행 3회), 모의 DB는 조인 건수의 근거가 못 된다(F14 ⑤). 오라클을 지금 쓰면 우리가 추정한 키를 정답에 박는 셈이다. 탐침 P09~P18이 연결을 확정한 조인만 7회차에 오라클로 올린다(후보: 220·223·225·229).
   - 모의 DB(3308)에서 구문·실행을 확인한다. 내부망 `--check-oracle`에서 0행·오류가 나면 그 턴은 `observe`로 되돌린다(146 W5 (1)과 같다).
4. **겹침 가드**: `tests/test_scripts/`에 테스트 1개를 둔다. r6 프롬프트가 기존 closed 시나리오·K2 질문(`examples.yaml`)과 정규화 문자열이 같거나, 낱말 Jaccard 0.6 이상이면 실패한다. 임계는 착수 때 기존 세트 안의 최대 유사도를 재서 정한다.
5. **비교·근거 묶음**:
   - 5→6회차는 범주별 지표로만 비교하고, 리포트·착지 보고에 「프롬프트 세트 교체」를 적는다. **(착지 교정)** 「`--compare`는 시나리오 파일이 다르면 run을 짝짓지 않는다」는 전제는 실측과 달랐다 — `compare_runs`는 파일과 무관하게 (ID, 턴)으로 짝지었다. 그래서 두 run의 `scenario_file`이 다르면 맨 위에 「프롬프트 세트 교체」 고지와 「범주별 판정」 표(pass/fail/hold/observe)를 내도록 고쳤다(같으면 비트 동일).
   - 근거 묶음은 `--evidence <run> --scenarios testdata/itam_bench/scenarios.closed.r6.yaml`로 만든다.
6. **사용자 검토**: 초안은 사용자 검토 뒤 확정한다(135 G-1 선례). 프롬프트는 **32건**(단일 테이블·서비스·코드·범위 밖 18 + 조인 군 14)이며 다중 턴 3건을 포함한다.
7. **판정 칸 필수(v1.3 · F14 ③)**: r6의 모든 턴은 `gold_tables`와 `key_columns`를 단다(범위 밖 216 제외). 조인 턴은 `join_keys`(W2 (6))도 단다. 로더 린트가 빈 칸을 거절하도록 r6 파일에만 `require_judgement: true`(파일 머리 · 기존 세트는 비트 동일)를 둔다. 아래 표의 테이블·키는 정의 `related`와 컬럼 목록(`itam_schema.json`)으로 확인한 이름이다. 같은 답을 내는 다른 경로가 있으면 「허용」에 적는다. 허용 경로는 `gold_tables` 후보 묶음으로 둔다 — 형식은 착수 때 W2 (6)과 함께 정한다.

**조인 지도**(정의 `related` · `itam_schema.json` 컬럼으로 확인 · 2026-10-08). 「관측」은 1~5회차 실행 SQL에서 본 것이다. 키는 정의에 적힌 칸이고, 모든 테이블에 공통인 `그룹회사코드`는 따로 적지 않는다.

| 조인 | 키(정의) | 관측 | 탐침 |
|---|---|---|---|
| 72 ↔ 79 | `서버호스트명`·`IP주소내용` | 서비스 조건과 함께 0행(103 · 2회) | 150 P02·P03 |
| 72 ↔ 80 | `서버호스트명`·`IP주소내용` | 행 있음 · IP를 빼면 행이 불어남(110) | — |
| 79 ↔ 80 | (정의 없음 — 둘 다 72와 같은 키) | 미관측 | P09 |
| 72 ↔ 75 | `서버호스트명`·`IP주소내용` | 미관측 | P18 |
| 72 ↔ 41 | `물품분류번호`·`물품고유번호` | 0행 3회 · 41 정의 「값 겹침 미확인」 | P16(80 ↔ 41 대조) |
| 72 ↔ `tcdmsbr01` | `담당부점코드` → `부점코드`(br01 정의 「`~부점코드`를 부점명으로」) | 행 있음 · 부서 칸 전부 NULL(102) | P05 |
| 52 ↔ 72 | `호스트명` ↔ `서버호스트명`(이름 다름) | 핵심어 조건과 함께 0행 | P11 |
| 51 ↔ 52 · 51 ↔ 58 | `시리얼번호` | 51 ↔ 52 핵심어와 함께 0행 · 51 ↔ 58 미관측 | P06 · P10 |
| 92 ↔ 89 | `호스트참조ID`(공통 칸 `가상화관리서버ID` — 참조 ID가 관리서버별일 수 있음(추정)) | 미관측 | P12 |
| 89 ↔ 86 | `군집참조ID`(같은 추정) | 미관측 | P13 |
| 11 ↔ 12 | `계약번호` | 미관측 | P14 |
| 41 ↔ 46 | `물품분류번호` | 미관측 | P15 |
| 31 ↔ 41 | `물품분류번호`·`물품고유번호` | 미관측 | P16 |
| 21 ↔ 22 | `IP주소`·`IP주소일련번호` | 미관측 | P17 |
| `tcdmsgt82` → 72 | 키 없음 — 이름 칸 부분 일치(gt82 정의) | 76행(`LIKE CONCAT`) | — |

초안 목록(문구는 검토 대상 · 서비스 이름은 G-5 답 — 무인공과금·법원공탁금·그린뱅크). 표에서 테이블 번호는 `tcdmsif` 접두를 뺐다(`br01` = `tcdmsbr01`). 「키」 칸은 조인 턴의 `join_keys`이고, 그 밖 턴은 `key_columns`의 핵심 칸이다.

**A. 단일 테이블·서비스·코드·범위 밖(18 · v1.2 초안에 판정 칸 추가)**

| ID | 범주 | 프롬프트 초안 | 테이블(허용) | 키 | 판정 | 겨냥 |
|---|---|---|---|---|---|---|
| ITAM-201 | maintenance | 다음 달 말까지 유지보수 계약이 끝나는 서버 목록 보여줘 | 80 | `유지보수계약종료년월일` | oracle keyset | 마트 단독 · 기간 변형 · **72 조인 없음**(W3) |
| ITAM-202 | maintenance | 유지보수 계약이 이미 끝난 서버가 몇 대야? | 80 | `유지보수계약종료년월일` | oracle value | 마트 단독 · 과거 경계 |
| ITAM-203 | lifecycle | 자산관리에서 취득한 지 8년 넘은 서버 몇 대인지 알려줘 | 80 | `취득년월일` 또는 `경과년수` | oracle value | 연수 변형 · 집계 |
| ITAM-204 | amount | 자산관리에 등록된 서버 취득금액 총합 알려줘 | 80 | `취득금액` | oracle value | 합계(argmax 아님) |
| ITAM-205 | eol | 올해 안에 하드웨어 지원이 끝나는 서버 있어? | 79 | `하드웨어지원종료년월일` | oracle keyset | HW 단독 · 연말 경계 |
| ITAM-206 | spec | 운영체제 버전별로 서버 몇 대씩인지 보여줘 | 72(80) | `운영체제버전내용` | oracle count | 집계 축 변경 · 오라클 테이블은 착수 때 K2 `k2-server-count-by-os`와 맞춘다 |
| ITAM-207 | inventory | 자산관리 기준으로 설치 지역별 서버 대수 알려줘 | 72(80) | `설치지역내용` | oracle count | 새 집계 축(칸은 72·80 모두 있음 — 확인함) |
| ITAM-208 | service | 무인공과금 쪽 서버 IP 목록 뽑아줘 | 72 | `IP주소내용` · 이름 칸 | observe | 서비스 연결 일반화 |
| ITAM-209 | service | 무인공과금 서버 중에 1년 안에 지원 끝나는 거 있어? | 72 · 79 | 72.`서버호스트명`=79.`서버호스트명` · 72.`IP주소내용`=79.`IP주소내용` | observe | **2테이블 조인** · 103 원인 대조(P02·P03) |
| ITAM-210 | service | 법원공탁금 업무에 쓰는 서버들 운영체제 버전 알려줘 | 72 | `운영체제버전내용` · 이름 칸 | observe | 다른 서비스 핵심어 |
| ITAM-211 | service | 그린뱅크 업무에 할당된 스토리지 용량 합계 알려줘 | 52 | `업무명` · `장비할당량` | observe | 스토리지 업무(W5) · **51 조인 불필요** |
| ITAM-212 | multiturn | t1 도입한 지 6년 넘은 서버 목록 보여줘 → t2 그 서버들 중에 유지보수 계약이 아직 남아 있는 건 몇 대야? | 80 / 80 | `취득년월일` / `유지보수계약종료년월일` | t1 observe · **t2 oracle value** | **21건 이상 표본 승계(W1)** |
| ITAM-213 | multiturn | t1 법원공탁금 업무를 돌리는 서버들이 뭐가 있는지 알려줘 → t2 그 서버들 담당 부서도 같이 알려줘 | 72 / 72 · br01(허용: 72 · 80) | t2: 72.`담당부점코드`=br01.`부점코드` · 허용 경로는 72 ↔ 80 뒤 80 `담당부점명` | observe | 서비스 결과 승계 · **이름 다른 키** · 부서 칸 경로(P05로 확정) |
| ITAM-214 | code_value | 자산관리에서 사용 중지된 서버 목록 보여줘 | 72 | `활성화여부` | observe | 활성·상태 코드 지어냄(F7) |
| ITAM-215 | code_value | 가상 서버가 몇 대인지 알려줘 | 92(80) | `활성화여부` 또는 80 `가상화서버매핑여부` | observe | 113과 다른 집계형 |
| ITAM-216 | out_of_scope | 자산관리에서 어제 메모리 사용률 높은 서버 알려줘 | — | — | observe(`no_data: true`) | 사용률은 관측 DB(D-308) |
| ITAM-217 | service | 구성 설명에 그린뱅크가 들어간 서버 몇 대야? | 72 | `구성항목설명내용` | observe | 칸 지목 · 0행 정직성 |
| ITAM-218 | inventory | 서버 분류 경로 중에 서버가 제일 많은 곳 알려줘 | 72 | `그룹경로내용` | oracle argmax | 116 변형(순위) |

**B. 조인 군(14 · v1.3 신규)** — 모두 관찰(7회차 오라클 승격 후보는 ★)이다. 227만 조인이 **필요 없는** 대조군 오라클이다.

| ID | 범주 | 프롬프트 초안 | 테이블 | 조인 키(`join_keys`) · 핵심 칸 | 판정 | 겨냥(함정) |
|---|---|---|---|---|---|---|
| ITAM-219 | join_agg | 하드웨어 지원이 이미 끝난 서버들을 운영체제 버전별로 몇 대씩인지 알려줘 | 79 · 72(허용 80) | 79.`서버호스트명`=72.`서버호스트명` · 79.`IP주소내용`=72.`IP주소내용` · 핵심 `운영체제버전내용` | observe | 조인 뒤 집계 · **복합 키 일부(호스트만) → 행 불어남** · 79 단독으로는 버전을 못 냄 |
| ITAM-220 | join_agg ★ | 하드웨어 지원이 끝난 스토리지 장비를 용도별로 몇 대씩인지 보여줘 | 58 · 51 | 58.`시리얼번호`=51.`시리얼번호` · 핵심 `용도구분명`·`하드웨어지원종료년월일` | observe | 서버가 아닌 군(스토리지) 조인 · 서버 79로 잘못 감 |
| ITAM-221 | join_name | 무인공과금 업무에 스토리지를 할당받은 서버들을 설치 지역별로 세어줘 | 52 · 72 | 52.`호스트명`=72.`서버호스트명` · 핵심 `업무명`·`설치지역내용` | observe | **이름이 다른 키 칸**(호스트명 ↔ 서버호스트명) · 서비스 경로가 72 이름 칸이 아니라 52 업무명 |
| ITAM-222 | join_chain3 | 그린뱅크 업무 스토리지 장비들 모델명이랑 하드웨어 지원 종료일 같이 보여줘 | 52 · 51 · 58 | 52.`시리얼번호`=51.`시리얼번호` · 51.`시리얼번호`=58.`시리얼번호` · 핵심 `모델명` | observe | **세 테이블 사슬** · 같은 이름 키 3개(별칭 구별) · 모델은 51에만 있음 |
| ITAM-223 | join_agg ★ | 가상 서버를 제일 많이 올려 둔 물리 호스트 상위 5개 알려줘 | 92 · 89 | 92.`호스트참조ID`=89.`호스트참조ID` · 92.`가상화관리서버ID`=89.`가상화관리서버ID`(P12로 필수 여부 확정) · 핵심 `호스트명` | observe | 조인 뒤 집계·순위 · **복합 키 함정(관리서버 칸 빠뜨림)** |
| ITAM-224 | join_chain3 | 클러스터별로 가상 서버가 몇 대씩 돌고 있는지 보여줘 | 92 · 89 · 86(허용 80) | 92 ↔ 89(223과 같음) · 89.`군집참조ID`=86.`군집참조ID` · 핵심 `군집명` | observe | 세 테이블 사슬 · 92에 군집 칸이 없음 · 80(`군집참조ID`·`가상화서버참조ID`)으로 줄이는 경로 허용 — 이때 이름은 86 필요 |
| ITAM-225 | join_agg ★ | 올해 맺은 계약별로 들어간 물품 수량 합계 알려줘 | 11 · 12 | 11.`계약번호`=12.`계약번호` · 핵심 `계약년월일`·`물품수량` | observe | 계약 군 · 문자열 날짜 칸 조건(`날짜 문자열 오용` 판정) |
| ITAM-226 | join_cond | 물품 분류에 정해진 기준 사용 연수를 넘긴 자산이 분류별로 몇 개인지 알려줘 | 41 · 46 | 41.`물품분류번호`=46.`물품분류번호` · 핵심 `취득년월일`·`기준사용년수` | observe | **양쪽 칸을 섞은 조건**(조인한 테이블의 값으로 날짜 비교) |
| ITAM-227 | join_control | 담당 부서별로 관리하는 서버가 몇 대씩인지 부서 이름으로 보여줘 | 80 | 핵심 `담당부점명` · `join_keys` 없음 | **oracle count** | **조인 불필요 대조군** — br01·72를 이으면 `gold_tables` 정밀도가 떨어진다(W3 마트 단독 규칙의 일반화) |
| ITAM-228 | join_agg | 올해 장애가 난 장비를 자산 분류별로 몇 건씩인지 알려줘 | 31 · 41 | 31.`물품분류번호`=41.`물품분류번호` · 31.`물품고유번호`=41.`물품고유번호` · 핵심 `장애접수일시`·`자산분류구분명` | observe | 장애 군 · 복합 키 · 일시 칸 형식 |
| ITAM-229 | anti_join ★ | 하드웨어 지원 종료 정보가 아예 등록 안 된 서버가 몇 대야? | 72 · 79 | 72 ↔ 79(209와 같음 · `NOT EXISTS`·`LEFT JOIN … IS NULL` 모두 인정) | observe | **없는 쪽 찾기** · 내부 조인으로 잘못 세면 0 또는 반대 집합 |
| ITAM-230 | anti_join | 업무 할당이 하나도 없는 스토리지 장비 목록 보여줘 | 51 · 52 | 51.`시리얼번호`=52.`시리얼번호` | observe | 없는 쪽 찾기 · 스토리지 |
| ITAM-231 | join_agg | IP 대장에서 장비 정보가 붙어 있는 주소가 건물별로 몇 개인지 알려줘 | 21 · 22 | 21.`IP주소`=22.`IP주소` · 21.`IP주소일련번호`=22.`IP주소일련번호` · 핵심 `건물명` | observe | IP 군 · 복합 키 · 서버 IP 칸(`IP주소내용`)과 혼동 |
| ITAM-232 | multiturn_join | t1 무인공과금 서비스가 올라가 있는 서버들 보여줘 → t2 그 서버들에 제일 많이 깔린 소프트웨어 5개 알려줘 | 72 / 72 · 75 | t2: 72.`서버호스트명`=75.`서버호스트명` · 72.`IP주소내용`=75.`IP주소내용` · 핵심 `서버소프트웨어명` | observe | **앞 결과 승계 + 조인**(W1 표본 승계가 조인 턴에서도 맞는지) |

조인 군 작성 메모:
- 프롬프트에 칸 이름이 보이는 낱말(「모델명」「계약」「용도」)은 산문 속 한글이라 로더 린트에 걸리지 않는다(`lint_prompt` — 한글 이름은 백틱·한정 꼴만 잡음). 착수 때 `--dry-run`으로 다시 확인한다.
- 정책 밖 칸(51·52·58·89·92·86·11·12·41·46·31·21·22·75)의 값은 반출에서 가려진다(`unclassified`). 판정은 테이블·칸 이름·행 수·조인 표지만 쓰므로 막히지 않는다. 다만 리포트 §8 분류 필요 목록이 늘어난다 → 6회차 결과를 보고 7회차 정책 등급(G-4와 같은 방식)을 따로 묻는다.
- 겹침 가드는 조인 군에도 똑같이 적용된다. 219·229(지원 종료), 211·222(그린뱅크 스토리지)처럼 r6 안에서 주제가 이어지는 것은 의도한 대조다 — 가드는 기존 세트·K2와의 겹침만 본다.

#### W8 — 내부망 6회차 체크리스트(사용자)

- [ ] 반입 확인: W2 (3) 반입 뒤에는 리포트 머리 줄 끝에 「지문 정규화 v2」가 붙고, 자산 지문이 외부망 빌드와 같아야 한다(반입 전 판이면 CRLF 변환 지문으로 대조 · 5회차와 `--compare`하면 「지문 방식 변경」이 1회 나온다).
- [ ] **「DB 구조」 탭 P1 재실행·승인 — 4회 연속 미이행.** 리포트 첫머리의 「구버전 빌드」 경고가 사라져야 한다. 105 코드값 지어냄(F7)·113 판독이 여기에 걸려 있다. **막히는 사유가 있으면 알려 달라** — 다른 경로를 계획한다.
- [ ] **탐침 실행**(2회 연속 미실행): `python -m scripts.itam_bench --check-oracle --env closed --scenarios testdata/itam_bench/scenarios.closed.probe.yaml --out results/itam_bench/<run>-probe`. 146 P01~P09(10항목) + 150 탐침 46항목(`ITAM-150-P01`~`ITAM-150-P19B` — P03·P05는 A·B, P09~P18은 측정마다 A·B·C(·D)로 나뉜다 · 행 수 = 건수 · 10000이면 「10000 이상」)이다(P07A~C·P08은 새 서비스 이름 3개의 존재 확인 · P09~P18은 조인 연결 건수 — 7회차 조인 오라클 승격 재료). 가장 중요한 것은 **146 P02·P03(101 85행 확정) · P04·P08 · 150 P02·P05A·P05B·P06(102·117·118·103)**이다.
- [ ] 오라클 `python -m scripts.itam_bench --check-oracle --env closed --scenarios testdata/itam_bench/scenarios.closed.r6.yaml --out results/itam_bench/<run>`(**신규 세트** · W9 · 오라클 10턴 = 201·202·203·204·205·206·207·212 t2·218·227). 0행·오류 오라클은 알려 주면 `observe`로 되돌린다.
- [ ] 6회차 run(`python -m scripts.itam_bench --run --env closed --scenarios testdata/itam_bench/scenarios.closed.r6.yaml`) → 반출. **기존 세트(`scenarios.closed.yaml`)는 돌리지 않는다.** 5회차와 `--compare`하면 맨 위에 「프롬프트 세트 교체」 고지와 「범주별 판정」 표가 나온다(턴 짝 전이는 의미 없음).
- [ ] 로그 확인:
  - `설명 정본 파일 적재: db_id=itam … kept=… added=…`(K3 반영 여부)
  - `.env`의 `TEXT2SQL_EMPTY_DIAGNOSIS_ENABLED` 값(F12 — 149 §10.8에서 미회신)
  - 105 모양 재시도를 만든 로그 줄(F7 경로)
- [ ] 판독(신규 세트 기준):
  - ITAM-212 t2가 앞 결과 전체 범위로 조회하거나 고지를 내는지(W1)
  - 마트 단독 질문(201~204)에 서버 원장 조인이 없는지 · 행 = 키에 가까운지(W3)
  - 211 재생성 LIKE가 핵심어인지(W5)
  - §8 분류 필요 컬럼(W6)
  - 날짜 형식 리터럴이 원문으로 남는지(201·205 모양 · W2 (2))
  - **조인 판정**(리포트 「조인 판정」 절 · W2 (6)):
    - 조인 군 14턴의 테이블 재현율(세 테이블 사슬 222·224에서 가운데 테이블을 빼먹는지)
    - `join_key_miss` 턴 — 특히 복합 키 일부(219·223·228·231)와 이름 다른 키(213 t2·221)
    - 0행 조인 턴은 같은 연결의 탐침(P09~P18) 건수와 짝지어 「모델이 키를 잘못 고름」과 「데이터가 안 이어짐」을 가른다
    - 227(조인 불필요)에 조인이 붙지 않았는지 · 229·230이 `NOT EXISTS`/`LEFT JOIN … IS NULL` 모양인지

### 6. 게이트 — 답(2026-10-08 · G-1~G-4 권고안 · G-5 (a))

| # | 질문(쉬운 말) | 선택지 | 권고 → **답** |
|---|---|---|---|
| G-1 | 「그 서버들 …」처럼 앞 결과를 가리키는 후속 질문에서, 앞 결과가 많아(21건 이상) 일부만 기억하고 있을 때 어떻게 할까 | (a) 앞 질문의 조건을 다시 걸어 전체를 조회하고, 그래도 일부만 조회됐으면 알린다(146 W3 「앞 결과 0건」 처리를 넓힘) · (b) 기억하는 서버 수 상한을 크게 올린다(예: 200 — 토큰↑ · 그보다 많으면 같은 문제) · (c) 지금처럼 두고 「일부만 조회했다」 안내만 붙인다 | **(a)** — 결과가 정확하고, 폴스타 노드 경로가 이미 「표본이면 승계 안 함」 규칙을 쓴다(대칭) → **답 (a)** |
| G-2 | 「유지보수 계약·취득·노후」 질문에서 조회 대상 고르기가 서버 통합 현황(`tcdmsif80`)을 놓친다(답은 맞게 나옴) | (a) 후보 설명 줄에 정의 `notes`를 싣는다(코드 1곳 · 폴스타 무변경) · (b) 경쟁 테이블(41·43)과 52의 정의 문구만 고친다(코드 0) · (c) 그대로 두고 횟수만 센다 | **(b)** — 149 문구 보강으로 2턴이 들어왔다. 남은 원인은 경쟁 테이블 문구다. 6회차에도 남으면 (a) → **답 (b)** |
| G-3 | 노후·계약 질문이 불필요하게 서버 원장을 붙여 결과 행이 늘어난다(110: 2,226행 · 실제 서버 1,559대) | (a) 문구를 바꾼 예시 SQL 2건(80 한 테이블) + 규칙 문장 · (b) 규칙 문장만 · (c) 코드가 SQL에서 불필요한 조인을 지운다 | **(a)** — 예시가 가장 잘 먹힌다(109·112·116은 예시 이후 안정). 시나리오 문구를 그대로 쓰지 않는다. (c)는 SQL 의미를 바꿀 위험이 있어 기각을 권한다 → **답 (a)** |
| G-4 | 반출 결과에서 값을 볼 수 있게 할 10칸의 등급 | §5 W6 표 권고안 그대로 · 칸별 수정 | **권고안** — 코드·용량·조직·서비스 이름은 원값, 경로·가상화 이름은 가짜 값 → **답 권고안** |
| G-5 | 6회차 새 프롬프트에 넣을 서비스 이름(`<서비스A>`·`<서비스B>`). 외부망에서는 운영 DB에 어떤 서비스가 있는지 알 수 없다(반출 값은 가짜 값) | (a) 운영에 실제로 있는 서비스 이름 1~2개를 알려 준다 · (b) 이미 쓴 「통합인증」「인터넷뱅킹」을 새 문구로 다시 쓴다 · (c) 서비스 질문을 빼고 나머지 범주만 새로 쓴다 | **(a)** — 핵심어까지 새로워야 「처음 보는 질문」 시험이 된다. 이름은 시나리오 파일(커밋 대상)에 들어가므로 공개돼도 되는 업무 이름이어야 한다. 답이 없으면 (b) → **답 (a) 무인공과금 · 법원공탁금 · 그린뱅크** |

### 7. 검증

- W1·W5는 신규 테스트를 둔다. 계획 끝에 `python scripts/regress.py --base c61e601 --files <바꾼 파일…>`을 1회 돌린다(D-303). W1은 `src/orchestration/intent_planner.py`·`src/nodes/context_resolver.py`를 바꾸므로 도구가 `[웨이브 회귀 필요]`를 내면 그 Wave 끝에 모듈 단위 회귀를 돌린다.
- 실 LLM: 목을 우선한다. MLX는 W1 대표 2턴만 1회 돌린다 — 두 평면 `mlx` 확인 뒤(D-240).
- W2: 5회차 반출물로 `--evidence` 통과를 확인한다. 반출물 원본은 테스트 데이터로 커밋하지 않는다 — 값 없는 재구성 입력만 쓴다(D-308).
- W3·W6·W7: `--validate-knowledge`(모의 DB) · 정책 가드 테스트 · 탐침 모의 DB 실행으로 확인한다.
- W9: 로더 린트(`--dry-run --env closed --scenarios …r6.yaml`) · 겹침 가드 테스트 · 신규 오라클 모의 DB 실행으로 확인한다. 조인 군은 각 턴의 **손으로 쓴 기준 SQL**(정의 키 사용 · 커밋하지 않는 scratch)을 모의 DB에서 실행해 테이블·칸 이름이 맞는지만 확인한다(건수는 근거 아님 — F14 ⑤). 그 기준 SQL을 판정기에 넣어 `join_key_miss`가 꺼지고, 키 하나를 지운 변형에서는 켜지는지 확인한다(W2 (6) 테스트와 같은 재료).
- W2 (6)(7): 기존 `scenarios.closed.yaml`로 `--dry-run`·리포트를 만들어 비트 동일을 확인한다(`join_keys` 없음).
- 선별 밖 계수: `trace.jsonl` `schema_context.presented_tables` ↔ `sql_analysis.tables` 차집합이 있는 턴 수다(3회차 5 · 4회차 8 · 5회차 4).
- 매뉴얼(D-255): W1 고지 문구만 대상 후보다. 나머지는 벤치·내부 지식이다.

### 8. 위험

| 위험 | 대응 |
|---|---|
| W1이 폴스타 「그 서버들」 후속의 동작을 바꾼다(20건 넘는 앞 결과) | 노드 경로가 이미 같은 판정을 쓴다 · 폴스타 대칭 테스트 · 20건 이하는 비트 동일 |
| W1 조건 승계가 앞 턴 SQL을 잘못 옮겨 넓히거나 좁힌다 | 146 W3 결정적 고지(넓힘)를 그대로 쓰고, 표본 `IN`도 고지한다 · 행은 숨기지 않는다(146 기각 설계) |
| W2 (1)이 실 IP를 가짜 값으로 오인한다 | 등록부는 반출 관문을 이미 통과한 목록뿐이다 · 치환 충돌은 구조상 0(D-321) · 목록 밖 IP는 막는다는 테스트를 둔다 |
| W2 (3) 지문 방식 변경으로 과거 run과 비교가 끊긴다 | 표지로 구분하고 1회 불연속을 리포트에 적는다 |
| W3 K2 예시가 벤치를 회상 시험으로 만든다 | 문구·기간·연수를 바꾼 변형만 쓰고, 오라클 턴 문구는 쓰지 않는다 |
| W9 세트 교체로 5회차와 같은 질문 기준의 전후 비교가 끊긴다 | 범주별 지표로 비교한다 · 기존 세트는 회귀 세트로 보존해 필요할 때 따로 돌린다 · 리포트에 세트 교체를 적는다 |
| 새 오라클이 내부망에서 0행·오류가 난다(모의 DB는 합성 행) | `--check-oracle` 먼저 · 실패 턴은 `observe`로 되돌린다 |
| 조인 군 14턴이 모두 관찰이라 6회차 통과율 숫자가 조인 능력을 말해 주지 않는다 | 6회차는 테이블 재현율·`join_key_miss`·탐침 대조로 읽는다 · 탐침이 연결을 확정한 조인(★)만 7회차 오라클로 올린다 |
| 정책 밖 테이블을 많이 써서 반출 리포트의 분류 필요 목록이 늘어난다 | 값은 `unclassified`로 가려져 반출 범위는 그대로다(W2 (7)) · 등급은 6회차 목록을 보고 따로 묻는다 |
| 세트가 32건으로 늘어 내부망 실행 시간이 길어진다(5회차 18건) | 다중 턴 3건뿐 · 필요하면 `--only`로 A·B 군을 나눠 돌린다 |
| P1·탐침이 6회차에도 빠진다 | W8 첫머리에 막힘 사유를 묻는다 · F5·F6·F10은 탐침 없이 확정하지 않는다 |

### 9. 버전 이력

- v1.5(2026-10-12) — 사용자 「권고에 맞게 진행하라.」: 201·221·213 t1·232 t1 문구 · 겹침 가드 서비스 이름 자리표 · count 오라클 NULL·빈 그룹 제외 · 203 경계 메모 · 탐침 P19A·B · `용도내용` free_text(D-301 부기)
- v1.4(2026-10-08) — 외부망 구현 착지(W1~W7·W9) · §5 W7 탐침 분할·W8 명령·W9 5번 `--compare` 전제를 구현에 맞춰 교정 · 상태 WIP(W8 내부망 대기)
- v1.3(2026-10-08) — 사용자 지시 반영(여러 테이블 조회·조인 조건 시험): F14(벤치가 조인을 판정하지 못함 — 카탈로그 관계 0 · 로더가 정책 테이블 2개만 허용) · W2 (6) `join_keys` 판정·(7) 로더 이름 검사 출처 · W7 조인 연결 탐침 P09~P18 · W9 조인 지도 + 조인 군 14건(ITAM-219~232 · 2·3테이블 · 조인 뒤 집계 · 없는 쪽 찾기 · 이름 다른 키 · 복합 키 함정 · 조인 불필요 대조군 · 다중 턴 조인) · 기존 18건에 판정 칸(테이블·키) · 32건
- v1.2(2026-10-08) — 게이트 답 반영: G-1 (a) · G-2 (b) · G-3 (a) · G-4 권고안 · G-5 (a) 무인공과금·법원공탁금·그린뱅크(W9 초안 자리표 채움 · W7 탐침 P07A~C·P08 추가) · 상태 「구현 대기」
- v1.1(2026-10-08) — 사용자 지시 반영: 6회차는 신규 사용자 프롬프트 세트로만 실행(W9 · `scenarios.closed.r6.yaml` · ITAM-2NN 초안 18건 · 겹침 가드) · W3 K2 비중복 조건 · W8 체크리스트 신규 세트 기준 · 게이트 G-5(서비스 이름)
- v1.0(2026-10-08) — 5회차 run·검증 run 분석 · 발견 F1~F13 · W1~W8 · 게이트 G-1~G-4
