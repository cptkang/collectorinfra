# 197. 노이즈 게이트 결함 정리 — 제니퍼 알람의 폴스타 경로 진입 · 여의도 `is_maintenance` 칼럼 없음 · 제니퍼 크로스호스트 억제

> **작성일**: 2026-10-27
> **상태**: **WIP** — W1·W2(PostgreSQL)·W3 구현 완료(2026-10-27 · 커밋 없음) · 잔여 W0(내부망 실측) · W2 DB2(b0) 분기 · §4 Q1·Q2 권고 적용(사용자 확인 전) · Q3 운영 판단 — §6 참조
> **회귀 대상 파일**: `config/settings_help/noise_gate_correlation.yaml` · `mcp_server/mcp_server/polestar_tools.py` · `noise_gate/application/nodes/alarm_context_enricher.py` · `noise_gate/application/signal_detection.py` · `noise_gate/domain/notification_policy.py` · `noise_gate/infrastructure/apm_noise_context.py` · `noise_gate/infrastructure/noise_signal_tools.py` · `noise_gate/infrastructure/polestar_hostname_resolver.py` · `noise_gate/infrastructure/polestar_noise_context.py` · `scripts/manual/content/admin.md` · `src/config/alarm.py` · `testdata/pg/init/01_create_tables.sql` · `testdata/pg/init/06_plan52_noise_fixtures.sql` · 테스트(`noise_gate/tests/test_polestar_noise_context.py` · `test_plan197_*.py` · `tests/test_api/test_settings_catalog.py` · `tests/test_composite/test_host_status_lookup.py` · `mcp_server/tests/test_polestar_tools.py`·`test_plan197_resource_status_verifier.py`)
> **분석 기준 HEAD**: `37b206e3`
> **계획 시작 SHA**: `37b206e3`
> **사용자 제기(2026-10-27)**: *"노이즈 컨텍스트 조회 건너뛰 - 미등록 db_id=polestar 등의 메시지가 지속적으로 나오고 있다"* → 내부망 본체 로그 사진 판독 뒤 *"발견된 문제를 수정 계획을 수립하라."*
> **근거**: 내부망 본체 터미널 로그 사진 1장(2026-10-27 17:48~17:49) · 저장소 코드 · 폴스타 PostgreSQL 스키마 덤프 `sample/cleaned_tables.json`
> **관련 계획·결정**: `plans/144`(제니퍼 노이즈 캔슬링 · §3 표 · §4.1 · §4.4) · `plans/81`(호스트 가용성 사전 점검) · D-048(graceful degradation) · D-109(크로스호스트 상관 · B-6) · D-175(가용성 판정 · G-4) · D-195 · D-317 · D-330 ③(수집 실패 PAGE 범위)

---

## 0. 로그 판독 (사진 기준 사실)

| 시각 | 로그 | 해석 |
|---|---|---|
| 17:48:49 | `DB 등록: polestar_b0` · `polestar_cm_gp` · `polestar_cm_yd` · `itam` | 폴스타 DB 등록은 정상이다. 미등록 경고는 폴스타 db_id 때문이 아니다 |
| 17:48:49 | `알람 이력 조회 건너뜀 — 미등록 db_id: jennifer_cm_prd` | 제니퍼 알람이 폴스타 이력 조회로 들어갔다 |
| 17:49:18 | `트랙 A 분류 — needed_signals=['message_signature','importance_maintenance','dependency']` 뒤 `노이즈 컨텍스트 조회 건너뜀 — 미등록 db_id: jennifer_cm_prd` ×2 | Advisory Enricher 트랙 A가 제니퍼 알람에 폴스타 신호 2종을 조회했다 |
| 17:49:18 | `발송 판단: tier=suppress reason=크로스-호스트 상관 — 클러스터 대표 외 억제` | 제니퍼 심각도 2 알람이 **통보 없이 억제**됐다 |
| 17:49:22~30 | `jennifer_b0`의 `SERVICE_EXCEPTION` · `BAD_RESPONSE_TIME_EXTERNAL_CALL`도 같은 경고 3줄 + 같은 억제 | 위와 같은 패턴이 반복된다 |
| 17:49:31·36 | `노이즈 컨텍스트 resource 조회 실패 — db_id=polestar_cm_yd …: svr.is_maintenance 칼럼 없음` | 여의도 폴스타의 중요도·점검 신호 조회가 실패한다 |

사용자가 처음에 말한 「`db_id=polestar`」는 로그상 `jennifer_*`와 `polestar_cm_yd` 메시지를 줄여 부른 것으로 본다. 등록 목록에 `polestar`(로컬 샌드박스 id)는 없고, 그 id로 찍힌 줄도 사진에 없다.

---

## 1. 결함 목록

### F1 (A) 제니퍼 알람이 폴스타 전용 수집 경로로 들어감 — 코드 확인

**영향**: 판정은 바뀌지 않는다(폴스타 신호는 제니퍼에 원래 없으므로 「없음」으로 처리된다). 하지만 알람 1건마다 WARNING이 3줄씩 쌓여, 실제 수집 실패 경고가 이 소음에 묻힌다. 사용자가 이번에 제기한 것도 이 때문이다.

| 위치 | 현재 | 제니퍼 거름 |
|---|---|---|
| `noise_gate/application/nodes/alarm_context_enricher.py:448-466` `_collect_history` | `repo.is_db_registered` 실패 → WARNING | ❌ — `plans/144` §3 표는 「폴스타 이력 — 제니퍼는 None 또는 명시 생략」으로 정했지만 명시 생략이 없다 |
| `noise_gate/application/nodes/agentic_enricher.py:87-111` `_collect_track_a` → `noise_gate/infrastructure/noise_signal_tools.py` `collect_importance_maintenance`·`collect_dependency_status` → `PolestarNoiseContextRepository.fetch` | `polestar_noise_context.py:502-508` 미등록 → WARNING ×2 | ❌ |
| 트랙 B(`build_noise_signal_tools`) — 같은 두 도구 | 같음 | ❌ (로그에는 트랙 A만 보이지만 경로는 대칭이다) |
| `alarm_context_enricher.py:529` `_collect_noise_context` | `apm_noise_ctx`가 있으면 제니퍼 정책 공급자로 간다 | ✅ (`NOISE_APM_NOISE_POLICY_ENABLED=true`일 때만) |

**고침(동작 불변 · 로그만 정리)**:
- `_collect_history`에서 `is_apm_source(event)`이면 DEBUG 1줄을 남기고 `None`을 돌려준다(명시 생략 — `plans/144` §3 표와 맞춘다).
- `noise_signal_tools.collect_signal`에서 제니퍼 이벤트(`is_apm_db_id(event.db_id)`)의 `importance_maintenance`·`dependency`는 폴스타를 부르지 않고 `None`을 돌려준다. `message_signature`(조건 로그 시그니처 스캔 · DB 무관)는 그대로 둔다. 트랙 B의 두 도구도 같은 판정을 쓴다(대칭).
- `PolestarNoiseContextRepository.fetch`의 미등록 WARNING은 **그대로 둔다**. 폴스타 알람의 진짜 미등록(설정 누락)을 알리는 신호이기 때문이다.

**판정 불변 근거**: 바뀌기 전에도 세 경로 모두 결과가 `None` 또는 `source="unavailable"`이었다. 트랙 A의 `collector`에는 `None`이 들어가지 않으므로 승격 후보도 같다.

**선택(§4 Q1)**: 트랙 A는 제니퍼 알람마다 LLM 분류를 1회 부른다. 제니퍼에서 실제로 쓸 수 있는 신호가 `message_signature` 하나뿐이면, 제니퍼 알람은 enricher를 통째로 건너뛰어 LLM 호출을 아낄 수 있다. 다만 이 경우 승격 경로가 하나 줄어 판정이 달라질 수 있으므로 기본안에서는 뺀다.

### F2 (A) 공동존 폴스타 `cmm_resource`에 `is_maintenance` 칼럼이 없음 — 로그 + 스키마 덤프 확인

**실측**:
- 로그: `polestar_cm_yd` 조회가 `svr.is_maintenance 칼럼 없음`으로 실패했다.
- 덤프 `sample/cleaned_tables.json`: `polestar.cmm_resource`에 `avail_status`는 있고 `is_maintenance`는 **없다**. 대신 별도 테이블 `polestar.cmm_resource_maintenance(resource_id, is_maintenance)`가 있다. 같은 덤프로 만든 `testdata/pg/init/04_create_all_tables.sql:1577`에도 그 테이블이 있다.
- 로컬 샌드박스 `testdata/pg/init/01_create_tables.sql:47`은 `cmm_resource.is_maintenance`를 직접 가진다. **로컬 테스트가 운영 스키마와 다르게 통과해 온 원인**이다.
- D-175 G-4의 「b0(DB2)도 `IS_MAINTENANCE` 보유」 근거는 `testdata/01_create_tables.sql`(샌드박스)이다. 운영 DB2 실측이 아니다.

**영향 범위 — 같은 칼럼을 쓰는 곳 4곳**:

| 위치 | 쓰는 기능 | 실패 시 결과 |
|---|---|---|
| `noise_gate/infrastructure/polestar_noise_context.py:80-106` `build_resource_signal_sql` | 노이즈 게이트 중요도·점검 신호 | resource 신호 전체 결손(중요도까지 잃음) → 보수적 처리. 점검 중 억제가 작동하지 않는다 |
| `noise_gate/infrastructure/polestar_hostname_resolver.py:78-100` `build_hostname_sql` | `lookup_host` — 조사 트리거 hostname 해소(`investigation_trigger.py:715`) · 채팅 장애 진단(`src/nodes/fault_diagnosis.py:295`) · 영향 프로세스(`src/orchestration/process_query.py:276`) · 알람 API(`src/api/routes/alarm.py:2488`) · 가용성 사전 점검(D-175) | fail-open이라 hostname이 `None`이 된다. 공동존 서버 조사·프로세스 보강·가용성 판정이 조용히 빠질 수 있다 (**W0에서 확인**) |
| `polestar_hostname_resolver.py:103-131` `build_host_status_sql` | `lookup_hosts` 다대상 · 조사 감사 SQL 표시(`src/orchestration/investigation_audit.py:123-124`) | 같음 |
| `mcp_server/mcp_server/polestar_tools.py:249-271` `build_resource_status_sql` | MCP 도구(자원 가용 상태) | 도구 오류 |

**고침**:
- PostgreSQL(gp·yd)에서는 `LEFT JOIN {schema}.cmm_resource_maintenance M ON M.resource_id = <자원 ID>`를 붙이고 `M.is_maintenance AS …`로 읽는다. 행이 없으면 `NULL`이 되고, 지금도 `NULL`은 「점검 아님/미상」으로 다룬다(`_parse_maintenance` · `judge_availability`).
- DB2(b0)는 W0 실측으로 정한다. ① `cmm_resource.IS_MAINTENANCE`가 있으면 현행을 유지한다. ② 없고 `CMM_RESOURCE_MAINTENANCE`가 있으면 PostgreSQL과 같은 JOIN을 쓴다. ③ 둘 다 없으면 `NULL AS is_maintenance`로 둔다.
- 분기는 각 파일에 이미 있는 db_id·엔진 분기 표(`_SCHEMA_BY_DB_ID` · `db_engine`)에 둔다. noise_gate와 mcp_server는 import 경계가 0이라(D-139) 각자 한 곳씩 둔다. 공용 추상은 만들지 않는다.
- 로컬 샌드박스를 운영과 맞춘다. `01_create_tables.sql`의 `cmm_resource.is_maintenance`와 `06_plan52_noise_fixtures.sql`의 점검 픽스처를 `cmm_resource_maintenance` 행으로 옮긴다. 그래야 F2가 로컬에서 재현되고 회귀가 잡힌다. **이 이동은 로컬 PG 컨테이너를 다시 만들어야 반영된다.**
- SQL 문자열을 단언하는 테스트는 repo 전체 grep(`is_maintenance`·`IS_MAINTENANCE`)으로 찾아 함께 고친다.

### F3 (B) 제니퍼 심각도 2 알람이 크로스호스트 상관으로 통보 없이 억제됨 — 코드 확인 · 운영 영향은 W0

**사실**:
- 로그의 제니퍼 알람 3건이 모두 `tier=suppress`(크로스-호스트 상관 · step 7.5)로 끝났다.
- `plans/144` §3 표에서는 정책이 꺼져 있으면 제니퍼 알람이 step 5 「수집 실패 → PAGE」에서 끝나 상관 단계에 닿지 않는다. 그런데 로그는 상관 단계에 닿았다. 따라서 내부망에서는 `NOISE_APM_NOISE_POLICY_ENABLED` 또는 `collection_failed_page_sev3_only`(D-330 ③) 중 하나가 켜져 있다고 본다(W0에서 확인).
- 상관 토큰(`noise_gate/domain/correlation.py:56` `signature_tokens`)은 `alarm_name`·`resource_type`·시그니처 라벨에 제니퍼 extra(`apmkind…`·`apmdomain…` — `apm_noise_context.py:166`)를 더한다. 스코프는 `event.db_id`(= `jennifer_<소스>`)다.

**위험(코드로 계산 — 운영 실측 아님)**:
1. **다른 앱끼리 묶임**. `SERVICE_EXCEPTION`처럼 흔한 유형은 도메인이 달라도 토큰이 `{service, exception, apm, instance}`로 거의 같다. 도메인 토큰 1개가 달라도 Jaccard는 4/6≈0.67로 임계 0.5를 넘는다. 즉 같은 소스 안의 **서로 다른 업무 앱 장애가 120초 창 안에서 한 군집으로 묶여 대표 1건만 남는다**. `plans/144` §4.4가 의도한 묶음은 「같은 도메인의 여러 인스턴스」였다.
2. **대표가 통보되지 않았을 수 있음**. 대표는 첫 도착 알람이고(D-109 · 소급 선출 없음), 대표 자신의 등급은 확인하지 않는다. 대표가 지속 조건 강등(`plans/144` §4.2 · 하한 DASHBOARD)으로 PAGE가 아니면 **군집 전체가 PAGE 없이 끝난다**. 이 문제는 폴스타에도 같은 구조로 있다. 제니퍼는 대표 강등이 정책으로 흔해서 더 크게 드러난다.
3. **SUPPRESS는 관제 화면에서도 사라진다**. D-317은 크로스소스 강등을 「SUPPRESS 없음 · DASHBOARD 상한」으로 정했다. 제니퍼 알람만 step 7.5를 거쳐 SUPPRESS까지 내려가는 것은 그 결정의 취지와 어긋난다.

**고침 후보(§4 Q2에서 선택)**:
- (a) **권고** — 제니퍼 이벤트의 상관 스코프를 `db_id|domain_id`로 좁힌다. 도메인이 다르면 아예 같은 군집 후보가 되지 않는다(§4.4 의도와 일치). 그리고 제니퍼 이벤트의 step 7.5 결과는 **DASHBOARD 상한**으로 둔다(D-317과 같은 원칙). `domain_id`가 없는 이벤트는 현행 스코프를 쓴다.
- (b) 제니퍼 이벤트를 step 7.5에서 빼고, 사건 묶음은 크로스소스 사건(W3 · `EpisodeTracker`)에만 맡긴다.
- (c) 현행 유지 + 측정만 한다(W0에서 억제 건수·대표 등급을 본 뒤 다시 결정).
- 위험 2(대표 미통보)는 폴스타에도 걸린 구조라 이 계획에서 고치지 않는다. W0 측정 결과만 남기고, 필요하면 별도 계획으로 뺀다.

---

## 2. Wave

| Wave | 내용 | 산출 | 검증 |
|---|---|---|---|
| **W0** (내부망 · 로컬 불가) | §5 체크리스트 | 실측 기록(이 계획서 §5에 기입) | — |
| **W1** F1 | `_collect_history` 제니퍼 명시 생략 · `noise_signal_tools` 제니퍼 폴스타 신호 생략(트랙 A·B 대칭) | `alarm_context_enricher.py` · `noise_signal_tools.py` · 테스트 | 제니퍼 이벤트로 WARNING 0줄 · `fetch` 미호출 · 승격 후보 동일 · 폴스타 이벤트는 현행 그대로(미등록 WARNING 유지) |
| **W2** F2 | PostgreSQL 점검 칼럼을 `cmm_resource_maintenance` LEFT JOIN으로 · DB2는 W0 결과대로 · 로컬 샌드박스 DDL·픽스처 이동 | `polestar_noise_context.py` · `polestar_hostname_resolver.py` · `mcp_server/.../polestar_tools.py` · `testdata/pg/init/01·06` · 테스트 | SQL 단위 테스트(엔진별) · 로컬 PG 재생성 뒤 점검 픽스처 서버 `maintenance=True` · hostname 해소 성공 · `mcp_server` 자체 pytest |
| **W3** F3 | Q2 선택안 구현 — 기본 플래그 off(비트 동일) | `signal_detection.py` · `notification_policy.py` · config 1개 · 테스트 | 다른 도메인 같은 유형 2건 → 군집 안 됨 · 같은 도메인 2건 → 군집되고 DASHBOARD · 폴스타 알람 판정 불변 · off면 비트 동일 |

- W1·W2는 서로 독립이라 병렬로 진행할 수 있다. W3은 W0의 F3 측정과 Q2 결정 뒤에 한다.
- W2의 DB2 분기는 W0의 b0 실측 전까지 현행(칼럼 직접 참조)을 유지한다. PostgreSQL만 먼저 고쳐도 운영 결손(gp·yd)은 해소된다.
- 매뉴얼(D-255): W1·W2는 내부 수집 결함 교정이라 매뉴얼 대상이 아니다. 다만 관리자 매뉴얼 「거의 모든 알람이 PAGE다」 항목(`scripts/manual/content/admin.md`)의 원인 목록에 「점검 칼럼 위치」를 추가할지 W2 끝에 판단한다. W3은 새 플래그가 생기므로 `config/settings_help`·관리자 매뉴얼을 갱신한다.
- 결정 기록: W2 끝에 D-175에 부기(G-4 근거 정정 · 공동존 점검 테이블). W3 끝에 D-109·D-317에 부기하거나 새 D-번호를 둔다(Q2 결과에 따름).
- 회귀(D-303): 계획 끝에 `regress.py --base <계획 시작 SHA> --files <바꾼 파일>` 1회. `mcp_server`는 `../.venv/bin/python -m pytest`로 따로 돈다.

---

## 3. 원인 관계

```
제니퍼 알람 (db_id = jennifer_<소스>)
 ├─ 이력 조회 ─────────────── 폴스타 전용인데 거름 없음 ─→ WARNING (F1)
 ├─ Advisory Enricher 트랙 A/B ─ 폴스타 신호 조회 ────────→ WARNING ×2 (F1)
 ├─ 노이즈 컨텍스트 ─────── 제니퍼 정책 공급자(정책 on) ─→ step 5 통과
 └─ 크로스호스트 상관(step 7.5) ─ 도메인 넘어 묶임 ─────→ SUPPRESS (F3)

폴스타 알람 (polestar_cm_gp / yd)
 └─ resource 신호·hostname 해소·MCP 자원 상태
       └─ cmm_resource.is_maintenance 참조 ─ 칼럼 없음 ─→ 조회 실패 (F2)
```

---

## 4. 사용자 결정 필요

| # | 질문 | 선택지 | 권고 |
|---|---|---|---|
| Q1 | 제니퍼 알람에 Advisory Enricher(LLM 1회)를 계속 돌릴까? | ① 유지(폴스타 신호만 생략 — 판정 불변) ② 제니퍼는 enricher 전체 생략(LLM 절약 · `message_signature` 승격 경로가 사라짐) | ① — 판정 불변으로 먼저 소음을 없애고, ②는 W0에서 제니퍼 알람의 `message_signature` 승격 실적을 본 뒤 정한다 |
| Q2 | 제니퍼 크로스호스트 억제를 어떻게 할까? | (a) 도메인 스코프 + DASHBOARD 상한 (b) 제니퍼 step 7.5 제외 (c) 유지·측정만 | (a) — `plans/144` §4.4 의도(같은 도메인 인스턴스 폭풍)와 D-317 원칙(SUPPRESS 없음)에 둘 다 맞는다 |
| Q3 | 내부망에서 제니퍼 억제가 이미 일어나고 있다. W3 전까지 임시 조치를 할까? | ① 그대로 둠 ② 내부망 `.env`에서 `NOISE_CROSS_HOST_CORRELATION_ENABLED=false`(폴스타 상관도 함께 꺼짐) | W0의 억제 건수를 보고 정한다. 다른 앱 장애가 묶인 사례가 1건이라도 확인되면 ② |

---

## 5. W0 내부망 실측 체크리스트 (로컬 불가)

1. **플래그**: 내부망 `.env`의 `NOISE_APM_NOISE_POLICY_ENABLED` · `NOISE_COLLECTION_FAILED_PAGE_SEV3_ONLY`(D-330 ③) · `NOISE_CROSS_HOST_CORRELATION_ENABLED` · `NOISE_ENABLE_AGENTIC_ENRICHER` 값.
2. **F2 스키마**(읽기 전용 SELECT):
   - gp·yd: `SELECT column_name FROM information_schema.columns WHERE table_schema='polestar' AND table_name IN ('cmm_resource','cmm_resource_maintenance') AND column_name IN ('id','is_maintenance','resource_id')`
   - b0: `SELECT COLNAME, TABNAME FROM SYSCAT.COLUMNS WHERE TABSCHEMA='POLESTAR' AND TABNAME IN ('CMM_RESOURCE','CMM_RESOURCE_MAINTENANCE') AND COLNAME IN ('ID','IS_MAINTENANCE','RESOURCE_ID')`
   - `cmm_resource_maintenance`의 행 수와 `is_maintenance` 값 분포(점검 등록이 실제로 이 테이블에 쌓이는지).
3. **F2 영향**: 본체 로그에서 `polestar_cm_gp`의 같은 오류 여부 · `hostname 해소`·`가용성` 관련 경고 건수(공동존 서버 조사·프로세스 보강이 빠지고 있는지).
4. **F3 측정**(결정 저장소 · 최근 7일): 제니퍼 알람의 `stage=correlation` SUPPRESS 건수 · 그 군집 대표(`representative_fp`)의 최종 등급 분포 · 대표와 멤버의 `domain_id`가 다른 건수.
5. **F1 확인**(선택): 제니퍼 알람의 Advisory Enricher `message_signature` 승격 건수(Q1 판단용).

---

## 6. 구현 결과 (2026-10-27 · 계획 시작 SHA `37b206e3` · 커밋 없음)

**권고 적용 · 사용자 확인 전**: Q1 = ①(enricher 유지 · 폴스타 신호만 생략) · Q2 = (a)(도메인 스코프 + DASHBOARD 상한 · 새 플래그 기본 off) · F2 DB2는 W0 전 현행 유지. Q3는 운영 조치라 범위 밖.

| Wave | 바꾼 것 | 결과 |
|---|---|---|
| W1 F1 | `_collect_history` 제니퍼면 DEBUG + `None` · `collect_importance_maintenance`·`collect_dependency_status` 제니퍼(`is_apm_db_id`)면 fetch 없이 `{"signal", "context": unavailable_noise_ctx()}`(트랙 A·B 공용) · 폴스타 미등록 WARNING 유지 | 새 테스트 11건 |
| W2 F2 | PostgreSQL 4곳 `LEFT JOIN (SELECT resource_id, MAX(is_maintenance) … GROUP BY resource_id)` · DB2는 종전 문자열 비트 동일(`polestar_noise_context._is_db2_dialect` — domain_config `db_engine == "db2"`일 때만) · 샌드박스 01 칼럼 제거 · 06 점검 픽스처 → `cmm_resource_maintenance` 행 | SQL 단위·SQLGuard·sqlite 실행(중복 행·NULL 의미) |
| W3 F3 | `NOISE_APM_CORRELATION_DOMAIN_SCOPE_ENABLED`(기본 off) — 제니퍼 스코프 `db_id|domain`(`apm_noise_context.correlation_domain` 공용) · step 7.5 제니퍼는 매트릭스 > DASHBOARD일 때만 DASHBOARD(그 밖 통과) · settings_help·관리자 매뉴얼 · 카탈로그 수 447→449(+1은 plans/196 `NOISE_HOST_LINK_ENABLED` 미반영분) | 새 테스트 15건 · test_manual 695 |

- **회귀(계획 끝 1회)**: `regress.py --base 37b206e3 --files <회귀 대상>` rc=0 — 본체 통과 2,670 · 실패 0 · 건너뜀 30 / mcp_server 통과 1,118 · 실패 0 · 건너뜀 21 · arch·overfit·config_fallback·ruff·mypy 통과(diff 줄 0) · `범위: 모듈 단위 — 전체 미실행` · `[전체 회귀 권고] 사유: 허브 모듈 변경(전이 import 영향 ≥ 85%) — src.config.alarm 88%`.
- **검증**: verifier 1라운드 Critical·Major 0 · Minor 4(매뉴얼 버퍼 상한 문구는 교정 · 나머지 3건은 아래 잔여). verifier 추가 테스트 `noise_gate/tests/test_plan197_verifier.py`(12) · `mcp_server/tests/test_plan197_resource_status_verifier.py`(3).
- **계획서와의 이탈**: F1에서 `collect_signal`이 `None`이 아니라 종전 fetch 미등록 분기와 같은 `unavailable_noise_ctx()`를 돌려준다 — collector·기록 `signals`·승격 후보 비트 동일(되돌리는 비용: 두 함수의 분기 2줄).
- **결정·기록**: D-349 신설 · D-109·D-317·D-175 부기 · `docs/18` 1건(샌드박스 DDL이 운영 스키마와 달라 결함 은폐).
- **잔여**:
  1. W0 §5 전 항목 + 운영 `cmm_resource_maintenance.is_maintenance` 타입(boolean이면 `MAX` 실패 — 덤프는 smallint).
  2. W2 DB2(b0) 분기 — W0 결과대로 ①유지 ②JOIN ③`NULL`.
  3. 로컬 PG 컨테이너 재생성 필요(01·06 반영 전에는 로컬 DB가 옛 모양). `testdata/pg/05_insert_excel_data.sql`(init 밖 · 생성기 `scripts/excel_to_pg_insert.py:34`)은 아직 `cmm_resource.is_maintenance`에 값을 넣는다 — 재생성한 DB에 수동 적재하면 실패한다(후속 정리 대상).
  4. 정책 off(`NOISE_APM_NOISE_POLICY_ENABLED=false`)일 때 `_collect_noise_context` → `enrich_noise_context` 경로로 제니퍼 알람마다 미등록 WARNING 1줄이 남는다(§1 표의 ✅ 행은 정책 on 전제 · W0 플래그 확인 뒤 판단).
  5. 제니퍼 판정 기준이 이력(`is_apm_source` — 원문 source 포함)과 신호(`is_apm_db_id`)에서 다르다 — 실발생 가능성 낮음.
  6. 위험 2(대표 미통보)는 범위 밖(W0 측정 뒤 별도 계획) · `.env.example`에 새 키 미기재.
