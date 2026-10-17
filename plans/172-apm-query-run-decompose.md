# 172. `run_apm_query` 분해 — Compose Method · 호출 구성 표 주도 dispatch(O2)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — `run_apm_query` 450줄·cc 189 → 48줄·cc 10 · `_RunCtx` + 단계 함수 18 + `_CALL_BUILDERS` 5종 · 새 함수 최대 cc 26 · 계획 끝 회귀 실패 0(모듈 단위) · verifier 차분 0(행렬 81 · 넓은 차분 2,249 테스트) · Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 6 · **166 단계 6b · 스멜 O2**(W4 거대 함수 분해 · 단독 계획 — 6a G1 `plans/171`과 병렬 · 파일 겹침 0)
> **성격**: 동작 불변 구조 리팩터(D-337) — 응답 dict · 거부 코드 · 로그 문구 · 게이트웨이 호출 순서·인자 바이트 불변 · 테스트 단언·스냅샷 갱신 금지(166 §0.1 규칙 4)
> **계획 시작 SHA**: `1ad7c161`(RCA)
> **회귀 대상 파일(누적)**: W1 — `src/orchestration/apm_query.py` *(누적 끝 — 계획 끝 회귀 1회: §4)*
> **관련 결정**: D-337(리팩터 규약 ②seam 보존 · ⑤특성화 · ⑥행동 변경 분리 · ⑦커밋 단위) · D-303(회귀) · D-331(게이트) · D-264 ②(거부 문구에 소스 이름 없음) · D-290 ⑥(대신 조회 없음) · D-322(소스 사다리)
> **적용 스킬**: `.claude/skills/refactoring/`
> **후행**: 단계 7a(O1 — `apm_query.py` God module 분할 · `src/orchestration/apm/`) — 이 계획은 **같은 파일 안 함수 분해만** 하고 새 모듈로 옮기지 않는다.

---

## 1. 범위 (166 §3.3 O2 행 · §5.6 W4 O2 행 — 2026-10-16 `1ad7c161` 재실측)

| 항목 | 166 분석(`7ba5c96`) | 재실측(`1ad7c161`) |
|---|---|---|
| 위치 | `run_apm_query` 2088-2521(433줄) | `src/orchestration/apm_query.py:2292-2741`(450줄) |
| 근사 cc(`smell_metrics.py`) | 184 | **189** |
| 반환 | early return 19 | 반환 13(early 12 + 성공 1) + 내부 클로저 `hosts_of` 1 |
| 호출 구성 분기 축 | `ViewSpec.target` 5종(""·reference·named·fleet·optional) | 어휘는 `VIEW_TARGETS = {"", "optional", "reference", "none", "named"}`(`src/routing/registry.py:91`). 166의 「fleet」은 `_service_scoped(view)` = `target == "none"` **그리고** `service` 인자 보유. `none`인데 `service`가 없으면 기본(대상별) 분기로 간다 |

**처방**(166 그대로): Compose Method + 표 주도 dispatch.
1. 호출 구성 루프(`for vid in views:` · 현 2530-2597)의 분기를 `{target: _calls_xxx}` 함수 dict로 바꾼다 — `reference`·`named`·`none`·`optional`·`""`. `none`은 `_service_scoped`가 거짓이면 기본 함수에 위임한다(종전 분기 순서와 같은 결과 — target은 서로 배타). 알 수 없는 target은 기본 함수로 간다(종전 `if` 사슬의 끝 분기와 같음).
2. 단계 간 상태는 `@dataclass _RunCtx` 1개로 묶는다.
3. 종단 판정(세션 뒤 거부 사슬)은 `_decide_outcome`으로 뽑는다 — **순서 그대로**: `apm_calls_failed` → `_blocked_refusal`(호출 0 ∧ blocked) → `apm_target_unresolved` → `apm_not_queried` → `_accepted_answer` → 성공 결과.
4. Strategy 클래스는 만들지 않는다.

**성공 기준**
- 거부 코드 단언 테스트(전수 grep — §2) 무수정 통과 · 테스트 파일 수정 0 · 스냅샷 갱신 0.
- `run_apm_query` cc ≤ 30(166 §5.8) · 새 함수도 각각 cc ≤ 30 목표(초과 시 근거 기록).
- verifier 차분(변경 전 `1ad7c161` worktree와 나란히): target 5종 × 성공·부분 실패·전부 실패·대상 미해결·소스 불가 경우의 결과 dict·게이트웨이 호출 기록 차분 0.
- `regress.py` 계획 끝 1회 종료 코드 0(원래 실패만) · arch error 0 · overfit 자기 델타 0 · ruff·mypy 신규 0.

**범위 밖**: 다른 함수(`_coerce_arg` — O9 · 6c) · 새 모듈 이동(O1 — 7a) · 동작 변경 후보(발견 시 §5 「후속」에 기록만).

## 2. 설계 제약(팀 리드 실측 · 2026-10-16)

- **patch seam**(테스트가 `apm_query` 모듈 속성으로 바꾸는 심볼 — `setattr`/`patch` grep): `_SESSION_FACTORY` 56 · `link_business_names` 14 · `get_registry` 6 · `link_hostnames` 5 · `poll_job` 3 · `render_view_rows` 2. 모두 같은 모듈 안에서 **전역 이름으로** 읽으면(호출 시점 조회) patch가 닿는다 — 뽑은 함수에서 기본 인자·지역 별칭·`_RunCtx` 필드로 미리 묶지 않는다.
- **소스 텍스트 가드**: `tests/test_orchestration/test_plan132_w6_source_synonyms.py:138` — `apm_query.py` 본문에 `synonym`(대소문자 무시) 문자열 0 · `tests/test_plan166_w1_apm_contract_consumers.py` — `'"instance_unresolved" not in call.error'`·`'"instance_unresolved" in call.error'` 원문 잔존 · `tests/test_orchestration/test_plan134_w2_verify.py:283` — 선택 경로 함수 `getsource`(이 계획 범위 밖 함수).
- **거부 코드 단언**: 29파일(`tests/test_orchestration/test_plan134_*`·`test_plan130_*`·`test_plan125_apm_query.py`·`test_plan147_w2_ladder.py`·`test_plan132_source_selection.py` · `tests/test_api/test_plan163_s3_user_errors.py`·`test_plan147_w3_scope.py` · `tests/test_scripts/test_apm_bench_*` · `noise_gate/tests/test_plan87_apm_consumer.py`·`test_plan144_w4.py` 등).
- **정체성·순서 민감 지점**: `notices`는 같은 리스트 객체에 `+=`(제자리 확장)하고 `SourceMcpError` 거부는 그 객체를 그대로 넘긴다 · `first_hop_notice`는 `notices` 인덱스이며 세션 뒤 교체는 `_named_unresolved` 고지 추가·`disclosures` 계산보다 먼저다 · `ledger`는 `hosts_of`·`_target_texts`·`_resolve_target_texts`가 함께 확장한다 · `task`는 두 번 재바인딩된다(`view_args` · 승계 `targets`) · `now`는 `query_time` 기준 시각으로 재바인딩된다 · `hostnames`는 첫 홉에서 재바인딩된다 · `try`는 세션 블록 전체(대상 해석·첫 홉·호출 구성·실행)를 감싼다.

## 3. 실행

| Wave | 담당 | 내용 | 상태 |
|---|---|---|---|
| W1 | implementer | `apm_query.py` 안 함수 분해(§1 처방) · 자기 테스트(거부 코드 단언 파일 선택분) + `regress.py --no-tests` | 완료 |
| W2 | verifier | 변경 전 worktree와 나란히 차분 하네스 · diff 리뷰 | 완료 |
| 끝 | team-lead | `regress.py --base 1ad7c161 --files src/orchestration/apm_query.py` 1회 | 완료(실패 0) |

## 4. 결과 (2026-10-16)

**바꾼 파일**: `src/orchestration/apm_query.py` 1개(+470 −260 · 4,220 → 4,430줄) — 같은 파일 안 함수 분해만(새 import 0 · 이동 0 · shim·별칭 0 · 테스트 수정 0 · 스냅샷 갱신 0).

| 항목 | 전 | 후 |
|---|---|---|
| `run_apm_query` | 450줄 · cc 189 · 반환 13 | 48줄 · **cc 10** · 공개 시그니처 불변 |
| 파일 cc>50 함수 | 1 | 0(최대는 범위 밖 `_coerce_arg` 50 — O9 · 6c) |
| 새 정의 | — | `@dataclass _RunCtx` · 단계 함수 `_new_run_ctx`·`_select_step`·`_source_step`·`_hosts_of`(종전 클로저)·`_target_hosts_step`·`_window_step`·`_view_args_step`·`_reference_step`·`_call_scope_step`·`_session_step`·`_resolve_texts_step`·`_first_hop_step`·`_build_calls`·`_collect_step`·`_decide_outcome`(cc 24)·`_target_unresolved_refusal`·`_success_result`(cc 26)·`_source_rows_step` · 호출 구성 `_calls_reference`·`_calls_named`·`_calls_none`·`_calls_optional`·`_calls_host` + `_CALL_BUILDERS` — 전부 cc ≤ 30 |

- **seam**: `_SESSION_FACTORY`(`_session_step`) · `link_hostnames`(`_hosts_of`) · `get_registry`(`_new_run_ctx`·`_source_rows_step`)는 호출 시점 전역 조회 · `link_business_names`·`aq.jobs.poll_job`·`render_view_rows`는 바뀌지 않은 헬퍼 안.
- **계획 끝 회귀**(`regress.py --base 1ad7c161 --files src/orchestration/apm_query.py`): 본체 파일 54 · 노드 7 · 정적 875건 → 통과 2,050 · 실패 0 · 에러 0 · 건너뜀 4 · arch·overfit·config_fallback 통과 · ruff 위반 1(기존 I001 · 이번 diff 줄 0) · mypy 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록 없음. 회귀 뒤 `_CALL_BUILDERS` 주석 2줄만 추가(`--no-tests` 재통과).
- **verifier 차분**(`1ad7c161` worktree vs 이 파일만 얹은 worktree · 결과 dict·게이트웨이 호출·로그 레코드): 시나리오 행렬 81건(target 5종 + `none` service 유무 × 성공·부분 실패·전부 실패·대상 미해결·소스 불가 + 경계 — 전부 blocked·`apm_not_queried`·작업 접수·첫 홉 3모드·SourceMcpError 직전 고지) 차분 0 · 넓은 차분 70파일 2,249 테스트(`run_apm_query` 931회 · 게이트웨이 호출 1,812회) 차분 0 · 기준 트리 2회 대조로 정규화(포트·시각·job_id·폴링 횟수)가 노이즈만 지움을 확인. 행렬에서 성립 불가로 적은 칸 2(참조 보기·`none` 무 service 보기의 대상 텍스트 미해결 — 대상 텍스트를 쓰지 않는 보기라 해당 없음 · 혼합으로 대신 성립). 소스 ≥2 배포·직전 대상 승계는 기존 테스트(`test_plan147_w2_ladder`·`test_plan157_apm_target_inherit`)로 넓은 차분에서 덮음.
- **지적**: Critical 0 · Major 0 · Minor 2 — ① `_CALL_BUILDERS`가 import 시점에 함수를 묶어 모듈 속성 patch가 dispatch에 닿지 않음(현재 그 patch 0 → 주석으로 명시해 반영) ② `_calls_host`만 `ctx.answers`에 부수효과(종전 동작 · docstring 명시 · 기록만).
- 매뉴얼 미반영: 사용자 접점 변경 없음(D-255).

## 5. 후속

- (7a O1) `_CALL_BUILDERS` 키 집합 = `registry.VIEW_TARGETS` 단언 가드 테스트 — 이번은 저장소 새 파일 0 원칙으로 미작성. 7a에서 `src/orchestration/apm/` 분할 시 `run`·`calls` 경계는 `_RunCtx`·단계 함수 단위로 자를 수 있다(seam 소유 함수 `_session_step`·`_hosts_of`·`_new_run_ctx`·`_source_rows_step`는 D-337 ② — Facade 잔류 후보).
- (동작 변경 후보 · 별도 계획) `SourceMcpError` 거부만 `notices`를 `disc.dedupe` 없이 넘긴다 — 다른 거부는 모두 dedupe. 통일은 동작 변경.
- (동작 변경 후보 · D-331 ② 유형) `_call_scope_step`의 `getattr(getattr(app_config, "dbhub", None), "source_call_timeout", 10.0)` + `isinstance` 폴백 — 종전 코드 그대로 이동. 직접 접근 전환은 테스트 config 형태에 따라 동작이 달라질 수 있어 별도 판단.
- → plans/182 #M2(SourceMcpError dedupe) · #L3(getattr 폴백)
