# 60. 노이즈 캔슬링 고도화 — 선진사례 벤치마킹 기반 수정·구현 계획 (Noise-Cancellation Benchmark Refinement)

> 작성일: 2026-07
> **상위 로드맵**: **Plan 62(AIOps 전체 역량 마스터 로드맵) — Phase P1(노이즈·상관 완성)**. E4 토폴로지 그래프는 P2(RCA)의 선행자산.
> **대상/선행 계획**: Plan 52(알람 노이즈 캔슬링 — 구현 완료, D-048), Plan 47(이력 패턴·`is_routine`), Plan 50(RCA — 계획), Plan 51(데이터 수집·3계층), Plan 53(장애관리 로드맵), Plan 54(노이즈 대시보드), Plan 55(멀티소스 관측 로드맵)
> **관련 결정**: D-003(읽기전용), D-035(결정적 규칙=판단·LLM=보조), D-048(노이즈 게이트 4-티어), D-005(멀티DB 순차·부분실패), D-037(deepagents 멀티에이전트)
> **신규 결정(본 계획에서 부여, 착수 시 등재)**: **D-077**(재발생 dedup 억제 count 관측성 보강), **D-078**(크로스-호스트 이벤트 상관 — 결정적 Jaccard+시간창), **D-079**(동적 baseline 이상탐지 — STL/Holt-Winters 로컬), **D-080**(토폴로지 의존성 그래프 자료구조 — 노이즈·RCA 공용 선행자산), **D-081**(변경/구성 이벤트 상관 피드)
> ※ 번호 규칙(Known Mistakes 2026-06-25·06-29): `grep -roE "D-0[0-9]{2}" docs/ plans/` 현재 등재 최댓값 **D-068**(D-067·068 등재 완료)·예약 최댓값 **D-076**(Plan 61이 D-072~076 예약). 본 계획은 그 위 연속 빈 블록 **D-077~D-081** 부여(초안의 D-067~071은 D-067·068 충돌로 재조정, §13 변경이력). 구현·등재 직전 `## D-` 헤더와 「변경 이력」 표를 모두 재확인해 충돌 시 다음 빈 번호로 재조정하고 사유를 본문에 명시한다.
> ※ 본 계획은 **신규 데이터 수집(변경 피드)·신규 자료구조(토폴로지 그래프)**를 포함한다. §8 결정 선택지 중 방향을 **사용자 확인 후** 착수한다(CLAUDE.md 의사결정 규칙).
> **상태**: **Wave A(E6·E1·E4) 구현 완료 (2026-07-21) — 사용자 §8 게이트 확정(B-1=AVAIL_DEPEND 단독·B-2=폴스타 이력·B-5=하이브리드·B-6=존 경계) 후 착수. 등재 D-106(E1)·D-107(E4)·D-108(E6)** (초안 D-077~081 결번, D-101~105 예약충돌로 D-106~108 재부여 — §13 변경이력·02_decision.md §8 규칙). **Wave B(E2·E3)·C(E5) 미착수.** E1~E5는 **회귀 없는 옵트인 증분**으로 설계됨. §2~§7은 현행 코드(`notification_policy`·`alarm_worker`·`alarm_context_enricher`·`config.py`) **실측 기반**으로 대상 함수·라인·변경 diff·신규 플래그·회귀 경계를 명시한다. 2026-07-13 심층 코드 검토의 개선 제안(P1~P6, `docs/plan60_improvement_proposals.md`)을 **확정 설계로 반영 완료** — 남은 사용자 확인은 §8(B-1·B-2·B-5·B-6)뿐이다(B-3은 순수 Python HW 확정으로 해소). **2026-07-21: §14 자동 조사 트리거 훅 신설** — "이벤트 발생 시 OS 현황 자동 조사→중요도 정밀 판정→운영자 브리핑"은 게이트 밖 오케스트레이션으로 분리해 신규 **Plan 64**가 구현하며, Plan 60은 트리거 계약만 정의한다. **2026-07-21: §15 LLM·AI 활용 방안 신설** — 흩어져 있던 LLM 접점(심각도 상향·actionable 분류·브리핑)을 실측으로 명확화하고, LLM/AI를 노이즈 캔슬링에 활용하는 방안을 최신 문헌(COLA·DiLink·Oasis·SOC 한계연구)으로 근거화하며 D-035 경계를 재확정한다. **2026-07-21: §14.4 역방향 Option A 신설(D-104)** — 조사에 쓰는 L3 호스트 실측 부하를 게이트의 **경계 케이스(고중요·sev2·TICKET)에만 선택적 probe**로 되먹여 상향 전용 tie-break(전 알람 사전수집 회피·Plan 64 L3 보안결정 D-102/B-1 선행). **2026-07-21: §16 E6 통보 컨텍스트 보강 신설(D-105)** — 사용자 요건("메시지 분석→필요정보 조회→운영자 전달")의 L1 부분을 Plan 60에서 우선 구현. **CPU/메모리 프로세스 첨부는 Plan 47-1로 이미 기구현** → kind 확장·메시지 타깃팅을 L1(블로커 없음)로 넓히고, L3 심화는 Plan 64 §4.8.

---

## 1. 개요 및 목적

### 1.1 배경 — 왜 지금 수정·고도화인가

Plan 52(D-048)로 노이즈 게이트는 **구현 완료**되었다. 실측 코드 기준 현행 자산은 다음과 같다(재확인):

| 구현된 자산 | 위치 | 동작 |
|------------|------|------|
| 결정적 4-티어 판정 | `src/alarm/domain/notification_policy.py::decide_notification` | PAGE/TICKET/DASHBOARD/SUPPRESS + 심각도3 단락 + 재현율 우선 보수화 |
| 재발생 지문 | `notification_policy.py::compute_fingerprint` | 재발생 dedup용 안정 식별자 |
| 중요도×심각도 매트릭스 | `notification_policy.py::_matrix_tier` | 심각도2·1 × 높음/보통/낮음 |
| 신호 수집 | `src/alarm/infrastructure/polestar_noise_context.py`, `noise_signal_tools.py` | 중요도·유지보수·의존성·알림정책·시그니처(L1 읽기전용) |
| 억제 신호 | flapping·storm·self_heal·cascade(연쇄) | 워커가 순수함수로 산출→인자 주입 |
| 게이트 노드 | `src/alarm/application/nodes/notification_gate.py` | 판정→감사(decision_store)→라우팅 |
| 메타모니터링·감사 | decision_store / SSE 브리지 | 억제≠삭제, 억제율 관측 |

즉 **단일 소스(폴스타 L1) 기반의 노이즈 억제는 업계 수준에 도달**했다(벤치마크 분석 결론: collectorinfra는 노이즈·상관 L1에서 선진 플랫폼과 대등, "결정적 규칙+LLM 보조" 방법론은 Dynatrace Davis의 deterministic 인과 엔진·최신 토폴로지 인지 RCA 연구와 정합).

그러나 선진 솔루션(Dynatrace Davis, Datadog Watchdog, Splunk ITSI, Moogsoft, BigPanda, New Relic) 구현을 기능별로 분해한 결과, 현행 게이트에는 **다섯 개의 정밀화·확장 여지**가 확인되었다. 본 계획은 이를 **회귀 없는 옵트인 증분**으로 구현한다.

### 1.2 벤치마크가 가리키는 수정·고도화 항목 (요약)

| # | 항목 | 현행 | 선진사례 구현 | 수정·고도화 방향 |
|---|------|------|--------------|----------------|
| **E1** | **재발생 dedup 관측성 보강** | 재발생 dedup **이미 구현**(`compute_fingerprint`+`_is_duplicate_fingerprint`, alarm_id 경로와 분리) — 단 억제 count 미노출 | Moogsoft/BigPanda = 재발생 count/last_seen 집계·대표 알람에 표기 | 억제 count/first_seen을 대표 알람·감사에 첨부(억제≠삭제 강화). 판정 로직 무변경 |
| **E2** | **크로스-호스트 이벤트 상관** | 스톰=동일 서버 다발만 그룹핑 | Moogsoft Cookbook(필드 Tanimoto/Jaccard 유사도), Splunk Event Analytics(episode 클러스터링) | `correlation_engine`에 **결정적 Jaccard 필드유사도 + 시간창 군집** 추가(서버 경계 넘는 상관), 대표 1건 PAGE |
| **E3** | **동적 baseline 이상탐지** | (게이트 밖) Plan 50 정적 z-score+지속성(계획) | Dynatrace 적응형 baseline, New Relic Holt-Winters, Splunk 적응형 임계(시간정책·야간 재계산), Pinterest STL | **STL 분해/Holt-Winters 동적 baseline**(로컬·폐쇄망)으로 계절 오탐↓ → 게이트의 "이상 심각도 상향" 보조 입력 |
| **E4** | **토폴로지 의존성 그래프** | `AVAIL_DEPEND_RESOURCE_ID` 부모-자식 1홉만 | Davis Smartscape(다홉 위상 그래프 순회), 토폴로지 인지 RCA 연구(그래프 BFS) | **의존성 그래프 자료구조**(다홉) 구축 → 연쇄 억제 정밀화 + Plan 50 결정적 RCA의 **최우선 선행자산** |
| **E5** | **변경/구성 이벤트 상관** | 없음(Plan 50 §4.5 Phase C 후보) | Davis/Watchdog faulty-deployment detection(배포=근본원인 지목) | **변경 피드**(CI/CD·구성변경)를 타임라인 오버레이 → 게이트의 원인성 판단·RCA 최대 레버 |

> 근거: `plans/aiops_vendor_implementation_reference`(벤더별 기능 구현 레퍼런스), 벤더 기술문서(공개 서술 기준). 도입효과 수치는 벤더 발표 기준으로 독립검증 아님 — 본 계획은 **구현 메커니즘**만 채택하고 효과 수치는 인용하지 않는다.

### 1.3 설계 원칙 (전 계획 계승 — 불변)

1. **결정적 규칙=판단 / LLM·ML=보조 입력** (D-035, Plan 52 §4.1). 신규 상관·이상탐지·그래프순회는 모두 **결정적 Python**. LLM은 사전계산된 증거의 해석·설명자 + **경계된 분류·상향 후보 제시**로. LLM/AI의 구체적 접점(심각도 상향·actionable 분류·브리핑)·문헌 기반 확장·D-035 경계는 **§15**에서 명확화한다.
2. **재현율 우선·비용 비대칭** — 불확실·수집실패·미식별 시 보수적 PAGE. **심각도 3은 모든 억제 단계 단락(short-circuit)하고 항상 PAGE** — 신규 단계도 예외 없음.
3. **억제 ≠ 삭제** — 신규 억제(크로스상관·연쇄정밀화)도 전 티어 감사(decision_store) 기록 유지.
4. **읽기전용·옵트인·회귀 없음** — 신규 신호는 SELECT/조회만, 전 기능 기본 off 플래그. 비활성 시 현행 게이트 경로 무변경.
5. **재구축이 아니라 조합** — 기존 `decide_notification`·`compute_fingerprint`·신호 컬렉터·decision_store·deepagents 오케스트레이션(D-037)을 확장. 새 프레임워크 금지.
6. **폐쇄망 호환** — 이상탐지·그래프 라이브러리는 로컬(statsmodels/numpy 등), tool-calling 비의존. 외부 SaaS 금지.
7. **억제기 메타모니터링 계승** — 신규 단계 도입 시 억제율 이상·이벤트 무수신 메타경보 범위에 포함.

---

## 2. 현행 구현 실측 및 수정 지점 (정밀 조준)

### 2.1 현행 코드 자산 (파일·함수·라인 단위 실측)

Plan 52(D-048)는 계획을 넘어 **완전히 구현·플래그화**되어 있다. 실측 결과:

| 자산 | 위치(실측) | 현행 동작 |
|------|-----------|----------|
| 결정 정책(순수함수) | `src/alarm/domain/notification_policy.py::decide_notification` (L104~322) | step1~9 순서형 결정, 첫 종착 확정 |
| 지문 | `notification_policy.compute_fingerprint(event)` (L51) | `SHA1(db_id·server∥hostname·alarm_name·resource_name)` |
| 매트릭스 | `_matrix_tier(effective_severity, importance)` (L80) | 심각도2/1 × 높음/보통/낮음 |
| 우선순위 | `_priority()` (L95), `_TIER_RANK`(page3>ticket2>dashboard1>suppress0) | 승격/강등 1단계 이동 기준 |
| 신호 수집(L1) | `src/alarm/application/nodes/alarm_context_enricher.py` (`fetch_noise_context`, `collect_dependency` 인자) | 중요도·유지보수·알림정책·부모상태, graceful degradation(`source="unavailable"`) |
| 워커·탐지 | `src/alarm/application/alarm_worker.py` | `_process`(L360~), `_detect_inhibition`·`_detect_flapping`·`_detect_storm`·`_update_firing_registry`(self_heal) |
| 재발생 dedup | `alarm_worker._is_duplicate_fingerprint` (L543), 상태 `self._gate_dedup: dict[str,float]` (L67) | 지문 기반 재통보 억제, 심각도별 TTL |
| 재처리 방지 | `alarm_worker._is_duplicate` (L518) | `alarm_id` TTL(별개 경로) |
| 플래핑 도메인 | `src/alarm/domain/flapping.py` (`flap_percent`·`update_flap_state`, `MAX_STATES=21`) | Nagios %-state-change + 히스테리시스 |
| 설정 | `src/config.py::NoiseGateConfig` (L389, env_prefix `NOISE_`) | 전 기능 옵트인 플래그 |

### 2.2 현행 `decide_notification` 결정 순서(실측 step)와 삽입 지점

```
[입력] event, history_stats, analysis, noise_ctx, config,
       *, self_heal, inhibited, flapping, storm  (워커가 순수함수로 산출·주입)
   │
step1  실효심각도 = max(폴스타 severity, AI 상향)   ← [E3] 이상탐지를 ai_message_severity 공급원으로
       (enable_ai_severity_boost 플래그·상향 전용)
step2  신호 수집 실패 보수화(source=="unavailable")
step3  심각도3 → 항상 PAGE (단락)                  ← 불변, 신규 단계도 이 뒤에만
step4  해소(sev0)/자가복구 상관 SUPPRESS
step5  수집 실패 + sev>=1 → 보수 PAGE
step6  유지보수 SUPPRESS
step6.4 의존성 억제(parent_avail_status≠0·1홉)      ← [E4] 다홉 is_cascaded로 확장
step6.5 인히비션(동일서버 상위심각도)                ← [E2] 스코프를 동일서버→상관클러스터로
step6  플래핑 SUPPRESS(flapping_enabled)
step7  스톰 SUPPRESS(storm_grouping_enabled·동일서버) ← [E2] 크로스-호스트 상관으로 대체·확장
step8  매트릭스 _matrix_tier(effective_severity,importance)
step9  보조 조정(noti_policy·is_routine·llm_actionability, 1단계·승격우선)
   │
[출력] NotificationDecision(tier, reason, priority, signals, fingerprint)
       → decision_store 감사 → 라우팅
```

**정정된 조준 결론**: 당초 초안이 신규로 제안했던 인히비션·의존성 억제·AI 심각도 상향·LLM 액션가능성은 **이미 구현·플래그화**되어 있다. 본 계획의 5개 항목은 대부분 **신규가 아니라 기존 함수의 확장/정밀화**이며, 아래 표로 재정의한다:

| 항목 | 성격 | 수정 대상(실측) |
|------|------|----------------|
| E1 | 기존 상태 dict 확장 | `alarm_worker._gate_dedup`·`_is_duplicate_fingerprint` |
| E2 | 기존 `_detect_storm` 대체·확장 | `alarm_worker._detect_storm` + 신규 `correlation.py` |
| E3 | 기존 `ai_message_severity` 공급원 신설 | 신규 `anomaly.py` → `analysis.ai_message_severity`(게이트 무변경) |
| E4 | 기존 1홉 의존성 억제 확장 | `notification_policy` step6.4 + enricher + 신규 `topology.py` |
| E5 | 신규 신호·단계 | 신규 `change_correlation.py` + 신규 step |

핵심 원칙: **모든 신규 로직은 순수함수로 산출해 워커가 `decide_notification` 인자/`analysis`로 주입**한다(현행 flapping·storm 패턴 그대로). 정책 모듈(domain)은 표준 라이브러리만 의존하므로 상관/그래프/이상탐지 모듈을 import하지 않는다.

---

## 3. E1 — 재발생 dedup 강화: count/last_seen 집계 (D-077)

### 3.1 현행 (실측 정정)
Plan 52 §6.1의 재발생 dedup은 **이미 구현되어 있다**(당초 벤치마크 분석의 "지문 미정합" 진단은 실측으로 정정):
- `notification_policy.compute_fingerprint(event)`(db_id+server+alarm_name+resource) 존재.
- `alarm_worker._is_duplicate_fingerprint(fingerprint, now, severity)` — 게이트 활성 시 재발생 dedup, `alarm_id` 경로(`_is_duplicate`)와 **완전 분리**(`self._gate_dedup`), 심각도별 TTL 분기(sev3 단축 가능, 기본 4h).
- `alarm_id` TTL(`_is_duplicate`)은 소켓 재처리 방지로 정상 병존.

→ 즉 **재처리 방지 / 재발생 dedup 분리는 이미 완료**. E1은 신규 구현이 아니라 **관측성 보강**만 남는다.

**실측 보완(2026-07-13 심층 검토)**: 재발생 억제는 `_process`가 그래프 진입 **이전**에 debug 로그 + ACK로 종료한다(`alarm_worker.py` L372~381). 즉 억제된 재발생은 `notification_gate`에 도달하지 못해 **decision_store에 아무 감사도 남지 않는다** — 현행은 "억제≠삭제" 원칙의 실질 사각지대(억제 사실이 debug 레벨 로그뿐)다. E1의 핵심 가치는 단순 count 표기가 아니라 **이 감사 사각지대의 해소**이며, 따라서 감사 기록은 gate 노드가 아니라 **워커가 직접** 남겨야 한다(§3.3 대상 3 메커니즘 확정 참조).

### 3.2 남은 개선 (선진사례 대비 gap)
Moogsoft/BigPanda는 재발생 억제 시 **count/first_seen/last_seen을 집계해 대표 알람에 재발생 빈도를 표기**한다(억제된 반복이 몇 회인지 운영자가 봄). 현행 `_is_duplicate_fingerprint`는 boolean 억제만 하고 **집계 카운트를 감사·대표 알람에 노출하지 않는다**.

### 3.3 상세 구현 (파일·함수 단위)

**대상 1 — 상태 자료구조 확장** (`alarm_worker.py` L67):
```python
# 현행:  self._gate_dedup: dict[str, float] = {}          # fingerprint → last_seen
# 변경:  self._gate_dedup: dict[str, dict] = {}           # fingerprint → {first_seen, last_seen, count}
```

**대상 2 — `_is_duplicate_fingerprint` 집계화** (`alarm_worker.py` L543~585). 현행은 `last`(float)와 now 차이를 TTL과 비교해 bool 반환하고 `self._gate_dedup[fingerprint]=now`로 갱신한다. 변경:
- 레코드를 dict로 읽고, **억제 판정 로직(TTL·심각도 분기)은 그대로 유지**하되,
- 억제(중복)로 판정될 때 `rec["count"] += 1; rec["last_seen"]=now`만 추가,
- 신규(비중복)일 때 `{first_seen:now, last_notified:now, last_seen:now, count:1}` 기록,
- **[실측 보완 — TTL 비교 기준 필드 명시]** 현행 float 값의 의미는 "마지막 **통보** 시각"이다(중복 판정 시 갱신하지 않음 → TTL 경과 후 재통보되는 **고정창**). dict화 시 TTL 비교는 반드시 신규 필드 **`last_notified`**(비중복 처리 시에만 갱신) 기준으로 유지해야 한다. `last_seen`(중복마다 갱신)과 비교하면 **슬라이딩 창으로 변질**되어 지속 재발 알람이 영원히 재통보되지 않는 회귀가 발생한다.
- 만료 정리(`expired`)는 `v["last_seen"]` 기준(연속 재발 중인 레코드의 count 보존). 단 이는 현행(통보시각 기준)보다 레코드 수명이 길어지는 **메모리 의미 변화**이므로, 판정 필드(`last_notified`)와 정리 필드(`last_seen`)가 다름을 코드 주석·테스트로 고정한다.
- 반환 타입 확장: `bool` → `tuple[bool, dict]`(is_dup, 재발생 메타). 또는 `self._gate_dedup[fp]`를 호출부가 직접 참조(호출부 L372 인접). **호출부 1곳만 수정**되도록 tuple 반환 권장. 재통보 시(TTL 만료 후 첫 비중복)에는 **리셋 직전 레코드**(직전 창의 count/first_seen)를 메타로 반환하여 대상 3의 "대표 알람 표기"에 쓴다.

**대상 3 — 대표 알람·감사에 노출 [실측 보완 — 메커니즘 확정]**. §3.1 보완에서 확인했듯 억제된 재발생은 그래프에 진입하지 않으므로 `notification_gate`의 `store.record()` 경로로는 감사가 불가능하다. 두 갈래로 분리한다:
- **(a) 억제 시점 감사(워커 직접 기록)**: 워커는 이미 `self._decision_store`를 보유한다. `DecisionStore.record_recurrence(*, fingerprint, count, first_seen_ts, alarm_id="", ts=None)` 신설 — 기존 `record_resolution`(`type="resolution"` 별도 레코드, D-049)과 동일 전례를 따라 `type="recurrence"` 레코드로 적재한다. `aggregate()`의 비-decision 제외는 `type=="resolution"` 개별 비교 대신 **`rec.get("type")` 보유 레코드(비-decision) 일반 제외**로 리팩토링한다(향후 레코드 타입 추가에도 안전). 매 억제마다 1줄 적재가 과하면 `recurrence_audit_every_n`(기본 1, `count % N == 0`일 때만 적재) 샘플링 옵션을 둔다.
- **(b) 재통보 시점 표기(대표 알람)**: TTL 만료 후 재통보되는 이벤트 처리 시, 대상 2의 tuple 메타(직전 창 count)를 그래프 state로 전달해 notifier·gate 감사에 첨부한다. 구현: ① `AlarmState`(alarm_graph.py)에 `recurrence: Optional[dict]` 키 추가(§12 산출물에 반영), ② `notification_gate`가 `store.record(decision, alarm_id=..., recurrence=state.get("recurrence"))`로 전달 — `record()`에 선택 인자를 추가해 **최상위 필드**로 기록, ③ `alarm_notifier`가 recurrence 존재 시 "직전 {window}h {count}회 재발 후 재통보" 1줄을 메시지에 첨부. `NotificationDecision.signals`에 신규 키를 넣으려면 §8.2 동결 스키마 확장 필요 → 스키마 외 별도 `recurrence` 필드로 첨부(스키마 동결 원칙 준수, §10 공통 방침 참조).
- **현행 단락 유지**: 해소 이벤트(is_clear)는 dedup을 호출하지 않는 현행 단락(자가복구 상관 전달)을 그대로 유지한다 — dedup 호출 자체가 상태를 변이하므로 호출 게이팅 순서를 바꾸지 않는다.

- **옵트인 플래그 불필요**: 억제 판정 로직 무변경, 집계는 기존 dedup 경로 내 부가 → 게이트 off 시 경로 미진입(회귀 0). 튜닝 노브는 `recurrence_audit_every_n`(기본 1 — 감사 적재 샘플링) 하나뿐이다. dict 마이그레이션은 in-memory 상태이므로 재기동 시 자연 초기화.

### 3.4 수용 기준
- 재발생 억제 시 count 증가가 대표 알람 로그·decision_store 감사에 표기(몇 회 억제됐는지 가시화).
- 억제 판정(TTL·심각도 분기)은 현행과 **비트 동일**(집계만 추가) — 기존 `_is_duplicate_fingerprint` 테스트 그대로 통과.
- 심각도3 최초 발생은 항상 PAGE 불변(현행 §6.1 계승). `signals` §8.2 동결 스키마 미훼손.

---

## 4. E2 — 크로스-호스트 이벤트 상관 (D-078)

### 4.1 문제 (실측)
현행 `_detect_storm`(`alarm_worker.py`)은 스코프 `f"{db_id}|{server_name}"` 사건창(`storm_window_seconds=60`, `storm_threshold=5`) 내 발생 다발만 `storm=True`로 억제한다 — **동일 서버 경계 안**이다. 선진 솔루션(Moogsoft Cookbook 필드 Tanimoto·Splunk Event Analytics episode)은 **서버 경계를 넘어 필드 유사도·시간 근접으로 사건을 군집**한다. 예: 한 스위치 장애로 20대 서버가 동시에 CPU/네트워크 알람 → 현행은 서버별로 각각 통보(20건), 선진사례는 1개 Situation으로 묶어 대표 1건.

### 4.2 상세 구현 (결정적)

**신규 모듈** `src/alarm/domain/correlation.py`(domain — 표준 라이브러리만, **온라인 그리디 군집 API**):
```python
def signature_tokens(alarm_name: str, resource_type: str,
                     signature_label: str, extra: str = "") -> frozenset[str]:
    # 정규화 토큰(소문자·구분자 분리). server_name은 토큰에서 제외한다 —
    # 호스트 경계를 넘는 상관이 목적이므로 호스트 식별자가 유사도를 깎으면 안 됨.
def jaccard(a: frozenset, b: frozenset) -> float:
    # |a∩b| / |a∪b|  (Tanimoto, 결정적)

@dataclass
class ClusterState:              # 워커가 보관하는 활성 클러스터(사건창 내)
    representative_fp: str       # 대표 fingerprint = 첫 도착 이벤트
    tokens: frozenset[str]
    first_ts: float
    last_ts: float
    member_count: int = 1

def match_cluster(clusters: list[ClusterState], tokens: frozenset[str],
                  *, sim_threshold: float) -> int | None:
    # 대표 토큰과의 Jaccard 최고점 클러스터 인덱스. 동점 시 first_ts 오름차순(결정성).
```
- **메시지 시그니처 재사용 — [실측 정정: 계층 주의]**: `noise_signal_tools.scan_message_signature`는 **infrastructure 계층**이므로 domain `correlation.py`가 import하면 `arch_check` 위반이다. 실측 결과 이 함수는 domain `severity_signatures.scan_signature_severity`의 얇은 래퍼일 뿐이므로, `signature_tokens`는 **domain 함수(`scan_signature_severity`)를 직접 사용**하거나, 워커가 토큰을 사전 계산해 인자로 넘긴다(현행 flapping 패턴과 동일 — domain은 값만 소비).

**워커 연동** (`alarm_worker.py`) — **온라인(스트리밍) 그리디 군집 확정**. 워커는 이벤트를 한 건씩 순차 처리하며 앞선 판정을 소급 변경할 수 없으므로, 배치 군집이 아니라 온라인 판정으로 정의한다. 신규 detection `_detect_correlated_storm`(기존 `_detect_storm`과 **별개·병존** — 두 플래그 독립):

```python
# 신규 상태: db_id(존) 스코프 — 존 경계는 넘지 않음(§8 B-6)
self._correlation_clusters: dict[str, list[ClusterState]] = {}

def _detect_correlated_storm(self, event, now) -> bool:
    if event.is_clear:
        return False                      # 해소는 군집/카운트 제외(_detect_storm 동일)
    clusters = self._correlation_clusters.setdefault(event.db_id, [])
    # ① 만료 sweep: last_ts가 correlation_window_seconds 밖인 클러스터 제거,
    #    빈 스코프 키 삭제 (Known Mistakes 2026-06-29 E2 — 키 만료 sweep 의무)
    # ② 버퍼 상한: correlation_buffer_max 초과 시 oldest 제거 + warning(침묵 금지)
    tokens = signature_tokens(event.alarm_name, event.resource_type,
                              sig_label)  # sig_label은 워커가 domain
                                          # scan_signature_severity로 사전 산출
    idx = match_cluster(clusters, tokens, sim_threshold=cfg.correlation_sim_threshold)
    if idx is not None:
        c = clusters[idx]
        c.member_count += 1; c.last_ts = now
        return c.member_count >= cfg.correlation_min_cluster_size  # n번째 멤버부터 억제
    clusters.append(ClusterState(compute_fingerprint(event), tokens, now, now))
    return False                          # 첫 도착 = 대표 → 통보(소급 선출 없음)
```

- `cross_host_correlation_enabled=False`(기본)면 detection 자체 미수행 → **회귀 0**. 결정성 테스트는 "동일 이벤트 시퀀스 → 동일 판정 시퀀스"로 고정한다.
- **감사 사유 구분(확정)**: `decide_notification`에 `correlated: bool = False` 키워드 인자를 추가해 **별도 step 7.5**(step7 스톰 다음·매트릭스 이전, 사유: "크로스-호스트 상관 — 클러스터 대표 외 억제")로 분리한다(기본값 False라 하위호환·회귀 0). storm 경로 재사용 시 reason "동일 서버 다발"이 감사를 오도하므로 재사용하지 않는다. 클러스터 식별자(대표 fingerprint)는 워커 로그 + decision_store `record()` 선택 인자(E1 recurrence와 동일 방식)로 첨부.

**설정** (`config.py::NoiseGateConfig`): 신규 `cross_host_correlation_enabled: bool=False`, `correlation_sim_threshold: float=0.5`, `correlation_window_seconds: int=120`, `correlation_field_weights_csv: str=""`, `correlation_min_cluster_size: int=2`(대표 포함 이 수 이상일 때만 억제 개시), `correlation_buffer_max: int=1000`(사건창 버퍼 상한 — 메모리 가드).

- **위상 가중은 E4 이후**: E4 그래프 확보 후 `jaccard` 점수에 "동일/인접 토폴로지 노드" 가중을 더한다(단계적 — E2 1차는 필드 유사도만).
- **문헌 기반 강화(§13.1)**: ① 오프라인에서 **itemset mining으로 빈발 알람그룹을 사전 학습**해 상관 규칙 시드로 활용(Fan 2018) — 하드코딩 없는 그룹 자동 발견. ② 원인→증상 전파 순서가 있는 다발에는 **순서 민감 유사도(local alignment)** 를 대안으로(Cheng 2016).

### 4.3 수용 기준
- 서로 다른 호스트의 동일 원인성 다발이 하나의 클러스터로 묶여 대표 1건만 통보, `correlation_min_cluster_size`번째 멤버부터 SUPPRESS+감사(억제≠삭제, step7.5 별도 사유).
- 온라인 판정은 결정적(동일 이벤트 시퀀스 → 동일 판정 시퀀스, `match_cluster` 동점 시 first_ts 오름차순). 단위 테스트로 고정.
- 억제는 step7.5(step3 이후)를 타므로 **심각도3은 군집돼도 각각 PAGE**(step3 단락 유지). `cross_host_correlation_enabled=False`면 detection 미수행·`_detect_storm` 동작 비트 동일(회귀 0).
- 상관 버퍼 sweep(만료 클러스터·빈 스코프 키·버퍼 상한) 테스트 고정. 유사도 미달 다발은 전건 통보(과억제 없음).

---

## 5. E3 — 동적 baseline 이상탐지 (D-079)

### 5.1 문제 (실측)
현행 게이트는 **AI 심각도 상향 연동 지점이 이미 존재**한다: `decide_notification` step1이 `effective_severity = max(폴스타 severity, analysis.ai_message_severity)`를 계산하고(`enable_ai_severity_boost` 플래그·`ai_severity_escalate_only=True` 상향 전용), `max()`가 하향 불가를 보장한다. 그러나 **`ai_message_severity`를 채우는 공급원이 없다**(현재는 LLM 메시지 심각도만). 선진 솔루션(Dynatrace 적응형 baseline·New Relic Holt-Winters·Splunk 적응형 임계)은 **메트릭 시계열의 동적 baseline 이탈**을 이 신호로 쓴다. Plan 50의 정적 z-score는 계절성(일간·주간 피크)에서 오탐이 크다.

→ **E3는 게이트 수정이 아니라, 기존 `ai_message_severity` 상향 슬롯을 채우는 백엔드 신설**이다. 게이트 로직은 무변경.

### 5.2 상세 구현 (로컬·결정적)

**신규 모듈** `src/alarm/domain/anomaly.py`(domain — **순수 Python·stdlib only 확정**, math/statistics만 사용. 1차는 additive Holt-Winters를 직접 구현하고 STL/statsmodels는 정확도 요구 확인 시 2차 강화·인프라 계층 헬퍼):
```python
def holt_winters_fit(series: list[float], period: int) -> HWState | None:
    # additive 삼중 지수평활 적합. len(series) < anomaly_min_periods*period면 None(계산 skip).
def residual_sigma(series: list[float], state: HWState) -> float:
    # 적합 잔차 표준편차
def anomaly_score(state: HWState, sigma: float, value: float) -> float:
    # 잔차 z-score (결정적)
def severity_from_anomaly(score: float, z_high: float) -> int | None:
    # z>hi → 상향 후보 심각도, 그 외 None (상향 전용)

# 알람→메트릭 결정 매핑표 (§실측 보완 — 게이팅). definition_name은 착수 시 실측 확정.
METRIC_SOURCE_BY_KIND = {
    "cpu":    ("server.Cpus",   "Utilization"),
    "memory": ("server.Memory", "Utilization"),
}
```
**데이터 어댑터**(읽기전용·인프라 계층): 소스 `cmm_metric_stat_h/d/m`(보유), 시간정밀도 분기. 고정 SQL + `noise_context_timeout_seconds`(3s) 재사용 + 단기 캐시(Plan 47 패턴). 히스토리 <3주기면 계산 skip→None.

**[실측 정정 — 주입 지점 확정]**: 초안의 "`alarm_context_enricher`가 `analysis.ai_message_severity`에 주입"은 **그래프 순서상 불가능**하다. 실측 배선은 `alarm_context_enricher → alarm_analyzer → (agentic_enricher) → notification_gate`이며, `analysis_result`는 **analyzer가 생성**하므로 enricher 시점에는 존재하지 않는다. 확정 설계(전례: `agentic_enricher_node` L250~262의 상향 가드 변이):
- **계산**: context_enricher의 병렬 수집(asyncio.gather)에 4번째 코루틴으로 편승(기존 타임아웃·graceful 틀 재사용) → 신규 state 키 **`anomaly_severity: Optional[int]`**(`AlarmState` 확장)로 산출만 한다.
- **반영**: `alarm_analyzer_node`의 LLM 결과 파싱 **직후 결정적 후처리**(LLM 무관)로 상향 전용 병합:
```python
anomaly_sev = state.get("anomaly_severity")
if (anomaly_sev is not None
        and getattr(cfg.noise_gate, "dynamic_baseline_enabled", False)
        and anomaly_sev > event.severity                       # 상향 전용
        and anomaly_sev > (result.ai_message_severity or 0)):  # 기존 ai 초과 시에만
    result.ai_message_severity = anomaly_sev
```
- agentic_enricher와 공존해도 안전: 셋(LLM·agentic·anomaly)이 모두 "후보 > 기존" 가드의 상향 전용 변이라 결과는 max와 동일. **게이트(`notification_policy`) 코드 무변경**(§5.1 주장 유지).

**[실측 보완 — 알람→메트릭 매핑 게이팅]**: 어떤 알람에 어떤 시계열을 조회할지가 초안에 없다. 기존 `process_rank.classify_alarm_kind`(cpu|memory 판정, Plan 47-1)를 재사용해 위 `METRIC_SOURCE_BY_KIND` 결정 매핑표로 `cmm_metric_stat_*`의 resource_type·definition_name을 확정하고, kind=None 또는 매핑 부재인 알람(LogMonitor·보안 등 비메트릭 알람)은 계산 자체를 skip→None(상향 없음)한다. 1차 범위는 CPU·메모리(매핑 확실)로 한정하고 디스크·네트워크는 definition_name 실측 후 확장.

**[실측 보완 — 지연 예산·캐시]**: Holt-Winters를 3주기(예: 3주×시간별) 데이터로 매 알람마다 계산하면 3s 예산을 초과할 수 있다. 인프라 어댑터 `src/alarm/infrastructure/polestar_metric_baseline.py`가 `cmm_metric_stat_h` 고정 SQL(읽기전용)로 시계열을 조회·적합하고, baseline 파라미터(HW 상태·잔차 σ)를 **Redis `alarm:baseline:{db_id}:{server}:{kind}`**(TTL `anomaly_baseline_cache_ttl_seconds=3600`)에 캐시한다. 캐시 히트 시 이벤트 시점 연산은 조회+잔차 z-score 계산뿐이다. 캐시 실패는 무시(순수 최적화 — enrich 캐시 전례).

**enricher 연동**: 계절성 없는 하드 플로어/실링 지표는 정적 임계 유지(Splunk ITSI식 적응형+정적 병용).

**설정** (`config.py::NoiseGateConfig`): 신규 `dynamic_baseline_enabled: bool=False`, `anomaly_z_high: float=3.0`, `anomaly_min_periods: int=3`, `anomaly_baseline_cache_ttl_seconds: int=3600`. 게이트의 기존 `enable_ai_severity_boost`와 **AND 조건**(둘 다 True여야 상향 반영).

**[B-3 블로커 해소 — 확정]**: additive Holt-Winters(삼중 지수평활)는 **순수 Python 수십 줄로 구현 가능**하다(외부 패키지 불요). 1차 구현을 순수 Python HW로 **확정**함으로써 ① statsmodels 폐쇄망 반입 협의(B-3) 자체가 소멸하고 ② domain 계층 stdlib-only 원칙과의 긴장(초안의 "인프라 계층 경유 권장" 우회)도 해소된다. STL(statsmodels)은 정확도 요구가 확인되면 2차 강화로 미룬다(인프라 계층 헬퍼). → **B-3는 필수 블로커에서 제거, "2차 강화 시 검토"로 강등**.

**문헌 근거(§13.1)**: Holt-Winters 이상탐지는 Szmit(2012)로 직접 확증되며, ARIMA vs ML 비교(2023)·딥러닝 리뷰(Choi 2021)는 **경량 통계가 폐쇄망·해석성·결정성에서 유리**함을 뒷받침한다. 계절 KPI 비지도 최신 대안인 Donut(VAE, Xu 2018, F 0.75~0.9)은 폐쇄망 모델 반입·GPU 검토가 필요하므로 §13.2 백로그(FI 후보 ③)로 미룬다.

### 5.3 수용 기준
- 계절 피크(정상)가 이상으로 오탐되지 않음(정적 z-score 대비 오탐 감소를 테스트셋으로 검증).
- 수치는 Python 결정적 계산, LLM은 해석만(환각 0). 히스토리 부족 시 None(상향 없음).
- **상향 전용** — `max()` 계약상 폴스타 심각도를 낮추지 않음(SSOT 보존). `dynamic_baseline_enabled=False` 또는 `enable_ai_severity_boost=False`면 게이트 비트 무변경(회귀 0).

---

## 6. E4 — 토폴로지 의존성 그래프 (D-080) [최우선 선행자산]

### 6.1 문제 (실측)
현행 의존성 억제(step6.4)는 enricher가 `collect_dependency=True`일 때 채우는 **`parent_avail_status`(부모 1홉)** 만 본다: `parent_avail_status≠0`이면 SUPPRESS, `None`(미수집·stale)이면 비억제(보수적·R-3). 즉 **조부모→손자 다홉 연쇄는 못 잡는다**. Davis Smartscape·토폴로지 인지 RCA 연구는 **다홉 의존성 그래프를 순회**해 근본원인·연쇄를 판정한다. 그래프 없이는 크로스-호스트 위상 상관(E2 위상 가중)도, Plan 50 결정적 RCA도 불가능하다 — **이 그래프가 노이즈·RCA 공용 병목 자산**이다.

### 6.2 상세 구현 (결정적)

**신규 모듈** `src/alarm/domain/topology.py`(domain 순수함수 + 인프라 계층 로더 분리):
```python
class DependencyGraph:                      # 불변 스냅샷(정적 엣지만 — 상태는 미포함)
    def ancestors(self, node_id, *, max_hops) -> list[str]:  # BFS·방문집합(순환방어)·홉 상한
    def is_cascaded(self, node_id, abnormal: set) -> bool:   # 조상 중 비정상 존재
    def find_root(self, node_id, abnormal: set) -> str|None: # 최상위 비정상 조상(root 후보)
    def descendants(self, node_id) -> set:                   # 연쇄 후보(Plan 50 RCA용)
    def name_of(self, node_id) -> str|None:                  # 리소스 ID→NAME 역조회(root_notified 산출용)
```
**그래프 로더**(인프라·읽기전용): `cmm_resource`의 `AVAIL_DEPEND_RESOURCE_ID`(+`_2`)·`IS_INHERIT_AVAIL_DEPEND` 컬럼으로 엣지 구성(노드=리소스, 엣지=가용성 의존). 그래프는 변경이 드무므로 **장기 캐시**(기존 `noise_context_cache_ttl` 대비 훨씬 긴 TTL·명시적 무효화). 폐쇄망 로컬.

**enricher 확장** (`alarm_context_enricher.py`): `collect_dependency` 경로에서 현재 부모 1홉 상태만 넣던 것을, `multi_hop_cascade_enabled`일 때 **다홉 조상 비정상 여부**를 계산해 `noise_ctx["cascaded"]`(bool)·`noise_ctx["root_resource"]`(최상위 비정상 조상 ID)·`noise_ctx["root_notified"]`(bool — 아래 하이브리드 정책용, 워커 산출값 경유)를 추가. `parent_avail_status`는 하위호환 유지. 신규 키는 noise_ctx 구성 3곳(§7.2 실측 보완)과 `_NOISE_CTX_KEYS` 헬퍼에 일관 반영.

**게이트 확장** (`notification_policy.py` step6.4): 현행
```python
if dependency_suppression:
    parent_avail_status = noise_ctx.get("parent_avail_status")
    if parent_avail_status is not None and parent_avail_status != 0:
        return _decision(SUPPRESS, "의존성 억제 — 부모 …")
```
를 **다홉으로 확장**한다. 억제/강등 분기는 아래 **[재현율 정책 — 하이브리드 확정]** 블록의 코드를 따른다(`cascaded`+`root_notified` 기반, 근본원인 노드는 통과→PAGE 후보). `cascaded` 미제공(1홉 모드·수집 실패)이면 현행 `parent_avail_status` 판정으로 폴백. 정책 모듈은 그래프를 import하지 않음(bool·id만 소비).

**[실측 보완 — 정적 그래프 / 동적 상태 분리 설계]**: `is_cascaded(node, abnormal)`의 `abnormal` 집합은 **동적 값**(AVAIL_STATUS)이라 24h 캐시 대상이 아니다. 반드시 둘을 분리한다:
- **엣지(정적, 장기 캐시)**: `SELECT ID, NAME, AVAIL_DEPEND_RESOURCE_ID, AVAIL_DEPEND_RESOURCE_ID_2 FROM cmm_resource WHERE DTIME IS NULL AND AVAIL_DEPEND_RESOURCE_ID IS NOT NULL`(전량 스캔 아닌 엣지 보유 행만, NAME은 `name_of` 역조회용) → db_id별 `topology_cache_ttl_seconds`(86400) 캐시.
- **상태(동적, 매 이벤트)**: 캐시된 엣지로 조상 ID 집합을 BFS 산출(홉 상한 `topology_max_hops: int=5` — 순환 방어의 이중 안전 겸 비용 가드) 후, **조상 ID들만** `WHERE ID IN (...) AND DTIME IS NULL` 단건 SQL로 AVAIL_STATUS를 신선 조회(행 수 = 홉 상한으로 유계). 조회 실패 시 1홉 폴백(보수적).
- **노드 식별**: 알람 이벤트 → server.Server 리소스 **ID** 해소는 기존 `build_resource_signal_sql`에 **`SVR.ID` 컬럼 1개 추가**(동일 쿼리·하위호환)로 해결한다 — 별도 SQL 불요. `root_resource` 비교는 리소스 ID 기준으로 명시. 또한 `AVAIL_DEPEND_*`는 서버(장비) 간 **가용성 의존** 그래프로, `event.resource_ancestry`(폴스타 트리 경로 문자열)와는 **다른 그래프**다 — 혼용 금지. E4 1차 범위는 서버 수준 연쇄만 커버한다.

**[실측 보완 — 엔진 방언 스코프]**: 현행 노이즈 컨텍스트 고정 SQL은 PostgreSQL 방언(`LIMIT 1`·`polestar.` 스키마) 고정이다. b0(DB2) 알람이 게이트에 들어오면 기존 SQL도 실패→unavailable 폴백이며, E4 신규 SQL(엣지 로더·상태 IN 조회)도 동일하다. **E4 1차 범위는 gp/yd(PostgreSQL)로 명시 한정**하고, b0는 unavailable→1홉 폴백→비억제(보수적)로 동작함을 수용 기준에 포함한다. b0 편입 시 새 DB 체크리스트(D-053·D-057: ①위치 힌트 ②base_url ③엔진 방언 ④스키마 한정)를 따른다.

**[재현율 정책 — 하이브리드 확정(B-5 권고안)]**: 다홉 억제는 "근본원인 노드만 PAGE"를 전제하나, **root의 알람이 실제로 PAGE됐다는 보장이 없다**(파이프라인 미유입·min_severity 드롭·재발생 dedup 가능). 다홉은 1홉보다 억제 반경이 넓어 리스크가 커지므로, **root 통보 여부로 억제 강도를 분기하는 하이브리드**를 채택한다:
- **`root_notified` 산출(워커, 결정적·신규 저장소 불요)**: root 리소스의 서버명을 `DependencyGraph.name_of(root_id)`로 역조회하고, **이미 존재하는 인히비션 상태 `self._active_firings`**(스코프 키 `f"{db_id}|{server_name}"`)에서 `self._active_firings.get(f"{db_id}|{root_server_name}")`이 window 내 활성인지 확인 → `noise_ctx["root_notified"]`(bool)로 주입.
- **게이트 분기(step6.4 확장)**:
```python
if dependency_suppression and noise_ctx.get("cascaded"):
    if noise_ctx.get("root_notified"):
        return _decision(TIER_SUPPRESS, "의존성 억제(다홉) — 근본원인 노드 통보됨")
    return _decision(TIER_DASHBOARD, "의존성 연쇄(다홉) — 근본원인 미통보, 대시보드 강등")
# cascaded 미제공(1홉 모드·수집 실패) → 현행 parent_avail_status 판정 폴백(무변경)
```
- 효과: root가 실제 통보된 연쇄만 SUPPRESS(정밀 억제), 불확실하면 DASHBOARD(억제≠삭제·재현율 우선 D-035 정합). 정책 모듈은 그래프를 import하지 않고 bool·id만 소비(순수성 유지).

**Plan 50 재사용**: 동일 `topology.py`를 RCA `correlation_engine`(계획)이 소비 → 결정적 그래프 BFS RCA의 선행자산(Plan 50 정합).

**문헌 근거(§13.1)**: 그래프 기반 RCA는 MicroRCA(NOMS 2020, **속성 그래프로 이상 전파 모델링**, precision 89%)·MicroHECL(ICSE 2021)로 주류가 확립됐다. 이상 전파 **방향을 엣지 가중**으로 표현하는 attributed graph 형태를 채택하면 E2 위상 가중·Plan 50 인과추론(MicroCause식 시간순 우선)과 자료구조를 공유한다. 트레이스가 없는 collectorinfra 환경에선 트레이스 기반 RCA(Li 2021) 대신 **의존성 그래프 + 메트릭 상관**이 문헌상 현실적 대안이다.

**설정**: 신규 `multi_hop_cascade_enabled: bool=False`, `topology_cache_ttl_seconds: int=86400`, `topology_max_hops: int=5`. (기존 `dependency_suppression`과 AND — 다홉은 의존성 억제의 상위 모드.)

### 6.3 수용 기준
- 다홉 조상 장애 시: root 통보 확인(`root_notified=True`)이면 하위 연쇄 SUPPRESS·**근본원인 노드만 PAGE**(증상보다 원인 — Google SRE·Davis), root 미통보면 **DASHBOARD 강등**(재현율 우선 하이브리드).
- 그래프는 결정적 구성·순회(동일 입력→동일 결과), 순환 의존성 방어(방문 집합)·홉 상한(`topology_max_hops`), 캐시 무효화 정책 명시.
- 수집 실패·`cascaded` 미제공 시 현행 1홉 `parent_avail_status` 판정으로 폴백(보수적). 비PostgreSQL(b0)은 로더가 즉시 None→1홉 폴백→비억제. `multi_hop_cascade_enabled=False`면 무변경(회귀 0).

---

## 7. E5 — 변경/구성 이벤트 상관 (D-081)

### 7.1 문제
"장애의 최대 원인은 변경"이나 현행 게이트·RCA는 배포·구성변경을 보지 못한다. Davis/Watchdog은 **결함 배포를 근본원인으로 지목**(faulty-deployment detection)한다.

### 7.2 상세 구현
- **변경 피드 어댑터**(읽기전용·인프라): 인터페이스만 먼저 고정한다 — `fetch_recent_changes(window_seconds) -> list[ChangeEvent]`(피드 부재/실패 시 빈 리스트, graceful). 구현은 B-2 확정 후: (a) 폴스타 변경이력 테이블(선조사) / (b) 외부 CI·CMDB 연동 / (c) 수동 변경 등록 API 임시(FastAPI 라우트 1개 + JSONL 저장 — decision_store 패턴 재사용).
- **신규 모듈** `src/alarm/domain/change_correlation.py::overlay_changes(incident_window, changes) -> list[ChangeCandidate]`: 알람 발생 시점 직전 창의 변경을 **타임라인 오버레이 + 영향범위(리소스·서비스) 매칭**(순수함수·결정적).
- **enricher 연동**: 변경 근접 여부를 `noise_ctx["change_nearby"]`(bool)·`noise_ctx["change_candidates"]`로 주입. **[계약 일관성 확정]** noise_ctx는 동결 계약(5키)으로 **세 곳에서 구성**된다 — `alarm_context_enricher._noise_unavailable()`, `polestar_noise_context._unavailable()`, 정상 fetch dict — 그리고 **Redis 캐시로 직렬화 왕복**된다(`alarm:noisectx:*`, TTL 300s). 키 불일치를 원천 차단하기 위해 **계약 키 목록을 상수 `_NOISE_CTX_KEYS`로 단일 출처화**하고 세 구성점이 이를 공유하는 헬퍼로 dict를 만들게 리팩토링한다(E4 `cascaded`/`root_resource`/`root_notified`·E5 `change_nearby`/`change_candidates` 추가 시 한 곳만 수정). 캐시된 노후 값(변경 발생 직후 최대 300s 미반영)은 promote 전용 신호라 재현율 위해 없음. 구버전 캐시 항목(신규 키 부재)은 `.get()` 소비로 하위호환.
- **게이트 연동** (`notification_policy.py` step9 보조 조정): 변경 근접 알람은 **억제하지 않고** `promote` 신호로 추가(현행 `promote` 리스트에 "변경 근접(원인성)" 항목 → 승격 우선 기계가 PAGE 근거 보강). Plan 50 RCA에는 최우선 원인 후보로 전달. **억제가 아니라 승격**이므로 재현율 우선 원칙과 정합.
- 플래그 `change_correlation_enabled`(기본 off). config `NoiseGateConfig`에 추가.

### 7.3 수용 기준
- 이상 직전 변경이 알람 컨텍스트·RCA 원인후보에 표시(감사 기록).
- 변경 피드 부재 시 graceful degradation(기능 skip, 게이트 무변경).
- **읽기전용** — 변경 소스 조회만. 변경 실행/롤백은 본 계획 범위 밖(자동복구는 Plan 53 Wave5·별도 거버넌스).

---

## 8. 선행 블로커·결정 선택지 (사용자 확인 필요)

| 블로커 | 내용 | 선택지 |
|--------|------|--------|
| **B-1 (E4)** | 토폴로지 그래프 소스 신뢰도 | (a) `AVAIL_DEPEND`만으로 충분 / (b) CMDB 관계 병합 필요 → E5와 통합 착수 |
| **B-2 (E5)** | 변경 피드 가용 소스 | (a) 폴스타 변경이력 有 / (b) 외부 CI·CMDB 연동(폐쇄망 네트워크존·자격증명 협의) / (c) 수동 등록 임시 |
| **B-3 (E3)** | ~~로컬 이상탐지 라이브러리~~ **해소됨** | 1차를 순수 Python Holt-Winters(stdlib)로 구현 확정(§5.2) — statsmodels 반입 협의 불요. STL은 2차 강화 시 재검토 |
| **B-4** | 착수 범위·순서 | §9 Wave 순서 승인 |
| **B-5 (E4)** | 다홉 억제의 재현율 정책(§6.2) | **권고: 하이브리드** — root 통보 확인(`root_notified`, `_active_firings` 재사용) 시 SUPPRESS, 미확인 시 DASHBOARD 강등. 승인 요청 |
| **B-6 (E2)** | 크로스-호스트 상관 스코프 | **권고: db_id(존) 경계 내 상관** — 존 간(gp↔yd) 상관은 공통 원인(네트워크 등) 실증 후 확장 |
| **B-7 (§15)** | LLM/AI 확장의 로컬 임베딩 모델 반입 | L-1(경계쌍 LLM 판정)·L-3(심각도 상향)은 **기존 `alarm_analyzer` LLM만으로 즉시 가능**(신규 모델 반입 불요) / L-2(의미 근접중복)·L-4(토폴로지+텍스트 융합)는 **폐쇄망 로컬 임베딩 모델 반입·검증** 필요(B-3 statsmodels와 동류 — 사용자·보안팀 확인 후 2차) |
| **B-8 (§14.4)** | 게이트 경계 케이스 호스트 probe(Option A·D-104) | **선행: Plan 64 D-102·B-1(L3 호스트 접근 보안결정)** — 미해소 시 probe inert. 추가 확인: ①경계 predicate 범위(고중요·sev2·TICKET) ②동기 probe 지연 예산(≤2s·캐시·소수 subset) 승인. read-only(uptime)·상향 전용 |

> CLAUDE.md 의사결정 규칙: 방향 확정 전 구현 착수 금지. 본 §8을 사용자와 합의 후 D-077~081을 `docs/02_decision.md`에 등재하며 번호 재확인.

---

## 9. 구현 순서 (Wave) 및 의존성

```
[E1] 재발생 count 관측성 (독립·XS·L1)   ── 즉시 착수 가능
   │
[E4] 토폴로지 그래프 (선행자산·M·L1)  ──┬─► [E2] 크로스-호스트 상관(위상 가중, M)
   │  (Plan 50 RCA와 공용)              │
   │                                    └─► Plan 50 결정적 BFS RCA (본 계획 밖·정합)
[E3] 동적 baseline (독립·M·L1)          ── 병렬 가능
   │
[E5] 변경 상관 (블로커 B-2·M~L)         ── 피드 확보 후
```

| Wave | 항목 | 의존성 | 비용 | 계층 | 선행 블로커 | 상태 |
|------|------|--------|------|------|------------|------|
| **A** | E1 재발생 count 관측성 | 독립 | XS | L1 | 없음 | ✅ **구현 완료 (D-106)** |
| **A** | E4 토폴로지 그래프 | 독립(선행자산) | M | L1 | B-1(=a 확정) | ✅ **구현 완료 (D-107)** |
| **B** | E2 크로스-호스트 상관 | E4(위상 가중) | M | L1 | B-6(=존 경계 확정) | 미착수 |
| **B** | E3 동적 baseline | 독립 | M | L1 | B-3(해소) | 미착수 |
| **C** | E5 변경 상관 | 피드 | M~L | L1/L2 | B-2(=폴스타 이력 확정) | 미착수 |
| **A(우선)** | E6 통보 컨텍스트 보강(§16·L1) | 독립(기존 확장) | S~M | L1 | 없음(즉시) | ✅ **L1 구현 완료 (D-108)** |

우선순위 결론: **E6(통보 컨텍스트 보강 L1) + E1(즉시·소규모 관측성) + E4(선행자산) → E2·E3(병렬) → E5(피드 확보 후)**. **E6은 기존 프로세스 스냅샷(Plan 47-1)·notifier 첨부를 kind 확장·메시지 타깃팅으로 넓히는 것이라 블로커 없이 즉시 착수(사용자 우선순위).** E4는 노이즈 정밀화와 Plan 50 RCA가 공유하는 최우선 자산이므로 Wave A에 포함한다. 실질적 병목은 **E4 토폴로지 그래프**이며 여기서 E2 위상상관·Plan 50 RCA가 파생된다.

---

## 10. 회귀·리스크 통제

- **전 항목 옵트인 플래그·기본 off** → 비활성 시 현행 게이트(D-048) 경로 완전 무변경(회귀 없음).
- **심각도3 단락 불변** — 신규 억제 단계는 심각도3을 절대 억제하지 않음(테스트 고정).
- **억제≠삭제** — 신규 억제도 decision_store 감사. 억제기 메타모니터링에 신규 단계 포함(억제율 이상 경보).
- **결정적 재현성** — 상관·그래프순회·이상탐지 모두 동일 입력→동일 출력 단위 테스트.
- **정책 모듈 순수성** — `notification_policy.py`는 상관/그래프/이상탐지/변경 모듈을 import하지 않음(현행 flapping 패턴). 워커가 산출해 인자 주입.
- **폐쇄망** — 외부 SaaS·tool-calling 비의존. 라이브러리 로컬 반입.
- **`signals` §8.2 스키마 확장은 일괄 1회** — E1~E5가 각자 signals 키를 조각 추가하면 "모든 키 필수" 동결 원칙이 무너진다. 전례(E4 `llm_actionability` 추가)처럼 **의식적 버전업 1회**로 Wave A에서 신규 키(`correlated`·`cascaded`·`root_resource` 등 채택분)를 일괄 확정하고 `_signals()`·기존 스냅샷 단언 테스트를 **repo 전체 grep으로 전수 갱신**한다(Known Mistakes 2026-06-30 E3 — 흩어진 단언 누락 방지). 스키마에 넣지 않을 메타(recurrence 등)는 decision_store 레코드의 별도 최상위 필드로.
- **워커 신규 in-memory 상태는 키 만료 sweep 필수** — E2 상관 버퍼 등 신규 dict는 값 상한(deque/maxlen)만으로 부족하며, 형제 상태(`_gate_dedup`·`_flap_last_seen` 정리 루프)와 일관된 키 sweep을 함께 구현·테스트한다(Known Mistakes 2026-06-29 E2 재발 방지).
- **재기동 휘발성 수용** — E1 count·E2 클러스터·E4 그래프 캐시는 전부 in-memory(단일 워커 `worker-1`)로, 재기동 시 자연 초기화된다(관측성 신호이므로 수용, 영속화는 비범위).

---

## 11. 테스트 계획 (요약)

| 항목 | 단위 테스트(대상 함수) | 통합·회귀 |
|------|----------------------|----------|
| E1 | `_is_duplicate_fingerprint` count/first_seen 집계·기존 억제 판정 비트동일 · **TTL 비교가 last_notified 기준(슬라이딩 창 변질 방지) — 지속 재발 알람이 TTL 후 재통보되는지** | 대표 알람·decision_store에 recurrence 노출 · `record_recurrence` 레코드가 `aggregate()` by_tier/total에서 제외되는지 · 기존 dedup 테스트 통과 |
| E2 | `correlation.jaccard`·`match_cluster` 결정성(동점 시 first_ts 오름차순) · **온라인 의미론(동일 이벤트 시퀀스→동일 판정 시퀀스)** · `signature_tokens` server_name 제외 · 상관 버퍼 키 sweep·상한 | `cross_host_correlation_enabled=False`→detection 미수행·`_detect_storm` 비트동일 · step7.5 사유 문자열 구분 · min_cluster_size 경계 |
| E3 | `anomaly.holt_winters_fit`·`anomaly_score`·`severity_from_anomaly` 계절피크 비오탐(합성 사인+노이즈, 정적 z-score 대비)·히스토리부족 None · **비메트릭 알람(매핑 없음) skip→None** | `dynamic_baseline_enabled`&`enable_ai_severity_boost` AND, off→게이트 무변경 · **상향 가드(후보>severity && 후보>기존 ai) — agentic_enricher와 공존 시 max 유지·`max()` 하향 불가** |
| E4 | `DependencyGraph.is_cascaded`·`find_root`·`ancestors`·`name_of` 다홉 BFS·순환 방어·**홉 상한** | step6.4 `cascaded` 없으면 1홉 폴백, off→무변경 · **root 미통보→DASHBOARD 강등(하이브리드)** · **미지원 엔진(b0)→unavailable→1홉 폴백→비억제** |
| E5 | `change_correlation.overlay_changes` 영향범위 매칭·피드부재 폴백 | step9 promote 경로(억제 아님), off→무변경 |
| 공통 | `arch_check --ci` 계층 위반 0(domain은 stdlib만), 읽기전용 검증 | **심각도3 step3 단락** 회귀 고정(신규 step 7.5·6.4 확장이 step3 뒤에만 위치함을 테스트로 고정), 전 플래그 off 시 현행 비트동일 — 기존 `test_e2_flags_off_regression.py` 패턴으로 **`test_plan60_flags_off_regression.py` 신설**(신규 플래그 전부 off → decide_notification·detection 경로 비트동일) |

---

## 12. 산출물·문서 갱신

- **신규 모듈**: `src/alarm/domain/{correlation,anomaly,topology,change_correlation}.py`(domain 순수함수·stdlib only) + 인프라 계층 `src/alarm/infrastructure/{topology_loader,polestar_metric_baseline,change_feed}.py`(로더/어댑터).
- **신규 테스트**: `tests/test_alarm/test_plan60_flags_off_regression.py`(전 신규 플래그 off 비트동일) + 항목별 단위·통합(§11).
- **기존 코드 갱신(실측 대상)**:
  - `alarm_worker.py`: `_gate_dedup` dict화(`last_notified` 판정 기준) + `_is_duplicate_fingerprint` tuple 집계 + `record_recurrence` 호출(E1), `_detect_correlated_storm` 신설 + `_correlation_clusters` 상태·sweep(E2), `root_notified` 산출(`_active_firings` 재사용, E4), recurrence 메타 state 전달(E1).
  - `decision_store.py`: `record_recurrence()` 신설, `aggregate()`의 비-decision 제외를 `rec.get("type")` 일반화(E1), `record()`에 `recurrence`·클러스터 메타 선택 인자(E1·E2 — 최상위 필드 기록).
  - `alarm_graph.py::AlarmState`: `recurrence: Optional[dict]`(E1)·`anomaly_severity: Optional[int]`(E3) 신규 state 키 추가.
  - `alarm_context_enricher.py`: gather 4번째 코루틴으로 이상 baseline 계산(E3), `noise_ctx`에 `cascaded`/`root_resource`/`root_notified`(E4)·`change_nearby`/`change_candidates`(E5) 주입 — **`_NOISE_CTX_KEYS` 상수로 계약 단일 출처화** 후 구성 3곳 공유(§7.2).
  - `alarm_analyzer.py`: LLM 파싱 직후 결정적 상향 훅 — `state["anomaly_severity"]`를 상향 가드(후보>severity && 후보>기존 ai)로 `result.ai_message_severity`에 반영(E3 — §5.2 확정 설계, context_enricher 주입 아님).
  - `alarm_notifier.py`: recurrence 존재 시 "직전 {window}h {count}회 재발 후 재통보" 표기(E1).
  - `notification_policy.py`: step6.4 다홉+하이브리드(E4, `cascaded`·`root_notified`)·step7.5 크로스상관(E2, `correlated` 인자)·step9 변경 promote(E5) — **인자/`noise_ctx` 키 추가만, 하위호환**.
  - `polestar_noise_context.py::build_resource_signal_sql`: `SVR.ID` 컬럼 추가(E4 노드 식별 — 동일 쿼리·하위호환).
  - `config.py::NoiseGateConfig`: 신규 플래그 `cross_host_correlation_enabled`·`correlation_sim_threshold`·`correlation_window_seconds`·`correlation_min_cluster_size`·`correlation_buffer_max`·`recurrence_audit_every_n`·`dynamic_baseline_enabled`·`anomaly_z_high`·`anomaly_min_periods`·`anomaly_baseline_cache_ttl_seconds`·`multi_hop_cascade_enabled`·`topology_cache_ttl_seconds`·`topology_max_hops`·`change_correlation_enabled`(전부 기본 off/보수값).
- **문서**: 착수 시 `docs/02_decision.md`에 D-077~081 등재(번호 재확인), `docs/17_future_improvements.md` 관련 FI 상태 갱신, Plan 52/53/55 상호참조 추가.
- **근거 자료**: `docs/aiops_benchmark/`(벤더별 기능 구현 레퍼런스·조사 dossier) — 구현 기준선. `docs/plan60_improvement_proposals.md`(심층 검토 개선 제안서 P1~P6) — 본 계획 §3~§7 확정 설계의 상세 근거.

---

## 13. 학술 근거 및 문헌 조사 (Academic Grounding)

Plan 60의 각 항목이 벤더 마케팅이 아니라 **동료심사 학술 문헌의 방법론**에 근거함을 확인하기 위해 10개 검색 쿼리로 문헌을 수집해 7개 기능영역 23편으로 정리했다(OpenAlex, `title.search`+피인용 정렬). 전체 목록·초록 요지는 `docs/aiops_benchmark/noise_cancellation_literature.csv` 및 `noise_cancellation_literature_brief.html` 참조.

### 13.1 항목별 핵심 근거와 계획 반영

**E1 중복·노이즈 억제** — Vaarandi & Guelfi(ACM TOPS 2022)는 **도메인 독립 ML 알람 집계**로 플러딩을 완화하고, Kidwai et al.(Comp&Sec 2020)은 **메타데이터 집계 시 단계(kill-chain) 분류**로 억제 알람에 맥락을 부여한다.
→ 반영: E1의 재발생 count 집계에 **분류 맥락(중요도·패턴)까지 함께 기록**하면 단순 카운트를 넘어 "왜 억제됐는지"가 감사에 남는다(§3.3 대상 3 강화).

**E2 이벤트 상관** — 이 영역이 문헌 근거가 가장 두껍다:
- Jakobson & Weissman(IEEE Network 1993) — 상관의 이론적 기원.
- **Fan et al.(IEEE TIE 2018)** — 산업 알람 플러딩에서 **동적 알람 억제 + itemset mining으로 빈발 알람그룹을 데이터에서 자동 발견**. "수천 개 변수 중 무엇을 억제할지"가 핵심 난제라고 명시.
- **Cheng et al.(Control Eng. Practice 2016)** — 알람 시퀀스를 **지역 정렬(local alignment, Smith-Waterman식)** 로 유사도 비교 — **순서를 고려**하는 유사도.
→ 반영: E2의 초안(Jaccard 필드 유사도, 순서 무시)에 **두 가지 문헌 기반 개선**을 추가한다. ① 사건창 상관을 실시간 그리디로 하되, **오프라인에서 itemset mining으로 빈발 알람그룹을 사전 학습**해 상관 규칙 시드로 활용(Fan 2018) — "그룹 자동 발견"으로 하드코딩 회피. ② 유사도에 **순서 민감 옵션**(정렬 기반)을 대안으로 명시(Cheng 2016) — 원인→증상 전파 순서가 있는 다발에 유효.

**E3 동적 baseline 이상탐지** — 초안의 STL/Holt-Winters가 문헌으로 직접 확증된다:
- **Szmit & Szmit(2012)** — Modified Holt-Winters로 네트워크 트래픽 이상탐지(초안 제안과 동일 기법).
- **Xu et al. "Donut"(WWW 2018)** — 계절 KPI 비지도 이상탐지(VAE), F 0.75~0.9로 지도학습 앙상블 상회.
- Choi et al.(IEEE Access 2021), 시계열 예측 ARIMA vs ML 비교(Future Internet 2023) — 딥러닝이 항상 우월하지 않으며 **경량 통계가 폐쇄망·해석성에서 유리**.
→ 반영: E3의 1차 구현은 **Holt-Winters/STL(결정적·경량)** 유지가 문헌상 정당(폐쇄망·해석성·결정성). Donut(VAE)은 **장래 옵션**으로 §13.2 백로그에 등재(라벨 불요·계절 KPI에 강하나 폐쇄망 모델 반입·GPU 검토 필요).

**E4 토폴로지·RCA** — 초안의 "그래프 기반 결정적 RCA"가 최신 연구 주류와 일치:
- **Wu et al. "MicroRCA"(NOMS 2020)** — **속성 그래프(attributed graph)로 서비스·머신 간 이상 전파를 모델링**, 계측 없이 precision 89%. 초안의 `topology.py` 그래프 순회와 정확히 같은 접근.
- **Liu et al. "MicroCause"(IWQoS 2020)** — 시계열 **인과 경로(PCTS) + 시간순 random walk(TCORW)** 로 상관→인과 분리.
- Gan et al. "MicroHECL"(ICSE-SEIP 2021), Li et al.(IWQoS 2021) — 이상전파 체인 탐색으로 대규모 실시간 RCA.
→ 반영: E4의 다홉 `is_cascaded`/`find_root`(연쇄 억제)에 더해, **이상 전파 방향을 엣지 가중으로 표현**하는 attributed graph 형태를 채택(MicroRCA) — E2 위상 가중과 Plan 50 인과추론(MicroCause식 시간순 우선)의 공용 자료구조가 된다. 트레이스가 없는 collectorinfra 환경에서는 트레이스 기반 RCA(Li 2021) 대신 **의존성 그래프 + 메트릭 상관**이 현실적 대안임을 문헌이 뒷받침.

**예측·로그(Plan 55 연계)** — Gao et al.(IEEE TSC 2020) 클라우드 작업 실패 예측, 디스크 고장 예측(2016/2021), 로그 이상탐지(LogRobust·LogAnomaly·LogBERT)는 **본 계획(노이즈) 범위 밖이나 Plan 55 다중소스 관측의 근거**로 등재.

**AIOps 종합 프레임** — **Notaro et al.(ACM CSUR 2021)** 는 100편을 **개입 시점 윈도우(사전탐지→진단→사후) × 목표**로 5카테고리·14서브 분류한다. 이 프레임으로 보면 collectorinfra의 노이즈 게이트는 "탐지·필터링" 성숙, RCA·예측은 미착수 — 본 계획의 우선순위(E4→예측)와 일치.

### 13.2 문헌이 추가한 백로그 (FI 후보)
- **FI 후보 ①**: 오프라인 itemset mining으로 빈발 알람그룹 사전학습 → E2 상관 규칙 시드(Fan 2018).
- **FI 후보 ②**: 순서 민감 유사도(local alignment)를 E2 유사도 대안으로(Cheng 2016).
- **FI 후보 ③**: Donut(VAE) 계절 KPI 이상탐지를 E3 장래 옵션으로(Xu 2018, 폐쇄망 모델 반입 검토).
- **FI 후보 ④**: attributed graph 엣지 가중(이상전파 방향)으로 E4/Plan 50 통합(Wu 2020, Liu 2020).
> 착수 확정 시 `docs/17_future_improvements.md`에 FI-NNN으로 등재.

### 13.3 조사 방법·한계
- 출처: OpenAlex(피인용·title.search). 10개 검색 쿼리, 도메인 필터(의료·천문 등 오검색 제거) 후 7개 기능영역 23편 선별.
- 한계: 산업 알람 플러딩 문헌은 **공정제어(석유화학·발전)** 맥락이 많아 IT 인프라와 신호 특성이 다르다(적용 시 도메인 차이 유의). 마이크로서비스 RCA 문헌은 **트레이스·계측 전제**가 많아, 트레이스 없는 collectorinfra에는 의존성 그래프 기반 방법으로 치환해 해석했다.

---

## 14. 노이즈 게이트 → 자동 조사·진단 브리핑 트리거 훅 (On-Event Investigation Hook → Plan 64)

> **신규(2026-07-21)**. 본 계획은 "이벤트가 노이즈 게이트를 통과해 **중요**로 판정되면, 운영자의 트리아지 절차(①부하 확인 → ②병목 식별 → ③원인 격리 → ④로그 분석)를 **자동으로 조사**해 이벤트 중요도를 정밀 판단하고 그 결과를 **운영자에게 브리핑**한다"는 요구를 다룬다. 이 조사·브리핑·조치권고 오케스트레이션은 노이즈 게이트의 책임 범위(라우팅 판정) **밖**이며, 신규 **Plan 64(이벤트 자동 조사·진단 브리핑 및 장애 대응 오케스트레이션)**가 구현한다. 본 §은 Plan 60이 제공하는 **트리거 계약**(정방향, §14.1~14.3)과 그 **역방향 — 조사 L3 데이터를 게이트 경계 케이스에 선택적으로 되먹이는 Option A**(§14.4) 를 정의한다(게이트 자체는 옵트인·읽기전용·상향 전용 유지).

### 14.1 책임 분리 (왜 게이트 안이 아니라 위인가)

- **게이트(Plan 60/52)**: 매 알람에 대해 **빠르고 결정적인** PAGE/TICKET/DASHBOARD/SUPPRESS + 중요도 판정(<10s, 저비용). 이것이 "1차 중요도 판단"이다.
- **자동 조사(Plan 64)**: PAGE로 판정된 사건에 한해 OS 현황을 조사(top/uptime·병목·프로세스·로그)해 **2차(정밀) 중요도 판정 + 브리핑**을 만든다. 조사는 사건당 수 초~수십 초·호스트 접근을 수반하므로 **모든 알람에 사전 실행하면 게이트 지연 예산(<10s)을 파괴**한다. 따라서 게이트가 먼저 결정하고, **조사는 사후(post-gate) 비차단(fire-and-forget) 트리거**로 돈다(Plan 50 §8.2 push 훅 재사용).
- **닭-달걀 회피**: 조사 결과의 중요도는 게이트 판정을 **소급 변경하지 않는다**. E3와 동일하게 **상향 전용(escalate-only)** — 조사가 더 심각함을 발견하면 후속 통보를 승격/에스컬레이션하되, 억제를 되돌리지 않는다(재현율·비용 비대칭 원칙 유지).

### 14.2 트리거 계약 (Plan 60이 제공 · Plan 64가 소비)

- **발화 지점**: `notification_gate` 노드가 최종 `NotificationDecision`을 산출한 직후. `tier == PAGE`(또는 config `investigation_trigger_min_tier`)일 때 조사 트리거를 **비동기 emit**한다. 게이트의 반환·라우팅 경로는 무변경(트리거는 부수효과, 실패해도 게이트 판정에 영향 없음).
- **전달 페이로드**: 이미 게이트가 보유한 값만 재사용 — `AlarmEvent`(대상 서버/hostname·db_id·resource·심각도), `NotificationDecision`(tier·reason·signals·fingerprint), E1 `recurrence`·E2 클러스터 메타·E4 `root_resource`/`cascaded`. 신규 수집 없음(조사 자체는 Plan 64가 수행).
- **재사용 배선**: Plan 50 §8.2가 정의한 push 진단 훅(`diagnosis_graph`의 `trigger="push"`, `source_alarm=event`)에 그대로 연결한다. 즉 Plan 60의 훅 = "게이트 PAGE 결정을 Plan 50 push 트리거로 잇는 1줄 배선" + Plan 64가 그 위에 L3 조사·브리핑·조치권고를 얹는다.
- **중복 억제 연동**: E1 재발생 dedup으로 억제된 재발 알람은 게이트 그래프에 진입하지 않으므로(§3.1) 트리거되지 않는다 — 동일 사건 반복 조사 폭주 자동 방지. E2 크로스-호스트 클러스터는 **대표 1건만** PAGE되므로 조사도 대표 1건만 발화(연쇄 자식은 억제 → 조사 안 함) = 조사 대상의 노이즈 캔슬링이 자연 상속된다.

### 14.3 경계 (Plan 60에 넣지 않는 것)

- L1(DB/API) + **L3(실호스트 읽기전용 명령 top/journalctl/dmesg)** 수집, LLM 브리핑 합성, 중요도 2차 판정, **이벤트 메시지 분석 기반 타깃 컨텍스트 보강(생존 통보 강화 — 억제와 보강은 노이즈 캔슬링의 두 축, Plan 64 §4.8)**, `renice`/`kill` **조치 권고**(실행은 운영자 승인 — D-003 유지)는 **전부 Plan 64 범위**다. Plan 60은 조사 로직을 포함하지 않는다(게이트 순수성·읽기전용·<10s 예산 보존).
- **옵트인**: `investigation_trigger_enabled: bool=False`(`NoiseGateConfig`). 비활성 시 게이트 경로 비트동일(회귀 0). 활성 시에도 트리거는 비차단이라 게이트 지연에 무영향.
- **결정**: 트리거 훅·조사 오케스트레이션·L3 보안통제·조치권고 거버넌스는 **Plan 64의 D-101~D-103**으로 등재한다(본 계획의 D-077~081과 분리 — Plan 60은 게이트, Plan 64는 조사·대응).

### 14.4 역방향 — 조사 L3 데이터의 게이트 경계 케이스 선택적 활용 (Option A · D-104) [Plan 64 L3 보안결정 선행]

> **신규(2026-07-21)**. §14.1~14.3이 "게이트 → 조사"(정방향 훅)라면, 본 §은 **역방향** — 조사에 쓰는 **L3 호스트 실측 데이터를 게이트의 경계 케이스 판정에 선택적으로 되먹여** 중요도 정확도를 높인다. 핵심 제약: **전 알람이 아니라 판정이 흔들리는 소수 경계 케이스에만**, **상향 전용(escalate-only)**, **경량·캐시·바운드**. §15.3 **L-1(COLA식 하이브리드 — 비싼 신호를 불확실 경계 케이스에만)** 와 동일 원리를 L3 호스트 데이터에 적용한 것이다.

**왜 필요한가(직관)**: 게이트가 쓰는 L1 신호(중요도 등급·의존성)는 중요도의 **프록시**이고, 호스트 실측 부하는 **그라운드 트루스**에 가깝다. "메타상 고중요 서버인데 라우팅은 TICKET에 떨어진" 경계 케이스에서, 실제 OS가 과부하면 **PAGE로 상향**하는 것이 정확하다. 다만 이를 **모든 알람에 하면 닭-달걀·지연 예산 파괴**(§14.1)이므로, **경계 케이스에만 초경량 probe**로 tie-break한다.

**14.4.1 발화 조건 (결정적 L1 사전 게이트 — probe 전에 평가)**. 아래 **전부** 충족 시에만 probe:
- `gate_host_probe_enabled=True`(옵트인) **AND** Plan 64 L3 접근 가용(`l3_host_access_mode` 설정·D-102/B-1 해소).
- **잠정 판정(L1 only) tier ∈ {TICKET, DASHBOARD}** — 이미 PAGE면 probe 무의미(상향 여지 없음).
- **importance == 높음**(`gate_host_probe_min_importance`) — "고중요인데 PAGE 못 간" 케이스로 한정.
- **effective_severity == 2** — 심각도3은 step3 단락으로 이미 PAGE, 심각도≤1은 호스트 접근 가치 없음.
- **유지보수·재발생 dedup·스톰으로 이미 억제된 알람 제외** — 억제된 노이즈에 호스트 접근 낭비 금지.
→ 결과적으로 **"고중요 서버의 심각도2 알람이 TICKET으로 떨어진 소수"** 로 좁혀진다(probe 볼륨 유계).

**14.4.2 probe (경량·캐시·바운드)**:
- Plan 64 `host_diagnostic_collector.py`를 **최소 프로파일 = `uptime`(loadavg)만**(+캐시된 코어 수) 재사용. **top/journalctl/dmesg는 쓰지 않는다**(그건 Plan 64 정밀 조사 몫). 단일 명령·읽기전용.
- 하드 타임아웃 `gate_host_probe_timeout_seconds=2.0`. 캐시 `gate:probe:{db_id}:{server}` TTL `gate_host_probe_cache_ttl_seconds=60` → E1 재발·E2 클러스터 자식·동일 호스트 후속이 **재수집 안 함**(정방향 조사가 이미 수집한 스냅샷도 같은 캐시로 공유 가능 → 장래 Option B의 토대).

**14.4.3 판정 활용 (escalate-only·불변식)**:
- load ratio = loadavg(1m·5m) / 코어수. `ratio ≥ gate_host_probe_load_ratio(2.0)`이면 **잠정 tier를 1단계 승격**(TICKET→PAGE / DASHBOARD→TICKET) — 기존 `_priority`·`_TIER_RANK`(page3>ticket2>dashboard1>suppress0) **1단계 이동 기계 재사용**(step9 승격과 동일 의미). 사유 "경계 케이스 호스트 실측 부하 이상(load {ratio})" + decision_store 감사.
- ratio 정상 **또는 probe 실패/타임아웃/불응** → **잠정 판정 유지(무변경)**. 실패는 **절대 하향·억제로 쓰지 않는다**(보수적 유지 = 재현율 우선).
- **불변 제약**: probe는 **억제를 되돌리거나 새로 억제하지 않는다**, **하향 없음**, 심각도3 단락·유지보수 억제를 침범하지 않는다.

**14.4.4 통합 지점 (게이트 순수성 보존)**:
- 오버레이는 **`notification_gate` 노드(오케스트레이션)** 에 둔다 — **순수 `decide_notification`(domain)에는 I/O를 넣지 않는다**(정책 모듈 순수성 원칙 §10 유지). 흐름: 워커 → `decide_notification`(L1) → **잠정 판정** → 게이트 노드가 14.4.1 predicate 평가 → (조건부 probe) → 1단계 승격 or 유지 → 감사 → 라우팅.
- **잠정 판정을 애매성 오라클로 재사용** → 워커/게이트에 매트릭스 로직 중복 없음.

**14.4.5 회귀·결정성**:
- probe 값은 외부·시변(순수함수처럼 재현 불가)이므로, **주입된 probe 값**으로 판정 로직을 결정적 단위테스트한다(값이 주어지면 승격/유지 결정은 결정적).
- `gate_host_probe_enabled=False` → predicate 미평가·probe 미수행·게이트 **비트동일(회귀 0)**. Plan 64 L3 미가용이어도 자동 no-op(접근 모드 없으면 발화 안 함).

**설정**(`NoiseGateConfig`): `gate_host_probe_enabled: bool=False`, `gate_host_probe_min_importance: str="높음"`, `gate_host_probe_timeout_seconds: float=2.0`, `gate_host_probe_load_ratio: float=2.0`, `gate_host_probe_cache_ttl_seconds: int=60`. L3 접근 자체는 Plan 64 `l3_host_access_mode`(A/B/C)·허용목록 재사용(신규 접근 경로 없음).

**결정·선행**: **D-104**(게이트 경계 케이스 선택적 호스트 probe — 동기·상향 전용·Plan 64 L3 최소 프로파일 재사용). 착수 시 등재(현재 D-번호 최댓값 D-103 실측 → D-104 부여, 등재 직전 재확인). **선행: Plan 64 D-102·B-1(L3 호스트 접근 보안결정)** — 미해소 시 probe는 inert(옵트인 off·접근 모드 없음). read-only(uptime)로 D-003 정합. 블로커 **B-8**(§8).

> 상호참조: **Plan 64**(구현), **Plan 50 §8.2**(push 훅 재사용), **Plan 51 §3(USE·60초 트리아지)·§6(유형별 플레이북)·§9(L3 보안통제)**(조사 데이터·기법·안전), **Plan 53 Wave2·4**(진단 MVP·L3 보안결정), **Plan 62 Phase P2·P4**(RCA·자동복구). §14.4는 **§15.3 L-1**(COLA 하이브리드)·**Plan 64 §7(L3 수집기·D-102)** 와 짝을 이룬다.

---

## 15. LLM·AI 활용 방안 — 역할 명확화 및 문헌 기반 확장

> **신규(2026-07-21)**. 사용자 지적("노이즈 캔슬링 계획에 LLM의 역할이 명확하지 않다")을 수용해, ① 현행 게이트에서 LLM이 **실제로 하는 일**을 실측으로 명확화하고, ② LLM/AI를 노이즈 캔슬링에 활용하는 방안을 **최신 문헌(2023–2026)**으로 근거화하며, ③ **D-035(결정적=판단, LLM=보조) 경계**를 재확정한다. §13이 결정적 방법(Holt-Winters·MicroRCA·itemset)의 학술 근거였다면, 본 §은 **LLM/AI 축의 역할·근거·확장·경계**를 전담한다.

### 15.1 현행 LLM/AI 접점 (실측 — "지금 LLM이 하는 일")

당초 §1.3 원칙 #1은 "LLM은 사전계산된 증거의 **해석·설명자로만**"으로 서술했으나, 실측 결과 현행 게이트의 LLM은 **해석을 넘어 경계된 분류·상향 후보 제시**까지 수행한다. 접점을 한곳에 모은다:

| 접점 | 위치(실측) | LLM이 하는 일 | 판정 계약(경계) | 플래그 |
|------|-----------|--------------|----------------|--------|
| **AI 메시지 심각도 상향** | `alarm_analyzer.py` L321~332 → `notification_policy` step1(L159 소비) | 자유텍스트 로그(`conditionLog`)를 읽어 구조화 severity가 놓친 심각도 **상향 후보** 제시 | **상향 전용**(`max()`, 하향 불가·SSOT 보존) | `enable_ai_severity_boost` |
| **액션가능성 분류** | `alarm_analyzer.py` L255·L337~339(운영자 피드백 few-shot) → `notification_policy` step9 L300~302 | 유사 과거 알람의 **운영자 라벨을 few-shot**으로 `actionable`/`noise` 분류 | 보조 조정(actionable→promote / noise&저severity→suppress), **1단계·승격 우선** | `enable_llm_actionability` |
| **에이전틱 보강** | `agentic_enricher.py` L250~262 | 도구 기반 추가 수집으로 심각도 **상향 후보** 산출 | 상향 전용 가드(후보>severity && 후보>기존 ai) | (게이트 활성) |
| **브리핑 합성** | Plan 64 §6(§14 훅으로 위임) | 조사 증거를 운영자용 **자연어 브리핑으로 요약·인용** | 서술만(판정 아님) | `investigation_trigger_enabled` |

→ **현행 LLM의 3역할**: ① escalate-only 심각도 상향, ② 피드백 학습형 actionable/noise **보조 분류**, ③ (Plan 64) 브리핑 요약. 모두 **결정적 게이트의 최종 티어 결정을 대체하지 않고** 상향·승격·서술로만 작용한다. 본 §으로 §1.3 원칙 #1을 정정: LLM은 **해석·요약 + 경계된 분류·상향 후보 제시**까지 하되, **억제·최종 티어·심각도 플로어·심각도3 단락은 결정적 코드가 전담**한다.

### 15.2 문헌이 말하는 LLM/AI의 노이즈 캔슬링 역할 (2023–2026)

- **COLA — Kuang et al., "Knowledge-aware Alert Aggregation in Large-scale Cloud Systems: a Hybrid Approach"(ICSE-SEIP 2024, arXiv 2403.06485)** — **하이브리드 알람 집계**의 대표 근거. 결정적 상관 마이닝(시·공간 통계)이 **빈발 알람을 1차 처리**하고, **불확실·저신뢰 알람쌍에만 LLM 추론 모듈을 선택 투입**(SOP 지식·In-Context Learning). F1 **0.901~0.930**(3개 프로덕션셋), SOTA 상회하면서 효율 유지. → **collectorinfra의 "결정적=판단·LLM=보조"(D-035)와 정확히 같은 아키텍처**. "LLM은 통계가 불확실한 경계 케이스에만"이라는 선택 투입이 비용·환각·결정성 문제를 동시에 해소한다(가장 강한 설계 근거).
- **MDPI Electronics 2024, 13(22):4425 — "Leveraging LLMs for Efficient Alert Aggregation in AIOps"** — 2단계 집계. 의미유사도-단독/통계-단독이 **모두 한계**(인과 무시 / 희소 알람 취약) → 결합. LLM의 가치는 **희소·의미적 알람**(통계 빈도가 낮아 결정적 규칙이 못 잡는 것)에서 나온다.
- **DiLink — Ghosh et al., "Dependency Aware Incident Linking in Large Cloud Systems"(Microsoft, arXiv 2403.18639, 2024)** — 인시던트 링킹에 **텍스트 임베딩 + 서비스 의존성 그래프**를 Orthogonal Procrustes로 정합. F1 **0.96**, **610개 서비스 배포**. → **E4 토폴로지 그래프 + 의미 임베딩 융합**이 실배포된 패턴임을 확증(E4 위상 + 텍스트를 함께 쓰면 정밀도가 오른다).
- **Oasis — Jin et al., "Assess and Summarize: Improve Outage Understanding with LLMs"(ESEC/FSE 2023 Industry, Microsoft, arXiv 2305.18084)** — 파인튜닝 GPT-3.x로 **아웃티지 영향평가 + 인간가독 요약**. 영향평가 컴포넌트는 MS에서 **3년+ 실운영**. → **§14→Plan 64 브리핑**(LLM=요약·인용)의 산업 검증.
- **SOC LLM 한계 연구 — "Possibilities and limitations of using LLMs for alert classification and prioritisation in SOCs"(Expert Systems with Applications, S0957417426021032)** — 핵심 결론: **"LLM은 초기 triage는 보조 가능하나, 프라이오리티화(우선순위·심각도 최종 판정)에는 신뢰 불가."** → **D-035 경계의 직접 근거**: LLM에 최종 심각도/억제 결정을 위임하지 말 것. 현행 escalate-only·보조조정 설계가 이 한계와 정합.
- **(인접) GPTrace — "Effective Crash Deduplication Using LLM Embeddings"(arXiv 2512.01609)** — 크래시 스택트레이스를 **LLM 임베딩 → HDBSCAN 군집**으로 중복 제거. → E1 정확지문(SHA1)이 놓치는 **의미적 근접중복**의 임베딩 기반 대안(단, 크래시 도메인 — 알람 적용 시 신호 특성 차이 유의).
- **(배경) SOC 알람 피로 서베이(arXiv 2605.08316)** — 알람 피로 규모·무시율이 노이즈 캔슬링의 동기임을 재확인(벤더/서베이 수치는 인용만·독립검증 아님).

### 15.3 문헌 기반 LLM/AI 확장 제안 (E1~E5 매핑 · 전부 옵트인 · D-035 정합)

각 제안은 **결정적 경로를 1차·기본으로 유지**하고 LLM을 **선택적·상향/주석 전용**으로만 얹는다(COLA 하이브리드 원칙). **자동 억제를 LLM 단독으로 판정하지 않는다.**

- **L-1 (E2 강화) — 경계쌍 LLM 판정(COLA식)**: E2 결정적 Jaccard가 **임계 근처(sim ∈ [τ−δ, τ+δ])** 인 저신뢰 클러스터 경계쌍만 LLM에 "같은 사건인가?"를 질의 → **군집 합류 승인/기각 라벨**로만 사용. **자동 억제는 여전히 결정적 `correlation_min_cluster_size`가 판정**(LLM 라벨은 승격·주석 근거). 빈발·고신뢰 다발은 LLM 미호출(비용·지연 가드). off 시 순수 Jaccard 경로. → COLA 근거. 기존 `alarm_analyzer` LLM만으로 구현 가능(신규 모델 반입 불요).
- **L-2 (E1 강화) — 의미적 근접중복 주석**: SHA1 지문이 다르지만 의미적으로 같은 재발(메시지 표현만 다른 경우)을 **임베딩 유사도**로 감지해 **재발 count 병합 후보로 주석**(GPTrace 패턴). **억제 판정은 결정적 지문(compute_fingerprint) 유지**, 임베딩은 관측성 보강만. **폐쇄망 로컬 임베딩 모델 반입 = 블로커 B-7**.
- **L-3 (심각도) — escalate-only 유지·강화**: 현행 `ai_message_severity`를 Oasis 영향평가처럼 **자유텍스트 로그의 심각도 신호 추출**에 계속 활용하되, SOC-한계 연구에 따라 **최종 심각도 하향·억제 결정은 절대 위임 금지**(`max()` 계약 유지). 이미 구현된 설계가 문헌 정합 — 별도 착수 불요, 근거만 명문화.
- **L-4 (E4 융합·장래) — 토폴로지+텍스트 임베딩(DiLink식)**: E4 의존성 그래프에 **알람 텍스트 임베딩을 융합**해 연쇄·root 귀속 정밀화. E4 그래프 확보 후 2차. **폐쇄망 임베딩 반입 = 블로커 B-7**.
- **L-5 (브리핑) — Plan 64 위임**: LLM 요약·인용 브리핑은 §14 훅 → Plan 64 §6(Oasis 산업검증). **게이트엔 넣지 않음**(<10s 지연 예산 보존).
- **actionability(현행) 문헌 정합 확인**: `llm_actionability`(few-shot)는 SOC 연구의 "triage 보조는 가능"에 해당하며, step9 보조조정(1단계·승격 우선)으로 **최종 판정을 대체하지 않아** 한계를 준수한다 — **현 설계 유지**(문헌 근거 추가만).

> **D-번호 없음**: L-1~L-5는 모두 **기존 D-035(결정적=판단·LLM=보조)·D-048(게이트) 원칙 내 정밀화**이므로 신규 D-번호를 부여하지 않는다. 로컬 임베딩 모델 반입(L-2·L-4)만 신규 블로커 **B-7**로 관리한다.

### 15.4 D-035 경계 재확정 (불변식)

- **LLM/AI가 하는 것**: ① escalate-only 심각도 상향 후보, ② 경계 케이스 군집 라벨(승인/기각), ③ actionable/noise **보조 힌트**, ④ 의미적 근접중복 주석, ⑤ 브리핑 요약·인용.
- **결정적 코드가 전담(LLM 위임 금지)**: **최종 티어 결정, 억제 확정, 심각도 플로어(`max()`), 심각도3 단락, `correlation_min_cluster_size` 군집 억제.**
- **불변 계약**: LLM은 **억제를 되돌리거나 새로 억제하지 않는다**(재현율 우선·비용 비대칭). LLM 라벨은 비결정적이므로 **군집 자동 억제의 최종 판정에서 제외**(라벨=승격/주석 근거로만) → §11 결정성 테스트 유지. 전 제안 옵트인·기본 off·회귀 0.
- 근거: SOC-한계 연구(프라이오리티화 신뢰 불가)·COLA(경계 케이스만 LLM)·D-035.

### 15.5 문헌 검증 상태·한계

- **검증(서지·방법 확인)**: COLA(ICSE-SEIP 2024)·DiLink(Microsoft 2024)·Oasis(FSE 2023)·MDPI 4425(2024) — 서지·방법·핵심 수치를 WebFetch/검색으로 확인.
- **미검증(검색 표면·초록 기준)**: SOC-한계 연구(2026)·GPTrace(2025)·알람 피로 서베이(2026)는 검색 스니펫·초록 기준이며 본문 독립검증 아님. 효과 수치는 인용만.
- **도메인 차이**: GPTrace=크래시 트레이스, COLA/DiLink=대규모 클라우드(SOP·트레이스 전제 일부). collectorinfra는 **폐쇄망·트레이스 부재·SOP 미비** 환경이므로, 임베딩 모델 반입(B-7)·SOP 부재를 적용 시 유의. 근거표: `docs/aiops_benchmark/noise_cancellation_literature.csv`(LLM 활용 6편 추가 등재).

---

## 16. E6 — 통보 컨텍스트 보강: 메시지 기반 L1 조회·첨부 (Plan 64 §4.8의 L1 선구현) [D-108 — 구현 완료]

> **신규(2026-07-21)**. 사용자 지시: "이 요건(이벤트 메시지 분석→필요정보 직접 조회→운영자 전달)에 필요한 **Plan 64 §4.8 기능 중 즉시 구현 가능한 L1 부분을 Plan 60에서 우선 구현**하라." 노이즈 캔슬링의 두 축(억제+보강) 중 **보강의 L1 부분**을 게이트 인프라 재사용으로 편입한다. L3 심화(top 실시간·pidstat·us/sy/wa 분해·journalctl 원문)는 Plan 64 §4.8이 보안결정(D-102·B-1) 후 확장.

### 16.1 실측 — 핵심 요건은 **이미 CPU/메모리에 구현·배선**돼 있다

사용자 요건의 급소("어느 프로세스가 원인인지 운영자에게 전달")는 **CPU/메모리 알람에 한해 이미 end-to-end 구현**돼 있다(Plan 47-1, 실측):

| 단계 | 실측 위치 | 동작 |
|---|---|---|
| 수집 | `alarm_context_enricher.enrich_processes` | `classify_alarm_kind`(cpu\|memory) → 폴스타 REST `list_by_hostname` → `select_top_processes`(마스킹·상위N) → `ProcessSnapshot` |
| 상태 | `alarm_graph.AlarmState.process_snapshot` | 그래프 state로 운반 |
| **전달** | `alarm_notifier._process_table_html`(L51·L106~109) | **"영향 프로세스 — {metric} 상위 N" 표를 통보 본문(workb)에 첨부** |
| UI/API | `app.js renderProcessEvidence` · `alarm.py process_snapshot` | 대시보드·API 응답 노출 |

→ **즉 CPU 90% 통보에는 이미 "원인 프로세스 상위 N"이 붙는다**(옵트인 `process_enrich_enabled`). **L3·보안결정 불요.** 사용자 요건은 "신규 구축"이 아니라 **범위 확장**이 남았다.

### 16.2 Plan 60에서 우선 확장할 갭 (전부 L1·블로커 없음)

1. **kind 확장**: `classify_alarm_kind`가 **cpu\|memory만** 판정(실측 `process_rank.py` L52) → **disk·network·process-down·log** 추가 + kind별 **L1 보강 프로파일**(디스크=관제 용량지표 L1, 프로세스다운=생존·재시작 이력 L1, 로그=관제 `conditionLog` 시그니처 L1).
2. **메시지형 알람 타깃팅**: 구조화 kind 불명한 **자유텍스트(`conditionLog`) 알람**(LogMonitor 등)은 `alarm_analyzer` LLM이 메시지를 분류해 프로파일 후보 제시 — **서술·추가조회 전용**(오분류해도 통보·판정 무영향, D-035).
3. **부하 추이 첨부**: 현재는 프로세스 **단면**만 → "정상 대비 N배" 추이 서술은 E3(§5 `cmm_metric_stat` baseline) 구현 시 합류.
4. **첨부 일반화**: `_process_table_html`(프로세스 전용)을 **kind별 보강 블록** 조립기로 일반화(이미 있는 첨부 지점·마스킹 재사용).

### 16.3 구현 (기존 파이프라인 재사용·확장 — 신규 프레임워크 없음)

- **`process_rank.classify_alarm_kind` 확장**(disk/network/process/log 키워드 추가, 순수) + 신규 `src/alarm/domain/enrichment_profile.py`(kind→L1 프로파일 매핑·요지 조립, 순수·stdlib).
- **`enrich_processes` 패턴을 kind별 L1 컬렉터로 일반화**(인프라·읽기전용): 프로세스=`list_by_hostname`(기존), 디스크/로그=관제 L1 소스. 각 자체 try/except graceful(한 항목 실패가 통보를 막지 않음 — 기존 gather 패턴).
- **`alarm_notifier`**: `_process_table_html`을 kind별 보강 블록으로 일반화(첨부 지점 L106~109 재사용). `signals` §8.2 동결 스키마 **밖** 별도 첨부(E1 recurrence 방식).
- **트리거·범위**: 게이트가 tier ≥ `enrichment_min_tier`(기본 PAGE, TICKET 확장 가능)로 통보 결정 시 보강(post-gate). 재발생 dedup·클러스터 대표 상속(§3.1·§4.2) → 보강도 대표 1건. **라우팅 불변**(첨부만 — §14.4 probe는 심각도 상향 판정, §16은 정보 첨부).

### 16.4 안전·회귀·경계

- **읽기전용**(L1 SELECT·REST GET)·D-003 정합. `mask_args`(기존) 마스킹 계승. 조치(renice/kill)는 범위 밖(권고도 Plan 64 §8).
- **옵트인·회귀 0**: 기존 `process_enrich_enabled`(CPU/메모리) 경로 무변경 + 신규 kind는 `message_enrichment_enabled`(기본 off) 뒤. 비활성 시 통보 비트동일. LLM 분류 실패→결정적 프로파일 폴백.
- **경계**: L3(top 실시간·pidstat·us/sy/wa 분해·journalctl/dmesg 원문)·프로세스 시계열 추이는 **Plan 64 §4.8**(D-102·B-1 후). Plan 60 §16 = **현재 단면 + 관제 L1 + (E3 시)DB 추이**까지.

### 16.5 설정·결정·수용 기준

- **설정**(`NoiseGateConfig`, 신규 — Plan 64 §4.8과 공유): `message_enrichment_enabled: bool=False`, `enrichment_min_tier: str="PAGE"`, `enrichment_l1_timeout_seconds: float=3.0`, `enrichment_profile_map_csv: str=""`(kind→프로파일 오버라이드).
- **결정**: **D-108**(이벤트 메시지 분석 기반 타깃 컨텍스트 보강 — **초안 D-105는 D-101~105 예약블록 충돌로 D-108 재부여**, §8 "등재 직전 번호 재확인" 규칙). **구현 단계 분리 — Plan 60 §16 = L1 선구현(구현 완료·블로커 없음: CPU/메모리 기구현 + kind 확장·메시지 타깃팅), Plan 64 §4.8 = L3 심화(D-102·B-1 후)**. 단일 결정·2단계. L3 단계는 Plan 64 §4.8 착수 시 D-105(또는 재확인 번호)로 별도 등재.
- **구현(2026-07-21)**: `process_rank.classify_alarm_kind`(cpu\|memory→+disk/network/process/log), 신규 domain `enrichment_profile.py`(순수), `alarm.py::MessageEnrichment`, `alarm_context_enricher.build_message_enrichment`(disk/network=host-wide 스냅샷·process/log=요지만 graceful·신규 SQL 0), `alarm_notifier`(kind별 보강 블록 일반화·cpu/memory 표 비트동일), `alarm_graph.AlarmState.enrichment`, `config.NoiseGateConfig`(message_enrichment_enabled·enrichment_min_tier·enrichment_l1_timeout_seconds·enrichment_profile_map_csv), `api/routes/alarm.py`. **cpu/memory 전용 가드**로 기존 process_enrich 회귀 방지(실측). 메시지형 LLM 분류는 결정적 프로파일로 대체(서술 전용·후속). 검증: `test_enrichment_profile.py`·`test_message_enrichment.py`·`test_alarm_process_rank.py`·`test_plan60_flags_off_regression.py`.
- **수용 기준**: ① CPU/메모리 외 디스크·로그·프로세스다운 알람도 통보에 kind별 L1 컨텍스트 첨부. ② 메시지형 알람 LLM 분류 프로파일 동작(실패 시 결정적 폴백). ③ `message_enrichment_enabled=False`·기존 `process_enrich_enabled` 경로 **비트동일**(회귀 0). ④ 라우팅 티어 불변(보강은 첨부만).

---

## 17. 변경 이력

| 날짜 | 변경 | 사유 |
|------|------|------|
| 2026-07 | Plan 60 최초 작성 | Plan 52(D-048) 구현 완료 후, 선진사례 벤치마킹으로 5개 정밀화·확장 항목(E1~E5) 도출. 회귀 없는 옵트인 증분·결정적 원칙 계승. D-077~081 부여(등재 전 번호 재확인). |
| 2026-07 | §2~§7·§11·§12 실측 상세화 | `notification_policy`·`alarm_worker`·`alarm_context_enricher`·`config.py::NoiseGateConfig` 실측(함수·라인·플래그) 반영. 인히비션·의존성 억제·AI 심각도 상향·LLM 액션가능성이 **이미 구현·플래그화**됨을 확인 → E1~E5를 "신규 구현"에서 **기존 함수 확장/정밀화**로 재정의. 각 항목에 대상 함수·변경 diff·신규 config 필드·회귀 경계(플래그 off 비트동일) 명시. |
| 2026-07 | **D-번호 재조정 D-067~071 → D-077~081** | 초안 예약 D-067·068이 그 사이 타 작업(D-067 드리프트 가드·D-068 폼필 EAV)에 **등재 완료**되고, D-069~071도 Plan 61이 D-072~076을 예약하며 연속 블록이 막힘. "등재 직전 번호 재확인" 규칙(§헤더)에 따라 예약 최댓값(D-076) 위 연속 빈 블록 **D-077~081**로 재부여. 상위 로드맵 Plan 62 §7·B-5가 이 조정을 추적. |
| 2026-07 | 상위 로드맵 Plan 62 상호참조 추가 | 본 계획을 Plan 62 마스터 로드맵의 **Phase P1**(노이즈·상관 완성)에 정합. E4 토폴로지 그래프는 P2(RCA)의 선행자산. |
| 2026-07 | §13 학술 근거 추가 | OpenAlex 10개 쿼리·7개 기능영역 23편 조사. E2(itemset mining·local alignment)·E3(Donut·Holt-Winters)·E4(MicroRCA·MicroCause) 방법론이 각 항목을 확증. 문헌 기반 4개 FI 후보(§13.2)·조사 한계(§13.3) 등재. 근거표: `docs/aiops_benchmark/noise_cancellation_literature.csv`. |
| 2026-07-13 | **개선 제안(P1~P6) 확정 설계 반영** | `docs/plan60_improvement_proposals.md`의 제안을 계획 본문에 통합. **E1**: `last_notified` 판정 기준·`record_recurrence`+`aggregate()` 비-decision 일반 제외·`record()` recurrence 인자·notifier 재발 표기·is_clear 단락 유지. **E2**: 온라인 그리디 군집 확정(`ClusterState`/`match_cluster` API·첫 도착=대표·db_id 스코프)·step7.5 확정(`correlated` 인자)·`_detect_correlated_storm` 코드 스케치. **E3**: 주입 지점 확정(계산=enricher gather 편승→`AlarmState.anomaly_severity`, 반영=analyzer 후처리 상향 가드)·순수 Python Holt-Winters 확정(B-3 소멸)·`METRIC_SOURCE_BY_KIND` 매핑표·Redis baseline 캐시. **E4**: 하이브리드 재현율 정책 확정(`root_notified`=`_active_firings` 재사용, root 미통보→DASHBOARD)·`SVR.ID` 컬럼 추가·`name_of`/`ancestors` API. **E5**: `fetch_recent_changes` 인터페이스·`_NOISE_CTX_KEYS` 단일 출처화. **공통**: `test_plan60_flags_off_regression.py` 신설·B-6(상관 스코프) 신규 결정 항목·§12 산출물 상세화. |
| 2026-07-13 | **심층 코드 검토 보완(구현 결함 5건 정정)** | 전 게이트 코드 재실측 결과 반영. **E1**: 재발생 억제가 그래프 진입 전 종료되어 decision_store 감사가 현재 전무함을 확인 — 워커 직접 기록(`record_recurrence`+aggregate 제외)·`last_notified` TTL 기준 필드(슬라이딩 창 회귀 방지)·AlarmState `recurrence` 키를 명시. **E2**: domain `correlation.py`의 `noise_signal_tools`(infra) import는 계층 위반 → domain `scan_signature_severity` 직접 사용으로 정정, 온라인(스트리밍) 군집 의미론·버퍼 sweep·step7.5 사유 분리·min_cluster_size 확정. **E3**: "context_enricher가 analysis에 주입"은 그래프 순서상 불가(analysis는 analyzer 산출) → agentic_enricher 전례(상향 가드 변이)의 analyzer-후 훅으로 정정, 알람→메트릭 결정 매핑표·baseline 캐시·순수 Python HW로 B-3 완화. **E4**: 정적 엣지(장기 캐시)/동적 AVAIL_STATUS(매 이벤트 IN 조회) 분리, 홉 상한, 엔진 방언 스코프(1차 gp/yd, b0 폴백), 다홉 재현율 리스크 선택지(B-5 신설). **E5**: noise_ctx 동결 계약 3개 구성점·Redis 캐시 왕복 일관 갱신. **공통(§10)**: signals 스키마 일괄 1회 확장·신규 상태 sweep 의무·재기동 휘발성 수용. §11 테스트·§12 산출물 목록 동기 갱신. |
| 2026-07-21 | **§14 자동 조사 트리거 훅 신설 (→ Plan 64)** | "이벤트 발생 시 OS 현황 자동 조사 → 중요도 정밀 판단 → 운영자 브리핑" 요구를 게이트 밖 오케스트레이션(신규 Plan 64)으로 분리하고, Plan 60은 **트리거 계약**(게이트 PAGE → Plan 50 §8.2 push 훅 비차단 emit)만 정의. 책임 분리(게이트=1차 판정<10s, 조사=2차 정밀판정·상향 전용)·닭달걀 회피·E1/E2 dedup 연동으로 조사 대상 노이즈 상속·옵트인 `investigation_trigger_enabled`(기본 off·회귀 0). L3 조사·브리핑·조치권고·거버넌스는 Plan 64 D-101~103으로 등재. |
| 2026-07-21 | **§14.4 역방향 Option A 신설 (조사 L3 데이터의 게이트 경계 케이스 활용 · D-104)** | 사용자 제안("60번 수집정보 기준 노이즈 캔슬링에 64번 OS 수집 데이터를 활용하는 게 낫지 않나") 중 **안전한 (A)안 채택·적용**. "게이트를 L3에 전면 의존"은 배제(닭-달걀·호스트 불가용·억제 재현율 위험)하되, **경계 케이스(고중요·sev2·잠정 TICKET) 소수에만 초경량 `uptime` probe**(Plan 64 `host_diagnostic_collector.py` 최소 프로파일 재사용·2s·캐시)로 **상향 전용 tie-break**. `_priority` 1단계 승격 재사용·`notification_gate` 오버레이(순수 정책모듈 무I/O)·잠정 판정을 애매성 오라클로 재사용·probe 실패→유지(하향 없음). 옵트인 `gate_host_probe_enabled`(기본 off·회귀 0). **D-104 부여**(실측 최댓값 D-103→104, 등재 직전 재확인)·**선행 Plan 64 D-102·B-1(L3 보안결정)**·블로커 **B-8**. §15.3 L-1(COLA 하이브리드)과 동일 원리. |
| 2026-07-21 | **§15 LLM·AI 활용 방안 신설 (역할 명확화 + 문헌)** | 사용자 지적("LLM 역할 불명확") 수용. ①현행 LLM 접점 3종(심각도 상향 escalate-only·actionable 분류 보조·브리핑)을 실측표로 명확화하고 §1.3 원칙 #1의 "해석·설명자로만" 과소서술을 정정. ②LLM/AI의 노이즈 캔슬링 역할을 최신 문헌으로 근거화 — **COLA(ICSE-SEIP 2024, 하이브리드 F1 0.90+, 경계 케이스만 LLM=D-035 정합)·MDPI 4425(2024, 희소·의미 알람)·DiLink(MS 2024, 토폴로지+텍스트 F1 0.96)·Oasis(FSE 2023, LLM 요약 3년+ 실운영)·SOC 한계연구(프라이오리티화 신뢰불가=D-035 근거)·GPTrace(임베딩 dedup)**. ③문헌 기반 확장 L-1~L-5(전부 옵트인·D-035 정합, L-1·L-3 즉시 가능·L-2·L-4는 로컬 임베딩 반입 블로커 B-7)와 D-035 경계 불변식(§15.4) 확정. 신규 D-번호 없음(기존 D-035/D-048 내 정밀화). 근거표: `docs/aiops_benchmark/noise_cancellation_literature.csv`에 LLM 활용 6편 추가. §15 변경이력 헤더 §15→§16. |
| 2026-07-21 | **§14.3 보강 요건 포인터 추가 (→ Plan 64 §4.8)** | 사용자 요건("통보 횟수 감소 외에 이벤트 메시지 분석→필요정보 직접 조회→운영자에게 추가 정보 전달이 중요 요건")을 반영. 노이즈 캔슬링의 실질 가치 = **억제(Plan 60 게이트) + 생존 통보의 타깃 보강(Plan 64 §4.8)** 두 축임을 §14.3 경계에 명시. 보강 구현(메시지→프로파일 결정 매핑·타깃 수집·요지 조립)·문헌(StepFly·CORTEX)·신규 결정 D-105는 **Plan 64 §4.8/§9.9**에 신설. |
| 2026-07-21 | **§16 E6 통보 컨텍스트 보강 신설 — Plan 64 §4.8의 L1 선구현 (D-105)** | 사용자 지시("이 요건에 필요한 Plan 64 기능을 Plan 60에서 우선 구현"). **실측 결과 핵심 요건("어느 프로세스가 원인인지 통보")은 CPU/메모리에 한해 이미 end-to-end 구현·배선됨**(`enrich_processes`→`ProcessSnapshot`→`alarm_notifier._process_table_html`, Plan 47-1·`process_enrich_enabled`). 따라서 §16은 "신규 구축"이 아니라 **범위 확장**으로 정의: ①`classify_alarm_kind` cpu\|memory→disk/network/process/log 확장 + kind별 L1 프로파일 ②자유텍스트 메시지형 LLM 분류(서술 전용·D-035) ③부하 추이(E3 연계) ④`_process_table_html` kind별 일반화. 전부 **L1 읽기전용·블로커 없음**(L3·D-102·B-1 불요) → **Wave A 우선**. post-gate·라우팅 불변·옵트인 `message_enrichment_enabled`(회귀 0). D-105 단일 결정의 **L1 단계=Plan 60 §16 / L3 단계=Plan 64 §4.8** 분리. §9 Wave·우선순위 결론에 E6 반영. |
| 2026-07-21 | **Wave A(E1·E4·E6) 구현 완료 + D-번호 재부여** | 사용자 §8 게이트 확정(B-1=AVAIL_DEPEND 단독·B-2=폴스타 이력·B-5=하이브리드·B-6=존 경계·B-3 해소·B-7/B-8 범위 밖) 후 Wave A 3항목 구현. **D-번호 재부여**: 초안 D-077~081은 02_decision.md 결번 확정·D-101~105는 Plan 64/§14.4/§16 예약충돌 → 예약블록 위 **D-106(E1)·D-107(E4)·D-108(E6)** 재부여(§8 "등재 직전 번호 재확인" 규칙, 팀장 확정). **E1**: `_gate_dedup` dict화(last_notified 고정창 판정·last_seen sweep)·`(is_dup,meta)` tuple·`record_recurrence` 감사·aggregate 비-decision 일반 제외·대표 알람 재발 표기. **E4**: 신규 domain `topology.py`(stdlib)+infra `topology_loader.py`, 게이트 step6.4 다홉 하이브리드(cascaded+root_notified→SUPPRESS/미통보→DASHBOARD·1홉 폴백), root_notified는 enricher가 worker `_active_firings`로 신선 산출, 정책모듈 topology 미import, signals Wave A 일괄확장(cascaded·root_resource·correlated[E2 휴면]), 1차 gp/yd·b0 폴백. 실측 정정: 엣지 로더 root NAME 누락→조상 IN 조회 NAME 보강. **E6**: classify_alarm_kind 확장+`enrichment_profile.py`+notifier kind별 블록 일반화·cpu/memory 전용 가드(회귀 방지)·process/log 요지만 graceful·LLM 분류는 결정적 대체(후속). **회귀 0 검증**: 신규 `test_plan60_flags_off_regression.py`(7)+`test_topology`·`test_multi_hop_cascade`·`test_recurrence_dedup`·`test_enrichment_profile`·`test_message_enrichment` 등, `tests/test_alarm/` **528 passed**, `arch_check --ci` exit 0. 사전 존재 실패 4건(analyzer SimpleNamespace fixture)은 클린 HEAD 격리 사본에서 동일 확인(Wave A 무관). |
