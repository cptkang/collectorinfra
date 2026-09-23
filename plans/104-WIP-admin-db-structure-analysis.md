# 104. DB 구조 분석을 질의 경로 HITL에서 관리자 페이지로 — MCP 연결 DB 목록 · 신규 시스템 연동(스키마 수집·캐시 등록·준비도) · 스키마 변경·신규 내용 점검 · 구조 분석 초안·승인·버전

> **작성일**: 2026-09-17 · **v2** 2026-09-17(신규 시스템 연동 보완) · **v4** 2026-09-17(게이트 확정 · 구현)
> **상태**: **구현 완료 — 계획 WU 잔여 0**(2026-09-21) — 게이트 G-1~G-11 사용자 확정(§7) · A-1~A-8·A-10·B-1~B-8 · 후속 C-1~C-6(§11.4 · A-9는 목 검증 · C-4는 **D-232 본문 등재**) 전건 완료. **남은 것**: 1·2단 경로의 인가 미적용(§11.5 U-6) · **(v9 · 2026-09-23) A-9 실 mlx 실측 완료 — 결함·잔여 R-1~R-7(§11.6)** · 사용자 몫(실브라우저 · 운영 MCP bearer 요구 여부 · 운영 반영 전 체크 2건 · `itam.yaml` 처분 결정) — 그래서 파일명은 `-WIP`
> **성격**: 구현 계획 + 구현 현황(§11)
> **요청 취지(사용자 지시 원문, 2026-09-17)**: *"구조 승인 기능은 admin 페이지에 mcp로 연결된 db리스트를 보여주고 각 db별 스키마 업데이트나 신규 내용을 조회하여 구조 분석을 통해 향후 사용할 수 있도록 정리하는 기능을 추가하라. 위 요건에 맞게 계획을 수정하라."*
> **v2 보완 지시(원문, 2026-09-17)**: *"104번 계획에서 신규 시스템 연동시 db 스키마 분석 및 캐스 등록 등에 대한 기능을 계획서에 보완하라."* — "캐스"는 **캐시**로 읽었다(스키마 캐시 등록). 반영: U5·U6(§0.1) · 실측 §1.7 · R8~R11(§2) · 설계 §3.8 · S5(§3.7) · 트랙 B(§4) · G-7~G-9(§7)
> **v3(2026-09-17)**: 사용자 지시 *"db 관련 정보는 104번 계획에서 조사하여 자동으로 스키마, 캐시 등을 생성하는 기능을 구현할 계획이다. 이에 맞게 처리하라."* — `plans/95`(자산관리)가 사용자에게 요청하던 운영 DB 정보와 수작업 산출물을 이 계획이 받는다: 물리 식별자(95 G-4 → O-2) · 서버 변수(95 G-13 → O-1) · `db_schema` 후보(95 G-2 → §3.8.4) · 코드성 컬럼 값(95 G-5 → **G-10**) · 수동 프로필·지식·시드(95 W-6·W-7 → O-4~O-8) · 양식 LLM 매핑 자동 등록 처분(**G-11**) · D-228 가드 후속(**B-8**). 반영: §3.4-4 · §3.8.1 O-1 · §3.8.6 · §4 B-8·순서 불변식 · §7 G-10·G-11 · §8 · §9 ⑧
> **상위/연결 계획**: **`plans/102`** v3(G-8 "3단 순차 러너의 HITL 조건" → 이 계획으로 해소) · **`plans/103`**(3단 기능 동등성 — 질의 경로에서 구조 승인이 사라져야 복합 실행이 막히지 않는다) ·
> `plans/27`(LLM 구조 분석 + `structure_approval_gate` — 이 계획이 질의 경로 부분을 대체) · `plans/30`(캐시 유효성 감사 · 문제 F "스키마 변경 시 structure_meta 미무효화") · `plans/32`(수동 프로필) ·
> `plans/59`·`59-a`(관리자 RBAC·UI) · `plans/68`(설정 웹 UI) · `plans/77`(유사어 제안 큐 + 관리자 승인 UI — 가장 가까운 관리자 탭 선례) · `plans/95`(자산관리 DB — 구조 정보 없는 신규 DB의 첫 사례)
> **관련 결정**: D-003(읽기 전용) · D-010·D-011·D-019(스키마 캐시·지문 — **D-011 설명 자동 생성은 v2 G-9로 부분 개정 대상**) · **D-020**(LLM 구조 분석 + HITL + 자동 프로필 — **개정 대상**) · D-027(감사) · D-053(사본 금지) · D-069·D-070·D-083(관리자 RBAC·break-glass) ·
> D-122(`execute_sql` 노출) · D-127(과금 승인) · D-129·D-135(설정 카탈로그·리로드) · **D-131**(SQL 지식 정본 = 프로필 + knowledge + 레지스트리) · D-161(승격-폐기 동반) · D-214(자산 DB · 로컬 파생 금지)
> **신규 결정 예약**: **D-227**(§9) — `docs/02_decision.md` 「채번 이력」 표 등재(2026-09-17). 채번 실측: `## D-` 헤더 최댓값 222 · 채번 이력 최댓값 D-226(`plans/103`) → D-227.
> **실측 기준**: 2026-09-17 작업 트리(`multiintent`, HEAD `c64ef98` + 미커밋). 표시 없는 항목은 파일을 읽어 확인했고, **"코드 읽기 추정"** 표시는 실행하지 않은 판단이다.

---

## 0. 요약

### 0.1 요구를 검증 가능한 문장으로

| # | 사용자 요구 | 검증 가능한 문장 |
|---|---|---|
| **U1** | admin 페이지에 MCP로 연결된 DB 목록 | 관리자 화면이 **MCP 서버의 실제 소스 목록**(`list_sources`)을 보여주고, 각 소스의 연결 상태·앱 등록·활성 여부·구조 정보 상태를 함께 보인다 |
| **U2** | DB별 스키마 업데이트나 신규 내용 조회 | DB마다 **지난 점검 대비 무엇이 바뀌었는지**(추가·삭제·타입 변경된 테이블·컬럼, 그리고 구조 정보가 참조하는 코드값의 신규 항목)를 결정적으로 보인다 |
| **U3** | 구조 분석 | 관리자가 **명시 실행**해 구조 분석 결과를 **초안**으로 만든다 |
| **U4** | 향후 사용할 수 있도록 정리 | 관리자가 **승인한 결과만** 질의 파이프라인이 쓴다. 버전이 남고 되돌릴 수 있다 |
| (귀결) | "구조 승인 기능은 admin 페이지에" | **질의 도중 구조 승인을 묻는 HITL을 없앤다** — 질의 경로는 구조를 분석하지 않고 읽기만 한다 |
| **U5** (v2) | 신규 시스템 연동 시 DB 스키마 분석 | 새 소스(MCP에만 있음·미활성)를 관리자가 **활성화 전에** 스키마 전체 수집 → 구조 분석까지 끝낼 수 있다. **첫 사용자 질의가 수집·LLM 생성을 떠안지 않는다** |
| **U6** (v2) | 캐시 등록 등 | 질의 파이프라인이 읽는 캐시 산출물(스키마·관계·컬럼 설명·유사어·DB 설명·구조 정보·값 인덱스)을 **한 흐름에서 등록**한다. 산출물별 등록 상태와 **활성화 준비도**를 코드가 판정해 보인다 |

### 0.2 현행 판정 — 요구 6개 모두 없거나 부분이다

| # | 현행 | 근거(§1) |
|---|---|---|
| U1 | ❌ 앱은 MCP `list_sources`를 **한 번도 호출하지 않는다.** DB 목록은 `ACTIVE_DB_IDS`에서만 나온다. 관리자 화면에 스키마·구조 탭이 없다 | §1.3·§1.4 |
| U2 | ◐ 지문(fingerprint)은 있으나 **테이블명 + 컬럼 개수**만 해시 — 컬럼 이름·타입 변경을 못 잡고, 무엇이 바뀌었는지 보여주지 않는다. 구조 정보는 스키마가 바뀌어도 **재분석 신호가 없다** | §1.2 |
| U3 | ◐ 분석 함수는 있으나 **질의 경로 전용 비공개 함수**이고, **그 질의에 관련된 테이블만** 본다 — DB 단위 결과가 어떤 질의가 먼저 왔느냐에 좌우된다 | §1.1 |
| U4 | ❌ 초안·승인·버전이 없다. 승인은 채팅에 "승인"을 **타이핑**하는 방식이고, 1·2단 경로는 승인을 **고지 없이 우회**하며 분석 결과를 **매 질의 버린다** | §1.1 |
| U5 | ❌ 워밍업이 없다. 활성화 후 **첫 질의가 요청 안에서** MCP 전체 수집 + **테이블당 1회 순차 LLM 설명 생성**을 한다. 관리자 화면의 「DB 연결 설정」은 레거시 단일 DB 폼이라 신규 소스를 다루지 못한다 | §1.7 |
| U6 | ◐ 등록 진입점이 API 14종·CLI·채팅·지연 경로로 흩어져 있다. `refresh_cache`는 스키마·관계·지문만 만들고, DB 설명은 수동 트리거로만 생기며, 유사어 시드는 런타임 로드가 0건이다. 상태 API는 설명·유사어 건수를 **항상 0**으로 보여 등록 완료를 판정할 수 없다 | §1.7 |

### 0.3 한 줄 권고

**구조 분석을 "질의 중에 우연히 일어나 사람에게 묻는 일"에서 "관리자가 DB 단위로 점검·분석·승인해 두는 일"로 옮긴다.**
질의 경로는 승인된 구조 정보(또는 수동 프로필)를 **읽기만** 하고, 없으면 사유를 알린다. 이로써 `plans/102` X-T8·`plans/103` 3단 복합 실행의 HITL 차단이 원천적으로 사라진다.
**(v2)** 신규 시스템은 **「발견 → 스키마 수집·캐시 등록 → 설명·구조 초안 → 적용 → 준비도 판정 → 활성화」** 한 흐름으로 관리자 탭에서 끝낸다(§3.8). 새 등록 로직을 만들지 않고 흩어진 기존 함수를 잡 하나로 묶는다. 설정 파일(레지스트리·`config.toml`·`.env`)은 앱이 쓰지 않고 **조각만 내보낸다**.

---

## 1. 현황 실측

### 1.1 구조 분석·승인 — 질의 경로 안에 있다

| 지점 | `file:line` | 현황 |
|---|---|---|
| 분석 | `src/nodes/schema_analyzer.py:274-315` `_analyze_db_structure` · 프롬프트 `src/prompts/structure_analyzer.py:7-52` · 스키마 텍스트 `schema_analyzer.py:205-246` | 산출 `{patterns:[eav 또는 hierarchy …], query_guide}`. 패턴이 비거나 파싱 실패면 `None` |
| **분석 범위** | `schema_analyzer.py:1056` → `:1145` | 입력 `schema_dict`는 **이번 질의의 관련 테이블만** — DB 전체가 아니다 |
| 샘플 | `:354-425` `_collect_structure_samples` · 검증 `:318-351` | LLM이 만든 SELECT 최대 3건(SELECT 전용·LIMIT 필수) → `structure_meta["samples"]` |
| 저장 | `:724-792` `_save_structure_profile` | Redis `schema:{db_id}:structure_meta`(TTL 없음 · `redis_cache.py:282-311`) + **`config/db_profiles/{db_id}.yaml`에 `source: auto`로 기록**(`:764-784`). 기존 파일이 `source: manual`이면 파일은 건너뛰고 Redis만(`:745-755`). 파일 캐시 백엔드는 구조 정보를 저장하지 않는다(`cache_manager.py:341-372`) |
| 조회 3단 | `:1074-1187` | ①수동 프로필(`source: manual`) → Redis에 덮어씀(`:1087-1092`) ②Redis ③LLM 분석 — `enable_structure_approval` on이면 `awaiting_approval` 반환(`:1148-1174`), off면 바로 저장(`:1175-1179`) |
| 플래그 | `src/config.py:1310` 기본 **True** · 운영 `.env` 미설정 · `.env.example:533` · 설정 카탈로그 `src/api/settings_catalog.py:260` · 도움말 `config/settings_help/general.yaml:162-190` | 도움말 문구가 이 플래그를 "질의 해석 승인"으로 **잘못 설명**한다(조사 보고 · 문구 확인 필요) |
| 게이트 | `src/nodes/structure_approval_gate.py:17-72` · 배선 `src/graph.py:500-502,613-628` · `interrupt_before` `:697-701` | 3단 단일 DB·4단에서만 도달. **1·2단은 도달하지 않는다** |
| 승인 입력 | `src/api/routes/query.py:466-474` `_resolve_turn_approval` · `:714-721` | 전용 라우트·UI 없음 — 사용자가 채팅에 "승인"을 쳐야 한다. `app.js`에 구조 승인 UI 없음 |
| **1·2단 우회** | `src/orchestration/subagents.py:539-613` `_run_single_db_pipeline`(`:572`에서 `schema_analyzer` 함수 호출) | `awaiting_approval`을 보지 않고 진행 · 분석 결과를 붙이지도 저장하지도 않는다 → **구조 정보가 없는 DB는 매 질의 LLM 분석을 다시 하고 버린다** |
| 멀티 DB | `src/nodes/multi_db_executor.py:1143-1206` | 분석·승인 없음 — 수동 프로필 또는 Redis 구조 정보가 있으면 붙이고, 없으면 없이 진행 |
| 순차 러너 | `src/orchestration/sequential_runner.py:9-11,44-46` | 위 우회 때문에 승인 플래그가 켜져 있으면 **진입 자체를 막는다**(`plans/102` X-T8) |
| 승인 루프 의심 | `schema_analyzer.py:1245-1256` · `structure_approval_gate.py` | **코드 읽기 추정**: 승인 후 재진입에서 `awaiting_approval`·`approval_context`를 비우지 않아 게이트로 다시 갈 수 있다. 라우팅 단위 테스트만 있고(`tests/test_graph_routing_gaps.py:64-96`) 종단 테스트 없음 |

### 1.2 스키마 캐시·변경 감지

| 지점 | `file:line` | 현황 |
|---|---|---|
| Redis 키 | `src/schema_cache/redis_cache.py:6-13,185-192` | `schema:{db_id}:meta`(fingerprint·cached_at·table_count …) · `:tables` · `:relationships` · `:descriptions` · `:synonyms` · `:fingerprint_checked_at` · `:structure_meta` · `:column_value_index`(TTL 1일) |
| 지문 | `src/schema_cache/fingerprint.py:17-54` | `information_schema.columns`를 **테이블명별 컬럼 개수**로 묶어 SHA-256 — 컬럼 이름·타입 변경, 같은 개수의 교체를 **못 잡는다** |
| 지문 비교 흐름 | `cache_manager.py:1208-1366` `get_schema_or_fetch` · `:1460-1545` `refresh_cache` | 메모리 5분 → Redis(점검 TTL 1800초 `config.py:645`) → 지문 SQL 비교 → 전체 재수집·저장·정리·설명 자동 생성 |
| **엔진 호환(코드 읽기 추정)** | `fingerprint.py:17-25` | `information_schema` 전용 SQL — **DB2(`polestar_b0`)에서 실패** 가능. PG 비 public 스키마·MariaDB는 SQL 지문과 `schema_dict` 파생 지문의 키 형태가 달라 **TTL 만료마다 "변경됨"**으로 판정될 수 있다 |
| **구조 정보 무효화 없음** | `redis_cache.py:1437-1450` | 스키마가 바뀌어도 `structure_meta`는 그대로 — 명시 `invalidate`로만 지워진다(`plans/30` 문제 F 미해소) |

### 1.3 관리자 페이지

| 지점 | `file:line` | 현황 |
|---|---|---|
| 기술 | `src/static/` · `server.py:673-684` | 바닐라 JS(IIFE) · 프레임워크·CDN 없음 · `/admin` → `static/admin/dashboard.html` |
| 탭 | `dashboard.html:59-67` · `admin.js` | 환경변수 설정(D-129) · DB 연결 설정(단일 DB 레거시 폼 · PG 전용 `admin.py:972-1115`) · 사용자 관리 · 감사 로그 · 열린 사건 · 알람 피드백 · DRM. **스키마·구조 탭 없음**(정적 파일에서 `schema-cache` 참조 0) |
| 인증 | `admin.js:12-43,109-126` · `src/api/dependencies.py:188-215` `require_admin_user` | 토큰 없으면 `/login?next=/admin` · API는 `require_admin_user`(break-glass `type=="admin"` 또는 사용자 토큰 + DB `role=="admin"`) · HTML 자체는 서버 보호 없음(JS 게이트 + API 게이트) |
| 스키마 캐시 API | `src/api/routes/schema_cache.py`(14개 · 전부 `require_admin_user`) | 생성(`:98` — **요청 핸들러 안에서 DB별 동기 루프**) · 상태 · 상세(**구조 정보 미포함** `:257-299`) · 삭제 · DB 설명 · 유사어. **구조 정보·소스 목록 엔드포인트 없음** · 감사 기록 없음 · 테스트 0 |
| **경로 가림** | `schema_cache.py:257`(`GET /admin/schema-cache/{db_id}`)이 `:377`(`GET /admin/schema-cache/db-descriptions`)보다 **먼저 선언** | 선언 순서상 `db-descriptions`가 `{db_id}`로 잡힌다(선언 순서 확인 · 요청 미실행) |
| 감사 선례 | `src/api/routes/admin.py:376-420` `_log_settings_event` · `src/domain/audit.py:35-36` `ADMIN_ACTION`·`CACHE_OPERATION` | 설정 변경은 감사 기록(성공 여부를 응답에 표시). 두 열거값은 **사용처 0** |
| 가까운 선례 | `plans/77`(유사어 제안 큐 + 관리자 승인 탭 · TODO) | "제안 → 관리자 승인" UI 모양이 같다 |

### 1.4 MCP 연결과 DB 목록

| 지점 | `file:line` | 현황 |
|---|---|---|
| MCP 도구 | `mcp_server/mcp_server/tools.py:298-321` `list_sources` → `[{name, type, readonly, query_timeout, max_rows}]` · `:255-296` `health_check` · `:59-99` `search_objects` · `:176-253` `get_table_schema`(컬럼 이름·타입·NULL·기본값·PK · FK) · `:101-174` `execute_sql`(읽기 전용 가드) | 필요한 읽기 도구가 **전부 있다** |
| 앱 클라이언트 | `src/dbhub/client.py` — `health_check`(`:183-204`) · `search_objects`(`:232-265`) · `get_table_schema`(`:267-296`) · `get_full_schema`(`:441-456,584-614`) · `execute_sql`(`:484-553`) | **`list_sources` 메서드 없음** |
| 소스 활성 | `mcp_server/config.toml:62-134`(`[[sources]]` 9개) + `mcp_server/.env` `{NAME}_CONNECTION` | 연결 문자열이 있는 소스만 활성 |
| 앱 등록·활성 | `config/db_registry.yaml`(등록) · `ACTIVE_DB_IDS`(`config.py:543-589`) | **세 목록이 따로 논다** — MCP에만 있는 소스(예: `infra_db`는 레지스트리 미등록), `ACTIVE_DB_IDS`에만 있는 이름이 가능. 레지스트리 `env_connection_key`는 MCP 명명(`*_CONNECTION`)과 달라 쓰이지 않는다 |

### 1.5 수동 프로필 ↔ LLM 분석 산출물

| 항목 | 수동 프로필(`config/db_profiles/polestar*.yaml`, 596~641줄, `source: manual`) | LLM 분석 산출 |
|---|---|---|
| 필드 | `patterns`(EAV·계층 + `entity_columns`·`excluded_join_columns`·`value_joins`·`direct_join`·**`known_attributes`**) · `query_guide` · **`query_examples`** · **`allowed_tables`** · `alarm_allowed_tables` · `column_synonyms` | `patterns` + `query_guide` (+ 샘플) |
| 소비처 | `query_generator` · `multi_db_executor:1461-1487` · `field_mapper` · `sql_validation` · `schema_analyzer:930-1053` | 같은 키를 읽되 없는 키는 빈 값 |
| 정본 규칙 | D-131 — SQL 지식 정본 = 프로필 + knowledge + 레지스트리 · `plans/95` §4.6.2 — **로컬 샌드박스에서 파생한 산출물을 운영 정본으로 승격 금지** | |

→ **LLM 분석은 수동 프로필을 대체하지 못한다**(필드가 훨씬 적다). 관리자 기능의 역할은 **수동 프로필이 없는 DB의 출발점**과 **수동 프로필이 있는 DB의 변경 점검**이다.

### 1.6 먼저 고쳐야 할 안전 결함

| # | 결함 | `file:line` | 위험 |
|---|---|---|---|
| **S1** | **캐시 무효화가 수동 프로필 파일을 지운다** | `src/schema_cache/cache_manager.py:1549-1568` `invalidate` → `:1585-1601` `_delete_db_profile` — `source`를 보지 않고 `config/db_profiles/{db_id}.yaml`·`.json` 삭제(확인) | 관리자 API `DELETE /admin/schema-cache/{db_id}`(`schema_cache.py:302`)나 채팅 캐시 관리의 무효화 한 번으로 **수백 줄짜리 수동 정본 프로필이 런타임에서 사라진다**(git 추적 파일이라 복구는 가능하나 운영 서버에서는 즉시 결손) |
| **S2** | 채팅 캐시 관리에 권한 확인 없음 | `src/nodes/cache_management.py:727-747`(생성·무효화 동작) · `src/orchestration/subagents.py:944-962` — 역할·권한 확인 코드 grep 0건 | 일반 사용자가 채팅으로 무효화(→ S1)를 일으킬 수 있다 |
| **S3** | 경로 가림 | §1.3 | DB 설명 목록 API가 동작하지 않을 수 있다 |
| **S4** | 스키마 캐시 변경 작업 감사 없음 | §1.3 | 누가 캐시·프로필을 지웠는지 추적 불가 |

### 1.7 (v2) 신규 시스템 연동 — 현행 절차와 캐시 등록

#### 1.7.1 현행 편입 절차(`DB_BACKEND=dbhub` · 정정본 `docs/18_known_mistakes.md:233` · D-214 ①⑤)

| # | 단계 | 방식 | 소유 파일 · 근거 |
|---|---|---|---|
| 0 | 엔진이 PG·DB2·MariaDB가 아니면 드라이버·실행기·인트로스펙션 구현 | 코드 | `mcp_server/mcp_server/{db,tools,config}.py` |
| 1 | `[[sources]]` 추가 + `{NAME}_CONNECTION` + `.env.example` 등재 | 수동 | `mcp_server/config.toml` · `mcp_server/.env` · `test_env_example_coverage.py:123` |
| 2 | MCP 서버 재기동 | 수동 | 핫 리로드 없음 — 설정은 lifespan에서 1회 로드(`mcp_server/mcp_server/server.py:86-90`) |
| 3 | 레지스트리 항목(`db_id`·`engine`·`db_schema`·`description`·존·위치 어휘) | 수동 · **앱 재기동** | `config/db_registry.yaml` — `reload_registry`(`src/routing/registry.py:442`) 호출처 0 |
| 4 | 구조 정본 `source: manual` 프로필(대안은 질의 중 LLM 자동 분석) | 수동 · 생성기 없음 | `config/db_profiles/{db_id}.yaml` |
| 5 | 지식 큐레이션(선택) · 유사어 시드 `derive`→`load` | 수동 · 반자동 | `config/knowledge/{db_id}/catalog.yaml` · `scripts/synonym_seeds.py` |
| 6 | 스키마 캐시·컬럼 설명·유사어·DB 설명 생성 | CLI·API 수동 트리거 또는 **첫 질의 지연 생성** | `scripts/schema_cache_cli.py:6-20` · `/admin/schema-cache/*` · `cache_manager.py:1325-1355` |
| 7 | 사용자 권한(`AUTH_DEFAULT_ALLOWED_DB_IDS` `config.py:528` · 사용자별 `allowed_db_ids`) | 관리자 UI | `admin.py:1352-1398` |
| 8 | `ACTIVE_DB_IDS` 추가(**마지막** · D-214 ⑤) | 설정 탭 + 리로드 | `settings_catalog.py:261`(리로드 대상 — 재기동 불요) |

→ 사람이 **파일 4~6개를 손으로 맞추고 재기동 2회**를 해야 하며, 6단계(캐시 등록)는 누가 언제 했는지 남지 않는다.

#### 1.7.2 캐시 산출물 등록 매트릭스

| 산출물 · Redis 키 | 만드는 곳 | LLM | 입력 | 신규 시스템에서의 문제 |
|---|---|---|---|---|
| 스키마 `schema:{db_id}:meta`·`:tables` | `refresh_cache`(`cache_manager.py:1460-1545`) · 지연 3차 조회(`:1290-1320`) | 0 | MCP `search_objects`→테이블마다 `get_table_schema`(`dbhub/client.py:441-456`) | `refresh_cache`는 지문 SQL이 실패하면 `"fingerprint 조회 실패"`로 끝난다(`:1480-1490`). **지문 SQL이 `information_schema` 전용**(`fingerprint.py:17-25`)이라 DB2 신규 시스템은 API·CLI로 등록할 수 없다(코드 읽기 추정) |
| 관계 `:relationships` | 같은 곳 | 0 | **PG `information_schema` 전용 FK SQL**(`dbhub/client.py:584-613` · 실패 시 `[]`) | DB2·MariaDB는 관계가 `[]`일 것(코드 읽기 추정 — MariaDB에는 `constraint_column_usage`가 없다). MCP `get_table_schema`는 **엔진별 FK를 이미 돌려주는데**(`mcp_server/mcp_server/tools.py:196-217,240`) 앱은 컬럼 `references`에만 쓴다(`dbhub/client.py:721-737`) |
| 컬럼 설명 `:descriptions` · 유사어 `:synonyms` | 지연 경로에서 **설명이 비었을 때만**(`cache_manager.py:1325-1355` · `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS` 기본 True `config.py:644`) · API `include_descriptions` · 채팅 | **테이블당 1회 순차**(`description_generator.py:193-200`) | 컬럼 목록만(샘플은 항상 빈 값) — 프로필·지식·레지스트리를 읽지 않는다 | 첫 질의 지연(파일 캐시 실측 `polestar_cm_gp` 394테이블 → 394회 호출). 실패는 테이블 단위로 `{}` + 로그만 남기고 계속 간다(`:159-172`) · **부기(2026-09-17 · 95 v11 · D-228)**: 이 지연 경로의 LLM 유사어 전역 병합(`sync_global_synonyms`)은 수동 프로필(`source: manual`) DB로 한정됐다 — 104 이관 시 이 가드를 재평가한다 |
| DB 설명 `schema:db_descriptions`(전역 Hash) | API(`schema_cache.py:494-544`) · 채팅 · CLI만 | O | 테이블 요약 | **자동 생성 경로 0.** 라우터는 레지스트리 `description`에 이 값을 「상세」로 덧붙인다(`semantic_router.py:227-234,922-925`). 레지스트리 `description`은 기본 `""`이고 검증이 없어 **빈 채로 렌더**된다(`registry.py:117,361` · `semantic_router.py:921`) |
| 구조 정보 `:structure_meta` | 질의 중 `schema_analyzer`(§1.1) | O | 관련 테이블 | §1.1 · §3.5 |
| 값 인덱스 `:column_value_index`(TTL 1일) | 단일 DB `schema_analyzer`에서만 | 0 | 구조 정보의 **EAV 패턴만**(`value_index.py:132-155`) | `SYNONYM_VALUE_RETRIEVAL` 기본 off(`config.py:255` · 운영 `.env:441` false) · EAV가 없으면 항상 빈 인덱스 |
| 유사어 시드 | `scripts/synonym_seeds.py load`만 | 0 | `config/synonym_seeds/{db_id}.yaml` | `load_seed_yaml` **런타임 호출처 0**(`scripts/synonym_seeds.py:233`·`scripts/scenario/runner.py:1145`뿐) · `derive`가 폴스타 테이블명을 하드코딩(`synonym_seeds.py:136-160`)하고 `config/semantic_models` 사본을 요구(`:105-108`) → **비폴스타 신규 DB는 시드를 만들 수 없다** |

#### 1.7.3 신규 시스템 연동의 결함 · 공백

| # | 결함 | `file:line` | 영향 |
|---|---|---|---|
| **N1** | 워밍업 없음 — 기동은 Redis 연결만 확인 | `server.py:368-376` · src 전체 warm/preload grep 0(조사 보고) | 활성화 직후 첫 사용자 질의가 수집 + 순차 LLM 생성을 떠안는다. 응답시간 목표(단순 <10s) 위반 |
| **N2** | 등록 완료를 판정할 수 없다 | `schema_cache.py:98-163`(응답 `description_status`가 설명 생성 **전에** 채워져 `pending` 고정) · `cache_manager.py:1532`(force가 아니면 신규도 `updated`) · `:1650-1674`(`/status`가 설명·유사어 건수를 채우지 않음 → 항상 0) · 설명 0건이어도 `complete` 응답(`schema_cache.py:196-204`) | 관리자가 "등록이 끝났는지" 알 방법이 없다. 구조 정보·DB 설명·값 인덱스 상태는 어떤 응답에도 없다 |
| **N3** | 관계 수집이 PG 전용 | §1.7.2 | DB2·MariaDB 신규 시스템의 조인 힌트 결손(코드 읽기 추정) |
| **N4** | 캐시 등록이 지문 SQL에 묶임 | §1.7.2 | DB2 신규 시스템은 `refresh_cache` 경로로 등록 불가(코드 읽기 추정). 지연 경로는 지문 실패를 삼키고 전체 재수집으로 넘어간다(`cache_manager.py:1265-1290`) |
| **N5** | **LLM 재생성이 사람 자산을 덮는다** | DB 설명: 수동 설정 엔드포인트 docstring *"수동 설정된 설명은 LLM 재생성 시에도 보존된다"*(`schema_cache.py:444`)와 달리 생성 엔드포인트가 무조건 저장(`:534-536`) · 유사어: `save_synonyms`가 컬럼 필드 단위 HSET **치환**(`redis_cache.py:598-626` — 새 단어만으로 JSON을 만들어 덮음 · 코드 확인, 실행 안 함) | 운영자가 등록한 유사어(`source: operator` `cache_manager.py:598`)·수동 DB 설명이 재생성 한 번에 사라진다 — 기존 운영 DB에도 해당 |
| **N6** | 유사어 시드를 신규 DB에 쓸 수 없다 | §1.7.2 | 신규 DB 유사어는 LLM 생성뿐 |
| **N7** | 설정 변경이 재기동에 묶인다 | 레지스트리 lru_cache·모듈 상수(`registry.py:436-442` · `domain_config.py:44-69`) · MCP lifespan 1회 로드 | 관리자 화면에서 "등록"을 끝내도 재기동 전에는 라우터에 보이지 않는다 — 화면이 이 사실을 알려야 한다 |
| **N8** | 활성화 순서 불변식(D-214 ⑤)을 코드가 강제하지 않는다 | 2026-09-17 13:54~14:40 작업 트리 `.env:85`가 `ACTIVE_DB_IDS=polestar,itam`이었고 그동안 `config/db_profiles/itam.yaml`은 없었다(병행 세션 `plans/95` W-12 로컬 확인 — 14:40 `polestar`로 원복 · 당사자 통지). 판정 자료가 아니라 **설정 한 줄로 불변식을 넘을 수 있다**는 예시 | 구조 정보 없는 DB가 활성화되어 질의 중 LLM 분석(T4)에 의존 |
| **N9** | 자동 산출물이 git 정본에 섞인 흔적 | `config/db_profiles/test_db.yaml`(`source: auto` · 커밋 `eb4b3fa`) | G-2 (a)의 근거 보강 — 자동 산출물은 설정 디렉터리에 쓰지 않는다 |
| **N10** | 관리자 「DB 연결 설정」 탭이 신규 소스를 추가하지 못한다 | 연결 테스트가 PG 전용(`admin.py:1078-1107`) · `_update_dbhub_toml`은 경고만 남기는 no-op(`admin.py:500-518`) · 화면 문구 *"저장 시 .env와 dbhub.toml이 업데이트됩니다"*(`dashboard.html:146-147`) · 입력 폼에 DB2·db_id 없음 | 화면 문구가 실제 동작(no-op)과 다르다 — 오해 유발 |
| **N11** | 편입 문서가 낡았다 | `docs/09_db_configuration_guide.md:638-646`(「새 DB 추가 전체 절차」가 `domain_config.py`의 `DB_DOMAINS` 직접 편집을 안내 — 지금은 레지스트리 파생) · 최신판은 `docs/18:233`에만 | 통합 편입 가이드 없음 |

---

## 2. 설계 원칙

| # | 원칙 | 이 계획에서 |
|---|---|---|
| **R1** | **질의 경로는 구조를 읽기만 한다** | 질의 중 LLM 구조 분석·승인 대기 제거 — 지연·비결정성·HITL 차단을 한꺼번에 없앤다 |
| **R2** | **초안 → 결정적 검증 → 사람 승인 → 적용** | 분석 결과는 초안. 코드가 참조 테이블·컬럼 실존을 검사한 뒤 관리자가 승인해야 런타임이 쓴다 |
| **R3** | **정본 우선순위는 하나** | 수동 프로필(`source: manual`) > 승인본 > 없음. 승인본은 수동 프로필을 덮지 않는다(D-131) |
| **R4** | **변경 감지는 결정적**(LLM 0) | MCP `get_table_schema` 스냅샷(컬럼 이름·타입·NULL·PK·FK)의 diff |
| **R5** | **무거운 일은 잡으로** | 점검·분석은 백그라운드 잡 + 진행 상태 조회. 요청 핸들러에서 DB 루프를 돌지 않는다 |
| **R6** | **읽기 전용 · 감사** | MCP 읽기 도구만(D-003) · 모든 관리자 작업 감사 기록 |
| **R7** | **환경 표기** | 분석·스냅샷에 실행 환경(MCP 소스 연결 대상)을 기록 — 로컬 샌드박스 산출물이 운영 정본으로 승격되지 않게(95 §4.6.2) |
| **R8** (v2) | **등록은 활성화 전에, 질의 밖에서** | 신규 시스템의 스키마 수집·LLM 생성은 관리자 잡이 끝낸다. 질의 경로는 캐시가 없을 때만 **LLM 0**인 스키마 수집으로 폴백한다(G-9) |
| **R9** (v2) | **준비도는 코드가 판정** | 산출물별 등록 상태를 결정적 규칙으로 계산해 목록·설정 탭에 보인다. D-214 ⑤ 활성화 순서 불변식을 사람 기억이 아니라 화면이 지킨다(G-7) |
| **R10** (v2) | **재생성은 사람 자산을 덮지 않는다** | 수동 DB 설명·운영자 유사어·시드 단어(출처 `operator`)는 보존한다. LLM 재생성은 `llm` 출처 항목만 바꾼다(S5) |
| **R11** (v2 · **v4 개정**) | **정본 설정 파일은 앱이 쓰지 않는다 — 프로필만 예외** | `config/db_registry.yaml`·`mcp_server/config.toml`·`.env`는 사람이 PR·배포로 바꾸고 앱은 **붙여 넣을 조각을 내보내기만** 한다(파일 쓰기 0 단언 유지). **`config/db_profiles/{db_id}.yaml`은 G-2 확정으로 관리자 승인 적용이 직접 쓴다**(쓰는 곳은 `StructureStore.apply_profile`·`rollback` 한 곳 · 질의 경로 쓰기 0 · git 커밋은 사람) — D-131 · G-2 · N9 |

---

## 3. 설계

### 3.1 화면 — 관리자 대시보드 새 탭 「DB 구조」

**목록(U1)**

| 열 | 출처 |
|---|---|
| 소스명 · 엔진 · 읽기 전용 | MCP `list_sources` |
| 연결 상태 · 응답 시간 | MCP `health_check`(목록 열 때 병렬 · 실패는 "확인 못 함") |
| 앱 등록 · 활성 | `db_registry.yaml` · `ACTIVE_DB_IDS` — **MCP에만 있음 / 앱에만 있음** 불일치 표시 |
| 구조 정보 | `수동 프로필` · `승인본 vN` · `초안 대기` · `없음` · **활성인데 없음(경고)** |
| 마지막 점검 · 변경 배지 | 스냅샷 시각 · "테이블 +2 · 컬럼 변경 5 · 신규 코드값 3" · 구조 정보 영향 시 **재분석 필요** |
| **캐시 등록**(v2) | `schema:{db_id}:registration` — 스키마·설명·DB 설명·유사어·값 인덱스 단계별 상태·건수(§3.8.1) |
| **준비도**(v2) | §3.8.3 판정 — `필수 n/7 · 권장 n/2` · 미활성 소스는 **「신규 연동」** 버튼 |

**상세(U2~U4)**

1. **변경 점검** 버튼 → 잡 → 스냅샷 diff(추가·삭제 테이블, 추가·삭제·타입 변경 컬럼, PK·FK 변경) + 신규 내용(§3.4 · G-3).
2. **구조 분석 실행** — 범위: 전체 · 변경된 테이블만 · 선택 테이블. 실행 LLM provider를 화면에 표시한다.
3. **초안 보기** — 패턴·쿼리 가이드·샘플 + **결정적 검증 결과**(§3.5) + 현재 적용본(또는 수동 프로필)과의 diff.
4. **승인 / 반려(사유)** — 승인하면 새 버전으로 적용. ~~수동 프로필 DB는 승인 대신 "차이 보고서 내려받기"만~~ **(v4 · G-4 (c)) 수동 프로필 DB도 승인 적용 — 필드 단위 병합 · 필드별 diff · 주석 소실 경고 · 적용 직전 현행 파일 v0 자동 보관**(§3.3).
5. **버전 이력 · 되돌리기** — 버전별 승인자·시각·사유·환경.

**신규 연동(U5·U6 · v2)** — 등록 상태가 빈 소스에서 「신규 연동」을 누르면 §3.8.1의 O-1~O-9를 **단계 표시줄**로 보인다.
단계마다 실행 버튼 · 진행률(잡) · 결과 건수 · 실패 항목 · 예상 LLM 호출 수와 provider를 표시하고, 끝나지 않은 단계가 있으면 O-9(활성화 안내)를 잠그지 않되 미충족 항목을 맨 위에 보인다(G-7).

### 3.2 API — 전부 `require_admin_user` + 감사

| 메서드 · 경로 | 동작 |
|---|---|
| `GET /api/v1/admin/db-structure/sources` | MCP `list_sources` × 레지스트리 × `ACTIVE_DB_IDS` × 구조 정보 상태 대조 목록 |
| `POST /api/v1/admin/db-structure/{source}/check` | 스냅샷 점검 잡 시작 → `job_id` |
| `POST /api/v1/admin/db-structure/{source}/analyze` `{scope, tables?}`(scope = `all`·`changed`·`tables`) | 구조 분석 잡 시작 → 초안 |
| `GET /api/v1/admin/db-structure/jobs/{job_id}` | 진행·결과 |
| `GET /api/v1/admin/db-structure/{source}` | 적용본 · 초안 · 버전 목록 · 마지막 diff |
| `POST /api/v1/admin/db-structure/{source}/drafts/{draft_id}/approve` · `/reject` `{reason}` | 승인(버전 생성·적용) · 반려 |
| `POST /api/v1/admin/db-structure/{source}/versions/{ver}/rollback` | 이전 버전 재적용 |
| `GET /api/v1/admin/db-structure/{source}/diff-report` | 수동 프로필 대비 차이 보고서(YAML) |
| (v2) `POST /api/v1/admin/db-structure/{source}/register` `{steps, tables?}`(steps ⊆ `schema`·`descriptions`·`db_description`·`seeds`·`value_index`) | 등록 잡 시작(O-2·O-4·O-5·O-7) → `job_id` |
| (v2) `GET /api/v1/admin/db-structure/{source}/registration` | 단계별 등록 상태 + 준비도 판정(§3.8.3) |
| (v2) `POST /api/v1/admin/db-structure/{source}/description-drafts/{draft_id}/apply` `{exclude_tables?}` · `/discard` | 컬럼 설명·유사어 초안 적용(병합 규칙 R10) · 폐기 |
| (v2) `PUT /api/v1/admin/db-structure/{source}/db-description` `{text, origin}` | DB 상세 설명 적용(LLM 초안 채택 = `llm` · 직접 입력 = `manual`) |
| (v2) `GET /api/v1/admin/db-structure/{source}/config-snippets` | 레지스트리 항목·프로필 골격 조각(§3.8.4) — **파일을 쓰지 않는다**(R11) |

- **경로 순서**: 고정 경로(`sources`·`jobs`)를 `{source}` 경로보다 **먼저** 선언한다(S3 재발 방지 · 라우트 순서 테스트).
- **잡 실행**: 앱 프로세스 안 `asyncio` 백그라운드 태스크 + Redis 잡 상태 키. **소스당 동시 1개**(Redis 락). 서버 재기동 시 진행 중 잡은 `interrupted`로 표시.
- **(v2) 기존 `/admin/schema-cache/*` 14종은 유지**한다 — 동작 변경은 S3(라우트 순서)·S4(감사)·S5(사람 자산 보존)뿐. 새 탭은 새 API만 쓰고, 두 API는 같은 함수를 부른다(사본 금지).

### 3.3 저장 — 승인본과 버전(G-2 · **v4 확정 개정**)

> **v4(사용자 확정 G-2·G-4)**: 현행본은 `config/db_profiles/{db_id}.yaml`(폴스타 수동 프로필과 같은 경로·형식 — 소비처 수정 0) · 버전 이력·백업은 `.cache/structure/{db_id}/versions/` · Redis는 적용본 캐시. 배포로 config가 덮이면 버전에서 복원(되돌리기) · git 커밋은 사람. 아래 v1~v3 표의 "Redis 적용본 + `v{ver}.yaml` 백업 · `config/db_profiles/`에 쓰지 않음"은 대체됐다.

| 키 · 위치 | 내용 |
|---|---|
| **`config/db_profiles/{db_id}.yaml`** (현행본 · v4) | 승인 적용 결과. 항상 `source: manual`(= "사람이 확정한 구조 정본" — 직접 작성 또는 관리자 승인)이라 `_load_manual_profile`·`_has_manual_profile` 등 소비처가 그대로 읽는다. 로컬 샌드박스에서 적용하면 `environment: local_sandbox` 표기(§3.8.2 · D-214 ⑥) · 머리 주석 1줄(`# plans/104 관리자 승인 적용 vN · 시각 · 승인자 · env`) |
| **`.cache/structure/{db_id}/versions/v{N}.yaml`** (버전 이력 · v4) | `{ver, kind, content, content_sha256, profile, created_at, by, reason, env, draft_id, rolled_back_from, field_diff, comment_lines_dropped}` — `kind` = `baseline`(적용 직전 현행 파일 자동 보관 · 첫 보관이 **v0**) · `external_change`(최신 버전과 현행 파일 sha가 달라 자동 보관 — 배포·수동 편집) · `approved` · `rollback`. `content`는 파일 원문(주석 포함) — **되돌리기는 원문을 바이트 그대로 쓴다**(v0로 되돌리면 주석까지 복원) |
| `schema:{db_id}:structure_meta` (기존 키 · **적용본 캐시**) | 적용 시 프로필에서 `source`를 뺀 dict로 갱신 · `:column_value_index` 삭제. Redis 미연결이어도 파일 적용은 진행(파일이 정본) |
| `schema:{db_id}:structure_drafts` (신규) | 초안 — `{draft_id, meta, merged_profile, field_diff, validation, samples, sample_attempts, scope, tables, groups, provider, env, created_by, created_at, analysis_status, status}` |
| `schema:{db_id}:schema_snapshot` · `:check_result` (신규) | 마지막 스냅샷 + 해시 · 마지막 변경 점검 결과 |
| (v2) `schema:{db_id}:registration` (신규) | 단계별 등록 상태 `{step: {status, count, at, by, env, provider, detail}}` — N2 해소 |
| (v2) `schema:{db_id}:description_drafts` (신규) | 컬럼 설명·유사어 초안 |
| (v2) `schema:db_description_origin` (신규 Hash · field = `db_id`) | DB 상세 설명의 출처 `manual`/`llm`. 설명 값은 기존 `schema:db_descriptions` 그대로 — **라우터 소비처 무변경** |
| (v2) `.cache/structure/{db_id}/descriptions.yaml` | 적용한 설명·유사어(출처 태그 포함) 백업 — G-9 (a) 확정으로 구현. 캐시 미스에서 설명이 비면 여기서 Redis로 복원(LLM 0) |
| `admin:db_structure:job:{job_id}` · `admin:db_structure:lock:{db_id}` | 잡 상태(TTL 7일) · 소스당 동시 1개 락(SET NX EX 3600) — `schema:` 접두가 아니라 `invalidate_all` 대상 밖 |

- `invalidate_all`은 관리자 자산 키(`structure_drafts`·`schema_snapshot`·`check_result`·`registration`·`description_drafts`·`db_description_origin`)를 보존한다. 캐시 무효화는 프로필 파일을 지우지 않는다(S1).
- **질의 경로 보호**: 질의 경로의 `source: auto` 파일 자동 기록(`_save_structure_profile`)은 A-8에서 삭제 — 프로필 쓰기는 관리자 승인·되돌리기 한 곳뿐이다(grep 단언 테스트).
- **우선순위(R3)**: 질의 경로 조회는 ①프로필 파일(`source: manual` — 수동 작성 또는 승인 적용) ②적용본 캐시(Redis → 없으면 최신 버전을 Redis로 복원 · 파일 쓰기 없음) ③없음(G-1 (a) 사유 고지).
- **G-4 (c) 필드 단위 병합**(`src/domain/profile_merge.py` — 실측한 필드 전수 기준): 초안이 갱신하는 것은 `patterns`·`code_values`뿐이고 `query_guide`는 기존 값이 비었을 때만 채운다. 그 밖의 최상위 키(`query_examples`·`allowed_tables`·`alarm_allowed_tables`·`column_synonyms`·`entity_keys` 및 목록 밖 신규 키)는 보존. 패턴은 식별자(eav: entity_table/config_table · hierarchy: table)로 짝지어 LLM 스칼라 키는 초안 값이 비지 않을 때만 갱신, `value_joins`는 `(eav_attribute, entity_column)` 합집합(충돌 시 기존 항목 — 수동 description 보존), 그 밖의 패턴 키(`known_attributes`·`excluded_join_columns`·`direct_join`·`entity_columns`)는 보존 · 기존에만 있는 패턴은 보존 · 초안의 `samples`(실데이터 행)는 프로필에 쓰지 않는다. 승인 시점의 현행 파일로 병합을 다시 계산한다. PyYAML 덤프는 주석을 잃으므로(폴스타 프로필 주석 51~57줄 실측) 승인 화면에 `comment_lines_dropped` 경고를 보이고 원문은 v0에 남는다.

### 3.4 변경 점검 — 스냅샷 diff(결정적 · LLM 0)

1. **스냅샷**: MCP `search_objects`로 테이블 목록 → 테이블마다 `get_table_schema` → 정렬된 `(컬럼명, 타입, NULL, PK)` + FK 목록 → 테이블 해시 · DB 해시.
   엔진 공통 경로라 `information_schema` 전용 지문 SQL의 DB2·MariaDB 문제(§1.2)를 피한다. 순수 diff 함수는 `src/domain/schema_snapshot.py`(I/O 0).
   **(v2)** 이 호출 순서는 `DBHubClient.get_full_schema()`(`dbhub/client.py:441-456`)와 같다 — 스냅샷은 그 결과(`SchemaInfo` — 컬럼 타입·NULL·PK·FK 참조 보유)에서 파생하고, 캐시 등록(§3.8 O-2)과 **수집 1회를 공유**한다.
2. **diff 분류**: 테이블 추가·삭제 · 컬럼 추가·삭제·타입 변경·NULL 변경 · PK·FK 변경.
3. **구조 정보 영향**: 적용본(또는 수동 프로필)의 패턴이 참조하는 테이블·컬럼(`entity_table`·`config_table`·`join_condition`·`value_joins`·`attribute_column`·계층 컬럼)이 diff에 걸리면 **재분석 필요** 배지 — `plans/30` 문제 F 해소.
4. **신규 내용(G-3 (b))**: 구조 정보가 **코드값 목록을 가진 곳만** 결정적 조회로 대조한다 — EAV 속성명(`SELECT DISTINCT <attribute_column> FROM <config_table>` 상한 N) ↔ `known_attributes`, 계층 유형 컬럼 ↔ 알려진 유형.
   SQL은 구조 정보의 식별자로 **코드가 조립**하고(D-035) MCP `execute_sql`(읽기 전용 가드)로 실행한다. 새 값은 "신규 코드값"으로 보이고, 유사어 등록은 `plans/77` 큐로 넘길 수 있다.
   **(v3)** 구조 정보에 코드값 목록이 없는 **비EAV 코드성 컬럼**(표준 코드 ID만 있고 값 목록이 없는 컬럼 — `plans/95` §3.3-③의 9종)의 값 수집은 **G-10**에서 범위를 정한다.
5. **주기**: G-5 기본은 **수동 버튼만**. 런타임 지문 흐름(`get_schema_or_fetch`)은 이 계획에서 바꾸지 않는다(§6 비범위).

### 3.5 구조 분석 — 질의 경로에서 떼어 낸 공용 모듈

- **이동**: `_analyze_db_structure`·`_collect_structure_samples`·`_format_schema_for_analysis`·`_validate_sample_sql`을 `src/schema_cache/structure_analysis.py`(infrastructure)로 옮기고 `schema_analyzer`는 import만 남긴다(사본 금지 · D-053).
- **범위**: 관리자가 고른 테이블 전체(스냅샷 기준). 테이블이 많으면 **FK 연결 묶음 단위**로 나눠 분석하고 패턴을 합친다(묶음 크기 상한 설정 1개).
- **결정적 검증(센서)** — 초안마다:
  ①패턴이 참조하는 테이블·컬럼이 스냅샷에 실존 ②조인 조건 컬럼 타입 호환 ③샘플 SQL이 읽기 전용·LIMIT 규칙 통과 ④샘플 실행 성공·행 존재.
  하나라도 실패하면 **승인 버튼 비활성** + 실패 항목 표시(사람이 틀린 초안을 승인하지 못하게).
- **(v2) "패턴 없음"과 "분석 실패"를 구분한다** — 현행은 둘 다 `None`(§1.1)이다. 이동 후에는 패턴 0건을 **"패턴 없음" 초안**으로 남기고, 승인하면 빈 승인본(`patterns: []`)이 된다. 단순 테이블 DB도 준비도 C6(§3.8.3)을 같은 절차로 채운다.
- **LLM**: 설정된 워커 provider를 그대로 쓴다(운영 FabriX · 로컬 MLX는 D-222 비과금). 화면에 provider를 표시한다.
  에이전트가 개발 중 실제 분석을 돌릴 때 과금 provider면 **D-127 건별 승인** 대상이다.

### 3.6 질의 경로 변경 — 구조 승인 HITL 제거

| 대상 | 변경 |
|---|---|
| `schema_analyzer.py:1124-1179` | ③LLM 분석 분기 **삭제** — 수동 프로필·적용본이 없으면 구조 정보 없이 진행하고 사유를 `dependency_notes`에 남긴다(G-1) |
| `structure_approval_gate` | 노드·라우팅(`graph.py:500-502,613-628`)·`interrupt_before` 항목(`:697-701`)·파일 **삭제**(D-161 ① — 같은 결정 안에서 삭제) |
| `enable_structure_approval` | 설정 필드(`config.py:1310`) · 설정 카탈로그(`settings_catalog.py:260`) · 도움말(`general.yaml:162-190`) · `.env.example:533` **삭제**. D-135 ① 주의 — 승인 대기 중이던 기존 스레드는 다음 턴에 일반 질의로 처리됨을 릴리스 노트에 |
| `_run_single_db_pipeline`(`subagents.py:572`) | 매 질의 분석·폐기 경로가 ③ 삭제로 함께 사라짐 |
| `sequential_runner.py:44-46` | 구조 승인 조건 제거(SQL 승인 조건만 남음 — 러너 자체는 `plans/103` P4-2에서 삭제) |
| 활성화 안내 | 관리자 목록에서 **"활성인데 구조 정보 없음"** 경고 · `plans/95` W-12 같은 활성화 절차의 확인 항목에 "구조 정보 있음(수동 또는 승인본)" 추가 |
| 테스트 | `tests/test_graph_routing_gaps.py` · `tests/test_structure_analysis.py` · `tests/test_plan32_manual_profile.py` · `tests/test_orchestration/test_sequential_runner.py` 골든 · `tests/test_multiturn/test_approval_gate.py`(구조 승인 부분) 갱신 |

### 3.7 안전 선행 수정(S1~S5)

| # | 수정 | 검증 |
|---|---|---|
| S1 | `invalidate`는 **프로필 파일을 지우지 않는다**(`_delete_db_profile` 호출 제거 · 함수 삭제). 구조 정보 삭제는 새 API의 되돌리기·폐기로만 | `source: manual` 프로필이 있는 상태에서 무효화 → 파일 잔존 테스트 |
| S2 | 채팅 캐시 관리의 생성·무효화는 **관리자 역할만**(사용자 역할이면 관리자 페이지 안내) | 일반 사용자 토큰으로 무효화 요청 → 거절·안내 |
| S3 | 스키마 캐시 라우트 순서 교정 | `GET /admin/schema-cache/db-descriptions`가 목록 응답 |
| S4 | 스키마 캐시 변경 작업 감사(`CACHE_OPERATION`) · 새 구조 API 감사(`ADMIN_ACTION`) — `_log_settings_event` 패턴 | 감사 로그 행 생성 · 실패 시 응답에 표시 |
| **S5** (v2) | **재생성이 사람 자산을 덮지 않는다**(N5) — ①DB 설명 저장에 출처(`manual`/`llm`)를 함께 남기고, LLM 생성 경로(API `schema_cache.py:534-536` · 채팅 · CLI)는 `manual`을 건너뛴다(docstring `:444`의 약속을 코드로) ②LLM 유사어 저장은 같은 컬럼의 **`llm`이 아닌 출처 단어**(`operator` — 시드 로드 기본 `source_tag`도 `operator` `synonym_loader.py:557,606`)를 유지하고 `llm` 단어만 교체한다(`redis_cache.py:598-626` 치환 → 병합) | 수동 DB 설명 → LLM 재생성 → 수동 값 유지 · 운영자 유사어 등록 → LLM 재생성 → 운영자 단어 유지 · 기존 `tests/test_schema_cache/test_cache_manager_synonyms.py`·`test_redis_cache_synonyms.py` 무회귀 |

### 3.8 (v2) 신규 시스템 연동 — 발견에서 활성화까지

**전제**: MCP 소스 추가(`config.toml` `[[sources]]` · `mcp_server/.env` 연결 문자열 · MCP 재기동)는 이 흐름 **앞**에 사람이 한다(`docs/18_known_mistakes.md:233` 0~3단계).
앱은 연결 문자열을 다루지 않는다 — 비밀 경계이자 D-003 MCP readonly 경계다. 흐름은 `list_sources`에 소스가 보이는 순간부터 시작한다.

#### 3.8.1 흐름

| 단계 | 이름 | 하는 일 | LLM | 재사용하는 기존 코드 | 완료 판정 |
|---|---|---|---|---|---|
| **O-1** | 발견·연결 확인 | 목록의 `MCP에만 있음`(G-6 (a)) 또는 `등록·미활성` 소스에서 「신규 연동」 시작. `health_check` · MCP `type` ↔ 레지스트리 `engine` 일치 · 엔진이 앱 지원 범위(PG·DB2·MariaDB)인지 · **(v3) 엔진 버전·방언에 영향 주는 서버 변수 표시**(엔진별 코드 조립 조회 → MCP `execute_sql` 읽기 전용 · MariaDB는 `@@version`·`@@sql_mode`·`@@lower_case_table_names`·`@@collation_server` — `plans/95` G-13 대체) | 0 | `DBHubClient.health_check`(`dbhub/client.py:183-204`) · `list_sources`(A-2) | 연결 성공 · 엔진 일치(미등록이면 "레지스트리 등록 필요" 표시만) |
| **O-2** | 스키마 수집 · 캐시 등록 | `get_full_schema()` **1회**로 ①스키마 캐시(`:meta`·`:tables`·`:relationships`) 저장 ②§3.4 스냅샷 기준선 저장. 관계는 테이블별 FK 응답에서 파생(§3.8.2) | 0 | `get_full_schema`(`dbhub/client.py:441-456`) · `schema_to_dict` · `save_schema` · `cleanup_stale_entries` | 캐시 테이블 수 = 스냅샷 테이블 수 |
| **O-3** | 범위 선정 | 설명·구조 분석 대상 테이블 선택 — 전체 · 이름 패턴 · FK 묶음 · 수동. **예상 LLM 호출 수**(설명 = 테이블 수 · 구조 = 묶음 수)와 실행 provider를 먼저 보인다 | 0 | §3.5 묶음 분할 | 관리자 확정 |
| **O-4** | 컬럼 설명·유사어 초안 | 범위 내 테이블만 생성 → **초안**(G-8). 실패 테이블 목록 · 실패분만 재실행 | O | `DescriptionGenerator.generate_for_table`(`description_generator.py:108-172`) | 실패 0 또는 실패 테이블 명시 수용 → 적용 |
| **O-5** | DB 설명 | 라우팅 설명 두 겹을 구분해 보인다 — ①레지스트리 `description`(정본 · 사람 · git · **빈 값이면 라우터가 빈 줄을 렌더** `semantic_router.py:921`) ②Redis 상세 설명(LLM 초안 → 적용). 초안은 ①의 후보로 조각(§3.8.4)에도 넣는다 | O | `generate_db_description`(`description_generator.py:38-106`) | ① 비어 있지 않음 · ② 적용 또는 생략 확인 |
| **O-6** | 구조 분석 | §3.5 그대로 — 수동 프로필 DB는 차이 보고서(G-4). 패턴이 없으면 "패턴 없음" 승인본(§3.5 v2) | O | §3.5 | 승인본 존재(빈 패턴 포함) |
| **O-7** | 부속 등록 | ①시드 파일(`config/synonym_seeds/{db_id}.yaml`)이 배포돼 있으면 로드(병합 · R10) ②적용본이 EAV면 값 인덱스 빌드 — `SYNONYM_VALUE_RETRIEVAL`이 off면 "만들어 두지만 질의에서 쓰이지 않음"을 표시 | 0 | `SynonymLoader.load_seed_yaml`(`synonym_loader.py:548-670`) · `load_or_build_value_index`(`value_index.py:158-195`) | 로드·빌드 건수(시드 파일 없으면 "해당 없음") |
| **O-8** | 설정 조각 내보내기 | 사람이 반영할 조각(§3.8.4) + 반영 뒤 필요한 재기동 안내(N7) | 0 | — | 내려받기 |
| **O-9** | 준비도 판정 · 활성화 | §3.8.3 판정 → **기존 설정 탭**에서 `ACTIVE_DB_IDS` 편집·리로드(앱이 대신 쓰지 않는다 — R11). 미충족 처리는 G-7. 리로드 뒤 스모크: 라우터 활성 DB 목록에 포함 · 사다리 확정 1줄이 활성화 전후 같음(`plans/95` W-12 verify와 같은 판정) | 0 | 설정 리로드(`admin.py:794-970`) · `record_ladder_resolution`(`src/observability/ladder.py:131`) | 필수 항목 전부 ✅ · 스모크 통과 |

- **순서**: O-1 → O-2는 필수 선행. O-4·O-5·O-6은 O-2 뒤 **서로 독립**(한 잡에서 이어 돌리거나 따로 실행). O-9는 마지막(D-214 ⑤).
- **등록 상태 기록**: 단계마다 `{status, count, at, by, env, provider}`를 `schema:{db_id}:registration`에 남기고 감사한다(S4) — N2 해소.
- **운영 중인 DB에도 같은 화면**을 쓴다(재등록·설명 재생성). 신규 전용 기능이 아니라, 등록 상태가 빈 DB의 시작점이 O-1일 뿐이다.

#### 3.8.2 캐시 등록 규칙

| 규칙 | 내용 | 근거 |
|---|---|---|
| **수집 1회 공유** | O-2의 `get_full_schema()` 결과에서 캐시(`schema_to_dict`)와 스냅샷(§3.4)을 함께 만든다 — 테이블당 MCP 호출이 두 번 일어나지 않는다 | §6 위험 「MCP 호출 수」 |
| **지문은 지연 경로와 같게** | `save_schema(fingerprint=None)` — 지연 3차 조회(`cache_manager.py:1290-1320`)와 **같은 저장 모양**이다. `refresh_cache`의 지문 SQL을 거치지 않으므로 DB2 신규 시스템도 등록된다(N4). 런타임 지문 비교 흐름은 바꾸지 않는다(§6 비범위) | "첫 질의가 했을 등록"을 미리 하는 것이지 새 캐시 형식이 아니다 |
| **관계 파생 엔진 공통화** | `get_full_schema`의 관계를 MCP 테이블별 FK 응답에서 만든다. PG는 기존 SQL 결과와 **동등성을 실측**(로컬 `polestar`)한 뒤 — 같으면 일원화, 다르면 SQL 실패·빈 결과일 때만 파생으로 폴백 | N3 · 기존 PG 동작 보존 |
| **설명·유사어는 초안에서 적용으로**(G-8 (a)) | 초안을 테이블 단위로 보고 일괄 적용한다(제외 테이블 선택). 적용 = `save_descriptions` + 유사어 **병합**(R10 · S5) + `sync_global_synonyms`(`cache_manager.py:1172-1204`) — API `generate`(`schema_cache.py:672-679`)·CLI가 빠뜨리던 전역 동기화(호출처는 채팅 `cache_management.py:428,473`뿐)를 적용 경로에서 한 번 부른다 | 적용 경로 단일화 |
| **대형 DB** | 예상 호출 수를 먼저 보이고 실행한다. 동시성 상한(설정 1개) · 테이블 단위 재개(실패분만) · 수동 프로필의 `allowed_tables`가 있으면 범위 기본값으로 쓴다 | N1 · `polestar_cm_gp` 394테이블(파일 캐시 실측) |
| **LLM** | §3.5와 같다 — 워커 provider · 화면 표시 · 에이전트 개발 실행은 과금 provider면 D-127 건별 승인 | D-127 · D-222 |
| **환경 표기**(R7) | 초안·적용·등록 상태에 `env` = 기록 시점 `DBHUB_SERVER_URL`을 남긴다. 조각(§3.8.4)은 로컬 샌드박스 산출물로 만들었으면 머리말 `# LOCAL SANDBOX — 운영 정본 재료로 쓰지 말 것`을 단다 | `plans/95` §4.6.2 파생 금지 · D-214 ⑥ |

#### 3.8.3 준비도 판정(결정적 · LLM 0)

| # | 항목 | 판정 규칙 | 등급 |
|---|---|---|---|
| C1 | MCP 소스 | `list_sources`에 있음 + `health_check` 성공 | 필수 |
| C2 | 레지스트리 등록 | **실행 중 프로세스의** 레지스트리에 `db_id` · `enabled` · `engine` = MCP `type`. 없으면 "레지스트리 반영 후 앱 재기동 필요"(N7) | 필수 |
| C3 | 라우팅 설명 | 레지스트리 `description` 비어 있지 않음 | 필수 |
| C4 | 스키마 한정 | 엔진이 DB2면 `db_schema` 설정(대문자 한정 규칙) · 그 외 엔진은 값 표시만 | DB2만 필수 |
| C5 | 스키마 캐시 | `:meta` 존재 · 테이블 수 = 마지막 스냅샷 테이블 수 | 필수 |
| C6 | 구조 정보 | 수동 프로필 또는 승인본(빈 패턴 포함) — D-214 ⑤ | 필수 |
| C7 | 환경 | C5 캐시와 C6 승인본(수동 프로필이면 제외)의 `env` = 현재 `DBHUB_SERVER_URL`(R7) | 필수 |
| C8 | 컬럼 설명 | 범위 내 테이블 적용률 100%, 또는 제외 테이블 명시 | 권장 |
| C9 | 유사어 | 시드 로드 건수 또는 LLM 유사어 적용 건수 > 0 | 권장 |
| C10 | 권한·존 | `AUTH_DEFAULT_ALLOWED_DB_IDS`(`config.py:528`)·사용자 허용 목록 포함 여부 · 존 미배정이면 잔여 그룹 실행 안내(D-214 ④) | 정보 |

- 결과는 목록 **준비도 열**(`필수 n/7 · 권장 n/2`)과 O-9 화면, 그리고 **설정 탭의 `ACTIVE_DB_IDS` 편집 시점**(G-7)에 보인다.
- 판정 함수는 순수 함수(입력 = 소스 목록·레지스트리·캐시 메타·등록 상태)로 두어 단위 테스트한다 — 공용 계층에 스키마 리터럴 0(`overfit_check`).

#### 3.8.4 설정 조각 내보내기(R11 — 레지스트리·`.env`·`mcp_server/`는 쓰지 않는다)

| 조각 | 내용 |
|---|---|
| `config/db_registry.yaml` 항목 | `db_id` · `display_name` · `engine`(MCP `type`) · `db_schema`(MCP 테이블 목록의 스키마 접두에서 후보 — 목록 응답에 스키마가 실리는지는 코드 읽기 추정 · B-5에서 엔진별 실측) · `description`(O-5 초안 + `# 사람 검토 필요`) · `zone`·`aliases` 빈칸 |
| ~~`config/db_profiles/{db_id}.yaml` 골격~~ | **(v4 삭제)** G-2 확정으로 승인 적용이 프로필을 직접 쓰므로 골격 조각은 만들지 않는다 |

- MCP 조각(`config.toml`·`.env`)은 만들지 않는다 — 흐름의 전제이고 연결 문자열은 비밀이다.
- 레지스트리 `env_connection_key`는 **읽는 코드가 0건**이라(D-214 ① · `docs/18:233`) 조각에 넣지 않는다.
- 조각 머리말에 반영 뒤 필요한 동작(앱 재기동 · 준비도 재판정)을 적는다.

#### 3.8.5 질의 경로의 지연 LLM 생성 처분(G-9)

| 현행(v3까지) | **G-9 (a) 확정 · 구현(v4)** |
|---|---|
| 캐시 미스 → MCP 전체 수집 → 저장 → **설명이 비었으면 테이블당 순차 LLM 생성**(`cache_manager.py:1325-1355` · 운영 `.env:174` `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS=true`) | 캐시 미스 → MCP 전체 수집 → 저장까지만(**LLM 0** — R8). 설명이 없으면 `dependency_notes`에 "컬럼 설명 미등록 — 관리자 등록 필요". 설정 필드(`config.py:644`)·카탈로그(`settings_catalog.py:238`)·도움말(`config/settings_help/infrastructure.yaml:366-386` · 연관 참조 `:364`)·`.env.example:222` 삭제(D-161 ① 같은 결정 안 삭제). 운영 `.env`의 남은 줄은 `extra="ignore"`(`config.py:647`)라 무해 — 정리 안내만 |

- **운영 영향 범위**: 설명이 이미 있는 DB는 이 분기에 닿지 않는다(비었을 때만 발동). 닿는 경우는 **신규 DB 첫 질의**와 **Redis 유실 뒤**뿐이며, 앞은 O-4가, 뒤는 적용본 백업 복원(§3.3 v2)이 대신한다.
- **운영 반영 전 체크항목(G-9 확정 조건 · 사용자 지시 2026-09-17)**: 운영 Redis의 DB별 `schema:{db_id}:descriptions` 건수(폴스타 3종 + 활성 DB 전부)를 실측하고, 0이거나 테이블 대비 부족한 DB는 **배포 전에 관리자 「DB 구조」 탭 등록 흐름(O-4 설명 초안 → 적용)으로 선채움**한다. 이 맥북에서는 운영 Redis를 잴 수 없어 미실측(§11.2).
- **(v4) 사유 노출**: 백업 복원 뒤에도 설명이 비었으면 구조 정보 없음과 같이 **질의마다**(한 응답 안에서는 DB당 1건) `descriptions_missing` 노트를 응답 본문 `[안내]`로 싣는다 — 캐시가 데워진 뒤 조용히 품질이 떨어지지 않게(침묵 강등 금지).
- **(v4) 운영 `.env` 잔존 줄**: `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS=true`는 `extra="ignore"`라 동작 무해하나 `.env` 커버리지 테스트 2종(`test_settings_catalog.py::test_t1_catalog_covers_all_env_file_keys` · `test_settings_catalog_sections.py::test_env_files_fully_covered_by_catalog`)이 실패한다 — 로컬·운영 `.env`에서 그 줄을 지운다.

#### 3.8.6 (v3) `plans/95`에서 이관된 조사 항목 — 자산관리 DB가 첫 적용 대상

사용자 지시(2026-09-17): *"db 관련 정보는 104번 계획에서 조사하여 자동으로 스키마, 캐시 등을 생성하는 기능을 구현할 계획이다. 이에 맞게 처리하라."*

| 95 항목 | 이 계획에서 받는 곳 | 비고 |
|---|---|---|
| G-4 물리 컬럼·테이블 식별자 · 테이블명 대소문자 | O-2 스키마 수집 · §3.4 스냅샷 | 사용자에게 `SHOW CREATE TABLE`을 요청하지 않는다 |
| G-2 `db_schema` | §3.8.4 레지스트리 조각 후보 · C4 | 운영 연결 문자열(95 W-4)은 여전히 사람 — §3.8 전제 |
| G-13 서버 변수 | O-1 표시(v3) | 방언 함정(이중 파이프 결합이 OR로 해석 · 큰따옴표 식별자가 문자열 · 테이블명 대소문자)의 판단 근거를 화면에 남긴다 |
| G-5 코드값 목록 | G-10 | 기본 (a) 관리자가 지정한 컬럼만 |
| W-6 구조 정본 · W-7 지식·시드 | O-4~O-8 | 95 §3.3 결정적 규칙 5건 · 전사본 `testdata/itam/schema.yaml`은 **초안 검토 때 대조 자료**(런타임 입력 여부는 B-4에서 판단) |
| 양식 LLM 매핑 자동 등록(D-228 ④) | G-11 · B-8 | |
| D-228 가드 | B-8 | 승인본이 생기면 조건 확장 → B-6에서 제거 |

- **로컬·운영 구분**(R7): 로컬 샌드박스(`itam_mariadb` 3307)에서 돌린 흐름은 A-9 검증이고, 운영 정본은 운영 MCP 소스 대상 승인본이다. 조각 머리말 `LOCAL SANDBOX` 규칙 그대로.

#### 3.8.7 (v6) 사용자 직접 실측 절차 — 로컬 mlx·MCP로 A-9 전 과정 돌리기

> A-9의 **자동 검증은 목**으로 끝냈다(`tests/test_schema_cache/test_plan104_a9_mock_rig.py` 10건 — 목 MCP·목 LLM·인메모리 Redis·tmp 프로필). 아래는 실제 서버로 확인할 때 쓰는 절차다. **(v9) 2026-09-23 사용자 지시("에이전트를 이용하여 실측해봐라")로 에이전트가 이 절차를 실행했고**, 그때 드러난 절차 오류 4건(D1~D4)을 아래에 고쳐 두었다 — 결과는 §11.6.

**0. 사전 확인(과금·공유 자원)**
```bash
grep -nE '^(LLM_PROVIDER|ORCHESTRATOR_PROVIDER|DB_BACKEND|DBHUB_SERVER_URL|ACTIVE_DB_IDS)=' .env
```
두 평면이 모두 `mlx`(또는 비과금 provider)인지 본다 — 하나라도 `gemini`면 멈춘다(D-127·D-222). MCP 9099와 mlx 8080은 다른 세션이 함께 쓴다(재기동·중지 금지 — 필요하면 소유 세션에 통지).

**1. 격리 환경으로 서버 기동**(공유 Redis db0·기본 캐시 디렉터리를 건드리지 않는다)
```bash
REDIS_DB=15 SCHEMA_CACHE_CACHE_DIR=.cache/schema_rig API_HOST=127.0.0.1 API_PORT=18140 \
  .venv/bin/python -c "import uvicorn; uvicorn.run('src.api.server:app', host='127.0.0.1', port=18140, reload=False)"
```
`REDIS_DB=15`가 스키마 캐시·잡·적용본 캐시를 전부 격리한다. 버전 이력은 `.cache/structure/`(gitignore)에 쌓인다.
- **(v9 D1 정정)** 초판의 `SERVER_PORT`·`SCHEMA_CACHE_DIR`는 **없는 변수라 조용히 무시된다**(기본 포트 8000·기본 캐시 디렉터리로 떠서 공유 캐시 격리가 깨진다). 실제 변수는 `API_PORT`(`ServerConfig` env_prefix `API_` · `src/config.py:467`)와 `SCHEMA_CACHE_CACHE_DIR`(`src/config.py:664`)다. 기동 로그의 `Redis 연결 성공: localhost:6380/15`와 포트로 격리가 먹었는지 확인한다.
- **(v9 D2 정정)** `python -m src.main --server`는 `reload=True`로 뜬다(`src/main.py:106`). 여러 세션이 파일을 동시에 고치는 작업 트리에서는 측정 중 서버가 재기동돼 잡·in-memory 상태가 사라지므로 위처럼 **reload 없이** 띄운다.
- Redis는 이 맥에서 도커 `collectorinfra-redis`의 **6380**이다(`.env` `REDIS_PORT`). `redis-cli`에는 항상 `-p 6380`을 붙인다.

**2. 관리자 화면**: `http://127.0.0.1:18140/admin` → 「DB 구조」 탭.
1. 목록에서 대상 소스(예: `itam`)의 연결·엔진·등록·활성·구조 상태를 확인한다.
2. **신규 연동**: O-1 연결·서버 변수 → O-2 스키마 수집·캐시 등록 → O-3 범위·예상 LLM 호출 수 확인 → O-4 설명 초안 → 검토 후 적용 → O-5 DB 설명 → O-6 구조 분석 → 초안의 **검증 4종**과 **필드별 diff**를 보고 승인.
3. 승인 직후 `config/db_profiles/{db_id}.yaml`과 `.cache/structure/{db_id}/versions/`를 확인한다(로컬이면 프로필에 `environment: local_sandbox` 표기).
4. 「되돌리기」로 복원이 원문 그대로인지 한 번 확인한다. **(v9 D4 정정)** v0(`baseline`)는 승인 직전에 **현행 프로필 파일이 있을 때만** 생긴다(`src/schema_cache/structure_store.py:499`). 신규 DB(itam 등)는 v0가 없어 `versions/0/rollback`이 404이고, "구조 없음"으로 되돌리는 API도 없다(§11.6 잔여 R-4) — 신규 DB에서는 승인 버전(v1)으로 되돌려 새 버전(v2 `rollback`)이 생기고 현행 파일이 v1 원문과 바이트 동일한지 본다. 기존 프로필이 있는 DB(폴스타 로컬 `polestar`)면 v0 복원을 확인할 수 있다.

**3. 질의 확인**(같은 서버에서)
```bash
curl -s -X POST http://127.0.0.1:18140/api/v1/query -H 'Content-Type: application/json'   -d '{"query":"<대상 DB를 향한 질의>"}' | python -m json.tool | head -40
```
- 서버 로그에 `Redis/파일 캐시 히트`가 찍히고 **스키마 전체 수집·컬럼 설명 LLM 생성이 0**이어야 한다.
- 구조 정보·컬럼 설명이 없는 DB면 응답 끝에 `[안내]` 문구가 붙는지 본다.

**4. 결과 처리**
- **승인으로 생긴 `config/db_profiles/{db_id}.yaml`은 지우지 않고 남긴다**(사용자 결정 2026-09-17). 같은 저장소를 쓰는 **병행 세션에 통지**한다 — 그 DB의 질의 경로가 즉시 이 파일을 읽는다.
- 커밋은 사람이 판단한다. 로컬 샌드박스 표기(`environment: local_sandbox`)가 붙은 프로필은 **커밋하면 품질 게이트 테스트가 실패한다**(`test_plan104_local_sandbox_profile_gate.py` — D-214 ⑥ 준수 장치).
- 원상복구: 임시 서버 종료 · `redis-cli -p 6380 -n 15 flushdb`(**(v9 D3 정정)** 초판은 `-p 6380`이 빠져 기본 6379로 붙는다 — 2026-09-23 이 맥에서는 6379가 떠 있지 않아 연결 거부로 끝나 **격리 db15가 비워지지 않고**, 6379에 다른 Redis가 뜬 환경이면 엉뚱한 db15를 비운다) · `.cache/schema_rig` 삭제. 버전 이력을 지우려면 `.cache/structure/{db_id}/` 삭제(적용본 캐시 복원 원천이 사라지므로 프로필 파일이 있는지 먼저 확인).

---

## 4. 작업 분해

| WU | 트랙 | 작업 | 선행 | verify |
|---|---|---|---|---|
| **A-0** | — | G-1~G-9 확정 | — | §7 기록 |
| **A-1** | 안전 | S1~S5 | — | §3.7 테스트 · 기존 스키마 캐시 테스트 전건 |
| **A-2** | 목록 | `DBHubClient.list_sources` · 대조 서비스 · `GET …/sources` | A-1 | MCP만 있음·앱만 있음·활성인데 구조 없음 3종 표시 · MCP 미가용 시 "확인 못 함" |
| **A-3** | 점검 | `src/domain/schema_snapshot.py`(스냅샷·diff·구조 영향 — 순수 함수) · 점검 잡 · `…/check`·`jobs` | A-2 | 컬럼 이름 교체(개수 동일) 감지 · 타입 변경 감지 · PG·DB2·MariaDB 스냅샷 형식 정규화 · 구조 영향 배지 |
| **A-4** | 점검 | 신규 코드값 대조(G-3 (b)) — 코드 조립 SQL · 상한 | A-3 · G-3 | EAV 속성 신규값 표시 · 조립 SQL이 `sql_guard` 통과 |
| **A-5** | 분석 | `structure_analysis.py` 이동 · 범위(묶음) · 결정적 검증 4종 · 분석 잡 · 초안 저장 | A-3 | 이동 후 기존 구조 분석 테스트 전건 · 검증 실패 초안은 승인 불가 |
| **A-6** | 승인 | 버전·적용·되돌리기·파일 백업·복원 · 우선순위(R3) · 감사 | A-5 · G-2·G-4 | 승인 → 질의 경로가 새 버전 사용 · 되돌리기 · Redis 비운 뒤 파일에서 복원 · 수동 프로필 DB 승인 차단 |
| **A-7** | 화면 | `dashboard.html` 탭 · `admin/db-structure.js`(기존 `apiRequest` 패턴) · 목록·상세·diff·초안·승인·이력 · **(v2) 신규 연동 단계 표시줄·등록 상태·준비도 열** | A-2~A-6 · B-1~B-5 | 로그인 안 한 첫 방문 → 로그인 이동 · 비관리자 403 · 잡 진행 표시 · 미충족 준비도 항목이 맨 위 |
| **A-8** | 질의 경로 | §3.6 HITL 제거 · 구조 정보 없음 고지 · 설정·카탈로그·도움말 삭제 | A-6 · G-1 | 구조 정보 없는 DB 질의가 멈추지 않고 사유 노출 · `structure_approval_gate` 참조 0 · 설정 카탈로그 항목 수 갱신 테스트 |
| **A-9** | 검증 | 로컬 리그 — 자산관리 샌드박스(MariaDB 3307 · 구조 정보 없음)로 목록→점검→분석→승인→질의 사용 전 과정 · 폴스타 로컬 PG(수동 프로필)로 차이 보고서 · **(v2) 같은 샌드박스로 O-1~O-9**(활성화는 로컬만 · 산출물·조각 커밋 금지 — `plans/95` §4.6.2) | A-8 · B-7 | 전 과정 통과 · 로컬 산출물에 환경 표기(R7) · 조각 머리말 `LOCAL SANDBOX` · 활성화 직후 첫 질의가 캐시 히트(지연 수집·LLM 생성 0) |
| **A-10** | 문서 | D-227 등재(D-020 개정 · D-011 부분 개정) · `docs/05_system_architecture.md` · 운영 가이드(관리자 절차) · **(v2) `docs/09_db_configuration_guide.md:638-646` 「새 DB 추가 전체 절차」를 관리자 흐름 기준으로 교체(N11)** · INDEX | 전건 | 링크·번호 실존 |
| **B-1** (v2) | 등록 상태 | `schema:{db_id}:registration` · 준비도 순수 함수(§3.8.3) · `GET …/registration` · 기존 `/status`의 설명·유사어 건수 채움(N2) | A-2 | C1~C10 항목별 충족·미충족 단위 테스트 · 레지스트리 미반영(재기동 전) 표시 · `/status` 건수 = Redis 실제 건수 |
| **B-2** (v2) | 등록 | 관계 파생 엔진 공통화(§3.8.2 · N3) | — | 로컬 PG로 기존 SQL 결과와 동등성 실측 기록 · MariaDB 샌드박스(`RUN_DOCKER_IT=1`) 관계 비어 있지 않음 · DB2 목 응답에서 관계 생성 |
| **B-3** (v2) | 등록 | 등록 잡 O-2·O-7 — 수집 1회 공유(캐시 + 스냅샷) · 시드 로드 · 값 인덱스 · `…/register`. 클라이언트는 `get_db_client`(dbhub 모드에서 활성 목록을 검사하지 않음 `src/db/__init__.py:44-55` — 레지스트리 클라이언트는 활성만 허용 `src/routing/db_registry.py:125`) | A-3 · B-1 · B-2 | MCP `get_table_schema` 호출 수 = 테이블 수(1회) · 미활성 소스 등록 성공 · 지문 SQL 미호출(DB2 목 소스 등록 성공) · 등록 뒤 질의 경로 캐시 히트 |
| **B-4** (v2) | 등록 | O-3~O-5 — 범위·예상 호출 수·동시성·재개 · 설명·유사어 초안/적용/폐기 · DB 설명 적용(출처) · 전역 유사어 동기화 | B-3 · A-1(S5) · G-8 | 목 LLM: 예상 호출 수 = 실제 호출 수 · 실패 테이블만 재실행 · 제외 테이블 미적용 · 적용 뒤 `operator` 단어·`manual` DB 설명 유지 |
| **B-5** (v2) | 등록 | 설정 조각 내보내기(O-8 · §3.8.4) | A-6 · B-4 | 레지스트리 조각이 `load_registry`로 파싱되고 가상 DB 편입 리허설(`tests/test_semantic_routing/test_registry_config.py:141` 방식) 통과 · 로컬 산출물 머리말 · **파일 쓰기 0 단언** |
| **B-6** (v2) | 질의 경로 | §3.8.5 지연 LLM 설명 생성 처분 | B-4 · G-9 · 운영 Redis 설명 건수 실측 | 캐시 미스 질의의 LLM 호출 0 · 사유 노출 · 설정 카탈로그 항목 수 갱신 테스트 · Redis 비운 뒤 백업에서 설명 복원 |
| **B-7** (v2) | 활성화 | 설정 탭 `ACTIVE_DB_IDS` 편집 시 준비도 판정 연결(G-7) · 감사 | B-1 · G-7 | 필수 미충족 DB 추가 시 **(v4 G-7 (a)) 응답 `readiness_warnings` 경고만 · 저장 진행** · 감사 extra 기록 · 충족 DB만 추가하면 종전과 동일(추가 입력 0) |
| **B-8** (v3) | 질의 경로 | D-228 가드 후속 + 양식 LLM 매핑 자동 등록 처분(G-11) — ①A-6 적용 후 가드 조건을 「수동 프로필 **또는 승인본**」으로 확장 ②B-6(지연 LLM 생성 제거)과 같은 결정 안에서 `get_schema_or_fetch`의 가드 분기를 제거(D-161 ①) ③G-11 답에 따라 `document/field_mapper.py` 자동 등록 경로 처리(**v4 G-11 (b)**: 구조 정보 없는 DB는 DB별 유사어 캐시에 `llm` 출처로 등록 · EAV 매핑은 등록 0 + 로그) | A-6 · B-6 · G-11 | 승인본 DB는 전역 동기화 · 구조 정보 없는 DB는 전역 쓰기 0 + DB별 등록 · B-6 뒤 가드 코드 참조 0 · `tests/test_schema_cache/test_global_synonym_guard.py` 갱신 |

**순서 불변식**
- **A-1(안전 수정)이 맨 먼저**다 — 새 기능이 무효화·프로필 파일을 건드리기 전에 수동 프로필 삭제 경로를 막는다.
- **A-8(HITL 제거)은 A-6(승인본 적용) 뒤**다 — 구조 정보를 채울 새 길이 생긴 뒤에 옛 길을 닫는다.
- ~~**자산관리 운영 활성화(`plans/95` W-12)는 A-8 뒤 또는 수동 프로필(95 W-6) 확보 뒤**다.~~ **(v3) 자산관리 운영 활성화는 준비도 필수 항목(C1~C7) 충족 뒤**다 — 95 W-6(수동 프로필)은 O-6 승인본으로 대체됐다(§3.8.6). 로컬 활성화(95 v11)는 검증용으로 유지.
- 실 LLM 분석 실행은 실행 직전 provider를 확인하고, 과금 provider면 D-127 건별 승인 후에만.
- **(v2) A-1(S5)은 B-4보다 먼저**다 — 적용 경로가 생기기 전에 재생성의 사람 자산 덮어쓰기를 막는다.
- **(v2) B-6(질의 경로 지연 LLM 생성 처분)은 B-4 뒤**다 — A-8과 같은 논리(새 길 → 옛 길 닫기).
- **(v2) B-3은 A-3 뒤**다 — 스냅샷 함수를 공유한다(사본 금지).
- **(v2) 신규 DB 운영 활성화는 준비도 필수 항목(C1~C7) 충족 뒤**다 — D-214 ⑤를 화면 판정으로 대체한다.

---

## 5. 성공 기준

1. **목록(U1)** — 관리자 탭이 MCP 소스 전부를 보이고, MCP·레지스트리·`ACTIVE_DB_IDS` 불일치와 "활성인데 구조 정보 없음"을 표시한다.
2. **변경 점검(U2)** — 개수가 같은 컬럼 이름 교체·타입 변경·테이블 추가/삭제를 감지하고, 구조 정보가 참조하는 대상이 바뀌면 재분석 필요를 표시한다. DB2·PG·MariaDB 모두 동작. LLM 호출 0.
3. **분석(U3)** — 관리자가 고른 범위 전체를 분석해 초안을 만들고, 결정적 검증 4종 결과가 함께 보인다. 검증 실패 초안은 승인할 수 없다.
4. **적용(U4)** — 승인본만 질의 경로가 쓰고, 버전·되돌리기·파일 복원이 동작한다. 수동 프로필은 덮이지 않는다.
5. **질의 경로** — 구조 승인 HITL 0 · 질의 중 LLM 구조 분석 0 · 구조 정보 없는 DB는 멈추지 않고 사유 노출. `plans/102` X-T8의 구조 승인 조건이 사라진다.
6. **안전** — 무효화 후 수동 프로필 파일 잔존 · 비관리자 채팅 무효화 거절 · 라우트 가림 0 · 모든 관리자 구조·캐시 작업 감사 기록.
7. **게이트** — `arch_check --ci`·`overfit_check --ci` 신규 위반 0(공용 계층 스냅샷·diff·준비도 판정 함수에 스키마 리터럴 0).
8. **(v2) 신규 연동(U5)** — MCP에만 있는 소스를 O-1~O-8까지 관리자 탭에서 끝낼 수 있고, 활성화 직후 첫 질의가 스키마 수집·LLM 생성을 하지 않는다(캐시 히트).
9. **(v2) 캐시 등록(U6)** — 단계별 등록 상태와 준비도 C1~C10이 보이고, 필수 미충족 DB의 활성화에 G-7 처리가 걸린다. DB2·MariaDB 소스도 스키마·관계가 등록된다.
10. **(v2) 사람 자산** — 재생성 뒤에도 수동 DB 설명·`operator` 유사어가 남는다. 앱이 `config/db_registry.yaml`·`.env`·`mcp_server/`에 쓰지 않는다(파일 쓰기 0 단언). **(v4 · R11 개정)** `config/db_profiles/`는 관리자 승인·되돌리기만 쓰고(질의 경로 0) 수동 전용 필드를 보존하며, 적용 직전 현행 파일이 버전으로 보관된다. 로컬 샌드박스 표기 프로필이 git에 추적되면 게이트 테스트가 실패한다.

---

## 6. 위험 · 비범위

| 위험 | 완화 |
|---|---|
| 테이블이 많은 DB 스냅샷의 MCP 호출 수(테이블당 1회) | 잡 · 동시성 상한 · 진행 표시 · 결과 캐시. 질의 경로와 분리 |
| LLM 분석이 수동 프로필보다 빈약(§1.5) | 수동 프로필 DB는 승인 대신 차이 보고서만(G-4) · 적용본 필드 부족은 화면에 명시 |
| 구조 정보 없는 DB 질의 품질 저하(G-1 (a)) | 사유 노출 + 관리자 목록 경고 + 활성화 절차 확인 항목 |
| Redis 유실로 승인본 소실 | 승인 시 파일 백업 · 조회 시 복원 |
| 승인 대기 중이던 기존 스레드(플래그 삭제 시) | 릴리스 노트 · 다음 턴 일반 처리(D-135 ①) |
| 로컬 샌드박스 산출물이 운영으로 섞임 | 초안·버전에 환경 표기(R7) · 운영 환경 승인만 적용 |
| 신규 코드값 조회 부하 | 구조 정보가 지목한 컬럼만 · `DISTINCT` 상한 · 수동 실행 |
| (v2) 대형 DB 설명 생성 시간·비용(테이블당 LLM 1회) | 예상 호출 수 사전 표시 · 범위 선정 · 동시성 상한 · 실패분 재개 |
| (v2) 조각 반영 뒤 레지스트리·MCP 재기동 누락 | 준비도 C2를 실행 중 레지스트리로 판정 · 조각 머리말 재기동 안내 |
| (v2) 준비도 경고를 무시한 활성화 | **(v4 · G-7 (a) 확정)** 설정 저장 응답·화면에 미충족 항목 경고 + 감사 extra에 경고 기록(추가 입력·저장 거부 없음) |
| (v2) G-9 (a) 뒤 Redis 유실 시 설명 공백 | 설명 백업 복원(`.cache/structure/{db_id}/descriptions.yaml`) · 사유 노출 · 준비도 C8 경고 · 운영 반영 전 설명 건수 실측·선채움(§3.8.5) |
| (v2) 관계 파생 교체로 PG 조인 힌트 변화 | 동등성 실측 뒤 교체 · 다르면 폴백 전용(B-2) |
| (v2) 새 탭(초안 → 적용)과 기존 캐시 API(즉시 적용)의 적용 방식 차이 | 두 경로가 같은 함수·같은 병합 규칙(S5)을 쓴다 · 기존 API 즉시 적용은 G-8 답에 따라 유지 또는 후속 정리 |

**비범위**
- 런타임 지문 흐름(`get_schema_or_fetch`)의 DB2·MariaDB 지문 교정 — 관찰 결과만 기록(§1.2), 별도 과제.
- LLM 분석 산출을 수동 프로필 수준(`query_examples`·`allowed_tables`·`known_attributes`)으로 확장.
- 유사어 제안 큐 자체(`plans/77`) — 신규 코드값을 넘기는 연결점만.
- 앱 측 DB 등록·활성화 자동화 — **(v2 정밀화)** 설정 파일(레지스트리·`config.toml`·`.env`·프로필) 쓰기와 MCP·앱 재기동 자동화는 하지 않는다. 조각 내보내기까지만(R11). `ACTIVE_DB_IDS` 편집은 기존 설정 탭.
- (v2) MCP 소스 추가 UI·연결 문자열 입력 — 비밀 경계(§3.8 전제).
- (v2) 레지스트리·MCP 설정 핫 리로드(N7).
- (v2) 레거시 「DB 연결 설정」 탭 처분(N10) — 관찰만 기록. 폐기 제안은 D-161 ② 4항 실측 뒤 별도.
- (v2) 유사어 시드 `derive`의 비폴스타 일반화(N6) — O-7은 배포된 시드 파일 로드만.
- (v2) 질의 이력 저장소(D-133) 신규 DB 적재.
- SQL 승인 HITL(`plans/103` P3).

---

## 7. 사용자 확정 게이트

> **v4(2026-09-17) 전건 사용자 확정**(팀 리드 경유 인터뷰) — 확정값: **G-1 (a)** · **G-2 변경**(현행본 `config/db_profiles/{db_id}.yaml` + 버전 `.cache/structure/{db_id}/versions/` + Redis 적용본 캐시 · R11 프로필 예외) · **G-3 (b)** · **G-4 (c) 변경**(필드 단위 병합 · 필드별 diff · 적용 직전 v0 보관 · 되돌리기) · **G-5 (a)** · **G-6 (a)** · **G-7 (a) 변경**(경고만) · **G-8 (a)** · **G-9 (a)**(지금 제거 · 운영 반영 전 설명 건수 체크항목) · **G-10 (a)** · **G-11 (b) 변경**(구조 정보 없는 DB는 DB별 유사어 캐시에 `llm` 출처로 등록 · 전역 쓰기 0) · 로컬 샌드박스 = 같은 경로 + `environment: local_sandbox` + 추적 차단 게이트 테스트. 아래 표의 "기본 가정" 열은 v3 원문이다.

| # | 질문 | 왜 막히는가 | 기본 가정(답 없으면 이걸로 진행) |
|---|---|---|---|
| **G-1** | **구조 정보(수동 프로필·승인본)가 없는 DB에 질의가 오면?** (a) 구조 정보 없이 진행 + 사유 고지 (b) 그 DB 조회를 막고 "관리자 분석 필요" 안내 (c) 질의 중 임시 분석(저장 안 함 — 현행 1·2단 동작) | A-8 | **(a)** — 단순 테이블 DB는 구조 정보 없이도 답할 수 있다. EAV처럼 구조 정보가 필수인 DB는 활성화 전 분석을 절차로 강제 |
| **G-2** | **승인본 저장 위치** — (a) Redis 적용본 + 런타임 데이터 디렉터리 파일 백업, 수동 프로필 승격은 사람이 PR (b) `config/db_profiles/{db_id}.yaml`에 `source: approved`로 기록 | A-6 · D-131 | **(a)** — 배포 때 설정 디렉터리가 덮여도 승인본이 남고, git 정본에 자동 산출물이 섞이지 않는다 |
| **G-3** | **"신규 내용"의 범위** — (a) 스키마(테이블·컬럼·키) 변경만 (b) + 구조 정보가 참조하는 **코드값의 신규 항목**(EAV 속성명·계층 유형) | A-4 | **(b)** — 폴스타 같은 EAV DB는 새 속성이 스키마가 아니라 **데이터**로 들어온다 |
| **G-4** | **수동 프로필이 있는 DB(폴스타 4종)에서 분석 실행** — (a) 허용하되 결과는 **차이 보고서**로만, 적용 불가 (b) 분석 비활성(변경 점검만) (c) 승인본으로 적용 허용 | A-6 · R3 | **(a)** |
| **G-5** | **점검 주기** — (a) 관리자 버튼만 (b) 주기 점검(예: 하루 1회) + 변경 배지 | 부하·운영 부담 | **(a)** — 주기 점검은 운영 후 필요 시 |
| **G-6** | **MCP에는 있으나 앱 레지스트리에 없는 소스** — (a) "미등록"으로 표시하고 점검·분석 허용 (b) 레지스트리 등록 DB만 표시 | A-2 · (v2) B-3 | **(a)** — 신규 DB를 등록 전에 미리 분석해 둘 수 있다. **(v2)** 등록 잡(O-2·O-4·O-5)도 같은 기준 — (a)면 미등록·미활성 소스도 활성화 전에 캐시를 등록한다 |
| **G-7** (v2) | **준비도 필수 항목(C1~C7)을 채우지 못한 DB를 `ACTIVE_DB_IDS`에 넣으면?** (a) 경고만 (b) 미충족 항목을 보이고 **사유를 입력해야 저장** + 감사 (c) 저장 거부 | B-7 · D-214 ⑤ | **(b)** — 불변식을 화면이 지키되 의도적 예외를 막지 않는다(예: 2026-09-17 `plans/95` W-12 로컬 샌드박스 확인은 구조 정본 없이 일시 활성화 후 원복했다 — N8) |
| **G-8** (v2) | **컬럼 설명·유사어 LLM 산출의 적용 방식** — (a) 초안 → 관리자가 테이블 단위로 보고 일괄 적용 (b) 생성 즉시 적용(현행 D-011) | B-4 | **(a)** — U4 "승인한 결과만 쓴다"와 일관되고, 활성화 전이라 대기 비용이 없다. 기존 `/admin/schema-cache/*` API의 즉시 적용은 이 답과 별개로 유지(정리는 후속) |
| **G-9** (v2) | **질의 경로의 지연 LLM 설명 생성**(`cache_manager.py:1325-1355`) — (a) 제거(MCP 스키마 수집만 남김 · 설정 삭제) (b) 코드 유지 · 운영 `.env`를 false로 (c) 현행 유지 | B-6 · 응답시간 목표 | **(a)** — R1을 설명·유사어로 연장해 신규 DB 첫 질의의 순차 LLM 호출(N1)을 없앤다. 채택 전 운영 Redis 설명 건수 실측(§3.8.5) |
| **G-10** (v3) | **코드성 컬럼 값 목록 수집**(구조 정보에 값 목록이 없는 비EAV 코드 컬럼 — 예: `plans/95` 자산관리 코드 컬럼 9종) — (a) 관리자가 지정한 컬럼만 `DISTINCT` 상한 N 수집 → 초안에 코드값 목록으로 포함(승인 대상) (b) 타입·이름 휴리스틱으로 후보 컬럼 자동 선정 후 수집 (c) 수집 안 함(코드 필터 질의 비범위) | A-4 · `plans/95` G-5 | **(a)** — 결정적이고 부하가 예측된다. (b)의 휴리스틱은 공용 계층 리터럴 누수(`overfit_check`) 위험 |
| **G-11** (v3) | **양식 업로드 LLM 매핑 자동 등록**(`document/field_mapper.py` `_register_llm_mappings_to_redis`·`_register_llm_synonym_discoveries_to_redis` — 전역 사전에만 쓴다)의 대상이 구조 정보 없는 DB일 때 — (a) 전역 쓰기 차단 + 사유 로그(학습 없음) (b) DB별 유사어로 등록 전환 (c) 현행 유지 | B-8 · D-228 ④ | **(a)** — D-228과 같은 기준. 수동 프로필·승인본이 생기면 종전대로 학습한다 |

---

## 8. 다른 계획과의 관계

| 계획 | 관계 |
|---|---|
| `plans/102` | G-8(3단 순차 러너 HITL 조건) → **이 계획으로 해소** — 구조 승인이 질의 경로에서 사라지면 X-T8의 구조 승인 조건이 없어진다 |
| `plans/103` | 3단 복합 실행(계획·팬아웃)이 HITL에 막히지 않는 전제. 103 P3은 SQL 승인만 다룬다 |
| `plans/95` | 자산관리 DB는 구조 정보 없는 첫 신규 DB — A-9 검증 무대. ~~운영 정본 프로필은 여전히 95 W-6(G-4 물리 식별자)~~ **(v3) 95 W-6·W-7과 조사형 게이트(G-2 일부·G-4·G-5·G-13)를 이 계획이 받는다(§3.8.6)** · 로컬 산출물 승격 금지 유지. **(v2)** 95에서 사람이 한 편입 수작업(스키마 확인·설명 재작성·활성화 순서 판단 — W-4·W-8·W-12)을 O-1~O-9가 화면 흐름으로 받는다. 레지스트리·프로필 반영은 여전히 사람(R11) |
| `plans/27`·`plans/30`·`plans/32` | 27의 질의 경로 분석·승인 게이트를 대체 · 30 문제 F(구조 정보 미무효화)를 구조 영향 배지로 해소 · 32 수동 프로필 우선 유지 |
| `plans/77` | 신규 코드값 → 유사어 제안 큐 연결점 · 관리자 승인 탭 UI 패턴 공유 · **(v2)** O-4 유사어 초안 적용과 제안 큐가 같은 출처 태그 보존 규칙(R10 · S5)을 쓴다 |

---

## 9. 신규 결정 — D-227 (v4 · 2026-09-17 `docs/02_decision.md` 본문 등재)

**제목(예정)**: DB 구조 분석의 관리자 기능 전환 — 질의 경로 구조 승인 HITL 제거 · MCP 소스 목록 · 결정적 스키마 스냅샷 diff · 초안·검증·승인·버전 · **신규 시스템 연동 등록 흐름** (D-020 개정 · D-011 부분 개정)

**담을 내용**
1. **질의 경로는 구조를 분석하지 않는다** — `structure_approval_gate`·`enable_structure_approval`·질의 중 LLM 분석 삭제(D-161 ① 같은 결정 안 삭제). 구조 정보가 없으면 사유를 알리고 진행(G-1).
2. **구조 분석은 관리자 페이지에서 명시 실행** — 초안 → 결정적 검증 → 관리자 승인 → 버전 적용 · 되돌리기.
3. **DB 목록은 MCP `list_sources` × 레지스트리 × `ACTIVE_DB_IDS` 대조**로 보인다.
4. **변경 감지는 MCP 스키마 스냅샷 diff**(LLM 0) · 구조 정보 영향 표시 · 구조 정보가 지목한 코드값의 신규 항목 대조(G-3).
5. **정본 우선순위**: 수동 프로필 > 승인본 > 없음. ~~자동 산출물을 `config/db_profiles/`에 쓰지 않는다~~ **(v4 · G-2·G-4 확정)** 승인본의 현행본은 `config/db_profiles/{db_id}.yaml`(`source: manual` — 소비처 수정 0) · 버전은 `.cache/structure/{db_id}/versions/` · Redis는 적용본 캐시 · 수동 프로필 DB도 필드 단위 병합으로 적용(적용 직전 v0 보관 · 되돌리기) · 질의 경로는 이 파일에 쓰지 않는다 · R11은 프로필만 예외 · 로컬 샌드박스 적용은 `environment: local_sandbox` 표기 + 추적 차단 게이트 테스트로 D-214 ⑥ 준수(개정 아님).
6. **안전·감사**: 캐시 무효화가 프로필 파일을 지우지 않는다 · 채팅 캐시 변경은 관리자만 · 관리자 구조·캐시 작업 감사.
7. **(v2) 신규 시스템 연동은 관리자 등록 흐름으로** — 발견(MCP 소스) → 스키마 수집·캐시 등록(수집 1회 공유 · 지연 경로와 같은 지문 규칙) → 설명·DB 설명·구조 초안 → 적용 → 준비도 판정(C1~C10) → 활성화(기존 설정 탭 · **G-7 (a) 경고만**). 재생성은 사람 자산(수동 DB 설명·`operator` 유사어)을 덮지 않는다(S5). 앱은 레지스트리·`.env`·`mcp_server/`를 쓰지 않고 조각만 내보낸다(R11). 질의 경로의 지연 LLM 설명 생성은 **G-9 (a)로 삭제**(D-011 부분 개정 · 운영 반영 전 설명 건수 실측·선채움 체크항목).
8. **(v3) 신규 DB의 구조 조사는 사용자에게 묻지 않는다** — 물리 식별자·서버 변수·`db_schema` 후보·코드값(G-10)은 MCP로 수집하고, 수동 프로필·지식·시드 작성은 초안·승인본·조각으로 대체한다(`plans/95` W-6·W-7 이관). D-228 가드는 승인본까지 조건을 넓힌 뒤 지연 LLM 생성 제거와 함께 없앤다(B-8). **(v4 · G-11 (b))** 양식 LLM 매핑 자동 등록은 구조 정보 없는 DB면 전역 대신 그 DB의 유사어 캐시에 `llm` 출처로 등록한다.

등재 시 `docs/02_decision.md`의 `## D-` 헤더·「변경 이력」·「채번 이력」 표를 재확인하고 최댓값+1을 재부여한다(예약 소진 가능). **(v4 실측)** 등재 직전 `## D-` 헤더 최댓값 D-230(병행 세션 D-229·D-230 등재) · 「채번 이력」 D-227 = 이 계획 예약 행 존재 → **예약 번호 D-227 사용**.

---

## 10. 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1 | 2026-09-17 | 최초 작성(사용자 지시 *"구조 승인 기능은 admin 페이지에 mcp로 연결된 db리스트를 보여주고 각 db별 스키마 업데이트나 신규 내용을 조회하여 구조 분석을 통해 향후 사용할 수 있도록 정리하는 기능을 추가하라"*). 실측: 앱이 MCP `list_sources`를 호출하지 않음 · 구조 분석이 질의 관련 테이블만 보는 질의 경로 비공개 함수 · 1·2단이 승인을 고지 없이 우회하고 매 질의 분석을 버림 · 지문이 테이블명+컬럼 수뿐이고 구조 정보 무효화 없음 · 관리자 화면에 스키마·구조 탭 없음 · ★캐시 무효화가 수동 프로필 파일 삭제(확인) · 채팅 캐시 관리 권한 확인 없음 · 스키마 캐시 라우트 가림. 설계: 「DB 구조」 탭 · API 8종 · 잡 · 승인본·버전·파일 백업 · 스냅샷 diff·구조 영향·신규 코드값 · 분석 모듈 이동·결정적 검증 · 질의 경로 HITL 제거 · 안전 선행 수정 S1~S4. WU A-0~A-10 · 게이트 G-1~G-6 · D-227 예약 |
| v2 | 2026-09-17 | 사용자 지시 *"104번 계획에서 신규 시스템 연동시 db 스키마 분석 및 캐스 등록 등에 대한 기능을 계획서에 보완하라"*("캐스" = 캐시로 해석). **실측(§1.7)**: 편입 절차가 파일 4~6개 수작업 + 재기동 2회 · 워밍업 없음(첫 질의가 MCP 전체 수집 + 테이블당 순차 LLM 설명 생성) · `refresh_cache`는 스키마·관계·지문만 · DB 설명은 수동 트리거로만 · 시드 런타임 로드 0 · `derive` 폴스타 하드코딩 · 상태 API 설명·유사어 건수 항상 0 · 생성 응답 `pending` 고정 · 관계 수집 PG 전용 SQL(MCP는 엔진별 FK를 이미 반환) · 지문 SQL 의존으로 DB2 등록 불가(추정) · ★재생성이 수동 DB 설명·운영자 유사어를 덮음(코드 확인) · 활성화 순서 불변식 미강제 · `test_db.yaml` 자동 산출물 커밋 흔적 · 「DB 연결 설정」 탭 저장 no-op · `docs/09` 편입 절차 낡음. **설계**: U5·U6 · R8~R11 · §3.8 신규 연동 흐름 O-1~O-9(수집 1회 공유 · 지연 경로와 같은 지문 규칙 · 관계 파생 엔진 공통화 · 초안→적용 · 준비도 C1~C10 · 설정 조각 내보내기 · 지연 LLM 생성 처분) · S5 · 트랙 B(B-1~B-7) · G-7~G-9 · D-227 ⑦(D-011 부분 개정) |
| v3 | 2026-09-17 | 사용자 지시 *"db 관련 정보는 104번 계획에서 조사하여 자동으로 스키마, 캐시 등을 생성하는 기능을 구현할 계획이다. 이에 맞게 처리하라."* — `plans/95` 조사 항목 이관: §3.8.1 O-1 서버 변수 표시 · §3.4-4 비EAV 코드성 컬럼 → G-10 · §3.8.6 대응표 · B-8(D-228 가드 조건 확장·제거 + 양식 LLM 매핑 자동 등록 처분) · 순서 불변식(자산관리 운영 활성화 = 준비도 C1~C7) · G-10·G-11 · §8 95 행 · D-227 ⑧ |
| v4 | 2026-09-17 | **게이트 전건 사용자 확정 · 구현**(사용자 지시 *"104번 계획을 구현하라"* · 팀 리드 경유 인터뷰). 확정 변경: G-2(현행본 `config/db_profiles` + 버전 `.cache/structure/{db_id}/versions/` · R11 프로필 예외) · G-4 (c)(필드 단위 병합·v0 보관·되돌리기) · G-7 (a) 경고만 · G-9 (a) 지금 제거 · G-11 (b) DB별 유사어 · 로컬 샌드박스 표기 + 추적 차단 테스트. 개정: 머리말·R11·§3.1-4·§3.3 전면·§3.8.4·§3.8.5·§5-10·§6 위험 2행·§7 머리말·§4 B-7·B-8 verify·§9 D-227 ⑤⑦⑧. 구현: A-1~A-8·A-10·B-1~B-8(§11) · 잔여 A-9 로컬 실측(승인 대기) · 사용자 결정 대기 4건 · D-227 본문 등재 · 파일명 `-TODO`→`-WIP` |
| v5 | 2026-09-17 | **5차·6차 사용자 확정 기록 + 작업 중지 현황**(사용자 지시 *"현재 작업 중인 내용을 계획파일에 업데이트하고 우선 현재까지 마무리하고 작업을 중지하라."*). 확정: 레거시 Redis `structure_meta`는 승인 버전만 적용본(화면 후보 표시·삭제 안 함) · 후속 턴 `user_role` 재주입 · `AUTH_DEFAULT_ALLOWED_DB_IDS` = 범위 확장 (a)(이번엔 계획만 · **D-232 예약**) · CLI 채팅 캐시 거절 유지 + CLI 안내 · A-9 = 목 검증 + 사용자 직접 실 mlx 실측(itam.yaml 생기면 유지·병행 세션 통지) · bearer 토큰 전달 결함 수정 대상. 신설 WU C-1~C-6(§11.4 — C-1 구현 완료 · main 실측 plan104 테스트 524 passed) · §11.1 A-9 행 · §11.3 갱신 |
| v6 | 2026-09-17 | **재개 권고 정리**(사용자 지시 *"권고에 맞게 계획파일에 정리하라."*). §11.5 신설 — 미검증 항목 5건(C-1 뒤 넓은 스위트 · C-1 코드 검토 · 실 환경 종단 · 실브라우저 · 운영 MCP bearer 요구 여부) · 재개 순서 R-0(C-1 확정 검증) → C-5 → C-2 → C-3 → C-6 → C-4 · 순서 근거 · 운영 반영 전 체크 위치. 머리말 상태 줄에 순서 요약 |
| v7 | 2026-09-21 | **중단 작업 재개 — C-1~C-3·C-5·C-6 완료**(사용자 지시 *"중단한 작업을 재개하라"*). C-2 후속·승인·폼필 답변 턴 델타에 현재 토큰 `user_role`·`allowed_db_ids` 재주입(강등 사용자 거절 테스트 포함) · C-3 거절 문구에 `scripts/schema_cache_cli.py` 안내 · C-5 소스 오버라이드를 `model_copy`로 바꿔 `bearer_token` 누락 해소(두 경로) · C-6 A-9 목 통합 검증 10건 + **§3.8.7 사용자 직접 실측 절차서** 신설 · `docs/18` 2건 기록. 검증: 재개 기준선(C-1 반영본) 3,692 passed·5 failed(기존) → 변경 뒤 관련 스위트 3,282 passed·10 failed(기존 4 + 병행 세션 plans/102 진행 중 편집 5 + 동시 편집으로 흔들린 쓰기 지문 1 — 단독 실행 통과) · arch error 0 · overfit 신규 0 · ruff·mypy 파일별 기준선 동일. 잔여: **C-4**(코드 0 · D-232 예약) |
| v8 | 2026-09-21 | **C-4 구현 — 사용자별 DB 조회 인가 강제**(사용자 결정 "(a) 범위 확장" · **D-232 본문 등재**). 신규 `src/routing/db_authz.py`(`None`=전체·`[]`=없음·목록=교집합 · 관리자 전체 허용) · `graph.py`가 라우터 노드를 감싸 모든 반환 경로에 한 번 강제하고 인가 0이면 `access_denied`+사유로 END · 가입 기본값 = `AUTH_DEFAULT_ALLOWED_DB_IDS`(빈 값이면 없음 · 기존 사용자 불변) · `general_inference` `[]` 의미 통일 · 미소비 키 20→19 · 준비도 C10·도움말 정정 · 관리자 DB 권한 편집 UI. 테스트 52건 신규(인가 32 · UI 20) + 기존 2건 새 계약 교체 · 회귀 2,774 passed·5 failed(기존 4 + 병행 세션 동시 편집으로 흔들린 쓰기 지문 1 — 단독 통과) · arch 0 · overfit 0 · ruff·mypy 기준선 동일 |
| v9 | 2026-09-23 | **A-9 실 환경 실측(에이전트 실행)**(사용자 지시 *"에이전트를 이용하여 실측해봐라"* · *"반영하라"*). 로컬 mlx(두 평면 비과금 확인)·MCP 9099·MariaDB 3307·Redis 6380 db15 격리·임시 포트 18140으로 itam O-1~O-6·승인·되돌리기·준비도·질의 전 과정 수행 — 흐름 통과 · 필수 준비도 4/7→7/7. **결함 1**(넓은 테이블 설명 초안 결정적 실패) · **절차서 오류 4건 정정**(§3.8.7 D1 변수명 · D2 reload · D3 `redis-cli -p` · D4 신규 DB v0 없음) · 품질 관찰 5건 · 로컬 MCP 9099 무인증 확인. 결과·잔여 = §11.6. 코드 변경 0 |
---

## 11. 구현 현황 (v4 · 2026-09-17)

### 11.1 WU 상태

| WU | 상태 | 요지 |
|---|---|---|
| A-0 | 완료 | 게이트 G-1~G-11 사용자 확정(§7 머리말) |
| A-1 | 완료 | S1 무효화가 프로필 미삭제(`_delete_db_profile` 삭제 · `invalidate_all` 관리자 자산 보존) · S2 채팅 생성·무효화 6종 관리자 전용(인증 켜짐 · 3단·2단·1단 진입 모두 `user_role` 도달 실측) · S3 라우트 순서 · S4 `CACHE_OPERATION`/`ADMIN_ACTION` 감사 + `audit_logged`(잡 상태 폴링은 감사 제외) · S5 DB 설명 출처(`schema:db_description_origin`) · LLM 유사어 병합(비-llm 단어 보존) — API·채팅·CLI 세 경로 |
| A-2 | 완료 | `DBHubClient.list_sources`·`health_check(source)`·`health_check_detail` · 소스 목록(MCP × 레지스트리 × ACTIVE · MCP만/앱만/활성인데 구조 없음/drift) |
| A-3 | 완료 | `src/domain/schema_snapshot.py`(순수) · 점검 잡 · diff·구조 영향 |
| A-4 | 완료 | EAV 속성명·계층 유형·관리자 지정 코드 컬럼(G-10 (a)) DISTINCT 상한 500 · 코드 조립 SQL + `SQLGuard` |
| A-5 | 완료 | `src/schema_cache/structure_analysis.py`(이동 · ok/no_patterns/failed · FK 묶음 · 결정적 검증 4종) · 분석 잡 · 필드 병합 초안 |
| A-6 | 완료(G-2·G-4 확정 설계) | `src/domain/profile_merge.py` · `StructureStore.apply_profile`·`rollback`·버전 `.cache/structure/{db_id}/versions/` · 승인·반려·되돌리기·차이 보고서 |
| A-7 | 완료 | `dashboard.html` 「DB 구조」 탭 · `src/static/js/admin-db-structure.js` · 비로그인 401·비관리자 403·관리자 200(TestClient) — 실브라우저 미검증 |
| A-8 | 완료 | 구조 승인 HITL·질의 중 LLM 분석·자동 기록·`ENABLE_STRUCTURE_APPROVAL` 삭제 · 사유 노출(3단 본문 `[안내]` · 2단 집계 블록 1회 · 멀티 대칭) · 구 승인 대기 스레드 해제 |
| **A-9** | **목 검증 + 실측 완료(잔여 §11.6)** | 사용자 확정대로 **목으로만** 검증(MCP 9099·mlx 8080·실 Redis·임시 앱 서버 미사용). `tests/test_schema_cache/test_plan104_a9_mock_rig.py` 10건 — 목록(MCP만 있음·활성인데 구조 없음) → 등록(`get_table_schema` 호출 = 테이블 수 · 지문 SQL 미호출) → 설명 초안·제외 적용 → 점검(타입 변경·구조 영향·신규 코드값) → 분석·승인(tmp 프로필에 `source: manual` · v0 baseline 보관 · `rollback(0)` 원문 바이트 동일) → 질의(캐시 히트 · 수집·LLM 설명·구조 분석 0 · `_structure_meta` 부착) → 준비도(조각 반영 뒤 필수 7/7·권장 2/2) → **실제 `config/`·`.cache/structure/` 쓰기 0 지문 비교**. **(v9) 실 mlx·실 MCP 실측 완료(2026-09-23 · 에이전트 실행 · 사용자 지시)** — 흐름 통과, 결함·잔여는 §11.6 |
| A-10 | 완료 | D-227 등재 · D-011·D-020·D-135·D-203·D-228 부기 · `docs/05`·`06`·`07`·`09`(새 DB 추가 절차 교체)·`flag_audit` · `docs/18` 4건 · INDEX · `plans/102` X-T8·G-8 부기 |
| B-1 | 완료 | `schema:{db_id}:registration` · 준비도 C1~C10 순수 함수(`src/domain/db_readiness.py`) · `/status` 설명·유사어 실제 건수 |
| B-2 | 완료 | 관계를 MCP 테이블별 FK 응답에서 파생 — PG 동등성 실측(로컬 5433 FK 4=4 · polestar 0=0) 후 일원화 · 종전 PG 전용 FK SQL 삭제 · MariaDB(itam 샌드박스 FK 0 — 종전 SQL은 `constraint_column_usage` 부재로 실패) · DB2 목 |
| B-3 | 완료 | 등록 잡 probe·schema(수집 1회 공유 · 지문 SQL 미경유)·seeds·value_index |
| B-4 | 완료 | 범위·예상 호출 수·동시성(`SCHEMA_CACHE_ADMIN_LLM_CONCURRENCY`)·실패 테이블 재실행 · 설명 초안 적용/폐기(병합·전역 동기화·설명 백업) · DB 설명 적용(출처) |
| B-5 | 완료 | 레지스트리 조각만(프로필 골격은 G-2로 불필요) · 로컬 머리말 · 레지스트리·`.env`·`mcp_server/` 쓰기 0 단언 |
| B-6 | 완료(G-9 (a)) | 지연 LLM 설명 생성·`SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS` 삭제 · 설명 백업 복원 · 질의마다 `descriptions_missing` 노트 · **운영 반영 전 설명 건수 실측은 운영 환경 작업**(§3.8.5) |
| B-7 | 완료(G-7 (a)) | `PUT /admin/settings` 응답 `readiness_warnings` · 감사 extra · 저장 진행 · 설정 탭 경고 배너 |
| B-8 | 완료 | ① 가드 조건 확장(프로필·승인 버전) → ② B-6과 함께 가드 분기 소멸 · ③ G-11 (b) DB별 유사어 등록 |

### 11.2 검증

- 관련 스위트(tests/test_schema_cache · test_domain · test_dbhub_* · test_api · test_nodes · test_document · test_orchestration · test_multiturn · test_graph* · test_structure_analysis · test_plan31·32·67 · test_pipeline · test_composite · test_semantic_routing · test_observability · test_utils · test_routing · text2sql · test_generic_path · test_empty_answer · test_excel_fill_pipeline · test_query_to_excel_mapping): **4,585 passed · 6 failed · 10 skipped**(mlx 기본 URL을 닫힌 포트로 주입 — 공유 로컬 LLM 실호출 차단). 6건은 전부 기존 실패로 클린 worktree 대조 확정(캐시 매니저 대소문자 폴백 1 · 파일 백엔드 3 · `test_plan31` `.env` 퍼지 매칭 누수 1 · `test_pipeline` `llm.astream` 목 1). 남은 실패는 전부 병행·기존 실패로 클린 worktree 대조 확정 — `test_cache_manager.py` 대소문자 폴백 1 · `test_cache_manager_new_features.py` 3(파일 백엔드) · `test_plan31` `.env` `SYNONYM_FUZZY_MATCH` 누수 1 · `test_pipeline::test_step7_output_generator`(`llm.astream` 목) 1.
- 신규 테스트: 도메인(스냅샷·준비도·병합) · 클라이언트 · 분석 모듈 · 저장소(프로필 버전·게이트 테스트) · 잡 · 캐시 안전 · 서비스 4파일 · API/화면 · 질의 경로 · 지연 생성 · 채팅 권한 · 양식 매핑.
- 게이트: `arch_check --ci` error 0(경고 81→80) · `overfit_check --ci` 신규 유입 0 · ruff 신규 모듈 0·수정 파일 증가 0(병행 세션 줄 제외) · mypy(3.12) 신규 모듈 0.
- 실측: 실제 폴스타 프로필 5종 tmp 사본으로 적용 → `rollback(v0)` 바이트 동일·주석 복원(57/52/52/51/2줄) · 빈 초안 병합 field_diff 0 · 43KB 적용 약 240ms.
- 미실측: 운영 Redis 설명 건수(이 맥북에서 측정 불가) · A-9 실 MCP·mlx 종단 · 실브라우저 화면.

### 11.3 확정·조치 대기

- **사용자 확정 완료(5차·6차)** — 레거시 `structure_meta`(C-1) · 후속 턴 `user_role`(C-2) · CLI 거절 유지(C-3) · `AUTH_DEFAULT_ALLOWED_DB_IDS` (a)(C-4) · bearer 결함 수정(C-5) · A-9 목 검증(C-6).
- **팀 리드 판단 유지(확정 기록)**: 관리자가 검토해 적용한 설명 초안은 구조 정보 없는 DB도 전역 유사어 동기화(D-228 ③ 기준) · 설명 없음 노트는 질의마다 · 잡 상태 폴링 GET은 감사 제외.
- **운영 반영 전 체크 2건**: ①운영 Redis DB별 `schema:{db_id}:descriptions` 건수 실측 → 부족하면 O-4로 선채움 ②운영 `.env`의 `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS` 줄 삭제(로컬 `.env`는 이미 정리 — 마지막 스위트에서 `.env` 커버리지 테스트 2종 통과).
- `CLAUDE.md`는 main이 갱신(에이전트 미수정).

### 11.4 잔여 WU (2026-09-17 작업 중지 시점)

| WU | 상태 | 내용 · 멈춘 지점 |
|---|---|---|
| **C-1** 레거시 `structure_meta` 승인 버전 한정 | **구현 완료**(main 실측 2026-09-17 17:50~17:55) | 적용본은 승인·되돌리기 버전만(Redis 원시값을 먼저 읽지 않음) · 관리자 목록 「레거시 분석본 — 승인 필요」 후보 · 레거시로 초안을 만들 때 샘플을 새로 생성해 검증 4종 통과 뒤 승인·버전화 · 키 삭제 안 함. 구현: `src/schema_cache/structure_store.py`(`latest_applied_version`·`has_approved_version` · `restore_applied` 승인 버전 기준) · `src/schema_cache/cache_manager.py`(`get_applied_structure_meta` :414 = `restore_applied`만 · `has_structure_authority` = 수동 프로필 또는 승인 버전 · `get_structure_meta_or_profile` 적용본 경유) · `src/schema_cache/db_structure_service.py`(`legacy_candidate`·`legacy_meta`·`legacy_structure_meta`·`run_legacy_draft`·경고 `legacy_unapproved`) · `src/api/routes/db_structure.py:324` `POST …/{source}/legacy/draft` · `src/static/js/admin-db-structure.js`(배지·버튼·`legacySection`) · 테스트 신규 `tests/test_schema_cache/test_plan104_legacy_structure.py` 16건 + 기존 plan104 테스트 갱신(위임 구현 에이전트가 17:36~17:45 반영 · 17:48 사용자 중지). **검증(main 실행)**: `tests/**/test_plan104_*.py` 20파일 + `tests/test_dbhub_plan104_client.py` + `tests/test_domain` + `tests/test_schema_cache/test_structure_analysis_module.py` **524 passed**(mlx URL 닫힌 포트 주입) · `arch_check --ci` exit 0 · `overfit_check --ci` exit 0. 넓은 스위트(§11.2 4,585건)는 C-1 반영 뒤 재실행하지 않았다 |
| **C-2** 후속 턴 `user_role` 재주입 | **구현 완료** | `src/api/routes/query.py`에 `_with_current_identity` 신설 — 후속 턴 세 분기(SQL 승인 턴 · 폼필 답변 턴 · 일반 후속 턴) 델타에 이번 요청 토큰의 `user_role`·`allowed_db_ids`를 다시 싣는다(권한은 매 요청 DB에서 최신값을 읽으므로 강등이 즉시 반영). 첫 턴 경로는 종전대로. 테스트 `tests/test_api/test_plan104_turn_identity.py` 9건 — 세 분기 재주입 · 첫 턴 회귀 · 헬퍼 덮어쓰기 · **강등 사용자가 기존 스레드에서 캐시 무효화 시도 → 거절·`invalidate` 미호출**. `docs/18` 기록 |
| **C-3** CLI 채팅 캐시 거절 안내 | **구현 완료** | `src/nodes/cache_management.py` `_ADMIN_ONLY_MESSAGE`에 `python scripts/schema_cache_cli.py` 안내와 거절 사유(CLI 대화에는 역할 정보가 없다)를 덧붙였다. 거절 자체는 유지(사용자 확정) · 테스트 포함 |
| **C-4** `AUTH_DEFAULT_ALLOWED_DB_IDS` 범위 확장 (a) — **D-232 등재** | **구현 완료**(2026-09-21) | **실측 충돌 3건**(착수 전): ①메인 질의 경로에 `allowed_db_ids` 필터 0건(Plan 41 미구현 · D-026 주의) ②`[]` 의미 불일치(`general_inference.py:99` 전체 허용 vs 나머지 전체 차단) ③관리자 역할 예외·권한 편집 UI 부재. **구현**: 신규 `src/routing/db_authz.py`(`None`=전체·`[]`=없음·목록=교집합 · 관리자 전체 허용 · `parse_allowed_db_ids`·`filter_router_result`·`authorized_router`) · `src/graph.py`가 라우터 노드를 감싸 **모든 반환 경로**에 한 번만 강제하고 인가 0이면 `access_denied`+사유로 END(라우터 본체는 병행 세션 편집 중이라 미수정) · `user_auth.py` 가입 기본값(빈 설정이면 `[]` · 기존 사용자 `None` 불변) · `general_inference` 규약 통일 · `settings_catalog` 미소비 20→19 · 준비도 C10·도움말 `auth.yaml` 정정 · 관리자 「사용자 관리」 DB 권한 편집 UI(열·편집기 · 후보는 health `db_status_map` · 보호 계정 편집 가능). **테스트**: `tests/test_api/test_plan104_db_authz.py` 51건 · `test_plan104_admin_permissions_ui.py` 20건 · 카탈로그·준비도 기존 테스트 2건 새 계약으로 교체. **보존 경로 상호작용**(plans/102 W-10 실측 대응): 존 선택 재개 턴의 보존 경로(`_keep_zoneless_targets` — 후보를 `active_db_ids`로만 고른다)로 되살아난 대상도 **노드 경계 필터가 함께 거른다**(회귀 2건으로 고정 · 라우터 본체 미수정). **요청 선택값 차단(2026-09-21 보강 · 사용자 지시)**: 래퍼는 **라우터 반환값만** 거르므로 요청 본문의 `selected_db_ids`(외부 입력)가 그대로 상태에 실려 순차 러너 task 고정이 비인가 DB로 확정되는 우회가 남아 있었다. `filter_selected_db_ids`+`apply_selection_authorization`으로 **네 진입 전부**(`/query`·`/query/stream`·`/query/file`·`/query/file/stream`)와 폼필 존 복원(`pending_form_fill.db_ids`)에 상태 조립 **전** 차단(일부 비인가=그 db_id만 제외 · 전부 비인가=조회 0 + 사유 응답). 테스트 17건 추가(총 51건 — 순차 러너 task 고정까지 실제로 태우는 3건 · 게이트 무력화 시 2건 실패 음성 대조 확인). **잔여**: 1·2단이 각자 읽는 활성 DB 목록(`subagents._make_isolated_input`의 `allowed_db_ids`·`zone_selection_db_ids` 포함) — 그 파일은 병행 세션 미커밋분이라 편집 전 통지가 필요하다(§11.5 U-6) |
| **C-5** bearer 토큰 전달 결함 | **구현 완료** | 소스 오버라이드 두 곳(`src/db/__init__.py` `get_db_client` · `src/routing/db_registry.py` `get_client`)이 `DBHubConfig`를 필드 나열로 재구성해 `bearer_token`이 빠지던 것을 `model_copy(update={"source_name": db_id})`로 바꿔 나머지 필드를 그대로 옮긴다(앞으로 필드가 늘어도 안 빠진다). 테스트 2건(두 경로 대칭) · `docs/18` 기록 |
| **C-6** A-9 목 통합 검증 + 실측 절차서 | **완료** | 목 통합 검증 = §11.1 A-9 행(10건). 실측 절차서 = **§3.8.7** — 과금 provider 사전 확인 · `REDIS_DB=15`·`SCHEMA_CACHE_DIR`·임시 포트 격리 기동 · 관리자 탭 O-1~O-6 · 승인·되돌리기 확인 · 질의 캐시 히트/LLM 0 확인 · **승인으로 생긴 `config/db_profiles/{db_id}.yaml`은 유지하고 병행 세션에 통지** · 로컬 표기 프로필 커밋 금지 · 원상복구 |

**기존 결함 — 별도 과제로 기록만(이 계획에서 수정 안 함)**: ⓪`src/nodes/schema_analyzer.py`의 `DEBUG[1]`~`DEBUG[5]` 로그가 **WARNING 레벨로 질의마다 출력**된다(529·540·549·587-589·615·684행 부근 · A-9 작성 중 관측) ①`SchemaCacheManager.cleanup_stale_entries`가 Redis의 사라진 컬럼 설명·유사어 필드를 실제로 지우지 못한다(HSET만 해 남는다) ②MCP PG FK 조회(`mcp_server/mcp_server/tools.py` `_pg_get_foreign_keys`)가 복합 FK를 kcu×ccu 교차곱으로 돌려준다(앱 파생 쪽에서 동일 쌍 중복만 제거).

**참고**: 공유 mlx 8080은 16:20~17:33 OOM 상태였다. §11.2의 최종 수치는 목 LLM·닫힌 포트(`LLM_MLX_BASE_URL=http://127.0.0.1:9/v1`)로 잰 것이라 무관하다. 구현 중간에 한 에이전트가 넓은 스위트를 돌리다 루프백 mlx에 실호출이 걸린 적이 있어(`docs/18` 기록) 그 중간 실행 결과는 무효로 본다. §11.2 수치(4,585 passed · 6 failed 기존 · 10 skipped · arch error 0 · overfit 신규 0)는 C-1 반영 **이전** 트리 기준이고, C-1 반영 뒤에는 main이 두 묶음을 실행했다 — ①plan104 테스트 전 파일(`tests/**/test_plan104_*.py` 20 + `test_dbhub_plan104_client.py`) + `tests/test_domain` + `test_structure_analysis_module.py`: **524 passed** ②`tests/test_schema_cache` · `tests/test_api` · `tests/test_domain` · plan104 노드·문서·클라이언트 테스트 · `test_structure_analysis` · `test_plan32_manual_profile`: **1,397 passed · 4 failed · 7 skipped**(4건은 §11.2의 기존 실패 — 캐시 매니저 대소문자 폴백 1 · 파일 백엔드 3) · `arch_check --ci`·`overfit_check --ci` exit 0. 넓은 스위트(§11.2 범위) 재실행은 하지 않았다.

### 11.5 재개 시 권고 순서 (v6 · 2026-09-17)

**"회귀 없음"이 확인된 것은 아래 범위뿐이다.** 재개 전에 다음 미검증 항목을 알고 시작한다.

> **해소 현황(2026-09-21 재개)**: **U-1 해소** — C-1 반영본에서 넓은 스위트 재실행 **3,692 passed · 5 failed**(기존 4 + `test_pipeline` astream 목 1) · C-2·C-3·C-5 반영 뒤 관련 스위트 3,282 passed · 10 failed(기존 4 + 병행 세션 plans/102가 실행 중 고친 파일 5 + 동시 편집으로 흔들린 쓰기 지문 1 — 단독 실행 통과). **U-2 부분 해소** — C-1 핵심 함수(`get_applied_structure_meta`·`has_structure_authority`·`latest_applied_version`·`restore_applied`·`run_legacy_draft`·라우트·화면)를 읽어 설계와 일치함을 확인했고 테스트가 통과한다(전 줄 검토는 아님). **U-3·U-4 유지** — 사용자 직접 실측(§3.8.7 절차서에 화면 확인 항목 포함). **U-5 유지** — 코드 결함은 C-5로 고쳤으나 **운영 MCP가 bearer 토큰을 요구하는지는 운영 설정 담당 확인 필요**. **(2026-09-21 추가 · 같은 날 개정) U-6** — 초판 문구 *"3단에만 걸려 있다"*는 **틀렸다**. 요청 본문의 `selected_db_ids`는 라우터를 우회해 상태에 실리고 **3단 순차 러너의 task 고정까지 도달한다**(`subagents.run_data_query_pipeline`의 `raw_targets` · `docs/21` §7). 사용자 지시로 **요청 경계에서 차단**했다 — `/query`·`/query/stream`·`/query/file`·`/query/file/stream` 네 진입 + 폼필 존 복원(D-232 ⑨ · 테스트 17건, 순차 러너까지 태우는 3건 포함). **남은 잔여는 1·2단이 자기 활성 DB 목록을 따로 읽는 부분**(`deep_agent`·`intent_orchestration` — 계획 수립·격리 입력에 같은 교집합 미적용). D-225 기준 경로 전제는 유지.

| # | 미검증 항목 | 왜 남았나 | 해소 WU |
|---|---|---|---|
| U-1 | C-1 반영 뒤 넓은 스위트(§11.2 범위 — 노드·오케스트레이션·그래프·파이프라인 포함) | 작업 중지 지시로 재실행 안 함. C-1 뒤 확인은 스키마 캐시·API·도메인·plan104 묶음(1,397 passed · 기존 실패 4)뿐 | R-0 |
| U-2 | C-1 코드 검토 | 사용자 중지(17:48)로 끊긴 위임 에이전트가 넣은 코드 — 완결 보고 없이 테스트 통과만 확인 | R-0 |
| U-3 | 실 환경 종단(MCP 9099 · mlx · 앱 서버) | 사용자 결정: 목 검증만, 실측은 사용자 직접(§11.1 A-9) | C-6 → 사용자 실측 |
| U-4 | 관리자 「DB 구조」 탭 실브라우저 | 브라우저 도구 없음 — TestClient로 401·403·200만 확인 | C-6 절차서에 화면 확인 항목 포함 |
| U-5 | 운영 MCP가 bearer 토큰을 요구하는지 | 로컬 `.env`에 토큰 설정 없음 — 운영 설정 미확인 | C-5 |

**재개 순서**

| 순서 | WU | 할 일 | 완료 판정 | 근거 |
|---|---|---|---|---|
| **R-0** | C-1 확정 검증 | ①§11.2 범위 넓은 스위트 재실행(`LLM_MLX_BASE_URL`·`ORCHESTRATOR_BASE_URL` 닫힌 포트 주입 — 공유 mlx 실호출 차단) ②C-1 변경 파일 검토(§11.4 C-1 행의 `structure_store.py`·`cache_manager.py`·`db_structure_service.py`·`routes/db_structure.py`·`admin-db-structure.js`) — 설계(승인·되돌리기 버전만 적용본 · Redis 원시값 선조회 없음 · 레거시 초안은 검증 4종 통과 뒤 승인 · 키 삭제 없음)와 대조 | 새 실패 0(실패는 클린 worktree 대조로 기존·병행 귀속 확정) · 설계 불일치 0 | 코드 변경 없이 끝나는 확인이고, 뒤 WU의 기준선이 된다 |
| **1** | C-5 bearer 토큰 | `src/db/__init__.py` `db_id` 오버라이드가 `DBHubConfig`를 새로 만들 때 `bearer_token`과 그 밖 누락 필드를 복사 + 테스트 · 운영 MCP 토큰 요구 여부(U-5)를 운영 설정 담당에게 확인 | 토큰 설정 시 소스별 클라이언트 요청에 `Authorization` 헤더 · 필드 누락 0 단언 | 작다(1곳). 운영 MCP가 토큰을 요구하면 관리자 탭 점검·등록(B-3 · `db_structure_service.py`의 `get_db_client`)이 인증 실패한다 |
| **2** | C-2 후속 턴 `user_role` | §11.4 C-2 행대로 매 턴 재주입 + 강등 사용자 거절 테스트 + `docs/18` | 강등 뒤 기존 스레드에서 관리자 전용 채팅 캐시 작업 거절 | 보안. S2(채팅 캐시 생성·무효화 관리자 전용)로 영향이 커졌다 |
| **3** | C-3 CLI 안내 | 거절 응답에 `scripts/schema_cache_cli.py` 안내 + 테스트 | 문구 단언 | 작고 독립적 |
| **4** | C-6 A-9 목 통합 + 실측 절차서 | 목 MCP·목 LLM·Redis 페이크·`tmp_path`로 목록→점검→분석→승인→질의 한 흐름 · 실제 `config/db_profiles/`·`.cache/structure/` 쓰기 0 단언 · 사용자용 실 mlx 절차서(§11.4 C-6 행 항목 + U-4 화면 확인) | 통합 테스트 통과 · 절차서 작성 → 사용자 실측으로 U-3 해소 | 기능 전 과정을 한 번에 잇는 유일한 검증 |
| **5** | C-4 `AUTH_DEFAULT_ALLOWED_DB_IDS` (a) | §11.4 C-4 행 설계대로 구현 · **D-232 본문 등재**(등재 직전 채번 재-grep) | 신규 가입자 `[]` = 메인 질의 경로에서 DB 접근 0 · 관리자 전체 허용 · 권한 편집 UI · 기존 사용자 불변 | 인가 동작 변경이라 범위가 가장 크고 104 본 기능과 독립 — 별도 결정(D-232)으로 묶는다 |

- 각 단계 뒤 `arch_check --ci`·`overfit_check --ci`(기준선 전면 재생성 금지)와 해당 테스트를 돌린다. 커밋 시점은 사용자가 정한다.
- **운영 반영 전 체크 2건**(§11.3 — 운영 Redis 설명 건수 실측·선채움 · 운영 `.env` `SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS` 줄 삭제)은 위 순서와 무관하게 **배포 직전**에 한다. 운영에서 달라지는 동작: 질의 중 LLM 설명 생성 없음 · 구조 정보·설명이 없는 DB 응답에 `[안내]` 노트.
- 기존 결함 2건(§11.4 아래)은 이 순서에 넣지 않는다 — 별도 과제.

### 11.6 (v9) A-9 실 환경 실측 결과 — 2026-09-23

**조건**: 사용자 지시로 에이전트가 §3.8.7(정정본)을 실행. 두 평면 `mlx`(`scripts.bench --show-env` 확인 · 비과금) · MCP 9099(읽기) · itam MariaDB 3307 · Redis 6380 **db15** 격리(기동 로그 `Redis 연결 성공: localhost:6380/15`) · 임시 포트 18140 · reload 없음. LLM 호출 수는 계측 프록시(18081→8080, 본문 그대로 전달), MCP 호출 수는 스크래치 런처의 `DBHubClient._call_tool` 래핑으로 셌다(추적 파일 수정 0). 인증이 꺼진 환경(`AUTH_ENABLED` 미설정)이라 관리자 작업은 `anonymous`로 기록됐다. 증거는 세션 스크래치 `a9/`(server.log · llm_calls.jsonl · mcp_calls.jsonl · 단계별 응답 JSON 13건). **MLX 결과라 성능 결론은 내지 않는다**(D-174).

| 단계 | 결과 | 근거 |
|---|---|---|
| 소스 목록 | 통과 | itam: mariadb · MCP 있음 · 등록·활성 · 구조 없음 경고 `active_without_structure` · 준비도 필수 4/7(C5·C6·C7 미충족) |
| O-1·O-2 | 통과 | 서버 변수(11.4.13-MariaDB · `lower_case_table_names=0` 등) · 테이블 2 · MCP `get_table_schema` 2회 = 테이블 수 · `execute_sql`은 서버 변수 SELECT 1회뿐(지문 SQL 0) |
| O-3 | 통과 | 예상 LLM: 설명 2 · 구조 1 · DB 설명 1 · 동시성 1(env 반영) |
| O-4 | **부분 실패** | TCDMSIF79(9컬럼) 성공 14.8s · **TCDMSIF80(68컬럼) `finish_reason=length`로 잘려 파싱 실패**(90s · 실패분 재실행도 같은 길이로 재현) · 예상 2 = 실측 2 · TCDMSIF80 제외 적용 = LLM 0 · 설명·유사어 9건 |
| O-5 | 통과 | 초안 LLM 1회 = 예상 · 출처 `llm`으로 적용 |
| O-6·승인 | 통과(품질 문제) | 구조 LLM 1회 = 예상 · 검증 4종 통과 · field_diff 4건 · 차이 보고서 `# LOCAL SANDBOX` · v1 승인 · 프로필에 `source: manual`·`environment: local_sandbox` |
| 되돌리기 | 절차서와 다름 → §3.8.7 D4 정정 | 신규 DB라 v0 없음(404) · v1로 되돌려 v2(`rollback`) 생성 · 현행 파일 = v1 원문 바이트 동일 |
| 준비도 | 통과 | 필수 4/7 → 7/7 · 권장 0/2 → 2/2 |
| 질의 | 통과(품질 문제) | "자산관리 DB에서 서버 호스트명과 IP 주소를 10건 보여줘" → 10행 정확 · 로그 `Redis/파일 캐시 히트` · `수동 프로필 로드` · 질의 중 MCP는 `execute_sql` 3회뿐(전체 수집 0) · 컬럼 설명·구조 분석 LLM 0 · 경로는 `.env` 확정 1단 `deep_agent` |
| MCP 9099 bearer | **로컬만** 무인증 | 토큰 없는 `GET /sse`가 200 · `mcp_server/.env`에 토큰 키 없음 · 운영 MCP 요구 여부(U-5)는 여전히 미확인 |

**미해소 → U-3 해소**(실 환경 종단). **U-4**(실브라우저)·**U-5**(운영 MCP)는 유지.

**잔여(코드 변경 0 — 착수는 사용자 결정)**

| # | 종류 | 내용 | 근거 |
|---|---|---|---|
| R-1 | **결함** | 넓은 테이블은 설명 초안이 구조적으로 실패한다 — 테이블당 LLM 1회에 컬럼 전부를 싣고 나누지 않아 출력이 `max_tokens`(4096)를 넘는다. 폴스타의 넓은 테이블도 같은 위험이 있다(운영 FabriX 재현 여부 미확인). 방향: 컬럼 묶음 분할 호출 또는 테이블 컬럼 수 기준 분할 | `src/schema_cache/description_generator.py` `generate_for_table`(:135) |
| R-2 | 품질 | 구조 분석이 FK 없는 복합키 조인을 못 보고 `query_guide`에 "JOIN 관계 없음"으로 적었다 — 이 문장이 질의 프롬프트에 주입된다. 검증 4종은 이런 **부재 단정**을 거르지 못한다 | `config/db_profiles/itam.yaml` · 실제 조인 `testdata/itam/schema.yaml:139-140`(3열 복합) |
| R-3 | 품질 | 질의에 쓰인 테이블의 설명이 0건이어도 `[안내]`가 없다 — `descriptions_missing`은 DB 전체 설명이 비었을 때만 발동해 부분 커버리지는 조용히 품질이 떨어진다 | `src/nodes/schema_analyzer.py:763` |
| R-4 | 기능 공백 | 신규 DB를 "구조 없음"으로 되돌리는 경로가 없다(v0 부재) | `src/schema_cache/structure_store.py:499` |
| R-5 | 비대칭 | 질의 경로는 수동 프로필에서 `source`만 빼고 `environment`를 남긴다 — 관리자 서비스는 둘 다 뺀다 | `src/nodes/schema_analyzer.py:709` vs `src/schema_cache/db_structure_service.py:644` |
| R-6 | 품질(104 밖) | 1단 `deep_agent` 오케스트레이터가 질문을 없는 테이블명(`asset_management`) SQL로 바꿔 워커에 넘겨 응답 본문에 노출(데이터는 정확) | 1단 경로 — plans/103·D-225 소관 |
| R-7 | **결정 대기** | 실측이 남긴 `config/db_profiles/itam.yaml`(untracked · `local_sandbox`)을 병행 세션의 itam 질의가 지금 읽는다 — R-2 문구 포함. 또 `testdata/itam/README.md:9`의 **파생 금지** 문구와 충돌한다(v4 로컬 샌드박스 규칙 이전 문구). 처분(문구 수정 / 파일 치움 · README 개정 여부)은 사용자 결정 | §3.8.7 4. 결과 처리 |

**측정 못 한 것**: D-232 비관리자 인가(인증 꺼짐 — 계정 생성 회피) · O-7 시드·값 인덱스(시드 파일 없음 · 값 인덱스는 EAV 전용) · O-9 설정 탭 활성화(itam 이미 활성) · 점검 잡 · 운영 Redis 설명 건수.

**남은 산출물**: `config/db_profiles/itam.yaml`(untracked — 커밋하면 `test_plan104_local_sandbox_profile_gate.py` 실패) · `.cache/structure/itam/`(v1·v2·descriptions.yaml · gitignore). 임시 서버·프록시 종료 · db15 비움 · `.cache/schema_rig` 삭제 완료. 공유 감사 로그와 `checkpoints.db`에 이번 관리자 작업·질의 1건이 남았다.
