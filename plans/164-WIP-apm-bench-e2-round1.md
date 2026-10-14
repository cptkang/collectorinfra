# 164. 제니퍼 APM 벤치 E2 1회차 후속 — 누출 관문 실패로 반출 0 · 판정 집계 왜곡(gw_error 대표화) 교정 · 실패해도 진단 가능한 반출 · 폐쇄망 재실행

> **작성일**: 2026-10-14 · **v1.0**(최초 · 코드 0) · **v1.1**(2026-10-14 — 독립 검토(verifier) High 4·Medium 7·Low 7 · 보안 검토(security-auditor) High 3·Medium 5·Low 3 반영) · **v1.2**(2026-10-14 — W0a 감사 로그 진단표 추가·구현)
> **상태**: **WIP** — **W0~W5 완료**(2026-10-14 · 커밋 없음 · 게이트 권고안 일괄 채택 · D-323 부기 ⓐ~ⓖ) · E1 전체 관문 통과 129/129 · 관문 실패 강제 e2e 통과 · 계획 끝 모듈 회귀 rc 0 · **잔여 W6 폐쇄망 재실행(사용자 — §5)** · 열린 질문 G-8 일부(§11)
> **요청 원문(2026-10-14)**: 「`plans/152`의 폐쇄망 E2 실행 결과를 분석해 후속 계획서를 수립하고 검토까지 마친다 — 계획 수립·검토만」
> **근거 run**: 폐쇄망 `20261013_1827`(E2 · 운영 뷰 서버 5.6.5.11 · FabriX · 2단 `intent_orchestration`). 외부망이 받은 것은 **실행 요약 로그 1개와 `leak_check.json` 1개뿐**이다(`/Users/cptkang/AIOps/collectorinfra/results/apm_bench/` — 저장소 밖). `trace.jsonl`·`report.md`·`run.json`·`coverage.md`·`probe.jsonl`은 누출 관문 실패로 **디스크에 쓰이지 않았다**(폐쇄망 `/root/apm_bench_result/20261013_1827/`에도 `leak_check.json`만 있다 — `write_gated` 동작). 벤치 세션 임시 디렉터리(`server.log`·`gateway.log`·`capture.jsonl`)는 run 끝에 지워진다(`scripts/apm_bench/driver.py:1019-1022`).
> **성격**: 반출 실패 원인 분석 + 측정 도구 교정 + 재실행 절차. **few-shot 교정 회차 계획이 아니다** — 턴별 근거가 없어 152 §3.9 루프를 돌 수 없다. 이 계획을 마친 뒤 재실행 반출본으로 `plans/NN-TODO-apm-bench-round1.md`(152 A.8)를 연다.
> **상위·인접 계획**: `plans/152`(벤치 · D-323 — 이 계획이 고치는 도구의 소유) · `plans/157`(E1 잔여 3턴 — 존 사전 게이트 · APM 대상 미승계 · 드라이버 파싱 답 — `driver.py`·`judge.py`가 겹친다 · 착수 순서 §6) · `plans/156`(시간 해석 후속) · `plans/161`(소스 id b0·cm_prd·cm_stg · D-329) · `plans/135`(ITAM 벤치 위생·관문 — `scripts/itam_bench/redact.py` 재사용원)
> **관련 결정**: D-323(벤치 — ① 5축 · ④ 근거 묶음 · ⑤ 위생 · ⑦ 본체 emit) · D-321(형식 보존 치환) · D-301 ④(사용자 정보) · D-219 ②③(서버 측 축소 · 원본 서버 보관 · 관측 0 = 사고) · D-003(읽기 전용) · D-274(게이트웨이 경계 — 벤치는 게이트웨이를 import하지 않는다) · D-329(소스 id) · D-303(회귀) · D-127·D-240(실 LLM = 로컬 MLX)
> **실측 기준**: 2026-10-14 `apm_noise` HEAD `4073f7f`. `scripts/apm_bench/`·`testdata/apm_bench/`·`apm_gateway/apm_gateway/`·`src/orchestration/`는 형제 체크아웃 `collectorinfra`(main `7ba5c96`)와 `diff -rq` 차이 0. `file:line`은 이 시점 값이다 — W0에서 다시 대조한다.
> **계획 시작 SHA**: `4073f7f`(2026-10-14 착수 확정 · 152·156·157 통합 진행과 같은 base)
> **회귀 대상 파일**(확정 — W0a~W5): `scripts/apm_bench/{__init__,__main__,audit_digest,driver,gateway,guard,hygiene,judge,kit,record,rundir,safe_summary,scenarios}.py` · 테스트 `tests/test_scripts/test_apm_bench_{audit_digest,audit_digest_verify,cli,driver,driver_parse,driver_parse_verify,hygiene,judge_w3,kit,leak_e2,verify,w164_verify,w2a,w2a_verify,w2b,w2b_verify}.py` · **본체(`src/`)·게이트웨이(`apm_gateway/`)·`scripts/itam_bench/redact.py` 변경 0**(G-5 (a))
> **번호 주의**: 이 저장소 `plans/` 최댓값은 161이지만 형제 체크아웃 `collectorinfra`에 미커밋 `162-TODO-codebase-smell-refactoring.md`·`163-TODO-security-hardening-separated.md`가 있어 164로 잡았다(병합 시 충돌 회피). 결정도 형제 체크아웃에 미커밋 D-330~D-332가 있다(이 체크아웃은 D-330 untracked) — 등재 직전 양쪽 `ls docs/decisions/ | tail -3`.

---

## 0. 요약

**한 줄** — 1회차 E2는 측정 자체는 끝까지 돌았지만(중단 0 · 타임아웃 0 · 429 0) **누출 관문 6건 때문에 근거가 한 줄도 나오지 않았다.** 로그 요약 수치는 판정 집계 방식 때문에 원인 분포를 잘못 보이게 한다(대표 분류 `gw_error 59`는 턴 실패 원인이 아니다). 그래서 이 계획은 **① 관문 위반 원인을 고치고 ② 관문이 실패해도 숫자·고정 어휘만 담은 안전 요약이 남게 하고 ③ 집계를 실패 원인이 보이게 고친 뒤 ④ 폐쇄망을 다시 돈다.**

| 묻는 것 | 답(확신도) | 근거 종류 |
|---|---|---|
| 28.0%의 주범은? | **target 축 36.4%**(상) — 다음이 args 74.4 · views 82.6 · source 84.0 | 로그 실측 + 판정 코드 |
| gw_error 59가 실패 원인인가? | **아니다**(상) — `gw_error`는 축이 아니라 턴 통과를 바꾸지 않는데, 대표 분류 우선순위 맨 앞이라 target 실패를 가리고, 통과 턴에도 붙어 집계된다 | 판정 코드 |
| gw_error의 실체는? | 보기 호출이 대상 없는 소스로 가서 받은 `instance_unresolved`(target 실패와 같은 뿌리 — 소스 오좁힘 T-1)가 1순위(중) · 업무명 간선 해석 단계의 정상 `instance_unresolved`가 섞임(중) · 인자 거부 `invalid_argument`·실 서버 응답 차이·권한 `apm_api_error`(중) | 코드 추론 — 확인은 재실행 |
| 누출 관문 `pii_value` 3건 | 근거 묶음이 재선택 결과 보기 id를 `output` 키로 옮겨 실어 **보호 키 목록(`SKIP_KEYS`)을 벗어났고**, 보기 id 낱말이 운영 인벤토리 이름 **토큰**과 겹쳤다 — **거짓 양성**(상) | 코드 실측(§2.6) |
| 누출 관문 `user_info` 3건 | 벤치를 **root로 돌려** OS 사용자 `root`·홈 `/root`가 사용자 정보 대조 값이 됐고 프로세스·인스턴스 목록 응답의 부분 문자열에 걸렸을 가능성(중) · 실행 호스트 이름(중하) · 로그인 id(하). 치환기는 이 값들을 모른다(비대칭 · 상) | 로그 경로 실측 + 코드 |
| `apm_gateway/var` 경고 | 스풀 주입 누락이 아니라 **이전부터 있던 `var/`**일 가능성이 크다(상) — 경고가 「이 run이 썼나」가 아니라 「있나」를 본다 | 코드 실측 |
| 157과 겹치나? | 일부만(§3) — 새 계획으로 두되 157 G-1 판단 재료를 이 계획 재실행에서 만든다 | |

**권고 ⓪**(v1.2) — 재실행 전에 **W0a 감사 로그 진단표**(§4.0)를 폐쇄망에서 만들어 반출한다(수 분 · LLM·제니퍼 호출 0 · 사람 확인 뒤 파일 1개). 그 결과로 §2 가설을 시나리오 단위로 가르고 W1~W4 우선순위·스모크 목록을 다시 정한다.
**권고 ①** — 다음 실행 전에 W1~W4를 끝낸다. 지금 상태로 다시 돌리면 같은 관문 위반으로 2.25시간이 또 사라질 가능성이 높다.
**권고 ②** — 재실행은 **스모크(위반 6턴 + 대상 대표 소수 · `--only`) → 전체 1회** 두 단계로 한다. 스모크가 관문을 통과하지 못하면 전체를 돌리지 않는다.
**권고 ③** — 28.0%를 152 E1(87.6~97.7%)과 같은 축으로 비교하지 않는다. E1은 가짜 LLM이 정답 계획을 넣고 활성 DB가 0인 환경이라 **결정적 경로 결함만** 잰다(152 §10.4). E2 28.0%는 실 LLM·실 인벤토리의 첫 기준선 후보이지만, 판정 집계 결함(§2.1)을 고친 재실행 값으로 다시 잡는다.

---

## 1. 실측 — 받은 것과 못 받은 것

### 1.1 실행 요약 로그 (`run_20261013_1827.log` 전문에서 옮김)

| 항목 | 값 |
|---|---|
| 환경 | E2 · 소스 `b0`·`cm_prd`·`cm_stg` · 게이트웨이 초당 2회 · 인벤토리 이름 887개(도구 2회 · 오류 0) |
| 본체 | 프로파일 `tier2_intent` · 사다리 `intent_orchestration`(사유 `intent_flag_on`) · 로그인 생략(인증 꺼짐 — docs/18 2026-10-13 방지책대로 `NO_AUTH_ENV`) |
| 시나리오 | 113 + 무거운 등급 5(`APM-HEALTH-untargeted`·`APM-RANK-tps-asc`·`APM-RANK-failrate-full`·`APM-FLEETEV-fatal`·`APM-FLEETEV-oom-full`) — **무거운 등급이 실행됐다**(`--skip-heavy` 미사용 · 건너뜀에 `heavy_off` 없음 · 152 A.6 ③ 「1회차는 `--skip-heavy`」와 다름) |
| 턴 | 실행 131 · APM 관측 105 · 건너뜀 `{'e2_skip': 4}` |
| 호출 | `b0` 367 · `cm_prd` 650 · `cm_stg` 66(합 1,083 — 소스당 예산 3,000의 22% 이하) · 타임아웃 0 · 429 0 · 기준선 1017.0ms · **P층 0회** |
| 중단 | 없음 · **소요 8102.7초**(2시간 15분) · 뷰 서버 5.6.5.11 |
| 턴 정확도 | **28.0%(35/125)** · dev 24.7%(22/89) · holdout 36.1%(13/36) |
| 축 | source 84.0 · views 82.6 · **target 36.4** · window 90.9 · args 74.4 |
| 대표 분류 | `gw_error` 59 · `source_miss` 22 · `empty_result` 10 · `arg_missing` 4 · `target_unresolved` 3 · `view_wrong` 3 · `sanitize_drop` 1 · `view_retry` 1 · `fewshot_copy` 1 · `window_wrong` 1(합 105) |
| 근거 누락 | 0 |
| 경고 | `apm_gateway/var 가 남았다 — 스풀 경로 주입을 확인한다` |
| 결과 | `[실패] 누출 관문 — 위반 6건 {'user_info': 3, 'pii_value': 3} · 산출물 미기록` |

산술(실측에서 바로 나오는 것): 일반 턴 125 = 실행 131 − 되묻기·거절 정답 턴 6(`APM-PROC-missing`·`APM-SRC-ambiguous#0`·`APM-SRC-none-all#0`·`APM-X-topology`·`APM-X-control`·`APM-X-rule-write`) → **판정 불가(unjudged) 0**. source 84.0% = 105/125와 맞는다.

### 1.2 `leak_check.json`

`passed: false` · 검사 파일 5종(coverage.md · probe.jsonl · report.md · run.json · trace.jsonl) · **위반 6건 모두 `trace.jsonl`**:

| 규칙 | record | 필드 | 시나리오·턴(§1.4) |
|---|---:|---|---|
| `pii_value` | 66 | `evidence.reselect.output[0]` | `APM-BIZTREND-interval#0`(`apm.business_trend`) |
| `user_info` | 81 | `observed.response` | `APM-PROC-pid-host#0`(호스트 + PID → 프로세스) |
| `pii_value` | 85 | `evidence.reselect.output[0]` | `APM-JSERVER-dbpath#0`(`apm.jennifer_server`) |
| `pii_value` | 86 | `evidence.reselect.output[0]` | `APM-JSERVER-dbpath-host#0`(`apm.jennifer_server`) |
| `user_info` | 115 | `observed.response` | `APM-SRC-legacy#0`(WAS 목록 · b0) |
| `user_info` | 119 | `observed.response` | `APM-SRC-none-all#1`(「전체」 답 · 3소스 인스턴스 목록) |

`APM-USERS-*`(로그 91~94 · 민감 API)는 위반 목록에 없다 — `sensitive: true` 턴은 응답이 모양만 남는다(`hygiene.py:706-709`). **사용자 정보 위반은 제니퍼 사용자 API가 아니다.**

### 1.3 이 근거로 알 수 없는 것 (한계)

- 턴별 5축·분류·지목 위치(`locus`) · 실패 턴 ID · few-shot 위치별 실패 · 축별 분모(source 제외) · 오류 코드 종류 — **전부 없음**. 아래 원인 분석은 로그 집계 + 코드 추론이며, 「실측」이라 쓴 것만 로그·파일에서 직접 읽은 값이다.
- 축별 분모: 로그는 비율만 준다. 36.4%·90.9%는 분모가 11의 배수일 때 나오는 값이다(예 36/99 · 40/110 · 32/88) — **추정**.
- 로그에 없는 폐쇄망 실행 조건: 체크아웃 SHA·작업 트리 변경 여부 · `--probe`·`--term-policy` 사용 여부 · 본체 `ACTIVE_DB_IDS` · 실행 계정 · 로컬 자리표의 `sources.by_placeholder` 범위 — G-8에서 사용자에게 확인한다. 단 산출 경로가 `/root/apm_bench_result/`라 **root 계정으로 실행했을 가능성이 높다**(경로 실측).
- 대표 분류 외의 분류(`categories` 전체 · `source_select` 등)는 로그에 없다 — 대표 분류에 가려진 분류는 0인지 알 수 없다(A-2).
- **이번 run의 턴별 근거는 복구할 수 없다** — 메모리에만 있다가 관문 실패로 버려졌다(`__main__.py:514` → `write_gated`). E2는 원본을 디스크에 쓰지 않는 것이 현행 규칙이다(`__main__.py:412-414` · `--debug-raw` E1 전용). 폐쇄망에 남은 것은 본체 `logs/`(운영 이름 포함 · 반출 금지 · D-323 부기 ⓑ)뿐이다. → **W0a**(§4.0)가 그 감사 줄을 화이트리스트 진단표로 줄여 반출한다(원본은 계속 반출 금지).

### 1.4 record 번호 → 시나리오 대응 (확정 · 상)

관문은 레코드를 1부터 센다(`scripts/itam_bench/redact.py` `LeakGate._units` `enumerate(start=1)` · `hygiene.py:1060`). 기록 순서는 일반 시나리오(YAML 순) → 무거운 등급 → `e2_skip`(맨 뒤 · `guard.schedule` `guard.py:116-140` · `driver.run` `driver.py:975-1000·993-995`). 로그가 일반 113 · 무거운 5 · 건너뜀 `e2_skip` 4뿐임을 확정하므로 `was_once`·`heavy_cap` 건너뜀이 끼지 않았고, YAML 턴 수로 재계산하면 위 표의 대응이 나온다(검토에서 재계산 일치).

---

## 2. 원인 분석

### 2.1 판정 집계가 원인 분포를 왜곡한다 (벤치 결함)

| # | 결함 | 근거 | 확신도 | 영향 |
|---|---|---|---|---|
| A-1 | **`gw_error`·`empty_result`·`answer_drop`은 축이 아니다** — 턴 통과는 5축만으로 정한다 | `scripts/apm_bench/judge.py:1101-1102`(`turn_pass` = 판정된 축 전부 pass ∧ 예상 밖 되묻기 없음) · `judge_results`(898~934행) | 상 | gw_error가 있어도 축이 다 맞으면 통과다. 「gw_error 59 → 28%」라는 읽기는 틀렸다 |
| A-2 | **대표 분류 우선순위 맨 앞이 `gw_error`** | `CATEGORIES`(98~114행) · `_rank`(955행)가 `CATEGORY_ORDER`로 정렬 | 상 | 같은 턴에 `target_unresolved`·`source_select`가 있어도 대표는 `gw_error`. target 축 36.4%인데 대표 `target_unresolved`가 3뿐인 모순이 이것으로 설명된다 |
| A-3 | **대표 분류는 통과 턴도 센다** | `summarize`의 `scored = [live 중 categories가 있는 턴]`(1185행 부근) — `live`에는 되묻기·거절 정답 턴도 들어간다(1043·1072-1078행) | 상 | 대표 분류 합 105 > 실패 일반 턴 90(125−35). 되묻기·거절 정답 턴 6이 모두 실패해 분류가 붙었다고 해도 **분류가 붙은 통과 턴이 최소 9개**(105−90−6). 「실패 원인 표」가 아니다 |
| A-4 | **업무명 간선 해석 단계의 정상 실패를 오류로 센다** | 드라이버는 그 스레드·chat 주체 감사 줄을 **전부** `calls`로 싣는다(`driver.py:589-590` · `_calls` 332~360행 — 해석 호출도 thread_id를 싣는다 `apm_query.py:2916-2918`). 본체는 업무명 → 서버(E6) 간선으로 얻은 서버마다 `apm_instance_map(hostname=…)`을 부르고, `instance_unresolved`면 「그 서버에 인스턴스 없음 = 0건」으로 조용히 넘어간다(`src/orchestration/apm_query.py:2988-2991` `_business_hosts` — 해석 단계에서 오류를 삼키는 곳은 여기 한 곳. 이름 검색 `_gateway_search` 실패는 `errors`로 기록된다 `:2934-2936`) | 중 | 업무명 대상 턴에서 인스턴스 없는 서버가 섞이면 정상인데도 `gw_error`. **E1은 활성 DB 0이라 E6 간선이 없어 재현되지 않았다**(157 §1.2 · G-1 (a)와 같은 환경 부정합) |

- 주의: **보기 호출**(대상별 본 호출)이 받은 `instance_unresolved`는 대상을 못 찾은 것이라 정당한 실패다(`apm_query.py:3405-3418` `_apm_hop_ledger`는 보기 호출의 원장). 이것은 계속 `gw_error`·target 실패로 남아야 한다.
- 결론: 로그 「대표 분류」로는 고칠 곳을 고를 수 없다. 재실행 전에 집계를 고친다(W3). **턴 통과 판정(5축)·분모는 바꾸지 않는다** — D-323 ① 불변.

### 2.2 target 축 36.4% (가장 큰 손실)

축별 분모를 모르므로 실패 턴 수는 **약 28~70턴(추정)**이다. 원인 후보:

| # | 가설 | 근거 | 확신도 | 검증(재실행 안전 요약으로) |
|---|---|---|---|---|
| T-1 | **소스를 잘못 좁혀 대상이 없는 소스로 불렀다** — 소스 3개가 모두 쓸 수 있으면 소스 단어가 없는 턴은 사다리 단 4에서 되묻고(`src/routing/apm_source_select.py:331-336`), 드라이버 자동 답은 자리표 → 소스 대응(`sources.by_placeholder`)에 없는 대상을 `default` 한 소스로 보낸다(`driver.py:131-134`·`:665-683`). 견본(`placeholders.e1.yaml`)은 `inst_b`·`inst_c`만 매핑한다. 해석 검색도 고른 소스 안에서만 하고(`apm_query.py:1156-1157` `source_ids` 부착), 게이트웨이는 고른 소스 전체에서 0건이면 `instance_unresolved`(`apm_gateway/apm_gateway/application/sources.py:187-205`) → **보기 호출 `gw_error` + target 실패가 한 턴에 같이 난다** | 코드 | 중상 | 턴별 호출 소스 × 대상 자리표의 실제 소스 · 소스 자동 답이 있었던 턴에 target 실패가 몰리는지 |
| T-2 | **자리표 값과 해석 결과 문자열의 경계 불일치**(거짓 실패) — 판정은 casefold 정확 일치 또는 `\w`·`-`·`/` 경계 포함만 인정(`judge.py` `_contains` 414~424행). 운영 이름이 `WAS01_1`·`WAS01-a`처럼 접미가 붙으면 실패 | 코드 | 중 | target 실패 하위 사유(`unresolved`·`other_target`·`kind_mismatch`·`dropped`) 분포 |
| T-3 | **887개 인벤토리에서 후보가 여럿이라 고르지 않고 제안만 했다** — 인스턴스 이름·설명 검색이 후보 상한(`MAX_SELECTION_CANDIDATES`)까지 제안만 하면 `_target_strings`는 후보를 세지 않는다 | `apm_query.py:2927-2950` · `judge.py:390-411` | 중 | 하위 사유 `unresolved` 중 후보 수 > 1 비율 |
| T-4 | **업무명 간선(E6)이 인스턴스 없는 서버를 냈다** — A-4와 같은 뿌리 | `apm_query.py:2953-2998` | 중 | 업무 대상 시나리오(`APM-BIZ-*`)의 target 실패율 |
| T-5 | **자리표 값 자체가 실 이름 형식과 다르다**(운영자가 채운 값 — 인스턴스 이름 대신 표시명 등) | A.1 절차는 채움만 검사하고 실재·형식은 검사하지 않는다 | 중하 | W2 자리표 실재 검사(인벤토리 대조 — 값은 출력하지 않고 「있음/없음/여럿」만) |

### 2.3 gw_error 59의 실체

게이트웨이 감사 줄 `error`는 오류 코드 어휘(`apm_gateway/apm_gateway/domain/errors.py`)만 싣는다. 후보 순위:

| # | 코드 | 조건 | 확신도 |
|---|---|---|---|
| H-1 | `instance_unresolved` | 보기 호출이 대상 없는 소스로 감(T-1 — 정당한 실패) + 업무명 간선 해석의 정상 실패(A-4 — 집계 결함) | 중 |
| H-2 | `invalid_argument` | 실 LLM이 채운 인자 형식 거부(양의 정수 · enum · ISO 시각 · 동시 지정 금지 — `application/tools.py:266·279·306·1060·1189·1191·2058-2082·3298-3315`) · `interval_minute`·지표 이름·`sort_by`의 **서버 측 거부**는 목이 재현하지 않는다(`spec/CAPABILITY-MAP-134.md` E-06 「W10 운영 검증 잔여」) | 중 — args 축 74.4%와 겹칠 것 |
| H-3 | `apm_api_error` | 401/403(소스별 토큰 권한) · 5.6.5.11 응답 모양 차이(`adapters/jennifer/api.py:40·72·199·375·438` · `manage_api.py:46`) — 구현 근거는 5.6.4 스펙·5.7.0.1 녹화(`CAPABILITY-MAP-134.md` 머리) | 중 |
| H-4 | `source_unavailable` | 도메인 미접속(빠른 실패) — 타임아웃 0이므로 9초 이상 걸린 것은 없다(`scripts/apm_bench/guard.py:53-56·146-149`) | 중하 |
| H-5 | `job_not_ready`·`job_not_found` | 작업 도구 폴링 중 정상 응답이 감사 `error`로 남음(`apm_gateway/apm_gateway/interface/server.py:300-305`). **W3 정정(2026-10-14)**: 작업 도구는 본체(`src/orchestration/apm_jobs.py` 폴링)가 부르지만 그 감사 줄에 thread_id가 없어 **턴 관측(`calls`)에는 잡히지 않는다** — 이 가설은 게이트웨이 로그 전체 기준으로만 성립하고 턴 판정의 `gw_error`(1회차 59)에는 기여하지 않는다 | 하~중 |

팬아웃 도구는 **부분 실패를 오류로 감사하지 않는다**(전부 실패일 때만 — `application/fleet_tools.py:227·395·624` · `batch.py:285-305`). 그러므로 gw_error 턴의 호출은 단건 실패이거나 전 도메인 실패다.

**검증 방법**: 재실행 안전 요약에 (도구 × 오류 코드 × 호출 단계[해석 / 보기]) 교차표를 싣는다(W3). 코드·도구 이름은 고정 어휘라 반출해도 이름이 새지 않는다.

### 2.4 source_miss 22 (축 ① · 157과의 관계)

- `source_miss`는 기대 담당(`apm_query`)과 관측 담당 집합이 다를 때다(`judge.py:466`). 소스 id 오지목(`source_select`)은 `expect.apm_sources`가 있는 소수 `APM-SRC-*` 턴에서만 판정되고 대표 순위가 `gw_error`·`source_miss` 뒤라(`judge.py:103-105·468-472`) 로그에 0으로 보여도 **0인지 알 수 없다**(중하). 또 대부분 턴의 소스는 드라이버 자동 답이 정하므로 source 축은 그 정확성을 재지 않는다 — T-1은 source 축이 아니라 target 축에 드러난다.
- 후보 ① **분해 LLM이 APM이 아닌 담당(폴스타 등)으로 보냈다** — E2 본체는 폴스타 DB가 활성일 가능성이 높아(폐쇄망 정본 `.env`) 담당 경쟁이 있다. E1은 활성 DB 0이라 경쟁이 없었다. few-shot F2·F3 대상(152 §3.5) · 중.
- 후보 ② **라우트 존 사전 게이트**(157 결함 ① · `code:route_zone_gate`) — 157은 「2단 ∧ 활성 시스템 ≥ 2」면 폐쇄망에서 재현되지 않는다고 판정했다. 이 run은 2단이 확정됐으므로 활성 DB가 있었다면 비재현이 맞다 · 중하.
- **겹침 판정**: 이 run의 22턴이 157 결함 ①인지는 `locus` 분포 없이는 알 수 없다. 재실행 안전 요약의 `locus`(`code:route_zone_gate` 대 `F2`·`F3`) 건수로 가른다 → 결과를 157 G-1에 넘긴다(§3).

### 2.5 args 74.4 · views 82.6 · window 90.9 (few-shot 회차 대상)

E1은 가짜 LLM이 정답 계획을 넣어 args·views가 100%였다(152 §9). 이 두 축의 손실은 **실 LLM 분해의 첫 측정값**이며 152 §3.9 few-shot 루프의 본 대상이다. 다만 턴별 근거가 없어 지금은 위치(F1~F4)를 고를 수 없다 — 재실행 반출 뒤 round1 계획으로 넘긴다. window 90.9%(대표 `window_wrong` 1)는 `plans/156` 범위로 본다.

### 2.6 누출 관문 위반 (W1 대상)

**`pii_value` 3건 — 근거 묶음 키 이름 변경이 보호를 벗겼다(거짓 양성 · 상)**
- 실측: `hygiene.py:884` `build_evidence`가 `observed.reselect.result_views`(재선택 결과 **보기 id 목록** — 출처 `driver.py:408` 본체 `meta["views"]`, `sanitize_views`(`apm_query.py:248-257`)를 거친 레지스트리 id)를 `"output"` 키로 옮겨 싣는다. `result_views`는 `SKIP_KEYS`(`hygiene.py:110-115`)라 치환·토큰 대조에서 빠지지만 `output`은 목록에 없다. 근거 묶음은 치환이 끝난 **뒤** 만든다(`__main__.py:497-498`).
- 관문의 토큰 대조(`ApmLeakGate._token_leaves` `hygiene.py:1036-1048`)가 `apm.business_trend`·`apm.jennifer_server`의 낱말(`business`·`jennifer`·`server`·`trend` 등)을 운영 인벤토리 이름 토큰과 대조해 걸렸고, 토큰 대조 위반은 rule `pii_value`로 기록된다(`hygiene.py:1068-1069`).
- **원이름 전체 일치는 배제된다**: 원이름 정규식 대조는 `SKIP_KEYS` 칸을 포함한 모든 잎을 보는데, 같은 값이 그대로 있는 `observed.reselect.result_views[0]`에서는 위반이 나지 않았다 → 원이름이 아니라 토큰 일치다.
- 왜 E1에서 안 났나: E1 목 인벤토리 이름에는 그런 낱말 토큰이 없었다. 실패 턴만 근거 묶음 `full`을 만들어 이 칸이 생긴다.

**같은 뿌리의 기존 위생 구멍(보안 검토 지적 · W1 대상)** — `SKIP_KEYS`는 **키 이름만 보고** 치환·토큰 대조를 면제한다. `parsed_views`는 재선택 LLM **원출력**(`apm_query.py:1427` `run_capture.emit("apm_view_reselect", {"parsed": parsed …})` → `driver.py:409`)을 그대로 싣는다 — LLM이 보기 id가 아닌 문자열(호스트 이름 등)을 내면 **치환 없이 반출된다**(원이름 전체 일치만 관문이 잡는다). E1 관문 통과 run에도 있던 구멍이다.

**`user_info` 3건 — 관문과 치환기의 대조 값이 비대칭(비대칭 상 · 원인 값 중)**
- 실측: 관문 사용자 정보 값은 `run_user_values()` = 로그인 id(인증 꺼짐 → `DEFAULT_USER_ID` `scripts/scenario/runner.py:88`) · `getpass.getuser()` · `socket.gethostname()` · 홈 경로(`__main__.py:221-226`)이고, 3자 이상이면 **대소문자 구분 순수 부분 문자열**로 대조한다(`redact.py:1940·1949`). 치환기 `RunSanitizer.sanitize()`는 이 값들을 받지 않는다(`hygiene.py:503-511`).
- 후보 ① **root 실행**(중): 산출 경로 `/root/apm_bench_result/` → OS 사용자 `root`·홈 `/root`. 프로세스 정보(record 81 — PID로 프로세스 조회)·인스턴스 목록 응답에 `root`가 부분 문자열로 들어가면 걸린다. 후보 ② **실행 호스트가 제니퍼 감시 대상**(중하): 호스트 이름이 인벤토리에 FQDN으로, 응답에는 짧은 이름으로(또는 반대로) 나오면 원이름 치환이 놓치고 관문의 부분 문자열 대조가 잡는다. 후보 ③ 로그인 id 숫자열이 응답 수치에 겹침(하).
- 어느 쪽이든 **관문 규칙을 완화하지 않는다**. 고칠 곳은 (a) 실행 계정 운용(G-3) (b) 치환기가 사용자 정보 값을 관문과 같은 의미로, 원이름 치환 **뒤에** 치환(W1)이다.

### 2.7 `apm_gateway/var` 경고 (상)

- 벤치는 `APM_SPOOL_DIR`을 세션 임시 디렉터리로 주입하고(`scripts/apm_bench/gateway.py:359-368`) 게이트웨이는 같은 키를 읽는다(`apm_gateway/apm_gateway/config.py:257-259·358`). `.env`의 `load_dotenv`는 이미 있는 환경 변수를 덮지 않는다(`config.py:298-299`). 작업 결과·응답 임시 파일·인벤토리도 모두 그 스풀 안이다 → **주입 누락이 아니다**.
- 경고 판정은 run 끝에 `apm_gateway/var`가 **있는가**만 본다(`gateway.py:507-509` → `driver.py:1030` → `__main__.py:539-540`). 이 맥에도 2026-10-13 11:05에 생긴 `apm_gateway/var/spool`(빈 디렉터리 · 700)이 있다(실측) — pytest·수동 기동으로 전에 생긴 디렉터리가 경고를 낸다.
- 교정: run 시작 시점과 끝 시점의 `var/` **안 파일 목록**을 비교해 이 run이 새로 쓴 경우만 경고(W4 — 디렉터리 존재 비교만으로는 기존 `var/` 안에 새로 쓴 경우를 놓친다).

### 2.8 소요 8102.7초 — 재실행 비용

- 호출 1,083회 ÷ 초당 2회 = 하한 약 9분. 나머지 약 2시간은 본체(FabriX 분해·재선택·응답 생성 — 턴당 약 62초)와 인벤토리 수집(최대 900초 상한)이다(추정 — 단계 시간 기록이 반출되지 않았다).
- §3.7 허용 범위에는 시간 상한이 없고 「업무 외 시간」만 있다. 위반은 아니지만 **같은 비용으로 근거 0을 다시 얻으면 안 된다** → 스모크 우선(§5).
- 부분 재실행 수단은 이미 있다: `--only <시나리오 id…>` · `--limit N`(`__main__.py:711-712`). `--turns ID#N`은 `--replay` 전용이고(`:722-723`), `--replay`는 반출 trace가 있어야 하므로 이번엔 쓸 수 없다.

---

## 3. 인접 계획과 범위 — 편입 판단

| 계획 | 겹침 | 처리 |
|---|---|---|
| `plans/152`(WIP) | 이 계획이 고치는 것은 전부 152가 만든 벤치 도구(`hygiene`·`judge`·`gateway`·`driver`·`__main__`)다 | 152 본문에 넣으면 W8(내부망 실행)과 섞여 상태를 읽기 어렵다. 152 A.8이 「회차마다 후속 계획」을 정했으므로 **새 계획(164)** 으로 두고, 152 §9에 1회차 결과 1항목과 넘김만 적었다 |
| `plans/157`(TODO) | ① 존 사전 게이트 — E2 source_miss 22의 일부일 수 있다(§2.4) · ② APM 대상 미승계 — target 축 중 **멀티턴 승계 턴**(`APM-MT-inherit-target`·`APM-SRC-inherit` 등 소수)에만 해당 · ③ 드라이버 가짜 파싱 답 — **E1 전용**(E2는 실 LLM) · 파일: 157도 `driver.py`·`judge.py`(필요 시)를 고친다 | **편입하지 않는다** — 164는 측정 도구·반출, 157은 제품 결함 2 + E1 드라이버. 164 재실행의 `locus` 건수를 157 G-1 판단 재료로 넘긴다. 157 G-1 (a)(E1에 폴스타 활성 DB 주입)는 A-4(E6 간선 경로)를 E1에서 재현하는 데도 필요하다. **착수 순서는 §6** |
| `plans/156`(TODO) | window 90.9%(대표 1) | 156 범위. 164는 손대지 않는다 |
| `plans/158`(TODO) | 없음 | — |
| `plans/161`(WIP) | 소스 id `b0`·`cm_prd`·`cm_stg`는 이미 반영(이 run도 그 id로 돌았다) | — |

---

## 4. 설계

### 4.0 W0a — 폐쇄망 본체 감사 로그 진단표 (재실행 없이 1회차 턴별 원인 복원 · 사용자 *"방안 1로 진행해서 164에 W0a 추가하라"* 2026-10-14)

**왜** — 1회차 벤치 산출물은 버려졌지만 **본체는 실행 중 폐쇄망 `logs/audit-2026-10-13.jsonl`에 감사 줄을 남겼다**(D-323 부기 ⓑ — 감사 로그는 끄지 않는다). 이것을 폐쇄망에서 **화이트리스트 진단표**로 줄여 반출하면 2.25시간 재실행 없이 §2 가설(T-1~T-5 · A-4 · source_miss·157 G-1 · H-1~H-5)을 시나리오 단위로 가를 재료가 생긴다. `logs/` 자체는 계속 반출 금지이고(D-323 부기 ⓑ · A.7), 반출하는 것은 **코드가 어휘를 강제한 파생 요약 1개**다(D-219 ② 서버 측 축소와 같은 방향).

**입력(감사 줄 실측 — `src/security/audit_logger.py:373-` `log_investigation` · `:491-` `log_clarification` · `src/orchestration/investigation_audit.py:85-108`)**
- 파일 이름은 **로컬 날짜**(`audit_logger.py:134`), `timestamp`는 **UTC**다. 로테이션 시 `audit-YYYY-MM-DD.N.jsonl`이 생긴다(`:633`) → 파일을 여러 개 받는다.
- `host_investigation`(backend `apm_gateway`): `profile`은 보기 id를 **쉼표로 이은 값**(`investigation_audit.py:87`) · `outcome` `ok|partial|failed|denied|timeout` · `degraded[]` = `{reason}` 또는 `{reason, detail}` · `target_count` · `duration_ms`. `reason`은 본체 어휘(`apm_unresolved_condition`·`apm_target_unresolved`·`apm_calls_failed`·`access_denied`·`apm_job_accepted`·`apm_partial_sources` 등)이거나 **보기 호출 실패 사유 원문 `str(f["reason"])[:120]`**(`:103`) — 원문은 `instance_unresolved: hostname '<이름>'에 대응하는 …`처럼 **게이트웨이 오류 코드 + 자유 문장**이다.
- `clarification_issued`: `kind`(`apm_source`·`zone_select` …) · `axis` · `option_count`.
- **출력 금지 칸**: `targets`·`commands`·`detail`·`stdout`·`authz`·`user_id`·`thread_id`·`timestamp` 원값 · `query_execution` 전부(SQL 원문). `detail`에는 작업 id·`<보기>:<호스트>`가 들어간다(`:104-105`).
- 벤치 본체는 인증을 끄고 돌아 `user_id`가 `anonymous`다(`src/api/dependencies.py:21` · 1회차 로그 「로그인 생략」). pytest·운영 질의가 같은 `logs/`에 섞일 수 있다(이 맥 `logs/`는 pytest 줄뿐 — `u1`·`alice`·None).

**도구** — `scripts/apm_bench/audit_digest.py` + 단일 진입점 모드 `python -m scripts.apm_bench --audit-digest <감사 로그…> --since <ISO+오프셋> --until <ISO+오프셋> --env e2 --out <dir>`(벤치 CLI 관례 「단일 진입점」 — `scripts/apm_bench/__init__.py`). 본체·`apm_gateway` import 0(레지스트리 보기 id는 기존 `scenarios.load_registry_info`로 읽는다 — D-274 · D-139). 제품 코드 변경 0.
1. **시간 창**: `--since`/`--until`은 **오프셋 필수**(없으면 exit 2 — 폐쇄망 현지 시각이 KST라는 가정은 검증 불가). 창 밖 줄·다른 사용자 줄(`--bench-user` 기본 `anonymous` · 값은 출력하지 않음)·thread_id 없는 줄은 **건수만** 머리에 낸다(혼입 지표).
2. **기대 순서**: 벤치와 같은 함수로 만든다 — `select_scenarios`(`--only`·`--limit`) → `--skip-heavy` → `guard.schedule(env)` → `plan.light` + `plan.heavy`(`driver.py:975-995`). 머리에 일반·무거운·건너뜀 수를 내 1회차 로그(113 + 5 · `e2_skip` 4턴)와 사람이 대조한다. 실행 SHA의 시나리오 파일이 다르면 `--scenarios`로 그 사본을 준다.
3. **관측 단위**: 감사 줄을 `thread_id`로 묶어 **첫 줄 시각 순**으로 놓는다. 벤치는 시나리오마다 새 스레드를 쓰고 멀티턴·소스 자동 답은 같은 스레드다(`driver.py:600-683`). 그래서 대응 단위는 **시나리오**이며, 멀티턴 시나리오의 줄을 턴별로 나누지 않는다(`turn_count`만 싣는다 — 턴 경계는 감사 줄로 판정할 수 없다).
4. **대응(정렬)**: 감사 줄이 **하나도 안 남는 시나리오**(거절·사전 되묻기·APM 미호출)가 끼면 순서 대응이 밀린다 → 기대 시나리오열과 관측 스레드열을 **순서 보존 정렬(DP)** 한다. 점수는 기대 보기 집합(턴 `expect.views` 합집합)과 관측 `profile` 집합의 겹침, 비용은 「기대 시나리오 건너뜀(이벤트 0)」 < 「관측 스레드 건너뜀(혼입)」. 대응 표지:
   - `exact` — 그 짝이 **모든 최적 정렬에 공통**이고 보기가 겹친다
   - `ambiguous` — 최적 정렬이 여럿이라 짝이 갈린다(같은 보기 시나리오가 연달아 있고 그중 일부가 이벤트 0일 때 등) 또는 보기가 안 겹친다 → `scenario_id`를 비우고 후보 id 목록만 싣는다(추측 금지)
   - `missing` — 대응 스레드 없음(이벤트 0 — 기대가 되묻기·거절인 시나리오면 정상)
   - 대응되지 않은 관측 스레드는 `unmatched` 건수만
   - 머리 `mapping` 블록에 위 건수를 낸다. **정확성 기준: `exact`는 틀리지 않는다**(외부망 실데이터로 정답 대조 — §6 W0a 완료 기준).
   - **구현 부기(2026-10-14)**: 비용은 기대 건너뜀 1 · 관측 건너뜀 3 · 보기 불일치 짝 2. 비용 모형 하나로는 창에 다른 run이 같은 보기 순서로 끼면 틀린 짝이 유일해져(외부망 정답 대조 실측) **관측 건너뜀 비용만 다른 모형 3개**(안쪽 3·가장자리 3 / 0·0 / 3·0)의 최적 정렬 전부에서 짝이 하나일 때만 `exact`로 한다(전방·후방 DP). 또 실 LLM이 틀린 보기를 고른 턴이 바로 옆 무이벤트 시나리오의 기대 보기와 같으면 감사 줄로는 가를 수 없으므로, **`exact` 짝의 바로 앞·뒤 기대 시나리오가 무이벤트면 `ambiguous`로 강등**하고(`mapping.downgraded_silent_neighbor`) 그 이웃은 `missing`에서 빼 후보로만 싣는다.
   - 기대 순서는 시나리오 파일에 좌우된다 — 실행 SHA와 시나리오 id가 다르면(예 `205d904`에서 `APM-SRC-common` → `APM-SRC-cm-prd`) 그 SHA의 사본을 `--scenarios`로 준다.
5. **화이트리스트 출력 `audit_digest.json`** — 잎은 정수·실수·불·null, 또는 다음 어휘의 문자열만: 시나리오 id(시나리오 파일에 있는 것) · split · 보기 id(레지스트리 apm 보기 ∩ `^apm\.[a-z_]+$`) · outcome enum · 대응 표지 enum · 사유 **코드**(정규식 `^[a-z][a-z0-9_]+` 접두 + 알려진 어휘 — 본체 사유 + 게이트웨이 오류 코드 사본 · 사본과 `apm_gateway/apm_gateway/domain/errors.py`의 일치는 테스트가 텍스트로 대조) · 되묻기 `kind`/`axis` enum · 기대 대상 종류(`inst`·`host`·`biz`·`svc` 등 `expect.target` 접두). 순서 번호가 thread_id·타임스탬프를 대신한다. 어휘 밖 문자열은 **버리고 `dropped` 건수만**. 쓰기 전 출력 전체(키 포함)의 문자열을 같은 어휘로 **다시 검사**해 하나라도 벗어나면 파일을 쓰지 않고 exit 1.
6. **집계 절**(같은 직렬화본에서 콘솔에도 낸다): ① 보기 × outcome ② 사유 코드별 건수(+ 버린 사유 수) ③ 기대 대상 종류별 대상 해석 실패(`apm_target_unresolved`·`instance_unresolved`·`target_unavailable`가 난 `exact` 시나리오 수 / 그 종류 시나리오 수) ④ 되묻기 종류별 · `zone_select` 되묻기만 있고 APM 조사가 없는 시나리오 수 ⑤ split별 대응·outcome ⑥ `hypotheses` — 아래 표의 지표를 한곳에 모은다.

| 가설(§2) | 진단표에서 읽는 지표 |
|---|---|
| T-1 소스 오좁힘 · H-1 | 사유 `instance_unresolved` 건수·시나리오 · `apm_source` 되묻기 뒤 `instance_unresolved`가 난 시나리오 수 |
| T-3·T-4·A-4 대상 해석 | 대상 종류(`inst`·`host`·`biz`·`svc`)별 대상 해석 실패율(사유 `apm_target_unresolved`·`instance_unresolved`·`target_unavailable` + 고정 문형 「대상 '…' 해석 0건」→`target_text_unresolved` · 「… 이름 길이 초과」→`target_name_too_long` — 따옴표 안 값은 버린다) — `biz`에 몰리면 T-4·A-4 |
| T-2·T-5 | 감사 줄로는 가를 수 없다(문자열 비교·자리표 값) — 재실행 안전 요약(W2) 몫 |
| H-2~H-5 오류 코드 | 사유 코드 `invalid_argument`·`apm_api_error`·`source_unavailable`·`job_not_ready`·`job_not_found` 건수 |
| source_miss 22 · 157 G-1 | 무이벤트 시나리오 하한 `silent_lower_bound`(= 기대 − 대응 스레드) · 강등 후보로 빠진 무이벤트 이웃 `silent_candidates` · 확정 `missing` 중 기대가 APM 조회인 것 · `zone_select` 되묻기만 있는 스레드(존 사전 게이트 `code:route_zone_gate` 후보) |

**한계** — 판정 5축·`locus`는 감사 줄에 없다(벤치 판정기 산출물). **`exact`의 전제**: 관측 보기가 우연히 이웃 시나리오의 기대 보기와 같아도 1칸 밀림은 강등 규칙이 막지만, **두 턴 연속으로 앞 시나리오 보기를 고른 2칸 밀림은 감사 줄만으로 가를 수 없다**(verifier 반례 · strict xfail로 고정) — `exact` 집계도 방향 근거로만 쓴다. 이 진단표는 **원인 분포의 방향**을 주고, 확정은 재실행 반출본(W2 안전 요약)으로 한다. `ambiguous` 구간은 결론에 쓰지 않는다.

### 4.1 W1 — 관문 위반 교정 (관문 규칙은 완화하지 않는다)

1. **근거 묶음 키**: `build_evidence`의 재선택 결과를 코드 어휘 키로 싣는다 — 권고는 `"output"` → `"result_views"`(키 이름 원복 · `SKIP_KEYS` 그대로). 대안 `SKIP_KEYS`에 `output` 추가는 일반 이름이라 자유 문자열 칸이 보호를 잘못 받는다 → 비권고. `reselect.output`을 단언하는 테스트·`report.md` 렌더를 grep으로 함께 고친다.
2. **`SKIP_KEYS` 칸 값 검증**(§2.6 기존 구멍): 보기 id를 담는 `SKIP_KEYS` 칸(`views`·`view`·`result_views`·`parsed_views`·`view_rows`)은 레지스트리 보기 id 집합과 **교집합만** 남기고 밖의 값은 버린 뒤 건수만 센다(trace·근거 묶음 양쪽). 그 밖의 `SKIP_KEYS` 칸도 값이 코드 어휘 정규식에 맞지 않으면 관문이 실패하게 하는 단위 테스트를 둔다.
3. **같은 유형 전수 점검**: 근거 묶음·trace에서 코드 어휘를 `SKIP_KEYS` 밖 이름으로 싣는 칸을 전수 찾는다. 이미 아는 것: `observed.calls[].error`·`evidence.execution.errors`(오류 코드 · `hygiene.py:935`) · `reselect.candidates`(보기 id · `apm_query.py:1488`). 단 `target.candidates`에는 **인스턴스 원이름**이 들어가므로 `candidates`를 `SKIP_KEYS`에 넣으면 안 된다 — 재선택 쪽 키 이름만 바꾼다(예 `candidate_views`).
4. **사용자 정보 대칭 치환**: `RunSanitizer`가 `run_user_values()` 값(3자 이상)을 관문과 **같은 의미(부분 문자열 · 대소문자 구분)** 로 자유 문장 칸(`response`·`clarify_text`·`answer_lines`·`disclosures.text`)에서 고정 표지(예 `<사용자정보>`)로 바꾼다. 관문 `user_info` 규칙은 그대로 둔다 — 치환기가 놓치면 여전히 관문이 막는다.
   - **순서**: 원이름·ID·토큰 치환이 **끝난 뒤 맨 마지막**에 적용한다 — 먼저 돌면 운영 이름 조각(예 실행 호스트 `was01`이 운영 이름 `ordwas01`의 일부)을 표지로 바꿔 원이름 치환·토큰 대조 신호를 가린다.
   - **충돌 중단**: 사용자 정보 값이 인벤토리 이름·토큰의 부분 문자열이거나 그 반대면 치환하지 않고 **턴 송신 전에 exit 2**(인벤토리 수집 직후 검사 — 값은 출력하지 않고 종류만: `os_user`/`host`/`home`/`login_id`).
   - 치환 건수는 종류별로 `report.md` §11과 `summary_safe.json`에 남긴다(값 없이).
   - 주의: `root`처럼 짧은 일반 단어를 부분 문자열로 바꾸면 `rootPath` 같은 무관한 낱말도 깨진다(진단 손실). 근본 처리는 G-3(비root 실행)이다.
5. **회귀 테스트**(신규 `tests/test_scripts/test_apm_bench_leak_e2.py`): ① 인벤토리 토큰이 보기 id 낱말과 겹치는 run에서 재선택 근거가 관문을 통과 ② OS 사용자·호스트 문자열이 응답에 부분 포함된 run에서 관문 통과 ③ **음성** — 실 원이름·카나리아·JWT를 자유 문장에 넣으면 여전히 위반(관문 미완화 고정) ④ **음성** — 실행 호스트 이름이 인벤토리 이름의 조각이면 exit 2 ⑤ **음성** — `parsed_views`에 보기 id가 아닌 문자열(자리표 원이름)이 오면 반출본에 남지 않는다.

### 4.2 W2 — 관문이 실패해도 진단 가능한 반출

**(a) 안전 요약 `summary_safe.json` — 관문 성패와 무관하게 기록 시도(G-1)**
- 구성 원칙: **허용 키·허용 값 어휘로 만든다(생성 시 화이트리스트 — 코드로 강제)**. 잎 값은 정수·실수·불·null, 또는 **모듈 상수 enum·정규식에 속하는 문자열만** 쓴다:
  - 시나리오 id·턴 번호 — `scenarios.yaml`에 있는 것만(집합 교집합)
  - 축 이름 · 분류 13종 + `resolve_miss` · target 하위 사유 enum(`unresolved`·`other_target`·`kind_mismatch`·`dropped` — W0에서 `judge.py` 사유 코드 전수로 확정) · 되묻기 종류 enum(`clarify_kind` 값 집합) · split
  - `locus` — `F1`~`F4`·`code:<상수 목록>`·`sources:<D-329 id>`는 고정. **`F1:<보기>`·`F1:<보기>:args.<인자>`·`F1:<보기>:example#N`은 레지스트리 보기 id 집합 ∩ 그 보기에 정의된 인자 이름 집합에 있을 때만** 통과(`judge.py:947`의 `arg_hallucination` locus는 LLM이 낸 인자 키에서 만들어진다 — `judge.py:859-870`). 밖이면 버리고 `locus_dropped` 건수만
  - 게이트웨이 오류 코드 · 도구 이름 — **벤치 쪽 상수 사본**으로 두고 `apm_gateway`를 import하지 않는다(D-274). 사본과 게이트웨이 정본의 일치는 테스트가 파일을 텍스트로 읽어 대조한다
  - 건너뜀 사유 — 접두가 아니라 **정규식 화이트리스트**(예 `^(e2_skip|heavy_off|was_once:[A-Za-z0-9_./-]+|heavy_cap:\d+|heavy_gate:.*|aborted:(quota_429|timeout_ratio|latency_3x|budget:(b0|cm_prd|cm_stg)))$` — W0에서 `guard.py:130·221-263`·`driver.py:986` 실제 값으로 확정). 불일치는 `skip_other` 건수만
  - 어휘 밖 문자열은 **버리고 건수만** 남긴다.
- 내용: run 수치(현 콘솔 요약 + `summarize()`의 counts·turn·axes·by_split·by_cov·categories·대표 분류 2종 — §4.3) · **턴별 행**(시나리오 id#턴 · split · 5축 pass/fail/na · 분류 · locus · 호출별 (단계 · 도구 · 오류 코드 · 행 0 여부 불값) · target 실패 하위 사유 enum · 되묻기 종류 enum · 건너뜀 사유 · 사용자 정보 치환 건수) · 관문 결과(규칙별 건수·record 번호·필드 경로 — `leak_check.json`과 같은 정보).
- 넣지 않는 것(경로째 금지): 프롬프트·응답·`answer_lines`·대상 문자열·`selected`·`candidates`·인자 **값**·오류 사유 문장(`reason` — 호스트 이름이 들어간다 · `apm_query.py:2995` · `driver.py:411`)·예외 메시지·PID·도메인 번호. 인스턴스 수·소스별 호출 수는 이미 콘솔 로그로 반출되는 수준이라 넣되 G-1에서 구간화 여부를 함께 묻는다.
- 쓰기 전 `ApmLeakGate`의 **전 규칙**(원이름·카나리아·정규식·사용자 정보 + **토큰 대조** — `TOKEN_FILES`(`hygiene.py:1029`)에 `summary_safe.json`을 추가)으로 재검사한다. 걸리면 **그 파일도 쓰지 않는다**. 단 관문은 대조 장치라 **새 어휘를 막지 못한다** — 안전의 1차 근거는 화이트리스트이고 관문은 2차다. 토큰 대조 때문에 보기 id 등이 막히는 일이 잦으면 §9 위험대로 사용자에게 올린다.
- **콘솔 요약도 같은 직렬화 함수에서만 만든다**: 실행 로그는 관문을 거치지 않고 반출된다(이번 run이 그랬다). 로그 요약 줄은 관문을 통과한 안전 요약에서만 만들고, 통과하지 못하면 그 줄을 `[위생] 로그 출력 억제`로 바꾼다. 예외 메시지(`str(exc)`)·`reason`·스택 트레이스는 반출 대상 로그에 원문으로 찍지 않는다. 152 A.7에 「실행 로그는 화이트리스트 직렬화본만 담는다」를 명시한다.
- 반출 목록(A.7)에 `summary_safe.json`을 추가한다 — 관문 실패 run이면 `leak_check.json` + `summary_safe.json` + 실행 로그만 반출.
- D-219 ②(서버 측 축소)와 같은 방향이고 D-323 ④(근거 묶음)를 대체하지 않는다 — 근거 묶음은 관문 통과 시에만 나간다.

**(b) 관문 실패 run의 내부망 보관과 재관문(G-2 — 현행 E2 규칙 개정)**
- 현행: E2는 원본을 디스크에 쓰지 않는다(`__main__.py:412-414` · `--debug-raw` E1 전용 · 152 §10.7). (b)는 이 규칙을 **관문 실패 시에 한해** 바꾼다 — 확장이 아니라 개정이다(§10). 선례: D-219 ② 「원본은 재측정 종료까지 서버에 보관 · 반출 금지」.
- 동작: 관문 실패 시 **치환 전 재료**를 실행 머신 로컬에 남기고, `--regate <quarantine>` 모드로 **위생 코드를 고친 뒤 실행 없이** 치환·근거·관문만 다시 돌린다(2.25시간 재실행 회피).
- 보관 목록: 판정된 레코드(`records`) · run 메타(`meta`) · 인벤토리 이름(887개 — 관문 원이름 대조 집합) · `probe_rows` · 자리표 값·카나리아(로컬 자리표 파일 경로가 아니라 run이 읽은 값) · 사용한 `term_policy` · 버전 정보(SHA·지문).
- 위치: **`<out>` 밖 형제 경로**(`<out>.quarantine/`) — `<out>` 하위에 두면 산출 디렉터리 통째 복사·압축 습관으로 원본이 반출된다. 코드가 `Path.resolve()`로 포함 관계를 검사해 `<out>` 하위·저장소 하위면 거부한다. 디렉터리 700 · 파일 600 · 실행 계정 소유.
- 보존: 반출 완료 또는 **최대 14일** 중 먼저 오는 때 삭제(`--regate` 성공 뒤 안내 · `--purge`로 삭제). `--regate`가 읽는 파일의 신뢰 경계는 같은 머신·같은 계정으로 한정한다.
- 보안 성격: 운영 이름 원문을 담는다 — 본체 `logs/`(D-323 부기 ⓑ)와 같은 등급이지만 「반출 직전 산출물 옆에 있다」는 점에서 실수 반출 위험이 더 크다. 그래서 위치·권한·보존을 코드로 강제하고, 반출은 선별 목록 + 파일 해시 매니페스트로만 한다(A.7 갱신). **사용자 결정 사항이다**(권고: 위 통제와 함께 허용).
- 활용: 관문이 또 실패하면 `--regate`로 원인 칸을 내부망에서 확인하는 유일한 길이다.

**(c) 위반 레코드 제외 축약본 — 비권고(G-1 대안)**
- 위반 필드만 가리면 같은 레코드 다른 칸에 같은 값이 남을 수 있어(관문이 못 보는 형태) 레코드 통째 제외가 최소 조건인데, 그러면 **진단하려는 실패 턴이 빠져** 통과 턴 쪽으로 치우친다. (a)+(b)로 충분하다.

**(d) 실행 전 키트 검사(T-1·T-5)** — 인벤토리 수집 직후, 턴 송신 전에:
1. **자리표 실재**: 자리표 값과 인벤토리 이름을 대조해 「있음 · 없음 · 여럿(부분 일치 N)」만 출력한다(값은 출력하지 않는다). 처리는 G-4.
2. **소스 대응 범위**: 시나리오가 쓰는 대상 자리표 중 `sources.by_placeholder`에 없는 것(→ 자동 답이 `default` 소스로 보낸다)을 목록으로 경고하고, 인벤토리에서 그 자리표 값이 실제로 있는 소스(도구 응답의 소스 표지가 있으면)와 `default`가 다르면 경고한다.

### 4.3 W3 — 판정 집계 교정 (턴 통과 판정·분모 불변)

1. **대표 분류 새 키** — 기존 `primary_categories`의 의미는 바꾸지 않고(소비처 `__main__.py:329·537` · `test_apm_bench_judge.py:695`), 실패 일반 턴만 센 `primary_failure_categories`를 **추가**한다. 콘솔·`report.md`·안전 요약은 새 키를 먼저 보이고 기존 키는 「통과 턴 포함」 표지와 함께 둔다.
2. **대표 순위 조정은 좁게** — `gw_error`를 통째로 뒤로 미루지 않는다(401 `apm_api_error`·`source_unavailable` 같은 환경 원인이 하류 증상을 만든다는 현행 근거 `judge.py:93-96` 유지). 뒤로 빼는 것은 ③의 **해석 단계 `instance_unresolved`** 뿐이다.
3. **호출 단계 구분** — 오류 감사 줄은 `search=`를 싣지 않고(`apm_gateway/apm_gateway/interface/audit.py:51-67` — warning 갈래에 꼬리 없음) 벤치 파서도 버린다(`gateway.py:535-546`). 실패한 보기 호출은 provenance가 아니라 `meta.failures`로 가므로(`apm_query.py:3540-3545`) `_calls`의 도구 이름 선착순 짝짓기(`driver.py:337-339`)로는 해석·보기 호출이 섞인다. **본체를 바꾸지 않는 방법**: 캡처 `investigation_result` 메타에 이미 있는 보기 호출 실패 목록(`failures` — 보기·호스트·사유)과 대상 해석 기록(`targets.texts[].failures/searched` · `scripts/apm_bench/_serve.py:86-114`)을 `build_observed`가 싣게 하고, 오류 감사 줄을 **보기 도구별 `failures` 건수와 맞춰** 맞는 것은 `view` 단계, 남는 `instance_unresolved` 줄은 `resolve` 단계로 본다. `resolve` 단계 `instance_unresolved`는 `gw_error`에서 빼고 `resolve_miss`(정보성 · 건수만)로 센다. 맞추기가 모호하면 현행대로 `gw_error`(보수적). 이것으로 안 되면 본체 emit이 필요하다 → G-5.
4. **target 실패 하위 사유 집계** — `axis_detail.target`의 사유 코드를 `summary`·`summary_safe.json`·콘솔에 건수로 낸다.
5. **콘솔 요약 확장** — 실패 턴 대표 분류 · (도구 × 오류 코드 × 단계) 상위 10 · target 하위 사유 · `locus` 상위 10. §4.2(a)대로 안전 요약 직렬화본에서만 만든다.
6. **재판정 동치 검증** — 기준 run 산출물이 저장소에 없고 152 E1 기준은 159·161 이전 코드라 그대로 대조할 수 없다. 그래서 **W0에서 계획 시작 SHA로 E1 `--debug-raw` 1회**를 돌려 `records.jsonl`(판정 전 기록 · `__main__.py:472-480`)을 확보하고, 같은 레코드를 시작 SHA 판정기(`git worktree add --detach`)와 새 판정기로 오프라인 재판정해 **턴별 `turn_pass`·축이 같고 분류 집계만 바뀌는지** 확인한다(판정 완화가 아님을 고정).

### 4.4 W4 — 실행 위생

1. `apm_gateway/var` 경고: run 시작 전 `var/` 안 파일 목록을 기록하고, 끝에 **새로 생기거나 바뀐 파일이 있을 때만** 경고 · 시작 전에 있었으면 「이전부터 있음」 정보 줄.
2. **실행 계정 점검**(G-3): E2에서 실행 계정이 root면 시작 전 exit 2(G-3 (a)). 전용 계정 이름은 3자 이상 무의미 문자열(예 `apmbench`)로 하고 홈 경로에 운영 서버 이름이 없게 한다 — 계정 이름·홈도 `user_info` 대조 값이기 때문이다. 인벤토리 이름과의 부분 문자열 충돌 검사는 §4.1-4 「충돌 중단」과 같은 검사다.
3. 1회차가 무거운 등급을 포함해 돌았다: 벤치가 E2 첫 회차를 알 방법은 없으므로 코드로 강제하지 않고 키트 문구만 강조한다(G-6).

### 4.5 W5 — 외부망 검증 · 키트 갱신

- §4.3-6 재판정 동치 검증.
- E1 전체(가짜 LLM · `--skip-heavy --probe`) 1회 → 관문 통과 · `summary_safe.json` 생성 · 콘솔 요약 줄이 안전 요약과 일치.
- **관문 실패 강제 E1**: 카나리아를 응답에 주입하는 테스트 픽스처로 관문을 일부러 실패시켜 `leak_check.json` + `summary_safe.json`만 남고 `<out>.quarantine/`이 권한 700/600으로 `<out>` 밖에 생기는지, `--regate`가 동작하는지, `<out>` 하위 경로 지정이 거부되는지 확인.
- 157 W2(E1 활성 DB 주입)가 끝나 있으면 같은 E1에서 A-4(`resolve_miss`)가 `gw_error`에서 빠지는지 확인한다(없으면 단위 테스트로만).
- 152 부록 A 갱신: A.0(비root 전용 계정 — G-3) · A.1(자리표 실재·소스 대응 범위 검사) · A.6(스모크 → 전체 순서 · `--skip-heavy` 강조) · A.7(반출 목록에 `summary_safe.json` · 관문 실패 시 3종 · `<out>.quarantine/` 반출 금지 · 해시 매니페스트 · 실행 로그는 화이트리스트 직렬화본만) · §10.7(이번 3증상 행 갱신).
- MLX: **실행하지 않는다** — 프롬프트·선택 경로를 바꾸지 않는다(D-240 「바뀐 프롬프트·선택 경로만」).

### 4.6 W6 — 폐쇄망 재실행 (사용자 실행)

§5 절차.

---

## 5. 폐쇄망 재실행 절차와 반출 조건

> **내부망 실행 안내 한 장**(상세는 아래 각 단계)
> | 항목 | 내용 |
> |---|---|
> | 실행 위치 | 이 계획을 반영한 SHA 체크아웃 루트 · `config/`·`.env`·`apm_gateway/.env`는 폐쇄망 정본 유지 |
> | 계정·호스트 | 비root 전용 계정(3자 이상 무의미 이름 · E2 root면 exit 2) · 전용 실행 호스트 권장(짧은 호스트명이 흔한 낱말이면 멈출 수 있다) |
> | 로컬 파일 | `~/apm_bench_local/placeholders.e2.yaml` · `term_policy.local.yaml` · `bench.local.yaml`(`view_server_version: <운영자 확인 값>`) — `chmod 700` 디렉터리 · `600` 파일 · 저장소 밖 · 반출 금지. E2가 자동으로 읽는다 |
> | 0. 1회차 진단표 | `python -m scripts.apm_bench --audit-digest 20261013_1827` → `results/apm_bench/20261013_1827_audit/audit_digest.json`을 눈으로 확인 후 그 파일 1개 반출 |
> | 1. 스모크 | `python -m scripts.apm_bench --run --env e2 --smoke` — 관문 통과 확인 전에는 전체를 돌리지 않는다 |
> | 2. 전체 | `python -m scripts.apm_bench --run --env e2 --bg` → 진행 확인 `python -m scripts.apm_bench --status` |
> | 산출 위치 | `results/apm_bench/<run_id>/`(run_id·파일명·실행 로그 `run.log`·PID 자동) |
> | 반출 | **`manifest.sha256`에 적힌 파일만**(관문 통과 = 6종 + `summary_safe.json` + `run.log` + `run_meta.json` + 매니페스트) — run 디렉터리 통째 복사·압축 금지 · `scenarios.yaml` 사본은 매니페스트 밖 |
> | 관문 실패 시 | 실패 목록만 반출 · 격리본 `~/apm_bench_quarantine/<run_id>/`(반출 금지 · 14일) · 교정본 반입 뒤 `--regate <run_id>` · 끝나면 `--purge <run_id>` |

**W0a — 1회차 감사 로그 진단표(재실행 전 · 실행 수 분 · LLM·제니퍼 호출 0)**
1. **실행**(1회차를 돌린 체크아웃 루트에서 — 감사 로그는 그 체크아웃의 `logs/`):
```bash
python -m scripts.apm_bench --audit-digest 20261013_1827
```
   1회차는 run 메타가 없어 벤치가 나머지를 자동으로 채우고 「자동: …」 줄로 출처만 찍는다 — 시작 = run id 시각(실행 머신 로컬) · 끝 = 실행 로그 `run_20261013_1827.log`의 「소요 N초」(run 디렉터리 · 현재 디렉터리 · `~/apm_bench_out` 순으로 찾음 — 없으면 `--duration 8102.7`) · 창 = 앞뒤 5분 여유 · 감사 로그 = 창이 걸친 로컬 날짜의 `logs/audit-<날짜>*.jsonl`(자정 넘김 · 로테이션 `audit-YYYY-MM-DD.N.jsonl` 포함) · `--env e2`. 출력은 `results/apm_bench/20261013_1827_audit/audit_digest.json`이고 머리에 `window_estimated: true`가 붙는다. 1회차는 `--skip-heavy`를 쓰지 않았으므로 붙이지 않는다(구 방식 run에는 자동으로 붙지 않는다).
2. **시나리오 파일**: 1회차 SHA를 모르므로 HEAD 시나리오를 쓰고 `[경고] run SHA 미상 — HEAD 시나리오 사용`과 id 변경 가능성(205d904 `APM-SRC-common` → `APM-SRC-cm-prd`) 경고를 낸다(exit 0). 1회차 SHA를 알면 `git show <1회차 SHA>:testdata/apm_bench/scenarios.yaml > ~/apm_bench_local/scenarios.r1.yaml`로 사본을 만들어 `--scenarios`로 준다. 머리의 「일반 113 · 무거운 5 · 건너뜀 3시나리오(e2_skip 4턴)」가 실행 로그와 같은지 본다.
3. **명시 인자 우선**: 자동 창이 맞지 않으면 `--since`·`--until`(ISO-8601 · 오프셋 필수 · 예 `2026-10-13T18:20:00+09:00`)·`--scenarios`·`--env`를 직접 준다 — 늘 자동값보다 우선한다. since > until이면 exit 2.
4. **혼입 확인**: 그날 같은 체크아웃에서 운영 질의·pytest·다른 벤치가 돌았는지 확인한다. 진단표 머리 `input` 블록의 「다른 사용자 줄」·`unmatched` 스레드 수가 0이 아니면 그 수를 함께 알린다(값은 나오지 않는다). **벤치 본체는 인증을 끈 채 떠서 그 시간에 누가 그 본체 포트로 웹 질의를 했다면 같은 `anonymous`로 남아 걸러지지 않는다** — 벤치 실행 중 그 포트를 다른 사람이 쓰지 않았는지(벤치 본체는 127.0.0.1 임시 포트라 원격 접속은 불가하나 같은 머신 사용자는 가능) 확인한다.
5. **exit 1**이면 자기 점검이 어휘 밖 문자열을 막은 것이다 — 파일이 없으니 반출하지 말고 콘솔 메시지(값 없음)만 알린다.
6. **사람 확인 후 반출**: `audit_digest.json`을 열어 운영 서버·인스턴스·업무 이름, 사용자 id, IP·URL이 **한 글자도 없는지** 눈으로 확인한다(`grep -nE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+|http|@' …`도 0건). 확인되면 **그 파일 1개**와 실행 콘솔 출력을 반출한다(152 A.7 「감사 로그 진단표」 행). 본체 `logs/` 원본은 옮기지 않고 반출하지 않는다.
   - 2회차부터(run 디렉터리가 있는 run)는 `python -m scripts.apm_bench --audit-digest [<run_id>]` — 생략 = 최근 run · 창 = `run_meta.json` 시작·끝 ±5분 · 시나리오 = run 사본 · env·skip-heavy = run 메타 · 출력 = `results/apm_bench/<run_id>/audit_digest.json`.
7. 외부망은 받은 진단표로 §2 가설 표(§4.0)를 갱신하고, W1~W4 우선순위·스모크 목록(§5 1단계)을 다시 정한다.

**사전**
1. 같은 SHA 체크아웃(이 계획 반영) · `config/`·`.env`·`apm_gateway/.env`는 폐쇄망 정본 유지(docs/18 2026-10-13 「반입 목록에 `config/db_registry.yaml`」 방지책).
2. 실행 계정: 비root 전용 계정(G-3 — 3자 이상 무의미 이름 · 홈 경로에 운영 서버 이름 없음 · E2 root면 exit 2) · 전용 실행 호스트 권장(짧은 호스트명이 흔한 낱말이면 사용자 정보 충돌로 멈출 수 있다) · 로컬 파일 3개 `~/apm_bench_local/placeholders.e2.yaml`·`term_policy.local.yaml`·`bench.local.yaml`(`view_server_version: <운영자 확인 값>` 한 줄 — 도구로 얻을 수 없다)을 두고 `chmod 700 ~/apm_bench_local; chmod 600 ~/apm_bench_local/*`(152 A.0). 산출물은 `results/apm_bench/<run_id>/`에 자동으로 모인다(`.gitignore` 대상).
3. `--dry-run` exit 0 · A.1 채움 검사 exit 0 · 로컬 자리표의 `sources.by_placeholder`에 대상 자리표(`inst_*`·`host_*`·`biz_*`·`svc_*`)의 실제 소스를 채운다(T-1 — 빠지면 실행 전 「`by_placeholder` 미대응」 경고).
4. `apm_gateway/var`는 벤치가 run 전후 비교로 알려 준다 — 새로 생기거나 바뀐 파일이 있으면 `[경고]`, 이전부터 있던 것만이면 `[정보]` 줄이다. 경고가 나면 내용을 보고 판단한다.

**1단계 — 스모크(약 10~20분 · 추정)**
```bash
python -m scripts.apm_bench --run --env e2 --smoke
```
(내장 10개: `APM-BIZTREND-interval` `APM-PROC-pid-host` `APM-JSERVER-dbpath` `APM-JSERVER-dbpath-host` `APM-SRC-legacy` `APM-SRC-none-all` `APM-INST-host` `APM-HEALTH-inst` `APM-BIZ-name` `APM-SERVICE-name` — 앞 6개 = 이번 위반 턴 · 뒤 4개 = 대상 종류별 대표. run_id 끝에 `-smoke`. E2 기본으로 로컬 파일 3개 자동 · `--skip-heavy`·`--probe` 켬. `--smoke`는 `--only`처럼 record 번호가 바뀌므로 번호 대조가 아니라 시나리오 단위로 본다.)
- 실행 전 점검이 경고·exit 2를 내면 고친 뒤 진행한다: root(G-3) · 사용자 정보 충돌(§4.1-4) · 키트 검사(§4.2 (d) — 자리표마다 「있음·없음·여럿·검사 불가」 · 값 미출력 · 「없음」 자리표를 쓴 일반 턴은 판정 불가로 분모 제외 · 「여럿」은 경고만 · 인벤토리 도구 실패 종류는 「검사 불가」로 분리 안 함) · `by_placeholder` 미대응 · `apm_gateway/var` 기준.
- 통과 조건: `leak_check.json` `passed: true` · 6종 + `summary_safe.json` 생성 · 중단 0. **실패하면 전체를 돌리지 않는다** — 아래 「관문 실패」 목록만 반출한다. 치환 전 재료는 `~/apm_bench_quarantine/<run_id>/`에 격리되므로(반출 금지 · 최대 14일), 외부망 교정본을 반입한 체크아웃에서 `python -m scripts.apm_bench --regate <run_id>`로 실행 없이 내부망에서 확인한다 · 지우기는 `--purge <run_id>`.

**2단계 — 전체 1회**:
```bash
python -m scripts.apm_bench --run --env e2 --bg
python -m scripts.apm_bench --status          # 생략 = 최근 run · 상태·PID·경과·run.log 마지막 줄
```
분리 실행이라 `nohup`·리다이렉트가 필요 없다(콘솔 = `run.log`). `--skip-heavy`는 E2 기본으로 켜진 채 유지한다(1회차가 무거운 등급을 이미 정상 통과했지만, 근거 반출이 처음이므로 변수를 줄인다 — G-6). `--status`가 「프로세스 없음 — 크래시 추정」이면 `--bg` 없이 다시 돌려 traceback을 확인한다.

**반출 조건**(`results/apm_bench/<run_id>/`에서)
- 관문 통과: 6종 + `summary_safe.json` + `run.log` + `run_meta.json` + `manifest.sha256`.
- 관문 실패: `leak_check.json` + `summary_safe.json`(그 파일이 관문을 통과해 존재할 때만) + `run.log` + `run_meta.json` + `manifest.sha256`.
- 감사 로그 진단표 `audit_digest.json`(W0a — 눈 확인 후).
- 반출은 선별 목록 + `manifest.sha256`으로만 한다(디렉터리 통째 복사·압축 금지).
- 반출 금지(현행 A.7 + 추가): `~/apm_bench_quarantine/` · `~/apm_bench_local/` · `.env` 류 · `logs/` · 세션 임시 파일 · run 디렉터리의 `scenarios.yaml` 사본(매니페스트 밖) · 콘솔을 따로 리다이렉트한 파일(강제 종료 시 재검사 안 됨 — `run.log`를 쓴다).

---

## 6. 작업 분해

| Wave | 내용 | 완료 기준 |
|---|---|---|
| **W0** 실측 재대조 · 기준 확보 · 확인 요청 | §2 `file:line` 재대조 · 시작 SHA로 E1 `--debug-raw` 1회(재판정 기준 `records.jsonl` — §4.3-6) · `failures` 맞추기 가능성 실측(§4.3-3 — 본체 변경 필요 여부 → G-5) · target 하위 사유·건너뜀 사유 실제 값 전수(§4.2(a) enum·정규식 확정) · `reselect.output`·`primary_categories` 소비처 grep · 사용자 확인 요청(G-8) | §11 진행 기록 · 게이트 확정 반영 |
| **W0a** 감사 로그 진단표(사용자 지시 2026-10-14) | §4.0 — `scripts/apm_bench/audit_digest.py` · `--audit-digest` 모드 · 화이트리스트 직렬화·자기 점검 · 순서 보존 정렬(`exact`/`ambiguous`/`missing`) · 집계 ①~⑥ · 테스트 `tests/test_scripts/test_apm_bench_audit_digest.py` · §5 W0a 절차 · 152 A.7 1행 | 자기 테스트 통과 · `regress.py --no-tests` 통과 · 외부망 E1 정답 대조에서 **틀린 `exact` 0** · verifier 누출 Critical 0 · 제품 코드 변경 0 |
| **W1** 관문 위반 교정 | §4.1 1~5 | 자기 테스트 통과 · `regress.py --no-tests` 통과 · 관문 규칙 코드 diff 0(`scripts/itam_bench/redact.py` 무변경) |
| **W2** 진단 가능 반출 | §4.2 (a) 안전 요약 · 콘솔 직렬화 단일화 · (b) 보관·재관문(G-2 승인 시) · (d) 실행 전 키트 검사 · 테스트(어휘 밖 문자열 버림 · `locus` 교집합 필터(`args.{{host_1}}`·`F1:evil-host:args.x` 입력) · 관문 실패 run에서도 생성 · 이중 관문 · `<out>` 하위 보관 거부) | 자기 테스트 통과 · `regress.py --no-tests` 통과 · 관문 실패 픽스처에서 지정 파일만 기록 |
| **W3** 판정 집계 교정 | §4.3 1~6 | §4.3-6 재판정 턴별 `turn_pass`·축 동일 · 분류 집계 변화만 · 자기 테스트 통과 · `regress.py --no-tests` 통과 |
| **W4** 실행 위생 | §4.4 1~2 | 사전 존재 `var/`에서 경고 없음 · 새 파일이 생기면 경고 · root 실행 exit 2(G-3 (a)) · `regress.py --no-tests` 통과 |
| **W5** 외부망 검증 · 키트 · 기록 | §4.5 · 152 부록 A·§10.7 갱신 · `docs/18` 항목(관문 키 이름 변경 · 관문/치환기 비대칭 · `SKIP_KEYS` 이름 면제 · 존재 기반 경고) · D-323 부기(G 확정분) · **계획 끝 `regress.py --base <계획 시작 SHA> --files <회귀 대상 파일>` 1회**(D-303 — 도구 마지막 범위 줄을 보고에 옮긴다) | E1 관문 통과 · 관문 실패 강제 E1 통과 · 회귀 신규 실패 0 |
| **W6** 폐쇄망 재실행(사용자) | §5 스모크 → 전체 | 반출 6종 + 안전 요약 · 받은 뒤 round1 계획(152 A.8) 개시 |

**착수 순서(157과 파일 겹침)**: 157은 `driver.py`(파싱 답)·`judge.py`(필요 시)를, 164는 `driver.py`(`build_observed`·키트 검사)·`judge.py`(집계)를 고친다. 권고: **157 W1(드라이버) → 164 W1~W4 → 157 W2~W4** 또는 두 계획을 같은 세션 순서로 직렬화한다. 병렬이면 `driver.py`·`judge.py`는 한쪽만 편집하고 다른 쪽은 그 커밋 뒤에 시작한다. 164 W6(폐쇄망 재실행)은 157 교정이 들어간 SHA로 돌리는 편이 재실행 1회로 둘을 함께 재는 데 유리하다(사용자 판단).

---

## 7. 수용 기준

1. 이번 위반 6건과 같은 모양(보기 id 낱말 ∩ 인벤토리 토큰 · 사용자 정보 부분 문자열 응답)의 E1 픽스처가 관문을 통과하고, 실 원이름·카나리아·JWT 주입 · 호스트 이름 충돌 · `parsed_views` 원이름 주입 음성 사례는 여전히 위반이거나 exit 2다.
2. 관문이 실패한 run에서도 `summary_safe.json`이 남고(그 파일이 관문을 통과한 경우), 그 파일의 모든 문자열 잎이 화이트리스트 어휘이며, 실행 로그 요약 줄은 그 직렬화본에서만 나온다.
3. 시작 SHA E1 `records.jsonl` 재판정: 턴별 `turn_pass`·축이 시작 SHA 판정기와 같다 · `primary_failure_categories`가 추가된다 · 판별 가능한 해석 단계 `instance_unresolved`가 `gw_error`로 세지지 않는다.
4. 사전 존재 `apm_gateway/var`에서 경고가 나지 않고, 이 run이 새로 쓴 파일이 있으면 경고한다.
5. 폐쇄망 재실행이 반출본(관문 통과 6종 또는 최소 안전 요약)을 낸다 — **근거 0으로 끝나는 run이 다시 나오지 않는다**.
6. 계획 끝 `regress.py --base <계획 시작 SHA> --files <회귀 대상 파일>` 종료 코드 0(원래 실패만) · arch_check error 0 · overfit_check 통과.
7. (W0a) `audit_digest.json`의 모든 문자열 잎·키가 화이트리스트 어휘이고(자기 점검 · 자유 문장 사유의 호스트 이름·`targets`·`commands`·`detail`·`user_id`·`thread_id`·시각 원값 미출력), 외부망 E1 정답 대조에서 틀린 `exact` 대응이 0이다.

---

## 8. 사용자 확정 게이트 (권고 = 첫 항)

> **확정 2026-10-14 · 사용자 *권고안 일괄 채택*** — G-1 (a)(부속: 인스턴스 수·소스별 호출 수 그대로) · G-2 (a) **단 보관 위치는 `<out>.quarantine/`이 아니라 저장소 밖 고정 경로**(사용자 확정 — 산출 기본 위치가 저장소 안 `results/apm_bench/<run_id>/`로 정리되므로 · 저장소·run 디렉터리 하위 거부 유지) · G-3 (a) · G-4 (a) · G-5 (a) · G-6 (a) · G-7 (a) D-323 부기. **G-8**: 1회차 SHA는 **미상으로 확정**(사용자 *"1회차 SHA는 모른다. 경고 내고 HEAD로 진행하라."*) — 나머지 항목(`--probe`·`--term-policy` 사용 · 실행 계정 · `ACTIVE_DB_IDS` · `by_placeholder` 범위 · 실행 호스트 감시 대상 여부)은 열린 질문으로 남고 구현을 막지 않는다.

| # | 질문 | **권고** | 대안 |
|---|---|---|---|
| **G-1** | 관문 실패 시 무엇을 반출할 수 있게 할까? | **(a) 화이트리스트 안전 요약 `summary_safe.json`** — 생성 시 어휘 강제 + 관문 전 규칙 재검사 · 관문 성패와 무관하게 기록 시도 · 실행 로그 요약은 그 직렬화본만. 부속 질문: 인스턴스 수·소스별 호출 수를 그대로 둘까, 구간화할까(권고: 그대로 — 이미 콘솔 로그로 반출되는 수준) | (b) 위반 레코드 제외 축약본(실패 턴 편향 · 같은 레코드 다른 칸 누출 위험 — 비권고) · (c) 현행 유지(근거 0 재발) |
| **G-2** | 관문 실패 run의 치환 전 재료를 실행 머신에 보관하고 `--regate`로 재관문할까? (**현행 E2 「원본 디스크 미기록」 규칙 개정**) | **(a) 허용** — `<out>` 밖 `<out>.quarantine/` · 700/600 · 저장소·`<out>` 하위 거부 · 최대 14일 · 반출 금지 · 해시 매니페스트 반출(D-219 ② 선례) | (b) 보관 금지(위생 코드 결함마다 2시간+ 재실행) |
| **G-3** | 벤치 실행 계정 | **(a) 비root 전용 계정(3자 이상 무의미 이름) · E2에서 root면 exit 2** | (b) root면 경고만 · (c) 제한 없음(이번 `user_info` 재발 가능) |
| **G-4** | 자리표 실재 검사 결과 처리 | **(a) 「없음」만 「판정 불가」로 분리(현행 `placeholder_missing`과 같은 결 · `judge.py:1086-1090`) · 「여럿」은 경고만 하고 분모에 남긴다**(후보 다수 실패 T-3이 정확도에서 사라지지 않게 — D-323 ① 분모 불변) | (b) 「없음」이면 exit 2(자리표를 고칠 때까지 실행 불가) |
| **G-5** | 해석·보기 호출 구분을 `failures` 맞추기로 못 하면? | **(a) 모호하면 보수적으로 `gw_error` 유지 — 본체 변경 0** | (b) 본체 emit 1지점 추가(호출 단계 표지 — D-323 ⑦ 「최대 3지점」 초과 → 부기) |
| **G-6** | 재실행 범위 | **(a) 스모크(`--only` 약 10시나리오) → 통과 시 전체 `--skip-heavy` 1회** | (b) 바로 전체 · (c) 전체 + 무거운 등급 |
| **G-7** | 대표 분류 새 키·`resolve_miss`·안전 요약을 D-323에 어떻게 남길까? | **(a) D-323 부기**(턴 통과 5축·분모 불변 · 집계·표기·반출 범위만 변경) | (b) 신규 D-번호 |
| **G-8** | 폐쇄망 1회차 실행 조건 확인 | 사용자 확인: 체크아웃 SHA·작업 트리 변경 · `--probe`·`--term-policy` 사용 여부(P층 0회의 원인) · 실행 계정 · 본체 `ACTIVE_DB_IDS` · 로컬 자리표 `sources.by_placeholder` 범위 · 벤치 실행 호스트가 제니퍼 감시 대상인지 · 157 교정을 재실행 SHA에 넣을지 | — |

결정 등재: G-1·G-2·G-3·G-7은 반출·위생·집계 계약(D-323 ④⑤ · D-219 ②)을 넓히거나 바꾸므로 **확정 뒤 D-323 부기**로 남긴다. 신규 D가 필요하면 그때 색인 최댓값+1 — 등재 직전 이 체크아웃과 형제 체크아웃 양쪽 `ls docs/decisions/ | tail -3`(형제에 미커밋 D-330~D-332).

---

## 9. 위험

- **안전 요약이 관문에 막힘**: 토큰 대조를 적용하면 시나리오 id·보기 id 낱말이 운영 이름 토큰과 겹쳐 안전 요약도 막힐 수 있다 — 그 경우에도 「막힘」이 맞는 동작이다(이중 안전). 막힘이 잦으면 안전 요약은 원이름·카나리아·정규식·사용자 정보만 적용하고 토큰 대조는 생략하는 안(`.md`와 같은 처리 — 152 §3.8 구현 부기)을 검토하되 **사용자 승인 사안**으로 올린다(1차 근거는 화이트리스트).
- **사용자 정보 대칭 치환의 과치환**: `root` 같은 짧은 값이 무관한 낱말을 깬다 → 진단 손실. 근본은 G-3.
- **`quarantine/` 평문 원본 잔존**: 보존 기간(최대 14일) 동안 운영 이름 원문이 실행 머신에 있다. 위치(`<out>` 밖)·권한·자동 삭제 안내로 줄이되 실수 반출 위험은 0이 아니다 — G-2 (b)를 고르면 이 위험은 없고 재실행 비용을 진다.
- **`SKIP_KEYS` 이름 기반 면제**: 키 이름만으로 치환·관문을 건너뛰는 구조 자체가 위험이다(§2.6). 이 계획은 보기 id 칸에 값 검증을 붙이고 전수 점검만 한다 — 면제 구조 전면 재설계는 하지 않는다. 다른 칸에서 같은 유형이 나오면 재상정.
- **집계 변경을 판정 완화로 오해**: W3는 `turn_pass`·분모를 바꾸지 않는다 — 수용 3의 재판정 검증으로 고정한다.
- **E1에서 A-4 재현 불가**: 활성 DB 0 E1은 E6 간선이 없다. 157 W2 이전이면 단위 테스트로만 확인되고 실증은 폐쇄망 재실행에서 한다.
- **토큰 보호 낱말(W1 4라운드)**: 원이름 토큰이라도 코드·안전 요약 어휘 낱말(`was`·`host`·`app`·`dev` 등)과 같으면 치환·대조하지 않는다(원이름 전체 문자열은 계속 치환·대조) · 구분자 변형(`z_bench`)은 막지 못한다 · 레지스트리 밖 소스 **라벨**이 자유 문장에 섞이면 막지 못한다(게이트웨이 `.env`에는 id만 있다) · NFKC 때문에 반출본의 전각·호환 문자 모양이 바뀐다.
- **닫힌 쪽 선택의 비용(W1 3라운드 · 수용)**: N-2 사용자 정보·코드 어휘 충돌이 E2를 넓게 멈춘다 · 판정 상세에서 레지스트리 밖 소스 id가 빠진다.
- **재실행 비용**: 전체 약 2.25시간(추정) · 업무 외 시간 · 운영 협의. 스모크로 관문을 먼저 확인해 헛도는 비용을 줄인다.

---

## 10. 의사결정 정합

- 유지: D-323 ①(5축 · 턴 통과 정의 · 분모) · ③(허용 범위 — 재실행도 같은 가드) · ④(근거 묶음 — 관문 통과 시에만 반출) · ⑤(위생 · 관문 규칙 완화 없음) · ⑦(본체 emit 최대 3지점 — G-5 (a)면 변경 0) · D-321(형식 보존 치환) · D-301 ④(사용자 정보) · D-219 ②③ · D-274(벤치는 게이트웨이 import 0 — 감사 줄·도구 응답만 읽고, 오류 코드·도구 이름은 벤치 쪽 상수 사본) · D-003(읽기 전용 — 제니퍼 쓰기 경로 없음 · `--regate`는 파일 읽기·치환만) · D-303(회귀는 계획 끝 1회) · D-127·D-240(실 LLM 미사용 · MLX 미실행).
- **개정(부기 필수)**: G-2 — 현행 「E2는 원본을 디스크에 쓰지 않는다」(`__main__.py:412-414` · 152 §10.7)를 관문 실패 시에 한해 바꾼다(D-219 ② 선례).
- 확장(부기 대상): D-323 ④⑤ — 안전 요약·실행 로그 직렬화(G-1) · 실행 계정(G-3) · 대표 분류 새 키·`resolve_miss`(G-7).
- 충돌 가능: G-4 (b)·G-5 (b) 선택 시 각각 실행 차단 규칙 추가·D-323 ⑦ 개정. G-4에서 「여럿」을 분모에서 빼는 안은 D-323 ① 분모 변경이라 채택하지 않았다.

## 11. 진행 기록

- 2026-10-14 — v1.0 작성(코드 0 · 실 LLM 0 · 벤치 실행 0). 근거: 폐쇄망 run `20261013_1827` 실행 로그 + `leak_check.json`(반출 2종). 코드 실측: 판정 집계(A-1~A-4) · 관문 키 이름 변경(`hygiene.py:884`) · 관문/치환기 사용자 정보 비대칭(`__main__.py:221-226` · `hygiene.py:503`) · `var/` 존재 기반 경고(`gateway.py:507-509`) · 스풀 주입 정상(`gateway.py:359-368` · `apm_gateway/config.py:257-259·298-299`).
- 2026-10-14 — v1.1 독립 검토 반영. **verifier**: High — 오류 감사 줄에 `search=` 없음 → 단계 구분을 `failures` 맞추기로 재설계(§4.3-3 · G-5 전제 정정) · A-4 근거에서 보기 호출 원장(`:3415-3418`) 제외 · 기준 run 부재 → 시작 SHA E1 `--debug-raw` 오프라인 재판정(§4.3-6) · G-2는 E2 원본 미기록 규칙 개정(§10). Medium — A-3 하한 15 → 9 · `source_select` 0 과신 정정 · G-4 「여럿」 분모 유지 · 전수 점검 대상(`calls[].error`·`reselect.candidates` — `target.candidates` 주의) · 안전 요약 토큰 대조 적용 명시 · `gw_error` 후순위는 해석 단계만 · T-1 키트 검사 추가. Low — §1.4 확정 · `pii_value` 원이름 일치 배제(상) · `--turns`는 replay 전용 · `var/` 파일 목록 비교 · 로그인 id 후보 · 형제 D-330~332 · W2~W4 `--no-tests` · 157 착수 순서 · `primary_categories` 새 키. **security-auditor**: High — `locus`의 보기·인자는 레지스트리 교집합만 · 건너뜀 사유 정규식 화이트리스트 · 문자열 잎 enum 강제·`reason` 등 경로째 금지. Medium — 숫자 식별성(G-1 부속 질문) · 사용자 정보 치환은 맨 마지막 + 충돌 시 exit 2 · `quarantine`은 `<out>` 밖·14일·해시 매니페스트 · 콘솔 로그는 안전 요약 직렬화본만 · `parsed_views` LLM 원출력 면제 구멍(기존 결함 — W1). Low — 전용 계정 이름 규칙 · 게이트웨이 상수는 벤치 쪽 사본(D-274). Critical 0.

- 2026-10-14 — **W0a 완료(v1.2)** — 사용자 *"방안 1로 진행해서 164에 W0a 추가하라"*. 구현: `scripts/apm_bench/audit_digest.py`(신규) · `__main__.py` 모드 `--audit-digest`·`--since`·`--until`·`--bench-user` · 테스트 `test_apm_bench_audit_digest.py`(신규) · `test_apm_bench_audit_digest_verify.py`(verifier 신규 · strict xfail 1 = 2칸 밀림 한계) · `test_apm_bench_cli.py` 모드 집합 1줄. 제품 코드 변경 0 · LLM·제니퍼 호출 0.
  - **외부망 정답 대조**(가짜 LLM E1 run 2개 — 형제 체크아웃 `collectorinfra_codex` `logs/audit-2026-10-1{2,3}.jsonl` + 그 run의 `--debug-raw` `records.jsonl`의 thread_id → 시나리오 정답 · 이 체크아웃 `logs/`는 pytest 줄뿐이라 E1 벤치 구간이 없었다): A(10-13 10:02~10:12 KST · 기대 121 · 스레드 117 · 혼입 6) exact 82 · ambiguous 35 · missing 1 · **틀린 exact 0** / B(10-12 17:34~17:39 KST · 스레드 109) exact 86 · ambiguous 23 · missing 1 · **틀린 exact 0**. 창을 ±수 분·1시간·하루로 넓힌 시험도 틀린 exact 0. pytest 줄만 있는 창은 전부 `other_user`로 빠져 대응 0. 시나리오 파일은 run 당시 사본(`d9a1ecc`)이 필요했다(`205d904`에서 id 1건 변경).
  - **verifier**(누출·정확성 한 라운드): 누출 Critical 0(전 칸 적대값·실데이터 금지 칸 문자열 7,591개 교차 대조 0건·콘솔 포함). High 1 = 멀티턴 1턴이 done 없이 끝나 스레드가 갈리면 틀린 exact → 「갈린 스레드 강등」 추가 · Medium 3 = 2칸 밀림 오보기(한계로 명기 · xfail 고정) · 무이벤트 지표 과소(`silent_lower_bound`·`silent_candidates` 추가) · 대상 해석 실패 자유 문장 버림(고정 문형 fullmatch → `target_text_unresolved`·`target_name_too_long`) · Low 2 = 이상한 줄 예외(줄 단위 `broken`) · 인증 끈 본체의 다른 `anonymous` 질의(§5 W0a 3단계에 명기). 전부 반영.
  - 정적 게이트: `regress.py --base 4073f7f --no-tests --files <6개>` arch_check·overfit_check 통과 · ruff 이번 diff 위반 0 · mypy 대상 없음 · `범위: 정적 게이트만 — 테스트 미실행`. 자기 테스트 45 passed · 1 xfailed. 모듈 단위 회귀는 계획 끝(W5) 1회.

- 2026-10-14 — **통합 진행**(사용자 *"apm_bench와 관련된 계획 152, 156, 157, 164 등의 계획을 종합적으로 판단하여 구현하라. 또한 apm_bench 로그 파일은 results/apm_bench 폴더에 모아서 정리하도록 구현하라."* + *"벤치마크 테스트 실행 스크립트 명령어는 최대한 간소화 시켜라. 파일명이나 시간 등은 내부 코드에서 자동으로 처리되도록 해야 한다."* · 통합 team-lead). **순서 근거**: §6 착수 순서 권고대로 157 W1·W2(`driver.py` 파싱 답·E1 주입값)를 먼저 끝내고 164 W1 ∥ W3 → W2a(안전 요약·격리·실행 디렉터리) → W2b(명령 간소화) → 키트(d)+W4 → W5. 157 W4·156 E1 재측정·164 W5는 최종 E1 1회로 합쳤다. 이탈: 키트(d)+W4를 한 implementer에 묶음(같은 `driver.py`·`gateway.py` 기동부 — 되돌리는 비용 없음).
- 2026-10-14 — **W1**(관문 위반 교정 · 4라운드): 근거 묶음 키 `result_views` · `SKIP_KEYS` 보기 id 칸 레지스트리 교집합 · 사용자 정보 대칭 치환(맨 마지막) · 충돌 exit 2 · `parsed_views` 원출력 구멍 차단 · `test_apm_bench_leak_e2.py`. 이탈(메인 수용): ① `SKIP_KEYS` 그 밖 칸은 「코드 어휘 정규식」 대신 **닫힌 어휘**로 대조(정규식은 운영 이름 소문자형도 통과시킨다) ② `ARG_KEYS`도 보기 id 레지스트리 대조. E1 충돌은 경고만(메인 수용). 4라운드(메인 직접 지시): 원이름 토큰 중 코드·안전 요약 어휘 낱말은 토큰 사전에서 제외(레지스트리 밖 소스 id `zetaledger-prd`의 `prd`가 `cm_prd`를 깨던 거짓 양성 · 치환기·관문 대칭 · 원이름 전체 대조 유지 · `redact.py` diff 0) · N-4 — 자유 문장 치환 전 NFKC + 제로폭 제거(관문 불변).
- 2026-10-14 — **W3**(판정 집계): 대표 분류 `primary_failure_categories`(실패 일반 턴만) · `resolve_miss` 분리 · target 하위 사유 · 호출 단계(`view`/`resolve`/`unknown`) — 시작 SHA E1 `--debug-raw` 재판정 135/135 `turn_pass`·축 동치.
- 2026-10-14 — **W2a**(진단 가능 반출 · G-1·G-2): `summary_safe.json`(닫힌 어휘 + 관문 2차) · 실행 로그 `run.log` = 화이트리스트 직렬화본만(관문 전 원문 미기록) · 관문 실패 시 격리본 `~/apm_bench_quarantine/<run_id>/`(저장소·`results/` 밖 · 700/600 · 14일) · `--regate <run_id>` · `--purge` · 실행 디렉터리 `results/apm_bench/<run_id>/`(산출물·`run.log`·`run.pid`). verifier V-1~V-5 · 감사 M-1·M-2·L-1~L-3 교정(Critical 0).
- 2026-10-14 — **W2b**(명령 간소화): E2 한 줄(`~/apm_bench_local/` 자동 탐색 · `--skip-heavy`·`--probe` 기본 on · `--no-…`로 끔) · `--smoke` 내장 목록 · run_id·파일명 자동 · `run_meta.json` 자동 · `--audit-digest [<run_id>]`(±5분 창 · 메타 없는 1회차 `20261013_1827`은 「소요 N초」로 창 추정 + `window_estimated` · 「run SHA 미상 — HEAD 시나리오 사용」·`APM-SRC-common`→`APM-SRC-cm-prd` 경고 · exit 0) · `--compare`·`--replay`·`--regate`는 run_id만 · `--bg`/`--status`. 감사 H1·M1·M2·L1·L3 · verifier V-2~V-6 교정(Critical 0). 교정 2라운드(메인 직접 지시): `--bg` 자식 env = 부모 env 그대로에서 비밀번호·내부 인자만 제외(셸 export 설정이 `--bg`로 달라져 과금 평면이 바뀌는 위험) · `--compare`/`--replay` 「산출물」 줄 `display()` · `--status` 문형 확장(키트 검사·`[경고]`·끝 요약).
- 2026-10-14 — **키트(d)+W4**: `kit.py` — 자리표 실재 있음·없음(「판정 불가」 — G-4 (a))·여럿(경고)·검사 불가(인벤토리 도구 실패)·검사 제외 · `by_placeholder` 미대응 경고 · `apm_gateway/var` 전후 비교(사전 존재는 경고 없음 · 새 파일만 경고) · E2 root exit 2(G-3) · `run_meta.kit` · `summary_safe.run.gateway_var`.
- 2026-10-14 — **W5**(외부망 검증): E1 전체 run `20261014-161040-e1-4073f7f` 관문 통과 129/129 · 관문 실패 강제 — 단위(`-k leak/regate/quarantine/gate` 18 passed) + 실제 기본 경로 e2e(실행 계정 이름을 응답에 흘린 가짜 드라이버 → 관문 실패 `user_info` 1 · 반출 4종 + `manifest.sha256` · run 디렉터리에 사용자명 없음 · 격리본 700/600 · `--regate <run_id>` 재현 · `--purge` 1건 삭제) · 자리표에 카나리아를 넣는 방식은 치환기가 먼저 바꿔 관문 실패를 만들지 못했다(설계상 이중 방어) · `--audit-digest`(인자 없음) exit 0 · 임시 HOME에서 `--run --env e2` → 안내 3줄 exit 2. 문서: 152 §10·부록 A(v1.4) · 이 계획 §5 · D-323 부기 · `docs/18` 4항목. 1회차 진단표 출력 위치(폐쇄망 실행 시): `results/apm_bench/20261013_1827_audit/audit_digest.json`.
- 2026-10-14 — **사용자 지시**(「내부망에서 벤치마크를 돌릴 예정이다. 단위 테스트 위주로 진행하고 구현하라. 시간이 너무 오래 진행되고 있다.」): 이후 벤치·MLX run과 추가 검증 라운드는 하지 않았다. W1 4라운드·W2b 2라운드(메인 직접 지시)는 위 E1 run 뒤 변경이라 단위 테스트(apm_bench 567 passed)로만 확인했다 — E2 실동작 확인은 W6 내부망 run 몫.
- 2026-10-14 — **계획 끝 모듈 회귀**(`regress.py --base 4073f7f --files <머리 29개>` · 메인 직접 교정 반영 뒤 재실행): rc 0 · 본체 통과 733 · 실패 0 · arch·overfit 통과 · ruff 위반 5(이번 diff 줄 0) · mypy 미실행(대상 파일 없음 — 루트 venv 미설치 · 수동 mypy 0) · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행` · 산출물 `logs/regress/20261014-164029-90798`.
- 2026-10-14 — **통합 모듈 회귀 1회**(메인 지시 — 152·156·157·164 기준 대비 전체 변경 63파일 · `regress.py --base 4073f7f --files …` · 모든 교정 반영 뒤): rc 0 · 본체 통과 9,467 · 실패 48(전부 `[원래 실패]` — 라우트·스트림·plan122_t4 등 4073f7f에서도 실패) · arch·overfit 통과 · ruff 293·mypy 219(이번 diff 줄 0) · 권고 블록 없음 · `범위: 모듈 단위 — 전체 미실행` · 산출물 `logs/regress/20261014-164850-7453`.
- **위험·잔여**: 구분자 변형(`z_bench`) 미치환 · 원이름 토큰이 코드 어휘 낱말과 같으면 치환·대조 안 함(보호 범위 넓어짐 — 원이름 전체 대조는 유지) · 레지스트리 밖 소스 라벨이 자유 문장에 섞이면 못 막음 · NFKC로 전각·호환 문자가 바뀐 모양으로 실림 · 짧은 호스트명이 흔한 낱말이면 E2 정지(→ 전용 실행 호스트·계정) · N-2가 넓게 멈춤 · 「없음」 턴도 송신됨 · trace/report `placeholder_missing:host_1`의 host 토큰 치환 · `--bg` 크래시 traceback 미보존 · 경로 목록 방식 `--audit-digest`는 skip-heavy 기본 off · 같은 계정 PID 재사용 판별 불가 · `ps eww`로 `APM_BENCH_BG_ARGV`가 같은 계정에 보임 · `--bg` 자식이 부모 셸 export를 모두 받음(의도) · E2에서 `--term-policy`·로컬 파일 모두 없으면 exit 2(의도) · `--status`·거부 문구는 벤치 CLI라 매뉴얼 대상 아님.
- **열린 질문(G-8 — 사용자)**: 1회차 체크아웃 SHA는 「모른다」로 확정(경고 내고 HEAD 시나리오) · 나머지(작업 트리 변경 · `--probe`·`--term-policy` 사용 여부 · 실행 계정 · 본체 `ACTIVE_DB_IDS` · `sources.by_placeholder` 범위 · 실행 호스트가 제니퍼 감시 대상인지 · 157 교정을 재실행 SHA에 넣을지)는 W6 재실행 전 확인 — 권고 없는 판단은 현행 비트 동일로 두었다.

## 12. 변경 이력

| 일자 | 버전 | 내용 |
|---|---|---|
| 2026-10-14 | v1.4 | W1~W5 구현 완료 기록(§11) · 상태 갱신 · 회귀 대상 확정 · 메인 직접 교정(W1 4라운드 · W2b 2라운드) 반영 |
| 2026-10-14 | v1.3 | 실행 명령 간소화(W2b) — results/apm_bench/<run_id>/ 자동 · E2 한 줄 · --smoke·--bg·--status · audit-digest run_id 모드(§5 W0a 1~6 · 사전 · 1·2단계 · 반출 조건) |
| 2026-10-14 | v1.2 | W0a 감사 로그 진단표 추가·구현(§4.0 · §5 W0a · §6 · §7-7 · 권고 ⓪) — 사용자 「방안 1로 진행해서 164에 W0a 추가하라」 · 파일명 TODO → WIP |
| 2026-10-14 | v1.1 | 독립 검토·보안 검토 반영(§11) — 단계 구분 재설계 · 재판정 기준 확보 · G-2 개정 명시 · 안전 요약 화이트리스트 강화 · `parsed_views` 구멍 · 157 착수 순서 |
| 2026-10-14 | v1.0 | 최초 작성 — 원인 분석(집계 A-1~4 · target T-1~5 · gw_error H-1~5 · 관문 2건 · var) · 게이트 G-1~G-8 · W0~W6 |
