# 180. `apm_query.py` God module 분할 — `src/orchestration/apm/` 순수 모듈 + Facade 재노출 (166 단계 7a · 스멜 O1)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — apm_query.py 4,472→2,315줄(잔류 72정의 + 재노출 shim) · 새 `src/orchestration/apm/` 13모듈 2,628줄(179정의 순수 이동 · DAG) · 기존 테스트 수정 0 · 계획 끝 회귀 실패 0(모듈 단위) · verifier 정의 249키 AST·원문 차분 0 · 시나리오 반환 1,092건 차분 0 · Critical·Major 0 · §3
> **상위**: `plans/166` §0.1 단계 7 · **계획 ID 7a**(스멜 O1 — §3.3 O1 행 · §5.7 W5 표 · §7 U-3). 선행 6b(`plans/172` — `run_apm_query` 분해 · `_RunCtx`·단계 함수·`_CALL_BUILDERS`)·6c(`plans/174` — `_COERCERS`)는 작업 트리에 반영(커밋 전). 같은 시간에 7e(`plans/181` — `src/state.py`·`src/orchestration/result_aggregator.py` 등)가 돈다 — 이 계획은 그 파일을 건드리지 않는다.
> **성격**: 동작 불변 구조 리팩터(D-337) — 응답 dict·로그 문구·로거 이름·거부 코드·프롬프트 바이트 불변 · 스냅샷 갱신 금지 · **기존 테스트 파일 수정 0**(새 테스트 추가는 허용).
> **계획 시작 SHA**: `1ad7c161`(RCA) + 단계 6·7 미커밋 변경 위 — 기준 사본 `scratchpad/p180_base/`(2026-10-16 20:19:22 · `.venv`·`build`·`logs`·`.git`·`node_modules`·캐시 제외 rsync). 착수 시 `src/orchestration/apm_query.py` 4,472줄.
> **회귀 대상 파일(누적)**: W1 — `src/orchestration/apm_query.py` · 새 `src/orchestration/apm/{__init__,common,args,window,selection,calls,jobs_notices,targets,reference,first_hop,context,builders,collect}.py` · 새 `tests/test_orchestration/test_plan180_apm_package_guards.py` *(누적 끝 — 계획 끝 회귀 1회: §3)* · verifier 새 테스트 `tests/test_orchestration/test_plan180_verify.py`
> **관련 결정**: D-337(①shim ②seam 보존 ③`_` 공개명+별칭 ⑥행동 변경 분리 ⑦커밋 단위) · D-303(회귀) · D-331(arch 경고) · D-088(overfit 기준선 경로 키 — 이번 이동 대상에 기준선 항목 0)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(기준 사본 · 팀 리드)

- 최상위 정의 251개(함수·클래스·상수). 테스트·스크립트·본체 108파일이 언급(테스트 70파일).
- **patch seam**(별칭 `setattr`·`patch.object`·직접 대입·문자열 경로 전수 — `scratchpad/p180/seams.json`): `_SESSION_FACTORY`(42파일 · 대입·setattr) · `link_business_names`(10) · `get_registry`(4) · `link_hostnames`(3) · `render_view_rows`(1) · `aq.jobs.poll_job`(3 — `apm_jobs` 모듈 속성 patch라 `jobs.poll_job` 속성 접근만 유지하면 이동 무관). 스크립트 `scripts/manual/apm_harness.py:140`도 `aq._SESSION_FACTORY`를 대입한다.
- **Facade 잔류 폐포**(seam을 읽거나 부르는 정의 + 그 호출자 전이 — `scratchpad/p180/callgraph.json`): 55정의 · 약 1,430줄. 이동 가능 196정의 · 약 2,390줄(아래로 닫힘 — 이동 대상은 잔류 정의를 참조하지 않음).
- **소스 가드가 이 파일 경로에 고정한 것**(이동하면 실패 — 잔류):
  - `tests/test_plan166_w1_apm_contract_consumers.py` — `_GUIDANCE_CODES` 대입문(AST)이 `apm_query.py`에 있어야 함 · 원문 조각 `"instance_unresolved" not in call.error`(`_business_hosts` — 이미 잔류)·`"instance_unresolved" in call.error`(`_apm_hop_ledger`). → `_GUIDANCE_CODES`·`_apm_hop_ledger`와 그 호출자 폐포 15정의 약 380줄 추가 잔류.
  - `tests/test_orchestration/test_plan132_w6_source_synonyms.py` — `apm_query.py`가 `src.schema_cache`를 import하지 않고 `synonym` 문자열이 없음. 옮긴 코드는 이 가드 밖으로 나가므로 새 모듈에 같은 단언을 새 테스트로 건다(공허화 방지).
  - `tests/test_orchestration/test_plan134_w2_verify.py:283` — `inspect.getsource(aq._select_views, … aq._uncovered, aq.default_views …)`: 함수 객체 기준이라 이동해도 유효.
- 본체 소비처: `result_aggregator`·`replanner`·`agent_orchestrator`·`conditional_agents`·`intent_planner`(지연)·`apm_jobs`(지연)·`api/routes/admin`(지연)·`api/routes/scope` — 모두 공개 이름 또는 `aq.<이름>` 읽기. 모듈 밖 production 코드가 import하는 `_` 이름 0(D-337 ③ 대상 0 — 스크립트 `apm_bench`의 `_SELECTION_SYSTEM`·`_select_views`·`_retry_selection` 읽기는 잔류·재노출로 보존).

## 2. Wave

| Wave | 내용 | 성격 |
|---|---|---|
| W1 | 이동 가능 정의를 `src/orchestration/apm/` 모듈로 순수 이동(본문·주석·평가 순서 불변) · `apm_query.py`는 잔류 정의 + 재노출 shim · 새 가드 테스트(`apm/` 패키지 유사어 가드 · `_CALL_BUILDERS` 키 = `VIEW_TARGETS` — 172 §5 후속) | 순수 이동 |

## 3. 결과 (2026-10-16)

### 3.1 구조

| 모듈 | 줄 | 정의 | 책임 | apm 내 의존 |
|---|---:|---:|---|---|
| `apm_query.py`(Facade) | 2,315 | 72 | seam 소유(`_SESSION_FACTORY`·`run_apm_query`·단계 함수 중 seam 호출자) · 보기 선택·소스 결정 · 참조 해소 · 수집·요약 · 가드 고정 15 · `__all__` · 재노출 12블록 | — |
| `apm/common` | 100 | 23 | 처리기 이름·상수·노트·`apm_endpoint`·`sanitize_sources` | — |
| `apm/args` | 207 | 22 | 인자 강제 변환 `_COERCERS`·`validate_view_args` | — |
| `apm/window` | 364 | 18 | 조회 창 `WindowPlan`·`plan_window` | common |
| `apm/selection` | 109 | 9 | 보기 선택 보조·소스 되묻기 | common |
| `apm/calls` | 317 | 23 | `_Call`·게이트웨이 호출·오류 봉투 | common · window |
| `apm/jobs_notices` | 123 | 6 | 작업 대기·고지 | calls · common |
| `apm/targets` | 609 | 41 | 대상 해소·승계·상태 행·대상 텍스트 | calls · common · jobs_notices · selection · window |
| `apm/reference` | 81 | 8 | 참조 후보 표시 | common · window |
| `apm/first_hop` | 143 | 7 | 첫 홉 판정 | calls |
| `apm/context` | 80 | 1 | `_RunCtx` | calls · reference · targets · window |
| `apm/builders` | 130 | 7 | `_CALL_BUILDERS` + 원소 함수(표와 원소 같은 모듈) | calls · context · targets · window |
| `apm/collect` | 359 | 16 | 답 줄 렌더 | common |

- **잔류 근거**(D-337 ② · U-3): seam 정의 1 + seam 직접 호출 19 + `run_apm_query` 1 + 호출자 폐포 34 = 55(`scratchpad/p180/callgraph.json`) · **소스 가드 고정 15** — `tests/test_plan166_w1_apm_contract_consumers.py`가 `apm_query.py` 파일에서 `_GUIDANCE_CODES` 대입문과 `"instance_unresolved" in call.error`(`_apm_hop_ledger`) 조각을 찾아 그 둘과 호출자 폐포(`_shown_call_error`·`_public_call_error`·`_decide_outcome`·`_first_hop_step` 등 13)를 남겼다(이탈 — 되돌리는 비용: 그 가드를 경로 무관 AST로 바꾸는 테스트 수정 1파일) · `__all__`·`logger` 2.
- **shim**: 이름 집합 317→314(빠진 것은 stdlib 모듈 객체 `re`·`sys`·`unicodedata` — 소비 0) · 옮긴 179이름 `aq.X is apm.<mod>.X` 179/179 · 옮긴 모듈 로거 `getLogger("src.orchestration.apm_query")`(같은 객체) · production 코드가 import하는 `_` 이름 0이라 D-337 ③ 공개명 승격 0.
- **새 가드**(`test_plan180_apm_package_guards.py`): `apm/` 모듈의 `src.schema_cache` import·`synonym` 0(132 원 가드가 이동으로 못 보게 된 코드를 덮음) · `_CALL_BUILDERS` 키 = `VIEW_TARGETS`(172 §5 후속) · `apm/` → Facade import 0(상대 import·`import_module` 문자열 포함 — verifier Minor 교정).

### 3.2 검증

- **계획 끝 회귀**(`regress.py --base 1ad7c161 --files <15파일>` · histogram diff): 본체 파일·폴더 56 · 노드 7 · 정적 892건 → 통과 2,115 · 실패 0 · 에러 0 · 건너뜀 4 · arch·overfit·config_fallback 통과 · ruff 위반 1(기존 `apm_query.py:69 I001` · 이번 diff 줄 0) · mypy 0 · `범위: 모듈 단위 — 전체 미실행` · 권고 블록 없음. 원래 실패(`test_plan160_chart_api…[True]`)는 선택 밖.
- **implementer**: apm 언급 테스트 70파일 전후 실패 집합 공집합(전 2,245 · 후 2,273 passed · 4 xfailed) · 정의 AST·원문 동등 249키 · 새 가드 변이 4건 FAIL.
- **verifier**(`scratchpad/p180/verify/`): 정의 249키 한 곳씩 · `ast.dump`·원문·직전 주석 차분 0 · Facade patch 6종(`_SESSION_FACTORY`·`link_hostnames`·`link_business_names`·`get_registry`·`render_view_rows`·`aq.jobs.poll_job`)이 `run_apm_query`에 반영 · 역방향 122파일 스캔 공허 patch 0 · 소스 가드 변이 26건(166·132·134 가드 성질 유지) · 시나리오 차분 apm 70파일 양 트리 2회씩 반환 1,092건·로그·게이트웨이 호출 인자 차분 0(노이즈 2종 — 폴링 횟수·동시 호출 순서, 같은 트리 반복에서도 남음) · 순환 0 · arch error 0 · dataclass 직렬화 노출 경로 0 · Critical 0 · Major 0 · Minor 2(새 가드의 상대 import·`import_module` 누락 · 비재귀 glob → 교정) · 새 `test_plan180_verify.py` 21 passed.
- 행동 변경 후보: 없음. 매뉴얼 미반영: 사용자 접점 변경 없음(D-255).

## 4. 후속

- (shim 제거 · D-337 ①) 다음 APM 계획에서 `grep -w`로 `apm_query.<옮긴 이름>` 소비처를 새 모듈 경로로 옮긴 뒤 재노출 제거.
- (가드 고정 잔류 15) `test_plan166_w1_apm_contract_consumers.py`를 경로 무관(패키지 전체 AST)으로 바꾸면 `_GUIDANCE_CODES`·오류 렌더 사슬을 `apm/calls`로 옮길 수 있다 — 테스트 수정을 동반하는 별 계획.
