# 계획서 인덱스 (`plans/`)

> **생성** 2026-08-20 (plans/70 D1) · **갱신** 2026-08-25 (76 추가) · **전건 81개** (최상위 `plans/*.md` 기준)
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
| 66 | [`66-sre-agent-integrated-implementation-plan.md`](./66-sre-agent-integrated-implementation-plan.md) | 66. SRE-Agent 통합 구현 계획 — sre-agent 01~06 × Plan 60~65 종합 실행 시퀀스 | **Phase 1~3 구현 완료 — MVP 실증**(2026-07-28 실 Gemini 조사 완주). Phase 4~5·잔여 항목은 §1.4 스냅샷(2026-… | 2026-08-06 |
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
| — | [`README.md`](./README.md) | 인프라 데이터 조회 에이전트 - 구현 계획서 목차 | *(미표기)* | 2026-04-09 |
| — | [`multiturn_plan.md`](./multiturn_plan.md) | 멀티턴 대화 및 Human-in-the-loop 구현 계획 | *(미표기)* | 2026-03-23 |
| — | [`schemacache_plan.md`](./schemacache_plan.md) | Redis 기반 스키마 캐시 구현 계획 | *(미표기)* | 2026-04-09 |
| — | [`xls_plan.md`](./xls_plan.md) | Excel 양식 기반 데이터 조회 및 파일 작성 — 개선 계획 | *(미표기)* | 2026-04-09 |

## 상태 표기 있는 계획서만 (22건)

신규 계획서는 앞부분에 `> **상태**: …`를 넣는다. 이 인덱스가 기계적으로 읽는 유일한 근거다.

