# 91. 장애 조사 계획군 잔여 통합 — 50·51·66 이관 장부 (Fault-Investigation Residual Consolidation)

> 작성일: 2026-09-09
> **성격**: **이관 장부(residual ledger)** — `plans/50`(RCA 축)·`plans/51`(증거 카탈로그·플레이북)·`plans/66`(SRE-Agent 실행 시퀀스)이 각자 `-WIP`로 들고 있던 잔여를 **한 파일로 모아 단일 소유**한다. 세 원본은 이관과 동시에 `-WIP`를 떼고 시점 문서로 완결된다(INDEX 「이관 조항」 · D-208). 설계 정본은 여전히 각 원 계획서의 해당 절이며, 본 계획은 **무엇이 남았고 어떤 순서·조건으로 착수하는가**만 적는다.
> **상위 로드맵**: Plan 62 P2(진단·RCA)·P4 · Plan 53 Wave 4. **선행 정리**: `plans/50` §0.8(D-207 — 표류 잔여 8항목 단일 소유 확정 · U-F~U-I 사용자 확정) · `plans/64`(2026-09-09 재판정 — 코드 잔여 0 · 무표기).
> **관련 결정**: D-207(계획군 단일 소유 · Phase C′ 구체화) · **D-208**(본 이관 + INDEX 이관 조항) · D-197(50 G1~G6) · D-189(L3 경로 B) · D-117(E8 — ② 개정: post-gate만) · D-138(권고) · D-177(83 피드백 루프) · D-127(과금 승인 게이트) · D-161(플래그 만료일).
> **상태**: **`-TODO` — 이관 항목 전건 코드 0건**(2026-09-09 실측). 한 항목이라도 랜딩하면 `-WIP`, 전부 끝나면 무표기. 외부 선행조건 항목(§1.3)은 해소 여부를 본 파일 §1.3 표에서만 추적한다.

---

## 0. 규칙 — 이 장부가 퇴화하지 않기 위한 세 가지

1. **항목마다 원 계획 절 링크·소재지·verify를 반드시 적는다.** 링크 없는 항목은 등재 불가(잔여 보관함 방지).
2. **한 항목은 한 곳에만.** 원 계획서에는 *"잔여는 `plans/91` §1-N 소유"* 한 줄만 남기고 본문 잔여 서술은 이력으로 보존한다(`plans/85` §1 교훈).
3. **완료 시 원 계획서가 아니라 본 파일의 행을 닫는다**(상태 열 갱신 + §5 이력). 원 계획서는 건드리지 않는다 — 다만 그 항목이 원 계획의 결정(D-번호) 상태를 바꾸면 `docs/02_decision.md`를 갱신한다.

## 1. 이관 항목 (2026-09-09 실측 · 전건 코드 0건)

### 1.1 코드 착수 가능 (블로커 없음)

| # | 항목 | 원 계획·절 | 소재지 | verify | 선행 | 상태 |
|---|---|---|---|---|---|---|
| **1-1** | **C′-0 e2e 상관 단언** — `test_investigation_e2e.py`에 `evidence_correlation_enabled=True` 경로: `root_cause_hypotheses`(rank·confidence)·상대시각 타임라인(`T-`)·`limitations` 단언. 무과금 부분 선행: 레벨 A 스텁 조사에서 dispatcher 감사 이벤트 `prefetch`(`leading_signal`) 기록 단언 | `plans/50` §0.8.3 C′-0 · `docs/23` §7 | `sre_agent/tests/test_investigation_e2e.py` | 스텁 경로 과금 0 · 실 완주는 **D-127 건별 승인** 후 RUN_E2E=1 | 없음 | 미착수 |
| **1-2** | **C′-2 변경 이벤트 오버레이** — `polestar_change_history`에 `reference_time`(G1 동형 · 미지정 시 SQL 문자열 동일) → prefetch 배치에 변경 이력 1건(PG만 · DB2 b0는 `notes` 한계) → `TimelineItem.kind="change"` + `change_finding` → *"변경 직후"* 가설 규칙(변경 offset < 첫 알람 offset이면 rank 상승 · confidence 상한 유지). 게이트 E5와 상호 호출 없음 | `plans/50` §0.8.3 C′-2 · §4.5 | `mcp_server/mcp_server/polestar_tools.py` · `sre_agent/sre_agent/application/evidence_prefetch.py` · `domain/correlation.py` · `application/briefing_builder.py` | SQL 스냅샷 · 골든(변경 T-20m → 알람 T-10m ⇒ 가설 1위 "변경") · PG 전용 한계 문장 · 도구 실패 시 나머지 상관 보존 | 1-1 권장(검증 경로 확보) | 미착수 |
| **1-3** | **C′-1 다중서버 연쇄 소비** — `scope_from_job`이 `payload["meta"]["root_resource"]`·`["cluster"]` 멤버를 읽어 `EvidenceScope.related_servers`(상한 **3** · U-I 확정) → 연관 서버당 알람 1건만 추가(지표는 대표 서버만) → `AlarmPoint.server` · 타임라인 서버명 접두 · `notes` *"연관 서버 X의 첫 알람 T-Nm — 대표보다 선행"*. **`polestar_topology` 호출 없음**(게이트 E4 판정만 신뢰). 플래그 `EVIDENCE_CORRELATION_RELATED_HOSTS`(기본 0=off · **만료일 부여** D-161 C1). **착수 시 `meta.cluster` 실 shape는 `_detect_correlated_storm` 반환값으로 실측**(추정 금지) | `plans/50` §0.8.3 C′-1 · §6.3 · `plans/60` E2·E4 | `sre_agent/sre_agent/application/evidence_prefetch.py` · `domain/correlation.py` · `application/briefing_builder.py` | 골든(연관 서버 알람 선행 시 notes 문장) · 멤버 상한 · **메타 부재·플래그 off 시 종전 결과 비트 동일** · 연관 서버 호출 부분 실패 시 대표 결과 보존 | 1-2 뒤 | 미착수 |
| **1-4** | **C′-3 진단 피드백 — Plan 83 피드백 루프 편승**(U-F 확정 (ii)) — 피드백 레코드에 `investigation_id` 선택 필드, 존 RBAC·작성자 기록·철회 재사용, 별도 저장소 없음. 이력 조회는 `JobStore` audit JSONL 그대로 | `plans/50` §0.8.3 C′-3 · §9.3 · `plans/83`(D-177) | `noise_gate`(피드백 도메인·저장) · `src/api/routes/alarm.py`(라우트 — D-139 예외 소재지) | 존 RBAC 거부 · 철회 · **기존 알람 피드백 경로 비트 동일** | 1-1 뒤(순서 무관) | 미착수 |
| **1-5** | **플레이북 내용 편입** — `plans/51` §6 6유형(CPU 포화·메모리/OOM·디스크/inode/IO·네트워크·프로세스 다운/플래핑·로그 패턴)을 **알람 kind별 결정적 문구**로 `build_guidance()`에 싣는다(LLM 아님 · D-035). 편입점은 D-197 `investigation_guidance_extra`가 이미 제공. `sre_agent`는 `overfit_check` 스캔 밖 — 폴스타 리터럴은 1-8과 함께 판정 | `plans/51` §6 · §4 · `plans/50` §0.8.2 ③ · `plans/85` §9 티어 1 | `sre_agent/sre_agent/application/investigation_guidance.py` | kind별 문구 골든 · 미매칭 kind는 종전 지침과 문자열 동일 · `system_prompt_additions` 도달 단언(기존 `test_investigation_guidance.py` 확장) | 없음 — **1-1~1-4와 병렬 가능** | 미착수 |
| **1-6** | **E8 post-gate 비차단 보강**(U-H 확정 (나) — 동기 probe 폐기) — 허용목록 명령 실행 채널 어댑터 `noise_gate/infrastructure/host_diagnostic_collector.py`(D-189 · `VM_DIAG_ALLOW` 준용) + post-gate 결정적 요지 첨부(sre_agent 미가용 시 폴백) + 측정 기반 dedup 상태지문 `{top_rss_pid, oom_flag, swap_active, sat_bucket}`. 플래그 `l3_enrichment_enabled`·`l3_audit_enabled`(기본 off · 만료일). `gate_l3_probe_*`는 **신설하지 않는다** | `plans/60` §18.2~§18.6 · `plans/66` 4-B · `plans/64` §4.8.6 워크드 · D-117 ② 개정 | `noise_gate/infrastructure/host_diagnostic_collector.py` · `application/nodes/alarm_notifier.py` · `config.py::NoiseGateConfig` | 60 §18.6 수용 기준 ①②④⑤(③ 경계 probe는 폐기로 제외) · 플래그 off 비트동일 · 변경명령 차단·마스킹·감사 테스트 · `arch_check --ci` 0 | 없음(D-189로 벤더 대기 해소) | 미착수 |
| **1-7** | **C′-4 문서 정합** — `plans/85` §4 A-1/A-2·§9 잔여 갱신(★5건 해소·티어 1·3.5 소유 표기), `plans/53` Wave 4 항목의 소유 표기 | `plans/50` §0.8.3 C′-4 | `plans/` | 링크 실존 | 1-1~1-6 완료 시점마다 | **부분**(2026-09-09 — 85 §9 티어 3 소멸 표기·53/85에 91 포인터) |

### 1.2 판정·실조회 (코드 아님 · 과금 아님)

| # | 항목 | 원 계획·절 | 필요한 것 | 영향 | 상태 |
|---|---|---|---|---|---|
| **1-8** | `sre_agent`의 `overfit_check` 스캔 편입 판정 — 편입 시 `severity_signatures` 어휘가 기준선에 대량 유입되므로 **자기 델타만 소거**(전면 재생성 금지) | `plans/50` §16 U-D · §13 R-16 | 판정 1건 | 1-3·1-5의 폴스타 리터럴 검열 | 미판정 |
| **1-9** | `cmm_metric_stat_h` 보존 기간 확인 — 운영 DB 실조회 | `plans/50` §16 U-C · §6.2 | DB 접속 | 이상탐지 정밀도·confidence 상한(현재는 `notes`에 결정적 기록으로 방어) | 미확인 |
| **1-10** | `alarm.prometheus_enabled`·`noise_gate/infrastructure/prometheus_client.py` 처리 택1 — ①배선 완결 ②플래그·클라이언트 동시 삭제 ③예비 코드 명시+기한(권고 ③). **1-6 관점 포함 판단**(게이트측 E3 baseline 폴백 유일 구현) | `plans/66` §1.5 C3 · `plans/70` P1-1 | 사용자 택1 | 삭제 시 D-161 ② 4항 실측 필수 | 미결 |

### 1.3 외부 선행조건 대기 (코드로 진행 불가 — 해소 시 §1.1로 승격)

| # | 항목 | 원 계획·절 | 선행조건 | 해소 시 할 일 | 상태 |
|---|---|---|---|---|---|
| **1-11** | L2 6항목 조사(벤더 협의) | `plans/51` §5.2 | 폴스타 벤더 | 카탈로그 §4 갱신 → 1-5 문구 확장 | 대기 |
| **1-12** | R10 운영 Prometheus 실연동 — `nodename` 라벨 규약 실측·표준화 협의 | `plans/66` 4-A · `docs/23` §7.2.2·§8.2 | P0-3(인프라) | 측정 5단계 실행 → sre-agent D-020 등재 | 대기 |
| **1-13** | R12 `polestar_host_snapshot` 노출 여부·마스킹 정책 | `plans/66` 4-C · sre-agent/04 §4.2 | 1-6 완료 | 결정 → 노출 시 `mcp_server` 도구 1종 | 대기(1-6 후) |
| **1-14** | 운영 LLM 활성화 게이트(§7-1) — 사내 vLLM tool-calling 완주 판정 | `plans/66` §7-1 · `docs/23` §7-V | vLLM 서빙 `--enable-auto-tool-choice` · 운영 접속 | `docs/23` §7-V.5 완주 판정 → 운영 활성화 | 대기 |
| **1-15** | DB2 실 인스턴스 런타임 검증(D-126 스코프 밖 보류) — 루트 venv `ibm-db` 미설치(2026-09-02 실측) | `plans/66` §1.4 ⑥ · CLAUDE.md 「개발 명령」 | DB2 인스턴스·드라이버 | 1-2의 DB2 한계 문장 해제 검토 | 대기 |
| **1-16** | P0-1 반입 행정(statsmodels·e5-small) → `anomaly_stl_enabled`·`change_correlation_enabled` 활성화 — **기한 2027-02-20**(D-162 기한부) | `plans/66` Phase 5 · §1.5 C2 | 폐쇄망 반입 | 플래그 on·임계 재검 / 미해소 시 삭제 또는 사유부 연장 판정 | 대기(기한부) |

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
