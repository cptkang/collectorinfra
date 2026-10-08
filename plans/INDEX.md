# 계획서 인덱스 (`plans/`)

> **생성** 2026-08-20 (plans/70 D1) · **최종 갱신** 2026-10-08 · 갱신 이력은 [`INDEX-CHANGELOG.md`](./INDEX-CHANGELOG.md) 맨 위에 한 줄씩 추가한다(D-304).
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
- **상태 칸은 240자 이내 요약**(2026-10-06 · D-304) — 위 「기계적으로 그대로 옮긴 값」을 대체한다. 경과·잔여의 상세는 계획서 머리의 상태 표기에 쓰고, 여기에는 단계와 핵심 잔여만 적는다. 축약 전 원문은 [`INDEX-CHANGELOG.md`](./INDEX-CHANGELOG.md)에 보존돼 있다.

### 파일명 상태 접미사 (2026-09-03 신설 · 근거는 `plans/85` §11)

**구현이 끝나지 않은 계획서는 파일명의 번호 바로 뒤에 상태를 단다** (`NN-TODO-slug.md` · `NN-WIP-slug.md` 형태. 2026-09-09부터 — 종전에는 파일명 끝 `-TODO`/`-WIP` 접미사였다). 목록만 보고도 남은 일이 보이게 하기 위함이며,
`plans/85`가 드러낸 문제 — *"헤더 상태 표기를 믿었다가 이미 있는 것을 다시 만들 뻔한 사례"* — 에 대한
가장 값싼 방어다.

| 접미사 | 뜻 | 판정 기준 |
|---|---|---|
| **`-TODO`** | 완전 미구현 | 계획서가 요구한 산출물의 **코드가 0건** |
| **`-WIP`** | 부분 구현 · 잔여 있음 | 일부가 랜딩했으나 **미완 항목이 남음**(잔여의 성격은 본문 참조) |
| *(무표기)* | 구현 완료 · 로드맵 · 조사 문서 | 잔여 0이거나, 애초에 구현 단위가 아닌 문서 |

- **로드맵(53·55·62)에는 붙이지 않는다** — 구현 단위가 아니라 방향·순서 권고이고, 실체는 하위 계획서다.
  하위가 `-TODO`/`-WIP`를 달면 로드맵의 미완도 그로써 드러난다.
- **접미사를 떼는 시점 = 잔여 0**. 착수해서 일부만 랜딩하면 `-TODO` → `-WIP`로 **바꾸고**, 전부 끝나면 뗀다.
- **이관 조항(2026-09-09 · D-208)**: 잔여를 **다른 계획서로 이관**하면 원본은 무표기(시점 문서로 완결)하고 **이관처가 태그를 단다** — 로드맵 조항과 같은 구조다(태그는 잔여가 *있는 곳*에 붙는다). 이관처는 항목마다 원 계획 절 링크·소재지·verify를 적어야 하며(링크 없는 항목 등재 불가), 완료는 이관처의 행을 닫는 것으로 추적한다. 첫 적용: 50·51·66 → `91`.
- **떼거나 바꿀 때는 참조 링크를 함께 고친다.** 전 참조가 `<파일명>.md` 형태(확장자 포함)라
  정확 문자열 치환으로 안전하다 — 2026-09-03 전환 시 **72건**을 이 방식으로 갱신했고 깨진 링크 0을 확인했다
  (검증: `plans/` 상대 링크 122건 전수 실존 확인).
- **코드 주석의 참조는 대부분 번호만 쓴다**(`plans/82` 형태 · 실측 70건 중 68건) — 접미사 변경에
  영향받지 않는다. 전체 파일명을 쓰는 소수만 함께 고친다.

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
| 49 | [`49-WIP-phase2-dynamic-replanning.md`](./49-WIP-phase2-dynamic-replanning.md) | 49. Phase 2 — 결과 기반 동적 재계획 (deepagents 실제 패키지 · vLLM 오케스트레이터 + FabriX 응답처리) | **§12 검증 완료 + 결함 수정(2026-09-21 · 로컬 MLX · 과금 0)** *(상세: 계획서 머리)* | 2026-09-21 |
| 50 | [`50-fault-diagnosis-rca.md`](./50-fault-diagnosis-rca.md) | 50. 장애진단 · 원인분석 (Fault Diagnosis & Root Cause Analysis) | **완결(시점 문서 · 2026-09-09 D-208)** *(상세: 계획서 머리)* | 2026-09-02 |
| 50 | [`50-multiturn-context-and-control-plane-token.md`](./50-multiturn-context-and-control-plane-token.md) | 50. 멀티턴 컨텍스트 전파 개선 + 제어 평면(vLLM) 토큰 한계 대응 | *(미표기)* | 2026-06-26 |
| 51 | [`51-fault-diagnosis-data-collection.md`](./51-fault-diagnosis-data-collection.md) | 51. 장애분석 데이터 수집 및 진단 기법 (OS-Level Evidence Collection & Diagnostic Techniques) | **완결(시점 문서 · 2026-09-09 D-208)** *(상세: 계획서 머리)* | 2026-09-09 |
| 51 | [`51-streaming-scroll-ux.md`](./51-streaming-scroll-ux.md) | 51. 스트리밍 응답 UX 개선 (① 조건부 자동 스크롤 + 플로팅 버튼 · ② 표 가로 스크롤 보존) | *(미표기)* | 2026-06-26 |
| 52 | [`52-alarm-noise-cancellation.md`](./52-alarm-noise-cancellation.md) | 52. 알람 노이즈 캔슬링 — 중요도 기반 발송 판단 (Alarm Noise Cancellation & Notification Gating) | E1~E5 구현 완료 (E5: 2026-07-02, D-048.7 — 사용자 확정 §13.1#8 3경로 전체·메시지형 한정). | 2026-07-14 |
| 53 | [`53-fault-management-roadmap.md`](./53-fault-management-roadmap.md) | 53. 장애관리 기능군 구현 로드맵 (Fault-Management Capability Roadmap) | 로드맵 (구현 순서 권고). 착수 시 `docs/02_decision.md`에 **다음 빈 번호**로 등재 가능(현재 D-048까지 점유). | 2026-07-14 |
| 54 | [`54-noise-cancellation-dashboard.md`](./54-noise-cancellation-dashboard.md) | 54. 알람 노이즈 캔슬링 모니터링·관리 대시보드 (Noise Cancellation Dashboard) | **구현 완료 (F1~F3 · D-196)** — F4는 선택 미착수 | 2026-06-29 |
| 55 | [`55-multi-source-observability-roadmap.md`](./55-multi-source-observability-roadmap.md) | 55. 멀티소스 관측 확장 로드맵 — APM·DPM 연동 에이전트 (Multi-Source Observability Roadmap) | 로드맵 (방향·순서 권고, 미구현) | 2026-07-14 |
| 56 | [`56-TODO-langfuse-observability.md`](./56-TODO-langfuse-observability.md) | 56. LLM 관측성 확보 — Langfuse 통합 (LLM Observability with Langfuse) | 계획 (Phase L1~L4 미착수) | 2026-07-14 |
| 57 | [`57-polestar-b0-token-overflow-and-replan-misdiagnosis.md`](./57-polestar-b0-token-overflow-and-replan-misdiagnosis.md) | 57. 폴스타 b0 자원조회 토큰 폭증 + 재계획 오진 분석 및 해결 | *(미표기)* | 2026-07-09 |
| 58 | [`58-polestar-form-fill-db2-schema-and-excel-output.md`](./58-polestar-form-fill-db2-schema-and-excel-output.md) | 58. 파일 업로드 양식 채우기 — 폴스타 DB2 스키마 오류 · 공동존 서버 식별자 NULL · Excel 산출물 누락 분석 및 개선 | *(미표기)* | 2026-07-09 |
| 59 | [`59-a-role-based-admin-access-and-alarm-group-ui.md`](./59-a-role-based-admin-access-and-alarm-group-ui.md) | 59-a. 역할 기반 어드민 접근 정정 + 알림그룹 UI + 보호 root 계정 + 부서 편집 + 감사 로그 로테이션 | *(미표기)* | 2026-07-15 |
| 59 | [`59-admin-rbac-and-chat-ux-improvements.md`](./59-admin-rbac-and-chat-ux-improvements.md) | 59. 어드민 접근 체계 정합화(RBAC) 및 채팅 UX 개선 | *(미표기)* | 2026-07-15 |
| 60 | [`60-noise-cancellation-benchmark-refinement.md`](./60-noise-cancellation-benchmark-refinement.md) | 60. 노이즈 캔슬링 고도화 — 선진사례 벤치마킹 기반 수정·구현 계획 (Noise-Cancellation Benchmark Refinement) | **Wave A(E6·E1·E4) 구현 완료 (2026-07-21) — 사용자 §8 게이트 확정(B-1=AVAIL_DEPEND 단독·B-2=폴스타 이력·B-5… | 2026-07-27 |
| 61 | [`61-text2sql-candidate-selection.md`](./61-text2sql-candidate-selection.md) | 61. Text-to-SQL 쿼리 품질 고도화 — 다중 후보 생성 + 실행기반 선택 + 동의어 매칭 + 결정적 조합 (Candidate Selection + Synonym + Deterministic Composition) | **대부분 구현 — 트랙 A·B·C 착수 완료(2026-07-15, §12 참조)**. E1 하네스(D-072)·트랙 B(E5-1·E5-2 인프라+**런타임 … | 2026-07-16 |
| 62 | [`62-aiops-capability-master-roadmap.md`](./62-aiops-capability-master-roadmap.md) | 62. AIOps 전체 역량 구현 마스터 로드맵 (AIOps Capability Master Roadmap) | 로드맵 (방향·순서 권고, 미구현). 개별 기능 착수 시 각 하위 계획에서 `docs/02_decision.md`에 **다음 빈 번호**로 등재(현재 **등재… | 2026-07-27 |
| 63 | [`63-polestar-overfit-decoupling.md`](./63-polestar-overfit-decoupling.md) | 63. 폴스타 과적합 분리 — DB 어댑터 격리 + 공통 경로 LLM 일반화 (Polestar Decoupling & LLM Generalization) | **완료** (전 트랙 P1~P4, 2026-07-20, 커밋 affca22~9fd1917). 후속 EX 라이브 측정은 **DB 데이터 미적재로 유효 검증 불… | 2026-07-21 |
| 64 | [`64-automated-incident-investigation-and-response.md`](./64-automated-incident-investigation-and-response.md) | 64. 이벤트 자동 조사·진단 브리핑 및 장애 대응 오케스트레이션 (Automated On-Event Investigation, Triage Briefing & Response) | **구현 완료(위임) — 2026-09-09 재판정(`plans/50` §0.8 · D-207)**: CW-A~C(D-124·D-137)·§4.8 L1(D-108)·§8 권고(D-138) 완료, **코드 잔여 0**. 남은 관할 = §8.3 B-3 자동 조치 거버넌스(범위 외·착수 금지). L3는 60 §18 E8(=66 R11)로 이관. **`-WIP` 해제(2026-09-09 사용자 확정 U-G)** · B-3 관할 유지… *(상세: 계획서 머리)* | 2026-09-09 |
| 65 | [`65-noise-cancellation-mock-event-generator.md`](./65-noise-cancellation-mock-event-generator.md) | 65. 노이즈 캔슬링 목업 이벤트 생성기 (Mock Polestar Event Generator) | **구현 완료 (2026-07-27 · Plan 66 Wave 1-A · D-121 등재)** — `scripts/mock_polestar_events.py`… | 2026-07-27 |
| 66 | [`66-sre-agent-integrated-implementation-plan.md`](./66-sre-agent-integrated-implementation-plan.md) | 66. SRE-Agent 통합 구현 계획 — sre-agent 01~06 × Plan 60~65 종합 실행 시퀀스 | **완결(시점 문서 · 2026-09-09 D-208)** *(상세: 계획서 머리)* | 2026-09-09 |
| 67 | [`67-stepwise-llm-query-composition.md`](./67-stepwise-llm-query-composition.md) | 67. 단계적 LLM 쿼리 조립 + 경직성 해소 리팩토링 — deep agents vs semantic routing 비교 검토 | **완료(v19)** — 전 트랙 구현 + E1 A/B **재측정 확정**(동일 커밋 09183c8·caffeinate·슬립 0): EX 11/15 vs 9/15, 승 0·패 2, 지연 2배 → **stepwise 기본 OFF 확정**(D-128). 후속 별건 3건(b0 EAV 실측·hi 조인 키·taxonomy 노출)은 범위 밖 | 2026-09-10 |
| 68 | [`68-webui-env-settings.md`](./68-webui-env-settings.md) | 68. 설정 웹UI 전면 개편 계획 (v2 — 설정 코드 정밀 분석 반영) | **확정 — 착수 가능 (2026-07-29 사용자 승인)**. §9 게이트 5건 전부 권고안대로 확정 + **사용자 인터뷰 4건 확정(§9-보완)**. 옵션… | 2026-07-30 |
| 69 | [`69-query-generation-structural-refactoring.md`](./69-query-generation-structural-refactoring.md) | 69. 쿼리 생성 영역 구조 리팩토링 — 중복 통합·경로 대칭·계층 정리 | v9 — **계획·후속·별건 전량 완결. 잔여 0건.** P0~P5 구현·D-134 등재·문구 통일 적용·후속 3건에 이어 마지막 별건(EAV 검증 리터럴 이… | 2026-08-05 |
| 70 | [`70-WIP-codebase-scale-and-path-debt.md`](./70-WIP-codebase-scale-and-path-debt.md) | 70. 코드베이스 규모·경로 부채 정리 — 경로 일원화·플래그 감사·시맨틱 레이어 수렴 | **v4 — 코드 대조 검증 반영 완료. 게이트 1 즉시 해소 가능(관측 대기 소멸). 코드 변경 0건.** | 2026-08-20 |
| 71 | [`71-realtime-usage-api.md`](./71-realtime-usage-api.md) | 71. CPU/메모리 실시간 사용률 조회 — 폴스타 measurement API 데이터 평면 | **구현 완료 (2026-07-24)**. **옵트인 기본 OFF**(`POLESTAR_REST_REALTIME_USAGE_ENABLED=false`) — … | 2026-08-24 |
| 72 | [`72-fss-audit-form-multirow-header-and-month-pivot.md`](./72-fss-audit-form-multirow-header-and-month-pivot.md) | 72. 금감원 감사 취합자료 양식 폼필 지원 — 2단 병합 헤더 결합 · 월별 가로 피벗(M~M+5) · 도메인 밖 필드 정책 | *(미표기)* | 2026-08-24 |
| 73 | [`73-formfill-deterministic-path-and-profiles.md`](./73-formfill-deterministic-path-and-profiles.md) | Plan 73 — 폼필 결정적 경로 + 멀티턴 HITL 폼필 (v2) | *(미표기)* | 2026-08-24 |
| 74 | [`74-WIP-drm-decryption-servicelinker.md`](./74-WIP-drm-decryption-servicelinker.md) | Plan 74 — 양식 업로드 DRM 해제 (Softcamp ServiceLinker 연동) | **Phase 1·2·2b 구현 완료 (2026-08-12, D-156)** — 감지·라우트 대칭 배선·… | 2026-08-24 |
| 75 | [`75-realtime-usage-api-and-ux-review.md`](./75-realtime-usage-api-and-ux-review.md) | 75. UX 개선 기획 검토 의견 — 실시간 사용률 API · 버튼 명령어 · LIMIT 절단 · 존 모호성 역질문 | 검토 의견 v2. §1은 사용자 확정(2안 채택)·실측 완료로 **별도 구현 계획서(Plan 71)** 분리 — … | 2026-08-24 |
| 76 | [`76-execution-logging-and-failure-trace.md`](./76-execution-logging-and-failure-trace.md) | 76. 실행 관측 로깅 — 실행 SQL 파일 로그 + 실패 요청 단계 트레이스 (설계·구현 정리) | **구현 완료 (2026-08-19 랜딩 · D-140/D-141 등재)** — 본 문서는 실행 코드 실측(2026-08-25) 대조본. | 2026-08-25 |
| 77 | [`77-TODO-synonym-proposal-queue-and-approval-ui.md`](./77-TODO-synonym-proposal-queue-and-approval-ui.md) | 77. 유사어 제안 대기열 + 승인 웹 UI — 자동 캡처 · 선택적 영구 저장 (Synonym Proposal Queue & Approval UI) | **계획 (미구현)** — 사용자 요건 확정 3건 반영(§1.2). 착수 시 D-163 등재. · **v2(2026-09-22) 구현 현황 재검토(§0)**: 계획 산출물 **코드 0건**이라 `-TODO`를 유지한다. 갭 3층은 그대로 남아 있다. 앵커 드리프트와 사실 오류 17건을 정정하고, 그대로 구현하면 **동작하지 않는 설계 결함 2건**(D-1 승인 반영 키 · D-3 S3 이미 등록됨)을 포함해 결함 10건을 반… *(상세: 계획서 머리)* | 2026-09-22 |
| 78 | [`78-WIP-composite-query-host-diagnostics-orchestration.md`](./78-WIP-composite-query-host-diagnostics-orchestration.md) | 78. 자원 조회 ↔ 장애 조사 배선 계층 — 대상 확정·경로 분화·미들웨어 조사 (구 제목: 복합 질의 오케스트레이션) | **부분 구현**(2026-08-27) *(상세: 계획서 머리)* | 2026-08-26 |
| 79 | [`79-WIP-semantic-routing-improvement.md`](./79-WIP-semantic-routing-improvement.md) | 79. 시멘틱 라우팅 성능 개선 · **의도 추출 출력 계약** — 분류 지시문 · 출력 형식 · 신뢰도 · 구조화 출력 | **트랙 A 완료 · 트랙 E 완료 · 트랙 B 구조 구현 완료**(2026-08-27 · 보류 해제 · **D-173**). 트랙 B는 **플래그 기본 off**이며 발효 판정은 S-1·S-2 이후 *(상세: 계획서 머리)* | 2026-08-26 |
| 80 | [`80-WIP-78-79-joint-execution-contract.md`](./80-WIP-78-79-joint-execution-contract.md) | 80. Plan 78·79 **공동 실행 계획** — 실행 구동(WU) · 게이트 · 공유 자산 소유권 | **v11 — LLM 평면 운영 정책 반영**(D-174): **vLLM=deep agents 전용 · 나머지 FabriX**. 게이트 이름 "vLLM 전환" → **"라우터 평면 이동"**(46곳), 이월 축은 "대기"가 아니라 **"정책상 미채택"**, **임계 정산은 C-4 → S-2로 이전**(J-8 해소). **실행 진입점 · 17/21 WU 완료**(2026-08-27 v9). **차수 3을 3-A/3-B로 분리** *(상세: 계획서 머리)* | 2026-08-26 |
| 81 | [`81-host-availability-precheck.md`](./81-host-availability-precheck.md) | 81. 호스트 가용성 사전 판정 — 프로세스·OS 조회 및 조사 진입 전 게이트 | **구현 완료 (2026-08-28 · D-175 등재)** — W1~W7 전건. SRE Agent 처리 필요성 검토 포함(§6, 결론=필요·판정은 본체·게이트는 sre_agent). 신규 테스트 86건·신규 실패 0. | 2026-08-28 |
| 82 | [`82-WIP-multi-zone-sequential-query-and-solution-routing.md`](./82-WIP-multi-zone-sequential-query-and-solution-routing.md) | 82. 복합 질의 실행 그룹 — 존 순차 조회 · 대상 소재 탐색 · 솔루션 축 파이프라인 · 범위 사전 선택 · 빈 결과 원인 진단 | **v7.1(2026-09-22 — R-1~R-3·R-5~R-7 수정 · §0.5.7 · 2026-09-23 기록 복원)**: 코드·테스트 완료(커밋 `ae67749` · 전체 pytest 실패 집합 HEAD와 동일 23건) · D-249(2026-09-23 등재) · R-4는 `plans/103` P1-3 이관 · 82·113 통합 검토 요청은 미착수 중단(§0.5.8). 〔이하 v7 시점 서술〕 **v7(2026-09-2…** *(상세: 계획서 머리)* | 2026-09-22 |
| 83 | [`83-noise-feedback-and-alarm-view-level.md`](./83-noise-feedback-and-alarm-view-level.md) | 83. 노이즈 캔슬링 피드백 루프 개선 + 알람 표시 레벨 선택 + 설정 UI 커버리지 완성 | **구현 완료(2026-08-28)** *(상세: 계획서 머리)* | 2026-08-28 |
| 84 | [`84-query-prompt-history.md`](./84-query-prompt-history.md) | 84. 질의 프롬프트 이력 — 로컬 목록 탭 + 서버 감사 경로 복구 | **구현 완료(2026-08-29 · D-183 등재) + UI 개정(2026-08-31 — 세 번째 탭 → **접이식 왼쪽 사이드바**, 오른쪽 진행 패널과 대칭 · 접힘 조합 4종 · 기본 접힘+상태 기억)** *(상세: 계획서 머리)* | 2026-08-28 |
| 85 | [`85-unimplemented-plan-inventory.md`](./85-unimplemented-plan-inventory.md) | 85. 미구현 계획 인벤토리 — `plans/` 전건 실측 대조 | **v3(2026-08-31)** 조사·정리 문서(D-번호 미부여) *(상세: 계획서 머리)* | 2026-08-31 |
| 86 | [`86-alarm-to-query-prompt-handoff.md`](./86-alarm-to-query-prompt-handoff.md) | 86. 알람 → 질의 프롬프트 인계 — 카드에서 클릭, 질의응답에서 조회 | **구현 완료(2026-08-31 · D-192 등재)** *(상세: 계획서 머리)* | 2026-08-31 |
| 87 | [`87-WIP-jennifer-apm-integration.md`](./87-WIP-jennifer-apm-integration.md) | 87. 제니퍼(JENNIFER) APM 연동 — 미들웨어(WAS) 장애 진단·대응·복구 범위 확대 | **v5.1(2026-09-30 — §0.14) J8 구현(커밋 `910c622` · 병합 `cce30f3`)** · **F-4 수정(2026-10-01 — 401 사유에 HTTP 상태)**: 게이트웨이 소스 N개(`JENNIFER_SOURCES`·접두 키 · 단일 설정 = `default` · 부분 실패 · 실패·빈 인벤토리 30초 — F-3 흡수) · 도구 계약 추가만(`source_ids` · `source_id` · … *(상세: 계획서 머리)* | 2026-09-29 |
| 88 | [`88-sequential-dependent-composite-query.md`](./88-sequential-dependent-composite-query.md) | 88. 복합 질의 순차 의존 처리 — 선행 조회 결과가 후속 조회의 대상이 되는 파이프라인 | **완료(2026-09-10 · 태그 해제 — W1~W9 코드 전건 · `.env` 반영 · 실 검증 11건 · 코드 잔여 0. 후속(코드 아님): 단계 4·5 뒤 2차 3종 on 판단 · W4 실 평가(D-127) · R-E)** · 1차·2차 구현 완료(2026-09-09 · D-203 등재 — 원 D-198 병합 재부여) *(상세: 계획서 머리)* | 2026-09-09 |
| 89 | [`89-streaming-progress-status-line.md`](./89-streaming-progress-status-line.md) | 89. 스트리밍 응답 진행 상태 표시 — 커서 아래 현재 단계·경과 시간 · 무이벤트 구간 계측 | **완료(2026-09-10 · 태그 해제 — T0~T9 전건 · T4 핸들러 마일스톤 v4 · 코드 잔여 0. 후속(코드 아님): 실 브라우저 체감·playwright는 D-127 승인 대기)** · T0~T3·T5·T7·T9 랜딩(신규 테스트 52건+13건 · arch/overfit 0 · 실 LLM 0), D-204 본문 등재 완료(원 D-199, 상류 선점으로 재부여). 게이트 G-1~G-5는 권고안 채택. 원 계획:… *(상세: 계획서 머리)* | 2026-09-09 |
| 90 | [`90-thread-zone-scope-indicator.md`](./90-thread-zone-scope-indicator.md) | 90. 채팅창 폴스타(존) 스코프 표시·선택·해제 — 암묵 승계를 보이게 하고, 끊을 수 있게 한다 | **구현 완료(2026-09-09 · D-205 등재 — 원 D-200, 원격 병합 후 재부여)** *(상세: 계획서 머리)* | 2026-09-09 |
| 91 | [`91-WIP-fault-investigation-residual-consolidation.md`](./91-WIP-fault-investigation-residual-consolidation.md) | 91. 장애 조사 계획군 잔여 통합 — 50·51·66 이관 장부 (Fault-Investigation Residual Consolidation) | **§1.1 코드 항목 1-1~1-6 구현 완료(2026-09-10 · D-209 · `CAPABILITY-MAP-91.md` 6모듈 · 플래그 3종 기본 off+만료일 · sre_agent 358·mcp 222·noise_gate 1278·카탈로그 334·arch/overfit 0)** *(상세: 계획서 머리)* | 2026-09-09 |
| 92 | [`92-WIP-prometheus-openmetrics-integration.md`](./92-WIP-prometheus-openmetrics-integration.md) | 92. Prometheus 연동의 OpenMetrics 활용 — 노출 형식(exposition format)을 읽고·내보내고·백필하는 세 경로 | **v5(2026-09-23 — §5.2)**: 통합 회귀 재개(92 기인 신규 실패 0) · O0 잔여 실측 완료 · O5 실 스크레이프 통과(픽스처 job `polestar`) · **O2b 구현**(`metric_source.py` · 6판정 + 판정 보류 · OM off 바이트 동일) · 잔여 = O6 · D-210 본문 등재 · docs/27 §10. **v4(2026-09-22 착수 · 중단 — §5.1)**: G… *(상세: 계획서 머리)* | 2026-09-22 |
| 93 | [`93-benchmark-driven-config-simplification.md`](./93-benchmark-driven-config-simplification.md) | 93. 벤치마크 기반 환경변수 최적화·간소화 — 측정으로 노브를 줄인다 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 94 | [`94-feature-perf-scenario-suite.md`](./94-feature-perf-scenario-suite.md) | 94. 기능·성능 시나리오 자동 실행 하네스 — 프롬프트로 전 기능을 돌리고, 리포트를 분석기에 넘긴다 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 95 | [`95-WIP-itam-asset-management-db-integration.md`](./95-WIP-itam-asset-management-db-integration.md) | 95. 자산관리(ITAM) DB 연동 — MariaDB 방언 지원 + 서빙 개방 + 스키마 지식 정본화 | 부분 구현 — 트랙 A0(W-1~W-3)·W-4 소스 정의·W-5 엔진 값·트랙 S(W-S1~W-S3)·W-8·W-9·W-10·W-12(로컬)·W-12a·W-13 랜딩, 잔여는 **W-4 운영 연결·운영 활성화(104 C1~C7)뿐**. **v15(2026-09-30)**: G-2(`INST1`)·G-3(`SDQ000`·방화벽) 확정 · 운영 108테이블 6군 · 전 테이블 ERD 수집 도구 `scripts/itam_erd.…` *(상세: 계획서 머리)* | 2026-09-17 |
| 96 | [`96-scenario-run-20260915-remediation.md`](./96-scenario-run-20260915-remediation.md) | 96. 시나리오 run `20260915-131903` 분석 기반 개선 계획 — 무효 표본 제거 · 판정 계약 교정 · 확정 결함 해소 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 97 | [`97-sweep-artifact-triage.md`](./97-sweep-artifact-triage.md) | 97. 벤치 스위프 산출물 분석 — 반출 전 축소 · 단계 분리 · config 처분 연결 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 98 | [`98-run-20260915-code-fixes.md`](./98-run-20260915-code-fixes.md) | 98. run `20260915-131903` 확정 결함 코드 수정 계획 — 수정 단위 15종 · 앵커·전후 동작·검증 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 99 | [`99-rerun-experiment-design.md`](./99-rerun-experiment-design.md) | 99. 재측정 실험 설계 — 무엇을 답하려고 10시간을 쓰는가 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 100 | [`100-WIP-mlx-local-llm-provider.md`](./100-WIP-mlx-local-llm-provider.md) | 100. 맥북(Apple Silicon) 로컬 테스트 LLM — MLX provider 추가 | 부분 구현(CU-1~CU-7 랜딩 · §7 권장안 채택 D-222 · 잔여 = Phase 4 다도구 루프 품질 판단(R-1) · 캐시 상한 조정 · Ollama 비교 — §5 Phase 4 결과 · §9) *(상세: 계획서 머리)* | 2026-09-17 |
| 101 | [`101-TODO-ml-fault-diagnosis-prediction-rca.md`](./101-TODO-ml-fault-diagnosis-prediction-rca.md) | 101. ML 기반 장애 진단·예측·RCA — 폴스타·제니퍼·DPM 연계, HolmesGPT에 정량 증거 공급 | 계획(코드 0건 · 사용자 확정 게이트 G-1~G-11 대기(G-8 허용 확정) · 확인 사항 J-1~J-11 대기) *(상세: 계획서 머리)* | 2026-09-20 |
| 102 | [`102-WIP-polestar-itam-cross-system-routing.md`](./102-WIP-polestar-itam-cross-system-routing.md) | 102. 폴스타 ↔ 자산관리(ITAM) 교차 시스템 질의 — 답변 영역 라우팅 · 값 기반 키 브리지 · 식별자 소재 프로브 | **부분 구현(v6 · 2026-09-21 처분 반영)** *(상세: 계획서 머리)* | 2026-09-17 · **2026-09-23 D-251: 기준 운영 단 2단 — L-5 목표가 1단 off로 바뀜** |
| 103 | [`103-WIP-tier3-langgraph-full-parity.md`](./103-WIP-tier3-langgraph-full-parity.md) | 103. 사다리 3단 기능 동등성 — LangGraph 네이티브 구성으로 1단(deep_agent)·2단 전 기능을 3단에서 제공 | **부분 구현 · 잔여 있음**(2026-09-22 v1.2 · `plans/111` C-4·C-5 범위 — §4.1): P0-1(추적 프록시 서브그래프·인터럽트 호환) · P0-2(팬인 키 `task_outcomes` · 루프 한정 턴 초기화 — 2단 K-5 미해소) · P0-3 부분(SSE 서브그래프 종료 무시 — 인터럽트 감지는 P3와) · P1-1(`task_run` 서브그래프) · P2-1(`needs_plan` ·… *(상세: 계획서 머리)* | 2026-09-23 · **2026-09-23 D-251: 3단은 2단 대비 비교 arm — 계속 진행** |
| 104 | [`104-WIP-admin-db-structure-analysis.md`](./104-WIP-admin-db-structure-analysis.md) | 104. DB 구조 분석을 질의 경로 HITL에서 관리자 페이지로 — MCP 연결 DB 목록 · 신규 시스템 연동(스키마 수집·캐시 등록·준비도) · 스키마 변경·신규 내용 점검 · 구조 분석 초안·승인·버전 | **구현(잔여 있음) · 2026-09-17 사용자 지시로 작업 중지** *(상세: 계획서 머리)* | 2026-09-17 |
| 105 | [`105-TODO-hermes-agent-adoption-review.md`](./105-TODO-hermes-agent-adoption-review.md) | 105. Hermes Agent 도입 검토 — deepagents(사다리 1단) 대체 가능성 · 주요 기능 · 소스 실측 · 적용 형태 | 검토 완료 · 계획(미구현) — 사용자 확정 게이트 G-1~G-5 대기(§11). 사용자 지시 "deep agent대신 최근 발표한 에르메스 에이전트를 사용하려고 한다" → "github의 소스를 보고 다시 검토" → "주요 기능을 포함하여 계획파일로 정리". **★실측**: 태그 v2026.9.14(0.21.3) editable 설치가 공식 경로(wheel 빌드 코드 차단) · exact pin은 공급망 정책이라 `open…` *(상세: 계획서 머리)* | 2026-09-17 |
| 106 | [`106-TODO-harness-intent-understanding.md`](./106-TODO-harness-intent-understanding.md) | 106. 사용자 의도 파악 하네스 — 도구 호출 밖의 의도 이해 기법 문헌 조사 · 현행 대조 · 적용 계획 | 조사 완료 · 계획(미구현) — 사용자 확정 게이트 G-1~G-11 대기(§9). 권고 = H0(되묻기 골든셋·지표) → H1(슬롯 기반 결정적 명확화 게이트 · logprob 불요) → H2(해석 표시). **v2(2026-09-20)**: Claude·ChatGPT 공개 방식(§2.9) 이식 *(상세: 계획서 머리)* | 2026-09-20 |
| 107 | [`107-WIP-intent-grounded-prompt-rewrite.md`](./107-WIP-intent-grounded-prompt-rewrite.md) | 107. 의도 확정 후 프롬프트 재작성 — IntentFrame 기반 정규 질의(canonical query) 생성 · 원문/해석 이중 채널 · 소비자별 전환 | **부분 구현(v2.5 · 2026-09-21 · §12)** *(상세: 계획서 머리)* | 2026-09-21 |
| 108 | [`108-run-20260918-remediation.md`](./108-run-20260918-remediation.md) | 108. 시나리오 run `20260918-182507` 개선 — 재테스트가 성립하게 만드는 것부터 | **완결(시점 문서 · 2026-09-21 D-208 2차 적용)** *(상세: 계획서 머리)* | 2026-09-21 |
| 109 | [`109-WIP-config-simplification-consolidated.md`](./109-WIP-config-simplification-consolidated.md) | 109. config 간소화 통합 계획 — 벤치 기반 노브 축소(93) · 스위프 산출물 분석·판정 관문(97)을 한 곳에 | 부분 구현 · 잔여 있음 — **93·97 통합 이관처**(사용자 지시 2026-09-21 · D-208 2차 적용). 측정 기계(트랙 T·A~C · D-219 관문 · D-237 레벨 비교 · D-238 전수 정리)는 랜딩했으나 **처분 근거로 쓸 측정 결과 0건**(스위프 run `20260914-185540` 판정 자격 없음 — SQL 관측 0 · 역질문 56.4%). 현행 실행 가이드(93 v12 0~8단계 · 4단계… *(상세: 계획서 머리)* | 2026-09-22 |
| 110 | [`110-WIP-scenario-test-consolidated.md`](./110-WIP-scenario-test-consolidated.md) | 110. 시나리오 테스트 통합 계획 — 하네스(94) · run 분석(96·108) · 제품 수정(98) · 재측정 설계(99)를 한 곳에 | 부분 구현 · 잔여 있음 — **94·96·98·99·108 통합 이관처**(사용자 지시 2026-09-21 · D-208 2차 적용). 하네스 S0~S4·§15~§18·Y-10과 제품 수정 19건 랜딩, **판정 자격을 갖춘 기준선 run 0건**(폐쇄망 run 3건 전부 무자격 — 환경 누락 · 401 26.9% · 2단 타임아웃 52.4%). 현행 실행 가이드(무과금→과금 단계 · 사다리 3단 고정 · 재테스트 절차 ·… *(상세: 계획서 머리)* | 2026-09-22 · **2026-09-23 D-251 등재 — Q-1·G-6 종결 · 부록 B 기준선 안내서 · 다음: 기준선 run → CU-4·CU-5 → 재측정** |
| 111 | [`111-WIP-composite-query-task-frame-node-split.md`](./111-WIP-composite-query-task-frame-node-split.md) | 111. 복합 질의 — task 프레임 기반 프롬프트 재작성과 LangGraph 노드 분리(94 시나리오 실측 분석) | 부분 구현 · 잔여 있음 — **게이트 G-1~G-5 확정**(2026-09-21 권고안). run `20260918-182507` 복합 계획 45턴 중 **사용자 복합 17턴(합격 0)** · 재계획 재시도 23턴 · 결함 4종(존 선택 재진입의 알람 교정 우회 27건 · 재계획=재시도 · `sub_query` 발명·SQL 주입 · 과·미분해). **C-1~C-3 랜딩**(`COMPOSITE_TASK_FRAME_ENABLE…` *(상세: 계획서 머리)* | 2026-09-22 |
| 112 | [`112-noise-console-drilldown-and-help.md`](./112-noise-console-drilldown-and-help.md) | 112. 노이즈 캔슬링 관제 화면 — 항목별 세부 리스트 · 퍼널 단계 드릴다운 · 드로어 가림 수정 · 항목 설명(느낌표) · 추이 그래프 가독성 | **완료(2026-09-22 · D-247 · 잔여 0)** *(상세: 계획서 머리)* | 2026-09-22 |
| 113 | [`113-WIP-multi-db-zone-fanout-and-synthesis.md`](./113-WIP-multi-db-zone-fanout-and-synthesis.md) | 113. 멀티 DB 조회 — 존 단위 위치어의 전 DB 팬아웃과 DB별 결과 종합 (공동존 김포·여의도 동시 조회 결함) | **구현(2026-09-22 v3 · D-246 + v3 부기)** *(상세: 계획서 머리)* | 2026-09-22 |
| 114 | [`114-WIP-bench-seg1-run-20260922-remediation.md`](./114-WIP-bench-seg1-run-20260922-remediation.md) | 114. 벤치 캠페인 `run-closed` 1구간(general-1) 분석 — 측정이 성립하지 않은 이유와 수정 계획 | **부분 구현 · v1.5 · G-A(D-250)·G-E(D-265) 확정 · 잔여는 게이트 G-C·G-D·G-F 대기** *(상세: 계획서 머리)* | 2026-09-22 |
| 115 | [`115-TODO-settings-recommendation-surface.md`](./115-TODO-settings-recommendation-surface.md) | 115. 설정 권고값을 관리자 설정 화면에 세운다 — 벤치 측정 + 분석 근거를 한 자리에, 적용은 사용자가 누른다 | **분석·계획(코드 0건) · 게이트 G-1·G-2 대기** *(상세: 계획서 머리)* | 2026-09-23 |
| 116 | [`116-WIP-user-admin-manual.md`](./116-WIP-user-admin-manual.md) | 116. 사용자 매뉴얼 · 관리자 매뉴얼 — 캡처 화면이 들어간 HTML 두 권, 메인 화면에서 바로 연다 | **구현 완료 · 잔여 있음(D-252)** *(상세: 계획서 머리)* | 2026-09-23 |
| 117 | [`117-TODO-ml-noise-judgment.md`](./117-TODO-ml-noise-judgment.md) | 117. 머신러닝 기반 노이즈 판정 — 알람 "대응 필요 확률"을 게이트 보조 신호로 넣는다 | **분석·계획(코드 0건) · 게이트 G-1~G-8 대기** *(상세: 계획서 머리)* | 2026-09-23 |
| 118 | [`118-WIP-bench-seg2-composite5-remediation.md`](./118-WIP-bench-seg2-composite5-remediation.md) | 118. 벤치 캠페인 `run-closed` 2구간(composite-5) 완주분 분석 — 114 이후 신규 무자격 원인과 수정 계획 | **게이트 G-1~G-4 확정 · 잔여 구현(2026-09-28 · D-266 · §4.0-b)** *(상세: 계획서 머리)* | 2026-09-23 |
| 119 | [`119-WIP-scenario-run-20260923-perf-remediation.md`](./119-WIP-scenario-run-20260923-perf-remediation.md) | 119. 시나리오 run `20260923-103638` 분석 — 2단 기준선의 지연 분해와 성능 개선 수정 계획 | **코드 단계 구현 완료(v4 · D-268 · 커밋 없음) · 잔여 = 사용자 폐쇄망 재측정 R1·R2 · G-5 군 목표 등재 · 서술 예약 재산정 · T-6·Q-5 arm 측정** *(상세: 계획서 머리)* | 2026-09-28 |
| 120 | [`120-WIP-bench-ladder1-run-20260923-remediation.md`](./120-WIP-bench-ladder1-run-20260923-remediation.md) | 120. 벤치 캠페인 `run-closed` 사다리 단 구간(`ladder-1`) 분석 — 판정 오염 3건과 폼필 월 시리즈 무력화(유사어 오염 사슬) 수정 계획 | **코드 단계 구현 완료(v2 · D-269 · 커밋 없음) · 잔여 = 사용자 폐쇄망 작업**(F-0 audit → prune · I군 확인 이력 `담당자` 삭제 · G-3 확인 SQL · 표적 재측정 · `--cite-tier` 새 캠페인 — §6.2) *(상세: 계획서 머리)* | 2026-09-28 |
| 121 | [`121-WIP-intent-routine-hierarchical-planning.md`](./121-WIP-intent-routine-hierarchical-planning.md) | 121. 의도 기반 계층 계획 — 작업 유형 · 지침(작업 루틴) · 주/세부 단계 · 결과 작성 계획을 세우고, 소스 카탈로그로 조회할 DB·시스템을 판단해 기존 노드에 바인딩하고 여러 소스를 연쇄·병렬로 조회한다: 문헌 조사와 2단 기준 경로 개선 계획 | **구현 진행(묶음 A·B 랜딩 · 묶음 C 로컬 선행 조건 충족분 랜딩 · 커밋·재측정 전 — D-273) · v7 · 게이트 G-1~G-30 확정(D-270·D-272) · G-31·G-32 대기** *(상세: 계획서 머리)* | 2026-09-28 |
| 122 | [`122-WIP-scenario-pass-rate-and-coverage.md`](./122-WIP-scenario-pass-rate-and-coverage.md) | 122. 벤치 run `20260923-140539` 기반 시나리오 합격률·커버리지 개선 — 판정 상한 37.5% 해소(수동 검토 이관) · 헤드라인 정의 정합 · 커버리지 분모·실행 범위 확대 | **구현 진행(v8 · 트랙 T 기본 on · D-309)** — T-0~T-9 랜딩(사용자 커밋 `9cc8c6b`) · 잔여 = R2 SHA 확인 · R3·R4 재측정(사용자) · 오라클 배선(DB2 검수 뒤) · 사람 몫 3건 *(상세: 계획서 §17)* | 2026-10-06 |
| 123 | [`123-WIP-silent-wrong-answer-and-artifact-failure.md`](./123-WIP-silent-wrong-answer-and-artifact-failure.md) | 123. 그럴듯한 오답 방지 — 오용·실수·착각 미탐지 · 2단 결정적 고지 배선 복구 · R군 판정화 · 파일 산출 기대 정정 | **v5 · 구현 진행(D-279 랜딩 `f4501b2` · 게이트 G-1~G-16 확정 D-280 · 결함 A 교정 D-234 ③ 개정 · V·CT·S-4 D-282 · v5 작업 트리: S-1b(D-199 부기) · V-6 재판정 실행(run `20260923-103638` — R 판정 커버리지 19 → 123/159) · CT-5 R2-09C 교정 · 잔여 분류 §18 · v6 D-285: S-7(a) 대응 표 `con…**` *(상세: 계획서 머리)* | 2026-09-29 |
| 124 | [`124-system-intro-page.md`](./124-system-intro-page.md) | 124. 시스템 소개 페이지 — 스크롤 연동 3D 장면으로 「Agentic AI 기반 장애 대응 자동화」를 소개 | **v3 · 완료(잔여 0)** — 메인 머리글 「매뉴얼 ▾」 메뉴 「시스템 소개」로 연결(D-277 ② 부기 · 매뉴얼 U-10·캡처 갱신) · v2: 게이트 G-1~G-8 확정(D-277) · `/intro` 9장면 · 브라우저 실측(상·중·정지 · 모바일 · 키보드 · 자동 강등) · 테스트 10건(사실 가드·미연결) · v1: 참고 사이트(genailabs.kr) 기법 분석(장면 고정 스크롤 · 3D 정거장 카메라 비… *(상세: 계획서 머리)* | 2026-09-29 |
| 125 | [`125-WIP-four-source-intent-routing-and-composed-answer.md`](./125-WIP-four-source-intent-routing-and-composed-answer.md) | 125. 4소스(폴스타 · Prometheus · ITAM · 제니퍼) 의도 기반 조회와 조합 응답 — 계층별 권위 소스 지도 · 제니퍼 1급 처리기 · 교차 계층 엔터티 연결 · 조합 응답 계약: 문헌 조사와 구현 계획 | **G-14 (a) 범위 랜딩(2026-09-30 · D-283 · `-WIP`)** *(상세: 계획서 머리)* | 2026-09-30 |
| 126 | [`126-WIP-fabrix-rag-document-query.md`](./126-WIP-fabrix-rag-document-query.md) | 126. 본부매뉴얼(전산관리매뉴얼) · 아키텍처 설계문서 일반 질의 — FabriX Retrieval Connector(RAG) 연동 | **W1~W5 구현 완료(W6 관리자 매뉴얼 A-62·A-63 작성 · 캡처 0장) · 게이트 G-13~G-18 확정 · D-284 본문 등재(2026-09-30 · 원 D-280 병합 재부여)** *(상세: 계획서 머리)* | 2026-09-30 |
| 127 | [`127-WIP-doc-rag-source-routing-on-125.md`](./127-WIP-doc-rag-source-routing-on-125.md) | 127. 문서 RAG를 4소스 라우팅에 편입 — `plans/125` 멀티 소스 계약(소스 레지스트리 · 조건부 1급 처리기 · `views[]` · `allowed_sources` · 조합 응답) 위에 `plans/126` 엔진(`answer_from_documents`)을 5번째 소스 «규정·설계 근거»로 | **WIP — W1~W3·W5 구현 랜딩(2026-09-30 · 작업 트리 · 커밋 없음 · D-286)** *(상세: 계획서 머리)* | 2026-09-30 |
| 128 | [`128-TODO-llm-gateway-package-split.md`](./128-TODO-llm-gateway-package-split.md) | 128. LLM 호출 일원화 — 독립 LLM 게이트웨이 패키지 `llm_gateway/`(FabriX · vLLM · Gemini · MLX · Ollama 전부 · 본체·noise_gate·sre_agent는 HTTP API로 호출) | **v2 · 계획만(코드 0) · 게이트 사용자 확정 대기(미결 G-1 · G-5 · G-6 · G-13~G-15)** · **146과 별개 — 146 성공 시 미사용 예정**(2026-10-08) *(상세: 계획서 머리)* | 2026-10-08 |
| 129 | [`129-TODO-doc-rag-local-retrieval-package.md`](./129-TODO-doc-rag-local-retrieval-package.md) | 129. 자체 문서 RAG 검색 패키지 `doc_rag/` — 벡터 DB(Qdrant)·색인·하이브리드 검색·재순위를 직접 구성해 FabriX 리트리벌과 같은 계약으로 본체에 붙이고, 본체는 두 RAG를 `RAG_BACKEND`로 골라 쓴다(NWAgent1 `rag_parser` 참고) | **TODO — v1.2 · 계획만(코드 0) · 게이트 G-1~G-13 확정 · D-288 본문 등재(2026-09-30)** *(상세: 계획서 머리)* | 2026-09-30 |
| 130 | [`130-WIP-apm-instance-and-business-name-targeting.md`](./130-WIP-apm-instance-and-business-name-targeting.md) | 130. 제니퍼 조회 대상 해석 — 인스턴스 이름·업무명으로 묻기(전체 인스턴스 목록 검색 · 업무명 → 폴스타(SMS)·제니퍼 근거로 인스턴스 찾기) | **WIP — v1.3 구현(W1-D·W0~W6 · D-290 · 상한 없이 전건 D-296) · 잔여: M-5 승계 · 실 DB E6 · Q-2 운영 형식 · J0-L-b·J0-O · 3단 targets** *(상세: 계획서 머리)* | 2026-10-06 |
| 131 | [`131-TODO-catalog-v2-exposed-wrong-answers.md`](./131-TODO-catalog-v2-exposed-wrong-answers.md) | 131. 카탈로그 v2 이관이 드러낸 제품 오답 — 폼필 값·대상 계약 · 멀티 DB 존 대조 · 집계 의미 가드 (`plans/122` §14.4 이관) | **계획 · 구현 0건 · 게이트 G-1~G-7 대기** *(상세: 계획서 머리)* | 2026-09-30 |
| 132 | [`132-WIP-source-first-selection.md`](./132-WIP-source-first-selection.md) | 132. 데이터 소스 우선 선별 — 질의마다 조회할 소스(폴스타 · ITAM · 제니퍼 · 문서 · Prometheus)를 먼저 고르고, 고른 소스만 조회한다: 로컬 실측과 개선 방향 | **WIP — v1.6 · W0~W6 + ITAM 3회차 전 교정(D-319) + 2단 다중 시스템 존 게이트 task 위임 · 사용률 정의 개정 · 사용자 결정(대량 프로세스 조회 A · 기간 없는 추이 현행) · 잔여: 운영 2단 전환 · 내부망 재측정 · 가드 1·3단 확장(보류)** *(상세: 계획서 머리)* | 2026-10-07 (미커밋) |
| 133 | [`133-WIP-schema-asset-autogen.md`](./133-WIP-schema-asset-autogen.md) | 133. 스키마 자산 자동 생성 — 신규 DB(ITAM 등)의 프로필 키·유사어 시드·DB 전용 프롬프트 섹션을 스키마 파싱과 읽기 전용 데이터 조회로 만든다 | **WIP — v1.1 · W0~W8 구현(작업 트리 · 커밋 없음 · D-294) · 잔여: 운영(폐쇄망) 산출 · P2 실 LLM 실측** *(상세: 계획서 머리)* | 2026-10-01 (미커밋) |
| 134 | [`134-WIP-jennifer-question-coverage.md`](./134-WIP-jennifer-question-coverage.md) | 134. 제니퍼 전체 읽기 기능 활용 — 조회·조합·분석과 채팅·조사·알람 연계 | **WIP — v1.6 · D-300·D-302·D-310·D-313**: W0~W7 구현(W0~W2 커밋) · **W6 잔여**(창 = 요청 단위 해석 · 두 구간 비교 채팅 · 기간 순위) · **서버 미지정 = TPS 상위 20대 + 안내** — 잔여 W8·W10 *(상세: 계획서 머리)* | 2026-10-07 (미커밋) |
| 135 | [`135-WIP-itam-query-benchmark-trace-log.md`](./135-WIP-itam-query-benchmark-trace-log.md) | 135. ITAM 자산 질의 벤치마크 — 사용자 프롬프트 시나리오를 실제 사용자 경로로 돌리고, 생성 SQL·실행 결과·스키마 맥락을 개인정보 없이 기록해 ITAM 프롬프트 개선 근거로 쓴다 | **WIP — v1.4 · W0~W6 완료 · W8 1회차 키트(작업 트리 · 커밋 없음 · D-301 부기)** *(상세: 계획서 머리)* | 2026-10-06 |
| 136 | [`136-WIP-module-scoped-regression-policy.md`](./136-WIP-module-scoped-regression-policy.md) | 136. 회귀 테스트 간소화 — 구현 뒤에는 관련 모듈만, 전체는 사용자가 요청할 때만, 가능한 것은 병렬로 | **WIP — v1.1 · W1~W4 구현(`scripts/regress.py` · xdist · 전체 회귀 권고 블록 · 진행 중 계획서 14건 반영 · D-303 부기) · 잔여: A3 `--full` 조용한 환경 측정** *(상세: 계획서 머리)* | 2026-10-06 |
| 137 | [`137-WIP-per-db-hangul-identifier-policy.md`](./137-WIP-per-db-hangul-identifier-policy.md) | 137. DB별 한글 식별자 허용 정책 — SQL 한글 토큰 가드(D-104)를 DB 레지스트리 설정(`allow_hangul_identifiers`)으로 분기 · 허용 DB도 스키마 실재 식별자만 통과 · MariaDB 백틱 인용 인정 · 큰따옴표 컬럼(침묵 오답) 가드 | **WIP — v2 · W1~W8 구현(D-297) · 잔여: 폐쇄망 ITAM 실 질의 검증** *(상세: 계획서 머리)* | **WIP — v2.5** · W1~W8(D-297) · W9~W12(D-305) · W13(D-306 · 게이트 교정 §9.5) · 잔여: 폐쇄망 재검증 · #3 기준 테이블(41/80) 확인 |
| 138 | [`138-WIP-rag-forced-doc-routing.md`](./138-WIP-rag-forced-doc-routing.md) | 138. RAG 문서 검색 라우팅 개선 — 명시 지목 강제 라우팅 · 문서군 고정 · R&R 설명 보강 · 원문 검색 | **WIP — v1.1 · W0~W6 구현(D-307) · 잔여: 폐쇄망 배포 후 §6 재확인** *(상세: 계획서 머리)* | 2026-10-06 |
| 139 | [`139-WIP-itam-run1-schema-scope-token-limit.md`](./139-WIP-itam-run1-schema-scope-token-limit.md) | 139. ITAM 내부망 1회차 결과 교정 — 테이블 관리 정보 정의 기반 조회 대상 선별 · FabriX 입력 한도 초과 · 오류 문구 오표면화 | **WIP — W1~W7 구현(사용자 커밋 `9cc8c6b`·`8f9b1c4`·`bac814c` · W7 작업 트리) · 정의 자산·LLM 선별(상한 8)·용도 블록·백엔드 오류 감지·단일 예산 사다리 · 검증 교정 · 매뉴얼 A-67 · 잔여 내부망 2회차(시드 가져오기 → 대조 → 승인 → run)** *(상세: 계획서 §9)* | 2026-10-06 |
| 140 | [`140-WIP-itam-offline-asset-build-and-export-evidence.md`](./140-WIP-itam-offline-asset-build-and-export-evidence.md) | 140. ITAM 자산 외부망 생성 — 반출 로그로 자산을 만들고, 다음 반출부터 생성 근거(P1 결과·치환 코드값)를 담는다 | **WIP — W1~W4·W6 완료 · W5 빌더 경로만**(D-311) · 1회차 빌드: 조회 대상 98 · 정의 108 · 오류 0 · 잔여: 2회차 반출 뒤 빌더 재실행·모의 DB 행·`--p2` MLX 1회 *(상세: 계획서 §8)* | 2026-10-07 |
| 141 | [`141-rag-retrieval-stability-and-tuning.md`](./141-rag-retrieval-stability-and-tuning.md) | 141. RAG 리트리벌 안정화·성능 개선 — 플랫폼 비결정(간헐 0건 24%) 대응 · 0건 재시도 · 리트리벌 재구성 실험(본부 매뉴얼 벡터 기준값 0.5→0.0 등) · 측정 기반 채택 | **완료 — v1.3 · R2 채택(본부 매뉴얼 0건률 45%→2% · 적중률 47%→98%) · D-314** *(상세: 계획서 머리)* | 2026-10-07 |
| 142 | [`142-TODO-portal-feature-help-qa.md`](./142-TODO-portal-feature-help-qa.md) | 142. 포탈 기능 질의응답 — 채팅에서 포탈 기능·화면·용어를 물으면 매뉴얼을 근거로 사용 방법(입력 예 포함)과 개념을 답한다 | **TODO — 계획 초안(코드 0)** · 게이트 G-1~G-9 답 대기 · D-315 예약 · 근거 = 매뉴얼 원천 색인 · 2단 조건부 처리기 `portal_help` *(상세: 계획서 §6·§8)* | 2026-10-07 |
| 143 | [`143-WIP-itam-knowledge-asset-parity.md`](./143-WIP-itam-knowledge-asset-parity.md) | 143. ITAM 지식 자산 폴스타 동등화 — 폴스타 자산 전수 대응표 · 반출 근거로 Claude Code가 자산 작성 · 검증 CLI 통과분 외부망 직접 커밋 · ITAM 결정적 조립(데이터 템플릿) · 벤치 폐루프 효과 판정 | **WIP — 3회차 반출 준비·후속 외부망분 완료(`89773af`)** · 반출 SQL 식별자 남김 · 구버전·손상 P1 초안 경고 · 회차 이력 프롬프트 밖 이동(검증기 규칙 미도입 — 2026-10-08) · `### 코드값` 통일 · D-316 · 잔여: 내부망 P1 재실행 · 3회차 반출 · 재기저 · W0 · K2·K8 · 효과 판정 | 2026-10-07 |
| 144 | [`144-WIP-jennifer-and-cross-source-noise-cancellation.md`](./144-WIP-jennifer-and-cross-source-noise-cancellation.md) | 144. 제니퍼 노이즈 캔슬링과 폴스타×제니퍼 복합 캔슬링 — 제니퍼 단독 판정 정책 · 공통 호스트 키 · 크로스소스 사건 상관(원인 아래 증상 강등) · 앱 영향 축 정밀화: 문헌 조사와 구현 계획 | **WIP v1.2** — 외부망 W0~W5 구현(제니퍼 정책 경로 · 크로스소스 사건 상관 step 7.6 DASHBOARD 상한 · `app_impact` 사후 승격 · 정상 강등 shadow · 사건 피드백 · 매뉴얼 · 전 플래그 기본 off) · D-317·D-320 · 잔여 W6 내부망 shadow·W7 · Q-3 확인 *(상세: 계획서 머리)* | 2026-10-07 |
| 145 | [`145-WIP-itam-export-format-preserving-substitution.md`](./145-WIP-itam-export-format-preserving-substitution.md) | 145. ITAM 벤치 반출 값 치환 — 가리던 값을 run 안 일관 형식 보존 가짜 값으로 · 관계 등가류로 조인 보존 · 관문 확장 | **WIP — 구현 완료 · 3회차 반출 확인 대기** · 가리던 값을 run 안 일관 형식 보존 가짜 값으로(2단계 발급·조인 보존·IP 계층) · 관문 확장 · 감사 4라운드 High 0 · 모듈 회귀 실패 0 · U-1 = 현행 유지 · D-321 *(상세: 계획서 머리)* |
| 146 | [`146-itam-run3-results-correction.md`](./146-itam-run3-results-correction.md) | 146. ITAM 내부망 3회차 결과 교정 — 백틱 식별자 검증 누락 · 선별↔생성 테이블 불일치 · 서비스 연결 탐침 · 4회차 정답 판정 준비 | **완료(잔여 이관)** · W1 백틱 실존 검사(D-297 부기) · W3 0행 앞 턴 승계 · W4 지식 자산 · 오라클 7·탐침 10 · `--check-oracle --out` · 4회차 오라클 7/7 · 이관: W2·W7·F12 → 149 · L1 → 103 *(상세: 계획서 머리)* | 2026-10-08 |
| 147 | [`147-WIP-jennifer-multi-source-selection.md`](./147-WIP-jennifer-multi-source-selection.md) | 147. 제니퍼 다중 소스 선택 조회 — 은행존·공동존·레거시(이후 N개) 중 고른 소스만 · 소스별 사전 정의 단어 · 애매·무지목 되묻기 · 선택 승계 · 화면 소스 축 · 소스 추가 = 설정만 + 정합 점검 | **WIP — W0~W5 구현(2026-10-08 · 커밋 없음 · D-322)** · 잔여: 소스 단어 운영 확정(G-B) · 비대화 채널 되묻기·단어 둘 합집합 여부 확인 · 소스별 상태 행 화면 미노출 · SSE 종료 노드 쓰기 유실(기존 결함) *(상세: 계획서 머리)* | 2026-10-08 |
| 148 | [`148-WIP-fabrix-openai-tool-calling-proxy.md`](./148-WIP-fabrix-openai-tool-calling-proxy.md) | 148. FabriX 도구 호출 프록시 — OpenAI Chat Completions(+tools)로 받아 KBGenAI로 변환·호출, 응답을 OpenAI `tool_calls`로 반환하는 독립 서버 `fabrix_proxy/`(프롬프트 에뮬레이션) | **WIP — 1단계 개발 맥 완료(D.1~D.3) · 내부망 실행(D.4~D.6) 대기** · `fabrix_proxy/` PoC 서버·러너·런북(`POC_RUNBOOK.md`) · 테스트 A·B·C 통과 · 커밋 없음 · 통과 시 deepagents 등 연동 · 128과 별개 *(상세: 계획서 머리)* | 2026-10-08 |
| 149 | [`149-WIP-itam-run4-results-correction.md`](./149-WIP-itam-run4-results-correction.md) | 149. ITAM 내부망 4회차 결과 교정 — 서비스 연결 가이드 전제 오류 · 선별 밖 테이블 증가 · 이름 칸 등호 · 반출 판독 결함 | **WIP — 외부망 W1~W5 완료 · 5회차 대기** · 서비스 연결 1·2순위 반전 · 정의 manages · 이름 칸 등호 0행 1회 재생성(D-297 부기) · 반출 판독 교정 · closed 정책 한글 재키잉+`identifier` 12칸(D-301 부기) · 회귀 rc=0 · 잔여 W6·사용자 확인 2 *(상세: 계획서 머리)* | 2026-10-08 |
| — | [`README.md`](./README.md) | 인프라 데이터 조회 에이전트 - 구현 계획서 목차 | *(미표기)* | 2026-04-09 |
| — | [`multiturn_plan.md`](./multiturn_plan.md) | 멀티턴 대화 및 Human-in-the-loop 구현 계획 | *(미표기)* | 2026-03-23 |
| — | [`schemacache_plan.md`](./schemacache_plan.md) | Redis 기반 스키마 캐시 구현 계획 | *(미표기)* | 2026-04-09 |
| — | [`xls_plan.md`](./xls_plan.md) | Excel 양식 기반 데이터 조회 및 파일 작성 — 개선 계획 | *(미표기)* | 2026-04-09 |

## 상태 표기 있는 계획서만 (27건)

신규 계획서는 앞부분에 `> **상태**: …`를 넣는다. 이 인덱스가 기계적으로 읽는 유일한 근거다.

