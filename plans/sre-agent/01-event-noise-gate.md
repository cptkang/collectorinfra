# 01. 이벤트 관리 — 폴스타 알람 수신·결정적 노이즈 게이트 (Event Noise Gate)

> 작성일: 2026-07-24 · **이관일: 2026-07-24** (SREAgent → collectorinfra `plans/sre-agent/`, 통합 결정: collectorinfra D-118 / SREAgent D-021)
> **원본**: collectorinfra `plans/60-noise-cancellation-benchmark-refinement.md` (및 그 기반인 Plan 52/D-048)를 SREAgent로 이식. 원본은 기존 게이트의 "증분 고도화" 계획이었으나, SREAgent에는 게이트 자체가 없으므로 **코어 게이트(원본 Plan 52 상당) + 고도화 항목(E1~E7)을 단계화한 신규 구축 계획**으로 재구성했다.
> **관련 계획**: `plans/02-incident-investigation-holmesgpt.md`(PAGE 사건 자동 조사 — 본 계획이 트리거 계약 제공), `plans/03-mock-event-generator.md`(테스트 주입기), `plans/04-polestar-mcp-integration.md`(폴스타 신호 수집이 소비하는 MCP 서버), `plans/05-collectorinfra-interop.md`(연동 모드 — 본 계획의 게이트는 **독립 모드 전용**, collectorinfra 연동 배치에서는 그쪽 게이트가 트리거를 대신한다)
> **관련 결정**: D-001(HolmesGPT SDK), D-004(VM 진단 대상·읽기 전용 프로파일), D-005(계획 3종 이식 방향), D-013(폴스타 연동 MCP 일원화)
> **신규 결정(본 계획 예약이었음)**: D-006~D-008 — 대체됨에 따라 등재하지 않는다(결번 처리).
> **상태**: **대체됨(superseded, 2026-07-24)** — 통합으로 SREAgent 독립 모드가 소멸하여, 게이트 신규 구축은 **collectorinfra 기존 게이트(Plan 52/60 — E1~E6 구현 완료)가 대신한다**. 본 문서는 구현하지 않으며 참조용으로 유지한다.
> **이관 후에도 유효한 부분**: ① **§8 트리거 계약** — collectorinfra 게이트의 자동 조사 트리거 훅(그쪽 Plan 60 §14)이 Plan 05 §4 공용 페이로드(`contract_version`)로 `sre_agent/` 조사 서비스를 호출하는 계약의 원형. ② **§4 이벤트 스키마·식별자 이원화, §6.1~6.3 신호·방언·금지 조인 명세** — Plan 04(`mcp_server` 고수준 도구)의 SQL 명세로 흡수. ③ §5·§7은 collectorinfra 게이트와의 대조 참조용.
> **번호 체계 주의**: 본 문서의 D-번호는 SREAgent(이관 전) 결정 체계의 인용 — collectorinfra D-번호와 무관(폴더 README 참조).

---

## 1. 개요 및 목적

폴스타(POLESTAR) 모니터링이 발신하는 알람 이벤트를 수신하여, **결정적(deterministic) 4-티어 노이즈 게이트**로 통보 여부를 판정한다:

- **PAGE** — 즉시 운영자 호출 + Plan 02 자동 조사 트리거
- **TICKET** — 티켓 발행(비긴급 조치 필요)
- **DASHBOARD** — 대시보드 표시만
- **SUPPRESS** — 통보 억제(단, 감사 기록은 항상 남김 — 억제 ≠ 삭제)

HolmesGPT는 **게이트 판정에 개입하지 않는다**. 게이트는 순수 Python 결정 로직이고, HolmesGPT는 게이트가 PAGE로 판정한 사건의 **사후 조사·브리핑**(Plan 02)에만 쓰인다. 이 경계는 collectorinfra D-035("결정적 규칙=판단 / LLM=보조")의 계승이며, SOC 한계 연구(LLM은 triage 보조는 가능하나 프라이오리티 최종 판정은 신뢰 불가)가 근거다.

## 2. 설계 원칙 (collectorinfra Plan 52/60에서 계승 — 불변)

1. **결정적 규칙=판단 / LLM=보조**: 티어 확정·억제 확정·심각도 플로어는 결정적 코드 전담. LLM은 상향 후보·분류 힌트·브리핑 서술만(전부 옵트인).
2. **재현율 우선·비용 비대칭**: 불확실·신호 수집 실패·미식별 시 보수적 PAGE. **심각도 3은 모든 억제 단계를 단락(short-circuit)하고 항상 PAGE** — 이후 어떤 고도화 항목도 이 앞에 끼어들 수 없다.
3. **억제 ≠ 삭제**: 모든 판정(억제 포함)을 감사 저장소(JSONL)에 기록.
4. **읽기 전용·옵트인·회귀 없음**: 폴스타 조회는 SELECT/GET만. 고도화 기능은 전부 기본 off 플래그 — 비활성 시 코어 게이트 경로 무변경.
5. **로컬 계산 우선**: 상관·이상탐지·그래프 순회는 표준 라이브러리(math/statistics) 우선, 외부 SaaS 비의존.
6. **메타모니터링**: 억제율 이상·이벤트 무수신을 감사 데이터로 관측 가능하게 유지.

## 3. 아키텍처 — SREAgent 계층 배치

`.claude/rules/architecture.md`의 계층 규칙에 따라 배치한다. **신규 모듈은 `scripts/arch_check.py`의 `MODULE_LAYER_MAP`(파일 경로 `src.sre_agent.*` + 임포트 경로 `sre_agent.*` 양쪽)에 반드시 등록한다(D-003).**

```
src/sre_agent/
├── domain/
│   ├── alarm.py                 # AlarmEvent·NotificationDecision 모델 (외부 의존 0)
│   ├── notification_policy.py   # decide_notification(순수함수)·compute_fingerprint·매트릭스
│   ├── correlation.py           # [Phase 2] Jaccard 토큰 유사도·온라인 그리디 군집
│   ├── anomaly.py               # [Phase 3] Holt-Winters 동적 baseline (stdlib only)
│   └── annotation.py            # [Phase 3] E7 주석·비알람 마커 정규식 (순수)
├── infrastructure/
│   ├── polestar_mcp_client.py   # 폴스타 MCP 서버(Plan 04) 고수준 도구 호출 — 결정적 MCP 클라이언트(SSE)
│   └── decision_store.py        # 판정 감사 JSONL append (logs/alarm_decisions.jsonl)
├── application/
│   ├── event_receiver.py        # asyncio TCP 서버 — 단일행 JSON 수신
│   ├── gate_pipeline.py         # 수신→dedup→신호 수집→판정→감사→라우팅/트리거
│   └── investigation_trigger.py # PAGE 시 Plan 02 비동기 emit (계약: §8)
└── settings.py                  # NoiseGateConfig 추가 (pydantic-settings)
```

- **정책 모듈 순수성**: `notification_policy.py`는 표준 라이브러리만 의존한다. 상관·그래프·이상탐지 결과는 워커(gate_pipeline)가 순수함수로 산출해 **인자로 주입**한다(collectorinfra flapping/storm 패턴). domain이 infrastructure를 import하면 arch-check 위반.
- **수신 파이프라인 MVP(D-008 예약)**: collectorinfra는 alarm_server(독립 프로세스) + Redis Stream + 워커 구조였다. SREAgent MVP는 **단일 asyncio 프로세스**(TCP 수신 → in-process 큐 → 게이트)로 시작한다 — Simplicity First. Redis Stream 분리는 유실 방지·수평 확장이 실제로 필요해질 때 별도 결정으로 승격한다.

## 4. 이벤트 수신 — 폴스타 알람 스키마 (실측 이식)

폴스타는 알람 발생/해소 시 TCP로 **개행 구분 단일행 JSON**을 push한다(기본 포트 9100, `readline()` + `json.loads()` 파싱). 수신 포트는 설정(`listen_port`, 기본 9100)으로 변경 가능하게 한다 — 9100은 node_exporter 기본 포트와 같아, 중앙 호스트에 exporter를 함께 두는 배치나 Plan 06 R-B 로컬 픽스처와 충돌할 수 있다.

| JSON 키 | 필드 | 비고 |
|---|---|---|
| `dbId` | db_id | 폴스타 인스턴스 식별자(§6.1) |
| `serverName` | server_name | **DB 조회 키** (`CMM_RESOURCE.NAME` 매칭) |
| `hostname` | hostname | **프로세스 API 조회 키** |
| `ipAddress`, `resourceAncestry` | ip_address, resource_ancestry | IP·폴스타 트리 경로 |
| `alarmId` | alarm_id | 발생 건별 발급 — 재처리 방지 키(이력 식별 키 아님) |
| `severity` | severity | **0=해소, 1=주의, 2=경고, 3=심각** |
| `alarmStatus` | alarm_status | UI ACK 상태 — **해소 판정에 쓰지 않음** |
| `resourceType`/`resourceName` | resource_type/name | `server.Server`, `server.Cpus` 등 |
| `alarmName` | alarm_name | `CMM_ALARM_DEF.NAME`과 동일 |
| `alarmTime` | alarm_time | `yyyyMMddHHmmss` |
| `conditions`/`conditionLog` | conditions/condition_log | 임계 정의 / 실제 발화 값(텍스트 신호원) |
| (파생) | is_clear | **`severity == 0` 단독 기준** |

**핵심 함정 — 식별자 이원화**: `serverName`과 `hostname`은 서로 다른 값이다(실측: `serverName="cop0-aisapd02"` vs `hostname="saisvd01"`). DB 이력 조회는 server_name, 프로세스 API는 hostname을 써야 하며, 혼동 시 "이력 0건 → 첫 발생" 오판이 난다.

## 5. 결정 순서 (`decide_notification` — 순서형, 첫 종착 확정)

```
[입력] event, history_stats, noise_ctx, config
       + 워커 주입: self_heal, inhibited, flapping, storm, correlated, anomaly_severity
step1   실효심각도 = max(폴스타 severity, 상향 후보)      ← E3 공급원(상향 전용, 하향 불가)
step2   신호 수집 실패 보수화(source == "unavailable")
step3   심각도 3 → 항상 PAGE (단락)                      ← 불변. 신규 단계는 전부 이 뒤에만
step4   해소(sev 0)·자가복구 상관 SUPPRESS
step5   수집 실패 + sev ≥ 1 → 보수 PAGE
step6   유지보수(IS_MAINTENANCE) SUPPRESS
step6.4 의존성 억제 — 부모 AVAIL_STATUS ≠ 0 (Phase 2에서 다홉 확장)
step6.5 인히비션(동일 서버 상위 심각도 활성 시 하위 억제)
step6.7 플래핑 SUPPRESS (Nagios %-state-change + 히스테리시스)
step7   스톰 SUPPRESS (동일 서버 사건창 다발)
step7.5 [Phase 2] 크로스-호스트 상관 SUPPRESS (별도 사유로 감사)
step8   매트릭스: 실효심각도(2·1) × 중요도(높음/보통/낮음) → 티어
step9   보조 조정(알림정책·변경 근접 promote 등, 1단계 이동·승격 우선)
[출력]  NotificationDecision(tier, reason, priority, signals, fingerprint)
        → decision_store 감사 → 라우팅 + PAGE면 조사 트리거(§8)
```

**재발생 dedup(게이트 앞단, E1 관측성 포함 구현)**: `compute_fingerprint = SHA1(db_id·server_name·alarm_name·resource_name)` 기반 TTL dedup. 원본의 사후 교훈을 처음부터 반영한다 —

- 상태는 `{first_seen, last_notified, last_seen, count}` dict로 시작. **TTL 비교는 `last_notified`(비중복 처리 시에만 갱신) 기준 고정창** — `last_seen` 기준이면 슬라이딩 창으로 변질되어 지속 재발 알람이 영원히 재통보되지 않는 회귀가 난다.
- 억제된 재발생도 감사 사각지대가 없도록 **억제 시점에 `type="recurrence"` 감사 레코드를 직접 기록**하고, TTL 만료 재통보 시 "직전 창 N회 재발" 메타를 대표 통보에 첨부한다.
- in-memory dedup dict는 값 bound와 **키 만료 sweep을 함께** 구현한다(Known Mistakes: 데몬류 in-memory dict 원칙).

## 6. 폴스타 신호 수집 (MCP 경유 — D-007 예약)

신호 수집은 **Plan 04의 폴스타 MCP 서버를 경유**한다(D-013). DB 드라이버·연결 문자열·방언 분기·고정 SQL은 전부 MCP 서버에 내장되고, 게이트는 `polestar_mcp_client.py`(결정적 MCP 클라이언트 — LLM 미개입)로 집약 도구 `polestar_noise_signals(source, server_name, alarm_name)`를 1회 호출한다. 미등록 db_id는 호출 전 선확인(`list_sources` 캐시)하고, 서버 다운·타임아웃 시 `source="unavailable"`로 반환해 step2/5 보수화가 발동하게 한다. 집약 도구 1회 호출로 충족되는 것은 **Phase 1(MVP) 신호까지**이며, Phase 2/3 신호(E4 다홉 토폴로지·E5 변경 이력·E3 메트릭 추이)는 Plan 04 §4.2의 해당 도구(`polestar_topology`·`polestar_change_history`·`polestar_metric_trend`)를 동일 결정적 클라이언트로 추가 호출한다. 아래 §6.1~6.3은 **MCP 서버에 내장될 고정 SQL의 명세**다(구현 위치는 Plan 04 §4).

### 6.1 인스턴스·방언

| db_id | 엔진 | 스키마 한정 | 비고 |
|---|---|---|---|
| `polestar_cm_gp` | PostgreSQL | `polestar.` (소문자, search_path 미포함 — 한정 필수) | `LIMIT n` |
| `polestar_cm_yd` | PostgreSQL | `polestar.` | 〃 |
| `polestar_b0` | IBM DB2 | `POLESTAR.` (대문자 필수) | `FETCH FIRST n ROWS ONLY`·집계 **전** `CAST(... AS DECIMAL)`(`::numeric` 금지)·결과 칼럼 소문자화 대응 |

다홉 토폴로지·변경 상관 등 고급 조회는 PostgreSQL(gp/yd)만 지원하고, b0(DB2)는 1홉 폴백/미조회로 보수적 강등한다.

### 6.2 신호별 소스 테이블 (고정 SQL, 파라미터는 이스케이프 리터럴)

| 신호 | 소스 | 골격 |
|---|---|---|
| 중요도·유지보수 | `cmm_resource` | `IMPORTANCE_ID`·`IS_MAINTENANCE` — `WHERE DTIME IS NULL AND RESOURCE_TYPE='server.Server' AND NAME='<server>'` |
| 알림정책 | `cmm_alarm_def D JOIN cmm_alarm_def_noti DN ON DN.DEFINITION_ID = D.MASTERDEFINITION_ID` | 알람명 기준 COUNT (조인 키가 이력 조인 `CA.DEFINITION_ID = D.ID`와 **다름**에 주의) |
| 1홉 의존성 | `cmm_resource` self-join (`AVAIL_DEPEND_RESOURCE_ID`, `_2`) | 부모 `AVAIL_STATUS`(0=정상) |
| 다홉 토폴로지 [Phase 2] | 〃 | 엣지 장기 캐시(86400s) + 조상 ID들만 `WHERE ID IN (...)` 신선 조회, BFS 홉 상한 5·방문집합 순환 방어 |
| 알람 이력 | `cmm_alarm CA JOIN cmm_alarm_def D JOIN cmm_resource CR/SVR` | `SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)`. **`CMM_ALARM.CTIME`은 timestamp, `CMM_RESOURCE.CTIME`은 epoch ms BIGINT — 타입이 다름** |
| 메트릭 추이 [Phase 3] | `cmm_metric_stat_h/d/m` | `resource_type`+`definition_name`(`'Utilization'`/`'MaxIORate'`) 조합으로 지표 판별, 장기 이력은 `_m` |
| 변경 이력 [Phase 2] | `cmm_resource_lifecycle_history` | gp/yd만. 알람 직전 창 변경 → step9 promote(억제가 아니라 승격) |
| OS 설정 | `core_config_prop` (EAV) | `MAX(CASE WHEN NAME='...' ...)` 피벗. `IS_LOB=1`→`STRINGVALUE`(CLOB), `0`→`STRINGVALUE_SHORT` |
| 실시간 프로세스 | REST `GET /rest/server/process/listByhostname?hostname=` | `p100cpu`/`pmem`/`rss` 랭킹. **`args`에 접속문자열·패스워드 노출 가능 — 마스킹 필수** |

### 6.3 도메인 제약 (금지 조인 — collectorinfra D-022/D-028 계승)

- `CMM_RESOURCE.RESOURCE_CONF_ID = CORE_CONFIG_PROP.CONFIGURATION_ID` 직접 조인 **금지**. 브릿지는 `CMM_RESOURCE.HOSTNAME = CORE_CONFIG_PROP.STRINGVALUE_SHORT` + `NAME='Hostname'` 동반.
- `cmm_vendor`·`cmm_os`·`cmm_os_param` 조회 금지 — 벤더/OS 정보는 전부 `core_config_prop` EAV로.
- 전 수집은 개별 try/except로 부분 반환(한 try 블록에 묶지 말 것), 실패 시 `source="unavailable"`로 표기해 step2/5의 보수화가 발동하게 한다. 침묵적 폴백 금지.
- 금지 조인·읽기 전용은 MCP 서버의 `validate_readonly` + 도메인 deny 검증으로 강제된다(Plan 04 §6).

## 7. 고도화 항목 단계화 (원본 E1~E7 → Phase)

| Phase | 항목 | 내용 | 플래그(기본 off) |
|---|---|---|---|
| **1 (MVP)** | 코어 게이트 | §4 수신 + §5 step 전 단계(7.5 제외) + E1 관측성 포함 dedup + 감사 JSONL | `noise_gate_enabled` |
| **2** | E2 크로스-호스트 상관 | `signature_tokens`(alarm_name·resource_type·시그니처, **server_name 제외**) → Jaccard → 온라인 그리디 군집(첫 도착=대표·소급 없음·동점 시 first_ts 오름차순). `correlation_min_cluster_size`번째부터 step7.5 억제. 사건창 sweep·버퍼 상한 필수 | `cross_host_correlation_enabled`, `correlation_sim_threshold=0.5`, `correlation_window_seconds=120`, `correlation_min_cluster_size=2`, `correlation_buffer_max=1000` |
| **2** | E4 다홉 토폴로지 | 정적 엣지 캐시/동적 상태 분리, **하이브리드**: cascaded이고 root가 통보됨 → SUPPRESS, root 미통보 → DASHBOARD 강등(재현율 보전) | `multi_hop_cascade_enabled`, `topology_cache_ttl_seconds=86400`, `topology_max_hops=5` |
| **2** | E5 변경 상관 | lifecycle_history 근접 변경 → step9 **promote**("변경 근접 — 원인성"). 피드 부재 시 graceful | `change_correlation_enabled` |
| **3** | E3 동적 baseline | Holt-Winters(additive, stdlib) 잔차 z-score → `anomaly_severity` 상향 후보(step1 max 병합, 상향 전용). 메트릭 매핑 화이트리스트(cpu→`server.Cpus`+Utilization 등), 이력 <3주기면 skip | `dynamic_baseline_enabled`, `anomaly_z_high=3.0`, `anomaly_min_periods=3` |
| **3** | E7 텍스트·주석 신호 | (a) dedup 억제 전 주석 마커(planned_work/resolution/operator_ack) 정규식 추출·감사, (b) 비알람 사전 분류(step0.5 SUPPRESS — 애매하면 알람 간주), (c) 파서 견고성(실패 시 침묵 드롭 금지·보수 PAGE), (d) 사이트 토큰 상관 차원. **코로보레이션 게이팅**: 주석 단독으로 억제 강화 금지 — planned_work AND (E2 클러스터 소속 OR E5 변경 근접 OR resolution)일 때만 DASHBOARD 강등 | `annotation_harvest_enabled`, `non_alarm_filter_enabled`, `format_tolerant_parsing_enabled` |
| **3** | E6 통보 컨텍스트 보강 | kind 분류(cpu/memory/disk/network/process/log) → kind별 L1 프로파일 조회를 통보에 첨부(라우팅 불변·첨부만). 프로세스 표는 Plan 04 §4.2 `polestar_process_snapshot` 재사용 | `message_enrichment_enabled`, `enrichment_min_tier=PAGE` |

## 8. Plan 02 자동 조사 트리거 계약 (원본 §14 계승)

- **정방향(push)**: `gate_pipeline`이 최종 결정 직후 `tier == PAGE`(또는 `investigation_trigger_min_tier`)일 때 **비동기·비차단(fire-and-forget)** emit. 게이트 반환·라우팅 경로는 무변경.
- **페이로드**: 게이트 보유값만 — `AlarmEvent` + `NotificationDecision`(tier·reason·signals·fingerprint) + recurrence 메타 + [Phase 2] 클러스터 메타·root_resource.
- **노이즈 상속**: dedup 억제 재발·클러스터 자식·연쇄 하위는 트리거되지 않는다 → 조사 폭주 자연 방지.
- **역방향은 escalate-only**: 조사 결과는 게이트 판정을 소급 변경하지 않으며, 상향(PAGE 승격) 제안만 가능하다.
- 플래그: `investigation_trigger_enabled`(기본 off).
- **외부 공용 계약**: 이 페이로드는 Plan 05 §4에서 `contract_version` 필드와 함께 **collectorinfra 등 외부 호출자와의 공용 계약으로 승격**된다 — 내부 게이트든 외부 게이트든 동일 스키마로 Plan 02 dispatcher에 수렴한다.

## 9. 설정 (pydantic-settings — Known Mistakes 원칙 준수)

`AgentSettings`에 `NoiseGateConfig`를 **`Field(default_factory=...)`** nested로 추가한다(임포트 시점 고정 방지). `.env`의 list/dict 값은 JSON 배열 형식, 인라인 주석 금지. 설정 유무 판단은 `os.getenv()`가 아니라 pydantic 필드로만.

## 10. 테스트·수용 기준

- **단위**: `decide_notification` 결정표 테스트(각 step 종착·심각도 3 단락·수집 실패 보수화), dedup 고정창 회귀 테스트(`last_notified` 기준), [Phase 2] 군집 결정성(동일 이벤트 시퀀스 → 동일 판정 시퀀스)·sweep 테스트.
- **e2e(옵트인 `RUN_E2E=1`)**: Plan 03 mock 생성기로 S1~S8 시나리오 주입 → 기대 티어 대조.
- **게이트**: 코드 변경 시 `python scripts/arch_check.py --ci` 통과 필수. 새 모듈의 `MODULE_LAYER_MAP` 등록 확인(등록 누락 시 검사가 조용히 무력화됨 — D-003 교훈, 분모 확인).
- **수용 기준(MVP)**: ① sev3 이벤트는 어떤 플래그 조합에서도 PAGE, ② 억제 이벤트 전건이 감사 JSONL에 남음(억제 ≠ 삭제), ③ 신호 수집 전 경로 실패 시에도 게이트가 보수적 PAGE로 종착(침묵 드롭 0), ④ 플래그 전부 off 시 코어 경로만으로 동작.
