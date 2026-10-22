# 190. SRE 조사 APM 호출 지연 교정 — 해소 소스 한정 · X-View 사전 확인 · APM 전용 타임아웃

> **작성일**: 2026-10-20 · **v1.1**(완료 기록)
> **상태**: **완료 · 커밋 전**(2026-10-20 · W0~W5) — 폐쇄망 재측정 대기(§6). 이탈: §3.2 ① 시 단위 통계로 「판정」 → 판정 불가(None)로 보수 해석 · W3 권장값 60 → 30(스텝 대기 상한 30초 고정 — §7) · 한계 문장에 `[한계]` 접두 없음(브리핑 한계 목록이 이미 구분)
> **계획 시작 SHA**: `7ef7412`
> **회귀 대상 파일**: `sre_agent/sre_agent/settings.py` · `sre_agent/sre_agent/application/{playbook_steps,playbook_runner,investigation_dispatcher}.py` · `sre_agent/sre_agent/infrastructure/mcp_tool_client.py` · `sre_agent/.env.example` · `.env.example` · `sre_agent/tests/test_plan190_{apm_scope,apm_timeout,verify}.py`
> **성격**: 행동 변경(D-337 ⑥) — 구조 리팩터와 섞지 않는다.
> **관련 결정**: **D-344**(이 계획의 정본 · 2026-10-20 등재) · D-341(플레이북 조사) · D-296 부기(plans/164 W7 — `xview_confirm_over` 옵트인 · ④ 자체 상한 없음 유지) · D-274(게이트웨이 경계 — 양방향 import 0) · D-123(조사 상태 어휘 불변) · D-213(타임박스)
> **패키지**: `sre_agent/`(주) · `apm_gateway/`(변경 없음 예상 — 계약만 소비) · 본체 `.env.example` 문서 1곳

## 1. 발단 — 폐쇄망 실측(2026-10-20)

SRE 조사가 정상 동작하지 않았다. sre_agent 로그에 `사전수집 MCP 세션 실패 (URL): ReadTimeout`와
`사전수집 도구 실패 apm_*: Timed out while waiting for response … 20.0 seconds`가 반복됐다. 원인을 단계적으로 확정했다.

| 관찰 | 값 |
|---|---|
| 연결·프록시·토큰 | 정상(`gateway_health` 3소스 ok · 서비스 env에 프록시 없음) |
| 게이트웨이 감사 `apm audit` | 도구 1건 `elapsed_ms` 469,979~656,449 · 소스당 `api_calls` 121 |
| 응답 스필 로그 | 35건 `path=/api/transaction/time`(X-View) · 3건 `/api/dbsearch/error` |
| 대상 인스턴스 | 같은 `instance_name`이 **b0·cm_prd 양쪽**에 1개씩(서로 다른 서버) |
| b0 호출 소요 | X-View 조각 1회 약 4초(4MB 초과 응답) · cm 약 0.2~1.3초 |
| 게이트웨이 ASGI 예외 | 103건 전부 `anyio.ClosedResourceError`(클라이언트 20초 포기 뒤 잔여) |
| 조사 감사(당일) | accepted 348 · done 246(중앙 182s · 최대 901s) · **stuck_watchdog 126** · restart_failed 39 |
| 설정 | `INVESTIGATION_TIMEOUT_SECONDS=900` · `INVESTIGATION_MAX_CONCURRENT=2` · `NOISE_INVESTIGATION_FOLLOWUP_TIMEOUT_SECONDS=900` |

**근본 원인**:
1. 플레이북 APM 스텝이 `instance_name`만 넘긴다(`playbook_steps.py` `_incident_health_call`·`_baseline_calls`·S0 비교 등).
   게이트웨이 `resolve_instance_name`은 **고른 소스 전부**에서 이름 정확 일치를 찾는다. 그래서 같은 이름을 가진
   **다른 존의 다른 서버까지** 조회한다. 이는 지연 문제이면서 **정합성 결함**이다(다른 서버 데이터가 섞인다).
2. `apm_app_health`(기간 모드)는 구간 p50/p95를 내려고 X-View를 **1분 조각 상한 없이** 부른다(`apm_core.xview`).
   lookback 60분이면 (소스·도메인)당 60회다. 사건 구간 + 기준선마다 반복된다.
3. 플레이북 APM 호출은 사전수집 상한 `evidence_prefetch_timeout_seconds`(20초)를 공유한다(`make_apm_batch_caller`).
   클라이언트가 포기해도 게이트웨이는 끝까지 돈다. 승격 전 취소가 전파되지 않아 b0 부하가 누적된다.
4. 파생: 조사 1건이 길어져 동시 상한 2가 포화됐다. 대기 중 워치독(1800초)에 걸리는 잡이 대량으로 생겼다.
   후속 대기(900초)가 조사 예산(900초)과 같아 브리핑을 놓쳤다.

별건(이 계획 밖 · §7): 게이트웨이 재기동 후 b0 인벤토리 콜드 적재 26,870초.

## 2. 범위

| # | 항목 | 대상 |
|---|---|---|
| W1 | **해소 소스 한정** — 대상 확정 시 얻은 인스턴스의 `source_id`를 대상과 함께 들고 다니고, 이후 모든 APM 도구 호출에 `source_ids=[해당 소스]`를 싣는다 | `sre_agent/application/playbook_steps.py`(대상 dict에 `source_id` · 호출 인자 조립 공용 헬퍼) · `playbook_runner.py`(targets 전달) |
| W2 | **X-View 사전 확인** — 기간 모드 `apm_app_health` 호출에 `xview_confirm_over`를 싣는다. `status: confirm_required`를 받으면 그 스텝은 X-View 전수 조회를 하지 않고 **시 단위 통계로 대체**한다(아래 §3.2). 대체한 사실은 `[한계]`로 브리핑에 노출한다(침묵 강등 금지) | `playbook_steps.py` · `settings.py`(`apm_xview_confirm_over`) |
| W3 | **APM 전용 호출 상한** — `apm_tool_timeout_seconds: float \| None = None`. None이면 종전대로 `evidence_prefetch_timeout_seconds`(비트 동일) | `settings.py` · `infrastructure/mcp_tool_client.py`(`make_apm_batch_caller`) · `sre_agent/.env.example` |
| W4 | **운영 문서** — 후속 대기는 조사 예산보다 커야 한다(권장 = 워치독 임계 `max(600, 2×INVESTIGATION_TIMEOUT_SECONDS)`). 본체 `.env.example`의 `NOISE_INVESTIGATION_FOLLOWUP_TIMEOUT_SECONDS` 주석과 sre_agent `.env.example`의 `INVESTIGATION_TIMEOUT_SECONDS` 주석에 정렬 규칙을 적는다 | `.env.example` 2곳(코드 변경 없음) |
| W5 | 기록 — D-344 본문·색인·CHANGELOG · D-341 부기 1줄 · INDEX 상태 · `docs/18`(실수 이력 대상이면) | docs |

**범위 밖**: 게이트웨이 코드 변경(필요하다고 판단되면 중단하고 보고) · 동시 상한·조사 예산 값 변경(운영 판단) ·
noise_gate 후속 poll 로직 변경 · b0 콜드 적재 원인 조사(§7).

### 2.6 W0 실측(2026-10-20 · 기준 `7ef7412`)

**게이트웨이 도구 시그니처** — `apm_gateway/apm_gateway/interface/server.py`의 `@mcp.tool()` 함수 인자를 AST로 전수 추출했다
(import 없이 — D-274 ③). 결과를 `playbook_steps.APM_SOURCE_IDS_TOOLS`·`APM_XVIEW_CONFIRM_TOOLS`로 고정했고,
`tests/test_plan190_apm_scope.py::test_allowlists_match_gateway_signatures`가 같은 추출로 사본 일치를 단언한다.

| 도구 | `source_ids` | `xview_confirm_over` | 비고 |
|---|:-:|:-:|---|
| `apm_instance_map` | O | — | `targets`(다건)도 받음 — 이하 데이터 도구 전부 동일 |
| `apm_app_health` | O | **O** | 사전 확인 `_xview_confirmed` → `ApmTools.app_health_preflight` |
| `apm_runtime_health` · `apm_resource_pool` · `apm_slow_transactions` · `apm_active_services` · `apm_events` | O | — | |
| `apm_status_stats` · `apm_metrics` · `apm_transaction_trace` | O | — | 플레이북 미사용 |
| `apm_source_changes` · `apm_change_impact` · `apm_period_compare` | O | — | |
| `apm_transaction_profile` | **—** (단수 `source_id`) | — | profile_ref의 `source_id`를 그대로 넘긴다 — 허용 목록 밖 |
| `apm_job_status` · `apm_job_cancel` · `apm_job_read` | — | — | 작업 관리(대상 인자 없음) |

**`confirm_required` 봉투**(`apm_gateway/application/tools.py` `app_health_preflight` · 상수 `CONFIRM_REQUIRED`) —
`ApmCore.ok()` 표준 봉투(`rows: []` · `row_count: 0` · `queried_at` · `source_kind` · `source` · `tool` · `window` ·
`limits: ["[한계] 사전 확인 — 예상 X-View 호출 N회가 기준 M회를 넘어 …"]`) + `status: "confirm_required"` ·
`expected_xview_calls` · `expected_api_calls` · `xview_confirm_over`(정규화된 상한) · `instance_count` · `estimate{api_calls, seconds, basis}`.
`error` 키가 없어 sre_agent `parse_response`는 **성공 응답**으로 본다 — 별도 판별이 없으면 `rows` 0건 → 통계 None으로 흘러간다
(S0′ 역방향은 `was_signals` 부재를 「정상」으로 읽는 결함 경로였다 — W2에서 판정 불가로 교정). 예상 호출 = ⌈창 ÷ 1분⌉ × (소스·도메인 군).
기준 이하이거나 현재값 모드(`reference_time` 없음)면 사전 확인은 None → 종전 조회 그대로.

**인스턴스 행의 소스** — `apm_instance_map` 행(`InstanceTools._instance_row`)은 `source_id`를 싣는다(query·hostname·business 경로 공통).

**플레이북 APM 호출 지점**(`sre_agent/application/playbook_steps.py` · 교정 전 인자 → W1·W2 뒤 인자)

| 스텝(함수) | 도구 | 교정 전 인자 | W1·W2 뒤 |
|---|---|---|---|
| S0 `resolve_was_targets` 탐색 | `apm_instance_map` | `query` | 그대로(대상 확정 전 탐색 — 소스 미상이 정상 · 한계 없음) |
| S0 호스트 확장 | `apm_instance_map` | `hostname` | + `source_ids=[알람 행 source_id]`(알람 서버의 소스) |
| S0 업무 확장 | `apm_instance_map` | `business` | 그대로(동료 탐색 — 나온 행마다 자기 `source_id`) |
| S0 범위 비교 · 기준선(`_incident_health_call`·`_baseline_calls`) | `apm_app_health` | `instance_name`·`reference_time`·`lookback_minutes` | + `source_ids` · + `xview_confirm_over` |
| S0 사전 확인 초과 대체 | `apm_period_compare` | (신규) | 사건창 vs 같은 길이 직전 구간 · `source_ids` |
| S0′ `host_to_was` 탐색 | `apm_instance_map` | `hostname` | 그대로(탐색) |
| S0′ 사건창 | `apm_app_health` | 위와 같음 | + `source_ids` · + `xview_confirm_over` · 초과면 대체 `apm_period_compare` |
| S1 `golden_signals` | `apm_app_health`(사건·기준선) · `apm_period_compare`(기준선마다) | `instance_name`·창 | + `source_ids` · app_health + `xview_confirm_over`(초과면 §3.2 ① — 기존 비교 사용) |
| S2 `runtime_and_pool` | `apm_runtime_health` ×2 · `apm_resource_pool` | `instance_name`(·창·metrics) | + `source_ids` |
| S3 `slow_tx_breakdown` | `apm_slow_transactions` · `apm_transaction_profile` | `instance_name`·창·`n` / profile_ref | 앞 + `source_ids` · 프로파일 그대로 |
| S5 `related_alarms` · `events_errors` | `apm_events` | `instance_name`·창(·record·n) | + `source_ids` |
| S7 `recent_changes` | `apm_source_changes` · `apm_change_impact` | `instance_name`·`reference_time` | + `source_ids` |
| `active_services` | `apm_active_services` | `instance_name`·`n` | + `source_ids` |

## 3. 설계

### 3.1 W1 해소 소스 한정
- 대상 확정 스텝(S0 · `apm_instance_map(query=알람 인스턴스)` → `hostname`/`business` 확장)과 역방향 스텝
  (`apm_instance_map(hostname=…)`)이 받은 행의 `source_id`를 `(instance, host, source_id)`로 보존한다.
  `ctx.targets`의 각 dict에 `source_id` 키를 더한다. 키가 없거나 비면 종전 동작(소스 미지정)으로 둔다.
  그 경우 `[한계] APM 소스 미확정 — 전 소스 조회`를 남긴다.
- 같은 이름이 여러 소스에서 나오면(대상 확정 결과 기준) 대상 행이 나뉜다. 알람 인스턴스 행은 S0의
  `alarm_row.source_id`를 쓴다. 비교 대상은 각 행의 `source_id`를 쓴다.
- APM 호출 인자를 조립하는 지점은 **공용 헬퍼 1곳**으로 모은다(`_apm_args(target, **kw)` 류). 이렇게 하면
  호출 지점 약 30곳이 같은 규칙을 쓰고, 테스트가 헬퍼 하나만 고정하면 된다. 이 리팩터는 W1의 동작 변경과
  한 Wave 안에서 한다. 동작 불변 리팩터를 따로 떼지 않는다 — 인자가 바뀌는 지점과 정확히 같기 때문이다.
- `source_ids`를 받지 않는 게이트웨이 도구가 있으면 싣지 않는다. 시그니처는 `apm_gateway/interface/server.py`에서
  실측한다. 실측 결과를 헬퍼에 도구 허용 목록으로 고정한다.

### 3.2 W2 X-View 사전 확인
- 설정 `apm_xview_confirm_over: int | None`. **기본값 10**(lookback 10분 분량)이다. 기본 off 원칙의 예외이며,
  근거는 config 주석과 D-344에 둔다: 기본 off면 보고된 정체(도구 1건 8~11분)가 그대로 남는다.
  D-296 ⓓ(플래그 없음) 선례와 같다. `None` 또는 0 이하는 종전 비트 동일(인자 미전송)이다.
- 대상: 기간 모드(`reference_time` 있음) `apm_app_health` 호출 전부(사건 구간·기준선·S0 비교·역방향).
- `confirm_required` 응답 처리 — 대체 경로는 구현자가 아래 순서로 판단한다.
  1. 그 스텝이 이미 `apm_period_compare`(시 단위 통계 · X-View 없음)를 부르는 스텝이면, p95를 N/A로 두고
     시 단위 통계(호출·실패율·평균 응답)로 판정한다.
  2. 아니면 `apm_period_compare`를 사건 구간과 같은 길이의 직전 구간으로 1회 불러 대체한다.
  3. 둘 다 불가하면 그 대상의 해당 신호를 `missing`으로 둔다.
  모든 경우 `[한계] X-View 사전 확인 초과(예상 N회 > 상한 M) — 구간 p95·에러율 생략, 시 단위 통계로 대체`를 남긴다.
  예상 호출 수는 응답 `expected_xview_calls`에서 읽는다.
- 판정 어휘·`verdict_source`는 D-341 규약을 따른다. p95 기반 판정(`p95_ratio`)이 불가하면 그 판정은
  "판정 불가"다. "정상"으로 강등하지 않는다.

### 3.3 W3 APM 전용 상한
- `make_apm_batch_caller`는 `apm_tool_timeout_seconds`가 있으면 그 값을 쓰고, 없으면 종전 값을 쓴다.
- 플레이북 수집 예산 `sre_playbook_budget_s`(기본 120)와의 관계를 `.env.example` 주석에 적는다. 값 자체는 바꾸지 않는다.
- 폐쇄망 권장값은 `.env.example` 주석에만 적는다(예: `APM_TOOL_TIMEOUT_SECONDS=60` · W1·W2 적용 뒤 실측으로 조정).

## 4. Wave

| Wave | 내용 | 담당 | 완료 조건 |
|---|---|---|---|
| W0 | 실측 — 게이트웨이 APM 도구별 `source_ids`·`xview_confirm_over` 수용 여부(`inspect`/시그니처) · `confirm_required` 봉투 shape(게이트웨이 테스트 픽스처 또는 코드) · 플레이북 APM 호출 지점 목록 | implementer | 실측표를 계획서 §2.6(신설)에 기록 |
| W1 | 해소 소스 한정 + 공용 인자 헬퍼 | implementer | 단위 테스트: 같은 이름이 2소스에 있을 때 각 호출에 해당 `source_ids`만 실림 · 소스 미확정 시 종전 인자 + `[한계]` |
| W2 | X-View 사전 확인 + 대체 경로 | implementer | 테스트: 상한 이하=인자 전송·데이터 정상 처리 · `confirm_required`=X-View 재호출 0 + 대체 호출 + `[한계]` · `None`=인자 미전송(비트 동일) |
| W3 | APM 전용 상한 · W4 문서 | implementer | 테스트: 설정 유무에 따른 타임아웃 값 · `.env.example` 키 정합(`settings` 필드 대조) |
| W5 | 독립 검증 · 회귀 · 기록 | verifier · team-lead | 아래 §5 |

## 5. 검증

- 테스트는 가짜 배치 호출자(목)로 한다. 실 LLM·실 게이트웨이는 불필요하다.
- Wave 중간: 자기 테스트 + `python scripts/regress.py --no-tests --files …`. 단, `sre_agent`는 자체 venv라
  `sre_agent/.venv`로 pytest를 돌린다. 루트 `regress.py`가 sre_agent를 수집하지 않으면 그 사실과 대체 실행 명령을 보고한다.
- 계획 끝: 모듈 단위 회귀 1회(`--base 7ef7412`) · `arch_check`(sre_agent 자체 `scripts/arch_check.py` 포함) ·
  ruff·mypy(바꾼 파일).
- 회귀 범위 줄과 `[전체 회귀 권고]` 블록은 보고에 그대로 옮긴다.

## 6. 폐쇄망 배포 메모(2026-10-20)

**선행 배포 확인**: `ee9e4af`(`_skip_if_stale` — `grep -n "_skip_if_stale" sre_agent/sre_agent/application/investigation_dispatcher.py`).
config 정본·`.env`는 덮어쓰지 않고 diff로만 반영한다(`docs/18` 2026-10-13 항목).

| 반영 파일 | 확인 grep 심볼 |
|---|---|
| `sre_agent/sre_agent/application/playbook_steps.py` | `def _apm_args` · `APM_SOURCE_IDS_TOOLS` · `def _confirm_fallback` · `XVIEW_CONFIRM_LIMIT` |
| `sre_agent/sre_agent/application/playbook_runner.py` | `target_sources=[src for _, src in picked]` · `notes.extend(n for n in list(ctx.limits)` |
| `sre_agent/sre_agent/application/investigation_dispatcher.py` | `xview_confirm_over=self._settings.apm_xview_confirm_over` |
| `sre_agent/sre_agent/settings.py` | `apm_xview_confirm_over: int \| None = 10` · `apm_tool_timeout_seconds` |
| `sre_agent/sre_agent/infrastructure/mcp_tool_client.py` | `def _apm_timeout_seconds` |
| `sre_agent/.env.example` · 루트 `.env.example` | `APM_XVIEW_CONFIRM_OVER` · `APM_TOOL_TIMEOUT_SECONDS` · `plans/190 W4`(주석 — 배포 필수 아님) |

**권장 `.env`**(diff로만 반영):
- sre_agent: `APM_XVIEW_CONFIRM_OVER=10`(미기재여도 코드 기본 10 · 끄려면 `0`) · `APM_TOOL_TIMEOUT_SECONDS=30`(스텝 대기 상한 30초와 같게 — 30 초과는 효과 없음 · 미기재 = 20초 공유).
- 본체: `NOISE_INVESTIGATION_FOLLOWUP_TIMEOUT_SECONDS=1800`(현행 `INVESTIGATION_TIMEOUT_SECONDS=900` 기준 = max(600, 2×900)).

**재측정 항목**: 게이트웨이 `apm audit`의 도구 1건 `elapsed_ms`·소스당 `api_calls`(b0·cm 동시 조회가 사라졌는지) · 스필 로그의 `/api/transaction/time` 건수 · 브리핑 한계에 「X-View 사전 확인 초과」·「APM 소스 미확정」이 얼마나 나오는지 · 조사 감사 `stuck_watchdog` 비율.

## 7. 후속(이 계획 밖)

- 플레이북 스텝 대기 상한(`playbook_runner.DEFAULT_STEP_TIMEOUT_S = 30` · 설정 키 없음)을 설정으로 열지 — 지금은 `APM_TOOL_TIMEOUT_SECONDS` 30 초과가 무효다.
- 알람 행 선택에 페이로드 `meta.hints.source_id` 사용(현재는 첫 정확 일치 행).

- b0 인벤토리 콜드 적재 26,870초 원인(API 351회면 수 분이어야 함) — DEBUG `apm http elapsed_ms` 실측이 필요하다. 재기동이 수반되므로 운영 일정에 맞춘다.
- 클라이언트 포기 시 게이트웨이 작업 취소 전파(`wait_seconds` 승격·취소 계약) — 게이트웨이 계약 변경이라 별도 계획으로 둔다.
- 동시 상한·조사 유입량(하루 348건) 조정 — 운영 판단.
