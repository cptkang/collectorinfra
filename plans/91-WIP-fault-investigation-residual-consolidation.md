# 91. 장애 조사 계획군 잔여 통합 — 50·51·66 이관 장부 (Fault-Investigation Residual Consolidation)

> 작성일: 2026-09-09
> **성격**: **이관 장부(residual ledger)** — `plans/50`(RCA 축)·`plans/51`(증거 카탈로그·플레이북)·`plans/66`(SRE-Agent 실행 시퀀스)이 각자 `-WIP`로 들고 있던 잔여를 **한 파일로 모아 단일 소유**한다. 세 원본은 이관과 동시에 `-WIP`를 떼고 시점 문서로 완결된다(INDEX 「이관 조항」 · D-208). 설계 정본은 여전히 각 원 계획서의 해당 절이며, 본 계획은 **무엇이 남았고 어떤 순서·조건으로 착수하는가**만 적는다.
> **상위 로드맵**: Plan 62 P2(진단·RCA)·P4 · Plan 53 Wave 4. **선행 정리**: `plans/50` §0.8(D-207 — 표류 잔여 8항목 단일 소유 확정 · U-F~U-I 사용자 확정) · `plans/64`(2026-09-09 재판정 — 코드 잔여 0 · 무표기).
> **관련 결정**: D-207(계획군 단일 소유 · Phase C′ 구체화) · **D-208**(본 이관 + INDEX 이관 조항) · D-197(50 G1~G6) · D-189(L3 경로 B) · D-117(E8 — ② 개정: post-gate만) · D-138(권고) · D-177(83 피드백 루프) · D-127(과금 승인 게이트) · D-161(플래그 만료일).
> **상태**: **`-WIP`(2026-09-10 · D-209)** — §1.1 코드 항목 **1-1~1-6 구현 완료**(`CAPABILITY-MAP-91.md` · SPEC 2건 · 신규 테스트 60여 건 · 플래그 3종 기본 off + 만료일). 잔여 = 1-7 문서 정합(항목 완료마다) · 1-8~1-10 판정(사용자) · §1.3 외부 대기 6건. 전부 끝나면 무표기. 외부 선행조건 항목(§1.3)은 해소 여부를 본 파일 §1.3 표에서만 추적한다.

---

## 0. 규칙 — 이 장부가 퇴화하지 않기 위한 세 가지

1. **항목마다 원 계획 절 링크·소재지·verify를 반드시 적는다.** 링크 없는 항목은 등재 불가(잔여 보관함 방지).
2. **한 항목은 한 곳에만.** 원 계획서에는 *"잔여는 `plans/91` §1-N 소유"* 한 줄만 남기고 본문 잔여 서술은 이력으로 보존한다(`plans/85` §1 교훈).
3. **완료 시 원 계획서가 아니라 본 파일의 행을 닫는다**(상태 열 갱신 + §5 이력). 원 계획서는 건드리지 않는다 — 다만 그 항목이 원 계획의 결정(D-번호) 상태를 바꾸면 `docs/02_decision.md`를 갱신한다.

## 1. 이관 항목 (2026-09-09 실측 · 전건 코드 0건)

### 1.1 코드 착수 가능 (블로커 없음)

| # | 항목 | 원 계획·절 | 소재지 | verify | 선행 | 상태 |
|---|---|---|---|---|---|---|
| **1-1** | **C′-0 e2e 상관 단언** — `test_investigation_e2e.py`에 `evidence_correlation_enabled=True` 경로: `root_cause_hypotheses`(rank·confidence)·상대시각 타임라인(`T-`)·`limitations` 단언. 무과금 부분 선행: 레벨 A 스텁 조사에서 dispatcher 감사 이벤트 `prefetch`(`leading_signal`) 기록 단언 | `plans/50` §0.8.3 C′-0 · `docs/23` §7 | `sre_agent/tests/test_investigation_e2e.py` | 스텁 경로 과금 0 · 실 완주는 **D-127 건별 승인** 후 RUN_E2E=1 | 없음 | **완료(2026-09-10 · D-209)** — 스텁 경로 브리핑 계약 단언 2건(`test_investigation_dispatcher`) + RUN_E2E 실 경로 1건(`test_investigation_e2e` · 실행은 D-127). 실측: 기존 e2e 파일은 전부 RUN_E2E 게이트라 무과금 스텁은 dispatcher 테스트에 둠. **실 경로 시도(2026-09-10 · 승인 1건)**: 1차는 루트 cwd에서 `tests` 패키지 충돌로 수집 실패(과금 0) · 2차(sre_agent cwd · 키 환경변수 · 9097 조사 프로파일)는 조사 LLM이 **Gemini 무료 등급 분당 5회 쿼터(429 RESOURCE_EXHAUSTED)**에 막혀 실패 — 조사 1건이 최대 40 step이라 무료 등급으로는 완주 불가. 실 경로 확인은 **§1.3 1-17로 이관**(2026-09-10 사용자 확정 (c) — 스텁 경로가 브리핑 계약을 고정하므로 실 경로는 환경 확인의 의미만 남는다) |
| **1-2** | **C′-2 변경 이벤트 오버레이** — `polestar_change_history`에 `reference_time`(G1 동형 · 미지정 시 SQL 문자열 동일) → prefetch 배치에 변경 이력 1건(PG만 · DB2 b0는 `notes` 한계) → `TimelineItem.kind="change"` + `change_finding` → *"변경 직후"* 가설 규칙(변경 offset < 첫 알람 offset이면 rank 상승 · confidence 상한 유지). 게이트 E5와 상호 호출 없음 | `plans/50` §0.8.3 C′-2 · §4.5 | `mcp_server/mcp_server/polestar_tools.py` · `sre_agent/sre_agent/application/evidence_prefetch.py` · `domain/correlation.py` · `application/briefing_builder.py` | SQL 스냅샷 · 골든(변경 T-20m → 알람 T-10m ⇒ 가설 1위 "변경") · PG 전용 한계 문장 · 도구 실패 시 나머지 상관 보존 | 1-1 권장(검증 경로 확보) | **완료(2026-09-10 · D-209)** — `polestar_change_history(reference_time, lookback_minutes)`(미지정 시 SQL 동일 · epoch 창) · 플래그 `EVIDENCE_CHANGE_OVERLAY_ENABLED`(기본 off · 만료 2027-03-10) · `ChangePoint`·`kind="change"`·`change_finding` · "변경 직후" 가설 rank 1 medium · DB2 한계 문장 |
| **1-3** | **C′-1 다중서버 연쇄 소비** — `scope_from_job`이 `payload["meta"]["root_resource"]`·`["cluster"]` 멤버를 읽어 `EvidenceScope.related_servers`(상한 **3** · U-I 확정) → 연관 서버당 알람 1건만 추가(지표는 대표 서버만) → `AlarmPoint.server` · 타임라인 서버명 접두 · `notes` *"연관 서버 X의 첫 알람 T-Nm — 대표보다 선행"*. **`polestar_topology` 호출 없음**(게이트 E4 판정만 신뢰). 플래그 `EVIDENCE_CORRELATION_RELATED_HOSTS`(기본 0=off · **만료일 부여** D-161 C1). **착수 시 `meta.cluster` 실 shape는 `_detect_correlated_storm` 반환값으로 실측**(추정 금지) | `plans/50` §0.8.3 C′-1 · §6.3 · `plans/60` E2·E4 | `sre_agent/sre_agent/application/evidence_prefetch.py` · `domain/correlation.py` · `application/briefing_builder.py` | 골든(연관 서버 알람 선행 시 notes 문장) · 멤버 상한 · **메타 부재·플래그 off 시 종전 결과 비트 동일** · 연관 서버 호출 부분 실패 시 대표 결과 보존 | 1-2 뒤 | **완료(2026-09-10 · D-209 · 전제 정정)** — 실측: `meta.cluster`엔 멤버명이 없고 `root_resource`는 ID → 생산자 측 1줄(`meta.root_resource_name` · 값 있을 때만 키)로 축소. **클러스터 멤버 소비는 폐기(2026-09-10 2차 실측)**: 상관 억제 이벤트는 SUPPRESS 티어라 조사가 트리거되지 않고(`notification_policy.py:559` · `investigation_trigger.py:78` min_tier PAGE), 대표(첫 도착)의 조사는 멤버 도착 **전**에 뜬다 — 멤버 목록이 조사 페이로드에 실릴 시점이 없다. 사용자가 진행을 승인했으나 실측 근거로 미구현(되돌리는 비용: 후속 조사 재스코프 설계가 생기면 `ClusterState.member_hosts` 확장 약 60줄). `EVIDENCE_CORRELATION_RELATED_HOSTS`(기본 0=off · 만료 2027-03-10) · 연관 서버 알람 1건 · `AlarmPoint.server` · 선행 notes · 부분 실패 보존 |
| **1-4** | **C′-3 진단 피드백 — Plan 83 피드백 루프 편승**(U-F 확정 (ii)) — 피드백 레코드에 `investigation_id` 선택 필드, 존 RBAC·작성자 기록·철회 재사용, 별도 저장소 없음. 이력 조회는 `JobStore` audit JSONL 그대로 | `plans/50` §0.8.3 C′-3 · §9.3 · `plans/83`(D-177) | `noise_gate`(피드백 도메인·저장) · `src/api/routes/alarm.py`(라우트 — D-139 예외 소재지) | 존 RBAC 거부 · 철회 · **기존 알람 피드백 경로 비트 동일** | 1-1 뒤(순서 무관) | **완료(2026-09-10 · D-209)** — `FeedbackStore.record_feedback(investigation_id=)`(값 있을 때만 키) · `POST /alarm/feedback` 요청 필드 · few-shot 렌더 미노출 · 존 RBAC·철회 재사용. **UI 노출 완료(2026-09-10 · 사용자 확정)**: 트리거 인라인 경로가 `state.investigation_id`를 남기고(rejected 제외), notifier가 인라인·후속(`investigation_pending`) 어느 경로든 `AlarmAnalysisResult.investigation_id`에 실어 SSE 티어·incident 페이로드에 값 있을 때만 키로 내보낸다. `app.js` 피드백 전송에 `investigation_id` 포함 |
| **1-5** | **플레이북 내용 편입** — `plans/51` §6 6유형(CPU 포화·메모리/OOM·디스크/inode/IO·네트워크·프로세스 다운/플래핑·로그 패턴)을 **알람 kind별 결정적 문구**로 `build_guidance()`에 싣는다(LLM 아님 · D-035). 편입점은 D-197 `investigation_guidance_extra`가 이미 제공. `sre_agent`는 `overfit_check` 스캔 밖 — 폴스타 리터럴은 1-8과 함께 판정 | `plans/51` §6 · §4 · `plans/50` §0.8.2 ③ · `plans/85` §9 티어 1 | `sre_agent/sre_agent/application/investigation_guidance.py` | kind별 문구 골든 · 미매칭 kind는 종전 지침과 문자열 동일 · `system_prompt_additions` 도달 단언(기존 `test_investigation_guidance.py` 확장) | 없음 — **1-1~1-4와 병렬 가능** | **완료(2026-09-10 · D-209)** — `classify_alarm_kind`(게이트 분류기 동형 · 페이로드 `event.resourceType/alarmName`) + `PLAYBOOK_NOTES` 6유형 결정적 문구 · 순서 ③상관→④플레이북→⑤운영자 지침 · 미매칭 kind 문자열 동일 |
| **1-6** | **E8 post-gate 비차단 보강**(U-H 확정 (나) — 동기 probe 폐기) — 허용목록 명령 실행 채널 어댑터 `noise_gate/infrastructure/host_diagnostic_collector.py`(D-189 · `VM_DIAG_ALLOW` 준용) + post-gate 결정적 요지 첨부(sre_agent 미가용 시 폴백) + 측정 기반 dedup 상태지문 `{top_rss_pid, oom_flag, swap_active, sat_bucket}`. 플래그 `l3_enrichment_enabled`·`l3_audit_enabled`(기본 off · 만료일). `gate_l3_probe_*`는 **신설하지 않는다** | `plans/60` §18.2~§18.6 · `plans/66` 4-B · `plans/64` §4.8.6 워크드 · D-117 ② 개정 | `noise_gate/infrastructure/host_diagnostic_collector.py` · `application/nodes/alarm_notifier.py` · `config.py::NoiseGateConfig` | 60 §18.6 수용 기준 ①②④⑤(③ 경계 probe는 폐기로 제외) · 플래그 off 비트동일 · 변경명령 차단·마스킹·감사 테스트 · `arch_check --ci` 0 | 없음(D-189로 벤더 대기 해소) | **완료(2026-09-10 · D-209)** — `noise_gate/infrastructure/host_diagnostic_collector.py`(구조적 허용목록 · deny 마커 · `timeout 20 nice -n 10` · 마스킹 · 상태지문 `{top_rss_pid, oom_flag, swap_active, sat_bucket}` · escalate-only 비교 · ssh runner 주입) · notifier post-gate fire-and-forget + `[L3 진단]` 후속 · `decision_store.record_l3_state/last_l3_state` · 플래그 `NOISE_L3_*` 7건(기본 off · 만료 2027-02-20) · 설정 카탈로그 334·구획·도움말 |
| **1-7** | **C′-4 문서 정합** — `plans/85` §4 A-1/A-2·§9 잔여 갱신(★5건 해소·티어 1·3.5 소유 표기), `plans/53` Wave 4 항목의 소유 표기 | `plans/50` §0.8.3 C′-4 | `plans/` | 링크 실존 | 1-1~1-6 완료 시점마다 | **부분**(2026-09-09 — 85 §9 티어 3 소멸 표기·53/85에 91 포인터 · **2026-09-10** — 1-1~1-6 완료 표기·D-209·INDEX `-WIP`) |

### 1.2 판정·실조회 (코드 아님 · 과금 아님)

| # | 항목 | 원 계획·절 | 필요한 것 | 영향 | 상태 |
|---|---|---|---|---|---|
| **1-8** | `sre_agent`의 `overfit_check` 스캔 편입 판정 — 편입 시 `severity_signatures` 어휘가 기준선에 대량 유입되므로 **자기 델타만 소거**(전면 재생성 금지) | `plans/50` §16 U-D · §13 R-16 | 판정 1건 | 1-3·1-5의 폴스타 리터럴 검열 | **확정·적용(2026-09-10 · 사용자)** — `sre_agent/sre_agent/domain`만 `SCAN_DIRS`에 편입(벤더 중립 계층 · noise_gate와 동일 원칙 · application/infrastructure는 폴스타 어휘 허용). `--ci` 신규 유입 0 — 기준선 변경 없음 |
| **1-9** | `cmm_metric_stat_h` 보존 기간 확인 — 운영 DB 실조회 | `plans/50` §16 U-C · §6.2 | DB 접속 | 이상탐지 정밀도·confidence 상한(현재는 `notes`에 결정적 기록으로 방어) | **외부 대기(2026-09-10 사용자 확인 — 값 모름)**. 실조회 1줄(운영 접속 가능한 사람이 실행): PG(gp/yd) `SELECT MIN(stat_date), MAX(stat_date), COUNT(*) FROM polestar.cmm_metric_stat_h;` · DB2(b0) `SELECT MIN(STAT_DATE), MAX(STAT_DATE), COUNT(*) FROM POLESTAR.CMM_METRIC_STAT_H` (`stat_date`=YYYYMMDDHH). 판정 기준: 보존 시간 ≥ `evidence_baseline_periods`(기본 24)×1h + lookback이면 현 기본값 타당, 미만이면 `EVIDENCE_BASELINE_PERIODS` 하향 + `notes` 문장 유지 |
| **1-10** | `alarm.prometheus_enabled`·`noise_gate/infrastructure/prometheus_client.py` 처리 택1 — ①배선 완결 ②플래그·클라이언트 동시 삭제 ③예비 코드 명시+기한(권고 ③). **1-6 관점 포함 판단**(게이트측 E3 baseline 폴백 유일 구현) | `plans/66` §1.5 C3 · `plans/70` P1-1 | 사용자 택1 | 삭제 시 D-161 ② 4항 실측 필수 | **③ 확정(2026-09-10 · 사용자)** — 예비 코드 명시(독스트링·config 주석·도움말 3항목·카탈로그 주석 4곳 동일 문장) · **판정 기한 2027-02-20** · 배선/삭제 작업 목록은 `docs/27` §3.3.3/§3.3.4 · D-161 4항 실측 §3.3.1 |

### 1.3 외부 선행조건 대기 (코드로 진행 불가 — 해소 시 §1.1로 승격)

| # | 항목 | 원 계획·절 | 선행조건 | 해소 시 할 일 | 상태 |
|---|---|---|---|---|---|
| **1-11** | L2 6항목 조사(벤더 협의) | `plans/51` §5.2 | 폴스타 벤더 | 카탈로그 §4 갱신 → 1-5 문구 확장 | 대기 |
| **1-12** | R10 운영 Prometheus 실연동 — `nodename` 라벨 규약 실측·표준화 협의 | `plans/66` 4-A · `docs/23` §7.2.2·§8.2 | P0-3(인프라) | 측정 5단계 실행 → sre-agent D-020 등재 | 대기 |
| **1-13** | R12 `polestar_host_snapshot` 노출 여부·마스킹 정책 | `plans/66` 4-C · sre-agent/04 §4.2 | 1-6 완료 | 결정 → 노출 시 `mcp_server` 도구 1종 | 대기(1-6 후) |
| **1-14** | 운영 LLM 활성화 게이트(§7-1) — 사내 vLLM tool-calling 완주 판정 | `plans/66` §7-1 · `docs/23` §7-V | vLLM 서빙 `--enable-auto-tool-choice` · 운영 접속 | `docs/23` §7-V.5 완주 판정 → 운영 활성화 | 대기 |
| **1-15** | DB2 실 인스턴스 런타임 검증(D-126 스코프 밖 보류) — 루트 venv `ibm-db` 미설치(2026-09-02 실측) | `plans/66` §1.4 ⑥ · CLAUDE.md 「개발 명령」 | DB2 인스턴스·드라이버 | 1-2의 DB2 한계 문장 해제 검토 | 대기 |
| **1-16** | P0-1 반입 행정(statsmodels·e5-small) → `anomaly_stl_enabled`·`change_correlation_enabled` 활성화 — **기한 2027-02-20**(D-162 기한부) | `plans/66` Phase 5 · §1.5 C2 | 폐쇄망 반입 | 플래그 on·임계 재검 / 미해소 시 삭제 또는 사유부 연장 판정 | 대기(기한부) |
| **1-17** | 91 1-1 실 경로(상관 on 조사 e2e · `test_investigation_e2e::test_correlation_on_job_briefing_has_hypotheses_timeline_limitations`) 완주 확인 | `plans/50` §0.8.3 C′-0 · 본 파일 1-1 | **분당 40회 이상 가능한 LLM 등급**(유료 Gemini 또는 1-14 사내 vLLM) — 2026-09-10 실측: 무료 등급 RPM 5로 429 실패. **2026-09-17**: 로컬 OpenAI 호환 LLM으로 RPM 제약 없이 실행 가능해짐 · e2e가 `API_BASE`면 운영 배선으로 조립(D-229 — 종전엔 Gemini 고정) | **절차서 `docs/23` §7-V.5.1**(2026-09-21 신설 — 배선·`mcp_server` 조사 프로파일 기동·실행 명령·합격 판정 5항·실패 원인별 조치·원상복구). 요약: `cd sre_agent && RUN_E2E=1 MODEL=openai/<served> API_BASE=<사내 vLLM>/v1 API_KEY=dummy INVESTIGATION_LLM_ENABLED=true OVERRIDE_MAX_CONTENT_SIZE=30000 OVERRIDE_MAX_OUTPUT_TOKEN=2048 GEMINI_API_KEY= LLM_GEMINI_API_KEY= POLESTAR_MCP_URL=http://localhost:9097/sse .venv/bin/python -m pytest tests/test_investigation_e2e.py -k correlation_on_job`(사내 vLLM은 무과금 · Gemini 경로면 D-127 건별 승인) | **부분(2026-09-17)** — ✅ **테스트 로직 검증**: 로컬 MLX(Qwen3.5-9B · 로직 확인용 · 성능 기준선 아님 D-174)로 첫 실 실행 → 결함 2건 발견·교정(픽스처 `alarmTime` 형식이라 `reference_time` None → 사전수집 생략 · 미완주 문구의 인용 마커 오인 — D-229) 후 **PASS**(LLM 40회 · 173s · 브리핑 가설 rank·confidence·`T-`/근거 없음 타임라인·상관≠인과 한계 단언 충족). ⏳ **잔여(내부망 실행 대기)**: 운영 등급 LLM(1-14 사내 vLLM) 실행 — MLX 9B 실행은 조사 자체가 step 상한 미완주였고(구조 단언만 통과), 운영 모델로 결론까지 도달하는 완주(`incomplete=False`)는 미확인. **에이전트는 실행하지 않는다**(사내 vLLM은 이 맥에 없다) — 사용자가 `docs/23` §7-V.5.1 절차로 내부망에서 실행하고 결과를 이 행에 적는다 |

**본 계획에 넣지 않은 것**: R14·R15(`plans/61`·`63` 소관 — 잔여가 있어도 무표기인 선례) · `plans/87` APM · `plans/78`/`80` 배선 잔여(W3-2/3·W7-2) · **자동 조치 실행 B-3**(`plans/64` §8.3 관할 · D-003 예외 미확정 · **착수 금지 유지**).

## 2. 착수 순서

```
1-1 (e2e 상관 단언 · 스텁 경로 먼저)
 └→ 1-2 (변경 이벤트 오버레이)  └→ 1-3 (연쇄 소비 · meta.cluster 실측 선행)
1-5 (플레이북 편입)  ── 병렬
1-6 (E8 post-gate)   ── 병렬 (1-13은 1-6 후)
1-4 (피드백 편승)    ── 1-1 뒤 아무 때나
1-7 (문서 정합)      ── 각 항목 완료 시점마다
```

- 1-8·1-9·1-10은 착수 전 판정이 유리하나 블로커는 아니다(1-3·1-5 리터럴 검열은 1-8 결과에 따라 후처리).
- 신규 플래그는 **기본 off + 만료일**(D-161 C1). 실 LLM·실 조사 실행은 **건마다 D-127 승인**.
- 각 항목은 SDD·TDD로 진행한다 — `CAPABILITY-MAP-50.md`·`SPEC-*.md`·`tasks/plan-50.md` 전례를 따르되 파일명은 91 기준(`CAPABILITY-MAP-91.md`·`tasks/plan-91.md`).

## 3. 이관 원본 처리 (2026-09-09 실행)

| 원본 | 처리 | 남긴 것 |
|---|---|---|
| `plans/50-fault-diagnosis-rca.md` | `-WIP` 해제(리네임) | 헤더 상태 1줄: 잔여 전건 → 91 §1 (1-1~1-4·1-7~1-9). §0.8은 설계 정본으로 유지 |
| `plans/51-fault-diagnosis-data-collection.md` | `-WIP` 해제 | 헤더 상태 1줄: 잔여 → 91 1-5·1-11 |
| `plans/66-sre-agent-integrated-implementation-plan.md` | `-WIP` 해제 | 헤더 상태 1줄: 잔여 → 91 1-6·1-10·1-12~1-16. §1.2~§1.6은 시점 기록으로 보존 |
| `plans/INDEX.md` | 「이관 조항」 신설 | *"잔여를 다른 계획서로 이관하면 원본은 무표기, 이관처가 태그를 단다"* |

## 4. 결정

- **D-208** — 잔여 이관 장부 신설 + INDEX 이관 조항 + 50·51·66 태그 종료. 문서 결정(코드 0). 착수 항목별 신규 결정은 각 항목 완료 시 `docs/02_decision.md` 채번 규칙으로 등재한다(예약 없음 — 계획서 예약은 효력이 없다, D-161).

## 5. 변경 이력

| 날짜 | 버전 | 내용 |
|---|---|---|
| 2026-09-09 | v1 | 최초 작성 — 사용자 지시 *"추가로 진행해야 되는 항목들을 모아서 별도의 계획파일로 만들고 WIP는 종료처리"* → 검토(A안: 91 신설 · 66 종료) → *"A안으로 진행하라"*. 50·51·66 잔여 16항목 이관(코드 착수 가능 7 · 판정 3 · 외부 대기 6). 세 원본 `-WIP` 해제·참조 갱신, INDEX 이관 조항, D-208 등재. |
| 2026-09-10 | v2 | **§1.1 코드 항목 1-1~1-6 구현**(사용자 지시 *"다음 계획을 구현하라"* · `CAPABILITY-MAP-91.md` ASSUMPTIONS 5건 · SPEC 2건 · `tasks/plan-91.md`). 전제 정정 2건: 1-1 무과금 스텁은 dispatcher 테스트에(기존 e2e 파일은 전부 RUN_E2E 게이트) · 1-3은 `meta.cluster`에 멤버명이 없어 `root_resource_name` 1줄 소비로 축소. 신규 플래그 3종(`EVIDENCE_CHANGE_OVERLAY_ENABLED`·`EVIDENCE_CORRELATION_RELATED_HOSTS`·`NOISE_L3_*`) 기본 off+만료일. 검증: sre_agent 358 · mcp_server 222 · noise_gate 1278(기존 실패 4 불변) · 설정 카탈로그 334 · arch/overfit 0. `-TODO`→`-WIP`. D-209 등재. |
| 2026-09-10 | v3 | **인터뷰 확정 반영**(사용자 지시 *"사용자 결정이 필요한 사항은 인터뷰를 진행하고 구현을 진행하라"*): 1-8 domain 편입(신규 유입 0) · 1-9 외부 대기 + 실조회 SQL 기록 · 1-10 ③ 예비 코드 확정(`docs/27` §3.3 확장 · 판정 기한 2027-02-20) · 1-4 UI 노출 구현(state `investigation_id` · 페이로드 조건부 키 · app.js) · 1-3 클러스터 멤버 소비는 2차 실측(SUPPRESS 티어·시간 순서)으로 **폐기**. 88 R-E는 (c) 확정·구현(`COMPOSITE_PRIOR_SCOPE_LATEST_ONLY`). |
| 2026-09-10 | v4 | 실 실행 결과 반영: 1-1 실 경로는 Gemini 무료 등급 RPM 쿼터(429)로 실패 → **§1.3 1-17 외부 대기 이관**(사용자 확정 (c)). 88 2차 3종 운영 on 확정(88 §11.5). 변경분 커밋. |
| 2026-09-21 | v6 | **1-17 절차서 신설**(사용자 지시 *"중단한 작업을 재개하라"* — C): 내부망 실행 절차를 `docs/23` §7-V.5.1에 편입(새 파일 없음) — 배선·조사 프로파일 `mcp_server` 기동·실행 명령·합격 판정 5항(PASS·`done`·인용·**완주**·`prefetch` 감사)·실패 원인별 조치·원상복구·결과 반영. 1-17 상태는 **「부분」 유지**(잔여 = 내부망 실행 대기). 부수: `docs/23` §7-G.2(a) Gemini 경로 실행 위치를 `cd sre_agent`로 정정(루트 CWD 수집 실패 — `docs/18` 2026-09-10). |
| 2026-09-17 | v5 | **1-17 부분 반영**(사용자 결정 *"3번은 반영하라"*): 로컬 MLX로 상관 e2e 첫 실 실행 — 결함 2건(픽스처 시각 형식 · 미완주 문구 인용 오인) 교정 후 구조 단언 PASS(D-229). MLX는 로직 확인용(D-174)이라 **운영 등급 LLM(1-14 사내 vLLM) 실행은 잔여로 유지** · 조사 자체는 9B step 상한 미완주. verify 명령을 운영 배선(`API_BASE` · D-229/D-230 — Gemini 키 불요)으로 갱신. §1.3 외부 대기 6건 불변 → `-WIP` 유지. |
