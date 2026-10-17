# 177. `AlarmWorker` God class 분할 — 와이어링 이동 · `EpisodeCoordinator` · 신호 탐지·`DedupService` (G2 · 166 단계 7c)

> **작성일**: 2026-10-16 · **v1.0**
> **상태**: **완료**(2026-10-16 · 커밋 전 — 메인이 커밋) — W1 와이어링 `worker_wiring.py` · W2 `EpisodeCoordinator` · W3 `SignalDetector`·`DedupService` · `AlarmWorker` 2,420→1,121줄(메서드 61 유지 · 위임 shim) · 기존 테스트 수정 0 · 계획 끝 회귀 실패 0(모듈 단위) · 하네스 차분 0(58,240건) · verifier AST 의미 차이 0 · Critical·Major 0 · §4
> **상위**: `plans/166` §0.1 단계 7 · **계획 ID 7c · 스멜 G2**(§3.6 G2 행 · §4.x 「G2 `alarm_worker`」 실측 · §5.0 공통 규약 · §5.7 W5 · §8 범위 밖). 선행 6f(`plans/176`) 완료. 동시 진행은 6c(`plans/174` — `src/`)·7d(`plans/178` — `apm_gateway/`) — 이 계획은 `noise_gate/` 안에서만 작업하므로 파일 겹침 0. 6a·6f가 바꾼 `noise_gate/` 파일(커밋 전)은 내용을 고치지 않는다.
> **성격**: 동작 불변 구조 리팩터(D-337 · 166 §5.0) — 알람 처리 결정·티어·reason·로그 문구(로거 이름 포함)·Redis 키/스트림 쓰기 순서 바이트 불변 · 기존 테스트 수정 0 · 스냅샷 갱신 금지
> **계획 시작 SHA**: `1ad7c161`(RCA) **+ 단계 6 미커밋 변경 위** — 변경 전 코드 = 작업 트리 rsync 사본 `/private/tmp/claude-501/-Users-cptkang-AIOps-collectorinfra/0678a2b8-2f62-4a70-aeee-0a6b7d5bc5c3/scratchpad/p177_base/`(2026-10-16 16:28 · `.venv`·`build`·`logs`·`.git` 제외). `git worktree`로 1ad7c161만 꺼내면 단계 6 변경이 빠져 차분이 오염되므로 쓰지 않는다.
> **회귀 대상 파일(누적)**: 5 — `noise_gate/application/alarm_worker.py` · `noise_gate/application/worker_wiring.py`(W1 신규) · `noise_gate/application/episode_coordinator.py`(W2 신규) · `noise_gate/application/signal_detection.py`·`dedup_service.py`(W3 신규) *(누적 끝 — 계획 끝 회귀 1회 실행: §4)*
> **관련 결정**: D-337(리팩터 규약) · D-048·D-318·D-330·D-336(통보 정책 — 판정·재판정·감사 기록 순서 보존) · D-049(incident 계측 — 발행 순서 보존) · D-139(패키지 경계 — noise_gate 역방향 import 최소) · D-303(회귀) · D-331(게이트) · D-255(매뉴얼 — 사용자 접점 변경 0이라 대상 아님)
> **적용 스킬**: `.claude/skills/refactoring/`

---

## 1. 범위 — 착수 시 실측(작업 트리 = 사본)

`noise_gate/application/alarm_worker.py` 2,577줄(166 분석 시 2,310줄 — 162·144 후속으로 증가) · `AlarmWorker` 메서드 58개.

| 책임 구간 | 메서드 | 처방 | Wave |
|---|---|---|---|
| 와이어링 | `_build_*` 19종(history_repo·identity_resolver·process_client·noise_repo·apm_noise_ctx·episode_tracker·page_registry·metric_baseline·embedding_provider·decision_store·investigation_store·silence_store·ticket_queue·feedback_store·sse_publisher·incident_publisher·sre_agent_client·apm_gateway_client·annotation_classifier) | `worker_wiring.py` 모듈 함수로 기계적 이동 · 워커에 위임 shim | W1 |
| 사건(episode)·사후 승격·종합 조사 | `_observe_episode`·`_record_episode_tier`·`_episode_of`·`_late_promotion_on`·`_episode_app_impact`·`_episode_summary`·`_episode_investigation`·`_usable_episode_investigation`·`_episode_context_on`·`_episode_snapshot`·`_remember_episode_ctx`·`_maybe_episode_investigation`·`_record_apm_investigation`·`_remember_late_candidate`·`_sweep_late_promotion`·`_late_promote`·`_after_episode_graph` | `EpisodeCoordinator` Extract Class(합성) | W2 |
| 신호 탐지 · dedup | `_detect_inhibition`·`_detect_flapping`·`_detect_storm`·`_detect_correlated_storm`·`_sweep_correlation_clusters`·`_load_topology_graph`·`_prune_expired` / `_is_duplicate`·`_is_duplicate_fingerprint`·`_record_recurrence`·`_annotate_semantic_dedup`·`_apm_*` | 신호 탐지 클래스 · `DedupService` | W3 |

**하지 않는 것**: `_process`(약 440줄) 분할(클래스 분할과 같은 Wave 금지 — 166 G2 행) · 이벤트 버스 · 행동 변경(발견 시 §5 후속에 기록만).

## 2. W0 실측 — seam·shim 대상 (팀 리드 · 2026-10-16)

- **테스트 파일**: `alarm_worker`/`AlarmWorker`를 언급하는 테스트 40파일(`noise_gate/tests` 37 · `tests/test_api` 3).
- **워커 private 접근**: 496지점 · 36파일 · 서로 다른 `_` 멤버 56종(상위 `_is_duplicate_fingerprint` 51 · `_decision_store` 37 · `_page_registry` 30 · `_process` 29 · `_detect_correlated_storm` 27 · `_graph` 25 · `_episodes` 21 · `_detect_inhibition` 18 · `_build_page_registry` 17). 전부 위임 shim·속성 위임으로 보존한다.
- **모듈 seam**(`worker_mod` = `noise_gate.application.alarm_worker`에 `monkeypatch.setattr`): `_APM_PERSIST_MAX_KEYS`(144_apm_policy·144_verify) · `_LATE_PROMOTION_MAX`·`_LATE_PROMOTION_PER_TRIGGER_MAX`·`send_late_promotion`(144_w4) · `_EPISODE_CTX_MAX`(153_verify_contract) · `build_alarm_graph`·`ensure_consumer_group`·`read_messages`·`run_ticket_summary_loop`(ticket_batch_summary) · `AlarmWorker`(`tests/test_api/test_plan173_t1_lifespan_order.py`). `worker_mod.time`·`worker_mod.aioredis`는 모듈 객체 속성 patch라 이동과 무관.
- **인스턴스 메서드 교체**: `w._apm_policy_signal`(144_verify 2) · `worker._build_ticket_queue`(ticket_batch_summary 1) — `run()`·`_process`가 `self.` 경유로 부르는 것을 유지해야 한다.
- **`__init__` 우회 생성**: `AlarmWorker.__new__(AlarmWorker)` + `_config`만 대입 후 `_build_investigation_store()` 호출(`test_plan153_w2_investigation_store.py:241`) — shim은 `_config`만으로 동작해야 한다.
- **로거 이름**: `caplog.at_level(..., logger=worker_mod.__name__ / worker_mod.logger.name / "noise_gate.application.alarm_worker")`(page_review 2 · 144_wave_a 2 · severity_parse 1) — 옮긴 코드의 로그는 같은 로거 이름 `noise_gate.application.alarm_worker`로 남아야 한다(운영 로그 `%(name)s`도 동일).
- **소스 가드**: `test_plan144_verify.py:378` `_NOISE_GATE_CHANGED`(alarm_worker.py import 집합 검사 — 새 모듈은 대상 아님) · `tests/test_api/test_lifespan_shutdown_cancel.py:50`(server.lifespan — 무관).
- **품질 게이트 매핑**: `arch_check` `noise_gate.application.alarm_worker` = orchestration, 새 모듈 `noise_gate.application.*` = application(최장 prefix) — 새 모듈은 orchestration·interface를 import하지 않는다. `overfit_baseline.json`에 `alarm_worker.py` 항목 없음.
- **실 DB 통합 테스트 제외**: `noise_gate/tests/test_polestar_noise_context_integration.py`는 로컬 PG(5434)가 떠 있으면 픽스처 SQL을 적용한다(docs/18 2026-10-16) — 모든 pytest에서 `--ignore`로 뺀다. `regress.py`에는 제외 옵션이 없다.
- **차분 하네스**: 사본·작업 트리를 같은 시나리오로 돌려 결정·로그·Redis 호출·워커 상태를 비교하는 스크립트(scratchpad · W0 산출) — Wave마다 차분 0을 확인한다.

## 3. Wave

| Wave | 내용 | 상태 |
|---|---|---|
| W0 | §2 실측 · 차분 하네스(scratchpad `p177_harness/` — 시나리오 413 · 관측 58,240건 · 결정성 0 · 변이 3종 검출 · 미실행 메서드 0) | 완료 |
| W1 | 와이어링 → `noise_gate/application/worker_wiring.py`(478줄 · 함수 19) · 워커 위임 shim 19 · `alarm_worker.py` 2,577→2,220줄 · 지정 테스트 841 통과(사본과 동일) · `regress --no-tests` 0 · 하네스 차분 0 | 완료 |
| W2 | `EpisodeCoordinator`(`episode_coordinator.py` 491줄) — 메서드 17 이동 · 워커 shim 17 · **상태·협력자·상한 상수·발송 함수는 워커/Facade 소유 유지**, 코디네이터는 `EpisodeHost`(`_WorkerEpisodeHost` — 접근 시점 읽기)로 접근 · shim마다 생성 · `alarm_worker.py` 2,220→2,001줄 · 지정 테스트 508 통과(사본 id 집합 동일) · 하네스 차분 0 · `regress --no-tests` 0 | 완료 |
| W3 | `SignalDetector`(`signal_detection.py` 532줄 — 8메서드 · `_update_firing_registry` 포함) · `DedupService`(`dedup_service.py` 388줄 — 7메서드 + `_dedup_*` 순수 함수) · W2와 같은 host 어댑터 방식 · `_apm_policy_signal`(인스턴스 교체 seam)·`_evict_oldest`(`_APM_PERSIST_MAX_KEYS` seam)는 Facade 잔류 · `alarm_worker.py` 2,001→1,491줄 · 지정 테스트 640 통과(사본 id 집합 동일) · 하네스 차분 0 · `regress --no-tests` 0 | 완료 |

## 4. 결과

| 지표 | 착수 | 완료 |
|---|---:|---:|
| `alarm_worker.py` 줄 | 2,577 | 1,491 |
| `AlarmWorker` 클래스 줄 | 2,420 | 1,121 |
| `AlarmWorker` 메서드 | 61 | 61(위임 shim 51 포함 — 클래스 표면 불변) |
| `_process` 줄 | 442 | 442(분할 안 함 — §5) |
| 새 모듈 | — | `worker_wiring.py` 478 · `episode_coordinator.py` 491 · `signal_detection.py` 532 · `dedup_service.py` 388 |

- **설계(W2에서 확정 · W3 동일)**: 상태·협력자·patch되는 상수·발송 함수는 워커/`alarm_worker` 모듈이 계속 소유한다. 새 클래스는 `alarm_worker.py`의 모듈 수준 host 어댑터(`_WorkerEpisodeHost`·`_WorkerSignalHost`·`_WorkerDedupHost` — `__slots__=("_w",)` · 프로퍼티로 **접근 시점** 읽기 · 재대입 속성만 setter)를 거쳐 읽고 쓰며, shim 호출마다 만든다(`_episode_coordinator()`·`_signal_detector()`·`_dedup_service()`). **이탈 근거**: 계획 초안(§2)은 상태를 코디네이터로 옮기고 워커에 get/set 프로퍼티를 두는 안이었으나, `test_plan153_verify_contract.py:95`가 `object.__new__(AlarmWorker)`로 `__init__`을 우회해 `_episodes`·`_episode_ctx`를 직접 대입하므로 테스트 수정 없이는 성립하지 않는다(팀 리드 W0 grep은 `AlarmWorker.__new__` 형태만 봤다). 되돌리는 비용: 상태 소유 이전은 그 테스트 1파일 수정을 동반하는 후속 계획에서 host 어댑터만 바꾸면 된다(새 클래스 본문은 그대로).
- **로거 이름**: 새 모듈 4개 모두 `logging.getLogger("noise_gate.application.alarm_worker")` — 운영 로그 `%(name)s`·caplog 필터 바이트 동일.
- **재노출(D-337 ①)**: `_DEDUP_SEVERITY_KEY`·`_dedup_meta`·`_dedup_severity_rises`·`_RECENT_TEXTS_MAX_PER_SCOPE`(dedup_service 정본) · `_EPISODE_INV_UNUSABLE`·`_LATE_PROMOTION_SWEEP_SECONDS`(episode_coordinator 정본) · `_TOPOLOGY_NEGATIVE_TTL_SECONDS`(signal_detection 정본). patch되는 상수(`_LATE_PROMOTION_MAX`·`_LATE_PROMOTION_PER_TRIGGER_MAX`·`_EPISODE_CTX_MAX`·`_APM_PERSIST_MAX_KEYS`)는 `alarm_worker` 정본 유지.
- **계획 끝 회귀**: `python scripts/regress.py --base 1ad7c161 --files <회귀 대상 5>` → 본체 통과 1,159 · 실패 0 · 에러 0 · 건너뜀 0(alarm_worker 직접 import 37파일 + 저장소 전역 가드 7노드) · arch_check·overfit_check·config_fallback_check 통과 · ruff·mypy 이번 diff 줄 위반 0 · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고]` 블록 없음. `regress.py --plan`으로 선택 목록을 먼저 확인했다 — 실 DB 통합 테스트 2파일은 `alarm_worker`를 import하지 않아 선택되지 않았다(둘 다 이제 `RUN_DOCKER_IT` 옵트인 가드도 있다).
- **verifier(독립)**: AST 비교 64건(이동 51 + 모듈 함수 3 + 잔류 워커 메서드 10) 의미 차이 0 · 표기 차이만(타입 주석·`Optional`→`X | None`·독스트링 7) · 워커 메서드 61 시그니처 동일 · host 매핑 39속성 전수 정확(setter 누락 0) · seam 변이 테스트 23 통과 · 하네스 독립 재실행 차분 0 · 새 모듈 변이 6건 모두 검출 · 순환 import 0 · 테스트 파일 수정 0(`diff -rq`). Critical 0 · Major 0 · Minor 2(§5).
- **실수 기록**: 실 DB 통합 테스트 1파일만 제외해 `test_noise_gate_graph_integration.py`가 W1·W2 자기 테스트에서 로컬 PG에 픽스처를 3회 적용했다(멱등 · 피해 확인 안 됨) — `docs/18` 2026-10-16 (plans/177) 항목.

## 5. 후속

- **`_process`(442줄) 분할** — 이 계획 범위 밖(클래스 분할과 같은 Wave 금지 · 166 G2 행). 166 단계 8 지표 재측정 뒤 별도 계획 후보.
- **상태 소유 이전**(사건·탐지·dedup 상태를 새 클래스로) — `test_plan153_verify_contract.py`의 `object.__new__` 우회 1곳을 고치는 계획에서 host 어댑터만 교체.
- **shim·재노출 제거(D-337 ①)** — 다음 계획에서 `grep -w` 0 확인 뒤. `_RECENT_TEXTS_MAX_PER_SCOPE`는 `test_semantic_annotation.py` import를 `dedup_service`로 옮긴 뒤 제거. 나머지 6개는 저장소 사용처 0(하네스 외).
- **[Minor · verifier] 내부 호출 seam**: 옮긴 본문 안의 내부 호출(예: `after_episode_graph`→`late_promote`→`sweep_late_promotion`, `detect_correlated_storm`→`sweep_correlation_clusters`)은 이제 새 클래스 메서드끼리 부른다 — 워커 메서드를 인스턴스·클래스에서 바꿔 끼우면 바깥 shim 호출에만 걸린다. 현재 저장소 테스트·하네스에는 해당 교체 0건. 새 seam은 새 클래스 메서드다.
- **[Minor · verifier] 모듈 이름공간 축소**: `alarm_worker`에서 import 이름 23개(`load_rules`·`late_promotion_targets`·`episode_snapshot`·`update_flap_state`·`match_cluster` 등)가 빠졌다 — `monkeypatch.setattr(worker_mod, "<이름>")`는 이제 효과가 없다. 저장소 사용처 0. 병행 브랜치가 이 이름을 patch하면 조용히 무효화되니 병합 시 확인.
- **가드 범위**: `test_plan144_verify.py:378` `_NOISE_GATE_CHANGED`(apm_gateway import 금지)가 새 모듈 4개를 덮지 않는다 — 테스트 수정이라 이 계획에서 하지 않았다(현재 새 모듈의 apm_gateway import 0).
- **(선택) 성능**: shim 호출마다 host+서비스 객체 2개 생성 — 알람 1건당 수십 회 수준으로 무시 가능하나 성능 측정 계획이 생기면 확인 대상.
- → plans/182 #P4
- **차분 하네스 보존**: `scratchpad/p177_harness/`는 세션 임시 경로다 — 후속 계획이 재사용하려면 `noise_gate/tests/` 특성화로 옮기는 별건 필요.
- 행동 변경 후보: 0(이동 중 발견한 결함 없음).
