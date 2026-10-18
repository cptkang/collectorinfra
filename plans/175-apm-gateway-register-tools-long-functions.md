# 175. APM 게이트웨이 `register_tools` dispatch(M1) · 도구 긴 함수 Extract Function(M4) (166 단계 6e)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — `register_tools` 935줄·cc 35 → 52줄·cc 2(`_Dispatch` + 도구군 등록 7) · M4 5함수 cc 41~55 → 3~17 · 순수 함수 3종 `period_stats.py` 이동(재노출) · 기존 테스트 수정 0 · 계획 끝 회귀 실패 0(모듈 단위) · verifier 차분 285케이스·HTTP 2,550건 불일치 0 · Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 6 · **계획 ID 6e**(스멜 M1 · M4 — §3.7 M1·M4 행 · §5.6 W4 표 · §5.0 공통 규약). 같은 시간에 6c(`plans/174` — `src/graph.py`·orchestration·routing)가 병렬로 돈다 — 이 계획은 `apm_gateway/` 안에서만 작업하므로 파일 겹침 0.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — MCP 도구 계약(이름·inputSchema·description)·도구 응답 JSON·로그 문구·제니퍼 Open API 호출(경로·쿼리·순서) 불변 · 기존 테스트 수정 0 · 스냅샷 갱신 금지(166 §0.1 규칙 4)
> **계획 시작 SHA**: `1ad7c161`(RCA)
> **회귀 대상 파일(누적)**: 5 — `apm_gateway/apm_gateway/interface/server.py` · `apm_gateway/apm_gateway/application/{tools,fleet_tools,manage_tools}.py` · 새 `apm_gateway/apm_gateway/application/period_stats.py` *(누적 끝 — 계획 끝 회귀 1회 실행: §4)*
> **관련 결정**: D-337(리팩터 규약 — shim·seam·`_` 별칭·행동 변경 분리) · D-139·D-274(apm_gateway 독립 패키지 · 양방향 import 0 · 제니퍼 토큰 격리) · D-303(회귀) · D-331(게이트) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(`smell_metrics.py` · 1ad7c161)

166 분석 이후 W1 apm_gateway 트랙으로 줄 번호가 바뀌어 다시 쟀다.

| 스멜 | 트랙 | 대상 | 위치(착수 시) | 줄 | 근사 cc |
|---|---|---|---|---:|---:|
| M1 | T1 | `register_tools`(도구 18 · `targeted`+`run_data` 14회) | `apm_gateway/apm_gateway/interface/server.py:331` | 935 | 35 |
| M4 | T3 | `ManageTools._event_rules` | `application/manage_tools.py:403` | 145 | 55 |
| M4 | T2 | `ApmTools._business_search` | `application/tools.py:1324` | 140 | 47 |
| M4 | T3 | `FleetTools.ranking` | `application/fleet_tools.py:137` | 152 | 43 |
| M4 | T3 | `FleetTools._period_ranking` | `application/fleet_tools.py:290` | 185 | 42 |
| M4 | T2 | `ApmTools.apm_status_stats` | `application/tools.py:2488` | 147 | 41 |
| M4 | T2 | 순수 함수 `_period_stats`·`_sum_stats`·`_stats_delta`(staticmethod) | `application/tools.py:3298-3375` | — | — |

이미 있는 것: 공통 인자 `Annotated` 별칭 7종(`OwnerArg`·`NArg`·`FullArg`·`MetricsArg`·`IntervalArg`·`WaitArg`·`TargetsArg` — `server.py` 머리). W0 안전망은 166 W0이 만들어 두었다 — `apm_gateway/tests/test_plan166_w0_tool_list_snapshot.py`(도구 목록 name·description·inputSchema 전문 · 등록 순서) · `test_plan166_w0_tool_output_golden.py`(M4 대상 5함수 + 순수 함수 3종 출력 골든 18케이스 · sort_keys). 새 W0 특성화는 필요 없다.

**seam**(테스트의 patch 지점 — 실측): `monkeypatch.setattr(fleet_tools, "expect_calls", …)`(`test_plan134_w6_period_ranking.py:205`) · `client_mod.load_json_file` · `mod.asyncio.sleep`. 테스트가 `ApmTools._period_stats`·`_sum_stats`·`_stats_delta`를 **클래스 속성으로** 부른다 — 모듈로 옮겨도 클래스 속성 재노출이 필요하다. `fleet_tools.py`가 `core._period_stats`로 부른다.

## 2. 처방과 성공 기준

| 트랙 | 처방 | 성공 기준 |
|---|---|---|
| T1 (M1) | `targeted`+`run_data` 반복 → `dispatch` 헬퍼 1개 · 도구군별 등록 함수 분할(등록 순서 보존) · 공통 인자는 기존 별칭 재사용(새 별칭은 스키마 바이트가 같을 때만). **동적 시그니처 생성 Registry 미도입** · 도구 이름·스키마·description 불변 · `run_data` 시그니처 불변(하위 등록 3종에 넘긴다) | W0 도구 목록 스냅샷 동일 · `test_tools_contract.py`·`test_boundary.py`·`test_server.py` 통과 · `register_tools` 줄·cc 감소 |
| T2 (M4) | `_business_search`·`apm_status_stats` Extract Function · 순수 함수 3종을 새 모듈로 이동 + `ApmTools` 클래스 속성 재노출(staticmethod) | W0 출력 골든 동일 · cc 감소 |
| T3 (M4) | `_event_rules`·`ranking`·`_period_ranking` Extract Function · `expect_calls` 호출은 `fleet_tools` 모듈 안에 남긴다(seam) | W0 출력 골든 동일 · `test_plan134_w6_period_ranking.py` 통과 · cc 감소 |

공통: 기존 테스트 수정 0 · 스냅샷 갱신 0 · `apm_gateway` 양방향 import 0 · overfit 신규 유입 0(새 파일이 스캔 대상 `apm_gateway/apm_gateway`에 들어가므로 리터럴 이동분 확인) · ruff·mypy 이번 diff 0.

## 3. 검증

- Wave 중간(트랙별): 자기 테스트 + `python scripts/regress.py --base 1ad7c161 --no-tests --files <바꾼 파일…>`.
- 계획 끝 1회: `python scripts/regress.py --base 1ad7c161 --files <이 계획이 바꾼 파일…>`(apm_gateway 파일은 도구가 패키지 전체 테스트로 선택한다 — `--plan` 실측).
- verifier: `1ad7c161` worktree와 나란히 도구 18종 × 대표 인자의 응답 JSON과 모의 제니퍼 HTTP 호출 기록 차분 0.

## 4. 결과

### 4.1 대상 함수 cc·줄 전후(`smell_metrics.py`)

| 스멜 | 트랙 | 함수 | 전 cc · 줄 | 후 cc · 줄 | 뽑은 헬퍼 |
|---|---|---|---|---|---|
| M1 | T1 | `register_tools` | 35 · 935 | 2 · 52 | `_Dispatch`(targeted+run_data 묶음 · 도구 이름 1회) · `_register_instance_tools`·`_register_transaction_tools`·`_register_stats_tools`·`_register_compare_tools`·`_register_delegated_tools`(manage→scope→fleet)·`_register_job_tools`·`_register_health_tool` |
| M4 | T3 | `ManageTools._event_rules` | 55 · 145 | 3 · 40 | `_rule_args`(모듈) · `_rule_types`·`_rule_calls`·`_fetch_rule`·`_rule_where`·`_rule_rows`·`_rule_notes` · 별칭 `_RuleCall` |
| M4 | T2 | `ApmTools._business_search` | 47 · 140 | 14 · 53 | `_add_business_hit`·`_business_map_hits`(B0)·`_business_inferred_hits`(B1~B3)·`_business_rows` |
| M4 | T3 | `FleetTools.ranking` | 43 · 152 | 17 · 71 | `_ranking_metric`·`_current_limits`·`_unranked_notice`(모듈) · `_ranking_window`·`_realtime_records`·`_rank_realtime` |
| M4 | T3 | `FleetTools._period_ranking` | 42 · 185 | 10 · 68 | `_period_tail_limits`(모듈) · `_period_head_limits`·`_period_plans`·`_period_records`·`_rank_period` |
| M4 | T2 | `ApmTools.apm_status_stats` | 41 · 147 | 14 · 75 | `_status_fetch`·`_status_shown`·`_mask_status_rows` |
| M4 | T2 | `_period_stats`·`_sum_stats`·`_stats_delta` | — | — | 새 모듈 `application/period_stats.py`(`period_stats`·`sum_stats`·`stats_delta`·`PERIOD_METRICS`) · `ApmTools`에 옛 이름 staticmethod 재노출 · 모듈 상수 `_PERIOD_METRICS` 삭제(외부 참조 0 — grep) |

새 함수는 전부 cc ≤ 21. 패키지 전체: 근사cc>50 1 → 0 · >20 29 → 23. 함수 >200줄은 2로 그대로 — `_register_transaction_tools`(219줄 · cc 9 · 본문 대부분이 도구 4종의 시그니처·docstring = 외부 계약)와 범위 밖 `manage_server.register_manage_tools`(205줄).

### 4.2 검증

- **계획 끝 회귀(1회)**: `python scripts/regress.py --base 1ad7c161 --files <회귀 대상 5>` 종료 0 — 저장소 전역 가드 통과 144 · apm_gateway 패키지 전체(도구가 ④ 패키지 전체로 선택) 통과 1,614 · 실패 0 · 건너뜀 14 · arch·overfit·config_fallback 통과 · ruff 이번 diff 줄 0. `범위: 모듈 단위 — 전체 미실행`. 권고 블록 없음.
- **mypy**(도구가 「대상 파일 없음」으로 건너뜀 → 캐시 바이너리 `--python-version 3.12`로 5파일 직접): 1건 — `fleet_tools.py` list-item, `1ad7c161` worktree에도 같은 1건(줄 번호만 이동).
- **W0 안전망**: `test_plan166_w0_tool_list_snapshot.py`(도구 25종 name·description·inputSchema 전문 · 등록 순서) · `test_plan166_w0_tool_output_golden.py`(18케이스) 스냅샷 갱신 없이 통과. 기존 테스트 수정 0.
- **verifier 차분**(`1ad7c161` worktree ↔ 작업 트리 · 같은 하네스 각 2회): 285케이스(MCP 경유 25도구 다중 인자 + M4 직접 호출) · 모의 제니퍼 HTTP 요청 2,550건(순서 포함) · 응답 텍스트·로그 레코드(감사 줄 포함) 불일치 0. Critical·Major 0 · Minor 2(§5).

## 5. 후속

- (Minor) `server.py`의 `RunData`는 `TYPE_CHECKING` 전용 import라 `typing.get_type_hints(_Dispatch.__init__)`가 `NameError`를 낸다. 런타임 영향 0(FastMCP는 도구 함수 주석만 읽는다 · 도구 목록 스냅샷 동일). 순환(`manage_server` ↔ `server`)을 푸는 시점에 함께 정리.
- (Minor) `fleet_tools.py`가 `_Limits`(tools.py 비공개)를 타입 주석용으로 새로 import한다 — 기존 `_check_n`·`_hour_floor` 등과 같은 결. M2(`plans/166` 7d — `_` 심볼 공용 모듈 승격)에서 공개명으로 함께 옮긴다.
- (행동 변경 후보 · 미수정) `fleet_tools`의 인스턴스 id가 None이어도 `application_status`를 그대로 부른다 — mypy list-item 1건이 가리키는 지점(기준선부터 있음). 고치면 호출이 줄어 동작이 바뀌므로 별건.
- → plans/182 #J5
- `ruff format --check`가 `server.py` `apm_transaction_profile`의 `top_k` 인자 한 줄을 합치라고 한다(기준선부터) — 도구 시그니처 원문이라 손대지 않았다.
- 검증 못 한 것: 실 네트워크 전송(인증 포함) · `apm_service_status`·`apm_business` 성공 경로(오류 응답만 차분) · 작업 승격은 `wait_seconds=0` 1흐름 · 동시 호출.

## 6. 이력

- 2026-10-16 v1.0 — 착수. 실측 §1 · 트랙 3개 병렬(T1 server.py · T2 tools.py+period_stats.py · T3 fleet_tools.py·manage_tools.py).
- 2026-10-16 완료 — 계획 끝 회귀 실패 0(모듈 단위) · verifier 차분 0 · 파일명 WIP 태그 제거. 코드 커밋은 메인.
