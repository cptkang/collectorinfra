# 플래그 전수 감사표 (plans/70 P0-3 / F1)

> **작성일** 2026-08-20 · **대상** `AppConfig` 및 중첩 설정의 `enable_*` / `use_*` / `*_enabled` **전 43개**
> **코드 변경 0** — 판정만 기록한다. 실제 상수화·삭제는 plans/70 P1-1·P1-2에서 플래그당 1커밋으로 처리한다.

## 측정 방법 (재현 명령)

```bash
# ① 플래그 전수 — pydantic introspection (D-129 SSOT와 동일 방식, grep 누락 방지)
.venv/bin/python -c "import src.config as C; ..."   # enable_*/use_*/*_enabled 재귀 수집 → 43개

# ② 프로덕션 참조 수 — 테스트·config.py 정의·벤더 venv 제외
grep -rnw --include='*.py' '<flag>' src noise_gate mcp_server sre_agent \
  | grep -v '/.venv/' | grep -v '/tests/' | grep -v '/test_' | grep -v '^src/config.py:'

# ③ 생성·최종 변경 — 현 브랜치(multiintent) 한정 pickaxe
git log --reverse -1 --format='%ad|%h' --date=short -S'<flag>' -- <참조 파일들> src/config.py
```

### 측정상 주의 2건 (실측으로 드러남)

1. **벤더 venv 오염** — `sre_agent/.venv/` 안에 동명 심볼이 있어 단순 grep이 `trace_enabled`를
   **52건**으로, `enable_thinking`을 **7건**으로 부풀렸다. 실제 프로덕션 참조는 각각 **5건·4건**이다.
   `plans/70` §1.3 재현 명령에 `.venv` 제외가 없다 → E1에서 반영.
2. **D-139 패키지 이전이 최종 변경일을 덮음** — `b79808a`(2026-08-05, noise_gate 최상위 분리)가
   pickaxe상 add+delete로 잡혀 noise_gate 플래그 **대부분의 "최종 변경일"이 일제히 2026-08-05**로
   보인다. 이는 파일 이동이지 의미 변경이 아니므로, 아래 표는 이 커밋을 제외한 **실질 최종 변경일**을 쓴다.

## 판정 규칙 (위에서부터 먼저 맞는 것 적용)

| # | 조건 | 판정 |
|---|---|---|
| 1 | 프로덕션 참조 0건 | **기한부** |
| 2 | `.env` 실제값이 코드 기본값과 다름 | **존치** (운영이 뒤집어 쓰는 살아있는 레버 — 상수화 금지) |
| 3 | 프로덕션 참조 3건 이상 | **존치** |
| 4 | 참조 1~2건 · 기본값 ON | **상수화** (끄는 경로가 사실상 없음) |
| 5 | 참조 1~2건 · 기본값 OFF | **기한부** (도입 후 켠 적 없음) |
| — | 단 규칙 4에 해당하더라도 **생성 30일 미만**이면 상수화하지 않고 **기한부** | 실사용 이력이 없어 판단 근거가 없다 |

기한부 만료일은 **2027-02-20**(6개월)로 통일한다. 기한 도래 시 D-143 ①에 따라 ①삭제 또는
②사유를 붙인 연장 중 하나를 강제한다.

## 감사표 (43행 · 기한부 → 상수화 → 존치 순)

| # | 플래그 (`.env` 키) | 생성 D-번호 | 코드 기본값 | `.env` 실제값 | 참조 | 생성일 | 실질 최종 변경 | 판정 | 근거 |
|---:|---|---|---|---|---:|---|---|---|---|
| 1 | `alarm.prometheus_enabled`<br>`ALARM_PROMETHEUS_ENABLED` | — | `False` | *(미명시)* | 0 | 2026-07-22 `e530c73` | 2026-07-22 `e530c73` | **기한부** | 프로덕션 참조 0 — 게이팅 호출부 없음(형상만 존재) |
| 2 | `noise_gate.anomaly_stl_enabled`<br>`NOISE_ANOMALY_STL_ENABLED` | D-113 | `False` | `false` | 1 | 2026-07-23 `7c8983e` | 2026-07-23 `7c8983e`<br>*(D-139 이전 제외)* | **기한부** | 참조 1건·기본 OFF — 도입 후 켠 적 없음 |
| 3 | `noise_gate.change_correlation_enabled`<br>`NOISE_CHANGE_CORRELATION_ENABLED` | D-111 | `False` | `false` | 2 | 2026-07-22 `ec2326a` | 2026-07-22 `ec2326a`<br>*(D-139 이전 제외)* | **기한부** | 참조 2건·기본 OFF — 도입 후 켠 적 없음 |
| 4 | `noise_gate.enable_agentic_enricher`<br>`NOISE_ENABLE_AGENTIC_ENRICHER` | D-048 | `False` | `false` | 2 | 2026-07-07 `7dbc02c` | 2026-07-07 `7dbc02c`<br>*(D-139 이전 제외)* | **기한부** | 참조 2건·기본 OFF — 도입 후 켠 적 없음 |
| 5 | `observability.sql_log_enabled`<br>`OBS_SQL_LOG_ENABLED` | D-140 *(커밋)* | `True` | *(미명시)* | 2 | 2026-08-19 `7fc3513` | 2026-08-19 `8c42332` | **기한부** | 참조 2건·기본 ON이나 2026-08-19 신설 — 실사용 이력 부재로 상수화 유보 |
| 6 | `audit.db_enabled`<br>`AUDIT_DB_ENABLED` | — | `True` | *(미명시)* | 1 | 2026-04-09 `6617625` | 2026-04-09 `6617625` | **상수화** | 참조 1건·기본 ON·`.env` 미명시 — 끄는 운영 경로가 사실상 없음 |
| 7 | `audit.jsonl_enabled`<br>`AUDIT_JSONL_ENABLED` | — | `True` | *(미명시)* | 1 | 2026-04-09 `6617625` | 2026-04-09 `6617625` | **상수화** | 참조 1건·기본 ON·`.env` 미명시 — 끄는 운영 경로가 사실상 없음 |
| 8 | `noise_gate.decision_store_enabled`<br>`NOISE_DECISION_STORE_ENABLED` | — | `True` | `true` | 2 | 2026-06-29 `b7bbfd5` | 2026-06-30 `2a8aee9`<br>*(D-139 이전 제외)* | **상수화** | 참조 2건·기본 ON — 끄는 운영 경로가 사실상 없음 |
| 9 | `noise_gate.correlation_topology_weight_enabled`<br>`NOISE_CORRELATION_TOPOLOGY_WEIGHT_ENABLED` | D-112 | `False` | `false` | 3 | 2026-07-23 `c4659ff` | 2026-07-23 `c4659ff`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 3건 — 실 게이트로 배선됨 |
| 10 | `noise_gate.feedback_store_enabled`<br>`NOISE_FEEDBACK_STORE_ENABLED` | — | `True` | `true` | 3 | 2026-07-07 `7dbc02c` | 2026-07-07 `7dbc02c`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 3건 — 실 게이트로 배선됨 |
| 11 | `noise_gate.format_tolerant_parsing_enabled`<br>`NOISE_FORMAT_TOLERANT_PARSING_ENABLED` | D-116 | `False` | *(미명시)* | 3 | 2026-07-27 `6b9fe67` | 2026-07-27 `6b9fe67`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 3건 — 실 게이트로 배선됨 |
| 12 | `noise_gate.sse_bridge_enabled`<br>`NOISE_SSE_BRIDGE_ENABLED` | D-048 *(커밋)* | `False` | `false` | 3 | 2026-06-30 `abd915b` | 2026-06-30 `abd915b`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 3건 — 실 게이트로 배선됨 |
| 13 | `enable_sql_approval`<br>`ENABLE_SQL_APPROVAL` | D-135 | `False` | `false` | 4 | 2026-03-23 `4079a30` | 2026-03-23 `4079a30` | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 14 | `noise_gate.annotation_harvest_enabled`<br>`NOISE_ANNOTATION_HARVEST_ENABLED` | D-116 | `False` | *(미명시)* | 4 | 2026-07-27 `6b9fe67` | 2026-07-27 `6b9fe67`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 15 | `noise_gate.annotation_llm_classification_enabled`<br>`NOISE_ANNOTATION_LLM_CLASSIFICATION_ENABLED` | D-132 | `False` | *(미명시)* | 4 | 2026-07-30 `b8f05f7` | 2026-07-30 `b8f05f7`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 16 | `noise_gate.correlation_site_dimension_enabled`<br>`NOISE_CORRELATION_SITE_DIMENSION_ENABLED` | D-116 | `False` | *(미명시)* | 4 | 2026-07-27 `6b9fe67` | 2026-07-27 `6b9fe67`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 17 | `noise_gate.incident_tracking_enabled`<br>`NOISE_INCIDENT_TRACKING_ENABLED` | D-049 | `False` | `false` | 4 | 2026-06-30 `e0b8ca1` | 2026-06-30 `e0b8ca1`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 18 | `noise_gate.multi_hop_cascade_enabled`<br>`NOISE_MULTI_HOP_CASCADE_ENABLED` | D-107 | `False` | `false` | 4 | 2026-07-22 `e530c73` | 2026-07-22 `e530c73`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 19 | `noise_gate.ticket_batch_queue_enabled`<br>`NOISE_TICKET_BATCH_QUEUE_ENABLED` | — | `True` | `true` | 4 | 2026-06-30 `2a8aee9` | 2026-06-30 `2a8aee9`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 20 | `orchestrator.enable_thinking`<br>`ORCHESTRATOR_ENABLE_THINKING` | D-042 | `False` | *(미명시)* | 4 | 2026-06-26 `a896de6` | 2026-06-26 `a896de6` | **존치** | 프로덕션 참조 4건 — 실 게이트로 배선됨 |
| 21 | `enable_deepagent_orchestration`<br>`ENABLE_DEEPAGENT_ORCHESTRATION` | D-129 | `None` | `true` | 5 | 2026-06-17 `475960b` | 2026-08-20 `3859917` | **존치** | `.env`가 코드 기본값을 뒤집음 — 운영이 실제로 쓰는 레버 |
| 22 | `enable_deepagents_package`<br>`ENABLE_DEEPAGENTS_PACKAGE` | — | `False` | `true` | 5 | 2026-06-17 `475960b` | 2026-08-20 `3859917` | **존치** | `.env`가 코드 기본값을 뒤집음 — 운영이 실제로 쓰는 레버 |
| 23 | `enable_structure_approval`<br>`ENABLE_STRUCTURE_APPROVAL` | D-020 | `True` | *(미명시)* | 5 | 2026-03-31 `f994ceb` | 2026-03-31 `f994ceb` | **존치** | 프로덕션 참조 5건 — 실 게이트로 배선됨 |
| 24 | `noise_gate.investigation_followup_enabled`<br>`NOISE_INVESTIGATION_FOLLOWUP_ENABLED` | D-137 | `False` | *(미명시)* | 5 | 2026-08-05 `b7ccc20` | 2026-08-05 `b7ccc20`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 5건 — 실 게이트로 배선됨 |
| 25 | `observability.trace_enabled`<br>`OBS_TRACE_ENABLED` | D-141 | `True` | *(미명시)* | 5 | 2026-08-19 `7fc3513` | 2026-08-19 `8c42332` | **존치** | 프로덕션 참조 5건 — 실 게이트로 배선됨 |
| 26 | `alarm.process_enrich_enabled`<br>`ALARM_PROCESS_ENRICH_ENABLED` | D-036 | `True` | *(미명시)* | 6 | 2026-06-16 `e649d63` | 2026-07-22 `e530c73`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 27 | `enable_semantic_routing`<br>`ENABLE_SEMANTIC_ROUTING` | D-141 | `None` | `true` | 6 | 2026-03-23 `4079a30` | 2026-08-20 `3859917` | **존치** | `.env`가 코드 기본값을 뒤집음 — 운영이 실제로 쓰는 레버 |
| 28 | `noise_gate.enable_ai_severity_boost`<br>`NOISE_ENABLE_AI_SEVERITY_BOOST` | D-110 | `False` | `false` | 6 | 2026-06-29 `b7bbfd5` | 2026-07-22 `ec2326a`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 29 | `noise_gate.fault_escalation_enabled`<br>`NOISE_FAULT_ESCALATION_ENABLED` | D-124 | `False` | *(미명시)* | 6 | 2026-07-28 `566e8b9` | 2026-07-28 `566e8b9`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 30 | `noise_gate.flapping_enabled`<br>`NOISE_FLAPPING_ENABLED` | — | `False` | `false` | 6 | 2026-06-30 `15f322a` | 2026-06-30 `15f322a`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 31 | `noise_gate.inhibition_enabled`<br>`NOISE_INHIBITION_ENABLED` | — | `False` | `false` | 6 | 2026-06-30 `15f322a` | 2026-06-30 `15f322a`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 32 | `noise_gate.non_alarm_filter_enabled`<br>`NOISE_NON_ALARM_FILTER_ENABLED` | D-116 | `False` | *(미명시)* | 6 | 2026-07-27 `6b9fe67` | 2026-07-27 `6b9fe67`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 33 | `noise_gate.semantic_dedup_annotation_enabled`<br>`NOISE_SEMANTIC_DEDUP_ANNOTATION_ENABLED` | D-114 | `False` | `false` | 6 | 2026-07-23 `7c8983e` | 2026-07-23 `7c8983e`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 34 | `noise_gate.storm_grouping_enabled`<br>`NOISE_STORM_GROUPING_ENABLED` | — | `False` | `false` | 6 | 2026-06-30 `15f322a` | 2026-06-30 `15f322a`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 6건 — 실 게이트로 배선됨 |
| 35 | `noise_gate.dynamic_baseline_enabled`<br>`NOISE_DYNAMIC_BASELINE_ENABLED` | D-110 | `False` | `false` | 8 | 2026-07-22 `ec2326a` | 2026-07-22 `ec2326a`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 8건 — 실 게이트로 배선됨 |
| 36 | `noise_gate.investigation_trigger_enabled`<br>`NOISE_INVESTIGATION_TRIGGER_ENABLED` | D-124 | `False` | *(미명시)* | 8 | 2026-07-28 `566e8b9` | 2026-07-28 `566e8b9`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 8건 — 실 게이트로 배선됨 |
| 37 | `noise_gate.topology_text_fusion_enabled`<br>`NOISE_TOPOLOGY_TEXT_FUSION_ENABLED` | D-114 | `False` | `false` | 8 | 2026-07-23 `7c8983e` | 2026-07-23 `7c8983e`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 8건 — 실 게이트로 배선됨 |
| 38 | `noise_gate.cross_host_correlation_enabled`<br>`NOISE_CROSS_HOST_CORRELATION_ENABLED` | D-109 | `False` | `false` | 9 | 2026-07-22 `ec2326a` | 2026-07-23 `c4659ff`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 9건 — 실 게이트로 배선됨 |
| 39 | `noise_gate.message_enrichment_enabled`<br>`NOISE_MESSAGE_ENRICHMENT_ENABLED` | D-108 | `False` | `false` | 9 | 2026-07-22 `e530c73` | 2026-07-22 `e530c73`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 9건 — 실 게이트로 배선됨 |
| 40 | `alarm.history_enabled`<br>`ALARM_HISTORY_ENABLED` | D-035 | `True` | *(미명시)* | 11 | 2026-06-15 `6c32f31` | 2026-06-15 `6c32f31`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 11건 — 실 게이트로 배선됨 |
| 41 | `noise_gate.enable_llm_actionability`<br>`NOISE_ENABLE_LLM_ACTIONABILITY` | D-048 | `False` | `false` | 11 | 2026-06-29 `b7bbfd5` | 2026-07-07 `7dbc02c`<br>*(D-139 이전 제외)* | **존치** | 프로덕션 참조 11건 — 실 게이트로 배선됨 |
| 42 | `noise_gate.fault_diagnosis_enabled`<br>`NOISE_FAULT_DIAGNOSIS_ENABLED` | D-124 | `False` | *(미명시)* | 14 | 2026-07-28 `566e8b9` | 2026-08-19 `8c42332` | **존치** | 프로덕션 참조 14건 — 실 게이트로 배선됨 |
| 43 | `noise_gate.enable_noise_gate`<br>`NOISE_ENABLE_NOISE_GATE` | D-048 | `False` | `true` | 36 | 2026-06-29 `b7bbfd5` | 2026-07-07 `7dbc02c`<br>*(D-139 이전 제외)* | **존치** | `.env`가 코드 기본값을 뒤집음 — 운영이 실제로 쓰는 레버 |
## 판정 집계

| 판정 | 개수 | 처리 |
|---|---:|---|
| **기한부** | 5 | 2027-02-20 만료 — 도래 시 삭제 또는 사유부 연장 (P1-1·P1-2) |
| **상수화** | 3 | 분기 제거 후 상수화 — 플래그당 1커밋 (P1-2) |
| **존치** | 35 | 유지. 이 중 `.env`가 기본값을 뒤집는 4개는 **상수화 금지** |
| **삭제** | 0 | 즉시 삭제 대상 없음 — 참조 0건인 `prometheus_enabled`도 의도적 보류가 명시돼 있다 |
| 합계 | 43 | |

### 참조 하위 구간 (P1-1·P1-2 착수 대상)

| 참조 | 플래그 |
|---:|---|
| **0** | `alarm.prometheus_enabled` |
| **1** | `audit.db_enabled` · `audit.jsonl_enabled` · `noise_gate.anomaly_stl_enabled` |
| **2** | `noise_gate.change_correlation_enabled` · `noise_gate.decision_store_enabled` · `noise_gate.enable_agentic_enricher` · `observability.sql_log_enabled` |

## 개별 주의 사항

- **`alarm.prometheus_enabled` (참조 0)** — 구현체 `noise_gate/infrastructure/prometheus_client.py`는
  존재하나 이 플래그로 게이팅하는 호출부가 없다. `polestar_metric_baseline.py:24`에
  *"prometheus_client(폴백 채널·preparatory)는 §5.2 확정 설계상 배선하지 않는다"* 는 사유가 이미
  남아 있어 **의도적 예비 코드**다. 따라서 삭제가 아니라 기한 부여가 맞다(P1-1 선택지 ③).
- **`enable_deepagent_orchestration` 명명 부채** — 이름은 "deepagent"지만 실제로 가리키는 것은
  **트랙 A(의도 분해)** 이고, 트랙 B(deepagents 패키지)는 `enable_deepagents_package`다.
  사다리 2단과 1단을 각각 가리키는데 이름이 뒤섞여 오독을 유발한다 → L2에서 개명 검토.
- **tri-state 2개** (`enable_semantic_routing` · `enable_deepagent_orchestration`) — `bool | None`이며
  `None`이면 멀티 DB 등록 여부로 자동 결정된다. 운영 경로가 DB 등록 상태에 종속되므로
  기동 로그의 `resolved_by`로 발동 여부를 확인할 것(O1).
- **`observability.trace_enabled` / `sql_log_enabled`** — 2026-08-19 신설(D-140·D-141). 실사용
  이력이 없어 상수화 판정을 유보했다. 2027-02-20 재검토.

