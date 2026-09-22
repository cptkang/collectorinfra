# 110. 시나리오 테스트 통합 계획 — 하네스(94) · run 분석(96·108) · 제품 수정(98) · 재측정 설계(99)를 한 곳에

> **작성일**: 2026-09-21
> **성격**: 통합 계획 + 잔여 이관 장부. INDEX 「이관 조항」(D-208)을 두 번째로 적용한 문서다.
> - **이 문서가 소유하는 것**: ①**현행 실행 가이드**(단일 출처) ②계속 유효한 계약의 요약 ③**잔여 전건** ④착수 순서.
> - **설계 근거의 정본**: 여전히 원본의 해당 절이다. 원본은 번호를 유지한 채 시점 문서로 남으므로 `plans/94 §15` 같은 참조는 깨지지 않는다.
>
> **상태**: 부분 구현 · 잔여 있음 — 파일명 `-WIP`. 하네스 S0~S4·§15~§18·Y-10과 제품 수정 19건이 랜딩했다. **2026-09-21 `110·N-1`(`--arm`)이 랜딩해 2단·3단 arm 병행 실행의 코드 차단은 풀렸다.** 그러나 **판정 자격을 갖춘 기준선 run은 아직 0건이다**(§0.3) — 남은 것은 실행과 사람 판단이다(§4 A·C).
>
> **요청 원문(2026-09-21)**: *"93번 부터 108번 까지 config간소화와 시나리오 테스트에 대한 계획이 정리되어 있다. 너무 분산되어 정리되어 있어 별도의 파일에 config간호화 계획 1개, 시나리오 테스트 1개로 통합 정리하고 기존 계획은 확인하여 종료처리하라."*
>
> **흡수한 원본**: 94 · 96 · 98 · 99 · 108 다섯 건이다. 전부 잔여를 이 문서로 옮기고 종료했다(§0.2 · §5).
>
> **짝 계획**: `plans/109`(config 간소화 — 93·97 통합).
> - 109 소유: 벤치 스위프, 설정 처분.
> - **이 문서 소유: 판정 계약**, 곧 단언이 평가되지 않은 턴을 분모에서 어떻게 다루는가. 두 하네스가 같은 규칙을 써야 수치가 비교되기 때문이다(108 §5.2e).
>
> **범위 밖**: 같은 번호대지만 주제가 다른 계획이다.
> - 95(ITAM) · 100(MLX) · 101(ML 장애 진단) · 102(교차 시스템) · 103(3단 동등성) · 104(관리자 DB 구조) · 105(Hermes) · 106(의도 파악) · 107(프롬프트 재작성).
> - 단, 107이 이 하네스에 요구하는 항목(원 94 §19)은 §3.1에 있다.
>
> **관련 결정**: D-003 · D-127 · D-208 · D-212(예약만 되어 있고 본문 미등재 — §3.1 `94·S7-b`) · D-215 · D-216 · D-217 · D-218 · D-220 · D-221 · D-222 · D-225 · D-231 · D-236
>
> **실측 기준**: 2026-09-21 작업 트리(`multiintent` HEAD `8830185` + 미커밋).
> - 원본 5건은 읽기 전용 에이전트 4개가 전문을 정독했다. 잔여는 코드를 grep하고 파일을 열어 판정했다.
> - pytest·실 LLM·서버 기동은 0회다.
> - 가이드 ⑥의 판독 명령 5종은 run `20260918-182507` 로그에 실제로 돌려 수치를 재현했다.
> - 원본 해시(sha1 앞 8자, 종료 직전 값): 94 `6aa4812e` · 96 `a9cdc3a5` · 98 `ece5df07` · 99 `a56a8126` · 108 `29b16b8d`.
>
> **▶ 돌리기만 하면 되면 아래 「실행 가이드」만 읽으면 된다.** 남은 일은 §3, 순서는 §4에 있다.

---

## ▶ 실행 가이드 — 현행 단일 출처

> 이 절은 94 「▶ 실행 가이드」 ①~⑪, 99 §3.5, 108 §5.0~§5.5를 합친 것이다. 원본의 해당 절은 시점 기록이다. **서로 다르면 이 절이 이긴다.**
> 명령과 옵션은 `scripts/scenario/__main__.py` argparse(`:325-379`)와 대조해 실재하는 것만 적었다.

### ① 한눈에

진입점은 `python -m scripts.scenario` 하나다. `scripts/scenario/run.py`는 **없다**(108 §5.5 구판에 잘못 적혀 있었다).

```bash
python -m scripts.scenario                     # 기본 — 내부망 두 평면이면 전 시나리오 실 실행 / 아니면 무과금 점검
python -m scripts.scenario --preflight         # 사전 점검: 사다리 단 · 설정 에코 · DB 조회 2건 (DB 미도달이면 --no-db)
python -m scripts.scenario --dry-run           # 카탈로그 검증만 (서버 없음 · 수 초)
python -m scripts.scenario --mock              # 모의 서버로 전 경로 (LLM·DB 0)
python -m scripts.scenario --estimate          # 규모·예상치 (무과금)
python -m scripts.scenario --run               # 실 실행
python -m scripts.scenario --report [RUN_ID]   # 리포트 재생성 (무과금 · 몇 번이든)
python -m scripts.scenario --analyze [RUN_ID]  # 분석·대안 수립 (무과금)
```

**기본 동작(인자 없음)의 판정.** D-216 ①을 D-222가 개정했다.
- 워커 `LLM_PROVIDER ∈ {fabrix, ollama, mlx}` **이고** 오케스트레이터 `ORCHESTRATOR_PROVIDER ∈ {vllm, mlx}`일 때만 내부망으로 본다(`scripts/scenario/preflight.py:45-46`).
- 내부망이면 전 시나리오를 승인 없이 실 실행한다.
- 둘 중 하나라도 외부면 dry-run → mock → estimate만 한다.
- **fabrix 워커에 gemini 오케스트레이터를 붙인 조합은 외부다.** 종전 가이드는 `LLM_PROVIDER=fabrix` 하나만 확인하라고 했는데, 그것만으로는 부족하다.

**외부 프로바이더로 실 실행할 때.**
- `RUN_E2E=1`과 승인 프롬프트 1회가 필요하다. 프롬프트는 `--yes`로 생략할 수 있다.
- **실행 건마다 사용자 승인을 받는다**(D-127). 키가 있다는 이유만으로 돌지 않는다.

**카탈로그 규모**: 정본은 로더다(`--dry-run` 출력 첫 줄). 2026-09-21 실측은 **220건**이다(Y-10으로 218 → 220). 산문에 박은 건수는 낡는다.

### ② 선택지

| 선택지 | 뜻 |
|---|---|
| `--profile <이름>` | 플래그 프로파일 **필터**(반복 가능). 시나리오가 선언한 자기 `profile:` 로 거른다. 정의는 `config/scenarios/profiles.yaml` |
| `--arm <프로파일>` | 전 시나리오에 **덧씌우는** 측정 축(반복 가능 · `110·N-1`). 시나리오 자기 프로파일과 **병합**하고(키 충돌 시 arm 우선) arm 마다 한 번씩 돈다. 조합 1개 = 서버 기동 1회 |
| `--group <문자>` | 군만 실행(반복 가능). 예: `--group C` · `--group R4` |
| `--only <ID,…>` | 개별 시나리오(쉼표 목록) |
| `--repeat <n>` | run 단위 반복. **기본 1이고 R군만 3**이다(`runner.py:286·1440`). 시나리오별 반복은 카탈로그 `repeat:` 필드로 준다 |
| `--env closed\|sandbox` | 대상 환경을 강제로 좁힌다. **보통 생략**한다. 미지정이면 서버 활성 DB로 판정하고, 환경이 다른 시나리오는 데이터 의존 단언을 보류한다(D-216) |
| `--resume <RUN_ID>` | 중단된 run을 **같은 run_id**로 이어서. 무효 턴은 다시 돈다. **일부 턴만 끝난 멀티턴 시나리오는 1턴부터 새 thread로 다시 돈다**(콘솔 `[재개]` · `meta.rerun_partial`). 이전 서버 로그는 `logs/server-<프로파일>.log.prev-<시각>`으로 남고, 끊기기 전과 판이 다르면 `[주의] 출처 섞임`이 뜬다(`109·CS-16`·`CS-17`·`CS-19③`) |
| `--resume-failed <RUN_ID>` | 끝난 run의 무효·오류 시나리오만 **새 run_id**로. 원본은 읽기만 하고 `meta.rerun_of`에 출처가 남는다. `fail`은 대상이 아니다. `--resume`과 동시에 줄 수 없다(D-221) |
| `--segment <N>` | 시나리오 N건마다 토큰을 새로 잡는다. 서버는 재기동하지 않는다(⑤) |
| `--no-db` | preflight의 DB 조회 2건 생략 |
| `--port <n>` | 자식 서버 포트. Windows 제외 대역 회피용(부록 A.1-6) |
| `--user` `--password` | 질의용 사용자 계정. 생략하면 인증 on 서버에서 **내장 테스트 계정**(`runner.py` `DEFAULT_USER_ID`)을 쓴다(D-216) |
| `--admin-user` `--admin-password` | 설정 에코용 운영자 계정. 생략하면 설정(`ADMIN_USERNAME`·`ADMIN_PASSWORD`)에서 읽는다 |
| `--token` `--admin-token` | 미리 받은 토큰. **둘 다** 주면 러너는 로그인하지 않는다 |
| `--timeout <초>` | 스트리밍 청크 간격 상한(기본 360). 비스트리밍 턴은 서버 상한 + 30초까지 기다린다 |
| `--yes` | 외부 프로바이더 승인 프롬프트 생략(내부망은 묻지 않는다) |

### ③ 준비

| 단계 | POSIX | Windows (PowerShell) |
|---|---|---|
| 가상환경 | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| 설치 | `pip install -e ".[dev,document]"` | 동일 |
| 인코딩 | 불필요 | `$env:PYTHONUTF8="1"` · `$env:PYTHONIOENCODING="utf-8"` · `chcp 65001` — 없으면 한글 출력에서 런이 죽는다 |
| 의존 서비스 | Redis · (샌드박스면) PostgreSQL | `docker compose -f redis\docker-compose.yml up -d` 등 |

**`.env` — 사람이 정하는 것**
- `LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER`(두 평면 모두), `ACTIVE_DB_IDS`(JSON 배열), `AUTH_ENABLED`, `DB_BACKEND`.
- 인라인 주석을 쓰지 않는다. list 필드는 JSON으로 쓴다.

**`.env` — 쓰지 말 것.** 러너가 자식 프로세스에 주입하는 값이라 `.env`에 쓰면 충돌한다.
- `ALARM_ENABLED=false`, `AUTH_JWT_EXPIRE_HOURS=8`: `runner.py:63-66` `ISOLATION_ENV`.
- `CHECKPOINT_DB_URL`: `runner.py:816`, run 전용.
- 프로파일 플래그.
- 러너는 `.env`를 수정하지 않는다. 병행 세션과 작업 트리를 오염시키지 않는다.

**재테스트 설계 — 2단·3단을 나란히 잰다**(2026-09-21 사용자 지시 · 36 세션 전달). 어느 단으로 운영할지는 **재어 보고 정한다.**
- 사용자 지시 원문 취지: *"2단과 3단을 모두 테스트를 진행하고 최적의 방향으로 시스템을 구동할 수 있도록 결과를 분석해야 되는 거 아니냐? 옵션을 끄고 돌리는 게 아니라."*
- 이 방침은 108 §5.1(사다리 3단 고정)과 108 G-2의 "재테스트는 3단으로"를 **대체한다.** 이유는 둘이다.
  - ①3단은 한 번도 측정된 적이 없다. 3단은 `semantic_router`에서 LLM을 1회 더 호출하는데, 그 비용이 어느 표본에도 없다. "131턴이 3단에 없는 노드에서 죽었다"는 2단의 결함일 뿐, 3단이 낫다는 근거가 아니다.
  - ②`.env`를 고쳐 한쪽 단만 돌리면 비교가 성립하지 않는다. 또 "러너는 `.env`를 수정하지 않는다"(위)는 원칙과도 어긋난다.
- CU-B1 보류(108 G-2의 본 결정)는 그대로다.
- **프로파일 2종**(`config/scenarios/profiles.yaml` · 36 세션 신설 · 세 플래그 모두 명시):

| 프로파일 | 주입 | 확정 단(격리 프로세스 `resolve_ladder_tier` 실측) |
|---|---|---|
| `tier2_intent` | `ENABLE_DEEPAGENTS_PACKAGE=false` · `ENABLE_INTENT_ORCHESTRATION=true` · `ENABLE_SEMANTIC_ROUTING=true` | `intent_orchestration`(`intent_flag_on`) |
| `tier3_router` | `ENABLE_DEEPAGENTS_PACKAGE=false` · `ENABLE_INTENT_ORCHESTRATION=false` · `ENABLE_SEMANTIC_ROUTING=true` | `semantic_router`(`none`) |

- 두 arm은 **같은 코드·같은 시나리오**로 돈다. 랜딩분(CU-A1~A3·CU-B2·CU-H1·CU-H2)이 양쪽에 똑같이 들어가므로 **단 차이만 분리**된다. 3단만 돌리면 "코드 수정"과 "단 전환"이 한꺼번에 바뀌어 효과를 어느 쪽에 돌릴지 가릴 수 없다.
- `baseline`(운영 `.env` 그대로)을 함께 돌리면 "운영이 오늘 쓰는 단"이 세 번째 참조점이 된다.
- 확인: `run.json`의 `profiles[].tier`가 arm마다 각각 `intent_orchestration` / `semantic_router`인가. **모든 arm이 같은 단이면 arm이 무너진 것이다.**
- 1단(`deep_agent`)은 두 arm 모두 끈다. 1단까지 재려면 세 번째 arm이 필요하고, 오케스트레이터가 가용해야 한다.
- ⚠ **3단 기능 동등성은 미완이다**(`plans/103` P5 · `plans/102` L-5). 3단 arm의 기능 불합격이 "3단이 나쁘다"가 아니라 "아직 안 옮겨진 기능" 때문일 수 있다. 두 arm의 기능 차이는 반드시 `plans/103` 잔여와 대조해 읽는다.
- **쓰는 법 — `--profile`이 아니라 `--arm`이다**(`110·N-1` 랜딩 2026-09-21).

```bash
# 2단·3단을 같은 run 의 두 arm 으로 나란히 잰다
python -m scripts.scenario --run --arm tier2_intent --arm tier3_router
# 먼저 무과금으로 전개를 확인한다(서버 없음 · 수 초)
python -m scripts.scenario --dry-run --arm tier2_intent --arm tier3_router
```

  - `--profile`은 시나리오의 `profile:` 필드로 **거르기만** 한다. 카탈로그 220건의 `profile:`은 baseline 212·optin_alarm 8뿐이라 `--profile tier2_intent`는 **선택 0건**이다. arm에는 쓰지 않는다.
  - `--arm`은 각 시나리오의 자기 프로파일 **위에 덧씌운다**. **치환이 아니라 병합**이고 키가 충돌하면 arm이 이긴다. 조합마다 서버를 따로 띄운다 — 2 arm × 프로파일 2종 = **기동 4회**:

| 조합 프로파일 | 시나리오 | 주입 |
|---|---:|---|
| `baseline+tier2_intent` | 212 | 사다리 3키 |
| `baseline+tier3_router` | 212 | 사다리 3키 |
| `optin_alarm+tier2_intent` | 8 | 사다리 3키 + `TEXT2SQL_ALARM_DETERMINISTIC` |
| `optin_alarm+tier3_router` | 8 | 사다리 3키 + `TEXT2SQL_ALARM_DETERMINISTIC` |

  - **D군 8건이 자기 플래그를 유지하는 것이 핵심이다.** 치환했다면 그 8건은 알람 결정적 경로가 꺼진 채 돌아 *잘못 측정한 것*이 아니라 **다른 것을 측정한 것**이 된다. 회귀 테스트: `tests/test_scenario/test_arm_fanout.py`.
  - 실행 순서는 **프로파일 major · arm minor**다. 중간에 끊겨도 먼저 끝난 프로파일에서는 두 arm이 모두 남는다(한 arm만 완주하면 비교 자체가 성립하지 않는다).
  - **`.env`를 바꿔 한 단만 돌리지 않는다.** 두 단을 서로 다른 시각의 run 두 개로 재면 실행 시각이 지연과 교란된다. 93 스위프 실측에서 전반 31 arm은 61.1초, 후반 31 arm은 56.6초로 축 효과와 같은 크기대였다(`runner.py` `iter_executions` 주석). 그래서 두 단은 **같은 run 안의 arm**이어야 한다.
  - `baseline` 자체도 arm으로 줄 수 있다(`--arm baseline` → 조합 `baseline` · `optin_alarm`). 운영 `.env` 그대로가 세 번째 참조점이 된다.
  - 오타난 arm 이름은 **실행 전에 거부**한다. 카탈로그 검증은 시나리오의 `profile:`만 보므로 오타를 통과시키면 주입만 조용히 빠진 채 전 arm이 같은 단으로 돈다.

**인증**(`AUTH_ENABLED=true` 서버)
- 러너는 프로파일마다 서버를 띄운 뒤 스스로 두 번 로그인한다.
  - 운영자: `/api/v1/admin/login`. 설정 에코에 쓴다.
  - 사용자: `/api/v1/auth/login`. 질의에 쓴다.
- 두 토큰은 서로 다른 시크릿으로 서명된다(D-070).
- 기본 사용자는 내장 테스트 계정이다. 그 계정이 **서버 인증 DB에 활성 상태로 있어야 하고**, 허용 DB(`allowed_db_ids`)에 대상 DB가 들어 있어야 한다.
- 먼저 웹 `/login`으로 한 번 로그인해 본다. 비밀번호를 5회 틀리면 30분 잠긴다.
- 키 우선순위는 **OS 환경변수 > `.encenv` > `.env`**다(`docs/03_setup_guide.md` §3.4 · §3.5가 정본). Windows는 세션에 남은 `$env:AUTH_*` 값이 파일 값을 덮는다.
- 인증을 끄고 돌리면(`AUTH_ENABLED=false`) 인증 미들웨어가 빠져 운영과 다른 경로를 잰다(94 G-3 · D-215).
  - ⚠ run `20260918-182507`은 두 프로파일 모두 `auth_enabled: false`였다.
  - 그래서 그 run의 무효 0건은 토큰 수명 관리(T-a·T-b)를 실검증한 결과가 **아니다**.

### ④ 단계별 실행 — 무과금에서 과금으로 (재테스트 기준)

| 단 | 명령 | 과금 | 이 단의 목적 | 통과 기준 |
|---:|---|---|---|---|
| **0** | `--preflight` | 무과금 | 설정 실효값 에코, 두 평면 프로바이더 판정, DB 조회 2건(99 E-1 — `98·G-5`·`98·G-8` 판정 입력). 서버 `.env` 자체의 단 경고(`intent_flag_on`)는 참고만 한다. **arm의 단은 프로파일이 정한다**(③). ⚠ **질의 상한 실효값을 에코에서 읽어 `run.json`에 남긴다** — 직전 run은 199턴(52.4%)이 상한에 걸렸고 그중 131턴이 2단 전용 노드에서 죽어, 이 값이 2단·3단 비교의 지배 변수다. 코드 기본값은 2026-09-21에 60→120으로 바뀌었지만(**D-242** · `src/config.py` `ServerConfig.query_timeout`), **`.env`에 `API_QUERY_TIMEOUT`이 있으면 코드 기본값은 적용되지 않는다**(개발 맥 실측 2026-09-21: `.env`에 900). 폐쇄망 실효값은 **추정하지 말고 0단 에코로 확인**한다(CLAUDE.md 「운영 실측 우선」). run 사이에 이 값이 바뀌면 지연·타임아웃 수치를 직접 비교하지 않는다 | `echo_ok: true` · `echo_mismatch: {}` |
| **1** | `--dry-run` | 무과금 | 카탈로그 검증(ID 중복 · `plans` 필드 · 프로파일 · 신규 단언 파싱) | 카탈로그 오류 0 |
| **2** | `--mock` | 무과금 | 러너·단언기·리포트 배관. 분류기가 `timeout` 유형을 실제로 내는가(CU-H2) | 불합격·오류 0. 제외는 SYN-F-05 1건뿐(Redis 쓰기는 실 모드에서만) |
| **3** | `--estimate` | 무과금 | 규모. 직전 run은 **약 6.4시간** 걸렸다(218 시나리오 / 380턴). `--estimate` 상한은 군 목표치의 합이라 실제보다 짧게 나온다 | 예산 확인 |
| **3.5** | `--run --only H-03,H-04,B-07,B-08,D-04,R3-03,R3-03C --arm tier2_intent --arm tier3_router` | **실 실행** | **표적 재현, 약 10분.** 랜딩분을 전체 실행 전에 먼저 판정한다(아래 표) | 아래 기대표 |
| **4** | `--run --arm tier2_intent --arm tier3_router` · 필요하면 `--group`/`--segment`(⑤) | **실 실행** | 전 시나리오 재테스트 → **단별 첫 유효 기준선** | ⑥ 판독 |
| 판독 | `--report <RUN_ID>` · `--analyze <RUN_ID>` | 무과금 | 리포트·분석 재생성(원시 로그 보존) | ⑥ |

**3.5단 기대표**(108 §5.5)

| 시나리오 | 기대 | 확인하는 것 |
|---|---|---|
| H-03 · H-04 | 데이터 행 > 0 | CU-A1(`target_sheets` 환각 검증) |
| B-07 · B-08 | 합격 | CU-H1(단언 `(numeric\|decimal)`) |
| R3-03 | 전 서버 반환이 아니라 **존 선택 역질문**이 뜨고, 문구에 '판교존'이 이름으로 나온다 | CU-B2(미등록 존 역질문) |
| R3-03C | 정상 조회. R3-03과 **함께 깨지면 과잉 거부**다 | CU-B2 대조군. ⚠ 직전 run에서는 timeout이라 비교 기준이 없다 — 이번이 첫 관측이다 |
| D-04 | `tier2_intent`에서는 **여전히 실패해야 정상**이다(CU-B1 보류). `tier3_router` 결과는 첫 관측이다 — 실패하면 §3.3 `98·CU-14 주석 고백` 판단의 입력으로 쓴다 | CU-B1 보류(108 G-2) · 3단 침묵 조건 드롭 여부 |

**멈추는 조건**
- **arm이 무너지면 멈춘다.** 3.5단 뒤 `run.json`의 `profiles[].tier`가 arm마다 기대한 단(③ 표)이 아니면 멈춘다. 모든 arm이 같은 단이면 주입이 먹지 않은 것이다.
- **3.5단에서 H-03·H-04가 여전히 0행이면 4단으로 가지 않는다.** CU-A1이 안 먹은 것이다. 6시간을 쓰기 전에 원인부터 본다.
- 4단은 실 실행이고 오래 걸린다. 끊기면 ⑤의 복구 수단을 쓴다.

**4단 전 마지막 확인**
1. **작업 트리 커밋 상태.** 직전 두 run은 `dirty: true`라 커밋 앵커가 사라졌다(`5b093016`·`69b665f`는 이 저장소에 없다). 커밋한 뒤 실행해 `run.json.meta.commit`을 재현 가능하게 만든다. 커밋 범위는 사람이 판단한다(`99·H-1` · §3.4).
2. **프로바이더 두 평면.** 둘 다 내부망이면 승인이 필요 없다. 외부면 D-127에 따라 건별로 승인받는다.
3. **디스크와 시간.** arm 1개는 전 시나리오 1회전이다. 직전 run은 프로파일 2개에 약 6.4시간이 걸렸으므로 arm 2개면 같은 규모이고, `baseline`을 더하면 1.5배가 된다. 직전 run 산출물은 **817 MB**였고, 그중 `checkpoints-baseline.db`가 804 MB(842,584,064 B)였다. preflight가 여유 5 GB 미만이면 경고한다(`preflight.py:65`).
4. **인증 on을 권장한다.** 인증 서버에서 돌려야 토큰 수명 관리가 실검증된다(③ 인증 ⚠).

### ⑤ 장시간 run 분할

전 스위트 1회는 벽시계로 6~8시간대다. 토큰 만료 자체는 T-b(수명 80% 경과 시 턴 경계에서 선제 갱신 · `runner.py:598·686`)가 막는다. 아래 수단들은 그 위에서 **실패 반경을 줄이고 복구를 싸게 만든다.**

```bash
python -m scripts.scenario --group R3 --group R4     # 군 단위 — 가장 단순하고, 중단해도 그 군만 잃는다(기본 권장)
python -m scripts.scenario --segment 40              # N건마다 토큰 재발급 · 서버 재기동 없음
python -m scripts.scenario --resume <RUN_ID>         # 중단 복구 — 같은 run_id 에 이어 쓴다
python -m scripts.scenario --resume-failed <RUN_ID>  # 사후 복구 — 무효·오류만 새 run_id 로
```

- **`--resume`은 턴이 아니라 시나리오 단위로 잇는다**(2026-09-22 · `109·CS-17`).
  - 일부 턴만 끝난 멀티턴은 1턴부터 새 thread로 다시 돈다. 앞 턴이 fail/error로 끊겨 뒤 턴을 일부러 건너뛴 시나리오는 끝난 것으로 본다.
  - 같은 턴이 두 번 적재되면 뒤 행이 결과다. 리포트·분석기도 같은 규칙으로 센다.
  - 끊기기 전 서버 로그는 `.log.prev-<시각>`으로 보존된다(`CS-16`). 시도별 출처는 `run.json` `meta.attempts`에 쌓인다(`CS-19③`).
  - 그래도 **끊긴 뒤 코드·`.env`를 바꾸지 않고 잇는다.** 바꾸면 `[주의] 출처 섞임`이 떠도 결과는 섞인다.
- **세그먼트 크기는 시나리오 수 기준이다**(D-221 ③). 예상 소요 기준이었던 99 §3.3은 폐기됐다.
  - 이유: 소요 추정치(`--estimate`)가 군 목표치 기반이라 체계적으로 틀린다. 같은 `--segment 40`은 언제 돌려도 같은 경계를 만든다.
  - 균등한 소요가 필요하면 `--group`으로 끊는다.
- **경계 보존 5원칙**(94 §15.4):
  - ①서버는 세그먼트마다 재기동하지 않는다.
  - ②cold 묶음(K-02)은 항상 첫 세그먼트에 둔다.
  - ③체크포인트 DB를 공유한다.
  - ④`raw.jsonl` 한 파일에 이어 쓴다.
  - ⑤teardown은 run 단위로 한다.
- **군별 턴당 소요**(run `20260915-131903` 유효 280턴, wall 합 7.52h 실측)는 R1 181초 · L 102초 · K 89초 · R2 73초다. 군에 따라 약 2.5배 벌어진다.

### ⑥ 결과 판독

**산출물**(`results/scenario/<RUN_ID>/`)
- `report.md`: 사람이 읽는 정본, 11절.
- `summary.json`: 분석기 입력.
- `raw.jsonl`: 건별 원시 측정치. 재개·재분석의 원본이다.
- `run.json`: 메타, 프로파일별 단·에코·토큰 갱신.
- 분석 6종: `countermeasures.md` · `bottleneck.md` · `failure_taxonomy.md` · `coverage_gap.md` · `regression.md` · `improvement_backlog.md`.
- `logs/server-<프로파일>.log` · `checkpoints-<프로파일>.db` · `artifacts/`.
- **`report.html`은 없다**(`94·G-8(b)` 미구현).

**판독 순서** — 리포트를 위에서부터 읽지 않는다(108 §5.0).
1. `run.json`의 `profiles[].tier`가 arm마다 기대한 단(`tier2_intent` → `intent_orchestration` · `tier3_router` → `semantic_router`)인가. 아니면 arm 비교가 성립하지 않는다.
   - `profiles[]`는 조합 이름(`name`)과 함께 **`arm`·`base_profile`**을 싣는다. `raw.jsonl` 행도 같은 칸 이름·같은 값 형태다. `report.md` 1절 「프로파일(arm)별 판정」 표가 같은 값을 arm별 판정·지연과 나란히 보여준다 — **arm 비교는 그 표로 한다.**
   - ⚠ **§2 군별 표는 arm 비교에 쓰지 않는다.** 시나리오 id로 접으므로 같은 시나리오의 두 arm 판정이 한 칸으로 합쳐지고 **나쁜 쪽이 이긴다**(`report.scenario_verdicts`). 기계 판독은 `summary.json`의 `by_profile[]`을 쓴다.
2. **arm별로** 타임아웃 턴 수를 본다(아래 표 1행). 직전 2단 run은 199턴이었고, 목표는 60 미만이다. 2단 arm과 3단 arm의 차이가 곧 단의 효과다.
3. `failure_taxonomy.md`에 `timeout` 유형이 실제로 나타나는가(CU-H2 회귀).
4. 그다음에 기능 판정을 본다. **`report.md` 2절 「판정 분모(턴 단위 · 제거 축)」가 먼저다**(`108·G-6` 랜딩) — 단언이 평가되지 않은 턴은 분모에서 빠지고 사유 3종(`invalid`·`timeout`·`clarify_blocked`)이 갈려 있다. `제거 합 + 분모 == 전체`가 **일치**인지 먼저 보고, `역질문 차단 N건 (자동응답 M건 · 발동률 P%)` 표기를 함께 읽는다(자동응답이 켜진 run은 `clarify_blocked`가 작게 나온다 — 표기 없이 자동응답 없는 run과 나란히 놓지 않는다). 같은 절의 시나리오 단위 표로 합격률을 다시 계산하지 않는다.
5. 지연은 노이즈 바닥 **±4,009 ms**(정확도 **2.0 %p**)보다 작은 차이를 개선으로 읽지 않는다.
   - 출처: 109의 벤치 스위프 대조군 arm 3개 실측(D-237).
   - 하네스와 run이 달라 **절대 비교에는 쓰지 않는다.** 유의성 문턱으로만 쓴다.

**단 무관 지표**(108 §5.3). 사다리 단이 다른 run과 **직접 비교하지 않는다.** 아래 계수(count)로 판정한다. 계수에는 노이즈 바닥을 적용하지 않는다.
명령 5종은 run `20260918-182507`에서 재현을 확인했다(2026-09-21). 108 §5.3의 원문 명령 2개는 결함이 있어 고쳤다.
- `grep -c 'SQL 생성 완료 (retry=2\|3)'`: 기본 grep에서 `\|`가 최상위 교체로 해석돼 **79**가 나온다.
- 산문 계수 명령: `-E`에 lookahead를 넣어 **구문 오류(exit 2)**가 난다.

| 지표 | 명령(`L=logs/server-baseline.log`) | 직전 run | 목표 |
|---|---|---:|---|
| 타임아웃 턴 수 | `python -c "import json;r=[json.loads(l) for l in open('raw.jsonl',encoding='utf-8') if l.strip()];print(sum('처리 시간이 초과' in (x.get('error') or '') for x in r))"` | **199/380** | < 60 |
| `retry=2`·`retry=3` 생성 | `grep -cE 'SQL 생성 완료 \(retry=[23]\)' $L` | **66** | < 25 |
| 산문 생성(SQL 아닌 응답) | `grep -E 'SQL 생성 완료 \(retry=[0-9]+\): ' $L \| grep -cvE 'SQL 생성 완료 \(retry=[0-9]+\): (--\|SELECT\|WITH)'` | **130** | 산문 자체는 줄지 않는다(프롬프트가 지시). 소모 호출이 124 → ~65 |
| `[WARNING]` 건수 | `grep -c '\[WARNING\]' $L` | **2,942** | < 500 |
| 빈 양식 산출 | `grep -c '0건 채워짐' $L` | 2(H-03·H-04) | **0** |

**raw.jsonl·run.json 요약 명령**(94 ⑩-6. POSIX는 경로 구분자만 `/`)

```powershell
python -c "import json,sys,collections as c; rows=[json.loads(l) for l in open(sys.argv[1],encoding='utf-8') if l.strip()]; print('rows', len(rows)); print('verdict', dict(c.Counter(r['func_verdict'] for r in rows))); print('auto_answers', sum(1 for r in rows if r.get('auto_answers'))); print('executed_sqls', sum(1 for r in rows if r.get('executed_sqls'))); print('env_mismatch', sum(1 for r in rows if r.get('env_mismatch'))); print('trace_files', sum(1 for r in rows if r.get('trace_files')))" results\scenario\<RUN_ID>\raw.jsonl
python -c "import json,sys; s=json.load(open(sys.argv[1],encoding='utf-8')); m=s['meta']; print('env', m.get('env'), m.get('env_source')); print('token_refresh', m.get('token_refresh')); print('setup_cleanup', m.get('setup_cleanup')); print('teardown_log', m.get('teardown_log')); [print(p.get('name'), p.get('tier'), p.get('degraded_reason'), p.get('echo_ok')) for p in s.get('profiles', [])]" results\scenario\<RUN_ID>\run.json
```

**함께 볼 것**
- `report.md` 요약에 **`silent_wrong`이 0이 아니면 가장 먼저 본다.** 조용한 오답은 사용자가 알아차릴 수 없다(94 §3.8).
- `executed_sqls`가 0이면 서버 감사 로그 `query_executed`를 수집하지 못한 것이다. 스모크 단계에서 `grep -c query_executed $L`이 1 이상인지 먼저 본다.
- `auto_answers`가 **물으면 안 되는 턴**에 붙었으면 제품 회귀 후보다. 그 턴에 `auto_answer: false`를 달아 역질문을 그대로 판정한다.
  - 존 자동응답은 직전 run에서 380턴 중 221턴(58.2%)에 발동했고, 전건 `polestar_cm_gp`였다.
  - 자동응답이 라우팅 오류를 정답 DB로 교정해 버리므로, **이 arm 하나로는 라우팅 정확도를 잴 수 없다**(§3.2 `108·CU-B6`).
- 노드 지연(`node_elapsed_ms`·`node_calls`)은 D-217 이후 **회차 누적** 의미라 그 이전 run과 비교하지 않는다.
- K-02 cold는 근사다(Redis·파일 캐시가 warm). 인용할 때 근사임을 적는다.

### ⑦ Redis 쓰기 사후 확인 (공유 Redis면 필수)

러너가 Redis에 쓰는 시나리오는 셋이다(94 ⑩-7).

| 시나리오 | 쓰는 것 | 되돌림 |
|---|---|---|
| K-10 | `schema:polestar_cm_gp:synonyms` 해시의 `polestar.cmm_resource.hostname`에 조어 `검증용사용률`을 등록 | 턴 후 그 단어만 삭제하고, 다음 실행 시작 때 잔여를 다시 삭제 |
| A-10 | 질의가 등록한 동의어(`vcore`·`cpu`·`core`) | 턴 전후 스냅샷 차이 중 선언한 단어(`unregister_words`)만 삭제 |
| SYN-F-05 | 활성 DB 운영 시드를 합집합으로 병합 | 없음 — 시드가 정본이다 |

```powershell
# K-10 잔여 확인 - clean 이면 정상
python -c "from scripts.scenario.runner import _with_redis; w=_with_redis(lambda c, _: c.load_synonyms('polestar_cm_gp')).get('polestar.cmm_resource.hostname') or []; print('LEFTOVER' if '검증용사용률' in w else 'clean')"
# LEFTOVER 면 그 단어만 삭제
python -c "from scripts.scenario.catalog import load_catalog; from scripts.scenario.runner import apply_synonym_setup; print(apply_synonym_setup(load_catalog().by_id('K-10').setup, remove=True))"
```

러너가 접속하는 Redis가 운영과 같은 인스턴스면 실행 시간을 운영 담당자에게 알린다.

### ⑧ 막혔을 때

| 증상 | 첫 확인 | 보통의 원인 |
|---|---|---|
| 1단에서 카탈로그 거부 | 출력의 시나리오 ID·필드명 | `plans` 필드 누락 · ID 중복 · 프로파일 미정의 |
| 자식 서버가 안 뜸 | `logs/server-<프로파일>.log`의 `기동 거부` · 포트 | 인증 on 서버의 시크릿 누락(`ADMIN_PASSWORD`·`ADMIN_JWT_SECRET`·`AUTH_JWT_SECRET`) · 포트 점유 · Windows 제외 대역 |
| 프로파일이 `INVALID` | 주입값과 실효 설정 에코의 차이 | OS env·`.encenv` 우선순위로 **주입이 무시됨** · 오케스트레이터 미가용으로 1단 강등(`orchestrator_unavailable`·`package_missing`) |
| **`완료 - 턴 0회`** | `report.md` 1절 「프로파일별 기동 결과」의 **사유** 칸 | 전 프로파일 `INVALID`. 사유에 `로그인`·`401`·`ConnectError`가 있으면 인증 문제다 |
| `/auth/login 로그인 실패 (http 401)` | 웹 `/login` | 내장 테스트 계정이 서버 인증 DB에 없거나 비활성 → `/register`로 가입하거나 `--user`로 다른 계정 |
| `(http 423)` · `(http 503)` | — | 연속 실패로 잠김(30분) · 인증 DB 연결 실패(`AUTH_AUTH_DB_URL`) |
| 전 건 타임아웃 | LLM 접속 · `LLM_FABRIX_TOTAL_TIMEOUT` | 프로바이더 설정 · 폐쇄망 경로 |
| 한글이 깨지거나 런이 죽음 | 콘솔 코드페이지 | Windows `PYTHONUTF8=1` 미설정 |
| 런 중단 후 포트가 안 풀림 | 남은 프로세스 | 고아 워커. Windows는 `taskkill /PID <pid> /T /F`(PID 확인 후) |

인증 실패 메시지 전체표는 94 ⑨-5(시점 기록)에 있다. 메시지 문구는 2026-09-21 코드와 같다.

### ⑨ 시나리오를 고치거나 추가할 때

- 어휘 정본은 `testdata/scenarios/_schema.yaml`과 `tests/test_scenario/test_assertion_coverage.py`의 `KNOWN_KEYS`다.
  - 단언 키는 2026-09-21 기준 **22종**이다(94 헤더의 "19종"은 Y-4·Y-5 이전 값).
  - 하위 키로 `options_contains`·`optional_columns`·`filled_columns`가 있다.
- 러너 동작 키(94 ⑩-9):
  - `auto_answer`(시나리오 dict / 턴 `false`), `replay`, `concurrent`, `action`(`seed_reload_idempotency`), `setup`(`synonym_add`).
  - `teardown`(`drop_thread`·`unregister_synonym`만 지원 — 그 밖은 10절 `teardown 미지원`), `unregister_words`.
  - `replay`·`concurrent`·`action`은 하나만 선언한다.
- 고친 뒤 순서: `--dry-run` → `--mock --only <ID>` → `pytest tests/test_scenario -q`.
- 벤치 스위프(109) 워크로드는 `replay`·`concurrent`·`action`·`setup` 시나리오를 자동으로 제외한다.
- **시나리오 증설은 동결 상태다**(94 §18 「이 후속이 하지 않는 것」). 예외는 Y-10 하나다. 동결 해제 여부는 §3.4의 `94·G-11`이다.

### ⑩ 맥북 로컬 MLX로 돌릴 때 (D-222 · **D-240** · `plans/100`)

> **D-240(2026-09-21 · D-127 ①③ 개정)**: 실 LLM이 필요한 테스트의 기본 경로는 로컬 MLX다. 두 평면이 모두 `mlx`(루프백)면 **사용자 승인 없이** 돌린다. 하네스 진입점은 바뀌지 않았다 — `--run`은 종전대로 `external_planes` 판정으로 갈린다(D-216·D-222). 건별 승인이 남는 것은 과금 외부 API와 가드를 끄는 `RUN_E2E=1`이다.

- **설정**: `.env`에 `LLM_PROVIDER=mlx` · `ORCHESTRATOR_PROVIDER=mlx`와 두 모델 ID를 둔다(`docs/03_setup_guide.md` §7.2). 서버는 별도 터미널에서 `scripts/mlx_server.sh`로 먼저 띄운다.
  - mlx는 두 평면 모두 비과금이다. 인자 없이 치면 **전 시나리오가 실 실행된다.**
- **실행 직전 자동 점검**: MLX 서버 도달과 **1토큰 생성**을 먼저 확인하고, 실패하면 앱 서버를 띄우기 전에 멈춘다. `/health`는 생성 스레드가 죽어도 200을 돌려주기 때문이다.
- **범위를 좁힌다**: 1턴이 9B 기준 354초였다. 전체는 수십 시간이므로 `--only`·`--group`으로 돈다.
- **기능 판정만 본다**(D-240 ④): 성능 판정은 노트북 LLM이라 운영 기준과 비교되지 않는다. ⚠ **사다리 단 arm 비교(③)의 결론을 MLX로 내지 말 것** — 직전 run에서 2단 사망 원인의 절반 이상이 60초 타임아웃이었으므로 이 비교의 주축은 지연이고, 지연 결론은 **내부망 run에서만** 낸다. MLX로 할 수 있는 것은 3.5단 표적 재현(CU-A1 시트·CU-H1 단언·CU-B2 역질문 = 전부 기능 판정)과 arm 전개가 기대한 단으로 확정되는지 확인(`run.json profiles[].tier`)까지다. MLX 프롬프트 캐시는 앱 서버 밖에 있어서, 프로파일이 바뀌어도 비워지지 않는다.
- **무효 판정**:
  - 오케스트레이터 미가용으로 1단이 내려간 프로파일(`degraded_reason=orchestrator_unavailable`·`package_missing`)은 **INVALID**다.
  - 운영자 선택으로 비기준 단이 된 경우(`intent_flag_on`·`semantic_routing_off`)는 리포트 상단에 경고만 남는다(D-221 ⑤ · 기준 단은 3단 — D-225).
  - 종전 어휘 `flag_off`는 D-225로 폐기됐다.
- **공유 서버**: 8080은 여러 세션이 함께 쓴다. 돌리기 전에 점유 세션에 **순서를 맞추고**, **자기가 띄우지 않은 서버는 종료하지 않는다**(D-240 ④). 동시 요청이 몰리면 9B도 Metal OOM이 난다.
- **장애**: MLX 로그에 `Insufficient Memory`가 있으면 생성 스레드가 죽은 것이다. 서버를 재기동한다. 32GB 맥북에서는 9B를 쓴다.

Windows 고유 절차는 **부록 A**에 있다(원 94 부록 A를 옮겼다).

---

## 0. 요약

### 0.1 한 줄 결론

**하네스는 이제 제품을 잴 수 있지만, 아직 한 번도 기준 경로(3단)에서 재지 않았다.**
- 폐쇄망 run 3건은 전부 판정 자격이 없다.
  - 첫 번째는 환경이 빠졌다.
  - 두 번째는 토큰이 죽었다.
  - 세 번째는 2단에서 60초 벽에 죽었다.
- 가장 큰 레버는 코드가 아니라 **사다리 단**이다. 직전 run의 타임아웃 131턴이 2단 전용 노드에서 죽었다. 다만 3단은 한 번도 측정되지 않았다. 그래서 **2단·3단을 같은 run의 두 arm으로 나란히 재서 고른다**(2026-09-21 사용자 지시 · 가이드 ③).
- 그다음이 **판정 계약 한 줄**이다. 평가되지 않은 턴을 기능 분모에서 뺄 것인가(사람 판단 대기).

### 0.2 계획군 지도 — 원본 5건과 이 문서의 관계

| 원본 | 종료 전 파일명 | 역할 | 종료 시점 실제 상태 | 이 문서에서 |
|---|---|---|---|---|
| **94** | `94-WIP-feature-perf-scenario-suite.md` | 하네스 정본: 카탈로그 · 러너 · 판정기 · 리포트 · 분석기 · Windows | S0~S4 · §15~§18(X/T/Y/O/V1~V27) · Y-10 랜딩. 헤더의 "잔여 Y-8 1건"은 과소 기재였다(실제 잔여 20여 건) | 가이드 · §2.1 · §3.1 · 부록 A |
| **96** | `96-TODO-scenario-run-20260915-remediation.md` | run `20260915-131903` 분석 정본(T·C·P·S·O·V 6축) | T·C·O군은 94 §18에서 전건 랜딩, P·S군은 98로 분화. 무소유 잔여 4건(P-8·P-12·S-d·S-4) | §2.2 · §3.3 무소유 행 |
| **98** | `98-TODO-run-20260915-code-fixes.md` | 제품측 수정 단위(CU-1~CU-22 + §8 차수 F) | 차수 A~E 13건 + 차수 F 6건 랜딩. 파일명 `-TODO`가 실제(부분 랜딩)와 달랐다 | §3.3 |
| **99** | `99-TODO-rerun-experiment-design.md` | 재측정 실험 설계(E-0~E-3) | E-0 자동화(`preflight.py`) 랜딩 · E-2 로컬 선행(D-231) · E-1 폐쇄망 시도(판정 불가). E-3는 **설계대로 실행된 적 없다** | 가이드 ④ · §3.2 |
| **108** | `108-WIP-run-20260918-remediation.md` | run `20260918-182507` 분석 + 재테스트 절차서 | CU-A1~A3 · CU-B2 · CU-H1 · CU-H2 랜딩(미커밋 · D-236). 게이트 G-1~G-5 확정, G-6 대기 | 가이드 ③~⑥ · §3 |

### 0.3 지금까지의 폐쇄망 run — 무엇을 쟀고 무엇을 못 쟀나

| run | 환경 | 규모 | 사다리 단 · 인증 | 핵심 사건 | 판정 자격 | 분석 정본 |
|---|---|---|---|---|---|---|
| `20260914-154940` | Windows · fabrix · `env=sandbox` | 93턴 | — | closed 158건이 빠졌다(구 `--env` 기본값) · 93턴 중 48턴이 역질문에서 조기 종료 · SQL 미수집 | 없음 | 94 ⑩-10 |
| `20260915-131903` | Windows · fabrix · `69b665f` dirty | 218건 / 383턴 | 2단(당시 기록 `flag_off` — D-225로 폐기된 어휘) · 인증 on | **103턴(26.9%)이 러너 JWT 만료(401)로 무효** · 유효 280턴 중 66%가 수동 판정 · 허위 불합격 14건 이상 | 없음 | 96 |
| `20260918-182507` | Linux `nclago01` · fabrix · `5b093016`(저장소에 없음) dirty | 218건 / 380턴 | **2단 `intent_flag_on`** · **인증 off** | **199턴(52.4%)이 60초 타임아웃.** 그중 **131턴이 2단 전용 노드**(`result_aggregator` 95 · `replanner` 36)에서 사망. 3단에는 이 두 노드가 없다. `repeat: 1` | 없음(2단 · dirty · 인증 off · 반복 없음) | 108 |

벤치 스위프 run `20260914-185540`(62 arm · 6,567턴)도 판정 자격이 없었다. 이 run은 109 소관이다.

### 0.4 랜딩 현황 한눈에

| 축 | 랜딩 | 근거 |
|---|---|---|
| 하네스 골격 | `scripts/scenario/` 14파일 · 카탈로그 220건 · 리포트 11절 · 분석기 6종 · `tests/test_scenario/` 25파일 | 94 §9 · D-212(예약) |
| 기본 실행 · 역질문 자동 응답 · 내장 계정 | 인자 없는 실행 = 내부망 실 실행(두 평면 판정) · `config/scenarios/auto_answer.yaml` | D-216 · D-222 |
| SQL 감사 수집 · K군 러너 동작 | `executed_sqls` · `replay`·`concurrent`·`setup`·`seed_reload` | D-217 |
| 측정 신뢰성 | 401 재로그인(T-a) · 수명 80% 선제 갱신(T-b) · `invalid` 분모 제외(T-c) · 무효율 5% 경고(T-e) · 분할 실행(X-1~X-4) | D-218 · D-221 |
| 판정 계약 교정 | Y-1~Y-7 · Y-9 · Y-10 · 불가 사유 고정(O-b) · 사다리 경고(O-c) | D-218 · D-220 · D-221 |
| 다른 계획이 94 코드에 넣은 것 | 실행 순서(R-6) · 스위프가 94 리포트 호출(R-7) · 실효 설정 보존(R-9) · `timeout` 분류 우선(CU-H2) · opt-in 실패 안내(102 결정 B) | D-238 · D-236 · D-224 부기 |
| 제품 수정(98 차수 A~E) | CU-1 장비명 · CU-2 LIMIT · CU-3 존 역질문 · CU-8 절단 고지 · CU-10① 후보 필터 · CU-11 전체 타임아웃 · CU-12 numeric · CU-16 멀티 LIMIT · CU-17 few-shot · CU-19~22 | D-220 · D-221 · D-231 |
| 제품 수정(108 = 98 차수 F) | CU-A1 시트 환각 · CU-A2 산문 예산 · CU-A3 로그 레벨 · CU-B2 미등록 존 · CU-H1 단언 | D-236 · **미커밋** |
| 종결(결함 아님) | 98 CU-7(알람 임계값 — D-202 4차) · 98 CU-9(구현 존재 — 미발화 원인만 잔여) | 98 §3 |

---

## 1. 장부 규칙

1. **행 키 = 원 계획 번호·원 ID**다(예: `98·CU-4` · `108·CU-B3` · `94·Y-11`). **번호를 다시 매기지 않는다.**
   - 원본에 번호 공간이 여럿이다. 98은 CU-1~CU-22, 108은 CU-A·B·H를 쓴다.
   - 게이트 번호는 계획마다 뜻이 다르다. G-5만 해도 96에서는 W2 시점, 98에서는 LOB, 108에서는 arm이다. 그래서 **게이트도 반드시 접두를 붙인다.**
   - 이 문서가 새로 올리는 물음만 `110·Q-n`을 쓴다.
2. **항목마다 원 계획 절 · 소재지 · verify를 적는다.** 링크 없는 항목은 등재하지 않는다(D-208).
3. **한 항목은 한 행에만 둔다.** 같은 잔여가 여러 원본에 적혀 있으면 한 행으로 합치고 키 칸에 `≡`로 병기한다.
4. **완료는 이 문서의 행을 닫는 것으로 추적한다.** 상태 칸을 고치고 §7에 1줄을 추가한다. 원본은 고치지 않는다. D-번호 상태가 바뀌면 `docs/02_decision.md`를 갱신한다.
5. **run 결과를 적을 때는 출처 4종을 같이 적는다**: run id · 커밋(이 저장소에 있는가) · 사다리 단 · 인증 on/off. 하나라도 빠지면 기준선 인용 근거로 쓰지 않는다.

---

## 2. 계속 유효한 계약 (요약 — 설계 정본은 원 절)

### 2.1 카탈로그 · 판정 (94)
- **카탈로그 SSOT**: 실행 정본은 `testdata/scenarios/` YAML이다. `plans` 필드는 필수이고 군 문자는 재번호하지 않는다(94 §3.1·§3.2).
- **판정 3계층 + LR**: L1 결정적 · L2 구조 · L3 수동 유보(합격으로 세지 않는다) · LR 대응 등급. 기능 판정과 성능 판정은 다른 축이다. LLM-as-judge는 쓰지 않는다(94 §2 ①② · D-035).
- **대응 등급**(R군): answer · correct · clarify · guide · partial · refuse · error 7종. 금지 3종(silent_wrong · hang · crash)은 무조건 불합격이다. 대조군 쌍을 강제한다(94 §3.7·§3.8).
- **분석기는 제안까지만 한다.** 적용은 사람이 한다(94 §6.1). 1회 관측으로는 처방하지 않는다. 반복 ≥3회에서 전건이 같은 키로 실패하면 결정적 결함으로 본다(96 §3 C-5 · D-218 ⑤).
- **상태 오염 관리**: 쓰기는 teardown과 순서 고정으로 다룬다(94 §2 ③). run 전용 체크포인트를 쓴다. 알람 워커는 격리한다(`ALARM_ENABLED=false`).
- **`src/`를 수정하지 않는다**(94 §14). 제품 수정은 §3.3 행에서 따로 추적한다.

### 2.2 측정 신뢰성 (96 · 94 §15~§18 · D-218)
- **측정을 고치기 전에 제품을 고치지 않는다**(96 §8).
- 리포트 원값을 쓰기 전에 **무효 턴을 뺀 판정표를 다시 만든다**(96 §1).
- **설정 에코를 대조한다.** 확인하지 못한 주입은 통과로 세지 않는다. 불일치는 INVALID다(94 §4.2·§4.5).
- 러너 401/403은 `invalid`로 두고 분모에서 뺀다. 무효율이 5%를 넘으면 리포트 최상단에 경고하고 회귀 비교에서 뺀다(T-c·T-e).
- **허위 불합격 6유형** — 제품은 옳았는데 하네스가 틀렸다고 판정한 경우다.
  - 96에서 나온 4유형: 개수 계약(C-1) · 공란이 정답인 열(C-2) · 팬아웃 합계 대 per-DB 기대값(C-3) · 리터럴 표기(C-4).
  - 108 §1.6에서 나온 2유형: 결정적 가드 치환으로 충족할 수 없는 단언(B-07·B-08) · run 간 상태 잔존(I-03~06).
  - 분류기 규칙 순서 결함(CU-H2)도 같은 계열이다.
- **「측정 불가」와 「0」을 구분한다.** 제품이 싣지 않는 값은 추정하지 않고 고정 문구를 남긴다(O-b · `analyze.LLM_COST_UNMEASURABLE`).

### 2.3 재측정 · 비교 (99 · 108)
- **비싼 것을 먼저 돌리지 않는다**: E-0 사전 점검 → E-1 DB 조회 → E-2 표적 재현 → 전체 → 기준선 선언(99 §1·§3.4). `[판정불가]`는 추정으로 메우지 않고 게이트를 미결로 둔다.
- **사다리 단이 다른 run끼리 직접 비교하지 않는다.** 단과 무관한 계수 지표로 판정한다(108 §5.3).
- **재측정 직전에는 통제되지 않은 변수를 얹지 않는다.** 프롬프트나 생성 경로를 바꾸면 랜딩분의 효과를 귀속할 수 없다(108 G-1 (b) · 98 §8.3). 재테스트 전에 넣어도 되는 것은 **하네스 측** 변경뿐이다.
- **반복은 결론이 반복에 의존하는 시나리오에만 3회 적용한다**(99 §3.2). 프로파일은 질문에 필요한 것만 쓴다(99 §3.1).
- **사다리 단도 arm으로 비교한다**(2026-09-21 사용자 지시). `.env`를 고쳐 한쪽 단만 돌리지 않는다. 운영할 단은 재어 보고 정한다(가이드 ③).
- **arm을 분리한다**(108 §5.2b). 존 자동응답 on은 기능·성능 측정용, off는 라우팅 측정용이다. 두 run이 반대편 극단에서 같은 결론에 닿았다.
  - 20260918은 자동응답 **있음** → 라우팅 판정 불가.
  - 20260914-185540은 자동응답 **없음** → 역질문에서 56.4% 차단.
- **이 실험이 답하지 못하는 것을 명시한다**(99 §0 · §6).

### 2.4 제품 수정 (98 §0 · §6)
- **신규 `enable_*` 플래그를 만들지 않는다**(D-162). 동작 변경이 불가피하면 기존 플래그의 기본값을 바꾸고 사람 판단 항목으로 올린다.
- `config/`로 풀리는 것을 `src/`로 내리지 않는다. DB별 특화는 `src/db_adapters/{db}/`에 둔다(D-089).
- **착수 전에 워드 경계 grep으로 구현 부재를 확인한다.** "부재"로 읽혔지만 실제로 구현이 있던 사례가 CU-8·CU-9·CU-10③으로 세 번 반복됐다.
- 회귀 방어(98 §6):
  - 프로필 4종 대칭을 테스트로 고정한다.
  - 버그를 정답으로 굳힌 기존 테스트는 전수 grep한 뒤 교체한다.
  - 게이트 기준선은 자기 델타만 소거한다.
  - 기준선 대조는 `git worktree`로 한다.
- **2단 전용 결함 처분 규율**(108 G-2): 기준 경로는 3단이다(D-225). 2단에만 있는 결함에는 투자하지 않는다(D-161). 단 **1단에서도 발현하는 결함에는 이 선례가 전이되지 않는다**(107 G-9).

### 2.5 경계
- **109(벤치 스위프)**: 카탈로그 · 실행 원자 · 판정기는 이 문서(원 94) 소유다. 109는 소비한다. 109는 arm 간 상대 비교, 이 문서는 절대 판정을 한다(94 §8 · §15.5).
  - 계정 정책이 의도적으로 다르다. 이 하네스는 계정이 없으면 내장 테스트 계정으로 로그인한다(D-216). 109 스위프는 계정이 없으면 멈춘다(D-215).
- **107(프롬프트 재작성)**: E2E 시나리오와 `rewrite_trace`의 수집·판정·리포트는 이 문서 소유다. `rewrite.yaml` 슬롯 채점과 `rewrite_trace` 적재는 107 소유다(원 94 §19.1).

---

## 3. 잔여 장부

상태 표기: **미구현** = 코드 0 · **부분** = 일부만 랜딩 · **미실행** = 실행 항목 · **판단 대기** = 사람 판단. 판정 근거는 2026-09-21 코드 실측이다.

### 3.1 하네스 (`scripts/scenario/` · `testdata/scenarios/` · `docs/30`)

| 키 | 내용 | 원 계획 절 | 소재지 | verify | 선행·차단 | 상태 |
|---|---|---|---|---|---|---|
| **`110·N-1`** | **사다리 단 arm을 전 시나리오에 적용하는 실행 수단 — `--arm <프로파일>`(반복 가능).** `--profile`은 시나리오의 `profile:` 필드로 거르기만 하므로 arm에 쓸 수 없다. `--arm`은 각 시나리오의 자기 프로파일 **위에 병합**한다(치환 아님 · 키 충돌 시 arm 우선) — 그래서 D군 8건이 `TEXT2SQL_ALARM_DETERMINISTIC`을 유지한다. 조합 이름은 `<시나리오 프로파일>+<arm>`이고(예: `optin_alarm+tier3_router`) 산출물 경로·재개 키·`run.json`이 모두 그 이름을 쓴다. **`raw.jsonl` 행과 `run.json` `profiles[]` 양쪽에 `arm`(arm id **원본** · 덧씌우기 없으면 `null`)과 `base_profile`(시나리오 자기 프로파일)을 별도 칸으로 싣는다** — 소비자는 조합 이름을 **파싱하지 않고** `arm_of(row) = row.get("arm") or row.get("profile")` 한 줄로 읽는다(벤치 `read_observations`·`scan_health` · 회귀 비교 키 `(env, tier, base_profile, arm)`). `arm`에 조합 이름을 넣으면 D군이 기준선 arm에서 떨어져 나가 쌍체 비교에서 빠진다. `split_arm_profile`은 그 칸이 없는 **옛 run 전용 폴백**이고 정상 경로는 쓰지 않는다. `summary.json` `by_profile[]`과 `report.md` 1절 「프로파일(arm)별 판정」이 arm별로 분리 집계한다. 실행 순서는 프로파일 major · arm minor다(중단해도 먼저 끝난 프로파일에서는 두 arm이 남는다). `--estimate`도 전개를 반영한다(승인 근거가 실제 실행의 절반이 되지 않게). ⚠ **공유 병합 함수(`runner.merge_arm_profiles`)까지만 랜딩했고 `scripts/bench/sweep.py` 호출부 교체는 보류했다** — 그 파일은 병행 세션 소유다(`110·N-2`) | 가이드 ③ · 2026-09-21 사용자 지시 | `scripts/scenario/runner.py`(`ARM_SEPARATOR`·`ArmBinding`·`arm_profile_name`·`split_arm_profile`·`merge_arm_profiles`·`_profile_order`·`iter_executions`·`estimate`·`_execute`) · `__main__.py`(`--arm`·`_check_arms`·`_print_arm_plan`) · `server.py`(`ProfileStatus.arm`·`base_profile`) · `report.py`(`profile_breakdown`·`_arm_section`) | `tests/test_scenario/test_arm_fanout.py` **23건** 통과(칸 3종 · `arm_of` 폴백 3경우 · 체크포인트/로그 arm별 분리 · 조합 이름 ASCII). 실측(2026-09-21): `--dry-run --arm tier2_intent --arm tier3_router` → **440건 전개 · 기동 4회**, `optin_alarm+*` 8건이 알람 플래그 유지. `--mock` 재현(2026-09-21 · run `20260921-151235` — **mock run 디렉터리는 재생성되므로 항구 근거는 위 테스트다**)의 `raw.jsonl`을 `arm_of`로 묶으면 `{tier2_intent: [A-01, D-01], tier3_router: [A-01, D-01]}` 2바구니이고(D군이 기준선 시나리오와 같은 arm) `run.json profiles[]` 4행이 `name`·`arm`·`base_profile`·`tier`를 싣는다. ⚠ mock 이라 `tier`는 `"mock"` 이다 — **arm 이 기대한 단으로 확정되는지는 실 run 에서만 판독된다**(가이드 ④ 「멈추는 조건」) | 없음 — **재테스트(§3.2) 차단 해소.** 벤치 읽기 쪽(`arm`으로 묶기)은 109/d9 소관이다 | **랜딩**(미커밋) |
| **`110·N-2`**(신규) | **벤치 스위프의 arm 치환 결함 교정.** `scripts/bench/sweep.py`의 `fanout_scenarios`(`:325` `dataclasses.replace(scenario, profile=arm.arm_id)`)와 `run_arms`(`:529` `catalog.profiles = {arm.arm_id: dict(arm.env) for arm in arms}`)는 둘 다 **치환**이라, D군 8건이 **모든 스위프 arm에서** `TEXT2SQL_ALARM_DETERMINISTIC`을 잃는다(2026-09-21 두 세션이 각각 독립 확인). 교정은 `runner.merge_arm_profiles`를 호출하도록 바꾸는 것이다 — 함수는 이미 랜딩했고 sweep의 arm(`ArmSpec`)을 `catalog.profiles`에 등록한 뒤 `RunConfig(arms=[...])`로 넘기면 된다. ⚠ **sweep 하류가 `profile == arm_id`를 전제한다**(`read_observations`가 `(profile, scenario_id, repeat)`로 접고 `scan_health`가 `profile == "baseline"`으로 기준선을 판정한다) — 쓰기 쪽은 이미 `arm`·`base_profile` 칸을 싣고 있으므로 **읽기 쪽을 `arm_of(row) = row.get("arm") or row.get("profile")`로 바꾸면 된다**(d9 소관 · 파싱 불필요). | 2026-09-21 36 세션 · d9 세션 합의(공유 병합 함수 1개) | `scripts/bench/sweep.py` `fanout_scenarios`·`run_arms`·`read_observations` · `scripts/bench/compare.py` | 스위프 D군이 알람 플래그를 유지 · 기존 `tests/test_scripts/test_bench_sweep.py` 유지 | **영역 조율** — `scripts/bench/sweep.py`는 병행 세션 `collectorinfra-d9`가 선언한 영역이다. 착수 주체 미정 | 미구현(보류) |
| **`108·G-6`** ≡ `97·G-1` | **판정 계약 — 단언이 평가되지 않은 턴을 기능 합격률 분모에서 빼고 사유별로 분리 집계.** 사유 3종은 ①`invalid`(401·teardown 오염 = **하네스 과실**) ②`timeout`(요청 상한 초과 = **제품 성능 축**) ③`clarify_blocked`(역질문으로 끝나 단언 미도달 = **하네스 구성**)이고 **합치지 않는다** — 합치면 "하네스를 고쳐야 할 것"과 "제품을 고쳐야 할 것"이 한 숫자가 된다. 행에 **`unevaluated_reason` 칸 1개**를 싣고(`func_verdict` 어휘는 늘리지 않는다 — docs/18:272 사고), 규칙 정본은 `report.unevaluated_reason` 한 곳이다. **`clarify_blocked`는 「역질문이 뜬 턴」이 아니라 「턴의 **최종** `response_mode`가 `clarify`인 턴」**이다. 러너가 `_answer_questions`가 돌려준 **답변 뒤** 관측치로 `obs`를 갈아끼우고(`runner._run_once`) `evaluate_turn`이 그 최종 `obs`로 `response_mode`를 정하므로, 자동응답이 답해서 진행된 턴(20260918 380턴 중 221턴·58.2%)은 `answer`로 남아 **분모에 남는다**(실 경로 테스트로 확인). 자동응답이 **먹지 않아**(같은 역질문 반복) 역질문으로 끝난 턴은 `auto_answers`가 기록돼 있어도 `clarify_blocked`다 — 두 칸은 독립이다. 역질문을 **기대한** 턴(R3-03·I-01~06)은 그 자체가 판정 대상이라 분모에 남는다. 리포트 표기는 **벤치와 통일**한다: `역질문 차단 N건 (자동응답 M건 · 발동률 P%)` / 자동응답이 없던 run은 `(자동응답 없음)`. 발동률 분모는 벤치 `RunHealth.auto_answered_turns`와 같다(`auto_answers`가 실린 턴 / 전체 턴). 이중 차감은 **제거 사다리**(전체 → −invalid → −timeout → −clarify_blocked → 분모)로 구조적으로 막는다 — `제거 합 + 분모 == 전체`를 리포트가 매번 단언한다 | 108 §5.2e · 97 §11.5 「G-1 영향 범위 조사」 · 계약 정본 94 §15~§18 | `runner._row`(칸 1개 · d7 승인 범위) · `report.py` `unevaluated_reason`·`row_unevaluated`·`scored_rows`·`unevaluated_summary`·`_unevaluated_section`·`_removal_ladder` · `summary.json` `unevaluated{}` · `report.md` 2절 「판정 분모(턴 단위 · 제거 축)」. **`assertions.py`는 미접촉**(d7 영역) | `tests/test_scenario/test_unevaluated_contract.py` **21건** 통과. 실측(2026-09-21 · `evaluate_turn` 실 경로 픽스처): 사유 3종이 각각 도출 · **실 자동응답 경로**(`_run_once` + 존 역질문)에서 진행된 턴은 `response_mode=answer`로 남아 분모 유지, 자동응답이 먹지 않은 턴만 `clarify_blocked` · 기대 역질문 턴은 분모에 남음 · 제거 사다리 합 일치 · **합격률 25.0% → 66.7%**(108 교차 대조표와 같은 값 재현) · 표기 `역질문 차단 N건 (자동응답 M건 · 발동률 P%)` | 없음 — **2026-09-21 사용자 승인**("판정 계약 수용하라"). D-241 본문 등재는 **d9 소관**, 이 문서는 인용만 한다 | **랜딩**(미커밋) |
| **`108·CU-B3`** ≡ `97·G-2` | **teardown 폼필 기억 격리** — `forget_form_memory` 신설 + 미지원 teardown 오염 턴을 `invalid`로 표시. 공백은 폼필 기억 하나다. `clear_schema_cache`는 파괴적이라 D-217에서 의도적으로 뺐다(`a_routing.yaml:86-88`) | 108 §3.3 · §1.6 · 97 §11.5 · 설계 근거 94 §2 ③ | `runner.py:479` `SUPPORTED_TEARDOWN`(현재 `drop_thread`·`unregister_synonym`) | I-01~I-06(같은 픽스처)에서 I-03~06의 순서 의존 오염이 사라진다 | 격리는 선행 없음. `invalid` 표시는 `108·G-6`과 함께 | 미구현 |
| **`94·§4.4`** ≡ 93 §4.4 ≡ 97 §7-4 | **`raw.jsonl` 적재 전 PII 마스킹**(`trace_writer._sanitize` 계열 재사용). O-a가 응답 4,000자를 싣게 되면서 노출면이 커졌다. 폐쇄망 반출본에도 적용한다 | 94 §4.4 · 97 §7 #4 | `runner._row` | 마스킹 단위 테스트 + 반출본 grep | 없음 — **안전 항목이라 재테스트 전 권장** | 미구현 |
| **`94·O-a 잔여`** | `column_mapping` 수집 코드가 없어 항상 `{}`다. `/query/{id}/mapping-report`를 파싱해 채운다. `98·J-4`의 판정 수단이다 | 94 §17 · 96 §6 O-a | `scripts/scenario/client.py` · `runner.py:453-461` | `Observation.column_mapping` 채움 | 없음 | 부분 |
| **`94·Y-8`** ≡ `96·V-b` | `docs/30` 「분류 미확정」 28건을 사람이 확정하고, `coverage_gap` 상단에 미확정 건수 헤드라인을 싣는다 | 94 §16.2 · 96 §7 | `docs/30_scenario_coverage.md` · `analyze.coverage_gap` | 미분류 0 또는 건수 줄 | 사람 판단 | 부분(행별 사유만 있음) |
| **`94·docs/30 분모`** | 커버리지 분모(95행)가 plans/1~94에서 멈춰 있다. 95~110을 반영한다 | 94 §6.4 | `docs/30` | 행 추가 | 없음 | 미구현(신규 등재) |
| **`94·V29 기준선`** ≡ `96·V-c` | **회귀 기준선 선언** — `analyze.regression`이 기준선 run id를 지정받고, 같은 사다리 단·프로파일끼리만 비교한다. 현재는 env만 거른다(`analyze.py:272-283`). 20260918 `regression.md`는 저장소 어디에도 없는 run `20260918-175454`를 비교 대상으로 잡았다 | 96 §7 V-c · 94 §5.3 · §19.4 V29 | `scripts/scenario/analyze.py` | 기준선 id가 `meta`에 기록 · 단이 다르면 비교 거부 | 없음(재테스트 후 첫 선언) | **부분 랜딩 — 열림**(2026-09-21 · d7) — 비교 키 `(env, tier, base_profile, arm)` 튜플(`analyze.COMPARISON_AXES`·`comparison_keys`·`select_baseline` — 옛 run은 `arm=None`, 미관측 `tier`는 막지 않는다)과 고른 기준선의 **`summary.meta.regression_baseline` 기록**(run id·축·키·건너뛴 run과 사유)이 들어갔다. **기준선 run id를 사람이 지정하는 수단은 없다**(CLI는 `__main__.py` — 36 소유라 미접촉) · 실제 기준선 선언은 §3.2 재테스트 후 · 테스트 `test_plan94_s19_rewrite.py` V29 5건 · **후속(미구현 · 2026-09-21 36 통지)**: 판정 계약(`108·G-6` · D-241 등재 예정) 적용 전후 run은 기능 합격률 분모가 달라 직접 비교하면 안 된다(20260918 기준 25.0%→66.7%) — 원시 행의 `unevaluated_reason` 칸 존재를 계약 판별 축으로 비교 키에 더하고, 다르면 비교 거부 + `meta` 사유 |
| **`94·§2-④`** ≡ 93 §2-③ | 실행 중 temperature 고정 주입(D-194) | 94 §2 ④ | `runner.ISOLATION_ENV` · profiles | 설정 에코에 고정값 | 없음 | 미구현 |
| **`94·§4.2-4`** ≡ 93 §4.2-4 | 군별 첫 1건 워밍업 폐기 | 94 §4.2 | runner | — | 없음 | 미구현 |
| **`94·Y-11`** | `expect.rewrite.gate` 단언 — 게이트가 `pass_through`인 턴에서 LLM 프롬프트 바이트 불변 | 94 §19.2 | `assertions.py` · `_schema.yaml` · `KNOWN_KEYS` | 단언 전수 테스트 확장 | 107 W2 **랜딩**(2026-09-21) · 카탈로그 선언은 `94·O-e` 섀도 run 뒤 | **기계 랜딩**(2026-09-21 · d7) — `assertions._check_rewrite`(`expect.rewrite.gate`: `pass_through`·`rewritten`) · `_schema.yaml` · `KNOWN_KEYS` · 단언 키 전수 쌍. 레코드가 없으면 불합격이 아니라 보류. 프롬프트 바이트 불변은 코드 계약으로 고정(`test_plan107_intent_frame.py`). **카탈로그 선언 0건** — 꺼진 run에서 합격이 보류로 바뀌므로 섀도 run으로 턴별 게이트를 본 뒤 선언한다(plans/107 §12.7) |
| **`94·Y-12`** | `rewrite.slots_preserved` — 슬롯(limit·기간·지표·대상·존) 값 보존. 실패 메시지에 슬롯별 before/after | 94 §19.2 | 위와 같음 | 슬롯 변동 시 불합격 | 107 W2 **랜딩**(2026-09-21) · 카탈로그 선언은 `94·O-e` 섀도 run 뒤 | **기계 랜딩**(2026-09-21 · d7) — `rewrite.slots_preserved`: 검증 결과 전부 `pass`. 실패 메시지는 **채널별 사유**(`{consumer.channel: fail:*}` + 프레임 슬롯 목록 — 계획의 "슬롯별 before/after" 대신, 검증기가 내는 단위가 채널이다). 검증 결과가 비면 보류. **카탈로그 선언 0건**(위와 같은 이유) |
| **`94·O-e`** | `raw.jsonl`에 `rewrite_trace` 적재 · `pass_through` 비율과 검증 실패율을 1절 지표로 · 미적재면 `REWRITE_TRACE_UNMEASURABLE` 고정 문구 | 94 §19.3 | `runner._row` · `report` · `analyze` | 비율 표기 · 미적재 시 칸 비움 | 107 W2 **랜딩**(2026-09-21 — `rewrite_trace` 적재: done 페이로드 + 감사 이벤트) | **랜딩**(2026-09-21 · d7) — 1순위 done `rewrite_trace`, 폴백 서버 로그 `rewrite_trace` 이벤트(`SqlAuditTail.collect_rewrite_traces`) · `raw.jsonl` `rewrite_trace`(없으면 None) · `analyze.rewrite_trace_summary`·`REWRITE_TRACE_UNMEASURABLE` · 실패 분류 `rewrite`(심각도 3). **리포트 1절이 아니라 `bottleneck.md` 「재작성 게이트·검증」 절**에 싣는다(이탈 — `LLM_COST_UNMEASURABLE`와 같은 자리). 측정 arm `tier2_intent_frame`·`tier3_intent_frame`(`profiles.yaml`) |
| **`94·V28~V31`** | V28 107 W0.5 전후로 F-01/03/04·A-01·B-10·A-02·G-02 판정 불변 · V29 107 W3 비열화(위 `94·V29 기준선` 선행) · V30 미적재 시 비율 칸 비움 · V31 107 골든셋과 하네스 상호 비참조를 import 테스트로 고정(조건은 현재 성립, 고정 테스트는 없음) | 94 §19.4 | `--only` 표적 재현 · `tests/test_scenario/` | 각 기준 | 107 W0.5 **랜딩** · W3 메커니즘 **랜딩**(소비자 켜기 전) · `94·O-e` 랜딩 | **부분 랜딩**(2026-09-21 · d7) — V28 대응표 단언 고정 테스트 랜딩(실 run 전후 대조는 **미실행** · 사용자 승인 사항) · V29 위 `94·V29 기준선` 행(비교 키·기록 랜딩, 수동 지정 미구현) · **V30·V31 랜딩** — `tests/test_scenario/test_plan94_s19_rewrite.py` 30건 |
| **`94·S7-a`** | `docs/29`를 카탈로그에서 생성하고, 헤더에 "실행 정본은 `testdata/scenarios/`"를 명시 | 94 §3.4 | `docs/29_query_performance_test_plan.md` | 생성 스크립트 · 헤더 | `94·G-7` | 미구현 |
| **`94·S7-b`** | **D-212 본문 등재**. 예약만 있고 본문이 없는데, D-216·D-217·D-218이 이미 "D-212 ⑨ 개정"을 참조한다 | 94 §13 | `docs/02_decision.md` | `## D-212` 헤더 | 없음 | 미구현(문서) |
| **`94·S1-MNOP`** | M(장애 조사)·N(실시간)·O(스트림 UX)·P(내부 경로 L2)군 미작성. `optin_query`·`optin_invest`·`optin_realtime` 프로파일을 쓰는 시나리오가 0건이라 "기본 off 기능이 테스트된 것처럼 보이는" 위험(94 §11 R3)이 그대로 남아 있다 | 94 §3.1 · §9 S1 | `testdata/scenarios/` | 파일·시나리오 존재 | 증설 동결 해제(`94·G-11`) | 미구현 |
| **`94·S1b`** | R군 목표 75 대비 작성 44(R1 10/20 · R2 10/15 · R3 12/20 · R4 12/20) | 94 §3.7 | r1~r4 yaml | 건수 | `94·G-11` · 동결 | 미구현(동결) |
| **`94·S1-expect`** | `manual_review`만 있는 83건의 단언 이관: L 31(전부 sandbox) · R 41 · J 5 · A-09 · A-11 · C-08 · C-12 · D-06 · H-18 | 94 §3.4 · §11 R1 | 카탈로그 | `test_assertion_coverage`(판정 가능 ≥85%) | R군은 `94·G-10` | 부분 |
| **`94·G-2(b)`** | 알람(noise_gate)·UI e2e 트랙 결과를 리포트에 통합 | 94 §3.6 | `report` | summary 정규화 | `94·G-2` | 미구현 |
| **`94·G-8(b)`** | `report.html`(`--html` 옵션도 없음) | 94 §5.1 | `report.py` | html 산출 | `94·G-8` | 미구현 |
| **`94·O-d`** ≡ `96·O-3` | 체크포인트 비대(20260918 `checkpoints-baseline.db` 804 MB). "plans/70 후보"로 넘긴다고 적었지만 70에 기록이 없다. 반출 측면은 D-219 ②(축소본 반출 · 109)가 대응한다 | 94 §17 · 96 §6 | plans/70 또는 여기서 종결 | 이관 기록 또는 종결 사유 | 없음 | 미처리(문서) |
| **`94·양식 자리표`** | H·I군이 자리표 `fixtures/form_sample.xlsx`를 2회 참조 → 실물 양식으로 교체 | `tasks/todo-94.md` B | h/i yaml | 실물 양식 | 사람 | 부분 |
| **`94·V19·V20·A.7`** ≡ `94·S5` | Windows 실단말에서 무과금 전 경로·고아 프로세스 0 · Playwright Windows · sre_agent py≥3.13 병존 · DRM(폐쇄망 전용). 지금까지 Windows 실 run은 1회(20260914-154940)와 MLX 스모크뿐이다 | 94 §10 · 부록 A.7 · §9 S5 | 폐쇄망 Windows 단말 | V19·V20 | S5 규모 run | 부분 |
| **`109·CS-17`** | **멀티턴 도중 재개 시 문맥 없이 이어 돎.** 러너는 시나리오 실행마다 새 `thread_id`를 만들고, 재개 때 끝난 턴을 `continue`로 건너뛰었다. 그래서 남은 턴이 **새 thread에서 이전 턴 문맥·역질문 응답 재료 없이** 돌았다. **사용자 결정(2026-09-22 "멀티턴은 다시 돌려라" · 표지만 붙이는 안 기각)**: 일부 턴만 끝난 시나리오는 `already`를 무시하고 **1턴부터 새 thread로 전부 다시 돈다.** 다시 돈 사실은 `run.json` `meta.rerun_partial`(profile·scenario_id·repeat·done_turns·turns·attempt)과 콘솔 `[재개]`에 남는다. 같은 수정으로 두 가지를 함께 막았다. ①앞 턴이 fail/error로 끊겨 러너가 뒤 턴을 **일부러** 건너뛴 시나리오를 재개가 뒤 턴만 돌리던 것 — 이제 끝난 것으로 보고, 남은 턴의 건너뜀 사유(「선행 턴 N 이 fail - 후속 턴 판정 불가」)를 `skipped`에 다시 적는다(`run.json`은 끝에서 이번 시도의 `skipped`로 새로 쓰인다). ②94 리포트·분석기(`report.load_rows`)가 같은 키를 두 번 세던 것 — 벤치 `read_raw_rows`와 같은 규칙(뒤 행이 결과 · 위치는 처음 자리)으로 맞췄다. ②는 기존 무효 턴 재실행(X-1)에도 걸리던 중복 집계다. 벤치 `--segment` 재개와 `--resume` 공통 | 109 §3.1 CS-17 · 94 X-1(D-218 ③ · 부기 2026-09-22) | `runner._resume_state`·`_run_once`·`RawLog.verdict` · `report.turn_key`·`load_rows` | `tests/test_scenario/test_resume_integrity.py` — 부분 멀티턴 1턴부터 재실행·재실행 행의 `profile`·`arm`·`base_profile` 보존·뒤 턴 무효·전 턴 완료·앞 턴 불합격 끊김·끊긴 시나리오의 건너뜀 사유 재기록·새 run 불변 7건 + 리포트 마지막 행·키 동일성 4건 | 없음 | **랜딩**(2026-09-22 · 109 세션) |
| **`109·CS-16`** | **재개하면 끊기기 전 서버 로그가 사라짐.** 재개는 끝난 arm까지 서버를 다시 띄우고 `ServerHandle._pump_log`가 `logs/server-<프로파일>.log`를 `"w"`로 열었다. 이제 기동 시 기존 로그(0바이트 초과)를 `server-<프로파일>.log.prev-<마지막 기록 시각>`으로 옮긴다. **덧붙이지 않고 옮기는 이유**: 소비자(`SqlAuditTail` 크기 오프셋 · 사다리 판독)는 이번 시도 로그만 읽으면 된다. 보존본은 `server-*.log` glob에 걸리지 않는다. 옮기지 못하면 종전처럼 덮어쓰고 콘솔에 경고한다. 새 run은 파일이 없어 동작이 같다 | 109 §3.1 CS-16 | `server.ServerHandle._keep_previous_log`·`start` | `test_resume_integrity.py` 서버 로그 2건(보존·새 run 불변) | 없음 | **랜딩**(2026-09-22 · 109 세션) — 109 가이드의 「재개 전 서버 로그 복사」 우회 절차를 뺐다 |
| **`109·CS-19③`** | **재개 시 출처(커밋·dirty)가 덮여 두 판이 섞여도 드러나지 않음.** 러너는 `run.json`을 `_execute` 끝에서만 `"w"`로 썼다. 그래서 끊긴 run에는 출처가 없었고, 재개하면 재개 시점 값만 남았다. 고친 동작 넷: ①**시작 시점에도** `run.json`을 쓴다. `meta.in_progress: true`를 달고, 끝의 기록에서는 뺀다. 이전 시도의 `profiles`·`skipped`는 끝의 기록이 덮을 때까지 둔다. ②`meta.attempts`에 시도별 출처를 누적한다 — started_at·commit·dirty, dirty면 작업 트리 지문(`git status --porcelain`+`git diff HEAD` 해시). ③판이 다르면 `meta.provenance_mixed`를 남긴다(콘솔 `[주의]` · `report.md` §1 「출처 섞임」·「시도(재개)」 행). 출처가 없는 옛 시도는 「확인할 수 없다」로 적는다. ④분석기 `select_baseline`은 `in_progress` run을 기준선에서 빼고 사유를 적는다. 벤치는 `RunHealth.provenance_mixed`로 **건전성 주의만** 낸다(멈춤 기준 아님) | 109 §3.1 CS-19 ③ | `runner.attempt_provenance`·`provenance_mix`·`_previous_run`·`_execute` · `report.render_markdown` §1 · `analyze.select_baseline` · 벤치 `sweep.RunHealth` | `test_resume_integrity.py` 출처 6건 + §1 표기 2건 · `test_plan94_s19_rewrite.py::TestV29Baseline::test_unfinished_run_is_not_a_baseline` · `test_bench_campaign.py::test_출처가_섞인_재개는_건전성_주의로만_알린다` | 출처 섞임을 **멈춤 기준으로 올릴지는 사용자 판단**(정책 변경) | **랜딩**(2026-09-22 · 109 세션) · 멈춤 승격은 판단 대기 |
| **`109·CS-43`** | **회귀 기준선이 mock 폴더일 수 있음.** `analyze.select_baseline`이 모드를 보지 않았다. mock은 tier가 미관측(None)이라 `_incompatible`을 통과한다. 그래서 arm 구성이 같은 mock 리허설 폴더가 실 run의 기준선이 될 수 있었다. 이제 모드(`meta.mode`)가 다른 후보는 `skipped`에 `모드가 다르다(mock → run)`로 남기고 건너뛴다. 이번 run의 모드를 모르면(옛 형식) 제약하지 않는다 | 109 §3.3 CS-43 | `analyze.select_baseline` | `test_plan94_s19_rewrite.py::TestV29Baseline::test_mock_run_is_never_a_baseline_for_a_real_run` | 없음 | **랜딩**(2026-09-22 · 109 세션 · 코드 판독 결함 — 실 캠페인으로는 확인하지 않았다) |

**`108·G-6` 교차 대조**(108 §5.2e — 이 규칙이 특정 run의 숫자를 좋게 만들려는 것이 아님을 보인다)

| run | 지배 사유 | 현행 | 미평가 제외 | 변화 |
|---|---|---:|---:|---:|
| `20260918-182507`(380턴 · 108) | 타임아웃(160 중 100) | 25.0 % | 66.7 % | **+41.7 %p** |
| `20260914-185540`(6,567턴 · 109 스위프 · d9 실측) | 역질문 차단(6,567 중 3,705) | 12.5 % | 12.8 % | **+0.3 %p** |

같은 규칙이 한쪽은 2.7배로 올리고 다른 쪽은 거의 움직이지 않는다.
- 타임아웃은 감추지 않는다. 성능 축(`perf_fail` 138)과 단 무관 계수 지표가 그대로 드러낸다.
- 20260914 run에서는 teardown 오염을 측정할 수 없었다. 역질문 차단에 묻혀서 I-01~I-08이 균일하게 fail했다. 따라서 20260918의 I-03~06 4턴은 그 run으로 반증되지도 확증되지도 않는다.

### 3.2 재측정 · 재테스트 (실행)

| 키 | 내용 | 원 계획 절 | 소재지 | verify | 선행·차단 | 상태 |
|---|---|---|---|---|---|---|
| **`108·R-L1`** → **사다리 단 arm** | **2단·3단 arm 병행**(가이드 ③ · 2026-09-21 사용자 지시). 108 §5.1의 "3단 고정"을 대체한다. 프로파일 `tier2_intent`·`tier3_router`는 신설됐다(36 세션). ⚠ CLAUDE.md의 운영 실측("1·2·3단 모두 true = 1단 확정")과 20260918 기록(`intent_flag_on` = 1단 off·2단 on)이 어긋난다(96 O-4). `baseline` arm을 함께 돌린다면 폐쇄망 `.env` 실제값부터 확인한다 | 108 §5.1(대체됨) · 96 §6 O-4 | `config/scenarios/profiles.yaml` · 폐쇄망 `.env` | `run.json` `profiles[].tier`가 arm마다 기대한 단 | ~~`110·N-1`~~ **해소**(2026-09-21 `--arm` 랜딩) | 프로파일·실행 수단 랜딩 · **실행 대기** |
| **`108·재테스트`** ≡ `96·W2` ≡ `99·E-3` ≡ `94·S6` | **전 시나리오 재테스트 → 단별 첫 유효 기준선 선언**(2단·3단 arm · 가이드 ③). 통과 조건: arm별 단 확인 · `dirty: false` · 무효 ≤5% · 기준선 run id 기록(`94·V29 기준선`). 판정: 가이드 ⑥ 단 무관 지표 + 96 §9 / 99 §5 완료 판정(#9는 "1단 측정"에서 **2단·3단 arm 비교**로 재정의). 인증 on 권장 | 108 §5 · 99 §1~§3 · 96 §8~§9 | 폐쇄망 run | 가이드 ⑥ | ~~`110·N-1`~~ 해소 · `99·H-1` · (해석) `108·G-6` · 3단 해석은 `plans/103` 잔여와 대조 | 미실행(20260918은 기준선 아님 — §0.3) |
| **`108·§5.5`** | 표적 재현 7건(가이드 ④ 3.5단) | 108 §5.5 | `--only` + `--arm` | 가이드 ④ 기대표 | ~~`110·N-1`~~ 해소 | 미실행 |
| **`99·E-1`** | preflight DB 조회 2건으로 `98·G-5`(LOB `stringvalue` NULL 비율)와 `98·G-8`(b0 `name`의 `_`·`%`)을 판정. 2026-09-18 폐쇄망 시도는 `async for` 버그로 판정 불가였다. `783a4b5`에서 수정했고, 재시도 결과는 기록이 없다 | 99 §1 E-1 · 98 §5 | `preflight.py:461-570` | `[OK]`/`[주의]` 판정 | `ACTIVE_DB_IDS`에 cm_gp·b0 · 폐쇄망 | 미실행(LLM 0) |
| **`98·J-1`** ≡ `99·E-2` | H-10 월 피벗이 결정적 조립에 **진입하지 못했나, 폴백했나**(`query_generator.py:398` · `multi_db_executor.py:1817`). `98·CU-6`의 선행이다 | 98 §4 · 99 §1 E-2 | `--only H-10` + 진입·폴백 로그 | 원인 확정 | 로그 레벨 | 미실행 |
| **`98·J-2`** ≡ `99·E-2` | B-06·K-10 가드 우회 경로. CU-11 랜딩 후 20260918 B-06은 `pass`였다. 로그 대조로 닫고, CU-11의 SSE 소비 루프 배선을 실측한다(로컬 MLX B-06) | 98 §4 · §0 CU-11 행 | `query.py:1509·2150` · 로그 | 로그 대조 | MLX 서버 | 부분 |
| **`98·J-4`** ≡ `96·P-10` | H-03 '리소스유형' 공란이 매핑 실패인가 조회 누락인가. 108 CU-A1(빈 양식)과는 증상이 달라 별건으로 추정한다 | 98 §4 | O-a `column_mapping` | 원인 확정 | `94·O-a 잔여` | 미실행 |
| **`99·E-0-3`** ≡ 94 V21·V23 실물 | `--segment` 2조각 · `--resume-failed`의 **실물** 동작 확인. 현재는 합성 픽스처 테스트만 있다 | 99 §4 · 94 §18 | `runner.py:165·179` | `--mock --resume-failed <RUN_ID>` | 원본 run이 로컬 `results/scenario/`에 있어야 함 | 부분 |
| **`99·§3.2`** ≡ `99·H-3` ≡ `96·G-5` | **선택적 3회 반복**. 구현 수단은 카탈로그 시나리오별 `repeat:`(`catalog.py:111·449`)인데 비R군 선언이 0건이다. 대상 선정은 사람이 한다. 20260918에는 적용되지 않았다 | 99 §3.2 · §7 | 카탈로그 | 대상 시나리오 `repeat: 3` | `99·H-3` | 미구현 |
| **`99·L-3`** | 전건 반복 3회 | 99 §7.1 | — | — | 장기 점유 승인 | 미실행 |
| **`96·V-1`** ≡ `99·L-2` | 옵트인 프로파일 3종 측정(`94·S1-MNOP`와 짝) | 96 §7 · 99 §7.1 | `config/scenarios/profiles.yaml` | `run.json` profiles | 별도 실험 설계 | 미실행 |
| **`108·CU-B6`** ≡ `94·G-13㉯` | **존 자동응답 off B arm**(소규모 라우팅 측정 — 위치 표면어가 없는 질의 위주). 108 G-5와 94 G-13에서 확정됐는데, 이관처로 지목된 99에는 등재가 없었다. **이 문서가 소유한다.** 턴 단위 `auto_answer: false` 기계는 있다(`catalog.py:76·249`). arm 단위 스위치가 필요한지는 설계에서 정한다. 109의 워크로드 도달 문제(스위프 역질문 56.4%)와 같은 뿌리다 | 108 §3.3 · §5.2b · 94 §19.5 | 러너 · 카탈로그 | arm B 라우팅 정확도 | 재테스트 A arm 이후 | 미구현 |
| **`98·§7`·`98·§8.5`** | **랜딩분 효과 측정**. CU-1은 B-04·K-01 5/5가 목표인데, 20260918 K-01 repeat 3 turn 301에서 `r.hostname = 'cob0-bnndbp01'`(1/6)이 재발했다. CU-2는 J-03, CU-3은 66턴 → 20260918 5턴(`db_ids` 미기록 232턴이라 참고치), CU-12는 CU-H1 기준으로 갱신한다. 차수 F는 F-a~F-h다. CU-19~22는 로컬 재실행 검증 대기다(D-231) | 98 §7 · §8.5 | 재테스트 산출 | 항목별 | `108·재테스트` | 미실행 |

### 3.3 제품 수정 (`src/` · `config/`)

재테스트 전에는 프롬프트·생성 경로를 건드리지 않는다(§2.3). 98 §8.3에 따라 **F-1(CU-6·CU-9)만 재테스트 전 순위**다.

| 키 | 내용 | 원 계획 절 | 소재지 | verify | 선행·차단 | 상태 |
|---|---|---|---|---|---|---|
| **`98·CU-6`** ≡ `96·P-3` | 월 피벗이 `cmm_metric_stat_m`을 쓰지 않는다. 20260918 H-10·H-11·H-15·K-04가 전건 EAV 피벗이었다 | 98 §8.3 F-1 · §3 표 | 폼필 결정적 조립 · `assembler.py:61` | H-10·H-11·H-15·K-04 SQL에 `cmm_metric_stat_m` | `98·J-1` | 미구현 |
| **`98·CU-9`** ≡ `96·P-11` | 안내 문구 3종 미발화. 구현(`output_generator.py:822·833·894`)과 계약 테스트(`test_form_month_series.py:733` 등)는 있다. **왜 발화하지 않았나**가 잔여다. 108 §1.6은 "실결함 유지", 98은 "오분류"로 판정이 갈린다 | 98 §8.3 F-1 · §3 | `output_generator.py:140` | H-06·H-12·H-13 `response_text` 판독 | O-a 랜딩분(`runner.py:458`) | 부분(원인 미규명) |
| **`98·CU-10②③`** | ② 의미 유사도 상위 N 랭킹(N은 사람이 확정) · ③ "공란 유지" 1급 선택지. **③은 이미 UI에 있다**(`src/static/js/app.js:1977` · 서버 `output_generator.py:861·876`, 2026-07-31) → 확인 후 종결 후보 | 98 §3 CU-10 | `assembler.py:358-470` | — | ② N 확정 | ② 미구현 · ③ 종결 후보 |
| **`98·CU-13`** ≡ `96·P-2` | LOB 속성 `stringvalue_short` 절단 — COALESCE인가 `stringvalue` 단독인가 | 98 §3 | `src/semantic/coverage.py:146-148` · `assembler.py:862` · `b_resource.yaml:150` | B-09 | `98·G-5` ← `99·E-1` | 미구현 |
| **`98·CU-15`** ≡ `96·P-15` | 생략형 후속 턴 호스트명 승계. 앵커가 1·2단(`subagents.py:345·1259`)에 있고 107 R4와 같은 경로다 | 98 §3 · §5 G-11 | `subagents.py` · `query_gen_common.py:1688` | G-01 턴3·턴4 | `98·G-11`(D-153 후속1과 충돌) | 미구현 |
| **`98·CU-18`** | 결정적 알람 조립이 사용자 건수를 버린다. **전제를 다시 확인해야 한다.** 두 호출부는 이미 `resolve_query_limit` 산출값을 넘긴다(`query_generator.py:503-506 → :1147` · `multi_db_executor.py:345-348 → :505`, 2026-09-03 이후 무변경). 실제 소실은 상류 질의 재작성(107의 입력 오염 축)일 수 있다(추정) | 98 §3 CU-18 · 107 §6(앵커 경합) | `assembler.py:1498·1585` | D-02·D-04 | 전제 재확인 → `98·G-10` | 판정 불가 |
| **`98·CU-4`** ≡ `96·S-b` · **`98·CU-5`** ≡ `96·S-a` | `replanner` 결정적 조기 종료 · `result_aggregator` 서술 비용. **둘 다 2단 전용 노드다**(108 §1.2 사망 위치 131턴 · 1단 배선은 `field_mapper → deep_agent → END`) | 98 §3 · 96 §5 | `replanner.py:69-94` · `result_aggregator.py:795` | 노드 p50 · 발동률 | **`110·Q-1`** · `98·G-6` | 미구현 |
| **`108·CU-B1`** ≡ 98 CU-14(1단 이관분) | 2단 `routing_intent` 소실(`subagents.py:784`) — 알람 의도가 사라진다 | 108 §3.3 · §1.5 | `subagents.py:784` · `schema_analyzer.py:558` | D-04 | 108 G-2 확정 보류 — 2단을 계속 쓰기로 할 때만 착수 | 보류 |
| **`108·CU-B4`** | 시트 지목을 무시했다는 사실을 사용자 응답에 노출(CU-A1은 로그까지만 남긴다) | 108 §3.3 · 98 §8.3 F-2 | `input_parser` → state → `output_generator` | 응답 문구 | 재테스트 후 | 미구현 |
| **`108·CU-B8`** | EAV 캐스트 지시문을 G-7(98 §5 — 양 엔진 `NUMERIC`)에 정합. `prompts.py:166` · `polestar_b0.yaml:334` 수정, `test_polestar_prompt_render.py:185`(위반을 정답으로 단언) 계약 교체, 스냅샷 4키 자기 델타. ⚠ 코드는 되돌린 상태다(HEAD 바이트 동일). D-236은 2026-09-21 "⑤ 미착수"로 정정됐지만, 98 본문 175행은 아직 "통일했다"로 적혀 있다 | 108 §3.3 「CU-B8 발견」 · 98 §8.3 F-3 | 위 앵커 | 게이트 4종 · `prompt_render_diff` | **재테스트 이후**(108 G-1 (b)) | 미구현 |
| **`108·CU-B10`** | **CU-A2 산문 예산의 단일/멀티 비대칭** — `multi_db_executor.py:590-620`의 자체 재생성 루프(`for _retry in range(1, 3)`)에 산문 조기 종결이 없다(CU-A2 심볼 0건). 3단 멀티 DB 경로에서 산문이 2회 재생성되고 사유 노출도 없다. 처방: 같은 루프에 이미 있는 조기 종료 2건(D-155 PII 차단 · D-159 토큰 한도 — 둘 다 "같은 프롬프트 재생성은 무의미") 옆에 세 번째로 얹는다 | 108 §3.1 CU-A2 「남긴 비대칭」 · §3.3(2026-09-21 이 통합 작업 중 발견 → 36 세션 등재) | `multi_db_executor.py:590-620` | 멀티 경로 회귀 테스트 | 재테스트 후 — 3단 멀티 경로 산문 빈도를 먼저 측정 | 미구현 |
| **`98·CU-14 주석 고백`**(신규 발견 · 무소유) | CU-14 원 정의인 "SQL 주석 자기 고백의 결정적 감지"는 CU-A2(산문 대체)와 형태가 달라 구현되지 않았다. `prompts.py:133` Strict Constraint 5(조건을 생략하고 SQL 주석으로 알려라)가 3단에도 있어, **D-04·R3-03형 침묵 조건 드롭이 3단에서도 재발할 수 있다**(추정) | 98 §3 CU-14 | `prompts.py:133` · validator | 재테스트 D-04 결과 | `108·재테스트` | 미구현(판단 대기) |
| **`96·P-8`**(무소유) | 존 스코프 오라우팅(A-01 — 여의도). 98에 대응 CU가 없다. 20260918에서 재현 | 96 §4 가드 실패 | 라우팅(미특정) | A-01 `db_ids` · `sql_must_not_match` | 조사 → 102·106 라우팅과 경계 판단 | 미구현 |
| **`96·P-12`**(무소유) | 파일 산출 실패 `has_file`(R1-03 3/3) | 96 §4 | 미특정 | R1-03 | 조사 | 미구현 |
| **`96·S-d`**(무소유) | `input_parser` 고정 약 7초(20260918 p50 6,931ms · 361표본) — 3단 공통 전단 | 96 §5 S-5 | `src/nodes/input_parser.py` | 노드 p50 | 조사 · `96·G-2` | 미구현 |
| **`96·S-4`**(무소유) | 진행 이벤트 공백(p50 12.1s). "plans/89 소관"이라 적었지만 89에 참조가 0건이다 | 96 §5 S-4 | SSE 진행 이벤트 | `max_event_gap` | 조사 | 미구현 |

### 3.4 사람 판단 (열린 것만)

| 키 | 물음 | 입력·선행 | 현황 · 권고 |
|---|---|---|---|
| **`110·Q-1`**(신규) | 98 CU-4·CU-5(2단 전용 노드)에 108 G-2 규율(2단 전용 결함 보류)을 적용하는가. **2단·3단 arm 비교(§3.2) 결과가 판단 입력이다** | 두 노드는 2단 배선에만 있다. 107 G-9 기준(1단에서도 발현하면 전이하지 않음)에 해당하지 않는다. 다만 `result_aggregator`는 20260918 완주 턴 p50 12.6s, 163건 중 69건이 20초 초과, 타임아웃 95턴 사망 위치로 **지연 기여가 가장 큰 단일 노드**다(36 세션 제공 · 108 §1.2) | 권고: **3단 전환이 실제로 확정된 뒤에 보류한다.** 순서는 2단·3단 arm 비교(§3.2) → 운영 단 선택(`plans/102` L-5)이다. 그 전에 보류하면 "2단을 안 쓰니 안 고친다"와 "가장 큰 병목을 방치한다"가 같은 결정이 된다. 보류가 확정되면 `98·G-6`(서술 정책)도 함께 소멸한다 |
| **`99·H-1`** | 재테스트 전 커밋 시점·범위 | 작업 트리에 여러 세션의 미커밋 변경이 섞여 있다. 차수 F 랜딩 6건도 전부 미커밋이다 | 대기 |
| **`99·H-3`** ≡ `96·G-5` | 선택적 3회 반복의 대상·시점 | §3.2 `99·§3.2` | 대기 |
| **`96·G-2`** | A군 5s · B군 10s 목표 유지 | `a_routing.yaml:10` `latency_target_ms: 5000` | 대기 |
| **`98·G-5`** · **`98·G-8`** | LOB COALESCE 대 `stringvalue` 단독 · LIKE 와일드카드 ESCAPE | `99·E-1` 결과 | 대기 |
| **`98·G-10`** | 알람 조립에 사용자 건수 전달 | `98·CU-18` 전제 재확인 | 대기 |
| **`98·G-11`** | 생략형 후속 승계(좁힌 안) | D-153 후속1과 충돌 | 대기 |
| **`94·G-1`·`G-4`·`G-5`·`G-6`·`G-9`·`G-12`** | 정본 실행 환경 · 과금 승인 단위 · 서술 품질 판정 · 93과 순서 · 백로그 등재 · Windows 지원 등급 | 코드가 이미 각 권고안대로 동작한다. (a) 폐쇄망 fabrix(D-216) · 내부망 무승인/외부 건별 · (a) 수동 검토 · 93이 94를 소비(순서 무의미) · (a) 제안까지만 · (a) 1급 | **코드 현실대로 확정할 것을 권고.** 확정 전까지는 대기로 표기한다 |
| **`94·G-2`·`G-7`·`G-8`·`G-10`·`G-11`** | 커버리지 범위(알람·UI 통합) · docs/29 처분 · HTML 리포트 · R군 대응 등급 정책(`policy_confirmed` 전 군 false) · R군 1차 범위(증설 동결 해제) | 각 행 | 대기 — 실작업이 딸린 게이트 |

확정된 게이트는 여기 싣지 않는다. 원본 절에 기록돼 있다.
- 94: G-3(D-215) · G-13
- 96: G-1 · G-3 · G-4(D-220)
- 98: G-1 · G-3 · G-4 · G-7 · G-9
- 108: G-1~G-5 · **G-6**(2026-09-21 사용자 승인 "판정 계약 수용하라" → 구현 §3.1 · D-241 d9 등재)

### 3.5 다른 계획 소유 (참조만 — 이 문서에서 닫지 않는다)

| 키 | 내용 | 소유 | 이 문서와의 연결 |
|---|---|---|---|
| `108·CU-B9` ≡ `97·G-3` | `NON_SQL_RETRY_BUDGET`(`src/graph.py:63`)을 `AppConfig`로 승격할지. 설정 밖 상수는 스위프 축이 되지 않는다 | **109** | 판단 입력은 **이 문서 재테스트의 retry=1 회복률**이다. 근거 값은 20260918 산문 체인 39건 중 retry=1 회복 7건, retry=2·3 합 3건이다. `src/graph.py`는 CU-A2 배선과 같은 파일이다 |
| `108·CU-B5` · `94·estimate` | 노드별 `llm_calls`·`tokens` 배선 · `ASSUMED_LLM_CALLS_PER_TURN=6` 가정의 실측 교체 | plans/56 | O-b 고정 문구로 닫혀 있다(D-221) |
| `108·CU-B7` | `classify_dbs` LLM 단일 호출 가용성(FabriX 오류 3건) | plans/102·106 | 관측만 |
| 107 W0.5·W2·W3 | 슬롯 승격 · `rewrite_trace` 적재 · 소비자 전환 | plans/107 | §3.1 `94·Y-11`·`Y-12`·`O-e`·`V28~V31`의 선행 |
| 벤치 스위프 재측정 · `triage.py` · 워크로드 도달 | — | 109 | 판정 계약(`108·G-6`)과 off arm(`108·CU-B6`)은 이 문서가 소유한다 |

---

## 4. 착수 순서

```
A. 지금 · 사람 판단 ─ ~~108·G-6(판정 계약)~~ **승인·랜딩(2026-09-21)** · 99·H-1(커밋 범위) · 110·Q-1(2단 전용 보류)
      │
B. 재테스트 전 · 하네스 코드(무과금 · 제품 동작 불변) ─ ~~110·N-1 arm 적용 수단~~ **랜딩(2026-09-21)** · 94·§4.4 PII 마스킹 · 108·CU-B3 teardown
      │   · 94·O-a column_mapping · (G-6 승인 시) 판정 계약 구현
      │   · ~~109·CS-16·CS-17·CS-19③·CS-43 재개·분석 결함~~ **랜딩(2026-09-22)**
      │
C. 폐쇄망 실행 ─ --preflight(E-0·E-1) → 표적 재현(3.5단 · J-1 · J-2 · arm 2종)
      │   → 전체 재테스트(`--arm tier2_intent --arm tier3_router`) → 단별 기준선 선언(94·V29 기준선)
      │   → 단 선택 판단(plans/103 잔여와 대조) → 98 §7·§8.5 효과 측정
      │
D. 재테스트 후 · 제품 코드 ─ CU-B8 · CU-B4 · CU-B10 · CU-6(J-1) · CU-9 · CU-13(G-5)
      │   · CU-15/CU-18(게이트) · CU-14 주석 고백(D-04 결과) · 무소유 4건 조사 · CU-B9(109)
      │
E. 장기 ─ off B arm(108·CU-B6) · 카탈로그 확장(M/N/O/P · R군 · manual 83건 — 동결 해제 판단)
          · 107 연동(Y-11 · Y-12 · O-e · V28~V31) · HTML · e2e 통합 · docs/29 생성 · D-212 등재
```

- **B는 제품 동작을 바꾸지 않는다.** 재측정 직전에 넣어도 효과 귀속을 흐리지 않는다(§2.3).
- 특히 PII 마스킹은 폐쇄망 산출물을 반출하기 전에 필요한 **안전 항목**이다.
- **C의 첫 run이 단별 기준선이 된다.** 이전 run 3건과는 직접 비교하지 않는다(§0.3). 운영 단 선택(`plans/102` L-5)은 이 비교가 나온 뒤에 사용자가 정한다.

---

## 5. 원본 종료 처리 (2026-09-21 실행)

| 원본 | 종료 전 → 종료 후 파일명 | 헤더에 추가한 1줄 |
|---|---|---|
| 94 | `94-WIP-feature-perf-scenario-suite.md` → `94-feature-perf-scenario-suite.md` | 잔여·현행 실행 가이드·Windows 부록은 `plans/110`으로 이관 |
| 96 | `96-TODO-scenario-run-20260915-remediation.md` → `96-scenario-run-20260915-remediation.md` | 잔여는 `plans/110`으로 이관 |
| 98 | `98-TODO-run-20260915-code-fixes.md` → `98-run-20260915-code-fixes.md` | 잔여는 `plans/110` §3.3으로 이관 |
| 99 | `99-TODO-rerun-experiment-design.md` → `99-rerun-experiment-design.md` | 실험 잔여는 `plans/110` §3.2로 이관 |
| 108 | `108-WIP-run-20260918-remediation.md` → `108-run-20260918-remediation.md` | 잔여·재테스트 절차는 `plans/110`으로 이관 |

**참조 갱신**(파일명 전체를 쓰는 곳만. 번호 참조 `plans/94` 등은 그대로 유효하다)
- `plans/INDEX.md` 5행.
- `docs/03_setup_guide.md:300`(실행 가이드 → 이 문서).
- `tasks/plan-94.md:3` · `tasks/todo-94.md:3`.
- `plans/108` §7 변경 파일 표(98 파일명).
- `plans/107`의 94 참조 12곳: E2E 회귀·`rewrite_trace` 수집·수용 기준의 소유처를 이 문서 §3.1로 바꾼다. 107 소유 세션(d7)과 합의했다.

**원본에 남겨 둔 정합성 문제.** 원본은 시점 기록이라 고치지 않았다. 이 문서가 정정된 값을 가진다.
- **94**
  - 헤더의 "잔여 Y-8 1건"·"S5·S6 승인 대기"·"G-1~G-12 대기"는 낡았다. G-3은 확정이고, 실 run이 3회 있었다.
  - §18의 W3 플래키 "고치지 않았다"는 틀렸다. `284137a`에서 수정됐다.
  - "단언 키 19종"은 현재 22종이다.
  - O-a `column_mapping`은 "랜딩"으로 적혀 있지만 수집 코드가 없다.
  - 가이드의 내부망 판정은 fabrix만 확인한다. D-222는 두 평면을 모두 본다.
  - `--repeat`를 "R군·성능 군 기본 3"이라 적었지만 코드는 R군만 3이다.
  - §8 표의 `scripts/bench/runner.py`는 존재하지 않는다.
- **96**
  - 헤더의 "코드 0건"은 틀렸다. W1은 전건 랜딩됐다.
  - §10은 "D-218 미등재"라 적었지만 D-218은 본문 등재됐다.
  - 206행에서 P-13 행과 P-14 행이 한 줄로 붙어 표가 깨졌다.
  - "D-211 ⑪"은 없는 항목이다. 올바른 근거는 ⑨이고 D-222가 개정했다(99 3곳·D-216 관련란도 같다).
- **98**
  - 파일명은 `-TODO`인데 실제로는 부분 랜딩이다.
  - 헤더는 "차수 F 5건"이라 했지만 6건이다.
  - §0 표와 §3 본문이 어긋난다(CU-7·8·10·11·18).
  - 175행의 CU-B8 "통일했다" 서술이 코드와 모순된다.
  - §4 "잔여 4건"은 3건이다. J-5 행에서 표가 끊긴다.
  - 게이트 번호가 108과 충돌한다.
- **99**
  - 헤더의 "실행 전"은 틀렸다. E-2 로컬 실행·E-1 시도·run 20260918이 있다.
  - §3.3(세그먼트 = 예상 소요)은 D-221 ③이 대체했다.
  - §7은 "2건"인데 본문 여러 곳에 H-2가 남아 있다.
  - §6 G-9는 미갱신이다. 98에서 종결됐다.
  - "218건"·"D군 6건"은 각각 220건·8건이다.
  - `ORCHESTRATOR_PROVIDER` 점검이 누락됐다.
- **108**. 이 통합 작업 중 지적한 6건 가운데 §5.3 grep 2건 · §5.0 `meta.token_refresh` · §5.2e `clear_schema_cache` · CU-B10 등재는 소유 세션(36)이 원본에서 정정했다(2026-09-21). 남은 것은 아래와 같다.
  - 헤더 "G-1~G-6 전건 확정"과 G-6 행 "대기"가 충돌한다.
  - §1.6 허위 불합격 수치가 raw와 다르다. raw 재계산은 6턴인데 본문은 8턴, §5.2e는 4턴이다.
  - §1 제목 "13,539행"은 `wc -l` 실측 20,121행과 다르다.
  - §5.2 토큰 수명 행은 옛 판독 방식이다. 현재는 러너가 주입한다.
  - §5.2d "한국어 타임아웃 사각지대는 93 소관"은 D-238·97 §11.4에서 종결됐다.
  - §7 "금지 구역 `scripts/scenario/*` 0건"은 CU-H2와 충돌한다.
- **이 작업 범위 밖이라 고치지 않은 것.** 사용자 확인이 필요하다.
  - `docs/02_decision.md` **D-236**. ⑤가 되돌린 CU-B8을 "구현 완료"로 기록하고 제목에 "CU-A4"가 남아 있던 문제는 이 작업 중 지적했고, 소유 세션(36)이 2026-09-21 정정했다(⑤ 미착수 명시 · 제목을 실제 랜딩분으로). 이 문서는 D-236을 편집하지 않았다.
  - `CLAUDE.md` 「멀티 엔진 방언」 절은 "B-07·B-08 `\bnumeric\b` 단언이 이 형태를 요구한다"고 적는다. CU-H1 이후로는 사실이 아니다.
  - **D-053**("사본 금지"로 인용됨 — 본문은 hostname SQL 엔진 인지)과 **D-086**("DB2 지표 스케일"로 인용됨 — 본문은 선행 task 스코프 주입)의 오인용 의심. 인용처는 94 §19·98 §8·107·108·D-236이다.

---

## 6. 결정

- **새 D-번호는 쓰지 않는다.** INDEX 「이관 조항」(D-208)의 두 번째 적용이라, `docs/02_decision.md` D-208에 부기로 기록했다(짝 계획 109와 함께).
- 이 문서가 새로 올리는 물음은 `110·Q-1` 하나다(§3.4). 확정되면 그때 채번 규칙에 따라 등재한다.

## 7. 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-09-22 | **`109·CS-16`·`CS-17`·`CS-19③`·`CS-43` 이관·랜딩**(사용자 지시 "이관하고 멀티턴은 다시 돌려라" · "발견된 오류는 수정하라" · 109 세션 — 109가 구간 재개를 코드와 대조하다 발견해 임시 등재했던 94 러너·분석기 결함 4건을 장부 규칙 1·3에 따라 원 번호로 옮겼다). ①일부만 끝난 멀티턴은 재개 때 1턴부터 새 thread로 다시 돈다(`meta.rerun_partial`). 앞 턴 fail/error로 끊긴 시나리오는 끝난 것으로 본다. 리포트·분석기는 같은 키의 마지막 행만 센다. ②이전 서버 로그를 `.log.prev-<시각>`으로 보존한다. ③`run.json`을 시작 시점에도 쓰고(`in_progress`) 시도별 출처 `meta.attempts`를 누적한다. 판이 다르면 `provenance_mixed`(콘솔·`report.md` §1·벤치 건전성 주의)를 남긴다. ④회귀 기준선은 모드가 다른 run과 끝나지 않은 run을 사유와 함께 건너뛴다. **새 run의 `raw.jsonl`·실행 순서는 비트 동일**하다. 신규 테스트: 시나리오 23건, 벤치 1건(CS-19③ 주의). D-218 ③ 부기. 출처 섞임을 멈춤 기준으로 올릴지는 사용자 판단으로 남겼다. d7·36 세션에 착수를 통지했다. |
| 2026-09-21 | **`108·G-6` 판정 계약 승인·랜딩**(사용자 "판정 계약 수용하라" · D-241 d9 등재). 단언 미평가 턴을 기능 합격률 분모에서 빼고 사유 3종을 **합치지 않고** 각각 집계한다. 행에 `unevaluated_reason` 칸 1개(`func_verdict` 어휘 불변), 규칙 정본은 `report.unevaluated_reason` 한 곳. `report.md` 2절에 제거 사다리(`제거 합 + 분모 == 전체` 단언)와 **벤치와 통일한 표기** `역질문 차단 N건 (자동응답 M건 · 발동률 P%)`를 싣고, 제거 사유 `timeout`과 6절 실패 유형 `timeout`은 절 제목으로 축을 갈랐다. §3.4에서 확정 게이트로 옮기고 가이드 ⑥ 판독 순서를 계약 적용본으로 고쳤다. |
| 2026-09-21 | **`94·Y-11`·`94·Y-12`·`94·O-e` 랜딩 · `94·V28~V31`·`94·V29 기준선` 부분 랜딩**(d7 · plans/107 v2.5 구현 연동). 재작성 감사 단언 `expect.rewrite.{gate,slots_preserved}`(레코드 없으면 보류 — 카탈로그 선언 0건, 섀도 run 뒤 선언) · `rewrite_trace` 수집(done 1순위 · 서버 로그 폴백) · `raw.jsonl` 칸 · `bottleneck.md` 「재작성 게이트·검증」 절과 `REWRITE_TRACE_UNMEASURABLE` · 실패 분류 `rewrite` · 측정 arm `tier2_intent_frame`·`tier3_intent_frame` · 회귀 비교 키 `(env, tier, base_profile, arm)`와 `summary.meta.regression_baseline` 기록. 수동 기준선 지정·실 run 대조는 열려 있다 |
| 2026-09-21 | **`110·N-1` 랜딩** — 시나리오 CLI에 `--arm <프로파일>`(반복 가능)을 넣어 2단·3단을 같은 run의 arm으로 나란히 잰다. 덧씌움은 **치환이 아니라 병합**이라 D군 8건이 알람 플래그를 유지한다. 조합 이름 `<프로파일>+<arm>`(ASCII — `profile` 값이 곧 파일명이다)과 함께 **`arm`(arm id 원본)·`base_profile` 두 칸을 `raw.jsonl` 행·`run.json` `profiles[]` 양쪽에 싣는다**(피어 확정 계약 — 소비자가 조합 이름을 파싱하지 않는다). `summary.json` `by_profile[]`·`report.md` 1절 「프로파일(arm)별 판정」이 arm별로 분리 집계한다. 가이드 ②③④⑥·§3.1·§3.2·§4를 실제 사용법으로 교체했다. 스위프 치환 결함 교정은 `110·N-2`로 분리해 보류했다(영역 조율). |
| 2026-09-21 | 재테스트 설계를 **2단·3단 arm 병행**으로 바꿨다(사용자 지시 · 36 세션 전달 · 108 §5.1 "3단 고정" 대체 · 가이드 ③④⑥ · §0.1 · §2.3 · §3.2 · §4). 프로파일 `tier2_intent`·`tier3_router`는 36 세션이 신설했다. 현 CLI로는 arm을 적용할 수 없어(`--profile`은 시나리오 필터) `110·N-1`을 신설했다. |
| 2026-09-21 | 신설. 94·96·98·99·108 잔여를 이관하고 원본 5건을 종료했다(§5). 판독 명령 5종은 run `20260918-182507`에서 수치 재현을 확인했다. 병행 세션 d7(94·107)·36(98·108)·d9(93·97 · 판정 계약)과 소유를 합의했다. |

---

## 부록 A. Windows 실행 가이드 *(원 94 부록 A · 2026-09-11 사용자 지시 — 2026-09-21 이관)*

> **사용자 지시 원문**: *"실행환경은 위도우 환경도 있다. 윈도우 환경에서도 실행할 수 있는 가이드를 추가하라."*
> 폐쇄망 운영·테스트 단말은 Windows다(`scripts/health_probe.ps1` · `docs/29:21`·`:276` · `docs/18:82`). 러너·리포트·분석기는 **Windows를 1급 실행 환경으로 지원한다**(W1~W10 — 2026-09-21 코드 실측으로 존재 확인).
> 109(벤치)의 Windows 절차도 이 부록을 정본으로 참조한다.

### A.1 Windows에서 달라지는 10지점

| # | 항목 | Windows에서 | 대응 |
|---|---|---|---|
| 1 | 프로세스 종료 | `SIGTERM`·`SIGKILL`이 없다 | `CREATE_NEW_PROCESS_GROUP`으로 기동한 뒤 `CTRL_BREAK_EVENT` → 유예 후 `taskkill /PID <pid> /T /F`(`server.py:199·271·287`) |
| 2 | 서버 기동 구조 | `src/main.py`의 `reload=True` 하드코딩 → 부모만 죽이면 자식이 고아로 남아 포트를 붙든다 | 러너는 reload 없는 기동을 쓴다(`scripts/scenario/_serve.py`) |
| 3 | 콘솔 인코딩 | 기본 cp949 → 한글·`—`·`·`에서 `UnicodeEncodeError`로 런이 죽는다 | `PYTHONUTF8=1` 필수. 러너 콘솔 출력은 ASCII 구두점만 쓴다 |
| 4 | 파일 개행 | 텍스트 모드가 `\r\n`으로 바꿔 재개(resume) 대조가 어긋난다 | 모든 쓰기에 `encoding="utf-8", newline="\n"` |
| 5 | `localhost` 해석 | `::1`이 먼저 시도된다 | 기준 URL을 `http://127.0.0.1:<port>`로 고정 |
| 6 | 포트 확보 | Hyper-V·WSL2·Docker Desktop 예약 제외 대역이 있다 | `netsh interface ipv4 show excludedportrange protocol=tcp`로 확인한 뒤 그 밖에서 고른다. 바인딩 실패는 재시도로 처리한다 |
| 7 | 파일 권한 | `0o600`이 사실상 무시된다 → 트레이스가 디렉터리 ACL을 상속한다 | 산출 디렉터리 ACL을 한 번 설정(A.3-⑤). provenance에 기록한다 |
| 8 | venv 경로 | `.venv\Scripts\python.exe` | A.4 대조표 |
| 9 | 보조 스크립트 | `db/setup.sh`는 bash 전용 | `docker compose -f db\docker-compose.yml up -d`로 직접 |
| 10 | 외부 명령 출력 인코딩 | `PYTHONUTF8=1`과 cp949 도구 출력(`powercfg`·`netsh`·`git`)이 충돌해 reader 스레드에서 `UnicodeDecodeError` → 런 사망(2026-09-11 폐쇄망 실측) | 바이트로 받아 폴백 디코딩(`scripts/scenario/__init__.py` `run_capture`). `.stdout.strip()` 직접 체이닝 금지 |

### A.2 측정 교란 요인

| 교란 | 대응 |
|---|---|
| 절전·모던 대기(지연 분포가 두 봉우리) | `powercfg /change standby-timeout-ac 0` · `monitor-timeout-ac 0`. 런 후 원복하고 `powercfg /requests`로 확인 |
| 절전 요금제(p95만 나빠짐) | 고성능 요금제로 고정하고 provenance에 기록 |
| 실시간 바이러스 검사 | 산출 디렉터리·`logs/`를 검사에서 제외하거나, **제외하지 않았다는 사실을 리포트에 남긴다** |
| 빠른 시작 후 잔여 상태 | 측정 런은 완전 재부팅이나 서비스 재시작 후에 시작 |
| 경로 길이 260자 | `run_id`를 짧게. 필요하면 긴 경로 지원을 켠다 |

`wall_ms`와 `processing_time_ms`가 크게 벌어지면 클라이언트 쪽(절전·검사·큐잉)에서 시간이 샜다는 신호다. 둘이 나란히 커지면 서버 쪽이다.

### A.3 사전 준비 (PowerShell)

```powershell
# ① 가상환경
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1          # 실행 정책이 막히면: powershell -ExecutionPolicy Bypass
python -m pip install -e ".[dev,document]"

# ② 인코딩 — 없으면 한글 출력에서 런이 죽는다
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001

# ③ 포트 제외 대역
netsh interface ipv4 show excludedportrange protocol=tcp

# ④ 절전 억제 — 런 종료 후 원복
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0

# ⑤ 산출 디렉터리 ACL (0600 미적용 보완)
New-Item -ItemType Directory -Force results\scenario | Out-Null
icacls results\scenario /inheritance:r /grant:r "$env:USERNAME:(OI)(CI)F" | Out-Null

# ⑥ 로컬 샌드박스(선택)
docker compose -f db\docker-compose.yml up -d
docker compose -f redis\docker-compose.yml up -d

# ⑦ 인증 설정 확인 — 비밀값은 출력하지 않고 키 이름만 본다
Select-String -Path .env, .encenv -Pattern '^AUTH_ENABLED=' -ErrorAction SilentlyContinue
Select-String -Path .env, .encenv -Pattern '^(ADMIN_USERNAME|ADMIN_PASSWORD|ADMIN_JWT_SECRET|AUTH_JWT_SECRET|AUTH_AUTH_DB_URL)=.+' -ErrorAction SilentlyContinue |
    ForEach-Object { "{0}  <- {1}" -f ($_.Line -split '=')[0], $_.Filename }
Get-ChildItem Env: | Where-Object Name -Match '^(AUTH|ADMIN|BENCH_USER)_' | Select-Object Name
```

DB2(`polestar_b0`) 대상이면 `ibm-db`를 확인한다. 루트 venv에는 없고 `mcp_server/pyproject.toml`에만 선언돼 있다.

```powershell
python -c "import ibm_db" ; if ($LASTEXITCODE -ne 0) { python -m pip install "ibm-db>=3.2.0" }
```

### A.4 명령 대조표

| 용도 | POSIX | Windows (PowerShell) |
|---|---|---|
| 가상환경 | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| MCP 서버 | `cd mcp_server && python -m mcp_server` | `cd mcp_server; ..\.venv\Scripts\python.exe -m mcp_server` |
| MCP 테스트 | `cd mcp_server && ../.venv/bin/python -m pytest` | `cd mcp_server; ..\.venv\Scripts\python.exe -m pytest` |
| 전체 회귀 | `pytest` | `$env:PYTHONUTF8="1"; pytest` |
| 품질 게이트 | `python scripts/arch_check.py --ci` | `python scripts\arch_check.py --ci` |
| 시나리오 기본 | `python -m scripts.scenario` | 동일 |
| 외부 프로바이더 실 실행 | `RUN_E2E=1 python -m scripts.scenario --run --profile baseline` | `$env:RUN_E2E="1"; python -m scripts.scenario --run --profile baseline` |

PowerShell에는 POSIX의 `KEY=value command` 형태가 없다. `$env:KEY="value"`로 먼저 설정해야 하고, 그 값은 **세션에 남는다.** 러너의 프로파일 주입은 자식 프로세스 환경 사본에만 쓰므로 영향이 없다. 하지만 사람이 손으로 돌릴 때는 이전 세션 값이 남아 결과를 오염시킬 수 있다.

### A.5 러너 요구사항 W1~W10 (구현 계약 — 랜딩)

| # | 요구사항 |
|---|---|
| W1 | 자식 프로세스는 reload 없이 기동하고 프로세스 그룹으로 묶는다. 종료는 OS별로 분기한다 |
| W2 | 기준 URL은 `127.0.0.1` 고정 |
| W3 | Windows 제외 대역을 회피하고, 바인딩 실패는 INVALID가 아니라 재시도로 처리한다 |
| W4 | 모든 쓰기에 `encoding="utf-8"`과 `newline="\n"` |
| W5 | 콘솔 출력은 ASCII 구두점만 쓴다. 리포트 파일에는 제한이 없다 |
| W6 | provenance에 OS·버전·코드페이지·전원 요금제·AV 제외 여부를 기록한다(`server.py:128-160`) |
| W7 | `--mock` 전 경로가 Windows에서도 통과한다(V19) |
| W8 | 경로는 전부 `pathlib`으로 조립한다 |
| W9 | MCP/SSE 배치는 호출별 `asyncio.run()` 금지 — 단일 공유 루프 |
| W10 | 외부 명령 출력은 `text=True` 금지. 바이트로 받아 폴백 디코딩하고, provenance 수집은 예외를 던지지 않는다 |

### A.6 실행 전 체크리스트

```
[ ] $env:PYTHONUTF8="1"
[ ] 이전 세션에 남은 $env:* 플래그 · Env:AUTH_* · Env:ADMIN_* 없음
[ ] AUTH_ENABLED=true 면 내장 테스트 계정(또는 --user)으로 웹 /login 1회 성공
[ ] --env 생략 - run.json env 가 closed 로 판정됐는지
[ ] 127.0.0.1 로 헬스 응답 · netsh 제외 대역 밖 포트
[ ] powercfg 절전 0 · 런 후 원복 메모
[ ] DB2 대상이면 python -c "import ibm_db" 통과
[ ] results\scenario ACL 설정
[ ] 기동 로그 "오케스트레이션 사다리 확정: tier=semantic_router" 1줄 (가이드 ③)
[ ] 런 종료 후 고아 프로세스: Get-Process python | Format-Table Id,StartTime
```

### A.7 남은 불확실성

§3.1 `94·V19·V20·A.7` 행에서 추적한다.
- Playwright e2e의 Windows 동작.
- `sre_agent`(Python ≥3.13) 병존 구성.
- DRM 해제 경로(`plans/74` — Windows 전용 모듈, 폐쇄망 단말에서만 유효).
