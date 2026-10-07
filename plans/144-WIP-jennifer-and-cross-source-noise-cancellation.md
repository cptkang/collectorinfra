# 144. 제니퍼 노이즈 캔슬링과 폴스타×제니퍼 복합 캔슬링 — 제니퍼 단독 판정 정책 · 공통 호스트 키 · 크로스소스 사건 상관(원인 알람 아래 증상 알람 강등) · 앱 영향 축 정밀화: 문헌 조사와 구현 계획

> **작성일**: 2026-10-07 · **v1.2**
> **상태**: **WIP — 외부망 구현 W0~W5 완료(작업 트리 · 커밋 없음)** · 게이트 G-1~G-8·Q-1~Q-5 사용자 확정(2026-10-07 「권고대로 진행」) · D-317·D-318 등재 · D-195 ②·D-109 B-6 부기 · **잔여: W6 내부망 shadow 2주·리플레이 지표·창 보정(외부망 불가) · W7(선택) · Q-3 사용자 확인 대기 · J0-L-b 뒤 정책 표·규칙 표 재측정**
> **진행(v1.2 · 2026-10-07)**: ① **Wave A** — Q-4 / D-318 지문 dedup 심각도 상승 우회(전 경로 · `NOISE_DEDUP_SEVERITY_RISE_BYPASS` **기본 on** — 신규 플래그 기본 off 원칙의 예외 · 근거 config 주석·D-318) · §4.4 제니퍼 상관 토큰에 `was_signals` kind·`domain_id`(`apm_noise_context.correlation_extra`) ② **W3** — `config/cross_source_rules.yaml`(3행 · 전부 `enforce: false` · `host_down_to_was_all` 비활성) · `noise_gate/domain/cross_source.py`(EpisodeTracker · 결합 판정) · `noise_gate/infrastructure/cross_source_rules.py`(역방향 demote → link 강제) · 정책 step 7.6 `STAGE_CROSS_SOURCE`(step 8~9.5 결과에 DASHBOARD 상한 — 티어 상승 없음) · `NOISE_CROSS_SOURCE_MODE`(기본 off) · 리플레이 `--cross-source-mode`(목업 shadow: 사건 5 · 했을 강등 2 · 적용 0) ③ **W4** — `app_impact` 사건 저장소 우선(annotate·enforce) · 사후 승격(`NOISE_APP_IMPACT_LATE_PROMOTION_ENABLED` · annotate·enforce만 · 알람당 1회 · 트리거당 5건 · `type="late_promotion"`) · 정상 강등 shadow(`NOISE_APM_HEALTHY_DEMOTION_SHADOW`) · 제니퍼 조사 사건당 1회 ④ **W5** — 통보문 사건 묶음 줄(annotate·enforce) · 관제 JS 단계·근거 라벨 · 사건 피드백 2동작(`POST /api/v1/admin/noise/episode-feedback` · 운영자 전용 — 관리자 화면 토큰이 `type=admin`이라 `/alarm/feedback` 불가 · D-245 사용자 경로 읽기 전용) · 매뉴얼 A-71 신설·A-60·A-45·U-40·U-44·U-45 갱신(캡처 없음 — 녹화 픽스처에 크로스소스 기록 없음) ⑤ 검증 verifier Critical·Major 0 · Minor 5 교정(`cause_ids` 상한 · 피드백 입력 길이·제어문자·형식 · 사후 승격 shadow 제외 · 트리거당 상한 · sweep 간격) · Q-5 ② 배지 플래그 보류
> **진행(v1.1 · 2026-10-07)**: ① **W0** 목업 `--with-jennifer`(시나리오 15~18 · `--dump`) · 리플레이 하네스 `noise_gate/scripts/replay_noise.py`(noise_ctx 공급 함수 주입) · 픽스처 `noise_gate/testdata/cross_source/mock_events.jsonl` — 기준선 재현: 제니퍼 미해소 14건 전부 `collection_failed` PAGE · sev3 1건 심각도3 PAGE · recovery 5건 resolved ② **W1(게이트웨이)** `raw.apm.event_type_norm` 추가(어댑터가 정규화 · domain은 옮기기만) ③ **W1(noise_gate)** 플래그 `NOISE_APM_NOISE_POLICY_ENABLED`(기본 off = 비트 동일) · 공급자 `noise_gate/infrastructure/apm_noise_context.py` · 정책 표 `config/apm_noise_policy.yaml`(11종 + default · suppress 없음 · 로더는 suppress→dashboard 강제) · 지속 조건(이 사유만이면 하한 DASHBOARD — G-1 (b) 보수) · 정규화 지문·해소 짝맞춤(플래그 on 제니퍼만) · evidence `apm_policy` · **이탈**: 워커 지문 dedup(4h)이 두 번째 이벤트를 게이트 전에 버려 §4.2 전제가 성립하지 않음 → 지속 조건이 채워지는 순간 dedup 1회 해제 재판정(`persistence.reevaluated`) + 정책 경로에서 심각도 상승은 dedup 우회 ④ **W2** `AlarmEvent.host_key`·`host_key_strength`(게이트웨이 실측 어휘 `host_name` — §5.2·§5.5 정정) · 등록명 배지 역조회는 기본 off 플래그가 필요해 미구현(설계 메모 §12) · 검증 verifier Critical 0
> **회귀(모듈 단위 · v1.2 계획 끝 · 2026-10-07)**: `regress.py --base c161bfd --files <회귀 대상 58개>` — 원래 실패 60 · 자기 귀속 0 · apm_gateway 1,576 통과 · arch/overfit/mypy 통과 · ruff 이번 diff 줄 3건(주석) 교정 후 `--no-tests` rc=0 · `범위: 모듈 단위 — 전체 미실행` · 교정 라운드 모듈 선택(교정 3파일 + 테스트) 원래 실패 58 · 자기 귀속 0 · `[전체 회귀 권고]` src.config 허브 88% · `parse_menu_input` 반환 주석 · 직접 선택 비율 42%(사용자 요청 시 `--full`) · 형제 venv(`../collectorinfra/.venv`)로 실행 · (v1.1 회귀: 25개 rc=0 · 원래 실패 60)
> **요청(사용자 2026-10-07)**: *"현재 폴스타 노이즈 캔슬링 기능만 있다. 제니퍼의 노이즈 캔슬링 기능을 구현하려고 한다. 또한 폴스타의 알람과 제니퍼를 복합적으로 검토하여 캔슬링하는 기능을 만들려고 한다. 관련 문헌과 논문을 찾아 위 요건에 맞는 계획파일을 작성하라."*
> **입력**: 저장소 `584a80f` 읽기·grep 실측(서브에이전트 2갈래 — `noise_gate` 제니퍼 처리 지도 · 문헌 조사) · 동반 문헌 조사 문서 [`docs/aiops_benchmark/apm_cross_source_noise_literature.md`](../docs/aiops_benchmark/apm_cross_source_noise_literature.md)
> **관련 계획**: `plans/52`(게이트 설계 정본) · `plans/60`(E1~E8 · §13 노이즈 문헌) · `plans/87`(제니퍼 연동 — §5.5 이벤트 폴러·`app_impact` · U-1·U-4·U-13 · J0-L-b) · `plans/55`(멀티소스 로드맵 — 「사용자 영향」 축 · cross-source 상관 자리) · `plans/112`(관제 드릴다운) · `plans/117`(ML 노이즈 판정 — 모드 사다리 off→shadow→annotate→advise) · `plans/101`(통합 이벤트 스키마 · 리플레이셋) · `plans/130`(인스턴스·업무명 해석) · `plans/134`(게이트웨이 이벤트 버퍼 W3)
> **관련 결정**: **D-048**(4-티어 · 결정적 판정 · 심각도3 절대 PAGE · 수집 실패 → 보수적 PAGE · 억제≠삭제) · **D-109**(상관은 db_id(존) 경계 안 — B-6) · D-111(변경 상관 — 폴스타 전용) · D-112(위상 가중 — 폴스타 토폴로지 전용) · **D-195 ②**·**D-274 ⑤**(WAS 판정은 게이트웨이 `domain/` 단일 정의 · `app_impact`는 승격 전용) · D-287 ④(제니퍼 소스 ↔ 존 정본은 레지스트리) · D-188(hostname 역조회) · D-177(피드백) · D-035(LLM은 판정자 아님) · D-139(패키지 경계) · D-243(미연동 한 줄 경고) · D-255(매뉴얼 동반) · D-303(회귀)
> **D-번호**: **D-317**(본문 등재 2026-10-07) · **D-318**(Q-4 지문 dedup 심각도 상승 우회) · D-195 ②·D-109 B-6 부기
> **계획 시작 SHA**: `c161bfd2a3209590041fcdef18052458e075cebb`
> **회귀 대상 파일**: `.env.example` `apm_gateway/apm_gateway/adapters/jennifer/fields.py` `apm_gateway/apm_gateway/domain/events.py` `apm_gateway/tests/test_plan144_event_type_norm.py` `apm_gateway/tests/test_plan144_verify_gw.py` `apm_gateway/tests/test_poller.py` `config/apm_noise_policy.yaml` `config/cross_source_rules.yaml` `config/settings_help/noise_gate_core.yaml` `config/settings_help/noise_gate_investigation.yaml` `docs/31_jennifer_integration_guide.md` `noise_gate/application/alarm_worker.py` `noise_gate/application/nodes/alarm_context_enricher.py` `noise_gate/application/nodes/alarm_notifier.py` `noise_gate/application/nodes/investigation_trigger.py` `noise_gate/application/nodes/notification_gate.py` `noise_gate/domain/alarm.py` `noise_gate/domain/cross_source.py` `noise_gate/domain/investigation_payload.py` `noise_gate/domain/notification_policy.py` `noise_gate/infrastructure/apm_noise_context.py` `noise_gate/infrastructure/cross_source_rules.py` `noise_gate/infrastructure/decision_store.py` `noise_gate/infrastructure/feedback_store.py` `noise_gate/orchestration/alarm_graph.py` `noise_gate/scripts/mock_polestar_events.py` `noise_gate/scripts/replay_noise.py` `noise_gate/testdata/cross_source/mock_events.jsonl` `noise_gate/tests/fixtures/decision_snapshot.json` `noise_gate/tests/test_decision_snapshot.py` `noise_gate/tests/test_decision_stage.py` `noise_gate/tests/test_mock_polestar_events.py` `noise_gate/tests/test_plan144_apm_policy.py` `noise_gate/tests/test_plan144_cross_source.py` `noise_gate/tests/test_plan144_host_key.py` `noise_gate/tests/test_plan144_verify.py` `noise_gate/tests/test_plan144_verify2.py` `noise_gate/tests/test_plan144_w4.py` `noise_gate/tests/test_plan144_w5.py` `noise_gate/tests/test_plan144_wave_a.py` `noise_gate/tests/test_replay_noise.py` `scripts/manual/content/admin.md` `scripts/manual/content/user.md` `scripts/manual/features.yaml` `spec/SPEC-apm-gateway.md` `src/api/routes/alarm.py` `src/api/routes/noise_dashboard.py` `src/api/settings_catalog.py` `src/config.py` `src/static/admin/noise.html` `src/static/index.html` `src/static/js/app.js` `src/static/js/noise-help.js` `src/static/js/noise.js` `src/static/manual/admin.html` `src/static/manual/user.html` `src/static/noise.html` `tests/test_api/test_plan144_verify2_api.py` `tests/test_api/test_plan144_w5_episode_feedback.py` `tests/test_api/test_settings_catalog.py`

---

## 0. 증거 규칙

- 과금 호출 0 · DB 호출 0 · 서버 기동 0 · 실 제니퍼 호출 0. 이 계획서의 앵커는 저장소 읽기·grep으로 실측했다.
- **운영 제니퍼 이벤트의 실제 모양은 모른다.** 이벤트 유형 전 목록(U-1·U-13) · `hostName` 형식(U-4) · recovery 이벤트가 발생과 같은 유형·인스턴스로 오는지는 `plans/87` J0-L-b(라이선스 있는 환경 재현) 전까지 미확정이다. 이 계획은 설치본 기본 룰(`apm_gateway/apm_gateway/adapters/jennifer/fields.py:60-72` `EVENT_TYPE_SIGNALS` 11종)을 기준으로 쓰고, 운영 값이 오면 §2.3 표를 다시 잰다.
- 문헌의 서지는 동반 문서 §0·§1의 「확인」 표기를 따른다. **「(서지 확인)」·「미재확인」 항목은 원문을 열지 않았다** — 구현 주석에 인용하기 전에 원문을 확인한다.

## 1. 결론

### 1.1 한 줄

**제니퍼 알람은 지금 노이즈 캔슬링을 받지 못한다.** 게이트를 켜면 미해소 제니퍼 알람은 거의 전부 「신호 수집 실패 → 보수적 PAGE」로 끝나고, 폴스타와 제니퍼를 엮는 코드는 폴스타 알람을 올리기만 하는 `app_impact` 하나다. 이 계획은 세 겹으로 메운다. ① 제니퍼 전용 노이즈 컨텍스트(유형별 정책·지속 조건·해소 짝맞춤)로 제니퍼 단독 판정을 성립시킨다. ② 두 소스가 같은 서버를 같은 이름으로 부르도록 **공통 호스트 키**를 만든다. ③ 같은 존·같은 호스트·사건창 안의 폴스타 알람과 제니퍼 이벤트를 **한 사건(episode)으로 묶고**, 규칙 표가 정한 원인→증상 방향이 확인될 때만 증상 쪽을 **DASHBOARD로 강등**한다(SUPPRESS 아님 · 심각도3 불변 · 매핑 불확실하면 묶기만). 모든 신규 동작은 off → shadow → annotate → enforce 사다리로 켠다.

### 1.2 왜 이 모양인가

| 제약 · 근거 | 설계 반영 |
|---|---|
| 판정은 결정적 규칙(D-035 · D-048) · LLM RCA 신뢰도 한계(OpenRCA 최고 11.34% · LLM-Fail 2026) | 억제·강등은 규칙 표 + 순수 함수가 정한다. LLM은 사건 요약·통보 문구에만(§7 W7은 주석 한정) |
| 재현율 우선 · 억제≠삭제(D-048.4) · 크로스소스 거짓 억제 비용 연구 공백(문헌 §3-3) | 크로스소스 조치 상한은 **DASHBOARD 강등**. SUPPRESS 승격은 shadow 측정 후 별도 결정 |
| 토폴로지 불완전이 사건 관리의 주 실패 원인(IcM BRAIN, FSE'20 Industry) | 매핑 신뢰도가 `override`·`hostName 정확 일치`일 때만 강등. 정규식 정합·미해소는 묶기만 |
| 시간창만 쓰면 무관 사건이 합쳐지고 구조만 쓰면 시간 무관 병합(GRLIA ASE'21 · DiLink preprint) | 결합 조건 = **같은 존 AND 같은 호스트 키 AND 사건창 AND 규칙 표의 방향** |
| 업계 억제 문법 = 원인(source)·증상(target)·같아야 할 키(equal)(Alertmanager `inhibit_rules`) · 상관 규칙 = {소스, 태그, 창, 필터}(BigPanda) | 규칙 표 `config/cross_source_rules.yaml`의 필드를 이 문법에 맞춘다 |
| 단발 임계 이벤트는 반복 노이즈가 된다 · 다중 창 지속 조건(Google SRE Workbook) | 제니퍼 지표형 경고 이벤트에 **지속 조건**(창 안 k회)을 둔다 |
| 인과 추론 RCA는 「모든 상황에서 우수한 방법 없음 · 합성 성능이 실환경과 괴리」(RCA-HowFar ASE'24) | Sage·CIRCA류 인과 모델은 도입 보류. 정적 규칙 + 시간 순서 검사로 출발 |
| 「APM 정상 → 인프라 알람 하향」을 직접 평가한 동료심사 연구 없음(문헌 §3-2) | 이 축은 **shadow 전용**으로만 만든다(G-5) |

### 1.3 범위 밖

- 제니퍼 이벤트를 다시 탐지하는 이상 탐지(Donut·OmniAnomaly류) — 제니퍼가 이미 임계로 만든 이벤트를 **후처리**하는 것이 이 계획이다. 지표 재탐지는 `plans/101`.
- ML 노이즈 확률 — `plans/117`. 이 계획의 사건(episode) 기록과 피드백이 117의 라벨 원천이 되도록 필드만 맞춘다(§6.4).
- 트레이스 기반 RCA — 제니퍼 이벤트 입력만으로는 불가(문헌 C-4).
- DPM(DB 성능) 소스 — `plans/55` 범위. 규칙 표 스키마는 소스 이름을 열어 두어 나중에 데이터로 더한다.

---

## 2. 현행 실측

### 2.1 제니퍼 이벤트가 게이트에 들어오는 경로

`apm_gateway` 폴러(`apm_gateway/apm_gateway/application/poller.py:195,225-253`, 기본 `enabled=False` · 30초 주기 · `min_level=warning`, recovery·clear는 항상 통과)가 Redis Stream `alarm:raw`에 XADD → `noise_gate` 워커가 폴스타와 같은 파서로 `AlarmEvent`를 만든다(`noise_gate/domain/alarm.py:60-90`).

| 필드 | 제니퍼 값(`apm_gateway/apm_gateway/domain/events.py:88-160`) |
|---|---|
| `dbId` | `jennifer` 또는 `jennifer_<source_id>` |
| `serverName` | 게이트웨이가 정합한 hostname, 실패하면 **인스턴스 이름**(폴스타 등록명 아님) |
| `hostname` | `resolver.reverse` — override → `Instance.hostName` → 정규식 순 · 실패 시 `""`(`application/resolver.py:354-389`) |
| `resourceType` / `resourceName` | `apm.Instance` / 인스턴스 이름 |
| `alarmName` | `event_type` = `errorType`(있으면) 또는 `metricsName` — **접두 `ERROR_`·`WARNING_` 정규화 전 원문** |
| `severity` | fatal·critical=3 · warning=2 · normal=1 · recovery·clear=0 · 미지 레벨=2(`apm_gateway/config/event_levels.yaml`) |
| `raw.apm` | `match_confidence` · `match_reason` · `was_signals` · `idempotency_key` · instance/domain id · txid |

### 2.2 게이트 단계별로 제니퍼 알람에 실제로 일어나는 일

| 단계 | 위치 | 제니퍼 |
|---|---|---|
| 핑거프린트 재발 억제(4h) | `alarm_worker.py:735-765` · 키 `notification_policy.py:239-252` | **동작** — (db_id, server, event_type 원문, 인스턴스) |
| min_severity · 심각도3 단락 · 비알람 사전분류 | worker:769 · policy:564·573 | 동작 |
| 해소/자가복구 | worker:1266-1308 · policy:579-592 | 형식상 동작 — recovery가 같은 `alarmName`으로 와야 짝이 맞는다(미확인) |
| **노이즈 컨텍스트 수집** | `polestar_noise_context.py:499-504` | **미등록 db_id → 즉시 `source="unavailable"`** |
| **step 5 수집 실패 → PAGE** | `notification_policy.py:595-598` | **미해소 심각도 1~2 제니퍼 알람이 전부 여기서 확정** |
| 유지보수·침묵·의존성·인히비션·스톰·플래핑·크로스호스트 상관·매트릭스·보조 조정 | policy:600-791 · worker:1310-1698 | 워커가 탐지값을 계산해도 **판정에 도달하지 않는다** |
| 변경 상관 · 폴스타 이력 · STL 베이스라인 · L3 보강 | `alarm_context_enricher.py:494-506` · `polestar_metric_baseline.py:201-204` · `alarm_notifier.py:487-492` | 폴스타 전용 — 제니퍼는 None 또는 명시 생략 |
| 조사 트리거 | `investigation_trigger.py:79-96` | PAGE면 발동 — hostname이 해소된 경고 이벤트마다 `sre_agent` 조사가 나갈 수 있다(운영 영향은 미측정) |

정책(`notification_policy.py`)에는 제니퍼 분기가 없다. 출처 분기는 `is_apm_event`(`domain/process_rank.py:27,58-60`) · `is_apm_source`(`application/server_identity.py:114-123`) 두 판정과 그 소비처 4곳(gate:209 · trigger:95 · payload:107 · notifier:487)뿐이다.

### 2.3 폴스타 ↔ 제니퍼 결합 — `app_impact` 하나

`notification_gate.py:91-116,175-293` · 정책 step 9.5 `notification_policy.py:774-791`.

- 발동: `NOISE_APP_IMPACT_ENABLED`(기본 False) AND 폴스타 알람 AND 매트릭스 결과가 DASHBOARD·TICKET → **PAGE로 승격만**.
- 매칭: 폴스타 `hostname`을 게이트웨이 `apm_events`에 넘겨 게이트웨이가 인스턴스로 정합(`apm_gateway/.../tools.py:2085-2130`) · 창 `[alarm_time − 10분, alarm_time]` 과거만 · fatal·critical 행만 센다 · 알람 존의 소스로 좁힌다.
- **역방향 없음** — 제니퍼 알람을 폴스타 맥락으로 강등·병합하는 경로가 없다. 폴스타 알람이 `unavailable`이면 매트릭스에 도달하지 않아 `app_impact`도 발동하지 않는다.
- 같은 사건을 묶는 단위가 없다 — 크로스호스트 상관 스코프는 `event.db_id`(`alarm_worker.py:1605` · D-109 B-6)라 `polestar_*`와 `jennifer*`는 서로 다른 스코프다. 인히비션·스톰 스코프 `db_id|server_name`도 같다. 위상 그래프는 폴스타 `AVAIL_DEPEND_RESOURCE_ID` 전용(`topology_loader.py:41-58`).

### 2.4 비어 있는 것(사실)

| # | 공백 | 근거 |
|---|---|---|
| E-1 | 제니퍼 단독 판정 정책 없음 — 유형별 `notify` 정책은 `plans/87` §5.5에 계획만 | `event_levels.yaml`에 notify 키 없음 · policy step 5 |
| E-2 | 제니퍼용 노이즈 컨텍스트 수집기 없음(중요도·유지보수·통보 정책 원천) | `polestar_noise_context.py:499` |
| E-3 | 두 소스의 서버 식별이 통일되지 않음 — 제니퍼 `serverName`은 hostname 또는 인스턴스명 | `server_identity.py:205-266` · `polestar_hostname_resolver.py:529` |
| E-4 | 크로스소스 사건 단위 · 증상 강등 없음 | §2.3 |
| E-5 | 유형 표기 정규화(`ERROR_` 접두)가 게이트웨이에만 있다 — 핑거프린트·해소 짝맞춤이 원문으로 비교 | `fields.py:75-101` vs `notification_policy.py:239` |
| E-6 | 제니퍼 알람을 정책까지 통과시키는 테스트·목업 없음 | `noise_gate/scripts/mock_polestar_events.py` 제니퍼 0건 · 테스트는 `test_plan87_apm_consumer.py`·`test_plan87_j8_multi_source.py`(계약·배지·`app_impact`)만 |

---

## 3. 문헌 조사 요약

전체 표(동료심사 26 · preprint 4 · 산업 7)·정정 기록·평가 지표는 동반 문서에 있다. 여기에는 설계에 직접 쓰는 것만 옮긴다.

### 3.1 동료심사 — 설계에 직접 쓰는 것

| 약칭 | 서지 | 차용 요소 | 반영 절 |
|---|---|---|---|
| AlertStorm | N. Zhao et al., ICSE-SEIP 2020(상업은행 적용) | 폭풍 구간 탐지 → 구간 안 대표 알람만 노출하는 2단 구조 | §5.3 사건 대표 · W3 |
| AlertRank | N. Zhao et al., IEEE INFOCOM 2020 | 과거 조치율 등 특징으로 심각 알람 선별 — 피드백 라벨이 쌓인 뒤의 후보 특징 | §6.4(117 연계) |
| IcM BRAIN | Z. Chen et al., ESEC/FSE 2020 Industry | 의존 정보 불완전이 핵심 난점 → **매핑 불확실하면 억제 금지** | §5.2 신뢰도 문턱 |
| LiDAR | Y. Chen et al., ESEC/FSE 2020 | 운영자가 묶은 사건 이력을 연결 라벨로 축적 | §6.3 피드백 |
| GRLIA | Z. Chen et al., ASE 2021 | 시간 인접 + 위상 인접을 함께 쓰는 집계 | §5.1 결합 조건 |
| COLA | J. Kuang et al., ICSE-SEIP 2024(arXiv 코멘트 기준) | 통계로 확정되는 쌍은 코드, 애매한 쌍만 LLM — **LLM은 주석만** | W7 |
| MicroRCA | L. Wu et al., NOMS 2020 | 앱 성능 증상 ↔ 같은 머신 자원 지표 연결 · 원인 순위 | §5.1 규칙 방향 · 원인 우선순위 |
| Groot | H. Wang et al., ASE 2021(eBay) | 알람·이벤트 단위 그래프 + SRE 규칙 주입 | 규칙 표 구조 |
| CloudRCA | Y. Zhang et al., CIKM 2021 | 이질 소스(KPI·로그·토폴로지) 융합 + 운영자 지식 주입 | 규칙 표 · 매핑 오버라이드 |
| EvTS | C. Luo et al., KDD 2014 | 이벤트-시계열 상관의 **시간 순서** 검정 → 원인/증상 방향 | §5.1 선행 조건 · W6 보정 |
| TASA · WINEPI | Klemettinen et al., JNSM 1999 · Mannila et al., DMKD 1997(서지 미재확인) | 창 폭 W를 명시 파라미터로 두고 빈발 에피소드로 상관 규칙 발굴 | W6 창 보정 · W7 규칙 제안 |
| RCA-HowFar | L. Pham et al., ASE 2024 | 인과 RCA 도입 보류 근거 | §1.2 |
| RCACopilot · LLM-RCM · OpenRCA | EuroSys 2024 · ICSE 2023 · ICLR 2025 | LLM은 원인 범주 서술 · 권고 — 억제 판정 근거 아님 | §1.2 |

### 3.2 산업 자료(벤더 수치는 독립 검증 없음)

Alertmanager `inhibit_rules`(source/target/equal) · Google SRE Workbook 다중 창 번레이트 · Dynatrace(수직 인프라 의존 + 수평 서비스 경로 2축) · BigPanda 상관 패턴 · Splunk ITSI episode(분할·종료 기준) · PagerDuty(내용 기반·시간 기반 그룹핑 분리). → 규칙 표 필드(§5.1)와 사건 종료 조건(§5.3)에 대응시킨다.

### 3.3 공백 — 보수 설계의 근거

1. 제니퍼류 **벤더 APM 이벤트의 후처리 노이즈 캔슬링을 평가한 동료심사 연구는 찾지 못했다**.
2. 「APM 정상 → 인프라 알람 하향」의 정확도·누락률을 평가한 연구는 찾지 못했다.
3. 크로스소스 억제의 **거짓 억제 비용**을 보고한 연구가 드물다 — 대부분 집계 F1·RCA top-k만 보고한다.
4. 창 크기의 일반 법칙이 없다 — 환경별 지연 분포로 정해야 한다(분위수 방식은 이 계획의 **설계 가정**이다).
5. 공개 데이터셋(RCAEval · LEMMA-RCA · OpenRCA)은 RCA용이라 알람 억제 평가에 쓸 수 없다 → **자체 리플레이 + shadow**가 유일한 평가 수단.

---

## 4. 설계 A — 제니퍼 단독 노이즈 캔슬링

### 4.1 APM 노이즈 컨텍스트 공급자 (E-1 · E-2)

`noise_gate/infrastructure/apm_noise_context.py`(신규) — 폴스타 `PolestarNoiseContext`와 **같은 계약 키**(`_NOISE_CTX_KEYS` 단일 출처)를 돌려준다. enricher는 `is_apm_source(event)`이면 이 공급자를, 아니면 폴스타 공급자를 부른다(`alarm_context_enricher.py:494-506`).

| 계약 키 | 제니퍼 원천 | 없을 때 |
|---|---|---|
| `importance` | ① 인스턴스·업무 오버라이드(`config/apm_noise_policy.yaml` `instances:` — 업무 중요 인스턴스) ② 유형 정책의 기본 중요도 | 「보통」(D-048.3 미매핑=보통 보수) |
| `maintenance` | 침묵 규칙(`silence_store`)이 `db_id=jennifer*`·인스턴스·유형으로 매칭되면 True | None |
| `noti_policy` | 유형 정책 `notify: page` → `"notify"` · 그 밖 → None | None — **`"notify"`를 기본값으로 두지 않는다**: step 9가 `"notify"`를 승격 사유로 쓰고(`notification_policy.py:740-741`) 승격 우선 규칙이 같은 알람의 강등 사유(§4.2 지속 조건 등)를 무시하므로, 기본 `"notify"`면 모든 제니퍼 알람이 한 단계 올라간다 |
| `parent_avail_status`·`cascaded`·`change_*` | 없음 | None |
| `source` | `"apm_policy"` | 정책 파일 적재 실패 → `"unavailable"`(현행 PAGE 그대로) |

- 플래그 `NOISE_APM_NOISE_POLICY_ENABLED`(기본 **False** = 현행 `unavailable → PAGE` 비트 동일).
- 유형 정책 표 `config/apm_noise_policy.yaml`(신규) — 키는 **정규화 유형**(§4.3). 열: `notify`(page·dashboard·suppress) · `importance`(높음·보통·낮음) · `persistence`(§4.2) · `kind`(게이트웨이 `was_signals` kind와 같은 어휘). 초기 행은 `EVENT_TYPE_SIGNALS` 11종 + 「미지 유형」 기본값. **`notify: suppress`는 G-1 답 전까지 쓰지 않는다.**
- WAS 판정 규칙을 noise_gate에 다시 만들지 않는다(D-274 ⑤). 정책 표는 **통보 정책**이고, 유형 → WAS 시그니처 kind 판정은 게이트웨이가 실어 보낸 `raw.apm.was_signals`를 읽는다.

### 4.2 지속 조건 — 지표형 경고 이벤트 (SRE Workbook 다중 창)

- 대상: `event_kind="metric"` AND 심각도 2(심각도3은 D-048 불변).
- 같은 핑거프린트가 `persistence.window_seconds`(초기 300) 안에 `persistence.min_count`(초기 2)회 이상이면 매트릭스대로, 미달이면 step 9 보조 조정 `demote`(사유 「지속 조건 미달」) → 1단계 강등 상한(현행 step 9 규칙)을 그대로 따른다.
- 첫 이벤트를 붙잡아 두지 않는다(지연 0). 두 번째가 오면 그때 매트릭스대로 다시 판정된다 — 첫 이벤트는 DASHBOARD에 이미 기록돼 있다.
- 워커 in-memory 카운터는 키 만료 sweep(Known Mistakes).

### 4.3 유형 표기 정규화와 해소 짝맞춤 (E-5)

- 게이트웨이가 `raw.apm.event_type_norm`(접두 제거 대문자 — `normalize_event_type` 결과)을 **추가로** 싣는다. `alarmName`은 바꾸지 않는다(기존 핑거프린트·관제 이력 보존).
- noise_gate 핑거프린트·해소 짝맞춤·정책 표 조회는 제니퍼 알람이면 `event_type_norm`을 쓴다(없으면 원문 — 구버전 게이트웨이 호환).
- recovery가 발생과 같은 유형으로 오는지는 **U-1(J0-L-b)로 확인**한다. 다르게 오면(예: 지표 회복이 `metricsName`만 바뀌어 옴) 정책 표 `recovers:` 열로 발생 유형 ↔ 회복 유형 대응을 데이터로 둔다.

### 4.4 제니퍼 도메인 단위 폭풍 (AlertStorm 2단)

- 같은 제니퍼 도메인(`domain_id`)의 여러 인스턴스가 같은 WAS kind(예: `was_db_pool_exhaustion`)를 짧은 창에 동시에 내면 공통 원인(DB·외부 시스템) 가능성이 높다.
- 정책이 매트릭스까지 도달하면 기존 크로스호스트 상관(step 7.5 · 스코프 `event.db_id` = `jennifer_<src>`)이 그대로 돈다. 상관 토큰에 `was_signals` kind와 `domain_id`를 더하는 것만 한다(`correlation.signature_tokens` 확장 — 제니퍼 알람일 때만). 존 경계(D-109 B-6)는 소스 ↔ 존 레지스트리(D-287 ④)로 이미 지켜진다.

### 4.5 조사 트리거 폭주 방지

정책이 성립하면 경고 이벤트가 PAGE에서 내려오므로 트리거 수가 자연히 준다. 추가로 제니퍼 알람은 **사건(§5.3) 단위로 1회만** 조사를 제출한다(같은 사건의 후속 알람은 기존 조사 id를 감사에 남김).

---

## 5. 설계 B — 폴스타 × 제니퍼 복합 캔슬링

### 5.1 규칙 표 — `config/cross_source_rules.yaml`(신규)

Alertmanager inhibit 문법(source·target·equal)에 방향·창·조치를 더한다. 원인 우선순위는 MicroRCA·Dynatrace 수직 계층(자원 → 프로세스 → 앱)을 기본값으로 둔다.

```yaml
version: 1
rules:
  - id: host_resource_to_was_latency      # 서버 자원 포화 → WAS 지연·큐잉
    cause:   {source: polestar, kinds: [cpu, memory]}          # classify_alarm_kind 어휘
    effect:  {source: jennifer, was_kinds: [was_service_queuing, was_gc_stall, was_heap_pressure]}
    equal:   [zone, host_key]
    window:  {cause_before_seconds: 600, cause_after_seconds: 60}   # §5.4 도착 지연 허용
    action:  demote_effect            # link | demote_effect (SUPPRESS 없음 — G-2)
  - id: host_down_to_was_all          # 서버 다운 → 그 위 인스턴스 이벤트 전부
    cause:   {source: polestar, alarm_names: ["<서버 가용성 알람명 — 미확정>"]}   # kind 어휘에 「다운」이 없다
    effect:  {source: jennifer, was_kinds: ["*"]}
    equal:   [zone, host_key]
    window:  {cause_before_seconds: 900, cause_after_seconds: 120}
    action:  demote_effect
  - id: was_oom_to_process_down       # 역방향 — WAS OOM → 폴스타 java 프로세스 다운
    cause:   {source: jennifer, was_kinds: [was_heap_pressure]}
    effect:  {source: polestar, kinds: [process]}
    equal:   [zone, host_key]
    window:  {cause_before_seconds: 300, cause_after_seconds: 60}
    action:  link                     # 역방향은 G-3 답 전까지 link만
```

- 폴스타 쪽 `kinds`는 `classify_alarm_kind` 어휘(`noise_gate/domain/process_rank.py:84` — cpu·memory·disk·network·process·log)를 쓴다. 이 어휘에 「서버 다운(가용성)」이 없으므로 그런 원인은 `alarm_names`(폴스타 알람 정의 이름 — 운영 값 확인 필요)로 지정한다.
- 제니퍼 쪽 `was_kinds`는 게이트웨이 `was_signals` kind 어휘(`fields.py:60-72`)를 쓴다(D-274 ⑤ — noise_gate는 판정을 다시 만들지 않는다).
- 초기 행의 kind 조합과 창 값은 **설계 가정**이다 — W6 리플레이의 지연 분포로 보정한다(§8.2).

### 5.2 결합 조건과 조치 (모두 결정적 · 순수 함수 `noise_gate/domain/cross_source.py`)

증상 알람 하나를 강등하려면 다음을 **전부** 만족해야 한다. 하나라도 빠지면 `link`(사건에 묶고 주석만)로 내려간다.

1. 같은 존 — 폴스타 알람 존 == 제니퍼 소스 존(D-287 ④ 레지스트리). D-109 B-6 존 경계 유지.
2. 같은 `host_key`(§5.5)이고 **양쪽 신뢰도가 강함**(폴스타: hostname 실값 · 제니퍼: `match_reason ∈ {override, host_name}` — 게이트웨이 실측 어휘 `override`·`host_name`·`regex`·`unresolved`(W2 정정)). 정규식 정합·미해소·`ambiguous`면 강등 금지(IcM BRAIN).
3. 규칙 표 방향이 맞고, 원인 알람이 창 안에 있다(EvTS 시간 순서).
4. 원인 알람이 **이미 PAGE 또는 TICKET으로 통보됐다** — 원인을 사람이 보고 있을 때만 증상을 내린다(Alertmanager inhibit의 「source firing」 조건).
5. 증상 알람 심각도 < 3(D-048 절대 PAGE).

조치는 step 7.6(신규 · `STAGE_CROSS_SOURCE`, 크로스호스트 상관 뒤 · 주석 강등 앞)에서 **DASHBOARD 강등**만 한다. 사유 문자열에 규칙 id·원인 alarm_id·호스트 키·시차를 남긴다(억제≠삭제 · 관제 드릴다운 근거 키).

### 5.3 사건(episode) 단위

- 워커 in-memory `_episodes`(키: `zone|host_key`) — 열린 사건에 폴스타·제니퍼 알람을 붙인다. 종료 조건(Splunk ITSI break): 마지막 알람 후 `episode_idle_seconds`(초기 900) 경과 또는 소속 알람 전부 해소. 키 만료 sweep 필수.
- 사건 대표(AlertStorm 2단): 규칙 방향상 가장 하위 계층 원인 알람. 대표가 없으면(원인 미도착) 먼저 온 알람.
- 사건 id는 `decision_store` 레코드 · `alarm:incident` 이벤트 · 조사 트리거 페이로드에 싣는다(§4.5 · `plans/117` 라벨 조인 키).
- 재기동 시 사건 상태는 비어 있다(현행 상관 클러스터와 같은 수준) — Redis 영속은 W6 측정 뒤 필요하면 G-8로 판단.

### 5.4 도착 순서 — 붙잡아 두지 않는다

폴스타는 TCP 실시간, 제니퍼는 30초 폴링 + 겹침 재조회라 **증상(제니퍼)이 원인(폴스타)보다 늦게 오는 것이 보통**이고, 반대로 폴링 지연 때문에 원인이 늦게 올 수도 있다.

| 순서 | 동작 |
|---|---|
| 원인 먼저 → 증상 나중 | §5.2 조건이면 증상 DASHBOARD 강등 |
| 증상 먼저 → 원인 나중 | 증상은 이미 판정·통보됐다 — **철회하지 않는다**. 원인 통보문에 「연관 제니퍼 이벤트 N건(이미 통보됨)」을 붙이고 사건에 묶는다 |
| 원인이 `cause_after_seconds` 안에 늦게 옴 | 다음 증상부터 강등. 이미 지나간 증상은 그대로 |

통보 보류(hold) 창은 두지 않는다 — MTTA를 늘리는 대가가 확인되지 않았다. 필요하면 G-4.

### 5.5 공통 호스트 키 (E-3)

- `AlarmEvent.host_key`(신규 도메인 필드 · 계산 프로퍼티): 소문자 · 앞뒤 공백 제거 · FQDN이면 첫 라벨. 원천은 폴스타 `hostname`, 제니퍼는 게이트웨이 정합 hostname.
- 신뢰도 `host_key_strength`: `strong`(폴스타 실값 · 제니퍼 `match_reason` override·host_name) · `weak`(제니퍼 정규식) · `none`.
- 같은 hostname이 다른 존에 있는 문제(`plans/87` R-32)는 결합 조건 1(같은 존)로 막는다.
- 제니퍼 알람의 `serverName`을 폴스타 등록명으로 바꾸지 않는다(관제·핑거프린트 이력 보존). 표시용 등록명 역조회(D-188)는 `host_key`로 별도 시도하고 결과는 배지에만 쓴다.

### 5.6 `app_impact` 정밀화

| 항목 | 현행 | 변경 |
|---|---|---|
| 조회 수단 | 알람마다 게이트웨이 `apm_events` MCP 호출(5초 상한) | **사건 저장소 우선** — 워커가 이미 받은 제니퍼 알람으로 판정하고, 사건에 없을 때만 MCP 호출(폴러가 꺼져 있는 환경 호환) |
| 창 | 과거 10분 | 과거 10분 + 사건이 열려 있는 동안 늦게 온 제니퍼 fatal·critical |
| 늦게 온 제니퍼 | 반영 안 됨 | **사후 승격 통보**(DASHBOARD·TICKET이던 폴스타 알람을 PAGE로 다시 통보) — G-6 |
| 성립 조건 | 폴스타 알람이 매트릭스에 도달할 때만 | 그대로(수집 실패면 이미 PAGE) |

승격 전용·억제 불가역(D-195 ② · D-274 ⑤)은 그대로다.

### 5.7 「제니퍼 정상 → 폴스타 알람 강등」(plans/55 옵트인 축)

문헌 근거가 없으므로(§3.3-2) **shadow 전용**으로만 만든다 — 폴스타 CPU·메모리 경고(심각도 2) 시점에 같은 호스트의 제니퍼 인스턴스가 창 안에 이벤트 0건이고 게이트웨이가 정상 응답했으면 `decision_store`에 「강등했을 것」 표지만 남긴다. enforce 전환은 W6 측정 뒤 별도 결정(G-5).

---

## 6. 공통 — 모드 사다리 · 기록 · 피드백

### 6.1 플래그(`NoiseGateConfig` · `NOISE_` · 전부 기본 off = 현행 비트 동일)

| 키 | 기본 | 뜻 |
|---|---|---|
| `apm_noise_policy_enabled` | False | §4.1~4.4 제니퍼 정책 경로 |
| `cross_source_mode` | `off` | `off` · `shadow`(판정 불변 · 「했을 조치」만 기록) · `annotate`(사건 묶음·통보문 주석) · `enforce`(§5.2 강등 실행) |
| `cross_source_rules_path` | `config/cross_source_rules.yaml` | 규칙 표 |
| `episode_idle_seconds` | 900 | §5.3 사건 종료 |
| `app_impact_late_promotion_enabled` | False | §5.6 사후 승격 |
| `apm_healthy_demotion_shadow` | False | §5.7 shadow 기록 |

신규 설정 키는 설정 카탈로그 가드가 요구하는 `src/api/settings_catalog.py` 구획 · `config/settings_help/noise_gate_*.yaml` 항목 · 카탈로그 총수 단언을 함께 갱신한다(`plans/87` D-195 구현 부기 ⑦ 전례).

### 6.2 기록

`decision_store` JSONL 레코드에 `episode_id` · `cross_source`(규칙 id · 원인 alarm_id · host_key · 시차 · 모드 · 적용 여부) · `apm_policy`(정규화 유형 · 정책 행 · 지속 조건 카운트)를 더한다. 관제 화면 근거 키 한글 라벨(`src/static/js/noise-help.js`)과 단계 설명 정본(D-247)에 `cross_source` 단계를 더한다.

### 6.3 피드백

기존 피드백(D-177)에 사건 단위 동작 2개를 더한다 — 「이 묶음은 틀렸다(분리)」 · 「강등된 증상이 실제로 조치가 필요했다」. 둘 다 거짓 억제 지표(§8.1)의 분자다(LiDAR식 라벨 축적).

### 6.4 `plans/117` · `plans/101` 연계

사건 id · `cross_source` 필드 · 피드백 라벨을 117 리플레이셋 빌더의 입력 키로 맞춘다. 통합 이벤트 스키마(101 §통합 이벤트)는 이 계획이 새로 정의하지 않고, `AlarmEvent` + `host_key` + `raw.apm`을 그대로 넘긴다.

---

## 7. 단계별 작업 (Wave)

| Wave | 내용 | 산출물(주요 파일) | verify |
|---|---|---|---|
| **W0** 기반 | 제니퍼 목업 이벤트 생성(폴스타 목업과 같은 시각축으로 복합 시나리오: 자원 포화→WAS 큐잉 · 서버 다운 · DB 풀 고갈 도메인 폭풍 · 제니퍼 단독 경고 반복) · 리플레이 하네스(`decision_store` JSONL + 목업 → 정책 재실행 · 지표 산출) | `noise_gate/scripts/mock_polestar_events.py` 확장(`--with-jennifer`) · `noise_gate/scripts/replay_noise.py` · `noise_gate/testdata/cross_source/` | 현행 정책으로 리플레이 시 제니퍼 미해소 알람 = 전부 `collection_failed` PAGE 재현(기준선) |
| **W1** 제니퍼 단독 | §4.1 공급자 · 정책 표 · enricher 분기 · §4.2 지속 조건 · §4.3 게이트웨이 `event_type_norm` 추가 · §4.4 상관 토큰 | `noise_gate/infrastructure/apm_noise_context.py` · `config/apm_noise_policy.yaml` · `alarm_context_enricher.py` · `notification_policy.py`(step 9 사유 1개) · `apm_gateway/.../domain/events.py` | 플래그 off 비트 동일(`test_plan60_flags_off_regression.py` 계열) · on에서 유형별 티어 단언 · recovery 짝 · 지속 조건 미달 강등 · 심각도3 불변 |
| **W2** 공통 호스트 키 | §5.5 `host_key`·강도 · 등록명 배지 역조회 | `noise_gate/domain/alarm.py` · `server_identity.py` | 폴스타·제니퍼 같은 서버 → 같은 키 · 정규식 정합 → weak · 다른 존 같은 hostname 분리 |
| **W3** 크로스소스 사건 상관 | §5.1 규칙 표 적재·검증 · §5.2 순수 함수 · §5.3 사건 저장 · step 7.6 · shadow/annotate/enforce | `noise_gate/domain/cross_source.py` · `config/cross_source_rules.yaml` · `alarm_worker.py` · `notification_policy.py` | 규칙 조건 5개 각각 하나씩 빠질 때 `link`로 내려감 · 도착 순서 3가지 · 모드별 판정 불변/변경 · 사건 sweep |
| **W4** `app_impact` 정밀화 · 정상 강등 shadow | §5.6 · §5.7 | `notification_gate.py` · `alarm_notifier.py`(사후 승격 통보) | 사건 저장소 판정 = MCP 판정(같은 입력) · 사후 승격 1회만 · shadow 표지 판정 불변 |
| **W5** 통보·관제·매뉴얼 | 통보문 사건 묶음 표시 · 관제 드릴다운 `cross_source` 단계 · 피드백 2동작 · 설정 카탈로그 · 매뉴얼(D-255) | `alarm_notifier.py` · `src/static/js/noise-*.js` · `src/api/settings_catalog.py` · `scripts/manual/` | `pytest tests/test_manual` · 카탈로그 총수 단언 |
| **W6** 평가·보정 | 리플레이로 §8.1 지표 · 원인→증상 지연 분포로 창 보정 · lift 검사 · 내부망 shadow 2주 | 평가 보고(`docs/aiops_benchmark/`) | §8.2 채택 기준 |
| **W7** (선택) 규칙 제안·LLM 주석 | 이력에서 빈발 원인→증상 쌍을 뽑아 규칙 **후보**로 제시(TASA·WINEPI) · 규칙으로 확정 못 하는 쌍만 LLM 주석(COLA) — 판정 불변 | `noise_gate/scripts/mine_cross_source_rules.py` | 후보는 사람이 규칙 표에 옮겨야 효력 |

순서: W0 → W1 → W2 → W3(shadow) → W4 → W5 → W6(내부망) → enforce 전환 결정. W1과 W2는 병렬 가능. W3은 W2에 의존한다.

회귀: 구현 뒤 `python scripts/regress.py --base <세션 시작 SHA>`(모듈 단위). 게이트웨이 변경분은 `cd apm_gateway && ../.venv/bin/python -m pytest`(루트 수집 밖). `overfit_check`는 `noise_gate/domain` 스캔 대상이다 — 새 도메인 모듈 독스트링에 폴스타 스키마 리터럴(`cmm_*` 등)을 쓰지 않는다(D-179).

## 8. 평가와 채택

### 8.1 지표(억제율은 반드시 누락 지표와 쌍으로 보고)

| 지표 | 정의 | 출처 |
|---|---|---|
| 통보 감소율 | PAGE+TICKET 건수 감소(소스별 · 모드별) | AlertStorm 계열 |
| **거짓 강등률** | 강등된 알람 중 피드백 「조치 필요」 또는 30분 안 같은 사건에서 PAGE가 난 비율 | 문헌 공백(§3.3-3) — 이 계획이 정의 |
| 누락 사건 수 | 사건 중 PAGE가 0건이었던 사건 수(심각도3 포함 사건은 구조상 0이어야 함) | 이 계획이 정의 |
| 사건 순도 · 분할도 | 피드백 「분리」 비율 · 같은 원인이 여러 사건으로 갈라진 비율 | 집계 F1(COLA·GRLIA)의 운영 대체 |
| 조사 제출 수 | 제니퍼 알람발 `sre_agent` 조사 건수 | §4.5 |
| MTTA | 기존 계측(D-048.9 — 없으면 null + 사유) | — |

### 8.2 채택 기준(초안 — G-7에서 확정)

- shadow 2주 동안 거짓 강등률 ≤ 2% AND 누락 사건 0 → 해당 규칙 행만 enforce.
- 창 값: 원인→증상 지연 분포의 95분위(설계 가정 — §3.3-4) · lift(조건부 동시발생 ÷ 기준 발생률) ≥ 3인 규칙만 enforce 후보.
- 규칙 단위로 켠다(규칙 표 `enforce: true` 열) — 전부 켜기 없음.

---

## 9. 사용자 확정 게이트

| # | 질문 | 선택지와 결과 | 권고 |
|---|---|---|---|
| G-1 | 제니퍼 유형 정책에서 **완전 억제(SUPPRESS)**를 허용할까 | (a) 허용 — 정보성 유형(예: 운영자가 지정한 지표 경고)은 기록만 남고 화면에도 안 뜬다 (b) 불허 — 가장 낮아도 DASHBOARD(화면에는 뜨고 통보만 안 감) | **(b)로 시작**, shadow 측정 뒤 유형별로 (a) |
| G-2 | 크로스소스 조치 상한 | (a) DASHBOARD 강등까지 (b) SUPPRESS까지 | **(a)** — 거짓 억제 비용 문헌 공백 |
| G-3 | 역방향(제니퍼 원인 → 폴스타 증상 강등)도 enforce 대상인가 | (a) 정방향만(서버 → WAS) (b) 양방향 | **(a)** — 역방향은 link만, 측정 후 재검토 |
| G-4 | 증상이 먼저 오면 통보를 잠시 붙잡아 원인을 기다릴까 | (a) 붙잡지 않음(MTTA 불변 · 먼저 온 증상은 그대로 통보) (b) DASHBOARD 이하로 판정된 것만 N초 보류 | **(a)** |
| G-5 | 「제니퍼 정상 → 폴스타 경고 강등」 | (a) shadow 기록만 (b) enforce 후보로 포함 | **(a)** — 문헌 근거 없음 |
| G-6 | 늦게 온 제니퍼 심각 이벤트로 이미 낮게 판정된 폴스타 알람을 **다시 통보**(사후 승격)할까 | (a) 한다(같은 알람이 두 번 울릴 수 있음) (b) 하지 않고 사건 화면에만 표시 | (a) — 재현율 우선(D-048.4) |
| G-7 | enforce 전환 기준 | §8.2 초안 그대로 / 수치 조정 | 초안 |
| G-8 | 사건 상태 영속 | (a) 워커 메모리(재기동 시 초기화 — 현행 상관과 동일) (b) Redis | **(a)**, W6에서 재기동 손실 측정 후 |

**답(사용자 2026-10-07 「권고대로 진행」)**: G-1 (b) · G-2 (a) · G-3 (a) · G-4 (a) · G-5 (a) · G-6 (a) · G-7 초안 그대로 · G-8 (a).

## 10. 리스크

| # | 리스크 | 통제 |
|---|---|---|
| R-1 | 제니퍼 운영 유형·hostName 형식이 기본 룰과 다름(U-1·U-4·U-13) | 정책·규칙 모두 데이터 표 · 미지 유형 = 보통 중요도 · J0-L-b 뒤 표 재측정 |
| R-2 | 같은 호스트의 CPU 경고와 WAS 지연이 업무시간에 **우연히** 함께 오른다 | lift 검사(§8.2) · 원인 통보 조건(§5.2-4) · 규칙 단위 enforce |
| R-3 | WAS 폭주가 CPU 포화를 일으키는 반대 인과(무한루프·트래픽 급증) | 정방향 강등이어도 증상은 DASHBOARD(화면에 남음) · 원인은 PAGE 그대로 |
| R-4 | 매핑 오류로 다른 서버의 증상을 강등 | 강한 신뢰도만 · 같은 존 · 정규식 정합은 link만 |
| R-5 | 폴러 장애로 제니퍼 알람이 몰려 늦게 도착 | 원인 창 밖이면 강등 안 됨(재현율 쪽으로 실패) · 사후 승격은 사건이 열려 있을 때만 |
| R-6 | 워커 메모리 증가(사건·지속 카운터) | 키 만료 sweep · 사건 수 상한 + 초과 시 경고 로그 |
| R-7 | 플래그·설정 증가로 카탈로그 가드·매뉴얼 누락 | W5에 묶어 처리 · 역방향 가드 `ignore` 우회 금지(D-255) |

## 11. 측정하지 못한 것

- 운영 제니퍼 이벤트 유형 · 레벨 값 · recovery 형식 · `hostName` 형식(J0-L-b 전).
- 운영 폴스타 `hostname`과 제니퍼 `Instance.hostName`의 실제 일치율.
- 제니퍼 이벤트 발생 → `alarm:raw` 도착 지연의 운영 분포(30초 주기 + 겹침 재조회의 설계값만 앎).
- 제니퍼 알람 유입량과 현행 PAGE·조사 트리거 건수(운영 `decision_store` 미열람).
- 동반 문헌 중 「(서지 확인)」·「미재확인」 표기 항목의 원문.

## 12. 구현 중 발견한 결정 충돌·질문 (v1.1 — W3 착수 전 답 필요)

§9 게이트(G-1~G-8)와 별개로, 기존 결정의 용도·경계를 넓히는 항목이 트랙 항목으로 들어가 있었다(docs/18 2026-10-07 항목). 아래 답을 받기 전에는 W1 §4.4 · W3 · W4를 착수하지 않는다.

| # | 충돌·질문 | 선택지 | 팀 리드 권고 |
|---|---|---|---|
| Q-1 | **D-195 ②**(「noise_gate는 `was_signals`를 `app_impact` **승격 전용**으로 쓴다」)와 §4.4(상관 토큰에 was kind·domain_id → step 7.5 SUPPRESS) · §5.1(`was_kinds`로 강등 대상 선택) 충돌 | (a) D-195 ② 개정 — noise_gate가 `was_signals` kind를 **묶음·강등 대상 선택**에 쓰는 것을 허용(판정 재구현은 여전히 금지) (b) D-195 ② 유지 — 규칙 표는 `was_kinds` 대신 **정규화 유형 목록**으로 증상을 고르고 §4.4는 폐기 | (a) — 판정 단일 정의(D-274 ⑤)는 지켜지고, (b)는 게이트웨이 유형→kind 표를 데이터로 한 벌 더 만든다 |
| Q-2 | **D-109 B-6**(상관은 **db_id** 경계 안 · 「gp↔yd 상관 금지」)과 §5.3 사건 키 `zone|host_key` 충돌 — 공동존 제니퍼 소스 하나가 `polestar_cm_gp`·`polestar_cm_yd` 양쪽 알람과 묶일 수 있다 | (a) B-6은 서로 다른 호스트 간 상관 규칙으로 한정하고, **같은 호스트 키**의 소스 간 묶음은 존 경계로 허용 (b) 제니퍼 소스 → 폴스타 db_id 대응을 레지스트리에 더해 db_id 경계를 유지 | (a) — 같은 호스트 키 + 강한 정합이 이미 조건이고, gp↔yd에 같은 hostname이 있으면 R-32처럼 강등 금지 |
| Q-3 | G-1 (b)「가장 낮아도 DASHBOARD」의 범위 — 유형 정책·지속 조건만인가, 플래그 on에서 제니퍼 알람이 새로 도달하는 공용 억제 단계(유지보수·침묵·스톰·플래핑·크로스호스트 상관 SUPPRESS)까지인가 | (a) 유형 정책만(현 구현) (b) 제니퍼 알람 전체 하한 DASHBOARD | (a) — 공용 단계는 폴스타와 같은 규칙을 따른다 |
| Q-4 | W1 이탈 수용 여부 — 지문 dedup(기본 4h) 때문에 「두 번째 이벤트 재판정」(§4.2)이 성립하지 않아, 지속 조건이 채워지는 순간 dedup 1회 해제 + 정책 경로의 심각도 상승 dedup 우회를 넣었다. 또 verifier가 **기존 경로**에서도 같은 지문 sev2 → sev3가 4h 안이면 버려지는 것을 실측했다(D-048 인접 · 기준선 동작) | (a) 이탈 수용 · 기존 경로 dedup은 별도 건 (b) 이탈 수용 + 기존 경로에도 「심각도 상승은 dedup 우회」를 넣는 별도 결정 | (b) — 심각도3 절대 PAGE의 빈틈이다 |
| Q-5 | 데이터 미확정 2건 — ① 규칙 `host_down_to_was_all`의 원인 = 폴스타 서버 가용성 알람 정의 이름(운영 값) ② 제니퍼 알람 표시용 폴스타 등록명 배지(D-188) — 현행은 제니퍼 db_id가 레지스트리 DB가 아니라 역조회가 사실상 안 된다. 넣으려면 존→폴스타 db_id 순회 · 대소문자 무시 조회 · `serverName` 승격 생략이 필요해 플래그 `NOISE_APM_IDENTITY_BADGE_LOOKUP`(기본 off)를 새로 둬야 한다 | ① 운영 알람명 제공 / 행을 비활성으로 둠 ② 배지 플래그 추가 / 보류 | ① 행 비활성(`enforce` 없음) ② 보류(W5에서 화면 요구와 함께) |

**답(사용자 2026-10-07)**:
- Q-1 **(a)** — D-195 ② 부분 개정(부기 2026-10-07): noise_gate가 `was_signals` kind를 상관 묶음·크로스소스 강등 대상 선택에 쓴다 · 판정 재구현 금지 · `app_impact` 승격 전용 불변 → §4.4 구현.
- Q-2 **(a)** — D-109 B-6은 서로 다른 호스트 간 상관에만 적용 · 같은 `host_key`의 소스 간 묶음은 존 경계로 허용(부기 2026-10-07) · 같은 존 사건에 서로 다른 폴스타 db_id가 섞이면 강등 금지(link).
- Q-3 — 사용자 권고 없음 → **현 구현 유지**(「가장 낮아도 DASHBOARD」는 유형 정책·지속 조건에만 · 공용 억제 단계는 폴스타와 같은 규칙). 최종 보고에서 별도 확인 요청.
- Q-4 **이탈 수용 + 기존 경로 별도 결정** — 기존(폴스타 포함) 지문 dedup에 「심각도 상승 시 dedup 건너뜀」을 넣고 D-318로 등재 · 플래그 `NOISE_DEDUP_SEVERITY_RISE_BYPASS` **기본 on**(신규 플래그 기본 off 원칙의 예외 — 근거는 D-318·config 주석).
- Q-5 — ① `host_down_to_was_all`은 비활성 행(`enabled: false`) 유지 ② `NOISE_APM_IDENTITY_BADGE_LOOKUP` 보류(미구현).

**측정 잔여(J0-L-b 뒤)**: 지표 기반 이벤트의 유형은 `metricsName`이라 정책 표 11종(오류 유형)과 맞지 않으면 `default`로 떨어져 지속 조건이 걸리지 않는다 · recovery 유형 대응(`recovers:` 열) 미구현(U-1) · 침묵 규칙은 원문 `alarm_name`으로 매칭한다(정규화 유형으로 쓴 규칙은 `WARNING_` 접두 이벤트에 걸리지 않음) · (v1.2 해소) `.env.example` 기재 · 관제 근거 키 한글 라벨 · 매뉴얼(침묵 규칙 원문 매칭 주의 포함).

## 변경 이력

| 날짜 | 버전 | 내용 |
|---|---|---|
| 2026-10-07 | v1.0 | 초안 — 현행 실측(§2) · 문헌 조사(§3 · 동반 문서) · 설계 A·B · Wave W0~W7 · 게이트 G-1~G-8 · D-317 예약 |
| 2026-10-07 | v1.1 | 게이트 무관분 구현 — W0(목업·리플레이) · W1 §4.1~4.3(게이트웨이 `event_type_norm` · 제니퍼 정책 경로 · 지속 조건 · dedup 이탈) · W2(`host_key`) · §5.2·§5.5 `match_reason` 어휘 정정(`hostName`→`host_name`) · §12 결정 충돌 질문 Q-1~Q-5 신설 · 파일명 `-TODO`→`-WIP` |
| 2026-10-07 | v1.2 | 게이트·Q 사용자 확정 반영(§9·§12 답) · Wave A(Q-4 D-318 · §4.4) · W3 크로스소스 사건 상관 · W4 `app_impact` 사건 저장소·사후 승격·정상 강등 shadow·조사 사건당 1회 · W5 통보·관제·사건 피드백·매뉴얼 · D-317 등재 · D-318 신설 · D-195 ②·D-109 B-6 부기 · W6(내부망)·W7 잔여 |
