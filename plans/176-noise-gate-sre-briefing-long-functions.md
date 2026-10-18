# 176. 노이즈 게이트 노드 가드 절 Extract Function(G3) · 브리핑 근거 상태 판정/렌더 분리(G7) (166 단계 6f)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — G3 4함수 cc 46·43·29·17 → 15·11·10·4 · G7 `build_briefing` cc 48 → 8(판정 `_judge_citations`·`_corr_pairs`·`_merge_rule_hypotheses` / 렌더 `_render_*` 7) · 100줄 초과 대상 0 · 기존 테스트 수정 0 · 계획 끝 회귀 실패 0(본체) · sre_agent 원래 실패 51만 · verifier 차분 40,000건 0 · Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 6 · **계획 ID 6f**(스멜 G3 · G7 — §3.6 G3·G7 행 · §5.6 W4 표 · §5.0 공통 규약 · §8 범위 밖). 같은 시간에 6c(`plans/174` — `src/graph.py`·orchestration·routing)가 병렬로 돈다 — 이 계획은 `noise_gate/`·`sre_agent/` 안에서만 작업하므로 파일 겹침 0. 6a가 바꾼 `noise_gate/domain/notification_policy.py`(커밋 전)는 건드리지 않는다.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — 알람 처리 결정·통보 티어·reason·로그 문구·Redis 키/스트림 쓰기·반환 state·브리핑 문구 바이트 불변 · 기존 테스트 수정 0 · 스냅샷 갱신 금지(166 §0.1 규칙 4)
> **계획 시작 SHA**: `1ad7c161`(RCA)
> **회귀 대상 파일(누적)**: 5 — `noise_gate/application/nodes/{investigation_trigger,notification_gate,alarm_context_enricher}.py` · `noise_gate/infrastructure/polestar_noise_context.py` · `sre_agent/sre_agent/application/briefing_builder.py` *(누적 끝 — 계획 끝 회귀 1회 실행: §4)*
> **관련 결정**: D-337(리팩터 규약 — seam 보존·`_` 별칭·행동 변경 분리) · D-048·D-318·D-330·D-336(통보 정책 — 판정·재판정·감사 기록 순서 보존) · D-139(패키지 경계 — `sre_agent` 양방향 import 0 · `noise_gate` 역방향 최소) · D-303(회귀) · D-331(게이트) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(`smell_metrics.py` · 1ad7c161 — 대상 5파일은 작업 트리와 동일)

| 스멜 | 트랙 | 대상 | 위치(착수 시) | 줄 | 근사 cc |
|---|---|---|---|---:|---:|
| G3 | T1 | `investigation_trigger_node` | `noise_gate/application/nodes/investigation_trigger.py:57` | 244 | 46 |
| G3 | T1 | `notification_gate_node` | `noise_gate/application/nodes/notification_gate.py:51` | 184 | 43 |
| G3 | T2 | `alarm_context_enricher_node` | `noise_gate/application/nodes/alarm_context_enricher.py:449` | 215 | 29 |
| G3 | T2 | `PolestarNoiseContextRepository.fetch` | `noise_gate/infrastructure/polestar_noise_context.py:453` | 188 | 17 |
| G7 | T3 | `build_briefing` | `sre_agent/sre_agent/application/briefing_builder.py:272` | 149 | 48 |

**하지 않는 것**(166 §3.6 G3·G7 · §8): `alarm_notifier.py`(1,437줄)·`decision_store.py`(1,044줄) 대분할 — G1·G2 이후 재평가(7c G2가 이 계획 뒤) · `InvestigationDispatcher` 분할 · 디자인 패턴 도입(G7) · 행동 변경(발견 시 §5 후속에 기록만).

**seam·소스 가드**(팀 리드 grep 기준 — 구현자가 직접 다시 확인):
- patch: `notification_gate` 모듈 `_APM_SYSTEM`(`test_plan87_j8_multi_source.py:214`) · `review_page`(`test_page_review.py:616·628`). 나머지 4모듈은 모듈 속성 patch 0건.
- 소스 텍스트 가드: `tests/test_plan166_w1_apm_contract_consumers.py` INLINE_COPIES — `notification_gate.py`에 `"invalid_argument — `·`"contract_violation — ` 원문 유지 · `tests/test_composite/test_host_authz.py` — `investigation_trigger.py`에 `authorize_host_investigation(...)` 호출(AST `ast.Name`) · `tests/test_composite/test_prior_targets_wiring.py` — `investigation_trigger.py`가 `src.utils.prior_targets`를 import · `noise_gate/tests/test_plan144_verify.py` D절 — enricher·gate의 import 집합 경계.
- `test_plan166_w1_noise_gate.py` — `polestar_noise_context._unavailable is unavailable_noise_ctx` 별칭(D-337 ③) 유지.

## 2. 성공 기준

1. 대상 5함수의 근사 cc·길이 감소(목표: 각 함수 100줄 이하 · cc 20 이하 — 가드 절·단계 블록 Extract Function).
2. 기존 테스트 수정 0 · 스냅샷 갱신 0 · `regress.py --base 1ad7c161` 실패가 기준선 실패만.
3. arch_check error 0(경고 증가 0) · overfit 신규 0(기준선 무수정) · 바꾼 파일 ruff·mypy 신규 0.
4. verifier 차분 0 — 변경 전 코드(대상 5파일만 1ad7c161로 되돌린 rsync 스냅샷)와 나란히 노드별 입력 조합 × 플래그의 반환 state·로그 레코드·Redis 쓰기·감사 기록 호출 인자, 브리핑 출력 문자열을 비교.

## 3. Wave

단일 Wave(W1) · 3트랙 병렬(파일 겹침 0) → verifier 1라운드 → 계획 끝 회귀 1회.

| 트랙 | 파일 | 스멜 |
|---|---|---|
| T1 | `investigation_trigger.py` · `notification_gate.py` | G3 |
| T2 | `alarm_context_enricher.py` · `polestar_noise_context.py` | G3 |
| T3 | `sre_agent/.../briefing_builder.py` | G7 |

## 4. 결과

### 4.1 지표(`smell_metrics.py` · 완료 시)

| 대상 | 줄 전 → 후 | 근사 cc 전 → 후 | 뽑은 함수(같은 모듈 안) |
|---|---|---|---|
| `investigation_trigger_node` | 244 → 77 | 46 → 15 | `_meets_min_tier` · `_reused_episode_investigation` · `_skipped_unresolved_apm_host` · `_resolve_first_target` · `_authz_denied` · `_service_unreachable` · `_build_payload` · `_run_inline` · `_inline_result` |
| `notification_gate_node` | 184 → 54 | 43 → 11 | `_decide`(옛 클로저) · `_promote_by_app_impact` · `_lookup_app_impact` · `_record_decision` · `_stage_evidence` |
| `alarm_context_enricher_node` | 215 → 97 | 29 → 10 | 클로저 5 → 모듈 코루틴 `_collect_history`·`_collect_processes`·`_collect_noise_context`·`_collect_message_enrichment`·`_collect_anomaly_baseline` + `_gather_with_timeout` |
| `PolestarNoiseContextRepository.fetch` | 188 → 87 | 17 → 4 | 메서드 `_collect_signals`·`_try_multi_hop_cascade`·`_try_change_correlation` + 모듈 함수 `_query_rows`·`_assemble_noise_ctx` |
| `build_briefing` | 149 → 72 | 48 → 8 | `_CitationState`(NamedTuple) · 판정 `_judge_citations`·`_corr_pairs`·`_merge_rule_hypotheses` · 렌더 `_render_summary`·`_render_llm_cause`·`_render_timeline`·`_render_bottleneck`·`_render_recommendation`·`_render_limitations`·`_render_severity` |

diff 5파일 +978/−561. 새 import는 `briefing_builder.py`의 stdlib `typing`뿐. 새 파일 이동 0(overfit 기준선 무수정).

### 4.2 검증

- **계획 끝 회귀 1회**(`regress.py --base 1ad7c161 --files <5파일>` · rc=0): 본체 통과 1,304 · 실패 0 / `sre_agent` 패키지 전체(자체 venv · 도구가 선택) 통과 1,115 · 실패 7 · 에러 44 → 전부 「원래 실패」 51건(`test_bench_worlds_selfcheck`·`test_bench_e2e_fake_llm`·`test_plan154_verify_security` — 기준선 동일) · arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 0 · `범위: 모듈 단위 — 전체 미실행`. `[웨이브 회귀 필요]`·`[전체 회귀 권고]` 없음.
- **verifier 차분**(기준선 = 작업 트리 rsync 사본에 대상 5파일만 1ad7c161로 — 6a 등 병행 변경을 양쪽 동일하게): `notification_gate_node` 5,000 · `investigation_trigger_node` 5,000 · `alarm_context_enricher_node` 5,000 · `fetch` 5,000 · `build_briefing` 20,000 — 반환·로그(레벨·msg·args·exc_info)·대역 호출 기록 불일치 0(funcName·lineno 제외) · 변이 7개 전부 검출 · Critical·Major 0 · Minor 2(§5).
- **seam·소스 가드**: `notification_gate._APM_SYSTEM`·`review_page` patch가 뽑은 함수에 닿음 · INLINE_COPIES·`test_host_authz`·`test_prior_targets_wiring` 공허화 없음(T1 변이 4건 · 인가→submit 순서는 verifier 동작 테스트로 고정).
- **추가 테스트**: `sre_agent/tests/test_plan176_t3_briefing_golden.py`(구현자 · 원 코드 기준 4,320조합 sha256) · `noise_gate/tests/test_plan176_verify.py`(7) · `sre_agent/tests/test_plan176_verify_briefing_alias.py`(2 — 반환 리스트 별칭 관계).
- 매뉴얼 미반영: 사용자 접점 변경 없음(D-255).

## 5. 후속

행동 변경 후보·남긴 대상 — 이 계획에서는 고치지 않았다.

- **남긴 대상**: `alarm_notifier.py`(1,437줄)·`decision_store.py`(1,044줄) 대분할 — G1·G2 이후 재평가(7c G2 뒤) · `_fetch_app_impact`(cc 23) · `submit_episode_investigation`(107줄 cc 22) · `root_cause_hypotheses`(cc 34) — 이 계획 범위 밖.
- `_collect_noise_context`의 `gate_on` 인자·`if not gate_on` 분기는 호출부가 `if gate_on:` 안에서만 불러 도달 불가(원 클로저부터 있던 방어 분기 — 유지).
- `_lookup_app_impact`가 값 반환과 함께 `app_impact_audit`를 출력 인자로 고친다(동작 동일 · 가독성 후보).
- `investigation_trigger_node`의 `prior_targets_enabled` getattr가 로그 인자와 if 조건에서 두 번 평가된다.
- `test_host_authz`(파일 단위 AST)·`test_prior_targets_wiring`(import 집합) 가드는 노드 경로에서 호출을 지워도 실패하지 않는다(리팩터 전부터) — 함수 단위 가드 보강 후보. 현재는 행동 테스트·`test_plan176_verify.py`가 잡는다.
- briefing: `_llm_hypothesis`가 `root_cause_hypotheses`의 LLM 분기 본문과 중복 · `root_cause_hypotheses`의 `if correlation:`은 항상 참 · 요약 판정의 `and tool_names`는 결과에 영향 없음 · `hypotheses_ranked[:-1]`은 LLM 가설이 늘 마지막이라는 가정에 기댐.
- `_run_inline`의 `asyncio.TimeoutError` 표기를 `TimeoutError`로 통일할 수 있음(enricher는 이번에 바꿈 — 3.11+ 동일 클래스).

## 6. 이력

- 2026-10-16 v1.0 — 최초 작성 · 착수 실측(§1).
- 2026-10-16 v1.1 — W1 3트랙(T1·T2·T3) 병렬 구현 · verifier 차분 0 · 계획 끝 회귀 1회 · 완료(파일명 태그 제거) · 커밋 전.
