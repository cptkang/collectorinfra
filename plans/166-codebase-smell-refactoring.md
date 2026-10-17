# 166. 저장소 전반 코드 스멜 정리 — SOLID·SSOT·선별 패턴 적용 구조 리팩터링

> **작성일**: 2026-10-14 · **v1.2**(단계별 진행 순서 §0.1 — 166·163·W3~W5를 단계 0~8로 정렬 · 구현 전)
> **상태**: **로드맵 완료**(2026-10-17 · 단계 0~8 전부 완료 — 단계 8 = §5.8 지표 재측정 「결과」 칸 · §5.9 shim·별칭 제거 사이클 · 미달 지표와 후보 계획은 §5.8 아래 표 · 커밋 전) — 이 계획의 실행분(W0~W2 = 단계 1·3·4) 완료. 단계 4 = 166 W2 Move-only 분할 · 완료(2026-10-15 · `3a0a1101` — §5.4 「W2 결과」 · arch 경고 43→17 · 계획 끝 회귀 원래 실패 2만 남김). 다음은 단계 5(W3 별도 계획 5a·5b — §0.1). 단계 3 = 166 W1 완료(2026-10-15 · 73851bef — §5.3 「W1 결과」). 스멜 48건 검출(H 15 · M 28 · L 5) · Wave 6개(W0~W5) · **사용자 결정 11건 확정(2026-10-14 · 사용자 위임 — 권고 채택 · §7)** · **진행은 §0.1 단계표가 정본** — 단계 0(커밋 지시 대기) → **단계 1(166 W0 ∥ 163 W0) 착수 가능**. 보안 성격 관찰 3건 + X6(LLM 클라이언트 env 폴백)은 `plans/163`으로 이관. 결정 등재: D-337(구조 리팩터 규약) · D-331(품질 게이트 조정) · D-139 부기(noise_gate 역방향 허용 목록). W3~W5는 착수 시 Wave별 별도 계획서(이 계획은 로드맵 — U-11).
> **요청 원문(2026-10-14)**: *"전체적인 코드 리팩토링을 위해 코드 분석을 진행하고 코드 스멜을 검출하여 리팩토링 계획을 작성하라. 리팩토링시 디자인패턴, SOLID, SSOT 등이 적용될 수 있도록 계획이 수립되어야 한다."*
> **성격**: **동작 불변 구조 리팩터링** 계획. 행동이 바뀌는 항목(보안 강화·기본값 통일·신호 통일)은 리팩터에 섞지 않는다(D-337 ⑥) — 보안은 `plans/163`, 나머지는 §8 범위 밖.
> **상위·인접 계획**: `plans/69`(완결 · D-134 — 쿼리 생성 영역 **내부** 형상. 이미 처리한 것은 재제안하지 않는다) · `plans/70`(WIP — 실행 경로 사다리 명시화·플래그 감사·시맨틱 레이어 수렴·문서 아카이빙. **그 범위는 이 계획에서 다루지 않는다** — §8) · `plans/136`(회귀 도구 · D-303) · `plans/158`(기준선 「원래 실패」 18건 — 회귀 판독 시 참조)
> **관련 결정**: **D-337(구조 리팩터 규약 — 이 계획의 공통 규약 §5.0)** · **D-331(품질 게이트 조정 — W1에서 구현)** · D-003(읽기 전용 3중 방어) · D-048·D-139(패키지 경계 · noise_gate in-process 예외 · 2026-10-14 부기 — 역방향 허용 목록) · D-066·D-067(단일/멀티 대칭 · 드리프트 가드) · D-088·D-089(공용 계층 DB-agnostic · 어댑터 레지스트리 · 과설계 금지 Plan 63 §9) · D-129(설정 카탈로그 SSOT = pydantic 인트로스펙션) · D-134(동작 불변 게이트 선례) · D-161(죽은 경로 판정 금지 · 폐기 전 4항 실측) · D-225→D-251(사다리 · 2단 기준) · D-274(apm_gateway 경계) · D-303(회귀 정책) · D-255(매뉴얼 동반 — 이 계획은 사용자 접점 변경 0이 목표)
> **적용 스킬**: `.claude/skills/refactoring/` (D-332) — 위임 프롬프트에 「`refactoring` 스킬을 따른다」를 적는다.
> **분석 기준 SHA**: `7ba5c96`(RCA · 트리 clean). `file:line`은 이 시점 값이다.
> **계획 시작 SHA**: `08863d6`(RCA · 단계 0 커밋 · 2026-10-14 W0 착수 시 기록) · **회귀 대상 파일**: W0 — 새 테스트 `tests/**/test_plan166_w0_*.py` 7 · `noise_gate/tests/test_plan166_w0_decision_matrix.py` (apm_gateway 2파일은 루트 수집 밖 — 직접 실행) · **W1 — 121파일**(소스 64 · 테스트 57 · 목록 §5.3 「W1 결과」 · `git diff --name-only 5f7f5c39 73851bef`로 재현 · apm_gateway 6파일은 직접 실행) · **W2 — 89파일**(소스·스크립트 .py 79(새 40 · 삭제 `src/config.py` 1 포함) · 테스트 5 · 매뉴얼 원천 2 · 빌드 산출 3 · 커밋 뒤 `git diff --name-only 73851bef <W2 커밋>`으로 재현 · mcp_server 4파일은 패키지 테스트 직접 실행) *(누적 끝 — 계획 끝 회귀 1회 실행: §5.4 「W2 결과」)* · **단계 8 — 19파일**(세션 시작 `33f238ea` · 소스만 · 목록 §5.9 · 계획 끝 회귀 1회 + 교정 3파일: §5.9)
> **분리 계획**: `plans/163-security-hardening-separated.md`(S1·S2·S3·X6 — 행동 변경 · security-auditor 동반 · **§0.1 단계 2 — 166 W0과 함께 단계 1을 끝낸 뒤, 166 W1 착수 전에 끝낸다**(v1.2 확정))

---

## 0. 요약

**문제는 코드량이 아니라 변경이 몰리는 지점의 구조다.** `plans/70`이 이미 판정했듯 규모(본체 `src/` 13.4만 줄 · 5개 패키지 18.2만 줄) 자체는 정상 범위다. 이번 분석에서 실측된 실제 이상 신호는 세 가지다.

| # | 진단 | 대표 실측 | 결과로 생긴 반복 실수 |
|---|---|---|---|
| 1 | **발산 변경(Divergent change)이 몰리는 거대 모듈** | 1,000줄 초과 파일 40개 · 2,000줄 초과 14개. `apm_query.py` 3,998줄(머리 docstring이 plans/125·130·132·134·147·159 변경 이력 누적) · `query.py` 4,312줄 · `multi_db_executor.py` 3,314줄 | 계획마다 같은 파일을 고쳐 병합 충돌·리뷰 비용 |
| 2 | **같은 개념의 다중 정의(SSOT 위반)** | 설정 필드 기본값과 **다른** `getattr` 폴백 약 80곳(`zone_group_exclusive`는 필드 `False`인데 폴백 `True`가 11곳) · 상수 사본(`DEGRADED_KEY` 4벌 · `display_rows_cut` 2벌) · 판정 함수 사본(`_capability_ownership_on` 2벌+변형 1 · `_ENGINE_ALIASES`/`engine_key` 2벌 · `_zone_label` 3벌) | Known Mistakes 2026-09-17(`getattr`가 속성명 오류를 삼킴) |
| 3 | **쌍으로 존재하는 경로의 복붙 중복** | 단일/멀티 DB 폼필 피벗 전처리 약 170줄×2 · 스트림 `event_generator` 2벌(약 2/3 동일) · 비스트림 핸들러 2벌 · 2단/3단 존 역질문 꼬리 25줄 동일 | Known Mistakes 2026-09-21(멀티 경로만 노트 덮어씀) · D-066 「4경로 대칭」이 수작업 규율로 유지됨 |

처방의 축은 **SRP(모듈 분할) · SSOT(상수·판정·설정 접근 일원화) · DRY(쌍 경로 공통 함수 추출)** 이다. 디자인 패턴은 **실측 근거가 있는 4곳에만** 쓴다 — 순서 있는 규칙 표(Chain of Responsibility의 함수 튜플형) 3곳(`decide_notification` · `replanner` · `output_generator` 노트), 표 주도 dispatch(경량 Registry) 3곳(`run_apm_query` 호출 구성 · SSE 진행 요약 · `_coerce_arg`), Facade 완결 2곳(`SchemaCacheManager` · `ApmTools`), 합성(Composition) 1곳(`RedisSchemaCache` 키 공간 분할). Strategy·Template Method·Abstract Factory·이벤트 버스·Dialect Strategy·상태 전면 객체화는 **도입하지 않는다**(§4.2에 근거).

모든 Wave는 **동작 불변을 게이트로 실증**한다(D-134 선례): 기존 테스트 + W0에서 먼저 만드는 특성화·스냅샷 테스트 + `prompt_render_diff.py` · `catalog_diff.py` · `arch_check`·`overfit_check`.

### 0.1 단계별 진행 순서 (실행 정본 — 2026-10-14 v1.2)

`plans/166`(W0~W2)·`plans/163`(보안)·W3~W5 별도 계획서를 **단계 0~8 한 줄로 세운 순서**다. 착수·위임·상태 판단은 이 표를 기준으로 하고, 각 Wave 절(§5.2~§5.7)은 그 단계의 상세다.

| 단계 | 계획 · Wave | 범위 | 선행(종료 게이트) | 단계 안 병렬 | 회귀(D-303) | 사용자 확인 | 상태 |
|---:|---|---|---|---|---|---|---|
| **0** | 준비 | 계획서 166·163 · 결정 D-337(구 D-330)·D-331·D-332 · D-139 부기 · 스킬 2종 커밋 → 그 SHA를 166·163 머리 「계획 시작 SHA」로 적는다 | — | — | 없음 | **커밋 지시**(커밋은 사용자 지시 시만). 커밋 전에는 `isolation: worktree` 에이전트가 계획서·스킬을 보지 못한다 | 완료(2026-10-14 · 단계 0 커밋) |
| **1** | 166 W0 ∥ 163 W0 | 특성화·스냅샷 테스트 · 파손 범위 실측표 · I4 판정(§5.2) / S1 고정 SQL 통과표·세션 읽기 전용 수단·S2·S3·X6 영향 실측(163 §2.1) | 단계 0 | 166 W0 4트랙(§5.2) + 163 W0 — 둘 다 테스트·문서 신규만이라 파일 겹침 0 | 자기 테스트만 | **163 S3 관리자 경로 문구 방침**(163 W0 끝에 묻는다 — 기본안 「예외 타입명 + 상관 ID」) | 완료(2026-10-14 · 커밋 전 — 기준 08863d6) |
| **2** | 163 W1 | S1 · S2 · S3 · X6 (행동 변경 · security-auditor 동반) | 단계 1 전부 종료(166 W0 스냅샷이 먼저 있어야 163의 동작 변경이 스냅샷에서 「의도한 갱신」으로 구분된다) · S3 관리자 분만 방침 확정 뒤(나머지 트랙은 먼저 진행) | 4트랙(163 §4 — 파일 겹침 0) | **163 계획 끝 회귀 1회** + `mcp_server` 자체 테스트 | S3 화면 문구가 바뀌면 매뉴얼 대상 여부(D-255) | 완료(2026-10-14 · 단계 1·2 일괄 커밋 — 163 §2.1a 사용자 확정 5건 · 스냅샷 4종 재채록(163 §5) · 이후 스냅샷 갱신 금지) |
| **3** | 166 W1 | 3a 게이트(D-331 ①②) → 3b SSOT 기계 치환(§5.3) | 단계 2 종료(163 완료 — S2 뒤에 A5, X6 뒤에 X5의 `fabrix_client` 분) | 3a는 팀 리드 단독 → 3b 3트랙(src / noise_gate / apm_gateway) | Wave 중간 규칙 | — | 완료(2026-10-15 · 73851bef — 3a 게이트 · 3b 3트랙+src 3묶음 · 3c 린트 `--ci` · arch 경고 43 · 폴백 0 · 모듈 회귀는 `[웨이브 회귀 필요]` 미발생으로 W2 끝 1회에 포함) |
| **4** | 166 W2 | Move-only 분할(§5.4) — **166의 마지막 Wave** | 단계 3 종료(arch 경고 기준선 43 · 폴백 린트 `--ci` 전환) | worktree 병렬(§5.4 트랙) · I4는 맨 뒤 단독 | **166 계획 끝 회귀 1회** | 전체 회귀가 필요하면 요청 | 완료(2026-10-15 · `3a0a1101` — 4a 10트랙(O4·O5/N2/N3/M5 · N7/I2/I3/A7/A8) · 4b 공유 파일 통합 · 4c I4 단독 · 4d 계획 끝 회귀(원래 실패 2만 남김) · arch 경고 43→17 · §5.4 「W2 결과」) |
| **5** | W3 별도 계획 3건 | 5a A1·A3(query 스트림 공통화) · 5b N1(a)·O6(쌍 경로 공통 함수) · 5c I1·X2·X5(`pii_filter` 분)(스키마 캐시 Facade·설정 주입) | 단계 4 종료 · 5c는 5b 뒤(`multi_db_executor.py` 겹침) | 5a ∥ 5b → 5c | 계획마다 끝 1회 | — | 완료(2026-10-16 · 커밋 전 — 5a·5b·5c 모두 완료) — **5b 완료**(2026-10-16 · 커밋 전 · `plans/169` — N1(a) 어댑터 `form_fill_plan.py` · O6 `routing/zone_clarification.py` · 회귀 실패 0 모듈 단위) · **5a 완료**(2026-10-16 · 커밋 전 · `plans/168` — `query.py` 4,377→4,256 · `_stream_graph_turn`·`_sse_response`·`_run_text_pre_gates` · 소스 계수 가드로 응답 리터럴 경로별 잔류 · 회귀 원래 실패 1만 · 턴 기록 가드 보강(사용자 결정)) · **5c 완료**(2026-10-16 · 커밋 전 · `plans/170` — I1 Facade 우회 7곳 교체·3곳 seam 잔류 · `_prefer_redis` · 유사어 키 공간 `RedisSynonymStore` 합성 · 키 빌더 · X2·X5 `pii_filter` `_flag` · 회귀 실패 0 모듈 단위) |
| **6** | W4 별도 계획 6건 | 6a G1 · 6b O2 · 6c O3(배선)·O7·O8·O9 · 6d A4·N5·N7(체인)·I5 · 6e M1·M4 · 6f G3·G7 | 단계 5 종료 | 6a~6f 파일 겹침 없음 — **동시 진행은 2건까지 권고**(회귀 귀속·리뷰 부담) | 계획마다 끝 1회 | — | 6a 완료(2026-10-16 · 커밋 전 · plans/171 — `decide_notification` 규칙 표 `RULES` 13행 + `GateInputs` · cc 110→5 · verifier 차분 94,800건 0) · 6b 완료(2026-10-16 · 커밋 전 · plans/172 — `run_apm_query` `_RunCtx`+단계 함수·`_CALL_BUILDERS` 5종 · cc 189→10 · verifier 차분 0(행렬 81·넓은 차분 2,249 테스트)) · 6d 완료(2026-10-16 · 커밋 전 · plans/173 — A4·N5·N7·I5 13함수 Extract · cc>50 5→1 · >200줄 7→0 · `TEXT_NOTES`·`DOC_NOTES` · 회귀 원래 실패 1만(모듈 단위) · verifier 차분 155k건 0) · 6c 완료(2026-10-16 · 커밋 전 · plans/174 — `build_graph` 선두 `resolve_ladder_tier` 1회·단별 `_wire_*` · replanner `GUARDS`·`FILTERS`·`HandlerResult` · semantic_router 3단 분할·`_plan_turn` 표 · `parse_registry` 파서·`_coerce_arg` dispatch · 회귀 실패 0(모듈 단위) · verifier 차분 0) · 6e 완료(2026-10-16 · 커밋 전 · plans/175 — `register_tools` `_Dispatch`+도구군 등록 7 · cc 35→2 · M4 5함수 cc 41~55→3~17 · 순수 함수 `period_stats.py` 이동 · verifier 차분 285케이스·HTTP 2,550건 0) · 6f 완료(2026-10-16 · 커밋 전 · plans/176 — G3 노드 4함수 Extract cc 46·43·29·17→15·11·10·4 · G7 `build_briefing` 판정/렌더 분리 cc 48→8 · 회귀 실패 0(모듈 단위) · verifier 차분 40,000건 0) |
| **7** | W5 별도 계획 5건 | 7a O1(선행 6b) · 7b A2(선행 5a · **사용자 결정(2026-10-16): query.py 계수 가드를 공유 헬퍼 호출 확인으로 전환** — 가드 약 8파일 수정은 테스트 수정 0의 예외 · `plans/168` §5) · 7c G2(선행 6f) · 7d M2(선행 6e) · 7e N6(선행 6c — `HandlerResult`) | 단계 6 종료 | 선행이 끝난 계획부터 · 동시 2건까지 | 계획마다 끝 1회 | — | 7c 완료(2026-10-16 · 커밋 전 · plans/177 — `AlarmWorker` 2,420→1,121줄 · 와이어링 `worker_wiring` · `EpisodeCoordinator` · `SignalDetector`·`DedupService` 합성(host 어댑터 · 클래스 표면 불변) · 회귀 실패 0(모듈 단위) · 하네스 차분 58,240건 0 · `_process` 분할 후속) · 7d 완료(2026-10-16 · 커밋 전 · plans/178 — `ApmTools` 2,883줄·63메서드 → Facade 126줄·2 + `ApmCore` + 도구군 5 · tools.py 3,590→342 · 회귀 실패 0(모듈 단위) · verifier 차분 427케이스·HTTP 4,121건 0) · 7b 완료(2026-10-16 · 커밋 전 · plans/179 — query.py 4,256→2,007줄 · `src/api/query/` 8모듈 + `src/pipeline/turn/` 4모듈 + 재노출 shim · 응답·사전 게이트·done 리터럴 한 벌 · 진행 요약 표 주도 dispatch · 계수 가드 8파일 AST 전환 · 회귀 원래 실패 1만(모듈 단위) · verifier 차분 326케이스 0 · 보안 감사 인가·감사 누락 0) · 7e 완료(2026-10-16 · 커밋 전 · plans/181 — `OutputState` TypedDict 39키(구간 4개 상속 · 비AgentState 8키) · `_build_output_state -> OutputState` · `create_initial_state` 초기값 표 데이터화 · 노드 반환 키 ⊆ 그래프 상태 정합 테스트(위반 3 고정) · 회귀 실패 0(모듈 단위) · verifier 차분 0) · 7a 완료(2026-10-16 · 커밋 전 · plans/180 — apm_query.py 4,472→2,315줄 · `src/orchestration/apm/` 13모듈 179정의 순수 이동 + 재노출 shim · seam 55·소스 가드 고정 15 Facade 잔류 · 테스트 수정 0 · 회귀 실패 0(모듈 단위) · verifier 정의 249키·시나리오 반환 1,092건 차분 0) |
| **8** | 로드맵 종료 | §5.8 지표 재측정(`refactoring` 스킬 `smell_metrics.py`) · D-337 ③ 별칭 잔존 `grep -w` — 0이면 제거, 아니면 목록 기록 · shim 잔존 목록 · 166 파일명 상태 태그 정리 | 단계 7 종료 | — | 바꾼 파일만 모듈 단위 | 로드맵 종료 확인 | 완료(2026-10-17 · 커밋 전 · 세션 시작 `33f238ea` — §5.8 「결과」 칸 · §5.9: shim·별칭 388 제거(사용처 0만 · 19파일 · 교정 6 포함) · 남김 426 표 · 계획 끝 회귀 원래 실패 2만(모듈 단위 · 교정 회귀 원래 실패 1 · `src/config` 전체 회귀 권고) · verifier Critical 0 · 미달 지표 SCC 8·X4 7·2,000줄 초과 5는 후보 계획으로) |

**의존 그래프**

```
0 커밋 ─▶ 1 [166 W0 ∥ 163 W0] ─▶ 2 [163 W1] ─▶ 3 [166 W1: 3a 게이트 → 3b 치환] ─▶ 4 [166 W2]
   ─▶ 5 [5a ∥ 5b → 5c] ─▶ 6 [6a~6f, 동시 2건] ─▶ 7 [7a←6b · 7b←5a · 7c←6f · 7d←6e · 7e←6c] ─▶ 8 정리
```

**단계 운영 규칙**

1. **한 단계 = team-lead 1명.** 단계 1처럼 계획이 둘이면 team-lead도 둘이다(에이전트 하나에 계획서 하나 또는 Wave 하나 — CLAUDE.md 위임 원칙). 단계 5~7의 별도 계획서는 계획마다 team-lead 1명.
2. **다음 단계는 앞 단계의 종료 게이트를 통과하고 보고를 받은 뒤 시작한다.** 단계를 건너뛰어 미리 착수하지 않는다(W0 스냅샷·shim 경계 위에서 분해한다는 전제와 회귀 귀속을 단순하게 유지하려는 것). 같은 단계 안의 계획은 표의 「선행」이 끝나면 병렬로 진행한다.
3. **단계가 끝나면** 이 표의 「상태」 칸을 `완료(YYYY-MM-DD · SHA)`로 고치고 `plans/INDEX.md` 행·`INDEX-CHANGELOG.md`를 갱신한다. 파일명 태그: 단계 1 착수 시 166·163을 `-WIP-`로, 163은 단계 2 완료 시 태그 제거, 166는 단계 4 완료 시 「로드맵」으로 태그 제거(INDEX 「파일명 상태 접미사」).
4. **스냅샷 갱신 규칙**: 단계 1에서 만든 스냅샷은 **단계 2(163)만** 갱신할 수 있다 — 163이 바꾸는 동작(S3 오류 문구 등)이 스냅샷에 걸리면 163이 갱신하고 사유를 163 §5에 적는다. 단계 3 이후에는 어떤 스냅샷도 갱신하지 않는다(갱신이 필요하면 리팩터가 아니므로 되돌린다).
5. **W3~W5 별도 계획서**(단계 5~7)는 착수할 때 `plans/INDEX.md` 최댓값+1로 채번하고, 이 표의 계획 ID(5a·6b 등)를 머리에 적는다. 범위·성공 기준은 §5.5~§5.7을 옮겨 쓴다.
6. **사용자 확인 지점은 표의 「사용자 확인」 칸뿐이다.** 그 밖의 판단은 D-337·D-331과 §7 확정을 따른다.
7. v1.1까지 어느 Wave에도 배정되지 않았던 스멜을 v1.2에서 배정했다: **A5·N4·X5(`pii_filter` 제외)·M6 → W1** · **A8 → W2** · **X5의 `pii_filter` 5곳 → W3(5c, X2와 같은 함수)** · G6은 D-139 부기로 해소 · X6은 163.

---

## 1. 측정 방법과 한계

| 측정 | 도구 | 산출 위치(세션 scratchpad — 저장소 밖) |
|---|---|---|
| 파일·함수 길이 · 순환 복잡도(cc) | 표준 `ast` 스크립트(분기 노드 수 근사 — radon 동등 아님). `radon`·`pylint`·`lizard`·`ruff`는 오프라인 캐시에 없어 못 씀 | `metrics.py` |
| 중복 코드 | 식별자·리터럴 정규화 후 연속 논리행 윈도 해시 클론 검출 + 함수 AST dump 해시 | `smell_xcut.py` |
| 같은 이름 최상위 함수 · 매직 문자열 · import 팬인/팬아웃 · SCC · 함수 내부 import · 플래그 읽기 밀도 | `ast` 스크립트 | `smell_xcut.py` |
| 계층 위반 | `python scripts/arch_check.py --ci` (error 0 · warning 117) | — |
| 설정 기본값 이중 정의 | `config.py` 필드 기본값 ↔ `getattr(cfg, "x", 기본)` AST 대조 | `ga.py` |
| 침묵 `except` | AST — 본문이 `pass`·상수 반환뿐인 핸들러 | `ex.py` |
| 영역별 정성 분석 | 패키지·관심사별 병렬 위임 7건(정독 + grep) — 팀 리드가 핵심 주장 9건을 코드로 재확인(§1.1) | `smell_*.md` |

### 1.1 팀 리드 재확인한 주장(코드 실측)

`zone_group_exclusive` 필드 `False`(config.py:669) vs 폴백 `True` 11곳 · `.env` 값 `false` · `noise_gate`의 `src.observability`·`src.domain.host_availability`·`src.domain.host_authz` 임포트 · `mcp_server` asyncpg 풀에 읽기 전용 세션 설정 없음 + `validate_readonly`는 `execute_sql`에만(tools.py:120-122) · `display_rows_cut` 2벌 · `DEGRADED_KEY` 4벌 · graph.py:523-531 주석이 「기준 3단」(D-251과 불일치) · `_get_eav_pattern`·`_render_samples_secure` 단일/멀티 사본 · `_ENGINE_ALIASES` 2벌 · `_verify_user_token`이 `type` 클레임 미검증(dependencies.py:31-53) · `overfit_baseline.json`이 **파일 경로 키**(→ 파일 분할 시 기준선 항목 이동 필요 — §5.0).

### 1.2 측정하지 못한 것

정확한 cc(radon 부재 — 근사치) · 커버리지 · git churn(변경 빈도) · 테스트가 `monkeypatch`하는 심볼 전수(이동 시 파손 범위 — W0에서 측정) · `except Exception` 651곳의 로그 수준 분포 · 호스트명 인라인 정규화 53곳의 의미 동일성 · 시간 파싱 함수 7종+의 호출 관계 · 프런트(`src/static`) SSE 소비 리터럴 · `alarm_notifier.py`·`decision_store.py`·`dbhub/client.py`·`synonym_loader.py` 내부 구조 정독 · DB2 쿼리 타임아웃·읽기 전용 처리 · `settings_catalog`가 config 클래스 열거에 묶인 정도 · LangGraph `add_node` 순서 민감도.

---

## 2. 정량 개요 (SHA `7ba5c96` · tests/scripts 제외)

| 지표 | 값 |
|---|---|
| 패키지 규모(파일/줄) | `src` 306/134,276 · `noise_gate` 73/19,805 · `apm_gateway` 41/15,897 · `mcp_server` 15/6,029 · `sre_agent` 24/5,714 |
| 1,000줄 초과 파일 / 2,000줄 초과 | 40 / 14 |
| 함수 수 · 100줄 초과 · 200줄 초과 | 5,606 · 154 · 33 |
| cc>20 · cc>50 함수 | 238 · 17 |
| 최대 cc 함수 | `run_apm_query` cc184(433줄) · `decide_notification` cc98(540줄) · `schema_analyzer` cc89(438줄) |
| 최장 함수 | `apm_gateway` `register_tools` 935줄 |
| God class(줄/메서드) | `ApmTools` 2,889/59 · `AlarmWorker` 2,310/58 · `RedisSchemaCache` 2,134/69 · `SchemaCacheManager` 1,817/57 |
| 클론 비율(논리행) | 3.0%(3,168/106,173) — mcp_server 5.4% · apm_gateway 3.6% · noise_gate 3.3% · src 2.8% · sre_agent 1.6%. 클론 283개 중 248개가 **같은 파일 안** |
| 2개 이상 파일에 같은 이름 최상위 함수 | 134개(대부분 이름만 같고 본문 다름 — `_norm` 7·`_normalize` 5는 통합 대상 아님. 진짜 복붙은 `_now_iso` 4벌 등) |
| 설정 접근 | `load_config(` 94곳 · `getattr(<cfg>, …)` 231곳(그중 필드 기본값과 **다른** 폴백 약 80곳) · `os.getenv/environ` 45곳·18파일 · bool 설정 필드 135개 |
| 예외 | `except Exception` 651곳(5개 패키지) · 침묵형(pass/상수 반환) 표본 src 18 · noise_gate+sre_agent 다수 |
| import | 팬아웃 최대 `multi_db_executor` 41 · 팬인 최대 `src.config` 70 · `query_gen_common` 41. 모듈 레벨 순환 1(`db_adapters`↔`polestar`), 지연 import 포함 SCC 7개 · 함수 내부 import 623건/110파일 |
| arch_check | error 0 · warning 117(112건이 application→application 「노드 간 직접 의존」 — 그중 22건은 같은 패키지 내부 오탐성, 실질 nodes→nodes 38건) |
| TODO/FIXME | 3건(전부 `query.py` `_results_store`) — 부채 표지가 주석으로 남지 않는 저장소 |

---

## 3. 스멜 목록

표기: **심각도** H(변경 빈도 높은 영역의 결함 유발 구조) · M(국소 결함 위험·가독성) · L(정리 수준). **검증**은 행동 불변을 보이는 수단이며, `(W0)` 표시는 W0에서 먼저 만들어야 하는 안전망이다.

### 3.1 횡단 — SSOT·설정·상수 (X)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방(원칙/패턴) | 검증 |
|---|---|---|---|---|---|---|---|
| **X1** | SSOT 위반 · 방어적 `getattr` | 표본: `multi_db_executor.py:1301` · `subagents.py:415,1837,1848` · `query.py:1897,1902,1927,1973,2212` · `scope.py:33` · `semantic_router.py:404,771`(`zone_group_exclusive` 폴백 True ↔ 필드 False) · `graph.py:557,664`(`trace_enabled` False↔True) · `prompt_blocks.py:861`(`prompt_token_budget` 0↔90000) · `answer_reserve_sec` 5곳(0↔15) · `alarm_worker.py:1720,2040,2406` 외 | 필드 기본값과 다른 폴백 약 80곳 · `getattr(cfg…)` 231곳 | 평시 무해(AppConfig가 값을 채움)하나 SimpleNamespace 테스트 설정·필드명 오타 시 **반대 방향 기본값으로 조용히 동작**. 기본값 변경 시 grep 불가 사본 잔존 | **H** | **SSOT · fail-fast** — 존재하는 필드는 `cfg.x.y` 직접 접근(오타는 `AttributeError`). 기계적 치환, 패턴 없음. `zone_group_exclusive` 군집 1순위 | 치환 전후 해당 모듈 테스트 · 신규 AST 린트(폴백≠필드 기본 0건)를 `scripts/`에 추가해 재유입 차단 · SimpleNamespace 기반 테스트는 실 `AppConfig` 경로로 교정 + 린트 CI 게이트화(U-7 확정 · D-331 ②) |
| **X2** | DIP 위반 · 전역 설정 직접 조회 | `security/pii_filter.py:233,293,306,319,332`(플래그 하나에 함수 하나, 매번 `load_config()` + `except→고정값`) · `redis_cache.py:209` · `field_mapper.py:459` · 군집 `api/routes/schema_cache.py` 14 · `semantic_router.py` 8 · `result_organizer.py` 6 | `load_config(` 94곳 | 하위 계층(security·schema_cache)이 전역 설정에 암묵 의존 → 테스트 주입이 `cache_clear`·monkeypatch 의존 · 설정 로드 실패 시 방향이 다른 고정값(governance False · pii_scrub True) 반환 | M | **DIP** — 생성자/인자 주입(이미 AppConfig를 받는 클래스부터). `pii_filter`는 `_flag(name, default)` 헬퍼 1개로 5함수 통합 · 설정 읽기 `try/except` 제거 | 모듈 테스트 · 플래그 on/off 파라미터 테스트(D-075 governance OFF 바이트 불변) |
| **X3** | 상수 사본 | `DEGRADED_KEY = "degraded_reason"` — `host_inspect.py:39` · `portal_help.py:63` · `apm_query.py:144` · `doc_query.py:53` + 리터럴 `subagents.py:682,745` · `replanner.py:1058` / `DISPLAY_CUT_KEY` — `apm_query.py:204` ↔ `result_aggregator.py:305` | 4벌 + 2벌 | `display_rows_cut`이 어긋나면 CSV 원천이 절단본이 됨(데이터 손실) | M | **SSOT** — 핸들러 결과 키 상수 모듈 1개(계층: orchestration보다 안쪽 — `src/domain/` 후보) | 상수값 단언 테스트 + grep 재유입 0 |
| **X4** | 판정 함수 사본 | `_capability_ownership_on` `subagents.py:300` = `intent_planner.py:1383`(본문 동일) + `semantic_router.py:520 _ownership_enabled`(load_config 변형) · `_ENGINE_ALIASES`/`engine_key` `schema_probe.py:89-95` = `db_registration_service.py:101-109` · `_zone_label` `realtime_usage.py:90` / `host_sweep.py:47` / `query.py:1442`(조회 순서가 달라 미등록 db_id 폴백 결과 상이 가능) · `_now_iso` 4벌(`admin_jobs.py:51` · `structure_store.py:95` · `db_structure_service.py:120` · `apm_jobs.py:225`) | 4개 개념 · 11벌 | 한쪽만 고치면 단 간·화면 간 결과가 갈라짐 | M | **SSOT** — 정본 1곳으로 모으고 사본은 import로 대체. `engine_key` → `utils/sql_dialect.py` · `zone_label(db_id)` → `routing/domain_config` · 소유 판정 → `routing/capability_owner` · `_now_iso` → `utils` | `_zone_label` 3벌은 **먼저 db_id 전수 파라미터 특성화(W0)** — **U-1 확정: 출력이 같은 db_id 집합에서만 정본으로 합치고, 하나라도 다르면 3벌을 현행 유지**(통일은 행동 변경 · 별건) |
| **X5** | 침묵 폴백 | src 침묵형 18곳(`redis_cache.py` 7 · `pii_filter.py` 5 · `fabrix_client.py:116` 등) · noise_gate `alarm_worker.py:291,308,326,347,798,831…`(`_build_*` → `return None`) · `server_identity.py:161,170` | `except Exception` 651곳 중 침묵형 표본 | CLAUDE.md 「침묵 폴백 금지」와 충돌 — 설정 없음과 설정 오류를 구분 못 함 | M | 로그 한 줄(`warning`, `exc_info`) 보장 또는 구체 예외로 축소. **651곳 일괄 정리는 하지 않는다**(과잉) — 표본 군집만 | caplog 단언 테스트 1건씩 · 반환값 불변 |
| **X6** | 규칙 우회 · 환경 직접 조회 | `clients/fabrix_client.py:54-56`(생성자 인자 없으면 `os.getenv("FABRIX_BASE_URL", "http://localhost:8002/v1")`) · `ollama_client.py:51-52` · `noise_gate/infrastructure/embedding_provider.py:156`(`os.environ` **쓰기**) | 45곳·18파일 중 설정 판단용 | `.env` 값은 `os.environ`에 없으므로 호출부가 값을 안 넘기면 침묵 오설정 | L | **`plans/163`으로 이관(U-6 확정)** — 2026-10-14 실측: 운영 호출부(`src/llm.py:336-342`·`:477-482`)와 `bind_tools` 복제(`fabrix_client.py:263`·`ollama_client.py:292`)는 인자를 항상 넘기지만, ① `fabrix_base_url` 필드 기본값이 `""`(config.py:51)라 빈 값이면 `os.getenv` → `http://localhost:8002/v1`로 폴백하고 ② `ollama_client.py:52`는 `os.getenv("LLM_OLLAMA_BASE_URL", base_url)`로 **프로세스 env가 인자보다 우선**한다 — 폴백 제거는 두 경우 모두 행동 변경 | `plans/163` 성공 기준 |

### 3.2 interface — `src/api` (A)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방 | 검증 |
|---|---|---|---|---|---|---|---|
| **A1** | Duplicated code | `query.py` `event_generator` 2801-3195(텍스트) ↔ 3663-4022(파일) · 비스트림 `process_query` 2476 ↔ `process_file_query` 3207 · SSE 응답 헤더 dict 6곳 · 조기 반환 미니 제너레이터 4곳 · `create_initial_state` 인자 15개 2곳(3295-3318 ↔ 3631-3654) | 공백 정규화 diff 126줄/약 375줄 → 약 2/3 동일 | 응답 필드 하나 추가에 4~6곳 수정 — 「4경로 대칭」이 수작업 규율 | **H** | **DRY · Extract Function** — `_stream_graph_turn(...)`(공통 스트림 본문) · `_build_turn_response(...)`(응답 dict) · `_sse_response(gen)`(헤더). 차이(시드 상태·한도·done 추가 필드)는 **인자로** 받는다. Template Method(추상 클래스)는 훅 3개라 과잉 | **(W0) SSE `done` 페이로드 키 집합 스냅샷** + `tests/test_api/test_stream_*`·`test_plan119_stream_timeouts.py`·`test_plan120_stream_scope.py`·`test_plan121_stream_dependency_notes.py`·`test_query_stream_progress.py` 등 15파일 |
| **A2** | God module · Feature envy | `query.py` 4,312줄 — 라우트 아닌 로직 약 2,400줄: 승인 의사 해석 509-710 · 진행 페이로드 `_extract_node_progress` 1037(169줄 cc56 · 노드명 분기 16곳) · 입력 상태 조립 `_build_turn_input_state` 1248 · 존·범위 역질문 1252-1990 · 인가 필터 1991-2100 · 타임아웃·부분응답 2299-2467 | 함수 105개 · 함수 내부 import 55건 · 70개 테스트 파일이 직접 import | 라우트가 state 키 수십 개를 직접 앎 — 단 추가마다 라우트 수정 | **H** | **SRP · Move Function** — `src/api/query/`(SSE·응답 전용) + 순수 판정(역질문·인가·승인 해석)은 application 모듈로. 진행 요약은 **표 주도 dispatch**(`dict[node_name → summarizer]`). `query.py`는 라우트 4종 + **전 심볼 재노출 shim**(§5.0) | 위 + `test_plan104_turn_identity.py`·`test_plan119_zone_parse_reuse.py`·`test_plan121_plan_summary.py` · import 스모크 |
| **A3** | Long method | `process_query_stream` 2681(516줄 cc65) · `process_file_query_stream` 3516(508줄 cc63) · `process_file_query` 3207(207줄) · `process_query` 2476(200줄) | 한 함수 8책임(인가→게이트→상태→마감→실행→응답→저장→감사) · 클로저 캡처 15+ 변수 | 부분 테스트 불가 | **H** | **Extract Function** — `_run_pre_gates(...) -> PreGateResult \| None`(인가→역질문→메모리삭제→선택거부 순서 보존) | `test_plan104_db_authz.py`·`test_plan132_zone_gate_defer.py`·`test_query_audit_path.py`·`test_plan147_w3_scope.py` |
| **A4** | SRP 위반 · 복붙 정리 코드 | `server.py` `lifespan` 291-625(334줄 cc45) — 책임 11+ · 설정 플래그 if 8개 · 종료부 `task.cancel(); await; except (Exception, CancelledError): pass` 5회 · redis/pool close 4회 | — | 서비스 추가마다 기동·종료 두 곳 수정 | M | **Extract Function** — `_cancel_quietly`/`_close_quietly` + `_start_alarm_worker`·`_start_sse_bridge`·`_start_incident_tracking`·`_init_auth_db`(정리 콜백을 `AsyncExitStack`에 등록). 플래그 분기는 기능 옵션이라 **제거 대상 아님**(D-161). ServiceRegistry 클래스는 과잉 | `test_lifespan_shutdown_cancel.py`(CancelledError 삼킴 고정 — 예외 튜플 보존) · `test_plan147_w4_admin_startup.py` · `test_routes.py` |
| **A5** | SSOT 부분 위반(인가) | role 인라인 판정 `noise_dashboard.py:775` · `alarm.py:1114,1643`(`"admin"` 리터럴) · `query.py` 다수 · `scope.py:55` / `type` 검증 이중 구현 `admin_auth.py:97` ↔ `dependencies.py:116` | 인라인 판정 8곳+ | 관리자 정의 변경 시 8곳 수정 · 이중 구현 불일치 위험 | M | **SSOT** — `is_admin(user)` 단일 술어 · 토큰 검증 함수 1개(`expected_type` 인자). **사용자 토큰의 `type` 미검증 보강은 행동 변경 → `plans/163` S2로 분리(U-6 확정)** | `test_admin_rbac.py`·`test_alarm_zone_rbac.py`·`test_query_result_access.py`·`test_plan104_db_authz.py` |
| **A6** | 매직 문자열 | SSE `type` 리터럴 `"done"` 11 · `"token"` 8 · `"meta"` 4 …(상수 모듈 없음 · 소비측 `thread_history.py:69`·`stream_failure.py`·프런트) · `_STREAM_KNOWN_NODES`(query.py:259-275, 38개)가 graph.py `add_node` 43회와 별도 수작업 | — | 노드 추가 시 두 곳 동기화 | M | **SSOT** — `src/api/sse_events.py` 상수 + `sse_event(type, **fields)` 팩토리. 노드명은 **정합 테스트만**(노드명 SSOT 모듈은 범위 대비 과잉) | stream 테스트 군 + 신규 `_STREAM_KNOWN_NODES ⊆ graph 노드` 정합 테스트 |
| **A7** | God module | `admin.py` 2,070줄(라우트 약 30 · 책임 7) — `update_settings` 696-850 · `reload_settings` 854-1025 · `.env` I/O 229-390(infrastructure 성격) / `settings_catalog.py` 1,250줄(카탈로그·파서·검증·마스킹 + 상수 약 700줄) / 감사 래퍼 3벌(`admin.py:1193` · `db_structure.py:298` · `schema_cache.py:22`) | — | — | M | **SRP** — `APIRouter` 분할(prefix·경로 불변) · `.env` I/O → `src/infrastructure/env_file_store.py` · 감사 래퍼 1개로. settings_catalog 상수 분리는 D-129 인트로스펙션 보존 확인 후 | `test_admin_*`·`test_settings_reload.py`·`test_settings_catalog.py`(카운트 단언 선재 실패는 D-179 주의대로 건드리지 않음) |
| **A8** | 라우트의 도메인 오케스트레이션 | `alarm.py` `analyze_alarm_test` 875(204줄 · payload 약 30키 조립 · 노드 직접 호출) · 오류 반환 블록 2곳 동일 · Pydantic 모델 약 20개 동거(62-540) · arch 경고 `alarm.py:1407`(interface→noise_gate.prompts) | 클론 `alarm.py:1009-1047` ↔ `2114-2152` | 워커 경로와 payload 조립 중복 가능(미측정) | L | Move Function — payload 조립을 `noise_gate/application` 함수로(D-139 방향 유지: src→noise_gate) · 스키마 모델 분리 | `test_alarm_*` · `noise_gate/tests` |

### 3.3 orchestration·routing·graph (O)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방 | 검증 |
|---|---|---|---|---|---|---|---|
| **O1** | God module · Divergent change | `apm_query.py` 3,998줄 — 책임 13구간(인자 220-470 · 창 473-785 · 대상 785-1075·2837-3165 · 호출 1079-1335 · 보기 선택 1355-1540 · 소스 사다리 1662-1900 · 참조 1897-2085 · run 2088-2521 · 첫 홉 2523-2835 · 작업·고지 3166-3430 · 수집·답 3426-3980) | 함수 144 · 상수 약 40 · 테스트 61파일이 언급 | 어떤 APM 계획이든 이 파일을 고침 · replanner·intent_planner가 지연 import | **H** | **SRP · Extract Module** — `src/orchestration/apm/`(window·args·targets·calls·selection·reference·first_hop·jobs_notices·collect·run) + `apm_query.py` **Facade 재노출**. 순수 함수부터 옮긴다. **seam 보존(U-3 확정 · D-337 ②)**: `run_apm_query`·`_SESSION_FACTORY`(215 정의 · 2280 `run_apm_query` 안에서만 읽힘 · 테스트 38파일 52곳 patch)와 테스트가 `apm_query`에서 patch하는 심볼(`link_business_names` 11 · `get_registry` 6 · `link_hostnames` 5 · `poll_job` 3 · `render_view_rows` 2)을 **호출하는 함수**는 `apm_query.py`에 남긴다 — 테스트 수정 0 · 순환 없음 | `test_plan134_*`·`test_plan147_w2_*`·`test_plan130_*`·`test_plan125_apm_active_path.py`·`test_plan132_source_selection.py`·`test_plan159_minute_consumers.py` |
| **O2** | Long method · Switch | `run_apm_query` 2088-2521(433줄 **cc184** · early return 19 · 지역 변수 25+) — 호출 구성 루프 2330-2400의 분기 축은 `ViewSpec.target` 5종(""·reference·named·fleet·optional) | 저장소 최대 cc | 수정마다 회귀 위험 · 부분 테스트 불가 | **H** | **Compose Method + 표 주도 dispatch** — `{target: _calls_xxx}` 함수 5개 dict · 단계 간 상태 `@dataclass _RunCtx` 1개 · 종단 판정 `_decide_outcome`(거부 코드 사슬 **순서 그대로**). Strategy 클래스는 과잉(어휘 5종 닫힘 · 공유 상태 6개) | O1 테스트 + 거부 코드(`apm_calls_failed`·`apm_target_unresolved`·`apm_not_queried`·`source_unavailable`…) 단언 전수 grep |
| **O3** | SSOT 위반(판정↔배선) · Long method | `graph.py` `build_graph` 505-975(470줄 cc39) — `not use_deep_agent and not config.enable_intent_orchestration` 3회 반복 · `observability/ladder.py:72 resolve_ladder_tier`는 배선 뒤 로그용으로만 호출되고 docstring(75-76)이 「build_graph 순서와 일치해야 한다」는 **수동 동기화 계약** · 주석 523-531 「기준 3단」(D-251과 불일치) | — | 사다리 판정이 두 곳에 정의 | M | **SSOT** — 선두에서 `resolve_ladder_tier()` 1회 계산 → 배선은 그 결과만 읽음 · 단별 `_wire_tier1/2/3/legacy` + `_wire_shared_front`·`_wire_single_db_tail` 함수 분할. Strategy/Factory 클래스 미도입(단 4개 닫힘 · 공유 상태 3개). **단·플래그 분기는 하나도 제거하지 않는다**(D-161·D-251). 낡은 주석 정정 | **(W0) 플래그 매트릭스별 그래프 노드·엣지 집합 스냅샷** + `test_graph_orchestration.py`·`test_deep_agent_wiring.py`·`test_plan103_tier3_plan_loop.py`·`test_ladder_startup_log.py`·`test_graph_routing_gaps.py` |
| **O4** | God module · Inappropriate intimacy | `subagents.py` 2,346줄 — `SubAgentSpec`(148) · DB 대상 정책 300-940(약 640줄) · 격리 입력 1173-1630(약 460줄) · `run_data_query_pipeline` 1721(327줄 cc66) · `_pack_pipeline_result` 2099 / `_make_isolated_input`을 `agent_orchestrator.py:458`·`deepagents_tools.py:384`·`tier3_plan.py:258`이 **`_` 접두 이름으로** import | 1·2·3단 공용 부품이 「private」 | 2·3단 전체 파급 · `SubAgentSpec`만 필요한 모듈이 subagents 전체를 import → 순환 | **H** | **SRP · Extract Module** — `agent_spec.py`(leaf · `SubAgentSpec`) · `db_targeting.py` · `isolated_input.py`(공개 이름 + 옛 이름 별칭 1사이클 — U-2 확정 · D-337 ③) · subagents에는 레지스트리·파이프라인만 | `tests/test_orchestration/`(97파일 언급) · `arch_check` · import 스모크 |
| **O5** | 순환 회피 지연 import | `replanner.py:581` · `intent_planner.py:1404` · `deep_agent.py:475` + SCC 7개(orchestration 7모듈 · apm_gateway.interface 4 · noise_gate.infrastructure 3 · `scope_tools`↔`tools` · `drm.decryptor`↔`scsl` · `api.routes.admin`↔`api.server`) | 함수 내부 import 623건 중 순환 회피 의심 고유 엣지 11 | 숨은 결합 · patch 경로 취약 | M | **ADP(비순환 의존)** — O4의 leaf 분리로 orchestration SCC 해소 후 상단 import로 | `arch_check` · `python -c "import src.graph"` |
| **O6** | 2·3단 사이 같은 판정 중복 | 존 역질문 꼬리 `subagents.py:328-424` ↔ `semantic_router.py:712-774`(약 25줄 동일) — **앞단 비발동 신호는 서로 다름**(`_has_new_location_db_signal` vs `ZONE_SKIP_SIGNAL_TERMS`) | — | 한쪽만 고치면 단 간 동작이 갈라짐 | M | **SSOT** — 공통 꼬리만 `zone_clarification_for_targets(...)`로 추출(동작 불변). 신호 함수 통일은 행동 변경 → **U-1 확정: 하지 않음(현행 유지 · 별건)** | `tests/test_semantic_routing/`·존 역질문 단언 grep — 양 단 모두 통과 |
| **O7** | Long method · Primitive obsession | `replanner` 82-383(301줄 cc66 — 조기 종료 가드 11종 + 신규 task 필터 8종) · 핸들러 결과 dict 암묵 스키마(읽는 distinct 키 subagents 130 · result_aggregator 105 · apm_query 231) · `result_aggregator` 1,892줄(`_merge_task_results_by_identity` cc45 · `_finalize_task` 약 140줄 · `_build_output_state` 약 110줄) | — | 키 오타·누락이 런타임에만 드러남 | **H**(replanner) | **순서 있는 규칙 표**(`GUARDS`·`FILTERS` 함수 튜플 — Chain of Responsibility의 함수형) · 핸들러 출력 `TypedDict HandlerResult` 선언(런타임 불변). pydantic 전면 이행은 미도입(체크포인터 직렬화·`**extra` 병합과 충돌) | `test_replanner.py`·plan119·plan121 계열 · mypy |
| **O8** | Long method | `semantic_router` 98-512(415줄 cc51 — 결정적 게이트 6종 → LLM 분류 → 의도별 분기 → 필터·핀·소유·역질문) · `intent_planner._plan_turn` 635(232줄 — 결정적 선처리 사다리) | — | — | M | **Extract Function** — `_deterministic_gates`·`_route_by_llm`·`_finalize_targets` · `_plan_turn`은 `[(pred, builder)]` 표(저우선). 2·3단 게이트 표 공유는 미도입(입력 state vs task가 다름) | `tests/test_semantic_routing/`·`test_graph_routing_gaps.py` |
| **O9** | Long method(겉보기 cc) | `routing/registry.py` `parse_registry` 851-1014(165줄 cc46 — 대부분 comprehension 필터 합산) · `apm_query._coerce_arg` 388(cc50 — `ViewArgSpec.kind`별 if 사슬) | — | — | L | 같은 파일 안 `_parse_zones/...` + `_warn_dangling_refs` 추출(기존 `_parse_views` 스타일 완결) · `_coerce_arg`는 `{kind: coercer}` dispatch. 파일 분할·Registry 클래스 미도입 | `test_plan125_registry.py` 등 11파일 · 경고 문구 caplog 불변 |

### 3.4 application — nodes·db_adapters·utils·state (N)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방 | 검증 |
|---|---|---|---|---|---|---|---|
| **N1** | Duplicated code · Shotgun surgery(단일/멀티) | 폼필 피벗 전처리(월 시리즈·Vendor+Model 결합·EAV 분류) `query_generator.py:283-540`(`_try_build_form_fill_pivot_sql` 231줄 cc70) ↔ `multi_db_executor.py:2195-2340, 2427-2640`(`_build_mapping_user_parts` 211줄 cc63) — 조립기 `build_form_fill_pivot_sql`만 공유 · 복붙 래퍼 7쌍(`_get_eav_pattern` qg:1696=multi:286 · `_render_samples_secure` qg:1961=multi:3261 · `_unmapped_fields_section` · `_build_stepwise_deps` · `_select_query_history_examples` · `_format_schema*` · `_SCHEMA_EXAMPLE_TABLE`) | 병행 구현 약 600~800줄 | D-146~D-151 폼필 규칙 수정 시 두 곳 · Known Mistakes 2026-09-21 | **H** | **DRY · Extract Module** — `FormFillPivotPlan`(dataclass)을 반환하는 순수 함수 1개를 공용 application 모듈로, 단일/멀티는 렌더만. 복붙 래퍼는 import로 즉시 대체(최저 위험). plans/69가 남긴 플래그 OFF 비대칭(`TEXT2SQL_MULTI_FULL_VALIDATION`)은 **행동 변경이라 범위 밖** | `tests/test_prompt_render_matrix.py`(sha256 12키) · `scripts/prompt_render_diff.py --ci` · gold_smq SQL 바이트 · 폼필 골든 · **(W0) `build_form_fill_pivot_sql` 입력 dict 덤프 대조** |
| **N2** | 계층 오배치 · Large module | `utils/query_gen_common.py` 2,288줄 · 함수 83 · 소비 파일 89 — 관심사 12종(기간 해석 197-345 · 한도 549-724 · EAV CAST 재작성 781-961 · 폴스타 규약 `missing_dtime_filter` 1108 · 필드명 휴리스틱 · 프롬프트 블록 1274-1511 · LLM SQL 추출 1583-1692 · 메모리 명령 · 용어 매칭 1750-1825 · 승계 2055 · 존 명확화 2180-2245 · 폴백 카운터) | 팬인 41(3위) | 「이름은 utils, 성격은 application/prompts」 | M | **SRP · Extract Module** — `period_resolution`·`limit_policy`·`sql_rewrite_eav`·`prior_turn_succession`·`text_terms`·`zone_clarification`·`llm_sql_extract`로 분할 + `query_gen_common` 재노출 shim. 프롬프트 블록·폴스타 규약은 계층 정합 위치로(arch_check 확인) | import 스모크 · 소비 테스트 · `arch_check` · `overfit_check`(기준선 항목 이동 — §5.0) |
| **N3** | Misplaced responsibility | 노드 간 import 실질 38건 — 대상이 `column_deriver` 6 · `table_selection` 5 · `intent_frame_builder` 4 · `candidate_generator` 4 · `prompt_blocks` 3 · `key_bridge` 2 · `candidate_selector` 2(그래프 노드가 아닌 순수 헬퍼가 `src/nodes/`에 있음) | arch 경고 112건 중 | 함수 내부 import 확산(multi 25 · qg 10 · output_generator 9) | M | **Move Module** — 헬퍼를 비노드 application 하위로. **arch_check 규칙은 W1에서 「노드 패키지 간」으로 좁힌다(U-4 확정 · D-331 ①)** — 좁힌 뒤 경고 43(노드↔노드 38 + prompts 직접 참조 5) · N3 이동 대상 7모듈 26건이 빠지면 17 | `arch_check` 경고 수 비교 · 이동 대상 소비처 테스트 · patch 경로 shim |
| **N4** | 반복 조합 · (D-089 허용 범위) | `get_polestar_db_ids()` + `get_adapter(db_id, ids)` 노드 8곳(`multi:628,2094,2185,2795,3015` · `qg:595,1402,1679` · `result_organizer:437` · `query_validator:322`) · 노드 로컬 게이트 `_is_polestar_db`(multi:2789) | 11곳 반복 | 두 번째 어댑터(ITAM) 편입 시 노드 수정 | M | 헬퍼 `get_adapter_for(db_id, app_config)` 1개. **노드의 `db_adapters.polestar.assembler` 등 직접 import 13건은 D-089가 명시 허용**(「조립기는 훅이 아니라 어댑터 모듈 직접 임포트」 · 「두 번째 어댑터 전까지 훅 확대 금지」) — **위반 아님, 처방 대상 아님** | `test_db_adapters.py` · `overfit_check` |
| **N5** | Long method · God object | `schema_analyzer` 582-1020(438줄 cc89) · `_run_output_generator` 107(251줄) · `realtime_usage_lookup` 97(223줄 cc53) · `result_organizer` 46(222줄) · `validate_sql` 172(199줄) · `_validate_forbidden_joins` sql_validation:1208(162줄) · `sql_time_conditions._read_condition` 446(cc62) · `_MultiRun`(multi:299-358 · 필드 32) | 80줄 초과 multi 14 · qg 8 · output_generator 5 | — | M~H | **Extract Function**(단계명 부여 · 분기 로직 불변). `_MultiRun`은 읽기 전용 입력과 가변 누적 분리까지만. `validate_sql`은 `check_*` 순서 의존 주의 | 모듈 스위트 기준선(worktree) 대조 · 로그 순서 caplog |
| **N6** | Primitive obsession(실피해 있음) | `AgentState` 136필드(state.py:86-448) · `state.get(` 765곳 · 출력 노드가 AgentState에 **없는** 키 8종을 읽음(`executed_sqls`·`period_sources`·`limit_sources`·`condition_check`·`task_id`·`per_task_finalize`·`all_null_downgrade`·`original_user_query`) — `result_aggregator._build_output_state` 허용목록 dict 때문 | docs/18 사고 4건(2026-07-30 · 08-26 · 09-21 · 10-06) | 키 오류가 테스트로 안 잡힘 | M | **선언만**: `OutputState` TypedDict(미선언 8키 포함) · 노드 반환 키 ⊆ `AgentState.__annotations__` 정합 테스트 일반화 · `create_initial_state`(208줄)의 요청 스코프 키 목록 데이터화. 전면 dataclass/pydantic 전환 미도입 | mypy · 신규 키 정합 테스트(런타임 불변) |
| **N7** | 순서 있는 수작업 체인 2벌 | `output_generator.py` `_append_*_note` 약 20종을 텍스트 경로(192-209)와 문서 경로(251-278)에서 **서로 다르게** 손 나열 — 텍스트에만: `current_month_partial`·`limit_truncation`·`generator_notes`·`condition_changes` · `_preview_rows`(private)를 `deepagents_tools.py:23`이 import | 파일 2,928줄 · 함수 92 | 노트 추가 시 경로 누락 | M | **순서 있는 함수 튜플**(`TEXT_NOTES`·`DOC_NOTES`) + `output_notes.py` 이동. 형식별 Strategy는 미도입(3갈래 · 반환 형태 이질). **U-8 확정: 두 튜플은 현행 구성·순서를 그대로 옮긴다**(문서 경로 4종 누락은 고치지 않음 — §8 후속 확인) | 응답 스냅샷 + 노트 호출 순서 단언 테스트(리스트 = 기존 순서) |

### 3.5 infrastructure — schema_cache·document·config (I)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방 | 검증 |
|---|---|---|---|---|---|---|---|
| **I1** | Facade 우회 · God class | `SchemaCacheManager`(cache_manager.py:166 · 57메서드 · `_backend == "redis"` 분기 39회) — 외부에서 private `_redis_cache` 직접 접근: `schema_analyzer.py:939-973`(5) · `multi_db_executor.py:3218,3224` · `excel_csv_converter.py:250,269` / `RedisSchemaCache`(69메서드 · 키 공간 7종) / `StructureStore._key`(184)가 `schema:` 키 규칙 중복 정의 | — | 키·TTL 정책 변경이 3클래스 · 쓰기 경로 2개(Manager 이중 저장 vs StructureStore Redis 전용)로 일관성 규칙이 한 곳에 없음 | **H** | **Facade 완결** — 우회 지점마다 Manager 공개 메서드 추가 후 교체 · `_prefer_redis()` 헬퍼 1개 · **Composition**: `RedisSchemaCache`가 키 공간별 저장소 객체(schema/fingerprint · descriptions · synonyms(약 750줄, 1순위) · csv·form_memory)를 속성으로 보유, 기존 메서드는 위임(호출부 무변경) · 키 빌더 1곳. 백엔드 Strategy는 미도입(파일 폴백이 일부 메서드만 — 대칭 인터페이스 강제는 과잉) | **(W0) fakeredis 키·값 스냅샷**(`scan_iter('schema:*')` 전후 동일) · schema_cache 테스트 · `arch_check` |
| **I2** | God file | `document/field_mapper.py` 2,451줄 — 3단계 매핑·LLM 매핑·유사어 발견·Redis 등록/피드백·md diff(`apply_mapping_feedback_to_redis` 2245 206줄 cc45 · `_apply_llm_mapping_with_synonyms` 1429 192줄) | 최상위 함수 37 | — | **H** | **SRP · Extract Module** — `mapping_steps`·`llm_mapping`·`synonym_discovery`·`redis_registration`·`md_diff` + 재노출 | 문서 매핑 테스트 · 폼필 골든 |
| **I3** | 기반 클래스 오배치 · Large service | `AdminServiceBase`가 `db_structure_service.py:419`(구현 파일)에 있어 형제 서비스가 import · `asset_generation_service.py` 2,110줄(보조 타입·파서·검증 1329행 이후 약 800줄) | — | — | M | `admin_base.py`로 이동 · asset_generation 보조 타입 분리 | 관리자 서비스 테스트 |
| **I4** | Large module · 설정 객체에 도메인 판정 | `config.py` 1,834줄 · 클래스 28(`NoiseGateConfig` 319줄) · `model_post_init` 4곳 · `get_polestar_db_ids`·`get_*_base_url` 등 운영 판정 메서드 | 팬인 70(1위) | — | M | `src/config/` 패키지화 + `from src.config import …` 재노출(호출부 94+곳 무변경) · 파생 메서드 이동은 보류. **D-129(인트로스펙션 SSOT)를 깨지 않는지 W0 실측** 후 착수(**U-10 확정: 조건부** — W0 실측표에서 `settings_catalog`가 config 클래스 열거에 묶이지 않거나 재노출로 `field_index()`가 같으면 W2에 포함, 아니면 W2에서 빼고 보류) | `python -c "import src.config"` · `test_settings_catalog*.py` · `catalog_diff.py --ci` |
| **I5** | Long method(판정) | `doc_qa/service._answer_impl` 258(217줄 cc60) · `audit_repository.get_stats` 229(208줄) · `host_discovery.decide_systems` 454(cc43) · `time_spec.resolve` 625(cc46) · `db_registration_service.config_snippets` 1068(cc39) | 내 범위 100줄 초과 27 | — | M | Extract Function — 판정 함수는 **(W0) 특성화 테스트 먼저**, 규칙표 평탄화는 행동 불변 확인 뒤에만 | 특성화(입출력 골든) |

### 3.6 noise_gate·sre_agent (G)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방 | 검증 |
|---|---|---|---|---|---|---|---|
| **G1** | Long method · 규칙 if 사슬 | `noise_gate/domain/notification_policy.py` `decide_notification` 394(540줄 **cc98** · 키워드 인자 약 25 · 플래그 10개 읽음) — 단계 번호 0.5·3·4·5·6·6.2·6.4·6.5·6(중복)·7·7.5·7.6·8·9·9.5 · 7.6 블록이 매트릭스 함수 정의 **뒤에** 물리 배치 | 저장소 2위 cc | 번호 사이 삽입 반복으로 체계 붕괴 · 규칙 추가 시 회귀 위험 | **H** | **순서 있는 규칙 표**(조기 종결 단계 0.5~7.5를 `RULES` 튜플 + for 루프 — Chain of Responsibility의 함수형) · 입력은 `frozen dataclass GateInputs` · step 9 누적 조정은 체인이 아닌 함수로 유지. 규칙별 클래스 계층 미도입. **심각도3 절대 PAGE·보수적 PAGE(D-048) 순서 보존** | **(W0) 골든 특성화**(플래그 조합 × 심각도 × 해소·유지보수·침묵 → 티어·reason 덤프) + `test_notification_policy.py`(32건)·`test_gate_off_regression.py`·`test_plan60_flags_off_regression.py`·`test_plan144_apm_policy.py`·`test_flapping_gate.py`·`test_min_severity_gate_handoff.py` |
| **G2** | God class | `alarm_worker.py` `AlarmWorker` 152(2,310줄 · 58메서드) — `_build_*` 18종 약 700줄 · episode·late-promotion 약 20메서드 · 신호 탐지(inhibition·flapping·storm·correlation) · dedup · `_process` 1182(410줄 cc44 · 플래그 8개) | — | — | **H** | **SRP · Extract Class(합성)** — ① 와이어링 `worker_wiring.py`(기계적) → ② `EpisodeCoordinator` → ③ 신호 탐지·`DedupService`. `_process` 분할과 클래스 분할을 **같은 Wave에 하지 않음**. 이벤트 버스 미도입 | `test_noise_gate_graph_integration`·`test_gate_off_regression`·`test_plan60_flags_off_regression` · `worker._xxx` 접근 테스트는 위임 shim으로 보존((W0) 접근 수 측정) |
| **G3** | Long method | `investigation_trigger_node` 57(244줄 cc46) · `notification_gate_node` 45(cc39) · `alarm_context_enricher_node` 449(215줄) · `polestar_noise_context.fetch` 449(188줄) · `alarm_notifier.py` 1,437줄 · `decision_store.py` 1,044줄 | — | — | M | 가드 절 Extract Function. 대분할은 G1·G2 이후 재평가 | 노드별 기존 테스트 |
| **G4** | 노드 간 의존 · private import | `alarm_context_enricher.py:35`→`server_identity.is_apm_source`(arch 경고) · 같은 파일 57행 `polestar_noise_context._unavailable` | arch 경고 1 | — | L | `is_apm_source` → `noise_gate/domain` · `_unavailable` 공개명 승격(옛 이름 별칭 1사이클 — D-337 ③). 경고는 D-331 ①로 규칙상 먼저 사라지지만(대상이 노드가 아님) 처방은 유지 | `test_server_identity.py` · import grep |
| **G5** | 매직 값 분산 | `alarm:` 접두 Redis 키 리터럴 비테스트 8파일(cross_source · enricher · redis_queue · polestar_metric_baseline · alarm_worker · incident_events · polestar_noise_context · apm_noise_context) · `notification_gate.py:250`이 `APP_IMPACT_LEVELS` 재선언 | — | 키 오타 시 침묵 단절 | M | **SSOT** — `noise_gate/domain/keys.py` 키 빌더 1곳(티어 상수는 이미 SSOT 양호) | 키 문자열 동치 단언 + grep |
| **G6** | 경계 허용 범위 초과 | `noise_gate` → `src.observability.investigation_metrics` · `src.domain.host_availability` · `src.domain.host_authz`(D-139 ② 역방향 최소 범위는 config/llm/utils/routing) | 3건 | 경계 침식 | M | **U-5 확정: D-139 부기로 허용 목록 확장(코드 이동 없음)** — 세 모듈 모두 stdlib만 쓰는 순수 모듈(154·234·206줄)이고 src 쪽 소비처가 각 2~4곳이라 `noise_gate`로 옮기면 `src → noise_gate` 의존이 새로 늘어난다 | 허용 목록 밖 신규 역방향 import 0(import grep) |
| **G7** | 문구 조립 if 사슬 | `sre_agent/application/briefing_builder.py` `build_briefing` 272(150줄 cc48) | — | — | L | 근거 상태 판정과 렌더 분리(패턴 불필요). `InvestigationDispatcher`(530줄)는 분할 시기 아님 | `sre_agent` briefing 테스트 23건(자체 venv) |

### 3.7 mcp_server·apm_gateway (M)

| ID | 범주 | 위치 | 정량 근거 | 영향 | 심각도 | 처방 | 검증 |
|---|---|---|---|---|---|---|---|
| **M1** | Long method · 위임 보일러플레이트 | `apm_gateway/interface/server.py` `register_tools` 331-1266(935줄 · 도구 18) — 본문은 `targeted(...)` + 9줄 `run_data(...)` 14회 반복 · 도구 이름 4회씩 · 공통 인자 재선언 | 저장소 최장 함수 · 그중 약 303줄은 시그니처·docstring(외부 계약) | — | **H** | `targeted`+`run_data` → `dispatch` 헬퍼 1개 · 공통 인자 `Annotated` 별칭 · 도구군별 함수 분할. **동적 시그니처 생성 Registry는 미도입**(도구 스키마 = 외부 계약이 바뀔 위험) · 도구 이름·스키마 변경 금지 | **(W0) `await mcp.list_tools()` name·inputSchema·description JSON 스냅샷** + `test_tools_contract.py`·`test_boundary.py` |
| **M2** | God class | `application/tools.py` `ApmTools` 691-3579(2,889줄 · 66메서드) · fleet·scope·manage가 `_Limits`·`_check_n`·`_given` 등 `_` 접두 심볼을 import · `core._at` 8 · `core._all_failed` 7 · `core._reason_tail` 5 | — | — | **H** | **Facade + Composition 완결** — fleet·scope·manage가 이미 `core`를 받는 형태 → 공유 코어(719-1170)를 `ApmCore`로 분리, 나머지 도구군을 `core` 기반 클래스로, `ApmTools`는 Facade 유지. `_` 심볼 공용 모듈 승격(U-2 확정 · D-337 ③) | `apm_gateway/tests` 전체(`../.venv/bin/python -m pytest`) |
| **M3** | 복붙 실패 처리 관용구 | `except ApmError: if e.code == CONTRACT_VIOLATION: raise` + 「[한계] … 조회 실패」 32회(tools 22 · scope 5 · fleet 3 · manage 1 · poller 1) | 32 | 한 사본이 재발생을 빠뜨리면 계약 위반이 한계 고지로 강등 | M | 그 규칙만 담은 작은 헬퍼 1개(데코레이터 미도입) | 오류 경로 테스트 + 계약 위반 전파 단위 테스트 신규 |
| **M4** | Long method | `_period_ranking`(185줄 cc42) · `_event_rules`(145줄 cc48~55) · `_business_search`(140줄 cc47) · `apm_status_stats`(150줄 cc41) · `ranking`(152줄 cc43) · 순수 함수 `_period_stats`·`_sum_stats`·`_stats_delta`(tools.py:3320-3375) | — | — | M | Extract Function · 순수 함수 모듈 이동 | 출력 JSON 골든(sort_keys) — 없으면 (W0) |
| **M5** | Duplicated code | `mcp_server` `_now_iso`·`_err`·`_ok`·`_audit`가 `polestar_tools`·`promql_tools`·`openmetrics_tools` 3벌 · 폴스타 도구 9종 공통 서두(엔진 해석→max_rows→SQL→실행→감싸기) · `polestar_tools.py`(빌더 83-650 + 등록 402줄) · `tools.py`(도구 + 엔진별 스키마 SQL 약 290줄) | 클론 5.4%(패키지 최고) | — | M | 공용 응답 헬퍼 1모듈 · `_run_fixed_sql` 헬퍼(약 -90줄) · 빌더/등록 파일 분리. 응답 필드 배치는 도구군별로 다르므로 유지. **분리한 빌더 파일은 overfit EXCLUDE 대칭 필요**(polestar_tools.py가 EXCLUDE — 새 파일명이면 스캔에 걸림, §5.0) | `mcp_server/tests/test_polestar_tools.py`·`test_promql_tools.py`·`test_openmetrics_tools.py`·`test_tool_gates.py` |
| **M6** | 패키지 간 계약 사본 | 오류 코드 `instance_unresolved`·`source_unavailable` 리터럴이 `src`·`noise_gate`·`sre_agent` 8파일에 복제(정본 `apm_gateway/domain/errors.py`) · 도구 이름 `"apm_*"` 약 100회/13파일 · 수작업 `JOB_TOOLS` 목록 | — | 계약 드리프트 | M | **공유 라이브러리 미도입**(D-139·D-274 · `test_boundary.py`가 막음) → **계약 문서 + 계약 스냅샷 테스트로 SSOT 대체**: `spec/SPEC-apm-gateway.md` 오류 코드·엔벨로프 표 + 양쪽 테스트가 같은 JSON 스냅샷 대조 · 등록 도구 이름 ↔ `JOB_TOOLS` 정합 테스트 | 신규 계약 테스트 |

### 3.8 보안 성격 관찰 — `plans/163`으로 이관(U-6 확정)

이 절의 세 건과 X6은 **행동 변경**이라 리팩터와 분리하고(D-337 ⑥) 별도 보안 계획 `plans/163-security-hardening-separated.md`(security-auditor 동반)에서 처리한다. 2026-10-14 재확인한 코드 근거와 성공 기준은 163에 있다. 이 계획은 아래 「리팩터 중 처리」만 한다.

| ID | 관찰(요지) | 리팩터 중 처리 |
|---|---|---|
| **S1** | `mcp_server` 읽기 전용 강제가 단일 지점이 아님 — `validate_readonly`는 `execute_sql`의 readonly 소스에만(tools.py:119-122) · 폴스타 도구·스키마 헬퍼·`polestar_exporter.py`는 `pool.execute`(db.py:90) 직행 · asyncpg 풀에 세션 읽기 전용 설정 없음(db.py:44-49) | 가드 이동·강화는 163(단계 2)이 끝냈다는 전제로 M5(단계 4)는 163이 정한 위치를 그대로 둔다. 「모든 `build_*_sql` 출력 통과표」는 **163 W0이 만든다**(v1.2 — 166 W0에서 중복 작성하지 않음) |
| **S2** | 사용자 토큰 `_verify_user_token`이 `sub`만 확인하고 `type` 미검증(dependencies.py:31-53) · 시크릿은 분리돼 있음(D-070 — `auth.jwt_secret` ↔ `admin.jwt_secret`) | A5의 `type` 검증 공용 함수 추출은 **관리자 쪽 기존 동작만** 옮긴다. 사용자 쪽 검증 추가는 163 |
| **S3** | 내부 예외 문자열을 응답에 실음 — 사용자 경로 `query.py:2605,3357`(HTTP detail) · `:3181,4007`(SSE 오류) · `apm_jobs.py:57` · `conversation.py:71` 외 관리자 경로 7곳 | A1·A3 공통화 때 **메시지 문자열을 바꾸지 않는다**(W0 done 스냅샷이 고정) |
| **X6** | LLM 클라이언트 env 폴백(§3.1 X6) | W1에서 다루지 않는다 |

---

## 4. 원칙·패턴 매핑

### 4.1 적용하는 것

| 원칙/패턴 | 적용 스멜 | 왜 여기에 필요한가(실측 근거) |
|---|---|---|
| **SSOT** | X1·X3·X4·A5·A6·O3·O6·G5·M6 | 같은 값·판정이 2~14벌 — 실제로 반대 방향 기본값(X1)·데이터 손실 가능 상수(X3)가 존재 |
| **SRP(Extract Module/Class)** | A2·A7·O1·O4·N2·I2·I3·I4·G2·M2·M5 | 1,000줄 초과 파일 40개 중 변경이 몰리는 것만 — 책임 구간이 줄 범위로 실측된 것만 대상 |
| **DIP(주입)** | X2·X6 | 하위 계층의 전역 설정 직접 조회 → 테스트 주입이 monkeypatch 의존 |
| **ADP(비순환 의존)** | O5 | 지연 import로 숨긴 SCC 7개 |
| **DRY(공통 함수 추출)** | A1·A3·N1·M3·M5 | 쌍 경로 중복이 반복 실수의 실원인(Known Mistakes 2026-09-21 등) |
| **Facade 완결** | I1·M2·(O1·N2·A2의 재노출 shim) | 이미 Facade가 있는데 우회로가 열려 있음 — 새 Facade를 만드는 게 아니라 닫는 것 |
| **Composition** | I1(RedisSchemaCache 키 공간) · G2(AlarmWorker) · M2(ApmCore) | 상속·믹스인 대신 — M2는 fleet·scope·manage가 이미 쓰는 형태의 완결 |
| **순서 있는 규칙 표(Chain of Responsibility의 함수 튜플형)** | G1·O7·N7 | 순서가 의미를 가지는 if/early-return 사슬 · 번호 중간 삽입이 반복됨(G1). 클래스가 아닌 `(이름, 함수)` 튜플 |
| **표 주도 dispatch(경량 Registry)** | O2·A2(진행 요약)·O9(`_coerce_arg`) | 분기 축이 닫힌 어휘(`ViewSpec.target` 5종 · 노드명 · `kind`)로 이미 데이터 구동 |
| **Compose Method + 작은 컨텍스트 dataclass** | O2(`_RunCtx`)·G1(`GateInputs`) | 지역 변수 25+ · 키워드 인자 25 |
| **TypedDict 선언(런타임 불변 타입)** | N6·O7 | 키 오류 사고 4건 — 런타임 객체 전환 없이 mypy만으로 잡음 |

### 4.2 도입하지 않는 것 (Simplicity First)

| 후보 | 기각 근거 |
|---|---|
| query.py 4종 핸들러 Template Method(추상 클래스) | 변하는 지점이 시드 상태·한도·done 추가 필드 3개뿐 — 함수 인자로 충분 |
| 4종 핸들러를 단일 라우트로 통합 | 입력 계약(JSON vs multipart)·에러 표면이 다름 · 사용자 접점 변경(D-255) |
| 사다리 단별 Strategy/Factory 클래스 | 단 4개로 닫힘 · 공유 상태 3개(`graph`·`config`·`llm`) — 함수 4개로 충분 |
| `run_apm_query` 보기 종류별 Strategy | 어휘 5종 닫힘 · 분기가 상태 6개 공유 — 클래스화하면 인자 전달만 늘어남 |
| DBAdapter 전면 훅화 · Dialect Strategy | 어댑터 1종(Plan 63 §9 · D-089) · 방언 분기는 이미 `utils/sql_dialect.py`로 중앙화(노드 직접 `== "db2"` 1곳) |
| output_generator 형식별 Strategy | 형식 3갈래 · 반환 형태 이질 — 실제 스멜은 노트 체인(N7) |
| AgentState 전면 dataclass/pydantic 전환 | 체크포인터 델타 병합·LangGraph 계약과 충돌 · 765 호출부 |
| 핸들러 결과 pydantic 전면 감싸기 | `**extra` 병합·직렬화와 충돌 — TypedDict로 충분 |
| 스키마 캐시 백엔드 Strategy | 파일 폴백이 일부 메서드에만 — 대칭 인터페이스 강제는 과잉 |
| AlarmWorker 이벤트 버스 | 같은 프로세스 순차 파이프라인 — 간접화 비용만 큼 |
| apm_gateway 동적 도구 Registry(시그니처 생성) | 도구 스키마 = 외부 계약 — 생성 방식이 바뀌면 스키마가 흔들림 |
| 패키지 간 공유 라이브러리(오류 코드·시간 파서·응답 헬퍼) | D-139·D-274 양방향 import 0 · 시간 파서는 시간대 처리가 서로 다름 — 계약 스냅샷 테스트로 대체(M6) |
| 설정 Protocol/인터페이스 추상화 | 과잉 — 재노출 패키지화(I4)만 |
| 노드명 SSOT 모듈 | 범위 대비 과잉 — 정합 테스트만(A6) |
| `except Exception` 651곳 일괄 정리 | 대부분 로그 있음 · 일괄 치환은 회귀 위험 — 침묵 군집만(X5) |
| 노드 간 import를 「그래프 라우팅으로 해결」(arch_check 경고 문구) | 순수 헬퍼 호출이라 그래프 간선화는 부적절 — 헬퍼 이동(N3) |
| `_norm`·`_normalize`·`_parse_ts` 등 동명 함수 통합 | 본문이 서로 다른 개념(공백 제거·casefold·NFKC…) — 이름만 같음 |
| LLM provider · DB 백엔드 Factory 신설 | 이미 `llm.create_llm`(183-191) · `db/__init__.py:38`에 집중 |

---

## 5. Wave 분할

### 5.0 전 Wave 공통 규약 (D-337으로 고정 — 2026-10-14)

1. **동작 불변이 기본**이다. 행동이 바뀌는 처방은 이 계획에서 하지 않는다(§7 확정 — 보안·X6은 `plans/163`, 신호·기본값 통일은 §8). 사용자 접점 변경 0 → 매뉴얼 갱신 대상 아님(D-255 — 보고에 「매뉴얼 미반영: 사용자 접점 변경 없음」 명시).
2. **이동은 재노출 shim과 함께** — 함수·클래스를 옮기면 원 모듈에서 같은 이름을 재노출해 테스트의 import·`monkeypatch.setattr(<원 모듈>, …)` 경로를 1사이클 보존한다. **주의**: 이동한 함수가 원 모듈 전역(예: `apm_query._SESSION_FACTORY`)을 읽는 경우 재노출만으로는 patch가 닿지 않는다 — 이동 전 해당 심볼의 patch 지점을 grep하고, **원 모듈 전역을 읽거나 원 모듈에서 patch되는 심볼을 호출하는 함수는 원 모듈(Facade)에 남긴다**(D-337 ② · U-3 확정 — 테스트 수정 0이 기준).
2a. **모듈 밖에서 import되는 `_` 접두 함수·클래스는 공개명으로 승격하고 옛 이름을 별칭으로 1사이클 둔다**(D-337 ③ · U-2 확정). 별칭 제거는 그 영역의 다음 계획에서 `grep -w <옛 이름>` 0을 확인한 뒤.
3. **overfit 기준선은 경로 키**다 — 리터럴이 있는 코드를 새 파일로 옮기면 새 경로가 「신규 유입」으로 걸린다. 자기 이동분만 기준선 항목을 새 경로로 옮기고(원 경로 항목 삭제), `--update-baseline` 전면 재생성은 하지 않는다(CLAUDE.md 품질 게이트 · 2026-08-28·09-28 Known Mistakes). `mcp_server/polestar_tools.py`·`polestar_exporter.py`처럼 **EXCLUDE 대상 파일을 쪼개면 새 파일도 EXCLUDE에 대칭 추가**해야 한다(`scripts/overfit_check.py:75-85`).
4. **arch_check 계층 매핑은 최장 prefix**(`arch_check.py:124`) — 새 하위 패키지(`src/api/query/`·`src/orchestration/apm/`·`src/config/`)는 부모 prefix로 자동 매핑된다. 다른 계층으로 내리는 이동은 `MODULE_LAYER_MAP` 확인 필수.
5. **커밋 단위 = 스멜 ID 하나**(되돌리기 비용 최소). 순수 이동 커밋과 로직 분해 커밋을 섞지 않는다.
6. **회귀(D-303)**: Wave 중간은 implementer가 자기 테스트 + `python scripts/regress.py --base <계획 시작 SHA> --no-tests --files <바꾼 파일…>`. 공개 시그니처·`src/config.py`·`src/state.py` 변경으로 `[웨이브 회귀 필요]`가 나면 그 Wave 끝에 모듈 단위 회귀. **이 계획의 마지막 Wave는 W2**다(U-11 확정 — W3~W5는 각자 별도 계획서이고 각자 끝에서 1회). W2 끝에서 1회 `regress.py --base <계획 시작 SHA> --files <W0~W2 회귀 대상 파일 누적…>`. 전체 회귀(`--full`)는 사용자 요청 시만. 기준선 실패는 `plans/158` 18건과 대조.
7. **기준선 대조는 `git worktree add --detach <dir> <계획 시작 SHA>`**(stash 금지).
8. 독립 패키지 테스트: `mcp_server`·`apm_gateway`는 `../.venv/bin/python -m pytest`, `sre_agent`는 자체 venv.
9. 실 LLM 검증은 필요 없다(프롬프트 바이트 불변을 `prompt_render_diff`로 증명). 프롬프트가 바뀌는 커밋이 나오면 그것은 리팩터가 아니므로 되돌린다.

### 5.1 Wave 표

| Wave | 목적 | 스멜 | 위험 | 병렬 | 진행 단위(U-11 확정) |
|---|---|---|---|---|---|
| Wave | 단계(§0.1) | 목적 | 스멜 | 위험 | 병렬 | 진행 단위(U-11 확정) |
|---|---:|---|---|---|---|---|
| **W0** | 1 | 안전망 — 특성화·스냅샷 테스트만 추가(제품 코드 0) + 파손 범위 실측 | (전 Wave 전제) | 없음 | 패키지별 4트랙 병렬 | 이 계획 |
| **W1** | 3 | SSOT 기계 치환·사본 제거 — 저위험 | **D-331 게이트 구현(arch 규칙 · 폴백 린트)** · X1·X3·X4·A5·A6·N4·X5(`pii_filter` 제외)·G4·G5·N1(b 래퍼)·O3(주석만)·M3·M6 | 낮음 | 3a 게이트(팀 리드) → 3b src / noise_gate / apm_gateway | 이 계획 |
| **W2** | 4 | Move-only 모듈 분할(+ 재노출 shim) — 로직 불변 | O4·O5·N2·N3·I2·I3·N7(이동만)·M5·A7(라우터 분할·`.env` I/O)·A8·I4(U-10 조건 충족 시) | 중 | 파일이 안 겹치면 worktree 병렬 | 이 계획(**마지막 Wave** — 계획 끝 회귀) |
| **W3** | 5 | 쌍 경로 공통화 — 중복 제거 | A1·A3·N1(a)·O6(꼬리만)·I1·X2·X5(`pii_filter`) | 중~높 | 5a A1·A3 ∥ 5b N1(a)·O6 → 5c I1·X2·X5 | **별도 계획서 3건** |
| **W4** | 6 | 거대 함수 분해 — 규칙 표·dispatch | G1·O2·O7·O3(배선)·O8·O9·A4·N5·N7(체인)·I5·M1·M4·G3·G7 | 높음 | 6a~6f · 동시 2건까지 | **별도 계획서 6건**(G1·O2는 단독 계획) |
| **W5** | 7 | God class·God module 분할 | O1·A2·G2·M2·N6(선언) | 높음 | 7a~7e(각 선행 충족 순) | **별도 계획서 5건**(각각 단독 계획) |

**U-11 확정**: 이 계획서는 W0~W2를 직접 진행하고, W3~W5는 **착수 시 Wave마다(규모가 크면 스멜 단위로) 별도 계획서**를 만든다. 이 계획서는 W3~W5의 「상위 로드맵」이다 — 새 계획서는 §5.5~§5.7의 범위·성공 기준을 옮겨 쓰고, W0 스냅샷과 D-337 규약을 그대로 따르며, 자기 「계획 시작 SHA」를 따로 적는다. 새 계획서 착수 전제는 이 계획 W2 완료다(W2의 shim·모듈 경계 위에서 분해한다).

### 5.2 W0 — 안전망 (제품 코드 0건)

| 산출 | 대상 스멜 | 내용 |
|---|---|---|
| SSE `done` 페이로드 키 집합 스냅샷 | A1·A2·A3·A6 | 텍스트/파일 × 스트림/비스트림 × 정상/타임아웃/역질문 대표 경로 |
| 그래프 위상 스냅샷 | O3 | 플래그 매트릭스(1~4단 × 3단 옵션 4종)별 `graph.get_graph()` 노드·엣지 집합 |
| `decide_notification` 골든 특성화 | G1 | 플래그 조합 × 심각도(0~3) × 해소·유지보수·침묵 → (tier, reason) |
| 폼필 피벗 입력 덤프 | N1 | 단일/멀티의 `build_form_fill_pivot_sql` 입력 dict 골든 |
| fakeredis 키·값 스냅샷 | I1 | `schema:*` 전후 동일 |
| MCP 도구 목록 스냅샷 | M1·M2 | `list_tools()` name·inputSchema·description |
| APM 도구 출력 JSON 골든(대표) | M4 | `sort_keys` 덤프 |
| `_zone_label` 3벌 출력 특성화 | X4 | 레지스트리 전 db_id + 미등록 db_id |
| 노드명 정합 테스트 | A6 | `_STREAM_KNOWN_NODES` ↔ graph 등록 노드 |
| ~~읽기 전용 단언~~ | S1 | **163 W0으로 이관(v1.2)** — 같은 단계 1에서 163 W0이 「고정 SQL 통과표」로 만든다. 166 W0은 만들지 않는다 |
| **파손 범위 실측표** | 전 Wave | 이동 대상 심볼별 테스트 import·`monkeypatch` 지점 수 · `AlarmWorker` private 접근 테스트 수 · SimpleNamespace 설정 테스트 수와 그중 X1 치환으로 깨질 것(X1 · U-7) · `apm_query` patch 심볼 목록 재확인(O1 Facade 잔류 함수 확정 — 2026-10-14 실측: `_SESSION_FACTORY` 38파일 52곳 외 5심볼) · `settings_catalog`의 config 클래스 열거 결합 → **I4 착수 판정(U-10)** · *(X6 호출부 실측은 2026-10-14에 끝남 — `plans/163`)* |
| 노트 호출 순서 단언 | N7 | 텍스트 경로·문서 경로 각각 **현행** `_append_*` 호출 목록과 순서(U-8 — 문서 경로에 4종이 없는 현 상태를 그대로 고정) |

**진행 순서(단계 1 — 4트랙 병렬)**

| 트랙 | 담당 산출 | 비고 |
|---|---|---|
| 1-src-api | SSE `done` 스냅샷 · 노드명 정합 · 노트 호출 순서 · 그래프 위상 스냅샷 · `_zone_label` 특성화 | `tests/test_api/`·`tests/test_orchestration/` 신규 파일만 |
| 1-src-data | 폼필 피벗 입력 덤프 · fakeredis 키·값 스냅샷 | 신규 테스트 파일만 |
| 1-noise_gate | `decide_notification` 골든 특성화 | `noise_gate/tests/` 신규 |
| 1-gateway | MCP 도구 목록 스냅샷 · APM 도구 출력 골든 | `apm_gateway/tests/` 신규 — `../.venv/bin/python -m pytest` |
| team-lead | 파손 범위 실측표 · I4 착수 판정 · 계획 시작 SHA 기록 | 코드 읽기·grep만 |

- **선행 조건**: 단계 0(커밋 — §0.1). W0 team-lead가 세션 시작 SHA를 머리 「계획 시작 SHA」에 적는다.
- **성공 기준(단계 1 종료 게이트 — 163 W0 종료와 함께)**: 새 테스트 전부 통과(현행 동작을 고정) · 제품 코드 diff 0 · `regress.py --no-tests` 0 · 파손 범위 실측표를 이 계획서 부록(또는 §5.2 아래)에 기록 · I4 착수 여부 판정 기록.
- **회귀**: 자기 테스트만.

#### W0 실측 부록 (2026-10-14 · 기준 `08863d6` · 단계 1 — 166 W0)

**산출(새 테스트 10파일 + 스냅샷 9파일 · 제품 코드·기존 테스트 diff 0)** — 스냅샷 갱신은 `UPDATE_PLAN162_SNAPSHOT=1`일 때만(평시 채록 테스트 skip). §0.1 운영 규칙 4: 단계 2(163)만 갱신 가능, 단계 3 이후 갱신 금지.

| 산출 | 파일(`*plan166_w0*`) | 덮는 범위 |
|---|---|---|
| SSE `done`·응답 키 집합 | `tests/test_api/…_done_payload_keys.py` | 34케이스 — 텍스트/파일 × 스트림(정상·ainvoke 폴백·상한 행無/부분·기간 되묻기·존 후단·존 사전 2종·그래프 예외)/비스트림(같은 축 · HTTP 상태+키+non-null 키). 판정 재료(`active_db_ids_csv`·사다리 플래그)는 픽스처에 명시 |
| 노드명 정합 | `tests/test_api/…_stream_node_names.py` | 6구성 노드 합집합 ↔ `_STREAM_KNOWN_NODES`(**30개** — 계획서 38은 오기). `known − graph = ∅`(⊆ 성립) · `graph − known = {entity_locator, sequential_runner}` 고정 |
| 그래프 위상 | `tests/test_orchestration/…_graph_topology.py` | 53조합 — 1·2·4단 각 7(base·단독 5·all_on) + 3단 옵션 5종 전곱 32. 옵션: `enable_sql_approval`·`fault_diagnosis_enabled`·`tier3_plan_loop_enabled`·`sequential_fallback_tiers_enabled`·`cross_system_probe_enabled`. 1단은 deepagents 실조립(가용 판정만 고정) |
| 노트 호출 순서 | `tests/test_nodes/…_output_note_order.py` | 텍스트·xlsx·docx 3경로 실행 기록(깊이 0 호출만) — 문서 경로 4종 누락 현행 고정(U-8) |
| `_zone_label` 3벌 | `tests/test_orchestration/…_zone_label.py` | 레지스트리 7 db_id + 미등록 2 |
| 폼필 피벗 입력 | `tests/test_nodes/…_form_fill_pivot_inputs.py` | 4케이스(월 시리즈+Vendor/Model · 월 시리즈+GB 용량 · 월 없음 EAV 혼합 · DB2 b0 QueryTime) × 단일/멀티(멀티는 `_generate_sql` 전 구간) — 현행 4건 모두 단일=멀티 |
| Redis 키·값 | `tests/test_schema_cache/…_redis_keyspace.py` | Facade·우회 `_redis_cache`·`StructureStore` 쓰기 → 키 24(`schema:*` 19) 값·TTL · `invalidate(db)`·`invalidate_all()` 뒤 잔존 키 · governance on/off |
| `decide_notification` | `noise_gate/tests/…_decision_matrix.py` | 512케이스 = 신호 2(quiet·loud) × 플래그 조합(12 + 순서 검출 8) × 심각도 0~3 × 상태 4. (tier, reason) 18종 · 단계 15 중 12 도달(self_heal·collection_failed·cross_source는 기존 `decision_snapshot`이 덮음) |
| MCP 도구 목록 | `apm_gateway/tests/…_tool_list_snapshot.py` | 도구 **25종**(계획서 18 = `register_tools` 직접분 · manage 4·scope 2·fleet 1 별도) name·description·inputSchema 전문 · 단일/다중 소스 구성 바이트 동일 |
| APM 출력 골든 | `apm_gateway/tests/…_tool_output_golden.py` | 18케이스 — `FleetTools.ranking`·`_period_ranking`(fleet_tools.py) · `ManageTools._event_rules`(manage_tools.py) · `ApmTools._business_search`·`apm_status_stats` · 순수 `_period_stats`·`_sum_stats`·`_stats_delta`. 정책 파일(`apm_gateway/config/`) 의존 |

검증: 새 테스트 루트 48 passed·1 skipped(직렬·`-n 4`·`ACTIVE_DB_IDS` 운영형 덮어쓰기 모두 동일) · apm_gateway 21 passed·2 skipped · `regress.py --base 08863d6 --no-tests --files <19파일>` 종료 0(`범위: 정적 게이트만 — 테스트 미실행`) · verifier 변이 15종 전부 검출(공허 테스트 0) · 네트워크 시도 0 · 이웃 기존 테스트 7파일과 교차 실행 오염 0.

**파손 범위 실측표** (정적 grep — `tests/`+`noise_gate/tests/`. 「patch 문자열」= `patch("모듈.X")`, 「setattr」= 모듈 객체 `monkeypatch.setattr(mod, …)`, 「`_` import」= `from 모듈 import _x` 개수. 근사치 — 별칭·간접 참조는 빠질 수 있다)

| 스멜·모듈 | import 테스트 파일 | patch 문자열(파일) | setattr(파일) | `_` import |
|---|---:|---:|---:|---:|
| O4·O5 `orchestration/subagents` | 75 | 0 | 37 (10) | 29 |
| N2 `utils/query_gen_common` | 44 | 0 | 1 (1) | 9 |
| N3 `column_deriver`·`table_selection`·`intent_frame_builder`·`candidate_generator`·`candidate_selector`·`key_bridge`·`prompt_blocks` | 3·7·2·1·2·4·16 | 0·0·0·2·1·0·0 | 4·0·0·0·0·6·0 | 0·1·0·0·1·0·0 |
| I2 `nodes/field_mapper` | 8 | 4 (2) | 0 | 16 |
| N7 `nodes/output_generator` | 17 | 4 (3) | 1 (1) | 69 |
| A7 `api/routes/admin` | 14 | 4 (3) | 9 (6) | 0 |
| A8 `api/routes/alarm` | 19 | 0 | 0 | 19 |
| I4 `config` | 205 | 14 (10) — 전부 `load_config` | 11 (6) — 전부 `load_config` | 0 |
| O1 `orchestration/apm_query` | 47 | 0 | 76 (40) | 1 |
| A2 `api/routes/query` | 68 | 0 | 4 (3) | 94 |
| N1 `multi_db_executor`·`query_generator` | 38·34 | 0·0 | 32 (7)·0 | 39·54 |
| I1 `cache_manager`·`redis_cache` | 25·19 | 31 (24)·4 (2) | 2 (2)·0 | 1·0 |
| O3 `graph` | 35 | 5 (3) | 47 (14) | 1 |
| G1 `notification_policy` | 65 | 0 | 0 | 0 |
| G2 `alarm_worker` | 32 | 0 | 13 (5) | 0 |

- **`AlarmWorker` private 접근(G2)**: 테스트 32파일 · 418지점 · 서로 다른 `_` 멤버 52종(상위 `_is_duplicate_fingerprint` 51 · `_detect_correlated_storm` 27 · `_decision_store` 26 · `_process` 25 · `_episodes` 21 · `_detect_inhibition` 18 · `_graph` 17). W5 7c의 위임 shim 대상 목록.
- **`apm_query` patch 심볼(O1 · U-3 재확인)**: `_SESSION_FACTORY` 52지점·37파일(언급 38파일) · `link_business_names` 11·8 · `get_registry` 6·4 · `link_hostnames` 5·3 · `render_view_rows` 2·1. `poll_job` 3건은 `aq.jobs`(하위 모듈 객체)에 patch하므로 Facade 잔류 조건이 아니다 — 계획서 「5심볼」 중 실효 4심볼.
- **SimpleNamespace 설정 테스트(X1 · U-7)**: SimpleNamespace 사용 283파일 · 설정 대용 206파일 · 그중 X1 대상 모듈 import 139파일. 설정 필드 기본값과 다른 상수 `getattr` 폴백은 **63지점·34필드·29파일**(단일 정의 필드·`None` 폴백 제외 AST 실측 — 계획서 「약 80」은 다른 셈법) · 상위 `zone_group_exclusive` 12 · `investigation_service_url` 5 · `answer_reserve_sec` 4. 치환으로 깨질 **후보 상한 142파일**(SimpleNamespace 사용 ∧ 불일치 폴백 모듈 import ∧ 해당 필드명 미기재 — 정적 상한, 실제 파손 수는 W1 3b 치환 후 테스트로 확정).
- 측정 스크립트: 세션 scratchpad `breakage.py`(재현용 — 저장소 미편입).

**I4 착수 판정(U-10) — 「착수」(W2 4c 포함) · 단 경로 결합 4곳 동반**
- 근거 ① `settings_catalog.field_index()`는 `AppConfig.model_fields`와 그 annotation 클래스만 순회한다 — `src.config` 모듈 열거·`__module__` 비교·파일 경로 의존 없음. `.env.example` 경로는 `settings_catalog.py`의 `__file__` 기준이라 무관. `env_file`은 cwd 상대(`.env`)라 패키지 위치와 무관.
- 근거 ② 실험(스크래치 worktree · 원복): `src/config.py` → `src/config/_core.py` + `__init__.py` 재노출 33심볼로 바꿔 `field_index()` 409키 sha256 앞 16자 `56ecee3f0187d317`이 기준선과 **동일** · `catalog_diff.py --ci` 총 차이 0 · `import src.config, src.graph, src.api.server` 성공 · `test_settings_catalog*`·`test_synonym_semantic`·`test_config_env_reload` 통과(.env 부재 2건은 worktree 산물).
- **동반 조건(4c 범위에 넣는다)**: 경로 문자열 `src/config.py`에 묶인 4곳 — `scripts/regress.py:53` `WAVE_REGRESS_FILES`(패키지화 후 `src/config/` 접두로 바꾸지 않으면 `[웨이브 회귀 필요]`가 안 뜬다) · `scripts/bench/axes.py:269` `NON_CONSUMER_MODULES` · `scripts/rag_hyde_repeat.py:103` 루트 탐지 · **`tests/test_composite/test_sufficiency.py:149`가 `Path("src/config.py").read_text()`로 실패**(실험에서 재현). 마지막은 테스트 파일 수정 1건이 불가피하다 — D-337 ②의 「테스트 수정 0」은 patch seam 기준이고 이 건은 경로 리터럴이라, 4c 착수 시 이탈로 명시하고 수정한다(되돌리는 비용: 1줄). `load_config`는 테스트 25지점이 `src.config.load_config`로 patch하므로 `__init__.py`(Facade)에 남긴다.

**발견한 이상 — 고치지 않음(현행 고정 · 후속 판단용)**
1. `entity_locator`·`sequential_runner`가 그래프에 등록되지만 `_STREAM_KNOWN_NODES`에 없어 SSE 진행 이벤트가 나가지 않는다(A6 정합 테스트가 차집합으로 고정).
2. 2·3단 플래그 동시 on(로컬 `.env` 구성)이면 2단 빌드에 3단 노드 6개(+장애 진단 on 시 `fault_diagnosis`)가 **엣지 없이** 등록되고, 1단 빌드에는 단일 DB 경로 노드 7개가 도달 불가 상태로 등록된다 — CLAUDE.md 「하위 단은 노드조차 등록되지 않는다」 서술과 불일치(동작 영향은 없음 · 문서 정정 또는 O3에서 확인).
3. `_zone_label` 3벌이 `polestar`·`cloud_portal`·`itsm`·`itam`에서 다르다(`host_sweep`만 db_id 그대로 · `realtime_usage`·`query._zone_display_label`은 9입력 전부 동일). **U-1에 따라 W1 X4에서 `_zone_label`은 통합하지 않는다.**
4. SSE: 스트림 done의 `awaiting_approval`은 astream 경로에만 있고 ainvoke 폴백엔 없다 · 텍스트 스트림 done은 `clarification`·`source_switch`를 null이어도 싣고 파일 스트림은 아니다 · 파일 두 경로는 그래프 출력 `zone_clarification`(후단 게이트)을 보지 않아 `completed`로 나간다 · 처리 상한 부분 결과 done은 키 8개(정상 done의 `db_scope`·`executed_sql`·`turn_count` 등 빠짐) · 파일 경로 사전 게이트 응답 `thread_id: null`(텍스트는 query_id로 채움).
5. Redis: `asset_drafts`·`ddl_comments`(D-294 관리자 자산)가 `ADMIN_ASSET_SUFFIXES`에 없어 `invalidate_all()`에서 지워진다 · `invalidate(db_id)`가 `descriptions`는 지우고 `knowledge_seeded`는 남겨 지식 설명이 다시 채워지지 않는다 · `StructureStore._key`와 `RedisSchemaCache._key`가 `structure_meta` 키를 공유(I1 그대로).
6. 노이즈 게이트(설계 의도로 보임): sev0·비해소는 보수적 PAGE · 비알람 필터 on이면 sev3 해소도 SUPPRESS(§17.4 주석 의도) · 플래핑 억제가 실효 sev0에도 걸린다.
7. 폼필 분류: 평균/최대 수식어 없는 「CPU 사용률」은 `classify_metric_field`가 지표로 분류하지 않는다(픽스처 작성 중 관찰 · 참고).
8. `import src.nodes.output_generator as og`는 `src.nodes`가 같은 이름의 함수를 재노출해 모듈이 아니라 함수를 받는다 — W2 N7 이동 시 patch 지점 주의(새 테스트는 `importlib.import_module` 사용).

### 5.3 W1 — SSOT 기계 치환

| 스멜 | 대상 파일(주) | 성공 기준 |
|---|---|---|
| X1 | `multi_db_executor.py` · `subagents.py` · `query.py` · `scope.py` · `semantic_router.py` · `graph.py` · `prompt_blocks.py` · `query_validator.py` · `replanner.py` · `agent_orchestrator.py` · `apm_jobs.py` · `doc_qa/service.py` · `investigation_metrics.py` · `fault_diagnosis.py` · `alarm_worker.py` 외 | 필드 기본값과 다른 폴백 0건(D-331 ② 린트 `--ci` 0) · SimpleNamespace 설정으로 깨지는 테스트는 실 `AppConfig`(검증 대상 필드 명시)로 교정(U-7) · 해당 모듈 테스트 통과 |
| X3 | `host_inspect.py` · `portal_help.py` · `apm_query.py` · `doc_query.py` · `result_aggregator.py` · `subagents.py` · `replanner.py` + 신규 키 상수 모듈 | 사본 0 · 상수값 단언 |
| X4 | `schema_probe.py` · `db_registration_service.py` · `subagents.py` · `intent_planner.py` · `semantic_router.py` · `admin_jobs.py` · `structure_store.py` · `db_structure_service.py` · `apm_jobs.py` · (`_zone_label` 3벌은 W0 특성화가 **전 db_id 동일 출력**일 때만 — 하나라도 다르면 현행 유지 · U-1 확정) | 정본 1곳 · 사본 import 대체 |
| **D-331 ①** | `scripts/arch_check.py:231-243`(application→application 경고를 「노드 패키지 간」 — `src.nodes.*`↔`src.nodes.*`·`noise_gate.application.nodes.*`↔같은 패키지 — 으로 한정) · `tests/test_orchestration/test_arch.py`에 규칙 단언 추가 | 경고 117 → 43(2026-10-14 실측: 노드↔노드 38 + prompts 직접 참조 5) · error 0 유지 · 경고 문구에서 「그래프 라우팅으로 해결」 대신 「순수 헬퍼는 비노드 application 모듈로」 |
| **D-331 ②** | 신규 `scripts/config_fallback_check.py`(AST — `getattr(<설정 객체>, "필드", 기본)`의 기본값을 `src/config.py` 필드 기본값과 대조) · `regress.py` 정적 게이트에 편입 | W1 시작 시 보고 모드로 현재 수(약 80)를 기록 → X1 치환 뒤 0 → `--ci`(위반 시 종료 코드 1)로 전환. 판정 불가(동적 이름·비설정 객체)는 건너뛰고 수만 보고 |
| A6 | 신규 `src/api/sse_events.py` · `query.py` · `thread_history.py` · `stream_failure.py` | SSE type 리터럴 0(프런트 제외) · W0 done 스냅샷 불변 |
| G4·G5 | `alarm_context_enricher.py` · `server_identity.py` · 신규 `noise_gate/domain/keys.py` + 8파일 | 키 단언 · `_unavailable` 별칭 1사이클(D-337 ③) · (arch 경고는 D-331 ①로 먼저 소거) |
| N1(b) | `query_generator.py` · `multi_db_executor.py` | 복붙 래퍼 7쌍 → import · `prompt_render_diff.py --ci` 0 · 렌더 매트릭스 12키 무갱신 |
| O3(주석) | `graph.py:523-531` | D-251 문구 정정(코드 무변경) |
| M3 | `apm_gateway/application/{tools,scope_tools,fleet_tools,manage_tools}.py` · `poller.py` | 관용구 32 → 헬퍼 1 · 계약 위반 전파 테스트 신규 |
| **A5**(v1.2 배정) | `src/api/dependencies.py` · `admin_auth.py` · `noise_dashboard.py` · `alarm.py` · `query.py` · `scope.py` | `is_admin(user)` 술어 1개 · 토큰 검증 함수 1개(`expected_type`) — 163 S2가 넣은 사용자 `type` 검증을 그대로 옮긴다 · RBAC 테스트 4파일 통과 |
| **N4**(v1.2 배정) | `multi_db_executor.py` · `query_generator.py` · `result_organizer.py` · `query_validator.py` | `get_adapter_for(db_id, app_config)` 1개로 11곳 대체 · 직접 import 13건(D-089 허용)은 손대지 않음 · `test_db_adapters.py`·`overfit_check` |
| **X5**(v1.2 배정 · `pii_filter` 제외) | `redis_cache.py` · `fabrix_client.py`(163 X6 뒤) · `alarm_worker.py` · `server_identity.py` | 침묵 핸들러에 `warning`+`exc_info` 한 줄 또는 구체 예외로 축소 · 반환값 불변 · caplog 단언 1건씩. `pii_filter` 5곳은 X2와 같은 함수라 W3(5c) |
| **M6**(v1.2 배정) | `spec/SPEC-apm-gateway.md` + 신규 계약 스냅샷 테스트(`apm_gateway/tests`·`tests/`) | 제품 코드 0 · 오류 코드·엔벨로프 표 + 양쪽 스냅샷 대조 · 등록 도구 이름 ↔ `JOB_TOOLS` 정합 |

**진행 순서(단계 3)**

| 하위 단계 | 담당 | 내용 | 끝 조건 |
|---|---|---|---|
| **3a** 게이트 | team-lead 단독(공유 파일) | D-331 ① `arch_check` 규칙 한정 · D-331 ② `config_fallback_check.py` 보고 모드 · `regress.py` 편입 | 경고 43 · 폴백 기준 수(약 80) 기록 |
| **3b-src** | implementer 1 | **X1 먼저**(가장 넓음 · SimpleNamespace 테스트 교정 포함) → X3 → X4 → N4 → A5 → A6 → N1(b) → X5(src) → O3 주석 — 스멜 하나에 커밋 하나 | 폴백 린트 src 분 0 |
| **3b-noise_gate** | implementer 2 | X1(noise_gate 분) → G4 → G5 → X5(noise_gate 분) | 폴백 린트 noise_gate 분 0 |
| **3b-gateway** | implementer 3 | M3 → M6 | apm_gateway 테스트 통과 |
| **3c** 전환 | team-lead | 폴백 린트를 `--ci`로 전환 · Wave 끝 판독 | 단계 3 종료 게이트 |

- **행동 불변 검증**: W0 스냅샷 + 모듈 테스트 + `prompt_render_diff.py --ci` + `arch_check --ci`(D-331 ① 적용 후 43에서 비증가) + `overfit_check --ci` + `config_fallback_check`(X1 끝에 0).
- **회귀**: Wave 중간 규칙(§5.0-6). `src/config.py`를 건드리지 않으므로 `[웨이브 회귀 필요]`는 X1 치환이 공개 시그니처를 바꿀 때만. `scripts/arch_check.py`·`regress.py`는 공유 파일이라 팀 리드가 통합한다.
- **선행 조건**: 단계 2 종료(163 완료 — W0 스냅샷·실측표는 단계 1에서 끝남). 게이트 변경(D-331)을 X1 치환보다 먼저 넣는다 — 경고 기준선 43과 린트 기준 수를 먼저 고정해야 치환 효과를 잴 수 있다.
- **종료 게이트**: 위 성공 기준 전부 · 폴백 린트 `--ci` 0 · arch 경고 43 이하 · W0 스냅샷 무갱신.

#### W1 결과 (2026-10-15 · 단계 3 · 세션 시작 `5f7f5c39` · 커밋 `73851bef`)

**게이트(3a·3c)**: D-331 ① `arch_check` 경고 117 → **43**(error 0 · W1 끝에도 43 — N1(b) 1차 시도가 노드 간 import로 44를 냈다가 정본을 `prompt_blocks.first_eav_pattern`으로 바꿔 43 유지) · D-331 ② `scripts/config_fallback_check.py` 신설 — 시작 **62지점·34필드·28파일**(W0 「63·29」는 `noise_gate/scripts` 1지점 포함 셈) → **0**(허용 목록 2 · 건너뜀: None 폴백 16 · 리터럴 아닌 폴백 8 · 동명 필드 11) · `regress.py` `GATE_SCRIPTS`에 `--ci` 편입 · `prompt_render_diff --ci` 차이 0 · overfit 통과(기준선 무수정) · ruff·mypy 이번 diff 줄 0.

| 스멜 | 처리 | 보류·이탈(근거) |
|---|---|---|
| X1 | src 55 + noise_gate 5 = **60지점** 직접 접근 · 운영에서 부모가 None일 수 있는 5곳은 명시 분기로 현행 값 보존(multi_db_executor 3 · query_validator 1 · sre_agent_client 1) · 테스트 약 45파일은 대역에 **치환 전 폴백 값**을 명시(단언 변경 0) | 2곳 허용 목록(`prompt_blocks` `prompt_token_budget` · `template_assembler` `template_assembly` — 대역 방어가 docstring 계약이고 기존 테스트가 「필드 부재 → off」를 단언 · 바꾸면 행동 변경) |
| X3 | 신규 `src/utils/task_result_keys.py`(`DEGRADED_KEY`·`DISPLAY_CUT_KEY`) · 사본 5벌(계획 4 + `user_error`) import · 리터럴 3곳 상수화 | 이탈: 정본을 `src/domain/`이 아니라 `src/utils/` — 소비처 `user_error`(utils)가 domain을 import하면 arch error |
| X4 | `capability_ownership_on` → `src/routing/capability_ownership.py` · `engine_key` → `src/utils/sql_dialect.py` · `now_iso` 4벌 → 신규 `src/utils/clock.py` | `semantic_router._ownership_enabled`(load_config·예외 삼킴 변형 · 테스트 3곳 patch) 유지 · `_zone_label` 통합 안 함(U-1) |
| N4 | `db_adapters.get_adapter_for(db_id, app_config)` 4곳 | getattr+callable 변형 1곳(대역 허용 해석이 다름) 유지 · 나머지는 반복 조합이 아님 |
| N1(b) | `_get_eav_pattern` 래퍼 2벌 → `first_eav_pattern` 직접(별칭 2) | 6쌍 보류 — 본문·시그니처 차이 4 · patch seam 1(`_render_samples_secure`) · `_SCHEMA_EXAMPLE_TABLE`(+ 형제 리터럴 4)은 옮기면 arch 경고 또는 overfit 기준선 경로 이동이 필요 → W3 5b에서 판단 |
| A5 | `dependencies.is_admin(user)` 6곳 · `decode_typed_token(token, secret, expected_type)`로 사용자·운영자 토큰 검증 공통화(서명·만료·type · 상태·문구·순서 불변 · 163 S2 type 검증 그대로) | `sub` 기준 차이(사용자 `not sub` / 운영자 `is None`)와 break-glass(무음 None)는 공통화 안 함 · interface 밖 admin 판정(routing·apm_jobs·cache_management)은 계층상 술어 import 불가 |
| A6 | 신규 `src/api/sse_events.py`(SSE_* 8) · query.py 31곳 · thread_history 2곳 | 팩토리 미도입(기존 `_sse_event` · 지점별 키 순서 상이 → 직렬화 바이트 보존) |
| X5 | redis_cache 7 · fabrix_client 1 · server_identity 2에 경고 로그(반환값·흐름 불변) | alarm_worker `_build_*`는 이미 로그 있음(침묵 0) · verifier Minor 반영: `health_check`(정상 재연결 경로)와 `_governance_enabled`(검증 오류 본문에 원값 가능)는 스택 없이 예외 종류만 |
| O3(주석) | `graph.py` 사다리 주석 D-251로 정정(코드 무변경) | — |
| G4 | `is_apm_source`·`is_apm_db_id` → `noise_gate/domain/alarm.py`(server_identity 재노출) · `_unavailable` → `unavailable_noise_ctx` + 별칭 | — |
| G5 | **해당 없음으로 닫음** | 계획의 「8파일」 중 5파일은 타입 주석·주석의 `alarm:` — 실제 키는 모두 제품 코드 1곳에서만 생성(사본은 테스트 리터럴) · `APP_IMPACT_LEVELS`는 이미 정본 import(Known Mistakes 2026-10-15) |
| M3 | `limit_notice(e, subject, where, tail, *, detail)` 헬퍼 · 사본 32 중 28 치환(문구 바이트 동일 · 재발생은 같은 예외 객체) | fleet_tools 3(고지 대신 누적) · poller 1(SPEC §5 — 재발생 대신 폴링 중지) 모양이 달라 유지 · 규칙 위반 사본 0 |
| M6 | `spec/SPEC-apm-gateway.md` §3.6 · 계약 스냅샷 `apm_gateway/tests/snapshots/plan166_w1/apm_contract.json` · 정본 대조(gateway 4건)·소비측 대조(루트 14건 · import 0) · `JOB_TOOLS` ↔ 등록 도구 정합 | 현행 불일치 0 |

- **남긴 shim·별칭(다음 계획 제거 대상 · D-337 ①③)**: `host_inspect`·`apm_query`·`portal_help`·`doc_query`의 `DEGRADED_KEY`(+ `apm_query.DISPLAY_CUT_KEY`) 재노출 · `intent_planner._capability_ownership_on` · `schema_probe.engine_key` · 4모듈 `_now_iso` import 별칭 · `query_generator`·`multi_db_executor`의 `_get_eav_pattern` · `server_identity.is_apm_source`·`is_apm_db_id` · `polestar_noise_context._unavailable`.
- **문구 후보(기록만 — 행동 변경)**: `apm_query` 첫 홉 고지 「(api_error (요청 rid))」 이중 괄호 정리. 오류 문구 함수 3종(`_call_failure_text`·`_shown_call_error`·`_public_call_error`)은 규칙 핵심이 이미 `_shown_call_error` 한 곳이고 나머지는 출력 형식이 다른 포맷 래퍼라 합치지 않았다.
- **검증**: implementer 자기 테스트(X1 src 332파일 8363 passed · noise 90파일 2024 · src 2묶음 258파일 7129 + 3021 · apm_gateway 패키지 1610 — 실패는 원래 실패 2건과 팀 리드가 교정한 bench 대역 3건뿐) · verifier Critical 0 · Major 0 · Minor 5(위 X5 2건 반영 · 나머지 기록) · 변이 7/7 검출 · W0 특성화 19파일·스냅샷 diff 0 · `regress.py --base 5f7f5c39 --no-tests --files <121>` 종료 0(`범위: 정적 게이트만 — 테스트 미실행`). 모듈 단위 회귀는 `[웨이브 회귀 필요]` 미발생 → **W2 끝 1회에 W1 121파일을 포함**(D-303 부기).
- **잔여(후속)**: 폴백 린트 오탐 대비 — 비설정 객체가 고유 설정 필드명을 쓰면 `ALLOWED`로 처리, 건너뛴 19건(동명·비리터럴)은 판정 밖 · `decode_typed_token`의 미지 `expected_type`은 KeyError(호출 2곳 고정 리터럴) · 보안 관찰: 운영자 토큰 `sub == ""` 판정이 `verify_admin_token`(`is None`)과 break-glass(`not sub`)에서 갈림(발급 경로상 미발생 · 고치지 않음 — D-337 ⑥).
- → plans/182 #S2(운영자 토큰 빈 `sub` 판정) · #M1(위 「문구 후보」 첫 홉 이중 괄호)
- **W1 바꾼 소스 64**: `scripts/{arch_check,config_fallback_check,regress}.py` · `spec/SPEC-apm-gateway.md` · `apm_gateway/apm_gateway/application/{tools,scope_tools,manage_tools}.py` · `noise_gate/application/{alarm_worker,server_identity}.py` · `noise_gate/application/nodes/{agentic_enricher,alarm_context_enricher,investigation_trigger}.py` · `noise_gate/domain/alarm.py` · `noise_gate/infrastructure/{apm_noise_context,polestar_noise_context,sre_agent_client}.py` · `src/api/{dependencies,sse_events,thread_history}.py` · `src/api/routes/{admin_auth,alarm,noise_dashboard,query,scope}.py` · `src/clients/fabrix_client.py` · `src/db_adapters/__init__.py` · `src/doc_qa/service.py` · `src/graph.py` · `src/nodes/{fault_diagnosis,input_parser,multi_db_executor,output_generator,query_generator,query_validator,result_organizer}.py` · `src/observability/{investigation_metrics,ladder}.py` · `src/orchestration/{agent_orchestrator,apm_jobs,apm_query,deepagents_tools,doc_query,host_inspect,intent_planner,investigation_audit,portal_help,replanner,result_aggregator,sequential_runner,subagents,sufficiency}.py` · `src/routing/{capability_ownership,semantic_router}.py` · `src/schema_cache/{admin_jobs,db_registration_service,db_structure_service,form_memory,redis_cache,schema_probe,structure_store}.py` · `src/utils/{clock,sql_dialect,task_result_keys,user_error}.py`. 테스트 57(신규 `*plan166_w1*` 9 · `test_config_fallback_check.py` · `test_arch.py` 규칙 단언 · 대역 필드 명시 약 45).
- **매뉴얼 미반영**: 사용자 접점 변경 없음(D-255 대상 아님 — 응답·화면·SSE 바이트 불변).

### 5.4 W2 — Move-only 모듈 분할

| 스멜 | 이동 | 성공 기준 |
|---|---|---|
| O4·O5 | `subagents.py` → `agent_spec.py`(leaf) · `db_targeting.py` · `isolated_input.py` · 지연 import 3곳 상단화 | orchestration SCC 해소(재측정) · `_` 접두 공유 함수 공개명 + 옛 이름 별칭(U-2 확정 · D-337 ③) |
| N2 | `query_gen_common.py` → 7개 모듈 + shim | 소비 89파일 무수정 · 기준선 경로 이동 델타만 |
| N3 | `column_deriver`·`table_selection`·`intent_frame_builder`·`candidate_*`·`key_bridge`·`prompt_blocks` → 비노드 application 하위 | arch 경고 43 → 17 이하(이동 대상 7모듈 26건 소거 · D-331 ①) · 이동 대상 소비처 테스트 |
| I2·I3 | `field_mapper.py` 5분할 · `AdminServiceBase` → `admin_base.py` · asset_generation 보조 타입 분리 | 폼필 골든 불변 |
| N7(이동) | 노트 함수 → `output_notes.py`(체인 선언화는 W4) — 텍스트·문서 경로 구성·순서는 현행 그대로(U-8) | 응답 스냅샷 불변 · W0 노트 순서 단언 불변 |
| M5 | `mcp_server` 응답 헬퍼 1모듈 · 빌더/등록 분리(EXCLUDE 대칭) | mcp_server 테스트 · overfit 0 |
| A7 | `admin.py` 라우터 분할 · `.env` I/O → `src/infrastructure/env_file_store.py` · 감사 래퍼 1개 | 라우트 경로 불변(OpenAPI 경로 목록 diff 0) |
| I4 | `config.py` → `src/config/` 패키지 + 재노출(**U-10 확정: W0 판정이 「착수」일 때만** — 아니면 이 Wave에서 빼고 §8 보류로 옮김) | `catalog_diff.py --ci` 0 · `field_index()` 결과 동일 · `[웨이브 회귀 필요]` → 모듈 단위 회귀 |
| **A8**(v1.2 배정) | `alarm.py` `analyze_alarm_test` payload 조립 → `noise_gate/application` 함수(src→noise_gate 방향 유지 · D-139) · 오류 반환 블록 2곳 → 헬퍼 1 · Pydantic 모델 약 20개 → 스키마 모듈 | `test_alarm_*` · `noise_gate/tests` · 라우트 경로 불변 |

**진행 순서(단계 4)**

| 하위 단계 | 트랙(worktree 병렬) | 비고 |
|---|---|---|
| **4a** | O4·O5(`subagents.py`) / N2(`query_gen_common.py`) / N3(노드 헬퍼 이동) / I2·I3(`field_mapper`·관리자 서비스) / N7 이동(`output_generator.py`) / M5(`mcp_server`) / A7(`admin.py`) / A8(`alarm.py`) | 서로 다른 파일 — 트랙마다 implementer 1명 · 동시 3~4트랙 권고. 각 트랙은 자기 overfit 기준선 델타·EXCLUDE 대칭 **목록만** 보고하고 공유 파일 수정은 팀 리드가 한다 |
| **4b** | team-lead — `overfit_baseline.json`·`arch_check.py` 통합 · import 스모크 · arch 경고 17 이하 확인 | 4a 전 트랙 병합 뒤 |
| **4c** | I4(U-10 판정이 「착수」일 때만 · 단독) | `src/config.py`라 `[웨이브 회귀 필요]` — 4a·4b와 섞지 않는다 |
| **4d** | team-lead — **166 계획 끝 회귀 1회**(아래) · §0.1 상태 갱신 · 파일명 태그 정리 | 단계 4 종료 |

- **행동 불변 검증**: import 스모크(`python -c "import src.graph; import src.api.server"`) · 모듈 테스트 · `arch_check`·`overfit_check` · W0 스냅샷.
- **병렬**: 4a 트랙은 파일이 겹치지 않아 `isolation: worktree` 병렬 가능(단계 0 커밋이 있어야 worktree에서 계획서·스킬이 보인다). 공유 파일(`scripts/overfit_baseline.json`·`arch_check.py`)은 팀 리드가 통합.
- **선행 조건**: 단계 3 종료(게이트 기준선 43 고정 · 폴백 린트 `--ci` 전환). `plans/163`은 단계 2에서 끝났으므로 M5는 163이 정한 읽기 전용 검사 위치를 옮기지 않고 둔다.
- **계획 끝 회귀(이 계획의 마지막 Wave)**: `python scripts/regress.py --base <계획 시작 SHA> --files <W0~W2 회귀 대상 파일 누적…>` 1회. 범위 줄과 `[전체 회귀 권고]` 블록을 보고에 그대로 옮긴다. W2 완료로 이 계획서는 「로드맵」 상태가 되고 W3~W5 별도 계획서가 착수 대상이 된다.

#### W2 결과 (2026-10-15 · 단계 4 · 세션 시작 `73851bef` · 커밋 `3a0a1101`)

**게이트**: arch 경고 **43 → 17**(error 0 — N3 이동 26건 소거 · 남은 17은 노드 간 12 + prompts 직접 참조 5) · overfit 신규 유입 0(**기준선 JSON 변경 0** — 리터럴 든 코드가 새 경로로 가는 대신 `PUBLIC_LAYER_DIRS`·`EXCLUDE_DIRS` 대칭으로 처리) · config_fallback 위반 0(검사 파일 384→415 · 건너뜀 수 동일) · `prompt_render_diff --ci` 0 · `catalog_diff --ci` 0 · import 스모크(`src.graph`·`src.api.server`) · 순환 import 0(이동·소비 모듈 60 + mcp 4를 각각 새 인터프리터 첫 import) · OpenAPI(admin·alarm·`create_app` 143 라우트) 경로·메서드·의존성·순서 기준선 동일 · mcp `list_tools()` JSON diff 0 · W0 스냅샷·`test_plan166_w0_*` diff 0.

**공유 파일(팀 리드 통합)**: `scripts/arch_check.py` `MODULE_LAYER_MAP`에 `"src.pipeline": "application"` · `scripts/overfit_check.py` `PUBLIC_LAYER_DIRS`에 `src/pipeline` · `EXCLUDE_DIRS`에 `mcp_server/mcp_server/polestar_helpers.py`(polestar_tools 대칭) · `scripts/config_fallback_check.py` `ALLOWED`에 `src/pipeline/prompt_blocks.py` 키 추가(옛 키는 합성 경로 테스트가 써서 유지) + `src/config/` 패키지 대응.

| 스멜 | 이동 | 잔류·이탈(근거) |
|---|---|---|
| O4·O5 | `subagents.py` 2341→1609줄 → `agent_spec.py`(leaf · `SubAgentSpec`) · `db_targeting.py`(존 역질문·위치 신호·턴 힌트 고정·승계·출처 가드 · 최상위 정의 23) · `isolated_input.py`(순수 조각 7) · `replanner.py` 지연 import 1곳 상단화 | patch seam 21심볼과 그 호출자 폐포 잔류(`classify_dbs`·`_zone_clarification_before_classify`·`_run_single_db_pipeline`·`make_isolated_input`·`extract_identity_rows` 등) · O5 `intent_planner`·`deep_agent` 지연 import 2곳은 seam 때문에 유지 · SCC(지연 import 포함) 7→2(`apm_jobs`↔`apm_query`) · top-level SCC 0 |
| N2 | `query_gen_common.py` 2287→1299줄 → `src/utils/{period_resolution,limit_policy,sql_rewrite_eav,llm_sql_extract,prior_turn_succession}.py` · 81 이름 재노출 · 소비처 무수정 | **7종 중 5종** — `text_terms`·`zone_clarification`은 `test_registry_config::test_no_location_term_tuple_in_src`(위치 표면어 리터럴을 qgc·`location_hints`에서만 허용) 때문에 미이동 · `date` patch seam 함수 잔류 · 프롬프트 블록·폴스타 규약은 utils→바깥 계층 arch error라 보류 |
| N3 | 노드 헬퍼 7모듈(`column_deriver`·`table_selection`·`intent_frame_builder`·`candidate_generator`·`candidate_selector`·`key_bridge`·`prompt_blocks`) → 새 패키지 **`src/pipeline/`**(application) · 옛 경로는 `sys.modules` 별칭 shim(옛 경로 patch가 새 모듈에 닿음) | Facade 잔류 없음 · 이동 코드 lint 부채 런타임 중립 정리(ruff 60·mypy 47→0) · 테스트 1줄(`test_plan67_phase0_core.py` `_SITES` 경로 리터럴) |
| N7 | `output_generator.py` 2931→2223줄 → `src/pipeline/output_notes.py`(노트 35 정의) · 호출 나열·순서는 og 유지(U-8) | `_append_zone_coverage_notes`(표 렌더 보조 의존)·`_append_turn_notices`·`collect_disclosures`(순환)·`_is_zoned_db`·`scope_partial_text`(seam)·`_prepend_alarm_headline` 잔류 · 공개명 개명 안 함(W0 노트 순서 테스트가 `co_name` 단언) |
| I2 | `field_mapper.py` 2450→791줄 → `src/document/{mapping_steps,synonym_matching,redis_registration,llm_mapping,mapping_feedback}.py` | 모듈명 이탈: 계획 `synonym_discovery`→`synonym_matching`(내용이 매칭) · `md_diff`→`mapping_feedback` · `perform_3step_mapping`·`_apply_llm_synonym_discovery`·`_apply_llm_mapping_with_synonyms` 잔류(Facade 전역 프롬프트·patch seam) |
| I3 | `AdminServiceBase`+헬퍼·`resolve_table` → `src/schema_cache/admin_base.py` · asset_generation 보조 → `asset_generation_assembly.py`·`asset_generation_llm.py` | 상한 상수를 읽는 후보 함수 3종 잔류(테스트가 원 모듈에서 상한 patch) |
| M5 | `mcp_server/mcp_server/tool_response.py`(`now_iso`·`err`) · `polestar_helpers.py`(순수 헬퍼 26) | **빌더 8종 미이동**(계획 이탈) — `test_readonly_fixed_sql::test_builder_cases_cover_every_public_builder`가 `__module__ == polestar_tools`인 빌더만 센다(163 누락 방지 가드) · `_METRIC_KIND_MAP`(본체 테스트가 AST로 파싱) · `_ok`·`_audit` 변형(필드·문구 상이) 잔류 · `DBPoolManager.execute`·sql_guard·readonly 경로 diff 0 |
| A7 | `admin.py` 2078→1510줄 → `admin_settings_reload.py`·`admin_audit_logs.py`·`admin_drm.py`(하위 라우터 · include 순서 = 원 위치) · `.env` I/O 본문 → `src/infrastructure/env_file_store.py`(경로 인자 주입 · `admin._ENV_FILE` 상수 1곳에서만) | 감사 래퍼 3벌 **미통합**(이벤트 종류·None 필드 처리가 서로 달라 이동이 아님 · 공통 본체는 이미 `log_admin_event`) · `.env` 래퍼 4개·`_ENV_FILE`·`_audit_admin_action` 호출 라우트(사용자 관리 6 등) 잔류(seam) |
| A8 | Pydantic 모델 20 → `src/api/routes/alarm_schemas.py` · payload 조립 → `noise_gate/application/alarm_test_payload.py`(`stats_to_dict`·`process_to_dict`·`identity_dict`·`build_ui_push_payload`) · 오류 반환 블록 → `_analysis_failure_response` 헬퍼 | 헬퍼 적용을 `analyze_alarm_raw`까지 4곳으로 확장(같은 블록 · 응답 JSON·push payload 기준선 차분 동일) · `noise_gate.prompts` 직접 참조 경고 그대로(17 유지) |
| I4 | `src/config.py` 삭제 → `src/config/{__init__(AppConfig·load_config·logger),llm,data_access,text2sql,security,alarm,services}.py` · 클래스 27·상수 3 재노출 · 경로 결합 동반 5곳: `scripts/config_fallback_check.py`(`__module__` 필터·스캔 제외 — 안 고치면 린트 무력화) · `scripts/regress.py` · `scripts/bench/axes.py` · `scripts/bench/optimize.py` · `scripts/rag_hyde_repeat.py` | `catalog_diff` 0 · `field_index()` 420키 동일(raw sha는 `group_cls` repr 모듈 경로만 다름 · 정규화 `3f2f36cbc193e65e`) · 설정 28클래스 필드·기본값·env_prefix·validator·JSON 스키마 기준선 동일 · `load_config`·`AppConfig`는 `__init__` 잔류(seam) · `RouterConfig.min_confidence`는 `Optional[float]` 유지(`# noqa: UP045` — pydantic 어노테이션 객체 보존) |

- **남긴 shim·별칭(다음 계획 제거 대상 · D-337 ①③)**: `src/nodes/` 7모듈 `sys.modules` 별칭 shim · `subagents`의 이동 이름 재노출 + `_normalize_targets`·`_request_query_text`·`_scope_time_resolution`·`_extract_identity_rows`·`_make_isolated_input`·`_pack_pipeline_result` · `query_gen_common` 81 재노출 + 새 모듈의 `_unit_normalized_expr` 등 9 별칭 · `output_generator` 35 재노출 · `field_mapper` 옛 이름 재노출 · `db_structure_service` 11 · `asset_generation_service` 52 재노출(+ 새 모듈 `_` 별칭 13) · `polestar_tools` 26 + `_err`·`_now_iso` · `promql_tools` `_err`·`_now_iso` · `admin.py` 13 재노출 · `alarm.py` 모델 20 + `_stats_to_dict`·`_process_to_dict`·`_identity_dict` · `src/config/__init__.py` 30 재노출.
- **로거 이름 고정**: 옮긴 코드의 로거는 이동 전 모듈 이름(`src.nodes.*`·`src.orchestration.subagents`·`src.utils.query_gen_common`·`src.document.field_mapper`·`src.schema_cache.*`·`src.api.routes.admin`·`src.config`)으로 고정 — 로그 `%(name)s`·caplog 단언 불변.
- **이탈**: ① 테스트 파일 수정 5파일(경로 리터럴 — `test_plan67_phase0_core.py` 1줄 · `test_sufficiency.py` 1줄(`src/config/*.py` 연결) · `test_bench_report.py`·`test_bench_sweep.py` 각 3줄(설정 mtime 가드를 `src/config/*.py` 전체로 — I4가 가드 범위를 좁힌 것을 verifier가 지적) · `test_preflight.py` 대역 필드 2개(W1 X1 귀속 — 계획 끝 회귀에서 발견 · 옛 폴백 값을 명시 · 단언 변경 0)) — 되돌리는 비용: 이동을 되돌리지 않는 한 필요. ② 이동 코드에 런타임 중립 재작성(`asyncio.TimeoutError`→`TimeoutError` · `timezone.utc`→`UTC` · `Optional[X]`→`X | None` 등 — regress가 새 파일 전 줄을 이번 diff로 세서 기존 lint 부채를 정리 · 3.11+ 동일 객체). ③ N2 5/7 · M5 빌더 미이동 · A7 감사 래퍼 미통합 · I2 모듈명 변경 · A8 헬퍼 4곳 확장(위 표).
- **계획 끝 회귀(1회)**: `python scripts/regress.py --base 08863d6 --files <W0 + W1 121 + W2 .py 193>` — 본체 통과 18,022 · 실패 5 · 건너뜀 51 · apm_gateway 1,614 통과 · mcp_server 980 통과 · ruff·mypy 이번 diff 줄 0 · arch·overfit·config_fallback 통과. 실패 귀속: [원래 실패] `test_plan160_chart_api::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` · `test_settings_help::test_t2_curated_groups_have_full_coverage` / [이번 변경 탓] 매뉴얼 앵커 A-49·A-70(근거 파일 이동) · `test_preflight::test_출력은_ASCII_구두점만_쓴다`(W1 X1 대역 누락) → 교정 후 해당 모듈 663 passed · `tests/test_manual` 619 passed. `범위: 모듈 단위 — 전체 미실행`. 도구 권고 블록(원문):

```
[전체 회귀 권고] 사유: 허브 모듈 변경(전이 import 영향 ≥ 85%) — src.config 88%, src.config 88%, src.config.alarm 88%, src.config.data_access 88%, src.config.llm 88%, src.config.security 88%, src.config.services 88%, src.config.text2sql 88%
                        공개 시그니처·반환 형태 변경(--wide 자동 적용) — src.nodes.candidate_generator.classify_complexity 삭제; src.nodes.candidate_generator.generate_candidates 삭제; src.nodes.candidate_selector.select_candidate 삭제; src.nodes.candidate_selector.run_candidate_pipeline 삭제; src.nodes.column_deriver.StepwiseDeps 삭제; src.nodes.column_deriver.StepwiseLimits 삭제 외 64
                        Python 모듈 이동·삭제 — src/config.py
                        직접 선택 비율 69% ≥ 40% — 전체와 차이가 작다
  → 사용자가 요청하면: python scripts/regress.py --full
범위: 모듈 단위 — 전체 미실행
```
  「삭제」 판정은 `sys.modules` 별칭 shim을 정적으로 읽지 못한 오탐이다(이름은 옛 경로에서 살아 있음 — verifier 33건 shim 테스트로 확인).
- **verifier**: Critical 0 · Major 0 · Minor 3(bench 설정 mtime 가드 범위 — 반영 · 런타임 중립 재작성 — 위 이탈 ②로 기록 · 경로 고정 부정 가드가 새 파일을 스캔하지 않음(`test_graph_retry_budget.py:68` — 현재 커버리지 손실 0 · shim 제거 사이클에서 넓힘)). 본문 동일성 AST 전수 · A8·`.env` I/O 기준선 차분 실행 동일 · 설정 28클래스 동일 · 인증 의존성 순서까지 동일.
- **매뉴얼**: 화면·사용법 변경 없음 — 근거 파일 경로만 동기화(`scripts/manual/features.yaml` 15줄 · `content/admin.md` 2줄 · 빌드 산출 3 · D-255 역방향 가드 통과).
- **잔여(후속 계획)**: N2 `text_terms`·`zone_clarification` 이동과 M5 빌더 이동은 각각 테스트 가드(허용 파일 목록 · `__module__` 조건) 확장이 먼저 · A7 사용자 관리 6라우트는 `_audit_admin_action` seam 규칙으로 남겼으나 현 테스트는 그 라우트를 patch하지 않음(옮겨도 깨지지 않음 — 판단 여지) · `CLAUDE.md`·`docs/34`·설정 도움말 YAML 등 문서의 `src/config.py` 언급 갱신(코드 밖 · 이 Wave 범위 밖) · W1 잔여(§5.3)는 그대로.

### 5.5 W3 — 쌍 경로 공통화 (별도 계획서 — U-11)

> 착수 전제: 이 계획 W2 완료(단계 4). 착수 시 새 계획서를 만들고 아래 표를 범위·성공 기준으로 옮긴다. **단계 5 = 별도 계획 3건**: 5a A1·A3 ∥ 5b N1(a)·O6 → 5c I1·X2·X5(`pii_filter`)(5c는 I1의 Facade 우회 지점 `multi_db_executor.py:3218,3224`가 5b와 겹쳐 5b 뒤).

| 스멜 | 내용 | 성공 기준 |
|---|---|---|
| A1·A3 | `_stream_graph_turn` · `_build_turn_response` · `_sse_response` · `_run_pre_gates`. `_scope_state` 시드·`_build_turn_input_state`/`create_initial_state` 초기화 책임은 **호출자에 남긴다** · `apm_source_pending` 시 `async for _ in _events: pass` 보존 | W0 done 스냅샷 불변 · stream 테스트 15파일 통과 · query.py 줄 수 감소 |
| N1(a) | `FormFillPivotPlan` 공용 순수 함수 | W0 피벗 입력 덤프 동일 · gold_smq·폼필 골든 바이트 동일 · 렌더 매트릭스 무갱신 |
| O6 | 존 역질문 공통 꼬리 추출(신호 함수 `_has_new_location_db_signal`·`ZONE_SKIP_SIGNAL_TERMS`는 현행 유지 — U-1 확정) | 양 단 역질문 테스트 통과 |
| I1 | Facade 우회 10곳 → Manager 공개 메서드 · `_prefer_redis()` · RedisSchemaCache 키 공간 합성(synonyms 먼저) · 키 빌더 1곳 | W0 fakeredis 스냅샷 동일 · 호출부 무변경 |
| X2·X5 | `pii_filter` `_flag` 통합(같은 5함수의 침묵 `except→고정값`도 함께 정리 — X5 `pii_filter` 분) · 이미 AppConfig를 받는 클래스의 `load_config()` 직호출 제거 | governance OFF 바이트 불변 |

### 5.6 W4 — 거대 함수 분해 (별도 계획서 — U-11 · G1·O2는 각각 단독)

> **단계 6 = 별도 계획 6건**: 6a G1 · 6b O2 · 6c O3(배선)·O7·O8·O9 · 6d A4·N5·N7(체인)·I5 · 6e M1·M4 · 6f G3·G7. 파일이 겹치지 않아 순서 제약은 없고 동시 진행은 2건까지 권고. 권장 착수 순: 6a·6b(최대 cc · 단독) → 6c·6d → 6e·6f.

| 스멜 | 내용 | 성공 기준 |
|---|---|---|
| G1 | 규칙 표 + `GateInputs` · 단계 번호 정규화(주석) | W0 골든 동일 · 정책 테스트 6파일 통과 · cc 감소 |
| O2 | `_RunCtx` · target dispatch 5 · `_decide_outcome`(순서 보존) | 거부 코드 단언 전수 통과 · cc 감소 |
| O3 | `resolve_ladder_tier` 선행 1회 · 단별 `_wire_*` | W0 그래프 위상 스냅샷 동일 · 기동 로그 1줄 불변 |
| O7 | replanner `GUARDS`·`FILTERS` 표 · `HandlerResult` TypedDict | replanner 테스트 · mypy |
| O8·O9 | semantic_router 3단 분할 · `parse_registry` 파서 추출 · `_coerce_arg` dispatch | 라우팅 테스트 · 경고 문구 불변 |
| A4 | lifespan 기동·종료 헬퍼 · `AsyncExitStack` | lifespan 테스트(예외 튜플 보존) |
| N5·N7·I5·G3·G7·M1·M4 | Extract Function · 노트 체인 튜플(`TEXT_NOTES`·`DOC_NOTES` = 현행 구성·순서 — U-8) · `register_tools` dispatch·`Annotated` | 각 모듈 테스트 · W0 MCP 도구 스냅샷 동일 |

### 5.7 W5 — God class·God module 분할 (별도 계획서 — U-11 · O1·A2·G2·M2 각각 단독)

> **단계 7 = 별도 계획 5건** — 같은 파일을 먼저 분해한 W3·W4 계획이 선행이다: 7a O1(← 6b `run_apm_query`) · 7b A2(← 5a 스트림 공통화) · 7c G2(← 6f) · 7d M2(← 6e) · 7e N6(← 6c `HandlerResult`). 선행이 끝난 계획부터, 동시 2건까지.

| 스멜 | 내용 | 성공 기준 |
|---|---|---|
| O1 | `src/orchestration/apm/` 순수 모듈 + `apm_query.py` Facade. `run_apm_query`·`_SESSION_FACTORY`·patch 대상 심볼(`link_business_names`·`get_registry`·`link_hostnames`·`poll_job`·`render_view_rows`)을 호출하는 함수는 **Facade에 남긴다**(U-3 확정 · D-337 ②) | apm 테스트 61파일 언급분 통과 · **테스트 파일 수정 0** · 순환 import 0 |
| A2 | `src/api/query/` + application 판정 모듈 + `query.py` 재노출 | 70개 테스트 파일 무수정 통과 |
| G2 | ① 와이어링 → ② EpisodeCoordinator → ③ 신호 탐지·DedupService(각각 별 커밋) | noise_gate 테스트 · 플래그 off 비트 동일 테스트 |
| M2 | `ApmCore` + 도구군 클래스 + `ApmTools` Facade | apm_gateway 테스트 · W0 도구 스냅샷 |
| N6 | `OutputState` TypedDict · 반환 키 정합 테스트 일반화 · 초기화 키 목록 데이터화 | mypy · 런타임 불변 |

- **회귀**: W3~W5 별도 계획서는 각자 「계획 시작 SHA」를 적고 각자 마지막 Wave에서 `regress.py --base <그 계획 시작 SHA> --files <그 계획이 바꾼 파일…>`을 1회 돌린다(D-303). 범위 줄과 `[전체 회귀 권고]` 블록을 보고에 그대로 옮긴다.

### 5.8 완료 지표(로드맵 전체 — W0~W2는 이 계획, W3~W5는 별도 계획서)

| 지표 | 현재 | 목표(방향) | 결과(2026-10-17 · 단계 8 — shim 정리 뒤 작업 트리) |
|---|---|---|---|
| 필드 기본값과 다른 `getattr` 폴백 | 약 80 | 0(D-331 ② 린트 `--ci` — W1) | **0** 달성(`config_fallback_check.py --ci` rc 0 · `7ba5c96` 재측정 84) |
| 상수·판정 사본(X3·X4) | X3 6벌 · X4 11벌 | 정본 1곳씩 | X3 **2**(`DEGRADED_KEY`·`DISPLAY_CUT_KEY` 각 1곳 `src/utils/task_result_keys.py`) 달성 · X4 **7**(같은 방법 재측정 12→7) 미달 — 소유 판정 2(정본 `intent_planner` + `semantic_router._ownership_enabled` 변형) · `_zone_label` 3(U-1: 3벌 상이 → 통합 안 함) · 엔진 별칭 1·`now_iso` src 1은 달성(apm_gateway·mcp_server 사본 2는 패키지 경계상 import 불가) |
| cc>50 함수 | 17 | 감소(G1·O2·O7·O8 대상은 각각 cc≤30) | **6** — 대상 4건 모두 ≤30(`decide_notification` 98→5 · `run_apm_query` 184→10 · replanner 66→7 · semantic_router 51→5 · 근사 cc) · 남은 6: `run_data_query_pipeline` 66 · `_build_mapping_user_parts` 54 · `_stream_graph_turn` 53 · `review_page` 52 · `AlarmWorker._process` 51(442줄 · 분할 전 44) · `mcp_server/security.py _code_text` 51(163이 새로 추가) |
| 2,000줄 초과 파일 | 14 | 감소(O1·A2·N2·I2·G2·M2 대상) | **5** — `multi_db_executor.py` 3,294 · `output_generator.py` 2,289 · `apm_query.py` 2,233(O1 — 대상 중 유일하게 2,000 초과) · `result_aggregator.py` 2,078 · `cache_manager.py` 2,050 · 대상 A2 `query.py` 4,312→1,947(단계 8 정리로 2,008→1,947) · N2 1,259 · I2 `document/field_mapper.py` 751 · G2 1,428 · M2 `tools.py` 253 |
| orchestration 지연 import SCC | 7모듈 | 0 | **8모듈** 미달·악화 — W2 O4로 2(`apm_jobs`↔`apm_query` · 단계 5 끝 `1ad7c161`에서 같은 정의로 재측정 확인)까지 줄었으나 7a가 만든 `apm/` 6모듈이 `apm_jobs`를 최상위 import하고 `apm_jobs`가 `apm_query`를 지연 import해 고리가 커졌다(최상위 import만 보면 0 · `__init__` 제외 정의) |
| arch_check warning | 117 | 43(D-331 ① — W1) → 17 이하(N3 — W2) | **17** · error 0 달성 |
| `overfit_check` | 기준선 0 신규 | 유지(자기 델타만 이동) | 신규 0 달성 |
| W0 스냅샷 | — | 전 Wave 무갱신 | 9파일 blob이 생성 커밋 `a409f482` 이후 동일 — 달성 |

**측정**: `.claude/skills/refactoring/scripts/smell_metrics.py`(근사 cc · 경로 `src noise_gate mcp_server/mcp_server apm_gateway/apm_gateway sre_agent` · tests/scripts 제외) + `arch_check`·`overfit_check`·`config_fallback_check` `--ci` + SCC 스크립트(`src/orchestration/` 모듈 간 import 그래프 · 함수 안 지연 import 포함 · `__init__` 노드 제외 · 크기≥2 강연결 요소 — 이 정의로 `7ba5c96`의 7모듈이 재현됨). `7ba5c96`에서 cc>50 17·2,000줄 초과 14·X3 6이 재현됐다. X4는 계획서 11과 달리 12로 셌다(원 합산 근거 불명 — 같은 방법으로 전후 비교).

**미달 항목과 후보 계획**(로드맵은 여기서 닫고 각각 별도 계획으로 연다):

| 남은 것 | 이유 | 후보 |
|---|---|---|
| orchestration SCC 8 | `apm_jobs.py:374`의 `apm_query` 지연 import가 `apm/`→`apm_jobs`→`apm_query`→`apm/` 고리를 닫는다 | `apm_jobs`가 쓰는 `apm_query` 기능을 주입 또는 `apm/`로 이동해 지연 import 제거(다음 APM 계획 — 180 §후속 shim 제거와 함께) → **plans/183 해소**(2026-10-17 · SCC 8→0 · `apm.common` 직접 import + 세션 공장 공급자 훅) |
| `apm_query.py` 2,233줄 | seam 소유 정의(`_SESSION_FACTORY`·단계 함수 중 seam 호출자)·소스 가드 고정 15가 Facade에 남음(D-337 ②) | 테스트 seam을 `apm/`로 옮기는 계획(테스트 수정 동반 — D-337 주의 ②) |
| `multi_db_executor.py` 3,294줄 · `_build_mapping_user_parts` cc54 · `_run_single_target` cc34 · `_multi_resource_pivot_result` cc33 | 166이 Wave에 배정하지 않았다(5b·5c는 쌍 경로 공통화·Facade만) | 멀티 DB 실행기 긴 함수 분해 계획 |
| `AlarmWorker._process` cc51 · 442줄 | 7c(177)가 협력자 분할만 하고 `_process` 본문은 후속으로 남김 | 177 §후속 `_process` 단계 분해 |
| `run_data_query_pipeline` cc66 · `_stream_graph_turn` cc53 | 166 스멜 목록 밖(분석 뒤 늘었거나 W 배정 없음) | 각 소유 영역 계획에서 판단 |
| `_finalize_targets` cc23 · `_parse_view_args` cc31 · `result_aggregator` 본체 cc28 · `_apply_empty_antecedent` cc27 | 174가 범위 밖으로 남김(cc≤30 목표는 대상 함수만) | 174 §5 후속 |
| X4 소유 판정 변형·`_zone_label` 3벌 | 변형은 `load_config` 읽기 경로 차이(행동 변경 여부 미확인) · `_zone_label`은 U-1로 통합 안 함 확정 | 소유 판정 변형만 행동 동등성 확인 뒤 별건 |
| `output_generator.py`·`result_aggregator.py`·`cache_manager.py` 2,000줄 초과 | 166 대상 아님(O1·A2·N2·I2·G2·M2 밖) | 필요 시 영역 계획 |

→ plans/182 #J4(X4 소유 판정 변형 — 행동 동등성 확인 뒤)

### 5.9 단계 8 결과 — D-337 ①③ shim·`_` 별칭 제거 사이클 (2026-10-17 · 세션 시작 `33f238ea`)

단계 3~7(166 W1·W2 · plans/168~181)이 남긴 재노출 shim·옛 `_` 이름 별칭·이름 집합 보존용 미사용 import를 이름마다 판정해 **사용처 0인 것만** 지웠다(D-337 ① 「다음 계획에서 `grep -w` 0 확인 뒤 제거」의 실행). 테스트·매뉴얼·스냅샷 수정 0.

- **판정 규칙**: `grep -rnw <이름>`(`src/ noise_gate/ apm_gateway/ sre_agent/ mcp_server/ tests/ scripts/ agents/` · JSON 픽스처 포함) 중 **그 shim 모듈 경로를 거치는 사용**(`from <shim> import` · `<shim 별칭>.X` · 문자열 patch 경로 · `setattr`/`patch.object`/`getattr(<shim>, "X")` · 메서드는 내부 호출·host 어댑터·`__new__` 우회 생성 포함)이 1건이라도 있으면 남김. 애매하면 사용으로 셈. 이름 집합을 단언하는 테스트(`test_plan178_verify`·`test_plan180_verify`·`test_plan166_w1_ssot_copies` 동적 모듈 루프)가 기대하는 이름과 매뉴얼 근거(`scripts/manual/content/admin.md:4130` `_owned_result`)도 남김.
- **4트랙 병렬**(파일 겹침 0): A `src/api/**` · B1 `src/orchestration/**`·`src/graph.py` · B2 그 밖 `src/` · C `noise_gate/`·`apm_gateway/`·`mcp_server/`·`sre_agent/`.

| 트랙 | 바꾼 파일 | 지움 | 내역 |
|---|---|---:|---|
| A | `src/api/routes/query.py` · `admin.py` (+ `admin_audit_logs`·`admin_drm`·`admin_settings_reload` 독스트링 정합) | 78 | query 재노출 41 · plans/179가 이름 집합(227) 보존용으로 남긴 미사용 import 29(ruff F401 29→0) · admin 재노출 8 |
| B1 | `src/orchestration/apm_query.py` · `subagents.py` | 95 | apm_query 재노출 66 · plans/180이 남긴 미사용 import 20 · subagents 재노출 4·별칭 2(`_request_query_text`·`_scope_time_resolution`)·고아 import 1 · (교정) 166 이전부터 미사용이던 import `_DEMONSTRATIVE_NOUNS`·`_DEMONSTRATIVE_PREFIXES` 2 |
| B2 | `src/utils/query_gen_common.py` · `src/document/field_mapper.py`(I2 Facade · `__all__` 포함) · `src/schema_cache/asset_generation_service.py` · `db_structure_service.py` · `src/nodes/output_generator.py` · (교정) `src/config/__init__.py` | 96 | 각 41 · 18 · 16 · 7 · 10 (재노출) · (교정) `src/config/__init__` 상수 재노출 3(`CANONICAL_QUERY_MODES`·`REWRITE_GATE_MODES`·`REWRITE_VERIFY_MODES`) · `query_gen_common._utilization_guard` 별칭 1(Plan 69 P2 산물) |
| C | `noise_gate/application/alarm_worker.py`(+ `dedup_service`·`episode_coordinator`·`signal_detection` 주석 정합) · `apm_gateway/apm_gateway/application/tools.py` · `mcp_server/mcp_server/polestar_tools.py` | 119 | alarm_worker 모듈 재노출 6 · `AlarmWorker` 위임 메서드 shim 11(`_episode_of`·`_late_promotion_on`·`_usable_episode_investigation`·`_episode_context_on`·`_maybe_episode_investigation`·`_record_apm_investigation`·`_remember_late_candidate`·`_sweep_late_promotion`·`_late_promote`·`_sweep_correlation_clusters`·`_prune_expired` — 내부·host·테스트 호출 0) · 고아 import 1(`Episode`) · apm tools 90(재노출 57·옛 이름 별칭 17·별칭 전용 import 14·미사용 `logger`+`logging`) · mcp polestar_tools 재노출 11 |
| 합계 | 19파일 | **388** | `sre_agent` 변경 0(단계 3~7 산물 shim 0) · `src/graph.py` 변경 0 |

- **동작 불변 확인**: diff 추가 줄은 여러 이름 import를 남은 이름으로 다시 쓴 줄과 주석·독스트링뿐, 삭제 줄은 import·재노출·별칭 대입·위임 메서드·`__all__` 항목뿐(팀 리드 diff 검토). 바꾼 18파일의 ruff F401·F811·F841 수는 기준과 같거나 줄었다(query.py 29→0 · 새 고아 import 0).
- **계획 끝 회귀**(1회 · `regress.py --base 33f238ea --files <18>`): 본체 통과 8,939 · 실패 2(둘 다 「원래 실패」 — `test_plan160_chart_api.py::test_create_app_registers_viz_routes_only_when_chart_enabled[True]` · `test_settings_help.py::test_t2_curated_groups_have_full_coverage`) · apm_gateway 1,629 · mcp_server 980 통과 · arch·overfit·config_fallback 통과 · ruff·mypy 이번 diff 줄 위반 0 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` 블록 없음. 두 번째 실패는 기존에 알려진 원래 실패 목록에 없던 것이다 — 「큐레이션 누락 1건 — config/settings_help/에 추가할 것: ['TEXT2SQL_TEMPLATE_ASSEMBLY']」(필드 `Text2SQL…template_assembly`의 설정 도움말 항목 부재). 도구 귀속 재실행과 팀 리드가 따로 만든 기준 worktree(`33f238ea` · 그 트리의 `src` import 확인)에서 모두 같은 단언으로 실패해 이 계획과 무관함을 확정했다(설정 도움말 보강은 별건).
- → plans/182 #H1(`TEXT2SQL_TEMPLATE_ASSEMBLY` 설정 도움말 큐레이션 누락)
- **교정 라운드**(verifier Minor 3 반영 — `src/config/__init__.py` · `subagents.py` · `query_gen_common.py` 6이름 제거 · `regress.py --base 33f238ea --files <3>`): 본체 통과 9,278 · 실패 1(원래 실패 `test_plan160_chart_api…[True]`) · 정적 게이트 통과 · `범위: 모듈 단위 — 전체 미실행` · 아래 권고 블록이 나왔다(`src/config` 허브 변경):
  ```
  [전체 회귀 권고] 사유: 허브 모듈 변경(전이 import 영향 ≥ 85%) — src.config 88%
    → 사용자가 요청하면: python scripts/regress.py --full
  ```
- **독립 검증**(verifier): Critical 0 · Major 0 · Minor 3(교정 완료) — 지운 382쌍 모두 기준에 있었고 지금 없음 · shim 경로 사용 0(.py 1,818 + 매뉴얼·yaml·json) · 동적 접근 후보 93건 무관 · 지운 메서드 11 참조 0 · ruff F821/F822/F823 0 · import 루트 78/78·apm 7/7·mcp 3/3 · 수집 오류 0(루트 22,815 · apm_gateway 1,643 · mcp_server 995) · 문자열 patch 15/15 해석 · 남긴 표 539행 전수 대조 · AST(import·별칭·`__all__`·독스트링 제외) 18/18 기준과 동일 · 매뉴얼 히트 7건은 환경변수 이름 등 부분 문자열 오탐. Minor: ① `src/config/__init__` 상수 3 사용 0 ② `subagents.py`의 `_DEMONSTRATIVE_NOUNS`·`_DEMONSTRATIVE_PREFIXES` import 사용 0(감사 누락) ③ `query_gen_common._utilization_guard` 별칭 사용 0(감사 누락) → 모두 제거(②③은 166 이전 산물 — 공통 규칙상 목록만 둘 대상이었으나 사용처 0이라 메인 지시로 함께 제거).
- **남긴 것**(다음 사이클 후보 — 사용처를 정본 경로로 옮기는 계획이 함께 와야 지울 수 있다): 아래 표. 요지 — 테스트가 shim 경로로 쓰는 것이 대부분이고, 코드 사용처가 남은 것은 `apm_jobs.py:27`·`result_viz.py:39`의 `from src.api.routes.query import _owned_result, _attachment_disposition, _audit_file_download` · `agent_orchestrator`의 `subagents._make_isolated_input` · `src/nodes/` 7모듈 `sys.modules` shim의 옛 경로 import(`agent_orchestrator.py:21`·`tier3_plan.py:50`·`subagents.py:554`·`src/api/query/fields.py:271`·`scripts/eval_text2sql.py:727`) · `scripts/eval_routing.py:1130`의 `intent_planner._capability_ownership_on` · `scripts/apm_bench/hygiene.py`의 `apm_query.TARGET_KINDS`. 분할 모듈의 `_` 별칭 22(`period_resolution`·`prior_turn_succession`·`sql_rewrite_eav`·`asset_generation_assembly`·`asset_generation_llm`)는 Facade 본문이 옛 이름으로 부른다 — 호출부 개명과 함께 지울 수 있다.
- **남긴 shim 독스트링**: `src/api/query/*`·`src/pipeline/turn/*`·`src/orchestration/apm/*`·분할 모듈 6개의 「옛 경로가 같은 이름으로 재노출한다」 문구는 이제 일부 이름에만 맞는다(파일 수를 늘리지 않으려고 미수정 — 다음 사이클 문서 정리).

<details><summary>남긴 shim·별칭 표(426개 · 모듈·종류별 · 괄호는 코드/테스트 히트 수(<code>-</code> = 미집계) · † = 매뉴얼 근거) — query.py 77 중 68과 alarm.py 모델 20은 그 모듈 본문이 실제로 쓰는 <code>as</code> 형식 import · apm_query·subagents 본문 사용 import 110은 표에서 뺐다</summary>

| 모듈 | 종류 | 수 | 이름(코드/테스트 히트 · 매뉴얼 근거는 †) |
|---|---|---:|---|
| `apm_gateway/apm_gateway/application/apm_core.py` | 별칭 | 11 | `_at`(25/150) · `_all_failed`(6/1) · `_reason_tail`(0/1) · `_resolve`(34/18) · `_inst_label`(0/1) · `_catalog`(2/187) · `_metric_plan`(2/1) · `_business_defs_of`(1/1) · `_event_options`(0/1) · `_filter_events`(0/1) · `_event_row`(2/2) |
| `apm_gateway/apm_gateway/application/manage_tools.py` | 별칭 | 1 | `_RuleCall`(0/0) |
| `apm_gateway/apm_gateway/application/tools.py` | 별칭 | 15 | `_Limits`(0/1) · `_given`(0/1) · `_check_n`(0/1) · `_ABSOLUTE_MAX_MS`(0/1) · `_hour_floor`(0/1) · `_hour_ceil`(0/1) · `_positive_int`(9/1) · `_target_text`(5/1) · `_nonneg_int`(0/1) · `_domain_order`(0/1) · `_fresh`(0/21) · `_guid`(2/2) · `_period_stats`(0/7) · `_sum_stats`(0/6) · `_stats_delta`(0/6) |
| `apm_gateway/apm_gateway/application/tools.py` | 재노출 | 7 | `Window`(20/4) · `limit_notice`(36/9) · `search_query`(10/1) · `QUERY_MAX`(6/1) · `SOURCE_KIND`(0/1) · `CHAT_PRINCIPAL`(5/5) · `KEY_ARG_NOTE`(0/2) |
| `mcp_server/mcp_server/polestar_tools.py` | 별칭 | 2 | `_err`(23/56) · `_now_iso`(35/4) |
| `mcp_server/mcp_server/polestar_tools.py` | 재노출 | 15 | `_DB2_SCHEMA`(0/0) · `_MAX_LOOKBACK_MINUTES`(1/0) · `_PG_SCHEMA`(0/6) · `_STAT_DATE_FMT`(6/0) · `_clamp`(0/0) · `_ctime_window_clause`(0/0) · `_id_literal`(0/7) · `_mask_process_args`(0/7) · `_q`(56/7) · `_rank_processes`(0/6) · `_row_limit`(4/2) · `_sql_literal`(45/21) · `_time_floor`(0/2) · `incident_window`(30/5) · `parse_reference_time`(0/3) |
| `mcp_server/mcp_server/promql_tools.py` | 별칭 | 2 | `_err`(39/56) · `_now_iso`(36/4) |
| `noise_gate/application/alarm_worker.py` | 메서드shim | 40 | `_build_history_repo`(0/0) · `_build_identity_resolver`(0/1) · `_build_process_client`(0/0) · `_build_noise_repo`(0/0) · `_build_apm_noise_ctx`(0/4) · `_build_episode_tracker`(0/4) · `_build_page_registry`(1/22) · `_observe_episode`(1/0) · `_record_episode_tier`(0/0) · `_episode_app_impact`(0/0) · `_episode_summary`(0/0) · `_episode_investigation`(0/0) · `_episode_snapshot`(0/0) · `_remember_episode_ctx`(0/6) · `_after_episode_graph`(0/0) · `_build_metric_baseline`(2/0) · `_build_embedding_provider`(0/2) · `_build_decision_store`(0/0) · `_build_investigation_store`(0/3) · `_build_silence_store`(0/0) · `_build_ticket_queue`(0/1) · `_build_feedback_store`(0/0) · `_build_sse_publisher`(0/6) · `_build_incident_publisher`(0/6) · `_build_sre_agent_client`(0/0) · `_build_apm_gateway_client`(0/5) · `_build_annotation_classifier`(0/3) · `_is_duplicate`(0/5) · `_is_duplicate_fingerprint`(1/59) · `_record_recurrence`(0/13) · `_annotate_semantic_dedup`(1/16) · `_update_firing_registry`(0/19) · `_apm_dedup_active`(0/3) · `_sweep_apm_persistence`(0/0) · `_cap_apm_persistence`(0/0) · `_detect_inhibition`(0/20) · `_detect_flapping`(0/13) · `_detect_storm`(1/16) · `_load_topology_graph`(0/0) · `_detect_correlated_storm`(0/38) |
| `noise_gate/application/alarm_worker.py` | 재노출 | 1 | `_RECENT_TEXTS_MAX_PER_SCOPE`(0/3) |
| `noise_gate/application/server_identity.py` | 재노출 | 2 | `is_apm_db_id`(2/12) · `is_apm_source`(26/23) |
| `noise_gate/infrastructure/polestar_noise_context.py` | 별칭 | 1 | `_unavailable`(2/12) |
| `src/api/query/store.py` | 별칭 | 3 | `_owned_result`(10/0†) · `_attachment_disposition`(3/5) · `_audit_file_download`(7/0) |
| `src/api/routes/admin.py` | 재노출 | 3 | `get_audit_logs_paginated`(0/2) · `get_audit_stats`(0/2) · `reload_settings`(0/17†) |
| `src/api/routes/alarm.py` | 별칭 | 3 | `_stats_to_dict`(0/2) · `_process_to_dict`(3/3) · `_identity_dict`(3/1) |
| `src/api/routes/alarm.py` | 재노출 | 20 | `_default_test_db_id`(0/3) · `AlarmAnalysisOutput`(-/-) · `AlarmCapabilitiesResponse`(-/-) · `AlarmFeedbackRequest`(-/-) · `AlarmFeedbackResponse`(-/-) · `AlarmFeedbackRetractRequest`(-/-) · `AlarmFeedbackRetractResponse`(-/-) · `AlarmFeedbackSummaryResponse`(-/-) · `AlarmMetricsResponse`(-/-) · `AlarmPromptSuggestion`(-/-) · `AlarmPromptSuggestRequest`(-/-) · `AlarmPromptSuggestResponse`(-/-) · `AlarmRawTestRequest`(-/-) · `AlarmTestRequest`(-/-) · `AlarmTestResponse`(-/-) · `IncidentAckResponse`(-/-) · `IncidentListResponse`(-/-) · `NotificationPreview`(-/-) · `_WebhookPreview`(-/-) · `_WorkbPreview`(-/-) |
| `src/api/routes/query.py` | 재노출 | 77 | `_results_store`(0/22) · `_next_event_or_timeout`(0/4) · `resolve_approval_action`(2/17) · `_nonsql_task_summary`(0/4) · `_summarize_tasks`(0/6) · `_unregistered_zone_clarification_or_none`(3/8) · `_zone_display_label`(2/3) · `_zone_group_exclusive_or_none`(3/9) · `_zone_gate_deferred_to_tasks`(0/8) · `_attachment_disposition`(3/5) · `_audit_file_download`(7/0) · `_owned_result`(10/0†) · `_store_result`(0/11) · `_graph_event_stream`(0/2) · `_heartbeat_sse_payload`(0/2) · `_is_subgraph_event`(1/8) · `_progress_sse_payload`(2/7) · `_single_done_stream`(0/0) · `_sse_event`(0/2) · `_sse_response`(0/2) · `_stream_error_payload`(0/0) · `_STREAM_KNOWN_NODES`(0/12) · `_token_text`(1/4) · `_get_checkpoint_state`(0/6) · `_resolve_turn_approval`(0/6) · `_source_selection_meta`(0/5) · `_zone_answer_parse_reuse`(0/5) · `_zone_answer_plan_reuse`(0/2) · `_is_legacy_structure_hitl_thread`(3/0) · `_parse_approval`(0/12) · `_release_legacy_structure_hitl`(0/0) · `_apm_source_text_answer`(2/6) · `apply_apm_source_selection_authorization`(0/3) · `apply_selection_authorization`(0/14) · `apply_source_selection_authorization`(0/1) · `_parse_selected_db_ids_form`(0/4) · `_with_current_identity`(1/7) · `_apm_source_fields`(0/4) · `_clarify_store_fields`(0/0) · `_clarify_turn_fields`(0/0) · `_count_human_messages`(0/0) · `_dependency_notes_field`(1/12) · `_disclosures_field`(1/3) · `_executed_sql`(1/6) · `_PARTIAL_DONE_DEFAULTS`(0/0) · `_partial_response_data`(0/0) · `_plan_summary_carry`(0/2) · `_plan_summary_field`(0/12) · `_pre_gate_plan_summary`(1/8) · `_rewrite_trace_fields`(0/3) · `_scope_reexpand_field`(0/3) · `_time_clarification`(0/0) · `_time_resolution_field`(0/0) · `_extract_node_progress`(1/8) · `_merge_node_delta`(0/0) · `_track_sql_state`(0/0) · `build_scope_reexpand`(2/5) · `_scope_narrowed_or_none`(0/10) · `_substitute_zone_placeholder`(0/14) · `_raw_query_seed`(0/4) · `_shadow_input_guard`(0/0) · `_turn_disclosures`(1/7) · `_audit_clarification`(1/4) · `_audit_scope_narrowed`(0/3) · `_file_zone_clarification_or_none`(0/36) · `_form_memory_delete_or_none`(0/7) · `_scope_select_or_none`(0/24) · `_zone_clarification_or_none`(1/54) · `_answer_prefix_text`(0/0) · `_bind_deadline`(0/2) · `_finish_timeline`(0/0) · `_processing_limit`(0/0) · `_request_deadline`(0/3) · `_stream_watch`(0/0) · `_unbind_deadline`(0/3) · `_get_file_extension`(0/0) · `_resolve_uploaded_bytes`(0/12) |
| `src/config/__init__.py` | 재노출 | 27 | `AlarmConfig`(0/17) · `NoiseGateConfig`(1/20) · `WorkbConfig`(0/0) · `DBHubConfig`(2/54) · `MultiDBConfig`(0/23) · `PolestarRestConfig`(0/2) · `QueryConfig`(1/18) · `RedisConfig`(0/3) · `SchemaCacheConfig`(0/4) · `LLMConfig`(0/37) · `OrchestratorConfig`(0/9) · `AdminConfig`(1/9) · `AuditConfig`(1/0) · `AuthConfig`(0/11) · `HostAuthzConfig`(0/0) · `SecurityConfig`(4/26) · `DrmConfig`(3/1) · `ObservabilityConfig`(1/3) · `PortalHelpConfig`(0/7) · `RagConfig`(0/4) · `ResultVizConfig`(0/3) · `ServerConfig`(1/31) · `CompositeConfig`(0/11) · `IntentFrameConfig`(0/1) · `RouterConfig`(0/5) · `SynonymMatchConfig`(0/0) · `Text2SQLConfig`(1/18) |
| `src/document/field_mapper.py` | 재노출 | 21 | `MappingResult`(8/67) · `_apply_eav_synonym_mapping`(0/18) · `_apply_hint_mapping`(0/0) · `_apply_synonym_mapping`(0/33) · `_build_eav_mapping_guide`(0/0) · `_core_entity_tables`(1/10) · `_format_field_names_with_examples`(0/0) · `_format_schema_columns`(0/8) · `_invoke_llm_mapping`(0/0) · `_is_metric_usage_field`(0/7) · `_load_structure_declarations`(0/2) · `_register_llm_mappings_to_redis`(0/30) · `_register_llm_synonym_discoveries_to_redis`(0/18) · `_resolve_fallback_db_id`(0/7) · `_schema_uses_eav_metric_pivot`(0/4) · `_sub_table_scope`(1/8) · `_synonym_match`(0/16) · `_validate_mapping`(0/16) · `analyze_md_diff`(2/7) · `apply_mapping_feedback_to_redis`(2/8) · `extract_field_names`(2/31) |
| `src/domain/disclosure.py` | 재노출 | 1 | `SPAN_MAX_CHARS`(6/6) |
| `src/graph.py` | 재노출 | 1 | `NON_SQL_RETRY_BUDGET`(-/-) |
| `src/nodes/candidate_generator.py` | 모듈shim | 1 | `(모듈 전체)`(0/3) |
| `src/nodes/candidate_selector.py` | 모듈shim | 1 | `(모듈 전체)`(0/3) |
| `src/nodes/column_deriver.py` | 모듈shim | 1 | `(모듈 전체)`(0/3) |
| `src/nodes/field_mapper.py` | 별칭 | 1 | `_resolve_priority_db_ids`(1/41) |
| `src/nodes/intent_frame_builder.py` | 모듈shim | 1 | `(모듈 전체)`(3/2) |
| `src/nodes/key_bridge.py` | 모듈shim | 1 | `(모듈 전체)`(3/4) |
| `src/nodes/multi_db_executor.py` | 별칭 | 1 | `_get_eav_pattern`(1/18) |
| `src/nodes/multi_db_executor.py` | 재노출 | 1 | `split_mapping_entries`(5/1) |
| `src/nodes/output_generator.py` | 재노출 | 25 | `_append_condition_changes`(0/2) · `_append_cross_system_notes`(0/9) · `_append_current_month_partial_note`(0/15) · `_append_elliptical_succession_note`(0/2) · `_append_form_fill_notes`(0/32) · `_append_generator_notes`(0/3) · `_append_inferred_mapping_info`(0/10) · `_append_limit_truncation_note`(0/17) · `_append_query_period_note`(0/2) · `_append_scope_note`(1/8) · `_append_spike_notes`(0/5) · `_append_unavailable_metric_notes`(0/11) · `_applied_row_limit`(0/4) · `_condition_change_texts`(0/2) · `_display_field_name`(0/0) · `_form_month_fields`(0/3) · `_format_ym`(0/1) · `_generator_note_texts`(0/0) · `_limit_truncation_texts`(0/1) · `_month_to_date_period`(0/0) · `_turn_query`(0/0) · `append_structure_missing_note`(5/9) · `applied_period`(0/3) · `executed_sql_list`(0/0) · `query_period_texts`(0/8) |
| `src/nodes/prompt_blocks.py` | 모듈shim | 1 | `(모듈 전체)`(1/24) |
| `src/nodes/query_generator.py` | 별칭 | 1 | `_get_eav_pattern`(1/18) |
| `src/nodes/table_selection.py` | 모듈shim | 1 | `(모듈 전체)`(1/13) |
| `src/orchestration/apm_jobs.py` | 별칭 | 1 | `_now_iso`(-/1) |
| `src/orchestration/apm_query.py` | 재노출 | 13 | `COMPARE_ASK_NOTE`(0/5) · `COMPARE_ELAPSED_NOTE`(0/4) · `COMPARE_OVERLAP_NOTE`(0/3) · `DEFAULT_VIEW`(0/1) · `DISPLAY_CELL_MAX`(0/2) · `_COERCERS`(0/4) · `_period_list`(0/1) · `plan_compare_window`(0/20) · `SOURCE_LABEL_KEY`(0/4) · `INSTANCE_ID_TOOLS`(0/1) · `TARGET_KINDS`(2/0) · `_CALL_BUILDERS`(0/4) · `DEGRADED_KEY`(0/1) |
| `src/orchestration/db_targeting.py` | 별칭 | 1 | `_strip_location_terms`(-/3) |
| `src/orchestration/doc_query.py` | 재노출 | 1 | `DEGRADED_KEY`(-/1) |
| `src/orchestration/host_inspect.py` | 재노출 | 1 | `DEGRADED_KEY`(-/1) |
| `src/orchestration/intent_planner.py` | 별칭 | 1 | `_capability_ownership_on`(1/1) |
| `src/orchestration/portal_help.py` | 재노출 | 1 | `DEGRADED_KEY`(-/1) |
| `src/orchestration/subagents.py` | 별칭 | 4 | `_normalize_targets`(0/2) · `_extract_identity_rows`(0/13) · `_make_isolated_input`(2/74) · `_pack_pipeline_result`(0/11) |
| `src/orchestration/subagents.py` | 재노출 | 7 | `REASON_SOURCE_INACTIVE`(0/1) · `REASON_SOURCE_NOT_CANONICAL`(0/6) · `REASON_SOURCE_UNSUPPORTED`(0/1) · `_has_new_location_db_signal`(0/5) · `_refers_to_specific_server`(0/7) · `_strip_location_terms`(0/3) · `pin_targets_to_hints`(0/4) |
| `src/orchestration/task_progress.py` | 재노출 | 1 | `dispatch_progress_event·emit_step`(-/-) |
| `src/schema_cache/admin_jobs.py` | 별칭 | 1 | `_now_iso`(37/3) |
| `src/schema_cache/asset_generation_assembly.py` | 별칭 | 3 | `_ColumnPlan`(3/1) · `_column_evidence`(1/2) · `_assemble`(2/13) |
| `src/schema_cache/asset_generation_llm.py` | 별칭 | 10 | `_definition_rows`(5/2) · `_display_row`(6/1) · `_merge_definition_batch`(2/1) · `_ask`(9/43) · `_SqlCheck`(6/0) · `_execute_check`(5/0) · `_validate_examples`(4/0) · `_validate_section`(11/0) · `_FENCE_RE`(3/0) · `_SIDE_EFFECT_FUNC_RE`(5/0) |
| `src/schema_cache/asset_generation_service.py` | 재노출 | 36 | `DEFINITION_BATCH_SIZE`(0/0) · `EXAMPLE_COUNT`(2/0) · `SECTION_MAX_CHARS`(2/0) · `SQL_CHECK_LIMIT`(4/0) · `SqlChecker`(0/0) · `_ColumnPlan`(0/1) · `_DefinitionContext`(0/0) · `_DefinitionPlan`(0/0) · `_FENCE_RE`(2/0) · `_SIDE_EFFECT_FUNC_RE`(4/0) · `_SqlCheck`(4/0) · `_approvable_definitions`(0/0) · `_ask`(3/0) · `_assemble`(0/3) · `_asset_summary`(0/0) · `_column_candidates`(0/0) · `_column_evidence`(0/2) · `_definition_batch`(0/0) · `_definition_rows`(0/2) · `_definition_summary`(0/0) · `_display_row`(0/1) · `_draft_comments`(0/0) · `_execute_check`(4/0) · `_merge_definition_batch`(0/1) · `_name_match_candidates`(0/0) · `_relation_candidates`(0/0) · `_relation_item`(0/0) · `_row_origin`(0/0) · `_scope_tables`(0/0) · `_str_list`(0/0) · `_table_columns`(0/0) · `_validate_examples`(2/0) · `_validate_section`(9/0) · `build_asset_summary`(2/0) · `definition_batches`(0/4) · `load_import_yaml`(0/4) |
| `src/schema_cache/db_structure_service.py` | 별칭 | 1 | `_now_iso`(36/3) |
| `src/schema_cache/db_structure_service.py` | 재노출 | 4 | `AdminServiceBase`(6/0) · `_is_valid_source`(0/0) · `_legacy_meta_summary`(0/0) · `resolve_table`(4/0) |
| `src/schema_cache/schema_probe.py` | 재노출 | 1 | `engine_key`(8/3) |
| `src/schema_cache/structure_store.py` | 별칭 | 1 | `_now_iso`(36/3) |
| `src/utils/period_resolution.py` | 별칭 | 4 | `_months_from_parsed_time_range`(2/0) · `_month_shift`(7/0) · `_resolve_month_range_expr`(3/0) · `_resolve_half_year_expr`(2/0) |
| `src/utils/prior_turn_succession.py` | 별칭 | 4 | `_selects_server_set`(2/0) · `_has_additive_marker`(2/0) · `_has_new_identifier`(2/0) · `_single_previous_entity`(2/0) |
| `src/utils/query_gen_common.py` | 재노출 | 40 | `BlockedComparison`(2/3) · `ComparisonPeriods`(2/3) · `DEMONSTRATIVE_NOUNS`(3/0) · `DEMONSTRATIVE_PREFIXES`(3/0) · `ELLIPTICAL_SUCCESSION_KEY`(5/7) · `FALLBACK_TIME_RANGE_LLM`(0/0) · `HOST_IDENTIFIER_FIELDS`(14/2) · `StatMonth`(9/0) · `_ALL_QUERY_LIMIT`(10/26) · `_has_additive_marker`(0/0) · `_has_new_identifier`(0/0) · `_month_shift`(0/0) · `_months_from_parsed_time_range`(0/0) · `_resolve_half_year_expr`(0/0) · `_resolve_month_range_expr`(0/0) · `_selects_server_set`(0/0) · `_single_previous_entity`(0/0) · `_unit_normalized_expr`(3/0) · `eav_value_cast_columns`(5/5) · `enforce_all_query_limit`(7/20) · `explicit_row_count`(3/2) · `extract_sql_from_llm_response`(0/16) · `extract_sql_from_response`(7/19) · `fallback_counters`(0/7) · `has_all_scope_keyword`(4/10) · `has_full_scope_request`(7/3) · `has_host_identifier_filter`(6/2) · `is_demonstrative_identifier`(11/2) · `is_full_scan_query`(5/2) · `looks_like_process_rows`(5/0) · `normalize_eav_numeric_casts`(5/19) · `normalize_eav_unit_casts`(5/20) · `normalize_stat_month`(3/0) · `note_fallback`(0/0) · `previous_month`(3/0) · `refers_to_demonstrative_server`(16/0) · `render_elliptical_succession_note`(2/3) · `reset_fallback_counters`(0/3) · `resolve_effective_limit`(5/19) · `resolve_query_limit`(19/30) |
| `src/utils/sql_rewrite_eav.py` | 별칭 | 1 | `_unit_normalized_expr`(4/0) |

합계 426

</details>

---

## 6. 기존 결정과의 충돌 검토

| 결정 | 요지 | 이 계획과의 관계 | 판정 |
|---|---|---|---|
| D-003 | 읽기 전용 3중 방어 · 절대 변경 불가 | 리팩터는 가드를 옮기지 않는다. S1(mcp_server 단일 초크포인트)은 **강화 방향의 행동 변경**이라 분리 | 충돌 없음 · 강화는 `plans/163`(U-6 확정 · D-003 부기 후보는 163에서) |
| D-089 | 어댑터 레지스트리 · 조립기는 어댑터 모듈 직접 import 허용 · 두 번째 어댑터 전 훅 확대 금지 | N4는 반복 조합 헬퍼만 추가하고 직접 import 13건은 **위반이 아니므로** 손대지 않는다. DBAdapter 전면 훅화·Dialect Strategy 미도입 | 충돌 없음 |
| D-088 | 공용 계층 DB-agnostic · overfit 기준선 | 리터럴 이동은 자기 델타만(§5.0-3). 리터럴 소거 자체는 목표 아님 | 충돌 없음 |
| D-139 · D-048 | 기능별 최상위 패키지 · noise_gate in-process 예외(D-048 워커 in-process) · `src → noise_gate` 잔존 · 역방향은 config/llm/utils/routing 최소(**D-139 ②**) | A8은 src→noise_gate 방향 유지. G6(허용 범위 밖 3건)은 **D-139 부기(2026-10-14)로 허용 목록에 넣음** — stdlib만 쓰는 `src.domain.*` 순수 모듈 · `src.observability.investigation_metrics`. 허용 목록이 D-139 ②에 있어 D-048이 아니라 D-139에 부기했다 | 해소(U-5 확정) |
| D-161 | 「분기가 있다」만으로 죽은 경로 판정 금지 · 폐기 전 4항 실측 | 이 계획은 **어떤 단·플래그 분기도 제거하지 않는다**(A4 lifespan 분기·O3 사다리 배선 모두 「분리」만). 미사용으로 측정된 설정 필드 4개(`prompt_knowledge_render`·`prometheus_enabled`·`ai_severity_escalate_only`·`availability_staleness_enabled`)도 4항 실측 없이 다루지 않고 `plans/70` P0-3(플래그 감사)에 넘긴다 | 충돌 없음 |
| D-225 → D-251 | 사다리 · 2단 기준 · 3단 비교 arm · 판정 단일 출처 `observability/ladder.py` | O3은 판정 SSOT를 `ladder.py`로 **강화**하는 방향. O6 신호 통일(2단 기준으로 3단을 맞출지)은 행동 변경 | 충돌 없음 · 통일하지 않음(U-1 확정) |
| D-066 · D-067 | 단일/멀티 경로 동등화 · 드리프트 가드 | N1·A1은 수작업 대칭을 코드 공유로 바꾸는 **강화** | 충돌 없음 |
| D-129 | 설정 카탈로그 SSOT = pydantic 인트로스펙션 | I4(config 패키지화)가 `field_index()` 결과를 바꾸면 위배 → W0 실측 + `catalog_diff` 0 조건 | 조건부(U-10 확정 — W0 판정 · `catalog_diff` 0) |
| D-179 | (본문) 설정 UI 경계 — 별도 패키지 설정을 본체 UI에서 쓰지 않음 | A7 `.env` I/O 이동은 본체 `.env`만 다룸 — 경계 불변. *참고: CLAUDE.md 품질 게이트 절이 「독스트링의 스키마 리터럴도 걸린다(D-179)」로 인용하나 D-179 본문은 설정 UI 경계다. overfit 기준선 규정의 출처는 D-088 · CLAUDE.md 「품질 게이트」 — 인용 번호 불일치를 실측했다(이 계획에서 고치지 않음)* | 충돌 없음 |
| D-274 | apm_gateway 독립 패키지 · 양방향 import 0 · 제니퍼 토큰 격리 | M6은 공유 라이브러리 대신 계약 스냅샷 테스트 · M1의 도구 이름·스키마 불변 | 충돌 없음 |
| D-134 | 동작 불변 게이트 4종(프롬프트 sha256 · 실패 집합 기준선 · arch/overfit · 골든 바이트) | 이 계획의 검증 방식으로 **재사용** · 저장소 전반 규약으로 일반화한 것이 D-337 | 정합 |
| D-070 | 사용자/운영자 JWT 시크릿 분리 | A5는 관리자 `type` 검증만 공용 함수로 옮김 · 사용자 `type` 검증 추가(S2)는 `plans/163` | 정합 |
| **D-337**(신규) | 구조 리팩터 규약 — shim 1사이클 · seam 보존 · `_` 공개명+별칭 · overfit 경로 델타 · 특성화 선행 · 행동 변경 분리 · 커밋 단위 | §5.0이 이 결정의 적용 | 정합 |
| **D-331**(신규) | 품질 게이트 조정 — arch 노드 간 경고 범위 한정 · 설정 폴백 린트 CI | W1에서 구현 | 정합 |
| D-303 | 회귀 정책 | §5.0-6 | 정합 |
| D-255 | 매뉴얼 동반 | 사용자 접점 변경 0이 목표 — 보고에 미반영 사유 명시 | 정합 |

**결정 등재(2026-10-14 · §7 확정 반영)**: D-337(구조 리팩터 규약 — D-134를 저장소 전반으로 일반화 · U-2·U-3·U-6 원칙 포함) · D-331(품질 게이트 조정 — U-4·U-7) · D-139 부기(U-5). U-1·U-8·U-9·U-10·U-11은 이 계획 내부 처리 방침이라 D-번호를 달지 않았다.

---

## 7. 결정(2026-10-14 · 사용자 위임 — 권고 채택)

사용자 지시(2026-10-14 「전체적인 코드와 계획에 맞게 결정사항은 권고에 맞게 정리하라」)에 따라 v1.0의 권고안으로 확정했다. 권고가 「확인 요청」·「실측 후 결정」이던 U-5·U-8은 2026-10-14 코드 실측으로 한 안을 골랐다.

| # | 항목 | 결정 | 근거(한 줄) | 기록 |
|---|---|---|---|---|
| **U-1** | 존 역질문 「안 묻는」 신호 2단(`_has_new_location_db_signal`)↔3단(`ZONE_SKIP_SIGNAL_TERMS`) 차이(O6) · `_zone_label` 3벌(X4) | **① 공통 꼬리만 추출, 신호 차이는 유지.** `_zone_label`은 W0 특성화에서 전 db_id 출력이 같을 때만 합치고 하나라도 다르면 현행 유지. 신호 통일(②)은 별건 | 통일은 3단 응답을 바꾸는 행동 변경이고, 3단은 2단 대비 비교 arm(D-251)이라 기준을 맞추는 일은 성능 비교 계획에서 정할 문제 | 계획서 내부 |
| **U-2** | 모듈 밖에서 쓰는 `_` 접두 함수(`_make_isolated_input`·`_pack_pipeline_result`·`_Limits`·`_check_n`·`_given` 등) | **① 공개명 승격 + 옛 이름 별칭 1사이클** | 이름이 사용 범위를 거짓으로 알리는 상태를 이동 시점에 함께 바로잡고, 별칭으로 테스트·피어 브랜치 파손 0 | **D-337 ③** |
| **U-3** | `apm_query._SESSION_FACTORY` 테스트 seam(O1) | **① seam을 Facade(`apm_query.py`)에 남긴다** — `run_apm_query`·`_SESSION_FACTORY`·patch 대상 심볼을 호출하는 함수는 Facade 잔류, 순수 모듈만 `orchestration/apm/`로 | 실측: `_SESSION_FACTORY`는 `run_apm_query` 안(2280)에서만 읽히고 테스트 38파일이 52곳 patch · 다른 patch 심볼 5종 27곳 — 남기면 테스트 수정 0·순환 없음 | **D-337 ②**(일반 원칙) · O1 범위는 계획서 |
| **U-4** | arch_check 「노드 간 직접 의존」 경고 범위 | **① 노드 패키지 간(`src.nodes.*`↔`src.nodes.*` · `noise_gate.application.nodes.*`↔같은 패키지)으로 한정** | 실측: 경고 117 중 112가 이 규칙이고 그중 74건은 한쪽 이상이 노드 패키지 밖 application 모듈(어댑터·semantic·tools 내부 · 노드→비노드 헬퍼 등) — 한정하면 43, 이후 N3 이동 효과가 경고 수로 보인다 | **D-331 ①** · 구현 W1 |
| **U-5** | `noise_gate`가 역방향 허용 범위 밖 `src` 모듈 3개 import(G6) | **① 허용 목록 부기(코드 이동 없음)** — stdlib만 쓰는 `src.domain.*` 순수 모듈 · `src.observability.investigation_metrics` | 실측: 3모듈 모두 stdlib만 import(154·234·206줄) · src 쪽 소비처 2~4곳씩 — `noise_gate`로 옮기면 `src → noise_gate` 의존이 새로 생긴다. 허용 목록 원문이 D-139 ②라 D-048이 아니라 D-139에 부기 | **D-139 부기** |
| **U-6** | 보안 성격 행동 변경 — S1(mcp_server 읽기 전용 단일 초크포인트·PG 세션 read-only) · S2(사용자 토큰 `type`) · S3(예외 문자열 노출) · X6(LLM 클라이언트 env 폴백) | **① 별도 보안 계획 `plans/163`**(security-auditor 동반) · 이 계획에서는 현 위치·문구 그대로 | 리팩터의 게이트(스냅샷 불변)와 보안 강화(응답·차단 동작 변경)는 성공 기준이 정반대라 한 커밋·한 계획에 섞으면 둘 다 검증할 수 없다 | **D-337 ⑥**(원칙) · `plans/163` |
| **U-7** | X1 치환 시 `SimpleNamespace` 설정 테스트 파손 | **① 실 `AppConfig`(검증 대상 필드 명시)로 교정 + 폴백 린트를 CI 게이트로** | Known Mistakes 2026-09-17(getattr가 속성명 오류를 삼킴)와 CLAUDE.md 「테스트 config는 검증 대상 필드를 명시」와 같은 방향 — 보고 전용 린트는 재유입을 막지 못한다 | **D-331 ②** · 구현 W1 |
| **U-8** | 문서(xlsx/docx) 경로에 텍스트 경로의 노트 4종이 없음(N7) | **리팩터는 현행 구성·순서를 그대로 보존**(`DOC_NOTES` = 현 문서 경로 목록). 누락 여부 확인은 별건 후속(§8) | 실측: 4종은 `2d485af`(2026-09-08 · D-197·D-198)와 `f4501b2`(2026-09-29 · plans/123 · D-279)가 텍스트 경로에만 넣었고, 문서 경로 제외 의도는 어디에도 기록이 없다 — 의도가 불명확한 동작을 리팩터에서 바꾸지 않는다 | 계획서 내부 |
| **U-9** | `TEXT2SQL_MULTI_FULL_VALIDATION` 기본 OFF(단일/멀티 검증 비대칭 · N1) | **범위 밖 유지** | 기본 ON은 행동 변경 · N1 공통화는 이 플래그와 독립으로 가능 | 계획서 내부(§8) |
| **U-10** | `config.py` 패키지화(I4) | **① 조건부** — W0 실측표에서 `settings_catalog`가 config 클래스 열거에 묶이지 않았거나 재노출만으로 `field_index()`가 같으면 W2에 포함, 아니면 보류 | 팬인 70 · D-129 인트로스펙션 SSOT를 깨면 설정 UI가 흔들린다 — 판정 근거를 W0이 먼저 만든다 | 계획서 내부 |
| **U-11** | W3~W5 진행 단위 | **① Wave마다 별도 계획서**(규모가 크면 스멜 단위) · 이 계획은 W0~W2 직접 진행 + W3~W5 로드맵 | W3~W5는 스멜 하나가 단독 계획 규모(G1·O2·O1·A2·G2·M2)이고, 계획마다 회귀 기준 SHA를 따로 잡아야 D-303 계획 끝 회귀가 한 번에 판독 가능하다 | 계획서 내부 |

### 7.1 v1.0 선택지(이력 — 확정 전 원문)

| # | 항목 | 선택지와 결과 | 권고 |
|---|---|---|---|
| **U-1** | 존 역질문 「안 묻는」 조건이 2단(`_has_new_location_db_signal`)과 3단(`ZONE_SKIP_SIGNAL_TERMS`)에서 다르다(O6) · `_zone_label` 3벌 출력이 다를 경우(X4) | ① 공통 부분만 합치고 차이는 그대로(동작 불변) ② 2단 기준(D-251)으로 3단을 맞춤 — 3단 응답이 바뀔 수 있음 | ① 먼저, ②는 별건 |
| **U-2** | 다른 모듈이 가져다 쓰는 `_` 접두 함수(`_make_isolated_input`·`_pack_pipeline_result`·apm_gateway `_Limits`·`_check_n`·`_given` 등)를 공개 이름으로 바꿔도 되는가 | ① 공개명 + 옛 이름 별칭 1사이클 ② 현행 유지(이동만) | ① |
| **U-3** | `apm_query._SESSION_FACTORY` 테스트 seam 위치(O1) | ① Facade에 남기고 실행 모듈이 Facade 값을 읽음 — 테스트 수정 0 ② 실행 모듈로 옮기고 테스트 8+곳 patch 대상 수정 | ① |
| **U-4** | `arch_check`의 「application→application 노드 간 직접 의존」 경고 범위 — 현재 같은 패키지 내부(`db_adapters/polestar`·`semantic`·`tools`)까지 경고(22건 오탐성) | ① 규칙을 `src/nodes` ↔ `src/nodes`로 좁힘(품질 게이트 변경) ② 현행 유지 — N3 이동해도 경고 수 그대로 | ① (게이트 변경이므로 결정 기록 필요) |
| **U-5** | `noise_gate`가 D-048 허용 범위 밖 `src` 모듈 3개를 import(G6) | ① D-048에 허용 목록 부기 ② 순수 로직을 `noise_gate/domain`으로 이동 | 3건의 성격 실측 후 결정 |
| **U-6** | 보안 성격 행동 변경 3건(§3.8) — (a) mcp_server 읽기 전용 단일 초크포인트·PG 세션 read-only (b) 사용자 토큰 `type` 클레임 검증 (c) 예외 문자열 노출 + (X6) LLM 클라이언트 env 폴백 제거가 호출부 동작을 바꾸는 경우 | ① 별도 보안 계획(security-auditor 동반) ② 이 계획 W1에 편입 | ① |
| **U-7** | X1 치환 시 `SimpleNamespace` 설정을 쓰는 테스트가 깨질 수 있다(Known Mistakes 2026-09-17 방지책과 같은 방향) | ① 그 테스트를 실 `AppConfig` 경로로 교정 + 폴백 린트를 CI 게이트로 ② 린트는 보고만 | ① |
| **U-8** | 문서(xlsx/docx) 경로에 텍스트 경로의 노트 4종(`current_month_partial`·`limit_truncation`·`generator_notes`·`condition_changes`)이 없다(N7) | 의도인가 누락인가 — 누락이면 기능 결함(별건) | 확인 요청 |
| **U-9** | `TEXT2SQL_MULTI_FULL_VALIDATION` 기본 OFF로 남은 단일/멀티 검증 비대칭(N1) | 행동 변경 — 이 계획 범위 밖으로 둘지 | 범위 밖 유지 |
| **U-10** | `config.py` 패키지화(I4) 착수 여부 — 팬인 70 · D-129 인트로스펙션 영향 | ① W0 실측 후 W2에 포함 ② 보류 | ① 조건부 |
| **U-11** | W3~W5를 Wave별 별도 계획서로 쪼갤지 | ① 쪼갬(이 계획은 로드맵) ② 이 계획 하나로 진행 | ① |

---

## 8. 범위 밖 · 하지 않을 것

- **`plans/70` 범위**: 실행 경로 사다리 단 폐기(4단 legacy 포함) · `enable_*` 플래그 감사·상수화·삭제 · 시맨틱 레이어 수렴 · 문서 아카이빙. 미사용 측정 설정 필드 4개도 여기로.
- **행동 변경**: 보안 강화(S1~S3·X6 → `plans/163`) · 기본값 통일 · 신호 통일(U-1 ②) · 멀티 full validation 기본 ON(U-9) · 예외 메시지 변경.
- **후속 확인(별건 · U-8)**: 문서(xlsx/docx) 응답 경로에 텍스트 경로의 노트 4종(`current_month_partial`·`limit_truncation` — `2d485af` · D-197·D-198 / `generator_notes`·`condition_changes` — `f4501b2` · plans/123 · D-279)이 붙지 않는다(`output_generator.py` 텍스트 192-206 ↔ 문서 251-264). 누락이면 기능 결함 — 해당 영역(plans/123 계열) 계획에서 의도를 확인하고, 고치면 매뉴얼 동반(D-255) 대상인지도 함께 본다. 이 계획은 현 상태를 W0 노트 순서 단언으로 고정만 한다.
- → plans/182 #K8
- **I4(config 패키지화)**: W0 판정이 「보류」이면 여기로 옮긴다(U-10).
- **기능 추가·성능 최적화·테스트 감축**(테스트는 이 계획의 유일한 안전망).
- **`schema_cache/` 축소·LLM 대체·노드 통합 대형 프롬프트화**(`plans/70` §0.5 문헌 근거 유지).
- **`tests/`·`testdata/`·`tools/`·`agents/` 리팩터**(W0 신규 테스트 추가만 예외).
- `except Exception` 일괄 정리 · 동명 이질 함수 통합(§4.2).

---

## 9. 이력

- 2026-10-14 v1.0 — 최초 작성. 분석 기준 `7ba5c96`. 패키지·관심사별 병렬 분석 7건(api · orchestration/routing · nodes/db_adapters · infra/schema_cache/config · noise_gate/sre_agent · mcp_server/apm_gateway · 횡단 정량) + 팀 리드 정량 측정(ast 길이·cc · arch_check) + 핵심 주장 재확인. 코드 변경 0건.
- 2026-10-14 v1.1 — §7 사용자 결정 11건을 권고안으로 확정(사용자 위임 「결정사항은 권고에 맞게 정리하라」). U-5·U-8은 코드 실측으로 선택(U-5: 3모듈 stdlib-only → D-139 부기 / U-8: 4종 도입 커밋 2건이 텍스트 경로만 수정·의도 기록 없음 → 현행 보존 + §8 후속 확인). 결정 등재 D-337(구조 리팩터 규약)·D-331(품질 게이트 조정)·D-139 부기. 보안 3건 + X6을 `plans/163-security-hardening-separated.md`로 이관(X6 호출부 실측 완료 — 빈 `fabrix_base_url` 폴백·ollama env 우선은 행동 변경). §5: W1에 D-331 게이트 구현 추가·X6 삭제 · arch 경고 목표 117→43→17 이하(재측정) · O1 seam Facade 잔류 · 이 계획의 마지막 Wave = W2(계획 끝 회귀) · W3~W5 별도 계획서. W0 선행 조건 해제(착수 가능). 코드 변경 0건.
- 2026-10-14 v1.2 — 사용자 지시 「계획을 단계적으로 진행하도록 계획을 정리하라」. §0.1 단계별 진행 순서(단계 0~8 — 166 W0~W2 · 163 · W3~W5 별도 계획 14건을 한 줄로 정렬 · 단계별 선행·병렬·회귀·사용자 확인 지점 · 운영 규칙 7항) 신설. 163 위치를 「W1 전 또는 W1·W2 사이」에서 **단계 2(166 W1 전)로 확정**. 어느 Wave에도 없던 스멜 배정: A5·N4·X5(`pii_filter` 제외)·M6 → W1, A8 → W2, X5 `pii_filter` → W3(5c). S1 읽기 전용 통과표를 163 W0 단독 소유로(중복 제거). §5.1에 단계 칸 · §5.2~§5.4에 하위 단계 순서표·종료 게이트 · §5.5~§5.7에 별도 계획 ID(5a~7e)와 선행. 코드 변경 0건.
- 2026-10-14 W0(단계 1) — 계획 시작 SHA `08863d6` 기록. 특성화·스냅샷 테스트 10파일 + 스냅샷 9파일 추가(SSE done 34케이스 · 노드명 정합 · 그래프 위상 53조합 · 노트 순서 3경로 · `_zone_label` 9입력 · 폼필 피벗 입력 4케이스 · Redis 키 24 · `decide_notification` 512케이스 · MCP 도구 25종 · APM 출력 골든 18). 제품 코드 diff 0 · verifier Critical 0(Major 1 `.env` 누수 교정). §5.2 아래 「W0 실측 부록」 — 파손 범위 실측표 · **I4 판정 「착수」(경로 결합 4곳 동반)** · U-1 결과(3벌 상이 → `_zone_label` 통합 안 함) · 이상 8건(미수정).
- 2026-10-15 — main 재병합: main이 D-336(제니퍼 심각도 3 유형 정책)을 먼저 푸시해 이 계획의 구조 리팩터 규약을 **D-337**로 재채번(구 D-330 → D-336 → D-337). 본문·단계표·스킬 2종·W0 테스트의 참조를 함께 고쳤다. 계획서 번호 166은 그대로. 코드 변경 0.
- 2026-10-15 W1(단계 3) — D-331 게이트 구현(arch 경고 117→43 · `config_fallback_check.py` 62→0 · `regress.py` `--ci` 편입 · 허용 목록 2) · SSOT 기계 치환 X1 60지점 · X3·X4·N4·N1(b 1쌍)·A5·A6·X5·O3·G4·M3(28/32)·M6 · G5 해당 없음으로 닫음 · 5트랙 implementer(src 3묶음 순차·noise_gate·gateway 병렬) · verifier Critical 0 · W0 스냅샷 무갱신 · 커밋 전.
- 2026-10-15 W2(단계 4) — Move-only 분할 10스멜(O4·O5·N2·N3·N7·I2·I3·M5·A7·A8) 4a 병렬 + I4(`src/config/` 패키지) 단독 · 새 패키지 `src/pipeline/` · arch 경고 43→17 · overfit 기준선 무수정 · 테스트 경로 리터럴 5파일 이탈 · verifier Critical 0 · 계획 끝 회귀 1회(원래 실패 2만 남김 · 범위: 모듈 단위) · 매뉴얼 경로 동기화 · 이 계획 「로드맵」 전환(파일명 태그 제거) · 커밋 전.
- 2026-10-16 — 단계 4 커밋 `3a0a1101`(전체 회귀는 사용자 결정으로 미실행 — 모듈 단위 결과로 커밋) · 사용자 결정: A7 사용자 관리 라우트 6개 추가 이동은 하지 않고 W2를 닫는다(W4 관리자 정리에서 함께) · 문서의 `src/config.py` 표기를 `src/config/`로 정정(CLAUDE.md·docs/34·docs/21·설정 도움말 24파일).
- 2026-10-17 단계 8(로드맵 종료) — §5.8 지표 재측정(「결과」 칸 + 미달 후보 표: getattr 폴백 0 · X3 2 · X4 7 · cc>50 17→6 · 2,000줄 초과 14→5 · SCC 7→8(악화 — `apm_jobs` 지연 import) · arch 경고 17 · overfit 신규 0 · W0 스냅샷 무갱신) · §5.9 D-337 ①③ 제거 사이클(4트랙 병렬 · 사용처 0만 388 제거 · 19파일 · 남김 426 표 · verifier Minor 3 교정) · 계획 끝 회귀 원래 실패 2만(모듈 단위) · verifier Critical 0 · 로드맵 완료 · 커밋 전.
