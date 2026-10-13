# 152. 제니퍼 APM 조회 벤치마크 — API 전수(경로 × 선택 인자) 사용자 프롬프트를 운영 경로로 돌려 선택 정확도를 판정하고, 반출 근거로 few-shot을 고쳐 정확도를 올린다

> **작성일**: 2026-10-12 · **v1.0**(최초) · **v1.1**(2026-10-12 — 사용자 인터뷰 G-1~G-8 확정 반영 · 정확도 중심 · API 전수 시나리오 · 운영 뷰 서버 허용 범위 · 반출 근거 묶음 · few-shot 개선 루프 · D-323) · **v1.2**(2026-10-13 — 외부망 구현 W0~W7 · 실행 방법 §10 · 폐쇄망 키트 부록 A) · **v1.3**(2026-10-13 — 남은 질문 정리: 후속 계획 156·157·158 · xdist 설치 · CLI 옵션 유지 · `logs/` 감사 로그 유지)
> **상태**: **WIP — 외부망 W0~W7 완료(2026-10-13) · 내부망 W8(1회차 실행·반출) 잔여** · 결정 **D-323** · E1(가짜 LLM) 턴 정확도 87.6% → 159 교정 뒤 97.7% — 남은 실패 3턴은 `plans/157`(존 사전 게이트 · APM 대상 미승계 · 벤치 드라이버) · 실행 방법 §10 · 폐쇄망 키트 부록 A
> **요청 원문(2026-10-12)**: *"apm의 api를 테스트하여 사용자 프롬프트에 따른 조회성능을 높일 수 있도록 하려고 한다. apm 벤치마크 테스트 계획을 작성하라."*
> **인터뷰 답(2026-10-12)**: ① 성능 = **답이 틀린다**(정확도) ② *"제니퍼 api를 분석하여 api를 모두 조회하는 프롬프트를 생성하라."* ③ 커버리지 = **경로 × 선택 인자** ④ *"뷰 서버를 통해 api를 호출하니 제약이 크지 않을까? 검토해보고 허용범위를 정하라."* ⑤ *"우선 개발 뷰 서버 없이 진행하라."* ⑥ 반출 이름 = **형식 보존 치환** ⑦ *"반출하는 정보를 보고 apm의 사용자 프롬프트 응답 정확도를 높일 수 있어야 한다."* ⑧ *"원하는 결과가 안나올 경우 사용자 프롬프트의 fewshot정보를 수정하거나 하는 방법으로 수정할 계획이다."* → 정리안 *"이대로 확정"*(개발 70 · 홀드아웃 30 포함).
> **성격**: 현행 실측 + 설계 + 작업 분해. 이 계획은 **측정 기계와 개선 루프 절차**를 만든다. 회차별 교정(few-shot·코드)은 회차마다 후속 계획으로 연다(ITAM 벤치 135 → 139·146·149·150·151과 같은 방식).
> **상위·인접 계획**: `plans/134`(제니퍼 전체 읽기 — **W8 채팅 진입점 골드 · W10 실응답·허용값**의 측정 수단) · `plans/135` 통합본(ITAM 벤치 — 드라이버·위생·관문·형식 보존 치환 재사용) · `plans/130`(대상 해석) · `plans/147`(다중 소스 선택) · `plans/132`(소스 선별) · `plans/122`(시나리오 하네스·기준일) · `plans/87`(게이트웨이)
> **관련 결정**: D-003 · D-053(레지스트리 파생 · 사본 금지) · D-127·D-240(실 LLM = 로컬 MLX · MLX 최소) · D-195 · D-219(관측 0 = 사고 · 폐쇄망 축소 반출) · D-251(기준 경로 2단) · D-274(패키지 경계) · D-290 · D-296 · D-299(⑤ 보기 선택 · ⑦ G-12 속도 5) · D-300 ④ · D-301(ITAM 벤치) · D-313 · D-321(형식 보존 치환) · D-322 · **D-323(이 계획)**
> **실측 기준**: 2026-10-12 `main` HEAD `ee13b47`. `file:line`은 이 시점 값이다 — W0에서 다시 대조한다.
> **계획 시작 SHA**: `ee13b47` · **회귀 대상 파일**: `src/orchestration/apm_query.py` · `src/orchestration/intent_planner.py` · `src/orchestration/investigation_audit.py` · `scripts/apm_bench/*.py`(14) · `testdata/apm_bench/*.yaml`(4) · `tests/test_scripts/test_apm_bench_*.py`(10)

---

## 0. 요약

**목표** — 제니퍼 사용자 프롬프트에 대해 시스템이 **맞는 소스·보기·대상·구간·조건을 고르는가**를 숫자로 만들고, 틀린 턴마다 **어느 단계가, 어느 few-shot이 원인인지**를 반출본만으로 알 수 있게 한다. 외부망에서 그 근거로 few-shot(또는 코드)을 고치고, 다음 회차의 **홀드아웃** 정확도로 효과를 판정한다.

```
 API 전수 분석(CAPABILITY-MAP-134 표 A·B)
   → 경로 × 선택 인자마다 사용자 프롬프트 + 기대값 5축 (개발 70 · 홀드아웃 30)
 내부망: 운영 뷰 서버 + FabriX · 허용 범위(§3.7) 안에서 1회 실행
   → 턴별 근거 묶음(분해 입력·원출력·정제·재선택·대상 후보·구간·호출·응답) · 형식 보존 치환 · 누출 관문
 반출 → 외부망: 분류 → few-shot 위치 지목(F1~F4) / 코드 교정 대상 분리
   → few-shot 수정 → 오프라인 재현(MLX · 방향 확인) → 다른 경로 회귀 → 다음 회차 → --compare(홀드아웃)
```

| 만드는 것 | 위치 | 한 줄 |
|---|---|---|
| 벤치 CLI | `scripts/apm_bench/` (신규) | `--dry-run` · `--coverage` · `--probe` · `--run` · `--compare` · `--replay` · `--fewshot-check` |
| 시나리오 | `testdata/apm_bench/scenarios.yaml` | API 경로 × 선택 인자 전수 · 일부 멀티턴 · 기대값 5축 · `split: dev|holdout` · `covers:`(COV 행·인자) |
| 일반 단어 허용 목록 | `testdata/apm_bench/term_policy.yaml` | 치환하지 않고 원문으로 남길 업무 일반 단어(내부망 운영자 검토) |
| few-shot 수정 이력 | `testdata/apm_bench/fewshot_log.yaml` | 회차별 수정 — 대상 실패 ID · 위치(F1~F4) · 지문 전후 |
| 산출물 | `results/apm_bench/<run_id>/` (git 제외) | `run.json` · `trace.jsonl`(근거 묶음) · `probe.jsonl` · `coverage.md` · `report.md` · `leak_check.json` |
| 테스트 | `tests/test_scripts/test_apm_bench_*.py` | 로더 린트·커버리지·판정·분류·예문 복사·치환·관문 |

**권고 ①** — APM은 살아 있는 데이터라 값 대조가 아니라 **구조 판정 5축**이 중심이다. 기대값은 `spec/CAPABILITY-MAP-134.md`의 「API → 도구 → 보기 → 인자」 대응에서 결정적으로 뽑는다(사람이 정답을 따로 쓰지 않는다).
**권고 ②** — 운영 뷰 서버는 운영자 화면과 같은 서버다. 벤치는 §3.7 허용 범위(초당 2 · 소스당 3,000회 · 자동 중단)를 벗어나지 않는다.
**권고 ③** — few-shot 수정은 **개발셋 실패만 보고** 하고, 효과는 **홀드아웃**으로만 판정한다. 벤치 프롬프트 원문을 예문에 넣지 않는다(검사기 강제).

---

## 1. 요구와 확정

### 1.1 요구 → 수용 기준

| # | 요구 | 수용 기준(§5) |
|---|---|---|
| R1 | API 전수 — 허용 GET 경로 × 선택 인자마다 그것을 호출하게 만드는 사용자 프롬프트 | `--coverage`가 표 A 경로·표 B 선택 인자 미커버 0(제외는 사유와 함께 명시) |
| R2 | 사용자 프롬프트 · 로그인 사용자 경로 · 고정 주입 없음 | 로더 린트 위반 0 · 첫 턴 본문 `query`뿐 |
| R3 | 정확도 판정 | 턴마다 5축 판정 · 실패 분류(결정적) |
| R4 | 운영 뷰 서버 부하 허용 범위 | §3.7 사전 점검·예산·자동 중단이 코드로 강제 |
| R5 | 반출본만으로 정확도 개선 가능 | 실패 턴마다 근거 묶음(§3.8) 필수 칸 누락 0 · few-shot 수정 가능 여부·위치 지목 |
| R6 | few-shot 수정 루프 | 예문 커버리지 표 · 예문 복사 탐지 · 홀드아웃 판정 · 프롬프트 크기 상한 · 다른 경로 회귀 |

### 1.2 확정 사항 (인터뷰 2026-10-12)

| 게이트 | 답 |
|---|---|
| G-1 성능의 뜻 | **정확도**. 지연은 기록만(판정·목표선 없음) |
| G-2 시나리오 출처 | **API 전수 분석으로 생성** — 43사례(`docs/33`)는 출발점일 뿐 |
| G-3 커버리지 단위 | **경로 × 선택 인자**(약 120~150턴) · 앞 턴 결과가 필요한 API는 멀티턴 묶음 · 응답 필드 단위는 범위 밖 |
| G-4 부하 허용 범위 | §3.7(검토 후 확정) |
| G-5 개발 뷰 서버 | **없이 진행** — 운영 뷰 서버 하나로 · 무거운 등급·WAS 접촉 API도 운영에서 마지막에 |
| G-6 반출 이름 | **형식 보존 치환**(D-321) + 토큰 단위 일관 + 일반 단어 허용 목록 |
| G-7 민감 API 3종 | 포함 · 응답 모양만 기록 |
| G-8 개선 수단 | **few-shot 수정**이 주 수단 · few-shot으로 못 고치는 실패는 코드 교정으로 분리 · 같은 실패가 예문 수정 2회 뒤에도 남으면 결정적 처리로 승격 |
| 분할 | **개발 70 · 홀드아웃 30** · 경로마다 양쪽 최소 1 |

범위 밖: 지연 판정 · 부하·동시성 시험 · 응답 필드 단위 커버리지 · 알람(F-11·12)·조사(F-13) 진입점 · 1단·3단 경로 · HTTP 재시도 도입 · 게이트웨이 감사 줄 칸 추가.

---

## 2. 현행 실측 (2026-10-12 · HEAD `ee13b47`)

### 2.1 게이트웨이 (`apm_gateway/`)

- **도구 25종**: 데이터 21(`interface/server.py` 14 · `scope_server.py` `apm_service_status`·`apm_business` · `fleet_server.py` `apm_fleet` · `manage_server.py` `apm_config`·`apm_environment`·`apm_users`·`apm_active_detail`) + 작업 3 + `gateway_health`. SSE · 기본 포트 9096.
- **허용 경로 41템플릿**(`adapters/jennifer/allowlist.py:70-161`) — GET만 · 템플릿 정확 일치 · 경로별 허용 쿼리 키.
- **클라이언트**: 타임아웃 10초 · 속도 5회/초 소스별(`PriorityThrottle` — poller > interactive > background · 10초 대기마다 승격) · 응답 4MiB = 스풀 임계 · HTTP 재시도 없음 · 인벤토리 캐시 600초 · 지표 카탈로그 3600초 · 작업 동시 4.
- **시간 조각**: X-View(`/api/transaction/time`) 1분 창 · 최대 10분 · 변경 이력 25시간 조각. 업무 정의는 도메인마다 1회 — 운영 약 350도메인이면 첫 업무명 질의 약 350호출·약 70초(`docs/31` 1173행).
- **감사 줄**: `apm audit: tool= target= elapsed_ms= api_calls= sources= rows= error= … thread_id=`(`interface/audit.py:55` 오류 갈래 `error=` · `:70` 성공 갈래 `rows=` — W0 정정).
- **실 제니퍼 없는 백엔드**: `apm_gateway/testdata/jennifer/scripts/mock_openapi.py`(`--mode connected` · `/__mock/hits`·`/__mock/usage`) + `tests/conftest.py` `synthetic_fixtures()`. 실프로세스 왕복 선례 `tests/test_orchestration/test_plan130_verify_real_gateway.py` · `tests/test_api/test_plan147_w4_real_gateway.py`.

### 2.2 본체 경로 (`src/`)

| 단계 | 위치 |
|---|---|
| 분해(LLM) | `src/prompts/intent_planner.py` `INTENT_PLANNER_APM_SECTION`(431행~) + `<apm_view_rows>` = `apm_query.py:359` `render_view_rows` |
| 정제 | `conditional_agents.py:89` `sanitize_task_views` · `:139` `apply_explicit_sources` |
| 소스 선택 | `src/routing/apm_source_select.py:238` · `apm_query.py:1672` `_source_decision` |
| 보기 재선택(LLM) | `apm_query.py:1396` `_select_views`(최대 1회) |
| 대상 해석 | `resolve_apm_targets`(:760) · `_resolve_target_texts`(:2964) · `entity_link.link_hostnames` · 서버 미지정 `_untargeted_step`(:2484) |
| 구간 | `input_parser` → `time_resolution` → `plan_window`(:525) · `plan_compare_window` · `auto_interval` |
| 실행·응답 | `run_apm_query`(:2049) · `_run_calls`(:1090) · `_settle_jobs`(:3172) · `_answer_lines`(:3615) → `result_aggregator._with_answer_lines` |

### 2.3 APM few-shot 위치 (개선 대상)

| # | 위치 | 내용 | 쓰이는 곳 |
|---|---|---|---|
| **F1** | `config/db_registry.yaml` `solutions[apm].views[].examples` | 보기 29개 중 27개에 「질문 → 인자」 예문(예: `「web01 fatal 이벤트만」 → level=fatal · level_mode=exact`) | 분해 프롬프트 보기 표 · 필수 인자 되묻기 문구(`apm_query.py:1617`) |
| **F2** | `src/prompts/intent_planner.py` `INTENT_PLANNER_APM_SECTION` | 소유 규칙 · JSON task 예시 | APM 활성 시 분해 프롬프트 |
| **F3** | 같은 파일 `INTENT_PLANNER_OWNERSHIP_EXAMPLES` | 교차 시스템 예시 | 소유 시스템 둘 이상 활성 시 |
| **F4** | `apm_query.py:1396` `_select_views` 프롬프트 | 보기 재선택 | 재선택 시(예문 유무 W0 확인) |

보기 설명(`label`)·인자 설명(`args[].label`)도 같은 렌더에 실리므로 F1과 함께 수정 대상이다.

### 2.4 측정 공백

| # | 공백 | 처리 |
|---|---|---|
| M1 | 보기·인자 정확도 골드 없음 — `fs_four_source.yaml`은 `plan.agents`만 단언(초안) | 이 계획 시나리오가 채운다(134 W8 채팅 골드 겸용) |
| M2 | 분해 원출력(`views`·`view_args`·`targets`·`sources`) · 정제 변경 · 재선택 입출력 · 대상 후보 목록이 응답 관측에 실리는지 미확인 | W0 실측 → 없으면 측정 연결점(§3.6) |
| M3 | 실 제니퍼 응답 모양·허용값·보존 기간(134 W10) | 내부망 P층이 측정 |
| M4 | 로컬 9B 조건 환각(12문항 중 10 — `plans/134` §12.3) — F1 예문과 같은 모양(`level_mode=exact`) | 예문 복사 탐지(§3.5) · 판정은 FabriX run만 |

---

## 3. 설계

### 3.1 흐름

```
[U층]  --run   본체 서버(ServerHandle · tier2 고정) ─ 로그인 ─ /api/v1/query/stream
              ├ SSE 관측(plan_summary · disclosures · 되묻기 · node_elapsed_ms)
              ├ 측정 연결점(§3.6): 분해 원출력 · 정제 변경 · 재선택 · 대상 후보 · 구간 · 호출
              ├ 벤치 전용 게이트웨이 감사 줄 tail(thread_id) · 예산 계수기 · 자동 중단 감시
              └ 판정(§3.4) → 분류(§3.5) → 치환·관문(§3.8) → results/apm_bench/<run_id>/
[P층]  --probe 결과가 빈 턴의 원인 구분 · 허용값 탐침만(같은 게이트웨이 · 같은 예산)
```

| 환경 | 백엔드 | LLM | 용도 |
|---|---|---|---|
| E1 외부망 | 목 Open API(`connected` · 합성 2소스) + 게이트웨이 서브프로세스 | 가짜 LLM · MLX 소수 | 로직 확인 · 오프라인 재현 — **결론 금지** |
| E2 내부망 | **운영 뷰 서버**(개발 뷰 서버 없음 — G-5) | FabriX | 결론 · 사용자 실행 · 반출 |

### 3.2 시나리오 — API 전수 생성 (`testdata/apm_bench/scenarios.yaml`)

```yaml
- id: APM-STATUS-SQL-sort          # COV 행 + 인자
  covers: [COV-STATUS-SQL, COV-STATUS-SQL.sort_by_metrics]
  split: dev                        # dev | holdout
  heavy: false                      # §3.7 무거운 등급
  turns:
    - prompt: "{{inst_1}}에서 오늘 실행 시간이 가장 긴 SQL 10개 보여줘"
      expect:
        agent: apm_query
        views: [apm.sql_stats]      # any_of 허용
        target: {kind: instance, ref: inst_1}
        window: {kind: hourly, from: today}
        args: {sort_by: elapsed, n: 10}   # 말하지 않은 인자는 적지 않음 → 붙으면 환각
```

- **생성 규칙**: 표 A(경로)의 COV 행마다 기본 프롬프트 1개 이상, 표 B의 **선택 인자**마다 그 인자를 말로 요구하는 프롬프트 1개(필수 인자·코드가 채우는 인자 `domain_id` 등은 제외 — 사유 표기). 표현은 사용자 말투(구어·약어·순서 변형)로 쓰고 도구명·보기명·API 경로·영문 인자 키를 넣지 않는다.
- **멀티턴**: 프로파일(`txid`)·GUID 추적·실행 중 요청 상세(`sessionId`·`threadHash`)·SQL 프로파일은 앞 턴 목록 → 「그 거래/그 요청」 후속으로 묶는다.
- **자리표**: 운영 이름은 저장소에 넣지 않는다 — `{{inst_N}}`·`{{host_N}}`·`{{biz_N}}`·`{{svc_N}}`·`{{src_N}}`. 내부망 키트가 운영 인벤토리에서 채운다(로컬 전용 파일 · 반출 금지).
- **분할**: 개발 70 · 홀드아웃 30 · COV 경로마다 양쪽 최소 1(가능한 경우) · 분할은 시드 고정 결정적 배정.
- **기대값 근거**: 보기·인자 기대값은 레지스트리 `views[].tool`·`args[].tool_arg`와 표 A·B의 대응에서 뽑는다. 로더가 기대 보기가 레지스트리에 존재하는지·기대 인자가 그 보기의 `args`에 있는지 검사한다.

### 3.3 P층 (축소)

정확도 중심(G-1)이므로 P층은 두 용도만 한다. ① **빈 결과 판별**: U층에서 행 0인 턴의 호출을 같은 인자로 직접 재호출해 「데이터 없음」과 「잘못 해석」을 가른다(과거 고정 창만). ② **허용값 탐침**(134 W10): `interval_minute`·`sort_by_metrics`·`max_row` 후보값 1회씩. 반복 측정 없음 (구현 부기 2026-10-13: 무거운 등급 시나리오의 0행 호출은 다시 부르지 않고 `skipped(heavy)`로 남긴다. 가드가 비정상 — 중단·타임아웃·429 1건 이상 — 이면 P층 호출을 하지 않는다). 같은 게이트웨이·같은 예산을 쓴다.

### 3.4 판정 — 구조 5축

| 축 | 판정 |
|---|---|
| ① 소스 | `apm_query` 담당 여부(교차 질의는 집합) · D-322 소스 지목 반영 |
| ② 보기 | 집합 일치 / 부분(누락·초과 수) / 재선택 발생 |
| ③ 대상 | 종류 일치 · 자리표 대상 해석 성공 · 서버 미지정은 상위 N 경로 진입 |
| ④ 구간 | 종류 일치 · 길이 오차 이내(앵커 = 턴 송신 시각 KST) |
| ⑤ 인자 | 요구한 인자 반영 · **요구하지 않은 인자 부착 = 환각** |
| (부가) 응답 | 행 > 0인데 판정 줄 없음 = 조합 탈락 |

- 되묻기 턴은 실패가 아니다(별도 집계). APM 도구 호출 관측 턴 0 = 판정 안 함·exit 1(D-219 ③).
- 지표: 축별 정확도 · **턴 정확도(5축 전부 통과)** · split별 · COV 경로별.

### 3.5 분류 → 고칠 곳 (few-shot 우선)

| 분류 | 조건 | few-shot 수정 가능 | 고칠 곳 |
|---|---|---|---|
| `source_miss` | ① 실패 | 예 | F2·F3 소유 규칙·예시 |
| `view_wrong` / `view_partial` | ② 불일치 | 예 | F1 해당 보기 예문·`label` · F2 |
| `view_retry` | 재선택 발생 | 예 | F1 · F4 |
| `arg_missing` | 요구 인자 누락 | 예 | F1 해당 인자 예문·`args[].label` |
| `arg_hallucination` | 비요구 인자 부착 | 예(대개 예문 조정) | F1 |
| **`fewshot_copy`** | 부착 인자가 질문 근거 없이 **특정 예문의 인자와 같음**(예문 지목) | 예 | 그 예문 |
| `source_select` | 소스 지목 오반영 | 일부 | `sources[].terms`(D-322) |
| `sanitize_drop` | 분해는 맞았는데 정제가 버림 | 아니오 | `sanitize_task_views` 코드 |
| `target_unresolved` / `target_wrong` | ③ 실패 | 아니오 | 대상 해석(`plans/130`) |
| `window_wrong` | ④ 실패 | 아니오 | `input_parser`·`plan_window` |
| `empty_result` | 행 0(P층으로 데이터 없음 확인 시 실패 아님) | 아니오 | 창·대상 |
| `gw_error` | 게이트웨이 오류 | 아니오 | — |
| `answer_drop` | 행 > 0 · 판정 줄 없음 | 아니오 | `result_aggregator` |

- **예문 커버리지 표**(`coverage.md`): 보기·인자별 예문 유무 × 실패율. 「예문 없음 + 실패」 = 예문 추가 · 「예문 있음 + 실패」 = 예문 교정 또는 결정적 처리 후보.
- **승격 규칙**: 같은 실패 ID가 예문 수정 2회 뒤에도 남으면 결정적 처리(코드) 대상으로 올린다(Known Mistakes 「프롬프트 강제가 few-shot과 경쟁해 반복 실패하면 결정적 조립 대상」).

### 3.6 측정 연결점 (본체 최소 변경)

- W0에서 `Observation.plan_summary`·SSE에 분해 원출력·정제 변경·재선택·대상 후보·구간이 실리는지 실측한다. 실리는 것은 본체 변경 0.
- 빠진 것만 기존 `src/observability/run_capture.emit()`(미설치 = 즉시 반환 · D-301 ⑥)으로 보탠다. 후보 지점(최대 3): ① 분해 직후 APM task 원출력 + 정제 전후 ② `_select_views` 입력 보기 후보·출력·사유 ③ `run_apm_query` 끝(대상 후보 목록·해석 결과·구간·호출 목록). 수신기 설치는 `scripts/apm_bench/_serve.py`에서만. 기본 동작 비트 동일을 테스트로 고정한다.

### 3.7 운영 뷰 서버 허용 범위 (G-4 · 검토 결과)

**검토 근거**: Open API는 운영자 화면과 같은 뷰 서버(7900 · Jetty)가 처리한다 · 부하 상한 미확인(`docs/31` U-7) · 5.6.2.18 미만은 토큰 호출마다 Jetty 세션 누적(`docs/31` 1627행) · X-View 1분 창(10분 = 10호출) · 전 도메인 순회 약 350호출 · 토큰 사용량 429 가능성(U-5) · 게이트웨이 5회/초는 운영 폴러와 공유 · 실행 중 요청 상세·로드된 클래스·환경변수는 뷰 서버가 WAS 에이전트에 물을 가능성(**추정** — W0·W10 확인).

| 항목 | 허용 범위 |
|---|---|
| 사전 점검(필수) | 뷰 서버 버전 확인 — 5.6.2.18 미만이면 실행하지 않고 보고 |
| 호출 속도 | 벤치 **전용 게이트웨이 초당 2회 · 폴러 끔** — 운영 게이트웨이(5회)와 합쳐 뷰 서버 초당 7회 이하 · 운영 게이트웨이 설정 불변 |
| 반복 | 시나리오 1회(반복 측정 없음) |
| 예산 | 소스당 **3,000호출/회** — 초과 시 즉시 중단 |
| 무거운 등급 | 전 도메인 순회 · 24시간 초과 X-View · 전체 대상 — **최대 5시나리오 · 맨 마지막 · 앞 단계 정상일 때만** |
| WAS 접촉 가능 API | 실행 중 요청 상세 · 로드된 클래스 · 환경변수 — **API당 1회 · 운영자 지정 인스턴스 1개** |
| 자동 중단 | 시작 시 `/api/domain` 지연 기준선 측정 → ① 429 1회 ② 타임아웃 비율 5% 초과 ③ 기준선 대비 지연 3배 초과 중 하나면 남은 시나리오 미실행 |
| 시간대 | 업무 외 시간(정확한 시간대는 운영 조직과 협의) |

예상: 일반 약 125턴 × 약 10호출 ≈ 1,300 + 무거운 등급 5 ≈ 1,500 → 예산 이내.

구현 부기(2026-10-13 · 검증 교정): 팬아웃 도구는 도메인별 429·타임아웃을 감사 줄 `error`에 남기지 않으므로, 벤치 게이트웨이를 DEBUG로 띄워 제니퍼 응답 줄(`apm http: … status=… elapsed_ms=…`)로도 판정한다(경로·키·상태만 찍힌다 — 토큰 없음). 응답 줄 `status=429` 1건이면 즉시 중단하고, 응답 줄이 없는 호출이 상한의 90% 이상 걸리면 타임아웃으로 센다. 타임아웃 비율 단위는 분자·분모 모두 **도구 호출 수**다. 지연 절대 하한 200ms는 E1(목 지터) 전용이고 E2는 3배만 본다. E2는 아무것도 띄우기 전에 본체 두 평면이 비과금인지 검사해 과금 평면이면 exit 2다.

### 3.8 반출 — 근거 묶음과 위생

**실패 턴마다 근거 묶음**(통과 턴은 요약만):

| 단계 | 반출 칸 |
|---|---|
| 질문 | 프롬프트(치환본) · 기대값 5축 · `covers` · `split` |
| ① 분해 | APM 절 렌더 지문·크기 · 그때 실린 보기 행 id 목록 · **LLM 원출력**(agent·views·view_args·targets·sources) |
| ② 정제 | 바뀌거나 버려진 항목과 사유 |
| ③ 재선택 | 여부 · 입력 후보 · 출력 · 사유 |
| ④ 대상 | 해석 문자열 · 게이트웨이 후보 목록(치환본) · 선택 · 「찾지 못함」 |
| ⑤ 구간·인자 | 기준 시각 · 해석 구간 · 실제 도구 인자(치환본) |
| ⑥ 실행 | 도구 순서 · 행 수 · 빈 결과 · 오류 유형 · P층 대조 |
| ⑦ 응답 | 판정 줄 유무 · 고지·되묻기 문구(치환본) · 길이 |
| 분류 | 분류 · few-shot 수정 가능 여부 · 지목 위치(F1~F4 · 보기·인자·예문 번호) · `fewshot_copy` 근거 예문 |
| 버전 | 코드 SHA · F1~F4 지문 · 레지스트리 지문 |

few-shot 원문은 반출하지 않는다 — 같은 SHA에서 외부망이 그대로 다시 렌더할 수 있다.

**위생**(ITAM 벤치 `scripts/itam_bench/redact.py`·`substitute.py`·`LeakGate` 재사용 · 게이트웨이 import 없음 — D-274):
- 이름(인스턴스·호스트·업무·서비스·도메인)은 **토큰 단위 일관 형식 보존 치환**(D-321 — `secrets` 난수 · 대응표 없음 · run 안 일관). 이름끼리 공유 토큰(`ORD`)은 어디서든 같은 가짜 토큰.
- **일반 단어 허용 목록**(`term_policy.yaml`) — 사람·조직을 식별하지 않는 업무 일반 단어(「주문」·「결제」·`WAS`·`PRD` 등)는 원문 유지. 내부망 운영자가 검토해 넣는다(기본 거부).
- 토큰 단위 치환의 범위(구현 부기 2026-10-13): ASCII 2자 토큰과 camelCase 결합 소문자형(`OrdPay01` → `ordpay`)도 기본 거부 대상이다. 토큰 사전은 trace·probe·run.json의 **문자열 값 전부**에 적용되고 관문이 같은 범위를 다시 대조한다(`.md`는 원이름·ID·카나리아만 — 저장소 어휘와의 거짓 양성 회피). 그래서 이름 토큰과 겹치는 코드 값(인자 값 `domain`, 보기 id의 `db` 등)도 가짜 값이 된다 — 진단에 필요하면 `term_policy`에 넣어야 원문으로 남는다.
- IP·GUID·txid·SQL 본문·계정은 치환 또는 가림 · 민감 API 3종은 응답 모양(키 이름·건수)만 · 로그인 계정·OS 사용자는 D-300 ④ · 토큰 미기록.
- 누출 관문: 카나리아 · 그 run 인벤토리 원이름 전부 · 정규식 · 사용자 정보 대조 → 실패 시 미기록 + `leak_check.json` + exit 1.

### 3.9 few-shot 개선 루프 (외부망)

1. 반출본 `report.md` §분류 → few-shot 수정 가능 실패(**개발셋만**)를 위치별로 묶는다. 코드 교정 대상은 별도 목록.
2. F1~F4를 고친다. **금지**: 벤치 프롬프트 원문·자리표 대상과 같은 문장을 예문으로 넣기 — `--fewshot-check`가 시나리오 전체와 n-gram 유사도로 차단(과적합 방지).
3. `--replay`(오프라인 재현): 같은 SHA에서 고친 프롬프트를 렌더하고 반출된 질문·앞 턴 맥락으로 분해·재선택 단계만 로컬 MLX로 다시 돌린다 — **방향 확인용**(FabriX와 다를 수 있음 · D-240 최소).
4. 게이트: 분해 프롬프트 APM 절 크기 상한(W0에서 현재 크기 기준으로 정함) · `prompt_render_diff.py` · `eval_routing` · `fs_four_source` 회귀 0(APM 절은 APM 활성 시 모든 질의의 분해 프롬프트에 실린다).
5. `fewshot_log.yaml`에 대상 실패 ID·위치·지문 전후를 남긴다.
6. 다음 내부망 회차 → `--compare`: **홀드아웃 턴 정확도 상승**이 판정 기준 · 대상 실패 ID 해소 여부 · 다른 ID 악화 여부 · 예문 수정 2회 뒤 잔존 = 결정적 처리 승격.

### 3.10 산출물

| 파일 | 내용 |
|---|---|
| `run.json` | run_id · SHA · 사다리 확정 단 · 두 LLM 평면 · 뷰 서버 버전 · 게이트웨이 설정(숫자만) · 예산 사용량 · 자동 중단 여부·사유 · F1~F4 지문 |
| `trace.jsonl` | 턴별 5축 판정 · 분류 · 근거 묶음(§3.8) · 단계 시간(기록만) |
| `probe.jsonl` | P층 기록 |
| `coverage.md` | COV 경로·인자 커버리지 · 예문 커버리지 × 실패율 |
| `report.md` | §0 축별·턴 정확도(dev/holdout) · 분류 상위 · few-shot 위치별 실패 · 코드 교정 목록 · `fewshot_copy` 목록 |
| `leak_check.json` | 관문 결과 |

---

## 4. 작업 분해

| Wave | 내용 | 검증 |
|---|---|---|
| **W0** 실측 재대조 | §2 인용 · `plan_summary`·SSE에 분해 원출력·정제·재선택·대상 후보 실림 여부 · F4 예문 유무 · APM 절 현재 렌더 크기 · 감사 줄 `thread_id` · 목 `connected`로 도구 25종 응답 | §9 기록 · §3.6 지점 확정 · 크기 상한 수치 |
| **W1** 시나리오·로더·커버리지 | `scenarios.yaml`(초안 생성됨 — §9) 검수 · 로더 린트 · 기대값 레지스트리 정합 검사 · `--coverage` · 분할 | 린트·정합 단위 테스트 · 미커버 0 |
| **W2** U층 드라이버 | `ServerHandle`(tier2) · 로그인 · 관측 · 측정 연결점 + `_serve.py` · 벤치 전용 게이트웨이 기동(초당 2 · 폴러 끔) · 예산·자동 중단·사전 점검 | E1 가짜 LLM 종단 · 미설치 비트 동일 · 예산 초과·429·지연 3배 중단 테스트 |
| **W3** 판정·분류 | 5축 · 분류 13종 · `fewshot_copy` · 예문 커버리지 · P층(빈 결과·허용값) | 관측 칸 → 분류 표 고정 단위 테스트 |
| **W4** 위생·반출 | 토큰 단위 치환 · `term_policy` · 민감 모양만 · 관문 · 근거 묶음 필수 칸 검사 | 카나리아·원이름·토큰 주입 시 관문 실패 · 필수 칸 누락 0 |
| **W5** 개선 루프 도구 | `--replay` · `--fewshot-check` · `--compare`(dev/holdout · 대상 ID) · `fewshot_log.yaml` | E1에서 예문 1건 바꿔 재현·비교 1회 |
| **W6** 외부망 스모크 | E1 전 시나리오 가짜 LLM 1회 + MLX 대표 3턴(두 평면 `mlx` 확인 후) | exit 0 · 관문 통과 |
| **W7** 폐쇄망 키트 | 절차서(부록) · 자리표 채우기 · `term_policy` 검토 절차 · 반출 목록 | 절차서대로 E1 완주 1회 |
| W8 | 내부망 1회차(사용자) → 반출 → **회차 교정 계획**(few-shot·코드) | 범위 밖 |

- 회귀(D-303): Wave 중간은 자기 테스트 + `regress.py --no-tests --files …` · 계획 끝 1회 `regress.py --base ee13b47 --files <바꾼 파일>`.
- 매뉴얼(D-255): 벤치·하네스라 대상 아님.

---

## 5. 수용 기준

1. `--coverage`: 표 A 허용 경로 · 표 B 선택 인자 미커버 0(제외는 사유 명시) · 분할 70/30 · 경로마다 양쪽 최소 1(불가 사유 명시).
2. `--dry-run`: 린트 위반 0 · 기대 보기·인자가 레지스트리와 정합.
3. E1 `--run`(가짜 LLM): 전 턴 2단 · 5축 판정·분류 기록 · 관측 0이면 exit 1.
4. 허용 범위: 버전 미달·예산 초과·429·타임아웃 5%·지연 3배 각각에서 중단(테스트) · 무거운 등급은 마지막·최대 5.
5. 실패 턴 근거 묶음 필수 칸 누락 0 · 누출 관문이 카나리아·원이름·토큰·IP 주입을 전부 잡음.
6. `fewshot_copy` 탐지 · 예문 커버리지 표 · `--fewshot-check`가 시나리오 원문 예문을 차단 · `--compare`가 holdout 기준 판정.
7. 측정 연결점 미설치 시 기본 동작 비트 동일 · `arch_check`·`overfit_check` 통과 · 모듈 단위 회귀 통과(범위 줄 그대로 보고).

---

## 6. 사용자 확정 게이트 — 전건 답 (2026-10-12 인터뷰)

§1.2 표가 정본이다. 남은 확인은 운영 협의 2건뿐이다: ① 업무 외 실행 시간대 ② (선택) 뷰 서버 Open API 부하 상한(U-7) — 협의 결과가 §3.7보다 엄격하면 그 값을 따른다.

## 7. 위험

- **운영 부하**: 허용 범위·자동 중단으로 막지만 뷰 서버 상한은 미협의다(U-7). 첫 회차는 무거운 등급을 꺼 두고 돌리는 선택지를 키트에 둔다.
- **WAS 접촉 추정**: 실행 중 요청 상세 등이 WAS 에이전트에 닿는지 미확인 — W0(문서)·W10(실측)으로 확인 전까지 API당 1회 · 지정 인스턴스 1개.
- **과적합**: 개발셋만 보고 고치고 홀드아웃으로 판정 · 예문 원문 검사기. 그래도 홀드아웃이 같은 생성 규칙에서 나왔다는 한계가 있다 — 회차가 쌓이면 실사용 질문 이력(`plans/84`)에서 홀드아웃을 보충하는 것을 검토한다.
- **프롬프트 크기**: 예문이 늘면 FabriX 입력 한도(ITAM `backend_limit` 전례) — 크기 상한 게이트.
- **치환의 의미 손실**: 한↔영 약어 관계(「주문」↔`ORD`)는 허용 목록에 없으면 보이지 않는다 — 대상 해석 실패 분석의 한계로 리포트에 표시.
- **살아 있는 데이터**: 회차 비교는 구조 5축으로만.
- **MLX 오용**: E1·`--replay` 수치는 방향 확인용 — `--compare` 판정 근거에서 제외.

## 8. 의사결정 정합

- 충돌 없음: D-296(게이트웨이 경유 · 같은 허용목록) · D-299 ⑤(보기 선택 정확도 측정 수단) · D-240 · D-219 · D-301·D-321(위생 재사용) · D-274 · D-053(예문은 레지스트리 정본에서만 고침 — 사본 금지).
- 신규 **D-323** 등재(2026-10-12).

## 9. 진행 기록

- 2026-10-12 — 인터뷰 확정 · D-323 · 시나리오 초안 `testdata/apm_bench/scenarios.yaml` 생성(W1 일부 — 검수·로더·커버리지 검사기는 미착수).
  - 규모: 시나리오 121 · 턴 135(멀티턴 12묶음 — 행 지목 후속 · 소스 되묻기·승계) · dev 85 / holdout 36(30%) · heavy 5(전부 dev) · WAS 접촉 6(내부망 실행 3 · 나머지 3은 `e2_skip: was_touch_limit`) · 답할 수 없는 질문 3(거절이 정답) · 필수 인자 되묻기 1 · 교차 소스 2.
  - 생성 시 대조(1회용 생성기 — 저장소 밖): `spec/CAPABILITY-MAP-134.md` 표 A 40경로 · 표 B 실제 전달 선택 인자 26개 · 레지스트리 보기 29개 · 보기 인자 59개 모두 미커버 0 · 기대 인자 중 레지스트리에 없는 이름 0. 표 A 40경로 모두 dev·holdout 양쪽에 있다 — 단 `COV-ACTIVE-DETAIL`·`COV-ENV`·`COV-LOADED-CLASS`는 §3.7 API당 1회 제한 때문에 holdout 쪽이 외부망 전용이다.
  - 실측 확인: 프롬프트 린트(도구명·보기명·`/api`·영문 인자 키·IP) 0건 · F1 예문 49개·F2/F3 프롬프트와 유사도 0.8 이상 0건 · `select_apm_sources`로 소스 시나리오 판정 일치(무지목·「은행」 = 되묻기, 「은행존 제니퍼」 = bank 하나, 승계 = common). 「은행존」만 답하면 bank·legacy 둘 다 걸려 다시 되묻는다 — 되묻기 답 턴은 「은행존 제니퍼」로 적었다. (D-329 이후 소스 id는 b0·cm_prd·cm_stg — plans/161)
- 2026-10-12 — **W0 실측 재대조**(파일 변경 0 · 실 LLM 0).
  - §2 인용: 어긋남 1건(경미) — 감사 줄은 `interface/audit.py:55`가 **오류 갈래**(`error=`), 성공 갈래(`rows=`)는 `:70`이다(두 갈래 모두 `thread_id`). 나머지(허용 41템플릿 · 도구 25종 · 속도 5/타임아웃 10 · F1 보기 29/예문 보유 27/예문 49 · `apm_query.py`·`conditional_agents.py`·`intent_planner.py` 행 번호) 전부 일치. 소스 파일은 `src/orchestration/apm_query.py`.
  - 벤치 게이트웨이 「초당 2 · 폴러 끔」은 기존 env로 된다 — `JENNIFER_RATE_LIMIT_PER_SEC=2`(실측 최소 간격 0.48초) · `APM_EVENT_POLLER_ENABLED=false`(기본값). 게이트웨이 코드 변경 0.
  - M2 관측: SSE `done`에는 `plan_summary`(정제 **뒤** agent·views) · `disclosures` · `time_resolution` · `apm_source_*`만 실린다. 분해 원출력·정제 변경·재선택·대상 후보·도구 인자는 안 실린다 → §3.6 **3지점 확정**: ① `investigation_audit.py` `_run` `finally`(처리기 결과 메타 전체 — ③~⑦) ② `intent_planner.py` `_sanitize_task_views`의 정제 호출 직전(정제 전 원출력 — thread_id 없음 → 캡처 오프셋으로 턴 귀속) ③ `apm_query.py` `_retry_selection`의 재선택 원출력. 하네스 `Observation`은 `time_resolution`·`apm_source_*`를 버리므로 벤치가 done을 따로 보관한다.
  - F4: `_SELECTION_SYSTEM`(`apm_query.py:1332`)에 자체 예문 없음 — `<rows>`로 F1 예문이 그대로 다시 실린다(F1을 고치면 F4도 바뀐다).
  - 크기(코드 기본값·레지스트리 정본 렌더): APM 절 렌더본 **12,344자**(19,690B) · 분해 프롬프트의 APM 증분 13,323자(F1 보기 표가 66%) → **상한: APM 절 ≤14,200자 · 증분 ≤15,300자**(+15%). 지문(sha256 앞 12 · `itam_bench/_serve.py` `fingerprint()` 방식): F1 `9c2eb9cd7418` · F2 `99ee2e9f9bf9` · F3 `f5646d317265` · F4 `71ff3e68f138`.
  - 목 `connected` + 게이트웨이 서브프로세스: 도구 25/25 응답. `apm_transaction_trace`·`apm_service_status`·`apm_business`는 합성 픽스처에 `/api/transaction/guid`·`/api/realtime/domain`·`/api/realtime/business`가 없어 501 → 벤치가 픽스처를 보탠다. 목에는 429·지연 주입 제어 경로가 없다 → 벤치 프로세스 안에서 `Handler._route`를 감싸 주입.
  - 없는 장치: GUID 가림 함수(ITAM `redact.py`에 없음) · 서브프로세스용 가짜 LLM → 127.0.0.1 가짜 OpenAI 호환 서버를 `mlx` 평면으로 연결(비과금 판정 통과).
- 2026-10-12 — **W1 완료**: `scripts/apm_bench/{__init__,__main__,scenarios}.py` · 테스트 52건 통과 · `--dry-run`·`--coverage` exit 0. 실측: 시나리오 121 · 턴 135 · dev 85/holdout 36(29.8%) · heavy 5(전부 dev) · was_touch 6(e2_skip 3) · 표 A 40/40 · 표 B 선택 인자 26/26 · 보기 29/29 · 보기 인자 59/59(미커버 0 · 표 B 제외 90행은 사유와 함께 출력). `scenarios.yaml` 수정 0건. 경고 3(위반 아님): `COV-ACTIVE-DETAIL`·`COV-ENV`·`COV-LOADED-CLASS`의 holdout은 외부망 전용. 결정: 되묻기 턴은 되물을 대상 보기를 `views`에 적을 수 있다(필수 인자 되묻기 판정에 필요) · 전 턴 거절 시나리오만 `covers: []` 허용 · `--coverage`는 분할 위반도 exit 1(§5 ①) · 허용 자리표에서 미사용 `{{src_N}}` 제외.
  - 모듈 계약: 턴 관측 레코드 `scripts/apm_bench/record.py`(팀 리드 — `TurnRecord`·`Observed`·`Verdict`).
- 2026-10-12 — **W2 완료**(U층 드라이버): `driver.py`·`gateway.py`·`guard.py`·`fake_llm.py`·`_serve.py`·`placeholders.e1.yaml` · 본체 emit 3지점(+7줄: `investigation_audit.py` `_run` finally `investigation_result` · `intent_planner.py` 정제 직전 `apm_plan_raw` · `apm_query.py` `_retry_selection` `apm_view_reselect`) · 테스트 38건. E1 전 시나리오(가짜 LLM): 135턴 · APM 관측 108 · 2단 확정 · 중단 0 · 297초.
  - 결정: 가짜 LLM = 127.0.0.1 OpenAI 호환 서버를 `mlx` 평면으로(분해 응답 기본값 = 기대값에서 만든 정답 계획 · 턴별 덮어쓰기 훅) · **뷰 서버 버전은 허용 API로 얻을 수 없다**(`Instance.version`은 에이전트 버전) → E2는 `--view-server-version` 운영자 입력 필수(미입력·5.6.2.18 미만 exit 2) · 예산 = 감사 줄 `api_calls` 소스별 합 · 타임아웃 비율 분모 하한 20 · 지연 3배는 절대 증가 200ms 하한 병행(목 지터) · 기준선 `gateway_health` 10시나리오마다 재측정 · 감시 스레드 0.5초 · E1은 레지스트리 3소스(b0·cm_prd·cm_stg — D-329 · plans/161)를 같은 목에 연결(소스 모호성 재현).
  - **제품 결함 발견(이 계획에서 고치지 않음)**: 분 단위 기간(「지난 30분」·「10분치」)이 `input_parser` 시간 해석에서 `clarify=unresolved`로 끝나 APM 처리기에 닿지 않는다(규칙이 분을 못 잡고 LLM 슬롯 `time_expr.unit`에 minute 없음 → `invalid_unit`) — E1 15턴.
- 2026-10-12 — **W3 완료**(판정·분류·P층): `judge.py`·`probe.py` · 테스트 35건(분류 13종 각 1+ · 기대값 그대로의 관측 합성 시 전 턴 통과 자기 일관성). 보기 축 비교 대상 = 재선택 결과 또는 정제 뒤 계획(도구는 여러 보기가 공유) · 환각 제외 = `fixed_args`·`target_arg`·`reference`·보기 기본값·코드 채움 인자·창 인자 · P층은 같은 인자(봉투 window로 기간 키 복원) 재호출, 과거 고정 창만.
- 2026-10-12 — **W4 완료**(위생·반출 관문): `hygiene.py`·`term_policy.yaml`(빈 목록 = 기본 거부) · 테스트 46건. ITAM `FakeValues`·`PiiVault`·`redact_text`·`LeakGate`·`write_gated` 재사용(ITAM 코드 변경 0). 토큰 단위 일관 치환 · GUID/txid 전용 형식 보존 생성기 · 가짜 값이 PII 정규식에 걸리면 재추첨(E1 실물에서 발견) · E1 실물 135턴: 근거 묶음 누락 0 · 관문 위반 0(40회 반복).
- 2026-10-12 — **W5 완료**(개선 루프 도구 로직): `fingerprints.py`·`loop.py`·`fewshot_log.yaml` · 테스트 17건. `--fewshot-check` = 문자 3-gram 자카드 0.8(현재 최고 0.778 · 위반 0) + 크기 상한 · `--compare` = holdout 턴 정확도 · E1/MLX run은 판정 제외 · 2회 뒤 잔존 승격 · `--replay` = `_llm_decompose`·`_select_views` in-process(LLM 주입 · MLX는 평면 확인 뒤만). 팀 리드 교정: 지문 현재 값을 테스트에 고정하던 단언 제거(개선 루프가 고치는 값이라 다른 작업 회귀를 깸).
- 2026-10-13 — **통합 Wave 완료**(W4·W5 CLI 연결 · E1 실패 분류): `--run` 6단계(드라이버 → 판정 → P층 → 재판정 → 치환 → 근거 → 관문 → 산출물 6종) · `--fewshot-check`·`--compare`·`--replay`·`--probe`·`--llm fake|mlx`(MLX 평면 점검 실패 = exit 2) · 인벤토리 이름 수집(도구 2호출) · 테스트 `test_apm_bench_cli.py` 15건(9파일 218건 통과).
  - E1 전체(가짜 LLM): 135턴 · APM 관측 114 · **턴 정확도 87.6%**(dev 78/89 · holdout 35/40) · 축별 source 99.2 · views 100 · target 97.4 · window 90.6 · args 100 · 관문 통과 · 근거 누락 0 · 576초.
  - 벤치 측 교정(실패 34 → 17턴): 가짜 LLM 입력 파서 정답 응답 · 소스 되묻기 자동 답 뒤 파싱 답 유지 · 목 픽스처 보충(인스턴스 1002·1003 · `/api/dbmetrics/domain`·`/business` · 빈 hostName) · 시간/존 사전 되묻기 분류(`window_wrong`/`code:input_parser` · `source_miss`/`code:route_zone_gate` — 거절로 세지 않음) · 판정 규칙: 대상 승계 시 앞 턴 인스턴스의 호스트도 인정 · 상위 N 첫 홉 인자와 참조 호출 창은 각 축 판정에서 제외 · P층 재호출에 hostname.
  - **남은 실패 17턴 = 제품 결함 2건**(이 계획에서 고치지 않음 — 후속 계획 대상): ① **분 단위 기간 미지원** 12턴 + 연쇄 4턴 — `src/domain/time_spec.py:57` `Unit`에 minute 없음 · `src/domain/time_expr.py:724` `invalid_unit` · `:76` 규칙 정규식에 「분」 없음 · `src/prompts/input_parser.py:185` 단위 목록 → `src/nodes/input_parser.py:197-198` 되묻기로 종료. ② **존 사전 게이트가 APM 질문을 가로챔**(`APM-JSERVER-domains#0`) — `src/api/routes/query.py:2188`(`is_full_scan_query` + 「서버」) · `:1656~` `_zone_gate_deferred_to_tasks`가 활성 시스템 2개 미만이면 분해에 넘기지 않음(로컬 `.env` 부재 조건 의존 — 폐쇄망 재현 미확인).
- 2026-10-13 — **W6 완료**(외부망 스모크): E1 전체 `--run --env e1 --probe`(가짜 LLM) exit 0 · 584초 · 135턴 · 평면 `fake` 표기 · **턴 정확도 87.6%**(113/129 · dev 78/89 · holdout 35/40) · 관문 통과 · 근거 누락 0. MLX: 127.0.0.1 임시 포트로 `scripts/mlx_server.sh` 기동 → `python -m scripts.bench --show-env`·`--preflight`로 두 평면 `mlx` 확인 → 대표 3턴(`APM-INST-host`·`APM-ACTIVE-n`·`APM-BIZ-list`) `--llm mlx` 3/3 · `--replay` 1턴 → 자기 PID만 종료. 과금 LLM·`RUN_E2E` 사용 0. 명령·결과는 §10.3.
  - 발견: `--compare`가 시나리오 집합이 달라도 판정함(→ 검증 교정에서 공통 턴 판정) · `python -m scripts.scenario --mock`이 저장소 `results/scenario/`에 씀(지웠고 §10.5에 주의) · `scripts.bench --preflight`는 provider 이름만 본다(도달·생성 점검은 벤치 `--llm mlx` 사전 점검이 한다).
- 2026-10-13 — **W7 완료**(폐쇄망 키트): 부록 A(A.0~A.9) · 절차서대로 E1 완주 1회(`--skip-heavy --term-policy --placeholders` · 87.1% 108/124 · 관문 통과 — A.9). 이탈: 키트에 필요해 CLI `--skip-heavy`·`--term-policy`를 더했다(자리표·허용 목록은 본체 기동 전 검사 · 실패 exit 2). 사용자 요청으로 §10 실행 방법(10.1~10.7)을 실측 명령 기준으로 정리.
- 2026-10-13 — **독립 검증**(verifier): Critical 0 · High 1 · Medium 4 · Low 6 · 결함 재현 `tests/test_scripts/test_apm_bench_verify.py`(xfail strict 8). High = 팬아웃 도구의 부분 429·타임아웃이 감사 줄 `error=None`이라 가드가 못 봄. Medium = E2 본체 과금 평면 미검사 · 구조 칸 문장 토큰 미치환 · ASCII 2자 토큰 사전 누락 · holdout 수정 위치 노출(report §3/§5/§6 · 예문 절 · replay 기본 · 로그 holdout 항목). Low = camelCase 결합형 · `_contains` 부분 일치·자리표 없음 통과 · 지연 하한 E2 적용·비율 단위 혼재 · P층 예산 판정·무거운 재호출 · WAS 1회 키 첫 경로만 · compare 집합 미검사. 정보성 = E2 본체가 저장소 `logs/`에 운영 이름을 남김(본체 로그 경로 env 없음 — 감사 로그 정책 판단이라 손대지 않음 · 부록 A 반출 대상 아님).
- 2026-10-13 — **검증 교정 완료**: 지적 전건 수정(벤치 측만 · 게이트웨이 코드 변경 0) — 게이트웨이 DEBUG 응답 줄 접기로 429·타임아웃 판정 · E2 과금 평면 exit 2 · 토큰 사전 값 전부 적용 + 관문 토큰 대조 · 2자·camelCase 토큰 · report/예문 절 holdout 건수만 · replay 기본 dev · `load_log(holdout_ids)` · `_contains` 경계 일치 · `placeholder_missing` 판정 불가 · 지연 하한 E1 전용 · 비율 단위 도구 호출 · P층 `guard.healthy`·무거운 0행 미재호출 · WAS 키 cov 경로 전부 · compare 공통 턴 판정. verify xfail 표지 0. 테스트 10파일 **248 passed**. E1 `--skip-heavy` 재실행 87.1%(108/124 · 직전과 동일) · 관문 통과 · 가드 tool_calls 105 · http 응답 403 · 타임아웃 0 · 429 0. 남은 근사: 동시 도구의 타임아웃 귀속은 근사(중단이 늦어지는 쪽) · dict 키는 토큰 사전 밖(원이름 전체는 치환). E2 반출본은 이름 토큰과 겹치는 코드 값도 가짜 값이 된다(A.2 6).
- 2026-10-13 — **계획 끝 모듈 단위 회귀 1회**(`regress.py --base ee13b47 --files` 회귀 대상 파일 전부): 본체 통과 2,658 · 실패 6(전부 `[원래 실패]` — `test_plan146_demonstrative_zero_rows`·`test_plan150_sample_antecedent`·`test_eval_routing_modes` 2·`test_intent_extraction_typing` 2) · arch_check·overfit_check·mypy 통과 · ruff 이번 diff 줄 4건(`record.py` 긴 주석) → 고친 뒤 `--no-tests` 재확인 통과 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` 없음. 이 python에는 `pytest-xdist`가 없어 도구의 `-n` 실행이 rc=4로 끝났다 → 도구를 바꾸지 않고 워커 0(직렬)으로 같은 선택을 돌렸다(§10.2 주의).
- 2026-10-13 — **남은 질문 정리**(사용자 *"결함은 후속 계획을 만들고 필요한 것은 설치하고 나머지는 권고에 맞게 진행하라."*): ① 존 사전 게이트·APM 대상 미승계·벤치 드라이버 결함 → `plans/157` · 159가 남긴 시간 해석 틈 → `plans/156` · 반복 「원래 실패」 테스트 → `plans/158`. ② `pytest-xdist`(dev extra · 3.8.0)를 개발 맥 python에 설치 — `regress.py` 병렬 실행 복구(§10.2 직렬 래퍼 주의는 xdist 없는 환경용으로 남긴다). ③ CLI `--skip-heavy`·`--term-policy`는 정식 옵션으로 유지(부록 A 키트가 쓰고, 자리표·허용 목록을 본체 기동 전에 검사한다). ④ 내부망 실행 때 본체가 `logs/`에 남기는 운영 이름은 그대로 둔다 — 감사 로그는 끄지 않는다(모든 질의 실행은 감사 대상 · 보안 원칙), `logs/`는 `.gitignore` 대상이고 A.7 반출 금지 목록에 있다. 벤치용 감사 로그 끄기 옵션은 만들지 않는다. D-323 부기.
- **남은 일(내부망 · W8)**: 운영 협의(업무 외 시간대 · U-7) → 부록 A대로 1회차 실행(`--skip-heavy`) → 관문 통과 6종 반출 → 외부망에서 회차 교정 계획(`plans/NN-TODO-apm-bench-round1.md`)을 연다. **제품 결함 2건**(분 단위 기간 → `plans/159-minute-period-resolution.md` — 2026-10-13 완료 · E1 87.6% → 97.7% · 존 사전 게이트 → `plans/157-TODO-apm-bench-residual-defects.md`)은 별도 후속 계획으로 열었다. 159 뒤 남은 E1 실패 3턴(존 사전 게이트 · APM 대상 미승계 · 벤치 드라이버 소스 답 턴)도 157이 맡는다.

## 10. 실행 방법

> **실측일 2026-10-13**(W6·W7): HEAD `ee13b47`에 이 계획의 미커밋 작업 트리를 얹은 상태, 저장소 `.env`·`.encenv` 없음(코드 기본값 = 워커 `ollama`·오케스트레이터 `vllm`), Apple Silicon 맥북. 명령 옆 결과는 이날 한 번씩 돌린 값이다. 과금 LLM과 운영 뷰 서버를 쓰는 명령은 돌리지 않았고, 그런 명령에는 「미실행」이라고 적었다.
> 명령은 모두 저장소 루트에서 돌린다. `<out>`은 저장소 밖 작업 디렉터리다. `--out`을 빼면 산출물이 저장소 `results/apm_bench/`에 쌓이므로 늘 붙인다.

### 10.1 사전 준비

| 항목 | 내용 |
|---|---|
| 설치 | `pip install -e ".[dev,document,deepagents]"`. `deepagents` extra는 `langchain-openai`를 설치한다. 가짜 LLM과 MLX는 둘 다 본체에 `mlx` 평면(OpenAI 호환)으로 붙기 때문에 이 extra가 없으면 E1 `--run`이 본체 LLM을 만들다 실패한다(`src/llm.py` `_create_mlx`). 게이트웨이 의존(`mcp<2`·`httpx`·`uvicorn`·`redis`)은 루트 의존에 이미 있다. 게이트웨이는 루트 venv로 뜨고 cwd만 `apm_gateway/`다 |
| MLX(선택) | Apple Silicon과 `uvx`가 필요하다. `mlx_lm.server`가 PATH에 없어도 `scripts/mlx_server.sh`가 `uvx --from mlx-lm==0.31.3`으로 받는다. 모델은 HF 캐시에 있는 것만 쓴다(기본은 오프라인). 실측 모델: `mlx-community/Qwen3.5-9B-OptiQ-4bit` |
| `.env` | E1에는 필요 없다. 벤치가 본체 env를 직접 주입한다: 프로파일 `tier2_intent`, `ALARM_ENABLED=false`, 체크포인트·캡처 경로, `MCP_SOURCE_ENDPOINTS`·`MCP_SOURCE_TOKENS`의 APM 키, LLM 두 평면. 단 `--replay`와 `python -m scripts.bench`는 `.env`가 없으면 코드 기본값(워커 `ollama`·오케스트레이터 `vllm`)으로 읽으므로 평면 env를 **명령 앞에** 붙인다(§10.3 ⑥). `.env` 파일은 만들지 않는다 |
| Redis·DB | 필요 없다. 본체는 Redis 연결에 실패하면 「파일 캐시로 폴백」 경고만 남기고, 게이트웨이의 Redis는 `REDIS_PORT=1`로 막혀 있다 |
| 자동 기동 | `--run` 한 번으로 다음 순서로 띄운다: 목 Open API(E1) → 벤치 게이트웨이 → 가짜 LLM(E1 `--llm fake`) → 본체(`scripts.apm_bench._serve` — 측정 수신기 설치). 포트는 127.0.0.1 임시 포트이고 8000·8080·9096·9097·9099는 피한다. 끝나면 자기 PID만 내리고 세션 임시 디렉터리(`$TMPDIR/apm_bench_*`, 권한 0700)를 지운다. 스풀도 세션 안에 두므로 `apm_gateway/var`가 생기지 않는다. run 끝에 이를 검사해 남아 있으면 `[경고]`를 낸다 |
| 벤치 게이트웨이 설정 | 자식 env를 벤치가 고정한다(`scripts/apm_bench/gateway.py` `gateway_env`). 부모 env의 `JENNIFER_*`·`APM_*`를 **모두 걷어낸 뒤** 다음 값을 넣는다: `JENNIFER_RATE_LIMIT_PER_SEC=2`(전역과 소스별 `JENNIFER_<ID>_RATE_LIMIT_PER_SEC`) · `JENNIFER_API_TIMEOUT_SECONDS=10`(전역·소스별) · `APM_EVENT_POLLER_ENABLED=false` · `APM_SPOOL_DIR=<세션>/spool` · `APM_GATEWAY_HOST=127.0.0.1`과 임시 포트 · `APM_GATEWAY_BEARER_TOKENS`에 주체 `chat`(본체)·`bench`(P층·기준선) · `REDIS_PORT=1`. E1은 소스 3개(b0·cm_prd·cm_stg — D-329)의 URL·토큰을 목 서버 값으로 채운다. E2는 URL·토큰을 넣지 않으므로 게이트웨이가 자기 `apm_gateway/.env`에서 읽는다. 그 로더는 이미 있는 키를 덮지 않으므로 속도·폴러·스풀은 벤치 값이 이긴다. 운영 게이트웨이 프로세스와 그 설정 파일은 건드리지 않는다 |

**목·게이트웨이 수동 기동**(디버깅용 — 벤치와 같은 합성 픽스처·같은 벤치 env). 실측: 기동부터 도구 1회까지 1.7초, `apm_instance_map` 9행, 끝난 뒤 `apm_gateway/var` 없음.

```bash
python - <<'EOF'
import shutil, tempfile
from pathlib import Path
from scripts.apm_bench import gateway as gw
d = Path(tempfile.mkdtemp(prefix="apm_bench_manual_"))
mock = gw.start_mock(gw.bench_fixtures(), "manual-mock-token", d / "fixtures")
proc = gw.start_gateway(d, sources=["b0", "cm_prd", "cm_stg"], mock=mock)
try:
    print(proc.sse_url, proc.call_tool("apm_instance_map", {}).get("row_count"))
finally:
    proc.stop(); mock.stop(); shutil.rmtree(d, ignore_errors=True)
EOF
```

목 단독 기동(`python apm_gateway/testdata/jennifer/scripts/mock_openapi.py --mode connected`)은 녹화 픽스처를 쓰므로 벤치 픽스처와 다르다. 벤치 재현에는 위 방법을 쓴다. 한 run의 본체·게이트웨이 로그와 캡처를 보고 싶으면 `--debug-raw`(§10.3 ③)를 쓴다.

### 10.2 단위 테스트·정적 게이트

```bash
python -m pytest tests/test_scripts/test_apm_bench_capture.py tests/test_scripts/test_apm_bench_cli.py \
  tests/test_scripts/test_apm_bench_driver.py tests/test_scripts/test_apm_bench_guard.py \
  tests/test_scripts/test_apm_bench_hygiene.py tests/test_scripts/test_apm_bench_judge.py \
  tests/test_scripts/test_apm_bench_loop.py tests/test_scripts/test_apm_bench_probe.py \
  tests/test_scripts/test_apm_bench_scenarios.py tests/test_scripts/test_apm_bench_verify.py -q
```

- 실측(검증 교정 뒤 2026-10-13): exit 0, **248 passed**, 22초(`python -m pytest tests/test_scripts/test_apm_bench_*.py -q`와 같다). `test_apm_bench_verify.py`는 독립 검증이 쓴 결함 재현 테스트다 — 고쳐진 뒤 xfail 표지를 지웠다.
- 마커: `serial` 1건(`test_apm_bench_driver.py::test_e1_end_to_end`). 실제 목·게이트웨이·가짜 LLM·본체를 띄워 3시나리오를 도는 E1 종단 테스트이고, `regress.py`가 직렬로 따로 돌린다. `live_llm`(실 LLM) 테스트는 없다.
- Wave 중간 정적 게이트: `python scripts/regress.py --base ee13b47 --no-tests --files <바꾼 파일…>`. 실측(`__main__.py`·CLI 테스트 2파일): exit 0, arch_check·overfit_check·ruff 통과, mypy는 「미실행 — 대상 파일 없음」(`scripts/`·`tests/`는 mypy 대상 밖), 범위 줄 `범위: 정적 게이트만 — 테스트 미실행`. 처음 돌렸을 때 ruff E501(테스트 독스트링 104자)이 나와 고쳤다. 로그는 `logs/regress/<시각>/`(gitignore)에 남는다.
- 계획 끝 모듈 단위 회귀(팀 리드가 1회 · D-303): `python scripts/regress.py --base ee13b47 --files src/orchestration/apm_query.py src/orchestration/intent_planner.py src/orchestration/investigation_audit.py scripts/apm_bench/*.py tests/test_scripts/test_apm_bench_*.py`. `--plan`을 붙이면 선택 목록만 출력한다. 실측(2026-10-13 · 1회): 본체 통과 2,658 · 실패 6(전부 `[원래 실패]` — 기준 `ee13b47`에서도 실패) · 정적 게이트 arch·overfit·mypy 통과 · ruff 이번 diff 줄 위반 4건(`record.py` 긴 주석)을 고친 뒤 `--no-tests` 재확인 통과 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` 없음. **주의**: 이 도구는 본체 테스트에 `-n <워커>`를 붙인다 — `pytest-xdist`(dev extra)가 없는 python이면 본체 pytest가 rc=4(`unrecognized arguments: -n 8`)로 끝나 통과 수가 0에 가깝게 찍힌다. `pip install -e ".[dev]"`로 설치하거나 직렬로 돌린다.

### 10.3 벤치 CLI — 모드·옵션별 실측

종료 코드: **0** 정상 · **1** run 무효(APM 관측 0)·누출 관문 실패·근거 필수 칸 누락·린트/커버리지 위반·모르는 시나리오 id · **2** 사전 점검 차단(뷰 서버 버전·자리표 파일·허용 목록 파일·MLX 평면·E2 금지 조합). argparse 인자 오류도 2다.

| # | 명령 | 실측 결과 |
|---|---|---|
| ① | `python -m scripts.apm_bench` (= `--dry-run`) | exit 0 · 2.7초 · `시나리오 121건 · 턴 135 · dev 85 / holdout 36 (29.8%)` · `heavy 5 · was_touch 6 (e2_skip 3) · sensitive 4` · 경고 3(`P-e2-only` — `COV-ACTIVE-DETAIL`·`COV-ENV`·`COV-LOADED-CLASS`의 holdout은 외부망 전용) · `결과 : 통과`. LLM·네트워크를 쓰지 않는다 |
| ② | `python -m scripts.apm_bench --coverage --out <out>/cov` | exit 0 · 2.5초 · `표 A 경로 40/40 · 표 B 선택 인자 26/26 · 레지스트리 보기 29/29 · 레지스트리 보기 인자 59/59 · 표 B 제외 90행` → `<out>/cov/coverage.md`. 미커버나 분할 위반이 있으면 exit 1 |
| ③ | `python -m scripts.apm_bench --run --env e1 --probe --out <out>/w6_e1` | exit 0 · 584초(본체 기동 포함) · `[1/4] 게이트웨이 기동 → [2/4] 본체 기동 — 프로파일 tier2_intent → 사다리 확정: intent_orchestration → [3/4] 로그인 생략(인증 꺼짐) → [4/4] 시나리오 116 + 무거운 등급 5 → [P층]` 순서로 진행 · 머리 `[방향 확인용 — 결론 금지] env e1 · 평면 {'worker': 'fake', 'orchestrator': 'fake'}` · `턴 : 실행 135 · APM 관측 114` · `호출 : 소스별 {'bank': 44, 'common': 478, 'legacy': 43} · 타임아웃 0 · 429 0 · 기준선 787.9ms · P층 19회` · **`턴 정확도 : 87.6% (113/129) · dev 87.6% (78/89) · holdout 87.5% (35/40)`** · `축 : source 99.2% · views 100.0% · target 97.4% · window 90.6% · args 100.0%` · `대표 분류 : {'window_wrong': 13, 'target_unresolved': 3, 'source_miss': 1, 'empty_result': 1}` · 근거 누락 0 · 관문 통과. 실패는 일반 턴 16개와 되묻기 정답 턴 1개(`APM-SRC-ambiguous#0`)다. 가짜 LLM이 정답 계획을 주입한 상태에서도 나므로 모두 **본체 결정적 경로 결함**이다: 분 단위 기간 미지원(`window_wrong` 13 — 그중 12턴이 예상 밖 `clarify:time` · §10.7) · 앞 턴 기간 실패에 이어진 후속 대상 해석 실패(`target_unresolved` 3) · 존 게이트(`APM-JSERVER-domains#0` `source_miss` · `clarify:zone`). `empty_result` 1(`APM-MT-guid-direct#0`)은 분류만 붙고 턴은 통과했다. P층: `allowed_value:accepted 16` · `empty_check:no_data 3` · `empty_check:skipped 12` |
| ③-a | `python -m scripts.apm_bench --run --env e1 --limit 3 --debug-raw <out>/raw --out <out>/lim3` | exit 0 · 41.7초 · 시나리오 앞 3개(`APM-INST-list`·`APM-INST-domain`·`APM-INST-host`) · `턴 정확도 100.0% (3/3) · dev 100.0% (1/1) · holdout 100.0% (2/2)` · 관문 통과. `--debug-raw` 디렉터리에 `capture.jsonl`·`gateway.log`·`meta.json`·`records.jsonl`·`server.log` 5종이 남는다(E1 합성 이름뿐 — 반출 대상 아님). E2에서는 `--debug-raw`가 거부된다(exit 2) |
| ③-b | `python -m scripts.apm_bench --run --env e1 --only APM-INST-host APM-ACTIVE-n APM-BIZ-list --out <out>/only3` | exit 0 · 55.6초 · 3/3 · `대표 분류 없음` · 관문 통과. ⑤-b MLX run과 같은 3시나리오를 가짜 LLM으로 돌린 대조군이다. 시간 대부분은 본체·게이트웨이 기동이다 |
| ④ | `… --run --env e1 --skip-heavy --term-policy <로컬 사본> --placeholders <로컬 자리표> --probe --out <out>/kit` | 부록 A.9 키트 완주 기록 참조. `--skip-heavy`를 주면 무거운 등급 5시나리오를 드라이버에 넘기지 않고 `verdict.skipped="heavy_off"`로 남긴다. `run.json`에 `skip_heavy: true`가 기록된다 |
| ⑤ | MLX 서버 기동(백그라운드)<br>`P=<빈 루프백 포트>; LLM_PROVIDER=mlx ORCHESTRATOR_PROVIDER=mlx LLM_MLX_BASE_URL=http://127.0.0.1:$P/v1 ORCHESTRATOR_BASE_URL=http://127.0.0.1:$P/v1 nohup scripts/mlx_server.sh > <out>/mlx_server.log 2>&1 &`<br>먼저 `scripts/mlx_server.sh --dry-run`(같은 env)으로 실행할 명령을 확인한다 | `--dry-run` exit 0: `모델=mlx-community/Qwen3.5-9B-OptiQ-4bit · 주소=http://127.0.0.1:<P>/v1 · 오프라인=1 · 실행기=uvx`. 기동 후 `curl -s http://127.0.0.1:<P>/health` → `{"status": "ok"}`(첫 기동은 uvx가 패키지 34개를 설치한다). 프로세스는 두 개다: `uv tool uvx …` 부모와 `…/bin/python …/mlx_lm.server` 자식. `ps -o pid,ppid,command`로 **둘 다** PID를 적어 둔다. 기본 8080은 다른 세션과 겹치므로 쓰지 않는다 |
| ⑤-a | 두 평면 확인<br>`U=http://127.0.0.1:$P/v1; M=mlx-community/Qwen3.5-9B-OptiQ-4bit; LLM_PROVIDER=mlx ORCHESTRATOR_PROVIDER=mlx LLM_MLX_BASE_URL=$U ORCHESTRATOR_BASE_URL=$U LLM_MLX_MODEL=$M ORCHESTRATOR_MODEL=$M python -m scripts.bench --show-env`, 같은 env로 `--preflight` | `--show-env` exit 0: `프로바이더 : 워커 mlx / 오케스트레이터 mlx` · `승인 정책 : 불요 — 내부망/로컬 프로바이더(워커 mlx, 오케스트레이터 mlx)`. `--preflight` exit 0: `[OK] provider 내부망/로컬 프로바이더(워커 mlx, 오케스트레이터 mlx)` · `→ 실행 가능합니다`. 이 두 명령은 provider 이름만 본다. 주소가 루프백인지와 서버 도달·1토큰 생성은 벤치의 `--llm mlx`·`--replay`가 실행 전에 따로 검사한다(실패하면 exit 2) |
| ⑤-b | `python -m scripts.apm_bench --run --env e1 --llm mlx --mlx-url http://127.0.0.1:$P/v1 --mlx-model mlx-community/Qwen3.5-9B-OptiQ-4bit --only APM-INST-host APM-ACTIVE-n APM-BIZ-list --out <out>/w6_mlx` | exit 0 · 186초(본체 기동 포함 · 첫 질의 콜드 prefill) · 머리 `[방향 확인용 — 결론 금지] env e1 · 평면 {'worker': 'mlx', 'orchestrator': 'mlx'}` · `턴 정확도 100.0% (3/3)` · 5축 전부 100% · `대표 분류 {'view_retry': 1}`(`APM-BIZ-list`가 재선택 뒤 맞힘 — 턴은 통과, 지목 F1·F4) · 관문 통과. 대상 해석(호스트 → 인스턴스)·인자(`n: 3`)·목록형(`mode: list`)이 모두 기대대로 나왔다. **로직 확인용 — 수치로 결론 내지 않는다** |
| ⑥ | `LLM_PROVIDER=mlx ORCHESTRATOR_PROVIDER=mlx LLM_MLX_BASE_URL=$U ORCHESTRATOR_BASE_URL=$U LLM_MLX_MODEL=$M ORCHESTRATOR_MODEL=$M python -m scripts.apm_bench --replay <out>/w6_mlx --turns 'APM-BIZ-list#0' --out <out>/w6_replay` | exit 0 · 9초 · 머리 `방향 확인용 — FabriX와 다를 수 있음` · `보기 축 pass → pass` · `인자 축 pass → pass · {'apm.business': {'mode': 'list'}} → 같음` · `분류 ['view_retry'] → ['view_retry'] · 바뀐 축 없음` → `<out>/w6_replay/replay.md`. `--turns`를 빼면 실패 턴 전부를 재현한다. zsh에서는 `#` 때문에 턴 id를 따옴표로 감싼다 |
| ⑥-a | 같은 명령을 평면 env 없이 실행 | exit 2 · `[중단] 재현 차단 — 두 평면이 모두 mlx 여야 한다(worker=ollama · orchestrator=vllm)`(`.env`가 없을 때) |
| ⑦ | `python -m scripts.apm_bench --fewshot-check` | exit 0 · 3.0초 · `통과 — 유사도 0.8 이상 0건 · 크기 상한 이내`, 이어서 「다음에 돌릴 명령」 5줄(§10.5 ③) |
| ⑧ | `python -m scripts.apm_bench --compare <A> <B> --out <out>/cmp` (A = 수정 전, B = 수정 후) | exit 0 · 0.2초 · 실측은 직전 E1 run(평면 표기 수정 전) → W6 E1 run: `holdout 턴 정확도 87.5% (35/40) → 87.5% (35/40) · 불변 (방향 확인용)` · `참고 dev: 87.6% → 87.6%` · 악화 없음 · 승격 없음 → `<out>/cmp/compare.md`. 머리에 `판정 근거에서 제외(A: E1… · A: MLX 평면… · B: E1…)`가 붙는다(직전 run은 가짜 LLM을 `mlx`로 적던 시절 산출물이라 A에 MLX 사유가 붙음). 두 run의 턴 집합이 다르면(예: 전체 vs `--only` 3개) **공통 턴만으로** 판정하고 `compare.json` `turn_sets` 칸과 보고서 한 줄로 알린다(공통 턴 0이면 판정 불가). 두 run의 holdout id를 `fewshot_log.yaml` 로더에 넘겨 holdout 턴을 대상으로 적은 항목을 거부한다. run 산출물이 아니면 exit 1 |
| ⑨ | MLX 서버 종료: `kill <자식 PID> <부모 PID>` → `lsof -nP -iTCP:$P -sTCP:LISTEN`(출력 없음)·`curl …/health`(연결 거부) | 실측: 두 PID 종료, `ps` 결과 없음, `lsof` rc 1, `curl` rc 7. 다른 세션의 MLX·본체 PID는 건드리지 않는다 |

옵션 메모
- `--only <id…>`는 입력 순서를 지킨다. `--limit N`은 `--only` 뒤에 적용된다. 모르는 id가 있으면 exit 1이다.
- 일정은 `guard.schedule`이 정한다. 무거운 등급(heavy)은 맨 끝에 최대 5개를 돌리고, 앞 단계에 중단·타임아웃·429가 하나라도 있으면 건너뛴다(`heavy_gate`). E2에서는 `e2_skip`을 건너뛰고 WAS 접촉 API는 경로당 1회만 돈다(`was_once`).
- `--probe`는 P층(§3.3)이다. 빈 결과를 같은 인자로 재호출하고, `interval_minute`·`sort_by_metrics`·`max_row` 허용값을 1회씩 탐침한다. 탐침 대상은 자리표 `inst_1`이다.
- `--debug-raw <dir>`는 E1 전용이다. 치환 전 `records.jsonl`·`meta.json`과 `server.log`·`gateway.log`·`capture.jsonl`을 남긴다. E1이라 합성 이름뿐이지만 반출물이 아니다.
- `--term-policy <파일>`: 일반 단어 허용 목록. 기본값은 `testdata/apm_bench/term_policy.yaml`(빈 목록 = 기본 거부)이다. `--placeholders`와 이 파일은 본체를 띄우기 **전에** 읽고, 못 읽거나 형식이 틀리면 exit 2다.
- `--view-server-version`: E2에서 필수다(5.6.2.18 이상). E1에서는 검사하지 않는다. 값을 주면 그 값을, 주지 않으면 `e1-mock(해당 없음)`을 기록한다.

### 10.4 산출물 읽는 법

`<out>/`(기본 `results/apm_bench/<run_id>/` · run_id = `YYYYMMDD-HHMMSS-<env>-<SHA 7자>`) 6종. 누출 관문에 실패하면 `leak_check.json` 하나만 남는다.

| 파일 | 읽는 법 |
|---|---|
| `report.md` | 머리: E1이나 MLX 평면이면 `방향 확인용 — 결론 금지` 표시 · SHA(작업 트리 변경 여부) · env · LLM · 평면 · 사다리 · 뷰 서버 · 소요 · 중단 · 예산. 본문: **§0** 턴 정확도(일반 턴 · 전체/dev/holdout) · 레코드 구성(되묻기·거절 정답 · 판정 불가 · 건너뜀) · run 무효 여부 · **§1** 축별 정확도(source·views·target·window·args × 통과·실패·na · 전체/dev/holdout) · **§2** 분류 상위(대표 = 턴마다 첫 분류 · 전체 = 모든 분류) · **§3** few-shot 위치별 실패(F1~F4 · 보기·인자·예문 · dev만 나열, holdout은 건수만) · **§4** 코드 교정 목록(dev만 상세, holdout은 건수만) · **§5** 예문 복사(`fewshot_copy` — 보기·예문 번호·부착 인자 · dev만) · **§6** 되묻기·거절 정답 집계와 예상 밖 되묻기·거절(dev만 나열) · **§7** few-shot 수정 후보(dev만 — 개선 루프 입력) · **§8** 판정 불가·건너뜀 사유 · **§9** P층(`--probe`) · **§10** 근거 필수 칸 누락 · **§11** 위생 주의(허용 단어 수 · 한↔영 약어 손실) |
| `trace.jsonl` | 턴 1줄. 주요 칸: `scenario_id`·`turn`(0부터 · 실패 ID는 `id#turn`)·`split`·`covers`·`heavy`·`was_touch`·`sensitive`·`prompt`(치환본)·`expect`·`placeholders`·`observed`(분해 원출력 `plan_raw`·정제 뒤 `plan_final`·`sanitize_changes`·`reselect`·`targets`·`time_resolution`·`calls`·`clarify_kind`·`response` 등)·`verdict`·`evidence`·`probe`. `verdict.axes`는 5축 각각 `pass`/`fail`/`na`. `verdict.categories`는 분류 13종(§3.5) 중 해당 목록이고 `verdict.locus`는 고칠 곳이다(`F1`~`F4` 또는 `code:<위치>` — 예 `code:input_parser`·`code:plan_window`·`code:route_zone_gate`·`code:resolve_apm_targets`). `verdict.fewshot_fixable`·`fewshot_copy`·`skipped`(건너뜀 사유 `e2_skip`·`was_once:<경로>`·`heavy_cap:5`·`heavy_gate:…`·`heavy_off`·`aborted:<사유>`)·`axis_detail`(축별 근거 · `pre_graph_clarify` · `unexpected`)도 있다. `evidence.level`은 실패 턴이 `full`, 통과 턴이 `summary`, 건너뛴 턴이 `skipped`이다. 실패 턴의 근거 묶음(§3.8)은 `question`·`decompose`·`sanitize`·`reselect`·`target`·`window`·`execution`·`response`·`classification`·`versions` |
| `run.json` | run_id · sha · dirty · env · llm · planes(`fake`/`mlx`/본체 provider) · tier · view_server_version · gateway_settings(숫자만) · schedule · budget(소스별 호출·타임아웃·429) · aborted/abort_reason · guard(기준선·지연 표본·한도) · fingerprints(F1~F4·레지스트리) · apm_section(지문·글자 수·보기 행 수) · counts · turn_accuracy · run_invalid · evidence_missing · skip_heavy |
| `probe.jsonl` | P층 1행 = 1호출(또는 건너뜀 1건 — 무거운 등급의 0행 호출은 `skipped(heavy)`, 가드 비정상이면 P층 호출 없음). `kind`가 `empty_check`(빈 결과 재호출)이면 `status`는 `no_data`(같은 인자로도 0행 — 데이터 없음)·`skipped`(과거 고정 창이 아니어서 재호출하지 않음 등)이고, `allowed_value`(허용값 탐침)이면 `accepted` 등이다. 빈 결과 턴은 P층 대조 뒤 다시 판정되고, 「데이터 없음」으로 확인되면 `empty_result`가 실패에서 빠진다 |
| `coverage.md` | 표 A·표 B·보기·보기 인자 커버리지, 분할, 그리고 run에서만 붙는 **예문 커버리지 × 실패율** 절. 「예문 없음 + 실패」면 예문을 추가하고, 「예문 있음 + 실패」면 예문을 고치거나 결정적 처리 후보로 본다(§3.5) |
| `leak_check.json` | `passed`·`checked_files`·`violations[]`(`rule`·파일·위치)·`rules`(`canary`·`pii_value`·`pii_regex`·`schema_form`·`user_info`·`substitution`·`code_original`). `passed: false`이면 나머지 5종이 기록되지 않는다 |

분류 13종 → 고칠 곳은 §3.5 표가 정본이다. 숫자를 읽을 때 E1 수치는 목 픽스처와 가짜 LLM(정답 계획 주입) 위의 값이라 **본체 결정적 경로의 결함만** 드러낸다는 점을 염두에 둔다.

### 10.5 few-shot 개선 루프 — 순서

1. **대상 고르기** — 반출본 `report.md`의 §7(few-shot 수정 후보 · dev만), §3(위치별), §5(`fewshot_copy`)를 본다. holdout 턴은 고치지 않는다. 코드 교정 대상(§4 · `code:*`)은 따로 모아 회차 교정 계획으로 넘긴다.
2. **고치기** — F1 `config/db_registry.yaml`의 `solutions` APM 항목 `views[]`(`examples`·`label`·`args[].label` · 사본 금지 D-053) · F2 `src/prompts/intent_planner.py`의 APM 상수 5종(`INTENT_PLANNER_APM_SECTION`·`APM_OUTPUT_RULE`·`APM_SKELETON_TAIL_WITH_KEYS`·`APM_SKELETON_TAIL_WITH_SOURCES`·`APM_SOURCES_RULE`) · F3 `INTENT_PLANNER_OWNERSHIP_EXAMPLES` · F4 `src/orchestration/apm_query.py` `_SELECTION_SYSTEM`(자체 예문이 없고 F1 예문이 `<rows>`로 실린다). 벤치 프롬프트 원문이나 자리표 대상과 같은 문장은 넣지 않는다.
3. **`python -m scripts.apm_bench --fewshot-check`** — 시나리오 원문과 문자 3-gram 자카드 0.8 이상이거나 크기 상한(APM 절 14,200자 · 증분 15,300자)을 넘으면 exit 1이다. 출력 끝의 「다음에 돌릴 명령」 5줄이 다음 게이트다:
   - `python scripts/prompt_render_diff.py --ci` — 실측 exit 0 · 0.3초 · `[데이터 조회]`·`[알람 조회]` 「차이 0 — 바이트 동일」.
   - `python -m scripts.bench --preflight` — 두 평면 `mlx`·127.0.0.1 확인(§10.3 ⑤-a).
   - `python scripts/eval_routing.py --decomposition` — 두 평면이 `mlx`면 승인 없이 돌리고, 아니면 `RUN_E2E=1` 건별 승인. 실 LLM을 쓰므로 이 Wave에서는 미실행(MLX 2회 한정).
   - `python -m scripts.scenario --mock --group FS` — fs_four_source 골드 무과금 모의. 실측 exit 0 · 15.3초 · `모의 실행 완료 - 턴 36회`(리포트 「실행 시나리오 36건 중 manual_review 없는 시나리오 36건」). **주의: 산출물이 저장소 `results/scenario/<run_id>/`에 쌓인다**(`--out` 없음). 확인한 뒤 그 디렉터리를 지운다 — 커밋하지 않는다.
   - `python -m scripts.scenario --run --group FS --profile tier2_intent` — 두 평면 `mlx`를 확인한 뒤에. 미실행(실 LLM).
4. **`--replay`(MLX · 방향 확인)** — §10.3 ⑤·⑥ 순서. 고친 프롬프트를 같은 SHA에서 렌더하고, 반출 run의 질문·앞 턴 맥락으로 분해·재선택만 다시 돈다. `replay.md`의 「바뀐 축」으로 방향만 본다(FabriX와 다를 수 있음 · D-240 최소).
5. **`testdata/apm_bench/fewshot_log.yaml`에 기록** — `round`·`date`·`targets`(dev `id#turn`만)·`locus`(`{slot: F1, view: …, arg: …, example: N}`)·`fingerprints.before/after`(`python -c "from scripts.apm_bench.fingerprints import fewshot_fingerprints as f; print(f())"` — 실측 현재 값 F1 `9c2eb9cd7418`·F2 `99ee2e9f9bf9`·F3 `f5646d317265`·F4 `71ff3e68f138`)·`run_id`(근거가 된 내부망 run)·`memo`. `scripts.apm_bench.loop.append_entry()`로 쓰면 형식을 검사한다(holdout 대상이면 거부).
6. **다음 내부망 회차** — 부록 A 절차로 같은 시나리오를 다시 돌린다.
7. **`--compare <이전 run> <새 run>`** — 판정 기준은 **holdout 턴 정확도 상승**이다. 그 밖에 대상 실패 ID 해소, 다른 ID 악화(dev는 상세, holdout은 건수만), 예문 수정 2회 뒤에도 남은 ID(결정적 처리 승격)를 함께 본다. E1·MLX run이 끼면 머리에 「판정 근거에서 제외」가 붙는다.

### 10.6 내부망 실행 요약 (상세는 부록 A)

1. 같은 SHA 체크아웃(운영 게이트웨이와 다른 디렉터리 권장)의 `apm_gateway/.env`에 소스 URL·토큰을 넣는다. 본체 `.env`에는 FabriX를 둔다.
2. 운영 인벤토리에서 자리표 로컬 파일을 만들고 채움 검사를 한다(A.1). 허용 목록 로컬 사본을 검토한다(A.2).
3. 뷰 서버 버전은 운영자가 확인해 입력한다. 5.6.2.18 미만이면 실행하지 않는다(A.3).
4. 업무 외 시간에 1회차는 `--skip-heavy`로 돌린다. 앞 회차가 정상이었으면 다음 회차는 무거운 등급을 포함한다(A.6).
5. 허용 범위와 자동 중단(§3.7 · A.5)은 벤치가 자동으로 지킨다. 중단되면 `run.json`의 `abort_reason`을 보고 다시 시도하지 않는다.
6. 반출 전 관문: `leak_check.json`의 `passed: true`와 파일 6종을 확인한다. 반출하는 것은 그 6종뿐이다(A.7).

### 10.7 자주 나는 오류와 대처

| 증상 | 원인 | 대처 |
|---|---|---|
| exit 2 `뷰 서버 버전을 도구로 얻을 수 없다 — --view-server-version 으로 넣는다` | E2에서 버전을 주지 않았다. 허용 API에는 뷰 서버 버전이 없다(`/api/instance` 버전은 에이전트 버전) | 운영자가 확인한 값을 `--view-server-version 5.x.y.z`로 준다 |
| exit 2 `뷰 서버 5.6.2.1 < 5.6.2.18 — 실행하지 않는다(D-323 ①)` | 뷰 서버가 하한 미만이다(Jetty 세션 누적 위험) | 실행하지 않고 보고한다 |
| exit 2 `E2 는 --placeholders(…)가 필수다` · exit 2 `자리표·허용 목록 파일을 읽지 못했다 — [Errno 2] …` | E2에 자리표 파일을 주지 않았거나 경로가 틀렸다. `--term-policy`도 같다(형식 위반 — 숫자만인 항목, 여러 토큰 등 — 도 exit 2) | 경로를 고친다. 자리표 채움 검사(A.1)를 먼저 돌린다 |
| 턴이 `판정 불가 · 자리표 미등록: …` | 자리표 파일에 시나리오가 쓰는 이름이 없다(16종 필요) | A.1 채움 검사 명령으로 미등록 목록을 보고 채운다 |
| exit 2 `--llm mlx 사전 점검 실패 — MLX 점검 중단: MLX 서버(워커)=도달 실패 (…)` | MLX 서버가 떠 있지 않거나 포트가 다르다 | `curl -s http://127.0.0.1:$P/health`로 확인하고 `--mlx-url`의 포트를 맞춘다 |
| exit 2 `MLX 주소가 루프백이 아니다: …` | `--mlx-url`이 127.0.0.1·localhost가 아니다 | 루프백 주소만 쓴다(D-240) |
| exit 2 `재현 차단 — 두 평면이 모두 mlx 여야 한다(worker=ollama · orchestrator=vllm)` | `.env`가 없어 코드 기본값으로 읽혔다(`--replay`는 본체 설정을 읽는다) | 명령 앞에 `LLM_PROVIDER=mlx ORCHESTRATOR_PROVIDER=mlx LLM_MLX_BASE_URL=… ORCHESTRATOR_BASE_URL=… LLM_MLX_MODEL=… ORCHESTRATOR_MODEL=…`을 붙인다. 오케스트레이터 주소 키는 `ORCHESTRATOR_BASE_URL`이다(`src/config.py` `OrchestratorConfig` · 접두 `ORCHESTRATOR_`) |
| exit 2 `E2 본체 과금 평면 [...] — 실행하지 않는다` | E2 본체 설정의 워커·오케스트레이터 평면 중 하나가 과금 provider다(예: `gemini`) | 폐쇄망 `.env`의 `LLM_PROVIDER`·`ORCHESTRATOR_PROVIDER`를 확인한다. 우회 옵션은 없다 |
| exit 2 `--debug-raw 는 E1 전용이다` · `--llm mlx 는 E1 전용이다` | E2 금지 조합이다 | 빼고 다시 돌린다. E2는 본체 LLM 설정을 그대로 쓰고 원본을 디스크에 쓰지 않는다 |
| `python -m scripts.apm_bench --coverage --out …` 가 `unrecognized arguments` | zsh에서 `"$m"`처럼 인자 묶음을 한 문자열로 넘겼다 | 인자를 따로 쓴다(실측에서 한 번 겪음) |
| `scripts/mlx_server.sh` `포트 … 를 다른 프로세스가 쓰고 있다` | 기본 8080이나 고른 포트가 이미 쓰이고 있다 | `python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1])"`로 빈 포트를 골라 `LLM_MLX_BASE_URL`에 넣는다. 다른 세션의 프로세스는 종료하지 않는다 |
| exit 1 `[중단] 기동 검증 실패: …` | 본체 설정 에코가 프로파일 `tier2_intent` 주입값과 다르거나, 사다리가 2단 `intent_orchestration`으로 확정되지 않았다 | 메시지 뒤 사유를 본다. E1이면 `--debug-raw`의 `server.log`에서 기동 에코와 `record_ladder_resolution` 줄을 본다 |
| `.env`에 다른 MCP 소스가 있는데 그 값이 그대로 남는다 | dict env(`MCP_SOURCE_ENDPOINTS`·`MCP_SOURCE_TOKENS`)는 `.env` 값과 **키 단위로 병합**된다. 벤치는 APM 키 하나만 덮는다 | 정상 동작이다. APM 키가 벤치 게이트웨이 주소로 덮였는지는 `--debug-raw`의 `server.log`에서 확인한다 |
| `[경고] apm_gateway/var 가 남았다` | 게이트웨이가 기본 스풀을 썼다(스풀 경로 주입 실패, 또는 수동으로 띄운 게이트웨이) | 벤치가 띄운 것이면 결함으로 보고한다. 끝나면 `ls apm_gateway/var`가 없어야 한다 |
| 기동 실패 `게이트웨이 포트 대기 30초 초과`·`게이트웨이 기동 실패(exit …)` | 의존 미설치(`mcp<2`), 또는 E2 `apm_gateway/.env` 형식 오류(`JENNIFER_SOURCES`와 단일 설정을 함께 씀 등) | 오류 끝부분이 메시지에 실린다. E1이면 `--debug-raw`의 `gateway.log`를 본다 |
| `[실패] 누출 관문 — 위반 N건 {...} · 산출물 미기록` (exit 1) | 반출본에 원이름·카나리아·PII·사용자 정보가 남았다 | `<out>/leak_check.json`의 `violations[].rule`·파일·위치를 본다. `canary` → 자리표 파일의 카나리아가 새어 나왔다. `pii_value` → 인벤토리·자리표 원이름이나 GUID/txid 원값이 남았다(치환 누락). `user_info` → 로그인 계정·OS 사용자·호스트명·홈 경로·JWT·Bearer가 섞였다. `pii_regex` → IP 등 PII 정규식, 또는 이 run의 가짜 값이 아닌 GUID 모양. 허용 목록에 단어를 넣어 우회하지 않는다. 원인 코드를 고친 뒤 다시 돌린다 |
| `결과 : 실패 — run 무효 — APM 관측 턴 0` (exit 1) | 본체가 APM을 한 번도 부르지 않았다(게이트웨이 미연결·사다리 강등 등 · D-219 ③) | `사다리` 줄과 `호출` 줄을 본다. E1이면 `--debug-raw`로 `server.log`를 본다 |
| `[중단] 가드 — latency_3x` / `timeout_ratio` / `quota_429` / `budget:<소스>` | 자동 중단(§3.7)이 걸렸다. 남은 시나리오는 `aborted:<사유>`로 기록된다 | E2에서는 다시 시도하지 않고 운영 조직에 보고한다. E1에서 다른 무거운 작업과 동시에 돌리면 지연 기준선이 흔들릴 수 있으므로 단독으로 돌린다 |
| 분 단위 기간(「지난 30분」·「최근 10분」 등) 턴이 시간 되묻기로 실패 | **제품 결함**이다: `src/domain/time_spec.py` Unit에 minute이 없어 `time_expr`가 `invalid_unit`으로 끝난다(§9 W2). 판정은 `window_wrong`이고 지목은 `code:input_parser`·`code:plan_window` | few-shot으로 고칠 수 없다. 회차 교정 계획의 코드 교정 대상이다. MLX 대표 턴을 고를 때도 빼야 한다 |
| `APM-JSERVER-domains#0`이 `source_miss`(`code:route_zone_gate`) | 존 사전 게이트가 APM 질문을 가로챈다(`src/api/routes/query.py` 존 게이트). 활성 시스템이 1개인 로컬 조건에서 나타난다 | 코드 교정 대상이다(제품 결함 ②) |
| `Redis 연결 실패, 파일 캐시로 폴백` 경고 | 로컬에 Redis가 없다 | 무시해도 된다 |
| `enable_semantic_routing 미입력 → …` 두 줄 | `.env`가 없어 사다리 플래그가 미입력이다 | 무시해도 된다. 벤치 본체는 프로파일 `tier2_intent`로 2단을 고정한다 |

---

## 부록 A. 폐쇄망 키트

> 내부망 운영자용 절차서다. 외부망에서는 이 절차를 E1으로 한 번 완주했다(A.9). 운영 이름·토큰·URL 실값은 저장소·반출본·이 문서 어디에도 적지 않는다.

### A.0 준비물

| 항목 | 위치 | 반출 |
|---|---|---|
| 코드 | 같은 SHA 체크아웃 · `pip install -e ".[dev,document,deepagents]"`. 운영 게이트웨이와 **다른 디렉터리를 권장**한다 — 그래야 벤치 전용 토큰을 그 체크아웃의 `apm_gateway/.env`에만 둘 수 있다. 같은 디렉터리여도 벤치는 운영 `.env`를 읽기만 하고 고치지 않는다 | — |
| 본체 설정 | 그 체크아웃의 `.env`(FabriX 워커 평면, 내부망 정본 값). 벤치는 프로파일 `tier2_intent`를 덮어 2단을 고정한다 | 금지 |
| 게이트웨이 설정 | 그 체크아웃의 `apm_gateway/.env`: `JENNIFER_SOURCES`(레지스트리 `solutions[apm].sources[].id`와 같은 id) + 소스별 `JENNIFER_<ID>_API_URL`·`JENNIFER_<ID>_API_TOKEN`(가능하면 벤치 전용 AIOps 토큰). 셸에 `JENNIFER_*`를 export해도 벤치가 걷어내므로 **반드시 이 파일**에 둔다. 속도·폴러·스풀·포트·주체 토큰은 벤치가 덮으므로 적지 않아도 된다 | 금지 |
| 자리표 로컬 파일 | 저장소 밖(예: `~/apm_bench_local/placeholders.e2.yaml` · 권한 600) | 금지 |
| 허용 목록 로컬 사본 | 저장소 밖(예: `~/apm_bench_local/term_policy.local.yaml` · 권한 600) | 금지 |
| 산출 디렉터리 | 저장소 밖(예: `~/apm_bench_out/<회차>/`) | 6종만(A.7) |

본체가 인증을 켜 두었으면 벤치는 내장 테스트 계정(`scripts/scenario/runner.py` `DEFAULT_USER_ID`)으로 로그인하므로 그 계정이 있어야 한다.

### A.1 자리표 로컬 파일

1. 형식 견본 `testdata/apm_bench/placeholders.e1.yaml`을 저장소 밖으로 복사한다: `mkdir -p ~/apm_bench_local && chmod 700 ~/apm_bench_local && cp testdata/apm_bench/placeholders.e1.yaml ~/apm_bench_local/placeholders.e2.yaml && chmod 600 ~/apm_bench_local/placeholders.e2.yaml`
2. 운영 인벤토리(제니퍼 콘솔 · 운영 게이트웨이의 인스턴스·업무·서비스 목록)에서 칸을 채운다.
   - `values`(16종 필수): `inst_1`·`inst_2`(일반 인스턴스 둘) · `inst_c`(공동존 소스 인스턴스) · `inst_b`(은행존 소스 인스턴스) · `inst_w`(**운영자가 지정한 WAS 접촉 허용 인스턴스 1개** — 실행 중 요청 상세·로드된 클래스·환경변수 대상) · `host_1`·`host_2`(WAS 호스트명 — `host_2`는 FQDN 형태 권장) · `biz_1`·`biz_2`(업무명) · `svc_1`·`svc_2`(서비스·도메인명) · `url_1`(애플리케이션 URL 하나) · `pid_1`(`host_1` 위 WAS 프로세스 PID) · `guid_1`(최근 거래 GUID 하나) · `user_1`(제니퍼 계정 하나 — 민감 API라 응답 모양만 기록) · `domain_id_1`(도메인 번호). 값은 모두 문자열로 쓴다(`"4242"`).
   - `any_values`: 기대 인자 `*` 중 레지스트리 허용값 선언이 없는 인자의 값이다. E1 가짜 LLM 전용이므로 E2에서는 견본 그대로 둬도 된다.
   - `sources`: 소스 되묻기 자동 답. `default`(그 밖 대상의 소스 id)와 `by_placeholder`(`inst_b: b0` · `inst_c: cm_prd`처럼 자리표 → 소스 id).
   - `answers`: 소스 id → 자동 답 문구. 라우트가 그 소스 하나로 읽는 말이어야 한다(D-322 부기 ①). D-329 확정 표는 소스 간 공유 단어가 0이라 그 소스의 `terms` 한 단어(예 b0 「은행 제니퍼」)면 하나로 읽힌다.
   - `canaries`: 누출 관문 카나리아. 운영 데이터에 절대 나오지 않을 고유 문자열 1~2개(예: 임의 난수 토큰)를 넣는다.
3. 채움 검사(시나리오가 쓰는 자리표가 다 있는지 확인). 실측: E1 사본은 `필요 16 · 미등록 없음`으로 exit 0, 일부를 비운 파일은 미등록 15개 목록을 내고 exit 1.

```bash
python -c "import re,sys,yaml; from pathlib import Path; need=set(re.findall(r'\{\{(\w+_(?:\d+|[a-z]))\}\}', Path('testdata/apm_bench/scenarios.yaml').read_text())); v=(yaml.safe_load(open(sys.argv[1])) or {}).get('values') or {}; miss=sorted(n for n in need if not str(v.get(n) or '').strip()); print(f'필요 {len(need)} · 미등록', miss or '없음'); sys.exit(1 if miss else 0)" ~/apm_bench_local/placeholders.e2.yaml
```

### A.2 `term_policy` 검토 (기본 거부)

1. 저장소 정본 `testdata/apm_bench/term_policy.yaml`은 빈 목록이다. 이 상태면 이름 토큰이 전부 형식 보존 가짜 값으로 바뀐다. 검토 절차는 파일 머리 주석 1~4를 따른다.
2. 로컬 사본을 만든다: `cp testdata/apm_bench/term_policy.yaml ~/apm_bench_local/term_policy.local.yaml && chmod 600 ~/apm_bench_local/term_policy.local.yaml`.
3. 운영자가 사람·조직·고객·고유 시스템을 식별하지 않는 업무 일반 단어만 `terms:`에 넣는다(업무 분류어·계층 약어·환경 약어 등). 한 항목은 토큰 하나여야 하고 숫자만인 항목은 받지 않는다(형식이 틀리면 run 시작 전에 exit 2). 한↔영 약어 쌍은 둘 다 넣거나 둘 다 뺀다.
4. 넣은 단어 수와 검토자·날짜는 폐쇄망 반출 기록에 남긴다(단어 자체는 `report.md` §11에 수로만 나온다).
5. 실행할 때 `--term-policy ~/apm_bench_local/term_policy.local.yaml`로 넘긴다. 저장소 파일을 고치지 않는다.
6. ASCII 2자 토큰(`db`·`ap` 등)과 camelCase 결합 소문자형도 기본 거부다. 토큰 사전은 반출 레코드의 문자열 값 전부에 적용되므로, 이름 토큰과 같은 일반 코드 값(`domain`·`db` 등)도 허용 목록에 없으면 가짜 값으로 바뀐다(§3.8 구현 부기). 반출본 `report.md` §11 위생 주의에서 손실을 확인하고 다음 회차 허용 목록에 반영한다.

### A.3 뷰 서버 버전

허용 API로는 뷰 서버 버전을 얻을 수 없다. 운영자가 제니퍼 콘솔이나 서버 정보에서 확인해 `--view-server-version`으로 넣는다. **5.6.2.18 미만이면 실행하지 않고 보고한다**(그 아래는 토큰 호출마다 Jetty 세션이 쌓인다 · §3.7). 버전 값은 `run.json`에 남고, 반출물에도 그대로 실린다(운영 이름이 아님).

### A.4 벤치 전용 게이트웨이

- 벤치가 자기 게이트웨이 서브프로세스를 띄운다(§10.1). 운영 게이트웨이(초당 5회)와 별개로 **초당 2회 · 폴러 끔 · 127.0.0.1 임시 포트 · 세션 스풀**이므로 뷰 서버가 받는 부하는 합쳐서 초당 7회 이하다.
- 실 뷰 서버 URL·토큰은 A.0 체크아웃의 `apm_gateway/.env`에만 둔다(셸 export는 벤치가 걷어낸다). 운영 게이트웨이 프로세스·포트(9096·9097)·설정 파일은 건드리지 않는다. 벤치는 9096·9097·9099·8000·8080 포트를 쓰지 않는다.
- run 메타의 `gateway_settings`는 `{"rate_per_sec": 2.0, "api_timeout_sec": 10.0}`로 기록된다.

### A.5 허용 범위와 자동 중단 (벤치가 자동으로 지킨다 · §3.7 · `scripts/apm_bench/guard.py`)

| 항목 | 구현 값 |
|---|---|
| 사전 점검 | 뷰 서버 버전 5.6.2.18 이상(미입력·미달은 exit 2) · 본체 두 평면 비과금(워커 `fabrix·ollama·mlx` / 오케스트레이터 `vllm·mlx` — 아니면 아무것도 띄우기 전에 exit 2 · D-222) |
| 예산 | 소스당 **3,000호출/회**(게이트웨이 감사 줄 `api_calls`의 소스별 합) — 넘으면 즉시 중단(`budget:<소스>`) |
| 429 | **1회**라도 나면 중단(`quota_429`) — 감사 줄 `error=apm_quota_exceeded` 또는 게이트웨이 DEBUG 응답 줄 `status=429`(팬아웃 도구의 도메인별 429 포함) |
| 타임아웃 | 비율 **5% 초과**면 중단(`timeout_ratio`). 분모 하한 20(처음 20호출 안에서 1건은 통과, 2건이면 중단). 타임아웃은 `source_unavailable`이면서 경과가 상한(10초)의 90% 이상인 호출, 또는 응답 줄이 없는 호출이 있으면서 도구 경과가 상한의 90% 이상인 호출이다. 분자·분모 모두 도구 호출 수다 |
| 지연 | 시작할 때와 10시나리오마다 `gateway_health` 기준선을 잰다. E2는 기준선의 **3배 초과**면 중단(`latency_3x`). E1만 목 지터 때문에 절대 200ms 초과를 함께 요구한다 |
| 무거운 등급 | 맨 끝에 **최대 5개**. 앞 단계에 중단·타임아웃·429가 하나라도 있으면 건너뛴다(`heavy_gate`). 1회차는 `--skip-heavy`로 끈다(`heavy_off`) |
| WAS 접촉 | 실행 중 요청 상세·로드된 클래스·환경변수는 **API(표 A 경로)당 1회**만, 그리고 `inst_w`만 대상으로 한다(`was_once:<경로>`) |
| E2 건너뜀 | 시나리오의 `e2_skip`(현재 3개 — WAS 1회 제한분) |
| 반복 | 시나리오 1회(반복 측정 없음) · 감시 스레드 0.5초 간격 |

중단되면 남은 시나리오는 `aborted:<사유>`로 기록되고 산출물은 정상적으로 남는다(`run.json` `abort_reason`). **중단된 회차는 다시 시도하지 않고** 사유를 운영 조직과 공유한다.

### A.6 실행 순서

1. 시간대: 업무 외 시간(정확한 시간대는 운영 조직과 협의 · §6 ①). 시작 전에 운영 게이트웨이와 뷰 서버가 정상인지 운영자가 확인한다.
2. 정적 확인: `python -m scripts.apm_bench --dry-run`(exit 0)과 A.1 채움 검사(exit 0).
3. **1회차(무거운 등급 끔)**:
   ```bash
   python -m scripts.apm_bench --run --env e2 \
     --placeholders ~/apm_bench_local/placeholders.e2.yaml \
     --term-policy ~/apm_bench_local/term_policy.local.yaml \
     --view-server-version <운영자 확인 값> --skip-heavy --probe \
     --out ~/apm_bench_out/r1
   ```
   예상 호출 수는 약 1,300회(§3.7)이고, 초당 2회 상한이라 호출만으로 10분 이상 걸린다. E1 소요(약 10분)는 참고만 한다.
4. 1회차가 중단 없이 끝났고 타임아웃·429가 0이면, 다음 회차에서 `--skip-heavy`를 빼고 돈다(무거운 등급은 맨 끝·최대 5개).
5. 끝나면 확인한다: `ls apm_gateway/var`가 없어야 하고, 벤치가 띄운 프로세스가 남지 않아야 한다(`ps`에 `apm_bench._serve`·`python -m apm_gateway` 중 벤치 세션 것이 없을 것).

### A.7 반출 관문과 목록

- **관문**: `<out>/leak_check.json`이 `"passed": true`이고, 디렉터리에 아래 6종이 모두 있을 때만 반출한다. 관문에 실패하면 `leak_check.json`만 남는다. 이 경우 반출하지 않고 `violations[].rule`을 외부망에 말로 알린다(값은 알리지 않는다).
- **반출 목록(6종)**: `run.json` · `trace.jsonl` · `probe.jsonl` · `coverage.md` · `report.md` · `leak_check.json`.
- **반출 금지**: 자리표 로컬 파일 · 허용 목록 로컬 사본 · 본체·게이트웨이 `.env` · 게이트웨이·서버 로그 · 캡처(`capture.jsonl`) · 체크포인트 · 세션 임시 디렉터리 · `--debug-raw`(E2는 거부됨) · 화면 캡처 · `logs/` 아래 파일 일체.
- **`logs/`(본체 감사 로그 등)**: 운영 이름이 그대로 남는다. 감사 로그는 끄지 않고(보안 원칙 — 모든 질의 실행은 감사 대상) 내부망 실행 머신의 보존 정책을 따른다. 외부 매체로 옮기지 않는다(2026-10-13 확정 · D-323 부기).
- 반출 전에 `report.md` 머리의 `SHA`와 `fingerprints`를 확인한다. 외부망은 같은 SHA로 F1~F4를 다시 렌더한다(few-shot 원문은 반출하지 않는다).

### A.8 회차 교정 계획으로 넘기는 법

1. 외부망에서 반출 디렉터리를 저장소 밖에 둔다(저장소 `results/`에 넣지 않는다).
2. `report.md` §0(holdout 턴 정확도) · §2 · §4(코드 교정 dev) · §7(few-shot 수정 후보 dev)과 `coverage.md` 예문 커버리지 × 실패율로 대상을 나눈다: ① few-shot 대상(§3.5 「예」) ② 코드 대상(`code:*`) ③ 승격 대상(`--compare`의 「결정적 처리 승격」).
3. 새 계획서 `plans/NN-TODO-apm-bench-round<N>.md`를 연다(ITAM 벤치 135 → 139·146·149·150·151 방식). 근거 run_id·SHA, 대상 실패 ID(dev만), 위치, 수용 기준(다음 회차 holdout 턴 정확도 상승)을 적는다.
4. 그 계획 안에서 §10.5 루프를 돈다. 수정은 `fewshot_log.yaml`에 기록하고, 다음 내부망 회차 뒤에 `--compare`로 판정한다.

### A.9 외부망 E1 완주 기록 (2026-10-13)

외부망에서 A.1~A.7을 E1(목 뷰 서버 · 가짜 LLM)으로 한 번 완주했다. E2 전용 단계는 아래처럼 대체했다.

| 단계 | E1에서 한 것 | 결과 |
|---|---|---|
| A.0 체크아웃·설정 | 같은 저장소(SHA `ee13b47` + 미커밋 W0~W7) · 본체 `.env` 없음(코드 기본값) · `apm_gateway/.env` 없음 → E1은 목 뷰 서버를 벤치가 자동으로 띄운다 | 해당 없음 |
| A.1 자리표 | `placeholders.e1.yaml`을 세션 임시 디렉터리로 복사(권한 600) — 운영 인벤토리 대신 견본 값 그대로 | 채움 검사 `필요 16 · 미등록 없음` exit 0. 일부 비운 사본은 미등록 15개 목록 · exit 1 |
| A.2 허용 목록 | 로컬 사본에 `terms: [WAS]` 1개 | `report.md` §11 「허용 목록 1개」. 목록에 없는 업무명(`주문`)은 형식 보존 가짜 값으로 바뀌어 반출본에 원문이 없음을 확인 |
| A.3 버전 | E1은 버전을 검사하지 않는다(`뷰 서버 e1-mock(해당 없음)`). 대신 `--env e2`에 버전 미입력·`5.6.2.1`(미달)을 주어 둘 다 exit 2로 run 전에 멈추는 것을 확인 | 통과 |
| A.4·A.5 게이트웨이·자동 중단 | 벤치 전용 게이트웨이 자동 기동(초당 2회 · 127.0.0.1 임시 포트) | 중단 사유 없음 · 타임아웃 0 · 429 0 · 기준선 742.8ms |
| A.6 ③ 1회차 | `python -m scripts.apm_bench --run --env e1 --placeholders <사본> --term-policy <사본> --skip-heavy --probe --out <out>/kit` | 아래 |
| A.6 ⑤ 정리 | `ls apm_gateway/var` 없음 · 벤치 프로세스 잔존 없음 | 통과 |
| A.7 관문 | `leak_check.json` `passed: true` · 6종 모두 생성 | 통과 |

1회차 실측: exit 0 · 620.7초 · `턴 : 실행 130 · APM 관측 109 · 건너뜀 {'heavy_off': 5}`(`APM-HEALTH-untargeted`·`APM-RANK-tps-asc`·`APM-RANK-failrate-full`·`APM-FLEETEV-fatal`·`APM-FLEETEV-oom-full` — 근거 수준 `skipped`) · `호출 : 소스별 {'bank': 46, 'common': 471, 'legacy': 45} · 타임아웃 0 · 429 0 · 기준선 742.8ms · P층 19회` · **`턴 정확도 87.1% (108/124) · dev 86.9% (73/84) · holdout 87.5% (35/40)`** · `축 : source 99.1% · views 100.0% · target 97.3% · window 90.2% · args 100.0%` · `대표 분류 {'window_wrong': 13, 'target_unresolved': 3, 'source_miss': 1, 'empty_result': 1}` · 근거 누락 0 · 관문 통과 · `run.json` `skip_heavy: true`. 무거운 등급을 뺀 것 말고는 §10.3 ③ 전체 run과 같은 실패 집합이다(holdout 35/40 동일).

키트 완주를 위해 이번에 CLI에 더한 것(2026-10-13 정식 옵션으로 유지 확정 · D-323 부기): `--skip-heavy`(1회차 무거운 등급 끄기)와 `--term-policy <파일>`(허용 목록 로컬 사본 경로 — 정본 파일 주석은 「로컬 사본을 경로로 넘긴다」인데 경로 인자가 없었다). 자리표·허용 목록 파일은 본체 기동 **전에** 읽으며, 없거나 형식이 틀리면 `[중단] 자리표·허용 목록 파일을 읽지 못했다`로 exit 2다(이전에는 없는 자리표 파일에서 traceback, 허용 목록 형식 오류는 run이 끝난 뒤에야 드러났다).

---

## 변경 이력

- **v1.2**(2026-10-13) — 외부망 구현 W0~W7(`scripts/apm_bench/` 14모듈 · 본체 emit 3지점 · 테스트 10파일 248건) · 독립 검증 지적(High 1 · Medium 4 · Low 6) 교정 · §10 실행 방법 · 부록 A 폐쇄망 키트 · 상태 TODO → WIP(내부망 W8 잔여) · 파일명 `152-TODO-…` → `152-WIP-…`.
- **v1.1**(2026-10-12) — 인터뷰 확정 반영: 정확도 중심 · API 전수(경로 × 선택 인자) 시나리오 · 개발 70/홀드아웃 30 · 운영 뷰 서버 허용 범위(§3.7) · 반출 근거 묶음 · 토큰 단위 치환 + 일반 단어 허용 목록 · few-shot 위치 F1~F4 · `fewshot_copy` · 개선 루프(§3.9) · D-323.
- **v1.0**(2026-10-12) — 최초 작성.
