# 162. 노이즈 게이트 PAGE 필요성 재판정 — 24시간 PAGE 170건을 10~20건으로

> **작성일**: 2026-10-14 · **v1.1**(2026-10-14 — 게이트 권고안 확정 · W0 분석 스크립트 상세 계획 §10 추가)
> **상태**: **WIP — W0~W3 구현 완료(2026-10-14 · 커밋 없음)** · W0 `noise_gate/scripts/page_audit.py`·`page_audit_sanitize.py`(비식별 허용 목록·HMAC 가명·누출 검사·what-if · R1~R3는 도메인 `review_page` 호출로 W1과 차등 0) · W1 `noise_gate/domain/page_review.py`+`page_review` 단계(`NOISE_PAGE_REVIEW_MODE` 기본 off = 비트 동일) · W2 R4 `NOISE_COLLECTION_FAILED_PAGE_SEV3_ONLY`(기본 off)·수집 실패율 메타경보 · W3 관제 화면·매뉴얼·docs/28 §4-C · **잔여**: W4 내부망 W0 실행 → shadow 24h → enforce 판정 · G-2(R6 만성)·G-5(R5) 결정 · `replay_noise.py --page-review-mode`(§8) 미구현 · shadow/enforce 화면 캡처 · 사용자 경로 대표 PAGE 포인터 마스킹(운영 alarm_id 형식 실측 뒤) · R6 보존 1·2·4 미적용 이탈(W0 시뮬 — G-2 때 부기)
> **회귀 대상 파일**(D-303 · 계획 끝 1회 `regress.py --base 4073f7f`): `.env.example` · `config/settings_help/noise_gate_core.yaml`·`noise_gate_notify.yaml` · `noise_gate/application/alarm_worker.py`·`nodes/notification_gate.py` · `noise_gate/domain/notification_policy.py`·`stage_handling.py`·`page_review.py` · `noise_gate/infrastructure/decision_store.py` · `noise_gate/orchestration/alarm_graph.py` · `noise_gate/scripts/page_audit.py`·`page_audit_sanitize.py` · `src/config.py` · `src/api/routes/alarm.py`·`noise_dashboard.py` · `src/api/settings_catalog.py` · `src/static/{noise.html,admin/noise.html,css/noise-console.css,js/noise.js,js/noise-help.js}` · `scripts/manual/{features.yaml,content/admin.md,content/user.md}` · `src/static/manual/{admin.html,user.html,help_index.json}` · 테스트 `noise_gate/tests/test_{page_audit,page_audit_verify,page_review,page_review_verify,collection_failed_sev3_only,collection_failed_verify,decision_snapshot,decision_stage,plan153_w1_analysis_record}.py` · `tests/test_api/test_{noise_page_review_console,noise_page_review_console_verify,noise_health_collection_failed,noise_dashboard_api,plan153_w1_console_explain,settings_catalog}.py`
> **계획 시작 SHA**: `4073f7f`
> **요청 원문(2026-10-14)**: *"노이즈 관제에서 아직도 너무 많은 page가 있다. 24시간동안 현재 170 정도인다. 10~20개 내외가 되어야 한다. page가 꼭 필요한지 다시 판단하도록 해야 한다. 현재 코드를 다시 분석하여 개선 계획을 수립하라."* → v1.1: *"권고에 맞게 정리하고 별도의 테스트 스크립트를 통해 현재 알람의 분류 정책을 로깅하여 분석할 수 있는 정보를 취합하는 코드 작성 계획을 정리하라. 로그 정보에는 ip 등의 중요 정보는 제외시키고 최대한 분석할 수 있는 정보 위주로 정리되어야한다."*
> **관련 결정**: **D-035**·**D-048**(「심각도3 절대 PAGE」 · 수집 실패 보수 PAGE — **D-330으로 부분 개정 확정 · 효력은 W1·W2 구현 시**) · D-048.5(AI 상향 · 메시지형 한정) · D-318(`is_routine=False` 승격 제거 — 직전 감축) · D-320(dedup 심각도 상승 우회) · D-317(제니퍼·크로스소스) · D-049(incident) · D-247(관제 화면 근거) · D-255(매뉴얼 동반) · D-303(회귀)
> **신규 결정**: **D-330**(확정 2026-10-14 — PAGE = 새 사건의 첫 알림 · `page_review` · 수집 실패 sev3만 PAGE · 리마인더 24h · 강등 목적지 TICKET · W0 분석 로그 비식별 규칙) · D-321(반출 값 치환·IPv4 잔존 검사 전례)
> **선행·접점 계획**: `plans/52`(게이트 정본) · `plans/60`(E1~E7) · `plans/112`(관제 드릴다운) · `plans/117`(ML 보조 신호 — 별개) · `plans/144`(모드 사다리 off→shadow→enforce 전례)

---

## 0. 증거 규칙

- 코드 앵커는 전부 `4073f7f` 작업 트리에서 읽기·grep으로 확인했다. 실 LLM 0 · 운영 DB 0 · 운영 감사 파일 0.
- **「24시간 PAGE 170」은 사용자 보고값이다.** 단계별 분해는 아직 없다. 저장소에는 운영 `logs/alarm_decisions.jsonl`이 없고, 개발 맥에도 `.env`가 없어 운영 플래그 값을 모른다. 그래서 §3은 가설이고, W0이 실측으로 확정한다.
- 비교 기준선: D-318 실측(2026-10-07 · 24h) — 수신 1,334 · PAGE 99(심각도3 단락 33 · 매트릭스 66). D-318로 매트릭스 66건은 TICKET이 됐어야 하는데 지금 170이다. **같은 날 분포가 아니므로 증가 원인부터 분리해야 한다**(§3).

---

## 1. 결론

### 1.1 한 줄

**PAGE의 기준을 「심각도가 높다」에서 「사람이 지금 새로 움직여야 하는 사건의 첫 알림」으로 바꾼다.** 기존 파이프라인이 PAGE로 판정한 건마다 마지막 관문 「PAGE 필요성 재판정」(`page_review`)을 한 번 더 거친다. 이미 PAGE한 사건의 반복, 같은 호스트에서 이어 온 알람, 여러 서버에 같은 알람이 한꺼번에 뜬 경우는 **TICKET으로 내리고 첫 PAGE에 묶는다**(억제가 아니라 강등이다. 감사 기록·화면 표시는 그대로 남는다). off → shadow → enforce 순서로 켜고, 내부망 shadow 24h에서 「했을 PAGE 수」가 20건 이하이고 놓친 사건이 0건이면 enforce로 넘긴다.

### 1.2 왜 이 모양인가

| 사실(코드 실측) | 함의 |
|---|---|
| 심각도3은 `decide_notification` step 3에서 **모든 억제 단계보다 먼저** PAGE로 끝난다(`noise_gate/domain/notification_policy.py:656`). 의존성·인히비션·스톰·크로스-호스트 상관은 전부 그 뒤에 있다 | 스위치 장애로 서버 30대가 sev3를 내면 **PAGE 30건**이다. 지금 있는 묶음 기능은 sev3에 하나도 닿지 않는다 |
| 지문 dedup은 4시간 고정창이다(`repeat_interval_seconds`·`sev3_repeat_interval_seconds` 기본 14400 · `alarm_worker.py:1710`) | 해소되지 않고 계속 들어오는 sev3 알람 1건이 **하루 최대 6번** PAGE된다 |
| dedup 상태는 워커 메모리(`self._gate_dedup`)에만 있다 | 워커가 재기동하면 열린 알람이 전부 처음 온 것처럼 다시 PAGE된다(D-318 주의 ②가 남긴 「4시간 안 반복 PAGE」 의문과 맞는다) |
| 신호 수집 실패면 심각도 1 이상이 전부 PAGE다(step 5 · `:678`) | DB2 조회 3초 초과, 미등록 db_id, 제니퍼 알람(정책 경로 off일 때 `unavailable`)이 그대로 PAGE가 된다 |
| AI 심각도 상향은 `max(원 심각도, AI)`이고, 결정적 시그니처 스캔은 **메시지형이 아닌 알람에도** 돈다(`alarm_analyzer.py:228` — LLM 해석만 `is_message_alarm`으로 한정) | 상향이 켜져 있으면 sev2가 sev3가 돼 step 3 단락으로 간다. D-048.5 「메시지형 한정」과 범위가 다르다 |
| TICKET은 즉시 메시지가 아니라 일배치 요약 큐 + 화면 SSE다(`alarm_notifier.py:375` `_route_non_page_tier`) | 같은 사건의 두 번째 알림부터 TICKET으로 내려도 운영자는 첫 PAGE로 이미 깨어 있다. 놓치는 것은 없다 |

**LLM에 「PAGE가 필요하냐」를 묻지 않는다.** D-048(판단은 결정적) · D-318(LLM 신호 하나가 매트릭스를 일괄로 뒤집은 사고)과 같은 이유다. 재판정은 결정적 규칙으로 하고, 근거는 감사 레코드에 남긴다.

---

## 2. 현행 PAGE 생성 경로 (전수)

| # | 경로 | 위치 | 단계 라벨 | 비고 |
|---|---|---|---|---|
| P1 | 실효 심각도 3 단락 | `notification_policy.py:656` | `severity3` | 원 심각도 3 + AI 상향(2→3) 포함. 억제 단계 미경유 |
| P2 | 신호 수집 실패 보수화 | `:678` | `collection_failed` | `noise_ctx is None` 또는 `source=="unavailable"` · 심각도≥1 |
| P3 | 매트릭스 sev2×높음 | `_matrix_tier` `:360` | `matrix` | `NOISE_IMPORTANCE_VALUE_MAP_CSV` 비면 「높음」 없음(D-318 주의 ③) |
| P4 | 매트릭스 TICKET→PAGE 승격 | `:831` | `matrix` | 통보 정책 `notify` · LLM `actionable` · 변경 근접 |
| P5 | 정의 밖 심각도 | `_matrix_tier` `:365` | `matrix` | 심각도 4 이상 등 → 보수 PAGE |
| P6 | 앱 영향 승격 | `:856` · `notification_gate.py:118` | `matrix` | `app_impact_enabled` · APM fatal |
| P7 | 지문 dedup 재통보 | `alarm_worker.py:1670` | (P1~P6 중 하나) | 4h마다 · 재기동 시 초기화 · D-320 심각도 상승 우회(창당 최대 +2) |
| P8 | 사후 승격 | `alarm_worker.py:663` | 결정 레코드 아님(`type=late_promotion`) | 관제 PAGE 수에 안 잡힌다(`decision_store.aggregate`가 `type` 레코드 제외) |

관제 화면의 PAGE 수는 `decision_store.aggregate()`가 `type` 없는 결정 레코드 중 `tier=="page"`를 센 값이다. P7은 매번 새 결정 레코드를 쓰므로 반복 통보도 그대로 1건씩 더해진다.

---

## 3. 170건 가설과 판독 방법 (W0이 확정)

| 가설 | 판독 필드(결정 레코드) | 대응 규칙(§4) |
|---|---|---|
| H1 같은 지문이 4h마다 다시 PAGE | 24h PAGE 중 `fingerprint` 중복 수 · 지문별 PAGE 횟수 분포 | R1 |
| H2 워커 재기동 뒤 일괄 재PAGE | PAGE 시각 군집(분 단위 히스토그램) · 직전 같은 지문 PAGE와 간격 <4h | R1 + 레지스트리 복원 |
| H3 같은 호스트 다발 sev3 | `server_name`별 10~30분 창 PAGE 수 | R2 |
| H4 여러 호스트 동시 sev3(망·스토리지 등 공통 원인) | (`db_id`, `alarm_name`) 별 창 안 호스트 수 | R3 |
| H5 수집 실패 보수 PAGE | `stage=="collection_failed"` 수 · `db_id`·출처(제니퍼 여부)별 | R4 |
| H6 AI 상향 sev3 | `stage=="severity3"` 중 `signals.severity<3` · `signals.ai_severity==3` | R5 |
| H7 만성 알람(매일 같은 것이 뜬다) | 지문·알람명별 최근 7일 PAGE 일수 | R6 |
| H8 지문 불안정(알람명·자원명에 값이 들어가 dedup이 안 걸림) | 같은 `server_name`+`alarm_name` 접두에서 지문이 갈리는 비율 | W2 지문 정규화(조건부) |
| H9 시험 경로 혼입(`/alarm/analyze-test/raw`·목업 주입) | API 시험 경로도 같은 감사 파일에 쓴다(`src/api/routes/alarm.py:839`) · `noise_ctx` 미수집 → `collection_failed` · **수신 스트림에 없는 판정 레코드**로 식별(§10.3 `decision_without_raw`) | 운영 저장소 분리 확인 |

**관제 화면에서 지금 바로 볼 수 있는 것**: 퍼널의 단계별 PAGE 수(plans/112 드릴다운). `severity3` · `collection_failed` · `matrix` 세 숫자만 알아도 W1 우선순위가 정해진다.

---

## 4. 설계 — 마지막 관문 「PAGE 필요성 재판정」(`page_review`)

### 4.1 위치와 계약

- 도메인 순수 함수 `noise_gate/domain/page_review.py::review_page(decision, signals, config) -> NotificationDecision`. 입력 `decision.tier != "page"`면 그대로 돌려준다(PAGE만 다룬다 — 티어를 올리는 일은 없다).
- 호출은 `notification_gate_node`에서 `decide_notification`(+ 앱 영향 재판정) **뒤**, `store.record` **앞**에서 한 번 한다. 기존 15단계와 그 판정은 건드리지 않는다.
- 신호(`PageReviewSignals`)는 워커가 그래프 진입 전에 만든다. PAGE가 확정되면 그래프 뒤에서 레지스트리를 갱신한다(`_record_episode_tier` 전례 · `alarm_worker.py:398`).
- 새 단계 라벨 `STAGE_PAGE_REVIEW = "page_review"`(표시명 「PAGE 재판정」)를 `STAGE_ORDER` 맨 뒤에 둔다. 사유 접두 「PAGE 재판정」을 `_REASON_STAGE_PREFIXES`에 넣는다.

### 4.2 강등 규칙 — 하나라도 걸리면 PAGE → TICKET

| 규칙 | 조건 | 사유 문구(예) | 근거 evidence |
|---|---|---|---|
| **R1 열린 사건의 반복** | 같은 지문에 PAGE가 있고 그 뒤 해소가 없다(또는 해소 뒤 `reopen_quiet_minutes` 안에 재발) · 마지막 PAGE로부터 `reminder_hours` 미만 | 「PAGE 재판정 — 진행 중 사건 반복(최초 PAGE 09:12)」 | `first_page_ts` · `page_alarm_id` · `repeat_count` |
| **R2 같은 호스트 진행 중 사건** | 같은 호스트 키에 `host_window_minutes` 안의 PAGE가 있고 해소 전이다 · 이번 실효 심각도가 그 PAGE 이하 | 「PAGE 재판정 — 동일 호스트 사건(알람 X) 진행 중」 | `host_page_alarm_id` · `host_page_ts` |
| **R3 다수 호스트 동시 발생** | 같은 존(db_id)·같은 알람명이 `fanout_window_minutes` 안에 `fanout_page_max`대 넘게 PAGE됐다 | 「PAGE 재판정 — 동시 발생 N대 중 대표 외」 | `fanout_hosts` · `representative_alarm_id` |
| **R6 만성 알람**(G-2에서 채택 시) | 같은 지문(또는 db_id+알람명)이 최근 7일 중 `chronic_days`일 이상 PAGE · 그 지문에 운영자 「유효」 피드백 없음 | 「PAGE 재판정 — 만성(7일 중 5일) · 임계 정비 대상」 | `page_days_7d` |

R4·R5는 재판정이 아니라 **앞 단계의 PAGE 생성 자체**를 고친다(§4.4).

### 4.3 보존 규칙 — 어떤 강등 규칙에도 우선한다

1. **새 사건의 첫 PAGE는 내리지 않는다** — 호스트에도 지문에도 열린 PAGE가 없으면 그대로 PAGE다.
2. **심각도 상승** — 열린 사건(지문·호스트)의 PAGE보다 실효 심각도가 높으면 PAGE.
3. **앱 영향** — `evidence.app_impact_fatal_events > 0`이면 PAGE.
4. **리마인더** — 해소 없이 `reminder_hours`(G-4 · 권고 24h)가 지나면 1회 다시 PAGE(「미해소 지속 N시간」 표기).
5. **운영자 지정 항상 PAGE** — `NOISE_PAGE_REVIEW_ALWAYS_PAGE_CSV`(알람명 글롭)에 걸리면 PAGE.

### 4.4 PAGE 생성 경로 정리 (G-3 · G-5)

- **R4 수집 실패**: step 5를 「심각도 3만 PAGE, 그 밖은 중요도 보통으로 매트릭스 진행(sev2 → TICKET)」으로 바꾼다. 수집 실패율이 창 안에서 임계를 넘으면 기존 메타경보(`meta_alerts`)에 `collection_failed_ratio`를 더해 **원인(DB 지연·미등록 db_id)을 고치게** 한다. 제니퍼 알람은 `apm_noise_policy_enabled` 운영 상태를 먼저 확인한다(off면 전부 수집 실패 경로다).
- **R5 AI 상향**(G-5 — W0 뒤 결정): 원 심각도 <3인데 LLM 해석·이상탐지만으로 3이 된 건은 step 3 단락에서 빼고 **원 심각도로 매트릭스를 탄다**(sev2×보통 = TICKET). AI 상향은 판정이 아니라 근거(evidence)로만 남긴다 — 승격 신호로 넣으면 sev2×보통이 다시 PAGE가 되어 효과가 없다. 결정적 시그니처(OOM·커널 패닉·FS 오류 등)로 오른 건은 현행 단락을 유지한다. 시그니처 스캔을 메시지형 알람으로 좁힐지는 W0 실측 뒤 정한다(D-048.5 문구와 실제 범위 차이).

### 4.5 워커 상태 — `PageRegistry`

- 메모리 dict 3개: 지문 → {첫/마지막 PAGE 시각·심각도·알람 id·해소 시각} · 호스트 키 → 열린 PAGE 목록 · (db_id, 알람명) → 창 안 PAGE 호스트 집합. 창 밖 항목 sweep · 키 상한(Known Mistakes 「데몬 in-memory dict는 키 만료 sweep」).
- **기동 시 복원**: `decision_store` 꼬리에서 최근 `max(reminder_hours, 24h)`의 PAGE·해소 레코드를 읽어 레지스트리를 다시 채운다. H2(재기동 일괄 재PAGE)를 막는 핵심이다. 실패하면 빈 레지스트리로 시작하고 경고 1줄을 남긴다(보수 = 현행 동작).
- 지문 dedup(`_gate_dedup`)은 그대로 둔다. R1은 dedup을 통과한 재통보를 TICKET으로 내리는 것이라, LLM 분석 비용은 줄지 않지만 기록과 화면 표시가 남는다. dedup 창을 늘리는 방식(설정만 변경)은 **해소 뒤 진짜 재발도 같이 버려서** 택하지 않는다(현행 dedup은 해소 때 지워지지 않는다).

### 4.6 모드와 플래그

| 플래그(`NOISE_` 접두) | 기본 | 뜻 |
|---|---|---|
| `PAGE_REVIEW_MODE` | `off` | `off`=평가 안 함(비트 동일) · `shadow`=판정 불변, `stage_evidence.page_review`에 「했을 강등」만 기록 · `enforce`=TICKET으로 강등 |
| `PAGE_REVIEW_HOST_WINDOW_MINUTES` | 30 | R2 창 |
| `PAGE_REVIEW_FANOUT_WINDOW_MINUTES` · `_FANOUT_PAGE_MAX` | 10 · 1 | R3 창 · 대표 수 |
| `PAGE_REVIEW_REMINDER_HOURS` | 24 | 보존 4 |
| `PAGE_REVIEW_REOPEN_QUIET_MINUTES` | 30 | R1 해소 뒤 같은 사건으로 보는 시간 |
| `PAGE_REVIEW_CHRONIC_DAYS` | 0(끔) | R6 — G-2 채택 시 5 권고 |
| `PAGE_REVIEW_ALWAYS_PAGE_CSV` | 빈 값 | 보존 5 |

R4·R5는 D-048 판정 자체를 바꾸므로 별도 플래그 `NOISE_COLLECTION_FAILED_PAGE_SEV3_ONLY` · `NOISE_AI_BOOST_LLM_NO_SHORT_CIRCUIT`(기본 off)로 둔다. shadow에서는 「했을 판정」만 남긴다.

---

## 5. 작업 단계 (Wave)

| Wave | 내용 | 산출물 | 완료 기준 |
|---|---|---|---|
| **W0 분류 정책 분석 로그** (선행 · 외부망 구현 → 내부망 실행 · **상세 §10**) | `noise_gate/scripts/page_audit.py` + `page_audit_sanitize.py` — 수신 원문 스트림(읽기 전용)과 판정 감사 기록을 알람 단위로 맞대어, 현재 분류 정책이 알람마다 어떻게 판정했는지를 **비식별 분석 로그**로 남기고 §3 H1~H9 판독표·규칙별 「했을 PAGE 수」를 집계한다 | 스크립트 2 + 테스트 + 픽스처 | §10.9 |
| **W1 재판정 도메인·워커** | `domain/page_review.py`(R1~R3·R6·보존 1~5) · `PageRegistry` + 기동 복원 · 게이트 노드 호출 · 단계 라벨·설명·사유 접두 · 설정 7종(`src/config.py` · `.env.example`) | 코드 + 테스트 | mode off에서 기존 스냅샷·정책 테스트 비트 동일 · 규칙·보존 단위 테스트 · 재기동 복원 테스트 |
| **W2 생성 경로 정리** (G-3·G-5 확정분만) | R4 수집 실패 · R5 LLM 상향 · (H8 실측 시) 지문 정규화 | 코드 + 테스트 | 플래그 off 비트 동일 · on 시 기대 티어 |
| **W3 관제 화면·매뉴얼** | KPI 「PAGE(재판정 후)」·「했을 PAGE」(shadow) · 퍼널 `page_review` 단계 · 드로어 근거(최초 PAGE 링크) · `src/static/js/noise-help.js` · 매뉴얼 admin(D-255) · `docs/28_noise_cancellation_operator_guide.md` | 화면 + 매뉴얼 | `pytest tests/test_manual` · 화면 캡처 |
| **W4 내부망 shadow → enforce** | 24h shadow → 「했을 PAGE」 ≤ 20 · 강등 건 전수가 유지된 PAGE에 묶임(놓친 사건 0) 확인 → enforce | 운영 결과 반출 | 목표 달성 + 사용자 승인 |

W0 결과로 규칙별 효과가 작으면 해당 규칙은 W1에서 뺀다(만들지 않는다).

---

## 6. 결정 기록

- **D-330 등재 완료(2026-10-14 · 확정 — 구현 전)** — 「PAGE는 새 사건의 첫 알림」 원칙 · `page_review` 규칙·보존 규칙 · 모드 사다리 · 수집 실패 sev3만 PAGE · 리마인더 24h · 강등 목적지 TICKET · W0 분석 로그 비식별 규칙(§10.4).
- **D-035 · D-048 부분 개정 부기 완료** — 원 문구는 이력으로 남기고, 효력은 W1(`page_review` enforce)·W2(R4) 구현 시점부터라고 적었다. 구현 시 각 파일에 「효력 발생」 부기를 한 줄 더 단다.
- 남은 부기(구현 때): D-048.5(G-5가 R5 채택이면) · D-320(심각도 상승 우회 = 보존 규칙 2와 같은 원칙).

---

## 7. 사용자 확정 게이트 (2026-10-14 — 「권고에 맞게 정리」 · 권고안 그대로 확정)

| # | 질문 | 확정 |
|---|---|---|
| **G-1** | 「심각도3 절대 PAGE」(D-035·D-048)를 「새 사건의 첫 sev3만 PAGE, 같은 사건의 반복·같은 호스트·동시 다발은 TICKET으로 묶음」으로 개정하는가 | **개정 확정**(D-330 ①) — 이게 없으면 10~20건이 나오지 않는다(sev3 단락만 D-318 때 33건). 효력은 W1 enforce부터 |
| **G-2** | 만성 알람(R6)도 강등하는가 | **W0 결과 뒤 결정**(권고 그대로) — W0 §10.6 S7·S8의 R6 단독 효과를 보고 정한다. 채택 시 운영자 「유효」 피드백 지문은 제외 |
| **G-3** | 수집 실패 보수 PAGE(R4)를 「sev3만 PAGE」로 바꾸는가 | **변경 확정**(D-330 ③) + 수집 실패율 메타경보 |
| **G-4** | 미해소 리마인더 간격 | **24h 확정**(D-330 ②) |
| **G-5** | LLM 해석만으로 오른 sev3(R5)를 단락에서 빼는가 | **W0 결과 뒤 결정**(권고 그대로) — W0 §10.6 S2의 상향 출처 분해(시그니처/LLM·이상탐지) 건수로 정한다 |
| **G-6** | 강등 목적지 | **TICKET 확정**(D-330 ④ · 일배치 요약 + 화면 실시간) · 첫 PAGE 카드에 「묶인 알림 N건」 표시 |

---

## 8. 검증

- 도메인: 규칙별(R1~R3·R6) 강등 · 보존 1~5 각각이 강등을 막는지 · mode off/shadow/enforce 티어 · 입력 TICKET 이하 무변경.
- 워커: 레지스트리 sweep·상한 · 해소 시 열린 사건 닫힘 · 재기동 복원(결정 JSONL 꼬리) · 복원 실패 시 빈 상태.
- 회귀: 기존 `noise_gate/tests/test_notification_policy.py`·스냅샷 픽스처는 **mode off에서 그대로 통과**해야 한다(픽스처 재생성 금지). 단계 수를 단언하는 테스트(`STAGE_ORDER` 길이·퍼널 키)는 repo 전체 grep으로 찾아 갱신한다.
- 리플레이: `replay_noise.py`에 `--page-review-mode` 추가 · W0 스크립트와 같은 수치가 나오는지 대조.
- 정적 게이트: `arch_check`(도메인은 stdlib만) · `overfit_check`(독스트링 스키마 리터럴 주의 — D-179).
- 회귀 실행: 계획 끝에 `python scripts/regress.py --base 4073f7f --files <바꾼 파일…>` 1회(D-303).

---

## 9. 위험과 대응

| 위험 | 대응 |
|---|---|
| R2·R3가 서로 다른 장애를 한 사건으로 묶어 두 번째 장애를 늦게 안다 | 보존 2(심각도 상승) · 창을 30분/10분으로 짧게 · TICKET도 화면 실시간 표시 · W4에서 강등 건 전수 검토 |
| 레지스트리 복원이 틀려 첫 PAGE를 반복으로 오판 | 복원은 PAGE·해소 레코드만 · 실패 시 빈 상태(= 현행) · shadow로 먼저 확인 |
| 170건의 대부분이 이 계획 밖 경로(예: 시험 경로 혼입·지문 불안정) | W0이 먼저다 — 규칙을 만들기 전에 분해한다 |
| 관제 수치 의미 변화(「PAGE」가 재판정 후 값) | 화면에 「재판정 전/후」 둘 다 표시 · 매뉴얼 갱신 |

---

## 10. W0 상세 — 분류 정책 분석 로그 스크립트 (`page_audit`)

### 10.1 목적과 경계

- **묻는 것**: 「지금 정책이 알람마다 어떻게 판정했고, PAGE 170건은 어느 경로·어떤 반복에서 나왔으며, §4 규칙을 적용하면 몇 건이 되는가」. G-2·G-5와 W1 규칙 선택이 이 결과로 정해진다.
- **별도 스크립트다** — 제품 코드(워커·게이트·도메인)는 한 줄도 바꾸지 않는다. 운영 워커와 같은 프로세스에서 돌지 않고, 내부망 서버에서 사람이 한 번씩 실행한다.
- **읽기 전용** — 판정 감사 파일은 읽기만 한다. Redis는 `XRANGE`·`XINFO`·`XPENDING`만 쓴다(소비자 그룹 생성·`XREADGROUP`·`XACK`·쓰기 명령 금지 — 운영 워커의 소비 위치를 건드리지 않는다). DB·LLM·게이트웨이는 부르지 않는다.
- **산출물은 비식별 분석 로그와 집계뿐이다** — IP·호스트명·서버명·자원명·알람 id·로그 원문·AI 서술은 내보내지 않는다(§10.4). 반출을 전제로 만든다.

### 10.2 입력 3종 + 정책 스냅샷

| 입력 | 읽는 법 | 쓰임 |
|---|---|---|
| 판정 감사 `logs/alarm_decisions.jsonl` | 파일 순차 읽기 · `--hours`(기본 24)·`--days` 창으로 거름 · 파일은 회전되지 않는다(`decision_store_max_lines`는 화면 응답 상한일 뿐) | 판정 레코드(`type` 없음) · 재발 억제(`type=recurrence`) · 해소(`type=resolution`) · 사후 승격(`type=late_promotion`) |
| 수신 원문 스트림 `alarm:raw` | `XRANGE` 페이지 단위(1,000건) · 스트림 id 시각으로 창 거름 · `MAXLEN` 없이 쌓이므로 7일 창도 가능 | 받은 알람 전수 — 판정 레코드가 없는 알람의 **처분 사유**를 가린다 · 잘리지 않은 `conditionLog`로 시그니처 라벨 산출 |
| dead-letter `alarm:dead` | `XRANGE` | 파싱 실패로 버려진 건 |
| 정책 스냅샷 | `AppConfig()`로 실제 `.env` 해석값 읽기(D-127 「코드 기본값이 아니라 실제 `.env`」) | 이번 판정을 만든 정책(플래그·창·임계) 기록 · 소비자 그룹 지연(`XINFO GROUPS`·`XPENDING` 요약) |

`--no-stream`이면 감사 파일만으로 돈다(처분 사유·시그니처 라벨은 「모름」으로 남는다).

### 10.3 처리 흐름

```
읽기(감사 · 스트림 · dead) → alarm_id로 맞대기 → 처분 분류 → 파생 특징 계산
  → 비식별 변환(허용 목록) → 알람 단위 분석 로그 → 집계 S1~S10 → what-if(§10.7)
  → 누출 검사(실패 시 아무것도 쓰지 않음) → 파일 쓰기
```

**처분(disposition)** — 수신 알람 1건마다 하나:

| 값 | 판별 |
|---|---|
| `decided` | 같은 `alarm_id`의 판정 레코드 있음 → `tier`·`stage` |
| `dedup_fingerprint` | 같은 `alarm_id`의 `type=recurrence` 있음(지문 dedup이 버림) |
| `below_min_severity` | `1 ≤ severity < ALARM_MIN_SEVERITY` |
| `dead_letter` | `alarm:dead`에 같은 id |
| `pending` | 스트림 id가 소비자 그룹 `last-delivered-id` 뒤(아직 처리 전) |
| `unaccounted` | 위 어디에도 없음(워커 중단·게이트 off 구간의 alarm_id dedup 등) |
| `decision_without_raw` | 판정 레코드는 있는데 수신 스트림에 없음 → **API 시험 경로·수동 주입**(H9) |

### 10.4 비식별 규칙 — 허용 목록 방식

**원칙: 내보낼 필드를 하나씩 정해 두고, 그 밖의 필드는 전부 버린다(차단 목록이 아니라 허용 목록).** 원문 필드가 새로 생겨도 자동으로 새어 나가지 않는다.

| 원천 필드 | 처리 | 출력 |
|---|---|---|
| `ipAddress` · `hostname` · `resourceAncestry` 원문 · `alarm_id` · `raw_payload` · `analysis`(AI 서술) · `reason` 원문 · `condition_log` 원문 | **버림** | — (`resourceAncestry`는 단계 수 `ancestry_depth`만) |
| `server_name`(없으면 `hostname`) | 실행 단위 무작위 키(`secrets.token_bytes(32)`)의 HMAC-SHA256 앞 8바이트 → `H-1a2b3c4d` | `host` |
| `resource_name` | 같은 방식 → `R-…` | `resource` |
| `fingerprint`(SHA-1 — 서버 목록으로 역산 가능) | 같은 방식으로 다시 해시 → `F-…` | `fp` |
| `alarm_name` | 정규화: 그 알람 자신의 서버명·호스트명·IP·자원명 문자열 → `<HOST>`·`<RES>` · IPv4/IPv6/MAC/이메일/URL → `<IP>`·`<MAC>`·`<EMAIL>`·`<URL>` · 영숫자 혼합 식별자형 토큰(글자+숫자 · 5자 이상 · `-`/`.` 포함 가능) → `<ID>` · 3자리 이상 숫자 → `<N>` · 60자 절단 | `alarm_name_norm` + 원문의 키 해시 `alarm_def`(`N-…` · 정규화로 합쳐진 이름이 몇 개였는지 보려고 — H8) |
| `conditions`(임계 조건) | `alarm_name`과 같은 정규화(2자리 이하 숫자는 남긴다 — 임계값 90 등) · 80자 | `conditions_norm` |
| `condition_log` | 원문은 버리고 특징만: 시그니처 라벨(`scan_signature_severity` — 고정 어휘) · 메시지형 여부(`is_message_alarm`) · 길이 구간 | `sig_label` · `is_message` · `cond_len_bucket` |
| `resource_type` | 정규화 · 40자 | `resource_type` |
| `db_id` · 존 라벨 · 출처(폴스타/제니퍼) | 그대로(식별 정보 아님) | `db_id` · `zone` · `source` |
| 시각 | 초 단위 KST | `recv_ts`(스트림) · `decision_ts` · `lag_ms` |
| `signals` | 열거·bool·정수라 그대로 · `root_resource`만 `R-…` | `signals` |
| `stage_evidence` | 허용 키만: `base_tier`·`promote`·`demote`·`app_impact_fatal_events`·`mode`·`root_notified`·`heal_seconds`·`fired_severity`·`markers`·`capped_from`·플래핑/스톰 수치·`would_demote`·`apm_policy.event_type_norm`·`apm_policy.persistence` 수치·`cross_source.rule_id`·`lag_seconds` | `evidence` · 버린 키는 **이름만** 요약 `dropped_evidence_keys`에 센다 |
| `recurrence` · `correlation_meta` | `count`·창(초) · `member_seq`·`similarity`(대표 지문은 `F-…`) | `recurrence` · `correlation` |

- **무작위 키는 어디에도 쓰지 않는다** — 대응표가 없으므로 실행이 끝나면 가명을 되돌릴 수 없다(D-321 「`secrets` 난수만 · 대응표 없음」 전례). 같은 실행 안에서는 같은 서버가 같은 가명이라 군집 분석은 된다. 실행끼리는 가명이 다르다 — 24h와 7일은 **한 번의 실행**으로 함께 낸다(`--hours 24 --days 7`).
- **누출 검사(실패하면 닫힌다)** — 쓰기 직전에 출력 전체 문자열을 검사한다: ① IPv4·IPv6·MAC·이메일 정규식 ② 이번 실행에서 본 **모든 원문 식별자 문자열**(서버명·호스트명·IP·자원명 4자 이상·alarm_id)의 부분 일치. 하나라도 걸리면 `leak_check.json`(걸린 출력 경로와 건수만 — 값 없음)만 쓰고 종료 코드 2로 끝난다. 분석 파일은 하나도 남기지 않는다.
- **정책 스냅샷도 허용 목록** — `NoiseGateConfig`·`AlarmConfig`에서 bool·int·float·Literal 필드와 지정한 열거형 문자열(`cross_source_mode`·`enrichment_min_tier`·`investigation_trigger_min_tier`·`agentic_enricher_fallback`)만 싣는다. 이름에 `url`·`token`·`secret`·`key`·`user`·`path`가 들어간 필드는 값 없이 「제외」 목록에 이름만 남긴다. CSV 필드는 `설정됨: N항목`으로 · 중요도 맵만 코드→라벨 그대로(식별 정보 아님).

### 10.5 분석 로그 — `alarms.jsonl` 한 줄(수신 알람 1건)

```json
{"seq": 812, "recv_ts": "2026-10-14T09:12:03+09:00", "decision_ts": "…", "lag_ms": 2140,
 "db_id": "polestar_cm_gp", "zone": "gongjon", "source": "polestar",
 "host": "H-1a2b3c4d", "resource": "R-9f8e7d6c", "resource_type": "Filesystem", "ancestry_depth": 3,
 "alarm_name_norm": "파일시스템 사용률 임계 초과", "alarm_def": "N-55aa…", "alarm_kind": "disk",
 "conditions_norm": "사용률 >= 90", "severity": 3, "is_clear": false,
 "sig_label": null, "is_message": false, "cond_len_bucket": "32-127",
 "disposition": "decided", "tier": "page", "stage": "severity3",
 "signals": {"severity": 3, "ai_severity": null, "effective_severity": 3, "importance": "보통", "…": "…"},
 "noise_ctx_source": "polestar_db", "evidence": {"base_tier": null},
 "fp": "F-77c1…", "fp_page_seq": 3, "since_prev_fp_page_s": 14412, "since_prev_host_page_s": 95,
 "fanout_hosts_10m": 7, "clear_after_s": null, "fp_page_days_7d": 6,
 "boost_origin": null, "within_dedup_window_repage": false,
 "whatif": {"R1": "demote", "R2": "keep", "R3": "demote", "R4": "n/a", "R5": "n/a", "R6": "demote", "cumulative": "demote:R1"}}
```

파생 필드: `fp_page_seq`(이 지문의 창 안 몇 번째 PAGE) · `since_prev_fp_page_s` · `since_prev_host_page_s` · `fanout_hosts_10m`(같은 db_id·`alarm_def` 10분 안 PAGE 호스트 수) · `clear_after_s`(같은 지문 다음 해소까지) · `fp_page_days_7d` · `boost_origin`(`signature:<라벨>` / `llm_or_anomaly` / `null`) · `within_dedup_window_repage`(직전 같은 지문 PAGE로부터 dedup 창 안인데 심각도 상승 없이 다시 PAGE — **재기동·지문 흔들림의 흔적**, H2·H8) · `noise_ctx_source`(`polestar_db`/`cache`/`unavailable`/`apm_policy`/없음).

### 10.6 집계 — `summary.json` · `summary.md`(한국어 표)

| # | 섹션 | 내용 |
|---|---|---|
| S0 | 실행 정보 | 창 · 입력 건수 · 코드 SHA(가능하면) · `notification_policy.py` 해시 · 정책 스냅샷 요약(켜진 단계 목록) |
| S1 | 수신·처분 | 수신 건수(심각도·db_id·출처별) · 처분 7종 건수 · 처리 지연 p50/p95 · `decision_without_raw`(H9) |
| S2 | 티어×단계 | 전 티어 × 15단계 표 · **PAGE 단계별** · sev3 단락의 상향 출처(원 sev3 / 시그니처 라벨별 / LLM·이상탐지 — H6 · G-5 판단 근거) · 수집 실패의 `noise_ctx_source`·db_id·출처별(H5) · 매트릭스 PAGE의 승격 사유별 |
| S3 | 반복 | 지문당 PAGE 횟수 분포 · 재PAGE 간격 분포 · `within_dedup_window_repage` 건수(H1·H2) · 상위 20 지문(가명·정규화 알람명·db_id·횟수) |
| S4 | 군집 | 30분 안 PAGE ≥2 호스트 수와 그 PAGE 합(H3) · 10분 안 같은 알람 다수 호스트 군집 수·크기 분포(H4) |
| S5 | 시간 | 시간대별 PAGE · 분당 PAGE ≥5 폭주 구간(재기동 의심 — 직전 판정 공백 길이와 함께) |
| S6 | 해소 | PAGE 지문의 해소까지 시간: <5분 · <15분 · <1시간 · 미해소 |
| S7 | 만성 | 최근 7일 중 PAGE 일수 분포 · 5일 이상 지문 수와 PAGE 합(H7 · G-2 판단 근거) |
| S8 | what-if | 규칙별 단독 · 누적 「했을 PAGE 수」(24h·7일 일평균) · 규칙별 강등 건의 원 단계 분포 · 묶음 검증(강등 건 중 대표 PAGE가 없는 고아 = 0이어야 함) |
| S9 | 지문 안정성 | 같은 호스트·`alarm_name_norm`에 지문 2개 이상인 묶음 수 · 정규화로 합쳐진 `alarm_def` 수(H8) |
| S10 | 데이터 품질 | 단계 라벨 없는 옛 레코드 · AI 분석 실패 판정 수 · 버린 근거 키 이름별 건수 · 정책 스냅샷 제외 필드 목록 |

### 10.7 what-if 규칙 (스크립트 안 시뮬레이션)

판정 레코드를 시각 순으로 한 번 훑으며 「남은 PAGE(대표)」 상태를 만든다. 생성 경로 규칙(R4·R5)을 먼저, 재판정 규칙(R1→R2→R3→R6)을 그 뒤에 적용하고, 보존 규칙(§4.3)이 모든 강등에 우선한다.

| 규칙 | 시뮬레이션 정의 | 한계 |
|---|---|---|
| R4 | `stage=collection_failed` · 실효 심각도 <3 → `_matrix_tier(심각도, 보통)` | 실제 신호가 수집됐다면 중요도·유지보수가 달랐을 수 있다 |
| R5 | `stage=severity3` · 원 심각도 <3 · `boost_origin=llm_or_anomaly` → 원 심각도 매트릭스 | `--no-stream`이면 시그니처 판별은 감사 파일의 200자 절단 로그로 한다(「모름」 비율을 따로 센다) |
| R1 | 같은 지문 대표 PAGE가 있고 그 뒤 해소 없음(또는 해소 뒤 30분 안 재발) · 대표로부터 24h 미만 · 심각도 상승 없음 | 지문 dedup이 버린 알람은 원래 판정 레코드가 없어 대상 밖(이미 PAGE 아님) |
| R2 | 같은 호스트 대표 PAGE가 30분 안 · 그 지문 미해소 · 실효 심각도 ≤ 대표 | 호스트 키는 등록 서버명 기준(D-188 역조회 실패분은 호스트명 — S9가 흔들림을 보여 준다) |
| R3 | 같은 db_id·`alarm_def` 대표 PAGE가 10분 안 다른 호스트에 있음(대표 1대) | — |
| R6 | 최근 7일 중 PAGE 일수 ≥5(직전 7일 데이터가 있어야 함 — 부족하면 「판정 불가」) | 운영자 「유효」 피드백 제외는 피드백 파일을 읽어야 해 1차 범위 밖(건수만 표시) |
| 보존 | 앱 영향 fatal>0 · 심각도 상승 · 24h 리마인더 · (항상 PAGE 목록은 빈 값으로 시뮬레이션) | — |

W1에서 도메인 `review_page`가 생기면 스크립트는 자체 시뮬레이션 대신 그 함수를 불러 같은 결과를 내도록 바꾼다(규칙 정의의 출처를 하나로).

### 10.8 파일·CLI·출력

| 파일 | 역할 |
|---|---|
| `noise_gate/scripts/page_audit.py` | CLI · 읽기(감사·스트림 읽기 전용 래퍼) · 맞대기·처분 · 파생 특징 · 집계 S0~S10 · what-if · `summary.md` 렌더 |
| `noise_gate/scripts/page_audit_sanitize.py` | 허용 목록 변환 · HMAC 가명 · 이름 정규화 · 누출 검사 · 정책 스냅샷 허용 목록 |
| `noise_gate/tests/test_page_audit.py` · `noise_gate/testdata/page_audit/` | 테스트 · 가짜 원문 스트림/감사 픽스처(IP는 문서용 대역 `192.0.2.0/24`·`198.51.100.0/24`) |

```
python -m noise_gate.scripts.page_audit --dry-run                 # 입력 위치·건수·창만 출력(쓰기 없음)
python -m noise_gate.scripts.page_audit --hours 24 --days 7       # 기본: 감사 + alarm:raw + alarm:dead
python -m noise_gate.scripts.page_audit --hours 24 --no-stream    # Redis 없이 감사 파일만
    [--decisions logs/alarm_decisions.jsonl] [--out results/page_audit] [--summary-only]
```

- 출력: `results/page_audit/<YYYYMMDD-HHMMSS>/` 아래 `summary.json` · `summary.md` · `policy_snapshot.json` · `leak_check.json` · `alarms.jsonl`(`--summary-only`면 생략). `results/`는 git 추적 밖이다(`.gitignore`).
- Redis 접속은 `AppConfig().redis` 설정을 쓴다. 래퍼 클래스가 `xrange`·`xinfo_groups`·`xpending`만 노출해 쓰기 명령을 코드 수준에서 막는다.
- 의존은 stdlib + `redis`(이미 설치) + `noise_gate.domain`(시그니처 스캔·지문·매트릭스 순수 함수) + `noise_gate.application.server_identity.is_apm_source`. `src/`는 `AppConfig` 하나만 읽는다(`replay_noise.py`와 같은 높이).

### 10.9 테스트·완료 기준

1. **누출 0** — 픽스처의 서버명·호스트명·IP·자원명·alarm_id·로그 원문이 출력 5개 파일 어디에도 없다(문자열 전수 검사). 알람명 안에 IP·서버명을 일부러 넣은 행도 정규화된다.
2. **누출 검사가 닫힌다** — 허용 목록을 우회해 원문을 넣은 가짜 변환기로 돌리면 종료 코드 2 · 분석 파일 0개 · `leak_check.json`에 값이 없다.
3. **가명** — 같은 실행 안에서 같은 서버 = 같은 가명 · 실행이 다르면 다른 가명 · 키가 어떤 파일에도 없다.
4. **처분 7종** — 각각을 만드는 픽스처 행이 기대 처분으로 분류된다(특히 `decision_without_raw`).
5. **파생·집계** — 손으로 만든 시퀀스(같은 지문 4h 간격 3회 · 같은 호스트 5분 안 3건 · 10분 안 7호스트 · 재기동 폭주)에서 S3·S4·S5·S8 수치가 기대값과 같다. what-if 고아 0.
6. **정책 스냅샷** — `apm_mcp_url`·`investigation_service_token`·`l3_ssh_user`·경로 필드 값이 없고 이름만 제외 목록에 있다.
7. **읽기 전용** — Redis 래퍼에 쓰기 메서드가 없고, 가짜 Redis로 돌린 뒤 쓰기 호출 0.
8. **정적 게이트** — `python scripts/regress.py --no-tests --files <위 4개>` 통과 · 자기 테스트 `pytest noise_gate/tests/test_page_audit.py` 통과.

### 10.10 내부망 실행·반출 절차

1. 반입: 위 스크립트 2개(제품 코드 변경 없음 — 워커 재기동 불필요).
2. `--dry-run`으로 입력 위치·건수 확인 → `--hours 24 --days 7` 실행.
3. `leak_check.json`의 `passed: true` 확인 · `summary.md`를 눈으로 한 번 훑는다.
4. 반출: `summary.json` · `summary.md` · `policy_snapshot.json` · `leak_check.json`. `alarms.jsonl`은 집계로 판단이 안 서는 경우에만(비식별이지만 건수가 많다).
5. 외부망에서 S2·S7·S8로 G-2·G-5를 정하고, 효과가 작은 규칙은 W1에서 뺀다.

### 10.11 하지 않는 것

- 운영 워커에 로그를 더하지 않는다(별도 스크립트 원칙 · 판정 회귀 0).
- 판정을 다시 계산해 감사 파일에 쓰지 않는다 — 결과는 스크립트 출력 폴더에만 남는다.
- LLM으로 요약하지 않는다(실 LLM 0 · 과금 0).
- 지문 dedup이 버린 알람을 다시 판정하지 않는다(처분 건수로만 센다).
