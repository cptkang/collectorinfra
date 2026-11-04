# 210. APM·SMS·DPM 연계 정보 DB — 장애 진단 고속 경로(매핑 있으면 DB · 없으면 직접 조회 후 저장) · 사전 스캔 · 관리자 관리

> **작성일**: 2026-11-03
> **상태**: **WIP** — 2026-11-03 착수·구현 완료(사용자 지시 *"210번 계획을 구현하라."* · 커밋 없음) · W1~W7 구현 · verifier(Critical 0 · Major 2)·보안 감사(Critical·High 0 · Medium 2) 교정 반영(D-362 부기 1) · 계획 끝 회귀 1회 + 교정 파일 회귀(원래 실패만) · 플래그 기본 off · **W8 채팅 노드 주소 결정성 추가·구현(2026-11-03 · §6.2 · 사용자 지시 *"남은 문제를 해결하라."* · verifier Critical 0 · Major 2 중 1 교정·1 W0 실측 잔여 · D-362 부기 2)** · **W9 노드 주소 조회 지연 상한(호출당 5초 예산 · 소스 간 병렬) · 서버명 경로 파드 노드 주소 부착(2026-11-03 · §6.3 · 사용자 지시 *"남은 문제 1, 2번도 해결하라."* · verifier Critical 0 · Major 1 교정 · D-362 부기 3 · D-355 부기 5)** · **잔여 = W0 폐쇄망 실측(§7 1~9 · 점검 스크립트 `noise_gate/scripts/probe_apm_host_map.py`)** · ITAM 표·칼럼은 추정(W0-4 확정 대기) · 매뉴얼 캡처 미실행(로컬 Docker·Redis 미기동)
> **분석 기준 HEAD**: `171bf832` (origin/main · plans/209·D-360·D-361 포함)
> **계획 시작 SHA**: `171bf832`
> **회귀 대상 파일**: W1 `src/config/alarm.py` `src/api/server.py` `ddl/apm_host_map.sql` `noise_gate/domain/apm_host_map.py` `noise_gate/infrastructure/apm_host_map_repository.py` `noise_gate/infrastructure/apm_host_map_cache.py` `noise_gate/tests/test_plan210_w1_{domain,repository,cache,startup}.py` · W2 `apm_gateway/apm_gateway/application/node_address.py` `apm_gateway/apm_gateway/application/node_address_tools.py` `apm_gateway/apm_gateway/interface/server.py` `config/db_registry.yaml` `src/orchestration/apm_query.py` `src/orchestration/apm/{node_address,builders,calls,common,collect}.py` `tests/test_orchestration/test_plan210_w2_node_address_view.py` `tests/test_orchestration/test_plan130_w4_targets.py` `tests/test_orchestration/test_plan134_w34_verify_schema.py` `tests/test_orchestration/test_plan134_w567_body_verify.py` `tests/test_routing/test_plan125_registry.py` `tests/test_routing/test_plan87_j8_registry_sources.py` · W4(sre_agent 자체 venv) `sre_agent/sre_agent/application/{playbook_runner,playbook_steps,evidence_prefetch,investigation_guidance}.py` `sre_agent/sre_agent/domain/correlation.py` `sre_agent/sre_agent/interface/mcp_service.py` · W4 보정(팀 리드) `sre_agent/sre_agent/application/playbook_steps.py` `APM_SOURCE_IDS_TOOLS` · W3 `noise_gate/application/apm_host_map_lookup.py` `noise_gate/application/apm_host_map_consumer.py` `noise_gate/application/host_candidates.py` `noise_gate/application/nodes/investigation_trigger.py` `noise_gate/application/nodes/notification_gate.py` `noise_gate/infrastructure/decision_store.py` `noise_gate/infrastructure/apm_gateway_client.py` `src/api/server.py` `src/api/routes/alarm.py` `noise_gate/tests/test_plan210_w3_{fast_path,consumer,manual}.py` `tests/test_api/test_plan210_w3_manual_event.py` `tests/test_api/test_plan196_verify.py`(basis 계약 집합에 `apm_map` 가산 — 팀 리드) · W5 `config/knowledge/itam/service_host_lookup.yaml` `noise_gate/domain/apm_host_map_itam.py` `noise_gate/domain/apm_host_map_scan.py` `noise_gate/infrastructure/apm_host_map_scanners.py` `noise_gate/infrastructure/apm_host_map_sql_scanners.py` `noise_gate/infrastructure/apm_host_map_scan_lock.py` `noise_gate/application/apm_host_map_scan.py` `noise_gate/scripts/probe_apm_host_map.py` `noise_gate/tests/plan210_w5_fakes.py` `noise_gate/tests/test_plan210_w5_{itam_chain,sql,scan,probe}.py` · W5 통합(팀 리드) `ddl/apm_host_map.sql`·`src/api/server.py`(`clue_text` · ITAM 처리기·스캔 서비스 배선)·`noise_gate/infrastructure/apm_host_map_repository.py`(`SNAPSHOT_COLUMNS`) · W6 `src/api/routes/admin_apm_host_map.py` `src/api/server.py`(라우터 등록) `src/static/admin/dashboard.html` `src/static/js/admin-apm-host-map.js` `src/static/js/diagnosis-view.js` `tests/test_api/test_plan210_w6_api.py` `tests/test_api/test_ui_plan210_w6.py` · 이음매 교정(팀 리드) `noise_gate/application/apm_host_map_lookup.py`(파드 미확정 `pod: true`) `noise_gate/application/apm_host_map_consumer.py`(처리기 반환형) `sre_agent/sre_agent/application/playbook_runner.py`(`POLESTAR_APM_MAP_POD_UNRESOLVED`) `sre_agent/tests/test_plan210_w4_apm_map_link.py` · 교정 `noise_gate/tests/test_plan210_fix_round.py` `noise_gate/tests/test_plan210_verify_fast_path.py` `tests/test_api/test_plan210_verify_ui.py` `noise_gate/tests/plan210_w5_fakes.py`(+ 기존 목록 파일 재수정) · W8 `apm_gateway/apm_gateway/application/{instance_tools,node_address,node_address_tools}.py` `apm_gateway/apm_gateway/interface/server.py` `apm_gateway/tests/test_plan210_w8_node_address_scope.py` `apm_gateway/tests/test_plan210_w2_node_addresses.py` `apm_gateway/tests/test_plan202_payload.py` `src/orchestration/apm_query.py` `src/orchestration/apm/{node_address,builders,context,targets}.py` `config/db_registry.yaml` `tests/test_orchestration/test_plan210_w8_{node_address,verify}.py` `tests/snapshots/plan210_w8/before_answers.json` `scripts/manual/content/user.md` `src/static/manual/{user,admin}.html` `src/static/manual/help_index.json` · W9 `apm_gateway/apm_gateway/application/{node_address,node_address_tools,instance_tools,apm_core}.py` `apm_gateway/apm_gateway/config.py` `apm_gateway/apm_gateway/interface/{config_admin_adapter,server}.py` `apm_gateway/.env.example` `apm_gateway/tests/test_plan210_w9_{budget,verify}.py` `src/orchestration/apm_query.py` `src/orchestration/apm/node_address.py` `tests/test_orchestration/test_plan210_w9_{host_node,verify}.py` `tests/snapshots/plan210_w9/before_answers.json` `noise_gate/infrastructure/apm_host_map_scanners.py`(독스트링) `scripts/manual/content/{user,admin}.md` `src/static/manual/{user,admin}.html` `src/static/manual/help_index.json`
> **개정 이력**
> - 초안(2026-11-03): 사용자 지시 *"hostAddress 는 pod의 ip가 나온다. 방법은 아래와 같다. 제니퍼 인스턴스 네임으로 domain_name, domain_description을 조회하여 나온 값과 itam의 업무 정보, 서비스 정보등을 확인하여 해당 호스트의 ip를 검색한다. 이 검색된 정보를 내부 db에서 apm, sms 연계 정보를 관리하고 향후 동일한 제니퍼 인스턴스가 나올 경우 해당 정보를 이용하여 폴스타(SMS)를 연동하여 장애 진단을 할 수 있도록 한다. 연계 정보는 운영자가 관리 할 수 있게 하여 자동을 생성된 정보가 잘못 매핑되어있으면 수정할 수 있어야하고, 수동으로도 추가할 수 있도록 해야 한다."*
> - 개정 1: *"계획서에 사전에 apm, sms, dpm(향후) 을 스캐닝하여 연계정보를 저장하는 기능을 추가하라."* → 사전 스캔 · 스냅샷 대조
> - 개정 2: *"스캔은 관리자 페이지에서 수동으로 실행시키도록 한다."* → 주기 스캔 삭제
> - **개정 3(인터뷰 반영)**: *"apm, dpm, sms 연계정보를 별도로 관리하는 이유는 장애 진단시 좀더 빠른 진단을 위해 매핑정보를 DB에서 이용하려는 목적이 있다. 매핑정보가 없으면 직접 조회하지만 매핑정보가 있으면 DB에서 조회하여 장애 진단 속도를 높이는 목적으로 계획을 업데이트하라."* → **목적을 「진단 고속 경로」로 재정의**(§1) · 매핑 hit = 실시간 조회 0회 · miss = 직접 조회 후 결과를 매핑으로 되적재(§3.1) · `hostAddress`를 알람·스캔·조사·채팅 4경로에서 코드가 호출(게이트웨이 도구·채팅 보기 신설 · §3.6) · 대상 여러 대면 전부 조사(≤10) · 관리자만 관리 · 파드 IP 블록 대조 삭제
> **근거**: 저장소 코드 실측(2026-11-03) · 사용자 인터뷰(2026-11-03 · §8) · 조사 보고서 `reports/K8s 파드 관측 연동 방안.md`
> **관련 계획·결정**: D-348(호스트 연결 학습 — ② 예외) · D-355·`plans/202`(노드 주소 — 이 계획의 1순위 직접 조회 경로) · D-188 · D-224 · D-049(워커 PG 쓰기 금지) · D-139 · D-274(게이트웨이 경계) · D-300 ⑧(APM 보기 단어 매칭 0) · D-341 ⑤(DPM 슬롯) · D-360(폴스타 식별자 0건 재해소) · D-255 · D-303 · **D-362**(이 계획 — 예약)

---

## 0. 전제 — `hostAddress`가 호출되지 않던 원인 (인터뷰 I-3·I-4)

- `hostAddress`(파드가 도는 노드 IP)는 제니퍼 Open API에 없고 **웹 내부 API `GET /agent/upgrade/list?sid=`를 웹 세션으로만** 읽는다(`apm_gateway/apm_gateway/adapters/jennifer/web_session.py:1-40`).
- 코드는 `JENNIFER_NODE_ADDRESS_ENABLED=true` **그리고** 소스별 `JENNIFER_<ID>_WEB_USER`·`_WEB_PASSWORD`가 있을 때만 호출한다(`application/node_address.py:398-408`). 폐쇄망 `.env`에 **둘 다 비어 있었다**(사용자 확인 2026-11-03) → 설계대로 「호출 안 함」이었고, 알람 `ipAddress`(Open API 인스턴스 IP = 파드 IP)만 보였다.
- 사용자가 값을 넣고 테스트한다(W0-1). 그 결과와 무관하게 이 계획은 ① 호출 못 한 사유를 침묵하지 않고 드러내며 ② 채팅·스캔에서도 호출하도록 경로를 연다(§3.6).
- 게이트웨이 한계(실측): `apm_instance_map`은 도메인 3개 이하일 때만 웹 조회를 하고 넘으면 캐시만 쓴다(`MCP_FETCH_DOMAIN_LIMIT = 3` · `node_address.py:61` · `instance_tools.py:78-102`). 전 도메인 스캔에는 별도 도구가 필요하다(§3.6).

---

## 1. 목적과 목표

### 1.1 목적 (개정 3 — 사용자 정의)

**장애 진단 속도.** APM 알람이 오면 지금은 매번 실시간으로 대상 서버를 찾는다. 연계 정보 DB에 매핑이 있으면 **DB만 보고 바로** 폴스타(SMS)·DPM 대상을 정하고, 없을 때만 직접 조회한 뒤 그 결과를 저장해 다음부터 빠르게 한다.

| 단계(현행 · 알람마다) | 위치 | 비용 |
|---|---|---|
| 노드 주소 조회 | 게이트웨이 웹 세션(로그인·목록 · 캐시 60~900초) | 웹 왕복 1~3회 |
| 노드 IP → 폴스타 서버 | `node_address_link` — 활성 폴스타 존 **전부 순차** 프로브(`noise_gate/application/host_candidates.py:87-127`) | DB 질의 존 수(3)회 |
| 저장 연결 조회 | D-348 `hostlink:` (파드명 키 — 재기동마다 miss) | Redis 1회 |
| 폴스타 소스 결정 | `sre_agent` `resolve_polestar_source` — 없으면 설정 매핑 대체(존이 틀리면 0건 → 재해소 D-360) | 조사 중 추가 질의 |

**매핑 hit = Redis 조회 1회로 끝**(웹·폴스타 프로브·ITAM 0회) · 대상의 폴스타 확정 식별자(등록명·hostname·resource_id)까지 실어 조사 쪽 식별자 재해소(D-360)도 건너뛴다.

### 1.2 목표

1. **고속 경로**: 매핑이 있으면 실시간 조회 없이 대상 확정(§3.1) — 측정 가능한 지연 감소(§6.1).
2. **직접 조회 + 되적재**: 매핑이 없으면 직접 조회(노드 주소 → 폴스타 정확 일치 · VM 정확 일치)로 이번 조사를 진행하고, 결과를 매핑으로 저장한다. ITAM 경유 추정은 비동기로 만들어 다음 알람부터 쓴다.
3. **같은 인스턴스 재사용**: 인스턴스(파드명) → 워크로드 → 도메인 3단 키(§3.2).
4. **사전 스캔**: 관리자가 수동 실행 — APM·SMS·ITAM 목록 적재 + 일괄 매핑 생성(첫 알람부터 hit). DPM은 슬롯.
5. **관리자 관리**: 조회·수정·수동 추가·사용 중지·삭제·재생성. 관리자가 고친 매핑은 자동이 덮지 않는다.
6. **비우지 않기**(인터뷰 I-1 「A」): 대상이 여러 대면 전부(≤10) 조사 · 자동 매핑은 확인 없이 사용(근거 「자동 · 추정」 표기).
7. 플래그 off = 현행 비트 동일.

---

## 2. 현행 코드 실측 — 재사용할 것

| 필요 | 이미 있는 것 | 위치 |
|---|---|---|
| 노드 주소(알람 시점) | 폴러가 알람 도메인만 웹 조회 → `alarm:raw` `apm.host_address`·`host_address_status`(`ok`·`stale`·`not_found`·`disabled`·`login_failed`·`login_blocked`·`error`)·`apm.pod` | `apm_gateway/apm_gateway/application/poller.py:23` · `node_address.py:15-22` |
| 노드 IP → 폴스타 | `node_address_link`(전 존 정확 일치 · 실패 존 `unchecked`) | `noise_gate/application/host_candidates.py:87` |
| 폴스타 정확 일치 프로브 | `probe_hosts(names, prefixes, ips)` · DB2/PG 방언 | `noise_gate/infrastructure/polestar_hostname_resolver.py:104-109,158-205,301` |
| 힌트 부착 단일 지점 | `_attach_polestar_link`(자동·사건·수동 공통) | `noise_gate/application/nodes/investigation_trigger.py:683` |
| 조사 쪽 소비 | `resolve_polestar_source` · `evidence_prefetch`(linked `db_id`·`server_name`) | `sre_agent/sre_agent/application/playbook_runner.py:453` · `evidence_prefetch.py:108-191` |
| 도메인명·설명·인스턴스 | 인벤토리 캐시 · `apm_instance_map`(설명 마스킹본) · 인라인 500행 초과는 `apm_job_read` 청크 | `apm_gateway/.../instance_tools.py:116-135` · `interface/server.py:466,1287` |
| 채팅 APM 경로 | `apm_query` — 분해 LLM이 닫힌 보기 목록에서 고르면 코드가 고정 도구 호출(단어 매칭 0 · D-300 ⑧) | `src/orchestration/apm_query.py` · `config/db_registry.yaml:94` `solutions[apm].views` |
| 워크로드 이름 | `k8s_workload_name()` | `src/domain/host_names.py:43` |
| 내부 PG | auth 풀 · 멱등 DDL | `src/api/server.py:64-100,551-599` |
| 관리자 CRUD·감사 | `require_admin_user` + 재검증 · `log_admin_event` before/after | `src/api/routes/admin.py:924-1074` · `src/api/admin_audit.py` |
| ITAM 접근 | `DBRegistry(cfg).get_client("itam").execute_sql` → MCP readonly · MariaDB 가드 | `mcp_server/mcp_server/security.py:71-124` |
| 폴스타 NIC IP | `server.NetworkInterface` EAV `IP` | `config/db_profiles/polestar.yaml:177-182` |
| DPM | 조사 쪽 `SourceAdapter` 슬롯·스텁(「미연동」) | D-341 ⑤ |

**없는 것**: 연계 매핑 SQL 테이블 · 인벤토리 스냅샷·스캔 · ITAM 고정 SQL · 전 도메인 노드 주소 도구 · 채팅 노드 주소 보기 · 관리자 수정/추가 API · 다중 대상 힌트.

---

## 3. 설계

### 3.1 진단 시 조회 순서 — 고속 경로 우선

`_attach_polestar_link`(워커: 자동·사건 / API: 수동 재분석)가 아래 순서로 **첫 성립**에서 멈춘다.

| 순 | 출처 | 비용 | 성립 시 |
|---|---|---|---|
| 0 | 사용자 「이 서버로 다시 조회」 선택(현행) | 0 | 그 서버 |
| 1 | **매핑 manual·verified** (instance → workload → domain) | Redis 1회 | 매핑 대상 |
| 2 | **매핑 auto · instance**(이 파드명) | (같은 조회) | 매핑 대상 |
| 3 | 페이로드 `apm.host_address`(status `ok`·`stale`) → **SMS IP 색인**(Redis · 스캔이 적재) | Redis 1회 | 그 노드 1대 + 되적재 |
| 3' | 색인 miss → 직접 조회 `node_address_link`(전 존 프로브) | DB 존 수 | 그 노드 + 되적재 |
| 4 | **매핑 auto · workload/domain** | (1의 조회) | 대상 전부(≤10) |
| 5 | VM: 인스턴스 `ip`·`host_name` → SMS 색인 → miss면 `probe_hosts` | Redis 1회 / DB | 그 서버 + 되적재 |
| 6 | D-348 확정 연결(`host_link_enabled`일 때 · 현행) | Redis 1회 | 그 서버 |
| 7 | 미확정 — 사유(§3.7) + `no_config_fallback` · **ITAM 생성 요청**(비동기) | 0 | 다음 알람부터 4에서 hit |

- 1·2·4는 한 번의 조회(`HMGET` 3필드)로 판정한다. 매핑 `disabled`(관리자 「연계 없음」)는 1에서 `absent`로 끝난다.
- 3을 2와 4 사이에 둔 이유: 파드는 생애 동안 한 노드에 묶이므로 instance 매핑은 정확하다. 새 파드명(instance miss)이면 페이로드에 **이미 실려 온** 노드 주소가 워크로드 노드 집합보다 정확하고, SMS 색인이면 비용도 Redis 1회다.
- **되적재**: 3·3'·5의 결과는 워커가 Redis Stream `apmhostmap:req`에 `{type: learn, …}`로 넣고 API 소비자가 auto 매핑으로 upsert한다(D-049 — 워커 PG 쓰기 없음). instance 행(이 파드 → 노드)과 workload 행(노드 집합에 합집합 · ≤10)을 함께 갱신한다.
- 직접 조회 예산: 3'·5 합계 `NOISE_APM_HOST_MAP_LIVE_BUDGET_SECONDS`(5초) — 넘으면 다음 순으로.
- 플래그 off면 이 표 전체가 없고 현행 그대로(`host_link_enabled`·노드 주소 경로의 기존 조합 결과 비트 동일 — 조기 반환 구조는 새 플래그와 독립으로 재배치).
- **측정**: 판정마다 로그 1줄 `apm_map_lookup source=… order=<순> scope=… result=hit|index|live|miss ms=…` → 고속 경로 비율·지연을 운영에서 읽는다.

### 3.2 매핑 키 — 「같은 제니퍼 인스턴스」

| scope | match_key | 쓰임 |
|---|---|---|
| `instance` | `normalize_hostname(instance_name)` | 파드명(파드 생애 동안 노드 고정) · VM 인스턴스 · 관리자 개별 지정 |
| `workload` | `k8s_workload_name(instance_name)` | 파드 재기동 뒤에도 같은 키 — 노드 집합 |
| `domain` | `""` | 도메인 기본값 — ITAM 추정 결과 |

모든 키에 `(source_id, domain_id)` 포함. auto instance 행은 `last_used_at`·`last_seen` 30일 경과 시 정리(파드명은 계속 바뀌어 쌓인다) · manual은 유지.

### 3.3 저장 — PG 정본 + Redis 읽기 사본·색인

**`apm_host_map`** (`ddl/apm_host_map.sql` + `server.py` `_ensure_apm_host_map_tables` · auth 풀)

| 칼럼 | 설명 |
|---|---|
| `id` BIGSERIAL · UNIQUE(`source_id`,`domain_id`,`scope`,`match_key`) | |
| `domain_name` · `instance_example` | 표시용 |
| `origin` | `auto` · `manual` |
| `basis` | `node_address`(hostAddress) · `vm_exact` · `itam` · `manual` |
| `state` | `active` · `ambiguous`(자동 — 너무 넓음 · 사용 안 함) · `failed` · `disabled`(관리자 「연계 없음」) |
| `verified` | 관리자가 자동 결과 확인 — 이후 자동 갱신 금지 |
| `targets` JSONB | `[{kind:"sms", db_id, server_name, hostname, ip, resource_id, target_gone}]` ≤ `MAX_TARGETS`(10) · `kind`는 DPM 대비(§3.9) |
| `evidence` JSONB | 근거(단계·건수·ITAM 업무 코드/명·서버 호스트/IP · PII 없음) · `previous_targets` · `scan_suggests` |
| `note` · `created/updated_by/at` · `last_used_at` · `version`(낙관적 동시성) · `next_retry_at` | |

**Redis(API 프로세스가 유일한 기록자)**
- `apmhostmap:{source_id}` 해시 — 필드 `{scope}|{domain_id}|{match_key}` → `{id, origin, basis, state, verified, targets, evidence_text, version}`.
- `apmhostmap:smsip` 해시 — `ip` → 폴스타 서버(대표 IP + NIC IP) · `apmhostmap:smshost` — `hostname_norm` → 서버. 스캔 스냅샷에서 생성. 한 키에 서버 2대 이상이면 값에 목록(색인 판정은 「1대일 때만 성립」).
- 쓰기마다 갱신 + 기동 시 전체 재구성 + 10분 주기 대조. 사본이 비면 결과는 miss(직접 조회)일 뿐 오연결 없음.
- `last_used_at`은 워커가 직접 못 쓰므로 hit 시 `apmhostmap:req` `{type: touch}`로 넘긴다(소비자가 1분 단위로 묶어 갱신).

### 3.4 자동 생성 — 직접 조회 결과 · VM 정확 일치 · ITAM 사슬

| basis | 생성 시점 | 규칙(전부 `noise_gate/domain/apm_host_map.py` 순수 함수 · LLM 0) |
|---|---|---|
| `node_address` | 알람 되적재(§3.1-3) · 스캔(§3.5 노드 주소) | 노드 IP가 폴스타 서버 1대와 정확 일치 → instance 행 · workload 행에 합집합 |
| `vm_exact` | 알람 되적재(§3.1-5) · 스캔 | 인스턴스 `ip`/`host_name`이 폴스타 서버 1대와 정확 일치(대표 IP·NIC IP·정규화 hostname) · 2대 이상 = `ambiguous` |
| `itam` | 스캔 · 알람 miss 비동기 요청 | 도메인명·설명 토큰(일반어 제외) → ITAM 업무(코드 정확 → 명 정확 → 명 포함 · 첫 성공 단계) → 0건이면 서버 칸 직접 포함 → ITAM 서버(활성 · 상한 50 · 초과 = `ambiguous`) → 폴스타 정확 일치 → 대상 1~10 `active` · 0 `failed` · 업무가 서로 다른 서버 집합으로 갈림 = `ambiguous` |

- ITAM은 **스캔 스냅샷을 메모리 대조**한다(관측 DB에 외부 입력 값을 넣은 SQL 없음 · 주입 표면 0). 스냅샷 없음 = `apm_map_no_inventory`.
- 알람 miss 비동기 요청: 소비자가 그 도메인만 게이트웨이 `apm_instance_map(domain_id, source_ids)`로 다시 읽어 ITAM·SMS 스냅샷과 대조 → upsert. 중복 억제 `SET apmhostmap:inflight:{src}:{dom} NX EX 600`.
- 쓰기 규칙: 자동은 `origin=auto AND verified=false AND state<>'disabled'`만 갱신 · manual·verified는 결과가 달라도 `scan_suggests`만 남김. 관리자가 자동 행 수정 → `origin=manual`.
- ITAM 테이블·칼럼·일반어: `config/knowledge/itam/service_host_lookup.yaml`(폐쇄망 실측으로 갱신 · 식별자 `^[0-9A-Za-z_가-힣]+$` 검증 + 백틱 · `entity_locator._IDENT_RE` 재사용 안 함 — 한글 거부).

### 3.5 사전 스캔 (관리자 수동 실행만)

- **실행**: 관리자 페이지 「지금 스캔」 → `POST /scan` → API 프로세스 백그라운드 태스크 · 202 + `run_id` · 화면 폴링. 주기·예약 없음. 단독 실행 Redis 락(`SET NX EX 3600` + 30초 연장 · 실행 중 409 `scan_running`) · 기동 시 남은 `running` 행은 `aborted`.
- **실행 기록** `apm_host_map_scan_run`: `run_id`·`requested_by`·시각·`status`(running·ok·partial·failed·aborted)·`scanners`(스캐너별 `{status, rows, seconds, error_kind, unchecked_zones}`)·`link_summary`(`{new, changed, ambiguous, failed, gone, kept_manual}`) · 보존 90일.
- **스캐너**(개별 try · 실패 스캐너는 이전 스냅샷 유지 · 그 스냅샷을 쓰는 규칙만 건너뜀 · 성공 시에만 「현재 스냅샷」 포인터 교체)

| 스캐너 | 조회 | 적재 |
|---|---|---|
| APM 인벤토리 | 소스별 `apm_instance_map(source_ids=[src])` · 500행 초과는 `apm_job_read` 청크 | `apm_inventory`(도메인·설명·인스턴스·ip·host_name·workload_key) |
| APM 노드 주소 | 신규 게이트웨이 도구 `apm_node_addresses(source_ids, domain_ids)`(§3.6) — 전 도메인 순차 | `apm_inventory.host_address`·`host_address_status` · 소스별 `disabled`·`login_failed` 등은 실행 기록에 그대로 |
| SMS | 폴스타 존별 키셋 페이지(`SCAN_PAGE_ROWS` 1000 · DB2 `FETCH FIRST`/PG `LIMIT` · DB2 `POLESTAR`) `server.Server` `dtime IS NULL` + NIC EAV `IP` | `sms_inventory` → Redis 색인 재구성 |
| ITAM | 업무 표 전체 · 서버 원장 활성 행 키셋 페이지(백틱 · 필요한 칼럼만 · PII 칼럼 제외) | `itam_snapshot_app`·`itam_snapshot_server`(IP 다중값 분해·검증) |
| DPM | 없음 — `status: not_integrated` 고정 기록 | (향후 `dpm_inventory`) |

- **일괄 생성**: 스냅샷 갱신 뒤 전 도메인·인스턴스에 §3.4(node_address → vm_exact → itam 순) 적용.

| 경우 | 처리 |
|---|---|
| 키에 행 없음 | auto 신규 |
| auto·미확인 · 결과 동일 / 변경 | `updated_at`만 / 갱신 + `previous_targets` + 「변경됨」 |
| manual·verified | 불변 · 다르면 `scan_suggests` |
| disabled | 불변 |
| 대상 서버가 폴스타에서 사라짐 | `target_gone=true` · 진단에 쓰지 않음(사유 노출) |
| APM 인스턴스·도메인 사라짐 | 행 유지 · 180일 미관측 auto 삭제 |

행별 개별 트랜잭션 · 끝나면 Redis 사본·색인 전체 재구성. 모든 관측 DB SQL은 코드가 조립한 고정 SELECT(LLM 0) · readonly MCP · `sql_guard`.

### 3.6 `hostAddress` 호출 경로 4종 (인터뷰 I-4 「모두」)

| 경로 | 현행 | 변경 |
|---|---|---|
| 알람 | 폴러가 알람 도메인만 조회(플래그·계정 있을 때) | 유지 · noise_gate가 결과를 §3.1-3에 사용 · **호출 못 한 사유 노출**: `host_address_status`가 `disabled`·`login_failed`·`login_blocked`·`error`이거나 키 자체가 없으면(게이트웨이 플래그 off) 힌트에 `node_address_status` 가산 → 진단 화면·조사 근거 문장 |
| 사전 스캔 | 없음 | 게이트웨이 신규 MCP 도구 `apm_node_addresses(source_ids?, domain_ids?)` — 지정 도메인 전부를 순차 웹 조회(기존 `NodeAddressService.domain_addresses(fetch=True)` · 차단기·속도 제한 그대로) → 인스턴스별 `{instance_id, instance_name, ip, host_address, pod, status}` · 큰 결과는 결과 파일 |
| 장애 조사 | sre_agent는 힌트만 사용 | 힌트에 노드·대상이 실리므로 추가 호출 없음 · 사유 문장(`node_address_status`) 표시 |
| 채팅(text2sql) | 전용 보기 없음 | `solutions[apm].views`에 `apm.node_address`(라벨 「WAS 인스턴스가 도는 노드(호스트) 주소 — K8s 파드의 노드 IP」 · `tool: apm_node_addresses` · `window: none` · 대상 인스턴스/도메인) 추가 · 분해 LLM이 보기를 고르면 `apm_query` 고정 표가 **반드시** 도구 호출(단어 매칭 0 · D-300 ⑧ 준수) · 상태가 `ok` 아니면 사유를 답에 싣는다 |

- 게이트웨이 도구는 `JENNIFER_NODE_ADDRESS_ENABLED=true`일 때만 등록한다(off = 도구 목록·HolmesGPT 도구 집합 비트 동일). 미등록일 때 채팅 보기는 「게이트웨이 노드 주소 조회 비활성(JENNIFER_NODE_ADDRESS_ENABLED)」 사유로 끝나고, 스캔은 실행 기록에 `node_address: disabled`.
- 자격증명은 게이트웨이에만(D-274) · 도구 응답·로그에 비밀번호·쿠키 없음(기존 웹 세션 규칙).

### 3.7 계약 (`meta.hints.polestar_link` 가산 키 · `contract_version "1"` 불변)

- linked(1대): 현행 `{status:"linked", db_id, server_name, hostname, basis, at}` + `basis: "apm_map"|"node_address"` · `map_origin`·`map_scope`·`map_id`·`map_basis` · `resource_id` · `evidence_text`.
- linked(여러 대 · 인터뷰 I-2): 위 + `targets: [{db_id, server_name, hostname, resource_id}]`(≤10) · 최상위 `db_id`·`server_name`은 첫 대상(하위 호환).
- unresolved: `reason: "apm_map_pending"|"apm_map_failed"|"apm_map_no_inventory"|"apm_map_ambiguous"` · `no_config_fallback: true` · `node_address_status`(있으면).
- disabled 매핑: `{status:"absent", basis:"apm_map"}`.
- **sre_agent**: `POLESTAR_LINK_BASIS`에 `apm_map` 문장(「APM↔SMS 연계 — 관리자 등록」 / 「APM↔SMS 연계 — 자동(노드 주소 · VM 일치 · ITAM 업무 추정)」) · `resolve_polestar_source`는 `no_config_fallback`이면 설정 매핑 대체 금지(D-355 ④ 일반화) · `targets`가 있으면 폴스타 축(사전 수집·플레이북 지표 단계)을 대상마다 실행하고 결론이 이상 노드를 짚게 한다(대상 수 × 단계 — 단계 상한·타임아웃은 기존 `EVIDENCE_PREFETCH_TIMEOUT_SECONDS` 안) · `resource_id`·`server_name`이 오면 식별자 재해소(D-360) 생략.
- `targets[].kind != "sms"` 원소는 소비자가 무시(DPM 대비).

### 3.8 수동 재분석 경로

`_manual_event`(`src/api/routes/alarm.py:2640`)가 판정 기록에서 이벤트를 다시 만들며 `apm` 블록을 잃는다 → 판정 기록에 `apm_ref {source_id, domain_id, domain_name, instance_name, ip, host_address, host_address_status}` 가산(플래그 on일 때만 · `noise_gate/infrastructure/decision_store.py`) → 복원. 옛 기록은 `db_id`(`jennifer_<src>`)·`resource_name`으로 게이트웨이 `apm_instance_map(query=…)` 역조회. 수동 경로는 API 프로세스라 되적재를 직접 쓴다.

### 3.9 DPM(향후) — 자리만

코드 0 · 스캔 실행 기록 `dpm: not_integrated` · `targets[].kind` 예약(`{kind:"dpm", dpm_source, db_instance, host}`) · 소비자 무시 규칙 · 실 연동(제품·API·자격증명·APM 도메인↔DB 인스턴스 근거)은 별 계획에서 §3.5 스캐너 표에 한 줄, §3.1 표에 DPM 대상 소비를 붙인다.

### 3.10 관리 API·화면 (관리자만 · 인터뷰 I-5)

`src/api/routes/admin_apm_host_map.py` · prefix `/api/v1/admin/apm-host-map`

| 메서드 | 경로 | 동작 |
|---|---|---|
| GET | `/` | 목록 — source·state·origin·basis·검색어 · 페이지 · 「검토 필요」(ambiguous·failed·변경됨·target_gone·scan_suggests) |
| POST | `/` | 수동 추가 — 대상마다 `locate_selected_server`로 폴스타 실재 확인 |
| PUT | `/{id}` | 수정 `{targets, state(active·disabled), note, version}` — 자동 행 수정 시 `origin=manual` · 409 |
| POST | `/{id}/verify` | 자동 결과 확인 |
| POST | `/{id}/unverify` | 확인 취소 — 확인된 자동 행만 · `verified=false`만(§6.4) |
| POST | `/{id}/resolve` · `/preview` | 다시 찾기 미리보기(`apply=true`일 때만 저장 · manual·verified는 미리보기만) · 관리자별 분당 10회 |
| DELETE | `/{id}` | `{version}` |
| POST | `/scan` · GET `/scan/runs[/{run_id}]` | 수동 스캔 · 이력·진행 |
| GET | `/inventory/{apm|sms|itam}` | 스냅샷 검색(「매핑 없음만」 필터) — 수동 추가 선택 목록 |

- 인가 `Depends(require_admin_user)` + role 명시 재검증 · 감사 `log_admin_event` before/after(스캔 시작 포함) · 화면 `textContent`만.
- 관리자 대시보드 새 탭 「APM·SMS 연계」(`src/static/admin/dashboard.html` + `src/static/js/admin-apm-host-map.js`) — 하위 영역 ①매핑 목록·편집 ②스캔(「지금 스캔」·마지막 스캔 소요·스캐너별 결과 · 노드 주소 소스별 상태 · DPM 「미연동」) ③인벤토리.
- 장애 진단 화면(`diagnosis-view.js`): 근거 문장(basis·origin·「자동 · 추정」) · 여러 대상 목록 · `node_address_status` 사유 · 관리자에게만 「연계 정보 수정」 링크. 일반 사용자는 보기만.

### 3.11 설정 (`src/config/alarm.py` noise_gate · env `NOISE_` · 전부 기본 off/현행)

| 키 | 기본 | 설명 |
|---|---|---|
| `NOISE_APM_HOST_MAP_ENABLED` | `false` | 매핑 조회(§3.1)·관리 탭·사본 |
| `NOISE_APM_HOST_MAP_AUTO_ENABLED` | `false` | 되적재·ITAM 생성 요청·스캔·일괄 생성 — off면 관리자 수동 매핑만 |
| `NOISE_APM_HOST_MAP_MAX_TARGETS` | `10` | 한 매핑·한 조사의 대상 상한 |
| `NOISE_APM_HOST_MAP_LIVE_BUDGET_SECONDS` | `5` | miss 시 직접 조회 예산 |
| `NOISE_APM_HOST_MAP_RETRY_HOURS` | `24` | `failed`·`ambiguous` 재시도 간격 |
| `NOISE_APM_HOST_MAP_SCAN_PAGE_ROWS` | `1000` | 스캔 페이지(MCP `max_rows` 10000 미만 검증) |

게이트웨이: 기존 `JENNIFER_NODE_ADDRESS_ENABLED` + 소스별 웹 계정(사용자 설정 · W0-1).

---

## 4. 정상 동작 가능성 검토

| # | 항목 | 판정 | 근거 | 위험·완화 |
|---|---|---|---|---|
| F1 | 매핑 hit 고속 경로 | **가능(높음)** | Redis 해시 조회 1회 · 기존 힌트 계약에 가산만 | 사본 유실 = miss(직접 조회) · 오연결 없음 |
| F2 | miss 직접 조회 + 되적재 | **가능** | `node_address_link`·`probe_hosts` 재사용 · 워커→Redis Stream→API 단일 라이터(D-049) | 예산 5초 · 실패 존 `unchecked` 분리 |
| F3 | `hostAddress` 알람 경로 | **조건부** | 코드 존재 · 폐쇄망 웹 계정·플래그 비어 있었음(§0) | W0-1 사용자 테스트 · 사유 노출로 침묵 없음 |
| F4 | `hostAddress` 스캔·채팅 | **가능(게이트웨이 변경)** | `domain_addresses(fetch=True)` 재사용 · 도구는 플래그 on일 때만 등록 | 전 도메인 순차 웹 조회 부하 — 수동 스캔·차단기·속도 제한 |
| F5 | 노드 IP ↔ 폴스타 정합 | **조건부** | `plans/202` §0 실측(노드 IP 10.136이 CM-GP에 등록) · 다른 존 미확인 | W0-2 · NIC IP 색인 포함 |
| F6 | VM 정확 일치 | **가능(높음)** | VM 인스턴스 IP = 서버 IP | 다중 NIC은 NIC 색인 · 2대 이상 `ambiguous` |
| F7 | ITAM 사슬 | **조건부(약함)** | 업무↔서버 코드 키 없음(`itam.yaml:170-176` 「추정」 · `plans/135` 417행) | 상한 50 · `ambiguous` 미사용 · 관리자 검토 · 일치율 미달이면 itam 규칙만 끔(yaml) |
| F8 | 여러 대 전부 조사 | **가능(sre_agent 변경)** | 사전 수집·플레이북이 단일 `db_id` 전제 → 대상 반복 필요 | 상한 10 · 타임아웃 기존 범위 · 대상별 개별 실패 |
| F9 | 사전 스캔 APM·SMS·ITAM | **가능 / 가능 / 조건부** | 도구·SQL 경로 실재 · ITAM 등록·권한 미확인 | 개별 try · 원자 교체 · W0-6·8 |
| F10 | 관리자 관리 | **가능** | CRUD·감사 선례 | 버전 409 |
| F11 | 수동 재분석 대칭 | **가능(교정)** | `apm_ref` 가산 · 옛 기록 역조회 | |
| F12 | 경계·게이트 | **가능** | 게이트웨이 변경은 자기 패키지 안(D-274 · `tests/test_boundary.py`) · sre_agent 힌트만 · ITAM·폴스타 식별자는 config·infrastructure(overfit_check 스캔 대상 domain에 리터럴 없음) · 채팅 보기는 닫힌 어휘(D-300 ⑧) | 독스트링 리터럴 금지 |
| F13 | DPM | **슬롯만** | D-341 ⑤ | 소비자 무시 테스트 |
| F14 | 매핑 신선도 | **조건부** | 파드 instance 행은 생애 동안 정확 · workload 노드 집합은 노드 증감에 늦음 | 스캔·되적재가 합집합 갱신 · `target_gone` · auto instance 30일 정리 · 관리자 수정 |

**종합**: 목적(진단 속도)의 핵심인 F1·F2·F6·F10은 기존 코드 위에서 확실히 동작한다. `hostAddress`(F3·F5)는 웹 계정 설정 뒤 폐쇄망 확인이 필요하고, 확인되면 파드도 정확한 1대로 hit가 된다. ITAM 사슬(F7)은 추정이라 「매핑이 비었을 때의 차선」으로만 둔다. 매핑이 틀리면 관리자가 고치고, 고친 매핑은 바로 고속 경로가 된다.

---

## 5. 보안

| 위험 | 대응 |
|---|---|
| 관측 DB 쓰기·과다 조회 | readonly MCP + `sql_guard` + `itam_ro` · 고정 SELECT · 키셋 페이지 · 스캔은 관리자 수동·단독 락 |
| SQL 주입 | 외부 입력(도메인명·설명)은 SQL에 들어가지 않음(메모리 대조) · 가변부는 config 식별자(검증·백틱)와 형식 검증한 페이지 키·IP(IPv4 검증)뿐 |
| 제니퍼 웹 자격증명 | 게이트웨이에만(D-274) · 소스별 전용 조회 계정 · 응답·로그 무노출(기존 규칙) · 도구는 플래그 on일 때만 |
| PII(ITAM 담당자) | 칼럼을 읽지도 적재하지도 않음 · 인벤토리 API 관리자만 |
| 관리 API 권한 | admin 의존성 + role 재검증 · 전 동작 감사 · 일반 사용자는 근거 보기만 |
| 오진 | 근거 「자동 · 추정」 상시 표기 · `target_gone` 미사용 · 관리자 수정 즉시 반영 |

---

## 6. Wave

| Wave | 범위 | 패키지 | 크기 |
|---|---|---|---|
| W0 | 폐쇄망 실측(§7) — 사용자가 웹 계정·플래그 설정 후 `host_address_status` 확인 · 읽기 전용 점검 스크립트 `noise_gate/scripts/probe_apm_host_map.py`(건수·일치율·소요만) | 내부망 | 실측 |
| W1 | 도메인 모델(키·상태·조회 순서·되적재 병합·대조 규칙 순수 함수 `noise_gate/domain/apm_host_map.py`) · 포트 · PG 저장소(매핑·스냅샷·실행 기록) · DDL·`_ensure_apm_host_map_tables` · Redis 사본·SMS 색인 · 설정 6키 | `noise_gate` · `src/api/server.py` · `src/config` | 중 |
| W2 | 게이트웨이 `apm_node_addresses` 도구(플래그 on 등록 · 결과 파일 · 테스트 `../.venv/bin/python -m pytest`) · text2sql 보기 `apm.node_address`(레지스트리 + `apm_query` 고정 표 · 사유 답) | `apm_gateway` · `config/db_registry.yaml` · `src/orchestration` | 중 |
| W3 | **진단 고속 경로** — `_attach_polestar_link` 순서(§3.1 · 기존 플래그 조합 비트 동일 테스트) · 되적재 스트림·API 소비자 · `apm_map_lookup` 로그 · `apm_ref`·`_manual_event` · 사유 `node_address_status` | `noise_gate` · `src/api` | 중 |
| W4 | sre_agent — basis 문장 · `no_config_fallback` · 다중 대상 폴스타 축 반복 · `resource_id` 전달 시 재해소 생략 · `kind` 무시 | `sre_agent`(자체 venv) | 중 |
| W5 | 사전 스캔(스캐너 4 + DPM 슬롯 · 락·기록·원자 교체) · 일괄 생성(§3.5 표) · ITAM 사슬 · ITAM 비동기 요청 | `noise_gate` · `config/knowledge/itam` | 대 |
| W6 | 관리 API·탭(매핑·스캔·인벤토리)·진단 화면 문구·링크 · API 테스트(인가·409·`scan_running`·감사·실재 확인) | `src/api` · `src/static` | 중 |
| W7 | 매뉴얼(관리자 새 탭 · 사용자 근거 문장·채팅 노드 주소 질의 · D-255) · `.env.example`(noise_gate·apm_gateway) · D-362 본문 · D-348 ②·D-355·D-300·D-341 부기 · INDEX | 문서 | 소 |
| W8 | **채팅 노드 주소 결정성**(2026-11-03 추가 · §6.2) — A 노드 주소 보기 인스턴스 대상 · B `apm_instance_map` 대상 지정 호출 웹 조회 상한 3→50 · C 파드 대상 보기에 노드 주소 결정적 부착 | `apm_gateway` · `config/db_registry.yaml` · `src/orchestration` · 매뉴얼 | 중 |
| W9 | **노드 주소 조회 지연 상한 · 서버명 경로 부착**(2026-11-03 추가 · §6.3) — A 대상 지정 호출 전체 시간 예산 · 소스 간 병렬(소스 안 직렬 유지) · 예산 초과 `[한계]`·partial·행 상태 `budget_exceeded` · B 서버명 직행 보기(`_host_calls`)에 파드 노드 주소 부착(게이트웨이 `instance_resolution.node_address` 표지) | `apm_gateway` · `src/orchestration` · 매뉴얼 | 중 |
| W10 | **관리자 「확인 취소」**(2026-11-04 후속 · §6.4) — `POST /{id}/unverify` · 확인된 자동 행에만 버튼 · 취소 뒤 자동 규칙 복귀 | `noise_gate` · `src/api` · `src/static` · 매뉴얼 | 소 |

W3·W4가 목적(속도)의 본체이므로 W5(스캔) 전에 둔다 — 스캔 전에도 되적재로 매핑이 쌓여 두 번째 알람부터 hit다. 단 SMS 색인(§3.1-3)은 스캔이 채우므로 W5 전에는 3'(직접 조회)로 동작한다.

회귀: D-303 — Wave 중간은 자기 테스트 + `regress.py --no-tests --files …` · `src/config/`·공개 시그니처 변경 Wave(W1·W3)는 도구 출력의 `[웨이브 회귀 필요]`로 판단 · 계획 끝 1회 `regress.py --base <계획 시작 SHA> --files <바꾼 파일>` · `apm_gateway`·`sre_agent`는 각자 방식.

### 6.1 수용 기준

- 플래그 off: 페이로드·판정 기록·화면·게이트웨이 도구 목록 비트 동일(기존 plan196·202 테스트 무수정 통과).
- **고속 경로**: 매핑 hit 알람 → 가짜 실행기 기준 웹·폴스타 프로브·ITAM 호출 **0회** · 힌트 `basis=apm_map` · 로그 `result=hit` · 판정 시간(가짜 지연 주입 기준) hit ≪ live.
- **되적재**: 매핑 없는 파드 알람(페이로드 `host_address` ok) → 직접 조회로 linked + `apmhostmap:req` learn → API가 instance·workload 행 upsert → 같은 파드 두 번째 알람 hit · 다른 파드명(같은 워크로드)은 자기 노드 주소로 linked(3순위)하고 workload 집합 합집합.
- 노드 주소 미설정(키 없음)·`disabled`·`login_blocked` → 힌트 `node_address_status` · 진단 화면·조사 근거에 사유 문장.
- 대상 3대 매핑 → 힌트 `targets` 3 · sre_agent 폴스타 축 3회 · 설정 매핑 미사용.
- 관리자 수정 → `origin=manual` · 자동이 덮지 않음 · `disabled` → `absent` · 409 · 비관리자 403 · 감사 before/after.
- 「지금 스캔」 → 실행 기록 `ok`/`partial` · 스냅샷 · 색인 · VM 인스턴스 instance auto · 실행 중 409 · ITAM 실패 시 이전 스냅샷 유지.
- 채팅 「xxx 파드가 도는 노드 주소」류 → 보기 `apm.node_address` 선택 시 도구 1회 호출 · 게이트웨이 플래그 off면 사유 답.
- 별도 워커 프로세스 모드에서 워커 PG 연결 0.

### 6.2 W8 — 채팅 노드 주소 결정성 (2026-11-03 추가)

사용자 지시(2026-11-03): *"남은 문제를 해결하라."* — 보고 증상: *"(노드 주소가) 나오긴 하는데 프롬프트에 따라 나오기도 하고 안나오기도 한다."*

원인 3가지(실측): ① `apm_instance_map`(보기 `apm.instances`)은 대상 도메인이 `MCP_FETCH_DOMAIN_LIMIT`(3)개 이하일 때만 웹을 조회하고 넘으면 캐시만 본다(`instance_tools._fill_host_address`) · ② W2 보기 `apm.node_address`는 `target: none`·`domain_id`만 받아 「order-api 파드가 도는 노드 주소」처럼 인스턴스 이름으로 물어도 그 인스턴스로 좁히지 못한다 · ③ 다른 보기는 `host_address` 칸을 LLM 산문에 맡긴다. 상한 3은 D-355·`plans/202`의 결정 문장이 아니라 W2 구현 상수다(grep 확인 — D-355 부기로 기록).

| 축 | 변경 |
|---|---|
| A. 인스턴스 대상 | `apm.node_address`가 대상(분해 `targets` · 파서 hostname · 직전 대상)을 받는다. 기존 대상 해석(`_resolve_target_texts` — 게이트웨이 `query`/`business` 검색 · plans/130 M-1·M-2·M-4)을 재사용해 해석 인스턴스의 (소스, 도메인)마다 `apm_node_addresses(source_ids, domain_ids, instance_ids)`를 1회 부르고, 답 줄은 **그 인스턴스들의** 노드 주소·상태·사유다. 대상 0건이면 「찾지 못함」+ 후보(D-290 ⑥ — 전 도메인 조회로 대신하지 않음). 대상이 없으면 W2 현행(도메인 조건 또는 전 도메인 1회). 게이트웨이 `instance_ids`는 가산 인자(생략 = 현행 응답 비트 동일 · 지정하면 그 인스턴스가 있는 도메인만 조회). |
| B. 상한 정리 | `apm_instance_map`의 대상 지정 호출(hostname·domain_id·query·business 중 하나라도)은 해석된 (소스, 도메인) 전부를 웹 조회 — 상한 `MAX_DOMAINS_PER_CALL`(50)과 같게, 초과분은 캐시만 보고 `[한계]`·partial. **무인자 전체 목록 호출만** 캐시 전용. |
| C. 결정적 부착 | 처리기가 해석 인스턴스별로 부르는 보기(응답시간·런타임 등)에서 해석 행에 게이트웨이 노드 주소 표지(`host_address` 칸 — 게이트웨이 플래그 on일 때만 있다)가 있으면, 그 턴(처리기 1회)에 (소스, 도메인)마다 `apm_node_addresses`를 1회 불러 **파드(`pod: true`)인 인스턴스만** 노드 주소 답 줄을 붙인다. 파드 판정은 게이트웨이 `pod` 표지만(원문 단어 매칭 0 · D-300 ⑧). VM(`pod: false`)은 부착 0. 도구 미등록(`Unknown tool`)은 `node_address_disabled` 사유 1줄. 표지가 없으면(게이트웨이 플래그 off) 부착 시도 0 · 답 비트 동일. 본체 새 플래그 없음. |

수용 기준(테스트로 고정):
- 인스턴스 이름 대상 「X 파드가 도는 노드 주소」 → 해석 인스턴스만의 노드 주소 답 줄 · 이름 0건이면 「찾지 못함」+ 후보 · 노드 주소 도구 호출 0.
- 도메인 4개 이상에 걸친 대상 지정 `apm_instance_map` → 웹 조회 수행(가짜 조회기 호출 수 단언) · 무인자 호출은 캐시 전용 유지 · 51개 이상은 `[한계]`·partial.
- 파드 대상 응답시간 보기 → 노드 주소 줄 포함 · 같은 도메인 두 보기에서 노드 주소 도구 1회 · VM 대상은 부착 0.
- 게이트웨이 플래그 off → 위 세 경우 모두 현행 답 비트 동일.
- 분해 LLM 선택: 로컬 MLX 대표 3~5문항 1회(두 평면 `mlx` 확인 뒤) — 로직 확인용 기록만.

회귀: D-303 — W8 끝 `regress.py --base 171bf832 --files <W8이 바꾼 파일>` 1회 · `apm_gateway`는 `../.venv/bin/python -m pytest`.

결과(2026-11-03):
- **A** 게이트웨이 `apm_node_addresses` 가산 인자 `instance_ids`(≤1000 · 그 id가 있는 도메인만 조회 · 미지 id `[한계]` · 생략 = 비트 동일) · 본체 `_node_targets_step`(이름·서버·직전 대상 해석 → (소스, 도메인)마다 1회 · 도메인 조건 AND · 0건 = 「찾지 못함」+ 후보 · 호출 0) · 인스턴스별 답 줄 5줄 + 「외 n개」(전체 수 = `total_row_count`·소스 인스턴스 합) · 레지스트리 `target: none` 유지(보기 id 특례 — `optional`로 바꾸면 스키마가 `targets`·`instance_name`·`source_ids` 수용을 요구) · 통로 표는 `src/orchestration/apm/node_address.py` 독스트링.
- **B** `MCP_FETCH_DOMAIN_LIMIT` 삭제 · 대상 지정 `apm_instance_map`은 앞 50 (소스, 도메인) 웹 조회 · 초과 `[한계]`+partial · 무인자는 캐시 전용.
- **C** `_pod_node_step` — 해석 행에 `host_address` 칸(게이트웨이 플래그 on 표지)이 있을 때만 (소스, 도메인)마다 1회 · `pod: true`만 줄 · VM 0 · `Unknown tool` = `node_address_disabled` 1줄 · 노드 주소 보기를 같이 골랐으면 부착 없음.
- **교정(verifier)**: q4 — 분해가 대상을 `view_args[apm.node_address].targets`에 넣는 모양(실 MLX 1/4)을 task `targets`로 옮기는 구조 정규화(`targets.lift_node_targets` · 단어 매칭 0) · 「외 n개」 전체 수 · `instance_ids` 상한·라벨 축약 · 서버 정합 `[한계]` 전달 · 매뉴얼 0건 예외. 팀 리드: `_GUIDANCE_CODES` 오삽입(`domain_ids`) 제거 · 예시 괄호 꼬리 제거(벤치 예시 파서 · docs/18 2건).
- **의도 편차(플래그 off)**: ① 대상이 전부 0건이면 비활성 사유 대신 「찾지 못함」(표지를 알 수 없음 · D-290 ⑥ 우선) ② 대상이 있으면 해석용 `apm_instance_map` 호출 1회 추가(답 바이트 동일) ③ 혼합 보기(`apm.node_address`+대상 보기) 후속 턴 직전 대상 승계가 새로 성립(개선).
- **테스트**: 본체 W8 36 + verifier 13(+교정 신규) — `test_plan210_w8_{node_address,verify}.py` · W2 보기 9 · 게이트웨이 W8 20 · 모듈 회귀 `regress.py --base 171bf832` rc=0 · 원래 실패 4건만(plan147_w4 3 · plan134_w34_verify_real 1) · `범위: 모듈 단위 — 전체 미실행` · 게이트웨이 변경 모듈 참조 테스트 1,183 passed · `apm_bench --dry-run`·`--fewshot-check` 통과.
- **MLX(Qwen3.5-9B · 두 평면 mlx · 1회 · 로직 확인용)**: 노드 주소 4문항 모두 `apm.node_address` 선택(이름 2 `targets` instance · 도메인 1 `domain_id` · 업무 1은 `view_args.targets`로 → 교정으로 수용) · 대조 「billing-api 지난 1시간 응답시간」 → `apm.service_trend`.
- **잔여**: ① B 지연 — 대상 지정 해석이 도메인을 순차 웹 조회(도메인당 5초 상한 · 콜드 캐시 최악 ~250초 · 실측 1건 ≤0.7초) → W0 폐쇄망에서 콜드 캐시 지연 실측, 필요 시 전체 시간 예산 또는 해석은 캐시 전용·웹은 `apm_node_addresses`로 모으기 ② hostname 직행 보기(「web01 응답시간」 — `_host_calls`)는 C 부착 없음(데이터 도구 행에 표지 없음) ③ q4 정규화의 실 MLX 재확인 없음.


### 6.3 W9 — 노드 주소 조회 지연 상한 · 서버명 경로 부착 (2026-11-03 추가)

사용자 지시(2026-11-03): *"남은 문제 1, 2번도 해결하라."* — §6.2 잔여 ①(대상 지정 해석의 순차 웹 조회 지연 · 콜드 캐시 최악 ~250초)과 ②(서버명 직행 보기 부착 없음).

웹 세션 동시성 실측(`adapters/jennifer/web_session.py` · `application/node_address.py`): 소스마다 `WebSession._lock`과 `_SourceState.lock` 두 겹이 조회 1건(로그인 포함)을 잠근다 — 같은 sid 동시 요청은 지금 구조에서 일어나지 않는다. 풀면 ⓐ `_login`이 쿠키 병을 비우고 다시 받으므로 진행 중인 다른 요청의 세션이 깨지고 ⓑ `_logged_in` 확인·로그인이 경합해 같은 계정 중복 로그인(D-355 ⑥ 「중복 로그인 충돌」)이 날 수 있으며 ⓒ `plans/202` §3 「뷰서버 부하 — 소스당 동시 1」(D-355 부기 4 「뷰서버 부하 기준(①) 불변」)을 바꾼다. 그래서 **소스 안 직렬은 유지하고 소스 간만 병렬**로 한다(소스는 뷰서버가 서로 다르다 — 서버당 부하 불변).

| 축 | 변경 |
|---|---|
| A. 시간 예산 | 대상 지정 호출(`apm_instance_map`의 hostname·domain_id·query·business · `apm_node_addresses`의 `domain_ids`·`instance_ids`)은 호출당 전체 예산 `JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS`(기본 5초) 안에서만 웹 조회한다. 소스마다 작업 1개(그 안은 직렬) · 소스 간 병렬. 예산이 끝나면 그 시점에 돌려주고(진행 중 웹 조회 1건은 뒤에서 끝까지 돌려 캐시·실패 기록을 종전대로 남긴다 · 잠금 대기·미시작만 취소 — verifier Major 교정) 못 본 도메인은 캐시(TTL 안)를 쓰고 캐시도 없으면 행 상태 `budget_exceeded`(계약 가산 어휘 1개) · `[한계]` 1줄 · partial. 무인자 `apm_node_addresses`(사전 스캔 · 작업 승격)는 예산 없이 소스 간 병렬만. 상한 50(`MAX_DOMAINS_PER_CALL`)은 그대로. 같은 (소스, 도메인)은 TTL 캐시·실패 간격으로 한 턴 1회 — 해석 `apm_instance_map` 뒤 `apm_node_addresses`는 캐시 hit(테스트 고정). |
| B. 서버명 부착 | 게이트웨이 플래그 on이면 `instance_resolution`이 있는 데이터 도구 봉투(배치 항목 포함)에 `instance_resolution.node_address: true`를 가산한다(off = 키 없음 · 비트 동일). 본체 `_pod_node_step`은 서버명 직행 호출(`_host_calls` — `c.instance` 없음)의 봉투가 이 표지를 달았을 때 `instance_refs`(source_id·domain_id·instance_id)를 W8 C 해석 행과 합쳐 (소스, 도메인)마다 `apm_node_addresses` 1회 · `pod: true`만 줄 · VM 0. 표지가 없으면 시도 0(추가 호출 0 · 답 바이트 동일). 판정은 게이트웨이 표지·`pod`만(D-300 ⑧). `apm_instance_map(hostname)` 해석을 따로 부르지 않는다(플래그 off에서도 호출이 생기므로 — 사용자 문안 「hostname 해석」을 봉투의 정합 결과로 대신). |

수용 기준(테스트로 고정):
- 가짜 조회기(도메인당 지연 주입) 도메인 50개 → 전체 소요 ≤ 예산 + 여유 · 예산 초과분은 `[한계]`·partial·행 `budget_exceeded` · 소스 안 동시 실행 ≤ 1 · 소스 간 병렬 성립 · 로그인 소스당 1회(실 `WebSession` + MockTransport) · 캐시가 따뜻하면 웹 조회 0회 · 해석 뒤 노드 주소 도구는 같은 도메인 웹 재조회 0.
- 서버명이 파드인 응답시간 보기 → 노드 주소 줄 · VM → 0 · 같은 턴 다른 보기와 같은 도메인이면 노드 주소 도구 1회 · 플래그 off → 스냅샷 `tests/snapshots/plan210_w9/before_answers.json`과 바이트 동일 · 추가 호출 0.

회귀: D-303 — W9 끝 `regress.py --base 171bf832 --files <W9가 바꾼 파일>` 1회 · `apm_gateway`는 `../.venv/bin/python -m pytest`.

결과(2026-11-03):
- **A** 게이트웨이 `NodeAddressService.domain_snapshots(pairs, budget_seconds=)` — 소스마다 작업 1개(직렬) · 소스 간 `asyncio.wait(timeout=예산)` · 예산 끝 = 그 시점 반환 · 웹 조회 중 1건은 뒤에서 끝까지(`_background` 강한 참조 · `aclose` 정리) · 잠금 대기·미시작만 취소 · 못 본 쌍 `cached_snapshot`(잠금 없음) 또는 `budget_exceeded`. `apm_node_addresses` 대상 지정만 예산 · 무인자는 병렬만 · `apm_instance_map` 대상 지정은 50 안 쌍 예산 · 50 초과 쌍은 잠금 없는 캐시 읽기. 설정 `JENNIFER_NODE_ADDRESS_CALL_BUDGET_SECONDS`(5 · `config_admin_adapter` 행 · `.env.example`) · `_positive` `nan`·`inf` 거부·키 이름 오류(같은 헬퍼 키 9개 공통). 동시성 실측 결론(소스 안 직렬 · 소스 간 병렬)과 다른 사실 없음.
- **B** 게이트웨이 `ApmCore.ok` 표지 `instance_resolution.node_address: true`(플래그 on · 배치 항목 포함) · 본체 `_pod_node_step`을 `_unbatched` 뒤로 · 서버명 직행 호출(`required_input` 보기 · 첫 홉 아님)의 `marked_refs`를 W8 C 행과 합쳐 (소스, 도메인)마다 1회. 플래그 on 추가 호출: 서버명 파드 1개 +1 · 서버명 2개 배치 +1 · 서버명+이름 대상 같은 도메인 +1 공유 · 서버명 VM +1(줄 0) · 정합 실패·첫 홉·`apm.instances`·노드 주소 보기 동반 0. 플래그 off 0.
- **교정(verifier Critical 0 · Major 1 · Minor 5)**: Major — 예산 끝에서 진행 중 웹 요청까지 취소해 걸린 뷰서버에 소스 백오프가 안 생김(요청 상한 5초 = 예산) → 진행 중 1건 뒤에서 완료로 교정 · Minor-1·2 설정 검증 교정 · Minor-3 소스 전부 예산 초과 시 `[한계]` 2줄(예산 줄 + W2 소스 상태 줄)은 유지 · Minor-4 인벤토리 조회 시간은 예산 밖(W9 전부터 · 웹 조회만 묶음) · Minor-5 스캐너 독스트링 교정(팀 리드).
- **테스트**: 게이트웨이 `test_plan210_w9_budget.py` 19 + verifier `test_plan210_w9_verify.py` 14 · 본체 `test_plan210_w9_host_node.py` 17 + verifier 6 · 스냅샷 `tests/snapshots/plan210_w9/before_answers.json`(변경 전 트리 캡처 5시나리오) · 회귀 `regress.py --base 171bf832 --files <W9 18개>` rc=0 · 원래 실패 4건만 · 교정 파일 재회귀 rc=0(apm_gateway 2,034 통과) · `범위: 모듈 단위 — 전체 미실행`.
- **매뉴얼**: U-85 「web01 응답시간」 행 · 시간 한계·`budget_exceeded` · A-88 키 행·상태 행 · 빌드 · `tests/test_manual` 통과.
- **잔여**: 부착 경로의 노드 주소 봉투 `[한계]`는 `meta.limits`로 오르지 않음(W8 C 현행 — 행 상태·사유 줄로만) · 차가운 인벤토리+느린 Open API면 본체 10초 상한 초과 가능(W9 전부터) · 첫 홉 서버는 부착 안 함(조건 1줄로 바꿀 수 있음) · W0 폐쇄망 실지연 실측.

### 6.4 W10 — 관리자 「확인 취소」 (2026-11-04 추가)

사용자 지시(2026-11-04): *"확인 취소 기능 추가하라."* — 「확인」(verified)은 되돌릴 방법이 수정(→ manual)·삭제뿐이었다.

- **API** `POST /{id}/unverify` `{version}` — `/verify`의 거울(인가·version 409·감사 `apm_host_map_unverify` before/after·사본 `put`). 대상 `origin=auto AND verified=true`만 · 그 밖 400 `unverify_verified_auto_only`(거부도 감사 — D-362 ⑯). 저장소 `unverify`는 `verified = FALSE`·`updated_by`·`updated_at`·`version + 1`만 바꾸는 바인딩 고정 UPDATE — 대상·상태·basis·note·evidence(`scan_suggests`·`previous_targets`) 유지. 포트 `ApmHostMapRepository.unverify` 추가.
- **효과** — 취소한 행은 §3.4 쓰기 규칙의 자동·미확인 행: 스캔·되적재가 `update`/`same`으로 덮고 `cleanup`(instance 30일 미사용 · 180일 미관측) 대상이 된다. 근거 문장 「자동 · 관리자 확인(…)」 → 「자동 · 추정(…)」.
- **화면** — 확인된 자동 행에만 「확인 취소」(확인 창 → 호출 → 목록 갱신 · 실패 = 오류 코드 문구 · 409는 기존 「다른 변경」 안내) · 스크립트 캐시 키 `?v=3`.
- **테스트** — `tests/test_api/test_plan210_unverify.py`(API 성공·400·409·404·403/401·503 · 취소 뒤 `plan_auto_write`·`purge_reason` · UI 버튼 노출·확인 창·실패 문구) · `noise_gate/tests/test_plan210_w1_repository.py`(SQL·version) · W6 라우트 집합 단언 갱신.
- **매뉴얼** — A-90 「확인 취소」 · `features.yaml` 앵커 2 · 캡처는 기존 불가 사유 유지.
- **잔여** — 취소 뒤에도 `scan_suggests`가 남아 「스캔 제안 있음」이 다음 자동 `update`까지 보인다(evidence 유지 지시 그대로).

---

## 7. W0 폐쇄망 실측

| # | 확인 | 판정 기준 |
|---|---|---|
| 1 | (사용자) `JENNIFER_NODE_ADDRESS_ENABLED=true` + 소스별 웹 계정 설정 → 게이트웨이 기동 로그 `node_address` 1줄 · 파드 알람 `apm.host_address_status` · 연속 로그인 실패 시 30분 정지 | 소스별 `ok` 여부 |
| 2 | 노드 IP(10.136 대역)가 폴스타 어느 존에 서버/NIC IP로 등록돼 있는가 | 정확 일치율 |
| 3 | VM 인스턴스 `ip`·`host_name` ↔ 폴스타 서버 정합률 | §3.4 vm_exact 신뢰도 |
| 4 | 도메인명·설명 형식 표본 · ITAM 업무 코드/명 대응 · 업무 → 서버 행 수 분포 | itam 사슬 사용 여부(관리자 정답 대조 ≥ 70%) |
| 5 | ITAM 호스트명·IP ↔ 폴스타 정합(G-6) | |
| 6 | `ACTIVE_DB_IDS`·MCP 소스에 `itam` 등록 · 권한 | |
| 7 | 운영 PG(auth DB) · Redis AOF | 저장소 전제 |
| 8 | 스캔 규모 — 소스별 도메인·인스턴스 수 · 존별 서버·NIC 행 수 · ITAM 서버 행 수 · 전 도메인 노드 주소 조회 소요 | 화면 예상 소요 안내 · 페이지 조정 |
| 9 | 현행 알람 1건당 폴스타 연결 판정 소요(로그) | 고속 경로 효과 기준선 |

---

## 8. 결정

### 8.1 인터뷰 확정 (2026-11-03)

| # | 질문 | 사용자 답 | 반영 |
|---|---|---|---|
| I-1 | 더 나쁜 것: (A) 폴스타 축 없이 끝남 vs (B) 틀린 서버 근거 | **A** | 자동 매핑을 확인 없이 사용(D-348 ② 예외 — 근거 「자동 · 추정」 표기) · 미확정 최소화 |
| I-2 | 대상이 여러 노드일 때 | **전부 조사(≤10)** | §3.7 `targets` · sre_agent 반복 · 파드 IP 블록 대조 삭제 |
| I-3 | `hostAddress`가 안 되는 이유 | 코드로 호출 못 함 → 웹 계정·플래그 **비어 있음** · 사용자가 설정 후 테스트 | §0 · W0-1 · 사유 노출 |
| I-4 | `hostAddress`를 부를 경로 | **알람·스캔·조사·채팅 모두** | §3.6 · 게이트웨이 도구·채팅 보기 신설 |
| I-5 | 관리 주체 | **관리자만** | §3.10 · 일반 사용자 보기만 |
| I-6 | 스캔 실행 | **관리자 페이지 수동만**(개정 2) | §3.5 |
| I-7 | 연계 DB의 목적 | **진단 속도** — 있으면 DB, 없으면 직접 조회(개정 3) | §1.1 · §3.1 |

### 8.2 잔여 — 확정 (2026-11-03 · 권고안을 「이의 없으면 확정」으로 제시 → 사용자 구현 지시로 확정)

| # | 질문 | 확정 |
|---|---|---|
| Q3 | 저장소 | PG 정본 + Redis 사본·색인(「내부 DB」 · D-049 정합) |
| Q4 | 키 단위 | instance → workload → domain 3단 |
| Q6 | D-348 호스트 연결과의 관계 | v1 병존(§3.1 6순위) · 진단 화면 선택 흐름은 D-348 그대로 · 통합은 후속 |
| Q7 | ITAM 일반어·칼럼 | W0-4 뒤 `service_host_lookup.yaml` 확정 |
| Q9 | DPM | 슬롯·계약만 · 실 스캐너는 별 계획 |

---

## 9. 비범위

- DPM 실 스캐너·DB 인스턴스 연계(§3.9 슬롯만).
- 주기·예약 스캔(개정 2).
- 일반 사용자 제안·승인 흐름(I-5) — D-348 진단 화면 선택은 기존 그대로.
- 노이즈 게이트 상관 키(`Alarm.host_key`·`episode_key`)에 매핑 반영(D-349 과묶음 위험).
- D-355 코드 폐기 — 오히려 직접 조회 경로(§3.1-3)로 쓴다.
- K8s API·kube-state-metrics·파드 IP 블록 대조(I-2로 불필요).
- 매핑 CSV 가져오기/내보내기 · CDC 실시간 동기화.
