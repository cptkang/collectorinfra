# Capability Map: 장애 조사 잔여 통합 (`plans/91` · D-208 이관 장부 §1.1 코드 착수 7항목)

> **작성일**: 2026-09-10 · **근거**: `plans/91-WIP-fault-investigation-residual-consolidation.md` §1.1·§2 · 설계 정본은 각 원 계획 절
> (`plans/50` §0.8.3 C′-0~C′-3 · `plans/51` §6 · `plans/60` §18 · `plans/83` D-177).
> **전제**: A′·B′(D-197 6모듈) 완료 위의 증분. LLM 호출 0(D-035) · 신규 플래그 기본 off + 만료일(D-161 C1) · 실 조사·실 LLM은 건마다 D-127.
> **상태**: **6모듈 구현 완료(2026-09-10 · D-209 등재)** — 검증 수치·전제 정정은 D-209. 잔여: `residual-doc-sync`(항목 완료마다) · 1-8~1-10 판정 · §1.3 외부 대기.
> **기준선(2026-09-10)**: `sre_agent/tests` 325 passed · `mcp_server/tests` 219 passed · 본체 전체 6,167 passed(기존 실패 31 — plans/88 §11.5 귀속표).

## ASSUMPTIONS I'M MAKING (사용자 지시 *"다음 계획을 구현하라"* 의 해석 · 2026-09-10)

1. **"다음 계획" = `plans/91`**. 89는 잔여가 "체감 확인 뒤 필요 시" 보류 T4와 과금 승인뿐이고, 87은 외부 시스템·게이트 G-1~G-7 대기라 착수 불가.
2. **착수 순서는 §2 그대로**: 1-1 → 1-2 → 1-3 · 1-5·1-6 병렬 · 1-4는 1-1 뒤 · 1-7은 항목 완료마다. 1-8~1-10(판정)은 사용자 몫 — 착수 블로커 아님.
3. **1-2에 플래그를 둔다**(`EVIDENCE_CHANGE_OVERLAY_ENABLED` 기본 off · 만료 2027-03-10). plans/91 표에는 플래그가 없지만 §2 규칙("신규 플래그는 기본 off + 만료일")이
   우선하고, 기존 테스트가 배치 길이 5·`to_dict` 키 집합에 결합돼 있어 무플래그 추가는 "off 비트 동일"을 깬다.
4. **1-3 전제 정정(실측)**: `meta.cluster`는 `{representative_fp, member_seq, similarity}`뿐 — **멤버 서버명이 없다**(`alarm_worker._detect_correlated_storm:1579`).
   `root_resource`는 리소스 **ID**이고 이름은 `signals["root_resource_name"]`에만 있어 페이로드에 실리지 않는다(`investigation_trigger.py:145-152`). 따라서 C′-1은
   **`root_resource_name` 1건을 페이로드 meta에 실어(noise_gate 생산자 측 1줄) 조사 측이 소비**하는 형태로 축소하고, 클러스터 멤버 소비는 게이트가 멤버명을
   보존하지 않으므로 **본 회차 범위 밖**(§1.3 승격 후보)으로 둔다. `src`/`noise_gate` 수정 0 전제(50 §0.8.3)는 이 1줄만 예외 — 대안(사전수집이 `polestar_topology`
   재조회)은 D-207이 기각(이중 계산).
5. **1-1의 무과금 부분은 이미 절반 있다**: `test_investigation_dispatcher::test_prefetch_result_lands_on_job_and_is_audited`가 `prefetch` 감사(`leading_signal`)를
   단언한다. 남은 것은 **브리핑 계약**(가설 rank·confidence · `T-` 타임라인 · limitations)을 스텁 경로에서 dispatcher→briefing 끝까지 단언하는 것과, RUN_E2E 게이트
   파일에 같은 단언을 실 경로용으로 추가하는 것(실행은 D-127 승인 뒤).
→ 틀린 가정은 플래그 off로 되돌릴 수 있다. 4번은 사용자에게 보고한다.

## 모듈

| Module id | 책임 | 소비자 | 패키지 | Depends on | 91 항목 |
|---|---|---|---|---|---|
| `correlation-e2e-assertions` | 상관 on 경로의 **브리핑 계약 단언** — 스텁(레벨 A · 과금 0): 가짜 배치 → `prefetch_and_correlate` → dispatcher → `build_briefing` 끝까지 `root_cause_hypotheses`(rank·confidence)·`T-` 타임라인·`limitations`·`prefetch` 감사 단언. 실 경로(RUN_E2E): 같은 단언을 JobStore 경유로 | 회귀 게이트 | `sre_agent/tests` | — | 1-1 |
| `change-event-overlay` | `polestar_change_history`에 `reference_time`·`lookback_minutes` 선택 인자(G1 동형 · 미지정 시 SQL 문자열 동일) → 사전수집 배치 **말미**에 변경 이력 1건(플래그 on일 때만) → `TimelineItem.kind="change"` + `change_finding` → *"변경 직후"* 가설(변경 offset < 첫 알람 offset이면 rank 1 · confidence 상한 medium) · DB2 소스는 결정적 한계 문장 | `diagnosis-briefing`(기존) | `mcp_server` + `sre_agent` | `correlation-e2e-assertions`(검증 경로) | 1-2 |
| `related-host-consumption` | 페이로드 `meta.root_resource_name` 소비 → `EvidenceScope.related_servers`(상한 3) → 연관 서버당 **알람 1건**만 추가 수집 → `AlarmPoint.server` · 타임라인 서버명 접두 · `notes` *"연관 서버 X의 첫 알람 T-Nm — 대표보다 선행"*. 플래그 `EVIDENCE_CORRELATION_RELATED_HOSTS`(기본 0=off · 만료일). `polestar_topology` 호출 없음 | `diagnosis-briefing` | `sre_agent` + `noise_gate`(페이로드 1줄) | `change-event-overlay` | 1-3 |
| `investigation-feedback-ref` | Plan 83 피드백 레코드에 `investigation_id` 선택 필드 — 존 RBAC·작성자·철회 재사용, 별도 저장소 없음. 기존 알람 피드백 경로 비트 동일 | 운영자(UI) | `noise_gate` + `src/api/routes/alarm.py` | `correlation-e2e-assertions` | 1-4 |
| `playbook-guidance` | `plans/51` §6 6유형을 알람 kind별 **결정적 문구**로 `build_guidance()`에 편입(LLM 0). 미매칭 kind는 종전 지침과 문자열 동일 | 조사 LLM(system_prompt_additions) | `sre_agent` | — (병렬) | 1-5 |
| `l3-postgate-enrichment` | E8 post-gate 비차단 보강 — 허용목록 명령 채널 어댑터 `host_diagnostic_collector` + post-gate 결정적 요지 첨부 + 상태지문 dedup. 플래그 `l3_enrichment_enabled`·`l3_audit_enabled`(기본 off · 만료일). 동기 경계 probe 없음(U-H (나)) | 통보 수신자 | `noise_gate` | — (병렬) | 1-6 |
| `residual-doc-sync` | `plans/85` §4 A-1/A-2·§9 · `plans/53` Wave 4 · `plans/91` 상태 행 갱신 — 항목 완료마다 | 계획 독자 | `plans/` | 각 모듈 | 1-7 |

**Build order**: `correlation-e2e-assertions` → `change-event-overlay` → `related-host-consumption` → `investigation-feedback-ref`
· `playbook-guidance` · `l3-postgate-enrichment` 는 **독립(병렬)** · `residual-doc-sync`는 매 모듈 뒤.

## 경계가 이렇게 그어진 이유

- **`correlation-e2e-assertions`가 맨 앞**: 뒤 두 모듈(변경·연관 호스트)이 타임라인·가설·한계에 무엇을 더하든, 그것이 **브리핑까지 도달했는지**를
  단언하는 경로가 먼저 있어야 한다(50 A′-0과 같은 이유). 스텁 경로가 있으면 뒤 모듈은 과금 0으로 검증된다.
- **`change-event-overlay`가 `mcp_server`와 `sre_agent`에 걸치는 이유**: 앵커 SQL은 읽기 경계(D-089·50 §0.4-b), 해석·가설은 조사 측. 두 패키지는
  import 0(MCP 계약만)이므로 도구 인자 계약(`reference_time`·`lookback_minutes`·응답 `window`)이 유일한 결합점이다.
- **`related-host-consumption`이 페이로드 1줄을 요구하는 이유**: 가정 4 — 게이트가 계산한 결과를 조사 측이 소비하려면 그 결과가 페이로드에 있어야
  하는데 이름이 빠져 있다. 재계산(토폴로지 재조회)은 D-207이 기각했다.
- **`playbook-guidance`·`l3-postgate-enrichment`가 독립인 이유**: 각각 `investigation_guidance.py` 단일 파일·`noise_gate` 단일 어댑터로 닫히고
  상관 파이프라인 파일을 건드리지 않는다.
