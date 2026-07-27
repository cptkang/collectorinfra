# 64. 이벤트 자동 조사·진단 브리핑 및 장애 대응 오케스트레이션 (Automated On-Event Investigation, Triage Briefing & Response)

> 작성일: 2026-07-21
> **상위 로드맵**: **Plan 62(AIOps 전체 역량 마스터 로드맵)**. 본 계획은 Plan 62 **§5.2가 예약한 "Plan 64 — 자동 복구·폐루프" 슬롯을 구체화**하되, 요구(이벤트 발생 시 자동 조사·중요도 판단·운영자 전달)에 맞춰 범위를 **탐지→조사→브리핑→조치권고(human-gated)** 로 우선 확정한다(자동 실행은 거버넌스 확정 후). Phase **P2(진단·RCA)** 의 조사·브리핑 + Phase **P4(자동복구)** 의 조치권고에 걸친다.
> **대상/선행 계획**: Plan 50(장애진단·RCA — `diagnosis_graph`·push 훅, 계획), Plan 51(OS 데이터 수집·진단 기법 L1/L2/L3·§9 보안통제, 계획), Plan 52(노이즈 게이트 — 구현 완료, D-048), Plan 60(노이즈 게이트 고도화 — 트리거 훅 §14 제공), Plan 53(장애관리 로드맵 Wave2·4·5), Plan 47-1(실시간 프로세스 API).
> **관련 결정**: **D-003(읽기전용 절대원칙 — 본 계획의 최대 제약)**, D-035(결정적 규칙=판단·LLM=보조), D-037(deepagents/서브그래프), D-048(노이즈 게이트), D-049(decision_store 감사).
> **신규 결정(본 계획에서 부여, 착수 시 등재)**: **D-101**(노이즈 게이트 PAGE→자동 조사 오케스트레이션 트리거·중요도 2차 판정 상향 전용), **D-102**(L3 실호스트 읽기전용 진단 수집기 + 보안통제), **D-103**(조치 권고 폐루프 거버넌스 — renice/kill 등은 human-gated 권고만).
> ※ 번호 규칙(Known Mistakes 2026-06-25·06-29): `grep -roE "D-[0-9]{3}" docs/ plans/` 현재 등재 최댓값 **D-100**(D-100 헤더·변경이력 등재 완료), plans/ 예약 최댓값 D-091. 본 계획은 그 위 연속 빈 블록 **D-101~D-103** 부여. 구현·등재 직전 `## D-` 헤더와 「변경 이력」 표를 모두 재확인해 충돌 시 다음 빈 번호로 재조정한다.
> ※ 본 계획은 **운영 호스트 접근(L3)** 과 **조치 권고**를 포함한다 — D-003과 충돌하는 영역이므로, §7(보안통제)·§8(거버넌스)·§12(블로커)의 결정을 **사용자·보안팀 확인 후** 착수한다(CLAUDE.md 의사결정 규칙).
> **상태**: 계획 (미구현). 사용자 확정(2026-07-21): ①산출물=Plan 60 훅 + 신규 Plan 64, ②조사 범위=**L3 실호스트 명령까지 즉시 대상**(→ §7 보안결정 선행), ③조치=**권고만·운영자 승인**(D-003 유지). **통합 재편(2026-07-24 · D-118)**: 조사 실행 본체(ReAct 조사 루프·severity_judge·브리핑 조립·조치 권고 생성)는 **`sre_agent/` 독립 패키지(HolmesGPT — `plans/sre-agent/02`)가 담당**하며, 본 계획의 `investigation_graph` 자체 구현(§3)은 **대체됨**. 본 계획의 잔존 유효 범위 = 요구·조사 절차 사양(§1·§4)·거버넌스(§8)·**collectorinfra 측 소비 배선** — 섹션별 상태와 재편 Wave는 **§0** 참조.

---

## 0. 통합 재편 (2026-07-24 · D-118) — 조사 실행의 `sre_agent/` 위임과 잔존 범위

SREAgent 통합(D-118, `plans/sre-agent/` 이관)으로 본 계획의 조사 실행 본체는 **HolmesGPT 기반 `sre_agent/` 독립 패키지**가 담당한다. sre-agent/02는 본 계획을 이식하며 "고정 LangGraph 파이프라인 → HolmesGPT ReAct + 결정적 후처리"로 재설계했고(그쪽 §2 — 원본이 §9.1에서 벤치마킹한 HolmesGPT를 SDK로 직접 채택), 노출 계약은 sre-agent/05(submit/poll·`contract_version`)가 기준 문서다. **본 계획의 조사 파이프라인을 collectorinfra에서 재구현하지 않는다**(중복 구현 금지).

### 0.1 섹션별 상태 매핑

| 섹션 | 상태 | 승계·잔존 |
|---|---|---|
| §1 요구·원칙 | **유효** | 요구 정의 원본 — sre-agent/02 §1~§2가 계승(D-035 경계 동일) |
| §2 기존 자산 재사용 | 부분 유효 | 폴스타 데이터 채널은 sre-agent/04(`mcp_server` 고수준 도구 8종)로 수렴. `alarm_notifier`·`decision_store` 재사용 행은 §0.2 배선에서 유효 |
| §3 investigation_graph·트리거 | **대체됨** | 조사 루프=sre-agent/02 §3(DiagnosisAgent + 결정적 dispatcher). 트리거 소비=Plan 60 §14 훅 → `sre_investigate_alarm`(sre-agent/05 §3). "Plan 50 §8.2 push 훅 재사용" 배선은 폐기. §3.4 노이즈 상속 원리는 sre-agent/02 §4에 계승 |
| §4 조사 절차·명령 카탈로그·§4.7 폴스타 API | **사양 원본(유효)** | 트리아지 절차·USE 매핑·폴스타 API 채널은 sre-agent/02 §5.3(조사 지침)·sre-agent/04 §4.2(도구 명세)의 원천. §4.8 L1 보강=Plan 60 §16 E6(기구현)·L3 심화=`sre_agent` 조사 브리핑 첨부로 대체 |
| §5 severity_judge | **이관** | sre-agent/02 §6(결정적·escalate-only — 동일 시그니처 표, `sre_agent/` 패키지 구현). collectorinfra는 poll 결과의 `verdict`를 소비해 후속 통보 승격만(§0.2 CW-C) |
| §6 브리핑 | **이관** | sre-agent/02 §7(6요소·인용 검증). collectorinfra는 브리핑 JSON 수신→`alarm_notifier` 첨부(§0.2 CW-A — §6.2 채널 재사용은 유효) |
| §7 L3·보안통제 | **이원화(유효)** | 게이트 목적 L3=Plan 60 §18 E8(폴스타 에이전트 확장 — collectorinfra 측·D-117 확정). 조사(원격) L3=sre-agent/06(Prometheus + 폴스타 MCP 2축·SSH 미채택). §7.2 허용목록·통제 사양은 E8 수집기와 `sre_agent` 로컬 `vm_profile`의 공통 기준으로 유효 |
| §8 조치 권고 거버넌스 | **유효(관할 유지)** | 권고 생성은 sre-agent/02 §9(human-gated 동일·실행 경로 미탑재). 자동 조치 거버넌스(§8.3 B-3=D-003 예외)는 본 계획이 계속 관할 |
| §9 문헌 | 유효 | 공용 근거(sre-agent/02 §2가 인용) |
| §10~§14 Wave·블로커·테스트·산출물 | **재편** | §0.2·§0.3으로 대체(원문은 참조용) |

### 0.2 collectorinfra 측 잔여 작업 (재편 Wave)

| Wave | 내용 | 선행 |
|---|---|---|
| **CW-A** | 게이트 훅 배선 — `notification_gate` PAGE 시 비차단 emit → MCP 클라이언트(기존 `DBHubClient` 패턴·SSE·Bearer)로 `sre_investigate_alarm` submit(페이로드=Plan 60 §14.2 보유값의 `contract_version: "1"` 직렬화 — sre-agent/05 §4) → 후속 poll(`sre_get_investigation`) → 브리핑을 `alarm_notifier` 통보 첨부 + `decision_store` 감사. 옵트인 `investigation_trigger_enabled`(기본 off·비활성 시 회귀 0) | sre-agent Plan 04 M-B → 02 W-A/W-B → 05 서비스 기동 |
| **CW-B** | pull 위임 — deepagents `fault_diagnosis` 의도에서 `sre_diagnose` 위임(동일 submit/poll — sre-agent/05 §7) | 〃 |
| **CW-C** | 후속 통보 승격 — poll 결과 `verdict.escalate`(ImportanceVerdict) 시 **escalate-only** 후속 통보 승격(§5.1 계약 유지 — 게이트 판정 소급 변경 없음) | CW-A |

- **테스트(재편)**: CW-A 계약 테스트(페이로드 필수 필드 결측 시 `rejected`·`duplicate` 재submit 비중복·조사 서비스 다운 시 게이트 무영향 graceful), `investigation_trigger_enabled=False` 회귀 0, 브리핑 첨부 렌더. 조사 파이프라인 자체 테스트(§13의 조사·병목·중요도·브리핑 행)는 `sre_agent/` 패키지 소관(sre-agent/02 §12).
- **산출물(재편)**: §14의 신규 모듈 중 `investigation_graph.py`·`severity_judge.py`·`briefing_deliverer.py`·`remediation_recommender.py`는 **생성하지 않는다**. collectorinfra 신규 = `sre_agent` MCP 클라이언트(`src/alarm/infrastructure/` — DBHubClient 패턴)·게이트 훅 emit·notifier 브리핑 첨부·config 플래그. `host_diagnostic_collector.py`(폴스타 에이전트 어댑터)는 게이트 목적으로 Plan 60 §18 소관.

### 0.3 블로커·결정 번호 재편

- **B-1(L3 보안)**: 게이트 목적은 **D-117로 해소**(폴스타 에이전트 확장 — Plan 60 §18.1). 조사(원격)는 sre-agent/06(SREAgent D-019 인용 — Prometheus + 폴스타 MCP 2축·SSH 미채택)으로 종결.
- **B-2(권고)·B-3(자동화 거버넌스)**: 유지 — B-3(D-003 예외)는 여전히 최대 블로커(sre-agent/02 §9도 동일하게 실행 경로 미탑재를 테스트로 고정).
- **예약 D-101~103**: ux_improvement 병합이 D-101~104를 이미 점유(02_decision.md §8)해 어차피 재부여 대상이었다. 재편 후 — D-101(트리거·오케스트레이션)은 **CW-A 배선 결정으로 축소 등재**, D-102는 D-117이 대체, D-103(권고 거버넌스)은 sre-agent/02의 권고 결정(구 SREAgent D-011 예약)과 합쳐 착수 시 등재. 전부 collectorinfra 채번 규칙(`## D-` 헤더+「변경 이력」 grep 최댓값+1)으로 부여.

---

## 1. 개요 및 목적

### 1.1 배경 — 운영자 트리아지 절차의 자동화

숙련된 운영자는 서버 이벤트(장애·경보) 발생 시 **정형화된 순서**로 현황을 조사한다:

```
① 부하 확인      : top / uptime            → 시스템이 실제로 바쁜가(load·run-queue)
② 병목 식별      : CPU(us·sy) · 메모리(swap) · I/O(wa) 구분  → 무엇이 포화됐나
③ 원인 격리      : renice / kill            → 어느 프로세스가 원인인가(조치 후보)
④ 로그 분석      : journalctl · systemd 자동 재시작          → 커널·서비스가 무엇을 말하나
```

이 절차가 체화되면 **처음 보는 장애도 같은 순서로** 신속·정확히 분류된다. 본 계획은 **이 판단 프로세스를 LLM 기반으로 자동화**한다: 노이즈 게이트(Plan 60/52)가 이벤트를 **중요(PAGE)** 로 판정하면, 위 ①~④를 **자동으로 조사**해 ⓐ 이벤트의 중요도를 정밀 판정하고 ⓑ 조사 결과를 **운영자에게 브리핑**하여 장애 대응을 빠르고 정확하게 만든다.

**핵심 통찰(중복 회피)**: 조사에 필요한 **데이터·기법 계층은 Plan 51**(L1/L2/L3 수집 + USE·60초 트리아지·유형별 플레이북 + §9 L3 보안통제)에, **진단 파이프라인 아키텍처는 Plan 50**(`diagnosis_graph`: 증거수집→상관→인과추론→리포트, push/pull 트리거)에 이미 설계돼 있다. 본 계획은 그 위에 **① 노이즈 게이트 발생 시 자동 오케스트레이션(트리거·조정), ② 중요도 2차 판정, ③ 운영자 브리핑 전달, ④ 조치 권고 거버넌스** 라는 **조정·전달·대응 계층**만 얹는다 — 수집·분석 로직을 재구현하지 않는다.

### 1.2 요구 → 기능 매핑

| 사용자 트리아지 단계 | 자동화 기능 | 결정적(수집·판정) | LLM(해석·서술) | 계층 |
|---|---|---|---|---|
| ① 부하 확인(top/uptime) | 부하·포화 스냅샷 | load avg·run-queue·CPU/Mem/FS Util 추이 | "정상 대비 N배 부하" 서술 | L1(추이)+L3(top/uptime) |
| ② 병목 식별(us/sy/swap/wa) | USE 자원 분류 | CPU us/sy/wa·steal, swap si/so, IO await/%util 결정적 분류 | "IO 병목(디스크로 드릴다운)" | L1(Util)+L3(vmstat/mpstat/iostat) |
| ③ 원인 격리(renice/kill) | 원인 프로세스 지목 + **조치 후보** | Top CPU/Mem 프로세스·선행 상관·OOM killed pid | "프로세스 X가 유력 원인(신뢰도)" | L1(프로세스 API)+L3(pidstat) |
| ④ 로그 분석(journalctl) | 로그 시그니처·재시작 이력 | `journalctl -p err`·dmesg OOM·systemd restart 시그니처 매칭 | "OOM으로 X 종료, systemd 재시작 3회" | L1(관제 로그)+L3(journalctl/dmesg) |
| (종합) 중요도 판단 | **중요도 2차 판정** | 병목 확정·OOM·FS RO·프로세스 다운 = 상향 신호 | 근거 인용·신뢰도 서술 | — |
| (전달) 운영자 통보 | **구조화 브리핑** | 타임라인·증거 정렬 | glass-box 서술(인용 30초 검증) | — |
| **(보강) 메시지 분석 기반 컨텍스트 첨부** | **타깃 컨텍스트 보강(§4.8)** | 메시지→프로파일 결정 매핑·타깃 수집·요지 조립 | 메시지 분류·요지 서술 | L1+L3 |

### 1.3 설계 원칙 (전 계획 계승 — 불변)

1. **읽기전용·비침습 절대 원칙(D-003)** — 모든 **조사**는 조회/읽기만. L3 호스트 명령도 **허용목록 read-only**(top·free·vmstat·journalctl 등 출력 캡처만), 어떤 변경·재기동·부하도 금지(§7). `renice`/`kill` 등 쓰기 조치는 **자동 실행하지 않고 권고만**(§8).
2. **결정적 규칙=판단 / LLM=보조 서술(D-035)** — 병목 분류(USE)·시그니처 매칭·상관·중요도 신호는 **결정적 Python**. LLM은 사전수집된 증거의 **해석·서술·순위화**만(2단계 파이프라인 — RCACopilot 정합, §9).
3. **human-in-the-loop 조치** — 조치는 **제안까지만**, 실행은 운영자 승인. 위험기반 게이팅(고신뢰+저위험 후보만 노출). 자동 실행은 D-003 예외 거버넌스 확정 전 금지(§8, Plan 62 §5.2).
4. **글래스박스·인용 의무** — 브리핑의 모든 주장은 로그줄/메트릭/프로세스 단면을 **인용**(운영자가 30초 내 검증). 환각 방지의 최대 레버(Plan 51 §3.8).
5. **폐쇄망·tool-calling 비의존** — 조사 파이프라인은 고정 LangGraph 서브그래프(Plan 50 계승). 워커 LLM(FabriX/KBGenAIChat)로 동작, 외부 SaaS·인터넷 tool-calling 금지.
6. **옵트인·회귀 없음** — 전 기능 기본 off 플래그. 비활성 시 게이트(Plan 60)·기존 경로 무변경. 트리거는 **비차단**이라 게이트 지연 예산(<10s)에 무영향.
7. **조사 대상의 노이즈 상속** — 조사는 게이트가 PAGE한 **대표 사건**만 발화(E1 dedup·E2 클러스터 대표 상속) → 동일 사건 반복 조사·연쇄 조사 폭주 자동 차단(§3.4).

### 1.4 로드맵 위치

Plan 62 §5.2가 "Plan 64 — 자동 복구·폐루프(거버넌스 우선)"를 예약했다. 본 계획은 그 슬롯을 다음과 같이 구체화한다:

- **즉시 구현 범위(폐루프 앞 절반)**: 탐지(게이트)→**조사(read-only L1+L3)**→**중요도 2차 판정**→**운영자 브리핑**→**조치 권고**(운영자 실행). = Plan 62 §5.2의 "초기엔 human-in-the-loop 승인(runbook 제안까지만)".
- **후속·거버넌스(폐루프 뒤 절반)**: 승인 기반 자동 조치→검증. D-003 예외·승인주체·감사·롤백·blast radius 확정 후(§8·§12 B-3).

---

## 2. 기존 자산 재사용 (실측 앵커 — 재구현 금지)

| 재사용 자산 | 위치(실측) | 본 계획에서의 역할 |
|---|---|---|
| 진단 서브그래프 | Plan 50 §5 `diagnosis_graph`(incident_scoper→evidence_collector→correlation_engine→causal_reasoner→diagnosis_reporter), `DiagnosisState` | 조사 파이프라인의 **본체**. 본 계획은 evidence_collector에 **L3 host-command 소스 추가** + `severity_judge`·`briefing_deliverer`·`remediation_recommender` 확장만 |
| push 트리거 훅 | Plan 50 §8.2(알람 파이프라인→`diagnosis_graph` `trigger="push"`·`source_alarm`) | 노이즈 게이트 PAGE와 연결(§3.2) |
| 노이즈 게이트 트리거 계약 | Plan 60 §14(`notification_gate`가 PAGE 시 비동기 emit·페이로드) | 발화 지점·페이로드 제공 |
| 수집 계층·기법·보안 | Plan 51 §1.4(L1/L2/L3), §3(USE·60초·Golden Signals), §4(데이터 카탈로그), §6(유형별 플레이북), §9(L3 보안통제) | 조사 **무엇을·어떻게·안전하게**의 사양. §4·§7이 이를 인용 |
| 실시간 프로세스 | 폴스타 REST `/rest/server/process/listByhostname`(Plan 47-1) | ③ 원인 프로세스 격리(L1·**현재 단면**) |
| **폴스타 API (ES 백엔드)** | 폴스타 **REST API**(프로세스 정보·**CPU/메모리 실시간 사용률**·로그 이력 — 폴스타가 **내부적으로 ES 조회**, 에이전트는 REST GET·프로세스 API 패턴; 엔드포인트·응답 스키마 벤더 실측, §4.7) | ①② **CPU/메모리 실시간 사용률**(집계 지연 없는 현재값)·③ **과거 프로세스 추이**(realtime 단면 보완)·④ 로그 원문(제공 시). **이미 설치된 폴스타 데이터(옵션 A·신규 설치 0·read-only)** |
| 메트릭 추이 | `cmm_metric_stat_h/d/m`(읽기전용 고정 SQL) + node_exporter(Prometheus, §4.5) | ①②④ 부하·병목·추이(L1) |
| 관제 로그·프로세스 | `cmm_resource` `server.LogMonitor`/`ProcessMonitor`, `conditionLogText` | ④ 로그 시그니처·프로세스 생존(L1/L2) |
| 알림 발송 | `alarm_notifier.py`(WorkB/webhook), `notification_bus.py`(SSE) | ⑥ 브리핑 전달(재사용) |
| 감사 저장소 | `decision_store.py`(JSONL 감사, D-049) | 조사·조치권고 감사 기록(재사용) |

> 원칙: 본 계획의 신규 코드는 **오케스트레이션·L3 어댑터·중요도 판정·브리핑 조립·조치권고**에 국한. 증거수집·상관·인과추론은 Plan 50/51의 모듈을 소비한다.

---

## 3. 아키텍처 — 자동 조사 오케스트레이션

> **대체됨(2026-07-24 · D-118)**: 본 § 자체 구현(LangGraph `investigation_graph`)은 착수하지 않는다 — 조사 루프는 sre-agent/02 §3(HolmesGPT DiagnosisAgent + 결정적 dispatcher)이 담당하고, 트리거는 §0.2 CW-A(`sre_investigate_alarm` MCP submit/poll)로 배선한다. 아래 원문은 요구·데이터 흐름 참조용으로 유지한다.

### 3.1 investigation_graph (Plan 50 diagnosis_graph 확장)

```
              ┌───────────────────────────────────────────────────────────────┐
 (게이트 PAGE │ InvestigationState (= DiagnosisState 확장)                     │
  push /      │  incident_scoper   ─ 대상 서버·기준시각·구간·관심신호(알람 kind) │
  "분석해줘"  │     ▼                                                           │
  pull)       │  evidence_collector ── asyncio.gather(타임아웃·부분실패 허용) ─┐│
   trigger ─► │     ├─ 알람 타임라인      (L1, Plan 50 §4.1)                   ││
              │     ├─ 메트릭 추이        (L1, Plan 50 §4.2)                   ││
              │     ├─ 프로세스 단면      (L1, Plan 47-1 API)                  ││
              │     ├─ 토폴로지           (L1, Plan 60 E4 그래프)              ││
              │     └─ ★ L3 호스트 스냅샷 (L3, §7 — 허용목록 read-only)  ◄─신규 ││
              │     ▼ EvidenceBundle                                          ◄┘│
              │  correlation_engine (결정적 Python) ─ USE 병목 분류·시그니처·타임라인 │
              │     ▼ CorrelationResult                                        │
              │  ★ severity_judge (결정적, §5) ─ 중요도 2차 판정(상향 전용)     │
              │     ▼ ImportanceVerdict(level, confidence, escalate, signals)  │
              │  causal_reasoner (LLM, Plan 50 §7) ─ 원인 가설·근거 인용·신뢰도  │
              │     ▼ DiagnosisReport                                          │
              │  ★ remediation_recommender (결정적+LLM, §8) ─ 조치 후보(권고만) │
              │     ▼ RemediationProposal[]                                    │
              │  ★ briefing_deliverer (§6) ─ 구조화 브리핑 → notifier/SSE       │
              └───────────────────────────────────────────────────────────────┘
   ★ = 본 계획 신규/확장.   L3·severity_judge·remediation은 전부 옵트인 플래그.
```

### 3.2 트리거 (Plan 60 §14 계약 소비)

- **push(주 경로)**: 노이즈 게이트가 PAGE(또는 `investigation_trigger_min_tier`) 결정 시 `notification_gate`가 **비동기·비차단** emit(Plan 60 §14.2). 페이로드 = `AlarmEvent` + `NotificationDecision`(tier·reason·signals·fingerprint·E1 recurrence·E2 클러스터·E4 root_resource). 게이트 반환·라우팅 무변경.
- **pull(보조)**: 사용자가 "○○ 서버 원인 분석해줘"로 명시 요청(Plan 50 §8.1 `fault_diagnosis` 의도·subagent 재사용). 트리거만 다르고 investigation_graph 본체는 동일.
- **비차단·타임아웃 가드**: push 조사는 게이트 응답과 **분리된 태스크**로 실행(SSE/워커 백그라운드). 전체 조사에 **오케스트레이션 타임아웃**(예: 45s, `investigation_timeout_seconds`)을 씌운다 — per-call 타임아웃만으론 SSE 스트리밍이 무력화될 수 있음(Known Mistakes: 장시간 경로 전체 타임아웃 가드 필수).

### 3.3 InvestigationState (DiagnosisState 확장)

```python
class InvestigationState(DiagnosisState, total=False):  # Plan 50 §5.1 상속
    # 트리거 컨텍스트(게이트 페이로드)
    gate_decision: dict            # NotificationDecision(tier·reason·signals·fingerprint)
    # ★ 신규 산출
    host_snapshot: dict            # L3 read-only 수집 결과(§7) — 부재/실패 시 {} (graceful)
    importance: dict               # ImportanceVerdict(§5)
    remediation: list              # RemediationProposal[](§8 — 권고, 실행 아님)
    briefing: dict                 # 구조화 브리핑(§6)
```

### 3.4 조사 대상의 노이즈 상속 (폭주 방지)

- E1 재발생 dedup으로 억제된 재발 알람은 게이트 그래프 진입 전 종료(Plan 60 §3.1) → **트리거 안 됨**(동일 사건 반복 조사 자동 차단).
- E2 크로스-호스트 클러스터는 **대표 1건만** PAGE(연쇄 자식 억제) → 조사도 대표 1건만. 20대 동시 장애 = 조사 1회.
- E4 다홉 연쇄에서 root만 PAGE → root만 조사(증상 노드 조사 안 함).
- 추가 가드: `investigation_dedup_ttl_seconds`(동일 fingerprint 조사 최소 간격)·`investigation_max_concurrent`(동시 조사 상한, 초과 시 큐잉·warning). in-memory 상태는 값 bound + **키 만료 sweep**(Known Mistakes 2026-06-29).

---

## 4. 조사 절차 자동화 — 운영자 트리아지 → 결정적 수집·분석 매핑

각 단계: **L1 소스(즉시)** / **L3 명령(§7 보안게이트)** / **결정적 판정** / **LLM 역할**. 기법은 Plan 51 §3(USE·Netflix 60초·Golden Signals)·§6(유형별 플레이북)을 인용한다.

### 4.1 ① 부하 확인 (uptime / top / vmstat)

- **L1(설치 기준 · 기본 폴스타)**: **기본** — 폴스타 `cmm_metric_stat` CPU/Mem/FS Util 추이(h/d/m 집계)·알람 타임라인 + **폴스타 API(ES)**(CPU/메모리 **실시간 사용률** — 집계 지연 없는 현재값, §4.7). **폴백** — **node_exporter** `node_load1/5/15`·`node_procs_running`(=run-queue)·`node_procs_blocked`(=D-state; 폴스타 미제공 신호 보강).
- **L3 명령(전체)**: `uptime`·`w`(부하평균 추세) → `cat /proc/loadavg`(부하+실행/전체 프로세스) → `vmstat 1 3`(**r**=런큐·**b**=blocked·**si/so**=스왑·**wa**=IO대기) → `top -b -n1`(load·CPU·MEM·top proc 종합 스냅샷). Netflix 60초 진입 2단계.
- **결정적**: 부하 vs baseline(Plan 60 E3 Holt-Winters 재사용) 이탈 배수·run-queue>코어수(CPU 포화)·지속성(연속 K구간). ※ 부하평균↑은 CPU 아닌 **D-state I/O**일 수 있음 → `vmstat r`로 구분(Plan 51 부록 A.2 가드레일).
- **LLM**: "부하평균 15분 3.0→24.0, 정상 대비 8배" 서술(§4.6 역할 1).

### 4.2 ② 병목 식별 (USE — Utilization/Saturation/Errors, 자원별 전체 명령어)

Brendan Gregg **USE 방법론**으로 자원별 결정적 분류(Plan 51 §3.1·부록 A.2). 각 자원의 **원인 검토·분석에 필요한 전체 명령어**:

| 자원 | L3 명령어(전체) | 결정적 판정 |
|---|---|---|
| **CPU** | `vmstat 1`(r·us/sy/id/**wa**/st) · `mpstat -P ALL 1`(코어별 %usr/%sys/%iowait/%steal/%soft) · `sar -u`/`sar -q`(util/run-queue) · `top` · `pidstat -u 1` | us↑=유저부하, sy↑=커널/시스템콜, **wa↑=IO 대기**(→디스크 §6.3), **st↑=가상화 steal**, soft↑=인터럽트, 단일 핫코어=단일스레드 |
| **메모리** | `free -m`/`free -h` · `cat /proc/meminfo`(**MemAvailable**·Committed_AS·**Slab**·SwapFree) · `vmstat`(**si/so**·페이지스캔) · `sar -r`/`-B`/`-W` · `slabtop`/`cat /proc/slabinfo`(슬랩 누수) · `pidstat -r 1` | **si/so>0=스왑 발생**(포화), MemAvailable 낮음=고갈(MemFree 아님), Slab 증가=커널 누수 |
| **디스크 IO** | `iostat -xz 1`(**await**·avgqu-sz·%util·r/s·w/s) · `sar -d` · `pidstat -d 1` · `iotop -bon1` | await↑+%util≈100=IO 포화(**SSD/NVMe는 %util 신뢰금지→await**) |
| **디스크 용량** | `df -h`(용량) · **`df -i`(inode)** · `lsof +L1`(삭제된 열린 파일) · `du -sh` | 용량 100%인데 쓰기실패=**inode 고갈** |
| **네트워크** | `ss -s`/`ss -tan`(소켓·**TIME_WAIT**·backlog) · `sar -n DEV/EDEV/TCP,ETCP 1` · `ip -s link`/`netstat -i`(errors/drops) · `nstat`/`netstat -s`(TCP 재전송) | 재전송↑=NW/원격, drops=포화, TIME_WAIT 폭증=커넥션 과다, conntrack/포트 고갈=신규연결 실패 |
| **SW(FD/스레드)** | `ls /proc/PID/fd\|wc -l` vs `cat /proc/PID/limits` · `cat /proc/sys/fs/file-nr` · `sar -v` | EMFILE=FD 고갈, threads-max 근접=스레드 고갈 |

- **드릴다운 규칙(결정적)**: CPU wa↑→디스크(§6.3), st↑→가상화 경합, swap si/so→메모리(§6.2). 코드가 병목 판정, LLM은 서술만.
- **L1 소스 우선순위(§4.5) — 기본 폴스타 API(ES) · 폴백 node_exporter**: **기본**은 폴스타 API(ES 백엔드 — CPU/메모리 실시간 Util%·신규 설치 0). **폴백**은 node_exporter — USE **분해**(us/sy/wa/steal=`node_cpu_seconds_total{mode}`·si/so=`node_vmstat_pswpin/pswpout`·inode=`node_filesystem_files_free`)는 node_exporter 고유이므로 **분해가 필요할 때만 폴백**한다. 둘 다 없이 폴스타 Util 집계만 있으면 "분해 불가·신뢰도 제한" 명시(Plan 51 §6).

### 4.3 ③ 원인 격리 (프로세스 지목 → renice/kill **후보**)

- **L1(설치 기준)**: 폴스타 실시간 프로세스 API(top N CPU/Mem, args 마스킹, Plan 47-1) + ProcessMonitor `avail_status`(생존).
- **L1+(폴스타 API·신규)**: 폴스타 **REST API**(내부 ES 조회)로 **과거 프로세스 추이**(사건창 top 프로세스 시계열) — realtime API의 "현재 단면" 한계를 보완해 "누가 선행 상승했나"를 조회(신규 설치 0·§4.7). Plan 51이 "폴스타 미보유→신규수집"으로 표시한 갭을 **기존 폴스타 데이터로 해소**.
- **L3 명령(전체)**: `top -b -n1 -o %CPU`/`-o %MEM` · `ps aux --sort=-%cpu|head`/`--sort=-%mem` · `pidstat -u 1`/`pidstat -r 1`/`pidstat -d 1`(프로세스별 CPU/MEM/IO) · `pstree -p`(부모 트리) · `cat /proc/PID/status`(VmRSS·Threads·**State**) · `cat /proc/PID/wchan`·`/proc/PID/stack`(**D-state** 대기위치) · `ls /proc/PID/fd|wc -l`(FD).
- **결정적**: Top 프로세스 CPU/Mem이 메트릭 선행 상승과 시간 일치(타임라인 상관 — **폴스타 API(ES) 추이로 단면 한계 극복**)·OOM killed pid(dmesg)·**D**(무중단 슬립=IO 멈춤)·**Z**(좀비→부모가 원인).
- **LLM**: "java(pid 12345)가 CPU Util 선행 상승과 일치 → 유력 원인(신뢰도 medium — 현재 단면)"(§4.6 역할 2).
- **조치 후보(권고만·§8)**: `renice`(우선순위 하향)·`kill`(종료) 후보를 근거와 함께 제시. **자동 실행 금지** — 운영자 승인·실행(D-003).

### 4.4 ④ 로그 분석 (journalctl / dmesg / systemd 재시작)

- **L1(설치 기준)**: 폴스타 관제 로그 매칭분(`conditionLogText`, LogMonitor — syslog/보안/DB2 매칭 라인) + node_exporter `node_vmstat_oom_kill`(OOM 발생 카운트)·(systemd collector 활성 시)`node_systemd_service_restart_total`.
- **L1+(폴스타 API·신규·조건부)**: 폴스타 REST API가 로그(syslog/journald)를 제공하면 **관제 규칙 밖 로그 원문**도 사건창 조회(§4.5 ④ 갭 축소·신규 설치 0). **로그 제공 여부·범위는 벤더 실측**(§4.7) — 미제공이면 채널2(로그전송)로 폴백.
- **L3 명령(전체)**: `journalctl -p err --since <기준시각-lookback>`(에러) · `journalctl -k`(커널) · `journalctl -u <unit> --since <T>`(서비스) · `journalctl -b`(부팅후) · `dmesg -T`/`dmesg --level=err,crit`(커널 링버퍼: OOM/segfault/FS RO/call trace) · `systemctl status <unit>` · `systemctl show -p NRestarts,ActiveState,SubState <unit>`(**재시작 횟수**) · `cat /var/log/{messages,syslog}`(권한 시) · `cat /var/log/{secure,auth.log}`(인증) · `coredumpctl list`/`coredumpctl info`(권한 시).
- **결정적 시그니처 매칭**(Plan 51 부록 A.1): `Out of memory: Killed process`(OOM)·`segfault at`(크래시)·`Too many open files`(FD)·`read-only filesystem`(FS 손상)·`soft lockup`·`hung_task`·`Call Trace`·systemd `Failed with result`·`start-limit-hit`/`Start request repeated too quickly`(재시작 루프). `NRestarts` = 플래핑/크래시 루프 신호.
- **LLM**: "OOM으로 java(pid 12345) 종료 → systemd 3회 재시작 후 start-limit-hit → 서비스 다운" 로그 라인 인용(§4.6 역할 1·2).

> **보안 주의(§7)**: L3 로그 수집은 비밀정보(환경변수·인증로그) 마스킹 후에만 LLM·저장. `/proc/PID/environ`·코어덤프 기본 미수집. **변이 명령**(`dmesg -C/-c`·`renice`·`kill`·`systemctl restart`)은 수집기에 부재(§7.2 허용목록·Plan 51 §9).

### 4.5 명령어 → 설치 기준 커버리지 (신규 에이전트 없이 얻는 것)

> **소스 우선순위(2026-07-21 확정): 기본 = 폴스타(DB·REST·ES) / 폴백 = node_exporter.** 폴스타 에이전트는 벤더 검증 채널이자 프로세스·로그·**실시간 사용률**을 통합 제공하므로 **1순위로 조회**하고, 폴스타가 못 주는 신호(특히 USE **분해** us/sy/wa/steal·host 카운터)만 **node_exporter로 폴백**한다. 둘 다 이미 설치돼 신규 설치 0. 폴스타/ES의 정확한 제공 범위는 벤더 실측(§4.7). 상세: `docs/aiops_benchmark/l3_host_collection_mechanism.md` §4A.

| L3 신호 | **기본: 폴스타(DB·REST·ES)** | **폴백: node_exporter** |
|---|---|---|
| CPU/Mem **실시간 사용률** | **폴스타 API(ES)** 실시간 Util% | (분해 필요 시만) |
| CPU us/sy/wa/steal **분해** | 폴스타 API 제공 시 그 값, 미제공 시 → | **`node_cpu_seconds_total{mode}`** |
| 메모리 상세(MemAvailable) | 폴스타 API(ES) / `cmm_metric_stat` | `node_memory_*` |
| swap si/so | (폴스타 API 제공 여부 실측) | `node_vmstat_pswpin/pswpout` |
| 부하·run-queue | `cmm_metric_stat` / 폴스타 API(ES) | `node_load1/5/15`·`node_procs_running` |
| IO await/%util | (폴스타 API 제공 여부 실측) | `node_disk_*`(근사) |
| inode | (실측) | `node_filesystem_files_free` |
| 소켓·netstat | 폴스타 `server.Netstat` / 폴스타 API(ES) | `node_sockstat_*` |
| **top 프로세스·생존** | **폴스타 REST·ProcessMonitor** | (없음 — 폴스타 고유) |
| **과거 프로세스 추이** | **폴스타 API**(내부 ES 조회, §4.7) | (없음 — 폴스타 고유) |
| journalctl/dmesg 원문 | 폴스타 LogMonitor + ES(인덱싱 시) | `node_vmstat_oom_kill`(카운트만) |
| per-process FD/wchan/thread | (폴스타 제공 여부 실측) | host `node_filefd_*`만 → 채널3(신규) |
| baseline(E3) | `cmm_metric_stat_h` / 폴스타 API(ES) 이력 | node_exporter + Prometheus TSDB |

→ **기본(폴스타 DB·REST·ES)으로 ①②③ + 과거 프로세스 추이 + 로그(관제/인덱싱분) + baseline을 조회**하고, **USE 분해·host 카운터는 node_exporter로 폴백**(둘 다 신규 설치 0). ④ 로그 원문(ES 미인덱싱분)·per-process 정밀만 채널2(로그전송)·채널3(최후 신규)로 남는다. **폴스타 우선·node_exporter 폴백 원칙은 "벤더 검증 채널 재사용 > 별도 스택 의존"**(Plan 51 §9)에 정합.

### 4.6 LLM 활용 역할 — 장애현황·원인분석 (문헌 기반)

> 요구(2026-07-21): "장애현황·원인분석에 LLM을 활용하는 역할을 관련 문헌을 검토해 반영." 대원칙(D-035·§1.3-2): **판정은 결정적 Python, LLM은 사전수집 증거의 해석·서술·순위·인용만**. 파이프라인 단계별 LLM 역할·결정적 경계·문헌 근거:

| # | LLM 역할 | 입력(결정적 증거) | LLM 출력 | 결정적 경계(LLM 금지) | 문헌 |
|---|---|---|---|---|---|
| 1 | **장애현황 요약**(status/impact) | 타임라인·병목분류·프로세스·영향범위 | 운영자용 human-readable 요약·영향범위 서술 | 영향범위·타임라인 **값 산출** | **Oasis**(Jin, FSE 2023) |
| 2 | **원인 가설 생성·순위화** | 결정적 상관·선후 판정 | 원인 후보 서술·top-k 순위 | 상관·인과 **판정** | **RCACopilot**(EuroSys 2024) |
| 3 | **근거 인용·신뢰도** | 로그줄·메트릭·프로세스 단면 | 주장별 인용 + 신뢰도(high/med/low) | 신뢰도 임계·계층 상한 | **PACE-LM**·**ReAct** |
| 4 | **조치 권고 서술** | 위험도·신뢰도 분류(§8) | 조치 후보 서술(실행 아님) | 위험도 분류·**실행** | **Ahmed**(ICSE 2023) |
| 5 | **반증 유도** | 다중 가설 | 반증 관찰·쿼리 제안(확증편향 차단) | — | Google SRE(differential) |

- **2단계 파이프라인 엄수**(RCACopilot·§9.1): 결정적 증거수집 → LLM 서술. 수치·시그니처·병목·중요도는 **코드가 판정**(환각으로 뒤집힘 방지, D-035).
- **글래스박스·인용 의무**: 모든 LLM 주장에 로그줄/메트릭 인용(운영자 30초 검증) — ReAct 도구그라운딩(정답 시 환각<1%, Plan 51 §3.8).
- **상향 전용 연동**: LLM 서술이 발견한 심각도는 §5 `severity_judge`의 **상향 신호로만**(게이트 소급변경 없음, §5.1). 판정 뒤집기는 결정적 신호(§5.2)만.
- 산출물 소비: 역할 1·2는 §6 브리핑([중요도]·[요약]·[원인]), 역할 3은 [근거], 역할 4는 [권고], 역할 5는 동적 보강(Plan 50 ad-hoc 조회).

### 4.7 폴스타 API를 통한 ES 데이터 조회 (프로세스·실시간 사용률·로그) — 신규 채널

> 폴스타는 실시간/이력 데이터(프로세스·CPU/메모리 사용률 등)를 **Elasticsearch에 적재**하나, **에이전트는 ES를 직접 호출하지 않는다.** 현재 구현된 프로세스 API(`polestar_process_api.py` → `/rest/server/process/listByhostname`)와 **동일하게 폴스타 REST API를 통해 조회**하고, **폴스타가 내부적으로 ES를 쿼리해 JSON으로 반환**한다(사용자 확인, 2026-07-21). 이미 설치된 폴스타 데이터(옵션 A)·신규 설치 0이며, **기본 소스**(§4.5).

- **무엇을 얻나(잠정 — 벤더 실측 필요)**: ①② **CPU/메모리 실시간 사용률**(Util% 현재값 — `cmm_metric_stat` h/d/m 집계보다 촘촘하고 지연 없는 실시간; us/sy/wa **분해** 제공 여부는 실측, 분해는 node_exporter가 확실) · ③ **과거 프로세스 추이**(사건창 top 프로세스 시계열 — realtime 단면 한계 보완, Plan 51이 "폴스타 미보유→신규수집"으로 표시한 갭을 **신규 설치 없이** 해소) · `server.Netstat` 세션(Plan 51 §5.2 `realtime_info` 위치 미상 항목) · 폴스타가 제공하면 로그 원문(④).
- **소스 우선순위(2026-07-21 확정) — 기본 폴스타(API) · 폴백 node_exporter**: CPU/메모리 실시간 사용률은 세 소스가 겹치나 우선순위를 둔다 — **기본 = 폴스타 API(ES 백엔드)**(실시간 Util%·신규 설치 0; 부재 시 `cmm_metric_stat` 집계), **폴백 = node_exporter**(USE **분해** us/sy/wa/steal·host 카운터가 필요하거나 폴스타 미제공 시). 폴스타 API는 이미 설치된 폴스타에서 지연 없는 실시간 사용률을 주므로 **Prometheus 미배포 존에서도 ①② 실시간 판정이 가능**하고, node_exporter는 **분해가 필요할 때만 폴백**으로 보강한다.
- **조회 메커니즘(read-only) — 폴스타 REST API 경유, ES 직접 호출 아님**: 에이전트는 폴스타 REST 엔드포인트에 **read-only GET**(프로세스 API와 동일)하고, hostname·시간창 등은 **REST 쿼리 파라미터**로 전달한다. **ES `_search`/Query DSL·인덱스 매핑은 폴스타 내부 구현**이며 에이전트는 ES 프로토콜을 말하지 않는다(ES 자격증명·엔드포인트를 알 필요 없음). 기준 시각은 호출부가 사건 시각으로 산정(now() 금지, Plan 50 §3.4). hostname 키 규칙은 프로세스 API와 동일(Plan 47-1 §2 — serverName 아닌 hostname).
- **배선(기존 패턴 복제)**: 신규 `src/alarm/infrastructure/polestar_es_api.py`(또는 `polestar_process_api.py` 확장) — **폴스타 REST 클라이언트**로 `polestar_process_api.py`와 **동형**(httpx read-only GET · db_id→**폴스타 REST base_url** CSV로 존별 분리 — 기존 `process_api_base_urls_csv`류 재사용 · 비200/오류/타임아웃 → graceful None). **직접 ES 클라이언트가 아니다.** `prometheus_client.py`(폴백)와 병존, `evidence_collector`가 `asyncio.gather`로 편입.
- **실측 필요(벤더 표본조사 — 착수 전, "추정 금지" CLAUDE.md)**: ① **폴스타 REST 엔드포인트 경로**(프로세스 이력·실시간 사용률·로그 각각)·요청 파라미터(hostname·시간창) ② **응답 JSON 스키마**(필드명) ③ 프로세스/사용률 이력 보존 기간 ④ 로그 제공 여부·범위. **ES 인덱스·매핑은 폴스타 내부 관심사로 에이전트 무관** — 확정 전 인터페이스만 고정하고 응답 스키마는 표본 호출로 실측(프로세스 API의 `data.list` envelope이 인스턴스마다 다른 전례 유의, Plan 47-1).
- **보안(§7)**: 폴스타 REST API가 read-only 경계 — 프로세스 API처럼 내부망 http·**base_url 고정**(사용자 입력 아님→SSRF 불가)·비로그인. **ES 접근 허용목록·자격증명은 폴스타 측 책임**(에이전트는 폴스타 REST base_url만 보유). 프로세스 args·비밀 마스킹 후 LLM 주입(`mask_args`·`data_masker` 재사용)·전 조회 감사(decision_store).

### 4.8 이벤트 메시지 분석 기반 타깃 컨텍스트 보강 (Message-Driven Alert Enrichment) — 통보 강화 [D-105]

> **신규(2026-07-21)**. 사용자 요건: **노이즈 캔슬링의 실질 가치는 "통보 횟수 감소"만이 아니라, 살아남아 통보되는 알람을 이벤트 메시지 분석 기반으로 타깃 보강해 운영자가 즉시 판단할 정보(어느 프로세스가 원인인지 등)를 함께 전달하는 것**이다. 게이트가 노이즈를 **억제**(Plan 60)하면, 그 **생존 통보**를 Plan 64가 **필요한 정보만 골라 조회→첨부**한다. **억제(suppress)와 보강(enrich)은 노이즈 캔슬링의 두 축**이다.

**4.8.1 왜 "메시지 분석 기반 타깃팅"인가**: §4의 ①②③④ 전체를 매 통보에 다 돌리면 비용·지연이 크고 대부분 무관 정보다. 알람 메시지를 분석해 **필요한 수집 프로파일만 선택 실행**한다 — **LLM은 구조화·분류, 실행은 결정적**(StepFly식, §9.9). "CPU High"면 top CPU 프로세스, "Disk Full"이면 df/du, "Memory"면 top RSS·swap·OOM만 조회.

**4.8.2 메시지 → 조회 대상 결정 (결정적 우선 + LLM 보조)**:
- **1차(결정적)**: 기존 `classify_alarm_kind`(cpu|memory|disk|network|process|log, Plan 47-1) + `alarm_name`·`resource_type`로 **수집 프로파일**을 결정 매핑표로 확정. 표에 없으면 기본(①부하)만.
- **2차(LLM 보조)**: 구조화 필드로 kind가 불명한 **자유텍스트(`conditionLog`) 메시지형 알람에만**, `alarm_analyzer` LLM이 메시지를 분류해 프로파일 후보 제시 — **추가 수집·서술 전용**(오분류해도 결정적 프로파일 우선, 억제·판정 변경 없음). D-035: LLM은 "무엇을 볼지" 분류·요지 서술만, 수집·중요도 판정은 결정적.

| 알람 kind/메시지 | 조회 프로파일(결정적) | 소스 |
|---|---|---|
| CPU High | top -b CPU정렬 상위N · us/sy/wa · load | L3 top/vmstat + L1 `cmm_metric_stat`/폴스타 API(ES) 추이 |
| Memory/OOM | top RSS정렬 · free · swap si/so · dmesg OOM | L3 free/vmstat/dmesg + L1 |
| Disk Full/IO | df · du 상위 · iostat await/%util | L3 df/du/iostat + L1 |
| Process Down | 프로세스 생존 · systemctl status · 재시작 이력 | L1 폴스타 프로세스 API(단면·ES 이력) + L3 systemctl/journalctl |
| Log/보안 | `journalctl -p err` 시그니처 · 관제 로그 | L1 관제로그 + L3 journalctl(옵션) |

> 매핑표는 착수 시 실측 확정(하드코딩 아님 — CSV 오버라이드). 폴스타 REST 프로세스 API·ES(§4.7)·`cmm_metric_stat`·관제로그로 **L1만으로도 상당 부분 보강 가능**(L3는 프로세스 단위 정밀화·로그 원문에서 추가).
>
> **★ 구현 분담(실측) — L1 선구현은 Plan 60 §16**: **CPU/메모리의 프로세스 첨부는 Plan 47-1로 이미 통보까지 배선·기구현**(`enrich_processes`→`ProcessSnapshot`→`alarm_notifier._process_table_html`, `process_enrich_enabled`). 사용자 지시에 따라 **Plan 60 §16(E6)이 kind 확장(disk/log/process)·메시지 타깃팅·kind별 첨부 일반화를 L1으로 우선 구현**(블로커 없음). 본 §4.8은 그 위에 **L3 심화**(top 실시간·pidstat·us/sy/wa 분해·journalctl/dmesg 원문 — D-102·B-1 선행)를 얹는다.

**4.8.3 보강 흐름 (수집→조립→전달)**: 게이트 통보 결정 → (비차단) 메시지 분류 → 타깃 프로파일 수집(§4 컬렉터·§4.7 ES·§7 L3 재사용) → **결정적 요지 조립**(핵심 3~5줄: 원인 프로세스·부하·로그 시그니처) → §6 브리핑으로 통보에 첨부/후속 전달. **글래스박스·인용 의무 계승**(모든 항목 출처 인용 — 환각 방지, §1.3-4).

**4.8.4 트리거 범위 (PAGE 전면조사와 구분)**:
- **전면 조사**(§3~6, 무거움·느림): PAGE 사건만 — 중요도 2차 판정·RCA·인과추론.
- **타깃 보강**(§4.8, 가벼움·빠름): `enrichment_min_tier`(기본 PAGE, 운영 시 TICKET까지 확장 가능)로 **통보되는 알람에 핵심 컨텍스트만 첨부**. 재발생 dedup·클러스터 대표 상속(§3.4·§1.3-7) → 보강도 **대표 1건만**(폭주 방지). 둘 다 비차단·상향/서술 전용.

**4.8.5 안전·회귀**: 읽기전용(§7 허용목록)·조치는 권고만(§8)·**escalate-only**(보강이 억제를 되돌리지 않음)·옵트인 `message_enrichment_enabled`(기본 off)·LLM 분류 실패→결정적 프로파일 폴백(통보 자체 무영향)·회귀 0.

**설정**(신규): `message_enrichment_enabled: bool=False`, `enrichment_min_tier: str="PAGE"`, `enrichment_profile_map_csv: str=""`(kind→프로파일 오버라이드), `enrichment_timeout_seconds: float`(수집 예산). L3 프로파일은 §7 `l3_host_access_mode`·허용목록 재사용.

**결정·선행**: **D-105**(이벤트 메시지 분석 기반 타깃 컨텍스트 보강 — 결정적 프로파일 매핑 + LLM 분류 보조, 통보 강화). 착수 시 등재(실측 최댓값 D-104→D-105, 등재 직전 재확인). **구현 단계 분리 — L1 선구현 = Plan 60 §16**(CPU/메모리 프로세스 첨부는 Plan 47-1로 기구현 → kind 확장·메시지 타깃팅을 Plan 60이 블로커 없이 우선 구현), **본 §4.8 = L3 심화**(D-102·B-1(§7) 선행). 단일 결정·2단계. 문헌 근거 §9.9.

### 4.8.6 워크드 예제 — "메모리 사용률 90%" 알람 end-to-end (조회→분석→판단→전달·중복제거)

> **신규(2026-07-24)**. 사용자 예시("메모리 90% 알람 시 `ps`로 상위 메모리 프로세스 확인·`vmstat`로 메모리 사용량 확인 → 이 알람의 심각도·중요도·영향도 판단 → 추가정보 전달 또는 중복제거")를 §4.3(메모리 USE)·§4.4(top 프로세스)·§5(중요도 2차)·§6(브리핑)·Plan 60(§16 E6 L1·§14.4 역방향 훅·E1 dedup) 자산으로 꿴 **구체 실행 플로우**. 트리거 = 게이트가 `메모리 사용률 [90% (>90%)]`을 통보 결정(tier ≥ `enrichment_min_tier`)한 직후의 **post-gate 비차단 훅**(§4.8.3). 아래 5단계는 **동일 1회 수집을 세 판단(정보전달·심각도·중복제거)이 공유**한다.

**① 조회 (collect) — 어떤 명령어로** (전부 읽기전용·§7 허용목록·마스킹·타임아웃):

| 계층 | 명령어 / 호출 | 얻는 값 |
|---|---|---|
| **L1(우선·설치 0)** | 폴스타 프로세스 API `list_by_hostname(db_id, hostname)` → `select_top_processes(kind=memory)` | top **RSS** 프로세스 상위N(pid·명령·%MEM·마스킹) — **Plan 60 E6 기구현** |
| **L1** | `cmm_metric_stat_h` 메모리 시계열(`build_metric_series_sql`) → E3 Holt-Winters baseline | 90%의 **계절 정상 대비 이탈 배수·추이**(급상승/지속) |
| **L3(§7 보안게이트 통과 시)** | `free -h` · `cat /proc/meminfo` | **MemAvailable**(실가용)·SwapFree·Committed_AS·**Slab** |
| **L3** | `vmstat 1 3` | **si/so**(스왑 in/out — >0이면 스왑 발생=포화)·페이지 스캔 |
| **L3** | `ps aux --sort=-%mem \| head` · `pidstat -r 1` | top RSS pid·VmRSS·증가율(L1 단면 정밀화) |
| **L3** | `dmesg \| grep -i "out of memory"` · `journalctl -k` | **OOM Killer** 발생 pid·시각(고갈 **확정** 신호) |
| **L3(선택)** | `slabtop` · `cat /proc/slabinfo` | 커널 슬랩 누수(사용자 프로세스 아닌 고갈) |

- **핵심**: "어느 프로세스가 메모리를 많이 쓰는가"는 **L1 top RSS로 즉시 답이 나온다**(폴스타 API — `ps` 불요·설치 0). L3(`ps`/`vmstat`/`dmesg`)는 **스왑·OOM·슬랩·정밀 추이**를 더해 "포화인가 고갈인가, 추정인가 확정인가"를 가른다. L3 미가용(§7 보안결정 전)이면 L1만으로 진행(보수적).

**② 분석 (analyze) — 어떻게 해석** (결정적 규칙·LLM 아님·§4.3·§5.2):
- **고갈 vs 여유**: `MemAvailable` 낮음 = **실제 고갈**(MemFree 아님 — 회수가능 캐시 제외한 실가용).
- **포화 실증**: `vmstat si/so > 0` = 스왑 발생(메모리 압박 확증).
- **확정 신호**: `dmesg OOM Killed <pid>` = 고갈 **확정**(최상위 증거).
- **원인 귀속**: top RSS 프로세스 identity + VmRSS 추이. 슬랩 증가면 **커널 누수**(프로세스 아님).
- **추이·지속성**: baseline(E3) 대비 이탈 배수·연속 K구간 지속 여부 — 순간 스파이크 후 회복이면 자기복구.

**③ 판단 (judge) — 심각도·중요도·영향도** (escalate-only·§5):
- **심각도(severity)**: OOM 발생 → **강 상향**(§5.2), si/so 지속 → 중, 단발 스파이크 자기복구 → 상향 안 함. **`max()` 상향 전용**(폴스타 severity 하향 금지·E3 계약·게이트 소급 변경 없음).
- **중요도(importance)**: 폴스타 `IMPORTANCE_ID`(L1) + top 프로세스가 핵심 서비스인지 + **E4 토폴로지 하위 의존 서비스** 존재 여부.
- **영향도(impact)**: 스왑/OOM으로 **서비스 재시작·다운** 발생 여부(`journalctl` restart 카운트) → 사용자 체감 영향 등급.
- 전부 **결정적 신호가 판정**하고 LLM은 종합·신뢰도(high/medium/low)·근거 인용만(D-035). 증거 불충분·L3 부재 시 **상향 보류 + "증거 불충분" 명시**(오탐 상향 억제).

**④ 추가정보 전달 (deliver) — 무엇을 어떻게** (§6 글래스박스·인용 의무):
```
[중요도] 심각(신뢰도 high) — 게이트 PAGE + 조사 상향(OOM 확정)
[요약]  web-01(gp) 메모리 고갈 → java(pid 12345, RSS 6.2G/%MEM 78) OOM 종료
[근거]  free MemAvailable 210MB · vmstat si/so 0→1200 · dmesg "Killed 12345 (java)"
[영향]  java.service 3회 재시작 후 다운(journalctl)
[병목]  메모리(USE: 포화=si/so↑, 고갈=OOM) — CPU/IO 정상
[권고]  ①(승인 후)힙 상향 재기동 ②누수 점검(RSS 지속증가)  ※실행은 운영자 승인(§8)
[한계]  프로세스는 조사 시점 단면. 슬랩 분해는 L3 미수집.
```
모든 항목에 출처 인용 → 운영자 30초 검증(§6.1·Plan 51 §3.8).

**⑤ 중복제거 (dedup) — 측정 기반 상태변화 감지 (escalate-only·§14.4 역방향)**:

**Plan 60 E1 지문 dedup**은 "같은 `server·alarm_name·resource`"면 재통보를 억제한다(빠름·게이트 <10s, `compute_fingerprint`). 그러나 동일한 "메모리 90%" 재발이라도 **측정 증거가 물질적으로 악화**됐다면 단순 중복이 아니라 **상태변화**다 — 이를 escalate-only로 가른다:

- **상태지문 보존**: 보강이 얻은 `{top_rss_pid, oom_flag, swap_active, mem_available_bucket}`을 재발 메타(E1 `record_recurrence` / E7-a `annotation`, Plan 60 §17.3 하베스팅 패턴)에 보존한다.
- **재발 시 대조(escalate-only)**: 다음 동일-지문 재발이 억제될 때 상태지문을 대조 —
  - **동일·완화**(같은 top pid·OOM 없음·swap 여전 이하) → **진짜 중복 → 억제 유지**(재통보 0·소음 억제 지속).
  - **악화**(90%→OOM 발생, top pid 변경, swap 0→발생, MemAvailable 버킷 하락) → **상태변화 → escalate**(억제 예외·재통보/승격 + 갱신 브리핑).
- **불변 제약(§14.4 계승)**: post-gate·비차단 → 게이트 1차 dedup을 **소급 변경하지 않는다**. **escalate-only** — 중복을 *더* 억제하지 않고 **악화 시에만** 통과(완화·동일은 절대 재통보 안 함 = 재현율·소음억제 양립). L3 부재 시 **L1 top RSS pid 지문만으로** 보수 대조(≥1 신호 악화만 escalate). 가벼운 상태지문 대조는 §14.4 캐시 `gate:probe:{db_id}:{server}` 재사용(재수집 0).

> **세 판단의 공유**: **"어느 프로세스가 많이 쓰나"(정보전달)=L1 top RSS 즉시**, **"포화·고갈·확정인가"(심각도)=L3 vmstat/dmesg 정밀**, **"이 재발이 새 상황인가"(중복제거)=측정 상태지문 대조 escalate-only** — 셋 다 **동일 1회 수집·캐시를 공유**한다(중복 조회 0). ⑤는 §14.4(D-104 경계 probe)의 **dedup 확장**이며 Plan 60 §14.4·E1과 짝을 이룬다 — 착수는 **L3 보안결정(D-102·B-1) 선행**(미해소 시 L1 상태지문만으로 동작).

---

## 5. 중요도 2차 판정 (Importance/Severity Judgment) — `severity_judge`

> **이관(2026-07-24 · D-118)**: 구현은 sre-agent/02 §6(`sre_agent/` 패키지·결정적 후처리)이 담당 — §5.2 시그니처 표·escalate-only 계약 동일. collectorinfra는 poll 결과의 `verdict`(ImportanceVerdict)를 소비해 후속 통보 승격만 한다(§0.2 CW-C). 원격 배치에서 dmesg/journal 원문 시그니처는 Prometheus 카운터로 대체(sre-agent/02 §6)하되, Plan 60 §18 E8 채널이 `mcp_server` 도구로 노출되면 원문 시그니처도 가용해진다(sre-agent/04 §4.2 후보).

### 5.1 목적과 게이트 1차 판정과의 관계

게이트(Plan 60)는 **빠른 1차 중요도**(심각도·중요도·억제신호)를 판정한다. 본 노드는 조사로 수집한 **실제 OS 현황**을 근거로 **2차(정밀) 중요도**를 산출한다. 관계:

- **상향 전용(escalate-only)** — E3 `max()` 계약과 동일. 조사가 더 심각함을 발견하면 후속 통보를 **승격·에스컬레이션**하되, 게이트의 억제/판정을 **소급 변경하지 않는다**(재현율 우선·닭달걀 회피, Plan 60 §14.1).
- 게이트 판정이 이미 PAGE인 사건이 대상이므로, 2차 판정의 실익은 **강등이 아니라** ⓐ 에스컬레이션(PAGE→즉시호출/에스컬레이션 정책)과 ⓑ **브리핑에 담을 정밀 심각도·신뢰도**다.

### 5.2 결정적 상향 신호 (코드가 판정)

| 신호 | 판정 | 상향 강도 |
|---|---|---|
| OOM Killer 발생 | dmesg `Out of memory: Killed` | 강(고갈 확정) |
| FS read-only 리마운트 | dmesg `read-only filesystem` | 강(데이터 위험) |
| 핵심 프로세스 다운 + 재시작 루프 | systemd `start-limit-hit` | 강 |
| IO/CPU/Mem 포화 지속(연속 K구간) | USE 포화 + 지속성 | 중 |
| inode/FD 고갈 시그니처 | `No space left`·`Too many open files` | 중 |
| 단일 순간 스파이크(자기복구) | 포화 1구간 후 회복 | 하(상향 안 함) |

- LLM은 이 결정적 신호들을 **종합·서술**하고 신뢰도(high/medium/low)와 근거 인용을 붙인다 — **판정 자체는 결정적**(D-035, 환각으로 중요도 뒤집힘 방지).
- 데이터 부족·L3 부재 시 **보수적**(상향 보류, "증거 불충분" 명시) — 재현율보다 **오탐 상향 억제**(이미 PAGE라 과소평가 리스크는 게이트가 커버).

### 5.3 수용 기준

- 중요도는 **결정적 신호 기반**, LLM은 서술만(환각 0). L3 부재 시 상향 보류·한계 명시.
- 게이트 판정 소급 변경 없음(상향 전용). `severity_judge_enabled=False`면 브리핑에 게이트 1차 판정만 표기(회귀 0).

---

## 6. 운영자 브리핑 (Delivery) — `briefing_deliverer`

> **이관(2026-07-24 · D-118)**: 브리핑 생성·인용 검증은 sre-agent/02 §7이 담당(6요소 스키마 동일). collectorinfra는 `sre_get_investigation`이 반환하는 구조화 브리핑 JSON을 `alarm_notifier`/`notification_bus`로 전달·첨부한다(§0.2 CW-A — §6.2 채널 재사용·decision_store 감사는 유효).

### 6.1 구조화 브리핑 포맷 (glass-box)

```
[중요도] 심각(신뢰도 high) — 게이트 PAGE + 조사 상향(OOM 확정)
[요약]   web-01(gp) 메모리 고갈 → java(pid 12345) OOM 종료 → 서비스 3회 재시작 후 다운
[타임라인]
  14:03:10  Mem Util 78%→95% 급상승        ← metric_stat(L1)
  14:05:22  vmstat si/so 0→1200            ← L3 vmstat
  14:06:01  OOM: Killed process 12345 (java) ← L3 dmesg  [인용]
  14:06:03  systemd java.service restart #3 ← L3 journalctl [인용]
[병목]   메모리(USE: 포화=스왑발생 si/so↑, 고갈=OOM)  — CPU/IO 정상
[원인]   java(pid 12345) 힙 증가 추정(신뢰도 medium — 단면) / 확정: OOM killed(high)
[근거]   dmesg L.882, journalctl java.service, metric_stat Mem 14:00~14:06
[권고]   ① (승인 후) java.service 힙 상향 재기동  ② 메모리 누수 점검(추이=지속증가)
         ※ renice/kill은 후보 제시만 — 실행은 운영자 승인(§8)
[한계]   프로세스는 조사 시점 단면. swap 분해(slab)는 L3 미수집.
```

- **인용 의무**: 모든 주장에 로그줄/메트릭/프로세스 출처를 붙여 운영자가 **30초 내 검증**(Plan 51 §3.8).
- **한계 명시**: 단면·미수집 신호를 반드시 서술(추정을 확정으로 오도 금지).

### 6.2 전달 채널

- push: `alarm_notifier`(WorkB/webhook)·`notification_bus`(SSE) 재사용 — 게이트 PAGE 통보에 **브리핑 첨부** 또는 후속 메시지.
- (선택) 문서: Excel/Word 템플릿 채움(기존 document 파이프라인) — 사후 리포트용.
- 감사: 브리핑·근거를 `decision_store`에 기록(재현·감사, D-049 재사용).

### 6.3 수용 기준

- 브리핑은 타임라인·병목·원인·근거·권고·한계 6요소를 포함, 각 주장에 인용. LLM 서술이 결정적 증거와 모순되면 테스트로 검출(증거-서술 정합).
- `investigation_trigger_enabled=False`면 브리핑 미생성(게이트 통보 무변경, 회귀 0).

---

## 7. L3 실호스트 읽기전용 조사 + 보안통제 (D-102) [선행 보안결정]

> 사용자 확정: **L3 실호스트 명령까지 즉시 대상**. 단 L3는 **운영 호스트 접근**이라 D-003·보안정책 결정이 선행 게이트다(Plan 53 Wave-4 = 로드맵 최대 블로커). 본 §은 Plan 51 §5.3·§9의 통제를 **그대로 채택**한다.
>
> **보안결정 확정(2026-07-24 · Plan 60 §18·D-117)**: 노이즈 게이트 목적(통보 보강·측정 dedup·경계 상향)의 L3 사용에 대해 사용자 인터뷰로 **접근=A(폴스타 에이전트 확장·§7.1)·허용목록=§7.2 전체 USE·통제=최소권한 read-only·권고만**이 확정됐다. 따라서 **B-1·D-102는 이 방향으로 해소**되며, §7 수집기(`host_diagnostic_collector.py`·폴스타 에이전트 어댑터)는 **Plan 60 §18 E8이 게이트 배선으로 먼저 활성화**한다(게이트 목적·escalate-only·kind 스코프).
>
> **통합 갱신(2026-07-24 · D-118)**: "전면 조사" 담당이 `sre_agent/` 패키지로 위임됨에 따라, 조사(원격) L3 데이터 경로는 **sre-agent/06(Prometheus + 폴스타 MCP 2축·SSH 미채택)** 이 확정 경로다. §7.2 허용목록·통제 사양은 ①Plan 60 §18 E8 수집기(폴스타 에이전트 확장 — collectorinfra 측)와 ②`sre_agent` 로컬 배치 `vm_profile` bash의 공통 기준으로 유효하다. E8 채널을 `mcp_server` 고수준 도구(예: `polestar_host_snapshot`)로 노출하면 `sre_agent` 조사도 동일 채널로 호스트 스냅샷·로그 원문을 소비할 수 있다(sre-agent/04 §4.2 후보 — E8 착수 시 결정).

### 7.1 접근 방식 3옵션 (Plan 51 §5.3 — 보안결정 B-1)

| 옵션 | 방식 | 권고 |
|---|---|---|
| **A. 폴스타 에이전트 확장** | 폴스타가 이미 호스트에 둔 에이전트로 추가 스냅샷(dmesg/free/ss) 정의·노출 | **1순위** — 신규 접근경로 없음, 검증된 채널 |
| **C. 로그 전송 파이프라인** | 호스트 로그를 중앙 수집(rsyslog/Fluentd) 후 조회 | 2순위 — 과거 로그 포렌식, 호스트 직접접근 불필요 |
| **B. 신규 read-only 진단 수집기** | on-demand 허용목록 명령 실행(에이전트리스 SSH/경량 에이전트) | **최후수단** — §7.2 통제·사용자 승인 하에서만 |

> 원칙: **운영 호스트에 새 접근경로를 여는 것은 폴스타가 이미 가진 채널 재사용보다 항상 위험**. A·C 우선, B는 통제·승인 하 최후수단(Plan 51 §9).

### 7.2 보안통제 (옵션 B 도입 시 필수 — Plan 51 §9 채택)

- **허용목록(allowlist) 전용**: 사전정의 read-only 진단 명령만 — `uptime`·`top -b -n1`·`vmstat 1 3`·`mpstat -P ALL 1 1`·`pidstat 1 1`·`iostat -xz 1 2`·`free -m`·`df -h`·`df -i`·`ss -s`·`sar -n DEV 1 1`·`journalctl -p err --since`·`dmesg`(권한 시)·`cat /proc/{loadavg,meminfo}`·`systemctl show -p NRestarts <unit>`. 임의 명령·셸 메타문자·쓰기 차단(인자 정규식 검증).
- **변경(mutating) 절대 제외**: `renice`·`kill`·`dmesg -C/-c`·sysctl 쓰기·`oom_score_adj`/`memory.max` 쓰기·`systemctl restart/reset-failed`·`fsck` 등 **수집기에서 실행 불가**(조치는 §8 권고 경로로만, 운영자 실행).
- **최소권한 계정**: 진단 전용 비-root, 명령별 sudoers 한정, 호스트별 스코프.
- **자격증명 분리**: SSH 키/시크릿은 **MCP/수집기 측에만**(에이전트는 URL/핸들만 — 기존 D-003 분리 원칙 계승).
- **감사·마스킹**: 모든 L3 수집을 대상·명령·시각·결과크기로 감사 기록. 로그/프로세스 args 비밀정보 마스킹 후에만 LLM·저장. `/proc/PID/environ`·코어덤프 기본 미수집.
- **부하·안전 가드**: `nice -n19 ionice -c3` 래핑, 사건당 수집 횟수·동시성 상한, **모든 호스트 읽기 `timeout` 래핑**(D-state 태스크에서 수집기 블록 방지), `strace`/`ltrace` 프로덕션 금지(~173× 슬로다운), `/proc` 전수스캔 `-p PID` 스코프.
- **엔진/OS 스코프**: 1차 Linux(systemd/journald) 대상. 비-Linux·미지원 호스트는 L3 skip→L1 폴백(보수적).

### 7.3 수용 기준

- L3는 기본 비활성(`l3_host_collection_enabled=False`). 허용목록 외 명령·쓰기 명령·셸 메타문자 **차단 테스트** 고정.
- 수집 실패·미지원 호스트·타임아웃 → graceful degradation(L1만으로 브리핑, 한계 명시). 회귀 0(플래그 off 시 L3 경로 미진입).
- 전 L3 수집 감사 기록·마스킹 검증. **읽기전용 불변** — mutating 명령이 수집기에 존재하지 않음을 테스트로 고정.

---

## 8. 조치 권고 폐루프 거버넌스 (D-103) — `remediation_recommender`

> **통합 갱신(2026-07-24 · D-118)**: 권고 생성(`remediation_recommender`)은 sre-agent/02 §9가 담당(human-gated·실행 경로 미탑재를 테스트로 고정 — 동일 원칙). 본 §의 거버넌스(§8.3 자동 조치 선행 결정·B-3)는 collectorinfra 관할로 유지된다.

### 8.1 범위 (사용자 확정: 권고만·운영자 승인)

- 조사로 지목된 원인에 대해 **조치 후보(RemediationProposal)** 를 근거·신뢰도·위험도와 함께 제시. 예: `renice -n 10 -p 12345`(우선순위 하향), `kill -TERM 12345`(종료), `java.service 힙 상향 재기동`, `로그 로테이션/디스크 정리`.
- **실행은 운영자** — 시스템은 **제안까지만**. renice/kill 등 쓰기 조치를 **자동 실행하지 않는다**(D-003 유지, Plan 62 §5.2 "runbook 제안까지만").

### 8.2 위험기반 게이팅 (Plan 51 §3.8)

- 후보는 **위험도 분류**(저: renice·로그정리 / 중: 프로세스 kill / 고: 서비스 재기동·설정변경)와 **신뢰도**를 함께 노출. 고위험·저신뢰 후보는 "검토 필요"로만.
- 자동 실행(향후·거버넌스 후)은 **고신뢰 + 저위험 + 사람승인** 3중 조건에서만(단계적 자동화).

### 8.3 향후 자동 조치 — 선행 거버넌스 (본 계획 범위 밖·블로커)

Plan 62 §5.2·§12 B-3: 자동 실행은 **D-003 예외** 결정 없이는 착수 불가. 확정 필요 항목:
- **D-003 예외 범위**(어떤 조치를·어떤 조건에서 허용) · **승인 주체**(운영자/보안팀 이중승인) · **감사 요건**(누가·무엇을·언제·근거) · **롤백**(조치 실패 시 복구) · **blast radius**(영향 범위 상한·서킷브레이커).
- 문헌·벤더 폐루프 패턴(LogicMonitor/IBM/RedHat): 탐지→진단→조치→**검증** 폐루프, 초기 human-in-the-loop → 점진 자동화(§9).

### 8.4 수용 기준

- 조치는 **권고만** — 실행 코드 경로 없음(테스트로 고정). 후보는 위험도·신뢰도·근거 인용 포함.
- `remediation_recommender_enabled=False`면 브리핑에 권고 미첨부(회귀 0). 감사에 권고 기록(무엇을 제안했는지).

---

## 9. 학술·기술 근거 (문헌 조사 및 분석)

> 본 계획이 벤더 마케팅이 아니라 **연구 합의·검증된 엔지니어링**에 근거함을 확인. 항목별 핵심 근거와 계획 반영을 정리한다. **전체 조사 dossier(6영역·소스별 메커니즘·검증 상태·근거 강도)는 `docs/aiops_benchmark/incident_investigation_literature.md`** 참조(프로젝트 기검증 인용 = Plan 51 §3.8). 아래 수치·특징은 실제 인용 검증 완료(미검증 항목은 §9.7 명시).

### 9.1 자동 조사·진단 에이전트 (LLM-RCA)

- **RCACopilot — Chen et al., "Automatic Root Cause Analysis via LLMs for Cloud Incidents"(EuroSys 2024, dl.acm.org/doi/10.1145/3627703.3629553)**: **2단계 = 결정적 incident handler(결정트리)로 증거번들 조립 → LLM 카테고리 예측+설명**. handler 액션 3종(Scope Switching·Query Action·**Mitigation Action은 "재시작/팀소집" 제안만**), "only relevant data gathered"(과잉정보 억제). **Micro-F1 0.766·추론 4.2s**, MS 30+팀 4년+ 프로덕션. 본 계획 `evidence_collector(결정적)→severity_judge(결정적)→causal_reasoner(LLM)`와 **정확 일치**. → 반영: 수집·판정은 결정적, LLM은 분류·서술만. 과거 유사장애 시간감쇠 few-shot은 향후 옵션.
- **Ahmed et al., "Recommending Root-Cause and Mitigation Steps for Cloud Incidents using LLMs"(ICSE 2023, arXiv 2301.03797)**: 제목·설명만으로 원인+완화 추천, 40,000+ 인시던트·**OCE 70%+ "유용" 평가**. → 반영: 최소 입력에서도 LLM 브리핑이 가치. §8 조치 권고(제안까지·사람승인).
- **Roy et al., "Exploring LLM-based Agents for Root Cause Analysis"(FSE 2024 Industry, arXiv 2403.04123)**: thought→action→observation ReAct(≤20스텝), 도구에 **DB Query Tool**·**Human Interaction Tool**. **핵심 실측 — 환각률 ReAct 4~6% vs CoT 18% vs 순수검색 49%**(도구기반 조회가 환각을 결정적으로 억제). 권고: **도구 실패 사유를 에이전트에 노출(침묵실패 금지)·HITL 필수**. → 반영: L1/L3 read-only 도구조회로 브리핑 사실성 확보(§4·§6). Human Interaction Tool = renice/kill 인간승인 게이트 원형(§8). 도구 실패 구조화 노출 = 프로젝트 "침묵적 폴백 금지"와 동일. **단 20스텝 자율루프의 raw accuracy 한계** → 본 계획은 결정적 플레이북으로 스텝 고정(폐쇄망·소형모델 안전).
- **PACE-LM(arXiv 2309.05833)**: RCA **신뢰도 추정**(사람 게이팅 전제). → 반영: §5 중요도·§6 브리핑에 신뢰도(high/med/low) 명시.
- **ReAct — Yao et al.(ICLR 2023, arXiv 2210.03629)**: 추론+행동(도구) 시너지. 참고 수치(Plan 51 §3.8): **도구사용 정답 시 환각<1%(RAG 26% 대비)** → **도구 기반 조회가 환각을 크게 낮춘다**. → 반영: 본 계획은 "증거만으로 답하라 + 인용 의무"로 이 이점을 취한다.
- **HolmesGPT(Robusta+Microsoft 공동유지, 오픈소스, CNCF Sandbox 2025-10)**: 경보 발생 시 **30~50+ read-only 진단 toolset**(kubectl/Prometheus/Loki/DB 조회)을 agentic ReAct로 자동 실행해 근본원인을 조사·write-back하는 SRE 에이전트. **"By design read-only, respects RBAC, safe in production."** 결정적으로, **조사(read-only)와 조치는 분리된 옵트인 모듈** — 신규 Operator Mode가 Remediation toolset(write)을 별도 추가한다. Ollama 로컬 지원하나 **소형모델 tool-calling 불안정(실측 문서화)**. → 반영: 본 계획의 **L3 read-only 조사(§7)와 renice/kill 권고·인간승인(§8) 분리가 HolmesGPT 구조와 정확 일치**. DB read-only를 "read-only 진단명령"으로 확장하는 것도 HolmesGPT 선례. 소형 로컬 LLM에 조사 루프 주도를 위임하지 않는 본 계획 고정 파이프라인(§1.3-5)이 실측으로 정당화됨.
- **Oasis — Jin et al., "Assess and Summarize: Improve Outage Understanding with LLMs"(ESEC/FSE 2023 Industry, arXiv 2305.18084)**: MS 사내 프로토타입 배포. LLM이 **장애 영향범위 자동 평가 + 사람이 읽을 요약** 생성(fine-tuned GPT-3.x). 온콜의 수작업 장애현황 정리를 자동화. → 반영: **§4.6 역할 1(장애현황 요약)** 의 직접 근거. LLM은 결정적으로 수집·정렬된 타임라인·병목·영향범위를 **서술로 압축**만 하고, 영향범위 값 자체는 결정적 산출(환각 차단). §6 브리핑 [요약]·[중요도] 라인과 정합.

### 9.2 트리아지 방법론 (형식화)

- **USE 방법론 — Brendan Gregg**: 자원별 **Utilization·Saturation·Errors** 점검. → §4.2 병목 식별의 결정적 골격(CPU us/sy/wa·mem swap·IO await).
- **"Linux Performance Analysis in 60,000 ms" — Netflix(Gregg 등)**: `uptime→dmesg→vmstat→mpstat→pidstat→iostat→free→sar→top` 10단계 신속 트리아지. → §4 조사 명령 표준 목록(Plan 51 §3.2 채택).
- **Four Golden Signals / RED — Google SRE**: 지연·트래픽·에러·포화. → 중요도·서비스 관점 보조(Plan 51 §3.3).
- **Google SRE Book — "Effective Troubleshooting"·"Managing Incidents"**: 가설→검증→분할정복, 증상보다 원인 우선. → §5 중요도(원인 노드 우선)·§4.3 격리.

### 9.3 조사→조치 폐루프·안전 (연구 합의)

- **읽기전용 스키마/툴 제약 + 재시도 상한**: 스키마 환각·정체 루프 차단(Plan 51 §3.8). → §7 L3 허용목록·타임아웃, §3.2 오케스트레이션 타임아웃.
- **순위 top-k + 신뢰도 + 위험기반 게이팅**: 제안은 사람 검증 전제 노출, 자동조치는 고신뢰+사람승인. → §8 위험기반 게이팅·human-in-the-loop.
- **폐루프 패턴(LogicMonitor/IBM/RedHat 등 벤더)**: 탐지→진단→조치→검증, 초기 승인기반 → 점진 자동화(Plan 62 §5.2). → §8.3 향후 자동화 로드맵(거버넌스 선행).

### 9.4 산업 시스템 — 자동 조사·조치 게이팅

| 시스템 | 자동 수집·조사 | 조치 게이팅 | 폐쇄망 | 본 계획 반영 |
|---|---|---|---|---|
| **Grafana Sift** | **고정 결정적 check 세트**(에러로그·OOMKill·과부하호스트·최근배포·리소스경합) → "interesting results" | 진단전용(조치 없음) | 셀프호스트 | **최적 모델** — LLM 자율루프 아닌 결정적 check로 증거 큐레이션. ①~④를 check 세트로 코드화(§4)와 동형 |
| **Cleric AI** | 알람시 다중소스 자동·다중가설 병렬검증 | **strictly read-only, 모든 조치 인간승인** | "no data leaves system" | renice/kill 인간승인 게이트(§8)의 산업 레퍼런스 |
| **PagerDuty AIOps** | 호출 **전** 사전 enrichment(로그·메트릭·runbook·과거인시던트 첨부) | 진단과 조치 분리 | SaaS | "PAGE 판정 시 자동조사→브리핑"(§3.2·§6) 워크플로 위치와 동일 |
| **Datadog Bits AI SRE** | 자율 가설→텔레메트리 수집→추론 | 제안+원클릭 | **클라우드 SaaS·에어갭 불가** | 메커니즘만 참고, 폐쇄망 직접 이식 불가 |
| **FLASH**(MS 2024)·**Nissist**(arXiv 2402.17531) | 상태감독+힌드사이트로 재발 자동화 / TSG 완화 코파일럿 | — | — | 결정적 상태감독으로 LLM 워크플로 신뢰성 보강(§11) |

> **결론**: 폐쇄망·"결정적 우선" collectorinfra엔 **Grafana Sift(결정적 check) + Cleric(read-only·조치 인간승인)** 조합이 문헌상 최적 대응 모델이다. 본 계획 §3(고정 파이프라인)·§4(결정적 플레이북)·§7(read-only)·§8(권고만)이 이를 따른다.

### 9.5 폐쇄망 실현성

- **Kim et al., "LLM-based AIOps via Log Prioritization in Air-Gapped Systems"(EuroMLSys '26, 2026)**: **규칙기반 변환→구조화 이벤트→시간집계→결정적 우선순위화**로 이벤트 51% 감축·**LLM 토큰 43% 절감** 후 로컬 LLM 진단. → 반영: §6 브리핑 전 **결정적 증거 압축·선별**로 토큰 절감(로컬 워커 LLM 예산 보호). *(단 본문 PDF는 ACM 403 미확인 — 수치는 초록 스니펫 기반, 본문 인용 전 재확인, §9.7.)*
- **로컬모델 한계**: 오픈웨이트 1B~8B는 오프라인 구동되나 **tool-calling 신뢰성 부족(HolmesGPT 실측)** → **에이전트 루프는 결정적 코드가 주도, 로컬 LLM은 요약·분류·심각도 서술 단발추론만**(§1.3-5·§5.2 정당화).

### 9.6 설계 테이크아웃 (문헌 → 계획 매핑)

1. 조사 플레이북을 **결정적 조사 그래프**로 코드화(RCACopilot handler+Sift check, ①~④를 USE×SRE로 정형화) → §3·§4.
2. **read-only 기본 + 조치 분리 게이트**(HolmesGPT Operator Mode·Cleric) → §7·§8.
3. **결정적 severity 매트릭스가 SEV 결정, LLM은 서술만** → §5.
4. **증거 결정적 압축 후 로컬 LLM 투입**(에어갭 로그우선화) → §6.
5. **도구 실패 사유 구조화 노출**(Roy·프로젝트 원칙) → §3.2·§7.3.
6. **HITL를 1급 도구로**(Roy Human Interaction Tool) → §8.

### 9.7 근거 강도·검증 한계

- **잘 지지됨(peer-reviewed/프로덕션 실측)**: RCACopilot 2단계·Micro-F1 0.766·MS 4년+(EuroSys 2024) · Roy 환각 4~6% vs 검색 49%(FSE 2024) · Ahmed OCE 70%+(ICSE 2023) · USE/60초/SRE·ReAct(ICLR 2023) · HolmesGPT read-only·CNCF·소형모델 tool-calling 불안정(오픈소스 검증가능).
- **벤더 마케팅·불확실(미인용)**: Datadog "90% faster"·Cleric "<2분"·PagerDuty 문구 — **메커니즘만 채택, 효과 수치 미인용**. Datadog/Cleric은 크로스-테넌트 데이터 의존 → 폐쇄망 직접 이식 불가.
- **검증 실패/부분**: (a) 에어갭 로그우선화 본문 PDF ACM 403 미확인(수치=스니펫). (b) Roy는 arxiv PDF 파싱 실패했으나 ar5iv HTML로 전량 검증. 그 외 전 시스템 실제 인용 검증 완료 — **날조 없음**.
- **도메인 차이 유의**: 문헌 다수가 마이크로서비스/K8s·트레이스 전제 → 트레이스 없는 collectorinfra엔 **의존성 그래프(Plan 60 E4)+메트릭/로그 상관**으로 치환. 산업 알람 문헌 일부는 공정제어 맥락(신호 특성 차이).

### 9.8 아키텍처 긴장 (D-102 근거)

문헌 대다수는 조사 데이터가 **관측 스택 내부**(로그·트레이스·APM)에 있다고 전제한다. collectorinfra의 ①~④ 플레이북(top/journalctl/renice)은 **모니터링 DB가 아니라 대상 호스트의 라이브 OS 상태**를 요구한다 → 조사 소스가 **두 갈래**(과거 메트릭=DB read-only 쿼리 / 현재 OS 상태=**호스트 read-only 명령**). 후자는 D-003 "DB read-only" 범위를 넘어 **read-only 실행 계층**을 새로 정의해야 하며(HolmesGPT read-only toolset 확장이 선례), 그 존재·경계는 **명시적 의사결정 대상** → 본 계획 **D-102·§7**(L3 진단 수집기·보안통제)로 반영, **B-1**(보안정책)을 착수 게이트로 확정.

### 9.9 메시지 기반 타깃 보강·도구증강 트리아지 (§4.8 근거)

§9.1의 **RCACopilot**(2단계 — 결정적 수집→LLM 예측)·**HolmesGPT**(read-only tool로 컨텍스트 조회)가 "알람→자동 컨텍스트 수집→운영자 제시"의 백본이다. 아래는 **메시지 분석 기반 타깃팅·도구증강 보강**을 직접 뒷받침한다:

- **StepFly (Mao et al., Microsoft, arXiv 2510.10074, 2025)** — **LLM이 오프라인에서 트러블슈팅 가이드를 DAG로 구조화**하고 **온라인 실행은 결정적 스케줄러**(병렬). GPT-4.1 ~94% 성공·실행시간 32.9~70.4%↓. → **§4.8의 "LLM=무엇을 볼지 분류/구조화, 수집 실행=결정적"(D-035)의 직접 근거** — 자유텍스트 지식(가이드/메시지)을 결정적 실행계획으로 변환하는 정확한 모델.
- **CORTEX (Wei et al., arXiv 2510.00311, 2025)** — 멀티에이전트가 **외부 시스템을 도구로 조회해 증거 수집→감사가능 판정**(behavior/evidence/reasoning 역할 분리). → 보강의 **증거 기반·글래스박스 인용**(§6·§1.3-4) 근거. 도메인=SOC 보안(인프라 적용 시 신호 차이 유의).
- **LLM-IRAgent (JRPS 2025)** — 정책구동 LLM 에이전트가 SOC 플레이북을 **triage·enrichment·containment 권고**의 구조화 추론으로 변환. → 보강 + 조치권고(§8) 결합의 산업 근거.
- **Autonomous Alert Triage w/ Tool-Augmented Reasoning (IJSRCSEIT 2026)** — 표준 도구 통합(MCP식)으로 **다소스 동적 조회·실시간 증거 수집**. → 보강의 다채널(§4.5 Prometheus·§4.7 ES·§7 L3) 동적 조회 정합.
- **산업 기술자료**: Datadog *Actionable Alerting* · Elastic *investigation guides*(알람에 임베드된 컨텍스트 플레이북) · Rootly(noise→actionable). → "알람에 조사 가이드·컨텍스트를 붙여 actionable하게"가 업계 표준 방향(효과 수치 미인용 — 벤더 발표).

**검증 상태**: StepFly·CORTEX는 arXiv 초록·방법 확인(2025). LLM-IRAgent·IJSRCSEIT·산업 자료는 검색 스니펫 기준(본문 독립검증 아님). **도메인 다수가 SOC 보안** → 인프라 알람(CPU/메모리/디스크) 적용 시 신호·도구 차이 유의. 근거표: `docs/aiops_benchmark/incident_investigation_literature.md`(보강 문헌 추가).

---

## 10. 구현 순서 (Wave) 및 의존성

> **재편(2026-07-24 · D-118)**: 아래 Wave A~D는 `sre_agent/` 위임 전 원문이다. 실행 순서는 **§0.2 CW-A~CW-C(collectorinfra 측) + sre-agent README 권장 착수 순서(Plan 04 M-B → 02 W-A + 06 R-A/R-B → 05 → 02 W-B/W-C)** 를 따른다.

```
[선행] Plan 60 §14 트리거 훅 + Plan 50 diagnosis_graph(증거수집·상관·인과)·Plan 51 L1 수집
   │
[W-A] 오케스트레이션 MVP (L1만·read-only) ─ 게이트 PAGE→investigation_graph→중요도 2차→브리핑
   │   · severity_judge(L1 신호)·briefing_deliverer·조사 dedup/동시성 가드   (블로커 없음)
   │
[W-B] L3 실호스트 조사 (보안결정 후) ─ evidence_collector에 host_snapshot 소스 추가
   │   · 옵션 A/B/C 결정(B-1)·허용목록·자격증명·감사·마스킹                    (B-1 선행)
   │
[W-C] 조치 권고 ─ remediation_recommender(위험기반 게이팅·권고만)             (B-2 선행)
   │
[W-D] (향후·거버넌스) 승인 기반 자동 조치→검증 폐루프                          (B-3 = D-003 예외)
```

| Wave | 내용 | 의존성 | 비용 | 선행 블로커 |
|---|---|---|---|---|
| **A** | 오케스트레이션 MVP(L1) + 중요도 2차 + 브리핑 | Plan 50·51 L1, Plan 60 §14 | M | 없음 |
| **B** | L3 실호스트 read-only 조사 | W-A | M~L | **B-1(L3 보안정책)** |
| **C** | 조치 권고(human-gated) | W-A | S~M | **B-2(권고 거버넌스)** |
| **D** | 자동 조치→검증(향후) | W-B·C·Plan 50 RCA | L~XL | **B-3(D-003 예외)** |

> 권고: **W-A(L1 오케스트레이션·브리핑) 즉시 → W-B(L3, 보안결정 후)·W-C(조치권고) 병렬 → W-D(자동화)는 거버넌스 확정 후**. 사용자가 L3 즉시 대상으로 확정했으므로 **B-1(L3 보안정책)을 착수 게이트로 최우선 확정**한다.

---

## 11. 회귀·리스크 통제

- **전 항목 옵트인·기본 off**(`investigation_trigger_enabled`·`l3_host_collection_enabled`·`severity_judge_enabled`·`remediation_recommender_enabled`) → 비활성 시 게이트(Plan 60)·기존 경로 완전 무변경(회귀 0).
- **비차단 트리거** — 조사는 게이트 응답과 분리된 태스크. 조사 실패·타임아웃이 게이트 판정·통보에 영향 없음(§3.2).
- **읽기전용 불변(D-003)** — L3는 허용목록 read-only, mutating 명령이 수집기에 **부재**(테스트 고정). 조치는 권고만(실행 코드 경로 없음).
- **상향 전용 중요도** — 게이트 판정 소급 변경 없음(E3 `max()` 계약 계승).
- **폭주 방지** — 조사 dedup·동시성 상한·전체 타임아웃·in-memory 키 sweep(Known Mistakes 2026-06-29).
- **글래스박스·한계 명시** — 추정을 확정으로 오도 금지(증거-서술 정합 테스트).
- **비밀 마스킹** — 로그/args/environ 비밀정보 마스킹 후에만 LLM·저장(§7.2).
- **폐쇄망** — 고정 파이프라인·워커 LLM·외부 SaaS 비의존.

---

## 12. 선행 블로커·결정 (사용자·보안팀 확인 필요)

> **재편(2026-07-24 · D-118)**: B-1은 게이트 목적 한정 D-117로 해소, 조사(원격)는 sre-agent/06으로 종결. B-2·B-3은 유지 — §0.3 참조.

| 블로커 | 내용 | 선택지·권고 |
|---|---|---|
| **B-1 (L3)** | L3 호스트 접근 보안정책 (Plan 53 Wave-4·본 §7) | (A) 폴스타 에이전트 확장〔권고 1순위〕 / (C) 로그 전송 / (B) 허용목록 read-only SSH 수집기〔통제·승인 하 최후수단〕. **사용자가 L3 즉시 대상으로 확정 → B-1을 착수 게이트로 우선 결정.** |
| **B-2 (조치)** | 조치 권고 거버넌스 (§8) | **권고: 권고만·운영자 승인**(D-003 유지·사용자 확정). 위험도·신뢰도 노출 기준 합의. |
| **B-3 (자동화)** | 향후 자동 조치의 D-003 예외 | **거버넌스 최대 블로커** — D-003 예외 범위·승인주체·감사·롤백·blast radius를 사용자·보안팀과 확정(Plan 62 §5.2·§12). 미확정 시 W-D 착수 금지. |
| **B-4 (범위)** | Wave 순서·즉시 착수 범위 | §10 Wave A→B/C 승인. |

> CLAUDE.md 의사결정 규칙: L3·조치는 D-003과 충돌하므로 방향 확정 전 구현 착수 금지. §12 합의 후 D-101~103을 `docs/02_decision.md`에 등재하며 번호 재확인.

---

## 13. 테스트 계획 (요약)

| 항목 | 단위 테스트 | 통합·회귀 |
|---|---|---|
| 트리거 | 게이트 PAGE→비동기 emit·비차단(게이트 지연 무영향)·페이로드 정합 | E1 dedup 억제분 미트리거·E2 대표만 트리거·조사 dedup/동시성 가드·전체 타임아웃 |
| 조사(L1) | incident_scoper 범위·evidence_collector 부분실패 graceful | `investigation_trigger_enabled=False`→미발화(회귀 0) |
| 조사(L3) | **허용목록 외·쓰기 명령·셸 메타문자 차단**·mutating 명령 부재·timeout 래핑·마스킹 | `l3_host_collection_enabled=False`→L3 미진입·L1 폴백·미지원 호스트 skip |
| 병목(USE) | CPU us/sy/wa·swap si/so·IO await 결정적 분류·드릴다운 분기·L3 부재 시 한계 명시 | L1만 vs L1+L3 병목 판정 |
| 중요도 | OOM·FS RO·재시작루프 상향 신호·스파이크 비상향·데이터부족 보수 | **상향 전용(게이트 소급변경 없음)**·`max()` 하향 불가 |
| 브리핑 | 6요소 포함·주장별 인용·증거-서술 정합(모순 검출) | 채널 전달·decision_store 감사 기록 |
| 조치 | 위험도·신뢰도·근거 포함·**실행 코드 경로 부재** | `remediation_recommender_enabled=False`→권고 미첨부·감사에 권고 기록 |
| 공통 | `arch_check --ci` 계층 위반 0·읽기전용 검증 | 전 플래그 off→기존 경로 비트동일(`test_plan64_flags_off_regression.py` 신설) |

---

## 14. 산출물·문서 갱신

- **신규 모듈**: `src/alarm/orchestration/investigation_graph.py`(Plan 50 diagnosis_graph 확장), `src/alarm/application/nodes/{severity_judge,briefing_deliverer,remediation_recommender}.py`, 인프라 `src/alarm/infrastructure/host_diagnostic_collector.py`(L3 read-only 허용목록 수집기·옵션 A/B/C 어댑터).
- **기존 코드 갱신**: `notification_gate.py`(PAGE 시 비동기 트리거 emit — Plan 60 §14 배선), `alarm_worker.py`/`alarm_server`(push 조사 태스크 실행·dedup/동시성 가드), `alarm_notifier.py`·`notification_bus.py`(브리핑 전달 — 재사용), `decision_store.py`(조사·조치권고 감사 — 재사용), `config.py`(신규 플래그: `investigation_trigger_enabled`·`investigation_trigger_min_tier`·`investigation_timeout_seconds`·`investigation_dedup_ttl_seconds`·`investigation_max_concurrent`·`l3_host_collection_enabled`·`l3_host_access_mode`(A/B/C)·`severity_judge_enabled`·`remediation_recommender_enabled` — 전부 기본 off/보수값).
- **신규 테스트**: `tests/test_alarm/test_plan64_flags_off_regression.py` + 항목별(§13).
- **문서**: 착수 시 `docs/02_decision.md`에 **D-101~103 등재**(번호 재확인), `docs/17_future_improvements.md` 관련 FI 갱신, **Plan 62 §5.2 상태 갱신**(예약→작성 완료), Plan 50/51/53/60 상호참조 추가. 문헌 dossier `docs/aiops_benchmark/incident_investigation_literature.md`(작성 완료 — §9 근거).

---

## 15. 변경 이력

| 날짜 | 변경 | 사유 |
|---|---|---|
| 2026-07-24 | **§0 통합 재편 신설 — 조사 실행의 `sre_agent/` 위임 (D-118)** | SREAgent 통합(D-118·`plans/sre-agent/` 이관)에 따라 본 계획의 조사 실행 본체를 sre-agent/02(HolmesGPT ReAct + 결정적 후처리)로 위임. §3 investigation_graph 자체 구현 **대체**·§5(severity_judge)/§6(브리핑)/§8(권고 생성) 생성부 **이관**·§7 L3 **이원화**(게이트 목적=Plan 60 §18 E8 폴스타 에이전트 확장 / 조사 원격=sre-agent/06 Prometheus+폴스타 MCP 2축)·§10~§14 **재편**(CW-A 게이트 훅→`sre_investigate_alarm` submit/poll·브리핑 통보 첨부, CW-B pull `sre_diagnose` 위임, CW-C escalate-only 후속 승격). 예약 D-101~103 재편(§0.3 — ux_improvement 점유로 재부여 대상이었음). 섹션별 상태 매핑 §0.1. E8 채널의 `mcp_server` 도구 노출 후보(`polestar_host_snapshot`) 기록. |
| 2026-07-24 | **§4.8.6 워크드 예제 신설 — "메모리 90%" end-to-end(조회→분석→판단→전달·중복제거)** | 사용자 예시("메모리 90% 알람 시 `ps`로 상위 메모리 프로세스·`vmstat`로 사용량 확인 → 심각도·중요도·영향도 판단 → 추가정보 전달 또는 중복제거를 구체 계획으로"). §4.3(메모리 USE)·§4.4(top 프로세스)·§5(severity_judge)·§6(브리핑)·Plan 60(§16 E6 L1·§14.4 역방향·E1 dedup) 자산을 **5단계 구체 플로우로 통합**: ①조회(L1 폴스타 top RSS[ps 불요]·E3 baseline / L3 `free`·`/proc/meminfo`·`vmstat si/so`·`ps aux --sort=-%mem`·`pidstat -r`·`dmesg OOM`·`slabtop`) ②분석(MemAvailable 고갈·si/so 포화·OOM 확정·슬랩 누수, 결정적) ③판단(심각도=escalate-only·OOM 강상향/중요도=IMPORTANCE_ID+E4/영향도=서비스 재시작) ④전달(§6 브리핑 인용의무) ⑤**중복제거=측정 기반 상태변화 감지**(상태지문 `{top_rss_pid,oom_flag,swap_active,mem_available_bucket}` 보존→재발 대조: 동일·완화→억제 유지 / 악화→escalate, **§14.4 dedup 확장·escalate-only·게이트 소급 변경 없음**). **세 판단이 동일 1회 수집·캐시 공유**. 경계: `ps`/`vmstat` 직접 실행은 L3(D-102·B-1 선행), L1 top RSS·E1 dedup·§14.4 훅은 Plan 60. Plan 60 §16.4에 상호참조 추가. |
| 2026-07-21 | Plan 64 최초 작성 | 사용자 요구("이벤트 발생 시 OS 현황(top/uptime·병목·격리·로그) 자동 조사→중요도 판단→운영자 브리핑, 장애 대응 자동화")를 Plan 62 §5.2 예약 슬롯으로 구체화. Plan 50(진단 파이프라인)·51(수집·기법·보안)·60 §14(게이트 트리거) 재사용 위에 **오케스트레이션·중요도 2차 판정·브리핑·조치권고** 계층만 신설. 사용자 확정 반영: 산출물=Plan 60 훅+Plan 64 / 조사 범위=L3 즉시 대상(→§7 보안결정 B-1 선행) / 조치=권고만·운영자 승인(D-003 유지). D-101~103 부여(등재 전 번호 재확인). 문헌 근거(§9): RCACopilot 2단계(Micro-F1 0.766)·Roy 환각 4~6%vs검색49%·HolmesGPT read-only/조치분리·Grafana Sift 결정적 check·Cleric read-only+인간승인·에어갭 로그우선화(토큰43%↓)·USE/Netflix 60초/SRE. 전체 조사 dossier를 `docs/aiops_benchmark/incident_investigation_literature.md`로 저장(6영역·검증상태·근거강도)하고 §9에 통합. 아키텍처 긴장(호스트 라이브 상태=read-only 실행계층 필요)을 §9.8·D-102 근거로 반영. |
| 2026-07-21 | **§4.7 폴스타 Elasticsearch API 연동 신설** | 사용자 정보("폴스타 ES API로 프로세스 정보 등 조회 가능")를 반영. ES를 DBHub(SQL)·Prometheus(PromQL)에 이은 **세 번째 read-only 조회 채널**로 편입 — **이미 설치된 폴스타 데이터(옵션 A·신규 설치 0)**. **§4.7 신설**: `_search`+Query DSL(@timestamp range·aggs 추이·top_hits) 메커니즘, `polestar_es_client.py`(DBHub/prometheus_client 패턴 복제), read-only 허용목록(`_search`류만·인덱싱/`_bulk`/delete 금지), 벤더 실측 필요항목(엔드포인트·인덱스·스키마·보존기간·로그 인덱싱 여부). **§2 재사용 자산·§4.3(과거 프로세스 추이)·§4.4(로그 원문 조건부)·§4.5 커버리지** 갱신 — **Plan 51이 "폴스타 미보유→신규수집"으로 표시한 과거 프로세스 추이 갭을 신규 설치 없이 해소**, 채널3(신규 설치) 잔여가 per-process 정밀 하나로 축소. ES `_search`는 POST이나 쿼리라 D-003 정합(읽기 허용목록 강제). 벤더 스키마는 추정 금지·표본 실측(CLAUDE.md). **(보완)** ES 조회 대상에 **CPU/메모리 실시간 사용률**(Util% 현재값 — cmm_metric_stat h/d/m 집계 지연 없음) 명시 추가(§2·§4.1·§4.2·§4.5·§4.7). 소스 관계 명확화: 실시간 사용률은 cmm_metric_stat(집계·지연)/폴스타 ES(실시간·신규설치0)/node_exporter(실시간+분해·Prometheus 필요) 3중 중복 — **ES로 Prometheus 미배포 존에서도 ①② 실시간 판정 가능**, node_exporter는 us/sy/wa 분해 필요 시만 보강. |
| 2026-07-21 | **소스 우선순위 확정: 기본 폴스타/ES · 폴백 node_exporter** | 사용자 지시("기본은 폴스타·ES 기준으로 작성, node_exporter는 폴백"). §4.1·§4.2·§4.5·§4.7의 소스 우선순위를 **기본=폴스타(DB·REST·ES)·폴백=node_exporter**로 재정렬 — 폴스타는 벤더 검증 채널이자 프로세스·로그·실시간 사용률 통합 제공(1순위 조회), node_exporter는 USE **분해**(us/sy/wa/steal)·host 카운터 등 폴스타 미제공 신호에만 폴백. §4.5 표를 **기본/폴백 2열**로 재구성. "벤더 검증 채널 재사용 > 별도 스택 의존"(Plan 51 §9) 정합. mechanism doc §4A도 동기 갱신. |
| 2026-07-21 | **정정: 폴스타 ES 직접 호출 아님 — 폴스타 API 경유** | 사용자 정정("ES를 직접 호출하지 않고 현재 구현된 것처럼 폴스타 API를 통해 ES 조회"). §4.7을 **폴스타 REST API 경유**(폴스타가 내부에서 ES 쿼리, 에이전트는 read-only GET — `polestar_process_api.py` 패턴)로 재작성. **폐기**: 직접 ES `_search`+Query DSL·`_search`류 허용목록·ES base_url/자격증명(모두 폴스타 내부 책임). **배선 변경**: `polestar_es_client.py`(ES 클라이언트) → **`polestar_es_api.py`(폴스타 REST 클라이언트)**, base_url은 폴스타 REST(`process_api_base_urls_csv`류 재사용). **실측 대상 변경**: ES 인덱스/매핑 → **폴스타 REST 엔드포인트 경로·응답 JSON 스키마**(ES 매핑은 폴스타 내부·에이전트 무관). §2·§4.3·§4.4·§4.5·mechanism §4A.4 동기 정정. |
| 2026-07-21 | **§4 명령어 종합 카탈로그 + §4.6 LLM 역할(문헌) 추가** | 사용자 요구("①부하~④로그 예시 외 vmstat/free 등 관련 명령어를 모두 명시, 원인 검토·분석 명령어 확인 + 장애현황·원인분석 LLM 역할을 문헌 검토해 반영"). **§4.1~4.4**: 각 트리아지 단계의 L3 명령을 예시→**전체 명령어**로 확장(부하: uptime/w/loadavg/vmstat/top; USE: 자원별 vmstat·mpstat·sar·pidstat·free·meminfo·slabtop·iostat·iotop·df -h/-i·lsof·ss·nstat·/proc·limits; 격리: ps/pidstat/pstree·/proc/PID status·wchan·fd; 로그: journalctl 4변형·dmesg·systemctl NRestarts·/var/log·coredumpctl). **§4.5 신설**: L3 명령→설치 기준(node_exporter+폴스타) 커버리지 매핑 — ①②③은 신규설치 0으로 대체, ④·per-process만 채널2/3(mechanism doc §4A 연계). **§4.6 신설**: LLM 활용 역할 5종(장애현황요약·가설순위·인용신뢰도·조치서술·반증)을 결정적 경계·문헌과 매핑. **§9.1**: Oasis(Assess and Summarize, FSE 2023, arXiv 2305.18084) 추가 — §4.6 역할1(장애현황 요약) 근거. |
| 2026-07-21 | **§4.8 메시지 분석 기반 타깃 컨텍스트 보강 신설 (통보 강화 · D-105)** | 사용자 요건("노이즈 캔슬링은 통보 횟수 감소만이 아니라, 이벤트 메시지를 분석해 필요 정보를 직접 조회→운영자에게 추가 정보 전달이 중요"). **억제(Plan 60)+보강(Plan 64)을 노이즈 캔슬링의 두 축**으로 정식화. **§4.8 신설**: 메시지→조회 프로파일 결정 매핑(1차 결정적 `classify_alarm_kind`+매핑표 / 2차 LLM은 자유텍스트 분류·서술 전용, D-035), kind별 프로파일(CPU→top/us·sy·wa, Mem→RSS·swap·OOM, Disk→df/du/iostat, Process→생존·systemctl, Log→journalctl), 보강 흐름(비차단 수집→결정적 요지 조립→§6 브리핑 첨부·인용), 트리거 범위 구분(전면조사=PAGE만 / 타깃보강=`enrichment_min_tier` 기본 PAGE·TICKET 확장가능·대표 상속), escalate-only·옵트인 `message_enrichment_enabled`(off·회귀0). **§1.2 요건 매핑에 (보강) 행 추가**. **§9.9 신설**: StepFly(MS, arXiv 2510.10074 — LLM 구조화+결정적 실행, 94%·시간33~70%↓, D-035 직접근거)·CORTEX(arXiv 2510.00311 — 멀티에이전트 도구조회 증거·감사가능)·LLM-IRAgent·Tool-Augmented Triage·Datadog/Elastic investigation guides. **D-105 부여**(실측 최댓값 D-104→105, 등재 직전 재확인)·L3 프로파일은 D-102·B-1 선행/L1은 즉시. dossier에 보강 문헌 추가. |
| 2026-07-21 | **ES 라벨 표기 정정 (폴스타 API 경유 일관화)** | 사용자 리뷰 지시. 2026-07-21 "폴스타 ES 직접호출 아님·폴스타 REST API 경유" 정정이 **소스 라벨까지 안 내려간 잔여**를 정리. §4.2(소스 우선순위)·§4.5 커버리지 표·§4.8 매핑표의 맨-"ES"/"폴스타 ES" 라벨을 **`폴스타 API(ES)`** 로 통일("ES 제공 시 ES"→"폴스타 API 제공 시 그 값"). §4.5 표 내부 불일치(L199만 "폴스타 API(내부 ES)"였고 나머지 행이 맨-"ES") 해소. **의미 불변·직접-ES 오해 제거**(에이전트는 폴스타 REST만 조회) — 본문 실질 정정은 기존 반영, 본 건은 라벨 표면 정리. |
