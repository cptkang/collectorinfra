# 52. 알람 노이즈 캔슬링 — 중요도 기반 발송 판단 (Alarm Noise Cancellation & Notification Gating)

> 작성일: 2026-06-26
> **상위 로드맵**: **Plan 62(AIOps 전체 역량 마스터 로드맵) — Phase P1(노이즈·상관 완성)**. 대응 벤치마크 역량 C2(이벤트 상관·노이즈 제거, 구현 완료). 고도화는 Plan 60.
> **관련 Plan**: 44(alarm_query), 46(알람 소켓 수신), 47(알람 이력 패턴·`is_routine`), 47-1(프로세스 보강),
> 50/51(장애진단·원인분석 — 사후 분석)
> **관련 결정**: D-029~D-036(알람 계열), 특히 **D-035**(패턴은 부가정보, 심각도 3은 is_routine 무관) /
> **D-048**로 등재 완료(2026-06-29 — 원안 D-040→D-041→최종 D-048; 변경이력·Plan 50 선점 회피, §13)
> **상태**: E1~E5 구현 완료 (E5: 2026-07-02, D-048.7 — 사용자 확정 §13.1#8 3경로 전체·메시지형 한정).
> E1~E4(D-048.1~.11). D-049(ack/incident 계측)·D-048.10(SSE 브리지)은 E3 후속 완료.
> — E5 트랙 B는 **Gemini API로 라이브 검증 가능**(2026-07-02 추가: `provider=gemini`+키 시 vLLM 없이 트랙 B, `test_agentic_enricher_gemini_live.py`). vLLM 실서빙 라이브는 여전히 후속.

---

## 1. 개요 및 목표

### 1.1 배경

현재 알람 파이프라인은 폴스타 알람을 수신하면 **dedup(TTL 300초) + min_severity(기본 2) 필터**만 통과시키면
무조건 LLM 분석 후 **모든 설정 채널(workB 등)로 발송**한다. "이 알람을 운영자에게 보내야 하는가?"라는
**발송 판단 게이트가 없다**. Plan 47의 `is_routine`/패턴(첫발생·주기적·급증·산발적)은 계산되지만
**알림 본문 배지(메타데이터)로만 쓰이고 발송 여부를 결정하지 않는다**(D-035).

결과적으로 일상적·반복적·저중요도·유지보수중·연쇄(cascade)·플래핑·자가복구 알람이 모두 운영자에게
전달되어 **알람 피로(alert fatigue)** 를 유발한다. 본 계획은 **발생 이벤트의 중요도·심각도와 다중 신호를
종합하여 운영자에게 보낼 알람인지 결정적으로 판단하고, 노이즈를 억제**하는 기능을 추가한다.

### 1.2 목표

모든 알람을 **4개 티어**로 라우팅한다(업계 표준 — Google SRE):

| 티어 | 의미 | 본 프로젝트 매핑 |
|------|------|----------------|
| **PAGE** | 즉시 운영자 통보 | workB/webhook 발송 (기존 경로) |
| **TICKET** | 중요하나 대기 가능 — 기록/대기열 | 저우선 채널 또는 일배치 요약, 감사 기록 |
| **DASHBOARD** | 정보성 — 알림 없이 표시만 | **SSE(`alarm_bus`)로 UI에만** push (기존 자산 재사용) |
| **SUPPRESS** | 양성/중복/유지보수/플래핑 — 미통보 | 발송 안 함, **감사 기록은 유지**(삭제 아님) |

판단은 **결정적 규칙 파이프라인(Python)** 이 수행하고, LLM(`is_routine`/패턴)은 **하나의 보조 입력**으로만
쓴다(환각 차단 — D-035·Plan 47 §3.3 계승). **심각도 3(심각)은 어떤 경우에도 억제하지 않는다**(D-035 계승).

### 1.3 성공 기준

1. 알람마다 (중요도·심각도·패턴·유지보수·의존성·폴스타 알림정책·플래핑·자가복구) 신호를 종합해
   **PAGE/TICKET/DASHBOARD/SUPPRESS** 중 하나로 라우팅하고, **결정 근거(reason)를 감사 로그에 남긴다**.
2. 수치·규칙은 **결정적 Python**으로 판정(LLM 환각 0). LLM `is_routine`은 보조 입력.
3. **심각도 3·미식별 중요도·신호 수집 실패 시 보수적으로 PAGE**(true positive 억제 방지 — 재현율 우선).
4. 기능 비활성 시 기존 발송 경로 **무변경**(회귀 없음, 옵트인 플래그).
5. **억제기 자체를 메타모니터링** — 억제 비율 이상·이벤트 미수신 시 경보(억제기가 장애를 묵살 방지).
6. 억제 신호는 **폴스타 읽기전용 DB로 수집**(신규 인프라 최소). 부하는 캐시·타임아웃으로 보호(Plan 47 계승).

### 1.4 설계 원칙 (계승)

- **결정적 규칙 = 판단 / LLM = 보조 입력** (D-035, Plan 47 §3.3).
- **재현율 우선 / 비용 비대칭** — 놓친 장애 ≫ 성가신 알람. 불확실하면 PAGE. **심각도 3 절대 억제 금지**.
- **억제 ≠ 삭제** — 억제·강등된 알람도 감사·대시보드에 기록(추적성).
- **억제기 메타모니터링** — "모니터를 모니터링"(Google SRE).
- **읽기전용·옵트인·기존 경로 보존** — 신호 수집은 SELECT만, 전 기능 기본 off.

### 1.5 추가 수집 데이터 및 분석 방법 (명세)

> "발송 판단을 위해 무엇을 추가로 수집하고, 어떻게 분석/판정하는가"의 한눈 요약. 상세는 §5(신호)·§6(결정 파이프라인).
> 대부분 **폴스타 읽기전용 DB(L1)** 에서 즉시 수집 — 신규 인프라 거의 불필요.

| # | 추가 수집 신호 | 소스 · 수집 방법 | 분석/판정 방법 (결정적 + LLM 자문) | 결정 기여 |
|---|---------------|-----------------|------------------------|----------|
| 1 | **자산 중요도** | `cmm_resource.IMPORTANCE_ID` 고정 SQL (L1) | 값코드→낮음/보통/높음 매핑 | 우선순위 매트릭스 축 (§3.2) |
| 2 | **유지보수 상태** | `cmm_resource.IS_MAINTENANCE` (L1) | 불리언/점검 윈도우 판정 | SUPPRESS (§3.5) |
| 3 | **의존성 + 부모 상태** | `AVAIL_DEPEND_RESOURCE_ID` + 부모 `AVAIL_STATUS` (L1) | 부모 비정상 여부 판정 | 연쇄 억제 (§3.6) |
| 4 | **폴스타 알림정책** | `cmm_alarm_def_noti*` (L1, 허용테이블 등재됨) | 통보대상/수신자 지정 여부 | 앵커 가중(SUPPRESS↔PAGE, §6.2) |
| 5 | **패턴·재발·주기·급증** | Plan 47 `AlarmHistoryStats` (보유) | `pre_classification`·`interval_cv` | `is_routine` 보조 입력 (§3.7) |
| 6 | **플래핑 상태** | 알람 상태전이 이력/인프로세스 추적 | Nagios 가중 %-state-change·히스테리시스 | 억제 (§3.7) |
| 7 | **자가복구(해소 매칭)** | 해소 이벤트(severity 0) 상관 | 시간창(`self_heal_window`) 상관 | 억제(저심각도 한정, §3.7) |
| 8 | **스톰(동일서버 다발)** | 사건창 내 알람 수 + 핑거프린트 | 그룹핑/dedup | 1건화·1회 PAGE (§3.8) |
| 9 | **AI 메시지 심각도(상향)** | `alarm_analyzer`(LLM) + Plan51 부록A.1 시그니처 | 결정적 시그니처 우선 + LLM 자문, **상향 전용** | `max(폴스타,AI)` 우선순위 (§3.11) |
| 10 | (Phase C) **운영자 피드백** | "노이즈/유효" 피드백 저장소 | **LLM few-shot 예시**(ML 미사용) | LLM 보조 판단 (§3.9) |

**분석/판정 절차**: ①~⑧을 `notification_context_enricher`가 동시 수집(부분실패→보수적 PAGE) →
**`notification_policy.decide_notification`(결정적 12단계 파이프라인, §6)** 이 신호를 순서대로 평가하여
**PAGE/TICKET/DASHBOARD/SUPPRESS** 1개 티어 + 근거(reason)를 산출 → `alarm_notifier`가 티어대로 라우팅(§7).
LLM `is_routine`은 보조 입력일 뿐 판단은 결정적 규칙. **심각도 3은 모든 억제 단계를 건너뛰고 항상 PAGE**.

---

## 2. 현재 자산 분석

### 2.1 기존 노이즈 관련 자산 (재사용)

| 자산 | 위치 | 현재 동작 | 한계 |
|------|------|----------|------|
| dedup | `alarm_worker._is_duplicate` (TTL 300s) | `alarm_id` 키로 중복 차단 | **`alarm_id`는 발생건마다 새로 발급** → 재발생(recurrence) dedup이 아니라 동일메시지 재처리 방지용. 핑거프린트 필요(§6.1) |
| min_severity | `alarm_worker` (기본 2) | severity<2 드롭 | 단일 임계뿐, 중요도·맥락 미반영 |
| 패턴/`is_routine` | `alarm_pattern.py`·`alarm.py`(`pre_classification`, `interval_cv`, `is_routine`) | 첫발생/주기적/급증/산발적 + LLM is_routine | **메타데이터(배지)일 뿐 발송 게이트 아님**(D-035) |
| 심각도별 수신자 | `WorkbConfig.get_user_ids(severity)` | severity 2/3 수신자 분기 | 발송 여부가 아니라 수신자만 분기 |
| SSE 푸시 | `notification_bus`(`alarm_bus`) | UI 실시간 push | **DASHBOARD 티어로 그대로 활용 가능** |
| 채널 선택 | `alarm_notifier`(`get_notification_channels`) | 항상 전 채널 발송 | **조건부 게이트 없음** |

→ **결론**: 패턴 판정·SSE UI·심각도 라우팅 자산은 있으나, 이를 묶어 "보낼지 말지"를 결정하는 **게이트가 없다**.
본 계획의 신규는 (a) 억제 신호 수집, (b) **결정적 결정 파이프라인**, (c) 4-티어 라우팅 통합이다.

### 2.2 폴스타 데이터 — 억제 신호가 이미 존재 (읽기전용 즉시 활용)

`schema/polestar-schema.md` `cmm_resource` 실측 — 핵심 신호가 모두 컬럼으로 존재한다:

| 컬럼 | 의미 | 노이즈 판단 용도 |
|------|------|----------------|
| `IMPORTANCE_ID` | 중요도 ID (NOT NULL, 기본 1) | **중요도 축** — 우선순위 매트릭스(§3.2) |
| `IS_MAINTENANCE` | 유지보수 모드 여부 | **유지보수 억제**(§3.5). 이미 기존 SQL에서 사용(`sqls/act/2026-03-26.sql`) |
| `AVAIL_DEPEND_RESOURCE_ID`(+`_2`) | 가용성 의존 리소스 | **의존성/연쇄 억제**(§3.6) — 부모 다운 시 자식 억제 |
| `IS_INHERIT_AVAIL_DEPEND` | 의존 상속 여부 | 의존성 체인 판정 보조 |
| `PRIORITY` | 우선순위 | 우선순위 보조 |
| `AVAIL_STATUS` | 가용 상태(0=정상,≠0=비정상) | 부모/연관 리소스 현재 상태 |

추가로 **폴스타 자체 알림정책 테이블이 이미 허용테이블에 등재**되어 있다
(`config/db_profiles/polestar*.yaml`):

- `cmm_alarm_def_noti`, `cmm_alarm_def_noti_user`, `cmm_alarm_def_noti_group`, `cmm_alarm_def_noti_role`,
  `cmm_alarm_def_noti_rmtype` — **"이 알람 정의가 통보 대상인가, 누구/어느 그룹에게, 어떤 방식으로"**.

→ **폴스타가 이미 "통보 대상으로 보는지"의 권위 있는 앵커**다. 이를 노이즈 판단의 1차 기준으로 삼으면
에이전트의 자체 규칙과 폴스타 운영정책이 정합한다(§6.2).

> ✅ 정정(실측 2026-06-29): `noise_context`는 **고정 SQL 직접 조회**(§8.3, Plan 47 history repo 패턴)라 LLM 쿼리
> 생성의 `allowed_columns` 검사를 **우회**한다. `cmm_resource`는 이미 `allowed_tables`에, `cmm_alarm_def_noti*` 5개는
> **모든 프로필 `alarm_allowed_tables`에 등재됨**(실측 확인). → **프로필 컬럼 보강은 착수 blocker가 아니다**
> (query_guide 문서화는 선택). **실제 유일한 선행 작업은 `IMPORTANCE_ID` 값 코드(1=낮음? 높음?) 인스턴스별 매핑
> 확정**(§13.1).

---

## 3. 노이즈 감소 기법 (조사 종합)

업계 정립 기법을 본 게이트의 규칙으로 인코딩한다. (출처 §15)

### 3.1 발송 4-티어 + 액션가능성 게이트 (마스터 규칙 — Google SRE)

- **증상 기준 통보(symptom over cause)**: 사용자 영향 증상에만 PAGE, 내부 원인은 대개 TICKET/DASHBOARD.
- **5-질문 테스트**(PAGE 조건): (1) 미탐지·긴급·조치가능·사용자 영향 (2) 무시 가능한 양성 아님
  (3) 사용자 영향 확실 (4) 즉시 조치 필요(아침까지 대기 불가 아님) (5) 중복 페이지 아님. **하나라도
  실패 → TICKET/DASHBOARD/SUPPRESS.** → 결정적 근사 규칙으로 인코딩(§6).
- 목표: **알람:사건 ≈ 1:1**, 교대(12h)당 실질 페이지 ≤ 2건(과도 시 디노이즈).

#### 3.1.1 SRE 원칙의 활용 방안 (본 계획 적용)

본 계획은 Google SRE의 알림 철학을 **정성적 가이드로 인용하는 데 그치지 않고, §6 결정 파이프라인의
결정적 규칙과 §9 운영 지표로 구체화**해 활용한다. 각 원칙을 본 계획에서 어떻게 쓰는지 정리한다.

| SRE 원칙 | 본 계획에서의 활용 방식 (어떻게) | 적용 위치 |
|----------|--------------------------------|----------|
| **4-티어 알림 분류** | 모든 알람을 PAGE/TICKET/DASHBOARD/SUPPRESS 1개 티어로 결정적 라우팅. PAGE=workB/webhook, TICKET=저우선·일배치, DASHBOARD=기존 SSE, SUPPRESS=감사만 | §1.2, §6 step 12, §7 |
| **증상 기준 통보(symptom over cause)** | 사용자 영향(심각도) 큰 알람 우선 PAGE, 내부 원인성·연쇄 알람은 강등/억제 — **부모(근본원인)에만 PAGE하고 자식(증상)은 억제** | §3.2, §3.6, §6 step 4 |
| **5-질문 PAGE 테스트** | §6 step 9 액션가능성 게이트에 **결정적 근사 규칙으로 인코딩** — 5질문 중 하나라도 실패 시 TICKET/DASHBOARD/SUPPRESS로 강등 | §3.1, §6 step 9 |
| **알람:사건 ≈ 1:1 / 교대당 페이지 ≤ 2** | 운영 지표로 **측정**하고, 초과 예상 시 예산 가드레일·레이트리밋으로 묶음/강등(드롭 아님) | §3.8, §3.10, §9 |
| **억제기 메타모니터링("모니터를 모니터링")** | 억제율 임계 초과·이벤트 무수신 시 **별도 메타경보** — 억제기가 진짜 장애를 묵살하는 것 방지 | §1.4, §9 |
| **재현율 우선·비용 비대칭(놓친 장애 ≫ 성가신 알람)** | 불확실·수집실패·미식별 시 **보수적 PAGE**, **심각도 3은 모든 억제 단계 건너뛰고 항상 PAGE** | §1.4, §4.3, §6.3 |
| (확장 여지) **SLO 번레이트 알림** | 현재 **미채택**. 향후 SLO 측정 도입 시 PAGE 조건 보강 입력으로 활용 가능 | §15 출처 |

**운용 방식**: SRE의 *철학*(증상 기준·페이지 가치 테스트·재현율 우선)은 §6 결정 파이프라인의 **결정적 규칙**으로,
SRE의 *부하 목표*(알람:사건 1:1·교대당 페이지 상한·메타모니터링)는 §9 **운영 지표와 자동 경보**로 구체화한다.
즉 SRE를 추상 가이드가 아니라 **판정 로직 + 측정 지표**로 활용하며, LLM(`is_routine`·AI 심각도)은 보조 입력일 뿐
판단은 결정적 규칙이다(§4.1). Alertmanager·Nagios·PagerDuty 등도 같은 방식으로 알고리즘·임계값의 출처로만
차용해 `notification_policy.py`에 직접 인코딩한다(§6, §15).

> 폐쇄망 적합성: 위 활용은 전부 **개념의 자체 Python 인코딩**이라 외부 서비스·API 의존이 없다 → 외부 egress 없이
> 폐쇄망에서 동작(신호=폴스타 읽기전용 DB §5, 추론=내부 FabriX/vLLM `src/config.py`). 검토 완료, 구조적 차단 요인 없음.

### 3.2 심각도 × 중요도 우선순위 매트릭스 (라우팅 핵심)

- **심각도(severity)=영향**, **중요도(importance)=자산 가치/긴급도**. 둘을 결합해 우선순위→티어.
- 본 프로젝트 매핑(예시, 표본으로 보정):

| 심각도＼중요도 | 높음 | 보통 | 낮음 |
|---|---|---|---|
| 3 심각 | PAGE | PAGE | PAGE(억제 금지) |
| 2 경고 | PAGE | TICKET | DASHBOARD |
| 1 주의 | TICKET | DASHBOARD | SUPPRESS |
| 0 해소 | 자가복구 상관(§3.7) | 〃 | 〃 |

> 핵심: **저중요도 자산의 저심각도·반복 알람**이 주된 억제 대상. **심각도 3은 매트릭스 무관 PAGE**(D-035).
>
> ⚠️ **도달 전제(실측 결함)**: severity 1(주의) 행을 라우팅하려면 그 알람이 게이트에 닿아야 한다. 현재 워커
> `min_severity`(기본 2)가 severity<2를 **게이트 이전에 드롭**하므로(`alarm_worker.py:201`), 게이트 활성 시
> `min_severity`를 낮추지 않으면 **매트릭스 하단 두 행(주의)이 영영 동작하지 않는다** → **§4.8**에서 역할 분리.

### 3.3 중복제거(dedup) + 그룹핑

- **핑거프린트 dedup**: `db_id+server+alarm_name+resource`로 안정 식별 → 재발생은 카운트만 증가, 재통보 억제
  (현재 `alarm_id` 키 결함 교정, §6.1). 참고: Alertmanager `group_wait`(30s)/`group_interval`(5m)/
  `repeat_interval`(4h).
- **그룹핑/스톰 압축**: 같은 키 집합의 알람 다발을 1건으로 묶어 한 번만 PAGE(§3.8).

### 3.4 억제(inhibition) / 침묵(silence)

- **억제**: 동일 리소스/서버에 **상위 심각도 알람이 발생 중이면 하위 심각도 알람 통보를 음소거**
  (Alertmanager inhibition: source_matchers/target_matchers/equal). 자기억제 금지.
- **침묵(수동)**: 운영자가 특정 매처+기간으로 임시 음소거(점검·인지된 진행중 사건). UI/관리 API로 관리.

### 3.5 유지보수 윈도우 억제

- 리소스가 **유지보수 모드(`IS_MAINTENANCE=1`)** 또는 점검 윈도우 내면 → **SUPPRESS(기록 유지)**.
- 주의(업계 공통): **기존 진행중 통보는 자동 해소하지 말 것** — 신규 발송만 억제. (Nagios/PagerDuty 동작 계승)

### 3.6 의존성/토폴로지 억제 (연쇄 노이즈)

- 알람 리소스의 **부모/상위(`AVAIL_DEPEND_RESOURCE_ID`)가 이미 비정상(`AVAIL_STATUS≠0`)/알람중**이면,
  자식 알람은 **연쇄 노이즈로 억제하고 부모(근본원인)에만 PAGE**. (Nagios reachability: DOWN vs UNREACHABLE)
- 정확한 토폴로지 필요 — stale 시 오억제 위험 → 신뢰도 낮으면 보수적으로 PAGE(R-3).

### 3.7 플래핑 탐지 + 상태 안정화 + 자가복구 상관

- **플래핑(Nagios 알고리즘)**: 최근 21개 결과의 ≤20개 전이를 **최신 가중(1.0→1.5)** 으로 % 산출,
  high 임계(예 20%) 이상이면 플래핑 시작 → 통보 억제, low(예 5%) 이하면 종료(히스테리시스).
- **상태 안정화(debounce-in)**: 조건이 N분 지속해야 통보(Prometheus `for`). 지속 전 해소면 미통보.
- **자가복구 상관(self-heal)**: 발생 알람이 짧은 창(예 5분) 내 **해소(severity 0, `is_clear`)** 가 오면
  → 자동 종료/억제. `keep_firing_for`로 단발 누락에 의한 오해소 방지.
  - **"저심각도 한정" 정량 정의**: **원발생 severity ≤ `suppress_max_severity`(기본 2)** 인 경우만 억제.
    **심각도 3은 제외**(해소가 와도 발생 PAGE는 보존). = §3.2·§6.3 일관.
  - **`is_clear` 정의(코드 정합)**: 해소는 **`severity == 0` 단독 기준**(`alarm_status` ACK 상태와 무관) — 이미
    코드에 반영됨(`alarm_worker.py:173`, D-035). 게이트는 이 정의를 그대로 사용(추가 작업 없음).

### 3.8 레이트리밋 / 스톰 제어 / 예산

- **버스트 압축**: 같은 키의 다발을 1 사건으로. **레이트리밋**: 단위시간 통보 상한(스톰 시 단일 요약).
- **예산 가드레일**: 교대당 페이지 상한 목표치 초과가 예상되면 디노이즈(드롭 아님, 묶음/강등).

### 3.9 LLM 기반 데이터 활용 (후속 단계) — ML 모델 미사용

운영자 피드백을 **ML 모델로 학습하지 않고 LLM 인컨텍스트(in-context)로 활용**한다 (본 프로젝트는 폐쇄망·
FabriX/vLLM 기반, 별도 ML 학습·특징저장 인프라가 없음 → LLM-only가 정합).

- **피드백 grounded LLM 판단**: 운영자가 남긴 "노이즈/유효" 라벨이 붙은 **유사 과거 알람 few-shot 예시**를
  (alarm_name·resource·패턴 키로) 검색하여 `alarm_analyzer` 프롬프트에 주입 → LLM이 현재 알람의 액션가능성을
  **인컨텍스트로 판단**(학습·재훈련 불필요). = Plan 51 §3.8(LLM-RCA: few-shot/procedural ICL) 계승.
- **결정에는 보조로만**: LLM 판단은 §6 결정 파이프라인의 **보조 입력**(매트릭스 미세조정·경계사례)이며,
  하드 규칙·심각도3 PAGE·재현율 우선은 불변(§4.1, §6.3).
- 참고(ML 배경, **미채택**): AlertRank(XGBoost 랭킹)·TEQ(감독학습 피드백)는 **특징 중요도의 근거**로만 인용하고,
  구현은 **ML 학습이 아니라 LLM-ICL**로 대체한다(§15).

### 3.10 운영 지표 / 주의

- 지표: **액션가능 비율(목표 30~50%)**, 사건전환율(≥20%), 교대당 페이지(≤2), MTTA/MTTR, FP율.
  불균형이라 **Precision/Recall/PR-AUC** 사용(정확도·ROC 부적합).
- 주의: **true positive 억제 위험**(재현율 우선·비용비대칭), **억제기 메타모니터링**(억제율 급변·무수신
  경보), **설명가능성**(결정 근거 기록·해석가능 규칙 우선).

### 3.11 AI(LLM) 메시지 기반 심각도 보강 (상향 전용)

폴스타 구조화 심각도(`AlarmEvent.severity`)는 임계/규칙 기반이라 **메시지 본문의 실제 위험도**를 반영하지
못할 수 있다(특히 LogMonitor·보안·앱 로그 알람은 고정 심각도로 올라옴). 이를 **LLM이 메시지를 해석해
보강**하되, 안전을 위해 **상향(escalate)만, 하향 억제는 금지**하는 비대칭·단조(monotonic) 모델로 한정한다.

- **에스컬레이션 전용(monotonic)**: `실효심각도 = max(폴스타 severity, AI 상향 등급)`. AI가 "사소함"이라
  봐도 폴스타 심각도 미만으로 내려 SUPPRESS하지 않는다(true positive 보호 — §12 R-10).
- **결정적 시그니처 우선 + LLM은 모호분만**: 1차로 **Plan 51 부록 A.1 OS 시그니처를 결정적으로 스캔**
  (`Out of memory`·`soft lockup`·`I/O error`·`Remounting filesystem read-only`·`segfault`·
  `Cannot assign requested address` 등 → 즉시 상향). 시그니처 없는 모호한 메시지에 한해 LLM이 의미론적
  긴급도를 **자문(advisory)** 으로 부여. = 결정적 우선·LLM 보조(§4.1, Plan 51 §3.3).
- **적용 범위 한정**: 메시지가 신호를 담는 알람(LogMonitor/보안/앱 로그, `conditionLogText` 보유)에 집중.
  임계형(CPU/메모리 수치) 알람은 폴스타 숫자 심각도가 이미 유효 → 재판단 불요.
- **SSOT 보존·자문**: 폴스타 심각도가 베이스라인(단일 진실 원천). LLM은 그 위에 **상향 신호만** 얹는 자문.
  심각도 3은 AI와 무관하게 항상 PAGE.
- **통합·비용**: `alarm_analyzer`(이미 LLM 1회 실행)가 `ai_message_severity`(상향 전용, 근거 시그니처 포함)를
  출력 → `notification_gate` 8단계 우선순위 산출에서 `max()`로 결합(§6 step 8). 순수 규칙 단계는 LLM 이전에
  단락(short-circuit)되어 비용 절감(경계 사례만 LLM 반영).

### 3.12 deepagents 기반 Advisory Enricher (agentic 보조 분석 — 옵션)

§3.11이 "메시지 1회 해석"이라면, 더 복잡한 알람(메시지가 신호를 담되 **무엇을 더 조회해야 하는지가 알람마다
다른** LogMonitor/보안/앱)은 고정 신호 세트로 부족하다. 이를 위해 deepagents(agentic LLM)를 **보조 분석기
(Advisory Enricher)** 로 옵션 도입한다 — **판단자가 아니라 신호 수집·해석 보조**다.

- **역할 한정(판단자 아님)**: deepagents는 `condition_log`를 해석해 **조회할 정보를 동적으로 분리**하고
  **읽기전용 도구로 수집**하여 `signals` 보조 입력(`ai_severity` 상향·`app_impact`/`db_impact` 추정·근거)을 반환할
  뿐, **최종 티어는 결정적 `decide_notification`(§6)이 산출**한다. (환각·비결정 차단 — §4.1·R-2 계승)
- **호출 조건 한정(비용 비대칭)**: 결정적 단계(§6 0~7)가 종착 티어를 확정하지 못한 **경계 사례** + **메시지형
  알람**(LogMonitor/보안/앱, `condition_log` 보유)에만. 임계형(CPU/메모리 수치)은 숫자 심각도가 이미 유효 →
  **호출 안 함**(§3.11 단락 계승).
- **승격 비대칭**: 보조 결과는 **승격 전용**(§3.11·§6.4) — 영향 발견 시 PAGE 가중, "조용함"으로 강등 금지.
  **심각도 3 절대 PAGE 불변**.
- **3단계 폴백 체인(가용성 우선 — 자동 강등)**: 동적 도구 호출은 tool-calling이 필수인데 **현 운영 LLM FabriX는
  tool-calling 불가**(D-037·R-08)이므로, 환경에 따라 아래로 자동 강등한다.
  1. **vLLM 가용 → deepagents(트랙 B)**: vLLM 오케스트레이터(Plan 48/49 — Qwen3.5-9B `bind_tools`)가 도구를
     **동적 호출(ReAct)** 하여 필요 신호를 스스로 수집. 최대 표현력.
  2. **vLLM 없음 + FabriX 가용 → semantic-routing 방식(트랙 A 폴백)**: tool-calling 없이 LLM이 **1회 프롬프트+JSON
     분류**로 "조회할 신호 목록(`needed_signals`)"만 산출 → **결정적 컬렉터가 그 목록을 고정 SQL로 수집**(§8.3
     재사용). FabriX 호환(D-004 `semantic_router`·Plan 48 트랙 A 패턴), tool-calling 불요·비용↓. *"무엇을 볼지"는
     LLM 분류(1회), 수집은 결정적*.
  3. **LLM 없음/옵트아웃 → 결정적 게이트만**: enricher 비활성(회귀 0).
  세 경로 모두 **결과는 `signals` 보조 입력(승격 전용)**, 판단은 결정적. **FabriX는 어느 경로에서도 tool-calling을
  강요받지 않는다**(도구 *내부*에서만 호출). vLLM 가용성은 health check로 자동 판정. 전 기능 옵트인.
- **부하 보호**: 경계 사례 한정 + 타임아웃 + 캐시 + 도구 **읽기전용 화이트리스트** + 도구 호출 상한(ReAct 루프 제한).
- **Plan 55 연계**: deepagents의 "동적 정보 수집"은 §8.6·Plan 55 멀티소스(APM/DPM)를 도구로 조회할 때 특히
  강력 — `app_impact`/`db_impact`를 agentic으로 채운다.

---

## 4. 핵심 설계 결정

§13에서 D-048 하위로 등재. 기존 결정 충돌 검토: **충돌 없음**(D-035의 "패턴은 부가정보·심각도3 보존"을
**확장·준수**, 읽기전용·옵트인).

- **4.1 결정적 게이트 + LLM 보조** — 규칙은 Python 순수함수, `is_routine`은 입력 하나. 결정 근거 기록.
- **4.2 4-티어 라우팅** — PAGE/TICKET/DASHBOARD/SUPPRESS. DASHBOARD는 기존 SSE 재사용.
- **4.3 재현율 우선·심각도3 절대 PAGE·억제≠삭제** — 보수적 기본값.
- **4.4 신호는 폴스타 읽기전용 DB 우선** — IMPORTANCE/MAINTENANCE/DEPEND/noti정책. 신규 인프라 최소.
- **4.5 폴스타 알림정책(`cmm_alarm_def_noti*`)을 1차 앵커** — 에이전트 규칙과 폴스타 운영정책 정합.
- **4.6 전 기능 옵트인 + 메타모니터링** — 회귀 없음, 억제기 자체 감시.
- **4.7 AI 분석은 LLM(인컨텍스트)으로, ML 모델 미사용** — 심각도 보강(§3.11)·액션가능성(§3.9)은 LLM 자문 +
  결정적 시그니처/규칙으로 구현. 별도 ML 학습·특징저장 인프라를 만들지 않는다(폐쇄망·LLM-only 정합).
- **4.8 `min_severity` 필터와 게이트의 역할 분리** — (실측 결함 교정) 현재 워커는 `severity < min_severity`(기본 2)를
  **게이트 이전에 드롭**한다(`alarm_worker.py:201`). 이대로면 §3.2 매트릭스의 **severity 1(주의) 행이 영영 게이트에
  닿지 못해** TICKET/DASHBOARD/SUPPRESS 라우팅이 무력화된다. → **역할 재정의**: ⓐ 워커 `min_severity`는 "게이트가 볼
  최저 심각도"의 1차 컷일 뿐이고 **강등·억제 판단은 전부 게이트가 수행**. ⓑ 게이트 활성(`enable_noise_gate=True`) 시
  권장 `min_severity=1`(주의까지 게이트로). ⓒ severity 0(해소)은 자가복구 상관(§3.7)을 위해 게이트에 전달(워커에서
  드롭 금지). ⓓ 심각도 3은 워커·게이트 어디서도 드롭 금지. 설정 상호작용은 §8.1·§8.5.
- **4.9 agentic 분석은 보조(Advisory)로만, 판단은 결정적** — deepagents Advisory Enricher(§3.12)는 `signals` 보조
  입력(승격 전용)만 제공하고 **발송 판단은 하지 않는다**(결정적 `decide_notification`이 판단). vLLM(트랙 B, D-037)
  옵트인 전제, 미가용 시 결정적 게이트로 폴백. 비용 비대칭(경계·메시지 알람 한정). 심각도 3 PAGE 불변.

---

## 5. 수집 데이터 카탈로그 (억제 신호) + 계층

대부분 **L1(폴스타 읽기전용 DB, 즉시)** 에서 얻는다 — 신규 수집 인프라가 거의 불필요한 것이 본 기능의 강점.

| 신호 | 소스 | 계층 | 비고 |
|------|------|------|------|
| 심각도/해소 | `AlarmEvent.severity`/`is_clear` | 보유 | 즉시 |
| 패턴·재발·주기·급증 | Plan 47 `AlarmHistoryStats`(`pre_classification`,`interval_cv`,`count_24h`) | 보유(L1) | enricher 재사용 |
| `is_routine` | `alarm_analyzer`(LLM) | 보유 | 보조 입력 |
| **자산 중요도** | `cmm_resource.IMPORTANCE_ID` | **L1(신규조회)** | 프로필 보강·값코드 매핑 필요 |
| **유지보수** | `cmm_resource.IS_MAINTENANCE` | **L1(신규조회)** | 기존 SQL 사용례 있음 |
| **의존성/부모상태** | `AVAIL_DEPEND_RESOURCE_ID`+부모 `AVAIL_STATUS` | **L1(신규조회)** | 연쇄 억제 |
| **폴스타 알림정책** | `cmm_alarm_def_noti*` | **L1(허용테이블 등재됨)** | 통보대상/수신자 앵커 |
| 플래핑 상태 | 알람 이력 상태전이(또는 인프로세스 상태추적) | L1/메모리 | Nagios 알고리즘 |
| 스톰/다발 | 사건창 내 동일서버 알람 수 | L1 | 그룹핑 |
| 자가복구(해소 매칭) | 해소 이벤트(severity 0) 상관 | 보유 | self-heal |
| 업무시간/시간대 | 설정(business window) | 설정 | 시간대 강등 |
| **AI 메시지 심각도(상향)** | `alarm_analyzer`(LLM) + Plan51 부록A.1 시그니처 | 보유/L1 | 상향 전용 보강 (§3.11) |
| 운영자 피드백 | 피드백 저장소("노이즈"표시) | 신규(Phase C) | **LLM few-shot 예시**(ML 미사용, §3.9) |

> 부하 보호: 중요도/유지보수/의존성/알림정책 조회는 **고정 SQL + 단기 캐시(TTL) + 타임아웃 + graceful
> degradation**(실패 시 보수적 PAGE). Plan 47 enricher 패턴 계승.

---

## 6. 결정 파이프라인 (순서형·결정적 — 게이트 핵심)

각 알람을 아래 순서로 평가, **첫 종착 티어가 확정**. 모든 단계는 결정 근거(reason)를 기록.
`src/alarm/domain/notification_policy.py`(신규, 순수함수)로 구현 → 단위 테스트·감사 용이.

```
0. (선행) 심각도 3 → 즉시 PAGE (억제 금지, D-035)  ───────────────┐
1. 핑거프린트 + dedup (재발생 카운트만, 재통보 억제; §3.3)         │
2. 해소(is_clear)/자가복구 상관 (§3.7) → 매칭 알람 종료/억제       │
3. 유지보수(IS_MAINTENANCE/윈도우) (§3.5) → SUPPRESS(기록)        │  심각도3은
4. 의존성 억제: 부모 비정상 (§3.6) → SUPPRESS(부모에 귀속)        │  이 단계들을
5. 인히비션: 상위심각도 동일리소스 발생중 (§3.4) → 하위 음소거     │  건너뛰고
6. 플래핑/debounce/자가복구창 (§3.7) → 억제/보류                   │  PAGE 유지
7. 스톰 상관 (§3.8) → 다발을 1건으로, 1회 PAGE                    │
8. 우선순위 산출 = f(실효심각도=max(폴스타 severity, AI 상향 §3.11), 중요도, 폴스타 알림정책) (§3.2,6.2) │
9. 액션가능성 게이트(5-질문 근사) + is_routine 보조 → 티어         │
10. (Phase C) LLM 액션가능성 판단(피드백 few-shot, ML 미사용) → 강등/승격 (§3.9) │
11. 레이트리밋/예산 가드 (§3.8) → 초과 시 묶음/강등               │
12. 티어 확정: PAGE / TICKET / DASHBOARD / SUPPRESS ◄─────────────┘
13. 메타모니터링 기록 (억제율·무수신 감시; §9)
```

### 6.1 핑거프린트 dedup 교정

현재 dedup 키 `alarm_id`는 발생건마다 새로 발급 → **재발생 억제 불가**. 핑거프린트
`f(db_id, server_name|hostname, alarm_name, resource_name)`로 변경하여 재발생은 카운트·`repeat_interval`
기반 재통보 억제. (`alarm_id`는 동일 메시지 재처리 방지용으로만 유지)

> **심각도3 재통보(설계 판단, 2026-06-29 — E1 구현 관찰)**: 핑거프린트 dedup은 sev3 **재발생**도 `repeat_interval`
> (기본 4h) 내 재통보를 억제한다. **최초 발생 sev3는 항상 PAGE**되므로 §4.8 ⓓ("억제 단계에서 드롭 금지")와
> 모순이 아니다 — `repeat_interval`은 *이미 PAGE한 같은 알람의 반복 빈도 조절*(Alertmanager 계승)이지 발송 판단
> 억제가 아니다. 단 미해결 sev3에 4h는 길 수 있어 **심각도별 repeat_interval 분리 옵션**(`sev3_repeat_interval_seconds`,
> 예 1h)을 E2에서 제공한다(기본은 공통 4h).

### 6.2 폴스타 알림정책 앵커

8단계 우선순위 산출 시, **폴스타 `cmm_alarm_def_noti*`가 해당 알람 정의를 비통보로 설정**했다면 강한
SUPPRESS/DASHBOARD 신호로 반영(폴스타 운영자가 이미 노이즈로 분류한 것). 반대로 통보+상위그룹 지정이면
PAGE 가중. → 에이전트 자체 규칙과 폴스타 정책의 **정합·이중 안전**.

### 6.3 보수적 기본값 (재현율 우선)

- 신호 수집 실패/미식별 중요도/모호 → **PAGE**(억제하지 않음).
- 심각도 3 → 항상 PAGE.
- 억제·강등도 **감사·대시보드 기록**(추적성).
- **독립 해소 알람**(step 2에서 매칭 발생 알람을 못 찾은 severity 0): 통보 가치가 없으므로 기본 **SUPPRESS(감사 기록)**.
  운영자 가시화가 필요하면 **DASHBOARD**로 옵션화(`resolved_to_dashboard` 설정). 심각도 3 발생에 연결된 해소는 PAGE 이력에 귀속.

### 6.4 향후 멀티소스 보조 축 (Plan 55 — 지금 미적용)

step 8 우선순위 산출은 향후 **APM `app_impact`(사용자 영향)·DPM `db_impact`(DB 영향)** 를 **보조 축**으로 받도록
확장 가능하다(상세 설계 §8.6, Plan 55 §3·§6). 지금은 폴스타 신호만 사용하며 아래 원칙만 미리 못박아 둔다:

- **승격 비대칭(§3.11 계승)**: 영향 신호는 **상향(승격) 전용** — "사용자 영향 있음 → PAGE 가중"은 허용, "영향 없음
  (APM 조용) → 강등/억제"는 **기본 금지**(엔티티 오매핑·APM 미커버 구간의 과억제 방지 — Plan 55 R-1/R-5).
- **불변**: 단계 순서·심각도3 단락·재현율 우선·억제≠삭제는 그대로. 새 축은 **매트릭스 미세조정(보조)** 일 뿐.

### 6.5 Advisory Enricher 연결 지점 (옵션 — §3.12)

agentic 보조(§3.12)는 **결정적 단계(0~7)가 종착 티어를 확정하지 못한 경계 사례에 한해** step 8 직전에 호출되어
`signals.ai_severity`/`app_impact`/`db_impact`를 채우고, step 8(우선순위 산출)이 그 값을 `max()`(승격 전용)로 결합
한다. step 10(LLM 액션가능성, §3.9)도 같은 agentic 호출에서 함께 수행 가능. 결정적 단계가 이미 SUPPRESS/PAGE를
확정했거나 임계형 알람이면 **호출 생략**(비용·단락). **심각도 3은 step 0에서 이미 PAGE라 enricher와 무관**.

---

## 7. 4-티어 라우팅 + 알림 통합

`alarm_notifier`를 티어 인지로 수정 (기존 발송 로직 재사용):

| 티어 | 동작 | 재사용 |
|------|------|--------|
| PAGE | workB/webhook 발송 + SSE | 기존 `_send_workb`/`_send_webhook` + `alarm_bus` |
| TICKET | 저우선 채널 또는 일배치 요약 큐에 적재 + SSE + 감사 | 신규 경량 큐(또는 webhook 분리), `alarm_bus` |
| DASHBOARD | **SSE(`alarm_bus`)로 UI만** push | 기존 SSE 그대로 |
| SUPPRESS | 발송 안 함, **감사 로그만** | 기존 감사 인프라 |

→ DASHBOARD/SUPPRESS도 UI·감사에 남아 **운영자가 억제 내역을 확인·피드백**(Phase C 학습 입력).

---

## 8. 아키텍처

### 8.1 노드/그래프 (`src/alarm/`)

```
alarm_context_enricher (확장: 중요도/유지보수/의존성/알림정책 동시 조회)
        ↓
alarm_analyzer (기존+확장: is_routine/패턴 + AI 메시지 심각도 보강(상향 전용, §3.11) — 보조 입력 제공)
        ↓
notification_gate (신규: 결정적 결정 파이프라인 §6 → 티어+근거)
        ↓
alarm_notifier (수정: 티어 인지 라우팅 §7)
```

- **`alarm_context_enricher` 확장**: 기존 history/process 동시수집(`asyncio.gather`+타임아웃)에
  중요도/유지보수/의존성/알림정책 조회 추가(부분실패 허용·캐시). 또는 별도 `noise_context_enricher`로 분리.
- **`notification_gate`(신규 노드)**: `notification_policy.py` 순수함수 호출 → `NotificationDecision`.
- **`alarm_notifier` 수정**: `decision.tier`에 따라 발송/큐/SSE/억제.

**플래그 조합별 배선**(실측: `alarm_graph.py:60-80`은 `history_enabled`에 따라 enricher를 **조건부** 포함):

| `history_enabled` | `enable_noise_gate` | 그래프 배선 |
|:---:|:---:|---|
| F | F | analyzer → notifier (현행 2노드, 무변경) |
| T | F | enricher → analyzer → notifier (현행 3노드, 무변경) |
| F | **T** | **enricher(강제 포함) → analyzer → gate → notifier** |
| T | **T** | enricher → analyzer → gate → notifier |

> 핵심: **게이트 활성 시 enricher는 항상 포함**해야 한다(enricher가 noise_context 수집원이므로 끌 수 없음).
> `build_alarm_graph`의 조건부 entry-point 로직을 `history_enabled` → **`history_enabled or enable_noise_gate`** 로
> 확장한다. noise_context 수집은 enricher에 통합하거나 별도 `noise_context_enricher`로 분리해 게이트 앞에 배치.

### 8.2 도메인 (`src/alarm/domain/notification_policy.py`, 신규 — 순수함수)

```python
@dataclass
class NotificationDecision:
    tier: str               # "page" | "ticket" | "dashboard" | "suppress"
    reason: str             # 결정 근거(어느 단계에서 무슨 신호로)
    priority: int           # 산출 우선순위
    signals: dict           # 사용된 신호 스냅샷(감사·설명가능성) — 키 스키마 ↓

def decide_notification(event, history_stats, analysis, noise_ctx, config) -> NotificationDecision: ...
```

**`signals` 키 스키마(확정 — Plan 54 결정추적 드로어 §4와 인터페이스 고정)**:

```python
signals = {
    "severity": int,            # 폴스타 원심각도
    "ai_severity": int | None,  # AI 상향 등급(§3.11, 상향 전용)
    "effective_severity": int,  # max(severity, ai_severity)
    "importance": str,          # 낮음|보통|높음 (IMPORTANCE_ID 매핑)
    "maintenance": bool,        # IS_MAINTENANCE
    "parent_avail_status": int | None,  # 부모 AVAIL_STATUS(의존성 억제)
    "pattern": str,             # pre_classification(§3.7)
    "is_routine": bool | None,  # LLM 보조
    "noti_policy": str | None,  # 폴스타 알림정책 앵커(§6.2)
    "flapping": bool, "self_heal": bool, "storm": bool,
    # (Plan 55 예약) "app_impact": dict | None, "db_impact": dict | None — §8.6
}
```

### 8.3 인프라 (`src/alarm/infrastructure/polestar_noise_context.py`, 신규)

중요도/유지보수/의존성/알림정책 **고정 SQL 조회**(읽기전용, `_sql_literal` 이스케이프, 프로필별 서버명
컬럼 규칙 — Plan 47 계승). 단기 캐시.

**결정 감사 저장소 (`decision_store.py`, 신규)** — 알람 경로엔 현재 **구조화 감사가 없다**(실측:
`alarm_notifier.py:144`는 `logger`만). 따라서 `NotificationDecision`(tier·reason·priority·signals·fingerprint·ts)을
적재하는 신규 컴포넌트가 필요하다(JSONL 감사 + 집계용 Redis/경량 테이블). **Plan 54 §6의 `decision_store`와 동일
컴포넌트**이므로 함께 설계한다. → §9·§14에서 "기존 감사 재사용"이 아니라 **신규 컴포넌트**로 정정.

### 8.4 상태 (`AlarmState` 확장 — `src/alarm/orchestration/alarm_graph.py:34`)

> ⚠️ 정정(실측): `AlarmState`는 `domain/alarm.py`가 아니라 **`orchestration/alarm_graph.py`의 TypedDict**(L34-41)에
> 정의됨(domain/alarm.py에는 `AlarmEvent`/`AlarmAnalysisResult`/`AlarmHistoryStats`/`ProcessSnapshot`만 존재).

`noise_context`(중요도/유지보수/의존성/알림정책), `notification_decision`(티어/근거/우선순위/신호) 필드 추가.

### 8.5 설정 (`src/config.py` — `AlarmConfig` 확장 또는 `NoiseGateConfig` 신규)

```python
enable_noise_gate: bool = False           # 전체 게이트 옵트인
suppress_max_severity: int = 2            # 억제 허용 상한(심각도 3은 항상 PAGE)
importance_value_map_csv: str = ""        # IMPORTANCE_ID 코드→낮음/보통/높음 매핑(인스턴스별)
self_heal_window_seconds: int = 300       # 자가복구 상관 창
debounce_seconds: int = 0                 # 상태 안정화(0=미사용)
flap_high_threshold: float = 20.0         # 플래핑 시작 %(Nagios 기본)
flap_low_threshold: float = 5.0           # 플래핑 종료 %
dependency_suppression: bool = False      # 의존성 억제 on/off
business_hours_csv: str = ""              # 업무시간(시간대 강등용)
repeat_interval_seconds: int = 14400      # 재발생 재통보 간격(4h)
noise_context_timeout_seconds: float = 3.0
noise_context_cache_ttl_seconds: int = 300
meta_alert_suppress_ratio: float = 0.9    # 억제율 이 값 초과 시 메타경보
enable_ai_severity_boost: bool = False    # AI(LLM) 메시지 심각도 보강(상향 전용, §3.11)
ai_severity_escalate_only: bool = True    # True=상향만(하향 억제 금지) — 안전 고정 권장
enable_llm_actionability: bool = False    # LLM 피드백 few-shot 액션가능성 판단(ML 미사용, §3.9)
```

`.env.example`에 `ALARM_*`/`NOISE_*` 추가. 기본 비활성 → 회귀 없음.

### 8.6 향후 멀티소스 확장점 (Plan 55 연계 — 지금 구현하지 않음)

52는 폴스타(인프라) 신호만으로 티어를 결정한다. Plan 55는 여기에 **APM(사용자·앱 영향)·DPM(DB 영향)** 을 더한다.
**지금 52를 변경하지는 않되**, 아래 확장점을 *인터페이스·자리만 예약*해 두면 Plan 55 Wave M2가 **노드·티어 구조
변경 없이 "보조 입력 추가"만으로 통합**된다.

| # | 확장점 | 지금 (폴스타 전용) | Plan 55에서 추가 |
|---|--------|-------------------|------------------|
| 1 | **소스 무관 `noise_context`** | 폴스타 키만 채움, 구조는 **확장형 dict** | `app_impact`·`db_impact` 키 추가. 미정의 키=None→"신호 없음"(기존 보수적 기본값과 동일) |
| 2 | **`decide_notification` 시그니처** | **보조 신호 dict를 받는** 형태로 설계(자리 예약) | step 8 매트릭스에 영향 축 가중만 추가 |
| 3 | **수집 provider 경계** | `polestar_noise_context`가 dict를 채움 | apm/dpm 커넥터(55 어댑터)가 **같은 dict에 병합** → 게이트 코드 무변경 |
| 4 | **설정 플래그(예약)** | 추가하지 않음 | `enable_app_impact`/`enable_db_impact`(기본 False), `app_impact_escalate_only`(기본 True) |

**불변 가드(설계 못박기)**: ① 영향 신호는 **승격 비대칭**(§6.4·§3.11) — 강등 방향은 옵트인 + 엔티티 매핑 신뢰도
충족 시에만. ② 심각도3 단락·재현율 우선·억제≠삭제 불변. ③ `notification_policy`는 **수집원에 무관**하게
`noise_context` dict만 소비(폴스타/APM/DPM 구분은 수집 계층 책임 — clean architecture 경계 유지).

**대시보드 정합**: `signals` 스냅샷에 멀티소스 필드가 추가되면 Plan 54 결정추적 드로어가 자동 표시(54 §4).

> 요지: 지금은 폴스타 전용으로 구현하되 **(a) noise_context=확장형 dict, (b) step 8=보조 신호 dict 수용 시그니처,
> (c) 영향 신호=승격 비대칭** 세 가지만 지켜 두면, Plan 55는 구조 변경 없이 보조 입력만 더해 통합된다.

### 8.7 Advisory Enricher 통합 (옵션 — `src/alarm/application/nodes/agentic_enricher.py`, 신규)

- **노드 배치**: `alarm_analyzer → (agentic_enricher) → notification_gate`. enricher는 **경계·메시지 알람에만 동작**
  하고 그 외엔 통과(no-op). 게이트는 enricher가 채운 `signals`를 보조로 사용(§6.5).
- **3경로 통합(§3.12 폴백 체인을 노드 내부에서 자동 선택)**:
  - **트랙 B(vLLM 가용)**: vLLM 오케스트레이터(`ChatOpenAI`→vLLM, `bind_tools`, Plan 49)가 **읽기전용 신호 수집
    @tool**(중요도·유지보수·의존성·메시지 시그니처·Plan 55 APM/DPM)을 동적 호출. 도구는 `deepagents_tools.py`
    패턴 재사용, 도구 *내부*는 고정 SQL/FabriX.
  - **트랙 A 폴백(vLLM 미서빙, FabriX)**: `semantic_router` 패턴으로 FabriX가 **1회 프롬프트+JSON 분류** →
    `needed_signals` 목록 산출 → `polestar_noise_context`(§8.3) 결정적 컬렉터가 고정 SQL 수집. tool-calling 불요.
  - **결정적 only**: enricher 통과(no-op).
- **설정(§8.5 확장)**:
  ```python
  enable_agentic_enricher: bool = False        # 보조 분석기 옵트인
  agentic_enricher_fallback: str = "semantic_routing"  # vLLM 미서빙 시: semantic_routing(FabriX 1회 분류) | deterministic_only
  agentic_enricher_timeout_seconds: float = 8.0
  agentic_enricher_max_tool_calls: int = 5      # 트랙 B ReAct 루프 상한
  agentic_enricher_message_alarms_only: bool = True  # LogMonitor/보안/앱 한정
  ```

---

## 9. 메타모니터링 & 운영 지표

- **억제기 메타모니터링**(필수): 일정 창의 **억제 비율이 임계 초과**하거나 **이벤트 무수신** 시 별도 경보
  (억제기가 진짜 장애를 묵살 방지 — Google SRE "모니터를 모니터링").
- **운영 지표 수집**: 티어별 건수, **액션가능 비율(목표 30~50%)**, 사건전환율(≥20%), 교대당 PAGE 수,
  MTTA/MTTR. 관리 API/대시보드로 노출(튜닝 루프).
- **결정 근거 로깅**: 모든 `NotificationDecision`을 감사 기록(설명가능성·튜닝·오억제 사후분석).

### 9.1 ack/incident 라이프사이클 계측 (E3 후속 — 미구현)

E3에서 `GET /alarm/metrics`는 `decision_store`(발송 판단 JSONL) 집계로 산출 가능한 지표만
노출한다: 티어별 건수·**액션가능 비율**·**억제율**·교대당 PAGE 추정·무수신 메타경보. 반면
**MTTA·MTTR·사건전환율은 `null` + `unavailable_metrics`(사유 명시)** 로 둔다 — `decision_store`에는
**확인(ack)·사건(incident) 라이프사이클 이벤트가 없기 때문**이다(환각 수치 금지 원칙).

이 지표들을 산출하려면 아래가 **선행 결정·계측**되어야 한다(후속 작업, 본 단계 미구현):

- **MTTA(평균 확인 시간)**: PAGE 발송 시각 ↔ 운영자 **확인(ack)** 시각의 차. → **ack 이벤트**를
  어디서 포착할지(워크B 회신/웹 UI "확인" 버튼/별도 ITSM) 와 저장소(ack 타임스탬프를 fingerprint·
  alarm_id로 결정 감사에 상관) 결정 필요.
- **MTTR(평균 해소 시간)**: 발생 ↔ 해소(severity 0, `is_clear`) 시각의 차. → 자가복구 상관
  (`_firing_registry`)이 이미 발생↔해소를 매칭하므로, **해소 매칭 시 소요시간을 감사에 기록**하면
  산출 가능(상대적으로 저비용). 단 PAGE/사건 단위 집계 정의(어떤 발생을 1건으로 셀지) 확정 필요.
- **사건전환율(incident conversion)**: PAGE 중 실제 **사건(incident)으로 전환**된 비율. → 사건 식별·
  라이프사이클(생성/병합/종료)을 어디서 관리할지(ITSM 연동/내부 incident 테이블) 결정 필요.

권장 접근: (1) MTTR은 기존 자가복구 상관에 소요시간 기록만 추가하면 단기 산출 가능(저위험),
(2) MTTA·사건전환율은 ack/incident 소스(UI 버튼·ITSM)를 먼저 확정 후 계측. 모두 **억제 결정과
독립**이라 노이즈 게이트 동작에는 영향 없음(관측성 보강).

---

## 10. 단계별 구현 계획

> 모두 옵트인. **결정적 규칙 먼저, LLM 자문은 보조(ML 모델 미사용).** L1 데이터 위주라 진입장벽 낮음.

- **Phase E1 — 신호 수집 + 매트릭스 MVP**
  - `polestar_noise_context.py`(중요도/유지보수/알림정책 고정 SQL) + `decision_store.py`(결정 감사 적재) +
    `IMPORTANCE_ID` 값코드 표본 확인·매핑. (**프로필 컬럼 보강 불요** — §2.2 정정: 고정SQL이 검사 우회)
    + `min_severity` 역할 분리 적용(§4.8).
  - `notification_policy.decide_notification`: 심각도3 PAGE + 우선순위 매트릭스 + 유지보수 SUPPRESS +
    `is_routine` 보조 + 핑거프린트 dedup 교정 + 자가복구 상관.
  - `notification_gate` 노드 + `alarm_notifier` 4-티어 라우팅(DASHBOARD=기존 SSE).
  - verify: 결정적 판정 단위테스트, 심각도3 절대 PAGE, 수집실패 시 보수적 PAGE, 기존 경로 무변경(off).
- **Phase E2 — 연쇄/스톰/플래핑**
  - 의존성 억제(`AVAIL_DEPEND_RESOURCE_ID`+부모상태), 인히비션, 스톰 그룹핑, 플래핑(Nagios 알고리즘),
    debounce/`keep_firing` 상관.
  - verify: 부모 다운 시 자식 억제·부모만 PAGE, 다발 1건화, 플래핑 히스테리시스.
- **Phase E3 — AI(LLM) 심각도 보강 + 메타모니터링 + 지표**
  - **AI 메시지 심각도 보강(§3.11)**: 결정적 시그니처 스캔(Plan51 부록A.1) → `alarm_analyzer` LLM 자문
    (상향 전용) → `max()`로 우선순위 결합.
  - 억제기 메타경보, 티어/액션가능 비율 지표, 결정 근거 대시보드, 수동 침묵(silence) 관리.
  - verify: AI는 **상향만**(하향 억제 0)·심각도3 PAGE 불변, 억제율 급변·무수신 경보, 지표 산출.
- **Phase E4 — LLM 액션가능성 판단(피드백 few-shot, ML 미사용·선택)** ✅ **구현 완료 2026-07-01 (D-048.11)**
  - 운영자 "노이즈/유효" 피드백 저장 → 유사 과거 알람 few-shot으로 `alarm_analyzer` 프롬프트에 주입 →
    LLM 인컨텍스트 판단을 매트릭스 보조 입력(§3.9). **ML 모델 학습 없음.**
  - verify: 피드백 few-shot이 후속 결정에 반영, 재현율 우선 유지, 심각도3 PAGE 불변. → **완료**:
    신규 `feedback_store.py`(JSONL·graceful)·`POST /alarm/feedback`·app.js 피드백 버튼. 판단은 결정적
    step9(actionable→승격/noise→강등, `severity≤suppress_max` 가드·승격 우선), 추가 LLM 호출 없음(재파싱).
    옵트인(`enable_llm_actionability=False`면 E3 무변경). `tests/test_alarm` **359 passed**·arch exit 0.
- **Phase E5 — deepagents Advisory Enricher (agentic 보조 — 옵션, 3경로 폴백)** ✅ **구현 완료 2026-07-02 (D-048.7)**
  - `agentic_enricher.py` 노드 + 읽기전용 신호 수집 도구(`noise_signal_tools.py`, **infra 배치** — arch 역방향 회피).
    3경로 자동선택(`_select_backend`)·승격 전용·심각도3 미개입·메시지형 한정·collector 패턴. `tests/test_alarm` **390 passed**·arch 0.
    트랙 B(vLLM)는 로컬 gemini라 fake bound LLM으로 ReAct 상한만 검증 → vLLM 서빙 후 통합 검증 후속.
  - `agentic_enricher.py` 노드 + 읽기전용 신호 수집 도구. **트랙 B**(vLLM `bind_tools`, Plan 49) /
    **트랙 A 폴백**(vLLM 미서빙 시 FabriX 1회 분류→결정적 수집, §3.12) / **결정적 only**(옵트아웃) 자동 선택.
    경계·메시지 알람 한정, **승격 전용**, `signals` 보조만.
  - verify: 판단은 결정적 불변, 심각도3 PAGE 불변, **vLLM 미서빙 시 semantic-routing 폴백 동작**·그조차 없으면
    결정적 게이트·회귀 0, 임계형 알람 호출 생략, 도구 읽기전용·호출 상한, FabriX tool-calling 미강요.

---

## 11. 테스트 계획

| 테스트 | 검증 |
|--------|------|
| `test_priority_matrix` | 심각도×중요도→티어, **심각도3 항상 PAGE** |
| `test_maintenance_suppress` | IS_MAINTENANCE→SUPPRESS, 진행중 통보 미자동해소 |
| `test_dependency_suppress` | 부모 비정상→자식 억제·부모 PAGE, stale시 보수적 PAGE |
| `test_inhibition` | 상위심각도 발생중→하위 음소거, 자기억제 금지 |
| `test_flapping` | 21결과/가중%/히스테리시스(시작20%/종료5%) |
| `test_self_heal` | 발생+해소 5분내→억제(저심각도 한정), 심각도3 제외 |
| `test_fingerprint_dedup` | 재발생 카운트·repeat_interval 재통보 억제 |
| `test_polestar_noti_anchor` | 폴스타 비통보 정의→억제 가중 |
| `test_conservative_default` | 신호 수집 실패/미식별 중요도→PAGE |
| `test_notifier_tier_routing` | PAGE/TICKET/DASHBOARD(SSE)/SUPPRESS 동작, 억제도 감사 기록 |
| `test_meta_monitoring` | 억제율 초과·무수신 메타경보 |
| `test_ai_severity_escalate_only` | AI 심각도는 **상향만**(하향으로 SUPPRESS 절대 불가), 시그니처 상향, 심각도3 PAGE |
| `test_llm_actionability_fewshot` | 피드백 few-shot 주입·LLM 보조 판단, **ML 모델 미사용**, 재현율 우선 |
| `test_min_severity_gate_handoff` | (§4.8) 게이트 활성 시 severity 1(주의)이 게이트 도달→TICKET/DASHBOARD/SUPPRESS, severity 0 자가복구용 전달(드롭 안 함), **심각도3 워커·게이트 드롭 금지** |
| `test_independent_clear_suppress` | (§6.3) 매칭 발생 없는 독립 해소(severity 0)→SUPPRESS(감사), `resolved_to_dashboard`=true시 DASHBOARD |
| `test_signals_schema` | (§8.2) `NotificationDecision.signals` 확정 키 스키마 충족(severity·ai_severity·effective_severity·importance·maintenance·parent_avail_status·pattern·is_routine·noti_policy·flapping·self_heal·storm) — Plan 54 드로어 인터페이스 정합 |
| `test_graph_wiring_flags` | (§8.1) 플래그 조합 배선: history off + gate on→**enricher 강제 포함**(enricher→analyzer→gate→notifier), gate off→게이트 노드 없음 |
| `test_decision_store_persist` | (§8.3) `NotificationDecision`이 `decision_store`에 적재(tier·reason·priority·signals·fingerprint·ts), **DASHBOARD/SUPPRESS도 기록** |
| `test_gate_off_regression` | `enable_noise_gate=false`→기존 발송 무변경 |
| `test_arch_check` | 계층 위반 0 |

> 폴스타 DB 실연동은 통합 마커 분리(CI 스킵) — Plan 47/51 정책 계승.

### 11.1 검증 baseline 정책 (회귀 판정 — 2026-06-29 실측 반영)

52 구현의 회귀 판정 기준은 **"전체 테스트 그린"이 아니라 "baseline 대비 신규 실패 0"** 이다. 현 브랜치
(multiintent)에는 **52와 무관한 기존 실패 46건**이 존재한다 — `_llm_classify` dict 반환 · `_build_response_prompt(sql=)`
시그니처 변경 · API 인증 `401`(로그인 D-026) 등 **multiintent/인증 작업의 테스트 미갱신**이며, *노이즈 모듈을
import하는 실패 파일은 0건* 으로 실측 확인됨(52 회귀 아님). 따라서:

- **52 합격 기준**: ⓐ `test_alarm_*`·`test_graph*` + 신규 `test_notification_policy`/`test_decision_store`/
  `test_polestar_noise_context` 전부 통과, ⓑ `AppConfig` 정상 생성, ⓒ `arch_check --ci` 위반 0, ⓓ 게이트오프 회귀 0
  (`enable_noise_gate=false`→기존 발송 무변경). **현재 ⓐ~ⓓ 모두 충족**(알람 단위 57/57 통과·arch 0).
- **기존 46건 분리·후속**: multiintent 부채로 **별도 트랙**에서 갱신한다(52 범위 밖). 실패를
  `_llm_classify`(반환구조)·`output_generator`(시그니처)·API(인증 fixture) 3그룹으로 묶어 현행 인터페이스에 맞게
  테스트를 수정. 회귀 측정은 **baseline(이 46건) 차감 후 비교**하여 52 검증과 혼동을 방지한다.

---

## 12. 리스크 및 대응

| # | 리스크 | 심각도 | 대응 |
|---|--------|--------|------|
| R-1 | **true positive 억제(실제 장애 묵살)** | High | 재현율 우선·비용비대칭, **심각도3 절대 PAGE**, 불확실시 PAGE, 억제≠삭제(기록), 메타모니터링 |
| R-2 | LLM `is_routine` 비결정성으로 오억제 | High | `is_routine`은 **보조 입력만**, 하드 규칙은 결정적, 심각도3 무관 PAGE(D-035) |
| R-3 | 토폴로지/의존성 stale → 오억제 | Med | 의존성 억제 옵트인, 신뢰도 낮으면 보수적 PAGE, 부모상태 실시간 확인 |
| R-4 | `IMPORTANCE_ID` 값코드 인스턴스별 상이 | Med | 표본 확인 후 매핑(`importance_value_map_csv`), 미매핑은 보통취급→보수적 |
| R-5 | 폴스타 DB 부하(신호 조회) | Med | 고정 SQL+단기캐시+타임아웃+graceful(실패시 PAGE) |
| R-6 | 억제기 자체 장애로 전부 묵살/전부 발송 | Med | 메타모니터링(억제율·무수신), off시 기존 경로 폴백 |
| R-7 | 프로필 미등재 컬럼(IMPORTANCE 등) | Low | 프로필 허용테이블/컬럼 보강 선행(Phase E1) |
| R-8 | 유지보수/점검 윈도우 자동해소 오류 | Low | 신규 발송만 억제, 진행중 통보 보존(업계 동작) |
| R-9 | 과도 그룹핑으로 2차 문제 은폐 | Low | 그룹 키 적정화, 그룹 내 상이 심각도 분리 |
| R-10 | **AI 심각도 보강 오판(하향→억제)** | High | **상향 전용(monotonic) `max()` 강제, 하향 경로 부재**, 결정적 시그니처 우선, 심각도3 PAGE, 기본 off |
| R-11 | AI 심각도/액션가능성 환각 | Med | 시그니처 그라운딩(Plan51 부록A.1), 자문·보조만(판단은 결정적), 근거 시그니처 기록 |
| R-12 | **agentic enricher가 판단까지 越權(환각→오억제)** | High | enricher는 **`signals` 보조 입력만**(승격 전용), 판단은 결정적 `decide_notification`, 심각도3 PAGE 불변, **강등 경로 부재**(§3.12·§8.7) |
| R-13 | agentic 비용·지연 폭증 / vLLM 인프라 의존 | Med | 경계·메시지 알람 한정 + 타임아웃·도구 호출 상한·캐시, **vLLM 미서빙 시 semantic-routing(FabriX 1회 분류) 폴백 → 그조차 없으면 결정적 게이트**(가용성 3중화, §3.12), 전 기능 옵트인 |

---

## 13. 의사결정 영향 (`docs/02_decision.md`)

**D-048. 알람 노이즈 캔슬링 — 4-티어 발송 게이트** 등재 완료(2026-06-29):

> **번호 정정**: 원안 D-040은 2026-06-23 변경이력이 D-039(다중의도)·D-040(replanner)을 선점하여 충돌 →
> **D-048** 등재(decision.md). Plan 53(로드맵 D-041)·Plan 55(D-043) 예약 번호는 각 착수 시 다음 빈 번호로 재조정.

- **D-048.1** 4-티어(PAGE/TICKET/DASHBOARD/SUPPRESS), 결정적 규칙=판단·LLM=보조, 결정 근거 기록.
- **D-048.2** 중요도×심각도 매트릭스 + 유지보수/의존성/인히비션/플래핑/자가복구 억제. **심각도3 절대 PAGE**.
- **D-048.3** 신호는 폴스타 읽기전용 DB(IMPORTANCE/MAINTENANCE/DEPEND/`cmm_alarm_def_noti*` 앵커).
- **D-048.4** 재현율 우선·억제≠삭제·억제기 메타모니터링. 전 기능 옵트인.
- **D-048.5** AI 분석은 **LLM(인컨텍스트)로, ML 모델 미사용**. 메시지 심각도 보강은 **상향 전용(monotonic)** +
  결정적 시그니처(Plan51 부록A.1) 우선. 폴스타 심각도가 베이스라인(SSOT).
- **D-048.6** (향후·미구현) **Plan 55 멀티소스 확장 대비** — `noise_context` 소스 무관 구조 · step 8 보조 축 ·
  영향 신호 **승격 비대칭**(§6.4·§8.6). 지금은 설계 예약만(폴스타 전용 구현). 착수 시 Plan 55 D-043과 연계.
- **D-048.7** (향후·옵션) **deepagents Advisory Enricher** — agentic 분석은 **보조(`signals` 승격 전용)** 만,
  판단은 결정적. vLLM(트랙 B, D-037) 옵트인 전제, 미가용 시 결정적 폴백. §3.12·§6.5·§8.7. 환각→오억제는 R-12로 통제.

기존 정합: **D-035 확장**(패턴을 부가정보에서 → 결정적 게이트의 보조 입력으로, 심각도3 보존 유지),
D-029~D-032 재사용, D-003(읽기전용) 준수. 충돌 없음.

### 13.1 사용자 확인 필요 항목 (착수 전)

1. **`IMPORTANCE_ID` 값 코드 의미**: 폴스타 인스턴스에서 1/2/3…이 낮음↔높음 중 무엇인가(매핑 확정 필요).
2. **억제 적극성 정책**: 어디까지 억제할 것인가? (저중요도 주의알람 SUPPRESS vs DASHBOARD) — 기본은 보수적.
3. **TICKET 티어 구현 방식**: 저우선 별도 채널 vs 일배치 요약 vs 단순 기록 — 운영 방식 확인.
4. **폴스타 알림정책(`cmm_alarm_def_noti*`) 앵커 사용 범위**: 폴스타 정책을 어디까지 신뢰/반영할지.
5. **유지보수/점검 윈도우 소스**: `IS_MAINTENANCE` 외 별도 점검 일정 테이블/ITSM 연동 여부.
6. **업무시간/시간대 강등** 적용 여부(야간 저심각도 강등 등).
7. **AI 메시지 심각도 보강(§3.11) 적용 범위**: 어떤 알람 유형(LogMonitor/보안/앱 로그)에 적용할지,
   상향 전용 정책 확정(하향 억제 금지 기본 권장).
8. **deepagents Advisory Enricher(§3.12) 도입 여부·시점**: vLLM 오케스트레이터(트랙 B, Plan 48/49) 가용성,
   적용 알람 유형(메시지형 한정 권장), 도구 읽기전용 화이트리스트 범위. (E1~E4와 독립 — vLLM 인프라 준비 후 옵트인)
9. **ack/incident 라이프사이클 계측 소스(§9.1)** — E3 후속: `/alarm/metrics`의 MTTA/MTTR/사건전환율을
   산출하려면 (a) **ack(확인) 이벤트 포착 위치**(워크B 회신/웹 UI "확인" 버튼/ITSM)와 저장 방식,
   (b) **incident 라이프사이클 관리 위치**(ITSM 연동/내부 incident 테이블) 결정이 선행되어야 한다.
   MTTR은 기존 자가복구 상관에 소요시간 기록 추가로 단기 산출 가능(저위험), MTTA·사건전환율은 ack/
   incident 소스 확정 후 계측. (억제 결정과 독립 — 관측성 보강)
10. **워커 PAGE→UI 라이브피드 parity(선택·후속)** — D-048.10 SSE 브리지는 TICKET/DASHBOARD만 브리지한다
    (워커 PAGE는 이미 workb/webhook 실발송되어 "안 보이는" 문제 대상 아님). PAGE도 UI 라이브피드에 표시하길
    원하면 별도 소규모 후속으로 분리(notifier PAGE 경로 SSE publish 추가 + API `push_to_ui` 직접 발행과
    이중발행 게이팅 필요). 현재 범위 외.

---

## 14. 변경 범위 요약

### 14.0 기존 코드 통합 분석 (수정 지점)

> 실측(2026-06-26, `src/alarm/*` 코드 분석) 기반. 노이즈 게이트는 **기존 알람 서브그래프에 노드 1개 삽입**으로
> 통합되며, **deepagents/메인 그래프(`src/graph.py`)와 무관**하다(알람은 독립 서브그래프 + AlarmWorker 경로).

- **`src/alarm/orchestration/alarm_graph.py`(수정)** — 현재 배선 `enricher → analyzer → notifier`
  (`build_alarm_graph`, L64-81)에 **`notification_gate` 노드를 analyzer↔notifier 사이에 삽입**
  (`analyzer → notification_gate → notifier`). `AlarmState` TypedDict(L34-41)에 `noise_context`·
  `notification_decision` 필드 추가. `enable_noise_gate=False`면 게이트 노드를 빼고 기존 배선 유지(회귀 0).
- **`src/alarm/application/nodes/alarm_context_enricher.py`(수정)** — 기존 history/process 동시수집
  (`asyncio.gather`+타임아웃)에 noise_context(중요도·유지보수·의존성·알림정책) 수집 추가(또는 별도 enricher 노드).
- **`src/alarm/application/nodes/alarm_notifier.py`(수정)** — 현재 항상 전 채널 발송(L128-156)을 **티어 인지**로
  변경: PAGE→기존 `_send_workb`/`_send_webhook`, DASHBOARD→SSE(`alarm_bus`)만, TICKET→저우선/요약, SUPPRESS→감사만.
- **`src/alarm/application/alarm_worker.py`(수정)** — **dedup 키 교정**: 현재 `alarm_id`(발생건마다 신규,
  `_is_duplicate` L240·키 L255)는 재발생 dedup 불가 → 핑거프린트(`db_id+server+alarm_name+resource`)로 변경(§6.1).
  **`min_severity` 필터(L201)와 게이트의 역할 분리는 §4.8 적용**(게이트 활성 시 `min_severity`↓, 강등·억제는
  게이트가 수행, severity 0은 자가복구용 전달, 심각도 3 드롭 금지).
- **신규 노드 주입** — `notification_gate`는 결정적 `notification_policy.decide_notification` 호출(LLM 불요).
  AlarmWorker가 noise_context repo를 graph config로 주입(history_repo/process_client 주입 패턴 재사용).
- **`src/config.py`(수정)** — `enable_noise_gate` 외 노이즈 설정(§8.5).
- **`config/db_profiles/polestar_*.yaml`(수정)** — `IMPORTANCE_ID`·`IS_MAINTENANCE`·`AVAIL_DEPEND_RESOURCE_ID`·
  `cmm_alarm_def_noti*` 허용 컬럼/가이드 보강(`cmm_alarm_def_noti*`는 이미 허용테이블 등재 — §2.2).
- **메인 그래프·오케스트레이션 무관** — 노이즈 게이트는 push(알람) 경로 전용이므로 `src/graph.py`·
  `intent_planner`·`deepagents_tools`·`semantic_router` 수정 **불필요**.

### 신규 파일
- `src/alarm/domain/notification_policy.py` — 결정적 결정 파이프라인(순수함수) + `NotificationDecision`(signals 키 스키마 §8.2)
- `src/alarm/infrastructure/polestar_noise_context.py` — 중요도/유지보수/의존성/알림정책 고정 SQL
- `src/alarm/infrastructure/decision_store.py` — 결정 감사·집계 적재(신규, Plan 54 §6과 공용)
- `src/alarm/application/nodes/notification_gate.py` — 게이트 노드
- `src/alarm/application/nodes/agentic_enricher.py` — deepagents 보조 분석기 노드 (**옵션·Phase E5**, §8.7)
- 노이즈 신호 수집 읽기전용 deepagents `@tool` (`src/orchestration/deepagents_tools.py` 패턴 재사용, 옵션·E5)
- `tests/test_alarm/test_notification_policy.py` 외(§11)

### 수정 파일
- `src/alarm/application/nodes/alarm_context_enricher.py` — 노이즈 컨텍스트 동시 수집(또는 별도 enricher)
- `src/alarm/application/nodes/alarm_notifier.py` — 4-티어 라우팅
- `src/alarm/orchestration/alarm_graph.py` — `notification_gate` 노드 삽입
- `src/alarm/application/alarm_worker.py` — 핑거프린트 dedup 교정(§6.1)
- `src/alarm/orchestration/alarm_graph.py`(AlarmState 확장: noise_context, notification_decision — **domain/alarm.py 아님**, §8.4) + 게이트 배선 조건 `history_enabled or enable_noise_gate`(§8.1)
- `src/config.py` — `enable_noise_gate` 외 노이즈 설정(§8.5)
- `config/db_profiles/polestar_*.yaml` — 허용테이블/컬럼에 IMPORTANCE_ID/IS_MAINTENANCE/AVAIL_DEPEND 보강
- `.env.example` — `ALARM_*`/`NOISE_*`
- `docs/02_decision.md` — D-048 등재

### 변경하지 않는 파일 (재사용)
- 알람 이력 repo·패턴 통계(Plan 47)·프로세스 보강(47-1)·SSE `notification_bus`·workB/webhook 발송 로직.
  (※ 구조화 **결정 감사는 신규** `decision_store` — §8.3. 알람 경로엔 기존 구조화 감사가 없어 "감사 인프라 재사용"이 아님)

---

## 15. 참고 (기법 출처)

- 발송 철학/5-질문/심각도: Google SRE — sre.google/sre-book/monitoring-distributed-systems/,
  /practical-alerting/, /being-on-call/ ; SLO 번레이트: sre.google/workbook/alerting-on-slos/
- 우선순위/심각도: PagerDuty incident-priority·priority-matrix·severity-classification ;
  Atlassian/Opsgenie incident-priority-levels
- dedup/그룹핑/억제/침묵: Prometheus Alertmanager(configuration, alertmanager), alerting_rules(`for`/`keep_firing_for`)
- 유지보수/의존성/플래핑: Nagios downtime·networkreachability·dependencies·flapping
  (기본 플랩 임계 low 5.0/high 20.0 — 샘플 nagios.cfg)
- 상관/스톰: BigPanda alert-correlation-logic ; Moogsoft Cookbook/Tempus ; PagerDuty intelligent/content/time grouping
- 레이트리밋/자가복구: Opsgenie api-rate-limiting·de-duplication ; PagerDuty rest-api-rate-limits·configurable-service-settings
- 데이터기반(배경, **ML은 미채택 — LLM-ICL로 대체**): AlertRank(INFOCOM 2020)·TEQ(arXiv:2302.06648)·
  DeepLog(CCS 2017)·AIOps 서베이(JNCA 2024) — 특징 중요도·피드백 가치의 근거로만 인용
- LLM-RCA(인컨텍스트·few-shot 그라운딩): `plans/51-fault-diagnosis-data-collection.md` §3.8 계승
- 지표: Atlassian incident KPIs ; incident.io alerting best practices(액션가능 30~50%) ;
  Google ML crash course(precision/recall/PR-AUC)
- 프로젝트 자산/데이터: `src/alarm/*`, `schema/polestar-schema.md`, `config/db_profiles/polestar_*.yaml`
- 자매 계획: `plans/47-alarm-history-pattern-analysis.md`, `plans/50-fault-diagnosis-rca.md`,
  `plans/51-fault-diagnosis-data-collection.md`
