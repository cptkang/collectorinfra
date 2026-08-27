# 계획서 인덱스 (`plans/`)

> **생성** 2026-08-20 (plans/70 D1) · **갱신** 2026-08-26 (78·79·80 추가) · **전건 85개** (최상위 `plans/*.md` 기준)
> 하위 `plans/sre-agent/` 7개는 별도 패키지 계획이라 이 인덱스에서 제외한다.

`plans/README.md`는 **초기 구현 계획(01~10)의 목차**로 성격이 달라 그대로 둔다.
이 파일은 그 위에 얹는 것이 아니라 **전건 인덱스**다 — 68·69·70과 ux_improvement 병합
편입분(71~75)을 포함해 누락 0. 편입분은 구 65~69의 재부여이며, 구 70(실시간·UX 검토)은
팀장 70(코드베이스 규모·경로 부채) 선점에 따라 **75로 재이동**했다(2026-08-24, 팀장 번호 우선).

## 표기 규칙

- **상태** = 파일 앞부분 40줄의 `**상태**:` / `상태:` 표기를 **기계적으로 그대로 옮긴 값**.
  추정하지 않는다. 표기가 없으면 `*(미표기)*` — 56건이 여기 해당한다.
  대부분 초기(01~59) 계획서로, 당시엔 상태 표기 관례가 없었다.
- **최종 수정일** = 현 브랜치(`multiintent`) 한정 `git log -1`. 다른 브랜치의 수정은 잡히지 않는다.

## 인덱스

| 번호 | 계획서 | 제목 | 상태 | 최종 수정 |
|---|---|---|---|---|
| 01 | [`01-project-structure.md`](./01-project-structure.md) | 01. 프로젝트 구조 및 설정 파일 | *(미표기)* | 2026-03-23 |
| 02 | [`02-state-schema.md`](./02-state-schema.md) | 02. AgentState 상세 스키마 및 노드 간 데이터 흐름 | *(미표기)* | 2026-03-23 |
| 03 | [`03-graph-design.md`](./03-graph-design.md) | 03. LangGraph 그래프 설계 | *(미표기)* | 2026-03-23 |
| 04 | [`04-nodes.md`](./04-nodes.md) | 04. 각 노드의 상세 구현 계획 | *(미표기)* | 2026-03-23 |
| 05 | [`05-dbhub-integration.md`](./05-dbhub-integration.md) | 05. DBHub MCP 클라이언트 설계 | *(미표기)* | 2026-03-23 |
| 06 | [`06-api-server.md`](./06-api-server.md) | 06. FastAPI 엔드포인트 설계 | *(미표기)* | 2026-03-23 |
| 07 | [`07-security.md`](./07-security.md) | 07. 보안: SQL 검증, 민감 데이터 마스킹, 감사 로그 | *(미표기)* | 2026-03-23 |
| 08 | [`08-ui-screens.md`](./08-ui-screens.md) | 08. UI 화면 구현 계획서 | *(미표기)* | 2026-03-23 |
| 09 | [`09-semantic-routing.md`](./09-semantic-routing.md) | 09. 시멘틱 라우팅 구현 계획서 (v2) | *(미표기)* | 2026-04-09 |
| 10 | [`10-document-processing.md`](./10-document-processing.md) | Plan 10: Phase 2 - 문서 처리 (Excel/Word 양식 파싱 및 생성) | *(미표기)* | 2026-04-09 |
| 15 | [`15-mcp-server.md`](./15-mcp-server.md) | 15. DBHub MCP 서버 구축 및 클라이언트 리팩토링 계획 | *(미표기)* | 2026-05-15 |
| 16 | [`16-field-cache-test-plan.md`](./16-field-cache-test-plan.md) | 필드 정보 캐시 생성 테스트 계획 | *(미표기)* | 2026-03-23 |
| 17 | [`17-testenv-and-synonym-dict.md`](./17-testenv-and-synonym-dict.md) | 스키마 캐시 테스트 환경 구축 및 글로벌 유사단어 사전 계획 | *(미표기)* | 2026-03-31 |
| 18 | [`18-claude-skills-plugins.md`](./18-claude-skills-plugins.md) | 18. Claude Code 스킬 및 플러그인 활용 계획 | *(미표기)* | 2026-03-31 |
| 19 | [`19-excel-csv-llm-pipeline.md`](./19-excel-csv-llm-pipeline.md) | Plan 19: Excel → CSV → LLM → Excel 파이프라인 전환 | *(미표기)* | 2026-04-09 |
| 20 | [`20-eav-pivot-query-support.md`](./20-eav-pivot-query-support.md) | Plan 20: EAV 비정규화 테이블 쿼리 지원 | *(미표기)* | 2026-04-09 |
| 21 | [`21-eav-field-mapper-support.md`](./21-eav-field-mapper-support.md) | Plan 21: EAV 구조 전체 파이프라인 지원 (Field Mapper + Redis 연동) | *(미표기)* | 2026-03-31 |
| 22 | [`22-llm-intelligent-field-mapping.md`](./22-llm-intelligent-field-mapping.md) | Plan 22: LLM 지능형 필드 매핑 + 매핑 보고서 + 사용자 피드백 학습 | *(미표기)* | 2026-03-31 |
| 23 | [`23-ui-progress-and-excel-fix.md`](./23-ui-progress-and-excel-fix.md) | Plan 23: Web UI 진행상태 표시 및 Excel 업로드/다운로드 수정 계획 | *(미표기)* | 2026-04-09 |
| 24 | [`24-ui-playwright-test-plan.md`](./24-ui-playwright-test-plan.md) | Plan 24: Playwright UI 테스트 계획 | *(미표기)* | 2026-03-31 |
| 25 | [`25-eav-query-field-validation.md`](./25-eav-query-field-validation.md) | Plan 25: EAV 쿼리 생성 시 존재하지 않는 필드 참조 문제 | *(미표기)* | 2026-03-31 |
| 26 | [`26-schema-redis-cache-optimization.md`](./26-schema-redis-cache-optimization.md) | Plan 26: 스키마 조회 최적화 — Redis 우선 캐시 전략 | *(미표기)* | 2026-04-09 |
| 27 | [`27-schema-analyzer-dependency-removal.md`](./27-schema-analyzer-dependency-removal.md) | Plan 27: schema_analyzer.py DB/테이블 하드코딩 의존성 제거 | *(미표기)* | 2026-03-31 |
| 28 | [`28-gemini-api-support.md`](./28-gemini-api-support.md) | Plan 28: Gemini API 프로바이더 추가 | *(미표기)* | 2026-04-09 |
| 29 | [`29-cache-policy-violations-fix.md`](./29-cache-policy-violations-fix.md) | Plan 29: 캐시 정책 위반 수정 | *(미표기)* | 2026-03-31 |
| 30 | [`30-cache-validity-and-invalidation-audit.md`](./30-cache-validity-and-invalidation-audit.md) | Plan 30: 캐시 값 유효성 검증 및 무효화 정합성 점검 | *(미표기)* | 2026-03-31 |
| 31 | [`31-field-mapping-failure-fix.md`](./31-field-mapping-failure-fix.md) | Plan 31: 필드 매핑 실패 원인 분석 및 해결 방안 | *(미표기)* | 2026-04-09 |
| 32 | [`32-eav-manual-profile-config.md`](./32-eav-manual-profile-config.md) | Plan 32: EAV 구조 메타데이터 수동 설정 지원 | *(미표기)* | 2026-04-09 |
| 33 | [`33-eav-join-directive-enforcement.md`](./33-eav-join-directive-enforcement.md) | Plan 33: EAV 조인 지침의 LLM 프롬프트 강제 적용 | *(미표기)* | 2026-04-09 |
| 33 | [`33-resource-conf-id-join-prevention.md`](./33-resource-conf-id-join-prevention.md) | Plan 33: resource_conf_id JOIN 방지 -- LLM 잘못된 조인 근본 원인 제거 | *(미표기)* | 2026-04-09 |
| 34 | [`34-polestar-domain-system-prompt.md`](./34-polestar-domain-system-prompt.md) | Plan 34: Polestar 도메인별 쿼리 생성 시스템 프롬프트 적용 | *(미표기)* | 2026-04-09 |
| 35 | [`35-excel-empty-data-fix.md`](./35-excel-empty-data-fix.md) | Plan 35: Excel 데이터 미채움 버그 수정 | *(미표기)* | 2026-03-31 |
| 36 | [`36-data-sufficiency-check-improvement.md`](./36-data-sufficiency-check-improvement.md) | Plan 36: 데이터 충분성 검사 로직 개선 | *(미표기)* | 2026-03-31 |
| 37 | [`37-eav-prefix-comparison-fix.md`](./37-eav-prefix-comparison-fix.md) | Plan 37: Synonym 통합 관리 및 EAV 접두사 비교 오류 수정 | *(미표기)* | 2026-04-09 |
| 38 | [`38-field-mapping-propagation-fix.md`](./38-field-mapping-propagation-fix.md) | Plan 38: 필드 매핑 전파 정합성 수정 (column_mapping → SQL alias → 값 추출) | *(미표기)* | 2026-03-31 |
| 39 | [`39-user-authentication.md`](./39-user-authentication.md) | 39. 사용자 로그인 및 인증 시스템 | *(미표기)* | 2026-04-09 |
| 40 | [`40-audit-logging-enhancement.md`](./40-audit-logging-enhancement.md) | 40. 사용자 행위 감사 로깅 강화 | *(미표기)* | 2026-04-09 |
| 41 | [`41-prompt-access-control.md`](./41-prompt-access-control.md) | 41. 프롬프트 기반 접근 제어 (Access Control) | *(미표기)* | 2026-04-09 |
| 42 | [`42-polestar-forbidden-join-tables.md`](./42-polestar-forbidden-join-tables.md) | Plan 42: Polestar 불필요 테이블 JOIN 차단 | *(미표기)* | 2026-04-09 |
| 43 | [`43-polestar-memory-query-failure-analysis.md`](./43-polestar-memory-query-failure-analysis.md) | Plan 43: Polestar 메모리 사용률 쿼리 실패 분석 및 해결 | *(미표기)* | 2026-04-09 |
| 44 | [`44-polestar-monitoring-alert-routing.md`](./44-polestar-monitoring-alert-routing.md) | Plan 44: 폴스타 모니터링 Alert 조회 의도 추가 | *(미표기)* | 2026-06-01 |
| 45 | [`45-alarm-severity-zero-resolved.md`](./45-alarm-severity-zero-resolved.md) | Plan 45 — 알람 심각도 0(해소) 지원 | *(미표기)* | 2026-06-01 |
| 46 | [`46-alarm-socket-receiver.md`](./46-alarm-socket-receiver.md) | Plan 46: 외부 알람 소켓 수신 → LLM 분석 → 메시지 발송 기능 구현 | 설계 완료 / 구현 대기 | 2026-06-09 |
| 47 | [`47-1-alarm-process-enrichment.md`](./47-1-alarm-process-enrichment.md) | Plan 47-1: CPU/메모리 알람 영향 프로세스 보강 (Plan 47 확장) | 구현 완료 (2026-06-16, D-036 기재) | 2026-06-16 |
| 47 | [`47-alarm-history-pattern-analysis.md`](./47-alarm-history-pattern-analysis.md) | Plan 47: 알람 이력 기반 패턴 분석 고도화 — 폴스타 DB 조회 방식 | 구현 완료 (2026-06-11, D-035 기재) | 2026-06-16 |
| 48 | [`48-deepagents-intent-orchestration.md`](./48-deepagents-intent-orchestration.md) | 48. deepagents 기반 의도 분해 오케스트레이션 적용 계획서 | *(미표기)* | 2026-06-17 |
| 49 | [`49-phase2-dynamic-replanning.md`](./49-phase2-dynamic-replanning.md) | 49. Phase 2 — 결과 기반 동적 재계획 (deepagents 실제 패키지 · vLLM 오케스트레이터 + FabriX 응답처리) | *(미표기)* | 2026-06-19 |
| 50 | [`50-fault-diagnosis-rca.md`](./50-fault-diagnosis-rca.md) | 50. 장애진단 · 원인분석 (Fault Diagnosis & Root Cause Analysis) | 계획 (미구현) | 2026-07-14 |
| 50 | [`50-multiturn-context-and-control-plane-token.md`](./50-multiturn-context-and-control-plane-token.md) | 50. 멀티턴 컨텍스트 전파 개선 + 제어 평면(vLLM) 토큰 한계 대응 | *(미표기)* | 2026-06-26 |
| 51 | [`51-fault-diagnosis-data-collection.md`](./51-fault-diagnosis-data-collection.md) | 51. 장애분석 데이터 수집 및 진단 기법 (OS-Level Evidence Collection & Diagnostic Techniques) | 계획 (미구현) | 2026-07-14 |
| 51 | [`51-streaming-scroll-ux.md`](./51-streaming-scroll-ux.md) | 51. 스트리밍 응답 UX 개선 (① 조건부 자동 스크롤 + 플로팅 버튼 · ② 표 가로 스크롤 보존) | *(미표기)* | 2026-06-26 |
| 52 | [`52-alarm-noise-cancellation.md`](./52-alarm-noise-cancellation.md) | 52. 알람 노이즈 캔슬링 — 중요도 기반 발송 판단 (Alarm Noise Cancellation & Notification Gating) | E1~E5 구현 완료 (E5: 2026-07-02, D-048.7 — 사용자 확정 §13.1#8 3경로 전체·메시지형 한정). | 2026-07-14 |
| 53 | [`53-fault-management-roadmap.md`](./53-fault-management-roadmap.md) | 53. 장애관리 기능군 구현 로드맵 (Fault-Management Capability Roadmap) | 로드맵 (구현 순서 권고). 착수 시 `docs/02_decision.md`에 **다음 빈 번호**로 등재 가능(현재 D-048까지 점유). | 2026-07-14 |
| 54 | [`54-noise-cancellation-dashboard.md`](./54-noise-cancellation-dashboard.md) | 54. 알람 노이즈 캔슬링 모니터링·관리 대시보드 (Noise Cancellation Dashboard) | 계획 (미구현) | 2026-06-29 |
| 55 | [`55-multi-source-observability-roadmap.md`](./55-multi-source-observability-roadmap.md) | 55. 멀티소스 관측 확장 로드맵 — APM·DPM 연동 에이전트 (Multi-Source Observability Roadmap) | 로드맵 (방향·순서 권고, 미구현) | 2026-07-14 |
| 56 | [`56-langfuse-observability.md`](./56-langfuse-observability.md) | 56. LLM 관측성 확보 — Langfuse 통합 (LLM Observability with Langfuse) | 계획 (Phase L1~L4 미착수) | 2026-07-14 |
| 57 | [`57-polestar-b0-token-overflow-and-replan-misdiagnosis.md`](./57-polestar-b0-token-overflow-and-replan-misdiagnosis.md) | 57. 폴스타 b0 자원조회 토큰 폭증 + 재계획 오진 분석 및 해결 | *(미표기)* | 2026-07-09 |
| 58 | [`58-polestar-form-fill-db2-schema-and-excel-output.md`](./58-polestar-form-fill-db2-schema-and-excel-output.md) | 58. 파일 업로드 양식 채우기 — 폴스타 DB2 스키마 오류 · 공동존 서버 식별자 NULL · Excel 산출물 누락 분석 및 개선 | *(미표기)* | 2026-07-09 |
| 59 | [`59-a-role-based-admin-access-and-alarm-group-ui.md`](./59-a-role-based-admin-access-and-alarm-group-ui.md) | 59-a. 역할 기반 어드민 접근 정정 + 알림그룹 UI + 보호 root 계정 + 부서 편집 + 감사 로그 로테이션 | *(미표기)* | 2026-07-15 |
| 59 | [`59-admin-rbac-and-chat-ux-improvements.md`](./59-admin-rbac-and-chat-ux-improvements.md) | 59. 어드민 접근 체계 정합화(RBAC) 및 채팅 UX 개선 | *(미표기)* | 2026-07-15 |
| 60 | [`60-noise-cancellation-benchmark-refinement.md`](./60-noise-cancellation-benchmark-refinement.md) | 60. 노이즈 캔슬링 고도화 — 선진사례 벤치마킹 기반 수정·구현 계획 (Noise-Cancellation Benchmark Refinement) | **Wave A(E6·E1·E4) 구현 완료 (2026-07-21) — 사용자 §8 게이트 확정(B-1=AVAIL_DEPEND 단독·B-2=폴스타 이력·B-5… | 2026-07-27 |
| 61 | [`61-text2sql-candidate-selection.md`](./61-text2sql-candidate-selection.md) | 61. Text-to-SQL 쿼리 품질 고도화 — 다중 후보 생성 + 실행기반 선택 + 동의어 매칭 + 결정적 조합 (Candidate Selection + Synonym + Deterministic Composition) | **대부분 구현 — 트랙 A·B·C 착수 완료(2026-07-15, §12 참조)**. E1 하네스(D-072)·트랙 B(E5-1·E5-2 인프라+**런타임 … | 2026-07-16 |
| 62 | [`62-aiops-capability-master-roadmap.md`](./62-aiops-capability-master-roadmap.md) | 62. AIOps 전체 역량 구현 마스터 로드맵 (AIOps Capability Master Roadmap) | 로드맵 (방향·순서 권고, 미구현). 개별 기능 착수 시 각 하위 계획에서 `docs/02_decision.md`에 **다음 빈 번호**로 등재(현재 **등재… | 2026-07-27 |
| 63 | [`63-polestar-overfit-decoupling.md`](./63-polestar-overfit-decoupling.md) | 63. 폴스타 과적합 분리 — DB 어댑터 격리 + 공통 경로 LLM 일반화 (Polestar Decoupling & LLM Generalization) | **완료** (전 트랙 P1~P4, 2026-07-20, 커밋 affca22~9fd1917). 후속 EX 라이브 측정은 **DB 데이터 미적재로 유효 검증 불… | 2026-07-21 |
| 64 | [`64-automated-incident-investigation-and-response.md`](./64-automated-incident-investigation-and-response.md) | 64. 이벤트 자동 조사·진단 브리핑 및 장애 대응 오케스트레이션 (Automated On-Event Investigation, Triage Briefing & Response) | 계획 (미구현). 사용자 확정(2026-07-21): ①산출물=Plan 60 훅 + 신규 Plan 64, ②조사 범위=**L3 실호스트 명령까지 즉시 대상**… | 2026-07-27 |
| 65 | [`65-noise-cancellation-mock-event-generator.md`](./65-noise-cancellation-mock-event-generator.md) | 65. 노이즈 캔슬링 목업 이벤트 생성기 (Mock Polestar Event Generator) | **구현 완료 (2026-07-27 · Plan 66 Wave 1-A · D-121 등재)** — `scripts/mock_polestar_events.py`… | 2026-07-27 |
| 66 | [`66-sre-agent-integrated-implementation-plan.md`](./66-sre-agent-integrated-implementation-plan.md) | 66. SRE-Agent 통합 구현 계획 — sre-agent 01~06 × Plan 60~65 종합 실행 시퀀스 | **Phase 1~3 구현 완료 — MVP 실증**(2026-07-28 실 Gemini 조사 완주). Phase 4~5·잔여 항목은 §1.4 스냅샷 참조. **2026-08-25 재점검: Plan 66 스코프 코드 진척 0·회귀 0 — 잔여 5건은 전부 코드 외 선행조건 대기**(§1.5)… | 2026-08-25 |
| 67 | [`67-stepwise-llm-query-composition.md`](./67-stepwise-llm-query-composition.md) | 67. 단계적 LLM 쿼리 조립 + 경직성 해소 리팩토링 — deep agents vs semantic routing 비교 검토 | **전 트랙 구현 + E1 A/B 평가 실행(2026-08-05, v16) — 단, 측정 조건 불일치로 판정은 잠정·재측정 대기(v17)** — E1 판정: … | 2026-08-06 |
| 68 | [`68-webui-env-settings.md`](./68-webui-env-settings.md) | 68. 설정 웹UI 전면 개편 계획 (v2 — 설정 코드 정밀 분석 반영) | **확정 — 착수 가능 (2026-07-29 사용자 승인)**. §9 게이트 5건 전부 권고안대로 확정 + **사용자 인터뷰 4건 확정(§9-보완)**. 옵션… | 2026-07-30 |
| 69 | [`69-query-generation-structural-refactoring.md`](./69-query-generation-structural-refactoring.md) | 69. 쿼리 생성 영역 구조 리팩토링 — 중복 통합·경로 대칭·계층 정리 | v9 — **계획·후속·별건 전량 완결. 잔여 0건.** P0~P5 구현·D-134 등재·문구 통일 적용·후속 3건에 이어 마지막 별건(EAV 검증 리터럴 이… | 2026-08-05 |
| 70 | [`70-codebase-scale-and-path-debt.md`](./70-codebase-scale-and-path-debt.md) | 70. 코드베이스 규모·경로 부채 정리 — 경로 일원화·플래그 감사·시맨틱 레이어 수렴 | **v4 — 코드 대조 검증 반영 완료. 게이트 1 즉시 해소 가능(관측 대기 소멸). 코드 변경 0건.** | 2026-08-20 |
| 71 | [`71-realtime-usage-api.md`](./71-realtime-usage-api.md) | 71. CPU/메모리 실시간 사용률 조회 — 폴스타 measurement API 데이터 평면 | **구현 완료 (2026-07-24)**. **옵트인 기본 OFF**(`POLESTAR_REST_REALTIME_USAGE_ENABLED=false`) — … | 2026-08-24 |
| 72 | [`72-fss-audit-form-multirow-header-and-month-pivot.md`](./72-fss-audit-form-multirow-header-and-month-pivot.md) | 72. 금감원 감사 취합자료 양식 폼필 지원 — 2단 병합 헤더 결합 · 월별 가로 피벗(M~M+5) · 도메인 밖 필드 정책 | *(미표기)* | 2026-08-24 |
| 73 | [`73-formfill-deterministic-path-and-profiles.md`](./73-formfill-deterministic-path-and-profiles.md) | Plan 73 — 폼필 결정적 경로 + 멀티턴 HITL 폼필 (v2) | *(미표기)* | 2026-08-24 |
| 74 | [`74-drm-decryption-servicelinker.md`](./74-drm-decryption-servicelinker.md) | Plan 74 — 양식 업로드 DRM 해제 (Softcamp ServiceLinker 연동) | **Phase 1·2·2b 구현 완료 (2026-08-12, D-156)** — 감지·라우트 대칭 배선·… | 2026-08-24 |
| 75 | [`75-realtime-usage-api-and-ux-review.md`](./75-realtime-usage-api-and-ux-review.md) | 75. UX 개선 기획 검토 의견 — 실시간 사용률 API · 버튼 명령어 · LIMIT 절단 · 존 모호성 역질문 | 검토 의견 v2. §1은 사용자 확정(2안 채택)·실측 완료로 **별도 구현 계획서(Plan 71)** 분리 — … | 2026-08-24 |
| 76 | [`76-execution-logging-and-failure-trace.md`](./76-execution-logging-and-failure-trace.md) | 76. 실행 관측 로깅 — 실행 SQL 파일 로그 + 실패 요청 단계 트레이스 (설계·구현 정리) | **구현 완료 (2026-08-19 랜딩 · D-140/D-141 등재)** — 본 문서는 실행 코드 실측(2026-08-25) 대조본. | 2026-08-25 |
| 77 | [`77-synonym-proposal-queue-and-approval-ui.md`](./77-synonym-proposal-queue-and-approval-ui.md) | 77. 유사어 제안 대기열 + 승인 웹 UI — 자동 캡처 · 선택적 영구 저장 (Synonym Proposal Queue & Approval UI) | **계획 (미구현)** — 사용자 요건 확정 3건 반영(§1.2). 착수 시 D-163 등재. | 2026-08-25 |
| 78 | [`78-composite-query-host-diagnostics-orchestration.md`](./78-composite-query-host-diagnostics-orchestration.md) | 78. 자원 조회 ↔ 장애 조사 배선 계층 — 대상 확정·경로 분화·미들웨어 조사 (구 제목: 복합 질의 오케스트레이션) | **부분 구현**(2026-08-27) — **W0·W1·W2·W3-1·4·5·W4·W5·W6·W7-1 완료**(`plans/80` 차수 3-A · 신규 테스트 166건 · 회귀 기준선 불변). **v15 실측 정정 4건**: §6.1 공통 모듈 배치 `orchestration` → **`src/utils`·`src/domain`**(소비자 둘이 application이라 계층 규칙상 import 불가 — **D-171**), `TargetRef`에 **`server_name` 추가**(`mcp_server` 도구별 식별 키 상이), W3-1 도구명 **`polestar_` 접두**(`process_snapshot`은 `source` 인자 없음), §6.2 캐시 TTL 기본 **0**(D-172). **남은 것**: W3-2·3(경로 선택 — WU-18 · S-2 선행) · W7-2(APM · R-11) · e2e 수용(3-B). **계획 (미구현) · v9 · 코드 변경 0건** — 사용자 확정 2건(수집기 어댑터 2백엔드 · 권고까지). 갭 G1/G2/G3 실측. v2 문헌 재조사(IPIGuard·Task Shield·Adaptive Attacks·P9). v3 해석/확정 분리(3단 계단 · 코드 생성 미채택 P10). **v4 ETCLOVG 7계층 적용 — 갭 3종 발견(호스트 인가 부재·V 미검출·대상 호스트 부하)**. v5 하네스 구현 명세 반영(세 결정 축 · Tier 규율로 착수 순서 정정 · KV 캐시 제약 · P13~P15). **v6 목표 재정의 — 실측 결과 조사 엔진(`sre_agent`)·두 진입점·해결방안이 이미 구현됨을 확인, W3(수집기 신설)·W4(권고 생성) 폐기하고 78을 "조회↔조사 배선 계층"으로 재정의. G1 정정·G4(미들웨어)·G5(진입점 비대칭) 신규. 신규 라이브러리 도입 0건.** **v7 미들웨어 데이터 소스 = APM 연계 확정(사용자) — D-119 경계 확장으로 편입, JMX 폐기, OS 근사는 폴백 존치, W7 2단 구조.** **v8: 79와 통합하지 않기로 확정 + §6.1/§6.2 정정(v6 어댑터 폐기 미반영분 해소).** **v9: §4 잔재 정정(폐기된 어댑터 설계를 그림·표·전용 절이 계속 제시하던 것 해소) + W3 하위 번호 드리프트 통일 + §8.4를 근거 문서로 강등(접점 정본은 `plans/80`).** 착수 순서는 **`plans/80` §5 Phase 3**부터 — 착수 시 D-164~168 등재 — **호스트 인가 모델은 사용자 확정 필요**. | 2026-08-26 |
| 79 | [`79-semantic-routing-improvement.md`](./79-semantic-routing-improvement.md) | 79. 시멘틱 라우팅 성능 개선 · **의도 추출 출력 계약** — 분류 지시문 · 출력 형식 · 신뢰도 · 구조화 출력 | **트랙 A 완료 · 트랙 E 완료 · 트랙 B 구조 구현 완료**(2026-08-27 · 보류 해제 · **D-173**). 트랙 B는 **플래그 기본 off**이며 발효 판정은 S-1·S-2 이후 — 이득/손해 자체가 미검증이다(근거가 **모델 크기 종속**: 1.5B −33.6 / 9B +11.2). 프롬프트는 쪼개지 않고 **절 상수로 추출**해 단일·2단 경로가 조립하며 렌더 **바이트 동일**을 골든으로 고정(S-1 기준선 오염 방지). **트랙 C는 라우터 평면 이동 후 이월**. **트랙 A 구현 진행 중** — IEEE Access 투고본 2요인 분해 실측 기반. **지시문 개선은 모델 무관 +9.2~17.6%p(정확도 1순위)**, label-only는 규모 종속·부호 반전(조건부). intent/DB 2단 분류가 선행. **v2: logprob은 성능 레버가 아니라 의도 신뢰도 측정·판정 인프라로 재정의 — 현행 `relevance_score`는 자기보고라 임계 0.3이 무력할 가능성, 저신뢰 되묻기·경로 차단·collapse 감지에 활용.** **v3: 프롬프트 전수 감사 — 규칙 5가 저신뢰를 원천 차단해 코드 게이트가 설계상 no-op, 클래스 정의 불일치(`synonym_registration` 누락), alarm_query 편중 7/19. 트랙 A를 A-1~A-8 조치로 재작성. v4: logprob은 **첫 생성 토큰**에서 나오므로 프롬프트를 **라벨 선행 구조**로 바꿔야 성립(C-0) — 형식·신뢰도는 완전 독립이 아니라 '제약이 있는 분리'. v5: 트랙 B(intent/DB 2단)를 선행조건→선택지로 정정. **v6: 멀티 DB 선택을 불변식으로 확정 → 순수 label-only(트랙 D) 배제, 신뢰도 이득은 라벨 선행 하이브리드로 취득. 산출물·설정·착수순서·회귀안전 절 신설. v7: intent/DB 분리가 label-only 이득의 유일 경로임을 확인해 트랙 B를 조건부 필수로 상향, D-004 정합성 검토(충돌 없음). v8: 오류 전파는 단일 호출과 동일함을 확인(autoregressive · intent 선행) — 분리의 실제 대가는 컨텍스트 대역폭이며 조기 차단은 오히려 이득. **v9: 78과 통합하지 않기로 확정(생명주기·범위·계층) — 단방향 의존 79→78, 게이트 직교(AND 결합), 랜딩은 79 선행.**** **v10: 트랙 A 구현 완료**(A-1~A-5 · A-7 불필요 판정 · 신규 테스트 12건). **운영 LLM은 Gemini가 아니라 FabriX(KBGenAI) → logprobs 원천 불가 → 트랙 C 전체를 라우터 평면 이동 후로 이월.** `synonym_registration` 누락 지적은 **오판으로 철회**(결정적 라우팅 대상). **v11: A-1 단독 적용은 승인된 예외**(트랙 C 이월로 C-4와 묶기 불가) · A-3에 **옵트인 클래스 3자리 조건부 제약**(78이 켜는 플래그 종속 — `plans/80` C-A) · **§8⑪ 신설**(라우터를 제어 평면으로 옮기면 트랙 C 즉시 성립 — 사용자 확인 사항).** 착수 시 D-169 등재. | 2026-08-26 |
| 80 | [`80-78-79-joint-execution-contract.md`](./80-78-79-joint-execution-contract.md) | 80. Plan 78·79 **공동 실행 계획** — 실행 구동(WU) · 게이트 · 공유 자산 소유권 | **v11 — LLM 평면 운영 정책 반영**(D-174): **vLLM=deep agents 전용 · 나머지 FabriX**. 게이트 이름 "vLLM 전환" → **"라우터 평면 이동"**(46곳), 이월 축은 "대기"가 아니라 **"정책상 미채택"**, **임계 정산은 C-4 → S-2로 이전**(J-8 해소). **실행 진입점 · 17/21 WU 완료**(2026-08-27 v9). **차수 3을 3-A/3-B로 분리** — 순서 계약 ①(WU-05 선행)이 막는 것은 **e2e 수용 검증**이지 구현·단위 검증이 아니다(78 §6.1 실측: 78이 수정하는 라우팅 파일 0건 → 트랙 A 롤백에도 W1~W17·W19 무효화 없음). 그 결과 **G-BILL 뒤에 묶여 있던 8개 WU가 먼저 랜딩**했다. 남은 것: WU-05·06(G-BILL) → WU-18 · 3-A e2e 수용 · WU-21 · 이월 D1~D3. **신규 결정 D-171·D-172**. **계획 (미구현)** — 78·79 **통합하지 않음**을 재확인(근거 5항, 다섯 번째는 실측: 79는 구현 진행 중·78은 코드 0건). **78·79 실행의 단일 출처**: 랜딩 순서 Phase 0~5 · 공유 자산 소유권 8종 · **미기술 접점 4건 계약화**(C-A 옵트인 클래스 프롬프트 3자리 조건부 / C-B 감사 스키마 소유권 / C-C AND 게이트 결합 지점 / 78 회귀 기준선의 79 종속). **v2: SPEC 실측 반영** — A-1 단독 적용은 **승인된 예외**(v1의 BLOCKER 판정 철회), **트랙 C는 FabriX KBGenAI라 logprobs 원천 불가 → 라우터 평면 이동 후 이월** → 랜딩 순서 전면 개정(**78은 트랙 C를 기다리지 않는다** · 감사 스키마 소유자도 78 W6로 뒤집힘). 착수 시 D-170 등재. | 2026-08-26 |
| — | [`README.md`](./README.md) | 인프라 데이터 조회 에이전트 - 구현 계획서 목차 | *(미표기)* | 2026-04-09 |
| — | [`multiturn_plan.md`](./multiturn_plan.md) | 멀티턴 대화 및 Human-in-the-loop 구현 계획 | *(미표기)* | 2026-03-23 |
| — | [`schemacache_plan.md`](./schemacache_plan.md) | Redis 기반 스키마 캐시 구현 계획 | *(미표기)* | 2026-04-09 |
| — | [`xls_plan.md`](./xls_plan.md) | Excel 양식 기반 데이터 조회 및 파일 작성 — 개선 계획 | *(미표기)* | 2026-04-09 |

## 상태 표기 있는 계획서만 (26건)

신규 계획서는 앞부분에 `> **상태**: …`를 넣는다. 이 인덱스가 기계적으로 읽는 유일한 근거다.

