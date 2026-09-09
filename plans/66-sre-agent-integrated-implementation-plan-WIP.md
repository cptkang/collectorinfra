# 66. SRE-Agent 통합 구현 계획 — sre-agent 01~06 × Plan 60~65 종합 실행 시퀀스

> 작성일: 2026-07-24
> **성격**: 실행 계획(execution sequencing) — sre-agent 계획 6종(`plans/sre-agent/01~06`)과 Plan 60~65의 **잔여 구현 항목을 하나의 의존성 기반 단계 시퀀스로 통합**한다. 개별 계획의 설계를 대체하지 않으며(상세는 각 계획 참조), 역량 우산은 Plan 62가 유지한다(본 계획은 그 아래 "무엇을 어떤 순서로 만들 것인가"의 단일 장부).
> **대상 계획**: `plans/sre-agent/` 01(대체)·02(중심)·03(대체)·04·05·06 + Plan 60(E7·E8·§14 잔여)·61(잔여)·62(우산)·63(완료·후속)·64(§0 재편 CW)·65(미구현).
> **관련 결정**: D-120(HolmesGPT 개발·테스트 LLM=Gemini API — 운영 LLM 결정과 분리), D-119(PromQL `mcp_server` 통합 — 관측 읽기 접근 경계 일원화), D-118(SREAgent 통합·`sre_agent/` 독립 패키지), D-117(E8 L3 게이트 편입·폴스타 에이전트 확장), D-116(E7 텍스트·주석 신호), D-106~D-114(Plan 60 E1~E6·STL·임베딩 — 구현 완료), D-035(결정적=판단/LLM=보조), D-003(읽기전용).
> **신규 결정**: 본 계획 자체는 신규 D-번호를 부여하지 않는다 — 각 단계 착수 시 해당 하위 계획의 예약분을 **collectorinfra 채번 규칙**(`## D-` 헤더+「변경 이력」+「채번 이력」 표 grep 최댓값+1 — **현재 최댓값은 §6 참조**. 작성 시점 표기값 D-120은 2026-07-24 스냅샷)으로 등재한다(§6). ※ D-119(PromQL `mcp_server` 통합 — R5 반영)·D-120(Gemini 테스트 LLM — R16 반영)은 2026-07-27 사용자 채택으로 기등재.
> **상태**: **Phase 1~3 구현 완료 — MVP 실증**(2026-07-28 실 Gemini 조사 완주). Phase 4~5·잔여 항목은 §1.4 스냅샷 참조. **2026-08-25 재점검: Plan 66 스코프 코드 진척 0·회귀 0 — 잔여 5건은 전부 코드 외 선행조건 대기**(§1.5). **⚠ 2026-08-31 갱신(D-189): R11(E8)의 P0-4(벤더 협의) 블로커가 해소돼 잔여 5건 중 1건이 착수 가능해졌다** — L3 접근 경로가 B(허용목록 명령 실행)로 확정됐다(§1.5 ③). 착수 게이트는 §7의 사용자·행정 확인 항목. **MVP 재현 절차: `docs/23_plan66_mvp_test_guide.md`**.

---

## 1. 현황 집계 — 구현 완료 vs 잔여 (2026-07-24 실측 · 상태 열 2026-08-05 갱신 · 2026-08-25 재확인 — 변동 없음)

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
| R1 | 목업 이벤트 생성기(대화형 메뉴·TCP 주입·판정 대조) | Plan 65 §3~§6 (sre-agent/03 대체 흡수) | **완료**(1-A · **D-121** — 예약 D-115 무효화. 카탈로그 [13][14] 보강, [12]는 3-C에서 활성) |
| R2 | E7 텍스트·주석 신호(a 하베스팅·b 비알람 분류·c 파서 견고성·d 사이트 상관) | Plan 60 §17 | **완료**(1-B · D-116 구현 + 1-C 교차 검증 `test_e7_mock_crossvalidation.py`. 후속: 주석 분류 LLM 전환은 Plan 67 D-132 소관) |
| R3 | `mcp_server` 조사용 고수준 도구 8종 + 프로세스 프록시(마스킹) + 도메인 deny | sre-agent/04 M-A/M-B | **완료**(2-A · **D-122** — PG 실 런타임 e2e 포함. ※ deny 중 RESOURCE_CONF_ID 조인 금지는 D-022 재검토로 제거 f15ac46) |
| R4 | `sre_agent/` 패키지 골격 + 조사 코어(HolmesGPT·pull) + 브리핑·후처리 | sre-agent/02 W-A, 05 §5 | **완료**(2-B·2-C · D-118 골격 + D-123 — 실 Gemini 조사 완주 실증 2026-07-28) |
| R5 | 원격 프로파일(`remote_vm_profile`) + **`mcp_server` PromQL 도구(D-119 — hostname 앵커 고수준 기본·원시 옵트인)** + 로컬 픽스처 검증·품질 게이트 | sre-agent/06 R-A/R-B·04 §4.4 | **완료**(2-B′ · D-119 구현·Docker e2e·서버측 nodename 조립 실증) + **A/B 품질 게이트 실측 완료**(2026-08-06 · A 4.0/4 = B 4.0/4 동률 → **열화 없음·B안 유지 확정**, A안 복귀 불필요) |
| R6 | 조사 서비스 MCP 노출(submit/poll·잡 저장소·경계 테스트) | sre-agent/05 §3~§5 | **완료**(2-C · **D-123**) |
| R7 | HolmesGPT `mcp_servers` 연동 + dispatcher·push·severity_judge | sre-agent/04 M-C, 02 W-B | **완료**(2-C/2-D · D-123 — dispatcher·severity_judge·briefing_builder) |
| R8 | collectorinfra 소비 배선: 게이트 훅→submit/poll·브리핑 첨부·pull 위임·escalate 승격 | Plan 64 §0.2 CW-A~C, Plan 60 §14 | **완료**(3-A/3-B · **D-124** — CW-A·CW-B·CW-C) + **정련 완료**(3-E · **D-137** — 즉시통보+후속 브리핑, 옵트인) |
| R9 | `invest-trigger` 시나리오(목업→훅→submit 확인, e2e 옵트인) | Plan 65 §4.3 | **완료**(3-C — 목업 [12] accepted/duplicate, 실 완주는 `RUN_E2E=1`) |
| R10 | 원격 합류(폴스타 MCP·hostname 규약 실측) + remediation_recommender | sre-agent/06 R-C/R-D, 02 W-C | **부분 완료** — remediation_recommender 완료(**D-138**). **R-D 라벨 규약 실측 완료(2026-08-06 · Docker Prometheus)**: nodename 커버리지 **1404/1404=100%**(스크레이프 시점 주입)·보존 15d·무인증·미존재 호스트 graceful·exporter 자체 라벨은 `exported_nodename`으로 밀려 타깃 라벨이 승리(조립 안전, 단 `exported_nodename`에 컨테이너 ID 잔재). **운영 Prometheus 실연동·표준화 협의는 잔여**(P0-3) |
| R11 | E8 L3 게이트 배선(폴스타 에이전트 채널·경계 probe kind 확장·post-gate 요지 첨부·측정 dedup) | Plan 60 §18 | **미착수 · 블로커 해소(2026-08-31 · D-189)** — 접근 경로가 B(허용목록 명령 실행)로 확정돼 **P0-4(벤더 협의)가 더 이상 선행이 아니다**. 착수 가능. 단 §18.3 (a) 게이트 동기 probe의 지연 예산은 착수 시 택일(D-189 「잔여 설계 과제」·Plan 60 §18.3 부기) |
| R12 | (후보) `polestar_host_snapshot` 도구 노출 — E8 채널의 조사 개방 | sre-agent/04 §4.2 후보 | E8(R11) 후 결정 |
| R13 | 전송 인증(Bearer) + 실 폴스타 DB 런타임 검증(**PostgreSQL 한정 — D-126**) | sre-agent/04 M-D | **완료**(D-125 Bearer·PG e2e — DB2는 방언 단위 테스트 유지·실 인스턴스 확보 시 별도) |
| R14 | Text-to-SQL 잔여(경로 C 값 인덱스·커버리지 확장·실 DB EX 측정·프로필 예시 정비) | Plan 61 §12.3-7/8 | 미착수 — 실 DB 접속 환경 필요 |
| R15 | Plan 63 후속(EX 라이브 재측정·라우팅 어휘 단일 출처화 별도 계획) | Plan 63 §1.3·상태 | 미착수 — 데이터 적재 시 |
| R16 | **HolmesGPT Gemini 테스트 경로**: `AgentSettings` LLM 필드 + 스모크 하네스 `smoke_llm.py`(litellm tool-calling 왕복·`DiagnosisAgent` ask 1회) + 데이터 통제(목업·픽스처만) | sre-agent/02 §10.1 | **완료**(2-0 · **D-120** — 기본 모델 `gemini/gemini-3.5-flash` ListModels 실측 확정·스모크 2단계 완주·D-127 승인 게이트 하 운용) |
| — | 자동 조치 실행(폐루프 뒤 절반) | Plan 64 §8.3 B-3 | **착수 금지 유지**(D-003 예외 거버넌스 미확정) |

### 1.3 구현하지 않는 것 (대체 확인)

- sre-agent/01(자체 게이트)·03(자체 목업): **대체됨** — 기존 게이트(Plan 52/60)·Plan 65가 담당.
- Plan 64 §3 `investigation_graph`·§5 severity_judge·§6 briefing_deliverer·§8 remediation_recommender의 **collectorinfra 자체 구현**: 대체됨(D-118) — `sre_agent/` 패키지(sre-agent/02)가 구현, collectorinfra는 MCP로 소비(Plan 64 §0).
- sre-agent/04 `polestar_noise_signals` 집약 도구·§7.1 게이트 결정적 클라이언트: 폐기 — 게이트는 기존 자체 신호 수집(`polestar_noise_context.py`) 유지.

### 1.4 구현 현황 스냅샷 (2026-08-05 실측 · 2026-08-25 재실측 — Phase 상태 전건 불변)

| Phase | 상태 | 비고 |
|---|---|---|
| Phase 0 | 부분 | P0-2는 개발 환경 확보 완료(`sre_agent/.venv` — holmesgpt 0.36.0·litellm 1.89.0 실측; 폐쇄망 반입 행정은 별도 잔여). P0-1(반입 행정)·P0-3(실 Prometheus 실측)·P0-4(벤더 협의) 대기 |
| Phase 1 | **완료** (2026-07-27) | 1-A(D-121)·1-B(D-116)·1-C 교차 검증 21건(`noise_gate/tests/test_e7_mock_crossvalidation.py` — D-139 이관 후 경로) |
| Phase 2 | **완료** (2026-07-27~28 · **게이트 실측 완료 2026-08-06**) | 2-0(D-120)·2-A(D-122)·2-B(D-118)·2-B′(D-119)·2-C/2-D(D-123). **D-119 A/B 품질 게이트 실측 완료 — 열화 없음(A 4.0/4 = B 4.0/4)·B안 유지 확정**. 잔여 0 |
| Phase 3 | **완료 = MVP** (2026-07-28) + **정련 완료**(3-E · 2026-08-05) | 3-A/3-B(D-124)·3-C(R9)·3-D(D-125·D-126). **MVP 실 완주 실증**: 실 Gemini(3.5-flash) 조사 완주 161s·promql 감사 37건·서버측 `{nodename}` 조립 실동작·한국어 인용 브리핑(1331abf). **3-E**(D-137): 그 161초가 곧 통보 지연이던 구조를 즉시통보+후속 브리핑 옵트인으로 해소 |
| Phase 4 | 부분 착수 | **remediation_recommender 완료**(4-A 일부 · **D-138**). 잔여: R10 원격 실연동(P0-3 선행)·**R11 E8(2026-08-31 D-189로 P0-4 블로커 해소 — 착수 가능)**·R12(R11 후 결정) |
| Phase 5 | 미착수 | R14(실 DB 환경)·R15(데이터 적재)·P0-1 활성화(행정 완료 시) |

**잔여 항목 전량(착수 조건)**: ~~① D-119 A/B 품질 게이트~~ **완료(2026-08-06)** ② R10 원격 실연동·hostname 규약 실측 — Prometheus 인프라 실측(P0-3) 선행 ③ R11 E8·R12 — ~~폴스타 에이전트 벤더 협의(P0-4) 선행~~ **해소(D-189) · 착수 가능** ④ Phase 5 R14·R15 — 실 DB 접속·데이터 적재 대기 ⑤ 운영 LLM 확정(§7-1 — 운영 활성화 게이트) ⑥ DB2 실 인스턴스 런타임 검증(D-126으로 스코프 밖 보류). **착수 금지(자동 조치 실행)는 유지**.

> ~~잔여 5건은 전부 **코드 외 선행조건**에 묶여 있다 — 현 환경에서 코드로 진행 가능한 잔여는 없다.~~
> **⚠ 이 문장은 2026-08-31 무효화됐다(D-189).** 접근 경로가 B로 확정되면서 **R11(E8)이 코드로 진행 가능한 잔여가 됐다** — 벤더를 기다리던 유일한 이유가 사라졌다. 나머지 4건(R10·R14·R15·⑥)은 여전히 코드 외 선행조건에 묶여 있다. (①은 2026-08-06 사용자 승인으로 실행·완료)

**기본 스위트 실측 ①(2026-08-05 · 3-E/4-A/D-139 반영 후 · RUN_E2E/RUN_DOCKER_IT 미설정)**: `noise_gate/tests` **1040 passed·9 skipped·4 failed**(사전 존재 — 클린 기준선에도 동일) · `mcp_server/tests` **175 passed·2 skipped** · `sre_agent/tests` **164 passed·2 skipped**(144→164) · 리포지토리 전체(e2e 제외) **3840 passed·40 failed·5 errors** — 실패 45건은 전부 D-139 이관 **이전 기준선과 집합 일치**(회귀 0). 과금 API 실 호출 0(D-127 전역 소켓 가드).

**기본 스위트 재실측 ②(2026-08-25 · 동일 조건)**: `noise_gate/tests` **1040 passed·9 skipped·4 failed**(①과 전건 동일 — 실패 4건도 같은 노드: `test_alarm_enricher` 2·`test_alarm_process_enrich` 2) · `mcp_server/tests` **183 passed·2 skipped**(175→183 · **+8은 Plan 76 D-140 `test_sql_log.py` 유입**으로 Plan 66 무관) · `sre_agent/tests` **164 passed·2 skipped**(불변) · 리포지토리 전체(e2e 제외) **4554 passed·41 failed·29 skipped·5 errors**(passed +714는 본체 Plan 71~77 유입). `scripts/arch_check.py --ci` 본체 exit 0(검사 **202파일**·error 0)·`sre_agent/scripts/arch_check.py --ci` exit 0. **Plan 66 스코프 3종 패키지의 실패 집합은 ①과 동일 — 19일간 회귀 0**.

> **경로 주의(D-139)**: 종전 `tests/test_alarm/`·`tests/test_scripts/`(노이즈 게이트분)·`scripts/mock_polestar_events.py`는 **`noise_gate/` 아래로 이관**됐다. 본 계획의 과거 이력 행에 남은 옛 경로는 그 시점 기록이다.

### 1.5 잔여 정체 점검 (2026-08-25 실측 — 진척 0 · 외부 제약 3건 신설)

**Plan 66 스코프 코드 진척 0.** 직전 갱신(2026-08-06) 이후 19일간 `sre_agent/`·`mcp_server/`·`noise_gate/`를 수정한 커밋은 **2건뿐이며 둘 다 Plan 76 소관**이다 — `7fc3513`(D-140 실행 SQL 로그의 `mcp_server` 편입 — 별도 venv라 자체 미니 로거로 같은 `logs/sql/`에 append)·`fe6305d`(그 검증 보강). R10~R12·R14·R15는 §1.2 상태 그대로다. 같은 기간 `docs/02_decision.md`에 등재된 **D-140~D-162 전건이 타 계획(70~77) 소관**으로, Plan 66 스코프 결정의 신규 등재·상태 변경은 0건이다.

**선행조건 5건 전부 미해소** — §1.4의 착수 조건 ②~⑥이 그대로다:

| 조건 | 2026-08-25 실측 |
|---|---|
| ② P0-3 운영 Prometheus | 미해소. Docker 픽스처(`fixture_prometheus` 9190·`fixture_target_vm` 9101·`fixture_mock_exporter` 9102·`polestar_pg` 5434)는 4주째 가동 중이나 **운영 인프라가 아니다**. `mcp_server/.env`에 `PROMETHEUS_URL` 여전히 미설정 — D-119 운영 주의(미설정 시 PromQL 도구 전건 실패) 유효. **(2026-08-25) 측정 절차는 `docs/23` §7.2.2에 5단계 명령으로 문서화**(도달성·`nodename` 값 목록·커버리지·폴스타 서버명 일치·보존기간 — 픽스처에서 200/`svr-web-01`/1404·1404/2시리즈/15d로 자가 검증). **실서버 측정·라벨 표준화 협의 자체는 여전히 잔여** |
| ③ P0-4 벤더 협의 | **해소(2026-08-31 · D-189)** — 접근 경로를 B(허용목록 명령 실행)로 확정해 **벤더 협의가 선행에서 빠졌다**. 2026-08-25 실측(E8 자산 grep 0건)은 여전히 유효하나, 그것이 *"A를 기다리는 이유"*가 아니라 *"B로 만들면 되는 이유"*가 됐다 |
| ④ 실 DB 접속·데이터 적재 | 미해소. Plan 61 §12.3-7/8·Plan 63 EX 라이브 모두 대기 상태 불변 |
| ⑤ 운영 LLM 확정(§7-1) | 미해소. 개발·테스트 경로는 D-120(Gemini)로 계속 성립 |
| ⑥ DB2 실 인스턴스 | 미해소(D-126으로 스코프 밖 보류 유지) |

**신규 외부 제약 3건 — 잔여 웨이브 착수 시 준수 대상**

| # | 제약 | 근거 | Plan 66에 대한 영향 |
|---|---|---|---|
| **C1** | **승격-폐기 동반 원칙 + 폐기 전 4항 실측 의무** — 새 경로를 기본값으로 승격할 때 구 경로 삭제를 같은 D-번호에 포함하거나 **폐기 기한(구체 일자)**을 명시. **신규 `enable_*` 플래그도 생성 시 만료일 부여** | **D-161**(2026-08-20) | 잔여 웨이브(4-B E8·4-C·Phase 5)가 만드는 신규 옵트인 플래그는 **만료일 없이 등재할 수 없다**. §5-2(신규 플래그 기본 off)에 만료일 부여가 추가된다 |
| **C2** | **플래그 판정 규칙 고정 + 기한부 만료일 2027-02-20 일괄** | **D-162** ⑥ · `docs/flag_audit.md`(43개 전수) | Plan 66 배선 플래그는 전부 **존치** 판정(참조 ≥3): `investigation_trigger_enabled` 8건·`fault_escalation_enabled` 6건·`investigation_followup_enabled` 5건. 그러나 §1.1 완료 자산 중 **`anomaly_stl_enabled`(D-113)·`change_correlation_enabled`(D-111)는 「기한부」** — Phase 5 「P0-1 활성화」가 **2027-02-20까지 이뤄지지 않으면 삭제 또는 사유부 연장 판정을 강제**받는다. P0-1 행정이 사실상 기한을 얻은 셈 |
| **C3** | `alarm.prometheus_enabled`(프로덕션 참조 **0건**) 처리 — ①배선 완결 ②플래그·클라이언트 **동시 삭제** ③예비 코드 명시+기한 부여 중 **사용자 택1 대기**(권고는 ③) | `plans/70` P1-1 | 선택지 ②는 `noise_gate/infrastructure/prometheus_client.py`를 삭제한다. 조사 경로 PromQL은 D-119로 `mcp_server`에 일원화돼 **영향 없으나**, 이 파일은 **게이트측 E3 baseline 폴백 채널**(Plan 60 §5.2·Plan 64 §4.5)의 유일 구현이며 현재 호출부 0건(docstring 언급 1건뿐)이다 — **택1은 Plan 66 Phase 4 관점을 포함해 판단할 것** |

**R15 후속 계획 미신설**: §3 Phase 5가 "별도 신규 계획으로 분리"로 넘긴 **라우팅·인스턴스 어휘 단일 출처화**(Plan 63 §1.3 — `_LOCATION_DB_HINTS`·`_GENERIC_DB_TOKENS` 등의 `DB_DOMAINS` 단일 출처화)는 2026-08-25 현재 `plans/` 어디에도 없다(67~77 전건 대조). `plans/70` P3(시맨틱 레이어 수렴)은 프롬프트 경로 폐기가 대상이라 성격이 다르다 — **신설 자체가 잔여**로 남는다.

## 2. 통합 목표 아키텍처 (완성 시점 그림)

```
폴스타 알람 ─TCP→ noise_gate.alarm_server → Redis Stream → AlarmWorker
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

**패키지 배치 규칙(2026-08-05 · D-139)**: 기능 코드는 **소속 최상위 패키지 폴더 안에서** 구현한다 — 노이즈 게이트·E7/E8 게이트 배선은 `noise_gate/`(종전 `src/alarm/`), 조사 코어·후처리는 `sre_agent/`, 관측 도구는 `mcp_server/`. 각 패키지가 자기 `tests/`·`scripts/`·`testdata/`를 소유하며, 본체 `src/`에는 조립(entry)·배선만 남긴다. 본 계획의 잔여 웨이브도 이 배치를 따른다 — 예: R11(E8 게이트 배선)의 `host_diagnostic_collector.py`는 `src/alarm/`이 아니라 **`noise_gate/infrastructure/`**에 만든다. `noise_gate`는 본체와 같은 프로세스·같은 venv라 `src/ → noise_gate` 의존만 예외로 허용된다(D-048 워커 in-process 기동).

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
| **3-A** | **R8 CW-A**: `notification_gate` PAGE 시 비차단 emit → `sre_agent` MCP 클라이언트(`noise_gate/infrastructure/`, DBHubClient 패턴) submit → poll → 브리핑 `alarm_notifier` 첨부 + `decision_store` 감사. `investigation_trigger_enabled`(기본 off). **트리거 배선 결정 등재**(구 D-101 대체분 — 채번 규칙) | Plan 64 §0.2·Plan 60 §14 | S~M |
| 3-B ∥ | **R8 CW-B**: deepagents `fault_diagnosis` 의도 → `sre_diagnose` 위임 · **CW-C**: `verdict.escalate` 시 escalate-only 후속 통보 승격 | Plan 64 §0.2 | S |
| 3-C | **R9**: Plan 65 §4.3 `invest-trigger` 시나리오(메뉴 [12]) — submit 응답·`duplicate` dedup 확인, 실 HolmesGPT 완주는 `RUN_E2E=1` | Plan 65 §4.3 | S |
| 3-D | **R13**: MCP 전송 인증(Bearer — mcp_server·조사 서비스 양쪽) + **실 폴스타 DB 런타임 검증(D-126: PostgreSQL 한정 — 완료)** — plans/15 승계 검증 부채, mock 통과를 완료로 치지 않는다 | sre-agent/04 M-D | S~M |

| **3-E** | **즉시통보 + 후속 브리핑**(D-124 설계 노트 정련 · Plan 64 §6.2 "후속 메시지") — 트리거 submit-only → notifier가 즉시 통보 **후** 백그라운드 poll·후속 workb 발송. 자체 클라이언트(워커 공유 인스턴스 경합 회피)·빈 후속 미발송·동시 상한·전 구간 감사. 옵트인 `investigation_followup_enabled` 기본 off | Plan 64 §6.2 | S~M |

> **Phase 3 완료 = 최소 완결 가치(MVP)**: "PAGE 1건 → 자동 조사 1회 → 인용 있는 브리핑이 통보에 첨부" 흐름이 목업으로 재현 가능.
> **실행 절차는 `docs/23_plan66_mvp_test_guide.md`**(2026-08-25 신설) — 구성 요소 5종 기동·무과금 스텁 검증(레벨 A)·실 조사 완주(레벨 B·D-127 승인)·관측 지점·문제 해결. **LLM 백엔드 2종 병행**(§6-G Gemini 검증완료 / §6-F FabriX 선행 게이트) — FabriX 직결은 **네이티브 tool-calling 미확인**이라 §7-1 게이트와 직결된다. **MVP 테스트는 실행될 때마다 테스트 코드가 스스로 결과를 `logs/mvp_test/mvp_test_log.md`(실행 대장)+`runs.jsonl`에 기록한다 — 잔여 웨이브 착수 전 이 대장부터 읽는다(해석 방법은 `docs/23` §12). `logs/`는 gitignore라 **실행 호스트에만 남으므로**, 세션·작업자 인계 시에는 요지를 본 계획 §8 변경 이력에 옮겨 적는다.** **§3은 구동 프로세스·서버 배치** — §3.3.1 **서버 A/B/C 정의**(역할·경계 근거·주의점)·§3.3.2 프로세스별 **기동 서버 지정표**·§3.3.5 **방화벽 방향표**·§3.7 **명령·설정 실행 위치 일람**(전 블록에 `# [서버 · CWD · 인터프리터]` 표기). **`sre_agent`는 `127.0.0.1` 고정 바인드라 본체 API와 동일 서버 필수**, vLLM은 별도 GPU 서버, **§8은 실 폴스타·실 Prometheus 직접 연결**(P0-3 측정 절차 포함) — 단 **실데이터×Gemini는 D-120 절대 제약상 금지**이므로 실연동 조사 LLM은 사내 백엔드여야 한다(§8.0 매트릭스).
> **3-E 추가 근거(2026-08-05)**: MVP 실 완주가 161초로 실측되면서 인라인 첨부가 곧 통보 지연이 됐다 — 운영 활성화 전 정련(D-137).

### Phase 4 — 원격·L3 확장

| Wave | 내용 | 원 계획 | 규모 |
|---|---|---|---|
| 4-A | **R10**: 실 Prometheus 연동·hostname 정합 규약 실측 확정(R-D — nodename 라벨 표준화·D-119 서버측 조립의 전제 라벨 실측, 착수 시 실측 후 D-등재 · **미착수·P0-3 선행**) + ~~remediation_recommender~~ **완료**(2026-08-05 · **D-138** — 시그니처 기반 결정적 카탈로그·고위험×저신뢰 "[검토 필요]" 강등·실행 경로 부재 테스트 4건 고정) — 02 W-C | sre-agent/06 R-C/R-D·02 W-C | M |
| 4-B | **R11 E8**: 폴스타 에이전트 스냅샷 채널 어댑터(`noise_gate/infrastructure/host_diagnostic_collector.py` — 게이트 자산(D-139 배치)) + 게이트 동기 경계 probe(kind별 USE·≤2s·캐시) + post-gate 결정적 요지 첨부(sre_agent 미가용 시 폴백) + 측정 기반 dedup 상태지문 | Plan 60 §18 | M~L (P0-4 선행) |
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

1. 단계 완료마다: 전체 pytest 무회귀(**현 기준선 = §1.4 재실측 ②** — `noise_gate/tests` 1040 passed·4 failed[사전 존재] · `mcp_server/tests` 183 · `sre_agent/tests` 164 · 전체 4554 passed·41 failed·5 errors) + `python scripts/arch_check.py --ci` exit 0 + (공용 계층 변경 시) `scripts/overfit_check.py --ci`.
2. 신규 플래그는 전부 기본 off — flags-off 비트동일 테스트(`test_plan60_flags_off_regression.py` 계열)에 섹션 추가. **(2026-08-25 추가 · D-161)** 신규 `enable_*` 플래그는 **생성 시 만료일을 함께 부여**하고 `docs/flag_audit.md`에 행을 추가한다(§1.5 C1).
3. `sre_agent/`·`mcp_server` 변경 시: 경계 테스트(양방향 import 0)·`sre_agent/scripts/arch_check.py` 자체 게이트.
4. e2e(실 LLM·실 DB·playwright)는 `RUN_E2E=1` 옵트인 — 기본 스위트에 편입 금지. **(D-127) 실 LLM(Gemini 등 과금 API) 호출은 사용자 사전 승인 필수** — `RUN_E2E=1` 설정 자체를 승인 후에만 수행, 키 존재만으로 실행되는 게이팅 금지.
5. 결정 등재·계획 갱신: 각 Wave 완료 시 `docs/02_decision.md` 등재(채번 규칙) + 해당 계획 변경 이력 갱신(본 계획 §8에도 반영).

## 6. 결정 채번 계획 (착수 시 등재 대기 목록)

| 시점 | 등재 대상 | 원 예약(인용) |
|---|---|---|
| 1-A | 목업 주입 경로(TCP 기본+Redis 폴백) | Plan 65 D-115 예약 → **D-121로 등재 완료**(D-115 무효화) |
| 2-A~2-D | 고수준 도구 노출 정책 / 조사 루프 위임+결정적 후처리 경계 / mcp_servers 등록 / 권고 human-gated | sre-agent D-014·D-009·D-010·D-011 인용 → **D-122·D-123으로 등재 완료** |
| 2-C | submit/poll 비동기 잡 계약 / 배치 구성(별도 venv·프로세스) | sre-agent D-017·D-018 인용 → **D-123에 통합 등재 완료** |
| 3-A | 게이트 훅 트리거 배선(구 Plan 64 D-101 대체분) | Plan 64 §0.3 → **D-124로 등재 완료** |
| 3-D | MCP 전송 인증 | sre-agent D-015 인용 → **D-125로 등재 완료**(검증 스코프는 D-126) |
| 4-A | 원격 VM 프로파일·hostname 정합 규약 | sre-agent D-020 |
| 4-B | E8 구현 확정분(D-117 상태 갱신) · 4-C `polestar_host_snapshot` 노출 여부 | Plan 60 §18 / sre-agent/04 §4.2 |

> 등재 직전 `## D-` 헤더·「변경 이력」 표·**「채번 이력」 표**를 모두 grep해 최댓값 재확인 — Known Mistakes 원칙.
> **2026-08-25 실측: 최댓값 D-162 → 다음 D-164**(D-158[ux_improvement 병합분]·D-163[Plan 77]은 **미등재 예약**이라 재사용 금지). 2026-08-05 표기값 D-136은 낡았다 — 그 사이 D-137~D-162가 등재됐고, 그중 D-137·D-138·D-139만 Plan 66 소관이다. 또한 **계획서에만 적은 예약은 효력이 없다**(D-161 부기) — 4-A(sre-agent D-020)·4-B/4-C 예약분은 착수 시 `docs/02_decision.md` 「채번 이력」 표에 행을 등재해야 보전된다.

## 7. 착수 전 사용자 확인 필요 (CLAUDE.md 의사결정 규칙)

| # | 질문 | 배경·긴장 |
|---|---|---|
| **7-1** | **(2026-08-25 확정 — 사내 백엔드 실측 · 사용자 지적 반영)** 사내 FabriX의 실제 클라이언트는 **`KBGenAIChat`**(`src/clients/fabrix_kbgenai.py` — `src/llm.py:266`이 `fabrix_client_key` 존재 시 선택, `.encenv`에 `LLM_FABRIX_CLIENT_KEY` 정의, D-037도 "FabriX(KBGenAIChat)"로 명시)이며 **OpenAI 호환이 아니다**: 요청 `{modelId, contents:[문자열], isStream, isRagOn, systemPrompt}`·응답 `{status, content}`·헤더 `x-openapi-token`/`x-generative-ai-client`로 규격이 전부 다르고, **payload에 도구 필드가 없으며** `bind_tools()`는 `tool_registry` dict에 넣기만 하고 **읽는 코드가 0건**(dead store)이라 tool-calling이 프로토콜 수준에서 불가능하다. holmes 0.36.0은 매 호출에 `tools`/`tool_choice`를 싣고 거부 시 예외로 끝나며 **프롬프트 폴백이 없다**(`tool_calling_llm.py:1165`·`:284`). ⇒ **FabriX는 조사 LLM이 될 수 없음이 확정**. **사용자 결정(2026-08-25): A안 — 별도 vLLM을 세워 HolmesGPT를 구동한다**(`docs/23` §7-V). 배선은 적용 완료(`AgentSettings.api_base` 신설 + `Config(api_base=…)` 전달 — 기본값 None으로 기존 동작 불변·`sre_agent/tests` 164 무회귀·arch 0). **잔여**: vLLM 서빙 사양 확정(모델·`--enable-auto-tool-choice`·`--tool-call-parser`·`--max-model-len`) → §7-V.2 tool-calling 왕복 판정 → §7-V.5 **조사 완주 판정** → 확정 시 D-등재. 미채택 B안(Plan 64 §3·§4 고정 파이프라인 부활)은 **vLLM이 완주 판정을 통과하지 못할 경우의 1순위 대안**으로 `docs/23` §7-V.6에 보존한다. ※ 종전 부기가 근거로 삼은 `fabrix_client.py`(`FabriXAPIClient`)는 `fabrix_client_key` 부재 시에만 쓰이는 **폴백 경로**로, 사내 실배치가 아니다.<br>**`sre_agent` 조사 LLM의 실행 환경**: sre-agent/02는 유능한 tool-calling 모델(`anthropic/claude-sonnet-5` API) 전제인데, collectorinfra 전 계획은 **폐쇄망·워커 LLM(FabriX/KBGenAIChat) 우선**이다. 운영 환경에서 ①외부 API 허용 ②사내 게이트웨이 경유 ③로컬 모델 중 무엇인가? (HolmesGPT 위임 설계의 성립 조건 — tool-calling 불안정 모델이면 원 Plan 64의 고정 파이프라인 근거가 부활한다) **갱신(2026-07-27 · D-120)**: 개발·테스트는 Gemini API로 선행 가능(외부 송신은 목업·픽스처만 — 2-0) — 본 게이트의 실체는 **운영 LLM 확정(운영 활성화 전 필요)**으로 축소되어 Phase 2 개발 착수를 차단하지 않으며, 2-0 스모크·조사 완주 실측이 이 결정의 판단 근거가 된다 | **운영 활성화 게이트**(개발은 D-120으로 선행) |
| 7-2 | holmesgpt(Python ≥3.13) 폐쇄망 반입 가능 여부·절차 — statsmodels·임베딩과 동종 행정 | P0-2 |
| 7-3 | Prometheus 인프라 소유·스크레이프 라벨 표준화(nodename) 가능 여부 | P0-3·4-A |
| 7-4 | 폴스타 에이전트 확장(E8 채널) 벤더 협의 착수 시점 — Phase 4-B의 유일한 외부 의존 | P0-4 |
| 7-5 | Phase 1(게이트 트랙)과 Phase 2(sre-agent 트랙)의 착수 우선순위 — 병렬 가능하나 리소스가 하나면 어느 쪽 먼저인가(권고: **Phase 1 먼저** — 블로커 0·기존 자산 위 증분. 단 D-120으로 Phase 2도 7-1 확정 없이 개발 착수 가능해짐 — P0-2 반입만 선행) | 일정 |

## 8. 변경 이력

| 날짜 | 변경 | 사유 |
|---|---|---|
| 2026-08-25 | **MVP 실행 기록 저장 위치 이동 — `docs/24` → `logs/mvp_test/`** — 사용자 지시("테스트 로그는 docs가 아니라 logs 폴더에 저장"). 대장을 `logs/mvp_test/mvp_test_log.md`로 옮겨 **runs.jsonl과 같은 폴더**에 두고(테스트 산출물은 문서가 아니라 운영 산출물), `docs/24_plan66_mvp_test_log.md` 삭제. 기록기 2종의 `LEDGER` 경로·독스트링·대장 헤더 문구, `docs/23`의 참조 4곳(§0.1 산출물표·§3.7 위치 일람·§12.1 확인 명령·§14 참조)을 일괄 갱신. **전제 변화와 그 반영**: `logs/`는 gitignore라 **대장이 더 이상 커밋되지 않는다** → "세션·작업자를 넘어 참조 가능한 유일한 기록"이라는 종전 근거가 성립하지 않으므로, ①§12 도입부에 **인계 방법**을 명시(폴더 첨부 또는 요지를 **본 계획 §8 변경 이력에 전재** — 모델·판정·소요·커밋 4가지면 충분) ②§12.3 "기록이 없을 때" 절차를 예외가 아니라 **자주 밟는 경로**로 격상 ③§12.4 비기록 원칙을 "커밋 대상이라서"가 아니라 "**파일 첨부·화면 공유로 밖으로 나갈 수 있어서**"로 근거 교체(운영 정보 유입 방지는 그대로). 임시 mvp 테스트로 생성 경로(`logs/mvp_test/` 2파일)·gitignore 적용을 실측 확인 후 산출물 제거 | 사용자 지시. 위치 이동으로 무효가 된 전제(커밋 참조성)를 문서에서 함께 교정 |
| 2026-08-25 | **가이드 §3.3.1 신설 — 서버 A/B/C 역할 정의** — 사용자 지시("서버 A, B의 설명을 추가"). 종전에는 배치 지정표에 서버 기호만 있고 **그 서버가 무엇인지·왜 나뉘는지**가 없었다. 서버별로 ①한 줄 정의 ②도는 것·런타임·배포물·설정·자원 성격·inbound/outbound·운영 주체 표 ③**왜 이 경계인가** ④주의점을 붙였다. **서버 A**(에이전트 호스트 — 모든 판단이 일어나는 곳): Python 3.12+3.13 **둘 다 필요**, GPU 불요(추론은 B), inbound는 알람 9100·UI 8050 **둘뿐**, 경계 근거는 `sre_agent` 루프백 바인드 강제 + "에이전트 중앙 1곳 실행"(sre-agent/06 §1), 주의는 CWD가 과금을 가름·9100 충돌·**단일 장애점**. **서버 B**(GPU — 조사 LLM 전용): vLLM 1개, VRAM이 병목(KV 캐시는 `--max-model-len` 비례), 분리 근거 3가지(자원 성격·모델 로딩/재기동 수명주기·장애 격리), **핵심 주의 — B가 없어도 레벨 A는 돈다**(조사만 `status=stub`으로 떨어지고 게이트·통보는 정상 → 조사 실패가 알람 처리를 막지 않는다). **서버 C**(데이터 접근 경계)와 **우리가 만들지 않는 것**(폴스타 DB·Prometheus·node_exporter·Redis·worKB) 표도 함께. 기존 §3.3.5(각 서버에 필요한 것)는 중복이라 신설 절에 흡수하고 하위 절 번호·상호참조 전건 재정렬(3.3.1~3.3.5) | 사용자 지시. 배치표만으로는 판단 근거가 없어 역할·경계 이유·강등 동작을 명시 |
| 2026-08-25 | **가이드 전 명령·설정 블록에 실행 위치 표기 + §3.7 일람 신설** — 사용자 지시("모든 스크립트·명령어 실행 위치를 포함"). 문서의 **38개 블록 전수**에 첫 줄 `# [서버 · CWD=… · 인터프리터]`를 삽입하고(누락 0 자동 감사), 상단에 **범례**(A=에이전트 호스트·B=GPU·C=데이터 접근, `★`=위치를 틀리면 동작·결과가 달라지는 곳)를 추가. **§3.7 일람** 신설 — 기동 명령 5종·테스트/검증 7종·주입/관찰 4종·**설정 파일 6종**(설정은 CWD가 아니라 *어느 서버의 어느 파일*이 기준)을 표로 집약. §3.1 인벤토리·§3.6 터미널 표에도 기동 서버 열 반영. **작업 중 발견·수정한 결함 1건**: §11 회귀 기준선 블록이 `cd mcp_server && …` 다음 줄에 `cd sre_agent && …`를 연달아 써 **두 번째 cd가 실패**하는 형태였다(본 세션에서 실제로 밟은 오류) → **서브셸 `( cd … && … )`로 교정**하고 3종 스위트를 그 형태로 재실행해 문서 기재값(1040/183/164) 재현 확인. 개발에서 A=B=C 1대여도 **CWD·인터프리터 구분은 그대로 유효**함을 명시(§2.3 과금 갈림·임포트 실패) | 사용자 지시. 표기 누락은 스크립트로 전수 감사해 0 확인 |
| 2026-08-25 | **가이드 §3.3 서버 배치 명확화 — 프로세스별 기동 서버 지정** — 사용자 지시("어떤 프로세스를 어떤 서버에서 기동해야 되는지 명확히"). §3.3.1 **배치 지정표**(프로세스×기동 서버×강제성×근거)·§3.3.2 배치도(서버 A/B/C 경계 표시)·§3.3.3 **시나리오 3종**(개발 1대 / 레벨 B A+B / 실연동 A+B+C)·§3.3.4 **방화벽 방향표**(출발→도착·포트·용도)·§3.3.5 서버별 필요 런타임. §3.1 인벤토리·§3.6 터미널 표에 **기동 서버 열** 추가. **실측으로 확정한 강제 제약**: `sre_agent`의 `DEFAULT_HOST = "127.0.0.1"`은 `interface/mcp_service.py:32` **하드코딩이고 env 오버라이드가 없다**(`run_service`도 상수를 그대로 사용) → **조사 서비스는 본체 API와 반드시 같은 서버**여야 하며, `NOISE_INVESTIGATION_SERVICE_URL`을 원격 주소로 바꿔도 연결되지 않는다. 원격 분리는 **바인드 설정화 + 인증 강제**가 선행(별건). 그 외 바인드 실측: ①`API_HOST=0.0.0.0`·②`socket_host=0.0.0.0`·③`SERVER_HOST=0.0.0.0` → 분리 가능. **방향성 정리**: inbound가 필요한 것은 **②(폴스타→9100)뿐**이고 나머지는 전부 outbound. ③은 폴스타 DB·Prometheus 도달 가능한 망에 있어야 해 망이 갈리면 서버 C로 분리. vLLM은 GPU 자원·수명주기가 달라 **서버 B 분리가 사실상 필수**. 문제 해결에 배치 증상 4건 추가 | 사용자 지시. 배치는 추정이 아니라 **바인드 주소 실측**으로 강제성을 판정 |
| 2026-08-25 | **MVP 실행 기록을 테스트 코드 내장으로 재설계 + 대장 해석 절 신설(`docs/23` §0.1·§12)** — 사용자 지적("별도 실행 스크립트가 아니라 MVP 테스트 코드에서 직접 로깅하는 게 낫지 않겠나")을 채택. **직전 판(`scripts/mvp_test_run.py` 489줄)을 폐기**한 이유: 외부 스크립트가 감사 파일을 뒤져 판정을 **재도출**하면 판정 로직이 두 벌이 되고, pytest를 직접 실행하면 기록이 남지 않는다. **신설**: `noise_gate/tests/mvp_record.py`(레벨 A)·`sre_agent/tests/mvp_record.py`(레벨 B) — `@pytest.mark.mvp` 마커 + `mvp_record` 픽스처 + `pytest_runtest_makereport` 훅으로 **어떤 실행 경로(pytest·IDE·CI)에서도** 결과·소요·환경 지문·관측값이 `logs/mvp_test/runs.jsonl` + **커밋 대상 대장 `docs/24`**에 남는다. **판정을 테스트로 이관**: `test_send_invest_trigger_e2e`가 종전 `rc==0`(트리거가 graceful 실패해도 통과하는 약한 단언)에서 **감사 레코드 기반 5단언**(게이트 적재·PAGE 티어·investigation 레코드·investigation_id·종결 status)으로 강화. 레벨 B `test_mvp_investigation_completes_or_graceful`은 완주·도구호출·토큰을 기록. **설계 판단 3건**: ①기록기 2벌은 중복이 아니라 **경계 준수** — `sre_agent`는 별도 venv·양방향 import 0(D-118/D-139)이라 모듈 공유 불가, **공유하는 것은 출력 파일 계약**(D-140 `mcp_server` 미니 SQL 로거 전례) ②**skip은 기록하지 않는다** — 기본 스위트가 매번 도는데 skip을 적으면 대장이 잡음으로 덮인다 ③**키 값은 남기지 않고 `api_key_set` 불리언만** — 대장은 커밋 대상. **§12 신설**: 대장을 잔여 웨이브 착수에 쓰는 법(열별 해석·웨이브별 확인 항목·기록 부재/노후/dirty 시 처리·비기록 원칙). 회귀 0(noise_gate 1040/4 사전존재·sre_agent 164·arch 양쪽 0) | 사용자 지적 채택 — 직전 설계를 폐기하고 재작성. 테스트 실행 자체는 미수행(vLLM 미확보·D-127) |
| 2026-08-25 | **MVP 테스트 실행 하네스 + 실행 대장 신설** — `scripts/mvp_test_run.py`(489줄)와 **`docs/24_plan66_mvp_test_log.md`(실행 대장)**. `docs/23` 절차를 1회 실행하고 **재현·비교 가능한 기록**을 남긴다: 환경 지문(git 커밋·브랜치·dirty·인터프리터 2종·포트 점유·Docker 픽스처·조사 관련 플래그)·프로세스별 stdout/stderr·목업 주입 출력·**런 구간만 슬라이스한 감사**(주입 직전 byte offset 기록 후 델타 읽기 — `logs/alarm_decisions.jsonl`·`sre_agent/.data/investigation_audit.jsonl`)·§5.5 합격 기준 7항 코드 대조(`result.json`)·`summary.md`. `logs/`가 gitignore라 원시 런은 로컬 한정이므로 **커밋 대상 대장에 런당 1행**을 덧붙여 세션·작업자를 넘는 참조 경로를 만든다. **설계 규약 4가지**: ①**이미 떠 있는 포트는 재사용하고 종료하지 않는다** — 하네스가 spawn한 것만 추적·정리(사용자 프로세스 보호) ②**`.env`를 고치지 않는다** — 게이트·트리거 플래그는 프로세스 env로만 주입하고 지문에 `.env` 기준값과 주입값을 **둘 다** 기록(한쪽만 남기면 나중에 해석 불가) ③**`--mode real`은 `--approved` 없이 거부**(D-127 건별 승인을 코드 게이트로 강제·exit 2) ④레벨 A는 `GEMINI_API_KEY=""`를 명시 주입해 스텁 확정 → 외부 호출 0. 자체 스모크로 승인 게이트 차단(exit 2)·수집/판정/요약/대장 생성 경로를 확인했고, 스모크 산출물은 실제 실행 기록이 아니므로 제거(대장은 헤더만 유지) | 사용자 지시("MVP 테스트 진행 시 로그가 저장되도록 코드를 수정 — 향후 66번 계획 진행 시 참조"). **테스트 실행 자체는 하지 않음** — vLLM 미확보·D-127 승인 대상 |
| 2026-08-25 | **사내 조사 LLM = 별도 vLLM 채택(사용자 결정) — 가이드 §7-V 재작성 + `api_base` 배선 적용(코드 2곳)** — FabriX 불가 확정(§2.2.1)에 따라 사용자가 **A안(별도 vLLM으로 HolmesGPT 구동)**을 선택. `docs/23`의 사내 백엔드 절을 **§7-V**로 재작성: §7-V.1 **vLLM 서빙**(`--enable-auto-tool-choice`·`--tool-call-parser` **2개 플래그가 필수** — 없으면 200을 주면서 `tool_calls`만 비는 조용한 실패, 파서명은 모델·버전 의존이라 실측 확인 필요 · ReAct 누적 때문에 `--max-model-len` 넉넉히)·§7-V.2 **왕복 판정**(불합격 원인별 조치표)·§7-V.3 배선·§7-V.4 기동·§7-V.5 **완주 판정**(왕복 1회 ≠ ReAct 완주 — `status=done`+6요소+도구 인용이어야 합격)·§7-V.6 미채택 대안 보존. **코드 변경 2곳(추가·기본값 None)**: `sre_agent/settings.py`에 `api_base: str | None = None`(env `API_BASE`) 신설, `diagnosis.py`가 `Config(api_base=…)`로 전달 — 종전엔 필드가 없어 사내 엔드포인트 지정 자체가 불가했다(holmes 0.36.0 `Config`는 `api_base` 보유 실측). 검증: `AgentSettings(api_base=…)` → `DiagnosisAgent._config.api_base` 도달 확인 · `sre_agent/tests` **164 passed·2 skipped**(기준선 불변) · `arch_check --ci` exit 0. **미해결로 남긴 것**: 스텁 게이트가 `gemini_api_key` 단일 조건이라 vLLM 사용 시에도 `GEMINI_API_KEY=dummy`가 필요하다 — 중립 이름(`investigation_api_key`) 개명은 D-161 ① 대상의 별건 결정으로 문서에만 명시 | 사용자 지시("별도의 vLLM을 기반으로 holmesgpt를 구동하여 테스트를 진행하도록 가이드를 수정하라"). 가이드 수정과 함께 실행 전제인 배선을 적용(문서만으론 실행 불가) |
| 2026-08-25 | **FabriX 판정 정정 — "게이트로 판정" → "조사 LLM 불가 확정"(§7-1 갱신·`docs/23` §2.2.1/§7-F 재작성)** — 사용자 지적("현재 fabrix는 openai 호환되지 않아 tool calling이 적절히 되지 않는다")에 따라 재검토한 결과 **지적이 옳았고 종전 판단은 클라이언트 오인**이었다. 사내 실클라이언트는 `FabriXAPIClient`(OpenAI 호환 폴백)가 아니라 **`KBGenAIChat`**(`src/llm.py:266` — `fabrix_client_key` 존재 시 선택, `.encenv`에 `LLM_FABRIX_CLIENT_KEY` 정의, D-037 표기와 일치)이며, 요청 `{modelId, contents:[문자열 배열], isStream, isRagOn, systemPrompt}`·응답 `{status, content}`·헤더 `x-openapi-token`/`x-generative-ai-client`로 **OpenAI 규격과 전부 다르고**, **payload에 도구 필드가 없으며 `bind_tools()`가 채우는 `tool_registry`는 소비처 0건(dead store)**이라 tool-calling이 프로토콜 수준에서 불가능하다. holmes 0.36.0은 매 호출에 `tools`/`tool_choice`를 싣고 거부 시 예외로 끝나며 프롬프트 폴백이 없다(`tool_calling_llm.py:1165`·`:284` 실측). ⇒ litellm `openai/` + `api_base` 시도는 tool-calling 이전에 프로토콜에서 실패하므로 **게이트로 판정할 미지수가 아니라 확정 사실**로 격하. **§7-1 게이트를 A/B 택일로 구체화**: **A안** tool-calling 되는 OpenAI 호환 엔드포인트 확보(D-037 vLLM 선례 — 단 Qwen3.5-9B는 "계획 신호만" 역할 한정이라 40-step ReAct 완주는 별도 확인) / **B안** HolmesGPT 위임 포기 → **Plan 64 §3·§4 고정 파이프라인 부활**(LLM은 서술만 담당 → FabriX로 충분, `mcp_server` 도구·severity_judge·briefing_builder·remediation_recommender·submit/poll 자산은 전량 재사용, 교체 대상은 조사 루프 하나). B안 근거는 Plan 64 §12가 이미 기록한 *"소형모델 tool-calling 불안정 → 고정 파이프라인이 실측으로 정당화됨"* 및 D-035(결정적=판단/LLM=보조) | 사용자 지적("다시 검토하라"). 실측으로 자기 판단을 뒤집고 근거 첨부. 코드 변경 0 |
| 2026-08-25 | **MVP 가이드에 구동 프로세스·물리 배치 절 신설(`docs/23` §3)** — 사용자 지시. §3.1 프로세스 인벤토리 4종(**본체 API+워커 in-process / 알람 수신부 / `mcp_server` / `sre_agent`** — 기동명령·CWD·인터프리터·포트·의존 대상)·§3.2 인프라 프로세스(개발 Docker ↔ 운영 대조)·§3.3 물리 배치 그림과 소유 주체 표·§3.4 포트 충돌·§3.5 파일 시스템 배치·§3.6 터미널 4개. 기존 §3~§12를 §4~§13으로 시프트하고 본문 상호참조 전건 교정. **실측 정리**: ①인터프리터가 **2종**(본체·수신부·`mcp_server`=`.venv` **3.12.11** / `sre_agent`=자체 `.venv` **3.13.1**) — `mcp_server`는 **전용 venv가 없고**(실측) 현재 구동 인스턴스는 pyenv 3.13.1, `sre_agent`만 `requires-python>=3.13`이라 분리 ②`sre_agent`는 **`127.0.0.1` 바인드**(`DEFAULT_HOST` 실측)라 같은 호스트 호출이 전제 ③**9100 충돌** — node_exporter 기본 포트와 알람 수신부가 겹쳐 픽스처가 9101로 재배치된 근거를 실배치 주의로 승격 ④`mcp_server`는 본체(`expose_execute_sql=true`)·조사(`false`) 프로파일이 **양립 불가**라 같은 호스트에선 포트 분리(9099/9097)가 필요 ⑤배치 원칙은 sre-agent/06 §1 인용 — **에이전트는 중앙 1곳 실행·대상 VM 미배포·SSH 미채택**, 대상 VM에는 node_exporter만. 포트 점유 확인 스니펫은 현 로컬에서 실행해 결과 대조(픽스처 5종 OPEN·자체 프로세스 4종 closed) | 사용자 지시("구동되는 프로세스와 물리적인 위치를 가이드에 포함"). 프로세스 토폴로지와 호스트 배치를 모두 포함하도록 정리. 코드 변경 0 |
| 2026-08-25 | **MVP 가이드에 실 폴스타·실 Prometheus 직접 연결 절 신설(`docs/23` §7) + D-120 충돌 명시** — 사용자 지시로 실연동 방법을 문서화. §7.0 데이터 통제 게이트·§7.1 폴스타 DB(소스명↔env 규약·읽기전용 계정·방언·DB2 블로커)·§7.2 Prometheus(설정·**P0-3 측정 5단계**·게이트측 채널 구분)·§7.3 실 알람 피드·§7.4 6단계 전환 순서·§7.5 준수 사항. 기존 §7~§11은 §8~§12로 시프트(내부 참조 교정). **발견한 결정 충돌(사용자 판단 필요)**: **D-120 「데이터 통제(절대 제약)」가 실 폴스타 데이터의 외부 API 송신을 금지**하고, 그 결정적 차단 수단으로 *"운영 `{NAME_UPPER}_CONNECTION` 미설정 → 소스 자동 비활성"*을 명시한다 — 즉 **본 절의 작업은 그 물리적 차단을 해제하는 것**이며, **실데이터 × Gemini 조합은 규정 위반**이다. 가이드에 백엔드×데이터 2×2 매트릭스로 못박고 실연동 조사 LLM을 사내 백엔드로 한정(사용자의 FabriX 계획과 정합). **추가 실측 3건**: ①`ibm_db`가 **세 인터프리터 전부 미설치** → b0(DB2) 존은 드라이버 반입 선행(D-126이 PG 한정인 이유의 실체) ②`readonly=true`는 `execute_sql`에만 `validate_readonly`를 걸므로(`tools.py:118`) 실 DB 방어선은 **계정 권한** ③게이트측 `ALARM_PROMETHEUS_*`는 조사 경로와 별개이고 호출부 0건(`plans/70` P1-1 택1 대기)이라 채워도 무효 — 혼동 방지 명시. P0-3 측정 명령 5종은 픽스처(9190)에서 실행해 200/`svr-web-01`/1404·1404/2시리즈/15d 재현 확인 | 사용자 지시("실제 폴스타와 프로메테우스를 직접 연결하는 방법도 같이 가이드하라"). 지시대로 연결 절차를 문서화하되, D-120 충돌은 임의 진행하지 않고 **금지 조합으로 명시 + 사용자 확인 요청**(CLAUDE.md 의사결정 규칙). 코드 변경 0 |
| 2026-08-25 | **MVP 가이드 LLM 백엔드 2종 확장 + §7-1 게이트에 FabriX 실측 부기** — 사용자가 MVP 테스트를 **FabriX**로 진행할 계획임을 밝힘에 따라 `docs/23`을 **§6-G(Gemini)·§6-F(FabriX)** 병행 구조로 재편(§2.2 백엔드 비교·§6.0 공통·§6-F.1 게이트→§6-F.2 배선→§6-F.3 실행→§6-F.4 대안, 문제 해결 FabriX 6항 추가). **실측으로 드러난 전제 불일치**: HolmesGPT `ToolCallingLLM` ReAct는 **네이티브 tool-calling**을 요구하는데 ①`src/clients/fabrix_client.py::_build_payload`는 **`tools`를 전혀 전송하지 않고**(Few-shot 프롬프트 주입 + 응답 JSON 파싱) ②**D-037**이 그것을 *tool-calling 블로커*로 기록하며 **vLLM(Qwen3.5-9B, OpenAI 호환 /v1)=제어평면 · FabriX=데이터평면** 분리로 우회했고 ③`AgentSettings`에 **`api_base` 필드가 없어** 사내 엔드포인트 연결에 2줄 배선이 선행된다(holmes 0.36.0 `Config`는 `api_base` 보유 — `model_fields` 실측) ④스텁 게이트 조건이 `gemini_api_key is None` **단일**이라 FabriX 키도 그 필드에 실어야 실 조사가 돈다(`investigation_dispatcher.py:142`). ⇒ **FabriX 직결 가부는 이 레포로 판정 불가**로 정리하고, 최소 비용 판정 수단(litellm 왕복 스니펫·목업 도구 1개)을 §6-F.1 게이트로 제공. 게이트 실패 시 경로를 D-037 vLLM 제어평면으로 명시. §7-1(운영 LLM 확정) 행에 이 실측을 부기해 계획과 가이드를 연결 | 사용자 지시("MVP 테스트는 fabrix에서 진행할 예정 — 가이드를 gemini와 fabrix 두 가지로 정리"). 지시대로 2종 병행 정리하되, FabriX 성립 조건이 실측과 어긋나 **가부 판정을 게이트로 분리**(추정으로 동작한다고 쓰지 않음). 코드 변경 0 |
| 2026-08-25 | **MVP 테스트 가이드 신설 — `docs/23_plan66_mvp_test_guide.md`** — 폴스타 실계 없이 "PAGE → 자동 조사 → 브리핑 첨부"를 재현하는 절차를 실측으로 문서화. 구성 요소 5종(Docker 픽스처·조사 프로파일 `mcp_server`·`sre_agent` 조사 서비스·본체 API 워커·`alarm_server`) 기동법, **레벨 A(스텁·과금 0)/레벨 B(실 Gemini·D-127 승인)** 분리, 목업 [12] 주입·관측 지점 3곳·worKB 스텁 수신기·3-E 후속 모드·CW-B pull·문제 해결 12항·회귀 기준선·잔여(§1.5) 연결. **당일 실측으로 확정한 함정 4건**: ①`AgentSettings`가 **CWD 기준**으로 `.encenv`를 읽어 **레포 루트 기동 시 승인 없이 실 Gemini가 도는** 과금 위험(`cd sre_agent`=키 없음·루트=키 있음 양쪽 실행 확인) ②`alarm_server`의 `ALARM_SERVER_REDIS_PORT` 기본 **6379** vs 워커 `REDIS_PORT` **6380** 불일치(6379는 닫혀 있음 — 이벤트 증발의 전형) ③본체 `mcp_server`(9099·`expose_execute_sql=true`)가 **조사 배치와 프로파일이 상충**하며 이미 포트 점유 중(별 포트+`POLESTAR_MCP_URL` 분리 필요) ④`PROMETHEUS_URL`이 `mcp_server/.env`에 없어 PromQL 도구 전건 실패. 부수 확인: 픽스처 hostname/`nodename`이 `svr-web-01`로 정렬·PG 1,597행, 감사는 JobStore가 `sre_agent/.data/investigation_audit.jsonl`에 기록(dispatcher 자체 `audit_path`는 미주입), e2e는 `RUN_E2E` 없이 `2 skipped`(과금 0) | 사용자 지시("66번 계획에서 폴스타 연동 등을 통해 MVP 테스트를 할 수 있는 방법을 docs 폴더에 자세히 설명"). 코드 변경 0 — 포트·환경변수·설정 해석·픽스처 데이터를 전부 당일 실행으로 확인해 작성 |
| 2026-08-25 | **진행 현황 재점검 — Plan 66 스코프 진척 0·회귀 0 확인 + 외부 제약 3건 편입(§1.5 신설)** — 직전 갱신(08-06) 이후 19일 실측. ①**코드 진척 0**: 3종 패키지를 건드린 커밋 2건은 전부 Plan 76 소관(`7fc3513` D-140 SQL 로그 `mcp_server` 편입·`fe6305d` 검증 보강)이고, 그 사이 등재된 D-140~D-162 **전건이 타 계획(70~77)** — Plan 66 결정의 신규 등재·상태 변경 0. ②**선행조건 5건 전부 미해소**(P0-3 운영 Prometheus·P0-4 벤더·실 DB 적재·운영 LLM·DB2) — Docker 픽스처 4종은 4주째 가동 중이나 운영 인프라가 아니며 `mcp_server/.env`의 `PROMETHEUS_URL`도 여전히 미설정. ③**회귀 0 실증**: `noise_gate` 1040/9s/4f(실패 노드까지 08-05와 동일)·`sre_agent` 164/2s 불변, `mcp_server` 175→**183**(+8은 Plan 76 `test_sql_log.py` 유입), 전체(e2e 제외) 3840→**4554 passed**·41 failed·5 errors(+714는 본체 Plan 71~77), arch_check 양쪽 exit 0(본체 202파일). ④**신규 외부 제약 3건 편입**: C1 D-161(승격-폐기 동반 — 신규 `enable_*`는 만료일 필수, §5-2에 반영)·C2 D-162+`docs/flag_audit.md`(Plan 66 배선 플래그 3종은 **존치**이나 §1.1 자산의 `anomaly_stl_enabled`·`change_correlation_enabled`가 **기한부 2027-02-20** → **Phase 5 P0-1 활성화에 사실상 기한이 생김**)·C3 `plans/70` P1-1(`prometheus_enabled` 택1 중 ②는 게이트측 `prometheus_client.py` 동시 삭제 — Phase 4 관점 판단 필요). ⑤**§6 채번 최댓값 D-136→D-162 정정**(다음 D-164 · D-158·D-163 예약). ⑥**R15 후속 계획 미신설 확인** — 라우팅 어휘 단일 출처화는 67~77 어디에도 없다(신설 자체가 잔여) | 사용자 지시("66번 계획의 진행사항을 파악하여 계획파일을 업데이트"). git 이력·`docs/02_decision.md`·`docs/flag_audit.md`·`plans/INDEX.md` 대조 + 테스트 스위트 4종·arch_check 2종·docker ps·`.env` 당일 재실행 실측. 코드 변경 0 |
| 2026-08-06 | **R-D nodename 라벨 규약 실측 (Docker Prometheus)** — 사용자 지시("prometheus는 docker로 구동중이다. 실측해봐라"). Prometheus 2.53.0 실측: **nodename 커버리지 1404/1404 = 100%**(스크레이프 `static_configs.labels`로 수집 시점 주입 → job 무관 전 메트릭 보유, D-119 서버측 `{nodename=…}` 조립이 임의 지표에서 빈 결과가 되지 않음)·보존 **15d**·간격 5s·타깃 2종 `up`·**무인증**(HTTP 200)·미존재 hostname은 빈 배열 graceful. **라벨 충돌 실측**: node_exporter가 `node_uname_info`에 싣는 자기 `nodename`(실 uname)이 타깃 라벨과 겹쳐 **`exported_nodename`으로 밀림**(타깃 라벨 승 → 조립 안전). 단 `exported_nodename` 값에 **컨테이너 ID `efc0cb8b934d` 잔재**(07-28 14:49 단일 시점·현재 활성 0건이나 15d 창 내 조회 가능) → 소비 측은 반드시 `nodename` 사용(현 코드 준수). **대상은 픽스처이며 운영 Prometheus 아님** — 운영 실연동·라벨 표준화 협의(P0-3)는 잔여 | 사용자 지시 — R-D 전제 검증분 선행 실측 |
| 2026-08-06 | **D-119 A/B 품질 게이트 실측 완료 — 열화 없음·B안 유지 확정** — Phase 2 마지막 잔여 해소(사용자 과금 승인분). 동일 픽스처(Prometheus 9190 mock 결정값)·동일 질문·동일 모델(flash-lite)에서 PromQL 접근 경로만 교체하고 **결정적 값 대조**로 채점(LLM 심판 배제·D-035). **A(내장 toolset) 4.0/4 = B(mcp_server 경유) 4.0/4**, 팔당 2회 전건 완주 → Plan 06 §8 수용 기준 7 충족, **A안 복귀 불필요**. 부수: B가 도구 6.0회·58.7k 토큰으로 A(8.0회·85.5k)보다 적게 소모(고수준 도구가 라벨 조립을 대신). 신규 하네스 `sre_agent/scripts/ab_promql_gate.py`(RUN_E2E 옵트인·D-127 게이트 내장)·산출물 `eval_results/d119_ab_gate_final.json`. **측정 중 교정한 하네스·환경 결함 4건**: ①429 전건 실패가 "열화 없음"으로 출력된 **거짓 통과** → 측정 불가 분기·exit 2 ②`prometheus/metrics`가 prerequisite **캐시 히트로 조용히 DISABLED**(A안 실 도구 0개) → `PrerequisiteCacheMode.DISABLED`+도구 존재 단언 ③지표명 미지정으로 LLM이 node_exporter를 조회해 양 팔 0/4 → 결정값 지표 명시 ④`8,589,934,592` 천 단위 구분자 미정규화로 정답을 오답 집계 → 표면형 정규화. **운영 주의**: `mcp_server`에 `PROMETHEUS_URL` 미설정이면 PromQL 도구 전건 실패(실행 중 9099에서 실측) | 사용자 승인("진행하라") — D-127 건별 승인 하 실 Gemini 호출 |
| 2026-08-06 | **배치 규칙 적용 완료 — `alarm_server` 편입(D-139 2차·1cc5d39)** — 폴스타 TCP 수신부(5파일·226줄·`src.` import 0의 자립 모듈)를 `noise_gate/alarm_server/`로 이동, 진입점 `python -m noise_gate.alarm_server`(§2 그림·목업 안내 2곳·docs/20 가이드·`src/config.py` 주석 동반 갱신). **부수 발견**: 종전 최상위 `alarm_server/`는 arch_check 스캔 범위(`src/`) 밖이라 **계층 검사를 한 번도 받지 않았다** — 편입으로 스캔에 들어왔으나 미매핑이라 매핑 추가(수신·적재=infrastructure/기동부=entry/설정=config), noise_gate 미매핑 0·검사 187파일·error 0. **의도적 잔류**: `src/api/routes/alarm.py`(1,420줄)는 본체 앱 인증 계층(`src.api.dependencies`) 의존이라 옮기면 `noise_gate → src.api` 역방향 결합이 신설돼 현 위치 유지(근거 README·D-139 등재). 회귀 0(기준선 대조 집계 동일·실패 집합 diff 0) | 사용자 지시("계획에 맞게 작업을 수행하라") — D-139 배치 규칙을 잔여 자산에 전수 적용 |
| 2026-08-05 | **패키지 경계 재편 — 노이즈 캔슬링 `noise_gate/` 분리(D-139·b79808a)** — `src/alarm`(53)·`tests/test_alarm`+알람 루트 테스트(63)·전용 스크립트(2)를 최상위 `noise_gate/`로 이관(git mv 119건), 각 기능 패키지가 자기 tests/scripts/testdata를 소유하는 배치를 표준화(§2 「패키지 배치 규칙」 신설 — 잔여 웨이브도 이 배치를 따른다: R11의 `host_diagnostic_collector.py`는 `noise_gate/infrastructure/`). 설계 실측 2건: ①2단 중첩(`noise_gate/noise_gate/`)은 루트에서 `import noise_gate.domain`이 해석되지 않아(네임스페이스 선점) editable 설치 의존 → **평탄 레이아웃** 채택 ②`src/api`가 AlarmWorker를 in-process 기동(D-048)하므로 `src → noise_gate` 의존은 설계상 잔존(sre_agent식 양방향 0은 프로세스 분리 선행 필요). arch_check 스캔 루트·내부 패키지 판정 확장(noise_gate 46파일 편입·미매핑 0)·overfit 경로·pyproject(packages/testpaths) 동반 갱신. **회귀 0 실증**: `git worktree add HEAD` 클린 기준선 대조로 3840 passed·40 failed·5 errors 집계 동일 + 실패 집합 45건 경로 정규화 후 diff 0 | 사용자 지시("노이즈 캔슬링을 별도 폴더로 구분 — 별도 코드는 별도 폴더로, 계획 수정 후 기구현분도 검토·수정"). 분리 수준은 인터뷰로 B안(최상위 패키지·venv 공유) 채택 |
| 2026-08-05 | **잔여 2건 구현 — 3-E 신설·4-A 일부 완료** — ① **3-E 즉시통보+후속 브리핑**(**D-137**·b7ccc20): MVP 실 완주 161초 실측으로 인라인 첨부가 곧 PAGE 통보 지연이 되는 구조를 옵트인 후속 모드로 해소. 트리거 `_submit_only` → `investigation_pending` → notifier가 즉시 통보 **후** 태스크 spawn(순서 보장) → poll 완주 → 후속 workb 발송 → 종결 감사. 설계 실측 2건: 워커가 알람 **직렬 처리·클라이언트 1개 공유**라 백그라운드 폴링은 **자체 클라이언트** 필요(`build_sre_agent_client` 신설), verdict 판정은 트리거·후속 공용이라 **domain으로 이관**. 빈 후속 미발송·workb 한정·원 통보 성공 시에만·동시 상한 8·전 구간 graceful. 플래그 3종 기본 off. 본체 915→**934 passed**. ② **4-A 일부 remediation_recommender**(**D-138**·03933b8): 브리핑 「권고」 자리표시를 결정적 카탈로그로 실효화 — 입력은 LLM 서술이 아니라 severity_judge 매칭 시그니처(환각 차단·D-035), 시그니처 11종 대응, 고위험×저신뢰 "[검토 필요]" 강등, 근거 없는 권고 금지, 옵트인 기본 off. **실행 경로 부재(D-003·D-011)를 테스트 4건으로 고정**(실행 수단 import 0·명령 리터럴 0·패키지 전역 subprocess/ssh 0). sre_agent 144→**164 passed**. 전 게이트 green(arch 양쪽 0·mcp_server 175 무회귀·flags-off 비트동일) | 사용자 지시("잔여 작업을 단계적으로 구현"). 잔여 6건 중 **외부 선행조건이 없는 2건**을 선별 착수(나머지는 과금 승인·인프라 실측·벤더 협의·환경 대기로 코드 진행 불가). 기존 경계 테스트가 금지한 명령 리터럴(`kill -`)을 실측 존중해 조치 문구를 서술형으로 교정 |
| 2026-08-05 | **구현 현황 정리(실측)** — §1.2 상태 열 전면 갱신(R1~R9·R13·R16 완료 / R10~R12·R14·R15 미착수)·**§1.4 스냅샷 신설**(Phase 1~3 완료=MVP·잔여 6항목·기본 스위트 실측 본체 915/9s·mcp_server 175/2s·sre_agent 144/2s 전부 green)·§6 등재 완료 반영(D-121~D-126)·헤더 상태 갱신. 아울러 본 이력에 미기록이던 7/28~7/29 구현 3건 소급: ① **MVP 실 완주 실증**(1331abf — `max_steps` 10→40+step-limit graceful·`_default_diagnose_fn`에 `remote_vm_profile`+mcp_servers(Bearer) 실 배선, `RUN_E2E=1` e2e로 실 Gemini(3.5-flash) 조사 완주 161s·promql 감사 37건·서버측 nodename 조립 실동작 — D-119 실증, A/B 게이트만 잔여. 조사 배치는 고수준 도구만: `EXPOSE_EXECUTE_SQL/RAW_PROMQL=false`) ② **D-120 갱신**(8f4e59e — 키 `.encenv` `LLM_GEMINI_API_KEY` AliasChoices 배선·기본 모델 gemini-2.0-flash 서버 퇴역(404) 실측→**gemini-3.5-flash** ListModels 실측 확정·스모크 2단계 완주) + **D-126**(실 DB 검증 PG 한정·Prometheus 픽스처 target-vm 승격 — `node_uname_info` 실 uname e2e) ③ **D-127 과금 API 승인 게이트**(d9142c7·39049bd·1048020 — 실 호출 전부 `RUN_E2E=1` 옵트인·키 존재 게이팅 금지·전역 소켓 가드로 기본 스위트 실 호출 0·§5-4 기반영). 부수: mcp_server 도메인 deny 중 RESOURCE_CONF_ID 조인 금지는 D-022 재검토로 제거(f15ac46 · 2026-08-04 등재) | 사용자 지시("66번 계획의 구현 현황을 관련 계획 파일에 정리"). git 이력·`docs/02_decision.md` D-120~D-127 대조 + 테스트 스위트 3종 당일 재실행 실측 |
| 2026-07-28 | **Docker 게이트 실 e2e 검증 (D-119·D-122)** — Prometheus(9190·§8.1)·PG(5434·cmm_resource 1581행) 픽스처 기동 후 `TestDockerPrometheusIntegration`·`TestDockerIntegration` placeholder→실 단언 e2e 교체. PromQL 서버측 nodename 조립+mock 결정값(8589934592·97.5/1.5·oom 3)·원시 옵트인·timeout / PG 실 asyncpg·반환 계약·LIMIT 방언·svr-web-01. `RUN_DOCKER_IT=1` 2 passed·기본 스위트 166/2 무회귀·test_alarm 총계 785 불변(Docker 기동으로 25 skip→pass·0 failed). **D-122 M-D PG 부채 일부 해소**(DB2 보류)·**D-119 PromQL 실 HTTP 검증**(A/B 게이트는 GEMINI_API_KEY 대기). 연결 env 주입·하드코딩 금지 | 사용자 지시(Docker 블로커 해소·팀장 f308a1a 픽스처 커밋) — placeholder 2곳 실 단언 교체·문서(§8.1 포트 9190·D-119/D-122 검증 상태) 갱신 |
| 2026-07-28 | **Phase 3 구현 (3-A~3-D · MVP 도달)** — R8 CW-A(게이트 훅→submit/poll→브리핑 첨부·`sre_agent_client`·`investigation_trigger`·config 7플래그·**D-124**)·CW-B(fault_diagnosis intent·D-004 3곳)·CW-C(verdict.escalate 승격)·R9 목업 [12] 활성화(accepted/duplicate·test_scripts 74→87)·R13 Bearer 전송 인증(mcp_server·sre_agent·클라이언트·**D-125**·mcp_server/tests 155→166). test_alarm 716→**756**·전 게이트 green(arch 양쪽 0·경계 import 0·flags-off 비트동일). 부수: test_dbhub_integration 15건(D-122 미포착 회귀) 교정→57 passed. **MVP(PAGE→조사→브리핑 첨부)가 스텁 서비스로 재현 가능**. **보류**: 실 HolmesGPT 완주(GEMINI_API_KEY)·실 DB(Docker)·즉시통보+후속브리핑 정련(실 LLM). **환경 주의**: 목업 런타임이 stale 비-editable `.venv/src` 로드(플래그 반영 안 됨) — `pip install -e .` 권고 | Plan 66 §3 Phase 3(사용자 "계속 진행"). 3-A→3-B 순차·3-C∥3-D 병렬·팀장 직접 검증·회귀 원인 실측 분류(.env·MagicMock·D-122)·D-021류 리뷰 교정 |
| 2026-07-27 | **Phase 2 심화 구현 (2-B′ R-A/R-B·2-C·2-D)** — R5(D-119 코드): mcp_server PromQL 도구(고수준 2종 서버측 `{nodename}` 조립+원시 옵트인·mcp_server/tests 103→155)·`remote_vm_profile`·`DiagnosisAgent.mcp_servers`·`AgentSettings.polestar_mcp_url/token`. R4/R6/R7(**D-123 신규**): FastMCP 조사 서비스(9098·도구 5종·contract "1")·잡 저장소(sweep·재기동 running→failed·감사 JSONL)·dispatcher(dedup TTL·동시 상한·전체 타임아웃 300s·예산·토큰 감사)·severity_judge(escalate-only·domain 순수)·briefing_builder(6요소·인용 검증)·DiagnosisResult 확장. `sre_agent/tests` 18→**140 passed**·arch 0·경계 양방향 import 0·test_alarm 716 무회귀. **보류(환경)**: 실 HolmesGPT 완주(GEMINI_API_KEY)·Docker Prometheus 픽스처·D-119 A/B 게이트·게이트 훅 배선(R8/Phase 3). 리뷰 교정: 2-D 조기종료(API 에러) 후 guard 테스트 briefing_fn 누락 4-5건 실측·수정 | Plan 66 §3 Phase 2 심화(사용자 지시 "계속 진행"). R-A∥R-B 병렬(별 패키지)·2-C→2-D 순차·팀장 직접 검증(sre_agent 140/0·arch 0·경계 0) |
| 2026-07-27 | **Phase 2 우선 항목 구현 (2-A·2-0·2-B 골격)** — R3 mcp_server 고수준 도구 8종(`polestar_*`·마스킹·도메인 deny·execute_sql 옵트인·**D-122 등재**·mcp_server/tests 34→103)·R16 Gemini 경로(`sre_agent/.venv` holmesgpt 0.36.0·litellm 1.89.0 실측·`gemini/gemini-2.0-flash`[D-021 준수]·smoke_llm 보류·**D-120 상태→구현완료**)·sre_agent 패키지 골격(구 SREAgent 이관·경계 양방향 import 0·**D-118 상태→골격 완료**·sre_agent/tests 18). 전 게이트 green(arch 0·test_alarm 716 무회귀). **환경 블로커**: GEMINI_API_KEY 미설정(실 Gemini 왕복 보류)·Docker 미기동(PG/Prometheus 통합·A/B 게이트 보류)·실 폴스타 DB 미접속(M-D·R13). **잔여**: 2-B 조사 코어 W-A·2-B′ R5(PromQL 도구·원격 프로파일)·2-C 서비스·2-D dispatcher | Plan 66 §3 Phase 2 실행(사용자 지시 "66번 계획 구현"). 2-A/2-0은 자립 착수 가능 항목 우선·팀장 직접 검증·D-021 충돌 리뷰 교정(known_mistakes 등재) |
| 2026-07-27 | **Phase 1 구현 완료 (Wave 1-A·1-B)** — R1 목업 생성기(`scripts/mock_polestar_events.py`·`tests/test_scripts` 42 passed·**D-121 등재**[예약 D-115 무효화, 최댓값 D-120+1])·R2 E7(`annotation_signal.py`·E7-a~d·플래그 5종 off·`tests/test_alarm` 688→716 passed[+28]·**D-116 상태→구현 완료**). arch_check exit 0·flags-off 비트동일. 잔여: 1-C 교차 검증, cascade/change-corr 픽스처(§7 G-3), invest-trigger는 R8 후 | Plan 66 §3 Phase 1 실행(사용자 지시 "66번 계획 구현"). Wave 1-A·1-B 상호 독립·병렬 구현(implementer 서브에이전트)·팀장 직접 검증(test_alarm 716/0·arch 0) 후 승인 |
| 2026-07-27 | **Prometheus Docker 테스트 픽스처 구체화** — sre-agent/06 §8.1 신설(`testdata/prometheus/` compose: prometheus 9090·node_exporter 9101 재배치·mock_exporter 합성 메트릭 결정적 단언·nodename 라벨=PG 픽스처 server_name 정렬), 2-B′·Plan 04 §9에 참조 연결. PromQL 도구·A/B 품질 게이트·Gemini e2e가 같은 픽스처 공유 | 사용자 지시("Prometheus도 테스트할 수 있도록 Docker 테스트 환경 구성 계획 수정"). `testdata/pg` 전례 실측 후 동일 관례 적용 — 신규 D-번호 없음(D-119/D-120 검증 세부) |
| 2026-07-27 | **D-120 반영**: R16(Gemini 테스트 LLM 경로)·Phase 2-0 wave 신설, §7-1 게이트를 "운영 활성화 게이트"로 완화(개발은 Gemini로 선행), 7-5 권고 갱신 | 사용자 지시("HolmesGPT 테스트를 위해 Gemini API로 테스트할 수 있도록 코드 작성 계획 추가"). 상세: `docs/02_decision.md` D-120·sre-agent/02 §10.1 |
| 2026-07-27 | §2 목표 아키텍처에 **HolmesGPT 연동 상세 명시** — DiagnosisAgent(ToolCallingLLM) ReAct 루프·LLM 엔드포인트(§7-1 게이트)·remote_vm_profile·`Config.mcp_servers`→RemoteMCPToolset 자동 발견·`llm_instructions` 단일 주입·LLMResult→결정적 후처리 흐름 | 사용자 지시("목표 아키텍처에 HolmesGPT 연동 내용이 보이도록") — 그림 상세화, 설계 변경 없음 |
| 2026-07-27 | **D-119 반영**: R5에 `mcp_server` PromQL 도구 편입(§1.2·2-B′·§2 그림·§4) — 하향 의존 `mcp_server` 단일화(관측 읽기 접근 경계 재정의)·품질 게이트(내장 toolset 대비 열화 없음·열화 시 A안 복귀) 추가 | 사용자 채택 지시("PromQL도 MCP 서버로 통합"). 상세: `docs/02_decision.md` D-119·sre-agent/04 §4.4·06 §3/§5-0/§8 |
| 2026-07-24 | Plan 66 최초 작성 | 사용자 지시("sre-agent 01~06과 60~65를 종합 정리하여 구현 계획 수립"). D-118 정합화(같은 날 완료된 Plan 60/62/64/65 갱신) 위에서 잔여 구현 15항목(R1~R15)을 실측 집계하고, 의존성 기반 Phase 0~5 단일 시퀀스로 통합. Phase 3 완료를 MVP(PAGE→조사→브리핑 첨부)로 정의. 신규 D-번호 없음(각 하위 계획 예약분을 착수 시 채번 규칙으로 등재 — §6). 착수 게이트 §7(특히 7-1 조사 LLM 실행 환경 — 폐쇄망 원칙과 HolmesGPT 전제의 긴장) 명시. |
