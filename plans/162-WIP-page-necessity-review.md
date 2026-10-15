# 162. 노이즈 게이트 PAGE 필요성 재판정 — 24시간 PAGE 170건을 10~20건으로

> **작성일**: 2026-10-14 · **v1.1**(2026-10-14 — 게이트 권고안 확정 · W0 분석 스크립트 상세 계획 §10 추가) · **v1.2**(2026-10-15 — W0-X 폴스타↔APM 상관 진단 계획 §11 추가 · 게이트 G-7~G-9 신설(진단 뒤 결정)) · **v1.3**(2026-10-15 — 내부망 W0 1회차 결과 분석 · 수정 계획 §12 · 게이트 G-10~G-12 신설 · G-2·G-5 권고 갱신)
> **상태**: **WIP — W0~W3 구현 완료(2026-10-14 · 커밋 `bc496a5`·`badb798`)** · **W0 1회차 내부망 실행(2026-10-15 · §12) — 24h PAGE 227 중 제니퍼 sev3 단락 203 · 현행 R1~R3로는 48(목표 밖) · G-10 (a) 확정(D-336) · G-2 미채택 · G-5 보류** · **W5 구현 완료(2026-10-15 · 커밋 없음 · `NOISE_APM_SEV3_POLICY_ENABLED` 기본 off = 비트 동일 · 정책 경로 sev3 `notify: page`만 PAGE · 그 밖 TICKET)** · **다음은 W0-T(W0-X 뒤) · W0-H 내부망 진단 · 2회차 F2 키 대조 뒤 W5 켜기 판단** · W0 `noise_gate/scripts/page_audit.py`·`page_audit_sanitize.py`(비식별 허용 목록·HMAC 가명·누출 검사·what-if · R1~R3는 도메인 `review_page` 호출로 W1과 차등 0) · W1 `noise_gate/domain/page_review.py`+`page_review` 단계(`NOISE_PAGE_REVIEW_MODE` 기본 off = 비트 동일) · W2 R4 `NOISE_COLLECTION_FAILED_PAGE_SEV3_ONLY`(기본 off)·수집 실패율 메타경보 · W3 관제 화면·매뉴얼·docs/28 §4-C · **잔여**: W4 내부망 W0 실행 → shadow 24h → enforce 판정 · G-2(R6 만성)·G-5(R5) 결정 · `replay_noise.py --page-review-mode`(§8) 미구현 · shadow/enforce 화면 캡처 · 사용자 경로 대표 PAGE 포인터 마스킹(운영 alarm_id 형식 실측 뒤) · R6 보존 1·2·4 미적용 이탈(W0 시뮬 — G-2 때 부기) · **W0-X 상관 진단 구현 완료(§11 · 2026-10-15 · 커밋 없음 · 기준 `9ccc44f`)** — `page_audit_xsource.py`(행 `xs` · S11~S14 · V0~V3 · 판독표 `xs_readout`) · 잔여: 내부망 2회차 실행으로 A/B/C 판독 → G-7~G-9 결정 · 판독 문턱은 해석값 상수 · ③ V0=replay는 기록 티어=재판정 티어 부분 집합에서만(§11.8 부기)
> **회귀 대상 파일**(D-303 · 계획 끝 1회 `regress.py --base 4073f7f`): `.env.example` · `config/settings_help/noise_gate_core.yaml`·`noise_gate_notify.yaml` · `config/apm_noise_policy.yaml`(주석) · `noise_gate/application/alarm_worker.py`·`nodes/notification_gate.py` · `noise_gate/domain/notification_policy.py`·`stage_handling.py`·`page_review.py` · `noise_gate/infrastructure/decision_store.py` · `noise_gate/orchestration/alarm_graph.py` · `noise_gate/scripts/page_audit.py`·`page_audit_sanitize.py`·`page_audit_xsource.py` · `noise_gate/testdata/page_audit/xsource/` · `src/config.py` · `src/api/routes/alarm.py`·`noise_dashboard.py` · `src/api/settings_catalog.py` · `src/static/{noise.html,admin/noise.html,css/noise-console.css,js/noise.js,js/noise-help.js}` · `scripts/manual/{features.yaml,content/admin.md,content/user.md}` · `src/static/manual/{admin.html,user.html,help_index.json}` · 테스트 `noise_gate/tests/test_{page_audit,page_audit_verify,page_audit_xsource,page_audit_xsource_verify,page_review,page_review_verify,collection_failed_sev3_only,collection_failed_verify,decision_snapshot,decision_stage,plan153_w1_analysis_record,apm_sev3_policy,apm_sev3_policy_verify,plan144_apm_policy}.py` · `tests/test_api/test_{noise_page_review_console,noise_page_review_console_verify,noise_health_collection_failed,noise_dashboard_api,plan153_w1_console_explain,settings_catalog}.py`
> **계획 시작 SHA**: `4073f7f`
> **요청 원문(2026-10-14)**: *"노이즈 관제에서 아직도 너무 많은 page가 있다. 24시간동안 현재 170 정도인다. 10~20개 내외가 되어야 한다. page가 꼭 필요한지 다시 판단하도록 해야 한다. 현재 코드를 다시 분석하여 개선 계획을 수립하라."* → v1.1: *"권고에 맞게 정리하고 별도의 테스트 스크립트를 통해 현재 알람의 분류 정책을 로깅하여 분석할 수 있는 정보를 취합하는 코드 작성 계획을 정리하라. 로그 정보에는 ip 등의 중요 정보는 제외시키고 최대한 분석할 수 있는 정보 위주로 정리되어야한다."* → v1.2(2026-10-15): *"현재 폴스타와 apm이 상관관계를 제대로 역지 못하는 것으로 보인다."* · *"진단 섹션 추가 계획을 162 에 추가 수립하라."*
> **관련 결정**: **D-035**·**D-048**(「심각도3 절대 PAGE」 · 수집 실패 보수 PAGE — **D-330으로 부분 개정 확정 · 효력은 W1·W2 구현 시**) · D-048.5(AI 상향 · 메시지형 한정) · D-318(`is_routine=False` 승격 제거 — 직전 감축) · D-320(dedup 심각도 상승 우회) · D-317(제니퍼·크로스소스 — §11 진단 대상) · D-329(제니퍼 소스 id 운영 확정값 — §11 A2 대조) · D-049(incident) · D-247(관제 화면 근거) · D-255(매뉴얼 동반) · D-303(회귀)
> **신규 결정**: **D-336**(확정 2026-10-15 — 제니퍼 심각도 3은 유형 정책 표 · `notify: page`만 PAGE · 그 밖 TICKET · G-10 (a)) · **D-330**(확정 2026-10-14 — PAGE = 새 사건의 첫 알림 · `page_review` · 수집 실패 sev3만 PAGE · 리마인더 24h · 강등 목적지 TICKET · W0 분석 로그 비식별 규칙) · D-321(반출 값 치환·IPv4 잔존 검사 전례)
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
| **W0-X 상관 진단** (외부망 구현 → 내부망 실행 · **상세 §11**) | W0 스크립트에 폴스타↔APM 상관 진단 S11~S14 추가 — 끊긴 지점을 A 설정 · B 이름 정합 · C 구조로 가른다 · 도메인 사건 추적기 시뮬레이션 V0~V3 | `page_audit_xsource.py` + 테스트 + 픽스처 | §11.8 |
| **W0-T 분석 도구 보정** (v1.3 · **상세 §12.5**) | 1회차 결과에서 드러난 `page_audit` 결함 6건(제니퍼 유형 가명화 · 해소·지문 안정성 소스 미분리 · 소비 공백 미표시 · 사건 id 미명명 · G-10 what-if 부재) | 스크립트 + 테스트 | §12.5 완료 기준 |
| **W0-H 소비 경로 진단** (v1.3 · 내부망 · **상세 §12.4**) | 수신의 59.7%가 판정·재발 기록 없이 사라진다(H10) — 같은 소비자 이름(`worker-1`)을 쓰는 다른 프로세스 여부 · 04시~08시 공백 원인 확인 | 내부망 확인 결과 | 원인 확정 → G-11 |
| **W5 제니퍼 sev3 정책 경로** (v1.3 · **G-10 확정 뒤** · **상세 §12.6**) | 제니퍼 심각도 3이 `apm_noise_policy.yaml` 유형 정책을 거치게 한다 — `notify: page` 유형만 PAGE, 그 밖은 TICKET 하한 · 정책 표 키 실측 정정 | 코드 + 테스트 + 정책 표 | 플래그 off 비트 동일 · on 시 기대 티어 · W0 what-if 대조 — **구현 완료(2026-10-15 · 커밋 없음 · D-336 · 키 정정·what-if 대조는 2회차 뒤)** |
| **W4 내부망 shadow → enforce** | 24h shadow → 「했을 PAGE」 ≤ 20 · 강등 건 전수가 유지된 PAGE에 묶임(놓친 사건 0) 확인 → enforce · **(v1.3) 전제: W0-H로 단일 소비 확인** — 소비가 갈려 있으면 레지스트리·지문 dedup이 프로세스마다 반쪽이라 shadow 수치가 틀린다 | 운영 결과 반출 | 목표 달성 + 사용자 승인 |

W0 결과로 규칙별 효과가 작으면 해당 규칙은 W1에서 뺀다(만들지 않는다).

---

## 6. 결정 기록

- **D-330 등재 완료(2026-10-14 · 확정 — 구현 전)** — 「PAGE는 새 사건의 첫 알림」 원칙 · `page_review` 규칙·보존 규칙 · 모드 사다리 · 수집 실패 sev3만 PAGE · 리마인더 24h · 강등 목적지 TICKET · W0 분석 로그 비식별 규칙(§10.4).
- **D-035 · D-048 부분 개정 부기 완료** — 원 문구는 이력으로 남기고, 효력은 W1(`page_review` enforce)·W2(R4) 구현 시점부터라고 적었다. 구현 시 각 파일에 「효력 발생」 부기를 한 줄 더 단다.
- 남은 부기(구현 때): D-048.5(G-5가 R5 채택이면) · D-320(심각도 상승 우회 = 보존 규칙 2와 같은 원칙).
- (v1.2) W0-X는 새 결정이 없다 — D-330 ⑥(분석 로그 비식별) 적용 범위를 상관 진단 필드(§11.4)로 넓힌다는 부기를 구현 때 단다. G-7~G-9가 정해지면 D-317에 부기하거나 새 D-번호로 등재한다.

---

## 7. 사용자 확정 게이트 (2026-10-14 — 「권고에 맞게 정리」 · 권고안 그대로 확정)

| # | 질문 | 확정 |
|---|---|---|
| **G-1** | 「심각도3 절대 PAGE」(D-035·D-048)를 「새 사건의 첫 sev3만 PAGE, 같은 사건의 반복·같은 호스트·동시 다발은 TICKET으로 묶음」으로 개정하는가 | **개정 확정**(D-330 ①) — 이게 없으면 10~20건이 나오지 않는다(sev3 단락만 D-318 때 33건). 효력은 W1 enforce부터 |
| **G-2** | 만성 알람(R6)도 강등하는가 | **W0 결과 뒤 결정**(권고 그대로) — W0 §10.6 S7·S8의 R6 단독 효과를 보고 정한다. 채택 시 운영자 「유효」 피드백 지문은 제외 · **미채택 확정(2026-10-15 · D-330 부기)** — 1회차 7일 창에서 5일 이상 PAGE 지문 0개 · R6 단독 강등 0건(§12.2) |
| **G-3** | 수집 실패 보수 PAGE(R4)를 「sev3만 PAGE」로 바꾸는가 | **변경 확정**(D-330 ③) + 수집 실패율 메타경보 |
| **G-4** | 미해소 리마인더 간격 | **24h 확정**(D-330 ②) |
| **G-5** | LLM 해석만으로 오른 sev3(R5)를 단락에서 빼는가 | **W0 결과 뒤 결정**(권고 그대로) — W0 §10.6 S2의 상향 출처 분해(시그니처/LLM·이상탐지) 건수로 정한다 · **보류 확정(2026-10-15 · D-330 부기)** — 1회차 24h 3건(1.3%)뿐이라 W2에 넣지 않는다. 2회차에서 10건 이상이면 다시 묻는다 |
| **G-6** | 강등 목적지 | **TICKET 확정**(D-330 ④ · 일배치 요약 + 화면 실시간) · 첫 PAGE 카드에 「묶인 알림 N건」 표시 |
| **G-7** | (W0-X 결과 B2) 호스트 키 정규화를 보강하는가(구분자·접미 차이) | **W0-X 결과 뒤 결정** — §11.6 S12 근접 불일치 건수로 정한다 · D-317 부기 대상 |
| **G-8** | (W0-X 결과 C1) 지문 dedup에 걸린 반복 알람도 크로스소스 사건을 살려 두는가 | **W0-X 결과 뒤 결정** — §11.6 S13 V1 증가분으로 정한다 · plans/144 설계 변경 |
| **G-9** | (W0-X 결과 C2·C3) 결합 조건을 시간 창 대신 「원인 미해소」 기준으로 바꾸는가 · 원인 티어 조건을 재검토하는가 | **W0-X 결과 뒤 결정** — §11.6 S13 V2·S14 지연 분포로 정한다 · plans/144 W6 §8.2 창 보정과 함께 |
| **G-10** | (v1.3 · §12.3 F1) 제니퍼 심각도 3(레벨 FATAL·CRITICAL)도 「심각도3 단락」 대신 제니퍼 유형 정책 표를 따르게 하는가 · 표 밖 유형(AGENT_STOP·Metrics Event·SERVICE_EXCEPTION 등)의 목적지는 | **(a) 확정(2026-10-15 · D-336 — 「권고에 맞게 진행」)** · 권고 (a) 정책 표 `notify: page` 유형만 PAGE · 나머지 sev3는 **TICKET 하한**(DASHBOARD 이하로는 내리지 않음) · AGENT_STOP은 TICKET(근사 24h 20건) — 대안 (a′) AGENT_STOP만 운영 소스(`b0`·`cm_prd`)에서 PAGE(근사 28건) · (c) 현행 유지(48건 · 목표 밖). D-035·D-048(D-330 개정분)·D-317 ①에 걸리므로 확정 시 D-330 부기 또는 새 D-번호 |
| **G-11** | (v1.3 · §12.4) 같은 Redis 소비자 그룹을 여러 프로세스가 읽고 있으면 어떻게 하는가 | **W0-H 진단 뒤 결정** — 진단이 다중 소비를 확인하면 선택지: 알람 워커는 한 프로세스만 켠다(`ALARM_ENABLED`·운영 절차) / 소비자 이름을 프로세스별로 바꾸고 판정 감사·레지스트리를 공유 저장소로 옮긴다(설계 변경) |
| **G-12** | (v1.3 · §12.3 F3) 비운영 제니퍼 소스(`cm_stg` — 공동존 개발/스테이징)의 PAGE를 TICKET으로 상한하는가 | **만들지 않음 확정(2026-10-15 · G-10 (a))** — 효과 0. (c) 현행 유지를 고르면 권고 채택(근사 24h 34건). `polestar_cm_yd`는 DR을 포함해(D-271) 대상이 아니다 |

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

---

## 11. W0-X 상세 — 폴스타↔APM 상관 진단 (`page_audit` S11~S14 · v1.2)

> **요청 원문(2026-10-15)**: *"현재 폴스타와 apm이 상관관계를 제대로 역지 못하는 것으로 보인다."* → *"진단 섹션 추가 계획을 162 에 추가 수립하라."*
> 운영 데이터가 없어 원인은 아직 모른다. 이 절은 원인을 **A 설정 · B 이름 정합 · C 구조** 가운데 어디인지 숫자로 가르는 진단을 W0 스크립트에 더하는 계획이다. 코드 앵커는 `badb798` 작업 트리에서 읽어 확인했다.

### 11.1 연관이 끊길 수 있는 지점 (코드 실측)

| # | 분류 | 지점 | 앵커 |
|---|---|---|---|
| A1 | 설정 | 사건 상관 `NOISE_CROSS_SOURCE_MODE`·앱 영향 조회 `app_impact_enabled` 기본 off · 규칙 표 적재 실패도 off와 같은 판정(경고 1줄) | `src/config.py:1177`·`:1194` · `infrastructure/cross_source_rules.py:189` |
| A2 | 설정 | APM 알람 존은 dbId `jennifer_<소스 id>`를 레지스트리 `solutions[apm].sources`(`b0`·`cm_prd`·`cm_stg`)로 풀어 얻는다. 게이트웨이 소스 id가 다르거나 단일 설정(`jennifer`)이면 존이 비어 사건 키가 `|host`가 되어 폴스타 `gongjon|host`와 만나지 않는다 | `src/routing/registry.py:516` · `application/server_identity.py:53` |
| B1 | 이름 정합 | 게이트웨이 정합 순서 `override` → 제니퍼 `hostName` → 인스턴스명 정규식 → `unresolved`(hostname 빈 값). 빈 값이면 사건에 아예 붙지 않는다 | `apm_gateway/.../resolver.py:354` · `domain/cross_source.py:530` |
| B2 | 이름 정합 | 호스트 키 정규화는 소문자·FQDN 첫 라벨뿐이다. 폴스타 hostname과 제니퍼 `hostName`이 다른 이름(등록명 ≠ OS 호스트명, 구분자 차이, IP)이면 키가 다르다 | `domain/alarm.py:35` |
| B3 | 이름 정합 | 정규식으로 맞춘 APM 키는 `weak`, 폴스타 hostname 2건 이상은 `none` — 강등에는 양쪽 `strong`이 필요하다(묶음은 된다) | `domain/alarm.py:138` |
| C1 | 구조 | 지문 dedup(4h)에 걸린 반복 알람은 사건에 닿기 전에 ACK된다. 사건은 마지막 알람 뒤 900초에 닫힌다 → 이어지는 폴스타 경보도 첫 통보 15분 뒤에는 사건이 없다 | `application/alarm_worker.py:1398`·`:1534` · `src/config.py:1196` |
| C2 | 구조 | 원인 발생이 증상보다 최대 600초 먼저인 것만 인정한다(규칙 `cause_before_seconds`) — 30분째 이어지는 원인은 창 밖 | `config/cross_source_rules.yaml` · `cross_source.py` `evaluate_link` |
| C3 | 구조 | 강등에는 원인 최종 티어가 PAGE·TICKET이어야 한다 — 원인이 DASHBOARD로 내려가 있으면 묶음만 된다 | `cross_source.py:239` |
| C4 | 구조 | 강등 규칙은 1행(`host_resource_to_was_latency` · was_kinds 3종)뿐 · 서버 다운 행 비활성 · 전 행 `enforce: false` | `config/cross_source_rules.yaml` |

### 11.2 목적과 경계

- **묻는 것**: 「폴스타와 APM이 같은 서버의 같은 사건으로 묶여야 했던 건이 몇 건이고, 각각 A·B·C 어느 지점에서 끊겼는가」. 운영 모드가 off여도 답한다 — 도메인 사건 추적기로 시뮬레이션한다.
- **같은 스크립트 · 같은 실행** — §10의 `page_audit`에 섹션을 더한다. 가명 키·누출 검사·출력 폴더를 공유하므로 S1~S10과 같은 서버가 같은 실행 안에서 비교된다. 새 CLI 플래그 없음(APM 수신 0건이면 「APM 수신 0건」으로 끝난다).
- **제품 코드 변경 0** · 읽기 전용(§10.1 그대로) · 게이트웨이·폴스타 DB·LLM 호출 0.
- **호스트 우주는 「창 안에 알람을 낸 서버」뿐이다** — 게이트웨이 인벤토리·폴스타 서버 목록을 읽지 않으므로 「알람이 없던 짝」은 보이지 않는다. 그래서 짝 비율은 7일 창으로 본다.
- 결과는 `plans/144` W6(§8.2 원인→증상 지연 분포·lift로 창 보정)의 재료도 된다.

### 11.3 입력 — 새 입력원 없음, 읽는 필드만 늘린다

| 원천 | 추가로 읽는 필드 | 쓰임 |
|---|---|---|
| `alarm:raw` APM 행 | `dbId` · `hostname` · `raw.apm.{source_id, match_reason, match_confidence, level, event_type_norm, was_signals[*].kind, instance_name, domain_name}` | 소스 id 등록 여부(A2) · 정합 사유(B1) · 인스턴스 단위 집계 · 규칙 선택자 |
| `alarm:raw` 폴스타 행 | `dbId` · `hostname` · 해소(`severity 0`) | 호스트 키 · 원인 미해소 판정 |
| 판정 레코드 | `stage_evidence.episode_id`(구현 실측 — `decision_store.record`에 최상위 인자가 없다 · 최상위도 함께 읽는다) · `evidence.cross_source.{rule_id, action, enforce, missing, applied, lag_seconds}` · `stage_evidence.app_impact_error`·`app_impact_fatal_events`·`app_impact_source` · 티어 | 실제 운영 기록(모드≠off일 때) · 원인 티어 |
| `type=recurrence` · `type=resolution` · `type=late_promotion` | 시각 · 지문 | C1 변형(반복이 사건 유지) · 원인 해소 시점 · 사후 승격 수 |
| 정책 스냅샷 | `cross_source_mode` · `app_impact_enabled` · `app_impact_window_minutes` · `app_impact_late_promotion_enabled` · `episode_idle_seconds` · `cross_source_rules_path` 적재 결과 | A1 |
| 규칙 표 · 레지스트리 | 규칙 행별 id·enabled·enforce·action·창·선택자 수 · 파일 해시 / `solutions[apm].sources`의 id·존 | A1·A2 (설정 파일이라 식별 정보 아님 · 그대로 싣는다) |

`--no-stream`이면 APM 정합 사유와 인스턴스 정보가 없다 — S12는 「모름」, S13은 판정 레코드에 남은 실제 기록만, S14는 판정 레코드의 시각으로만 계산한다.

### 11.4 비식별 추가 규칙 (§10.4 허용 목록에 더함)

| 원천 | 처리 | 출력 |
|---|---|---|
| `hostname`(양 소스) | `normalize_host_key`(도메인 함수 그대로) → 같은 실행 HMAC → `K-…` · **폴스타와 APM이 같은 함수라 같은 서버 = 같은 `K-`**. 기존 `host`(`H-`)는 등록 서버명 기준이고 APM 미정합 시 인스턴스명이라 소스 간 비교에 쓰지 않는다 | `xs.hk` |
| `raw.apm.instance_name` | HMAC → `I-…` · 원문 버림 | `xs.inst` |
| `episode_id` | HMAC → `E-…` | `xs.episode` |
| `match_reason` · `match_confidence` | 닫힌 열거 {`override`·`host_name`·`regex`·`unresolved`·`ambiguous`}(게이트웨이 `resolver.reverse` 반환 어휘 + 소비측 `ambiguous`) / {`high`·`medium`·`none`} — 그 밖은 `<other>` | `xs.match_reason` · `xs.match_conf` |
| APM 소스 id | 레지스트리 등록 id면 그대로 · 아니면 `S-…` 가명 + `registered: false` · 단일 설정 `jennifer`는 `default` | `xs.apm_source` |
| `was_signals[*].kind` · `cross_source.missing` · `action` | 도메인 상수 어휘(`was_*` 게이트웨이 고정 어휘 · `MISSING_*` · `link`/`demote`) — 그 밖은 `<other>` | `xs.was_kinds` · `xs.actual` |
| `app_impact_error` | `" — "` 앞 코드만 열거 — 소비측 3종(`gateway_unreachable`·`gateway_error`·`contract_violation`) + 게이트웨이 오류 어휘(`apm_gateway/domain/errors.py` `ERROR_CODES` 사본 · 패키지 경계상 import하지 않는다). 그 밖은 `<other>` · 뒤의 예외 문구(URL·호스트가 섞일 수 있다)는 버림. **`instance_unresolved`는 「폴스타 호스트에 맞는 제니퍼 인스턴스가 없다」는 뜻이라 S12에 따로 센다** | `xs.app_impact` |
| 느슨한 키(영숫자만 남긴 키) · 포함 관계 | 값은 내보내지 않는다 — 일치 여부 bool만 | `xs.loose_peer` · `xs.contains_peer` |
| `domain_name` · `application`(URL) · `ipAddress` | **버림** | — |

- **누출 검사 식별자 목록에 더한다**: 인스턴스명 · 제니퍼 도메인명 · 정규화 전·후 호스트 키(4자 이상) · `application` URL.
- 규칙 표·레지스트리 소스 표는 설정 파일 값이라 그대로 싣되, 규칙 `alarm_names`는 §10.4 알람명 정규화를 거친다.

### 11.5 분석 로그 — `alarms.jsonl` 행에 `xs` 객체 추가

```json
"xs": {"hk": "K-3f2a…", "hk_strength": "strong", "zone": "gongjon", "zone_ok": true,
       "apm_source": "cm_prd", "registered": true, "match_reason": "host_name", "match_conf": "high",
       "inst": "I-81c0…", "was_kinds": ["was_service_queuing"],
       "peer_7d": true, "loose_peer": false, "contains_peer": false,
       "nearest_cause": {"lag_s": 1830, "kind": "cpu", "tier": "dashboard", "open": true},
       "actual": {"episode": "E-0b9d…", "rule_id": "host_resource_to_was_latency",
                  "action": "link", "missing": ["window", "cause_tier"], "applied": false},
       "app_impact": null,
       "sim": {"V0": "link:window,cause_tier", "V1": "link:cause_tier", "V2": "link:cause_tier", "V3": "demote"}}
```

- `peer_7d`: 같은 `K-`가 7일 창 안에 **다른 소스**에도 나타났는가 · `nearest_cause`: 같은 존·`K-`의 직전 폴스타 알람(규칙과 무관)과의 지연·kind·최종 티어·그 시점 미해소 여부.
- 폴스타 행은 `match_reason`·`inst`·`apm_source`·`was_kinds` 없이 `hk`·`peer_7d`·`actual`·`sim`만 싣는다. 사건에 붙을 수 없는 행(키 빈 값)은 `hk: null`과 사유만 싣는다.

### 11.6 집계 S11~S14

| # | 섹션 | 내용 | 가르는 것 |
|---|---|---|---|
| S11 | 설정 판독 | 모드·앱 영향 플래그·idle 값 · 규칙 표 적재 성공 여부와 행별 enabled/enforce/action/창 · **수신 APM 소스 id × 레지스트리 등록 여부**(미등록·단일 설정 건수 = 존 빈 값 건수) · 앱 영향 조회 결과 분포(조회 대상 아님 / fatal 있음 / fatal 0 / 실패 코드별 — `instance_unresolved`는 S12로) · 사후 승격 건수 | A1 · A2 |
| S12 | 이름 정합 | APM 알람·인스턴스 단위로 정합 사유 분포 · 키 신뢰도 분포 · 미정합 인스턴스 수와 그 알람 건수 · **짝 비율**: 정합된 APM 키 중 7일 안 폴스타에도 나타난 비율, 폴스타 키 중 APM에도 나타난 비율 · 근접 불일치(느슨한 키로만 일치 = 구분자·대소문자 밖 차이 / 한쪽이 다른 쪽을 포함 = 접미·접두) 건수 · **미정합 인스턴스명 안에 폴스타 키가 토큰으로 들어 있는 인스턴스 수**(= 정합 규칙·`overrides`로 풀 수 있는 후보) · 반대 방향: 앱 영향 조회가 `instance_unresolved`로 끝난 폴스타 키 수 | B1 · B2 · B3 |
| S13 | 사건·결합 | **실제 기록**(모드≠off): 사건 수 · 두 소스 멤버 사건 수 · 링크 action별 · `missing` 라벨별(한 링크에 여러 개면 각각) · applied 수. **시뮬레이션**(모드와 무관): 도메인 `EpisodeTracker`를 판정 레코드 순으로 재생 — 변형 4종의 두 소스 사건 수·link·demote 후보·`missing` 분포·demote 후보 중 sev3 증상 0 확인 | C1~C4 |
| S14 | 시간 관계 | 같은 존·`K-` 쌍의 폴스타→APM 최근접 지연 분포(음수 = APM 먼저 · 구간 <1분·<5분·<10분·<30분·<1시간·<4시간·그 이상) · 원인 kind × 증상 was_kind 상위 표 · 그 시점 원인 미해소 비율 · 원인 최종 티어 분포 · 규칙 행별 lift(원인 뒤 창 안 증상 비율 ÷ 기준 증상 비율) | C2 · C3 · plans/144 W6 |

**시뮬레이션 변형** — 규칙 정의는 도메인 하나에서만 온다(`EpisodeTracker`·`evaluate_link`·`episode_alarm`·`load_rules` 호출 · `replay_noise._cross_source_signal`과 같은 조립 · §10.7의 R1~R3 재사용과 같은 원칙). 시계는 워커처럼 `now` = 판정 시각, 창은 발생 시각으로 잰다. 원인 티어는 판정 레코드 티어로 `record_tier`한다. 폴스타 서버 식별 모호(`ambiguous`)는 오프라인에서 알 수 없어 `strong`으로 두고 한계로 적는다.

| 변형 | 바꾸는 것 | 보는 것 |
|---|---|---|
| V0 현행 | 없음(판정 레코드 알람만 · idle `episode_idle_seconds` · 규칙 표 그대로) | 운영이 off여도 「켰다면」의 결과 · 모드≠off면 실제 기록과 같아야 한다(대조 실패는 S10에 경고) |
| V1 반복 유지 | `type=recurrence` 시각마다 같은 존·키의 열린 사건 `last_seen`을 갱신(스크립트 안 어댑터 · `episodes()`로 얻은 사건만 만진다) | C1 효과 |
| V2 미해소 기준 | 규칙 `cause_before_seconds`를 dedup 창(`repeat_interval_seconds`)으로 넓힌 사본(`dataclasses.replace`) — 결합 조건 ④ 「원인 미해소」가 사실상 기준이 된다 | C2 효과 |
| V3 | V1 + V2 | 구조 개선 상한 |

### 11.7 판독표 — 결과 → 원인 → 다음 조치

| 결과 | 원인 | 조치(이 계획 밖 · 결정 필요 여부) |
|---|---|---|
| 모드 off · 규칙 표 적재 실패 | A1 | 운영 `.env`·규칙 표 경로 정정 — 결정 불필요 |
| 미등록 APM 소스 ≥1 · 존 빈 값 건수 >0 | A2 | 게이트웨이 `JENNIFER_SOURCES` id 또는 레지스트리 `sources` 정정 — 결정 불필요(D-329 확정값 대조) |
| 미정합 비율이 높고 토큰 후보가 많다 | B1 | 게이트웨이 정합 파일 정규식·`overrides` 보강 — 결정 불필요 |
| 정합됐는데 짝 비율이 낮고 근접 불일치가 많다 | B2 | 호스트 키 정규화 보강(코드 변경) — **G-7** |
| 짝 비율은 높은데 V0 링크가 적고 V1에서 크게 는다 | C1 | dedup 반복이 사건을 살리게 — **G-8** |
| V2에서 크게 늘고 S14 지연이 600초 밖에 몰린다 | C2 | 창 대신 「원인 미해소」 기준 또는 창 보정 — **G-9** (plans/144 W6 §8.2와 같이) |
| `missing=cause_tier`가 다수 | C3 | 원인이 DASHBOARD로 내려간 설계 결과 — 강등 조건 재검토는 G-9에 포함 |
| 링크는 충분한데 demote 0 | C4 | 규칙 행 `enforce` 전환 — plans/144 G-7 기준(shadow 2주 거짓 강등률 ≤2%) |

G-7~G-9는 크로스소스 설계(plans/144 · D-317)를 바꾸는 결정이다. **진단 결과를 보고 사용자가 정한다** — 이 Wave에서는 아무것도 바꾸지 않는다.

### 11.8 파일·테스트·완료 기준

| 파일 | 변경 |
|---|---|
| `noise_gate/scripts/page_audit_xsource.py`(신규) | S11~S14 집계 · 변형 V0~V3 시뮬레이션 · `xs` 행 필드 계산 — `page_audit.py`(1,947줄)를 더 키우지 않으려고 나눈다 |
| `noise_gate/scripts/page_audit.py` | `xs` 필드·섹션 호출 배선 · `summary.md` 렌더에 S11~S14 · 정책 스냅샷에 §11.3 필드 |
| `noise_gate/scripts/page_audit_sanitize.py` | `K-`·`I-`·`E-`·`S-` 가명 · 열거 허용 목록 · 누출 식별자 추가 |
| `noise_gate/testdata/page_audit/` | APM 행 추가(정합 사유 각 종 · 미등록 소스 · FQDN·구분자 근접 불일치 · 인스턴스명 토큰 후보 · dedup이 사건을 닫는 시퀀스 · 30분 지연 시퀀스 · 원인 DASHBOARD) — IP는 문서용 대역, 이름은 `fake-*` |
| `noise_gate/tests/test_page_audit_xsource.py`(신규) | 아래 완료 기준 |

1. **누출 0** — 인스턴스명·제니퍼 도메인명·`application` URL·`app_impact_error` 예외 문구·정규화 전후 호스트 키가 출력 5개 파일 어디에도 없다.
2. **소스 간 같은 가명** — 폴스타 `FAKE-WEB01.example.test`와 APM `fake-web01`이 같은 `K-`.
3. **도메인과 차등 0** — 같은 픽스처에서 V0의 사건·link·demote 수가 `replay_noise`의 `cross_source` 요약과 같다. *(구현 해석 2026-10-15: V0은 원인 티어를 판정 레코드에서, `replay_noise`는 재판정 티어를 쓰므로 「기록 티어 = 재판정 티어」인 부분 집합에서 같다 — 전체 픽스처는 원인 DASHBOARD 시나리오 1건만 link↔demote로 갈린다. 시계도 V0 = 판정 시각 · replay = 발생 시각이라 판정 지연이 idle을 넘으면 갈린다)*
4. **변형 효과** — dedup이 사건을 닫는 시퀀스는 V0 link 0 → V1 link 1, 30분 지연 시퀀스는 V0 `missing=window` → V2 window 해소. demote 후보 중 sev3 증상 0.
5. **판독표** — §11.7의 각 행을 만드는 픽스처가 기대 원인 분류를 낸다(미등록 소스 → A2 등).
6. **`--no-stream`** — S12는 「모름」, 예외 없이 끝난다.
7. **기존 출력 불변** — S0~S10과 기존 `alarms.jsonl` 필드는 비트 동일(`xs`·새 섹션만 추가). 기존 `test_page_audit*.py` 그대로 통과.
8. **정적 게이트** — `regress.py --no-tests --files <위 파일>` 통과. 계획 끝 모듈 회귀는 `--base 4073f7f`(§0 · D-303).

### 11.9 내부망 실행 · 반출

- 명령은 §10.10과 같다 — `python -m noise_gate.scripts.page_audit --hours 24 --days 7`. 짝 비율은 7일 창이 있어야 의미가 있다.
- Redis 스트림이 필요하다(정합 사유는 원문에만 있다). `--no-stream`은 S12를 비운다.
- 반출 파일도 같다. 판단에 쓰는 표는 `summary.md`의 S11~S14다.

### 11.10 하지 않는 것

- 게이트웨이 인벤토리·폴스타 서버 목록을 조회하지 않는다(우주는 알람을 낸 서버뿐 — §11.2).
- 규칙 표·정합 파일·정규화 코드를 자동으로 고치지 않는다 — 판독표의 조치는 사용자 결정 뒤 별도 작업.
- 사건 상태를 운영 워커에 되돌려 쓰지 않는다.

---

## 12. W0 1회차 내부망 실행 결과와 수정 계획 (v1.3 · 2026-10-15)

> **요청 원문(2026-10-15)**: *"20261015-082712 폴더에 162번 로그가 저장되어 있다. 분석하여 수정 계획을 162번 계획에 업데이트하라."*
> **입력**: `results/page_audit/20261015-082712/`(git 추적 밖) — `summary.{md,json}` · `policy_snapshot.json` · `leak_check.json`(`passed: true` · 식별자 151,612개 대조 · 걸림 0) · `alarms.jsonl`(170,199행 · 163MB). 창 끝 2026-10-15 08:27 KST · 짧은 창 24h · 긴 창 7일. 내부망 코드 SHA `aa49653`(외부망 이력에 없음 — 반입 묶음 대조 기준이 따로 필요하다). **이 실행본에는 W0-X(S11~S14)가 없다.**
> **증거 규칙**: §12.1~12.2는 요약 파일 그대로다. 「근사」라고 적은 수치는 `alarms.jsonl`을 외부망에서 간이 재생한 값이다(도메인 `review_page`가 아니라 같은 규칙을 단순화한 스크래치 계산 · R1~R3 근사 51 vs 스크립트 48로 오차 ±3). 운영 워커 로그·운영 `.env` 원문은 보지 못했다.

### 12.1 사실 — 무엇이 PAGE를 만들었나

| 항목 | 값 | 출처 |
|---|---|---|
| 24h 수신 | 19,997건 — **제니퍼 16,122(80.6%)** · 폴스타 3,875 · 심각도 2가 15,839 | S1 |
| 24h PAGE | **227건**(사용자 보고 170보다 많다) — `severity3` 단락 223 · 매트릭스 4(제니퍼 `notify` 승격) · 수집 실패 0 | S2 |
| PAGE 출처 | **제니퍼 207** = `jennifer_b0` 104 · `jennifer_cm_stg` 91 · `jennifer_cm_prd` 12 / 폴스타 20 = `polestar_cm_yd` 11 · `polestar_cm_gp` 9 | alarms.jsonl |
| 제니퍼 PAGE 알람 | `cm_stg` AGENT_STOP **79**(84호스트) · `b0` Metrics Event 45 · `b0` DB_CONNECTION_FAIL 41 · `b0` AGENT_STOP 16 · `cm_stg` SERVICE_EXCEPTION 12 · `cm_prd` AGENT_STOP 7 · 기타 7 | alarms.jsonl |
| 제니퍼 sev3의 원천 | 원 심각도 3이 202건 — 게이트웨이 레벨 매핑 `fatal: 3`·`critical: 3`(`apm_gateway/config/event_levels.yaml`). 제니퍼 정책 표(`config/apm_noise_policy.yaml`)는 AGENT_STOP·Metrics Event·SERVICE_EXCEPTION을 `default`(dashboard)로 두지만 **sev3는 step 3 단락이 정책 표보다 먼저라 표가 닿지 않는다** | alarms.jsonl · 코드 |
| 제니퍼 sev2 | 정책 경로가 잘 듣는다 — 판정 1,328건 중 억제 706 · TICKET 617 · PAGE 5 | alarms.jsonl |
| AI 상향(H6) | 3건(폴스타 2 · 제니퍼 1) | S2 |
| 수집 실패(H5) | 24h 0건. 단 10-08에 제니퍼 `unavailable` 513건이 PAGE였다 → 그 뒤 `apm_noise_policy_enabled=true`로 해소된 것으로 보인다 | alarms.jsonl · 정책 스냅샷 |
| 폭주(H4) | 10-14 17:57~18:53에 PAGE 114건(71호스트) — `b0` Metrics Event 45 · DB_CONNECTION_FAIL 37 · AGENT_STOP 13 … 한 사건의 여러 알람명. 같은 시간 처리 지연 p50 246초 · 최대 701초 | S5 · alarms.jsonl |
| 반복(H1·H2) | 지문당 PAGE 1회가 207개 · dedup 창 안 재PAGE 1건 — **반복은 주원인이 아니다** | S3 |
| 만성(H7) | 7일 중 5일 이상 PAGE 지문 0개 | S7 |
| 해소 | PAGE 227 중 미해소 218 — **제니퍼 해소 이벤트가 7일 동안 0건**이라 구조적으로 미해소다 | S6 · alarms.jsonl |
| 일별 PAGE | 10-12 142 · 10-13 188 · 10-14 226 · (10-10·10-11은 판정 0건) | alarms.jsonl |
| 크로스소스 | `cross_source_mode=enforce`인데 7일간 사건 합류 5,487건 · **link·demote 0건**(근거에 `rule_id`·`action`이 한 번도 없다) — 사용자 관찰 「상관관계를 엮지 못한다」와 맞는다. 원인 가르기는 W0-X(§11) 재실행으로 | S10 · alarms.jsonl |

### 12.2 what-if — 현행 규칙만으로는 목표에 못 닿는다

| 시나리오 | 24h 남는 PAGE | 일별(10-12·13·14) | 출처 |
|---|---|---|---|
| 현행 | 227 | 142·188·226 | S8 |
| R1 · R2 · R3 · R4 · R5 · R6 단독 강등 | 14 · 44 · **163** · 0 · 3 · 0 | — | S8 |
| R1~R3 누적(W1 enforce 가정) | **48** — 남는 것: `cm_stg` AGENT_STOP 12 · `gp` 사용률(심각_평균) 5 · `b0` AGENT_STOP 5 · `yd` Server 가용성 5 · … | 7일 일평균 32.4 | S8 |
| (근사) F1 제니퍼 sev3 정책 표 경로만 | 62 | 33·47·60 | 근사 |
| (근사) **F1 + R1~R3** | **20** | 15·26·16 | 근사 |
| (근사) F1′(AGENT_STOP 운영 소스만 PAGE) + R1~R3 | 28 | 21·33·25 | 근사 |
| (근사) F3 `cm_stg` 상한 + R1~R3(F1 없이) | 34 | 27·35·31 | 근사 |
| (근사) R3 묶음 키를 알람명 없이 db_id로 | 46 | 48·59·42 | 근사 — 효과 작아 택하지 않는다 |

**F1 + R1~R3 뒤에 남는 20건**(근사): `b0` DB_CONNECTION_FAIL 5 · `gp` 사용률(심각_평균) 4 · `yd` Server 가용성 3 · `yd` 사용률 2 · `yd` Filesystem 사용률 2 · `gp` 사용률(경계) 2 · `b0` OUT_OF_MEMORY 1 · `gp` 사용률(심각_피크) 1 — 사람이 볼 만한 것만 남는다.

### 12.3 수정 항목

| # | 문제 | 수정 | 결정 | Wave |
|---|---|---|---|---|
| **F1** | 제니퍼 레벨 FATAL·CRITICAL이 폴스타 「심각」과 같은 sev3 단락을 탄다 — 제니퍼 정책 표가 sev3에 닿지 않아 PAGE의 89%(203/227)가 여기서 나온다 | 제니퍼 정책 경로(`apm_policy` 있음)의 sev3는 step 3 단락 대신 정책 표로 판정 — `notify: page` 유형은 PAGE, 그 밖은 **TICKET 하한**. 새 플래그 `NOISE_APM_SEV3_POLICY_ENABLED`(기본 off = 비트 동일). 폴스타 sev3 단락은 그대로 | **G-10** | W5 |
| F2 | 정책 표 키와 실제 유형 이름이 어긋날 수 있다 — 표는 `OUTOFMEMORY`, 관측 알람명은 `OUT_OF_MEMORY`. 1회차는 `event_type_norm`이 가명화돼 대조가 안 된다(T1) | T1 반영 2회차에서 PAGE·TICKET 유형의 `event_type_norm`을 표 키와 전수 대조 → 다른 키는 표에 더하거나 고친다(데이터 정정) | 불필요 | W0-T → W5 |
| F3 | 비운영 소스 `cm_stg`가 PAGE 91건 | G-10 (a)면 효과 0 → 만들지 않는다. (c)면 소스 단위 TICKET 상한 | **G-12** | (조건부) W5 |
| F4 | 제니퍼는 해소가 없어 `PageRegistry`에서 사건이 닫히지 않는다 → R1이 같은 지문의 **진짜 재발**도 24h 동안 강등할 수 있다(보존 4 리마인더만 남는다) | 제니퍼 지문은 「마지막 이벤트(재발 억제 포함) 뒤 `reopen_quiet_minutes` 무소식이면 닫힘」으로 본다. 먼저 T6으로 같은 지문 이벤트 간격 분포를 재고 값을 정한다 | 불필요(D-330 R1 정의 안 — 해소 대용 규칙 부기만) | W0-T → W1 보강 |
| F5 | 수신의 59.7%(11,942건)가 판정·재발 기록 없이 사라진다(H10) · 10-15 04:00~08:27은 수신 3,000여 건에 판정 0건인데 소비자 그룹 `last-delivered-id`는 08:26:58까지 와 있고 dead-letter 0 | §12.4 진단 → G-11 | **G-11** | W0-H |
| F6 | R4·수집 실패율 메타경보가 꺼져 있다(`collection_failed_page_sev3_only=false` · `meta_alert_collection_failed_ratio=0.0`) — 24h 효과 0이지만 10-08 같은 정책 경로 장애(513 PAGE)를 막는 안전망이다 | W4 enforce 때 함께 켠다(D-330 ③ 확정 범위 — 새 결정 없음) | 불필요 | W4 |
| F7 | 폭주 시 처리 지연(18시 p50 246초·최대 701초) — PAGE가 최대 12분 늦게 나간다 | 이 계획 범위 밖(워커 직렬 처리·LLM 호출 비용). 기록만 하고 별도 계획 후보로 둔다 | — | — |

### 12.4 W0-H — 소비 경로 진단 (H10 · 내부망)

**가설**: 워커 소비자 이름이 상수 `worker-1`(`noise_gate/application/alarm_worker.py:96`)이고 알람 워커는 `ALARM_ENABLED`인 앱 프로세스마다 하나씩 뜬다(`src/api/server.py:427`). 같은 Redis 그룹을 읽는 프로세스가 둘 이상이면 `XINFO`에는 소비자 1개로 보이면서 수신이 나뉘고, 판정 감사는 프로세스마다 제 파일에 쓴다. 1회차 정황 — 시간대마다 수신의 약 절반이 미집계 · 04:00~08:27 전부 미집계인데 소비 위치는 최신 · dead-letter 0 · 미판정 52건.

| # | 확인 | 판독 |
|---|---|---|
| 1 | 같은 Redis를 쓰는 서버·컨테이너에서 `python -m src.main --server` 프로세스 수 · 각 `ALARM_ENABLED` · uvicorn worker 수 · 시험·벤치 인스턴스 | 2개 이상이면 다중 소비 확정 |
| 2 | `XINFO CONSUMERS alarm:raw <group>`의 `idle`·`pending`을 수 분 간격 2회 | 한 이름이 서로 다른 곳에서 읽히면 idle이 계속 0 근처 |
| 3 | 각 프로세스의 `NOISE_DECISION_STORE_PATH`(상대 경로면 작업 디렉터리 포함)와 파일 마지막 줄 시각 | 다른 파일에 04~08시 판정이 있으면 다중 소비 확정 |
| 4 | 10-15 03~08시 워커 로그의 `알람 처리 시작`·`알람 처리 실패`·`알람 워커 종료` | 처리 시작이 있는데 감사가 없으면 감사 쓰기 실패(`decision_store` OSError 경고) |
| 5 | 10-10·10-11(판정 0) 운영 이력 | 주말 정지인지 확인 |

- **결과별 다음 조치**: 다중 소비 → G-11 · 감사 쓰기 실패 → 디스크·권한 정정(결정 불필요) · 단일 소비인데 공백 → 워커 로그로 새 원인 분리.
- **W4와의 관계**: 소비가 나뉘면 지문 dedup·`PageRegistry`·크로스소스 사건이 모두 프로세스별 반쪽이다. 그 상태의 shadow 수치는 믿을 수 없으니 W4는 W0-H 뒤에 한다. 관제 화면의 PAGE 수도 화면을 띄운 프로세스의 감사 파일 기준이라, 사용자가 본 170과 1회차의 227이 다른 이유일 수 있다.

### 12.5 W0-T — 분석 도구 보정 (`page_audit`)

| # | 결함(1회차 실측) | 보정 |
|---|---|---|
| T1 | `evidence.apm_policy.event_type_norm`이 `N-…`로 가명화돼 정책 표 키와 대조가 안 된다(§10.4는 그대로 내보내기로 정했다) | 게이트웨이 정규화 결과(대문자·`_`·숫자 · 2~40자)면 그대로 · 아니면 가명. 정책 표 키 일치 여부 bool을 더한다 |
| T2 | S6 미해소 218이 제니퍼(해소 이벤트가 없음)와 폴스타를 섞는다 | S6을 출처별로 나누고 「해소 이벤트 0건 출처」를 표시 |
| T3 | S9 「같은 호스트·알람명에 지문 2개 이상」 168묶음이 제니퍼 인스턴스 단위 지문과 섞여 H8 판독이 안 된다 | 출처별로 나누고 제니퍼는 지문 원천(유형·인스턴스)으로 따로 센다 |
| T4 | S1 처분이 소비 공백을 못 보여 준다 — 미판정(XPENDING) 52건이 `pending`에 안 들어가고, 시간대별 맞대기 비율이 없다 | `delivered_unacked` 건수 · 시간대별 `decided+dedup` 비율 · **판정 공백 구간**(수신 있고 판정·재발 0인 연속 시간) · `XINFO CONSUMERS`의 이름·idle·pending을 정책 스냅샷 `stream`에 싣는다 |
| T5 | 판정 레코드 최상위 `episode_id`가 `<unknown_key>` 5,487건으로만 센다 | 이름을 붙여 센다(값은 W0-X `xs.episode` 가명 규칙) |
| T6 | G-10·G-12·F4를 고를 what-if가 없다 | S8에 시나리오 추가 — `APM_SEV3_POLICY`(정책 표 `notify`로 판정 · 표 밖 TICKET) · `APM_SEV3_POLICY+AGENT_STOP_PRD` · `NONPROD_CAP`(`cm_stg`) · 각각 R1~R3 누적과 결합한 값. 제니퍼 같은 지문 이벤트 간격 분포(재발 억제 포함 · F4 근거). W5 구현 뒤에는 도메인 함수를 불러 차등 0으로 바꾼다(§10.7 원칙) |

- **완료 기준**: 기존 `test_page_audit*.py` 그대로 통과 · T1~T6 각각 픽스처 테스트(T1은 가짜 유형 `FAKE_EVENT_TYPE`이 그대로 나오고 소문자·공백 섞인 값은 가명) · 누출 검사 통과 · `regress.py --no-tests --files <바꾼 파일>`.
- **같은 파일을 W0-X가 고치는 중이다**(`page_audit.py`·`page_audit_sanitize.py` · 작업 트리 미커밋). W0-T는 W0-X가 끝난 뒤 그 위에서 한다 — 내부망 2회차는 W0-X·W0-T를 한 번에 반입해 S0~S14를 함께 낸다.

### 12.6 W5 — 제니퍼 sev3 정책 경로 (G-10 확정 뒤)

- **위치**: `decide_notification` step 3(`noise_gate/domain/notification_policy.py` 심각도3 단락) 앞에서, 플래그가 켜져 있고 `apm_policy`가 있으면(제니퍼 정책 경로) 단락하지 않는다. 정책 표 `notify`가 `page`면 PAGE(단계 `severity3` 유지 · 사유에 「제니퍼 유형 정책」), 아니면 매트릭스로 내려가되 결과가 TICKET보다 낮으면 **TICKET으로 올린다**(sev3를 DASHBOARD 이하로 내리지 않는다 — D-317 ① 「정책 사유만으로 DASHBOARD 아래 금지」와 같은 원칙).
- **바뀌지 않는 것**: 폴스타 sev3 단락 · 크로스소스 강등 조건 ⓔ(증상 심각도 <3) · 앱 영향 승격 · `page_review` 재판정(F1 뒤에도 R1~R3가 DB_CONNECTION_FAIL 폭주 등을 묶는다).
- **정책 표**: G-10에서 AGENT_STOP 목적지가 정해지면 표에 행을 더한다(`notify: ticket` 또는 소스별 — 소스별이면 표 문법 확장이 필요하다). F2 대조 결과로 키를 정정한다.
- **테스트**: 플래그 off 비트 동일(기존 정책·스냅샷 테스트 그대로) · on에서 `notify: page` 유형 PAGE · 표 밖 유형 TICKET · 폴스타 sev3 불변 · 정책 경로 미가용(`unavailable`)이면 현행 단락.
- **기록**: D-330 부기(제니퍼 sev3 예외) 또는 새 D-번호 · D-317 ① 부기 · `docs/28` §4-C · 관제 화면 사유 문구 · 매뉴얼 admin(D-255).
- **구현 결과(2026-10-15 · 커밋 없음)**: D-336 ①대로 **step 3에서 끝낸다** — 위 「매트릭스로 내려가 TICKET 하한」 원안 대신 정책 경로(`source=apm_policy`) sev3는 `noti_policy=="notify"`면 PAGE, 그 밖은 TICKET(단계 `severity3` · 근거 `apm_sev3_policy`{notify, policy_row}). AGENT_STOP 행은 두지 않았다(표 밖 = TICKET · 정책 표는 주석만). 배선: `src/config.py` `apm_sev3_policy_enabled` · `.env.example` · 설정 도움말 · `settings_catalog`(「PAGE 재판정」 구획) · `stage_handling` ticket 안내 · `noise-help.js`(퍼널 설정·근거 라벨·요약 티어 중립) · `noise.js` 퍼널 질문 · `docs/28` §4-C · 매뉴얼 admin·user(캡처 없음 — 새 화면 없음). 테스트 31건(`test_apm_sev3_policy{,_verify}.py`). 회귀 `--base 4073f7f` 모듈 단위 신규 실패 0. **이탈·잔여**: shadow(「켰으면 TICKET」) 근거 없음 — 켜기 전 영향은 T6 what-if · F2 키 정정은 2회차 뒤 · TICKET sev3는 자동 조사·incident open·L1 보강 미부착 · 재통보 간격은 sev3 값 그대로(D-336 주의 부기) · `page_audit_sanitize` 허용 목록에 `apm_sev3_policy` 미추가(W0-T에서).

### 12.7 다음 순서

1. ~~사용자 결정 G-10~~ — **(a) 확정 2026-10-15(D-336)** · G-12 불필요 · AGENT_STOP TICKET.
2. 내부망 **W0-H** 확인(§12.4 표 1~5) → G-11.
3. 외부망 **W0-X 마무리 → W0-T**(같은 파일 순서) → 내부망 **2회차** `--hours 24 --days 7`(S0~S14 · T1~T6).
4. ~~W5 구현~~ — **완료 2026-10-15(커밋 없음 · 기본 off)**. 2회차로 F2 키 대조(표 키 정정) · F4 값 · G-7~G-9 판독 → W5 켜기 판단(W4와 같이 · D-336 ④).
5. **W4** shadow 24h(전제: W0-H 단일 소비 · R4·메타경보 함께 켬) → 「했을 PAGE」 ≤ 20 · 놓친 사건 0 → enforce.
