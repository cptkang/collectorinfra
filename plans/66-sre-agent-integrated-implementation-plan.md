# 66. SRE-Agent 통합 구현 계획 — sre-agent 01~06 × Plan 60~65 종합 실행 시퀀스

> 작성일: 2026-07-24
> **성격**: 실행 계획(execution sequencing) — sre-agent 계획 6종(`plans/sre-agent/01~06`)과 Plan 60~65의 **잔여 구현 항목을 하나의 의존성 기반 단계 시퀀스로 통합**한다. 개별 계획의 설계를 대체하지 않으며(상세는 각 계획 참조), 역량 우산은 Plan 62가 유지한다(본 계획은 그 아래 "무엇을 어떤 순서로 만들 것인가"의 단일 장부).
> **대상 계획**: `plans/sre-agent/` 01(대체)·02(중심)·03(대체)·04·05·06 + Plan 60(E7·E8·§14 잔여)·61(잔여)·62(우산)·63(완료·후속)·64(§0 재편 CW)·65(미구현).
> **관련 결정**: D-120(HolmesGPT 개발·테스트 LLM=Gemini API — 운영 LLM 결정과 분리), D-119(PromQL `mcp_server` 통합 — 관측 읽기 접근 경계 일원화), D-118(SREAgent 통합·`sre_agent/` 독립 패키지), D-117(E8 L3 게이트 편입·폴스타 에이전트 확장), D-116(E7 텍스트·주석 신호), D-106~D-114(Plan 60 E1~E6·STL·임베딩 — 구현 완료), D-035(결정적=판단/LLM=보조), D-003(읽기전용).
> **신규 결정**: 본 계획 자체는 신규 D-번호를 부여하지 않는다 — 각 단계 착수 시 해당 하위 계획의 예약분을 **collectorinfra 채번 규칙**(`## D-` 헤더+「변경 이력」 grep 최댓값+1, 현재 D-120)으로 등재한다(§6). ※ D-119(PromQL `mcp_server` 통합 — R5 반영)·D-120(Gemini 테스트 LLM — R16 반영)은 2026-07-27 사용자 채택으로 기등재.
> **상태**: 로드맵(실행 순서 확정 문서). 착수 게이트는 §7의 사용자·행정 확인 항목.

---

## 1. 현황 집계 — 구현 완료 vs 잔여 (2026-07-24 실측)

### 1.1 구현 완료 (재구현 금지 — 조합 대상 자산)

| 자산 | 계획·결정 | 비고 |
|---|---|---|
| 노이즈 게이트 코어(4-티어·dedup·매트릭스·신호수집·감사) | Plan 52 · D-048/D-049 | 운영 기준선 |
| E1 재발생 관측성·E4 다홉 토폴로지·E6 통보 보강(L1) | Plan 60 Wave A · D-106~108 | `tests/test_alarm/` 그린 |
| E2 크로스-호스트 상관(+메타 감사·위상 가중)·E3 동적 baseline(+STL) | Plan 60 Wave B · D-109/110/112/113 | STL은 statsmodels 반입 전 기본 off |
| E5 변경 상관 1차 | Plan 60 Wave C · D-111 | lifecycle_history 기반 |
| B-7 로컬 임베딩 주석(L-2/L-4, e5-small 확정) | Plan 60 · D-114 | 운영 활성화는 반입 행정 선행 |
| Text-to-SQL 트랙 A(다중후보)·B(동의어+시딩)·C(시맨틱 조합) | Plan 61 · D-072~076/084 | 전 플래그 기본 OFF·153+ 테스트 |
| 폴스타 과적합 분리(어댑터·overfit_check·generic_mon 하네스) | Plan 63 · D-088~091 | 완료 — EX 라이브 재측정만 잔여 |
| 자체 MCP 서버(`mcp_server/` — execute_sql·validate_readonly·DBPoolManager) | plans/15 | sre-agent/04의 확장 기반 |
| MCP 클라이언트 패턴(`src/dbhub/client.py` — SSE·재연결·타임아웃) | 기존 | CW-A 소비 코드의 원형 |
| SREAgent 초기 구현(diagnosis.py·toolset_profiles.py·settings·arch_check) | 구 SREAgent 저장소 | `sre_agent/` 착수 시 이관(D-118) |

### 1.2 잔여 구현 (본 계획의 시퀀싱 대상)

| # | 항목 | 원 계획 | 상태 |
|---|---|---|---|
| R1 | 목업 이벤트 생성기(대화형 메뉴·TCP 주입·판정 대조) | Plan 65 §3~§6 (sre-agent/03 대체 흡수) | 미구현 · D-115 예약 |
| R2 | E7 텍스트·주석 신호(a 하베스팅·b 비알람 분류·c 파서 견고성·d 사이트 상관) | Plan 60 §17 | 미구현 · D-116 등재·B-9 확정 — **착수 가능** |
| R3 | `mcp_server` 조사용 고수준 도구 8종 + 프로세스 프록시(마스킹) + 도메인 deny | sre-agent/04 M-A/M-B | 미구현 |
| R4 | `sre_agent/` 패키지 골격 + 조사 코어(HolmesGPT·pull) + 브리핑·후처리 | sre-agent/02 W-A, 05 §5 | 미구현 |
| R5 | 원격 프로파일(`remote_vm_profile`) + **`mcp_server` PromQL 도구(D-119 — hostname 앵커 고수준 기본·원시 옵트인)** + 로컬 픽스처 검증·품질 게이트 | sre-agent/06 R-A/R-B·04 §4.4 | 미구현 · **D-119 등재** |
| R6 | 조사 서비스 MCP 노출(submit/poll·잡 저장소·경계 테스트) | sre-agent/05 §3~§5 | 미구현 |
| R7 | HolmesGPT `mcp_servers` 연동 + dispatcher·push·severity_judge | sre-agent/04 M-C, 02 W-B | 미구현 |
| R8 | collectorinfra 소비 배선: 게이트 훅→submit/poll·브리핑 첨부·pull 위임·escalate 승격 | Plan 64 §0.2 CW-A~C, Plan 60 §14 | 미구현 |
| R9 | `invest-trigger` 시나리오(목업→훅→submit 확인, e2e 옵트인) | Plan 65 §4.3 | 미구현 (R1·R8 선행) |
| R10 | 원격 합류(폴스타 MCP·hostname 규약 실측) + remediation_recommender | sre-agent/06 R-C/R-D, 02 W-C | 미구현 |
| R11 | E8 L3 게이트 배선(폴스타 에이전트 채널·경계 probe kind 확장·post-gate 요지 첨부·측정 dedup) | Plan 60 §18 | 미구현 · D-117 등재·보안 확정 |
| R12 | (후보) `polestar_host_snapshot` 도구 노출 — E8 채널의 조사 개방 | sre-agent/04 §4.2 후보 | E8(R11) 후 결정 |
| R13 | 전송 인증(Bearer) + 실 폴스타 DB 런타임 검증(PG·DB2) | sre-agent/04 M-D | 미구현 · 15번 검증 부채 승계 |
| R14 | Text-to-SQL 잔여(경로 C 값 인덱스·커버리지 확장·실 DB EX 측정·프로필 예시 정비) | Plan 61 §12.3-7/8 | 실 DB 접속 환경 필요 |
| R15 | Plan 63 후속(EX 라이브 재측정·라우팅 어휘 단일 출처화 별도 계획) | Plan 63 §1.3·상태 | 데이터 적재 시 |
| R16 | **HolmesGPT Gemini 테스트 경로**: `AgentSettings` LLM 필드 + 스모크 하네스 `smoke_llm.py`(litellm tool-calling 왕복·`DiagnosisAgent` ask 1회) + 데이터 통제(목업·픽스처만) | sre-agent/02 §10.1 | 미구현 · **D-120 등재** |
| — | 자동 조치 실행(폐루프 뒤 절반) | Plan 64 §8.3 B-3 | **착수 금지 유지**(D-003 예외 거버넌스 미확정) |

### 1.3 구현하지 않는 것 (대체 확인)

- sre-agent/01(자체 게이트)·03(자체 목업): **대체됨** — 기존 게이트(Plan 52/60)·Plan 65가 담당.
- Plan 64 §3 `investigation_graph`·§5 severity_judge·§6 briefing_deliverer·§8 remediation_recommender의 **collectorinfra 자체 구현**: 대체됨(D-118) — `sre_agent/` 패키지(sre-agent/02)가 구현, collectorinfra는 MCP로 소비(Plan 64 §0).
- sre-agent/04 `polestar_noise_signals` 집약 도구·§7.1 게이트 결정적 클라이언트: 폐기 — 게이트는 기존 자체 신호 수집(`polestar_noise_context.py`) 유지.

## 2. 통합 목표 아키텍처 (완성 시점 그림)

```
폴스타 알람 ─TCP→ alarm_server → Redis Stream → AlarmWorker
                                    │
                          노이즈 게이트 (기구현 E1~E6 + R2 E7 + R11 E8 probe/요지첨부)
                                    │ PAGE (대표 사건만 — 노이즈 상속)
                     ┌──────────────┴ [R8 CW-A: MCP 클라이언트, 비차단]
                     ▼
        sre_agent 조사 서비스 (R4·R6 — 별도 venv·프로세스, 포트 9098)
        sre_investigate_alarm / sre_get_investigation / sre_diagnose  (contract_version 1)
                     │ dispatcher (R7 — fingerprint dedup TTL·동시 상한·전체 타임아웃 300s·시간당 예산)
                     ▼
        HolmesGPT 조사 코어 (R4·R7 — holmesgpt ≥0.36.0 · 구 SREAgent diagnosis.py 이관)
        - DiagnosisAgent(ToolCallingLLM) ReAct 루프: LLM은 증거 수집·서술만(D-035)
        - LLM 엔드포인트: 개발·테스트=Gemini API(D-120·R16, litellm 경유·목업 데이터만) /
          운영=§7-1 확정 대상(유능한 tool-calling 모델 전제 — 운영 활성화 게이트)
        - toolset 프로파일: remote_vm_profile()(R5 — bash 미확장·SSH 없음·내장 prometheus toolset 비활성)
        - Config.mcp_servers 등록 → RemoteMCPToolset이 mcp_server 도구 자동 발견(list_tools,
          health_check_tool=list_sources) + llm_instructions 지침 단일 주입(소스 선택·교차 검증 — D-119)
                     │ ReAct 도구 호출⇄결과(JSON·인용 근거) — MCP(SSE)·Bearer(R13), 하향 의존 단일화(D-119)
                     ▼
   mcp_server — 관측 데이터 읽기 접근 경계 (R3 폴스타 고수준 도구 8종
    + R5 PromQL 도구 + R12 host_snapshot 후보)
          ▼ SELECT/GET 읽기전용        ▼ HTTP(PromQL)
   폴스타 PG(gp/yd)·DB2(b0)·REST   Prometheus (node_exporter —
                                    nodename 서버측 조립·규약 실측 R10)

        (조사 완주 후) LLMResult(result, tool_calls) → 결정적 후처리 (R7·2-D)
        - severity_judge: 도구 원시 출력 시그니처 매칭 → verdict(escalate-only)
        - briefing_builder: 6요소 스키마·인용 검증 / remediation_recommender: 권고만(실행 경로 없음)
                     │ 브리핑 JSON (poll)
                     ▼
        alarm_notifier 통보 첨부 + escalate-only 승격 (R8 CW-C) + decision_store 감사

검증 주입: Plan 65 목업 생성기 (R1) — 시나리오 번호키 → 게이트 판정 대조 + invest-trigger (R9)
챗 pull: deepagents fault_diagnosis 의도 → sre_diagnose 위임 (R8 CW-B)
```

**불변 원칙(전 단계 공통)**: 결정적=판단/LLM=보조(D-035) · 읽기전용·조치는 권고만(D-003) · 전 신규 기능 옵트인 기본 off·플래그 off 시 비트동일 · 억제≠삭제(전 판정 감사) · 패키지 경계(collectorinfra↔`sre_agent` 양방향 import 0, 통신은 MCP 계약뿐) · `arch_check --ci` 0.

## 3. 통합 구현 단계 (Phase 0~5)

각 Phase는 독립 배포 가능하며, Phase 내 병렬 표기(∥)는 동시 진행 가능. 규모는 S/M/L.

### Phase 0 — 선행·행정 (개발과 병렬 진행, 코드 아님)

| # | 항목 | 근거 | 성격 |
|---|---|---|---|
| P0-1 | statsmodels·sentence-transformers·e5-small 폐쇄망 반입·보안 협의 | Plan 60 D-113/114 (기구현 기능의 운영 활성화 조건) | 행정 — `docs/plan60_embedding_import_security_review.md` 기작성 |
| P0-2 | **holmesgpt 스택 반입·실행 환경 확정**(Python ≥3.13 venv·의존성 트리·LLM 엔드포인트) | sre-agent/02 D-001 인용 — **§7-1 사용자 확인 필수**(폐쇄망 원칙과의 정합) | 행정+결정 |
| P0-3 | Prometheus 서버 실 위치·인증·보존기간 실측 | sre-agent/06 §9 (R-B는 로컬 픽스처로 선행 가능) | 인프라 실측 |
| P0-4 | 폴스타 에이전트 확장(read-only 스냅샷 노출) 벤더·운영 협의 | Plan 60 §18.1 (E8 R11의 채널) | 벤더 협의 |

### Phase 1 — 게이트 잔여 고도화 + 검증 도구 (블로커 없음 · 즉시 착수 가능)

| Wave | 내용 | 원 계획 | 규모 |
|---|---|---|---|
| **1-A** ∥ | **R1 목업 생성기**: `scripts/mock_polestar_events.py`(카탈로그·빌더·TcpSender/Redis 폴백·판정기·메뉴 루프) — §4.1 기본 6종 + §4.2 Plan 60 5종. **D-115 등재** | Plan 65 §3~§6 | M |
| **1-B** ∥ | **R2 E7**: E7-a 주석 하베스팅(B-9 코로보레이션 게이팅) → E7-b 비알람 분류 → E7-c 파서 견고성·사이트 토큰 → E7-d 사이트 상관·chattering 정합. 신규 domain `annotation_signal.py` 등(§17.9 산출물) | Plan 60 §17 | M |
| 1-C | E7 × 목업 교차 검증: S3(주석 재발신)·S8(비알람·이질 포맷)·S5(사이트) 시나리오로 E7 수용 기준(§17.9) 대조 | 65 §4 × 60 §17.9 | S |

> 1-A와 1-B는 상호 독립이나, 1-A를 먼저 끝내면 1-B의 수동 검증이 "번호 입력"으로 줄어든다 — **1-A 선행 권장**.

### Phase 2 — SRE-Agent 기반 구축 (sre-agent README 권장 순서)

| Wave | 내용 | 원 계획 | 규모 |
|---|---|---|---|
| **2-0** ∥ | **R16**: Gemini 테스트 LLM 경로(D-120) — `AgentSettings.investigation_llm_model`·`gemini_api_key`(SecretStr) + 스모크 하네스 `sre_agent/scripts/smoke_llm.py`(①litellm 단독 tool-calling 왕복 실측 ②`DiagnosisAgent` ask 1회 — mock MCP 픽스처). litellm 모델 문자열·env 규약은 **holmesgpt 반입(P0-2) 직후 실측 확정**. 데이터 통제: 외부 송신은 목업·픽스처만(테스트 config 운영 connection 미설정 — 물리 차단) | sre-agent/02 §10.1 | S |
| **2-A** | **R3**: `mcp_server` M-A(로컬 PG 픽스처 기동·기존 도구 회귀 확인) → M-B(고수준 도구 8종 — alarm_history·metric_trend·resource_status·topology·process_snapshot[args 마스킹]·os_config·change_history·condition_log + 도메인 deny + SQL 자체 LIMIT/FETCH FIRST) | sre-agent/04 §4·§9 | M |
| **2-B** ∥ | **R4**: `sre_agent/` 패키지 골격(pyproject·venv ≥3.13·arch_check 동반 이관·구 SREAgent 코드 이관) + 조사 코어 W-A(폴스타 toolset 소비·pull `ask` 경로·브리핑 형식) | sre-agent/02 W-A·05 §5 | M~L |
| **2-B′** ∥ | **R5**: `remote_vm_profile()`·`DiagnosisAgent.mcp_servers` 확장·`AgentSettings` 확장(R-A — D-119: Prometheus 접속 설정은 mcp_server 측) + **(D-119) `mcp_server` PromQL 도구 구현**(hostname 앵커 고수준 기본·원시 옵트인·서버측 `{nodename=…}` 조립·감사 일원화 — 04 §4.4) + Docker Prometheus 픽스처(**06 §8.1** — `testdata/prometheus/` compose: prometheus·node_exporter 9101 재배치·mock_exporter 결정적 단언·nodename=PG 픽스처 server_name 정렬) MCP 경유 e2e·**품질 게이트**(내장 toolset 대비 열화 없음 실측 — 열화 시 A안 복귀)(R-B — `PrerequisiteCacheMode.DISABLED`) | sre-agent/06 R-A/R-B·04 §4.4 | M |
| **2-C** | **R6**: FastMCP 조사 서비스(포트 9098) — `sre_investigate_alarm`/`sre_get_investigation`/`sre_diagnose`/`sre_list_investigations`/`sre_health`, 잡 저장소(in-memory+감사 JSONL·sweep·재기동 시 running→failed), 페이로드 계약 검증(`contract_version: "1"`), **경계 테스트**(collectorinfra 모듈 import 0·엔트리 `run_service` 유일) + **R7 전반부**: `Config.mcp_servers` 등록(M-C)·`llm_instructions` 실 런타임 반영 실측 | sre-agent/05 §3~§8·04 M-C | M |
| 2-D | **R7 후반부**: dispatcher(fingerprint dedup TTL·동시 상한·전체 타임아웃 300s·시간당 예산·토큰 비용 감사) + push 소비 + severity_judge(도구 원시 출력 시그니처 매칭·escalate-only) + briefing_builder(6요소·인용 검증) | sre-agent/02 W-B | M |

### Phase 3 — collectorinfra 연동 (Plan 64 §0.2 CW)

| Wave | 내용 | 원 계획 | 규모 |
|---|---|---|---|
| **3-A** | **R8 CW-A**: `notification_gate` PAGE 시 비차단 emit → `sre_agent` MCP 클라이언트(`src/alarm/infrastructure/`, DBHubClient 패턴) submit → poll → 브리핑 `alarm_notifier` 첨부 + `decision_store` 감사. `investigation_trigger_enabled`(기본 off). **트리거 배선 결정 등재**(구 D-101 대체분 — 채번 규칙) | Plan 64 §0.2·Plan 60 §14 | S~M |
| 3-B ∥ | **R8 CW-B**: deepagents `fault_diagnosis` 의도 → `sre_diagnose` 위임 · **CW-C**: `verdict.escalate` 시 escalate-only 후속 통보 승격 | Plan 64 §0.2 | S |
| 3-C | **R9**: Plan 65 §4.3 `invest-trigger` 시나리오(메뉴 [12]) — submit 응답·`duplicate` dedup 확인, 실 HolmesGPT 완주는 `RUN_E2E=1` | Plan 65 §4.3 | S |
| 3-D | **R13**: MCP 전송 인증(Bearer — mcp_server·조사 서비스 양쪽) + **실 폴스타 DB 런타임 검증(PG·DB2 각 1회 이상)** — plans/15 승계 검증 부채, mock 통과를 완료로 치지 않는다 | sre-agent/04 M-D | S~M |

> **Phase 3 완료 = 최소 완결 가치(MVP)**: "PAGE 1건 → 자동 조사 1회 → 인용 있는 브리핑이 통보에 첨부" 흐름이 목업으로 재현 가능.

### Phase 4 — 원격·L3 확장

| Wave | 내용 | 원 계획 | 규모 |
|---|---|---|---|
| 4-A | **R10**: 실 Prometheus 연동·hostname 정합 규약 실측 확정(R-D — nodename 라벨 표준화·D-119 서버측 조립의 전제 라벨 실측, 착수 시 실측 후 D-등재) + remediation_recommender(권고만·실행 경로 부재 테스트 고정) — 02 W-C 완결 | sre-agent/06 R-C/R-D·02 W-C | M |
| 4-B | **R11 E8**: 폴스타 에이전트 스냅샷 채널 어댑터(`host_diagnostic_collector.py` — collectorinfra 게이트 자산) + 게이트 동기 경계 probe(kind별 USE·≤2s·캐시) + post-gate 결정적 요지 첨부(sre_agent 미가용 시 폴백) + 측정 기반 dedup 상태지문 | Plan 60 §18 | M~L (P0-4 선행) |
| 4-C | **R12 결정**: E8 채널의 `polestar_host_snapshot` 고수준 도구 노출 여부·마스킹 정책 — 노출 시 원격 조사에서 dmesg/journal 원문 시그니처 가용(sre-agent/02 §6 Prometheus 카운터 대체를 폴백으로 강등) | sre-agent/04 §4.2 후보 | S |

### Phase 5 — 독립 트랙 (어느 Phase와도 병렬 가능)

| 항목 | 내용 | 조건 |
|---|---|---|
| **R14** Plan 61 잔여 | (a) 경로 C 값 인덱스 프롬프트 주입 (b) 커버리지 확장(HAVING·동적 날짜·상위 N·LOB) (c) 실 DB에서 트랙 A EX 이득·후보수 곡선 측정 + 프로필 `query_examples` `platform.server%` 술어 실측 정비 | 실 DB 접속 환경 |
| **R15** Plan 63 후속 | EX 라이브 재측정(3경로) · 라우팅·인스턴스 어휘 단일 출처화(§1.3 — **별도 신규 계획으로 분리**, 회귀 성격이 다름) | 데이터 적재 시 |
| P0-1 활성화 | statsmodels(STL)·e5-small(B-7) 반입 완료 시 플래그 on·운영 실데이터로 임계(0.87) 재검 | 행정 완료 시 |

### 착수 금지 (블로커 유지)

- **자동 조치 실행**(폐루프 뒤 절반): D-003 예외 거버넌스(승인 주체·감사·롤백·blast radius) 확정 전 금지 — Plan 64 §8.3 B-3. `sre_agent`에도 실행 코드 경로를 만들지 않으며 테스트로 고정(sre-agent/02 §9).

## 4. 의존성 그래프 (요약)

```
P0-2 holmesgpt 환경 ──────────────┐
Phase 1  [1-A 목업 ∥ 1-B E7] → 1-C │        (게이트 트랙 — sre-agent와 독립)
                                  ▼
Phase 2  2-0 Gemini 스모크(D-120 — P0-2 직후) ∥ 2-A mcp_server 도구 → 2-B 조사코어 ∥ 2-B′ 원격프로파일+PromQL도구(D-119) → 2-C 서비스+연동 → 2-D dispatcher/judge
                                  ▼
Phase 3  3-A CW-A 훅 배선 → 3-B CW-B/C ∥ 3-C invest-trigger(1-A 필요) → 3-D 인증+실DB 검증  ★MVP
                                  ▼
Phase 4  4-A 원격 실연동(P0-3) ∥ 4-B E8(P0-4) → 4-C host_snapshot 결정
Phase 5  R14(실DB) ∥ R15 ∥ P0-1 활성화     (전 구간 병렬 가능)
```

## 5. 회귀·품질 게이트 (전 단계 공통)

1. 단계 완료마다: 전체 pytest 무회귀(현 기준선 `tests/test_alarm/` 713 passed 등) + `python scripts/arch_check.py --ci` exit 0 + (공용 계층 변경 시) `scripts/overfit_check.py --ci`.
2. 신규 플래그는 전부 기본 off — flags-off 비트동일 테스트(`test_plan60_flags_off_regression.py` 계열)에 섹션 추가.
3. `sre_agent/`·`mcp_server` 변경 시: 경계 테스트(양방향 import 0)·`sre_agent/scripts/arch_check.py` 자체 게이트.
4. e2e(실 LLM·실 DB·playwright)는 `RUN_E2E=1` 옵트인 — 기본 스위트에 편입 금지.
5. 결정 등재·계획 갱신: 각 Wave 완료 시 `docs/02_decision.md` 등재(채번 규칙) + 해당 계획 변경 이력 갱신(본 계획 §8에도 반영).

## 6. 결정 채번 계획 (착수 시 등재 대기 목록)

| 시점 | 등재 대상 | 원 예약(인용) |
|---|---|---|
| 1-A | 목업 주입 경로(TCP 기본+Redis 폴백) | Plan 65 **D-115(예약 유지)** |
| 2-A~2-D | 고수준 도구 노출 정책 / 조사 루프 위임+결정적 후처리 경계 / mcp_servers 등록 / 권고 human-gated | sre-agent D-014·D-009·D-010·D-011 (SREAgent 체계 인용 — collectorinfra 번호로 재부여) |
| 2-C | submit/poll 비동기 잡 계약 / 배치 구성(별도 venv·프로세스) | sre-agent D-017·D-018 |
| 3-A | 게이트 훅 트리거 배선(구 Plan 64 D-101 대체분) | Plan 64 §0.3 |
| 3-D | MCP 전송 인증 | sre-agent D-015 |
| 4-A | 원격 VM 프로파일·hostname 정합 규약 | sre-agent D-020 |
| 4-B | E8 구현 확정분(D-117 상태 갱신) · 4-C `polestar_host_snapshot` 노출 여부 | Plan 60 §18 / sre-agent/04 §4.2 |

> 등재 직전 `## D-` 헤더·「변경 이력」 표 전체 grep으로 최댓값 재확인(현재 D-119) — Known Mistakes 원칙.

## 7. 착수 전 사용자 확인 필요 (CLAUDE.md 의사결정 규칙)

| # | 질문 | 배경·긴장 |
|---|---|---|
| **7-1** | **`sre_agent` 조사 LLM의 실행 환경**: sre-agent/02는 유능한 tool-calling 모델(`anthropic/claude-sonnet-5` API) 전제인데, collectorinfra 전 계획은 **폐쇄망·워커 LLM(FabriX/KBGenAIChat) 우선**이다. 운영 환경에서 ①외부 API 허용 ②사내 게이트웨이 경유 ③로컬 모델 중 무엇인가? (HolmesGPT 위임 설계의 성립 조건 — tool-calling 불안정 모델이면 원 Plan 64의 고정 파이프라인 근거가 부활한다) **갱신(2026-07-27 · D-120)**: 개발·테스트는 Gemini API로 선행 가능(외부 송신은 목업·픽스처만 — 2-0) — 본 게이트의 실체는 **운영 LLM 확정(운영 활성화 전 필요)**으로 축소되어 Phase 2 개발 착수를 차단하지 않으며, 2-0 스모크·조사 완주 실측이 이 결정의 판단 근거가 된다 | **운영 활성화 게이트**(개발은 D-120으로 선행) |
| 7-2 | holmesgpt(Python ≥3.13) 폐쇄망 반입 가능 여부·절차 — statsmodels·임베딩과 동종 행정 | P0-2 |
| 7-3 | Prometheus 인프라 소유·스크레이프 라벨 표준화(nodename) 가능 여부 | P0-3·4-A |
| 7-4 | 폴스타 에이전트 확장(E8 채널) 벤더 협의 착수 시점 — Phase 4-B의 유일한 외부 의존 | P0-4 |
| 7-5 | Phase 1(게이트 트랙)과 Phase 2(sre-agent 트랙)의 착수 우선순위 — 병렬 가능하나 리소스가 하나면 어느 쪽 먼저인가(권고: **Phase 1 먼저** — 블로커 0·기존 자산 위 증분. 단 D-120으로 Phase 2도 7-1 확정 없이 개발 착수 가능해짐 — P0-2 반입만 선행) | 일정 |

## 8. 변경 이력

| 날짜 | 변경 | 사유 |
|---|---|---|
| 2026-07-27 | **Phase 2 우선 항목 구현 (2-A·2-0·2-B 골격)** — R3 mcp_server 고수준 도구 8종(`polestar_*`·마스킹·도메인 deny·execute_sql 옵트인·**D-122 등재**·mcp_server/tests 34→103)·R16 Gemini 경로(`sre_agent/.venv` holmesgpt 0.36.0·litellm 1.89.0 실측·`gemini/gemini-2.0-flash`[D-021 준수]·smoke_llm 보류·**D-120 상태→구현완료**)·sre_agent 패키지 골격(구 SREAgent 이관·경계 양방향 import 0·**D-118 상태→골격 완료**·sre_agent/tests 18). 전 게이트 green(arch 0·test_alarm 716 무회귀). **환경 블로커**: GEMINI_API_KEY 미설정(실 Gemini 왕복 보류)·Docker 미기동(PG/Prometheus 통합·A/B 게이트 보류)·실 폴스타 DB 미접속(M-D·R13). **잔여**: 2-B 조사 코어 W-A·2-B′ R5(PromQL 도구·원격 프로파일)·2-C 서비스·2-D dispatcher | Plan 66 §3 Phase 2 실행(사용자 지시 "66번 계획 구현"). 2-A/2-0은 자립 착수 가능 항목 우선·팀장 직접 검증·D-021 충돌 리뷰 교정(known_mistakes 등재) |
| 2026-07-27 | **Phase 1 구현 완료 (Wave 1-A·1-B)** — R1 목업 생성기(`scripts/mock_polestar_events.py`·`tests/test_scripts` 42 passed·**D-121 등재**[예약 D-115 무효화, 최댓값 D-120+1])·R2 E7(`annotation_signal.py`·E7-a~d·플래그 5종 off·`tests/test_alarm` 688→716 passed[+28]·**D-116 상태→구현 완료**). arch_check exit 0·flags-off 비트동일. 잔여: 1-C 교차 검증, cascade/change-corr 픽스처(§7 G-3), invest-trigger는 R8 후 | Plan 66 §3 Phase 1 실행(사용자 지시 "66번 계획 구현"). Wave 1-A·1-B 상호 독립·병렬 구현(implementer 서브에이전트)·팀장 직접 검증(test_alarm 716/0·arch 0) 후 승인 |
| 2026-07-27 | **Prometheus Docker 테스트 픽스처 구체화** — sre-agent/06 §8.1 신설(`testdata/prometheus/` compose: prometheus 9090·node_exporter 9101 재배치·mock_exporter 합성 메트릭 결정적 단언·nodename 라벨=PG 픽스처 server_name 정렬), 2-B′·Plan 04 §9에 참조 연결. PromQL 도구·A/B 품질 게이트·Gemini e2e가 같은 픽스처 공유 | 사용자 지시("Prometheus도 테스트할 수 있도록 Docker 테스트 환경 구성 계획 수정"). `testdata/pg` 전례 실측 후 동일 관례 적용 — 신규 D-번호 없음(D-119/D-120 검증 세부) |
| 2026-07-27 | **D-120 반영**: R16(Gemini 테스트 LLM 경로)·Phase 2-0 wave 신설, §7-1 게이트를 "운영 활성화 게이트"로 완화(개발은 Gemini로 선행), 7-5 권고 갱신 | 사용자 지시("HolmesGPT 테스트를 위해 Gemini API로 테스트할 수 있도록 코드 작성 계획 추가"). 상세: `docs/02_decision.md` D-120·sre-agent/02 §10.1 |
| 2026-07-27 | §2 목표 아키텍처에 **HolmesGPT 연동 상세 명시** — DiagnosisAgent(ToolCallingLLM) ReAct 루프·LLM 엔드포인트(§7-1 게이트)·remote_vm_profile·`Config.mcp_servers`→RemoteMCPToolset 자동 발견·`llm_instructions` 단일 주입·LLMResult→결정적 후처리 흐름 | 사용자 지시("목표 아키텍처에 HolmesGPT 연동 내용이 보이도록") — 그림 상세화, 설계 변경 없음 |
| 2026-07-27 | **D-119 반영**: R5에 `mcp_server` PromQL 도구 편입(§1.2·2-B′·§2 그림·§4) — 하향 의존 `mcp_server` 단일화(관측 읽기 접근 경계 재정의)·품질 게이트(내장 toolset 대비 열화 없음·열화 시 A안 복귀) 추가 | 사용자 채택 지시("PromQL도 MCP 서버로 통합"). 상세: `docs/02_decision.md` D-119·sre-agent/04 §4.4·06 §3/§5-0/§8 |
| 2026-07-24 | Plan 66 최초 작성 | 사용자 지시("sre-agent 01~06과 60~65를 종합 정리하여 구현 계획 수립"). D-118 정합화(같은 날 완료된 Plan 60/62/64/65 갱신) 위에서 잔여 구현 15항목(R1~R15)을 실측 집계하고, 의존성 기반 Phase 0~5 단일 시퀀스로 통합. Phase 3 완료를 MVP(PAGE→조사→브리핑 첨부)로 정의. 신규 D-번호 없음(각 하위 계획 예약분을 착수 시 채번 규칙으로 등재 — §6). 착수 게이트 §7(특히 7-1 조사 LLM 실행 환경 — 폐쇄망 원칙과 HolmesGPT 전제의 긴장) 명시. |
