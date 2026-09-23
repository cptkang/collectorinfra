# 117. 머신러닝 기반 노이즈 판정 — 알람 "대응 필요 확률"을 게이트 보조 신호로 넣는다

> **작성일**: 2026-09-23
> **상태**: 분석·계획(코드 0건) — 파일명 `-TODO`. 사용자 확정 게이트 G-1~G-8 대기(§8).
>
> **요청 원문(2026-09-23)**: *"머신러닝 기반 노이즈 판정을 추가하려면 어떻게 해야 하냐?"* → 답변 뒤 *"ML구현 계획을 별도의 계획파일로 작성하고 앞서 노이즈 캔슬링 동작 방법은 메뉴얼에 더 자세한 내용이 들어가도록 반영하라."*
>
> **관련 결정**: D-003(읽기 전용) · **D-035**(결정적 판정 · LLM 주석) · **D-048**(4-티어 게이트 · 재현율 우선 · .5·.11 "ML 미사용") · D-049(incident 라이프사이클) · D-110·D-113(HW·STL — 게이트 내 통계) · D-114(임베딩 주석 전용) · D-118(`sre_agent` 경계) · D-119(관측 데이터 읽기 = `mcp_server`) · D-139(패키지 경계) · D-161(승격·폐기 동반) · D-162(플래그 만료) · D-177(피드백) · **D-223**(`plans/101` ML 역할·배치·평가·반입 계약 — 예약)
> **선행·접점 계획**: **`plans/101`**(ML 장애 진단·예측·RCA — 배치·평가 규약·누수 금지의 정본) · `plans/52`(게이트 설계 정본) · `plans/60`(E1~E7 보강) · `plans/83`(피드백 = 라벨 원천) · `plans/112`(결정 추적 근거 필드) · `plans/116`(매뉴얼 — 노이즈 동작 설명 반영처)
> **신규 결정 예약**: **D-253**(「채번 이력」 표 등재 — 2026-09-23 실측: `## D-` 헤더 최댓값 D-251 · 「채번 이력」 D-252=`plans/116` 예약 · 안내 라인 "다음 D-253").

---

## 0. 이 문서의 증거 규칙

- 앵커는 전부 2026-09-23 작업 트리(`multiintent` HEAD `a276812` + 미커밋)에서 읽기·grep으로 실측했다. 패키지 설치 0 · 실 LLM 0 · 운영 DB 조회 0.
- 플래그 값은 **리포지토리 `.env`(개발 맥)** 기준이다. 폐쇄망 운영 `.env`는 확인하지 못했다(§9).
- 알고리즘·평가 규약의 문헌 근거는 새로 조사하지 않았다. `plans/101` §0.2·§3·§6과 그 동반 조사 문서(`docs/aiops_benchmark/ml_*`)를 그대로 인용한다.

---

## 1. 결론

### 1.1 한 줄

**ML은 판정자가 아니라 "대응 필요 확률" 점수를 만드는 보조 신호로 들인다.** 학습·배치 점수 계산은 `plans/101`이 정한 대로 `sre_agent` 안의 `ml_batch`에서 하고, 게이트는 Redis 키만 읽어 **step9 보조 조정에 신호 1개**로 넣는다. 켜는 순서는 off → shadow → annotate → advise이고, advise에서도 심각도 3·1단계 제한·승격 우선 규칙은 그대로다.

### 1.2 왜 이 모양인가

| 제약 | 근거 | 설계 반영 |
|---|---|---|
| 판정은 결정적 규칙 | D-035 · D-048 · `notification_policy.py` 모듈 독스트링 | ML은 티어를 정하지 않는다. step9 `promote`/`demote` 목록에 사유 1개를 더할 뿐이다 |
| ML은 증거 생산자 · `sre_agent` 편입 | D-223 ①② · `plans/101` §0.1·§4.2 | 학습·스코어링은 `sre_agent.ml_batch`, 게이트는 `ml:*` 키 읽기만(101 G-11 (가)) |
| 재현율 우선 | D-048.4 | 강등은 제한적으로, 초기 하한은 DASHBOARD(§4.6) |
| 라벨이 빈약하다 | §2.3 | M0(라벨 확보)이 모든 구현보다 앞선다 |

### 1.3 `plans/101`과의 관계

101은 게이트 신호를 **이상 점수(상향 전용)**로만 설계했다(101 §4.6 · CU-22·CU-23). 이 계획은 101에 없는 **노이즈 분류(강등 방향 자문)**를 더한다. 배치·반입·평가 규약·누수 금지(KL-1~KL-6)는 101을 그대로 따르고 새로 만들지 않는다. 101이 먼저 만드는 산출물(리플레이셋 빌더 · 누수 감사기 · `ml_batch` 골격 · 피드백 이벤트 키 G-9)을 이 계획이 재사용한다.

---

## 2. 현행 실측

### 2.1 게이트 판정 구조 — ML이 들어갈 자리

`noise_gate/domain/notification_policy.py:364` `decide_notification()` 이 단계 순서대로 평가하고 먼저 확정된 단계가 결과다.

| 단계 | 결정 | 코드 기본값 |
|---|---|---|
| (워커) 지문 dedup | `f(db_id, server, alarm_name, resource_name)` SHA-1, 재통보 창 `repeat_interval_seconds`=14400 · 게이트 전에 ACK | 게이트 on일 때 |
| (워커) 최소 심각도 | `1 ≤ severity < ALARM_MIN_SEVERITY` 드롭(코드 기본 2 · 개발 `.env` 1) | — |
| step0.5 비알람 | 마커 규칙 → SUPPRESS | off |
| step1 실효 심각도 | `max(폴스타, AI 상향)` | AI 상향 off |
| **step3 심각도 3** | **항상 PAGE** | 불변 |
| step4 해소 | 자가복구 SUPPRESS / 독립 해소 SUPPRESS(또는 DASHBOARD) | — |
| step5 수집 실패 | 심각도 ≥1이면 PAGE | — |
| step6 유지보수 · 6.2 침묵 · 6.4 의존성 · 6.5 인히비션 · 플래핑 · 7 스톰 · 7.5 상관 · 7.7 계획 주석 | SUPPRESS 또는 DASHBOARD | 침묵 이하 전부 off |
| step8 매트릭스 | 심각도×중요도 | — |
| **step9 보조 조정** | `promote`가 있으면 1단계 승격(강등 무시), 없고 `demote`만 있으면 1단계 강등(하한 SUPPRESS) | — |

step9의 현행 신호(`notification_policy.py` step9 블록):

| 방향 | 신호 | 조건 |
|---|---|---|
| promote | 폴스타 통보 정책 `notify` | — |
| promote | `is_routine=False`(LLM) | — |
| promote | LLM actionability `actionable` | `enable_llm_actionability` |
| promote | 변경 근접 | `change_correlation_enabled` |
| demote | 폴스타 비통보 정책 `suppress` | — |
| demote | `is_routine=True`(LLM) | 실효 심각도 ≤ `suppress_max_severity` |
| demote | LLM actionability `noise` | 위와 같음 + `enable_llm_actionability` |

→ **ML 노이즈 점수는 이 표에 한 행으로 들어간다.** 새 단계를 만들지 않는다.

### 2.2 결정 기록 — 피처·약한 라벨 원천

`noise_gate/infrastructure/decision_store.py:112` `record()` — 알람 이벤트마다 `ts · alarm_id · tier · reason · priority · fingerprint · signals(동결 스키마) · stage` + 선택 `alarm_name · server_name · db_id · resource_name · condition_log · recurrence · correlation_meta · evidence`. 별도 레코드 `type ∈ {resolution, recurrence, investigation, l3_state}`.

- **dedup으로 걸러진 재발은 decision 레코드가 아니라 `recurrence` 레코드로만 남는다**(`alarm_worker.py` 지문 dedup 분기). 학습 표본을 만들 때 이 둘을 합쳐야 발생 빈도가 맞다.
- `signals`는 판단 시점 값이다 → 피처로 쓸 수 있다. `resolution{duration}`·`recurrence`·`investigation{verdict}`는 **사후 값**이다 → 라벨 쪽에만 둔다(101 KL-3).

### 2.3 라벨 원천

| 원천 | 단위 | 상태 | 문제 |
|---|---|---|---|
| 피드백 `logs/alarm_feedback.jsonl`(`feedback_store.py` `record`) | 알람 **유형** — `alarm_name·resource_name·pattern·server_name·db_id·severity·note·labeled_by` (+선택 `investigation_id`) | 개발 `.env`: `NOISE_FEEDBACK_STORE_ENABLED=true` · `NOISE_ENABLE_LLM_ACTIONABILITY=false` | `alarm_id`·`fingerprint` 없음 → 이벤트에 붙일 수 없다(101 G-9가 선택 필드 추가 예정) |
| incident ack/resolve(PG · D-049) | 사건 | `incident_tracking_enabled` 코드 기본 False · 개발 `.env` false | **PAGE만 incident가 된다** → 양성 라벨이 PAGE에 편향 |
| 침묵 규칙 | 알람 패턴 | `silence_enabled` 코드 기본 False | 운영자 억제 의사 — 약한 음성 라벨 |
| SUPPRESS된 알람 | 이벤트 | 카드 미표시 → 피드백 불가 | **선택 편향**: 억제된 알람은 라벨을 다시 못 받는다 |

### 2.4 의존성·반입

- `wheels/requirements_all.txt`(폐쇄망 반입 목록)에 scikit-learn·skops·onnx가 **없다**(grep 0건). 101 §2.5와 같다.
- `sre_agent` venv는 루트와 분리돼 있고 ML 스택 미설치(101 §2.5 실측). `sre_agent/sre_agent/`에 `ml_batch`는 아직 없다(`ls` 실측).

---

## 3. 결정 경계 — 사용자 확정이 필요한 충돌

| 결정 | 현재 내용 | 이 계획이 요구하는 변경 |
|---|---|---|
| D-035 | 판정 = 결정적 규칙 · LLM = 주석 | **변경 없음** — ML 점수는 규칙이 소비하는 입력이고, 강등 여부는 step9 규칙이 정한다 |
| D-048.5 · .11 | "ML 미사용" 명기 | step9 입력에 ML 점수 추가 — **D-048 하위결정 추가**(D-253에서 D-048.12로 연결) |
| D-223 ① | ML은 점수·순위 생산자 · 판정은 규칙 | 변경 없음 |
| D-223 ⑥ · 101 §4.6 | 게이트 신호는 이상 점수 · **escalate-only** | **강등 방향 자문 허용**으로 범위 확장 — G-1 |

G-1에서 (나) "ML이 티어를 직접 판정"을 고르면 D-035·D-048 개정이 필요하다. 이 계획은 (가)를 권고한다.

---

## 4. 설계

### 4.1 예측 대상(라벨) 정의

**"이 알람은 사람이 대응할 가치가 있었는가"**의 이진 분류 확률 `p_actionable`.

| 라벨 | 원천 | 가중 |
|---|---|---|
| 양성(대응 필요) | 피드백 `valid` · incident `acked` · 조사 verdict 확정 | 강 |
| 음성(노이즈) | 피드백 `noise` · 침묵 규칙 매칭 · 자가복구(창 안 해소) + 미ack | 피드백 강 / 나머지 약 |
| 미라벨 | 그 외 | 학습 제외, 평가용 PU 추정에만 |

- 약한 라벨 가중치와 PU(positive-unlabeled) 처리 여부는 G-3.
- 라벨 단위는 **이벤트**다. 피드백은 101 G-9(가) 확장(`alarm_id`·`fingerprint` 선택 필드)이 들어간 뒤부터 이벤트 라벨이 된다. 그 전 피드백은 같은 `alarm_name`의 시간창 안 이벤트에 **약한 라벨**로만 전파한다.

### 4.2 피처 — 알람 도착 **이전** 정보만

| 계열 | 피처 | 원천 |
|---|---|---|
| 이벤트 | 심각도, 중요도, 알람 종류(kind), 자원 유형, 존 | 이벤트 · `noise_ctx` |
| 이력 | 같은 지문·같은 알람명의 1h/24h/7d/30d 발생 수, 간격 CV, 마지막 발생 이후 경과, 사전분류(첫 발생/급증/주기/산발) | `alarm_pattern.py` · decision+recurrence 레코드 |
| 과거 결과 | 같은 알람명의 과거 피드백 비율·incident 전환율(**학습 시점 이전 창만**) | 피드백 · incident |
| 상황 | 시각·요일·월말/월초·업무시간, 같은 서버 동시 알람 수 | 이벤트 시각 · 워커 창 |
| 게이트 신호 | flapping %, 상관 군집 크기, 변경 근접, 이상 z-score | `signals` |

- **금지**: `resolution.duration`·ack 시각·`recurrence` 사후 집계·원인 메모(101 KL-3), 행 순서·ID 차이(KL-1·KL-2), 미래 창 집계(KL-5).
- 알람명 같은 고카디널리티 범주는 학습 구간에서 고정한 인코딩 사전만 쓴다(KL-5).

### 4.3 모델

- **1차**: scikit-learn `HistGradientBoostingClassifier` + 확률 보정(isotonic 또는 Platt · 학습 구간 안 CV). 101 v3가 사건 위험 모델을 GBDT로 고정한 것과 같다.
- **의무 기준선 두 개**: ① 현행 게이트 규칙 그대로 ② "알람명별 과거 noise 비율" 한 줄 규칙. ML이 ②를 못 이기면 채택하지 않는다.
- **하지 않는 것**: 딥 표 모델 · 스태킹 · 다중 시드 앙상블(101 K-9) · 온라인 학습(재기동 시 상태 소실).

### 4.4 배치 — 어디서 무엇이 도는가

```
sre_agent.ml_batch (별도 엔트리 · 주기 실행)
   ├─ 학습(주 1회 등 · G-5): decision/recurrence/feedback/incident 읽기 → 모델 파일(skops) + 메타
   └─ 스코어링(N분 주기): 지문·알람명 단위 p_actionable 계산
        → Redis  ml:noise:{fingerprint}  = {p, model_version, computed_at, top_features}  (TTL = 주기×2)

noise_gate AlarmWorker (본체 프로세스)
   └─ 알람 도착 시 ml:noise:{fingerprint} GET (타임아웃 짧게 · 실패 = 신호 없음)
        → decide_notification(..., ml_noise=...) → step9
```

- **알람마다 모델을 호출하지 않는다.** 피처 대부분이 이력 기반이라 지문 단위로 미리 계산할 수 있다. 워커 지연 0, `sre_agent` 장애 시 키 만료 → 신호 없음 = 현행 동작(101 G-11 (가)와 같은 원리).
- 처음 보는 지문(키 없음)은 신호 없음으로 처리한다. 알람명 단위 폴백 키 `ml:noise:name:{db_id}:{alarm_name}` 사용 여부는 G-4.
- `sre_agent` ↔ `noise_gate` import 0 유지(D-118). 계약은 Redis 키 스키마뿐이다.
- 로그 JSONL은 본체 프로세스의 `logs/`에 있다. `ml_batch`가 그 파일을 읽는 경로(같은 호스트 파일 읽기 vs 본체가 내보내기)는 G-6.

### 4.5 게이트 연계 — 모드

플래그 `NOISE_ML_SIGNAL_MODE ∈ {off, shadow, annotate, advise}`(코드 기본 `off` · 만료일 부여 D-162).

| 모드 | 동작 | 판정 영향 |
|---|---|---|
| off | 키를 읽지 않는다 | 비트 동일 |
| shadow | 읽어서 `signals.ml = {p, model_version, mode}`에 기록만 | 없음 |
| annotate | shadow + 결정 추적·카드에 "ML 대응 필요 확률" 표시 | 없음 |
| advise | step9에 신호 추가 | 1단계 이내 |

- `signals`는 동결 스키마다(`_signals()` "모든 키 필수"). `ml` 키 추가는 스키마 버전 변경이므로 관제 화면·분석 스크립트 소비처를 함께 grep해 갱신한다.

### 4.6 advise 규칙과 안전 가드

```
p ≥ θ_hi                       → promote.append("ML 대응 필요(p=…)")
p ≤ θ_lo  AND 실효심각도 ≤ suppress_max_severity → demote.append("ML 노이즈(p=…)")
```

- 심각도 3은 step3에서 끝나므로 닿지 않는다(불변).
- 승격 신호가 하나라도 있으면 ML 강등은 무시된다(현행 기계 그대로).
- **ML 단독 강등의 하한은 DASHBOARD**로 둔다(G-2). SUPPRESS까지 내리면 카드가 사라져 피드백이 끊기고, 모델이 자기 판단을 검증받지 못한다(§2.3 선택 편향).
- **탐색 표본**: ML 강등 대상 중 비율 ε(예: 5%)은 강등하지 않고 원래 티어로 보내며 `signals.ml.holdout=true`로 표시한다. 오억제율을 측정할 유일한 표본이다(G-7).
- `θ_hi`·`θ_lo`는 학습 구간에서 고정한다(101 평가 규약). 운영 중 자동 조정하지 않는다.

### 4.7 설명 가능성

- 스코어에 상위 기여 피처 3개(`top_features`)를 싣고, 결정 추적 드로어에 "ML이 이렇게 본 이유"로 표시한다(annotate 이상).
- 사유 문자열 형식은 기존 step9와 같다: `· 강등: ML 노이즈(p=0.08)`.

---

## 5. 평가와 채택

| 항목 | 기준 |
|---|---|
| 평가셋 | 101 리플레이셋 빌더 재사용 · 사건 group ∧ 시간순(purged) 분할 · 누수 감사기(101 §5.1) 통과 |
| 주 지표 | **대응 필요 알람 재현율(현행 게이트 대비 하락 0)**을 제약으로 두고, 그 안에서 "통보량(PAGE+TICKET) 감소율" 최대화 |
| 보조 지표 | PR-AUC · 보정 오차(ECE) · 존별·알람 종류별 분리 지표 |
| 기준선 | §4.3 두 개 — 둘 다 이겨야 채택 |
| 섀도 기간 | 월말 2회 포함(101 규약) · shadow에서 "advise였다면 바뀌었을 티어" 카운트를 결정 기록으로 계산 |
| advise 전환 조건 | 섀도에서 재현율 하락 0 · holdout 오억제 0건(또는 G-7 허용치 이하) · 사용자 승인 |
| 표본 수 | 라벨 이벤트 < 20이면 수치 결론을 쓰지 않는다(D-176 ⑤) |

---

## 6. 단계별 작업

| 단계 | 산출물 | 변경 대상 | 선행 |
|---|---|---|---|
| **M0 라벨 확보** | 피드백 이벤트 키(`alarm_id`·`fingerprint`) · incident 추적 운영 on 검토 · 데이터 카드(라벨 수·분포·존별) | `noise_gate/infrastructure/feedback_store.py` · `src/api/routes/alarm.py` 요청 스키마 · `src/static/js/app.js` 전송 필드 · 운영 `.env`(사용자) | 101 G-9 |
| M1 평가셋·기준선 | 리플레이셋에 노이즈 라벨 열 추가 · 기준선 2종 점수 | `sre_agent/scripts/ml/`(101 산출물 확장) | 101 M0·F0 |
| M2 모델 | GBDT + 보정 · skops 저장 · 모델 카드 | `sre_agent/sre_agent/ml/noise/`(신규) | M1 · 반입(G-8) |
| M3 스코어 발행 | `ml_batch` 스코어링 잡 · `ml:noise:*` 키 계약 문서 | `sre_agent/sre_agent/ml_batch`(101) | M2 |
| M4 게이트 연계 | `ml_signal_reader`(개별 try/except · 부분 반환) · `NOISE_ML_SIGNAL_MODE` · step9 행 추가 · `signals.ml` · 드로어 표시 | `noise_gate/infrastructure/ml_signal_reader.py`(신규 · 101 CU-22와 통합) · `noise_gate/domain/notification_policy.py` · `src/config.py` · `src/static/js/noise.js` | M3 |
| M5 섀도→advise | 섀도 보고서 · 전환 결정 · 매뉴얼 갱신(`scripts/manual/content/admin.md` 9장) | 문서 · `.env`(사용자) | §5 조건 |

- 각 단계 테스트: M4는 `off` 비트 동일 회귀 테스트(`noise_gate/tests/test_gate_off_regression.py` 계열) · 심각도 3 불변 · 승격 우선 · DASHBOARD 하한 · holdout 표시 · 키 만료/Redis 실패 시 신호 없음.
- 품질 게이트: `arch_check`(noise_gate의 infrastructure→domain 방향) · `overfit_check`(도메인 독스트링에 폴스타 스키마 리터럴 금지).

---

## 7. 리스크

| # | 리스크 | 완화 |
|---|---|---|
| R-1 | 라벨 부족으로 모델이 기준선을 못 이김 | M0를 먼저 · 기준선 미달이면 중단(채택하지 않는 것도 결과) |
| R-2 | 선택 편향 자기강화 | DASHBOARD 하한 · holdout · 억제 포함(감사) 레벨로 주기 점검 |
| R-3 | 피드백 오염(한 사람이 대량 noise) | `labeled_by`별 기여 상한 · 이상 작성자 제외 규칙(학습 시) |
| R-4 | 분포 이동(신규 서버·알람 정의 변경) | 모델 메타에 학습 구간 · 입력 분포 드리프트 지표 · 만료 시 키 미발행 |
| R-5 | 동결 스키마 `signals` 변경이 소비처를 깸 | M4에서 소비처 grep 전수 갱신 |
| R-6 | 폐쇄망 반입 지연 | 101 반입 협의와 묶음 · 반입 전까지 M0·M1만 진행 |

---

## 8. 사용자 확정 게이트

| ID | 질문 | 선택지 | 권고 |
|---|---|---|---|
| **G-1** | ML의 권한 | (가) step9 자문 신호 (나) 티어 직접 판정 (다) 섀도만 | **(가)** — D-035 유지 · 첫 단계는 어느 쪽이든 (다) |
| G-2 | ML 단독 강등의 하한 | (가) DASHBOARD (나) SUPPRESS | **(가)** — 라벨 루프 유지 |
| G-3 | 약한 라벨 사용 | (가) 피드백·incident만 (나) 침묵·자가복구 약한 라벨 포함(가중) | (나) — 표본 수 확보. 평가는 강 라벨만 |
| G-4 | 처음 보는 지문 | (가) 신호 없음 (나) 알람명 단위 폴백 키 | (가) — 단순 · 재현율 쪽 |
| G-5 | 재학습 주기 | 주 1회 / 월 1회 / 수동 | 월 1회 + 수동 — 모델 버전을 결정 기록에 남긴다 |
| G-6 | `ml_batch`의 로그 읽기 경로 | (가) 같은 호스트 파일 읽기 (나) 본체가 주기 내보내기 | 101 G-11과 함께 결정 |
| G-7 | holdout 비율 · 허용 오억제 | ε=5% · 0건 | 운영 통보량을 보고 결정 |
| G-8 | 반입 | scikit-learn + skops를 101 반입 협의에 포함 | 포함 |

---

## 9. 측정하지 못한 것

- 폐쇄망 운영 `.env`의 게이트·피드백·incident 플래그 실제값.
- 운영 `logs/alarm_feedback.jsonl`·`alarm_decisions.jsonl`의 실제 라벨 수·분포(개발 맥의 파일은 모의 이벤트다).
- 폴스타 `cmm_alarm` ack·note 필드 채움률(101 J-2·J-3 대기).
- scikit-learn 반입 가능 시점.

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-23 | 최초 작성 — 사용자 지시로 ML 노이즈 판정 계획을 별도 파일로. D-253 예약 |
